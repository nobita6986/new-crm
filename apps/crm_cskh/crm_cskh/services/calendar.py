"""Pure business-hours calendar arithmetic & upstream `CRM Holiday List` adapter (`P3-02`, `ADR-P3-02..03`).

Provides:
  * `WorkingCalendarSpec` — immutable, JSON-serializable snapshot of timezone, shifts (with breaks &
    overnight support), and holiday dates.
  * `business_minutes_between(start, end, calendar)` — pure $O(\\text{days} \\cdot \\text{shifts})$ elapsed
    working minutes, never negative.
  * `add_business_minutes(start, target_minutes, calendar)` — pure deadline calculation skipping breaks,
    weekends, and holidays while handling overnight shifts, `target=0`, and timezone boundaries.
  * `resolve_working_calendar(calendar_code=None)` — loads `CSKH Working Calendar` + pinned upstream
    `CRM Holiday List` (`crm/fcrm/doctype/crm_holiday_list`) without modifying upstream code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from crm_cskh.services.errors import PolicyConflict

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_CALENDAR_CODE = "VN-OFFICE-DEFAULT"
DEFAULT_HOLIDAY_LIST_NAME = "VN-HOLIDAYS-DEV"

WEEKDAY_NAMES = (
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
)
WEEKDAY_TO_INDEX = {name.lower(): idx for idx, name in enumerate(WEEKDAY_NAMES)}


class InvalidCalendarError(PolicyConflict):
	"""Raised when a working calendar has invalid timezone, shifts, or zero working capacity."""


@dataclass(frozen=True)
class ShiftWindow:
	"""Single working window on `weekday` (`0 = Monday .. 6 = Sunday`)."""

	weekday: int
	start_seconds: int
	end_seconds: int
	is_overnight: bool = False

	def to_dict(self) -> dict:
		return {
			"workday": WEEKDAY_NAMES[self.weekday],
			"start_time": _seconds_to_hhmmss(self.start_seconds),
			"end_time": _seconds_to_hhmmss(self.end_seconds),
			"is_overnight": bool(self.is_overnight),
		}


@dataclass(frozen=True)
class WorkingCalendarSpec:
	"""Immutable calendar snapshot stored on each `CSKH SLA Obligation`."""

	calendar_code: str
	timezone: str
	shifts: tuple[ShiftWindow, ...]
	holidays: frozenset[date]
	holiday_list_name: str | None = None
	clip_overnight_on_holiday: bool = False

	def zoneinfo(self) -> ZoneInfo:
		try:
			return ZoneInfo(self.timezone)
		except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
			raise InvalidCalendarError(f"Múi giờ lịch làm việc không hợp lệ: {self.timezone}") from exc

	def validate_non_empty(self) -> None:
		self.zoneinfo()
		if not self.shifts:
			raise InvalidCalendarError(
				f"Lịch làm việc '{self.calendar_code}' không có ca làm việc nào được cấu hình."
			)
		has_positive = False
		for sh in self.shifts:
			if sh.weekday < 0 or sh.weekday > 6:
				raise InvalidCalendarError("Thứ trong tuần của ca làm việc không hợp lệ.")
			if sh.start_seconds < 0 or sh.start_seconds >= 86400:
				raise InvalidCalendarError(
					"Giờ bắt đầu ca làm việc phải nằm trong khoảng 00:00:00..23:59:59."
				)
			if sh.end_seconds < 0 or sh.end_seconds > 86400:
				raise InvalidCalendarError("Giờ kết thúc ca làm việc không hợp lệ.")
			if sh.is_overnight or sh.end_seconds <= sh.start_seconds:
				dur = (86400 - sh.start_seconds) + sh.end_seconds
			else:
				dur = sh.end_seconds - sh.start_seconds
			if dur > 0:
				has_positive = True
		if not has_positive:
			raise InvalidCalendarError(f"Lịch làm việc '{self.calendar_code}' có tổng thời lượng ca bằng 0.")

	def to_snapshot_dict(self) -> dict:
		return {
			"calendar_code": self.calendar_code,
			"timezone": self.timezone,
			"holiday_list": self.holiday_list_name,
			"clip_overnight_on_holiday": bool(self.clip_overnight_on_holiday),
			"holidays": sorted(d.isoformat() for d in self.holidays),
			"shifts": [s.to_dict() for s in self.shifts],
		}

	def to_snapshot_json(self) -> str:
		return json.dumps(self.to_snapshot_dict(), ensure_ascii=False, sort_keys=True)

	@classmethod
	def from_snapshot(cls, raw: str | dict) -> WorkingCalendarSpec:
		data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
		shifts_raw = data.get("shifts") or []
		holidays_raw = data.get("holidays") or []
		return build_calendar_spec(
			calendar_code=str(data.get("calendar_code") or DEFAULT_CALENDAR_CODE),
			timezone=str(data.get("timezone") or DEFAULT_TIMEZONE),
			shifts=shifts_raw,
			holidays=holidays_raw,
			holiday_list_name=data.get("holiday_list"),
			clip_overnight_on_holiday=bool(data.get("clip_overnight_on_holiday", False)),
		)


def _seconds_to_hhmmss(total_sec: int) -> str:
	h = total_sec // 3600
	rem = total_sec % 3600
	m = rem // 60
	s = rem % 60
	return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_time_to_seconds(val: time | timedelta | str | int | float) -> int:
	if isinstance(val, timedelta):
		secs = int(val.total_seconds())
	elif isinstance(val, time):
		secs = val.hour * 3600 + val.minute * 60 + val.second
	elif isinstance(val, int | float):
		secs = int(val)
	elif isinstance(val, str):
		raw = val.strip()
		if raw == "24:00:00" or raw == "24:00":
			return 86400
		parts = raw.split(":")
		if len(parts) < 2 or len(parts) > 3:
			raise InvalidCalendarError(f"Định dạng giờ ca làm việc không hợp lệ: {val!r}")
		try:
			hh = int(parts[0])
			mm = int(parts[1])
			ss = int(float(parts[2])) if len(parts) == 3 else 0
		except ValueError as exc:
			raise InvalidCalendarError(f"Định dạng giờ ca làm việc không hợp lệ: {val!r}") from exc
		if hh == 24 and mm == 0 and ss == 0:
			return 86400
		if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
			raise InvalidCalendarError(f"Giá trị giờ ca làm việc vượt biên: {val!r}")
		secs = hh * 3600 + mm * 60 + ss
	else:
		raise InvalidCalendarError(f"Kiểu dữ liệu giờ ca làm việc không được hỗ trợ: {type(val).__name__}")
	if secs < 0 or secs > 86400:
		raise InvalidCalendarError(f"Số giây trong ngày vượt biên [0, 86400]: {secs}")
	return secs


def _parse_holiday_date(val: date | datetime | str) -> date:
	if isinstance(val, datetime):
		return val.date()
	if isinstance(val, date):
		return val
	if isinstance(val, str):
		return date.fromisoformat(val.strip()[:10])
	raise InvalidCalendarError(f"Định dạng ngày nghỉ lễ không hợp lệ: {val!r}")


def build_calendar_spec(
	*,
	calendar_code: str = DEFAULT_CALENDAR_CODE,
	timezone: str = DEFAULT_TIMEZONE,
	shifts: list[dict] | tuple[dict, ...],
	holidays: list[date | str] | set[date | str] | frozenset[date] = (),
	holiday_list_name: str | None = None,
	clip_overnight_on_holiday: bool = False,
) -> WorkingCalendarSpec:
	parsed_shifts: list[ShiftWindow] = []
	for item in shifts or ():
		wd_raw = item.get("workday") if isinstance(item, dict) else getattr(item, "workday", None)
		if isinstance(wd_raw, int):
			wd_idx = wd_raw
		else:
			wd_key = str(wd_raw or "").strip().lower()
			if wd_key not in WEEKDAY_TO_INDEX:
				raise InvalidCalendarError(f"Thứ làm việc không hợp lệ: {wd_raw!r}")
			wd_idx = WEEKDAY_TO_INDEX[wd_key]

		st_raw = item.get("start_time") if isinstance(item, dict) else getattr(item, "start_time", None)
		et_raw = item.get("end_time") if isinstance(item, dict) else getattr(item, "end_time", None)
		ov_raw = (
			item.get("is_overnight", False)
			if isinstance(item, dict)
			else getattr(item, "is_overnight", False)
		)

		start_sec = _parse_time_to_seconds(st_raw)
		end_sec = _parse_time_to_seconds(et_raw)
		is_overnight = bool(int(ov_raw)) if isinstance(ov_raw, int | bool) else bool(ov_raw)
		if end_sec <= start_sec and end_sec != 86400:
			is_overnight = True
		if not is_overnight and end_sec <= start_sec:
			raise InvalidCalendarError("Ca trong ngày phải có giờ kết thúc lớn hơn giờ bắt đầu.")

		parsed_shifts.append(
			ShiftWindow(
				weekday=wd_idx,
				start_seconds=start_sec,
				end_seconds=end_sec,
				is_overnight=is_overnight,
			)
		)

	parsed_shifts.sort(key=lambda s: (s.weekday, s.start_seconds, s.end_seconds))
	holiday_set = frozenset(_parse_holiday_date(h) for h in (holidays or ()))
	spec = WorkingCalendarSpec(
		calendar_code=calendar_code,
		timezone=timezone or DEFAULT_TIMEZONE,
		shifts=tuple(parsed_shifts),
		holidays=holiday_set,
		holiday_list_name=holiday_list_name,
		clip_overnight_on_holiday=clip_overnight_on_holiday,
	)
	spec.validate_non_empty()
	return spec


def default_vn_office_calendar(
	holidays: list[date | str] | tuple[date | str, ...] = (),
) -> WorkingCalendarSpec:
	"""Standard DEV/configurable Vietnam office working calendar (`08:00-12:00`, `13:00-17:30` Mon-Fri, Sat morning)."""
	shifts: list[dict] = []
	for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
		shifts.append({"workday": day, "start_time": "08:00:00", "end_time": "12:00:00"})
		shifts.append({"workday": day, "start_time": "13:00:00", "end_time": "17:30:00"})
	shifts.append({"workday": "Saturday", "start_time": "08:00:00", "end_time": "12:00:00"})
	return build_calendar_spec(
		calendar_code=DEFAULT_CALENDAR_CODE,
		timezone=DEFAULT_TIMEZONE,
		shifts=shifts,
		holidays=list(holidays),
		holiday_list_name=DEFAULT_HOLIDAY_LIST_NAME,
	)


def normalize_to_calendar_local(dt_or_str: datetime | str, calendar: WorkingCalendarSpec) -> datetime:
	"""Convert aware ISO/datetime or naive local datetime into naive `calendar.timezone` `datetime`.

	Preserves P2's MariaDB storage convention (naive `Asia/Ho_Chi_Minh` `DATETIME(6)`) while
	handling `Z` and explicit `+HH:MM` offsets accurately across midnight boundaries.
	"""
	tz = calendar.zoneinfo()
	if isinstance(dt_or_str, datetime):
		parsed = dt_or_str
	elif isinstance(dt_or_str, str):
		raw = dt_or_str.strip()
		if not raw:
			raise ValueError("Chuỗi thời gian không được để trống.")
		if raw.endswith("Z") or raw.endswith("z"):
			raw = raw[:-1] + "+00:00"
		try:
			parsed = datetime.fromisoformat(raw)
		except ValueError:
			import frappe

			parsed = frappe.utils.get_datetime(raw)
			if parsed is None:
				raise
	else:
		raise TypeError(f"Kiểu thời gian không hợp lệ: {type(dt_or_str).__name__}")

	if parsed.tzinfo is not None and parsed.tzinfo.utcoffset(parsed) is not None:
		return parsed.astimezone(tz).replace(tzinfo=None)
	return parsed


def format_calendar_iso(dt_or_str: datetime | str | None, timezone: str = DEFAULT_TIMEZONE) -> str | None:
	"""Format naive site-local datetime as explicit ISO-8601 string with `+07:00` offset for APIs."""
	if dt_or_str is None or dt_or_str == "":
		return None
	tz = ZoneInfo(timezone or DEFAULT_TIMEZONE)
	if isinstance(dt_or_str, str):
		raw = dt_or_str.strip()
		if not raw:
			return None
		if raw.endswith("Z") or raw.endswith("z"):
			raw = raw[:-1] + "+00:00"
		parsed = datetime.fromisoformat(raw)
	else:
		parsed = dt_or_str
	if parsed.tzinfo is None:
		aware = parsed.replace(tzinfo=tz)
	else:
		aware = parsed.astimezone(tz)
	return aware.isoformat(timespec="seconds")


def _day_working_segments(day: date, calendar: WorkingCalendarSpec) -> list[tuple[datetime, datetime]]:
	"""Return merged sorted `(seg_start, seg_end)` working intervals falling inside `[day 00:00, day+1 00:00]`."""
	day_start = datetime.combine(day, time.min)
	raw_segments: list[tuple[datetime, datetime]] = []

	# 1. Spillover tail `[00:00, end_time]` from an overnight shift started on `day - 1`
	prev_day = day - timedelta(days=1)
	prev_is_holiday = prev_day in calendar.holidays
	curr_is_holiday = day in calendar.holidays
	if not prev_is_holiday and not (curr_is_holiday and calendar.clip_overnight_on_holiday):
		prev_wd = prev_day.weekday()
		for sh in calendar.shifts:
			if sh.weekday == prev_wd and sh.is_overnight and sh.end_seconds > 0:
				raw_segments.append((day_start, day_start + timedelta(seconds=sh.end_seconds)))

	# 2. Shifts starting on `day` itself (skipped when `day` is a holiday)
	if not curr_is_holiday:
		wd = day.weekday()
		for sh in calendar.shifts:
			if sh.weekday != wd:
				continue
			seg_s = day_start + timedelta(seconds=sh.start_seconds)
			if sh.is_overnight:
				seg_e = day_start + timedelta(seconds=86400)
			else:
				seg_e = day_start + timedelta(seconds=sh.end_seconds)
			if seg_e > seg_s:
				raw_segments.append((seg_s, seg_e))

	if not raw_segments:
		return []

	raw_segments.sort(key=lambda pair: (pair[0], pair[1]))
	merged: list[tuple[datetime, datetime]] = [raw_segments[0]]
	for s, e in raw_segments[1:]:
		last_s, last_e = merged[-1]
		if s <= last_e:
			merged[-1] = (last_s, max(last_e, e))
		else:
			merged.append((s, e))
	return merged


def business_minutes_between(
	start: datetime | str,
	end: datetime | str,
	calendar: WorkingCalendarSpec,
) -> float:
	"""Compute exact working minutes in `[start, end]` according to `calendar` (`>= 0.0`)."""
	calendar.validate_non_empty()
	s_dt = normalize_to_calendar_local(start, calendar)
	e_dt = normalize_to_calendar_local(end, calendar)
	if e_dt <= s_dt:
		return 0.0

	total_seconds = 0.0
	cur_day = s_dt.date()
	end_day = e_dt.date()
	while cur_day <= end_day:
		for seg_s, seg_e in _day_working_segments(cur_day, calendar):
			overlap_s = s_dt if s_dt > seg_s else seg_s
			overlap_e = e_dt if e_dt < seg_e else seg_e
			if overlap_e > overlap_s:
				total_seconds += (overlap_e - overlap_s).total_seconds()
		cur_day += timedelta(days=1)

	return round(total_seconds / 60.0, 6)


def add_business_minutes(
	start: datetime | str,
	target_minutes: int | float,
	calendar: WorkingCalendarSpec,
	*,
	max_search_days: int = 366,
) -> datetime:
	"""Advance `start` by `target_minutes` working minutes in `calendar.timezone` (naive local output)."""
	calendar.validate_non_empty()
	if target_minutes < 0:
		raise InvalidCalendarError("Số phút SLA mục tiêu (target_minutes) không được âm.")

	cur_dt = normalize_to_calendar_local(start, calendar)
	remaining_seconds = round(float(target_minutes) * 60.0, 6)

	for day_offset in range(max_search_days + 1):
		day = cur_dt.date() + timedelta(days=day_offset)
		for seg_s, seg_e in _day_working_segments(day, calendar):
			if seg_e <= cur_dt:
				continue
			eff_start = cur_dt if cur_dt > seg_s else seg_s
			if remaining_seconds == 0.0:
				return eff_start
			avail_seconds = (seg_e - eff_start).total_seconds()
			if avail_seconds <= 0:
				continue
			if remaining_seconds <= avail_seconds + 1e-9:
				return eff_start + timedelta(seconds=remaining_seconds)
			remaining_seconds = round(remaining_seconds - avail_seconds, 6)

	raise InvalidCalendarError(
		f"Không tìm đủ thời gian làm việc hợp lệ trong {max_search_days} ngày trên lịch '{calendar.calendar_code}'."
	)


def load_holidays_from_crm_holiday_list(holiday_list_name: str | None) -> frozenset[date]:
	"""Adapter reading pinned upstream `CRM Holiday List` (`crm/fcrm/doctype/crm_holiday_list`)."""
	if not holiday_list_name:
		return frozenset()
	import frappe

	if not frappe.db.table_exists("CRM Holiday List") or not frappe.db.exists(
		"CRM Holiday List", holiday_list_name
	):
		return frozenset()
	rows = frappe.db.get_all(
		"CRM Holiday",
		filters={"parenttype": "CRM Holiday List", "parent": holiday_list_name},
		fields=["date"],
	)
	return frozenset(_parse_holiday_date(r.date) for r in rows if r.get("date"))


def resolve_working_calendar(calendar_code: str | None = None) -> WorkingCalendarSpec:
	"""Load active `CSKH Working Calendar` (or fallback default) with upstream `CRM Holiday List` dates."""
	import frappe

	if frappe.db.table_exists("CSKH Working Calendar"):
		cal_row = None
		if calendar_code:
			cal_row = frappe.db.get_value(
				"CSKH Working Calendar",
				{"name": calendar_code, "enabled": 1},
				["name", "calendar_code", "timezone", "holiday_list"],
				as_dict=True,
			)
		if not cal_row:
			rows = frappe.db.get_all(
				"CSKH Working Calendar",
				filters={"enabled": 1},
				fields=["name", "calendar_code", "timezone", "holiday_list", "is_default"],
				order_by="is_default desc, creation asc",
				limit=1,
			)
			cal_row = rows[0] if rows else None

		if cal_row:
			shift_rows = frappe.db.get_all(
				"CSKH Working Shift",
				filters={"parenttype": "CSKH Working Calendar", "parent": cal_row.name},
				fields=["workday", "start_time", "end_time", "is_overnight"],
				order_by="idx asc",
			)
			holidays = load_holidays_from_crm_holiday_list(cal_row.holiday_list)
			return build_calendar_spec(
				calendar_code=cal_row.calendar_code or cal_row.name,
				timezone=cal_row.timezone or DEFAULT_TIMEZONE,
				shifts=shift_rows,
				holidays=holidays,
				holiday_list_name=cal_row.holiday_list,
			)

	holidays = load_holidays_from_crm_holiday_list(DEFAULT_HOLIDAY_LIST_NAME)
	return default_vn_office_calendar(holidays=tuple(holidays))
