"""Deterministic E2E reset & synthetic seed helper for the isolated P2 site (`p2.localhost`).

CLI-only (`bench --site p2.localhost execute crm_cskh.tools.p2_e2e_seed.reset_and_seed_p2`).
Refuses to run on `p1.localhost` or any site other than `p2.localhost` / `p2-ci.localhost`.
Never whitelisted over HTTP; never modifies `permissions.py` or `p1_permissions.py`.
"""

import os

import frappe
from frappe.utils import now_datetime
from frappe.utils.password import update_password

ALLOWED_P2_SITES = {"p2.localhost", "p2-ci.localhost"}

USERS = {
	"admin": ("cskh-p2-admin@example.test", "Quản trị CSKH P2", ("CSKH CRM Admin",)),
	"bod": ("cskh-p2-bod@example.test", "Ban Giám đốc P2", ("CSKH BOD Viewer",)),
	"leader_a": ("cskh-p2-leader-a@example.test", "Trưởng nhóm A", ("Sales Manager", "Sales User")),
	"leader_b": ("cskh-p2-leader-b@example.test", "Trưởng nhóm B", ("Sales Manager", "Sales User")),
	"staff_a": ("cskh-p2-staff-a@example.test", "Nhân viên A1", ("Sales User",)),
	"staff_b": ("cskh-p2-staff-b@example.test", "Nhân viên A2", ("Sales User",)),
	"staff_c": ("cskh-p2-staff-c@example.test", "Nhân viên B1", ("Sales User",)),
	"outside": ("cskh-p2-outside@example.test", "Nhân viên Ngoài Cây", ("Sales User",)),
}


def _assert_p2_site():
	site = getattr(frappe.local, "site", "")
	if site not in ALLOWED_P2_SITES:
		raise RuntimeError(f"Refusing to run p2_e2e_seed on non-P2 site: {site!r}")
	return site


def _ensure_role(role_name):
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def _ensure_strict_user(email, full_name, allowed_roles, password, time_zone=""):
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


def _ensure_node(user, is_group, reports_to_node=None):
	doc = frappe.get_doc(
		{
			"doctype": "CRM Sales Hierarchy",
			"user": user,
			"is_group": is_group,
			"reports_to": reports_to_node,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def reset_and_seed_p2():
	"""Reset transactional P1/P2 data on `p2.localhost` and seed synthetic accounts, hierarchy & campaigns."""
	site = _assert_p2_site()
	frappe.set_user("Administrator")
	password = os.environ.get("P2_SEED_PASSWORD", "P2Seed-Only-2026!")

	# 1. Enforce canonical site timezone (`Asia/Ho_Chi_Minh`) and sales hierarchy.
	frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
	frappe.db.set_single_value("System Settings", "country", "Vietnam")
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

	# 2. Wipe transactional tables (leaving catalog DocTypes ready to re-seed cleanly).
	optional_tables = (
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
	for doctype in optional_tables:
		if frappe.db.table_exists(doctype):
			frappe.db.sql(f"delete from `tab{doctype}`")

	for native_dt in ("CRM Call Log", "CRM Task", "FCRM Note"):
		if frappe.db.table_exists(native_dt):
			frappe.db.sql(f"delete from `tab{native_dt}`")

	frappe.db.sql("delete from `tabToDo` where reference_type in ('CRM Lead', 'CRM Task')")
	frappe.db.sql("delete from `tabDocShare` where share_doctype in ('CRM Lead', 'CRM Call Log', 'CRM Task')")
	frappe.db.sql("delete from `tabFile` where attached_to_doctype in ('CSKH Import Batch', 'CRM Lead')")
	frappe.db.sql("delete from `tabCRM Lead`")

	# Reset P2 Outcome Codes & Policies to deterministic v1 baseline if tables exist.
	for cat_dt in ("CSKH Policy", "CSKH Outcome Code"):
		if frappe.db.table_exists(cat_dt):
			frappe.db.sql(f"delete from `tab{cat_dt}`")

	from crm_cskh.p2_models import ensure_p2_seeds

	ensure_p2_seeds()

	# 3. Ensure business roles and strict user accounts (zero System Manager on Admin/BOD).
	_ensure_role("CSKH CRM Admin")
	_ensure_role("CSKH BOD Viewer")
	for _key, (email, full_name, role_tuple) in USERS.items():
		_ensure_strict_user(email, full_name, role_tuple, password)

	# 4. Rebuild clean CRM Sales Hierarchy for Team A and Team B.
	frappe.db.sql("delete from `tabCRM Sales Hierarchy`")
	team_a = _ensure_node(USERS["leader_a"][0], 1, None)
	_ensure_node(USERS["staff_a"][0], 0, team_a)
	_ensure_node(USERS["staff_b"][0], 0, team_a)

	team_b = _ensure_node(USERS["leader_b"][0], 1, None)
	_ensure_node(USERS["staff_c"][0], 0, team_b)

	frappe.clear_cache()
	frappe.db.commit()
	return {
		"site": site,
		"timezone": frappe.db.get_single_value("System Settings", "time_zone"),
		"server_now": str(now_datetime()),
		"team_a": team_a,
		"team_b": team_b,
		"users": {k: v[0] for k, v in USERS.items()},
	}


def set_user_timezone(user, time_zone=""):
	"""Update `User.time_zone` on `p2.localhost` for deterministic cross-timezone browser testing."""
	_assert_p2_site()
	frappe.db.set_value("User", user, "time_zone", time_zone or "", update_modified=False)
	frappe.clear_cache(user=user)
	frappe.db.commit()
	return {"user": user, "time_zone": time_zone or ""}


def seed_native_activities(lead, caller_user, phone="0912340002"):
	"""Seed 2 native `CRM Call Log` rows and 1 XSS-bearing `FCRM Note` on `lead` (`p2.localhost` only)."""
	_assert_p2_site()
	frappe.set_user("Administrator")

	e164_to = ("+84" + phone[1:]) if phone.startswith("0") else phone
	call_logs = []
	for idx, duration in enumerate((0, 42), start=1):
		cl_dict = {
			"doctype": "CRM Call Log",
			"id": f"p2-e2e-call-{lead}-{idx}",
			"type": "Outgoing",
			"from": "+842873001100",
			"to": e164_to,
			"caller": caller_user,
			"status": "No Answer" if duration == 0 else "Completed",
			"duration": duration,
			"start_time": now_datetime(),
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
		}
		if frappe.db.has_column("CRM Call Log", "telephony_medium"):
			cl_dict["telephony_medium"] = "Manual"
		cl = frappe.get_doc(cl_dict)
		cl.insert(ignore_permissions=True)
		call_logs.append(cl.name)

	note_doc = frappe.get_doc(
		{
			"doctype": "FCRM Note",
			"title": "Ghi chú nội bộ CRM =1+1",
			"content": "Khách hỏi bảng giá <script>window.__xssTriggered=1</script> <img src=x onerror=window.__xssTriggered=2>",
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
		}
	)
	note_doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"call_logs": call_logs, "note": note_doc.name}


def set_task_due_exact(work_item, task_id, due_at_str):
	"""Set exact naive site timestamp (`YYYY-MM-DD HH:MM:SS`) on callback task + work item projection (`p2.localhost` only).

	Used exclusively to test `23:59:00` today vs `00:00:00` tomorrow even when the E2E run
	itself executes after `23:00` wall-clock time (where `23:59` might already be in the past
	or <60s away for `schedule_followup` future-time validation).
	"""
	_assert_p2_site()
	frappe.db.set_value("CRM Task", task_id, "due_date", due_at_str, update_modified=False)
	frappe.db.set_value("CSKH Work Item", work_item, "next_action_due", due_at_str, update_modified=False)
	frappe.db.commit()
	return {"work_item": work_item, "task_id": task_id, "due_at": due_at_str}
