# Hồ Sơ Rà Soát Bảo Mật, Phân Quyền Phía Server, Ma Trận `S01`–`S20` & Ánh Xạ Kiểm Thử `113` Endpoint (`P4-04` — Cổng `G10`–`G12`)

- **Mã tài liệu:** `P4-SECURITY-REVIEW` (`docs/p4/security-review.md` — `P4.md §8`, `§13.2`, `§20.2`)
- **Tài liệu liên kết:** [`pii-field-catalog.md`](./pii-field-catalog.md) · [`api-contract.md`](./api-contract.md) · [`t0-critical-cve-dossier.md`](../../deploy/release/t0-critical-cve-dossier.md)
- **Bằng chứng kiểm thử tự động đã thực thi (`0` lỗi rò rỉ chéo PII):**
  1. **Kiểm thử JUnit trên image bất biến `ghcr.io/nobita6986/crm-cskh@sha256:693a3cdcc758dab64b3ac61d8e6f066581d94b362c9649d709684b72d7d0a2e6`** (GitHub Actions Run `42984867723`, [`diff-impact-verification-summary.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/diff-impact-verification-summary.json)): **`426/426` ca kiểm thử đạt `PASS` (`0` failures, `0` errors)** trên 31 module (`g007=99`, `p1=209`, `p2=38`, `p3=39`, `p3_ui=7`, `auxiliary=34`).
  2. **Diễn tập HTTPS thật trên môi trường Staging/DR cô lập** ([`deploy/ops/manage_isolated_staging_dr.py`](../../deploy/ops/manage_isolated_staging_dr.py)): kiểm chứng trực tiếp qua TLS reverse proxy các header bảo mật, phiên `HttpOnly`/`Secure`, chặn CSRF, ma trận từ chối `401`/`403` giữa `Staff A`, `Staff B`, `Leader`, `BOD`, `Guest`, thu hồi tức thì khi điều chuyển `A → B → A`, chống giả mạo `performed_by`, khử XSS `<script>` và ủy quyền tệp `/private/files/*` + `CSKH Export Job`.

---

## 1. Mô Hình Đe Dọa (`Threat Model`) & Các Lớp Kiểm Soát Thực Thi (`P4.md §8.1`–`§8.3`)

### 1.1. Tài sản bảo vệ & Bề mặt tấn công
- **Tài sản trọng yếu:** Số điện thoại ứng viên (`mobile_no`, `phone`, `normalized_phone`), ghi chú trao đổi lương/ca làm việc (`note`, `FCRM Note`), tệp CSV nhập kho & tệp xuất báo cáo (`private/files`), lịch sử phân công (`CSKH Assignment History`), kết quả KPI/QA nhân sự, cookie phiên `sid`, khóa mã hóa gói sao lưu (`backup-key`) và `site_config.json`.
- **Nguyên tắc bất khả xâm phạm (`P4.md §8.1`):** Ẩn nút trên giao diện không phải là phân quyền. `100%` kiểm tra quyền diễn ra ở tầng Python/SQL phía server qua `permission_query_conditions`, `has_permission`, `auth_hooks`, `doc_events`, `override_whitelisted_methods` và kiểm tra phạm vi trực tiếp trong từng hàm dịch vụ.

### 1.2. Các lớp phòng vệ kỹ thuật đang có hiệu lực
1. **Mạng, TLS & Security Headers (`deploy/images/resources/security_headers.conf`):**
   - MariaDB (`db`) và Redis (`redis-cache`, `redis-queue`) nằm hoàn toàn trong mạng Docker nội bộ (`data_net` `internal: true`), **`0` cổng mở ra Internet**.
   - Nginx/Proxy gắn cứng cho mọi phản hồi (`always`):
     - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
     - `X-Frame-Options: SAMEORIGIN`
     - `X-Content-Type-Options: nosniff`
     - `X-XSS-Protection: 1; mode=block`
     - `Referrer-Policy: same-origin, strict-origin-when-cross-origin`
2. **Phiên đăng nhập, Thu hồi Tức thì & CSRF (`G11`):**
   - Toàn bộ `113` endpoint `crm_cskh` đặt `allow_guest=False`.
   - Mọi lệnh ghi yêu cầu `X-Frappe-CSRF-Token` hợp lệ (`S16`).
   - Khi khóa tài khoản nghỉ việc (`User.enabled = 0`) hoặc thu hồi/điều chuyển hồ sơ (`ownership.reassign` / `revoke`), phiên HTTP cũ mất hiệu lực tức thì (`401`/`403`), `tabSessions` và khóa phiên trên `redis-cache` bị xóa, mọi kết nối/kết nối lại Socket.IO bằng `sid` cũ bị từ chối ngay ở bước bắt tay namespace (`44/<site>`), và toàn bộ `DocShare` / `ToDo` cũ trên `CRM Lead` được dọn sạch đồng bộ (`clear_stale_lead_shares`, `INV-01 == 0`, `INV-03 == 0`). *(Lưu ý kiến trúc Frappe v15 trên topology 10 dịch vụ hiện tại: một kết nối TCP WebSocket rảnh đã mở sẵn từ trước thời điểm khóa chưa tự đóng cưỡng bức từ phía tiến trình Node `websocket`, nhưng mọi yêu cầu `doc_subscribe` mới đều gọi lại `can_subscribe_doc` sang `backend` với `sid` đã thu hồi nên bị chặn `403` và `0` rò rỉ PII.)*
3. **Bảo vệ Tệp Riêng tư, Đóng Oracle Dò Tồn tại & Kiểm soát Xuất Báo cáo 3 Lớp (`G12`):**
   - Tệp nhập kho và tệp xuất báo cáo luôn lưu với `is_private = 1` (`/private/files/...`), đi qua `crm_cskh.permissions.linked_lead_has_permission`.
   - Hook `crm_cskh.errors.normalize_error_response` chuẩn hóa phản hồi `403`/`404` trên `/api/resource/File/<name>` và `frappe.client.get(doctype="File")` về cùng mã `404` và chuỗi JSON cố định byte-identical, triệt tiêu kênh dò sự tồn tại của tệp.
   - `CSKH Export Job` kiểm tra 3 bước độc lập: (a) lúc `request_export` (chặn `BOD` truyền `include_pii=1`), (b) lúc worker tạo tệp (trung hòa ký tự đầu `=`, `+`, `-`, `@` bằng dấu nháy đơn `'` chống CSV Formula Injection), và (c) lúc `download_export` (chỉ chính người tạo còn quyền hoạt động và trước `expires_at` mới tải được; `expire_stale_exports` thu hồi định kỳ mỗi giờ).
4. **Chống XSS & Khử PII trong Log/Cảnh báo (`S17`, `S20`):**
   - Ghi chú liên hệ, lý do dời hẹn, nhãn chiến dịch và phản hồi lỗi đều đi qua bộ làm sạch HTML/script (`crm_cskh.services.sanitizer`) và bộ khử SĐT/token (`redact_pii_text`, `redact_payload_text` trong `check_health_and_invariants.py`).

---

## 2. Ma Trận Kiểm Thử Phá Ranh Giới Quyền `S01`–`S20` (`P4.md §13.2` — Cổng `G10`–`G12`)

| Mã | Kịch bản kiểm thử bảo mật bắt buộc | Kỳ vọng | Module JUnit & Kiểm chứng HTTPS Cô lập | Kết quả |
| :--- | :--- | :--- | :--- | :---: |
| **S01** | `Staff A` đọc `CRM Lead` của `Staff B` qua REST `/api/resource/CRM Lead/<id>` | Từ chối `403`, `0` lộ SĐT/tên | `test_permissions`, `test_p1_api_permissions`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S02** | `Staff A` sửa `CRM Lead` của `Staff B` qua REST `PUT` / `frappe.client.set_value` | Từ chối `403`, DB không đổi | `test_permissions`, `test_security_remediation_v2`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S03** | `Staff A` đọc `CSKH Contact Attempt` của `Staff B` | Từ chối `403` | `test_p2_permissions_sec`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S04** | `Staff A` đọc `CRM Call Log` của `Staff B` qua REST / `get_call_log` / `get_linked_calls` | Từ chối `403`, `0` lộ SĐT/ghi âm | `test_bt1c5_matrix_gaps`, `test_p2_permissions_sec`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S05** | `Staff A` đọc/sửa `CRM Task` hoặc `FCRM Note` gắn khách của `Staff B` | Từ chối `403` | `test_bt1c5_matrix_gaps`, `test_p2_permissions_sec` | **`PASS`** |
| **S06** | `Staff A` hoặc `Guest` mở trực tiếp URL `/private/files/...` gắn hồ sơ của `Staff B` | `Guest` `401/403`, `Staff A` `403`, `Staff B` `200` | `test_security_remediation`, `test_p2_permissions_sec`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S07** | `Staff A` hoặc `Guest` mở link `download_export` của `BOD` hoặc link đã quá `expires_at` | Từ chối `401`/`403`/`410` | `test_p3_reporting_and_security`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S08** | `Staff A` gọi `frappe.desk.form.assign_to.add` hoặc `remove_assignments` để chiếm/gỡ khách `B` | Từ chối `403`, không sinh `ToDo` | `test_docshare_lifecycle`, `test_security_remediation_v2`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S09** | `Staff A` chèn `lead_owner`, `assigned_to`, `performed_by`, `team` giả vào payload ghi | Bị từ chối `403` hoặc ghi đè bằng `session.user` | `test_p1_api_permissions`, `test_p2_permissions_sec`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S10** | `Staff A` gọi trực tiếp `distribution.preview/execute` hoặc `ownership.assign/reassign/revoke` | Từ chối `403` | `test_p1_api_permissions`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S11** | `Leader A` truyền bộ lọc `team=TEAM-B`, `employee=StaffB` trên dashboard/báo cáo/SLA | Từ chối `403 FORBIDDEN_SCOPE`, không rò số tổng | `test_p3_reporting_and_security`, `test_security_remediation_v2`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S12** | `BOD` (`CSKH BOD Viewer`) thử gọi API quản trị / sửa dữ liệu / yêu cầu `include_pii=1` | Từ chối `403`, không có quyền `System Manager` | `test_p3_reporting_and_security`, `test_p3_t1c_remediation`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S13** | Sau khi điều chuyển `A → B`, `Staff A` đọc lại bằng ID `CRM Lead` / `CSKH Work Item` cũ | Từ chối `403` ngay lập tức | `test_p1_ownership`, `test_docshare_lifecycle`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S14** | Điều chuyển vòng lặp `A → B → A`, kiểm tra `DocShare` / `ToDo` / `CRM Task` | `0` quyền thừa tồn đọng, `INV-01 == 0`, `INV-03 == 0` | `test_docshare_lifecycle`, `test_p3_sla_and_attribution`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S15** | Tài khoản bị khóa (`User.enabled = 0`) tiếp tục dùng cookie phiên cũ | Phiên bị từ chối tức thì (`401`/`403`) | `test_p1_api_permissions`, `test_security_remediation_v2` | **`PASS`** |
| **S16** | Gọi lệnh ghi `POST /api/method/crm_cskh.*` thiếu hoặc sai `X-Frappe-CSRF-Token` | Từ chối `400`/`403` | `test_security_remediation`, `verify_offsite_restore.py`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S17** | Chèn mã `<script>alert(1)</script>` / HTML độc hại / công thức CSV `=CMD|...` vào tên/ghi chú | Đã làm sạch thẻ độc hại & tiền tố `'` trong CSV | `test_p2_permissions_sec`, `test_p3_reporting_and_security`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S18** | Gửi `page_size=99999` hoặc tạo hàng loạt job xuất báo cáo vượt hạn mức | Bị kẹp `<= 100` bản ghi/trang; chặn vượt quota xuất | `test_p1_api_permissions`, `test_p3_reporting_and_security`, `manage_isolated_staging_dr.py` | **`PASS`** |
| **S19** | Truy cập trực tiếp `/api/resource/CSKH KPI Snapshot` hoặc `reportview_get` của cá nhân khác | Từ chối `403` / lọc rỗng | `test_p3_reporting_and_security`, `manage_isolated_staging_dr.py`, `run_https_browser_uat_and_security.py` | **`PASS`** |
| **S20** | Kiểm tra phản hồi lỗi `403`/`404`, tệp log sức khỏe/backup, gói báo cáo & URL tải tệp | Khử sạch SĐT/sid/token (`<REDACTED_*>`), đóng File oracle | `test_error_response_normalization`, `test_ops_toolkit.py`, `test_offsite_restore.py`, `run_https_browser_uat_and_security.py` | **`PASS`** |

### 2.2. Ánh Xạ Đầy Đủ 16 Mục `P4.md §8.4` (`P4-8.4-01`–`P4-8.4-16`) Trên Trình Duyệt Chromium HTTPS Thật

Toàn bộ 16 bài kiểm thử phá ranh giới quyền bắt buộc của `P4.md §8.4` được thực thi trên trình duyệt **Playwright Headless Chromium** qua TLS 1.3 thật (`ignore_https_errors=False`) bằng đúng 6 tài khoản nghiệp vụ (`CRM Admin`, `Leader A`, `Leader B`, `Staff A`, `Staff B`, `BOD` — **`administrator_used_for_business_journeys = false`**) tại [`run_https_browser_uat_and_security.py`](../../deploy/ops/run_https_browser_uat_and_security.py) (bằng chứng đã khử dữ liệu nhạy cảm tại [`https-browser-uat-security-report.json`](../../deploy/ops/evidence/p4-https-delta-20261109/https-browser-uat-security-report.json)):

| Mã `P4.md §8.4` | Actor thực hiện trên Chromium HTTPS | Kịch bản kiểm chứng UI + API | Expected / Actual |
| :--- | :--- | :--- | :---: |
| **`P4-8.4-01`** | `Staff A` | Đổi URL sang `/cskh/work-item?id=<wi_b>` (`#wi-summary-card` bị xóa trắng) + `get` / `PUT` / `export_query` `CRM Lead` của `Staff B` | Deny (`403`, `0` SĐT trên DOM) — **`PASS`** |
| **`P4-8.4-02`** | `Staff A` | Đọc `CRM Call Log` của `Staff B` qua ID trực tiếp | Deny (`403`, `0` PII) — **`PASS`** |
| **`P4-8.4-03`** | `Staff A` | Đọc/sửa `CRM Task`, `FCRM Note`, `CSKH Contact Attempt` của `Staff B` | Deny (`403`) — **`PASS`** |
| **`P4-8.4-04`** | `Staff A` | Mở `/cskh/pool`, `/cskh/campaigns` (`403` SSR) & gọi `distribution.execute`, `ownership.reassign`, `assign_to.add`, `remove_assignments` | Deny (`403`) — **`PASS`** |
| **`P4-8.4-05`** | `Staff A` | Chèn `performed_by` giả của `Staff B` vào payload `record_attempt` | Deny (`403`) — **`PASS`** |
| **`P4-8.4-06`** | `BOD`, `Staff A`, `Guest` | `BOD` mở hộp thoại Xuất CSV trên UI & tải thành công (`0` SĐT); `Staff A` và `Guest` mở URL tải đó | `BOD 200`, `Staff A 403`, `Guest 403` — **`PASS`** |
| **`P4-8.4-07`** | `Leader A`, `Leader B` | `Leader A` xem Đội A (`>0` dòng) rồi sửa URL `?team=TeamB` (`0` dòng + cảnh báo lỗi tiếng Việt); đối xứng cho `Leader B` (`403`) | Deny chéo đội 2 chiều — **`PASS`** |
| **`P4-8.4-08`** | `Staff C` (`enabled=0`) | Khóa tài khoản khi đang mở phiên trình duyệt, tải lại trang & gọi API | Phiên cũ bị từ chối tức thì (`401`/`403`/`/login`) — **`PASS`** |
| **`P4-8.4-09`** | `Staff A` | Truyền `page_size=99999` (`400`) & giả mạo `reference_doctype=User` trên `search_widget` (`403`) & đọc `CSKH KPI Snapshot` của `B` (`403`) | Deny (`400`/`403`) — **`PASS`** |
| **`P4-8.4-10`** | `Staff A`, `Staff B`, `Guest` | Mở `/private/files/...` của `B`, thử `..%2f` traversal, và kiểm `/api/resource/File/<id>` chuẩn hóa `404` | `A 403`, `Guest 403`, `B 200`, Oracle `404` — **`PASS`** |
| **`P4-8.4-11`** | `CRM Admin`, `Staff A`, `Staff B` | `CRM Admin` chuyển `A → B → A`: kiểm tra UI + API trước và ngay sau từng lần chuyển + `0` `DocShare`/`ToDo` mồ côi | `A` mất quyền ngay (`403`), `B` nhận (`200`), rồi hoàn trả chuẩn — **`PASS`** |
| **`P4-8.4-12`** | `BOD` | Mở hộp thoại Drill-down KPI (`0` SĐT thô), kiểm `0` role `System Manager`, bị chặn `403` trên `/cskh/work-item`, `/cskh/scorecard`, `request_recompute` | Đúng phạm vi `BOD` — **`PASS`** |
| **`P4-8.4-13`** | `CRM Admin` | Gọi `qa.create_sample` khi `p3_qa_enabled=false` + kiểm tra chặn tự chấm `QA_CONFLICT` trong JUnit | Chặn đúng quy chế — **`PASS`** |
| **`P4-8.4-14`** | `BOD` | Yêu cầu xuất báo cáo kèm `include_pii=1` bị chặn `403`; xuất hợp lệ chạy nền qua hàng đợi | Deny `403` khi `include_pii=1` — **`PASS`** |
| **`P4-8.4-15`** | `Staff A` | Bỏ `X-Frappe-CSRF-Token` (`400`/`403`) & gửi `<script>`/`onerror` trong ghi chú liên hệ rồi tải lại DOM Chromium | `window.__cskh_p4_xss == false`, `<script>` bị khử — **`PASS`** |
| **`P4-8.4-16`** | `BOD` | Gọi lặp lại các endpoint báo cáo chỉ đọc (`get_executive_overview`, `get_team_comparison`) | Số dòng `CSKH KPI Run` không đổi — **`PASS`** |

---

## 3. Bảng Ánh Xạ Đầy Đủ `113` Endpoint Tới Bộ Kiểm Thử Tự Động (`G12`, `G21`)

| Module & Danh sách đầy đủ `113` Endpoint `@frappe.whitelist` | Số lượng | Kiểm thử JUnit (`Run 42625295772` — `421` tests) & Kiểm chứng HTTPS (`manage_isolated_staging_dr.py`) |
| :--- | :---: | :--- |
| **`crm_cskh.api.health`:** `crm_cskh.api.health.ping` | `1` | `test_security_remediation`, `test_security_remediation_v2`, `test_error_response_normalization`, `manage_isolated_staging_dr.py` |
| **`crm_cskh.api.campaigns`:** `crm_cskh.api.campaigns.list`, `crm_cskh.api.campaigns.save`, `crm_cskh.api.campaigns.rules`, `crm_cskh.api.campaigns.save_rule` | `4` | `test_p1_distribution`, `test_p1_api_permissions`, `test_ui_p1_endpoints`, `manage_isolated_staging_dr.py` |
| **`crm_cskh.api.customers`:** `crm_cskh.api.customers.my_list`, `crm_cskh.api.customers.pool_list`, `crm_cskh.api.customers.customer_pool` | `3` | `test_p1_api_permissions`, `test_ui_p1_endpoints`, `run_isolated_benchmark.py`, `manage_isolated_staging_dr.py` (`page_size=99999` clamp) |
| **`crm_cskh.api.pool_ui`:** `crm_cskh.api.pool_ui.list` | `1` | `test_ui_p1_endpoints`, `test_p1_api_permissions` |
| **`crm_cskh.api.dashboard`:** `crm_cskh.api.dashboard.distribution`, `crm_cskh.api.dashboard.campaigns` | `2` | `test_ui_p1_endpoints`, `test_p1_api_permissions` |
| **`crm_cskh.api.import_files`:** `crm_cskh.api.import_files.upload_import_file` | `1` | `test_p1_imports`, `test_p1_api_permissions`, `test_ui_p1_endpoints` |
| **`crm_cskh.api.imports`:** `crm_cskh.api.imports.create_preview`, `crm_cskh.api.imports.confirm`, `crm_cskh.api.imports.get_status` | `3` | `test_p1_imports`, `test_p1_identity`, `test_p1_chaos`, `test_p1_concurrency`, `run_isolated_benchmark.py` |
| **`crm_cskh.api.import_history`:** `crm_cskh.api.import_history.download_error_file`, `crm_cskh.api.import_history.batch_detail`, `crm_cskh.api.import_history.history` | `3` | `test_p1_imports`, `test_p1_regressions`, `test_ui_p1_endpoints` |
| **`crm_cskh.api.distribution`:** `crm_cskh.api.distribution.preview`, `crm_cskh.api.distribution.execute`, `crm_cskh.api.distribution.get_run`, `crm_cskh.api.distribution.reconcile` | `4` | `test_p1_distribution`, `test_p1_concurrency`, `test_p1_chaos`, `test_p1_regressions`, `manage_isolated_staging_dr.py` (`Staff` `403`) |
| **`crm_cskh.api.run_history`:** `crm_cskh.api.run_history.runs` | `1` | `test_ui_p1_endpoints`, `test_p1_distribution` |
| **`crm_cskh.api.ownership`:** `crm_cskh.api.ownership.assign`, `crm_cskh.api.ownership.reassign`, `crm_cskh.api.ownership.revoke`, `crm_cskh.api.ownership.history` | `4` | `test_p1_ownership`, `test_p1_api_permissions`, `test_p1_regressions`, `test_docshare_lifecycle`, `manage_isolated_staging_dr.py` (`A → B → A`) |
| **`crm_cskh.api.ownership_ui`:** `crm_cskh.api.ownership_ui.assigned_list`, `crm_cskh.api.ownership_ui.assignees` | `2` | `test_ui_p1_endpoints`, `test_p1_api_permissions` |
| **`crm_cskh.api.workflow`:** `crm_cskh.api.workflow.list_work_items`, `crm_cskh.api.workflow.get_work_item`, `crm_cskh.api.workflow.list_attempts`, `crm_cskh.api.workflow.list_unlinked_call_logs`, `crm_cskh.api.workflow.record_attempt`, `crm_cskh.api.workflow.get_outcome_codes`, `crm_cskh.api.workflow.schedule_followup`, `crm_cskh.api.workflow.list_followups`, `crm_cskh.api.workflow.complete_followup`, `crm_cskh.api.workflow.close_work_item`, `crm_cskh.api.workflow.reopen_work_item`, `crm_cskh.api.workflow.request_correction`, `crm_cskh.api.workflow.review_correction`, `crm_cskh.api.workflow.list_corrections`, `crm_cskh.api.workflow.progress_summary`, `crm_cskh.api.workflow.reconcile` | `16` | `test_p2_lifecycle`, `test_p2_calllog_followup`, `test_p2_corrections`, `test_p2_outcomes_policy`, `test_p2_permissions_sec`, `test_p2_concurrency_chaos`, `test_p2_reconciliation_perf`, `test_p2_t1c_regressions`, `test_ui_p2_pages_and_context`, `manage_isolated_staging_dr.py` |
| **`crm_cskh.api.settings`:** `crm_cskh.api.settings.list_policies`, `crm_cskh.api.settings.save_policy`, `crm_cskh.api.settings.list_outcomes`, `crm_cskh.api.settings.save_outcome`, `crm_cskh.api.settings.set_lead_dnc` | `5` | `test_p2_outcomes_policy`, `test_p2_permissions_sec`, `test_p2_t1c_regressions`, `test_ui_p2_pages_and_context` |
| **`crm_cskh.api.sla`:** `crm_cskh.api.sla.list_obligations`, `crm_cskh.api.sla.get_obligation_detail`, `crm_cskh.api.sla.waive_obligation`, `crm_cskh.api.sla.pause_obligation`, `crm_cskh.api.sla.resume_obligation`, `crm_cskh.api.sla.evaluate_due_obligations` | `6` | `test_p3_sla_and_attribution`, `test_p3_calendar`, `test_p3_reporting_and_security`, `test_p3_t1c_remediation`, `test_ui_p3_pages_and_context` |
| **`crm_cskh.api.qa`:** `crm_cskh.api.qa.get_qa_config`, `crm_cskh.api.qa.list_reviews`, `crm_cskh.api.qa.create_sample`, `crm_cskh.api.qa.submit_review` | `4` | `test_p3_kpi_and_snapshots`, `test_p3_reporting_and_security`, `test_p3_t1c_remediation`, `test_ui_p3_pages_and_context` |
| **`crm_cskh.api.p3_reporting`:** `crm_cskh.api.p3_reporting.get_executive_overview`, `crm_cskh.api.p3_reporting.get_team_comparison`, `crm_cskh.api.p3_reporting.get_employee_quality`, `crm_cskh.api.p3_reporting.get_campaign_quality`, `crm_cskh.api.p3_reporting.get_my_scorecard`, `crm_cskh.api.p3_reporting.get_filter_options`, `crm_cskh.api.p3_reporting.get_metric_drilldown`, `crm_cskh.api.p3_reporting.request_export`, `crm_cskh.api.p3_reporting.get_export_status`, `crm_cskh.api.p3_reporting.download_export`, `crm_cskh.api.p3_reporting.request_recompute`, `crm_cskh.api.p3_reporting.get_run_status` | `12` | `test_p3_kpi_and_snapshots`, `test_p3_reporting_and_security`, `test_p3_t1c_remediation`, `test_ui_p3_pages_and_context`, `manage_isolated_staging_dr.py` |
| **`crm_cskh.api.reporting` (aliases 1-1):** `crm_cskh.api.reporting.get_executive_overview`, `crm_cskh.api.reporting.get_team_comparison`, `crm_cskh.api.reporting.get_employee_quality`, `crm_cskh.api.reporting.get_campaign_quality`, `crm_cskh.api.reporting.get_my_scorecard`, `crm_cskh.api.reporting.get_filter_options`, `crm_cskh.api.reporting.get_metric_drilldown`, `crm_cskh.api.reporting.request_export`, `crm_cskh.api.reporting.get_export_status`, `crm_cskh.api.reporting.download_export`, `crm_cskh.api.reporting.request_recompute`, `crm_cskh.api.reporting.get_run_status` | `12` | `test_p3_reporting_and_security`, `test_p3_t1c_remediation`, `test_ui_p3_pages_and_context` |
| **`crm_cskh.api.guarded_crm` (`override_whitelisted_methods`):** `crm_cskh.api.guarded_crm.get_contact_by_phone_number`, `crm_cskh.api.guarded_crm.get_contact_lead_or_deal_from_number`, `crm_cskh.api.guarded_crm.get_assigned_users`, `crm_cskh.api.guarded_crm.add_assignment`, `crm_cskh.api.guarded_crm.get_linked_docs_of_document`, `crm_cskh.api.guarded_crm.get_linked_calls`, `crm_cskh.api.guarded_crm.get_linked_notes`, `crm_cskh.api.guarded_crm.get_linked_tasks`, `crm_cskh.api.guarded_crm.get_attachments`, `crm_cskh.api.guarded_crm.get_activities`, `crm_cskh.api.guarded_crm.delete_bulk_docs`, `crm_cskh.api.guarded_crm.get_call_log`, `crm_cskh.api.guarded_crm.search_link`, `crm_cskh.api.guarded_crm.search_widget`, `crm_cskh.api.guarded_crm.validate_link`, `crm_cskh.api.guarded_crm.client_get_value`, `crm_cskh.api.guarded_crm.client_get_count`, `crm_cskh.api.guarded_crm.client_set_value`, `crm_cskh.api.guarded_crm.client_bulk_update` | `19` | `test_permissions`, `test_security_remediation`, `test_security_remediation_v2`, `test_bt1c5_matrix_gaps`, `manage_isolated_staging_dr.py` |
| **`crm_cskh.api.guarded_reporting` (`override_whitelisted_methods`):** `crm_cskh.api.guarded_reporting.reportview_get`, `crm_cskh.api.guarded_reporting.reportview_get_count`, `crm_cskh.api.guarded_reporting.reportview_get_list`, `crm_cskh.api.guarded_reporting.reportview_export_query`, `crm_cskh.api.guarded_reporting.data_export`, `crm_cskh.api.guarded_reporting.query_report_run`, `crm_cskh.api.guarded_reporting.query_report_export_query`, `crm_cskh.api.guarded_reporting.crm_manager_dashboard`, `crm_cskh.api.guarded_reporting.crm_manager_chart` | `9` | `test_permissions`, `test_security_remediation`, `test_security_remediation_v2`, `test_bt1c5_matrix_gaps` |
| **`crm_cskh.permissions` (`override_whitelisted_methods`):** `crm_cskh.permissions.remove_assignments` | `1` | `test_docshare_lifecycle`, `test_security_remediation_v2` |

---

## 4. Quét Lỗ Hổng Gói Phụ Thuộc & Container Image (`Container CVE Status`)

Chi tiết kỹ thuật đầy đủ được lưu tại [`deploy/release/t0-critical-cve-dossier.md`](../../deploy/release/t0-critical-cve-dossier.md) và [`docs/p4/p4-02-cve-exception-requests.md`](./p4-02-cve-exception-requests.md):
1. **Triệt tiêu `100%` lỗ hổng `CRITICAL` có bản vá (`unique_fixable_critical_unapproved = 0`):**
   - Tầng Python đã nâng tương thích lên `frappe v15.126.0`, khóa `PyJWT==2.15.1` và `pillow==13.0.0` (`pip check` = `0` xung đột, không sửa `apps/frappe/pyproject.toml`).
   - Tầng OS đã nâng lên `python:3.11.18-slim-trixie` (`frappe_docker v4.2.0`), loại bỏ hoàn toàn các mã Bookworm cũ trên `zlib1g`, `libsqlite3-0`, `libglib2.0-0`.
2. **Trạng thái `3` mã `CRITICAL` gói nguồn Debian 13 `trixie` chưa có bản vá upstream (`fixed_version: null`):**
   - `CVE-2026-6653` (`libxml2` `<no-dsa> Minor issue`), `CVE-2026-44172` (`src:mariadb` client khi dùng bảng mã `big5` — không áp dụng vì Frappe dùng `PyMySQL==1.1.1` thuần Python + MariaDB khóa cứng `utf8mb4`), và `CVE-2026-49261` (`mariadbd` Galera `wsrep_notify_cmd` — không tồn tại trong image ứng dụng).
   - **Quản trị fail-closed trung thực:** [`deploy/release/cve-exceptions.json`](../../deploy/release/cve-exceptions.json) giữ nguyên `"exceptions": []`. Image `ghcr.io/nobita6986/crm-cskh@sha256:b1329ef7c26a9861a07d25d425eb4438150d065d2548c464e7d3395e568a201d` được xác nhận hợp lệ ở cấp **`staging_candidate`**, trong khi cấp **`production_candidate` giữ `BLOCKED_PENDING_UPSTREAM_FIX_OR_OWNER_EXCEPTION`** cho tới khi Debian phát hành bản vá `deb13u*` hoặc Owner quyết định phương án từng mục (không tự ý chèn ngoại lệ).

---

## 5. Kết Luận Nghiệm Thu Bảo Mật (`G10`–`G12`)

- **Cổng `G10` (Chống truy cập chéo PII `S01`–`S20`):** **`PASS`** (`0` lỗ hổng lộ SĐT/lịch sử chăm sóc/ghi chú/báo cáo chéo vai trò hoặc chéo đội trên cả JUnit `421/421` lẫn diễn tập HTTPS thật).
- **Cổng `G11` (Thu hồi quyền khi khóa tài khoản / điều chuyển / đổi đội):** **`PASS`** (`User.enabled=0` chặn phiên tức thì; `A → B → A` thu hồi quyền `A` ngay lập tức, `0` `DocShare`/`ToDo` mồ côi).
- **Cổng `G12` (Ủy quyền API, tệp riêng tư, xuất báo cáo, giới hạn trang & kiểm toán):** **`PASS`** (`113/113` endpoint có kiểm quyền server-side, đóng File existence oracle, kẹp `page_size <= 100`, trung hòa CSV formula injection và thu hồi tệp xuất hết hạn).
