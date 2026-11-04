"""Row-level permissions and scope rules for the P1 DocTypes (ADR-P1-07, L-29).

Two independent axes decide what a P1 actor may do (P1 §8.1):

1. **Business role** — `CSKH CRM Admin`, `CSKH BOD Viewer`, `Sales Manager`,
   `Sales User`.
2. **Scope** — the sales-hierarchy subtree of the actor.

`CSKH CRM Admin` is a *business* role: it never implies `System Manager` and it
never opens `CRM Lead` / `User` / `Role` / `System Settings` (ADR-005 P0). The
only way it mutates an ownership is through
`crm_cskh.services.ownership`.

`CSKH BOD Viewer` stays read-only and never receives `System Manager`
(P1 §8.1: *"BOD viewer không mặc định là System Manager"*).

The scope is always derived from the actor on the server. A client can only
narrow it, never widen it (P1 §10.1, SEC-07). Both `permission_query_conditions`
(for list queries) and `has_permission` (for direct document reads AND
DocType-level checks) enforce the exact same team boundaries (`L-29`), while
raw distribution/selection JSON fields live at `permlevel = 1` (restricted to
`System Manager` and `CSKH CRM Admin`).

> **Contract note:** `permission_query_conditions` hooks are called as
> `frappe.call(method, user, doctype=doctype)` and must return a **single SQL
> string** — Frappe does not bind parameters for them. Every user value is
> therefore escaped with `frappe.db.escape` before it is interpolated.
"""

import inspect
import json
import re
from urllib.parse import unquote

import frappe

ROLE_ADMIN = "CSKH CRM Admin"
ROLE_BOD = "CSKH BOD Viewer"

# `CSKH Identity Index` and `CSKH Active Claim` stay system-only: they hold the
# canonical phone numbers and the ownership-protection mechanism, not business
# data a business role may browse (ADR-P1-07 §3.4).
SYSTEM_ONLY_DOCTYPES = ("CSKH Identity Index", "CSKH Active Claim")

WRITE_PTYPES = frozenset(
	("create", "write", "delete", "submit", "cancel", "amend", "share", "export", "import")
)
READ_PTYPES = frozenset(("read", "select", "email", "print", "report"))

PERMLEVEL_PROTECTED_FIELDS = {
	"CSKH Distribution Run": frozenset(
		("preview_json", "actual_json", "state_json", "progress", "error", "lease_expires_at")
	),
	"CSKH Campaign": frozenset(("selection_json",)),
	"CSKH Work Item": frozenset(("completion_reason", "reopen_reason")),
	"CSKH Assignment History": frozenset(("private_reason", "payload_hash")),
	"CRM Task": frozenset(("cskh_task_history_json",)),
	"CSKH SLA Obligation": frozenset(
		("waive_reason", "policy_snapshot_json", "calendar_snapshot_json", "audit_events_json")
	),
}


def _roles(user):
	return set(frappe.get_roles(user))


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in _roles(user)


def _escape(value):
	return frappe.db.escape(value, percent=False)


def _team_bounds(user):
	"""Return the `(lft, rgt)` bounds of the user's sales-hierarchy node."""
	row = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"], as_dict=True)
	if not row:
		return None
	return (row.lft, row.rgt)


def _node_in_subtree(user, node_name):
	"""True when `CRM Sales Hierarchy` node `node_name` lies in `user`'s subtree."""
	if not node_name:
		return False
	bounds = _team_bounds(user)
	if not bounds:
		return False
	return bool(
		frappe.db.exists(
			"CRM Sales Hierarchy",
			{"name": node_name, "lft": [">=", bounds[0]], "rgt": ["<=", bounds[1]]},
		)
	)


def _campaign_in_leader_scope(user, campaign_doc):
	"""True when `campaign_doc` is shared (`team_scope` empty) or inside `user`'s subtree."""
	if not campaign_doc:
		return False
	team_scope = campaign_doc.get("team_scope")
	if not team_scope:
		return True
	return _node_in_subtree(user, team_scope)


def _resolve_doc(doctype, doc):
	"""Resolve `doc` into `(doc_like_or_none, is_doctype_level)`.

	* `doc is None` -> `(None, True)` (explicit DocType-level check).
	* `doc` is `str | int` -> loads the document if it exists, otherwise `(None, False)`.
	* `doc` is a `Document` or `dict` -> `(doc, False)`.
	"""
	if doc is None:
		return None, True
	if isinstance(doc, str | int):
		if not frappe.db.exists(doctype, doc):
			return None, False
		return frappe.get_doc(doctype, doc), False
	return doc, False


def _team_user_condition(user, column):
	"""SQL condition restricting `column` to the actor's team subtree."""
	bounds = _team_bounds(user)
	if not bounds:
		return f"`{column}` = {_escape(user)}"
	return (
		f"`{column}` in (select `member`.`user` from `tabCRM Sales Hierarchy` `member` "
		f"where `member`.`lft` >= {_escape(bounds[0])} and `member`.`rgt` <= {_escape(bounds[1])})"
	)


def _hierarchy_subtree(user):
	"""SQL subquery selecting the hierarchy nodes of the user's subtree."""
	bounds = _team_bounds(user)
	if not bounds:
		return f"(select `name` from `tabCRM Sales Hierarchy` where `user` = {_escape(user)})"
	return (
		f"(select `name` from `tabCRM Sales Hierarchy` "
		f"where `lft` >= {_escape(bounds[0])} and `rgt` <= {_escape(bounds[1])})"
	)


def _campaign_scope_condition(user, column="campaign"):
	"""SQL condition restricting a campaign link to the actor's team scope.

	The `column` holds the **name of a `CSKH Campaign`**, not a hierarchy node
	name. The campaign carries the team scope in its own `team_scope` field
	(the name of a `CRM Sales Hierarchy` node), so the condition has to go
	through the campaign — comparing the campaign name with a hierarchy node
	name never matches (L-07).

	The subtree is resolved from the **nested set** (`lft` / `rgt`), so the
	condition covers the whole team subtree and not only the direct reports.
	"""
	return (
		f"`{column}` in (select c.name from `tabCSKH Campaign` c "
		f"where c.team_scope is null or c.team_scope in {_hierarchy_subtree(user)})"
	)


def _campaign_and_assignee_scope_condition(user, campaign_column="campaign", assignee_column="user"):
	"""SQL condition for a row that links a campaign **and** an assignee.

	A shared campaign (`team_scope is null`) is visible to every leader, but
	the **assignee** of a rule still belongs to a team: a leader of team A
	must not see the rule of a member of team B, not even in a shared
	campaign (L-07, L-29).
	"""
	subtree = _hierarchy_subtree(user)
	return (
		f"({_campaign_scope_condition(user, campaign_column)} "
		f"and {_team_user_condition(user, assignee_column)} "
		f"and (`team` is null or `team` = '' or `team` in {subtree}))"
	)


def _admin_or_bod(user):
	return bool({ROLE_ADMIN, ROLE_BOD} & _roles(user))


def _is_leader(user):
	return "Sales Manager" in _roles(user)


def _is_staff(user):
	return "Sales User" in _roles(user)


# ---------------------------------------------------------------------------
# permission_query_conditions
# ---------------------------------------------------------------------------


def campaign_query_conditions(user=None, **kwargs):
	"""Campaign visibility in list queries."""
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Campaign", user)
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		condition = f"`team_scope` in {_hierarchy_subtree(user)}"
		return f"(`team_scope` is null or {condition})"
	return "1=0"


def rule_query_conditions(user=None, **kwargs):
	"""The rules of a campaign follow both campaign scope and assignee team scope."""
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		return _campaign_and_assignee_scope_condition(user, "campaign", "user")
	return "1=0"


def work_item_query_conditions(user=None, **kwargs):
	"""A work item is visible to the leader of its team and to its authorized owner."""
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Work Item", user)
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		from crm_cskh.p2_permissions import leader_authorized_work_item_sql

		return leader_authorized_work_item_sql(user, "tabCSKH Work Item")
	if _is_staff(user):
		from crm_cskh.p2_permissions import staff_authorized_work_item_sql

		return staff_authorized_work_item_sql(user, "tabCSKH Work Item")
	return "1=0"


def assignment_history_query_conditions(user=None, **kwargs):
	"""The history follows the current owner/scope of the work item, not historical `to_user` (`SEC2-04`)."""
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Assignment History", user)
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		from crm_cskh.p2_permissions import leader_authorized_work_item_sql

		wi_pred = leader_authorized_work_item_sql(user, "wi_ah")
		return f"`work_item` in (select `wi_ah`.`name` from `tabCSKH Work Item` `wi_ah` where {wi_pred})"
	if _is_staff(user):
		from crm_cskh.p2_permissions import staff_authorized_work_item_sql

		wi_pred = staff_authorized_work_item_sql(user, "wi_ah")
		return f"`work_item` in (select `wi_ah`.`name` from `tabCSKH Work Item` `wi_ah` where {wi_pred})"
	return "1=0"


def import_batch_query_conditions(user=None, **kwargs):
	"""The raw import files and the error rows stay with the CRM Admin."""
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return ""
	return "1=0"


def distribution_run_query_conditions(user=None, **kwargs):
	"""Runs are visible to the admin, the BOD and the leader of the team."""
	user = user or frappe.session.user
	_check_active_db_query_oracles("CSKH Distribution Run", user)
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		return _campaign_scope_condition(user, "campaign")
	return "1=0"


def system_only_query_conditions(user=None, **kwargs):
	"""`CSKH Identity Index` and `CSKH Active Claim` are never exposed."""
	user = user or frappe.session.user
	return "" if _is_system(user) else "1=0"


# ---------------------------------------------------------------------------
# has_permission (DocType-level AND Document-level)
# ---------------------------------------------------------------------------


def campaign_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Campaigns are read-only for BOD and scoped leaders; foreign-team docs are denied (`L-29`)."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES:
		if _admin_or_bod(user):
			return None
		if _is_leader(user):
			resolved, is_doctype_level = _resolve_doc("CSKH Campaign", doc)
			if is_doctype_level:
				return None
			if resolved is None:
				return False
			return None if _campaign_in_leader_scope(user, resolved) else False
	return False


def rule_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Rules are read-only for BOD and scoped leaders; foreign-team rules are denied (`L-29`)."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES:
		if _admin_or_bod(user):
			return None
		if _is_leader(user):
			resolved, is_doctype_level = _resolve_doc("CSKH Assignment Rule", doc)
			if is_doctype_level:
				return None
			if resolved is None:
				return False
			campaign_name = resolved.get("campaign")
			if not campaign_name:
				return False
			campaign_doc = frappe.db.get_value(
				"CSKH Campaign", campaign_name, ["name", "team_scope"], as_dict=True
			)
			if not _campaign_in_leader_scope(user, campaign_doc):
				return False
			assignee = resolved.get("user")
			if not (assignee == user or _in_team(user, assignee)):
				return False
			rule_team = resolved.get("team")
			if rule_team and not _node_in_subtree(user, rule_team):
				return False
			return None
	return False


def work_item_has_permission(doc, ptype="read", user=None, **kwargs):
	"""A staff member may read only the work items they own; a leader only their team."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES:
		if _admin_or_bod(user):
			return None
		resolved, is_doctype_level = _resolve_doc("CSKH Work Item", doc)
		if is_doctype_level:
			return None if (_is_leader(user) or _is_staff(user)) else False
		if resolved is None:
			return False
		if _is_leader(user) or _is_staff(user):
			from crm_cskh.p2_permissions import is_work_item_readable_for_workflow

			return None if is_work_item_readable_for_workflow(resolved, user=user) else False
	return False


def assignment_history_has_permission(doc, ptype="read", user=None, **kwargs):
	"""The history is append-only and follows current work-item authorization (`SEC2-04`)."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES:
		if _admin_or_bod(user):
			return None
		resolved, is_doctype_level = _resolve_doc("CSKH Assignment History", doc)
		if is_doctype_level:
			return None if (_is_leader(user) or _is_staff(user)) else False
		if resolved is None:
			return False
		work_item_name = resolved.get("work_item")
		if not work_item_name:
			return False
		return work_item_has_permission(work_item_name, ptype="read", user=user)
	return False


def import_batch_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Only the CRM Admin touches an import batch."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES and ROLE_ADMIN in _roles(user):
		return None
	return False


def import_batch_lead_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Import batch lead links are read-only for the CRM Admin and closed to other business roles."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES and ROLE_ADMIN in _roles(user):
		return None
	return False


def distribution_run_has_permission(doc, ptype="read", user=None, **kwargs):
	"""Runs are read-only for BOD and scoped leaders; foreign-team runs are denied (`L-29`)."""
	user = user or frappe.session.user
	if _is_system(user):
		return None
	effective_ptype = (ptype or "read").lower()
	if effective_ptype in WRITE_PTYPES:
		return False
	if effective_ptype in READ_PTYPES:
		if _admin_or_bod(user):
			return None
		if _is_leader(user):
			resolved, is_doctype_level = _resolve_doc("CSKH Distribution Run", doc)
			if is_doctype_level:
				return None
			if resolved is None:
				return False
			campaign_name = resolved.get("campaign")
			if not campaign_name:
				return False
			campaign_doc = frappe.db.get_value(
				"CSKH Campaign", campaign_name, ["name", "team_scope"], as_dict=True
			)
			return None if _campaign_in_leader_scope(user, campaign_doc) else False
	return False


def system_only_has_permission(doc, ptype="read", user=None, **kwargs):
	"""The identity index and the active claim stay system-only."""
	user = user or frappe.session.user
	return None if _is_system(user) else False


def _in_team(manager, member):
	"""True when `member` belongs to the team subtree of `manager`."""
	if not member:
		return False
	bounds = _team_bounds(manager)
	if not bounds:
		return member == manager
	return bool(
		frappe.db.exists(
			"CRM Sales Hierarchy",
			{"user": member, "lft": [">=", bounds[0]], "rgt": ["<=", bounds[1]]},
		)
	)


# ---------------------------------------------------------------------------
# Field-level (`permlevel > 0`) filter/search/sort protection (`L-29`, `F-P2-01`)
# ---------------------------------------------------------------------------

_QUALIFIED_TAB_COL_RE = re.compile(r"`?tab([^`.]+)`?\s*\.\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
_BARE_IDENT_RE = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]*)`?")
_SQL_CLAUSE_KEYWORDS = frozenset(("asc", "desc", "nulls", "first", "last", "distinct"))


def can_read_protected_field(doctype, field, user=None) -> bool:
	"""True when `user` may read/filter/sort on `permlevel = 1` `field` of `doctype`."""
	user = user or frappe.session.user
	if _is_system(user):
		return True
	protected = PERMLEVEL_PROTECTED_FIELDS.get(doctype)
	if not protected or field not in protected:
		return True
	roles = _roles(user)
	if doctype in ("CSKH Work Item", "CSKH Assignment History", "CRM Task"):
		return bool({ROLE_ADMIN, "Sales Manager", "Sales User"} & roles)
	if doctype in ("CSKH Distribution Run", "CSKH Campaign"):
		return ROLE_ADMIN in roles
	return False


def _normalize_filter_field(raw_field):
	if not isinstance(raw_field, str):
		return ""
	cleaned = raw_field.strip().replace("`", "")
	if "." in cleaned:
		cleaned = cleaned.rsplit(".", 1)[-1].strip()
	return cleaned


def _extract_qualified_doctype_and_field(raw_field, default_doctype):
	if not isinstance(raw_field, str):
		return default_doctype, ""
	match = _QUALIFIED_TAB_COL_RE.search(raw_field)
	if match:
		return match.group(1).strip(), match.group(2).strip()
	return default_doctype, _normalize_filter_field(raw_field)


def _extract_filter_pairs(raw_filters, default_doctype):
	"""Yield `(doctype, fieldname)` referenced inside Frappe `filters` / `or_filters`."""
	if raw_filters is None or raw_filters == "":
		return
	parsed = raw_filters
	if isinstance(parsed, str):
		try:
			parsed = json.loads(parsed)
		except (TypeError, ValueError):
			return

	if isinstance(parsed, dict):
		for key in parsed:
			dt, field = _extract_qualified_doctype_and_field(key, default_doctype)
			if field and dt:
				yield dt, field
		return

	if isinstance(parsed, list | tuple):
		if not parsed:
			return
		# Single filter tuple: `[field, op, val]` or `[doctype, field, op, val]`
		if isinstance(parsed[0], str):
			if len(parsed) >= 4 and isinstance(parsed[1], str):
				yield parsed[0].strip(), _normalize_filter_field(parsed[1])
			else:
				dt, field = _extract_qualified_doctype_and_field(parsed[0], default_doctype)
				if field and dt:
					yield dt, field
			return
		for entry in parsed:
			yield from _extract_filter_pairs(entry, default_doctype)


def _extract_clause_fields(raw_clause, default_doctype):
	"""Yield `(doctype, fieldname)` referenced in `searchfield`, `filter_fields`, `order_by`, `group_by`."""
	if raw_clause is None or raw_clause == "":
		return
	if isinstance(raw_clause, dict):
		for key in raw_clause:
			yield from _extract_clause_fields(key, default_doctype)
		return
	if isinstance(raw_clause, list | tuple | set):
		for item in raw_clause:
			yield from _extract_clause_fields(item, default_doctype)
		return
	if not isinstance(raw_clause, str):
		return
	stripped = raw_clause.strip()
	if not stripped:
		return
	if stripped[0] in "[{":
		try:
			parsed = json.loads(stripped)
		except (TypeError, ValueError):
			parsed = None
		if parsed is not None and not isinstance(parsed, str):
			yield from _extract_clause_fields(parsed, default_doctype)
			return

	for match in _QUALIFIED_TAB_COL_RE.finditer(stripped):
		yield match.group(1).strip(), match.group(2).strip()

	unqualified = _QUALIFIED_TAB_COL_RE.sub(" ", stripped)
	if default_doctype:
		for token_match in _BARE_IDENT_RE.finditer(unqualified):
			ident = token_match.group(1)
			if ident.lower() not in _SQL_CLAUSE_KEYWORDS:
				yield default_doctype, ident


def _merged_request_args() -> dict:
	merged: dict = {}
	request = getattr(frappe.local, "request", None)
	if request is not None:
		try:
			json_body = request.get_json(silent=True)
		except Exception:
			json_body = None
		if isinstance(json_body, dict):
			merged.update(json_body)
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	if isinstance(form_dict, dict):
		merged.update(form_dict)
	return merged


def _request_target_doctype(args: dict | None = None):
	args = args if args is not None else _merged_request_args()
	dt = args.get("doctype")
	if isinstance(dt, str) and dt.strip():
		return dt.strip()
	request = getattr(frappe.local, "request", None)
	path = getattr(request, "path", "") or ""
	for prefix in ("/api/resource/", "/api/v2/document/"):
		if path.startswith(prefix):
			rest = path[len(prefix) :].strip("/")
			if rest:
				return unquote(rest.split("/", 1)[0])
	return None


def validate_p1_filter_fields(
	doctype,
	filters=None,
	or_filters=None,
	user=None,
	*,
	searchfield=None,
	filter_fields=None,
	order_by=None,
	group_by=None,
	explicit_fields=None,
):
	"""Reject filters/search/sort on `permlevel = 1` fields when `user` lacks `permlevel = 1` read."""
	user = user or frappe.session.user
	if _is_system(user):
		return
	for raw in (filters, or_filters):
		for dt, field in _extract_filter_pairs(raw, doctype):
			if not can_read_protected_field(dt, field, user=user):
				frappe.throw("Not permitted to filter on restricted field.", frappe.PermissionError)
	for clause in (searchfield, filter_fields, order_by, group_by, explicit_fields):
		for dt, field in _extract_clause_fields(clause, doctype):
			if not can_read_protected_field(dt, field, user=user):
				frappe.throw("Not permitted to query or sort on restricted field.", frappe.PermissionError)


def _check_active_db_query_oracles(doctype: str, user: str) -> None:
	"""Enforce `permlevel = 1` filter/search/sort blocks inside `DatabaseQuery` (both HTTP & Python)."""
	if _is_system(user):
		return
	frame = inspect.currentframe()
	try:
		cur = frame.f_back if frame is not None else None
		steps = 0
		while cur is not None and steps < 8:
			obj = cur.f_locals.get("self")
			if (
				obj is not None
				and obj.__class__.__name__ == "DatabaseQuery"
				and getattr(obj, "doctype", None) == doctype
				and not getattr(obj, "ignore_permissions", False)
			):
				raw_fields = getattr(obj, "fields", None)
				func_fields = None
				if isinstance(raw_fields, list | tuple | set):
					func_fields = [f for f in raw_fields if isinstance(f, str) and "(" in f]
				elif isinstance(raw_fields, str) and "(" in raw_fields:
					func_fields = raw_fields
				validate_p1_filter_fields(
					doctype,
					filters=getattr(obj, "filters", None),
					or_filters=getattr(obj, "or_filters", None),
					user=user,
					searchfield=getattr(obj, "searchfield", None),
					order_by=getattr(obj, "order_by", None),
					group_by=getattr(obj, "group_by", None),
					explicit_fields=func_fields,
				)
				break
			cur = cur.f_back
			steps += 1
	finally:
		del frame


def enforce_p1_filter_field_permissions():
	"""`auth_hooks` callback blocking `permlevel = 1` filter/search/sort oracles across HTTP endpoints."""
	user = getattr(getattr(frappe.local, "session", None), "user", None) or "Guest"
	if _is_system(user):
		return
	args = _merged_request_args()
	filters = args.get("filters")
	or_filters = args.get("or_filters")
	searchfield = args.get("searchfield")
	filter_fields = args.get("filter_fields")
	order_by = args.get("order_by")
	group_by = args.get("group_by")
	request = getattr(frappe.local, "request", None)
	path = getattr(request, "path", "") or ""
	raw_explicit = args.get("fieldname") if "get_value" in path else args.get("fields")
	if isinstance(raw_explicit, str) and raw_explicit.strip().startswith("["):
		try:
			raw_explicit = frappe.parse_json(raw_explicit)
		except Exception:
			pass
	explicit_fields = None
	if isinstance(raw_explicit, str) and "(" in raw_explicit:
		explicit_fields = raw_explicit
	elif isinstance(raw_explicit, list | tuple | set):
		func_items = [f for f in raw_explicit if isinstance(f, str) and "(" in f]
		if func_items:
			explicit_fields = func_items
	if all(
		v is None
		for v in (filters, or_filters, searchfield, filter_fields, order_by, group_by, explicit_fields)
	):
		return
	target_doctype = _request_target_doctype(args)
	validate_p1_filter_fields(
		target_doctype,
		filters=filters,
		or_filters=or_filters,
		user=user,
		searchfield=searchfield,
		filter_fields=filter_fields,
		order_by=order_by,
		group_by=group_by,
		explicit_fields=explicit_fields,
	)


# ---------------------------------------------------------------------------
# Hook wiring (kept explicit so the mapping is reviewable)
# ---------------------------------------------------------------------------

PERMISSION_QUERY_CONDITIONS = {
	"CSKH Campaign": "crm_cskh.p1_permissions.campaign_query_conditions",
	"CSKH Assignment Rule": "crm_cskh.p1_permissions.rule_query_conditions",
	"CSKH Work Item": "crm_cskh.p1_permissions.work_item_query_conditions",
	"CSKH Assignment History": "crm_cskh.p1_permissions.assignment_history_query_conditions",
	"CSKH Import Batch": "crm_cskh.p1_permissions.import_batch_query_conditions",
	"CSKH Import Batch Lead": "crm_cskh.p1_permissions.import_batch_query_conditions",
	"CSKH Distribution Run": "crm_cskh.p1_permissions.distribution_run_query_conditions",
	"CSKH Identity Index": "crm_cskh.p1_permissions.system_only_query_conditions",
	"CSKH Active Claim": "crm_cskh.p1_permissions.system_only_query_conditions",
}

HAS_PERMISSION = {
	"CSKH Campaign": "crm_cskh.p1_permissions.campaign_has_permission",
	"CSKH Assignment Rule": "crm_cskh.p1_permissions.rule_has_permission",
	"CSKH Work Item": "crm_cskh.p1_permissions.work_item_has_permission",
	"CSKH Assignment History": "crm_cskh.p1_permissions.assignment_history_has_permission",
	"CSKH Import Batch": "crm_cskh.p1_permissions.import_batch_has_permission",
	"CSKH Import Batch Lead": "crm_cskh.p1_permissions.import_batch_lead_has_permission",
	"CSKH Distribution Run": "crm_cskh.p1_permissions.distribution_run_has_permission",
	"CSKH Identity Index": "crm_cskh.p1_permissions.system_only_has_permission",
	"CSKH Active Claim": "crm_cskh.p1_permissions.system_only_has_permission",
}
