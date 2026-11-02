"""P1 patch: the `cskh_is_placeholder_name` business field of `CRM Lead`.

P1 §6.2 and ADR-006 P0 require that a lead created by the import carries a
controlled placeholder name and is **marked** as a placeholder, so the UI and
the reports can tell a real name from a placeholder.

The field is created by a patch (not only by a fixture) so an existing site
gets it on `bench migrate` even when its fixtures were never re-imported.
"""

import frappe

FIELD_NAME = "cskh_is_placeholder_name"


def execute():
	"""Create the placeholder marker field on `CRM Lead` if it is missing."""
	if frappe.db.exists("Custom Field", {"dt": "CRM Lead", "fieldname": FIELD_NAME}):
		return
	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "CRM Lead",
			"fieldname": FIELD_NAME,
			"fieldtype": "Check",
			"label": "CSKH Is Placeholder Name",
			"insert_after": "lead_owner",
			"default": "0",
			"description": ("Đánh dấu khách hàng được tạo từ import với tên thay thế (ADR-006 P0, P1 §6.2)."),
			"print_hide": 1,
			"no_copy": 1,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
