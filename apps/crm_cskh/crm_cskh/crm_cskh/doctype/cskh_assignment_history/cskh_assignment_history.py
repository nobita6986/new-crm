from crm_cskh.p1_models import AssignmentHistory, ensure_indexes


class CSKHAssignmentHistory(AssignmentHistory):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Assignment History")
