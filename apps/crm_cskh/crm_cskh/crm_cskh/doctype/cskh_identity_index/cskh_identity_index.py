from crm_cskh.p1_models import IdentityIndex, ensure_indexes


class CSKHIdentityIndex(IdentityIndex):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Identity Index")
