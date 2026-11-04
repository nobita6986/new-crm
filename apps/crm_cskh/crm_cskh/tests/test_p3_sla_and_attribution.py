"""Integration & security tests for Historical Attribution (`P3-01`) and SLA Engine (`P3-03`).

Verifies:
  1. `team-as-of` historical attribution across team transfers (`CRM Sales Hierarchy` -> `CSKH Team Membership History`)
     and `historical_attribution_unknown` for pre-P3 legacy rows lacking membership evidence.
  2. `FIRST_ATTEMPT` `fulfilled_on_time`, `fulfilled_late`, delayed worker `breached_open` without shifting
     `original_due_at` / `effective_due_at`, and deduplicated `due_soon` + `breached` notifications (`0` duplicates).
  3. Ownership transfers `A -> B` before breach (`handoff_closed`), `A -> B` after breach (`breached_handoff` +
     `is_recovery_obligation`), and `A -> B -> A` distinct intervals with `max_handoff_grace_resets` cap.
  4. `FOLLOWUP` reusing native `CRM Task` (`0` second open callback task), reschedule before vs after breach
     (preserving prior breach), and `max_reschedules_without_approval` cap for Staff.
  5. `FIRST_CONNECTED` informational tracking (`informational_only = 1`) and policy-gated `MIN_ATTEMPTS`
     (`min_attempts_enabled = 0` vs `1` with `min_attempt_gap_minutes`).
  6. DNC before vs after breach, and approved `void`/`restore` attempt correction reconciliation.
  7. Audited `waive_obligation`, `pause_obligation`, `resume_obligation`, immutable `original_due_at`, and
     row-level / generic API security (`S03..S05`, `S14`, `S16`).
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from crm_cskh.jobs.evaluate_sla import evaluate_sla_obligations
from crm_cskh.p3_models import authorized_sla_write
from crm_cskh.services import contact_attempt, correction, followup, ownership, policies, sla_engine
from crm_cskh.services.attribution import (
	AUTHORITATIVE,
	UNKNOWN_HISTORICAL,
	backfill_pre_p3_assignment_attribution,
	resolve_team_for_user_at,
)
from crm_cskh.services.calendar import add_business_minutes, resolve_working_calendar
from crm_cskh.services.errors import ForbiddenScope, NoteRequired, PolicyConflict
from crm_cskh.tests.p3_base import P3TestCase


class TestP3SLAAndAttribution(P3TestCase):
	"""End-to-end verification of `P3-01` historical attribution and `P3-03` SLA lifecycle."""

	def setUp(self):
		super().setUp()
		self.campaign_a = self.make_campaign(team_scope=self.team_a)
		self.campaign_global = self.make_campaign(team_scope=None)
		self.make_rule(self.campaign_a, self.staff_a, weight=50)
		self.make_rule(self.campaign_a, self.staff_b, weight=50)
		self.make_rule(self.campaign_global, self.staff_a, weight=40)
		self.make_rule(self.campaign_global, self.staff_b, weight=30)
		self.make_rule(self.campaign_global, self.staff_c, weight=30)

	def _get_obligation(
		self, work_item: str, obligation_type: str = "FIRST_ATTEMPT", owner_user: str | None = None
	):
		filters: dict = {"work_item": work_item, "obligation_type": obligation_type}
		if owner_user:
			filters["owner_user"] = owner_user
		rows = frappe.db.get_all(
			"CSKH SLA Obligation",
			filters=filters,
			fields=["*"],
			order_by="creation desc",
		)
		self.assertTrue(rows, f"Expected obligation {obligation_type} on {work_item}")
		return rows[0]

	def test_historical_attribution_team_transfer_and_unknown_legacy_rows(self):
		"""`P3-01`: Moving Staff A3 from Team A to Team B preserves Team A on past intervals/obligations; legacy row -> `historical_attribution_unknown`."""
		# 1. Assign WI-1 to `staff_a3` while reporting to `leader_a` (`team_a`)
		wi1, _ = self.make_assigned_work_item(self.campaign_global, self.staff_a3)
		hist1 = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": wi1, "action": "assign"},
			["name", "team_at_assignment", "attribution_status", "ownership_start"],
			as_dict=True,
		)
		self.assertEqual(hist1.team_at_assignment, self.team_a)
		self.assertEqual(hist1.attribution_status, AUTHORITATIVE)

		ob1 = self._get_obligation(wi1, "FIRST_ATTEMPT", owner_user=self.staff_a3)
		self.assertEqual(ob1.team_at_obligation, self.team_a)
		self.assertEqual(ob1.attribution_status, AUTHORITATIVE)

		# 2. Transfer `staff_a3` in `CRM Sales Hierarchy` from `leader_a` (`team_a`) to `leader_b` (`team_b`)
		node_name = frappe.db.get_value("CRM Sales Hierarchy", {"user": self.staff_a3}, "name")
		node_doc = frappe.get_doc("CRM Sales Hierarchy", node_name)
		try:
			node_doc.reports_to = self.team_b
			node_doc.save(ignore_permissions=True)
			frappe.db.commit()

			# Assign WI-2 to `staff_a3` AFTER transfer -> attributed to `team_b`
			wi2, _ = self.make_assigned_work_item(self.campaign_global, self.staff_a3)
			hist2 = frappe.db.get_value(
				"CSKH Assignment History",
				{"work_item": wi2, "action": "assign"},
				["name", "team_at_assignment", "attribution_status"],
				as_dict=True,
			)
			self.assertEqual(hist2.team_at_assignment, self.team_b)
			self.assertEqual(hist2.attribution_status, AUTHORITATIVE)

			ob2 = self._get_obligation(wi2, "FIRST_ATTEMPT", owner_user=self.staff_a3)
			self.assertEqual(ob2.team_at_obligation, self.team_b)

			# Past WI-1 interval and obligation MUST retain `team_a` (never recalculated from current tree!)
			hist1_after = frappe.db.get_value(
				"CSKH Assignment History",
				hist1.name,
				["team_at_assignment", "attribution_status"],
				as_dict=True,
			)
			ob1_after = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob1.name,
				["team_at_obligation", "attribution_status"],
				as_dict=True,
			)
			self.assertEqual(hist1_after.team_at_assignment, self.team_a)
			self.assertEqual(ob1_after.team_at_obligation, self.team_a)

			# 3. Simulate a pre-P3 legacy Assignment History row from 2024 predating any membership interval
			legacy_dt = get_datetime("2024-06-15 09:00:00")
			team_guess, status_guess = resolve_team_for_user_at(
				self.staff_a3,
				legacy_dt,
				allow_live_capture_for_current_tx=False,
			)
			self.assertIsNone(team_guess)
			self.assertEqual(status_guess, UNKNOWN_HISTORICAL)

			frappe.db.set_value(
				"CSKH Assignment History",
				hist2.name,
				{
					"ownership_start": legacy_dt,
					"event_at": legacy_dt,
					"team_at_assignment": None,
					"attribution_status": "",
				},
				update_modified=False,
			)
			updated_cnt = backfill_pre_p3_assignment_attribution()
			self.assertGreaterEqual(updated_cnt, 1)
			legacy_checked = frappe.db.get_value(
				"CSKH Assignment History",
				hist2.name,
				["team_at_assignment", "attribution_status"],
				as_dict=True,
			)
			self.assertIsNone(legacy_checked.team_at_assignment)
			self.assertEqual(legacy_checked.attribution_status, UNKNOWN_HISTORICAL)
		finally:
			# Restore `staff_a3` back to `team_a` for subsequent tests
			frappe.set_user("Administrator")
			restore_doc = frappe.get_doc("CRM Sales Hierarchy", node_name)
			restore_doc.reports_to = self.team_a
			restore_doc.save(ignore_permissions=True)
			frappe.db.commit()

	def test_first_attempt_on_time_late_and_delayed_worker_idempotent_alerts(self):
		"""`P3-03`: On-time vs late attempt, delayed worker preserves `original_due_at`/`effective_due_at`, `0` duplicate alerts."""
		# 1. On-time fulfillment
		wi_ok, _ = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob_ok = self._get_obligation(wi_ok, "FIRST_ATTEMPT", owner_user=self.staff_a)
		self.assertEqual(ob_ok.status, "open")

		contact_attempt.record_attempt(
			work_item=wi_ok,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi đúng hạn SLA lần đầu",
			idempotency_key=self.unique_key("fa-ok"),
			actor=self.staff_a,
		)
		ob_ok_after = frappe.get_doc("CSKH SLA Obligation", ob_ok.name)
		self.assertEqual(ob_ok_after.status, "fulfilled_on_time")
		self.assertIsNotNone(ob_ok_after.fulfilled_at)
		self.assertIsNone(ob_ok_after.breached_at)

		# 2. Delayed background evaluator: `due_soon` -> `breached_open` -> late fulfillment -> `fulfilled_late`
		wi_late, _ = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob_late = self._get_obligation(wi_late, "FIRST_ATTEMPT", owner_user=self.staff_a)
		orig_due = get_datetime(ob_late.original_due_at)
		eff_due = get_datetime(ob_late.effective_due_at)

		# Trigger `due_soon` at `started_at + 25m` (5 business minutes left inside 10m warning window); retry -> 1 Notification Log!
		cal = resolve_working_calendar()
		due_soon_as_of = add_business_minutes(ob_late.started_at, 25, cal)
		r_warn1 = evaluate_sla_obligations(as_of=due_soon_as_of)
		r_warn2 = evaluate_sla_obligations(as_of=due_soon_as_of)
		self.assertGreaterEqual(r_warn1["warned_due_soon"], 1)
		self.assertEqual(r_warn2["notifications_created"], 0)

		due_soon_token = f"%[CSKH_SLA_ALERT:{ob_late.name}:due_soon]%"
		self.assertEqual(
			len(
				frappe.db.sql(
					"select name from `tabNotification Log` where for_user=%s and email_content like %s",
					(self.staff_a, due_soon_token),
				)
			),
			1,
		)

		# Now run worker 4 hours LATE (`eff_due + 4h`) and retry -> `breached_open`, `breached_at == eff_due`, 1 breach alert
		delayed_worker_dt = add_to_date(eff_due, hours=4)
		r_br1 = evaluate_sla_obligations(as_of=delayed_worker_dt)
		r_br2 = evaluate_sla_obligations(as_of=delayed_worker_dt)
		self.assertGreaterEqual(r_br1["breached"], 1)
		self.assertEqual(r_br2["notifications_created"], 0)

		ob_breached = frappe.get_doc("CSKH SLA Obligation", ob_late.name)
		self.assertEqual(ob_breached.status, "breached_open")
		self.assertEqual(get_datetime(ob_breached.original_due_at), orig_due)
		self.assertEqual(get_datetime(ob_breached.effective_due_at), eff_due)
		self.assertEqual(get_datetime(ob_breached.breached_at), eff_due)

		breach_token = f"%[CSKH_SLA_ALERT:{ob_late.name}:breached]%"
		self.assertEqual(
			len(
				frappe.db.sql(
					"select name from `tabNotification Log` where for_user=%s and email_content like %s",
					(self.staff_a, breach_token),
				)
			),
			1,
		)

		# Now record a late contact attempt (pushing `effective_due_at` into the past relative to `now_datetime()`
		# while keeping `original_due_at` untouched? Wait: if `eff_due` was in the future of real wall clock,
		# let's test true late attempt by setting a campaign policy with `first_attempt_sla_minutes = 0` or backdating!)
		camp_zero = self.make_campaign(team_scope=self.team_a)
		self.make_rule(camp_zero, self.staff_a, weight=100)
		policies.save_policy(
			policy_code=f"POL-ZERO-{frappe.generate_hash(length=6)}",
			title="DEV SLA 0 phút kiểm thử trễ hạn",
			scope_type="Campaign",
			campaign=camp_zero,
			first_attempt_sla_minutes=0,
			actor=self.admin_user,
		)
		wi_zero, _ = self.make_assigned_work_item(camp_zero, self.staff_a)
		ob_zero = self._get_obligation(wi_zero, "FIRST_ATTEMPT", owner_user=self.staff_a)
		# Wait or ensure `occurred_at` is 1 second after `effective_due_at` if inside working hours;
		# or set `effective_due_at` to 5 minutes ago via authorized_sla_write (leaving `original_due_at` immutable!)
		past_eff = add_to_date(now_datetime(), minutes=-5)
		with authorized_sla_write():
			doc_z = frappe.get_doc("CSKH SLA Obligation", ob_zero.name)
			doc_z.effective_due_at = past_eff
			doc_z.save(ignore_permissions=True)

		contact_attempt.record_attempt(
			work_item=wi_zero,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi sau khi đã quá hạn effective_due_at",
			idempotency_key=self.unique_key("fa-late"),
			actor=self.staff_a,
		)
		ob_zero_after = frappe.get_doc("CSKH SLA Obligation", ob_zero.name)
		self.assertEqual(ob_zero_after.status, "fulfilled_late")
		self.assertEqual(get_datetime(ob_zero_after.breached_at), get_datetime(past_eff))

	def test_ownership_transfer_before_and_after_breach_and_a_b_a_reset_cap(self):
		"""`P3-03`: Transfer `A->B` before breach (`handoff_closed`), after breach (`breached_handoff`), and `A->B->A` grace reset cap."""
		camp = self.make_campaign(team_scope=self.team_a)
		self.make_rule(camp, self.staff_a, weight=50)
		self.make_rule(camp, self.staff_b, weight=50)
		policies.save_policy(
			policy_code=f"POL-HANDOFF-{frappe.generate_hash(length=6)}",
			title="DEV Handoff Cap 2",
			scope_type="Campaign",
			campaign=camp,
			first_attempt_sla_minutes=10,
			handoff_min_grace_minutes=20,
			max_handoff_grace_resets=2,
			actor=self.admin_user,
		)

		# 1. `A -> B -> A -> B` BEFORE breach:
		# Since `first_attempt_sla_minutes=10` < `handoff_min_grace_minutes=20`, first 2 handoffs consume grace resets (1, 2),
		# and the 3rd handoff hits `handoff_cap_reached = 1`!
		wi, _ = self.make_assigned_work_item(camp, self.staff_a)
		ob_a1 = self._get_obligation(wi, "FIRST_ATTEMPT", owner_user=self.staff_a)

		ownership.reassign(
			wi,
			self.staff_a,
			self.staff_b,
			reason="Chuyển lần 1 A sang B trước hạn",
			request_id=self.unique_key("tr1"),
			actor=self.admin_user,
		)
		self.assertEqual(frappe.db.get_value("CSKH SLA Obligation", ob_a1.name, "status"), "handoff_closed")
		ob_b1 = self._get_obligation(wi, "FIRST_ATTEMPT", owner_user=self.staff_b)
		self.assertEqual(ob_b1.status, "open")
		self.assertEqual(int(ob_b1.handoff_reset_seq), 1)
		self.assertEqual(int(ob_b1.handoff_cap_reached), 0)

		# Transfer 2: `B -> A` (`A` gets a brand-new interval & obligation distinct from `ob_a1`!)
		ownership.reassign(
			wi,
			self.staff_b,
			self.staff_a,
			reason="Chuyển lần 2 B quay lại A",
			request_id=self.unique_key("tr2"),
			actor=self.admin_user,
		)
		self.assertEqual(frappe.db.get_value("CSKH SLA Obligation", ob_b1.name, "status"), "handoff_closed")
		a_obs = frappe.db.get_all(
			"CSKH SLA Obligation",
			filters={"work_item": wi, "obligation_type": "FIRST_ATTEMPT", "owner_user": self.staff_a},
			fields=["name", "status", "interval_event", "handoff_reset_seq", "handoff_cap_reached"],
			order_by="creation asc",
		)
		self.assertEqual(len(a_obs), 2)
		self.assertNotEqual(a_obs[0].interval_event, a_obs[1].interval_event)
		self.assertEqual(a_obs[0].status, "handoff_closed")
		self.assertEqual(a_obs[1].status, "open")
		self.assertEqual(int(a_obs[1].handoff_reset_seq), 2)

		# Transfer 3: `A -> B` exceeds `max_handoff_grace_resets=2` -> `handoff_cap_reached = 1`
		ownership.reassign(
			wi,
			self.staff_a,
			self.staff_b,
			reason="Chuyển lần 3 chạm trần chống lách SLA",
			request_id=self.unique_key("tr3"),
			actor=self.admin_user,
		)
		b_obs = frappe.db.get_all(
			"CSKH SLA Obligation",
			filters={"work_item": wi, "obligation_type": "FIRST_ATTEMPT", "owner_user": self.staff_b},
			fields=["name", "status", "handoff_reset_seq", "handoff_cap_reached"],
			order_by="creation desc",
		)
		self.assertEqual(b_obs[0].status, "open")
		self.assertEqual(int(b_obs[0].handoff_cap_reached), 1)

		# 2. Transfer AFTER breach -> `A` permanently retains `breached_handoff`, `B` receives `is_recovery_obligation = 1`
		wi_br, _ = self.make_assigned_work_item(camp, self.staff_a)
		ob_br_a = self._get_obligation(wi_br, "FIRST_ATTEMPT", owner_user=self.staff_a)
		past_due = add_to_date(now_datetime(), minutes=-20)
		with authorized_sla_write():
			d = frappe.get_doc("CSKH SLA Obligation", ob_br_a.name)
			d.effective_due_at = past_due
			d.save(ignore_permissions=True)

		ownership.reassign(
			wi_br,
			self.staff_a,
			self.staff_b,
			reason="Chuyển giao sau khi A đã vi phạm SLA",
			request_id=self.unique_key("tr-after-br"),
			actor=self.admin_user,
		)
		ob_br_a_after = frappe.get_doc("CSKH SLA Obligation", ob_br_a.name)
		self.assertEqual(ob_br_a_after.status, "breached_handoff")
		self.assertEqual(get_datetime(ob_br_a_after.breached_at), get_datetime(past_due))

		ob_br_b = self._get_obligation(wi_br, "FIRST_ATTEMPT", owner_user=self.staff_b)
		self.assertEqual(ob_br_b.status, "open")
		self.assertEqual(int(ob_br_b.is_recovery_obligation), 1)

	def test_followup_reuse_crm_task_reschedule_before_and_after_breach_and_self_cap(self):
		"""`P3-03`: Reuses single `CRM Task`, enforces `max_reschedules_without_approval=1` for Staff, preserves breach on late reschedule."""
		camp = self.make_campaign(team_scope=self.team_a)
		self.make_rule(camp, self.staff_a, weight=100)
		policies.save_policy(
			policy_code=f"POL-FU-{frappe.generate_hash(length=6)}",
			title="DEV Followup Cap 1",
			scope_type="Campaign",
			campaign=camp,
			max_reschedules_without_approval=1,
			followup_grace_minutes=15,
			actor=self.admin_user,
		)

		wi, _ = self.make_assigned_work_item(camp, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			note="Mở hồ sơ trước khi tạo lịch hẹn",
			idempotency_key=self.unique_key("fu-att0"),
			actor=self.staff_a,
		)

		fu1 = followup.schedule_followup(
			work_item=wi,
			due_at=self.future_due_str(hours=2),
			title="Gọi lại lần đầu",
			note="Khách bận họp",
			idempotency_key=self.unique_key("fu-s1"),
			actor=self.staff_a,
		)
		task_id = fu1["task_id"]
		ob_fu0 = self._get_obligation(wi, "FOLLOWUP", owner_user=self.staff_a)
		self.assertEqual(ob_fu0.followup_task, str(task_id))
		self.assertEqual(ob_fu0.status, "open")

		# 1. Staff reschedules 1st time BEFORE breach -> allowed (`1 <= max_reschedules_without_approval`), `ob_fu0` -> `superseded`
		fu2 = followup.schedule_followup(
			work_item=wi,
			due_at=self.future_due_str(hours=4),
			reason="Khách nhắn dời sang cuối chiều",
			idempotency_key=self.unique_key("fu-s2"),
			actor=self.staff_a,
		)
		self.assertEqual(fu2["task_id"], task_id)
		self.assertEqual(
			frappe.db.count(
				"CRM Task",
				{
					"cskh_work_item": wi,
					"cskh_is_followup": 1,
					"status": ["in", ["Backlog", "Todo", "In Progress"]],
				},
			),
			1,
		)
		self.assertEqual(frappe.db.get_value("CSKH SLA Obligation", ob_fu0.name, "status"), "superseded")
		ob_fu1 = self._get_obligation(wi, "FOLLOWUP", owner_user=self.staff_a)
		self.assertEqual(int(ob_fu1.reschedule_seq), 1)
		self.assertEqual(ob_fu1.status, "open")

		# 2. Staff tries 2nd self-reschedule -> rejected with `PolicyConflict`!
		with self.assertRaises(PolicyConflict):
			followup.schedule_followup(
				work_item=wi,
				due_at=self.future_due_str(hours=6),
				reason="Nhân viên tự dời vượt trần",
				idempotency_key=self.unique_key("fu-s3-denied"),
				actor=self.staff_a,
			)

		# 3. Simulate overdue `ob_fu1` and Leader reschedules AFTER breach -> `ob_fu1` MUST stay `breached_handoff` (never erased!)
		past_due = add_to_date(now_datetime(), minutes=-30)
		with authorized_sla_write():
			d_fu1 = frappe.get_doc("CSKH SLA Obligation", ob_fu1.name)
			d_fu1.effective_due_at = past_due
			d_fu1.save(ignore_permissions=True)

		followup.schedule_followup(
			work_item=wi,
			due_at=self.future_due_str(hours=6),
			reason="Leader phê duyệt đặt lại lịch hẹn cứu hồ sơ sau khi đã trễ",
			idempotency_key=self.unique_key("fu-s3-leader"),
			actor=self.leader_a,
		)
		ob_fu1_after = frappe.get_doc("CSKH SLA Obligation", ob_fu1.name)
		self.assertEqual(ob_fu1_after.status, "breached_handoff")
		self.assertEqual(get_datetime(ob_fu1_after.breached_at), get_datetime(past_due))

		ob_fu2 = self._get_obligation(wi, "FOLLOWUP", owner_user=self.staff_a)
		self.assertEqual(ob_fu2.status, "open")
		self.assertEqual(int(ob_fu2.is_recovery_obligation), 1)
		self.assertEqual(int(ob_fu2.reschedule_seq), 2)

	def test_first_connected_informational_and_min_attempts_policy_gate(self):
		"""`P3-03`: `FIRST_CONNECTED` has `informational_only=1` (0 breach alerts); `MIN_ATTEMPTS` only activates when `min_attempts_enabled=1`."""
		# 1. Default policy (`min_attempts_enabled = 0`): `FIRST_CONNECTED` is informational, closing unreached creates 0 `MIN_ATTEMPTS`
		wi1, _ = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob_fc = self._get_obligation(wi1, "FIRST_CONNECTED", owner_user=self.staff_a)
		self.assertEqual(int(ob_fc.informational_only), 1)

		contact_attempt.record_attempt(
			work_item=wi1,
			channel="phone_call",
			outcome_code="no_answer",
			note="Khách không bắt máy",
			idempotency_key=self.unique_key("fc-na"),
			actor=self.staff_a,
		)
		# Even if `FIRST_CONNECTED` passes target time, evaluator emits 0 user breach alerts for `informational_only=1`
		evaluate_sla_obligations(as_of=add_to_date(ob_fc.effective_due_at, hours=5))
		fc_alert_cnt = frappe.db.sql(
			"select count(*) from `tabNotification Log` where email_content like %s",
			(f"%[CSKH_SLA_ALERT:{ob_fc.name}:%",),
		)[0][0]
		self.assertEqual(int(fc_alert_cnt), 0)

		contact_attempt.close_work_item(
			work_item=wi1,
			completion_reason="Đóng hồ sơ theo chính sách mặc định chưa bật MIN_ATTEMPTS",
			idempotency_key=self.unique_key("cl-no-min"),
			actor=self.staff_a,
		)
		self.assertEqual(
			frappe.db.count("CSKH SLA Obligation", {"work_item": wi1, "obligation_type": "MIN_ATTEMPTS"}),
			0,
		)

		# 2. Policy with `min_attempts_enabled = 1, min_attempts_before_close = 2, min_attempt_gap_minutes = 30`
		camp_min = self.make_campaign(team_scope=self.team_a)
		self.make_rule(camp_min, self.staff_a, weight=100)
		policies.save_policy(
			policy_code=f"POL-MIN-{frappe.generate_hash(length=6)}",
			title="DEV Min Attempts 2 Spaced 30m",
			scope_type="Campaign",
			campaign=camp_min,
			allowed_backdate_minutes=180,
			min_attempts_enabled=True,
			min_attempts_before_close=2,
			min_attempt_gap_minutes=30,
			actor=self.admin_user,
		)

		# Case 2A: 2 rapid attempts within 1 minute (< 30m gap) -> `spaced_count = 1 < 2` -> `MIN_ATTEMPTS` = `breached_open`
		wi_rapid, _ = self.make_assigned_work_item(camp_min, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi_rapid,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi dồn lần 1",
			idempotency_key=self.unique_key("min-r1"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_rapid,
			channel="phone_call",
			outcome_code="busy",
			note="Gọi dồn lần 2 ngay lập tức",
			idempotency_key=self.unique_key("min-r2"),
			actor=self.staff_a,
		)
		contact_attempt.close_work_item(
			work_item=wi_rapid,
			completion_reason="Đóng sớm khi chưa đủ giãn cách 30 phút",
			idempotency_key=self.unique_key("min-r-close"),
			actor=self.staff_a,
		)
		ob_min_fail = self._get_obligation(wi_rapid, "MIN_ATTEMPTS", owner_user=self.staff_a)
		self.assertEqual(ob_min_fail.status, "breached_open")

		# Case 2B: 2 attempts spaced 45 minutes apart (`>= 30m`) -> `MIN_ATTEMPTS` = `fulfilled_on_time`
		wi_spaced, _ = self.make_assigned_work_item(camp_min, self.staff_a)
		own_start = add_to_date(now_datetime(), minutes=-60)
		frappe.db.set_value("CSKH Work Item", wi_spaced, "assigned_at", own_start, update_modified=False)
		frappe.db.sql(
			"update `tabCSKH Assignment History` set ownership_start=%s, event_at=%s where work_item=%s and action='assign'",
			(own_start, own_start, wi_spaced),
		)
		t1 = add_to_date(now_datetime(), minutes=-50)
		contact_attempt.record_attempt(
			work_item=wi_spaced,
			channel="phone_call",
			outcome_code="no_answer",
			occurred_at=str(t1),
			note="Gọi lần 1 cách đây 50 phút",
			idempotency_key=self.unique_key("min-s1"),
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_spaced,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi lần 2 đủ giãn cách > 30 phút",
			idempotency_key=self.unique_key("min-s2"),
			actor=self.staff_a,
		)
		contact_attempt.close_work_item(
			work_item=wi_spaced,
			completion_reason="Đóng hợp lệ sau đủ 2 lần gọi giãn cách",
			idempotency_key=self.unique_key("min-s-close"),
			actor=self.staff_a,
		)
		ob_min_ok = self._get_obligation(wi_spaced, "MIN_ATTEMPTS", owner_user=self.staff_a)
		self.assertEqual(ob_min_ok.status, "fulfilled_on_time")

	def test_dnc_before_vs_after_breach_and_approved_correction_reconciliation(self):
		"""`P3-03`: Admin DNC before breach -> `canceled_validly`, after breach -> `breached_handoff`; void/restore reconciles SLA."""
		# 1. Admin DNC before breach -> `canceled_validly`
		wi_dnc_ok, lead_dnc_ok = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob_dnc_ok = self._get_obligation(wi_dnc_ok, "FIRST_ATTEMPT", owner_user=self.staff_a)
		policies.set_lead_dnc(
			lead=lead_dnc_ok,
			do_not_contact=True,
			reason="Khách gửi yêu cầu ngừng liên hệ qua tổng đài trước hạn SLA",
			idempotency_key=self.unique_key("dnc-ok"),
			actor=self.admin_user,
		)
		self.assertEqual(
			frappe.db.get_value("CSKH SLA Obligation", ob_dnc_ok.name, "status"), "canceled_validly"
		)

		# 2. Admin DNC after breach -> `breached_handoff` (never erases prior breach!)
		wi_dnc_late, lead_dnc_late = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob_dnc_late = self._get_obligation(wi_dnc_late, "FIRST_ATTEMPT", owner_user=self.staff_a)
		with authorized_sla_write():
			d = frappe.get_doc("CSKH SLA Obligation", ob_dnc_late.name)
			d.effective_due_at = add_to_date(now_datetime(), minutes=-15)
			d.save(ignore_permissions=True)

		policies.set_lead_dnc(
			lead=lead_dnc_late,
			do_not_contact=True,
			reason="Khách báo DNC sau khi nhân viên đã để quá hạn SLA",
			idempotency_key=self.unique_key("dnc-late"),
			actor=self.admin_user,
		)
		self.assertEqual(
			frappe.db.get_value("CSKH SLA Obligation", ob_dnc_late.name, "status"), "breached_handoff"
		)

		# 3. Approved `void` & `restore` on sole attempt reconciles `FIRST_ATTEMPT`
		wi_corr, _ = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob_corr = self._get_obligation(wi_corr, "FIRST_ATTEMPT", owner_user=self.staff_a)
		att = contact_attempt.record_attempt(
			work_item=wi_corr,
			channel="phone_call",
			outcome_code="no_answer",
			note="Ghi nhận nhầm hồ sơ",
			idempotency_key=self.unique_key("corr-att"),
			actor=self.staff_a,
		)
		self.assertEqual(
			frappe.db.get_value("CSKH SLA Obligation", ob_corr.name, "status"), "fulfilled_on_time"
		)

		void_req = correction.request_correction(
			attempt=att["attempt_id"],
			action="void",
			reason="Bấm nhầm khách hàng, cần hủy bản ghi liên hệ",
			auto_approve=True,
			idempotency_key=self.unique_key("corr-void"),
			actor=self.leader_a,
		)
		self.assertEqual(void_req["status"], "approved")
		self.assertIn(
			frappe.db.get_value("CSKH SLA Obligation", ob_corr.name, "status"),
			("open", "breached_open"),
		)

		restore_req = correction.request_correction(
			attempt=att["attempt_id"],
			action="restore",
			reason="Xác minh lại cuộc gọi là hợp lệ, khôi phục",
			auto_approve=True,
			idempotency_key=self.unique_key("corr-restore"),
			actor=self.admin_user,
		)
		self.assertEqual(restore_req["status"], "approved")
		self.assertEqual(
			frappe.db.get_value("CSKH SLA Obligation", ob_corr.name, "status"), "fulfilled_on_time"
		)

	def test_waive_pause_resume_immutability_and_sla_security_matrix(self):
		"""`P3-03` & `S03..S05`, `S14`, `S16`: Pause/resume/waive audit, immutable `original_due_at`, and strict RBAC."""
		wi, _ = self.make_assigned_work_item(self.campaign_a, self.staff_a)
		ob = self._get_obligation(wi, "FIRST_ATTEMPT", owner_user=self.staff_a)
		orig_due = get_datetime(ob.original_due_at)

		# 1. `original_due_at` is strictly immutable even inside `authorized_sla_write()`
		with self.assertRaises(PolicyConflict):
			with authorized_sla_write():
				d_mut = frappe.get_doc("CSKH SLA Obligation", ob.name)
				d_mut.original_due_at = add_to_date(orig_due, hours=2)
				d_mut.save(ignore_permissions=True)

		# 2. Direct ORM write/delete outside `sla_engine` is blocked (`PermissionError`)
		for actor in ("Administrator", self.admin_user, self.leader_a, self.staff_a):
			frappe.set_user(actor)
			with self.assertRaises(frappe.PermissionError):
				d_raw = frappe.get_doc("CSKH SLA Obligation", ob.name)
				d_raw.status = "waived"
				d_raw.save(ignore_permissions=True)
			with self.assertRaises(frappe.PermissionError):
				frappe.delete_doc("CSKH SLA Obligation", ob.name, force=True, ignore_permissions=True)
		frappe.set_user("Administrator")

		# 3. `pause_obligation` + `resume_obligation` shifts `effective_due_at` by exact business minutes while preserving `original_due_at`
		# Pause at a deterministic Monday shift timestamp `09:00` and resume at `09:20` (+20 business minutes)
		pause_t = get_datetime("2026-04-27 09:00:00")
		resume_t = get_datetime("2026-04-27 09:20:00")
		# Ensure `effective_due_at` is after `pause_t` so pause is valid
		cal = resolve_working_calendar()
		base_eff = get_datetime("2026-04-27 10:00:00")
		with authorized_sla_write():
			d_prep = frappe.get_doc("CSKH SLA Obligation", ob.name)
			d_prep.effective_due_at = base_eff
			d_prep.save(ignore_permissions=True)

		# Staff, BOD, and Cross-Team Leader B cannot pause/waive
		for unauth in (self.staff_a, self.bod_user, self.leader_b):
			with self.assertRaises(ForbiddenScope):
				sla_engine.pause_obligation(ob.name, reason="Trái thẩm quyền", actor=unauth, now=pause_t)
			with self.assertRaises(ForbiddenScope):
				sla_engine.waive_obligation(ob.name, reason="Trái thẩm quyền", actor=unauth)

		# Missing reason rejected (`NoteRequired`)
		with self.assertRaises(NoteRequired):
			sla_engine.pause_obligation(ob.name, reason="   ", actor=self.leader_a, now=pause_t)
		with self.assertRaises(NoteRequired):
			sla_engine.waive_obligation(ob.name, reason="", actor=self.leader_a)

		paused_res = sla_engine.pause_obligation(
			ob.name,
			reason="Hệ thống tổng đài bảo trì đột xuất 20 phút",
			actor=self.leader_a,
			now=pause_t,
		)
		self.assertTrue(paused_res["is_paused"])

		resumed_res = sla_engine.resume_obligation(
			ob.name,
			reason="Tổng đài hoạt động trở lại",
			actor=self.leader_a,
			now=resume_t,
		)
		self.assertFalse(resumed_res["is_paused"])
		self.assertAlmostEqual(resumed_res["total_paused_business_minutes"], 20.0, places=2)
		ob_after_resume = frappe.get_doc("CSKH SLA Obligation", ob.name)
		self.assertEqual(get_datetime(ob_after_resume.original_due_at), orig_due)
		self.assertEqual(
			get_datetime(ob_after_resume.effective_due_at),
			add_business_minutes(base_eff, 20, cal),
		)

		# 4. Self-waiving Leader check: if Leader A owns an obligation personally, Leader A CANNOT waive it (`ForbiddenScope`), only Admin can!
		self.make_rule(self.campaign_a, self.leader_a, weight=20)
		wi_leader, _ = self.make_assigned_work_item(self.campaign_a, self.leader_a)
		ob_leader = self._get_obligation(wi_leader, "FIRST_ATTEMPT", owner_user=self.leader_a)
		with self.assertRaises(ForbiddenScope):
			sla_engine.waive_obligation(
				ob_leader.name, reason="Leader tự miễn SLA của mình", actor=self.leader_a
			)

		# Authorized Leader A CAN waive subordinate Staff A's obligation
		waived_res = sla_engine.waive_obligation(
			ob.name,
			reason="Sự cố mạng diện rộng đã có biên bản xác nhận",
			actor=self.leader_a,
		)
		self.assertEqual(waived_res["status"], "waived")
		self.assertEqual(waived_res["waived_by"], self.leader_a)

		# 5. Read visibility: BOD denied raw obligation list/detail; Staff A loses read access to `ob` once `wi` transfers to `staff_b`
		with self.assertRaises(ForbiddenScope):
			sla_engine.list_obligations(work_item=wi, actor=self.bod_user)
		with self.assertRaises(ForbiddenScope):
			sla_engine.get_obligation_detail(ob.name, actor=self.bod_user)

		ownership.reassign(
			wi,
			self.staff_a,
			self.staff_b,
			reason="Chuyển hồ sơ sang Staff B kiểm tra ẩn lịch sử SLA của Staff A",
			request_id=self.unique_key("sec-tr"),
			actor=self.admin_user,
		)
		with self.assertRaises(ForbiddenScope):
			sla_engine.get_obligation_detail(ob.name, actor=self.staff_a)
		staff_a_rows = sla_engine.list_obligations(work_item=wi, actor=self.staff_a)["rows"]
		self.assertEqual(staff_a_rows, [])
		# Positive control: Leader A and Admin still read full obligation history of `wi`
		leader_rows = sla_engine.list_obligations(work_item=wi, actor=self.leader_a)["rows"]
		self.assertGreaterEqual(len(leader_rows), 1)
