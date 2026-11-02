from crm_cskh.p1_models import ImportBatch, ensure_indexes


class CSKHImportBatch(ImportBatch):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Import Batch")
