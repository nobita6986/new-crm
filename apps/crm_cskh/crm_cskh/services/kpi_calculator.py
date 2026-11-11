"""Bulk-SQL KPI & `PROCESS_SCORE` calculator + reconciliation verifier (`P3-04`, `cskh-metrics-v2`).

Invariants (`docs/p3/kpi-dictionary.md`, `docs/p3/scoring-and-data-sufficiency.md`, `P3.md` §§7.2-7.5):
  * Uses effective `CSKH Contact Attempt` overlays (`approved` corrections `<= as_of`), NEVER counting `CRM Call Log`.
  * Preserves valid P2 `verification_status` (`verified`, `self_reported`, `disputed`) even when `qa_enabled=false`.
  * `denominator == 0` -> `value: null, status: "insufficient_data", reason: "no_eligible_records"`.
  * Count & latency metrics emit `denominator: null` (never `1`).
  * `CONNECTED_RATE` uses `ownership_interval` grain (`connected tried intervals / tried intervals`, excluding `invalid_phone`).
  * SLA rates (`FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `CALLBACK_COMPLETION_RATE`)
    use the half-open `deadline_cohort` (`start_dt <= effective_due_at < end_dt_exclusive` and `effective_due_at <= as_of`),
    evaluated strictly as-of `as_of` so a late call in month `M+1` never erases a breach in month `M`.
  * Company/Campaign `DISTINCT work_item` != Employee/Team `ownership interval` (`A -> B -> A` = 1 Work Item vs 3 intervals).
  * Rollups (`Employee -> Team -> Company`, `Daily -> Monthly -> Yearly`) always recompute from raw numerators/denominators
    and `COUNT(DISTINCT ...)` sets — NEVER averaging percentages.
  * Materializes per-`(employee, team_at_event)` `employee_team` slices so `Leader` views only historical activity belonging
    to their authorized subtree (`team-as-of`).
  * `PROCESS_SCORE` (`0-100`) uses `25%` First-touch SLA + `20%` Eligible coverage + `20%` Valid outcome completeness +
    `20%` Follow-up SLA + `15%` Representative `random_sample` QA pass rate (`7350 / 85 = 86.47` when `qa_enabled=false`).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime

from crm_cskh.p3_models import DEFAULT_P3_POLICY_FIELDS
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL, resolve_team_for_user_at
from crm_cskh.services.calendar import (
	WorkingCalendarSpec,
	business_minutes_between,
	resolve_working_calendar,
)
from crm_cskh.services.errors import DataReconciliationFailed
from crm_cskh.services.kpi_periods import PeriodSpec, iter_days

METRIC_VERSION = "cskh-metrics-v2"
COMPANY_SCOPE_ID = "__company__"
COMPANY_SCOPE_LABEL = "Toàn công ty"
UNATTRIBUTED_TEAM_ID = "__unattributed_historical_team__"
UNATTRIBUTED_TEAM_LABEL = "Chưa xác định nhánh lịch sử (Trước P3)"

ELIGIBLE_SLA_STATUSES = frozenset(
	("fulfilled_on_time", "fulfilled_late", "breached_open", "breached_handoff")
)
BREACH_SLA_STATUSES = frozenset(("fulfilled_late", "breached_open", "breached_handoff"))

METRIC_LABELS_VI: dict[str, str] = {
	"ASSIGNED_INTERVALS": "Lượt nhận hồ sơ (Khoảng phụ trách)",
	"UNIQUE_ASSIGNED_ITEMS": "Hồ sơ liên hệ được giao duy nhất",
	"ELIGIBLE_COVERAGE": "Tỷ lệ phủ liên hệ trên hồ sơ đủ điều kiện",
	"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE": "Tỷ lệ đạt SLA liên hệ lần đầu (Cấp hồ sơ liên hệ)",
	"FIRST_ATTEMPT_SLA_RATE": "Tỷ lệ đạt SLA liên hệ lần đầu (Khoảng phụ trách)",
	"FOLLOWUP_SLA_RATE": "Tỷ lệ đạt SLA lịch hẹn liên hệ lại (Callback)",
	"FIRST_CONNECTED_TRACKING_RATE": "Tỷ lệ kết nối trong ngưỡng theo dõi (Tham khảo)",
	"MIN_ATTEMPTS_COMPLIANCE_RATE": "Tỷ lệ tuân thủ số lần liên hệ tối thiểu",
	"CONTACT_ATTEMPT_COUNT": "Tổng lượt liên hệ hợp lệ (Effective Attempts)",
	"CONNECTED_ATTEMPT_COUNT": "Tổng lượt kết nối hai chiều thành công",
	"CONNECTED_ITEMS": "Hồ sơ đã kết nối hai chiều duy nhất",
	"CONNECTED_RATE": "Tỷ lệ kết nối hai chiều (Cấp khoảng phụ trách)",
	"WORK_ITEMS_TOUCHED": "Số hồ sơ công việc đã tác nghiệp",
	"VALID_OUTCOME_LOGGING_RATE": "Tỷ lệ cập nhật kết quả đầy đủ (Outcome Completeness)",
	"VERIFICATION_RATE": "Tỷ lệ kết quả đã xác minh (Verified)",
	"CONVERSION_RATE": "Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch",
	"CORRECTION_RATE": "Tỷ lệ bản ghi phải đính chính / hủy",
	"VOID_ATTEMPT_RATE": "Tỷ lệ bản ghi liên hệ bị hủy (Void)",
	"CALLBACK_COMPLETION_RATE": "Tỷ lệ hoàn tất lịch hẹn hợp lệ",
	"RESCHEDULE_RATE": "Tỷ lệ dời lịch hẹn (Reschedule)",
	"DNC_COMPLIANCE_RATE": "Tỷ lệ tuân thủ DNC & kỷ luật đóng hồ sơ",
	"OVERDUE_BACKLOG": "Hồ sơ công việc đang tồn quá hạn",
	"OPEN_FOLLOWUPS_OVERDUE": "Lịch hẹn gọi lại đang quá hạn",
	"SLA_BREACH_COUNT": "Tổng số nghĩa vụ vi phạm SLA trong kỳ",
	"SLA_WAIVER_COUNT": "Số nghĩa vụ được miễn trừ hợp lệ",
	"FIRST_ATTEMPT_LATENCY_P50_MINUTES": "Trung vị thời gian phản hồi lần đầu (Phút làm việc - P50)",
	"FIRST_ATTEMPT_LATENCY_P90_MINUTES": "Phân vị P90 thời gian phản hồi lần đầu (Phút làm việc - P90)",
	"QA_PASS_RATE": "Tỷ lệ đạt kiểm định chất lượng QA",
	"PROCESS_SCORE": "Điểm tuân thủ quy trình tổng hợp (0-100)",
}

SCORE_COMPONENT_LABELS_VI: dict[str, str] = {
	"C1_FIRST_ATTEMPT_SLA": "SLA liên hệ lần đầu đúng hạn",
	"ELIGIBLE_COVERAGE": "Tỷ lệ phủ hồ sơ được giao đủ điều kiện",
	"C3_DATA_HYGIENE": "Cập nhật kết quả đầy đủ",
	"FOLLOWUP_SLA_RATE": "SLA liên hệ lại đúng lịch hẹn",
	"C5_QA_QUALITY": "Chất lượng kiểm định QA đại diện",
}

DRILLDOWN_SUPPORTED_METRICS = frozenset(
	(
		"ELIGIBLE_COVERAGE",
		"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE",
		"FIRST_ATTEMPT_SLA_RATE",
		"FOLLOWUP_SLA_RATE",
		"FIRST_CONNECTED_TRACKING_RATE",
		"MIN_ATTEMPTS_COMPLIANCE_RATE",
		"CONTACT_ATTEMPT_COUNT",
		"CONNECTED_ITEMS",
		"CONNECTED_RATE",
		"VALID_OUTCOME_LOGGING_RATE",
		"VERIFICATION_RATE",
		"CONVERSION_RATE",
		"CORRECTION_RATE",
		"VOID_ATTEMPT_RATE",
		"CALLBACK_COMPLETION_RATE",
		"DNC_COMPLIANCE_RATE",
		"OVERDUE_BACKLOG",
		"OPEN_FOLLOWUPS_OVERDUE",
		"SLA_BREACH_COUNT",
		"SLA_WAIVER_COUNT",
	)
)


def round_half_up_2(val: Decimal | float | int) -> float:
	d = val if isinstance(val, Decimal) else Decimal(str(val))
	return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _percentile_sorted(sorted_vals: list[float], pct: float) -> float | None:
	if not sorted_vals:
		return None
	n = len(sorted_vals)
	if n == 1:
		return round_half_up_2(sorted_vals[0])
	pos = (pct / 100.0) * (n - 1)
	lo = math.floor(pos)
	hi = math.ceil(pos)
	if lo == hi:
		return round_half_up_2(sorted_vals[lo])
	frac = pos - lo
	interp = sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])
	return round_half_up_2(interp)


def make_rate_metric(
	metric_code: str,
	numerator: int | float,
	denominator: int | float,
	*,
	informational_only: bool = False,
	disabled: bool = False,
	disabled_reason: str = "policy_disabled_or_no_eligible_records",
	insufficient_reason: str = "no_eligible_records",
) -> dict:
	"""Construct a standardized `percent` `MetricValue` enforcing `denominator == 0 -> null`."""
	label = METRIC_LABELS_VI.get(metric_code, metric_code)
	drill = metric_code in DRILLDOWN_SUPPORTED_METRICS
	if disabled:
		return {
			"metric_code": metric_code,
			"label_vi": label,
			"value": None,
			"numerator": 0,
			"denominator": 0,
			"unit": "percent",
			"status": "disabled",
			"reason": disabled_reason,
			"informational_only": informational_only,
			"drilldown_supported": drill,
		}
	if not denominator or denominator <= 0:
		return {
			"metric_code": metric_code,
			"label_vi": label,
			"value": None,
			"numerator": 0,
			"denominator": 0,
			"unit": "percent",
			"status": "insufficient_data",
			"reason": insufficient_reason,
			"informational_only": informational_only,
			"drilldown_supported": drill,
		}
	pct = round_half_up_2((Decimal(str(numerator)) * Decimal("100")) / Decimal(str(denominator)))
	return {
		"metric_code": metric_code,
		"label_vi": label,
		"value": pct,
		"numerator": numerator,
		"denominator": denominator,
		"unit": "percent",
		"status": "ok",
		"reason": None,
		"informational_only": informational_only,
		"drilldown_supported": drill,
	}


def make_count_metric(metric_code: str, count_val: int, **extra) -> dict:
	payload = {
		"metric_code": metric_code,
		"label_vi": METRIC_LABELS_VI.get(metric_code, metric_code),
		"value": int(count_val),
		"numerator": int(count_val),
		"denominator": None,
		"unit": "count",
		"status": "ok",
		"reason": None,
		"informational_only": False,
		"drilldown_supported": metric_code in DRILLDOWN_SUPPORTED_METRICS,
	}
	payload.update(extra)
	return payload


def make_duration_metric(
	metric_code: str,
	minutes_val: float | None,
	sample_count: int,
	*,
	unit: str = "minutes_business",
	**extra,
) -> dict:
	label = METRIC_LABELS_VI.get(metric_code, metric_code)
	if sample_count <= 0 or minutes_val is None:
		payload = {
			"metric_code": metric_code,
			"label_vi": label,
			"value": None,
			"numerator": 0,
			"denominator": None,
			"sample_count": 0,
			"unit": unit,
			"status": "insufficient_data",
			"reason": "no_eligible_records",
			"informational_only": True,
			"drilldown_supported": False,
		}
		payload.update(extra)
		return payload
	rounded = round_half_up_2(minutes_val)
	payload = {
		"metric_code": metric_code,
		"label_vi": label,
		"value": rounded,
		"numerator": rounded,
		"denominator": None,
		"sample_count": int(sample_count),
		"unit": unit,
		"status": "ok",
		"reason": None,
		"informational_only": True,
		"drilldown_supported": False,
	}
	payload.update(extra)
	return payload


def compute_process_score_from_components(
	*,
	first_attempt_sla: dict,
	eligible_coverage: dict | None = None,
	data_hygiene: dict,
	followup_sla: dict,
	qa_pass_rate: dict,
	qa_enabled: bool,
	qa_sample_count: int,
	eligible_events_total: int,
	policy_cfg: dict | None = None,
	callback_dnc_discipline_rate: float | None = None,
	callback_dnc_eligible_count: int = 0,
) -> tuple[dict, list[dict]]:
	"""Pure `PROCESS_SCORE` (0-100) calculator (`P3.md` §7.5, Golden `7350/85 = 86.47`).

	Default weights (configurable on `CSKH Policy`):
	  1. `C1_FIRST_ATTEMPT_SLA` (`FIRST_ATTEMPT_SLA_RATE`): `25%`
	  2. `ELIGIBLE_COVERAGE` (`ELIGIBLE_COVERAGE`): `20%`
	  3. `C3_DATA_HYGIENE` (`VALID_OUTCOME_LOGGING_RATE`): `20%`
	  4. `FOLLOWUP_SLA_RATE` (`FOLLOWUP_SLA_RATE`): `20%`
	  5. `C5_QA_QUALITY` (`QA_PASS_RATE`, `random_sample >= min_qa_sample_size`): `15%`
	"""
	cfg = dict(DEFAULT_P3_POLICY_FIELDS)
	if policy_cfg:
		cfg.update({k: v for k, v in policy_cfg.items() if v is not None})

	if eligible_coverage is None:
		if callback_dnc_discipline_rate is not None and callback_dnc_eligible_count > 0:
			eligible_coverage = {
				"metric_code": "ELIGIBLE_COVERAGE",
				"value": round_half_up_2(callback_dnc_discipline_rate),
				"numerator": None,
				"denominator": callback_dnc_eligible_count,
				"status": "ok",
			}
		else:
			eligible_coverage = make_rate_metric("ELIGIBLE_COVERAGE", 0, 0)

	w1 = max(cint(cfg.get("weight_first_attempt_sla", 25)), 0)
	w_cov_raw = cfg.get("weight_eligible_coverage")
	if w_cov_raw is None:
		w_cov_raw = cfg.get("weight_callback_discipline", 20)
	w2 = max(cint(w_cov_raw), 0)
	w3 = max(cint(cfg.get("weight_data_hygiene", 20)), 0)
	w4 = max(cint(cfg.get("weight_followup_sla", 20)), 0)
	w5 = max(cint(cfg.get("weight_qa_quality", 15)), 0)
	total_cfg_weight = w1 + w2 + w3 + w4 + w5
	min_valid_weight = max(cint(cfg.get("min_valid_score_weight", 60)), 1)
	min_events = max(cint(cfg.get("min_eligible_events_for_score", 1)), 1)
	min_qa_samples = max(cint(cfg.get("min_qa_sample_size", 5)), 1)

	c5_valid = (
		bool(qa_enabled)
		and qa_sample_count >= min_qa_samples
		and qa_pass_rate.get("status") == "ok"
		and qa_pass_rate.get("value") is not None
	)

	raw_specs = [
		(
			"C1_FIRST_ATTEMPT_SLA",
			"FIRST_ATTEMPT_SLA_RATE",
			SCORE_COMPONENT_LABELS_VI["C1_FIRST_ATTEMPT_SLA"],
			w1,
			first_attempt_sla,
			first_attempt_sla.get("value") if first_attempt_sla.get("status") == "ok" else None,
			first_attempt_sla.get("status", "insufficient_data"),
		),
		(
			"ELIGIBLE_COVERAGE",
			"ELIGIBLE_COVERAGE",
			SCORE_COMPONENT_LABELS_VI["ELIGIBLE_COVERAGE"],
			w2,
			eligible_coverage,
			eligible_coverage.get("value") if eligible_coverage.get("status") == "ok" else None,
			eligible_coverage.get("status", "insufficient_data"),
		),
		(
			"C3_DATA_HYGIENE",
			"VALID_OUTCOME_LOGGING_RATE",
			SCORE_COMPONENT_LABELS_VI["C3_DATA_HYGIENE"],
			w3,
			data_hygiene,
			data_hygiene.get("value") if data_hygiene.get("status") == "ok" else None,
			data_hygiene.get("status", "insufficient_data"),
		),
		(
			"FOLLOWUP_SLA_RATE",
			"FOLLOWUP_SLA_RATE",
			SCORE_COMPONENT_LABELS_VI["FOLLOWUP_SLA_RATE"],
			w4,
			followup_sla,
			followup_sla.get("value") if followup_sla.get("status") == "ok" else None,
			followup_sla.get("status", "insufficient_data"),
		),
		(
			"C5_QA_QUALITY",
			"QA_PASS_RATE",
			SCORE_COMPONENT_LABELS_VI["C5_QA_QUALITY"],
			w5,
			qa_pass_rate,
			qa_pass_rate.get("value") if c5_valid else None,
			"ok" if c5_valid else ("disabled" if not qa_enabled else "insufficient_data"),
		),
	]

	weighted_sum = Decimal("0")
	valid_weight = 0
	missing_codes: list[str] = []
	interim_rows: list[tuple[str, str, str, int, dict, float | None, str, bool, Decimal]] = []

	for code, mkey, title_vi, weight, m_obj, val, comp_status in raw_specs:
		is_participating = weight > 0 and val is not None and comp_status == "ok"
		if is_participating:
			raw_prod = Decimal(str(weight)) * Decimal(str(val))
			weighted_sum += raw_prod
			valid_weight += weight
		else:
			if weight > 0:
				missing_codes.append(code)
			raw_prod = Decimal("0")
		interim_rows.append(
			(code, mkey, title_vi, weight, m_obj, val, comp_status, is_participating, raw_prod)
		)

	score_ready = (
		eligible_events_total >= min_events and valid_weight >= min_valid_weight and valid_weight > 0
	)

	components_breakdown: list[dict] = []
	for code, mkey, title_vi, weight, m_obj, val, comp_status, is_participating, raw_prod in interim_rows:
		# `weighted_points` is explicitly normalized on the `0-100` scale (`[0, weight]`, e.g. `22.50` out of `25`)
		# so UI tables never display `100x` inflated products; `raw_weight_rate_product` preserves `weight * rate` (`2250.0`).
		norm_pts = round_half_up_2(raw_prod / Decimal("100")) if is_participating else None
		reweighted_pts = (
			round_half_up_2(raw_prod / Decimal(str(valid_weight)))
			if (is_participating and score_ready and valid_weight > 0)
			else None
		)
		components_breakdown.append(
			{
				"component_code": code,
				"metric_key": mkey,
				"label_vi": title_vi,
				"weight": weight,
				"configured_weight": weight,
				"rate_value": val,
				"numerator": m_obj.get("numerator") if is_participating else 0,
				"denominator": m_obj.get("denominator") if is_participating else 0,
				"status": comp_status,
				"participates": is_participating,
				"scale": "points_0_100",
				"weighted_points": norm_pts,
				"raw_weighted_points": norm_pts,
				"reweighted_points": reweighted_pts,
				"normalized_contribution": reweighted_pts,
				"raw_weight_rate_product": round_half_up_2(raw_prod) if is_participating else None,
			}
		)

	norm_weighted_sum = round_half_up_2(weighted_sum / Decimal("100"))
	raw_weighted_sum = round_half_up_2(weighted_sum)

	if not score_ready:
		score_metric = {
			"metric_code": "PROCESS_SCORE",
			"label_vi": METRIC_LABELS_VI["PROCESS_SCORE"],
			"value": None,
			"numerator": norm_weighted_sum,
			"raw_points_total": norm_weighted_sum,
			"raw_weight_rate_product": raw_weighted_sum,
			"denominator": float(valid_weight),
			"unit": "score_100",
			"status": "insufficient_data",
			"reason": "insufficient_valid_weight_or_sample",
			"valid_weight": valid_weight,
			"total_configured_weight": total_cfg_weight,
			"configured_weight_total": total_cfg_weight,
			"missing_components": missing_codes,
			"status_label_vi": "Chưa đủ dữ liệu công bố điểm",
			"informational_only": False,
			"drilldown_supported": False,
		}
		return score_metric, components_breakdown

	score_val = round_half_up_2(weighted_sum / Decimal(str(valid_weight)))
	is_full = len(missing_codes) == 0 and valid_weight == total_cfg_weight
	reason_code = (
		None
		if is_full
		else (
			"qa_component_excluded_reweighted"
			if missing_codes == ["C5_QA_QUALITY"]
			else "partial_components_reweighted"
		)
	)
	score_disp_vi = f"{score_val:.2f}".replace(".", ",")
	status_label_vi = (
		f"{score_disp_vi} — Đã chốt"
		if is_full
		else (
			f"{score_disp_vi} — Tạm tính; thiếu mẫu QA"
			if missing_codes == ["C5_QA_QUALITY"]
			else f"{score_disp_vi} — Tạm tính; tái phân bổ trọng số"
		)
	)
	score_metric = {
		"metric_code": "PROCESS_SCORE",
		"label_vi": METRIC_LABELS_VI["PROCESS_SCORE"],
		"value": score_val,
		"numerator": norm_weighted_sum,
		"raw_points_total": norm_weighted_sum,
		"raw_weight_rate_product": raw_weighted_sum,
		"denominator": float(valid_weight),
		"unit": "score_100",
		"status": "final" if is_full else "provisional",
		"reason": reason_code,
		"valid_weight": valid_weight,
		"total_configured_weight": total_cfg_weight,
		"configured_weight_total": total_cfg_weight,
		"missing_components": missing_codes,
		"status_label_vi": status_label_vi,
		"informational_only": False,
		"drilldown_supported": False,
	}
	return score_metric, components_breakdown


def _load_effective_global_policy_cfg() -> dict:
	from crm_cskh.services.policies import resolve_effective_policy

	pol = dict(DEFAULT_P3_POLICY_FIELDS)
	pol.update({k: v for k, v in resolve_effective_policy().items() if v is not None})
	return pol


def _lead_source_column() -> str | None:
	if not frappe.db.table_exists("CRM Lead"):
		return None
	if frappe.db.has_column("CRM Lead", "source"):
		return "source"
	if frappe.db.has_column("CRM Lead", "lead_source"):
		return "lead_source"
	return None


def _in_half_open_window(
	dt_val: datetime, start_dt: datetime, end_dt_excl: datetime, as_of_dt: datetime
) -> bool:
	return start_dt <= dt_val < end_dt_excl and dt_val <= as_of_dt


def load_period_raw_dataset(
	period: PeriodSpec,
	as_of: datetime | str | None = None,
	*,
	campaign_filter: str = "",
	lead_source_filter: str = "",
	employee_filter: str = "",
	drilldown_metric: str = "",
) -> dict:
	"""Execute bounded indexed bulk SQL queries for `period` capped at `as_of` (`0` N+1 queries)."""
	from crm_cskh.services.p3_flags import is_qa_enabled as resolve_is_qa_enabled
	from crm_cskh.services.policies import resolve_effective_policy

	as_of_dt = get_datetime(as_of or now_datetime())
	start_dt = period.start_dt
	end_dt_excl = period.end_dt_exclusive
	cutoff_dt = min(end_dt_excl, as_of_dt)

	policy_cfg = _load_effective_global_policy_cfg()
	qa_enabled = bool(resolve_is_qa_enabled(policy_cfg=policy_cfg))
	min_attempts_enabled = bool(cint(policy_cfg.get("min_attempts_enabled", 0)))
	any_min_attempts_policy = min_attempts_enabled or bool(
		frappe.db.exists("CSKH Policy", {"enabled": 1, "min_attempts_enabled": 1})
		if frappe.db.has_column("CSKH Policy", "min_attempts_enabled")
		else False
	)

	# Preload hierarchy team labels & membership history for fast in-memory team resolution
	team_labels: dict[str, str] = {}
	active_staff_by_team: dict[str, set[str]] = defaultdict(set)
	user_primary_team: dict[str, str] = {}
	if frappe.db.table_exists("CRM Sales Hierarchy"):
		h_fields = ["name", "user", "reports_to", "is_group"]
		if frappe.db.has_column("CRM Sales Hierarchy", "full_name"):
			h_fields.append("full_name")
		h_rows = frappe.db.get_all(
			"CRM Sales Hierarchy",
			fields=h_fields,
		)
		by_node = {r.name: r for r in h_rows}
		for r in h_rows:
			if int(r.is_group or 0) or not r.reports_to:
				label = (
					r.get("full_name")
					or (frappe.db.get_value("User", r.user, "full_name") if r.user else None)
					or r.user
					or r.name
				)
				team_labels[r.name] = str(label)
				if r.user:
					user_primary_team[r.user] = r.name
			elif r.reports_to:
				parent = by_node.get(r.reports_to)
				if parent and r.reports_to not in team_labels:
					p_label = (
						parent.get("full_name")
						or (frappe.db.get_value("User", parent.user, "full_name") if parent.user else None)
						or parent.user
						or r.reports_to
					)
					team_labels[r.reports_to] = str(p_label)
				if r.user:
					active_staff_by_team[r.reports_to].add(r.user)
					user_primary_team[r.user] = r.reports_to

	membership_by_user: dict[str, list[tuple[datetime, datetime | None, str, str]]] = defaultdict(list)
	if frappe.db.table_exists("CSKH Team Membership History"):
		tmh_rows = frappe.db.sql(
			"""
			select user, team_node, team_name_snapshot, effective_from, effective_to
			from `tabCSKH Team Membership History`
			order by effective_from desc, creation desc
			""",
			as_dict=True,
		)
		for m in tmh_rows:
			ef_from = get_datetime(m.effective_from)
			ef_to = get_datetime(m.effective_to) if m.effective_to else None
			membership_by_user[m.user].append(
				(ef_from, ef_to, m.team_node, m.team_name_snapshot or m.team_node)
			)
			if m.team_node and m.team_name_snapshot:
				cur_lbl = team_labels.get(m.team_node, "")
				if not cur_lbl or cur_lbl == m.team_node or "@" in cur_lbl:
					team_labels[m.team_node] = str(m.team_name_snapshot)

	def _fast_team_as_of(user: str | None, dt_val: datetime) -> tuple[str | None, str]:
		if not user:
			return None, UNKNOWN_HISTORICAL
		for ef_from, ef_to, tnode, _ in membership_by_user.get(user, ()):
			if ef_from <= dt_val and (ef_to is None or dt_val < ef_to):
				return tnode, AUTHORITATIVE
		return resolve_team_for_user_at(user, dt_val, allow_live_capture_for_current_tx=False)

	if as_of_dt < start_dt:
		return {
			"period": period,
			"as_of": as_of_dt,
			"cutoff_dt": cutoff_dt,
			"source_watermark": None,
			"policy_cfg": policy_cfg,
			"qa_enabled": qa_enabled,
			"min_attempts_enabled": any_min_attempts_policy,
			"team_labels": team_labels,
			"active_staff_by_team": {k: len(v) for k, v in active_staff_by_team.items()},
			"user_primary_team": user_primary_team,
			"attempts": [],
			"obligations": [],
			"intervals": [],
			"assigned_intervals": [],
			"qa_reviews": [],
			"dnc_since_by_lead": {},
			"campaign_titles": {},
			"campaign_lead_sources": {},
			"user_labels": {},
			"unattributed_work_items_count": 0,
		}

	lead_src_col = _lead_source_column()
	src_filter_clean = (lead_source_filter or "").strip()

	def _lead_src_subclause(lead_expr: str) -> tuple[str, tuple]:
		if not src_filter_clean:
			return "", ()
		if not lead_src_col:
			return " and 1 = 0", ()
		return (
			f" and {lead_expr} in (select name from `tabCRM Lead` where `{lead_src_col}` = %s)",
			(src_filter_clean,),
		)

	camp_clause = " and campaign = %s" if campaign_filter else ""
	camp_params = (campaign_filter,) if campaign_filter else ()
	emp_clean = (employee_filter or "").strip()
	dd_mcode = (drilldown_metric or "").strip().upper()
	is_drilldown = bool(dd_mcode)

	# Preload Outcome Code metadata & Campaign conversion overrides
	valid_outcome_codes: set[str] = set()
	requires_note_codes: set[str] = set()
	conversion_outcome_codes_global: set[str] = {"converted"}
	invalid_phone_codes_global: set[str] = {"wrong_number"}
	if frappe.db.table_exists("CSKH Outcome Code"):
		oc_fields = ["name", "code", "requires_note"]
		for opt_col in ("is_conversion", "is_invalid_phone"):
			if frappe.db.has_column("CSKH Outcome Code", opt_col):
				oc_fields.append(opt_col)
		for oc in frappe.db.get_all("CSKH Outcome Code", fields=oc_fields):
			c_key = str(oc.code or oc.name)
			valid_outcome_codes.add(c_key)
			if cint(oc.requires_note):
				requires_note_codes.add(c_key)
			if cint(oc.get("is_conversion")):
				conversion_outcome_codes_global.add(c_key.lower())
			if cint(oc.get("is_invalid_phone")):
				invalid_phone_codes_global.add(c_key.lower())

	campaign_conversion_map: dict[str, set[str]] = {}
	campaign_policy_map: dict[str, dict] = {}
	campaign_calendar_map: dict[str, WorkingCalendarSpec | None] = {}
	default_calendar: WorkingCalendarSpec | None = None
	try:
		default_calendar = resolve_working_calendar(policy_cfg.get("working_calendar"))
	except Exception:
		default_calendar = None

	if frappe.db.table_exists("CSKH Campaign"):
		c_fields = ["name", "title", "code"]
		if frappe.db.has_column("CSKH Campaign", "conversion_outcome_codes"):
			c_fields.append("conversion_outcome_codes")
		for cr in frappe.db.get_all("CSKH Campaign", fields=c_fields):
			raw_conv = str(cr.get("conversion_outcome_codes") or "").strip()
			if raw_conv:
				campaign_conversion_map[cr.name] = {
					x.strip().lower() for x in raw_conv.split(",") if x.strip()
				} | conversion_outcome_codes_global

	def _campaign_cal_and_min_exp(camp_id: str | None) -> tuple[WorkingCalendarSpec | None, int]:
		key = camp_id or ""
		if key not in campaign_policy_map:
			try:
				cpol = resolve_effective_policy(campaign=camp_id) if camp_id else policy_cfg
			except Exception:
				cpol = policy_cfg
			campaign_policy_map[key] = cpol
			cal_code = cpol.get("working_calendar") or policy_cfg.get("working_calendar")
			try:
				campaign_calendar_map[key] = (
					resolve_working_calendar(cal_code) if cal_code else default_calendar
				)
			except Exception:
				campaign_calendar_map[key] = default_calendar
		cpol = campaign_policy_map[key]
		min_exp = max(cint(cpol.get("min_exposure_business_minutes", 60)), 0)
		return campaign_calendar_map[key], min_exp

	# 1. Bulk query `CSKH Assignment History` intervals overlapping `[start_dt, cutoff_dt]` OR currently open at `as_of_dt`
	has_team_col = frappe.db.has_column("CSKH Assignment History", "team_at_assignment")
	team_sel = (
		"ah.team_at_assignment, ah.attribution_status"
		if has_team_col
		else "null as team_at_assignment, null as attribution_status"
	)
	wi_camp_clause = " and wi.campaign = %s" if campaign_filter else ""
	wi_src_clause, wi_src_params = _lead_src_subclause("wi.lead")
	ah_emp_clause = " and ah.to_user = %s" if emp_clean else ""
	ah_emp_params = (emp_clean,) if emp_clean else ()
	is_iv_cohort_dd = bool(
		is_drilldown
		and dd_mcode
		in (
			"CONNECTED_RATE",
			"ELIGIBLE_COVERAGE",
			"ASSIGNED_INTERVALS",
			"UNIQUE_ASSIGNED_ITEMS",
			"CONVERSION_RATE",
		)
	)
	if is_iv_cohort_dd:
		intervals = frappe.db.sql(
			f"""
			select ah.name, ah.work_item, ah.action, ah.to_user, ah.event_at,
			       ah.ownership_start, ah.ownership_end, ah.creation, ah.modified,
			       {team_sel}, wi.campaign, wi.lead
			from `tabCSKH Assignment History` ah
			inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
			where ah.action in ('assign', 'reassign', 'reopen')
			  and (ah.ownership_start is null or (ah.ownership_start >= %s and ah.ownership_start < %s))
			  and coalesce(ah.ownership_start, ah.event_at) >= %s
			  and coalesce(ah.ownership_start, ah.event_at) < %s
			  and coalesce(ah.ownership_start, ah.event_at) <= %s{wi_camp_clause}{wi_src_clause}{ah_emp_clause}
			order by coalesce(ah.ownership_start, ah.event_at) asc, ah.name asc
			""",
			(
				start_dt,
				cutoff_dt,
				start_dt,
				end_dt_excl,
				as_of_dt,
				*camp_params,
				*wi_src_params,
				*ah_emp_params,
			),
			as_dict=True,
		)
	else:
		intervals = frappe.db.sql(
			f"""
			select ah.name, ah.work_item, ah.action, ah.to_user, ah.event_at,
			       ah.ownership_start, ah.ownership_end, ah.creation, ah.modified,
			       {team_sel}, wi.campaign, wi.lead
			from `tabCSKH Assignment History` ah
			inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
			where ah.action in ('assign', 'reassign', 'reopen')
			  and coalesce(ah.ownership_start, ah.event_at) <= %s
			  and (
			        ah.ownership_end is null
			     or ah.ownership_end >= %s
			      ){wi_camp_clause}{wi_src_clause}{ah_emp_clause}
			order by coalesce(ah.ownership_start, ah.event_at) asc, ah.name asc
			""",
			(as_of_dt, start_dt, *camp_params, *wi_src_params, *ah_emp_params),
			as_dict=True,
		)

	hist_team_map: dict[str, tuple[str | None, str]] = {}
	iv_by_id: dict[str, dict] = {}
	assigned_intervals: list[dict] = []
	for iv in intervals:
		iv_start = get_datetime(iv.ownership_start or iv.event_at)
		iv_end = get_datetime(iv.ownership_end) if iv.ownership_end else None
		if iv.attribution_status:
			t_node, a_stat = iv.team_at_assignment, str(iv.attribution_status)
		else:
			t_node, a_stat = _fast_team_as_of(iv.to_user, iv_start)
		iv["iv_start_dt"] = iv_start
		iv["iv_end_dt"] = iv_end
		iv["in_assigned_window"] = _in_half_open_window(iv_start, start_dt, end_dt_excl, as_of_dt)
		iv["is_open_as_of"] = iv_start <= as_of_dt and (iv_end is None or iv_end > as_of_dt)
		iv["resolved_team"] = t_node
		iv["resolved_attribution"] = a_stat
		hist_team_map[iv.name] = (t_node, a_stat)
		iv_by_id[iv.name] = iv
		if iv["in_assigned_window"]:
			assigned_intervals.append(iv)

	# Also detect Work Items that have an active/closed owner or status != 'queued' WITHOUT any valid opening Assignment History interval (`G16`)
	unattributed_wis: list[dict] = []
	if not is_drilldown and frappe.db.table_exists("CSKH Work Item"):
		unattributed_wis = frappe.db.sql(
			f"""
			select wi.name, wi.campaign, wi.lead, wi.status, wi.creation
			from `tabCSKH Work Item` wi
			where wi.status != 'queued'
			  and wi.creation < %s
			  and wi.creation <= %s
			  and not exists (
			      select 1 from `tabCSKH Assignment History` ah
			      where ah.work_item = wi.name and ah.action in ('assign', 'reassign', 'reopen')
			  ){wi_camp_clause}{wi_src_clause}
			""",
			(end_dt_excl, as_of_dt, *camp_params, *wi_src_params),
			as_dict=True,
		)

	# 2. Bulk query `CSKH SLA Obligation` relevant to:
	#    (a) deadline cohort `[start_dt, end_dt_excl)` (`effective_due_at <= as_of_dt`),
	#    (b) `FOLLOWUP` started in window (for `RESCHEDULE_RATE`),
	#    (c) `FIRST_ATTEMPT` on `assigned_intervals` (for `ELIGIBLE_COVERAGE` `canceled_validly` check),
	#    (d) open overdue obligations at `as_of_dt` (`OVERDUE_BACKLOG` / `OPEN_FOLLOWUPS_OVERDUE`).
	dd_needs_obligations = (not is_drilldown) or (
		dd_mcode
		in (
			"ELIGIBLE_COVERAGE",
			"FIRST_ATTEMPT_SLA_RATE",
			"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE",
			"FOLLOWUP_SLA_RATE",
			"CALLBACK_COMPLETION_RATE",
			"FIRST_CONNECTED_TRACKING_RATE",
			"MIN_ATTEMPTS_COMPLIANCE_RATE",
			"RESCHEDULE_RATE",
			"OVERDUE_BACKLOG",
			"OPEN_FOLLOWUPS_OVERDUE",
			"SLA_BREACH_COUNT",
			"BREACH_COUNT",
			"SLA_WAIVER_COUNT",
		)
	)
	obligations_raw: list[dict] = []
	if dd_needs_obligations and frappe.db.table_exists("CSKH SLA Obligation"):
		ob_src_clause, ob_src_params = _lead_src_subclause("lead")
		ob_emp_clause = " and owner_user = %s" if emp_clean else ""
		ob_emp_params = (emp_clean,) if emp_clean else ()
		ob_type_clause = (
			" and obligation_type = 'FIRST_ATTEMPT'"
			if dd_mcode
			in ("CONNECTED_RATE", "ELIGIBLE_COVERAGE", "ASSIGNED_INTERVALS", "UNIQUE_ASSIGNED_ITEMS")
			else ""
		)
		if dd_mcode == "ELIGIBLE_COVERAGE" and len(assigned_intervals) <= 2500:
			obligations_raw = []
		else:
			obligations_raw = frappe.db.sql(
				f"""
				select name, obligation_type, work_item, lead, campaign, interval_event, owner_user,
				       team_at_obligation, attribution_status, followup_task, status, informational_only,
				       is_recovery_obligation, reschedule_seq, started_at, original_due_at, effective_due_at,
				       is_paused, paused_at, fulfilled_at, fulfilled_by_attempt, breached_at, closed_at,
				       waived_by, waived_at, evaluation_anchor_at, modified
				from `tabCSKH SLA Obligation`
				where started_at <= %s
				  and (
				        (effective_due_at >= %s and effective_due_at < %s)
				     or (started_at >= %s and started_at < %s)
				     or (
				           effective_due_at < %s
				           and (
				                 status in ('open', 'breached_open')
				              or (
				                    status in ('fulfilled_late', 'fulfilled_on_time', 'waived', 'canceled_validly', 'breached_handoff', 'handoff_closed')
				                and coalesce(fulfilled_at, waived_at, closed_at) > %s
				                 )
				               )
				        )
				      ){ob_type_clause}{camp_clause}{ob_src_clause}{ob_emp_clause}
				order by effective_due_at asc, name asc
				""",
				(
					as_of_dt,
					start_dt,
					end_dt_excl,
					start_dt,
					end_dt_excl,
					as_of_dt,
					as_of_dt,
					*camp_params,
					*ob_src_params,
					*ob_emp_params,
				),
				as_dict=True,
			)
		seen_ob_ids = {r.name for r in obligations_raw}
		missing_cov_ivs = sorted(
			{iv.name for iv in assigned_intervals}
			- {r.interval_event for r in obligations_raw if r.interval_event}
		)
		if missing_cov_ivs:
			for idx in range(0, len(missing_cov_ivs), 2000):
				chunk = missing_cov_ivs[idx : idx + 2000]
				ph = ", ".join(["%s"] * len(chunk))
				for extra_ob in frappe.db.sql(
					f"""
					select name, obligation_type, work_item, lead, campaign, interval_event, owner_user,
					       team_at_obligation, attribution_status, followup_task, status, informational_only,
					       is_recovery_obligation, reschedule_seq, started_at, original_due_at, effective_due_at,
					       is_paused, paused_at, fulfilled_at, fulfilled_by_attempt, breached_at, closed_at,
					       waived_by, waived_at, evaluation_anchor_at, modified
					from `tabCSKH SLA Obligation`
					where interval_event in ({ph}) and started_at <= %s{ob_type_clause}{camp_clause}{ob_src_clause}{ob_emp_clause}
					""",
					(*chunk, as_of_dt, *camp_params, *ob_src_params, *ob_emp_params),
					as_dict=True,
				):
					if extra_ob.name not in seen_ob_ids:
						obligations_raw.append(extra_ob)
						seen_ob_ids.add(extra_ob.name)
			obligations_raw.sort(key=lambda r: (get_datetime(r.effective_due_at), str(r.name)))

	# Ensure `iv_by_id` has interval metadata for any obligation whose interval closed before `start_dt`
	missing_ob_ivs = sorted(
		{
			ob.interval_event
			for ob in obligations_raw
			if ob.interval_event and ob.interval_event not in iv_by_id
		}
	)
	if missing_ob_ivs:
		for idx in range(0, len(missing_ob_ivs), 2000):
			chunk = missing_ob_ivs[idx : idx + 2000]
			ph = ", ".join(["%s"] * len(chunk))
			for extra_iv in frappe.db.sql(
				f"""
				select ah.name, ah.work_item, ah.action, ah.to_user, ah.event_at,
				       ah.ownership_start, ah.ownership_end, {team_sel}, wi.campaign, wi.lead
				from `tabCSKH Assignment History` ah
				inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
				where ah.name in ({ph})
				""",
				tuple(chunk),
				as_dict=True,
			):
				iv_s = get_datetime(extra_iv.ownership_start or extra_iv.event_at)
				iv_e = get_datetime(extra_iv.ownership_end) if extra_iv.ownership_end else None
				t_node, a_stat = (
					(extra_iv.team_at_assignment, str(extra_iv.attribution_status))
					if extra_iv.attribution_status
					else _fast_team_as_of(extra_iv.to_user, iv_s)
				)
				extra_iv["iv_start_dt"] = iv_s
				extra_iv["iv_end_dt"] = iv_e
				extra_iv["in_assigned_window"] = False
				extra_iv["is_open_as_of"] = iv_s <= as_of_dt and (iv_e is None or iv_e > as_of_dt)
				extra_iv["resolved_team"] = t_node
				extra_iv["resolved_attribution"] = a_stat
				iv_by_id[extra_iv.name] = extra_iv
				hist_team_map[extra_iv.name] = (t_node, a_stat)

	# 3. Bulk query `CSKH Contact Attempt` + `CSKH Attempt Correction`:
	#    Include attempts in `[start_dt, cutoff_dt]`, attempts moved into window via correction,
	#    AND attempts on `assigned_intervals` / `obligations` up to `as_of_dt` so interval/deadline cohorts evaluate cleanly!
	total_corrs = (
		int(
			frappe.db.sql(
				"select count(*) from `tabCSKH Attempt Correction` where requested_at <= %s",
				(as_of_dt,),
			)[0][0]
			or 0
		)
		if frappe.db.table_exists("CSKH Attempt Correction")
		else 0
	)
	moved_attempt_ids: set[str] = set()
	if total_corrs > 0:
		corr_move_rows = frappe.db.sql(
			"""
			select attempt, corrected_fields_json
			from `tabCSKH Attempt Correction`
			where status = 'approved'
			  and action = 'correct'
			  and coalesce(reviewed_at, creation) <= %s
			  and corrected_fields_json like '%%occurred_at%%'
			""",
			(as_of_dt,),
			as_dict=True,
		)
		for cm in corr_move_rows:
			try:
				p = json.loads(cm.corrected_fields_json or "{}")
				if p.get("occurred_at"):
					occ_dt = get_datetime(p["occurred_at"])
					if start_dt <= occ_dt < end_dt_excl and occ_dt <= as_of_dt:
						moved_attempt_ids.add(cm.attempt)
			except Exception:
				pass

	att_opt_cols = []
	for col in ("conversion_snapshot", "invalid_phone_snapshot", "complete_fields"):
		att_opt_cols.append(col if frappe.db.has_column("CSKH Contact Attempt", col) else f"null as {col}")
	att_opt_sel = ", ".join(att_opt_cols)
	att_src_clause, att_src_params = _lead_src_subclause("lead")
	att_emp_clause = " and (performed_by = %s or owner_at_attempt = %s)" if emp_clean else ""
	att_emp_params = (emp_clean, emp_clean) if emp_clean else ()

	use_sql_interval_agg = bool(
		dd_mcode in ("CONNECTED_RATE", "ELIGIBLE_COVERAGE", "ASSIGNED_INTERVALS", "UNIQUE_ASSIGNED_ITEMS")
		and total_corrs == 0
	)
	iv_agg_map: dict[str, tuple[datetime | None, bool, bool, bool, bool]] = {}

	raw_attempts: list[dict] = []
	corr_rows: list[dict] = []
	attempts_enriched: list[dict] = []
	projected_by_id: dict[str, dict] = {}
	eff_attempts_by_iv_cutoff: dict[str, list[dict]] = defaultdict(list)
	eff_attempts_by_iv_asof: dict[str, list[dict]] = defaultdict(list)

	if use_sql_interval_agg:
		if dd_mcode in ("ASSIGNED_INTERVALS", "UNIQUE_ASSIGNED_ITEMS") or not assigned_intervals:
			agg_rows = []
		else:
			has_inv_col = frappe.db.has_column("CSKH Contact Attempt", "invalid_phone_snapshot")
			inv_codes_sorted = sorted(invalid_phone_codes_global)
			inv_ph = ", ".join(["%s"] * len(inv_codes_sorted)) if inv_codes_sorted else "''"
			inv_expr = (
				f"(coalesce(ca.invalid_phone_snapshot, 0) = 1 or lower(coalesce(ca.outcome_code, '')) in ({inv_ph}))"
				if has_inv_col
				else f"(lower(coalesce(ca.outcome_code, '')) in ({inv_ph}))"
			)
			if len(assigned_intervals) <= 2500:
				iv_names = [iv.name for iv in assigned_intervals]
				ph_ivs = ", ".join(["%s"] * len(iv_names))
				agg_rows = frappe.db.sql(
					f"""
					select ca.ownership_event,
					       min(case when ca.occurred_at <= %s and (ah_iv.ownership_end is null or ca.occurred_at <= ah_iv.ownership_end) then ca.occurred_at else null end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s then 1 else 0 end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s and coalesce(ca.connected_snapshot, 0) = 1 then 1 else 0 end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s and {inv_expr} then 1 else 0 end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s and coalesce(ca.dnc_snapshot, 0) = 1 then 1 else 0 end)
					from `tabCSKH Contact Attempt` ca USE INDEX (idx_cskh_attempt_ownevt_occ)
					inner join `tabCSKH Assignment History` ah_iv on ah_iv.name = ca.ownership_event
					where ca.ownership_event in ({ph_ivs})
					  and coalesce(ca.contact_attempt_snapshot, 1) = 1
					  and ca.recorded_at <= %s
					group by ca.ownership_event
					""",
					(
						as_of_dt,
						end_dt_excl,
						as_of_dt,
						end_dt_excl,
						as_of_dt,
						end_dt_excl,
						as_of_dt,
						*inv_codes_sorted,
						end_dt_excl,
						as_of_dt,
						*iv_names,
						as_of_dt,
					),
					as_dict=False,
				)
			else:
				ca_camp_clause = " and ca.campaign = %s" if campaign_filter else ""
				ca_src_clause, ca_src_params = _lead_src_subclause("ca.lead")
				ca_emp_clause = " and ah_iv.to_user = %s" if emp_clean else ""
				ca_emp_params = (emp_clean,) if emp_clean else ()
				agg_rows = frappe.db.sql(
					f"""
					select ca.ownership_event,
					       min(case when ca.occurred_at <= %s and (ah_iv.ownership_end is null or ca.occurred_at <= ah_iv.ownership_end) then ca.occurred_at else null end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s then 1 else 0 end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s and coalesce(ca.connected_snapshot, 0) = 1 then 1 else 0 end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s and {inv_expr} then 1 else 0 end),
					       max(case when ca.occurred_at < %s and ca.occurred_at <= %s and coalesce(ca.dnc_snapshot, 0) = 1 then 1 else 0 end)
					from `tabCSKH Contact Attempt` ca
					inner join `tabCSKH Assignment History` ah_iv on ah_iv.name = ca.ownership_event
					where ca.ownership_event is not null
					  and coalesce(ca.contact_attempt_snapshot, 1) = 1
					  and ca.recorded_at <= %s
					  and coalesce(ah_iv.ownership_start, ah_iv.event_at) <= %s
					  and (ah_iv.ownership_end is null or ah_iv.ownership_end >= %s){ca_camp_clause}{ca_src_clause}{ca_emp_clause}
					group by ca.ownership_event
					""",
					(
						as_of_dt,
						end_dt_excl,
						as_of_dt,
						end_dt_excl,
						as_of_dt,
						end_dt_excl,
						as_of_dt,
						*inv_codes_sorted,
						end_dt_excl,
						as_of_dt,
						as_of_dt,
						as_of_dt,
						start_dt,
						*camp_params,
						*ca_src_params,
						*ca_emp_params,
					),
					as_dict=False,
				)
		for oe_val, min_occ_val, has_att_v, has_conn_v, has_inv_v, has_dnc_v in agg_rows:
			iv_agg_map[str(oe_val)] = (
				get_datetime(min_occ_val) if min_occ_val else None,
				bool(has_att_v),
				bool(has_conn_v),
				bool(has_inv_v),
				bool(has_dnc_v),
			)
	else:
		raw_attempts = frappe.db.sql(
			f"""
			select name, work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
			       occurred_at, recorded_at, channel, outcome_code, outcome_label_snapshot,
			       outcome_group_snapshot, outcome_version_snapshot, contact_attempt_snapshot,
			       connected_snapshot, interested_snapshot, terminal_snapshot, dnc_snapshot,
			       {att_opt_sel},
			       note, call_log, callback_task, resolved_callback_task, verification_status,
			       policy_name_snapshot, policy_version_snapshot, creation, modified
			from `tabCSKH Contact Attempt`
			where occurred_at >= %s and occurred_at < %s and occurred_at <= %s and recorded_at <= %s{camp_clause}{att_src_clause}{att_emp_clause}
			order by occurred_at asc, name asc
			""",
			(start_dt, end_dt_excl, as_of_dt, as_of_dt, *camp_params, *att_src_params, *att_emp_params),
			as_dict=True,
		)
		seen_att_ids = {r.name for r in raw_attempts}

		# Load attempts on `assigned_intervals` or `obligations` that occurred OUTSIDE `[start_dt, min(end_dt_excl, as_of_dt)]`
		needed_iv_ids = sorted(
			{iv.name for iv in assigned_intervals}
			| {ob.interval_event for ob in obligations_raw if ob.interval_event}
		)
		needed_iv_set = set(needed_iv_ids)
		if needed_iv_ids:
			for idx in range(0, len(needed_iv_ids), 2000):
				chunk = needed_iv_ids[idx : idx + 2000]
				ph = ", ".join(["%s"] * len(chunk))
				for extra_a in frappe.db.sql(
					f"""
					select name, work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
					       occurred_at, recorded_at, channel, outcome_code, outcome_label_snapshot,
					       outcome_group_snapshot, outcome_version_snapshot, contact_attempt_snapshot,
					       connected_snapshot, interested_snapshot, terminal_snapshot, dnc_snapshot,
					       {att_opt_sel},
					       note, call_log, callback_task, resolved_callback_task, verification_status,
					       policy_name_snapshot, policy_version_snapshot, creation, modified
					from `tabCSKH Contact Attempt`
					where ownership_event in ({ph})
					  and recorded_at <= %s
					  and (occurred_at < %s or occurred_at >= %s or occurred_at > %s){camp_clause}{att_src_clause}
					""",
					(*chunk, as_of_dt, start_dt, end_dt_excl, as_of_dt, *camp_params, *att_src_params),
					as_dict=True,
				):
					if extra_a.name not in seen_att_ids:
						raw_attempts.append(extra_a)
						seen_att_ids.add(extra_a.name)

		missing_moved = sorted(
			(
				moved_attempt_ids
				| {ob.fulfilled_by_attempt for ob in obligations_raw if ob.fulfilled_by_attempt}
			)
			- seen_att_ids
		)
		if missing_moved:
			for idx in range(0, len(missing_moved), 2000):
				chunk = missing_moved[idx : idx + 2000]
				ph = ", ".join(["%s"] * len(chunk))
				for extra_a in frappe.db.sql(
					f"""
					select name, work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
					       occurred_at, recorded_at, channel, outcome_code, outcome_label_snapshot,
					       outcome_group_snapshot, outcome_version_snapshot, contact_attempt_snapshot,
					       connected_snapshot, interested_snapshot, terminal_snapshot, dnc_snapshot,
					       {att_opt_sel},
					       note, call_log, callback_task, resolved_callback_task, verification_status,
					       policy_name_snapshot, policy_version_snapshot, creation, modified
					from `tabCSKH Contact Attempt`
					where name in ({ph}) and recorded_at <= %s{camp_clause}{att_src_clause}
					""",
					(*chunk, as_of_dt, *camp_params, *att_src_params),
					as_dict=True,
				):
					if extra_a.name not in seen_att_ids:
						raw_attempts.append(extra_a)
						seen_att_ids.add(extra_a.name)

		if seen_att_ids and total_corrs > 0:
			if total_corrs <= 5000:
				all_corrs = frappe.db.sql(
					"""
					select name, work_item, attempt, action, status, requested_by, requested_at,
					       reason, original_snapshot_json, corrected_fields_json,
					       reviewed_by, reviewed_at, review_note, creation, modified
					from `tabCSKH Attempt Correction`
					where requested_at <= %s
					""",
					(as_of_dt,),
					as_dict=True,
				)
				corr_rows = [c for c in all_corrs if c.attempt in seen_att_ids]
			else:
				att_list = sorted(seen_att_ids)
				for idx in range(0, len(att_list), 2000):
					chunk = att_list[idx : idx + 2000]
					ph = ", ".join(["%s"] * len(chunk))
					corr_rows.extend(
						frappe.db.sql(
							f"""
							select name, work_item, attempt, action, status, requested_by, requested_at,
							       reason, original_snapshot_json, corrected_fields_json,
							       reviewed_by, reviewed_at, review_note, creation, modified
							from `tabCSKH Attempt Correction`
							where attempt in ({ph}) and requested_at <= %s
							""",
							(*chunk, as_of_dt),
							as_dict=True,
						)
					)

		as_of_corrs = []
		for c in corr_rows:
			c_copy = frappe._dict(c)
			if (
				c_copy.status == "approved"
				and c_copy.reviewed_at
				and get_datetime(c_copy.reviewed_at) > as_of_dt
			):
				c_copy.status = "pending"
			elif (
				c_copy.status == "rejected"
				and c_copy.reviewed_at
				and get_datetime(c_copy.reviewed_at) > as_of_dt
			):
				c_copy.status = "pending"
			as_of_corrs.append(c_copy)

		from crm_cskh.services.read_model import _project_attempts_rows

		projected_all = _project_attempts_rows(raw_attempts, as_of_corrs)

		missing_hist_ids = sorted(
			{
				p["ownership_event"]
				for p in projected_all
				if p.get("ownership_event") and p["ownership_event"] not in hist_team_map
			}
		)
		if missing_hist_ids and has_team_col:
			for idx in range(0, len(missing_hist_ids), 2000):
				chunk = missing_hist_ids[idx : idx + 2000]
				ph = ", ".join(["%s"] * len(chunk))
				for eh in frappe.db.sql(
					f"""
					select name, to_user, ownership_start, event_at, team_at_assignment, attribution_status
					from `tabCSKH Assignment History`
					where name in ({ph})
					""",
					tuple(chunk),
					as_dict=True,
				):
					if eh.attribution_status:
						hist_team_map[eh.name] = (eh.team_at_assignment, str(eh.attribution_status))
					else:
						hist_team_map[eh.name] = _fast_team_as_of(
							eh.to_user, get_datetime(eh.ownership_start or eh.event_at)
						)

		for p in projected_all:
			eff_occ = get_datetime(p["effective"]["occurred_at"])
			raw_occ = get_datetime(p["original"]["occurred_at"])
			in_eff_window = _in_half_open_window(eff_occ, start_dt, end_dt_excl, as_of_dt)
			in_raw_window = _in_half_open_window(raw_occ, start_dt, end_dt_excl, as_of_dt)

			actor_user = p.get("performed_by") or p.get("owner_at_attempt")
			oe = p.get("ownership_event")
			if oe and oe in hist_team_map:
				t_node, a_stat = hist_team_map[oe]
			else:
				t_node, a_stat = _fast_team_as_of(actor_user, eff_occ)

			eff_code = str(p["effective"].get("outcome_code") or "")
			eff_code_lower = eff_code.lower()
			camp_id = p.get("campaign") or ""
			camp_conv_set = campaign_conversion_map.get(camp_id, conversion_outcome_codes_global)

			is_conv = bool(p["effective"].get("is_conversion")) or (eff_code_lower in camp_conv_set)
			is_inv = bool(p["effective"].get("is_invalid_phone")) or (
				eff_code_lower in invalid_phone_codes_global
			)
			p["effective"]["is_conversion"] = is_conv
			p["effective"]["is_invalid_phone"] = is_inv

			req_note = eff_code in requires_note_codes
			has_note_ok = (not req_note) or bool(str(p["effective"].get("note") or "").strip())
			code_valid = (not valid_outcome_codes) or (eff_code in valid_outcome_codes)
			channel_valid = bool(str(p["effective"].get("channel") or "").strip())
			complete_ok = (
				bool(p["effective"].get("complete_fields", True))
				and code_valid
				and channel_valid
				and has_note_ok
			)

			# F-P3-03: Preserve authoritative P2 `CSKH Contact Attempt.verification_status` (`verified`, `self_reported`, `disputed`)!
			raw_ver = str(p.get("verification_status") or "self_reported").lower()
			is_disputed = raw_ver == "disputed"
			is_verified = raw_ver == "verified"

			p["eff_occ_dt"] = eff_occ
			p["in_effective_window"] = in_eff_window
			p["in_raw_window"] = in_raw_window
			p["in_cutoff_window"] = eff_occ < end_dt_excl and eff_occ <= as_of_dt
			p["actor_user"] = actor_user
			p["resolved_team"] = t_node
			p["resolved_attribution"] = a_stat
			p["note_Policy_ok"] = has_note_ok
			p["complete_fields_ok"] = complete_ok
			p["is_verified_state"] = is_verified
			p["is_disputed_state"] = is_disputed
			projected_by_id[p["attempt_id"]] = p

			if not p["is_voided"] and bool(p["effective"].get("is_contact_attempt", True)):
				if oe and eff_occ <= as_of_dt:
					eff_attempts_by_iv_asof[oe].append(p)
					if eff_occ < end_dt_excl:
						eff_attempts_by_iv_cutoff[oe].append(p)

			if in_eff_window or in_raw_window or (oe in needed_iv_set and eff_occ <= as_of_dt):
				attempts_enriched.append(p)

		attempts_enriched.sort(key=lambda x: (x["eff_occ_dt"], str(x["attempt_id"])))

		for oe_key in eff_attempts_by_iv_asof:
			eff_attempts_by_iv_asof[oe_key].sort(key=lambda x: (x["eff_occ_dt"], x["attempt_id"]))
		for oe_key in eff_attempts_by_iv_cutoff:
			eff_attempts_by_iv_cutoff[oe_key].sort(key=lambda x: (x["eff_occ_dt"], x["attempt_id"]))

	# 4. Bulk query `CSKH QA Review` when `qa_enabled` (or `QA_PASS_RATE` drilldown)
	qa_by_attempt: dict[str, dict] = {}
	qa_reviews: list[dict] = []
	if (
		(qa_enabled or dd_mcode == "QA_PASS_RATE")
		and (not is_drilldown or dd_mcode == "QA_PASS_RATE")
		and frappe.db.table_exists("CSKH QA Review")
	):
		has_cohort_col = frappe.db.has_column("CSKH QA Review", "cohort_type")
		cohort_sel = "cohort_type" if has_cohort_col else "'random_sample' as cohort_type"
		qa_src_clause, qa_src_params = (
			(
				" and work_item in (select wi2.name from `tabCSKH Work Item` wi2 inner join `tabCRM Lead` l2 on l2.name = wi2.lead"
				f" where l2.`{lead_src_col}` = %s)",
				(src_filter_clean,),
			)
			if (src_filter_clean and lead_src_col)
			else (" and 1 = 0", ())
			if src_filter_clean
			else ("", ())
		)
		qa_emp_clause = " and reviewed_staff = %s" if emp_clean else ""
		qa_emp_params = (emp_clean,) if emp_clean else ()
		qa_reviews = frappe.db.sql(
			f"""
			select name, contact_attempt, work_item, campaign, reviewed_staff, team_at_review,
			       reviewer, status, {cohort_sel}, points_achieved, max_points, passed, reviewed_at, modified
			from `tabCSKH QA Review`
			where reviewed_at >= %s and reviewed_at < %s and reviewed_at <= %s
			  and status in ('published', 'approved', 'disputed'){camp_clause}{qa_src_clause}{qa_emp_clause}
			""",
			(start_dt, end_dt_excl, as_of_dt, *camp_params, *qa_src_params, *qa_emp_params),
			as_dict=True,
		)
		if not is_drilldown and projected_by_id:
			att_list = sorted(projected_by_id.keys())
			for idx in range(0, len(att_list), 2000):
				chunk = att_list[idx : idx + 2000]
				ph = ", ".join(["%s"] * len(chunk))
				for qr in frappe.db.sql(
					f"""
					select contact_attempt, status, passed, reviewed_at
					from `tabCSKH QA Review`
					where contact_attempt in ({ph}) and reviewed_at <= %s
					""",
					(*chunk, as_of_dt),
					as_dict=True,
				):
					qa_by_attempt[qr.contact_attempt] = qr

	# Evaluate `obligations` strictly on `deadline_cohort` (`effective_due_at in [start_dt, end_dt_excl)`) and as-of `as_of_dt`!
	obligations: list[dict] = []
	fa_ob_by_iv: dict[str, dict] = {}
	for ob in obligations_raw:
		if ob.status == "superseded":
			continue
		eff_due = get_datetime(ob.effective_due_at)
		started_dt = get_datetime(ob.started_at)
		iv_obj = iv_by_id.get(ob.interval_event or "")
		iv_end = iv_obj["iv_end_dt"] if iv_obj else None

		# Determine `as_of_status` strictly at `as_of_dt` (`F-P3-02`)
		if ob.status == "canceled_validly" and (not ob.closed_at or get_datetime(ob.closed_at) <= as_of_dt):
			as_of_status = "canceled_validly"
			f_dt = None
		elif ob.status == "waived" and (not ob.waived_at or get_datetime(ob.waived_at) <= as_of_dt):
			as_of_status = "waived"
			f_dt = None
		else:
			f_dt: datetime | None = None
			if ob.obligation_type == "FIRST_ATTEMPT" and ob.interval_event:
				if use_sql_interval_agg:
					agg_tup = iv_agg_map.get(ob.interval_event)
					if agg_tup and agg_tup[0] is not None:
						f_dt = agg_tup[0]
				else:
					cand_atts = [
						a
						for a in eff_attempts_by_iv_asof.get(ob.interval_event, ())
						if (iv_end is None or a["eff_occ_dt"] <= iv_end)
					]
					if cand_atts:
						f_dt = cand_atts[0]["eff_occ_dt"]
			if f_dt is None and ob.fulfilled_by_attempt and ob.fulfilled_by_attempt in projected_by_id:
				fa_proj = projected_by_id[ob.fulfilled_by_attempt]
				if not fa_proj["is_voided"] and fa_proj["eff_occ_dt"] <= as_of_dt:
					f_dt = fa_proj["eff_occ_dt"]
			elif f_dt is None and (not ob.fulfilled_by_attempt or use_sql_interval_agg) and ob.fulfilled_at:
				cand_f = get_datetime(ob.fulfilled_at)
				if cand_f <= as_of_dt:
					f_dt = cand_f

			if f_dt is not None:
				as_of_status = "fulfilled_on_time" if f_dt <= eff_due else "fulfilled_late"
			elif (
				cint(ob.is_paused)
				and ob.status == "open"
				and (not ob.paused_at or get_datetime(ob.paused_at) <= as_of_dt)
			):
				as_of_status = "open"
			elif eff_due <= as_of_dt:
				closed_before = bool(
					(iv_end and iv_end <= as_of_dt)
					or (
						ob.status == "breached_handoff"
						and ob.closed_at
						and get_datetime(ob.closed_at) <= as_of_dt
					)
				)
				as_of_status = "breached_handoff" if closed_before else "breached_open"
			else:
				as_of_status = "open"

		in_deadline_cohort = start_dt <= eff_due < end_dt_excl
		in_started_window = _in_half_open_window(started_dt, start_dt, end_dt_excl, as_of_dt)
		iv_open_as_of = bool(iv_obj["is_open_as_of"]) if iv_obj else (ob.status in ("open", "breached_open"))
		is_open_overdue_as_of = bool(
			not cint(ob.informational_only)
			and iv_open_as_of
			and eff_due < as_of_dt
			and as_of_status == "breached_open"
		)

		a_stat = ob.attribution_status or AUTHORITATIVE
		t_node = ob.team_at_obligation
		if (
			ob.obligation_type == "FIRST_CONNECTED"
			and iv_obj
			and ob.owner_user
			and iv_obj.get("to_user")
			and ob.owner_user != iv_obj.get("to_user")
		):
			t_node, a_stat = _fast_team_as_of(ob.owner_user, as_of_dt)
		elif not t_node and a_stat != UNKNOWN_HISTORICAL:
			t_node, a_stat = _fast_team_as_of(ob.owner_user, started_dt)

		ob["eff_due_dt"] = eff_due
		ob["as_of_fulfilled_dt"] = f_dt
		ob["as_of_status"] = as_of_status
		ob["in_deadline_cohort"] = in_deadline_cohort
		ob["in_due_window"] = in_deadline_cohort
		ob["in_anchor_window"] = in_deadline_cohort
		ob["in_started_window"] = in_started_window
		ob["is_open_overdue_as_of"] = is_open_overdue_as_of
		ob["resolved_team"] = t_node
		ob["resolved_attribution"] = a_stat
		obligations.append(ob)
		if ob.obligation_type == "FIRST_ATTEMPT" and ob.interval_event:
			fa_ob_by_iv[ob.interval_event] = ob

	# Enrich `assigned_intervals` with `ELIGIBLE_COVERAGE`, `CONNECTED_RATE`, and first-attempt latency metrics!
	for iv in assigned_intervals:
		fa_ob = fa_ob_by_iv.get(iv.name)
		fa_canceled = bool(fa_ob and fa_ob["as_of_status"] == "canceled_validly")
		fa_matured_by_sla = bool(
			fa_ob and fa_ob["as_of_status"] in ("breached_open", "breached_handoff", "waived")
		)
		if use_sql_interval_agg:
			agg_tup = iv_agg_map.get(iv.name)
			if agg_tup:
				_, has_att, has_conn, has_inv, has_dnc = agg_tup
			else:
				has_att = has_conn = has_inv = has_dnc = False
			iv_al = []
		else:
			iv_al = eff_attempts_by_iv_cutoff.get(iv.name, [])
			has_att = len(iv_al) > 0
			has_conn = any(bool(a["effective"].get("is_connected")) for a in iv_al)
			has_inv = any(bool(a["effective"].get("is_invalid_phone")) for a in iv_al)
			has_dnc = any(bool(a["effective"].get("is_do_not_contact")) for a in iv_al)

		if (not fa_canceled) and (not has_att) and (not fa_matured_by_sla):
			cal_spec, min_exp_mins = _campaign_cal_and_min_exp(iv.campaign)
			exp_end = min(iv["iv_end_dt"] or cutoff_dt, cutoff_dt)
			if cal_spec is not None and exp_end > iv["iv_start_dt"]:
				exp_biz_mins = float(business_minutes_between(iv["iv_start_dt"], exp_end, cal_spec))
			else:
				exp_biz_mins = max((exp_end - iv["iv_start_dt"]).total_seconds() / 60.0, 0.0)
			cov_eligible = exp_biz_mins >= min_exp_mins
		else:
			cov_eligible = not fa_canceled

		iv["has_attempt_cutoff"] = has_att
		iv["has_connected_cutoff"] = has_conn
		iv["has_invalid_phone_cutoff"] = has_inv
		iv["has_dnc_cutoff"] = has_dnc
		iv["coverage_eligible"] = cov_eligible
		iv["tried_for_connected_rate"] = has_att and (not has_inv)
		if has_att and not is_drilldown:
			cal_spec, _ = _campaign_cal_and_min_exp(iv.campaign)
			first_att_dt = max(iv_al[0]["eff_occ_dt"], iv["iv_start_dt"])
			iv["first_attempt_wall_minutes"] = max(
				(first_att_dt - iv["iv_start_dt"]).total_seconds() / 60.0, 0.0
			)
			iv["first_attempt_biz_minutes"] = (
				float(business_minutes_between(iv["iv_start_dt"], first_att_dt, cal_spec))
				if cal_spec is not None
				else iv["first_attempt_wall_minutes"]
			)
		else:
			iv["first_attempt_wall_minutes"] = None
			iv["first_attempt_biz_minutes"] = None

	# 5. Check Lead DNC timestamps & Lead Source labels
	dnc_since_by_lead: dict[str, datetime] = {}
	lead_source_by_lead: dict[str, str] = {}
	need_lead_meta = (not is_drilldown) or (dd_mcode == "DNC_COMPLIANCE_RATE")
	if need_lead_meta:
		lead_ids = sorted(
			{iv.lead for iv in intervals if iv.lead}
			| {a["lead"] for a in attempts_enriched if a.get("lead")}
			| {ob.lead for ob in obligations if ob.lead}
		)
		if lead_ids and frappe.db.table_exists("CRM Lead"):
			has_dnc = frappe.db.has_column("CRM Lead", "cskh_do_not_contact")
			dnc_col = "cskh_dnc_at" if frappe.db.has_column("CRM Lead", "cskh_dnc_at") else "modified"
			for idx in range(0, len(lead_ids), 2000):
				chunk = lead_ids[idx : idx + 2000]
				ph = ", ".join(["%s"] * len(chunk))
				sel_parts = ["name"]
				if has_dnc:
					sel_parts.append("coalesce(cskh_do_not_contact, 0) as is_dnc")
					sel_parts.append(f"{dnc_col} as dnc_at")
				if lead_src_col and not is_drilldown:
					sel_parts.append(f"`{lead_src_col}` as lead_src")
				for lr in frappe.db.sql(
					f"select {', '.join(sel_parts)} from `tabCRM Lead` where name in ({ph})",
					tuple(chunk),
					as_dict=True,
				):
					if has_dnc and cint(lr.is_dnc) and lr.dnc_at:
						dnc_since_by_lead[lr.name] = get_datetime(lr.dnc_at)
					if lead_src_col and not is_drilldown and lr.get("lead_src"):
						lead_source_by_lead[lr.name] = str(lr.lead_src)

	campaign_lead_sources: dict[str, str] = {}
	campaign_titles: dict[str, str] = {}
	user_labels: dict[str, str] = {}
	watermark = as_of_dt

	if not is_drilldown:
		sources_by_camp: dict[str, set[str]] = defaultdict(set)
		for iv in assigned_intervals:
			if iv.campaign and iv.lead and iv.lead in lead_source_by_lead:
				sources_by_camp[iv.campaign].add(lead_source_by_lead[iv.lead])
		for cid, s_set in sources_by_camp.items():
			campaign_lead_sources[cid] = ", ".join(sorted(s_set))

		# Compute `source_watermark` capped by `as_of_dt`
		wm_candidates: list[datetime] = []
		for a in raw_attempts:
			if a.get("recorded_at"):
				wm_candidates.append(get_datetime(a.recorded_at))
		for c in corr_rows:
			if c.get("reviewed_at"):
				wm_candidates.append(get_datetime(c.reviewed_at))
			elif c.get("requested_at"):
				wm_candidates.append(get_datetime(c.requested_at))
		for ob in obligations:
			if ob.get("as_of_fulfilled_dt"):
				wm_candidates.append(get_datetime(ob["as_of_fulfilled_dt"]))
			elif ob.get("eff_due_dt") and ob["eff_due_dt"] <= as_of_dt:
				wm_candidates.append(ob["eff_due_dt"])
			elif ob.get("started_at"):
				wm_candidates.append(get_datetime(ob.started_at))
		for iv in intervals:
			if iv.get("event_at"):
				wm_candidates.append(get_datetime(iv.event_at))
		bounded_wms = [w for w in wm_candidates if w <= as_of_dt]
		watermark = max(bounded_wms) if bounded_wms else as_of_dt

		camp_ids = sorted(
			{iv.campaign for iv in intervals if iv.campaign}
			| {ob.campaign for ob in obligations if ob.campaign}
			| {a["campaign"] for a in attempts_enriched if a.get("campaign")}
		)
		if camp_ids and frappe.db.table_exists("CSKH Campaign"):
			for cr in frappe.db.get_all(
				"CSKH Campaign", filters={"name": ["in", camp_ids]}, fields=["name", "title", "code"]
			):
				campaign_titles[cr.name] = str(cr.title or cr.code or cr.name)

		user_ids = sorted(
			{iv.to_user for iv in intervals if iv.to_user}
			| {ob.owner_user for ob in obligations if ob.owner_user}
			| {a["actor_user"] for a in attempts_enriched if a.get("actor_user")}
			| {qr.reviewed_staff for qr in qa_reviews if qr.get("reviewed_staff")}
			| set(user_primary_team.keys())
		)
		if user_ids:
			for ur in frappe.db.get_all(
				"User", filters={"name": ["in", user_ids]}, fields=["name", "full_name"]
			):
				user_labels[ur.name] = str(ur.full_name or ur.name)

	return {
		"period": period,
		"as_of": as_of_dt,
		"cutoff_dt": cutoff_dt,
		"source_watermark": watermark,
		"policy_cfg": policy_cfg,
		"qa_enabled": qa_enabled,
		"min_attempts_enabled": any_min_attempts_policy,
		"team_labels": team_labels,
		"active_staff_by_team": {k: len(v) for k, v in active_staff_by_team.items()},
		"user_primary_team": user_primary_team,
		"attempts": attempts_enriched,
		"obligations": obligations,
		"intervals": intervals,
		"assigned_intervals": assigned_intervals,
		"qa_reviews": qa_reviews,
		"dnc_since_by_lead": dnc_since_by_lead,
		"campaign_titles": campaign_titles,
		"campaign_lead_sources": campaign_lead_sources,
		"user_labels": user_labels,
		"unattributed_work_items_count": len(unattributed_wis),
	}


def _aggregate_bucket(
	*,
	scope_type: str,
	scope_id: str,
	scope_label: str,
	attempts: list[dict],
	obligations: list[dict],
	intervals: list[dict],
	qa_reviews: list[dict],
	dnc_since_by_lead: dict[str, datetime],
	policy_cfg: dict,
	qa_enabled: bool,
	min_attempts_enabled: bool,
	unknown_team_exclusions: int = 0,
	parent_team_id: str = "",
	parent_team_label: str = "",
	team_filter: str = "",
	active_staff_count: int = 0,
	lead_source_label: str = "",
) -> dict:
	"""Compute all `cskh-metrics-v2` metrics & `PROCESS_SCORE` from raw subset rows (`0` % averaging!)."""
	assigned_ivs = [iv for iv in intervals if iv.get("in_assigned_window")]
	assigned_iv_cnt = len(assigned_ivs)
	unique_assigned_wis = {iv.work_item for iv in assigned_ivs if iv.work_item}

	assigned_intervals_metric = make_count_metric("ASSIGNED_INTERVALS", assigned_iv_cnt)
	unique_assigned_items_metric = make_count_metric("UNIQUE_ASSIGNED_ITEMS", len(unique_assigned_wis))

	# 1. `ELIGIBLE_COVERAGE` (`P3.md` §7.2 & §7.4)
	cov_elig_ivs = [iv for iv in assigned_ivs if iv.get("coverage_eligible")]
	cov_ok_ivs = [iv for iv in cov_elig_ivs if iv.get("has_attempt_cutoff")]
	eligible_coverage = make_rate_metric("ELIGIBLE_COVERAGE", len(cov_ok_ivs), len(cov_elig_ivs))

	# 2. `FIRST_ATTEMPT_SLA_RATE` & `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` (`deadline_cohort`, `F-P3-02`)
	fa_cohort_obs = [
		ob for ob in obligations if ob.obligation_type == "FIRST_ATTEMPT" and ob["in_deadline_cohort"]
	]
	fa_elig = [ob for ob in fa_cohort_obs if ob["as_of_status"] in ELIGIBLE_SLA_STATUSES]
	fa_num = sum(1 for ob in fa_elig if ob["as_of_status"] == "fulfilled_on_time")
	fa_den = len(fa_elig)
	first_attempt_sla = make_rate_metric("FIRST_ATTEMPT_SLA_RATE", fa_num, fa_den)

	wi_fa_statuses: dict[str, set[str]] = defaultdict(set)
	for ob in fa_elig:
		wi_fa_statuses[ob.work_item].add(ob["as_of_status"])
	wi_fa_den = len(wi_fa_statuses)
	wi_fa_num = sum(
		1
		for st_set in wi_fa_statuses.values()
		if "fulfilled_on_time" in st_set
		and not (st_set & {"fulfilled_late", "breached_open", "breached_handoff"})
	)
	wi_first_attempt_sla = make_rate_metric("WORK_ITEM_FIRST_ATTEMPT_SLA_RATE", wi_fa_num, wi_fa_den)

	# 3. `FOLLOWUP_SLA_RATE`, `CALLBACK_COMPLETION_RATE`, `RESCHEDULE_RATE`
	fu_cohort_obs = [
		ob for ob in obligations if ob.obligation_type == "FOLLOWUP" and ob["in_deadline_cohort"]
	]
	fu_elig = [ob for ob in fu_cohort_obs if ob["as_of_status"] in ELIGIBLE_SLA_STATUSES]
	fu_num = sum(1 for ob in fu_elig if ob["as_of_status"] == "fulfilled_on_time")
	fu_den = len(fu_elig)
	followup_sla = make_rate_metric("FOLLOWUP_SLA_RATE", fu_num, fu_den)

	cb_completed_num = sum(
		1 for ob in fu_elig if ob["as_of_status"] in ("fulfilled_on_time", "fulfilled_late")
	)
	callback_completion = make_rate_metric("CALLBACK_COMPLETION_RATE", cb_completed_num, fu_den)

	fu_window_obs = [
		ob
		for ob in obligations
		if ob.obligation_type == "FOLLOWUP" and (ob["in_started_window"] or ob["in_deadline_cohort"])
	]
	distinct_fu_tasks = {ob.followup_task for ob in fu_window_obs if ob.followup_task}
	resched_tasks = {
		ob.followup_task
		for ob in fu_window_obs
		if ob["in_started_window"] and cint(ob.reschedule_seq) > 0 and ob.followup_task
	}
	distinct_fu_tasks |= resched_tasks
	reschedule_rate = make_rate_metric("RESCHEDULE_RATE", len(resched_tasks), len(distinct_fu_tasks))

	# 4. `FIRST_CONNECTED_TRACKING_RATE` (`informational_only = True`) & `MIN_ATTEMPTS_COMPLIANCE_RATE`
	fc_elig = [
		ob
		for ob in obligations
		if ob.obligation_type == "FIRST_CONNECTED"
		and ob["in_deadline_cohort"]
		and ob["as_of_status"] in ("fulfilled_on_time", "fulfilled_late", "breached_open")
	]
	fc_num = sum(1 for ob in fc_elig if ob["as_of_status"] == "fulfilled_on_time")
	first_connected_rate = make_rate_metric(
		"FIRST_CONNECTED_TRACKING_RATE",
		fc_num,
		len(fc_elig),
		informational_only=True,
	)

	min_elig = [
		ob
		for ob in obligations
		if ob.obligation_type == "MIN_ATTEMPTS"
		and ob["in_deadline_cohort"]
		and ob["as_of_status"] in ELIGIBLE_SLA_STATUSES
	]
	min_num = sum(1 for ob in min_elig if ob["as_of_status"] == "fulfilled_on_time")
	min_attempts_rate = make_rate_metric(
		"MIN_ATTEMPTS_COMPLIANCE_RATE",
		min_num,
		len(min_elig),
		disabled=(not min_attempts_enabled and len(min_elig) == 0),
		disabled_reason="policy_disabled_or_no_eligible_records",
	)

	# 5. Effective Contact Attempts (`0` `CRM Call Log` double count, preserves P2 `verification_status` — `F-P3-03`)
	eff_valid_attempts = [
		a
		for a in attempts
		if a["in_effective_window"]
		and not a["is_voided"]
		and bool(a["effective"].get("is_contact_attempt", True))
	]
	attempted_cnt = len(eff_valid_attempts)
	connected_cnt = sum(1 for a in eff_valid_attempts if bool(a["effective"].get("is_connected")))
	verified_cnt = sum(1 for a in eff_valid_attempts if a["is_verified_state"])
	disputed_cnt = sum(1 for a in eff_valid_attempts if a["is_disputed_state"])
	self_reported_cnt = max(attempted_cnt - verified_cnt - disputed_cnt, 0)
	attempt_breakdown = {
		"attempted": attempted_cnt,
		"connected": connected_cnt,
		"self_reported": self_reported_cnt,
		"verified": verified_cnt,
		"disputed": disputed_cnt,
	}

	contact_attempt_metric = make_count_metric(
		"CONTACT_ATTEMPT_COUNT",
		attempted_cnt,
		attempt_breakdown=attempt_breakdown,
	)
	connected_attempt_metric = make_count_metric("CONNECTED_ATTEMPT_COUNT", connected_cnt)
	verification_rate = make_rate_metric("VERIFICATION_RATE", verified_cnt, attempted_cnt)

	connected_wis = {
		a["work_item"]
		for a in eff_valid_attempts
		if a.get("work_item") and bool(a["effective"].get("is_connected"))
	}
	connected_items_metric = make_count_metric("CONNECTED_ITEMS", len(connected_wis))

	# 6. `CONNECTED_RATE` (`ownership_interval` grain, `F-P3-01`: `41/49 = 83.67%` on Golden!)
	tried_ivs = [iv for iv in assigned_ivs if iv.get("tried_for_connected_rate")]
	conn_ivs = [iv for iv in tried_ivs if iv.get("has_connected_cutoff")]
	connected_rate = make_rate_metric("CONNECTED_RATE", len(conn_ivs), len(tried_ivs))

	# 7. `WORK_ITEMS_TOUCHED` (`DISTINCT work_item` vs `intervals_touched`)
	touched_wis = {a["work_item"] for a in eff_valid_attempts if a.get("work_item")}
	touched_intervals = {a["ownership_event"] for a in eff_valid_attempts if a.get("ownership_event")}
	work_items_touched_metric = make_count_metric(
		"WORK_ITEMS_TOUCHED",
		len(touched_wis),
		intervals_touched=len(touched_intervals),
	)

	# 8. `CONVERSION_RATE` (`P3.md` §7.2: `13/55 = 23.64%` on Golden)
	cutoff_valid_attempts = [
		a
		for a in attempts
		if a.get("in_cutoff_window")
		and not a["is_voided"]
		and bool(a["effective"].get("is_contact_attempt", True))
	]
	invalid_phone_wis = {
		a["work_item"]
		for a in cutoff_valid_attempts
		if a.get("work_item") and bool(a["effective"].get("is_invalid_phone"))
	} | {iv.work_item for iv in assigned_ivs if iv.work_item and iv.get("has_invalid_phone_cutoff")}
	conv_elig_wis = unique_assigned_wis - invalid_phone_wis
	converted_wis_in_scope = {
		a["work_item"]
		for a in cutoff_valid_attempts
		if a.get("work_item") in conv_elig_wis and bool(a["effective"].get("is_conversion"))
	}
	conversion_rate = make_rate_metric("CONVERSION_RATE", len(converted_wis_in_scope), len(conv_elig_wis))

	# 9. `VALID_OUTCOME_LOGGING_RATE` (`outcome_completeness` on `eff_valid_attempts`: `127/129 = 98.45%`) + Audit Hygiene Rates
	complete_cnt = sum(1 for a in eff_valid_attempts if a.get("complete_fields_ok", True))
	valid_outcome_logging = make_rate_metric("VALID_OUTCOME_LOGGING_RATE", complete_cnt, attempted_cnt)

	raw_window_attempts = [a for a in attempts if a["in_raw_window"]]
	raw_cnt = len(raw_window_attempts)
	voided_cnt = sum(1 for a in raw_window_attempts if a["is_voided"])
	corrected_or_voided_cnt = sum(1 for a in raw_window_attempts if a["has_approved_correction"])
	correction_rate = make_rate_metric("CORRECTION_RATE", corrected_or_voided_cnt, raw_cnt)
	void_attempt_rate = make_rate_metric("VOID_ATTEMPT_RATE", voided_cnt, raw_cnt)

	# 10. `DNC_COMPLIANCE_RATE`
	violated_intervals: set[str] = set()
	for a in eff_valid_attempts:
		dnc_ts = dnc_since_by_lead.get(a.get("lead") or "")
		if (
			dnc_ts
			and not bool(a["effective"].get("is_do_not_contact"))
			and a["eff_occ_dt"] > dnc_ts + timedelta(seconds=1)
			and a.get("ownership_event")
		):
			violated_intervals.add(a["ownership_event"])

	eval_interval_ids = {iv.name for iv in assigned_ivs} | touched_intervals
	dnc_den = len(eval_interval_ids)
	dnc_num = sum(1 for iv_id in eval_interval_ids if iv_id not in violated_intervals)
	dnc_compliance = make_rate_metric("DNC_COMPLIANCE_RATE", dnc_num, dnc_den)

	# 11. Operational SLA Inventory & Counts (`OVERDUE_BACKLOG`, `OPEN_FOLLOWUPS_OVERDUE`, `SLA_BREACH_COUNT`, `SLA_WAIVER_COUNT`)
	open_overdue_obs = [ob for ob in obligations if ob.get("is_open_overdue_as_of")]
	overdue_backlog_wis = sorted({ob.work_item for ob in open_overdue_obs if ob.work_item})
	open_fu_overdue_cnt = sum(1 for ob in open_overdue_obs if ob.obligation_type == "FOLLOWUP")

	deadline_non_info_obs = [
		ob for ob in obligations if ob["in_deadline_cohort"] and not cint(ob.informational_only)
	]
	breach_cnt = sum(1 for ob in deadline_non_info_obs if ob["as_of_status"] in BREACH_SLA_STATUSES)
	waived_cnt = sum(1 for ob in obligations if ob["in_deadline_cohort"] and ob["as_of_status"] == "waived")
	canceled_validly_cnt = sum(
		1 for ob in obligations if ob["in_deadline_cohort"] and ob["as_of_status"] == "canceled_validly"
	)

	overdue_backlog_metric = make_count_metric("OVERDUE_BACKLOG", len(overdue_backlog_wis))
	open_followups_overdue_metric = make_count_metric("OPEN_FOLLOWUPS_OVERDUE", open_fu_overdue_cnt)
	breach_count_metric = make_count_metric("SLA_BREACH_COUNT", breach_cnt)
	sla_waiver_count_metric = make_count_metric("SLA_WAIVER_COUNT", waived_cnt)

	# 12. First-Attempt Latency Percentiles (`P3.md` §7.3)
	biz_latencies = sorted(
		float(iv["first_attempt_biz_minutes"])
		for iv in assigned_ivs
		if iv.get("first_attempt_biz_minutes") is not None
	)
	wall_latencies = sorted(
		float(iv["first_attempt_wall_minutes"])
		for iv in assigned_ivs
		if iv.get("first_attempt_wall_minutes") is not None
	)
	lat_sample_cnt = len(biz_latencies)
	lat_untouched_cnt = max(assigned_iv_cnt - lat_sample_cnt, 0)
	biz_p50 = _percentile_sorted(biz_latencies, 50.0)
	biz_p90 = _percentile_sorted(biz_latencies, 90.0)
	wall_p50 = _percentile_sorted(wall_latencies, 50.0)
	wall_p90 = _percentile_sorted(wall_latencies, 90.0)

	latency_p50_metric = make_duration_metric(
		"FIRST_ATTEMPT_LATENCY_P50_MINUTES",
		biz_p50,
		lat_sample_cnt,
		wall_minutes=wall_p50,
		pending_or_untouched_count=lat_untouched_cnt,
	)
	latency_p90_metric = make_duration_metric(
		"FIRST_ATTEMPT_LATENCY_P90_MINUTES",
		biz_p90,
		lat_sample_cnt,
		wall_minutes=wall_p90,
		pending_or_untouched_count=lat_untouched_cnt,
	)

	# 13. `QA_PASS_RATE` (`random_sample` vs `risk_targeted`, `P3.md` §7.2 & §8.2)
	approved_qa = [q for q in qa_reviews if q.status in ("published", "approved")]
	random_qa = [q for q in approved_qa if (q.get("cohort_type") or "random_sample") == "random_sample"]
	targeted_qa = [q for q in approved_qa if q.get("cohort_type") == "risk_targeted"]
	min_qa_samples = max(cint(policy_cfg.get("min_qa_sample_size", 5)), 1)

	qa_cohort_breakdown = {
		"random_sample": {
			"count": len(random_qa),
			"passed": sum(1 for q in random_qa if cint(q.passed)),
		},
		"risk_targeted": {
			"count": len(targeted_qa),
			"passed": sum(1 for q in targeted_qa if cint(q.passed)),
		},
	}

	if not qa_enabled:
		qa_pass = make_rate_metric(
			"QA_PASS_RATE",
			0,
			0,
			disabled=True,
			disabled_reason="qa_disabled_by_policy",
		)
		qa_pass["sample_count"] = 0
		qa_pass["targeted_sample_count"] = 0
	elif len(random_qa) == 0:
		qa_pass = make_rate_metric("QA_PASS_RATE", 0, 0)
		qa_pass["sample_count"] = 0
		qa_pass["targeted_sample_count"] = len(targeted_qa)
	else:
		passed_qa_cnt = sum(1 for q in random_qa if cint(q.passed))
		pts_num = round(sum(flt(q.points_achieved) for q in random_qa), 2)
		pts_den = round(sum(flt(q.max_points) for q in random_qa), 2)
		qa_pass = make_rate_metric("QA_PASS_RATE", passed_qa_cnt, len(random_qa))
		qa_pass["sample_count"] = len(random_qa)
		qa_pass["targeted_sample_count"] = len(targeted_qa)
		qa_pass["targeted_passed_count"] = sum(1 for q in targeted_qa if cint(q.passed))
		qa_pass["below_min_sample_for_score"] = len(random_qa) < min_qa_samples
		qa_pass["qa_points_numerator"] = pts_num
		qa_pass["qa_points_denominator"] = pts_den
		qa_pass["qa_points_rate"] = (
			round_half_up_2((Decimal(str(pts_num)) * Decimal("100")) / Decimal(str(pts_den)))
			if pts_den > 0
			else None
		)

	# 14. `PROCESS_SCORE` (`25 / 20 / 20 / 20 / 15` -> `7350 / 85 = 86.47` when `qa_enabled=false`)
	eligible_events_total = fa_den + len(cov_elig_ivs) + attempted_cnt + fu_den
	process_score, score_components = compute_process_score_from_components(
		first_attempt_sla=first_attempt_sla,
		eligible_coverage=eligible_coverage,
		data_hygiene=valid_outcome_logging,
		followup_sla=followup_sla,
		qa_pass_rate=qa_pass,
		qa_enabled=qa_enabled,
		qa_sample_count=len(random_qa),
		eligible_events_total=eligible_events_total,
		policy_cfg=policy_cfg,
	)

	dnc_ivs_cnt = sum(1 for iv in assigned_ivs if iv.get("has_dnc_cutoff"))
	inv_ivs_cnt = sum(1 for iv in assigned_ivs if iv.get("has_invalid_phone_cutoff"))
	exclusion_counts = {
		"historical_attribution_unknown": int(unknown_team_exclusions),
		"waived_obligations": int(waived_cnt),
		"canceled_validly_obligations": int(canceled_validly_cnt),
		"canceled_validly_dnc": int(dnc_ivs_cnt),
		"do_not_contact": int(dnc_ivs_cnt),
		"invalid_phone": int(inv_ivs_cnt),
		"voided_attempts": int(voided_cnt),
	}

	metrics_map = {
		"ASSIGNED_INTERVALS": assigned_intervals_metric,
		"UNIQUE_ASSIGNED_ITEMS": unique_assigned_items_metric,
		"ELIGIBLE_COVERAGE": eligible_coverage,
		"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE": wi_first_attempt_sla,
		"FIRST_ATTEMPT_SLA_RATE": first_attempt_sla,
		"FOLLOWUP_SLA_RATE": followup_sla,
		"FIRST_CONNECTED_TRACKING_RATE": first_connected_rate,
		"MIN_ATTEMPTS_COMPLIANCE_RATE": min_attempts_rate,
		"CONTACT_ATTEMPT_COUNT": contact_attempt_metric,
		"CONNECTED_ATTEMPT_COUNT": connected_attempt_metric,
		"CONNECTED_ITEMS": connected_items_metric,
		"CONNECTED_RATE": connected_rate,
		"WORK_ITEMS_TOUCHED": work_items_touched_metric,
		"VALID_OUTCOME_LOGGING_RATE": valid_outcome_logging,
		"VERIFICATION_RATE": verification_rate,
		"CONVERSION_RATE": conversion_rate,
		"CORRECTION_RATE": correction_rate,
		"VOID_ATTEMPT_RATE": void_attempt_rate,
		"CALLBACK_COMPLETION_RATE": callback_completion,
		"RESCHEDULE_RATE": reschedule_rate,
		"DNC_COMPLIANCE_RATE": dnc_compliance,
		"OVERDUE_BACKLOG": overdue_backlog_metric,
		"OPEN_FOLLOWUPS_OVERDUE": open_followups_overdue_metric,
		"SLA_BREACH_COUNT": breach_count_metric,
		"SLA_WAIVER_COUNT": sla_waiver_count_metric,
		"FIRST_ATTEMPT_LATENCY_P50_MINUTES": latency_p50_metric,
		"FIRST_ATTEMPT_LATENCY_P90_MINUTES": latency_p90_metric,
		"QA_PASS_RATE": qa_pass,
		"PROCESS_SCORE": process_score,
	}

	# Distinct Work Items vs Ownership Intervals (`A -> B -> A` invariant!)
	if assigned_iv_cnt > 0:
		distinct_wi_count = len(unique_assigned_wis)
		intervals_count = assigned_iv_cnt
	else:
		fallback_wis = {ob.work_item for ob in obligations if ob["in_deadline_cohort"] and ob.work_item} | {
			a["work_item"] for a in eff_valid_attempts if a.get("work_item")
		}
		fallback_ivs = {
			ob.interval_event for ob in obligations if ob["in_deadline_cohort"] and ob.interval_event
		} | {a["ownership_event"] for a in eff_valid_attempts if a.get("ownership_event")}
		distinct_wi_count = len(fallback_wis)
		intervals_count = len(fallback_ivs)

	return {
		"scope_type": scope_type,
		"scope_id": scope_id,
		"scope_label": scope_label,
		"team_filter": team_filter,
		"parent_team_id": parent_team_id,
		"parent_team_label": parent_team_label,
		"lead_source": lead_source_label,
		"active_staff_count": active_staff_count,
		"distinct_work_items": distinct_wi_count,
		"ownership_intervals_count": intervals_count,
		"work_items_touched": len(touched_wis),
		"contact_attempt_count": attempted_cnt,
		"connected_attempt_count": connected_cnt,
		"process_score_status": process_score["status"],
		"process_score_sort": float(process_score["value"]) if process_score["value"] is not None else -1.0,
		"metrics": metrics_map,
		"score_components": score_components,
		"attempt_breakdown": attempt_breakdown,
		"qa_cohort_breakdown": qa_cohort_breakdown,
		"exclusion_counts": exclusion_counts,
	}


def _build_trend_series(
	period: PeriodSpec,
	attempts: list[dict],
	obligations: list[dict],
	intervals: list[dict] | None = None,
) -> list[dict]:
	"""Build compact daily (or monthly for `> 62d`) trend points from the in-memory loaded rows."""
	by_bucket_att: dict[str, int] = defaultdict(int)
	by_bucket_conn_att: dict[str, int] = defaultdict(int)
	by_bucket_tried_iv: dict[str, int] = defaultdict(int)
	by_bucket_conn_iv: dict[str, int] = defaultdict(int)
	by_bucket_fa_num: dict[str, int] = defaultdict(int)
	by_bucket_fa_den: dict[str, int] = defaultdict(int)
	by_bucket_fu_num: dict[str, int] = defaultdict(int)
	by_bucket_fu_den: dict[str, int] = defaultdict(int)

	use_monthly = (period.to_date - period.from_date).days > 62

	def _bkey(dt_val: datetime) -> str:
		d = dt_val.date()
		return f"{d.year:04d}-{d.month:02d}" if use_monthly else d.isoformat()

	for a in attempts:
		if (
			a["in_effective_window"]
			and not a["is_voided"]
			and bool(a["effective"].get("is_contact_attempt", True))
		):
			k = _bkey(a["eff_occ_dt"])
			by_bucket_att[k] += 1
			if bool(a["effective"].get("is_connected")):
				by_bucket_conn_att[k] += 1

	for iv in intervals or ():
		if iv.get("in_assigned_window") and iv.get("tried_for_connected_rate"):
			k = _bkey(iv["iv_start_dt"])
			by_bucket_tried_iv[k] += 1
			if iv.get("has_connected_cutoff"):
				by_bucket_conn_iv[k] += 1

	for ob in obligations:
		if not ob["in_deadline_cohort"] or ob["as_of_status"] not in ELIGIBLE_SLA_STATUSES:
			continue
		k = _bkey(ob["eff_due_dt"])
		if ob.obligation_type == "FIRST_ATTEMPT":
			by_bucket_fa_den[k] += 1
			if ob["as_of_status"] == "fulfilled_on_time":
				by_bucket_fa_num[k] += 1
		elif ob.obligation_type == "FOLLOWUP":
			by_bucket_fu_den[k] += 1
			if ob["as_of_status"] == "fulfilled_on_time":
				by_bucket_fu_num[k] += 1

	if use_monthly:
		keys: list[str] = []
		cur_y, cur_m = period.from_date.year, period.from_date.month
		while (cur_y, cur_m) <= (period.to_date.year, period.to_date.month):
			keys.append(f"{cur_y:04d}-{cur_m:02d}")
			if cur_m == 12:
				cur_y, cur_m = cur_y + 1, 1
			else:
				cur_m += 1
	else:
		keys = [d.isoformat() for d in iter_days(period.from_date, period.to_date)]

	series: list[dict] = []
	for k in keys:
		fa_d = by_bucket_fa_den[k]
		fu_d = by_bucket_fu_den[k]
		att_c = by_bucket_att[k]
		conn_att_c = by_bucket_conn_att[k]
		tried_iv_c = by_bucket_tried_iv[k]
		conn_iv_c = by_bucket_conn_iv[k]
		fa_rate = round_half_up_2(Decimal(by_bucket_fa_num[k] * 100) / Decimal(fa_d)) if fa_d > 0 else None
		fu_rate = round_half_up_2(Decimal(by_bucket_fu_num[k] * 100) / Decimal(fu_d)) if fu_d > 0 else None
		conn_rate = (
			round_half_up_2(Decimal(conn_iv_c * 100) / Decimal(tried_iv_c)) if tried_iv_c > 0 else None
		)
		series.append(
			{
				"bucket": k,
				"contact_attempts": att_c,
				"connected_attempts": conn_att_c,
				"tried_intervals": tried_iv_c,
				"connected_intervals": conn_iv_c,
				"connected_rate": conn_rate,
				"first_attempt_sla_rate": fa_rate,
				"first_touch_on_time_rate": fa_rate,
				"followup_sla_rate": fu_rate,
				"followup_on_time_rate": fu_rate,
			}
		)
	return series


def compute_period_snapshots(
	period: PeriodSpec,
	as_of: datetime | str | None = None,
	*,
	campaign_filter: str = "",
	lead_source_filter: str = "",
) -> dict:
	"""Compute all (`company`, `team`, `employee`, `employee_team`, `campaign`) snapshots and verify reconciliation."""
	ds = load_period_raw_dataset(
		period,
		as_of=as_of,
		campaign_filter=campaign_filter,
		lead_source_filter=lead_source_filter,
	)
	attempts = ds["attempts"]
	obligations = ds["obligations"]
	intervals = ds["intervals"]
	qa_reviews = ds["qa_reviews"]
	dnc_map = ds["dnc_since_by_lead"]
	pol_cfg = ds["policy_cfg"]
	qa_on = ds["qa_enabled"]
	min_on = ds["min_attempts_enabled"]
	team_labels = ds["team_labels"]
	staff_counts = ds["active_staff_by_team"]
	user_primary_team = ds["user_primary_team"]
	camp_titles = ds["campaign_titles"]
	camp_sources = ds["campaign_lead_sources"]
	user_labels = ds["user_labels"]
	unattributed_wi_cnt = int(ds.get("unattributed_work_items_count", 0))

	unknown_ob_cnt = sum(
		1
		for ob in obligations
		if ob["in_deadline_cohort"] and ob["resolved_attribution"] == UNKNOWN_HISTORICAL
	)
	unknown_iv_cnt = sum(
		1
		for iv in intervals
		if iv.get("in_assigned_window") and iv["resolved_attribution"] == UNKNOWN_HISTORICAL
	)
	unknown_att_cnt = sum(
		1
		for a in attempts
		if a["in_effective_window"] and not a["is_voided"] and a["resolved_attribution"] == UNKNOWN_HISTORICAL
	)
	total_unknown_events = unattributed_wi_cnt or (unknown_ob_cnt + unknown_iv_cnt + unknown_att_cnt)

	snapshots: list[dict] = []

	# 1. `company` snapshot
	comp_snap = _aggregate_bucket(
		scope_type="company",
		scope_id=COMPANY_SCOPE_ID,
		scope_label=COMPANY_SCOPE_LABEL,
		attempts=attempts,
		obligations=obligations,
		intervals=intervals,
		qa_reviews=qa_reviews,
		dnc_since_by_lead=dnc_map,
		policy_cfg=pol_cfg,
		qa_enabled=qa_on,
		min_attempts_enabled=min_on,
		unknown_team_exclusions=total_unknown_events,
	)
	comp_snap["trend_series"] = _build_trend_series(period, attempts, obligations, intervals)
	snapshots.append(comp_snap)

	# 2. `team` snapshots
	att_by_team: dict[str, list[dict]] = defaultdict(list)
	ob_by_team: dict[str, list[dict]] = defaultdict(list)
	iv_by_team: dict[str, list[dict]] = defaultdict(list)
	qa_by_team: dict[str, list[dict]] = defaultdict(list)

	for a in attempts:
		tk = (
			a["resolved_team"]
			if (a["resolved_team"] and a["resolved_attribution"] == AUTHORITATIVE)
			else UNATTRIBUTED_TEAM_ID
		)
		att_by_team[tk].append(a)
	for ob in obligations:
		tk = (
			ob["resolved_team"]
			if (ob["resolved_team"] and ob["resolved_attribution"] == AUTHORITATIVE)
			else UNATTRIBUTED_TEAM_ID
		)
		ob_by_team[tk].append(ob)
	for iv in intervals:
		tk = (
			iv["resolved_team"]
			if (iv["resolved_team"] and iv["resolved_attribution"] == AUTHORITATIVE)
			else UNATTRIBUTED_TEAM_ID
		)
		iv_by_team[tk].append(iv)
	for qr in qa_reviews:
		tk = qr.team_at_review or UNATTRIBUTED_TEAM_ID
		qa_by_team[tk].append(qr)

	all_team_ids = sorted(
		(
			set(team_labels.keys())
			| set(att_by_team.keys())
			| set(ob_by_team.keys())
			| set(iv_by_team.keys())
			| set(qa_by_team.keys())
		)
		- {UNATTRIBUTED_TEAM_ID}
	)
	for tid in all_team_ids:
		t_atts = att_by_team.get(tid, [])
		t_obs = ob_by_team.get(tid, [])
		t_ivs = iv_by_team.get(tid, [])
		t_snap = _aggregate_bucket(
			scope_type="team",
			scope_id=tid,
			scope_label=team_labels.get(tid, tid),
			attempts=t_atts,
			obligations=t_obs,
			intervals=t_ivs,
			qa_reviews=qa_by_team.get(tid, []),
			dnc_since_by_lead=dnc_map,
			policy_cfg=pol_cfg,
			qa_enabled=qa_on,
			min_attempts_enabled=min_on,
			unknown_team_exclusions=0,
			active_staff_count=staff_counts.get(tid, 0),
		)
		t_snap["trend_series"] = _build_trend_series(period, t_atts, t_obs, t_ivs)
		snapshots.append(t_snap)

	if (
		total_unknown_events > 0
		or att_by_team.get(UNATTRIBUTED_TEAM_ID)
		or ob_by_team.get(UNATTRIBUTED_TEAM_ID)
		or iv_by_team.get(UNATTRIBUTED_TEAM_ID)
	):
		u_atts = att_by_team.get(UNATTRIBUTED_TEAM_ID, [])
		u_obs = ob_by_team.get(UNATTRIBUTED_TEAM_ID, [])
		u_ivs = iv_by_team.get(UNATTRIBUTED_TEAM_ID, [])
		u_snap = _aggregate_bucket(
			scope_type="team",
			scope_id=UNATTRIBUTED_TEAM_ID,
			scope_label=UNATTRIBUTED_TEAM_LABEL,
			attempts=u_atts,
			obligations=u_obs,
			intervals=u_ivs,
			qa_reviews=qa_by_team.get(UNATTRIBUTED_TEAM_ID, []),
			dnc_since_by_lead=dnc_map,
			policy_cfg=pol_cfg,
			qa_enabled=qa_on,
			min_attempts_enabled=min_on,
			unknown_team_exclusions=total_unknown_events,
			active_staff_count=0,
		)
		u_snap["trend_series"] = _build_trend_series(period, u_atts, u_obs, u_ivs)
		snapshots.append(u_snap)

	# 3. `employee` (company-wide) AND `employee_team` (`team-as-of` per `(uid, tid)` slice — `F-P3-04`!)
	att_by_emp: dict[str, list[dict]] = defaultdict(list)
	ob_by_emp: dict[str, list[dict]] = defaultdict(list)
	iv_by_emp: dict[str, list[dict]] = defaultdict(list)
	qa_by_emp: dict[str, list[dict]] = defaultdict(list)

	att_by_emp_team: dict[tuple[str, str], list[dict]] = defaultdict(list)
	ob_by_emp_team: dict[tuple[str, str], list[dict]] = defaultdict(list)
	iv_by_emp_team: dict[tuple[str, str], list[dict]] = defaultdict(list)
	qa_by_emp_team: dict[tuple[str, str], list[dict]] = defaultdict(list)

	for a in attempts:
		uid = a.get("actor_user")
		if uid:
			att_by_emp[uid].append(a)
			if a.get("resolved_team") and a.get("resolved_attribution") == AUTHORITATIVE:
				att_by_emp_team[(uid, a["resolved_team"])].append(a)
	for ob in obligations:
		uid = ob.get("owner_user")
		if uid:
			ob_by_emp[uid].append(ob)
			if ob.get("resolved_team") and ob.get("resolved_attribution") == AUTHORITATIVE:
				ob_by_emp_team[(uid, ob["resolved_team"])].append(ob)
	for iv in intervals:
		uid = iv.get("to_user")
		if uid:
			iv_by_emp[uid].append(iv)
			if iv.get("resolved_team") and iv.get("resolved_attribution") == AUTHORITATIVE:
				iv_by_emp_team[(uid, iv["resolved_team"])].append(iv)
	for qr in qa_reviews:
		uid = qr.get("reviewed_staff")
		if uid:
			qa_by_emp[uid].append(qr)
			if qr.get("team_at_review"):
				qa_by_emp_team[(uid, qr["team_at_review"])].append(qr)

	all_emp_ids = sorted(
		set(user_primary_team.keys())
		| set(att_by_emp.keys())
		| set(ob_by_emp.keys())
		| set(iv_by_emp.keys())
		| set(qa_by_emp.keys())
	)
	for uid in all_emp_ids:
		e_atts = att_by_emp.get(uid, [])
		e_obs = ob_by_emp.get(uid, [])
		e_ivs = iv_by_emp.get(uid, [])
		emp_team = user_primary_team.get(uid, "")
		if not emp_team:
			for iv in reversed(e_ivs):
				if iv.get("resolved_team") and iv.get("resolved_attribution") == AUTHORITATIVE:
					emp_team = iv["resolved_team"]
					break
		if not emp_team:
			for ob in reversed(e_obs):
				if ob.get("resolved_team") and ob.get("resolved_attribution") == AUTHORITATIVE:
					emp_team = ob["resolved_team"]
					break

		e_snap = _aggregate_bucket(
			scope_type="employee",
			scope_id=uid,
			scope_label=user_labels.get(uid, uid),
			attempts=e_atts,
			obligations=e_obs,
			intervals=e_ivs,
			qa_reviews=qa_by_emp.get(uid, []),
			dnc_since_by_lead=dnc_map,
			policy_cfg=pol_cfg,
			qa_enabled=qa_on,
			min_attempts_enabled=min_on,
			unknown_team_exclusions=0,
			parent_team_id=emp_team,
			parent_team_label=team_labels.get(emp_team, emp_team) if emp_team else "",
		)
		e_snap["trend_series"] = []
		snapshots.append(e_snap)

	# Emit `(uid, tid)` `employee_team` slices for every active hierarchy membership AND every historical `(uid, tid)` slice!
	emp_team_pairs: set[tuple[str, str]] = (
		set(att_by_emp_team.keys())
		| set(ob_by_emp_team.keys())
		| set(iv_by_emp_team.keys())
		| set(qa_by_emp_team.keys())
	)
	for uid, tid in user_primary_team.items():
		if uid and tid:
			emp_team_pairs.add((uid, tid))

	for uid, tid in sorted(emp_team_pairs):
		et_atts = att_by_emp_team.get((uid, tid), [])
		et_obs = ob_by_emp_team.get((uid, tid), [])
		et_ivs = iv_by_emp_team.get((uid, tid), [])
		et_qas = qa_by_emp_team.get((uid, tid), [])
		et_snap = _aggregate_bucket(
			scope_type="employee_team",
			scope_id=uid,
			scope_label=user_labels.get(uid, uid),
			attempts=et_atts,
			obligations=et_obs,
			intervals=et_ivs,
			qa_reviews=et_qas,
			dnc_since_by_lead=dnc_map,
			policy_cfg=pol_cfg,
			qa_enabled=qa_on,
			min_attempts_enabled=min_on,
			unknown_team_exclusions=0,
			parent_team_id=tid,
			parent_team_label=team_labels.get(tid, tid),
			team_filter=tid,
		)
		et_snap["trend_series"] = []
		snapshots.append(et_snap)

	# 4. `campaign` snapshots
	att_by_camp: dict[str, list[dict]] = defaultdict(list)
	ob_by_camp: dict[str, list[dict]] = defaultdict(list)
	iv_by_camp: dict[str, list[dict]] = defaultdict(list)
	qa_by_camp: dict[str, list[dict]] = defaultdict(list)

	for a in attempts:
		if a.get("campaign"):
			att_by_camp[a["campaign"]].append(a)
	for ob in obligations:
		if ob.get("campaign"):
			ob_by_camp[ob["campaign"]].append(ob)
	for iv in intervals:
		if iv.get("campaign"):
			iv_by_camp[iv["campaign"]].append(iv)
	for qr in qa_reviews:
		if qr.get("campaign"):
			qa_by_camp[qr["campaign"]].append(qr)

	all_camp_ids = sorted(
		set(camp_titles.keys()) | set(att_by_camp.keys()) | set(ob_by_camp.keys()) | set(iv_by_camp.keys())
	)
	if campaign_filter and campaign_filter not in all_camp_ids:
		all_camp_ids.append(campaign_filter)

	for cid in all_camp_ids:
		c_atts = att_by_camp.get(cid, [])
		c_obs = ob_by_camp.get(cid, [])
		c_ivs = iv_by_camp.get(cid, [])
		c_snap = _aggregate_bucket(
			scope_type="campaign",
			scope_id=cid,
			scope_label=camp_titles.get(cid, cid),
			attempts=c_atts,
			obligations=c_obs,
			intervals=c_ivs,
			qa_reviews=qa_by_camp.get(cid, []),
			dnc_since_by_lead=dnc_map,
			policy_cfg=pol_cfg,
			qa_enabled=qa_on,
			min_attempts_enabled=min_on,
			unknown_team_exclusions=sum(
				1
				for ob in c_obs
				if ob["in_deadline_cohort"] and ob["resolved_attribution"] == UNKNOWN_HISTORICAL
			),
			lead_source_label=camp_sources.get(cid, ""),
		)
		c_snap["trend_series"] = _build_trend_series(period, c_atts, c_obs, c_ivs)
		snapshots.append(c_snap)

	reconciliation = verify_snapshot_reconciliation(ds, snapshots)
	return {
		"period": period,
		"as_of": ds["as_of"],
		"source_watermark": ds["source_watermark"],
		"metric_version": METRIC_VERSION,
		"qa_enabled": qa_on,
		"campaign_filter": campaign_filter or "",
		"snapshots": snapshots,
		"reconciliation": reconciliation,
	}


def verify_snapshot_reconciliation(dataset: dict, snapshots: list[dict]) -> dict:
	"""Enforce `docs/root-plan/P3.md` §9.5 reconciliation invariants before snapshot publish."""
	comp_rows = [s for s in snapshots if s["scope_type"] == "company"]
	if len(comp_rows) != 1:
		raise DataReconciliationFailed("Thiếu hoặc trùng bản chụp KPI cấp công ty (company snapshot).")
	comp = comp_rows[0]
	emp_rows = [s for s in snapshots if s["scope_type"] == "employee"]
	emp_team_rows = [s for s in snapshots if s["scope_type"] == "employee_team"]
	team_rows = [s for s in snapshots if s["scope_type"] == "team"]

	# 1. Range, `denominator == 0 -> null`, and `count -> denominator is None` invariants
	for snap in snapshots:
		for mcode, mcell in snap["metrics"].items():
			unit = mcell.get("unit")
			val = mcell.get("value")
			den = mcell.get("denominator")
			if unit == "percent":
				if (den is None or den == 0) and val is not None:
					raise DataReconciliationFailed(
						f"Vi phạm bất biến chia cho 0 tại {snap['scope_type']}:{snap['scope_id']}:{mcode}."
					)
				if val is not None and not (0.0 <= float(val) <= 100.0):
					raise DataReconciliationFailed(
						f"Giá trị phần trăm vượt biên [0, 100] tại {snap['scope_type']}:{snap['scope_id']}:{mcode}={val}."
					)
			elif unit == "score_100" and val is not None:
				if not (0.0 <= float(val) <= 100.0):
					raise DataReconciliationFailed(
						f"Điểm quy trình vượt biên [0, 100] tại {snap['scope_type']}:{snap['scope_id']}={val}."
					)
			elif unit == "count" and den is not None:
				raise DataReconciliationFailed(
					f"Chỉ số đếm (count) không được gán mẫu số giả tại {snap['scope_type']}:{snap['scope_id']}:{mcode}."
				)
		if not dataset["qa_enabled"]:
			if snap["metrics"]["QA_PASS_RATE"]["status"] != "disabled":
				raise DataReconciliationFailed(
					"QA_PASS_RATE phải ở trạng thái 'disabled' khi qa_enabled=false."
				)

	# 2. Additive attempt & obligation conservation (`SUM(employees) == company`, `SUM(teams) == company`, `SUM(employee_team) + unattributed == company`)
	emp_att_sum = sum(s["contact_attempt_count"] for s in emp_rows)
	emp_conn_sum = sum(s["connected_attempt_count"] for s in emp_rows)
	team_att_sum = sum(s["contact_attempt_count"] for s in team_rows)
	unattr_team = next((s for s in team_rows if s["scope_id"] == UNATTRIBUTED_TEAM_ID), None)
	unattr_att = unattr_team["contact_attempt_count"] if unattr_team else 0
	emp_team_att_sum = sum(s["contact_attempt_count"] for s in emp_team_rows) + unattr_att

	if emp_att_sum != comp["contact_attempt_count"]:
		raise DataReconciliationFailed(
			f"Lệch tổng lượt liên hệ nhân viên ({emp_att_sum}) so với công ty ({comp['contact_attempt_count']})."
		)
	if emp_conn_sum != comp["connected_attempt_count"]:
		raise DataReconciliationFailed(
			f"Lệch tổng lượt kết nối nhân viên ({emp_conn_sum}) so với công ty ({comp['connected_attempt_count']})."
		)
	if team_att_sum != comp["contact_attempt_count"]:
		raise DataReconciliationFailed(
			f"Lệch tổng lượt liên hệ cấp team ({team_att_sum}) so với công ty ({comp['contact_attempt_count']})."
		)
	if emp_team_att_sum != comp["contact_attempt_count"]:
		raise DataReconciliationFailed(
			f"Lệch tổng lượt liên hệ lát cắt employee_team ({emp_team_att_sum}) so với công ty ({comp['contact_attempt_count']})."
		)

	emp_fa_num = sum(s["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["numerator"] for s in emp_rows)
	emp_fa_den = sum(s["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["denominator"] for s in emp_rows)
	team_fa_num = sum(s["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["numerator"] for s in team_rows)
	team_fa_den = sum(s["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["denominator"] for s in team_rows)
	comp_fa = comp["metrics"]["FIRST_ATTEMPT_SLA_RATE"]
	if (emp_fa_num, emp_fa_den) != (comp_fa["numerator"], comp_fa["denominator"]):
		raise DataReconciliationFailed(
			f"Lệch SLA lần đầu nhân viên ({emp_fa_num}/{emp_fa_den}) so với công ty ({comp_fa['numerator']}/{comp_fa['denominator']})."
		)
	if (team_fa_num, team_fa_den) != (comp_fa["numerator"], comp_fa["denominator"]):
		raise DataReconciliationFailed(
			f"Lệch SLA lần đầu cấp team ({team_fa_num}/{team_fa_den}) so với công ty ({comp_fa['numerator']}/{comp_fa['denominator']})."
		)

	return {
		"ok": True,
		"company_contact_attempts": comp["contact_attempt_count"],
		"company_connected_attempts": comp["connected_attempt_count"],
		"company_first_attempt_sla": f"{comp_fa['numerator']}/{comp_fa['denominator']}",
		"company_connected_rate": (
			f"{comp['metrics']['CONNECTED_RATE']['numerator']}/{comp['metrics']['CONNECTED_RATE']['denominator']}"
		),
		"company_distinct_work_items": comp["distinct_work_items"],
		"company_ownership_intervals": comp["ownership_intervals_count"],
		"snapshots_verified": len(snapshots),
	}
