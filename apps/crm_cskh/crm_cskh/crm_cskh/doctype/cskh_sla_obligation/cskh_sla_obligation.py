from crm_cskh.p3_models import SLAObligation, ensure_p3_indexes


class CSKHSLAObligation(SLAObligation):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH SLA Obligation")
