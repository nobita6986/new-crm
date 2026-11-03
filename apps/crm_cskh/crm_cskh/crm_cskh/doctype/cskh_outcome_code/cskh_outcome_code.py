from crm_cskh.p2_models import OutcomeCode, ensure_p2_indexes


class CSKHOutcomeCode(OutcomeCode):
	pass


def on_doctype_update():
	ensure_p2_indexes("CSKH Outcome Code")
