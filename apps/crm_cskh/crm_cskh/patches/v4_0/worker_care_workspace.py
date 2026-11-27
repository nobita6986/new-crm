"""Retire the app-owned Deal shortcut when upgrading an existing site.

Only an app-owned form script is disabled. Existing Deal/Lead documents,
assignments, role IDs, permissions and historical metrics are preserved.
"""

import frappe

CARE_DEFAULT_APP_ROLES = (
	"Sales User",
	"Sales Manager",
	"CSKH CRM Admin",
	"CSKH BOD Viewer",
	"System Manager",
)


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
		placeholders = ", ".join(["%s"] * len(CARE_DEFAULT_APP_ROLES))
		sql_fn(
			f"""
			update `tabUser`
			set default_app = 'crm_cskh'
			where default_app = 'crm'
			  and (
			    name = 'Administrator'
			    or exists (
			      select 1
			      from `tabHas Role` hr
			      where hr.parent = `tabUser`.name
			        and hr.parenttype = 'User'
			        and hr.role in ({placeholders})
			    )
			  )
			""",
			CARE_DEFAULT_APP_ROLES,
		)
	frappe.clear_cache()
