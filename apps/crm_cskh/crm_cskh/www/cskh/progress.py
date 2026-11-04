"""Context for the CSKH Care Progress page (`/cskh/progress`).

Mirrors the 0-PII role grant of `crm_cskh.api.workflow.progress_summary`
(`docs/api-contract-p2.md` §3 & §4.13): `Sales User` (`self`),
`Sales Manager` (`team`), `CSKH BOD Viewer` (`all`), and `CSKH CRM Admin`
(`all`). Displays scoped operational counts only — never a KPI/SLA score.
"""

from frappe import _

from crm_cskh.www.cskh_context import READ_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/progress",
		title=_("Care progress"),
		subtitle=_("Basic customer care activity counts in your scope (not a quality or SLA score)."),
		allowed_roles=READ_ROLES,
	)
