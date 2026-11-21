"""Regression, exact parity, deep-pagination, N+1 ceiling, EXPLAIN & N->N-1 index rollback tests for G18 (`get_metric_drilldown`)."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.utils import get_datetime, now_datetime

from crm_cskh.api import p3_reporting
from crm_cskh.p3_models import authorized_sla_write, ensure_p3_indexes
from crm_cskh.services import contact_attempt, correction, followup, ownership, report_query, runtime_cache
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL
from crm_cskh.services.errors import ForbiddenScope
from crm_cskh.services.kpi_calculator import COMPANY_SCOPE_ID, UNATTRIBUTED_TEAM_ID
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import (
	_flush_pending_dirty_events,
	execute_kpi_recompute,
	process_dirty_scopes,
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

			# A real source mutation must bypass the stale published total while preserving exact fast/reference parity.
			frappe.set_user("Administrator")
			frappe.db.sql(
				"update `tabCSKH Contact Attempt` set contact_attempt_snapshot = 0 where name = %s",
				("CA-G18-DEEP-0000",),
			)
			frappe.db.commit()
			prev_suppress = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
			frappe.flags.cskh_suppress_dirty_enqueue = True
			try:
				schedule_post_commit_dirty_invalidation(
					work_item="WI-G18-DEEP-0000",
					campaign=self.shared_campaign,
					owner_user=self.staff_a,
					event_dt=base_dt,
					reason="g18_attempt_fixture_mutation",
					trigger_reference="WI-G18-DEEP-0000",
				)
				_flush_pending_dirty_events()
				frappe.db.commit()
			finally:
				frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress
			frappe.set_user(self.bod_user)
			dirty_after_mutation = self._assert_fast_matches_reference(
				metric_code="CONNECTED_RATE",
				period_type="monthly",
				period_key=m_key,
				scope_type="company",
				page=1,
				page_size=20,
			)
			self.assertTrue(dirty_after_mutation["freshness"]["dirty_pending"])
			self.assertEqual(dirty_after_mutation["pagination"]["total"], 564)
			self.assertNotIn(
				"AH-G18-DEEP-0000",
				{row["record_id"] for row in dirty_after_mutation["rows"]},
			)

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
			# The dirty-source mutation removed one attempt, and this approved void removes a second.
			self.assertEqual(void_res["pagination"]["total"], 563)
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
		self.assertIn("idx_cskh_attempt_ownevt_cover", att_idx)
		cover_index_rows = frappe.db.sql(
			"show index from `tabCSKH Contact Attempt` where Key_name = %s",
			("idx_cskh_attempt_ownevt_cover",),
			as_dict=True,
		)
		cover_index_columns = [
			r.Column_name for r in sorted(cover_index_rows, key=lambda r: int(r.Seq_in_index))
		]
		self.assertEqual(
			cover_index_columns,
			["ownership_event", "occurred_at", "recorded_at", "contact_attempt_snapshot"],
		)

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

		exp_att_cover = frappe.db.sql(
			"""
			explain select 1
			from `tabCSKH Contact Attempt` ca FORCE INDEX (idx_cskh_attempt_ownevt_cover)
			where ca.ownership_event = 'AH-G18-DEEP-0000'
			  and coalesce(ca.contact_attempt_snapshot, 0) = 1
			  and ca.recorded_at <= '2026-11-01 00:00:00'
			  and ca.occurred_at < '2026-11-01 00:00:00'
			limit 1
			""",
			as_dict=True,
		)
		self.assertEqual(exp_att_cover[0]["key"], "idx_cskh_attempt_ownevt_cover")
		self.assertIn("Using index", str(exp_att_cover[0].get("Extra") or ""))

		exp_iv = frappe.db.sql(
			"""
			explain select ah.name
			from `tabCSKH Assignment History` ah FORCE INDEX (idx_cskh_ah_start_name)
			inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
			where ah.ownership_start >= '2026-10-01 00:00:00'
			  and ah.ownership_start < '2026-11-01 00:00:00'
			  and ah.action in ('assign', 'reassign', 'reopen')
			  and coalesce((
			      select 1 from `tabCSKH Contact Attempt` ca FORCE INDEX (idx_cskh_attempt_ownevt_cover)
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
		frappe.db.sql_ddl("alter table `tabCSKH Contact Attempt` drop index `idx_cskh_attempt_ownevt_cover`")

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
		att_idx_after = {
			r.Key_name for r in frappe.db.sql("show index from `tabCSKH Contact Attempt`", as_dict=True)
		}
		self.assertIn("idx_cskh_sla_type_due_name", sla_idx_after)
		self.assertIn("idx_cskh_attempt_ownevt_cover", att_idx_after)

	def test_dirty_pending_and_concurrent_worker_interval_count_plan_and_invariants(self):
		"""F-G18-AB-CONFIGB-AND-DIRTY-COUNT-01: verify index-only EXPLAIN on `_count_intervals`, deferred deep pagination, multi-bucket/scope/RBAC parity across fresh/dirty/invalid-phone/canceled-SLA/correction states, `process_dirty_scopes` convergence, and `N -> N-1` fallback."""
		frappe.set_user("Administrator")
		ensure_p3_indexes()

		expected_new_indexes = (
			("CSKH Assignment History", "idx_cskh_ah_start_cover"),
			("CSKH Assignment History", "idx_cskh_ah_team_start_cover"),
			("CSKH Assignment History", "idx_cskh_ah_user_start_cover"),
			("CSKH Work Item", "idx_cskh_wi_name_camp"),
			("CSKH Contact Attempt", "idx_cskh_attempt_iv_flags_cover"),
			("CSKH Contact Attempt", "idx_cskh_attempt_inv_occ"),
			("CSKH Contact Attempt", "idx_cskh_attempt_ocode_occ"),
			("CSKH SLA Obligation", "idx_cskh_sla_ivevt_cover"),
		)
		for dt_name, idx_name in expected_new_indexes:
			rows = frappe.db.sql(f"show index from `tab{dt_name}` where Key_name = %s", (idx_name,))
			self.assertTrue(rows, f"Expected additive index {idx_name} on {dt_name}")

		base_dt = datetime(2026, 8, 12, 9, 0, 0)
		m_key = "2026-08"
		orig_tmh = frappe.db.sql(
			"select name, effective_from from `tabCSKH Team Membership History`",
			as_dict=True,
		)
		frappe.db.sql("update `tabCSKH Team Membership History` set effective_from = '2026-01-01 00:00:00'")
		frappe.db.commit()

		try:
			now_s = now_datetime()
			for i in range(60):
				lead_id = f"LEAD-G18-DEEP-DC-{i:04d}"
				wi_id = f"WI-G18-DC-{i:04d}"
				ah_id = f"AH-G18-DC-{i:04d}"
				sla_id = f"SLA-G18-DC-{i:04d}"
				is_team_a = i < 40
				owner_u = (
					self.staff_a
					if (i % 2 == 0 and is_team_a)
					else (self.staff_b if is_team_a else self.staff_c)
				)
				team_u = self.team_a if is_team_a else self.team_b
				camp_u = self.shared_campaign if (i % 3 != 0) else self.team_b_campaign
				iv_ts = base_dt + timedelta(minutes=i * 5)
				due_ts = iv_ts + timedelta(minutes=30)

				frappe.db.sql(
					"""
					insert into `tabCRM Lead`
					(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
					 `naming_series`, `first_name`, `lead_name`, `mobile_no`, `status`, `lead_owner`, `cskh_do_not_contact`)
					values (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
					        'CRM-LEAD-', %s, %s, %s, 'Contacted', %s, 0)
					""",
					(
						lead_id,
						now_s,
						now_s,
						f"Ứng viên DC {i:04d}",
						f"Ứng viên DC {i:04d}",
						f"097788{i:04d}",
						owner_u,
					),
				)

				has_attempt = i < 54  # 6 untried intervals (54..59)
				is_conn = i < 36  # 36 connected (0..35), 18 not connected (36..53)
				att_ts = iv_ts + timedelta(minutes=10)
				ocode = "connected_interested" if is_conn else "no_answer"
				olabel = "Đã trao đổi — Có quan tâm" if is_conn else "Không nghe máy"
				ogroup = "positive" if is_conn else "unreached"

				frappe.db.sql(
					"""
					insert into `tabCSKH Work Item`
					(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
					 `lead`, `campaign`, `status`, `assigned_to`, `assigned_at`,
					 `attempt_count`, `connected_attempt_count`, `first_attempt_at`, `last_attempt_at`,
					 `last_outcome_code`, `last_outcome_label`, `revision`)
					values (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
					        %s, %s, 'in_progress', %s, %s, %s, %s, %s, %s, %s, %s, 1)
					""",
					(
						wi_id,
						iv_ts,
						att_ts if has_attempt else iv_ts,
						lead_id,
						camp_u,
						owner_u,
						iv_ts,
						1 if has_attempt else 0,
						1 if is_conn else 0,
						att_ts if has_attempt else None,
						att_ts if has_attempt else None,
						ocode if has_attempt else None,
						olabel if has_attempt else None,
					),
				)
				frappe.db.sql(
					"""
					insert into `tabCSKH Assignment History`
					(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
					 `work_item`, `action`, `from_user`, `to_user`, `actor`, `event_at`,
					 `ownership_start`, `ownership_end`, `event_id`, `team_at_assignment`, `attribution_status`, `reason`)
					values (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
					        %s, 'assign', null, %s, 'Administrator', %s, %s, null, %s, %s, %s, 'Seed DC')
					""",
					(
						ah_id,
						iv_ts,
						iv_ts,
						wi_id,
						owner_u,
						iv_ts,
						iv_ts,
						f"evt-dc-{i:04d}",
						team_u,
						AUTHORITATIVE,
					),
				)

				sla_status = (
					"fulfilled_on_time"
					if has_attempt
					else ("canceled_validly" if i == 59 else "breached_open")
				)
				frappe.db.sql(
					"""
					insert into `tabCSKH SLA Obligation`
					(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
					 `obligation_type`, `work_item`, `lead`, `campaign`, `interval_event`, `owner_user`,
					 `team_at_obligation`, `attribution_status`, `status`, `informational_only`,
					 `is_recovery_obligation`, `reschedule_seq`, `started_at`, `original_due_at`, `effective_due_at`,
					 `is_paused`, `fulfilled_at`, `fulfilled_by_attempt`, `breached_at`, `closed_at`,
					 `evaluation_anchor_at`, `policy_name_snapshot`, `policy_version_snapshot`, `idempotency_key`)
					values (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
					        'FIRST_ATTEMPT', %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0,
					        %s, %s, %s, 0, %s, %s, %s, %s, %s, 'GLOBAL-DEFAULT', 1, %s)
					""",
					(
						sla_id,
						iv_ts,
						att_ts if has_attempt else due_ts,
						wi_id,
						lead_id,
						camp_u,
						ah_id,
						owner_u,
						team_u,
						AUTHORITATIVE,
						sla_status,
						iv_ts,
						due_ts,
						due_ts,
						att_ts if has_attempt else None,
						f"CA-G18-DC-{i:04d}" if has_attempt else None,
						None if has_attempt or i == 59 else due_ts,
						due_ts if i == 59 else None,
						att_ts if has_attempt else due_ts,
						f"sla-dc-{i:04d}",
					),
				)
				if has_attempt:
					frappe.db.sql(
						"""
						insert into `tabCSKH Contact Attempt`
						(`name`, `creation`, `modified`, `modified_by`, `owner`, `docstatus`, `idx`,
						 `work_item`, `lead`, `campaign`, `ownership_event`, `owner_at_attempt`, `performed_by`,
						 `occurred_at`, `recorded_at`, `channel`, `outcome_code`, `outcome_label_snapshot`,
						 `outcome_group_snapshot`, `outcome_version_snapshot`, `contact_attempt_snapshot`,
						 `connected_snapshot`, `interested_snapshot`, `terminal_snapshot`, `dnc_snapshot`,
						 `invalid_phone_snapshot`, `note`, `verification_status`,
						 `policy_name_snapshot`, `policy_version_snapshot`, `idempotency_key`, `payload_hash`, `work_item_revision`)
						values (%s, %s, %s, %s, %s, 0, 0,
						        %s, %s, %s, %s, %s, %s, %s, %s, 'phone_call', %s, %s, %s, 1, 1,
						        %s, %s, 0, 0, 0, %s, 'self_reported', 'GLOBAL-DEFAULT', 1, %s, %s, 1)
						""",
						(
							f"CA-G18-DC-{i:04d}",
							att_ts,
							att_ts,
							owner_u,
							owner_u,
							wi_id,
							lead_id,
							camp_u,
							ah_id,
							owner_u,
							owner_u,
							att_ts,
							att_ts,
							ocode,
							olabel,
							ogroup,
							1 if is_conn else 0,
							1 if is_conn else 0,
							f"Ghi chú DC {i:04d}",
							f"idem-dc-{i:04d}",
							f"hash-dc-{i:04d}",
						),
					)
			frappe.db.commit()

			execute_kpi_recompute(
				resolve_period_spec("monthly", m_key), run_mode="full", actor="Administrator"
			)

			with runtime_cache.enable_runtime_cache_for_test():
				# 1. Fresh state (`dirty_pending=False`) parity across pages 1..3
				frappe.set_user(self.bod_user)
				fresh_p1 = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=1,
					page_size=20,
				)
				self.assertFalse(fresh_p1["freshness"]["dirty_pending"])
				self.assertEqual(fresh_p1["pagination"]["total"], 54)

				# 2. Mark dirty (`dirty_pending=True`, no invalid-phone rows yet) and inspect `_count_intervals` EXPLAIN + deferred page=3 SQL
				frappe.set_user("Administrator")
				prev_sup = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
				frappe.flags.cskh_suppress_dirty_enqueue = True
				try:
					schedule_post_commit_dirty_invalidation(
						work_item="WI-G18-DC-0000",
						campaign=self.shared_campaign,
						owner_user=self.staff_a,
						event_dt=base_dt,
						reason="fg18_dirty_count_plan_check",
						trigger_reference="WI-G18-DC-0000",
					)
					_flush_pending_dirty_events()
					frappe.db.commit()
				finally:
					frappe.flags.cskh_suppress_dirty_enqueue = prev_sup

				captured_sql: list[tuple[str, tuple]] = []
				orig_sql = frappe.db.sql

				def _capture_sql(query, values=(), *args, **kwargs):
					captured_sql.append(
						(str(query).strip(), tuple(values) if isinstance(values, list | tuple) else values)
					)
					return orig_sql(query, values, *args, **kwargs)

				frappe.set_user(self.bod_user)
				frappe.db.sql = _capture_sql
				try:
					dirty_p3 = p3_reporting.get_metric_drilldown(
						metric_code="CONNECTED_RATE",
						period_type="monthly",
						period_key=m_key,
						scope_type="company",
						page=3,
						page_size=20,
					)
				finally:
					frappe.db.sql = orig_sql

				self.assertTrue(dirty_p3["freshness"]["dirty_pending"])
				self.assertEqual(dirty_p3["pagination"]["total"], 54)
				self.assertEqual(len(dirty_p3["rows"]), 14)

				count_queries = [
					(q, v)
					for q, v in captured_sql
					if "select count(*)" in q.lower() and "tabcskh assignment history" in q.lower()
				]
				self.assertEqual(len(count_queries), 1)
				cnt_q, cnt_v = count_queries[0]
				self.assertNotIn("tabcskh work item", cnt_q.lower())
				exp_cnt = orig_sql("EXPLAIN " + cnt_q, cnt_v, as_dict=True)
				ah_exp = next(r for r in exp_cnt if r.get("table") == "ah")
				ca_exp = next(r for r in exp_cnt if r.get("table") == "ca")
				self.assertEqual(ah_exp["key"], "idx_cskh_ah_start_cover")
				self.assertIn("Using index", str(ah_exp.get("Extra") or ""))
				self.assertEqual(ca_exp["key"], "idx_cskh_attempt_iv_flags_cover")
				self.assertIn("Using index", str(ca_exp.get("Extra") or ""))

				# Deferred join check on `offset > 0` (`page=3`)
				deferred_queries = [(q, v) for q, v in captured_sql if "page_iv" in q.lower()]
				self.assertEqual(len(deferred_queries), 1)
				exp_def = orig_sql("EXPLAIN " + deferred_queries[0][0], deferred_queries[0][1], as_dict=True)
				for er in exp_def:
					if er.get("select_type") == "DERIVED" and er.get("table") == "ah":
						self.assertEqual(er["key"], "idx_cskh_ah_start_cover")
						self.assertIn("Using index", str(er.get("Extra") or ""))
						self.assertNotIn("Using filesort", str(er.get("Extra") or ""))

				# 3. Mutate attempt `CA-G18-DC-0002` into `wrong_number` (`invalid_phone`) while `dirty_pending=True`
				#    and verify immediate live freshness (`54 -> 53` denominator, `36 -> 35` numerator, `6 -> 7` excluded)
				frappe.set_user("Administrator")
				frappe.db.sql(
					"""
					update `tabCSKH Contact Attempt`
					set outcome_code = 'wrong_number', connected_snapshot = 0, invalid_phone_snapshot = 1
					where name = 'CA-G18-DC-0002'
					"""
				)
				frappe.db.commit()

				frappe.set_user(self.admin_user)
				for mcode in ("CONNECTED_RATE", "ELIGIBLE_COVERAGE", "FIRST_ATTEMPT_SLA_RATE"):
					for bucket in ("all_denominator", "numerator_only", "breached_only", "excluded"):
						res_b = self._assert_fast_matches_reference(
							metric_code=mcode,
							period_type="monthly",
							period_key=m_key,
							scope_type="company",
							filter_bucket=bucket,
							page=1,
							page_size=20,
						)
						if mcode == "CONNECTED_RATE" and bucket == "all_denominator":
							self.assertEqual(res_b["pagination"]["total"], 53)
						elif mcode == "CONNECTED_RATE" and bucket == "numerator_only":
							self.assertEqual(res_b["pagination"]["total"], 35)
						elif mcode == "CONNECTED_RATE" and bucket == "breached_only":
							self.assertEqual(res_b["pagination"]["total"], 18)
						elif mcode == "CONNECTED_RATE" and bucket == "excluded":
							self.assertEqual(res_b["pagination"]["total"], 7)
						elif mcode == "ELIGIBLE_COVERAGE" and bucket == "all_denominator":
							self.assertEqual(res_b["pagination"]["total"], 59)
						elif mcode == "ELIGIBLE_COVERAGE" and bucket == "excluded":
							self.assertEqual(res_b["pagination"]["total"], 1)

				# Check pagination disjointness & total consistency across all pages (`page=1..3`) while dirty
				p1_dirty = p3_reporting.get_metric_drilldown(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=1,
					page_size=20,
				)
				p2_dirty = p3_reporting.get_metric_drilldown(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=2,
					page_size=20,
				)
				p3_dirty = p3_reporting.get_metric_drilldown(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=3,
					page_size=20,
				)
				all_ids_dirty = (
					[r["record_id"] for r in p1_dirty["rows"]]
					+ [r["record_id"] for r in p2_dirty["rows"]]
					+ [r["record_id"] for r in p3_dirty["rows"]]
				)
				self.assertEqual(len(all_ids_dirty), 53)
				self.assertEqual(len(set(all_ids_dirty)), 53)

				# Check Team Leader (`team_a`), Campaign (`shared_campaign`), and Staff (`staff_a`) scopes while dirty
				frappe.set_user(self.leader_a)
				for bucket in ("all_denominator", "numerator_only", "breached_only", "excluded"):
					self._assert_fast_matches_reference(
						metric_code="CONNECTED_RATE",
						period_type="monthly",
						period_key=m_key,
						scope_type="team",
						scope_id=self.team_a,
						filter_bucket=bucket,
						page=1,
						page_size=20,
					)
				self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="campaign",
					scope_id=self.shared_campaign,
					page=1,
					page_size=20,
				)
				frappe.set_user(self.staff_a)
				self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="employee",
					scope_id=self.staff_a,
					page=1,
					page_size=20,
				)

				# 4. Drop the 8 new additive indexes (`N -> N-1` rollback) while `dirty_pending=True` and verify fallback parity
				frappe.set_user("Administrator")
				for dt_name, idx_name in expected_new_indexes:
					frappe.db.sql_ddl(f"alter table `tab{dt_name}` drop index `{idx_name}`")
				runtime_cache.clear_schema_cache()

				frappe.set_user(self.bod_user)
				fallback_res = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=2,
					page_size=20,
				)
				self.assertEqual(fallback_res["pagination"]["total"], 53)

				# Re-apply `ensure_p3_indexes()` (`N-1 -> N`)
				frappe.set_user("Administrator")
				ensure_p3_indexes()

				# 5. Simulate active worker mid-flight (`status = 'processing'`) -> verify `dirty_pending=True` still holds,
				#    then reset to `'pending'` and execute `process_dirty_scopes(max_scopes=25)` to verify snapshot convergence (53 / 35)
				frappe.db.sql(
					"update `tabCSKH Dirty Scope` set status = 'processing' where status = 'pending'"
				)
				runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
				frappe.db.commit()
				frappe.set_user(self.bod_user)
				in_flight_res = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=1,
					page_size=20,
				)
				self.assertTrue(in_flight_res["freshness"]["dirty_pending"])
				self.assertEqual(in_flight_res["pagination"]["total"], 53)

				frappe.set_user("Administrator")
				frappe.db.sql(
					"update `tabCSKH Dirty Scope` set status = 'pending' where status = 'processing'"
				)
				runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
				frappe.db.commit()
				worker_res = process_dirty_scopes(max_scopes=25)
				self.assertGreaterEqual(int(worker_res.get("processed_scopes") or 0), 1)
				frappe.set_user(self.bod_user)
				post_worker_res = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=1,
					page_size=20,
				)
				self.assertFalse(post_worker_res["freshness"]["dirty_pending"])
				self.assertEqual(post_worker_res["pagination"]["total"], 53)
				snap_after = (post_worker_res.get("drilldown_context") or {}).get("snapshot_metric") or {}
				self.assertEqual(snap_after.get("denominator"), 53)
				self.assertEqual(snap_after.get("numerator"), 35)
		finally:
			frappe.set_user("Administrator")
			ensure_p3_indexes()
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

	def test_api05b_cross_process_coalescing_cold_warm_dirty_worker_and_rbac_scopes(self):
		"""Verify cross-Gunicorn-process & c=12/c=40 coalescing across cold/warm/dirty/worker & RBAC scopes."""
		frappe.set_user("Administrator")
		base_dt = datetime(2026, 7, 15, 9, 0, 0)
		m_key = "2026-07"
		orig_tmh = frappe.get_all(
			"CSKH Team Membership History",
			fields=["name", "effective_from"],
		)
		site_name = str(frappe.local.site)
		sites_path = os.path.abspath(getattr(frappe.local, "sites_path", ".") or ".")
		child_env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}

		try:
			frappe.db.sql(
				"update `tabCSKH Team Membership History` set effective_from = %s",
				(datetime(2026, 1, 1, 0, 0, 0),),
			)
			frappe.db.commit()

			staff_cycle = [self.staff_a, self.staff_b, self.staff_c]
			team_cycle = [self.team_a, self.team_a, self.team_b]
			camp_cycle = [self.shared_campaign, self.shared_campaign, self.team_b_campaign]

			now_ts = now_datetime()
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
				"invalid_phone_snapshot",
				"note",
				"verification_status",
				"policy_name_snapshot",
				"policy_version_snapshot",
				"idempotency_key",
				"payload_hash",
				"work_item_revision",
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

			lead_rows, wi_rows, ah_rows, att_rows, sla_rows = [], [], [], [], []
			for i in range(90):
				lid = f"LEAD-G18-DEEP-SF-{i:04d}"
				wi_id = f"WI-G18-SF-{i:04d}"
				ah_id = f"AH-G18-SF-{i:04d}"
				att_id = f"CA-G18-SF-{i:04d}"
				sla_id = f"SLA-G18-SF-{i:04d}"
				u = staff_cycle[i % 3]
				t_node = team_cycle[i % 3]
				c_id = camp_cycle[i % 3]
				st_dt = base_dt + timedelta(minutes=i)
				att_dt = st_dt + timedelta(minutes=10)
				due_dt = st_dt + timedelta(minutes=30)
				is_conn = 1 if (i % 2 == 0) else 0
				ocode = "connected_interested" if is_conn else "no_answer"
				olabel = "Đã trao đổi — Có quan tâm" if is_conn else "Không nghe máy"
				ogroup = "positive" if is_conn else "unreached"

				lead_rows.append(
					(
						lid,
						now_ts,
						now_ts,
						"Administrator",
						"Administrator",
						0,
						0,
						"CRM-LEAD-",
						f"Ứng viên SF {i:04d}",
						f"Ứng viên SF {i:04d}",
						f"09681{i:05d}",
						"Contacted",
						u,
						0,
					)
				)
				wi_rows.append(
					(
						wi_id,
						st_dt,
						att_dt,
						"Administrator",
						"Administrator",
						0,
						0,
						lid,
						c_id,
						"in_progress",
						u,
						st_dt,
						1,
						is_conn,
						att_dt,
						att_dt,
						ocode,
						olabel,
						1,
					)
				)
				ah_rows.append(
					(
						ah_id,
						st_dt,
						st_dt,
						"Administrator",
						"Administrator",
						0,
						0,
						wi_id,
						"assign",
						None,
						u,
						"Administrator",
						st_dt,
						st_dt,
						None,
						f"evt-g18-sf-{i:04d}",
						t_node,
						AUTHORITATIVE,
						"Seed SF",
					)
				)
				att_rows.append(
					(
						att_id,
						att_dt,
						att_dt,
						u,
						u,
						0,
						0,
						wi_id,
						lid,
						c_id,
						ah_id,
						u,
						u,
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
						0,
						f"Ghi chú SF {i:04d}",
						"self_reported",
						"GLOBAL-DEFAULT",
						1,
						f"idem-g18-sf-{i:04d}",
						f"hash-g18-sf-{i:04d}",
						1,
					)
				)
				sla_rows.append(
					(
						sla_id,
						st_dt,
						att_dt,
						"Administrator",
						"Administrator",
						0,
						0,
						"FIRST_ATTEMPT",
						wi_id,
						lid,
						c_id,
						ah_id,
						u,
						t_node,
						AUTHORITATIVE,
						"fulfilled_on_time",
						0,
						0,
						0,
						st_dt,
						due_dt,
						due_dt,
						0,
						att_dt,
						att_id,
						None,
						att_dt,
						att_dt,
						"GLOBAL-DEFAULT",
						1,
						f"idem-sla-sf-{i:04d}",
					)
				)

			def _insert_rows(tbl, cols, vals):
				assert len(vals[0]) == len(cols), f"{tbl}: {len(vals[0])} != {len(cols)}"
				c_sql = ", ".join(f"`{c}`" for c in cols)
				ph = "(" + ", ".join(["%s"] * len(cols)) + ")"
				v_sql = ", ".join([ph] * len(vals))
				flat = [x for row in vals for x in row]
				frappe.db.sql(f"insert into `{tbl}` ({c_sql}) values {v_sql}", tuple(flat))

			_insert_rows("tabCRM Lead", lead_cols, lead_rows)
			_insert_rows("tabCSKH Work Item", wi_cols, wi_rows)
			_insert_rows("tabCSKH Assignment History", ah_cols, ah_rows)
			_insert_rows("tabCSKH Contact Attempt", att_cols, att_rows)
			_insert_rows("tabCSKH SLA Obligation", sla_cols, sla_rows)
			frappe.db.commit()

			execute_kpi_recompute(
				resolve_period_spec("monthly", m_key), run_mode="full", actor="Administrator"
			)
			frappe.db.commit()

			with runtime_cache.enable_runtime_cache_for_test():
				# 1. Cold cache (`dirty_pending=False`, `page=1`): `0` `ah_orphan_work_item` `LEFT JOIN` & `0` `COUNT(*)`!
				frappe.clear_cache()
				runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
				captured_cold_sql: list[str] = []
				orig_sql = frappe.db.sql

				def _spy_cold(q, *a, **kw):
					captured_cold_sql.append(" ".join(str(q).lower().split()))
					return orig_sql(q, *a, **kw)

				frappe.set_user(self.bod_user)
				with patch.object(frappe.db, "sql", side_effect=_spy_cold):
					cold_clean = p3_reporting.get_metric_drilldown(
						metric_code="CONNECTED_RATE",
						period_type="monthly",
						period_key=m_key,
						scope_type="company",
						page=1,
						page_size=20,
					)
				self.assertFalse(cold_clean["freshness"]["dirty_pending"])
				self.assertEqual(cold_clean["pagination"]["total"], 90)
				self.assertTrue(
					all(r["pii_redacted"] and r["lead"] == "REDACTED" for r in cold_clean["rows"])
				)
				orphan_scans = [
					q
					for q in captured_cold_sql
					if "left join `tabcskh work item`" in q and "wi.name is null" in q
				]
				self.assertEqual(
					len(orphan_scans),
					0,
					f"Expected 0 eager ah_orphan_work_item LEFT JOIN scans on page=1 fresh snapshot, got {orphan_scans}",
				)
				self.assertEqual(runtime_cache.get_coalesce_telemetry()["sql_count_executions"], 0)

				# 2. Scoped `frappe.clear_cache(user=...)` (e.g. `ToDo` creation in `record_attempt` callback)
				#    preserves `DOMAIN_WORK_ITEM`, `DOMAIN_KPI_SNAPSHOT`, `DOMAIN_DIRTY_SCOPE`, and `_SCHEMA_LRU`!
				ep_before_user_cc = runtime_cache.get_domains_epoch_tuple(
					runtime_cache.DOMAIN_WORK_ITEM,
					runtime_cache.DOMAIN_KPI_SNAPSHOT,
					runtime_cache.DOMAIN_DIRTY_SCOPE,
					runtime_cache.DOMAIN_USER_HIERARCHY,
				)
				self.assertIsNotNone(
					runtime_cache._SCHEMA_LRU.get(
						(site_name, "idx", "CSKH Work Item", "idx_cskh_wi_name_camp")
					)
				)
				frappe.clear_cache(user=self.staff_a)
				ep_after_user_cc = runtime_cache.get_domains_epoch_tuple(
					runtime_cache.DOMAIN_WORK_ITEM,
					runtime_cache.DOMAIN_KPI_SNAPSHOT,
					runtime_cache.DOMAIN_DIRTY_SCOPE,
					runtime_cache.DOMAIN_USER_HIERARCHY,
				)
				self.assertEqual(ep_after_user_cc[:3], ep_before_user_cc[:3])
				self.assertGreater(ep_after_user_cc[3], ep_before_user_cc[3])
				self.assertIsNotNone(
					runtime_cache._SCHEMA_LRU.get(
						(site_name, "idx", "CSKH Work Item", "idx_cskh_wi_name_camp")
					)
				)

				# 3. Mutate period data (`dirty_pending=True`) & verify REAL 4-process OS stampede coalescing!
				frappe.set_user("Administrator")
				frappe.db.sql(
					"update `tabCSKH Contact Attempt` set invalid_phone_snapshot = 1, outcome_code = 'wrong_number' where name = %s",
					("CA-G18-SF-0000",),
				)
				prev_suppress = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
				frappe.flags.cskh_suppress_dirty_enqueue = True
				try:
					schedule_post_commit_dirty_invalidation(
						work_item="WI-G18-SF-0000",
						campaign=self.shared_campaign,
						owner_user=self.staff_a,
						event_dt=base_dt,
						reason="sf_dirty_mutation",
						trigger_reference="CA-G18-SF-0000",
					)
					_flush_pending_dirty_events()
					frappe.db.commit()
				finally:
					frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress

				# 3a. Spawn 4 REAL separate OS Python worker processes hitting cold Redis epoch simultaneously:
				runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
				runtime_cache.clear_all_local_caches()
				runtime_cache.set_cross_process_diag_mode(probe_enabled=True, disable_coalesce=False)
				runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)

				barrier_key = f"cskh:test:proc_barrier:{int(time.time() * 1000)}"
				child_code = """
import json, os, sys, time
import redis
import frappe
from crm_cskh.api import p3_reporting
from crm_cskh.services import runtime_cache

cfg = json.loads(sys.argv[1])
os.chdir(cfg["sites_path"])
frappe.init(cfg["site"], sites_path=cfg["sites_path"], force=True)
frappe.connect()
frappe.flags.in_test = True
frappe.set_user(cfg["actor"])
try:
	with runtime_cache.enable_runtime_cache_for_test():
		cache_obj = runtime_cache._resolve_redis_wrapper()
		bkey = cache_obj.make_key(cfg["barrier_key"])
		redis.Redis.incr(cache_obj, bkey)
		redis.Redis.expire(cache_obj, bkey, 30)
		dl = time.monotonic() + 8.0
		while time.monotonic() < dl:
			raw_cur = redis.Redis.get(cache_obj, bkey)
			cur = int(raw_cur.decode("utf-8") if isinstance(raw_cur, bytes) else (raw_cur or 0))
			if cur >= int(cfg["party_size"]):
				break
			time.sleep(0.005)
		runtime_cache.on_before_request()
		res = p3_reporting.get_metric_drilldown(
			metric_code="CONNECTED_RATE",
			period_type="monthly",
			period_key=cfg["m_key"],
			scope_type="company",
			page=1,
			page_size=20,
		)
		print("CHILD_SF_JSON:" + json.dumps({
			"pid": os.getpid(),
			"total": res["pagination"]["total"],
			"dirty_pending": res["freshness"]["dirty_pending"],
			"rows_len": len(res["rows"]),
			"first_lead": res["rows"][0]["lead"] if res["rows"] else None,
		}))
finally:
	try:
		frappe.db.rollback()
	except Exception:
		pass
	frappe.destroy()
"""
				procs = []
				for p_idx in range(4):
					actor_u = "Administrator" if (p_idx % 2 == 0) else self.bod_user
					arg_json = json.dumps(
						{
							"site": site_name,
							"sites_path": sites_path,
							"actor": actor_u,
							"m_key": m_key,
							"barrier_key": barrier_key,
							"party_size": 4,
						}
					)
					procs.append(
						subprocess.Popen(
							[sys.executable, "-c", child_code, arg_json],
							stdout=subprocess.PIPE,
							stderr=subprocess.PIPE,
							text=True,
							env=child_env,
						)
					)

				child_payloads = []
				for p_idx, proc in enumerate(procs):
					out_txt, err_txt = proc.communicate(timeout=45)
					self.assertEqual(proc.returncode, 0, f"Child {p_idx} failed:\n{out_txt}\n{err_txt}")
					lines = [
						ln[len("CHILD_SF_JSON:") :]
						for ln in out_txt.splitlines()
						if ln.startswith("CHILD_SF_JSON:")
					]
					self.assertEqual(len(lines), 1, out_txt)
					child_payloads.append(json.loads(lines[0]))

				self.assertEqual(len({c["pid"] for c in child_payloads}), 4)
				for idx, cp in enumerate(child_payloads):
					self.assertEqual(cp["total"], 89)
					self.assertTrue(cp["dirty_pending"])
					self.assertEqual(cp["rows_len"], 20)
					if idx % 2 == 0:
						self.assertNotEqual(cp["first_lead"], "REDACTED")
					else:
						self.assertEqual(cp["first_lead"], "REDACTED")

				cross_stats = runtime_cache.read_cross_process_diag_telemetry()
				self.assertEqual(
					cross_stats["totals"]["sql_count_executions"],
					1,
					f"Expected exactly 1 SQL COUNT(*) across 4 separate OS processes, got {cross_stats}",
				)
				self.assertGreaterEqual(
					cross_stats["totals"]["coalesced_follower_hits"] + cross_stats["totals"]["redis_hits"],
					3,
					cross_stats,
				)

				# 3b. Compare c=12 BEFORE (`disable_coalesce=True`) vs AFTER (`disable_coalesce=False`) + c=40 & warm!
				def _run_threaded_drilldown_wave(
					concurrency: int, actor_user: str, *, clear_l1_each: bool
				) -> list[dict]:
					barrier = threading.Barrier(concurrency, timeout=10.0)

					def _worker(_idx: int) -> dict:
						frappe.init(site_name, sites_path=sites_path, force=True)
						frappe.connect()
						frappe.flags.in_test = True
						frappe.flags.cskh_force_runtime_cache = True
						frappe.set_user(actor_user)
						try:
							try:
								barrier.wait()
							except threading.BrokenBarrierError:
								pass
							if clear_l1_each:
								runtime_cache._DRILLDOWN_COUNT_LRU.clear()
							runtime_cache.on_before_request()
							return p3_reporting.get_metric_drilldown(
								metric_code="CONNECTED_RATE",
								period_type="monthly",
								period_key=m_key,
								scope_type="company",
								page=1,
								page_size=20,
							)
						finally:
							try:
								frappe.db.rollback()
								frappe.destroy()
							except Exception:
								pass

					with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
						futs = [pool.submit(_worker, idx) for idx in range(concurrency)]
						return [f.result(timeout=30) for f in futs]

				# Before-mode (`disable_coalesce=True`): 12 concurrent requests execute 12 duplicate `COUNT(*)`!
				runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
				runtime_cache.clear_all_local_caches()
				runtime_cache.set_cross_process_diag_mode(probe_enabled=True, disable_coalesce=True)
				runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
				before_c12 = _run_threaded_drilldown_wave(12, self.bod_user, clear_l1_each=True)
				self.assertTrue(all(r["pagination"]["total"] == 89 for r in before_c12))
				before_stats = runtime_cache.read_cross_process_diag_telemetry()["totals"]
				self.assertEqual(before_stats["sql_count_executions"], 12)

				# After-mode (`disable_coalesce=False`): 12 concurrent cold requests execute 1 `COUNT(*)`, warm c=20 executes 0!
				runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
				runtime_cache.clear_all_local_caches()
				runtime_cache.set_cross_process_diag_mode(probe_enabled=True, disable_coalesce=False)
				runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
				after_c12 = _run_threaded_drilldown_wave(12, self.bod_user, clear_l1_each=True)
				self.assertTrue(all(r["pagination"]["total"] == 89 for r in after_c12))
				after_c12_stats = runtime_cache.read_cross_process_diag_telemetry()["totals"]
				self.assertEqual(after_c12_stats["sql_count_executions"], 1, after_c12_stats)

				runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
				warm_wave = _run_threaded_drilldown_wave(20, "Administrator", clear_l1_each=False)
				self.assertTrue(all(r["pagination"]["total"] == 89 for r in warm_wave))
				warm_stats = runtime_cache.read_cross_process_diag_telemetry()["totals"]
				self.assertEqual(warm_stats["sql_count_executions"], 0, warm_stats)
				self.assertEqual(warm_stats["l1_hits"] + warm_stats["redis_hits"], 20, warm_stats)

				runtime_cache.set_cross_process_diag_mode(probe_enabled=False, disable_coalesce=False)

				# 4. RBAC scope isolation & `process_dirty_scopes` convergence
				runtime_cache.on_before_request()

				frappe.set_user(self.leader_a)
				with self.assertRaises(ForbiddenScope):
					p3_reporting.get_metric_drilldown(
						metric_code="CONNECTED_RATE",
						period_type="monthly",
						period_key=m_key,
						scope_type="company",
					)
				tl_dirty = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="team",
					scope_id=self.team_a,
					page=1,
					page_size=20,
				)
				self.assertEqual(tl_dirty["pagination"]["total"], 59)

				frappe.set_user(self.staff_a)
				with self.assertRaises(ForbiddenScope):
					p3_reporting.get_metric_drilldown(
						metric_code="CONNECTED_RATE",
						period_type="monthly",
						period_key=m_key,
						scope_type="team",
						scope_id=self.team_a,
					)
				st_dirty = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="employee",
					scope_id=self.staff_a,
					page=1,
					page_size=20,
				)
				self.assertEqual(st_dirty["pagination"]["total"], 29)

				frappe.set_user("Administrator")
				worker_out = process_dirty_scopes(max_scopes=25)
				self.assertGreaterEqual(int(worker_out.get("processed_scopes") or 0), 1)
				frappe.set_user(self.bod_user)
				runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
				post_worker = self._assert_fast_matches_reference(
					metric_code="CONNECTED_RATE",
					period_type="monthly",
					period_key=m_key,
					scope_type="company",
					page=1,
					page_size=20,
				)
				self.assertFalse(post_worker["freshness"]["dirty_pending"])
				self.assertEqual(post_worker["pagination"]["total"], 89)
				self.assertEqual(runtime_cache.get_coalesce_telemetry()["sql_count_executions"], 0)
		finally:
			try:
				frappe.db.rollback()
			except Exception:
				pass
			frappe.set_user("Administrator")
			runtime_cache.set_cross_process_diag_mode(probe_enabled=False, disable_coalesce=False)
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

	def test_singleflight_mid_count_epoch_change_lock_expiry_dead_holder_and_bounded_wait(self):
		"""Verify mid-COUNT epoch change, lock TTL expiry, SIGKILL dead lock holder, and bounded wait."""
		import redis as _redis_raw

		frappe.set_user("Administrator")
		site_name = str(frappe.local.site)
		sites_path = os.path.abspath(getattr(frappe.local, "sites_path", ".") or ".")
		child_env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}

		with runtime_cache.enable_runtime_cache_for_test():
			cache_obj = runtime_cache._resolve_redis_wrapper()
			self.assertIsNotNone(cache_obj)

			# ------------------------------------------------------------------
			# 1. Mid-COUNT epoch change: leader MUST NOT poison old or new epoch,
			#    and concurrent follower switches cleanly to the new epoch!
			# ------------------------------------------------------------------
			runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
			runtime_cache.clear_all_local_caches()
			runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)

			scope_ep = ("CONNECTED_RATE", "monthly", "2099-08", "ep-mid-change-case")
			db_backing_count = [75000]
			leader_entered = threading.Event()
			proceed_after_bump = threading.Event()
			compute_calls = [0]

			def _leader_or_fresh_count() -> int:
				compute_calls[0] += 1
				call_no = compute_calls[0]
				snap_val = db_backing_count[0]
				if call_no == 1:
					leader_entered.set()
					proceed_after_bump.wait(timeout=3.0)
				return snap_val

			follower_res: list[int] = []

			def _follower_thread():
				frappe.init(site_name, sites_path=sites_path, force=True)
				frappe.connect()
				frappe.flags.in_test = True
				frappe.flags.cskh_force_runtime_cache = True
				try:
					leader_entered.wait(timeout=3.0)
					time.sleep(0.02)
					follower_res.append(
						runtime_cache.get_coalesced_drilldown_count(
							scope_ep,
							_leader_or_fresh_count,
							lock_ttl_ms=3000,
							wait_timeout_ms=2000,
						)
					)
				finally:
					try:
						frappe.db.rollback()
						frappe.destroy()
					except Exception:
						pass

			def _mutator_thread():
				frappe.init(site_name, sites_path=sites_path, force=True)
				frappe.connect()
				frappe.flags.in_test = True
				frappe.flags.cskh_force_runtime_cache = True
				try:
					leader_entered.wait(timeout=3.0)
					time.sleep(0.05)
					db_backing_count[0] = 74998
					runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
					proceed_after_bump.set()
				finally:
					try:
						frappe.db.rollback()
						frappe.destroy()
					except Exception:
						pass

			t_fol = threading.Thread(target=_follower_thread)
			t_mut = threading.Thread(target=_mutator_thread)
			t_fol.start()
			t_mut.start()

			leader_val = runtime_cache.get_coalesced_drilldown_count(
				scope_ep,
				_leader_or_fresh_count,
				lock_ttl_ms=3000,
				wait_timeout_ms=2000,
			)
			t_fol.join(timeout=10.0)
			t_mut.join(timeout=10.0)

			self.assertEqual(leader_val, 75000)
			self.assertEqual(follower_res, [74998])
			tel_ep = runtime_cache.get_coalesce_telemetry()
			self.assertGreaterEqual(tel_ep["epoch_changed_mid_compute"], 1, tel_ep)

			# Subsequent call under the new epoch hits the fresh 74,998 value (never 75,000!)
			after_bump_val = runtime_cache.get_coalesced_drilldown_count(
				scope_ep,
				lambda: 999999,
				lock_ttl_ms=3000,
				wait_timeout_ms=2000,
			)
			self.assertEqual(after_bump_val, 74998)

			# ------------------------------------------------------------------
			# 2. Lock expiration (`lock_ttl_ms` expires while slow Leader 1 runs):
			#    Leader 2 takes over cleanly, and Leader 1's Lua compare-and-del
			#    NEVER deletes Leader 2's active lock!
			# ------------------------------------------------------------------
			runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
			runtime_cache.clear_all_local_caches()
			runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)

			scope_exp = ("CONNECTED_RATE", "monthly", "2099-08", "lock-expiry-case")
			l1_started = threading.Event()
			l2_acquired_and_holding = threading.Event()
			l1_finished_release = threading.Event()
			lock_still_present_after_l1_release: list[bool] = []

			ep_exp = runtime_cache.get_domains_epoch_tuple(*runtime_cache.DRILLDOWN_COUNT_DOMAINS)
			dig_exp = runtime_cache._build_sf_digest(site_name, "dd_count", scope_exp, ep_exp)
			lock_rkey_exp = cache_obj.make_key(f"cskh:sf:v1:lock:dd_count:{dig_exp}")

			def _slow_l1_compute() -> int:
				l1_started.set()
				l2_acquired_and_holding.wait(timeout=3.0)
				return 18750

			def _l2_takeover_worker() -> int:
				frappe.init(site_name, sites_path=sites_path, force=True)
				frappe.connect()
				frappe.flags.in_test = True
				frappe.flags.cskh_force_runtime_cache = True
				try:
					l1_started.wait(timeout=3.0)
					dl = time.monotonic() + 1.5
					while time.monotonic() < dl and runtime_cache._sf_redis_lock_exists(
						cache_obj, lock_rkey_exp
					):
						time.sleep(0.01)
					runtime_cache._DRILLDOWN_COUNT_LRU.clear()

					def _l2_compute() -> int:
						l2_acquired_and_holding.set()
						l1_finished_release.wait(timeout=3.0)
						lock_still_present_after_l1_release.append(
							runtime_cache._sf_redis_lock_exists(cache_obj, lock_rkey_exp)
						)
						return 18750

					return runtime_cache.get_coalesced_drilldown_count(
						scope_exp,
						_l2_compute,
						lock_ttl_ms=3000,
						wait_timeout_ms=2000,
					)
				finally:
					try:
						frappe.db.rollback()
						frappe.destroy()
					except Exception:
						pass

			l2_out: list[int] = []
			t_l2 = threading.Thread(target=lambda: l2_out.append(_l2_takeover_worker()))
			t_l2.start()

			val_l1 = runtime_cache.get_coalesced_drilldown_count(
				scope_exp,
				_slow_l1_compute,
				lock_ttl_ms=70,
				wait_timeout_ms=2000,
			)
			l1_finished_release.set()
			t_l2.join(timeout=10.0)

			self.assertEqual(val_l1, 18750)
			self.assertEqual(l2_out, [18750])
			self.assertEqual(
				lock_still_present_after_l1_release,
				[True],
				"Expired Leader 1 must NOT delete Leader 2's active Redis lock!",
			)
			tel_exp = runtime_cache.get_coalesce_telemetry()
			self.assertGreaterEqual(tel_exp["lock_release_skipped_token_mismatch"], 1, tel_exp)

			# ------------------------------------------------------------------
			# 3. Dead lock-holder OS process (`SIGKILL` / `proc.kill()` mid-COUNT):
			#    Lock auto-expires via Redis `PX` TTL and follower recovers cleanly!
			# ------------------------------------------------------------------
			runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
			runtime_cache.clear_all_local_caches()
			runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)

			scope_dead = ("CONNECTED_RATE", "monthly", "2099-08", "sigkill-dead-holder-case")
			ready_rkey_raw = f"cskh:test:dead_holder_ready:{int(time.time() * 1000)}"
			dead_holder_script = """
import json, os, sys, time
import redis
import frappe
from crm_cskh.services import runtime_cache

cfg = json.loads(sys.argv[1])
os.chdir(cfg["sites_path"])
frappe.init(cfg["site"], sites_path=cfg["sites_path"], force=True)
frappe.connect()
frappe.flags.in_test = True
frappe.flags.cskh_force_runtime_cache = True
runtime_cache.on_before_request()

def _hang_holding_lock():
	c = runtime_cache._resolve_redis_wrapper()
	redis.Redis.set(c, c.make_key(cfg["ready_key"]), str(os.getpid()), ex=30)
	time.sleep(30.0)
	return 56250

runtime_cache.get_coalesced_drilldown_count(
	tuple(cfg["scope"]),
	_hang_holding_lock,
	lock_ttl_ms=500,
	wait_timeout_ms=2000,
)
"""
			dead_proc = subprocess.Popen(
				[
					sys.executable,
					"-c",
					dead_holder_script,
					json.dumps(
						{
							"site": site_name,
							"sites_path": sites_path,
							"scope": list(scope_dead),
							"ready_key": ready_rkey_raw,
						}
					),
				],
				stdout=subprocess.PIPE,
				stderr=subprocess.PIPE,
				text=True,
				env=child_env,
			)
			try:
				ready_full = cache_obj.make_key(ready_rkey_raw)
				dl_ready = time.monotonic() + 15.0
				while time.monotonic() < dl_ready and not _redis_raw.Redis.get(cache_obj, ready_full):
					time.sleep(0.02)
				self.assertIsNotNone(
					_redis_raw.Redis.get(cache_obj, ready_full),
					"Dead-holder child did not acquire lock in time",
				)
				# Hard-kill (`SIGKILL`) the lock holder so its `finally:` block NEVER runs!
				dead_proc.kill()
				dead_proc.wait(timeout=10.0)
			finally:
				if dead_proc.poll() is None:
					dead_proc.kill()
					dead_proc.wait(timeout=5.0)

			runtime_cache.on_before_request()
			t_rec0 = time.perf_counter()
			recovered_val = runtime_cache.get_coalesced_drilldown_count(
				scope_dead,
				lambda: 56250,
				lock_ttl_ms=2000,
				wait_timeout_ms=1500,
			)
			rec_ms = (time.perf_counter() - t_rec0) * 1000.0
			self.assertEqual(recovered_val, 56250)
			self.assertLess(rec_ms, 1300.0, f"Expected fast TTL recovery after SIGKILL, took {rec_ms:.1f} ms")
			tel_dead = runtime_cache.get_coalesce_telemetry()
			self.assertGreaterEqual(tel_dead["leader_computes"], 1, tel_dead)

			# ------------------------------------------------------------------
			# 4. Bounded follower wait timeout (`wait_timeout_ms` ceiling):
			#    Pre-plant a 20,000 ms Redis lock; follower MUST return within
			#    bounded budget (~120 ms) via clean direct fallback (`0` hang)!
			# ------------------------------------------------------------------
			runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
			runtime_cache.clear_all_local_caches()
			runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)

			scope_bw = ("CONNECTED_RATE", "monthly", "2099-08", "bounded-wait-ceiling-case")
			ep_bw = runtime_cache.get_domains_epoch_tuple(*runtime_cache.DRILLDOWN_COUNT_DOMAINS)
			dig_bw = runtime_cache._build_sf_digest(site_name, "dd_count", scope_bw, ep_bw)
			lock_rkey_bw = cache_obj.make_key(f"cskh:sf:v1:lock:dd_count:{dig_bw}")
			self.assertTrue(
				runtime_cache._sf_redis_acquire_lock(
					cache_obj, lock_rkey_bw, "stalled-holder:999:tok", 20_000
				)
			)
			try:
				t_bw0 = time.perf_counter()
				bw_val = runtime_cache.get_coalesced_drilldown_count(
					scope_bw,
					lambda: 12500,
					lock_ttl_ms=5000,
					wait_timeout_ms=120,
				)
				bw_elapsed_ms = (time.perf_counter() - t_bw0) * 1000.0
			finally:
				runtime_cache._sf_release_lock_if_owner(cache_obj, lock_rkey_bw, "stalled-holder:999:tok")

			self.assertEqual(bw_val, 12500)
			self.assertGreaterEqual(bw_elapsed_ms, 95.0)
			self.assertLess(
				bw_elapsed_ms,
				800.0,
				f"Follower wait exceeded bounded timeout budget (elapsed={bw_elapsed_ms:.1f} ms)",
			)
			tel_bw = runtime_cache.get_coalesce_telemetry()
			self.assertEqual(tel_bw["wait_timeouts"], 1, tel_bw)
			self.assertEqual(tel_bw["fallback_computes"], 1, tel_bw)
			self.assertEqual(tel_bw["sql_count_executions"], 1, tel_bw)
