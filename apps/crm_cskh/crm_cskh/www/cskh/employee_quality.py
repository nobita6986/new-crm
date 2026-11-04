"""Context for the P3 Employee Quality & Compliance page (`/cskh/employee-quality`).

Mirrors `crm_cskh.api.reporting.get_employee_quality_table` (P3.md §10.3 & §12.1):
accessible to `CSKH BOD Viewer`, `CSKH CRM Admin`, and `Sales Manager`
(scoped to managed team members on backend).
"""

from frappe import _

from crm_cskh.www.cskh_context import P3_EMPLOYEE_QUALITY_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/employee-quality",
		title=_("Employee quality & compliance"),
		subtitle=_(
			"Evaluate staff across process compliance, connection and data reliability without mixing provisional and finalized ranks."
		),
		allowed_roles=P3_EMPLOYEE_QUALITY_ROLES,
		require_p3=True,
	)
