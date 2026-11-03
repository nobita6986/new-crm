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

from crm_cskh.api._params import parse_optional_str, parse_pagination
from crm_cskh.services.errors import Forbidden, InvalidRequest

ALLOWED_POOL_STATES = {"eligible", "queued", "assigned", "conflict", "revoked", "closed", "invalid"}


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
		f"{alias}.campaign in (select c_psf.name from `tabCSKH Campaign` c_psf "
		f"where c_psf.team_scope is null or c_psf.team_scope in ({placeholders}))",
		nodes,
	)


def my_list(user=None, page=1, page_size=20, status=None, campaign=None, search=None):
	"""The customers currently assigned to the caller (P1 9.4).

	The filter is applied on the backend, not in the browser.
	"""
	user = user or frappe.session.user
	page_num, limit, offset = parse_pagination(page, page_size)
	status_val = parse_optional_str(status)
	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	if search is not None:
		parse_optional_str(search)

	filters = {"assigned_to": user, "status": status_val or "assigned"}
	if campaign_val:
		filters["campaign"] = campaign_val

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
		"page": page_num,
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


def lead_in_batch_clause(batch, lead_expr="l.name"):
	"""SQL predicate matching a `CRM Lead` expression against an import batch or run (L-25).

	Matches:
	* `tabCSKH Import Batch Lead` (`ibl.import_batch = %s and ibl.lead = <lead_expr>`),
	  covering newly imported leads with zero `CSKH Work Item` AND canonical leads
	  reused across multiple import batches;
	* `tabCSKH Work Item` (`wi_b.import_batch = %s or wi_b.allocation_run = %s`),
	  covering distribution run IDs and direct work-item batch links.
	"""
	batch_val = (batch or "").strip() if isinstance(batch, str) else ""
	if not batch_val:
		return "", []
	return (
		f"(exists (select 1 from `tabCSKH Import Batch Lead` ibl "
		f"where ibl.lead = {lead_expr} and ibl.import_batch = %s) "
		f"or exists (select 1 from `tabCSKH Work Item` wi_b "
		f"where wi_b.lead = {lead_expr} and (wi_b.import_batch = %s or wi_b.allocation_run = %s)))",
		[batch_val, batch_val, batch_val],
	)


def work_item_in_batch_clause(batch, alias="wi"):
	"""SQL predicate matching a `CSKH Work Item` row against an import batch or run (L-25)."""
	batch_val = (batch or "").strip() if isinstance(batch, str) else ""
	if not batch_val:
		return "", []
	prefix = f"{alias}." if alias else ""
	return (
		f"({prefix}import_batch = %s or {prefix}allocation_run = %s "
		f"or exists (select 1 from `tabCSKH Import Batch Lead` ibl_w "
		f"where ibl_w.lead = {prefix}lead and ibl_w.import_batch = %s))",
		[batch_val, batch_val, batch_val],
	)


def _scoped_wi_predicate(scope, alias="wi"):
	"""Return `(sql, params)` restricting `tabCSKH Work Item` `alias` to `scope` (`L-23`)."""
	if scope["scope"] in ("system", "all"):
		return "1=1", []
	if scope["scope"] != "team":
		return "1=0", []

	team_nodes = _scope_team_nodes(scope)
	team_users = _team_users(scope["user"])
	if not team_nodes or not team_users:
		return "1=0", []

	user_ph = ",".join(["%s"] * len(team_users))
	node_ph = ",".join(["%s"] * len(team_nodes))
	sql = (
		f"(({alias}.status = 'assigned' and {alias}.assigned_to in ({user_ph})) "
		f"or ({alias}.status = 'queued' and {alias}.assigned_to is null "
		f"and {alias}.campaign in (select c_wq.name from `tabCSKH Campaign` c_wq "
		f"where c_wq.team_scope is null or c_wq.team_scope in ({node_ph}))) "
		f"or ({alias}.status in ('revoked', 'closed') and {alias}.assigned_to is null "
		f"and ({alias}.campaign in (select c_wt.name from `tabCSKH Campaign` c_wt "
		f"where c_wt.team_scope in ({node_ph})) "
		f"or ({alias}.campaign in (select c_ws.name from `tabCSKH Campaign` c_ws "
		f"where c_ws.team_scope is null) "
		f"and coalesce((select coalesce(h_ws.from_user, h_ws.to_user) "
		f"from `tabCSKH Assignment History` h_ws "
		f"where h_ws.work_item = {alias}.name "
		f"order by h_ws.event_at desc, h_ws.creation desc limit 1), '') in ({user_ph})))))"
	)
	params = [*team_users, *team_nodes, *team_nodes, *team_users]
	return sql, params


def _scoped_ac_predicate(scope, alias="ac"):
	"""Return `(sql, params)` restricting `tabCSKH Active Claim` `alias` to `scope` (`L-23`)."""
	if scope["scope"] in ("system", "all"):
		return "1=1", []
	if scope["scope"] != "team":
		return "1=0", []

	team_nodes = _scope_team_nodes(scope)
	team_users = _team_users(scope["user"])
	if not team_nodes or not team_users:
		return "1=0", []

	node_ph = ",".join(["%s"] * len(team_nodes))
	user_ph = ",".join(["%s"] * len(team_users))
	sql = (
		f"(exists (select 1 from `tabCSKH Campaign` c_acs "
		f"where c_acs.name = {alias}.campaign "
		f"and (c_acs.team_scope is null or c_acs.team_scope in ({node_ph}))) "
		f"and not exists (select 1 from `tabCSKH Work Item` wi_acs "
		f"where wi_acs.name = {alias}.work_item "
		f"and wi_acs.assigned_to is not null "
		f"and wi_acs.assigned_to not in ({user_ph})))"
	)
	params = [*team_nodes, *team_users]
	return sql, params


def _team_lead_authorization_clauses(scope, lead_alias="l"):
	"""Return `(clauses, params)` enforcing current team authorization on `CRM Lead` (`L-23`).

	A team leader (`scope == "team"`) may only read a lead in the Customer Pool if:
	  1. The lead belongs to the team's scope (has a scoped work item or is
	     currently owned by a member of `team_users`), AND
	  2. The lead has NO active `assigned` work item outside `team_users`, AND
	  3. The lead has NO `queued` work item in a private campaign outside
	     `team_nodes`, AND
	  4. The lead has NO `CSKH Active Claim` outside `team_nodes`/`team_users`, AND
	  5. `CRM Lead.lead_owner` is empty/`Administrator` or inside `team_users`.
	"""
	if scope["scope"] in ("system", "all"):
		return [], []
	if scope["scope"] != "team":
		return ["1=0"], []

	team_nodes = _scope_team_nodes(scope)
	team_users = _team_users(scope["user"])
	if not team_nodes or not team_users:
		return ["1=0"], []

	user_ph = ",".join(["%s"] * len(team_users))
	node_ph = ",".join(["%s"] * len(team_nodes))
	wi_s_sql, wi_s_params = _scoped_wi_predicate(scope, "wi_s")

	clauses = [
		(
			f"(exists (select 1 from `tabCSKH Work Item` wi_s "
			f"where wi_s.lead = {lead_alias}.name and {wi_s_sql}) "
			f"or {lead_alias}.lead_owner in ({user_ph}))"
		),
		(
			f"not exists (select 1 from `tabCSKH Work Item` wi_out "
			f"where wi_out.lead = {lead_alias}.name and wi_out.status = 'assigned' "
			f"and (wi_out.assigned_to is null or wi_out.assigned_to not in ({user_ph})))"
		),
		(
			f"(exists (select 1 from `tabCSKH Work Item` wi_in_asg "
			f"where wi_in_asg.lead = {lead_alias}.name and wi_in_asg.status = 'assigned' "
			f"and wi_in_asg.assigned_to in ({user_ph})) "
			f"or exists (select 1 from `tabCSKH Active Claim` ac_in "
			f"inner join `tabCSKH Campaign` c_ac_in on c_ac_in.name = ac_in.campaign "
			f"where ac_in.lead = {lead_alias}.name and c_ac_in.team_scope in ({node_ph})) "
			f"or not exists (select 1 from `tabCSKH Work Item` wi_q_out "
			f"where wi_q_out.lead = {lead_alias}.name and wi_q_out.status = 'queued' "
			f"and exists (select 1 from `tabCSKH Campaign` c_q_out "
			f"where c_q_out.name = wi_q_out.campaign "
			f"and c_q_out.team_scope is not null "
			f"and c_q_out.team_scope not in ({node_ph}))))"
		),
		(
			f"not exists (select 1 from `tabCSKH Active Claim` ac_out "
			f"left join `tabCSKH Campaign` c_ac_out on c_ac_out.name = ac_out.campaign "
			f"left join `tabCSKH Work Item` wi_ac_out on wi_ac_out.name = ac_out.work_item "
			f"where ac_out.lead = {lead_alias}.name "
			f"and ((c_ac_out.team_scope is not null and c_ac_out.team_scope not in ({node_ph})) "
			f"or (wi_ac_out.assigned_to is not null and wi_ac_out.assigned_to not in ({user_ph}))))"
		),
		(
			f"({lead_alias}.lead_owner is null "
			f"or {lead_alias}.lead_owner in ('', 'Administrator') "
			f"or {lead_alias}.lead_owner in ({user_ph}))"
		),
	]
	params = [
		*wi_s_params,
		*team_users,
		*team_users,
		*team_users,
		*team_nodes,
		*team_nodes,
		*team_nodes,
		*team_users,
		*team_users,
	]
	return clauses, params


def filter_team_authorized_leads(scope, candidate_leads):
	"""Return the subset of `candidate_leads` currently authorized for `scope` (`L-23`)."""
	if not candidate_leads:
		return set()
	unique_leads = sorted({str(lead) for lead in candidate_leads if lead})
	if not unique_leads:
		return set()
	if scope["scope"] in ("system", "all"):
		return set(unique_leads)
	if scope["scope"] != "team":
		return set()

	auth_clauses, auth_params = _team_lead_authorization_clauses(scope, "l")
	if auth_clauses == ["1=0"]:
		return set()

	lead_ph = ",".join(["%s"] * len(unique_leads))
	rows = frappe.db.sql(
		f"select l.name from `tabCRM Lead` l where l.name in ({lead_ph}) and " + " and ".join(auth_clauses),
		(*unique_leads, *auth_params),
		pluck=True,
	)
	return set(rows or [])


def _empty_pool_response(scope_name, page_num, limit):
	return {
		"scope": scope_name,
		"items": [],
		"total": 0,
		"unique_customers": 0,
		"work_items": 0,
		"page": page_num,
		"page_size": limit,
		"as_of": now_datetime(),
	}


def pool_list(user=None, page=1, page_size=20, campaign=None, batch=None):
	"""The customer pool of queued work items inside the caller's scope (P1 9.1, L-23).

	Enforces current `CRM Lead` authorization (`_team_lead_authorization_clauses`)
	directly inside the SQL query selecting `CSKH Work Item` rows, sharing one
	unified `FROM ... INNER JOIN tabCRM Lead l ... WHERE ...` predicate across
	`rows`, `total`, and `unique_customers` prior to `LIMIT`/`OFFSET` pagination.
	"""
	user = user or frappe.session.user
	scope = _scope(user)
	page_num, limit, offset = parse_pagination(page, page_size)
	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	batch_val = parse_optional_str(batch)

	if scope["scope"] not in ("system", "all", "team"):
		return _empty_pool_response(scope["scope"], page_num, limit)

	clause, scope_params = _pool_scope_filter(scope, "wi")
	if clause == "1=0":
		return _empty_pool_response(scope["scope"], page_num, limit)
	auth_clauses, auth_params = _team_lead_authorization_clauses(scope, "l")
	if auth_clauses == ["1=0"]:
		return _empty_pool_response(scope["scope"], page_num, limit)
	batch_clause, batch_params = work_item_in_batch_clause(batch_val, "wi")

	where_clauses = ["wi.status = 'queued'", "wi.assigned_to is null"]
	where_params = []
	if campaign_val:
		where_clauses.append("wi.campaign = %s")
		where_params.append(campaign_val)
	if batch_clause:
		where_clauses.append(batch_clause)
		where_params.extend(batch_params)
	if clause:
		where_clauses.append(clause)
		where_params.extend(scope_params)
	if auth_clauses:
		where_clauses.extend(auth_clauses)
		where_params.extend(auth_params)

	from_where_sql = (
		"from `tabCSKH Work Item` wi "
		"inner join `tabCRM Lead` l on l.name = wi.lead "
		"where " + " and ".join(where_clauses)
	)
	param_tuple = tuple(where_params)

	counts = frappe.db.sql(
		f"select count(*) as total, count(distinct wi.lead) as unique_customers {from_where_sql}",
		param_tuple,
		as_dict=True,
	)[0]
	total = int(counts.total or 0)
	unique_customers = int(counts.unique_customers or 0)
	if total == 0:
		return _empty_pool_response(scope["scope"], page_num, limit)

	rows = frappe.db.sql(
		"select wi.name as work_item, wi.lead as lead, wi.campaign as campaign, "
		"wi.creation as wi_created_at, "
		"l.lead_name as lead_name, l.first_name as first_name, "
		"l.mobile_no as mobile_no, l.source as source, "
		"l.lead_owner as lead_owner, l.creation as lead_creation "
		f"{from_where_sql} "
		"order by wi.creation desc, wi.name desc limit %s offset %s",
		(*param_tuple, limit, offset),
		as_dict=True,
	)

	items = []
	allow_pii = bool(scope.get("pii", True))
	for row in rows:
		state, state_campaign, owner, created_at = _pool_state(row.lead, scope=scope)
		fallback_owner = row.lead_owner if row.lead_owner and row.lead_owner != "Administrator" else ""
		items.append(
			{
				"work_item": row.work_item,
				"lead": row.lead,
				"display_name": (row.lead_name or row.first_name or "") if allow_pii else "",
				"phone": row.mobile_no if allow_pii else None,
				"source": row.source,
				"campaign": row.campaign,
				"campaign_title": state_campaign or row.campaign,
				"state": state,
				"owner": owner or fallback_owner,
				"created_at": created_at or row.wi_created_at or row.lead_creation,
			}
		)

	return {
		"scope": scope["scope"],
		"items": items,
		"total": total,
		"unique_customers": unique_customers,
		"work_items": total,
		"page": page_num,
		"page_size": limit,
		"as_of": now_datetime(),
	}


def _pool_state(lead, scope=None):
	"""The derived distribution state of one lead within `scope` (P1 §9.1, L-23)."""
	if scope is not None:
		wi_sql, wi_params = _scoped_wi_predicate(scope, "wi")
		rows = frappe.db.sql(
			"select wi.name as work_item, wi.campaign as campaign, wi.status as status, "
			"wi.assigned_to as assigned_to, wi.creation as created_at "
			f"from `tabCSKH Work Item` wi where wi.lead = %s and {wi_sql} "
			"order by wi.creation desc",
			(lead, *wi_params),
			as_dict=True,
		)
	else:
		rows = frappe.db.sql(
			"select wi.name as work_item, wi.campaign as campaign, wi.status as status, "
			"wi.assigned_to as assigned_to, wi.creation as created_at "
			"from `tabCSKH Work Item` wi where wi.lead = %s "
			"order by wi.creation desc",
			(lead,),
			as_dict=True,
		)
	if not rows:
		return "eligible", "", "", None

	if scope is not None:
		ac_sql, ac_params = _scoped_ac_predicate(scope, "ac")
		claim_rows = frappe.db.sql(
			f"select ac.campaign as campaign, ac.work_item as work_item "
			f"from `tabCSKH Active Claim` ac where ac.lead = %s and {ac_sql} limit 1",
			(lead, *ac_params),
			as_dict=True,
		)
		claim = claim_rows[0] if claim_rows else None
	else:
		claim = frappe.db.get_value(
			"CSKH Active Claim", {"lead": lead}, ["campaign", "work_item"], as_dict=True
		)

	for row in rows:
		if row.status == "assigned" and row.assigned_to:
			return "assigned", row.campaign, row.assigned_to, row.created_at
		if row.status == "queued" and not row.assigned_to:
			return "queued", row.campaign, "", row.created_at
		if row.status == "revoked":
			return "revoked", row.campaign, row.assigned_to or "", row.created_at
		if row.status == "closed":
			return "closed", row.campaign, row.assigned_to or "", row.created_at

	if claim and claim.campaign:
		return "conflict", claim.campaign, "", rows[0].created_at
	return "eligible", "", "", rows[0].created_at


def customer_pool(
	user=None,
	page=1,
	page_size=20,
	campaign=None,
	state=None,
	source=None,
	batch=None,
	search=None,
):
	"""Scoped Customer Pool view with derived states and `Import Batch ↔ Lead` filtering (P1 §9.1, L-23, L-25, L-26).

	Enforces identical team authorization policy (`_team_lead_authorization_clauses`,
	`_scoped_wi_predicate`, `_scoped_ac_predicate`) as `crm_cskh.api.pool_ui.list`:
	for a team leader (`scope == "team"`), historical `revoked`/`closed` work items
	in Team A never grant access to a lead currently active, queued in another team's
	private campaign, claimed, or owned outside Team A (`L-23`), and pagination runs
	in SQL (`LIMIT`/`OFFSET`) with strict `400 INVALID_REQUEST` validation (`L-26`).
	"""
	user = user or frappe.session.user
	scope = _scope(user)
	allow_pii = bool(scope.get("pii", True))

	page_num, limit, offset = parse_pagination(page, page_size)

	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	state_val = parse_optional_str(state)
	source_val = parse_optional_str(source)
	batch_val = parse_optional_str(batch)
	search_val = parse_optional_str(search)

	if state_val and state_val not in ALLOWED_POOL_STATES:
		raise InvalidRequest("The request is invalid.")
	if search_val and not allow_pii:
		raise Forbidden("You do not have permission to perform this operation.")

	if scope["scope"] not in ("system", "all", "team"):
		return _empty_pool_response(scope["scope"], page_num, limit)

	auth_clauses, auth_params = _team_lead_authorization_clauses(scope, "l")
	if auth_clauses == ["1=0"]:
		return _empty_pool_response(scope["scope"], page_num, limit)

	if scope["scope"] == "team" and campaign_val:
		team_nodes = _scope_team_nodes(scope)
		if not team_nodes:
			return _empty_pool_response(scope["scope"], page_num, limit)
		node_ph = ",".join(["%s"] * len(team_nodes))
		readable = frappe.db.sql(
			f"select name from `tabCSKH Campaign` where name = %s "
			f"and (team_scope is null or team_scope in ({node_ph}))",
			(campaign_val, *team_nodes),
			pluck=True,
		)
		if not readable:
			return _empty_pool_response(scope["scope"], page_num, limit)

	lead_clauses = ["1=1", *auth_clauses]
	lead_params = [*auth_params]

	if batch_val:
		b_sql, b_params = lead_in_batch_clause(batch_val, "l.name")
		lead_clauses.append(b_sql)
		lead_params.extend(b_params)

	if source_val:
		lead_clauses.append("coalesce(l.source, '') like %s")
		lead_params.append(f"%{source_val}%")

	if search_val and allow_pii:
		like = f"%{search_val}%"
		digits = search_val.lstrip("0") if search_val.startswith("0") and search_val[1:].isdigit() else ""
		if digits:
			alt_like = f"%+84{digits}%"
			lead_clauses.append(
				"(coalesce(l.lead_name, '') like %s "
				"or coalesce(l.first_name, '') like %s "
				"or coalesce(l.mobile_no, '') like %s "
				"or coalesce(l.mobile_no, '') like %s "
				"or l.name like %s)"
			)
			lead_params.extend([like, like, like, alt_like, like])
		else:
			lead_clauses.append(
				"(coalesce(l.lead_name, '') like %s "
				"or coalesce(l.first_name, '') like %s "
				"or coalesce(l.mobile_no, '') like %s "
				"or l.name like %s)"
			)
			lead_params.extend([like, like, like, like])

	wi_p_sql, wi_p_params = _scoped_wi_predicate(scope, "wi_p")
	ac_sql, ac_params = _scoped_ac_predicate(scope, "ac")

	pool_subquery = (
		"select "
		"l_pick.name as lead, "
		"l_pick.lead_name as lead_name, "
		"l_pick.first_name as first_name, "
		"l_pick.mobile_no as mobile_no, "
		"l_pick.source as source, "
		"l_pick.lead_owner as lead_owner, "
		"l_pick.creation as lead_creation, "
		"wi_cur.name as wi_name, "
		"wi_cur.campaign as wi_campaign, "
		"wi_cur.status as wi_status, "
		"wi_cur.assigned_to as wi_assigned_to, "
		"wi_cur.creation as wi_created_at, "
		"c_wi.title as wi_campaign_title, "
		"c_wi.code as wi_campaign_code, "
		"ac.campaign as ac_campaign, "
		"ac.work_item as ac_work_item, "
		"c_ac.title as ac_campaign_title, "
		"c_ac.code as ac_campaign_code, "
		"case "
		"when wi_cur.status = 'assigned' and coalesce(wi_cur.assigned_to, '') != '' then 'assigned' "
		"when wi_cur.status = 'queued' and coalesce(wi_cur.assigned_to, '') = '' then 'queued' "
		"when wi_cur.status = 'revoked' then 'revoked' "
		"when wi_cur.status = 'closed' then 'closed' "
		"when wi_cur.name is null and ac.name is null "
		"and coalesce(l_pick.lead_owner, '') not in ('', 'Administrator') then 'conflict' "
		"when wi_cur.name is null and ii.name is null then 'invalid' "
		"when coalesce(ac.campaign, '') != '' then 'conflict' "
		"when coalesce(l_pick.lead_owner, '') not in ('', 'Administrator') then 'conflict' "
		"when ii.name is null then 'invalid' "
		"else 'eligible' "
		"end as derived_state, "
		"case "
		"when wi_cur.status = 'assigned' and coalesce(wi_cur.assigned_to, '') != '' then coalesce(wi_cur.campaign, '') "
		"when wi_cur.status = 'queued' and coalesce(wi_cur.assigned_to, '') = '' then coalesce(wi_cur.campaign, '') "
		"when wi_cur.status in ('revoked', 'closed') then coalesce(wi_cur.campaign, '') "
		"when wi_cur.name is null and ac.name is null "
		"and coalesce(l_pick.lead_owner, '') not in ('', 'Administrator') then '' "
		"when wi_cur.name is null and ii.name is null then '' "
		"when coalesce(ac.campaign, '') != '' then coalesce(ac.campaign, '') "
		"else '' "
		"end as derived_campaign "
		"from ("
		"select l.name, l.lead_name, l.first_name, l.mobile_no, l.source, l.lead_owner, l.creation, "
		"(select wi_p.name from `tabCSKH Work Item` wi_p "
		f"where wi_p.lead = l.name and {wi_p_sql} "
		"order by case "
		"when wi_p.status = 'assigned' and coalesce(wi_p.assigned_to, '') != '' then 0 "
		"when wi_p.status = 'queued' and coalesce(wi_p.assigned_to, '') = '' then 1 "
		"when wi_p.status = 'revoked' then 2 "
		"when wi_p.status = 'closed' then 3 "
		"else 4 end asc, "
		"wi_p.modified desc, wi_p.creation desc, wi_p.name desc limit 1) as primary_wi_name "
		"from `tabCRM Lead` l "
		"where " + " and ".join(lead_clauses) + ") l_pick "
		"left join `tabCSKH Identity Index` ii on ii.canonical_lead = l_pick.name "
		"left join `tabCSKH Work Item` wi_cur on wi_cur.name = l_pick.primary_wi_name "
		"left join `tabCSKH Campaign` c_wi on c_wi.name = wi_cur.campaign "
		f"left join `tabCSKH Active Claim` ac on ac.lead = l_pick.name and {ac_sql} "
		"left join `tabCSKH Campaign` c_ac on c_ac.name = ac.campaign"
	)
	pool_params = [*wi_p_params, *lead_params, *ac_params]

	outer_clauses = ["1=1"]
	outer_params = []
	if campaign_val:
		outer_clauses.append("pool.derived_campaign = %s")
		outer_params.append(campaign_val)
	if state_val:
		outer_clauses.append("pool.derived_state = %s")
		outer_params.append(state_val)
	outer_where = " and ".join(outer_clauses)

	unique_customers = int(
		frappe.db.sql(
			f"select count(*) from ({pool_subquery}) pool where {outer_where}",
			(*pool_params, *outer_params),
		)[0][0]
		or 0
	)
	if unique_customers == 0:
		return _empty_pool_response(scope["scope"], page_num, limit)

	rows = frappe.db.sql(
		f"select * from ({pool_subquery}) pool where {outer_where} "
		"order by pool.lead_creation desc, pool.lead desc limit %s offset %s",
		(*pool_params, *outer_params, limit, offset),
		as_dict=True,
	)

	page_items = []
	for row in rows:
		state_code = row.derived_state
		item_campaign = row.derived_campaign or ""
		if state_code in ("assigned", "queued", "revoked", "closed") and row.wi_name:
			item_campaign_title = row.wi_campaign_title or row.wi_campaign_code or item_campaign
			work_item = row.wi_name
			created_at = row.wi_created_at or row.lead_creation
			if state_code == "queued":
				owner = ""
			else:
				owner = row.wi_assigned_to or ""
		elif state_code == "conflict" and item_campaign:
			item_campaign_title = row.ac_campaign_title or row.ac_campaign_code or item_campaign
			work_item = row.ac_work_item
			owner = row.lead_owner or ""
			created_at = row.wi_created_at or row.lead_creation
		else:
			item_campaign_title = ""
			work_item = None
			owner = (row.lead_owner or "") if state_code in ("conflict", "invalid") else ""
			created_at = row.wi_created_at or row.lead_creation

		display = row.lead_name or row.first_name or ""
		page_items.append(
			{
				"work_item": work_item,
				"lead": row.lead,
				"display_name": display if allow_pii else "",
				"phone": row.mobile_no if allow_pii else None,
				"source": row.source,
				"campaign": item_campaign,
				"campaign_title": item_campaign_title,
				"state": state_code,
				"owner": owner,
				"created_at": created_at,
			}
		)

	wi_cnt_sql, wi_cnt_params = _scoped_wi_predicate(scope, "wi")
	wi_cnt_clauses = [wi_cnt_sql]
	if campaign_val:
		wi_cnt_clauses.append("wi.campaign = %s")
		wi_cnt_params.append(campaign_val)
	if batch_val:
		bw_sql, bw_params = work_item_in_batch_clause(batch_val, "wi")
		wi_cnt_clauses.append(bw_sql)
		wi_cnt_params.extend(bw_params)
	wi_cnt_clauses.append(f"wi.lead in (select pool.lead from ({pool_subquery}) pool where {outer_where})")
	wi_cnt_params.extend([*pool_params, *outer_params])

	work_items_count = int(
		frappe.db.sql(
			"select count(*) from `tabCSKH Work Item` wi where " + " and ".join(wi_cnt_clauses),
			tuple(wi_cnt_params),
		)[0][0]
		or 0
	)

	return {
		"scope": scope["scope"],
		"items": page_items,
		"total": unique_customers,
		"unique_customers": unique_customers,
		"work_items": work_items_count,
		"page": page_num,
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
