# API contract — P1 (Kho khách hàng, Team & Weighted Distribution)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/api-contract-p1.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Frozen cho T1A (UI)** — backend đã hiện thực và test trong `codex/p1-foundation`
> **Phạm vi:** **chỉ backend P1**. Toàn bộ nghiệp vụ P1 nằm ngoài phạm vi tài liệu này.
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
| 9 | POST | `crm_cskh.api.distribution.execute` | `CSKH CRM Admin` | Thực thi preview |
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

> **Lưu ý:** `crm_cskh.api.customers.my_list` và `crm_cskh.api.pool_list` **không** có bản
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
| `mapping` | object \| string | ✖ | `{field: tên_cột}`; `field` = `mobile_no` (chỉ cần SĐT) |
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
(`preview_truncated: true` khi có nhiều hơn).

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
(đã sanitize formula/HTML — SEC-08).

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
  "active_work_items": 500,
  "active_claims": 500,
  "as_of": "…"
}
```

`current_owners` đếm từ `CSKH Work Item` (sự thật hiện tại), `assignment_events` đếm từ
`CSKH Assignment History` ⇒ **hai số khác nhau là bình thường** khi có chuyển giao
(P1 §12.5).

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
3. **`date_from` / `date_to` của dashboard chưa được dùng để lọc** (tham số được nhận để giữ
   ổn định contract, hiện scope theo campaign + trạng thái).
4. **Chưa có pagination sâu** (`offset` không có cursor).
5. **Chưa có bulk operation** cho assign/reassign (P1 §10.1 cấm nhận danh sách ID tuỳ ý).

---

## 7. Nguồn

- P1 §8.1, §8.2, §8.3, §9.1, §9.4, §9.5, §10, §12.5, §16
- [`schema-p1.md`](./schema-p1.md) — 8 DocType, constraint, index
- [`adr-p1-03-import-engine.md`](./adr-p1-03-import-engine.md),
  [`adr-p1-04-weighted-fair.md`](./adr-p1-04-weighted-fair.md),
  [`adr-p1-05-ownership-sync.md`](./adr-p1-05-ownership-sync.md),
  [`adr-p1-07-rbac.md`](./adr-p1-07-rbac.md)
- Source: `apps/crm_cskh/crm_cskh/api/*.py`, `apps/crm_cskh/crm_cskh/services/*.py`
