"""P3 domain models, validation invariants, indexes & DEV/configurable seeds (`P3-00..P3-08`).

Enforces:
  * `CSKH Team Membership History`: append-only via controlled attribution service.
  * `CSKH Working Calendar`: validated shifts & timezone; non-admin write blocked.
  * `CSKH SLA Obligation`: `original_due_at` is strictly immutable (`ADR-P3-01`); mutations must run
    through `crm_cskh.services.sla_engine`.
  * Upstream `CRM Holiday List`: blocks generic write/delete by `Sales User` / `Sales Manager` / `BOD`
    unless caller holds `CSKH CRM Admin` or `System Manager` (`ADR-P3-02`).
"""

from __future__ import annotations

import contextvars
import json
from datetime import date

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime

from crm_cskh.p1_permissions import ROLE_ADMIN, _is_system
from crm_cskh.services.attribution import is_authorized_membership_write
from crm_cskh.services.calendar import (
	DEFAULT_CALENDAR_CODE,
	DEFAULT_HOLIDAY_LIST_NAME,
	DEFAULT_TIMEZONE,
	build_calendar_spec,
)
from crm_cskh.services.errors import PolicyConflict

P3_PR2_DOCTYPES = (
	"CSKH Team Membership History",
	"CSKH Working Calendar",
	"CSKH Working Shift",
	"CSKH SLA Obligation",
)

OBLIGATION_TYPES = ("FIRST_ATTEMPT", "FOLLOWUP", "FIRST_CONNECTED", "MIN_ATTEMPTS")
OBLIGATION_STATUSES = (
	"open",
	"fulfilled_on_time",
	"fulfilled_late",
	"breached_open",
	"breached_handoff",
	"handoff_closed",
	"superseded",
	"waived",
	"canceled_validly",
)

# DEV / configurable P3 defaults on `CSKH Policy` (NOT BOD-approved production policy!).
DEFAULT_P3_POLICY_FIELDS = {
	"p3_sla_enabled": 1,
	"p3_reporting_enabled": 1,
	"qa_enabled": 0,
	"working_calendar": DEFAULT_CALENDAR_CODE,
	"first_attempt_sla_minutes": 30,
	"followup_grace_minutes": 15,
	"handoff_min_grace_minutes": 15,
	"max_handoff_grace_resets": 2,
	"due_soon_warning_minutes": 10,
	"track_first_connected": 1,
	"first_connected_target_minutes": 120,
	"min_attempts_enabled": 0,
	"min_attempts_before_close": 3,
	"min_attempt_gap_minutes": 30,
	"max_reschedules_without_approval": 2,
	"allow_leader_waive_sla": 1,
	"allow_leader_pause_sla": 1,
	"weight_first_attempt_sla": 25,
	"weight_followup_sla": 20,
	"weight_data_hygiene": 20,
	"weight_callback_discipline": 20,
	"weight_qa_quality": 15,
	"min_valid_score_weight": 60,
	"min_eligible_events_for_score": 1,
	"min_qa_sample_size": 5,
}

DEFAULT_VN_HOLIDAYS = (
	(date(2025, 1, 1), "Tết Dương lịch 2025"),
	(date(2025, 4, 30), "Ngày Chiến thắng 30/04"),
	(date(2025, 5, 1), "Ngày Quốc tế Lao động 01/05"),
	(date(2025, 9, 2), "Quốc khánh 02/09"),
	(date(2026, 1, 1), "Tết Dương lịch 2026"),
	(date(2026, 4, 30), "Ngày Chiến thắng 30/04"),
	(date(2026, 5, 1), "Ngày Quốc tế Lao động 01/05"),
	(date(2026, 9, 2), "Quốc khánh 02/09"),
	(date(2027, 1, 1), "Tết Dương lịch 2027"),
	(date(2027, 4, 30), "Ngày Chiến thắng 30/04"),
	(date(2027, 5, 1), "Ngày Quốc tế Lao động 01/05"),
	(date(2027, 9, 2), "Quốc khánh 02/09"),
)

_AUTHORIZED_SLA_WRITE: contextvars.ContextVar[bool] = contextvars.ContextVar(
	"_AUTHORIZED_SLA_WRITE",
	default=False,
)


class _AuthorizedSlaContext:
	def __enter__(self):
		self._token = _AUTHORIZED_SLA_WRITE.set(True)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		_AUTHORIZED_SLA_WRITE.reset(self._token)
		return False


def authorized_sla_write() -> _AuthorizedSlaContext:
	return _AuthorizedSlaContext()


def is_authorized_sla_write() -> bool:
	return bool(_AUTHORIZED_SLA_WRITE.get())


def _is_cskh_admin_or_system(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if _is_system(user):
		return True
	return ROLE_ADMIN in set(frappe.get_roles(user))


def guard_crm_holiday_list_mutation(doc, method=None) -> None:
	"""Prevent `Sales User` / `Sales Manager` / `BOD` from mutating upstream `CRM Holiday List`."""
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
		return
	if getattr(frappe.flags, "cskh_seed_holiday_list", False):
		return
	if not _is_cskh_admin_or_system(frappe.session.user):
		frappe.throw(
			_("Chỉ CSKH CRM Admin hoặc System Manager được phép thay đổi danh sách ngày nghỉ lễ."),
			frappe.PermissionError,
		)


class TeamMembershipHistory(Document):
	def validate(self):
		if not (
			is_authorized_membership_write()
			or getattr(frappe.flags, "in_migrate", False)
			or getattr(frappe.flags, "in_install", False)
		):
			frappe.throw(
				_("Lịch sử thành viên team là bất biến (append-only) và chỉ được ghi qua dịch vụ hệ thống."),
				frappe.PermissionError,
			)
		if not self.is_new():
			frappe.throw(
				_("Không được sửa trực tiếp bản ghi lịch sử thành viên team."), frappe.PermissionError
			)
		if self.effective_from and self.effective_to:
			if get_datetime(self.effective_to) < get_datetime(self.effective_from):
				frappe.throw(_("Thời điểm kết thúc hiệu lực không được nhỏ hơn thời điểm bắt đầu."))

	def on_trash(self):
		frappe.throw(_("Lịch sử thành viên team là bất biến, không được phép xóa."), frappe.PermissionError)


class WorkingCalendar(Document):
	def validate(self):
		if not (
			_is_cskh_admin_or_system(frappe.session.user)
			or getattr(frappe.flags, "in_migrate", False)
			or getattr(frappe.flags, "in_install", False)
			or getattr(frappe.flags, "cskh_seed_calendar", False)
		):
			frappe.throw(
				_("Chỉ CSKH CRM Admin hoặc System Manager được phép cấu hình lịch làm việc."),
				frappe.PermissionError,
			)
		build_calendar_spec(
			calendar_code=self.calendar_code or self.name or DEFAULT_CALENDAR_CODE,
			timezone=self.timezone or DEFAULT_TIMEZONE,
			shifts=self.shifts or [],
			holidays=[],
			holiday_list_name=self.holiday_list,
		)

	def on_trash(self):
		frappe.throw(
			_("Không được xóa lịch làm việc; hãy tắt cờ Enabled nếu ngừng sử dụng."), frappe.PermissionError
		)


class SLAObligation(Document):
	def validate(self):
		if not (
			is_authorized_sla_write()
			or getattr(frappe.flags, "in_migrate", False)
			or getattr(frappe.flags, "in_install", False)
		):
			frappe.throw(
				_("Nghĩa vụ SLA chỉ được phép tạo hoặc cập nhật thông qua dịch vụ SLA Engine."),
				frappe.PermissionError,
			)
		if self.obligation_type not in OBLIGATION_TYPES:
			raise PolicyConflict(f"Loại nghĩa vụ SLA không hợp lệ: {self.obligation_type}")
		if self.status not in OBLIGATION_STATUSES:
			raise PolicyConflict(f"Trạng thái nghĩa vụ SLA không hợp lệ: {self.status}")

		if not self.is_new():
			prev_orig = frappe.db.get_value("CSKH SLA Obligation", self.name, "original_due_at")
			if prev_orig and get_datetime(prev_orig) != get_datetime(self.original_due_at):
				raise PolicyConflict("Trường original_due_at là bất biến, tuyệt đối không được phép sửa đổi.")

		for json_field in ("policy_snapshot_json", "calendar_snapshot_json", "audit_events_json"):
			raw = getattr(self, json_field, None)
			if raw:
				try:
					json.loads(raw)
				except (TypeError, ValueError) as exc:
					raise PolicyConflict(f"Trường {json_field} không phải JSON hợp lệ.") from exc

		self.evaluation_anchor_at = self.fulfilled_at or self.breached_at or self.closed_at

	def on_trash(self):
		frappe.throw(_("Nghĩa vụ SLA là dữ liệu kiểm toán, không được phép xóa."), frappe.PermissionError)


def _index_exists(table_name: str, index_name: str) -> bool:
	clean_table = table_name.replace("`", "")
	rows = frappe.db.sql(f"show index from `{clean_table}` where Key_name = %s", (index_name,))
	return bool(rows)


def _ensure_index(doctype: str, fields: tuple[str, ...], index_name: str, *, unique: bool = False) -> None:
	table = f"tab{doctype}"
	if not frappe.db.table_exists(doctype):
		return
	if _index_exists(table, index_name):
		return
	cols = ", ".join(f"`{col}`" for col in fields)
	kind = "unique index" if unique else "index"
	frappe.db.sql_ddl(f"alter table `{table}` add {kind} `{index_name}` ({cols})")


def ensure_p3_indexes(only_doctype: str | None = None) -> None:
	"""Create idempotent composite indexes for P3 attribution & SLA queries."""
	specs: list[tuple[str, tuple[str, ...], str, bool]] = [
		(
			"CSKH Team Membership History",
			("user", "effective_from", "effective_to"),
			"idx_cskh_tmh_user_window",
			False,
		),
		(
			"CSKH Team Membership History",
			("team_node", "effective_from", "effective_to"),
			"idx_cskh_tmh_team_window",
			False,
		),
		(
			"CSKH Assignment History",
			("to_user", "ownership_start", "ownership_end"),
			"idx_cskh_ah_user_interval",
			False,
		),
		(
			"CSKH Assignment History",
			("team_at_assignment", "ownership_start"),
			"idx_cskh_ah_team_start",
			False,
		),
		(
			"CSKH SLA Obligation",
			("idempotency_key",),
			"uniq_cskh_sla_idem_key",
			True,
		),
		(
			"CSKH SLA Obligation",
			("status", "effective_due_at"),
			"idx_cskh_sla_status_due",
			False,
		),
		(
			"CSKH SLA Obligation",
			("work_item", "obligation_type", "status"),
			"idx_cskh_sla_wi_type_status",
			False,
		),
		(
			"CSKH SLA Obligation",
			("owner_user", "evaluation_anchor_at", "status"),
			"idx_cskh_sla_owner_anchor",
			False,
		),
		(
			"CSKH SLA Obligation",
			("team_at_obligation", "evaluation_anchor_at", "status"),
			"idx_cskh_sla_team_anchor",
			False,
		),
		(
			"CSKH SLA Obligation",
			("campaign", "evaluation_anchor_at", "status"),
			"idx_cskh_sla_camp_anchor",
			False,
		),
	]
	for dt, cols, idx_name, is_uniq in specs:
		if only_doctype and dt != only_doctype:
			continue
		if all(frappe.db.has_column(dt, c) for c in cols):
			_ensure_index(dt, cols, idx_name, unique=is_uniq)


def ensure_p3_seeds() -> None:
	"""Seed DEV/configurable Vietnam holiday list, working calendar, and P3 policy defaults idempotently."""
	# 1. Ensure System Settings.time_zone is `Asia/Ho_Chi_Minh` if still at Frappe's install default `Asia/Kolkata`
	if frappe.db.exists("DocType", "System Settings"):
		cur_tz = frappe.db.get_single_value("System Settings", "time_zone")
		if not cur_tz or cur_tz == "Asia/Kolkata":
			frappe.db.set_single_value("System Settings", "time_zone", DEFAULT_TIMEZONE)

	# 2. Seed `VN-HOLIDAYS-DEV` in upstream `CRM Holiday List`
	if frappe.db.table_exists("CRM Holiday List") and not frappe.db.exists(
		"CRM Holiday List", DEFAULT_HOLIDAY_LIST_NAME
	):
		prev_flag = getattr(frappe.flags, "cskh_seed_holiday_list", False)
		frappe.flags.cskh_seed_holiday_list = True
		try:
			hl = frappe.get_doc(
				{
					"doctype": "CRM Holiday List",
					"holiday_list_name": DEFAULT_HOLIDAY_LIST_NAME,
					"from_date": "2025-01-01",
					"to_date": "2027-12-31",
					"weekly_off": "Sunday",
					"holidays": [
						{"date": d.isoformat(), "description": desc, "weekly_off": 0}
						for d, desc in DEFAULT_VN_HOLIDAYS
					],
				}
			)
			hl.insert(ignore_permissions=True)
		finally:
			frappe.flags.cskh_seed_holiday_list = prev_flag

	# 3. Seed `VN-OFFICE-DEFAULT` in `CSKH Working Calendar`
	if frappe.db.table_exists("CSKH Working Calendar") and not frappe.db.exists(
		"CSKH Working Calendar", DEFAULT_CALENDAR_CODE
	):
		prev_cal_flag = getattr(frappe.flags, "cskh_seed_calendar", False)
		frappe.flags.cskh_seed_calendar = True
		try:
			shifts = []
			for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
				shifts.append(
					{"workday": day, "start_time": "08:00:00", "end_time": "12:00:00", "is_overnight": 0}
				)
				shifts.append(
					{"workday": day, "start_time": "13:00:00", "end_time": "17:30:00", "is_overnight": 0}
				)
			shifts.append(
				{"workday": "Saturday", "start_time": "08:00:00", "end_time": "12:00:00", "is_overnight": 0}
			)
			cal_doc = frappe.get_doc(
				{
					"doctype": "CSKH Working Calendar",
					"calendar_code": DEFAULT_CALENDAR_CODE,
					"title": "Lịch Hành Chính CSKH Mặc Định (DEV / Configurable)",
					"timezone": DEFAULT_TIMEZONE,
					"holiday_list": (
						DEFAULT_HOLIDAY_LIST_NAME
						if frappe.db.exists("CRM Holiday List", DEFAULT_HOLIDAY_LIST_NAME)
						else None
					),
					"enabled": 1,
					"is_default": 1,
					"shifts": shifts,
				}
			)
			cal_doc.insert(ignore_permissions=True)
		finally:
			frappe.flags.cskh_seed_calendar = prev_cal_flag

	# 4. Backfill P3 DEV/configurable fields on existing `CSKH Policy` rows where unset
	if frappe.db.table_exists("CSKH Policy") and frappe.db.has_column(
		"CSKH Policy", "first_attempt_sla_minutes"
	):
		policies = frappe.db.get_all(
			"CSKH Policy", fields=["name", "working_calendar", "first_attempt_sla_minutes"]
		)
		for pol in policies:
			updates: dict = {}
			if not pol.get("working_calendar") and frappe.db.exists(
				"CSKH Working Calendar", DEFAULT_CALENDAR_CODE
			):
				updates["working_calendar"] = DEFAULT_CALENDAR_CODE
			if not cint(pol.get("first_attempt_sla_minutes")):
				for field, val in DEFAULT_P3_POLICY_FIELDS.items():
					if frappe.db.has_column("CSKH Policy", field):
						updates[field] = val
			if updates:
				frappe.db.set_value("CSKH Policy", pol["name"], updates, update_modified=False)
