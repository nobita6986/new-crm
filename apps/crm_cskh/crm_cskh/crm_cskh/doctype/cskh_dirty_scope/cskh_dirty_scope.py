from crm_cskh.p3_models import DirtyScope, ensure_p3_indexes


class CSKHDirtyScope(DirtyScope):
	pass


def on_doctype_update():
	ensure_p3_indexes("CSKH Dirty Scope")
