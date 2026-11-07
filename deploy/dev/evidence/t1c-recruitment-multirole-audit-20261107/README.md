# Gói bằng chứng kiểm định độc lập T1C — Bản Việt hóa tuyển dụng & P3 Parity

Thư mục này lưu trữ toàn bộ bằng chứng kiểm định độc lập của **T1C** trên mã nguồn SHA **`6912c78c1c74483c1a0063379d94d8c9b8bdb30b`** (kết hợp với bằng chứng đóng gói tại commit `14960eb`).

---

## 1. Thành phần gói bằng chứng

- **`scripts/`**:
  - `run_t1c_multirole_browser_audit.mjs`: Script Playwright Chromium kiểm thử tự động 3 vai trò (Nhân viên, Quản trị CRM, BOD), ghi nhận 12 ảnh chụp màn hình và kiểm tra các cổng bảo mật (403 ngoài phạm vi, Zero PII, nhãn Giao dịch CRM).
  - `verify_bod_csv_export.py`: Script kiểm định nội bộ trên máy chủ (server-side direct doc/filesystem) cho luồng xuất báo cáo CSV bằng tài khoản BOD, đối soát 29 dòng chỉ số với tên tiếng Việt chuẩn, kiểm tra Zero PII và các cổng chặn `ForbiddenScope`.
  - `verify_bod_http_csv_download.py`: Script kiểm định độc lập luồng tải CSV qua HTTP xác thực thực tế (BOD login session cookie `sid` -> HTTP POST `request_export` -> HTTP GET `get_export_status` -> HTTP GET `download_export`), đối chiếu header HTTP, SHA-256 tệp tải về, đối soát nhãn và kiểm tra 4 cổng từ chối HTTP (PII, `my_scorecard`, cross-actor Staff download, unauthenticated Guest download).
  - `verify_migrate_x2_and_kpi.py`: Script kiểm tra tính lũy đẳng sau `migrate × 2`, bảo tồn nhãn do Admin tùy chỉnh, bảo tồn `outcome_label_snapshot` và tính bất biến của dữ liệu thô `metrics_json` trong CSDL.
- **`raw_results/`**:
  - `multirole_browser_results.json`: Kết quả thực thi chi tiết của 3 vai trò trên trình duyệt.
  - `bod_executive_overview_export.csv`: Tệp CSV đọc từ tài liệu lưu trữ nội bộ máy chủ `File` của phiên xuất `executive_overview`.
  - `bod_http_downloaded_export.csv`: Tệp CSV nhị phân nhận qua luồng tải HTTP thực tế từ endpoint `download_export` với đầy đủ HTTP response headers.
  - `bod_http_export_verification.json`: Báo cáo chi tiết định dạng JSON ghi nhận trạng thái HTTP, headers, SHA-256 tệp, 30 dòng, 29 chỉ số và kết quả 4 cổng từ chối bảo mật.
  - `p3_reporting_standalone_reconciliation.json`: Ma trận đối soát 7 phương thức kiểm thử của `crm_cskh.tests.test_p3_reporting_and_security` khi chạy độc lập vs chạy theo chuỗi CI.
- **`screenshots/`**:
  - 12 ảnh chụp màn hình kiểm định thực tế (`01-staff-crm-leads-to-cskh.png` .. `12-bod-csv-export-dialog.png`).
- **`SHA256SUMS.txt`**:
  - Bảng băm SHA-256 toàn vẹn cho mọi tệp tin trong gói bằng chứng này.

---

## 2. Danh mục 12 ảnh kiểm định trình duyệt

| STT | Tên tệp ảnh | Vai trò | Màn hình & Nội dung kiểm chứng |
| :-: | :--- | :--- | :--- |
| 01 | `01-staff-crm-leads-to-cskh.png` | Nhân viên | Lối vào **Không gian tuyển dụng** từ `/crm/leads`, nhãn *"Hồ sơ liên hệ"* & *"Giao dịch CRM"* |
| 02 | `02-staff-assigned-candidates-list.png` | Nhân viên | Danh sách **Hồ sơ được giao** (`/cskh/my-customers`) và bộ lọc trạng thái |
| 03 | `03-staff-record-attempt-and-disclaimer.png` | Nhân viên | Ghi nhận liên hệ: Cảnh báo không hiểu nhầm sang đã tuyển/bố trí khi chọn *"Hoàn tất mục tiêu liên hệ chiến dịch"* |
| 04 | `04-staff-callbacks-list.png` | Nhân viên | Màn hình **Lịch hẹn liên hệ lại** (`/cskh/callbacks`) hiển thị lịch hẹn vừa tạo |
| 05 | `05-staff-out-of-scope-forbidden.png` | Nhân viên | Kiểm soát phân quyền: Chặn **`403 FORBIDDEN_SCOPE`** khi mở hồ sơ của nhân viên khác |
| 06 | `06-staff-personal-scorecard.png` | Nhân viên | **Bảng điểm chất lượng cá nhân** (`/cskh/scorecard`) hiển thị đúng tên chỉ số tiếng Việt |
| 07 | `07-admin-crm-deals-native.png` | Quản trị CRM | Màn hình **Giao dịch CRM** gốc (`/crm/deals`), loại bỏ hoàn toàn từ thương mại cũ *"Cơ hội"* |
| 08 | `08-admin-crm-lead-detail.png` | Quản trị CRM | Chi tiết Hồ sơ liên hệ (`/crm/leads/<id>`): Nút *"Mở trong Không gian tuyển dụng"* |
| 09 | `09-admin-settings-outcomes.png` | Quản trị CRM | Cấu hình kết quả (`/cskh/settings`): Đồng bộ *"Hoàn tất mục tiêu liên hệ chiến dịch"* |
| 10 | `10-bod-quality-overview-drilldown.png` | Ban Giám đốc | Tổng quan chất lượng (`/cskh/quality-overview`): Modal Truy vết bản ghi nguồn bảo mật **Zero PII** |
| 11 | `11-bod-team-quality-table.png` | Ban Giám đốc | Bảng **So sánh chất lượng giữa các đội nhóm** (`/cskh/team-quality`) |
| 12 | `12-bod-csv-export-dialog.png` | Ban Giám đốc | Hộp thoại xuất báo cáo CSV với lý do bắt buộc và kiểm soát quyền |

---

## 3. Hòa giải nhật ký `test_p3_reporting_and_security` (7 methods)

- **Khi chạy độc lập trên site sạch (Standalone):**
  - 6/7 methods **PASS**:
    - `test_drilldown_reconciliation_across_eleven_kpis_and_nulls_last_sorting`
    - `test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled` (với `site_config.json` sạch)
    - `test_role_capability_matrix_and_scope_isolation_s01_s02_s04`
    - `test_sla_authority_and_original_due_immutability_s05_s14`
    - `test_three_stage_export_file_lock_revocation_ttl_and_csv_sanitization_s11_s12_s16_s18`
    - `test_transferred_lead_and_staff_transfer_privacy_redaction_s03_s15`
  - 1 method báo lỗi do phụ thuộc fixture: `test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10`:
    - Hàm `_seed_two_team_activity` tạo cuộc gọi không truyền `telephony_medium`.
    - Quy tắc bảo mật SEC2-02 (`call_log_adapter.py:117-123`) phân loại chính xác các cuộc gọi này là `self_reported` (Manual).
    - `overview["attempt_breakdown"]["self_reported"] == 3`, `overview["attempt_breakdown"]["verified"] == 0`, làm fail assertion dòng 249.
- **Khi chạy theo trình tự CI chuẩn (.github/workflows/p3.yml:128-129):**
  - `test_p3_kpi_and_snapshots` chạy ngay trước đó và seed 1 cuộc gọi `verified`, giúp `test_p3_reporting_and_security` đạt **`7/7 PASS`** (`0 failures, 0 errors`).

---

## 4. Phân biệt bằng chứng kiểm định xuất CSV của BOD

Để đảm bảo tính nghiêm ngặt và minh bạch trong kiểm định, T1C phân tách rõ hai tầng bằng chứng:

### 4.1 Bằng chứng đọc tệp trực tiếp trên máy chủ (Server-side File & DB Verification)
- **Phương thức thực hiện:** Gọi nội bộ qua API tầng dịch vụ `crm_cskh.services.p3_export` và đọc trực tiếp từ doc `File` (`file_doc.get_full_path()`).
- **Ý nghĩa kiểm chứng:** Xác nhận logic nghiệp vụ tạo bản ghi `CSKH Export Job`, cấu trúc các cột dữ liệu CSV (`metric_code`, `label_vi`, `value`, `numerator`, `denominator`, `unit`, `status`, `reason`), tính bảo mật của tệp khi lưu trữ trên đĩa (`is_private = 1`, `owner = 'Administrator'`).
- **Hiện vật:** `scripts/verify_bod_csv_export.py`, `raw_results/bod_executive_overview_export.csv`.

### 4.2 Bằng chứng tải qua đường truyền HTTP có xác thực (Authenticated HTTP Transport Verification)
- **Phương thức thực hiện:** Sử dụng phiên HTTP client gửi cookie phiên `sid` thu được sau khi đăng nhập tài khoản BOD `cskh-p3-bod@example.test`.
  1. `POST /api/method/crm_cskh.api.p3_reporting.request_export` (HTTP 200, trả về mã tiến trình `EXP-BE8FDEFBDD98`).
  2. Background worker xử lý bất đồng bộ từ hàng đợi Redis (`status: completed`).
  3. `GET /api/method/crm_cskh.api.p3_reporting.download_export?export_id=EXP-BE8FDEFBDD98`.
- **Thông số phản hồi HTTP:**
  - **HTTP Status Code:** `200 OK`
  - **Content-Type:** `text/csv; charset=utf-8; charset=utf-8`
  - **Content-Disposition:** `attachment; filename=EXP-BE8FDEFBDD98.csv`
  - **Kích thước nhận:** `3018 bytes`
  - **SHA-256 tệp nhận được:** `92bb90d5be2b924edd36851c381d130345a9cb0c1bbb59509bd7a2c7275b75c0`
  - **Số dòng dữ liệu:** 30 dòng (1 dòng tiêu đề + 29 dòng chỉ số tuyển dụng chuẩn).
  - **Zero PII:** 0 số điện thoại khách hàng, 0 email, 0 danh tính cá nhân bị lộ lọt.
- **Kiểm tra 4 cổng từ chối bảo mật qua giao thức HTTP:**
  - **Cổng 1 (BOD yêu cầu PII):** Gửi `include_pii=1` -> Bị từ chối **`HTTP 403 FORBIDDEN_SCOPE`** (`"BOD Viewer không được phép xuất báo cáo chứa dữ liệu định danh khách hàng (PII)."`).
  - **Cổng 2 (BOD yêu cầu bảng điểm cá nhân):** Gửi `report_type="my_scorecard"` -> Bị từ chối **`HTTP 403 FORBIDDEN_SCOPE`** (`"BOD Viewer không có bảng điểm cá nhân để xuất."`).
  - **Cổng 3 (Nhân viên tải trộm bản xuất của BOD):** Tài khoản `cskh-p3-staff-a@example.test` gọi endpoint tải của BOD -> Bị từ chối **`HTTP 403 FORBIDDEN_SCOPE`** (`"Bạn không có quyền tải tệp xuất báo cáo của người khác."`).
  - **Cổng 4 (Người dùng chưa xác thực tải tệp):** Phiên Guest không cookie -> Bị từ chối **`HTTP 403 PermissionError`**.
- **Hiện vật:** `scripts/verify_bod_http_csv_download.py`, `raw_results/bod_http_downloaded_export.csv`, `raw_results/bod_http_export_verification.json`.
