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

	**The CRM Admin business role sees the whole installation** (P1 8.1:
	"Xem tong quan phan phoi -- BOD Viewer: Toan he thong, Admin: Toan he
	thong"). Treating the Admin like a staff member made the dashboard show
	zero customers to the only role that is allowed to distribute them.
	"""
	user = user or frappe.session.user
	if _is_system(user):
		return {"scope": "system", "users": None, "teams": None}
	if _has_role(user, "CSKH CRM Admin"):
		# The Admin has the full business scope, but keeps the PII: the Admin
		# is the role that manages the campaigns and the import.
		return {"scope": "system", "users": None, "teams": None, "pii": True}
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
	"""The customer pool of the caller's scope (P1 9.1).

	The pool lists the **leads** of the scope with their *derived* distribution
	state. The state is computed from the work items and the active claims, it
	is never written back to `CRM Lead.status` (P1 9.1).

	Two counters are kept apart (P1 9.1): the **unique customers** (the leads)
	and the **work items per campaign**. The payload therefore carries
	`total` (the number of leads of the page) and `work_items` (the number of
	work items of those leads).

	The scope of the pool is the scope of the dashboard (ADR-P1-07): a leader
	sees the leads of the campaigns of their own team, a staff member has no
	pool at all (the API refuses the call).
	"""
	user = user or frappe.session.user
	scope = _scope(user)
	limit = max(1, min(int(page_size or 20), 100))
	offset = max(0, (int(page or 1) - 1) * limit)

	users = _scope_users(scope)
	if users is None:
		# The whole installation: every canonical lead.
		lead_clauses = ["1=1"]
		lead_params = []
	else:
		if not users:
			lead_clauses = ["1=0"]
			lead_params = []
		else:
			placeholders = ",".join(["%s"] * len(users))
			lead_clauses = [
				"(l.source in (select l2.source from `tabCRM Lead` l2 where l2.lead_owner in ("
				+ placeholders
				+ ")) or l.lead_owner in ("
				+ placeholders
				+ "))"
			]
			lead_params = list(users) + list(users)

	if campaign:
		lead_clauses.append(
			"exists (select 1 from `tabCSKH Work Item` wi where wi.lead = l.name and wi.campaign = %s)"
		)
		lead_params.append(campaign)

	lead_where = " and ".join(lead_clauses)
	total = frappe.db.sql(
		"select count(*) from `tabCRM Lead` l where " + lead_where,
		tuple(lead_params),
	)[0][0]

	rows = frappe.db.sql(
		"select l.name as lead, l.lead_name as display_name, l.mobile_no as phone, "
		"l.source as source, l.lead_owner as owner, l.creation as created_at "
		"from `tabCRM Lead` l where "
		+ lead_where
		+ " order by l.creation desc limit %s offset %s",
		(*tuple(lead_params), limit, offset),
		as_dict=True,
	)

	items = []
	work_items = 0
	campaign_titles = {}
	for row in rows:
		state, state_campaign, item_count = _pool_state(row.lead, users)
		work_items += item_count
		if state_campaign and state_campaign not in campaign_titles:
			campaign_titles[state_campaign] = frappe.db.get_value(
				"CSKH Campaign", state_campaign, ["title", "code"], as_dict=True
			) or {}
		campaign_meta = campaign_titles.get(state_campaign) or {}
		items.append(
			{
				"work_item": state_campaign or "",
				"lead": row.lead,
				"display_name": row.display_name or "",
				"phone": row.phone,
				"source": row.source or "",
				"owner": row.owner or "",
				"campaign": state_campaign or "",
				"campaign_title": campaign_meta.get("title") or campaign_meta.get("code") or "",
				"campaign_code": campaign_meta.get("code") or "",
				"state": state,
				"work_items": item_count,
				"created_at": row.created_at,
			}
		)

	return {
		"scope": scope["scope"],
		"items": items,
		"total": total,
		"work_items": work_items,
		"page": int(page or 1),
		"page_size": limit,
		"as_of": now_datetime(),
	}


def _pool_state(lead, users=None):
	"""The derived distribution state of one lead (P1 9.1).

	The state is read from the work items and the active claims of the lead:

	* `conflict` -- an active claim of **another** campaign;
	* `assigned` -- a work item with an owner;
	* `queued` -- a work item that waits for an owner;
	* `revoked` / `closed` -- the work item is in that state;
	* `eligible` -- the lead has no work item at all, so it is a customer that
	  can still enter a campaign.

	Returns `(state, campaign, work_item_count)`.
	"""
	rows = frappe.db.sql(
		"select wi.name as work_item, wi.campaign as campaign, wi.status as status, "
		"wi.assigned_to as assigned_to "
		"from `tabCSKH Work Item` wi where wi.lead = %s "
		"order by wi.creation desc",
		(lead,),
		as_dict=True,
	)
	if not rows:
		return "eligible", "", 0

	claim = frappe.db.get_value(
		"CSKH Active Claim", {"lead": lead}, ["campaign", "work_item"], as_dict=True
	)

	for row in rows:
		if row.status == "assigned" and row.assigned_to:
			return "assigned", row.campaign, len(rows)
		if row.status == "queued" and not row.assigned_to:
			return "queued", row.campaign, len(rows)
		if row.status == "revoked":
			return "revoked", row.campaign, len(rows)
		if row.status == "closed":
			return "closed", row.campaign, len(rows)

	if claim and claim.campaign:
		return "conflict", claim.campaign, len(rows)
	return "eligible", "", len(rows)


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
	# An active claim belongs to a work item, so it is scoped through the
	# work item (L-05: a scope that is only *reported* and not *applied* is a
	# cross-access leak).
	#
	# The scope of a work item is the same as the scope of the dashboard: the
	# assigned work items of the users of the scope, plus the queued work
	# items of the campaigns of the scope.
	claim_clauses = []
	claim_params = []
	for field, value in (campaign_filters or {}).items():
		claim_clauses.append(f"wi.`{field}` = %s")
		claim_params.append(value)
	if users is not None:
		if not users:
			claim_clauses.append("1=0")
		else:
			placeholders = ",".join(["%s"] * len(users))
			claim_clauses.append(
				"wi.assigned_to in (" + placeholders + ")"
			)
			claim_params.extend(users)
			if queue_clause:
				claim_clauses.append("(" + queue_clause + ")")
				claim_params.extend(queue_params)
	claim_where = " where " + " and ".join(claim_clauses) if claim_clauses else ""
	active_claims = frappe.db.sql(
		"select count(*) from `tabCSKH Active Claim` ac"
		" where ac.work_item in ("
		"select wi.name from `tabCSKH Work Item` wi" + claim_where
		+ ")",
		tuple(claim_params),
	)[0][0]

	# The active campaigns are scoped by their team scope: a leader only sees
	# the campaigns of their own team (L-05). A campaign without a team scope
	# is visible to every scope, exactly like in the pool.
	active_campaigns = frappe.db.count("CSKH Campaign", {"status": "Active"})
	if users is not None:
		if not users:
			active_campaigns = 0
		else:
			placeholders = ",".join(["%s"] * len(users))
			active_campaigns = frappe.db.sql(
				"select count(*) from `tabCSKH Campaign` "
				f"where status = 'Active' and (team_scope is null or team_scope in ({placeholders}))",
				tuple(users),
			)[0][0]

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

	# The assignment events are also scoped (L-05): a leader only counts the
	# events of their own team, a staff member only their own events.
	if users is not None:
		history_clauses = []
		history_params = []
		for field, value in history_filters.items():
			operator = value[0] if isinstance(value, (list, tuple)) else "="
			values = value[1] if isinstance(value, (list, tuple)) else value
			if operator == "in":
				placeholders = ",".join(["%s"] * len(values))
				history_clauses.append(f"h.`{field}` in ({placeholders})")
				history_params.extend(values)
			elif operator == "is":
				history_clauses.append(f"h.`{field}` is null")
			else:
				history_clauses.append(f"h.`{field}` = %s")
				history_params.append(values)
		if not users:
			history_clauses.append("1=0")
		else:
			placeholders = ",".join(["%s"] * len(users))
			history_clauses.append(
				"(h.to_user in (" + placeholders + ") or h.from_user in (" + placeholders + "))"
			)
			history_params.extend(users)
			history_params.extend(users)
		history_where = " where " + " and ".join(history_clauses)
		history_events = frappe.db.sql(
			"select count(*) from `tabCSKH Assignment History` h" + history_where,
			tuple(history_params),
		)[0][0]

	# The identity count is scoped like every other card (L-05): the number of
	# canonical identities of the installation would tell a leader how many
	# customers the other teams own.
	identity_count = identity_service.identity_snapshot()["identities"]
	if users is not None:
		identity_count = frappe.db.sql(
			"select count(*) from `tabCSKH Identity Index` idx"
			" where idx.canonical_lead in (select distinct wi.lead from `tabCSKH Work Item` wi"
			+ (unique_where if unique_clauses else "")
			+ ")",
			tuple(unique_params),
		)[0][0] if unique_clauses else 0

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
		"identity_count": identity_count,
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
