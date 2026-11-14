"""Weighted distribution engine: preview, RQ enqueue, chunked worker & reconciliation.

P1 7.5 requires DB-level locking, chunked transactions, post-commit notifications
and crash recovery:
  * `api.distribution.execute` validates the preview, acquires a DB row lock on
    `CSKH Distribution Run`, moves the run to `queued` with a bounded lease,
    enqueues `crm_cskh.jobs.distribution_worker.run_distribution` onto Frappe's
    existing RQ `long` queue (`enqueue_after_commit=True, deduplicate=True`),
    and returns early with `run_id` and status;
  * `run_distribution_worker` claims `running` under `SELECT ... FOR UPDATE`,
    re-checks actor permission, campaign status, `rule_version` and assignee
    eligibility/capacity before every chunk, commits `progress`, `state_json`
    and DB-reconciled `actual_json` after every chunk, and delivers notifications
    only after commit;
  * double-submit / retry on an already `queued`, `running` (with active lease)
    or `completed` run never enqueues a duplicate RQ job and never creates a
    duplicate assignment; an expired lease (`lease_expires_at <= now`) allows a
    recovery worker to take over cleanly from `run.progress`.
"""

import hashlib
import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from crm_cskh.p1_models import ACTIVE_WORK_ITEM_STATES, OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p3_models import authorized_sla_write
from crm_cskh.services import eligibility, ownership
from crm_cskh.services.attribution import AUTHORITATIVE, resolve_team_for_user_at
from crm_cskh.services.calendar import add_business_minutes
from crm_cskh.services.errors import Conflict, Forbidden, InvalidRequest, NotFound, RuleChanged
from crm_cskh.services.notifications import TODO_BULK_FIELDS
from crm_cskh.services.weighted_allocator import allocate, planned_distribution

CHUNK_SIZE = 100
PREVIEW_TTL_MINUTES = 30
LEASE_SECONDS = 300

WORK_ITEM_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"campaign",
	"lead",
	"status",
	"revision",
	"assigned_to",
	"assigned_at",
	"allocation_run",
	"attempt_count",
	"connected_attempt_count",
	"policy_version",
]

ACTIVE_CLAIM_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"lead",
	"campaign",
	"work_item",
]

ASSIGNMENT_HISTORY_BASE_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"work_item",
	"action",
	"from_user",
	"to_user",
	"actor",
	"event_at",
	"reason",
	"private_reason",
	"payload_hash",
	"distribution_run",
	"ownership_start",
	"ownership_end",
	"event_id",
	"previous_event",
]

SLA_OBLIGATION_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"obligation_type",
	"work_item",
	"lead",
	"campaign",
	"interval_event",
	"owner_user",
	"team_at_obligation",
	"attribution_status",
	"followup_task",
	"status",
	"informational_only",
	"is_recovery_obligation",
	"handoff_reset_seq",
	"handoff_cap_reached",
	"reschedule_seq",
	"started_at",
	"original_due_at",
	"effective_due_at",
	"work_item_original_due_at",
	"evaluation_anchor_at",
	"is_paused",
	"total_paused_business_minutes",
	"policy_name_snapshot",
	"policy_version_snapshot",
	"policy_snapshot_json",
	"calendar_snapshot_json",
	"audit_events_json",
	"idempotency_key",
]

DOCSHARE_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"user",
	"share_doctype",
	"share_name",
	"read",
	"write",
	"share",
	"submit",
	"everyone",
	"notify_by_email",
]

_DEFAULT_OWNERSHIP_ASSIGN = ownership.assign


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_distribute_actor(actor):
	"""Verify that `actor` is enabled and holds `CSKH CRM Admin` (or system)."""
	actor = actor or frappe.session.user
	if not actor or actor == "Guest":
		raise Forbidden("Distributing the customers requires the CRM Admin business role.")
	if actor == "Administrator":
		return
	if not frappe.db.get_value("User", actor, "enabled"):
		raise Forbidden("The actor account is disabled.")
	roles = set(frappe.get_roles(actor))
	if "System Manager" in roles or "CSKH CRM Admin" in roles:
		return
	raise Forbidden("Distributing the customers requires the CRM Admin business role.")


def _db_rule_version(campaign):
	"""Read the authoritative rule version directly from MariaDB."""
	row = frappe.db.sql(
		"select coalesce(max(version), 1) from `tabCSKH Assignment Rule` where campaign = %s",
		(campaign,),
	)
	return int(row[0][0] or 1) if row else 1


def _fingerprint(leads):
	"""A stable fingerprint of the selected lead set."""
	payload = json.dumps(sorted(leads), separators=(",", ":"))
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _state_cursor(state):
	"""A comparable summary of the fairness counters of a state."""
	state = state or {}
	return {
		"epoch": int(state.get("epoch", 0) or 0),
		"total": int(state.get("total", 0) or 0),
		"counts": dict(state.get("counts", {}) or {}),
		# The cumulative ledger is part of the cursor: a run that committed
		# allocations changes it, so a preview computed before that commit is
		# refused instead of distributing twice.
		"allocated_total": int(state.get("allocated_total", 0) or 0),
		"allocated_counts": dict(state.get("allocated_counts", {}) or {}),
	}


def _run_state(run):
	if not run.state_json:
		return {}
	try:
		state = json.loads(run.state_json)
	except (ValueError, TypeError):
		return {}
	return state if isinstance(state, dict) else {}


def _run_preview_payload(run):
	if not run.preview_json:
		return {}
	try:
		payload = json.loads(run.preview_json)
	except (ValueError, TypeError):
		return {}
	return payload if isinstance(payload, dict) else {}


def _campaign_loads(campaign):
	"""Current active owned work items (`assigned`, `in_progress`, `on_hold`) per user."""
	rows = frappe.db.sql(
		"""select assigned_to as user, count(*) as total
		   from `tabCSKH Work Item`
		   where campaign = %s
		     and status in ('assigned', 'in_progress', 'on_hold')
		     and assigned_to is not null
		   group by assigned_to""",
		(campaign,),
		as_dict=True,
	)
	return {row.user: int(row.total) for row in rows}


def _reconcile_run_db_counts(campaign, run_name):
	"""Reconcile per-user `assigned` and `created` for `run_name` from DB truth."""
	rows = frappe.db.sql(
		"""select assigned_to as user, count(*) as total
		   from `tabCSKH Work Item`
		   where campaign = %s and allocation_run = %s
		     and status in ('assigned', 'in_progress', 'on_hold')
		     and assigned_to is not null
		   group by assigned_to""",
		(campaign, run_name),
		as_dict=True,
	)
	assigned = {row.user: int(row.total) for row in rows if row.user}
	created = sum(assigned.values())
	return assigned, created


def preview(campaign, actor=None, idempotency_key=None, selection=None):
	"""Compute a dry run of the distribution and return the preview contract."""
	actor = actor or frappe.session.user
	_campaign = frappe.db.get_value("CSKH Campaign", campaign, ["name", "status", "team_scope"], as_dict=True)
	if not _campaign:
		raise NotFound("The campaign does not exist.")
	if _campaign.status not in ("Active", "Draft"):
		raise Conflict("The campaign is not open for distribution.", code="CONFLICT")

	version = eligibility.current_rule_version(campaign)
	eligible_assignees, excluded_assignees = eligibility.eligible_assignees(
		campaign, _campaign.team_scope, version
	)
	selection_result = eligibility.eligible_leads(campaign)
	leads = selection_result["eligible"]

	if not eligible_assignees:
		raise Conflict(
			"No assignee is eligible for this campaign.",
			code="CAPACITY_FULL",
			details={"excluded": excluded_assignees},
		)
	if not leads:
		raise Conflict("No lead is eligible for this campaign.", code="NO_ELIGIBLE_LEADS")

	rules = [
		{"user": rule["user"], "weight": rule["weight"], "capacity": rule["capacity"], "enabled": True}
		for rule in eligible_assignees
	]
	plan = planned_distribution(len(leads), rules)
	loads = _campaign_loads(campaign)

	# The fairness state of the last **committed** run is the base of this
	# preview. The preview must NOT advance the counters: the counters belong
	# to the committed assignments, and the execute replays them from the base.
	base_state = _latest_state(campaign, version)
	simulation = allocate(len(leads), rules, state=base_state, loads=loads)
	actual_preview = {}
	for user in simulation["assignments"]:
		actual_preview[user] = actual_preview.get(user, 0) + 1

	fingerprint = _fingerprint(leads)
	key = idempotency_key or f"preview:{campaign}:{fingerprint}"

	existing = frappe.db.get_value(
		"CSKH Distribution Run",
		{"idempotency_key": key, "campaign": campaign},
		["name", "status"],
		as_dict=True,
	)
	if existing:
		run = frappe.get_doc("CSKH Distribution Run", existing.name)
		return _preview_response(run)

	run = frappe.get_doc(
		{
			"doctype": "CSKH Distribution Run",
			"campaign": campaign,
			"idempotency_key": key,
			"rule_version": version,
			"selection_fingerprint": fingerprint,
			"status": "planned",
			"preview_json": json.dumps(
				{
					"eligible_count": len(leads),
					"excluded_count": selection_result["counts"]["excluded"],
					"targets": plan["targets"],
					"simulated": actual_preview,
					"excluded_assignees": excluded_assignees,
					"leads": list(leads),
				},
				ensure_ascii=False,
			),
			"state_json": json.dumps(base_state, ensure_ascii=False),
			"expires_at": add_to_date(now_datetime(), minutes=PREVIEW_TTL_MINUTES),
		}
	)
	run.insert(ignore_permissions=True)
	return _preview_response(run)


def _latest_state(campaign, version):
	"""The persisted allocator state of the last **committed** run.

	Only a run that actually committed assignments may seed the next epoch: a
	preview is a dry run and must never move the fairness counters (ADR-P1-04
	§2.4).

	The "latest" run is the one with the **highest committed total**, not the
	newest row: several runs may share the same `creation` timestamp (the
	second resolution of the column is not enough to order them), so ordering
	by `creation` alone is not deterministic. The highest total is exactly the
	state the committed assignments produced, and the name is the stable
	tie-break.
	"""
	# `frappe.db.get_all` is the **database level** API: it does not apply the
	# row-level permission, so it is only safe here because the output is
	# reduced to the campaign state of the run, never returned to a caller.
	runs = frappe.db.get_all(
		"CSKH Distribution Run",
		filters={
			"campaign": campaign,
			"rule_version": version,
			"status": ["in", ("completed", "completed_with_errors")],
		},
		fields=["name", "state_json"],
	)
	best = None
	best_key = None
	for run in runs:
		state = _parse_state(run.state_json)
		key = (int(state.get("total", 0) or 0), int(state.get("epoch", 0) or 0), run.name)
		if best_key is None or key > best_key:
			best_key = key
			best = state
	return best or {}


def _parse_state(state_json):
	if not state_json:
		return {}
	try:
		state = json.loads(state_json)
	except (ValueError, TypeError):
		return {}
	return state if isinstance(state, dict) else {}


def _preview_response(run):
	preview_payload = _run_preview_payload(run)
	return {
		"preview_id": run.name,
		"campaign": run.campaign,
		"selection_fingerprint": run.selection_fingerprint,
		"rule_version": run.rule_version,
		"expires_at": run.expires_at,
		"summary": {
			"eligible_count": preview_payload.get("eligible_count", 0),
			"excluded_count": preview_payload.get("excluded_count", 0),
			"targets": preview_payload.get("targets", []),
			"simulated": preview_payload.get("simulated", {}),
			"excluded_assignees": preview_payload.get("excluded_assignees", []),
		},
	}


def _lease_is_active(lease_expires_at, now=None):
	if not lease_expires_at:
		return False
	now = now or now_datetime()
	try:
		return get_datetime(lease_expires_at) > get_datetime(now)
	except Exception:
		return False


def _validate_rule_version_and_campaign(run, expected_rule_version=None):
	from crm_cskh.api._params import parse_int

	campaign_doc = frappe.db.get_value(
		"CSKH Campaign", run.campaign, ["name", "status", "team_scope"], as_dict=True
	)
	if not campaign_doc:
		raise NotFound("The campaign does not exist.")
	if campaign_doc.status not in ("Active", "Draft"):
		raise Conflict("The campaign is not open for distribution.", code="CONFLICT")
	version = _db_rule_version(run.campaign)
	if expected_rule_version is not None and not (
		isinstance(expected_rule_version, str) and expected_rule_version.strip() == ""
	):
		parsed_expected = parse_int(expected_rule_version, minimum=1, field="expected_rule_version")
		if parsed_expected != int(run.rule_version):
			raise RuleChanged(
				"The assignment rules changed after the preview.",
				details={"expected": parsed_expected, "current": version},
			)
	if int(version) != int(run.rule_version):
		raise RuleChanged(
			"The assignment rules changed after the preview.",
			details={"expected": run.rule_version, "current": version},
		)
	return campaign_doc, version


def _target_leads_for_run(run):
	preview_payload = _run_preview_payload(run)
	stored_leads = preview_payload.get("leads")
	if isinstance(stored_leads, list) and stored_leads:
		return list(stored_leads)
	return list(eligibility.selection_leads(run.campaign))


def _enqueue_distribution_job(run_id, actor):
	from crm_cskh.jobs import distribution_worker

	frappe.enqueue(
		distribution_worker.run_distribution,
		queue="long",
		timeout=7200,
		enqueue_after_commit=True,
		job_id=f"cskh-dist-{run_id}",
		deduplicate=True,
		run_id=run_id,
		actor=actor,
	)


def enqueue_run(preview_id, expected_rule_version=None, idempotency_key=None, actor=None, enqueue_job=True):
	"""Validate and enqueue a distribution preview onto RQ (or return idempotently).

	Used directly by `crm_cskh.api.distribution.execute` so HTTP callers receive
	`run_id` and `status='queued'` immediately instead of blocking on 10k items.
	"""
	actor = actor or frappe.session.user
	_require_distribute_actor(actor)
	if not preview_id or not frappe.db.exists("CSKH Distribution Run", preview_id):
		raise NotFound("The preview does not exist.")

	if idempotency_key:
		other_run = frappe.db.get_value("CSKH Distribution Run", {"idempotency_key": idempotency_key}, "name")
		if other_run and other_run != preview_id:
			raise Conflict(
				"This idempotency key was already used by another distribution run.",
				code="CONFLICT",
			)

	locked = frappe.db.sql(
		"select name, status, lease_expires_at, progress from `tabCSKH Distribution Run` where name = %s for update",
		(preview_id,),
		as_dict=True,
	)
	if not locked:
		raise NotFound("The preview does not exist.")

	run = frappe.get_doc("CSKH Distribution Run", preview_id)
	now = now_datetime()

	if run.status == "completed":
		return _run_response(run)

	if run.status in ("queued", "running"):
		if _lease_is_active(run.lease_expires_at, now):
			# Double-submit / retry while job is already queued or actively owned:
			# return current status and progress without enqueueing a second job.
			return _run_response(run)
		# Lease expired (worker crashed): re-verify rules/campaign, transition
		# back to `queued` with a fresh lease, and re-enqueue recovery from `run.progress`.
		_validate_rule_version_and_campaign(run, expected_rule_version=expected_rule_version)
		actual = _run_actual(run)
		actual["actor"] = actor
		run.actual_json = json.dumps(actual, ensure_ascii=False)
		run.status = "queued"
		run.lease_expires_at = add_to_date(now, seconds=LEASE_SECONDS)
		run.save(ignore_permissions=True)
		if enqueue_job:
			_enqueue_distribution_job(run.name, actor)
		return _run_response(run)

	if run.status in ("completed_with_errors", "failed"):
		_validate_rule_version_and_campaign(run, expected_rule_version=expected_rule_version)
		target_leads = _target_leads_for_run(run)
		assigned_leads = {
			row.lead
			for row in frappe.db.get_all(
				"CSKH Work Item",
				filters={"campaign": run.campaign, "allocation_run": run.name},
				fields=["lead"],
			)
		}
		prior_skipped = _run_skipped(run)
		retryable_failed = {
			entry.get("lead")
			for entry in prior_skipped
			if entry.get("reason") == "assignment_failed"
			and entry.get("lead")
			and entry.get("lead") not in assigned_leads
		}
		unprocessed = [lead for lead in target_leads[int(run.progress or 0) :] if lead not in assigned_leads]
		if not retryable_failed and not unprocessed:
			return _run_response(run)

		actual = _run_actual(run)
		actual["actor"] = actor
		actual["retry_failed"] = True
		actual.setdefault("total", len(target_leads))
		run.actual_json = json.dumps(actual, ensure_ascii=False)
		run.status = "queued"
		run.error = None
		run.finished_at = None
		run.lease_expires_at = add_to_date(now, seconds=LEASE_SECONDS)
		run.save(ignore_permissions=True)
		if enqueue_job:
			_enqueue_distribution_job(run.name, actor)
		return _run_response(run)

	# `status == "planned"`
	if run.expires_at and get_datetime(run.expires_at) < get_datetime(now):
		raise Conflict("The preview has expired; run the preview again.", code="PREVIEW_EXPIRED")

	campaign_doc, _ = _validate_rule_version_and_campaign(run, expected_rule_version=expected_rule_version)

	# Verify fairness cursor has not moved since the preview was computed.
	base_state = _run_state(run)
	latest_state = _latest_state(run.campaign, run.rule_version)
	if _state_cursor(base_state) != _state_cursor(latest_state):
		raise RuleChanged(
			"The distribution state changed after the preview.",
			details={"expected": _state_cursor(base_state), "current": _state_cursor(latest_state)},
		)

	current_eligible = eligibility.eligible_leads(run.campaign)["eligible"]
	if _fingerprint(current_eligible) != run.selection_fingerprint:
		run.status = "failed"
		run.error = "RULE_CHANGED: The eligible lead set changed after the preview."
		run.finished_at = now
		run.lease_expires_at = None
		run.save(ignore_permissions=True)
		frappe.db.commit()
		raise RuleChanged("The eligible lead set changed after the preview.")

	curr_eligible_assignees, curr_excluded_assignees = _eligible_assignees_for_worker(
		run.campaign, campaign_doc.team_scope, run.rule_version, actor
	)
	if not curr_eligible_assignees:
		raise Conflict(
			"No assignee is eligible for this campaign.",
			code="CAPACITY_FULL",
			details={"excluded": curr_excluded_assignees},
		)

	if idempotency_key and idempotency_key != run.idempotency_key:
		run.idempotency_key = idempotency_key

	preview_payload = _run_preview_payload(run)
	if not preview_payload.get("leads"):
		preview_payload["leads"] = list(current_eligible)
		run.preview_json = json.dumps(preview_payload, ensure_ascii=False)

	run.actual_json = json.dumps(
		{
			"actor": actor,
			"total": len(current_eligible),
			"assigned": {},
			"created": 0,
			"errors": 0,
			"skipped": [],
			"excluded_assignees": curr_excluded_assignees,
		},
		ensure_ascii=False,
	)
	run.progress = 0
	run.error = None
	run.finished_at = None
	run.status = "queued"
	run.lease_expires_at = add_to_date(now, seconds=LEASE_SECONDS)
	run.save(ignore_permissions=True)
	if enqueue_job:
		_enqueue_distribution_job(run.name, actor)
	return _run_response(run)


def execute(preview_id, expected_rule_version=None, idempotency_key=None, actor=None):
	"""Synchronous service entry point: enqueue in-DB then run chunked worker inline."""
	actor = actor or frappe.session.user
	queued = enqueue_run(
		preview_id,
		expected_rule_version=expected_rule_version,
		idempotency_key=idempotency_key,
		actor=actor,
		enqueue_job=False,
	)
	if queued["status"] != "queued":
		return queued
	frappe.db.commit()
	return run_distribution_worker(queued["run_id"], actor=actor)


def _abort_run(run, actor, code_prefix, message):
	"""Persist a clean terminal failure/partial status when execution-time rechecks fail."""
	run.reload()
	actual = _run_actual(run)
	assigned, created = _reconcile_run_db_counts(run.campaign, run.name)
	skipped = actual.get("skipped", []) or []
	actual["assigned"] = assigned
	actual["created"] = created
	actual["errors"] = len(skipped)
	actual["actor"] = actor
	actual.pop("retry_failed", None)
	run.actual_json = json.dumps(actual, ensure_ascii=False)
	run.status = "completed_with_errors" if created > 0 else "failed"
	run.error = f"{code_prefix}: {message}"[:500]
	run.finished_at = now_datetime()
	run.lease_expires_at = None
	run.save(ignore_permissions=True)
	frappe.db.commit()
	return _run_response(run)


def _eligible_assignees_for_worker(campaign, team_scope, version, effective_actor):
	previous_user = getattr(frappe.session, "user", None)
	try:
		if effective_actor and previous_user != effective_actor:
			frappe.set_user(effective_actor)
		return eligibility.eligible_assignees(campaign, team_scope, version)
	finally:
		if previous_user and getattr(frappe.session, "user", None) != previous_user:
			frappe.set_user(previous_user)


def run_distribution_worker(run_id, actor=None, *, diagnostics_callback=None):
	"""Chunked RQ worker execution with DB row lock, lease renewal, and recovery."""
	if not run_id:
		return None

	locked = frappe.db.sql(
		"select name, status, lease_expires_at, progress from `tabCSKH Distribution Run` where name = %s for update",
		(run_id,),
		as_dict=True,
	)
	if not locked:
		return None

	row = locked[0]
	now = now_datetime()
	if row.status in ("completed", "completed_with_errors", "failed"):
		return _run_response(frappe.get_doc("CSKH Distribution Run", run_id))
	if row.status == "running" and _lease_is_active(row.lease_expires_at, now):
		# Another live worker currently holds an unexpired lease on this run.
		return _run_response(frappe.get_doc("CSKH Distribution Run", run_id))
	if row.status not in ("planned", "queued", "running"):
		return _run_response(frappe.get_doc("CSKH Distribution Run", run_id))

	run = frappe.get_doc("CSKH Distribution Run", run_id)
	actual = _run_actual(run)
	effective_actor = actor or actual.get("actor") or frappe.session.user

	# Claim ownership of the run and commit the lease before processing chunks.
	run.status = "running"
	if not run.started_at:
		run.started_at = now
	run.lease_expires_at = add_to_date(now, seconds=LEASE_SECONDS)
	run.save(ignore_permissions=True)
	frappe.db.commit()

	# Execution-time recheck #1: actor permission & enabled status.
	try:
		_require_distribute_actor(effective_actor)
	except Forbidden as exc:
		return _abort_run(run, effective_actor, "FORBIDDEN", str(exc))

	# Execution-time recheck #2: campaign status & rule_version.
	try:
		campaign_doc, version = _validate_rule_version_and_campaign(run)
	except RuleChanged as exc:
		return _abort_run(run, effective_actor, "RULE_CHANGED", str(exc))
	except (Conflict, NotFound) as exc:
		return _abort_run(run, effective_actor, exc.code, str(exc))

	campaign = run.campaign
	_initial_assigned, initial_created = _reconcile_run_db_counts(campaign, run.name)
	initial_progress = int(run.progress or 0)

	# Execution-time recheck #3: if zero items have been processed yet, verify
	# that the eligible lead set still matches the preview's selection_fingerprint.
	if initial_progress == 0 and initial_created == 0:
		current_eligible = eligibility.eligible_leads(campaign)["eligible"]
		if _fingerprint(current_eligible) != run.selection_fingerprint:
			return _abort_run(
				run,
				effective_actor,
				"RULE_CHANGED",
				"The eligible lead set changed after the preview.",
			)
		target_leads = list(current_eligible)
	else:
		target_leads = _target_leads_for_run(run)

	total_targets = len(target_leads)
	already_assigned_leads = {
		item.lead
		for item in frappe.db.get_all(
			"CSKH Work Item",
			filters={"campaign": campaign, "allocation_run": run.name},
			fields=["lead"],
		)
	}

	retry_failed_mode = bool(actual.get("retry_failed"))
	prior_skipped = _run_skipped(run)
	if retry_failed_mode:
		carried_skipped = [
			entry
			for entry in prior_skipped
			if entry.get("reason") != "assignment_failed" and entry.get("lead") not in already_assigned_leads
		]
		retryable_leads = {
			entry.get("lead")
			for entry in prior_skipped
			if entry.get("reason") == "assignment_failed"
			and entry.get("lead")
			and entry.get("lead") not in already_assigned_leads
		}
	else:
		carried_skipped = [
			entry for entry in prior_skipped if entry.get("lead") not in already_assigned_leads
		]
		retryable_leads = set()

	# Build work queue of `(target_index, lead)`:
	#   * any retryable failed lead from earlier attempts (`idx < initial_progress`);
	#   * every unprocessed lead from `initial_progress .. total_targets - 1`.
	work_entries = []
	for idx, lead in enumerate(target_leads):
		if lead in already_assigned_leads:
			continue
		if idx < initial_progress:
			if lead in retryable_leads:
				work_entries.append((idx, lead))
		else:
			work_entries.append((idx, lead))

	state = _run_state(run)
	new_skipped = []
	excluded_assignees = actual.get("excluded_assignees", [])
	worker_owner_cache: dict[str, dict] = {}
	last_meta_sig: tuple | None = None

	from crm_cskh.services import kpi_runs

	prev_suppress = bool(getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False))
	frappe.flags.cskh_suppress_dirty_enqueue = True

	try:
		for chunk_offset in range(0, len(work_entries), CHUNK_SIZE):
			# Re-verify actor permission, campaign status & rule_version before EVERY chunk.
			try:
				_require_distribute_actor(effective_actor)
				campaign_doc, version = _validate_rule_version_and_campaign(run)
			except Forbidden as exc:
				return _abort_run(run, effective_actor, "FORBIDDEN", str(exc))
			except RuleChanged as exc:
				return _abort_run(run, effective_actor, "RULE_CHANGED", str(exc))
			except (Conflict, NotFound) as exc:
				return _abort_run(run, effective_actor, exc.code, str(exc))

			# Execution-time recheck #4: re-evaluate assignee eligibility & DB loads per chunk.
			eligible_assignees, excluded_assignees = _eligible_assignees_for_worker(
				campaign, campaign_doc.team_scope, version, effective_actor
			)
			rules = [
				{
					"user": rule["user"],
					"weight": rule["weight"],
					"capacity": rule["capacity"],
					"enabled": True,
				}
				for rule in eligible_assignees
			]
			loads = _campaign_loads(campaign)

			chunk = work_entries[chunk_offset : chunk_offset + CHUNK_SIZE]
			pending_notifications = []
			highest_index = initial_progress
			chunk_leads = [ld for _, ld in chunk if ld]
			already_in_chunk: set[str] = set()
			chunk_exclusions: dict[str, str] | None = None
			if len(chunk_leads) > 1 and len(set(chunk_leads)) == len(chunk_leads):
				ph = ", ".join(["%s"] * len(chunk_leads))
				already_in_chunk = set(
					frappe.db.sql(
						f"select lead from `tabCSKH Work Item` where campaign = %s and allocation_run = %s and lead in ({ph})",
						(campaign, run.name, *chunk_leads),
						pluck=True,
					)
					or ()
				)
				chunk_exclusions = eligibility._bulk_lead_exclusion_reasons(campaign, chunk_leads)

			batch_completed = False
			if (
				chunk_exclusions is not None
				and ownership.assign is _DEFAULT_OWNERSHIP_ASSIGN
				and _create_work_item is _DEFAULT_CREATE_WORK_ITEM
			):
				curr_meta_sig = _read_distribution_meta_sig(campaign_doc.team_scope)
				if curr_meta_sig != last_meta_sig:
					worker_owner_cache.clear()
				state_before_chunk = json.loads(json.dumps(state))
				loads_before_chunk = dict(loads)
				skipped_len_before = len(new_skipped)
				local_obj = getattr(frappe, "local", None)
				dirty_len_before = (
					len(getattr(local_obj, "cskh_pending_dirty_events", None) or [])
					if local_obj is not None
					else 0
				)
				batch_savepoint = f"cskh_dist_batch_{chunk_offset}"
				frappe.db.savepoint(batch_savepoint)
				try:
					highest_index, state, loads, pending_notifications = _batch_assign_chunk(
						chunk=chunk,
						highest_index=highest_index,
						already_in_chunk=already_in_chunk,
						chunk_exclusions=chunk_exclusions,
						rules=rules,
						state=state,
						loads=loads,
						new_skipped=new_skipped,
						campaign_doc=campaign_doc,
						run=run,
						effective_actor=effective_actor,
						worker_owner_cache=worker_owner_cache,
					)
					last_meta_sig = _read_distribution_meta_sig(campaign_doc.team_scope)
					batch_completed = True
				except Exception:
					frappe.db.rollback(save_point=batch_savepoint)
					state = state_before_chunk
					loads = loads_before_chunk
					del new_skipped[skipped_len_before:]
					if local_obj is not None and getattr(local_obj, "cskh_pending_dirty_events", None):
						del local_obj.cskh_pending_dirty_events[dirty_len_before:]
					worker_owner_cache.clear()
					last_meta_sig = None
					highest_index = initial_progress
					pending_notifications = []
					ph = ", ".join(["%s"] * len(chunk_leads))
					already_in_chunk = set(
						frappe.db.sql(
							f"select lead from `tabCSKH Work Item` where campaign = %s and allocation_run = %s and lead in ({ph})",
							(campaign, run.name, *chunk_leads),
							pluck=True,
						)
						or ()
					)
					chunk_exclusions = eligibility._bulk_lead_exclusion_reasons(campaign, chunk_leads)

			if not batch_completed:
				for row_pos, (target_idx, lead) in enumerate(chunk):
					highest_index = max(highest_index, target_idx + 1)
					# Idempotency guard if a lead was already committed by an earlier attempt.
					if (
						lead in already_in_chunk
						if chunk_exclusions is not None
						else frappe.db.exists(
							"CSKH Work Item",
							{"campaign": campaign, "lead": lead, "allocation_run": run.name},
						)
					):
						continue

					# Re-check lead eligibility at execution time (P1 §7.1 #5).
					lead_exclusion = (
						chunk_exclusions.get(lead)
						if chunk_exclusions is not None
						else eligibility._lead_exclusion_reason(campaign, lead)
					)
					if lead_exclusion:
						new_skipped.append({"lead": lead, "reason": lead_exclusion})
						continue

					savepoint = f"cskh_dist_{chunk_offset}_{row_pos}"
					state_before = json.loads(json.dumps(state))
					loads_before = dict(loads)
					frappe.db.savepoint(savepoint)
					try:
						owner, state, loads = _pick_owner(rules, state, loads)
						if owner is None:
							frappe.db.rollback(save_point=savepoint)
							new_skipped.append({"lead": lead, "reason": "no_eligible_assignee"})
							continue
						work_item = _create_work_item(campaign, lead, run)
						ownership.assign(
							work_item,
							owner,
							actor=effective_actor,
							reason=f"Distribution run {run.name}",
							request_id=f"run:{run.name}:{lead}",
							distribution_run=run.name,
						)
						pending_notifications.append((work_item, lead, owner))
					except Conflict as exc:
						frappe.db.rollback(save_point=savepoint)
						state = state_before
						loads = loads_before
						if exc.code == "ALREADY_ASSIGNED":
							reason_code = "already_claimed"
						elif exc.code == "DO_NOT_CONTACT":
							reason_code = "do_not_contact"
						else:
							reason_code = "assignment_failed"
						new_skipped.append({"lead": lead, "reason": reason_code, "detail": str(exc)[:200]})
					except Exception as exc:
						frappe.db.rollback(save_point=savepoint)
						state = state_before
						loads = loads_before
						new_skipped.append(
							{"lead": lead, "reason": "assignment_failed", "detail": str(exc)[:200]}
						)

			# Checkpoint after every chunk: reconcile DB truth, update `progress`,
			# renew `lease_expires_at`, and commit before delivering notifications.
			run.reload()
			db_assigned, db_created = _reconcile_run_db_counts(campaign, run.name)
			combined_skipped = _dedupe_skipped(carried_skipped + new_skipped, campaign, run.name)
			initial_progress = max(int(run.progress or 0), highest_index)
			run.progress = min(total_targets, initial_progress)
			run.lease_expires_at = add_to_date(now_datetime(), seconds=LEASE_SECONDS)
			run.state_json = json.dumps(state, ensure_ascii=False)
			run.actual_json = json.dumps(
				{
					"actor": effective_actor,
					"total": total_targets,
					"assigned": db_assigned,
					"created": db_created,
					"errors": len(combined_skipped),
					"skipped": combined_skipped,
					"excluded_assignees": excluded_assignees,
				},
				ensure_ascii=False,
			)
			run.save(ignore_permissions=True)
			frappe.db.commit()
			if diagnostics_callback:
				try:
					diagnostics_callback(int(run.progress or 0))
				except Exception:
					# Optional instrumentation must not affect the committed job chunk.
					pass

			if pending_notifications:
				_deliver_notifications(run, pending_notifications, effective_actor)
		# Finalize run status from DB truth BEFORE flushing deferred dirty-scope worker enqueue.
		run.reload()
		if not work_entries:
			_, excluded_assignees = _eligible_assignees_for_worker(
				campaign, campaign_doc.team_scope, version, effective_actor
			)
		db_assigned, db_created = _reconcile_run_db_counts(campaign, run.name)
		combined_skipped = _dedupe_skipped(carried_skipped + new_skipped, campaign, run.name)
		total_errors = len(combined_skipped)

		run.progress = total_targets
		run.state_json = json.dumps(state, ensure_ascii=False)
		run.actual_json = json.dumps(
			{
				"actor": effective_actor,
				"total": total_targets,
				"assigned": db_assigned,
				"created": db_created,
				"errors": total_errors,
				"skipped": combined_skipped,
				"excluded_assignees": excluded_assignees,
			},
			ensure_ascii=False,
		)
		run.status = "completed_with_errors" if total_errors else "completed"
		run.error = None
		run.finished_at = now_datetime()
		run.lease_expires_at = None
		run.save(ignore_permissions=True)
		frappe.db.commit()
		return _run_response(run)
	except Exception as exc:
		frappe.db.rollback()
		return _abort_run(run, effective_actor, "RETRYABLE_FAILURE", str(exc))
	finally:
		frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress
		if not prev_suppress:
			kpi_runs.flush_suppressed_dirty_scope_enqueue()


def _dedupe_skipped(entries, campaign, run_name):
	"""Keep at most one skipped record per lead, excluding leads now assigned by `run_name`."""
	assigned_leads = {
		row.lead
		for row in frappe.db.get_all(
			"CSKH Work Item",
			filters={"campaign": campaign, "allocation_run": run_name},
			fields=["lead"],
		)
	}
	by_lead = {}
	order = []
	for entry in entries or []:
		lead = entry.get("lead")
		if not lead or lead in assigned_leads:
			continue
		if lead not in by_lead:
			order.append(lead)
		by_lead[lead] = entry
	return [by_lead[lead] for lead in order]


def _deliver_notifications(run, pending, actor=None):
	"""Deliver the assignment notifications of a committed run.

	The delivery is idempotent (the ToDo and the notification are looked up
	before they are created), so a retry after a failed delivery re-notifies
	the assignee without creating a second assignment event (P1 §12.4).
	"""
	from crm_cskh.services import notifications

	res = notifications.deliver_assignments_batch(pending, run.name, actor=actor)
	if res.get("delivered"):
		frappe.db.commit()
	return res


def _unique_hashes(count: int, length: int = 10) -> list[str]:
	seen: set[str] = set()
	out: list[str] = []
	while len(out) < count:
		h = frappe.generate_hash(length=length)
		if h not in seen:
			seen.add(h)
			out.append(h)
	return out


def _read_distribution_meta_sig(campaign_team_scope: str | None) -> tuple:
	conf_raw = getattr(frappe, "conf", None)
	conf_sig = (
		tuple(
			(k, repr(conf_raw.get(k)))
			for k in ("cskh_p3_enabled", "p3_enabled", "cskh_p3_sla_enabled", "p3_sla_enabled")
			if k in conf_raw
		)
		if isinstance(conf_raw, dict)
		else ()
	)
	union_parts = []
	for tbl in (
		"CSKH Policy",
		"CSKH Working Calendar",
		"CRM Holiday List",
		"CSKH Team Membership History",
		"CRM Sales Hierarchy",
	):
		if frappe.db.table_exists(tbl):
			union_parts.append(
				f"select '{tbl}' as t, count(*) as c, coalesce(cast(max(modified) as char), '') as m from `tab{tbl}`"
			)
	db_sig: tuple = ()
	if union_parts:
		db_sig = tuple(
			(str(r[0]), int(r[1] or 0), str(r[2] or ""))
			for r in frappe.db.sql(" union all ".join(union_parts))
		)
	return (campaign_team_scope or "", conf_sig, db_sig)


def _resolve_batch_owner_contexts(
	*,
	campaign: str,
	campaign_team_scope: str | None,
	owners: list[str],
	now_dt,
	worker_owner_cache: dict[str, dict],
) -> dict[str, dict]:
	from frappe.utils import cint

	from crm_cskh.services import sla_engine

	missing_owners = [u for u in owners if u not in worker_owner_cache]
	if not missing_owners:
		return worker_owner_cache

	# 1. Batch-read authoritative team intervals for all uncached owners in 1 SQL query
	team_by_user: dict[str, tuple[str | None, str]] = {}
	if frappe.db.table_exists("CSKH Team Membership History"):
		ph_u = ", ".join(["%s"] * len(missing_owners))
		for row in frappe.db.sql(
			f"""
			select user, team_node
			from `tabCSKH Team Membership History`
			where user in ({ph_u})
			  and effective_from <= %s
			  and (effective_to is null or %s < effective_to)
			order by effective_from desc, creation desc
			""",
			(*missing_owners, now_dt, now_dt),
			as_dict=True,
		):
			u_str = str(row.user)
			if u_str not in team_by_user and row.team_node:
				team_by_user[u_str] = (str(row.team_node), AUTHORITATIVE)

	for u in missing_owners:
		if u not in team_by_user:
			team_by_user[u] = resolve_team_for_user_at(
				u,
				now_dt,
				allow_live_capture_for_current_tx=True,
			)

	# 2. Resolve P3 SLA policy & calendar (shared across campaign when no per-user Team policy applies)
	sla_ready = sla_engine._p3_sla_ready()
	shared_sla_bundle: dict | None = None
	can_share_policy = bool(campaign_team_scope) or (
		not frappe.db.table_exists("CSKH Policy")
		or not frappe.db.sql(
			"select 1 from `tabCSKH Policy` where scope_type = 'Team' and enabled = 1 limit 1"
		)
	)

	def _build_sla_bundle(owner_for_lookup: str | None) -> dict:
		if not sla_ready or not sla_engine.is_p3_sla_enabled(campaign=campaign, owner_user=owner_for_lookup):
			return {"enabled": False}
		policy, calendar = sla_engine._resolve_policy_and_calendar(
			campaign=campaign, owner_user=owner_for_lookup
		)
		if not cint(policy.get("p3_sla_enabled", 1)):
			return {"enabled": False}
		pol_json = json.dumps(policy, ensure_ascii=False, sort_keys=True, default=str)
		cal_json = calendar.to_snapshot_json()
		return {
			"enabled": True,
			"policy_name": policy.get("name"),
			"policy_version": cint(policy.get("version", 1)),
			"pol_json": pol_json,
			"cal_json": cal_json,
			"calendar": calendar,
			"fa_min": max(cint(policy.get("first_attempt_sla_minutes", 30)), 0),
			"track_fc": bool(cint(policy.get("track_first_connected", 1))),
			"fc_min": max(cint(policy.get("first_connected_target_minutes", 120)), 0),
		}

	if can_share_policy and missing_owners:
		shared_sla_bundle = _build_sla_bundle(missing_owners[0])

	for u in missing_owners:
		team_node, attr_status = team_by_user[u]
		sla_bundle = shared_sla_bundle if shared_sla_bundle is not None else _build_sla_bundle(u)
		worker_owner_cache[u] = {
			"team_node": team_node,
			"attr_status": attr_status,
			"sla": sla_bundle,
		}

	return worker_owner_cache


def _batch_assign_chunk(
	*,
	chunk,
	highest_index: int,
	already_in_chunk: set[str],
	chunk_exclusions: dict[str, str],
	rules: list[dict],
	state: dict,
	loads: dict[str, int],
	new_skipped: list[dict],
	campaign_doc,
	run,
	effective_actor: str,
	worker_owner_cache: dict[str, dict],
):
	"""Execute one distribution chunk using batched SQL reads/writes inside the caller's savepoint."""
	from frappe.utils import cint

	from crm_cskh.services import sla_engine

	campaign = run.campaign
	ownership._require_actor_permission(effective_actor, ownership.ASSIGN)
	if campaign_doc.status == "Closed":
		raise Conflict("The campaign is closed and cannot accept ownership changes.", code="CONFLICT")

	planned_pairs: list[tuple[str, str]] = []
	for target_idx, lead in chunk:
		highest_index = max(highest_index, target_idx + 1)
		if lead in already_in_chunk:
			continue
		lead_exclusion = chunk_exclusions.get(lead)
		if lead_exclusion:
			new_skipped.append({"lead": lead, "reason": lead_exclusion})
			continue
		owner, state, loads = _pick_owner(rules, state, loads)
		if owner is None:
			new_skipped.append({"lead": lead, "reason": "no_eligible_assignee"})
			continue
		planned_pairs.append((lead, owner))

	if not planned_pairs:
		return highest_index, state, loads, []

	assigned_leads = [ld for ld, _ in planned_pairs]
	ph_leads = ", ".join(["%s"] * len(assigned_leads))

	# Lock target `CRM Lead` rows and verify zero concurrent DNC / active claim / active work item races.
	has_dnc_col = bool(frappe.db.has_column("CRM Lead", "cskh_do_not_contact"))
	if has_dnc_col:
		locked_leads = frappe.db.sql(
			f"select name, coalesce(cskh_do_not_contact, 0) as dnc from `tabCRM Lead` where name in ({ph_leads}) for update",
			tuple(assigned_leads),
			as_dict=True,
		)
		if len(locked_leads) != len(assigned_leads) or any(cint(r.dnc) for r in locked_leads):
			raise Conflict("Concurrent lead mutation or DNC detected during batch.", code="CONFLICT")
	else:
		locked_leads = frappe.db.sql(
			f"select name from `tabCRM Lead` where name in ({ph_leads}) for update",
			tuple(assigned_leads),
		)
		if len(locked_leads) != len(assigned_leads):
			raise Conflict("Lead missing during batch.", code="CONFLICT")

	if frappe.db.sql(
		f"select 1 from `tabCSKH Active Claim` where lead in ({ph_leads}) limit 1",
		tuple(assigned_leads),
	):
		raise Conflict("Concurrent active claim detected during batch.", code="ALREADY_ASSIGNED")

	ph_act_states = ", ".join(["%s"] * len(ACTIVE_WORK_ITEM_STATES))
	if frappe.db.sql(
		f"select 1 from `tabCSKH Work Item` where lead in ({ph_leads}) and status in ({ph_act_states}) limit 1",
		(*assigned_leads, *ACTIVE_WORK_ITEM_STATES),
	):
		raise Conflict("Concurrent active work item detected during batch.", code="ALREADY_ASSIGNED")

	# Re-verify finite assignee capacities against live DB counts inside transaction.
	cap_by_user = {r["user"]: int(r["capacity"] or 0) for r in rules if int(r.get("capacity") or 0) > 0}
	if cap_by_user:
		batch_owner_counts: dict[str, int] = {}
		for _ld, ow in planned_pairs:
			if ow in cap_by_user:
				batch_owner_counts[ow] = batch_owner_counts.get(ow, 0) + 1
		if batch_owner_counts:
			capped_users = sorted(batch_owner_counts.keys())
			ph_cap = ", ".join(["%s"] * len(capped_users))
			ph_owned = ", ".join(["%s"] * len(OWNED_ACTIVE_WORK_ITEM_STATES))
			live_counts = {
				str(r.assigned_to): int(r.c)
				for r in frappe.db.sql(
					f"""
					select assigned_to, count(*) as c
					from `tabCSKH Work Item`
					where campaign = %s
					  and assigned_to in ({ph_cap})
					  and status in ({ph_owned})
					group by assigned_to
					""",
					(campaign, *capped_users, *OWNED_ACTIVE_WORK_ITEM_STATES),
					as_dict=True,
				)
			}
			for u_cap, add_cnt in batch_owner_counts.items():
				if live_counts.get(u_cap, 0) + add_cnt > cap_by_user[u_cap]:
					raise Conflict("Concurrent capacity exhaustion during batch.", code="CAPACITY_EXCEEDED")

	now_ts = now_datetime()
	today_str = now_ts.date().isoformat()
	session_user = (
		getattr(getattr(frappe, "session", None), "user", None) or effective_actor or "Administrator"
	)
	distinct_owners = sorted({ow for _, ow in planned_pairs})
	owner_ctxs = _resolve_batch_owner_contexts(
		campaign=campaign,
		campaign_team_scope=campaign_doc.team_scope,
		owners=distinct_owners,
		now_dt=now_ts,
		worker_owner_cache=worker_owner_cache,
	)

	n_items = len(planned_pairs)
	wi_names = _unique_hashes(n_items, 10)
	claim_names = _unique_hashes(n_items, 10)
	hist_names = _unique_hashes(n_items, 10)
	pub_reason = ownership.public_history_reason(ownership.ASSIGN, distribution_run=run.name)
	has_hist_attr_col = bool(frappe.db.has_column("CSKH Assignment History", "attribution_status"))
	hist_fields = (
		[*ASSIGNMENT_HISTORY_BASE_BULK_FIELDS, "team_at_assignment", "attribution_status"]
		if has_hist_attr_col
		else ASSIGNMENT_HISTORY_BASE_BULK_FIELDS
	)

	wi_rows: list[tuple] = []
	claim_rows: list[tuple] = []
	hist_rows: list[tuple] = []
	ob_specs: list[tuple] = []
	due_cache: dict[tuple[int, int, str], tuple] = {}
	notifications_out: list[tuple[str, str, str]] = []

	for idx, (lead, owner) in enumerate(planned_pairs):
		wi_name = wi_names[idx]
		claim_name = claim_names[idx]
		hist_name = hist_names[idx]
		event_id = ownership._event_id(f"run:{run.name}:{lead}", wi_name, ownership.ASSIGN)
		octx = owner_ctxs[owner]
		team_node = octx["team_node"]
		attr_status = octx["attr_status"]

		wi_rows.append(
			(
				wi_name,
				now_ts,
				now_ts,
				session_user,
				session_user,
				0,
				0,
				campaign,
				lead,
				"assigned",
				1,
				owner,
				now_ts,
				run.name,
				0,
				0,
				1,
			)
		)
		claim_rows.append(
			(
				claim_name,
				now_ts,
				now_ts,
				session_user,
				session_user,
				0,
				0,
				lead,
				campaign,
				wi_name,
			)
		)
		base_hist = (
			hist_name,
			now_ts,
			now_ts,
			session_user,
			session_user,
			0,
			0,
			wi_name,
			ownership.ASSIGN,
			None,
			owner,
			effective_actor,
			now_ts,
			pub_reason,
			None,
			None,
			run.name,
			now_ts,
			None,
			event_id,
			None,
		)
		hist_rows.append((*base_hist, team_node, attr_status) if has_hist_attr_col else base_hist)
		notifications_out.append((wi_name, lead, owner))

		sla_info = octx["sla"]
		if sla_info.get("enabled"):
			cache_key = (sla_info["fa_min"], sla_info["fc_min"], sla_info["cal_json"])
			if cache_key not in due_cache:
				cal = sla_info["calendar"]
				fa_due = add_business_minutes(now_ts, sla_info["fa_min"], cal)
				fa_audit = json.dumps(
					[
						{
							"event": "created_assign",
							"actor": effective_actor,
							"at": str(now_ts),
							"target_minutes": sla_info["fa_min"],
							"due_at": str(fa_due),
						}
					],
					ensure_ascii=False,
				)
				if sla_info["track_fc"]:
					fc_due = add_business_minutes(now_ts, sla_info["fc_min"], cal)
					fc_audit = json.dumps(
						[
							{
								"event": "created_informational",
								"actor": effective_actor,
								"at": str(now_ts),
								"target_minutes": sla_info["fc_min"],
								"due_at": str(fc_due),
							}
						],
						ensure_ascii=False,
					)
				else:
					fc_due = None
					fc_audit = ""
				due_cache[cache_key] = (fa_due, fa_audit, fc_due, fc_audit)
			else:
				fa_due, fa_audit, fc_due, fc_audit = due_cache[cache_key]

			ob_specs.append(
				(
					"FIRST_ATTEMPT",
					wi_name,
					lead,
					hist_name,
					owner,
					team_node,
					attr_status,
					0,
					fa_due,
					fa_audit,
					sla_info,
				)
			)
			if sla_info["track_fc"] and fc_due is not None:
				ob_specs.append(
					(
						"FIRST_CONNECTED",
						wi_name,
						lead,
						hist_name,
						owner,
						team_node,
						attr_status,
						1,
						fc_due,
						fc_audit,
						sla_info,
					)
				)
			sla_engine._notify_dirty_post_commit(
				work_item=wi_name,
				campaign=campaign,
				owner_user=owner,
				team_node=team_node,
				event_dt=now_ts,
				reason="interval_opened",
			)

	frappe.db.bulk_insert("CSKH Work Item", fields=WORK_ITEM_BULK_FIELDS, values=wi_rows)
	frappe.db.bulk_insert("CSKH Active Claim", fields=ACTIVE_CLAIM_BULK_FIELDS, values=claim_rows)
	frappe.db.bulk_insert("CSKH Assignment History", fields=hist_fields, values=hist_rows)

	if ob_specs:
		ob_names = _unique_hashes(len(ob_specs), 10)
		ob_rows = [
			(
				ob_names[i],
				now_ts,
				now_ts,
				session_user,
				session_user,
				0,
				0,
				ob_type,
				wi_name,
				lead,
				campaign,
				hist_name,
				owner,
				team_node,
				attr_status,
				None,
				"open",
				info_only,
				0,
				0,
				0,
				0,
				now_ts,
				due_dt,
				due_dt,
				due_dt,
				None,
				0,
				0.0,
				sla_info["policy_name"],
				sla_info["policy_version"],
				sla_info["pol_json"],
				sla_info["cal_json"],
				audit_json,
				f"{ob_type}:{wi_name}:{hist_name}",
			)
			for i, (
				ob_type,
				wi_name,
				lead,
				hist_name,
				owner,
				team_node,
				attr_status,
				info_only,
				due_dt,
				audit_json,
				sla_info,
			) in enumerate(ob_specs)
		]
		with authorized_sla_write():
			frappe.db.bulk_insert(
				"CSKH SLA Obligation",
				fields=SLA_OBLIGATION_BULK_FIELDS,
				values=ob_rows,
			)

	# Sync `CRM Lead.lead_owner` (and `_assign` when column exists) in 1 batched UPDATE.
	expected_owner_by_lead = dict(planned_pairs)
	owner_cases = " ".join(["when %s then %s"] * n_items)
	owner_params: list = []
	for ld, ow in planned_pairs:
		owner_params.extend((ld, ow))
	if frappe.db.has_column("CRM Lead", "_assign"):
		assign_params: list = []
		for ld, ow in planned_pairs:
			assign_params.extend((ld, json.dumps([ow])))
		frappe.db.sql(
			f"""
			update `tabCRM Lead`
			set lead_owner = case name {owner_cases} end,
			    `_assign` = case name {owner_cases} end
			where name in ({ph_leads})
			""",
			(*owner_params, *assign_params, *assigned_leads),
		)
	else:
		frappe.db.sql(
			f"""
			update `tabCRM Lead`
			set lead_owner = case name {owner_cases} end
			where name in ({ph_leads})
			""",
			(*owner_params, *assigned_leads),
		)

	# Cancel stale `Open` `ToDo`s and bulk-insert missing owner `Open` `ToDo`s.
	existing_open_todos = frappe.db.sql(
		f"""
		select name, reference_name, allocated_to
		from `tabToDo`
		where reference_type = 'CRM Lead'
		  and status = 'Open'
		  and reference_name in ({ph_leads})
		""",
		tuple(assigned_leads),
		as_dict=True,
	)
	stale_todo_names: list[str] = []
	existing_owner_todos: set[tuple[str, str]] = set()
	for td in existing_open_todos:
		ld_key = str(td.reference_name)
		if td.allocated_to == expected_owner_by_lead.get(ld_key):
			existing_owner_todos.add((ld_key, str(td.allocated_to)))
		else:
			stale_todo_names.append(str(td.name))
	if stale_todo_names:
		ph_stale_td = ", ".join(["%s"] * len(stale_todo_names))
		frappe.db.sql(
			f"update `tabToDo` set status = 'Cancelled' where name in ({ph_stale_td})",
			tuple(stale_todo_names),
		)

	missing_todos = [
		(wi_name, ld, ow) for wi_name, ld, ow in notifications_out if (ld, ow) not in existing_owner_todos
	]
	if missing_todos:
		todo_names = _unique_hashes(len(missing_todos), 10)
		todo_rows = [
			(
				todo_names[i],
				now_ts,
				now_ts,
				session_user,
				session_user,
				0,
				0,
				"Open",
				"Medium",
				today_str,
				ow,
				f"CSKH assignment ({wi_name})",
				"CRM Lead",
				ld,
				session_user,
			)
			for i, (wi_name, ld, ow) in enumerate(missing_todos)
		]
		frappe.db.bulk_insert("ToDo", fields=TODO_BULK_FIELDS, values=todo_rows)

	# Revoke stale `CRM Lead` + child `DocShare`s and bulk-insert missing owner `DocShare`s.
	existing_lead_shares = frappe.db.sql(
		f"""
		select name, share_name, user
		from `tabDocShare`
		where share_doctype = 'CRM Lead'
		  and share_name in ({ph_leads})
		""",
		tuple(assigned_leads),
		as_dict=True,
	)
	stale_share_names: list[str] = []
	existing_owner_shares: set[tuple[str, str]] = set()
	for ds in existing_lead_shares:
		ld_key = str(ds.share_name)
		if ds.user == expected_owner_by_lead.get(ld_key):
			existing_owner_shares.add((ld_key, str(ds.user)))
		else:
			stale_share_names.append(str(ds.name))
	if stale_share_names:
		ph_stale_ds = ", ".join(["%s"] * len(stale_share_names))
		frappe.db.sql(
			f"delete from `tabDocShare` where name in ({ph_stale_ds})",
			tuple(stale_share_names),
		)

	active_child_dts = {
		str(r[0])
		for r in frappe.db.sql(
			"select distinct share_doctype from `tabDocShare` "
			"where share_doctype in ('CRM Call Log', 'CRM Task', 'FCRM Note', 'File')"
		)
	}
	if active_child_dts:
		for child_dt, dt_col, name_col in ownership._CHILD_LEAD_SHARE_SPECS:
			if child_dt not in active_child_dts or not frappe.db.table_exists(child_dt):
				continue
			child_rows = frappe.db.sql(
				f"select name, `{name_col}` as lead_name from `tab{child_dt}` where `{dt_col}` = 'CRM Lead' and `{name_col}` in ({ph_leads})",
				tuple(assigned_leads),
				as_dict=True,
			)
			if not child_rows:
				continue
			lead_by_child = {str(r.name): str(r.lead_name) for r in child_rows}
			ph_child = ", ".join(["%s"] * len(lead_by_child))
			stale_child_ds = [
				str(ds.name)
				for ds in frappe.db.sql(
					f"select name, share_name, user from `tabDocShare` where share_doctype = %s and share_name in ({ph_child})",
					(child_dt, *lead_by_child.keys()),
					as_dict=True,
				)
				if ds.user != expected_owner_by_lead.get(lead_by_child.get(str(ds.share_name), ""))
			]
			if stale_child_ds:
				ph_sc = ", ".join(["%s"] * len(stale_child_ds))
				frappe.db.sql(
					f"delete from `tabDocShare` where name in ({ph_sc})",
					tuple(stale_child_ds),
				)

	missing_shares = [(ld, ow) for ld, ow in planned_pairs if (ld, ow) not in existing_owner_shares]
	if missing_shares:
		ds_names = _unique_hashes(len(missing_shares), 10)
		ds_rows = [
			(
				ds_names[i],
				now_ts,
				now_ts,
				session_user,
				session_user,
				0,
				0,
				ow,
				"CRM Lead",
				ld,
				1,
				1,
				0,
				0,
				0,
				0,
			)
			for i, (ld, ow) in enumerate(missing_shares)
		]
		frappe.db.bulk_insert("DocShare", fields=DOCSHARE_BULK_FIELDS, values=ds_rows)

	# Final batch ownership invariant assertion (`S14` parity with `ownership._assert_final_owner`).
	final_shares = frappe.db.sql(
		f"select share_name, user from `tabDocShare` where share_doctype = 'CRM Lead' and share_name in ({ph_leads})",
		tuple(assigned_leads),
		as_dict=True,
	)
	if any(ds.user != expected_owner_by_lead.get(str(ds.share_name)) for ds in final_shares):
		raise Conflict("Unexpected share holders remain after the ownership change.", code="CONFLICT")
	if {str(ds.share_name) for ds in final_shares} != set(assigned_leads):
		raise Conflict("Missing expected DocShare after batch ownership change.", code="CONFLICT")

	final_todos = frappe.db.sql(
		f"select reference_name, allocated_to from `tabToDo` where reference_type = 'CRM Lead' and status = 'Open' and reference_name in ({ph_leads})",
		tuple(assigned_leads),
		as_dict=True,
	)
	if any(td.allocated_to != expected_owner_by_lead.get(str(td.reference_name)) for td in final_todos):
		raise Conflict(
			"Unexpected open ToDo assignments remain after the ownership change.",
			code="CONFLICT",
		)
	if {str(td.reference_name) for td in final_todos} != set(assigned_leads):
		raise Conflict("Missing expected open ToDo after batch ownership change.", code="CONFLICT")

	final_lead_owners = {
		str(r.name): r.lead_owner
		for r in frappe.db.sql(
			f"select name, lead_owner from `tabCRM Lead` where name in ({ph_leads})",
			tuple(assigned_leads),
			as_dict=True,
		)
	}
	if any(final_lead_owners.get(ld) != ow for ld, ow in planned_pairs):
		raise Conflict("The lead owner does not match the intended owner.", code="CONFLICT")

	return highest_index, state, loads, notifications_out


def _pick_owner(rules, state, loads):
	"""Pick the next owner for one lead, updating the persisted fairness state.

	The allocator state is updated in place so the cumulative counters survive
	across batches (P1 7.3). Returning None means no assignee is eligible any
	more (all capacities are exhausted).

	`loads` is the **database truth** (the active assigned work items per
	user). It is re-read from the DB on every chunk and updated per row so a
	capacity consumed by a concurrent run is respected immediately (P1 §7.2).
	"""
	result = allocate(1, rules, state=state, loads=loads)
	if not result["assignments"]:
		return None, state, loads
	owner = result["assignments"][0]
	return owner, result["state"], result["state"].get("loads", loads)


def _create_work_item(campaign, lead, run):
	"""Create the work item and its active claim atomically.

	The caller already opened a savepoint: the whole work item + claim pair is
	rolled back together when the active claim of the lead already exists in
	another campaign (ADR-P1-02).
	"""
	work_item = frappe.get_doc(
		{
			"doctype": "CSKH Work Item",
			"campaign": campaign,
			"lead": lead,
			"status": "queued",
			"allocation_run": run.name,
		}
	)
	work_item.insert(ignore_permissions=True)

	claim = frappe.get_doc(
		{
			"doctype": "CSKH Active Claim",
			"lead": lead,
			"campaign": campaign,
			"work_item": work_item.name,
		}
	)
	try:
		claim.insert(ignore_permissions=True)
	except Exception as exc:
		# The lead is already claimed elsewhere: the DB unique index wins.
		raise Conflict(
			"The lead is already claimed by another campaign.",
			code="ALREADY_ASSIGNED",
		) from exc
	return work_item.name


_DEFAULT_CREATE_WORK_ITEM = _create_work_item


def _run_actual(run):
	actual = {}
	if run.actual_json:
		try:
			actual = json.loads(run.actual_json)
		except (ValueError, TypeError):
			actual = {}
	return actual if isinstance(actual, dict) else {}


def _run_skipped(run):
	return _run_actual(run).get("skipped", []) or []


def _run_response(run):
	actual = _run_actual(run)
	preview_payload = _run_preview_payload(run)
	total = int(actual.get("total", preview_payload.get("eligible_count", 0)) or 0)
	progress = int(run.progress or 0)
	return {
		"run_id": run.name,
		"campaign": run.campaign,
		"status": run.status,
		"rule_version": run.rule_version,
		"selection_fingerprint": run.selection_fingerprint,
		"total": total,
		"progress": progress,
		"assigned": actual.get("assigned", {}),
		"created": int(actual.get("created", 0) or 0),
		"errors": int(actual.get("errors", 0) or 0),
		"skipped": actual.get("skipped", []),
		"excluded_assignees": actual.get("excluded_assignees", preview_payload.get("excluded_assignees", [])),
		"error": run.error,
		"started_at": run.started_at,
		"finished_at": run.finished_at,
	}


def get_run(run_id, actor=None):
	"""Return the progress and result of a distribution run."""
	if not run_id or not frappe.db.exists("CSKH Distribution Run", run_id):
		raise NotFound("The distribution run does not exist.")
	run = frappe.get_doc("CSKH Distribution Run", run_id)
	return _run_response(run)


def reconcile(campaign, actor=None):
	"""Reconcile the persisted state with the database (P1 7.4, 12.5).

	The reconciliation compares **five independent numbers** (P1 §12.5):

	* the distinct leads (the customers),
	* the work items,
	* the active claims,
	* the current owners,
	* the assignment events.

	The events are read from `CSKH Assignment History`, whose assignee column
	is `to_user` — not `assigned_to`, which is the column of the work item
	(L-01). The events are filtered by the **campaign of the run** that
	produced them, so the history of the whole installation is never added to
	the reconciliation of one campaign.
	"""
	actor = actor or frappe.session.user
	loads = _campaign_loads(campaign)

	# The events of a campaign are the events of the **work items** of that
	# campaign — the events that carry its `distribution_run` **and** the
	# manual events of the same work items. The history of the whole
	# installation is never added to the reconciliation of one campaign
	# (L-01).
	counts = frappe.db.sql(
		"""select h.to_user as user, count(*) as total
		   from `tabCSKH Assignment History` h
		   inner join `tabCSKH Work Item` wi on wi.name = h.work_item
		   where wi.campaign = %s and h.action = 'assign'
		   group by h.to_user""",
		(campaign,),
		as_dict=True,
	)

	distinct_leads = frappe.db.sql(
		"select count(distinct lead) from `tabCSKH Work Item` where campaign = %s",
		(campaign,),
	)[0][0]

	active_work_items = frappe.db.count(
		"CSKH Work Item",
		{"campaign": campaign, "status": ["in", list(OWNED_ACTIVE_WORK_ITEM_STATES)]},
	)
	active_claims = frappe.db.count("CSKH Active Claim", {"campaign": campaign})

	stale_docshares = int(
		frappe.db.sql(
			"""select count(distinct ds.name)
			   from `tabDocShare` ds
			   inner join `tabCRM Lead` l on l.name = ds.share_name and ds.share_doctype = 'CRM Lead'
			   where l.name in (select distinct wi.lead from `tabCSKH Work Item` wi where wi.campaign = %s)
			     and coalesce(ds.user, '') != coalesce(l.lead_owner, '')""",
			(campaign,),
		)[0][0]
		or 0
	)
	stale_open_todos = int(
		frappe.db.sql(
			"""select count(distinct td.name)
			   from `tabToDo` td
			   inner join `tabCRM Lead` l on l.name = td.reference_name and td.reference_type = 'CRM Lead'
			   where td.status = 'Open'
			     and l.name in (select distinct wi.lead from `tabCSKH Work Item` wi where wi.campaign = %s)
			     and coalesce(td.allocated_to, '') != coalesce(l.lead_owner, '')""",
			(campaign,),
		)[0][0]
		or 0
	)
	owners_sum = sum(int(v) for v in loads.values())
	ownership_reconciled = bool(
		active_work_items == active_claims
		and active_work_items == owners_sum
		and stale_docshares == 0
		and stale_open_todos == 0
	)

	return {
		"campaign": campaign,
		"current_owners": loads,
		"assignment_events": {row.user: int(row.total) for row in counts if row.user},
		"distinct_leads": int(distinct_leads),
		"active_work_items": active_work_items,
		"active_claims": active_claims,
		"stale_docshares": stale_docshares,
		"stale_open_todos": stale_open_todos,
		"ownership_reconciled": ownership_reconciled,
		"as_of": now_datetime(),
	}
