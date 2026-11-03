"""P1 API: imports (preview -> confirm -> status).

Every endpoint is a POST for mutations and a GET for reads (P1 8.2), validates
the actor's permission on the server side, and returns a stable error code
from `crm_cskh.services.errors`.
"""

import frappe

from crm_cskh.services import import_service
from crm_cskh.services.errors import Forbidden, InvalidRequest, P1Error


def _require_import_permission(user=None):
	"""Only the CRM Admin business role may import (P1 8.1)."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return
	if "CSKH CRM Admin" in frappe.get_roles(user):
		return
	raise Forbidden("Importing the customer pool requires the CRM Admin business role.")


@frappe.whitelist(methods=["POST"])
def create_preview(file_name=None, mapping=None, idempotency_key=None, import_mode="standard"):
	"""Create an import preview from an already uploaded private file."""
	actor = frappe.session.user
	_require_import_permission(actor)
	if not file_name:
		raise InvalidRequest("The uploaded file is required.")
	parsed_mapping = frappe.parse_json(mapping) if isinstance(mapping, str) else mapping
	try:
		return import_service.create_preview(
			file_name=file_name,
			mapping=parsed_mapping,
			actor=actor,
			idempotency_key=idempotency_key,
			import_mode=import_mode,
		)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["POST"])
def confirm(batch=None):
	"""Confirm a preview and enqueue the import worker."""
	actor = frappe.session.user
	_require_import_permission(actor)
	if not batch:
		raise InvalidRequest("The import batch is required.")
	return import_service.confirm(batch, actor=actor)


@frappe.whitelist(methods=["GET"])
def get_status(batch=None):
	"""Return the status and totals of an import batch."""
	actor = frappe.session.user
	if not batch:
		raise InvalidRequest("The import batch is required.")
	role = frappe.get_roles(actor)
	if actor != "Administrator" and "System Manager" not in role and "CSKH CRM Admin" not in role:
		raise Forbidden("Only the CRM Admin may read an import batch.")
	return import_service._status_response(batch)
