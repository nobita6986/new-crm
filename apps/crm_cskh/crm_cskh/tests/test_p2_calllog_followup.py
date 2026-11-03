"""P2 Call Log Reuse, Follow-up/Callback Invariants, Side-Artifact Isolation & Timezone Buckets (`T2-06`, `T2-07`, `T2-12`, `T2-16`, `T2-17`, `T2-21`)."""

from __future__ import annotations

from unittest import mock

import frappe
from frappe.utils import get_datetime

from crm_cskh.services import call_log_adapter, contact_attempt, followup, read_model
from crm_cskh.services.errors import (
	AlreadyLinked,
	InvalidFollowupTime,
	NoteRequired,
	OutcomeNotCompatible,
)
from crm_cskh.tests.p2_base import P2TestCase


class TestP2CallLogReuseAndSideArtifactIsolation(P2TestCase):
	"""`T2-12`, `T2-16`, `T2-17`: Truthful `CRM Call Log` linking/creation and zero inflation from Notes/Tasks/CallLogs."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=100)

	def test_link_existing_call_log_create_manual_call_log_and_block_duplicate_linking(self):
		"""`T2-16`: 1:1 `CRM Call Log` linking, manual `call_log_data`, foreign Lead block, and `409 ALREADY_LINKED`."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		cl1 = self.make_native_call_log(lead, status="Completed", duration=65)

		unlinked = call_log_adapter.list_unlinked_call_logs(wi, actor=self.staff_a)
		self.assertIn(cl1, [x["name"] for x in unlinked["items"]])

		# Cannot attach `call_log` to non-phone channel
		with self.assertRaises(OutcomeNotCompatible):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="zalo",
				outcome_code="connected_interested",
				call_log=cl1,
				note="Kênh Zalo gắn nhầm Call Log",
				idempotency_key=self.unique_key("cl-zalo"),
				actor=self.staff_a,
			)

		# Link manual `cl1` on Attempt #1 -> `verification_status == 'self_reported'` (Staff cannot spoof `verified` via Manual log)
		r1 = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=cl1,
			note="Cuộc gọi trao đổi 65 giây",
			idempotency_key=self.unique_key("cl-link-1"),
			actor=self.staff_a,
		)
		self.assertEqual(r1["call_log"], cl1)
		self.assertEqual(
			frappe.db.get_value("CSKH Contact Attempt", r1["attempt_id"], "verification_status"),
			"self_reported",
		)

		# `cl1` disappears from unlinked list and a second attempt claiming `cl1` fails `409 ALREADY_LINKED`
		unlinked_after = call_log_adapter.list_unlinked_call_logs(wi, actor=self.staff_a)
		self.assertNotIn(cl1, [x["name"] for x in unlinked_after["items"]])

		with self.assertRaises(AlreadyLinked):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="connected_needs_followup",
				call_log=cl1,
				note="Thử gắn trùng Call Log",
				followup={"due_at": self.future_due_str(hours=2)},
				idempotency_key=self.unique_key("cl-dup"),
				actor=self.staff_a,
			)

		# Foreign Lead's Call Log is rejected
		other_lead = self.make_canonical_lead()
		foreign_cl = self.make_native_call_log(other_lead)
		with self.assertRaises(OutcomeNotCompatible):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="no_answer",
				call_log=foreign_cl,
				idempotency_key=self.unique_key("cl-foreign"),
				actor=self.staff_a,
			)

		# Create truthful `CRM Call Log` inline via `call_log_data` (requires truthful `from` & `to`)
		lead_phone = frappe.db.get_value("CRM Lead", lead, "mobile_no")
		r2 = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="busy",
			call_log_data={
				"from": "+842873001101",
				"to": lead_phone,
				"type": "Outgoing",
				"status": "Busy",
				"duration": 0,
				"note": "Máy bận",
			},
			idempotency_key=self.unique_key("cl-manual"),
			actor=self.staff_a,
		)
		self.assertTrue(r2["call_log"])
		self.assertEqual(frappe.db.get_value("CRM Call Log", r2["call_log"], "reference_docname"), lead)

	def test_fcrm_note_general_crm_task_and_standalone_call_log_never_inflate_counters(self):
		"""`T2-12`, `T2-17`: Creating `FCRM Note`, general `CRM Task`, or unlinked `CRM Call Log` keeps `attempt_count=0`."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)

		frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "Ghi chú nghiên cứu nội bộ",
				"content": "Xem lịch sử mua hàng trước khi gọi",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		).insert(ignore_permissions=True)

		general_task = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "Chuẩn bị bảng báo giá PDF",
				"status": "Done",
				"priority": "Low",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
				"cskh_is_followup": 0,
			}
		).insert(ignore_permissions=True)
		self.assertTrue(general_task.name)

		self.make_native_call_log(lead, status="No Answer", duration=0)

		detail = read_model.get_work_item_detail(wi, actor=self.staff_a)["work_item"]
		self.assertEqual(detail["status"], "assigned")
		self.assertEqual(detail["attempt_count"], 0)
		self.assertEqual(detail["connected_attempt_count"], 0)


class TestP2FollowupInvariantsAndTimezoneBuckets(P2TestCase):
	"""`T2-06`, `T2-07`, `T2-21`: Single open callback, reschedule audit, no-empty-Done rule, and VN timezone buckets."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=100)

	def test_single_open_callback_reschedule_and_block_empty_completion_or_direct_bypass(self):
		"""`T2-06`, `T2-07`: Rescheduling updates the same `CRM Task`; empty Done and direct ORM bypass are rejected."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		# Past `due_at` rejected
		with self.assertRaises(InvalidFollowupTime):
			followup.schedule_followup(
				work_item=wi,
				due_at="2020-01-01 09:00:00",
				title="Giờ quá khứ",
				idempotency_key=self.unique_key("fu-past"),
				actor=self.staff_a,
			)

		# Schedule first callback -> creates 1 open `CRM Task`
		fu1 = followup.schedule_followup(
			work_item=wi,
			due_at=self.future_due_str(hours=2),
			title="Hẹn gọi lại lần 1",
			note="Khách đang họp",
			idempotency_key=self.unique_key("fu-create"),
			actor=self.staff_a,
		)
		task_id = fu1["task_id"]
		self.assertEqual(followup.count_open_followup_tasks(wi), 1)

		# Reschedule without `reason` -> `NoteRequired`
		with self.assertRaises(NoteRequired):
			followup.schedule_followup(
				work_item=wi,
				due_at=self.future_due_str(hours=5),
				reason="",
				idempotency_key=self.unique_key("fu-resched-noreason"),
				actor=self.staff_a,
			)

		# Reschedule with reason -> updates the SAME `task_id` (`reschedule_count == 1`), still 1 open task
		fu2 = followup.schedule_followup(
			work_item=wi,
			due_at=self.future_due_str(hours=6),
			reason="Khách nhắn dời sang cuối chiều",
			idempotency_key=self.unique_key("fu-resched-ok"),
			actor=self.staff_a,
		)
		self.assertEqual(fu2["status"], "rescheduled")
		self.assertEqual(fu2["task_id"], task_id)
		self.assertEqual(fu2["reschedule_count"], 1)
		self.assertEqual(followup.count_open_followup_tasks(wi), 1)

		# `T2-07`: `complete_followup(action='complete')` without `attempt_id` MUST fail!
		with self.assertRaises(OutcomeNotCompatible):
			followup.complete_followup(
				work_item=wi,
				task_id=task_id,
				action="complete",
				attempt_id=None,
				idempotency_key=self.unique_key("fu-empty-done"),
				actor=self.staff_a,
			)

		# `SEC2-05`: Direct ORM save or delete on CSKH callback `CRM Task` outside service is blocked
		task_doc = frappe.get_doc("CRM Task", task_id)
		task_doc.status = "Done"
		with self.assertRaises(frappe.PermissionError):
			task_doc.save(ignore_permissions=True)

		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("CRM Task", task_id, force=True, ignore_permissions=True)

		# Cancel without `completion_reason` -> `NoteRequired`; with reason -> `Canceled`, Work Item returns to `assigned`
		with self.assertRaises(NoteRequired):
			followup.complete_followup(
				work_item=wi,
				task_id=task_id,
				action="cancel",
				completion_reason="  ",
				idempotency_key=self.unique_key("fu-cancel-empty"),
				actor=self.staff_a,
			)

		canceled = followup.complete_followup(
			work_item=wi,
			task_id=task_id,
			action="cancel",
			completion_reason="Khách đã chủ động đến quầy giao dịch trực tiếp",
			idempotency_key=self.unique_key("fu-cancel-ok"),
			actor=self.staff_a,
		)
		self.assertEqual(canceled["status"], "canceled")
		self.assertEqual(canceled["work_item_status"], "assigned")
		self.assertEqual(followup.count_open_followup_tasks(wi), 0)

	def test_timezone_consistent_overdue_today_upcoming_buckets_at_day_boundaries(self):
		"""`T2-21`: `overdue` / `today` / `upcoming` buckets classify `09:59`, `23:59`, and `tomorrow 00:01` accurately."""
		wi_over, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		wi_today, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		wi_up, _ = self.make_assigned_work_item(self.campaign, self.staff_a)

		anchor_create = get_datetime("2026-11-10 08:00:00")
		with mock.patch("crm_cskh.services.followup.now_datetime", return_value=anchor_create):
			t_over = followup.schedule_followup(
				work_item=wi_over,
				due_at="2026-11-10 09:59:00",
				title="Quá hạn lúc 10h",
				idempotency_key=self.unique_key("tz-over"),
				actor=self.staff_a,
			)["task_id"]
			t_today = followup.schedule_followup(
				work_item=wi_today,
				due_at="2026-11-10 23:59:00",
				title="Cuối ngày hôm nay",
				idempotency_key=self.unique_key("tz-today"),
				actor=self.staff_a,
			)["task_id"]
			t_up = followup.schedule_followup(
				work_item=wi_up,
				due_at="2026-11-11 00:01:00",
				title="Đầu ngày mai",
				idempotency_key=self.unique_key("tz-up"),
				actor=self.staff_a,
			)["task_id"]

		# Query at `2026-11-10 10:00:00` (Asia/Ho_Chi_Minh local wall-clock)
		anchor_query = get_datetime("2026-11-10 10:00:00")
		with (
			mock.patch("crm_cskh.services.followup.now_datetime", return_value=anchor_query),
			mock.patch("crm_cskh.services.read_model.now_datetime", return_value=anchor_query),
		):
			over_res = followup.list_followups(campaign=self.campaign, bucket="overdue", actor=self.staff_a)
			today_res = followup.list_followups(campaign=self.campaign, bucket="today", actor=self.staff_a)
			up_res = followup.list_followups(campaign=self.campaign, bucket="upcoming", actor=self.staff_a)
			summary = read_model.progress_summary(campaign=self.campaign, actor=self.leader_a)

		self.assertEqual([x["task_id"] for x in over_res["items"]], [t_over])
		self.assertTrue(over_res["items"][0]["is_overdue"])
		self.assertEqual({x["task_id"] for x in today_res["items"]}, {t_over, t_today})
		self.assertEqual([x["task_id"] for x in up_res["items"]], [t_up])
		self.assertEqual(summary["cards"]["open_followups"], 3)
		self.assertEqual(summary["cards"]["overdue_followups"], 1)
		self.assertEqual(summary["cards"]["due_today_followups"], 2)
