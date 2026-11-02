"""Row-level permissions and scope rules for the P1 DocTypes (ADR-P1-07).

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
narrow it, never widen it (P1 §10.1, SEC-07).

> **Contract note:** `permission_query_conditions` hooks are called as
> `frappe.call(method, user, doctype=doctype)` and must return a **single SQL
> string** — Frappe does not bind parameters for them. Every user value is
> therefore escaped with `frappe.db.escape` before it is interpolated.
"""

import frappe

ROLE_ADMIN = "CSKH CRM Admin"
ROLE_BOD = "CSKH BOD Viewer"

# `CSKH Identity Index` and `CSKH Active Claim` stay system-only: they hold the
# canonical phone numbers and the ownership-protection mechanism, not business
# data a business role may browse (ADR-P1-07 §3.4).
SYSTEM_ONLY_DOCTYPES = ("CSKH Identity Index", "CSKH Active Claim")


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
	campaign (L-07).

	The assignee is matched against the **user** of the hierarchy node, so the
	condition follows the same subtree as the campaign scope.
	"""
	return (
		f"({_campaign_scope_condition(user, campaign_column)} "
		f"and `{assignee_column}` in (select m.`user` from `tabCRM Sales Hierarchy` m "
		f"where m.`lft` >= (select lft from `tabCRM Sales Hierarchy` where `user` = {_escape(user)}) "
		f"and m.`rgt` <= (select rgt from `tabCRM Sales Hierarchy` where `user` = {_escape(user)})))"
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
	"""Campaign and rule visibility."""
	user = user or frappe.session.user
	if _is_system(user) or ROLE_ADMIN in _roles(user) or ROLE_BOD in _roles(user):
		return ""
	if _is_leader(user):
		# `team_scope` holds the name of a `CRM Sales Hierarchy` node, so the
		# condition is the subtree of that node.
		condition = f"`team_scope` in {_hierarchy_subtree(user)}"
		# A campaign without a team scope is visible to every leader.
		return f"(`team_scope` is null or {condition})"
	return "1=0"


def rule_query_conditions(user=None, **kwargs):
	"""The rules of a campaign follow the campaign scope.

	A rule links a campaign **and** an assignee. Both must be in the scope of
	the actor: a leader of team A never sees the rule of a member of team B,
	not even in a shared campaign (L-07).
	"""
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
# has_permission
# ---------------------------------------------------------------------------


def campaign_has_permission(doc, ptype, user, **kwargs):
	"""Campaigns are read-only for the BOD and the leaders."""
	if _is_system(user):
		return None
	if ptype in ("create", "write", "delete", "submit", "cancel", "share", "export"):
		return ROLE_ADMIN in _roles(user)
	if ptype in ("read", "select", "email", "print"):
		if _admin_or_bod(user) or _is_leader(user):
			return None
	return False


def rule_has_permission(doc, ptype, user, **kwargs):
	"""Only the CRM Admin may change a weight or a capacity."""
	if _is_system(user):
		return None
	if ptype in ("create", "write", "delete", "submit", "cancel", "share", "export"):
		return ROLE_ADMIN in _roles(user)
	if ptype in ("read", "select", "email", "print"):
		if _admin_or_bod(user) or _is_leader(user):
			return None
	return False


def work_item_has_permission(doc, ptype, user, **kwargs):
	"""A staff member may read only the work items he owns."""
	if _is_system(user):
		return None
	if ptype in ("create", "write", "delete", "submit", "cancel", "share", "export"):
		return ROLE_ADMIN in _roles(user)
	if ptype in ("read", "select", "email", "print"):
		if _admin_or_bod(user):
			return None
		if _is_leader(user):
			return doc.get("assigned_to") == user or _in_team(user, doc.get("assigned_to"))
		if _is_staff(user):
			return doc.get("assigned_to") == user
	return False


def assignment_history_has_permission(doc, ptype, user, **kwargs):
	"""The history is append-only for everyone, including the administrator."""
	if _is_system(user):
		return None
	if ptype in ("write", "delete", "submit", "cancel", "share", "export"):
		return False
	if ptype in ("read", "select", "email", "print"):
		if _admin_or_bod(user):
			return None
		if _is_leader(user):
			return _in_team(user, doc.get("to_user"))
		if _is_staff(user):
			return doc.get("to_user") == user
	return False


def import_batch_has_permission(doc, ptype, user, **kwargs):
	"""Only the CRM Admin touches an import batch."""
	if _is_system(user):
		return None
	if ROLE_ADMIN in _roles(user):
		return None
	return False


def distribution_run_has_permission(doc, ptype, user, **kwargs):
	"""Runs are read-only for the BOD and the leaders."""
	if _is_system(user):
		return None
	if ptype in ("create", "write", "delete", "submit", "cancel", "share", "export"):
		return ROLE_ADMIN in _roles(user)
	if ptype in ("read", "select", "email", "print"):
		if _admin_or_bod(user) or _is_leader(user):
			return None
	return False


def system_only_has_permission(doc, ptype, user, **kwargs):
	"""The identity index and the active claim stay system-only."""
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
# Hook wiring (kept explicit so the mapping is reviewable)
# ---------------------------------------------------------------------------

PERMISSION_QUERY_CONDITIONS = {
	"CSKH Campaign": "crm_cskh.p1_permissions.campaign_query_conditions",
	"CSKH Assignment Rule": "crm_cskh.p1_permissions.rule_query_conditions",
	"CSKH Work Item": "crm_cskh.p1_permissions.work_item_query_conditions",
	"CSKH Assignment History": "crm_cskh.p1_permissions.assignment_history_query_conditions",
	"CSKH Import Batch": "crm_cskh.p1_permissions.import_batch_query_conditions",
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
	"CSKH Distribution Run": "crm_cskh.p1_permissions.distribution_run_has_permission",
	"CSKH Identity Index": "crm_cskh.p1_permissions.system_only_has_permission",
	"CSKH Active Claim": "crm_cskh.p1_permissions.system_only_has_permission",
}
