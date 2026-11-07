# Gói bằng chứng kiểm thử Việt hóa & đồng bộ báo cáo P3 — Không gian tuyển dụng (`6912c78`)

Thư mục này đóng gói trọn bộ script tái chạy, báo cáo trình duyệt `browser_e2e_report.json`, 17 ảnh chụp màn hình được báo cáo tham chiếu, bộ JUnit XML + log từng suite, log đối chiếu các lượt chạy `test_p3_reporting_and_security`, và bảng băm toàn vẹn `SHA256SUMS.txt` cho mã nguồn tại SHA **`6912c78c1c74483c1a0063379d94d8c9b8bdb30b`**.

---

## 1. Revision thực chạy & phiên bản môi trường

| Hạng mục | Giá trị thực chạy |
| :--- | :--- |
| **SHA mã nguồn được kiểm thử (`testedCodeSha`)** | `6912c78c1c74483c1a0063379d94d8c9b8bdb30b` (merge UI `b36b6c402c4b83167fe83bf95e37ca47cb0fea1a` + backend `32b53fb51a87b85b7e04da4fbd1c62259affe616`) |
| **Tree SHA `apps/crm_cskh`** | `8bab15364b7965250b4e2ac85c86fe99142d7cca` (mount chỉ đọc `:ro` vào `/home/frappe/frappe-bench/apps/crm_cskh/crm_cskh`) |
| **Upstream `apps/frappe` SHA** | `d2efa79657ba5c1abf96098b3bfacc199517d57e` (`v15.123.1`) |
| **Upstream `apps/crm` SHA** | `0d3910b456ef4fb913e77b29db011b3debc4f778` (`v1.87.0`) |
| **Docker base image** | `crm-cskh:0.0.1-92e14af` (`sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`) |
| **MariaDB / Redis digests** | `mariadb@sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa` · `redis@sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` |
| **Trình duyệt E2E (`browser.version`)** | **Headless Chromium `153.0.8010.12`** |
| **Playwright / Node.js** | `playwright@1.63.0` · `Node.js v22.23.3` |
| **Python runtime** | `Python 3.11.18` (container bench env) · `Python 3.12.10` (host orchestrator) |
| **Cấu hình phiên trình duyệt** | Viewport `1440x920`, locale `vi-VN`, múi giờ `Asia/Ho_Chi_Minh` |
| **Môi trường cô lập** | Compose project `cskh-recruit-ui-verify`, site `recruit-ui.localhost`, cổng nội bộ `127.0.0.1:18620`, tự động `down -v --remove-orphans` sau khi chạy |

---

## 2. Lệnh chạy tái lập

### 2.1. Chạy trọn gói một lệnh (khuyến nghị cho T1C)

Từ thư mục gốc repository:

```bash
python deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/rerun_recruitment_vi_suites.py
```

Script trên tự động thực hiện tuần tự:
1. Chạy 5 cổng kiểm tra tĩnh/hợp đồng offline (`01..05`).
2. Dựng stack cô lập `cskh-recruit-ui-verify` (`scripts/docker-compose.recruit-ui.yml`) và khởi tạo `recruit-ui.localhost` sạch (`scripts/bootstrap_recruit_ui.sh`, không ghi đè cờ `p3_*_enabled` vào `site_config.json`).
3. Chạy 6 module Frappe unittest có xuất JUnit XML chuẩn (`06..11`) bằng `scripts/run_frappe_junit.py`, sau đó kiểm tra fail-closed **48/48 method** qua `deploy/dev/seed/ci_junit_gate.py`.
4. Tạo dữ liệu mẫu tuyển dụng & báo cáo P3 (`scripts/seed_recruitment_sample.py`), bật `bench worker` + `bench serve`, chạy kiểm thử trình duyệt Chromium E2E (`12`, `scripts/verify_recruitment_ui_browser.mjs`), sinh `browser_e2e_report.json` và 17 ảnh `screenshots/01-...png` .. `17-...png`.
5. Hạ toàn bộ container/volume của `cskh-recruit-ui-verify`, xóa tệp thông tin đăng nhập tạm ở thư mục temp của hệ điều hành, và cập nhật `suite_results.json` + `SHA256SUMS.txt`.

### 2.2. Kiểm tra toàn vẹn manifest SHA-256

```bash
# Trên Linux / Git Bash:
(cd deploy/dev/evidence/recruitment-workspace-ui-20261107 && sha256sum -c SHA256SUMS.txt)

# Hoặc bằng Python đa nền tảng:
python -c "import hashlib, pathlib; b=pathlib.Path('deploy/dev/evidence/recruitment-workspace-ui-20261107'); lines=b.joinpath('SHA256SUMS.txt').read_text('utf-8').splitlines(); bad=[l for l in lines if hashlib.sha256((b/l.split('  ',1)[1]).read_bytes()).hexdigest()!=l.split('  ',1)[0]]; assert not bad, bad; print(f'VERIFIED {len(lines)}/{len(lines)} FILES OK')"
```

---

## 3. Kết quả từng suite (`12/12 PASS` — `48/48` JUnit methods)

Báo cáo tổng hợp máy đọc được lưu tại `suite_results.json` và `junit/junit_per_method_gate.json`.

| # | Suite ID | Lệnh thực thi | Phạm vi | Kết quả | Thời lượng | Bằng chứng (Log / JUnit / Report) |
| :-: | :--- | :--- | :--- | :---: | :---: | :--- |
| 01 | `01-check_translations` | `python deploy/p1/check_translations.py apps/crm_cskh/crm_cskh/translations/vi.csv` | Toàn bộ (`1331` dòng, `0` lỗi cú pháp) | **PASS** | `0.093s` | `logs/01-check_translations.log` |
| 02 | `02-check_translations_audit` | `python deploy/p1/check_translations_audit.py` | Toàn bộ (`missing=0`, `conflict=0`, `orphan=0`) | **PASS** | `0.688s` | `logs/02-check_translations_audit.log` |
| 03 | `03-verify_p3_metric_parity` | `python deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_p3_metric_parity.py` | Toàn bộ (`29/29` chỉ số & `5/5` điểm thành phần khớp 3 chiều) | **PASS** | `0.172s` | `logs/03-verify_p3_metric_parity.log` |
| 04 | `04-test_p2_ui_timezone_and_contracts` | `node deploy/p2/test_p2_ui_timezone_and_contracts.mjs` | Toàn bộ hợp đồng UI & múi giờ P2 | **PASS** | `0.125s` | `logs/04-test_p2_ui_timezone_and_contracts.log` |
| 05 | `05-test_p3_ui_timezone_and_contracts` | `node deploy/p3/test_p3_ui_timezone_and_contracts.mjs` | Toàn bộ hợp đồng UI, N/A, provisional & DOM an toàn P3 | **PASS** | `0.703s` | `logs/05-test_p3_ui_timezone_and_contracts.log` |
| 06 | `06-test_ui_p1_endpoints` | `docker exec cskh-recruit-ui-verify-web-1 bash -lc "/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py recruit-ui.localhost crm_cskh.tests.test_ui_p1_endpoints /tmp/junit/test_ui_p1_endpoints.junit.xml"` | Trọn module (`12/12` methods) | **PASS** | `13.406s` | `junit/test_ui_p1_endpoints.junit.xml`<br>`logs/06-test_ui_p1_endpoints.log` |
| 07 | `07-test_ui_p2_pages_and_context` | `docker exec cskh-recruit-ui-verify-web-1 bash -lc "/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py recruit-ui.localhost crm_cskh.tests.test_ui_p2_pages_and_context /tmp/junit/test_ui_p2_pages_and_context.junit.xml"` | Trọn module (`9/9` methods) | **PASS** | `8.406s` | `junit/test_ui_p2_pages_and_context.junit.xml`<br>`logs/07-test_ui_p2_pages_and_context.log` |
| 08 | `08-test_ui_p3_pages_and_context` | `docker exec cskh-recruit-ui-verify-web-1 bash -lc "/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py recruit-ui.localhost crm_cskh.tests.test_ui_p3_pages_and_context /tmp/junit/test_ui_p3_pages_and_context.junit.xml"` | Trọn module (`7/7` methods) | **PASS** | `5.187s` | `junit/test_ui_p3_pages_and_context.junit.xml`<br>`logs/08-test_ui_p3_pages_and_context.log` |
| 09 | `09-test_p2_outcomes_policy` | `docker exec cskh-recruit-ui-verify-web-1 bash -lc "/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py recruit-ui.localhost crm_cskh.tests.test_p2_outcomes_policy /tmp/junit/test_p2_outcomes_policy.junit.xml"` | Trọn module (`5/5` methods) | **PASS** | `7.000s` | `junit/test_p2_outcomes_policy.junit.xml`<br>`logs/09-test_p2_outcomes_policy.log` |
| 10 | `10-test_p3_kpi_and_snapshots` | `docker exec cskh-recruit-ui-verify-web-1 bash -lc "/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py recruit-ui.localhost crm_cskh.tests.test_p3_kpi_and_snapshots /tmp/junit/test_p3_kpi_and_snapshots.junit.xml"` | Trọn module (`8/8` methods) | **PASS** | `11.766s` | `junit/test_p3_kpi_and_snapshots.junit.xml`<br>`logs/10-test_p3_kpi_and_snapshots.log` |
| 11 | `11-test_p3_reporting_and_security` | `docker exec cskh-recruit-ui-verify-web-1 bash -lc "/home/frappe/frappe-bench/env/bin/python /tmp/exec_junit.py recruit-ui.localhost crm_cskh.tests.test_p3_reporting_and_security /tmp/junit/test_p3_reporting_and_security.junit.xml"` | Trọn module (`7/7` methods) | **PASS** | `16.469s` | `junit/test_p3_reporting_and_security.junit.xml`<br>`logs/11-test_p3_reporting_and_security.log` |
| 12 | `12-verify_recruitment_ui_browser` | `node deploy/dev/evidence/recruitment-workspace-ui-20261107/scripts/verify_recruitment_ui_browser.mjs` | Trọn luồng E2E (`8` nhóm kiểm chứng, `17` ảnh, `2` CSV export, `0` lỗi console/500/XSS) | **PASS** | `55.860s` | `browser_e2e_report.json`<br>`logs/12-verify_recruitment_ui_browser.log` |

---

## 4. Đối chiếu chi tiết các lượt chạy `crm_cskh.tests.test_p3_reporting_and_security`

Trong quá trình xác minh ban đầu trên môi trường nháp, module `crm_cskh.tests.test_p3_reporting_and_security` đã trải qua 3 lượt chạy chẩn đoán trước khi chạy lại chuẩn hóa ở Lượt 4. Toàn bộ log nguyên văn của 3 lượt lịch sử và JUnit XML + log cuối cùng đều được lưu kèm trong gói bằng chứng này; **không có bất kỳ chỉnh sửa mã nguồn ứng dụng nào sau SHA `6912c78c1c74483c1a0063379d94d8c9b8bdb30b`**.

| Lượt | Phạm vi thực thi | Kết quả | Bằng chứng lưu trữ | Khác biệt cấu hình & nguyên nhân gốc |
| :-: | :--- | :---: | :--- | :--- |
| **Lượt 1** *(lịch sử)* | **Trọn module (`7/7` methods)** — chạy độc lập (không chạy `test_p3_kpi_and_snapshots` trước đó), `site_config.json` có ghi đè `p3_reporting_enabled=1` & `p3_sla_enabled=1` | **FAILED**<br>(`5` pass, `1` error, `1` fail) | `logs/historical-p3-reporting-runs/run1-standalone-siteconfig-override-7methods.log` | 1. **`ERROR` tại `test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled`** (`KeyError: 'reporting_enabled'` ở `test_p3_reporting_and_security.py:902`): script bootstrap nháp gọi `bench set-config p3_reporting_enabled 1` ghi thẳng vào `site_config.json` (`frappe.conf`). Hàm `is_p3_reporting_enabled()` (`services/p3_flags.py:16-18`) ưu tiên `frappe.conf` cao hơn `CSKH Policy`, làm vô hiệu thao tác tắt cờ trên `CSKH Policy` của bài test.<br>2. **`FAIL` tại `test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10`** (`AssertionError: 0 not greater than or equal to 1` trên `overview["attempt_breakdown"]["verified"]` ở dòng `249`): xem phân tích phụ thuộc fixture liên module bên dưới. |
| **Lượt 2** *(lịch sử)* | **Trọn module (`7/7` methods)** — sau khi gỡ `p3_reporting_enabled` & `p3_sla_enabled` khỏi `site_config.json`, nhưng vẫn chạy độc lập đơn lẻ | **FAILED**<br>(`6` pass, `0` error, `1` fail) | `logs/historical-p3-reporting-runs/run2-standalone-clean-siteconfig-7methods.log` | Gỡ ghi đè khỏi `site_config.json` giúp `test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled` **PASS** hoàn toàn (`6/7` methods PASS). Chỉ còn duy nhất `test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10` báo `verified == 0` do chạy tách rời khỏi `test_p3_kpi_and_snapshots`. |
| **Lượt 3** *(lịch sử — phạm vi thu hẹp)* | **Chỉ chạy 1 method lẻ (`1/7` method)**: `--test test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10` sau khi nạp lại `ensure_p2_seeds` / `ensure_p3_seeds` | **FAILED**<br>(`0` pass, `1` fail) | `logs/historical-p3-reporting-runs/run3-single-method-bod-s08-s09-s10.log` | Lượt chẩn đoán thu hẹp **1 method duy nhất** để loại trừ giả thuyết thiếu seed `CSKH Policy`; xác nhận nguyên nhân 100% nằm ở phụ thuộc dữ liệu giữa hai module P3 trong thứ tự CI chuẩn. |
| **Lượt 4** *(cuối cùng — bàn giao)* | **Trọn module (`7/7` methods)** — chạy trên `site_config.json` sạch theo đúng trình tự CI chuẩn (`wipe_clean` → `test_p3_kpi_and_snapshots` (`8/8`) → `test_p3_reporting_and_security` (`7/7`)) | **PASS**<br>(`7` pass, `0` error, `0` fail, `0` skip) | `junit/test_p3_reporting_and_security.junit.xml`<br>`logs/11-test_p3_reporting_and_security.log`<br>`junit/junit_per_method_gate.json` | Khớp đúng cấu hình và thứ tự thực thi của `.github/workflows/p3.yml:128-129` và `deploy/p3/run_suites.py:75`; cả **7/7 method PASS** mà không sửa bất kỳ dòng code ứng dụng nào. |

### Giải thích chi tiết phụ thuộc fixture ở `test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10`

- Trong `apps/crm_cskh/crm_cskh/tests/test_p3_reporting_and_security.py:98-132`, hàm `_seed_two_team_activity()` gọi `p2_base.make_native_call_log()` (`apps/crm_cskh/crm_cskh/tests/p2_base.py:296-322`) mà không truyền `telephony_medium` (giá trị mặc định `""`).
- Theo quy tắc bảo mật `SEC2-02` tại `apps/crm_cskh/crm_cskh/services/call_log_adapter.py:117-123`, mọi `CRM Call Log` có `telephony_medium` rỗng hoặc `"Manual"` đều nhận `verification_status = "self_reported"` (không tự nâng lên `"verified"`).
- Trong pipeline CI gốc của dự án (`.github/workflows/p3.yml:128-129` và `deploy/p3/run_suites.py:75`), module `crm_cskh.tests.test_p3_kpi_and_snapshots` luôn chạy ngay trước `crm_cskh.tests.test_p3_reporting_and_security` trên cùng site kiểm thử; tại `test_p3_kpi_and_snapshots.py:305-311`, bài test tạo bản ghi `CSKH Contact Attempt` có `verification_status = "verified"` trong `month_key` hiện hành, và `TestP3ReportingAndSecurity.setUp()` (`test_p3_reporting_and_security.py:53-62`) chủ đích giữ nguyên bảng `CSKH Contact Attempt`.

---

## 5. Danh mục 17 ảnh chụp màn hình được `browser_e2e_report.json` tham chiếu

Toàn bộ 17 tệp dưới đây khớp 1:1 với mảng `screenshots[]` trong `browser_e2e_report.json`:

| STT | Tệp ảnh (`screenshots/`) | Vai trò | Màn hình & nội dung đối chứng |
| :-: | :--- | :--- | :--- |
| 01 | `01-crm-leads-list-staff.png` | Nhân viên | Danh sách Hồ sơ liên hệ (`/crm/leads`) & nút lối vào **Không gian tuyển dụng** |
| 02 | `02-crm-lead-detail-staff.png` | Nhân viên | Chi tiết Hồ sơ liên hệ (`/crm/leads/<id>`): nút **Chuyển thành Giao dịch CRM**, nút **Mở trong Không gian tuyển dụng**, và ghi chú mẫu lương/ca làm việc đã khử XSS |
| 03 | `03-crm-deals-list-staff.png` | Nhân viên | Danh sách **Giao dịch CRM** gốc (`/crm/deals`) & lối vào **Không gian tuyển dụng** |
| 04 | `04-cskh-workspace-home-staff.png` | Nhân viên | Trang chủ & menu **Không gian tuyển dụng** (`/cskh`) — vai trò Nhân viên |
| 05 | `05-cskh-workspace-home-admin.png` | Quản trị CRM | Trang chủ & menu 6 bước **Không gian tuyển dụng** (`/cskh`) — vai trò Quản trị CRM |
| 06 | `06-step1-import-preview-admin.png` | Quản trị CRM | Bước 1 — **Nhập danh sách liên hệ**: xem trước & kiểm trùng/lỗi (`/cskh/import`) |
| 07 | `07-step1-import-completed-admin.png` | Quản trị CRM | Bước 1 — **Nhập danh sách liên hệ**: hoàn tất lô nhập & điều hướng sang Kho hồ sơ (`/cskh/import`) |
| 08 | `08-step2-pool-batch-admin.png` | Quản trị CRM | Bước 2 — **Kho hồ sơ liên hệ**: trạng thái *Chưa phân phối* & nút chuyển sang Phân công (`/cskh/pool`) |
| 09 | `09-step3-campaigns-preview-admin.png` | Quản trị CRM | Bước 3 — **Phân công**: xem trước phân phối hồ sơ liên hệ (`/cskh/campaigns`) |
| 10 | `10-step3-campaigns-distributed-admin.png` | Quản trị CRM | Bước 3 — **Phân công**: hoàn tất phân phối & nút mở Hồ sơ được giao (`/cskh/campaigns`) |
| 11 | `11-step4-assigned-profiles-staff.png` | Nhân viên | Bước 4 — **Hồ sơ được giao**: nhận hồ sơ `Hoàng Minh Tuấn (Luồng 6 Bước)` vừa phân phối (`/cskh/my-customers`) |
| 12 | `12-step5-record-attempt-staff.png` | Nhân viên | Bước 5 — **Ghi nhận liên hệ**: kết quả *Hoàn tất mục tiêu liên hệ chiến dịch* & hộp lưu ý giữ đúng nghĩa kết quả liên hệ |
| 13 | `13-step5-profile-detail-staff.png` | Nhân viên | Bước 5 — **Chi tiết hồ sơ liên hệ** & Nhật ký hợp nhất (`/cskh/work-item`) |
| 14 | `14-step6-callbacks-staff.png` | Nhân viên | Bước 6 — **Lịch hẹn liên hệ lại**: theo dõi & xử lý lịch hẹn của `Hoàng Minh Tuấn (Luồng 6 Bước)` (`/cskh/callbacks`) |
| 15 | `15-p3-quality-overview-admin.png` | Quản trị CRM | Báo cáo P3 — **Tổng quan chất lượng** (`/cskh/quality-overview`): đồng bộ tên chỉ số giữa Thẻ KPI & Bảng đối chiếu đội nhóm |
| 16 | `16-p3-campaign-quality-admin.png` | Quản trị CRM | Báo cáo P3 — **Hiệu quả chiến dịch & nguồn liên hệ** (`/cskh/campaign-quality`): đồng bộ *Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch* trên Thẻ & Bảng chi tiết |
| 17 | `17-p3-personal-scorecard-staff.png` | Nhân viên | Báo cáo P3 — **Bảng điểm chất lượng cá nhân** (`/cskh/scorecard`): đồng bộ Thẻ KPI, Điểm thành phần & CSV cá nhân |
