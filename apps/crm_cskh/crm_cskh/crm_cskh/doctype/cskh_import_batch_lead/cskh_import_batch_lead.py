from crm_cskh.p1_models import ImportBatchLead, ensure_indexes


class CSKHImportBatchLead(ImportBatchLead):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Import Batch Lead")
