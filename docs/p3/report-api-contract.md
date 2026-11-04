# Hợp Đồng API Báo Cáo, SLA, Drill-down & Xuất Dữ Liệu P3 (`docs/p3/report-api-contract.md`)

Tài liệu này là **hợp đồng API đóng băng sớm (Frozen Early Contract)** giữa **T1B (Backend)** và **T1A (Frontend UI)** cho Phase P3 (`SLA, KPI, QA & BOD Quality Dashboard`).

- **Namespace chính:** `crm_cskh.api.p3_reporting.*` (đồng thời expose alias tương thích trên `crm_cskh.api.reporting.*` cho các hàm báo cáo P3 để T1A gọi thống nhất), `crm_cskh.api.sla.*`, `crm_cskh.api.qa.*`.
- **Giao thức:** Frappe RPC (`/api/method/<dotted.path>`), hỗ trợ `GET` cho truy vấn đọc và `POST` (`application/json` hoặc `application/x-www-form-urlencoded`, bắt buộc kèm `X-Frappe-CSRF-Token` khi dùng cookie session) cho tác vụ thay đổi trạng thái / yêu cầu recompute / yêu cầu export.
- **Phiên bản công thức:** `"cskh-metrics-v1"`.
- **Múi giờ chuẩn:** `"Asia/Ho_Chi_Minh"`.

---

## 1. Quy ước Chung về Request, Envelope Metadata & Phân Trang

### 1.1 Tham số lọc kỳ thời gian chuẩn (`Period Filter`)
Tất cả endpoint báo cáo nhận bộ tham số kỳ thống nhất:
- `period_type`: `"daily" | "weekly" | "monthly" | "quarterly" | "yearly" | "custom"` (mặc định `"monthly"`).
- `period_key` *(tùy chọn khi dùng kỳ chuẩn)*:
  - `daily`: `"YYYY-MM-DD"` (ví dụ `"2026-11-04"`)
  - `weekly`: `"YYYY-Www"` theo chuẩn ISO-8601 Thứ Hai–Chủ Nhật (ví dụ `"2026-W45"`)
  - `monthly`: `"YYYY-MM"` (ví dụ `"2026-11"`)
  - `quarterly`: `"YYYY-Qq"` (ví dụ `"2026-Q4"`)
  - `yearly`: `"YYYY"` (ví dụ `"2026"`)
- `from_date`, `to_date` *(bắt buộc khi `period_type == "custom"`, định dạng `"YYYY-MM-DD"`, giới hạn `from_date <= to_date` và tối đa `366` ngày)*: nếu truyền sai định dạng hoặc `from_date > to_date`, trả `HTTP 400` với mã lỗi `"INVALID_PERIOD"`.
- `campaign`: mã `CSKH Campaign` *(tùy chọn)*.
- `team`: mã node `CRM Sales Hierarchy` *(tùy chọn; tự động kiểm tra quyền phạm vi của người gọi)*.

### 1.2 Tham số phân trang chuẩn (`Pagination`)
- `page`: số nguyên `>= 1` (mặc định `1`).
- `page_size`: số nguyên `1..100` (mặc định `20`).
- Giá trị không phải số nguyên, `<= 0` hoặc vượt ngưỡng không hợp lệ trả `HTTP 400` (`"INVALID_REQUEST"`), không bao giờ sinh `HTTP 500`.

### 1.3 Cấu trúc Envelope trả về chuẩn (`message`)
Mọi API đọc báo cáo P3 trả trong `{"message": { ... }}` cấu trúc metadata nhất quán để UI hiển thị trạng thái độ tươi dữ liệu và cảnh báo chính sách:

```json
{
  "ok": true,
  "actor_scope": {
    "user": "bod.viewer@dev.local",
    "role_profile": "bod",
    "pii_allowed": false,
    "qa_enabled": false,
    "p3_sla_enabled": true
  },
  "period": {
    "period_type": "monthly",
    "period_key": "2026-11",
    "from_date": "2026-11-01",
    "to_date": "2026-11-30",
    "timezone": "Asia/Ho_Chi_Minh"
  },
  "freshness": {
    "run_id": "KPI-RUN-20261104-00012",
    "run_mode": "incremental",
    "snapshot_status": "fresh",
    "as_of": "2026-11-04T16:00:00+07:00",
    "source_watermark": "2026-11-04T15:59:58+07:00",
    "is_restated": false,
    "restated_from_run": null,
    "dirty_pending": false,
    "metric_version": "cskh-metrics-v1"
  },
  "policy_notice": {
    "config_stage": "DEV_CONFIGURABLE",
    "production_approved": false,
    "message_vi": "Ngưỡng SLA và trọng số điểm hiện là cấu hình DEV/configurable, chưa phải chính sách production do BOD phê duyệt."
  },
  "exclusion_counts": {
    "historical_attribution_unknown": 0,
    "waived_obligations": 1,
    "canceled_validly_dnc": 2,
    "voided_attempts": 1
  },
  "metrics": {},
  "rows": [],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 0,
    "total_pages": 0
  }
}
```

**Các giá trị của `freshness.snapshot_status`:**
- `"fresh"`: Snapshot đã publish và không có `CSKH Dirty Scope` đang chờ cho phạm vi này.
- `"stale_recomputing"`: Đã có dữ liệu phát sinh mới sau `as_of` và đang chờ/chạy recompute nền; UI vẫn hiển thị số liệu của `as_of` kèm huy hiệu *"Đang cập nhật dữ liệu mới..."*.
- `"empty_not_computed"`: Kỳ chưa có snapshot nào được tính (hoặc vừa khởi tạo hệ thống, hệ thống tự động kích hoạt recompute nhẹ cho kỳ nhỏ hoặc trả trạng thái khởi tạo rõ ràng).

### 1.4 Cấu trúc chuẩn của một Ô Chỉ Số (`MetricValue`)
```json
{
  "metric_code": "FIRST_ATTEMPT_SLA_RATE",
  "label_vi": "Tỷ lệ đạt SLA liên hệ lần đầu",
  "value": 90.0,
  "numerator": 18,
  "denominator": 20,
  "unit": "percent",
  "status": "ok",
  "reason": null,
  "informational_only": false,
  "drilldown_supported": true
}
```
- Khi `denominator == 0`: `value: null`, `status: "insufficient_data"`, `reason: "no_eligible_records"`.
- Với `QA_PASS_RATE` khi `qa_enabled == false`: `value: null`, `status: "disabled"`, `reason: "qa_disabled_by_policy"`.
- Với `PROCESS_SCORE` khi `qa_enabled == false` (ví dụ `7350/85`): `value: 86.47`, `numerator: 7350.0`, `denominator: 85.0`, `unit: "score_100"`, `status: "provisional"`, `reason: "qa_component_excluded_reweighted"`, `valid_weight: 85`, `missing_components: ["C5_QA_QUALITY"]`.

---

## 2. Ma Trận Quyền Gọi API Theo Vai Trò (`Capability Matrix`)

| Endpoint | `Staff` | `Team Leader` | `BOD Viewer` | `CRM Admin` | Ghi chú phân quyền cốt lõi |
|---|:---:|:---:|:---:|:---:|---|
| `get_executive_overview` | `403` | Có *(chỉ scope team mình)* | Có *(toàn công ty, `0` PII)* | Có *(toàn công ty)* | BOD xem tổng hợp công ty mà không cần `System Manager`. |
| `get_team_comparison` | `403` | Có *(chỉ các node thuộc cây mình)* | Có *(toàn bộ các team, `0` PII khách hàng)* | Có *(toàn bộ các team)* | Có dòng `__unattributed_historical_team__` nếu tồn tại dữ liệu lịch sử trước P3. |
| `get_employee_quality` | Có *(chỉ `employee == self`)* | Có *(chỉ nhân sự thuộc cây mình)* | Có *(chỉ số hiệu suất nhân viên tổng hợp, `0` PII khách hàng)* | Có *(toàn bộ)* | Staff truyền `employee != self` -> `403 FORBIDDEN_SCOPE`. |
| `get_campaign_quality` | `403` | Có *(phạm vi dữ liệu team mình trong chiến dịch)* | Có *(tổng hợp toàn chiến dịch, `0` PII)* | Có *(toàn chiến dịch)* | Phân biệt rõ `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` (`DISTINCT Work Item`) với interval nhân viên. |
| `get_my_scorecard` | Có *(chính mình)* | Có *(chính mình)* | `403` *(BOD không phải nhân sự tác nghiệp)* | Có *(chính mình)* | Không bao giờ trả PII khách hàng của các Lead đã chuyển giao sang người khác. |
| `get_metric_drilldown` | Có *(chỉ sự kiện của mình; ẩn PII nếu Lead đã chuyển giao)* | Có *(chỉ sự kiện thuộc scope team được phép; ẩn PII nếu Lead đã rời team)* | **Từ chối PII** *(chỉ trả phân phối ẩn danh không định danh khách hàng hoặc `403` nếu yêu cầu PII)* | Có *(đầy đủ)* | Hỗ trợ tối thiểu 6 KPI cốt lõi khớp đúng tập mẫu số/tử số của snapshot. |
| `request_export` / `get_export_status` / `download_export` | Có *(chỉ scorecard tổng hợp của bản thân, `0` PII khách hàng)* | Có *(báo cáo/drill-down trong phạm vi team mình)* | Có *(chỉ báo cáo aggregate `0` PII)* | Có *(theo phạm vi quản trị)* | Revalidate quyền sống ở cả 3 bước: `enqueue`, `worker`, `download` + khóa `/private/files/...`. |
| `request_recompute` / `get_run_status` | `403` | Có *(get_run_status; recompute giới hạn scope team)* | Có *(get_run_status)* | Có *(toàn quyền recompute / backfill)* | Chặn chạy trùng cùng scope bằng Redis/DB lock (`RECOMPUTE_IN_PROGRESS`). |
| `sla.list_obligations` / `sla.get_obligation_detail` | Có *(chỉ nghĩa vụ hiện hành của mình)* | Có *(scope team)* | `403` *(tránh lộ chi tiết hồ sơ khách hàng)* | Có *(toàn bộ)* | Phục vụ hiển thị đồng hồ đếm ngược SLA trên màn hình tác nghiệp. |
| `sla.waive_obligation` / `sla.pause_obligation` / `sla.resume_obligation` | `403` | Có *(khi policy cho phép & trong team mình, bắt buộc `reason`)* | `403` | Có *(bắt buộc `reason`)* | Ghi vết bất biến vào `audit_events_json` và đánh dấu dirty scope hậu commit. |
| `qa.list_reviews` / `qa.create_sample` / `qa.submit_review` | Đọc kết quả của mình (`0` ghi) | Đọc/ghi trong team khi `qa_enabled=1` (`reviewer != staff`) | Đọc aggregate (`0` ghi) | Quản trị khi `qa_enabled=1` (`reviewer != staff`) | Mặc định `qa_enabled=false`: API ghi trả `409 QA_DISABLED`. |

---

## 3. Chi Tiết Các Endpoint Báo Cáo & Vận Hành

### 3.1 `crm_cskh.api.p3_reporting.get_executive_overview`
*(Alias: `crm_cskh.api.reporting.get_executive_overview`)*
- **Query params:** `period_type`, `period_key`, `from_date`, `to_date`, `campaign` *(optional)*, `team` *(optional)*.
- **Response `metrics`:**
  - `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `FIRST_CONNECTED_TRACKING_RATE` (`informational_only: true`), `MIN_ATTEMPTS_COMPLIANCE_RATE`, `CONTACT_ATTEMPT_COUNT` (kèm `attempt_breakdown: {attempted, connected, self_reported, verified, disputed}`), `CONNECTED_ATTEMPT_COUNT`, `CONNECTED_RATE`, `WORK_ITEMS_TOUCHED`, `VALID_OUTCOME_LOGGING_RATE`, `CORRECTION_RATE`, `VOID_ATTEMPT_RATE`, `CALLBACK_COMPLETION_RATE`, `RESCHEDULE_RATE`, `DNC_COMPLIANCE_RATE`, `QA_PASS_RATE`, `PROCESS_SCORE`.
- **Response `trend_series`:** Chuỗi theo ngày/tuần trong kỳ để vẽ biểu đồ xu hướng mà không cần gọi N request con.

### 3.2 `crm_cskh.api.p3_reporting.get_team_comparison`
*(Alias: `crm_cskh.api.reporting.get_team_comparison`)*
- **Query params:** Bộ lọc kỳ + `campaign` *(optional)* + `sort_by` (mặc định `"PROCESS_SCORE"`), `sort_dir` (`"asc" | "desc"`), `page`, `page_size`.
- **Response `rows[]`:** Mỗi phần tử đại diện cho 1 Team:
  ```json
  {
    "team_id": "HIER-TEAM-1",
    "team_name": "Nhóm CSKH 1",
    "is_unattributed_bucket": false,
    "active_staff_count": 2,
    "metrics": {
      "FIRST_ATTEMPT_SLA_RATE": { "...MetricValue..." },
      "FOLLOWUP_SLA_RATE": { "...MetricValue..." },
      "CONNECTED_RATE": { "...MetricValue..." },
      "VALID_OUTCOME_LOGGING_RATE": { "...MetricValue..." },
      "CALLBACK_COMPLETION_RATE": { "...MetricValue..." },
      "DNC_COMPLIANCE_RATE": { "...MetricValue..." },
      "QA_PASS_RATE": { "...MetricValue..." },
      "PROCESS_SCORE": { "...MetricValue..." }
    }
  }
  ```

### 3.3 `crm_cskh.api.p3_reporting.get_employee_quality`
*(Alias: `crm_cskh.api.reporting.get_employee_quality`)*
- **Query params:** Bộ lọc kỳ + `team` *(optional)* + `employee` *(optional)* + `campaign` *(optional)* + `page`, `page_size`.
- **Response `rows[]`:** Từng nhân viên trong phạm vi được phép (họ tên nhân viên là thông tin tổ chức nội bộ được phép hiển thị cho BOD/Leader/Admin; **tuyệt đối `0` trường PII khách hàng**):
  - `employee_id`, `employee_name`, `team_id`, `team_name`, `intervals_count`, `work_items_touched`, `attempt_breakdown`, và dictionary `metrics` đầy đủ (`FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `CONNECTED_RATE`, `VALID_OUTCOME_LOGGING_RATE`, `CALLBACK_COMPLETION_RATE`, `DNC_COMPLIANCE_RATE`, `QA_PASS_RATE`, `PROCESS_SCORE`).

### 3.4 `crm_cskh.api.p3_reporting.get_campaign_quality`
*(Alias: `crm_cskh.api.reporting.get_campaign_quality`)*
- **Query params:** Bộ lọc kỳ + `campaign` *(optional)* + `team` *(optional)* + `page`, `page_size`.
- **Response `rows[]`:** Mỗi chiến dịch gồm `campaign_id`, `campaign_name`, `distinct_work_items`, `ownership_intervals_count` (thể hiện tường minh sự khác biệt giữa `DISTINCT Work Item` của chiến dịch và tổng `ownership interval` khi có chuyển giao `A -> B -> A`), cùng bộ `metrics`.

### 3.5 `crm_cskh.api.p3_reporting.get_my_scorecard`
*(Alias: `crm_cskh.api.reporting.get_my_scorecard`)*
- **Query params:** Bộ lọc kỳ. Luôn cố định `employee = frappe.session.user` (không nhận tham số xem hộ người khác).
- **Response:** Bộ `metrics` cá nhân của kỳ hiện tại, phân rã `score_components` của `PROCESS_SCORE` (trọng số, giá trị, trạng thái `provisional` / `insufficient_data`), và danh sách tóm tắt các nghĩa vụ SLA **đang mở hiện hành** do chính nhân viên đang sở hữu (`active_obligations` — chỉ các Work Item đang thuộc quyền sở hữu hiện tại của nhân viên đó).

### 3.6 `crm_cskh.api.p3_reporting.get_metric_drilldown`
*(Alias: `crm_cskh.api.reporting.get_metric_drilldown`)*
- **Query params:**
  - `metric_code` *(bắt buộc)*: hỗ trợ đầy đủ tối thiểu 7 mã:
    1. `"FIRST_ATTEMPT_SLA_RATE"` / `"WORK_ITEM_FIRST_ATTEMPT_SLA_RATE"`
    2. `"FOLLOWUP_SLA_RATE"`
    3. `"FIRST_CONNECTED_TRACKING_RATE"`
    4. `"CONTACT_ATTEMPT_COUNT"` / `"CONNECTED_RATE"`
    5. `"VALID_OUTCOME_LOGGING_RATE"` / `"CORRECTION_RATE"`
    6. `"CALLBACK_COMPLETION_RATE"`
    7. `"DNC_COMPLIANCE_RATE"`
  - `scope_type`: `"company" | "team" | "employee" | "campaign"`
  - `scope_id`: mã đối tượng tương ứng (trống nếu `"company"`)
  - `filter_bucket`: `"all_denominator" | "numerator_only" | "breached_only" | "excluded"` (mặc định `"all_denominator"`)
  - Bộ lọc kỳ + `page`, `page_size`.
- **Quy tắc ẩn PII trên dòng drill-down (`Privacy Projection`):**
  - `BOD Viewer`: Chỉ nhận dữ liệu tổng hợp theo nhóm/khung giờ hoặc dòng sự kiện đã ẩn danh hoàn toàn (`lead_ref: "REDACTED"`, `customer_name: null`, `phone: null`, `note: null`). Nếu truyền `include_pii=1` -> trả `403 FORBIDDEN_SCOPE`.
  - `Staff` / `Team Leader`: Nếu bản ghi lịch sử thuộc về một Lead mà tại thời điểm gọi API người dùng **không còn quyền đọc trực tiếp** (ví dụ Lead đã chuyển sang nhân viên/team khác), các trường `lead`, `customer_name`, `phone`, `note_excerpt` tự động bị che thành `null` với cờ `"pii_redacted_due_to_transfer": true`, chỉ giữ thông tin kỹ thuật của sự kiện SLA/Attempt (`obligation_id`, `obligation_type`, `started_at`, `original_due_at`, `effective_due_at`, `fulfilled_at`, `status`).

### 3.7 Xuất Báo Cáo Bất Đồng Bộ (`request_export`, `get_export_status`, `download_export`)
1. **`POST crm_cskh.api.p3_reporting.request_export`:**
   - Body: `{"report_type": "executive_overview" | "team_comparison" | "employee_quality" | "campaign_quality" | "my_scorecard" | "metric_drilldown", "period_type": "...", "period_key": "...", "from_date": "...", "to_date": "...", "scope_type": "...", "scope_id": "...", "metric_code": "...", "format": "csv"}`.
   - Kiểm tra quyền lần 1 ngay tại thời điểm enqueue; nếu vượt phạm vi trả `403 FORBIDDEN_SCOPE` ngay lập tức.
   - Trả về: `{"export_id": "EXP-00001", "status": "queued", "expires_in_seconds": 3600}`.
2. **`GET crm_cskh.api.p3_reporting.get_export_status`:**
   - Params: `export_id`. Chỉ `requested_by` (cùng quyền hợp lệ) hoặc `CRM Admin` xem được; người khác nhận `403`/`404`.
   - Trả về: `{"export_id": "EXP-00001", "status": "queued | running | completed | failed | expired | revoked", "row_count": 42, "expires_at": "...", "download_endpoint": "/api/method/crm_cskh.api.p3_reporting.download_export?export_id=EXP-00001"}` (không khuyến khích UI dùng URL `/private/files/...`, dù URL `/private/files/...` cũng đã được khóa bằng `File.owner="Administrator"` + `CSKH Export Job.has_permission`).
3. **`GET crm_cskh.api.p3_reporting.download_export`:**
   - Params: `export_id`.
   - Kiểm tra quyền sống lần 3 (vai trò hiện tại, cây team hiện tại, trạng thái `completed`, `now < expires_at`). Nếu đã hết hạn -> `410` / `403` với mã `"EXPORT_EXPIRED"`; nếu đã bị thu hồi quyền/đổi team -> `403 FORBIDDEN_SCOPE`.
   - Chống CSV Formula Injection: Mọi ô chuỗi bắt đầu bằng `=`, `+`, `-`, `@`, `\t`, `\r`, `\n` đều được tiền tố dấu nháy đơn `'` trước khi ghi file.

### 3.8 Điều Khiển Recompute & Xem Trạng Thái Run (`request_recompute`, `get_run_status`)
1. **`POST crm_cskh.api.p3_reporting.request_recompute`:**
   - Body: `{"period_type": "...", "period_key": "...", "from_date": "...", "to_date": "...", "scope_type": "...", "scope_id": "...", "reason": "..."}`.
   - Nếu đang có job chạy trùng phạm vi -> trả `409 RECOMPUTE_IN_PROGRESS`.
2. **`GET crm_cskh.api.p3_reporting.get_run_status`:**
   - Params: `run_id` (kiểm tra quyền xem run theo scope; Staff không dò được `run_id` của team/công ty).

### 3.9 Nhóm API SLA Vận Hành (`crm_cskh.api.sla.*`)
- `GET crm_cskh.api.sla.list_obligations(work_item=None, status=None, obligation_type=None, page=1, page_size=20)`
- `GET crm_cskh.api.sla.get_obligation_detail(obligation_id)`
- `POST crm_cskh.api.sla.waive_obligation(obligation_id, reason)` *(chỉ Leader được phép theo policy hoặc CRM Admin)*
- `POST crm_cskh.api.sla.pause_obligation(obligation_id, reason)` *(chỉ Leader được phép theo policy hoặc CRM Admin)*
- `POST crm_cskh.api.sla.resume_obligation(obligation_id, reason=None)`

---

## 4. Bảng Mã Lỗi Chuẩn Hóa P3 (`Error Codes`)

Mọi lỗi nghiệp vụ trả JSON chuẩn qua `crm_cskh/p1_errors.py` (`{"code": "<CODE>", "p1_code": "<CODE>", "message": "<Thông báo tiếng Việt không chứa PII>"}`):

| Mã lỗi (`code`) | HTTP Status | Tình huống kích hoạt |
|---|---:|---|
| `FORBIDDEN_SCOPE` | `403` | Truy cập kỳ/team/nhân viên/run/snapshot/export ngoài phạm vi quyền hoặc BOD yêu cầu PII. |
| `INVALID_PERIOD` | `400` | Định dạng `period_type`/`period_key`/`from_date`/`to_date` sai, `from_date > to_date`, hoặc khoảng custom vượt giới hạn cho phép. |
| `INVALID_METRIC` | `400` | Truyền `metric_code` không tồn tại trong từ điển `cskh-metrics-v1` hoặc không hỗ trợ drill-down. |
| `SNAPSHOT_STALE` | `409` | Chế độ đối soát nghiêm ngặt yêu cầu snapshot tươi tuyệt đối nhưng đang có dirty scope chưa tính xong. |
| `POLICY_CONFLICT` | `409` | Cấu hình SLA/lịch làm việc mâu thuẫn, cố sửa `original_due_at`, hoặc thao tác trái trạng thái nghĩa vụ. |
| `DATA_RECONCILIATION_FAILED` | `422` | Kiểm tra bất biến đối soát sau recompute/backfill phát hiện sai lệch tử/mẫu số (chặn publish snapshot lỗi). |
| `INSUFFICIENT_DATA` | `422` | Yêu cầu thao tác bắt buộc đủ cỡ mẫu khi dữ liệu chưa đạt ngưỡng tối thiểu. |
| `QA_DISABLED` / `QA_CONFLICT` | `409` / `403` | Gọi ghi QA khi `qa_enabled=false`, tự chấm bài của chính mình (`reviewer == staff`), hoặc review attempt đã `voided`. |
| `RECOMPUTE_IN_PROGRESS` | `409` | Phạm vi/kỳ đang có một tiến trình recompute khác giữ khóa. |
| `EXPORT_EXPIRED` | `410` | Liên kết hoặc job xuất báo cáo đã quá hạn `expires_at` hoặc đã bị thu hồi. |
