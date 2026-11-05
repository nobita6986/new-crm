"""Context for the P3 Personal Scorecard page (`/cskh/scorecard`).

Mirrors `crm_cskh.api.reporting.get_my_scorecard` (P3.md §10.6 & §12.1):
accessible to `Sales User` (own scope only, never peer ranks), `Sales Manager`,
and `CSKH CRM Admin`. Denied to `CSKH BOD Viewer` (who uses executive/employee views).
"""

from frappe import _

from crm_cskh.www.cskh_context import P3_SCORECARD_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/scorecard",
		title=_("My quality scorecard"),
		subtitle=_("Personal SLA obligations, component breakdown and progress in your own scope."),
		allowed_roles=P3_SCORECARD_ROLES,
		require_p3=True,
	)
