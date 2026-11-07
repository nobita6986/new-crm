"""Whitelisted RPC endpoints for P3 BOD Quality Dashboard, Scorecards, Drill-downs, Filter Options & Exports (`docs/p3/report-api-contract.md` §3.1-§3.9)."""

from __future__ import annotations

import frappe

from crm_cskh.services import kpi_runs, p3_export, report_query
from crm_cskh.services.errors import InvalidRequest, P3DisabledError


def _require_post() -> None:
	req = getattr(frappe.local, "request", None)
	if req is not None and getattr(req, "method", "POST").upper() != "POST":
		raise InvalidRequest(
			"Thao tác yêu cầu tính lại hoặc xuất báo cáo P3 bắt buộc dùng phương thức HTTP POST."
		)


def _return_or_raise_http_disabled(res: dict) -> dict:
	"""Preserve `_disabled_report_envelope` for direct Python calls while returning HTTP 503 (`F-P3-08`) over HTTP."""
	if (
		isinstance(res, dict)
		and res.get("status") == "disabled_by_policy"
		and getattr(frappe.local, "request", None) is not None
	):
		raise P3DisabledError(
			"Hệ thống báo cáo chất lượng liên hệ & SLA P3 hiện đang tạm tắt theo cờ cấu hình (p3_reporting_enabled=false)."
		)
	return res


@frappe.whitelist()
def get_executive_overview(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	lead_source: str | None = None,
):
	return _return_or_raise_http_disabled(
		report_query.get_executive_overview(
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
			campaign=campaign,
			team=team,
			lead_source=lead_source,
		)
	)


@frappe.whitelist()
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
):
	return _return_or_raise_http_disabled(
		report_query.get_team_comparison(
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
			campaign=campaign,
			team=team,
			lead_source=lead_source,
			sort_by=sort_by,
			sort_dir=sort_dir,
			page=page,
			page_size=page_size,
		)
	)


@frappe.whitelist()
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
):
	return _return_or_raise_http_disabled(
		report_query.get_employee_quality(
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
			team=team,
			employee=employee,
			campaign=campaign,
			lead_source=lead_source,
			sort_by=sort_by,
			sort_dir=sort_dir,
			page=page,
			page_size=page_size,
		)
	)


@frappe.whitelist()
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
):
	return _return_or_raise_http_disabled(
		report_query.get_campaign_quality(
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
			campaign=campaign,
			team=team,
			lead_source=lead_source,
			sort_by=sort_by,
			sort_dir=sort_dir,
			page=page,
			page_size=page_size,
		)
	)


@frappe.whitelist()
def get_my_scorecard(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign: str | None = None,
	lead_source: str | None = None,
):
	return _return_or_raise_http_disabled(
		report_query.get_my_scorecard(
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
			campaign=campaign,
			lead_source=lead_source,
		)
	)


@frappe.whitelist()
def get_filter_options(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
):
	return _return_or_raise_http_disabled(
		report_query.get_filter_options(
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
		)
	)


@frappe.whitelist()
def get_metric_drilldown(
	metric_code: str = "",
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
):
	return _return_or_raise_http_disabled(
		report_query.get_metric_drilldown(
			metric_code=metric_code,
			period_type=period_type,
			period_key=period_key,
			from_date=from_date,
			to_date=to_date,
			scope_type=scope_type,
			scope_id=scope_id,
			campaign=campaign,
			team=team,
			employee=employee,
			lead_source=lead_source,
			search=search,
			sort_by=sort_by,
			sort_dir=sort_dir,
			filter_bucket=filter_bucket,
			include_pii=include_pii,
			page=page,
			page_size=page_size,
		)
	)


@frappe.whitelist()
def request_export(
	report_type: str = "",
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	scope_type: str | None = None,
	scope_id: str | None = None,
	campaign: str | None = None,
	metric_code: str | None = None,
	filter_bucket: str = "all_denominator",
	include_pii: int | str | bool | None = None,
	format: str = "csv",
):
	_require_post()
	return p3_export.request_export(
		report_type=report_type,
		period_type=period_type,
		period_key=period_key,
		from_date=from_date,
		to_date=to_date,
		scope_type=scope_type,
		scope_id=scope_id,
		campaign=campaign,
		metric_code=metric_code,
		filter_bucket=filter_bucket,
		include_pii=include_pii,
		export_format=format,
	)


@frappe.whitelist()
def get_export_status(export_id: str = ""):
	return p3_export.get_export_status(export_id=export_id)


@frappe.whitelist()
def download_export(export_id: str = ""):
	return p3_export.download_export(export_id=export_id)


@frappe.whitelist()
def request_recompute(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	scope_type: str = "all",
	scope_id: str | None = None,
	campaign: str | None = None,
	reason: str | None = None,
):
	_require_post()
	if not report_query.is_p3_reporting_enabled():
		report_query.resolve_report_actor()
		raise P3DisabledError(
			"Chức năng tính toán và báo cáo P3 hiện đang tạm tắt theo cờ cấu hình (p3_reporting_enabled=false)."
		)
	return kpi_runs.request_scoped_recompute(
		period_type=period_type,
		period_key=period_key,
		from_date=from_date,
		to_date=to_date,
		scope_type=scope_type,
		scope_id=scope_id,
		campaign=campaign,
		reason=reason,
	)


@frappe.whitelist()
def get_run_status(run_id: str = ""):
	if getattr(frappe.local, "request", None) is not None and not report_query.is_p3_reporting_enabled():
		report_query.resolve_report_actor()
		raise P3DisabledError(
			"Hệ thống báo cáo chất lượng liên hệ & SLA P3 hiện đang tạm tắt theo cờ cấu hình (p3_reporting_enabled=false)."
		)
	return kpi_runs.get_kpi_run_status(run_id=run_id)
