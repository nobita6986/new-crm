"""T1A tests for the P1 UI read helpers, authorised error download and PII/role boundaries.

Named `test_ui_p1_endpoints.py` (not `test_p1_*.py`) so the frozen T1B
174-method inventory (`deploy/dev/seed/p1_test_inventory.json`) stays untouched.
"""

import inspect

import frappe

from crm_cskh.api import customers as customers_api
from crm_cskh.api import import_history, ownership_ui, pool_ui, run_history
from crm_cskh.services.errors import Forbidden, InvalidRequest, NotFound
from crm_cskh.services.import_service import write_error_artifact
from crm_cskh.tests.p1_base import P1TestCase
from crm_cskh.www import cskh_context


class TestUiP1Endpoints(P1TestCase):
	"""Verify T1A UI read endpoints, error-file download, scope isolation and BOD PII omission."""

	def setUp(self):
		super().setUp()
		self.campaign_a = self.make_campaign(title="Chiến dịch Đội A", team_scope=self.team_a)
		self.campaign_b = self.make_campaign(title="Chiến dịch Đội B", team_scope=self.team_b)

		self.lead_eligible = self.make_canonical_lead()
		self.lead_invalid = self.make_lead()  # no CSKH Identity Index -> derived `invalid`
		self.lead_team_a = self.make_canonical_lead()
		self.lead_team_b = self.make_canonical_lead()
		self.lead_queued_a = self.make_canonical_lead()

		self.wi_team_a = self.make_work_item(
			self.campaign_a, self.lead_team_a, status="assigned", assigned_to=self.staff_a
		)
		self.wi_team_b = self.make_work_item(
			self.campaign_b, self.lead_team_b, status="assigned", assigned_to=self.staff_c
		)
		self.wi_queued_a = self.make_work_item(self.campaign_a, self.lead_queued_a, status="queued")

	def _make_batch_with_errors(self):
		source_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "ui-test-source.csv",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": self.lead_team_a,
				"content": b"phone\n0912345678\n",
			}
		)
		source_doc.insert(ignore_permissions=True)

		batch = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch",
				"source_file": source_doc.name,
				"checksum": self.unique_key("chk"),
				"idempotency_key": self.unique_key("ui-batch"),
				"status": "COMPLETED_WITH_ERRORS",
				"total_rows": 3,
				"valid_rows": 1,
				"new_rows": 1,
				"duplicate_rows": 0,
				"error_rows": 2,
				"created_rows": 1,
				"reused_rows": 0,
				"skipped_rows": 2,
			}
		)
		batch.insert(ignore_permissions=True)
		artifact = write_error_artifact(
			batch.name,
			[
				{"row_number": 2, "reason": "=1+1"},
				{"row_number": 3, "reason": "<script>alert(1)</script>"},
			],
		)
		batch.error_artifact = artifact
		batch.save(ignore_permissions=True)
		return batch.name

	def test_ui_endpoints_do_not_accept_scope_widening_parameters(self):
		for fn in (
			pool_ui.list,
			ownership_ui.assigned_list,
			ownership_ui.assignees,
			import_history.history,
			import_history.batch_detail,
			import_history.download_error_file,
			run_history.runs,
		):
			params = set(inspect.signature(fn).parameters)
			self.assertNotIn("user", params)
			self.assertNotIn("team", params)
			self.assertNotIn("scope", params)

	def test_error_file_download_allows_admin_and_denies_other_roles(self):
		batch_name = self._make_batch_with_errors()

		frappe.set_user(self.admin_user)
		frappe.local.response = frappe._dict()
		import_history.download_error_file(batch=batch_name)
		self.assertEqual(frappe.local.response.get("type"), "download")
		content = frappe.local.response.get("filecontent")
		text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
		self.assertIn("'=1+1", text)
		self.assertNotIn("<script>", text)

		for denied in (self.leader_a, self.leader_b, self.staff_a, self.bod_user, self.staff_outside):
			frappe.set_user(denied)
			with self.assertRaises(Forbidden):
				import_history.download_error_file(batch=batch_name)

		frappe.set_user(self.admin_user)
		with self.assertRaises(InvalidRequest):
			import_history.download_error_file(batch="")
		with self.assertRaises(NotFound):
			import_history.download_error_file(batch="BATCH-DOES-NOT-EXIST")

	def test_import_history_and_batch_detail_expose_reconciled_counters_to_admin_only(self):
		batch_name = self._make_batch_with_errors()

		frappe.set_user(self.admin_user)
		detail = import_history.batch_detail(batch=batch_name)
		self.assertEqual(detail["batch"], batch_name)
		self.assertEqual(detail["totals"]["created_rows"], 1)
		self.assertEqual(detail["totals"]["skipped_rows"], 2)
		self.assertIn("download_error_file", detail["error_artifact"]["download_url"])

		listing = import_history.history(page=1, page_size=20)
		self.assertGreaterEqual(listing["total"], 1)
		batches = {item["batch"]: item for item in listing["items"]}
		self.assertIn(batch_name, batches)

		for denied in (self.leader_a, self.staff_a, self.bod_user):
			frappe.set_user(denied)
			with self.assertRaises(Forbidden):
				import_history.history()
			with self.assertRaises(Forbidden):
				import_history.batch_detail(batch=batch_name)

	def test_pool_ui_derives_states_isolates_teams_and_omits_bod_pii(self):
		frappe.set_user(self.admin_user)
		admin_pool = pool_ui.list(page=1, page_size=100)
		by_lead = {item["lead"]: item for item in admin_pool["items"]}
		self.assertEqual(by_lead[self.lead_eligible]["state"], "eligible")
		self.assertEqual(by_lead[self.lead_invalid]["state"], "invalid")
		self.assertEqual(by_lead[self.lead_team_a]["state"], "assigned")
		self.assertEqual(by_lead[self.lead_queued_a]["state"], "queued")
		self.assertIsNotNone(by_lead[self.lead_eligible]["phone"])

		frappe.set_user(self.leader_a)
		leader_pool = pool_ui.list(page=1, page_size=100)
		leader_leads = {item["lead"] for item in leader_pool["items"]}
		self.assertIn(self.lead_team_a, leader_leads)
		self.assertIn(self.lead_queued_a, leader_leads)
		self.assertNotIn(self.lead_team_b, leader_leads)
		self.assertNotIn(self.lead_eligible, leader_leads)

		frappe.set_user(self.bod_user)
		bod_pool = pool_ui.list(page=1, page_size=100)
		self.assertGreaterEqual(len(bod_pool["items"]), 1)
		for item in bod_pool["items"]:
			self.assertIsNone(item["phone"])
			self.assertEqual(item["display_name"], "")
		with self.assertRaises(Forbidden):
			pool_ui.list(page=1, page_size=20, search="09")

		# Frozen `customers.pool_list` also omits PII for BOD.
		bod_queued = customers_api.pool_list(page=1, page_size=100)
		self.assertGreaterEqual(len(bod_queued["items"]), 1)
		for item in bod_queued["items"]:
			self.assertIsNone(item["phone"])
			self.assertEqual(item["display_name"], "")

		for denied in (self.staff_a, self.staff_outside):
			frappe.set_user(denied)
			with self.assertRaises(Forbidden):
				pool_ui.list()

	def test_ownership_ui_scopes_team_blocks_widening_and_restricts_assignees(self):
		frappe.set_user(self.admin_user)
		admin_res = ownership_ui.assigned_list(page=1, page_size=100)
		self.assertTrue(admin_res["can_manage"])
		admin_items = {row["work_item"] for row in admin_res["items"]}
		self.assertIn(self.wi_team_a, admin_items)
		self.assertIn(self.wi_team_b, admin_items)
		assignee_res = ownership_ui.assignees()
		self.assertIn(self.staff_a, [u["user"] for u in assignee_res["items"]])

		frappe.set_user(self.leader_a)
		leader_res = ownership_ui.assigned_list(page=1, page_size=100)
		self.assertEqual(leader_res["scope"], "team")
		self.assertFalse(leader_res["can_manage"])
		leader_items = {row["work_item"] for row in leader_res["items"]}
		self.assertIn(self.wi_team_a, leader_items)
		self.assertNotIn(self.wi_team_b, leader_items)
		# Trying to narrow to a Team B user returns zero rows, never widening scope.
		narrowed_b = ownership_ui.assigned_list(page=1, page_size=100, owner=self.staff_c)
		self.assertEqual(narrowed_b["items"], [])
		with self.assertRaises(Forbidden):
			ownership_ui.assignees()

		frappe.set_user(self.staff_a)
		staff_res = ownership_ui.assigned_list(page=1, page_size=100)
		self.assertEqual(staff_res["scope"], "self")
		self.assertFalse(staff_res["can_manage"])
		self.assertTrue(all(r["assigned_to"] == self.staff_a for r in staff_res["items"]))
		staff_camp_res = ownership_ui.assigned_list(page=1, page_size=100, campaign=self.campaign_a)
		self.assertEqual([r["work_item"] for r in staff_camp_res["items"]], [self.wi_team_a])
		self.assertEqual(ownership_ui.assigned_list(owner=self.staff_c)["items"], [])
		with self.assertRaises(Forbidden):
			ownership_ui.assignees()

		frappe.set_user(self.bod_user)
		bod_res = ownership_ui.assigned_list(page=1, page_size=100)
		self.assertFalse(bod_res["can_manage"])
		self.assertGreaterEqual(len(bod_res["items"]), 1)
		for row in bod_res["items"]:
			self.assertIsNone(row["phone"])
			self.assertEqual(row["display_name"], "")
		with self.assertRaises(Forbidden):
			ownership_ui.assignees()

	def test_run_history_and_navigation_match_role_capabilities(self):
		frappe.set_user(self.admin_user)
		admin_runs = run_history.runs()
		self.assertTrue(admin_runs["can_lookup"])
		self.assertTrue(admin_runs["can_distribute"])

		frappe.set_user(self.bod_user)
		bod_runs = run_history.runs()
		self.assertTrue(bod_runs["can_lookup"])
		self.assertFalse(bod_runs["can_distribute"])
		ctx = frappe._dict()
		cskh_context.build_context(
			ctx, route="/cskh/campaigns", allowed_roles=cskh_context.CAMPAIGN_READ_ROLES
		)
		nav_keys = [entry["key"] for entry in ctx.cskh_navigation]
		self.assertIn("campaigns", nav_keys)
		self.assertFalse(ctx.cskh_capabilities["pii"])

		frappe.set_user(self.leader_a)
		leader_runs = run_history.runs()
		self.assertFalse(leader_runs["can_lookup"])
		self.assertFalse(leader_runs["can_distribute"])

		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			run_history.runs()
