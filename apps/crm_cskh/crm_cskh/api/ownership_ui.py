"""P1 API: the work items of the scope, for the manual ownership actions.

P1 §9.4 and §4.4 require the UI to show the customers that are currently
assigned and to let an authorised operator **transfer** or **revoke** an
ownership. The mutations themselves are the frozen contract of T1B
(`crm_cskh.api.ownership.reassign` / `.revoke`); this module only adds the
**read side** the page needs:

* `assigned_list` -- the assigned work items of the caller's scope;
* `assignees` -- the enabled `Sales User` accounts that may receive a work
  item (restricted to `CSKH CRM Admin`, so non-admin roles cannot enumerate
  installation users).

**No permission is widened.** The scope of the list is the scope of the
dashboard (ADR-P1-07): the whole installation for the CRM Admin, the subtree
of the team for a leader, the own work items for a staff member. For the BOD
viewer (`pii = False`), lead PII (`display_name`, `phone`) is omitted. The
mutations stay restricted to the CRM Admin business role inside
`crm_cskh.services.ownership`.
"""

import frappe
from frappe.utils import now_datetime

from crm_cskh.api._ui_params import parse_optional_str, parse_pagination
from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden, NotFound

ROLE_ADMIN = "CSKH CRM Admin"
READ_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN, "CSKH BOD Viewer"}


def _roles(user=None):
	return set(frappe.get_roles(user or frappe.session.user))


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_read(user=None):
	"""The list follows the role grants of `CSKH Work Item` (P1 §8.1)."""
	user = user or frappe.session.user
	if _is_system(user):
		return
	if _roles(user) & READ_ROLES:
		return
	raise Forbidden("You may not read the assigned customers.")


def _can_mutate(user=None):
	"""Only the CRM Admin business role may transfer or revoke an ownership."""
	user = user or frappe.session.user
	if _is_system(user):
		return True
	return ROLE_ADMIN in frappe.get_roles(user)


def _require_admin(user=None):
	"""Only the CRM Admin business role may enumerate all sales assignees."""
	if _can_mutate(user):
		return
	raise Forbidden("Only the CRM Admin may list all sales assignees.")


def _campaign_team_scope(team_scope):
	"""Return `(allowed_users, allowed_nodes)` for a `CRM Sales Hierarchy` node."""
	if not team_scope:
		return None, None
	node = frappe.db.get_value(
		"CRM Sales Hierarchy",
		team_scope,
		["name", "lft", "rgt"],
		as_dict=True,
	)
	if not node:
		return set(), set()
	rows = frappe.db.sql(
		"""select name, user from `tabCRM Sales Hierarchy`
		   where lft >= %s and rgt <= %s""",
		(node.lft, node.rgt),
		as_dict=True,
	)
	users = {row.user for row in rows if row.user}
	nodes = {row.name for row in rows if row.name}
	return users, nodes


def _campaign_ineligible_users(campaign_name, allowed_nodes):
	"""Return users excluded on `campaign_name` by disabled/zero-weight/wrong-team/full-capacity rules."""
	rules = frappe.db.get_all(
		"CSKH Assignment Rule",
		filters={"campaign": campaign_name},
		fields=["user", "team", "weight", "capacity_limit", "enabled", "version"],
	)
	if not rules:
		return set()

	max_version = max((int(row.version or 1) for row in rules), default=1)
	latest_by_user = {}
	enabled_by_user = {}
	for row in rules:
		if not row.user:
			continue
		if int(row.version or 1) == max_version:
			latest_by_user.setdefault(row.user, []).append(row)
		if int(row.enabled or 0):
			enabled_by_user.setdefault(row.user, []).append(row)

	load_rows = frappe.db.sql(
		"""select assigned_to as user, count(*) as total
		   from `tabCSKH Work Item`
		   where campaign = %s
		     and status in ('assigned', 'in_progress', 'on_hold')
		     and assigned_to is not null
		   group by assigned_to""",
		(campaign_name,),
		as_dict=True,
	)
	loads = {row.user: int(row.total or 0) for row in load_rows if row.user}

	ineligible = set()
	for user, user_latest in latest_by_user.items():
		enabled_latest = [row for row in user_latest if int(row.enabled or 0)]
		if not enabled_latest:
			ineligible.add(user)
			continue
		if max((int(row.weight or 0) for row in enabled_latest), default=0) <= 0:
			ineligible.add(user)
			continue
		if allowed_nodes is not None:
			for row in enabled_latest:
				if row.team and row.team not in allowed_nodes:
					ineligible.add(user)
					break

	for user, user_enabled in enabled_by_user.items():
		cap_limit = max((int(row.capacity_limit or 0) for row in user_enabled), default=0)
		if cap_limit > 0 and loads.get(user, 0) >= cap_limit:
			ineligible.add(user)

	return ineligible


@frappe.whitelist(methods=["GET"])
def assigned_list(page=1, page_size=20, campaign=None, owner=None):
	"""The assigned work items of the caller's scope.

	The `owner` parameter can only **narrow** the scope: it is intersected
	with the scope the server computed, never applied on its own (SEC-07).
	"""
	actor = frappe.session.user
	_require_read(actor)

	page_num, limit, offset = parse_pagination(page, page_size)
	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	owner_val = parse_optional_str(owner)

	scope = reporting._scope(actor)
	users = reporting._scope_users(scope)
	allow_pii = scope.get("pii", True)

	clauses = ["wi.status in ('assigned', 'in_progress', 'on_hold')"]
	params = []
	if campaign_val:
		clauses.append("wi.campaign = %s")
		params.append(campaign_val)

	# The scope of the actor: `system` / `all` see everything, a leader sees
	# the subtree of the team, a staff member only the own work items.
	if users is not None:
		if not users:
			return {
				"scope": scope["scope"],
				"items": [],
				"total": 0,
				"page": page_num,
				"page_size": limit,
				"can_manage": _can_mutate(actor),
				"as_of": now_datetime(),
			}
		placeholders = ",".join(["%s"] * len(users))
		clauses.append(f"wi.assigned_to in ({placeholders})")
		params.extend(users)

	if owner_val:
		# The filter narrows the scope, it never widens it.
		clauses.append("wi.assigned_to = %s")
		params.append(owner_val)

	where = " and ".join(clauses)
	total = frappe.db.sql(
		f"select count(*) from `tabCSKH Work Item` wi where {where}",
		tuple(params),
	)[0][0]
	rows = frappe.db.sql(
		"select wi.name as work_item, wi.lead as lead, wi.campaign as campaign, "
		"wi.status as status, wi.assigned_to as assigned_to, wi.assigned_at as assigned_at, "
		"c.title as campaign_title, c.code as campaign_code "
		"from `tabCSKH Work Item` wi "
		"left join `tabCSKH Campaign` c on c.name = wi.campaign "
		f"where {where} order by wi.assigned_at desc limit %s offset %s",
		(*tuple(params), limit, offset),
		as_dict=True,
	)

	items = []
	for row in rows:
		lead = frappe.db.get_value(
			"CRM Lead",
			row.lead,
			["name", "lead_name", "first_name", "mobile_no", "source"],
			as_dict=True,
		)
		if not lead:
			continue
		items.append(
			{
				"work_item": row.work_item,
				"lead": lead.name,
				"display_name": (lead.lead_name or lead.first_name or "") if allow_pii else "",
				"phone": lead.mobile_no if allow_pii else None,
				"campaign": row.campaign,
				"campaign_title": row.campaign_title,
				"campaign_code": row.campaign_code,
				"source": lead.source,
				"status": row.status,
				"work_item_status": row.status,
				"assigned_to": row.assigned_to,
				"assigned_at": row.assigned_at,
			}
		)

	return {
		"scope": scope["scope"],
		"items": items,
		"total": total,
		"page": page_num,
		"page_size": limit,
		"can_manage": _can_mutate(actor),
		"as_of": now_datetime(),
	}


@frappe.whitelist(methods=["GET"])
def assignees(campaign=None):
	"""The enabled `Sales User` accounts that may receive a work item.

	Restricted to `CSKH CRM Admin` (the only role allowed to mutate ownership
	or rules), preventing non-admin roles from enumerating installation users.
	When `campaign` is supplied, applies the campaign `team_scope` subtree and
	assignee rule/capacity eligibility (`L-26`).
	"""
	actor = frappe.session.user
	_require_admin(actor)
	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")

	allowed_users = None
	ineligible_users = set()
	if campaign_val:
		campaign_doc = frappe.db.get_value(
			"CSKH Campaign",
			campaign_val,
			["name", "team_scope"],
			as_dict=True,
		)
		if not campaign_doc:
			raise NotFound("The campaign does not exist.")
		allowed_users, allowed_nodes = _campaign_team_scope(campaign_doc.team_scope)
		ineligible_users = _campaign_ineligible_users(campaign_doc.name, allowed_nodes)

	rows = frappe.get_all(
		"User",
		filters={"enabled": 1},
		fields=["name", "full_name", "email"],
		order_by="full_name asc, name asc",
		limit_page_length=0,
	)
	items = []
	for row in rows:
		if allowed_users is not None and row.name not in allowed_users:
			continue
		if row.name in ineligible_users:
			continue
		if "Sales User" not in frappe.get_roles(row.name):
			continue
		items.append(
			{
				"user": row.name,
				"full_name": row.full_name or row.name,
			}
		)

	return {"items": items, "total": len(items), "as_of": now_datetime()}
