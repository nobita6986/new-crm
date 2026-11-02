"""P1 API: campaigns and assignment rules.

The CRM Admin business role manages the campaigns and the weighted rules; the
BOD viewer may read them; a leader may read the campaigns of their own team
(ADR-P1-07).
"""

import json

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import eligibility
from crm_cskh.services.errors import Forbidden, InvalidRequest, NotFound


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_read(user=None):
	user = user or frappe.session.user
	if _is_system(user):
		return
	if {"CSKH CRM Admin", "CSKH BOD Viewer", "Sales Manager"} & set(frappe.get_roles(user)):
		return
	raise Forbidden("You may not read the campaigns.")


def _require_write(user=None):
	user = user or frappe.session.user
	if _is_system(user):
		return
	if "CSKH CRM Admin" in frappe.get_roles(user):
		return
	raise Forbidden("Managing the campaigns requires the CRM Admin business role.")


@frappe.whitelist(methods=["GET"])
def list(status=None, campaign=None):
	"""List the campaigns visible to the caller."""
	actor = frappe.session.user
	_require_read(actor)
	filters = {}
	if status:
		filters["status"] = status
	rows = frappe.get_list(
		"CSKH Campaign",
		filters=filters,
		fields=[
			"name",
			"code",
			"title",
			"description",
			"status",
			"team_scope",
			"rule_version",
			"started_at",
			"ended_at",
		],
		order_by="code asc",
		limit_page_length=0,
	)
	items = []
	for row in rows:
		items.append(
			{
				"campaign": row.name,
				"code": row.code,
				"title": row.title,
				"description": row.description,
				"status": row.status,
				"team_scope": row.team_scope,
				"rule_version": row.rule_version,
				"started_at": row.started_at,
				"ended_at": row.ended_at,
			}
		)
	return {"items": items, "total": len(items), "as_of": now_datetime()}


@frappe.whitelist(methods=["POST"])
def save(
	code=None, title=None, description=None, status=None, team_scope=None, campaign=None, selection=None
):
	"""Create or update a campaign. The server validates every field."""
	actor = frappe.session.user
	_require_write(actor)
	if not title:
		raise InvalidRequest("The campaign title is required.")
	if status is None:
		status = "Draft"
	if status not in ("Draft", "Active", "Paused", "Closed"):
		raise InvalidRequest("The campaign status is not supported.")

	if campaign:
		doc = frappe.get_doc("CSKH Campaign", campaign)
	else:
		if not code:
			raise InvalidRequest("The campaign code is required.")
		doc = frappe.get_doc({"doctype": "CSKH Campaign", "code": code, "title": title})

	doc.title = title
	doc.description = description
	doc.status = status
	doc.team_scope = team_scope
	if selection is not None:
		doc.selection_json = (
			selection if isinstance(selection, str) else json.dumps(selection, ensure_ascii=False)
		)
	doc.save(ignore_permissions=True)
	return {
		"campaign": doc.name,
		"code": doc.code,
		"status": doc.status,
		"rule_version": doc.rule_version,
	}


@frappe.whitelist(methods=["GET"])
def rules(campaign=None):
	"""List the assignment rules of a campaign with their eligibility status."""
	actor = frappe.session.user
	_require_read(actor)
	if not campaign:
		raise InvalidRequest("The campaign is required.")
	team_scope = frappe.db.get_value("CSKH Campaign", campaign, "team_scope")
	version = eligibility.current_rule_version(campaign)
	eligible, excluded = eligibility.eligible_assignees(campaign, team_scope, version)
	return {
		"campaign": campaign,
		"rule_version": version,
		"eligible": eligible,
		"excluded": excluded,
		"as_of": now_datetime(),
	}


@frappe.whitelist(methods=["POST"])
def save_rule(campaign=None, user=None, weight=None, capacity_limit=0, team=None, enabled=1, version=None):
	"""Create or update the rule of one assignee in a campaign."""
	actor = frappe.session.user
	_require_write(actor)
	if not campaign or not user:
		raise InvalidRequest("The campaign and the assignee are required.")
	if not frappe.db.exists("CSKH Campaign", campaign):
		raise NotFound("The campaign does not exist.")
	try:
		weight = int(weight)
	except (TypeError, ValueError) as exc:
		raise InvalidRequest("The weight must be an integer.") from exc
	if weight <= 0:
		raise InvalidRequest("The weight must be positive.")
	try:
		capacity_limit = int(capacity_limit or 0)
	except (TypeError, ValueError) as exc:
		raise InvalidRequest("The capacity must be an integer.") from exc
	if capacity_limit < 0:
		raise InvalidRequest("The capacity must not be negative.")

	existing = frappe.db.get_value(
		"CSKH Assignment Rule",
		{"campaign": campaign, "user": user, "version": int(version or 1)},
		"name",
	)
	if existing:
		doc = frappe.get_doc("CSKH Assignment Rule", existing)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": campaign,
				"user": user,
				"version": int(version or 1),
			}
		)
	doc.weight = weight
	doc.capacity_limit = capacity_limit
	doc.team = team
	doc.enabled = 1 if enabled else 0
	doc.save(ignore_permissions=True)

	# The rule version of the campaign is bumped so an old preview is refused.
	campaign_doc = frappe.get_doc("CSKH Campaign", campaign)
	campaign_doc.rule_version = int(campaign_doc.rule_version or 1) + 1
	campaign_doc.save(ignore_permissions=True)

	return {
		"rule": doc.name,
		"campaign": campaign,
		"user": user,
		"weight": weight,
		"capacity_limit": capacity_limit,
		"enabled": bool(enabled),
		"campaign_rule_version": campaign_doc.rule_version,
	}
