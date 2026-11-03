from crm_cskh.p1_models import WorkItem, ensure_indexes


class CSKHWorkItem(WorkItem):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Work Item")
