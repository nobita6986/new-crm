"""P1 API: the BOD distribution dashboard (P1 9.5).

The dashboard never returns the PII of a lead and never returns a
`System Manager` power to a BOD account: `CSKH BOD Viewer` is a business role
that only unlocks the aggregated read-only view (ADR-P1-07).
"""

import frappe

from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden

DASHBOARD_ROLES = {"CSKH BOD Viewer", "CSKH CRM Admin", "Sales Manager"}


def _roles(user):
	return set(frappe.get_roles(user))


@frappe.whitelist(methods=["GET"])
def distribution(campaign=None, date_from=None, date_to=None):
	"""The aggregated distribution numbers of the caller's scope."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not _roles(actor) & DASHBOARD_ROLES:
			raise Forbidden("You may not read the distribution dashboard.")
	return reporting.distribution_dashboard(actor, campaign=campaign, date_from=date_from, date_to=date_to)


@frappe.whitelist(methods=["GET"])
def campaigns():
	"""The campaigns usable as a dashboard filter."""
	actor = frappe.session.user
	if actor != "Administrator" and "System Manager" not in _roles(actor):
		if not _roles(actor) & DASHBOARD_ROLES:
			raise Forbidden("You may not read the campaign list.")
	return reporting.campaign_summary(actor)
