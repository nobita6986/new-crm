"""P1 API: the Customer Pool view of P1 §9.1 (`crm_cskh.api.pool_ui.list`).

`crm_cskh.api.customers.pool_list` is frozen by T1B for the queued work items
inside a campaign (`SEC-07`, `L-04`, `L-14`). The Customer Pool page of P1 §9.1
(`/cskh/pool`) also shows:
  * canonical leads imported into `CSKH Identity Index` before any campaign
    work item exists (`eligible` -> "Chưa phân phối"),
  * leads already distributed (`assigned` -> "Đã giao", `queued` -> "Trong hàng
    đợi", `revoked` -> "Đã thu hồi", `closed` -> "Đã đóng"),
  * leads held by another campaign or external owner (`conflict` -> "Xung đột"),
  * leads missing a canonical phone identity (`invalid` -> "Lỗi dữ liệu"),
  * server-side filters by `campaign`, `state`, `source`, `batch`, and `search`
    (with PII omitted and `search` blocked for `CSKH BOD Viewer`),
  * separate counters for `unique_customers` (distinct `CRM Lead`) and
    `work_items` (`CSKH Work Item`).

The scope is resolved strictly on the server via `reporting._scope`: no `user`,
`team`, or `scope` parameter is accepted (`SEC-07`). For a team leader
(`scope == "team"`), historical `revoked`/`closed` work items in Team A never
grant visibility into a lead currently active or owned outside Team A (`L-23`),
and pagination runs in SQL (`LIMIT`/`OFFSET`) without loading full tables into
memory.
"""

import frappe
from frappe.utils import now_datetime

from crm_cskh.api._ui_params import parse_optional_str, parse_pagination
from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden, InvalidRequest

POOL_ROLES = {"Sales Manager", "CSKH CRM Admin", "CSKH BOD Viewer"}
ALLOWED_STATES = {"eligible", "queued", "assigned", "conflict", "revoked", "closed", "invalid"}


def _roles(user):
	return set(frappe.get_roles(user))


def _scoped_wi_predicate(scope, alias="wi"):
	"""Return `(sql, params)` restricting `tabCSKH Work Item` `alias` to `scope`."""
	if scope["scope"] in ("system", "all"):
		return "1=1", []
	if scope["scope"] != "team":
		return "1=0", []

	team_nodes = reporting._scope_team_nodes(scope)
	team_users = reporting._team_users(scope["user"])
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
	"""Return `(sql, params)` restricting `tabCSKH Active Claim` `alias` to `scope`."""
	if scope["scope"] in ("system", "all"):
		return "1=1", []
	if scope["scope"] != "team":
		return "1=0", []

	team_nodes = reporting._scope_team_nodes(scope)
	team_users = reporting._team_users(scope["user"])
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
	"""Return `(clauses, params)` enforcing current team authorization on `CRM Lead` (`L-23`)."""
	return reporting._team_lead_authorization_clauses(scope, lead_alias)


def filter_team_authorized_leads(scope, candidate_leads):
	"""Return the subset of `candidate_leads` currently authorized for `scope` (`L-23`)."""
	return reporting.filter_team_authorized_leads(scope, candidate_leads)


def _batch_lead_predicate(batch_val, lead_alias="l"):
	"""Return `(sql, params)` matching leads imported by or allocated under `batch_val` (`L-25`)."""
	return reporting.lead_in_batch_clause(batch_val, f"{lead_alias}.name")


def _batch_wi_predicate(batch_val, wi_alias="wi"):
	"""Return `(sql, params)` matching work items belonging to leads/runs of `batch_val` (`L-25`)."""
	return reporting.work_item_in_batch_clause(batch_val, wi_alias)


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


@frappe.whitelist(methods=["GET"])
def list(page=1, page_size=20, campaign=None, state=None, source=None, batch=None, search=None):
	"""Return the scoped Customer Pool with derived distribution states (P1 §9.1)."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not (_roles(actor) & POOL_ROLES):
			raise Forbidden("You may not read the customer pool.")

	scope = reporting._scope(actor)
	allow_pii = bool(scope.get("pii", True))

	page_num, limit, offset = parse_pagination(page, page_size)

	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	state_val = parse_optional_str(state)
	source_val = parse_optional_str(source)
	batch_val = parse_optional_str(batch)
	search_val = parse_optional_str(search)

	if state_val and state_val not in ALLOWED_STATES:
		raise InvalidRequest("The request is invalid.")
	if search_val and not allow_pii:
		raise Forbidden("You do not have permission to perform this operation.")

	if scope["scope"] not in ("system", "all", "team"):
		return _empty_pool_response(scope["scope"], page_num, limit)

	auth_clauses, auth_params = _team_lead_authorization_clauses(scope, "l")
	if auth_clauses == ["1=0"]:
		return _empty_pool_response(scope["scope"], page_num, limit)

	if scope["scope"] == "team" and campaign_val:
		team_nodes = reporting._scope_team_nodes(scope)
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
		b_sql, b_params = _batch_lead_predicate(batch_val, "l")
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
			if state_code == "assigned":
				owner = row.wi_assigned_to or ""
			elif state_code == "queued":
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
		bw_sql, bw_params = _batch_wi_predicate(batch_val, "wi")
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
