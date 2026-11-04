"""Context for the CSKH Outcome Code & Policy Settings page (`/cskh/settings`).

`CSKH CRM Admin` has full create/update/disable rights via
`crm_cskh.api.settings.save_outcome` and `save_policy`. `CSKH BOD Viewer` and
`Sales Manager` have read-only visibility (`list_outcomes`, `list_policies`).
`Sales User` and `Guest` are denied with HTTP 403.
"""

from frappe import _

from crm_cskh.www.cskh_context import SETTINGS_READ_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/settings",
		title=_("Outcome and policy settings"),
		subtitle=_("Configure contact outcome codes and versioned customer care policies."),
		allowed_roles=SETTINGS_READ_ROLES,
	)
