from crm_cskh.p3_models import QAReview, ensure_p3_indexes


class CSKHQAReview(QAReview):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH QA Review")
