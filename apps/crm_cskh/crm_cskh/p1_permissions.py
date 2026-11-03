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

import json
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
	"""A work item is visible to the leader of its team and to its owner."""
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		return _team_user_condition(user, "assigned_to")
	if _is_staff(user):
		return f"`assigned_to` = {_escape(user)}"
	return "1=0"


def assignment_history_query_conditions(user=None, **kwargs):
	"""The history follows the current owner, not the historical one."""
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		return _team_user_condition(user, "to_user")
	if _is_staff(user):
		return f"`to_user` = {_escape(user)}"
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
		if _is_leader(user):
			return resolved.get("assigned_to") == user or _in_team(user, resolved.get("assigned_to"))
		if _is_staff(user):
			return resolved.get("assigned_to") == user
	return False


def assignment_history_has_permission(doc, ptype="read", user=None, **kwargs):
	"""The history is append-only for everyone, including the administrator."""
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
		if _is_leader(user):
			return resolved.get("to_user") == user or _in_team(user, resolved.get("to_user"))
		if _is_staff(user):
			return resolved.get("to_user") == user
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
# Field-level (`permlevel > 0`) filter protection (`L-29`)
# ---------------------------------------------------------------------------


def _normalize_filter_field(raw_field):
	if not isinstance(raw_field, str):
		return ""
	cleaned = raw_field.strip().replace("`", "")
	if "." in cleaned:
		cleaned = cleaned.rsplit(".", 1)[-1].strip()
	return cleaned


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
			field = _normalize_filter_field(key)
			if field and default_doctype:
				yield default_doctype, field
		return

	if isinstance(parsed, list | tuple):
		if not parsed:
			return
		# Single filter tuple: `[field, op, val]` or `[doctype, field, op, val]`
		if isinstance(parsed[0], str):
			if len(parsed) >= 4 and isinstance(parsed[1], str):
				yield parsed[0].strip(), _normalize_filter_field(parsed[1])
			elif default_doctype:
				yield default_doctype, _normalize_filter_field(parsed[0])
			return
		for entry in parsed:
			yield from _extract_filter_pairs(entry, default_doctype)


def _request_target_doctype():
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	dt = form_dict.get("doctype")
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


def validate_p1_filter_fields(doctype, filters=None, or_filters=None, user=None):
	"""Reject filters on `permlevel = 1` fields when `user` lacks `permlevel = 1` read access."""
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return
	for raw in (filters, or_filters):
		for dt, field in _extract_filter_pairs(raw, doctype):
			protected = PERMLEVEL_PROTECTED_FIELDS.get(dt)
			if protected and field in protected:
				frappe.throw("Not permitted to filter on restricted field.", frappe.PermissionError)


def enforce_p1_filter_field_permissions():
	"""`auth_hooks` callback blocking `permlevel = 1` filter oracles across HTTP endpoints."""
	user = getattr(getattr(frappe.local, "session", None), "user", None) or "Guest"
	if _is_system(user) or ROLE_ADMIN in _roles(user):
		return
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	filters = form_dict.get("filters")
	or_filters = form_dict.get("or_filters")
	if filters is None and or_filters is None:
		return
	target_doctype = _request_target_doctype()
	validate_p1_filter_fields(target_doctype, filters=filters, or_filters=or_filters, user=user)


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
