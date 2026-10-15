#!/usr/bin/env python3
# deploy/dev/seed/seed_demo.py
#
# Seed DỮ LIỆU GIẢ, LẶP LẠI ĐƯỢC cho môi trường DEV CRM-CSKH (WP-01 / P0-A, §7 của P0.md).
#
# - Idempotent: chạy nhiều lần không tạo bản ghi trùng (khóa theo email/mobile_no/tên).
# - Deterministic: cùng đầu vào -> cùng bản ghi.
# - CHỈ dùng dữ liệu giả: tên giả, số điện thoại KHÔNG thể gọi được, không gửi mail/SMS ra ngoài.
#
# Cách chạy (qua wrapper, trong container bench):
#   docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh

import os
import sys

import frappe

DEMO = "DEMO"
FAKE_PHONE = "0900000"          # 090 + 0000xx -> 9 số, rõ ràng là placeholder, không dialable
SEED_PASSWORD = "DevSeed-Only-2026!"   # mật khẩu DEV giả, KHÔNG phải bí mật thật

# (email, tên hiển thị, danh sách role)
USERS = [
    ("bod.viewer@dev.local",   "BOD Viewer",    ["CSKH BOD Viewer"]),
    ("crm.admin@dev.local",    "CRM Admin",     ["CSKH CRM Admin"]),
    ("team1.leader@dev.local", "Team1 Leader",  ["Sales Manager", "Sales User"]),
    ("staff.a@dev.local",      "Staff A",       ["Sales User"]),
    ("staff.b@dev.local",      "Staff B",       ["Sales User"]),
    ("team2.leader@dev.local", "Team2 Leader",  ["Sales Manager", "Sales User"]),
    ("staff.c@dev.local",      "Staff C",       ["Sales User"]),
]

# Cây quản lý: (full_name, email user, is_group, reports_to_full_name)
HIERARCHY = [
    ("Team1 Leader",  "team1.leader@dev.local", 1, None),
    ("Staff A",       "staff.a@dev.local",      0, "Team1 Leader"),
    ("Staff B",       "staff.b@dev.local",      0, "Team1 Leader"),
    ("Team2 Leader",  "team2.leader@dev.local", 1, None),
    ("Staff C",       "staff.c@dev.local",      0, "Team2 Leader"),
]

# Lead giả: (mã, owner_email|None, status, source, ghi chú)
LEADS = [
    ("A1", "staff.a@dev.local", "New",        "Facebook",      "Mới giao, chưa liên hệ"),
    ("A2", "staff.a@dev.local", "Contacted",  "Website",       "Đã thử gọi, không nghe máy"),
    ("A3", "staff.a@dev.local", "Nurture",    "Cold Calling",  "Hẹn gọi lại"),
    ("A4", "staff.a@dev.local", "Qualified",  "Reference",     "Có nhu cầu, cần tư vấn"),
    ("B1", "staff.b@dev.local", "New",        "Facebook",      "Mới giao"),
    ("B2", "staff.b@dev.local", "Contacted",  "Advertisement", "Busy"),
    ("B3", "staff.b@dev.local", "Nurture",    "Campaign",      "Hẹn lại tuần sau"),
    ("B4", "staff.b@dev.local", "Unqualified","Walk In",       "Không phù hợp"),
    ("C1", "staff.c@dev.local", "New",        "Website",       "Mới giao"),
    ("C2", "staff.c@dev.local", "Contacted",  "Email",         "Đã gọi, no answer"),
    ("C3", "staff.c@dev.local", "Converted",  "Reference",     "Đạt mục tiêu"),
    ("C4", "staff.c@dev.local", "Nurture",    "Mass Mailing",  "Theo dõi tiếp"),
    ("U1", None,                "New",        "Web Form",      "Chưa phân phối"),
    ("U2", None,                "New",        "Facebook",      "Chưa phân phối"),
    ("U3", None,                "New",        "Exhibition",    "Chưa phân phối"),
]


def _ensure_role(role_name):
    if not frappe.db.exists("Role", role_name):
        frappe.get_doc({
            "doctype": "Role",
            "role_name": role_name,
            "desk_access": 1,
        }).insert(ignore_permissions=True)
        print(f"  + role {role_name}")


def _ensure_user(email, full_name, roles):
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": full_name,
            "enabled": 1,
            "send_welcome_email": 0,
            "user_type": "System User",
        })
        user.insert(ignore_permissions=True)
        user.new_password = SEED_PASSWORD
        user.save(ignore_permissions=True)
        print(f"  + user {email}")
    user = frappe.get_doc("User", email)
    existing = {r.role for r in user.roles}
    to_add = [r for r in roles if r not in existing]
    if to_add:
        user.add_roles(*to_add)
        print(f"  ~ roles {email}: {to_add}")


def _ensure_dev_settings():
    if frappe.db.get_single_value("System Settings", "time_zone") != "Asia/Ho_Chi_Minh":
        # Site DEV thiếu language bắt buộc nên save() lỗi; chỉ cập nhật setting này.
        frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
        print("  ~ System Settings.time_zone=Asia/Ho_Chi_Minh")
    else:
        print("  = System Settings.time_zone=Asia/Ho_Chi_Minh")

    if not frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy"):
        frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
        print("  ~ FCRM Settings.enable_sales_hierarchy=1")
    else:
        print("  = FCRM Settings.enable_sales_hierarchy=1")


def _ensure_hierarchy(full_name, user_email, is_group, reports_to):
    if frappe.db.exists("CRM Sales Hierarchy", {"full_name": full_name}):
        return
    doc = frappe.get_doc({
        "doctype": "CRM Sales Hierarchy",
        "full_name": full_name,
        "is_group": is_group,
        "user": user_email,
    })
    if reports_to:
        parent = frappe.db.get_value("CRM Sales Hierarchy", {"full_name": reports_to}, "name")
        if parent:
            doc.reports_to = parent
    doc.insert(ignore_permissions=True)
    print(f"  + hierarchy {full_name}")


def _phone(index):
    # 090 + 0000 + 2 số -> ví dụ 090000001..0900000NN
    return f"{FAKE_PHONE}{index:02d}0"


def _ensure_lead(code, owner_email, status, source, note, index, phone_only=False):
    phone = _phone(index)
    existing = frappe.db.get_value("CRM Lead", {"mobile_no": phone}, "name")
    if existing:
        return existing

    lost_reason = "Not interested" if status == "Unqualified" else None
    if lost_reason and not frappe.db.exists("CRM Lost Reason", lost_reason):
        frappe.get_doc({"doctype": "CRM Lost Reason", "lost_reason": lost_reason}).insert(ignore_permissions=True)

    if phone_only:
        first_name = f"Khách chưa rõ tên - {DEMO}{code}"
    else:
        first_name = f"{DEMO} {code} (giả lập)"

    lead = frappe.get_doc({
        "doctype": "CRM Lead",
        "naming_series": "CRM-LEAD-",
        "first_name": first_name,
        "lead_name": first_name,
        "mobile_no": phone,
        "status": status,
        "lost_reason": lost_reason,
        "source": source if frappe.db.exists("CRM Lead Source", source) else None,
        "lead_owner": owner_email,
        "lead_temperature": "Warm" if status in ("Contacted", "Nurture", "Qualified") else None,
    })
    lead.append("status_change_log", {
        "from": "New",
        "to": status,
        "from_type": "Open",
        "to_type": "Open",
        "log_owner": owner_email or "Administrator",
    })
    lead.insert(ignore_permissions=True)
    print(f"  + lead {lead.name} ({code}, owner={owner_email or '-'}, status={status})")

    # Ghi chú mô tả hoàn cảnh (audit cho seed)
    frappe.get_doc({
        "doctype": "FCRM Note",
        "title": f"{DEMO} note {code}: {note}",
        "content": note,
        "reference_doctype": "CRM Lead",
        "reference_docname": lead.name,
    }).insert(ignore_permissions=True)

    return lead.name


def _ensure_task(lead_name, title, owner_email, status="Todo", priority="Medium"):
    if frappe.db.exists("CRM Task", {"title": title, "reference_docname": lead_name}):
        return
    frappe.get_doc({
        "doctype": "CRM Task",
        "title": title,
        "priority": priority,
        "status": status,
        "reference_doctype": "CRM Lead",
        "reference_docname": lead_name,
        "assigned_to": owner_email,
        "description": f"[{DEMO}] Công việc giả lập DEV",
    }).insert(ignore_permissions=True)
    print(f"  + task '{title}' -> {lead_name}")


def _ensure_call_log(lead_name, from_no, to_no, status, index):
    call_id = f"{DEMO}-CALL-{index:03d}"
    if frappe.db.exists("CRM Call Log", call_id):
        return
    frappe.get_doc({
        "doctype": "CRM Call Log",
        "id": call_id,
        "from": from_no,
        "to": to_no,
        "type": "Outgoing",
        "status": status,
        "telephony_medium": "Manual",
        "duration": 0 if status in ("No Answer", "Busy", "Failed") else 42,
        "reference_doctype": "CRM Lead",
        "reference_docname": lead_name,
    }).insert(ignore_permissions=True)
    print(f"  + call log {call_id} ({status}) -> {lead_name}")


def _ensure_attachment(lead_name):
    file_name = f"{DEMO}-{lead_name}.txt"
    if frappe.db.exists("File", {"file_name": file_name, "attached_to_name": lead_name}):
        return
    frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "is_private": 1,
        "attached_to_doctype": "CRM Lead",
        "attached_to_name": lead_name,
        "content": f"[{DEMO}] Tệp đính kèm giả lập cho {lead_name}. Không chứa dữ liệu thật.",
    }).insert(ignore_permissions=True)
    print(f"  + attachment {file_name}")


def run():
    print("== DEV Settings ==")
    _ensure_dev_settings()

    print("== Roles ==")
    _ensure_role("CSKH BOD Viewer")
    _ensure_role("CSKH CRM Admin")

    print("== Users ==")
    for email, full_name, roles in USERS:
        _ensure_user(email, full_name, roles)

    print("== Sales Hierarchy ==")
    for full_name, user_email, is_group, reports_to in HIERARCHY:
        _ensure_hierarchy(full_name, user_email, is_group, reports_to)

    print("== Leads ==")
    lead_names = {}
    for index, (code, owner, status, source, note) in enumerate(LEADS, start=1):
        phone_only = code in ("U1", "A4")   # A4: lead chỉ có SĐT (kịch bản P0 D1)
        lead_names[code] = _ensure_lead(code, owner, status, source, note, index, phone_only=phone_only)

    print("== Call Log (kỹ thuật, KHÔNG phải outcome CSKH) ==")
    _ensure_call_log(lead_names["A2"], "0900000010", _phone(2), "No Answer", 1)
    _ensure_call_log(lead_names["B2"], "0900000020", _phone(6), "Busy", 2)
    _ensure_call_log(lead_names["A4"], "0900000030", _phone(4), "Completed", 3)

    print("== Task ==")
    _ensure_task(lead_names["A3"], f"[{DEMO}] Gọi lại A3", "staff.a@dev.local", status="Todo", priority="High")
    _ensure_task(lead_names["C2"], f"[{DEMO}] Gọi lại C2", "staff.c@dev.local", status="In Progress")

    print("== Attachment ==")
    _ensure_attachment(lead_names["A1"])

    # Case chuyển giao (state-level). Lịch sử phân phối hạng nhất là việc của ADR-007/P1.
    print("== Reassignment / team change (state-level + audit note) ==")
    a2 = lead_names["A2"]
    if frappe.db.get_value("CRM Lead", a2, "lead_owner") != "staff.b@dev.local":
        frappe.db.set_value("CRM Lead", a2, "lead_owner", "staff.b@dev.local")
        frappe.get_doc({
            "doctype": "FCRM Note",
            "title": f"{DEMO} reassignment A2: staff.a -> staff.b",
            "content": "Chuyển giao giả lập A -> B để kiểm thử quyền (P0 §7).",
            "reference_doctype": "CRM Lead",
            "reference_docname": a2,
        }).insert(ignore_permissions=True)
        print(f"  ~ reassign {a2} -> staff.b")

    print("== XONG ==")


def _main():
    site = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SITE_NAME", "dev.localhost")
    bench_dir = os.environ.get("BENCH_DIR", "/workspace/frappe-bench")
    frappe.init(site=site, sites_path=os.path.join(bench_dir, "sites"))
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        run()
        frappe.db.commit()
        print(f"\nSeed hoàn tất cho site '{site}'.")
    except Exception:
        frappe.db.rollback()
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    _main()
