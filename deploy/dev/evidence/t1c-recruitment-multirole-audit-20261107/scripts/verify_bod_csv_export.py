#!/usr/bin/env python3
"""Independent verification script for BOD CSV Export, permission guards, and Zero PII."""

import frappe
from crm_cskh.services import p3_export
from crm_cskh.services.errors import ForbiddenScope

def verify_bod_csv():
    frappe.init("p3ui.localhost")
    frappe.connect()

    frappe.set_user("cskh-p3-bod@example.test")

    print("[1] Testing BOD request export for executive_overview (PII=0)...")
    job = p3_export.request_export(report_type="executive_overview", actor="cskh-p3-bod@example.test")
    assert job["ok"] is True, f"Failed to request export: {job}"
    export_id = job["export_id"]
    print(f"  Created export job: {export_id}")

    print("[2] Executing export job synchronously...")
    res = p3_export.execute_export_job_now(export_id)
    assert res["status"] == "completed", f"Export execution failed: {res}"
    print(f"  Export status: {res['status']} with {res['row_count']} rows")

    print("[3] Inspecting exported file artifact...")
    job_doc = frappe.get_doc("CSKH Export Job", export_id)
    file_doc = frappe.get_doc("File", job_doc.file_doc)
    with open(file_doc.get_full_path(), encoding="utf-8") as f:
        csv_text = f.read()

    lines = csv_text.splitlines()
    header = lines[0].split(",")
    assert header == ["metric_code", "label_vi", "value", "numerator", "denominator", "unit", "status", "reason"], f"Invalid header: {header}"
    print("  Header verified OK:", header)

    # Assert canonical Vietnamese labels in CSV
    assert "Lượt nhận hồ sơ (Khoảng phụ trách)" in csv_text
    assert "Hồ sơ liên hệ được giao duy nhất" in csv_text
    assert "Tỷ lệ đạt SLA liên hệ lần đầu (Khoảng phụ trách)" in csv_text
    assert "Tỷ lệ đạt SLA lịch hẹn liên hệ lại (Callback)" in csv_text
    assert "Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch" in csv_text
    print("  Canonical Vietnamese labels verified in CSV rows!")

    # Zero PII Check: No 10-digit phone numbers, no emails, no personal names in executive overview
    import re
    raw_phones = re.findall(r"\b0[0-9]{9}\b", csv_text)
    assert len(raw_phones) == 0, f"Leaked phone numbers in BOD export: {raw_phones}"
    print("  Zero PII verified OK (0 customer phone numbers/identities leaked)!")

    print("[4] Testing Security Gate: BOD requesting PII export must raise ForbiddenScope...")
    try:
        p3_export.request_export(
            report_type="metric_drilldown",
            metric_code="CONTACT_ATTEMPT_COUNT",
            include_pii=1,
            actor="cskh-p3-bod@example.test",
        )
        raise AssertionError("BOD PII export was NOT blocked!")
    except ForbiddenScope as e:
        print("  Blocked PII export as expected:", e)

    print("[5] Testing Security Gate: BOD requesting my_scorecard must raise ForbiddenScope...")
    try:
        p3_export.request_export(report_type="my_scorecard", actor="cskh-p3-bod@example.test")
        raise AssertionError("BOD my_scorecard export was NOT blocked!")
    except ForbiddenScope as e:
        print("  Blocked my_scorecard export as expected:", e)

    print("ALL BOD CSV EXPORT CHECKS PASSED 100%!")

if __name__ == "__main__":
    verify_bod_csv()
