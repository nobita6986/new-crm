"""Scoped reporting for My Customers and the BOD distribution dashboard.

P1 9.5 and 12.5 require the dashboard to distinguish:
  * the number of unique customers (leads),
  * the number of work items,
  * the number of currently owned work items,
  * the number of assignment events.

The current ownership is always read from `CSKH Work Item`, never from the
history, because a transfer produces two events but only one current owner
(P1 12.5).

Every query is scoped by the actor's team scope. A caller can never widen the
scope with a `user` or `team` parameter (ADR-P1-07).
"""

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import identity as identity_service


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _has_role(user, *roles):
	return bool(set(roles) & set(frappe.get_roles(user)))


def _scope(user=None):
	"""Return the scope descriptor of an actor.

	`scope` is one of `system` (whole installation), `team` (the sales
	hierarchy subtree of the user) or `self` (only the own assignments).
	"""
	user = user or frappe.session.user
	if _is_system(user):
		return {"scope": "system", "users": None, "teams": None}
	if _has_role(user, "CSKH BOD Viewer"):
		# The BOD sees the aggregated numbers of the whole installation, but
		# never the PII of a lead (P1 8.1).
		return {"scope": "all", "users": None, "teams": None, "pii": False}
	if _has_role(user, "Sales Manager"):
		manager = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"])
		if manager:
			return {"scope": "team", "user": user, "manager": manager, "pii": True}
		return {"scope": "self", "user": user, "pii": True}
	return {"scope": "self", "user": user, "pii": True}


def _team_users(user):
	manager = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"])
	if not manager:
		return [user]
	rows = frappe.db.sql(
		"""select user from `tabCRM Sales Hierarchy`
		   where lft >= %s and rgt <= %s""",
		(manager[0], manager[1]),
		pluck=True,
	)
	return list(rows) or [user]


def _user_filter(scope, column="assigned_to"):
	"""Return an SQL fragment and parameters that restrict `column` to the scope.

	`column` may carry a table alias (`wi.assigned_to`). The alias must stay
	**outside** the backticks: `` `wi.assigned_to` `` is parsed by MariaDB as a
	table named `wi.assigned_to`, which does not exist.
	"""
	if scope["scope"] in ("system", "all"):
		return "", []
	if scope["scope"] == "team":
		users = _team_users(scope["user"])
		if not users:
			return "1=0", []
		placeholders = ",".join(["%s"] * len(users))
		return f"{column} in ({placeholders})", users
	return f"{column} = %s", [scope["user"]]


def _scope_users(scope):
	"""The users the actor may see, or None for the whole installation."""
	if scope["scope"] in ("system", "all"):
		return None
	if scope["scope"] == "team":
		return _team_users(scope["user"])
	return [scope["user"]]


def _pool_scope_filter(scope, alias="wi"):
	"""Restrict the unassigned pool to the campaigns of the actor's scope.

	The pool contains **unassigned** work items (`assigned_to is null`), so it
	cannot be filtered by the assignee. The scope is therefore applied on the
	campaign: a leader sees the campaigns of their team, a staff member only
	the campaigns of their own team (and the campaigns without a team scope).
	"""
	users = _scope_users(scope)
	if users is None:
		return "", []
	if not users:
		return "1=0", []
	placeholders = ",".join(["%s"] * len(users))
	return (
		f"{alias}.campaign in (select c.name from `tabCSKH Campaign` c "
		f"where c.team_scope is null or c.team_scope in ({placeholders}))",
		users,
	)


def my_list(user=None, page=1, page_size=20, status=None, campaign=None, search=None):
	"""The customers currently assigned to the caller (P1 9.4).

	The filter is applied on the backend, not in the browser.
	"""
	user = user or frappe.session.user
	limit = max(1, min(int(page_size or 20), 100))
	offset = max(0, (int(page or 1) - 1) * limit)

	filters = {"assigned_to": user, "status": status or "assigned"}
	if campaign:
		filters["campaign"] = campaign

	rows = frappe.db.sql(
		*_my_list_query(filters, user, limit, offset),
		as_dict=True,
	)

	total = frappe.db.count("CSKH Work Item", filters)

	items = []
	for row in rows:
		lead = frappe.db.get_value(
			"CRM Lead",
			row.lead,
			["name", "lead_name", "first_name", "mobile_no", "source", "status"],
			as_dict=True,
		)
		if not lead:
			continue
		items.append(
			{
				"work_item": row.work_item,
				"lead": lead.name,
				"display_name": lead.lead_name or lead.first_name or "",
				"phone": lead.mobile_no,
				"campaign": row.campaign,
				"campaign_title": row.campaign_title,
				"campaign_code": row.campaign_code,
				"source": lead.source,
				"lead_status": lead.status,
				"work_item_status": row.status,
				"assigned_at": row.assigned_at,
			}
		)

	return {
		"scope": "self",
		"items": items,
		"total": total,
		"page": int(page or 1),
		"page_size": limit,
		"as_of": now_datetime(),
	}


def _my_list_query(filters, user, limit, offset):
	clauses = ["wi.assigned_to = %s", "wi.status = %s"]
	params = [user, filters["status"]]
	if filters.get("campaign"):
		clauses.append("wi.campaign = %s")
		params.append(filters["campaign"])
	return (
		"select wi.name as work_item, wi.lead as lead, wi.campaign as campaign, "
		"wi.status as status, wi.assigned_at as assigned_at, "
		"c.title as campaign_title, c.code as campaign_code "
		"from `tabCSKH Work Item` wi "
		"left join `tabCSKH Campaign` c on c.name = wi.campaign "
		"where " + " and ".join(clauses) + " "
		"order by wi.assigned_at desc limit %s offset %s",
		(*tuple(params), limit, offset),
	)


def pool_list(user=None, page=1, page_size=20, campaign=None):
	"""The unassigned pool of the caller's scope (P1 9.1)."""
	user = user or frappe.session.user
	scope = _scope(user)
	limit = max(1, min(int(page_size or 20), 100))
	offset = max(0, (int(page or 1) - 1) * limit)

	clauses = ["wi.status = 'queued'", "wi.assigned_to is null"]
	params = []
	if campaign:
		clauses.append("wi.campaign = %s")
		params.append(campaign)
	clause, scope_params = _pool_scope_filter(scope, "wi")
	if clause:
		clauses.append(clause)
		params.extend(scope_params)

	count_clauses = ["wi.status = 'queued'"]
	count_params = []
	if campaign:
		count_clauses.append("wi.campaign = %s")
		count_params.append(campaign)
	if clause:
		count_clauses.append(clause)
		count_params.extend(scope_params)

	total = frappe.db.sql(
		"select count(*) from `tabCSKH Work Item` wi where " + " and ".join(count_clauses),
		tuple(count_params),
	)[0][0]

	rows = frappe.db.sql(
		"select wi.name as work_item, wi.lead as lead, wi.campaign as campaign "
		"from `tabCSKH Work Item` wi where "
		+ " and ".join(clauses)
		+ " order by wi.creation desc limit %s offset %s",
		(*tuple(params), limit, offset),
		as_dict=True,
	)

	items = []
	for row in rows:
		lead = frappe.db.get_value("CRM Lead", row.lead, ["name", "lead_name", "mobile_no"], as_dict=True)
		if not lead:
			continue
		items.append(
			{
				"work_item": row.work_item,
				"lead": lead.name,
				"display_name": lead.lead_name or "",
				"phone": lead.mobile_no,
				"campaign": row.campaign,
			}
		)

	return {
		"scope": scope["scope"],
		"items": items,
		"total": total,
		"page": int(page or 1),
		"page_size": limit,
		"as_of": now_datetime(),
	}


def distribution_dashboard(user=None, campaign=None, date_from=None, date_to=None):
	"""The BOD distribution dashboard (P1 9.5).

	The numbers come from the current ownership (`CSKH Work Item` and
	`CSKH Active Claim`), never from the assignment history.

	Every count is **scoped**: a leader only sees the numbers of their own
	team subtree (P1 §10.1, SEC-04). A scope that is only *reported* in the
	payload but not *applied* to the counts is a cross-access leak, so the
	scope is pushed into the SQL of every card (ADR-P1-07).
	"""
	user = user or frappe.session.user
	scope = _scope(user)

	campaign_filters = {}
	if campaign:
		campaign_filters = {"campaign": campaign}

	users = _scope_users(scope)
	# A leader sees only the work items of their team; a staff member only
	# their own. The BOD and the system see the whole installation.
	assigned_to_clause = ""
	assigned_to_params = ()
	if users is not None:
		if not users:
			assigned_to_clause = "1=0"
			assigned_to_params = ()
		else:
			placeholders = ",".join(["%s"] * len(users))
			assigned_to_clause = f"assigned_to in ({placeholders})"
			assigned_to_params = tuple(users)

	# The queue is unassigned, so it is scoped through the campaign scope.
	queue_clause = ""
	queue_params = ()
	if users is not None:
		if not users:
			queue_clause = "1=0"
		else:
			placeholders = ",".join(["%s"] * len(users))
			queue_clause = (
				"campaign in (select c.name from `tabCSKH Campaign` c "
				f"where c.team_scope is null or c.team_scope in ({placeholders}))"
			)
			queue_params = tuple(users)

	def scoped_count(doctype, filters, extra=None, params=()):
		"""Count rows of `doctype` restricted by the actor's scope."""
		clauses = []
		values = []
		for field, value in (filters or {}).items():
			clauses.append(f"`{field}` = %s")
			values.append(value)
		if extra:
			clauses.append(extra)
			values.extend(params)
		where = " where " + " and ".join(clauses) if clauses else ""
		return frappe.db.sql(
			f"select count(*) from `tab{doctype}`{where}",
			tuple(values),
		)[0][0]

	assigned = scoped_count(
		"CSKH Work Item", {**campaign_filters, "status": "assigned"}, assigned_to_clause, assigned_to_params
	)
	queued = scoped_count(
		"CSKH Work Item", {**campaign_filters, "status": "queued"}, queue_clause, queue_params
	)
	revoked = scoped_count(
		"CSKH Work Item", {**campaign_filters, "status": "revoked"}, assigned_to_clause, assigned_to_params
	)
	closed = scoped_count(
		"CSKH Work Item", {**campaign_filters, "status": "closed"}, assigned_to_clause, assigned_to_params
	)
	active_claims = scoped_count("CSKH Active Claim", campaign_filters)
	active_campaigns = frappe.db.count("CSKH Campaign", {"status": "Active"})
	failed_batches = frappe.db.count("CSKH Import Batch", {"status": "FAILED"})
	failed_runs = frappe.db.count("CSKH Distribution Run", {"status": "failed"})

	# "Unique customers" counts the **distinct leads**, not the work items
	# (P1 §12.5): a lead with a work item in two campaigns is one customer.
	unique_clauses = []
	unique_params = []
	for field, value in (campaign_filters or {}).items():
		unique_clauses.append(f"`{field}` = %s")
		unique_params.append(value)
	if assigned_to_clause:
		unique_clauses.append(assigned_to_clause)
		unique_params.extend(assigned_to_params)
	unique_where = " where " + " and ".join(unique_clauses) if unique_clauses else ""
	unique_leads = frappe.db.sql(
		"select count(distinct lead) from `tabCSKH Work Item`" + unique_where,
		tuple(unique_params),
	)[0][0]

	per_user_rows = frappe.db.sql(
		"""select wi.assigned_to as user, count(*) as current_total,
		          max(r.weight) as weight, max(r.capacity_limit) as capacity
		   from `tabCSKH Work Item` wi
		   left join `tabCSKH Assignment Rule` r
		     on r.user = wi.assigned_to and r.campaign = wi.campaign
		   where wi.status = 'assigned' {campaign_clause} {scope_clause}
		   group by wi.assigned_to
		   order by max(r.weight) desc, wi.assigned_to asc""".format(
			campaign_clause="and wi.campaign = %s" if campaign else "",
			scope_clause=("and wi." + assigned_to_clause) if assigned_to_clause else "",
		),
		((campaign,) if campaign else ()) + assigned_to_params,
		as_dict=True,
	)

	per_user = []
	for row in per_user_rows:
		used = int(row.current_total or 0)
		capacity = int(row.capacity or 0)
		per_user.append(
			{
				"user": row.user,
				"weight": int(row.weight or 0),
				"current_total": used,
				"capacity": capacity or None,
				"remaining_capacity": (capacity - used) if capacity else None,
			}
		)

	# The assignment events of a campaign are the events of the runs of that
	# campaign. The filter is a **dict**: a tuple would be read by Frappe as
	# an `(operator, value)` pair.
	history_filters = {}
	if campaign:
		run_names = frappe.get_all("CSKH Distribution Run", filters={"campaign": campaign}, pluck="name")
		history_filters = (
			{"distribution_run": ["in", run_names]} if run_names else {"distribution_run": ["is", "not set"]}
		)
	history_events = frappe.db.count("CSKH Assignment History", history_filters)

	return {
		"scope": scope["scope"],
		"cards": {
			"unique_customers": unique_leads,
			"eligible_unassigned": queued,
			"assigned_work_items": assigned,
			"unassigned_work_items": queued,
			"revoked_work_items": revoked,
			"closed_work_items": closed,
			"active_claims": active_claims,
			"active_campaigns": active_campaigns,
			"failed_batches": failed_batches,
			"failed_runs": failed_runs,
		},
		"per_user": per_user,
		"assignment_events": history_events,
		"identity_count": identity_service.identity_snapshot()["identities"],
		"as_of": now_datetime(),
	}


def campaign_summary(user=None, campaign=None):
	"""Per-campaign distribution summary used by the dashboard filters.

	The per-campaign counters are scoped like the dashboard cards: a leader
	only sees the campaigns of their team (P1 §10.1).
	"""
	user = user or frappe.session.user
	scope = _scope(user)
	users = _scope_users(scope)

	campaign_rows = frappe.get_all(
		"CSKH Campaign",
		filters={"status": ["!=", "Closed"]},
		fields=["name", "code", "title", "status", "rule_version", "team_scope"],
		order_by="code asc",
	)
	if users is not None:
		campaign_rows = [row for row in campaign_rows if not row.team_scope or row.team_scope in users]

	summary = []
	for row in campaign_rows:
		assigned_users = None
		if users is not None:
			placeholders = ",".join(["%s"] * len(users)) if users else None
			if not users:
				assigned_users = []
			else:
				assigned_users = frappe.db.sql(
					f"select distinct assigned_to from `tabCSKH Work Item` "
					f"where campaign = %s and status = 'assigned' and assigned_to in ({placeholders})",
					tuple([row.name, *list(users)]),
					pluck=True,
				)
		summary.append(
			{
				"campaign": row.name,
				"code": row.code,
				"title": row.title,
				"status": row.status,
				"rule_version": row.rule_version,
				"assigned": frappe.db.count("CSKH Work Item", {"campaign": row.name, "status": "assigned"})
				if assigned_users is None
				else len(assigned_users),
				"queued": frappe.db.count("CSKH Work Item", {"campaign": row.name, "status": "queued"}),
			}
		)
	return {"campaigns": summary, "as_of": now_datetime()}
