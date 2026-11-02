"""Context for the CSKH distribution dashboard page.

The page mirrors the role grant of `crm_cskh.api.dashboard.distribution`
(P1 §8.1 and the frozen API contract `api-contract-p1.md` §4): the BOD viewer
sees the whole installation, the CRM Admin the whole installation and a team
leader their own team. A staff member is **not** a dashboard reader: the page
gate mirrors the API, so the navigation never offers a screen that answers
`FORBIDDEN` on every read.

The scope is applied by the backend, never by the page.
"""

from frappe import _

from crm_cskh.www.cskh_context import DASHBOARD_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/dashboard",
		title=_("Distribution dashboard"),
		subtitle=_("Total pool, assigned, unassigned, per team, per user and per campaign."),
		allowed_roles=DASHBOARD_ROLES,
	)
