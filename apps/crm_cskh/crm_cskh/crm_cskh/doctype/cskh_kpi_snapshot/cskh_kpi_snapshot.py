from crm_cskh.p3_models import KPISnapshot, ensure_p3_indexes


class CSKHKPISnapshot(KPISnapshot):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH KPI Snapshot")
