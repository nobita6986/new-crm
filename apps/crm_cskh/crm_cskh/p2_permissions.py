"""Row-level & document-level permissions and authorization helpers for P2 DocTypes.

Implements `docs/security-matrix-p2.md` (`ADR-P2-08`, `SEC2-01..SEC2-16`):
  * `CSKH Outcome Code`: read-only metadata for Admin, BOD, Leader, Staff; write strictly via `settings` service (`CSKH CRM Admin` / `System Manager`).
  * `CSKH Policy`: read-only scoped metadata for Admin, BOD, Leader, Staff; write strictly via `settings` service (`CSKH CRM Admin` / `System Manager`).
  * `CSKH Contact Attempt`: strictly append-only; 0 PII for `CSKH BOD Viewer` (`1=0` / `False`); current-owner only for `Sales User`; current team scope (with `L-23` cross-team guard on closed items) for `Sales Manager`; global read for `CSKH CRM Admin`.
  * `CSKH Attempt Correction`: audited overlay; 0 PII for `CSKH BOD Viewer`; current-owner / current-team / Admin read only; write strictly via `correction` service.
"""

from __future__ import annotations

import frappe

from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p1_permissions import (
	READ_PTYPES,
	ROLE_ADMIN,
	ROLE_BOD,
	WRITE_PTYPES,
	_campaign_in_leader_scope,
	_campaign_scope_condition,
	_escape,
	_hierarchy_subtree,
	_in_team,
	_is_leader,
	_is_staff,
	_is_system,
	_node_in_subtree,
	_resolve_doc,
	_roles,
	_team_bounds,
	_team_user_condition,
)
from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden, NotFound


def _user_ancestor_nodes(user: str) -> list[str]:
	row = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"], as_dict=True)
	if not row:
		return []
	return list(
		frappe.db.sql(
			"select name from `tabCRM Sales Hierarchy` where lft <= %s and rgt >= %s",
			(row.lft, row.rgt),
			pluck=True,
		)
		or []
	)


def leader_authorized_work_item_sql(user: str, wi_alias: str = "wi") -> str:
	"""SQL predicate on `tabCSKH Work Item` `wi_alias` for a `Sales Manager` (`user`).

	Grants read access when:
	  1. `wi.status in ('assigned', 'in_progress', 'on_hold')` and `wi.assigned_to` is in `user`'s team subtree; OR
	  2. `wi.status = 'closed'` and `wi.assigned_to is null`, the item was closed/worked by `user`'s team
	     (or belongs to `user`'s private team campaign), AND the underlying `CRM Lead` has NOT moved to
	     another team's active ownership, claim, or private queue (`L-23` / `SEC2-03`).
	"""
	bounds = _team_bounds(user)
	if not bounds:
		return f"`{wi_alias}`.`assigned_to` = {_escape(user)}"
	lft_sql = _escape(bounds[0])
	rgt_sql = _escape(bounds[1])
	team_users_subq = (
		f"(select `m`.`user` from `tabCRM Sales Hierarchy` `m` "
		f"where `m`.`lft` >= {lft_sql} and `m`.`rgt` <= {rgt_sql} and `m`.`user` is not null)"
	)
	team_nodes_subq = (
		f"(select `n`.`name` from `tabCRM Sales Hierarchy` `n` "
		f"where `n`.`lft` >= {lft_sql} and `n`.`rgt` <= {rgt_sql})"
	)
	owner_close_pred = (
		f"`{wi_alias}`.`owner_at_close` in {team_users_subq} or "
		if frappe.db.has_column("CSKH Work Item", "owner_at_close")
		else ""
	)
	return (
		f"((`{wi_alias}`.`status` in ('assigned', 'in_progress', 'on_hold') "
		f"and `{wi_alias}`.`assigned_to` in {team_users_subq}) "
		f"or (`{wi_alias}`.`status` = 'closed' and `{wi_alias}`.`assigned_to` is null "
		f"and ({owner_close_pred}`{wi_alias}`.`closed_by` in {team_users_subq} "
		f"or `{wi_alias}`.`last_activity_by` in {team_users_subq} "
		f"or `{wi_alias}`.`campaign` in (select `c_cl`.`name` from `tabCSKH Campaign` `c_cl` "
		f"where `c_cl`.`team_scope` in {team_nodes_subq})) "
		f"and not exists (select 1 from `tabCSKH Work Item` `wi_for` "
		f"where `wi_for`.`lead` = `{wi_alias}`.`lead` "
		f"and `wi_for`.`status` in ('assigned', 'in_progress', 'on_hold') "
		f"and (`wi_for`.`assigned_to` is null or `wi_for`.`assigned_to` not in {team_users_subq})) "
		f"and not exists (select 1 from `tabCSKH Active Claim` `ac_for` "
		f"left join `tabCSKH Campaign` `c_ac_for` on `c_ac_for`.`name` = `ac_for`.`campaign` "
		f"left join `tabCSKH Work Item` `wi_ac_for` on `wi_ac_for`.`name` = `ac_for`.`work_item` "
		f"where `ac_for`.`lead` = `{wi_alias}`.`lead` "
		f"and ((`c_ac_for`.`team_scope` is not null and `c_ac_for`.`team_scope` not in {team_nodes_subq}) "
		f"or (`wi_ac_for`.`assigned_to` is not null and `wi_ac_for`.`assigned_to` not in {team_users_subq}))) "
		f"and exists (select 1 from `tabCRM Lead` `l_for` "
		f"where `l_for`.`name` = `{wi_alias}`.`lead` "
		f"and (`l_for`.`lead_owner` is null or `l_for`.`lead_owner` in ('', 'Administrator') "
		f"or `l_for`.`lead_owner` in {team_users_subq}))))"
	)


def staff_authorized_work_item_sql(user: str, wi_alias: str = "wi") -> str:
	"""SQL predicate on `tabCSKH Work Item` `wi_alias` for a `Sales User` (`user`).

	Grants read access when:
	  1. `wi.status in ('assigned', 'in_progress', 'on_hold')` and `wi.assigned_to = user`; OR
	  2. `wi.status = 'closed'`, `wi.assigned_to is null`, `user` was the owner at the time of close
	     (`coalesce(nullif(wi.owner_at_close, ''), wi.closed_by) = user` or `wi.closed_by = user`),
	     AND the underlying `CRM Lead` has NOT moved to any other user or active claim
	     (`SEC2-07`, `F-P2-04`, `docs/api-contract-p2.md` §3).
	"""
	u_sql = _escape(user)
	if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
		closed_owner_pred = (
			f"(coalesce(nullif(`{wi_alias}`.`owner_at_close`, ''), `{wi_alias}`.`closed_by`) = {u_sql} "
			f"or `{wi_alias}`.`closed_by` = {u_sql})"
		)
	else:
		closed_owner_pred = f"`{wi_alias}`.`closed_by` = {u_sql}"
	return (
		f"((`{wi_alias}`.`status` in ('assigned', 'in_progress', 'on_hold') "
		f"and `{wi_alias}`.`assigned_to` = {u_sql}) "
		f"or (`{wi_alias}`.`status` = 'closed' and `{wi_alias}`.`assigned_to` is null "
		f"and {closed_owner_pred} "
		f"and not exists (select 1 from `tabCSKH Work Item` `wi_for` "
		f"where `wi_for`.`lead` = `{wi_alias}`.`lead` "
		f"and `wi_for`.`status` in ('assigned', 'in_progress', 'on_hold') "
		f"and (`wi_for`.`assigned_to` is null or `wi_for`.`assigned_to` != {u_sql})) "
		f"and not exists (select 1 from `tabCSKH Active Claim` `ac_for` "
		f"where `ac_for`.`lead` = `{wi_alias}`.`lead`) "
		f"and exists (select 1 from `tabCRM Lead` `l_for` "
		f"where `l_for`.`name` = `{wi_alias}`.`lead` "
		f"and (`l_for`.`lead_owner` is null "
		f"or `l_for`.`lead_owner` in ('', 'Administrator', {u_sql})))))"
	)


def is_work_item_readable_for_workflow(work_item_doc_or_name, user: str | None = None) -> bool:
	"""True when `user` may read workflow PII/timeline of `work_item` (`SEC2-01..04`)."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return True
	# `CSKH BOD Viewer` NEVER reads customer PII or workflow timelines (`SEC2-01`).
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return False

	has_owner_close = frappe.db.has_column("CSKH Work Item", "owner_at_close")
	if isinstance(work_item_doc_or_name, str):
		wi_fields = [
			"name",
			"lead",
			"campaign",
			"status",
			"assigned_to",
			"closed_by",
			"last_activity_by",
		]
		if has_owner_close:
			wi_fields.append("owner_at_close")
		wi = frappe.db.get_value(
			"CSKH Work Item",
			work_item_doc_or_name,
			wi_fields,
			as_dict=True,
		)
	else:
		wi = work_item_doc_or_name
	if not wi:
		return False

	status = wi.get("status")
	assigned_to = wi.get("assigned_to")
	owner_at_close = wi.get("owner_at_close")
	if status == "closed" and owner_at_close is None and has_owner_close and wi.get("name"):
		owner_at_close = frappe.db.get_value("CSKH Work Item", wi.get("name"), "owner_at_close")

	if _is_leader(user):
		if status in OWNED_ACTIVE_WORK_ITEM_STATES and assigned_to:
			return assigned_to == user or _in_team(user, assigned_to)
		if status == "closed" and not assigned_to:
			camp_team = frappe.db.get_value("CSKH Campaign", wi.get("campaign"), "team_scope")
			affiliated = (
				(owner_at_close and (owner_at_close == user or _in_team(user, owner_at_close)))
				or (
					wi.get("closed_by")
					and (wi.get("closed_by") == user or _in_team(user, wi.get("closed_by")))
				)
				or (
					wi.get("last_activity_by")
					and (wi.get("last_activity_by") == user or _in_team(user, wi.get("last_activity_by")))
				)
				or (camp_team and _node_in_subtree(user, camp_team))
			)
			if not affiliated:
				return False
			scope = reporting._scope(user)
			authorized = reporting.filter_team_authorized_leads(scope, [wi.get("lead")])
			return bool(wi.get("lead") in authorized)
		return False

	if _is_staff(user):
		if status in OWNED_ACTIVE_WORK_ITEM_STATES and assigned_to == user:
			return True
		effective_close_owner = owner_at_close or wi.get("closed_by")
		if (
			status == "closed"
			and not assigned_to
			and (effective_close_owner == user or wi.get("closed_by") == user)
		):
			lead_name = wi.get("lead")
			if not lead_name:
				return False
			other_owned = frappe.db.exists(
				"CSKH Work Item",
				{
					"lead": lead_name,
					"status": ["in", list(OWNED_ACTIVE_WORK_ITEM_STATES)],
					"assigned_to": ["!=", user],
				},
			)
			if other_owned or frappe.db.exists("CSKH Active Claim", {"lead": lead_name}):
				return False
			lead_owner = frappe.db.get_value("CRM Lead", lead_name, "lead_owner")
			return lead_owner in (None, "", "Administrator", user)
		return False

	return False


def assert_workflow_actor(user: str | None = None, allow_bod: bool = False) -> str:
	"""Verify `user` is an authenticated CSKH actor and block BOD on PII endpoints."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		raise Forbidden("Bạn cần đăng nhập để thực hiện thao tác này.")
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return user
	roles = _roles(user)
	if ROLE_BOD in roles and not (_is_leader(user) or _is_staff(user)):
		if allow_bod:
			return user
		raise Forbidden("Vai trò BOD chỉ được xem số liệu tổng hợp, không truy cập dữ liệu chi tiết PII.")
	if _is_leader(user) or _is_staff(user):
		return user
	raise Forbidden("Tài khoản không có quyền truy cập nghiệp vụ chăm sóc khách hàng.")


def assert_work_item_workflow_access(
	work_item: str,
	user: str | None = None,
	require_active_mutation: bool = False,
) -> dict:
	"""Load `work_item` metadata and enforce P2 workflow authorization.

	Raises `NotFound` when `work_item` does not exist for authorized Admin/System callers,
	or `Forbidden` when `user` has no scope over `work_item` (avoiding cross-team existence leaks).
	"""
	user = assert_workflow_actor(user, allow_bod=False)
	wi_fields = [
		"name",
		"lead",
		"campaign",
		"status",
		"revision",
		"assigned_to",
		"assigned_at",
		"open_callback_task",
		"next_action_due",
		"attempt_count",
		"connected_attempt_count",
		"closed_by",
		"last_activity_by",
	]
	if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
		wi_fields.append("owner_at_close")
	wi = frappe.db.get_value(
		"CSKH Work Item",
		work_item,
		wi_fields,
		as_dict=True,
	)
	if not wi:
		if _is_system(user) or ROLE_ADMIN in _roles(user):
			raise NotFound("Không tìm thấy Work Item.")
		raise Forbidden("Bạn không có quyền truy cập Work Item này.")

	if not is_work_item_readable_for_workflow(wi, user=user):
		raise Forbidden("Bạn không có quyền truy cập Work Item ngoài phạm vi phụ trách.")

	if require_active_mutation:
		if _is_system(user) or ROLE_ADMIN in _roles(user):
			return wi
		if _is_leader(user):
			if wi.assigned_to and (wi.assigned_to == user or _in_team(user, wi.assigned_to)):
				return wi
			if wi.status == "closed":
				return wi
			raise Forbidden("Leader chỉ được thao tác trên Work Item thuộc phạm vi team.")
		if _is_staff(user):
			if wi.status in OWNED_ACTIVE_WORK_ITEM_STATES and wi.assigned_to == user:
				return wi
			raise Forbidden("Nhân viên chỉ được thao tác trên Work Item đang do chính mình phụ trách.")
	return wi


def assert_settings_admin(user: str | None = None) -> str:
	user = user or frappe.session.user
	if not user or user == "Guest":
		raise Forbidden("Bạn cần đăng nhập để thực hiện thao tác quản trị CSKH.")
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return user
	raise Forbidden("Thao tác cấu hình này chỉ dành cho CSKH CRM Admin.")


# ---------------------------------------------------------------------------
# `permission_query_conditions` for P2 DocTypes
# ---------------------------------------------------------------------------


def outcome_code_query_conditions(user: str | None = None, **kwargs) -> str:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return "1=0"
	if _is_system(user) or bool({ROLE_ADMIN, ROLE_BOD, "Sales Manager", "Sales User"} & _roles(user)):
		return ""
	return "1=0"


def policy_query_conditions(user: str | None = None, **kwargs) -> str:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return "1=0"
	if _is_system(user) or bool({ROLE_ADMIN, ROLE_BOD} & _roles(user)):
		return ""
	if _is_leader(user):
		subtree = _hierarchy_subtree(user)
		camp_cond = _campaign_scope_condition(user, "campaign")
		return (
			f"(`scope_type` = 'Global' "
			f"or (`scope_type` = 'Team' and `team` in {subtree}) "
			f"or (`scope_type` = 'Campaign' and {camp_cond}))"
		)
	if _is_staff(user):
		ancestors = _user_ancestor_nodes(user)
		team_part = "1=0"
		if ancestors:
			escaped = ", ".join(_escape(node) for node in ancestors)
			team_part = f"(`scope_type` = 'Team' and `team` in ({escaped}))"
		user_esc = _escape(user)
		camp_part = (
			f"(`scope_type` = 'Campaign' and `campaign` in "
			f"(select distinct `wi_pol`.`campaign` from `tabCSKH Work Item` `wi_pol` "
			f"where `wi_pol`.`assigned_to` = {user_esc}))"
		)
		return f"(`scope_type` = 'Global' or {team_part} or {camp_part})"
	return "1=0"


def contact_attempt_query_conditions(user: str | None = None, **kwargs) -> str:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return "1=0"
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	# BOD Viewer has 0 access to attempt timelines / notes (`SEC2-01`).
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return "1=0"
	if _is_leader(user):
		wi_pred = leader_authorized_work_item_sql(user, "wi_att")
		return f"`work_item` in (select `wi_att`.`name` from `tabCSKH Work Item` `wi_att` where {wi_pred})"
	if _is_staff(user):
		wi_pred = staff_authorized_work_item_sql(user, "wi_att")
		return f"`work_item` in (select `wi_att`.`name` from `tabCSKH Work Item` `wi_att` where {wi_pred})"
	return "1=0"


def attempt_correction_query_conditions(user: str | None = None, **kwargs) -> str:
	# Exact same Work Item ownership / scope boundary as `CSKH Contact Attempt`.
	return contact_attempt_query_conditions(user=user, **kwargs)


# ---------------------------------------------------------------------------
# `has_permission` for P2 DocTypes
# ---------------------------------------------------------------------------


def outcome_code_has_permission(doc, ptype: str = "read", user: str | None = None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES:
		if bool({ROLE_ADMIN, ROLE_BOD, "Sales Manager", "Sales User"} & _roles(user)):
			return None
	return False


def policy_has_permission(doc, ptype: str = "read", user: str | None = None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype not in READ_PTYPES:
		return False
	if bool({ROLE_ADMIN, ROLE_BOD} & _roles(user)):
		return None
	resolved, is_doctype_level = _resolve_doc("CSKH Policy", doc)
	if is_doctype_level:
		return None if (_is_leader(user) or _is_staff(user)) else False
	if resolved is None:
		return False
	scope_type = resolved.get("scope_type")
	if scope_type == "Global":
		return None if (_is_leader(user) or _is_staff(user)) else False
	if _is_leader(user):
		if scope_type == "Team":
			return None if _node_in_subtree(user, resolved.get("team")) else False
		if scope_type == "Campaign":
			camp = frappe.db.get_value(
				"CSKH Campaign", resolved.get("campaign"), ["name", "team_scope"], as_dict=True
			)
			return None if _campaign_in_leader_scope(user, camp) else False
	if _is_staff(user):
		if scope_type == "Team":
			return None if resolved.get("team") in _user_ancestor_nodes(user) else False
		if scope_type == "Campaign":
			has_wi = frappe.db.exists(
				"CSKH Work Item", {"campaign": resolved.get("campaign"), "assigned_to": user}
			)
			return None if has_wi else False
	return False


def contact_attempt_has_permission(doc, ptype: str = "read", user: str | None = None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		# Even System Manager cannot mutate/delete existing Contact Attempts (`before_save`/`on_trash`),
		# and direct generic writes are blocked here unless running inside `cskh_in_attempt_service`.
		effective_ptype = (ptype or "read").lower()
		if effective_ptype in WRITE_PTYPES and not getattr(frappe.flags, "cskh_in_attempt_service", False):
			return False
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype not in READ_PTYPES:
		return False
	if ROLE_ADMIN in _roles(user):
		return None
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return False
	resolved, is_doctype_level = _resolve_doc("CSKH Contact Attempt", doc)
	if is_doctype_level:
		return None if (_is_leader(user) or _is_staff(user)) else False
	if resolved is None:
		return False
	return None if is_work_item_readable_for_workflow(resolved.get("work_item"), user=user) else False


def attempt_correction_has_permission(doc, ptype: str = "read", user: str | None = None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		effective_ptype = (ptype or "read").lower()
		if effective_ptype in WRITE_PTYPES and not getattr(frappe.flags, "cskh_in_correction_service", False):
			return False
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype not in READ_PTYPES:
		return False
	if ROLE_ADMIN in _roles(user):
		return None
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return False
	resolved, is_doctype_level = _resolve_doc("CSKH Attempt Correction", doc)
	if is_doctype_level:
		return None if (_is_leader(user) or _is_staff(user)) else False
	if resolved is None:
		return False
	return None if is_work_item_readable_for_workflow(resolved.get("work_item"), user=user) else False


P2_PERMISSION_QUERY_CONDITIONS = {
	"CSKH Outcome Code": "crm_cskh.p2_permissions.outcome_code_query_conditions",
	"CSKH Policy": "crm_cskh.p2_permissions.policy_query_conditions",
	"CSKH Contact Attempt": "crm_cskh.p2_permissions.contact_attempt_query_conditions",
	"CSKH Attempt Correction": "crm_cskh.p2_permissions.attempt_correction_query_conditions",
}

P2_HAS_PERMISSION = {
	"CSKH Outcome Code": "crm_cskh.p2_permissions.outcome_code_has_permission",
	"CSKH Policy": "crm_cskh.p2_permissions.policy_has_permission",
	"CSKH Contact Attempt": "crm_cskh.p2_permissions.contact_attempt_has_permission",
	"CSKH Attempt Correction": "crm_cskh.p2_permissions.attempt_correction_has_permission",
}
