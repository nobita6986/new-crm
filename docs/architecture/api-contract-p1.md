# API contract — P1 (Kho khách hàng, Team & Weighted Distribution)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/api-contract-p1.md`
> **Ngày cập nhật:** 2026-11-03
> **Trạng thái:** **Frozen cho T1A (UI)** — backend đã hiện thực và kiểm chứng runtime trên `codex/p1-foundation`
> **Phạm vi:** **chỉ backend P1**. Toàn bộ UI P1 nằm ngoài phạm vi tài liệu này.
> **Tài liệu liên quan:** [`../root-plan/P1.md`](../root-plan/P1.md) §10, [`schema-p1.md`](./schema-p1.md), [`adr-p1-07-rbac.md`](./adr-p1-07-rbac.md), [`api-contract.md`](./api-contract.md) (P0)

---

## 0. Nguyên tắc bất biến của contract

1. **Authentication theo Frappe.** Mọi endpoint yêu cầu phiên đăng nhập hợp lệ của site.
   `Administrator` / `System Manager` là **đường quản trị hệ thống**, không phải actor nghiệp vụ.
   Evidence của actor `CRM-ADMIN` phải dùng tài khoản mang role `CSKH CRM Admin` thật.
2. **Server quyết định scope.** Endpoint **không** nhận tham số `user=` / `team=` để mở rộng
   phạm vi. Scope suy ra từ role + cây `CRM Sales Hierarchy` của actor
   (ADR-P1-07 §3.2, P1 §10.1).
3. **Mutation chỉ qua controlled service.** Không mở quyền generic `frappe.client.insert` /
   `frappe.client.set_value` / `frappe.desk.form.save` cho nghiệp vụ P1. Tất cả thay đổi
   `assigned_to`, `lead_owner`, `ToDo`, `DocShare` đi qua `crm_cskh.services.ownership`.
4. **Mã lỗi ổn định.** Client branch theo `code`, không parse message tiếng Việt.
5. **Idempotency bắt buộc cho mutation.** Mọi mutation nhận `idempotency_key` hoặc
   `request_id`; lặp lại cùng key ⇒ kết quả idempotent, không nhân bản dữ liệu.
6. **Không trả PII ngoài phạm vi.** Dashboard BOD là **aggregate**; không có endpoint nào
   trả PII của một lead mà actor không được quyền.

---

## 1. Danh mục endpoint

| # | Method | Dotted path | Vai trò tối thiểu | Mục đích |
|---|---|---|---|---|
| 1 | POST | `crm_cskh.api.imports.create_preview` | `CSKH CRM Admin` | Tạo preview import từ file private đã upload |
| 2 | POST | `crm_cskh.api.imports.confirm` | `CSKH CRM Admin` | Xác nhận batch, enqueue worker |
| 3 | GET | `crm_cskh.api.imports.get_status` | `CSKH CRM Admin` | Trạng thái + tổng hợp batch |
| 4 | GET | `crm_cskh.api.campaigns.list` | `CSKH CRM Admin` / `CSKH BOD Viewer` / `Sales Manager` | Danh sách campaign |
| 5 | POST | `crm_cskh.api.campaigns.save` | `CSKH CRM Admin` | Tạo/sửa campaign (server validate) |
| 6 | GET | `crm_cskh.api.campaigns.rules` | `CSKH CRM Admin` / `CSKH BOD Viewer` / `Sales Manager` | Rules + trạng thái eligibility |
| 7 | POST | `crm_cskh.api.campaigns.save_rule` | `CSKH CRM Admin` | Tạo/sửa rule (weight, capacity) |
| 8 | POST | `crm_cskh.api.distribution.preview` | `CSKH CRM Admin` | Dry-run phân phối |
| 9 | POST | `crm_cskh.api.distribution.execute` | `CSKH CRM Admin` | Đưa preview vào hàng đợi nền RQ (trả ngay `run_id` + trạng thái sớm; hỗ trợ resume) |
| 10 | GET | `crm_cskh.api.distribution.get_run` | `CSKH CRM Admin` / `CSKH BOD Viewer` / `Sales Manager` (trong team) | Polling tiến độ (`progress`/`total`), kết quả cộng dồn và lỗi của run |
| 11 | GET | `crm_cskh.api.distribution.reconcile` | `CSKH CRM Admin` | Đối soát state với DB |
| 12 | POST | `crm_cskh.api.ownership.assign` | `CSKH CRM Admin` | Giao work item queued |
| 13 | POST | `crm_cskh.api.ownership.reassign` | `CSKH CRM Admin` | Chuyển owner (expected-owner) |
| 14 | POST | `crm_cskh.api.ownership.revoke` | `CSKH CRM Admin` | Thu hồi owner, trả về kho |
| 15 | GET | `crm_cskh.api.ownership.history` | theo quyền read của work item | Lịch sử append-only |
| 16 | GET | `crm_cskh.api.customers.my_list` | `Sales User` / `Sales Manager` / `CSKH CRM Admin` / `CSKH BOD Viewer` | Khách đang được giao |
| 17 | GET | `crm_cskh.api.customers.pool_list` | `Sales Manager` / `CSKH CRM Admin` / `CSKH BOD Viewer` | Kho work item `queued` chưa giao trong scope (hỗ trợ `campaign`, `batch`) |
| 18 | GET | `crm_cskh.api.customers.customer_pool` | `Sales Manager` / `CSKH CRM Admin` / `CSKH BOD Viewer` | Kho khách hàng canonical theo scope, gồm cả khách chưa có Work Item (`eligible`) và lọc theo `batch` (`L-25`) |
| 19 | GET | `crm_cskh.api.dashboard.distribution` | `CSKH BOD Viewer` / `CSKH CRM Admin` / `Sales Manager` | Dashboard BOD (aggregate) |
| 20 | GET | `crm_cskh.api.dashboard.campaigns` | `CSKH BOD Viewer` / `CSKH CRM Admin` / `Sales Manager` | Bộ lọc campaign của dashboard |

> **Lưu ý:** `crm_cskh.api.customers.my_list` và `crm_cskh.api.customers.pool_list` **không** có bản
> `frappe.client.get_list` tương đương. Đọc `CSKH Work Item` bằng generic API vẫn bị giới hạn
> bởi `permission_query_conditions` (ADR-P1-07), nhưng UI P1 **phải** dùng endpoint scoped ở trên
> để có `scope` + `total` + `as_of` nhất quán.

### 1.1. Method và CSRF

- Mọi endpoint ghi (`POST`) khai báo `@frappe.whitelist(methods=["POST"])` ⇒ Frappe **từ chối**
  `GET`/`PUT` với `frappe.exceptions.MethodNotAllowed`.
- `POST` trong phiên hợp lệ cần `X-Frappe-CSRF-Token` (Frappe mặc định, `ignore_csrf` không bật).
  Thiếu/sai ⇒ HTTP 400 `CSRFTokenError`.
- Không endpoint nào `allow_guest=True`.

---

## 2. Mã lỗi ổn định

Nguồn: `apps/crm_cskh/crm_cskh/services/errors.py`.

| Code | HTTP | Ý nghĩa | Endpoint hay gặp |
|---|---|---|---|
| `INVALID_REQUEST` | 400 | Thiếu/sai tham số, dữ liệu vào không hợp lệ | tất cả |
| `INVALID_PHONE` | 400 | SĐT không chuẩn hóa được về E.164 VN | import |
| `UNSUPPORTED_FORMAT` | 400 | File không phải CSV/XLSX | import |
| `FORBIDDEN` | 403 | Thiếu vai trò nghiệp vụ hoặc ngoài scope | tất cả |
| `NOT_FOUND` | 404 | Đối tượng không tồn tại (đã che identifier) | ownership, distribution |
| `FILE_TOO_LARGE` | 413 | Vượt giới hạn kích thước file (mặc định 10 MB) | import |
| `TOO_MANY_ROWS` | 413 | Vượt giới hạn số dòng (mặc định 50 000) | import |
| `CONFLICT` | 409 | Xung đột trạng thái (race, claim đã có, idempotency sai checksum) | identity, ownership, distribution |
| `DUPLICATE` | 409 | SĐT đã gắn canonical lead khác | identity/import |
| `ALREADY_ASSIGNED` | 409 | Work item không ở trạng thái `queued` / lead đã có claim | ownership, distribution |
| `RULE_CHANGED` | 409 | Rule/selection/state đổi sau preview | distribution |
| `PREVIEW_EXPIRED` | 409 | Preview hết hạn (TTL 30 phút) | distribution |
| `CAPACITY_EXCEEDED` | 409 | Vượt capacity của người nhận | ownership, distribution |
| `CAPACITY_FULL` | 409 | Không còn assignee nào đủ điều kiện | distribution |
| `NO_ELIGIBLE_LEADS` | 409 | Không có lead nào đủ điều kiện | distribution |
| `PARTIAL_SUCCESS` | 200 | Run/batch hoàn tất **có** lỗi cục bộ (xem `errors`, `skipped`) | import, distribution |
| `RETRYABLE_FAILURE` | 200 | Lỗi có thể retry (job chưa commit được) | import, distribution |

**Mapping sang HTTP status.** `P1Error` (và các lớp con) expose **cả** `http_status` và
`http_status_code`. Frappe đọc `getattr(e, "http_status_code", 500)` trong
`frappe.app.handle_exception`, nên chỉ khai báo `http_status` sẽ khiến mọi lỗi P1 trả
HTTP 500. Vì vậy `http_status_code` là thuộc tính **bắt buộc**: 400 / 403 / 404 / 409 / 413
đều được trả đúng, và UI có thể branch theo HTTP status lẫn theo `code`.

**Envelope lỗi** (`L-16` — chuẩn hóa qua `crm_cskh.errors.normalize_error_response` của P0 nối tiếp
`crm_cskh.p1_errors.normalize_p1_error_response` của P1):

```json
{
  "code": "FORBIDDEN",
  "p1_code": "FORBIDDEN",
  "exc_type": "PermissionError",
  "message": "Distributing the customers requires the CRM Admin business role.",
  "_server_messages": "[\"Distributing the customers requires the CRM Admin business role.\"]"
}
```

Khi lỗi được ném từ `P1Error` (400 / 403 / 404 / 409 / 413), JSON HTTP trả về luôn có khóa ổn định
ở top-level: `code` và `p1_code` (khớp bảng trên), kèm `exc_type` chuẩn để client branch trực tiếp
theo `code` mà không cần parse chuỗi thông báo. Các truy vấn trực tiếp tới tài nguyên `File`
(`/api/resource/File/*`, `/private/files/*`, `frappe.client.*` trên `doctype="File"`) được giữ nguyên
100% lớp bảo vệ File-oracle P0 (trả HTTP `404` đồng nhất byte-for-byte, không gắn `p1_code`).

---

## 3. Endpoint chi tiết

### 3.1 `crm_cskh.api.imports.create_preview` — POST

**Vai trò:** `CSKH CRM Admin` (hoặc `System Manager`).

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `file_name` | string | ✔ | Tên `File` đã upload (bắt buộc `is_private`) |
| `mapping` | object \| string | ✖ | `{field: tên_cột}` cho các khóa chuẩn `phone`, `name`, `source` (`L-25`) |
| `idempotency_key` | string | ✖ | Mặc định `import:{sha256(file)}:{import_mode}` (kèm hash `mapping` nếu có truyền `mapping`) |
| `import_mode` | string | ✖ | `standard` (mặc định) |

**Response 200**

```json
{
  "batch": "3f9c1e2a4b5c6d7e8f90",
  "status": "DRY_RUN",
  "checksum": "…sha256…",
  "idempotency_key": "import:…",
  "mapping": { "phone": "Số điện thoại", "name": "Họ và tên", "source": "Nguồn" },
  "resolved_mapping": { "phone": "Số điện thoại", "name": "Họ và tên", "source": "Nguồn" },
  "totals": {
    "total_rows": 1200,
    "valid_rows": 1000,
    "new_rows": 980,
    "duplicate_rows": 200,
    "error_rows": 20
  },
  "preview": [
    {
      "row_number": 3,
      "class": "valid",
      "canonical": "+84900000010",
      "name": "Nguyễn Thị Mai",
      "source": "Zalo Official"
    }
  ],
  "preview_truncated": false,
  "committed_at": null,
  "error_artifact": null,
  "as_of": "2026-11-02 10:00:00.000000"
}
```

`class` trong `preview` là một trong: `valid`, `duplicate_in_file`, `duplicate_in_crm`,
`conflict_active_campaign`, `invalid`. `preview` bị cắt còn **200 dòng đầu**
(`preview_truncated: true` khi có nhiều hơn); khi thực thi thật, `import_worker` đọc lại
toàn bộ file gốc từ `classification["rows"]` nên không bị giới hạn 200 dòng của `preview`.

**`mapping` ánh xạ *tên cột*, không phải *giá trị ô* (`L-03` + `L-25`).**
- `mapping` nhận tối đa 3 khóa nghiệp vụ chuẩn: `phone`, `name`, `source` (ví dụ `{"phone": "SDT_KH", "name": "TEN_KH", "source": "KENH_KH"}`).
- Nếu người dùng không truyền `mapping` (hoặc để trống từng khóa), `resolve_import_columns(header, cells, mapping)` tự nhận diện từ dòng tiêu đề tiếng Việt/tiếng Anh của CSV/XLSX (kể cả file XLSX thưa có dòng dữ liệu đầu tiên để trống `None` ở cột `name`/`source`).
- **Áp `name` / `source` & quy tắc Placeholder khi tạo `CRM Lead` mới (`L-25`):**
  - Mọi giá trị `name` và `source` đều đi qua `sanitize_cell` (vô hiệu hóa công thức `=`, `+`, `-`, `@` bằng dấu nháy đơn `'`; gỡ thẻ HTML/script; giới hạn 140 ký tự).
  - **Placeholder `"Chưa rõ tên"` (`cskh_is_placeholder_name = 1`) CHỈ dùng khi thiếu tên hợp lệ** (ô rỗng, chỉ khoảng trắng, thẻ HTML rỗng, chuỗi `"Chưa rõ tên"`, hoặc gõ nhầm số điện thoại vào cột tên theo P1 §5.5). Khi có tên hợp lệ sau sanitize, `first_name` và `lead_name` lưu tên thật và đặt `cskh_is_placeholder_name = 0`.
  - Khi có `source` hợp lệ sau sanitize, worker đảm bảo bản ghi `CRM Lead Source` tương ứng một cách an toàn dưới savepoint (chống race `1062`) và gán `CRM Lead.source`.
- **Không ghi đè canonical `CRM Lead` đã tồn tại (`L-25`):** Khi số điện thoại đã thuộc về một `CRM Lead` canonical có sẵn (`duplicate_in_crm`, import lặp ở batch sau, hoặc thua race đồng thời), worker **tuyệt đối không ghi đè** `first_name`, `lead_name`, `cskh_is_placeholder_name`, `source`, `lead_owner`, `status` hay ghi chú/dữ liệu nghiệp vụ của lead đó.
- **Ghi quan hệ `Import Batch ↔ Lead` (`CSKH Import Batch Lead`):** Mọi dòng hợp lệ được tạo mới (`outcome = "created"`) hoặc reuse canonical lead có sẵn (`outcome = "reused"`) đều được ghi idempotent vào `CSKH Import Batch Lead` (`UNIQUE (import_batch, lead)`), đảm bảo lọc kho theo `batch` chính xác cho cả khách chưa có `Work Item` lẫn khách được reuse qua nhiều batch.

**Checksum.** `checksum` là SHA-256 của **nội dung file**, đã chuẩn hóa về `bytes` trước khi
băm: `File.get_content()` trả `str` khi file đã flush xuống disk và `bytes` khi file còn
trong bộ nhớ. Cùng một file + cùng `mapping` ⇒ cùng `idempotency_key` ⇒ cùng `batch`.

**Chuẩn hóa số điện thoại.** `0912345678`, `84912345678` và `+84912345678` là **cùng một**
khách hàng: canonical E.164 `+84912345678`. Ô trống, ô chỉ có số, và ô ghi bằng ký hiệu
khoa học (`9.12345678E+08`) là **dòng lỗi**, không phải một khách hàng.

**Idempotency của import.** Import lại cùng một file **không** đổi `owner`, `status` hay
ghi chú của lead đã có, và **không** tự phân phối lead đó.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST` (file không tồn tại / không private),
`UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE`, `TOO_MANY_ROWS`, `CONFLICT`
(tái sử dụng cùng `idempotency_key` nhưng `checksum` hoặc `mapping` khác — P1 §6.4).

> **Không** dùng endpoint này để upload file. File được upload qua `File` API chuẩn của
> Frappe với `is_private=1`; server từ chối file không private.

### 3.2 `crm_cskh.api.imports.confirm` — POST

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `batch` | string | ✔ |

**Response 200:** giống shape `get_status` với `status` = `QUEUED` ngay từ **lần gọi `confirm` đầu tiên** (`L-18`).
Server khóa dòng batch (`SELECT ... FOR UPDATE`), đi qua đúng state machine hợp lệ
`DRY_RUN → CONFIRMED → QUEUED` (hoặc `COMPLETED_WITH_ERRORS` / `FAILED → QUEUED` khi retry) rồi
đưa job vào RQ `short` qua `frappe.enqueue(..., deduplicate=True, enqueue_after_commit=True)`.
Gọi lặp khi batch đang `QUEUED` / `RUNNING` hoặc đã `COMPLETED` trả lại trạng thái hiện tại một cách idempotent.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`.

### 3.3 `crm_cskh.api.imports.get_status` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `batch` | string | ✔ |

**Response 200:** shape như §3.1, `status` ∈ `DRY_RUN` | `CONFIRMED` | `QUEUED` |
`RUNNING` | `COMPLETED` | `COMPLETED_WITH_ERRORS` | `FAILED`.

Khi batch lỗi, `error_artifact` là tên `File` **private** chứa CSV dòng lỗi
(đã sanitize formula/HTML — SEC-08). `totals.error_rows` giữ đúng tổng số dòng không hợp lệ
từ preview cộng số dòng lỗi phát sinh thêm lúc chạy worker (không nhân đôi các dòng đã bị
preview đánh dấu `invalid`).

**Lỗi:** `FORBIDDEN` (không phải CRM Admin), `INVALID_REQUEST`.

### 3.4 `crm_cskh.api.campaigns.list` — GET

| Tham số | Kiểu | Mô tả |
|---|---|---|
| `status` | string | Lọc theo `Draft`/`Active`/`Paused`/`Closed` |
| `campaign` | string | Dự trữ (contract P0 không dùng) |

**Response 200**

```json
{
  "items": [ { "campaign": "…", "code": "CAMP-2026-11", "title": "…", "description": null,
               "status": "Active", "team_scope": null, "rule_version": 3,
               "started_at": null, "ended_at": null } ],
  "total": 1,
  "as_of": "2026-11-02 10:00:00.000000"
}
```

**Lỗi:** `FORBIDDEN`.

### 3.5 `crm_cskh.api.campaigns.save` — POST

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `code` | string | khi tạo mới | Mã campaign, unique |
| `title` | string | ✔ | |
| `description` | string | ✖ | |
| `status` | string | ✖ | `Draft` (mặc định) / `Active` / `Paused` / `Closed` |
| `team_scope` | string | ✖ | Tên `CRM Sales Hierarchy` |
| `campaign` | string | ✖ | Tên campaign cần sửa |
| `selection` | object \| string | ✖ | JSON: `{"leads": [...]}` / `{"source": "Facebook"}` / `{}` = toàn kho |

**Response 200:** `{ "campaign": "…", "code": "…", "status": "…", "rule_version": 3 }`

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST` (thiếu title/code, status không hợp lệ),
`frappe.ValidationError` (đóng campaign khi còn work item active — P1 §7.7).

### 3.6 `crm_cskh.api.campaigns.rules` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `campaign` | string | ✔ |

**Response 200**

```json
{
  "campaign": "…",
  "rule_version": 3,
  "eligible": [ { "rule": "…", "user": "staff.a@dev.local", "team": null, "weight": 50,
                  "capacity": 0, "version": 3, "enabled": true, "used": 12, "remaining": null } ],
  "excluded": [ { "user": "staff.c@dev.local", "weight": 20, "reason": "capacity_full" } ],
  "as_of": "…"
}
```

`reason` là một trong: `user_disabled`, `user_is_not_sales_user`, `no_positive_weight`,
`user_out_of_team_scope`, `capacity_full`.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`.

### 3.7 `crm_cskh.api.campaigns.save_rule` — POST

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `campaign` | string | ✔ | |
| `user` | string | ✔ | Phải là `Sales User` đang enabled |
| `weight` | int | ✔ | > 0 |
| `capacity_limit` | int | ✖ | 0 = không giới hạn |
| `team` | string | ✖ | |
| `enabled` | bool \| 0/1 \| `"true"`/`"false"` | ✖ | Mặc định `1`; parse chặt cho cả JSON và `x-www-form-urlencoded` (`L-21`) |
| `version` | int | ✖ | mặc định 1 |

**Response 200:** `{ "rule": "…", "campaign": "…", "user": "…", "weight": 50,
"capacity_limit": 0, "enabled": true, "campaign_rule_version": 4 }`

> Mỗi lần `save_rule` **bump `rule_version` của campaign** ⇒ các preview cũ bị từ chối
> với `RULE_CHANGED` ở `execute`.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`, `NOT_FOUND` (campaign không tồn tại).

### 3.8 `crm_cskh.api.distribution.preview` — POST

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `campaign` | string | ✔ |
| `idempotency_key` | string | ✖ (mặc định `preview:{campaign}:{fingerprint}`) |

**Response 200**

```json
{
  "preview_id": "9a8b7c…",
  "campaign": "…",
  "selection_fingerprint": "…sha256…",
  "rule_version": 3,
  "expires_at": "2026-11-02 10:30:00.000000",
  "summary": {
    "eligible_count": 1000,
    "excluded_count": 25,
    "targets": [ { "user": "staff.a@dev.local", "weight": 50, "planned": 500 },
                 { "user": "staff.b@dev.local", "weight": 30, "planned": 300 },
                 { "user": "staff.c@dev.local", "weight": 20, "planned": 200 } ],
    "simulated": { "staff.a@dev.local": 500, "staff.b@dev.local": 300, "staff.c@dev.local": 200 },
    "excluded_assignees": []
  }
}
```

`simulated` là kết quả chạy allocator **từ state đã commit gần nhất** ⇒ phản ánh tỷ lệ tích lũy,
không phải chia đều mỗi lần.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`, `NOT_FOUND`, `CONFLICT` (campaign không mở),
`NO_ELIGIBLE_LEADS`, `CAPACITY_FULL`.

### 3.9 `crm_cskh.api.distribution.execute` — POST (Phân phối nền qua RQ)

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `preview_id` | string | ✔ | `preview_id` từ `preview` |
| `expected_rule_version` | int | ✖ | Nếu khác version hiện tại ⇒ `RULE_CHANGED` |
| `idempotency_key` | string | ✖ | Ghi đè key của run |

**Response 200 (trả sớm ngay sau khi enqueue job nền vào RQ `long` — shape đồng nhất với `get_run`):**

```json
{
  "run_id": "9a8b7c…",
  "campaign": "…",
  "status": "queued",
  "rule_version": 3,
  "selection_fingerprint": "…",
  "total": 1000,
  "progress": 0,
  "assigned": {},
  "created": 0,
  "errors": 0,
  "skipped": [],
  "error": null,
  "started_at": null,
  "finished_at": null
}
```

**Vòng đời trạng thái (`status`):**
`planned → queued → running → completed | completed_with_errors | failed`
(khi worker bị dừng cứng và hết hạn `lease_expires_at`, gọi lại `execute` cho phép thu hồi `running → queued`).

**Cơ chế thực thi nền, checkpoint, chống double-submit và phục hồi:**
1. **Nhận job & trả sớm (`enqueue`):** API khóa dòng `CSKH Distribution Run` bằng `SELECT ... FOR UPDATE`,
   kiểm tra quyền `CSKH CRM Admin`, hạn preview (`PREVIEW_EXPIRED`), `rule_version` + con trỏ fairness +
   `selection_fingerprint` (`RULE_CHANGED`), đặt `status = "queued"` kèm `lease_expires_at`, và gọi
   `frappe.enqueue("crm_cskh.jobs.distribution_worker.run_distribution", queue="long", deduplicate=True, enqueue_after_commit=True)`.
2. **Xử lý theo chunk & tiến độ bền vững:** Worker nền xử lý theo lô (`CHUNK_SIZE = 50`), sau mỗi chunk
   đều `commit` tiến độ `progress`, con trỏ fairness `state_json`, số liệu đối soát từ DB (`assigned`,
   `created`), danh sách `skipped` (`{lead, reason, detail}`) và gia hạn `lease_expires_at`.
3. **Recheck tại thời điểm thực thi nền:** Trước khi chạy và ở đầu mỗi chunk, worker kiểm tra lại:
   - Quyền `CSKH CRM Admin` và trạng thái `enabled` của actor kích hoạt (nếu mất quyền ⇒ dừng run với `error = "FORBIDDEN: ..."`),
   - Trạng thái `Active` và `rule_version` hiện hành của campaign (nếu đổi sau khi enqueue ⇒ dừng an toàn với `error = "RULE_CHANGED: ..."`),
   - Tính hợp lệ và `capacity_limit` trực tiếp của từng nhân sự cũng như trạng thái chưa bị claim của từng lead.
4. **Double-submit / Retry / Crash recovery:**
   - Khi run đang `queued` hoặc `running` và `lease_expires_at` còn hiệu lực: gọi lặp `execute` trả nguyên
     snapshot hiện tại, **không** enqueue job thứ hai và **không** phân phối trùng.
   - Khi run đã `completed`: gọi lại `execute` trả kết quả đã lưu (idempotent).
   - Khi worker chết cứng giữa batch (`os._exit(137)`) và `lease_expires_at` đã hết hạn, hoặc khi run ở
     trạng thái `completed_with_errors` / `failed`: gọi lại `execute` kiểm tra lại `rule_version`, chuyển
     run về `queued` và chỉ xử lý tiếp các lead chưa được giao, giữ nguyên tỷ lệ fairness tích lũy.

**Lỗi đồng bộ lúc enqueue:** `FORBIDDEN`, `INVALID_REQUEST`, `NOT_FOUND`, `RULE_CHANGED`,
`PREVIEW_EXPIRED`, `CONFLICT` (`idempotency_key` trùng với run khác).

### 3.10 `crm_cskh.api.distribution.get_run` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `run_id` | string | ✔ |

**Vai trò:** `CSKH CRM Admin`, `CSKH BOD Viewer`, hoặc `Sales Manager` có quyền đọc run thuộc nhánh team của mình.

**Response 200:** cùng shape với §3.9 (`run_id`, `campaign`, `status`, `rule_version`,
`selection_fingerprint`, `total`, `progress`, `assigned`, `created`, `errors`, `skipped`,
`error`, `started_at`, `finished_at`). Khi `status == "completed"`, trả đúng số liệu cuối ví dụ:
`progress = 1000`, `total = 1000`, `assigned = {"staff.a@dev.local": 500, ...}`, `created = 1000`.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`, `NOT_FOUND`.

### 3.11 `crm_cskh.api.distribution.reconcile` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `campaign` | string | ✔ |

**Response 200**

```json
{
  "campaign": "…",
  "current_owners": { "staff.a@dev.local": 500 },
  "assignment_events": { "staff.a@dev.local": 520 },
  "distinct_leads": 1000,
  "active_work_items": 500,
  "active_claims": 500,
  "as_of": "…"
}
```

`current_owners` đếm từ `CSKH Work Item` (sự thật hiện tại), `assignment_events` đếm từ
`CSKH Assignment History` ⇒ **hai số khác nhau là bình thường** khi có chuyển giao
(P1 §12.5).

**Phạm vi của sự kiện.** `assignment_events` đếm **riêng** campaign được hỏi: sự kiện được
lấy từ `CSKH Assignment History` **nối** `CSKH Work Item` theo `work_item`, lọc
`wi.campaign = <campaign>`. Lịch sử của toàn hệ thống **không** được cộng vào, và một
campaign không có work item (hoặc không có run) trả **0** sự kiện — không phải lịch sử của
site. Cột của lịch sử là `to_user` (không phải `assigned_to`).

**Năm số đối soát riêng biệt** (P1 §12.5): `distinct_leads` (khách), `active_work_items`
(công việc), `active_claims` (claim), `current_owners` (chủ hiện tại) và
`assignment_events` (sự kiện kiểm toán) là năm con số **không suy ra được lẫn nhau**.

### 3.12 `crm_cskh.api.ownership.assign` — POST

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `work_item` | string | ✔ | |
| `target_user` | string | ✔ | Phải là `Sales User` enabled |
| `reason` | string | ✖ | Mặc định `Initial assignment` |
| `request_id` | string | ✔ | Khóa idempotency của event |

**Response 200:** `{ "work_item": "…", "status": "assigned", "owner": "staff.a@dev.local" }`
hoặc `{ "work_item": "…", "status": "already_applied", "event": "<history name>" }` khi retry.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST` (thiếu `request_id`, target không hợp lệ),
`ALREADY_ASSIGNED` (work item không `queued`), `CONFLICT`.

### 3.13 `crm_cskh.api.ownership.reassign` — POST

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `work_item` | string | ✔ | |
| `expected_owner` | string | ✖ | Nếu khác owner hiện tại ⇒ `CONFLICT` |
| `target_user` | string | ✔ | |
| `reason` | string | ✔ | Bắt buộc |
| `request_id` | string | ✔ | |

**Response 200:** `{ "work_item": "…", "status": "reassigned", "owner": "…",
"ledger": { "owner": "…", "shares": ["…"] } }`

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST` (thiếu `reason`/`request_id`),
`ALREADY_ASSIGNED`, `CONFLICT` (expected-owner lệch; còn share thừa sau giao dịch),
`CAPACITY_EXCEEDED`.

### 3.14 `crm_cskh.api.ownership.revoke` — POST

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `work_item` | string | ✔ | |
| `expected_owner` | string | ✖ | |
| `reason` | string | ✔ | |
| `request_id` | string | ✔ | |
| `return_to_pool` | bool \| 0/1 \| `"true"`/`"false"` | ✖ | Mặc định `1`/`true` → `status=queued`; `0`/`false`/`"false"`/`"0"` → `status=revoked` (`L-21`) |

**Response 200:** `{ "work_item": "…", "status": "queued"|"revoked", "owner": null }`

`return_to_pool` được parse chặt qua `crm_cskh.api._params.parse_bool` cho cả JSON lẫn
`application/x-www-form-urlencoded`: `true`/`"true"`/`1`/`"1"` → `True` (`queued`),
`false`/`"false"`/`0`/`"0"` → `False` (`revoked`), chuỗi lạ → HTTP `400 INVALID_REQUEST`.
Side-effect: `CRM Lead.lead_owner` = trống, `ToDo` cũ `Cancelled`, **mọi `DocShare` của lead
bị xoá**, `CSKH Active Claim` của lead bị release khi `status=revoked` (và giữ claim của campaign
với `owner=null` khi `status=queued`).

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`, `ALREADY_ASSIGNED`, `CONFLICT`.

### 3.15 `crm_cskh.api.ownership.history` — GET

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `work_item` | string | ✔ | Chuỗi không rỗng (`parse_optional_str`) |
| `limit` | int | ✖ | Mặc định `50`, miền hợp lệ `1..200` (`parse_int`, `L-26`) |

**Response 200**

```json
{
  "work_item": "…",
  "items": [ { "name": "…", "action": "assign", "from_user": null, "to_user": "staff.a@dev.local",
               "actor": "crm.admin@dev.local", "event_at": "…", "reason": "…",
               "distribution_run": null, "ownership_start": "…", "ownership_end": null,
               "event_id": "assign:<wi>:<request_id>" } ],
  "total": 3
}
```

**Lỗi:** `FORBIDDEN` (không có quyền read work item), `INVALID_REQUEST` (HTTP `400`, `code = "INVALID_REQUEST"`, `p1_code = "INVALID_REQUEST"` khi thiếu `work_item` hoặc `limit` không hợp lệ như `"abc"`, `"1.5"`, `true`/`false`, `<= 0`, `> 200` — tuyệt đối không để `int(...)` sinh HTTP `500`, `L-26`).

### 3.16 `crm_cskh.api.customers.my_list` — GET

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `page` | int | ✖ | Mặc định `1`, miền hợp lệ `1..1000000` (`parse_pagination`, `L-26`) |
| `page_size` | int | ✖ | Mặc định `20`, miền hợp lệ `1..100` (`parse_pagination`, `L-26`) |
| `status` | string | ✖ | Mặc định `assigned` |
| `campaign` | string | ✖ | Lọc campaign |

**Response 200**

```json
{
  "scope": "self",
  "items": [ { "work_item": "…", "lead": "CRM-LEAD-0001", "display_name": "…",
               "phone": "0900000010", "campaign": "…", "campaign_title": "…",
               "campaign_code": "…", "source": "Facebook", "lead_status": "New",
               "work_item_status": "assigned", "assigned_at": "…" } ],
  "total": 12,
  "page": 1,
  "page_size": 20,
  "as_of": "…"
}
```

`scope` luôn là `"self"` — danh sách của chính actor, không mở rộng được bằng tham số. Input `page`/`page_size` sai định dạng hoặc ngoài miền trả HTTP `400 INVALID_REQUEST` (`L-26`).

### 3.17 `crm_cskh.api.customers.pool_list` — GET

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `page` | int | ✖ | Mặc định `1`, miền hợp lệ `1..1000000` (`parse_pagination`, `L-26`) |
| `page_size` | int | ✖ | Mặc định `20`, miền hợp lệ `1..100` (`parse_pagination`, `L-26`) |
| `campaign` | string | ✖ | Lọc theo chiến dịch |
| `batch` | string | ✖ | Lọc theo `CSKH Import Batch` (qua cả `wi.import_batch` lẫn `CSKH Import Batch Lead` — `L-25`) |

**Response 200**

```json
{
  "scope": "system"|"all"|"team",
  "items": [ { "work_item": "…", "lead": "…", "display_name": "…", "phone": "…", "campaign": "…" } ],
  "total": 87,
  "page": 1,
  "page_size": 20,
  "as_of": "…"
}
```

Pool là work item `status='queued'` **và** `assigned_to is null`; scope của leader áp qua
`team_scope` của campaign (trạng thái phụ `_pool_state` cũng được giới hạn trong scope của actor, không rò owner/campaign ngoài team); `CSKH BOD Viewer` được che `phone = null` và `display_name = ""`. Input `page`/`page_size` không hợp lệ trả HTTP `400 INVALID_REQUEST` (`L-26`).

### 3.17b `crm_cskh.api.customers.customer_pool` — GET (`L-23`, `L-25`, `L-26`)

Endpoint kho khách hàng đầy đủ cho màn hình `/cskh/pool` (dùng chung 100% policy quyền, SQL phân trang `LIMIT`/`OFFSET` và response shape với `crm_cskh.api.pool_ui.list` của PR #5):

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `page` | int | ✖ | Mặc định `1`, miền hợp lệ `1..1000000` (`parse_pagination`, `L-26`) |
| `page_size` | int | ✖ | Mặc định `20`, miền hợp lệ `1..100` (`parse_pagination`, `L-26`) |
| `campaign` | string | ✖ | Lọc theo chiến dịch (`derived_campaign`) |
| `state` | string | ✖ | `eligible` / `queued` / `assigned` / `conflict` / `revoked` / `closed` / `invalid` |
| `source` | string | ✖ | Lọc `CRM Lead.source` |
| `batch` | string | ✖ | Lọc theo `CSKH Import Batch` qua `CSKH Import Batch Lead` (kể cả khách chưa có `Work Item` và khách được reuse qua nhiều batch) hoặc `CSKH Distribution Run` |
| `search` | string | ✖ | Tìm theo tên/SĐT (`0...` hoặc `+84...`)/mã lead (bị từ chối `403 FORBIDDEN` đối với `CSKH BOD Viewer`) |

**Response 200**

```json
{
  "scope": "system"|"all"|"team",
  "items": [
    {
      "work_item": null,
      "lead": "CRM-LEAD-2026-00001",
      "display_name": "Nguyễn Thị Mai",
      "phone": "+84900000010",
      "source": "Zalo Official",
      "campaign": "",
      "campaign_title": "",
      "state": "eligible",
      "owner": "",
      "created_at": "2026-11-03 10:00:00.000000"
    }
  ],
  "total": 1,
  "unique_customers": 1,
  "work_items": 0,
  "page": 1,
  "page_size": 20,
  "as_of": "…"
}
```

- Với `CSKH CRM Admin` (`scope = "system"`) và `CSKH BOD Viewer` (`scope = "all"`): hiển thị đầy đủ cả khách mới import chưa có `Work Item` (`work_item = null`, `state = "eligible"`), và với `CSKH BOD Viewer` luôn ẩn `phone = null`, `display_name = ""`.
- Với `Sales Manager` (`scope = "team"`, **`L-23`**): áp đồng thời `_team_lead_authorization_clauses`, `_scoped_wi_predicate` và `_scoped_ac_predicate`:
  1. Lead phải thuộc phạm vi team hiện tại (có `Work Item` trong scope team hoặc `lead_owner ∈ team_users`);
  2. Lead **không** có bất kỳ `Work Item` `assigned` nào ngoài `team_users`;
  3. Lead **không** có `Work Item` `queued` trong campaign riêng (`team_scope IS NOT NULL`) ngoài `team_nodes`;
  4. Lead **không** có `CSKH Active Claim` ngoài `team_nodes` / `team_users`;
  5. `CRM Lead.lead_owner` trống / `'Administrator'` hoặc thuộc `team_users`.
  Nhờ đó lịch sử `revoked`/`closed` cũ tại Đội A **tuyệt đối không** cho phép `Leader A` thấy khách đang active/queued/claimed/owned ở Đội B qua bất kỳ đường `items`, `total`, `unique_customers`, `work_items`, `search`, `campaign`, `source` hay `batch`.
- Với `Sales User`: từ chối `403 FORBIDDEN`.
- Input `page`/`page_size`/`state` không hợp lệ trả ổn định HTTP `400 INVALID_REQUEST` (`L-26`).

### 3.18 `crm_cskh.api.dashboard.distribution` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `campaign` | string | ✖ |
| `date_from` | string | ✖ |
| `date_to` | string | ✖ |

**Response 200**

```json
{
  "scope": "system"|"all"|"team"|"self",
  "cards": {
    "unique_customers": 1000,
    "eligible_unassigned": 87,
    "assigned_work_items": 1000,
    "unassigned_work_items": 87,
    "revoked_work_items": 3,
    "closed_work_items": 0,
    "active_claims": 1000,
    "active_campaigns": 2,
    "failed_batches": 0,
    "failed_runs": 0
  },
  "per_user": [ { "user": "staff.a@dev.local", "weight": 50, "current_total": 500,
                   "capacity": null, "remaining_capacity": null } ],
  "assignment_events": 1003,
  "identity_count": 1200,
  "as_of": "…"
}
```

- `unique_customers` = `count(distinct lead)` của work item trong scope (P1 §12.5).
- `assigned_work_items` / `unassigned_work_items` / `revoked_work_items` / `closed_work_items`
  là **work items**, không phải khách.
- `assignment_events` đếm từ history ⇒ **lớn hơn** `assigned_work_items` khi có chuyển giao.
- `identity_count` = số dòng `CSKH Identity Index` (không lộ số điện thoại).
- Mọi card đều **đã áp scope** của actor trong SQL ⇒ leader chỉ thấy nhánh của mình (SEC-04).

**Phạm vi (scope) của từng card.** Không có card nào là phép đếm toàn hệ thống:

| Card / biến | Nguồn | Cách áp scope |
|---|---|---|
| `unique_customers`, `assigned_work_items`, `unassigned_work_items`, `revoked_work_items`, `closed_work_items` | `CSKH Work Item` | Thống nhất qua `_work_item_scope_clause`: `assigned_to ∈ scope` **hoặc** (`assigned_to IS NULL` và `campaign` thuộc campaign đọc được của Leader — `L-22`); `unique_customers = COUNT(DISTINCT wi.lead)` đồng nhất cho mọi vai trò |
| `active_claims` | `CSKH Active Claim` ⋈ `CSKH Work Item` | Theo `_work_item_scope_clause` của work item bảo vệ claim (bao gồm cả hàng `queued` chưa gán người trong campaign của Leader — `L-22`) |
| `active_campaigns` | `CSKH Campaign` | `team_scope` của campaign ∈ scope (campaign chung `team_scope = null` luôn thấy) |
| `failed_batches` | `CSKH Import Batch` | `owner` của batch ∈ scope |
| `failed_runs` | `CSKH Distribution Run` | `campaign` của run ∈ scope |
| `per_user` | `CSKH Work Item` group by `(assigned_to, campaign)` | chỉ user trong scope; weight/capacity đọc theo **từng (campaign, user)** |
| `assignment_events` | `CSKH Assignment History` ⋈ `CSKH Work Item` | work item của campaign / của scope |
| `identity_count` | `CSKH Identity Index` ⋈ lead qua `CSKH Work Item` | chỉ identity của lead trong scope |

**Bộ lọc.** `campaign` lọc **mọi** card và mọi dòng `per_user`. `date_from` / `date_to` lọc
theo `CSKH Work Item.creation` và **cũng** áp cho `assignment_events` (theo `event_at`) và
`identity_count`. Một bộ lọc **được nhận** thì **phải** được áp — không có trường hợp nhận
rồi bỏ qua.

**Ngữ nghĩa capacity trong `per_user` (P1 §7.2).** `capacity_limit = 0` là **không giới hạn**:

* `capacity` và `remaining_capacity` là `null` khi người dùng có ít nhất một campaign không
  giới hạn;
* nếu không, `capacity` là **tổng** capacity của các campaign mà người dùng đang hoạt động,
  và `remaining_capacity = capacity - current_total` (có thể âm khi vượt ⇒ dữ liệu bất thường);
* `campaigns[]` là chi tiết **theo từng campaign**: `{campaign, weight, current_total,
  capacity, remaining_capacity}` với `capacity = null` cho campaign không giới hạn.

Vì rule gắn **một (campaign, user)**, không thể gộp bằng `max(capacity_limit)` qua các
campaign: một campaign không giới hạn không được bị campaign khác giới hạn làm mất.

`scope` trả một trong `system` | `all` | `team` | `self`: `system` = `CSKH CRM Admin` (quyền nghiệp vụ P1, **không** cần cấp `System Manager` — `L-17`) hoặc `Administrator`/`System Manager`, `all` = `CSKH BOD Viewer` (aggregate toàn hệ thống, **không** PII), `team` = leader đọc được cây con của mình, `self` = staff chỉ thấy phần của mình.

### 3.19 `crm_cskh.api.dashboard.campaigns` — GET

**Response 200**

```json
{
  "campaigns": [ { "campaign": "…", "code": "…", "title": "…", "status": "Active",
                   "rule_version": 3, "assigned": 1000, "queued": 87 } ],
  "as_of": "…"
}
```

---

## 4. Ma trận quyền theo endpoint (ADR-P1-07)

| Endpoint | `Sales User` | `Sales Manager` | `CSKH CRM Admin` | `CSKH BOD Viewer` |
|---|---|---|---|---|
| `imports.*` | ❌ | ❌ | ✔ | ❌ |
| `campaigns.list` / `rules` | ❌ | ✔ (trong team) | ✔ | ✔ |
| `campaigns.save` / `save_rule` | ❌ | ❌ | ✔ | ❌ |
| `distribution.preview` / `execute` / `reconcile` | ❌ | ❌ | ✔ | ❌ |
| `distribution.get_run` | ❌ | ✔ (trong team) | ✔ | ✔ |
| `ownership.assign` / `reassign` / `revoke` | ❌ | ❌ | ✔ | ❌ |
| `ownership.history` | ✔ (work item của mình) | ✔ (trong team) | ✔ | ✔ |
| `customers.my_list` | ✔ (của mình) | ✔ (của mình) | ✔ | ✔ |
| `customers.pool_list` / `customers.customer_pool` | ❌ | ✔ (trong team) | ✔ | ✔ (ẩn PII) |
| `dashboard.distribution` / `campaigns` | ❌ | ✔ (trong team) | ✔ | ✔ (aggregate toàn hệ thống) |

> `CSKH BOD Viewer` **không** có `System Manager` và **không** đọc được `CRM Lead` role-wide.
> Dashboard BOD là aggregate; drill-down PII đi qua `customers.my_list` với scope của actor.

---

## 5. Bootstrap prerequisites

Các role nghiệp vụ **không** nằm trong repo dưới dạng DocType `Role`; chúng được seed bởi
`deploy/dev/seed/seed_demo.py`. Trên site mới, cần:

1. Tạo role `CSKH CRM Admin`, `CSKH BOD Viewer` (`desk_access=1`).
2. Gán role cho user (CRM Admin cho import/campaign/distribution/ownership; BOD Viewer cho
   dashboard).
3. Bật `FCRM Settings.enable_sales_hierarchy = 1` và có cây `CRM Sales Hierarchy` để scope
   leader hoạt động.
4. `bench --site <site> migrate` để tạo constraint/index của 8 DocType P1.

Nếu chưa seed role, endpoint P1 **fail-closed** với `FORBIDDEN` — đây là hành vi đúng, không
phải lỗi.

---

## 6. Giới hạn đã biết

1. **Chưa có endpoint download file lỗi.** `error_artifact` trả **tên** `File`; UI tải qua
   route `File` chuẩn của Frappe (file private ⇒ cần quyền đọc file).
2. **Chưa có `PUT`/`DELETE` generic cho campaign/rule.** Tất cả qua `campaigns.save*`.
3. **Bộ lọc ngày của dashboard lọc theo `creation` của work item** (`date_from` / `date_to`),
   còn `assignment_events` lọc theo `event_at` của sự kiện. Hai cột khác nhau theo thiết kế
   (sự kiện diễn ra sau khi công việc được tạo); UI nên hiển thị rõ phạm vi.
4. **Chưa có pagination sâu** (`offset` không có cursor).
5. **Chưa có bulk operation** cho assign/reassign (P1 §10.1 cấm nhận danh sách ID tuỳ ý).
6. **Thông báo là tác dụng phụ sau commit.** P1 §7.5 yêu cầu thông báo chỉ chạy **sau
   commit** và không coi việc giao thông báo thành điều kiện lưu assignment. Vì vậy
   `distribution.execute` và `ownership.assign` **luôn** trả về success khi assignment đã
   commit, kể cả khi thông báo thất bại — UI không được coi là lỗi nghiệp vụ. Thông báo là
   idempotent (`ToDo` và `CRM Notification` được tra cứu trước khi tạo), nên retry là an toàn.
7. **Một run `completed_with_errors` / `failed` hoặc hết hạn lease có thể resume.** Gọi lại
   `distribution.execute` với cùng `preview_id` sẽ khóa dòng run, kiểm tra lại `rule_version`,
   đưa run về `queued` để worker **tiếp tục** những dòng chưa được giao, cộng dồn đúng số lượng
   `assigned` theo từng user từ DB, giữ nguyên tỉ lệ fairness của các dòng đã commit và **không**
   tạo thêm work item / claim / event cho các dòng đã giao.
8. **Thông lượng thực thi nền của worker phân phối lớn (`distribution_worker`).** `POST distribution.execute`
   qua HTTP hiện chỉ làm nhiệm vụ kiểm tra + khóa dòng + enqueue vào RQ `long` nên phản hồi rất nhanh
   (`~0,22s` đo thực tế), không còn rủi ro chạm timeout HTTP `60–120s`. Ở phía nền, worker xử lý từng
   Work Item qua đầy đủ vòng đời Frappe ORM với tốc độ thực tế `~2,0–2,1 items/s` (`~80–81 phút` cho
   10.000 Work Item trên máy DEV, `CHUNK_SIZE = 50`), nên UI cần hiển thị thanh tiến độ `progress / total`
   qua `distribution.get_run`.

### 6.1. Ghi chú bàn giao thay đổi contract cho T1A (UI)

Revision xử lý `L-16..L-22` và chuyển **phân phối sang chạy nền bằng RQ** giữ nguyên toàn bộ 19 dotted path
và tham số request, đồng thời bổ sung các cập nhật contract sau để **T1A** tích hợp UI:

1. **Chuyển `POST crm_cskh.api.distribution.execute` sang bất đồng bộ qua RQ + polling `get_run` (QUAN TRỌNG):**
   - `POST distribution.execute` **không còn chặn đồng bộ đến khi giao xong toàn bộ danh sách**. API trả
     ngay HTTP `200` với `status = "queued"` (hoặc trạng thái hiện tại nếu double-submit), kèm các trường
     mới trong payload của cả `execute` lẫn `get_run`:
     - `status`: `"planned" | "queued" | "running" | "completed" | "completed_with_errors" | "failed"`
     - `total`: tổng số khách mục tiêu của đợt phân phối (`int`)
     - `progress`: số dòng đã xử lý và checkpoint xuống DB (`int`, tăng theo từng chunk `50` dòng)
     - `assigned`: `dict[user, count]` cộng dồn trực tiếp từ DB tại thời điểm poll
     - `created`: tổng số Work Item đã giao thành công tính đến hiện tại (`int`)
     - `errors`, `skipped`: số lỗi và danh sách dòng bị bỏ qua
     - `error`: chuỗi mã lỗi ổn định nếu worker dừng sớm do recheck (`"FORBIDDEN: ..."`, `"RULE_CHANGED: ..."`) hoặc `null`.
   - **Hành động phía UI (T1A):** Sau khi gọi `POST distribution.execute`, nếu `status` là `"queued"` hoặc
     `"running"`, UI **phải poll `GET crm_cskh.api.distribution.get_run?run_id=<run_id>`** (ví dụ mỗi `1,5–3s`),
     hiển thị tiến độ `progress / total`, khóa nút bấm lặp (server cũng đã chống double-submit), và dừng poll
     khi `status` đạt `"completed"`, `"completed_with_errors"`, hoặc `"failed"`.
2. **Envelope mã lỗi ổn định trên HTTP `4xx` (`L-16`):** Mọi lỗi `P1Error` trả qua HTTP hiện có sẵn trường
   top-level `code` và `p1_code` (ví dụ `"FORBIDDEN"`, `"INVALID_REQUEST"`, `"RULE_CHANGED"`, `"NOT_FOUND"`,
   `"CAPACITY_EXCEEDED"`). UI có thể đọc trực tiếp `res.code` (hoặc `res.p1_code`) thay vì dò chuỗi message.
   Các route `File` trực tiếp vẫn trả `404` đồng nhất theo bảo vệ File-oracle P0.
3. **Confirm import lần đầu (`L-18`) & lô > 200 dòng (`L-19`, `L-20`):** `POST imports.confirm` chuyển thẳng
   batch sang `status = "QUEUED"` ngay ở lần gọi đầu tiên và tự chạy RQ worker nền; `create_preview` vẫn cắt
   mảng `preview` ở 200 dòng (`preview_truncated = true`) nhưng worker xử lý đủ 100% dòng của file và xuất đủ
   mọi dòng lỗi (kể cả sau dòng 200) vào `error_artifact` mà không đếm đúp `totals.error_rows`.
4. **Parse boolean chặt (`L-21`):** `ownership.revoke(return_to_pool=...)` và `campaigns.save_rule(enabled=...)`
   nhận nhất quán `true`/`"true"`/`1`/`"1"` và `false`/`"false"`/`0`/`"0"` trên cả JSON lẫn form-urlencoded.
5. **Scope `CRM-ADMIN` & Dashboard `Leader` (`L-17`, `L-22`):** Tài khoản `CSKH CRM Admin` nhận `scope = "system"`
   trên `dashboard.distribution`, `dashboard.campaigns`, `customers.pool_list` mà không cần `System Manager`;
   `Leader` (`scope = "team"`) đếm đầy đủ `revoked_work_items`, `closed_work_items`, `active_claims` của các
   Work Item chưa gán người trong campaign thuộc team, và `unique_customers` thống nhất là `COUNT(DISTINCT lead)`
   trên toàn bộ Work Item trong scope.

### 6.2. Bàn giao `L-25` để T1A tích hợp vào PR #5 (`codex/p1-ui`)

Để tránh xung đột merge `add/add` với PR #5, PR #4 **không** tạo file `apps/crm_cskh/crm_cskh/api/pool_ui.py` mà cung cấp sẵn toàn bộ logic backend trong [`services/reporting.py`](../../apps/crm_cskh/crm_cskh/services/reporting.py) và [`api/customers.py`](../../apps/crm_cskh/crm_cskh/api/customers.py):

1. **Màn hình Import (`import.js` trên PR #5):**
   - `import.js` hiện đã gửi `mapping: { phone, name, source }` tới `POST crm_cskh.api.imports.create_preview`. Sau khi merge PR #4, backend tự động áp `name` / `source` (đã sanitize formula/HTML) khi tạo `CRM Lead` mới, chỉ gán `"Chưa rõ tên"` (`cskh_is_placeholder_name = 1`) khi thiếu tên hợp lệ, không ghi đè `CRM Lead` canonical đã tồn tại, và ghi đầy đủ `CSKH Import Batch Lead`.
   - Response của `create_preview` / `confirm` / `get_status` nay có thêm `mapping` và `resolved_mapping` (`{"phone": ..., "name": ..., "source": ...}`) cùng các trường preview `name` / `source` đã sanitize.
   - Lưu ý: nếu tái sử dụng cùng `idempotency_key` nhưng người dùng đổi `mapping`, API trả `409 CONFLICT` (`code = "CONFLICT"`). Nếu để trống `idempotency_key`, backend tự băm `mapping` vào key mặc định nên đổi cột map trên cùng file sẽ tạo preview batch mới mượt mà.
2. **Màn hình Kho khách hàng (`pool_ui.py` / `pool.js` trên PR #5):**
   - Trong `apps/crm_cskh/crm_cskh/api/pool_ui.py` của PR #5, hàm `list(...)` trước đây chỉ lọc `wi.import_batch = %s` nên bỏ sót hoàn toàn khách mới import chưa có `Work Item` (`state == "eligible"`) và khách canonical được reuse ở batch sau.
   - Sau khi merge PR #4 vào PR #5, **T1A chỉ cần ủy quyền trực tiếp `pool_ui.list` sang `reporting.customer_pool`** (cùng chữ ký tham số và cùng payload 100%, giữ nguyên `pool_ui.filter_options`):
     ```python
     @frappe.whitelist(methods=["GET"])
     def list(
         page=1,
         page_size=20,
         campaign=None,
         state=None,
         source=None,
         batch=None,
         search=None,
     ):
         return reporting.customer_pool(
             user=frappe.session.user,
             page=page,
             page_size=page_size,
             campaign=campaign,
             state=state,
             source=source,
             batch=batch,
             search=search,
         )
     ```
   - Hoặc nếu giữ SQL riêng trong `pool_ui.py`, có thể gọi trực tiếp 2 helper đã export sẵn tại `crm_cskh.services.reporting`: `lead_in_batch_clause(batch, lead_expr="l.name")` (nhánh Admin/BOD) và `work_item_in_batch_clause(batch, alias="wi")` (nhánh Leader).
3. **Migration khi cập nhật môi trường tích hợp:** Chạy `bench --site <site> migrate` (tạo bảng + index `tabCSKH Import Batch Lead`) và chạy lại seed phân quyền (`set_p1_permissions.py` / `seed_demo.py`) nếu site đã tạo trước đó.

### 6.3. Bàn giao đồng bộ `L-23` & `L-26` trên Backend để T1A tích hợp vào PR #5

1. **`L-23` tại `reporting.customer_pool` và `customers.customer_pool`:**
   - [`services/reporting.py`](../../apps/crm_cskh/crm_cskh/services/reporting.py) đã bổ sung đầy đủ `_scoped_wi_predicate`, `_scoped_ac_predicate`, `_team_lead_authorization_clauses`, `filter_team_authorized_leads` và cập nhật `reporting.customer_pool` phân trang trực tiếp bằng SQL (`LIMIT`/`OFFSET`), dùng **đúng 100% policy quyền hiện hành của `pool_ui.list` trên PR #5 (`3f07b9b`)**.
   - Khi merge vào PR #5: không có bất kỳ xung đột file nào với `pool_ui.py` hoặc `_ui_params.py`; cả đường gọi UI (`pool_ui.list`) lẫn đường gọi trực tiếp backend (`customers.customer_pool` / `reporting.customer_pool`) đều bảo vệ chặt `L-23`. (Tùy chọn: `pool_ui.py` có thể giữ nguyên hoặc tái sử dụng trực tiếp các hàm `reporting._team_lead_authorization_clauses` / `reporting.filter_team_authorized_leads` / `reporting.customer_pool`).
2. **`L-26` trên các đường Backend (`api/_params.py`, `ownership.history`, `customers.pool_list`, `customers.customer_pool`, `customers.my_list`):**
   - [`api/_params.py`](../../apps/crm_cskh/crm_cskh/api/_params.py) đã bổ sung `parse_int`, `parse_pagination` (`page ∈ 1..1_000_000`, `page_size ∈ 1..100`) và `parse_optional_str` (cùng quy ước với `_ui_params.py` của PR #5: từ chối `bool`, số thực `"1.5"`, chuỗi chữ `"abc"`, `<= 0`, vượt trần bằng `InvalidRequest` → HTTP `400` kèm `code = "INVALID_REQUEST"`, `p1_code = "INVALID_REQUEST"`).
   - Áp dụng trực tiếp tại cả tầng API và tầng service cho `ownership.history(limit ∈ 1..200)`, `customers.pool_list`, `customers.customer_pool`, `customers.my_list` (cùng `campaigns.save_rule` `version` và `distribution.enqueue_run` `expected_rule_version`).

---

## 7. Bằng chứng runtime đã kiểm chứng

Các hành vi được ghi trong tài liệu này đã được kiểm chứng bằng **runtime**, tách biệt rõ
giữa lời gọi hàm trong tiến trình test (`service_unit_test`) và giao vận HTTP thật (`http`):

| Hạng mục | Lớp bằng chứng | Phạm vi kiểm chứng | Kết quả | Bằng chứng |
|---|---|---|---|---|
| Suite P1 (8 module) | `service_unit_test` | Per-method JUnit gate trên inventory P1 riêng (`205` method) | **205/205 PASS** | [`evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json) |
| Chaos / rollback / retry (unit + child process) | `service_unit_test` + `os_process_crash` | `unittest.mock.patch` + tiến trình con `os._exit(137)` cho cả `import_worker` và `distribution_worker`; 0 nhánh gây lỗi trong code production | **11/11 PASS** (unit) & **4/4 PASS** (harness) | [`evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml), [`evidence/p1-runtime-20261102/chaos/p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json) |
| HTTP transport P1 + RQ workers | `http` | HTTP thật tới `bench serve` + RQ worker `short`/`long` thật, cookie phiên + CSRF thật, 6 actor đã seed (`Admin`, `BOD`, `Leader 1/2`, `Staff A/B`) + `Guest`, đối chiếu SQL trực tiếp cho `L-16..L-23`, `L-25`, `L-26` và phân phối nền RQ | **103/103 PASS** | [`evidence/p1-runtime-20261102/http/p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) |
| Benchmark 10k import + 10k phân phối + RQ enqueue | `runtime_benchmark` | 2 đợt × 10.000 dòng (`0 → 10k`, `10k → 20k`) + lô hỗn hợp & RQ capacity `30/42/28` (`api_enqueue_seconds = 0,222s`) trên `p1bench.localhost` | **Số đo thực tế (0 lỗi, 0 trùng)** | [`evidence/p1-runtime-20261102/benchmark/`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/) |

> **Giới hạn:** probe HTTP phủ giao vận trực tiếp tới `bench serve` của Frappe; tầng reverse
> proxy (Nginx/TLS) và CORS giữa các origin **không** nằm trong phạm vi của probe này.

---

## 8. Nguồn

- P1 §8.1, §8.2, §8.3, §9.1, §9.4, §9.5, §10, §12.4, §12.5, §16
- [`schema-p1.md`](./schema-p1.md) — 8 DocType, constraint, index
- [`adr-p1-03-import-engine.md`](./adr-p1-03-import-engine.md),
  [`adr-p1-04-weighted-fair.md`](./adr-p1-04-weighted-fair.md),
  [`adr-p1-05-ownership-sync.md`](./adr-p1-05-ownership-sync.md),
  [`adr-p1-07-rbac.md`](./adr-p1-07-rbac.md)
- Source: `apps/crm_cskh/crm_cskh/api/*.py`, `apps/crm_cskh/crm_cskh/services/*.py`
