# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0
#
# P0-B §B4 — kiểm thử API health.
#
# Phạm vi đã chạy: tầng whitelist của Frappe (`frappe.is_whitelisted`) — đúng
# hàm mà HTTP handler dùng để chặn Guest. Test KHÔNG bao phủ toàn bộ đường HTTP
# (transport, reverse proxy); xem ghi chú trong báo cáo WP-02.

import frappe
from frappe.tests.utils import FrappeTestCase

from crm_cskh.api import health


class TestHealthAPIAuthorization(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_ping_not_registered_as_guest_method(self):
		"""Endpoint không được đăng ký cho Guest."""
		self.assertIn(health.ping, frappe.whitelisted)
		self.assertNotIn(health.ping, frappe.guest_methods)

	def test_guest_is_denied(self):
		"""Guest gọi API yêu cầu đăng nhập → bị từ chối."""
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			frappe.is_whitelisted(health.ping)

	def test_logged_in_user_is_allowed(self):
		frappe.set_user("Administrator")
		frappe.is_whitelisted(health.ping)  # không raise
		result = health.ping()
		self.assertEqual(result["status"], "ok")
		self.assertEqual(result["app"], "crm_cskh")


class TestHealthAPIResponseShape(FrappeTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")

	def test_response_exposes_no_sensitive_fields(self):
		frappe.set_user("Administrator")
		result = health.ping()
		self.assertEqual(set(result), {"app", "status", "app_version", "user"})
		for forbidden in (
			"password",
			"db_name",
			"db_password",
			"site",
			"site_config",
			"path",
			"secret",
		):
			self.assertNotIn(forbidden, result)
