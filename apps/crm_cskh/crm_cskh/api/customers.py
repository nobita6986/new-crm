"""P1 API: scoped customer lists (My Customers and the Customer Pool).

The scope is decided on the server: the caller never sends a `user` or `team`
parameter that could widen it (ADR-P1-07, P1 10.1).
"""

import frappe

from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden

READ_ROLES = {"Sales User", "Sales Manager", "CSKH CRM Admin", "CSKH BOD Viewer"}
POOL_ROLES = {"Sales Manager", "CSKH CRM Admin", "CSKH BOD Viewer"}


def _roles(user):
	return set(frappe.get_roles(user))


@frappe.whitelist(methods=["GET"])
def my_list(page=1, page_size=20, status=None, campaign=None):
	"""The customers currently assigned to the caller."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not _roles(actor) & READ_ROLES:
			raise Forbidden("You may not read the customer list.")
	return reporting.my_list(actor, page=page, page_size=page_size, status=status, campaign=campaign)


@frappe.whitelist(methods=["GET"])
def pool_list(page=1, page_size=20, campaign=None, batch=None):
	"""The unassigned queued work-item pool inside the caller's scope."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not _roles(actor) & POOL_ROLES:
			raise Forbidden("You may not read the customer pool.")
	return reporting.pool_list(actor, page=page, page_size=page_size, campaign=campaign, batch=batch)


@frappe.whitelist(methods=["GET"])
def customer_pool(
	page=1,
	page_size=20,
	campaign=None,
	state=None,
	source=None,
	batch=None,
	search=None,
):
	"""The scoped Customer Pool view with derived states and `batch` filtering (P1 §9.1, L-25)."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not _roles(actor) & POOL_ROLES:
			raise Forbidden("You may not read the customer pool.")
	return reporting.customer_pool(
		actor,
		page=page,
		page_size=page_size,
		campaign=campaign,
		state=state,
		source=source,
		batch=batch,
		search=search,
	)
