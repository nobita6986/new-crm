"""P2 Reconciliation Gate, `A -> B -> A` Journey, 120-Attempt Pagination & Schema Idempotency (`P2.md` §15.2, §15.4, §15.5)."""

from __future__ import annotations

import frappe

from crm_cskh.patches.v2_0.p2_schema_and_seeds import (
	ensure_p2_custom_fields,
	ensure_p2_indexes,
	ensure_p2_seeds,
)
from crm_cskh.services import contact_attempt, followup, ownership, read_model
from crm_cskh.services.errors import InvalidRequest
from crm_cskh.tests.p2_base import P2TestCase


class TestP2ReconciliationTimelineAndSchema(P2TestCase):
	"""`P2.md` §15.2, §15.4, §15.5: `A -> B -> A` journey, 100+ Work Items, 120-attempt pagination, and corruption detection."""

	def setUp(self):
		super().setUp()
		self.camp_a = self.make_campaign(team_scope=self.team_a, title="Chiến dịch A")
		self.camp_b = self.make_campaign(team_scope=self.team_b, title="Chiến dịch B")
		self.camp_c = self.make_campaign(team_scope=self.team_a, title="Chiến dịch Tái chăm sóc A")
		self.make_rule(self.camp_a, self.staff_a, weight=50)
		self.make_rule(self.camp_a, self.staff_b, weight=50)
		self.make_rule(self.camp_b, self.staff_c, weight=100)
		self.make_rule(self.camp_c, self.staff_a, weight=100)

	def test_lead_journey_a_to_b_to_a_across_campaigns_and_105_work_items_reconcile(self):
		"""`P2.md` §15.2: Lead moves `A -> closed -> B -> closed -> A` plus 105 mixed Work Items with `ok=True`."""
		lead = self.make_canonical_lead(first_name="Khách A-B-A")

		# Leg 1: Campaign A (Staff A -> reassign Staff B -> close)
		wi1, _ = self.make_assigned_work_item(self.camp_a, self.staff_a, lead=lead)
		contact_attempt.record_attempt(
			work_item=wi1,
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key("aba-1"),
			actor=self.staff_a,
		)
		ownership.reassign(
			wi1,
			self.staff_a,
			self.staff_b,
			reason="Chuyển cho B",
			request_id=self.unique_key("aba-r1"),
			actor=self.admin_user,
		)
		contact_attempt.record_attempt(
			work_item=wi1,
			channel="phone_call",
			outcome_code="connected_not_interested",
			close_work_item=True,
			completion_reason="Chưa mua đợt 1",
			idempotency_key=self.unique_key("aba-2"),
			actor=self.staff_b,
		)

		# Leg 2: Campaign B (Staff C -> close)
		wi2, _ = self.make_assigned_work_item(self.camp_b, self.staff_c, lead=lead)
		contact_attempt.record_attempt(
			work_item=wi2,
			channel="zalo",
			outcome_code="message_sent_no_reply",
			idempotency_key=self.unique_key("aba-3"),
			actor=self.staff_c,
		)
		contact_attempt.close_work_item(
			work_item=wi2,
			completion_reason="Kết thúc đợt 2 tại Team B",
			idempotency_key=self.unique_key("aba-c2"),
			actor=self.staff_c,
		)

		# Leg 3: Campaign C back to Staff A! Staff C & Leader B cannot read `wi2` anymore (`L-23`), while Staff A owns `wi3`
		wi3, _ = self.make_assigned_work_item(self.camp_c, self.staff_a, lead=lead)
		contact_attempt.record_attempt(
			work_item=wi3,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Quay lại quan tâm đợt 3",
			idempotency_key=self.unique_key("aba-4"),
			actor=self.staff_a,
		)
		self.assertFalse(
			frappe.has_permission(
				"CSKH Work Item", "read", doc=frappe.get_doc("CSKH Work Item", wi2), user=self.staff_c
			)
		)

		# Create 102 additional mixed Work Items (`queued`, `assigned`, `in_progress`, `on_hold`, `closed`, `revoked`) -> 105 total!
		for idx in range(102):
			if idx % 6 == 0:
				l_q = self.make_canonical_lead()
				self.make_queued_work_item(self.camp_a, l_q)
			elif idx % 6 == 1:
				self.make_assigned_work_item(self.camp_a, self.staff_a)
			elif idx % 6 == 2:
				wi_ip, _ = self.make_assigned_work_item(self.camp_a, self.staff_b)
				contact_attempt.record_attempt(
					work_item=wi_ip,
					channel="phone_call",
					outcome_code="busy",
					idempotency_key=self.unique_key(f"mix-ip-{idx}"),
					actor=self.staff_b,
				)
			elif idx % 6 == 3:
				wi_oh, _ = self.make_assigned_work_item(self.camp_b, self.staff_c)
				followup.schedule_followup(
					work_item=wi_oh,
					due_at=self.future_due_str(hours=2 + (idx % 10)),
					title=f"Callback #{idx}",
					idempotency_key=self.unique_key(f"mix-oh-{idx}"),
					actor=self.staff_c,
				)
			elif idx % 6 == 4:
				wi_cl, _ = self.make_assigned_work_item(self.camp_a, self.staff_a)
				contact_attempt.close_work_item(
					work_item=wi_cl,
					completion_reason="Dữ liệu sai",
					close_category="invalid_or_duplicate",
					idempotency_key=self.unique_key(f"mix-cl-{idx}"),
					actor=self.staff_a,
				)
			else:
				wi_rv, _ = self.make_assigned_work_item(self.camp_b, self.staff_c)
				ownership.revoke(
					wi_rv,
					self.staff_c,
					reason="Thu hồi mẫu",
					return_to_pool=False,
					request_id=self.unique_key(f"mix-rv-{idx}"),
					actor=self.admin_user,
				)

		for camp in (self.camp_a, self.camp_b, self.camp_c):
			rec = read_model.reconcile_p2_invariants(camp)
			self.assertTrue(rec["ok"], f"reconciliation failed on {camp}: {rec}")

	def test_single_work_item_with_120_attempts_paginates_cleanly_and_detects_counter_drift(self):
		"""`P2.md` §15.4-§15.5: 120-attempt timeline paginates boundedly; `reconcile_p2_invariants` catches drift."""
		wi, _ = self.make_assigned_work_item(self.camp_a, self.staff_a)
		for idx in range(120):
			code = "connected_interested" if idx % 10 == 0 else "no_answer"
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code=code,
				note=f"Lần gọi #{idx + 1}",
				idempotency_key=self.unique_key(f"tl120-{idx}"),
				actor=self.staff_a,
			)

		p1 = read_model.list_attempts_timeline(wi, page=1, page_size=25, actor=self.staff_a)
		p5 = read_model.list_attempts_timeline(wi, page=5, page_size=25, actor=self.staff_a)
		self.assertEqual(p1["total"], 120)
		self.assertEqual(len(p1["items"]), 25)
		self.assertEqual(len(p5["items"]), 20)
		# Newest first ordering
		self.assertEqual(p1["items"][0]["effective"]["note"], "Lần gọi #120")
		self.assertEqual(p5["items"][-1]["effective"]["note"], "Lần gọi #1")

		with self.assertRaises(InvalidRequest):
			read_model.list_attempts_timeline(wi, page=1, page_size=500, actor=self.staff_a)

		# Negative check: corrupt `attempt_count` in SQL -> `reconcile_p2_invariants` MUST report `ok=False`
		frappe.db.sql("update `tabCSKH Work Item` set attempt_count = 999 where name = %s", (wi,))
		bad_rec = read_model.reconcile_p2_invariants(self.camp_a)
		self.assertFalse(bad_rec["ok"])
		self.assertGreaterEqual(len(bad_rec["counter_drifts"]), 1)

		# Repair via `recompute_work_item_read_model` -> returns to `ok=True` (`attempt_count=120`, `connected=12`)
		repaired = read_model.recompute_work_item_read_model(wi, bump_revision=False)
		self.assertEqual(repaired["attempt_count"], 120)
		self.assertEqual(repaired["connected_attempt_count"], 12)
		self.assertTrue(read_model.reconcile_p2_invariants(self.camp_a)["ok"])

	def test_p2_migration_seeds_and_mariadb_indexes_are_idempotent(self):
		"""`P2.md` §15.4: Running P2 schema/index/seed helpers twice is idempotent and preserves required indexes."""
		app_hooks = frappe.get_hooks(app_name="crm_cskh")
		self.assertIn(
			"crm_cskh.patches.v2_0.p2_schema_and_seeds.execute",
			app_hooks.get("after_install", []),
		)
		self.assertIn(
			"crm_cskh.patches.v2_0.p2_schema_and_seeds.execute",
			app_hooks.get("after_migrate", []),
		)
		ensure_p2_custom_fields()
		ensure_p2_indexes()
		ensure_p2_seeds()
		ensure_p2_custom_fields()
		ensure_p2_indexes()
		ensure_p2_seeds()

		expected_indexes = {
			"tabCSKH Outcome Code": {"cskh_outcome_enabled_sort_idx"},
			"tabCSKH Policy": {"cskh_policy_scope_idx"},
			"tabCSKH Contact Attempt": {
				"cskh_attempt_idem_uq",
				"cskh_attempt_call_log_uq",
				"cskh_attempt_wi_occ_idx",
				"cskh_attempt_lead_occ_idx",
				"cskh_attempt_camp_occ_idx",
				"cskh_attempt_perf_occ_idx",
				"cskh_attempt_out_occ_idx",
			},
			"tabCSKH Attempt Correction": {
				"cskh_corr_req_idem_uq",
				"cskh_corr_attempt_status_idx",
				"cskh_corr_wi_status_idx",
			},
			"tabCRM Task": {
				"cskh_task_wi_open_idx",
				"cskh_task_assignee_due_idx",
				"cskh_task_followup_due_idx",
			},
			"tabCSKH Work Item": {"cskh_wi_queue_due_idx", "cskh_wi_camp_last_idx"},
		}
		for table, req_names in expected_indexes.items():
			rows = frappe.db.sql(f"SHOW INDEX FROM `{table}`", as_dict=True)
			present = {r.Key_name for r in rows}
			self.assertTrue(
				req_names.issubset(present),
				f"missing indexes on {table}: {req_names - present}",
			)
