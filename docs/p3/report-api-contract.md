# Hợp Đồng API Báo Cáo, SLA, Drill-down & Xuất Dữ Liệu P3 (`docs/p3/report-api-contract.md`)

Tài liệu này quy định hợp đồng API giữa **T1B (Backend)** và **T1A (Frontend UI)** cho Phase P3 (`SLA, KPI, QA & BOD Quality Dashboard`).

- **Namespace chính:** `crm_cskh.api.p3_reporting.*` (kèm alias tương thích trên `crm_cskh.api.reporting.*`), `crm_cskh.api.sla.*`, `crm_cskh.api.qa.*`.
- **Giao thức:** Frappe RPC (`/api/method/<dotted.path>`), hỗ trợ `GET` cho truy vấn đọc và `POST` (kèm `X-Frappe-CSRF-Token` khi dùng cookie session) cho tác vụ ghi / recompute / export.
- **Phiên bản công thức:** `"cskh-metrics-v2"`.
- **Múi giờ chuẩn:** `"Asia/Ho_Chi_Minh"`.

---

## 1. Quy ước Chung về Request, Envelope Metadata & Phân Trang

### 1.1 Tham số lọc chuẩn
- `period_type`: `"daily" | "weekly" | "monthly" | "quarterly" | "yearly" | "custom"` (mặc định `"monthly"`).
- `period_key`: `"YYYY-MM-DD"` | `"YYYY-Www"` | `"YYYY-MM"` | `"YYYY-Qq"` | `"YYYY"`.
- `from_date`, `to_date`: bắt buộc khi `period_type == "custom"` (`from_date <= to_date`, tối đa `366` ngày; vi phạm trả `HTTP 400 INVALID_PERIOD`).
- `campaign`: mã `CSKH Campaign` *(tùy chọn; kiểm tra quyền phạm vi của Leader)*.
- `team`: mã node `CRM Sales Hierarchy` *(tùy chọn; tự động mở rộng toàn bộ cây con hợp lệ `_subtree_nodes(team)` trong phạm vi người gọi)*.
- `lead_source`: nguồn khách hàng từ `CRM Lead` *(tùy chọn; áp dụng đồng nhất trên tất cả endpoint báo cáo và drill-down)*.
- `page` (`>= 1`, mặc định `1`), `page_size` (`1..100`, mặc định `20`; tối đa `200` cho `sla.list_obligations`). Giá trị sai kiểu trả `HTTP 400 INVALID_REQUEST`.

### 1.2 Cấu trúc Ô Chỉ Số (`MetricValue`) & Thang Điểm `score_components` (`F-P3-09`)
- Với chỉ số tỷ lệ (`unit: "percent"`): `{metric_code, label_vi, value, numerator, denominator, unit: "percent", status, reason, informational_only, drilldown_supported}`. Khi `denominator == 0`: `value: null`, `status: "insufficient_data"`, `reason: "no_eligible_records"`.
- Với chỉ số đếm (`unit: "count"`): `value: <int>`, `numerator: <int>`, **`denominator: null`** (không bao giờ trả `denominator: 1`).
- Với `PROCESS_SCORE` (`unit: "score_100"`): trả kèm `valid_weight`, `total_configured_weight`, `raw_weight_rate_product`, `missing_components`, `label_display_vi`, và mảng `score_components[]` trên thang `0–100`:
  ```json
  {
    "component_code": "C1_FIRST_ATTEMPT_SLA",
    "metric_code": "FIRST_ATTEMPT_SLA_RATE",
    "weight": 25,
    "rate_value": 90.0,
    "status": "ok",
    "included_in_score": true,
    "weighted_points": 22.5,
    "reweighted_points": 26.47,
    "raw_weight_rate_product": 2250.0,
    "scale": "0_100"
  }
  ```

---

## 2. Danh Mục Endpoint Chi Tiết

### 2.1 `GET crm_cskh.api.p3_reporting.get_filter_options` *(Mới — `F-P3-10`)*
Trả danh mục bộ lọc theo đúng phạm vi quyền của actor (`0` lộ đối tượng ngoài team cho Leader/Staff, `0` PII cho BOD):
- `campaigns`: `[{campaign_id, campaign_name, status}]`
- `teams`: `[{team_id, team_name, parent_team}]`
- `employees`: `[{employee_id, employee_name, team_id}]`
- `lead_sources`: `[string]`

### 2.2 `GET crm_cskh.api.p3_reporting.get_executive_overview`
- **Params:** Bộ lọc kỳ + `campaign`, `team`, `lead_source`.
- **Response bổ sung (`F-P3-10`):**
  - `metrics`: đầy đủ chỉ số `cskh-metrics-v2` (`ASSIGNED_INTERVALS`, `UNIQUE_ASSIGNED_ITEMS`, `CONTACT_ATTEMPT_COUNT`, `CONNECTED_ATTEMPT_COUNT`, `CONNECTED_ITEMS`, `WORK_ITEMS_TOUCHED`, `ELIGIBLE_COVERAGE`, `CONNECTED_RATE`, `CONVERSION_RATE`, `FIRST_ATTEMPT_SLA_RATE`, `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `CALLBACK_COMPLETION_RATE`, `RESCHEDULE_RATE`, `FIRST_CONNECTED_TRACKING_RATE`, `MIN_ATTEMPTS_COMPLIANCE_RATE`, `OVERDUE_BACKLOG`, `OPEN_FOLLOWUPS_OVERDUE`, `SLA_BREACH_COUNT`, `SLA_WAIVER_COUNT`, `FIRST_ATTEMPT_LATENCY_P50_MINUTES`, `FIRST_ATTEMPT_LATENCY_P90_MINUTES`, `VALID_OUTCOME_LOGGING_RATE`, `VERIFICATION_RATE`, `CORRECTION_RATE`, `VOID_ATTEMPT_RATE`, `DNC_COMPLIANCE_RATE`, `QA_PASS_RATE`, `PROCESS_SCORE`) + alias `first_touch_on_time_rate`.
  - `teams_summary`: danh sách tóm tắt các team được phép kèm `overdue_work_item_backlog`, `breach_count`, `process_score`, `first_touch_on_time_rate`, `connected_rate`.
  - `alerts`: cảnh báo điều hành tự động (`overdue_backlog`, `open_followups_overdue`, `high_correction_or_void_rate`, `historical_attribution_unknown`) không chứa PII.
  - `trend_series`: chuỗi xu hướng theo ngày trong kỳ.

### 2.3 `GET crm_cskh.api.p3_reporting.get_team_comparison`
- **Params:** Bộ lọc kỳ + `campaign`, `lead_source`, `sort_by`, `sort_dir`, `page`, `page_size`.
- **Response `rows[]`:** `team_id`, `team_name`, `is_unattributed_bucket`, `active_staff_count`, `overdue_work_item_backlog` (`OVERDUE_BACKLOG` của team), `ownership_intervals_count`, `distinct_work_items`, `attempt_breakdown`, `metrics`.

### 2.4 `GET crm_cskh.api.p3_reporting.get_employee_quality` (`F-P3-04`)
- **Params:** Bộ lọc kỳ + `team`, `employee`, `campaign`, `lead_source`, `sort_by`, `sort_dir`, `page`, `page_size`.
- **Cô lập `team-as-of` cho Leader:**
  - `BOD` / `Admin`: đọc snapshot tổng toàn kỳ của từng nhân viên (`scope_type = "employee"`).
  - `Team Leader`: hệ thống tự động tổng hợp từ lát cắt lịch sử **`employee_team` (`user::team_node`)** thuộc `allowed_nodes` của Leader. Khi nhân viên `staff.a3` chuyển từ `Team A` sang `Team B` giữa kỳ:
    - `Leader A` xem được đúng phần KPI giai đoạn `Team A` của `staff.a3` (`0` lộ dữ liệu giai đoạn `Team B`);
    - `Leader B` chỉ xem được đúng phần KPI giai đoạn `Team B` của `staff.a3` (`0` lộ dữ liệu giai đoạn `Team A`).
    - Nếu nhân viên nằm ngoài cây hiện tại **và** có `0` hoạt động lịch sử trong cây của Leader -> trả `403 FORBIDDEN_SCOPE`.

### 2.5 `GET crm_cskh.api.p3_reporting.get_campaign_quality`
- **Params:** Bộ lọc kỳ + `campaign`, `team`, `lead_source`, `sort_by`, `sort_dir`, `page`, `page_size`.
- **Response `rows[]`:** `campaign_id`, `campaign_name`, `lead_source`, `distinct_work_items`, `ownership_intervals_count`, `campaign_conversion_rate`, `attempt_breakdown`, `metrics`.

### 2.6 `GET crm_cskh.api.p3_reporting.get_my_scorecard`
- **Params:** Bộ lọc kỳ + `campaign`, `lead_source`. Cố định `employee = frappe.session.user`.
- **Response:** `metrics`, `score_components` (thang `0–100`), `attempt_breakdown`, `active_obligations` (kèm `campaign_name`, chỉ gồm các Work Item đang thuộc quyền sở hữu hoạt động của chính nhân viên).

### 2.7 `GET crm_cskh.api.p3_reporting.get_metric_drilldown` (`F-P3-04`, `F-P3-10`)
- **Params:** `metric_code`, `period_type`, `period_key`, `from_date`, `to_date`, `scope_type`, `scope_id`, `filter_bucket` (`all_denominator | numerator_only | breached_only | excluded`), `campaign`, `lead_source`, `team`, `employee`, `search`, `sort_by`, `sort_dir`, `include_pii`, `page`, `page_size`.
- **Hỗ trợ toàn bộ 21 mã `DRILLDOWN_SUPPORTED_METRICS`** khớp `100%` với tử số/mẫu số của `cskh-metrics-v2`.
- **Bảo vệ quyền & chống Filter-Oracle:**
  - Lọc cứng `resolved_team in allowed_nodes` cho Leader ngay cả khi `scope_type == "employee"` hoặc `"campaign"`.
  - Thực thi che PII (`customer_name`, `phone`, `note_excerpt`, `lead`) **trước** bước `search` và `sort_by` (ngăn dò tên/SĐT khách đã đóng hoặc đã chuyển sang đội khác qua từ khóa `search`), đồng thời trả `team_name` và `campaign_name` cho các dòng hợp lệ.

### 2.8 Xuất Báo Cáo & Điều Khiển Recompute
- `POST request_export`, `GET get_export_status`, `GET download_export`: khóa quyền sống 3 chặng (`enqueue`, `worker`, `download` + `/private/files/...` + `/api/resource/File`).
- `POST request_recompute`, `GET get_run_status`: `Leader` chỉ được xem/kích hoạt run thuộc đúng scope `team` trong cây quản lý; truy cập run `company`/`all` trả `403 FORBIDDEN_SCOPE` (`F-P3-05`).

### 2.9 Nhóm API SLA (`crm_cskh.api.sla.*`) & QA (`crm_cskh.api.qa.*`)
- `GET sla.list_obligations`: hỗ trợ lọc phía server theo `work_item`, `status`, `obligation_type`, `campaign`, `team`, `owner_user`, `overdue_only`, `due_from`, `due_to`, `search`, `sort_by`, `sort_dir`, `page`, `page_size` (trả kèm `team_name`, `campaign_name`, áp quyền trước phân trang/đếm).
- `POST qa.create_sample`, `POST qa.create_review`, `POST qa.submit_review`, `GET qa.list_reviews`:
  - Kiểm tra cờ `qa_enabled` **trước** khi kiểm tra tham số bắt buộc: khi `qa_enabled = false`, gọi thiếu tham số vẫn trả sạch **`409 QA_DISABLED`**; khi `qa_enabled = true` và thiếu tham số bắt buộc, trả **`400 INVALID_REQUEST`** (`0` lỗi `500 TypeError`).
  - Hỗ trợ `cohort_type` (`"random_sample" | "risk_targeted"`).
