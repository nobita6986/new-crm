# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0
#
# P0-B §B3 — kiểm thử DocType PoC `CSKH App Settings` (Single).
#
# Phạm vi đã chạy: tồn tại sau migrate, đọc/ghi được bởi Administrator, và user
# không có quyền KHÔNG ghi được (kiểm ở backend qua frappe.has_permission).

import frappe
from frappe.tests.utils import FrappeTestCase

POC_USER = "cskh.poc.user@example.com"


class TestCSKHAppSettings(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def _ensure_plain_user(self):
		if not frappe.db.exists("User", POC_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": POC_USER,
					"first_name": "CSKH POC User",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

	def test_single_exists_after_migrate(self):
		settings = frappe.get_single("CSKH App Settings")
		self.assertEqual(settings.doctype, "CSKH App Settings")
		self.assertEqual(settings.name, "CSKH App Settings")

	def test_admin_can_write_single(self):
		settings = frappe.get_single("CSKH App Settings")
		original = settings.notes
		self.addCleanup(self._restore_notes, original)

		settings.notes = "poc write ok"
		settings.save(ignore_permissions=False)
		self.assertEqual(frappe.db.get_single_value("CSKH App Settings", "notes"), "poc write ok")

	def test_plain_user_has_no_write_permission(self):
		self._ensure_plain_user()
		frappe.set_user(POC_USER)
		self.assertFalse(frappe.has_permission("CSKH App Settings", "write"))

	def _restore_notes(self, original):
		frappe.set_user("Administrator")
		settings = frappe.get_single("CSKH App Settings")
		settings.notes = original
		settings.save(ignore_permissions=True)
