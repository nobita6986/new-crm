"""Context for the CSKH customer pool page.

The page mirrors the role grant of `crm_cskh.api.customers.pool_list`
(P1 §8.1): the BOD viewer, the CRM Admin and a team leader may read the pool;
a staff member may not.
"""

from frappe import _

from crm_cskh.www.cskh_context import POOL_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/pool",
		title=_("Customer pool"),
		subtitle=_("Leads in your scope, with the derived distribution state."),
		allowed_roles=POOL_ROLES,
	)
