"""Snapshot-backed P3 quality reporting & reconciled metric drill-down service (`P3-07`, `P3-08`).

Guarantees (`docs/p3/report-api-contract.md`, `docs/p3/authorization-matrix.md`):
  * **Indexed Snapshot Reads (`OBS-P2-02`):**
    Dashboard endpoints (`get_executive_overview`, `get_team_comparison`, `get_employee_quality`,
    `get_campaign_quality`, `get_my_scorecard`) read published `CSKH KPI Snapshot` rows via
    `idx_cskh_kpisnap_lookup` / `idx_cskh_kpisnap_rank` (`0` full-table scans on `tabCSKH Contact Attempt`).
  * **Strict Role & Scope Isolation (`S01..S09`, `S15`):**
    - `Staff`: ONLY `get_my_scorecard`, `get_employee_quality(employee=self)`, and `get_metric_drilldown(employee=self)`.
      `403 FORBIDDEN_SCOPE` on `get_executive_overview`, `get_team_comparison`, `get_campaign_quality`, or `employee != self`.
    - `Team Leader`: strictly bounded to live `CRM Sales Hierarchy` subtree (`403 FORBIDDEN_SCOPE` outside subtree).
    - `BOD Viewer`: company-wide aggregate KPIs without `System Manager` (`0` customer PII ever; `403 FORBIDDEN_SCOPE`
      on `get_my_scorecard` or `get_metric_drilldown(..., include_pii=1)`).
    - `CRM Admin` / `System`: full operational & audit visibility.
  * **Transfer-Safe Privacy Projection (`S03`, `S15`):**
    On `get_metric_drilldown`, any row whose Lead is NOT currently readable by the caller (`Staff` / `Leader`)
    has `lead`, `customer_name`, `phone`, `note_excerpt` scrubbed to `None` with `"pii_redacted_due_to_transfer": True`.
  * **Honest Policy & Freshness Envelope:**
    Every response includes `policy_notice` (`config_stage: "DEV_CONFIGURABLE"`, `production_approved: False`),
    `freshness` (`fresh` | `stale_recomputing` | `empty_not_computed`), and `exclusion_counts`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import timedelta

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime

from crm_cskh.api._params import parse_bool, parse_optional_str, parse_pagination
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p1_permissions import (
	ROLE_ADMIN,
	ROLE_BOD,
	_campaign_in_leader_scope,
	_is_system,
	_node_in_subtree,
	_roles,
	_team_bounds,
)
from crm_cskh.p2_permissions import _in_team, _is_leader, _is_staff, is_work_item_readable_for_workflow
from crm_cskh.services.attribution import AUTHORITATIVE
from crm_cskh.services.calendar import DEFAULT_TIMEZONE, format_calendar_iso
from crm_cskh.services.errors import (
	ForbiddenScope,
	InvalidMetric,
	InvalidRequest,
)
from crm_cskh.services.kpi_calculator import (
	COMPANY_SCOPE_ID,
	DRILLDOWN_SUPPORTED_METRICS,
	ELIGIBLE_SLA_STATUSES,
	METRIC_LABELS_VI,
	METRIC_VERSION,
	UNATTRIBUTED_TEAM_ID,
	_aggregate_bucket,
	_build_trend_series,
	_load_effective_global_policy_cfg,
	load_period_raw_dataset,
)
from crm_cskh.services.kpi_periods import PeriodSpec, resolve_period_spec
from crm_cskh.services.kpi_runs import (
	execute_kpi_recompute,
	has_pending_dirty_scope_for_period,
	unpack_snapshot_metrics,
)
from crm_cskh.services.sanitizer import redact_pii_text

AUTO_COMPUTE_MAX_RAW_ROWS = 2000

ALL_SORTABLE_METRICS = frozenset(METRIC_LABELS_VI.keys())
VALID_DRILLDOWN_BUCKETS = frozenset(("all_denominator", "numerator_only", "breached_only", "excluded"))

POLICY_NOTICE_PAYLOAD = {
	"config_stage": "DEV_CONFIGURABLE",
	"production_approved": False,
	"message_vi": (
		"Ngưỡng SLA và trọng số điểm hiện là cấu hình DEV/configurable, "
		"chưa phải chính sách production do BOD phê duyệt."
	),
}


@dataclass(frozen=True)
class ReportActor:
	user: str
	role_profile: str  # "crm_admin" | "bod" | "team_leader" | "staff"
	pii_allowed: bool
	root_node: str | None
	allowed_nodes: tuple[str, ...] | None
	allowed_users: tuple[str, ...] | None


def resolve_report_actor(user: str | None = None) -> ReportActor:
	"""Resolve the authenticated caller's P3 reporting capability profile."""
	actor = user or frappe.session.user
	if not actor or actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập để truy cập báo cáo chất lượng CSKH.")
	enabled = frappe.db.get_value("User", actor, "enabled")
	if enabled is not None and not cint(enabled):
		raise ForbiddenScope("Tài khoản người dùng đã bị vô hiệu hóa.")

	roles = _roles(actor)
	if _is_system(actor) or ROLE_ADMIN in roles:
		return ReportActor(
			user=actor,
			role_profile="crm_admin",
			pii_allowed=True,
			root_node=None,
			allowed_nodes=None,
			allowed_users=None,
		)

	if _is_leader(actor):
		node_row = frappe.db.get_value(
			"CRM Sales Hierarchy",
			{"user": actor},
			["name", "lft", "rgt"],
			as_dict=True,
		)
		if node_row:
			nodes = tuple(
				frappe.db.sql(
					"select name from `tabCRM Sales Hierarchy` where lft >= %s and rgt <= %s order by lft asc",
					(node_row.lft, node_row.rgt),
					pluck=True,
				)
				or ()
			)
			users = tuple(
				sorted(
					set(
						frappe.db.sql(
							"""
							select distinct user from `tabCRM Sales Hierarchy`
							where lft >= %s and rgt <= %s and user is not null and user != ''
							""",
							(node_row.lft, node_row.rgt),
							pluck=True,
						)
						or []
					)
					| {actor}
				)
			)
			return ReportActor(
				user=actor,
				role_profile="team_leader",
				pii_allowed=True,
				root_node=node_row.name,
				allowed_nodes=nodes,
				allowed_users=users,
			)
		# Sales Manager without a hierarchy subtree falls back to strict self-only staff capability
		return ReportActor(
			user=actor,
			role_profile="staff",
			pii_allowed=True,
			root_node=None,
			allowed_nodes=(),
			allowed_users=(actor,),
		)

	if ROLE_BOD in roles:
		return ReportActor(
			user=actor,
			role_profile="bod",
			pii_allowed=False,
			root_node=None,
			allowed_nodes=None,
			allowed_users=None,
		)

	if _is_staff(actor):
		return ReportActor(
			user=actor,
			role_profile="staff",
			pii_allowed=True,
			root_node=None,
			allowed_nodes=(),
			allowed_users=(actor,),
		)

	raise ForbiddenScope("Bạn không có quyền truy cập hệ thống báo cáo CSKH P3.")


def get_policy_flags() -> dict:
	pol = _load_effective_global_policy_cfg()
	return {
		"p3_sla_enabled": bool(cint(pol.get("p3_sla_enabled", 1))),
		"p3_reporting_enabled": bool(cint(pol.get("p3_reporting_enabled", 1))),
		"qa_enabled": bool(cint(pol.get("qa_enabled", 0))),
	}


def is_p3_reporting_enabled() -> bool:
	return get_policy_flags()["p3_reporting_enabled"]


def _actor_scope_dict(actor: ReportActor, flags: dict) -> dict:
	return {
		"user": actor.user,
		"role_profile": actor.role_profile,
		"pii_allowed": actor.pii_allowed,
		"qa_enabled": flags["qa_enabled"],
		"p3_sla_enabled": flags["p3_sla_enabled"],
		"p3_reporting_enabled": flags["p3_reporting_enabled"],
	}


def _period_dict(period: PeriodSpec) -> dict:
	return {
		"period_type": period.period_type,
		"period_key": period.period_key,
		"from_date": period.from_date.isoformat(),
		"to_date": period.to_date.isoformat(),
		"timezone": DEFAULT_TIMEZONE,
	}


def _disabled_report_envelope(
	actor: ReportActor, period: PeriodSpec, flags: dict, page: int, page_size: int
) -> dict:
	return {
		"ok": True,
		"reporting_enabled": False,
		"status": "disabled_by_policy",
		"actor_scope": _actor_scope_dict(actor, flags),
		"period": _period_dict(period),
		"freshness": {
			"run_id": None,
			"run_mode": None,
			"snapshot_status": "disabled",
			"as_of": None,
			"source_watermark": None,
			"is_restated": False,
			"restated_from_run": None,
			"dirty_pending": False,
			"metric_version": METRIC_VERSION,
		},
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": {
			"historical_attribution_unknown": 0,
			"waived_obligations": 0,
			"canceled_validly_dnc": 0,
			"voided_attempts": 0,
		},
		"metrics": {},
		"trend_series": [],
		"rows": [],
		"pagination": {
			"page": page,
			"page_size": page_size,
			"total": 0,
			"total_pages": 0,
		},
	}


def _validate_campaign_filter(actor: ReportActor, campaign: str | None) -> str:
	camp = parse_optional_str(campaign, "Tham số campaign không hợp lệ.")
	if not camp:
		return ""
	camp_doc = frappe.db.get_value("CSKH Campaign", camp, ["name", "team_scope"], as_dict=True)
	if not camp_doc:
		if actor.role_profile == "crm_admin":
			raise InvalidRequest("Chiến dịch được lọc không tồn tại.")
		raise ForbiddenScope("Chiến dịch nằm ngoài phạm vi quyền của bạn.")
	if actor.role_profile == "team_leader" and not _campaign_in_leader_scope(actor.user, camp_doc):
		raise ForbiddenScope("Chiến dịch thuộc đội nhóm khác, nằm ngoài phạm vi quản lý của bạn.")
	return camp_doc.name


def _validate_team_filter(actor: ReportActor, team: str | None) -> str | None:
	t_val = parse_optional_str(team, "Tham số team không hợp lệ.")
	if not t_val:
		return None
	if actor.role_profile == "staff":
		raise ForbiddenScope("Nhân viên không được phép lọc báo cáo cấp đội nhóm.")
	if actor.role_profile == "team_leader":
		if t_val == UNATTRIBUTED_TEAM_ID or not actor.allowed_nodes or t_val not in actor.allowed_nodes:
			raise ForbiddenScope("Đội nhóm được chọn nằm ngoài phạm vi cây phân cấp của bạn.")
	return t_val


def _ensure_or_load_published_run(period: PeriodSpec, campaign_filter: str = "") -> dict | None:
	"""Fetch the latest published `CSKH KPI Run` for `(period, campaign_filter)`, auto-computing small windows if missing."""
	run_row = frappe.db.get_value(
		"CSKH KPI Run",
		{
			"period_type": period.period_type,
			"period_key": period.period_key,
			"campaign_filter": campaign_filter or "",
			"is_latest_published": 1,
		},
		[
			"name",
			"run_mode",
			"as_of",
			"source_watermark",
			"is_restated",
			"restated_from_run",
			"metric_version",
		],
		as_dict=True,
	)
	if run_row:
		return run_row

	# Bounded lightweight auto-materialization ONLY when raw rows in the window are small (`<= 2000`),
	# so freshly seeded dev/UI sites render immediately while 10k/100k sites never block web workers.
	att_cnt = int(
		frappe.db.sql(
			"select count(*) from `tabCSKH Contact Attempt` where occurred_at >= %s and occurred_at <= %s",
			(period.start_dt, period.end_dt),
		)[0][0]
		or 0
	)
	ob_cnt = (
		int(
			frappe.db.sql(
				"select count(*) from `tabCSKH SLA Obligation` where started_at <= %s and effective_due_at >= %s",
				(period.end_dt, period.start_dt),
			)[0][0]
			or 0
		)
		if frappe.db.table_exists("CSKH SLA Obligation")
		else 0
	)
	if (att_cnt + ob_cnt) <= AUTO_COMPUTE_MAX_RAW_ROWS:
		try:
			execute_kpi_recompute(
				period,
				run_mode="full",
				trigger_reason="initial_on_demand_small_period",
				campaign_filter=campaign_filter or "",
				actor="Administrator",
			)
		except Exception:
			# E.g. another worker already holds the recompute lock
			pass
		run_row = frappe.db.get_value(
			"CSKH KPI Run",
			{
				"period_type": period.period_type,
				"period_key": period.period_key,
				"campaign_filter": campaign_filter or "",
				"is_latest_published": 1,
			},
			[
				"name",
				"run_mode",
				"as_of",
				"source_watermark",
				"is_restated",
				"restated_from_run",
				"metric_version",
			],
			as_dict=True,
		)
		return run_row

	# Large uncomputed period: enqueue async recompute without blocking the HTTP reader
	if not getattr(frappe.flags, "in_test", False):
		frappe.enqueue(
			"crm_cskh.jobs.recompute_kpi.run_single_period_recompute",
			queue="long",
			period_type=period.period_type,
			period_key=period.period_key,
			from_date=period.from_date.isoformat(),
			to_date=period.to_date.isoformat(),
			campaign_filter=campaign_filter or None,
			actor="Administrator",
		)
	return None


def _build_freshness(period: PeriodSpec, run_row: dict | None, campaign_filter: str = "") -> dict:
	dirty = has_pending_dirty_scope_for_period(period, campaign_filter=campaign_filter)
	if not run_row:
		return {
			"run_id": None,
			"run_mode": None,
			"snapshot_status": "empty_not_computed",
			"as_of": None,
			"source_watermark": None,
			"is_restated": False,
			"restated_from_run": None,
			"dirty_pending": dirty,
			"metric_version": METRIC_VERSION,
		}
	return {
		"run_id": run_row.name,
		"run_mode": run_row.run_mode,
		"snapshot_status": "stale_recomputing" if dirty else "fresh",
		"as_of": format_calendar_iso(run_row.as_of) if run_row.as_of else None,
		"source_watermark": format_calendar_iso(run_row.source_watermark)
		if run_row.source_watermark
		else None,
		"is_restated": bool(cint(run_row.is_restated)),
		"restated_from_run": run_row.restated_from_run,
		"dirty_pending": dirty,
		"metric_version": run_row.metric_version or METRIC_VERSION,
	}


def _fetch_snapshots(
	period: PeriodSpec,
	scope_type: str,
	*,
	campaign_filter: str = "",
	scope_ids: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
	"""Indexed read on `tabCSKH KPI Snapshot` via `idx_cskh_kpisnap_lookup`."""
	if scope_ids is not None and len(scope_ids) == 0:
		return []
	params: list = [1, period.period_type, period.period_key, campaign_filter or "", scope_type]
	id_clause = ""
	if scope_ids is not None:
		ph = ", ".join(["%s"] * len(scope_ids))
		id_clause = f" and scope_id in ({ph})"
		params.extend(scope_ids)

	rows = frappe.db.sql(
		f"""
		select name, kpi_run, scope_type, scope_id, scope_label, parent_team_id, parent_team_label,
		       active_staff_count, process_score_status, process_score_sort,
		       distinct_work_items, ownership_intervals_count, work_items_touched,
		       contact_attempt_count, connected_attempt_count,
		       metrics_json, exclusion_counts_json, trend_series_json
		from `tabCSKH KPI Snapshot`
		where is_latest_published = %s
		  and period_type = %s
		  and period_key = %s
		  and campaign_filter = %s
		  and scope_type = %s{id_clause}
		""",
		tuple(params),
		as_dict=True,
	)
	for r in rows:
		metrics, comps, breakdown = unpack_snapshot_metrics(r.metrics_json)
		r["metrics"] = metrics
		r["score_components"] = comps
		r["attempt_breakdown"] = breakdown
		r["exclusion_counts"] = json.loads(r.exclusion_counts_json or "{}")
		r["trend_series"] = json.loads(r.trend_series_json or "[]")
	return rows


def _sort_rows_nulls_last(rows: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
	"""Sort snapshot-derived rows by `metrics[sort_by].value` with `None` ALWAYS last (`NULLS LAST`)."""
	metric_key = (sort_by or "PROCESS_SCORE").strip().upper()
	if metric_key not in ALL_SORTABLE_METRICS:
		raise InvalidMetric(f"Chỉ số sắp xếp '{metric_key}' không hợp lệ trong {METRIC_VERSION}.")
	direction = (sort_dir or "desc").strip().lower()
	if direction not in ("asc", "desc"):
		raise InvalidRequest("Tham số sort_dir phải là 'asc' hoặc 'desc'.")

	valid_rows = []
	null_rows = []
	for r in rows:
		val = (r.get("metrics") or {}).get(metric_key, {}).get("value")
		if val is None:
			null_rows.append(r)
		else:
			valid_rows.append((float(val), str(r.get("_tie_id") or ""), r))

	if direction == "desc":
		valid_rows.sort(key=lambda item: (-item[0], item[1]))
	else:
		valid_rows.sort(key=lambda item: (item[0], item[1]))

	null_rows.sort(key=lambda r: str(r.get("_tie_id") or ""))
	ordered = [item[2] for item in valid_rows] + null_rows
	for r in ordered:
		r.pop("_tie_id", None)
	return ordered


# ---------------------------------------------------------------------------
# 1. `get_executive_overview` (§3.1)
# ---------------------------------------------------------------------------


def get_executive_overview(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	*,
	actor: str | None = None,
) -> dict:
	act = resolve_report_actor(actor)
	if act.role_profile == "staff":
		raise ForbiddenScope("Nhân viên không có quyền xem bảng điều hành tổng quan (Executive Overview).")

	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return _disabled_report_envelope(act, period, flags, 1, 20)

	camp_filter = _validate_campaign_filter(act, campaign)
	team_filter = _validate_team_filter(act, team)

	# Team Leader without explicit `team` filter defaults strictly to their own root hierarchy node!
	target_scope_type = "company"
	target_scope_id = COMPANY_SCOPE_ID
	if act.role_profile == "team_leader":
		target_scope_type = "team"
		target_scope_id = team_filter or act.root_node or ""
	elif team_filter:
		target_scope_type = "team"
		target_scope_id = team_filter

	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = (
		_fetch_snapshots(period, target_scope_type, campaign_filter=camp_filter, scope_ids=[target_scope_id])
		if run_row
		else []
	)
	snap = snaps[0] if snaps else None

	metrics = dict(snap["metrics"]) if snap else {}
	if snap and "CONTACT_ATTEMPT_COUNT" in metrics:
		metrics["CONTACT_ATTEMPT_COUNT"] = {
			**metrics["CONTACT_ATTEMPT_COUNT"],
			"attempt_breakdown": snap["attempt_breakdown"],
		}

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"scope": {
			"scope_type": target_scope_type,
			"scope_id": target_scope_id,
			"campaign_filter": camp_filter or None,
			"distinct_work_items": int(snap.distinct_work_items) if snap else 0,
			"ownership_intervals_count": int(snap.ownership_intervals_count) if snap else 0,
		},
		"freshness": _build_freshness(period, run_row),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": snap["exclusion_counts"]
		if snap
		else {
			"historical_attribution_unknown": 0,
			"waived_obligations": 0,
			"canceled_validly_dnc": 0,
			"voided_attempts": 0,
		},
		"attempt_breakdown": snap["attempt_breakdown"]
		if snap
		else {
			"attempted": 0,
			"connected": 0,
			"self_reported": 0,
			"verified": 0,
			"disputed": 0,
		},
		"score_components": snap["score_components"] if snap else [],
		"metrics": metrics,
		"trend_series": snap["trend_series"] if snap else [],
		"rows": [],
		"pagination": {"page": 1, "page_size": 1, "total": 1 if snap else 0, "total_pages": 1 if snap else 0},
	}


# ---------------------------------------------------------------------------
# 2. `get_team_comparison` (§3.2)
# ---------------------------------------------------------------------------


def get_team_comparison(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	sort_by: str = "PROCESS_SCORE",
	sort_dir: str = "desc",
	page: int | str = 1,
	page_size: int | str = 20,
	*,
	actor: str | None = None,
) -> dict:
	act = resolve_report_actor(actor)
	if act.role_profile == "staff":
		raise ForbiddenScope("Nhân viên không có quyền xem báo cáo so sánh giữa các đội nhóm.")

	page_num, limit, offset = parse_pagination(page, page_size, default_page_size=20, max_page_size=100)
	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return _disabled_report_envelope(act, period, flags, page_num, limit)

	camp_filter = _validate_campaign_filter(act, campaign)
	team_filter = _validate_team_filter(act, team)

	scope_ids: list[str] | None = None
	if team_filter:
		scope_ids = [team_filter]
	elif act.role_profile == "team_leader":
		scope_ids = list(act.allowed_nodes or ())

	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = (
		_fetch_snapshots(period, "team", campaign_filter=camp_filter, scope_ids=scope_ids) if run_row else []
	)

	comp_snaps = (
		_fetch_snapshots(period, "company", campaign_filter=camp_filter, scope_ids=[COMPANY_SCOPE_ID])
		if (run_row and act.role_profile in ("crm_admin", "bod"))
		else []
	)
	excl = (
		comp_snaps[0]["exclusion_counts"]
		if comp_snaps
		else (
			snaps[0]["exclusion_counts"]
			if snaps
			else {
				"historical_attribution_unknown": 0,
				"waived_obligations": 0,
				"canceled_validly_dnc": 0,
				"voided_attempts": 0,
			}
		)
	)

	formatted = []
	for s in snaps:
		if act.role_profile == "team_leader" and s.scope_id == UNATTRIBUTED_TEAM_ID:
			continue
		formatted.append(
			{
				"_tie_id": s.scope_id,
				"team_id": s.scope_id,
				"team_name": s.scope_label or s.scope_id,
				"is_unattributed_bucket": s.scope_id == UNATTRIBUTED_TEAM_ID,
				"active_staff_count": int(s.active_staff_count or 0),
				"distinct_work_items": int(s.distinct_work_items or 0),
				"ownership_intervals_count": int(s.ownership_intervals_count or 0),
				"contact_attempt_count": int(s.contact_attempt_count or 0),
				"attempt_breakdown": s["attempt_breakdown"],
				"metrics": s["metrics"],
			}
		)

	ordered = _sort_rows_nulls_last(formatted, sort_by=sort_by, sort_dir=sort_dir)
	total = len(ordered)
	paged = ordered[offset : offset + limit]

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"freshness": _build_freshness(period, run_row),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": excl,
		"metrics": comp_snaps[0]["metrics"] if comp_snaps else {},
		"rows": paged,
		"pagination": {
			"page": page_num,
			"page_size": limit,
			"total": total,
			"total_pages": math.ceil(total / limit) if total > 0 else 0,
		},
	}


# ---------------------------------------------------------------------------
# 3. `get_employee_quality` (§3.3)
# ---------------------------------------------------------------------------


def get_employee_quality(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	team: str | None = None,
	employee: str | None = None,
	campaign: str | None = None,
	sort_by: str = "PROCESS_SCORE",
	sort_dir: str = "desc",
	page: int | str = 1,
	page_size: int | str = 20,
	*,
	actor: str | None = None,
) -> dict:
	act = resolve_report_actor(actor)
	page_num, limit, offset = parse_pagination(page, page_size, default_page_size=20, max_page_size=100)
	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return _disabled_report_envelope(act, period, flags, page_num, limit)

	emp_param = parse_optional_str(employee, "Tham số employee không hợp lệ.")
	if act.role_profile == "staff":
		if team:
			raise ForbiddenScope("Nhân viên không được phép lọc bảng chất lượng theo đội nhóm.")
		if emp_param and emp_param != act.user:
			raise ForbiddenScope("Nhân viên chỉ được xem chỉ số chất lượng của chính mình.")
		emp_param = act.user

	camp_filter = _validate_campaign_filter(act, campaign)
	team_filter = _validate_team_filter(act, team)

	if act.role_profile == "team_leader" and emp_param:
		if not act.allowed_users or emp_param not in act.allowed_users:
			raise ForbiddenScope("Nhân viên được chọn nằm ngoài phạm vi quản lý hiện tại của bạn.")

	emp_scope_ids: list[str] | None = None
	if emp_param:
		emp_scope_ids = [emp_param]
	elif act.role_profile == "team_leader":
		emp_scope_ids = list(act.allowed_users or ())

	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = (
		_fetch_snapshots(period, "employee", campaign_filter=camp_filter, scope_ids=emp_scope_ids)
		if run_row
		else []
	)

	formatted = []
	for s in snaps:
		if team_filter and (s.parent_team_id or "") != team_filter:
			# Also allow if user currently belongs to a child node under `team_filter`
			continue
		formatted.append(
			{
				"_tie_id": s.scope_id,
				"employee_id": s.scope_id,
				"employee_name": s.scope_label or s.scope_id,
				"team_id": s.parent_team_id or None,
				"team_name": s.parent_team_label or None,
				"intervals_count": int(s.ownership_intervals_count or 0),
				"work_items_touched": int(s.work_items_touched or 0),
				"contact_attempt_count": int(s.contact_attempt_count or 0),
				"connected_attempt_count": int(s.connected_attempt_count or 0),
				"attempt_breakdown": s["attempt_breakdown"],
				"metrics": s["metrics"],
			}
		)

	ordered = _sort_rows_nulls_last(formatted, sort_by=sort_by, sort_dir=sort_dir)
	total = len(ordered)
	paged = ordered[offset : offset + limit]

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"freshness": _build_freshness(period, run_row),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": {
			"historical_attribution_unknown": 0,
			"waived_obligations": sum(int(s["exclusion_counts"].get("waived_obligations", 0)) for s in snaps),
			"canceled_validly_dnc": sum(
				int(s["exclusion_counts"].get("canceled_validly_dnc", 0)) for s in snaps
			),
			"voided_attempts": sum(int(s["exclusion_counts"].get("voided_attempts", 0)) for s in snaps),
		},
		"metrics": {},
		"rows": paged,
		"pagination": {
			"page": page_num,
			"page_size": limit,
			"total": total,
			"total_pages": math.ceil(total / limit) if total > 0 else 0,
		},
	}


# ---------------------------------------------------------------------------
# 4. `get_campaign_quality` (§3.4)
# ---------------------------------------------------------------------------


def get_campaign_quality(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	sort_by: str = "PROCESS_SCORE",
	sort_dir: str = "desc",
	page: int | str = 1,
	page_size: int | str = 20,
	*,
	actor: str | None = None,
) -> dict:
	act = resolve_report_actor(actor)
	if act.role_profile == "staff":
		raise ForbiddenScope("Nhân viên không có quyền xem báo cáo chất lượng cấp chiến dịch.")

	page_num, limit, offset = parse_pagination(page, page_size, default_page_size=20, max_page_size=100)
	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return _disabled_report_envelope(act, period, flags, page_num, limit)

	camp_filter = _validate_campaign_filter(act, campaign)
	team_filter = _validate_team_filter(act, team)

	run_row = _ensure_or_load_published_run(period, campaign_filter="")
	formatted = []

	if act.role_profile in ("crm_admin", "bod") and not team_filter:
		scope_ids = [camp_filter] if camp_filter else None
		snaps = (
			_fetch_snapshots(period, "campaign", campaign_filter="", scope_ids=scope_ids) if run_row else []
		)
		for s in snaps:
			formatted.append(
				{
					"_tie_id": s.scope_id,
					"campaign_id": s.scope_id,
					"campaign_name": s.scope_label or s.scope_id,
					"distinct_work_items": int(s.distinct_work_items or 0),
					"ownership_intervals_count": int(s.ownership_intervals_count or 0),
					"work_items_touched": int(s.work_items_touched or 0),
					"contact_attempt_count": int(s.contact_attempt_count or 0),
					"attempt_breakdown": s["attempt_breakdown"],
					"metrics": s["metrics"],
				}
			)
	else:
		# Scoped campaign projection for Team Leader (or explicit `team` filter):
		# never exposes metrics from other teams inside a shared campaign!
		allowed_teams = {team_filter} if team_filter else set(act.allowed_nodes or ())
		as_of_dt = get_datetime(run_row.as_of) if (run_row and run_row.as_of) else None
		ds = load_period_raw_dataset(period, as_of=as_of_dt, campaign_filter=camp_filter or "")
		camp_docs = frappe.get_all(
			"CSKH Campaign",
			filters={"name": camp_filter} if camp_filter else {},
			fields=["name", "title", "team_scope"],
		)
		for cdoc in camp_docs:
			if act.role_profile == "team_leader" and not _campaign_in_leader_scope(act.user, cdoc):
				continue
			cid = cdoc["name"]
			c_atts = [
				a
				for a in ds["attempts"]
				if a.get("campaign") == cid
				and a.get("resolved_attribution") == AUTHORITATIVE
				and a.get("resolved_team") in allowed_teams
			]
			c_obs = [
				ob
				for ob in ds["obligations"]
				if ob.get("campaign") == cid
				and ob.get("resolved_attribution") == AUTHORITATIVE
				and ob.get("resolved_team") in allowed_teams
			]
			c_ivs = [
				iv
				for iv in ds["intervals"]
				if iv.get("campaign") == cid
				and iv.get("resolved_attribution") == AUTHORITATIVE
				and iv.get("resolved_team") in allowed_teams
			]
			c_qa = [
				qr
				for qr in ds["qa_reviews"]
				if qr.get("campaign") == cid and (qr.get("team_at_review") or "") in allowed_teams
			]
			bucket = _aggregate_bucket(
				scope_type="campaign",
				scope_id=cid,
				scope_label=cdoc.get("title") or cid,
				attempts=c_atts,
				obligations=c_obs,
				intervals=c_ivs,
				qa_reviews=c_qa,
				dnc_since_by_lead=ds["dnc_since_by_lead"],
				policy_cfg=ds["policy_cfg"],
				qa_enabled=ds["qa_enabled"],
				min_attempts_enabled=ds["min_attempts_enabled"],
				unknown_team_exclusions=0,
			)
			formatted.append(
				{
					"_tie_id": cid,
					"campaign_id": cid,
					"campaign_name": cdoc.get("title") or cid,
					"distinct_work_items": bucket["distinct_work_items"],
					"ownership_intervals_count": bucket["ownership_intervals_count"],
					"work_items_touched": bucket["work_items_touched"],
					"contact_attempt_count": bucket["contact_attempt_count"],
					"attempt_breakdown": bucket["attempt_breakdown"],
					"metrics": bucket["metrics"],
				}
			)

	ordered = _sort_rows_nulls_last(formatted, sort_by=sort_by, sort_dir=sort_dir)
	total = len(ordered)
	paged = ordered[offset : offset + limit]

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"freshness": _build_freshness(period, run_row),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": {
			"historical_attribution_unknown": 0,
			"waived_obligations": 0,
			"canceled_validly_dnc": 0,
			"voided_attempts": 0,
		},
		"metrics": {},
		"rows": paged,
		"pagination": {
			"page": page_num,
			"page_size": limit,
			"total": total,
			"total_pages": math.ceil(total / limit) if total > 0 else 0,
		},
	}


# ---------------------------------------------------------------------------
# 5. `get_my_scorecard` (§3.5)
# ---------------------------------------------------------------------------


def get_my_scorecard(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	*,
	actor: str | None = None,
) -> dict:
	act = resolve_report_actor(actor)
	if act.role_profile == "bod":
		raise ForbiddenScope(
			"BOD Viewer là vai trò giám sát tổng hợp, không có bảng điểm tác nghiệp cá nhân."
		)

	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return _disabled_report_envelope(act, period, flags, 1, 20)

	camp_filter = _validate_campaign_filter(act, campaign)
	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = (
		_fetch_snapshots(period, "employee", campaign_filter=camp_filter, scope_ids=[act.user])
		if run_row
		else []
	)
	snap = snaps[0] if snaps else None

	# Active SLA obligations STRICTLY on Work Items currently owned by `act.user` (`0` transferred Lead leakage!)
	active_obligations: list[dict] = []
	if frappe.db.table_exists("CSKH SLA Obligation") and flags["p3_sla_enabled"]:
		active_states_ph = ", ".join(["%s"] * len(OWNED_ACTIVE_WORK_ITEM_STATES))
		obs = frappe.db.sql(
			f"""
			select ob.name, ob.obligation_type, ob.work_item, ob.campaign, ob.status,
			       ob.started_at, ob.original_due_at, ob.effective_due_at, ob.is_paused, ob.informational_only
			from `tabCSKH SLA Obligation` ob
			inner join `tabCSKH Work Item` wi on wi.name = ob.work_item
			where ob.owner_user = %s
			  and ob.status in ('open', 'breached_open')
			  and wi.assigned_to = %s
			  and wi.status in ({active_states_ph})
			order by ob.effective_due_at asc
			limit 25
			""",
			(act.user, act.user, *OWNED_ACTIVE_WORK_ITEM_STATES),
			as_dict=True,
		)
		for ob in obs:
			active_obligations.append(
				{
					"obligation_id": ob.name,
					"obligation_type": ob.obligation_type,
					"work_item": ob.work_item,
					"campaign": ob.campaign,
					"status": ob.status,
					"started_at": format_calendar_iso(ob.started_at) if ob.started_at else None,
					"original_due_at": format_calendar_iso(ob.original_due_at)
					if ob.original_due_at
					else None,
					"effective_due_at": format_calendar_iso(ob.effective_due_at)
					if ob.effective_due_at
					else None,
					"is_paused": bool(cint(ob.is_paused)),
					"informational_only": bool(cint(ob.informational_only)),
				}
			)

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"employee": {
			"employee_id": act.user,
			"employee_name": snap.scope_label if snap else act.user,
			"team_id": snap.parent_team_id if snap else None,
			"team_name": snap.parent_team_label if snap else None,
			"intervals_count": int(snap.ownership_intervals_count) if snap else 0,
			"work_items_touched": int(snap.work_items_touched) if snap else 0,
		},
		"freshness": _build_freshness(period, run_row),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": snap["exclusion_counts"]
		if snap
		else {
			"historical_attribution_unknown": 0,
			"waived_obligations": 0,
			"canceled_validly_dnc": 0,
			"voided_attempts": 0,
		},
		"attempt_breakdown": snap["attempt_breakdown"]
		if snap
		else {
			"attempted": 0,
			"connected": 0,
			"self_reported": 0,
			"verified": 0,
			"disputed": 0,
		},
		"score_components": snap["score_components"] if snap else [],
		"metrics": snap["metrics"] if snap else {},
		"active_obligations": active_obligations,
		"rows": active_obligations,
		"pagination": {
			"page": 1,
			"page_size": 25,
			"total": len(active_obligations),
			"total_pages": 1 if active_obligations else 0,
		},
	}


# ---------------------------------------------------------------------------
# 6. `get_metric_drilldown` (§3.6 — Reconciled Numerator/Denominator + Privacy Projection)
# ---------------------------------------------------------------------------


def _load_lead_display_map(lead_ids: list[str], actor: ReportActor) -> dict[str, dict]:
	"""Batch-load `CRM Lead` display attributes for the current drill-down page (`<= 100` rows)."""
	if not lead_ids or actor.role_profile == "bod" or not actor.pii_allowed:
		return {}
	uniq = sorted({lid for lid in lead_ids if lid})
	if not uniq:
		return {}
	ph = ", ".join(["%s"] * len(uniq))
	rows = frappe.db.sql(
		f"""
		select name, lead_name, first_name, last_name, mobile_no, phone, lead_owner
		from `tabCRM Lead`
		where name in ({ph})
		""",
		tuple(uniq),
		as_dict=True,
	)
	out: dict[str, dict] = {}
	for r in rows:
		cname = r.lead_name or " ".join(p for p in (r.first_name, r.last_name) if p).strip() or r.name
		out[r.name] = {
			"lead": r.name,
			"customer_name": cname,
			"phone": r.mobile_no or r.phone or None,
			"lead_owner": r.lead_owner,
		}
	return out


def _can_actor_read_drilldown_row_pii(
	raw_row: dict,
	lead_info: dict | None,
	actor: ReportActor,
	wi_readable_cache: dict[str, bool],
) -> bool:
	"""Evaluate live workflow/lead PII readability (`SEC2-01..04`, `L-23`, `S03`, `S15`), including closed Work Items."""
	if actor.role_profile == "crm_admin":
		return True
	if actor.role_profile == "bod" or not actor.pii_allowed:
		return False
	wi_name = raw_row.get("work_item")
	if wi_name:
		if wi_name not in wi_readable_cache:
			wi_readable_cache[wi_name] = bool(is_work_item_readable_for_workflow(wi_name, user=actor.user))
		return wi_readable_cache[wi_name]
	if not lead_info:
		return False
	owner = lead_info.get("lead_owner")
	if actor.role_profile == "team_leader":
		return bool(owner and actor.allowed_users and owner in actor.allowed_users)
	if actor.role_profile == "staff":
		return bool(owner == actor.user)
	return False


def _project_drilldown_pii(
	raw_row: dict,
	actor: ReportActor,
	lead_display_map: dict[str, dict],
	wi_readable_cache: dict[str, bool],
	*,
	emit_pii: bool = True,
) -> dict:
	"""Apply strict `S03` / `S08` / `S09` / `S15` privacy redaction onto a drill-down event row."""
	out = dict(raw_row)
	lead_id = out.pop("_lead_id", None)
	raw_note = out.pop("_note_excerpt", None)

	if actor.role_profile == "bod" or not emit_pii:
		out["lead"] = "REDACTED"
		out["customer_name"] = None
		out["phone"] = None
		out["note_excerpt"] = None
		out["pii_redacted"] = True
		out["pii_redacted_due_to_transfer"] = False
		return out

	info = lead_display_map.get(lead_id or "")
	if not info or not _can_actor_read_drilldown_row_pii(raw_row, info, actor, wi_readable_cache):
		out["lead"] = None
		out["customer_name"] = None
		out["phone"] = None
		out["note_excerpt"] = None
		out["pii_redacted"] = True
		out["pii_redacted_due_to_transfer"] = True
		return out

	out["lead"] = info["lead"]
	out["customer_name"] = info["customer_name"]
	out["phone"] = info["phone"]
	out["note_excerpt"] = redact_pii_text(str(raw_note)[:160]) if raw_note else None
	out["pii_redacted"] = False
	out["pii_redacted_due_to_transfer"] = False
	return out


def get_metric_drilldown(
	metric_code: str,
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	scope_type: str | None = None,
	scope_id: str | None = None,
	campaign: str | None = None,
	filter_bucket: str = "all_denominator",
	include_pii: int | str | bool | None = None,
	page: int | str = 1,
	page_size: int | str = 20,
	*,
	actor: str | None = None,
) -> dict:
	act = resolve_report_actor(actor)
	wants_pii = parse_bool(include_pii, default=False) if include_pii is not None else False
	if act.role_profile == "bod" and wants_pii:
		raise ForbiddenScope(
			"BOD Viewer không được phép yêu cầu dữ liệu định danh khách hàng (PII) trên drill-down."
		)

	mcode = (parse_optional_str(metric_code, "Tham số metric_code không hợp lệ.") or "").upper()
	# Allow aliases `CONNECTED_ATTEMPT_COUNT` -> `CONNECTED_RATE` numerator & `RESCHEDULE_RATE`
	supported = DRILLDOWN_SUPPORTED_METRICS | {"CONNECTED_ATTEMPT_COUNT", "RESCHEDULE_RATE"}
	if not mcode or mcode not in supported:
		raise InvalidMetric(
			f"Mã chỉ số '{mcode}' không tồn tại hoặc không hỗ trợ drill-down trong {METRIC_VERSION}."
		)

	bucket = (
		parse_optional_str(filter_bucket, "Tham số filter_bucket không hợp lệ.") or "all_denominator"
	).lower()
	if bucket not in VALID_DRILLDOWN_BUCKETS:
		raise InvalidRequest(
			"Tham số filter_bucket phải thuộc ('all_denominator', 'numerator_only', 'breached_only', 'excluded')."
		)

	page_num, limit, offset = parse_pagination(page, page_size, default_page_size=20, max_page_size=100)
	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return _disabled_report_envelope(act, period, flags, page_num, limit)

	camp_filter = _validate_campaign_filter(act, campaign)

	stype = (parse_optional_str(scope_type, "Tham số scope_type không hợp lệ.") or "").lower()
	sid = parse_optional_str(scope_id, "Tham số scope_id không hợp lệ.") or ""

	if not stype:
		if act.role_profile == "staff":
			stype, sid = "employee", act.user
		elif act.role_profile == "team_leader":
			stype, sid = "team", act.root_node or ""
		else:
			stype, sid = "company", COMPANY_SCOPE_ID

	if stype not in ("company", "team", "employee", "campaign"):
		raise InvalidRequest("Tham số scope_type phải thuộc ('company', 'team', 'employee', 'campaign').")

	# Enforce strict actor scope on `(stype, sid)`
	if act.role_profile == "staff":
		if stype != "employee" or (sid and sid != act.user):
			raise ForbiddenScope("Nhân viên chỉ được xem chi tiết drill-down của chính mình.")
		sid = act.user
	elif act.role_profile == "team_leader":
		if stype == "company":
			raise ForbiddenScope("Team Leader không được phép drill-down toàn công ty.")
		if stype == "team":
			sid = sid or act.root_node or ""
			if sid == UNATTRIBUTED_TEAM_ID or not act.allowed_nodes or sid not in act.allowed_nodes:
				raise ForbiddenScope("Đội nhóm drill-down nằm ngoài phạm vi cây phân cấp của bạn.")
		elif stype == "employee":
			if not sid or not act.allowed_users or sid not in act.allowed_users:
				raise ForbiddenScope("Nhân viên drill-down nằm ngoài phạm vi quản lý của bạn.")
		elif stype == "campaign":
			if not sid:
				raise InvalidRequest("Bắt buộc truyền scope_id khi scope_type='campaign'.")
			_validate_campaign_filter(act, sid)
	else:
		if stype == "company":
			sid = COMPANY_SCOPE_ID
		elif not sid:
			raise InvalidRequest(f"Bắt buộc truyền scope_id khi scope_type='{stype}'.")

	# Anchor `as_of` to the published snapshot run (when available) so drill-down counts match 100%!
	eff_camp_filter = "" if stype == "campaign" else camp_filter
	run_row = _ensure_or_load_published_run(period, campaign_filter=eff_camp_filter)
	as_of_dt = get_datetime(run_row.as_of) if (run_row and run_row.as_of) else None

	ds = load_period_raw_dataset(
		period,
		as_of=as_of_dt,
		campaign_filter=(sid if stype == "campaign" else (camp_filter or "")),
	)

	# Slice dataset to `(stype, sid)` using the exact rules of `compute_period_snapshots`
	attempts = ds["attempts"]
	obligations = ds["obligations"]
	intervals = ds["intervals"]

	if stype == "team":
		if sid == UNATTRIBUTED_TEAM_ID:
			attempts = [
				a for a in attempts if not (a["resolved_team"] and a["resolved_attribution"] == AUTHORITATIVE)
			]
			obligations = [
				ob
				for ob in obligations
				if not (ob["resolved_team"] and ob["resolved_attribution"] == AUTHORITATIVE)
			]
			intervals = [
				iv
				for iv in intervals
				if not (iv["resolved_team"] and iv["resolved_attribution"] == AUTHORITATIVE)
			]
		else:
			attempts = [
				a
				for a in attempts
				if a["resolved_team"] == sid and a["resolved_attribution"] == AUTHORITATIVE
			]
			obligations = [
				ob
				for ob in obligations
				if ob["resolved_team"] == sid and ob["resolved_attribution"] == AUTHORITATIVE
			]
			intervals = [
				iv
				for iv in intervals
				if iv["resolved_team"] == sid and iv["resolved_attribution"] == AUTHORITATIVE
			]
	elif stype == "employee":
		attempts = [a for a in attempts if a.get("actor_user") == sid]
		obligations = [ob for ob in obligations if ob.get("owner_user") == sid]
		intervals = [iv for iv in intervals if iv.get("to_user") == sid]
	elif stype == "campaign":
		if act.role_profile == "team_leader":
			allowed_t = set(act.allowed_nodes or ())
			attempts = [
				a
				for a in attempts
				if a.get("campaign") == sid
				and a.get("resolved_attribution") == AUTHORITATIVE
				and a.get("resolved_team") in allowed_t
			]
			obligations = [
				ob
				for ob in obligations
				if ob.get("campaign") == sid
				and ob.get("resolved_attribution") == AUTHORITATIVE
				and ob.get("resolved_team") in allowed_t
			]
			intervals = [
				iv
				for iv in intervals
				if iv.get("campaign") == sid
				and iv.get("resolved_attribution") == AUTHORITATIVE
				and iv.get("resolved_team") in allowed_t
			]
		else:
			attempts = [a for a in attempts if a.get("campaign") == sid]
			obligations = [ob for ob in obligations if ob.get("campaign") == sid]
			intervals = [iv for iv in intervals if iv.get("campaign") == sid]

	events = _extract_drilldown_events(
		mcode=mcode,
		bucket=bucket,
		attempts=attempts,
		obligations=obligations,
		intervals=intervals,
		dnc_since_by_lead=ds["dnc_since_by_lead"],
	)

	total = len(events)
	sliced = events[offset : offset + limit]
	emit_pii = (
		act.role_profile != "bod" and act.pii_allowed and (wants_pii if include_pii is not None else True)
	)
	page_lead_ids = [r.get("_lead_id") for r in sliced if r.get("_lead_id")] if emit_pii else []
	lead_display_map = _load_lead_display_map(page_lead_ids, act) if emit_pii else {}
	wi_readable_cache: dict[str, bool] = {}
	projected_rows = [
		_project_drilldown_pii(r, act, lead_display_map, wi_readable_cache, emit_pii=emit_pii) for r in sliced
	]

	# Also fetch the matching snapshot cell for reconciliation metadata
	snap_cell = None
	if run_row and not (stype == "campaign" and act.role_profile == "team_leader"):
		snaps = _fetch_snapshots(period, stype, campaign_filter=eff_camp_filter, scope_ids=[sid])
		if snaps:
			snap_cell = snaps[0]["metrics"].get(mcode)

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"drilldown_context": {
			"metric_code": mcode,
			"label_vi": METRIC_LABELS_VI.get(mcode, mcode),
			"scope_type": stype,
			"scope_id": sid,
			"filter_bucket": bucket,
			"snapshot_metric": snap_cell,
		},
		"freshness": _build_freshness(period, run_row),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": {
			"historical_attribution_unknown": 0,
			"waived_obligations": 0,
			"canceled_validly_dnc": 0,
			"voided_attempts": 0,
		},
		"metrics": {mcode: snap_cell} if snap_cell else {},
		"rows": projected_rows,
		"pagination": {
			"page": page_num,
			"page_size": limit,
			"total": total,
			"total_pages": math.ceil(total / limit) if total > 0 else 0,
		},
	}


def _ob_event_dict(ob: dict, *, in_numerator: bool) -> dict:
	return {
		"record_type": "sla_obligation",
		"record_id": ob.name,
		"obligation_id": ob.name,
		"obligation_type": ob.obligation_type,
		"work_item": ob.work_item,
		"campaign": ob.campaign,
		"owner_user": ob.owner_user,
		"team_id": ob.get("resolved_team"),
		"status": ob["as_of_status"],
		"in_numerator": in_numerator,
		"started_at": format_calendar_iso(ob.started_at) if ob.started_at else None,
		"original_due_at": format_calendar_iso(ob.original_due_at) if ob.original_due_at else None,
		"effective_due_at": format_calendar_iso(ob.effective_due_at) if ob.effective_due_at else None,
		"fulfilled_at": format_calendar_iso(ob.fulfilled_at) if ob.fulfilled_at else None,
		"_lead_id": ob.lead,
		"_note_excerpt": None,
	}


def _att_event_dict(a: dict, *, in_numerator: bool) -> dict:
	eff = a["effective"]
	return {
		"record_type": "contact_attempt",
		"record_id": a["attempt_id"],
		"attempt_id": a["attempt_id"],
		"work_item": a["work_item"],
		"campaign": a.get("campaign"),
		"owner_user": a.get("actor_user"),
		"team_id": a.get("resolved_team"),
		"channel": eff.get("channel"),
		"outcome_code": eff.get("outcome_code"),
		"is_connected": bool(eff.get("is_connected")),
		"is_voided": bool(a["is_voided"]),
		"has_approved_correction": bool(a["has_approved_correction"]),
		"status": "voided" if a["is_voided"] else ("connected" if eff.get("is_connected") else "attempted"),
		"in_numerator": in_numerator,
		"occurred_at": format_calendar_iso(eff.get("occurred_at")) if eff.get("occurred_at") else None,
		"_lead_id": a.get("lead"),
		"_note_excerpt": eff.get("note"),
	}


def _extract_drilldown_events(
	*,
	mcode: str,
	bucket: str,
	attempts: list[dict],
	obligations: list[dict],
	intervals: list[dict],
	dnc_since_by_lead: dict,
) -> list[dict]:
	"""Return exact numerator/denominator/breached/excluded items matching `kpi_calculator._aggregate_bucket`."""
	if mcode in (
		"FIRST_ATTEMPT_SLA_RATE",
		"FOLLOWUP_SLA_RATE",
		"CALLBACK_COMPLETION_RATE",
		"FIRST_CONNECTED_TRACKING_RATE",
		"MIN_ATTEMPTS_COMPLIANCE_RATE",
	):
		ob_type_map = {
			"FIRST_ATTEMPT_SLA_RATE": "FIRST_ATTEMPT",
			"FOLLOWUP_SLA_RATE": "FOLLOWUP",
			"CALLBACK_COMPLETION_RATE": "FOLLOWUP",
			"FIRST_CONNECTED_TRACKING_RATE": "FIRST_CONNECTED",
			"MIN_ATTEMPTS_COMPLIANCE_RATE": "MIN_ATTEMPTS",
		}
		target_type = ob_type_map[mcode]
		elig_statuses = (
			frozenset(("fulfilled_on_time", "fulfilled_late", "breached_open"))
			if mcode == "FIRST_CONNECTED_TRACKING_RATE"
			else ELIGIBLE_SLA_STATUSES
		)
		num_statuses = (
			frozenset(("fulfilled_on_time", "fulfilled_late"))
			if mcode == "CALLBACK_COMPLETION_RATE"
			else frozenset(("fulfilled_on_time",))
		)

		anchor_obs = [
			ob for ob in obligations if ob.obligation_type == target_type and ob["in_anchor_window"]
		]
		if bucket == "excluded":
			excl = [ob for ob in anchor_obs if ob["as_of_status"] not in elig_statuses]
			return [_ob_event_dict(ob, in_numerator=False) for ob in excl]

		elig = [ob for ob in anchor_obs if ob["as_of_status"] in elig_statuses]
		if bucket == "numerator_only":
			return [
				_ob_event_dict(ob, in_numerator=True) for ob in elig if ob["as_of_status"] in num_statuses
			]
		if bucket == "breached_only":
			return [
				_ob_event_dict(ob, in_numerator=False)
				for ob in elig
				if ob["as_of_status"] not in num_statuses
			]
		return [_ob_event_dict(ob, in_numerator=(ob["as_of_status"] in num_statuses)) for ob in elig]

	if mcode == "WORK_ITEM_FIRST_ATTEMPT_SLA_RATE":
		fa_elig = [
			ob
			for ob in obligations
			if ob.obligation_type == "FIRST_ATTEMPT"
			and ob["in_anchor_window"]
			and ob["as_of_status"] in ELIGIBLE_SLA_STATUSES
		]
		by_wi: dict[str, list[dict]] = {}
		for ob in fa_elig:
			by_wi.setdefault(ob.work_item, []).append(ob)
		rows = []
		for wi, ob_list in sorted(by_wi.items()):
			st_set = {o["as_of_status"] for o in ob_list}
			is_ok = "fulfilled_on_time" in st_set and not (
				st_set & {"fulfilled_late", "breached_open", "breached_handoff"}
			)
			if bucket == "numerator_only" and not is_ok:
				continue
			if bucket == "breached_only" and is_ok:
				continue
			if bucket == "excluded":
				continue
			rep = ob_list[-1]
			item = _ob_event_dict(rep, in_numerator=is_ok)
			item["record_type"] = "work_item_sla"
			item["record_id"] = wi
			item["intervals_in_period"] = len(ob_list)
			rows.append(item)
		return rows

	if mcode in ("CONTACT_ATTEMPT_COUNT", "CONNECTED_ATTEMPT_COUNT", "CONNECTED_RATE"):
		eff_valid = [
			a
			for a in attempts
			if a["in_effective_window"] and not a["is_voided"] and bool(a["effective"]["is_contact_attempt"])
		]
		if bucket == "excluded":
			voids = [a for a in attempts if a["in_raw_window"] and a["is_voided"]]
			return [_att_event_dict(a, in_numerator=False) for a in voids]
		if mcode == "CONTACT_ATTEMPT_COUNT":
			if bucket == "breached_only":
				return []
			return [_att_event_dict(a, in_numerator=True) for a in eff_valid]
		# `CONNECTED_RATE` / `CONNECTED_ATTEMPT_COUNT`
		if bucket in ("numerator_only",) or mcode == "CONNECTED_ATTEMPT_COUNT":
			if bucket == "breached_only":
				return [
					_att_event_dict(a, in_numerator=False)
					for a in eff_valid
					if not a["effective"]["is_connected"]
				]
			if mcode == "CONNECTED_ATTEMPT_COUNT" and bucket == "all_denominator":
				return [
					_att_event_dict(a, in_numerator=True) for a in eff_valid if a["effective"]["is_connected"]
				]
			return [
				_att_event_dict(a, in_numerator=True) for a in eff_valid if a["effective"]["is_connected"]
			]
		if bucket == "breached_only":
			return [
				_att_event_dict(a, in_numerator=False)
				for a in eff_valid
				if not a["effective"]["is_connected"]
			]
		return [_att_event_dict(a, in_numerator=bool(a["effective"]["is_connected"])) for a in eff_valid]

	if mcode in ("VALID_OUTCOME_LOGGING_RATE", "CORRECTION_RATE", "VOID_ATTEMPT_RATE"):
		raw_win = [a for a in attempts if a["in_raw_window"]]
		if bucket == "excluded":
			return []
		rows = []
		for a in raw_win:
			if mcode == "VALID_OUTCOME_LOGGING_RATE":
				in_num = (not a["has_approved_correction"]) and bool(a["note_Policy_ok"])
				is_bad = not in_num
			elif mcode == "CORRECTION_RATE":
				in_num = bool(a["has_approved_correction"])
				is_bad = in_num
			else:
				in_num = bool(a["is_voided"])
				is_bad = in_num
			if bucket == "numerator_only" and not in_num:
				continue
			if bucket == "breached_only" and not is_bad:
				continue
			rows.append(_att_event_dict(a, in_numerator=in_num))
		return rows

	if mcode == "RESCHEDULE_RATE":
		fu_win = [
			ob
			for ob in obligations
			if ob.obligation_type == "FOLLOWUP" and (ob["in_started_window"] or ob["in_anchor_window"])
		]
		by_task: dict[str, list[dict]] = {}
		for ob in fu_win:
			if ob.followup_task:
				by_task.setdefault(ob.followup_task, []).append(ob)
		rows = []
		for task_id, ob_list in sorted(by_task.items()):
			was_resched = any(o["in_started_window"] and cint(o.reschedule_seq) > 0 for o in ob_list)
			if bucket == "numerator_only" and not was_resched:
				continue
			if bucket == "breached_only" and not was_resched:
				continue
			if bucket == "excluded":
				continue
			rep = ob_list[-1]
			d = _ob_event_dict(rep, in_numerator=was_resched)
			d["record_type"] = "followup_task"
			d["record_id"] = task_id
			d["followup_task"] = task_id
			rows.append(d)
		return rows

	if mcode == "DNC_COMPLIANCE_RATE":
		eff_valid = [
			a
			for a in attempts
			if a["in_effective_window"] and not a["is_voided"] and bool(a["effective"]["is_contact_attempt"])
		]
		violated_ivs: set[str] = set()
		lead_by_iv: dict[str, str] = {}
		owner_by_iv: dict[str, str] = {}
		wi_by_iv: dict[str, str] = {}
		camp_by_iv: dict[str, str] = {}
		team_by_iv: dict[str, str | None] = {}
		for iv in intervals:
			lead_by_iv[iv.name] = iv.lead
			owner_by_iv[iv.name] = iv.to_user
			wi_by_iv[iv.name] = iv.work_item
			camp_by_iv[iv.name] = iv.campaign
			team_by_iv[iv.name] = iv.get("resolved_team")
		for a in eff_valid:
			oe = a.get("ownership_event")
			if oe:
				lead_by_iv.setdefault(oe, a.get("lead") or "")
				owner_by_iv.setdefault(oe, a.get("actor_user") or "")
				wi_by_iv.setdefault(oe, a.get("work_item") or "")
				camp_by_iv.setdefault(oe, a.get("campaign") or "")
				team_by_iv.setdefault(oe, a.get("resolved_team"))
			dnc_ts = dnc_since_by_lead.get(a.get("lead") or "")
			if (
				dnc_ts
				and not bool(a["effective"].get("is_do_not_contact"))
				and get_datetime(a["effective"]["occurred_at"]) > dnc_ts + timedelta(seconds=1)
				and oe
			):
				violated_ivs.add(oe)

		eval_ids = sorted(
			{iv.name for iv in intervals}
			| {a["ownership_event"] for a in eff_valid if a.get("ownership_event")}
		)
		if bucket == "excluded":
			return []
		rows = []
		for iv_id in eval_ids:
			compliant = iv_id not in violated_ivs
			if bucket == "numerator_only" and not compliant:
				continue
			if bucket == "breached_only" and compliant:
				continue
			rows.append(
				{
					"record_type": "ownership_interval_dnc",
					"record_id": iv_id,
					"interval_event": iv_id,
					"work_item": wi_by_iv.get(iv_id),
					"campaign": camp_by_iv.get(iv_id),
					"owner_user": owner_by_iv.get(iv_id),
					"team_id": team_by_iv.get(iv_id),
					"status": "dnc_compliant" if compliant else "dnc_violated",
					"in_numerator": compliant,
					"_lead_id": lead_by_iv.get(iv_id),
					"_note_excerpt": None,
				}
			)
		return rows

	return []
