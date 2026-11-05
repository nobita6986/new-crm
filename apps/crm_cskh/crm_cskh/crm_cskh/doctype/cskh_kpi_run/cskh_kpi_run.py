from crm_cskh.p3_models import KPIRun, ensure_p3_indexes


class CSKHKPIRun(KPIRun):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH KPI Run")
