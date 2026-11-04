"""Row-level permissions & generic-API guards for P3 DocTypes (`docs/p3/authorization-matrix.md`).

Enforces from Day 1:
  * `CSKH Working Calendar`: read-only for authenticated CSKH actors; write restricted to `CSKH CRM Admin`
    via service or `System Manager`.
  * `CSKH Team Membership History`: `Staff` reads self only; `Leader` reads current subtree users only;
    `BOD` has `0` direct table access; `Admin`/`System` reads all; append-only (`0` generic write).
  * `CSKH SLA Obligation`: `Staff` reads ONLY obligations of Work Items currently owned by `self`
    (prevents historical obligations from leaking transferred Lead IDs); `Leader` reads authorized
    team scope; `BOD` has `0` direct table access (uses aggregate APIs only); `0` generic write.
"""

from __future__ import annotations

import frappe

from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p1_permissions import (
	READ_PTYPES,
	ROLE_ADMIN,
	ROLE_BOD,
	WRITE_PTYPES,
	_escape,
	_is_system,
	_node_in_subtree,
	_resolve_doc,
	_roles,
	_team_bounds,
)
from crm_cskh.p2_permissions import (
	_in_team,
	_is_leader,
	_is_staff,
	leader_authorized_work_item_sql,
)


def _subtree_users_sql(user: str) -> str:
	bounds = _team_bounds(user)
	if not bounds:
		return f"({_escape(user)})"
	rows = frappe.db.sql(
		"""
		select distinct user from `tabCRM Sales Hierarchy`
		where lft >= %s and rgt <= %s and user is not null and user != ''
		""",
		(bounds[0], bounds[1]),
		pluck=True,
	)
	users = set(rows or []) | {user}
	return "(" + ", ".join(_escape(u) for u in sorted(users)) + ")"


# --- 1. `CSKH Working Calendar` ------------------------------------------------


def working_calendar_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	roles = _roles(user)
	if roles & {ROLE_BOD, "Sales Manager", "Sales User"}:
		return "`tabCSKH Working Calendar`.`enabled` = 1"
	return "1=0"


def working_calendar_has_permission(doc=None, ptype="read", user=None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES:
		return False
	if ptype not in READ_PTYPES:
		return False
	roles = _roles(user)
	if ROLE_ADMIN in roles:
		return True
	if not (roles & {ROLE_BOD, "Sales Manager", "Sales User"}):
		return False
	resolved, is_doctype_level = _resolve_doc("CSKH Working Calendar", doc)
	if is_doctype_level:
		return True
	if not resolved:
		return False
	return bool(int(resolved.get("enabled") or 0))


# --- 2. `CSKH Team Membership History` -----------------------------------------


def team_membership_history_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	# BOD never browses raw membership rows via generic APIs
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return "1=0"
	if _is_leader(user):
		return f"`tabCSKH Team Membership History`.`user` in {_subtree_users_sql(user)}"
	if _is_staff(user):
		return f"`tabCSKH Team Membership History`.`user` = {_escape(user)}"
	return "1=0"


def team_membership_history_has_permission(doc=None, ptype="read", user=None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
		return False
	roles = _roles(user)
	if ROLE_ADMIN in roles:
		return True
	if ROLE_BOD in roles and not (_is_leader(user) or _is_staff(user)):
		return False
	resolved, is_doctype_level = _resolve_doc("CSKH Team Membership History", doc)
	if is_doctype_level:
		return _is_leader(user) or _is_staff(user)
	if not resolved:
		return False
	row_user = resolved.get("user")
	if _is_leader(user):
		return bool(row_user == user or (row_user and _in_team(user, row_user)))
	if _is_staff(user):
		return bool(row_user == user)
	return False


# --- 3. `CSKH SLA Obligation` --------------------------------------------------


def sla_obligation_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	# BOD accesses aggregate SLA metrics via reporting APIs only (`0` raw Lead link exposure).
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return "1=0"
	if _is_leader(user):
		wi_sql = leader_authorized_work_item_sql(user, wi_alias="wi_sla")
		return (
			"exists (select 1 from `tabCSKH Work Item` wi_sla "
			f"where wi_sla.name = `tabCSKH SLA Obligation`.`work_item` and {wi_sql})"
		)
	if _is_staff(user):
		active_states = ", ".join(_escape(s) for s in OWNED_ACTIVE_WORK_ITEM_STATES)
		return (
			f"`tabCSKH SLA Obligation`.`owner_user` = {_escape(user)} "
			"and exists (select 1 from `tabCSKH Work Item` wi_sla "
			"where wi_sla.name = `tabCSKH SLA Obligation`.`work_item` "
			f"and wi_sla.assigned_to = {_escape(user)} "
			f"and wi_sla.status in ({active_states}))"
		)
	return "1=0"


def sla_obligation_has_permission(doc=None, ptype="read", user=None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
		return False
	roles = _roles(user)
	if ROLE_ADMIN in roles:
		return True
	if ROLE_BOD in roles and not (_is_leader(user) or _is_staff(user)):
		return False
	resolved, is_doctype_level = _resolve_doc("CSKH SLA Obligation", doc)
	if is_doctype_level:
		return _is_leader(user) or _is_staff(user)
	if not resolved:
		return False
	wi_name = resolved.get("work_item")
	if not wi_name:
		return False
	wi_row = frappe.db.get_value(
		"CSKH Work Item",
		wi_name,
		["name", "lead", "campaign", "status", "assigned_to", "closed_by", "owner_at_close"],
		as_dict=True,
	)
	if not wi_row:
		return False
	if _is_leader(user):
		team_at_ob = resolved.get("team_at_obligation")
		if team_at_ob and _node_in_subtree(user, team_at_ob):
			# Also verify lead has not transferred outside leader's current scope
			lead_owner = frappe.db.get_value("CRM Lead", wi_row.lead, "lead_owner") if wi_row.lead else None
			if not lead_owner or lead_owner == user or _in_team(user, lead_owner):
				return True
		from crm_cskh.p2_permissions import is_work_item_readable_for_workflow

		return is_work_item_readable_for_workflow(wi_row, user=user)
	if _is_staff(user):
		return bool(
			resolved.get("owner_user") == user
			and wi_row.assigned_to == user
			and wi_row.status in OWNED_ACTIVE_WORK_ITEM_STATES
		)
	return False
