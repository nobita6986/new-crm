"""Deterministic `Asia/Ho_Chi_Minh` period parser & trend bucket generator (`docs/p3/report-api-contract.md` §1.1)."""

from __future__ import annotations

import calendar as pycal
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from crm_cskh.services.calendar import DEFAULT_TIMEZONE
from crm_cskh.services.errors import InvalidPeriod

VALID_PERIOD_TYPES = ("daily", "weekly", "monthly", "quarterly", "yearly", "custom")
MAX_CUSTOM_DAYS = 366

_RE_DAILY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_RE_WEEKLY = re.compile(r"^(\d{4})-W(\d{2})$", re.IGNORECASE)
_RE_MONTHLY = re.compile(r"^(\d{4})-(\d{2})$")
_RE_QUARTERLY = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)
_RE_YEARLY = re.compile(r"^(\d{4})$")


@dataclass(frozen=True)
class PeriodSpec:
	period_type: str
	period_key: str
	from_date: date
	to_date: date
	timezone: str = DEFAULT_TIMEZONE

	@property
	def start_dt(self) -> datetime:
		return datetime.combine(self.from_date, time.min)

	@property
	def end_dt(self) -> datetime:
		return datetime.combine(self.to_date, time(23, 59, 59, 999999))

	def to_dict(self) -> dict:
		return {
			"period_type": self.period_type,
			"period_key": self.period_key,
			"from_date": self.from_date.isoformat(),
			"to_date": self.to_date.isoformat(),
			"timezone": self.timezone,
		}


def today_hcm(ref_dt: datetime | None = None) -> date:
	if ref_dt is not None:
		if ref_dt.tzinfo is not None:
			return ref_dt.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
		return ref_dt.date()
	return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()


def _parse_iso_date(raw: str | date | None, field: str) -> date:
	if isinstance(raw, datetime):
		return raw.date()
	if isinstance(raw, date):
		return raw
	if not isinstance(raw, str) or not raw.strip():
		raise InvalidPeriod(f"Tham số ngày '{field}' là bắt buộc và phải có định dạng YYYY-MM-DD.")
	m = _RE_DAILY.match(raw.strip())
	if not m:
		raise InvalidPeriod(f"Tham số '{field}' không đúng định dạng YYYY-MM-DD: {raw!r}")
	try:
		return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
	except ValueError as exc:
		raise InvalidPeriod(f"Giá trị ngày '{field}' không hợp lệ: {raw!r}") from exc


def resolve_period_spec(
	period_type: str | None = "monthly",
	period_key: str | None = None,
	from_date: str | date | None = None,
	to_date: str | date | None = None,
	*,
	ref_date: date | None = None,
) -> PeriodSpec:
	"""Parse and validate period request parameters into a canonical `PeriodSpec`."""
	if period_type is not None and not isinstance(period_type, str):
		raise InvalidPeriod("Tham số period_type phải là chuỗi ký tự.")
	ptype = (period_type or "monthly").strip().lower()
	if ptype not in VALID_PERIOD_TYPES:
		raise InvalidPeriod(
			f"Loại kỳ báo cáo không hợp lệ: {period_type!r}. Chỉ hỗ trợ {', '.join(VALID_PERIOD_TYPES)}."
		)

	if period_key is not None and not isinstance(period_key, str):
		raise InvalidPeriod("Tham số period_key phải là chuỗi ký tự.")
	pkey = (period_key or "").strip()
	anchor = ref_date or today_hcm()

	if ptype == "custom":
		d_from = _parse_iso_date(from_date, "from_date")
		d_to = _parse_iso_date(to_date, "to_date")
		if d_from > d_to:
			raise InvalidPeriod("Ngày bắt đầu (from_date) không được lớn hơn ngày kết thúc (to_date).")
		if (d_to - d_from).days + 1 > MAX_CUSTOM_DAYS:
			raise InvalidPeriod(f"Khoảng ngày tùy chỉnh không được vượt quá {MAX_CUSTOM_DAYS} ngày.")
		return PeriodSpec(
			period_type="custom",
			period_key=f"{d_from.isoformat()}..{d_to.isoformat()}",
			from_date=d_from,
			to_date=d_to,
		)

	if ptype == "daily":
		d = (
			_parse_iso_date(pkey, "period_key")
			if pkey
			else (_parse_iso_date(from_date, "from_date") if from_date else anchor)
		)
		return PeriodSpec(period_type="daily", period_key=d.isoformat(), from_date=d, to_date=d)

	if ptype == "weekly":
		if pkey:
			m = _RE_WEEKLY.match(pkey)
			if not m:
				raise InvalidPeriod(f"Định dạng tuần phải là YYYY-Www (ví dụ 2026-W45): {pkey!r}")
			yr, wk = int(m.group(1)), int(m.group(2))
			try:
				d_from = date.fromisocalendar(yr, wk, 1)
				d_to = date.fromisocalendar(yr, wk, 7)
			except ValueError as exc:
				raise InvalidPeriod(f"Tuần ISO không tồn tại: {pkey!r}") from exc
			norm_key = f"{yr:04d}-W{wk:02d}"
		else:
			iso_yr, iso_wk, _ = anchor.isocalendar()
			d_from = date.fromisocalendar(iso_yr, iso_wk, 1)
			d_to = date.fromisocalendar(iso_yr, iso_wk, 7)
			norm_key = f"{iso_yr:04d}-W{iso_wk:02d}"
		return PeriodSpec(period_type="weekly", period_key=norm_key, from_date=d_from, to_date=d_to)

	if ptype == "monthly":
		if pkey:
			m = _RE_MONTHLY.match(pkey)
			if not m:
				raise InvalidPeriod(f"Định dạng tháng phải là YYYY-MM: {pkey!r}")
			yr, mo = int(m.group(1)), int(m.group(2))
			if not (1 <= mo <= 12):
				raise InvalidPeriod(f"Tháng không hợp lệ trong period_key: {pkey!r}")
		else:
			yr, mo = anchor.year, anchor.month
		last_day = pycal.monthrange(yr, mo)[1]
		return PeriodSpec(
			period_type="monthly",
			period_key=f"{yr:04d}-{mo:02d}",
			from_date=date(yr, mo, 1),
			to_date=date(yr, mo, last_day),
		)

	if ptype == "quarterly":
		if pkey:
			m = _RE_QUARTERLY.match(pkey)
			if not m:
				raise InvalidPeriod(f"Định dạng quý phải là YYYY-Q1..Q4: {pkey!r}")
			yr, q = int(m.group(1)), int(m.group(2))
		else:
			yr = anchor.year
			q = (anchor.month - 1) // 3 + 1
		start_mo = (q - 1) * 3 + 1
		end_mo = start_mo + 2
		last_day = pycal.monthrange(yr, end_mo)[1]
		return PeriodSpec(
			period_type="quarterly",
			period_key=f"{yr:04d}-Q{q}",
			from_date=date(yr, start_mo, 1),
			to_date=date(yr, end_mo, last_day),
		)

	# yearly
	if pkey:
		m = _RE_YEARLY.match(pkey)
		if not m:
			raise InvalidPeriod(f"Định dạng năm phải là YYYY: {pkey!r}")
		yr = int(m.group(1))
		if yr < 2000 or yr > 2100:
			raise InvalidPeriod(f"Năm ngoài phạm vi hợp lệ: {pkey!r}")
	else:
		yr = anchor.year
	return PeriodSpec(
		period_type="yearly",
		period_key=f"{yr:04d}",
		from_date=date(yr, 1, 1),
		to_date=date(yr, 12, 31),
	)


def affected_standard_periods_for_date(d: date) -> list[PeriodSpec]:
	"""Return the canonical `(daily, weekly, monthly, quarterly, yearly)` periods containing `d`."""
	iso_yr, iso_wk, _ = d.isocalendar()
	q = (d.month - 1) // 3 + 1
	return [
		resolve_period_spec("daily", d.isoformat()),
		resolve_period_spec("weekly", f"{iso_yr:04d}-W{iso_wk:02d}"),
		resolve_period_spec("monthly", f"{d.year:04d}-{d.month:02d}"),
		resolve_period_spec("quarterly", f"{d.year:04d}-Q{q}"),
		resolve_period_spec("yearly", f"{d.year:04d}"),
	]


def iter_days(d_from: date, d_to: date) -> list[date]:
	days: list[date] = []
	cur = d_from
	while cur <= d_to:
		days.append(cur)
		cur += timedelta(days=1)
	return days
