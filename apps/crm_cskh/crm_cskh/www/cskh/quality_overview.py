"""Context for the P3 Executive Quality Overview page (`/cskh/quality-overview`).

Mirrors the role grant of `crm_cskh.api.reporting.get_bod_executive_overview`
(P3.md §10.1 & §12.1): accessible to `CSKH BOD Viewer` and `CSKH CRM Admin`
with strict `0` raw customer PII exposure for BOD.
"""

from frappe import _

from crm_cskh.www.cskh_context import P3_EXECUTIVE_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/quality-overview",
		title=_("Executive quality overview (BOD)"),
		subtitle=_("Company-wide SLA compliance, connection effectiveness, data reliability and drill-down."),
		allowed_roles=P3_EXECUTIVE_ROLES,
		require_p3=True,
	)
