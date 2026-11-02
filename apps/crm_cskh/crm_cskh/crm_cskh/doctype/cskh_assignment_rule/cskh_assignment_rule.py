from crm_cskh.p1_models import AssignmentRule, ensure_indexes


class CSKHAssignmentRule(AssignmentRule):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Assignment Rule")
