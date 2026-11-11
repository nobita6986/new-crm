"""Regression, exact parity, deep-pagination, N+1 ceiling, EXPLAIN & N->N-1 index rollback tests for G18 (`get_metric_drilldown`)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.utils import get_datetime, now_datetime

from crm_cskh.api import p3_reporting
from crm_cskh.p3_models import authorized_sla_write, ensure_p3_indexes
from crm_cskh.services import contact_attempt, correction, followup, ownership, report_query
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL
from crm_cskh.services.errors import ForbiddenScope
from crm_cskh.services.kpi_calculator import COMPANY_SCOPE_ID, UNATTRIBUTED_TEAM_ID
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import (
	_flush_pending_dirty_events,
	execute_kpi_recompute,
	schedule_post_commit_dirty_invalidation,
)
from crm_cskh.tests.p3_base import P3TestCase


class TestP3G18DrilldownOptimization(P3TestCase):
	"""Verify SQL-paginated `get_metric_drilldown` matches `_extract_drilldown_events(load_period_raw_dataset)` 1:1."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		ensure_p3_indexes()
		for dt in (
			"CSKH Export Job",
			"CSKH Dirty Scope",
			"CSKH KPI Snapshot",
			"CSKH KPI Run",
			"CSKH QA Review",
			"CSKH SLA Obligation",
			"CSKH Attempt Correction",
			"CSKH Contact Attempt",
			"CSKH Active Claim",
			"CSKH Assignment History",
			"CSKH Work Item",
		):
			if frappe.db.table_exists(dt):
				frappe.db.sql(f"delete from `tab{dt}`")
		if frappe.db.table_exists("CRM Lead"):
			frappe.db.sql("delete from `tabCRM Lead` where name like 'LEAD-G18-DEEP-%'")
		if frappe.db.table_exists("CRM Task") and frappe.db.has_column("CRM Task", "cskh_is_followup"):
			frappe.db.sql("delete from `tabCRM Task` where coalesce(cskh_is_followup, 0) = 1")
		frappe.db.commit()

		self.shared_campaign = self.make_campaign(title="Chiến dịch G18 Chung", team_scope=None)
		self.make_rule(self.shared_campaign, self.staff_a, weight=40)
		self.make_rule(self.shared_campaign, self.staff_b, weight=30)
		self.make_rule(self.shared_campaign, self.staff_c, weight=30)

		self.team_b_campaign = self.make_campaign(title="Chiến dịch G18 Team B", team_scope=self.team_b)
		self.make_rule(self.team_b_campaign, self.staff_c, weight=100)

	def _assert_fast_matches_reference(self, **kwargs) -> dict:
		fast_res = p3_reporting.get_metric_drilldown(**kwargs)
		with patch.object(report_query, "_try_fast_sql_drilldown_page", return_value=None):
			ref_res = p3_reporting.get_metric_drilldown(**kwargs)
		self.assertEqual(fast_res["pagination"], ref_res["pagination"], f"Pagination mismatch for {kwargs}")
		self.assertEqual(fast_res["rows"], ref_res["rows"], f"Row parity mismatch for {kwargs}")
		return fast_res

	def test_sql_paginated_drilldown_exact_parity_across_metrics_buckets_scopes_and_roles(self):
		frappe.set_user("Administrator")
		wi_conn, lead_conn = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		wi_noans, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		wi_inv, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_b)
		wi_untried, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_b)
		wi_team_b, _lead_team_b = self.make_assigned_work_item(self.team_b_campaign, self.staff_c)

		# 1. Connected attempt with verified native call log (`staff_a`, `team_a`) -> then transfer to `staff_c` (`team_b`)
		frappe.set_user(self.staff_a)
		cl_1 = self.make_native_call_log(lead_conn, status="Completed", duration=45)
		contact_attempt.record_attempt(
			work_item=wi_conn,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=cl_1,
			note="Khách VIP G18 cần gọi lại báo giá",
			idempotency_key=self.unique_key("g18-conn"),
		)
		# Create a follow-up task on `wi_conn`
		day_floor = now_datetime().replace(hour=0, minute=0, second=1, microsecond=0)
		fu_due = max(now_datetime() - timedelta(seconds=20), day_floor)
		followup.schedule_followup(
			work_item=wi_conn,
			due_at=(now_datetime() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
			note="Hẹn gọi lại G18",
			idempotency_key=self.unique_key("g18-fu"),
		)
		# Force one FOLLOWUP obligation into overdue state at `now_datetime()` for `OPEN_FOLLOWUPS_OVERDUE` & `SLA_BREACH_COUNT`
		fu_ob_name = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_conn, "obligation_type": "FOLLOWUP"},
			"name",
		)
		if fu_ob_name:
			frappe.db.set_value(
				"CSKH SLA Obligation",
				fu_ob_name,
				{
					"effective_due_at": fu_due,
					"original_due_at": fu_due,
					"started_at": max(fu_due - timedelta(seconds=30), day_floor),
				},
				update_modified=False,
			)

		# 2. Not-connected attempt (`no_answer`) + overdue follow-up on `wi_noans` (not transferred)
		contact_attempt.record_attempt(
			work_item=wi_noans,
			channel="phone_call",
			outcome_code="no_answer",
			note="Không nghe máy lần 1",
			idempotency_key=self.unique_key("g18-noans"),
		)
		followup.schedule_followup(
			work_item=wi_noans,
			due_at=(now_datetime() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
			note="Hẹn gọi lại khách chưa nghe máy G18",
			idempotency_key=self.unique_key("g18-fu-noans"),
		)
		fu_noans_ob = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_noans, "obligation_type": "FOLLOWUP"},
			"name",
		)
		if fu_noans_ob:
			frappe.db.set_value(
				"CSKH SLA Obligation",
				fu_noans_ob,
				{
					"effective_due_at": fu_due,
					"original_due_at": fu_due,
					"started_at": max(fu_due - timedelta(seconds=30), day_floor),
				},
				update_modified=False,
			)

		# 3. Invalid phone (`wrong_number`) on `wi_inv` (`staff_b`)
		frappe.set_user(self.staff_b)
		contact_attempt.record_attempt(
			work_item=wi_inv,
			channel="phone_call",
			outcome_code="wrong_number",
			note="Sai số điện thoại",
			idempotency_key=self.unique_key("g18-inv"),
		)

		# 4. Waive SLA obligation on `wi_untried` and backdate `effective_due_at` into window so `SLA_WAIVER_COUNT` > 0
		frappe.set_user("Administrator")
		ut_ob_name = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_untried, "obligation_type": "FIRST_ATTEMPT"},
			"name",
		)
		if ut_ob_name:
			with authorized_sla_write():
				ut_doc = frappe.get_doc("CSKH SLA Obligation", ut_ob_name)
				ut_doc.status = "waived"
				ut_doc.waived_by = self.admin_user
				ut_doc.waived_at = max(now_datetime() - timedelta(seconds=5), day_floor)
				ut_doc.waive_reason = "Miễn trừ kiểm thử G18"
				ut_doc.effective_due_at = max(now_datetime() - timedelta(seconds=10), day_floor)
				ut_doc.save(ignore_permissions=True)

		# 5. `Team B` attempt (`staff_c`) + transfer `wi_conn` from `staff_a` (`Team A`) -> `staff_c` (`Team B`)
		frappe.set_user(self.staff_c)
		contact_attempt.record_attempt(
			work_item=wi_team_b,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Ghi chú riêng Team B 0988777666",
			idempotency_key=self.unique_key("g18-tb"),
		)
		frappe.set_user(self.admin_user)
		ownership.reassign(
			wi_conn,
			self.staff_a,
			self.staff_c,
			reason="Bàn giao sang Team B kiểm tra che PII",
			actor=self.admin_user,
			request_id=self.unique_key("g18-reasgn"),
		)

		# 6. Inject one `UNATTRIBUTED_TEAM_ID` (`historical_attribution_unknown`) interval + obligation
		frappe.set_user("Administrator")
		wi_unattr, _ = self.make_assigned_work_item(self.shared_campaign, self.staff_a)
		ah_unattr = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": wi_unattr, "action": "assign"},
			"name",
		)
		if ah_unattr:
			frappe.db.set_value(
				"CSKH Assignment History",
				ah_unattr,
				{"team_at_assignment": None, "attribution_status": UNKNOWN_HISTORICAL},
				update_modified=False,
			)
		ob_unattr = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_unattr, "obligation_type": "FIRST_ATTEMPT"},
			"name",
		)
		if ob_unattr:
			frappe.db.set_value(
				"CSKH SLA Obligation",
				ob_unattr,
				{
					"team_at_obligation": None,
					"attribution_status": UNKNOWN_HISTORICAL,
					"effective_due_at": max(now_datetime() - timedelta(seconds=15), day_floor),
				},
				update_modified=False,
			)
		# Mature all remaining `FIRST_ATTEMPT` obligations into current month window so SLA denominator is rich
		frappe.db.sql(
			"""
			update `tabCSKH SLA Obligation`
			set effective_due_at = %s
			where obligation_type = 'FIRST_ATTEMPT' and effective_due_at > %s
			""",
			(max(now_datetime() - timedelta(seconds=8), day_floor), now_datetime()),
		)
		frappe.db.commit()

		now_dt = now_datetime()
		m_key = f"{now_dt.year:04d}-{now_dt.month:02d}"
		d_key = now_dt.strftime("%Y-%m-%d")
		execute_kpi_recompute(resolve_period_spec("monthly", m_key), run_mode="full", actor="Administrator")
		execute_kpi_recompute(resolve_period_spec("daily", d_key), run_mode="full", actor="Administrator")

		# Verify parity across all 16 SQL-fast-path metrics & all 4 buckets as `CRM Admin`
		frappe.set_user(self.admin_user)
		for mcode in sorted(report_query._FAST_SQL_DRILLDOWN_METRICS):
			for bucket in ("all_denominator", "numerator_only", "breached_only", "excluded"):
				self._assert_fast_matches_reference(
					metric_code=mcode,
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					filter_bucket=bucket,
					page=1,
					page_size=20,
				)

		# Verify daily campaign (`den < 500` — previously bypassed fast path!) as `BOD Viewer` (`0` PII)
		frappe.set_user(self.bod_user)
		bod_res = self._assert_fast_matches_reference(
			metric_code="CONNECTED_RATE",
			period_type="daily",
			period_key=d_key,
			scope_type="campaign",
			scope_id=self.shared_campaign,
			filter_bucket="all_denominator",
			page=1,
			page_size=20,
		)
		for row in bod_res["rows"]:
			self.assertTrue(row["pii_redacted"])
			self.assertEqual(row["lead"], "REDACTED")
			self.assertIsNone(row["phone"])

		# Verify `UNATTRIBUTED_TEAM_ID` scope as `CRM Admin`
		frappe.set_user(self.admin_user)
		for mcode, bucket in (
			("ELIGIBLE_COVERAGE", "all_denominator"),
			("FIRST_ATTEMPT_SLA_RATE", "all_denominator"),
			("CONNECTED_RATE", "excluded"),
		):
			unattr_res = self._assert_fast_matches_reference(
				metric_code=mcode,
				period_type="monthly",
				period_key=m_key,
				scope_type="team",
				scope_id=UNATTRIBUTED_TEAM_ID,
				filter_bucket=bucket,
				page=1,
				page_size=20,
			)
			self.assertGreaterEqual(unattr_res["pagination"]["total"], 1)

		# Verify `Team Leader A`: exact `team-as-of` parity, transferred PII redacted, `403` on `company` & `team_b`
		frappe.set_user(self.leader_a)
		for mcode in (
			"CONNECTED_RATE",
			"FIRST_ATTEMPT_SLA_RATE",
			"CONTACT_ATTEMPT_COUNT",
			"ELIGIBLE_COVERAGE",
		):
			la_res = self._assert_fast_matches_reference(
				metric_code=mcode,
				period_type="monthly",
				period_key=m_key,
				scope_type="team",
				scope_id=self.team_a,
				page=1,
				page_size=20,
			)
			for r in la_res["rows"]:
				self.assertEqual(r["team_id"], self.team_a)
				if r["work_item"] == wi_conn:
					self.assertTrue(r["pii_redacted"])
					self.assertTrue(r["pii_redacted_due_to_transfer"])
					self.assertIsNone(r["phone"])

		# Anti-oracle search check on transferred lead
		la_search = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=m_key,
			scope_type="team",
			scope_id=self.team_a,
			search="VIP G18",
		)
		self.assertEqual(la_search["pagination"]["total"], 0)

		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_metric_drilldown(
				metric_code="CONNECTED_RATE",
				period_type="monthly",
				period_key=m_key,
				scope_type="company",
			)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_metric_drilldown(
				metric_code="CONNECTED_RATE",
				period_type="monthly",
				period_key=m_key,
				scope_type="team",
				scope_id=self.team_b,
			)

		# Verify `Staff A`: own slice parity + `403` on company and other staff
		frappe.set_user(self.staff_a)
		for mcode in ("CONNECTED_RATE", "FIRST_ATTEMPT_SLA_RATE", "CONTACT_ATTEMPT_COUNT"):
			self._assert_fast_matches_reference(
				metric_code=mcode,
				period_type="monthly",
				period_key=m_key,
				scope_type="employee",
				scope_id=self.staff_a,
				page=1,
				page_size=20,
			)
		with self.assertRaises(ForbiddenScope):
			p3_reporting.get_metric_drilldown(
				metric_code="CONNECTED_RATE",
				period_type="monthly",
				period_key=m_key,
				scope_type="employee",
				scope_id=self.staff_b,
			)

	def test_deep_pagination_dirty_pending_correction_fallback_and_query_ceiling(self):
		"""Verify `offset > 500` (`page=26..29`), `dirty_pending=True`, `Team Leader` SQL query ceiling (`<=55`), and correction fallback."""
		frappe.set_user("Administrator")
		base_dt = datetime(2026, 9, 10, 9, 0, 0)
		m_key = "2026-09"
		orig_tmh = frappe.db.sql(
			"select name, effective_from from `tabCSKH Team Membership History`",
			as_dict=True,
		)
		frappe.db.sql("update `tabCSKH Team Membership History` set effective_from = '2026-01-01 00:00:00'")
		frappe.db.commit()

		try:
			# Bulk-insert 565 distinct Leads + Work Items + Assignment History + SLA Obligations + Contact Attempts (`29` pages of 20)
			now_s = now_datetime()
			lead_cols = [
				"name",
				"creation",
				"modified",
				"modified_by",
				"owner",
				"docstatus",
				"idx",
				"naming_series",
				"first_name",
				"lead_name",
				"mobile_no",
				"status",
				"lead_owner",
				"cskh_do_not_contact",
			]
			wi_cols = [
				"name",
				"creation",
				"modified",
				"modified_by",
				"owner",
				"docstatus",
				"idx",
				"lead",
				"campaign",
				"status",
				"assigned_to",
				"assigned_at",
				"owner_at_close",
				"closed_by",
				"closed_at",
				"attempt_count",
				"connected_attempt_count",
				"first_attempt_at",
				"last_attempt_at",
				"last_outcome_code",
				"last_outcome_label",
				"revision",
			]
			ah_cols = [
				"name",
				"creation",
				"modified",
				"modified_by",
				"owner",
				"docstatus",
				"idx",
				"work_item",
				"action",
				"from_user",
				"to_user",
				"actor",
				"event_at",
				"ownership_start",
				"ownership_end",
				"event_id",
				"team_at_assignment",
				"attribution_status",
				"reason",
			]
			sla_cols = [
				"name",
				"creation",
				"modified",
				"modified_by",
				"owner",
				"docstatus",
				"idx",
				"obligation_type",
				"work_item",
				"lead",
				"campaign",
				"interval_event",
				"owner_user",
				"team_at_obligation",
				"attribution_status",
				"status",
				"informational_only",
				"is_recovery_obligation",
				"reschedule_seq",
				"started_at",
				"original_due_at",
				"effective_due_at",
				"is_paused",
				"fulfilled_at",
				"fulfilled_by_attempt",
				"breached_at",
				"closed_at",
				"evaluation_anchor_at",
				"policy_name_snapshot",
				"policy_version_snapshot",
				"idempotency_key",
			]
			att_cols = [
				"name",
				"creation",
				"modified",
				"modified_by",
				"owner",
				"docstatus",
				"idx",
				"work_item",
				"lead",
				"campaign",
				"ownership_event",
				"owner_at_attempt",
				"performed_by",
				"occurred_at",
				"recorded_at",
				"channel",
				"outcome_code",
				"outcome_label_snapshot",
				"outcome_group_snapshot",
				"outcome_version_snapshot",
				"contact_attempt_snapshot",
				"connected_snapshot",
				"interested_snapshot",
				"terminal_snapshot",
				"dnc_snapshot",
				"note",
				"verification_status",
				"policy_name_snapshot",
				"policy_version_snapshot",
				"idempotency_key",
				"payload_hash",
				"work_item_revision",
			]

			lead_rows = []
			wi_rows = []
			ah_rows = []
			ob_rows = []
			ca_rows = []
			for i in range(565):
				lead_id = f"LEAD-G18-DEEP-{i:04d}"
				wi_id = f"WI-G18-DEEP-{i:04d}"
				ah_id = f"AH-G18-DEEP-{i:04d}"
				ob_id = f"OB-G18-DEEP-{i:04d}"
				ca_id = f"CA-G18-DEEP-{i:04d}"
				iv_start = base_dt + timedelta(minutes=i)
				att_dt = iv_start + timedelta(minutes=5)
				close_dt = att_dt + timedelta(minutes=1)
				due_dt = iv_start + timedelta(minutes=(15 if i % 3 != 0 else 2))
				is_conn = 1 if (i % 2 == 0) else 0
				ocode = "connected_interested" if is_conn else "no_answer"
				olabel = "Đã trao đổi — Có quan tâm" if is_conn else "Không nghe máy"
				ogroup = "positive" if is_conn else "unreached"
				on_time = att_dt <= due_dt

				lead_rows.append(
					(
						lead_id,
						now_s,
						now_s,
						"Administrator",
						"Administrator",
						0,
						0,
						"CRM-LEAD-",
						f"Khách Deep {i:04d}",
						f"Khách Deep {i:04d}",
						f"0978{i:06d}",
						"Converted",
						None,
						0,
					)
				)
				wi_rows.append(
					(
						wi_id,
						now_s,
						now_s,
						"Administrator",
						"Administrator",
						0,
						0,
						lead_id,
						self.shared_campaign,
						"closed",
						None,
						iv_start,
						self.staff_a,
						self.staff_a,
						close_dt,
						1,
						is_conn,
						att_dt,
						att_dt,
						ocode,
						olabel,
						2,
					)
				)
				ah_rows.append(
					(
						ah_id,
						now_s,
						now_s,
						"Administrator",
						"Administrator",
						0,
						0,
						wi_id,
						"assign",
						None,
						self.staff_a,
						"Administrator",
						iv_start,
						iv_start,
						close_dt,
						f"ev-g18-{i:04d}",
						self.team_a,
						AUTHORITATIVE,
						"Phân bổ G18 Deep",
					)
				)
				ob_rows.append(
					(
						ob_id,
						now_s,
						now_s,
						"Administrator",
						"Administrator",
						0,
						0,
						"FIRST_ATTEMPT",
						wi_id,
						lead_id,
						self.shared_campaign,
						ah_id,
						self.staff_a,
						self.team_a,
						AUTHORITATIVE,
						"fulfilled_on_time" if on_time else "fulfilled_late",
						0,
						0,
						0,
						iv_start,
						due_dt,
						due_dt,
						0,
						att_dt,
						ca_id,
						None if on_time else due_dt,
						close_dt,
						iv_start,
						"GLOBAL-DEFAULT",
						1,
						f"idem-ob-g18-{i:04d}",
					)
				)
				ca_rows.append(
					(
						ca_id,
						now_s,
						now_s,
						self.staff_a,
						self.staff_a,
						0,
						0,
						wi_id,
						lead_id,
						self.shared_campaign,
						ah_id,
						self.staff_a,
						self.staff_a,
						att_dt,
						att_dt,
						"phone_call",
						ocode,
						olabel,
						ogroup,
						1,
						1,
						is_conn,
						is_conn,
						0,
						0,
						f"Note G18 {i:04d}",
						"self_reported",
						"GLOBAL-DEFAULT",
						1,
						f"idem-ca-g18-{i:04d}",
						f"hash-ca-g18-{i:04d}",
						2,
					)
				)

			frappe.db.bulk_insert("CRM Lead", fields=lead_cols, values=lead_rows)
			frappe.db.bulk_insert("CSKH Work Item", fields=wi_cols, values=wi_rows)
			frappe.db.bulk_insert("CSKH Assignment History", fields=ah_cols, values=ah_rows)
			frappe.db.bulk_insert("CSKH SLA Obligation", fields=sla_cols, values=ob_rows)
			frappe.db.bulk_insert("CSKH Contact Attempt", fields=att_cols, values=ca_rows)
			frappe.db.commit()

			execute_kpi_recompute(
				resolve_period_spec("monthly", m_key), run_mode="full", actor="Administrator"
			)

			# 1. Deep pagination past `offset=500` (`page=25`, `page=26`, `page=29`) across all 3 families
			frappe.set_user(self.bod_user)
			for mcode in (
				"CONNECTED_RATE",
				"FIRST_ATTEMPT_SLA_RATE",
				"CONTACT_ATTEMPT_COUNT",
				"ELIGIBLE_COVERAGE",
			):
				seen_ids: set[str] = set()
				for pg in (1, 25, 26, 29):
					res = self._assert_fast_matches_reference(
						metric_code=mcode,
						period_type="monthly",
						period_key=m_key,
						scope_type="company",
						page=pg,
						page_size=20,
					)
					self.assertEqual(res["pagination"]["total"], 565)
					page_ids = {r["record_id"] for r in res["rows"]}
					self.assertFalse(seen_ids & page_ids)
					seen_ids |= page_ids
				self.assertEqual(len(seen_ids), 20 * 3 + 5)

			# 2. `Team Leader` SQL query ceiling check (`<= 55` queries for a 20-row page instead of `377`!)
			frappe.set_user(self.leader_a)
			sql_counter = [0]
			orig_sql = frappe.db.sql

			def _counting_sql(query, *a, **kw):
				sql_counter[0] += 1
				return orig_sql(query, *a, **kw)

			with patch.object(frappe.db, "sql", side_effect=_counting_sql):
				tl_res = p3_reporting.get_metric_drilldown(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="team",
					scope_id=self.team_a,
					page=27,
					page_size=20,
				)
			self.assertEqual(tl_res["pagination"]["total"], 565)
			self.assertEqual(len(tl_res["rows"]), 20)
			self.assertLessEqual(
				sql_counter[0], 55, f"Expected <=55 SQL queries for Team Leader page, got {sql_counter[0]}"
			)
			with patch.object(report_query, "_try_fast_sql_drilldown_page", return_value=None):
				tl_ref = p3_reporting.get_metric_drilldown(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="team",
					scope_id=self.team_a,
					page=27,
					page_size=20,
				)
			self.assertEqual(tl_res["rows"], tl_ref["rows"])

			# 3. `dirty_pending = True` parity (no fallback cliff!)
			frappe.set_user("Administrator")
			prev_suppress = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
			frappe.flags.cskh_suppress_dirty_enqueue = True
			try:
				schedule_post_commit_dirty_invalidation(
					work_item="WI-G18-DEEP-0000",
					campaign=self.shared_campaign,
					owner_user=self.staff_a,
					event_dt=base_dt,
					reason="g18_dirty_test",
					trigger_reference="WI-G18-DEEP-0000",
				)
				_flush_pending_dirty_events()
				frappe.db.commit()
			finally:
				frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress

			frappe.set_user(self.bod_user)
			for mcode in ("CONNECTED_RATE", "FIRST_ATTEMPT_SLA_RATE", "CONTACT_ATTEMPT_COUNT"):
				dirty_res = self._assert_fast_matches_reference(
					metric_code=mcode,
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=26,
					page_size=20,
				)
				self.assertTrue(dirty_res["freshness"]["dirty_pending"])
				self.assertEqual(dirty_res["pagination"]["total"], 565)

			# 4. Approved `CSKH Attempt Correction` (`void`) falls back cleanly and preserves exact parity
			frappe.set_user(self.admin_user)
			corr_req = correction.request_correction(
				attempt="CA-G18-DEEP-0001",
				action="void",
				reason="Ghi nhận nhầm cuộc gọi kiểm thử G18",
				auto_approve=False,
				idempotency_key=self.unique_key("g18-void-req"),
				actor=self.admin_user,
			)
			correction.review_correction(
				correction_id=corr_req["correction_id"],
				decision="approve",
				review_note="Duyệt hủy bản ghi kiểm thử G18",
				idempotency_key=self.unique_key("g18-void-rev"),
				actor=self.admin_user,
			)
			frappe.set_user("Administrator")
			execute_kpi_recompute(
				resolve_period_spec("monthly", m_key), run_mode="restated", actor="Administrator"
			)
			frappe.set_user(self.bod_user)
			void_res = self._assert_fast_matches_reference(
				metric_code="CONTACT_ATTEMPT_COUNT",
				period_type="monthly",
				period_key=m_key,
				scope_type="company",
				page=1,
				page_size=20,
			)
			self.assertEqual(void_res["pagination"]["total"], 564)
		finally:
			frappe.set_user("Administrator")
			for row in orig_tmh:
				frappe.db.set_value(
					"CSKH Team Membership History",
					row["name"],
					"effective_from",
					row["effective_from"],
					update_modified=False,
				)
			frappe.db.sql("delete from `tabCRM Lead` where name like 'LEAD-G18-DEEP-%'")
			frappe.db.commit()

	def test_additive_g18_indexes_explain_idempotent_migrate_and_n_minus_1_rollback(self):
		"""Verify G18 additive indexes exist, eliminate `type=ALL` / `Using filesort`, survive `migrate x2`, and allow clean `N -> N-1` rollback."""
		frappe.set_user("Administrator")
		ensure_p3_indexes()
		ensure_p3_indexes()  # Idempotent second execution (`migrate x2`)

		sla_idx = {
			r.Key_name for r in frappe.db.sql("show index from `tabCSKH SLA Obligation`", as_dict=True)
		}
		att_idx = {
			r.Key_name for r in frappe.db.sql("show index from `tabCSKH Contact Attempt`", as_dict=True)
		}
		for expected_sla_idx in (
			"idx_cskh_sla_type_due_name",
			"idx_cskh_sla_due_name",
			"idx_cskh_sla_ivevt_type",
		):
			self.assertIn(expected_sla_idx, sla_idx)
		self.assertIn("idx_cskh_attempt_occ_name", att_idx)

		# EXPLAIN checks: verify `key` selection and `0` `Using filesort` on pagination queries
		exp_sla = frappe.db.sql(
			"""
			explain select name, effective_due_at, status
			from `tabCSKH SLA Obligation` USE INDEX (idx_cskh_sla_type_due_name)
			where obligation_type = 'FIRST_ATTEMPT'
			  and effective_due_at >= '2026-10-01 00:00:00'
			  and effective_due_at < '2026-11-01 00:00:00'
			  and status != 'superseded'
			order by effective_due_at asc, name asc
			limit 20 offset 580
			""",
			as_dict=True,
		)
		self.assertEqual(exp_sla[0]["key"], "idx_cskh_sla_type_due_name")
		self.assertNotIn("Using filesort", str(exp_sla[0].get("Extra") or ""))

		exp_att = frappe.db.sql(
			"""
			explain select name, occurred_at
			from `tabCSKH Contact Attempt` USE INDEX (idx_cskh_attempt_occ_name)
			where occurred_at >= '2026-10-01 00:00:00'
			  and occurred_at < '2026-11-01 00:00:00'
			  and recorded_at <= '2026-11-01 00:00:00'
			  and coalesce(contact_attempt_snapshot, 0) = 1
			order by occurred_at asc, name asc
			limit 20 offset 580
			""",
			as_dict=True,
		)
		self.assertEqual(exp_att[0]["key"], "idx_cskh_attempt_occ_name")
		self.assertNotIn("Using filesort", str(exp_att[0].get("Extra") or ""))

		exp_iv = frappe.db.sql(
			"""
			explain select ah.name
			from `tabCSKH Assignment History` ah FORCE INDEX (idx_cskh_ah_start_name)
			inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
			where ah.ownership_start >= '2026-10-01 00:00:00'
			  and ah.ownership_start < '2026-11-01 00:00:00'
			  and ah.action in ('assign', 'reassign', 'reopen')
			  and coalesce((
			      select 1 from `tabCSKH Contact Attempt` ca FORCE INDEX (idx_cskh_attempt_ownevt_occ)
			      where ca.ownership_event = ah.name
			        and coalesce(ca.contact_attempt_snapshot, 0) = 1
			      limit 1
			  ), 0) = 1
			order by ah.ownership_start asc, ah.name asc
			limit 20 offset 580
			""",
			as_dict=True,
		)
		for row in exp_iv:
			self.assertNotIn("<subquery", str(row.get("table") or ""))
			self.assertNotIn("Using filesort", str(row.get("Extra") or ""))

		# Explicit N -> N-1 rollback proof + forward re-apply
		frappe.db.sql_ddl("alter table `tabCSKH SLA Obligation` drop index `idx_cskh_sla_type_due_name`")
		frappe.db.sql_ddl("alter table `tabCSKH SLA Obligation` drop index `idx_cskh_sla_due_name`")
		frappe.db.sql_ddl("alter table `tabCSKH SLA Obligation` drop index `idx_cskh_sla_ivevt_type`")
		frappe.db.sql_ddl("alter table `tabCSKH Contact Attempt` drop index `idx_cskh_attempt_occ_name`")

		# N-1 style query (without index hints) runs cleanly after rollback DDL
		cnt = int(
			frappe.db.sql(
				"select count(*) from `tabCSKH SLA Obligation` where obligation_type = 'FIRST_ATTEMPT'"
			)[0][0]
			or 0
		)
		self.assertGreaterEqual(cnt, 0)

		# Re-apply `ensure_p3_indexes()` (`N-1 -> N` forward re-migration)
		ensure_p3_indexes()
		sla_idx_after = {
			r.Key_name for r in frappe.db.sql("show index from `tabCSKH SLA Obligation`", as_dict=True)
		}
		self.assertIn("idx_cskh_sla_type_due_name", sla_idx_after)
