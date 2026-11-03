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

from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden, InvalidRequest

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


@frappe.whitelist(methods=["GET"])
def assigned_list(page=1, page_size=20, campaign=None, owner=None):
	"""The assigned work items of the caller's scope.

	The `owner` parameter can only **narrow** the scope: it is intersected
	with the scope the server computed, never applied on its own (SEC-07).
	"""
	actor = frappe.session.user
	_require_read(actor)

	limit = max(1, min(int(page_size or 20), 100))
	offset = max(0, (int(page or 1) - 1) * limit)

	scope = reporting._scope(actor)
	users = reporting._scope_users(scope)
	allow_pii = scope.get("pii", True)

	clauses = ["wi.status = 'assigned'"]
	params = []
	if campaign:
		clauses.append("wi.campaign = %s")
		params.append(campaign)

	# The scope of the actor: `system` / `all` see everything, a leader sees
	# the subtree of the team, a staff member only the own work items.
	if users is not None:
		if not users:
			return {
				"scope": scope["scope"],
				"items": [],
				"total": 0,
				"page": int(page or 1),
				"page_size": limit,
				"can_manage": _can_mutate(actor),
				"as_of": now_datetime(),
			}
		placeholders = ",".join(["%s"] * len(users))
		clauses.append(f"wi.assigned_to in ({placeholders})")
		params.extend(users)

	if owner:
		# The filter narrows the scope, it never widens it.
		clauses.append("wi.assigned_to = %s")
		params.append(owner)

	where = " and ".join(clauses)
	total = frappe.db.sql(
		f"select count(*) from `tabCSKH Work Item` wi where {where}",
		tuple(params),
	)[0][0]
	rows = frappe.db.sql(
		"select wi.name as work_item, wi.lead as lead, wi.campaign as campaign, "
		"wi.assigned_to as assigned_to, wi.assigned_at as assigned_at, "
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
				"assigned_to": row.assigned_to,
				"assigned_at": row.assigned_at,
			}
		)

	return {
		"scope": scope["scope"],
		"items": items,
		"total": total,
		"page": int(page or 1),
		"page_size": limit,
		"can_manage": _can_mutate(actor),
		"as_of": now_datetime(),
	}


@frappe.whitelist(methods=["GET"])
def assignees(campaign=None):
	"""The enabled `Sales User` accounts that may receive a work item.

	Restricted to `CSKH CRM Admin` (the only role allowed to mutate ownership
	or rules), preventing non-admin roles from enumerating installation users.
	"""
	actor = frappe.session.user
	_require_admin(actor)
	if campaign and not isinstance(campaign, str):
		raise InvalidRequest("The campaign must be a string.")

	rows = frappe.get_all(
		"User",
		filters={"enabled": 1},
		fields=["name", "full_name", "email"],
		order_by="full_name asc",
		limit_page_length=0,
	)
	items = []
	for row in rows:
		if "Sales User" not in frappe.get_roles(row.name):
			continue
		items.append(
			{
				"user": row.name,
				"full_name": row.full_name or row.name,
			}
		)

	return {"items": items, "total": len(items), "as_of": now_datetime()}
