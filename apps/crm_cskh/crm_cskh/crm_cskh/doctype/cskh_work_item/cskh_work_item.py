from crm_cskh.p1_models import WorkItem, ensure_indexes
from crm_cskh.p2_models import ensure_p2_indexes


class CSKHWorkItem(WorkItem):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Work Item")
	ensure_p2_indexes("CSKH Work Item")
