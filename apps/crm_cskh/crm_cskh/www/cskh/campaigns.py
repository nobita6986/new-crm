"""Context for the CSKH campaigns page.

The page mirrors the role grant of `crm_cskh.api.campaigns.*` and
`crm_cskh.api.distribution.*` (P1 §8.1): a leader may read the campaigns and
the rules of their scope; only the CRM Admin may write them or distribute.

The read part of the page is therefore available to a leader, while the write
controls are hidden for a role that may not use them. Hiding a control is a
convenience only: the API rejects the mutation server-side.
"""

from frappe import _

from crm_cskh.www.cskh_context import CAMPAIGN_READ_ROLES, build_context, is_admin


def get_context(context):
	return build_context(
		context,
		route="/cskh/campaigns",
		title=_("Campaigns and distribution rules"),
		subtitle=_("Set up the target set and the weights, then preview before distributing."),
		allowed_roles=CAMPAIGN_READ_ROLES,
	)
