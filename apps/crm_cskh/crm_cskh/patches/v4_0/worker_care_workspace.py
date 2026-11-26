"""Retire the app-owned Deal shortcut when upgrading an existing site.

Only an app-owned form script is disabled. Existing Deal/Lead documents,
assignments, role IDs, permissions and historical metrics are preserved.
"""

import frappe


def execute():
	name = "CSKH - Open CSKH Page from Deal"
	if frappe.db.exists("CRM Form Script", name):
		frappe.db.set_value("CRM Form Script", name, "enabled", 0)
	get_single = getattr(frappe.db, "get_single_value", None)
	set_single = getattr(frappe.db, "set_single_value", None)
	if callable(get_single) and callable(set_single):
		cur_default = get_single("System Settings", "default_app")
		if not cur_default or cur_default == "crm":
			set_single("System Settings", "default_app", "crm_cskh")
	has_col = getattr(frappe.db, "has_column", None)
	sql_fn = getattr(frappe.db, "sql", None)
	if callable(has_col) and callable(sql_fn) and has_col("User", "default_app"):
		sql_fn("update `tabUser` set default_app = 'crm_cskh' where default_app = 'crm'")
	frappe.clear_cache()
