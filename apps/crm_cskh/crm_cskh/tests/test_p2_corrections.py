"""P2 Contact Attempt Immutability & Audited Correction/Void/Restore Overlay (`T2-19`, `T2-20`, `SEC2-03`)."""

from __future__ import annotations

import frappe

from crm_cskh.services import contact_attempt, correction, policies, read_model
from crm_cskh.services.errors import Forbidden, InvalidRequest, NoteRequired
from crm_cskh.tests.p2_base import P2TestCase


class TestP2AttemptImmutabilityAndCorrections(P2TestCase):
	"""`T2-19`, `T2-20`: Raw `CSKH Contact Attempt` never mutates or deletes; corrections project cleanly."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=50)

	def test_contact_attempt_and_correction_docs_enforce_orm_immutability_and_no_delete(self):
		"""`T2-19`, `SEC2-03`: Direct insert, business-field edit, or delete on `CSKH Contact Attempt` fails closed."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)

		# Direct ORM insert outside `record_attempt` service is blocked even for Administrator
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "CSKH Contact Attempt",
					"work_item": wi,
					"lead": lead,
					"campaign": self.campaign,
					"performed_by": self.staff_a,
					"occurred_at": frappe.utils.now_datetime(),
					"channel": "phone_call",
					"outcome_code": "no_answer",
					"idempotency_key": self.unique_key("raw-bypass"),
				}
			).insert(ignore_permissions=True)

		rec = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			note="Bản ghi gốc bất biến",
			idempotency_key=self.unique_key("imm-1"),
			actor=self.staff_a,
		)
		att_id = rec["attempt_id"]

		# Direct mutation of `outcome_code` or `note` is rejected
		att_doc = frappe.get_doc("CSKH Contact Attempt", att_id)
		att_doc.outcome_code = "connected_interested"
		with self.assertRaises(frappe.ValidationError):
			att_doc.save(ignore_permissions=True)

		# Direct deletion is rejected even with `force=True, ignore_permissions=True`
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("CSKH Contact Attempt", att_id, force=True, ignore_permissions=True)

	def test_correction_request_reject_approve_void_and_admin_restore_recompute_read_model(self):
		"""`T2-20`: Full pending -> reject/approve -> void -> restore flow preserving raw attempt truth."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		rec = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			note="Nhân viên bấm nhầm mã không nghe máy",
			idempotency_key=self.unique_key("corr-base"),
			actor=self.staff_a,
		)
		att_id = rec["attempt_id"]
		self.assertEqual(rec["connected_attempt_count"], 0)

		# Staff cannot pass `auto_approve=True`, cannot pass disallowed field `performed_by`, and Staff B cannot touch Staff A's attempt
		with self.assertRaises(Forbidden):
			correction.request_correction(
				attempt=att_id,
				action="correct",
				corrected_fields={"outcome_code": "connected_interested"},
				reason="Tự duyệt trái phép",
				auto_approve=True,
				idempotency_key=self.unique_key("corr-self-app"),
				actor=self.staff_a,
			)

		with self.assertRaises(InvalidRequest):
			correction.request_correction(
				attempt=att_id,
				action="correct",
				corrected_fields={"performed_by": self.staff_b},
				reason="Thử sửa người thực hiện",
				idempotency_key=self.unique_key("corr-bad-field"),
				actor=self.staff_a,
			)

		with self.assertRaises(Forbidden):
			correction.request_correction(
				attempt=att_id,
				action="correct",
				corrected_fields={"note": "Người khác sửa"},
				reason="Staff B sửa bài của Staff A",
				idempotency_key=self.unique_key("corr-other-staff"),
				actor=self.staff_b,
			)

		# 1. Staff A submits pending request -> `reject` requires `review_note`
		req1 = correction.request_correction(
			attempt=att_id,
			action="correct",
			corrected_fields={"outcome_code": "connected_interested", "note": "Khách có nghe máy"},
			reason="Chọn nhầm dropdown trên màn hình",
			idempotency_key=self.unique_key("corr-req1"),
			actor=self.staff_a,
		)
		self.assertEqual(req1["status"], "pending")
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "connected_attempt_count") or 0), 0)

		with self.assertRaises(NoteRequired):
			correction.review_correction(
				correction_id=req1["correction_id"],
				decision="reject",
				review_note="",
				idempotency_key=self.unique_key("rev-noreason"),
				actor=self.leader_a,
			)

		rej = correction.review_correction(
			correction_id=req1["correction_id"],
			decision="reject",
			review_note="Chưa đính kèm mã cuộc gọi xác minh",
			idempotency_key=self.unique_key("rev-rej"),
			actor=self.leader_a,
		)
		self.assertEqual(rej["status"], "rejected")
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "connected_attempt_count") or 0), 0)

		# 2. Staff A submits second request -> Leader A approves -> `connected_attempt_count` becomes 1, raw DB stays `no_answer`
		req2 = correction.request_correction(
			attempt=att_id,
			action="correct",
			corrected_fields={
				"outcome_code": "connected_interested",
				"note": "Đã đối chiếu băng ghi âm, khách quan tâm",
			},
			reason="Bổ sung xác nhận cuộc gọi",
			idempotency_key=self.unique_key("corr-req2"),
			actor=self.staff_a,
		)
		app = correction.review_correction(
			correction_id=req2["correction_id"],
			decision="approve",
			review_note="Đồng ý hiệu chỉnh sau kiểm tra",
			idempotency_key=self.unique_key("rev-app"),
			actor=self.leader_a,
		)
		self.assertEqual(app["status"], "approved")
		self.assertEqual(frappe.db.get_value("CSKH Contact Attempt", att_id, "outcome_code"), "no_answer")
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "connected_attempt_count") or 0), 1)

		tl = read_model.list_attempts_timeline(wi, actor=self.staff_a)["items"][0]
		self.assertEqual(tl["original"]["outcome_code"], "no_answer")
		self.assertEqual(tl["effective"]["outcome_code"], "connected_interested")
		self.assertTrue(tl["has_approved_correction"])

		# 3. Void -> `attempt_count` drops to 0 and `Work Item.status` reverts to `assigned`
		void_res = correction.request_correction(
			attempt=att_id,
			action="void",
			reason="Ghi nhầm sang hồ sơ khách hàng khác",
			auto_approve=True,
			idempotency_key=self.unique_key("corr-void"),
			actor=self.leader_a,
		)
		self.assertEqual(void_res["status"], "approved")
		self.assertEqual(void_res["work_item_status"], "assigned")
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "attempt_count") or 0), 0)

		# Leader cannot `restore`; Admin restores -> `attempt_count` returns to 1 and `status` returns to `in_progress`
		with self.assertRaises(Forbidden):
			correction.request_correction(
				attempt=att_id,
				action="restore",
				reason="Leader thử restore",
				auto_approve=True,
				idempotency_key=self.unique_key("corr-leader-restore"),
				actor=self.leader_a,
			)

		restored = correction.request_correction(
			attempt=att_id,
			action="restore",
			reason="Admin khôi phục sau rà soát cuối kỳ",
			auto_approve=True,
			idempotency_key=self.unique_key("corr-admin-restore"),
			actor=self.admin_user,
		)
		self.assertEqual(restored["status"], "approved")
		self.assertEqual(restored["work_item_status"], "in_progress")
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "attempt_count") or 0), 1)
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "connected_attempt_count") or 0), 1)
		self.assertTrue(read_model.reconcile_p2_invariants(self.campaign)["ok"])

	def test_policy_disabling_leader_correction_review_enforced_fail_closed(self):
		"""When Campaign Policy sets `allow_leader_review_correction=False`, Leader cannot approve corrections."""
		policies.save_policy(
			policy_code=f"NO-LDR-CORR-{frappe.generate_hash(length=6)}",
			title="Chỉ Admin duyệt hiệu chỉnh",
			scope_type="Campaign",
			campaign=self.campaign,
			allow_leader_review_correction=False,
			idempotency_key=self.unique_key("pol-nolc"),
			actor=self.admin_user,
		)
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		rec = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key("att-nolc"),
			actor=self.staff_a,
		)
		req = correction.request_correction(
			attempt=rec["attempt_id"],
			action="void",
			reason="Nhầm hồ sơ",
			idempotency_key=self.unique_key("req-nolc"),
			actor=self.staff_a,
		)
		with self.assertRaises(Forbidden):
			correction.review_correction(
				correction_id=req["correction_id"],
				decision="approve",
				review_note="Leader cố duyệt",
				idempotency_key=self.unique_key("rev-nolc-ldr"),
				actor=self.leader_a,
			)
		ok = correction.review_correction(
			correction_id=req["correction_id"],
			decision="approve",
			review_note="Admin duyệt hợp lệ",
			idempotency_key=self.unique_key("rev-nolc-adm"),
			actor=self.admin_user,
		)
		self.assertEqual(ok["status"], "approved")
