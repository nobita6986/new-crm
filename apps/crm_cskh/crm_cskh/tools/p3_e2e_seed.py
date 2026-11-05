"""Deterministic E2E reset & synthetic seed helper for isolated P3 test sites (`p3-e2e.localhost`, `p3-ci.localhost`, `p3-t1c.localhost`).

CLI-only (`bench --site p3-e2e.localhost execute crm_cskh.tools.p3_e2e_seed.reset_and_seed_p3`).
Strictly refuses to run on Owner stacks (`p1.localhost`, `p2.localhost`, `p3ui.localhost`) or any non-test site.
Never whitelisted over HTTP; never modifies `permissions.py`, `p1_permissions.py`, `p2_permissions.py`, or `p3_permissions.py`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.utils.password import update_password

ALLOWED_P3_SITES = {
	"p3-e2e.localhost",
	"p3-ci.localhost",
	"p3-t1c.localhost",
	"p3-test.localhost",
	"p3.localhost",
}
E2E_CALENDAR_CODE = "CAL-P3-E2E-24X7"

USERS = {
	"admin": ("cskh-p3-admin@example.test", "Quản trị CSKH P3", ("CSKH CRM Admin",)),
	"bod": ("cskh-p3-bod@example.test", "Ban Giám đốc P3", ("CSKH BOD Viewer",)),
	"leader_a": (
		"cskh-p3-leader-a@example.test",
		"=CMD|' /C calc'!A0 Nhóm CSKH Miền Bắc",
		("Sales Manager", "Sales User"),
	),
	"leader_b": (
		"cskh-p3-leader-b@example.test",
		"Nhóm CSKH Miền Nam",
		("Sales Manager", "Sales User"),
	),
	"staff_a": ("cskh-p3-staff-a@example.test", "Nhân viên A1", ("Sales User",)),
	"staff_b": (
		"cskh-p3-staff-b@example.test",
		"Nhân viên A2 (Golden 86,47)",
		("Sales User",),
	),
	"staff_c": (
		"cskh-p3-staff-c@example.test",
		"Nhân viên B1 (Mẫu số 0)",
		("Sales User",),
	),
	"staff_d": (
		"cskh-p3-staff-d@example.test",
		"Nhân viên B2 (Chuyển nhóm)",
		("Sales User",),
	),
	"outside": (
		"cskh-p3-outside@example.test",
		"Người dùng Ngoài Hệ thống CSKH",
		("Desk User",),
	),
}


def _assert_p3_site() -> str:
	site = getattr(frappe.local, "site", "")
	if site not in ALLOWED_P3_SITES:
		raise RuntimeError(f"Refusing to run p3_e2e_seed on non-P3 site: {site!r}")
	return site


def _ensure_role(role_name: str) -> None:
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def _ensure_strict_user(
	email: str,
	full_name: str,
	allowed_roles: tuple[str, ...],
	password: str,
	time_zone: str = "",
) -> None:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": full_name,
				"enabled": 1,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)

	user = frappe.get_doc("User", email)
	user.first_name = full_name
	user.last_name = ""
	user.enabled = 1
	user.user_type = "System User"
	user.language = "vi"
	user.time_zone = time_zone or ""
	user.set("roles", [{"role": r} for r in allowed_roles])
	user.save(ignore_permissions=True)
	update_password(email, password)


def _ensure_node(user: str, is_group: int, reports_to_node: str | None = None, full_name: str = "") -> str:
	payload = {
		"doctype": "CRM Sales Hierarchy",
		"user": user,
		"is_group": is_group,
		"reports_to": reports_to_node,
	}
	if full_name and frappe.db.has_column("CRM Sales Hierarchy", "full_name"):
		payload["full_name"] = full_name
	doc = frappe.get_doc(payload)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_lead_source(source_name: str) -> str | None:
	if not frappe.db.table_exists("CRM Lead Source"):
		return None
	if frappe.db.exists("CRM Lead Source", source_name):
		return source_name
	meta = frappe.get_meta("CRM Lead Source")
	payload: dict = {"doctype": "CRM Lead Source"}
	for fname in ("source_name", "lead_source", "title", "name"):
		if fname == "name" or meta.has_field(fname):
			payload[fname] = source_name
	doc = frappe.get_doc(payload).insert(ignore_permissions=True)
	return doc.name


def _ensure_e2e_24x7_calendar() -> str:
	"""Create an isolated 24x7 working calendar for deterministic E2E runs at any wall-clock hour/day."""
	if frappe.db.exists("CSKH Working Calendar", E2E_CALENDAR_CODE):
		return E2E_CALENDAR_CODE
	prev_cal_flag = getattr(frappe.flags, "cskh_seed_calendar", False)
	frappe.flags.cskh_seed_calendar = True
	try:
		shifts = [
			{
				"workday": day,
				"start_time": "00:00:00",
				"end_time": "23:59:59",
				"is_overnight": 0,
			}
			for day in (
				"Monday",
				"Tuesday",
				"Wednesday",
				"Thursday",
				"Friday",
				"Saturday",
				"Sunday",
			)
		]
		cal_doc = frappe.get_doc(
			{
				"doctype": "CSKH Working Calendar",
				"calendar_code": E2E_CALENDAR_CODE,
				"title": "Lịch 24x7 Cô lập cho Kiểm thử E2E P3 (DEV)",
				"timezone": "Asia/Ho_Chi_Minh",
				"holiday_list": None,
				"enabled": 1,
				"is_default": 0,
				"shifts": shifts,
			}
		)
		cal_doc.insert(ignore_permissions=True)
		return cal_doc.name
	finally:
		frappe.flags.cskh_seed_calendar = prev_cal_flag


def _create_lead_and_assign(
	*,
	idx_num: int,
	first_name: str,
	campaign: str,
	target_user: str,
	admin_user: str,
	assigned_dt: datetime,
	due_dt: datetime,
	lead_source: str | None = None,
) -> tuple[str, str, str]:
	from crm_cskh.services import identity as identity_service
	from crm_cskh.services import ownership
	from crm_cskh.services.phone_identity import normalize_vn_phone

	phone = f"0978{idx_num:06d}"
	canonical = normalize_vn_phone(phone)
	lead_payload = {
		"doctype": "CRM Lead",
		"first_name": first_name,
		"lead_name": first_name,
		"mobile_no": phone,
		"status": "New",
	}
	if lead_source and frappe.db.exists("CRM Lead Source", lead_source):
		lead_payload["source"] = lead_source
	lead_doc = frappe.get_doc(lead_payload).insert(ignore_permissions=True)
	identity_service.claim_canonical_identity(canonical, lead_doc.name)

	wi_doc = frappe.get_doc(
		{
			"doctype": "CSKH Work Item",
			"campaign": campaign,
			"lead": lead_doc.name,
			"status": "queued",
		}
	).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "CSKH Active Claim",
			"lead": lead_doc.name,
			"campaign": campaign,
			"work_item": wi_doc.name,
		}
	).insert(ignore_permissions=True)

	ownership.assign(
		wi_doc.name,
		target_user,
		reason="Phân bổ chiến dịch E2E P3",
		request_id=f"p3-seed-assign-{idx_num}-{frappe.generate_hash(length=6)}",
		actor=admin_user,
	)

	# Pin deterministic assignment & FIRST_ATTEMPT obligation window
	frappe.db.sql(
		"""
        update `tabCSKH Work Item`
        set assigned_at = %s, creation = %s
        where name = %s
        """,
		(assigned_dt, assigned_dt, wi_doc.name),
	)
	from crm_cskh.services.attribution import resolve_team_for_user_at

	t_node, a_stat = resolve_team_for_user_at(
		target_user, assigned_dt, allow_live_capture_for_current_tx=False
	)
	if frappe.db.has_column("CSKH Assignment History", "team_at_assignment"):
		frappe.db.sql(
			"""
            update `tabCSKH Assignment History`
            set event_at = %s, ownership_start = %s, creation = %s,
                team_at_assignment = %s, attribution_status = %s
            where work_item = %s and ownership_end is null
            """,
			(assigned_dt, assigned_dt, assigned_dt, t_node, a_stat, wi_doc.name),
		)
	else:
		frappe.db.sql(
			"""
            update `tabCSKH Assignment History`
            set event_at = %s, ownership_start = %s, creation = %s
            where work_item = %s and ownership_end is null
            """,
			(assigned_dt, assigned_dt, assigned_dt, wi_doc.name),
		)

	ob_name = frappe.db.get_value(
		"CSKH SLA Obligation",
		{"work_item": wi_doc.name, "obligation_type": "FIRST_ATTEMPT"},
		"name",
	)
	if ob_name:
		frappe.db.sql(
			"""
			update `tabCSKH SLA Obligation`
			set started_at = %s,
			    original_due_at = %s,
			    effective_due_at = %s,
			    team_at_obligation = %s,
			    attribution_status = %s,
			    evaluation_anchor_at = case when status = 'open' then null else evaluation_anchor_at end
			where name = %s
			""",
			(assigned_dt, due_dt, due_dt, t_node, a_stat, ob_name),
		)

	# Pin FIRST_CONNECTED informational obligation if present
	fc_name = frappe.db.get_value(
		"CSKH SLA Obligation",
		{"work_item": wi_doc.name, "obligation_type": "FIRST_CONNECTED"},
		"name",
	)
	if fc_name:
		fc_due = add_to_date(due_dt, minutes=60)
		frappe.db.sql(
			"""
			update `tabCSKH SLA Obligation`
			set started_at = %s,
			    original_due_at = %s,
			    effective_due_at = %s,
			    team_at_obligation = %s,
			    attribution_status = %s
			where name = %s
			""",
			(assigned_dt, fc_due, fc_due, t_node, a_stat, fc_name),
		)

	return wi_doc.name, lead_doc.name, str(ob_name or "")


def _attach_verified_call_log(
	attempt_id: str, lead_name: str, caller_user: str, start_dt: datetime, idx: int
) -> str:
	"""Attach a completed `CRM Call Log` (`duration >= 15s`) and set `verification_status = 'verified'` (`F-P3-03`)."""
	cl_doc = frappe.get_doc(
		{
			"doctype": "CRM Call Log",
			"id": f"P3-VERIFIED-CALL-{idx}-{attempt_id}",
			"type": "Outgoing",
			"status": "Completed",
			"from": "+842873001100",
			"to": f"+8497899{idx:04d}",
			"caller": caller_user,
			"duration": 45,
			"start_time": start_dt,
			"reference_doctype": "CRM Lead",
			"reference_docname": lead_name,
		}
	).insert(ignore_permissions=True)
	frappe.db.sql(
		"""
		update `tabCSKH Contact Attempt`
		set call_log = %s, verification_status = 'verified'
		where name = %s
		""",
		(cl_doc.name, attempt_id),
	)
	return cl_doc.name


def reset_and_seed_p3() -> dict:
	"""Reset transactional P1/P2/P3 data on isolated P3 site and seed deterministic SLA/KPI/QA scenarios."""
	site = _assert_p3_site()
	frappe.set_user("Administrator")
	prev_suppress = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
	frappe.flags.cskh_suppress_dirty_enqueue = True
	try:
		return _reset_and_seed_p3_inner(site)
	finally:
		frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress


def _reset_and_seed_p3_inner(site: str) -> dict:
	password = os.environ.get("P3_SEED_PASSWORD", "P2Seed-Only-2026!")

	frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
	frappe.db.set_single_value("System Settings", "country", "Vietnam")
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

	# 1. Wipe all transactional P1/P2/P3 tables cleanly
	wipe_doctypes = (
		"CSKH Export Job",
		"CSKH Dirty Scope",
		"CSKH KPI Snapshot",
		"CSKH KPI Run",
		"CSKH QA Review",
		"CSKH SLA Obligation",
		"CSKH Team Membership History",
		"CSKH Attempt Correction",
		"CSKH Contact Attempt",
		"CSKH Workflow Idempotency",
		"CSKH Idempotency Key",
		"CSKH Import Batch Lead",
		"CSKH Active Claim",
		"CSKH Assignment History",
		"CSKH Work Item",
		"CSKH Distribution Run",
		"CSKH Assignment Rule",
		"CSKH Campaign",
		"CSKH Import Batch",
		"CSKH Identity Index",
	)
	for dt_name in wipe_doctypes:
		if frappe.db.table_exists(dt_name):
			frappe.db.sql(f"delete from `tab{dt_name}`")

	for native_dt in ("CRM Call Log", "CRM Task", "FCRM Note"):
		if frappe.db.table_exists(native_dt):
			frappe.db.sql(f"delete from `tab{native_dt}`")

	frappe.db.sql("delete from `tabToDo` where reference_type in ('CRM Lead', 'CRM Task')")
	frappe.db.sql("delete from `tabDocShare` where share_doctype in ('CRM Lead', 'CRM Call Log', 'CRM Task')")
	frappe.db.sql(
		"delete from `tabFile` where attached_to_doctype in ('CSKH Import Batch', 'CRM Lead', 'CSKH Export Job')"
	)
	frappe.db.sql("delete from `tabCRM Lead`")

	for cat_dt in ("CSKH Policy", "CSKH Outcome Code"):
		if frappe.db.table_exists(cat_dt):
			frappe.db.sql(f"delete from `tab{cat_dt}`")

	from crm_cskh.p2_models import ensure_p2_seeds
	from crm_cskh.p3_models import (
		authorized_sla_write,
		ensure_p3_indexes,
		ensure_p3_seeds,
	)
	from crm_cskh.services import contact_attempt, correction, ownership
	from crm_cskh.services.attribution import record_membership_interval
	from crm_cskh.services.kpi_periods import (
		affected_standard_periods_for_date,
		today_hcm,
	)
	from crm_cskh.services.kpi_runs import execute_kpi_recompute

	ensure_p2_seeds()
	ensure_p3_indexes()
	ensure_p3_seeds()
	e2e_cal = _ensure_e2e_24x7_calendar()

	# Ensure `qa_enabled = 0` (`false`) and deterministic DEV scoring weights (`25/20/20/20/15`, `P3.md` §7.5)
	desired_policy_values = {
		"qa_enabled": 0,
		"min_attempts_enabled": 0,
		"p3_enabled": 1,
		"p3_sla_enabled": 1,
		"p3_reporting_enabled": 1,
		"working_calendar": e2e_cal,
		"weight_first_attempt_sla": 25,
		"weight_eligible_coverage": 20,
		"weight_callback_discipline": 20,
		"weight_data_hygiene": 20,
		"weight_followup_sla": 20,
		"weight_qa_quality": 15,
		"min_valid_score_weight": 60,
		"min_eligible_events_for_score": 1,
		"min_exposure_business_minutes": 60,
		"min_exposure_minutes": 60,
		"allowed_backdate_minutes": 60 * 24 * 60,
	}
	pol_updates = {k: v for k, v in desired_policy_values.items() if frappe.db.has_column("CSKH Policy", k)}
	for pol in frappe.get_all("CSKH Policy", filters={"enabled": 1}, pluck="name"):
		frappe.db.set_value("CSKH Policy", pol, pol_updates, update_modified=False)

	# Ensure real Lead Sources (`Website`, `Facebook`, `Hotline`) for `#p3-filter-source`
	src_website = _ensure_lead_source("Website")
	src_facebook = _ensure_lead_source("Facebook")
	src_hotline = _ensure_lead_source("Hotline")

	# 2. Ensure strict roles and users
	_ensure_role("CSKH CRM Admin")
	_ensure_role("CSKH BOD Viewer")
	for _key, (email, full_name, role_tuple) in USERS.items():
		_ensure_strict_user(email, full_name, role_tuple, password)

	admin_u = USERS["admin"][0]
	leader_a_u = USERS["leader_a"][0]
	leader_b_u = USERS["leader_b"][0]
	staff_a_u = USERS["staff_a"][0]
	staff_b_u = USERS["staff_b"][0]
	staff_c_u = USERS["staff_c"][0]
	staff_d_u = USERS["staff_d"][0]

	# 3. Choose deterministic timestamps strictly before `now_dt` within current HCM day/month
	now_dt = get_datetime(now_datetime()).replace(microsecond=0)
	cur_date = today_hcm()
	month_start = datetime(cur_date.year, cur_date.month, 1, 0, 0, 0)
	prev_month_end = month_start - timedelta(days=1)
	prev_month_start = datetime(prev_month_end.year, prev_month_end.month, 1, 0, 0, 0)

	# Anchor base 3h30m before `now_dt` (clamped inside current month so monthly & daily cohorts align)
	t0 = now_dt - timedelta(hours=3, minutes=30)
	if t0 < month_start + timedelta(minutes=10):
		t0 = month_start + timedelta(minutes=10)
	anchor_day = t0.date()
	month_key = f"{anchor_day.year:04d}-{anchor_day.month:02d}"
	prev_month_key = f"{prev_month_start.year:04d}-{prev_month_start.month:02d}"

	# 4. Build Sales Hierarchy & authoritative `CSKH Team Membership History` from `prev_month_start`
	frappe.db.sql("delete from `tabCRM Sales Hierarchy`")
	frappe.db.sql("delete from `tabCSKH Team Membership History`")

	team_a = _ensure_node(leader_a_u, 1, None, USERS["leader_a"][1])
	node_staff_a = _ensure_node(staff_a_u, 0, team_a, USERS["staff_a"][1])
	node_staff_b = _ensure_node(staff_b_u, 0, team_a, USERS["staff_b"][1])
	node_staff_d = _ensure_node(staff_d_u, 0, team_a, USERS["staff_d"][1])

	team_b = _ensure_node(leader_b_u, 1, None, USERS["leader_b"][1])
	node_staff_c = _ensure_node(staff_c_u, 0, team_b, USERS["staff_c"][1])

	# Reset auto-captured cutover timestamps back to `prev_month_start` so carry-over & current intervals are authoritative
	frappe.db.sql("delete from `tabCSKH Team Membership History`")
	for u_email, h_node, t_node, t_snap in (
		(leader_a_u, team_a, team_a, USERS["leader_a"][1]),
		(staff_a_u, node_staff_a, team_a, USERS["leader_a"][1]),
		(staff_b_u, node_staff_b, team_a, USERS["leader_a"][1]),
		(staff_d_u, node_staff_d, team_a, USERS["leader_a"][1]),
		(leader_b_u, team_b, team_b, USERS["leader_b"][1]),
		(staff_c_u, node_staff_c, team_b, USERS["leader_b"][1]),
	):
		record_membership_interval(
			user=u_email,
			hierarchy_node=h_node,
			team_node=t_node,
			team_name_snapshot=t_snap,
			effective_from=prev_month_start,
			source_type="hierarchy_hook",
		)

	# 5. Create Campaigns (`CAMP-P3-E2E` company-wide + `CAMP-P3-PRIVATE-A` scoped to `team_a`)
	campaign_doc = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": "CAMP-P3-E2E",
			"title": "Chiến dịch Chăm sóc Khách hàng P3",
			"status": "Active",
		}
	).insert(ignore_permissions=True)
	campaign = campaign_doc.name

	private_camp_doc = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": "CAMP-P3-PRIVATE-A",
			"title": "Chiến dịch Nội bộ Nhóm Miền Bắc",
			"status": "Active",
			"team_scope": team_a,
		}
	).insert(ignore_permissions=True)
	private_campaign_a = private_camp_doc.name

	for u in (staff_a_u, staff_b_u, staff_c_u, staff_d_u):
		frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": campaign,
				"user": u,
				"weight": 25,
				"capacity_limit": 0,
				"version": 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

	seq = 1

	# ------------------------------------------------------------------
	# A. Seed `staff_b` (`golden_employee`) -> canonical `P3.md` §7.5 & T1C Golden Vector (`86.47`):
	#    - `FIRST_ATTEMPT_SLA_RATE` (`w1=25`):  `9 / 10 = 90.00%` -> raw `22.50`
	#        (`1` carry-over obligation from `prev_month` due in current month + `8` on-time current-month
	#         + `1` untouched `breached_open`; `1` untouched `waived` excluded from SLA denominator)
	#    - `ELIGIBLE_COVERAGE` (`w2=20`):       `8 / 10 = 80.00%` -> raw `16.00`
	#        (`10` mature current-month intervals: `8` touched + `1` breached untouched + `1` waived untouched)
	#    - `VALID_OUTCOME_LOGGING` (`w3=20`):   `18 / 18 = 100.00%` -> raw `20.00` (`0` corrections)
	#    - `FOLLOWUP_SLA_RATE` (`w4=20`):       `3 / 4 = 75.00%`  -> raw `15.00` (`3` on-time + `1` late)
	#    - `CONNECTED_RATE` (grain=`ownership_interval`): `7 / 8 = 87.50%` (while `attempts_count = 18`!)
	#    - `qa_enabled = false`: raw sum `73.50 / valid_weight 85 = 86.47` (`provisional`)!
	# ------------------------------------------------------------------
	b_wis: list[str] = []
	b_leads: list[str] = []

	# A.0: `WI_0` — Carry-over interval opened in previous month (`month_start - 2h`), due at `t0 + 20m`
	seq += 1
	wi_b0_assign = month_start - timedelta(hours=2)
	wi_b0_due = t0 + timedelta(minutes=20)
	wi_b0, lead_b0, _ob_b0 = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Golden Chuyển Kỳ B00",
		campaign=campaign,
		target_user=staff_b_u,
		admin_user=admin_u,
		assigned_dt=wi_b0_assign,
		due_dt=wi_b0_due,
		lead_source=src_website,
	)
	att_b0 = contact_attempt.record_attempt(
		work_item=wi_b0,
		channel="phone_call",
		outcome_code="connected_interested",
		occurred_at=str(t0 + timedelta(minutes=5)),
		note="Liên hệ đúng hạn hồ sơ chuyển kỳ tháng trước sang kỳ hiện tại",
		idempotency_key="p3-b-carryover-0",
		actor=staff_b_u,
	)
	_attach_verified_call_log(att_b0["attempt_id"], lead_b0, staff_b_u, t0 + timedelta(minutes=5), 1)

	# A.1: `WI_1 .. WI_8` — `8` mature current-month intervals with on-time `FIRST_ATTEMPT`
	for i in range(8):
		seq += 1
		t_assign = t0 + timedelta(minutes=i + 1)
		t_due = t_assign + timedelta(minutes=25)
		wi_b, lead_b, _ob_b = _create_lead_and_assign(
			idx_num=seq,
			first_name=f"Khách Golden B{i + 1:02d}",
			campaign=campaign,
			target_user=staff_b_u,
			admin_user=admin_u,
			assigned_dt=t_assign,
			due_dt=t_due,
			lead_source=src_website,
		)
		b_wis.append(wi_b)
		b_leads.append(lead_b)

		occ_1 = t_assign + timedelta(minutes=10)
		if i < 4:
			# Schedule 4 callbacks (`3` completed on-time, `1` completed late -> `3 / 4 = 75.00%`)
			cb_due_future = now_dt + timedelta(hours=4)
			att_res = contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="callback_requested",
				occurred_at=str(occ_1),
				note=f"Gọi lần 1 khách Golden B{i + 1:02d} hẹn gọi lại",
				followup={
					"due_at": str(cb_due_future),
					"title": f"Gọi lại khách Golden B{i + 1:02d}",
				},
				idempotency_key=f"p3-b-fa-{i}",
				actor=staff_b_u,
			)
			if i < 3:
				_attach_verified_call_log(att_res["attempt_id"], lead_b, staff_b_u, occ_1, i + 2)

			cb_due_past = t_due + timedelta(minutes=30)
			task_id = att_res.get("callback_task") or frappe.db.get_value(
				"CRM Task", {"cskh_work_item": wi_b, "status": "Todo"}, "name"
			)
			if task_id:
				frappe.db.set_value("CRM Task", task_id, "due_date", cb_due_past, update_modified=False)
				frappe.db.set_value(
					"CSKH Work Item", wi_b, "next_action_due", cb_due_past, update_modified=False
				)
			fu_ob = frappe.db.get_value(
				"CSKH SLA Obligation",
				{"work_item": wi_b, "obligation_type": "FOLLOWUP"},
				"name",
			)
			if fu_ob:
				frappe.db.sql(
					"""
					update `tabCSKH SLA Obligation`
					set started_at = %s,
					    original_due_at = %s,
					    effective_due_at = %s
					where name = %s
					""",
					(occ_1, cb_due_past, cb_due_past, fu_ob),
				)

			occ_2 = cb_due_past - timedelta(minutes=5) if i < 3 else cb_due_past + timedelta(minutes=25)
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="connected_interested",
				occurred_at=str(occ_2),
				note=f"Hoàn tất lịch hẹn khách Golden B{i + 1:02d}",
				complete_open_followup=True,
				idempotency_key=f"p3-b-fu-{i}",
				actor=staff_b_u,
			)
		else:
			# `i = 4, 5, 6` -> connected_interested (`7` connected intervals total); `i = 7` -> `no_answer` (`7/8 = 87.50%`)
			out_code = "connected_interested" if i < 7 else "no_answer"
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code=out_code,
				occurred_at=str(occ_1),
				note=f"Gọi lần 1 khách Golden B{i + 1:02d} ghi nhận kết quả đầy đủ",
				idempotency_key=f"p3-b-fa-{i}",
				actor=staff_b_u,
			)

	# Add `5` follow-up info attempts across `b_wis[0..4]` so `staff_b` has `1 + 12 + 5 = 18` total attempts in period!
	for j in range(5):
		occ_extra = t0 + timedelta(minutes=95 + j * 3)
		contact_attempt.record_attempt(
			work_item=b_wis[j],
			channel="zalo",
			outcome_code="connected_interested",
			occurred_at=str(occ_extra),
			note=f"Gửi tài liệu tư vấn chi tiết qua Zalo cho khách Golden B{j + 1:02d}",
			idempotency_key=f"p3-b-extra-{j}",
			actor=staff_b_u,
		)

	# A.2: `WI_9` — `1` mature untouched `breached_open` interval (`FIRST_ATTEMPT = 9/10`, `ELIGIBLE_COVERAGE` untouched #1)
	seq += 1
	t_b9_assign = t0 + timedelta(minutes=10)
	t_b9_due = t_b9_assign + timedelta(minutes=25)
	_wi_b9, _lead_b9, ob_b9 = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Golden B09 Quá Hạn Chưa Gọi",
		campaign=campaign,
		target_user=staff_b_u,
		admin_user=admin_u,
		assigned_dt=t_b9_assign,
		due_dt=t_b9_due,
		lead_source=src_website,
	)
	if ob_b9:
		with authorized_sla_write():
			ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_b9)
			ob_doc.status = "breached_open"
			ob_doc.breached_at = t_b9_due
			ob_doc.evaluation_anchor_at = t_b9_due
			ob_doc.flags.ignore_links = True
			ob_doc.save(ignore_permissions=True)

	# A.3: `WI_10` — `1` mature untouched `waived` interval (`FIRST_ATTEMPT` excluded -> stays `9/10`; `ELIGIBLE_COVERAGE = 8/10`!)
	seq += 1
	t_b10_assign = t0 + timedelta(minutes=11)
	t_b10_due = t_b10_assign + timedelta(minutes=25)
	_wi_b10, _lead_b10, ob_b10 = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Golden B10 Miễn Trừ Kiểm Toán",
		campaign=campaign,
		target_user=staff_b_u,
		admin_user=admin_u,
		assigned_dt=t_b10_assign,
		due_dt=t_b10_due,
		lead_source=src_website,
	)
	if ob_b10:
		from crm_cskh.services import sla_engine

		if hasattr(sla_engine, "waive_obligation"):
			sla_engine.waive_obligation(
				obligation_id=ob_b10,
				reason="Miễn trừ SLA có kiểm toán theo mẫu chuẩn P3 §7.5 (hệ thống tổng đài bảo trì)",
				actor=admin_u,
			)
		else:
			with authorized_sla_write():
				ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_b10)
				ob_doc.status = "waived"
				ob_doc.waived_by = admin_u
				ob_doc.waived_at = t_b10_due + timedelta(minutes=5)
				ob_doc.waiver_reason = "Miễn trừ SLA có kiểm toán theo mẫu chuẩn P3 §7.5"
				ob_doc.flags.ignore_links = True
				ob_doc.save(ignore_permissions=True)

	# ------------------------------------------------------------------
	# B. Seed `staff_a` (`12` regular WIs + `1` prior-day WI for daily trend + `1` transferred WI + `1` `A -> D -> A` WI)
	# ------------------------------------------------------------------
	sample_waivable_ob_id = ""
	a_first_attempt_id_for_corr = ""
	a_disputed_attempt_id = ""
	for i in range(12):
		seq += 1
		t_assign = t0 + timedelta(minutes=15 + i)
		t_due = t_assign + timedelta(minutes=25)
		wi_a, _lead_a, ob_a = _create_lead_and_assign(
			idx_num=seq,
			first_name=f"Khách Nhóm A {i + 1:02d}",
			campaign=campaign,
			target_user=staff_a_u,
			admin_user=admin_u,
			assigned_dt=t_assign,
			due_dt=t_due,
			lead_source=src_facebook,
		)
		if i < 10:
			att_a = contact_attempt.record_attempt(
				work_item=wi_a,
				channel="phone_call",
				outcome_code="connected_interested" if i % 2 == 0 else "no_answer",
				occurred_at=str(t_assign + timedelta(minutes=10)),
				note=f"Liên hệ lần đầu khách A{i + 1:02d}",
				idempotency_key=f"p3-a-fa-{i}",
				actor=staff_a_u,
			)
			if i == 0:
				a_first_attempt_id_for_corr = att_a["attempt_id"]
			elif i == 1:
				a_disputed_attempt_id = att_a["attempt_id"]
		else:
			# Materialize `breached_open` for the 2 overdue obligations of `staff_a`
			with authorized_sla_write():
				ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_a)
				ob_doc.status = "breached_open"
				ob_doc.breached_at = t_due
				ob_doc.evaluation_anchor_at = t_due
				ob_doc.flags.ignore_links = True
				ob_doc.save(ignore_permissions=True)
			if not sample_waivable_ob_id:
				sample_waivable_ob_id = ob_a

	if a_disputed_attempt_id:
		frappe.db.sql(
			"update `tabCSKH Contact Attempt` set verification_status = 'disputed' where name = %s",
			(a_disputed_attempt_id,),
		)

	# Seed 1 prior-day WI on `anchor_day - 1 day` so daily trend bars also have >= 2 populated days
	prev_day_dt = datetime.combine(anchor_day - timedelta(days=1), datetime.min.time()) + timedelta(hours=10)
	seq += 1
	wi_prev_day, _lead_prev_day, _ob_prev_day = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Xu Hướng Ngày Trước",
		campaign=campaign,
		target_user=staff_a_u,
		admin_user=admin_u,
		assigned_dt=prev_day_dt,
		due_dt=prev_day_dt + timedelta(minutes=25),
		lead_source=src_facebook,
	)
	contact_attempt.record_attempt(
		work_item=wi_prev_day,
		channel="phone_call",
		outcome_code="connected_interested",
		occurred_at=str(prev_day_dt + timedelta(minutes=8)),
		note="Liên hệ đúng hạn ngày hôm trước để đối chiếu xu hướng",
		idempotency_key="p3-a-prevday-1",
		actor=staff_a_u,
	)

	# Transferred Work Item (`staff_a` -> `staff_d`): `staff_a` records on-time attempt, then handed over to `staff_d`
	seq += 1
	t_tr_assign = t0 + timedelta(minutes=30)
	t_tr_due = t_tr_assign + timedelta(minutes=25)
	wi_transferred, _lead_tr, _ob_tr = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Đã Bàn Giao Sang Nhóm B",
		campaign=campaign,
		target_user=staff_a_u,
		admin_user=admin_u,
		assigned_dt=t_tr_assign,
		due_dt=t_tr_due,
		lead_source=src_facebook,
	)
	contact_attempt.record_attempt(
		work_item=wi_transferred,
		channel="phone_call",
		outcome_code="connected_interested",
		occurred_at=str(t_tr_assign + timedelta(minutes=8)),
		note="Staff A đã gọi đúng hạn trước khi chuyển vùng",
		idempotency_key="p3-tr-a1",
		actor=staff_a_u,
	)
	ownership.reassign(
		wi_transferred,
		staff_a_u,
		staff_d_u,
		reason="Chuyển khách sang khu vực miền Nam",
		request_id="p3-tr-reassign-ad",
		actor=admin_u,
	)

	# `A -> D -> A` Work Item (`1` DISTINCT Work Item vs `3` ownership intervals + unlinked Call Log)
	seq += 1
	t_aba_assign = t0 + timedelta(minutes=35)
	t_aba_due = t_aba_assign + timedelta(minutes=30)
	wi_aba, lead_aba, _ob_aba = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Chuỗi Chuyển Giao ABA",
		campaign=campaign,
		target_user=staff_a_u,
		admin_user=admin_u,
		assigned_dt=t_aba_assign,
		due_dt=t_aba_due,
		lead_source=src_facebook,
	)
	frappe.get_doc(
		{
			"doctype": "CRM Call Log",
			"id": f"P3-UNLINKED-CALL-{lead_aba}",
			"type": "Outgoing",
			"status": "Completed",
			"from": "+842873001100",
			"to": "+84978000999",
			"caller": staff_a_u,
			"duration": 25,
			"start_time": t_aba_assign + timedelta(minutes=5),
			"reference_doctype": "CRM Lead",
			"reference_docname": lead_aba,
		}
	).insert(ignore_permissions=True)
	ownership.reassign(
		wi_aba,
		staff_a_u,
		staff_d_u,
		reason="Bàn giao tạm ca sáng",
		request_id="p3-aba-1",
		actor=admin_u,
	)
	ownership.reassign(
		wi_aba,
		staff_d_u,
		staff_a_u,
		reason="Nhận lại ca chiều",
		request_id="p3-aba-2",
		actor=admin_u,
	)
	# Pin latest interval & due_at on `wi_aba` and fulfill on time
	aba_latest_ob = frappe.db.get_value(
		"CSKH SLA Obligation",
		{
			"work_item": wi_aba,
			"obligation_type": "FIRST_ATTEMPT",
			"owner_user": staff_a_u,
			"status": "open",
		},
		"name",
	)
	if aba_latest_ob:
		frappe.db.sql(
			"""
			update `tabCSKH SLA Obligation`
			set started_at = %s,
			    original_due_at = %s,
			    effective_due_at = %s
			where name = %s
			""",
			(t_aba_assign + timedelta(minutes=10), t_aba_due, t_aba_due, aba_latest_ob),
		)
	frappe.db.sql(
		"""
        update `tabCSKH Assignment History`
        set ownership_start = %s, event_at = %s
        where work_item = %s and ownership_end is null
        """,
		(t_aba_assign + timedelta(minutes=10), t_aba_assign + timedelta(minutes=10), wi_aba),
	)
	contact_attempt.record_attempt(
		work_item=wi_aba,
		channel="phone_call",
		outcome_code="connected_interested",
		occurred_at=str(t_aba_assign + timedelta(minutes=15)),
		note="Liên hệ thành công sau khi nhận lại hồ sơ ABA",
		idempotency_key="p3-aba-att",
		actor=staff_a_u,
	)

	# ------------------------------------------------------------------
	# C. Seed `staff_d` (`team-as-of` transfer + `F-P3-04` closed private-campaign Lead in `Team A` before moving to `Team B`)
	# ------------------------------------------------------------------
	d_team_a_breached_ob = ""
	for i in range(2):
		seq += 1
		t_assign = t0 + timedelta(minutes=40 + i)
		t_due = t_assign + timedelta(minutes=20)
		wi_d1, _, ob_d1 = _create_lead_and_assign(
			idx_num=seq,
			first_name=f"Khách Chuyển Nhóm (Lúc ở A) {i + 1}",
			campaign=campaign,
			target_user=staff_d_u,
			admin_user=admin_u,
			assigned_dt=t_assign,
			due_dt=t_due,
			lead_source=src_hotline,
		)
		if i == 0:
			contact_attempt.record_attempt(
				work_item=wi_d1,
				channel="phone_call",
				outcome_code="connected_interested",
				occurred_at=str(t_assign + timedelta(minutes=8)),
				note="Gọi khi nhân sự còn thuộc Nhóm A",
				idempotency_key=f"p3-d-teama-{i}",
				actor=staff_d_u,
			)
		else:
			with authorized_sla_write():
				ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_d1)
				ob_doc.status = "breached_open"
				ob_doc.breached_at = t_due
				ob_doc.evaluation_anchor_at = t_due
				ob_doc.flags.ignore_links = True
				ob_doc.save(ignore_permissions=True)
			d_team_a_breached_ob = ob_d1

	# Seed a closed Work Item & Lead in `CAMP-P3-PRIVATE-A` (`team_scope = team_a`) while `staff_d` belongs to `Team A` (`F-P3-04`)
	seq += 1
	t_priv_assign = t0 + timedelta(minutes=44)
	t_priv_due = t_priv_assign + timedelta(minutes=20)
	private_team_a_wi, private_team_a_lead, private_team_a_ob = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Mật Nhóm Miền Bắc (Đã Đóng Trước Chuyển Nhóm)",
		campaign=private_campaign_a,
		target_user=staff_d_u,
		admin_user=admin_u,
		assigned_dt=t_priv_assign,
		due_dt=t_priv_due,
		lead_source=src_hotline,
	)
	contact_attempt.record_attempt(
		work_item=private_team_a_wi,
		channel="phone_call",
		outcome_code="connected_interested",
		occurred_at=str(t_priv_assign + timedelta(minutes=6)),
		note="Tư vấn xong chiến dịch nội bộ Nhóm A trước khi nhân sự chuyển vùng",
		idempotency_key="p3-d-priv-teama-1",
		actor=staff_d_u,
	)
	frappe.db.set_value("CSKH Work Item", private_team_a_wi, "status", "closed", update_modified=False)
	frappe.db.sql(
		"""
		update `tabCSKH Assignment History`
		set ownership_end = %s
		where work_item = %s and ownership_end is null
		""",
		(t_priv_assign + timedelta(minutes=15), private_team_a_wi),
	)
	frappe.db.sql("delete from `tabCSKH Active Claim` where work_item = %s", (private_team_a_wi,))

	# Transfer `staff_d` from `Team A` to `Team B` at `t_transfer_team = t0 + 75m`
	t_transfer_team = t0 + timedelta(minutes=75)
	frappe.db.set_value("CRM Sales Hierarchy", node_staff_d, "reports_to", team_b, update_modified=False)
	record_membership_interval(
		user=staff_d_u,
		hierarchy_node=node_staff_d,
		team_node=team_b,
		team_name_snapshot=USERS["leader_b"][1],
		effective_from=t_transfer_team,
		source_type="hierarchy_hook",
	)

	for i in range(2):
		seq += 1
		t_assign = t_transfer_team + timedelta(minutes=5 + i)
		t_due = t_assign + timedelta(minutes=20)
		wi_d2, _, _ = _create_lead_and_assign(
			idx_num=seq,
			first_name=f"Khách Chuyển Nhóm (Sau sang B) {i + 1}",
			campaign=campaign,
			target_user=staff_d_u,
			admin_user=admin_u,
			assigned_dt=t_assign,
			due_dt=t_due,
			lead_source=src_hotline,
		)
		contact_attempt.record_attempt(
			work_item=wi_d2,
			channel="phone_call",
			outcome_code="connected_interested" if i == 0 else "no_answer",
			occurred_at=str(t_assign + timedelta(minutes=8)),
			note="Gọi sau khi nhân sự đã chuyển sang Nhóm B",
			idempotency_key=f"p3-d-teamb-{i}",
			actor=staff_d_u,
		)

	# Restore strict 60-minute operational backdate policy after seeding historical timestamps
	for pol in frappe.get_all("CSKH Policy", filters={"enabled": 1}, pluck="name"):
		frappe.db.set_value("CSKH Policy", pol, "allowed_backdate_minutes", 60, update_modified=False)
	frappe.db.commit()

	# ------------------------------------------------------------------
	# D. Publish KPI Run #1 (`initial`) across prior month, prior day, and current day/month,
	#    then approve 1 Correction on `staff_a` (`restated` revision lineage) while keeping
	#    `staff_b`'s canonical `18/18` (`100%`) `VALID_OUTCOME_LOGGING_RATE` untouched!
	# ------------------------------------------------------------------
	prior_periods = {
		(p.period_type, p.period_key): p
		for d_ref in (prev_month_end.date(), prev_day_dt.date())
		for p in affected_standard_periods_for_date(d_ref)
	}
	std_periods = affected_standard_periods_for_date(anchor_day)

	for p_spec in prior_periods.values():
		execute_kpi_recompute(
			p_spec,
			run_mode="backfill",
			trigger_reason="initial_seed_prior_trend",
			actor=admin_u,
		)

	for p_spec in std_periods:
		execute_kpi_recompute(
			p_spec,
			run_mode="backfill",
			trigger_reason="initial_seed_publish",
			actor=admin_u,
		)
		execute_kpi_recompute(
			p_spec,
			run_mode="backfill",
			campaign_filter=campaign,
			trigger_reason="initial_seed_publish_campaign",
			actor=admin_u,
		)
		execute_kpi_recompute(
			p_spec,
			run_mode="backfill",
			campaign_filter=private_campaign_a,
			trigger_reason="initial_seed_publish_private_campaign",
			actor=admin_u,
		)

	# Approve 1 correction on `staff_a`'s first attempt -> creates `restated` revision lineage!
	corr_req = correction.request_correction(
		attempt=a_first_attempt_id_for_corr,
		action="correct",
		corrected_fields={"note": "Đính chính nội dung ghi chú chuẩn xác sau rà soát"},
		reason="Bổ sung chi tiết cuộc gọi theo phiếu kiểm tra nội bộ",
		auto_approve=False,
		idempotency_key="p3-seed-a-corr-1",
		actor=staff_a_u,
	)
	correction.review_correction(
		correction_id=corr_req["correction_id"],
		decision="approve",
		review_note="Đồng ý đính chính ghi chú nhân viên A1",
		idempotency_key="p3-seed-a-corr-rev-1",
		actor=admin_u,
	)

	for p_spec in std_periods:
		execute_kpi_recompute(
			p_spec,
			run_mode="restated",
			trigger_reason="correction_approved",
			trigger_reference=corr_req["correction_id"],
			actor=admin_u,
		)
		execute_kpi_recompute(
			p_spec,
			run_mode="restated",
			campaign_filter=campaign,
			trigger_reason="correction_approved",
			trigger_reference=corr_req["correction_id"],
			actor=admin_u,
		)

	# Mark all Dirty Scopes created during seed as processed so initial dashboard state is `"fresh"` + `"restated"`
	if frappe.db.table_exists("CSKH Dirty Scope"):
		frappe.db.sql("update `tabCSKH Dirty Scope` set status = 'processed'")

	frappe.clear_cache()
	frappe.db.commit()

	return {
		"site": site,
		"timezone": frappe.db.get_single_value("System Settings", "time_zone"),
		"server_now": str(now_datetime()),
		"period_key": month_key,
		"prev_period_key": prev_month_key,
		"daily_key": anchor_day.isoformat(),
		"campaign_id": campaign,
		"private_campaign_a": private_campaign_a,
		"lead_sources": [s for s in (src_website, src_facebook, src_hotline) if s],
		"team_a": team_a,
		"team_a_name": USERS["leader_a"][1],
		"team_b": team_b,
		"team_b_name": USERS["leader_b"][1],
		"users": {k: v[0] for k, v in USERS.items()},
		"golden_employee": staff_b_u,
		"zero_denominator_employee": staff_c_u,
		"transferred_employee": staff_d_u,
		"transferred_work_item": wi_transferred,
		"aba_work_item": wi_aba,
		"sample_obligation_id": sample_waivable_ob_id,
		"transferred_emp_team_a_obligation": d_team_a_breached_ob,
		"private_team_a_lead": private_team_a_lead,
		"private_team_a_work_item": private_team_a_wi,
		"private_team_a_obligation": private_team_a_ob,
	}


def set_p3_feature_flags_for_test(enabled: bool = True) -> dict:
	"""Toggle P3 feature flags on `CSKH Policy` (`F-P3-08` single-source rollback verification)."""
	_assert_p3_site()
	frappe.set_user("Administrator")
	val = 1 if enabled else 0
	flag_cols = [
		col
		for col in ("p3_enabled", "p3_sla_enabled", "p3_reporting_enabled")
		if frappe.db.has_column("CSKH Policy", col)
	]
	updates = {col: val for col in flag_cols}
	if updates:
		for pol in frappe.get_all("CSKH Policy", pluck="name"):
			frappe.db.set_value("CSKH Policy", pol, updates, update_modified=False)
	frappe.clear_cache()
	frappe.db.commit()
	return {"enabled": bool(enabled), "updated_columns": flag_cols}


def mark_period_dirty_for_test(period_key: str | None = None) -> dict:
	"""Insert a `pending` `CSKH Dirty Scope` row on the isolated P3 test site to verify stale banner UI."""
	_assert_p3_site()
	frappe.set_user("Administrator")
	from crm_cskh.services.kpi_periods import resolve_period_spec, today_hcm
	from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation

	if period_key:
		spec = resolve_period_spec("monthly", period_key)
		target_d = min(spec.to_date, today_hcm())
		if target_d < spec.from_date:
			target_d = spec.from_date
	else:
		target_d = today_hcm()

	prev_suppress = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
	frappe.flags.cskh_suppress_dirty_enqueue = True
	try:
		schedule_post_commit_dirty_invalidation(
			event_dt=target_d,
			reason="e2e_stale_banner_check",
		)
		frappe.db.commit()
	finally:
		frappe.flags.cskh_suppress_dirty_enqueue = prev_suppress
	return {"ok": True, "dirty_date": target_d.isoformat()}


def get_export_private_file_url(export_id: str) -> str | None:
	"""Return the raw `/private/files/...` URL for `export_id` on the isolated P3 site (for `S11`/`S12` verification)."""
	_assert_p3_site()
	if not export_id:
		return None
	if frappe.db.table_exists("CSKH Export Job"):
		for col in ("file_url", "private_file_url"):
			if frappe.db.has_column("CSKH Export Job", col):
				val = frappe.db.get_value("CSKH Export Job", export_id, col)
				if val:
					return str(val)
	f_url = frappe.db.get_value(
		"File",
		{"attached_to_doctype": "CSKH Export Job", "attached_to_name": export_id},
		"file_url",
	)
	return str(f_url) if f_url else None


def temporarily_revoke_leader_team(user: str, team: str, revoke: bool = True) -> dict:
	"""Temporarily revoke or restore `Leader A`'s role & hierarchy grant on `p3ui.localhost` (`S12` test)."""
	_assert_p3_site()
	frappe.set_user("Administrator")
	if revoke:
		u_doc = frappe.get_doc("User", user)
		u_doc.set("roles", [{"role": "Sales User"}])
		u_doc.save(ignore_permissions=True)
		if team and frappe.db.exists("CRM Sales Hierarchy", team):
			frappe.db.set_value(
				"CRM Sales Hierarchy",
				team,
				"user",
				USERS["admin"][0],
				update_modified=False,
			)
	else:
		u_doc = frappe.get_doc("User", user)
		u_doc.set("roles", [{"role": "Sales Manager"}, {"role": "Sales User"}])
		u_doc.save(ignore_permissions=True)
		if team and frappe.db.exists("CRM Sales Hierarchy", team):
			frappe.db.set_value(
				"CRM Sales Hierarchy",
				team,
				"user",
				user,
				update_modified=False,
			)
	frappe.clear_cache(user=user)
	frappe.clear_cache()
	frappe.db.commit()
	return {"user": user, "team": team, "revoked": bool(revoke)}


def wipe_p3_site_clean() -> dict:
	"""Wipe transactional P1/P2/P3 records while preserving P2/P3 catalog seeds (for isolated JUnit suite runs)."""
	site = _assert_p3_site()
	frappe.set_user("Administrator")
	wipe_doctypes = (
		"CSKH Export Job",
		"CSKH Dirty Scope",
		"CSKH KPI Snapshot",
		"CSKH KPI Run",
		"CSKH QA Review",
		"CSKH SLA Obligation",
		"CSKH Team Membership History",
		"CSKH Attempt Correction",
		"CSKH Contact Attempt",
		"CSKH Workflow Idempotency",
		"CSKH Idempotency Key",
		"CSKH Import Batch Lead",
		"CSKH Active Claim",
		"CSKH Assignment History",
		"CSKH Work Item",
		"CSKH Distribution Run",
		"CSKH Assignment Rule",
		"CSKH Campaign",
		"CSKH Import Batch",
		"CSKH Identity Index",
	)
	for dt_name in wipe_doctypes:
		if frappe.db.table_exists(dt_name):
			frappe.db.sql(f"delete from `tab{dt_name}`")
	for native_dt in ("CRM Call Log", "CRM Task", "FCRM Note"):
		if frappe.db.table_exists(native_dt):
			frappe.db.sql(f"delete from `tab{native_dt}`")
	frappe.db.sql("delete from `tabToDo` where reference_type in ('CRM Lead', 'CRM Task')")
	frappe.db.sql("delete from `tabDocShare` where share_doctype in ('CRM Lead', 'CRM Call Log', 'CRM Task')")
	frappe.db.sql("delete from `tabCRM Lead`")

	if frappe.db.table_exists("CSKH Working Calendar") and frappe.db.exists(
		"CSKH Working Calendar", E2E_CALENDAR_CODE
	):
		frappe.db.sql("delete from `tabCSKH Working Shift` where parent = %s", (E2E_CALENDAR_CODE,))
		frappe.db.sql("delete from `tabCSKH Working Calendar` where name = %s", (E2E_CALENDAR_CODE,))

	from crm_cskh.p2_models import ensure_p2_seeds
	from crm_cskh.p3_models import DEFAULT_CALENDAR_CODE, ensure_p3_indexes, ensure_p3_seeds

	ensure_p2_seeds()
	ensure_p3_indexes()
	ensure_p3_seeds()
	if frappe.db.table_exists("CSKH Policy") and frappe.db.has_column("CSKH Policy", "working_calendar"):
		for pol in frappe.get_all("CSKH Policy", pluck="name"):
			frappe.db.set_value(
				"CSKH Policy", pol, "working_calendar", DEFAULT_CALENDAR_CODE, update_modified=False
			)
	frappe.clear_cache()
	frappe.db.commit()
	return {"site": site, "wiped": True}
