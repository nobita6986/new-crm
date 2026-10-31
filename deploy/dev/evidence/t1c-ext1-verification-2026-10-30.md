# EVIDENCE — T1C kiểm định độc lập T1B-ext+1 (Dynamic Link fail-closed + get_activities override)

- **LẦN 31 (2026-10-30) — T1C tự xác minh GitHub API + tự tải/hash/parse artifact, KHÔNG tin số ghi sẵn.**

## 1. Xác định run mới nhất (trước khi sửa, bắt buộc)

T1C liệt kê toàn bộ run `pull_request` qua GitHub API và xác định run mới nhất **sau khi `#41037431038` kết thúc**:

| Run | Status | head_sha | Kết luận |
|---|---|---|---|
| `#41037431038` (run_number 59) | `success` (completed 2026-10-30T10:04:19Z) | `b1087bc911a35b2310ddd3e80dd17f6438170dec` | **MỚI NHẤT** |
| `#41036251872` (run_number 58) | `success` (completed 2026-10-30T09:54:05Z) | `afa74b382282e9dcf5908b51aad83d4c330e21af` | T1B báo cáo |
| `#41035584080` (run_number 57) | `failure` | `8e6aa9f3383bc9626f47e869db184de8572d0a00` | Dynamic Link (bản đầu) |

## 2. Provenance — phân biệt head SHA vs merge SHA

- PR #2 `state=open`, `merged=false`, `head_sha=b1087bc`, `base=main`, `base_sha=b382fe4` (main chưa đổi).
- `workflow-metadata.json` run #59: `commit_sha = 682a9d93cc0267bfa2ab23054bb1f7bc6217f677`.
- `git`/API xác minh parents của merge `682a9d93` = `[b382fe4…, b1087bc…]` ⇒ artifact đúng là bằng chứng của **merge ref PR #2 (head b1087bc vào main b382fe4)**.
- Diff `afa74b3…b1087bc` = 1 commit, chỉ thêm `deploy/dev/evidence/t1b-ext1-dynamic-link-fail-closed.md` (+150 dòng) ⇒ `b1087bc` là **báo cáo (docs-only)**, không đổi code.

## 3. Artifact — tự tải + tự hash

| Run | Artifact id | Size (B) | Digest (GitHub API) | T1C tự hash (SHA-256 zip) |
|---|---|---|---|---|
| #59 | `12968528845` | 139 781 | `sha256:c6b298a8…e9425` | `C6B298A8040B0C6A306C6B75E91393FE1715AFA91A354FC36282EA26732E9425` ✅ KHỚP |
| #58 | `12967408725` | 139 779 | `sha256:e415edc6…ced012` | `E415EDC6DFBE78C9720B36AC1D004BFF3BB3822FA7A71357A44241C11FCED012` ✅ KHỚP |

## 4. Per-method gate — T1C tự parse 5 JUnit XML (KHÔNG tin `gate_passed`)

| Module | expected | PASS (T1C tự parse) |
|---|---:|---:|
| `test_permissions` | 18 | 18 |
| `test_security_remediation` | 12 | 12 |
| `test_security_remediation_v2` | 32 | 32 |
| `test_bt1c5_matrix_gaps` | 27 | 27 |
| `test_docshare_lifecycle` | 10 | 10 |
| **Tổng** | **99** | **99 PASS / 0 FAIL / 0 ERROR / 0 SKIP** |

- `g007-per-method-gate.json`: `schema_version=2`, `gate=g007-per-method-junit`, `gate_passed=true`, `expected_total=99`, `observed_total=99`, `passing_total=99`, `counts={"PASS":99}`; `missing_modules/empty_xml_files/missing_methods/failed_methods/errored_methods/skipped_methods/duplicate_methods/unexpected_cases/parse_errors` đều rỗng `[]`.
- `xml_module_binding` đúng 5/5 module.
- Auxiliary `test_error_response_normalization`: 8/8 PASS (ngoài inventory 99).
- `module-status.txt`: `probe_static exit=0` + 5 module `exit=0` + `per-method gate exit=0` + `remaining_gaps_http exit=0`.

## 5. HTTP transport probe — 18/18 PASS

`probe_result=PASS`, `failed_cases=[]`. `coverage_scope` **không đổi**: *"direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered"*.

18 case gồm các case cũ (SEC-13/14/16/26) + mới: `staffc-login`, `sec26-activities-in-scope` (200), `sec26-activities-out-of-scope` (403), `sec26-activities-download-in-scope`.

⭐ **Observation `sec26-activities-in-scope` (endpoint `crm.api.activities.get_activities`, caller in-scope `staff.a@dev.local`, file private `DEMO-CRM-LEAD-00001.txt`):** tại 4 leak site (`message[4].attachments[].file_url`, `message[0][attachment_log].data.file_url`, `docinfo.attachments[].file_url`, `docinfo.attachment_logs[].content`) — `payload_entry_present=true`, `observation=file_url_absent`, `file_url_present=false`, `value_sha256=null`, `value_len=null`. Khác hẳn run #40–#41 (trước override: `value_sha256=13af0423…` = URL private thật). ⇒ đường `get_activities` nay **đã redaction private file_url ở tầng ứng dụng**.

## 6. File oracle — 9/9 PASS (`http/file-oracle/file-oracle.json`)

`probe_result=PASS`, `failed_cases=[]`. Out-of-scope `client.get`/`get_list`/`rest` file existing/unknown ⇒ **HTTP 404 `DoesNotExistError`, không echo id, không lộ URL**; `getdoc` out-of-scope ⇒ **403 `PermissionError`, identifier_echo=false, exception_sanitized=true**; in-scope positive ⇒ 200 + `private_file_url_present=true`.

## 7. Remaining gaps — 62/62 PASS (`http/remaining-gaps/remaining-gaps.json`)

`case_count=62`, `probe_result=PASS`, `failed_cases=[]`, `merge_sha=682a9d93`, `head_sha=b1087bc`. Phủ các nhóm:

- **Dynamic Link `dl01` (4 case):** fixture → `dl01-linked-calls-in-scope-actor` (`leaked_marker_count=0`, `out_of_scope_lead_echo=false`) → `dl01-linked-calls-positive-control` (`positive_call_visible=true`, `returned_call_count=2`) → `dl01-activities-in-scope-actor` (`leaked_marker_count=0`). ⇒ âm (không lộ contact/phone/note/task/recording/link_name ngoài scope) + dương (Call Log hợp lệ vẫn hiển thị) + positive control đều PASS.
- **SEC-16 app-wide (12 permlevel surfaces):** `sec16-{crm-get-data, frappe-client-get, rest-resource-get, desk-getdoc, desk-get-docinfo, frappe-client-get-list, frappe-client-get-value, crm-activities, crm-linked-calls, crm-linked-notes, crm-linked-tasks, crm-linked-docs}` × in-scope + out-of-scope, `sentinel_present=false` cho non-system; **3 system positive control** (`frappe-client-get`, `rest-resource-get`, `desk-getdoc`) `sentinel_present=true` ⇒ assertion non-vacuous.
- **SEC-26 residual routes:** `get_filtered_attachments` (in/out), `get_docinfo` (in/out), `getdoc` (in/out), `client-get-list` (in/out), `private-download-out-of-scope-existing-vs-unknown`.
- **Matrix gaps:** `cl09-*` (3), `at06-*` (4), `nt04-*` (2), `tk04-*` (3), `ls04-*` (2), `cross-doctype-get-data-vs-get-list-*` (6 doctype).

## 8. Đối chiếu source tại đúng revision CI (`b1087bc`)

T1C tự tải raw source `apps/crm_cskh/crm_cskh/hooks.py` + `api/guarded_crm.py` @ `b1087bc`:

- `hooks.py` L177–196 `override_whitelisted_methods` nay có **`crm.api.activities.get_activities → crm_cskh.api.guarded_crm.get_activities`** (L196) + `get_linked_calls`/`get_linked_notes`/`get_linked_tasks`/`get_attachments`.
- `guarded_crm.py` có: `_link_target_is_readable` (L163), `_filter_linked_calls_for_scope` (L194 — bỏ row Dynamic Link `link_doctype`/`link_name`, gỡ `recording_url` private), `_filter_linked_calls_payload` (L235), `get_linked_calls` (L266), `get_linked_notes` (L300), `get_linked_tasks` (L306), `get_activities` (L324 — gọi upstream rồi `_filter_linked_calls_payload` + `_redact_private_file_payload` + redact `docinfo`).

## 9. Verdict T1C (LẦN 31)

- **SEC-16 → PASS** (12 permlevel surfaces app-wide + system positive control; hết lý do "chỉ 1 endpoint / derived field").
- **SEC-26 → PASS** (RFC `get_activities` ĐÓNG — override + runtime `file_url_absent`; residual File routes đo qua file-oracle + sec26-*; hết lý do "get_activities chưa override / residual routes chưa đo").
- **SEC tally → 28/28 PASS** (SEC-13 giữ `PASS` **giới hạn topology P0** — điều kiện mở lại khi thêm reverse-proxy/CORS; `coverage_scope` không đổi).
- **Matrix 4 ô NOT RUN (CL-09, AT-06, LEAD-15/cross-doctype, LS-05/cross-doctype) → có HTTP runtime** (cl09-*/at06-*/cross-doctype-*); nhiều ô PARTIAL nâng lên PASS.
- **AT-05 / RA-06 / RA-07 → giữ DEFERRED P1** (cần BOD scope ADR-005 / ownership service ADR-004 — chưa xây, KHÔNG chấm PASS).
- **G0-07 → PASS (T1C ký)** — dựa trên bằng chứng runtime độc lập (99/99 per-method + 18/18 HTTP + 9/9 file-oracle + 62/62 remaining-gaps + Dynamic Link âm/dương + source override), KHÔNG chỉ từ job CI xanh.

## 10. Bất biến / KHÔNG làm

- **KHÔNG merge PR #2.** KHÔNG tự ký `P0` (GO/NO-GO P0 thuộc T0/Owner sau khi tổng hợp các gate).
- **KHÔNG** coi 62/62 static guards là runtime PASS (static tách riêng).
- **KHÔNG** đổi policy/workflow/code/test. **KHÔNG** sửa file T1A/T1B/Owner/ADR. **KHÔNG** stage/commit/push.
- Bàn giao kết luận cho **T1A** để đồng bộ `p0-exit-report.md` (P0 gate).

## 11. Bổ sung — PHỤC HỒI TÀI LIỆU & HÒA GIẢI (theo yêu cầu T0)

- **Nguồn phục hồi:** bản Round 30 (2026-10-28) của 3 file T1C-owned được phục hồi **byte-identical** từ snapshot `snapshot cuc bo ngoai repo (`t1b-pr2-review-e67`)`:
  - `docs/architecture/permission-matrix.md` — 63 070 B, SHA-256 `39405C1E1706…`
  - `docs/architecture/risk-register.md` — 36 503 B, SHA-256 `AE7F8DA70D65…`
  - `deploy/dev/evidence/t1c-security-verification.md` — 60 217 B, SHA-256 `4C7DF92F8996…`
  - Đã hash cả src lẫn dst sau copy — khớp 100%. Lịch sử Round 1–30 bảo toàn; phần thân cũ 2026-10-19 giữ nguyên làm lịch sử (không xóa).
- **Cảnh báo snapshot 28/10:** snapshot KHÔNG chứa các tinh chỉnh 2026-10-29 của workspace đã bị revert (Q1–Q4 tái xác nhận Owner 2026-10-29, RA-04 D/fail-closed, SEC-13 giới hạn topology P0, AT-05/RA-06/RA-07 DEFER P1) — các quyết định Owner này được ghi lại trong Round 31 (đã khớp với quyết định Owner ghi ở Round 30 snapshot, chỉ bổ sung khung DEFER P1/giới hạn topology).
- **Bản ghi Lần 31 trước đó (ghi nhầm lên nền "1.0") đã được thay thế** bằng bản ghi Round 31 trên nền Round 30 phục hồi; file này giữ nguyên làm evidence của kiểm định.
- **Đối chiếu tiêu chí SEC-16/26 với phạm vi probe (trước khi ký G0-07):** SEC-26 — đủ 4 tiêu chí đóng (a)-(d) của Round 30; lưu ý phạm vi probe = direct `bench serve` (không phủ reverse-proxy/CORS — thuộc SEC-13, đã PASS giới hạn topology P0). SEC-16 — 12 permlevel surfaces + 3 system positive control (non-vacuous); `guarded_reporting` (BOD reporting) KHÔNG nằm trong phạm vi probe và thuộc P1 (AT-05/BD-01 DEFER) — không dùng để chấm SEC-16. **G0-07 PASS được ký trên cơ sở này, không chỉ từ CI xanh.**
- **Trạng thái working tree:** 3 file T1C-owned modified (unstaged) + file này untracked; KHÔNG stage/commit/push/merge (đã kiểm bằng `git status --short`).
