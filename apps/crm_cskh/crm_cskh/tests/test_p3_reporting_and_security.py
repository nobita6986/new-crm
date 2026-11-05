"""Integration, reconciliation, export, rollback & security (`S01..S18`) tests for `P3-07`, `P3-08`, `P3-09`.

Covers:
  1. Role capability matrix & cross-team / cross-staff isolation (`S01`, `S02`, `S04`, `S17`) across
     `crm_cskh.api.p3_reporting.*` and `crm_cskh.api.reporting.*` compatible aliases.
  2. `BOD Viewer` company-wide aggregate access without `System Manager`, `0` customer PII guarantee,
     and strict `403` blocks on `get_my_scorecard`, `include_pii=1`, and raw `CSKH SLA Obligation` (`S08`, `S09`, `S10`).
  3. Transfer-safe privacy redaction (`pii_redacted_due_to_transfer`) when a Lead moves to another owner/team
     (`S03`), and post-transfer staff team boundary isolation (`S15`).
  4. Snapshot <-> Drill-down numerator/denominator reconciliation across 11 KPIs and `NULLS LAST` sorting (`asc`/`desc`).
  5. SLA operational authority (`S05`) and `original_due_at` immutability (`S14`).
  6. 3-stage live export authorization (`enqueue` -> `worker` -> `download` + raw `File` `/private/files/...`),
     `File.owner == 'Administrator'` lock, live role revocation (`S12`), cross-actor denial (`S11`),
     TTL `410 EXPORT_EXPIRED` + disk reaper, `CSKH Export Job` `permlevel=1` filter-oracle defense (`S16`),
     and CSV formula-injection `'` escaping (`S18`).
  7. Runtime feature-flag rollback (`p3_reporting_enabled=0` & `p3_sla_enabled=0`) with zero P1/P2 regression.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from pathlib import Path

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.api import p3_reporting
from crm_cskh.api import reporting as reporting_alias
from crm_cskh.api import sla as sla_api
from crm_cskh.p3_models import authorized_sla_write
from crm_cskh.p3_permissions import is_export_file_downloadable
from crm_cskh.services import contact_attempt, correction, followup, ownership, p3_export
from crm_cskh.services.errors import (
	ExportExpired,
	ForbiddenScope,
	PolicyConflict,
)
from crm_cskh.services.kpi_calculator import COMPANY_SCOPE_ID
from crm_cskh.services.kpi_periods import resolve_period_spec, today_hcm
from crm_cskh.services.kpi_runs import execute_kpi_recompute
from crm_cskh.tests.p3_base import P3TestCase


class TestP3ReportingAndSecurity(P3TestCase):
	"""End-to-end verification of `P3-07` reporting, `P3-08` drill-down/export, and `S01..S18` security."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		for dt in (
			"CSKH Export Job",
			"CSKH Dirty Scope",
			"CSKH KPI Snapshot",
			"CSKH KPI Run",
			"CSKH QA Review",
		):
			if frappe.db.table_exists(dt):
				frappe.db.sql(f"delete from `tab{dt}`")
		frappe.db.commit()

		self.shared_campaign = self.make_campaign(title="Chiến dịch Chung P3", team_scope=None)
		self.make_rule(self.shared_campaign, self.staff_a, weight=40)
		self.make_rule(self.shared_campaign, self.staff_b, weight=30)
		self.make_rule(self.shared_campaign, self.staff_c, weight=30)

		self.team_b_campaign = self.make_campaign(title="Chiến dịch Riêng Team B", team_scope=self.team_b)
		self.make_rule(self.team_b_campaign, self.staff_c, weight=100)

	def _set_enabled_policies(self, updates: dict) -> list[dict]:
		fields = ["name", *list(updates.keys())]
		orig = frappe.get_all("CSKH Policy", filters={"enabled": 1}, fields=fields)
		for row in orig:
			frappe.db.set_value("CSKH Policy", row["name"], updates, update_modified=False)
		frappe.db.commit()
		return orig

	def _restore_policies(self, orig_rows: list[dict], keys: tuple[str, ...]) -> None:
		for row in orig_rows:
			frappe.db.set_value(
				"CSKH Policy",
				row["name"],
				{k: row[k] for k in keys},
				update_modified=False,
			)
		frappe.db.commit()

	def _seed_two_team_activity(self) -> tuple[str, str, str]:
		"""Seed deterministic Team A (`staff_a`) and Team B (`staff_c`) attempts today and return `(month_key, wi_a, wi_c)`."""
		frappe.set_user("Administrator")
		wi_a, lead_a = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		wi_c, lead_c = self.make_assigned_work_item(self.shared_campaign, self.staff_c)
		wi_b_priv, lead_b_priv = self.make_assigned_work_item(self.team_b_campaign, self.staff_c)

		frappe.set_user(self.staff_a)
		cl_a = self.make_native_call_log(lead_a, duration=60)
		contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=cl_a,
			note="Khách hàng quan tâm gói dịch vụ VIP",
			close_work_item=True,
			completion_reason="Hoàn tất chăm sóc",
			idempotency_key=self.unique_key("att-a"),
		)

		frappe.set_user(self.staff_c)
		cl_c = self.make_native_call_log(lead_c, duration=45)
		contact_attempt.record_attempt(
			work_item=wi_c,
			channel="phone_call",
			outcome_code="no_answer",
			call_log=cl_c,
			note="Khách Team B chưa bắt máy",
			followup_due_at=self.future_due_str(hours=3),
			followup_title="Gọi lại khách Team B",
			idempotency_key=self.unique_key("att-c"),
		)
		cl_c2 = self.make_native_call_log(lead_b_priv, duration=30)
		contact_attempt.record_attempt(
			work_item=wi_b_priv,
			channel="phone_call",
			outcome_code="connected_not_interested",
			call_log=cl_c2,
			note="Khách riêng Team B",
			close_work_item=True,
			completion_reason="Hoàn tất chăm sóc",
			idempotency_key=self.unique_key("att-c2"),
		)

		frappe.set_user("Administrator")
		frappe.db.commit()
		cur_d = today_hcm()
		month_key = f"{cur_d.year:04d}-{cur_d.month:02d}"
		period = resolve_period_spec("monthly", month_key)
		execute_kpi_recompute(period, run_mode="full", actor="Administrator")
		return month_key, wi_a, wi_c

	def test_role_capability_matrix_and_scope_isolation_s01_s02_s04(self):
		"""`S01`, `S02`, `S04`, `S17`: Staff self-only bounds, Leader subtree bounds, and `reporting.*` alias parity."""
		month_key, _, _ = self._seed_two_team_activity()

		# 1. `Staff A` (`S01`, `S02`, `S17`)
		frappe.set_user(self.staff_a)
		my_card = p3_reporting.get_my_scorecard(period_type="monthly", period_key=month_key)
		self.assertTrue(my_card["ok"])
		self.assertEqual(my_card["employee"]["employee_id"], self.staff_a)
		self.assertEqual(my_card["policy_notice"]["config_stage"], "DEV_CONFIGURABLE")
		self.assertFalse(my_card["policy_notice"]["production_approved"])
		self.assertGreaterEqual(my_card["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 1)

		# Compatible alias `crm_cskh.api.reporting.*` returns identical scorecard
		alias_card = reporting_alias.get_my_scorecard(period_type="monthly", period_key=month_key)
		self.assertEqual(
			alias_card["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"],
			my_card["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"],
		)

		emp_self = p3_reporting.get_employee_quality(period_type="monthly", period_key=month_key)
		self.assertEqual([r["employee_id"] for r in emp_self["rows"]], [self.staff_a])

		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_employee_quality(
				period_type="monthly", period_key=month_key, employee=self.staff_b
			)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_employee_quality(period_type="monthly", period_key=month_key, team=self.team_a)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_team_comparison(period_type="monthly", period_key=month_key)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_campaign_quality(period_type="monthly", period_key=month_key)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.request_recompute(period_type="monthly", period_key=month_key)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_run_status(run_id=my_card["freshness"]["run_id"])

		# 2. `Leader A` (`S04`): bounded strictly to `Team A` subtree
		frappe.set_user(self.leader_a)
		exec_leader = p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
		self.assertEqual(exec_leader["scope"]["scope_type"], "team")
		self.assertEqual(exec_leader["scope"]["scope_id"], self.team_a)

		teams_leader = p3_reporting.get_team_comparison(period_type="monthly", period_key=month_key)
		self.assertEqual([r["team_id"] for r in teams_leader["rows"]], [self.team_a])

		emps_leader = p3_reporting.get_employee_quality(period_type="monthly", period_key=month_key)
		returned_emps = {r["employee_id"] for r in emps_leader["rows"]}
		self.assertIn(self.staff_a, returned_emps)
		self.assertNotIn(self.staff_c, returned_emps)

		# Shared campaign for `Leader A` reflects ONLY `Team A`'s 1 attempt (excludes `Team B`'s attempt!)
		camps_leader = p3_reporting.get_campaign_quality(period_type="monthly", period_key=month_key)
		camp_ids = [r["campaign_id"] for r in camps_leader["rows"]]
		self.assertIn(self.shared_campaign, camp_ids)
		self.assertNotIn(self.team_b_campaign, camp_ids)
		shared_row_leader = next(r for r in camps_leader["rows"] if r["campaign_id"] == self.shared_campaign)
		self.assertEqual(shared_row_leader["contact_attempt_count"], 1)

		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_team_comparison(period_type="monthly", period_key=month_key, team=self.team_b)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_employee_quality(
				period_type="monthly", period_key=month_key, employee=self.staff_c
			)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_campaign_quality(
				period_type="monthly", period_key=month_key, campaign=self.team_b_campaign
			)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_metric_drilldown(
				metric_code="CONTACT_ATTEMPT_COUNT",
				period_type="monthly",
				period_key=month_key,
				scope_type="company",
			)

	def test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10(self):
		"""`S08`, `S09`, `S10`: BOD Viewer gets company aggregates without `System Manager` and `0` customer PII."""
		month_key, wi_a, wi_c = self._seed_two_team_activity()
		lead_a = frappe.db.get_value("CSKH Work Item", wi_a, "lead")
		lead_c = frappe.db.get_value("CSKH Work Item", wi_c, "lead")
		phone_a = frappe.db.get_value("CRM Lead", lead_a, "mobile_no")
		phone_c = frappe.db.get_value("CRM Lead", lead_c, "mobile_no")

		bod_roles = set(frappe.get_roles(self.bod_user))
		self.assertIn("CSKH BOD Viewer", bod_roles)
		self.assertNotIn("System Manager", bod_roles)
		self.assertNotIn("CSKH CRM Admin", bod_roles)

		frappe.set_user(self.bod_user)
		overview = p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
		teams = p3_reporting.get_team_comparison(period_type="monthly", period_key=month_key)
		emps = p3_reporting.get_employee_quality(period_type="monthly", period_key=month_key)
		camps = p3_reporting.get_campaign_quality(period_type="monthly", period_key=month_key)
		run_status = p3_reporting.get_run_status(run_id=overview["freshness"]["run_id"])

		self.assertTrue(overview["ok"])
		self.assertFalse(overview["actor_scope"]["pii_allowed"])
		self.assertEqual(overview["scope"]["scope_id"], COMPANY_SCOPE_ID)
		self.assertGreaterEqual(overview["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 3)
		self.assertEqual(overview["metrics"]["QA_PASS_RATE"]["status"], "disabled")
		self.assertIsNone(overview["metrics"]["QA_PASS_RATE"]["value"])
		# Per `F-P3-03`, `qa_enabled=false` disables QA sampling (`QA_PASS_RATE`) without erasing P2 call-log `verified` status
		self.assertGreaterEqual(overview["attempt_breakdown"]["verified"], 1)
		self.assertGreaterEqual(len(teams["rows"]), 2)
		self.assertGreaterEqual(len(emps["rows"]), 2)
		self.assertGreaterEqual(len(camps["rows"]), 2)
		self.assertTrue(run_status["ok"])

		# Anonymized drill-down (`include_pii=0` / omitted) returns rows with `0` customer PII
		anon_drill = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=month_key,
			scope_type="company",
			page_size=100,
		)
		self.assertGreaterEqual(anon_drill["pagination"]["total"], 3)
		for row in anon_drill["rows"]:
			self.assertEqual(row["lead"], "REDACTED")
			self.assertIsNone(row["customer_name"])
			self.assertIsNone(row["phone"])
			self.assertIsNone(row["note_excerpt"])
			self.assertTrue(row["pii_redacted"])

		combined_json = json.dumps([overview, teams, emps, camps, anon_drill], ensure_ascii=False)
		for secret in (lead_a, lead_c, phone_a, phone_c, "Khách P2 Giả Lập", "gói dịch vụ VIP"):
			self.assertNotIn(secret, combined_json)

		# Explicit PII request or personal scorecard -> `403 ForbiddenScope` (`S09`, `S10`)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_my_scorecard(period_type="monthly", period_key=month_key)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_metric_drilldown(
				metric_code="CONTACT_ATTEMPT_COUNT",
				period_type="monthly",
				period_key=month_key,
				include_pii=1,
			)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.request_export(
				report_type="metric_drilldown",
				metric_code="CONTACT_ATTEMPT_COUNT",
				period_type="monthly",
				period_key=month_key,
				include_pii=1,
			)

		# Generic ORM / Desk list access to `CSKH SLA Obligation` & `CSKH QA Review` denied (`S09`)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("CSKH SLA Obligation")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("CSKH QA Review")
		ob_any = frappe.db.get_value("CSKH SLA Obligation", {}, "name")
		self.assertIsNotNone(ob_any)
		self.assertFalse(frappe.has_permission("CSKH SLA Obligation", "read", doc=ob_any, user=self.bod_user))

	def test_transferred_lead_and_staff_transfer_privacy_redaction_s03_s15(self):
		"""`S03`, `S15`: Historical KPI credit stays, but transferred Lead PII & post-transfer Team B activity are isolated."""
		frappe.set_user("Administrator")
		lead_secret = self.make_canonical_lead(first_name="Trần Thị Bí Mật Chuyển Giao")
		secret_phone = frappe.db.get_value("CRM Lead", lead_secret, "mobile_no")
		wi_t, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_a, lead=lead_secret)

		frappe.set_user(self.staff_a)
		cl_1 = self.make_native_call_log(lead_secret, duration=55)
		att_pre = contact_attempt.record_attempt(
			work_item=wi_t,
			channel="phone_call",
			outcome_code="no_answer",
			call_log=cl_1,
			note="Ghi chú riêng trước khi chuyển giao",
			followup_due_at=self.future_due_str(hours=4),
			followup_title="Hẹn gọi lại trước chuyển giao",
			idempotency_key=self.unique_key("pre-xfer"),
		)

		# Transfer `wi_t` (`lead_secret`) from `staff_a` (`Team A`) -> `staff_c` (`Team B`)
		frappe.set_user(self.admin_user)
		ownership.reassign(
			wi_t,
			self.staff_a,
			self.staff_c,
			reason="Điều chuyển sang Team B",
			request_id=self.unique_key("reassign-b"),
			actor=self.admin_user,
		)
		frappe.db.commit()

		cur_d = today_hcm()
		month_key = f"{cur_d.year:04d}-{cur_d.month:02d}"
		period = resolve_period_spec("monthly", month_key)
		execute_kpi_recompute(period, run_mode="full", actor="Administrator")

		# `Staff A`: keeps KPI count >= 1, `0` active obligations for transferred `wi_t`, `0` PII in drill-down (`S03`)
		frappe.set_user(self.staff_a)
		sc_a = p3_reporting.get_my_scorecard(period_type="monthly", period_key=month_key)
		self.assertGreaterEqual(sc_a["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 1)
		self.assertNotIn(wi_t, [o["work_item"] for o in sc_a["active_obligations"]])

		dd_a = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=month_key,
			campaign=self.shared_campaign,
			page_size=100,
		)
		row_a = next(r for r in dd_a["rows"] if r["attempt_id"] == att_pre["attempt_id"])
		self.assertIsNone(row_a["lead"])
		self.assertIsNone(row_a["customer_name"])
		self.assertIsNone(row_a["phone"])
		self.assertIsNone(row_a["note_excerpt"])
		self.assertTrue(row_a["pii_redacted"])
		self.assertTrue(row_a["pii_redacted_due_to_transfer"])
		self.assertNotIn(secret_phone, json.dumps(dd_a, ensure_ascii=False))
		self.assertNotIn("Trần Thị Bí Mật", json.dumps(dd_a, ensure_ascii=False))

		# `Leader A`: also gets `pii_redacted_due_to_transfer == True` because `lead_secret` now belongs to `Team B`!
		frappe.set_user(self.leader_a)
		dd_leader_a = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=month_key,
			scope_type="team",
			scope_id=self.team_a,
			campaign=self.shared_campaign,
			page_size=100,
		)
		row_la = next(r for r in dd_leader_a["rows"] if r["attempt_id"] == att_pre["attempt_id"])
		self.assertTrue(row_la["pii_redacted_due_to_transfer"])
		self.assertIsNone(row_la["phone"])

		# Meanwhile `Leader B` (current owner's team!) DOES read live PII when drilling down `staff_c` after `staff_c` acts
		frappe.set_user(self.staff_c)
		cl_2 = self.make_native_call_log(lead_secret, duration=40)
		att_post = contact_attempt.record_attempt(
			work_item=wi_t,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=cl_2,
			note="Team B đã chăm sóc tiếp",
			close_work_item=True,
			completion_reason="Hoàn tất chăm sóc",
			idempotency_key=self.unique_key("post-xfer-c"),
		)
		frappe.set_user("Administrator")
		frappe.db.commit()
		execute_kpi_recompute(period, run_mode="full", actor="Administrator")
		execute_kpi_recompute(
			period, run_mode="full", campaign_filter=self.shared_campaign, actor="Administrator"
		)

		frappe.set_user(self.leader_b)
		dd_leader_b = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=month_key,
			scope_type="team",
			scope_id=self.team_b,
			campaign=self.shared_campaign,
			page_size=100,
		)
		row_lb = next(r for r in dd_leader_b["rows"] if r["attempt_id"] == att_post["attempt_id"])
		self.assertFalse(row_lb["pii_redacted_due_to_transfer"])
		self.assertEqual(row_lb["lead"], lead_secret)

		# `S15`: Move `staff_a3` from `Team A` -> `Team B` in `CRM Sales Hierarchy`, then record Team B work
		frappe.set_user("Administrator")
		a3_node_name = frappe.db.get_value("CRM Sales Hierarchy", {"user": self.staff_a3}, "name")
		orig_parent = frappe.db.get_value("CRM Sales Hierarchy", a3_node_name, "reports_to")
		try:
			a3_doc = frappe.get_doc("CRM Sales Hierarchy", a3_node_name)
			a3_doc.reports_to = self.team_b
			a3_doc.save(ignore_permissions=True)
			frappe.db.commit()

			self.make_rule(self.team_b_campaign, self.staff_a3, weight=50)
			wi_a3_b, lead_a3_b = self.make_assigned_work_item(self.team_b_campaign, self.staff_a3)
			frappe.set_user(self.staff_a3)
			cl_a3 = self.make_native_call_log(lead_a3_b, duration=35)
			contact_attempt.record_attempt(
				work_item=wi_a3_b,
				channel="phone_call",
				outcome_code="connected_not_interested",
				call_log=cl_a3,
				note="Hoạt động sau khi chuyển sang Team B",
				close_work_item=True,
				completion_reason="Hoàn tất chăm sóc",
				idempotency_key=self.unique_key("a3-in-b"),
			)
			frappe.set_user("Administrator")
			frappe.db.commit()
			execute_kpi_recompute(
				period, run_mode="full", campaign_filter=self.shared_campaign, actor="Administrator"
			)
			execute_kpi_recompute(period, run_mode="full", actor="Administrator")

			# `Leader A` still sees ONLY `Team A`'s pre-transfer attempt in `shared_campaign` (`0` Team B leakage!)
			frappe.set_user(self.leader_a)
			ov_leader_a = p3_reporting.get_executive_overview(
				period_type="monthly", period_key=month_key, campaign=self.shared_campaign
			)
			self.assertEqual(ov_leader_a["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 1)
			with self.assertRaises(ForbiddenScope):
				p3_reporting.get_employee_quality(
					period_type="monthly", period_key=month_key, employee=self.staff_a3
				)
		finally:
			frappe.set_user("Administrator")
			a3_doc = frappe.get_doc("CRM Sales Hierarchy", a3_node_name)
			a3_doc.reports_to = orig_parent
			a3_doc.save(ignore_permissions=True)
			frappe.db.commit()

	def test_drilldown_reconciliation_across_eleven_kpis_and_nulls_last_sorting(self):
		"""`P3-07/08`: Drill-down `all_denominator` & `numerator_only` totals match snapshot `100%` across 11 KPIs + `NULLS LAST`."""
		frappe.set_user("Administrator")
		wi_1, lead_1 = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		wi_2, lead_2 = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		wi_3, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_c)

		# `wi_1`: on-time first attempt -> callback -> rescheduled once -> completed on time
		frappe.set_user(self.staff_a)
		cl_1a = self.make_native_call_log(lead_1, duration=40)
		contact_attempt.record_attempt(
			work_item=wi_1,
			channel="phone_call",
			outcome_code="no_answer",
			call_log=cl_1a,
			note="Lần 1 hẹn gọi lại",
			followup_due_at=self.future_due_str(hours=2),
			followup_title="Callback WI-1",
			idempotency_key=self.unique_key("rec-1a"),
		)
		followup.schedule_followup(
			work_item=wi_1,
			due_at=self.future_due_str(hours=3),
			title="Callback WI-1 dời lịch",
			note="Khách xin dời lịch 1 giờ",
			reason="Khách xin dời lịch 1 giờ",
			idempotency_key=self.unique_key("resched-1"),
			actor=self.staff_a,
		)

		cl_1b = self.make_native_call_log(lead_1, duration=65)
		contact_attempt.record_attempt(
			work_item=wi_1,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=cl_1b,
			complete_open_followup=True,
			note="Đã kết nối hoàn tất callback",
			close_work_item=True,
			completion_reason="Hoàn tất chăm sóc",
			idempotency_key=self.unique_key("rec-1b"),
		)

		# `wi_2`: attempt recorded then voided via approved correction + DNC outcome closing work item
		cl_2a = self.make_native_call_log(lead_2, duration=20)
		att_2a = contact_attempt.record_attempt(
			work_item=wi_2,
			channel="phone_call",
			outcome_code="busy",
			call_log=cl_2a,
			note="Ghi nhầm kết quả",
			followup_due_at=self.future_due_str(hours=2),
			followup_title="Callback nhầm",
			idempotency_key=self.unique_key("rec-2a"),
		)
		corr_req = correction.request_correction(
			attempt=att_2a["attempt_id"],
			action="void",
			reason="Bấm nhầm hồ sơ khách hàng",
			idempotency_key=self.unique_key("corr-void"),
		)
		frappe.set_user(self.leader_a)
		correction.review_correction(
			correction_id=corr_req["correction_id"],
			decision="approve",
			review_note="Đồng ý hủy bản ghi nhầm",
			idempotency_key=self.unique_key("corr-rev"),
		)

		frappe.set_user(self.staff_a)
		cl_2b = self.make_native_call_log(lead_2, duration=25)
		contact_attempt.record_attempt(
			work_item=wi_2,
			channel="phone_call",
			outcome_code="do_not_contact",
			call_log=cl_2b,
			note="Khách từ chối liên hệ DNC",
			close_work_item=True,
			completion_reason="Khách yêu cầu DNC",
			idempotency_key=self.unique_key("rec-2b"),
		)

		# `wi_3` (`staff_c`): backdate `FIRST_ATTEMPT` `effective_due_at` into the past so it breaches!
		frappe.set_user("Administrator")
		past_start = now_datetime() - timedelta(hours=3)
		past_due = now_datetime() - timedelta(hours=2)
		ob_3 = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_3, "obligation_type": "FIRST_ATTEMPT"},
			"name",
		)
		frappe.db.sql(
			"""
			update `tabCSKH SLA Obligation`
			set started_at = %s, original_due_at = %s, effective_due_at = %s, evaluation_anchor_at = %s
			where name = %s
			""",
			(past_start, past_due, past_due, past_due, ob_3),
		)
		frappe.db.commit()

		cur_d = today_hcm()
		month_key = f"{cur_d.year:04d}-{cur_d.month:02d}"
		period = resolve_period_spec("monthly", month_key)
		execute_kpi_recompute(
			period, run_mode="full", campaign_filter=self.shared_campaign, actor="Administrator"
		)

		frappe.set_user(self.admin_user)
		overview = p3_reporting.get_executive_overview(
			period_type="monthly", period_key=month_key, campaign=self.shared_campaign
		)
		comp_metrics = overview["metrics"]

		reconciled_codes = (
			"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE",
			"FIRST_ATTEMPT_SLA_RATE",
			"FOLLOWUP_SLA_RATE",
			"FIRST_CONNECTED_TRACKING_RATE",
			"CONNECTED_RATE",
			"VALID_OUTCOME_LOGGING_RATE",
			"CORRECTION_RATE",
			"VOID_ATTEMPT_RATE",
			"CALLBACK_COMPLETION_RATE",
			"RESCHEDULE_RATE",
			"DNC_COMPLIANCE_RATE",
		)
		for code in reconciled_codes:
			snap_m = comp_metrics[code]
			dd_all = p3_reporting.get_metric_drilldown(
				metric_code=code,
				period_type="monthly",
				period_key=month_key,
				scope_type="company",
				campaign=self.shared_campaign,
				filter_bucket="all_denominator",
				page_size=100,
			)
			dd_num = p3_reporting.get_metric_drilldown(
				metric_code=code,
				period_type="monthly",
				period_key=month_key,
				scope_type="company",
				campaign=self.shared_campaign,
				filter_bucket="numerator_only",
				page_size=100,
			)
			dd_bad = p3_reporting.get_metric_drilldown(
				metric_code=code,
				period_type="monthly",
				period_key=month_key,
				scope_type="company",
				campaign=self.shared_campaign,
				filter_bucket="breached_only",
				page_size=100,
			)
			self.assertEqual(
				dd_all["pagination"]["total"],
				snap_m["denominator"],
				f"Denominator mismatch on {code}",
			)
			self.assertEqual(
				dd_num["pagination"]["total"],
				snap_m["numerator"],
				f"Numerator mismatch on {code}",
			)
			if code not in ("CORRECTION_RATE", "VOID_ATTEMPT_RATE", "RESCHEDULE_RATE"):
				self.assertEqual(
					dd_num["pagination"]["total"] + dd_bad["pagination"]["total"],
					dd_all["pagination"]["total"],
					f"Numerator + breached != denominator on {code}",
				)

		# Verify `NULLS LAST` sorting (`desc` and `asc`) on `get_employee_quality`:
		# `staff_b` has `0` events -> `PROCESS_SCORE.value is None` -> MUST sort AFTER all non-null scores in BOTH directions!
		emp_desc = p3_reporting.get_employee_quality(
			period_type="monthly",
			period_key=month_key,
			campaign=self.shared_campaign,
			sort_by="PROCESS_SCORE",
			sort_dir="desc",
			page_size=50,
		)
		emp_asc = p3_reporting.get_employee_quality(
			period_type="monthly",
			period_key=month_key,
			campaign=self.shared_campaign,
			sort_by="PROCESS_SCORE",
			sort_dir="asc",
			page_size=50,
		)
		for res_list in (emp_desc["rows"], emp_asc["rows"]):
			seen_null = False
			for r in res_list:
				val = r["metrics"]["PROCESS_SCORE"]["value"]
				if val is None:
					seen_null = True
				else:
					self.assertFalse(
						seen_null, "Non-null score appeared AFTER a null score (violated NULLS LAST)!"
					)
		non_null_desc = [
			r["metrics"]["PROCESS_SCORE"]["value"]
			for r in emp_desc["rows"]
			if r["metrics"]["PROCESS_SCORE"]["value"] is not None
		]
		non_null_asc = [
			r["metrics"]["PROCESS_SCORE"]["value"]
			for r in emp_asc["rows"]
			if r["metrics"]["PROCESS_SCORE"]["value"] is not None
		]
		self.assertGreaterEqual(len(non_null_desc), 1)
		self.assertEqual(non_null_desc, sorted(non_null_desc, reverse=True))
		self.assertEqual(non_null_asc, sorted(non_null_asc))

	def test_sla_authority_and_original_due_immutability_s05_s14(self):
		"""`S05`, `S14`: Staff/cross-team Leader denied on SLA waive/pause; `original_due_at` strictly immutable."""
		frappe.set_user("Administrator")
		wi_a, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		ob_name = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_a, "obligation_type": "FIRST_ATTEMPT"},
			"name",
		)
		self.assertIsNotNone(ob_name)
		orig_due_before = frappe.db.get_value("CSKH SLA Obligation", ob_name, "original_due_at")

		# `Staff A` denied on `pause`, `resume`, `waive`, and `evaluate_due_obligations` (`S05`)
		frappe.set_user(self.staff_a)
		with self.assertRaises(ForbiddenScope):
			sla_api.pause_obligation(obligation_id=ob_name, reason="Nhân viên tự xin tạm dừng")
		with self.assertRaises(ForbiddenScope):
			sla_api.waive_obligation(obligation_id=ob_name, reason="Nhân viên tự miễn SLA")
		with self.assertRaises(ForbiddenScope):
			sla_api.evaluate_due_obligations()

		# `Leader B` (wrong team) denied on `Team A`'s obligation
		frappe.set_user(self.leader_b)
		with self.assertRaises(ForbiddenScope):
			sla_api.waive_obligation(obligation_id=ob_name, reason="Leader khác team can thiệp")

		# `Leader A` pauses & resumes cleanly; `original_due_at` never changes
		frappe.set_user(self.leader_a)
		paused = sla_api.pause_obligation(obligation_id=ob_name, reason="Hệ thống tổng đài bảo trì 5 phút")
		self.assertTrue(paused["is_paused"])
		resumed = sla_api.resume_obligation(obligation_id=ob_name, reason="Tổng đài hoạt động lại")
		self.assertFalse(resumed["is_paused"])
		self.assertEqual(
			str(frappe.db.get_value("CSKH SLA Obligation", ob_name, "original_due_at")),
			str(orig_due_before),
		)

		# Direct controller edit to `original_due_at` is blocked even inside `authorized_sla_write` (`S14`)
		frappe.set_user("Administrator")
		ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_name)
		ob_doc.original_due_at = add_to_date(orig_due_before, hours=5)
		with authorized_sla_write():
			with self.assertRaises(PolicyConflict):
				ob_doc.save(ignore_permissions=True)

	def test_three_stage_export_file_lock_revocation_ttl_and_csv_sanitization_s11_s12_s16_s18(self):
		"""`S11`, `S12`, `S16`, `S18`: 3-stage export auth, `File.owner='Administrator'`, live revocation, `410` TTL, CSV escaping."""
		# 1. Formula injection unit + end-to-end check (`S18`)
		for dangerous in (
			"=CMD|' /C calc'!A0",
			"+1+1",
			"-2+3",
			"@SUM(A1:A2)",
			'  =HYPERLINK("http://x")',
		):
			escaped = p3_export.sanitize_csv_cell(dangerous)
			self.assertTrue(
				escaped.startswith("'"), f"Expected leading quote on {dangerous!r}, got {escaped!r}"
			)

		frappe.set_user("Administrator")
		lead_inj = self.make_canonical_lead(first_name="=CMD|' /C calc'!A0")
		wi_inj, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_a, lead=lead_inj)

		frappe.set_user(self.staff_a)
		cl_inj = self.make_native_call_log(lead_inj, duration=35)
		contact_attempt.record_attempt(
			work_item=wi_inj,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=cl_inj,
			note="@SUM(1,2) khách hỏi báo giá",
			close_work_item=True,
			completion_reason="Hoàn tất báo giá",
			idempotency_key=self.unique_key("att-inj"),
		)
		frappe.set_user("Administrator")
		frappe.db.commit()

		cur_d = today_hcm()
		month_key = f"{cur_d.year:04d}-{cur_d.month:02d}"
		period = resolve_period_spec("monthly", month_key)
		execute_kpi_recompute(
			period, run_mode="full", campaign_filter=self.shared_campaign, actor="Administrator"
		)
		execute_kpi_recompute(period, run_mode="full", actor="Administrator")

		# 2. Stage 1 (`request_export`) + Stage 2 (`execute_export_job_now`) as `Leader A`
		frappe.set_user(self.leader_a)
		enq_1 = p3_reporting.request_export(
			report_type="metric_drilldown",
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=month_key,
			scope_type="team",
			scope_id=self.team_a,
			campaign=self.shared_campaign,
			include_pii=1,
		)
		exp_1 = enq_1["export_id"]
		self.assertTrue(exp_1.startswith("EXP-"))

		frappe.set_user("Administrator")
		w_res_1 = p3_export.execute_export_job_now(exp_1)
		self.assertEqual(w_res_1["status"], "completed")

		job_1 = frappe.get_doc("CSKH Export Job", exp_1)
		file_1 = frappe.get_doc("File", job_1.file_doc)
		self.assertEqual(int(file_1.is_private), 1)
		self.assertEqual(file_1.owner, "Administrator")
		self.assertEqual(file_1.attached_to_doctype, "CSKH Export Job")
		self.assertEqual(file_1.attached_to_name, exp_1)
		self.assertTrue(file_1.file_url.startswith("/private/files/"))

		# `get_export_status` hides raw `/private/files/...` URL; `permlevel=1` blocks filter-oracle on `file_url` (`S16`)
		frappe.set_user(self.leader_a)
		st_1 = p3_reporting.get_export_status(export_id=exp_1)
		self.assertEqual(st_1["status"], "completed")
		self.assertNotIn("/private/files/", json.dumps(st_1))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("CSKH Export Job", filters={"file_url": ["like", "%private%"]})

		# Authorized `Leader A` downloads CSV AND passes `File` `has_permission` / `is_downloadable`
		frappe.local.response = frappe._dict()
		dl_1 = p3_reporting.download_export(export_id=exp_1)
		self.assertEqual(dl_1["export_id"], exp_1)
		csv_text = frappe.local.response["filecontent"].decode("utf-8")
		parsed_rows = list(csv.reader(io.StringIO(csv_text)))
		self.assertGreaterEqual(len(parsed_rows), 2)
		flat_cells = [cell for row in parsed_rows[1:] for cell in row]
		self.assertTrue(any(cell.startswith("'=CMD") for cell in flat_cells))
		self.assertTrue(any(cell.startswith("'@SUM") for cell in flat_cells))
		self.assertTrue(frappe.has_permission("File", "read", doc=file_1, user=self.leader_a))
		self.assertTrue(file_1.is_downloadable())

		# 3. Cross-Actor Denial (`S11`): `Staff A`, `Leader B`, `BOD Viewer`, `Guest` denied on BOTH RPC & raw `File`!
		for intruder in (self.staff_a, self.leader_b, self.bod_user, "Guest"):
			frappe.set_user(intruder)
			with self.assertRaises(ForbiddenScope):
				p3_reporting.get_export_status(export_id=exp_1)
			with self.assertRaises(ForbiddenScope):
				p3_reporting.download_export(export_id=exp_1)
			self.assertFalse(
				frappe.has_permission("File", "read", doc=file_1, user=intruder),
				f"Intruder {intruder} must NOT have read permission on export File!",
			)
			self.assertFalse(is_export_file_downloadable(exp_1, user=intruder))

		# 4. Stage 2 Worker Revocation & Stage 3 Post-Completion Live Revocation (`S12`)
		frappe.set_user(self.leader_a)
		enq_2 = p3_reporting.request_export(
			report_type="team_comparison",
			period_type="monthly",
			period_key=month_key,
		)
		exp_2 = enq_2["export_id"]

		frappe.set_user("Administrator")
		leader_doc = frappe.get_doc("User", self.leader_a)
		leader_doc.remove_roles("Sales Manager")
		frappe.db.commit()
		try:
			# Queued `exp_2` is revoked at Stage 2 BEFORE writing any CSV file!
			w_res_2 = p3_export.execute_export_job_now(exp_2)
			self.assertEqual(w_res_2["status"], "revoked")
			self.assertIsNone(frappe.db.get_value("CSKH Export Job", exp_2, "file_doc"))

			# Already-completed `exp_1` is IMMEDIATELY blocked at Stage 3 on BOTH `download_export` and raw `File`!
			frappe.set_user(self.leader_a)
			with self.assertRaises(ForbiddenScope):
				p3_reporting.download_export(export_id=exp_1)
			self.assertFalse(frappe.has_permission("File", "read", doc=file_1, user=self.leader_a))
			self.assertFalse(file_1.is_downloadable())
			self.assertFalse(is_export_file_downloadable(exp_1, user=self.leader_a))
		finally:
			frappe.set_user("Administrator")
			leader_doc = frappe.get_doc("User", self.leader_a)
			leader_doc.add_roles("Sales Manager")
			frappe.db.commit()

		# 5. TTL Expiry (`410 ExportExpired`) & Disk Reaper (`cleanup_expired_exports`)
		frappe.set_user(self.admin_user)
		enq_3 = p3_reporting.request_export(
			report_type="executive_overview",
			period_type="monthly",
			period_key=month_key,
		)
		exp_3 = enq_3["export_id"]
		frappe.set_user("Administrator")
		p3_export.execute_export_job_now(exp_3)
		job_3 = frappe.get_doc("CSKH Export Job", exp_3)
		disk_path_3 = Path(frappe.get_site_path(job_3.file_url.lstrip("/")))
		self.assertTrue(disk_path_3.is_file())

		frappe.db.set_value(
			"CSKH Export Job",
			exp_3,
			"expires_at",
			now_datetime() - timedelta(seconds=15),
			update_modified=False,
		)
		frappe.db.commit()

		frappe.set_user(self.admin_user)
		with self.assertRaises(ExportExpired) as ctx:
			p3_reporting.download_export(export_id=exp_3)
		self.assertEqual(ctx.exception.http_status_code, 410)
		self.assertEqual(ctx.exception.code, "EXPORT_EXPIRED")
		self.assertFalse(is_export_file_downloadable(exp_3, user=self.admin_user))

		frappe.set_user("Administrator")
		reap = p3_export.cleanup_expired_exports()
		self.assertGreaterEqual(reap["expired_marked"], 1)
		self.assertFalse(disk_path_3.exists())

	def test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled(self):
		"""`P3-09`: Disabling `p3_reporting_enabled` & `p3_sla_enabled` degrades cleanly with `0` P1/P2 impact."""
		frappe.set_user("Administrator")
		orig = self._set_enabled_policies({"p3_reporting_enabled": 0, "p3_sla_enabled": 0})
		try:
			cur_d = today_hcm()
			month_key = f"{cur_d.year:04d}-{cur_d.month:02d}"

			# 1. `p3_reporting_enabled = 0`: read APIs return clean `disabled_by_policy`; export/recompute raise `409`
			frappe.set_user(self.bod_user)
			ov = p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
			self.assertTrue(ov["ok"])
			self.assertFalse(ov["reporting_enabled"])
			self.assertEqual(ov["status"], "disabled_by_policy")

			frappe.set_user(self.staff_a)
			sc = p3_reporting.get_my_scorecard(period_type="monthly", period_key=month_key)
			self.assertFalse(sc["reporting_enabled"])
			self.assertEqual(sc["status"], "disabled_by_policy")

			frappe.set_user(self.admin_user)
			with self.assertRaises(PolicyConflict):
				p3_reporting.request_export(
					report_type="executive_overview",
					period_type="monthly",
					period_key=month_key,
				)
			with self.assertRaises(PolicyConflict):
				p3_reporting.request_recompute(period_type="monthly", period_key=month_key)

			# 2. `p3_sla_enabled = 0`: full P1/P2 assign -> callback attempt -> resolve attempt -> close succeeds with `0` SLA rows!
			frappe.set_user("Administrator")
			wi_rb, lead_rb = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
			self.assertEqual(frappe.db.count("CSKH SLA Obligation", {"work_item": wi_rb}), 0)

			frappe.set_user(self.staff_a)
			cl_1 = self.make_native_call_log(lead_rb, duration=30)
			contact_attempt.record_attempt(
				work_item=wi_rb,
				channel="phone_call",
				outcome_code="no_answer",
				call_log=cl_1,
				note="Gọi khi p3_sla_enabled=0",
				followup_due_at=self.future_due_str(hours=2),
				followup_title="Callback khi tắt SLA",
				idempotency_key=self.unique_key("rb-1"),
			)

			cl_2 = self.make_native_call_log(lead_rb, duration=50)
			contact_attempt.record_attempt(
				work_item=wi_rb,
				channel="phone_call",
				outcome_code="connected_interested",
				call_log=cl_2,
				complete_open_followup=True,
				note="Hoàn tất bình thường khi tắt SLA",
				idempotency_key=self.unique_key("rb-2"),
			)
			contact_attempt.close_work_item(
				work_item=wi_rb,
				completion_reason="Hoàn tất chăm sóc khách hàng",
				idempotency_key=self.unique_key("rb-close"),
				actor=self.staff_a,
			)
			self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_rb, "status"), "closed")
			self.assertEqual(frappe.db.count("CSKH SLA Obligation", {"work_item": wi_rb}), 0)
		finally:
			frappe.set_user("Administrator")
			self._restore_policies(orig, ("p3_reporting_enabled", "p3_sla_enabled"))
