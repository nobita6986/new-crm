"""Integration, golden-expectation, reconciliation & security tests for `P3-04`, `P3-05`, `P3-06`.

Covers:
  1. Independent golden expectations (`p3_golden_expectations.json`): `7350 / 85 = 86.47` (`provisional`),
     `8700 / 100 = 87.0` (`final`), `denominator == 0 -> null`, and `Asia/Ho_Chi_Minh` period specs.
  2. Effective `CSKH Contact Attempt` vs native `CRM Call Log` (`0` double count), `attempted` / `connected` /
     `self_reported` / `verified` / `disputed` split, and `A -> B -> A` (`1` `DISTINCT Work Item` vs `3` intervals).
  3. Non-averaging rollups (`Employee -> Team -> Company`, `Daily -> Monthly` distinct sets) and transparent
     `__unattributed_historical_team__` bucket for pre-P3 unknown attribution rows.
  4. Post-commit `CSKH Dirty Scope` invalidation (`0` report SQL inside `record_attempt` tx, rollback safety)
     and `reconcile_published_period` verification.
  5. Prior-period approved correction producing a `restated` `CSKH KPI Run` (`restated_from_run`) while keeping
     prior revision & snapshots immutable (append-only).
  6. Redis scope lock (`409 RECOMPUTE_IN_PROGRESS`) and `run_kpi_backfill` (`dry_run`, batching, checkpoint resume).
  7. QA disabled honesty (`qa_enabled=false` default, `409 QA_DISABLED`, `0` fake reviews/verified) and
     anti-self-review (`403 QA_CONFLICT` — `S06`) when enabled in policy.
  8. Row-level RBAC, generic ORM/API write blocks & `permlevel=1` filter-oracle blocks (`S07`, `S13`, `S16`, `S17`).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.jobs.reconcile_metrics import reconcile_published_period
from crm_cskh.p3_models import authorized_sla_write
from crm_cskh.services import contact_attempt, correction, ownership
from crm_cskh.services import qa as qa_svc
from crm_cskh.services.attribution import UNKNOWN_HISTORICAL
from crm_cskh.services.errors import (
	ForbiddenScope,
	InvalidPeriod,
	QaConflict,
	QaDisabled,
	RecomputeInProgress,
)
from crm_cskh.services.kpi_calculator import (
	COMPANY_SCOPE_ID,
	UNATTRIBUTED_TEAM_ID,
	compute_process_score_from_components,
	make_rate_metric,
)
from crm_cskh.services.kpi_periods import resolve_period_spec, today_hcm
from crm_cskh.services.kpi_runs import (
	acquire_recompute_lock,
	execute_kpi_recompute,
	get_kpi_run_status,
	process_dirty_scopes,
	request_scoped_recompute,
	run_kpi_backfill,
	unpack_snapshot_metrics,
)
from crm_cskh.tests.p3_base import P3TestCase

GOLDEN_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "p3_golden_expectations.json"


class TestP3KPIAndSnapshots(P3TestCase):
	"""End-to-end verification of `P3-04` KPI calculator, `P3-05` runs/snapshots/backfill, and `P3-06` QA."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		# Clean P3 KPI/QA state between tests so counts on synthetic dates are deterministic
		for dt in ("CSKH Dirty Scope", "CSKH KPI Snapshot", "CSKH KPI Run", "CSKH QA Review"):
			if frappe.db.table_exists(dt):
				frappe.db.sql(f"delete from `tab{dt}`")
		frappe.db.commit()

		self.campaign = self.make_campaign(team_scope=None)
		self.make_rule(self.campaign, self.staff_a, weight=40)
		self.make_rule(self.campaign, self.staff_b, weight=30)
		self.make_rule(self.campaign, self.staff_c, weight=30)

	def _get_published_snapshot(
		self,
		period_type: str,
		period_key: str,
		scope_type: str,
		scope_id: str,
		campaign_filter: str = "",
	) -> dict:
		row = frappe.db.get_value(
			"CSKH KPI Snapshot",
			{
				"period_type": period_type,
				"period_key": period_key,
				"campaign_filter": campaign_filter or "",
				"scope_type": scope_type,
				"scope_id": scope_id,
				"is_latest_published": 1,
			},
			"*",
			as_dict=True,
		)
		self.assertIsNotNone(
			row,
			f"Expected published snapshot for {period_type}:{period_key} [{campaign_filter}] {scope_type}:{scope_id}",
		)
		metrics, comps, breakdown = unpack_snapshot_metrics(row.metrics_json)
		row["metrics"] = metrics
		row["score_components"] = comps
		row["attempt_breakdown"] = breakdown
		row["exclusion_counts"] = json.loads(row.exclusion_counts_json or "{}")
		return row

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

	def test_golden_process_score_provisional_final_and_zero_denominator(self):
		"""`P3-04`: Independent golden fixture (`7350/85 = 86.47` provisional, `87.0` final, `0/0 -> null`) & period validation."""
		golden = json.loads(GOLDEN_FIXTURE_PATH.read_text(encoding="utf-8"))

		# 1. Zero denominator -> `value: None, status: "insufficient_data", reason: "no_eligible_records"`
		zd = golden["golden_zero_denominator"]
		m_zero = make_rate_metric("FIRST_ATTEMPT_SLA_RATE", zd["numerator"], zd["denominator"])
		self.assertIsNone(m_zero["value"])
		self.assertEqual(m_zero["status"], zd["expected_status"])
		self.assertEqual(m_zero["reason"], zd["expected_reason"])

		# 2. Golden `qa_enabled = false`: `7350 / 85 = 86.47` (`provisional`, `QA_PASS_RATE` `disabled`, NOT `0%`)
		g_prov = golden["golden_process_score_qa_disabled"]
		inp = g_prov["inputs"]
		exp = g_prov["expected"]
		fa = make_rate_metric(
			"FIRST_ATTEMPT_SLA_RATE",
			inp["first_attempt_sla"]["numerator"],
			inp["first_attempt_sla"]["denominator"],
		)
		fu = make_rate_metric(
			"FOLLOWUP_SLA_RATE",
			inp["followup_sla"]["numerator"],
			inp["followup_sla"]["denominator"],
		)
		dh = make_rate_metric(
			"VALID_OUTCOME_LOGGING_RATE",
			inp["data_hygiene"]["numerator"],
			inp["data_hygiene"]["denominator"],
		)
		qa_disabled = make_rate_metric(
			"QA_PASS_RATE",
			0,
			0,
			disabled=True,
			disabled_reason="qa_disabled_by_policy",
		)
		self.assertEqual(qa_disabled["status"], exp["qa_pass_rate_status"])
		self.assertIsNone(qa_disabled["value"])

		score_prov, comps_prov = compute_process_score_from_components(
			first_attempt_sla=fa,
			followup_sla=fu,
			data_hygiene=dh,
			callback_dnc_discipline_rate=80.0,
			callback_dnc_eligible_count=inp["callback_dnc_discipline"]["denominator"],
			qa_pass_rate=qa_disabled,
			qa_enabled=False,
			qa_sample_count=0,
			eligible_events_total=35,
			policy_cfg=inp["weights"],
		)
		self.assertEqual(score_prov["numerator"], exp["weighted_numerator"])
		self.assertEqual(score_prov["valid_weight"], exp["valid_weight"])
		self.assertEqual(score_prov["value"], exp["value"])
		self.assertEqual(score_prov["status"], exp["status"])
		self.assertEqual(score_prov["reason"], exp["reason"])
		self.assertEqual(score_prov["missing_components"], exp["missing_components"])
		self.assertEqual(len(comps_prov), 5)

		# 3. Golden `qa_enabled = true` with `>= 5` samples -> `8700 / 100 = 87.0` (`final`)
		g_fin = golden["golden_process_score_qa_enabled_final"]
		qa_ok = make_rate_metric(
			"QA_PASS_RATE",
			g_fin["inputs"]["qa_points"]["numerator"],
			g_fin["inputs"]["qa_points"]["denominator"],
		)
		score_fin, _ = compute_process_score_from_components(
			first_attempt_sla=fa,
			followup_sla=fu,
			data_hygiene=dh,
			callback_dnc_discipline_rate=80.0,
			callback_dnc_eligible_count=5,
			qa_pass_rate=qa_ok,
			qa_enabled=True,
			qa_sample_count=g_fin["inputs"]["qa_sample_count"],
			eligible_events_total=35,
		)
		self.assertEqual(score_fin["value"], g_fin["expected"]["value"])
		self.assertEqual(score_fin["status"], g_fin["expected"]["status"])
		self.assertEqual(score_fin["missing_components"], [])

		# 4. Insufficient valid weight (`< 60`) -> `value: None, status: "insufficient_data"`
		score_insuf, _ = compute_process_score_from_components(
			first_attempt_sla=make_rate_metric("FIRST_ATTEMPT_SLA_RATE", 0, 0),
			followup_sla=make_rate_metric("FOLLOWUP_SLA_RATE", 0, 0),
			data_hygiene=dh,
			callback_dnc_discipline_rate=None,
			callback_dnc_eligible_count=0,
			qa_pass_rate=qa_disabled,
			qa_enabled=False,
			qa_sample_count=0,
			eligible_events_total=20,
		)
		self.assertIsNone(score_insuf["value"])
		self.assertEqual(score_insuf["status"], "insufficient_data")

		# 5. Period parser boundaries
		p_week = resolve_period_spec("weekly", "2026-W45")
		self.assertEqual((p_week.from_date, p_week.to_date), (date(2026, 11, 2), date(2026, 11, 8)))
		with self.assertRaises(InvalidPeriod):
			resolve_period_spec("custom", from_date="2026-11-10", to_date="2026-11-01")
		with self.assertRaises(InvalidPeriod):
			resolve_period_spec("custom", from_date="2025-01-01", to_date="2026-06-01")

	def test_effective_attempts_no_call_log_double_count_and_aba_distinct_grain(self):
		"""`P3-04`: `CRM Call Log` is never double-counted; `A -> B -> A` yields `1` `DISTINCT Work Item` vs `3` intervals."""
		wi_aba, lead_aba = self.make_assigned_work_item(self.campaign, self.staff_a)

		# Insert an unlinked native `CRM Call Log` on `lead_aba` -> must NOT count as a CSKH Contact Attempt!
		frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": f"CALL-UNLINKED-{frappe.generate_hash(length=8)}",
				"type": "Outgoing",
				"status": "Completed",
				"from": "0900000001",
				"to": "0912345678",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead_aba,
			}
		).insert(ignore_permissions=True)

		# Record 1 attempt with inline `call_log_data` as `staff_a`
		att1 = contact_attempt.record_attempt(
			work_item=wi_aba,
			channel="phone_call",
			outcome_code="no_answer",
			note="Gọi lần 1 không bắt máy",
			call_log_data={
				"type": "Outgoing",
				"status": "No Answer",
				"duration": 0,
				"from": "0900000001",
				"to": "0912345678",
			},
			idempotency_key=f"kpi-aba-1-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)

		# Transfer `A -> B -> A` via `ownership.reassign` (`admin_user`)
		ownership.reassign(
			wi_aba,
			self.staff_a,
			self.staff_b,
			reason="Chuyển ca sang B",
			request_id=f"req-ab-{frappe.generate_hash(length=6)}",
			actor=self.admin_user,
		)
		ownership.reassign(
			wi_aba,
			self.staff_b,
			self.staff_a,
			reason="Bàn giao lại cho A",
			request_id=f"req-ba-{frappe.generate_hash(length=6)}",
			actor=self.admin_user,
		)

		# Record a 2nd attempt as `staff_a` (`connected_interested`) and open a `pending` correction on `att1`
		contact_attempt.record_attempt(
			work_item=wi_aba,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Khách nghe máy và quan tâm gói dịch vụ",
			idempotency_key=f"kpi-aba-2-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)
		correction.request_correction(
			attempt=att1["attempt_id"],
			action="correct",
			corrected_fields={"note": "Cập nhật ghi chú chi tiết hơn"},
			reason="Bổ sung ngữ cảnh cuộc gọi",
			auto_approve=False,
			idempotency_key=f"corr-pend-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)

		cur_d = today_hcm()
		p_daily = resolve_period_spec("daily", cur_d.isoformat())
		res_global = execute_kpi_recompute(p_daily, actor=self.admin_user)
		self.assertEqual(res_global["status"], "completed")
		res_camp = execute_kpi_recompute(p_daily, campaign_filter=self.campaign, actor=self.admin_user)
		self.assertEqual(res_camp["status"], "completed")

		comp = self._get_published_snapshot(
			"daily", cur_d.isoformat(), "company", COMPANY_SCOPE_ID, campaign_filter=self.campaign
		)
		camp = self._get_published_snapshot("daily", cur_d.isoformat(), "campaign", self.campaign)

		# 2 CSKH Contact Attempts (despite 2 CRM Call Logs existing: 1 unlinked + 1 linked -> ZERO double count!)
		self.assertEqual(comp["contact_attempt_count"], 2)
		self.assertEqual(comp["connected_attempt_count"], 1)
		self.assertEqual(
			comp["attempt_breakdown"],
			{
				"attempted": 2,
				"connected": 1,
				"self_reported": 1,
				"verified": 0,
				"disputed": 1,
			},
		)

		# `A -> B -> A` grain invariant: 1 distinct Work Item vs 3 ownership intervals!
		self.assertEqual(comp["distinct_work_items"], 1)
		self.assertEqual(comp["ownership_intervals_count"], 3)
		self.assertEqual(camp["distinct_work_items"], 1)
		self.assertEqual(camp["ownership_intervals_count"], 3)
		self.assertEqual(comp["metrics"]["WORK_ITEM_FIRST_ATTEMPT_SLA_RATE"]["denominator"], 1)

	def test_non_averaging_rollup_and_unattributed_historical_team_bucket(self):
		"""`P3-04`: Team rollup computes `2/4 = 50.0%` (not `(100+33.33)/2`); Monthly dedupes `DISTINCT Work Item`; legacy -> `__unattributed_historical_team__`."""
		# Choose a clean past 2-day window (`today - 5d` and `today - 4d`) in the same ISO year/month if possible
		d2 = today_hcm() - timedelta(days=4)
		d1 = d2 - timedelta(days=1)
		if d1.month != d2.month:
			d1 = d2
			d2 = d1 + timedelta(days=1)

		t_day1 = datetime(d1.year, d1.month, d1.day, 9, 0, 0)
		t_day2 = datetime(d2.year, d2.month, d2.day, 10, 0, 0)

		wi_a, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		wi_b, _ = self.make_assigned_work_item(self.campaign, self.staff_b)

		# Backdate ownership start to `t_day1 - 10m` so attempts on `d1` and `d2` are valid
		for wi in (wi_a, wi_b):
			frappe.db.set_value(
				"CSKH Work Item", wi, "assigned_at", add_to_date(t_day1, minutes=-10), update_modified=False
			)
			frappe.db.sql(
				"update `tabCSKH Assignment History` set ownership_start = %s, event_at = %s where work_item = %s",
				(add_to_date(t_day1, minutes=-10), add_to_date(t_day1, minutes=-10), wi),
			)
		frappe.db.commit()

		# Temporarily widen `allowed_backdate_minutes` across enabled policies so historical `occurred_at` records cleanly
		orig_pols = self._set_enabled_policies({"allowed_backdate_minutes": 60 * 24 * 30})
		try:
			# `staff_a`: 1/1 connected (`100.0%`) on `d1`;
			# `staff_b`: 1/3 connected (`33.33%`: 1 connected on `d1`, 2 non-connected on `d2` on the SAME `wi_b`!).
			contact_attempt.record_attempt(
				work_item=wi_a,
				channel="phone_call",
				outcome_code="connected_interested",
				occurred_at=str(t_day1),
				note="Khách A quan tâm",
				idempotency_key=f"rollup-a1-{frappe.generate_hash(length=6)}",
				actor=self.staff_a,
			)
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="connected_interested",
				occurred_at=str(t_day1),
				note="Khách B nghe máy ngày 1",
				idempotency_key=f"rollup-b1-{frappe.generate_hash(length=6)}",
				actor=self.staff_b,
			)
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="no_answer",
				occurred_at=str(t_day2),
				note="Khách B không bắt máy lần 2",
				idempotency_key=f"rollup-b2-{frappe.generate_hash(length=6)}",
				actor=self.staff_b,
			)
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="busy",
				occurred_at=str(add_to_date(t_day2, minutes=15)),
				note="Khách B máy bận lần 3",
				idempotency_key=f"rollup-b3-{frappe.generate_hash(length=6)}",
				actor=self.staff_b,
			)
		finally:
			self._restore_policies(orig_pols, ("allowed_backdate_minutes",))

		# Also inject 1 pre-P3 legacy `historical_attribution_unknown` obligation anchored on `t_day1`
		wi_leg, _lead_leg = self.make_assigned_work_item(self.campaign, self.staff_c)
		ob_leg_name = frappe.db.get_value(
			"CSKH SLA Obligation", {"work_item": wi_leg, "obligation_type": "FIRST_ATTEMPT"}, "name"
		)
		with authorized_sla_write():
			ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_leg_name)
			ob_doc.team_at_obligation = None
			ob_doc.attribution_status = UNKNOWN_HISTORICAL
			ob_doc.status = "fulfilled_on_time"
			ob_doc.started_at = add_to_date(t_day1, minutes=-20)
			ob_doc.fulfilled_at = t_day1
			ob_doc.flags.ignore_links = True
			ob_doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Recompute `d1`, `d2`, and their `monthly` container scoped to `self.campaign`
		month_key = f"{d1.year:04d}-{d1.month:02d}"
		execute_kpi_recompute(
			resolve_period_spec("daily", d1.isoformat()), campaign_filter=self.campaign, actor=self.admin_user
		)
		execute_kpi_recompute(
			resolve_period_spec("daily", d2.isoformat()), campaign_filter=self.campaign, actor=self.admin_user
		)
		execute_kpi_recompute(
			resolve_period_spec("monthly", month_key), campaign_filter=self.campaign, actor=self.admin_user
		)

		snap_a = self._get_published_snapshot(
			"monthly", month_key, "employee", self.staff_a, campaign_filter=self.campaign
		)
		snap_b = self._get_published_snapshot(
			"monthly", month_key, "employee", self.staff_b, campaign_filter=self.campaign
		)
		snap_team_a = self._get_published_snapshot(
			"monthly", month_key, "team", self.team_a, campaign_filter=self.campaign
		)

		self.assertEqual(snap_a["metrics"]["CONNECTED_RATE"]["value"], 100.0)
		self.assertEqual(snap_b["metrics"]["CONNECTED_RATE"]["value"], 33.33)
		# Non-averaging Team A rollup: `(1 + 1) / (1 + 3) = 2 / 4 = 50.0%` (NEVER `(100 + 33.33)/2 = 66.67%`!)
		self.assertEqual(snap_team_a["metrics"]["CONNECTED_RATE"]["numerator"], 2)
		self.assertEqual(snap_team_a["metrics"]["CONNECTED_RATE"]["denominator"], 4)
		self.assertEqual(snap_team_a["metrics"]["CONNECTED_RATE"]["value"], 50.0)

		# Daily `work_items_touched` for `staff_b`: 1 on `d1` + 1 on `d2` = 2 daily touches, but Monthly `DISTINCT work_item` == 1!
		b_d1 = self._get_published_snapshot(
			"daily", d1.isoformat(), "employee", self.staff_b, campaign_filter=self.campaign
		)
		b_d2 = self._get_published_snapshot(
			"daily", d2.isoformat(), "employee", self.staff_b, campaign_filter=self.campaign
		)
		self.assertEqual(b_d1["work_items_touched"] + b_d2["work_items_touched"], 2)
		self.assertEqual(snap_b["work_items_touched"], 1)

		# Verify `__unattributed_historical_team__` bucket is emitted and counted transparently
		snap_unattr = self._get_published_snapshot(
			"monthly", month_key, "team", UNATTRIBUTED_TEAM_ID, campaign_filter=self.campaign
		)
		self.assertGreaterEqual(snap_unattr["exclusion_counts"]["historical_attribution_unknown"], 1)

	def test_post_commit_dirty_invalidation_and_rollback_safety(self):
		"""`P3-05`: Failed `record_attempt` leaves `0` dirty scopes; committed `record_attempt` writes `CSKH Dirty Scope` post-commit without sync KPI run."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		# Flush `make_assigned_work_item` `after_commit` first, then clear dirty scopes cleanly
		frappe.db.commit()
		frappe.db.sql("delete from `tabCSKH Dirty Scope`")
		frappe.db.commit()

		# 1. Trigger a failing `record_attempt` (`requires_note` outcome `other` without note) -> savepoint rollback!
		with self.assertRaises(Exception):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="other",
				note="",
				idempotency_key=f"fail-dirty-{frappe.generate_hash(length=6)}",
				actor=self.staff_a,
			)
		frappe.db.commit()
		self.assertEqual(frappe.db.count("CSKH Dirty Scope"), 0)

		# 2. Valid `record_attempt` -> commits cleanly with `0` synchronous `CSKH KPI Run` rows, and `>= 1` `pending` `CSKH Dirty Scope`!
		contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Liên hệ thành công",
			idempotency_key=f"ok-dirty-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)
		self.assertEqual(frappe.db.count("CSKH KPI Run"), 0)
		self.assertGreaterEqual(frappe.db.count("CSKH Dirty Scope", {"status": "pending"}), 1)

		# 3. Drain dirty scopes -> publishes `(daily, weekly, monthly, quarterly, yearly)` and reconciles cleanly
		drain_res = process_dirty_scopes()
		self.assertGreaterEqual(drain_res["processed_scopes"], 1)
		self.assertEqual(drain_res["periods_recomputed"], 5)
		self.assertEqual(frappe.db.count("CSKH Dirty Scope", {"status": "pending"}), 0)

		cur_d = today_hcm()
		rec = reconcile_published_period(period_type="daily", period_key=cur_d.isoformat())
		self.assertTrue(rec["ok"])
		self.assertEqual(rec["status"], "reconciled")

	def test_prior_period_correction_restated_run_and_append_only_provenance(self):
		"""`P3-05`: Prior-period approved correction triggers `restated` `CSKH KPI Run` linked via `restated_from_run` while keeping old revision intact."""
		prior_d = today_hcm() - timedelta(days=12)
		prior_dt = datetime(prior_d.year, prior_d.month, prior_d.day, 10, 30, 0)

		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		frappe.db.set_value(
			"CSKH Work Item", wi, "assigned_at", add_to_date(prior_dt, minutes=-15), update_modified=False
		)
		frappe.db.sql(
			"update `tabCSKH Assignment History` set ownership_start = %s, event_at = %s where work_item = %s",
			(add_to_date(prior_dt, minutes=-15), add_to_date(prior_dt, minutes=-15), wi),
		)
		frappe.db.commit()
		orig_pols = self._set_enabled_policies({"allowed_backdate_minutes": 60 * 24 * 30})
		try:
			att = contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="no_answer",
				occurred_at=str(prior_dt),
				note="Lần gọi ngày T-12 ghi nhầm mã",
				idempotency_key=f"rest-att-{frappe.generate_hash(length=6)}",
				actor=self.staff_a,
			)
		finally:
			self._restore_policies(orig_pols, ("allowed_backdate_minutes",))

		frappe.db.commit()
		frappe.db.sql("delete from `tabCSKH Dirty Scope`")
		frappe.db.commit()

		# Initial published run `run_1` on `prior_d` (`self.campaign` has `connected_attempt_count == 0`)
		p_prior = resolve_period_spec("daily", prior_d.isoformat())
		run1 = execute_kpi_recompute(p_prior, run_mode="full", actor=self.admin_user)
		snap1_comp_id = frappe.db.get_value(
			"CSKH KPI Snapshot",
			{"kpi_run": run1["run_id"], "scope_type": "company"},
			"name",
		)
		snap1_camp_id = frappe.db.get_value(
			"CSKH KPI Snapshot",
			{"kpi_run": run1["run_id"], "scope_type": "campaign", "scope_id": self.campaign},
			"name",
		)
		snap1_before = frappe.db.get_value(
			"CSKH KPI Snapshot",
			snap1_comp_id,
			["connected_attempt_count", "is_latest_published"],
			as_dict=True,
		)
		snap1_camp_before = frappe.db.get_value(
			"CSKH KPI Snapshot",
			snap1_camp_id,
			["connected_attempt_count", "is_latest_published"],
			as_dict=True,
		)
		base_connected = int(snap1_before.connected_attempt_count)
		self.assertEqual(snap1_camp_before.connected_attempt_count, 0)
		self.assertEqual(snap1_before.is_latest_published, 1)

		# Staff requests correction -> Leader approves (`no_answer` -> `connected_interested`)
		req = correction.request_correction(
			attempt=att["attempt_id"],
			action="correct",
			corrected_fields={"outcome_code": "connected_interested", "note": "Khách thực tế có nghe máy"},
			reason="Đính chính nhầm kết quả cuộc gọi ngày T-12",
			auto_approve=False,
			idempotency_key=f"rest-req-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)
		rev = correction.review_correction(
			correction_id=req["correction_id"],
			decision="approve",
			review_note="Đã nghe lại ghi âm xác nhận khách có nghe máy",
			idempotency_key=f"rest-rev-{frappe.generate_hash(length=6)}",
			actor=self.leader_a,
		)
		self.assertEqual(rev["status"], "approved")

		# Verify `CSKH Dirty Scope` flagged `is_prior_period = 1` with `trigger_reference = correction_id`
		dirty = frappe.db.get_value(
			"CSKH Dirty Scope",
			{"dirty_date": prior_d.isoformat(), "status": "pending"},
			["is_prior_period", "reason", "trigger_reference"],
			as_dict=True,
		)
		self.assertIsNotNone(dirty)
		self.assertEqual(int(dirty.is_prior_period), 1)
		self.assertEqual(dirty.trigger_reference, req["correction_id"])

		# Drain dirty scopes -> produces `restated` run linked to `run1["run_id"]`!
		process_dirty_scopes()
		run2_row = frappe.db.get_value(
			"CSKH KPI Run",
			{"period_type": "daily", "period_key": prior_d.isoformat(), "is_latest_published": 1},
			["name", "run_mode", "is_restated", "restated_from_run"],
			as_dict=True,
		)
		self.assertNotEqual(run2_row.name, run1["run_id"])
		self.assertEqual(run2_row.run_mode, "restated")
		self.assertEqual(int(run2_row.is_restated), 1)
		self.assertEqual(run2_row.restated_from_run, run1["run_id"])

		# Append-only verification: `snap1_comp_id` & `snap1_camp_id` STILL exist unchanged, `is_latest_published == 0`!
		snap1_after = frappe.db.get_value(
			"CSKH KPI Snapshot",
			snap1_comp_id,
			["connected_attempt_count", "is_latest_published"],
			as_dict=True,
		)
		snap1_camp_after = frappe.db.get_value(
			"CSKH KPI Snapshot",
			snap1_camp_id,
			["connected_attempt_count", "is_latest_published"],
			as_dict=True,
		)
		self.assertEqual(snap1_after.connected_attempt_count, base_connected)
		self.assertEqual(snap1_after.is_latest_published, 0)
		self.assertEqual(snap1_camp_after.connected_attempt_count, 0)
		self.assertEqual(snap1_camp_after.is_latest_published, 0)

		snap2_comp = self._get_published_snapshot("daily", prior_d.isoformat(), "company", COMPANY_SCOPE_ID)
		snap2_camp = self._get_published_snapshot("daily", prior_d.isoformat(), "campaign", self.campaign)
		self.assertEqual(snap2_comp["connected_attempt_count"], base_connected + 1)
		self.assertEqual(snap2_camp["connected_attempt_count"], 1)

	def test_recompute_concurrency_lock_and_backfill_checkpoint_resume(self):
		"""`P3-05`: Concurrent recompute raises `409 RECOMPUTE_IN_PROGRESS`; backfill supports `dry_run`, batching & checkpoint resume."""
		d_end = today_hcm()
		d_start = d_end - timedelta(days=3)
		p_today = resolve_period_spec("daily", d_end.isoformat())

		# 1. Concurrency lock check
		with acquire_recompute_lock(p_today):
			with self.assertRaises(RecomputeInProgress):
				execute_kpi_recompute(p_today, actor=self.admin_user)

		# 2. `dry_run = True` writes `0` `CSKH KPI Snapshot` rows
		dry = run_kpi_backfill(
			d_start,
			d_end,
			period_types=("daily",),
			batch_days=2,
			dry_run=True,
			actor=self.admin_user,
		)
		self.assertTrue(dry["dry_run"])
		self.assertEqual(dry["status"], "completed")
		self.assertEqual(frappe.db.count("CSKH KPI Snapshot"), 0)

		# 3. Partial checkpointed backfill (`batch_days=2`, `stop_after_batches=1` over 4 days -> 2 daily runs)
		part1 = run_kpi_backfill(
			d_start,
			d_end,
			period_types=("daily",),
			batch_days=2,
			dry_run=False,
			stop_after_batches=1,
			actor=self.admin_user,
		)
		self.assertEqual(part1["status"], "partial_checkpointed")
		self.assertEqual(part1["runs_count"], 2)
		self.assertEqual(len(part1["checkpoint"]["completed_period_keys"]), 2)

		# 4. Resume from `part1["checkpoint"]` -> executes ONLY the remaining 2 days!
		part2 = run_kpi_backfill(
			d_start,
			d_end,
			period_types=("daily",),
			batch_days=2,
			dry_run=False,
			resume_checkpoint=part1["checkpoint"],
			actor=self.admin_user,
		)
		self.assertEqual(part2["status"], "completed")
		self.assertEqual(part2["runs_count"], 2)
		self.assertEqual(len(part2["checkpoint"]["completed_period_keys"]), 4)

	def test_qa_disabled_default_and_anti_self_review_when_enabled(self):
		"""`P3-06`: Default `qa_enabled=false` blocks QA writes (`409 QA_DISABLED`, `0` fake data); when enabled, enforces `reviewer != reviewed_staff` (`S06`)."""
		wi_a, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		att_a = contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Khách nghe máy tư vấn đầy đủ",
			idempotency_key=f"qa-att-a-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)
		self.assertEqual(frappe.db.count("CSKH QA Review"), 0)

		# 1. Default `qa_enabled == 0` -> `list_reviews` returns `disabled`, write raises `QaDisabled` (`409`)
		listed_off = qa_svc.list_reviews(actor=self.leader_a)
		self.assertFalse(listed_off["qa_enabled"])
		self.assertEqual(listed_off["status"], "disabled")
		with self.assertRaises(QaDisabled):
			qa_svc.submit_review(att_a["attempt_id"], 90, 100, actor=self.leader_a)

		# 2. Enable `qa_enabled = 1` & `min_qa_sample_size = 1` across enabled policies for security & scoring verification
		orig_pols = self._set_enabled_policies({"qa_enabled": 1, "min_qa_sample_size": 1})
		try:
			# Staff cannot review (`403 ForbiddenScope`)
			with self.assertRaises(ForbiddenScope):
				qa_svc.submit_review(att_a["attempt_id"], 95, 100, actor=self.staff_b)

			# Leader of Team B cannot review Team A's staff (`403 ForbiddenScope`)
			with self.assertRaises(ForbiddenScope):
				qa_svc.submit_review(att_a["attempt_id"], 95, 100, actor=self.leader_b)

			# Anti-self-review (`S06`): even `admin_user` or `leader_a` CANNOT review their OWN attempt!
			self.make_rule(self.campaign, self.leader_a, weight=10)
			wi_lead, _ = self.make_assigned_work_item(self.campaign, self.leader_a)
			att_lead = contact_attempt.record_attempt(
				work_item=wi_lead,
				channel="phone_call",
				outcome_code="connected_interested",
				note="Leader trực tiếp gọi khách VIP",
				idempotency_key=f"qa-att-lead-{frappe.generate_hash(length=6)}",
				actor=self.leader_a,
			)
			with self.assertRaises(QaConflict):
				qa_svc.submit_review(att_lead["attempt_id"], 100, 100, actor=self.leader_a)

			# Authorized cross-person review: `leader_a` reviews `staff_a`'s attempt
			rev_ok = qa_svc.submit_review(
				att_a["attempt_id"],
				points_achieved=92,
				max_points=100,
				feedback_summary="Chào hỏi chuẩn, giải đáp đúng kịch bản",
				internal_note="Ghi chú nội bộ cấp quản lý",
				actor=self.leader_a,
			)
			self.assertTrue(rev_ok["passed"])

			# Recompute today's KPI scoped to `self.campaign` -> `staff_a`'s `QA_PASS_RATE` is `ok` (`92.0%`) and `verified == 1`!
			cur_d = today_hcm()
			execute_kpi_recompute(
				resolve_period_spec("daily", cur_d.isoformat()),
				campaign_filter=self.campaign,
				actor=self.admin_user,
			)
			snap_a = self._get_published_snapshot(
				"daily", cur_d.isoformat(), "employee", self.staff_a, campaign_filter=self.campaign
			)
			self.assertEqual(snap_a["attempt_breakdown"]["verified"], 1)
			self.assertEqual(snap_a["metrics"]["QA_PASS_RATE"]["status"], "ok")
			self.assertEqual(snap_a["metrics"]["QA_PASS_RATE"]["value"], 92.0)

			# Staff sees own published review WITHOUT `internal_note`!
			staff_list = qa_svc.list_reviews(actor=self.staff_a)
			matched = [r for r in staff_list["rows"] if r["contact_attempt"] == att_a["attempt_id"]]
			self.assertEqual(len(matched), 1)
			self.assertNotIn("internal_note", matched[0])
		finally:
			self._restore_policies(orig_pols, ("qa_enabled", "min_qa_sample_size"))

	def test_pr3_doctype_permissions_and_filter_oracles(self):
		"""`S07`, `S13`, `S16`, `S17`: Row-level scope, generic ORM write blocks, run ID probing & `permlevel=1` filter oracles."""
		wi_a, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		wi_b, _ = self.make_assigned_work_item(self.campaign, self.staff_b)
		contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Tác nghiệp A",
			idempotency_key=f"perm-a-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_b,
			channel="phone_call",
			outcome_code="no_answer",
			note="Tác nghiệp B",
			idempotency_key=f"perm-b-{frappe.generate_hash(length=6)}",
			actor=self.staff_b,
		)

		cur_d = today_hcm()
		run_res = execute_kpi_recompute(
			resolve_period_spec("daily", cur_d.isoformat()), actor=self.admin_user
		)
		run_id = run_res["run_id"]

		try:
			# 1. `S17`: Staff cannot probe `get_kpi_run_status` or call `request_scoped_recompute` or read `CSKH KPI Run`
			with self.assertRaises(ForbiddenScope):
				get_kpi_run_status(run_id, actor=self.staff_a)
			with self.assertRaises(ForbiddenScope):
				request_scoped_recompute(
					period_type="daily", period_key=cur_d.isoformat(), actor=self.staff_a
				)

			frappe.set_user(self.staff_a)
			with self.assertRaises(frappe.PermissionError):
				frappe.get_list("CSKH KPI Run")
			with self.assertRaises(frappe.PermissionError):
				frappe.get_list("CSKH Dirty Scope")

			# 2. `S13`: Staff sees ONLY own `employee` snapshot(s) and CANNOT mutate via ORM/generic API
			staff_snaps = frappe.get_list("CSKH KPI Snapshot", fields=["name", "scope_type", "scope_id"])
			self.assertGreaterEqual(len(staff_snaps), 1)
			for s_row in staff_snaps:
				self.assertEqual(s_row.scope_type, "employee")
				self.assertEqual(s_row.scope_id, self.staff_a)

			snap_doc = frappe.get_doc("CSKH KPI Snapshot", staff_snaps[0].name)
			snap_doc.process_score_sort = 100.0
			with self.assertRaises(frappe.PermissionError):
				snap_doc.save()

			# 3. `S16`: Filter-oracle on `permlevel=1` fields (`policy_snapshot_json`, `reconciliation_json`, `internal_note`) blocked!
			with self.assertRaises(frappe.PermissionError):
				frappe.get_list(
					"CSKH SLA Obligation",
					filters=[["CSKH SLA Obligation", "policy_snapshot_json", "like", "%30%"]],
				)

			frappe.set_user(self.leader_a)
			with self.assertRaises(frappe.PermissionError):
				frappe.get_list(
					"CSKH KPI Run",
					filters=[["CSKH KPI Run", "reconciliation_json", "like", "%ok%"]],
				)
		finally:
			frappe.set_user("Administrator")
