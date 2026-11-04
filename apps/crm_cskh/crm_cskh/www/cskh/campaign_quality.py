"""Context for the P3 Campaign & Lead Source Performance page (`/cskh/campaign-quality`).

Mirrors `crm_cskh.api.reporting.get_campaign_quality_report` (P3.md §10.4 & §12.1):
accessible to `CSKH BOD Viewer`, `CSKH CRM Admin`, and `Sales Manager`.
"""

from frappe import _

from crm_cskh.www.cskh_context import P3_CAMPAIGN_QUALITY_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/campaign-quality",
		title=_("Campaign & lead source performance"),
		subtitle=_(
			"Separate lead source quality (invalid/DNC) from staff SLA execution across versioned policies."
		),
		allowed_roles=P3_CAMPAIGN_QUALITY_ROLES,
		require_p3=True,
	)
