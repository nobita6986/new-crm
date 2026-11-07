"""Deterministic E2E reset & synthetic seed helper for the isolated P1 site (`p1.localhost`).

CLI-only (`bench --site p1.localhost execute crm_cskh.tools.e2e_seed.reset_and_seed`).
Never whitelisted over HTTP; never touches `permissions.py` or `p1_permissions.py`.
"""

import os

import frappe
from frappe.utils.password import update_password

USERS = {
	"admin": ("cskh-p1-admin@example.test", "Quản trị CSKH P1", ("CSKH CRM Admin",)),
	"bod": ("cskh-p1-bod@example.test", "Ban Giám đốc P1", ("CSKH BOD Viewer",)),
	"leader_a": ("cskh-p1-leader-a@example.test", "Trưởng nhóm A", ("Sales Manager", "Sales User")),
	"leader_b": ("cskh-p1-leader-b@example.test", "Trưởng nhóm B", ("Sales Manager", "Sales User")),
	"staff_a": ("cskh-p1-staff-a@example.test", "Nhân viên A1", ("Sales User",)),
	"staff_b": ("cskh-p1-staff-b@example.test", "Nhân viên A2", ("Sales User",)),
	"staff_c": ("cskh-p1-staff-c@example.test", "Nhân viên B1", ("Sales User",)),
	"outside": ("cskh-p1-outside@example.test", "Nhân viên Ngoài Cây", ("Sales User",)),
}


def _ensure_role(role_name):
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def _ensure_strict_user(email, full_name, allowed_roles, password):
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
	# Replace roles strictly so `CSKH CRM Admin` and `CSKH BOD Viewer` never hold `System Manager`.
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


def reset_and_seed():
	"""Wipe leftover test data on `p1.localhost` and seed clean accounts, hierarchy, and edge states."""
	site = getattr(frappe.local, "site", "")
	if site != "p1.localhost":
		raise RuntimeError(f"Refusing to reset non-P1 site: {site!r}")

	frappe.set_user("Administrator")
	password = os.environ.get("P1_SEED_PASSWORD", "P1Seed-Only-2026!")

	# 1. Clean P1 transactional tables and synthetic leads so counts reconcile deterministically from zero.
	if frappe.db.table_exists("CSKH Import Batch Lead"):
		frappe.db.sql("delete from `tabCSKH Import Batch Lead`")
	for table in (
		"tabCSKH Active Claim",
		"tabCSKH Assignment History",
		"tabCSKH Work Item",
		"tabCSKH Distribution Run",
		"tabCSKH Assignment Rule",
		"tabCSKH Campaign",
		"tabCSKH Import Batch",
		"tabCSKH Identity Index",
	):
		frappe.db.sql(f"delete from `{table}`")

	frappe.db.sql("delete from `tabToDo` where reference_type = 'CRM Lead'")
	frappe.db.sql("delete from `tabDocShare` where share_doctype = 'CRM Lead'")
	frappe.db.sql("delete from `tabFile` where attached_to_doctype in ('CSKH Import Batch', 'CRM Lead')")
	frappe.db.sql("delete from `tabCRM Lead`")

	# 2. Ensure business roles and strict user accounts (no System Manager on Admin or BOD).
	_ensure_role("CSKH CRM Admin")
	_ensure_role("CSKH BOD Viewer")
	for _key, (email, full_name, role_tuple) in USERS.items():
		_ensure_strict_user(email, full_name, role_tuple, password)

	# 3. Rebuild clean CRM Sales Hierarchy for Team A and Team B.
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
	frappe.db.sql("delete from `tabCRM Sales Hierarchy`")

	team_a = _ensure_node(USERS["leader_a"][0], 1, None)
	_ensure_node(USERS["staff_a"][0], 0, team_a)
	_ensure_node(USERS["staff_b"][0], 0, team_a)

	team_b = _ensure_node(USERS["leader_b"][0], 1, None)
	_ensure_node(USERS["staff_c"][0], 0, team_b)

	# 4. Seed 1 lead without canonical identity (`invalid` -> "Lỗi dữ liệu") and
	#    1 lead owned outside CSKH (`conflict` -> "Xung đột") + 1 failed import batch
	#    and 1 failed distribution run so error/retry UI states are inspectable too.
	invalid_lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": "Hồ sơ thiếu định danh SĐT (mẫu lỗi)",
			"mobile_no": "",
			"status": "New",
		}
	).insert(ignore_permissions=True)

	conflict_lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": "Hồ sơ ngoài luồng phân công <img src=x onerror=alert(1)>",
			"mobile_no": "",
			"status": "New",
			"lead_owner": USERS["outside"][0],
		}
	).insert(ignore_permissions=True)

	bad_file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "synthetic-failed-batch.csv",
			"is_private": 1,
			"attached_to_doctype": "CRM Lead",
			"attached_to_name": invalid_lead.name,
			"content": b"so_dt,ho_ten\n0918880888,Lien He Thu Lai Job Loi\n",
		}
	).insert(ignore_permissions=True)

	failed_batch = frappe.get_doc(
		{
			"doctype": "CSKH Import Batch",
			"source_file": bad_file.name,
			"checksum": "e2e-failed-batch-seed",
			"idempotency_key": "e2e-failed-batch-seed",
			"status": "FAILED",
			"mapping_json": '{"phone": "so_dt", "name": "ho_ten"}',
			"total_rows": 1,
			"valid_rows": 1,
			"new_rows": 1,
			"duplicate_rows": 0,
			"error_rows": 0,
		}
	).insert(ignore_permissions=True)

	frappe.db.commit()
	return {
		"site": site,
		"team_a": team_a,
		"team_b": team_b,
		"invalid_lead": invalid_lead.name,
		"conflict_lead": conflict_lead.name,
		"failed_batch": failed_batch.name,
		"users": {k: v[0] for k, v in USERS.items()},
	}


def expire_preview(preview_id):
	"""Backdate `expires_at` on one planned preview doc to test `PREVIEW_EXPIRED` deterministically."""
	if getattr(frappe.local, "site", "") != "p1.localhost":
		raise RuntimeError("p1.localhost only")
	import json
	from datetime import timedelta

	from frappe.utils import now_datetime

	doc = frappe.get_doc("CSKH Distribution Run", preview_id)
	past = now_datetime() - timedelta(minutes=30)
	payload = json.loads(doc.preview_json or "{}")
	payload["expires_at"] = past.isoformat()
	doc.db_set("expires_at", past, update_modified=False)
	doc.db_set("preview_json", json.dumps(payload), update_modified=False)
	frappe.db.commit()
	return {"expired": preview_id}


def seed_failed_states_for_ui(campaign):
	"""Create 1 FAILED import batch and 1 failed distribution run so UI error/retry states stay inspectable."""
	if getattr(frappe.local, "site", "") != "p1.localhost":
		raise RuntimeError("p1.localhost only")
	lead_name = frappe.db.get_value("CRM Lead", {}, "name")
	f_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "ui-failed-batch-demo.csv",
			"is_private": 1,
			"attached_to_doctype": "CRM Lead",
			"attached_to_name": lead_name,
			"content": b"so_dt,ho_ten\n0918880777,Lien He Demo Retry\n",
		}
	).insert(ignore_permissions=True)

	b_doc = frappe.get_doc(
		{
			"doctype": "CSKH Import Batch",
			"source_file": f_doc.name,
			"checksum": "ui-failed-batch-demo",
			"idempotency_key": "ui-failed-batch-demo",
			"status": "FAILED",
			"mapping_json": '{"phone": "so_dt", "name": "ho_ten"}',
			"total_rows": 1,
			"valid_rows": 1,
			"new_rows": 1,
		}
	).insert(ignore_permissions=True)

	r_doc = frappe.get_doc(
		{
			"doctype": "CSKH Distribution Run",
			"campaign": campaign,
			"rule_version": 1,
			"selection_fingerprint": "sha256:demo-failed-run",
			"idempotency_key": "ui-failed-run-demo",
			"status": "failed",
			"preview_json": "{}",
			"actual_json": '{"assigned": {}, "created": 0, "errors": 1, "skipped": []}',
			"error": "The operation failed temporarily. Try again.",
		}
	).insert(ignore_permissions=True)

	frappe.db.commit()
	return {"failed_batch": b_doc.name, "failed_run": r_doc.name}
