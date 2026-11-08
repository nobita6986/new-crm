"""Unit & integration tests for P3 Working Calendar & Holiday arithmetic (`P3-02`, `T-CAL-01..09`)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import frappe

from crm_cskh.services.calendar import (
	DEFAULT_CALENDAR_CODE,
	DEFAULT_HOLIDAY_LIST_NAME,
	DEFAULT_TIMEZONE,
	InvalidCalendarError,
	WorkingCalendarSpec,
	add_business_minutes,
	build_calendar_spec,
	business_minutes_between,
	default_vn_office_calendar,
	load_holidays_from_crm_holiday_list,
	resolve_working_calendar,
)
from crm_cskh.tests.p3_base import P3TestCase


class TestP3Calendar(P3TestCase):
	"""Covers `T-CAL-01..T-CAL-09` from `docs/p3/calendars-and-holidays.md`."""

	def test_same_shift_business_minutes_and_add(self):
		cal = default_vn_office_calendar()
		start = datetime(2026, 4, 27, 8, 30, 0)  # Monday
		due = add_business_minutes(start, 45, cal)
		self.assertEqual(due, datetime(2026, 4, 27, 9, 15, 0))
		self.assertAlmostEqual(business_minutes_between(start, due, cal), 45.0, places=4)

	def test_lunch_break_skip_and_symmetry(self):
		cal = default_vn_office_calendar()
		start = datetime(2026, 4, 27, 11, 45, 0)  # 15m before 12:00 lunch break
		due = add_business_minutes(start, 30, cal)
		self.assertEqual(due, datetime(2026, 4, 27, 13, 15, 0))
		self.assertAlmostEqual(business_minutes_between(start, due, cal), 30.0, places=4)
		# Interval wholly inside lunch break -> 0 business minutes
		self.assertEqual(
			business_minutes_between(
				datetime(2026, 4, 27, 12, 5, 0),
				datetime(2026, 4, 27, 12, 55, 0),
				cal,
			),
			0.0,
		)

	def test_saturday_afternoon_and_sunday_rollover(self):
		cal = default_vn_office_calendar()
		# 2026-04-25 is Saturday (shift 08:00-12:00 only); Sunday 2026-04-26 is off
		sat_late = datetime(2026, 4, 25, 11, 50, 0)
		due = add_business_minutes(sat_late, 30, cal)
		self.assertEqual(due, datetime(2026, 4, 27, 8, 20, 0))  # Monday 08:20
		self.assertAlmostEqual(business_minutes_between(sat_late, due, cal), 30.0, places=4)

	def test_holiday_and_weekend_rollover_with_upstream_crm_holiday_list(self):
		holidays = load_holidays_from_crm_holiday_list(DEFAULT_HOLIDAY_LIST_NAME)
		self.assertIn(date(2026, 4, 30), holidays)
		self.assertIn(date(2026, 5, 1), holidays)

		cal = resolve_working_calendar(DEFAULT_CALENDAR_CODE)
		# Wed 2026-04-29 17:15 (15m left in afternoon shift 13:00-17:30)
		# Thu 04-30 & Fri 05-01 are holidays -> resumes Sat 2026-05-02 08:00 + 15m = 08:15
		start = datetime(2026, 4, 29, 17, 15, 0)
		due = add_business_minutes(start, 30, cal)
		self.assertEqual(due, datetime(2026, 5, 2, 8, 15, 0))
		self.assertAlmostEqual(business_minutes_between(start, due, cal), 30.0, places=4)

	def test_overnight_shift_crossing_midnight_and_holiday_clipping(self):
		overnight_shifts = [
			{"workday": "Wednesday", "start_time": "22:00:00", "end_time": "06:00:00", "is_overnight": 1},
			{"workday": "Friday", "start_time": "08:00:00", "end_time": "12:00:00", "is_overnight": 0},
		]
		# Case A: default (`clip_overnight_on_holiday=False`) lets Wednesday night shift finish at Thu 06:00
		# even though Thu 2026-04-30 is a holiday
		cal_unclipped = build_calendar_spec(
			calendar_code="NIGHT-UNCLIPPED",
			timezone=DEFAULT_TIMEZONE,
			shifts=overnight_shifts,
			holidays=[date(2026, 4, 30)],
			clip_overnight_on_holiday=False,
		)
		start = datetime(2026, 4, 29, 23, 30, 0)  # Wed 23:30
		due_unclipped = add_business_minutes(start, 90, cal_unclipped)
		self.assertEqual(due_unclipped, datetime(2026, 4, 30, 1, 0, 0))
		self.assertAlmostEqual(business_minutes_between(start, due_unclipped, cal_unclipped), 90.0, places=4)

		# Case B: `clip_overnight_on_holiday=True` clips at Thu 00:00 (30m used) and rolls remaining 60m to Fri 09:00
		cal_clipped = build_calendar_spec(
			calendar_code="NIGHT-CLIPPED",
			timezone=DEFAULT_TIMEZONE,
			shifts=overnight_shifts,
			holidays=[date(2026, 4, 30)],
			clip_overnight_on_holiday=True,
		)
		due_clipped = add_business_minutes(start, 90, cal_clipped)
		self.assertEqual(due_clipped, datetime(2026, 5, 1, 9, 0, 0))
		self.assertAlmostEqual(business_minutes_between(start, due_clipped, cal_clipped), 90.0, places=4)

	def test_timezone_boundary_aware_utc_vs_hcm_local_equivalence(self):
		cal = default_vn_office_calendar()
		hcm = ZoneInfo("Asia/Ho_Chi_Minh")
		# 2026-04-27 01:15 UTC == 2026-04-27 08:15 Asia/Ho_Chi_Minh
		utc_start = datetime(2026, 4, 27, 1, 15, 0, tzinfo=timezone.utc)
		hcm_start = datetime(2026, 4, 27, 8, 15, 0, tzinfo=hcm)
		iso_z = "2026-04-27T01:15:00Z"

		d1 = add_business_minutes(utc_start, 30, cal)
		d2 = add_business_minutes(hcm_start, 30, cal)
		d3 = add_business_minutes(iso_z, 30, cal)
		self.assertEqual(d1, datetime(2026, 4, 27, 8, 45, 0))
		self.assertEqual(d1, d2)
		self.assertEqual(d1, d3)

		# Snapshot JSON roundtrip preserves exact rules
		snap = WorkingCalendarSpec.from_snapshot(cal.to_snapshot_json())
		self.assertEqual(add_business_minutes(iso_z, 30, snap), d1)

	def test_zero_target_minutes_and_non_negative_elapsed_guard(self):
		cal = default_vn_office_calendar()
		# Inside working window: target=0 returns exact start
		in_shift = datetime(2026, 4, 27, 9, 10, 0)
		self.assertEqual(add_business_minutes(in_shift, 0, cal), in_shift)
		# During lunch break: target=0 snaps to next working window boundary (13:00)
		in_break = datetime(2026, 4, 27, 12, 20, 0)
		self.assertEqual(add_business_minutes(in_break, 0, cal), datetime(2026, 4, 27, 13, 0, 0))
		# Reversed timestamps never return negative minutes
		self.assertEqual(
			business_minutes_between(
				datetime(2026, 4, 27, 10, 0, 0),
				datetime(2026, 4, 27, 9, 0, 0),
				cal,
			),
			0.0,
		)
		with self.assertRaises(InvalidCalendarError):
			add_business_minutes(in_shift, -5, cal)

	def test_empty_or_zero_capacity_calendar_raises_calendar_invalid(self):
		with self.assertRaises(InvalidCalendarError):
			build_calendar_spec(calendar_code="EMPTY", timezone=DEFAULT_TIMEZONE, shifts=[])

		with self.assertRaises(InvalidCalendarError):
			build_calendar_spec(
				calendar_code="BAD-TZ",
				timezone="Mars/Olympus_Mons",
				shifts=[{"workday": "Monday", "start_time": "08:00:00", "end_time": "12:00:00"}],
			)

	def test_upstream_crm_holiday_list_write_guard_and_working_calendar_permissions(self):
		# Sales User, Team Leader, and BOD must be blocked from mutating upstream `CRM Holiday List`
		for actor in (self.staff_a, self.leader_a, self.bod_user):
			frappe.set_user(actor)
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(
					{
						"doctype": "CRM Holiday List",
						"holiday_list_name": f"UNAUTH-{frappe.generate_hash(length=6)}",
						"from_date": "2026-01-01",
						"to_date": "2026-12-31",
					}
				).insert(ignore_permissions=True)

			# Also blocked from creating/modifying `CSKH Working Calendar`
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(
					{
						"doctype": "CSKH Working Calendar",
						"calendar_code": f"UNAUTH-CAL-{frappe.generate_hash(length=6)}",
						"title": "Lịch trái phép",
						"timezone": DEFAULT_TIMEZONE,
						"shifts": [{"workday": "Monday", "start_time": "08:00:00", "end_time": "12:00:00"}],
					}
				).insert(ignore_permissions=True)

		# CSKH CRM Admin is authorized to create a holiday list and working calendar
		frappe.set_user(self.admin_user)
		hl_name = f"ADMIN-HL-{frappe.generate_hash(length=6)}"
		hl = frappe.get_doc(
			{
				"doctype": "CRM Holiday List",
				"holiday_list_name": hl_name,
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
				"holidays": [{"date": "2026-09-02", "description": "Quốc khánh"}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(hl.name, hl_name)

		cal_code = f"ADMIN-CAL-{frappe.generate_hash(length=6)}"
		cal_doc = frappe.get_doc(
			{
				"doctype": "CSKH Working Calendar",
				"calendar_code": cal_code,
				"title": "Lịch Admin hợp lệ",
				"timezone": DEFAULT_TIMEZONE,
				"holiday_list": hl_name,
				"enabled": 1,
				"shifts": [{"workday": "Monday", "start_time": "08:30:00", "end_time": "17:30:00"}],
			}
		).insert(ignore_permissions=True)
		resolved = resolve_working_calendar(cal_doc.name)
		self.assertEqual(resolved.calendar_code, cal_code)
		self.assertIn(date(2026, 9, 2), resolved.holidays)
