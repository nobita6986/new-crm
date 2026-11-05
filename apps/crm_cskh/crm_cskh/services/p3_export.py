"""Async CSV export service with 3-stage authorization, `File.owner='Administrator'` lock, TTL & CSV formula-injection defense (`P3-08`, `S10..S12`, `S18`, `OBS-P2-03`).

Three-Stage Live Permission Revalidation:
  1. **Stage 1 (`request_export` / Enqueue):**
     Validates actor role, live hierarchy subtree, `include_pii` rules (`BOD` -> `403` if PII), and `Staff` self-only bounds before inserting `CSKH Export Job`.
  2. **Stage 2 (`execute_export_job_now` / Worker):**
     Re-evaluates `is_export_job_currently_authorized(job, user=job.requested_by)` against LIVE DB roles & `CRM Sales Hierarchy`. If revoked while queued, marks `status = 'revoked'` and writes `0` files.
     Writes `File` with `is_private = 1`, **`owner = 'Administrator'`**, `attached_to_doctype = 'CSKH Export Job'`, `attached_to_name = job.name`, and random hex filename token.
  3. **Stage 3 (`download_export` + raw `/private/files/...`):**
     Re-evaluates live role/subtree + `status == 'completed'` + `now < expires_at` on BOTH the RPC endpoint and Frappe's `File` permission hook (`permissions.py::linked_lead_has_permission`).
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
from pathlib import Path

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from crm_cskh.api._params import parse_bool, parse_optional_str
from crm_cskh.p3_models import authorized_export_write
from crm_cskh.p3_permissions import is_export_job_currently_authorized
from crm_cskh.services import report_query
from crm_cskh.services.calendar import format_calendar_iso
from crm_cskh.services.errors import (
	Conflict,
	ExportExpired,
	ForbiddenScope,
	InvalidMetric,
	InvalidRequest,
	NotFound,
	PolicyConflict,
	TooManyRows,
)
from crm_cskh.services.kpi_calculator import (
	COMPANY_SCOPE_ID,
	DRILLDOWN_SUPPORTED_METRICS,
	METRIC_VERSION,
	UNATTRIBUTED_TEAM_ID,
)
from crm_cskh.services.kpi_periods import resolve_period_spec

EXPORT_TTL_SECONDS = 3600
MAX_EXPORT_ROWS = 50000

VALID_EXPORT_REPORT_TYPES = frozenset(
	(
		"executive_overview",
		"team_comparison",
		"employee_quality",
		"campaign_quality",
		"my_scorecard",
		"metric_drilldown",
	)
)

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def sanitize_csv_cell(value: object) -> str:
	"""Escape CSV formula injection (`S18`): prefix `'` when a text cell starts with `=`, `+`, `-`, `@`, `\\t`, `\\r`, `\\n`."""
	if value is None:
		return ""
	if isinstance(value, bool):
		return "true" if value else "false"
	if isinstance(value, (int, float)):
		return str(value)
	text = str(value)
	if not text:
		return ""
	stripped = text.lstrip(" ")
	if text.startswith(FORMULA_PREFIXES) or (stripped and stripped[0] in ("=", "+", "-", "@")):
		return f"'{text}"
	return text


def render_csv_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
	"""Serialize `headers` + `rows` to UTF-8 (with BOM-free standard UTF-8) applying `sanitize_csv_cell` to every cell."""
	buf = io.StringIO(newline="")
	writer = csv.writer(buf, lineterminator="\n")
	writer.writerow([sanitize_csv_cell(h) for h in headers])
	for r in rows:
		writer.writerow([sanitize_csv_cell(cell) for cell in r])
	return buf.getvalue().encode("utf-8")


def request_export(
	report_type: str,
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
	export_format: str = "csv",
	*,
	ttl_seconds: int = EXPORT_TTL_SECONDS,
	actor: str | None = None,
) -> dict:
	"""Stage 1: Validate permissions & parameters synchronously, persist `CSKH Export Job`, and enqueue background worker."""
	act = report_query.resolve_report_actor(actor)
	from crm_cskh.services.p3_flags import is_p3_export_enabled

	if not is_p3_export_enabled():
		raise PolicyConflict("Chức năng báo cáo và xuất dữ liệu P3 hiện đang tạm tắt theo cờ cấu hình.")

	rtype = (parse_optional_str(report_type, "Tham số report_type không hợp lệ.") or "").lower()
	if rtype not in VALID_EXPORT_REPORT_TYPES:
		raise InvalidRequest(
			"Tham số report_type phải thuộc ('executive_overview', 'team_comparison', 'employee_quality', "
			"'campaign_quality', 'my_scorecard', 'metric_drilldown')."
		)

	fmt = (parse_optional_str(export_format, "Tham số format không hợp lệ.") or "csv").lower()
	if fmt != "csv":
		raise InvalidRequest("Định dạng xuất dữ liệu hiện chỉ hỗ trợ 'csv'.")

	wants_pii = parse_bool(include_pii, default=False) if include_pii is not None else False
	if act.role_profile == "bod":
		if wants_pii:
			raise ForbiddenScope(
				"BOD Viewer không được phép xuất báo cáo chứa dữ liệu định danh khách hàng (PII)."
			)
		if rtype == "my_scorecard":
			raise ForbiddenScope("BOD Viewer không có bảng điểm cá nhân để xuất.")

	if act.role_profile == "staff":
		if wants_pii:
			raise ForbiddenScope(
				"Nhân viên chỉ được xuất bảng điểm tổng hợp cá nhân không chứa PII khách hàng."
			)
		if rtype not in ("my_scorecard", "employee_quality"):
			raise ForbiddenScope("Nhân viên chỉ được phép xuất báo cáo bảng điểm cá nhân của chính mình.")

	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	camp_filter = report_query._validate_campaign_filter(act, campaign)

	stype = (parse_optional_str(scope_type, "Tham số scope_type không hợp lệ.") or "").lower()
	sid = parse_optional_str(scope_id, "Tham số scope_id không hợp lệ.") or ""

	if rtype == "my_scorecard":
		stype, sid = "employee", act.user
	elif not stype:
		if act.role_profile == "staff":
			stype, sid = "employee", act.user
		elif act.role_profile == "team_leader":
			stype, sid = "team", act.root_node or ""
		else:
			stype, sid = "company", COMPANY_SCOPE_ID

	if stype not in ("company", "team", "employee", "campaign"):
		raise InvalidRequest("Tham số scope_type phải thuộc ('company', 'team', 'employee', 'campaign').")

	# Strict Stage-1 scope enforcement
	if act.role_profile == "staff":
		if stype != "employee" or (sid and sid != act.user):
			raise ForbiddenScope(
				"Nhân viên không được phép xuất dữ liệu của người khác hoặc cấp team/công ty."
			)
		sid = act.user
	elif act.role_profile == "team_leader":
		if stype == "company":
			# Pin Leader exports strictly to their own root hierarchy node so team removal revokes access (`S12`)!
			stype = "team"
			sid = act.root_node or ""
		if stype == "team":
			sid = sid or act.root_node or ""
			if sid == UNATTRIBUTED_TEAM_ID or not act.allowed_nodes or sid not in act.allowed_nodes:
				raise ForbiddenScope("Đội nhóm xuất dữ liệu nằm ngoài phạm vi cây phân cấp của bạn.")
		elif stype == "employee":
			if not sid or not act.allowed_users or sid not in act.allowed_users:
				raise ForbiddenScope("Nhân viên xuất dữ liệu nằm ngoài phạm vi quản lý của bạn.")
		elif stype == "campaign":
			if not sid:
				raise InvalidRequest("Bắt buộc truyền scope_id khi scope_type='campaign'.")
			report_query._validate_campaign_filter(act, sid)
	else:
		if stype == "company":
			sid = COMPANY_SCOPE_ID
		elif not sid:
			raise InvalidRequest(f"Bắt buộc truyền scope_id khi scope_type='{stype}'.")

	mcode = (parse_optional_str(metric_code, "Tham số metric_code không hợp lệ.") or "").upper()
	if rtype == "metric_drilldown":
		supported = DRILLDOWN_SUPPORTED_METRICS | {"CONNECTED_ATTEMPT_COUNT", "RESCHEDULE_RATE"}
		if not mcode or mcode not in supported:
			raise InvalidMetric(
				f"Mã chỉ số '{mcode}' không tồn tại hoặc không hỗ trợ xuất drill-down trong {METRIC_VERSION}."
			)

	bucket = (
		parse_optional_str(filter_bucket, "Tham số filter_bucket không hợp lệ.") or "all_denominator"
	).lower()
	if bucket not in report_query.VALID_DRILLDOWN_BUCKETS:
		raise InvalidRequest("Tham số filter_bucket không hợp lệ.")

	# Drill-down for Leader/Admin includes live-readable Lead PII unless BOD or explicitly `include_pii=False`
	effective_includes_pii = False
	if rtype == "metric_drilldown" and act.role_profile in ("crm_admin", "team_leader"):
		effective_includes_pii = True if include_pii is None else wants_pii

	now_dt = now_datetime()
	bounded_ttl = min(max(int(ttl_seconds or EXPORT_TTL_SECONDS), 5), 86400)
	exp_dt = add_to_date(now_dt, seconds=bounded_ttl)
	payload_json = json.dumps(
		{
			"report_type": rtype,
			"period_type": period.period_type,
			"period_key": period.period_key,
			"from_date": period.from_date.isoformat(),
			"to_date": period.to_date.isoformat(),
			"scope_type": stype,
			"scope_id": sid,
			"campaign": camp_filter,
			"metric_code": mcode,
			"filter_bucket": bucket,
			"include_pii": effective_includes_pii,
		},
		ensure_ascii=False,
		sort_keys=True,
	)

	with authorized_export_write():
		job_doc = frappe.get_doc(
			{
				"doctype": "CSKH Export Job",
				"report_type": rtype,
				"period_type": period.period_type,
				"period_key": period.period_key,
				"from_date": period.from_date.isoformat(),
				"to_date": period.to_date.isoformat(),
				"scope_type": stype,
				"scope_id": sid,
				"campaign_filter": camp_filter,
				"metric_code": mcode,
				"export_format": "csv",
				"includes_pii": 1 if effective_includes_pii else 0,
				"requested_by": act.user,
				"status": "queued",
				"row_count": 0,
				"requested_at": now_dt,
				"expires_at": exp_dt,
				"filter_payload_json": payload_json,
			}
		)
		job_doc.insert(ignore_permissions=True)

	export_id = job_doc.name

	def _enqueue_after_commit():
		frappe.enqueue(
			"crm_cskh.jobs.run_export.execute_export_job",
			queue="short",
			export_id=export_id,
		)

	frappe.db.after_commit.add(_enqueue_after_commit)

	return {
		"ok": True,
		"export_id": export_id,
		"status": "queued",
		"expires_in_seconds": bounded_ttl,
		"expires_at": format_calendar_iso(exp_dt),
	}


def _build_export_table(job_doc, payload: dict) -> tuple[list[str], list[list[object]]]:
	"""Generate `(headers, rows)` executing as `job_doc.requested_by` so live privacy redaction applies."""
	rtype = job_doc.report_type
	actor = job_doc.requested_by
	pt = job_doc.period_type
	pk = job_doc.period_key
	fd = str(job_doc.from_date)
	td = str(job_doc.to_date)
	camp = job_doc.campaign_filter or None

	if rtype == "executive_overview":
		res = report_query.get_executive_overview(
			period_type=pt,
			period_key=pk,
			from_date=fd,
			to_date=td,
			campaign=camp,
			team=(job_doc.scope_id if job_doc.scope_type == "team" else None),
			actor=actor,
		)
		headers = ["metric_code", "label_vi", "value", "numerator", "denominator", "unit", "status", "reason"]
		rows = []
		for mcode, mcell in (res.get("metrics") or {}).items():
			rows.append(
				[
					mcode,
					mcell.get("label_vi"),
					mcell.get("value"),
					mcell.get("numerator"),
					mcell.get("denominator"),
					mcell.get("unit"),
					mcell.get("status"),
					mcell.get("reason"),
				]
			)
		return headers, rows

	if rtype == "my_scorecard":
		res = report_query.get_my_scorecard(
			period_type=pt,
			period_key=pk,
			from_date=fd,
			to_date=td,
			campaign=camp,
			actor=actor,
		)
		headers = ["employee_id", "metric_code", "label_vi", "value", "numerator", "denominator", "status"]
		rows = []
		for mcode, mcell in (res.get("metrics") or {}).items():
			rows.append(
				[
					actor,
					mcode,
					mcell.get("label_vi"),
					mcell.get("value"),
					mcell.get("numerator"),
					mcell.get("denominator"),
					mcell.get("status"),
				]
			)
		return headers, rows

	if rtype == "team_comparison":
		res = report_query.get_team_comparison(
			period_type=pt,
			period_key=pk,
			from_date=fd,
			to_date=td,
			campaign=camp,
			team=(job_doc.scope_id if job_doc.scope_type == "team" else None),
			page=1,
			page_size=100,
			actor=actor,
		)
		headers = [
			"team_id",
			"team_name",
			"active_staff_count",
			"process_score",
			"process_score_status",
			"first_attempt_sla_rate",
			"followup_sla_rate",
			"connected_rate",
			"valid_outcome_logging_rate",
			"dnc_compliance_rate",
		]
		rows = []
		for r in res.get("rows") or []:
			m = r.get("metrics") or {}
			rows.append(
				[
					r.get("team_id"),
					r.get("team_name"),
					r.get("active_staff_count"),
					(m.get("PROCESS_SCORE") or {}).get("value"),
					(m.get("PROCESS_SCORE") or {}).get("status"),
					(m.get("FIRST_ATTEMPT_SLA_RATE") or {}).get("value"),
					(m.get("FOLLOWUP_SLA_RATE") or {}).get("value"),
					(m.get("CONNECTED_RATE") or {}).get("value"),
					(m.get("VALID_OUTCOME_LOGGING_RATE") or {}).get("value"),
					(m.get("DNC_COMPLIANCE_RATE") or {}).get("value"),
				]
			)
		return headers, rows

	if rtype == "employee_quality":
		res = report_query.get_employee_quality(
			period_type=pt,
			period_key=pk,
			from_date=fd,
			to_date=td,
			team=(job_doc.scope_id if job_doc.scope_type == "team" else None),
			employee=(job_doc.scope_id if job_doc.scope_type == "employee" else None),
			campaign=camp,
			page=1,
			page_size=100,
			actor=actor,
		)
		headers = [
			"employee_id",
			"employee_name",
			"team_id",
			"team_name",
			"intervals_count",
			"work_items_touched",
			"contact_attempt_count",
			"process_score",
			"first_attempt_sla_rate",
			"followup_sla_rate",
			"connected_rate",
		]
		rows = []
		for r in res.get("rows") or []:
			m = r.get("metrics") or {}
			rows.append(
				[
					r.get("employee_id"),
					r.get("employee_name"),
					r.get("team_id"),
					r.get("team_name"),
					r.get("intervals_count"),
					r.get("work_items_touched"),
					r.get("contact_attempt_count"),
					(m.get("PROCESS_SCORE") or {}).get("value"),
					(m.get("FIRST_ATTEMPT_SLA_RATE") or {}).get("value"),
					(m.get("FOLLOWUP_SLA_RATE") or {}).get("value"),
					(m.get("CONNECTED_RATE") or {}).get("value"),
				]
			)
		return headers, rows

	if rtype == "campaign_quality":
		res = report_query.get_campaign_quality(
			period_type=pt,
			period_key=pk,
			from_date=fd,
			to_date=td,
			campaign=(job_doc.scope_id if job_doc.scope_type == "campaign" else camp),
			team=(job_doc.scope_id if job_doc.scope_type == "team" else None),
			page=1,
			page_size=100,
			actor=actor,
		)
		headers = [
			"campaign_id",
			"campaign_name",
			"distinct_work_items",
			"ownership_intervals_count",
			"contact_attempt_count",
			"work_item_first_attempt_sla_rate",
			"first_attempt_sla_rate",
			"followup_sla_rate",
			"connected_rate",
			"process_score",
		]
		rows = []
		for r in res.get("rows") or []:
			m = r.get("metrics") or {}
			rows.append(
				[
					r.get("campaign_id"),
					r.get("campaign_name"),
					r.get("distinct_work_items"),
					r.get("ownership_intervals_count"),
					r.get("contact_attempt_count"),
					(m.get("WORK_ITEM_FIRST_ATTEMPT_SLA_RATE") or {}).get("value"),
					(m.get("FIRST_ATTEMPT_SLA_RATE") or {}).get("value"),
					(m.get("FOLLOWUP_SLA_RATE") or {}).get("value"),
					(m.get("CONNECTED_RATE") or {}).get("value"),
					(m.get("PROCESS_SCORE") or {}).get("value"),
				]
			)
		return headers, rows

	# `metric_drilldown`: paginate across pages up to `MAX_EXPORT_ROWS`
	include_pii_flag = bool(payload.get("include_pii")) and bool(cint(job_doc.includes_pii))
	bucket = payload.get("filter_bucket") or "all_denominator"
	all_items: list[dict] = []
	cur_page = 1
	while True:
		res = report_query.get_metric_drilldown(
			metric_code=job_doc.metric_code,
			period_type=pt,
			period_key=pk,
			from_date=fd,
			to_date=td,
			scope_type=job_doc.scope_type,
			scope_id=job_doc.scope_id,
			campaign=camp,
			filter_bucket=bucket,
			include_pii=include_pii_flag if include_pii_flag else None,
			page=cur_page,
			page_size=100,
			actor=actor,
		)
		total = int((res.get("pagination") or {}).get("total") or 0)
		if total > MAX_EXPORT_ROWS:
			raise TooManyRows(
				f"Tập dữ liệu xuất vượt ngưỡng tối đa {MAX_EXPORT_ROWS} dòng; vui lòng thu hẹp bộ lọc."
			)
		batch = res.get("rows") or []
		if not batch:
			break
		all_items.extend(batch)
		if len(all_items) >= total:
			break
		cur_page += 1

	if include_pii_flag:
		headers = [
			"record_type",
			"record_id",
			"work_item",
			"campaign",
			"owner_user",
			"team_id",
			"status",
			"in_numerator",
			"lead",
			"customer_name",
			"phone",
			"note_excerpt",
			"pii_redacted_due_to_transfer",
		]
		rows = [
			[
				it.get("record_type"),
				it.get("record_id"),
				it.get("work_item"),
				it.get("campaign"),
				it.get("owner_user"),
				it.get("team_id"),
				it.get("status"),
				it.get("in_numerator"),
				it.get("lead"),
				it.get("customer_name"),
				it.get("phone"),
				it.get("note_excerpt"),
				it.get("pii_redacted_due_to_transfer"),
			]
			for it in all_items
		]
	else:
		headers = [
			"record_type",
			"record_id",
			"work_item",
			"campaign",
			"owner_user",
			"team_id",
			"status",
			"in_numerator",
		]
		rows = [
			[
				it.get("record_type"),
				it.get("record_id"),
				it.get("work_item"),
				it.get("campaign"),
				it.get("owner_user"),
				it.get("team_id"),
				it.get("status"),
				it.get("in_numerator"),
			]
			for it in all_items
		]
	return headers, rows


def execute_export_job_now(export_id: str) -> dict:
	"""Stage 2 Worker: revalidate live roles/subtree, build sanitized CSV, attach private `File` owned by `Administrator`."""
	from crm_cskh.services.p3_flags import is_p3_reporting_enabled

	if not is_p3_reporting_enabled():
		return {
			"ok": True,
			"skipped": True,
			"reason": "p3_reporting_disabled",
			"export_id": export_id,
		}
	if not frappe.db.exists("CSKH Export Job", export_id):
		return {"ok": False, "status": "not_found", "export_id": export_id}

	job_doc = frappe.get_doc("CSKH Export Job", export_id)
	if job_doc.status in ("completed", "revoked", "expired"):
		return {"ok": True, "status": job_doc.status, "export_id": export_id}

	if job_doc.expires_at and now_datetime() >= get_datetime(job_doc.expires_at):
		with authorized_export_write():
			job_doc.status = "expired"
			job_doc.save(ignore_permissions=True)
		frappe.db.commit()
		return {"ok": False, "status": "expired", "export_id": export_id}

	# Stage 2 LIVE permission revalidation against `job_doc.requested_by`!
	ok_live, _ = is_export_job_currently_authorized(job_doc.as_dict(), user=job_doc.requested_by)
	if not ok_live:
		with authorized_export_write():
			job_doc.status = "revoked"
			job_doc.error_summary = "Quyền phạm vi của người yêu cầu đã thay đổi trước khi worker xử lý."
			job_doc.save(ignore_permissions=True)
		frappe.db.commit()
		return {"ok": False, "status": "revoked", "export_id": export_id}

	with authorized_export_write():
		job_doc.status = "running"
		job_doc.save(ignore_permissions=True)
	frappe.db.commit()

	prev_user = frappe.session.user
	try:
		payload = json.loads(job_doc.filter_payload_json or "{}")
		frappe.set_user(job_doc.requested_by)
		headers, rows = _build_export_table(job_doc, payload)
		csv_bytes = render_csv_bytes(headers, rows)

		frappe.set_user("Administrator")
		fname = f"cskh_export_{export_id.lower()}_{secrets.token_hex(16)}.csv"
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": fname,
				"is_private": 1,
				"content": csv_bytes,
				"attached_to_doctype": "CSKH Export Job",
				"attached_to_name": export_id,
			}
		)
		file_doc.insert(ignore_permissions=True)
		# Guarantee `File.owner == "Administrator"` so Frappe's `File.has_permission` NEVER short-circuits on `owner`!
		frappe.db.set_value("File", file_doc.name, "owner", "Administrator", update_modified=False)

		with authorized_export_write():
			job_doc.reload()
			job_doc.status = "completed"
			job_doc.row_count = len(rows)
			job_doc.completed_at = now_datetime()
			job_doc.file_doc = file_doc.name
			job_doc.file_url = file_doc.file_url
			job_doc.save(ignore_permissions=True)
		frappe.db.commit()
		return {
			"ok": True,
			"export_id": export_id,
			"status": "completed",
			"row_count": len(rows),
		}
	except Exception as exc:
		frappe.set_user("Administrator")
		with authorized_export_write():
			job_doc.reload()
			job_doc.status = "failed"
			job_doc.error_summary = str(exc)[:400]
			job_doc.save(ignore_permissions=True)
		frappe.db.commit()
		return {"ok": False, "export_id": export_id, "status": "failed"}
	finally:
		frappe.set_user(prev_user)


def get_export_status(export_id: str, *, actor: str | None = None) -> dict:
	"""Return sanitized job metadata (`0` raw `file_url`) after checking live ownership/scope."""
	act = report_query.resolve_report_actor(actor)
	eid = parse_optional_str(export_id, "Tham số export_id không hợp lệ.")
	if not eid:
		raise InvalidRequest("Tham số export_id là bắt buộc.")

	job_row = frappe.db.get_value(
		"CSKH Export Job",
		eid,
		[
			"name",
			"report_type",
			"period_type",
			"period_key",
			"scope_type",
			"scope_id",
			"includes_pii",
			"requested_by",
			"status",
			"row_count",
			"requested_at",
			"completed_at",
			"expires_at",
		],
		as_dict=True,
	)
	if not job_row:
		if act.role_profile == "crm_admin":
			raise NotFound("Không tìm thấy tiến trình xuất báo cáo.")
		raise ForbiddenScope("Bạn không có quyền truy cập tiến trình xuất báo cáo này.")

	if act.role_profile != "crm_admin" and job_row.requested_by != act.user:
		raise ForbiddenScope("Bạn không có quyền xem tiến trình xuất báo cáo của người khác.")

	ok_live, _ = is_export_job_currently_authorized(job_row, user=act.user)
	if not ok_live:
		raise ForbiddenScope("Phạm vi quyền hiện tại của bạn không còn hiệu lực đối với bản xuất này.")

	cur_status = job_row.status
	if cur_status in ("queued", "running", "completed") and job_row.expires_at:
		if now_datetime() >= get_datetime(job_row.expires_at):
			cur_status = "expired"
			frappe.db.set_value("CSKH Export Job", eid, "status", "expired", update_modified=False)

	return {
		"ok": True,
		"export_id": job_row.name,
		"report_type": job_row.report_type,
		"status": cur_status,
		"row_count": int(job_row.row_count or 0),
		"requested_at": format_calendar_iso(job_row.requested_at) if job_row.requested_at else None,
		"completed_at": format_calendar_iso(job_row.completed_at) if job_row.completed_at else None,
		"expires_at": format_calendar_iso(job_row.expires_at) if job_row.expires_at else None,
		"download_endpoint": (
			f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={job_row.name}"
			if cur_status == "completed"
			else None
		),
	}


def download_export(export_id: str, *, actor: str | None = None) -> dict:
	"""Stage 3 Download: revalidate live permissions + TTL (`410 EXPORT_EXPIRED` / `403 FORBIDDEN_SCOPE`) and stream CSV."""
	act = report_query.resolve_report_actor(actor)
	from crm_cskh.services.p3_flags import is_p3_export_enabled

	if not is_p3_export_enabled():
		raise PolicyConflict("Chức năng báo cáo và xuất dữ liệu P3 hiện đang tạm tắt theo cờ cấu hình.")

	eid = parse_optional_str(export_id, "Tham số export_id không hợp lệ.")
	if not eid:
		raise InvalidRequest("Tham số export_id là bắt buộc.")

	job_row = frappe.db.get_value(
		"CSKH Export Job",
		eid,
		[
			"name",
			"report_type",
			"scope_type",
			"scope_id",
			"includes_pii",
			"requested_by",
			"status",
			"row_count",
			"expires_at",
			"file_doc",
			"file_url",
		],
		as_dict=True,
	)
	if not job_row:
		if act.role_profile == "crm_admin":
			raise NotFound("Không tìm thấy tiến trình xuất báo cáo.")
		raise ForbiddenScope("Bạn không có quyền tải tệp xuất báo cáo này.")

	if act.role_profile != "crm_admin" and job_row.requested_by != act.user:
		raise ForbiddenScope("Bạn không có quyền tải tệp xuất báo cáo của người khác.")

	# Stage 3 LIVE permission revalidation BEFORE checking file/expiry so revoked actors always get `403 FORBIDDEN_SCOPE`
	ok_live, _ = is_export_job_currently_authorized(job_row, user=act.user)
	if not ok_live or job_row.status == "revoked":
		if job_row.status not in ("revoked", "expired"):
			frappe.db.set_value("CSKH Export Job", eid, "status", "revoked", update_modified=False)
		raise ForbiddenScope("Quyền truy cập phạm vi dữ liệu của bản xuất này đã bị thu hồi.")

	# TTL enforcement (`410 EXPORT_EXPIRED`)
	if job_row.status == "expired" or (
		job_row.expires_at and now_datetime() >= get_datetime(job_row.expires_at)
	):
		if job_row.status != "expired":
			frappe.db.set_value("CSKH Export Job", eid, "status", "expired", update_modified=False)
		raise ExportExpired("Liên kết tải báo cáo đã hết hạn hiệu lực (TTL). Vui lòng tạo yêu cầu xuất mới.")

	if job_row.status != "completed" or not job_row.file_doc:
		raise Conflict("Tiến trình xuất báo cáo chưa hoàn tất hoặc đã gặp lỗi.")

	file_doc = frappe.get_doc("File", job_row.file_doc)
	content = file_doc.get_content()
	if isinstance(content, str):
		content_bytes = content.encode("utf-8")
	else:
		content_bytes = bytes(content)

	# Populate Frappe binary download response when serving an HTTP request
	if getattr(frappe.local, "response", None) is not None:
		frappe.local.response["filename"] = f"{job_row.name}.csv"
		frappe.local.response["filecontent"] = content_bytes
		frappe.local.response["type"] = "download"
		frappe.local.response["content_type"] = "text/csv; charset=utf-8"

	return {
		"ok": True,
		"export_id": job_row.name,
		"filename": f"{job_row.name}.csv",
		"row_count": int(job_row.row_count or 0),
		"byte_length": len(content_bytes),
	}


def cleanup_expired_exports() -> dict:
	"""Mark expired `CSKH Export Job` rows `expired` and purge underlying private CSV files from disk."""
	if not frappe.db.table_exists("CSKH Export Job"):
		return {"expired_marked": 0, "files_removed": 0}
	now_dt = now_datetime()
	stale = frappe.db.sql(
		"""
		select name, file_doc, file_url
		from `tabCSKH Export Job`
		where status in ('queued', 'running', 'completed', 'expired', 'revoked')
		  and expires_at <= %s
		""",
		(now_dt,),
		as_dict=True,
	)
	marked = 0
	removed = 0
	for r in stale:
		frappe.db.set_value(
			"CSKH Export Job",
			r.name,
			{"status": "expired", "file_url": None},
			update_modified=False,
		)
		marked += 1
		if r.file_url and r.file_url.startswith("/private/files/"):
			rel = r.file_url.lstrip("/")
			full_path = Path(frappe.get_site_path(rel))
			if full_path.is_file():
				try:
					os.remove(full_path)
					removed += 1
				except OSError:
					pass
	if marked:
		frappe.db.commit()
	return {"expired_marked": marked, "files_removed": removed}
