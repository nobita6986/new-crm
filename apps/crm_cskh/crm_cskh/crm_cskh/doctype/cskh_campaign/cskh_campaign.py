from crm_cskh.p1_models import Campaign, ensure_indexes


class CSKHCampaign(Campaign):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Campaign")
