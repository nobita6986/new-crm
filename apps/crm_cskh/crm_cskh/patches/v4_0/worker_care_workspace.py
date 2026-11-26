"""Retire the app-owned Deal shortcut when upgrading an existing site.

Only an app-owned form script is disabled. Existing Deal/Lead documents,
assignments, role IDs, permissions and historical metrics are preserved.
"""

import frappe


def execute():
	name = "CSKH - Open CSKH Page from Deal"
	if frappe.db.exists("CRM Form Script", name):
		frappe.db.set_value("CRM Form Script", name, "enabled", 0)
	frappe.clear_cache()
