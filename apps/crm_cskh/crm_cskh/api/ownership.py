"""P1 API: ownership transfer and revocation.

Every mutation goes through `crm_cskh.services.ownership`, which enforces the
permission, the expected-owner check, the transaction, the idempotency key and
the audit trail (P1 7.6, 7.7).
"""

import frappe

from crm_cskh.api._params import parse_bool, parse_int, parse_optional_str
from crm_cskh.services import ownership
from crm_cskh.services.errors import Forbidden, InvalidRequest, P1Error, is_transient_tx_abort


def _require_owner_permission(user=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return
	if "CSKH CRM Admin" in frappe.get_roles(user):
		return
	raise Forbidden("Transferring a customer requires the CRM Admin business role.")


def _can_read_private_history_reason(user: str) -> bool:
	"""True when `user` has `permlevel = 1` read access on `CSKH Assignment History` (`F-P2-01`)."""
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	return bool({"System Manager", "CSKH CRM Admin", "Sales Manager", "Sales User"} & roles)


def _run_with_tx_retry(fn, max_retries: int = 3):
	"""Retry top-level ownership RPC mutations on transient MariaDB `1213` / `1020` aborts."""
	attempt = 0
	while True:
		try:
			return fn()
		except Exception as exc:
			if is_transient_tx_abort(exc) and attempt < max_retries:
				attempt += 1
				frappe.db.rollback()
				continue
			raise


@frappe.whitelist(methods=["POST"])
def assign(work_item=None, target_user=None, reason=None, request_id=None):
	"""Assign a queued work item."""
	actor = frappe.session.user
	_require_owner_permission(actor)
	if not work_item or not target_user:
		raise InvalidRequest("The work item and the target user are required.")
	try:
		return _run_with_tx_retry(
			lambda: ownership.assign(
				work_item, target_user, actor=actor, reason=reason, request_id=request_id
			)
		)
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
		return _run_with_tx_retry(
			lambda: ownership.reassign(
				work_item,
				expected_owner=expected_owner,
				target_user=target_user,
				actor=actor,
				reason=reason,
				request_id=request_id,
			)
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
	parsed_return_to_pool = parse_bool(return_to_pool, default=True, field="return_to_pool")
	try:
		return _run_with_tx_retry(
			lambda: ownership.revoke(
				work_item,
				expected_owner=expected_owner,
				actor=actor,
				reason=reason,
				request_id=request_id,
				return_to_pool=parsed_return_to_pool,
			)
		)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["GET"])
def history(work_item=None, limit=50):
	"""Return the append-only assignment history of a work item (`F-P2-01`)."""
	actor = frappe.session.user
	work_item_val = parse_optional_str(work_item, "The work item must be a string.")
	if not work_item_val:
		raise InvalidRequest("The work item is required.")
	parsed_limit = parse_int(limit, default=50, minimum=1, maximum=200, field="limit")
	if actor != "Administrator" and "System Manager" not in frappe.get_roles(actor):
		if not frappe.has_permission("CSKH Work Item", "read", doc=work_item_val, throw=False):
			raise Forbidden("You may not read this assignment history.")

	can_read_private = _can_read_private_history_reason(actor)
	query_fields = [
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
	]
	if can_read_private and frappe.db.has_column("CSKH Assignment History", "private_reason"):
		query_fields.append("private_reason")

	raw_rows = frappe.get_list(
		"CSKH Assignment History",
		filters={"work_item": work_item_val},
		fields=query_fields,
		order_by="event_at asc",
		limit_page_length=parsed_limit,
	)
	rows = []
	for raw in raw_rows:
		item = dict(raw)
		if can_read_private:
			item["reason"] = ownership.display_history_reason(item)
		else:
			item["reason"] = ownership.public_history_reason_from_row(item)
		item.pop("private_reason", None)
		rows.append(item)
	return {"work_item": work_item_val, "items": rows, "total": len(rows)}
