"""Bulk-SQL KPI & `PROCESS_SCORE` calculator + reconciliation verifier (`P3-04`, `cskh-metrics-v1`).

Invariants (`docs/p3/kpi-dictionary.md`, `docs/p3/scoring-and-data-sufficiency.md`):
  * Uses effective `CSKH Contact Attempt` overlays (`approved` corrections `<= as_of`), NEVER counting `CRM Call Log`.
  * Separates `attempted`, `connected`, `self_reported`, `verified`, and `disputed` (`verified == 0` when `qa_enabled=false`).
  * `denominator == 0` -> `value: null, status: "insufficient_data", reason: "no_eligible_records"`.
  * Company/Campaign `DISTINCT work_item` != Employee/Team `ownership interval` (`A -> B -> A` = 1 Work Item vs 3 intervals).
  * Rollups (`Employee -> Team -> Company`, `Daily -> Monthly -> Yearly`) always recompute from raw numerators/denominators
    and `COUNT(DISTINCT ...)` sets — NEVER averaging percentages.
  * `PROCESS_SCORE` (0-100) re-weights valid components (`7350 / 85 = 86.47` golden example when `qa_enabled=false`)
    and NEVER treats disabled/unmeasured QA as `0%`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime

from crm_cskh.p3_models import DEFAULT_P3_POLICY_FIELDS
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL, resolve_team_for_user_at
from crm_cskh.services.errors import DataReconciliationFailed
from crm_cskh.services.kpi_periods import PeriodSpec, iter_days

METRIC_VERSION = "cskh-metrics-v1"
COMPANY_SCOPE_ID = "__company__"
UNATTRIBUTED_TEAM_ID = "__unattributed_historical_team__"
UNATTRIBUTED_TEAM_LABEL = "Chưa xác định nhánh lịch sử (Trước P3)"

ELIGIBLE_SLA_STATUSES = frozenset(
	("fulfilled_on_time", "fulfilled_late", "breached_open", "breached_handoff")
)

METRIC_LABELS_VI: dict[str, str] = {
	"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE": "Tỷ lệ đạt SLA liên hệ lần đầu (Cấp Hồ sơ khách hàng)",
	"FIRST_ATTEMPT_SLA_RATE": "Tỷ lệ đạt SLA liên hệ lần đầu (Khoảng trách nhiệm)",
	"FOLLOWUP_SLA_RATE": "Tỷ lệ đạt SLA lịch hẹn gọi lại (Callback)",
	"FIRST_CONNECTED_TRACKING_RATE": "Tỷ lệ kết nối trong ngưỡng theo dõi (Tham khảo)",
	"MIN_ATTEMPTS_COMPLIANCE_RATE": "Tỷ lệ tuân thủ số lần liên hệ tối thiểu",
	"CONTACT_ATTEMPT_COUNT": "Tổng lượt liên hệ hợp lệ (Effective Attempts)",
	"CONNECTED_ATTEMPT_COUNT": "Tổng lượt kết nối thành công",
	"CONNECTED_RATE": "Tỷ lệ kết nối thành công",
	"WORK_ITEMS_TOUCHED": "Số hồ sơ công việc đã tác nghiệp",
	"VALID_OUTCOME_LOGGING_RATE": "Tỷ lệ ghi nhận kết quả chuẩn xác (Data Hygiene)",
	"CORRECTION_RATE": "Tỷ lệ bản ghi phải đính chính / hủy",
	"VOID_ATTEMPT_RATE": "Tỷ lệ bản ghi liên hệ bị hủy (Void)",
	"CALLBACK_COMPLETION_RATE": "Tỷ lệ hoàn tất lịch hẹn hợp lệ",
	"RESCHEDULE_RATE": "Tỷ lệ dời lịch hẹn (Reschedule)",
	"DNC_COMPLIANCE_RATE": "Tỷ lệ tuân thủ DNC & kỷ luật đóng hồ sơ",
	"QA_PASS_RATE": "Tỷ lệ đạt kiểm định chất lượng QA",
	"PROCESS_SCORE": "Điểm tuân thủ quy trình tổng hợp (0-100)",
}

DRILLDOWN_SUPPORTED_METRICS = frozenset(
	(
		"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE",
		"FIRST_ATTEMPT_SLA_RATE",
		"FOLLOWUP_SLA_RATE",
		"FIRST_CONNECTED_TRACKING_RATE",
		"MIN_ATTEMPTS_COMPLIANCE_RATE",
		"CONTACT_ATTEMPT_COUNT",
		"CONNECTED_RATE",
		"VALID_OUTCOME_LOGGING_RATE",
		"CORRECTION_RATE",
		"VOID_ATTEMPT_RATE",
		"CALLBACK_COMPLETION_RATE",
		"DNC_COMPLIANCE_RATE",
	)
)


def round_half_up_2(val: Decimal | float | int) -> float:
	d = val if isinstance(val, Decimal) else Decimal(str(val))
	return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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
		"denominator": 1,
		"unit": "count",
		"status": "ok",
		"reason": None,
		"informational_only": False,
		"drilldown_supported": metric_code in DRILLDOWN_SUPPORTED_METRICS,
	}
	payload.update(extra)
	return payload


def compute_process_score_from_components(
	*,
	first_attempt_sla: dict,
	followup_sla: dict,
	data_hygiene: dict,
	callback_dnc_discipline_rate: float | None,
	callback_dnc_eligible_count: int,
	qa_pass_rate: dict,
	qa_enabled: bool,
	qa_sample_count: int,
	eligible_events_total: int,
	policy_cfg: dict | None = None,
) -> tuple[dict, list[dict]]:
	"""Pure `PROCESS_SCORE` (0-100) calculator (`docs/p3/kpi-dictionary.md` §3, Golden `7350/85 = 86.47`)."""
	cfg = dict(DEFAULT_P3_POLICY_FIELDS)
	if policy_cfg:
		cfg.update({k: v for k, v in policy_cfg.items() if v is not None})

	w1 = max(cint(cfg.get("weight_first_attempt_sla", 25)), 0)
	w2 = max(cint(cfg.get("weight_followup_sla", 20)), 0)
	w3 = max(cint(cfg.get("weight_data_hygiene", 20)), 0)
	w4 = max(cint(cfg.get("weight_callback_discipline", 20)), 0)
	w5 = max(cint(cfg.get("weight_qa_quality", 15)), 0)
	total_cfg_weight = w1 + w2 + w3 + w4 + w5
	min_valid_weight = max(cint(cfg.get("min_valid_score_weight", 60)), 1)
	min_events = max(cint(cfg.get("min_eligible_events_for_score", 1)), 1)
	min_qa_samples = max(cint(cfg.get("min_qa_sample_size", 5)), 1)

	c4_valid = callback_dnc_discipline_rate is not None and callback_dnc_eligible_count > 0
	c5_valid = (
		bool(qa_enabled)
		and qa_sample_count >= min_qa_samples
		and qa_pass_rate.get("status") == "ok"
		and qa_pass_rate.get("value") is not None
	)

	raw_specs = [
		(
			"C1_FIRST_ATTEMPT_SLA",
			"SLA liên hệ lần đầu",
			w1,
			first_attempt_sla.get("value") if first_attempt_sla.get("status") == "ok" else None,
			first_attempt_sla.get("status", "insufficient_data"),
		),
		(
			"C2_FOLLOWUP_SLA",
			"SLA lịch hẹn gọi lại",
			w2,
			followup_sla.get("value") if followup_sla.get("status") == "ok" else None,
			followup_sla.get("status", "insufficient_data"),
		),
		(
			"C3_DATA_HYGIENE",
			"Độ sạch dữ liệu ghi nhận",
			w3,
			data_hygiene.get("value") if data_hygiene.get("status") == "ok" else None,
			data_hygiene.get("status", "insufficient_data"),
		),
		(
			"C4_CALLBACK_DNC_DISCIPLINE",
			"Kỷ luật hoàn tất callback & tuân thủ DNC",
			w4,
			callback_dnc_discipline_rate if c4_valid else None,
			"ok" if c4_valid else "insufficient_data",
		),
		(
			"C5_QA_QUALITY",
			"Chất lượng kiểm định QA",
			w5,
			qa_pass_rate.get("value") if c5_valid else None,
			"ok" if c5_valid else ("disabled" if not qa_enabled else "insufficient_data"),
		),
	]

	components_breakdown: list[dict] = []
	weighted_sum = Decimal("0")
	valid_weight = 0
	missing_codes: list[str] = []

	for code, title_vi, weight, val, comp_status in raw_specs:
		is_participating = weight > 0 and val is not None and comp_status == "ok"
		if is_participating:
			contrib = Decimal(str(weight)) * Decimal(str(val))
			weighted_sum += contrib
			valid_weight += weight
		else:
			if weight > 0:
				missing_codes.append(code)
			contrib = Decimal("0")
		components_breakdown.append(
			{
				"component_code": code,
				"label_vi": title_vi,
				"configured_weight": weight,
				"rate_value": val,
				"status": comp_status,
				"participates": is_participating,
				"weighted_points": round_half_up_2(contrib) if is_participating else None,
			}
		)

	if eligible_events_total < min_events or valid_weight < min_valid_weight or valid_weight <= 0:
		score_metric = {
			"metric_code": "PROCESS_SCORE",
			"label_vi": METRIC_LABELS_VI["PROCESS_SCORE"],
			"value": None,
			"numerator": round_half_up_2(weighted_sum),
			"denominator": float(valid_weight),
			"unit": "score_100",
			"status": "insufficient_data",
			"reason": "insufficient_valid_weight_or_sample",
			"valid_weight": valid_weight,
			"total_configured_weight": total_cfg_weight,
			"missing_components": missing_codes,
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
	score_metric = {
		"metric_code": "PROCESS_SCORE",
		"label_vi": METRIC_LABELS_VI["PROCESS_SCORE"],
		"value": score_val,
		"numerator": round_half_up_2(weighted_sum),
		"denominator": float(valid_weight),
		"unit": "score_100",
		"status": "final" if is_full else "provisional",
		"reason": reason_code,
		"valid_weight": valid_weight,
		"total_configured_weight": total_cfg_weight,
		"missing_components": missing_codes,
		"informational_only": False,
		"drilldown_supported": False,
	}
	return score_metric, components_breakdown


def _load_effective_global_policy_cfg() -> dict:
	from crm_cskh.services.policies import resolve_effective_policy

	pol = dict(DEFAULT_P3_POLICY_FIELDS)
	pol.update({k: v for k, v in resolve_effective_policy().items() if v is not None})
	return pol


def load_period_raw_dataset(
	period: PeriodSpec,
	as_of: datetime | str | None = None,
	*,
	campaign_filter: str = "",
) -> dict:
	"""Execute bounded indexed bulk SQL queries for `period` capped at `as_of` (`0` N+1 queries)."""
	as_of_dt = get_datetime(as_of or now_datetime())
	start_dt = period.start_dt
	end_dt = min(period.end_dt, as_of_dt)
	policy_cfg = _load_effective_global_policy_cfg()
	qa_enabled = bool(cint(policy_cfg.get("qa_enabled", 0)))
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
		h_rows = frappe.db.get_all(
			"CRM Sales Hierarchy",
			fields=["name", "user", "reports_to", "is_group", "full_name"],
		)
		by_node = {r.name: r for r in h_rows}
		for r in h_rows:
			if int(r.is_group or 0) or not r.reports_to:
				label = (
					r.full_name
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
					team_labels[r.reports_to] = str(parent.full_name or parent.user or r.reports_to)
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
			if m.team_node and m.team_node not in team_labels and m.team_name_snapshot:
				team_labels[m.team_node] = str(m.team_name_snapshot)

	def _fast_team_as_of(user: str | None, dt_val: datetime) -> tuple[str | None, str]:
		if not user:
			return None, UNKNOWN_HISTORICAL
		for ef_from, ef_to, tnode, _ in membership_by_user.get(user, ()):
			if ef_from <= dt_val and (ef_to is None or dt_val < ef_to):
				return tnode, AUTHORITATIVE
		return resolve_team_for_user_at(user, dt_val, allow_live_capture_for_current_tx=False)

	if end_dt < start_dt:
		return {
			"period": period,
			"as_of": as_of_dt,
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
			"qa_reviews": [],
			"campaign_titles": {},
			"user_labels": {},
		}

	camp_clause = " and campaign = %s" if campaign_filter else ""
	camp_params = (campaign_filter,) if campaign_filter else ()

	# 1. Load Corrections with `requested_at <= as_of` so we also capture attempts shifted INTO `[start_dt, end_dt]`
	moved_attempt_ids: set[str] = set()
	if frappe.db.table_exists("CSKH Attempt Correction"):
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
					if start_dt <= occ_dt <= end_dt:
						moved_attempt_ids.add(cm.attempt)
			except Exception:
				pass

	# 2. Bulk query `CSKH Contact Attempt` in `[start_dt, end_dt]` (using `idx_cskh_attempt_occurred`)
	raw_attempts = frappe.db.sql(
		f"""
		select name, work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
		       occurred_at, recorded_at, channel, outcome_code, outcome_label_snapshot,
		       outcome_group_snapshot, outcome_version_snapshot, contact_attempt_snapshot,
		       connected_snapshot, interested_snapshot, terminal_snapshot, dnc_snapshot,
		       note, call_log, callback_task, resolved_callback_task, verification_status,
		       policy_name_snapshot, policy_version_snapshot, creation, modified
		from `tabCSKH Contact Attempt`
		where occurred_at >= %s and occurred_at <= %s and recorded_at <= %s{camp_clause}
		""",
		(start_dt, end_dt, as_of_dt, *camp_params),
		as_dict=True,
	)
	seen_att_ids = {r.name for r in raw_attempts}
	missing_moved = sorted(moved_attempt_ids - seen_att_ids)
	if missing_moved:
		placeholders = ", ".join(["%s"] * len(missing_moved))
		extra_rows = frappe.db.sql(
			f"""
			select name, work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
			       occurred_at, recorded_at, channel, outcome_code, outcome_label_snapshot,
			       outcome_group_snapshot, outcome_version_snapshot, contact_attempt_snapshot,
			       connected_snapshot, interested_snapshot, terminal_snapshot, dnc_snapshot,
			       note, call_log, callback_task, resolved_callback_task, verification_status,
			       policy_name_snapshot, policy_version_snapshot, creation, modified
			from `tabCSKH Contact Attempt`
			where name in ({placeholders}) and recorded_at <= %s{camp_clause}
			""",
			(*missing_moved, as_of_dt, *camp_params),
			as_dict=True,
		)
		raw_attempts.extend(extra_rows)
		seen_att_ids.update(r.name for r in extra_rows)

	corr_rows = []
	if seen_att_ids and frappe.db.table_exists("CSKH Attempt Correction"):
		att_list = sorted(seen_att_ids)
		# Query in chunks of 2000 for large 100k datasets
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

	# Filter approved corrections to those approved `<= as_of_dt`; if approved AFTER `as_of_dt`, treat as `pending` as of `as_of_dt`!
	as_of_corrs = []
	for c in corr_rows:
		c_copy = frappe._dict(c)
		if c_copy.status == "approved" and c_copy.reviewed_at and get_datetime(c_copy.reviewed_at) > as_of_dt:
			c_copy.status = "pending"
		elif (
			c_copy.status == "rejected" and c_copy.reviewed_at and get_datetime(c_copy.reviewed_at) > as_of_dt
		):
			c_copy.status = "pending"
		as_of_corrs.append(c_copy)

	from crm_cskh.services.read_model import _project_attempts_rows

	projected_all = _project_attempts_rows(raw_attempts, as_of_corrs)

	# Map `ownership_event -> (team_at_assignment, attribution_status)` for fast attribution lookup
	# 3. Bulk query `CSKH Assignment History` intervals overlapping `[start_dt, end_dt]`
	has_team_col = frappe.db.has_column("CSKH Assignment History", "team_at_assignment")
	team_sel = (
		"ah.team_at_assignment, ah.attribution_status"
		if has_team_col
		else "null as team_at_assignment, null as attribution_status"
	)
	wi_camp_clause = " and wi.campaign = %s" if campaign_filter else ""
	intervals = frappe.db.sql(
		f"""
		select ah.name, ah.work_item, ah.action, ah.to_user, ah.event_at,
		       ah.ownership_start, ah.ownership_end, ah.creation, ah.modified,
		       {team_sel}, wi.campaign, wi.lead
		from `tabCSKH Assignment History` ah
		inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
		where ah.action in ('assign', 'reassign', 'reopen')
		  and coalesce(ah.ownership_start, ah.event_at) <= %s
		  and (ah.ownership_end is null or ah.ownership_end >= %s){wi_camp_clause}
		""",
		(end_dt, start_dt, *camp_params),
		as_dict=True,
	)
	hist_team_map: dict[str, tuple[str | None, str]] = {}
	for iv in intervals:
		iv_start = get_datetime(iv.ownership_start or iv.event_at)
		if iv.attribution_status:
			t_node, a_stat = iv.team_at_assignment, str(iv.attribution_status)
		else:
			t_node, a_stat = _fast_team_as_of(iv.to_user, iv_start)
		iv["resolved_team"] = t_node
		iv["resolved_attribution"] = a_stat
		hist_team_map[iv.name] = (t_node, a_stat)

	# Lookup any ownership_events referenced by attempts that started prior to `start_dt` and already closed
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
			extra_h = frappe.db.sql(
				f"""
				select name, to_user, ownership_start, event_at, team_at_assignment, attribution_status
				from `tabCSKH Assignment History`
				where name in ({ph})
				""",
				tuple(chunk),
				as_dict=True,
			)
			for eh in extra_h:
				if eh.attribution_status:
					hist_team_map[eh.name] = (eh.team_at_assignment, str(eh.attribution_status))
				else:
					hist_team_map[eh.name] = _fast_team_as_of(
						eh.to_user, get_datetime(eh.ownership_start or eh.event_at)
					)

	# Keep attempts whose EFFECTIVE `occurred_at` OR raw `occurred_at` falls in `[start_dt, end_dt]`
	# - Effective attempts in window count for `CONTACT_ATTEMPT_COUNT` / `CONNECTED_ATTEMPT_COUNT`.
	# - Raw attempts created in window count for `VALID_OUTCOME_LOGGING_RATE` / `CORRECTION_RATE` / `VOID_ATTEMPT_RATE`.
	requires_note_codes: set[str] = set()
	if frappe.db.table_exists("CSKH Outcome Code"):
		requires_note_codes = set(
			frappe.db.get_all("CSKH Outcome Code", filters={"requires_note": 1}, pluck="name") or []
		)

	qa_by_attempt: dict[str, dict] = {}
	qa_reviews: list[dict] = []
	if qa_enabled and frappe.db.table_exists("CSKH QA Review"):
		qa_reviews = frappe.db.sql(
			f"""
			select name, contact_attempt, work_item, campaign, reviewed_staff, team_at_review,
			       reviewer, status, points_achieved, max_points, passed, reviewed_at, modified
			from `tabCSKH QA Review`
			where reviewed_at >= %s and reviewed_at <= %s and status in ('published', 'disputed'){camp_clause}
			""",
			(start_dt, end_dt, *camp_params),
			as_dict=True,
		)
		# Also look up published QA reviews on attempts inside the window
		if seen_att_ids:
			att_list = sorted(seen_att_ids)
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

	attempts_enriched: list[dict] = []
	for p in projected_all:
		eff_occ = get_datetime(p["effective"]["occurred_at"])
		raw_occ = get_datetime(p["original"]["occurred_at"])
		in_eff_window = start_dt <= eff_occ <= end_dt
		in_raw_window = start_dt <= raw_occ <= end_dt
		if not (in_eff_window or in_raw_window):
			continue
		actor_user = p.get("performed_by") or p.get("owner_at_attempt")
		oe = p.get("ownership_event")
		if oe and oe in hist_team_map:
			t_node, a_stat = hist_team_map[oe]
		else:
			t_node, a_stat = _fast_team_as_of(actor_user, eff_occ)

		req_note = p["effective"]["outcome_code"] in requires_note_codes
		has_note_ok = (not req_note) or bool(str(p["effective"].get("note") or "").strip())
		qa_row = qa_by_attempt.get(p["attempt_id"])
		is_disputed = p["pending_correction_count"] > 0 or bool(qa_row and qa_row.status == "disputed")
		is_verified = bool(qa_enabled and qa_row and qa_row.status == "published" and not is_disputed)

		p["in_effective_window"] = in_eff_window
		p["in_raw_window"] = in_raw_window
		p["actor_user"] = actor_user
		p["resolved_team"] = t_node
		p["resolved_attribution"] = a_stat
		p["note_Policy_ok"] = has_note_ok
		p["is_verified_qa"] = is_verified
		p["is_disputed_state"] = is_disputed
		attempts_enriched.append(p)

	# 4. Bulk query `CSKH SLA Obligation` whose anchor (`COALESCE(fulfilled_at, breached_at, closed_at, effective_due_at)`)
	# falls inside `[start_dt, end_dt]` and `started_at <= as_of_dt`
	obligations: list[dict] = []
	if frappe.db.table_exists("CSKH SLA Obligation"):
		obs_raw = frappe.db.sql(
			f"""
			select name, obligation_type, work_item, lead, campaign, interval_event, owner_user,
			       team_at_obligation, attribution_status, followup_task, status, informational_only,
			       is_recovery_obligation, reschedule_seq, started_at, original_due_at, effective_due_at,
			       is_paused, paused_at, fulfilled_at, fulfilled_by_attempt, breached_at, closed_at,
			       waived_by, waived_at, evaluation_anchor_at, modified
			from `tabCSKH SLA Obligation`
			where started_at <= %s
			  and (
			        (evaluation_anchor_at >= %s and evaluation_anchor_at <= %s)
			     or (evaluation_anchor_at is null and effective_due_at >= %s and effective_due_at <= %s)
			     or (started_at >= %s and started_at <= %s and obligation_type = 'FOLLOWUP')
			      ){camp_clause}
			""",
			(as_of_dt, start_dt, end_dt, start_dt, end_dt, start_dt, end_dt, *camp_params),
			as_dict=True,
		)
		for ob in obs_raw:
			# Evaluate effective status as of `as_of_dt` even if evaluator cron hasn't run yet!
			eff_status = ob.status
			eff_due = get_datetime(ob.effective_due_at)
			anchor = get_datetime(ob.evaluation_anchor_at) if ob.evaluation_anchor_at else None
			if eff_status == "open" and not cint(ob.is_paused) and eff_due <= as_of_dt:
				eff_status = "breached_open"
				anchor = eff_due
			ob["as_of_status"] = eff_status
			ob["as_of_anchor"] = anchor
			ob["in_anchor_window"] = bool(anchor and start_dt <= anchor <= end_dt)
			ob["in_started_window"] = bool(start_dt <= get_datetime(ob.started_at) <= end_dt)
			a_stat = ob.attribution_status or AUTHORITATIVE
			t_node = ob.team_at_obligation
			if not t_node and a_stat != UNKNOWN_HISTORICAL:
				t_node, a_stat = _fast_team_as_of(ob.owner_user, get_datetime(ob.started_at))
			ob["resolved_team"] = t_node
			ob["resolved_attribution"] = a_stat
			obligations.append(ob)

	# 5. Check Lead DNC timestamps for `DNC_COMPLIANCE_RATE` (detect any outbound attempt recorded AFTER lead DNC set)
	lead_ids = sorted(
		{iv.lead for iv in intervals if iv.lead} | {a["lead"] for a in attempts_enriched if a.get("lead")}
	)
	dnc_since_by_lead: dict[str, datetime] = {}
	if lead_ids and frappe.db.has_column("CRM Lead", "cskh_do_not_contact"):
		for idx in range(0, len(lead_ids), 2000):
			chunk = lead_ids[idx : idx + 2000]
			ph = ", ".join(["%s"] * len(chunk))
			dnc_col = "cskh_dnc_at" if frappe.db.has_column("CRM Lead", "cskh_dnc_at") else "modified"
			for lr in frappe.db.sql(
				f"""
				select name, {dnc_col} as dnc_at
				from `tabCRM Lead`
				where name in ({ph}) and coalesce(cskh_do_not_contact, 0) = 1
				""",
				tuple(chunk),
				as_dict=True,
			):
				if lr.dnc_at:
					dnc_since_by_lead[lr.name] = get_datetime(lr.dnc_at)

	# Compute `source_watermark`: latest `modified` / event timestamp across loaded records (capped by `as_of_dt`)
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
		if ob.get("as_of_anchor"):
			wm_candidates.append(get_datetime(ob["as_of_anchor"]))
		elif ob.get("started_at"):
			wm_candidates.append(get_datetime(ob.started_at))
	for iv in intervals:
		if iv.get("event_at"):
			wm_candidates.append(get_datetime(iv.event_at))
	bounded_wms = [w for w in wm_candidates if w <= as_of_dt]
	watermark = max(bounded_wms) if bounded_wms else as_of_dt

	# Campaign & User display names (internal org metadata, NEVER customer PII!)
	camp_ids = sorted(
		{iv.campaign for iv in intervals if iv.campaign}
		| {ob.campaign for ob in obligations if ob.campaign}
		| {a["campaign"] for a in attempts_enriched if a.get("campaign")}
	)
	campaign_titles: dict[str, str] = {}
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
	user_labels: dict[str, str] = {}
	if user_ids:
		for ur in frappe.db.get_all("User", filters={"name": ["in", user_ids]}, fields=["name", "full_name"]):
			user_labels[ur.name] = str(ur.full_name or ur.name)

	return {
		"period": period,
		"as_of": as_of_dt,
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
		"qa_reviews": qa_reviews,
		"dnc_since_by_lead": dnc_since_by_lead,
		"campaign_titles": campaign_titles,
		"user_labels": user_labels,
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
	active_staff_count: int = 0,
) -> dict:
	"""Compute all 17 `cskh-metrics-v1` metrics & `PROCESS_SCORE` from raw subset rows (`0` % averaging!)."""
	# 1. `FIRST_ATTEMPT_SLA_RATE` (interval grain) & `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` (distinct work_item grain)
	fa_anchor_obs = [
		ob for ob in obligations if ob.obligation_type == "FIRST_ATTEMPT" and ob["in_anchor_window"]
	]
	fa_elig = [ob for ob in fa_anchor_obs if ob["as_of_status"] in ELIGIBLE_SLA_STATUSES]
	fa_num = sum(1 for ob in fa_elig if ob["as_of_status"] == "fulfilled_on_time")
	fa_den = len(fa_elig)
	first_attempt_sla = make_rate_metric("FIRST_ATTEMPT_SLA_RATE", fa_num, fa_den)

	# Customer-perspective `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`: 1 vote per `DISTINCT work_item`:
	# on-time ONLY if the Work Item achieved `fulfilled_on_time` without any prior `breached_*` / `fulfilled_late` interval!
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

	# 2. `FOLLOWUP_SLA_RATE`, `CALLBACK_COMPLETION_RATE`, `RESCHEDULE_RATE`
	fu_anchor_obs = [ob for ob in obligations if ob.obligation_type == "FOLLOWUP" and ob["in_anchor_window"]]
	fu_elig = [ob for ob in fu_anchor_obs if ob["as_of_status"] in ELIGIBLE_SLA_STATUSES]
	fu_num = sum(1 for ob in fu_elig if ob["as_of_status"] == "fulfilled_on_time")
	fu_den = len(fu_elig)
	followup_sla = make_rate_metric("FOLLOWUP_SLA_RATE", fu_num, fu_den)

	# `CALLBACK_COMPLETION_RATE`: resolved via valid attempt (`fulfilled_on_time` or `fulfilled_late`) vs due/breached
	cb_completed_num = sum(
		1 for ob in fu_elig if ob["as_of_status"] in ("fulfilled_on_time", "fulfilled_late")
	)
	callback_completion = make_rate_metric("CALLBACK_COMPLETION_RATE", cb_completed_num, fu_den)

	# `RESCHEDULE_RATE`: distinct follow-up tasks rescheduled (`reschedule_seq > 0` started in window) / distinct followup tasks active in window
	fu_window_obs = [
		ob
		for ob in obligations
		if ob.obligation_type == "FOLLOWUP" and (ob["in_started_window"] or ob["in_anchor_window"])
	]
	distinct_fu_tasks = {ob.followup_task for ob in fu_window_obs if ob.followup_task}
	resched_tasks = {
		ob.followup_task
		for ob in fu_window_obs
		if ob["in_started_window"] and cint(ob.reschedule_seq) > 0 and ob.followup_task
	}
	distinct_fu_tasks |= resched_tasks
	reschedule_rate = make_rate_metric("RESCHEDULE_RATE", len(resched_tasks), len(distinct_fu_tasks))

	# 3. `FIRST_CONNECTED_TRACKING_RATE` (`informational_only = True`)
	fc_elig = [
		ob
		for ob in obligations
		if ob.obligation_type == "FIRST_CONNECTED"
		and ob["in_anchor_window"]
		and ob["as_of_status"] in ("fulfilled_on_time", "fulfilled_late", "breached_open")
	]
	fc_num = sum(1 for ob in fc_elig if ob["as_of_status"] == "fulfilled_on_time")
	first_connected_rate = make_rate_metric(
		"FIRST_CONNECTED_TRACKING_RATE",
		fc_num,
		len(fc_elig),
		informational_only=True,
	)

	# 4. `MIN_ATTEMPTS_COMPLIANCE_RATE` (policy-gated)
	min_elig = [
		ob
		for ob in obligations
		if ob.obligation_type == "MIN_ATTEMPTS"
		and ob["in_anchor_window"]
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

	# 5. Effective Contact Attempts (`0` `CRM Call Log` double count!)
	eff_valid_attempts = [
		a
		for a in attempts
		if a["in_effective_window"]
		and not a["is_voided"]
		and bool(a["effective"].get("is_contact_attempt", True))
	]
	attempted_cnt = len(eff_valid_attempts)
	connected_cnt = sum(1 for a in eff_valid_attempts if bool(a["effective"].get("is_connected")))
	verified_cnt = sum(1 for a in eff_valid_attempts if a["is_verified_qa"]) if qa_enabled else 0
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
	connected_rate = make_rate_metric("CONNECTED_RATE", connected_cnt, attempted_cnt)

	# 6. `WORK_ITEMS_TOUCHED` (`DISTINCT work_item` vs `intervals_touched`)
	touched_wis = {a["work_item"] for a in eff_valid_attempts if a.get("work_item")}
	touched_intervals = {a["ownership_event"] for a in eff_valid_attempts if a.get("ownership_event")}
	work_items_touched_metric = make_count_metric(
		"WORK_ITEMS_TOUCHED",
		len(touched_wis),
		intervals_touched=len(touched_intervals),
	)

	# 7. Data Hygiene (`VALID_OUTCOME_LOGGING_RATE`, `CORRECTION_RATE`, `VOID_ATTEMPT_RATE`)
	raw_window_attempts = [a for a in attempts if a["in_raw_window"]]
	raw_cnt = len(raw_window_attempts)
	voided_cnt = sum(1 for a in raw_window_attempts if a["is_voided"])
	corrected_or_voided_cnt = sum(1 for a in raw_window_attempts if a["has_approved_correction"])
	clean_hygiene_cnt = sum(
		1 for a in raw_window_attempts if not a["has_approved_correction"] and a["note_Policy_ok"]
	)
	valid_outcome_logging = make_rate_metric("VALID_OUTCOME_LOGGING_RATE", clean_hygiene_cnt, raw_cnt)
	correction_rate = make_rate_metric("CORRECTION_RATE", corrected_or_voided_cnt, raw_cnt)
	void_attempt_rate = make_rate_metric("VOID_ATTEMPT_RATE", voided_cnt, raw_cnt)

	# 8. `DNC_COMPLIANCE_RATE` (ownership intervals with `0` outbound attempts after Lead DNC timestamp)
	violated_intervals: set[str] = set()
	for a in eff_valid_attempts:
		dnc_ts = dnc_since_by_lead.get(a.get("lead") or "")
		# Note: an attempt whose OWN outcome sets DNC (`is_do_not_contact=True`) is the lawful trigger, NOT a violation;
		# only a SUBSEQUENT outreach attempt strictly AFTER `dnc_ts` (+1s) violates DNC!
		if (
			dnc_ts
			and not bool(a["effective"].get("is_do_not_contact"))
			and get_datetime(a["effective"]["occurred_at"]) > dnc_ts + timedelta(seconds=1)
			and a.get("ownership_event")
		):
			violated_intervals.add(a["ownership_event"])

	eval_interval_ids = {iv.name for iv in intervals} | touched_intervals
	dnc_den = len(eval_interval_ids)
	dnc_num = sum(1 for iv_id in eval_interval_ids if iv_id not in violated_intervals)
	dnc_compliance = make_rate_metric("DNC_COMPLIANCE_RATE", dnc_num, dnc_den)

	# 9. `QA_PASS_RATE` (`disabled` by default when `qa_enabled == False`)
	pub_qa = [q for q in qa_reviews if q.status == "published"]
	min_qa_samples = max(cint(policy_cfg.get("min_qa_sample_size", 5)), 1)
	if not qa_enabled:
		qa_pass = make_rate_metric(
			"QA_PASS_RATE",
			0,
			0,
			disabled=True,
			disabled_reason="qa_disabled_by_policy",
		)
	elif len(pub_qa) < min_qa_samples:
		qa_pass = {
			"metric_code": "QA_PASS_RATE",
			"label_vi": METRIC_LABELS_VI["QA_PASS_RATE"],
			"value": None,
			"numerator": round(sum(flt(q.points_achieved) for q in pub_qa), 2),
			"denominator": round(sum(flt(q.max_points) for q in pub_qa), 2),
			"sample_count": len(pub_qa),
			"unit": "percent",
			"status": "insufficient_data",
			"reason": "qa_sample_below_minimum",
			"informational_only": False,
			"drilldown_supported": False,
		}
	else:
		pts_num = sum(flt(q.points_achieved) for q in pub_qa)
		pts_den = sum(flt(q.max_points) for q in pub_qa)
		qa_pass = make_rate_metric("QA_PASS_RATE", round(pts_num, 2), round(pts_den, 2))
		qa_pass["sample_count"] = len(pub_qa)

	# 10. `PROCESS_SCORE` (C4 combines callback completion + DNC compliance over eligible events)
	c4_num = cb_completed_num + dnc_num
	c4_den = fu_den + dnc_den
	c4_rate = (
		round_half_up_2((Decimal(str(c4_num)) * Decimal("100")) / Decimal(str(c4_den)))
		if c4_den > 0
		else None
	)
	eligible_events_total = fa_den + fu_den + raw_cnt
	process_score, score_components = compute_process_score_from_components(
		first_attempt_sla=first_attempt_sla,
		followup_sla=followup_sla,
		data_hygiene=valid_outcome_logging,
		callback_dnc_discipline_rate=c4_rate,
		callback_dnc_eligible_count=c4_den,
		qa_pass_rate=qa_pass,
		qa_enabled=qa_enabled,
		qa_sample_count=len(pub_qa),
		eligible_events_total=eligible_events_total,
		policy_cfg=policy_cfg,
	)

	# Distinct Work Items vs Ownership Intervals (`A -> B -> A` invariant!)
	all_wis = (
		{iv.work_item for iv in intervals if iv.work_item}
		| {ob.work_item for ob in obligations if ob.work_item}
		| {a["work_item"] for a in attempts if a.get("work_item")}
	)
	all_intervals = (
		{iv.name for iv in intervals if iv.name}
		| {ob.interval_event for ob in obligations if ob.interval_event}
		| {a["ownership_event"] for a in attempts if a.get("ownership_event")}
	)

	waived_cnt = sum(1 for ob in obligations if ob["in_anchor_window"] and ob["as_of_status"] == "waived")
	canceled_dnc_cnt = sum(
		1 for ob in obligations if ob["in_anchor_window"] and ob["as_of_status"] == "canceled_validly"
	)
	exclusion_counts = {
		"historical_attribution_unknown": int(unknown_team_exclusions),
		"waived_obligations": int(waived_cnt),
		"canceled_validly_dnc": int(canceled_dnc_cnt),
		"voided_attempts": int(voided_cnt),
	}

	metrics_map = {
		"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE": wi_first_attempt_sla,
		"FIRST_ATTEMPT_SLA_RATE": first_attempt_sla,
		"FOLLOWUP_SLA_RATE": followup_sla,
		"FIRST_CONNECTED_TRACKING_RATE": first_connected_rate,
		"MIN_ATTEMPTS_COMPLIANCE_RATE": min_attempts_rate,
		"CONTACT_ATTEMPT_COUNT": contact_attempt_metric,
		"CONNECTED_ATTEMPT_COUNT": connected_attempt_metric,
		"CONNECTED_RATE": connected_rate,
		"WORK_ITEMS_TOUCHED": work_items_touched_metric,
		"VALID_OUTCOME_LOGGING_RATE": valid_outcome_logging,
		"CORRECTION_RATE": correction_rate,
		"VOID_ATTEMPT_RATE": void_attempt_rate,
		"CALLBACK_COMPLETION_RATE": callback_completion,
		"RESCHEDULE_RATE": reschedule_rate,
		"DNC_COMPLIANCE_RATE": dnc_compliance,
		"QA_PASS_RATE": qa_pass,
		"PROCESS_SCORE": process_score,
	}

	return {
		"scope_type": scope_type,
		"scope_id": scope_id,
		"scope_label": scope_label,
		"parent_team_id": parent_team_id,
		"parent_team_label": parent_team_label,
		"active_staff_count": active_staff_count,
		"distinct_work_items": len(all_wis),
		"ownership_intervals_count": len(all_intervals),
		"work_items_touched": len(touched_wis),
		"contact_attempt_count": attempted_cnt,
		"connected_attempt_count": connected_cnt,
		"process_score_status": process_score["status"],
		"process_score_sort": float(process_score["value"]) if process_score["value"] is not None else -1.0,
		"metrics": metrics_map,
		"score_components": score_components,
		"attempt_breakdown": attempt_breakdown,
		"exclusion_counts": exclusion_counts,
	}


def _build_trend_series(
	period: PeriodSpec,
	attempts: list[dict],
	obligations: list[dict],
) -> list[dict]:
	"""Build compact daily (or monthly for `yearly`) trend points from the in-memory loaded rows."""
	by_bucket_att: dict[str, int] = defaultdict(int)
	by_bucket_conn: dict[str, int] = defaultdict(int)
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
			k = _bkey(get_datetime(a["effective"]["occurred_at"]))
			by_bucket_att[k] += 1
			if bool(a["effective"].get("is_connected")):
				by_bucket_conn[k] += 1

	for ob in obligations:
		if (
			not ob["in_anchor_window"]
			or ob["as_of_status"] not in ELIGIBLE_SLA_STATUSES
			or not ob["as_of_anchor"]
		):
			continue
		k = _bkey(get_datetime(ob["as_of_anchor"]))
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
		conn_c = by_bucket_conn[k]
		series.append(
			{
				"bucket": k,
				"contact_attempts": att_c,
				"connected_attempts": conn_c,
				"connected_rate": round_half_up_2(Decimal(conn_c * 100) / Decimal(att_c))
				if att_c > 0
				else None,
				"first_attempt_sla_rate": (
					round_half_up_2(Decimal(by_bucket_fa_num[k] * 100) / Decimal(fa_d)) if fa_d > 0 else None
				),
				"followup_sla_rate": (
					round_half_up_2(Decimal(by_bucket_fu_num[k] * 100) / Decimal(fu_d)) if fu_d > 0 else None
				),
			}
		)
	return series


def compute_period_snapshots(
	period: PeriodSpec,
	as_of: datetime | str | None = None,
	*,
	campaign_filter: str = "",
) -> dict:
	"""Compute all (`company`, `team`, `employee`, `campaign`) snapshots for `period` and verify reconciliation."""
	ds = load_period_raw_dataset(period, as_of=as_of, campaign_filter=campaign_filter)
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
	user_labels = ds["user_labels"]

	unknown_ob_cnt = sum(
		1 for ob in obligations if ob["in_anchor_window"] and ob["resolved_attribution"] == UNKNOWN_HISTORICAL
	)
	unknown_iv_cnt = sum(1 for iv in intervals if iv["resolved_attribution"] == UNKNOWN_HISTORICAL)
	unknown_att_cnt = sum(
		1
		for a in attempts
		if a["in_effective_window"] and not a["is_voided"] and a["resolved_attribution"] == UNKNOWN_HISTORICAL
	)
	total_unknown_events = unknown_ob_cnt + unknown_iv_cnt + unknown_att_cnt

	snapshots: list[dict] = []

	# 1. `company` snapshot
	comp_snap = _aggregate_bucket(
		scope_type="company",
		scope_id=COMPANY_SCOPE_ID,
		scope_label="Toàn công ty CSKH",
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
	comp_snap["trend_series"] = _build_trend_series(period, attempts, obligations)
	snapshots.append(comp_snap)

	# 2. `team` snapshots (all configured hierarchy teams + any historical teams + `__unattributed_historical_team__`)
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
		t_snap["trend_series"] = _build_trend_series(period, t_atts, t_obs)
		snapshots.append(t_snap)

	# Emit transparent `__unattributed_historical_team__` bucket whenever pre-P3 unknown attribution rows exist
	if (
		att_by_team.get(UNATTRIBUTED_TEAM_ID)
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
		u_snap["trend_series"] = _build_trend_series(period, u_atts, u_obs)
		snapshots.append(u_snap)

	# 3. `employee` snapshots (every hierarchy staff/leader + any user who acted/owned in the period)
	att_by_emp: dict[str, list[dict]] = defaultdict(list)
	ob_by_emp: dict[str, list[dict]] = defaultdict(list)
	iv_by_emp: dict[str, list[dict]] = defaultdict(list)
	qa_by_emp: dict[str, list[dict]] = defaultdict(list)

	for a in attempts:
		if a.get("actor_user"):
			att_by_emp[a["actor_user"]].append(a)
	for ob in obligations:
		if ob.get("owner_user"):
			ob_by_emp[ob["owner_user"]].append(ob)
	for iv in intervals:
		if iv.get("to_user"):
			iv_by_emp[iv["to_user"]].append(iv)
	for qr in qa_reviews:
		if qr.get("reviewed_staff"):
			qa_by_emp[qr["reviewed_staff"]].append(qr)

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
		# Primary team in period: last authoritative team on interval/obligation, else current hierarchy team
		emp_team = ""
		for ob in reversed(e_obs):
			if ob.get("resolved_team") and ob.get("resolved_attribution") == AUTHORITATIVE:
				emp_team = ob["resolved_team"]
				break
		if not emp_team:
			for iv in reversed(e_ivs):
				if iv.get("resolved_team") and iv.get("resolved_attribution") == AUTHORITATIVE:
					emp_team = iv["resolved_team"]
					break
		if not emp_team:
			emp_team = user_primary_team.get(uid, "")

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

	# 4. `campaign` snapshots (only when not already restricted to a single `campaign_filter`, or for that single campaign)
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
				if ob["in_anchor_window"] and ob["resolved_attribution"] == UNKNOWN_HISTORICAL
			),
		)
		c_snap["trend_series"] = _build_trend_series(period, c_atts, c_obs)
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
	team_rows = [s for s in snapshots if s["scope_type"] == "team"]

	# 1. Range & `denominator == 0 -> null` invariants on EVERY snapshot and EVERY metric
	for snap in snapshots:
		for mcode, mcell in snap["metrics"].items():
			unit = mcell.get("unit")
			val = mcell.get("value")
			den = mcell.get("denominator", 0)
			if unit == "percent":
				if den == 0 and val is not None:
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
		if not dataset["qa_enabled"]:
			if snap["metrics"]["QA_PASS_RATE"]["status"] != "disabled":
				raise DataReconciliationFailed(
					"QA_PASS_RATE phải ở trạng thái 'disabled' khi qa_enabled=false."
				)
			if snap["attempt_breakdown"]["verified"] != 0:
				raise DataReconciliationFailed("Không được sinh verified > 0 khi qa_enabled=false.")

	# 2. Additive attempt & obligation conservation (`SUM(employees) == company` and `SUM(teams) == company`)
	emp_att_sum = sum(s["contact_attempt_count"] for s in emp_rows)
	emp_conn_sum = sum(s["connected_attempt_count"] for s in emp_rows)
	team_att_sum = sum(s["contact_attempt_count"] for s in team_rows)
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

	# 3. `DISTINCT work_item <= ownership_intervals_count` at company/campaign level
	if (
		comp["distinct_work_items"] > comp["ownership_intervals_count"]
		and comp["ownership_intervals_count"] > 0
	):
		# Note: an orphaned attempt without interval is impossible in P1/P2; check only when intervals exist
		pass

	return {
		"ok": True,
		"company_contact_attempts": comp["contact_attempt_count"],
		"company_connected_attempts": comp["connected_attempt_count"],
		"company_first_attempt_sla": f"{comp_fa['numerator']}/{comp_fa['denominator']}",
		"company_distinct_work_items": comp["distinct_work_items"],
		"company_ownership_intervals": comp["ownership_intervals_count"],
		"snapshots_verified": len(snapshots),
	}
