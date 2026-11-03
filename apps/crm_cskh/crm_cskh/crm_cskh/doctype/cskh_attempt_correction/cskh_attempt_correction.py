from crm_cskh.p2_models import AttemptCorrection, ensure_p2_indexes


class CSKHAttemptCorrection(AttemptCorrection):
	pass


def on_doctype_update():
	ensure_p2_indexes("CSKH Attempt Correction")
