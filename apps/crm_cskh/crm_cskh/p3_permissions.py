"""Row-level permissions & generic-API guards for P3 DocTypes (`docs/p3/authorization-matrix.md`).

Enforces from Day 1:
  * `CSKH Working Calendar`: read-only for authenticated CSKH actors; write restricted to `CSKH CRM Admin`
    via service or `System Manager`.
  * `CSKH Team Membership History`: `Staff` reads self only; `Leader` reads current subtree users only;
    `BOD` has `0` direct table access; `Admin`/`System` reads all; append-only (`0` generic write).
  * `CSKH SLA Obligation`: `Staff` reads ONLY obligations of Work Items currently owned by `self`
    (prevents historical obligations from leaking transferred Lead IDs); `Leader` reads authorized
    team scope; `BOD` has `0` direct table access (uses aggregate APIs only); `0` generic write.
  * `CSKH Dirty Scope`: `Admin`/`System` read-only (`0` access for Staff, Leader, BOD; `0` generic write).
  * `CSKH KPI Run`: `Admin`/`System` reads all; `BOD` reads published aggregate runs; `Leader` reads
    runs requested by self, published aggregate runs, or subtree team runs; `Staff` has `0` direct
    table access (`S17`); `0` generic write.
  * `CSKH KPI Snapshot`: `Admin`/`System` reads all; `BOD` reads published snapshots (`0` PII);
    `Leader` reads published snapshots in subtree (`team` & `employee`); `Staff` reads ONLY published
    `employee == self` snapshots (`S13`); `0` generic write.
  * `CSKH QA Review`: `Admin`/`System` reads all; `BOD` has `0` direct table access; `Leader` reads
    subtree staff reviews; `Staff` reads ONLY `published` reviews of `self` (`S07`); `0` generic write.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p1_permissions import (
	READ_PTYPES,
	ROLE_ADMIN,
	ROLE_BOD,
	WRITE_PTYPES,
	_check_active_db_query_oracles,
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


def _leader_allowed_nodes(user: str) -> set[str]:
	bounds = _team_bounds(user)
	if not bounds:
		return set()
	rows = frappe.db.sql(
		"""
		select name from `tabCRM Sales Hierarchy`
		where lft >= %s and rgt <= %s
		""",
		(bounds[0], bounds[1]),
		pluck=True,
	)
	return set(rows or ())


def _subtree_nodes_sql(user: str) -> str:
	nodes = _leader_allowed_nodes(user)
	if not nodes:
		return "('')"
	return "(" + ", ".join(_escape(n) for n in sorted(nodes)) + ")"


# --- 1. `CSKH Working Calendar` ------------------------------------------------


def working_calendar_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Working Calendar", user)
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
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
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
	_check_active_db_query_oracles("CSKH Team Membership History", user)
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
	_check_active_db_query_oracles("CSKH SLA Obligation", user)
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	# BOD accesses aggregate SLA metrics via reporting APIs only (`0` raw Lead link exposure).
	if ROLE_BOD in _roles(user) and not (_is_leader(user) or _is_staff(user)):
		return "1=0"
	if _is_leader(user):
		nodes_sql = _subtree_nodes_sql(user)
		wi_sql = leader_authorized_work_item_sql(user, wi_alias="wi_sla")
		return (
			f"(`tabCSKH SLA Obligation`.`team_at_obligation` in {nodes_sql} "
			"or (coalesce(`tabCSKH SLA Obligation`.`team_at_obligation`, '') = '' "
			"and `tabCSKH SLA Obligation`.`attribution_status` = 'historical_attribution_unknown')) "
			"and exists (select 1 from `tabCSKH Work Item` wi_sla "
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
		[
			"name",
			"lead",
			"campaign",
			"status",
			"assigned_to",
			"closed_by",
			"last_activity_by",
			"owner_at_close",
		],
		as_dict=True,
	)
	if not wi_row:
		return False
	if _is_leader(user):
		team_at_ob = resolved.get("team_at_obligation") or ""
		attr_stat = resolved.get("attribution_status") or ""
		if team_at_ob:
			if not _node_in_subtree(user, team_at_ob):
				return False
		elif attr_stat != "historical_attribution_unknown":
			return False
		from crm_cskh.p2_permissions import is_work_item_readable_for_workflow

		return is_work_item_readable_for_workflow(wi_row, user=user)
	if _is_staff(user):
		return bool(
			resolved.get("owner_user") == user
			and wi_row.assigned_to == user
			and wi_row.status in OWNED_ACTIVE_WORK_ITEM_STATES
		)
	return False


# --- 4. `CSKH Dirty Scope` -----------------------------------------------------


def dirty_scope_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Dirty Scope", user)
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	return "1=0"


def dirty_scope_has_permission(doc=None, ptype="read", user=None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
		return False
	return ROLE_ADMIN in _roles(user)


# --- 5. `CSKH KPI Run` ---------------------------------------------------------


def kpi_run_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH KPI Run", user)
	roles = _roles(user)
	if _is_system(user) or ROLE_ADMIN in roles:
		return ""
	if ROLE_BOD in roles:
		return (
			"`tabCSKH KPI Run`.`is_latest_published` = 1 "
			"and `tabCSKH KPI Run`.`target_scope_type` in ('all', 'company', 'team', 'campaign')"
		)
	if _is_leader(user):
		nodes_sql = _subtree_nodes_sql(user)
		users_sql = _subtree_users_sql(user)
		return (
			"`tabCSKH KPI Run`.`target_scope_type` in ('team', 'employee') "
			f"and (`tabCSKH KPI Run`.`requested_by` = {_escape(user)} "
			f"or (`tabCSKH KPI Run`.`target_scope_type` = 'team' and `tabCSKH KPI Run`.`target_scope_id` in {nodes_sql}) "
			f"or (`tabCSKH KPI Run`.`target_scope_type` = 'employee' and `tabCSKH KPI Run`.`target_scope_id` in {users_sql}))"
		)
	return "1=0"


def kpi_run_has_permission(doc=None, ptype="read", user=None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
		return False
	roles = _roles(user)
	if ROLE_ADMIN in roles:
		return True
	resolved, is_doctype_level = _resolve_doc("CSKH KPI Run", doc)
	if is_doctype_level:
		return bool(ROLE_BOD in roles or _is_leader(user))
	if not resolved:
		return False
	if ROLE_BOD in roles:
		return bool(
			cint(resolved.get("is_latest_published"))
			and (resolved.get("target_scope_type") or "all") in ("all", "company", "team", "campaign")
		)
	if _is_leader(user):
		t_type = (resolved.get("target_scope_type") or "all").lower()
		if t_type not in ("team", "employee"):
			return False
		if resolved.get("requested_by") == user:
			return True
		t_id = resolved.get("target_scope_id") or ""
		if t_type == "team" and t_id and _node_in_subtree(user, t_id):
			return True
		if t_type == "employee" and t_id and (t_id == user or _in_team(user, t_id)):
			return True
	return False


# --- 6. `CSKH KPI Snapshot` ----------------------------------------------------


def kpi_snapshot_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH KPI Snapshot", user)
	roles = _roles(user)
	if _is_system(user) or ROLE_ADMIN in roles:
		return ""
	if ROLE_BOD in roles:
		return (
			"`tabCSKH KPI Snapshot`.`is_latest_published` = 1 "
			"and `tabCSKH KPI Snapshot`.`scope_type` in ('company', 'team', 'employee', 'campaign')"
		)
	if _is_leader(user):
		nodes_sql = _subtree_nodes_sql(user)
		return (
			"`tabCSKH KPI Snapshot`.`is_latest_published` = 1 "
			f"and ((`tabCSKH KPI Snapshot`.`scope_type` = 'team' and `tabCSKH KPI Snapshot`.`scope_id` in {nodes_sql}) "
			f"or (`tabCSKH KPI Snapshot`.`scope_type` = 'employee_team' and `tabCSKH KPI Snapshot`.`team_filter` in {nodes_sql}))"
		)
	if _is_staff(user):
		return (
			"`tabCSKH KPI Snapshot`.`is_latest_published` = 1 "
			"and `tabCSKH KPI Snapshot`.`scope_type` = 'employee' "
			f"and `tabCSKH KPI Snapshot`.`scope_id` = {_escape(user)}"
		)
	return "1=0"


def kpi_snapshot_has_permission(doc=None, ptype="read", user=None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
		return False
	roles = _roles(user)
	if ROLE_ADMIN in roles:
		return True
	resolved, is_doctype_level = _resolve_doc("CSKH KPI Snapshot", doc)
	if is_doctype_level:
		return bool(ROLE_BOD in roles or _is_leader(user) or _is_staff(user))
	if not resolved or not cint(resolved.get("is_latest_published")):
		return False
	stype = resolved.get("scope_type")
	sid = resolved.get("scope_id") or ""
	tfilter = resolved.get("team_filter") or ""
	if ROLE_BOD in roles:
		return stype in ("company", "team", "employee", "campaign")
	if _is_leader(user):
		if stype == "team":
			return bool(sid and _node_in_subtree(user, sid))
		if stype == "employee_team":
			return bool(tfilter and _node_in_subtree(user, tfilter))
		return False
	if _is_staff(user):
		return bool(stype == "employee" and sid == user)
	return False


# --- 7. `CSKH QA Review` -------------------------------------------------------


def qa_review_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH QA Review", user)
	roles = _roles(user)
	if _is_system(user) or ROLE_ADMIN in roles:
		return ""
	if ROLE_BOD in roles and not (_is_leader(user) or _is_staff(user)):
		return "1=0"
	if _is_leader(user):
		return f"`tabCSKH QA Review`.`reviewed_staff` in {_subtree_users_sql(user)}"
	if _is_staff(user):
		return (
			f"`tabCSKH QA Review`.`reviewed_staff` = {_escape(user)} "
			"and `tabCSKH QA Review`.`status` in ('published', 'approved')"
		)
	return "1=0"


def qa_review_has_permission(doc=None, ptype="read", user=None) -> bool:
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
	resolved, is_doctype_level = _resolve_doc("CSKH QA Review", doc)
	if is_doctype_level:
		return _is_leader(user) or _is_staff(user)
	if not resolved:
		return False
	staff_u = resolved.get("reviewed_staff")
	if _is_leader(user):
		return bool(staff_u and (staff_u == user or _in_team(user, staff_u)))
	if _is_staff(user):
		return bool(staff_u == user and resolved.get("status") in ("published", "approved"))
	return False


# --- 8. `CSKH Export Job` + 3-Stage Live Export Revalidation (`S10`..`S12`) ---


def is_export_job_currently_authorized(job_row: dict, user: str | None = None) -> tuple[bool, str]:
	"""Revalidate `user`'s LIVE roles & `CRM Sales Hierarchy` scope against `job_row`.

	Used at Stage 2 (`jobs/run_export.py` worker) and Stage 3 (`download_export` + `/private/files/...`).
	Returns `(authorized, reason_code)`.
	"""
	from crm_cskh.services.p3_flags import is_p3_reporting_enabled

	user = user or frappe.session.user
	if not user or user == "Guest":
		return False, "FORBIDDEN_SCOPE"
	if not is_p3_reporting_enabled():
		return False, "POLICY_CONFLICT"
	enabled = frappe.db.get_value("User", user, "enabled")
	if enabled is not None and not cint(enabled):
		return False, "FORBIDDEN_SCOPE"

	roles = _roles(user)
	if _is_system(user) or ROLE_ADMIN in roles:
		return True, "OK"

	req_by = job_row.get("requested_by")
	if not req_by or req_by != user:
		return False, "FORBIDDEN_SCOPE"

	report_type = job_row.get("report_type") or ""
	scope_type = job_row.get("scope_type") or "company"
	scope_id = job_row.get("scope_id") or ""
	includes_pii = bool(cint(job_row.get("includes_pii")))

	is_bod = ROLE_BOD in roles
	is_leader = _is_leader(user)
	is_staff = _is_staff(user)

	if not (is_bod or is_leader or is_staff):
		return False, "FORBIDDEN_SCOPE"

	# BOD: aggregate only (`0` PII, no `my_scorecard`)
	if is_bod and not (is_leader or is_staff):
		if includes_pii or report_type == "my_scorecard":
			return False, "FORBIDDEN_SCOPE"
		return True, "OK"

	# Team Leader: must STILL hold `Sales Manager` + active subtree covering `scope_id`
	if is_leader:
		bounds = _team_bounds(user)
		if not bounds:
			return False, "FORBIDDEN_SCOPE"
		if report_type == "my_scorecard":
			ok_self = bool(scope_type == "employee" and scope_id == user)
			return ok_self, ("OK" if ok_self else "FORBIDDEN_SCOPE")
		if scope_type == "team":
			if scope_id and _node_in_subtree(user, scope_id):
				return True, "OK"
			return False, "FORBIDDEN_SCOPE"
		if scope_type == "employee":
			if scope_id and (scope_id == user or _in_team(user, scope_id)):
				return True, "OK"
			return False, "FORBIDDEN_SCOPE"
		if scope_type == "campaign":
			camp_doc = (
				frappe.db.get_value("CSKH Campaign", scope_id, ["name", "team_scope"], as_dict=True)
				if scope_id
				else None
			)
			if camp_doc and (not camp_doc.team_scope or _node_in_subtree(user, camp_doc.team_scope)):
				return True, "OK"
			return False, "FORBIDDEN_SCOPE"
		return False, "FORBIDDEN_SCOPE"

	# Staff (`Sales User` without Leader/BOD/Admin): ONLY `my_scorecard` (or self `employee_quality`) with `0` PII!
	if is_staff:
		if includes_pii:
			return False, "FORBIDDEN_SCOPE"
		if (
			report_type in ("my_scorecard", "employee_quality")
			and scope_type == "employee"
			and scope_id == user
		):
			return True, "OK"
		return False, "FORBIDDEN_SCOPE"

	return False, "FORBIDDEN_SCOPE"


def authorized_export_job_names_for_user(
	user: str | None = None,
	*,
	completed_only: bool = False,
) -> list[str]:
	"""Return `CSKH Export Job` names currently passing LIVE Stage 3 authorization & TTL for `user`."""
	from frappe.utils import get_datetime, now_datetime

	user = user or frappe.session.user
	if not user or user == "Guest" or not frappe.db.table_exists("CSKH Export Job"):
		return []
	now_dt = now_datetime()
	status_clause = "`status` = 'completed'" if completed_only else "`status` not in ('revoked', 'expired')"
	rows = frappe.db.sql(
		f"""
		select name, report_type, scope_type, scope_id, includes_pii, requested_by, status, expires_at
		from `tabCSKH Export Job`
		where requested_by = %s
		  and {status_clause}
		""",
		(user,),
		as_dict=True,
	)
	valid: list[str] = []
	for row in rows or []:
		if completed_only:
			if not row.expires_at or now_dt >= get_datetime(row.expires_at):
				continue
		elif row.expires_at and now_dt >= get_datetime(row.expires_at):
			continue
		ok, _ = is_export_job_currently_authorized(row, user=user)
		if ok:
			valid.append(str(row.name))
	return valid


def is_export_file_downloadable(export_job_name: str, user: str | None = None) -> bool:
	"""Enforce Stage 3 live authorization + `status == 'completed'` + TTL `now < expires_at` for raw `File` routes."""
	from frappe.utils import get_datetime, now_datetime

	user = user or frappe.session.user
	if not export_job_name or not user or user == "Guest":
		return False
	job_row = frappe.db.get_value(
		"CSKH Export Job",
		export_job_name,
		[
			"name",
			"report_type",
			"scope_type",
			"scope_id",
			"includes_pii",
			"requested_by",
			"status",
			"expires_at",
		],
		as_dict=True,
	)
	if not job_row:
		return False
	if job_row.status != "completed":
		return False
	if not job_row.expires_at or now_datetime() >= get_datetime(job_row.expires_at):
		return False
	ok, _ = is_export_job_currently_authorized(job_row, user=user)
	return ok


def export_job_query_conditions(user=None, doctype=None) -> str:
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Export Job", user)
	roles = _roles(user)
	if _is_system(user) or ROLE_ADMIN in roles:
		return ""
	if roles & {ROLE_BOD, "Sales Manager", "Sales User"}:
		valid_ids = authorized_export_job_names_for_user(user, completed_only=False)
		if not valid_ids:
			return "1=0"
		in_sql = "(" + ", ".join(_escape(jid) for jid in valid_ids) + ")"
		return f"`tabCSKH Export Job`.`name` in {in_sql}"
	return "1=0"


def export_job_has_permission(doc=None, ptype="read", user=None) -> bool:
	from frappe.utils import get_datetime, now_datetime

	user = user or frappe.session.user
	if _is_system(user):
		return True
	if ptype in WRITE_PTYPES or ptype not in READ_PTYPES:
		return False
	roles = _roles(user)
	if ROLE_ADMIN in roles:
		return True
	resolved, is_doctype_level = _resolve_doc("CSKH Export Job", doc)
	if is_doctype_level:
		return bool(roles & {ROLE_BOD, "Sales Manager", "Sales User"})
	if not resolved:
		return False
	if resolved.get("status") in ("revoked", "expired"):
		return False
	if resolved.get("expires_at") and now_datetime() >= get_datetime(resolved.get("expires_at")):
		return False
	ok, _ = is_export_job_currently_authorized(resolved, user=user)
	return ok
