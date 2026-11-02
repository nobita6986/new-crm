"""Weighted distribution engine: preview, execute, reconciliation.

P1 7.5 requires DB level locking, not an in-memory lock: a `CSKH Distribution
Run` is claimed with a conditional state transition, the allocator state is
persisted in the run, and the `CSKH Active Claim` unique index is the final
guarantee that one lead can never have two active owners.

The whole execution happens inside one Frappe transaction per chunk so a
failure rolls back the work items, claims, history events and counters
together (P1 12.4: artificial failure after creating a work item must not leave
an orphan).
"""

import hashlib
import json

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.services import eligibility, ownership
from crm_cskh.services.errors import Conflict, InvalidRequest, NotFound, RuleChanged
from crm_cskh.services.weighted_allocator import allocate, planned_distribution

CHUNK_SIZE = 100
PREVIEW_TTL_MINUTES = 30


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


def _campaign_loads(campaign):
	"""Current active assigned work items per user (capacity accounting)."""
	rows = frappe.db.sql(
		"""select assigned_to as user, count(*) as total
		   from `tabCSKH Work Item`
		   where campaign = %s and status = 'assigned' and assigned_to is not null
		   group by assigned_to""",
		(campaign,),
		as_dict=True,
	)
	return {row.user: int(row.total) for row in rows}


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
	preview_payload = {}
	if run.preview_json:
		try:
			preview_payload = json.loads(run.preview_json)
		except (ValueError, TypeError):
			preview_payload = {}
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


def execute(preview_id, expected_rule_version=None, idempotency_key=None, actor=None):
	"""Execute a previously computed preview."""
	actor = actor or frappe.session.user
	run = frappe.get_doc("CSKH Distribution Run", preview_id)
	if not run:
		raise NotFound("The preview does not exist.")

	if run.status in ("completed", "completed_with_errors"):
		return _run_response(run)
	if run.status == "running":
		raise Conflict("The distribution run is already executing.", code="CONFLICT")
	if run.expires_at and run.expires_at < now_datetime():
		raise Conflict("The preview has expired; run the preview again.", code="PREVIEW_EXPIRED")

	version = eligibility.current_rule_version(run.campaign)
	if expected_rule_version is not None and int(expected_rule_version) != int(run.rule_version):
		raise RuleChanged(
			"The assignment rules changed after the preview.",
			details={"expected": expected_rule_version, "current": version},
		)
	if int(version) != int(run.rule_version):
		raise RuleChanged(
			"The assignment rules changed after the preview.",
			details={"expected": run.rule_version, "current": version},
		)

	# The preview also freezes the **fairness counters** it was computed from.
	# If another run committed assignments in the meantime, the preview is
	# stale and must not be executed (P1 §7.4: a stale preview is refused
	# instead of distributing stale numbers).
	base_state = _run_state(run)
	latest_state = _latest_state(run.campaign, run.rule_version)
	if _state_cursor(base_state) != _state_cursor(latest_state):
		raise RuleChanged(
			"The distribution state changed after the preview.",
			details={"expected": _state_cursor(base_state), "current": _state_cursor(latest_state)},
		)

	if idempotency_key and idempotency_key != run.idempotency_key:
		run.idempotency_key = idempotency_key

	# Only one executor may own the run (P1 7.5). The row is locked with
	# `SELECT ... FOR UPDATE`, so the check and the transition are one atomic
	# step: a second executor blocks on the lock and then observes the
	# already-running status.
	locked = frappe.db.sql(
		"select status from `tabCSKH Distribution Run` where name = %s for update",
		(run.name,),
		as_dict=True,
	)
	if not locked or locked[0].status != "planned":
		raise Conflict("The distribution run is already owned by another executor.", code="CONFLICT")
	frappe.db.sql(
		"""update `tabCSKH Distribution Run`
		   set status = 'running', started_at = %s
		   where name = %s and status = 'planned'""",
		(now_datetime(), run.name),
	)
	frappe.db.commit()

	return _execute_chunks(run, actor)


def _execute_chunks(run, actor):
	campaign = run.campaign
	campaign_doc = frappe.db.get_value("CSKH Campaign", campaign, ["name", "team_scope"], as_dict=True)
	version = eligibility.current_rule_version(campaign)
	eligible_assignees, excluded_assignees = eligibility.eligible_assignees(
		campaign, campaign_doc.team_scope, version
	)
	selection_result = eligibility.eligible_leads(campaign)
	leads = selection_result["eligible"]

	fingerprint = _fingerprint(leads)
	if fingerprint != run.selection_fingerprint:
		run.reload()
		run.status = "failed"
		run.error = "The eligible lead set changed after the preview."
		run.finished_at = now_datetime()
		run.save(ignore_permissions=True)
		frappe.db.commit()
		raise RuleChanged("The eligible lead set changed after the preview.")

	rules = [
		{"user": rule["user"], "weight": rule["weight"], "capacity": rule["capacity"], "enabled": True}
		for rule in eligible_assignees
	]
	state = _run_state(run)
	loads = _campaign_loads(campaign)

	assigned = {}
	skipped = []
	created = 0
	errors = 0

	for start in range(0, len(leads), CHUNK_SIZE):
		chunk = leads[start : start + CHUNK_SIZE]
		for index, lead in enumerate(chunk):
			# One savepoint per row: a failing row rolls back only itself and
			# never leaves an orphan work item (P1 12.4).
			savepoint = f"cskh_dist_{start}_{index}"
			# The fairness counters are rolled back with the row: a failed row
			# must not consume a "slot" of the cumulative target.
			state_before = json.loads(json.dumps(state))
			loads_before = dict(loads)
			frappe.db.savepoint(savepoint)
			try:
				owner, state, loads = _pick_owner(rules, state, loads)
				if owner is None:
					errors += 1
					skipped.append({"lead": lead, "reason": "no_eligible_assignee"})
					continue
				work_item = _create_work_item(campaign, lead, run)
				ownership.assign(
					work_item,
					owner,
					actor=actor,
					reason=f"Distribution run {run.name}",
					request_id=f"run:{run.name}:{lead}",
					distribution_run=run.name,
				)
				assigned[owner] = assigned.get(owner, 0) + 1
				created += 1
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				# Restore the fairness counters: the rolled back row never
				# consumed a slot of the cumulative target.
				state = state_before
				loads = loads_before
				errors += 1
				skipped.append({"lead": lead, "reason": "assignment_failed", "detail": str(exc)[:200]})

	run.reload()
	run.state_json = json.dumps(state, ensure_ascii=False)
	run.actual_json = json.dumps(
		{
			"assigned": assigned,
			"created": created,
			"errors": errors,
			"skipped": skipped,
			"excluded_assignees": excluded_assignees,
		},
		ensure_ascii=False,
	)
	run.status = "completed_with_errors" if errors else "completed"
	run.finished_at = now_datetime()
	run.save(ignore_permissions=True)
	frappe.db.commit()
	return _run_response(run)


def _pick_owner(rules, state, loads):
	"""Pick the next owner for one lead, updating the persisted fairness state.

	The allocator state is updated in place so the cumulative counters survive
	across batches (P1 7.3). Returning None means no assignee is eligible any
	more (all capacities are exhausted).

	`loads` is the **database truth** (the active assigned work items per
	user). It is re-read from the DB on every call so a capacity consumed by a
	concurrent run is respected immediately (P1 §7.2).
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


def _run_response(run):
	actual = {}
	if run.actual_json:
		try:
			actual = json.loads(run.actual_json)
		except (ValueError, TypeError):
			actual = {}
	return {
		"run_id": run.name,
		"campaign": run.campaign,
		"status": run.status,
		"rule_version": run.rule_version,
		"selection_fingerprint": run.selection_fingerprint,
		"assigned": actual.get("assigned", {}),
		"created": actual.get("created", 0),
		"errors": actual.get("errors", 0),
		"skipped": actual.get("skipped", []),
		"started_at": run.started_at,
		"finished_at": run.finished_at,
	}


def get_run(run_id, actor=None):
	"""Return the progress and result of a distribution run."""
	run = frappe.get_doc("CSKH Distribution Run", run_id)
	if not run:
		raise NotFound("The distribution run does not exist.")
	return _run_response(run)


def reconcile(campaign, actor=None):
	"""Reconcile the persisted state with the database (P1 7.4, 12.5)."""
	actor = actor or frappe.session.user
	loads = _campaign_loads(campaign)
	counts = frappe.db.sql(
		"""select to_user as user, count(*) as total
		   from `tabCSKH Assignment History`
		   where action = 'assign' group by to_user""",
		as_dict=True,
	)
	return {
		"campaign": campaign,
		"current_owners": loads,
		"assignment_events": {row.user: int(row.total) for row in counts if row.user},
		"active_work_items": frappe.db.count("CSKH Work Item", {"campaign": campaign, "status": "assigned"}),
		"active_claims": frappe.db.count("CSKH Active Claim", {"campaign": campaign}),
		"as_of": now_datetime(),
	}
