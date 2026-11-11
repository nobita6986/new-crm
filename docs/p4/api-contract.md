# Hợp Đồng API Ổn Định & Chính Sách Tương Thích Migration Schema (`P4-09` — Cổng `G21`)

- **Mã tài liệu:** `P4-API-CONTRACT` (`docs/p4/api-contract.md` — `P4.md §6`, `§11`, `§20.2`)
- **Phiên bản bàn giao khóa cứng (`Release & Integration Lock`):**
  - **Cây mã nguồn ứng dụng (`apps_tree_sha`):** `4d361e68912f52243751ae5986cabbd13033f050` · `apps/crm_cskh` tree SHA `a8737dbcce74f9095a6e76c5e93491e92843f3d0` (`crm_cskh 0.0.1`, candidate SHA `bcdc419f9f95ad75449315572928e3d8c43817cc`)
  - **Nền tảng khóa cứng ([`release-lock.json`](../../deploy/images/release-lock.json)):** `Frappe Framework v15.126.0` (`53bae3ef2550ca96320f036f581446783cd11b72`, nâng từ `N-1 v15.123.1` @ `d2efa79657ba5c1abf96098b3bfacc199517d57e`), `Frappe CRM v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`, `frappe-ui 1.0.0-beta.29`, `vite 8.2.0`)
  - **Registry Digest bất biến:** `ghcr.io/nobita6986/crm-cskh@sha256:693a3cdcc758dab64b3ac61d8e6f066581d94b362c9649d709684b72d7d0a2e6` (OCI Image ID `sha256:4d43b76850b8caa34f96a4139b31e8bd17815706bf93edcb8a0dd18cec25e8df`)
  - **Hợp đồng công thức & Schema đỉnh:** `cskh-metrics-v2` · `crm_cskh.patches.v3_0.p3_schema_and_seeds` (`Class B` additive schema, idempotent qua `migrate ×2`)
- **Phạm vi:** Toàn bộ **`113` endpoint `@frappe.whitelist`** trong `apps/crm_cskh`, các bề mặt gốc Frappe được gắn rào chắn (`REST /api/resource/*`, `/private/files/*`, `override_whitelisted_methods`), chuẩn phản hồi lỗi khử PII, hợp đồng báo cáo `cskh-metrics-v2` và chính sách tương thích migration `Class A/B/C/D` + `BLG-P4-MIG-01`.
- **Nguyên tắc nền tảng (`P4.md §0.2`, `§11`):**
  1. **Một hệ thống Web duy nhất trên Frappe/MariaDB:** Không xây ứng dụng di động/desktop ở P4, không mở database thứ hai và không đổi tên hàng loạt các route `/api/method/crm_cskh.*` đã khóa từ P0–P3.
  2. **100% kiểm quyền phía server (`0` endpoint `allow_guest=True`):** Mọi endpoint `crm_cskh` đều để mặc định `allow_guest=False`, buộc Frappe từ chối `Guest` ở tầng `frappe.is_whitelisted()` trước khi hàm nghiệp vụ thực thi; actor luôn lấy từ `frappe.session.user` (không bao giờ tin `performed_by`/`owner`/`team` do client gửi).

---

## 1. Quy Ước Xác Thực, CSRF, Phân Trang, Idempotency & Chuẩn Lỗi Chung (`P4.md §11.2`)

### 1.1. Xác thực phiên, Cookie TLS & Bảo vệ CSRF
- **Phiên đăng nhập:** Quản lý qua Redis session của Frappe (`POST /api/method/login`), cookie `sid` gắn `HttpOnly`, `SameSite=Lax`, và `Secure` trên cổng HTTPS (`proxy` Traefik / Nginx). Khi `User.enabled = 0`, phiên hiện hữu bị từ chối tức thì trên mọi request kế tiếp (`S15`, `F30`).
- **CSRF bắt buộc cho mọi lệnh ghi (`POST` / `PUT` / `DELETE`):** Client web truyền header `X-Frappe-CSRF-Token` khớp `frappe.session.data.csrf_token`. Mọi yêu cầu `POST` thiếu hoặc sai mã CSRF đều bị từ chối `400`/`403` (`CSRFTokenError`, kiểm chứng tự động trong `S16` và trên HTTPS thật bởi `manage_isolated_staging_dr.py`).
- **Cấm nhúng khóa quản trị vào trình duyệt:** Không bao giờ xuất `api_key:api_secret` của `Administrator` hay `System Manager` xuống HTML/JS bundle.

### 1.2. Giới hạn phân trang (`Pagination Clamp`), Kích thước Payload & Rate-Limit
- **Kẹp cứng kích thước trang (`S18`):** Tất cả API danh sách và truy vết (`customers.*`, `workflow.*`, `p3_reporting.*`, `reporting.*`, `sla.*`, `qa.*`, `import_history.*`, `run_history.*`, `ownership_ui.*`, `pool_ui.*`) đều ép kiểu số nguyên và kẹp trần **`1 <= page_size <= 100`** (mặc định `20`–`50`); truyền `page_size=99999` luôn trả tối đa `<= 100` bản ghi, không gây tràn bộ nhớ Gunicorn.
- **Giới hạn tệp & payload:** Nginx khóa `CLIENT_MAX_BODY_SIZE=25m`; `upload_import_file` kiểm tra định dạng `.csv`/`.xlsx`, dung lượng tối đa `10 MB` (`FILE_TOO_LARGE`) và số dòng tối đa theo cấu hình lô (`TOO_MANY_ROWS`).
- **Kiểm soát tải xuất báo cáo:** Tách rời khỏi luồng đồng bộ qua hàng đợi `CSKH Export Job` (`request_export` → worker → `download_export`), áp hạn mức số job đang mở trên mỗi người dùng và tự động thu hồi tệp sau `expires_at` (`expire_stale_exports` chạy mỗi giờ, hậu kiểm bởi `INV-06`).

### 1.3. Ngữ nghĩa Idempotency & Kiểm soát Cạnh tranh Lạc quan (`Optimistic Concurrency`)
- **Khóa chống ghi trùng (`idempotency_key` / `request_id`):** Bắt buộc/hỗ trợ trên các lệnh ghi trọng yếu (`imports.create_preview`, `distribution.preview`, `distribution.execute`, `ownership.assign`, `ownership.reassign`, `ownership.revoke`, `workflow.record_attempt`, `workflow.schedule_followup`, `workflow.complete_followup`, `workflow.close_work_item`, `workflow.reopen_work_item`, `workflow.request_correction`, `workflow.review_correction`, `settings.save_policy`, `settings.save_outcome`, `settings.set_lead_dnc`).
  - Gửi lại **cùng `idempotency_key` và cùng tham số nghiệp vụ** bởi cùng người thực hiện: trả lại kết quả lần đầu (`idempotent_replay = true`), **không tạo bản ghi thứ hai** (`F10`, `D04`–`D06`, `INV-02`, `INV-04`).
  - Gửi lại **cùng `idempotency_key` nhưng khác nội dung nghiệp vụ**: từ chối `409` với mã `IDEMPOTENCY_CONFLICT`.
- **Chống ghi đè mù (`Optimistic Locking`):**
  - Phân phối chiến dịch kiểm tra `expected_rule_version` (`RULE_CHANGED`) và TTL bản xem trước (`PREVIEW_EXPIRED`), kết hợp bảng khóa nguyên tử `CSKH Active Claim` (`F11`).
  - Đầu việc & quy chế P2 kiểm tra `expected_revision` (`STALE_WORK_ITEM`), `expected_policy_version` (`POLICY_CHANGED`) và `expected_owner` (`OWNERSHIP_CHANGED`).

### 1.4. Chuẩn Bao Thư Lỗi (`Error Envelope`), Chống Dò Tồn Tại Tệp & Khử PII (`P4.md §11.3`)
Hệ thống gắn hai hook `after_request` trong [`apps/crm_cskh/crm_cskh/hooks.py`](../../apps/crm_cskh/crm_cskh/hooks.py):
1. **[`crm_cskh.errors.normalize_error_response`](../../apps/crm_cskh/crm_cskh/errors.py) (Đóng lỗ hổng dò tồn tại `File` & Xóa phản chiếu ID):**
   - Trên đường dẫn trực tiếp `/api/resource/File/<name>` và `/api/method/frappe.client.get?doctype=File`: cả trường hợp không đủ quyền (`403`) lẫn không tồn tại (`404`) đều được chuẩn hóa về **cùng mã HTTP `404` và cùng chuỗi JSON cố định byte-identical** (`{"exc_type":"DoesNotExistError","_server_messages":...}` với thông điệp `"Không tìm thấy tài liệu."`), tuyệt đối không phản chiếu tên tệp do client truyền vào.
   - Trên các đường dẫn khác khi phát sinh `PermissionError`/`DoesNotExistError`: giữ nguyên mã HTTP (`403`/`404`) nhưng thay thế mọi thông điệp phản chiếu mã `CRM-LEAD-*`, `CSKH Work Item`, `CRM Call Log`, `CRM Task` bằng thông điệp trung tính `"Không đủ quyền truy cập tài liệu này."`.
2. **[`crm_cskh.p1_errors.normalize_p1_error_response`](../../apps/crm_cskh/crm_cskh/p1_errors.py) (Chuẩn hóa 37 ngoại lệ nghiệp vụ P1–P3):**
   - Gắn trường ổn định `code`, `p1_code`, `exc_type` và `details` (đã qua `redact_pii_text` che SĐT/bí mật) cho toàn bộ họ ngoại lệ `P1Error`:
     - `400 Bad Request`: `INVALID_REQUEST`, `INVALID_PHONE`, `UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE`, `TOO_MANY_ROWS`, `INVALID_CHANNEL`, `INVALID_OUTCOME`, `NOTE_REQUIRED`, `FOLLOWUP_REQUIRED`, `INVALID_FOLLOWUP_TIME`, `OUTCOME_NOT_COMPATIBLE`, `INVALID_PERIOD`, `INVALID_METRIC`, `INSUFFICIENT_DATA`.
     - `403 Forbidden`: `FORBIDDEN`, `FORBIDDEN_SCOPE`, `DO_NOT_CONTACT`, `QA_DISABLED`.
     - `404 Not Found` / `410 Gone`: `NOT_FOUND`, `PREVIEW_EXPIRED`, `EXPORT_EXPIRED`.
     - `409 Conflict`: `CONFLICT`, `RULE_CHANGED`, `CAPACITY_EXCEEDED`, `OWNERSHIP_CHANGED`, `WORK_ITEM_NOT_ACTIVE`, `STALE_WORK_ITEM`, `POLICY_CHANGED`, `IDEMPOTENCY_CONFLICT`, `ALREADY_LINKED`, `SNAPSHOT_STALE`, `POLICY_CONFLICT`, `DATA_RECONCILIATION_FAILED`, `QA_CONFLICT`, `RECOMPUTE_IN_PROGRESS`.

### 1.5. Phân Loại PII, Masking & Header Cache
- **Ẩn PII theo vai trò (`S12`, `S20`, `F02`, `F27`, `F29`):** Vai trò `CSKH BOD Viewer` xem báo cáo tổng hợp và drill-down toàn công ty nhưng luôn bị che số điện thoại thô (`mask_phone`) và chặn cứng `include_pii=1` trên `get_metric_drilldown` / `request_export` (`403 FORBIDDEN_SCOPE`).
- **Cache Strategy:** Các trang `/cskh/*` và phản hồi API chứa dữ liệu theo phiên người dùng không lưu cache công khai trên proxy; snapshot KPI đọc từ bảng `tabCSKH KPI Snapshot` có trường `as_of`, `formula_version`, `run_id`, `is_latest_published`, `is_restated` để kiểm chứng độ tươi mà không cần quét lại bảng `tabCSKH Contact Attempt`.

---

## 2. Ma Trận Quyền Vai Trò Chuẩn Trên Hệ Thống

| Ký hiệu trong bảng | Frappe Role thực tế | Phạm vi dữ liệu phía Server |
| :--- | :--- | :--- |
| **`Admin`** | `System Manager` hoặc `CSKH CRM Admin` | Cấu hình chiến dịch, nhập kho, phân phối, điều chuyển/thu hồi có kiểm toán, xem báo cáo & xuất dữ liệu trong phạm vi quản trị. |
| **`BOD`** | `CSKH BOD Viewer` | Chỉ đọc báo cáo tổng hợp, so sánh đội/nhân sự/chiến dịch, drill-down và xuất CSV tổng hợp **đã che sạch PII thô**; **cấm** quyền `System Manager`, sửa dữ liệu hay mở hồ sơ cá nhân thô. |
| **`Leader`** | `Sales Manager` (gắn nhánh `CRM Sales Hierarchy`) | Chỉ đọc/duyệt hiệu chỉnh/xem báo cáo và SLA của nhân sự thuộc nhánh đội mình (`team-as-of`); cấm đọc sang nhánh đội khác (`403 FORBIDDEN_SCOPE`). |
| **`Staff`** | `Sales User` (**Người tuyển dụng**) | Chỉ đọc/ghi `CSKH Work Item`, `CRM Lead`, `CSKH Contact Attempt`, `CRM Task` đang giao cho chính mình (`assigned_to == session.user`) và bảng điểm cá nhân (`get_my_scorecard`). |
| **`QA`** | `CSKH QA Reviewer` / `CSKH CRM Admin` | Lấy mẫu và chấm phiếu `CSKH QA Review` (cấm tự chấm lần liên hệ của chính mình — `QA_CONFLICT`). |

---

## 3. Danh Mục Đầy Đủ `113` Endpoint `@frappe.whitelist` Theo 21 Module

> Toàn bộ `113` endpoint dưới đây được đối chiếu tự động bằng cây cú pháp AST từ `apps/crm_cskh/crm_cskh` trong kiểm thử cổng tài liệu tại [`deploy/ops/test_ops_toolkit.py`](../../deploy/ops/test_ops_toolkit.py).

### 3.1. Nhóm Sức Khỏe, Kho Hồ Sơ, Nhập Liệu & Phân Phối Chiến Dịch (`P0`–`P1` — `25` endpoints)

| STT | Dotted Endpoint (`/api/method/...`) | HTTP | Vai trò được phép | Tham số chính & Giới hạn | Idempotency / Ghi chú hợp đồng |
| :---: | :--- | :---: | :--- | :--- | :--- |
| 1 | `crm_cskh.api.health.ping` | `GET`/`POST` | Mọi user đã đăng nhập (`!= Guest`) | Không tham số | Chỉ trả `{app, status, app_version, user}`; `0` secret/path. |
| 2 | `crm_cskh.api.campaigns.list` | `GET` | `Admin`, `BOD`, `Leader`, `Staff` (lọc scope) | `status`, `campaign` | Trả danh sách chiến dịch trong phạm vi vai trò. |
| 3 | `crm_cskh.api.campaigns.save` | `POST` | `Admin` | `code`, `title`, `description`, `status`, `team_scope`, `campaign`, `selection` | Tạo/cập nhật `CSKH Campaign` kèm kiểm toán. |
| 4 | `crm_cskh.api.campaigns.rules` | `GET` | `Admin`, `Leader` (đội mình) | `campaign` | Đọc trọng số & sức chứa `CSKH Assignment Rule`. |
| 5 | `crm_cskh.api.campaigns.save_rule` | `POST` | `Admin` | `campaign`, `user`, `weight`, `capacity_limit`, `team`, `enabled`, `version` | Kiểm tra `version` chống ghi đè quy tắc phân công. |
| 6 | `crm_cskh.api.customers.my_list` | `GET` | `Staff`, `Leader`, `Admin` | `page`, `page_size` (`<=100`), `status`, `campaign` | Luôn lọc cứng theo `frappe.session.user` cho `Staff`. |
| 7 | `crm_cskh.api.customers.pool_list` | `GET` | `Admin` | `page`, `page_size` (`<=100`), `campaign`, `batch` | Danh sách hồ sơ trong kho chờ phân phối. |
| 8 | `crm_cskh.api.customers.customer_pool` | `GET` | `Admin` | `page`, `page_size` (`<=100`), `campaign`, `state`, `source`, `batch`, `search` | Tra cứu kho hồ sơ tổng hợp theo trạng thái/nguồn. |
| 9 | `crm_cskh.api.pool_ui.list` | `GET` | `Admin` | `page`, `page_size` (`<=100`), `campaign`, `state`, `source`, `batch`, `search` | Adapter dữ liệu cho màn `/cskh/pool`. |
| 10 | `crm_cskh.api.dashboard.distribution` | `GET` | `Admin`, `BOD`, `Leader` (lọc đội) | `campaign`, `date_from`, `date_to` | Tổng hợp phễu kho số & phân phối P1. |
| 11 | `crm_cskh.api.dashboard.campaigns` | `GET` | `Admin`, `BOD`, `Leader` | Không tham số | Danh mục chiến dịch cho bộ lọc dashboard P1. |
| 12 | `crm_cskh.api.import_files.upload_import_file` | `POST` | `Admin` | `file_name`, `content` (`<=10 MB`, `.csv`/`.xlsx`) | Lưu bắt buộc vào `private/files` (`is_private=1`). |
| 13 | `crm_cskh.api.imports.create_preview` | `POST` | `Admin` | `file_name`, `mapping`, `idempotency_key`, `import_mode` | Chuẩn hóa SĐT Việt Nam, phát hiện trùng nội lô & kho chính. |
| 14 | `crm_cskh.api.imports.confirm` | `POST` | `Admin` | `batch` | Xếp hàng job nhập bất đồng bộ, chống chạy đúp cùng `batch`. |
| 15 | `crm_cskh.api.imports.get_status` | `GET` | `Admin` | `batch` | Theo dõi tiến độ & kết quả lô nhập `CSKH Import Batch`. |
| 16 | `crm_cskh.api.import_history.history` | `GET` | `Admin` | `page`, `page_size` (`<=100`), `status` | Lịch sử các lô nhập danh sách liên hệ. |
| 17 | `crm_cskh.api.import_history.batch_detail` | `GET` | `Admin` | `batch` | Chi tiết thống kê mới/trùng/lỗi của một lô nhập. |
| 18 | `crm_cskh.api.import_history.download_error_file` | `GET` | `Admin` | `batch` | Xuất danh sách dòng lỗi đã trung hòa công thức CSV (`'`). |
| 19 | `crm_cskh.api.distribution.preview` | `POST` | `Admin` | `campaign`, `idempotency_key` | Tính toán phân bổ trọng số có nhớ lịch sử (`50/30/20`). |
| 20 | `crm_cskh.api.distribution.execute` | `POST` | `Admin` | `preview_id`, `expected_rule_version`, `idempotency_key` | Khóa `CSKH Active Claim`, bảo đảm duy nhất 1 owner active. |
| 21 | `crm_cskh.api.distribution.get_run` | `GET` | `Admin` | `run_id` | Đọc kết quả lượt chạy `CSKH Distribution Run`. |
| 22 | `crm_cskh.api.distribution.reconcile` | `GET` | `Admin` | `campaign` | Đối soát toàn vẹn phân phối & `CSKH Active Claim`. |
| 23 | `crm_cskh.api.run_history.runs` | `GET` | `Admin` | `page`, `page_size` (`<=100`), `campaign`, `status`, `run_id` | Lịch sử các đợt phân phối hồ sơ. |
| 24 | `crm_cskh.api.ownership_ui.assigned_list` | `GET` | `Admin`, `Leader` (đội mình) | `page`, `page_size` (`<=100`), `campaign`, `owner` | Danh sách đầu việc đã phân công theo người phụ trách. |
| 25 | `crm_cskh.api.ownership_ui.assignees` | `GET` | `Admin`, `Leader` (đội mình) | `campaign` | Danh sách nhân sự đủ điều kiện nhận chuyển giao. |

### 3.2. Nhóm Quản Trị Quyền Sở Hữu, Tác Nghiệp Liên Hệ, Lịch Hẹn & Quy Chế (`P1`–`P2` — `25` endpoints)

| STT | Dotted Endpoint (`/api/method/...`) | HTTP | Vai trò được phép | Tham số chính & Giới hạn | Idempotency / Ghi chú hợp đồng |
| :---: | :--- | :---: | :--- | :--- | :--- |
| 26 | `crm_cskh.api.ownership.assign` | `POST` | `Admin` | `work_item`, `target_user`, `reason`, `request_id` | Giao mới thủ công qua service chuẩn, ghi `CSKH Assignment History`. |
| 27 | `crm_cskh.api.ownership.reassign` | `POST` | `Admin`, `Leader` (trong đội được ủy quyền) | `work_item`, `expected_owner`, `target_user`, `reason`, `request_id` | Điều chuyển `A → B`, đồng bộ `CRM Task` mở, xóa sạch `DocShare`/`ToDo` của `A`. |
| 28 | `crm_cskh.api.ownership.revoke` | `POST` | `Admin` | `work_item`, `expected_owner`, `reason`, `request_id`, `return_to_pool` | Thu hồi đầu việc về kho, chấm dứt ngay quyền truy cập của owner cũ. |
| 29 | `crm_cskh.api.ownership.history` | `GET` | `Admin`, `BOD`, `Leader` (đội), `Staff` (interval hợp lệ) | `work_item`, `limit` | Đọc lịch sử bàn giao bất biến (`append-only`). |
| 30 | `crm_cskh.api.workflow.list_work_items` | `GET` | `Staff` (của mình), `Leader` (đội), `Admin` | `page`, `page_size` (`<=100`), `status`, `care_state`, `campaign`, `followup_filter`, `assignee`, `owner_at_close`, `closed_by`, `search` | Lọc phạm vi server-side trước khi đếm/phân trang. |
| 31 | `crm_cskh.api.workflow.get_work_item` | `GET` | `Staff` (owner hiện tại), `Leader` (đội), `Admin` | `work_item` | Chi tiết đầu việc + trạng thái SLA/DNC; chặn `BOD` xem thô cá nhân (`403`). |
| 32 | `crm_cskh.api.workflow.list_attempts` | `GET` | `Staff` (owner), `Leader` (đội), `Admin` | `work_item`, `page`, `page_size` (`<=100`), `include_milestones` | Lịch sử các lần liên hệ phân trang theo đầu việc. |
| 33 | `crm_cskh.api.workflow.list_unlinked_call_logs` | `GET` | `Staff` (owner), `Leader` (đội), `Admin` | `work_item`, `limit` | Liệt kê `CRM Call Log` chưa gắn `CSKH Contact Attempt` (chống gắn trùng). |
| 34 | `crm_cskh.api.workflow.record_attempt` | `POST` | `Staff` (owner active), `Leader`/`Admin` (ủy quyền) | `work_item`, `channel`, `outcome_code`, `occurred_at`, `note`, `call_log`, `followup_due_at`, `expected_revision`, `expected_policy_version`, `idempotency_key` | Actor = `session.user`; khử XSS trên `note`; replay cùng `idempotency_key` không nhân đôi. |
| 35 | `crm_cskh.api.workflow.get_outcome_codes` | `GET` | `Staff`, `Leader`, `Admin` | `channel`, `work_item`, `campaign`, `include_disabled` | Trả 12 mã kết quả tiếng Việt tương thích kênh liên hệ. |
| 36 | `crm_cskh.api.workflow.schedule_followup` | `POST` | `Staff` (owner active), `Leader`/`Admin` | `work_item`, `due_at`, `title`, `note`, `reason`, `expected_revision`, `idempotency_key` | Đặt/dời lịch hẹn `CRM Task`, bảo toàn `original_due_at` trên SLA. |
| 37 | `crm_cskh.api.workflow.list_followups` | `GET` | `Staff` (của mình), `Leader` (đội), `Admin` | `page`, `page_size` (`<=100`), `work_item`, `campaign`, `bucket`, `status`, `assignee` | Danh sách lịch hẹn hôm nay / sắp tới / quá hạn. |
| 38 | `crm_cskh.api.workflow.complete_followup` | `POST` | `Staff` (owner active), `Leader`/`Admin` | `work_item`, `task_id`, `action`, `attempt_id`, `completion_reason`, `expected_revision`, `idempotency_key` | Hoàn tất hoặc hủy lịch hẹn có lý do kiểm toán. |
| 39 | `crm_cskh.api.workflow.close_work_item` | `POST` | `Staff` (owner active), `Leader`/`Admin` | `work_item`, `completion_reason`, `close_category`, `expected_revision`, `idempotency_key` | Đóng bước liên hệ chiến dịch theo đúng `CSKH Policy`. |
| 40 | `crm_cskh.api.workflow.reopen_work_item` | `POST` | `Admin`, `Leader` (khi policy bật `allow_leader_reopen`) | `work_item`, `target_user`, `reason`, `override_dnc`, `expected_revision`, `idempotency_key` | Mở lại đầu việc có kiểm toán, chặn mở hồ sơ DNC nếu không có quyền ghi đè. |
| 41 | `crm_cskh.api.workflow.request_correction` | `POST` | `Staff` (người tạo attempt), `Leader`, `Admin` | `attempt`, `action`, `corrected_fields`, `reason`, `auto_approve`, `expected_revision`, `idempotency_key` | Tạo phiếu `CSKH Attempt Correction` (`correct`/`void`/`restore`). |
| 42 | `crm_cskh.api.workflow.review_correction` | `POST` | `Leader` (đội mình, khi policy cho phép), `Admin` | `correction_id`, `decision`, `review_note`, `expected_revision`, `idempotency_key` | Duyệt/từ chối hiệu chỉnh và đánh dấu `CSKH Dirty Scope`. |
| 43 | `crm_cskh.api.workflow.list_corrections` | `GET` | `Staff` (của mình), `Leader` (đội), `Admin` | `page`, `page_size` (`<=100`), `work_item`, `status` | Danh sách yêu cầu hiệu chỉnh lần liên hệ. |
| 44 | `crm_cskh.api.workflow.progress_summary` | `GET` | `Leader` (đội), `Admin`, `BOD` | `campaign`, `date_from`, `date_to` | Thống kê tiến độ xử lý đầu việc & lịch hẹn. |
| 45 | `crm_cskh.api.workflow.reconcile` | `GET` | `Admin` | `campaign` | Kiểm tra đối soát bất biến P2 (`Attempt` / `CRM Task` / `Call Log`). |
| 46 | `crm_cskh.api.settings.list_policies` | `GET` | `Admin`, `Leader` | `scope_type`, `campaign`, `team` | Xem quy chế vận hành `CSKH Policy`. |
| 47 | `crm_cskh.api.settings.save_policy` | `POST` | `Admin` | `policy_code`, `title`, `scope_type`, ..., `idempotency_key` | Cập nhật quy chế và tăng `policy_version` không làm hỏng lịch sử cũ. |
| 48 | `crm_cskh.api.settings.list_outcomes` | `GET` | `Admin`, `Leader`, `Staff` | `include_disabled` | Danh mục mã kết quả liên hệ `CSKH Outcome Code`. |
| 49 | `crm_cskh.api.settings.save_outcome` | `POST` | `Admin` | `code`, `label_vi`, `result_group`, ..., `idempotency_key` | Cấu hình mã kết quả tiếng Việt chuẩn hóa. |
| 50 | `crm_cskh.api.settings.set_lead_dnc` | `POST` | `Staff` (hồ sơ đang giao), `Leader` (đội), `Admin` | `lead`, `do_not_contact`, `reason`, `idempotency_key` | Gắn/gỡ cờ `Yêu cầu ngừng liên hệ (DNC)` có kiểm toán. |

### 3.3. Nhóm Hạn Xử Lý SLA, Chấm Mẫu QA & Báo Cáo Chất Lượng `cskh-metrics-v2` (`P3` — `34` endpoints)

> Module `crm_cskh.api.reporting` giữ 12 hàm ủy quyền (`alias`) tương thích ngược 1-1 sang `crm_cskh.api.p3_reporting` để không phá vỡ bất kỳ client/script nào gọi một trong hai namespace.

| STT | Dotted Endpoint (`/api/method/...`) | HTTP | Vai trò được phép | Tham số chính & Giới hạn | Hợp đồng dữ liệu / `cskh-metrics-v2` |
| :---: | :--- | :---: | :--- | :--- | :--- |
| 51 | `crm_cskh.api.sla.list_obligations` | `GET`/`POST` | `Staff` (của mình), `Leader` (đội), `BOD` (che PII), `Admin` | `work_item`, `status`, `obligation_type`, `campaign`, `team`, `employee`, `page`, `page_size` (`<=100`) | Tra cứu nghĩa vụ `FIRST_ATTEMPT` / `FOLLOWUP` theo phạm vi. |
| 52 | `crm_cskh.api.sla.get_obligation_detail` | `GET`/`POST` | `Staff` (của mình), `Leader` (đội), `BOD` (che PII), `Admin` | `obligation_id` | Trả hạn gốc `original_due_at`, hạn hiệu lực `effective_due_at`, lịch làm việc. |
| 53 | `crm_cskh.api.sla.waive_obligation` | `POST` | `Admin`, `Leader` (ủy quyền đội) | `obligation_id`, `reason` | Miễn trừ vi phạm SLA có lý do kiểm toán và kích hoạt `CSKH Dirty Scope`. |
| 54 | `crm_cskh.api.sla.pause_obligation` | `POST` | `Admin`, `Leader` (ủy quyền đội) | `obligation_id`, `reason` | Tạm dừng đồng hồ SLA có kiểm toán. |
| 55 | `crm_cskh.api.sla.resume_obligation` | `POST` | `Admin`, `Leader` (ủy quyền đội) | `obligation_id`, `reason` | Tiếp tục đồng hồ SLA, cộng bù đúng số phút làm việc thực tế. |
| 56 | `crm_cskh.api.sla.evaluate_due_obligations` | `POST` | `Admin` (`System Manager` / `CSKH CRM Admin`) | `batch_size` | Đánh giá nghĩa vụ quá hạn theo lô idempotent. |
| 57 | `crm_cskh.api.qa.get_qa_config` | `GET`/`POST` | `Admin`, `BOD`, `Leader`, `QA`, `Staff` | `campaign` | Đọc trạng thái bật/tắt QA và phiên bản rubric. |
| 58 | `crm_cskh.api.qa.list_reviews` | `GET`/`POST` | `Admin`, `QA`, `Leader` (đội), `Staff` (phiếu `published` của mình) | `reviewed_staff`, `campaign`, `status`, `page`, `page_size` (`<=100`) | Tra cứu phiếu chấm chất lượng `CSKH QA Review`. |
| 59 | `crm_cskh.api.qa.create_sample` | `POST` | `Admin`, `QA` | `contact_attempt`, `cohort_type`, `rubric_version` | Lấy mẫu kiểm định chất lượng (chặn khi `p3_qa_enabled=false`). |
| 60 | `crm_cskh.api.qa.submit_review` | `POST` | `Admin`, `QA` (`reviewer != attempt.performed_by`) | `contact_attempt`, `points_achieved`, `max_points`, `passed`, `status`, `scores`, ... | Chặn tự chấm (`QA_CONFLICT`); khi `published` tự đánh dấu `Dirty Scope`. |
| 61 | `crm_cskh.api.p3_reporting.get_executive_overview` | `GET`/`POST` | `BOD`, `Admin` | `period_type`, `period_key`, `from_date`, `to_date`, `campaign`, `team`, `lead_source` | Đọc snapshot `cskh-metrics-v2` toàn công ty (`0` quét bảng Attempt trực tiếp). |
| 62 | `crm_cskh.api.p3_reporting.get_team_comparison` | `GET`/`POST` | `BOD`, `Admin`, `Leader` (chỉ đội mình) | `period_type`, `period_key`, ..., `page`, `page_size` (`<=100`) | So sánh đội; `Leader` truyền `team` đội khác bị chặn `403 FORBIDDEN_SCOPE`. |
| 63 | `crm_cskh.api.p3_reporting.get_employee_quality` | `GET`/`POST` | `BOD`, `Admin`, `Leader` (đội mình) | `period_type`, `period_key`, `team`, `employee`, ..., `page`, `page_size` (`<=100`) | Bảng chất lượng nhân sự; tách rõ điểm `provisional` (`86.47`) và `verified`. |
| 64 | `crm_cskh.api.p3_reporting.get_campaign_quality` | `GET`/`POST` | `BOD`, `Admin`, `Leader` (đội mình) | `period_type`, `period_key`, `campaign`, `team`, ..., `page`, `page_size` (`<=100`) | Phễu chất lượng theo chiến dịch (`DISTINCT` kết nối hai chiều duy nhất). |
| 65 | `crm_cskh.api.p3_reporting.get_my_scorecard` | `GET`/`POST` | `Staff`, `Leader`, `Admin` | `period_type`, `period_key`, `from_date`, `to_date`, `campaign`, `lead_source` | Luôn lấy `employee = frappe.session.user`; `QA_PASS_RATE = null` khi mẫu số `= 0`. |
| 66 | `crm_cskh.api.p3_reporting.get_filter_options` | `GET`/`POST` | `BOD`, `Admin`, `Leader`, `Staff` | `period_type`, `period_key`, `from_date`, `to_date` | Chỉ trả danh sách đội/nhân sự/chiến dịch nằm trong scope người gọi. |
| 67 | `crm_cskh.api.p3_reporting.get_metric_drilldown` | `GET`/`POST` | `BOD` (che PII), `Admin`, `Leader` (đội), `Staff` (của mình) | `metric_code`, `period_type`, `period_key`, `scope_type`, `scope_id`, `filter_bucket`, `include_pii`, `page`, `page_size` (`<=100`) | Tổng dòng khớp tử/mẫu số snapshot; `BOD`/`Staff` yêu cầu `include_pii=1` bị chặn `403`. |
| 68 | `crm_cskh.api.p3_reporting.request_export` | `POST` | `BOD` (`include_pii=0`), `Admin`, `Leader` (đội) | `report_type`, `period_type`, `period_key`, `scope_type`, `scope_id`, `include_pii`, `format` | Khởi tạo `CSKH Export Job` chạy nền, lưu vết kiểm toán người yêu cầu. |
| 69 | `crm_cskh.api.p3_reporting.get_export_status` | `GET`/`POST` | Chính người tạo job hoặc `System Manager` | `export_id` | Trả trạng thái job xuất (`queued`/`running`/`completed`/`expired`/`failed`). |
| 70 | `crm_cskh.api.p3_reporting.download_export` | `GET` | Chính người tạo job (còn hiệu lực vai trò & chưa quá `expires_at`) | `export_id` | Ủy quyền 3 lớp + trung hòa CSV formula injection (`'`), từ chối `403`/`410` người khác. |
| 71 | `crm_cskh.api.p3_reporting.request_recompute` | `POST` | `Admin` | `period_type`, `period_key`, `from_date`, `to_date`, `scope_type`, `scope_id`, `campaign`, `reason` | Xếp hàng tính lại KPI có lý do kiểm toán; đánh dấu `is_restated=1` nếu kỳ đã chốt. |
| 72 | `crm_cskh.api.p3_reporting.get_run_status` | `GET`/`POST` | `Admin`, `BOD`, `Leader` | `run_id` | Theo dõi tiến độ lượt tính `CSKH KPI Run`. |
| 73 | `crm_cskh.api.reporting.get_executive_overview` | `GET`/`POST` | `BOD`, `Admin` | Giữ nguyên chữ ký tham số của mục `61` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_executive_overview`. |
| 74 | `crm_cskh.api.reporting.get_team_comparison` | `GET`/`POST` | `BOD`, `Admin`, `Leader` (đội mình) | Giữ nguyên chữ ký tham số của mục `62` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_team_comparison`. |
| 75 | `crm_cskh.api.reporting.get_employee_quality` | `GET`/`POST` | `BOD`, `Admin`, `Leader` (đội mình) | Giữ nguyên chữ ký tham số của mục `63` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_employee_quality`. |
| 76 | `crm_cskh.api.reporting.get_campaign_quality` | `GET`/`POST` | `BOD`, `Admin`, `Leader` (đội mình) | Giữ nguyên chữ ký tham số của mục `64` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_campaign_quality`. |
| 77 | `crm_cskh.api.reporting.get_my_scorecard` | `GET`/`POST` | `Staff`, `Leader`, `Admin` | Giữ nguyên chữ ký tham số của mục `65` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_my_scorecard`. |
| 78 | `crm_cskh.api.reporting.get_filter_options` | `GET`/`POST` | `BOD`, `Admin`, `Leader`, `Staff` | Giữ nguyên chữ ký tham số của mục `66` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_filter_options`. |
| 79 | `crm_cskh.api.reporting.get_metric_drilldown` | `GET`/`POST` | `BOD` (che PII), `Admin`, `Leader` (đội), `Staff` (của mình) | Giữ nguyên chữ ký tham số của mục `67` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_metric_drilldown`. |
| 80 | `crm_cskh.api.reporting.request_export` | `POST` | `BOD` (`include_pii=0`), `Admin`, `Leader` (đội) | Giữ nguyên chữ ký tham số của mục `68` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.request_export`. |
| 81 | `crm_cskh.api.reporting.get_export_status` | `GET`/`POST` | Chính người tạo job hoặc `System Manager` | Giữ nguyên chữ ký tham số của mục `69` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_export_status`. |
| 82 | `crm_cskh.api.reporting.download_export` | `GET` | Chính người tạo job (còn quyền & chưa hết hạn) | Giữ nguyên chữ ký tham số của mục `70` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.download_export`. |
| 83 | `crm_cskh.api.reporting.request_recompute` | `POST` | `Admin` | Giữ nguyên chữ ký tham số của mục `71` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.request_recompute`. |
| 84 | `crm_cskh.api.reporting.get_run_status` | `GET`/`POST` | `Admin`, `BOD`, `Leader` | Giữ nguyên chữ ký tham số của mục `72` | Alias tương thích ngược 100% sang `crm_cskh.api.p3_reporting.get_run_status`. |

### 3.4. Nhóm `29` Endpoint Ghi Đè Rào Chắn Lên Frappe & Frappe CRM Gốc (`override_whitelisted_methods`)

Các endpoint này tự động chặn mọi lời gọi trực tiếp vào đường dẫn gốc của Frappe (`/api/method/frappe.*`, `/api/method/crm.*`) cũng như đường dẫn nội bộ `/api/method/crm_cskh.*`:

| STT | Endpoint thực thi trong `crm_cskh` | Đường dẫn gốc được ghi đè (`hooks.py`) | Quy tắc kiểm soát truy cập phía Server |
| :---: | :--- | :--- | :--- |
| 85 | `crm_cskh.api.guarded_crm.get_contact_by_phone_number` | `crm.integrations.api.get_contact_by_phone_number` | Chỉ trả liên hệ gắn với `CRM Lead` nằm trong scope của `session.user`. |
| 86 | `crm_cskh.api.guarded_crm.get_contact_lead_or_deal_from_number` | `crm.integrations.api.get_contact_lead_or_deal_from_number` | Chặn dò SĐT chéo nhân viên/đội qua tích hợp tổng đài. |
| 87 | `crm_cskh.api.guarded_crm.get_assigned_users` | `crm.api.doc.get_assigned_users` | Kiểm tra `has_permission("read")` trên tài liệu gốc trước khi trả người được giao. |
| 88 | `crm_cskh.api.guarded_crm.add_assignment` | `frappe.desk.form.assign_to.add` | **Chặn cứng `Staff` tự tạo `ToDo` để chiếm `CRM Lead` / `CSKH Work Item`** (`S08` → `403`). |
| 89 | `crm_cskh.api.guarded_crm.get_linked_docs_of_document` | `crm.api.doc.get_linked_docs_of_document` | Kiểm tra quyền đọc trên tài liệu cha và lọc từng tài liệu con theo scope. |
| 90 | `crm_cskh.api.guarded_crm.get_linked_calls` | `crm.api.activities.get_linked_calls` | Chặn đọc `CRM Call Log` của khách người khác (`S04`). |
| 91 | `crm_cskh.api.guarded_crm.get_linked_notes` | `crm.api.activities.get_linked_notes` | Chặn đọc `FCRM Note` ngoài phạm vi (`S05`). |
| 92 | `crm_cskh.api.guarded_crm.get_linked_tasks` | `crm.api.activities.get_linked_tasks` | Chặn đọc `CRM Task` lịch hẹn của người khác (`S05`). |
| 93 | `crm_cskh.api.guarded_crm.get_attachments` | `crm.api.activities.get_attachments` | Chặn liệt kê `File` đính kèm của hồ sơ ngoài scope (`S06`). |
| 94 | `crm_cskh.api.guarded_crm.get_activities` | `crm.api.activities.get_activities` | Dòng thời gian hoạt động chỉ hiển thị trên `CRM Lead` có quyền đọc hợp lệ. |
| 95 | `crm_cskh.api.guarded_crm.delete_bulk_docs` | `crm.api.doc.delete_bulk_docs` | Cấm xóa hàng loạt `CSKH*` / `CRM Task` lịch hẹn hoặc hồ sơ ngoài quyền. |
| 96 | `crm_cskh.api.guarded_crm.get_call_log` | `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log` | Kiểm tra quyền đọc `CRM Call Log` theo `CRM Lead` liên kết (`S04`). |
| 97 | `crm_cskh.api.guarded_crm.search_link` | `frappe.desk.search.search_link` | Áp `permission_query_conditions` và vô hiệu hóa cờ `ignore_user_permissions` từ client. |
| 98 | `crm_cskh.api.guarded_crm.search_widget` | `frappe.desk.search.search_widget` | Chặn dò tìm kiếm qua `reference_doctype` giả mạo (`P4.md §8.4 #9`). |
| 99 | `crm_cskh.api.guarded_crm.validate_link` | `frappe.client.validate_link` | Chặn trích xuất trường nhạy cảm (`mobile_no`, `phone`) qua `fields` tùy ý. |
| 100 | `crm_cskh.api.guarded_crm.client_get_value` | `frappe.client.get_value` | Kiểm tra `has_permission` cấp bản ghi thay vì chỉ kiểm tra cấp DocType. |
| 101 | `crm_cskh.api.guarded_crm.client_get_count` | `frappe.client.get_count` | Áp điều kiện lọc dòng theo phạm vi, chặn dò số lượng bằng bộ lọc SĐT. |
| 102 | `crm_cskh.api.guarded_crm.client_set_value` | `frappe.client.set_value` | Chặn sửa trực tiếp `lead_owner`, trường `DNC`, hoặc các DocType bất biến `CSKH*` (`S02`, `S09`). |
| 103 | `crm_cskh.api.guarded_crm.client_bulk_update` | `frappe.client.bulk_update` | Kiểm tra quyền ghi từng tài liệu và chặn sửa trường bảo vệ hàng loạt. |
| 104 | `crm_cskh.api.guarded_reporting.reportview_get` | `frappe.desk.reportview.get` | Áp điều kiện lọc dòng + chặn lộ cột PII / snapshot cá nhân người khác (`S19`). |
| 105 | `crm_cskh.api.guarded_reporting.reportview_get_count` | `frappe.desk.reportview.get_count` | Đếm đúng phạm vi người gọi. |
| 106 | `crm_cskh.api.guarded_reporting.reportview_get_list` | `frappe.desk.reportview.get_list` | Lọc dòng theo scope và kẹp kích thước trang an toàn. |
| 107 | `crm_cskh.api.guarded_reporting.reportview_export_query` | `frappe.desk.reportview.export_query` | Chặn xuất bảng thô vượt quyền qua Desk ReportView (`S07`, `S20`). |
| 108 | `crm_cskh.api.guarded_reporting.data_export` | `frappe.core.doctype.data_export.exporter.export_data` | Khóa Data Export gốc đối với người dùng không có quyền xuất được duyệt. |
| 109 | `crm_cskh.api.guarded_reporting.query_report_run` | `frappe.desk.query_report.run` | Ràng buộc scope theo user/team trên Query Report. |
| 110 | `crm_cskh.api.guarded_reporting.query_report_export_query` | `frappe.desk.query_report.export_query` | Chặn xuất Query Report trái phạm vi. |
| 111 | `crm_cskh.api.guarded_reporting.crm_manager_dashboard` | `crm.api.dashboard.get_dashboard` | Chặn `Staff` / `Leader` xem biểu đồ quản lý ngoài nhánh đội (`S11`). |
| 112 | `crm_cskh.api.guarded_reporting.crm_manager_chart` | `crm.api.dashboard.get_chart` | Chặn truyền `user` của đội khác vào biểu đồ Frappe CRM gốc (`S11`). |
| 113 | `crm_cskh.permissions.remove_assignments` | `crm.api.doc.remove_assignments` | Chặn `Staff` tự hủy `ToDo` để làm lệch chủ sở hữu và bỏ qua cờ `ignore_permissions` từ client (`S08`, `S14`). |

---

## 4. Hợp Đồng Phiên Bản Hóa API & Tương Thích Tiến (`Forward Compatibility ADR` — `P4.md §11.2`)

1. **Giữ ổn định namespace không hậu tố phiên bản hiện hành (`/api/method/crm_cskh.api.<module>.<fn>`):**
   - Tuân thủ `P4.md §11.2`: không đổi tên hàng loạt sang `/api/method/crm_cskh.api.v1.*` trong P4 để bảo toàn tuyệt đối tính ổn định của 15 trang `/cskh/*`, 3 `CRM Form Script` và bộ `421` bài kiểm thử JUnit.
2. **Quy tắc tiến hóa tương thích ngược (`Additive-Only Evolution`) cho Client tương lai:**
   - **Tham số đầu vào:** Chỉ thêm tham số tùy chọn có giá trị mặc định an toàn (`None` / mặc định giữ nguyên hành vi cũ); không đổi tên hay đổi kiểu tham số bắt buộc hiện có.
   - **Trường đầu ra JSON:** Chỉ bổ sung thêm khóa mới; không xóa khóa hiện có, không đổi ý nghĩa đơn vị (tỷ lệ `%`, phút, dấu thời gian) và luôn giữ nguyên trường `formula_version` (hiện là `"cskh-metrics-v2"`), `as_of`, `source_quality` (`self_reported` vs `verified`), `is_provisional`, `QA_PASS_RATE = null` khi `denominator == 0`.
   - **Khi bắt buộc có thay đổi phá vỡ tương thích ở các giai đoạn sau P4:** Phát hành song song hàm/module mới (ví dụ `crm_cskh.api.v2.*` hoặc hậu tố `_v2`) kèm thời gian chuyển tiếp có thông báo; không sửa đè hành vi của hàm hiện hữu.

---

## 5. Chính Sách Phân Loại & Tương Thích Schema Migration (`P4.md §6.1`–`§6.3`, Cổng `G06`)

### 5.1. Ma trận 4 lớp thay đổi (`Class A/B/C/D`)

| Phân lớp | Định nghĩa thay đổi | Quy tắc tương thích & Quay lui bắt buộc |
| :--- | :--- | :--- |
| **`Class A` — Chỉ đổi mã nguồn (`Code-only`)** | Sửa giao diện, bản dịch, kiểm tra đầu vào hoặc logic đọc không thêm/sửa DocType hay dữ liệu lịch sử. | Cho phép quay lui image Cấp độ 2 về digest liền trước ([`rollback-runbook.md`](./rollback-runbook.md)). |
| **`Class B` — Bổ sung Schema tương thích (`Additive Schema`)** | Thêm DocType mới, cột nullable/có default an toàn, hoặc thêm index mới (`cskh_*_idx`). | Diễn tập `migrator` **2 lần liên tiếp (`migrate ×2`)** xác nhận idempotent; nếu cần quay lui image cũ phải có rào chắn tương thích đã kiểm chứng (`D13`). |
| **`Class C` — Bù dữ liệu lịch sử (`Data Backfill`)** | Chuẩn hóa khóa định danh, tái tính `CSKH KPI Snapshot` hoặc vá dữ liệu kiểm toán. | Bắt buộc có chế độ `dry_run`, chạy theo lô có checkpoint idempotent, đối soát số dòng/SHA-256 trước–sau và bảo toàn bất biến `INV-01..06 == 0` (`D01`, `D02`, `D09`). |
| **`Class D` — Phá vỡ / Xóa cấu trúc (`Breaking / Destructive`)** | Đổi tên/xóa bảng hoặc cột đang dùng, đổi nghĩa khóa chính hoặc xóa lịch sử nghiệp vụ. | **CẤM mặc định trong P4.** Nếu phát sinh về sau bắt buộc tách 2 kỳ phát hành theo mẫu `Expand → Migrate → Contract`, có ADR riêng và bản sao lưu offsite đã diễn tập khôi phục trước khi chạm DB. |

### 5.2. Hạng mục Theo Dõi `BLG-P4-MIG-01`, Bằng Chứng `migrate ×2` & Tương Thích Quay Lui Cấp 3 (`N -> N-1`) (`G06`, `G09`)
- **Trạng thái hiện hành ([`staging-migration-backlog.md`](./staging-migration-backlog.md)):** 22/23 DocType `CSKH *` khai báo `"module": "CRM CSKH"`, riêng `CSKH Export Job` khai báo `"module": "Crm Cskh"`. Trên MariaDB 11.8 collation chuẩn `utf8mb4_unicode_ci`, `install-app` và `bench migrate` hoạt động hoàn toàn bình thường.
- **Kiểm định tự động trong [`deploy/ops/manage_isolated_staging_dr.py`](../../deploy/ops/manage_isolated_staging_dr.py) (`rehearse_migration_x2_and_reconcile` & `rehearse_real_tier3_n_to_n_minus_1_rollback`):**
  1. Chụp snapshot trước migration trên bản clone từ `N-1` (`v15.123.1`) → nâng lên `N` (`v15.126.0`, `@sha256:b1329ef7...`) → chạy one-shot `migrator` lần 1 → chụp snapshot → chạy one-shot `migrator` lần 2 → chụp snapshot đối chiếu ([`isolated-staging-dr-https-report.json`](../../deploy/ops/evidence/p4-https-delta-20261109/isolated-staging-dr-https-report.json), [`g06-g09-migration-and-rollback-report.json`](../../deploy/ops/evidence/p4-https-delta-20261109/g06-g09-migration-and-rollback-report.json)).
  2. Xác nhận `collation` của `tabModule Def` và `tabDocType` khớp `utf8mb4_unicode_ci`, phân bố `BINARY module` giữ nguyên chính xác `{"CRM CSKH": 22, "Crm Cskh": 1}` (không phát sinh lệch mới), `0` biến động số dòng ngoài dự kiến trên toàn bộ bảng `CRM Lead` / `CSKH*`, `0` bản ghi `self_reported` bị biến thành `verified`, `0` bản ghi `QA_PASS_RATE` mẫu số `0` bị biến thành `0`, bảo toàn `5/5` bản ghi `Translation` của Owner, và luồng `CSKH Export Job` sau `migrate ×2` thực thi + chặn quyền `403` chính xác.
  3. **Chứng minh tương thích ngược runtime khi quay lui Cấp 3 (`N -> N-1`):** Do thay đổi giữa `N-1` (`v15.123.1`) và `N` (`v15.126.0`) thuộc `Class B` (chỉ bổ sung tương thích, không xóa/đổi nghĩa cột hiện hữu), diễn tập hạ container ứng dụng từ `N` về lại image `N-1` thật (`46783019`, `v15.123.1`) mà **không hạ schema DB** đạt `PASS` trong `32,60 giây`, đọc/ghi bình thường toàn bộ bản ghi phát sinh trên `N` và giữ nguyên `INV-01..06 == 0`.
