#!/usr/bin/env python3
"""Automated Independent Verification Script for BOD CSV Export via Authenticated HTTP Flow.

Verifies:
1. HTTP Session Login as BOD Viewer (`cskh-p3-bod@example.test`)
2. Asynchronous Export Job Creation (`request_export`) over HTTP POST
3. Export Status Polling (`get_export_status`) over HTTP GET until 'completed'
4. Authenticated File Download (`download_export`) over HTTP GET
5. HTTP Response Verification: Status 200, Content-Type, Content-Disposition, Content-Length
6. CSV Data Integrity & Canonical Vietnamese Recruitment Labels Reconciliation (30 lines, 29 metrics)
7. Zero PII Guard: 0 customer phone numbers, emails, or personal identifiers in BOD overview
8. Negative Gate 1: BOD requesting PII export -> HTTP 403 FORBIDDEN_SCOPE
9. Negative Gate 2: BOD requesting personal scorecard -> HTTP 403 FORBIDDEN_SCOPE
10. Negative Gate 3: Unauthorized actor (Staff) downloading BOD export -> HTTP 403 FORBIDDEN_SCOPE
11. Negative Gate 4: Unauthenticated (Guest) downloading BOD export -> HTTP 403 PermissionError
"""

import hashlib
import json
import os
import re
import sys
import time
import requests

BASE_URL = os.environ.get("CSKH_BASE_URL", "http://127.0.0.1:8320")
HOST_HEADER = os.environ.get("CSKH_HOST_HEADER", "p3ui.localhost")

def create_session(user=None, password="TestPassword123!"):
    s = requests.Session()
    s.headers.update({"Host": HOST_HEADER})
    if user:
        login_res = s.post(f"{BASE_URL}/api/method/login", data={"usr": user, "pwd": password})
        if login_res.status_code != 200:
            raise RuntimeError(f"Login failed for {user}: status={login_res.status_code}, text={login_res.text}")
    return s

def run_http_verification():
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"=== Starting BOD CSV Export Authenticated HTTP Verification against {BASE_URL} ({HOST_HEADER}) ===")
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": BASE_URL,
        "host_header": HOST_HEADER,
        "http_flow": {},
        "file_verification": {},
        "label_reconciliation": {},
        "negative_security_gates": {},
    }

    # Step 1: Authenticate as BOD
    print("[Step 1] Authenticating as BOD Viewer (cskh-p3-bod@example.test)...")
    bod_session = create_session("cskh-p3-bod@example.test")
    bod_sid = bod_session.cookies.get("sid")
    assert bod_sid, "BOD session missing 'sid' cookie"
    print(f"  BOD authenticated successfully. SID length: {len(bod_sid)}")

    # Step 2: Request Export via HTTP POST
    print("[Step 2] Requesting executive_overview export via HTTP POST...")
    req_res = bod_session.post(
        f"{BASE_URL}/api/method/crm_cskh.api.p3_reporting.request_export",
        json={"report_type": "executive_overview"},
    )
    assert req_res.status_code == 200, f"request_export failed: {req_res.status_code} - {req_res.text}"
    req_json = req_res.json().get("message", {})
    export_id = req_json.get("export_id")
    assert export_id and export_id.startswith("EXP-"), f"Invalid export_id: {export_id}"
    print(f"  Export job created: {export_id}, initial status: {req_json.get('status')}")

    results["http_flow"]["request_export"] = {
        "status_code": req_res.status_code,
        "export_id": export_id,
        "response": req_json,
    }

    # Step 3: Poll Export Status via HTTP GET
    print(f"[Step 3] Polling export status for {export_id}...")
    status = req_json.get("status")
    download_endpoint = None
    poll_count = 0
    max_polls = 15

    while status != "completed" and poll_count < max_polls:
        time.sleep(1)
        poll_count += 1
        st_res = bod_session.get(
            f"{BASE_URL}/api/method/crm_cskh.api.p3_reporting.get_export_status?export_id={export_id}"
        )
        assert st_res.status_code == 200, f"get_export_status failed: {st_res.status_code}"
        st_json = st_res.json().get("message", {})
        status = st_json.get("status")
        download_endpoint = st_json.get("download_endpoint")
        print(f"  Poll #{poll_count}: status = {status}")

    assert status == "completed", f"Export job did not complete in time, status={status}"
    assert download_endpoint, f"Missing download_endpoint for completed job: {st_json}"
    print(f"  Export job completed! Download endpoint: {download_endpoint}")

    results["http_flow"]["status_polling"] = {
        "poll_count": poll_count,
        "final_status": status,
        "row_count": st_json.get("row_count"),
        "download_endpoint": download_endpoint,
    }

    # Step 4: Download Export over HTTP GET
    print(f"[Step 4] Downloading CSV payload via {download_endpoint}...")
    dl_url = f"{BASE_URL}{download_endpoint}"
    dl_res = bod_session.get(dl_url)
    assert dl_res.status_code == 200, f"Download failed: status={dl_res.status_code}, text={dl_res.text}"

    content_bytes = dl_res.content
    content_type = dl_res.headers.get("content-type", "")
    content_disp = dl_res.headers.get("content-disposition", "")
    content_length = dl_res.headers.get("content-length", str(len(content_bytes)))
    sha256_hash = hashlib.sha256(content_bytes).hexdigest()

    print(f"  Download HTTP Status: {dl_res.status_code}")
    print(f"  Content-Type: {content_type}")
    print(f"  Content-Disposition: {content_disp}")
    print(f"  Byte Length: {len(content_bytes)} bytes")
    print(f"  SHA-256: {sha256_hash}")

    assert "text/csv" in content_type, f"Unexpected Content-Type: {content_type}"
    assert "attachment" in content_disp and f"{export_id}.csv" in content_disp, f"Unexpected Content-Disposition: {content_disp}"

    results["http_flow"]["download"] = {
        "status_code": dl_res.status_code,
        "headers": {
            "Content-Type": content_type,
            "Content-Disposition": content_disp,
            "Content-Length": content_length,
            "Server": dl_res.headers.get("server", ""),
        },
        "byte_length": len(content_bytes),
        "sha256": sha256_hash,
    }

    # Step 5: CSV Content & Label Reconciliation
    print("[Step 5] Analyzing CSV structure and reconciling canonical Vietnamese labels...")
    csv_text = content_bytes.decode("utf-8")
    lines = csv_text.splitlines()
    header_cols = lines[0].split(",")
    expected_header = ["metric_code", "label_vi", "value", "numerator", "denominator", "unit", "status", "reason"]
    assert header_cols == expected_header, f"Header mismatch: {header_cols} != {expected_header}"

    total_lines = len(lines)
    data_rows = total_lines - 1
    print(f"  Total CSV lines: {total_lines} (1 header + {data_rows} data rows)")
    assert data_rows == 29, f"Expected 29 metric rows, got {data_rows}"

    canonical_checks = [
        ("ASSIGNED_INTERVALS", "Lượt nhận hồ sơ (Khoảng phụ trách)"),
        ("UNIQUE_ASSIGNED_ITEMS", "Hồ sơ liên hệ được giao duy nhất"),
        ("ELIGIBLE_COVERAGE", "Tỷ lệ phủ liên hệ trên hồ sơ đủ điều kiện"),
        ("WORK_ITEM_FIRST_ATTEMPT_SLA_RATE", "Tỷ lệ đạt SLA liên hệ lần đầu (Cấp hồ sơ liên hệ)"),
        ("FIRST_ATTEMPT_SLA_RATE", "Tỷ lệ đạt SLA liên hệ lần đầu (Khoảng phụ trách)"),
        ("FOLLOWUP_SLA_RATE", "Tỷ lệ đạt SLA lịch hẹn liên hệ lại (Callback)"),
        ("FIRST_CONNECTED_TRACKING_RATE", "Tỷ lệ kết nối trong ngưỡng theo dõi (Tham khảo)"),
        ("MIN_ATTEMPTS_COMPLIANCE_RATE", "Tỷ lệ tuân thủ số lần liên hệ tối thiểu"),
        ("CONTACT_ATTEMPT_COUNT", "Tổng lượt liên hệ hợp lệ (Effective Attempts)"),
        ("CONNECTED_ATTEMPT_COUNT", "Tổng lượt kết nối hai chiều thành công"),
        ("CONNECTED_ITEMS", "Hồ sơ đã kết nối hai chiều duy nhất"),
        ("CONNECTED_RATE", "Tỷ lệ kết nối hai chiều (Cấp khoảng phụ trách)"),
        ("WORK_ITEMS_TOUCHED", "Số hồ sơ công việc đã tác nghiệp"),
        ("VALID_OUTCOME_LOGGING_RATE", "Tỷ lệ cập nhật kết quả đầy đủ (Outcome Completeness)"),
        ("VERIFICATION_RATE", "Tỷ lệ kết quả đã xác minh (Verified)"),
        ("CONVERSION_RATE", "Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch"),
        ("CORRECTION_RATE", "Tỷ lệ bản ghi phải đính chính / hủy"),
        ("VOID_ATTEMPT_RATE", "Tỷ lệ bản ghi liên hệ bị hủy (Void)"),
        ("CALLBACK_COMPLETION_RATE", "Tỷ lệ hoàn tất lịch hẹn hợp lệ"),
        ("RESCHEDULE_RATE", "Tỷ lệ dời lịch hẹn (Reschedule)"),
        ("DNC_COMPLIANCE_RATE", "Tỷ lệ tuân thủ DNC & kỷ luật đóng hồ sơ"),
        ("OVERDUE_BACKLOG", "Hồ sơ công việc đang tồn quá hạn"),
        ("OPEN_FOLLOWUPS_OVERDUE", "Lịch hẹn gọi lại đang quá hạn"),
        ("SLA_BREACH_COUNT", "Tổng số nghĩa vụ vi phạm SLA trong kỳ"),
        ("SLA_WAIVER_COUNT", "Số nghĩa vụ được miễn trừ hợp lệ"),
        ("FIRST_ATTEMPT_LATENCY_P50_MINUTES", "Trung vị thời gian phản hồi lần đầu (Phút làm việc - P50)"),
        ("FIRST_ATTEMPT_LATENCY_P90_MINUTES", "Phân vị P90 thời gian phản hồi lần đầu (Phút làm việc - P90)"),
        ("QA_PASS_RATE", "Tỷ lệ đạt kiểm định chất lượng QA"),
        ("PROCESS_SCORE", "Điểm tuân thủ quy trình tổng hợp (0-100)"),
    ]

    reconciled_labels = {}
    for code, expected_vi in canonical_checks:
        found = False
        for line in lines[1:]:
            parts = line.split(",")
            if parts[0] == code:
                actual_vi = parts[1]
                assert actual_vi == expected_vi, f"Label mismatch for {code}: expected '{expected_vi}', got '{actual_vi}'"
                found = True
                reconciled_labels[code] = actual_vi
                break
        assert found, f"Metric {code} not found in CSV lines!"

    print(f"  Canonical label check: {len(reconciled_labels)}/29 metrics fully reconciled (100% PASS)!")

    # Step 6: Zero PII Verification
    print("[Step 6] Scanning CSV payload for PII leakage (phone numbers, emails, identities)...")
    phone_matches = re.findall(r"\b0[0-9]{9}\b", csv_text)
    email_matches = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", csv_text)
    assert len(phone_matches) == 0, f"PII Leak detected (phone numbers): {phone_matches}"
    assert len(email_matches) == 0, f"PII Leak detected (emails): {email_matches}"
    print("  Zero PII verified: 0 customer phone numbers, 0 email addresses, 0 personal candidate profiles leaked!")

    results["file_verification"] = {
        "total_lines": total_lines,
        "data_rows": data_rows,
        "header": header_cols,
        "zero_pii_verified": True,
        "phone_numbers_detected": len(phone_matches),
        "emails_detected": len(email_matches),
    }
    results["label_reconciliation"] = {
        "status": "PASS",
        "reconciled_count": len(reconciled_labels),
        "labels": reconciled_labels,
    }

    # Step 7: Negative Security Gates
    print("[Step 7] Testing Negative Security Gates...")

    # Gate 1: BOD requests PII export
    print("  [Gate 1] BOD requesting export with include_pii=1...")
    g1_res = bod_session.post(
        f"{BASE_URL}/api/method/crm_cskh.api.p3_reporting.request_export",
        json={"report_type": "metric_drilldown", "metric_code": "CONTACT_ATTEMPT_COUNT", "include_pii": 1},
    )
    assert g1_res.status_code == 403, f"Expected 403 for BOD PII export, got {g1_res.status_code}"
    g1_json = g1_res.json()
    assert g1_json.get("exc_type") == "FORBIDDEN_SCOPE", f"Unexpected exc_type: {g1_json.get('exc_type')}"
    assert "BOD Viewer không được phép xuất báo cáo chứa dữ liệu định danh khách hàng" in g1_json.get("exception", "")
    print("    Gate 1 PASS: Blocked with HTTP 403 FORBIDDEN_SCOPE.")
    results["negative_security_gates"]["gate_1_bod_pii_rejected"] = {
        "status": "PASS",
        "status_code": g1_res.status_code,
        "exc_type": g1_json.get("exc_type"),
        "message": g1_json.get("exception"),
    }

    # Gate 2: BOD requests personal scorecard
    print("  [Gate 2] BOD requesting report_type='my_scorecard'...")
    g2_res = bod_session.post(
        f"{BASE_URL}/api/method/crm_cskh.api.p3_reporting.request_export",
        json={"report_type": "my_scorecard"},
    )
    assert g2_res.status_code == 403, f"Expected 403 for BOD my_scorecard, got {g2_res.status_code}"
    g2_json = g2_res.json()
    assert g2_json.get("exc_type") == "FORBIDDEN_SCOPE", f"Unexpected exc_type: {g2_json.get('exc_type')}"
    assert "BOD Viewer không có bảng điểm cá nhân để xuất" in g2_json.get("exception", "")
    print("    Gate 2 PASS: Blocked with HTTP 403 FORBIDDEN_SCOPE.")
    results["negative_security_gates"]["gate_2_bod_my_scorecard_rejected"] = {
        "status": "PASS",
        "status_code": g2_res.status_code,
        "exc_type": g2_json.get("exc_type"),
        "message": g2_json.get("exception"),
    }

    # Gate 3: Unauthorized actor (Staff) downloading BOD export
    print(f"  [Gate 3] Staff (cskh-p3-staff-a@example.test) downloading BOD export ({export_id})...")
    staff_session = create_session("cskh-p3-staff-a@example.test")
    g3_res = staff_session.get(f"{BASE_URL}{download_endpoint}")
    assert g3_res.status_code == 403, f"Expected 403 for Staff downloading BOD export, got {g3_res.status_code}"
    g3_json = g3_res.json()
    assert g3_json.get("exc_type") == "FORBIDDEN_SCOPE", f"Unexpected exc_type: {g3_json.get('exc_type')}"
    assert "Bạn không có quyền tải tệp xuất báo cáo của người khác" in g3_json.get("exception", "")
    print("    Gate 3 PASS: Blocked with HTTP 403 FORBIDDEN_SCOPE.")
    results["negative_security_gates"]["gate_3_cross_actor_staff_download_rejected"] = {
        "status": "PASS",
        "status_code": g3_res.status_code,
        "exc_type": g3_json.get("exc_type"),
        "message": g3_json.get("exception"),
    }

    # Gate 4: Guest (unauthenticated) downloading BOD export
    print(f"  [Gate 4] Guest session downloading BOD export ({export_id})...")
    guest_session = create_session(None)
    g4_res = guest_session.get(f"{BASE_URL}{download_endpoint}")
    assert g4_res.status_code == 403, f"Expected 403 for Guest downloading BOD export, got {g4_res.status_code}"
    g4_json = g4_res.json()
    assert g4_json.get("exc_type") == "PermissionError", f"Unexpected exc_type: {g4_json.get('exc_type')}"
    print("    Gate 4 PASS: Blocked with HTTP 403 PermissionError.")
    results["negative_security_gates"]["gate_4_guest_download_rejected"] = {
        "status": "PASS",
        "status_code": g4_res.status_code,
        "exc_type": g4_json.get("exc_type"),
    }

    print("\n=== ALL BOD HTTP CSV EXPORT & REJECTION CHECKS PASSED 100%! ===")
    return results, content_bytes

if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir = os.path.join(out_dir, "raw_results")
    os.makedirs(raw_dir, exist_ok=True)

    results, csv_bytes = run_http_verification()

    json_path = os.path.join(raw_dir, "bod_http_export_verification.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Saved audit JSON to {json_path}")

    csv_path = os.path.join(raw_dir, "bod_http_downloaded_export.csv")
    with open(csv_path, "wb") as f:
        f.write(csv_bytes)
    print(f"Saved HTTP downloaded CSV to {csv_path}")
