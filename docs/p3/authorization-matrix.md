# Ma Trận Phân Quyền & Kiểm Soát Truy Cập P3 (`docs/p3/authorization-matrix.md`)

Tài liệu này quy định cơ chế bảo vệ đa tầng cho toàn bộ DocType, API, lát cắt lịch sử `team-as-of`, filter-oracle, run/snapshot IDs và luồng xuất file của Phase P3, tuân thủ tuyệt đối `ADR-004` (phân quyền theo owner/team) và `ADR-005` (BOD `0` PII không cần `System Manager`).

---

## 1. Phân Quyền Trên Từng DocType P3 & `File` Đính Kèm (Generic REST / `frappe.client.*` / `reportview` / `getdoc`)

Mọi DocType P3 và `File` đính kèm đều đăng ký trong `hooks.py` (`permission_query_conditions`, `has_permission`, `doc_events`) và `api/guarded_crm.py`, bảo đảm quyền được áp **trước phân trang và trước khi đếm tổng số dòng**.

| DocType | `CSKH Staff` (`Sales User`) | `CSKH Team Leader` (`Sales Manager`) | `CSKH BOD Viewer` | `CSKH CRM Admin` / `System Manager` |
|---|---|---|---|---|
| `CSKH Working Calendar` | Đọc lịch đang kích hoạt (`0` ghi) | Đọc lịch đang kích hoạt (`0` ghi) | Đọc (`0` ghi) | Đọc / Ghi qua service |
| `CRM Holiday List` *(upstream)* | Đọc (`0` ghi — khóa qua hook P3) | Đọc (`0` ghi — khóa qua hook P3) | Đọc (`0` ghi) | Đọc / Ghi |
| `CSKH Team Membership History` | Chỉ đọc dòng `user == self` (`0` ghi) | Đọc dòng của nhân sự trong cây quản lý (`0` ghi) | `0` đọc bảng thô | Đọc kiểm toán (append-only) |
| `CSKH SLA Obligation` | Chỉ đọc nghĩa vụ thuộc Work Item mình **đang sở hữu hoạt động hiện tại** (`0` ghi) | Chỉ đọc nghĩa vụ có `team_at_obligation` thuộc cây `allowed_nodes` **và** `attribution_status = 'authoritative'` (hoặc đang mở dưới nhân sự thuộc cây hiện tại) | `0` đọc bảng thô | Đọc toàn bộ (`0` sửa `original_due_at`) |
| `CSKH KPI Run` (`F-P3-05`) | `0` đọc bảng thô / `get_run_status` | **Chỉ đọc run có `scope_type = 'team'` và `scope_id` thuộc cây `allowed_nodes`** (`403` tuyệt đối với run cấp `company`/`all` hoặc team khác) | Đọc metadata run đã publish | Đọc toàn bộ (append-only) |
| `CSKH KPI Snapshot` (`F-P3-04`) | Chỉ đọc dòng `scope_type='employee' AND scope_id=self` đã publish | Chỉ đọc dòng `scope_type='team'` thuộc `allowed_nodes` và lát cắt `scope_type='employee_team'` có `parent_team_id` thuộc `allowed_nodes` (không đọc dòng tổng `employee` xuyên đội) | Đọc dòng aggregate đã publish (`0` PII) | Đọc toàn bộ (append-only) |
| `CSKH Dirty Scope` | `0` truy cập | `0` truy cập | `0` truy cập | Chỉ đọc kiểm toán |
| `CSKH QA Review` (`F-P3-04`) | Chỉ đọc review `published` của chính mình (ẩn `internal_note`) | Chỉ đọc review có `team_at_review` thuộc `allowed_nodes` | `0` đọc bảng thô | Đọc quản trị |
| `CSKH Export Job` | Chỉ đọc job `requested_by == self` còn hiệu lực | Chỉ đọc job `requested_by == self` còn hiệu lực **và** scope vẫn thuộc cây hiện tại | Chỉ đọc job `requested_by == self` còn hiệu lực (`0` PII) | Đọc quản trị |
| `File` gắn `CSKH Export Job` (`F-P3-05`) | Khóa cả `permission_query_conditions("File")` lẫn `has_permission("File")` theo trạng thái sống của `CSKH Export Job` | Khóa cả list/query, single-doc, `download_export` và `/private/files/...`: trả `403` / `0` dòng ngay khi gỡ role, đổi team hoặc hết hạn `expires_at` | Như trên (`0` PII) | Đọc kiểm toán |

---

## 2. Cô Lập Lịch Sử `team-as-of` Khi Nhân Sự Chuyển Đội & Khóa PII Hồ Sơ Đã Đóng (`F-P3-04`)

1. **Tách lát cắt lịch sử `employee_team` (`user::team_node`):**
   - Khi nhân viên `staff.a3` chuyển từ `Team A` sang `Team B` giữa kỳ (`16/10`):
     - `Leader A` xem được đúng phần KPI, nghĩa vụ SLA và lượt liên hệ giai đoạn `01..15/10` phát sinh tại `Team A` (và không thấy dữ liệu giai đoạn `16..31/10` của `Team B`).
     - `Leader B` chỉ xem được đúng phần KPI, nghĩa vụ SLA và lượt liên hệ giai đoạn `16..31/10` phát sinh tại `Team B` (`0` lộ nghĩa vụ SLA, mã chiến dịch riêng hay lịch sử `Team A`).
2. **Chặn lộ PII hồ sơ `closed` của đội cũ sau khi nhân viên chuyển đội:**
   - Quyền đọc Work Item trạng thái `closed` của `Leader` kiểm tra **`team_at_assignment` của khoảng trách nhiệm cuối cùng** và **`team_scope` của chiến dịch** (`_can_leader_access_closed_work_item`), **không** dựa vào đội hiện tại của nhân viên đã chuyển sang team mới.
   - Nhờ đó, `Leader B` không thể đọc hay trích xuất `customer_name`, `phone`, `note` của hồ sơ đã đóng tại `Team A` qua bất kỳ đường nào (`get_metric_drilldown`, `reporting.work_item_detail`, `read_model`, REST `/api/resource/CSKH Work Item`).
   - Trên `get_metric_drilldown`, bước chiếu quyền & che PII luôn thực thi **trước** bước lọc `search` và `sort_by`, triệt tiêu hoàn toàn kênh dò tìm nhị phân (`filter-oracle`).

---

## 3. Ba Chặng Khóa Quyền Xuất Báo Cáo & Tệp Đính Kèm (`F-P3-05`)

1. **Chặng 1 (`request_export`):** Kiểm tra vai trò, phạm vi `scope_type`/`scope_id` và chặn cứng BOD xuất PII (`403 FORBIDDEN_SCOPE`).
2. **Chặng 2 (`run_export_job` trên RQ Worker):** Kiểm tra cờ kill switch (`p3_flags.is_reporting_enabled`) và xác thực lại quyền hiện hành của `requested_by` trước khi tạo file `is_private = 1`, `owner = "Administrator"`, `attached_to_doctype = "CSKH Export Job"`.
3. **Chặng 3 (`download_export` + `/private/files/...` + `/api/resource/File` + `frappe.client.get_list("File")`):**
   - `permissions.py:linked_lead_query_conditions("File")` và `linked_lead_has_permission` kiểm tra trực tiếp `is_export_job_downloadable_by_user(job, user)`:
     - `job.status == "completed"` và `now_datetime() < job.expires_at`;
     - `user` vẫn giữ vai trò CSKH hợp lệ và cây `CRM Sales Hierarchy` hiện tại vẫn bao phủ `scope_type`/`scope_id` của bản xuất.
   - Khi thu hồi role, đổi team hoặc hết hạn TTL, tất cả các đường (`download_export`, URL trực tiếp `/private/files/...`, `GET /api/resource/File/<name>`, `get_list("File")`) đều từ chối truy cập đồng nhất.
