"""Deterministic E2E reset & synthetic seed helper for isolated P3 sites (`p3ui.localhost`, `p3.localhost`, `p3-ci.localhost`).

CLI-only (`bench --site p3ui.localhost execute crm_cskh.tools.p3_e2e_seed.reset_and_seed_p3`).
Strictly refuses to run on `p1.localhost`, `p2.localhost`, or any non-P3 site.
Never whitelisted over HTTP; never modifies `permissions.py`, `p1_permissions.py`, `p2_permissions.py`, or `p3_permissions.py`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.utils.password import update_password

ALLOWED_P3_SITES = {"p3ui.localhost", "p3.localhost", "p3-ci.localhost"}

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
	from crm_cskh.p3_models import authorized_sla_write
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

	# Ensure `qa_enabled = 0` (`false`) and deterministic DEV scoring weights (`25/20/20/20/15`)
	for pol in frappe.get_all("CSKH Policy", filters={"enabled": 1}, pluck="name"):
		frappe.db.set_value(
			"CSKH Policy",
			pol,
			{
				"qa_enabled": 0,
				"min_attempts_enabled": 0,
				"p3_sla_enabled": 1,
				"weight_first_attempt_sla": 25,
				"weight_eligible_coverage": 20,
				"weight_followup_sla": 20,
				"weight_data_hygiene": 20,
				"weight_callback_discipline": 20,
				"weight_qa_quality": 15,
				"min_valid_score_weight": 60,
				"min_eligible_events_for_score": 1,
				"allowed_backdate_minutes": 60 * 24 * 30,
			},
			update_modified=False,
		)

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
	# Anchor base 3 hours before `now_dt` (clamped inside current month so monthly/daily match)
	t0 = now_dt - timedelta(hours=3, minutes=30)
	if t0 < month_start:
		t0 = month_start + timedelta(minutes=5)
	anchor_day = t0.date()
	month_key = f"{anchor_day.year:04d}-{anchor_day.month:02d}"

	# 4. Build Sales Hierarchy & authoritative `CSKH Team Membership History` from `month_start`
	frappe.db.sql("delete from `tabCRM Sales Hierarchy`")
	frappe.db.sql("delete from `tabCSKH Team Membership History`")

	team_a = _ensure_node(leader_a_u, 1, None, USERS["leader_a"][1])
	node_staff_a = _ensure_node(staff_a_u, 0, team_a, USERS["staff_a"][1])
	node_staff_b = _ensure_node(staff_b_u, 0, team_a, USERS["staff_b"][1])
	node_staff_d = _ensure_node(staff_d_u, 0, team_a, USERS["staff_d"][1])

	team_b = _ensure_node(leader_b_u, 1, None, USERS["leader_b"][1])
	node_staff_c = _ensure_node(staff_c_u, 0, team_b, USERS["staff_c"][1])

	# Reset auto-captured cutover timestamps back to `hist_start` (`month_start - 2d`) so all seeded intervals are authoritative
	hist_start = month_start - timedelta(days=2)
	frappe.db.sql("delete from `tabCSKH Team Membership History`")
	record_membership_interval(
		user=leader_a_u,
		hierarchy_node=team_a,
		team_node=team_a,
		team_name_snapshot=USERS["leader_a"][1],
		effective_from=hist_start,
		source_type="hierarchy_hook",
	)
	record_membership_interval(
		user=staff_a_u,
		hierarchy_node=node_staff_a,
		team_node=team_a,
		team_name_snapshot=USERS["leader_a"][1],
		effective_from=hist_start,
		source_type="hierarchy_hook",
	)
	record_membership_interval(
		user=staff_b_u,
		hierarchy_node=node_staff_b,
		team_node=team_a,
		team_name_snapshot=USERS["leader_a"][1],
		effective_from=hist_start,
		source_type="hierarchy_hook",
	)
	record_membership_interval(
		user=staff_d_u,
		hierarchy_node=node_staff_d,
		team_node=team_a,
		team_name_snapshot=USERS["leader_a"][1],
		effective_from=hist_start,
		source_type="hierarchy_hook",
	)
	record_membership_interval(
		user=leader_b_u,
		hierarchy_node=team_b,
		team_node=team_b,
		team_name_snapshot=USERS["leader_b"][1],
		effective_from=hist_start,
		source_type="hierarchy_hook",
	)
	record_membership_interval(
		user=staff_c_u,
		hierarchy_node=node_staff_c,
		team_node=team_b,
		team_name_snapshot=USERS["leader_b"][1],
		effective_from=hist_start,
		source_type="hierarchy_hook",
	)

	# 5. Create Campaign & Assignment Rules
	campaign_doc = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": "CAMP-P3-E2E",
			"title": "Chiến dịch Chăm sóc Khách hàng P3",
			"status": "Active",
		}
	).insert(ignore_permissions=True)
	campaign = campaign_doc.name

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
	# A. Seed `staff_b` (`golden_employee`) -> exact `90 / 80 / 100 / 75 -> 86.47` (`7350 / 85` provisional):
	#    - C1 `FIRST_ATTEMPT_SLA_RATE`: 9 / 10 = 90.0% (`w1=25 -> 22.5 pts`, 1 prior-month carry-over + 8 on-time + 1 breached + 1 waived)
	#    - C2 `ELIGIBLE_COVERAGE`:      8 / 10 = 80.0% (`w2=20 -> 16.0 pts`, 8 touched of 10 assigned intervals in period)
	#    - C3 `VALID_OUTCOME_LOGGING`:  15 / 15 = 100.0% (`w3=20 -> 20.0 pts`, all complete fields, 1 approved correction)
	#    - C4 `FOLLOWUP_SLA_RATE`:      3 / 4 = 75.0% (`w4=20 -> 15.0 pts`, 3 on-time + 1 late)
	# ------------------------------------------------------------------
	from crm_cskh.services import sla_engine

	b_wis: list[str] = []
	b_first_attempt_id_for_corr = ""

	# 1 carry-over interval assigned prior to `month_start` whose `FIRST_ATTEMPT` is due & fulfilled in `[t0, t0 + 20m]`
	seq += 1
	wi_b_prior, _lead_bp, _ob_bp = _create_lead_and_assign(
		idx_num=seq,
		first_name="Khách Golden B00 (Kỳ trước chuyển sang)",
		campaign=campaign,
		target_user=staff_b_u,
		admin_user=admin_u,
		assigned_dt=month_start - timedelta(hours=7),
		due_dt=t0 + timedelta(minutes=20),
	)
	att_bp = contact_attempt.record_attempt(
		work_item=wi_b_prior,
		channel="phone_call",
		outcome_code="connected_interested",
		occurred_at=str(t0 + timedelta(minutes=4)),
		note="Hoàn tất liên hệ lần đầu đúng hạn hồ sơ chuyển kỳ",
		idempotency_key="p3-b-fa-prior",
		actor=staff_b_u,
	)
	b_first_attempt_id_for_corr = att_bp["attempt_id"]

	for i in range(10):
		seq += 1
		t_assign = t0 + timedelta(minutes=i)
		t_due = t_assign + timedelta(minutes=25)
		wi_b, _lead_b, ob_b = _create_lead_and_assign(
			idx_num=seq,
			first_name=f"Khách Golden B{i + 1:02d}",
			campaign=campaign,
			target_user=staff_b_u,
			admin_user=admin_u,
			assigned_dt=t_assign,
			due_dt=t_due,
		)
		b_wis.append(wi_b)

		if i < 4:
			# Touch on-time + schedule callback due at `t_due + 30m` (`3` on-time, `1` late -> `3/4 = 75%`)
			occ_1 = t_assign + timedelta(minutes=10)
			cb_due_future = now_dt + timedelta(hours=4)
			att_res = contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="callback_requested",
				occurred_at=str(occ_1),
				note=f"Gọi lần 1 khách B{i + 1:02d} hẹn gọi lại",
				followup={
					"due_at": str(cb_due_future),
					"title": f"Gọi lại khách B{i + 1:02d}",
				},
				idempotency_key=f"p3-b-fa-{i}",
				actor=staff_b_u,
			)
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
			occ_2 = cb_due_past - timedelta(minutes=5) if i < 3 else cb_due_past + timedelta(minutes=8)
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="connected_interested",
				occurred_at=str(occ_2),
				note=f"Hoàn tất lịch hẹn khách B{i + 1:02d}",
				complete_open_followup=True,
				idempotency_key=f"p3-b-fu-{i}",
				actor=staff_b_u,
			)
		elif i < 8:
			# Touch on-time (`connected_interested`, no followup)
			occ_1 = t_assign + timedelta(minutes=10)
			contact_attempt.record_attempt(
				work_item=wi_b,
				channel="phone_call",
				outcome_code="connected_interested",
				occurred_at=str(occ_1),
				note=f"Gọi lần 1 khách B{i + 1:02d} kết nối thành công",
				idempotency_key=f"p3-b-fa-{i}",
				actor=staff_b_u,
			)
		elif i == 8:
			# Untouched `breached_open` (`FIRST_ATTEMPT` breached -> `0/1` in FA SLA & `0/1` in ELIGIBLE_COVERAGE)
			with authorized_sla_write():
				ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_b)
				ob_doc.status = "breached_open"
				ob_doc.breached_at = t_due
				ob_doc.evaluation_anchor_at = t_due
				ob_doc.flags.ignore_links = True
				ob_doc.save(ignore_permissions=True)
		else:
			# `i == 9`: Untouched `waived` (`excluded` from FA SLA -> `9/10`, included in ELIGIBLE_COVERAGE -> `8/10`!)
			sla_engine.waive_obligation(
				ob_b,
				reason="Miễn trừ SLA hợp lệ do hệ thống tổng đài bảo trì đột xuất",
				actor=admin_u,
			)

	# Add 2 more attempts on `b_wis[0]` and `b_wis[1]` -> `15/15 = 100.0%` valid outcome logging!
	for j in range(2):
		occ_extra = t0 + timedelta(minutes=95 + j * 5)
		contact_attempt.record_attempt(
			work_item=b_wis[j],
			channel="zalo",
			outcome_code="connected_interested",
			occurred_at=str(occ_extra),
			note=f"Gửi thông tin báo giá bổ sung lần {j + 3}",
			idempotency_key=f"p3-b-extra-{j}",
			actor=staff_b_u,
		)

	# ------------------------------------------------------------------
	# B. Seed `staff_a` (`10` regular WIs + `1` transferred WI + `1` `A -> D -> A` WI)
	# ------------------------------------------------------------------
	sample_waivable_ob_id = ""
	for i in range(10):
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
		)
		if i < 8:
			contact_attempt.record_attempt(
				work_item=wi_a,
				channel="phone_call",
				outcome_code="connected_interested" if i % 2 == 0 else "no_answer",
				occurred_at=str(t_assign + timedelta(minutes=10)),
				note=f"Liên hệ lần đầu khách A{i + 1:02d}",
				idempotency_key=f"p3-a-fa-{i}",
				actor=staff_a_u,
			)
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
	# C. Seed `staff_d` (`team-as-of` transfer: 2 WIs in `Team A`, then move to `Team B` + 2 WIs in `Team B`)
	# ------------------------------------------------------------------
	for i in range(2):
		seq += 1
		t_assign = t0 + timedelta(minutes=40 + i)
		t_due = t_assign + timedelta(minutes=20)
		wi_d1, _, _ = _create_lead_and_assign(
			idx_num=seq,
			first_name=f"Khách Chuyển Nhóm (Lúc ở A) {i + 1}",
			campaign=campaign,
			target_user=staff_d_u,
			admin_user=admin_u,
			assigned_dt=t_assign,
			due_dt=t_due,
		)
		contact_attempt.record_attempt(
			work_item=wi_d1,
			channel="phone_call",
			outcome_code="connected_interested",
			occurred_at=str(t_assign + timedelta(minutes=8)),
			note="Gọi khi nhân sự còn thuộc Nhóm A",
			idempotency_key=f"p3-d-teama-{i}",
			actor=staff_d_u,
		)

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
	# D. Publish KPI Run #1 (`initial`), then approve 1 Correction on `staff_b` (`19/20 -> 86.47`)
	#    and publish KPI Run #2 (`restated`) across all standard grains!
	# ------------------------------------------------------------------
	std_periods = affected_standard_periods_for_date(anchor_day)
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

	# Approve 1 correction on `staff_b`'s first attempt -> `VALID_OUTCOME_LOGGING_RATE = 19/20 = 95%` -> `86.47`!
	corr_req = correction.request_correction(
		attempt=b_first_attempt_id_for_corr,
		action="correct",
		corrected_fields={"note": "Đính chính nội dung ghi chú chuẩn xác sau rà soát"},
		reason="Bổ sung chi tiết hẹn gọi lại theo phiếu kiểm tra",
		auto_approve=False,
		idempotency_key="p3-seed-b-corr-1",
		actor=staff_b_u,
	)
	correction.review_correction(
		correction_id=corr_req["correction_id"],
		decision="approve",
		review_note="Đồng ý đính chính ghi chú",
		idempotency_key="p3-seed-b-corr-rev-1",
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
		"daily_key": anchor_day.isoformat(),
		"campaign_id": campaign,
		"team_a": team_a,
		"team_b": team_b,
		"users": {k: v[0] for k, v in USERS.items()},
		"golden_employee": staff_b_u,
		"zero_denominator_employee": staff_c_u,
		"transferred_employee": staff_d_u,
		"transferred_work_item": wi_transferred,
		"aba_work_item": wi_aba,
		"sample_obligation_id": sample_waivable_ob_id,
	}


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

	from crm_cskh.p2_models import ensure_p2_seeds
	from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds

	ensure_p2_seeds()
	ensure_p3_indexes()
	ensure_p3_seeds()
	frappe.clear_cache()
	frappe.db.commit()
	return {"site": site, "wiped": True}
