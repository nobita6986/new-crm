"""Context of the CSKH landing page (P1).

The landing page only links to the P1 business screens. Every list is served
by the scoped P1 APIs; the page itself shows no business data.
"""

from frappe import _

from crm_cskh.www.cskh_context import READ_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh",
		title=_("CSKH"),
		subtitle=_("Customer pool, imports, campaigns and distribution."),
		allowed_roles=READ_ROLES,
	)
