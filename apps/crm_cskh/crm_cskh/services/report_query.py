"""Snapshot-backed P3 quality reporting & reconciled metric drill-down service (`P3-07`, `P3-08`, `cskh-metrics-v2`).

Guarantees (`docs/p3/report-api-contract.md`, `docs/p3/authorization-matrix.md`):
  * **Indexed Snapshot Reads (`OBS-P2-02`):**
    Dashboard endpoints (`get_executive_overview`, `get_team_comparison`, `get_employee_quality`,
    `get_campaign_quality`, `get_my_scorecard`) read published `CSKH KPI Snapshot` rows via
    `idx_cskh_kpisnap_lookup` / `idx_cskh_kpisnap_rank` (`0` full-table scans on `tabCSKH Contact Attempt`).
  * **Strict Role, Historical `team-as-of` Slice & Closed-WI Isolation (`F-P3-04`, `S01..S09`, `S15`):**
    - `Staff`: ONLY `get_my_scorecard`, `get_employee_quality(employee=self)`, and `get_metric_drilldown(employee=self)`.
      `403 FORBIDDEN_SCOPE` on `get_executive_overview`, `get_team_comparison`, `get_campaign_quality`, or `employee != self`.
    - `Team Leader`: strictly bounded to live `CRM Sales Hierarchy` subtree AND historical `team-as-of` slice
      (`scope_type = 'employee_team'`). `Leader B` never sees `Team A` historical intervals/attempts/PII after a staff
      transfer; `Leader A` retains exact historical access to the `Team A` slice with transferred/closed-outside PII redacted.
    - `BOD Viewer`: company-wide aggregate KPIs without `System Manager` (`0` customer PII ever; `403 FORBIDDEN_SCOPE`
      on `get_my_scorecard` or `get_metric_drilldown(..., include_pii=1)`).
    - `CRM Admin` / `System`: full operational & audit visibility.
  * **Append-Only Metric Revisioning (`F-P3-06`):**
    Runs computed under an older `metric_version` trigger append-only `CSKH Dirty Scope` invalidation and restated
    `cskh-metrics-v2` publication without mutating historical snapshots.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from crm_cskh.api._params import parse_bool, parse_optional_str, parse_pagination
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p1_permissions import (
	ROLE_ADMIN,
	ROLE_BOD,
	_campaign_in_leader_scope,
	_is_system,
	_roles,
	_subtree_nodes,
)
from crm_cskh.p2_permissions import _is_leader, _is_staff, is_work_item_readable_for_workflow
from crm_cskh.services import p3_flags, reporting
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL
from crm_cskh.services.calendar import (
	DEFAULT_TIMEZONE,
	business_minutes_between,
	format_calendar_iso,
	resolve_working_calendar,
)
from crm_cskh.services.errors import (
	ForbiddenScope,
	InvalidMetric,
	InvalidRequest,
)
from crm_cskh.services.kpi_calculator import (
	COMPANY_SCOPE_ID,
	COMPANY_SCOPE_LABEL,
	DRILLDOWN_SUPPORTED_METRICS,
	ELIGIBLE_SLA_STATUSES,
	METRIC_LABELS_VI,
	METRIC_VERSION,
	UNATTRIBUTED_TEAM_ID,
	_aggregate_bucket,
	_lead_source_column,
	compute_period_snapshots,
	load_period_raw_dataset,
)
from crm_cskh.services.kpi_periods import PeriodSpec, resolve_period_spec
from crm_cskh.services.kpi_runs import (
	execute_kpi_recompute,
	has_pending_dirty_scope_for_period,
	schedule_post_commit_dirty_invalidation,
	unpack_snapshot_extra,
	unpack_snapshot_metrics,
)
from crm_cskh.services.policies import resolve_effective_policy
from crm_cskh.services.sanitizer import redact_pii_text

AUTO_COMPUTE_MAX_RAW_ROWS = 2000

ALL_SORTABLE_METRICS = frozenset(METRIC_LABELS_VI.keys())
VALID_DRILLDOWN_BUCKETS = frozenset(("all_denominator", "numerator_only", "breached_only", "excluded"))
ALLOWED_DRILLDOWN_SORT_FIELDS = frozenset(
	(
		"effective_due_at",
		"started_at",
		"occurred_at",
		"status",
		"work_item",
		"record_id",
		"owner_user",
		"campaign",
		"team_id",
	)
)

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
	return p3_flags.get_effective_p3_flags()


def is_p3_reporting_enabled() -> bool:
	return bool(get_policy_flags()["p3_reporting_enabled"])


def _actor_scope_dict(actor: ReportActor, flags: dict) -> dict:
	return {
		"user": actor.user,
		"role_profile": actor.role_profile,
		"pii_allowed": actor.pii_allowed,
		"p3_enabled": bool(flags.get("p3_enabled", True)),
		"qa_enabled": bool(flags["qa_enabled"]),
		"p3_sla_enabled": bool(flags["p3_sla_enabled"]),
		"p3_reporting_enabled": bool(flags["p3_reporting_enabled"]),
		"p3_export_enabled": bool(flags.get("p3_export_enabled", flags["p3_reporting_enabled"])),
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
		"teams_summary": [],
		"alerts": [],
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


def _validate_lead_source_filter(lead_source: str | None) -> str:
	return parse_optional_str(lead_source, "Tham số lead_source không hợp lệ.") or ""


def _resolve_team_label_map(only_team_ids: set[str] | None = None) -> dict[str, str]:
	team_map: dict[str, str] = {UNATTRIBUTED_TEAM_ID: "Chưa xác định đội nhóm (lịch sử trước P3)"}
	target_ids: list[str] | None = None
	if only_team_ids is not None:
		target_ids = sorted({str(t) for t in only_team_ids if t and str(t) != UNATTRIBUTED_TEAM_ID})
		if not target_ids:
			return team_map
	if frappe.db.table_exists("CRM Sales Hierarchy"):
		h_cols = ["h.name", "h.user", "h.is_group", "h.reports_to"]
		if frappe.db.has_column("CRM Sales Hierarchy", "full_name"):
			h_cols.append("h.full_name")
		where_sql = ""
		h_params: tuple = ()
		if target_ids is not None:
			ph = ", ".join(["%s"] * len(target_ids))
			where_sql = f" where h.name in ({ph})"
			h_params = tuple(target_ids)
		for r in frappe.db.sql(
			f"""
			select {", ".join(h_cols)}, u.full_name as user_full_name
			from `tabCRM Sales Hierarchy` h
			left join `tabUser` u on u.name = h.user{where_sql}
			""",
			h_params,
			as_dict=True,
		):
			team_map[str(r.name)] = str(
				r.get("full_name") or r.get("user_full_name") or r.get("user") or r.name
			)
	need_hist_ids: list[str] | None = None
	if target_ids is not None:
		need_hist_ids = [
			tid
			for tid in target_ids
			if (not team_map.get(tid)) or team_map.get(tid) == tid or "@" in str(team_map.get(tid))
		]
	if frappe.db.table_exists("CSKH Team Membership History") and (
		need_hist_ids is None or len(need_hist_ids) > 0
	):
		m_where = "where team_node is not null and team_node != ''"
		m_params: tuple = ()
		if need_hist_ids is not None:
			ph_m = ", ".join(["%s"] * len(need_hist_ids))
			m_where = f"where team_node in ({ph_m})"
			m_params = tuple(need_hist_ids)
		for mr in frappe.db.sql(
			f"""
			select team_node, max(team_name_snapshot) as team_name_snapshot
			from `tabCSKH Team Membership History`
			{m_where}
			group by team_node
			""",
			m_params,
			as_dict=True,
		):
			snap_lbl = mr.get("team_name_snapshot")
			if snap_lbl:
				cur = team_map.get(str(mr.team_node), "")
				if not cur or cur == mr.team_node or "@" in cur:
					team_map[str(mr.team_node)] = str(snap_lbl)
	return team_map


def _resolve_campaign_title_map(only_campaign_ids: set[str] | None = None) -> dict[str, str]:
	if not frappe.db.table_exists("CSKH Campaign"):
		return {}
	filters = None
	if only_campaign_ids is not None:
		cids = sorted({str(c) for c in only_campaign_ids if c})
		if not cids:
			return {}
		filters = {"name": ["in", cids]}
	rows = frappe.db.get_all("CSKH Campaign", filters=filters, fields=["name", "title", "code"])
	return {str(r.name): str(r.title or r.code or r.name) for r in rows}


def _active_users_in_team_nodes(team_nodes: set[str]) -> set[str]:
	if not team_nodes or not frappe.db.table_exists("CRM Sales Hierarchy"):
		return set()
	ph = ", ".join(["%s"] * len(team_nodes))
	rows = frappe.db.sql(
		f"""
		select distinct user
		from `tabCRM Sales Hierarchy`
		where (name in ({ph}) or reports_to in ({ph}))
		  and user is not null and user != ''
		""",
		tuple([*team_nodes, *team_nodes]),
		pluck=True,
	)
	return set(rows or ())


def _snapshot_has_activity(snap: dict) -> bool:
	if int(snap.get("ownership_intervals_count") or 0) > 0:
		return True
	if int(snap.get("contact_attempt_count") or 0) > 0:
		return True
	metrics = snap.get("metrics") or {}
	for mcode in ("FIRST_ATTEMPT_SLA_RATE", "FOLLOWUP_SLA_RATE", "QA_PASS_RATE"):
		den = (metrics.get(mcode) or {}).get("denominator")
		if den and int(den) > 0:
			return True
	return False


def _load_latest_published_run_row(period: PeriodSpec, campaign_filter: str = "") -> dict | None:
	rows = frappe.db.sql(
		"""
		select name, run_mode, as_of, source_watermark, is_restated, restated_from_run, metric_version, creation
		from `tabCSKH KPI Run`
		where period_type = %s
		  and period_key = %s
		  and coalesce(campaign_filter, '') = %s
		  and is_latest_published = 1
		order by creation desc
		limit 1
		""",
		(period.period_type, period.period_key, campaign_filter or ""),
		as_dict=True,
	)
	return rows[0] if rows else None


def _ensure_or_load_published_run(period: PeriodSpec, campaign_filter: str = "") -> dict | None:
	"""Fetch the latest published `CSKH KPI Run` for `(period, campaign_filter)`.

	If an older `metric_version` run is currently published (`F-P3-06`), marks `CSKH Dirty Scope`
	append-only and publishes a restated `METRIC_VERSION` run without mutating historical snapshots.
	"""
	run_row = _load_latest_published_run_row(period, campaign_filter=campaign_filter)
	stale_version = bool(run_row and (run_row.metric_version or "") != METRIC_VERSION)
	if run_row and not stale_version:
		return run_row

	if stale_version and run_row:
		prev_suppress = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
		frappe.flags.cskh_suppress_dirty_enqueue = True
		try:
			schedule_post_commit_dirty_invalidation(
				campaign=campaign_filter or None,
				event_dt=period.to_date,
				reason="metric_version_upgrade",
				trigger_reference=run_row.name,
			)
			from crm_cskh.services.kpi_runs import _flush_pending_dirty_events

			_flush_pending_dirty_events()
		finally:
			frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress

	att_cnt = int(
		frappe.db.sql(
			"select count(*) from `tabCSKH Contact Attempt` where occurred_at >= %s and occurred_at < %s",
			(period.start_dt, period.end_dt_exclusive),
		)[0][0]
		or 0
	)
	ob_cnt = (
		int(
			frappe.db.sql(
				"select count(*) from `tabCSKH SLA Obligation` where effective_due_at >= %s and effective_due_at < %s",
				(period.start_dt, period.end_dt_exclusive),
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
				run_mode="restated" if stale_version else "full",
				trigger_reason="metric_version_upgrade"
				if stale_version
				else "initial_on_demand_small_period",
				trigger_reference=run_row.name if (stale_version and run_row) else "",
				campaign_filter=campaign_filter or "",
				as_of=run_row.as_of if (stale_version and run_row and run_row.as_of) else None,
				actor="Administrator",
			)
		except Exception:
			pass
		return _load_latest_published_run_row(period, campaign_filter=campaign_filter)

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
	return run_row


def _build_freshness(period: PeriodSpec, run_row: dict | None, campaign_filter: str = "") -> dict:
	dirty = has_pending_dirty_scope_for_period(period, campaign_filter=campaign_filter)
	if run_row and (run_row.metric_version or "") != METRIC_VERSION:
		dirty = True
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
	team_filters: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
	"""Indexed read on `tabCSKH KPI Snapshot` via `idx_cskh_kpisnap_lookup`."""
	if scope_ids is not None and len(scope_ids) == 0:
		return []
	if team_filters is not None and len(team_filters) == 0:
		return []
	camp_val = campaign_filter or ""
	camp_sql = "and campaign_filter = %s"
	params: list = [1, period.period_type, period.period_key, camp_val, scope_type]
	extra_sql = ""
	if scope_ids is not None:
		ph = ", ".join(["%s"] * len(scope_ids))
		extra_sql += f" and scope_id in ({ph})"
		params.extend(scope_ids)
	if team_filters is not None:
		ph_t = ", ".join(["%s"] * len(team_filters))
		extra_sql += f" and team_filter in ({ph_t})"
		params.extend(team_filters)

	index_hint = (
		"USE INDEX (idx_cskh_kpisnap_lookup)"
		if team_filters is None
		else "USE INDEX (idx_cskh_kpisnap_emp_team)"
	)
	rows = frappe.db.sql(
		f"""
		select name, kpi_run, scope_type, scope_id, scope_label, team_filter,
		       parent_team_id, parent_team_label,
		       active_staff_count, process_score_status, process_score_sort,
		       distinct_work_items, ownership_intervals_count, work_items_touched,
		       contact_attempt_count, connected_attempt_count,
		       metrics_json, exclusion_counts_json, trend_series_json
		from `tabCSKH KPI Snapshot` {index_hint}
		where is_latest_published = %s
		  and period_type = %s
		  and period_key = %s
		  {camp_sql}
		  and scope_type = %s{extra_sql}
		""",
		tuple(params),
		as_dict=True,
	)
	for r in rows:
		if r.scope_type == "company" and r.scope_label == "Toàn công ty CSKH":
			r["scope_label"] = COMPANY_SCOPE_LABEL
		metrics, comps, breakdown = unpack_snapshot_metrics(r.metrics_json)
		extra = unpack_snapshot_extra(r.metrics_json)
		r["metrics"] = metrics
		r["score_components"] = comps
		r["attempt_breakdown"] = breakdown
		r["lead_source"] = extra.get("lead_source")
		r["exclusion_counts"] = json.loads(r.exclusion_counts_json or "{}")
		r["trend_series"] = json.loads(r.trend_series_json or "[]")
	return rows


def _fetch_or_compute_snapshots(
	period: PeriodSpec,
	scope_type: str,
	*,
	run_row: dict | None,
	campaign_filter: str = "",
	lead_source_filter: str = "",
	scope_ids: list[str] | tuple[str, ...] | None = None,
	team_filters: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
	if not lead_source_filter:
		if not run_row:
			return []
		return _fetch_snapshots(
			period,
			scope_type,
			campaign_filter=campaign_filter,
			scope_ids=scope_ids,
			team_filters=team_filters,
		)
	as_of_dt = get_datetime(run_row.as_of) if (run_row and run_row.as_of) else None
	computed = compute_period_snapshots(
		period,
		as_of=as_of_dt,
		campaign_filter=campaign_filter,
		lead_source_filter=lead_source_filter,
	)
	id_set = set(scope_ids) if scope_ids is not None else None
	tf_set = set(team_filters) if team_filters is not None else None
	out = []
	for s in computed["snapshots"]:
		if s["scope_type"] != scope_type:
			continue
		if id_set is not None and s["scope_id"] not in id_set:
			continue
		if tf_set is not None and (s.get("team_filter") or "") not in tf_set:
			continue
		out.append(frappe._dict(s))
	return out


def _sort_rows_nulls_last(rows: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
	"""Sort snapshot-derived rows by `metrics[sort_by].value` with `None` ALWAYS last (`NULLS LAST`)."""
	metric_key = (sort_by or "PROCESS_SCORE").strip().upper()
	if metric_key == "BREACH_COUNT":
		metric_key = "SLA_BREACH_COUNT"
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


def _format_team_row(s: dict) -> dict:
	metrics = s["metrics"]
	return {
		"_tie_id": s.scope_id,
		"team_id": s.scope_id,
		"team_name": s.scope_label or s.scope_id,
		"is_unattributed_bucket": s.scope_id == UNATTRIBUTED_TEAM_ID,
		"active_staff_count": int(s.active_staff_count or 0),
		"distinct_work_items": int(s.distinct_work_items or 0),
		"ownership_intervals_count": int(s.ownership_intervals_count or 0),
		"contact_attempt_count": int(s.contact_attempt_count or 0),
		"overdue_work_item_backlog": metrics.get("OVERDUE_BACKLOG"),
		"open_followups_overdue": metrics.get("OPEN_FOLLOWUPS_OVERDUE"),
		"attempt_breakdown": s["attempt_breakdown"],
		"metrics": metrics,
	}


def _build_aggregate_alerts(metrics: dict, freshness: dict) -> list[dict]:
	"""Build zero-PII operational alerts for `get_executive_overview` (`BOD`-safe)."""
	alerts: list[dict] = []
	overdue_val = int((metrics.get("OVERDUE_BACKLOG") or {}).get("value") or 0)
	if overdue_val > 0:
		alerts.append(
			{
				"code": "OVERDUE_WORK_ITEM_BACKLOG",
				"severity": "warning",
				"count": overdue_val,
				"message_vi": f"Có {overdue_val} hồ sơ tác nghiệp đang quá hạn SLA chưa hoàn tất.",
			}
		)
	fu_val = int((metrics.get("OPEN_FOLLOWUPS_OVERDUE") or {}).get("value") or 0)
	if fu_val > 0:
		alerts.append(
			{
				"code": "OPEN_FOLLOWUPS_OVERDUE",
				"severity": "warning",
				"count": fu_val,
				"message_vi": f"Có {fu_val} lịch hẹn gọi lại đang mở bị quá hạn.",
			}
		)
	breach_val = int(
		((metrics.get("SLA_BREACH_COUNT") or metrics.get("BREACH_COUNT")) or {}).get("value") or 0
	)
	if breach_val > 0:
		alerts.append(
			{
				"code": "SLA_BREACHES_IN_PERIOD",
				"severity": "info",
				"count": breach_val,
				"message_vi": f"Ghi nhận {breach_val} nghĩa vụ SLA vi phạm hạn trong kỳ báo cáo.",
			}
		)
	if freshness.get("dirty_pending"):
		alerts.append(
			{
				"code": "SNAPSHOT_STALE_RECOMPUTING",
				"severity": "info",
				"count": 1,
				"message_vi": "Dữ liệu phát sinh mới đang chờ cập nhật bản chụp KPI.",
			}
		)
	return alerts


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
	lead_source: str | None = None,
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
	src_filter = _validate_lead_source_filter(lead_source)

	target_scope_type = "company"
	target_scope_id = COMPANY_SCOPE_ID
	if act.role_profile == "team_leader":
		target_scope_type = "team"
		target_scope_id = team_filter or act.root_node or ""
	elif team_filter:
		target_scope_type = "team"
		target_scope_id = team_filter

	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = _fetch_or_compute_snapshots(
		period,
		target_scope_type,
		run_row=run_row,
		campaign_filter=camp_filter,
		lead_source_filter=src_filter,
		scope_ids=[target_scope_id],
	)
	snap = snaps[0] if snaps else None

	metrics = dict(snap["metrics"]) if snap else {}
	if snap and "CONTACT_ATTEMPT_COUNT" in metrics:
		metrics["CONTACT_ATTEMPT_COUNT"] = {
			**metrics["CONTACT_ATTEMPT_COUNT"],
			"attempt_breakdown": snap["attempt_breakdown"],
		}

	# Load scoped `teams_summary` (`0` PII) so overview renders team comparison & backlog cards in 1 roundtrip
	team_scope_ids: list[str] | None = None
	if team_filter:
		team_scope_ids = sorted(_subtree_nodes(team_filter) or {team_filter})
		if act.role_profile == "team_leader" and act.allowed_nodes:
			team_scope_ids = [n for n in team_scope_ids if n in act.allowed_nodes]
	elif act.role_profile == "team_leader":
		team_scope_ids = list(act.allowed_nodes or ())

	team_snaps = _fetch_or_compute_snapshots(
		period,
		"team",
		run_row=run_row,
		campaign_filter=camp_filter,
		lead_source_filter=src_filter,
		scope_ids=team_scope_ids,
	)
	teams_summary_raw = [
		_format_team_row(ts)
		for ts in team_snaps
		if not (act.role_profile == "team_leader" and ts.scope_id == UNATTRIBUTED_TEAM_ID)
	]
	teams_summary = _sort_rows_nulls_last(teams_summary_raw, sort_by="PROCESS_SCORE", sort_dir="desc")
	freshness = _build_freshness(period, run_row, campaign_filter=camp_filter)

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"scope": {
			"scope_type": target_scope_type,
			"scope_id": target_scope_id,
			"campaign_filter": camp_filter or None,
			"lead_source_filter": src_filter or None,
			"distinct_work_items": int(snap.distinct_work_items) if snap else 0,
			"ownership_intervals_count": int(snap.ownership_intervals_count) if snap else 0,
		},
		"freshness": freshness,
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
		"first_touch_on_time_rate": metrics.get("FIRST_ATTEMPT_SLA_RATE"),
		"score_components": snap["score_components"] if snap else [],
		"metrics": metrics,
		"teams_summary": teams_summary,
		"alerts": _build_aggregate_alerts(metrics, freshness),
		"trend_series": snap["trend_series"] if snap else [],
		"rows": [],
		"pagination": {
			"page": 1,
			"page_size": 1,
			"total": 1 if snap else 0,
			"total_pages": 1 if snap else 0,
		},
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
	lead_source: str | None = None,
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
	src_filter = _validate_lead_source_filter(lead_source)

	scope_ids: list[str] | None = None
	if team_filter:
		sub = sorted(_subtree_nodes(team_filter) or {team_filter})
		if act.role_profile == "team_leader" and act.allowed_nodes:
			sub = [n for n in sub if n in act.allowed_nodes]
		scope_ids = sub
	elif act.role_profile == "team_leader":
		scope_ids = list(act.allowed_nodes or ())

	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = _fetch_or_compute_snapshots(
		period,
		"team",
		run_row=run_row,
		campaign_filter=camp_filter,
		lead_source_filter=src_filter,
		scope_ids=scope_ids,
	)

	comp_snaps = (
		_fetch_or_compute_snapshots(
			period,
			"company",
			run_row=run_row,
			campaign_filter=camp_filter,
			lead_source_filter=src_filter,
			scope_ids=[COMPANY_SCOPE_ID],
		)
		if act.role_profile in ("crm_admin", "bod")
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

	formatted = [
		_format_team_row(s)
		for s in snaps
		if not (act.role_profile == "team_leader" and s.scope_id == UNATTRIBUTED_TEAM_ID)
	]

	ordered = _sort_rows_nulls_last(formatted, sort_by=sort_by, sort_dir=sort_dir)
	total = len(ordered)
	paged = ordered[offset : offset + limit]

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"freshness": _build_freshness(period, run_row, campaign_filter=camp_filter),
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
# 3. `get_employee_quality` (§3.3 — `F-P3-04` Historical `team-as-of` Slice!)
# ---------------------------------------------------------------------------


def _aggregate_multi_node_employee_slice(
	period: PeriodSpec,
	run_row: dict | None,
	*,
	uid: str,
	emp_label: str,
	target_team_nodes: set[str],
	primary_team_id: str | None,
	primary_team_label: str | None,
	campaign_filter: str,
	lead_source_filter: str,
) -> dict:
	as_of_dt = get_datetime(run_row.as_of) if (run_row and run_row.as_of) else None
	ds = load_period_raw_dataset(
		period,
		as_of=as_of_dt,
		campaign_filter=campaign_filter,
		lead_source_filter=lead_source_filter,
	)
	u_atts = [
		a
		for a in ds["attempts"]
		if a.get("actor_user") == uid
		and a.get("resolved_attribution") == AUTHORITATIVE
		and a.get("resolved_team") in target_team_nodes
	]
	u_obs = [
		ob
		for ob in ds["obligations"]
		if ob.get("owner_user") == uid
		and ob.get("resolved_attribution") == AUTHORITATIVE
		and ob.get("resolved_team") in target_team_nodes
	]
	u_ivs = [
		iv
		for iv in ds["intervals"]
		if iv.get("to_user") == uid
		and iv.get("resolved_attribution") == AUTHORITATIVE
		and iv.get("resolved_team") in target_team_nodes
	]
	u_qa = [
		qr
		for qr in ds["qa_reviews"]
		if qr.get("reviewed_staff") == uid and (qr.get("team_at_review") or "") in target_team_nodes
	]
	bucket = _aggregate_bucket(
		scope_type="employee",
		scope_id=uid,
		scope_label=emp_label,
		attempts=u_atts,
		obligations=u_obs,
		intervals=u_ivs,
		qa_reviews=u_qa,
		dnc_since_by_lead=ds["dnc_since_by_lead"],
		policy_cfg=ds["policy_cfg"],
		qa_enabled=ds["qa_enabled"],
		min_attempts_enabled=ds["min_attempts_enabled"],
		parent_team_id=primary_team_id,
		parent_team_label=primary_team_label,
		unknown_team_exclusions=0,
	)
	return frappe._dict(bucket)


def get_employee_quality(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	team: str | None = None,
	employee: str | None = None,
	campaign: str | None = None,
	lead_source: str | None = None,
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
	src_filter = _validate_lead_source_filter(lead_source)

	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)

	# Determine whether `employee_team` historical slice isolation applies (`F-P3-04`!)
	use_team_slice = bool(act.role_profile == "team_leader" or team_filter)
	snaps_used: list[dict] = []
	formatted: list[dict] = []

	if not use_team_slice:
		# `crm_admin` / `bod` (unfiltered by team) or `staff` (`self` only)
		emp_scope_ids = [emp_param] if emp_param else None
		snaps_used = _fetch_or_compute_snapshots(
			period,
			"employee",
			run_row=run_row,
			campaign_filter=camp_filter,
			lead_source_filter=src_filter,
			scope_ids=emp_scope_ids,
		)
		for s in snaps_used:
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
					"score_components": s.get("score_components") or [],
					"metrics": s["metrics"],
				}
			)
	else:
		# Slice strictly by `team-as-of` (`scope_type = 'employee_team'`) over `target_team_nodes`!
		if team_filter:
			sub_nodes = set(_subtree_nodes(team_filter) or {team_filter})
			if act.role_profile == "team_leader":
				sub_nodes &= set(act.allowed_nodes or ())
			target_team_nodes = sub_nodes
		else:
			target_team_nodes = set(act.allowed_nodes or ())

		et_snaps = _fetch_or_compute_snapshots(
			period,
			"employee_team",
			run_row=run_row,
			campaign_filter=camp_filter,
			lead_source_filter=src_filter,
			team_filters=sorted(target_team_nodes),
		)
		by_uid: dict[str, list[dict]] = defaultdict(list)
		for et in et_snaps:
			raw_sid = str(et.scope_id or "")
			uid = raw_sid.split("::", 1)[0] if "::" in raw_sid else raw_sid
			if uid:
				by_uid[uid].append(et)

		active_members = _active_users_in_team_nodes(target_team_nodes)
		if act.role_profile == "team_leader" and emp_param:
			in_live_subtree = bool(act.allowed_users and emp_param in act.allowed_users)
			has_slice_activity = any(_snapshot_has_activity(s) for s in by_uid.get(emp_param, ()))
			if not (in_live_subtree or has_slice_activity):
				raise ForbiddenScope(
					"Nhân viên được chọn nằm ngoài phạm vi quản lý và lịch sử của đội nhóm bạn."
				)

		candidate_uids = [emp_param] if emp_param else sorted(set(by_uid.keys()) | active_members)
		for uid in candidate_uids:
			if not uid:
				continue
			u_slices = by_uid.get(uid, [])
			has_act = any(_snapshot_has_activity(s) for s in u_slices)
			is_active_in_target = uid in active_members
			if not (has_act or is_active_in_target):
				continue

			if len(u_slices) == 1:
				chosen = u_slices[0]
			elif len(u_slices) > 1:
				chosen = _aggregate_multi_node_employee_slice(
					period,
					run_row,
					uid=uid,
					emp_label=u_slices[0].scope_label or uid,
					target_team_nodes=target_team_nodes,
					primary_team_id=u_slices[0].parent_team_id,
					primary_team_label=u_slices[0].parent_team_label,
					campaign_filter=camp_filter,
					lead_source_filter=src_filter,
				)
			else:
				# Active member of `target_team_nodes` with `0` slice rows (e.g. brand-new user)
				emp_snaps = _fetch_or_compute_snapshots(
					period,
					"employee",
					run_row=run_row,
					campaign_filter=camp_filter,
					lead_source_filter=src_filter,
					scope_ids=[uid],
				)
				if emp_snaps and not _snapshot_has_activity(emp_snaps[0]):
					chosen = emp_snaps[0]
				else:
					t_lbls = _resolve_team_label_map()
					pt_id = sorted(target_team_nodes)[0] if target_team_nodes else None
					chosen = _aggregate_multi_node_employee_slice(
						period,
						run_row,
						uid=uid,
						emp_label=str(frappe.db.get_value("User", uid, "full_name") or uid),
						target_team_nodes=target_team_nodes,
						primary_team_id=pt_id,
						primary_team_label=t_lbls.get(pt_id or "") if pt_id else None,
						campaign_filter=camp_filter,
						lead_source_filter=src_filter,
					)

			snaps_used.append(chosen)
			formatted.append(
				{
					"_tie_id": uid,
					"employee_id": uid,
					"employee_name": chosen.scope_label or uid,
					"team_id": chosen.parent_team_id or None,
					"team_name": chosen.parent_team_label or None,
					"intervals_count": int(chosen.ownership_intervals_count or 0),
					"work_items_touched": int(chosen.work_items_touched or 0),
					"contact_attempt_count": int(chosen.contact_attempt_count or 0),
					"connected_attempt_count": int(chosen.connected_attempt_count or 0),
					"attempt_breakdown": chosen["attempt_breakdown"],
					"score_components": chosen.get("score_components") or [],
					"metrics": chosen["metrics"],
				}
			)

	ordered = _sort_rows_nulls_last(formatted, sort_by=sort_by, sort_dir=sort_dir)
	total = len(ordered)
	paged = ordered[offset : offset + limit]

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"freshness": _build_freshness(period, run_row, campaign_filter=camp_filter),
		"policy_notice": dict(POLICY_NOTICE_PAYLOAD),
		"exclusion_counts": {
			"historical_attribution_unknown": 0,
			"waived_obligations": sum(
				int((s.get("exclusion_counts") or {}).get("waived_obligations", 0)) for s in snaps_used
			),
			"canceled_validly_dnc": sum(
				int((s.get("exclusion_counts") or {}).get("canceled_validly_dnc", 0)) for s in snaps_used
			),
			"voided_attempts": sum(
				int((s.get("exclusion_counts") or {}).get("voided_attempts", 0)) for s in snaps_used
			),
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


def _load_campaign_lead_sources_summary(campaign_ids: list[str]) -> dict[str, str | None]:
	if not campaign_ids or not frappe.db.table_exists("CSKH Work Item"):
		return {}
	src_col = _lead_source_column()
	ph = ", ".join(["%s"] * len(campaign_ids))
	rows = frappe.db.sql(
		f"""
		select distinct wi.campaign, ld.`{src_col}` as lead_source
		from `tabCSKH Work Item` wi
		inner join `tabCRM Lead` ld on ld.name = wi.lead
		where wi.campaign in ({ph})
		  and ld.`{src_col}` is not null and ld.`{src_col}` != ''
		""",
		tuple(campaign_ids),
		as_dict=True,
	)
	by_camp: dict[str, set[str]] = defaultdict(set)
	for r in rows:
		if r.campaign and r.lead_source:
			by_camp[str(r.campaign)].add(str(r.lead_source))
	return {cid: (", ".join(sorted(vals)) if vals else None) for cid, vals in by_camp.items()}


def get_campaign_quality(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	lead_source: str | None = None,
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
	src_filter = _validate_lead_source_filter(lead_source)

	run_row = _ensure_or_load_published_run(period, campaign_filter="")
	formatted = []

	if act.role_profile in ("crm_admin", "bod") and not team_filter:
		scope_ids = [camp_filter] if camp_filter else None
		snaps = _fetch_or_compute_snapshots(
			period,
			"campaign",
			run_row=run_row,
			campaign_filter="",
			lead_source_filter=src_filter,
			scope_ids=scope_ids,
		)
		missing_src_cids = [str(s.scope_id) for s in snaps if not s.get("lead_source")]
		fallback_src_map = _load_campaign_lead_sources_summary(missing_src_cids) if not src_filter else {}
		for s in snaps:
			if src_filter and not (
				int(s.distinct_work_items or 0) > 0
				or int(s.ownership_intervals_count or 0) > 0
				or int(s.contact_attempt_count or 0) > 0
			):
				continue
			c_src = src_filter or s.get("lead_source") or fallback_src_map.get(str(s.scope_id))
			formatted.append(
				{
					"_tie_id": s.scope_id,
					"campaign_id": s.scope_id,
					"campaign_name": s.scope_label or s.scope_id,
					"lead_source": c_src,
					"distinct_work_items": int(s.distinct_work_items or 0),
					"ownership_intervals_count": int(s.ownership_intervals_count or 0),
					"work_items_touched": int(s.work_items_touched or 0),
					"contact_attempt_count": int(s.contact_attempt_count or 0),
					"campaign_conversion_rate": s["metrics"].get("CONVERSION_RATE"),
					"attempt_breakdown": s["attempt_breakdown"],
					"metrics": s["metrics"],
				}
			)
	else:
		# Scoped campaign projection for Team Leader (or explicit `team` filter)
		if team_filter:
			allowed_teams = set(_subtree_nodes(team_filter) or {team_filter})
			if act.role_profile == "team_leader":
				allowed_teams &= set(act.allowed_nodes or ())
		else:
			allowed_teams = set(act.allowed_nodes or ())
		as_of_dt = get_datetime(run_row.as_of) if (run_row and run_row.as_of) else None
		ds = load_period_raw_dataset(
			period,
			as_of=as_of_dt,
			campaign_filter=camp_filter or "",
			lead_source_filter=src_filter,
		)
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
			if src_filter and not (c_atts or c_obs or c_ivs):
				continue
			c_src_str = ds.get("campaign_lead_sources", {}).get(cid)
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
					"lead_source": src_filter or (str(c_src_str) if c_src_str else None),
					"distinct_work_items": bucket["distinct_work_items"],
					"ownership_intervals_count": bucket["ownership_intervals_count"],
					"work_items_touched": bucket["work_items_touched"],
					"contact_attempt_count": bucket["contact_attempt_count"],
					"campaign_conversion_rate": bucket["metrics"].get("CONVERSION_RATE"),
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
	lead_source: str | None = None,
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
	src_filter = _validate_lead_source_filter(lead_source)
	run_row = _ensure_or_load_published_run(period, campaign_filter=camp_filter)
	snaps = _fetch_or_compute_snapshots(
		period,
		"employee",
		run_row=run_row,
		campaign_filter=camp_filter,
		lead_source_filter=src_filter,
		scope_ids=[act.user],
	)
	snap = snaps[0] if snaps else None

	# Active SLA obligations STRICTLY on Work Items currently owned by `act.user` (`0` transferred Lead leakage!)
	active_obligations: list[dict] = []
	if frappe.db.table_exists("CSKH SLA Obligation") and flags["p3_sla_enabled"]:
		active_states_ph = ", ".join(["%s"] * len(OWNED_ACTIVE_WORK_ITEM_STATES))
		camp_map = _resolve_campaign_title_map()
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
					"campaign_name": camp_map.get(str(ob.campaign)) if ob.campaign else None,
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

	ps_cell = (snap["metrics"] or {}).get("PROCESS_SCORE", {}) if snap else {}
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
		"freshness": _build_freshness(period, run_row, campaign_filter=camp_filter),
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
		"raw_points_total": ps_cell.get("raw_points_total"),
		"valid_weight": ps_cell.get("valid_weight"),
		"configured_weight_total": ps_cell.get("configured_weight_total"),
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
# 6. `get_filter_options` (`F-P3-10` — Scoped Filter Dropdown Catalog)
# ---------------------------------------------------------------------------


def get_filter_options(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	*,
	actor: str | None = None,
) -> dict:
	"""Return strictly role-scoped filter options (`campaigns`, `teams`, `employees`, `lead_sources`, `revisions`)."""
	act = resolve_report_actor(actor)
	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	flags = get_policy_flags()
	if not flags["p3_reporting_enabled"]:
		return {
			"ok": True,
			"reporting_enabled": False,
			"status": "disabled_by_policy",
			"actor_scope": _actor_scope_dict(act, flags),
			"period": _period_dict(period),
			"campaigns": [],
			"teams": [],
			"employees": [],
			"lead_sources": [],
			"revisions": [],
		}

	team_labels = _resolve_team_label_map()

	# 1. Scoped Campaigns (`0` customer PII)
	campaigns: list[dict] = []
	camp_ids_for_sources: list[str] = []
	if frappe.db.table_exists("CSKH Campaign"):
		camp_docs = frappe.get_all(
			"CSKH Campaign",
			fields=["name", "title", "code", "status", "team_scope"],
			order_by="creation desc",
		)
		if act.role_profile == "staff":
			staff_camps = set(
				frappe.db.sql(
					"""
					select distinct campaign from `tabCSKH Work Item` where assigned_to = %s
					union
					select distinct campaign from `tabCSKH Assignment Rule` where user = %s
					""",
					(act.user, act.user),
					pluck=True,
				)
				or []
			)
			camp_docs = [c for c in camp_docs if c.name in staff_camps]
		elif act.role_profile == "team_leader":
			camp_docs = [c for c in camp_docs if _campaign_in_leader_scope(act.user, c)]
		for c in camp_docs:
			camp_ids_for_sources.append(str(c.name))
			campaigns.append(
				{
					"value": c.name,
					"campaign_id": c.name,
					"label": str(c.title or c.code or c.name),
					"code": c.code,
					"status": c.status,
				}
			)

	# 2. Scoped Teams (`value` = hierarchy node ID, `label` = Vietnamese team label)
	teams: list[dict] = []
	if act.role_profile != "staff" and frappe.db.table_exists("CRM Sales Hierarchy"):
		group_nodes = frappe.db.get_all(
			"CRM Sales Hierarchy",
			filters={"is_group": 1},
			fields=["name", "lft"],
			order_by="lft asc",
		)
		allowed_set = set(act.allowed_nodes or ()) if act.role_profile == "team_leader" else None
		for g in group_nodes:
			if allowed_set is not None and g.name not in allowed_set:
				continue
			teams.append(
				{
					"value": g.name,
					"team_id": g.name,
					"label": team_labels.get(str(g.name), str(g.name)),
				}
			)

	# 3. Scoped Employees (`0` customer PII)
	employees: list[dict] = []
	if act.role_profile == "staff":
		u_lbl = frappe.db.get_value("User", act.user, "full_name") or act.user
		employees.append({"value": act.user, "employee_id": act.user, "label": str(u_lbl), "team_id": None})
	elif frappe.db.table_exists("CRM Sales Hierarchy"):
		emp_uids: set[str] = set()
		uid_team: dict[str, str | None] = {}
		h_rows = frappe.db.get_all("CRM Sales Hierarchy", fields=["name", "user", "reports_to", "is_group"])
		for hr in h_rows:
			if not hr.user:
				continue
			t_id = hr.name if cint(hr.is_group) else hr.reports_to
			if act.role_profile == "team_leader":
				if t_id not in set(act.allowed_nodes or ()):
					continue
			emp_uids.add(str(hr.user))
			uid_team[str(hr.user)] = t_id
		if act.role_profile == "team_leader" and act.allowed_nodes:
			et_rows = _fetch_snapshots(
				period,
				"employee_team",
				team_filters=list(act.allowed_nodes),
			)
			for et in et_rows:
				if _snapshot_has_activity(et):
					uid = str(et.scope_id).split("::", 1)[0]
					emp_uids.add(uid)
					uid_team.setdefault(uid, et.parent_team_id)
		if emp_uids:
			ph_u = ", ".join(["%s"] * len(emp_uids))
			u_rows = frappe.db.sql(
				f"select name, full_name from `tabUser` where name in ({ph_u}) order by full_name asc, name asc",
				tuple(sorted(emp_uids)),
				as_dict=True,
			)
			for ur in u_rows:
				tid = uid_team.get(str(ur.name))
				employees.append(
					{
						"value": ur.name,
						"employee_id": ur.name,
						"label": str(ur.full_name or ur.name),
						"team_id": tid,
						"team_name": team_labels.get(str(tid)) if tid else None,
					}
				)

	# 4. Scoped Lead Sources (distinct `CRM Lead.source` values, `0` customer PII)
	lead_sources: list[dict] = []
	if camp_ids_for_sources and frappe.db.table_exists("CSKH Work Item"):
		src_col = _lead_source_column()
		ph_c = ", ".join(["%s"] * len(camp_ids_for_sources))
		src_vals = frappe.db.sql(
			f"""
			select distinct ld.`{src_col}`
			from `tabCSKH Work Item` wi
			inner join `tabCRM Lead` ld on ld.name = wi.lead
			where wi.campaign in ({ph_c})
			  and ld.`{src_col}` is not null and ld.`{src_col}` != ''
			order by ld.`{src_col}` asc
			""",
			tuple(camp_ids_for_sources),
			pluck=True,
		)
		for sv in src_vals or ():
			lead_sources.append({"value": str(sv), "label": str(sv)})

	# 5. Revisions (`CSKH KPI Run` history — company-wide metadata restricted to `crm_admin` & `bod`)
	revisions: list[dict] = []
	if act.role_profile in ("crm_admin", "bod") and frappe.db.table_exists("CSKH KPI Run"):
		r_rows = frappe.db.sql(
			"""
			select name, run_mode, status, metric_version, as_of, is_latest_published, is_restated, restated_from_run
			from `tabCSKH KPI Run`
			where period_type = %s and period_key = %s and coalesce(campaign_filter, '') = ''
			order by creation desc
			limit 20
			""",
			(period.period_type, period.period_key),
			as_dict=True,
		)
		for rr in r_rows:
			revisions.append(
				{
					"run_id": rr.name,
					"run_mode": rr.run_mode,
					"status": rr.status,
					"metric_version": rr.metric_version,
					"as_of": format_calendar_iso(rr.as_of) if rr.as_of else None,
					"is_latest_published": bool(cint(rr.is_latest_published)),
					"is_restated": bool(cint(rr.is_restated)),
					"restated_from_run": rr.restated_from_run,
				}
			)

	return {
		"ok": True,
		"actor_scope": _actor_scope_dict(act, flags),
		"period": _period_dict(period),
		"campaigns": campaigns,
		"teams": teams,
		"employees": employees,
		"lead_sources": lead_sources,
		"revisions": revisions,
	}


# ---------------------------------------------------------------------------
# 7. `get_metric_drilldown` (§3.6 — Reconciled Cohort + `team-as-of` + Privacy Projection)
# ---------------------------------------------------------------------------


def _load_lead_display_map(lead_ids: list[str], actor: ReportActor) -> dict[str, dict]:
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


def _prime_wi_readable_cache(
	wi_ids: list[str],
	actor: ReportActor,
	wi_readable_cache: dict[str, bool],
) -> None:
	"""Batch-prefetch `wi_readable_cache` (`SEC2-01..07`, `L-23`, `F-P3-04`) to eliminate per-row N+1 SQL loops."""
	if actor.role_profile in ("crm_admin", "bod") or not actor.pii_allowed or not wi_ids:
		return
	missing = sorted({str(w) for w in wi_ids if w and str(w) not in wi_readable_cache})
	if not missing:
		return

	has_owner_close = frappe.db.has_column("CSKH Work Item", "owner_at_close")
	wi_cols = ["name", "lead", "campaign", "status", "assigned_to", "closed_by", "last_activity_by"]
	if has_owner_close:
		wi_cols.append("owner_at_close")
	ph_wi = ", ".join(["%s"] * len(missing))
	wi_rows = frappe.db.sql(
		f"""
		select {", ".join(wi_cols)}
		from `tabCSKH Work Item`
		where name in ({ph_wi})
		""",
		tuple(missing),
		as_dict=True,
	)
	found_by_name = {str(r.name): r for r in wi_rows}
	for w in missing:
		if w not in found_by_name:
			wi_readable_cache[w] = False

	if actor.role_profile == "team_leader":
		allowed_nodes_set = set(actor.allowed_nodes or ())
		allowed_users_set = set(actor.allowed_users or ()) | {actor.user}
		closed_wis: list[dict] = []
		for wi in wi_rows:
			w_name = str(wi.name)
			status = wi.get("status")
			assigned_to = wi.get("assigned_to")
			if status in OWNED_ACTIVE_WORK_ITEM_STATES and assigned_to:
				wi_readable_cache[w_name] = bool(assigned_to in allowed_users_set)
			elif status == "closed" and not assigned_to:
				closed_wis.append(wi)
			else:
				wi_readable_cache[w_name] = False

		if not closed_wis:
			return

		camp_ids = sorted({str(w.campaign) for w in closed_wis if w.get("campaign")})
		camp_team_map: dict[str, str | None] = {}
		if camp_ids and frappe.db.table_exists("CSKH Campaign"):
			for cr in frappe.db.get_all(
				"CSKH Campaign", filters={"name": ["in", camp_ids]}, fields=["name", "team_scope"]
			):
				camp_team_map[str(cr.name)] = cr.team_scope

		latest_ah_by_wi: dict[str, dict] = {}
		if frappe.db.has_column("CSKH Assignment History", "team_at_assignment"):
			c_names = [str(w.name) for w in closed_wis]
			ph_c = ", ".join(["%s"] * len(c_names))
			ah_rows = frappe.db.sql(
				f"""
				select work_item, team_at_assignment, attribution_status, ownership_start, event_at, creation, name
				from `tabCSKH Assignment History` USE INDEX (idx_cskh_ah_wi_act_start)
				where work_item in ({ph_c})
				  and action in ('assign', 'reassign', 'reopen')
				""",
				tuple(c_names),
				as_dict=True,
			)
			by_wi_ah: dict[str, list[dict]] = defaultdict(list)
			for ar in ah_rows:
				by_wi_ah[str(ar.work_item)].append(ar)
			for wn, alist in by_wi_ah.items():
				alist.sort(
					key=lambda r: (
						get_datetime(r.ownership_start or r.event_at),
						get_datetime(r.creation),
						str(r.name),
					)
				)
				latest_ah_by_wi[wn] = alist[-1]

		affiliated_wis: list[dict] = []
		for wi in closed_wis:
			w_name = str(wi.name)
			camp_team = camp_team_map.get(str(wi.get("campaign") or ""))
			if camp_team and camp_team not in allowed_nodes_set:
				wi_readable_cache[w_name] = False
				continue
			latest_ah = latest_ah_by_wi.get(w_name)
			owner_at_close = wi.get("owner_at_close")
			if (
				latest_ah
				and latest_ah.get("attribution_status") == "authoritative"
				and latest_ah.get("team_at_assignment")
			):
				affiliated = bool(latest_ah.get("team_at_assignment") in allowed_nodes_set)
			else:
				affiliated = bool(
					(owner_at_close and owner_at_close in allowed_users_set)
					or (wi.get("closed_by") and wi.get("closed_by") in allowed_users_set)
					or (wi.get("last_activity_by") and wi.get("last_activity_by") in allowed_users_set)
					or (camp_team and camp_team in allowed_nodes_set)
				)
			if not affiliated or not wi.get("lead"):
				wi_readable_cache[w_name] = False
			else:
				affiliated_wis.append(wi)

		if affiliated_wis:
			lead_Batch = sorted({str(w.lead) for w in affiliated_wis if w.get("lead")})
			scope = reporting._scope(actor.user)
			authorized_leads = set(reporting.filter_team_authorized_leads(scope, lead_Batch) or ())
			for wi in affiliated_wis:
				wi_readable_cache[str(wi.name)] = bool(str(wi.lead) in authorized_leads)
		return

	if actor.role_profile == "staff" and not _is_leader(actor.user):
		closed_staff_wis: list[dict] = []
		for wi in wi_rows:
			w_name = str(wi.name)
			status = wi.get("status")
			assigned_to = wi.get("assigned_to")
			if status in OWNED_ACTIVE_WORK_ITEM_STATES and assigned_to == actor.user:
				wi_readable_cache[w_name] = True
			elif status == "closed" and not assigned_to:
				eff_close_owner = wi.get("owner_at_close") or wi.get("closed_by")
				if (eff_close_owner == actor.user or wi.get("closed_by") == actor.user) and wi.get("lead"):
					closed_staff_wis.append(wi)
				else:
					wi_readable_cache[w_name] = False
			else:
				wi_readable_cache[w_name] = False

		if closed_staff_wis:
			s_leads = sorted({str(w.lead) for w in closed_staff_wis if w.get("lead")})
			ph_l = ", ".join(["%s"] * len(s_leads))
			active_states = tuple(OWNED_ACTIVE_WORK_ITEM_STATES)
			ph_st = ", ".join(["%s"] * len(active_states))
			other_owned_leads = set(
				frappe.db.sql(
					f"""
					select distinct lead
					from `tabCSKH Work Item`
					where lead in ({ph_l})
					  and status in ({ph_st})
					  and assigned_to != %s
					""",
					(*s_leads, *active_states, actor.user),
					pluck=True,
				)
				or ()
			)
			claimed_leads = set(
				frappe.db.sql(
					f"select distinct lead from `tabCSKH Active Claim` where lead in ({ph_l})",
					tuple(s_leads),
					pluck=True,
				)
				or ()
			)
			valid_owner_leads = set(
				frappe.db.sql(
					f"""
					select name
					from `tabCRM Lead`
					where name in ({ph_l})
					  and (lead_owner is null or lead_owner in ('', 'Administrator', %s))
					""",
					(*s_leads, actor.user),
					pluck=True,
				)
				or ()
			)
			for wi in closed_staff_wis:
				ld = str(wi.lead)
				wi_readable_cache[str(wi.name)] = (
					(ld not in other_owned_leads) and (ld not in claimed_leads) and (ld in valid_owner_leads)
				)


def _can_actor_read_drilldown_row_pii(
	raw_row: dict,
	lead_info: dict | None,
	actor: ReportActor,
	wi_readable_cache: dict[str, bool],
) -> bool:
	"""Evaluate live workflow/lead PII readability (`SEC2-01..04`, `L-23`, `S03`, `S15`, `F-P3-04` closed WI)."""
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
	team_map: dict[str, str] | None = None,
	camp_map: dict[str, str] | None = None,
) -> dict:
	"""Apply strict `S03` / `S08` / `S09` / `S15` / `F-P3-04` privacy redaction onto a drill-down event row."""
	out = dict(raw_row)
	for dt_key in ("started_at", "original_due_at", "effective_due_at", "fulfilled_at", "occurred_at"):
		if dt_key in out:
			out[dt_key] = format_calendar_iso(out[dt_key]) if out[dt_key] else None
	lead_id = out.pop("_lead_id", None)
	raw_note = out.pop("_note_excerpt", None)
	tid = out.get("team_id")
	cid = out.get("campaign")
	out["team_name"] = (team_map or {}).get(str(tid)) if tid else None
	out["campaign_name"] = (camp_map or {}).get(str(cid)) if cid else None

	if actor.role_profile == "bod" or not emit_pii:
		out["lead"] = "REDACTED"
		out["customer_name"] = None
		out["phone"] = None
		out["note_excerpt"] = None
		out["pii_redacted"] = True
		out["pii_redacted_due_to_transfer"] = False
		return out

	if not _can_actor_read_drilldown_row_pii(
		raw_row, lead_display_map.get(lead_id or ""), actor, wi_readable_cache
	):
		out["lead"] = None
		out["customer_name"] = None
		out["phone"] = None
		out["note_excerpt"] = None
		out["pii_redacted"] = True
		out["pii_redacted_due_to_transfer"] = True
		return out

	info = lead_display_map.get(lead_id or "")
	if not info:
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


def _drilldown_row_matches_search(row: dict, q_lower: str) -> bool:
	fields = [
		row.get("record_id"),
		row.get("work_item"),
		row.get("campaign"),
		row.get("campaign_name"),
		row.get("owner_user"),
		row.get("team_id"),
		row.get("team_name"),
		row.get("status"),
		row.get("outcome_code"),
	]
	if not row.get("pii_redacted"):
		fields.extend([row.get("lead"), row.get("customer_name"), row.get("phone")])
	return any(f and q_lower in str(f).lower() for f in fields)


def _sort_drilldown_rows(rows: list[dict], sort_by: str | None, sort_dir: str | None) -> list[dict]:
	if not sort_by:
		return rows
	s_key = sort_by.strip().lower()
	if s_key not in ALLOWED_DRILLDOWN_SORT_FIELDS:
		raise InvalidRequest(
			f"Tham số sort_by drill-down không hợp lệ: '{s_key}'. Chỉ chấp nhận: {', '.join(sorted(ALLOWED_DRILLDOWN_SORT_FIELDS))}."
		)
	direction = (sort_dir or "asc").strip().lower()
	if direction not in ("asc", "desc"):
		raise InvalidRequest("Tham số sort_dir phải là 'asc' hoặc 'desc'.")

	with_val = []
	nulls = []
	for r in rows:
		v = r.get(s_key)
		if v is None or v == "":
			nulls.append(r)
		else:
			with_val.append((str(v), str(r.get("record_id") or ""), r))
	if direction == "desc":
		with_val.sort(key=lambda x: x[0], reverse=True)
	else:
		with_val.sort(key=lambda x: x[0])
	return [x[2] for x in with_val] + nulls


_FAST_INTERVAL_METRICS = frozenset(("CONNECTED_RATE", "ASSIGNED_INTERVALS", "ELIGIBLE_COVERAGE"))
_FAST_SLA_METRICS = frozenset(
	(
		"FIRST_ATTEMPT_SLA_RATE",
		"FOLLOWUP_SLA_RATE",
		"CALLBACK_COMPLETION_RATE",
		"FIRST_CONNECTED_TRACKING_RATE",
		"MIN_ATTEMPTS_COMPLIANCE_RATE",
		"SLA_BREACH_COUNT",
		"SLA_WAIVER_COUNT",
		"OPEN_FOLLOWUPS_OVERDUE",
	)
)
_FAST_ATTEMPT_METRICS = frozenset(
	(
		"CONTACT_ATTEMPT_COUNT",
		"CONNECTED_ATTEMPT_COUNT",
		"VERIFICATION_RATE",
		"VALID_OUTCOME_LOGGING_RATE",
		"VOID_ATTEMPT_RATE",
		"CORRECTION_RATE",
	)
)
_FAST_SQL_DRILLDOWN_METRICS = _FAST_INTERVAL_METRICS | _FAST_SLA_METRICS | _FAST_ATTEMPT_METRICS


def _resolve_drilldown_team_scope_mode(
	*,
	stype: str,
	sid: str,
	team_filter_arg: str | None,
	leader_allowed_nodes: set[str] | None,
) -> tuple[str, tuple[str, ...]]:
	"""Return `('none'|'unattributed'|'empty'|'authoritative_in', sorted_team_nodes)` matching `F-P3-04`."""
	if stype == "team":
		if sid == UNATTRIBUTED_TEAM_ID:
			return "unattributed", ()
		nodes = set(_subtree_nodes(sid) or {sid})
		if leader_allowed_nodes is not None:
			nodes &= leader_allowed_nodes
		if not nodes:
			return "empty", ()
		return "authoritative_in", tuple(sorted(nodes))

	nodes_opt: set[str] | None = None
	if leader_allowed_nodes is not None:
		nodes_opt = set(leader_allowed_nodes)
	if team_filter_arg:
		tf_nodes = set(_subtree_nodes(team_filter_arg) or {team_filter_arg})
		nodes_opt = tf_nodes if nodes_opt is None else (nodes_opt & tf_nodes)
	if nodes_opt is None:
		return "none", ()
	if not nodes_opt:
		return "empty", ()
	return "authoritative_in", tuple(sorted(nodes_opt))


def _resolve_fast_total(
	*,
	mcode: str,
	bucket: str,
	snap_cell: dict | None,
	dirty_pending: bool,
	count_fn,
) -> int:
	"""Use published `snap_cell` count in `O(1)` when fresh & exact, else run exact SQL `COUNT(*)`."""
	if not dirty_pending and snap_cell is not None and bucket != "excluded" and mcode != "SLA_BREACH_COUNT":
		if mcode in (
			"CONNECTED_RATE",
			"ELIGIBLE_COVERAGE",
			"FIRST_ATTEMPT_SLA_RATE",
			"FOLLOWUP_SLA_RATE",
			"CALLBACK_COMPLETION_RATE",
			"FIRST_CONNECTED_TRACKING_RATE",
			"MIN_ATTEMPTS_COMPLIANCE_RATE",
			"VALID_OUTCOME_LOGGING_RATE",
			"VERIFICATION_RATE",
		):
			den = int(snap_cell.get("denominator") or 0)
			num = int(snap_cell.get("numerator") or 0)
			if bucket == "all_denominator":
				return den
			if bucket == "numerator_only":
				return num
			if bucket == "breached_only":
				return max(0, den - num)
		elif mcode in ("VOID_ATTEMPT_RATE", "CORRECTION_RATE"):
			den = int(snap_cell.get("denominator") or 0)
			num = int(snap_cell.get("numerator") or 0)
			if bucket == "all_denominator":
				return den
			return num
		elif mcode in (
			"ASSIGNED_INTERVALS",
			"CONTACT_ATTEMPT_COUNT",
			"CONNECTED_ATTEMPT_COUNT",
			"SLA_WAIVER_COUNT",
			"OPEN_FOLLOWUPS_OVERDUE",
		):
			if bucket in ("all_denominator", "numerator_only") and snap_cell.get("value") is not None:
				return int(snap_cell.get("value") or 0)
	return int(count_fn())


def _fast_interval_cohort_drilldown_page(
	*,
	mcode: str,
	period: PeriodSpec,
	eff_as_of,
	eff_camp: str,
	eff_emp: str,
	team_mode: str,
	team_nodes: tuple[str, ...],
	bucket: str,
	offset: int,
	limit: int,
	snap_cell: dict | None,
	snap_metrics_all: dict | None,
	dirty_pending: bool,
) -> tuple[list[dict], int] | None:
	"""Exact SQL-paginated interval-cohort query (`CONNECTED_RATE`, `ASSIGNED_INTERVALS`, `ELIGIBLE_COVERAGE`)."""
	if mcode == "ASSIGNED_INTERVALS" and bucket in ("breached_only", "excluded"):
		return [], 0
	if not frappe.db.has_column("CSKH Assignment History", "team_at_assignment"):
		return None

	start_dt = period.start_dt
	end_dt_excl = period.end_dt_exclusive

	# Index-prefix guard (`<0.05ms` via `idx_cskh_ah_act_interval` & `idx_cskh_ah_team_start`):
	# fall back cleanly if legacy rows have `ownership_start IS NULL` or unbackfilled `attribution_status`.
	if frappe.db.sql(
		"""
		select 1 from `tabCSKH Assignment History` USE INDEX (idx_cskh_ah_act_interval)
		where action in ('assign', 'reassign', 'reopen') and ownership_start is null
		limit 1
		"""
	):
		return None
	if frappe.db.sql(
		"""
		select 1 from `tabCSKH Assignment History` USE INDEX (idx_cskh_ah_team_start)
		where (team_at_assignment is null or team_at_assignment = '')
		  and ownership_start >= %s and ownership_start < %s
		  and action in ('assign', 'reassign', 'reopen')
		  and (attribution_status is null or attribution_status = '')
		limit 1
		""",
		(start_dt, end_dt_excl),
	):
		return None

	if eff_emp:
		ah_idx = "FORCE INDEX (idx_cskh_ah_user_interval)"
	elif team_mode == "authoritative_in" and len(team_nodes) == 1:
		ah_idx = "FORCE INDEX (idx_cskh_ah_team_start)"
	else:
		ah_idx = "FORCE INDEX (idx_cskh_ah_start_name)"

	where_parts = [
		"ah.ownership_start >= %s",
		"ah.ownership_start < %s",
		"ah.ownership_start <= %s",
		"ah.action in ('assign', 'reassign', 'reopen')",
	]
	base_params: list = [start_dt, end_dt_excl, eff_as_of]
	if eff_camp:
		where_parts.append("wi.campaign = %s")
		base_params.append(eff_camp)
	if eff_emp:
		where_parts.append("ah.to_user = %s")
		base_params.append(eff_emp)
	if team_mode == "unattributed":
		where_parts.append(
			"not (ah.team_at_assignment is not null and ah.team_at_assignment != '' and ah.attribution_status = %s)"
		)
		base_params.append(AUTHORITATIVE)
	elif team_mode == "authoritative_in":
		if len(team_nodes) == 1:
			where_parts.append("ah.team_at_assignment = %s and ah.attribution_status = %s")
			base_params.extend([team_nodes[0], AUTHORITATIVE])
		else:
			ph_t = ", ".join(["%s"] * len(team_nodes))
			where_parts.append(f"ah.team_at_assignment in ({ph_t}) and ah.attribution_status = %s")
			base_params.extend([*team_nodes, AUTHORITATIVE])

	# Scalar `coalesce((select 1 ... FORCE INDEX ... limit 1), 0)` prevents MariaDB 11.x from flattening
	# correlated `idx_cskh_attempt_ownevt_occ` probes (3 rows/interval) into a 250k-row semi-join `<subquery>` scan.
	ca_att_flag_sql = """
		coalesce((
			select 1 from `tabCSKH Contact Attempt` ca FORCE INDEX (idx_cskh_attempt_ownevt_occ)
			where ca.ownership_event = ah.name
			  and coalesce(ca.contact_attempt_snapshot, 0) = 1
			  and ca.recorded_at <= %s
			  and ca.occurred_at < %s
			  and ca.occurred_at <= %s
			limit 1
		), 0)
	"""
	ca_att_params = (eff_as_of, end_dt_excl, eff_as_of)

	ca_conn_flag_sql = """
		coalesce((
			select 1 from `tabCSKH Contact Attempt` ca_c FORCE INDEX (idx_cskh_attempt_ownevt_occ)
			where ca_c.ownership_event = ah.name
			  and coalesce(ca_c.contact_attempt_snapshot, 0) = 1
			  and ca_c.recorded_at <= %s
			  and ca_c.occurred_at < %s
			  and ca_c.occurred_at <= %s
			  and coalesce(ca_c.connected_snapshot, 0) = 1
			limit 1
		), 0)
	"""
	ca_conn_params = (eff_as_of, end_dt_excl, eff_as_of)

	extra_sel_sql = ""
	extra_sel_params: list = []

	if mcode == "ASSIGNED_INTERVALS":
		pass
	elif mcode == "CONNECTED_RATE":
		invalid_phone_codes: set[str] = {"wrong_number"}
		if frappe.db.table_exists("CSKH Outcome Code") and frappe.db.has_column(
			"CSKH Outcome Code", "is_invalid_phone"
		):
			for oc in frappe.db.get_all(
				"CSKH Outcome Code", filters={"is_invalid_phone": 1}, fields=["name", "code"]
			):
				invalid_phone_codes.add(str(oc.code or oc.name).lower())
		inv_codes_sorted = sorted(invalid_phone_codes)
		inv_ph = ", ".join(["%s"] * len(inv_codes_sorted)) if inv_codes_sorted else "''"
		has_inv_col = frappe.db.has_column("CSKH Contact Attempt", "invalid_phone_snapshot")
		inv_expr = (
			f"(coalesce(ca_i.invalid_phone_snapshot, 0) = 1 or lower(coalesce(ca_i.outcome_code, '')) in ({inv_ph}))"
			if has_inv_col
			else f"(lower(coalesce(ca_i.outcome_code, '')) in ({inv_ph}))"
		)
		ca_inv_flag_sql = f"""
			coalesce((
				select 1 from `tabCSKH Contact Attempt` ca_i FORCE INDEX (idx_cskh_attempt_ownevt_occ)
				where ca_i.ownership_event = ah.name
				  and coalesce(ca_i.contact_attempt_snapshot, 0) = 1
				  and ca_i.recorded_at <= %s
				  and ca_i.occurred_at < %s
				  and ca_i.occurred_at <= %s
				  and {inv_expr}
				limit 1
			), 0)
		"""
		ca_inv_params = (eff_as_of, end_dt_excl, eff_as_of, *inv_codes_sorted)

		if bucket == "all_denominator":
			where_parts.append(f"({ca_att_flag_sql} = 1) and ({ca_inv_flag_sql} = 0)")
			base_params.extend([*ca_att_params, *ca_inv_params])
			extra_sel_sql = f", ({ca_conn_flag_sql}) as is_conn_flag"
			extra_sel_params.extend(ca_conn_params)
		elif bucket == "numerator_only":
			where_parts.append(f"({ca_conn_flag_sql} = 1) and ({ca_inv_flag_sql} = 0)")
			base_params.extend([*ca_conn_params, *ca_inv_params])
		elif bucket == "breached_only":
			where_parts.append(
				f"({ca_att_flag_sql} = 1) and ({ca_inv_flag_sql} = 0) and ({ca_conn_flag_sql} = 0)"
			)
			base_params.extend([*ca_att_params, *ca_inv_params, *ca_conn_params])
		else:  # excluded
			where_parts.append(f"(({ca_att_flag_sql} = 0) or ({ca_inv_flag_sql} = 1))")
			base_params.extend([*ca_att_params, *ca_inv_params])
			extra_sel_sql = f", ({ca_inv_flag_sql}) as has_inv_flag"
			extra_sel_params.extend(ca_inv_params)
	else:  # ELIGIBLE_COVERAGE
		if not frappe.db.table_exists("CSKH SLA Obligation"):
			return None
		fa_canc_flag_sql = """
			coalesce((
				select 1 from `tabCSKH SLA Obligation` fa FORCE INDEX (idx_cskh_sla_ivevt_type)
				where fa.interval_event = ah.name
				  and fa.obligation_type = 'FIRST_ATTEMPT'
				  and fa.started_at <= %s
				  and fa.status = 'canceled_validly'
				  and (fa.closed_at is null or fa.closed_at <= %s)
				limit 1
			), 0)
		"""
		fa_canc_params = (eff_as_of, eff_as_of)

		# Fast check if 100% of assigned intervals in published fresh snapshot are already eligible
		all_assigned_eligible = False
		if not dirty_pending and snap_metrics_all and snap_cell is not None:
			asg_val = (snap_metrics_all.get("ASSIGNED_INTERVALS") or {}).get("value")
			cov_den = snap_cell.get("denominator")
			if asg_val is not None and cov_den is not None and int(asg_val) == int(cov_den):
				all_assigned_eligible = True

		if not all_assigned_eligible:
			# Verify zero intervals require calendar-based `min_exposure_business_minutes` (`not has_att and not fa_canceled and not fa_matured`)
			fa_matured_strict_flag_sql = """
				coalesce((
					select 1 from `tabCSKH SLA Obligation` fa_m FORCE INDEX (idx_cskh_sla_ivevt_type)
					where fa_m.interval_event = ah.name
					  and fa_m.obligation_type = 'FIRST_ATTEMPT'
					  and fa_m.started_at <= %s
					  and fa_m.status != 'superseded'
					  and (
					        (fa_m.status = 'waived' and (fa_m.waived_at is null or fa_m.waived_at <= %s))
					     or (
					          fa_m.effective_due_at <= %s
					          and not (coalesce(fa_m.is_paused, 0) = 1 and fa_m.status = 'open' and (fa_m.paused_at is null or fa_m.paused_at <= %s))
					          and (fa_m.fulfilled_at is null or fa_m.fulfilled_at > %s)
					          and coalesce((
					              select 1 from `tabCSKH Contact Attempt` ca_fa FORCE INDEX (idx_cskh_attempt_ownevt_occ)
					              where ca_fa.ownership_event = ah.name
					                and coalesce(ca_fa.contact_attempt_snapshot, 0) = 1
					                and ca_fa.recorded_at <= %s
					                and ca_fa.occurred_at <= %s
					                and (ah.ownership_end is null or ca_fa.occurred_at <= ah.ownership_end)
					              limit 1
					          ), 0) = 0
					        )
					      )
					limit 1
				), 0)
			"""
			fa_mat_params = (eff_as_of,) * 7
			ambig_where = " and ".join(
				[
					*where_parts,
					f"({ca_att_flag_sql} = 0)",
					f"({fa_canc_flag_sql} = 0)",
					f"({fa_matured_strict_flag_sql} = 0)",
				]
			)
			if frappe.db.sql(
				f"""
				select 1
				from `tabCSKH Assignment History` ah {ah_idx}
				inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
				where {ambig_where}
				limit 1
				""",
				tuple([*base_params, *ca_att_params, *fa_canc_params, *fa_mat_params]),
			):
				return None

		if bucket == "all_denominator":
			if not all_assigned_eligible:
				where_parts.append(f"({fa_canc_flag_sql} = 0)")
				base_params.extend(fa_canc_params)
			extra_sel_sql = f", ({ca_att_flag_sql}) as is_touched_flag"
			extra_sel_params.extend(ca_att_params)
		elif bucket == "numerator_only":
			if not all_assigned_eligible:
				where_parts.append(f"({fa_canc_flag_sql} = 0)")
				base_params.extend(fa_canc_params)
			where_parts.append(f"({ca_att_flag_sql} = 1)")
			base_params.extend(ca_att_params)
		elif bucket == "breached_only":
			if not all_assigned_eligible:
				where_parts.append(f"({fa_canc_flag_sql} = 0)")
				base_params.extend(fa_canc_params)
			where_parts.append(f"({ca_att_flag_sql} = 0)")
			base_params.extend(ca_att_params)
		else:  # excluded
			if all_assigned_eligible:
				return [], 0
			where_parts.append(f"({fa_canc_flag_sql} = 1)")
			base_params.extend(fa_canc_params)

	where_clause = " and ".join(where_parts)

	def _count_intervals() -> int:
		return int(
			frappe.db.sql(
				f"""
				select count(*)
				from `tabCSKH Assignment History` ah {ah_idx}
				inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
				where {where_clause}
				""",
				tuple(base_params),
			)[0][0]
			or 0
		)

	total = _resolve_fast_total(
		mcode=mcode,
		bucket=bucket,
		snap_cell=snap_cell,
		dirty_pending=dirty_pending,
		count_fn=_count_intervals,
	)
	if total == 0 or offset >= total:
		return [], total

	page_rows = frappe.db.sql(
		f"""
		select ah.name, ah.work_item, ah.to_user, ah.event_at,
		       ah.ownership_start, ah.ownership_end,
		       ah.team_at_assignment, ah.attribution_status,
		       wi.campaign, wi.lead{extra_sel_sql}
		from `tabCSKH Assignment History` ah {ah_idx}
		inner join `tabCSKH Work Item` wi on wi.name = ah.work_item
		where {where_clause}
		order by ah.ownership_start asc, ah.name asc
		limit %s offset %s
		""",
		tuple([*extra_sel_params, *base_params, limit, offset]),
		as_dict=True,
	)
	if any(not r.get("attribution_status") for r in page_rows):
		return None

	out_events: list[dict] = []
	for iv in page_rows:
		iv["resolved_team"] = iv.team_at_assignment
		if mcode == "ASSIGNED_INTERVALS":
			out_events.append(_iv_event_dict(iv, in_numerator=True, status_label="assigned"))
		elif mcode == "CONNECTED_RATE":
			if bucket == "excluded":
				has_inv = bool(cint(iv.get("has_inv_flag")))
				out_events.append(
					_iv_event_dict(
						iv,
						in_numerator=False,
						status_label="invalid_phone" if has_inv else "untried",
					)
				)
			else:
				is_conn = (
					True
					if bucket == "numerator_only"
					else (False if bucket == "breached_only" else bool(cint(iv.get("is_conn_flag"))))
				)
				out_events.append(
					_iv_event_dict(
						iv,
						in_numerator=is_conn,
						status_label="connected" if is_conn else "not_connected",
					)
				)
		else:  # ELIGIBLE_COVERAGE
			if bucket == "excluded":
				out_events.append(_iv_event_dict(iv, in_numerator=False, status_label="excluded_coverage"))
			else:
				is_touched = (
					True
					if bucket == "numerator_only"
					else (False if bucket == "breached_only" else bool(cint(iv.get("is_touched_flag"))))
				)
				out_events.append(
					_iv_event_dict(
						iv,
						in_numerator=is_touched,
						status_label="touched" if is_touched else "untouched",
					)
				)
	return out_events, total


def _fast_sla_obligation_drilldown_page(
	*,
	mcode: str,
	period: PeriodSpec,
	eff_as_of,
	eff_camp: str,
	eff_emp: str,
	team_mode: str,
	team_nodes: tuple[str, ...],
	bucket: str,
	offset: int,
	limit: int,
	snap_cell: dict | None,
	dirty_pending: bool,
) -> tuple[list[dict], int] | None:
	"""Exact SQL-paginated SLA deadline-cohort & backlog query (`F-P3-02` `as_of_status` short-circuit)."""
	if mcode in ("SLA_BREACH_COUNT", "SLA_WAIVER_COUNT", "OPEN_FOLLOWUPS_OVERDUE") and bucket in (
		"breached_only",
		"excluded",
	):
		return [], 0
	if not frappe.db.table_exists("CSKH SLA Obligation"):
		return [], 0

	start_dt = period.start_dt
	end_dt_excl = period.end_dt_exclusive

	# Index-prefix guard (`<0.05ms` via `idx_cskh_sla_team_due`): fall back if any obligation lacks `team_at_obligation`
	due_upper = eff_as_of if mcode == "OPEN_FOLLOWUPS_OVERDUE" else end_dt_excl
	due_lower_sql = "" if mcode == "OPEN_FOLLOWUPS_OVERDUE" else "and effective_due_at >= %s "
	due_guard_params = (
		(due_upper, UNKNOWN_HISTORICAL)
		if mcode == "OPEN_FOLLOWUPS_OVERDUE"
		else (start_dt, due_upper, UNKNOWN_HISTORICAL)
	)
	if frappe.db.sql(
		f"""
		select 1 from `tabCSKH SLA Obligation` USE INDEX (idx_cskh_sla_team_due)
		where (team_at_obligation is null or team_at_obligation = '')
		  {due_lower_sql}and effective_due_at < %s
		  and coalesce(attribution_status, '') != %s
		limit 1
		""",
		due_guard_params,
	):
		return None

	if mcode == "FIRST_CONNECTED_TRACKING_RATE":
		if frappe.db.sql(
			"""
			select 1
			from `tabCSKH SLA Obligation` ob USE INDEX (idx_cskh_sla_type_due_name)
			inner join `tabCSKH Assignment History` ah_iv on ah_iv.name = ob.interval_event
			where ob.obligation_type = 'FIRST_CONNECTED'
			  and ob.effective_due_at >= %s and ob.effective_due_at < %s
			  and ob.owner_user != ah_iv.to_user
			limit 1
			""",
			(start_dt, end_dt_excl),
		):
			return None

	ob_type_map = {
		"FIRST_ATTEMPT_SLA_RATE": "FIRST_ATTEMPT",
		"FOLLOWUP_SLA_RATE": "FOLLOWUP",
		"CALLBACK_COMPLETION_RATE": "FOLLOWUP",
		"FIRST_CONNECTED_TRACKING_RATE": "FIRST_CONNECTED",
		"MIN_ATTEMPTS_COMPLIANCE_RATE": "MIN_ATTEMPTS",
		"OPEN_FOLLOWUPS_OVERDUE": "FOLLOWUP",
	}
	single_ob_type = ob_type_map.get(mcode)

	if eff_emp:
		ob_idx = "USE INDEX (idx_cskh_sla_owner_due)"
	elif single_ob_type:
		ob_idx = "USE INDEX (idx_cskh_sla_type_due_name)"
	elif team_mode == "authoritative_in" and len(team_nodes) == 1:
		ob_idx = "USE INDEX (idx_cskh_sla_team_due)"
	elif eff_camp and team_mode == "none":
		ob_idx = "USE INDEX (idx_cskh_sla_camp_due)"
	else:
		ob_idx = "USE INDEX (idx_cskh_sla_due_name)"

	where_parts = ["ob.started_at <= %s", "ob.status != 'superseded'"]
	base_params: list = [eff_as_of]

	if single_ob_type:
		where_parts.append("ob.obligation_type = %s")
		base_params.append(single_ob_type)
	elif mcode == "SLA_BREACH_COUNT":
		where_parts.append("ob.obligation_type in ('FIRST_ATTEMPT', 'FOLLOWUP')")

	if mcode == "OPEN_FOLLOWUPS_OVERDUE":
		where_parts.append("coalesce(ob.informational_only, 0) = 0")
		where_parts.append("ob.effective_due_at < %s")
		base_params.append(eff_as_of)
	else:
		where_parts.append("ob.effective_due_at >= %s and ob.effective_due_at < %s")
		base_params.extend([start_dt, end_dt_excl])

	if eff_camp:
		where_parts.append("ob.campaign = %s")
		base_params.append(eff_camp)
	if eff_emp:
		where_parts.append("ob.owner_user = %s")
		base_params.append(eff_emp)

	if team_mode == "unattributed":
		where_parts.append(
			"not (ob.team_at_obligation is not null and ob.team_at_obligation != '' and coalesce(nullif(ob.attribution_status, ''), %s) = %s)"
		)
		base_params.extend([AUTHORITATIVE, AUTHORITATIVE])
	elif team_mode == "authoritative_in":
		if len(team_nodes) == 1:
			where_parts.append(
				"ob.team_at_obligation = %s and coalesce(nullif(ob.attribution_status, ''), %s) = %s"
			)
			base_params.extend([team_nodes[0], AUTHORITATIVE, AUTHORITATIVE])
		else:
			ph_t = ", ".join(["%s"] * len(team_nodes))
			where_parts.append(
				f"ob.team_at_obligation in ({ph_t}) and coalesce(nullif(ob.attribution_status, ''), %s) = %s"
			)
			base_params.extend([*team_nodes, AUTHORITATIVE, AUTHORITATIVE])

	is_canc_sql = "(ob.status = 'canceled_validly' and (ob.closed_at is null or ob.closed_at <= %s))"
	is_waiv_sql = "(ob.status = 'waived' and (ob.waived_at is null or ob.waived_at <= %s))"
	is_paused_sql = "(coalesce(ob.is_paused, 0) = 1 and ob.status = 'open' and (ob.paused_at is null or ob.paused_at <= %s))"
	is_handoff_sql = (
		"((ah_iv.ownership_end is not null and ah_iv.ownership_end <= %s) "
		"or (ob.status = 'breached_handoff' and ob.closed_at is not null and ob.closed_at <= %s))"
	)
	f_dt_sql = """
		coalesce(
			case when ob.obligation_type = 'FIRST_ATTEMPT' and ob.interval_event is not null and ob.interval_event != '' then (
				select min(ca.occurred_at)
				from `tabCSKH Contact Attempt` ca USE INDEX (idx_cskh_attempt_ownevt_occ)
				where ca.ownership_event = ob.interval_event
				  and coalesce(ca.contact_attempt_snapshot, 0) = 1
				  and ca.recorded_at <= %s
				  and ca.occurred_at <= %s
				  and (ah_iv.ownership_end is null or ca.occurred_at <= ah_iv.ownership_end)
			) else null end,
			case when ob.fulfilled_by_attempt is not null and ob.fulfilled_by_attempt != '' then (
				select ca2.occurred_at
				from `tabCSKH Contact Attempt` ca2
				where ca2.name = ob.fulfilled_by_attempt
				  and ca2.recorded_at <= %s
				  and ca2.occurred_at <= %s
			) else (
				case when ob.fulfilled_at is not null and ob.fulfilled_at <= %s then ob.fulfilled_at else null end
			) end
		)
	"""
	f_dt_params = (eff_as_of,) * 5

	if mcode in ("FIRST_ATTEMPT_SLA_RATE", "FOLLOWUP_SLA_RATE", "MIN_ATTEMPTS_COMPLIANCE_RATE"):
		if bucket == "all_denominator":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
				f"and (((ob.effective_due_at <= %s) and (not {is_paused_sql})) or (({f_dt_sql}) is not null))"
			)
			base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params])
		elif bucket == "numerator_only":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) and (({f_dt_sql}) <= ob.effective_due_at)"
			)
			base_params.extend([eff_as_of, eff_as_of, *f_dt_params])
		elif bucket == "breached_only":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
				f"and (((ob.effective_due_at <= %s) and (not {is_paused_sql})) or (({f_dt_sql}) is not null)) "
				f"and (coalesce(({f_dt_sql}), '9999-12-31 23:59:59') > ob.effective_due_at)"
			)
			base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params, *f_dt_params])
		else:  # excluded
			where_parts.append(
				f"({is_canc_sql} or {is_waiv_sql} "
				f"or (((ob.effective_due_at > %s) or {is_paused_sql}) and (({f_dt_sql}) is null)))"
			)
			base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params])
	elif mcode == "CALLBACK_COMPLETION_RATE":
		if bucket == "all_denominator":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
				f"and (((ob.effective_due_at <= %s) and (not {is_paused_sql})) or (({f_dt_sql}) is not null))"
			)
			base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params])
		elif bucket == "numerator_only":
			where_parts.append(f"(not {is_canc_sql}) and (not {is_waiv_sql}) and (({f_dt_sql}) is not null)")
			base_params.extend([eff_as_of, eff_as_of, *f_dt_params])
		elif bucket == "breached_only":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
				f"and (ob.effective_due_at <= %s) and (not {is_paused_sql}) and (({f_dt_sql}) is null)"
			)
			base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params])
		else:  # excluded
			where_parts.append(
				f"({is_canc_sql} or {is_waiv_sql} "
				f"or (((ob.effective_due_at > %s) or {is_paused_sql}) and (({f_dt_sql}) is null)))"
			)
			base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params])
	elif mcode == "FIRST_CONNECTED_TRACKING_RATE":
		if bucket == "all_denominator":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
				f"and ((({f_dt_sql}) is not null) or ((ob.effective_due_at <= %s) and (not {is_paused_sql}) and (not {is_handoff_sql})))"
			)
			base_params.extend(
				[eff_as_of, eff_as_of, *f_dt_params, eff_as_of, eff_as_of, eff_as_of, eff_as_of]
			)
		elif bucket == "numerator_only":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) and (({f_dt_sql}) <= ob.effective_due_at)"
			)
			base_params.extend([eff_as_of, eff_as_of, *f_dt_params])
		elif bucket == "breached_only":
			where_parts.append(
				f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
				f"and ((({f_dt_sql}) is not null) or ((ob.effective_due_at <= %s) and (not {is_paused_sql}) and (not {is_handoff_sql}))) "
				f"and (coalesce(({f_dt_sql}), '9999-12-31 23:59:59') > ob.effective_due_at)"
			)
			base_params.extend(
				[
					eff_as_of,
					eff_as_of,
					*f_dt_params,
					eff_as_of,
					eff_as_of,
					eff_as_of,
					eff_as_of,
					*f_dt_params,
				]
			)
		else:  # excluded
			where_parts.append(
				f"({is_canc_sql} or {is_waiv_sql} "
				f"or ((({f_dt_sql}) is null) and ((ob.effective_due_at > %s) or {is_paused_sql} or {is_handoff_sql})))"
			)
			base_params.extend(
				[eff_as_of, eff_as_of, *f_dt_params, eff_as_of, eff_as_of, eff_as_of, eff_as_of]
			)
	elif mcode == "SLA_BREACH_COUNT":
		where_parts.append(
			f"(not {is_canc_sql}) and (not {is_waiv_sql}) "
			f"and (((ob.effective_due_at <= %s) and (not {is_paused_sql})) or (({f_dt_sql}) is not null)) "
			f"and (coalesce(({f_dt_sql}), '9999-12-31 23:59:59') > ob.effective_due_at)"
		)
		base_params.extend([eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params, *f_dt_params])
	elif mcode == "SLA_WAIVER_COUNT":
		where_parts.append(is_waiv_sql)
		base_params.append(eff_as_of)
	else:  # OPEN_FOLLOWUPS_OVERDUE
		where_parts.append(
			"(case when ah_iv.name is not null "
			"then (coalesce(ah_iv.ownership_start, ah_iv.event_at) <= %s and (ah_iv.ownership_end is null or ah_iv.ownership_end > %s)) "
			"else (ob.status in ('open', 'breached_open')) end)"
		)
		where_parts.append(
			f"(not {is_canc_sql}) and (not {is_waiv_sql}) and (not {is_paused_sql}) "
			f"and (not {is_handoff_sql}) and (({f_dt_sql}) is null)"
		)
		base_params.extend(
			[eff_as_of, eff_as_of, eff_as_of, eff_as_of, eff_as_of, eff_as_of, eff_as_of, *f_dt_params]
		)

	where_clause = " and ".join(where_parts)

	def _count_obligations() -> int:
		return int(
			frappe.db.sql(
				f"""
				select count(*)
				from `tabCSKH SLA Obligation` ob {ob_idx}
				left join `tabCSKH Assignment History` ah_iv on ah_iv.name = ob.interval_event
				where {where_clause}
				""",
				tuple(base_params),
			)[0][0]
			or 0
		)

	total = _resolve_fast_total(
		mcode=mcode,
		bucket=bucket,
		snap_cell=snap_cell,
		dirty_pending=dirty_pending,
		count_fn=_count_obligations,
	)
	if total == 0 or offset >= total:
		return [], total

	page_rows = frappe.db.sql(
		f"""
		select ob.name, ob.obligation_type, ob.work_item, ob.lead, ob.campaign, ob.interval_event,
		       ob.owner_user, ob.team_at_obligation, ob.attribution_status, ob.status,
		       ob.started_at, ob.original_due_at, ob.effective_due_at, ob.is_paused, ob.paused_at,
		       ob.fulfilled_at, ob.closed_at, ob.waived_at,
		       ah_iv.ownership_end as iv_end_dt,
		       ({f_dt_sql}) as f_dt_val
		from `tabCSKH SLA Obligation` ob {ob_idx}
		left join `tabCSKH Assignment History` ah_iv on ah_iv.name = ob.interval_event
		where {where_clause}
		order by ob.effective_due_at asc, ob.name asc
		limit %s offset %s
		""",
		tuple([*f_dt_params, *base_params, limit, offset]),
		as_dict=True,
	)

	num_statuses = (
		frozenset(("fulfilled_on_time", "fulfilled_late"))
		if mcode == "CALLBACK_COMPLETION_RATE"
		else frozenset(("fulfilled_on_time",))
	)
	count_mode = mcode in ("SLA_BREACH_COUNT", "SLA_WAIVER_COUNT", "OPEN_FOLLOWUPS_OVERDUE")

	out_events: list[dict] = []
	for ob in page_rows:
		eff_due = get_datetime(ob.effective_due_at)
		iv_end = get_datetime(ob.iv_end_dt) if ob.iv_end_dt else None
		f_dt = get_datetime(ob.f_dt_val) if ob.f_dt_val else None

		if ob.status == "canceled_validly" and (not ob.closed_at or get_datetime(ob.closed_at) <= eff_as_of):
			as_of_status = "canceled_validly"
		elif ob.status == "waived" and (not ob.waived_at or get_datetime(ob.waived_at) <= eff_as_of):
			as_of_status = "waived"
		elif f_dt is not None:
			as_of_status = "fulfilled_on_time" if f_dt <= eff_due else "fulfilled_late"
		elif (
			cint(ob.is_paused)
			and ob.status == "open"
			and (not ob.paused_at or get_datetime(ob.paused_at) <= eff_as_of)
		):
			as_of_status = "open"
		elif eff_due <= eff_as_of:
			closed_before = bool(
				(iv_end and iv_end <= eff_as_of)
				or (
					ob.status == "breached_handoff"
					and ob.closed_at
					and get_datetime(ob.closed_at) <= eff_as_of
				)
			)
			as_of_status = "breached_handoff" if closed_before else "breached_open"
		else:
			as_of_status = "open"

		ob["as_of_status"] = as_of_status
		ob["resolved_team"] = ob.team_at_obligation
		in_num = True if count_mode else (False if bucket == "excluded" else (as_of_status in num_statuses))
		out_events.append(_ob_event_dict(ob, in_numerator=in_num))
	return out_events, total


def _fast_attempt_grain_drilldown_page(
	*,
	mcode: str,
	period: PeriodSpec,
	eff_as_of,
	eff_camp: str,
	eff_emp: str,
	team_mode: str,
	team_nodes: tuple[str, ...],
	bucket: str,
	offset: int,
	limit: int,
	snap_cell: dict | None,
	dirty_pending: bool,
) -> tuple[list[dict], int] | None:
	"""Exact SQL-paginated attempt-grain query when `total_corrs == 0` (`CONTACT_ATTEMPT_COUNT`, etc.)."""
	if mcode in ("CONTACT_ATTEMPT_COUNT", "CONNECTED_ATTEMPT_COUNT") and bucket in (
		"breached_only",
		"excluded",
	):
		return [], 0
	if mcode in ("VERIFICATION_RATE", "VALID_OUTCOME_LOGGING_RATE") and bucket == "excluded":
		return [], 0
	if mcode in ("VOID_ATTEMPT_RATE", "CORRECTION_RATE") and bucket in (
		"numerator_only",
		"breached_only",
		"excluded",
	):
		return [], 0
	if not frappe.db.has_column("CSKH Assignment History", "team_at_assignment"):
		return None

	start_dt = period.start_dt
	end_dt_excl = period.end_dt_exclusive

	# Index-prefix guards (`<0.05ms` via `idx_cskh_attempt_ownevt_occ` & `cskh_attempt_perf_occ_idx`)
	if frappe.db.sql(
		"""
		select 1 from `tabCSKH Contact Attempt` USE INDEX (idx_cskh_attempt_ownevt_occ)
		where (ownership_event is null or ownership_event = '')
		  and occurred_at >= %s and occurred_at < %s
		limit 1
		""",
		(start_dt, end_dt_excl),
	):
		return None
	if eff_emp and frappe.db.sql(
		"""
		select 1 from `tabCSKH Contact Attempt` USE INDEX (cskh_attempt_perf_occ_idx)
		where (performed_by is null or performed_by = '')
		  and occurred_at >= %s and occurred_at < %s
		limit 1
		""",
		(start_dt, end_dt_excl),
	):
		return None

	if eff_emp:
		ca_idx = "USE INDEX (cskh_attempt_perf_occ_idx)"
	elif eff_camp and team_mode == "none":
		ca_idx = "USE INDEX (cskh_attempt_camp_occ_idx)"
	else:
		ca_idx = "USE INDEX (idx_cskh_attempt_occ_name)"

	where_parts = [
		"ca.occurred_at >= %s",
		"ca.occurred_at < %s",
		"ca.occurred_at <= %s",
		"ca.recorded_at <= %s",
	]
	base_params: list = [start_dt, end_dt_excl, eff_as_of, eff_as_of]

	if mcode not in ("VOID_ATTEMPT_RATE", "CORRECTION_RATE"):
		where_parts.append("coalesce(ca.contact_attempt_snapshot, 0) = 1")
	if mcode == "CONNECTED_ATTEMPT_COUNT":
		where_parts.append("coalesce(ca.connected_snapshot, 0) = 1")
	elif mcode == "VERIFICATION_RATE":
		if bucket == "numerator_only":
			where_parts.append("ca.verification_status = 'verified'")
		elif bucket == "breached_only":
			where_parts.append("coalesce(ca.verification_status, '') != 'verified'")

	valid_outcome_codes: set[str] = set()
	requires_note_codes: set[str] = set()
	has_comp_col = frappe.db.has_column("CSKH Contact Attempt", "complete_fields")
	if mcode == "VALID_OUTCOME_LOGGING_RATE":
		if frappe.db.table_exists("CSKH Outcome Code"):
			for oc in frappe.db.get_all("CSKH Outcome Code", fields=["name", "code", "requires_note"]):
				c_key = str(oc.code or oc.name)
				valid_outcome_codes.add(c_key)
				if cint(oc.requires_note):
					requires_note_codes.add(c_key)
		comp_parts = ["trim(coalesce(ca.channel, '')) != ''"]
		comp_params: list = []
		if has_comp_col:
			comp_parts.append("coalesce(ca.complete_fields, 1) = 1")
		if valid_outcome_codes:
			voc_sorted = sorted(valid_outcome_codes)
			ph_v = ", ".join(["%s"] * len(voc_sorted))
			comp_parts.append(f"ca.outcome_code in ({ph_v})")
			comp_params.extend(voc_sorted)
		if requires_note_codes:
			rnc_sorted = sorted(requires_note_codes)
			ph_r = ", ".join(["%s"] * len(rnc_sorted))
			comp_parts.append(f"(ca.outcome_code not in ({ph_r}) or trim(coalesce(ca.note, '')) != '')")
			comp_params.extend(rnc_sorted)
		comp_sql = "(" + " and ".join(comp_parts) + ")"
		if bucket == "numerator_only":
			where_parts.append(comp_sql)
			base_params.extend(comp_params)
		elif bucket == "breached_only":
			where_parts.append(f"not {comp_sql}")
			base_params.extend(comp_params)

	if eff_camp:
		where_parts.append("ca.campaign = %s")
		base_params.append(eff_camp)
	if eff_emp:
		where_parts.append("ca.performed_by = %s")
		base_params.append(eff_emp)

	need_ah_join = team_mode != "none"
	if team_mode == "unattributed":
		where_parts.append(
			"not (ah_iv.team_at_assignment is not null and ah_iv.team_at_assignment != '' and ah_iv.attribution_status = %s)"
		)
		base_params.append(AUTHORITATIVE)
	elif team_mode == "authoritative_in":
		if len(team_nodes) == 1:
			where_parts.append("ah_iv.team_at_assignment = %s and ah_iv.attribution_status = %s")
			base_params.extend([team_nodes[0], AUTHORITATIVE])
		else:
			ph_t = ", ".join(["%s"] * len(team_nodes))
			where_parts.append(f"ah_iv.team_at_assignment in ({ph_t}) and ah_iv.attribution_status = %s")
			base_params.extend([*team_nodes, AUTHORITATIVE])

	where_clause = " and ".join(where_parts)
	count_join = (
		"inner join `tabCSKH Assignment History` ah_iv on ah_iv.name = ca.ownership_event"
		if need_ah_join
		else ""
	)

	def _count_attempts() -> int:
		return int(
			frappe.db.sql(
				f"""
				select count(*)
				from `tabCSKH Contact Attempt` ca {ca_idx}
				{count_join}
				where {where_clause}
				""",
				tuple(base_params),
			)[0][0]
			or 0
		)

	total = _resolve_fast_total(
		mcode=mcode,
		bucket=bucket,
		snap_cell=snap_cell,
		dirty_pending=dirty_pending,
		count_fn=_count_attempts,
	)
	if total == 0 or offset >= total:
		return [], total

	comp_sel = "ca.complete_fields" if has_comp_col else "null as complete_fields"
	page_rows = frappe.db.sql(
		f"""
		select ca.name, ca.work_item, ca.lead, ca.campaign, ca.ownership_event,
		       ca.owner_at_attempt, ca.performed_by, ca.occurred_at, ca.channel,
		       ca.outcome_code, ca.connected_snapshot, ca.verification_status,
		       {comp_sel}, ca.note,
		       ah_iv.team_at_assignment, ah_iv.attribution_status
		from `tabCSKH Contact Attempt` ca {ca_idx}
		inner join `tabCSKH Assignment History` ah_iv on ah_iv.name = ca.ownership_event
		where {where_clause}
		order by ca.occurred_at asc, ca.name asc
		limit %s offset %s
		""",
		tuple([*base_params, limit, offset]),
		as_dict=True,
	)
	if any(not r.get("attribution_status") for r in page_rows):
		return None

	out_events: list[dict] = []
	for r in page_rows:
		is_conn = bool(cint(r.connected_snapshot))
		actor_u = r.get("performed_by") or r.get("owner_at_attempt")
		eff_code = str(r.get("outcome_code") or "")
		if mcode in ("CONTACT_ATTEMPT_COUNT", "CONNECTED_ATTEMPT_COUNT"):
			in_num = True
		elif mcode == "VERIFICATION_RATE":
			in_num = r.get("verification_status") == "verified"
		elif mcode == "VALID_OUTCOME_LOGGING_RATE":
			raw_comp = r.get("complete_fields")
			comp_flag = True if raw_comp is None else bool(cint(raw_comp))
			has_note_ok = (eff_code not in requires_note_codes) or bool(str(r.get("note") or "").strip())
			code_ok = (not valid_outcome_codes) or (eff_code in valid_outcome_codes)
			chan_ok = bool(str(r.get("channel") or "").strip())
			in_num = comp_flag and code_ok and chan_ok and has_note_ok
		else:
			in_num = False

		out_events.append(
			{
				"record_type": "contact_attempt",
				"record_id": r.name,
				"attempt_id": r.name,
				"work_item": r.work_item,
				"campaign": r.campaign,
				"owner_user": actor_u,
				"team_id": r.team_at_assignment,
				"channel": r.channel,
				"outcome_code": r.outcome_code,
				"verification_status": r.verification_status,
				"is_connected": is_conn,
				"is_voided": False,
				"has_approved_correction": False,
				"status": "connected" if is_conn else "attempted",
				"in_numerator": in_num,
				"occurred_at": r.occurred_at or None,
				"_lead_id": r.lead,
				"_note_excerpt": r.note or "",
			}
		)
	return out_events, total


def _try_fast_sql_drilldown_page(
	*,
	mcode: str,
	period: PeriodSpec,
	as_of_dt,
	act: ReportActor,
	stype: str,
	sid: str,
	camp_filter: str,
	team_filter_arg: str | None,
	emp_filter_arg: str | None,
	leader_allowed_nodes: set[str] | None,
	bucket: str,
	offset: int,
	limit: int,
	snap_cell: dict | None,
	snap_metrics_all: dict | None,
	dirty_pending: bool,
) -> tuple[list[dict], int] | None:
	"""Dispatch SQL-paginated drill-down (`G18` — `0` full-period Python hydration)."""
	if mcode not in _FAST_SQL_DRILLDOWN_METRICS:
		return None

	# Preserve exact `ForbiddenScope` check when a `Team Leader` queries an employee outside live `allowed_users`
	if leader_allowed_nodes is not None:
		if stype == "employee" and not (act.allowed_users and sid in act.allowed_users):
			return None
		if (
			emp_filter_arg
			and stype != "employee"
			and not (act.allowed_users and emp_filter_arg in act.allowed_users)
		):
			return None

	eff_as_of = get_datetime(as_of_dt or now_datetime())
	if eff_as_of < period.start_dt:
		return [], 0

	# Fall back cleanly whenever any `CSKH Attempt Correction` exists up to `eff_as_of`
	if frappe.db.table_exists("CSKH Attempt Correction") and frappe.db.sql(
		"select 1 from `tabCSKH Attempt Correction` where requested_at <= %s limit 1",
		(eff_as_of,),
	):
		return None

	team_mode, team_nodes = _resolve_drilldown_team_scope_mode(
		stype=stype,
		sid=sid,
		team_filter_arg=team_filter_arg,
		leader_allowed_nodes=leader_allowed_nodes,
	)
	if team_mode == "empty":
		return [], 0

	eff_camp = sid if stype == "campaign" else (camp_filter or "")
	eff_emp = sid if stype == "employee" else (emp_filter_arg or "")

	if mcode in _FAST_INTERVAL_METRICS:
		return _fast_interval_cohort_drilldown_page(
			mcode=mcode,
			period=period,
			eff_as_of=eff_as_of,
			eff_camp=eff_camp,
			eff_emp=eff_emp,
			team_mode=team_mode,
			team_nodes=team_nodes,
			bucket=bucket,
			offset=offset,
			limit=limit,
			snap_cell=snap_cell,
			snap_metrics_all=snap_metrics_all,
			dirty_pending=dirty_pending,
		)
	if mcode in _FAST_SLA_METRICS:
		return _fast_sla_obligation_drilldown_page(
			mcode=mcode,
			period=period,
			eff_as_of=eff_as_of,
			eff_camp=eff_camp,
			eff_emp=eff_emp,
			team_mode=team_mode,
			team_nodes=team_nodes,
			bucket=bucket,
			offset=offset,
			limit=limit,
			snap_cell=snap_cell,
			dirty_pending=dirty_pending,
		)
	return _fast_attempt_grain_drilldown_page(
		mcode=mcode,
		period=period,
		eff_as_of=eff_as_of,
		eff_camp=eff_camp,
		eff_emp=eff_emp,
		team_mode=team_mode,
		team_nodes=team_nodes,
		bucket=bucket,
		offset=offset,
		limit=limit,
		snap_cell=snap_cell,
		dirty_pending=dirty_pending,
	)


def get_metric_drilldown(
	metric_code: str,
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	scope_type: str | None = None,
	scope_id: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	employee: str | None = None,
	lead_source: str | None = None,
	search: str | None = None,
	sort_by: str | None = None,
	sort_dir: str | None = None,
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
	if mcode == "BREACH_COUNT":
		mcode = "SLA_BREACH_COUNT"
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
	team_filter_arg = _validate_team_filter(act, team)
	emp_filter_arg = parse_optional_str(employee, "Tham số employee không hợp lệ.")
	src_filter = _validate_lead_source_filter(lead_source)
	search_q = parse_optional_str(search, "Tham số search không hợp lệ.")
	sort_key_clean = parse_optional_str(sort_by, "Tham số sort_by không hợp lệ.")
	sort_dir_clean = parse_optional_str(sort_dir, "Tham số sort_dir không hợp lệ.")

	stype = (parse_optional_str(scope_type, "Tham số scope_type không hợp lệ.") or "").lower()
	sid = parse_optional_str(scope_id, "Tham số scope_id không hợp lệ.") or ""

	if not stype:
		if act.role_profile == "staff":
			stype, sid = "employee", act.user
		elif act.role_profile == "team_leader":
			stype, sid = "team", team_filter_arg or act.root_node or ""
		else:
			stype, sid = "company", COMPANY_SCOPE_ID

	if stype not in ("company", "team", "employee", "campaign"):
		raise InvalidRequest("Tham số scope_type phải thuộc ('company', 'team', 'employee', 'campaign').")

	if act.role_profile == "staff":
		if (
			stype != "employee"
			or (sid and sid != act.user)
			or (emp_filter_arg and emp_filter_arg != act.user)
		):
			raise ForbiddenScope("Nhân viên chỉ được xem chi tiết drill-down của chính mình.")
		sid = act.user
	elif act.role_profile == "team_leader":
		if stype == "company":
			raise ForbiddenScope("Team Leader không được phép drill-down toàn công ty.")
		if stype == "team":
			sid = sid or team_filter_arg or act.root_node or ""
			if sid == UNATTRIBUTED_TEAM_ID or not act.allowed_nodes or sid not in act.allowed_nodes:
				raise ForbiddenScope("Đội nhóm drill-down nằm ngoài phạm vi cây phân cấp của bạn.")
		elif stype == "employee":
			if not sid:
				raise InvalidRequest("Bắt buộc truyền scope_id khi scope_type='employee'.")
		elif stype == "campaign":
			if not sid:
				raise InvalidRequest("Bắt buộc truyền scope_id khi scope_type='campaign'.")
			_validate_campaign_filter(act, sid)
	else:
		if stype == "company":
			sid = COMPANY_SCOPE_ID
		elif not sid:
			raise InvalidRequest(f"Bắt buộc truyền scope_id khi scope_type='{stype}'.")

	eff_camp_filter = "" if stype == "campaign" else camp_filter
	run_row = _ensure_or_load_published_run(period, campaign_filter=eff_camp_filter)
	as_of_dt = get_datetime(run_row.as_of) if (run_row and run_row.as_of) else None
	freshness = _build_freshness(period, run_row, campaign_filter=eff_camp_filter)
	leader_allowed_nodes = set(act.allowed_nodes or ()) if act.role_profile == "team_leader" else None

	# Fetch matching snapshot cell for reconciliation metadata (and paginated fast path)
	snap_cell = None
	snap_metrics_all = None
	if run_row and not src_filter and not team_filter_arg and not emp_filter_arg and not search_q:
		if (
			stype == "employee"
			and act.role_profile == "team_leader"
			and leader_allowed_nodes
			and len(leader_allowed_nodes) == 1
		):
			only_node = next(iter(leader_allowed_nodes))
			et_snaps = _fetch_snapshots(
				period,
				"employee_team",
				campaign_filter=eff_camp_filter,
				scope_ids=[f"{sid}::{only_node}"],
			)
			if et_snaps:
				snap_metrics_all = et_snaps[0]["metrics"]
				snap_cell = snap_metrics_all.get(mcode)
		elif not (stype == "campaign" and act.role_profile == "team_leader"):
			snaps = _fetch_snapshots(period, stype, campaign_filter=eff_camp_filter, scope_ids=[sid])
			if snaps:
				snap_metrics_all = snaps[0]["metrics"]
				snap_cell = snap_metrics_all.get(mcode)

	fast_page_result = None
	if not search_q and not sort_key_clean and not src_filter:
		fast_page_result = _try_fast_sql_drilldown_page(
			mcode=mcode,
			period=period,
			as_of_dt=as_of_dt,
			act=act,
			stype=stype,
			sid=sid,
			camp_filter=camp_filter,
			team_filter_arg=team_filter_arg,
			emp_filter_arg=emp_filter_arg,
			leader_allowed_nodes=leader_allowed_nodes,
			bucket=bucket,
			offset=offset,
			limit=limit,
			snap_cell=snap_cell,
			snap_metrics_all=snap_metrics_all,
			dirty_pending=bool(freshness.get("dirty_pending")),
		)

	emit_pii = (
		act.role_profile != "bod" and act.pii_allowed and (wants_pii if include_pii is not None else True)
	)

	if fast_page_result is not None:
		sliced, total = fast_page_result
		team_map = _resolve_team_label_map({str(r.get("team_id")) for r in sliced if r.get("team_id")})
		camp_map = _resolve_campaign_title_map({str(r.get("campaign")) for r in sliced if r.get("campaign")})
		page_lead_ids = [r.get("_lead_id") for r in sliced if r.get("_lead_id")] if emit_pii else []
		lead_display_map = _load_lead_display_map(page_lead_ids, act) if emit_pii else {}
		wi_readable_cache: dict[str, bool] = {}
		if emit_pii:
			_prime_wi_readable_cache(
				[str(r.get("work_item")) for r in sliced if r.get("work_item")],
				act,
				wi_readable_cache,
			)
		projected_rows = [
			_project_drilldown_pii(
				r,
				act,
				lead_display_map,
				wi_readable_cache,
				emit_pii=emit_pii,
				team_map=team_map,
				camp_map=camp_map,
			)
			for r in sliced
		]
	else:
		eff_emp_pushdown = sid if stype == "employee" else (emp_filter_arg or "")
		ds = load_period_raw_dataset(
			period,
			as_of=as_of_dt,
			campaign_filter=(sid if stype == "campaign" else (camp_filter or "")),
			lead_source_filter=src_filter,
			employee_filter=eff_emp_pushdown,
			drilldown_metric=mcode,
		)

		attempts = ds["attempts"]
		obligations = ds["obligations"]
		intervals = ds["intervals"]
		qa_reviews = ds["qa_reviews"]

		# Apply `(stype, sid)` AND `team_leader` historical `team-as-of` bounds (`F-P3-04`!)
		if stype == "team":
			if sid == UNATTRIBUTED_TEAM_ID:
				attempts = [
					a
					for a in attempts
					if not (a["resolved_team"] and a["resolved_attribution"] == AUTHORITATIVE)
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
				qa_reviews = [qr for qr in qa_reviews if not qr.get("team_at_review")]
			else:
				sub_t = set(_subtree_nodes(sid) or {sid})
				if leader_allowed_nodes is not None:
					sub_t &= leader_allowed_nodes
				attempts = [
					a
					for a in attempts
					if a["resolved_team"] in sub_t and a["resolved_attribution"] == AUTHORITATIVE
				]
				obligations = [
					ob
					for ob in obligations
					if ob["resolved_team"] in sub_t and ob["resolved_attribution"] == AUTHORITATIVE
				]
				intervals = [
					iv
					for iv in intervals
					if iv["resolved_team"] in sub_t and iv["resolved_attribution"] == AUTHORITATIVE
				]
				qa_reviews = [qr for qr in qa_reviews if (qr.get("team_at_review") or "") in sub_t]
		elif stype == "employee":
			attempts = [a for a in attempts if a.get("actor_user") == sid]
			obligations = [ob for ob in obligations if ob.get("owner_user") == sid]
			intervals = [iv for iv in intervals if iv.get("to_user") == sid]
			qa_reviews = [qr for qr in qa_reviews if qr.get("reviewed_staff") == sid]
			if leader_allowed_nodes is not None:
				attempts = [
					a
					for a in attempts
					if a.get("resolved_attribution") == AUTHORITATIVE
					and a.get("resolved_team") in leader_allowed_nodes
				]
				obligations = [
					ob
					for ob in obligations
					if ob.get("resolved_attribution") == AUTHORITATIVE
					and ob.get("resolved_team") in leader_allowed_nodes
				]
				intervals = [
					iv
					for iv in intervals
					if iv.get("resolved_attribution") == AUTHORITATIVE
					and iv.get("resolved_team") in leader_allowed_nodes
				]
				qa_reviews = [
					qr for qr in qa_reviews if (qr.get("team_at_review") or "") in leader_allowed_nodes
				]
				in_live_subtree = bool(act.allowed_users and sid in act.allowed_users)
				if not (in_live_subtree or attempts or obligations or intervals or qa_reviews):
					raise ForbiddenScope("Nhân viên drill-down nằm ngoài phạm vi quản lý và lịch sử của bạn.")
		elif stype == "campaign":
			attempts = [a for a in attempts if a.get("campaign") == sid]
			obligations = [ob for ob in obligations if ob.get("campaign") == sid]
			intervals = [iv for iv in intervals if iv.get("campaign") == sid]
			qa_reviews = [qr for qr in qa_reviews if qr.get("campaign") == sid]
			if leader_allowed_nodes is not None:
				attempts = [
					a
					for a in attempts
					if a.get("resolved_attribution") == AUTHORITATIVE
					and a.get("resolved_team") in leader_allowed_nodes
				]
				obligations = [
					ob
					for ob in obligations
					if ob.get("resolved_attribution") == AUTHORITATIVE
					and ob.get("resolved_team") in leader_allowed_nodes
				]
				intervals = [
					iv
					for iv in intervals
					if iv.get("resolved_attribution") == AUTHORITATIVE
					and iv.get("resolved_team") in leader_allowed_nodes
				]
				qa_reviews = [
					qr for qr in qa_reviews if (qr.get("team_at_review") or "") in leader_allowed_nodes
				]

		# Optional additional `team` / `employee` filters (`F-P3-10`)
		if team_filter_arg and stype != "team":
			tf_nodes = set(_subtree_nodes(team_filter_arg) or {team_filter_arg})
			if leader_allowed_nodes is not None:
				tf_nodes &= leader_allowed_nodes
			attempts = [
				a
				for a in attempts
				if a.get("resolved_attribution") == AUTHORITATIVE and a.get("resolved_team") in tf_nodes
			]
			obligations = [
				ob
				for ob in obligations
				if ob.get("resolved_attribution") == AUTHORITATIVE and ob.get("resolved_team") in tf_nodes
			]
			intervals = [
				iv
				for iv in intervals
				if iv.get("resolved_attribution") == AUTHORITATIVE and iv.get("resolved_team") in tf_nodes
			]
			qa_reviews = [qr for qr in qa_reviews if (qr.get("team_at_review") or "") in tf_nodes]

		if emp_filter_arg and stype != "employee":
			if leader_allowed_nodes is not None:
				in_live = bool(act.allowed_users and emp_filter_arg in act.allowed_users)
				has_emp_hist = (
					any(a.get("actor_user") == emp_filter_arg for a in attempts)
					or any(ob.get("owner_user") == emp_filter_arg for ob in obligations)
					or any(iv.get("to_user") == emp_filter_arg for iv in intervals)
				)
				if not (in_live or has_emp_hist):
					raise ForbiddenScope("Nhân viên lọc trên drill-down nằm ngoài phạm vi quản lý của bạn.")
			attempts = [a for a in attempts if a.get("actor_user") == emp_filter_arg]
			obligations = [ob for ob in obligations if ob.get("owner_user") == emp_filter_arg]
			intervals = [iv for iv in intervals if iv.get("to_user") == emp_filter_arg]
			qa_reviews = [qr for qr in qa_reviews if qr.get("reviewed_staff") == emp_filter_arg]

		events = _extract_drilldown_events(
			mcode=mcode,
			bucket=bucket,
			attempts=attempts,
			obligations=obligations,
			intervals=intervals,
			qa_reviews=qa_reviews,
			dnc_since_by_lead=ds["dnc_since_by_lead"],
			cutoff_dt=ds["cutoff_dt"],
			policy_cfg=ds["policy_cfg"],
		)

		if search_q or sort_key_clean:
			# Project PII BEFORE search/sort so redacted rows NEVER leak via search filter-oracles!
			team_map = _resolve_team_label_map({str(r.get("team_id")) for r in events if r.get("team_id")})
			camp_map = _resolve_campaign_title_map(
				{str(r.get("campaign")) for r in events if r.get("campaign")}
			)
			all_lead_ids = [r.get("_lead_id") for r in events if r.get("_lead_id")] if emit_pii else []
			lead_display_map = _load_lead_display_map(all_lead_ids, act) if emit_pii else {}
			wi_readable_cache = {}
			if emit_pii:
				_prime_wi_readable_cache(
					[str(r.get("work_item")) for r in events if r.get("work_item")],
					act,
					wi_readable_cache,
				)
			projected_all = [
				_project_drilldown_pii(
					r,
					act,
					lead_display_map,
					wi_readable_cache,
					emit_pii=emit_pii,
					team_map=team_map,
					camp_map=camp_map,
				)
				for r in events
			]
			if search_q:
				ql = search_q.lower()
				projected_all = [r for r in projected_all if _drilldown_row_matches_search(r, ql)]
			if sort_key_clean:
				projected_all = _sort_drilldown_rows(projected_all, sort_key_clean, sort_dir_clean)
			total = len(projected_all)
			projected_rows = projected_all[offset : offset + limit]
		else:
			total = len(events)
			sliced = events[offset : offset + limit]
			team_map = _resolve_team_label_map({str(r.get("team_id")) for r in sliced if r.get("team_id")})
			camp_map = _resolve_campaign_title_map(
				{str(r.get("campaign")) for r in sliced if r.get("campaign")}
			)
			page_lead_ids = [r.get("_lead_id") for r in sliced if r.get("_lead_id")] if emit_pii else []
			lead_display_map = _load_lead_display_map(page_lead_ids, act) if emit_pii else {}
			wi_readable_cache = {}
			if emit_pii:
				_prime_wi_readable_cache(
					[str(r.get("work_item")) for r in sliced if r.get("work_item")],
					act,
					wi_readable_cache,
				)
			projected_rows = [
				_project_drilldown_pii(
					r,
					act,
					lead_display_map,
					wi_readable_cache,
					emit_pii=emit_pii,
					team_map=team_map,
					camp_map=camp_map,
				)
				for r in sliced
			]

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
		"freshness": freshness,
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
		"started_at": ob.started_at or None,
		"original_due_at": ob.original_due_at or None,
		"effective_due_at": ob.effective_due_at or None,
		"fulfilled_at": ob.fulfilled_at or None,
		"_lead_id": ob.lead,
		"_note_excerpt": None,
	}


def _iv_event_dict(iv: dict, *, in_numerator: bool, status_label: str) -> dict:
	iv_s = iv.get("iv_start_dt") or iv.get("ownership_start") or iv.get("event_at")
	return {
		"record_type": "ownership_interval",
		"record_id": iv.name,
		"interval_event": iv.name,
		"work_item": iv.work_item,
		"campaign": iv.campaign,
		"owner_user": iv.to_user,
		"team_id": iv.get("resolved_team"),
		"status": status_label,
		"in_numerator": in_numerator,
		"started_at": iv_s or None,
		"effective_due_at": iv.get("ownership_end") or None,
		"_lead_id": iv.lead,
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
		"verification_status": a.get("verification_status"),
		"is_connected": bool(eff.get("is_connected")),
		"is_voided": bool(a["is_voided"]),
		"has_approved_correction": bool(a["has_approved_correction"]),
		"status": "voided" if a["is_voided"] else ("connected" if eff.get("is_connected") else "attempted"),
		"in_numerator": in_numerator,
		"occurred_at": eff.get("occurred_at") or None,
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
	qa_reviews: list[dict],
	dnc_since_by_lead: dict,
	cutoff_dt,
	policy_cfg: dict,
) -> list[dict]:
	"""Return exact numerator/denominator/breached/excluded items matching `kpi_calculator._aggregate_bucket` (`cskh-metrics-v2`)."""
	# 1. Deadline-cohort SLA obligations (`F-P3-02`!)
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

		due_obs = [ob for ob in obligations if ob.obligation_type == target_type and ob["in_due_window"]]
		if bucket == "excluded":
			excl = [ob for ob in due_obs if ob["as_of_status"] not in elig_statuses]
			return [_ob_event_dict(ob, in_numerator=False) for ob in excl]

		elig = [ob for ob in due_obs if ob["as_of_status"] in elig_statuses]
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
			and ob["in_due_window"]
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

	# 2. Interval-grain `CONNECTED_RATE` (`F-P3-01`!) & `ELIGIBLE_COVERAGE` (`F-P3-03`!)
	eff_valid = [
		a
		for a in attempts
		if a["in_effective_window"] and not a["is_voided"] and bool(a["effective"]["is_contact_attempt"])
	]
	assigned_ivs = [iv for iv in intervals if iv.get("in_assigned_window")]

	if mcode == "CONNECTED_RATE":
		rows = []
		for iv in assigned_ivs:
			is_tried = bool(iv.get("tried_for_connected_rate"))
			is_conn = is_tried and bool(iv.get("has_connected_cutoff"))
			has_inv_phone = bool(iv.get("has_invalid_phone_cutoff"))
			if bucket == "excluded":
				if not is_tried:
					rows.append(
						_iv_event_dict(
							iv,
							in_numerator=False,
							status_label="invalid_phone" if has_inv_phone else "untried",
						)
					)
				continue
			if not is_tried:
				continue
			if bucket == "numerator_only" and not is_conn:
				continue
			if bucket == "breached_only" and is_conn:
				continue
			rows.append(
				_iv_event_dict(
					iv, in_numerator=is_conn, status_label="connected" if is_conn else "not_connected"
				)
			)
		return rows

	if mcode == "ELIGIBLE_COVERAGE":
		rows = []
		for iv in assigned_ivs:
			is_elig = bool(iv.get("coverage_eligible"))
			is_touched = bool(iv.get("has_attempt_cutoff"))
			if bucket == "excluded":
				if not is_elig:
					rows.append(_iv_event_dict(iv, in_numerator=False, status_label="excluded_coverage"))
				continue
			if not is_elig:
				continue
			if bucket == "numerator_only" and not is_touched:
				continue
			if bucket == "breached_only" and is_touched:
				continue
			rows.append(
				_iv_event_dict(
					iv, in_numerator=is_touched, status_label="touched" if is_touched else "untouched"
				)
			)
		return rows

	if mcode == "ASSIGNED_INTERVALS":
		if bucket in ("breached_only", "excluded"):
			return []
		return [_iv_event_dict(iv, in_numerator=True, status_label="assigned") for iv in assigned_ivs]

	if mcode in ("UNIQUE_ASSIGNED_ITEMS", "CONVERSION_RATE"):
		by_wi_iv: dict[str, dict] = {}
		for iv in assigned_ivs:
			if iv.work_item:
				by_wi_iv[str(iv.work_item)] = iv
		if mcode == "UNIQUE_ASSIGNED_ITEMS":
			if bucket in ("breached_only", "excluded"):
				return []
			out = []
			for wi in sorted(by_wi_iv.keys()):
				d = _iv_event_dict(by_wi_iv[wi], in_numerator=True, status_label="assigned")
				d["record_type"] = "work_item"
				d["record_id"] = wi
				out.append(d)
			return out

		# `CONVERSION_RATE`
		eff_up_to_cutoff = [
			a
			for a in attempts
			if not a["is_voided"]
			and bool(a["effective"]["is_contact_attempt"])
			and get_datetime(a["effective"]["occurred_at"]) <= cutoff_dt
		]
		inv_wis = {
			str(a["work_item"])
			for a in eff_up_to_cutoff
			if a.get("work_item") and bool(a["effective"].get("is_invalid_phone"))
		}
		conv_wis = {
			str(a["work_item"])
			for a in eff_valid
			if a.get("work_item") and bool(a["effective"].get("is_conversion"))
		}
		out = []
		for wi in sorted(by_wi_iv.keys()):
			rep_iv = by_wi_iv[wi]
			if wi in inv_wis:
				if bucket == "excluded":
					d = _iv_event_dict(rep_iv, in_numerator=False, status_label="invalid_phone")
					d["record_type"] = "work_item"
					d["record_id"] = wi
					out.append(d)
				continue
			if bucket == "excluded":
				continue
			is_conv = wi in conv_wis
			if bucket == "numerator_only" and not is_conv:
				continue
			if bucket == "breached_only" and is_conv:
				continue
			d = _iv_event_dict(
				rep_iv, in_numerator=is_conv, status_label="converted" if is_conv else "not_converted"
			)
			d["record_type"] = "work_item"
			d["record_id"] = wi
			out.append(d)
		return out

	if mcode == "CONNECTED_ITEMS":
		if bucket in ("breached_only", "excluded"):
			return []
		by_wi_att: dict[str, dict] = {}
		for a in eff_valid:
			if a.get("work_item") and bool(a["effective"].get("is_connected")):
				by_wi_att[str(a["work_item"])] = a
		out = []
		for wi in sorted(by_wi_att.keys()):
			d = _att_event_dict(by_wi_att[wi], in_numerator=True)
			d["record_type"] = "work_item"
			d["record_id"] = wi
			out.append(d)
		return out

	# 3. Attempt-grain volume, completeness, verification & correction metrics
	if mcode in ("CONTACT_ATTEMPT_COUNT", "CONNECTED_ATTEMPT_COUNT"):
		if bucket == "excluded":
			voids = [a for a in attempts if a["in_raw_window"] and a["is_voided"]]
			return [_att_event_dict(a, in_numerator=False) for a in voids]
		if bucket == "breached_only":
			return []
		if mcode == "CONTACT_ATTEMPT_COUNT":
			return [_att_event_dict(a, in_numerator=True) for a in eff_valid]
		return [_att_event_dict(a, in_numerator=True) for a in eff_valid if a["effective"]["is_connected"]]

	if mcode in ("VALID_OUTCOME_LOGGING_RATE", "VERIFICATION_RATE"):
		if bucket == "excluded":
			voids = [a for a in attempts if a["in_raw_window"] and a["is_voided"]]
			return [_att_event_dict(a, in_numerator=False) for a in voids]
		rows = []
		for a in eff_valid:
			in_num = (
				bool(a.get("complete_fields_ok"))
				if mcode == "VALID_OUTCOME_LOGGING_RATE"
				else (a.get("verification_status") == "verified")
			)
			if bucket == "numerator_only" and not in_num:
				continue
			if bucket == "breached_only" and in_num:
				continue
			rows.append(_att_event_dict(a, in_numerator=in_num))
		return rows

	if mcode in ("CORRECTION_RATE", "VOID_ATTEMPT_RATE"):
		raw_win = [a for a in attempts if a["in_raw_window"]]
		if bucket == "excluded":
			return []
		rows = []
		for a in raw_win:
			in_num = (
				bool(a["has_approved_correction"]) if mcode == "CORRECTION_RATE" else bool(a["is_voided"])
			)
			if bucket == "numerator_only" and not in_num:
				continue
			if bucket == "breached_only" and not in_num:
				continue
			rows.append(_att_event_dict(a, in_numerator=in_num))
		return rows

	if mcode == "QA_PASS_RATE":
		rows = []
		for qr in qa_reviews:
			is_pub = (qr.get("status") or "").lower() in ("published", "approved")
			is_rand = (qr.get("cohort_type") or "random_sample") == "random_sample"
			if bucket == "excluded":
				if not (is_pub and is_rand):
					rows.append(
						{
							"record_type": "qa_review",
							"record_id": qr.name,
							"qa_review_id": qr.name,
							"work_item": qr.work_item,
							"campaign": qr.campaign,
							"owner_user": qr.reviewed_staff,
							"team_id": qr.team_at_review,
							"status": qr.status,
							"in_numerator": False,
							"occurred_at": format_calendar_iso(qr.reviewed_at) if qr.reviewed_at else None,
							"_lead_id": None,
							"_note_excerpt": qr.feedback_summary,
						}
					)
				continue
			if not (is_pub and is_rand):
				continue
			passed = bool(cint(qr.passed))
			if bucket == "numerator_only" and not passed:
				continue
			if bucket == "breached_only" and passed:
				continue
			rows.append(
				{
					"record_type": "qa_review",
					"record_id": qr.name,
					"qa_review_id": qr.name,
					"work_item": qr.work_item,
					"campaign": qr.campaign,
					"owner_user": qr.reviewed_staff,
					"team_id": qr.team_at_review,
					"status": "passed" if passed else "failed",
					"in_numerator": passed,
					"occurred_at": format_calendar_iso(qr.reviewed_at) if qr.reviewed_at else None,
					"_lead_id": None,
					"_note_excerpt": qr.feedback_summary,
				}
			)
		return rows

	# 4. Operational backlog, breach & waiver counts
	if mcode in (
		"OVERDUE_BACKLOG",
		"OPEN_FOLLOWUPS_OVERDUE",
		"SLA_BREACH_COUNT",
		"BREACH_COUNT",
		"SLA_WAIVER_COUNT",
	):
		if bucket in ("breached_only", "excluded"):
			return []
		if mcode == "OVERDUE_BACKLOG":
			overdue_obs = [ob for ob in obligations if bool(ob.get("is_open_overdue_as_of"))]
			by_wi_ob: dict[str, dict] = {}
			for ob in overdue_obs:
				if ob.work_item:
					by_wi_ob[str(ob.work_item)] = ob
			out = []
			for wi in sorted(by_wi_ob.keys()):
				d = _ob_event_dict(by_wi_ob[wi], in_numerator=True)
				d["record_type"] = "overdue_work_item"
				d["record_id"] = wi
				out.append(d)
			return out

		if mcode == "OPEN_FOLLOWUPS_OVERDUE":
			fu_over = [
				ob
				for ob in obligations
				if ob.obligation_type == "FOLLOWUP" and bool(ob.get("is_open_overdue_as_of"))
			]
			return [_ob_event_dict(ob, in_numerator=True) for ob in fu_over]

		if mcode in ("SLA_BREACH_COUNT", "BREACH_COUNT"):
			breached = [
				ob
				for ob in obligations
				if ob.obligation_type in ("FIRST_ATTEMPT", "FOLLOWUP")
				and ob["in_due_window"]
				and ob["as_of_status"] in ("fulfilled_late", "breached_open", "breached_handoff")
			]
			return [_ob_event_dict(ob, in_numerator=True) for ob in breached]

		waived = [ob for ob in obligations if ob["in_due_window"] and ob["as_of_status"] == "waived"]
		return [_ob_event_dict(ob, in_numerator=True) for ob in waived]

	if mcode == "RESCHEDULE_RATE":
		fu_win = [
			ob
			for ob in obligations
			if ob.obligation_type == "FOLLOWUP" and (ob["in_started_window"] or ob["in_due_window"])
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
		violated_ivs: set[str] = set()
		lead_by_iv: dict[str, str] = {}
		owner_by_iv: dict[str, str] = {}
		wi_by_iv: dict[str, str] = {}
		camp_by_iv: dict[str, str] = {}
		team_by_iv: dict[str, str | None] = {}
		for iv in assigned_ivs:
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
			{iv.name for iv in assigned_ivs}
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
