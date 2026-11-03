# ADR-P1-05 — Đồng bộ Lead owner / ToDo / DocShare: adapter atomic qua ownership service

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-05-ownership-sync.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §7.6, §3.1, §16)
> **Work package:** P1-WP07

---

## 1. Vấn đề

P1 §3.1 cảnh báo (đã đọc source upstream CRM `v1.87.0`):

> `crm/api/todo.py::after_insert` đồng bộ `ToDo.allocated_to` sang `CRM Lead.lead_owner` bằng
> `frappe.db.set_value`; `clear_owner_on_unassign` có thể xóa owner khi hủy assignment. Vì vậy
> **trình tự ToDo → owner → DocShare phải được kiểm chứng cùng transaction**, không được cập
> nhật rời rạc để tồn tại trạng thái nửa vời.

Đã kiểm chứng trên source đã ghim (`apps/crm` @ `0d3910b`):

| Upstream | Hành vi | Hệ quả P1 |
|---|---|---|
| `crm/api/todo.py::after_insert` | `frappe.db.set_value("CRM Lead", name, "lead_owner", doc.allocated_to)` | ToDo insert ⇒ **tự** đổi owner |
| `crm/api/todo.py::clear_owner_on_unassign` | khi ToDo `status == "Cancelled"` ⇒ `set_value(lead_owner, None)` | Hủy ToDo ⇒ **xoá** owner |
| `crm/fcrm/doctype/crm_lead/crm_lead.py::assign_agent` | `assign({"assign_to": [agent], ...}, ignore_permissions=True)` | Tạo ToDo + DocShare |
| `crm/fcrm/doctype/crm_lead/crm_lead.py::share_with_agent` | `frappe.share.add_docshare(..., read=1, write=1, submit=0, share=0)` | Tạo DocShare |
| `crm/fcrm/doctype/crm_lead/crm_lead.py::validate` | khi `lead_owner` đổi ⇒ `share_with_agent` + `assign_agent` | Sửa owner trực tiếp cũng tạo ToDo/DocShare |

⇒ **Ba đường ghi owner**: (1) `ToDo.after_insert`, (2) `CRM Lead.validate` khi owner đổi,
(3) `frappe.db.set_value` trực tiếp. Chỉ (2) là "có kiểm soát".

---

## 2. Quyết định

**`crm_cskh.services.ownership` là cổng mutation DUY NHẤT.** Toàn bộ thay đổi sở hữu đi qua
service này, trong **một transaction Frappe** duy nhất, theo thứ tự đã kiểm chứng.

### 2.1. Thứ tự áp dụng (bên trong 1 transaction)

```
1. Lock Work Item (SELECT ... FOR UPDATE)
2. Kiểm tra expected_owner (optimistic concurrency)
3. Kiểm tra Active Claim / Work Item status
4. Ghi Work Item (assigned_to, assigned_at, status)
5. Đóng ownership interval cũ + mở Assignment History event mới
6. Đồng bộ CRM Lead.lead_owner (dùng frappe.db.set_value trong transaction)
7. Đồng bộ ToDo (assign / cancel của user cũ)
8. Đồng bộ DocShare (thêm quyền cho user mới, THU HỒI quyền user cũ)
9. Commit
```

Sau commit mới gửi notification / invalidate cache (P1 §7.5: *"Các sự kiện ra ngoài transaction
(notifications/realtime) thực hiện sau commit"*).

### 2.2. Vì sao `frappe.db.set_value` cho Lead owner mà không dùng `doc.save()`

`CRM Lead.validate` khi `lead_owner` đổi sẽ tự gọi `assign_agent` + `share_with_agent`, tức tự
tạo ToDo/DocShare — **ngoài tầm kiểm soát** của ta. Nhưng:

- P0 đã chặn non-system đổi `lead_owner` (`permissions.py::validate_lead_owner`),
- và ownership service chạy với `frappe.flags.ignore_permissions` **chỉ sau khi** đã tự kiểm
  quyền actor.

Dùng `frappe.db.set_value` (bỏ qua `validate`) giúp **tránh** việc upstream tự sinh ToDo/DocShare
theo đường không kiểm soát; service tự quản lý ToDo/DocShare theo đúng thứ tự. Đây là lựa chọn
**có chủ đích**, được nêu rõ trong `docs/architecture/api-contract-p1.md`.

### 2.3. Hủy ToDo cũ ⇒ không được xoá owner

`clear_owner_on_unassign` sẽ set `lead_owner = None` khi ToDo bị Cancelled. Vì service đã set
owner **trước** khi hủy ToDo cũ (thứ tự 6 rồi 7), nếu `clear_owner_on_unassign` chạy sau đó sẽ
xoá mất owner mới.

⇒ **Bắt buộc**: khi hủy ToDo cũ, phải **re-assert** owner sau đó, hoặc hủy ToDo với cờ chặn
`clear_owner_on_unassign`. Triển khai: sau khi hủy ToDo cũ, service **set lại** `lead_owner`
(nếu khác `None`) và kiểm tra lại bằng assert (P1 §7.6 #4: *"sau thao tác assert chỉ owner mong
muốn còn quyền hợp lệ theo policy"*).

Đây là **bằng chứng bắt buộc**, không phải tối ưu: nếu thiếu, T1-15/T1-16 sẽ fail với owner
rỗng sau reassign/revoke.

### 2.4. Thu hồi quyền người cũ (P0 invariant phải giữ)

P0 đã có: `permissions.py::clear_stale_lead_shares` (khi `lead_owner` đổi ⇒ xoá **toàn bộ**
DocShare của Lead) và `permissions.py::remove_assignments` (khi unassign ⇒ xoá DocShare của các
assignee bị gỡ).

⇒ Với ownership service: sau khi đổi owner, **xoá DocShare của user cũ** và ToDo cũ, và **chỉ**
giữ/cấp lại quyền cho user mới. Việc xoá DocShare của user cũ là **bắt buộc** để user cũ mất
quyền đọc Lead (T1-16, SEC-06) — P1 §7.7: *"nhân viên cũ phải bị từ chối đọc hồ sơ qua API
sau khi thu hồi nếu không còn quyền hợp lệ nào khác"*.

### 2.5. Idempotency & audit

- Mỗi mutation có `request_id` (P1 §7.7: *"Manual reassign và revoke đều cần lý do và
  `request_id` để idempotent"*).
- `request_id` được ghi vào `CSKH Assignment History.event_id` (UNIQUE ở DB) ⇒ retry cùng
  `request_id` ⇒ phát hiện event đã tồn tại ⇒ trả kết quả cũ, **không** tạo event thứ hai.
- `CSKH Assignment History` là **append-only**: `before_save` chặn sửa, `on_trash` chặn xoá
  (P1 §8.2: *"update/delete bị chặn với user thường và Admin hành chính, ngoại lệ correction
  cần cơ chế event bù riêng"*).

### 2.6. Ownership interval

`CSKH Assignment History` có `ownership_start` / `ownership_end` ⇒ lưu **chính xác** khoảng thời
gian sở hữu. A → B → A ⇒ **ba** phiên ownership, không gộp (P1 §5.3). `previous_event` nối chuỗi.

---

## 3. API (P1 §10)

| Endpoint | Method | Vai trò |
|---|---|---|
| `crm_cskh.api.ownership.reassign` | POST | Admin/ủy quyền — có `expected_owner` + `reason` + `request_id` |
| `crm_cskh.api.ownership.revoke` | POST | Admin/ủy quyền — có `expected_owner` + `reason` + `request_id` |
| `crm_cskh.api.ownership.assign` | POST | Nội bộ (distribution) — qua service, không mở rộng cho client |

Mã lỗi ổn định: `CONFLICT` (expected_owner sai), `FORBIDDEN` (không có quyền),
`CAPACITY_EXCEEDED`, `PARTIAL_SUCCESS` (batch nhiều item, mỗi item kết quả riêng),
`RETRYABLE_FAILURE`.

---

## 4. Điều kiện chấp nhận

| Test | Kỳ vọng |
|---|---|
| T1-15 | A → B → C ⇒ history đủ **3** phiên ownership, current owner C |
| T1-16 | Thu hồi A về kho ⇒ owner rỗng, claim/work item state đúng, **A mất quyền** |
| SEC-06 | A sau thu hồi không xem được Lead + Call Log/Task/Note/file |
| SEC-10 | Admin thao tác đồng thời với thay đổi của user hiện tại ⇒ optimistic conflict, không overwrite |
| §7.7 | Đóng campaign có work item active ⇒ không xoá/mất lịch sử, yêu cầu hành động rõ ràng |
| §12.3 #10 | Sửa/xoá `CSKH Assignment History` bị từ chối |

---

## 5. Nguồn

- P1 §3.1, §5.3, §7.5, §7.6, §7.7, §8.2, §10, §12.2 (T1-15..T1-17), §12.3 (SEC-06, SEC-10), §16
- Upstream CRM `v1.87.0`: `crm/api/todo.py`, `crm/fcrm/doctype/crm_lead/crm_lead.py`
- P0: `apps/crm_cskh/crm_cskh/permissions.py` (`validate_lead_owner`, `clear_stale_lead_shares`,
  `remove_assignments`)
