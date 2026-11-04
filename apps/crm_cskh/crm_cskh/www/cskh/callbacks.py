"""Context for the CSKH Callbacks Schedule page (`/cskh/callbacks`).

Mirrors the PII role grant of `crm_cskh.api.workflow.list_followups`
(`docs/api-contract-p2.md` §3): `Sales User`, `Sales Manager` and
`CSKH CRM Admin` see callbacks within their server-resolved scope, while
`Guest` and `CSKH BOD Viewer` are denied with HTTP 403.
"""

from frappe import _

from crm_cskh.www.cskh_context import WORKFLOW_PII_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/callbacks",
		title=_("Callbacks schedule"),
		subtitle=_("Follow-up callbacks in your scope, classified by site/user timezone."),
		allowed_roles=WORKFLOW_PII_ROLES,
	)
