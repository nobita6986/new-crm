"""Authenticated marketing care overview; caller cannot select another scope."""

import frappe

from crm_cskh.services import worker_overview


@frappe.whitelist(methods=["GET"])
def get_overview():
	return worker_overview.get_overview(actor=frappe.session.user)
