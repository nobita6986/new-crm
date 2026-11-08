from crm_cskh.p3_models import WorkingCalendar, ensure_p3_indexes


class CSKHWorkingCalendar(WorkingCalendar):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH Working Calendar")
