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

from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.services import eligibility, ownership
from crm_cskh.services.errors import Conflict, Forbidden, InvalidRequest, NotFound, RuleChanged
from crm_cskh.services.weighted_allocator import allocate, planned_distribution

CHUNK_SIZE = 100
PREVIEW_TTL_MINUTES = 30
LEASE_SECONDS = 300


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


def run_distribution_worker(run_id, actor=None):
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
			if len(chunk_leads) > 20 and len(set(chunk_leads)) == len(chunk_leads):
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
					pending_notifications.append((work_item, owner))
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

			if pending_notifications:
				_deliver_notifications(run, pending_notifications, effective_actor)
	except Exception as exc:
		frappe.db.rollback()
		return _abort_run(run, effective_actor, "RETRYABLE_FAILURE", str(exc))

	# Finalize run status from DB truth.
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

	delivered = 0
	failed = 0
	for work_item, owner in pending:
		result = notifications.deliver_assignment(
			work_item,
			owner,
			event_id=f"assign:{work_item}:run:{run.name}",
			actor=actor,
		)
		if result.get("delivered"):
			delivered += 1
		else:
			failed += 1
	if delivered:
		frappe.db.commit()
	return {"delivered": delivered, "failed": failed}


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
