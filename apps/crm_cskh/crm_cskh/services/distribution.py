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

	if run.status == "completed":
		return _run_response(run)
	if run.status == "completed_with_errors":
		# A run that committed some rows and failed others is **resumable**
		# (P1 §12.4: the retry must not create a second assignment). The
		# resume only processes the rows that are still unassigned.
		return _resume_run(run, actor, expected_rule_version=expected_rule_version)
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
	# The (work item, owner) pairs whose notification is delivered **after**
	# the commit. The list is collected during the loop so the post-commit
	# phase never has to re-read the work items (no N+1 query at 10k scale).
	pending_notifications = []

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
				pending_notifications.append((work_item, owner))
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

	# The notifications are delivered **after** the commit (P1 §7.5): the
	# assignment is the durable fact, the notification is a best-effort side
	# effect. A delivery failure never undoes the assignment and never
	# produces a second assignment event.
	_deliver_notifications(run, pending_notifications, actor)

	return _run_response(run)


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


def _resume_run(run, actor, expected_rule_version=None):
	"""Resume a run that committed some rows and failed others (P1 §12.4).

	A run whose status is `completed_with_errors` holds **committed**
	assignments and a list of rows that failed. The retry must:

	* **not** create a second work item, claim, history event or ToDo for the
	  rows that were already assigned — the `request_id` of the ownership
	  service and the `CSKH Active Claim` unique index are the arbiters;
	* **retry** the rows that failed transiently (`assignment_failed`), so a
	  transient failure does not lose a customer;
	* keep the fairness counters of the committed rows, so the resumed run
	  continues the same 50/30/20 ratio instead of restarting it.

	The resume works on the **unfiltered** selection: a lead that was already
	assigned by the first attempt is excluded from the *new* work, but it must
	still be visible so the resume can recognise it.
	"""
	campaign = run.campaign
	version = eligibility.current_rule_version(campaign)
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

	locked = frappe.db.sql(
		"select status from `tabCSKH Distribution Run` where name = %s for update",
		(run.name,),
		as_dict=True,
	)
	if not locked:
		raise NotFound("The distribution run does not exist.")
	if locked[0].status == "completed":
		run.reload()
		return _run_response(run)
	if locked[0].status != "completed_with_errors":
		raise Conflict("The distribution run is already owned by another executor.", code="CONFLICT")

	run.reload()
	campaign_doc = frappe.db.get_value("CSKH Campaign", campaign, ["name", "team_scope"], as_dict=True)
	eligible_assignees, excluded_assignees = eligibility.eligible_assignees(
		campaign, campaign_doc.team_scope, version
	)
	selection = eligibility.selection_leads(campaign)

	# The rows that are already assigned by this run: they must not be
	# assigned a second time.
	assigned_leads = {
		row.lead
		for row in frappe.db.get_all(
			"CSKH Work Item",
			filters={"campaign": campaign, "allocation_run": run.name},
			fields=["lead"],
		)
	}

	# The rows that failed transiently and must be retried. A row that failed
	# for a permanent reason (no eligible assignee, or claimed elsewhere) stays
	# recorded without mutating the run into a stuck `running` state.
	prior_skipped = _run_skipped(run)
	permanently_skipped = [entry for entry in prior_skipped if entry.get("reason") != "assignment_failed"]
	retryable = {
		entry.get("lead")
		for entry in prior_skipped
		if entry.get("reason") == "assignment_failed"
		and entry.get("lead")
		and entry.get("lead") not in assigned_leads
	}
	pending = [lead for lead in selection if lead in retryable]
	if not pending:
		return _run_response(run)

	# The run re-enters the **planned** state before it is executed again:
	# the status machine of P1 only allows `completed_with_errors -> planned`,
	# so the resume is an explicit, visible re-execution of the same run.
	run.status = "planned"
	run.save(ignore_permissions=True)
	run.status = "running"
	run.started_at = now_datetime()
	run.save(ignore_permissions=True)
	frappe.db.commit()

	rules = [
		{"user": rule["user"], "weight": rule["weight"], "capacity": rule["capacity"], "enabled": True}
		for rule in eligible_assignees
	]
	# The fairness counters of the committed rows are the base of the resume:
	# the resumed run continues the same ratio (ADR-P1-04 §2.4).
	state = _run_state(run)
	loads = _campaign_loads(campaign)

	assigned = {}
	skipped = []
	created = 0
	pending_notifications = []

	for start in range(0, len(pending), CHUNK_SIZE):
		chunk = pending[start : start + CHUNK_SIZE]
		for index, lead in enumerate(chunk):
			savepoint = f"cskh_resume_{run.name}_{start}_{index}"
			state_before = json.loads(json.dumps(state))
			loads_before = dict(loads)
			frappe.db.savepoint(savepoint)
			try:
				owner, state, loads = _pick_owner(rules, state, loads)
				if owner is None:
					skipped.append({"lead": lead, "reason": "no_eligible_assignee"})
					continue
				work_item = _create_work_item(campaign, lead, run)
				ownership.assign(
					work_item,
					owner,
					actor=actor,
					reason=f"Distribution run {run.name} (resume)",
					request_id=f"run:{run.name}:{lead}",
					distribution_run=run.name,
				)
				assigned[owner] = assigned.get(owner, 0) + 1
				pending_notifications.append((work_item, owner))
				created += 1
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				state = state_before
				loads = loads_before
				skipped.append({"lead": lead, "reason": "assignment_failed", "detail": str(exc)[:200]})

	run.reload()
	previous = _run_actual(run)
	merged_assigned = {user: int(count) for user, count in (previous.get("assigned") or {}).items()}
	for user, count in assigned.items():
		merged_assigned[user] = merged_assigned.get(user, 0) + int(count)
	all_skipped = permanently_skipped + skipped
	total_errors = len(all_skipped)

	run.state_json = json.dumps(state, ensure_ascii=False)
	run.actual_json = json.dumps(
		{
			"assigned": merged_assigned,
			"created": int(previous.get("created", 0)) + created,
			"errors": total_errors,
			"skipped": all_skipped,
			"excluded_assignees": excluded_assignees,
		},
		ensure_ascii=False,
	)
	run.status = "completed_with_errors" if total_errors else "completed"
	run.finished_at = now_datetime()
	run.save(ignore_permissions=True)
	frappe.db.commit()

	_deliver_notifications(run, pending_notifications, actor)

	return _run_response(run)


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

	return {
		"campaign": campaign,
		"current_owners": loads,
		"assignment_events": {row.user: int(row.total) for row in counts if row.user},
		"distinct_leads": int(distinct_leads),
		"active_work_items": frappe.db.count("CSKH Work Item", {"campaign": campaign, "status": "assigned"}),
		"active_claims": frappe.db.count("CSKH Active Claim", {"campaign": campaign}),
		"as_of": now_datetime(),
	}
