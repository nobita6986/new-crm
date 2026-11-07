# Gói bằng chứng kiểm định độc lập T1C — Bản Việt hóa tuyển dụng & P3 Parity

Thư mục này lưu trữ toàn bộ bằng chứng kiểm định độc lập của **T1C** trên mã nguồn SHA **`6912c78c1c74483c1a0063379d94d8c9b8bdb30b`** (kết hợp với bằng chứng đóng gói tại commit `14960eb`).

---

## 1. Thành phần gói bằng chứng

- **`scripts/`**:
  - `run_t1c_multirole_browser_audit.mjs`: Script Playwright Chromium kiểm thử tự động 3 vai trò (Nhân viên, Quản trị CRM, BOD), ghi nhận 12 ảnh chụp màn hình và kiểm tra các cổng bảo mật (403 ngoài phạm vi, Zero PII, nhãn Giao dịch CRM).
  - `verify_bod_csv_export.py`: Script kiểm định độc lập luồng xuất báo cáo CSV bằng tài khoản BOD, đối soát 29 dòng chỉ số với tên tiếng Việt chuẩn, kiểm tra Zero PII và các cổng chặn `ForbiddenScope` (chặn xuất PII và chặn xuất `my_scorecard`).
  - `verify_migrate_x2_and_kpi.py`: Script kiểm tra tính lũy đẳng sau `migrate × 2`, bảo tồn nhãn do Admin tùy chỉnh, bảo tồn `outcome_label_snapshot` và tính bất biến của dữ liệu thô `metrics_json` trong CSDL.
- **`raw_results/`**:
  - `multirole_browser_results.json`: Kết quả thực thi chi tiết của 3 vai trò trên trình duyệt.
  - `bod_executive_overview_export.csv`: Tệp CSV thật tải về từ hệ thống bằng tài khoản `cskh-p3-bod@example.test`, đối chiếu 29 dòng chỉ số chuẩn tiếng Việt và không rò rỉ dữ liệu nhạy cảm.
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

## 4. Kiểm định luồng xuất CSV của BOD

- **Kiểm tra hộp thoại (Dialog check):**
  - Nút xuất dữ liệu mở hộp thoại bắt buộc nhập mục đích sử dụng dữ liệu.
  - Bảng tác vụ hiển thị tiến trình hàng đợi an toàn.
- **Kiểm tra nội dung tệp (File content check):**
  - Tệp thật `bod_executive_overview_export.csv` có đúng 29 dòng chỉ số.
  - Cột `label_vi` khớp 100% với tên gọi trên thẻ KPI và bảng chi tiết.
  - **Zero PII**: Hoàn toàn không có số điện thoại 10 chữ số thô hay định danh cá nhân của ứng viên.
  - Cố tình yêu cầu kèm PII (`include_pii=1`) hoặc tải `my_scorecard` bị từ chối bằng `ForbiddenScope`.
