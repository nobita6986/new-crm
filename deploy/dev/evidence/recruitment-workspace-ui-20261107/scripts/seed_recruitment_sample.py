#!/usr/bin/env python3
"""Seed synthetic recruitment outreach + P3 KPI sample data on isolated `recruit-ui.localhost`."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import sys

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.password import update_password

from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.patches.v2_0.p2_schema_and_seeds import (
	ensure_p2_custom_fields,
	ensure_p2_indexes,
	ensure_p2_seeds,
)
from crm_cskh.services import contact_attempt
from crm_cskh.services import identity as identity_service
from crm_cskh.services import ownership
from crm_cskh.services.attribution import record_membership_interval
from crm_cskh.services.kpi_periods import affected_standard_periods_for_date, today_hcm
from crm_cskh.services.kpi_runs import execute_kpi_recompute
from crm_cskh.services.phone_identity import normalize_vn_phone

ALLOWED_SITE = "recruit-ui.localhost"
SEED_PASSWORD = os.environ.get("RECRUIT_UI_PASSWORD", "RecruitUiSeed-2026!")


def ensure_role(role_name: str) -> None:
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def ensure_user(email: str, full_name: str, roles: tuple[str, ...]) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": full_name,
				"enabled": 1,
				"language": "vi",
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.first_name = full_name
	user.last_name = ""
	user.enabled = 1
	user.language = "vi"
	user.user_type = "System User"
	user.set("roles", [{"role": r} for r in roles])
	user.save(ignore_permissions=True)
	update_password(email, SEED_PASSWORD)
	return email


def ensure_hierarchy(
	user: str, is_group: int, reports_to_user: str | None = None, full_name: str = ""
) -> str:
	existing = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, "name")
	if existing:
		return existing
	parent = (
		frappe.db.get_value("CRM Sales Hierarchy", {"user": reports_to_user}, "name")
		if reports_to_user
		else None
	)
	payload = {
		"doctype": "CRM Sales Hierarchy",
		"user": user,
		"is_group": is_group,
		"reports_to": parent,
	}
	if full_name and frappe.db.has_column("CRM Sales Hierarchy", "full_name"):
		payload["full_name"] = full_name
	doc = frappe.get_doc(payload).insert(ignore_permissions=True)
	return doc.name


def ensure_canonical_lead(
	first_name: str,
	phone: str,
	status: str = "New",
	source: str | None = None,
	organization: str | None = None,
	job_title: str | None = None,
	owner: str | None = None,
) -> str:
	canonical = normalize_vn_phone(phone)
	idx_lead = frappe.db.get_value("CRM Lead", {"mobile_no": phone}, "name")
	if idx_lead:
		return idx_lead

	if source and frappe.db.table_exists("CRM Lead Source") and not frappe.db.exists("CRM Lead Source", source):
		frappe.get_doc({"doctype": "CRM Lead Source", "source_name": source}).insert(ignore_permissions=True)

	payload = {
		"doctype": "CRM Lead",
		"first_name": first_name,
		"lead_name": first_name,
		"mobile_no": phone,
		"status": status,
	}
	if organization:
		payload["organization"] = organization
	if job_title:
		payload["job_title"] = job_title
	if owner:
		payload["lead_owner"] = owner
	if source and frappe.db.exists("CRM Lead Source", source):
		payload["source"] = source

	doc = frappe.get_doc(payload).insert(ignore_permissions=True)
	identity_service.claim_canonical_identity(canonical, doc.name)
	return doc.name


def ensure_deal(
	organization_or_person: str,
	owner: str,
	status: str = "Qualification",
	lead_name: str | None = None,
	annual_revenue: float = 120000000,
) -> str:
	org_name = organization_or_person
	if frappe.db.table_exists("CRM Organization"):
		if not frappe.db.exists("CRM Organization", {"organization_name": organization_or_person}):
			org_doc = frappe.get_doc(
				{
					"doctype": "CRM Organization",
					"organization_name": organization_or_person,
					"annual_revenue": annual_revenue,
				}
			).insert(ignore_permissions=True)
			org_name = org_doc.name
		else:
			org_name = frappe.db.get_value(
				"CRM Organization", {"organization_name": organization_or_person}, "name"
			)

	deal_status = status
	if not frappe.db.exists("CRM Deal Status", deal_status):
		statuses = frappe.get_all("CRM Deal Status", pluck="name", limit=1)
		deal_status = statuses[0] if statuses else None

	payload = {
		"doctype": "CRM Deal",
		"deal_owner": owner,
		"annual_revenue": annual_revenue,
	}
	if deal_status:
		payload["status"] = deal_status
	if frappe.db.has_column("CRM Deal", "organization"):
		payload["organization"] = org_name
	if frappe.db.has_column("CRM Deal", "organization_name"):
		payload["organization_name"] = organization_or_person
	if lead_name and frappe.db.has_column("CRM Deal", "lead"):
		payload["lead"] = lead_name

	doc = frappe.get_doc(payload).insert(ignore_permissions=True)
	return doc.name


def wipe_clean() -> dict:
	"""Wipe transactional P1/P2/P3 rows on isolated `recruit-ui.localhost` while restoring P2/P3 catalog seeds."""
	site = getattr(frappe.local, "site", "")
	if site != ALLOWED_SITE:
		raise RuntimeError(f"Refusing to run recruitment UI wipe on site {site!r}")

	frappe.set_user("Administrator")
	ensure_p2_custom_fields()
	ensure_p2_indexes()
	ensure_p3_indexes()

	for dt in (
		"CSKH QA Review",
		"CSKH KPI Snapshot",
		"CSKH KPI Run",
		"CSKH Dirty Scope",
		"CSKH SLA Obligation",
		"CSKH Export Job",
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
		"CSKH Policy",
		"CSKH Outcome Code",
		"CRM Call Log",
		"CRM Task",
		"FCRM Note",
		"CRM Deal",
		"CRM Organization",
		"CRM Lead",
		"CRM Sales Hierarchy",
	):
		if frappe.db.table_exists(dt):
			frappe.db.sql(f"delete from `tab{dt}`")
	frappe.db.sql("delete from `tabToDo` where reference_type in ('CRM Lead', 'CRM Task', 'CRM Deal')")
	frappe.db.sql(
		"delete from `tabDocShare` where share_doctype in ('CRM Lead', 'CRM Call Log', 'CRM Task', 'CRM Deal')"
	)
	frappe.db.sql("delete from `tabSeries` where name in ('CRM-LEAD-2026-', 'CRM-DEAL-2026-')")

	ensure_p2_seeds()
	ensure_p3_seeds()
	frappe.clear_cache()
	frappe.db.commit()
	return {"site": site, "wiped": True}


def run_seed() -> dict:
	site = wipe_clean()["site"]
	frappe.db.set_single_value("System Settings", "language", "vi")
	frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
	frappe.db.set_single_value("System Settings", "country", "Vietnam")
	frappe.db.set_single_value("System Settings", "setup_complete", 1)
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
	frappe.db.set_value("User", "Administrator", "language", "vi", update_modified=False)

	desired_policy_values = {
		"qa_enabled": 0,
		"min_attempts_enabled": 0,
		"p3_enabled": 1,
		"p3_sla_enabled": 1,
		"p3_reporting_enabled": 1,
		"min_valid_score_weight": 60,
		"min_eligible_events_for_score": 1,
		"min_exposure_business_minutes": 0,
		"min_exposure_minutes": 0,
	}
	pol_updates = {
		k: v for k, v in desired_policy_values.items() if frappe.db.has_column("CSKH Policy", k)
	}
	for pol in frappe.get_all("CSKH Policy", filters={"enabled": 1}, pluck="name"):
		frappe.db.set_value("CSKH Policy", pol, pol_updates, update_modified=False)

	ensure_role("CSKH CRM Admin")
	ensure_role("CSKH BOD Viewer")

	admin_email = ensure_user(
		"admin.tuyendung@example.test",
		"Quản trị CRM Tuyển dụng",
		("CSKH CRM Admin",),
	)
	leader_name = "Nhóm Tuyển dụng Khu vực Bắc Ninh"
	leader_email = ensure_user(
		"leader.tuyendung@example.test",
		leader_name,
		("Sales Manager", "Sales User"),
	)
	staff_a_email = ensure_user(
		"nhanvien.a@example.test",
		"Nhân viên Tuyển dụng Minh",
		("Sales User",),
	)
	staff_b_email = ensure_user(
		"nhanvien.b@example.test",
		"Nhân viên Tuyển dụng Hà",
		("Sales User",),
	)
	bod_email = ensure_user(
		"bod.tuyendung@example.test",
		"Ban Giám đốc",
		("CSKH BOD Viewer",),
	)

	team_a = ensure_hierarchy(leader_email, 1, None, leader_name)
	node_staff_a = ensure_hierarchy(staff_a_email, 0, leader_email, "Nhân viên Tuyển dụng Minh")
	node_staff_b = ensure_hierarchy(staff_b_email, 0, leader_email, "Nhân viên Tuyển dụng Hà")

	cur_date = today_hcm()
	prev_month_end = datetime(cur_date.year, cur_date.month, 1, 0, 0, 0) - timedelta(days=1)
	prev_month_start = datetime(prev_month_end.year, prev_month_end.month, 1, 0, 0, 0)
	frappe.db.sql("delete from `tabCSKH Team Membership History`")
	for u_email, h_node in (
		(leader_email, team_a),
		(staff_a_email, node_staff_a),
		(staff_b_email, node_staff_b),
	):
		record_membership_interval(
			user=u_email,
			hierarchy_node=h_node,
			team_node=team_a,
			team_name_snapshot=leader_name,
			effective_from=prev_month_start,
			source_type="hierarchy_hook",
		)

	# Campaign & rules
	camp_code = "TD-KTV-2026Q4"
	camp_doc = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": camp_code,
			"title": "Đợt liên hệ Hồ sơ Kỹ thuật viên & Vận hành Q4 (Mẫu)",
			"status": "Active",
			"team_scope": team_a,
		}
	).insert(ignore_permissions=True)
	camp_name = camp_doc.name

	for u in (staff_a_email, staff_b_email):
		frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": camp_name,
				"user": u,
				"weight": 50,
				"capacity_limit": 20,
				"version": 1,
				"enabled": 1,
				"team": team_a,
			}
		).insert(ignore_permissions=True)

	def assign_lead_to_staff(lead_id: str, target_user: str, reason: str) -> str:
		wi = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"campaign": camp_name,
				"lead": lead_id,
				"status": "queued",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_id,
				"campaign": camp_name,
				"work_item": wi.name,
			}
		).insert(ignore_permissions=True)
		ownership.assign(
			wi.name,
			target_user,
			reason=reason,
			request_id=f"seed-assign-{lead_id}",
			actor=admin_email,
		)
		return wi.name

	# Assigned leads for Staff A (Nhân viên Tuyển dụng Minh)
	lead_1 = ensure_canonical_lead(
		"Nguyễn Văn Minh (Mẫu)",
		"0900001011",
		status="New",
		source="Facebook",
		job_title="Kỹ thuật viên cơ khí",
	)
	wi_1 = assign_lead_to_staff(lead_1, staff_a_email, "Giao hồ sơ mới tiếp nhận đợt Q4")

	# Seed recruitment salary & work shift note with XSS/formula payload on lead_1 to verify safe inert rendering
	frappe.get_doc(
		{
			"doctype": "FCRM Note",
			"title": "Ghi chú nội bộ CRM =1+1",
			"content": "Người liên hệ hỏi mức lương và ca làm việc <script>window.__xssTriggered=1</script> <img src=x onerror=window.__xssTriggered=2>",
			"reference_doctype": "CRM Lead",
			"reference_docname": lead_1,
		}
	).insert(ignore_permissions=True)

	lead_2 = ensure_canonical_lead(
		"Trần Thị Lan (Mẫu)",
		"0900001012",
		status="Contacted",
		source="Website",
		job_title="Nhân viên vận hành kho",
	)
	wi_2 = assign_lead_to_staff(lead_2, staff_a_email, "Giao hồ sơ cần hẹn liên hệ lại")

	lead_3 = ensure_canonical_lead(
		"Lê Hoàng Nam (Mẫu)",
		"0900001013",
		status="Contacted",
		source="Reference",
		job_title="Tổ trưởng chuyền lắp ráp",
	)
	wi_3 = assign_lead_to_staff(lead_3, staff_a_email, "Giao hồ sơ giới thiệu nội bộ")

	# Assigned lead for Staff B
	lead_4 = ensure_canonical_lead(
		"Phạm Thu Hà (Mẫu)",
		"0900001014",
		status="New",
		source="Web Form",
		job_title="Nhân viên kiểm tra chất lượng (QC)",
	)
	wi_4 = assign_lead_to_staff(lead_4, staff_b_email, "Giao hồ sơ đợt Q4 nhóm A")

	# Unassigned eligible pool leads (for Kho hồ sơ liên hệ /cskh/pool)
	lead_pool_1 = ensure_canonical_lead(
		"Võ Quốc Bảo (Mẫu - Chờ giao)",
		"0900001021",
		status="New",
		source="Facebook",
		job_title="Thợ điện công nghiệp",
	)
	lead_pool_2 = ensure_canonical_lead(
		"Đặng Mỹ Linh (Mẫu - Chờ giao)",
		"0900001022",
		status="New",
		source="Website",
		job_title="Nhân viên đóng gói",
	)

	# Seed 1 callback_requested attempt on wi_2 + 1 converted ("Hoàn tất mục tiêu liên hệ chiến dịch") attempt on wi_3
	frappe.set_user(staff_a_email)
	due_soon = add_to_date(now_datetime(), hours=2)
	contact_attempt.record_attempt(
		work_item=wi_2,
		channel="phone_call",
		outcome_code="callback_requested",
		note="Ứng viên đang ca sáng, hẹn gọi lại sau giờ nghỉ trưa để trao đổi mức lương và lịch phỏng vấn.",
		followup_due_at=due_soon.strftime("%Y-%m-%d %H:%M:%S"),
		followup_title="Gọi lại trao đổi mức lương & lịch phỏng vấn kỹ thuật",
		idempotency_key="seed-attempt-wi2",
		actor=staff_a_email,
	)
	contact_attempt.record_attempt(
		work_item=wi_3,
		channel="phone_call",
		outcome_code="converted",
		note="Ứng viên xác nhận phù hợp mức lương cơ bản + phụ cấp ca đêm, đồng ý tham gia phỏng vấn đợt Q4.",
		close_work_item=1,
		completion_reason="Hoàn tất mục tiêu liên hệ đầu vào của chiến dịch Q4",
		idempotency_key="seed-attempt-wi3-converted",
		actor=staff_a_email,
	)

	frappe.set_user(staff_b_email)
	contact_attempt.record_attempt(
		work_item=wi_4,
		channel="phone_call",
		outcome_code="connected_interested",
		note="Đã trao đổi hai chiều về mức lương và ca làm việc hành chính; ứng viên đang chuẩn bị hồ sơ.",
		idempotency_key="seed-attempt-wi4-b",
		actor=staff_b_email,
	)

	frappe.set_user("Administrator")

	# Seed native CRM Deals (`Giao dịch CRM`) so `/crm/deals` demonstrates native CRM transactions
	deal_1 = ensure_deal(
		"Hợp đồng cung ứng Kỹ thuật viên Cơ khí Q4 (Mẫu)",
		owner=staff_a_email,
		lead_name=lead_2,
	)
	deal_2 = ensure_deal(
		"Thỏa thuận dịch vụ tuyển dụng Vận hành kho Bắc Ninh (Mẫu)",
		owner=admin_email,
		lead_name=lead_1,
	)

	# Compute & publish P3 KPI snapshots for current standard periods so P3 cards/tables/drilldown/exports are populated
	std_periods = affected_standard_periods_for_date(cur_date)
	for p_spec in std_periods:
		execute_kpi_recompute(
			p_spec,
			run_mode="backfill",
			trigger_reason="recruit_ui_seed_publish",
			actor=admin_email,
		)
		execute_kpi_recompute(
			p_spec,
			run_mode="backfill",
			campaign_filter=camp_name,
			trigger_reason="recruit_ui_seed_publish_campaign",
			actor=admin_email,
		)
	if frappe.db.table_exists("CSKH Dirty Scope"):
		frappe.db.sql("update `tabCSKH Dirty Scope` set status = 'processed'")

	frappe.clear_cache()
	frappe.db.commit()

	month_key = f"{cur_date.year:04d}-{cur_date.month:02d}"
	return {
		"site": site,
		"password": SEED_PASSWORD,
		"period_key": month_key,
		"users": {
			"admin": admin_email,
			"leader": leader_email,
			"staff_a": staff_a_email,
			"staff_b": staff_b_email,
			"bod": bod_email,
		},
		"campaign": camp_name,
		"team_a": team_a,
		"team_a_name": leader_name,
		"leads": {
			"lead_1": lead_1,
			"lead_2": lead_2,
			"lead_3": lead_3,
			"lead_4": lead_4,
			"pool_1": lead_pool_1,
			"pool_2": lead_pool_2,
		},
		"work_items": {
			"wi_1": wi_1,
			"wi_2": wi_2,
			"wi_3": wi_3,
			"wi_4": wi_4,
		},
		"deals": [deal_1, deal_2],
	}


if __name__ == "__main__":
	wipe_only = "--wipe-only" in sys.argv[1:]
	pos_args = [a for a in sys.argv[1:] if not a.startswith("--")]
	site_arg = pos_args[0] if pos_args else ALLOWED_SITE
	os.chdir("/home/frappe/frappe-bench/sites")
	frappe.init(site=site_arg, sites_path=".")
	frappe.connect()
	try:
		result = wipe_clean() if wipe_only else run_seed()
		print(json.dumps(result, ensure_ascii=False))
	finally:
		frappe.destroy()
