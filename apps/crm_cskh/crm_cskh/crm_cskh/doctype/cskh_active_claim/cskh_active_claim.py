from crm_cskh.p1_models import ActiveClaim, ensure_indexes


class CSKHActiveClaim(ActiveClaim):
	pass


def on_doctype_update():
	ensure_indexes("CSKH Active Claim")
