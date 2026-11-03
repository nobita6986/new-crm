"""Atomic ownership service: the only mutation gateway for P1 assignments.

P1 7.6 requires that assign / reassign / revoke all go through one service
that enforces permission, transaction, locking, idempotency and audit.

ADR-P1-05 fixes the order of operations inside a single transaction:

    lock work item -> check expected owner -> update work item ->
    close the old ownership interval and open a history event ->
    sync `CRM Lead.lead_owner` -> sync ToDo -> sync DocShare -> commit

Two upstream behaviours make the ToDo step dangerous and are handled here:

  * `crm/api/todo.py::after_insert` sets `CRM Lead.lead_owner` from
    `ToDo.allocated_to`;
  * `crm/api/todo.py::clear_owner_on_unassign` clears `lead_owner` when a ToDo
    is cancelled.

The owner is therefore re-asserted after the old ToDo is cancelled, and the
result is asserted before the transaction commits.

> **Implementation note (P1):** the service writes with `frappe.db.set_value`
> and creates the `ToDo` document directly instead of going through
> `frappe.desk.form.assign_to.add`. The upstream helper runs a full
> `check_permission()` on the parent document and `frappe.share.add_docshare`
> runs `check_share_permission()`; a `CSKH CRM Admin` legitimately has
> neither the generic `CRM Lead` write right nor the `share` right (ADR-005
> P0, ADR-P1-07 §3.1). The ownership service is the controlled path, so it
> performs the same operations itself inside its own transaction.
"""

import frappe
from frappe.utils import now_datetime

from crm_cskh.api._params import parse_bool, parse_optional_str
from crm_cskh.services import eligibility
from crm_cskh.services.errors import Conflict, Forbidden, InvalidRequest, NotFound

REASSIGN = "reassign"
REVOKE = "revoke"
ASSIGN = "assign"

ROLE_ADMIN = "CSKH CRM Admin"


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _has_role(user, *roles):
	return bool(set(roles) & set(frappe.get_roles(user)))


def _require_actor_permission(actor, action):
	"""Only the CRM Admin business role (or a system manager) may mutate ownership.

	ADR-P1-07: `CSKH CRM Admin` is a business role. It never implies
	`System Manager`, and it never grants access to the generic Frappe
	endpoints.
	"""
	if _is_system(actor):
		return
	if not _has_role(actor, ROLE_ADMIN):
		raise Forbidden("This action requires the CRM Admin business role.")


def _require_target_eligibility(target_user):
	target_user = parse_optional_str(target_user, "The target user must be a string.")
	if not target_user:
		raise InvalidRequest("A target user is required.")
	if not frappe.db.get_value("User", target_user, "enabled"):
		raise InvalidRequest("The target user is disabled.")
	if "Sales User" not in frappe.get_roles(target_user):
		raise InvalidRequest("The target user must be a Sales User.")
	return target_user


def _lock_work_item(work_item):
	"""Row-lock the work item so two mutators cannot interleave."""
	rows = frappe.db.sql(
		"select name, campaign, lead, status, assigned_to, assigned_at "
		"from `tabCSKH Work Item` where name = %s for update",
		(work_item,),
		as_dict=True,
	)
	if not rows:
		raise NotFound("The work item does not exist.")
	return rows[0]


def _capacity_usage(campaign, user):
	"""Number of active assigned work items of `user` inside `campaign`."""
	return frappe.db.count(
		"CSKH Work Item",
		{"campaign": campaign, "assigned_to": user, "status": "assigned"},
	)


def _capacity_limit(campaign, user):
	rules = frappe.db.get_all(
		"CSKH Assignment Rule",
		filters={"campaign": campaign, "user": user},
		fields=["capacity_limit", "enabled", "version"],
	)
	if not rules:
		return 0
	latest_version = max((int(row.version or 1) for row in rules), default=1)
	enabled_latest = [
		row for row in rules if int(row.version or 1) == latest_version and int(row.enabled or 0)
	]
	if not enabled_latest:
		return 0
	# 0 means unlimited (P1 5.2).
	return max(int(row.capacity_limit or 0) for row in enabled_latest)


def _require_campaign_target_eligibility(campaign, target_user):
	"""Enforce campaign status, `team_scope` subtree, latest rule state, and capacity (P1 7.1, 7.2, 7.6)."""
	target_user = _require_target_eligibility(target_user)
	campaign_doc = frappe.db.get_value(
		"CSKH Campaign",
		campaign,
		["name", "status", "team_scope"],
		as_dict=True,
	)
	if not campaign_doc:
		raise NotFound("The campaign does not exist.")
	if campaign_doc.status == "Closed":
		raise Conflict("The campaign is closed and cannot accept ownership changes.", code="CONFLICT")

	allowed_users, allowed_nodes = eligibility.campaign_team_scope_members(campaign_doc.team_scope)
	if allowed_users is not None and target_user not in allowed_users:
		raise InvalidRequest("The target user is outside the campaign team scope.")

	user_rules = frappe.db.get_all(
		"CSKH Assignment Rule",
		filters={"campaign": campaign, "user": target_user},
		fields=["name", "team", "weight", "capacity_limit", "enabled", "version"],
	)
	if user_rules:
		latest_version = max((int(row.version or 1) for row in user_rules), default=1)
		latest_rows = [row for row in user_rules if int(row.version or 1) == latest_version]
		enabled_latest = [row for row in latest_rows if int(row.enabled or 0)]
		if not enabled_latest:
			raise InvalidRequest("The target user's assignment rule is disabled in this campaign.")
		if max((int(row.weight or 0) for row in enabled_latest), default=0) <= 0:
			raise InvalidRequest("The target user has no positive assignment weight in this campaign.")
		if allowed_nodes is not None:
			for row in enabled_latest:
				if row.team and row.team not in allowed_nodes:
					raise InvalidRequest(
						"The target user's assignment rule is outside the campaign team scope."
					)
		limit = max((int(row.capacity_limit or 0) for row in enabled_latest), default=0)
		if limit > 0:
			used = _capacity_usage(campaign, target_user)
			if used + 1 > limit:
				raise Conflict(
					"The target user has no remaining capacity.",
					code="CAPACITY_EXCEEDED",
					details={"capacity_limit": limit, "used": used},
				)
	return target_user


def _original_distribution_run(work_item):
	"""Return the distribution run that produced the **original** assignment.

	A manual transfer has no run of its own. The audit trail must then keep
	the source of the *original* assignment, so the history never claims that
	a manual transfer came from a run it did not belong to (L-08).

	The source is read from the **first `assign` event** of the work item: the
	`CSKH Work Item.allocation_run` field is only written by the distribution
	engine, so a work item that was assigned through the ownership service
	would otherwise lose its source (L-08).
	"""
	row = frappe.db.get_value(
		"CSKH Assignment History",
		{"work_item": work_item, "action": ASSIGN},
		["distribution_run", "event_at", "creation"],
		order_by="event_at asc",
		as_dict=True,
	)
	if row and row.distribution_run:
		return row.distribution_run
	return frappe.db.get_value("CSKH Work Item", work_item, "allocation_run")


def _event_id(request_id, work_item, action):
	"""A deterministic, unique event id so a retry is idempotent."""
	return f"{action}:{work_item}:{request_id}"


def _find_history_event(event_id):
	return frappe.db.get_value("CSKH Assignment History", {"event_id": event_id}, "name")


def _close_open_interval(work_item, ended_at):
	"""Close the currently open ownership interval of a work item."""
	open_event = frappe.db.get_value(
		"CSKH Assignment History",
		{"work_item": work_item, "ownership_end": ["is", "not set"]},
		["name", "to_user", "ownership_start"],
		order_by="event_at desc",
		as_dict=True,
	)
	if not open_event:
		return None
	frappe.db.set_value(
		"CSKH Assignment History",
		open_event.name,
		{"ownership_end": ended_at},
		update_modified=False,
	)
	return open_event


def _insert_history(doc):
	history = frappe.get_doc(doc)
	history.insert(ignore_permissions=True)
	return history


def _sync_lead_owner(lead, owner):
	"""Set the CRM Lead owner inside the current transaction.

	`frappe.db.set_value` is used on purpose: the upstream `CRM Lead.validate`
	would call `assign_agent` / `share_with_agent`, creating a ToDo and a
	DocShare outside the controlled order of this service (ADR-P1-05 2.2).
	"""
	if not lead:
		return
	frappe.db.set_value("CRM Lead", lead, "lead_owner", owner, update_modified=False)


def _lead_docshares(lead):
	return frappe.db.get_all(
		"DocShare",
		filters={"share_doctype": "CRM Lead", "share_name": lead},
		fields=["name", "user"],
	)


def _sync_docshares(lead, keep_users):
	"""Grant the share to the current owner and revoke every other share.

	P0 already removes stale shares when the owner changes
	(`permissions.py::clear_stale_lead_shares`); the ownership service repeats
	the cleanup inside its own transaction so a revoked owner cannot keep
	reading the lead (T1-16, SEC-06).
	"""
	if not lead:
		return
	keep = set(keep_users or [])
	for share in _lead_docshares(lead):
		if share.user not in keep:
			frappe.db.delete("DocShare", {"name": share.name})
	for user in keep:
		if not frappe.db.exists("DocShare", {"user": user, "share_name": lead, "share_doctype": "CRM Lead"}):
			frappe.share.add_docshare(
				"CRM Lead",
				lead,
				user,
				read=1,
				write=1,
				submit=0,
				share=0,
				flags={"ignore_share_permission": True},
			)


def _todo_for(lead, user):
	return frappe.db.get_value(
		"ToDo",
		{"reference_type": "CRM Lead", "reference_name": lead, "allocated_to": user, "status": "Open"},
		"name",
	)


def _create_todo(lead, user, description):
	"""Create the assignment ToDo of the new owner.

	The `ToDo` is created directly (not through `frappe.desk.form.assign_to`)
	because the upstream helper requires a generic `CRM Lead` permission that
	the CRM Admin business role deliberately does not have. The upstream
	`crm.api.todo.after_insert` hook still runs and mirrors `allocated_to` into
	`lead_owner`; the caller re-asserts the owner afterwards.
	"""
	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": user,
			"reference_type": "CRM Lead",
			"reference_name": lead,
			"description": description,
			"priority": "Medium",
			"status": "Open",
			"assigned_by": frappe.session.user,
		}
	)
	todo.insert(ignore_permissions=True)
	return todo.name


def _cancel_todo(todo_name):
	if not todo_name:
		return
	frappe.db.set_value("ToDo", todo_name, "status", "Cancelled", update_modified=False)


def _assert_final_owner(lead, expected_owner):
	"""Assert that only the intended owner still has access to the lead."""
	shares = [share.user for share in _lead_docshares(lead)]
	unexpected = [user for user in shares if user != expected_owner]
	if unexpected:
		raise Conflict(
			"Unexpected share holders remain after the ownership change.",
			code="CONFLICT",
			details={"unexpected": len(unexpected)},
		)
	actual_owner = frappe.db.get_value("CRM Lead", lead, "lead_owner")
	if actual_owner != expected_owner:
		raise Conflict(
			"The lead owner does not match the intended owner.",
			code="CONFLICT",
		)
	return {"owner": actual_owner, "shares": shares}


def assign(work_item, target_user, actor=None, reason=None, request_id=None, distribution_run=None):
	"""Assign a queued work item to `target_user`."""
	actor = actor or frappe.session.user
	_require_actor_permission(actor, ASSIGN)
	work_item = parse_optional_str(work_item, "The work item must be a string.")
	if not work_item:
		raise InvalidRequest("The work item is required.")
	request_id = parse_optional_str(request_id, "The request id must be a string.")
	if not request_id:
		raise InvalidRequest("A request id is required so the action stays idempotent.")
	reason = parse_optional_str(reason, "The reason must be a string.") or "Initial assignment"

	event_id = _event_id(request_id, work_item, ASSIGN)
	existing = _find_history_event(event_id)
	if existing:
		return {"work_item": work_item, "status": "already_applied", "event": existing}

	item = _lock_work_item(work_item)
	if item.status != "queued":
		raise Conflict(
			"Only a queued work item can be assigned.",
			code="ALREADY_ASSIGNED",
			details={"status": item.status},
		)
	claim = frappe.db.get_value(
		"CSKH Active Claim",
		{"lead": item.lead},
		["work_item", "campaign"],
		as_dict=True,
	)
	if claim and (claim.work_item != work_item or claim.campaign != item.campaign):
		raise Conflict(
			"The customer already has an active claim.",
			code="ALREADY_ASSIGNED",
			details={"campaign": claim.campaign, "work_item": claim.work_item},
		)
	other_assigned = frappe.db.get_value(
		"CSKH Work Item",
		{"lead": item.lead, "status": "assigned", "name": ["!=", work_item]},
		["name", "campaign"],
		as_dict=True,
	)
	if other_assigned:
		raise Conflict(
			"The customer is already assigned in another work item.",
			code="ALREADY_ASSIGNED",
			details={"campaign": other_assigned.campaign, "work_item": other_assigned.name},
		)
	target_user = _require_campaign_target_eligibility(item.campaign, target_user)

	now = now_datetime()
	frappe.db.set_value(
		"CSKH Work Item",
		work_item,
		{"status": "assigned", "assigned_to": target_user, "assigned_at": now},
		update_modified=False,
	)

	_insert_history(
		{
			"doctype": "CSKH Assignment History",
			"work_item": work_item,
			"action": ASSIGN,
			"from_user": None,
			"to_user": target_user,
			"actor": actor,
			"event_at": now,
			"reason": reason,
			"ownership_start": now,
			"ownership_end": None,
			"event_id": event_id,
			"distribution_run": distribution_run,
		}
	)

	_sync_lead_owner(item.lead, target_user)
	_sync_docshares(item.lead, [target_user])
	if not _todo_for(item.lead, target_user):
		_create_todo(item.lead, target_user, f"CSKH assignment ({work_item})")
	# The upstream ToDo hook mirrors the allocated user into `lead_owner`; the
	# value is re-asserted so the transaction always ends on the intended owner.
	_sync_lead_owner(item.lead, target_user)

	return {"work_item": work_item, "status": "assigned", "owner": target_user}


def reassign(
	work_item,
	expected_owner,
	target_user,
	actor=None,
	reason=None,
	request_id=None,
	distribution_run=None,
):
	"""Move the ownership of a work item from `expected_owner` to `target_user`.

	`distribution_run` records the run that caused the transfer. A **manual**
	reassign has no run: the event then keeps the source of the *original*
	assignment, so the audit never claims that a manual transfer came from a
	run it did not belong to (L-08).
	"""
	actor = actor or frappe.session.user
	_require_actor_permission(actor, REASSIGN)
	work_item = parse_optional_str(work_item, "The work item must be a string.")
	if not work_item:
		raise InvalidRequest("The work item is required.")
	if expected_owner is not None and not isinstance(expected_owner, str):
		raise InvalidRequest("The expected owner must be a string.")
	request_id = parse_optional_str(request_id, "The request id must be a string.")
	if not request_id:
		raise InvalidRequest("A request id is required so the action stays idempotent.")
	reason = parse_optional_str(reason, "The reason must be a string.")
	if not reason:
		raise InvalidRequest("A reason is required to transfer a customer.")
	target_user = _require_target_eligibility(target_user)

	event_id = _event_id(request_id, work_item, REASSIGN)
	existing = _find_history_event(event_id)
	if existing:
		return {"work_item": work_item, "status": "already_applied", "event": existing}

	item = _lock_work_item(work_item)
	if item.status != "assigned" or not item.assigned_to:
		raise Conflict(
			"The work item is not currently assigned.",
			code="ALREADY_ASSIGNED",
			details={"status": item.status},
		)
	if expected_owner is not None and item.assigned_to != expected_owner:
		raise Conflict(
			"The current owner is not the expected owner.",
			code="CONFLICT",
			details={"expected_owner": expected_owner, "current_owner": item.assigned_to},
		)
	if item.assigned_to == target_user:
		raise Conflict(
			"The work item already belongs to the target user.",
			code="ALREADY_ASSIGNED",
		)

	target_user = _require_campaign_target_eligibility(item.campaign, target_user)

	now = now_datetime()
	old_owner = item.assigned_to
	_close_open_interval(work_item, now)

	frappe.db.set_value(
		"CSKH Work Item",
		work_item,
		{"status": "assigned", "assigned_to": target_user, "assigned_at": now, "last_transfer_at": now},
		update_modified=False,
	)

	_insert_history(
		{
			"doctype": "CSKH Assignment History",
			"work_item": work_item,
			"action": REASSIGN,
			"from_user": old_owner,
			"to_user": target_user,
			"actor": actor,
			"event_at": now,
			"reason": reason,
			"ownership_start": now,
			"ownership_end": None,
			"event_id": event_id,
			# A manual reassign keeps the source of the **original**
			# assignment, so the audit never claims that a manual transfer
			# came from a run it did not belong to (L-08).
			"distribution_run": distribution_run or _original_distribution_run(work_item),
		}
	)

	_sync_lead_owner(item.lead, target_user)
	# Revoke the previous owner: their ToDo is cancelled and their DocShare is
	# removed, so the stale grant cannot outlive the access.
	old_todo = _todo_for(item.lead, old_owner)
	_cancel_todo(old_todo)
	_sync_docshares(item.lead, [target_user])
	if not _todo_for(item.lead, target_user):
		_create_todo(item.lead, target_user, f"CSKH assignment ({work_item})")
	# Cancelling the old ToDo clears `lead_owner` upstream; re-assert it.
	_sync_lead_owner(item.lead, target_user)

	ledger = _assert_final_owner(item.lead, target_user)
	return {"work_item": work_item, "status": "reassigned", "owner": target_user, "ledger": ledger}


def revoke(
	work_item,
	expected_owner,
	actor=None,
	reason=None,
	request_id=None,
	return_to_pool=True,
	distribution_run=None,
):
	"""Revoke the ownership of a work item and return it to the pool.

	A **manual** revoke has no distribution run: the event keeps the source of
	the *original* assignment, so the audit trail stays truthful (L-08).
	"""
	actor = actor or frappe.session.user
	_require_actor_permission(actor, REVOKE)
	if not request_id:
		raise InvalidRequest("A request id is required so the action stays idempotent.")
	if not reason:
		raise InvalidRequest("A reason is required to revoke a customer.")
	return_to_pool = parse_bool(return_to_pool, default=True, field="return_to_pool")

	event_id = _event_id(request_id, work_item, REVOKE)
	existing = _find_history_event(event_id)
	if existing:
		return {"work_item": work_item, "status": "already_applied", "event": existing}

	item = _lock_work_item(work_item)
	if item.status != "assigned" or not item.assigned_to:
		raise Conflict(
			"The work item is not currently assigned.",
			code="ALREADY_ASSIGNED",
			details={"status": item.status},
		)
	if expected_owner is not None and item.assigned_to != expected_owner:
		raise Conflict(
			"The current owner is not the expected owner.",
			code="CONFLICT",
			details={"expected_owner": expected_owner, "current_owner": item.assigned_to},
		)

	now = now_datetime()
	old_owner = item.assigned_to
	_close_open_interval(work_item, now)

	new_status = "queued" if return_to_pool else "revoked"
	frappe.db.set_value(
		"CSKH Work Item",
		work_item,
		{"status": new_status, "assigned_to": None, "assigned_at": None, "last_transfer_at": now},
		update_modified=False,
	)

	_insert_history(
		{
			"doctype": "CSKH Assignment History",
			"work_item": work_item,
			"action": REVOKE,
			"from_user": old_owner,
			"to_user": None,
			"actor": actor,
			"event_at": now,
			"reason": reason,
			"ownership_start": None,
			"ownership_end": now,
			"event_id": event_id,
			# A manual revoke keeps the source of the **original**
			# assignment, so the audit stays truthful (L-08).
			"distribution_run": distribution_run or _original_distribution_run(work_item),
		}
	)

	_sync_lead_owner(item.lead, None)
	old_todo = _todo_for(item.lead, old_owner)
	_cancel_todo(old_todo)
	_sync_docshares(item.lead, [])
	_sync_lead_owner(item.lead, None)

	# The active claim is released together with the ownership: the lead is
	# back in the pool and may join another campaign (ADR-P1-02 §2.2).
	frappe.db.delete("CSKH Active Claim", {"lead": item.lead})

	return {"work_item": work_item, "status": new_status, "owner": None}
