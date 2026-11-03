"""P2 Security Matrix (`SEC2-01..SEC2-16`) & Non-Double-Counted Progress Reconciliation (`T2-22`)."""

from __future__ import annotations

import json
import re

import frappe
import frappe.client
from frappe.desk.form.load import getdoc
from frappe.utils import add_to_date, now_datetime

from crm_cskh.api import settings as settings_api
from crm_cskh.api import workflow as workflow_api
from crm_cskh.p1_errors import normalize_p1_error_response
from crm_cskh.services import contact_attempt, correction, followup, ownership, read_model
from crm_cskh.services.errors import (
	Forbidden,
	InvalidFollowupTime,
	OwnershipChanged,
)
from crm_cskh.services.sanitizer import neutralize_csv_cell, sanitize_note_text
from crm_cskh.tests.p2_base import P2TestCase

PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")


class TestP2PermissionsAndGenericApiGuards(P2TestCase):
	"""`SEC2-01`, `SEC2-02`, `SEC2-06`, `SEC2-07`, `SEC2-08`, `SEC2-09`, `SEC2-14`: Scoped access across RPC & generic Frappe APIs."""

	def setUp(self):
		super().setUp()
		self.camp_a = self.make_campaign(team_scope=self.team_a, title="Chiến dịch Team A")
		self.camp_b = self.make_campaign(team_scope=self.team_b, title="Chiến dịch Team B")
		self.make_rule(self.camp_a, self.staff_a, weight=50)
		self.make_rule(self.camp_a, self.staff_b, weight=50)
		self.make_rule(self.camp_b, self.staff_c, weight=100)

	def test_actor_matrix_across_workflow_rpc_and_generic_frappe_surfaces(self):
		"""`SEC2-01`, `SEC2-02`, `SEC2-08`, `SEC2-09`: Staff, Leader, BOD, Admin, Outside, and Guest boundaries."""
		wi_a, lead_a = self.make_assigned_work_item(self.camp_a, self.staff_a)
		wi_b, _ = self.make_assigned_work_item(self.camp_b, self.staff_c)

		cl_a = self.make_native_call_log(lead_a)
		att_a = contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="callback_requested",
			call_log=cl_a,
			note="Ghi chú riêng Team A",
			followup={"due_at": self.future_due_str(hours=3), "title": "Hẹn Team A"},
			idempotency_key=self.unique_key("sec-a"),
			actor=self.staff_a,
		)
		att_b = contact_attempt.record_attempt(
			work_item=wi_b,
			channel="phone_call",
			outcome_code="no_answer",
			note="Ghi chú riêng Team B",
			idempotency_key=self.unique_key("sec-b"),
			actor=self.staff_c,
		)

		corr_a = correction.request_correction(
			attempt=att_a["attempt_id"],
			action="correct",
			corrected_fields={"note": "Cập nhật nội dung Team A"},
			reason="Bổ sung chi tiết",
			idempotency_key=self.unique_key("sec-corr-a"),
			actor=self.staff_a,
		)
		corr_b = correction.request_correction(
			attempt=att_b["attempt_id"],
			action="void",
			reason="Hủy thử tại Team B",
			idempotency_key=self.unique_key("sec-corr-b"),
			actor=self.staff_c,
		)

		# 1. Guest is blocked everywhere
		frappe.set_user("Guest")
		with self.assertRaises(Forbidden):
			workflow_api.list_work_items()
		with self.assertRaises(Forbidden):
			workflow_api.progress_summary()

		# 2. Staff A sees own item (`wi_a`), blocked on `wi_b` and blocked from spoofing `assignee=staff_c` (`SEC2-08`)
		frappe.set_user(self.staff_a)
		own_q = workflow_api.list_work_items(campaign=self.camp_a)
		self.assertEqual([x["work_item"] for x in own_q["items"]], [wi_a])
		self.assertEqual(workflow_api.list_work_items(campaign=self.camp_b)["total"], 0)
		spoof_q = workflow_api.list_work_items(assignee=self.staff_c)
		self.assertEqual(spoof_q["total"], 0)
		with self.assertRaises(Forbidden):
			workflow_api.get_work_item(work_item=wi_b)

		# Generic Frappe `get_list` & `frappe.client.get` & `getdoc` for Staff A
		att_list_staff_a = frappe.get_list("CSKH Contact Attempt", pluck="name")
		self.assertIn(att_a["attempt_id"], att_list_staff_a)
		self.assertNotIn(att_b["attempt_id"], att_list_staff_a)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("CSKH Contact Attempt", att_b["attempt_id"])
		with self.assertRaises(frappe.PermissionError):
			getdoc("CSKH Contact Attempt", att_b["attempt_id"])

		# 3. Leader A sees `wi_a` / `att_a` / `corr_a`, blocked on `wi_b` / `att_b`
		frappe.set_user(self.leader_a)
		ldr_a_att = frappe.get_list("CSKH Contact Attempt", pluck="name")
		self.assertIn(att_a["attempt_id"], ldr_a_att)
		self.assertNotIn(att_b["attempt_id"], ldr_a_att)
		with self.assertRaises(Forbidden):
			workflow_api.get_work_item(work_item=wi_b)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("CSKH Attempt Correction", corr_b["correction_id"])
		self.assertTrue(
			frappe.has_permission(
				"CSKH Attempt Correction",
				"read",
				doc=frappe.get_doc("CSKH Attempt Correction", corr_a["correction_id"]),
			)
		)

		# Leader B cannot read `corr_a` or `att_a`
		frappe.set_user(self.leader_b)
		self.assertFalse(
			frappe.has_permission(
				"CSKH Attempt Correction",
				"read",
				doc=frappe.get_doc("CSKH Attempt Correction", corr_a["correction_id"]),
			)
		)
		with self.assertRaises(Forbidden):
			workflow_api.list_attempts(work_item=wi_a)

		# 4. `BOD Viewer` gets 0-PII `progress_summary`, blocked on detail/list PII surfaces (`SEC2-09`)
		frappe.set_user(self.bod_user)
		bod_sum = workflow_api.progress_summary()
		serialized_bod = json.dumps(bod_sum, ensure_ascii=False, default=str)
		self.assertIsNone(PHONE_RE.search(serialized_bod))
		self.assertNotIn(lead_a, serialized_bod)
		self.assertNotIn("Ghi chú riêng Team A", serialized_bod)

		for blocked_fn in (
			lambda: workflow_api.list_work_items(),
			lambda: workflow_api.get_work_item(work_item=wi_a),
			lambda: workflow_api.list_attempts(work_item=wi_a),
			lambda: workflow_api.list_followups(),
			lambda: workflow_api.list_corrections(),
		):
			with self.assertRaises(Forbidden):
				blocked_fn()
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("CSKH Contact Attempt", pluck="name")

		# 5. `CRM Admin` reads both teams and settings
		frappe.set_user(self.admin_user)
		adm_q = workflow_api.list_work_items(campaign=self.camp_a)
		self.assertEqual(adm_q["total"], 1)
		self.assertGreaterEqual(settings_api.list_outcomes()["total"], 12)
		frappe.set_user("Administrator")

	def test_post_reassign_and_post_close_cross_team_total_pii_revocation(self):
		"""`SEC2-06`, `SEC2-07`, `L-23`: Old owner loses all access on `reassign(A -> B)`, and Team A loses closed-item access once Lead moves to Team B."""
		wi_a, lead = self.make_assigned_work_item(self.camp_a, self.staff_a)
		cl = self.make_native_call_log(lead)
		note_doc = frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "Ghi chú nhạy cảm",
				"content": "Nội dung khách hàng",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		).insert(ignore_permissions=True)

		att = contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="callback_requested",
			call_log=cl,
			note="Hẹn gọi lại",
			followup={"due_at": self.future_due_str(hours=4), "title": "Callback nhạy cảm"},
			idempotency_key=self.unique_key("rev-pii-att"),
			actor=self.staff_a,
		)
		task_id = att["callback_task"]

		# Reassign `Staff A -> Staff B` inside `camp_a`
		ownership.reassign(
			wi_a,
			self.staff_a,
			self.staff_b,
			reason="Chuyển bàn giao hoàn toàn sang Staff B",
			request_id=self.unique_key("rev-pii-ab"),
			actor=self.admin_user,
		)

		# Staff A loses EVERY read/mutation path on Lead, WorkItem, Attempt, Task, CallLog, Note!
		frappe.set_user(self.staff_a)
		self.assertFalse(frappe.has_permission("CRM Lead", "read", doc=frappe.get_doc("CRM Lead", lead)))
		self.assertFalse(
			frappe.has_permission("CSKH Work Item", "read", doc=frappe.get_doc("CSKH Work Item", wi_a))
		)
		self.assertFalse(
			frappe.has_permission(
				"CSKH Contact Attempt", "read", doc=frappe.get_doc("CSKH Contact Attempt", att["attempt_id"])
			)
		)
		self.assertFalse(frappe.has_permission("CRM Task", "read", doc=frappe.get_doc("CRM Task", task_id)))
		self.assertFalse(
			frappe.has_permission("CRM Call Log", "read", doc=frappe.get_doc("CRM Call Log", cl))
		)
		self.assertFalse(frappe.has_permission("FCRM Note", "read", doc=note_doc))

		with self.assertRaises(Forbidden):
			workflow_api.get_work_item(work_item=wi_a)
		with self.assertRaises(OwnershipChanged):
			contact_attempt.record_attempt(
				work_item=wi_a,
				channel="phone_call",
				outcome_code="no_answer",
				idempotency_key=self.unique_key("stale-owner-att"),
				actor=self.staff_a,
			)

		# Now Staff B closes `wi_a` -> while Lead is unowned in pool, Staff B & Leader A can still inspect closed `wi_a`
		frappe.set_user("Administrator")
		contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="connected_not_interested",
			complete_open_followup=True,
			close_work_item=True,
			completion_reason="Đóng tại Team A",
			idempotency_key=self.unique_key("close-in-a"),
			actor=self.staff_b,
		)
		self.assertEqual(
			read_model.get_work_item_detail(wi_a, actor=self.staff_b)["work_item"]["status"],
			"closed",
		)

		# Now Lead is assigned to `Staff C` in `Team B` (`camp_b`) -> BOTH `Staff B` and `Leader A` MUST lose read access to `wi_a` (`L-23` / `SEC2-07`)!
		self.make_assigned_work_item(self.camp_b, self.staff_c, lead=lead)
		for former_team_actor in (self.staff_b, self.leader_a):
			frappe.set_user(former_team_actor)
			with self.assertRaises(Forbidden):
				workflow_api.get_work_item(work_item=wi_a)
			with self.assertRaises(Forbidden):
				workflow_api.list_attempts(work_item=wi_a)
			self.assertNotIn(att["attempt_id"], frappe.get_list("CSKH Contact Attempt", pluck="name"))
		frappe.set_user("Administrator")


class TestP2AntiSpoofingSanitizationBackdateAndSummary(P2TestCase):
	"""`SEC2-03`, `SEC2-04`, `SEC2-10`, `SEC2-13`, `SEC2-15`, `T2-22`: Anti-spoofing, XSS/CSV sanitizer, backdate bounds, PII error redaction, and 10-Lead / 15-Attempt reconciliation."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=50)

	def test_client_spoofed_identity_or_verification_fields_rejected_with_403(self):
		"""`SEC2-03`, `SEC2-04`: Supplying `performed_by`, `verification_status`, `actor`, `closed_by`, `reviewed_by` fails closed."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		frappe.set_user(self.staff_a)

		for spoof_kwargs in (
			{"performed_by": self.staff_b},
			{"verification_status": "verified"},
			{"owner_at_attempt": self.staff_b},
			{"actor": "Administrator"},
		):
			with self.assertRaises(Forbidden):
				workflow_api.record_attempt(
					work_item=wi,
					channel="phone_call",
					outcome_code="no_answer",
					idempotency_key=self.unique_key("spoof"),
					**spoof_kwargs,
				)

		with self.assertRaises(Forbidden):
			workflow_api.close_work_item(
				work_item=wi,
				completion_reason="Giả mạo người đóng",
				close_category="invalid_or_duplicate",
				idempotency_key=self.unique_key("spoof-close"),
				closed_by=self.leader_a,
			)
		frappe.set_user("Administrator")

	def test_xss_formula_sanitization_backdate_bounds_and_pii_error_redaction(self):
		"""`SEC2-10`, `SEC2-13`, `SEC2-15`: Strips `<script>`/event handlers, neutralizes `=CMD`, enforces backdate window, and scrubs phone numbers from errors."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		# XSS & spreadsheet formula neutralization
		dirty_note = '=CMD|\' /C calc\'!A0 <script>alert("xss")</script> <img src=x onerror="evil()"> Khách hẹn gọi lại'
		rec = contact_attempt.record_attempt(
			work_item=wi,
			channel="zalo",
			outcome_code="other",
			note=dirty_note,
			idempotency_key=self.unique_key("san-1"),
			actor=self.staff_a,
		)
		stored_note = frappe.db.get_value("CSKH Contact Attempt", rec["attempt_id"], "note")
		self.assertNotIn("<script", stored_note.lower())
		self.assertNotIn("onerror", stored_note.lower())
		self.assertTrue(neutralize_csv_cell(stored_note).startswith("'="))
		self.assertEqual(sanitize_note_text("<a href='javascript:evil()'>Bấm</a>"), "Bấm")

		# `SEC2-15`: Future `occurred_at`, beyond `allowed_backdate_minutes` (120m), or before `ownership_start` rejected!
		for bad_dt in (
			str(add_to_date(now_datetime(), minutes=30)),
			str(add_to_date(now_datetime(), hours=-5)),
			str(
				add_to_date(now_datetime(), minutes=-10)
			),  # 10m ago is before `ownership_start` (just assigned now)!
		):
			with self.assertRaises(InvalidFollowupTime):
				contact_attempt.record_attempt(
					work_item=wi,
					channel="phone_call",
					outcome_code="no_answer",
					occurred_at=bad_dt,
					idempotency_key=self.unique_key("bad-occ"),
					actor=self.staff_a,
				)

		# `SEC2-13`: `normalize_p1_error_response` scrubs Vietnamese phone numbers from error payloads
		fake_resp = frappe._dict(
			status_code=400,
			mimetype="application/json",
			data=json.dumps(
				{
					"exc_type": "InvalidRequest",
					"message": "Lỗi xử lý khách hàng SĐT 0912345678 / +84987654321",
					"exception": "InvalidRequest: SĐT 0912345678 không hợp lệ",
				},
				ensure_ascii=False,
			),
		)
		normalize_p1_error_response(fake_resp)
		normalized_err = json.loads(fake_resp.data)
		self.assertNotIn("0912345678", normalized_err["message"])
		self.assertNotIn("84987654321", normalized_err["message"])
		self.assertIn("[REDACTED_PHONE]", normalized_err["message"])

	def test_progress_summary_reconciles_10_leads_15_attempts_without_double_counting(self):
		"""`T2-22`: 10 Leads, 15 raw attempts (1 voided -> 14 effective), reassign A -> B, exact DB match & 0 PII."""
		camp = self.make_campaign(team_scope=self.team_a, title="Chiến dịch Đối soát 10/15")
		self.make_rule(camp, self.staff_a, weight=50, capacity=20)
		self.make_rule(camp, self.staff_b, weight=50, capacity=20)

		wis_a = [self.make_assigned_work_item(camp, self.staff_a)[0] for _ in range(6)]
		wis_b = [self.make_assigned_work_item(camp, self.staff_b)[0] for _ in range(4)]

		# Staff A records:
		# - wi_a[0]: 2 attempts (`no_answer`, `connected_interested`) -> `in_progress` (2 eff, 1 conn)
		# - wi_a[1]: 2 attempts (`busy`, `callback_requested` + followup) -> `on_hold` (2 eff, 1 conn), then REASSIGNED to Staff B!
		# - wi_a[2]: 2 attempts (`no_answer`, `converted` -> `closed`) (2 eff, 1 conn)
		# - wi_a[3]: 2 attempts (`no_answer`, plus 1 `no_answer` that gets VOIDED) -> `in_progress` (1 eff, 0 conn)
		# - wi_a[4], wi_a[5]: untouched `assigned` (0 attempts)
		contact_attempt.record_attempt(
			work_item=wis_a[0],
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wis_a[0],
			channel="phone_call",
			outcome_code="connected_interested",
			note="Quan tâm",
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)

		contact_attempt.record_attempt(
			work_item=wis_a[1],
			channel="phone_call",
			outcome_code="busy",
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wis_a[1],
			channel="phone_call",
			outcome_code="callback_requested",
			note="Hẹn lại",
			followup={"due_at": self.future_due_str(hours=4)},
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)
		ownership.reassign(
			wis_a[1],
			self.staff_a,
			self.staff_b,
			reason="Chuyển ca",
			request_id=self.unique_key(),
			actor=self.admin_user,
		)

		contact_attempt.record_attempt(
			work_item=wis_a[2],
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wis_a[2],
			channel="zalo",
			outcome_code="converted",
			note="Chốt thành công",
			close_work_item=True,
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)

		contact_attempt.record_attempt(
			work_item=wis_a[3],
			channel="sms",
			outcome_code="message_sent_no_reply",
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)
		to_void = contact_attempt.record_attempt(
			work_item=wis_a[3],
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key(),
			actor=self.staff_a,
		)
		correction.request_correction(
			attempt=to_void["attempt_id"],
			action="void",
			reason="Bấm nhầm",
			auto_approve=True,
			idempotency_key=self.unique_key(),
			actor=self.leader_a,
		)

		# Staff B records 7 more attempts across `wis_a[1]` and `wis_b[0..2]` (total raw = 8 + 7 = 15, voided = 1 -> 14 effective):
		# - wis_a[1] (reassigned from A): 1 attempt (`connected_interested`, resolves callback -> `in_progress`)
		# - wis_b[0]: 2 attempts (`unreachable`, `do_not_contact` -> `closed` + DNC)
		# - wis_b[1]: 2 attempts (`no_answer`, `callback_requested` + followup -> `on_hold`)
		# - wis_b[2]: 2 attempts (`message_sent_no_reply`, `connected_not_interested` + close -> `closed`)
		# - wis_b[3]: untouched `assigned`
		contact_attempt.record_attempt(
			work_item=wis_a[1],
			channel="phone_call",
			outcome_code="connected_interested",
			note="Đã trao đổi sau bàn giao",
			complete_open_followup=True,
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)
		contact_attempt.record_attempt(
			work_item=wis_b[0],
			channel="phone_call",
			outcome_code="unreachable",
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)
		contact_attempt.record_attempt(
			work_item=wis_b[0],
			channel="phone_call",
			outcome_code="do_not_contact",
			note="DNC",
			completion_reason="Ngừng gọi",
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)
		contact_attempt.record_attempt(
			work_item=wis_b[1],
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)
		contact_attempt.record_attempt(
			work_item=wis_b[1],
			channel="phone_call",
			outcome_code="callback_requested",
			followup={"due_at": self.future_due_str(hours=5)},
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)
		contact_attempt.record_attempt(
			work_item=wis_b[2],
			channel="zalo",
			outcome_code="message_sent_no_reply",
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)
		contact_attempt.record_attempt(
			work_item=wis_b[2],
			channel="phone_call",
			outcome_code="connected_not_interested",
			close_work_item=True,
			completion_reason="Không nhu cầu",
			idempotency_key=self.unique_key(),
			actor=self.staff_b,
		)

		self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"campaign": camp}), 15)
		summary = read_model.progress_summary(campaign=camp, actor=self.bod_user)
		cards = summary["cards"]

		# Total 10 Work Items: 3 `assigned` (wis_a[4], wis_a[5], wis_b[3]), 3 `in_progress` (wis_a[0], wis_a[3], wis_a[1]), 1 `on_hold` (wis_b[1]), 3 `closed` (wis_a[2], wis_b[0], wis_b[2])
		self.assertEqual(cards["assigned"], 3)
		self.assertEqual(cards["in_progress"], 3)
		self.assertEqual(cards["on_hold"], 1)
		self.assertEqual(cards["closed"], 3)
		self.assertEqual(cards["total_active_owned"], 7)

		# 15 raw - 1 voided = 14 effective attempts; connected = 7 (`wis_a[0]`x1, `wis_a[1]`x2, `wis_a[2]`x1, `wis_b[0]`x1 DNC, `wis_b[1]`x1 cb, `wis_b[2]`x1 not_int)
		self.assertEqual(cards["effective_attempts"], 14)
		self.assertEqual(cards["connected_attempts"], 7)
		self.assertEqual(cards["unreached_attempts"], 7)
		self.assertEqual(cards["open_followups"], 1)
		self.assertEqual(cards["dnc_leads"], 1)

		# Per-user attribution: `performed_by` credits Staff A with 7 effective attempts and Staff B with 7 effective attempts (no double count!)
		per_user = {row["user"]: row for row in summary["per_user"]}
		self.assertEqual(per_user[self.staff_a]["effective_attempts"], 7)
		self.assertEqual(per_user[self.staff_b]["effective_attempts"], 7)
		self.assertEqual(sum(r["effective_attempts"] for r in summary["per_user"]), 14)
		self.assertTrue(read_model.reconcile_p2_invariants(camp)["ok"])
