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


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _has_role(user, *roles):
	return bool(set(roles) & set(frappe.get_roles(user)))


def _scope(user=None):
	"""Return the scope descriptor of an actor.

	`scope` is one of `system` (whole installation), `all` (read-only whole
	installation without PII), `team` (the sales hierarchy subtree of the user)
	or `self` (only the own assignments).
	"""
	user = user or frappe.session.user
	if _is_system(user):
		return {"scope": "system", "users": None, "teams": None, "pii": True}
	if _has_role(user, "CSKH CRM Admin"):
		# ADR-P1-07 / L-17: `CSKH CRM Admin` is the business administrator of
		# CSKH campaigns, pool and distribution without granting `System Manager`.
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


def _team_nodes(user):
	"""The names of the `CRM Sales Hierarchy` nodes of the user's subtree.

	`CSKH Campaign.team_scope` holds the **name of a hierarchy node**, not a
	user. Comparing it with the user emails never matches, so a leader would
	see neither their own campaigns nor the campaigns of their subtree
	(L-07).
	"""
	manager = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"])
	if not manager:
		return []
	rows = frappe.db.sql(
		"""select name from `tabCRM Sales Hierarchy`
		   where lft >= %s and rgt <= %s""",
		(manager[0], manager[1]),
		pluck=True,
	)
	return list(rows)


def _scope_team_nodes(scope):
	"""The hierarchy node names the actor may see, or None for the whole site."""
	if scope["scope"] in ("system", "all"):
		return None
	if scope["scope"] == "team":
		return _team_nodes(scope["user"])
	return []


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


def _scoped_users_clause(users, column="assigned_to"):
	"""SQL fragment restricting `column` to `users`.

	Returns `(clause, params)`. An empty `users` list yields `1=0`, never an
	empty `in ()` (which is a syntax error) and never an unfiltered query.
	"""
	if users is None:
		return "", []
	if not users:
		return "1=0", []
	placeholders = ",".join(["%s"] * len(users))
	return f"{column} in ({placeholders})", list(users)


def _scoped_campaign_clause(nodes, alias=""):
	"""SQL fragment restricting a campaign column to the campaigns in scope.

	The scope of a campaign is its `team_scope`: the name of a
	`CRM Sales Hierarchy` node. A leader sees the campaigns of their subtree
	plus the campaigns without a team scope (a shared campaign).

	The subquery uses a **distinct alias** (`cskh_scope_campaign`): the
	alias of the outer query (`c`) would shadow it and produce
	`Unknown column 'c.campaign'`.
	"""
	if nodes is None:
		return "", []
	if not nodes:
		return "1=0", []
	prefix = f"{alias}." if alias else ""
	placeholders = ",".join(["%s"] * len(nodes))
	return (
		f"({prefix}campaign in (select cskh_scope_campaign.name from `tabCSKH Campaign` cskh_scope_campaign "
		f"where cskh_scope_campaign.team_scope is null "
		f"or cskh_scope_campaign.team_scope in ({placeholders})))",
		list(nodes),
	)


def _scoped_campaign_name_clause(nodes, alias=""):
	"""SQL fragment restricting a campaign **name** to the campaigns in scope.

	Used where the query already selects from `tabCSKH Campaign`: the
	condition then reads the `team_scope` of the row itself instead of
	looking the campaign up again in a subquery.
	"""
	if nodes is None:
		return "", []
	if not nodes:
		return "1=0", []
	prefix = f"{alias}." if alias else ""
	placeholders = ",".join(["%s"] * len(nodes))
	return (
		f"({prefix}team_scope is null or {prefix}team_scope in ({placeholders}))",
		list(nodes),
	)


def _work_item_scope_clause(scope, alias="wi"):
	"""Unified work-item scope predicate across assigned and unassigned rows (L-22).

	`WorkItem.validate()` enforces `assigned_to IS NULL` for `queued`, `revoked`
	and `closed` rows (`p1_models.py`). Filtering those statuses or `Active Claim`
	by `assigned_to IN (team_users)` alone would always drop unassigned rows for
	a `Sales Manager` (`scope == 'team'`).

	* `system` / `all` (`Administrator`, `System Manager`, `CSKH CRM Admin`,
	  `CSKH BOD Viewer`): unrestricted (`""`).
	* `team` (`Sales Manager`): `(assigned_to IS NOT NULL AND assigned_to IN team)`
	  OR `(assigned_to IS NULL AND campaign IN readable_campaigns)`.
	* `self` (`Sales User`): `assigned_to = actor` (never sees unassigned pool /
	  revoked / closed items).
	"""
	if scope["scope"] in ("system", "all"):
		return "", []
	prefix = f"{alias}." if alias else ""
	if scope["scope"] == "team":
		users = _scope_users(scope)
		nodes = _scope_team_nodes(scope)
		user_clause, user_params = _scoped_users_clause(users, f"{prefix}assigned_to")
		camp_clause, camp_params = _scoped_campaign_clause(nodes, alias)
		return (
			f"(({prefix}assigned_to is not null and {user_clause}) "
			f"or ({prefix}assigned_to is null and {camp_clause}))",
			list(user_params) + list(camp_params),
		)
	return f"{prefix}assigned_to = %s", [scope["user"]]


def _pool_scope_filter(scope, alias="wi"):
	"""Restrict the unassigned pool to the campaigns of the actor's scope.

	The pool contains **unassigned** work items (`assigned_to is null`), so it
	cannot be filtered by the assignee. The scope is therefore applied on the
	campaign: a leader sees the campaigns of their team, a staff member only
	the campaigns of their own team (and the campaigns without a team scope).
	"""
	nodes = _scope_team_nodes(scope)
	if nodes is None:
		return "", []
	if not nodes:
		return "1=0", []
	placeholders = ",".join(["%s"] * len(nodes))
	return (
		f"{alias}.campaign in (select c.name from `tabCSKH Campaign` c "
		f"where c.team_scope is null or c.team_scope in ({placeholders}))",
		nodes,
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
	scope is pushed into the SQL of **every** card, of `per_user`, of
	`assignment_events` and of `identity_count` (ADR-P1-07).

	The `campaign` and `date_from` / `date_to` filters are **applied** to the
	counts; a filter that cannot be honoured is refused explicitly instead of
	being accepted and silently ignored (P1 §9.5).
	"""
	user = user or frappe.session.user
	scope = _scope(user)

	campaign_filters = {}
	if campaign:
		campaign_filters = {"campaign": campaign}

	users = _scope_users(scope)
	nodes = _scope_team_nodes(scope)
	# A leader sees only the work items of their team; a staff member only
	# their own. The BOD and the system see the whole installation.
	# The clause is built **per alias**: a bare `assigned_to` would be
	# ambiguous in a joined query (L-04).
	assigned_to_clause_wi, assigned_to_params_wi = _scoped_users_clause(users, "wi.assigned_to")

	# The queue is unassigned, so it is scoped through the campaign scope.
	queue_clause_wi, queue_params_wi = _scoped_campaign_clause(nodes, "wi")

	# Unified work-item scope across assigned and unassigned rows (L-22):
	# `assigned_to IN team` when assigned, `campaign IN readable_campaigns`
	# when `assigned_to IS NULL` (`queued`, `revoked`, `closed`).
	work_item_scope_clause, work_item_scope_params = _work_item_scope_clause(scope, "wi")

	# The date filter is applied to **every** card, so a filter the contract
	# accepts is never accepted-and-ignored (L-06). The window is the
	# **creation** of the work item: every card of the dashboard counts work
	# items, so one single column keeps the cards consistent with each other.
	date_clauses = []
	date_params = []
	if date_from:
		date_clauses.append("wi.`creation` >= %s")
		date_params.append(date_from)
	if date_to:
		date_clauses.append("wi.`creation` <= %s")
		date_params.append(date_to)
	date_clause = " and ".join(date_clauses) if date_clauses else None

	# The campaigns the actor may read at all. A leader never sees the
	# campaigns of another team, even in the aggregate counters.
	campaign_name_clause, campaign_name_params = _scoped_campaign_name_clause(nodes, "c")

	# The import batch belongs to the user that created it, so the scope of
	# the failed batches is the scope of their creator.
	owner_clause, owner_params = _scoped_users_clause(users, "b.owner")

	# A distribution run belongs to a campaign, so the scope of the failed
	# runs is the scope of their campaign.
	run_campaign_clause, run_campaign_params = _scoped_campaign_clause(nodes, "r")

	def scoped_count(
		doctype,
		filters,
		extra=None,
		params=(),
		alias=None,
		extra_date=None,
		extra_date_params=(),
	):
		"""Count rows of `doctype` restricted by the actor's scope.

		The alias, when given, qualifies the **filter columns** and the
		`extra` clause: `` `wi.assigned_to` `` would be parsed by MariaDB as a
		table named `wi.assigned_to`, so the backticks must wrap the column
		name only (`` wi.`assigned_to` ``) (L-04).

		`extra_date` is the date filter of the dashboard (`date_from` /
		`date_to`). It is applied **inside** the count, so a filter that the
		contract accepts is never silently ignored (L-06).
		"""
		clauses = []
		values = []
		prefix = f"{alias}." if alias else ""
		for field, value in (filters or {}).items():
			clauses.append(f"{prefix}`{field}` = %s")
			values.append(value)
		if extra:
			clauses.append(extra)
			values.extend(params)
		if extra_date:
			clauses.append(extra_date)
			values.extend(extra_date_params)
		where = " where " + " and ".join(clauses) if clauses else ""
		table = f"`tab{doctype}`" + (f" {alias}" if alias else "")
		return frappe.db.sql(
			f"select count(*) from {table}{where}",
			tuple(values),
		)[0][0]

	assigned = scoped_count(
		"CSKH Work Item",
		{**campaign_filters, "status": "assigned"},
		assigned_to_clause_wi,
		assigned_to_params_wi,
		alias="wi",
		extra_date=date_clause,
		extra_date_params=date_params,
	)
	queued = scoped_count(
		"CSKH Work Item",
		{**campaign_filters, "status": "queued"},
		queue_clause_wi,
		queue_params_wi,
		alias="wi",
		extra_date=date_clause,
		extra_date_params=date_params,
	)
	revoked = scoped_count(
		"CSKH Work Item",
		{**campaign_filters, "status": "revoked"},
		work_item_scope_clause,
		work_item_scope_params,
		alias="wi",
		extra_date=date_clause,
		extra_date_params=date_params,
	)
	closed = scoped_count(
		"CSKH Work Item",
		{**campaign_filters, "status": "closed"},
		work_item_scope_clause,
		work_item_scope_params,
		alias="wi",
		extra_date=date_clause,
		extra_date_params=date_params,
	)

	# The active claim is bound to a work item, so it is scoped through the
	# work item it protects (both `assigned` items of the team and `queued`
	# unassigned items in the leader's readable campaigns — L-22).
	active_claims = frappe.db.sql(
		"""select count(*) from `tabCSKH Active Claim` ac
		   inner join `tabCSKH Work Item` wi on wi.name = ac.work_item
		   where wi.status in ('queued', 'assigned') {campaign_clause} {scope_clause} {date_clause}""".format(
			campaign_clause="and ac.campaign = %s" if campaign else "",
			scope_clause=("and " + work_item_scope_clause) if work_item_scope_clause else "",
			date_clause=("and " + date_clause) if date_clause else "",
		),
		((campaign,) if campaign else ()) + tuple(work_item_scope_params) + tuple(date_params),
	)[0][0]

	# The campaigns the actor may see: the ones in scope, or all of them for
	# the BOD and the system.
	active_campaigns = frappe.db.sql(
		"""select count(*) from `tabCSKH Campaign` c
		   where c.status = 'Active' {scope_clause}""".format(
			scope_clause=("and " + campaign_name_clause) if campaign_name_clause else "",
		),
		tuple(campaign_name_params),
	)[0][0]

	# The failed import batches and the failed distribution runs are the
	# operations of the scope, so they are scoped the same way.
	failed_batches = frappe.db.sql(
		"""select count(*) from `tabCSKH Import Batch` b
		   where b.status = 'FAILED' {scope_clause}""".format(
			scope_clause=("and " + owner_clause) if owner_clause else "",
		),
		tuple(owner_params),
	)[0][0]
	failed_runs = frappe.db.sql(
		"""select count(*) from `tabCSKH Distribution Run` r
		   where r.status = 'failed' {scope_clause}""".format(
			scope_clause=("and " + run_campaign_clause) if run_campaign_clause else "",
		),
		tuple(run_campaign_params),
	)[0][0]

	# "Unique customers" counts the **distinct leads** across all work items
	# in the actor's scope (`assigned`, `queued`, `revoked`, `closed` — P1
	# §12.5, contract §3.18, L-22): one unified definition for system, BOD,
	# leader and staff.
	unique_clauses = []
	unique_params = []
	for field, value in (campaign_filters or {}).items():
		unique_clauses.append(f"wi.`{field}` = %s")
		unique_params.append(value)
	if work_item_scope_clause:
		unique_clauses.append(work_item_scope_clause)
		unique_params.extend(work_item_scope_params)
	if date_clause:
		unique_clauses.append(date_clause)
		unique_params.extend(date_params)
	unique_where = " where " + " and ".join(unique_clauses) if unique_clauses else ""
	unique_leads = frappe.db.sql(
		"select count(distinct wi.`lead`) from `tabCSKH Work Item` wi" + unique_where,
		tuple(unique_params),
	)[0][0]

	# The workload of every user of the scope, **per campaign**: a rule binds a
	# campaign to an assignee, so the weight and the capacity of a user are
	# properties of a (campaign, user) pair. The breakdown is therefore built
	# from a per-(user, campaign) grouping; a single `max()` over the rules
	# would mix unrelated campaigns and make a user with an unlimited rule
	# look limited (L-10).
	per_user_rows = frappe.db.sql(
		"""select wi.assigned_to as user, wi.campaign as campaign, count(*) as current_total
		   from `tabCSKH Work Item` wi
		   where wi.status = 'assigned' {campaign_clause} {scope_clause} {date_clause}
		   group by wi.assigned_to, wi.campaign
		   order by wi.assigned_to asc""".format(
			campaign_clause="and wi.campaign = %s" if campaign else "",
			scope_clause=("and " + assigned_to_clause_wi) if assigned_to_clause_wi else "",
			date_clause=("and " + date_clause) if date_clause else "",
		),
		((campaign,) if campaign else ()) + tuple(assigned_to_params_wi) + tuple(date_params),
		as_dict=True,
	)

	per_user = _per_user_breakdown(per_user_rows)

	# The assignment events of a campaign are the events of the **work items**
	# of that campaign, and the events of its **distribution runs**. A
	# campaign without a distribution run has **no** event of its own: the
	# history of the whole installation is never substituted (L-06).
	history_events = _campaign_assignment_events(campaign, users, date_from, date_to)

	# The identity index holds the canonical phone numbers. A leader may only
	# count the identities of the leads of their team, never the whole
	# installation (P1 §8.1: the BOD sees the numbers, a leader sees the team).
	identity_count = _scoped_identity_count(
		scope, campaign=campaign, date_clause=date_clause, date_params=date_params
	)

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


def _per_user_breakdown(rows):
	"""Build the `per_user` breakdown of the dashboard.

	The rows are the (user, campaign, current_total) groups of the assigned
	work items. The **weight** and the **capacity** of a user come from the
	`CSKH Assignment Rule` of that (campaign, user) pair, so the rules are
	read **per campaign** and never aggregated across campaigns with a plain
	`max()`: a user who is unlimited (`capacity_limit = 0`) in one campaign
	must not look limited because another campaign caps him (L-10).

	**Capacity semantics (P1 §7.2, L-10):**

	* `capacity_limit = 0` means **unlimited**. A campaign that is unlimited
	  makes the aggregate of the user **unbounded** as well, so the breakdown
	  reports `null` for `capacity` and `remaining_capacity`.
	* Otherwise the aggregate capacity is the **sum** of the limits of the
	  campaigns the user is active in, and the remaining capacity is the sum
	  of the per-campaign remainders. The workload of the user is the sum of
	  the workloads of the campaigns, so the capacity must be summed too.
	* With a `campaign` filter only that campaign contributes, so the numbers
	  are exact.

	The per-campaign detail is returned in `campaigns`, so the dashboard can
	drill down without a second request (P1 §9.5).
	"""
	rows = list(rows or [])
	if not rows:
		return []

	users = sorted({row.user for row in rows if row.user})
	campaigns = sorted({row.campaign for row in rows if row.campaign})
	if not users or not campaigns:
		return []

	placeholders = ",".join(["%s"] * len(users))
	campaign_placeholders = ",".join(["%s"] * len(campaigns))
	rule_rows = frappe.db.sql(
		f"""select campaign, `user`, weight, capacity_limit, version
		    from `tabCSKH Assignment Rule`
		    where enabled = 1
		      and `user` in ({placeholders})
		      and campaign in ({campaign_placeholders})
		    order by version asc""",
		tuple(users) + tuple(campaigns),
		as_dict=True,
	)

	# The **newest** rule of a (campaign, user) pair wins.
	rules = {}
	for rule in rule_rows:
		rules[(rule.campaign, rule.user)] = rule

	# (user, campaign) -> current_total
	usage = {}
	for row in rows:
		key = (row.user, row.campaign)
		usage[key] = usage.get(key, 0) + int(row.current_total or 0)

	per_user = []
	for user in users:
		total = 0
		weight = 0
		capacity = 0
		unlimited = False
		detail = []
		for (row_user, row_campaign), count in sorted(usage.items()):
			if row_user != user:
				continue
			total += count
			rule = rules.get((row_campaign, user))
			limit = int(rule.capacity_limit or 0) if rule else 0
			rule_weight = int(rule.weight or 0) if rule else 0
			weight = max(weight, rule_weight)
			if limit > 0:
				capacity += limit
			else:
				# 0 = unlimited: the aggregate of the user is unbounded.
				unlimited = True
			detail.append(
				{
					"campaign": row_campaign,
					"weight": rule_weight,
					"current_total": count,
					"capacity": limit or None,
					"remaining_capacity": (limit - count) if limit else None,
				}
			)
		per_user.append(
			{
				"user": user,
				"weight": weight,
				"current_total": total,
				"capacity": None if unlimited else (capacity or None),
				"remaining_capacity": None if unlimited else (capacity - total),
				"campaigns": detail,
			}
		)

	# The breakdown is ordered by weight, then by the user, so the table of
	# the dashboard is stable (P1 §9.5).
	per_user.sort(key=lambda item: (-int(item["weight"] or 0), item["user"] or ""))
	return per_user


def _campaign_run_names(campaign):
	"""The names of the distribution runs of a campaign."""
	if not campaign:
		return []
	return frappe.get_list(
		"CSKH Distribution Run",
		filters={"campaign": campaign},
		pluck="name",
		limit_page_length=0,
	)


def _campaign_assignment_events(campaign, users=None, date_from=None, date_to=None):
	"""Count the assignment events of a campaign.

	The events of a campaign are the events of the **work items** of that
	campaign — the events that carry its `distribution_run` **and** the manual
	events of the same work items. The history of the whole installation is
	never counted for a campaign (L-01, L-06).

	A campaign without a distribution run and without a work item reports 0
	events, never the history of the site.
	"""
	clauses = []
	params = []

	if campaign:
		clauses.append("wi.campaign = %s")
		params.append(campaign)
	elif users is not None:
		# Without a campaign filter the events are the ones of the work
		# items of the actor's scope.
		if not users:
			return 0
		placeholders = ",".join(["%s"] * len(users))
		clauses.append(f"wi.assigned_to in ({placeholders})")
		params.extend(users)

	if date_from:
		clauses.append("h.event_at >= %s")
		params.append(date_from)
	if date_to:
		clauses.append("h.event_at <= %s")
		params.append(date_to)
	if users is not None:
		if not users:
			return 0
		placeholders = ",".join(["%s"] * len(users))
		clauses.append(f"h.to_user in ({placeholders})")
		params.extend(users)

	where = " where " + " and ".join(clauses) if clauses else ""
	return frappe.db.sql(
		"select count(*) from `tabCSKH Assignment History` h "
		"inner join `tabCSKH Work Item` wi on wi.name = h.work_item" + where,
		tuple(params),
	)[0][0]


def _scoped_identity_count(scope, campaign=None, date_clause=None, date_params=()):
	"""Count the canonical identities the actor may see.

	The identity index is system-only (ADR-P1-07 §3.4), so its **rows** are
	never exposed. The dashboard may only report an **aggregate** of the
	identities of the leads the actor is allowed to see: the whole
	installation for the BOD and the system, the subtree of the team for a
	leader, the own identities for a staff member.
	"""
	if isinstance(scope, dict):
		is_unrestricted = scope.get("scope") in ("system", "all")
		wi_clause, wi_params = _work_item_scope_clause(scope, "wi")
	else:
		# Backward-compatible signature when a caller passes a `users` list/None.
		is_unrestricted = scope is None
		wi_clause, wi_params = _scoped_users_clause(scope, "wi.assigned_to")

	if is_unrestricted and not campaign and not date_clause:
		return frappe.db.count("CSKH Identity Index")

	inner_clauses = []
	params = []
	if wi_clause:
		inner_clauses.append(wi_clause)
		params.extend(wi_params)
	if campaign:
		inner_clauses.append("wi.campaign = %s")
		params.append(campaign)
	if date_clause:
		inner_clauses.append(date_clause)
		params.extend(date_params)

	return frappe.db.sql(
		"select count(distinct ii.name) from `tabCSKH Identity Index` ii "
		"where ii.canonical_lead in (select wi.lead from `tabCSKH Work Item` wi where "
		+ " and ".join(inner_clauses)
		+ ")",
		tuple(params),
	)[0][0]


def campaign_summary(user=None, campaign=None):
	"""Per-campaign distribution summary used by the dashboard filters.

	The per-campaign counters are scoped like the dashboard cards: a leader
	only sees the campaigns of their team (P1 §10.1).

	The `assigned` counter counts the **work items** assigned in the campaign
	inside the scope of the actor — never the number of distinct users, which
	would under-report a campaign where one user owns several work items
	(P1 §7.2: "đếm số được giao ở Work Item").
	"""
	user = user or frappe.session.user
	scope = _scope(user)
	users = _scope_users(scope)
	nodes = _scope_team_nodes(scope)

	campaign_rows = frappe.get_list(
		"CSKH Campaign",
		filters={"status": ["!=", "Closed"]},
		fields=["name", "code", "title", "status", "rule_version", "team_scope"],
		order_by="code asc",
		limit_page_length=0,
	)
	if nodes is not None:
		campaign_rows = [row for row in campaign_rows if not row.team_scope or row.team_scope in nodes]

	summary = []
	for row in campaign_rows:
		filters = {"campaign": row.name, "status": "assigned"}
		if users is not None:
			filters["assigned_to"] = ["in", list(users)]
		assigned = frappe.db.count("CSKH Work Item", filters)

		queued_filters = {"campaign": row.name, "status": "queued"}
		if users is not None:
			# The queue is unassigned: it is scoped through the campaign, and
			# a campaign of another team is already filtered out above.
			queued = frappe.db.count("CSKH Work Item", queued_filters)
		else:
			queued = frappe.db.count("CSKH Work Item", queued_filters)

		summary.append(
			{
				"campaign": row.name,
				"code": row.code,
				"title": row.title,
				"status": row.status,
				"rule_version": row.rule_version,
				"assigned": assigned,
				"queued": queued,
			}
		)
	return {"campaigns": summary, "as_of": now_datetime()}
