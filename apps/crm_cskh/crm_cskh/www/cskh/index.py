"""Context of the CSKH landing page (P1 + P2 + P3)."""

from frappe import _

from crm_cskh.www.cskh_context import READ_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh",
		title=_("Worker care overview"),
		subtitle=_(
			"Marketing workspace for contacting workers, following up on time, and keeping care responsibilities clear."
		),
		allowed_roles=READ_ROLES,
	)
