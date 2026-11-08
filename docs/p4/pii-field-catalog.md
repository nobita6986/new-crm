# Danh mục Dữ liệu Cá nhân (PII) Cấp Trường & Bản đồ Nơi Lưu Bản sao (`P4-01`)

- **Baseline đối chiếu mã nguồn:** `main` tại `92e14af3532876ad5bc2468bf0e502474cc95a53` (`apps/crm_cskh` tree `da6373a01ad963301877f69c413fdda9a361f718`, Frappe `v15.123.1`, Frappe CRM `v1.87.0`)
- **Mục đích:** Xác lập danh mục PII cấp trường dựa trên mã nguồn thực tế (không suy đoán lý thuyết), bao phủ cơ sở dữ liệu, tệp đính kèm (`File`), luồng nhập/xuất (`import`/`export`), nhật ký (`logs`) và toàn bộ nơi lưu bản sao phục vụ cô lập Staging (`P4.md §5.3`), hardening quyền (`P4-04`) và sao lưu/khôi phục (`P4-05`).

---

## 1. Phân loại Mức Nhạy cảm Dữ liệu

| Nhãn phân loại | Ý nghĩa | Quy tắc bắt buộc trên Staging & Báo cáo/Log |
| :--- | :--- | :--- |
| **`PII-DIRECT`** | Định danh trực tiếp khách hàng (số điện thoại thô hoặc chuẩn hóa E.164 `+84...`, họ tên thật, email, số gọi đi/nhận, file ghi âm). | **Tuyệt đối không nạp dữ liệu thật lên Staging/Smoke**; không ghi ra log/error response; che hoặc ẩn trên UI/API theo vai trò (`BOD` không xem/xuất PII; `Team Leader` bị che dòng lịch sử sau chuyển giao). |
| **`PII-CONTEXT`** | Văn bản tự do hoặc nhật ký nghiệp vụ gắn với khách hàng (ghi chú cuộc gọi/Zalo/FB, lý do ngừng liên hệ DNC, lý do dời hẹn/đóng việc/miễn trừ SLA, snapshot trước khi sửa). | Quét sạch HTML/script và tiền tố công thức CSV (`sanitize_note_text`, `sanitize_csv_cell`); kiểm soát quyền đọc theo dòng (`CRM Lead` / `CSKH Work Item` ownership interval). |
| **`PII-INTERNAL`** | Thông tin định danh & đánh giá nhân sự nội bộ (email tài khoản `User`, họ tên nhân viên, lý do chuyển giao nội bộ `private_reason`, điểm KPI/QA cá nhân). | Giới hạn truy cập theo cây phân cấp `CRM Sales Hierarchy` và vai trò (`Staff` chỉ xem bản thân; `Team Leader` chỉ xem cây đội mình). |
| **`SECRET`** | Khóa mật mã & thông tin xác thực (`encryption_key` trong `site_config.json`, mật khẩu DB, `tab__Auth`, `api_secret`, cookie `sid`). | Không commit vào Git; lưu tách biệt khỏi kho backup dữ liệu hoặc mã hóa độc lập. |

---

## 2. Danh mục PII Cấp Trường trên DocType Upstream (`Frappe` & `Frappe CRM`)

Đối chiếu từ `apps/crm_cskh/crm_cskh/fixtures/custom_field.json`, `permissions.py`, `services/identity.py` và `services/call_log_adapter.py`:

| DocType (Bảng MariaDB) | Trường (`fieldname`) | Kiểu dữ liệu | Mức nhạy cảm | Nguồn phát sinh & Cơ chế kiểm soát hiện hành trong code |
| :--- | :--- | :--- | :---: | :--- |
| **`CRM Lead`** (`tabCRM Lead`) | `mobile_no`, `phone` | `Data` | **`PII-DIRECT`** | Nhập từ `import_worker._ensure_lead` (chuẩn hóa `+84...`) hoặc CRM form. Bảo vệ bởi `lead_query_conditions` & `lead_has_permission` (`lead_owner` trong scope). |
| | `first_name`, `middle_name`, `last_name`, `lead_name` | `Data` | **`PII-DIRECT`** | Họ tên khách hàng; khi thiếu tên hợp lệ sẽ đặt `"Chưa rõ tên"` kèm `cskh_is_placeholder_name = 1` (tránh ghi nhầm SĐT vào cột tên). |
| | `email`, `organization`, `website` | `Data` | **`PII-DIRECT`** | Trường chuẩn của `CRM Lead` trong Frappe CRM. |
| | `cskh_dnc_reason` *(Custom Field)* | `Small Text` | **`PII-CONTEXT`** | Lý do khách yêu cầu ngừng liên hệ toàn cục (`cskh_do_not_contact = 1`); khóa sửa trái phép qua `validate_crm_lead_dnc_fields`. |
| | `lead_owner` | `Link (User)` | `PII-INTERNAL` | Người phụ trách hiện tại; chặn tự đổi qua `validate_lead_owner` và xóa `DocShare` cũ qua `clear_stale_lead_shares`. |
| **`CRM Call Log`** (`tabCRM Call Log`) | `from`, `to` | `Data` | **`PII-DIRECT`** | Số điện thoại gọi đi và số nhận (tối đa 32 ký tự trong `call_log_adapter.py`). Bảo vệ bởi `linked_lead_query_conditions` / `linked_lead_has_permission`. |
| | `recording_url` | `Data` | **`PII-DIRECT`** | URL bản ghi âm cuộc gọi từ tổng đài (nếu tích hợp telephony). |
| | `caller`, `receiver` | `Link (User)` | `PII-INTERNAL` | Nhân viên thực hiện/nhận cuộc gọi. |
| | `note` | `Link (FCRM Note)` | `PII-CONTEXT` | Liên kết sang bản ghi ghi chú cuộc gọi của cùng `CRM Lead`. |
| **`CRM Task`** (`tabCRM Task`) | `title`, `description` | `Data` / `Text Editor` | **`PII-CONTEXT`** | Tiêu đề và mô tả công việc / lịch hẹn gọi lại khách hàng (`cskh_is_followup = 1`). |
| | `cskh_completion_reason` *(Custom Field)* | `Small Text` | **`PII-CONTEXT`** | Lý do hoàn tất hoặc hủy lịch hẹn chăm sóc lại. |
| | `cskh_task_history_json` *(Custom Field)* | `Long Text` | **`PII-CONTEXT`** | Chuỗi JSON kiểm toán các lần dời hẹn/chuyển giao/hủy kèm lý do và người thực hiện (`permlevel = 1`). |
| | `assigned_to` | `Link (User)` | `PII-INTERNAL` | Nhân viên được giao lịch hẹn. |
| **`FCRM Note`** (`tabFCRM Note`) | `title`, `content` | `Data` / `Text Editor` | **`PII-CONTEXT`** | Ghi chú chi tiết gắn với `CRM Lead`; bảo vệ bởi `linked_lead_has_permission` và `guarded_crm.get_linked_notes`. |
| **`Comment` / `Communication`** | `content`, `comment_email`, `comment_by`, `sender`, `recipients` | `Text Editor` / `Data` | **`PII-CONTEXT`** / **`PII-DIRECT`** | Bình luận timeline và thư điện tử gắn với tài liệu; `Comment` bị ràng buộc qua `linked_lead_has_permission`. |
| **`ToDo` / `DocShare`** | `allocated_to`, `description`, `user`, `share_name` | `Link` / `Text` | `PII-INTERNAL` | Bản ghi phân công và chia sẻ quyền đọc của Frappe; bị khóa không cho nhân viên tự gán qua `validate_lead_assignment`. |
| **`User`** (`tabUser`) | `name`, `email`, `first_name`, `last_name`, `full_name`, `mobile_no`, `phone`, `last_ip` | `Data` | `PII-INTERNAL` | Hồ sơ định danh nhân sự vận hành hệ thống (`Staff`, `Team Leader`, `CRM Admin`, `BOD`). |

---

## 3. Danh mục PII Cấp Trường trên 23 DocType `CSKH *` (`apps/crm_cskh`)

Đối chiếu trực tiếp từ 23 tệp schema `apps/crm_cskh/crm_cskh/crm_cskh/doctype/*/*.json`:

| DocType | Trường chứa PII / Liên kết định danh | Mức nhạy cảm | Ghi chú thực tế từ mã nguồn |
| :--- | :--- | :---: | :--- |
| **`CSKH Identity Index`** | `normalized_phone` (`Data`, unique), `canonical_lead` (`Link -> CRM Lead`) | **`PII-DIRECT`** | Lưu **số điện thoại chuẩn hóa E.164 dạng rõ (`+84xxxxxxxxx`)** để chống trùng khách hàng toàn cục; khóa cứng chỉ hệ thống truy cập (`system_only_has_permission`). |
| **`CSKH Import Batch`** | `source_file` (`Link -> File`), **`preview_json`** (`Long Text`), `error_artifact` (`Link -> File`), `error` (`Small Text`), `owner` | **`PII-DIRECT`** | **Lưu ý quan trọng:** `preview_json` lưu tối đa **200 dòng đầu** gồm `canonical` (`+84...`), `name`, `source` và `lead` (`import_service.py` dòng 425–431, 610–615). Trường `error` đã được che SĐT bằng `_redact()` (`***`). Chỉ `CSKH CRM Admin` / `System Manager` đọc được. |
| **`CSKH Import Batch Lead`** | `import_batch`, `lead` (`Link -> CRM Lead`), `row_number` | Liên kết gián tiếp | Ánh xạ dòng tệp nhập sang `CRM Lead` phục vụ đối soát idempotent khi retry. |
| **`CSKH Active Claim`** | `lead` (`Link -> CRM Lead`), `campaign`, `work_item` | Liên kết gián tiếp | Bảng khóa chống tạo hai `Work Item` đang mở cho cùng một khách trong một chiến dịch; chặn đọc trực tiếp qua `system_only_has_permission`. |
| **`CSKH Work Item`** | `lead`, `assigned_to`, `last_activity_by`, `closed_by`, `owner_at_close`, `reopened_by`, **`completion_reason`**, **`reopen_reason`** | **`PII-CONTEXT`** & `PII-INTERNAL` | Không sao chép SĐT trực tiếp (đọc qua `CRM Lead` khi trong scope), nhưng `completion_reason` và `reopen_reason` là văn bản tự do do nhân viên/quản lý nhập. |
| **`CSKH Assignment History`** | `from_user`, `to_user`, `actor`, **`reason`**, **`private_reason`** | **`PII-CONTEXT`** & **`PII-INTERNAL`** | Append-only; `private_reason` chứa lý do quản trị nội bộ khi thu hồi/điều chuyển (ẩn với nhân viên thường). |
| **`CSKH Distribution Run`** | `preview_json`, `actual_json`, `state_json`, `error` | `PII-INTERNAL` & liên kết `lead` | Lưu danh sách phân bổ `work_item` / `lead` cho từng `user` trong đợt chia số. |
| **`CSKH Contact Attempt`** | `lead`, `owner_at_attempt`, `performed_by`, **`note`** (`Text`), `call_log`, `callback_task` | **`PII-CONTEXT`** & `PII-INTERNAL` | `note` lưu nội dung trao đổi khách hàng qua điện thoại/SMS/Zalo/Facebook (đã làm sạch HTML/formula qua `sanitize_note_text`, tối đa 4.000 ký tự). |
| **`CSKH Attempt Correction`** | `lead`, `requested_by`, `reviewed_by`, **`original_snapshot_json`**, **`corrected_fields_json`**, **`reason`**, **`review_note`** | **`PII-CONTEXT`** & `PII-INTERNAL` | `original_snapshot_json` bảo tồn nguyên văn `note` cũ trước khi đính chính hoặc hủy (`void`); `corrected_fields_json` lưu `note` mới. |
| **`CSKH SLA Obligation`** | `lead`, `owner_user`, `waived_by`, **`waive_reason`**, **`audit_events_json`** | **`PII-CONTEXT`** & `PII-INTERNAL` | `waive_reason` và `audit_events_json` lưu người thao tác và lý do miễn trừ / tạm dừng / tiếp tục SLA. |
| **`CSKH QA Review`** | `reviewed_staff`, `reviewer`, **`feedback_summary`**, **`internal_note`**, `scores_json` | `PII-INTERNAL` | Đánh giá chất lượng cuộc gọi/tương tác của nhân viên (hiện mặc định tắt qua feature flag P3). |
| **`CSKH Export Job`** | `requested_by`, `scope_id`, `includes_pii`, `file_doc`, `file_url`, `filter_payload_json`, `error_summary` | `PII-INTERNAL` & con trỏ tệp PII | Theo dõi yêu cầu xuất CSV, cờ `includes_pii` và đường dẫn tệp riêng tư gắn kèm. |
| **`CSKH KPISnapshot` / `CSKH KPI Run`** | `requested_by`, `target_scope_id`, `scope_id`, `scope_label` | `PII-INTERNAL` | Khi `scope_type` thuộc `employee` / `employee_team`, `scope_id` lưu email nhân viên và `scope_label` lưu họ tên nhân viên. `metrics_json` chỉ chứa số thống kê, **`0` PII khách hàng**. |
| **`CSKH Assignment Rule` / `CSKH Team Membership History` / `CSKH Dirty Scope`** | `user`, `recorded_by`, `owner_user` | `PII-INTERNAL` | Lưu định danh tài khoản nhân sự và lịch sử biến động đội nhóm (`team-as-of`). |
| **`CSKH Campaign` / `CSKH Outcome Code` / `CSKH Policy` / `CSKH Working Calendar` / `CSKH Working Shift` / `CSKH App Settings`** | *(Không chứa trường PII khách hàng; chỉ có `owner` / `modified_by` chuẩn của Frappe)* | Không (`0` PII khách) | Dữ liệu danh mục và cấu hình chính sách nghiệp vụ. |

---

## 4. PII trong Tệp Đính kèm (`Attachment`), Luồng Nhập (`Import`) & Xuất (`Export`)

| Luồng / Tệp | Điểm sinh dữ liệu trong code | Trường / Nội dung PII bên trong | Đường dẫn lưu trữ & Thời hạn tồn tại | Kiểm soát quyền & Rủi ro cần lưu ý ở P4 |
| :--- | :--- | :--- | :--- | :--- |
| **1. Tệp nguồn Import (`.csv`, `.xlsx`)** | `crm_cskh.api.import_files.upload_import_file` | **`PII-DIRECT` toàn phần:** toàn bộ SĐT thô, họ tên, nguồn khách và **mọi cột phụ dư thừa** có sẵn trong bảng tính gốc người dùng tải lên (tối đa 10 MB, 50.000 dòng). | `sites/<site>/private/files/<file_name>` (`File.is_private = 1`, không gắn `attached_to_doctype`). **Hiện lưu vô thời hạn trên đĩa** nếu chưa có tác vụ dọn dẹp theo chính sách lưu trữ. | Chỉ `CSKH CRM Admin` / `System Manager` tải lên & đọc qua API import; các vai trò khác bị chặn bởi `linked_lead_has_permission` + đóng oracle 403/404 tại `errors.py`. |
| **2. Tệp báo cáo lỗi Import (`import-errors-<batch>.csv`)** | `crm_cskh.services.import_service.write_error_artifact` | Cột `row_number,reason` (đã khử công thức bảng tính `sanitize_cell`); thông báo lỗi dòng không hợp lệ có thể phản chiếu chuỗi nhập sai định dạng. | `sites/<site>/private/files/import-errors-<batch>.csv` (`is_private = 1`, gắn `CSKH Import Batch`). Lưu lâu dài cùng lịch sử lô nhập. | Chỉ `CSKH CRM Admin` / `System Manager` truy cập. |
| **3. Tệp CSV Export báo cáo P3 (`cskh_export_<id>_<32hex>.csv`)** | `crm_cskh.services.p3_export.execute_export_job_now` | - Báo cáo tổng hợp: `0` PII khách hàng (chỉ có `employee_id`, `employee_name` ở báo cáo nhân sự).<br>- Báo cáo `metric_drilldown` có `includes_pii = 1`: chứa **`customer_name`, `phone`, `note_excerpt`, `lead`, `owner_user`**. | `sites/<site>/private/files/cskh_export_<id>_<32hex>.csv` (`is_private = 1`, `File.owner = 'Administrator'`). **TTL mặc định `3600s` (tối đa `86400s`)**; job hàng giờ `expire_stale_exports` gọi `os.remove()` xóa tệp vật lý trên đĩa. | Ba vòng kiểm tra quyền sống (enqueue → worker → download + `/private/files/*`). `BOD` và `Staff` bị chặn `403` khi `include_pii=true`; dòng đã chuyển giao của `Team Leader` bị ẩn PII (`pii_redacted_due_to_transfer`). Lưu ý: sau khi xóa tệp vật lý, bản ghi metadata trong `tabFile` và `tabCSKH Export Job` vẫn giữ lại để kiểm toán. |
| **4. Kênh Export mặc định của Frappe (`Data Export`, `reportview.export_query`, `query_report.export_query`)** | `crm_cskh.api.guarded_reporting` (`override_whitelisted_methods`) | Có thể trích xuất toàn bộ cột bảng nếu được gọi bởi tài khoản quản trị kỹ thuật. | Kết xuất trực tiếp qua HTTP response về trình duyệt người gọi. | **Đã khóa cứng cho mọi tài khoản nghiệp vụ** (`_require_system_manager`); chỉ `Administrator` / `System Manager` gọi được — bắt buộc hạn chế tài khoản `System Manager` trên Production (`P4.md §8.2`). |
| **5. Tệp đính kèm nghiệp vụ (`File` gắn `CRM Lead`, `CRM Call Log`, `FCRM Note`, `CRM Task`)** | Upload qua Frappe/CRM attachment APIs | **`PII-DIRECT` / `PII-CONTEXT`:** ảnh chụp màn hình chat Zalo/FB, tài liệu khách gửi, tệp ghi âm đính kèm, tên tệp gốc (`file_name`) có thể chứa SĐT/tên khách. | `sites/<site>/private/files/*` (hoặc `sites/<site>/public/files/*` nếu không bật riêng tư). | `linked_lead_has_permission` buộc kiểm tra quyền đọc trên `CRM Lead` đích qua Nginx `X-Accel-Redirect`. Ở `P4-04` cần rà soát chặn upload `is_private = 0` đối với tài liệu khách hàng. |

---

## 5. PII trong Nhật ký (`Logs`) & Bản đồ Toàn diện Các Nơi Lưu Bản sao (`Storage & Secondary Copies`)

Bảng dưới đây liệt kê toàn bộ nơi dữ liệu cá nhân tồn tại (bản gốc và bản sao thứ cấp) trong kiến trúc Compose `P4-01`:

| STT | Nơi lưu trữ / Bản sao | Vị trí cụ thể trong Compose (`P4-01`) | Loại PII có thể xuất hiện | Cơ chế bảo vệ hiện có & Yêu cầu kiểm soát vận hành |
| :---: | :--- | :--- | :--- | :--- |
| **1** | **Cơ sở dữ liệu MariaDB (Bản gốc + Bảng log nội bộ Frappe)** | Named volume `${COMPOSE_PROJECT_NAME}_db_data` (`/var/lib/mysql` trên `db`, mạng `data_net` cô lập) | - Toàn bộ PII ở Mục 2 & Mục 3.<br>- Bảng hệ thống Frappe: `tabVersion` (lưu JSON diff cũ/mới nếu bật `track_changes`), `tabError Log` (stacktrace lỗi), `tabActivity Log` (user, IP, login), `tabAccess Log`, `tabRoute History`, `tabNotification Log`, `tabEmail Queue` + InnoDB redo/undo logs. | Không mở cổng `3306`; mật khẩu `root` cấp qua Docker secret `/run/secrets/db_password`. Cấu hình dọn định kỳ `Error Log` / `Activity Log` / `Route History` và tuyệt đối không sao chép nguyên bản sang Staging. |
| **2** | **Thư mục Tệp riêng tư của Site (`private/files`)** | Named volume `${COMPOSE_PROJECT_NAME}_sites` (`sites/${SITE_NAME}/private/files/`) | Tệp nguồn import `.csv`/`.xlsx`, `import-errors-*.csv`, tệp export `cskh_export_*.csv` còn hạn TTL, tệp đính kèm riêng tư của khách hàng. | Phục vụ qua Nginx internal `/protected/...` sau khi `backend` xác thực quyền; không truy cập trực tiếp từ mạng ngoài. Cần chính sách lưu giữ/xóa định kỳ cho tệp nguồn import cũ. |
| **3** | **Thư mục Tệp công khai của Site (`public/files`)** | Named volume `${COMPOSE_PROJECT_NAME}_sites` (`sites/${SITE_NAME}/public/files/`) | Ảnh đại diện nhân sự hoặc tài nguyên giao diện công khai (**rủi ro lộ PII nếu nhân viên tải nhầm tài liệu khách hàng ở chế độ Public**). | Nginx phục vụ tĩnh không qua kiểm tra phiên; quy trình vận hành & `P4-04` phải cấm lưu tài liệu khách hàng vào `public/files`. |
| **4** | **Nhật ký tệp của Frappe (`sites/logs`)** | Named volume `${COMPOSE_PROJECT_NAME}_sites` (`sites/${SITE_NAME}/logs/{frappe,web.error,worker.error,schedule}.log`) | Các ngoại lệ ở tầng `crm_cskh` đã che SĐT/email qua `sanitizer.redact_pii_text()` và `import_worker._redact()`, nhưng ngoại lệ chưa bắt ở tầng framework/SQL vẫn có thể ghi giá trị biến vào `worker.error.log` / `web.error.log`. | Không xuất công khai qua web; chỉ vận hành viên có quyền host/container đọc được; khi trích xuất log đính kèm báo cáo bắt buộc chạy bộ lọc redact. |
| **5** | **Docker Container JSON Logs trên Máy chủ** | `/var/lib/docker/containers/<container-id>/*-json.log` trên host chạy Docker | Access log của `proxy` (Traefik), `frontend` (Nginx), `backend` (Gunicorn): chứa IP người dùng, `User-Agent`, đường dẫn URL và query string (`export_id`, `work_item`, `scope_id`). | Đã giới hạn xoay vòng tự động bằng `LOG_MAX_SIZE` (`20m` Prod / `10m` Staging) và `LOG_MAX_FILE` (`5` / `3`) trong `compose.cskh-common.yaml`. Không truyền SĐT qua URL query string. |
| **6** | **Hàng đợi Bền vững Redis (`redis-queue` AOF)** | Named volume `${COMPOSE_PROJECT_NAME}_redis_queue_data` (`/data` với `--appendonly yes` trên `redis-queue`) | Tham số gọi hàm RQ (`rq:job:*`) và stacktrace khi job nền thất bại. Các job `crm_cskh` chỉ truyền ID (`batch_name`, `export_id`, `run_id`), giúp hạn chế PII trực tiếp trong AOF trừ khi traceback in biến cục bộ. | Nằm hoàn toàn trong `data_net` (`internal: true`), không mở cổng `6379`; volume tách riêng từng môi trường. |
| **7** | **Bộ nhớ Đệm Phiên Redis (`redis-cache`)** | Chỉ nằm trong RAM của container `redis-cache` (`--save "" --appendonly no`) | Cookie phiên `sid`, hồ sơ người dùng đang đăng nhập (`bootinfo`), dữ liệu cache ngắn hạn. | Không ghi xuống đĩa (`appendonly no`), tự giải phóng theo `allkeys-lru` và khi khởi động lại container. |
| **8** | **Bản sao lưu Cục bộ & Kho Backup Offsite (`P4-05`)** | Tạm thời tại `sites/${SITE_NAME}/private/backups/` trên volume `_sites` và kho lưu trữ ngoài VPS | Bản chụp `.sql.gz` + `private-files.tar` + `files.tar` chứa **100% PII của toàn hệ thống**; nếu đi kèm `site_config.json` (`encryption_key`) thì giải mã được cả trường bí mật. | Bắt buộc mã hóa trước khi đẩy ra khỏi VPS (`restic`/`gpg`), tách quyền ghi backup với quyền xóa/khôi phục, xóa bản tạm cục bộ sau khi upload thành công và không bao giờ khôi phục bản thật lên Staging công khai. |
| **9** | **Thiết bị Đầu cuối & Trình duyệt Người dùng** | Thư mục `Downloads` trên máy tính cá nhân của `CRM Admin` / `Team Leader`, bộ nhớ trình duyệt | Bảng tính nguồn khách hàng trước khi import (`.csv`/`.xlsx`) và tệp CSV tải về từ `download_export`. | Bản sao nằm ngoài tầm xóa tự động của máy chủ: áp dụng kiểm soát quyền xuất tối thiểu, ghi vết kiểm toán mọi lượt xuất trong `CSKH Export Job` và ban hành quy định bảo mật đầu cuối trong SOP vận hành (`P4-11`). |
