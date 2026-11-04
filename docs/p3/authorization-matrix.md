# Ma Trận Phân Quyền & Kiểm Soát Truy Cập P3 (`docs/p3/authorization-matrix.md`)

Tài liệu này quy định cơ chế bảo vệ đa tầng cho toàn bộ DocType, API, filter-oracle, run/snapshot IDs và luồng xuất file của Phase P3, tuân thủ tuyệt đối `ADR-004` (phân quyền theo owner/team) và `ADR-005` (BOD `0` PII không cần `System Manager`).

---

## 1. Phân Quyền Trên Từng DocType P3 (Bao gồm Generic REST / `frappe.client.*` / Desk `reportview` / `getdoc`)

Nguyên tắc chung: **Không tin tưởng chỉ riêng giao diện Vue hay custom API.** Mọi DocType P3 đều đăng ký trong `hooks.py` (`permission_query_conditions`, `has_permission`, `doc_events` chặn ghi generic API) và trong `api/guarded_crm.py`.

| DocType | `CSKH Staff` (`Sales User`) | `CSKH Team Leader` (`Sales Manager`) | `CSKH BOD Viewer` | `CSKH CRM Admin` / `System Manager` | Ghi qua Generic REST / `frappe.client` |
|---|---|---|---|---|---|
| `CSKH Working Calendar` | Đọc lịch đang kích hoạt (`0` ghi) | Đọc lịch đang kích hoạt (`0` ghi) | Đọc (`0` ghi) | Đọc / Ghi (qua service kiểm tra hợp lệ) | Chặn đối với Staff / Leader / BOD |
| `CRM Holiday List` *(upstream)* | Đọc (`0` ghi — đã khóa qua hook P3) | Đọc (`0` ghi — đã khóa qua hook P3) | Đọc (`0` ghi) | Đọc / Ghi | Hook `validate` & `on_trash` chặn mọi user không có `CSKH CRM Admin` / `System Manager` |
| `CSKH Team Membership History` | Chỉ đọc dòng `user == self` (`0` ghi) | Đọc dòng của nhân sự trong cây hiện tại (`0` ghi) | `0` truy cập trực tiếp bảng thô | Đọc (append-only, không sửa/xóa dòng cũ) | Chặn sửa/xóa tuyệt đối (append-only) |
| `CSKH SLA Obligation` | Chỉ đọc nghĩa vụ thuộc Work Item mình **đang sở hữu hiện tại** (`0` ghi) | Đọc nghĩa vụ thuộc phạm vi team được phép (`0` ghi trực tiếp) | `0` đọc bảng thô (tránh lộ liên kết `lead` / ghi chú; BOD chỉ đọc qua API aggregate) | Đọc toàn bộ (`0` sửa `original_due_at`) | Chặn hoàn toàn ghi generic API; mọi thay đổi phải qua `services/sla_engine.py` |
| `CSKH KPI Run` | `0` đọc bảng thô (tránh dò `run_id` công ty/team) | Chỉ đọc metadata run thuộc scope team mình (`0` ghi) | Đọc metadata run aggregate (`0` ghi, ẩn trường kỹ thuật nhạy cảm) | Đọc toàn bộ (append-only) | Chặn ghi generic API |
| `CSKH KPI Snapshot` | Chỉ đọc dòng `scope_type='employee' AND scope_id=self` đã publish (`0` ghi) | Chỉ đọc dòng `scope_type IN ('team','employee')` thuộc cây team mình đã publish (`0` ghi) | Chỉ đọc dòng aggregate đã publish (`0` PII) | Đọc toàn bộ (append-only) | Chặn ghi generic API |
| `CSKH Dirty Scope` | `0` truy cập | `0` truy cập | `0` truy cập | Chỉ đọc kiểm toán | Nội bộ hệ thống |
| `CSKH QA Review` | Chỉ đọc review `published` của chính mình (ẩn `internal_note` `permlevel=1`) | Đọc trong cây team mình | `0` đọc bảng thô (tránh lộ `lead`/`attempt` cụ thể) | Đọc quản trị | Chặn ghi generic API; mọi thao tác phải qua `services/qa.py` |
| `CSKH Export Job` | Chỉ đọc job `requested_by == self` còn hiệu lực | Chỉ đọc job `requested_by == self` **và** scope vẫn thuộc cây team hiện tại | Chỉ đọc job `requested_by == self` (`0` PII) | Đọc quản trị | Chặn ghi generic API; gắn liền với kiểm tra `File` download |

---

## 2. Chống Filter-Oracle, Sort-Oracle & Dò ID (`S16`, `S17`, `S18`)

1. **Chặn Filter/Order-By trên trường hạn chế (`guarded_crm.py`):**
   - Các trường nội bộ nhạy cảm trên DocType P3 (`audit_events_json`, `policy_snapshot_json`, `calendar_snapshot_json`, `internal_note`, `filter_payload_json`, `file_url`) được đặt `permlevel = 1` và đưa vào danh sách chặn filter/order_by đối với user không có quyền đọc `permlevel = 1` (trả `403` ngay khi truyền trong `filters`, `or_filters` hoặc `order_by`, ngăn suy luận nhị phân qua độ dài danh sách kết quả).
2. **Bảo vệ `run_id`, `snapshot_id`, `obligation_id`, `export_id`:**
   - Sử dụng định danh không đoán tuần tự dễ dàng hoặc luôn kiểm tra `has_permission` theo đúng scope của đối tượng trước khi tiết lộ sự tồn tại hay trạng thái. Staff gọi `get_run_status(run_id_công_ty)` hoặc `download_export(export_id_người_khác)` luôn bị từ chối `403 FORBIDDEN_SCOPE`.

---

## 3. Ba Chặng Kiểm Tra Quyền Cho Luồng Xuất File & Bảo Vệ `/private/files/...` (`S10`, `S11`, `S12`, `OBS-P2-03`)

1. **Chặng 1 — Lúc tạo yêu cầu (`request_export` / Enqueue):**
   - Xác thực phiên đăng nhập, vai trò và phạm vi (`scope_type`, `scope_id`, `report_type`).
   - BOD yêu cầu báo cáo chứa cột PII khách hàng -> từ chối `403 FORBIDDEN_SCOPE` ngay lập tức, không tạo job.
   - Staff yêu cầu xuất ngoài `my_scorecard` cá nhân không PII -> từ chối `403 FORBIDDEN_SCOPE`.
2. **Chặng 2 — Lúc Worker xử lý nền (`jobs/run_export.py`):**
   - Trước khi truy vấn dữ liệu và ghi file, worker nạp lại vai trò & cây phân cấp **hiện tại** của `requested_by` từ DB. Nếu quyền đã bị thu hồi trong lúc job nằm trong hàng đợi, job chuyển sang `revoked` và không sinh file.
   - Ghi file với `is_private = 1`, **`owner = "Administrator"`**, `attached_to_doctype = "CSKH Export Job"`, `attached_to_name = job.name`, tên file chứa token ngẫu nhiên `secrets.token_hex(16)`.
3. **Chặng 3 — Lúc tải file (`download_export` VÀ truy cập trực tiếp `/private/files/...`):**
   - Vì `File.owner == "Administrator"` (khác `requested_by`), hàm `frappe/core/doctype/file/file.py::has_permission` (dòng `1003–1029`) **không bao giờ** cho qua theo `owner`, mà bắt buộc gọi `CSKH Export Job.has_permission("read", user=current_user)`.
   - Hàm kiểm tra quyền của `CSKH Export Job` xác minh đồng thời:
     1. `current_user == job.requested_by` (hoặc `CSKH CRM Admin` được phép kiểm toán);
     2. `job.status == "completed"` và `now_datetime() < job.expires_at`;
     3. Quyền vai trò và cây `CRM Sales Hierarchy` **tại thời điểm tải** của `current_user` vẫn bao phủ đầy đủ `scope_type` / `scope_id` của bản xuất.
   - Nếu bất kỳ điều kiện nào sai (ví dụ Leader vừa bị chuyển khỏi Team A hoặc gỡ role sau khi xuất file), **cả `download_export` lẫn URL `/private/files/<tên_file>.csv` gốc đều lập tức trả `403 Forbidden`**.

---

## 4. Ánh Xạ Bộ Kịch Bản Kiểm Thử Bảo Mật Bắt Buộc (`S01..S18`)

| Mã | Kịch bản tấn công / lạm quyền | Kết quả bắt buộc |
|---|---|---|
| `S01` | `Staff A` gọi `get_employee_quality(employee=Staff B)` | `403 FORBIDDEN_SCOPE` |
| `S02` | `Staff A` gọi `get_team_comparison` / `get_executive_overview` | `403 FORBIDDEN_SCOPE` |
| `S03` | `Staff A` xem `get_my_scorecard` / `get_metric_drilldown` cho kỳ cũ chứa Lead đã chuyển sang `Staff B` | Xem được số đếm KPI của mình; **0 PII** (không có tên/SĐT/ghi chú của khách đã chuyển giao) |
| `S04` | `Leader Team 1` gọi `get_team_comparison` / `get_employee_quality` lọc sang `Team 2` | `403 FORBIDDEN_SCOPE` |
| `S05` | `Staff A` tự gọi `sla.waive_obligation` / `sla.pause_obligation` | `403 FORBIDDEN_SCOPE` |
| `S06` | Người dùng (kể cả Leader/Admin) tự tạo `CSKH QA Review` cho attempt của chính mình khi bật QA | `403` / `409 QA_CONFLICT` |
| `S07` | `Staff` đọc/ghi `CSKH QA Review` của người khác qua `/api/resource/CSKH QA Review` | `403` / `404` |
| `S08` | `BOD Viewer` gọi toàn bộ dashboard P3 mà không có role `System Manager` | `200 OK` cho aggregate; **`0` trường PII khách hàng** trong toàn bộ JSON |
| `S09` | `BOD Viewer` gọi `get_metric_drilldown` yêu cầu PII khách hàng hoặc đọc `/api/resource/CSKH SLA Obligation` | `403 FORBIDDEN_SCOPE` |
| `S10` | `BOD Viewer` yêu cầu `request_export` cho tập dữ liệu chứa PII khách hàng | `403 FORBIDDEN_SCOPE` |
| `S11` | `Staff B` hoặc `Guest` gọi `download_export` hoặc `/private/files/...` của file xuất do `Leader 1` tạo | `403 Forbidden` |
| `S12` | `Leader 1` xuất file thành công -> bị gỡ quyền Team 1 (hoặc hết hạn TTL) -> tải lại bằng `download_export` VÀ URL `/private/files/...` cũ | `403 Forbidden` / `410 EXPORT_EXPIRED` trên **cả hai đường** |
| `S13` | `Staff` gọi `frappe.client.set_value` / `PUT /api/resource/CSKH KPI Snapshot` để sửa điểm | `403 Forbidden` |
| `S14` | `Staff` hoặc `Admin` cố sửa `original_due_at` trên `CSKH SLA Obligation` | Bị chặn cứng (`403` / `409 POLICY_CONFLICT`) |
| `S15` | `Leader Team 1` xem kỳ sau thời điểm nhân sự chuyển sang `Team 2` hoặc drill-down Lead đã sang `Team 2` | Không thấy hoạt động tại `Team 2`; không lộ PII Lead ngoài phạm vi hiện hành |
| `S16` | Dò thông tin qua `filters=[["CSKH SLA Obligation","policy_snapshot_json","like","%...%"]]` trên `reportview`/`get_list` | `403 Forbidden` (chặn filter-oracle) |
| `S17` | `Staff` thử dò `get_run_status(run_id)` của công ty/team khác | `403 FORBIDDEN_SCOPE` |
| `S18` | Dữ liệu tên chiến dịch/nhãn chứa `=CMD\|' /C calc'!A0`, `+1+1`, `@SUM(...)` được xuất ra CSV | Được escape an toàn thành `'=CMD...`, `'+1+1`, `'@SUM(...)` |
