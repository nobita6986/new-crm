"""Eligibility rules for campaigns and assignees (P1 7.1, 7.2).

The rules are evaluated again at execute time, not only at preview time
(P1 7.1 #5), so a lead that became ineligible between preview and execute is
skipped with a recorded reason instead of being assigned.

The campaign selection resolves to the **customer pool** (the canonical leads
of `CSKH Identity Index`), not to the existing work items: a lead that already
has a work item in the campaign is *excluded* by `_lead_exclusion_reason`, so
selecting the work items themselves would make every campaign ineligible.
"""

import json

import frappe

from crm_cskh.p1_models import ACTIVE_WORK_ITEM_STATES, OWNED_ACTIVE_WORK_ITEM_STATES

# Reasons are stable strings so the UI can group them.
REASON_NO_IDENTITY = "no_canonical_identity"
REASON_WORK_ITEM_EXISTS = "work_item_exists_in_campaign"
REASON_ACTIVE_CLAIM = "active_claim_in_other_campaign"
REASON_DO_NOT_CONTACT = "do_not_contact"
REASON_OWNED_OUTSIDE = "owned_outside_cskh"
REASON_RULE_MISSING = "no_assignment_rule"
REASON_USER_DISABLED = "user_disabled"
REASON_USER_WRONG_TEAM = "user_out_of_team_scope"
REASON_USER_NOT_SALES = "user_is_not_sales_user"
REASON_NO_WEIGHT = "no_positive_weight"
REASON_CAPACITY_FULL = "capacity_full"


def campaign_team_scope_members(team_scope):
	"""Return `(allowed_users, allowed_nodes)` inside `team_scope`'s hierarchy subtree."""
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


def campaign_rules(campaign, version=None):
	"""Return the enabled rules of a campaign as allocator-shaped dicts.

	The rules are read with `frappe.get_list`, so the row-level permission of
	the caller applies: a leader only receives the rules of their own team
	(P1 §8.2: `frappe.get_all` must not be used to bypass the row-level
	permission).
	"""
	filters = {"campaign": campaign, "enabled": 1}
	if version is not None:
		filters["version"] = version
	rules = frappe.get_list(
		"CSKH Assignment Rule",
		filters=filters,
		fields=["name", "user", "team", "weight", "capacity_limit", "version", "enabled"],
		order_by="user asc",
		limit_page_length=0,
	)
	return [
		{
			"rule": rule.name,
			"user": rule.user,
			"team": rule.team,
			"weight": int(rule.weight or 0),
			"capacity": int(rule.capacity_limit or 0),
			"version": int(rule.version or 1),
			"enabled": bool(rule.enabled),
		}
		for rule in rules
	]


def current_rule_version(campaign):
	"""The highest rule version configured for a campaign."""
	versions = frappe.get_list(
		"CSKH Assignment Rule",
		filters={"campaign": campaign},
		fields=["version"],
		limit_page_length=0,
	)
	return max([int(row.version or 1) for row in versions], default=1)


def capacity_usage(campaign, user):
	"""Active owned work items (`assigned`, `in_progress`, `on_hold`) of `user` in `campaign`."""
	return frappe.db.count(
		"CSKH Work Item",
		{"campaign": campaign, "assigned_to": user, "status": ["in", list(OWNED_ACTIVE_WORK_ITEM_STATES)]},
	)


def eligible_assignees(campaign, team_scope=None, version=None):
	"""Return the assignees that may receive a work item right now.

	Each returned entry carries the reason it is eligible so the UI can explain
	the plan.
	"""
	rules = campaign_rules(campaign, version=version)
	allowed_users, allowed_nodes = campaign_team_scope_members(team_scope)
	eligible = []
	excluded = []
	for rule in rules:
		reason = _assignee_exclusion_reason(
			rule, team_scope, campaign, allowed_users=allowed_users, allowed_nodes=allowed_nodes
		)
		if reason:
			excluded.append({"user": rule["user"], "weight": rule["weight"], "reason": reason})
			continue
		rule["used"] = capacity_usage(campaign, rule["user"])
		rule["remaining"] = (rule["capacity"] - rule["used"]) if rule["capacity"] else None
		eligible.append(rule)
	return eligible, excluded


def _assignee_exclusion_reason(rule, team_scope, campaign, allowed_users=None, allowed_nodes=None):
	user = rule["user"]
	if not frappe.db.get_value("User", user, "enabled"):
		return REASON_USER_DISABLED
	if "Sales User" not in frappe.get_roles(user):
		return REASON_USER_NOT_SALES
	if not rule["weight"] or rule["weight"] <= 0:
		return REASON_NO_WEIGHT
	if team_scope:
		if allowed_users is None or allowed_nodes is None:
			allowed_users, allowed_nodes = campaign_team_scope_members(team_scope)
		if user not in allowed_users:
			return REASON_USER_WRONG_TEAM
		if rule.get("team") and rule["team"] not in allowed_nodes:
			return REASON_USER_WRONG_TEAM
	if rule["capacity"] and capacity_usage(campaign, user) >= rule["capacity"]:
		return REASON_CAPACITY_FULL
	return None


def eligible_leads(campaign, limit=None):
	"""Return the leads that can enter the campaign, with the reason for exclusion.

	Returns a dict with `eligible` (list of lead names), `excluded` (list of
	`{lead, reason}`) and `counts`.
	"""
	campaign_doc = frappe.db.get_value(
		"CSKH Campaign", campaign, ["name", "team_scope", "status"], as_dict=True
	)
	if not campaign_doc:
		return {"eligible": [], "excluded": [], "counts": {}}

	selection = frappe.db.get_value("CSKH Campaign", campaign, "selection_json")
	leads = _selection_leads(campaign, selection)

	eligible = []
	excluded = []
	for lead in leads:
		reason = _lead_exclusion_reason(campaign, lead)
		if reason:
			excluded.append({"lead": lead, "reason": reason})
		else:
			eligible.append(lead)
		if limit and len(eligible) >= limit:
			break

	return {
		"eligible": eligible,
		"excluded": excluded,
		"counts": {
			"considered": len(leads),
			"eligible": len(eligible),
			"excluded": len(excluded),
		},
	}


def _selection_leads(campaign, selection_json):
	"""Resolve the campaign selection into a list of lead names.

	Supported `selection_json` shapes (all optional):

	* `{"leads": ["CRM-LEAD-0001", ...]}` — an explicit list;
	* `{"source": "Facebook"}` — every canonical lead of that source;
	* `{}` (empty) — the whole customer pool of the site.

	The pool is the set of canonical leads known to `CSKH Identity Index`
	(ADR-P1-01). It is deliberately *not* derived from the work items: a lead
	that already has a work item in this campaign is excluded from the
	selection by `_lead_exclusion_reason`.
	"""
	selection = {}
	if selection_json:
		try:
			selection = json.loads(selection_json)
		except (ValueError, TypeError):
			selection = {}
	if not isinstance(selection, dict):
		selection = {}

	if selection.get("leads"):
		return list(selection["leads"])

	source = selection.get("source")
	if source:
		rows = frappe.db.sql(
			"""select ii.canonical_lead as lead
			   from `tabCSKH Identity Index` ii
			   inner join `tabCRM Lead` l on l.name = ii.canonical_lead
			   where l.source = %s
			   order by ii.canonical_lead asc""",
			(source,),
			pluck=True,
		)
		return list(rows)

	return frappe.db.sql(
		"select canonical_lead as lead from `tabCSKH Identity Index` order by canonical_lead asc",
		pluck=True,
	)


def selection_leads(campaign):
	"""Return **every** lead of the campaign selection, without any filter.

	The filtered list (`eligible_leads`) is what a **preview** works on. The
	resume path of a crashed distribution run needs the **unfiltered** list:
	the leads that a previous attempt already assigned must be visible, so the
	retry can recognise them and skip them instead of creating a second work
	item (P1 §12.4).
	"""
	campaign_doc = frappe.db.get_value("CSKH Campaign", campaign, "selection_json")
	return _selection_leads(campaign, campaign_doc)


def _lead_exclusion_reason(campaign, lead):
	from crm_cskh.services import identity as identity_service

	if not identity_service.find_by_lead(lead):
		return REASON_NO_IDENTITY
	if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and int(
		frappe.db.get_value("CRM Lead", lead, "cskh_do_not_contact") or 0
	):
		return REASON_DO_NOT_CONTACT
	if frappe.db.exists("CSKH Work Item", {"campaign": campaign, "lead": lead}):
		return REASON_WORK_ITEM_EXISTS
	claim = frappe.db.get_value("CSKH Active Claim", {"lead": lead}, ["campaign", "work_item"], as_dict=True)
	if claim and claim.campaign != campaign:
		return REASON_ACTIVE_CLAIM
	if claim and claim.campaign == campaign:
		item = frappe.db.get_value("CSKH Work Item", claim.work_item, "status")
		if item not in ACTIVE_WORK_ITEM_STATES:
			return REASON_WORK_ITEM_EXISTS
	if frappe.db.exists(
		"CSKH Work Item",
		{
			"lead": lead,
			"campaign": ["!=", campaign],
			"status": ["in", list(OWNED_ACTIVE_WORK_ITEM_STATES)],
		},
	):
		return REASON_ACTIVE_CLAIM
	return None
