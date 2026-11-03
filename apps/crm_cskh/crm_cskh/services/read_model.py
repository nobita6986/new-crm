"""Effective read-model projection, workflow queries, progress summary & DB reconciliation.

Implements `docs/data-model-p2.md`, `docs/attempt-immutability-correction.md`,
`docs/api-contract-p2.md` (§4.2, §4.3, §4.4, §4.13), and `docs/race-retry-reconciliation.md`.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime

from crm_cskh.api._params import parse_bool, parse_optional_str, parse_pagination
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p2_models import ALL_WORK_ITEM_STATES, RESULT_GROUPS, VALID_CHANNELS
from crm_cskh.p2_permissions import (
	ROLE_ADMIN,
	_in_team,
	_is_leader,
	_is_staff,
	_is_system,
	_roles,
	assert_work_item_workflow_access,
	assert_workflow_actor,
	leader_authorized_work_item_sql,
	staff_authorized_work_item_sql,
)
from crm_cskh.services import reporting
from crm_cskh.services.errors import InvalidRequest
from crm_cskh.services.followup import get_open_followup_task, serialize_followup_task
from crm_cskh.services.policies import resolve_effective_policy


def _parse_json_dict(raw: str | None) -> dict:
	if not raw:
		return {}
	try:
		parsed = json.loads(raw)
	except (TypeError, ValueError):
		return {}
	return parsed if isinstance(parsed, dict) else {}


_ATTEMPT_PROJECTION_FIELDS = [
	"name",
	"work_item",
	"lead",
	"campaign",
	"ownership_event",
	"owner_at_attempt",
	"performed_by",
	"occurred_at",
	"recorded_at",
	"channel",
	"outcome_code",
	"outcome_label_snapshot",
	"outcome_group_snapshot",
	"outcome_version_snapshot",
	"contact_attempt_snapshot",
	"connected_snapshot",
	"interested_snapshot",
	"terminal_snapshot",
	"dnc_snapshot",
	"note",
	"call_log",
	"callback_task",
	"resolved_callback_task",
	"verification_status",
	"policy_name_snapshot",
	"policy_version_snapshot",
	"creation",
]

_CORRECTION_PROJECTION_FIELDS = [
	"name",
	"work_item",
	"attempt",
	"action",
	"status",
	"requested_by",
	"requested_at",
	"reason",
	"original_snapshot_json",
	"corrected_fields_json",
	"reviewed_by",
	"reviewed_at",
	"review_note",
	"creation",
]


def _project_attempts_rows(attempts: list, corrections: list) -> list[dict]:
	if not attempts:
		return []
	by_attempt: dict[str, list[dict]] = {}
	for corr in corrections:
		by_attempt.setdefault(corr.attempt, []).append(corr)

	projected: list[dict] = []
	for att in attempts:
		original = {
			"occurred_at": att.occurred_at,
			"channel": att.channel,
			"outcome_code": att.outcome_code,
			"outcome_label": att.outcome_label_snapshot,
			"outcome_group": att.outcome_group_snapshot,
			"outcome_version": cint(att.outcome_version_snapshot or 1),
			"is_contact_attempt": bool(cint(att.contact_attempt_snapshot)),
			"is_connected": bool(cint(att.connected_snapshot)),
			"is_interested": bool(cint(att.interested_snapshot)),
			"is_terminal": bool(cint(att.terminal_snapshot)),
			"is_do_not_contact": bool(cint(att.dnc_snapshot)),
			"note": att.note or "",
			"call_log": att.call_log,
		}
		effective = dict(original)
		is_voided = False
		has_approved_correction = False
		pending_count = 0

		att_corrs = by_attempt.get(att.name, [])
		# Apply approved overlays in approval chronological order
		approved_corrs = sorted(
			(c for c in att_corrs if c.status == "approved"),
			key=lambda c: (str(c.reviewed_at or c.creation), c.name),
		)
		for c in att_corrs:
			if c.status == "pending":
				pending_count += 1

		for corr in approved_corrs:
			has_approved_correction = True
			if corr.action == "void":
				is_voided = True
			elif corr.action == "restore":
				is_voided = False
			elif corr.action == "correct":
				patch = _parse_json_dict(corr.corrected_fields_json)
				if patch.get("occurred_at"):
					effective["occurred_at"] = get_datetime(patch["occurred_at"])
				if patch.get("channel"):
					effective["channel"] = patch["channel"]
				if "note" in patch and patch["note"] is not None:
					effective["note"] = patch["note"]
				if "call_log" in patch:
					effective["call_log"] = patch["call_log"] or None
				if patch.get("outcome_code"):
					effective["outcome_code"] = patch["outcome_code"]
					if "outcome_label" in patch:
						effective["outcome_label"] = patch["outcome_label"]
					if "outcome_group" in patch:
						effective["outcome_group"] = patch["outcome_group"]
					if "outcome_version" in patch:
						effective["outcome_version"] = cint(patch["outcome_version"] or 1)
					for b_key in (
						"is_contact_attempt",
						"is_connected",
						"is_interested",
						"is_terminal",
						"is_do_not_contact",
					):
						if b_key in patch:
							effective[b_key] = bool(cint(patch[b_key]))

		serialized_corrs = [
			{
				"correction_id": c.name,
				"action": c.action,
				"status": c.status,
				"requested_by": c.requested_by,
				"requested_at": c.requested_at,
				"reason": c.reason,
				"corrected_fields": _parse_json_dict(c.corrected_fields_json),
				"reviewed_by": c.reviewed_by,
				"reviewed_at": c.reviewed_at,
				"review_note": c.review_note,
			}
			for c in att_corrs
		]

		projected.append(
			{
				"attempt_id": att.name,
				"work_item": att.work_item,
				"lead": att.lead,
				"campaign": att.campaign,
				"ownership_event": att.ownership_event,
				"owner_at_attempt": att.owner_at_attempt,
				"performed_by": att.performed_by,
				"recorded_at": att.recorded_at,
				"verification_status": att.verification_status,
				"is_voided": is_voided,
				"has_approved_correction": has_approved_correction,
				"pending_correction_count": pending_count,
				"effective": effective,
				"original": original,
				"policy_snapshot": {
					"name": att.policy_name_snapshot,
					"version": cint(att.policy_version_snapshot or 1),
				},
				"callback_task": att.callback_task,
				"resolved_callback_task": att.resolved_callback_task,
				"corrections": serialized_corrs,
			}
		)

	projected.sort(
		key=lambda item: (
			get_datetime(item["effective"]["occurred_at"]),
			get_datetime(item["recorded_at"]),
			item["attempt_id"],
		)
	)
	return projected


def compute_effective_attempts(work_item: str) -> list[dict]:
	"""Load raw `CSKH Contact Attempt`s of `work_item` and overlay `approved` corrections."""
	attempts = frappe.db.get_all(
		"CSKH Contact Attempt",
		filters={"work_item": work_item},
		fields=_ATTEMPT_PROJECTION_FIELDS,
		order_by="occurred_at asc, creation asc, name asc",
	)
	if not attempts:
		return []
	corrections = frappe.db.get_all(
		"CSKH Attempt Correction",
		filters={"work_item": work_item},
		fields=_CORRECTION_PROJECTION_FIELDS,
		order_by="creation asc, name asc",
	)
	return _project_attempts_rows(attempts, corrections)


def summarize_effective_attempts(projected: list[dict]) -> dict:
	"""Aggregate effective read-model columns from `compute_effective_attempts` output."""
	active = [
		item
		for item in projected
		if not item["is_voided"] and bool(item["effective"].get("is_contact_attempt", True))
	]
	connected = [item for item in active if bool(item["effective"].get("is_connected"))]
	return {
		"attempt_count": len(active),
		"connected_attempt_count": len(connected),
		"first_attempt_at": active[0]["effective"]["occurred_at"] if active else None,
		"last_attempt_at": active[-1]["effective"]["occurred_at"] if active else None,
		"last_outcome_code": active[-1]["effective"]["outcome_code"] if active else None,
		"last_outcome_label": active[-1]["effective"]["outcome_label"] if active else None,
		"last_connected_at": connected[-1]["effective"]["occurred_at"] if connected else None,
	}


def recompute_work_item_read_model(
	work_item: str,
	*,
	bump_revision: bool = False,
	update_status_when_active: bool = False,
	extra_updates: dict | None = None,
) -> dict:
	"""Recompute and persist the materialized read-model columns on `CSKH Work Item` in-tx."""
	wi_row = frappe.db.get_value(
		"CSKH Work Item",
		work_item,
		["name", "status", "revision", "assigned_to"],
		as_dict=True,
	)
	if not wi_row:
		return {}

	projected = compute_effective_attempts(work_item)
	summary = summarize_effective_attempts(projected)
	open_task = get_open_followup_task(work_item)

	updates: dict = {
		**summary,
		"open_callback_task": str(open_task["name"]) if open_task else None,
		"next_action_due": open_task["due_date"] if open_task else None,
	}

	if update_status_when_active and wi_row.status in OWNED_ACTIVE_WORK_ITEM_STATES and wi_row.assigned_to:
		if open_task:
			updates["status"] = "on_hold"
		elif summary["attempt_count"] > 0:
			updates["status"] = "in_progress"
		else:
			updates["status"] = "assigned"

	if extra_updates:
		updates.update(extra_updates)

	if bump_revision and "revision" not in updates:
		updates["revision"] = cint(wi_row.revision or 1) + 1

	frappe.db.set_value("CSKH Work Item", work_item, updates, update_modified=True)
	return {
		"work_item": work_item,
		"status": updates.get("status", wi_row.status),
		"revision": cint(updates.get("revision", wi_row.revision or 1)),
		**summary,
		"open_callback_task": updates["open_callback_task"],
		"next_action_due": updates["next_action_due"],
	}


def _capabilities_for_work_item(wi_row: dict, policy: dict, actor: str) -> dict:
	roles = _roles(actor)
	is_admin = _is_system(actor) or ROLE_ADMIN in roles
	is_leader = _is_leader(actor)
	is_staff = _is_staff(actor)

	status = wi_row.get("status")
	assigned_to = wi_row.get("assigned_to")
	in_active_scope = False
	if status in OWNED_ACTIVE_WORK_ITEM_STATES and assigned_to:
		if is_admin:
			in_active_scope = True
		elif is_leader and (assigned_to == actor or _in_team(actor, assigned_to)):
			in_active_scope = True
		elif is_staff and assigned_to == actor:
			in_active_scope = True

	can_reopen = False
	if status == "closed":
		if is_admin:
			can_reopen = True
		elif is_leader and bool(policy.get("allow_leader_reopen")):
			can_reopen = True

	can_review_corr = bool(is_admin or (is_leader and bool(policy.get("allow_leader_review_correction"))))
	return {
		"can_record_attempt": in_active_scope,
		"can_schedule_followup": in_active_scope,
		"can_complete_followup": bool(in_active_scope and wi_row.get("open_callback_task")),
		"can_close": in_active_scope,
		"can_reopen": can_reopen,
		"can_request_correction": bool(in_active_scope or (status == "closed" and (is_admin or is_leader))),
		"can_review_correction": can_review_corr,
		"can_reassign_or_revoke": bool(is_admin and status in OWNED_ACTIVE_WORK_ITEM_STATES),
	}


def get_work_item_detail(work_item: str, actor: str | None = None) -> dict:
	"""Full workspace context for a single `CSKH Work Item` (`docs/api-contract-p2.md` §4.3)."""
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not wi_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	actor = assert_workflow_actor(actor, allow_bod=False)
	assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	wi = frappe.db.get_value(
		"CSKH Work Item",
		wi_val,
		[
			"name",
			"campaign",
			"lead",
			"status",
			"revision",
			"assigned_to",
			"assigned_at",
			"first_attempt_at",
			"last_attempt_at",
			"last_outcome_code",
			"last_outcome_label",
			"last_connected_at",
			"attempt_count",
			"connected_attempt_count",
			"open_callback_task",
			"next_action_due",
			"last_activity_by",
			"closed_at",
			"closed_by",
			"completion_reason",
			"reopened_at",
			"reopened_by",
			"reopen_reason",
			"policy_version",
		],
		as_dict=True,
	)
	camp = (
		frappe.db.get_value("CSKH Campaign", wi.campaign, ["code", "title", "status"], as_dict=True)
		or frappe._dict()
	)

	lead_fields = ["name", "lead_name", "first_name", "mobile_no", "email", "source"]
	for opt_f in ("cskh_is_placeholder_name", "cskh_do_not_contact", "cskh_dnc_reason", "cskh_dnc_at"):
		if frappe.db.has_column("CRM Lead", opt_f):
			lead_fields.append(opt_f)
	lead = frappe.db.get_value("CRM Lead", wi.lead, lead_fields, as_dict=True) or frappe._dict()

	open_event = frappe.db.get_value(
		"CSKH Assignment History",
		{"work_item": wi.name, "ownership_end": ["is", "not set"]},
		["name", "to_user", "ownership_start"],
		order_by="event_at desc, creation desc",
		as_dict=True,
	)

	now_dt = now_datetime()
	open_task_row = get_open_followup_task(wi.name)
	open_followup = (
		serialize_followup_task(open_task_row, now=now_dt, include_history=True) if open_task_row else None
	)

	policy = resolve_effective_policy(campaign=wi.campaign, owner_user=wi.assigned_to)
	capabilities = _capabilities_for_work_item(wi, policy, actor)

	return {
		"work_item": {
			"name": wi.name,
			"campaign": wi.campaign,
			"campaign_code": camp.get("code"),
			"campaign_title": camp.get("title"),
			"campaign_status": camp.get("status"),
			"status": wi.status,
			"revision": cint(wi.revision or 1),
			"assigned_to": wi.assigned_to,
			"assigned_at": wi.assigned_at,
			"first_attempt_at": wi.first_attempt_at,
			"last_attempt_at": wi.last_attempt_at,
			"last_outcome_code": wi.last_outcome_code,
			"last_outcome_label": wi.last_outcome_label,
			"last_connected_at": wi.last_connected_at,
			"attempt_count": cint(wi.attempt_count or 0),
			"connected_attempt_count": cint(wi.connected_attempt_count or 0),
			"open_callback_task": wi.open_callback_task,
			"next_action_due": wi.next_action_due,
			"last_activity_by": wi.last_activity_by,
			"closed_at": wi.closed_at,
			"closed_by": wi.closed_by,
			"completion_reason": wi.completion_reason,
			"reopened_at": wi.reopened_at,
			"reopened_by": wi.reopened_by,
			"reopen_reason": wi.reopen_reason,
			"policy_version": cint(wi.policy_version or policy["version"] or 1),
		},
		"lead": {
			"name": lead.get("name") or wi.lead,
			"display_name": lead.get("lead_name") or lead.get("first_name") or "",
			"phone": lead.get("mobile_no"),
			"email": lead.get("email"),
			"source": lead.get("source"),
			"is_placeholder_name": bool(cint(lead.get("cskh_is_placeholder_name") or 0)),
			"do_not_contact": bool(cint(lead.get("cskh_do_not_contact") or 0)),
			"dnc_reason": lead.get("cskh_dnc_reason"),
			"dnc_at": lead.get("cskh_dnc_at"),
		},
		"active_ownership": (
			{
				"event_name": open_event.name,
				"owner": open_event.to_user,
				"ownership_start": open_event.ownership_start,
			}
			if open_event
			else None
		),
		"open_followup": open_followup,
		"effective_policy": policy,
		"capabilities": capabilities,
		"as_of": now_dt,
	}


def list_work_items(
	page=1,
	page_size=20,
	status: str | None = None,
	campaign: str | None = None,
	followup_filter: str | None = None,
	assignee: str | None = None,
	search: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Scoped CSKH work queue (`docs/api-contract-p2.md` §4.2)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	page_num, limit, offset = parse_pagination(page, page_size)
	status_val = (parse_optional_str(status, "Bộ lọc status không hợp lệ.") or "active").lower()
	if status_val not in ("active", "all", *ALL_WORK_ITEM_STATES):
		raise InvalidRequest("Tham số status không hợp lệ.")
	camp_val = parse_optional_str(campaign, "Mã chiến dịch không hợp lệ.")
	fu_val = parse_optional_str(followup_filter, "Tham số followup_filter không hợp lệ.")
	if fu_val:
		fu_val = fu_val.lower()
		if fu_val not in ("overdue", "today", "upcoming", "has_open", "none"):
			raise InvalidRequest(
				"Tham số followup_filter chỉ chấp nhận overdue | today | upcoming | has_open | none."
			)
	assignee_val = parse_optional_str(assignee, "Tham số assignee không hợp lệ.")
	search_val = parse_optional_str(search, "Tham số search không hợp lệ.")

	scope = reporting._scope(actor)
	scope_label = "all" if scope["scope"] in ("system", "all") else scope["scope"]
	now_dt = now_datetime()
	today = getdate(now_dt)
	day_start = f"{today} 00:00:00"
	day_end = f"{today} 23:59:59"

	clauses: list[str] = ["1=1"]
	params: list = []

	if scope["scope"] in ("system", "all"):
		pass
	elif scope["scope"] == "team":
		clauses.append(leader_authorized_work_item_sql(actor, "wi"))
	else:
		clauses.append(staff_authorized_work_item_sql(actor, "wi"))

	if assignee_val:
		allowed_users = reporting._scope_users(scope)
		if allowed_users is not None and assignee_val not in allowed_users:
			return {
				"scope": scope_label,
				"items": [],
				"total": 0,
				"page": page_num,
				"page_size": limit,
				"as_of": now_dt,
			}
		clauses.append("wi.assigned_to = %s")
		params.append(assignee_val)

	if status_val == "active":
		clauses.append("wi.status in ('assigned', 'in_progress', 'on_hold')")
	elif status_val != "all":
		clauses.append("wi.status = %s")
		params.append(status_val)

	if camp_val:
		clauses.append("wi.campaign = %s")
		params.append(camp_val)

	if fu_val == "overdue":
		clauses.append("wi.status = 'on_hold' and wi.next_action_due is not null and wi.next_action_due < %s")
		params.append(now_dt)
	elif fu_val == "today":
		clauses.append("wi.status = 'on_hold' and wi.next_action_due >= %s and wi.next_action_due <= %s")
		params.extend([day_start, day_end])
	elif fu_val == "upcoming":
		clauses.append("wi.status = 'on_hold' and wi.next_action_due > %s")
		params.append(day_end)
	elif fu_val == "has_open":
		clauses.append("wi.status = 'on_hold' and wi.open_callback_task is not null")
	elif fu_val == "none":
		clauses.append("wi.open_callback_task is null")

	if search_val:
		like = f"%{search_val}%"
		digits = search_val.lstrip("0") if search_val.startswith("0") and search_val[1:].isdigit() else ""
		if digits:
			alt_like = f"%+84{digits}%"
			clauses.append(
				"(coalesce(l.lead_name, '') like %s "
				"or coalesce(l.first_name, '') like %s "
				"or coalesce(l.mobile_no, '') like %s "
				"or coalesce(l.mobile_no, '') like %s "
				"or l.name like %s "
				"or wi.name like %s)"
			)
			params.extend([like, like, like, alt_like, like, like])
		else:
			clauses.append(
				"(coalesce(l.lead_name, '') like %s "
				"or coalesce(l.first_name, '') like %s "
				"or coalesce(l.mobile_no, '') like %s "
				"or l.name like %s "
				"or wi.name like %s)"
			)
			params.extend([like, like, like, like, like])

	from_where = (
		"from `tabCSKH Work Item` wi "
		"inner join `tabCRM Lead` l on l.name = wi.lead "
		"left join `tabCSKH Campaign` c on c.name = wi.campaign "
		"where " + " and ".join(clauses)
	)
	total = int(frappe.db.sql(f"select count(*) {from_where}", tuple(params))[0][0] or 0)
	if total == 0:
		return {
			"scope": scope_label,
			"items": [],
			"total": 0,
			"page": page_num,
			"page_size": limit,
			"as_of": now_dt,
		}

	has_dnc = frappe.db.has_column("CRM Lead", "cskh_do_not_contact")
	dnc_col = "coalesce(l.cskh_do_not_contact, 0) as do_not_contact" if has_dnc else "0 as do_not_contact"

	rows = frappe.db.sql(
		f"""
		select wi.name as work_item, wi.lead as lead, wi.campaign as campaign,
		       wi.status as status, coalesce(wi.revision, 1) as revision,
		       wi.assigned_to as assigned_to, wi.assigned_at as assigned_at,
		       coalesce(wi.attempt_count, 0) as attempt_count,
		       coalesce(wi.connected_attempt_count, 0) as connected_attempt_count,
		       wi.first_attempt_at as first_attempt_at, wi.last_attempt_at as last_attempt_at,
		       wi.last_outcome_code as last_outcome_code, wi.last_outcome_label as last_outcome_label,
		       wi.last_connected_at as last_connected_at,
		       wi.open_callback_task as open_callback_task, wi.next_action_due as next_action_due,
		       wi.closed_at as closed_at, wi.closed_by as closed_by,
		       c.title as campaign_title, c.code as campaign_code,
		       l.lead_name as lead_name, l.first_name as first_name,
		       l.mobile_no as mobile_no, l.source as source, l.status as lead_status,
		       {dnc_col}
		{from_where}
		order by case when wi.status = 'on_hold' and wi.next_action_due is not null and wi.next_action_due < %s then 0 else 1 end asc,
		         coalesce(wi.next_action_due, wi.assigned_at, wi.creation) desc,
		         wi.name desc
		limit %s offset %s
		""",
		(*tuple(params), now_dt, limit, offset),
		as_dict=True,
	)

	items = []
	for row in rows:
		is_overdue = False
		if row.status == "on_hold" and row.next_action_due:
			try:
				is_overdue = get_datetime(row.next_action_due) < get_datetime(now_dt)
			except Exception:
				is_overdue = False
		items.append(
			{
				"work_item": row.work_item,
				"lead": row.lead,
				"display_name": row.lead_name or row.first_name or "",
				"phone": row.mobile_no,
				"source": row.source,
				"lead_status": row.lead_status,
				"campaign": row.campaign,
				"campaign_title": row.campaign_title,
				"campaign_code": row.campaign_code,
				"status": row.status,
				"work_item_status": row.status,
				"revision": int(row.revision or 1),
				"assigned_to": row.assigned_to,
				"assigned_at": row.assigned_at,
				"attempt_count": int(row.attempt_count or 0),
				"connected_attempt_count": int(row.connected_attempt_count or 0),
				"first_attempt_at": row.first_attempt_at,
				"last_attempt_at": row.last_attempt_at,
				"last_outcome_code": row.last_outcome_code,
				"last_outcome_label": row.last_outcome_label,
				"last_connected_at": row.last_connected_at,
				"open_callback_task": row.open_callback_task,
				"next_action_due": row.next_action_due,
				"is_followup_overdue": is_overdue,
				"closed_at": row.closed_at,
				"closed_by": row.closed_by,
				"do_not_contact": bool(int(row.do_not_contact or 0)),
			}
		)

	return {
		"scope": scope_label,
		"items": items,
		"total": total,
		"page": page_num,
		"page_size": limit,
		"as_of": now_dt,
	}


def list_attempts_timeline(
	work_item: str,
	page=1,
	page_size=20,
	include_milestones=True,
	actor: str | None = None,
) -> dict:
	"""Paginated effective attempt timeline + ownership milestones (`docs/api-contract-p2.md` §4.4)."""
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not wi_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	page_num, limit, offset = parse_pagination(page, page_size)
	inc_milestones = parse_bool(include_milestones, default=True, field="include_milestones")
	actor = assert_workflow_actor(actor, allow_bod=False)
	assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	all_projected = list(reversed(compute_effective_attempts(wi_val)))
	total = len(all_projected)
	page_items = all_projected[offset : offset + limit]

	milestones = []
	if inc_milestones:
		hist_rows = frappe.db.get_all(
			"CSKH Assignment History",
			filters={"work_item": wi_val},
			fields=[
				"event_id",
				"action",
				"from_user",
				"to_user",
				"actor",
				"event_at",
				"ownership_start",
				"ownership_end",
				"reason",
			],
			order_by="event_at asc, creation asc",
		)
		milestones = list(hist_rows)

	return {
		"work_item": wi_val,
		"items": page_items,
		"ownership_milestones": milestones,
		"total": total,
		"page": page_num,
		"page_size": limit,
		"as_of": now_datetime(),
	}


def _effective_attempt_rows_for_summary(
	wi_where: str,
	wi_params: list,
	date_from: str | None = None,
	date_to: str | None = None,
) -> list[dict]:
	"""Compute effective non-voided attempts across scoped Work Items for `progress_summary`."""
	date_clauses = []
	date_params = []
	if date_from:
		date_clauses.append("att.occurred_at >= %s")
		date_params.append(date_from)
	if date_to:
		date_clauses.append("att.occurred_at <= %s")
		date_params.append(date_to)
	extra_dt = (" and " + " and ".join(date_clauses)) if date_clauses else ""

	att_rows = frappe.db.sql(
		f"""
		select att.name, att.work_item, att.performed_by, att.occurred_at, att.channel,
		       att.outcome_code, att.outcome_group_snapshot as outcome_group,
		       att.contact_attempt_snapshot as is_contact_attempt,
		       att.connected_snapshot as is_connected
		from `tabCSKH Contact Attempt` att
		inner join `tabCSKH Work Item` wi on wi.name = att.work_item
		where {wi_where}{extra_dt}
		""",
		(*wi_params, *date_params),
		as_dict=True,
	)
	if not att_rows:
		return []

	att_names = [r.name for r in att_rows]
	corr_map: dict[str, list[dict]] = {}
	# Fetch approved corrections ONLY for attempts that have at least one approved correction
	for chunk_start in range(0, len(att_names), 1000):
		chunk = att_names[chunk_start : chunk_start + 1000]
		ph = ",".join(["%s"] * len(chunk))
		c_rows = frappe.db.sql(
			f"""
			select attempt, action, corrected_fields_json, reviewed_at, creation, name
			from `tabCSKH Attempt Correction`
			where status = 'approved' and attempt in ({ph})
			order by reviewed_at asc, creation asc, name asc
			""",
			tuple(chunk),
			as_dict=True,
		)
		for c in c_rows:
			corr_map.setdefault(c.attempt, []).append(c)

	effective_list: list[dict] = []
	for r in att_rows:
		is_voided = False
		channel = r.channel
		group = r.outcome_group or "other"
		is_att = bool(cint(r.is_contact_attempt))
		is_conn = bool(cint(r.is_connected))
		for c in corr_map.get(r.name, ()):
			if c.action == "void":
				is_voided = True
			elif c.action == "restore":
				is_voided = False
			elif c.action == "correct":
				patch = _parse_json_dict(c.corrected_fields_json)
				if patch.get("channel"):
					channel = patch["channel"]
				if patch.get("outcome_group"):
					group = patch["outcome_group"]
				if "is_contact_attempt" in patch:
					is_att = bool(cint(patch["is_contact_attempt"]))
				if "is_connected" in patch:
					is_conn = bool(cint(patch["is_connected"]))
		if not is_voided and is_att:
			effective_list.append(
				{
					"performed_by": r.performed_by,
					"channel": channel,
					"outcome_group": group,
					"is_connected": is_conn,
				}
			)
	return effective_list


def progress_summary(
	campaign: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Scoped P2 operational summary with strictly ZERO PII (`docs/api-contract-p2.md` §4.13)."""
	actor = assert_workflow_actor(actor, allow_bod=True)
	camp_val = parse_optional_str(campaign, "Mã chiến dịch không hợp lệ.")
	df_val = parse_optional_str(date_from, "Tham số date_from không hợp lệ.")
	dt_val = parse_optional_str(date_to, "Tham số date_to không hợp lệ.")
	if df_val:
		try:
			get_datetime(df_val)
		except Exception as exc:
			raise InvalidRequest("Tham số date_from không đúng định dạng thời gian.") from exc
	if dt_val:
		try:
			get_datetime(dt_val)
		except Exception as exc:
			raise InvalidRequest("Tham số date_to không đúng định dạng thời gian.") from exc

	scope = reporting._scope(actor)
	scope_label = "all" if scope["scope"] in ("system", "all") else scope["scope"]
	now_dt = now_datetime()
	today = getdate(now_dt)
	day_start = f"{today} 00:00:00"
	day_end = f"{today} 23:59:59"

	wi_scope_sql, wi_scope_params = reporting._work_item_scope_clause(scope, "wi")
	wi_clauses = [wi_scope_sql] if wi_scope_sql else ["1=1"]
	wi_params = list(wi_scope_params)
	if camp_val:
		wi_clauses.append("wi.campaign = %s")
		wi_params.append(camp_val)
	if df_val:
		wi_clauses.append("wi.creation >= %s")
		wi_params.append(df_val)
	if dt_val:
		wi_clauses.append("wi.creation <= %s")
		wi_params.append(dt_val)
	wi_where = " and ".join(wi_clauses)

	status_rows = frappe.db.sql(
		f"select wi.status, count(*) as cnt from `tabCSKH Work Item` wi where {wi_where} group by wi.status",
		tuple(wi_params),
		as_dict=True,
	)
	status_counts = {st: 0 for st in ALL_WORK_ITEM_STATES}
	for row in status_rows:
		if row.status in status_counts:
			status_counts[row.status] = int(row.cnt or 0)

	# Open follow-ups & overdue / due_today counts
	fu_row = frappe.db.sql(
		f"""
		select
			count(*) as open_total,
			sum(case when t.due_date < %s then 1 else 0 end) as overdue_total,
			sum(case when t.due_date >= %s and t.due_date <= %s then 1 else 0 end) as today_total
		from `tabCRM Task` t
		inner join `tabCSKH Work Item` wi on wi.name = t.cskh_work_item
		where coalesce(t.cskh_is_followup, 0) = 1
		  and t.status in ('Backlog', 'Todo', 'In Progress')
		  and {wi_where}
		""",
		(now_dt, day_start, day_end, *wi_params),
		as_dict=True,
	)[0]
	open_followups = int(fu_row.open_total or 0)
	overdue_followups = int(fu_row.overdue_total or 0)
	due_today_followups = int(fu_row.today_total or 0)

	# Pending corrections in scope
	pending_corrections = int(
		frappe.db.sql(
			f"""
			select count(*)
			from `tabCSKH Attempt Correction` corr
			inner join `tabCSKH Work Item` wi on wi.name = corr.work_item
			where corr.status = 'pending' and {wi_where}
			""",
			tuple(wi_params),
		)[0][0]
		or 0
	)

	# DNC leads in scope
	dnc_leads = 0
	if frappe.db.has_column("CRM Lead", "cskh_do_not_contact"):
		if scope["scope"] in ("system", "all") and not camp_val and not df_val and not dt_val:
			dnc_leads = frappe.db.count("CRM Lead", {"cskh_do_not_contact": 1})
		else:
			dnc_leads = int(
				frappe.db.sql(
					f"""
					select count(distinct wi.lead)
					from `tabCSKH Work Item` wi
					inner join `tabCRM Lead` l on l.name = wi.lead
					where coalesce(l.cskh_do_not_contact, 0) = 1 and {wi_where}
					""",
					tuple(wi_params),
				)[0][0]
				or 0
			)

	# Effective attempts (excluding date filter on wi.creation so date_from/date_to applies to occurred_at)
	att_wi_clauses = [wi_scope_sql] if wi_scope_sql else ["1=1"]
	att_wi_params = list(wi_scope_params)
	if camp_val:
		att_wi_clauses.append("wi.campaign = %s")
		att_wi_params.append(camp_val)
	effective_attempts_list = _effective_attempt_rows_for_summary(
		" and ".join(att_wi_clauses), att_wi_params, date_from=df_val, date_to=dt_val
	)

	by_outcome_group = {grp: 0 for grp in RESULT_GROUPS}
	by_channel = {ch: 0 for ch in VALID_CHANNELS}
	connected_total = 0
	user_att_stats: dict[str, dict[str, int]] = {}
	for att in effective_attempts_list:
		grp = att["outcome_group"] if att["outcome_group"] in by_outcome_group else "other"
		by_outcome_group[grp] += 1
		ch = att["channel"] if att["channel"] in by_channel else "other"
		by_channel[ch] += 1
		if att["is_connected"]:
			connected_total += 1
		perf = att["performed_by"]
		if perf:
			slot = user_att_stats.setdefault(perf, {"effective_attempts": 0, "connected_attempts": 0})
			slot["effective_attempts"] += 1
			if att["is_connected"]:
				slot["connected_attempts"] += 1

	eff_total = len(effective_attempts_list)
	unreached_total = eff_total - connected_total

	# Per-user breakdown
	per_user_wi_rows = frappe.db.sql(
		f"""
		select
			coalesce(wi.assigned_to, wi.closed_by) as user_id,
			sum(case when wi.status = 'assigned' and wi.assigned_to is not null then 1 else 0 end) as assigned_cnt,
			sum(case when wi.status = 'in_progress' and wi.assigned_to is not null then 1 else 0 end) as in_progress_cnt,
			sum(case when wi.status = 'on_hold' and wi.assigned_to is not null then 1 else 0 end) as on_hold_cnt,
			sum(case when wi.status = 'closed' then 1 else 0 end) as closed_cnt
		from `tabCSKH Work Item` wi
		where coalesce(wi.assigned_to, wi.closed_by) is not null and {wi_where}
		group by coalesce(wi.assigned_to, wi.closed_by)
		""",
		tuple(wi_params),
		as_dict=True,
	)
	per_user_fu_rows = frappe.db.sql(
		f"""
		select
			t.assigned_to as user_id,
			count(*) as open_cnt,
			sum(case when t.due_date < %s then 1 else 0 end) as overdue_cnt
		from `tabCRM Task` t
		inner join `tabCSKH Work Item` wi on wi.name = t.cskh_work_item
		where coalesce(t.cskh_is_followup, 0) = 1
		  and t.status in ('Backlog', 'Todo', 'In Progress')
		  and t.assigned_to is not null
		  and {wi_where}
		group by t.assigned_to
		""",
		(now_dt, *wi_params),
		as_dict=True,
	)
	fu_by_user = {
		r.user_id: {"open": int(r.open_cnt or 0), "overdue": int(r.overdue_cnt or 0)}
		for r in per_user_fu_rows
	}
	wi_by_user = {r.user_id: r for r in per_user_wi_rows}

	allowed_scope_users = reporting._scope_users(scope)
	all_users = sorted(
		u
		for u in (set(wi_by_user) | set(fu_by_user) | set(user_att_stats))
		if u and (allowed_scope_users is None or u in allowed_scope_users)
	)
	team_by_user = {}
	if all_users:
		ph = ",".join(["%s"] * len(all_users))
		for h in frappe.db.sql(
			f"select user, name from `tabCRM Sales Hierarchy` where user in ({ph})",
			tuple(all_users),
			as_dict=True,
		):
			team_by_user[h.user] = h.name

	per_user = []
	for u in all_users:
		w = wi_by_user.get(u) or frappe._dict()
		f = fu_by_user.get(u) or {"open": 0, "overdue": 0}
		a = user_att_stats.get(u) or {"effective_attempts": 0, "connected_attempts": 0}
		per_user.append(
			{
				"user": u,
				"team": team_by_user.get(u),
				"assigned": int(w.get("assigned_cnt") or 0),
				"in_progress": int(w.get("in_progress_cnt") or 0),
				"on_hold": int(w.get("on_hold_cnt") or 0),
				"closed": int(w.get("closed_cnt") or 0),
				"effective_attempts": a["effective_attempts"],
				"connected_attempts": a["connected_attempts"],
				"open_followups": f["open"],
				"overdue_followups": f["overdue"],
			}
		)

	total_active_owned = status_counts["assigned"] + status_counts["in_progress"] + status_counts["on_hold"]
	return {
		"scope": scope_label,
		"cards": {
			"total_active_owned": total_active_owned,
			"assigned": status_counts["assigned"],
			"in_progress": status_counts["in_progress"],
			"on_hold": status_counts["on_hold"],
			"closed": status_counts["closed"],
			"revoked": status_counts["revoked"],
			"queued": status_counts["queued"],
			"effective_attempts": eff_total,
			"connected_attempts": connected_total,
			"unreached_attempts": unreached_total,
			"open_followups": open_followups,
			"overdue_followups": overdue_followups,
			"due_today_followups": due_today_followups,
			"pending_corrections": pending_corrections,
			"dnc_leads": dnc_leads,
		},
		"by_outcome_group": by_outcome_group,
		"by_channel": by_channel,
		"per_user": per_user,
		"as_of": now_dt,
	}


def reconcile_p2_invariants(campaign: str | None = None) -> dict:
	"""Verify all P2 database consistency invariants (`docs/race-retry-reconciliation.md`)."""
	camp_clause = "where wi.campaign = %s" if campaign else ""
	camp_params = (campaign,) if campaign else ()

	# 1. Orphan or terminal `CSKH Active Claim` rows
	orphan_claims = frappe.db.sql(
		f"""
		select ac.name, ac.lead, ac.work_item, wi.status
		from `tabCSKH Active Claim` ac
		left join `tabCSKH Work Item` wi on wi.name = ac.work_item
		where (wi.name is null or wi.status not in ('queued', 'assigned', 'in_progress', 'on_hold'))
		{"and ac.campaign = %s" if campaign else ""}
		""",
		camp_params,
		as_dict=True,
	)

	# 2. Leads holding > 1 owned active `CSKH Work Item`
	duplicate_owned_leads = frappe.db.sql(
		"""
		select lead, count(*) as active_count
		from `tabCSKH Work Item`
		where status in ('assigned', 'in_progress', 'on_hold')
		group by lead
		having count(*) > 1
		""",
		as_dict=True,
	)

	# 3. Callback / `on_hold` invariants:
	#    a) Work items with > 1 open callback task
	multi_open_callbacks = frappe.db.sql(
		f"""
		select t.cskh_work_item as work_item, count(*) as open_count
		from `tabCRM Task` t
		inner join `tabCSKH Work Item` wi on wi.name = t.cskh_work_item
		where coalesce(t.cskh_is_followup, 0) = 1
		  and t.status in ('Backlog', 'Todo', 'In Progress')
		  {"and wi.campaign = %s" if campaign else ""}
		group by t.cskh_work_item
		having count(*) > 1
		""",
		camp_params,
		as_dict=True,
	)
	#    b) `on_hold` work items missing an open callback task, OR non-`on_hold` work items still having an open callback task
	callback_status_mismatches = frappe.db.sql(
		f"""
		select wi.name as work_item, wi.status, wi.open_callback_task,
		       coalesce(tc.actual_open_tasks, 0) as actual_open_tasks
		from `tabCSKH Work Item` wi
		left join (
			select cskh_work_item, count(*) as actual_open_tasks
			from `tabCRM Task`
			where coalesce(cskh_is_followup, 0) = 1
			  and status in ('Backlog', 'Todo', 'In Progress')
			group by cskh_work_item
		) tc on tc.cskh_work_item = wi.name
		{camp_clause}
		{"and" if camp_clause else "where"} (
			(wi.status = 'on_hold' and coalesce(tc.actual_open_tasks, 0) != 1)
			or (wi.status != 'on_hold' and coalesce(tc.actual_open_tasks, 0) > 0)
		)
		""",
		camp_params,
		as_dict=True,
	)

	# 4. Open ownership interval mismatches on owned active Work Items
	ownership_mismatches = frappe.db.sql(
		f"""
		select wi.name as work_item, wi.status, wi.assigned_to,
		       coalesce(hc.open_intervals, 0) as open_intervals,
		       hc.open_interval_owner
		from `tabCSKH Work Item` wi
		left join (
			select work_item,
			       count(*) as open_intervals,
			       max(to_user) as open_interval_owner
			from `tabCSKH Assignment History`
			where ownership_end is null
			group by work_item
		) hc on hc.work_item = wi.name
		{camp_clause}
		{"and" if camp_clause else "where"} (
			(wi.status in ('assigned', 'in_progress', 'on_hold')
			 and (coalesce(hc.open_intervals, 0) != 1
			      or coalesce(hc.open_interval_owner, '') != coalesce(wi.assigned_to, '')))
			or (wi.status in ('queued', 'closed', 'revoked') and coalesce(hc.open_intervals, 0) > 0)
		)
		""",
		camp_params,
		as_dict=True,
	)

	# 5. Open callback task due_date vs Work Item.next_action_due & assignee/lead alignment (`P2.md` §15.5)
	due_projection_mismatches = frappe.db.sql(
		f"""
		select wi.name as work_item, wi.next_action_due, wi.assigned_to as wi_owner, wi.lead as wi_lead,
		       t.name as task_id, t.due_date as task_due_date, t.assigned_to as task_owner,
		       t.reference_docname as task_lead
		from `tabCRM Task` t
		left join `tabCSKH Work Item` wi on wi.name = t.cskh_work_item
		where coalesce(t.cskh_is_followup, 0) = 1
		  and t.status in ('Backlog', 'Todo', 'In Progress')
		  {"and wi.campaign = %s" if campaign else ""}
		  and (wi.name is null
		       or coalesce(t.assigned_to, '') != coalesce(wi.assigned_to, '')
		       or coalesce(t.reference_docname, '') != coalesce(wi.lead, '')
		       or t.due_date != wi.next_action_due)
		""",
		camp_params,
		as_dict=True,
	)

	# 6. Duplicate `CRM Call Log` links across `CSKH Contact Attempt`s
	duplicate_call_log_links = frappe.db.sql(
		f"""
		select att.call_log, count(*) as link_count
		from `tabCSKH Contact Attempt` att
		inner join `tabCSKH Work Item` wi on wi.name = att.work_item
		where att.call_log is not null and att.call_log != ''
		  {"and wi.campaign = %s" if campaign else ""}
		group by att.call_log
		having count(*) > 1
		""",
		camp_params,
		as_dict=True,
	)

	# 7. Attempt `occurred_at` outside linked `CSKH Assignment History` interval (`P2.md` §15.5)
	interval_violations = frappe.db.sql(
		f"""
		select att.name as attempt_id, att.work_item, att.performed_by, att.occurred_at,
		       h.name as history_name, h.to_user, h.ownership_start, h.ownership_end
		from `tabCSKH Contact Attempt` att
		inner join `tabCSKH Work Item` wi on wi.name = att.work_item
		left join `tabCSKH Assignment History` h on h.name = att.ownership_event
		where (h.name is null
		   or (h.ownership_start is not null and att.occurred_at < h.ownership_start)
		   or (h.ownership_end is not null and att.occurred_at > h.ownership_end))
		  {"and wi.campaign = %s" if campaign else ""}
		""",
		camp_params,
		as_dict=True,
	)

	# 8. Effective counter drift check across Work Items that have >= 1 attempt or nonzero counter
	candidate_wis = frappe.db.sql(
		f"""
		select wi.name, coalesce(wi.attempt_count, 0) as attempt_count,
		       coalesce(wi.connected_attempt_count, 0) as connected_attempt_count,
		       wi.last_outcome_code
		from `tabCSKH Work Item` wi
		where (coalesce(wi.attempt_count, 0) > 0
		   or coalesce(wi.connected_attempt_count, 0) as_check > 0
		   or exists (select 1 from `tabCSKH Contact Attempt` a where a.work_item = wi.name))
		  {"and wi.campaign = %s" if campaign else ""}
		""".replace(" as_check", ""),
		camp_params,
		as_dict=True,
	)
	counter_drifts = []
	for chunk_start in range(0, len(candidate_wis), 500):
		wi_chunk = candidate_wis[chunk_start : chunk_start + 500]
		wi_names = [row.name for row in wi_chunk]
		chunk_attempts = frappe.db.get_all(
			"CSKH Contact Attempt",
			filters={"work_item": ["in", wi_names]},
			fields=_ATTEMPT_PROJECTION_FIELDS,
			order_by="occurred_at asc, creation asc, name asc",
			limit_page_length=0,
		)
		chunk_corrs = frappe.db.get_all(
			"CSKH Attempt Correction",
			filters={"work_item": ["in", wi_names]},
			fields=_CORRECTION_PROJECTION_FIELDS,
			order_by="creation asc, name asc",
			limit_page_length=0,
		)
		atts_by_wi: dict[str, list] = {}
		for a in chunk_attempts:
			atts_by_wi.setdefault(a.work_item, []).append(a)
		corrs_by_wi: dict[str, list] = {}
		for c in chunk_corrs:
			corrs_by_wi.setdefault(c.work_item, []).append(c)

		for wi in wi_chunk:
			summary = summarize_effective_attempts(
				_project_attempts_rows(atts_by_wi.get(wi.name, []), corrs_by_wi.get(wi.name, []))
			)
			if (
				int(wi.attempt_count) != summary["attempt_count"]
				or int(wi.connected_attempt_count) != summary["connected_attempt_count"]
				or (wi.last_outcome_code or None) != (summary["last_outcome_code"] or None)
			):
				counter_drifts.append(
					{
						"work_item": wi.name,
						"stored_attempt_count": int(wi.attempt_count),
						"computed_attempt_count": summary["attempt_count"],
						"stored_connected_count": int(wi.connected_attempt_count),
						"computed_connected_count": summary["connected_attempt_count"],
						"stored_last_outcome": wi.last_outcome_code,
						"computed_last_outcome": summary["last_outcome_code"],
					}
				)

	ok = not (
		orphan_claims
		or duplicate_owned_leads
		or multi_open_callbacks
		or callback_status_mismatches
		or ownership_mismatches
		or due_projection_mismatches
		or duplicate_call_log_links
		or interval_violations
		or counter_drifts
	)
	return {
		"ok": ok,
		"campaign": campaign,
		"orphan_claims": orphan_claims,
		"duplicate_owned_leads": duplicate_owned_leads,
		"multi_open_callbacks": multi_open_callbacks,
		"callback_status_mismatches": callback_status_mismatches,
		"ownership_mismatches": ownership_mismatches,
		"due_projection_mismatches": due_projection_mismatches,
		"duplicate_call_log_links": duplicate_call_log_links,
		"interval_violations": interval_violations,
		"counter_drifts": counter_drifts,
		"checked_work_items_with_attempts": len(candidate_wis),
		"as_of": now_datetime(),
	}
