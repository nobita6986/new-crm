from crm_cskh.p2_models import ContactAttempt, ensure_p2_indexes


class CSKHContactAttempt(ContactAttempt):
	pass


def on_doctype_update():
	ensure_p2_indexes("CSKH Contact Attempt")
