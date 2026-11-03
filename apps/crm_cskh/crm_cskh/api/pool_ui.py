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
`team`, or `scope` parameter is accepted (`SEC-07`).
"""

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden, InvalidRequest

POOL_ROLES = {"Sales Manager", "CSKH CRM Admin", "CSKH BOD Viewer"}
ALLOWED_STATES = {"eligible", "queued", "assigned", "conflict", "revoked", "closed", "invalid"}


def _roles(user):
	return set(frappe.get_roles(user))


def _derive_lead_state(lead_row, wi_rows, claim, campaign_titles):
	"""Compute `(state, campaign, campaign_title, owner, work_item, created_at)` in memory."""
	has_identity = bool(lead_row.has_identity)

	if not wi_rows and not claim and lead_row.lead_owner and lead_row.lead_owner not in ("", "Administrator"):
		return "conflict", "", "", lead_row.lead_owner, None, lead_row.creation

	if not has_identity and not wi_rows:
		return "invalid", "", "", lead_row.lead_owner or "", None, lead_row.creation

	for row in wi_rows:
		c_title = campaign_titles.get(row.campaign, row.campaign or "")
		if row.status == "assigned" and row.assigned_to:
			return "assigned", row.campaign or "", c_title, row.assigned_to, row.work_item, row.created_at
		if row.status == "queued" and not row.assigned_to:
			return "queued", row.campaign or "", c_title, "", row.work_item, row.created_at
		if row.status == "revoked":
			return (
				"revoked",
				row.campaign or "",
				c_title,
				row.assigned_to or "",
				row.work_item,
				row.created_at,
			)
		if row.status == "closed":
			return (
				"closed",
				row.campaign or "",
				c_title,
				row.assigned_to or "",
				row.work_item,
				row.created_at,
			)

	if claim and claim.campaign:
		c_title = campaign_titles.get(claim.campaign, claim.campaign)
		created = wi_rows[0].created_at if wi_rows else lead_row.creation
		return "conflict", claim.campaign, c_title, lead_row.lead_owner or "", claim.work_item, created

	if lead_row.lead_owner and lead_row.lead_owner not in ("", "Administrator"):
		return "conflict", "", "", lead_row.lead_owner, None, lead_row.creation

	if not has_identity:
		return "invalid", "", "", lead_row.lead_owner or "", None, lead_row.creation

	created = wi_rows[0].created_at if wi_rows else lead_row.creation
	return "eligible", "", "", "", None, created


@frappe.whitelist(methods=["GET"])
def list(page=1, page_size=20, campaign=None, state=None, source=None, batch=None, search=None):
	"""Return the scoped Customer Pool with derived distribution states (P1 §9.1)."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not (_roles(actor) & POOL_ROLES):
			raise Forbidden("You may not read the customer pool.")

	scope = reporting._scope(actor)
	allow_pii = bool(scope.get("pii", True))

	limit = max(1, min(int(page_size or 20), 100))
	page_num = max(1, int(page or 1))
	offset = (page_num - 1) * limit

	campaign_val = (campaign or "").strip() if isinstance(campaign, str) else ""
	state_val = (state or "").strip() if isinstance(state, str) else ""
	source_val = (source or "").strip() if isinstance(source, str) else ""
	batch_val = (batch or "").strip() if isinstance(batch, str) else ""
	search_val = (search or "").strip() if isinstance(search, str) else ""

	if state_val and state_val not in ALLOWED_STATES:
		raise InvalidRequest("The request is invalid.")
	if search_val and not allow_pii:
		raise Forbidden("You do not have permission to perform this operation.")

	lead_clauses = ["1=1"]
	lead_params = []

	wi_clauses = ["1=1"]
	wi_params = []

	if scope["scope"] in ("system", "all"):
		if campaign_val:
			lead_clauses.append(
				"exists (select 1 from `tabCSKH Work Item` wi_f where wi_f.lead = l.name and wi_f.campaign = %s)"
			)
			lead_params.append(campaign_val)
			wi_clauses.append("wi.campaign = %s")
			wi_params.append(campaign_val)
		if batch_val:
			lead_clauses.append(
				"exists (select 1 from `tabCSKH Work Item` wi_b "
				"where wi_b.lead = l.name and (wi_b.import_batch = %s or wi_b.allocation_run = %s))"
			)
			lead_params.extend([batch_val, batch_val])
			wi_clauses.append("(wi.import_batch = %s or wi.allocation_run = %s)")
			wi_params.extend([batch_val, batch_val])
	elif scope["scope"] == "team":
		pool_clause, pool_params = reporting._pool_scope_filter(scope, "wi_s")
		team_users = reporting._team_users(scope["user"])
		if not pool_clause or pool_clause == "1=0" or not team_users:
			return {
				"scope": scope["scope"],
				"items": [],
				"total": 0,
				"unique_customers": 0,
				"work_items": 0,
				"page": page_num,
				"page_size": limit,
				"as_of": now_datetime(),
			}
		user_ph = ",".join(["%s"] * len(team_users))
		sub_clauses = [
			"wi_s.lead = l.name",
			f"({pool_clause})",
			f"(wi_s.assigned_to is null or wi_s.assigned_to in ({user_ph}))",
		]
		sub_params = [*pool_params, *team_users]

		wi_pool_clause, wi_pool_params = reporting._pool_scope_filter(scope, "wi")
		wi_clauses.append(f"({wi_pool_clause})")
		wi_params.extend(wi_pool_params)
		wi_clauses.append(f"(wi.assigned_to is null or wi.assigned_to in ({user_ph}))")
		wi_params.extend(team_users)

		if campaign_val:
			sub_clauses.append("wi_s.campaign = %s")
			sub_params.append(campaign_val)
			wi_clauses.append("wi.campaign = %s")
			wi_params.append(campaign_val)
		if batch_val:
			sub_clauses.append("(wi_s.import_batch = %s or wi_s.allocation_run = %s)")
			sub_params.extend([batch_val, batch_val])
			wi_clauses.append("(wi.import_batch = %s or wi.allocation_run = %s)")
			wi_params.extend([batch_val, batch_val])

		lead_clauses.append(
			"exists (select 1 from `tabCSKH Work Item` wi_s where " + " and ".join(sub_clauses) + ")"
		)
		lead_params.extend(sub_params)
	else:
		return {
			"scope": scope["scope"],
			"items": [],
			"total": 0,
			"unique_customers": 0,
			"work_items": 0,
			"page": page_num,
			"page_size": limit,
			"as_of": now_datetime(),
		}

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

	candidate_rows = frappe.db.sql(
		"""select l.name as name, l.lead_name as lead_name, l.first_name as first_name,
		          l.mobile_no as mobile_no, l.source as source, l.lead_owner as lead_owner,
		          l.creation as creation,
		          exists(select 1 from `tabCSKH Identity Index` ii where ii.canonical_lead = l.name) as has_identity
		   from `tabCRM Lead` l
		   where """
		+ " and ".join(lead_clauses)
		+ " order by l.creation desc",
		tuple(lead_params),
		as_dict=True,
	)

	if not candidate_rows:
		return {
			"scope": scope["scope"],
			"items": [],
			"total": 0,
			"unique_customers": 0,
			"work_items": 0,
			"page": page_num,
			"page_size": limit,
			"as_of": now_datetime(),
		}

	campaign_rows = frappe.db.sql(
		"select name, title, code from `tabCSKH Campaign`",
		as_dict=True,
	)
	campaign_titles = {row.name: (row.title or row.code or row.name) for row in campaign_rows}

	all_wi_rows = frappe.db.sql(
		"""select wi.lead as lead, wi.name as work_item, wi.campaign as campaign,
		          wi.status as status, wi.assigned_to as assigned_to, wi.creation as created_at
		   from `tabCSKH Work Item` wi
		   order by wi.creation desc""",
		as_dict=True,
	)
	wi_by_lead = {}
	for row in all_wi_rows:
		wi_by_lead.setdefault(row.lead, []).append(row)

	all_claim_rows = frappe.db.sql(
		"select lead, campaign, work_item from `tabCSKH Active Claim`",
		as_dict=True,
	)
	claim_by_lead = {row.lead: row for row in all_claim_rows}

	matched = []
	for lead_row in candidate_rows:
		derived_state, item_campaign, item_campaign_title, owner, work_item, created_at = _derive_lead_state(
			lead_row,
			wi_by_lead.get(lead_row.name, []),
			claim_by_lead.get(lead_row.name),
			campaign_titles,
		)
		if state_val and derived_state != state_val:
			continue
		display = lead_row.lead_name or lead_row.first_name or ""
		matched.append(
			{
				"work_item": work_item,
				"lead": lead_row.name,
				"display_name": display if allow_pii else "",
				"phone": lead_row.mobile_no if allow_pii else None,
				"source": lead_row.source,
				"campaign": item_campaign,
				"campaign_title": item_campaign_title,
				"state": derived_state,
				"owner": owner,
				"created_at": created_at or lead_row.creation,
			}
		)

	unique_customers = len(matched)
	page_items = matched[offset : offset + limit]

	if state_val or source_val or search_val:
		lead_names = [row["lead"] for row in matched]
		if lead_names:
			lead_ph = ",".join(["%s"] * len(lead_names))
			work_items_count = frappe.db.sql(
				"select count(*) from `tabCSKH Work Item` wi where "
				+ " and ".join([*wi_clauses, f"wi.lead in ({lead_ph})"]),
				(*wi_params, *lead_names),
			)[0][0]
		else:
			work_items_count = 0
	else:
		work_items_count = frappe.db.sql(
			"select count(*) from `tabCSKH Work Item` wi where " + " and ".join(wi_clauses),
			tuple(wi_params),
		)[0][0]

	return {
		"scope": scope["scope"],
		"items": page_items,
		"total": unique_customers,
		"unique_customers": unique_customers,
		"work_items": int(work_items_count or 0),
		"page": page_num,
		"page_size": limit,
		"as_of": now_datetime(),
	}
