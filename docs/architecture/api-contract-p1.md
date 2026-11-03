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
| 9 | POST | `crm_cskh.api.distribution.execute` | `CSKH CRM Admin` | Thực thi preview (hỗ trợ resume khi partial failure) |
| 10 | GET | `crm_cskh.api.distribution.get_run` | `CSKH CRM Admin` / `CSKH BOD Viewer` | Tiến độ / kết quả run |
| 11 | GET | `crm_cskh.api.distribution.reconcile` | `CSKH CRM Admin` | Đối soát state với DB |
| 12 | POST | `crm_cskh.api.ownership.assign` | `CSKH CRM Admin` | Giao work item queued |
| 13 | POST | `crm_cskh.api.ownership.reassign` | `CSKH CRM Admin` | Chuyển owner (expected-owner) |
| 14 | POST | `crm_cskh.api.ownership.revoke` | `CSKH CRM Admin` | Thu hồi owner, trả về kho |
| 15 | GET | `crm_cskh.api.ownership.history` | theo quyền read của work item | Lịch sử append-only |
| 16 | GET | `crm_cskh.api.customers.my_list` | `Sales User` / `Sales Manager` / `CSKH CRM Admin` / `CSKH BOD Viewer` | Khách đang được giao |
| 17 | GET | `crm_cskh.api.customers.pool_list` | `Sales Manager` / `CSKH CRM Admin` / `CSKH BOD Viewer` | Kho chưa giao trong scope |
| 18 | GET | `crm_cskh.api.dashboard.distribution` | `CSKH BOD Viewer` / `CSKH CRM Admin` / `Sales Manager` | Dashboard BOD (aggregate) |
| 19 | GET | `crm_cskh.api.dashboard.campaigns` | `CSKH BOD Viewer` / `CSKH CRM Admin` / `Sales Manager` | Bộ lọc campaign của dashboard |

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

**Envelope lỗi** (Frappe chuẩn, đã chuẩn hóa qua `crm_cskh.errors.normalize_error_response`):

```json
{
  "exc_type": "PermissionError",
  "message": "Distributing the customers requires the CRM Admin business role.",
  "_server_messages": "[\"Distributing the customers requires the CRM Admin business role.\"]"
}
```

`code` ổn định được nhúng trong **message** và, khi ném từ service, trong
`P1Error.as_dict()`. UI **phải** map theo message đã map sẵn ở tầng client, không theo chuỗi
tiếng Việt.

---

## 3. Endpoint chi tiết

### 3.1 `crm_cskh.api.imports.create_preview` — POST

**Vai trò:** `CSKH CRM Admin` (hoặc `System Manager`).

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `file_name` | string | ✔ | Tên `File` đã upload (bắt buộc `is_private`) |
| `mapping` | object \| string | ✖ | `{field: tên_cột}`; field chuẩn của số điện thoại là `phone` |
| `idempotency_key` | string | ✖ | Mặc định `import:{sha256(file)}:{import_mode}` |
| `import_mode` | string | ✖ | `standard` (mặc định) |

**Response 200**

```json
{
  "batch": "3f9c1e2a4b5c6d7e8f90",
  "status": "DRY_RUN",
  "checksum": "…sha256…",
  "idempotency_key": "import:…",
  "totals": {
    "total_rows": 1200,
    "valid_rows": 1000,
    "new_rows": 980,
    "duplicate_rows": 200,
    "error_rows": 20
  },
  "preview": [ { "row_number": 3, "class": "valid", "canonical": "+84900000010" } ],
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

**`mapping` ánh xạ *tên cột*, không phải *giá trị ô*.** Giá trị của `mapping[phone]` là
**tên** của cột trong file (ví dụ `{"phone": "Số điện thoại"}`), không phải số điện thoại.
Khi `mapping` có nhiều field, cột của field chuẩn `phone` được ưu tiên; nếu `mapping`
không có field `phone`, chỉ một cột đã map mà **tên** của nó là một header điện thoại
được nhận, và nếu không có cột nào như vậy thì pipeline dùng header điện thoại của file.
Preview và worker dùng **cùng** quy tắc này và **cùng** giới hạn/validation, nên một file
mà preview chấp nhận thì worker không thể từ chối.

**Checksum.** `checksum` là SHA-256 của **nội dung file**, đã chuẩn hóa về `bytes` trước khi
băm: `File.get_content()` trả `str` khi file đã flush xuống disk và `bytes` khi file còn
trong bộ nhớ. Cùng một file ⇒ cùng `checksum` ⇒ cùng `idempotency_key` ⇒ cùng `batch`.

**Chuẩn hóa số điện thoại.** `0912345678`, `84912345678` và `+84912345678` là **cùng một**
khách hàng: canonical E.164 `+84912345678`. Ô trống, ô chỉ có số, và ô ghi bằng ký hiệu
khoa học (`9.12345678E+08`) là **dòng lỗi**, không phải một khách hàng.

**Idempotency của import.** Import lại cùng một file **không** đổi `owner`, `status` hay
ghi chú của lead đã có, và **không** tự phân phối lead đó.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST` (file không tồn tại / không private),
`UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE`, `TOO_MANY_ROWS`, `CONFLICT`
(cùng `idempotency_key` nhưng checksum khác).

> **Không** dùng endpoint này để upload file. File được upload qua `File` API chuẩn của
> Frappe với `is_private=1`; server từ chối file không private.

### 3.2 `crm_cskh.api.imports.confirm` — POST

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `batch` | string | ✔ |

**Response 200:** giống shape `get_status` với `status` = `QUEUED` (worker được enqueue
qua `frappe.enqueue(..., enqueue_after_commit=True)`).

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
| `enabled` | 0/1 | ✖ | mặc định 1 |
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

### 3.9 `crm_cskh.api.distribution.execute` — POST

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `preview_id` | string | ✔ | `preview_id` từ `preview` |
| `expected_rule_version` | int | ✖ | Nếu khác version hiện tại ⇒ `RULE_CHANGED` |
| `idempotency_key` | string | ✖ | Ghi đè key của run |

**Response 200** (shape của `get_run`)

```json
{
  "run_id": "9a8b7c…",
  "campaign": "…",
  "status": "completed",
  "rule_version": 3,
  "selection_fingerprint": "…",
  "assigned": { "staff.a@dev.local": 500, "staff.b@dev.local": 300, "staff.c@dev.local": 200 },
  "created": 1000,
  "errors": 0,
  "skipped": [],
  "started_at": "…",
  "finished_at": "…"
}
```

`status` = `completed` | `completed_with_errors` (khi `errors > 0` ⇒ partial success).
`skipped` là danh sách `{lead, reason, detail}` với `reason` ∈ `no_eligible_assignee` |
`assignment_failed`.

**Resume an toàn và thông báo sau commit:**
- Khi run đã `completed`: gọi lại `execute` với cùng `preview_id` trả kết quả đã lưu và đồng bộ
  idempotent các thông báo (`CRM Notification` + `ToDo`) còn thiếu nếu bước thông báo sau commit
  của lần trước bị lỗi.
- Khi run ở trạng thái `completed_with_errors`: gọi lại `execute` với cùng `preview_id` sẽ khóa
  dòng run (`SELECT ... FOR UPDATE`), kiểm tra lại `rule_version` (nếu đổi ⇒ `RULE_CHANGED`),
  chỉ thực thi các lead chưa được giao, cộng dồn chính xác `assigned` theo từng user
  (`previous + resumed`) và giữ nguyên các mục `no_eligible_assignee` vĩnh viễn.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`, `NOT_FOUND`, `RULE_CHANGED`,
`PREVIEW_EXPIRED`, `CONFLICT` (run đang chạy / bị executor khác giữ).

### 3.10 `crm_cskh.api.distribution.get_run` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `run_id` | string | ✔ |

**Response 200:** shape như §3.9.

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
| `return_to_pool` | 0/1 | ✖ | Mặc định `1` → `status=queued`; `0` → `status=revoked` |

**Response 200:** `{ "work_item": "…", "status": "queued"|"revoked", "owner": null }`

Side-effect: `CRM Lead.lead_owner` = trống, `ToDo` cũ `Cancelled`, **mọi `DocShare` của lead
bị xoá**, `CSKH Active Claim` của lead bị release.

**Lỗi:** `FORBIDDEN`, `INVALID_REQUEST`, `ALREADY_ASSIGNED`, `CONFLICT`.

### 3.15 `crm_cskh.api.ownership.history` — GET

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `work_item` | string | ✔ | |
| `limit` | int | ✖ | Mặc định 50, tối đa 200 |

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

**Lỗi:** `FORBIDDEN` (không có quyền read work item), `INVALID_REQUEST`.

### 3.16 `crm_cskh.api.customers.my_list` — GET

| Tham số | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `page` | int | ✖ | Mặc định 1 |
| `page_size` | int | ✖ | Mặc định 20, tối đa 100 |
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

`scope` luôn là `"self"` — danh sách của chính actor, không mở rộng được bằng tham số.

### 3.17 `crm_cskh.api.customers.pool_list` — GET

| Tham số | Kiểu | Bắt buộc |
|---|---|---|
| `page` | int | ✖ |
| `page_size` | int | ✖ |
| `campaign` | string | ✖ |

**Response 200**

```json
{
  "scope": "system"|"all"|"team"|"self",
  "items": [ { "work_item": "…", "lead": "…", "display_name": "…", "phone": "…", "campaign": "…" } ],
  "total": 87,
  "page": 1,
  "page_size": 20,
  "as_of": "…"
}
```

Pool là work item `status='queued'` **và** `assigned_to is null`; scope của leader áp qua
`team_scope` của campaign.

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
| `unique_customers`, `assigned_work_items`, `unassigned_work_items`, `revoked_work_items`, `closed_work_items` | `CSKH Work Item` | `assigned_to ∈ scope` (với hàng chưa giao thì scope theo `campaign.team_scope`) |
| `active_claims` | `CSKH Active Claim` ⋈ `CSKH Work Item` | theo work item bảo vệ claim |
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

`scope` trả một trong `system` | `all` | `team` | `self`: `system` = Administrator/System
Manager, `all` = `CSKH BOD Viewer` (aggregate toàn hệ thống, **không** PII), `team` =
leader đọc được cây con của mình, `self` = staff chỉ thấy phần của mình.

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
| `distribution.get_run` | ❌ | ❌ | ✔ | ✔ |
| `ownership.assign` / `reassign` / `revoke` | ❌ | ❌ | ✔ | ❌ |
| `ownership.history` | ✔ (work item của mình) | ✔ (trong team) | ✔ | ✔ |
| `customers.my_list` | ✔ (của mình) | ✔ (của mình) | ✔ | ✔ |
| `customers.pool_list` | ❌ | ✔ (trong team) | ✔ | ✔ |
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
7. **Một run `completed_with_errors` có thể resume.** Gọi lại `distribution.execute`
   với cùng `preview_id` sẽ **tiếp tục** những dòng chưa được giao, cộng dồn đúng số lượng
   `assigned` theo từng user, giữ nguyên tỉ lệ fairness của các dòng đã commit và **không**
   tạo thêm work item / claim / event cho các dòng đã giao.
8. **`distribution.execute` hiện chạy đồng bộ theo từng dòng.** Với tập lớn (10.000 Work Item,
   đo thực tế `~4802–4880s` ≈ `~2.0–2.1 items/s`), gọi trực tiếp một lần qua HTTP sẽ chạm
   timeout của reverse proxy / Gunicorn (`60–120s`). UI/vận hành nên giới hạn phạm vi
   `selection` cho mỗi đợt thực thi đồng bộ (ví dụ theo lô vài chục đến vài trăm dòng) cho
   tới khi có cơ chế thực thi bất đồng bộ qua RQ worker.

### 6.1. Ghi chú bàn giao cho T1A (UI)

Revision hoàn tất runtime/chaos/benchmark **không thay đổi bất kỳ dotted path, tham số đầu vào,
mã lỗi hay schema JSON trả về** của cả 19 endpoint P1 (0 breaking change cho UI). Ba tinh chỉnh
hành vi phía server mà T1A cần lưu ý khi hiển thị trạng thái:

1. **Resume phân phối (`crm_cskh.api.distribution.execute`):** Khi một run trả
   `status = "completed_with_errors"`, UI có thể cho phép `CRM Admin` bấm thực thi lại với
   cùng `preview_id` (miễn `rule_version` của campaign chưa đổi); server chỉ chạy phần còn lại
   và trả `assigned` là tổng cộng dồn đầy đủ của cả run. Nếu rule đã đổi trong lúc đó, server
   trả `409 RULE_CHANGED` — UI cần yêu cầu tạo `preview` mới.
2. **Import > 200 dòng (`crm_cskh.api.imports.*`):** `create_preview` vẫn cắt mảng `preview`
   tối đa 200 dòng (`preview_truncated = true`) để giữ payload nhẹ, nhưng worker phía sau đọc
   và xử lý đầy đủ toàn bộ file gốc (đã kiểm chứng ở mức 10.000 dòng) và không nhân đôi
   `totals.error_rows` cho các dòng đã bị preview đánh dấu `invalid`.
3. **Thông báo sau commit:** Việc giao `CRM Notification` / `ToDo` tách khỏi transaction
   ownership chính; nếu bước thông báo gặp sự cố tạm thời, trạng thái ownership đã commit vẫn
   bền vững và tự bù idempotent khi gọi lại.

---

## 7. Bằng chứng runtime đã kiểm chứng

Các hành vi được ghi trong tài liệu này đã được kiểm chứng bằng **runtime**, tách biệt rõ
giữa lời gọi hàm trong tiến trình test (`service_unit_test`) và giao vận HTTP thật (`http`):

| Hạng mục | Lớp bằng chứng | Phạm vi kiểm chứng | Kết quả | Bằng chứng |
|---|---|---|---|---|
| Suite P1 (8 module) | `service_unit_test` | Per-method JUnit gate trên inventory P1 riêng (`184` method) | **184/184 PASS** | [`evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json) |
| Chaos / rollback / retry (unit + child process) | `service_unit_test` + `os_process_crash` | `unittest.mock.patch` + tiến trình con `os._exit(137)`; 0 nhánh gây lỗi trong code production | **10/10 PASS** (unit) & **3/3 PASS** (harness) | [`evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml), [`evidence/p1-runtime-20261102/chaos/p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json) |
| HTTP transport P1 | `http` | HTTP thật tới `bench serve`, cookie phiên + CSRF thật, 5 actor đã seed + `Guest`, phủ đủ 7 nhóm API P1 | **52/52 PASS** | [`evidence/p1-runtime-20261102/http/p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) |
| Benchmark 10k import + 10k phân phối | `runtime_benchmark` | 2 đợt × 10.000 dòng (`0 → 10k`, `10k → 20k`) + lô hỗn hợp & capacity `30/42/28` trên `p1bench.localhost` | **Số đo thực tế (0 lỗi, 0 trùng)** | [`evidence/p1-runtime-20261102/benchmark/`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/) |

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
