"""Context for the P3 SLA & QA Operations page (`/cskh/sla-operations`).

Mirrors `crm_cskh.api.sla` & `crm_cskh.api.reporting` SLA operations (P3.md §10.5 & §12.1):
accessible to `CSKH BOD Viewer` (0-PII read-only), `CSKH CRM Admin`, and `Sales Manager`.
"""

from frappe import _

from crm_cskh.www.cskh_context import P3_SLA_OPS_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/sla-operations",
		title=_("SLA & QA operations center"),
		subtitle=_(
			"Inspect overdue, breached, waived and escalated SLA obligations, QA status and audit actions."
		),
		allowed_roles=P3_SLA_OPS_ROLES,
		require_p3=True,
	)
