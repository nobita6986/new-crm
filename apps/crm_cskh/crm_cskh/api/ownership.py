"""P1 API: ownership transfer and revocation.

Every mutation goes through `crm_cskh.services.ownership`, which enforces the
permission, the expected-owner check, the transaction, the idempotency key and
the audit trail (P1 7.6, 7.7).
"""

import frappe

from crm_cskh.services import ownership
from crm_cskh.services.errors import Forbidden, InvalidRequest, P1Error


def _require_owner_permission(user=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return
	if "CSKH CRM Admin" in frappe.get_roles(user):
		return
	raise Forbidden("Transferring a customer requires the CRM Admin business role.")


@frappe.whitelist(methods=["POST"])
def assign(work_item=None, target_user=None, reason=None, request_id=None):
	"""Assign a queued work item."""
	actor = frappe.session.user
	_require_owner_permission(actor)
	if not work_item or not target_user:
		raise InvalidRequest("The work item and the target user are required.")
	try:
		return ownership.assign(work_item, target_user, actor=actor, reason=reason, request_id=request_id)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["POST"])
def reassign(work_item=None, expected_owner=None, target_user=None, reason=None, request_id=None):
	"""Transfer a work item to another owner."""
	actor = frappe.session.user
	_require_owner_permission(actor)
	if not work_item or not target_user:
		raise InvalidRequest("The work item and the target user are required.")
	try:
		return ownership.reassign(
			work_item,
			expected_owner=expected_owner,
			target_user=target_user,
			actor=actor,
			reason=reason,
			request_id=request_id,
		)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["POST"])
def revoke(work_item=None, expected_owner=None, reason=None, request_id=None, return_to_pool=True):
	"""Revoke the ownership of a work item."""
	actor = frappe.session.user
	_require_owner_permission(actor)
	if not work_item:
		raise InvalidRequest("The work item is required.")
	try:
		return ownership.revoke(
			work_item,
			expected_owner=expected_owner,
			actor=actor,
			reason=reason,
			request_id=request_id,
			return_to_pool=bool(return_to_pool),
		)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["GET"])
def history(work_item=None, limit=50):
	"""Return the append-only assignment history of a work item."""
	actor = frappe.session.user
	if not work_item:
		raise InvalidRequest("The work item is required.")
	limit = max(1, min(int(limit or 50), 200))
	if actor != "Administrator" and "System Manager" not in frappe.get_roles(actor):
		if not frappe.has_permission("CSKH Work Item", "read", doc=work_item, throw=False):
			raise Forbidden("You may not read this assignment history.")
	rows = frappe.get_all(
		"CSKH Assignment History",
		filters={"work_item": work_item},
		fields=[
			"name",
			"action",
			"from_user",
			"to_user",
			"actor",
			"event_at",
			"reason",
			"distribution_run",
			"ownership_start",
			"ownership_end",
			"event_id",
		],
		order_by="event_at asc",
		limit=limit,
	)
	return {"work_item": work_item, "items": rows, "total": len(rows)}
