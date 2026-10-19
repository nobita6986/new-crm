# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0
#
# P0-B §B2 — xác minh Custom Field PoC được fixture/migration tạo trên CRM Lead.
#
# PHẠM VI: đây là kiểm chứng **lưu trữ/metadata** (field tồn tại, đọc/ghi được),
# KHÔNG phải kiểm chứng quyền. Hai test dưới dùng `ignore_permissions=True` và
# `frappe.db.set_value` nên BỎ QUA tầng quyền; không được tính là bằng chứng cho
# yêu cầu permission của P0-B (security matrix là WP-05).

import frappe
from frappe.tests.utils import FrappeTestCase

POC_FIELD = "cskh_poc_marker"


class TestPOCCustomField(FrappeTestCase):
	def test_crm_lead_list_has_cskh_page_action(self):
		script = frappe.get_doc("CRM Form Script", "CSKH - Open CSKH Page")
		self.assertEqual((script.dt, script.view, script.enabled), ("CRM Lead", "List", 1))
		self.assertIn("window.location.assign('/cskh')", script.script)

	def test_custom_field_exists_on_crm_lead(self):
		"""Metadata: Custom Field PoC phải tồn tại trên CRM Lead sau migrate."""
		self.assertTrue(
			frappe.db.exists("Custom Field", {"dt": "CRM Lead", "fieldname": POC_FIELD}),
			msg=f"Thiếu Custom Field {POC_FIELD} trên CRM Lead (fixture chưa sync?)",
		)
		self.assertTrue(frappe.get_meta("CRM Lead").has_field(POC_FIELD))

	def test_custom_field_is_writable_and_readable(self):
		"""Lưu trữ: field PoC đọc/ghi được trên một record giả (không phải dữ liệu thật).

		Dùng quyền nâng cao nên KHÔNG chứng minh quyền theo role.
		"""
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "POC Custom Field",
				"mobile_no": "0900000999",
				"status": "New",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("CRM Lead", lead.name, force=True, ignore_permissions=True))

		frappe.db.set_value("CRM Lead", lead.name, POC_FIELD, "poc-ok")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, POC_FIELD), "poc-ok")
