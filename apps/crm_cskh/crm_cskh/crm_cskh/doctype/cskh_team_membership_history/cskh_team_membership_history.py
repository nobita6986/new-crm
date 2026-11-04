from crm_cskh.p3_models import TeamMembershipHistory, ensure_p3_indexes


class CSKHTeamMembershipHistory(TeamMembershipHistory):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH Team Membership History")
