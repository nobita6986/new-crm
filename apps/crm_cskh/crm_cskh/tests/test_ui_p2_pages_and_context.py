"""T1A tests for P2 `/cskh/*` page gates, navigation matrix, timezone context, and fixtures.

Named `test_ui_p2_pages_and_context.py` so frozen `test_p1_*.py` / `test_p2_*.py`
backend inventories stay untouched while CI runs this module explicitly.
"""

import json
import os
from unittest import mock

import frappe

from crm_cskh import p2_strings
from crm_cskh.api import ownership_ui
from crm_cskh.tests.p1_base import P1TestCase
from crm_cskh.www import cskh_context
from crm_cskh.www.cskh import (
	callbacks as callbacks_page,
)
from crm_cskh.www.cskh import (
	index as index_page,
)
from crm_cskh.www.cskh import (
	my_customers as my_customers_page,
)
from crm_cskh.www.cskh import (
	progress as progress_page,
)
from crm_cskh.www.cskh import (
	settings as settings_page,
)
from crm_cskh.www.cskh import (
	work_item as work_item_page,
)


class TestUiP2PagesAndContext(P1TestCase):
	"""Verify P2 `/cskh/*` role gates, BOD PII exclusion, timezone context, and Form Scripts."""

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def _fresh_ctx(self):
		return frappe._dict({"boot": {}})

	def test_guest_is_denied_on_all_p2_pages(self):
		frappe.set_user("Guest")
		for mod in (
			index_page,
			my_customers_page,
			work_item_page,
			callbacks_page,
			progress_page,
			settings_page,
		):
			with self.assertRaises(frappe.PermissionError):
				mod.get_context(self._fresh_ctx())

	def test_staff_page_grants_and_navigation(self):
		frappe.set_user(self.staff_a)
		ctx = index_page.get_context(self._fresh_ctx())
		nav_keys = {item["key"] for item in ctx.cskh_navigation}
		self.assertEqual(nav_keys, {"my-customers", "callbacks", "progress"})
		self.assertTrue(ctx.cskh_capabilities["pii"])
		self.assertFalse(ctx.cskh_capabilities["can_manage_settings"])
		self.assertFalse(ctx.cskh_capabilities["can_review_correction"])

		# Staff may open workspace, callbacks, and progress
		for mod in (my_customers_page, work_item_page, callbacks_page, progress_page):
			page_ctx = mod.get_context(self._fresh_ctx())
			self.assertEqual(page_ctx.lang, "vi")
			self.assertIn("X-Frappe-CSRF-Token", page_ctx.cskh_csrf_header)

		# Staff is denied on settings
		with self.assertRaises(frappe.PermissionError):
			settings_page.get_context(self._fresh_ctx())

	def test_bod_is_denied_on_pii_p2_pages_and_allowed_on_zero_pii_pages(self):
		frappe.set_user(self.bod_user)
		ctx = index_page.get_context(self._fresh_ctx())
		nav_keys = {item["key"] for item in ctx.cskh_navigation}
		self.assertNotIn("callbacks", nav_keys)
		self.assertNotIn("import", nav_keys)
		self.assertIn("progress", nav_keys)
		self.assertIn("settings", nav_keys)
		self.assertFalse(ctx.cskh_capabilities["pii"])
		self.assertFalse(ctx.cskh_capabilities["can_manage_settings"])

		# BOD is denied on PII pages (`/cskh/work-item` and `/cskh/callbacks`)
		for pii_mod in (work_item_page, callbacks_page):
			with self.assertRaises(frappe.PermissionError):
				pii_mod.get_context(self._fresh_ctx())

		# BOD is allowed on 0-PII progress and read-only settings
		prog_ctx = progress_page.get_context(self._fresh_ctx())
		self.assertEqual(prog_ctx.cskh_route, "/cskh/progress")
		set_ctx = settings_page.get_context(self._fresh_ctx())
		self.assertEqual(set_ctx.cskh_route, "/cskh/settings")

	def test_leader_and_admin_page_capabilities(self):
		frappe.set_user(self.leader_a)
		leader_ctx = settings_page.get_context(self._fresh_ctx())
		self.assertFalse(leader_ctx.cskh_capabilities["can_manage_settings"])
		self.assertTrue(leader_ctx.cskh_capabilities["can_review_correction"])
		work_item_page.get_context(self._fresh_ctx())
		callbacks_page.get_context(self._fresh_ctx())

		frappe.set_user(self.admin_user)
		admin_ctx = settings_page.get_context(self._fresh_ctx())
		self.assertTrue(admin_ctx.cskh_capabilities["can_manage_settings"])
		self.assertTrue(admin_ctx.cskh_capabilities["can_review_correction"])

	def test_timezone_context_respects_site_and_user_timezone_with_safe_fallback(self):
		with mock.patch("frappe.utils.get_system_timezone", return_value="Asia/Ho_Chi_Minh"):
			frappe.db.set_value("User", self.staff_a, "time_zone", "", update_modified=False)
			tz_default = cskh_context.timezone_context(self.staff_a)
			self.assertEqual(tz_default["site"], "Asia/Ho_Chi_Minh")
			self.assertEqual(tz_default["effective"], "Asia/Ho_Chi_Minh")
			self.assertTrue(tz_default["server_now_iso"].endswith("+07:00"))

			frappe.db.set_value("User", self.staff_a, "time_zone", "Asia/Tokyo", update_modified=False)
			tz_tokyo = cskh_context.timezone_context(self.staff_a)
			self.assertEqual(tz_tokyo["site"], "Asia/Ho_Chi_Minh")
			self.assertEqual(tz_tokyo["effective"], "Asia/Tokyo")
			self.assertTrue(tz_tokyo["server_now_iso"].endswith("+09:00"))

			frappe.db.set_value(
				"User", self.staff_a, "time_zone", "Invalid/NonExistentZone", update_modified=False
			)
			tz_fallback = cskh_context.timezone_context(self.staff_a)
			self.assertEqual(tz_fallback["effective"], "Asia/Ho_Chi_Minh")
			self.assertTrue(tz_fallback["server_now_iso"].endswith("+07:00"))

			frappe.db.set_value("User", self.staff_a, "time_zone", "", update_modified=False)

	def test_p2_error_codes_and_vietnamese_explanations_published_to_browser(self):
		frappe.set_user(self.staff_a)
		ctx = work_item_page.get_context(self._fresh_ctx())
		for code in p2_strings.P2_ERROR_CODES:
			self.assertIn(code, ctx.cskh_error_codes)
			self.assertTrue(ctx.cskh_error_texts.get(code), f"Missing Vietnamese text for {code}")
			self.assertNotEqual(
				ctx.cskh_error_texts[code],
				p2_strings.P2_ERROR_EXPLANATIONS[code],
				f"Error explanation for {code} was not translated to Vietnamese",
			)

	def test_lead_form_script_fixture_declares_open_care_workspace(self):
		fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "crm_form_script.json")
		with open(fixture_path, encoding="utf-8") as fh:
			entries = json.load(fh)
		by_name = {row["name"]: row for row in entries}
		self.assertIn("CSKH - Open CSKH Page", by_name)
		self.assertIn("CSKH - Open Lead CSKH Workspace", by_name)
		lead_script = by_name["CSKH - Open Lead CSKH Workspace"]
		self.assertEqual(lead_script["dt"], "CRM Lead")
		self.assertEqual(lead_script["view"], "Form")
		self.assertEqual(lead_script["enabled"], 1)
		self.assertIn("/cskh/work-item?lead=", lead_script["script"])

	def test_o02_ownership_ui_assignees_respects_per_user_latest_rule_version(self):
		camp = self.make_campaign(title="Chiến dịch đa version O-02", team_scope=self.team_a)
		lead_a = self.make_canonical_lead()
		self.make_work_item(camp, lead_a, status="assigned", assigned_to=self.staff_a)

		# staff_a has historical v1 (cap=5, enabled=1) superseded by v2 (cap=1, enabled=1) -> full at 1/1
		self.make_rule(camp, self.staff_a, weight=50, capacity=5, version=1, enabled=1, team=self.team_a)
		self.make_rule(camp, self.staff_a, weight=50, capacity=1, version=2, enabled=1, team=self.team_a)

		# staff_b remains at v1 (cap=5, enabled=1) -> still eligible even though staff_a is at v2
		self.make_rule(camp, self.staff_b, weight=50, capacity=5, version=1, enabled=1, team=self.team_a)

		frappe.set_user(self.admin_user)
		res = ownership_ui.assignees(campaign=camp)
		eligible_users = {row["user"] for row in res["items"]}
		self.assertNotIn(self.staff_a, eligible_users)
		self.assertIn(self.staff_b, eligible_users)
