from crm_cskh.p1_models import DistributionRun, ensure_indexes


class CSKHDistributionRun(DistributionRun):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Distribution Run")
