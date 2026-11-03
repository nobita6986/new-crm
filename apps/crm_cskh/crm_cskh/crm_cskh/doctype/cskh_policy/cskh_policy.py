from crm_cskh.p2_models import Policy, ensure_p2_indexes


class CSKHPolicy(Policy):
	pass


def on_doctype_update():
	ensure_p2_indexes("CSKH Policy")
