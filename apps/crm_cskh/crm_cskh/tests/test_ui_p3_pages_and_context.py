"""T1A tests for P3 `/cskh/*` quality/SLA page gates, role navigation, feature flags & i18n.

Named `test_ui_p3_pages_and_context.py` so frozen P1/P2 inventories and T1B's
`test_p3_*.py` backend inventory stay conflict-free while CI enforces per-method
JUnit coverage via `deploy/p3/ui_test_inventory.json`.
"""

from unittest import mock

import frappe

from crm_cskh import p1_strings, p3_strings
from crm_cskh.tests.p2_base import P2TestCase
from crm_cskh.www import cskh_context
from crm_cskh.www.cskh import (
	callbacks as callbacks_page,
)
from crm_cskh.www.cskh import (
	campaign_quality as campaign_quality_page,
)
from crm_cskh.www.cskh import (
	employee_quality as employee_quality_page,
)
from crm_cskh.www.cskh import (
	index as index_page,
)
from crm_cskh.www.cskh import (
	quality_overview as quality_overview_page,
)
from crm_cskh.www.cskh import (
	scorecard as scorecard_page,
)
from crm_cskh.www.cskh import (
	sla_operations as sla_operations_page,
)
from crm_cskh.www.cskh import (
	team_quality as team_quality_page,
)
from crm_cskh.www.cskh import (
	work_item as work_item_page,
)

ALL_P3_PAGES = (
	quality_overview_page,
	team_quality_page,
	employee_quality_page,
	campaign_quality_page,
	sla_operations_page,
	scorecard_page,
)


class TestUiP3PagesAndContext(P2TestCase):
	"""Verify P3 `/cskh/*` role gates, `p3_enabled` / `qa_enabled` flags, and Vietnamese i18n."""

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def _fresh_ctx(self):
		return frappe._dict({"boot": {}})

	def test_guest_is_denied_on_all_six_p3_pages(self):
		frappe.set_user("Guest")
		for mod in ALL_P3_PAGES:
			with self.assertRaises(frappe.PermissionError):
				mod.get_context(self._fresh_ctx())

	def test_staff_only_accesses_personal_scorecard_among_p3_pages(self):
		frappe.set_user(self.staff_a)
		ctx = scorecard_page.get_context(self._fresh_ctx())
		self.assertEqual(ctx.cskh_route, "/cskh/scorecard")
		self.assertEqual(ctx.lang, "vi")
		self.assertFalse(ctx.cskh_capabilities["can_recompute_kpi"])
		self.assertFalse(ctx.cskh_capabilities["can_waive_sla"])
		self.assertTrue(ctx.cskh_capabilities["can_export_report"])

		nav_keys = {item["key"] for item in ctx.cskh_navigation}
		self.assertEqual(nav_keys, {"my-customers", "callbacks", "progress", "scorecard"})

		for denied_mod in (
			quality_overview_page,
			team_quality_page,
			employee_quality_page,
			campaign_quality_page,
			sla_operations_page,
		):
			with self.assertRaises(frappe.PermissionError):
				denied_mod.get_context(self._fresh_ctx())

	def test_leader_accesses_team_employee_campaign_sla_and_scorecard_not_bod_overview(self):
		frappe.set_user(self.leader_a)
		for allowed_mod in (
			team_quality_page,
			employee_quality_page,
			campaign_quality_page,
			sla_operations_page,
			scorecard_page,
		):
			page_ctx = allowed_mod.get_context(self._fresh_ctx())
			self.assertTrue(page_ctx.cskh_capabilities["can_waive_sla"])
			self.assertFalse(page_ctx.cskh_capabilities["can_recompute_kpi"])

		with self.assertRaises(frappe.PermissionError):
			quality_overview_page.get_context(self._fresh_ctx())

	def test_bod_accesses_five_management_p3_pages_with_zero_pii_and_denied_scorecard(self):
		frappe.set_user(self.bod_user)
		for allowed_mod in (
			quality_overview_page,
			team_quality_page,
			employee_quality_page,
			campaign_quality_page,
			sla_operations_page,
		):
			page_ctx = allowed_mod.get_context(self._fresh_ctx())
			self.assertFalse(page_ctx.cskh_capabilities["pii"])
			self.assertFalse(page_ctx.cskh_capabilities["can_waive_sla"])
			self.assertFalse(page_ctx.cskh_capabilities["can_recompute_kpi"])

		for denied_mod in (scorecard_page, work_item_page, callbacks_page):
			with self.assertRaises(frappe.PermissionError):
				denied_mod.get_context(self._fresh_ctx())

	def test_crm_admin_accesses_all_six_p3_pages_with_recompute_and_waive_capabilities(self):
		frappe.set_user(self.admin_user)
		for mod in ALL_P3_PAGES:
			page_ctx = mod.get_context(self._fresh_ctx())
			self.assertTrue(page_ctx.cskh_capabilities["can_recompute_kpi"])
			self.assertTrue(page_ctx.cskh_capabilities["can_waive_sla"])
			self.assertEqual(len(page_ctx.cskh_p3_subnav), 6)

	def test_p3_feature_flag_rollback_hides_p3_nav_and_blocks_p3_routes_cleanly(self):
		frappe.set_user(self.admin_user)
		with mock.patch.dict(frappe.conf, {"cskh_p3_enabled": False}):
			self.assertFalse(cskh_context.is_p3_enabled())
			idx_ctx = index_page.get_context(self._fresh_ctx())
			nav_keys = {item["key"] for item in idx_ctx.cskh_navigation}
			self.assertFalse(nav_keys & cskh_context.P3_NAV_KEYS)

			for mod in ALL_P3_PAGES:
				with self.assertRaises(frappe.PermissionError):
					mod.get_context(self._fresh_ctx())

	def test_qa_enabled_flag_defaults_false_and_publishes_p3_vietnamese_catalogue(self):
		frappe.set_user(self.bod_user)
		with mock.patch.dict(frappe.conf, {}, clear=False):
			frappe.conf.pop("cskh_qa_enabled", None)
			frappe.conf.pop("qa_enabled", None)
			ctx = quality_overview_page.get_context(self._fresh_ctx())
			self.assertFalse(ctx.cskh_capabilities["qa_enabled"])

		with mock.patch.dict(frappe.conf, {"cskh_qa_enabled": True}):
			ctx_qa = quality_overview_page.get_context(self._fresh_ctx())
			self.assertTrue(ctx_qa.cskh_capabilities["qa_enabled"])

		for code in p3_strings.P3_ERROR_CODES:
			self.assertIn(code, ctx.cskh_error_codes)
			self.assertIn(code, ctx.cskh_error_texts)
			self.assertTrue(ctx.cskh_error_texts[code])

		i18n_map = p1_strings.translated_map()
		for msg in p3_strings.P3_NAV_AND_TITLES:
			self.assertIn(msg, i18n_map)
			self.assertNotEqual(i18n_map[msg], msg)
