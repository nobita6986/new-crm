"""Site regressions for the daily care overview, outside frozen P1/P2 inventories."""

import json
from datetime import datetime
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.api import ownership as ownership_api
from crm_cskh.api import worker_overview as overview_api
from crm_cskh.api import workflow as workflow_api
from crm_cskh.services import runtime_cache, worker_overview
from crm_cskh.services.errors import Forbidden
from crm_cskh.tests.p2_base import P2TestCase


class TestWorkerCareOverview(P2TestCase):
	def test_current_staff_and_team_counts_cannot_include_foreign_assignments(self):
		with mock.patch.object(worker_overview, "is_p3_sla_enabled", return_value=False):
			before_own = worker_overview.get_overview(self.staff_a)["workload"]["active"]
			before_team = worker_overview.get_overview(self.leader_a)["workload"]["active"]
			campaign_a = self.make_campaign(team_scope=self.team_a)
			campaign_b = self.make_campaign(team_scope=self.team_b)
			self.make_assigned_work_item(campaign_a, self.staff_a)
			self.make_assigned_work_item(campaign_a, self.staff_b)
			self.make_assigned_work_item(campaign_b, self.staff_c)
			self.assertEqual(worker_overview.get_overview(self.staff_a)["workload"]["active"], before_own + 1)
			self.assertEqual(
				worker_overview.get_overview(self.leader_a)["workload"]["active"], before_team + 2
			)

	def test_bod_only_receives_aggregates_without_contact_or_assignment_identifiers(self):
		campaign = self.make_campaign(team_scope=self.team_a)
		work_item, lead = self.make_assigned_work_item(campaign, self.staff_a)
		phone = frappe.db.get_value("CRM Lead", lead, "mobile_no")
		frappe.set_user(self.bod_user)
		with mock.patch.object(worker_overview, "is_p3_sla_enabled", return_value=False):
			payload = overview_api.get_overview()
		self.assertEqual(payload["scope"], "all")
		self.assertIsNone(payload["sla"])
		serialized = json.dumps(payload)
		for value in (work_item, lead, phone, self.staff_a, campaign):
			self.assertNotIn(value, serialized)
		self.assertEqual(payload["definitions"]["coverage_denominator"], payload["workload"]["active"])
		self.assertEqual(payload["definitions"]["contact_records_unit"], "distinct_active_worker_leads")
		self.assertEqual(
			set(payload["bottlenecks"].keys()),
			{"on_hold", "active_assignees", "assignees_with_unattempted", "assignees_with_overdue"},
		)
		for forbidden_sales_or_hiring_key in (
			"revenue",
			"deal_value",
			"forecast",
			"hired",
			"placed",
			"d7",
			"d30",
			"converted",
		):
			self.assertNotIn(forbidden_sales_or_hiring_key, payload)
			self.assertNotIn(forbidden_sales_or_hiring_key, payload["workload"])

	def test_guest_is_rejected_and_api_does_not_accept_scope_override(self):
		frappe.set_user("Guest")
		with self.assertRaises(Forbidden):
			overview_api.get_overview()
		with self.assertRaises(TypeError):
			overview_api.get_overview(actor=self.staff_a)

	def test_full_dataset_counts_exceed_pagination_and_separate_distinct_workers_from_work_items(self):
		"""Verify >55 items are never truncated at 20/50 and multi-campaign leads count once in contact_records."""
		with mock.patch.object(worker_overview, "is_p3_sla_enabled", return_value=False):
			base = worker_overview.get_overview(self.staff_a)["workload"]
			camp_1 = self.make_campaign(team_scope=self.team_a)
			camp_2 = self.make_campaign(team_scope=self.team_a)

			leads = []
			for _ in range(55):
				_, lead_id = self.make_assigned_work_item(camp_1, self.staff_a)
				leads.append(lead_id)

			# Second active work item for 4 of the SAME worker leads in a second campaign
			self.make_rule(camp_2, self.staff_a, weight=50, team=self.team_a)
			for shared_lead in leads[:4]:
				wi_dup = frappe.get_doc(
					{
						"doctype": "CSKH Work Item",
						"campaign": camp_2,
						"lead": shared_lead,
						"status": "assigned",
						"assigned_to": self.staff_a,
						"assigned_at": now_datetime(),
					}
				).insert(ignore_permissions=True)
				self.assertTrue(wi_dup.name)

			after = worker_overview.get_overview(self.staff_a)["workload"]
			self.assertEqual(after["active"] - base["active"], 59)
			self.assertEqual(after["contact_records"] - base["contact_records"], 55)
			self.assertGreater(after["active"], after["contact_records"])

	def test_reassign_dnc_completed_callbacks_converted_lead_and_timezone_boundaries(self):
		"""Reconcile ownership transfer, cross-campaign DNC, Done/Cancelled callbacks, converted flag & TZ."""
		with mock.patch.object(worker_overview, "is_p3_sla_enabled", return_value=False):
			camp_1 = self.make_campaign(team_scope=self.team_a)
			camp_2 = self.make_campaign(team_scope=self.team_a)
			self.make_rule(camp_1, self.staff_b, weight=50, team=self.team_a)

			before_a = worker_overview.get_overview(self.staff_a)
			before_b = worker_overview.get_overview(self.staff_b)
			before_leader = worker_overview.get_overview(self.leader_a)

			wi_1, lead_1 = self.make_assigned_work_item(camp_1, self.staff_a)
			_wi_2, lead_2 = self.make_assigned_work_item(camp_1, self.staff_a)

			# Mark lead_1 converted=1 in CRM Lead: must never create hiring/placement counts
			if frappe.db.has_column("CRM Lead", "converted"):
				frappe.db.set_value("CRM Lead", lead_1, "converted", 1, update_modified=False)

			# Add second work item on lead_2 in camp_2 and mark lead_2 DNC -> DNC increments by 1 (not 2)
			frappe.get_doc(
				{
					"doctype": "CSKH Work Item",
					"campaign": camp_2,
					"lead": lead_2,
					"status": "assigned",
					"assigned_to": self.staff_a,
					"assigned_at": now_datetime(),
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value(
				"CRM Lead",
				lead_2,
				{"cskh_do_not_contact": 1, "cskh_dnc_reason": "Người lao động yêu cầu ngừng liên hệ"},
				update_modified=False,
			)
			dnc_after = worker_overview.get_overview(self.staff_a)["do_not_contact"]
			self.assertEqual(dnc_after - (before_a["do_not_contact"] or 0), 1)

			# Record attempt with open callback on wi_1, then verify Done/Cancelled tasks are excluded
			due_future = str(add_to_date(now_datetime(), hours=2))
			frappe.set_user(self.staff_a)
			att = workflow_api.record_attempt(
				work_item=wi_1,
				channel="phone_call",
				outcome_code="no_answer",
				note="Chưa bắt máy lần 1",
				followup_due_at=due_future,
				followup_title="Hẹn gọi lại",
				idempotency_key=self.unique_key("ov-att"),
			)
			task_id = att["callback_task"]
			self.assertTrue(task_id)

			mid_a = worker_overview.get_overview(self.staff_a)
			self.assertEqual(mid_a["callbacks"]["open"] - before_a["callbacks"]["open"], 1)

			# Mark callback Done -> excluded from open/today/overdue
			frappe.set_user("Administrator")
			frappe.db.set_value("CRM Task", task_id, "status", "Done", update_modified=False)
			done_a = worker_overview.get_overview(self.staff_a)
			self.assertEqual(done_a["callbacks"]["open"], before_a["callbacks"]["open"])

			# Restore open callback at 2026-11-26 23:30:00 Asia/Ho_Chi_Minh (= 2026-11-27 01:30 Asia/Tokyo)
			frappe.db.set_value(
				"CRM Task",
				task_id,
				{"status": "Todo", "due_date": "2026-11-26 23:30:00"},
				update_modified=False,
			)
			fixed_now = datetime(2026, 11, 26, 10, 0, 0)
			with (
				mock.patch.object(worker_overview, "now_datetime", return_value=fixed_now),
				mock.patch.object(
					runtime_cache, "get_cached_system_timezone", return_value="Asia/Ho_Chi_Minh"
				),
			):
				frappe.db.set_value(
					"User", self.staff_a, "time_zone", "Asia/Ho_Chi_Minh", update_modified=False
				)
				runtime_cache.clear_all_local_caches()
				ict_ov = worker_overview.get_overview(self.staff_a)

				frappe.db.set_value("User", self.staff_a, "time_zone", "Asia/Tokyo", update_modified=False)
				runtime_cache.clear_all_local_caches()
				tokyo_ov = worker_overview.get_overview(self.staff_a)

			frappe.db.set_value("User", self.staff_a, "time_zone", "", update_modified=False)
			runtime_cache.clear_all_local_caches()
			self.assertEqual(ict_ov["callbacks"]["today"] - tokyo_ov["callbacks"]["today"], 1)

			# Reassign wi_1 from staff_a to staff_b -> staff_a drops by 1, staff_b rises by 1, leader_a unchanged
			frappe.set_user(self.admin_user)
			ownership_api.reassign(
				work_item=wi_1,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				reason="Điều phối tải nhóm A",
				request_id=self.unique_key("ov-reassign"),
			)
			post_a = worker_overview.get_overview(self.staff_a)
			post_b = worker_overview.get_overview(self.staff_b)
			post_leader = worker_overview.get_overview(self.leader_a)
			self.assertEqual(post_a["workload"]["active"] - before_a["workload"]["active"], 2)
			self.assertEqual(post_b["workload"]["active"] - before_b["workload"]["active"], 1)
			self.assertEqual(post_leader["workload"]["active"] - before_leader["workload"]["active"], 3)

	def test_real_mariadb_sla_obligations_dnc_auto_close_and_cross_tz_followups(self):
		"""Verify live MariaDB SLA SQL, DNC auto-close via record_attempt, and cross-TZ today callbacks."""
		from crm_cskh.p3_models import authorized_sla_write
		from crm_cskh.services import followup as followup_svc

		camp = self.make_campaign(team_scope=self.team_a)
		self.make_rule(camp, self.staff_b, weight=50, team=self.team_a)

		with mock.patch.object(worker_overview, "is_p3_sla_enabled", return_value=True):
			wi_sla_open, lead_sla_open = self.make_assigned_work_item(camp, self.staff_a)
			wi_sla_over, lead_sla_over = self.make_assigned_work_item(camp, self.staff_a)
			wi_dnc, lead_dnc = self.make_assigned_work_item(camp, self.staff_a)

			base_a = worker_overview.get_overview(self.staff_a)
			base_sla = base_a["sla"] or {"pending": 0, "overdue": 0}
			base_dnc = int(base_a["do_not_contact"] or 0)

			now_ts = now_datetime()
			past_due = add_to_date(now_ts, hours=-3)
			future_due = add_to_date(now_ts, hours=4)

			with authorized_sla_write():
				# 1. Valid open obligation (pending +1, overdue +0)
				frappe.get_doc(
					{
						"doctype": "CSKH SLA Obligation",
						"obligation_type": "FIRST_ATTEMPT",
						"work_item": wi_sla_open,
						"lead": lead_sla_open,
						"campaign": camp,
						"owner_user": self.staff_a,
						"attribution_status": "authoritative",
						"status": "open",
						"started_at": add_to_date(now_ts, hours=-1),
						"original_due_at": future_due,
						"effective_due_at": future_due,
						"idempotency_key": self.unique_key("ov-sla-open"),
					}
				).insert(ignore_permissions=True)

				# 2. Breached open obligation (pending +1, overdue +1)
				frappe.get_doc(
					{
						"doctype": "CSKH SLA Obligation",
						"obligation_type": "FIRST_ATTEMPT",
						"work_item": wi_sla_over,
						"lead": lead_sla_over,
						"campaign": camp,
						"owner_user": self.staff_a,
						"attribution_status": "authoritative",
						"status": "breached_open",
						"started_at": add_to_date(now_ts, hours=-5),
						"original_due_at": past_due,
						"effective_due_at": past_due,
						"breached_at": past_due,
						"idempotency_key": self.unique_key("ov-sla-over"),
					}
				).insert(ignore_permissions=True)

				# 3. Paused, informational_only, and mismatched-owner obligations MUST be excluded
				for idx, extra_flags in enumerate(
					(
						{"is_paused": 1, "owner_user": self.staff_a},
						{"informational_only": 1, "owner_user": self.staff_a},
						{"owner_user": self.staff_b},
					)
				):
					frappe.get_doc(
						{
							"doctype": "CSKH SLA Obligation",
							"obligation_type": "FIRST_CONNECTED",
							"work_item": wi_sla_open,
							"lead": lead_sla_open,
							"campaign": camp,
							"attribution_status": "authoritative",
							"status": "open",
							"started_at": add_to_date(now_ts, hours=-2),
							"original_due_at": past_due,
							"effective_due_at": past_due,
							"idempotency_key": self.unique_key(f"ov-sla-excl-{idx}"),
							**extra_flags,
						}
					).insert(ignore_permissions=True)

			after_sla_a = worker_overview.get_overview(self.staff_a)
			self.assertEqual(after_sla_a["sla"]["pending"] - base_sla["pending"], 2)
			self.assertEqual(after_sla_a["sla"]["overdue"] - base_sla["overdue"], 1)

			# 4. Real DNC via workflow.record_attempt auto-closes wi_dnc (assigned_to=None, owner_at_close=staff_a)
			#    and still increments staff_a's readable DNC count via owner_at_close.
			frappe.set_user(self.staff_a)
			workflow_api.record_attempt(
				work_item=wi_dnc,
				channel="phone_call",
				outcome_code="do_not_contact",
				note="Người lao động yêu cầu ngừng liên hệ thực tế",
				completion_reason="Người lao động từ chối liên hệ và yêu cầu DNC",
				idempotency_key=self.unique_key("ov-dnc-att"),
			)
			wi_dnc_row = frappe.db.get_value(
				"CSKH Work Item",
				wi_dnc,
				["status", "assigned_to", "owner_at_close"],
				as_dict=True,
			)
			self.assertEqual(wi_dnc_row["status"], "closed")
			self.assertFalse(wi_dnc_row["assigned_to"])
			self.assertEqual(wi_dnc_row["owner_at_close"], self.staff_a)
			self.assertEqual(int(frappe.db.get_value("CRM Lead", lead_dnc, "cskh_do_not_contact") or 0), 1)

			after_dnc_a = worker_overview.get_overview(self.staff_a)
			self.assertEqual(int(after_dnc_a["do_not_contact"] or 0) - base_dnc, 1)

			# 5. Cross-timezone parity between worker_overview callbacks.today and followup.list_followups(bucket='today')
			att_cb = workflow_api.record_attempt(
				work_item=wi_sla_open,
				channel="phone_call",
				outcome_code="no_answer",
				note="Hẹn cuối ngày",
				followup_due_at=str(add_to_date(now_ts, hours=2)),
				followup_title="Gọi lại tối nay",
				idempotency_key=self.unique_key("ov-tz-parity"),
			)
			frappe.set_user("Administrator")
			frappe.db.set_value(
				"CRM Task",
				att_cb["callback_task"],
				{"status": "Todo", "due_date": "2026-11-26 23:30:00"},
				update_modified=False,
			)
			fixed_now = datetime(2026, 11, 26, 10, 0, 0)
			try:
				with (
					mock.patch.object(worker_overview, "now_datetime", return_value=fixed_now),
					mock.patch.object(followup_svc, "now_datetime", return_value=fixed_now),
					mock.patch.object(
						runtime_cache, "get_cached_system_timezone", return_value="Asia/Ho_Chi_Minh"
					),
				):
					for tz_name in ("Asia/Ho_Chi_Minh", "Asia/Tokyo"):
						frappe.db.set_value("User", self.staff_a, "time_zone", tz_name, update_modified=False)
						runtime_cache.clear_all_local_caches()
						ov = worker_overview.get_overview(self.staff_a)
						fu = followup_svc.list_followups(
							status="open", bucket="today", page=1, page_size=50, actor=self.staff_a
						)
						self.assertEqual(ov["callbacks"]["today"], fu["total"], tz_name)
			finally:
				frappe.db.set_value("User", self.staff_a, "time_zone", "", update_modified=False)
				runtime_cache.clear_all_local_caches()
