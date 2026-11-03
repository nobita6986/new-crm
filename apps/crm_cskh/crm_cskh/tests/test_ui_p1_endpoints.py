"""T1A tests for the P1 UI read helpers, authorised error download and PII/role boundaries.

Named `test_ui_p1_endpoints.py` (not `test_p1_*.py`) so the frozen T1B
174-method inventory (`deploy/dev/seed/p1_test_inventory.json`) stays untouched.
"""

import base64
import inspect
import json
from unittest import mock

import frappe

from crm_cskh.api import (
	campaigns as campaigns_api,
)
from crm_cskh.api import (
	customers as customers_api,
)
from crm_cskh.api import (
	dashboard as dashboard_api,
)
from crm_cskh.api import (
	distribution as distribution_api,
)
from crm_cskh.api import (
	import_files,
	import_history,
	ownership_ui,
	pool_ui,
	run_history,
)
from crm_cskh.api import (
	imports as imports_api,
)
from crm_cskh.api import (
	ownership as ownership_api,
)
from crm_cskh.jobs import import_worker
from crm_cskh.p1_permissions import validate_p1_filter_fields
from crm_cskh.services import ownership as ownership_service
from crm_cskh.services.errors import Forbidden, InvalidRequest, NotFound, P1Error
from crm_cskh.services.import_service import write_error_artifact
from crm_cskh.services.phone_identity import normalize_vn_phone
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
			self.campaign_a,
			self.lead_team_a,
			status="assigned",
			assigned_to=self.staff_a,
		)
		self.wi_team_b = self.make_work_item(
			self.campaign_b,
			self.lead_team_b,
			status="assigned",
			assigned_to=self.staff_c,
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

		for denied in (
			self.leader_a,
			self.leader_b,
			self.staff_a,
			self.bod_user,
			self.staff_outside,
		):
			frappe.set_user(denied)
			with self.assertRaises(Forbidden):
				import_history.download_error_file(batch=batch_name)

		frappe.set_user(self.admin_user)
		with self.assertRaises(InvalidRequest):
			import_history.download_error_file(batch="")
		with self.assertRaises(NotFound):
			import_history.download_error_file(batch="BATCH-DOES-NOT-EXIST")

	def test_import_history_and_batch_detail_expose_reconciled_counters_to_admin_only(
		self,
	):
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
		run_b = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": self.campaign_b,
				"status": "running",
				"rule_version": 1,
				"selection_fingerprint": self.unique_key("fp"),
				"idempotency_key": self.unique_key("run-b"),
				"progress": 4,
				"preview_json": '{"eligible_count": 10}',
			}
		)
		run_b.insert(ignore_permissions=True)

		frappe.set_user(self.admin_user)
		admin_runs = run_history.runs(run_id=run_b.name)
		self.assertTrue(admin_runs["can_lookup"])
		self.assertTrue(admin_runs["can_distribute"])
		self.assertEqual(len(admin_runs["items"]), 1)
		self.assertEqual(admin_runs["items"][0]["progress"], 4)
		self.assertEqual(admin_runs["items"][0]["total"], 10)

		frappe.set_user(self.bod_user)
		bod_runs = run_history.runs(run_id=run_b.name)
		self.assertTrue(bod_runs["can_lookup"])
		self.assertFalse(bod_runs["can_distribute"])
		self.assertEqual(len(bod_runs["items"]), 1)
		ctx = frappe._dict()
		cskh_context.build_context(
			ctx, route="/cskh/campaigns", allowed_roles=cskh_context.CAMPAIGN_READ_ROLES
		)
		nav_keys = [entry["key"] for entry in ctx.cskh_navigation]
		self.assertIn("campaigns", nav_keys)
		self.assertFalse(ctx.cskh_capabilities["pii"])

		frappe.set_user(self.leader_a)
		leader_runs = run_history.runs(run_id=run_b.name)
		self.assertFalse(leader_runs["can_lookup"])
		self.assertFalse(leader_runs["can_distribute"])
		self.assertEqual(leader_runs["items"], [])

		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			run_history.runs()

	def test_l23_pool_ui_blocks_revoked_or_closed_history_leak_to_active_other_team(
		self,
	):
		frappe.set_user(self.leader_a)
		baseline_pool_a = pool_ui.list(page=1, page_size=100)
		baseline_total_a = baseline_pool_a["total"]
		baseline_wi_a = baseline_pool_a["work_items"]
		frappe.set_user("Administrator")

		campaign_shared = self.make_campaign(title="Chiến dịch Chung L23", team_scope=None)

		# 1. Lead revoked in Team A, now active (`assigned`) in Team B (`staff_c`).
		lead_revoked_to_b = self.make_canonical_lead()
		raw_phone_b = frappe.db.get_value("CRM Lead", lead_revoked_to_b, "mobile_no")
		alt_phone_b = "+84" + raw_phone_b[1:] if raw_phone_b.startswith("0") else raw_phone_b
		src_b = self.unique_key("L23SourceB")
		name_b = self.unique_key("KhachL23DoiB")
		frappe.db.set_value(
			"CRM Lead",
			lead_revoked_to_b,
			{"first_name": name_b, "lead_name": name_b, "source": src_b},
			update_modified=False,
		)
		wi_a_old = self.make_work_item(self.campaign_a, lead_revoked_to_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_revoked_to_b,
				"campaign": self.campaign_a,
				"work_item": wi_a_old,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_a_old,
			self.staff_a,
			actor=self.admin_user,
			reason="Initial Team A",
			request_id=self.unique_key("l23-assign-a"),
		)
		ownership_service.revoke(
			wi_a_old,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="Revoke from Team A",
			request_id=self.unique_key("l23-revoke-a"),
			return_to_pool=False,
		)
		wi_b_now = self.make_work_item(self.campaign_b, lead_revoked_to_b, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_revoked_to_b,
				"campaign": self.campaign_b,
				"work_item": wi_b_now,
			}
		).insert(ignore_permissions=True)
		ownership_service.assign(
			wi_b_now,
			self.staff_c,
			actor=self.admin_user,
			reason="Assign to Team B",
			request_id=self.unique_key("l23-assign-b"),
		)

		# 2. Lead closed in Team A, now `queued` with active claim in Team B.
		lead_closed_to_b_queued = self.make_canonical_lead()
		self.make_work_item(self.campaign_a, lead_closed_to_b_queued, status="closed")
		wi_b_queued = self.make_work_item(self.campaign_b, lead_closed_to_b_queued, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_closed_to_b_queued,
				"campaign": self.campaign_b,
				"work_item": wi_b_queued,
			}
		).insert(ignore_permissions=True)

		# 3. Lead revoked in Team A, now `assigned` to `staff_c` in a shared campaign.
		lead_revoked_to_shared_b = self.make_canonical_lead()
		self.make_work_item(self.campaign_a, lead_revoked_to_shared_b, status="revoked")
		wi_shared_b = self.make_work_item(
			campaign_shared,
			lead_revoked_to_shared_b,
			status="assigned",
			assigned_to=self.staff_c,
		)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_revoked_to_shared_b,
				"campaign": campaign_shared,
				"work_item": wi_shared_b,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"CRM Lead",
			lead_revoked_to_shared_b,
			"lead_owner",
			self.staff_c,
			update_modified=False,
		)

		# 4. Positive control for Team A: lead revoked in Team A and NOT active anywhere else.
		lead_revoked_stay_a = self.make_canonical_lead()
		stay_a_phone = frappe.db.get_value("CRM Lead", lead_revoked_stay_a, "mobile_no")
		self.make_work_item(self.campaign_a, lead_revoked_stay_a, status="revoked")

		# --- Verify Leader A sees ZERO data/counts/search/filter oracle for Team B leads ---
		frappe.set_user(self.leader_a)
		pool_a = pool_ui.list(page=1, page_size=100)
		leads_a = {row["lead"]: row for row in pool_a["items"]}
		self.assertNotIn(lead_revoked_to_b, leads_a)
		self.assertNotIn(lead_closed_to_b_queued, leads_a)
		self.assertNotIn(lead_revoked_to_shared_b, leads_a)
		self.assertNotIn(self.lead_team_b, leads_a)

		serialized_a = json.dumps(pool_a, default=str)
		self.assertNotIn(raw_phone_b, serialized_a)
		self.assertNotIn(alt_phone_b, serialized_a)
		self.assertNotIn(self.staff_c, serialized_a)
		self.assertNotIn(self.campaign_b, serialized_a)
		self.assertNotIn("Chiến dịch Đội B", serialized_a)

		# Positive controls for Leader A: active, queued, and legitimately revoked-only in Team A.
		self.assertEqual(leads_a[self.lead_team_a]["state"], "assigned")
		self.assertEqual(leads_a[self.lead_team_a]["owner"], self.staff_a)
		self.assertEqual(leads_a[self.lead_queued_a]["state"], "queued")
		self.assertEqual(leads_a[lead_revoked_stay_a]["state"], "revoked")
		self.assertEqual(pool_a["total"], baseline_total_a + 1)
		self.assertEqual(pool_a["unique_customers"], baseline_total_a + 1)
		self.assertEqual(pool_a["work_items"], baseline_wi_a + 1)

		camp_a_pool = pool_ui.list(page=1, page_size=100, campaign=self.campaign_a)
		self.assertEqual(camp_a_pool["total"], 3)
		self.assertEqual(camp_a_pool["unique_customers"], 3)
		self.assertEqual(camp_a_pool["work_items"], 3)

		# Search & filter probes as Leader A must return 0 across items, total, unique_customers, work_items.
		for q in (raw_phone_b, alt_phone_b, name_b, lead_revoked_to_b):
			res_q = pool_ui.list(page=1, page_size=20, search=q)
			self.assertEqual(res_q["items"], [])
			self.assertEqual(res_q["total"], 0)
			self.assertEqual(res_q["unique_customers"], 0)
			self.assertEqual(res_q["work_items"], 0)

		for kwargs in (
			{"campaign": self.campaign_b},
			{"source": src_b},
			{"campaign": self.campaign_a, "source": src_b},
		):
			res_f = pool_ui.list(page=1, page_size=20, **kwargs)
			self.assertEqual(res_f["items"], [])
			self.assertEqual(res_f["total"], 0)
			self.assertEqual(res_f["unique_customers"], 0)
			self.assertEqual(res_f["work_items"], 0)

		# Positive search control for Leader A on Team A's own revoked lead.
		stay_search = pool_ui.list(page=1, page_size=20, search=stay_a_phone)
		self.assertEqual([r["lead"] for r in stay_search["items"]], [lead_revoked_stay_a])
		self.assertEqual(stay_search["total"], 1)

		# --- Positive controls for Leader B, Admin, and BOD ---
		frappe.set_user(self.leader_b)
		pool_b = pool_ui.list(page=1, page_size=100)
		leads_b = {row["lead"]: row for row in pool_b["items"]}
		self.assertEqual(leads_b[lead_revoked_to_b]["state"], "assigned")
		self.assertEqual(leads_b[lead_revoked_to_b]["owner"], self.staff_c)
		self.assertEqual(leads_b[lead_revoked_to_b]["campaign"], self.campaign_b)
		self.assertEqual(leads_b[lead_closed_to_b_queued]["state"], "queued")
		self.assertEqual(leads_b[lead_closed_to_b_queued]["campaign"], self.campaign_b)
		self.assertEqual(leads_b[lead_revoked_to_shared_b]["state"], "assigned")
		self.assertEqual(leads_b[lead_revoked_to_shared_b]["owner"], self.staff_c)
		self.assertNotIn(self.lead_team_a, leads_b)
		self.assertNotIn(lead_revoked_stay_a, leads_b)

		frappe.set_user(self.admin_user)
		pool_admin = pool_ui.list(page=1, page_size=100, source=src_b)
		self.assertEqual(pool_admin["total"], 1)
		self.assertEqual(pool_admin["items"][0]["lead"], lead_revoked_to_b)
		self.assertEqual(pool_admin["items"][0]["state"], "assigned")
		self.assertEqual(pool_admin["items"][0]["campaign"], self.campaign_b)
		self.assertEqual(pool_admin["items"][0]["owner"], self.staff_c)
		self.assertEqual(pool_admin["items"][0]["phone"], raw_phone_b)

		frappe.set_user(self.bod_user)
		pool_bod = pool_ui.list(page=1, page_size=100, source=src_b)
		self.assertEqual(pool_bod["total"], 1)
		self.assertEqual(pool_bod["items"][0]["state"], "assigned")
		self.assertIsNone(pool_bod["items"][0]["phone"])
		self.assertEqual(pool_bod["items"][0]["display_name"], "")

	def test_l24_run_history_shared_campaign_scopes_assigned_skipped_preview_counters_and_polling(
		self,
	):
		campaign_shared = self.make_campaign(title="Chiến dịch Liên Đội L24", team_scope=None)

		lead_skip_a = self.make_canonical_lead()
		self.make_work_item(self.campaign_a, lead_skip_a, status="assigned", assigned_to=self.staff_a)

		lead_skip_b = self.make_canonical_lead()
		self.make_work_item(self.campaign_b, lead_skip_b, status="assigned", assigned_to=self.staff_c)

		lead_skip_global = self.make_canonical_lead()

		preview_payload = {
			"eligible_count": 7,
			"excluded_count": 3,
			"targets": [
				{"user": self.staff_a, "weight": 60, "planned": 4},
				{"user": self.staff_c, "weight": 40, "planned": 3},
			],
			"simulated": {self.staff_a: 4, self.staff_c: 3},
			"excluded_assignees": [
				{"user": self.staff_b, "weight": 0, "reason": "no_positive_weight"},
				{"user": self.staff_outside, "weight": 10, "reason": "user_disabled"},
			],
			"leads": [self.lead_team_a, self.lead_team_b, lead_skip_global],
		}

		# 1. Active `running` run mid-progress (polling check via `run_id`).
		run_running = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": campaign_shared,
				"status": "running",
				"rule_version": 1,
				"selection_fingerprint": self.unique_key("fp-l24-run"),
				"idempotency_key": self.unique_key("l24-running"),
				"progress": 5,
				"preview_json": json.dumps(preview_payload),
				"actual_json": json.dumps(
					{
						"created": 4,
						"errors": 1,
						"assigned": {self.staff_a: 2, self.staff_c: 2},
						"skipped": [
							{
								"lead": lead_skip_b,
								"reason": "active_claim_in_other_campaign",
							}
						],
					}
				),
			}
		)
		run_running.insert(ignore_permissions=True)

		# 2. Terminal `completed_with_errors` run with mixed two-team assigned & skipped items.
		run_done = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": campaign_shared,
				"status": "completed_with_errors",
				"rule_version": 1,
				"selection_fingerprint": self.unique_key("fp-l24-done"),
				"idempotency_key": self.unique_key("l24-done"),
				"progress": 7,
				"preview_json": json.dumps(preview_payload),
				"actual_json": json.dumps(
					{
						"created": 4,
						"errors": 3,
						"assigned": {self.staff_a: 3, self.staff_c: 1},
						"skipped": [
							{
								"lead": lead_skip_a,
								"reason": "active_claim_in_other_campaign",
							},
							{
								"lead": lead_skip_b,
								"reason": "active_claim_in_other_campaign",
							},
							{"lead": lead_skip_global, "reason": "capacity_full"},
						],
					}
				),
			}
		)
		run_done.insert(ignore_permissions=True)

		# --- Leader A polling & history ---
		frappe.set_user(self.leader_a)
		poll_a = run_history.runs(run_id=run_running.name, page=1, page_size=1)
		self.assertEqual(len(poll_a["items"]), 1)
		item_run_a = poll_a["items"][0]
		self.assertEqual(item_run_a["assigned"], {self.staff_a: 2})
		self.assertEqual(item_run_a["skipped"], [])
		self.assertEqual(item_run_a["created"], 2)
		self.assertEqual(item_run_a["errors"], 0)
		self.assertEqual(item_run_a["total"], 4)
		self.assertEqual(item_run_a["progress"], 2)
		self.assertEqual(item_run_a["preview"]["eligible_count"], 4)
		self.assertEqual(item_run_a["preview"]["excluded_count"], 0)
		self.assertEqual(item_run_a["preview"]["simulated"], {self.staff_a: 4})
		self.assertEqual([t["user"] for t in item_run_a["preview"]["targets"]], [self.staff_a])
		self.assertEqual(
			[e["user"] for e in item_run_a["preview"]["excluded_assignees"]],
			[self.staff_b],
		)
		self.assertNotIn(self.staff_c, json.dumps(poll_a, default=str))
		self.assertNotIn(lead_skip_b, json.dumps(poll_a, default=str))

		done_a = run_history.runs(run_id=run_done.name, page=1, page_size=1)["items"][0]
		self.assertEqual(done_a["assigned"], {self.staff_a: 3})
		self.assertEqual([s["lead"] for s in done_a["skipped"]], [lead_skip_a])
		self.assertEqual(done_a["created"], 3)
		self.assertEqual(done_a["errors"], 1)
		self.assertEqual(done_a["total"], 4)
		self.assertEqual(done_a["progress"], 4)
		self.assertNotIn(self.staff_c, json.dumps(done_a, default=str))
		self.assertNotIn(lead_skip_b, json.dumps(done_a, default=str))
		self.assertNotIn(lead_skip_global, json.dumps(done_a, default=str))

		# --- Leader B polling & history (positive control for Team B) ---
		frappe.set_user(self.leader_b)
		poll_b = run_history.runs(run_id=run_running.name, page=1, page_size=1)["items"][0]
		self.assertEqual(poll_b["assigned"], {self.staff_c: 2})
		self.assertEqual([s["lead"] for s in poll_b["skipped"]], [lead_skip_b])
		self.assertEqual(poll_b["created"], 2)
		self.assertEqual(poll_b["errors"], 1)
		self.assertEqual(poll_b["total"], 3)
		self.assertEqual(poll_b["progress"], 3)
		self.assertEqual(poll_b["preview"]["simulated"], {self.staff_c: 3})
		self.assertEqual(poll_b["preview"]["excluded_assignees"], [])
		self.assertNotIn(self.staff_a, json.dumps(poll_b, default=str))
		self.assertNotIn(self.staff_b, json.dumps(poll_b, default=str))

		done_b = run_history.runs(run_id=run_done.name, page=1, page_size=1)["items"][0]
		self.assertEqual(done_b["assigned"], {self.staff_c: 1})
		self.assertEqual([s["lead"] for s in done_b["skipped"]], [lead_skip_b])
		self.assertEqual(done_b["created"], 1)
		self.assertEqual(done_b["errors"], 1)
		self.assertEqual(done_b["total"], 3)
		self.assertEqual(done_b["progress"], 2)
		self.assertNotIn(lead_skip_a, json.dumps(done_b, default=str))
		self.assertNotIn(lead_skip_global, json.dumps(done_b, default=str))

		# --- Admin & BOD positive controls ---
		for full_actor in (self.admin_user, self.bod_user):
			frappe.set_user(full_actor)
			full_done = run_history.runs(run_id=run_done.name, page=1, page_size=1)["items"][0]
			self.assertEqual(full_done["assigned"], {self.staff_a: 3, self.staff_c: 1})
			self.assertEqual(full_done["created"], 4)
			self.assertEqual(full_done["errors"], 3)
			self.assertEqual(full_done["total"], 7)
			self.assertEqual(full_done["progress"], 7)
			self.assertEqual(full_done["preview"]["eligible_count"], 7)
			self.assertNotIn("leads", full_done["preview"])

	def test_l26_invalid_pagination_returns_400_and_assignees_applies_campaign_eligibility(
		self,
	):
		frappe.set_user(self.admin_user)
		invalid_cases = [
			{"page": "abc", "page_size": 20},
			{"page": 1, "page_size": "xyz"},
			{"page": 0, "page_size": 20},
			{"page": -1, "page_size": 20},
			{"page": 1, "page_size": 0},
			{"page": 1, "page_size": -5},
			{"page": 1, "page_size": 101},
			{"page": "1.5", "page_size": 20},
			{"page": True, "page_size": 20},
			{"page": 1, "page_size": False},
		]
		for fn in (
			pool_ui.list,
			ownership_ui.assigned_list,
			import_history.history,
			run_history.runs,
		):
			for bad_kwargs in invalid_cases:
				with self.assertRaises(InvalidRequest) as ctx:
					fn(**bad_kwargs)
				self.assertEqual(ctx.exception.http_status, 400)
				self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

			# Positive control: string digits and empty strings parse cleanly.
			ok_res = fn(page="1", page_size="2")
			self.assertEqual(ok_res["page"], 1)
			self.assertEqual(ok_res["page_size"], 2)
			empty_res = fn(page="", page_size="")
			self.assertEqual(empty_res["page"], 1)
			self.assertEqual(empty_res["page_size"], 20)

		# Campaign-scoped `assignees(campaign)`: team_scope + capacity/rule eligibility.
		res_a = ownership_ui.assignees(campaign=self.campaign_a)
		users_a = {u["user"] for u in res_a["items"]}
		self.assertIn(self.staff_a, users_a)
		self.assertIn(self.staff_b, users_a)
		self.assertNotIn(self.staff_c, users_a)
		self.assertNotIn(self.staff_outside, users_a)
		self.assertNotIn(self.admin_user, users_a)
		self.assertNotIn(self.bod_user, users_a)

		res_b = ownership_ui.assignees(campaign=self.campaign_b)
		users_b = {u["user"] for u in res_b["items"]}
		self.assertIn(self.staff_c, users_b)
		self.assertNotIn(self.staff_a, users_b)
		self.assertNotIn(self.staff_b, users_b)
		self.assertNotIn(self.staff_outside, users_b)

		# Capacity full on `staff_a` (already holds `self.wi_team_a` in `self.campaign_a`) excludes `staff_a`
		# while keeping `staff_b` (`capacity=5`) eligible.
		self.make_rule(self.campaign_a, self.staff_a, weight=1, capacity=1, team=self.team_a)
		self.make_rule(self.campaign_a, self.staff_b, weight=1, capacity=5, team=self.team_a)
		res_a_cap = ownership_ui.assignees(campaign=self.campaign_a)
		users_a_cap = {u["user"] for u in res_a_cap["items"]}
		self.assertNotIn(self.staff_a, users_a_cap)
		self.assertIn(self.staff_b, users_a_cap)

		# Shared campaign (`team_scope=None`) allows eligible Sales Users across teams.
		campaign_shared = self.make_campaign(title="Chiến dịch Chung L26", team_scope=None)
		res_shared = ownership_ui.assignees(campaign=campaign_shared)
		users_shared = {u["user"] for u in res_shared["items"]}
		self.assertIn(self.staff_a, users_shared)
		self.assertIn(self.staff_b, users_shared)
		self.assertIn(self.staff_c, users_shared)

		with self.assertRaises(NotFound) as nf_ctx:
			ownership_ui.assignees(campaign="CAMP-DOES-NOT-EXIST")
		self.assertEqual(nf_ctx.exception.http_status, 404)
		self.assertEqual(nf_ctx.exception.code, "NOT_FOUND")

		with self.assertRaises(InvalidRequest) as bad_camp_ctx:
			ownership_ui.assignees(campaign=123)
		self.assertEqual(bad_camp_ctx.exception.http_status, 400)
		self.assertEqual(bad_camp_ctx.exception.code, "INVALID_REQUEST")

	def test_upload_import_file_enforces_admin_role_private_flag_extension_and_size(
		self,
	):
		payload_b64 = base64.b64encode(b"phone,name\n0912345678,Nguyen Van A\n").decode("ascii")

		for denied in (
			self.leader_a,
			self.leader_b,
			self.staff_a,
			self.bod_user,
			self.staff_outside,
		):
			frappe.set_user(denied)
			with self.assertRaises(Forbidden):
				import_files.upload_import_file(file_name="batch.csv", content=payload_b64)

		frappe.set_user(self.admin_user)
		uploaded = import_files.upload_import_file(file_name="batch.csv", content=payload_b64)
		self.assertEqual(uploaded["is_private"], 1)
		self.assertNotIn("file_url", uploaded)
		meta = frappe.db.get_value(
			"File",
			uploaded["name"],
			["is_private", "attached_to_doctype", "attached_to_name"],
			as_dict=True,
		)
		self.assertEqual(int(meta.is_private or 0), 1)
		self.assertFalse(meta.attached_to_doctype)
		self.assertFalse(meta.attached_to_name)

		with self.assertRaises(P1Error) as ext_ctx:
			import_files.upload_import_file(file_name="bad.txt", content=payload_b64)
		self.assertEqual(ext_ctx.exception.http_status, 400)
		self.assertEqual(ext_ctx.exception.code, "UNSUPPORTED_FORMAT")

		with self.assertRaises(InvalidRequest):
			import_files.upload_import_file(file_name="empty.csv", content="")

		oversized_b64 = base64.b64encode(b"a" * (import_files.MAX_FILE_BYTES + 1)).decode("ascii")
		with self.assertRaises(P1Error) as size_ctx:
			import_files.upload_import_file(file_name="huge.csv", content=oversized_b64)
		self.assertEqual(size_ctx.exception.http_status, 413)
		self.assertEqual(size_ctx.exception.code, "FILE_TOO_LARGE")

	def test_l27_l29_leader_and_bod_ui_flows_under_strict_doctype_and_permlevel_rules(
		self,
	):
		"""Verify Leader & BOD UI endpoints work cleanly alongside L-23/L-26/L-29 DocType & permlevel lockdown."""
		import frappe.client as frappe_client

		frappe.set_user("Administrator")
		campaign_shared = self.make_campaign(
			title="Chiến dịch Chung L29",
			team_scope=None,
			selection={"source": "L29SecretSource"},
		)
		self.make_rule(campaign_shared, self.staff_a, weight=60, capacity=0, team=self.team_a)
		self.make_rule(campaign_shared, self.staff_c, weight=40, capacity=5, team=self.team_b)

		lead_shared_a = self.make_canonical_lead()
		lead_shared_b = self.make_canonical_lead()
		wi_shared_a = self.make_work_item(campaign_shared, lead_shared_a, status="queued")
		wi_shared_b = self.make_work_item(
			campaign_shared, lead_shared_b, status="assigned", assigned_to=self.staff_c
		)

		frappe.set_user(self.admin_user)
		ownership_service.assign(
			work_item=wi_shared_a,
			target_user=self.staff_a,
			reason="Giao lần đầu kiểm thử L29 Đội A",
			actor=self.admin_user,
			request_id=self.unique_key("l29-assign-a"),
		)
		ownership_service.reassign(
			work_item=wi_shared_a,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			reason="Chuyển giao kiểm thử L29 Đội A",
			actor=self.admin_user,
			request_id=self.unique_key("l29-reassign-a"),
		)

		frappe.set_user("Administrator")
		run_shared = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": campaign_shared,
				"status": "completed",
				"rule_version": 1,
				"idempotency_key": self.unique_key("l29-run-shared"),
				"selection_fingerprint": "fp-l29-shared",
				"progress": 2,
				"preview_json": json.dumps(
					{
						"eligible_count": 2,
						"excluded_count": 0,
						"simulated": {self.staff_a: 1, self.staff_c: 1},
						"targets": [
							{"user": self.staff_a, "weight": 60, "planned": 1},
							{"user": self.staff_c, "weight": 40, "planned": 1},
						],
						"excluded_assignees": [],
					}
				),
				"actual_json": json.dumps(
					{
						"created": 2,
						"errors": 0,
						"assigned": {self.staff_a: 1, self.staff_c: 1},
						"skipped": [],
					}
				),
				"state_json": json.dumps({"loads": {self.staff_a: 1, self.staff_c: 1}}),
			}
		).insert(ignore_permissions=True)

		run_team_b = frappe.get_doc(
			{
				"doctype": "CSKH Distribution Run",
				"campaign": self.campaign_b,
				"status": "completed",
				"rule_version": 1,
				"idempotency_key": self.unique_key("l29-run-b"),
				"selection_fingerprint": "fp-l29-b",
				"progress": 1,
				"preview_json": json.dumps({"eligible_count": 1, "excluded_count": 0}),
				"actual_json": json.dumps(
					{
						"created": 1,
						"errors": 0,
						"assigned": {self.staff_c: 1},
						"skipped": [],
					}
				),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# 1. Raw Frappe client / DocType / permlevel = 1 lockdown (L-29):
		#    - Non-system actors cannot write directly to P1 DocTypes;
		#    - Leader A is denied foreign-team docs (`campaign_b`, `run_team_b`, `wi_team_b`);
		#    - Leader A and BOD have `permlevel = 1` fields stripped on raw reads and blocked as filters.
		self.assertFalse(
			frappe.has_permission("CSKH Campaign", "write", doc=self.campaign_a, user=self.admin_user)
		)
		self.assertFalse(
			frappe.has_permission(
				"CSKH Distribution Run",
				"write",
				doc=run_shared.name,
				user=self.admin_user,
			)
		)

		frappe.set_user(self.admin_user)
		admin_raw_camp = frappe_client.get("CSKH Campaign", name=campaign_shared)
		self.assertIn("L29SecretSource", admin_raw_camp.get("selection_json") or "")
		admin_raw_run = frappe_client.get("CSKH Distribution Run", name=run_shared.name)
		self.assertEqual(admin_raw_run.get("progress"), 2)
		self.assertIn(self.staff_c, admin_raw_run.get("actual_json") or "")

		frappe.set_user(self.leader_a)
		with self.assertRaises(frappe.PermissionError):
			frappe_client.get("CSKH Campaign", name=self.campaign_b)
		with self.assertRaises(frappe.PermissionError):
			frappe_client.get("CSKH Distribution Run", name=run_team_b.name)
		with self.assertRaises(frappe.PermissionError):
			frappe_client.get("CSKH Work Item", name=self.wi_team_b)
		raw_camp_a = frappe_client.get("CSKH Campaign", name=campaign_shared)
		self.assertIsNone(raw_camp_a.get("selection_json"))
		self.assertNotIn("L29SecretSource", json.dumps(raw_camp_a, default=str))
		list_camp_a = frappe_client.get_list(
			"CSKH Campaign",
			fields=["name", "selection_json"],
			filters={"name": campaign_shared},
		)
		self.assertEqual(len(list_camp_a), 1)
		self.assertNotIn("selection_json", list_camp_a[0])
		with self.assertRaises(frappe.PermissionError):
			validate_p1_filter_fields(
				"CSKH Campaign",
				filters={"selection_json": ["like", "%L29%"]},
				user=self.leader_a,
			)

		frappe.set_user(self.bod_user)
		raw_run_bod = frappe_client.get("CSKH Distribution Run", name=run_shared.name)
		for restricted_field in (
			"preview_json",
			"actual_json",
			"state_json",
			"error",
			"lease_expires_at",
		):
			self.assertIsNone(raw_run_bod.get(restricted_field))
		self.assertIn(raw_run_bod.get("progress"), (None, 0))
		self.assertNotIn(self.staff_c, json.dumps(raw_run_bod, default=str))
		list_run_bod = frappe_client.get_list(
			"CSKH Distribution Run",
			fields=[
				"name",
				"preview_json",
				"actual_json",
				"state_json",
				"progress",
				"error",
				"lease_expires_at",
			],
			filters={"name": run_shared.name},
		)
		self.assertEqual(len(list_run_bod), 1)
		for restricted_field in (
			"preview_json",
			"actual_json",
			"state_json",
			"progress",
			"error",
			"lease_expires_at",
		):
			self.assertNotIn(restricted_field, list_run_bod[0])
		with self.assertRaises(frappe.PermissionError):
			validate_p1_filter_fields(
				"CSKH Distribution Run",
				filters={"actual_json": ["like", "%staff%"]},
				user=self.bod_user,
			)

		# 2. Controlled UI & contract APIs for Leader A, Leader B, and BOD continue working seamlessly:
		frappe.set_user(self.leader_a)
		la_campaigns = {c["campaign"] for c in campaigns_api.list()["items"]}
		self.assertIn(self.campaign_a, la_campaigns)
		self.assertIn(campaign_shared, la_campaigns)
		self.assertNotIn(self.campaign_b, la_campaigns)

		la_rules_shared = campaigns_api.rules(campaign=campaign_shared)
		self.assertEqual([r["user"] for r in la_rules_shared["eligible"]], [self.staff_a])
		with self.assertRaises(Forbidden):
			campaigns_api.rules(campaign=self.campaign_b)

		la_assigned = ownership_ui.assigned_list()
		self.assertFalse(la_assigned["can_manage"])
		la_wi_ids = {row["work_item"] for row in la_assigned["items"]}
		self.assertIn(self.wi_team_a, la_wi_ids)
		self.assertIn(wi_shared_a, la_wi_ids)
		self.assertNotIn(self.wi_team_b, la_wi_ids)
		self.assertNotIn(wi_shared_b, la_wi_ids)

		la_hist = ownership_api.history(work_item=wi_shared_a)
		self.assertGreaterEqual(la_hist["total"], 2)
		with self.assertRaises(Forbidden):
			ownership_api.history(work_item=self.wi_team_b)

		la_dash = dashboard_api.distribution()
		self.assertEqual(la_dash["scope"], "team")
		la_dash_users = {row["user"] for row in la_dash["per_user"]}
		self.assertTrue(la_dash_users.issubset({self.leader_a, self.staff_a, self.staff_b}))

		frappe.set_user(self.leader_b)
		lb_rules_shared = campaigns_api.rules(campaign=campaign_shared)
		self.assertEqual([r["user"] for r in lb_rules_shared["eligible"]], [self.staff_c])
		with self.assertRaises(Forbidden):
			campaigns_api.rules(campaign=self.campaign_a)

		frappe.set_user(self.bod_user)
		bod_rules_shared = campaigns_api.rules(campaign=campaign_shared)
		self.assertEqual(
			{r["user"] for r in bod_rules_shared["eligible"]},
			{self.staff_a, self.staff_c},
		)
		bod_runs = run_history.runs(campaign=campaign_shared)
		self.assertTrue(bod_runs["can_lookup"])
		self.assertFalse(bod_runs["can_distribute"])
		bod_lookup = distribution_api.get_run(run_id=run_shared.name)
		self.assertEqual(bod_lookup["status"], "completed")
		self.assertEqual(bod_lookup["progress"], 2)
		self.assertEqual(bod_lookup["total"], 2)
		self.assertEqual(bod_lookup["assigned"], {self.staff_a: 1, self.staff_c: 1})

		bod_assigned = ownership_ui.assigned_list()
		self.assertEqual(bod_assigned["scope"], "all")
		self.assertFalse(bod_assigned["can_manage"])
		for row in bod_assigned["items"]:
			self.assertEqual(row["display_name"], "")
			self.assertIsNone(row["phone"])
		bod_hist = ownership_api.history(work_item=wi_shared_a)
		self.assertGreaterEqual(bod_hist["total"], 2)

		# 3. Backend pool endpoints (`customers.customer_pool`, `customers.pool_list`) match `pool_ui.list`
		#    on L-23 cross-team authorization and L-26 400 INVALID_REQUEST validation (L-27):
		frappe.set_user(self.admin_user)
		ownership_service.revoke(
			work_item=wi_shared_a,
			expected_owner=self.staff_b,
			reason="Thu hồi Đội A chuyển Đội B (L27)",
			actor=self.admin_user,
			request_id=self.unique_key("l27-revoke"),
			return_to_pool=False,
		)
		frappe.set_user("Administrator")
		self.make_work_item(self.campaign_b, lead_shared_a, status="assigned", assigned_to=self.staff_c)
		frappe.db.commit()

		frappe.set_user(self.leader_a)
		for pool_fn in (
			pool_ui.list,
			customers_api.customer_pool,
			customers_api.pool_list,
		):
			res_la = pool_fn()
			self.assertNotIn(lead_shared_a, {row["lead"] for row in res_la["items"]})
			with self.assertRaises(InvalidRequest) as bad_page:
				pool_fn(page="bad")
			self.assertEqual(bad_page.exception.http_status, 400)
			self.assertEqual(bad_page.exception.code, "INVALID_REQUEST")

		frappe.set_user(self.leader_b)
		self.assertIn(lead_shared_a, {row["lead"] for row in pool_ui.list()["items"]})
		self.assertIn(
			lead_shared_a,
			{row["lead"] for row in customers_api.customer_pool()["items"]},
		)

		# 4. `return_to_pool=True` in Team A leaving a queued Work Item in `campaign_a`
		#    then claimed & assigned in `campaign_b` (`6c457b3`): Leader A is blocked
		#    across all 3 pool endpoints (`items`, `total`, `unique_customers`), while
		#    Leader B sees the lead in both `pool_ui.list` and `customers.customer_pool`!
		frappe.set_user("Administrator")
		lead_ret_pool = self.make_canonical_lead()
		wi_ret_a = self.make_work_item(self.campaign_a, lead_ret_pool, status="queued")
		frappe.set_user(self.admin_user)
		ownership_service.assign(
			work_item=wi_ret_a,
			target_user=self.staff_a,
			reason="Giao Đội A trước khi trả về kho",
			actor=self.admin_user,
			request_id=self.unique_key("l23-ret-asg-a"),
		)
		ownership_service.revoke(
			work_item=wi_ret_a,
			expected_owner=self.staff_a,
			reason="Trả về kho Đội A rồi chuyển Đội B",
			actor=self.admin_user,
			request_id=self.unique_key("l23-ret-rev-a"),
			return_to_pool=True,
		)
		frappe.set_user("Administrator")
		wi_ret_b = self.make_work_item(self.campaign_b, lead_ret_pool, status="queued")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_ret_pool,
				"campaign": self.campaign_b,
				"work_item": wi_ret_b,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# While `wi_ret_b` is `queued` with `Active Claim` in Team B:
		# Leader A must NOT see `lead_ret_pool` even in `customers.pool_list(campaign=self.campaign_a)`,
		# whereas Leader B DOES see `lead_ret_pool` in `customers.pool_list(campaign=self.campaign_b)`!
		frappe.set_user(self.leader_a)
		la_pool_list_a = customers_api.pool_list(campaign=self.campaign_a)
		self.assertNotIn(lead_ret_pool, {row["lead"] for row in la_pool_list_a["items"]})
		self.assertEqual(la_pool_list_a["total"], len(la_pool_list_a["items"]))
		self.assertNotIn(lead_ret_pool, {row["lead"] for row in pool_ui.list()["items"]})
		self.assertNotIn(lead_ret_pool, {row["lead"] for row in customers_api.customer_pool()["items"]})

		frappe.set_user(self.leader_b)
		lb_pool_list_b = customers_api.pool_list(campaign=self.campaign_b)
		self.assertIn(lead_ret_pool, {row["lead"] for row in lb_pool_list_b["items"]})
		self.assertIn(lead_ret_pool, {row["lead"] for row in pool_ui.list()["items"]})

		# Now assign `wi_ret_b` to `staff_c` in Team B and re-verify both teams:
		frappe.set_user(self.admin_user)
		ownership_service.assign(
			work_item=wi_ret_b,
			target_user=self.staff_c,
			reason="Giao Đội B sau khi trả kho Đội A",
			actor=self.admin_user,
			request_id=self.unique_key("l23-ret-asg-b"),
		)
		frappe.set_user(self.leader_a)
		for pool_fn in (pool_ui.list, customers_api.customer_pool, customers_api.pool_list):
			self.assertNotIn(lead_ret_pool, {row["lead"] for row in pool_fn()["items"]})
		frappe.set_user(self.leader_b)
		self.assertIn(lead_ret_pool, {row["lead"] for row in pool_ui.list()["items"]})
		self.assertIn(lead_ret_pool, {row["lead"] for row in customers_api.customer_pool()["items"]})


class TestUiP1ImportBatchPoolFilter(P1TestCase):
	"""Verify `pool_ui.list(batch=...)` against persisted `CSKH Import Batch Lead` links (`L-25` + `L-23`)."""

	def _run_csv_import(self, csv_text, mapping, key_prefix, file_name="l25-ui.csv"):
		frappe.set_user(self.admin_user)
		b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
		uploaded = import_files.upload_import_file(file_name=file_name, content=b64)
		preview = imports_api.create_preview(
			file_name=uploaded["name"],
			mapping=mapping,
			idempotency_key=self.unique_key(key_prefix),
		)
		with mock.patch("frappe.enqueue"):
			confirmed = imports_api.confirm(batch=preview["batch"])
		self.assertEqual(confirmed["status"], "QUEUED")
		frappe.set_user("Administrator")
		import_worker.run_batch(preview["batch"])
		return preview["batch"], uploaded["name"]

	def test_l25_pool_ui_import_batch_source_and_name_filters_match_persisted_import_links(
		self,
	):
		p1 = self.unique_phone()
		p2 = self.unique_phone()
		p3 = self.unique_phone()
		p4 = self.unique_phone()
		canonicals = [normalize_vn_phone(p) for p in (p1, p2, p3, p4)]
		src_alpha = self.unique_key("L25SrcAlpha")
		src_beta = self.unique_key("L25SrcBeta")
		src_gamma = self.unique_key("L25SrcGamma")
		src_delta = self.unique_key("L25SrcDelta")

		created_batches = []
		created_files = []
		created_campaigns = []

		try:
			csv_b1 = (
				"so_dt,ho_ten_kh,kenh_nguon\n"
				f"{p1},=SUM(1;2) Nguyễn L25 Một,{src_alpha}\n"
				f"{p2},Trần L25 Hai,{src_beta}\n"
				f"{p3},,{src_gamma}\n"
			)
			batch_1, file_1 = self._run_csv_import(
				csv_b1,
				{"phone": "so_dt", "name": "ho_ten_kh", "source": "kenh_nguon"},
				"l25-ui-b1",
			)
			created_batches.append(batch_1)
			created_files.append(file_1)

			# Batch 2 reuses p1 (must NOT overwrite canonical name/source) and adds p4.
			csv_b2 = (
				"so_dt,ho_ten_kh,kenh_nguon\n"
				f"{p1},Tên Ghi Đè Trái Phép,{src_delta}\n"
				f"{p4},Phạm L25 Bốn,{src_delta}\n"
			)
			batch_2, file_2 = self._run_csv_import(
				csv_b2,
				{"phone": "so_dt", "name": "ho_ten_kh", "source": "kenh_nguon"},
				"l25-ui-b2",
			)
			created_batches.append(batch_2)
			created_files.append(file_2)

			lead_1 = frappe.db.get_value(
				"CSKH Identity Index",
				{"normalized_phone": canonicals[0]},
				"canonical_lead",
			)
			lead_2 = frappe.db.get_value(
				"CSKH Identity Index",
				{"normalized_phone": canonicals[1]},
				"canonical_lead",
			)
			lead_3 = frappe.db.get_value(
				"CSKH Identity Index",
				{"normalized_phone": canonicals[2]},
				"canonical_lead",
			)
			lead_4 = frappe.db.get_value(
				"CSKH Identity Index",
				{"normalized_phone": canonicals[3]},
				"canonical_lead",
			)
			self.assertTrue(all([lead_1, lead_2, lead_3, lead_4]))

			# Verify persisted CSKH Import Batch Lead outcomes (`created` vs `reused`).
			self.assertEqual(
				frappe.db.get_value(
					"CSKH Import Batch Lead",
					{"import_batch": batch_1, "lead": lead_1},
					"outcome",
				),
				"created",
			)
			self.assertEqual(
				frappe.db.get_value(
					"CSKH Import Batch Lead",
					{"import_batch": batch_2, "lead": lead_1},
					"outcome",
				),
				"reused",
			)

			# Admin (`scope = "system"`): batch filter finds newly imported leads BEFORE any Work Item exists,
			# preserves formula sanitization + placeholder rules, and matches reused canonical leads across batches.
			frappe.set_user(self.admin_user)
			admin_b1 = pool_ui.list(batch=batch_1)
			self.assertEqual(admin_b1["total"], 3)
			self.assertEqual(admin_b1["unique_customers"], 3)
			self.assertEqual(admin_b1["work_items"], 0)
			by_lead_b1 = {row["lead"]: row for row in admin_b1["items"]}
			self.assertEqual(set(by_lead_b1), {lead_1, lead_2, lead_3})
			self.assertTrue(all(row["state"] == "eligible" for row in admin_b1["items"]))
			self.assertTrue(by_lead_b1[lead_1]["display_name"].startswith("'"))
			self.assertNotIn("Tên Ghi Đè", by_lead_b1[lead_1]["display_name"])
			self.assertEqual(by_lead_b1[lead_1]["source"], src_alpha)
			self.assertEqual(by_lead_b1[lead_2]["display_name"], "Trần L25 Hai")
			self.assertEqual(by_lead_b1[lead_3]["display_name"], "Chưa rõ tên")

			admin_b2 = pool_ui.list(batch=batch_2)
			self.assertEqual(admin_b2["total"], 2)
			self.assertEqual(admin_b2["unique_customers"], 2)
			self.assertEqual({row["lead"] for row in admin_b2["items"]}, {lead_1, lead_4})

			admin_b1_beta = pool_ui.list(batch=batch_1, source=src_beta)
			self.assertEqual(admin_b1_beta["total"], 1)
			self.assertEqual(admin_b1_beta["items"][0]["lead"], lead_2)

			# BOD (`scope = "all"`): sees batch counts with zero PII.
			frappe.set_user(self.bod_user)
			bod_b1 = pool_ui.list(batch=batch_1)
			self.assertEqual(bod_b1["total"], 3)
			for row in bod_b1["items"]:
				self.assertIsNone(row["phone"])
				self.assertEqual(row["display_name"], "")

			# Two-team + positive control: create Campaign A (Team A) & Campaign B (Team B) with Work Items
			# that do NOT set `wi.import_batch` (matching distribution_worker), proving `CSKH Import Batch Lead`
			# powers Leader batch filtering while respecting team isolation and L-23.
			frappe.set_user("Administrator")
			camp_a = self.make_campaign(title="Chiến dịch L25 Đội A", team_scope=self.team_a)
			camp_b = self.make_campaign(title="Chiến dịch L25 Đội B", team_scope=self.team_b)
			created_campaigns.extend([camp_a, camp_b])
			wi_a = self.make_work_item(camp_a, lead_2, status="assigned", assigned_to=self.staff_a)
			self.make_work_item(camp_b, lead_4, status="assigned", assigned_to=self.staff_c)
			frappe.db.commit()

			frappe.set_user(self.leader_a)
			la_b1 = pool_ui.list(batch=batch_1)
			self.assertEqual(la_b1["total"], 1)
			self.assertEqual(la_b1["work_items"], 1)
			self.assertEqual(la_b1["items"][0]["lead"], lead_2)
			self.assertEqual(la_b1["items"][0]["owner"], self.staff_a)
			la_b2 = pool_ui.list(batch=batch_2)
			self.assertEqual(la_b2["total"], 0)
			self.assertEqual(la_b2["work_items"], 0)

			frappe.set_user(self.leader_b)
			lb_b2 = pool_ui.list(batch=batch_2)
			self.assertEqual(lb_b2["total"], 1)
			self.assertEqual(lb_b2["work_items"], 1)
			self.assertEqual(lb_b2["items"][0]["lead"], lead_4)
			self.assertEqual(lb_b2["items"][0]["owner"], self.staff_c)
			lb_b1 = pool_ui.list(batch=batch_1)
			self.assertEqual(lb_b1["total"], 0)

			# L-23 + L-25 combined: revoke lead_2 in Team A (`revoked`), then assign lead_2 in Team B.
			# Leader A filtering `batch=batch_1` MUST NOT see lead_2 via old revoked history,
			# while Leader B filtering `batch=batch_1` sees lead_2 as positive control.
			frappe.set_user(self.admin_user)
			ownership_service.revoke(
				work_item=wi_a,
				expected_owner=self.staff_a,
				reason="Thu hồi Đội A chuyển sang Đội B",
				actor=self.admin_user,
				request_id=self.unique_key("l25-l23-revoke"),
				return_to_pool=False,
			)
			frappe.set_user("Administrator")
			self.make_work_item(camp_b, lead_2, status="assigned", assigned_to=self.staff_c)
			frappe.db.commit()

			frappe.set_user(self.leader_a)
			la_after = pool_ui.list(batch=batch_1)
			self.assertEqual(la_after["total"], 0)
			self.assertEqual(la_after["work_items"], 0)
			self.assertEqual(la_after["items"], [])

			frappe.set_user(self.leader_b)
			lb_after = pool_ui.list(batch=batch_1)
			self.assertEqual(lb_after["total"], 1)
			self.assertEqual(lb_after["work_items"], 1)
			self.assertEqual(lb_after["items"][0]["lead"], lead_2)
			self.assertEqual(lb_after["items"][0]["owner"], self.staff_c)
		finally:
			frappe.set_user("Administrator")
			lead_ids = frappe.get_all(
				"CSKH Identity Index",
				filters={"normalized_phone": ["in", canonicals]},
				pluck="canonical_lead",
			)
			if lead_ids:
				wi_ids = frappe.get_all("CSKH Work Item", filters={"lead": ["in", lead_ids]}, pluck="name")
				if wi_ids:
					frappe.db.delete("CSKH Assignment History", {"work_item": ["in", wi_ids]})
					frappe.db.delete(
						"ToDo",
						{
							"reference_type": "CRM Lead",
							"reference_name": ["in", lead_ids],
						},
					)
					frappe.db.delete(
						"DocShare",
						{"share_doctype": "CRM Lead", "share_name": ["in", lead_ids]},
					)
				frappe.db.delete("CSKH Active Claim", {"lead": ["in", lead_ids]})
				frappe.db.delete("CSKH Work Item", {"lead": ["in", lead_ids]})
				frappe.db.delete("CSKH Import Batch Lead", {"lead": ["in", lead_ids]})
				frappe.db.delete("CSKH Identity Index", {"normalized_phone": ["in", canonicals]})
				frappe.db.delete("CRM Lead", {"name": ["in", lead_ids]})
			if created_batches:
				frappe.db.delete("CSKH Import Batch Lead", {"import_batch": ["in", created_batches]})
				frappe.db.delete("CSKH Import Batch", {"name": ["in", created_batches]})
			if created_campaigns:
				frappe.db.delete("CSKH Campaign", {"name": ["in", created_campaigns]})
			for fid in created_files:
				if frappe.db.exists("File", fid):
					frappe.delete_doc("File", fid, force=True, ignore_permissions=True)
			frappe.db.commit()
