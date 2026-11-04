"""Context for the P3 Team Quality Comparison page (`/cskh/team-quality`).

Mirrors `crm_cskh.api.reporting.get_team_comparison` (P3.md §10.2 & §12.1):
accessible to `CSKH BOD Viewer`, `CSKH CRM Admin`, and `Sales Manager`
(scoped to managed teams on backend).
"""

from frappe import _

from crm_cskh.www.cskh_context import P3_TEAM_QUALITY_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/team-quality",
		title=_("Team quality comparison"),
		subtitle=_(
			"Compare teams by historical team-as-of attribution, SLA compliance, connection and data confidence."
		),
		allowed_roles=P3_TEAM_QUALITY_ROLES,
		require_p3=True,
	)
