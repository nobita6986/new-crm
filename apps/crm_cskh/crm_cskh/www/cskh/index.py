"""Context of the CSKH landing page (P1 + P2 + P3)."""

from frappe import _

from crm_cskh.www.cskh_context import READ_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh",
		title=_("Recruitment workspace"),
		subtitle=_(
			"Manage contact records, assignments, follow-up schedules, and recruitment team performance."
		),
		allowed_roles=READ_ROLES,
	)
