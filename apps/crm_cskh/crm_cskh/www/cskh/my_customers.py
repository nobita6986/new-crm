"""Context for the CSKH My Customers page.

The page mirrors the role grant of `crm_cskh.api.customers.my_list`
(P1 §8.1): every business role may read the list, but the list always holds
the work that is currently assigned to the signed-in user.
"""

from frappe import _

from crm_cskh.www.cskh_context import READ_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/my-customers",
		title=_("My customers"),
		subtitle=_("Customers assigned to me."),
		allowed_roles=READ_ROLES,
	)
