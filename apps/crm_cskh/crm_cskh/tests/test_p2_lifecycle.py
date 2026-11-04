"""P2 Lifecycle, State Machine, Close/Reopen, DNC & P1 Compatibility (`T2-01`, `T2-05`, `T2-13`, `T2-14`, `T2-15`, `T2-23`)."""

from __future__ import annotations

import frappe

from crm_cskh.api import customers as customers_api
from crm_cskh.services import contact_attempt, distribution, followup, ownership, policies, reporting
from crm_cskh.services.errors import (
	Conflict,
	DoNotContact,
	Forbidden,
	NoteRequired,
	OutcomeNotCompatible,
	WorkItemNotActive,
)
from crm_cskh.services.read_model import get_work_item_detail, reconcile_p2_invariants
from crm_cskh.tests.p2_base import P2TestCase


class TestP2WorkItemLifecycle(P2TestCase):
	"""End-to-end state transitions and isolation from `CRM Lead.status` (`T2-01`, `T2-13`)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=60, capacity=5)
		self.make_rule(self.campaign, self.staff_b, weight=40, capacity=5)

	def test_full_state_lifecycle_assigned_in_progress_on_hold_closed(self):
		"""`T2-01`: `assigned -> in_progress -> on_hold -> in_progress -> closed` with single Active Claim."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi, "status"), "assigned")
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 1)

		# 1. `assigned -> in_progress` via non-connected contact attempt
		res1 = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi lần 1 chuông đổ không nghe",
			idempotency_key=self.unique_key("att1"),
			actor=self.staff_a,
		)
		self.assertEqual(res1["work_item_status"], "in_progress")
		self.assertEqual(res1["attempt_count"], 1)
		self.assertEqual(res1["connected_attempt_count"], 0)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_owner"), self.staff_a)

		# 2. `in_progress -> on_hold` via `schedule_followup`
		fu = followup.schedule_followup(
			work_item=wi,
			due_at=self.future_due_str(hours=3),
			title="Gọi lại buổi chiều",
			note="Khách có thể rảnh sau 15h",
			idempotency_key=self.unique_key("fu1"),
			actor=self.staff_a,
		)
		self.assertEqual(fu["work_item_status"], "on_hold")
		self.assertTrue(fu["task_id"])
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 1)

		# 3. `on_hold -> in_progress` via subsequent attempt resolving open callback
		res2 = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Đã trao đổi thành công, khách quan tâm gói tiêu chuẩn",
			complete_open_followup=True,
			idempotency_key=self.unique_key("att2"),
			actor=self.staff_a,
		)
		self.assertEqual(res2["work_item_status"], "in_progress")
		self.assertEqual(res2["attempt_count"], 2)
		self.assertEqual(res2["connected_attempt_count"], 1)
		self.assertEqual(res2["resolved_callback_task"], fu["task_id"])
		self.assertEqual(frappe.db.get_value("CRM Task", fu["task_id"], "status"), "Done")

		# 4. `in_progress -> closed` via `close_work_item`
		closed = contact_attempt.close_work_item(
			work_item=wi,
			completion_reason="Hoàn tất tư vấn và bàn giao bộ phận chốt đơn",
			idempotency_key=self.unique_key("close1"),
			actor=self.staff_a,
		)
		self.assertEqual(closed["work_item_status"], "closed")
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 0)
		self.assertIsNone(frappe.db.get_value("CRM Lead", lead, "lead_owner"))
		self.assertEqual(
			frappe.db.count("ToDo", {"reference_type": "CRM Lead", "reference_name": lead, "status": "Open"}),
			0,
		)
		self.assertTrue(reconcile_p2_invariants(self.campaign)["ok"])

	def test_work_item_status_and_crm_lead_status_remain_isolated(self):
		"""`T2-13`: P2 `Work Item` workflow never mutates `CRM Lead.status`, and vice versa."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), "New")

		contact_attempt.record_attempt(
			work_item=wi,
			channel="zalo",
			outcome_code="connected_interested",
			note="Khách quan tâm trên Zalo",
			idempotency_key=self.unique_key("iso1"),
			actor=self.staff_a,
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), "New")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi, "status"), "in_progress")

		# Even `converted` + `close_work_item=True` closes the CSKH Work Item without mutating `CRM Lead.status`
		contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="converted",
			note="Đạt mục tiêu chiến dịch CSKH",
			close_work_item=True,
			completion_reason="Khách đồng ý tham gia chương trình",
			idempotency_key=self.unique_key("iso2"),
			actor=self.staff_a,
		)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi, "status"), "closed")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), "New")

	def test_reassign_preserves_in_progress_and_on_hold_and_interval_attribution(self):
		"""`T2-14`: `ownership.reassign(A -> B)` keeps `in_progress`/`on_hold` and `performed_by = A`."""
		wi, _lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		att_a = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="callback_requested",
			note="Khách hẹn gọi lại ngày mai",
			followup={"due_at": self.future_due_str(hours=24), "title": "Gọi lại theo hẹn"},
			idempotency_key=self.unique_key("att-a"),
			actor=self.staff_a,
		)
		self.assertEqual(att_a["work_item_status"], "on_hold")
		task_id = att_a["callback_task"]
		due_before = frappe.db.get_value("CRM Task", task_id, "due_date")

		reassigned = ownership.reassign(
			wi,
			self.staff_a,
			self.staff_b,
			reason="Điều phối ca trực",
			request_id=self.unique_key("reassign-ab"),
			actor=self.admin_user,
		)
		self.assertEqual(reassigned["status"], "reassigned")
		self.assertEqual(reassigned["work_item_status"], "on_hold")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi, "assigned_to"), self.staff_b)
		self.assertEqual(frappe.db.get_value("CRM Task", task_id, "assigned_to"), self.staff_b)
		self.assertEqual(frappe.db.get_value("CRM Task", task_id, "due_date"), due_before)
		self.assertEqual(
			frappe.db.get_value("CSKH Contact Attempt", att_a["attempt_id"], "performed_by"),
			self.staff_a,
		)

		# History intervals: Staff A's interval is closed, Staff B's interval is open
		open_intervals = frappe.get_all(
			"CSKH Assignment History",
			filters={"work_item": wi, "ownership_start": ["is", "set"], "ownership_end": ["is", "not set"]},
			fields=["to_user"],
		)
		self.assertEqual([r.to_user for r in open_intervals], [self.staff_b])

	def test_revoke_from_on_hold_cancels_callback_and_handles_return_to_pool(self):
		"""`T2-15`: `revoke(return_to_pool=False/True)` cancels open callback and clears ownership."""
		wi1, lead1 = self.make_assigned_work_item(self.campaign, self.staff_a)
		att1 = contact_attempt.record_attempt(
			work_item=wi1,
			channel="phone_call",
			outcome_code="callback_requested",
			note="Khách xin gọi lại tối",
			followup={"due_at": self.future_due_str(hours=5)},
			idempotency_key=self.unique_key("rev1-att"),
			actor=self.staff_a,
		)
		task1 = att1["callback_task"]

		ownership.revoke(
			wi1,
			self.staff_a,
			reason="Thu hồi ngừng chiến dịch cá nhân",
			return_to_pool=False,
			request_id=self.unique_key("rev1"),
			actor=self.admin_user,
		)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi1, "status"), "revoked")
		self.assertEqual(frappe.db.get_value("CRM Task", task1, "status"), "Canceled")
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead1}), 0)
		self.assertIsNone(frappe.db.get_value("CRM Lead", lead1, "lead_owner"))

		# `return_to_pool=True` keeps Active Claim and sets status='queued'
		wi2, lead2 = self.make_assigned_work_item(self.campaign, self.staff_a)
		att2 = contact_attempt.record_attempt(
			work_item=wi2,
			channel="phone_call",
			outcome_code="callback_requested",
			note="Hẹn lại sáng mai",
			followup={"due_at": self.future_due_str(hours=12)},
			idempotency_key=self.unique_key("rev2-att"),
			actor=self.staff_a,
		)
		ownership.revoke(
			wi2,
			self.staff_a,
			reason="Trả về hàng đợi phân phối lại",
			return_to_pool=True,
			request_id=self.unique_key("rev2"),
			actor=self.admin_user,
		)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi2, "status"), "queued")
		self.assertEqual(frappe.db.get_value("CRM Task", att2["callback_task"], "status"), "Canceled")
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead2}), 0)
		self.assertTrue(reconcile_p2_invariants(self.campaign)["ok"])


class TestP2CloseReopenDncAndCompatibility(P2TestCase):
	"""Close rules, audited reopen, DNC persistence, and P1 API/capacity compatibility (`T2-05`, `T2-23`)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=50, capacity=2)
		self.make_rule(self.campaign, self.staff_b, weight=50, capacity=2)

	def test_close_blocks_zero_attempts_by_default_but_allows_invalid_duplicate_with_reason(self):
		"""`T2-23`: `require_attempt_before_close=True` blocks normal close at 0 attempts."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		with self.assertRaises(OutcomeNotCompatible):
			contact_attempt.close_work_item(
				work_item=wi,
				completion_reason="Muốn đóng khi chưa gọi",
				close_category="normal",
				idempotency_key=self.unique_key("close-fail"),
				actor=self.staff_a,
			)

		with self.assertRaises(NoteRequired):
			contact_attempt.close_work_item(
				work_item=wi,
				completion_reason="",
				close_category="invalid_or_duplicate",
				idempotency_key=self.unique_key("close-noreason"),
				actor=self.staff_a,
			)

		ok = contact_attempt.close_work_item(
			work_item=wi,
			completion_reason="Số điện thoại rác / dữ liệu trùng xác nhận",
			close_category="invalid_or_duplicate",
			idempotency_key=self.unique_key("close-inv"),
			actor=self.staff_a,
		)
		self.assertEqual(ok["work_item_status"], "closed")

	def test_reopen_audit_permissions_and_active_claim_collision_guard(self):
		"""`T2-23`: Staff cannot reopen; Leader reopens with reason; collision with new campaign raises `ALREADY_ASSIGNED`."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="connected_not_interested",
			note="Khách báo chưa có nhu cầu hiện tại",
			close_work_item=True,
			completion_reason="Khách từ chối lịch sự",
			idempotency_key=self.unique_key("att-close"),
			actor=self.staff_a,
		)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi, "status"), "closed")

		# Staff cannot reopen
		with self.assertRaises(Forbidden):
			contact_attempt.reopen_work_item(
				work_item=wi,
				target_user=self.staff_a,
				reason="Nhân viên tự mở lại",
				idempotency_key=self.unique_key("reopen-staff"),
				actor=self.staff_a,
			)

		# Leader reopen requires audit reason
		with self.assertRaises(NoteRequired):
			contact_attempt.reopen_work_item(
				work_item=wi,
				target_user=self.staff_a,
				reason="   ",
				idempotency_key=self.unique_key("reopen-empty"),
				actor=self.leader_a,
			)

		# Leader A reopens to Staff B -> restores Active Claim, status='in_progress' (attempt_count=1 > 0)
		reopened = contact_attempt.reopen_work_item(
			work_item=wi,
			target_user=self.staff_b,
			reason="Khách gọi lại hotline hỏi thêm ưu đãi",
			idempotency_key=self.unique_key("reopen-ok"),
			actor=self.leader_a,
		)
		self.assertEqual(reopened["work_item_status"], "in_progress")
		self.assertEqual(reopened["assigned_to"], self.staff_b)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_owner"), self.staff_b)

		# Close again, then assign Lead in Campaign 2 -> reopening old Work Item must fail `ALREADY_ASSIGNED`
		contact_attempt.close_work_item(
			work_item=wi,
			completion_reason="Đóng lần 2",
			idempotency_key=self.unique_key("close-2"),
			actor=self.staff_b,
		)
		camp2 = self.make_campaign(team_scope=self.team_b)
		self.make_assigned_work_item(camp2, self.staff_c, lead=lead)

		with self.assertRaises(Conflict) as ctx:
			contact_attempt.reopen_work_item(
				work_item=wi,
				target_user=self.staff_b,
				reason="Thử mở lại khi khách đã sang chiến dịch 2",
				idempotency_key=self.unique_key("reopen-collision"),
				actor=self.admin_user,
			)
		self.assertEqual(ctx.exception.code, "ALREADY_ASSIGNED")

	def test_dnc_persists_on_lead_blocks_future_campaigns_and_requires_admin_override(self):
		"""`T2-05`: `do_not_contact` marks `CRM Lead.cskh_do_not_contact=1`, auto-closes, and blocks new campaigns."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		res = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="do_not_contact",
			note="Khách yêu cầu xóa khỏi danh sách gọi điện",
			completion_reason="Khách yêu cầu ngừng liên hệ vĩnh viễn",
			idempotency_key=self.unique_key("dnc-1"),
			actor=self.staff_a,
		)
		self.assertTrue(res["do_not_contact"])
		self.assertEqual(res["work_item_status"], "closed")
		self.assertEqual(int(frappe.db.get_value("CRM Lead", lead, "cskh_do_not_contact") or 0), 1)

		# Leader cannot reopen DNC Lead
		with self.assertRaises(DoNotContact):
			contact_attempt.reopen_work_item(
				work_item=wi,
				target_user=self.staff_a,
				reason="Leader thử mở lại DNC",
				idempotency_key=self.unique_key("dnc-leader-reopen"),
				actor=self.leader_a,
			)

		# Campaign 2 preview/execute skips DNC Lead (`do_not_contact`), and direct `ownership.assign` rejects DNC Lead
		normal_lead = self.make_canonical_lead()
		camp2 = self.make_campaign(team_scope=self.team_b, selection={"leads": [lead, normal_lead]})
		self.make_rule(camp2, self.staff_c, weight=100)
		preview = distribution.preview(camp2, actor=self.admin_user)
		self.assertEqual(preview["summary"]["eligible_count"], 1)
		self.assertEqual(preview["summary"]["excluded_count"], 1)

		wi_c2 = self.make_queued_work_item(camp2, lead)
		with self.assertRaises(DoNotContact) as assign_err:
			ownership.assign(
				wi_c2,
				self.staff_c,
				reason="Thử giao khách DNC",
				request_id=self.unique_key("dnc-assign"),
				actor=self.admin_user,
			)
		self.assertEqual(assign_err.exception.code, "DO_NOT_CONTACT")

		# Remove queued wi_c2 claim, then Admin explicitly clears DNC via `set_lead_dnc` with audit reason
		frappe.db.delete("CSKH Active Claim", {"work_item": wi_c2})
		frappe.delete_doc("CSKH Work Item", wi_c2, force=True, ignore_permissions=True)

		cleared = policies.set_lead_dnc(
			lead=lead,
			do_not_contact=False,
			reason="Khách gửi phiếu đăng ký lại có xác nhận",
			idempotency_key=self.unique_key("dnc-clear"),
			actor=self.admin_user,
		)
		self.assertFalse(cleared["do_not_contact"])
		reopened = contact_attempt.reopen_work_item(
			work_item=wi,
			target_user=self.staff_a,
			reason="Mở lại sau khi Admin gỡ DNC hợp lệ",
			idempotency_key=self.unique_key("dnc-reopened"),
			actor=self.leader_a,
		)
		self.assertEqual(reopened["work_item_status"], "in_progress")

	def test_p1_capacity_distribution_reporting_and_my_list_recognize_in_progress_and_on_hold(self):
		"""P1 audit proof: `capacity_limit=2` counts `in_progress` + `on_hold`, and `customers.my_list` returns P2 fields."""
		# Staff A has capacity=2 in `self.campaign`; move 1 item to `in_progress` and 1 item to `on_hold`
		wi1, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		wi2, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		contact_attempt.record_attempt(
			work_item=wi1,
			channel="phone_call",
			outcome_code="no_answer",
			idempotency_key=self.unique_key("cap-1"),
			actor=self.staff_a,
		)
		followup.schedule_followup(
			work_item=wi2,
			due_at=self.future_due_str(hours=4),
			title="Hẹn gọi lại",
			idempotency_key=self.unique_key("cap-2"),
			actor=self.staff_a,
		)

		# Now Staff A has 0 `assigned`, 1 `in_progress`, 1 `on_hold` -> total active = 2 == capacity_limit!
		lead3 = self.make_canonical_lead()
		wi3 = self.make_queued_work_item(self.campaign, lead3)
		with self.assertRaises(Conflict) as cap_ctx:
			ownership.assign(
				wi3,
				self.staff_a,
				reason="Vượt trần capacity 2",
				request_id=self.unique_key("cap-over"),
				actor=self.admin_user,
			)
		self.assertEqual(cap_ctx.exception.code, "CAPACITY_EXCEEDED")

		# `customers.my_list` for Staff A still returns both items + P2 summary fields
		frappe.set_user(self.staff_a)
		my_rows = customers_api.my_list(campaign=self.campaign)
		frappe.set_user("Administrator")
		self.assertEqual(my_rows["total"], 2)
		by_wi = {r["work_item"]: r for r in my_rows["items"]}
		self.assertEqual(by_wi[wi1]["work_item_status"], "in_progress")
		self.assertEqual(by_wi[wi1]["attempt_count"], 1)
		self.assertEqual(by_wi[wi2]["work_item_status"], "on_hold")
		self.assertIsNotNone(by_wi[wi2]["open_callback_task"])

		# `reporting.distribution_dashboard`, `reporting.campaign_summary` and `get_work_item_detail` agree on active states
		dash = reporting.distribution_dashboard(user=self.leader_a, campaign=self.campaign)
		self.assertEqual(dash["cards"]["in_progress_work_items"], 1)
		self.assertEqual(dash["cards"]["on_hold_work_items"], 1)
		self.assertEqual(dash["cards"]["assigned_work_items"], 2)
		camp_sum = reporting.campaign_summary(user=self.leader_a)
		camp_entry = next(c for c in camp_sum["campaigns"] if c["campaign"] == self.campaign)
		self.assertEqual(camp_entry["assigned"], 2)
		detail = get_work_item_detail(wi2, actor=self.staff_a)
		self.assertEqual(detail["work_item"]["status"], "on_hold")
