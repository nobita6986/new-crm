"""Context for the CSKH Customer Care Workspace (`/cskh/work-item`).

Mirrors the PII role grant of `crm_cskh.api.workflow.get_work_item` and
`crm_cskh.api.workflow.list_attempts` (`docs/api-contract-p2.md` §3):
`Sales User`, `Sales Manager` and `CSKH CRM Admin` may load the page, while
`Guest` and `CSKH BOD Viewer` are denied with HTTP 403. Row-level authorization
(active owner / team subtree) is enforced by the backend APIs on every read and
mutation.
"""

from frappe import _

from crm_cskh.www.cskh_context import WORKFLOW_PII_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/work-item",
		title=_("Customer care workspace"),
		subtitle=_("Customer care detail, unified timeline, callbacks and append-only corrections."),
		allowed_roles=WORKFLOW_PII_ROLES,
	)
