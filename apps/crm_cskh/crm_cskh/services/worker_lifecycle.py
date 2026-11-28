"""KCN Worker Profile updates, 7-Workday Attendance Lifecycle, and End-of-Day Acceptance Board.

Implements the locked KCN Marketing workflow:
  * Marketing staff (`assigned_to == actor`) or `CSKH CRM Admin` updates worker screening
    profile fields (`full_name`, `birth_year`, `gender`, `current_area`, `id_card_status`,
    `worker_tags`, `zalo_phone`, `target_factory`, `target_industrial_zone`) directly.
  * Every lifecycle milestone (`schedule_interview`, `interview_no_show`, `switch_factory`,
    `onboard`, `checkin_workday`, `dropout`) records an immutable `CSKH Contact Attempt`
    while keeping `CSKH Work Item` active & protected until **7 workdays (`workdays_completed >= 7`)**.
  * Reaching 7 workdays (`7 ngày công đi làm`) stamps `completed_7d_at`, sets
    `worker_stage = 'completed_7_days'`, and closes the Work Item into permanent staff credit.
  * End-of-Day Acceptance Board (`get_daily_acceptance_board`) surfaces pending end-of-day
    confirmations for Marketing staff and aggregate/verifiable acceptance tables for Admin/BOD
    (`0 PII` strictly enforced for `CSKH BOD Viewer`).
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime

from crm_cskh.api._params import parse_bool, parse_int, parse_optional_str
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p2_permissions import ROLE_ADMIN, ROLE_BOD, _is_staff, _is_system, _roles
from crm_cskh.services import contact_attempt, reporting, runtime_cache
from crm_cskh.services.errors import (
	Conflict,
	Forbidden,
	InvalidRequest,
	NotFound,
	OwnershipChanged,
	WorkItemNotActive,
)
from crm_cskh.services.import_service import looks_like_formula
from crm_cskh.services.sanitizer import sanitize_note_text
from crm_cskh.services.worker_intake import (
	_apply_lead_profile_fields,
	_clean_profile_inputs,
	to_national_phone,
)

WORKER_STAGES = (
	"new_contact",
	"consulting",
	"interview_scheduled",
	"working_care",
	"completed_7_days",
	"dropped_early",
	"stopped",
)

DEFAULT_TARGET_WORKDAYS = 7


def _assert_owner_or_admin_mutation(work_item: str, actor: str | None = None) -> tuple[str, dict]:
	"""Enforce 2-tier mutation rule: ONLY current `assigned_to` (`Sales User`) or `CSKH CRM Admin` / `System Manager`."""
	actor = actor or frappe.session.user
	if not actor or actor == "Guest":
		raise Forbidden("Bạn cần đăng nhập để thực hiện thao tác này.")

	roles = _roles(actor)
	is_admin = _is_system(actor) or ROLE_ADMIN in roles
	if not is_admin and not _is_staff(actor):
		raise Forbidden("Chỉ Nhân viên Marketing phụ trách hoặc Quản trị viên mới được cập nhật kết quả.")

	cols = [
		"name",
		"lead",
		"campaign",
		"status",
		"revision",
		"assigned_to",
		"open_callback_task",
	]
	for opt in (
		"worker_stage",
		"interview_date",
		"onboard_date",
		"workdays_completed",
		"target_workdays",
		"last_workday_logged_on",
		"target_factory",
		"target_industrial_zone",
	):
		if frappe.db.has_column("CSKH Work Item", opt):
			cols.append(opt)

	wi = frappe.db.get_value("CSKH Work Item", work_item, cols, as_dict=True)
	if not wi:
		if is_admin:
			raise NotFound("Không tìm thấy hồ sơ chăm sóc.")
		raise Forbidden("Bạn không có quyền truy cập hồ sơ chăm sóc này.")

	if wi.status not in OWNED_ACTIVE_WORK_ITEM_STATES or not wi.assigned_to:
		if is_admin or wi.get("assigned_to") == actor:
			raise WorkItemNotActive("Hồ sơ chăm sóc đã đóng hoặc thu hồi, không thể cập nhật thêm.")
		raise Forbidden("Bạn không có quyền thao tác trên hồ sơ chăm sóc này.")

	if not is_admin and wi.assigned_to != actor:
		raise OwnershipChanged("Hồ sơ người lao động này không thuộc quyền phụ trách hiện tại của bạn.")

	return actor, wi


def _parse_iso_date(raw_val: str | None, field_label: str) -> date | None:
	cleaned = parse_optional_str(raw_val, f"{field_label} không hợp lệ.")
	if not cleaned:
		return None
	try:
		return date.fromisoformat(cleaned[:10])
	except ValueError as exc:
		raise InvalidRequest(f"{field_label} phải có định dạng YYYY-MM-DD.") from exc


def update_worker_profile(
	*,
	work_item: str | None,
	full_name: str | None = None,
	birth_year=None,
	gender: str | None = None,
	current_area: str | None = None,
	id_card_status: str | None = None,
	worker_tags: str | None = None,
	zalo_phone: str | None = None,
	target_factory: str | None = None,
	target_industrial_zone: str | None = None,
	campaign: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Allow the assigned Marketing staff (or Admin) to update worker KCN screening details inline."""
	wi_id = parse_optional_str(work_item, "Mã hồ sơ chăm sóc không hợp lệ.")
	if not wi_id:
		raise InvalidRequest("Mã hồ sơ chăm sóc (work_item) là bắt buộc.")
	actor, wi = _assert_owner_or_admin_mutation(wi_id, actor=actor)

	cleaned = _clean_profile_inputs(
		full_name=full_name,
		birth_year=birth_year,
		gender=gender,
		current_area=current_area,
		id_card_status=id_card_status,
		worker_tags=worker_tags,
		zalo_phone=zalo_phone,
		target_factory=target_factory,
		target_industrial_zone=target_industrial_zone,
	)

	_apply_lead_profile_fields(wi.lead, cleaned, overwrite_existing=True)

	# Clear empty optional fields if explicitly passed as empty string by user
	explicit_clears: dict = {}
	for param_val, db_col in (
		(gender, "cskh_gender"),
		(current_area, "cskh_current_area"),
		(id_card_status, "cskh_id_card_status"),
		(worker_tags, "cskh_worker_tags"),
		(zalo_phone, "cskh_zalo_phone"),
	):
		if (
			param_val is not None
			and str(param_val).strip() == ""
			and frappe.db.has_column("CRM Lead", db_col)
		):
			explicit_clears[db_col] = None
	if birth_year == "" and frappe.db.has_column("CRM Lead", "cskh_birth_year"):
		explicit_clears["cskh_birth_year"] = None
	if explicit_clears:
		frappe.db.set_value("CRM Lead", wi.lead, explicit_clears, update_modified=True)

	wi_updates: dict = {}
	if target_factory is not None and frappe.db.has_column("CSKH Work Item", "target_factory"):
		wi_updates["target_factory"] = cleaned["target_factory"] or None
	if target_industrial_zone is not None and frappe.db.has_column(
		"CSKH Work Item", "target_industrial_zone"
	):
		wi_updates["target_industrial_zone"] = cleaned["target_industrial_zone"] or None

	camp_val = parse_optional_str(campaign, "Mã đơn hàng KCN không hợp lệ.")
	if camp_val and camp_val != wi.campaign and frappe.db.exists("CSKH Campaign", camp_val):
		camp_row = frappe.db.get_value(
			"CSKH Campaign",
			camp_val,
			[
				c
				for c in ("name", "title", "factory_name", "industrial_zone")
				if frappe.db.has_column("CSKH Campaign", c) or c == "name"
			],
			as_dict=True,
		)
		if camp_row:
			if (
				target_factory is None
				and camp_row.get("factory_name")
				and frappe.db.has_column("CSKH Work Item", "target_factory")
			):
				wi_updates["target_factory"] = camp_row.get("factory_name")
			if (
				target_industrial_zone is None
				and camp_row.get("industrial_zone")
				and frappe.db.has_column("CSKH Work Item", "target_industrial_zone")
			):
				wi_updates["target_industrial_zone"] = camp_row.get("industrial_zone")

	if wi_updates:
		wi_updates["last_activity_by"] = actor
		frappe.db.set_value("CSKH Work Item", wi.name, wi_updates, update_modified=True)
		runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_WORK_ITEM)

	lead_row = (
		frappe.db.get_value(
			"CRM Lead",
			wi.lead,
			["lead_name", "first_name", "mobile_no"],
			as_dict=True,
		)
		or {}
	)
	return {
		"status": "updated",
		"work_item": wi.name,
		"lead": wi.lead,
		"display_name": lead_row.get("lead_name") or lead_row.get("first_name") or wi.lead,
		"national_phone": to_national_phone(lead_row.get("mobile_no")),
		"target_factory": wi_updates.get("target_factory", wi.get("target_factory")) or "",
		"target_industrial_zone": wi_updates.get("target_industrial_zone", wi.get("target_industrial_zone"))
		or "",
	}


def sync_stage_after_attempt_in_tx(
	*,
	work_item: str,
	outcome_code: str,
	should_close: bool,
	now_dt: datetime,
) -> dict:
	"""Keep `CSKH Work Item.worker_stage` & `completed_7d_at` consistent when `record_attempt` runs directly."""
	if not frappe.db.has_column("CSKH Work Item", "worker_stage"):
		return {}

	code = (outcome_code or "").strip().lower()
	cur = (
		frappe.db.get_value(
			"CSKH Work Item",
			work_item,
			[
				c
				for c in (
					"worker_stage",
					"workdays_completed",
					"target_workdays",
					"interview_date",
					"onboard_date",
					"completed_7d_at",
				)
				if frappe.db.has_column("CSKH Work Item", c)
			],
			as_dict=True,
		)
		or {}
	)

	cur_stage = cur.get("worker_stage") or "new_contact"
	cur_days = cint(cur.get("workdays_completed") or 0)
	target_days = max(cint(cur.get("target_workdays") or DEFAULT_TARGET_WORKDAYS), 1)
	today = getdate(now_dt)
	updates: dict = {}

	if code == "completed_7_days":
		updates["worker_stage"] = "completed_7_days"
		updates["workdays_completed"] = max(cur_days, target_days)
		updates["last_workday_logged_on"] = today
		updates["completed_7d_at"] = cur.get("completed_7d_at") or now_dt
	elif should_close:
		if cur_days >= target_days:
			updates["worker_stage"] = "completed_7_days"
			updates["completed_7d_at"] = cur.get("completed_7d_at") or now_dt
		else:
			updates["worker_stage"] = "stopped"
	elif code == "interview_scheduled":
		updates["worker_stage"] = "interview_scheduled"
		if not cur.get("interview_date"):
			updates["interview_date"] = today + timedelta(days=1)
	elif code == "onboarded_working":
		updates["worker_stage"] = "working_care"
		if not cur.get("onboard_date"):
			updates["onboard_date"] = today
		if cur_days < 1:
			updates["workdays_completed"] = 1
			updates["last_workday_logged_on"] = today
	elif code == "workday_checkin":
		updates["worker_stage"] = "working_care"
	elif code == "dropped_before_7_days":
		updates["worker_stage"] = "dropped_early"
	elif code in ("interview_no_show", "interview_failed_switch"):
		updates["worker_stage"] = "consulting"
	elif cur_stage == "new_contact":
		updates["worker_stage"] = "consulting"

	if updates:
		frappe.db.set_value("CSKH Work Item", work_item, updates, update_modified=False)
	return updates


def record_worker_progress(
	*,
	work_item: str | None,
	action_type: str | None,
	channel: str = "zalo",
	note: str | None = None,
	interview_date: str | None = None,
	onboard_date: str | None = None,
	workdays_completed=None,
	increment_workday=False,
	target_factory: str | None = None,
	target_industrial_zone: str | None = None,
	followup_due_at: str | None = None,
	dropout_reason: str | None = None,
	close_after_dropout=False,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Single-action KCN progress & workday check-in service backed by immutable `CSKH Contact Attempt`."""
	wi_id = parse_optional_str(work_item, "Mã hồ sơ chăm sóc không hợp lệ.")
	if not wi_id:
		raise InvalidRequest("Mã hồ sơ chăm sóc (work_item) là bắt buộc.")
	act_key = (parse_optional_str(action_type, "Loại cập nhật không hợp lệ.") or "").strip().lower()
	if act_key not in (
		"schedule_interview",
		"interview_no_show",
		"switch_factory",
		"onboard",
		"checkin_workday",
		"dropout",
	):
		raise InvalidRequest("Loại cập nhật tiến độ người lao động không hợp lệ.")

	actor, wi = _assert_owner_or_admin_mutation(wi_id, actor=actor)
	idem = (
		parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
		or f"wprog-{frappe.generate_hash(length=12)}"
	)
	ch_val = (parse_optional_str(channel, "Kênh liên hệ không hợp lệ.") or "zalo").strip().lower()
	clean_note = sanitize_note_text(parse_optional_str(note, "Ghi chú không hợp lệ."), max_length=2000)
	fac_val = parse_optional_str(target_factory, "Nhà máy không hợp lệ.")
	iz_val = parse_optional_str(target_industrial_zone, "Khu công nghiệp không hợp lệ.")

	now_dt = now_datetime()
	today = getdate(now_dt)
	target_days = max(cint(wi.get("target_workdays") or DEFAULT_TARGET_WORKDAYS), 1)
	cur_days = cint(wi.get("workdays_completed") or 0)

	extra_wi_updates: dict = {}
	if fac_val is not None and str(fac_val).strip():
		extra_wi_updates["target_factory"] = str(fac_val).strip()[:140]
	if iz_val is not None and str(iz_val).strip():
		extra_wi_updates["target_industrial_zone"] = str(iz_val).strip()[:140]

	should_close = False
	completion_reason = None

	if act_key == "schedule_interview":
		iv_date = _parse_iso_date(interview_date, "Ngày phỏng vấn") or (today + timedelta(days=1))
		outcome_code = "interview_scheduled"
		extra_wi_updates["worker_stage"] = "interview_scheduled"
		extra_wi_updates["interview_date"] = iv_date
		eff_note = clean_note or f"Chốt lịch phỏng vấn KCN ngày {iv_date.strftime('%d/%m/%Y')}"
	elif act_key == "interview_no_show":
		outcome_code = "interview_no_show"
		extra_wi_updates["worker_stage"] = "consulting"
		eff_note = clean_note or "Người lao động chưa ra điểm phỏng vấn theo hẹn — cần gọi lại."
	elif act_key == "switch_factory":
		outcome_code = "interview_failed_switch"
		extra_wi_updates["worker_stage"] = "consulting"
		eff_note = clean_note or "Chuyển tư vấn sang đơn nhà máy KCN khác phù hợp hơn."
	elif act_key == "onboard":
		ob_date = _parse_iso_date(onboard_date, "Ngày vào xưởng") or today
		init_days = (
			parse_int(workdays_completed, minimum=1, field="workdays_completed")
			if workdays_completed is not None and str(workdays_completed).strip() != ""
			else max(cur_days, 1)
		)
		extra_wi_updates["onboard_date"] = ob_date
		extra_wi_updates["last_workday_logged_on"] = today
		if init_days >= target_days:
			outcome_code = "completed_7_days"
			extra_wi_updates["workdays_completed"] = target_days
			extra_wi_updates["worker_stage"] = "completed_7_days"
			extra_wi_updates["completed_7d_at"] = now_dt
			should_close = True
			completion_reason = f"Đạt đủ {target_days} ngày công đi làm — Chốt nghiệm thu"
			eff_note = clean_note or completion_reason
		else:
			outcome_code = "onboarded_working"
			extra_wi_updates["workdays_completed"] = init_days
			extra_wi_updates["worker_stage"] = "working_care"
			eff_note = (
				clean_note
				or f"Đã nhận việc vào xưởng ngày {ob_date.strftime('%d/%m/%Y')} ({init_days}/{target_days} ngày công)."
			)
	elif act_key == "checkin_workday":
		inc_flag = parse_bool(increment_workday, default=True, field="increment_workday")
		if workdays_completed is not None and str(workdays_completed).strip() != "":
			next_days = parse_int(workdays_completed, minimum=1, field="workdays_completed")
		elif inc_flag:
			last_logged = wi.get("last_workday_logged_on")
			if last_logged and getdate(last_logged) == today and cur_days > 0:
				raise Conflict(
					f"Hôm nay bạn đã điểm công ({cur_days}/{target_days} công) cho người lao động này rồi.",
					code="CONFLICT",
				)
			next_days = cur_days + 1
		else:
			next_days = max(cur_days, 1)

		next_days = min(next_days, target_days)
		if not wi.get("onboard_date"):
			extra_wi_updates["onboard_date"] = today
		extra_wi_updates["workdays_completed"] = next_days
		extra_wi_updates["last_workday_logged_on"] = today

		if next_days >= target_days:
			outcome_code = "completed_7_days"
			extra_wi_updates["worker_stage"] = "completed_7_days"
			extra_wi_updates["completed_7d_at"] = now_dt
			should_close = True
			completion_reason = (
				f"Đạt đủ {target_days}/{target_days} ngày công đi làm — Chốt nghiệm thu cuối ngày"
			)
			eff_note = clean_note or completion_reason
		else:
			outcome_code = "workday_checkin"
			extra_wi_updates["worker_stage"] = "working_care"
			eff_note = clean_note or f"Nghiệm thu cuối ngày: Đạt {next_days}/{target_days} ngày công đi làm."
	else:
		# `dropout`
		close_flag = parse_bool(close_after_dropout, default=False, field="close_after_dropout")
		reason_clean = sanitize_note_text(
			parse_optional_str(dropout_reason, "Lý do nghỉ không hợp lệ.") or clean_note,
			max_length=1000,
		)
		if not reason_clean:
			raise InvalidRequest("Vui lòng nhập lý do người lao động nghỉ sớm trước 7 ngày công.")
		outcome_code = "dropped_before_7_days"
		extra_wi_updates["dropout_reason"] = reason_clean
		extra_wi_updates["worker_stage"] = "stopped" if close_flag else "dropped_early"
		should_close = close_flag
		completion_reason = f"Nghỉ trước {target_days} ngày công: {reason_clean}" if close_flag else None
		eff_note = reason_clean

	att_res = contact_attempt.record_attempt(
		work_item=wi.name,
		channel=ch_val,
		outcome_code=outcome_code,
		note=eff_note,
		followup_due_at=None if should_close else followup_due_at,
		complete_open_followup=True,
		close_work_item=should_close,
		completion_reason=completion_reason,
		idempotency_key=idem,
		actor=actor,
	)

	valid_wi_updates = {
		col: val for col, val in extra_wi_updates.items() if frappe.db.has_column("CSKH Work Item", col)
	}
	if valid_wi_updates:
		frappe.db.set_value("CSKH Work Item", wi.name, valid_wi_updates, update_modified=True)
		frappe.db.commit()
		runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_WORK_ITEM)

	return {
		**att_res,
		"worker_stage": extra_wi_updates.get("worker_stage") or wi.get("worker_stage") or "consulting",
		"workdays_completed": cint(extra_wi_updates.get("workdays_completed", cur_days)),
		"target_workdays": target_days,
		"interview_date": str(extra_wi_updates["interview_date"])
		if extra_wi_updates.get("interview_date")
		else (str(wi.get("interview_date")) if wi.get("interview_date") else None),
		"onboard_date": str(extra_wi_updates["onboard_date"])
		if extra_wi_updates.get("onboard_date")
		else (str(wi.get("onboard_date")) if wi.get("onboard_date") else None),
		"completed_7_days": bool(should_close and outcome_code == "completed_7_days"),
	}


def _day_bounds_in_site_tz(target_date_str: str | None = None) -> tuple[date, datetime, datetime, str]:
	site_tz_name = runtime_cache.get_cached_system_timezone() or "Asia/Ho_Chi_Minh"
	try:
		tz = ZoneInfo(site_tz_name)
	except Exception:
		site_tz_name = "Asia/Ho_Chi_Minh"
		tz = ZoneInfo("Asia/Ho_Chi_Minh")

	if target_date_str:
		d = _parse_iso_date(target_date_str, "Ngày nghiệm thu")
	else:
		now_aware = datetime.now(tz)
		d = now_aware.date()

	assert d is not None
	start_dt = datetime.combine(d, time.min)
	end_dt = datetime.combine(d + timedelta(days=1), time.min)
	return d, start_dt, end_dt, site_tz_name


def get_daily_acceptance_board(
	*,
	target_date: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	campaign: str | None = None,
	actor: str | None = None,
) -> dict:
	"""End-of-Day Acceptance Board & 7-Workday Funnel for Marketing Staff, Admin, and BOD (0-PII)."""
	actor = actor or frappe.session.user
	if not actor or actor == "Guest":
		raise Forbidden("Bạn cần đăng nhập để xem bảng nghiệm thu.")

	roles = _roles(actor)
	is_sys_or_admin = _is_system(actor) or ROLE_ADMIN in roles
	is_bod_only = (ROLE_BOD in roles) and not is_sys_or_admin and not _is_staff(actor)
	if not (is_sys_or_admin or is_bod_only or _is_staff(actor) or "Sales Manager" in roles):
		raise Forbidden("Tài khoản không có quyền xem bảng nghiệm thu chăm sóc người lao động.")

	allow_pii = bool(is_sys_or_admin or (_is_staff(actor) and not is_bod_only))
	camp_val = parse_optional_str(campaign, "Mã đơn hàng KCN không hợp lệ.")

	day_date, day_start, day_end, site_tz = _day_bounds_in_site_tz(target_date)
	if date_from or date_to:
		p_from = _parse_iso_date(date_from, "Từ ngày") or day_date
		p_to = _parse_iso_date(date_to, "Đến ngày") or day_date
		if p_from > p_to:
			raise InvalidRequest("Từ ngày không được lớn hơn Đến ngày.")
		period_start = datetime.combine(p_from, time.min)
		period_end = datetime.combine(p_to + timedelta(days=1), time.min)
	else:
		p_from = day_date
		p_to = day_date
		period_start = day_start
		period_end = day_end

	has_stage = frappe.db.has_column("CSKH Work Item", "worker_stage")
	if not has_stage:
		return {
			"scope": "all" if (is_sys_or_admin or is_bod_only) else "self",
			"allow_pii": allow_pii,
			"date": day_date.isoformat(),
			"period": {"from": p_from.isoformat(), "to": p_to.isoformat()},
			"timezone": site_tz,
			"summary": {},
			"pending_interviews": [],
			"pending_workdays": [],
			"completed_7d_items": [],
			"per_staff": [],
			"as_of": now_datetime(),
		}

	scope = reporting._scope(actor)
	is_global = scope["scope"] in ("system", "all")

	camp_sql = " and wi.campaign = %s" if camp_val else ""
	camp_params = (camp_val,) if camp_val else ()

	if is_global:
		active_owner_sql = ""
		active_owner_params: tuple = ()
		closed_owner_sql = ""
		closed_owner_params: tuple = ()
	else:
		# Marketing staff strict self scope
		active_owner_sql = " and wi.assigned_to = %s"
		active_owner_params = (actor,)
		closed_owner_sql = " and coalesce(nullif(wi.owner_at_close, ''), wi.closed_by) = %s"
		closed_owner_params = (actor,)

	# 1. Aggregate summary across scope
	active_agg = frappe.db.sql(
		f"""
		select
			count(*) as active_total,
			sum(case when coalesce(wi.attempt_count, 0) = 0 then 1 else 0 end) as unattempted,
			sum(case when wi.worker_stage = 'consulting' then 1 else 0 end) as consulting,
			sum(case when wi.worker_stage = 'interview_scheduled' then 1 else 0 end) as interview_scheduled,
			sum(case when wi.worker_stage = 'interview_scheduled' and wi.interview_date <= %s then 1 else 0 end) as interview_due_today,
			sum(case when wi.worker_stage = 'working_care' then 1 else 0 end) as working_1_to_6,
			sum(case when wi.worker_stage = 'working_care' and (wi.last_workday_logged_on is null or wi.last_workday_logged_on < %s) then 1 else 0 end) as workday_unlogged_today,
			sum(case when wi.worker_stage = 'dropped_early' then 1 else 0 end) as dropped_active
		from `tabCSKH Work Item` wi
		where wi.status in ('assigned', 'in_progress', 'on_hold'){camp_sql}{active_owner_sql}
		""",
		(day_date, day_date, *camp_params, *active_owner_params),
		as_dict=True,
	)[0]

	period_agg = frappe.db.sql(
		f"""
		select
			sum(case when wi.creation >= %s and wi.creation < %s and wi.source_type = 'self_sourced' then 1 else 0 end) as self_sourced_period,
			sum(case when wi.creation >= %s and wi.creation < %s then 1 else 0 end) as intake_period,
			sum(case when wi.completed_7d_at >= %s and wi.completed_7d_at < %s and wi.worker_stage = 'completed_7_days' then 1 else 0 end) as completed_7d_period,
			sum(case when wi.completed_7d_at >= %s and wi.completed_7d_at < %s and wi.worker_stage = 'completed_7_days' then 1 else 0 end) as completed_7d_today,
			sum(case when wi.closed_at >= %s and wi.closed_at < %s and wi.worker_stage in ('dropped_early', 'stopped') and coalesce(wi.workdays_completed, 0) > 0 and coalesce(wi.workdays_completed, 0) < 7 then 1 else 0 end) as dropped_closed_period
		from `tabCSKH Work Item` wi
		where 1=1{camp_sql}{closed_owner_sql if not is_global else ""}
		""",
		(
			period_start,
			period_end,
			period_start,
			period_end,
			period_start,
			period_end,
			day_start,
			day_end,
			period_start,
			period_end,
			*camp_params,
			*(closed_owner_params if not is_global else ()),
		),
		as_dict=True,
	)[0]
	if not is_global:
		# For staff, `intake_period` / `self_sourced_period` includes both still-active (`assigned_to = actor`) and closed (`owner_at_close = actor`).
		staff_intake_row = frappe.db.sql(
			f"""
			select
				sum(case when wi.source_type = 'self_sourced' then 1 else 0 end) as self_cnt,
				count(*) as total_cnt
			from `tabCSKH Work Item` wi
			where wi.creation >= %s and wi.creation < %s
			  and (wi.assigned_to = %s or coalesce(nullif(wi.owner_at_close, ''), wi.closed_by) = %s){camp_sql}
			""",
			(period_start, period_end, actor, actor, *camp_params),
			as_dict=True,
		)[0]
		period_agg["self_sourced_period"] = int(staff_intake_row.get("self_cnt") or 0)
		period_agg["intake_period"] = int(staff_intake_row.get("total_cnt") or 0)

	summary = {
		"active_protected": int(active_agg.get("active_total") or 0),
		"unattempted": int(active_agg.get("unattempted") or 0),
		"consulting": int(active_agg.get("consulting") or 0),
		"interview_scheduled": int(active_agg.get("interview_scheduled") or 0),
		"interview_due_today": int(active_agg.get("interview_due_today") or 0),
		"working_1_to_6": int(active_agg.get("working_1_to_6") or 0),
		"workday_unlogged_today": int(active_agg.get("workday_unlogged_today") or 0),
		"intake_period": int(period_agg.get("intake_period") or 0),
		"self_sourced_period": int(period_agg.get("self_sourced_period") or 0),
		"completed_7_days_today": int(period_agg.get("completed_7d_today") or 0),
		"completed_7_days_period": int(period_agg.get("completed_7d_period") or 0),
		"dropped_early": int(active_agg.get("dropped_active") or 0)
		+ int(period_agg.get("dropped_closed_period") or 0),
	}

	pending_interviews = []
	pending_workdays = []
	completed_7d_items = []

	if allow_pii:
		iv_rows = frappe.db.sql(
			f"""
			select wi.name as work_item, wi.lead, wi.campaign, wi.assigned_to,
			       wi.target_factory, wi.target_industrial_zone, wi.interview_date,
			       c.title as campaign_title, c.factory_name, c.industrial_zone,
			       l.lead_name, l.first_name, l.mobile_no
			from `tabCSKH Work Item` wi
			inner join `tabCRM Lead` l on l.name = wi.lead
			left join `tabCSKH Campaign` c on c.name = wi.campaign
			where wi.status in ('assigned', 'in_progress', 'on_hold')
			  and wi.worker_stage = 'interview_scheduled'
			  and (wi.interview_date is null or wi.interview_date <= %s){camp_sql}{active_owner_sql}
			order by coalesce(wi.interview_date, %s) asc, wi.modified desc
			limit 50
			""",
			(day_date, *camp_params, *active_owner_params, day_date),
			as_dict=True,
		)
		for r in iv_rows:
			pending_interviews.append(
				{
					"work_item": r.work_item,
					"lead": r.lead,
					"display_name": r.lead_name or r.first_name or r.lead,
					"national_phone": to_national_phone(r.mobile_no),
					"assigned_to": r.assigned_to,
					"factory": r.target_factory or r.factory_name or r.campaign_title or "",
					"industrial_zone": r.target_industrial_zone or r.industrial_zone or "",
					"interview_date": str(r.interview_date) if r.interview_date else day_date.isoformat(),
				}
			)

		wd_rows = frappe.db.sql(
			f"""
			select wi.name as work_item, wi.lead, wi.campaign, wi.assigned_to,
			       wi.target_factory, wi.target_industrial_zone, wi.onboard_date,
			       coalesce(wi.workdays_completed, 0) as workdays_completed,
			       coalesce(wi.target_workdays, 7) as target_workdays,
			       wi.last_workday_logged_on,
			       c.title as campaign_title, c.factory_name, c.industrial_zone,
			       l.lead_name, l.first_name, l.mobile_no
			from `tabCSKH Work Item` wi
			inner join `tabCRM Lead` l on l.name = wi.lead
			left join `tabCSKH Campaign` c on c.name = wi.campaign
			where wi.status in ('assigned', 'in_progress', 'on_hold')
			  and wi.worker_stage = 'working_care'{camp_sql}{active_owner_sql}
			order by case when wi.last_workday_logged_on is null or wi.last_workday_logged_on < %s then 0 else 1 end asc,
			         coalesce(wi.workdays_completed, 0) desc, wi.modified desc
			limit 50
			""",
			(*camp_params, *active_owner_params, day_date),
			as_dict=True,
		)
		for r in wd_rows:
			logged_today = bool(r.last_workday_logged_on and getdate(r.last_workday_logged_on) >= day_date)
			pending_workdays.append(
				{
					"work_item": r.work_item,
					"lead": r.lead,
					"display_name": r.lead_name or r.first_name or r.lead,
					"national_phone": to_national_phone(r.mobile_no),
					"assigned_to": r.assigned_to,
					"factory": r.target_factory or r.factory_name or r.campaign_title or "",
					"industrial_zone": r.target_industrial_zone or r.industrial_zone or "",
					"onboard_date": str(r.onboard_date) if r.onboard_date else "",
					"workdays_completed": int(r.workdays_completed or 0),
					"target_workdays": max(int(r.target_workdays or 7), 1),
					"logged_today": logged_today,
				}
			)

		c7_rows = frappe.db.sql(
			f"""
			select wi.name as work_item, wi.lead, wi.campaign,
			       coalesce(nullif(wi.owner_at_close, ''), wi.closed_by, wi.assigned_to) as owner_user,
			       wi.target_factory, wi.target_industrial_zone, wi.onboard_date,
			       coalesce(wi.workdays_completed, 7) as workdays_completed,
			       wi.completed_7d_at,
			       c.title as campaign_title, c.factory_name, c.industrial_zone,
			       l.lead_name, l.first_name, l.mobile_no
			from `tabCSKH Work Item` wi
			inner join `tabCRM Lead` l on l.name = wi.lead
			left join `tabCSKH Campaign` c on c.name = wi.campaign
			where wi.worker_stage = 'completed_7_days'
			  and wi.completed_7d_at >= %s and wi.completed_7d_at < %s{camp_sql}{closed_owner_sql}
			order by wi.completed_7d_at desc
			limit 50
			""",
			(period_start, period_end, *camp_params, *closed_owner_params),
			as_dict=True,
		)
		for r in c7_rows:
			completed_7d_items.append(
				{
					"work_item": r.work_item,
					"lead": r.lead,
					"display_name": r.lead_name or r.first_name or r.lead,
					"national_phone": to_national_phone(r.mobile_no),
					"owner_user": r.owner_user,
					"factory": r.target_factory or r.factory_name or r.campaign_title or "",
					"industrial_zone": r.target_industrial_zone or r.industrial_zone or "",
					"onboard_date": str(r.onboard_date) if r.onboard_date else "",
					"workdays_completed": int(r.workdays_completed or 7),
					"completed_7d_at": r.completed_7d_at,
				}
			)

	# 2. Per-Marketing-Staff breakdown for Admin / BOD (contains 0 worker PII!)
	per_staff = []
	if is_global:
		staff_map: dict[str, dict] = {}

		def _slot(u: str) -> dict:
			if u not in staff_map:
				full_n = frappe.db.get_value("User", u, "full_name") or u
				staff_map[u] = {
					"user": u,
					"full_name": full_n,
					"self_sourced_period": 0,
					"active_protected": 0,
					"connected_active": 0,
					"interview_scheduled": 0,
					"working_1_to_6": 0,
					"completed_7_days_today": 0,
					"completed_7_days_period": 0,
					"dropped_period": 0,
				}
			return staff_map[u]

		for r in frappe.db.sql(
			f"""
			select wi.assigned_to as u,
			       count(*) as active_cnt,
			       sum(case when coalesce(wi.connected_attempt_count, 0) > 0 then 1 else 0 end) as conn_cnt,
			       sum(case when wi.worker_stage = 'interview_scheduled' then 1 else 0 end) as iv_cnt,
			       sum(case when wi.worker_stage = 'working_care' then 1 else 0 end) as wk_cnt
			from `tabCSKH Work Item` wi
			where wi.status in ('assigned', 'in_progress', 'on_hold')
			  and wi.assigned_to is not null and wi.assigned_to != ''{camp_sql}
			group by wi.assigned_to
			""",
			camp_params,
			as_dict=True,
		):
			s = _slot(r.u)
			s["active_protected"] = int(r.active_cnt or 0)
			s["connected_active"] = int(r.conn_cnt or 0)
			s["interview_scheduled"] = int(r.iv_cnt or 0)
			s["working_1_to_6"] = int(r.wk_cnt or 0)

		for r in frappe.db.sql(
			f"""
			select coalesce(nullif(wi.assigned_to, ''), nullif(wi.owner_at_close, ''), wi.closed_by) as u,
			       sum(case when wi.creation >= %s and wi.creation < %s and wi.source_type = 'self_sourced' then 1 else 0 end) as self_cnt,
			       sum(case when wi.completed_7d_at >= %s and wi.completed_7d_at < %s and wi.worker_stage = 'completed_7_days' then 1 else 0 end) as c7_today,
			       sum(case when wi.completed_7d_at >= %s and wi.completed_7d_at < %s and wi.worker_stage = 'completed_7_days' then 1 else 0 end) as c7_period,
			       sum(case when wi.closed_at >= %s and wi.closed_at < %s and wi.worker_stage in ('dropped_early', 'stopped') and coalesce(wi.workdays_completed, 0) > 0 and coalesce(wi.workdays_completed, 0) < 7 then 1 else 0 end) as drop_cnt
			from `tabCSKH Work Item` wi
			where 1=1{camp_sql}
			group by u
			""",
			(
				period_start,
				period_end,
				day_start,
				day_end,
				period_start,
				period_end,
				period_start,
				period_end,
				*camp_params,
			),
			as_dict=True,
		):
			if not r.u:
				continue
			s = _slot(r.u)
			s["self_sourced_period"] = int(r.self_cnt or 0)
			s["completed_7_days_today"] = int(r.c7_today or 0)
			s["completed_7_days_period"] = int(r.c7_period or 0)
			s["dropped_period"] = int(r.drop_cnt or 0)

		per_staff = sorted(
			staff_map.values(),
			key=lambda item: (
				-item["completed_7_days_period"],
				-item["working_1_to_6"],
				-item["active_protected"],
				item["user"],
			),
		)

	return {
		"scope": "all" if is_global else "self",
		"allow_pii": allow_pii,
		"date": day_date.isoformat(),
		"period": {"from": p_from.isoformat(), "to": p_to.isoformat()},
		"timezone": site_tz,
		"summary": summary,
		"pending_interviews": pending_interviews,
		"pending_workdays": pending_workdays,
		"completed_7d_items": completed_7d_items,
		"per_staff": per_staff,
		"as_of": now_datetime(),
	}


def _csv_safe(val) -> str:
	text = "" if val is None else str(val)
	if looks_like_formula(text):
		return "'" + text
	return text


def export_acceptance_csv(
	*,
	date_from: str | None = None,
	date_to: str | None = None,
	campaign: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Generate lightweight, formula-safe CSV export of the End-of-Day & 7-Workday Funnel (replaces heavy PDF)."""
	board = get_daily_acceptance_board(
		date_from=date_from,
		date_to=date_to,
		campaign=campaign,
		actor=actor,
	)
	buf = io.StringIO()
	buf.write("\ufeff")
	writer = csv.writer(buf)

	if board["scope"] == "all" and not board["allow_pii"]:
		# BOD 0-PII aggregate CSV
		writer.writerow(
			[
				"Nhân viên Marketing",
				"Họ tên nhân viên",
				"Tự khai thác trong kỳ",
				"Đang bảo hộ chăm sóc",
				"Hẹn phỏng vấn KCN",
				"Đang đi làm (1-6 công)",
				"Đạt đủ 7 công hôm nay",
				"Đạt đủ 7 công trong kỳ",
				"Nghỉ sớm trước 7 công",
			]
		)
		for row in board["per_staff"]:
			writer.writerow(
				[
					_csv_safe(row["user"]),
					_csv_safe(row["full_name"]),
					row["self_sourced_period"],
					row["active_protected"],
					row["interview_scheduled"],
					row["working_1_to_6"],
					row["completed_7_days_today"],
					row["completed_7_days_period"],
					row["dropped_period"],
				]
			)
	else:
		writer.writerow(
			[
				"Mã hồ sơ",
				"Tên người lao động",
				"Số điện thoại",
				"Nhân viên Marketing",
				"Nhà máy",
				"Khu công nghiệp",
				"Ngày vào xưởng",
				"Số ngày công",
				"Thời điểm chốt 7 ngày công",
			]
		)
		for item in board["completed_7d_items"]:
			writer.writerow(
				[
					_csv_safe(item["work_item"]),
					_csv_safe(item["display_name"]),
					_csv_safe(item["national_phone"]),
					_csv_safe(item["owner_user"]),
					_csv_safe(item["factory"]),
					_csv_safe(item["industrial_zone"]),
					_csv_safe(item["onboard_date"]),
					item["workdays_completed"],
					_csv_safe(item["completed_7d_at"]),
				]
			)

	p = board["period"]
	filename = f"nghiem-thu-7-cong-{p['from']}-{p['to']}.csv"
	return {
		"filename": filename,
		"csv_content": buf.getvalue(),
		"row_count": len(board["per_staff"]) if not board["allow_pii"] else len(board["completed_7d_items"]),
	}
