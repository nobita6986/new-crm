# ADR-P1-02 — Active Claim: unique theo Lead, tạo cùng transaction với Work Item

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-02-active-claim.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §1.3, §5.2, §5.6, §16)
> **Work package:** P1-WP02 (schema) + P1-WP06/WP07 (transaction)

---

## 1. Vấn đề

P1 §1.3 chốt: *"chỉ một Work Item active trên toàn CRM"*. Một Lead không được bị giao đồng thời
ở hai campaign. Cơ chế chặn phải là **DB uniqueness**, không phải kiểm tra tồn tại ở app layer.

P1 §7.5 yêu cầu: *"tạo Work Item, active claim, owner/ToDo/DocShare và Assignment History
**atomically**"* và *"kiểm tra lại Active Claim/Work Item bằng row lock/DB uniqueness"*.

---

## 2. Quyết định

**Dùng DocType `CSKH Active Claim` với `lead` UNIQUE ở DB, `work_item` UNIQUE ở DB.**

| Ràng buộc | Cách hiện thực |
|---|---|
| Một Lead chỉ có một active claim | `CSKH Active Claim.lead` → `unique: 1` ⇒ `ADD UNIQUE INDEX lead` |
| Một Work Item chỉ có một claim | `CSKH Active Claim.work_item` → `unique: 1` ⇒ `ADD UNIQUE INDEX work_item` |
| Claim và Work Item phải khớp | `p1_models.py::ActiveClaim.validate` + tạo trong **cùng transaction** |

### 2.1. Vì sao `lead` unique (không phải `(campaign, lead)`)

P1 §5.2 ghi: *"**Unique `lead` trong claim table**"* — và P1 §1.3 nói rõ chỉ **một** Work Item
active trên **toàn CRM**, không phải mỗi campaign. Nếu unique là `(campaign, lead)` thì một Lead
có thể bị claim ở campaign A và campaign B cùng lúc ⇒ vi phạm "một owner hiện tại" của
`CRM Lead.lead_owner` (single-valued). Vì vậy unique là **theo `lead`**.

Campaign vẫn được lưu trên claim (để biết claim thuộc campaign nào ⇒ phục vụ conflict message
và release khi đóng campaign).

### 2.2. "Active" nghĩa là gì khi bảng chứa claim của work item đã đóng?

`CSKH Active Claim` chỉ chứa claim **đang active**. Khi Work Item rời trạng thái active
(`closed`, `revoked`), claim **bị xoá** trong cùng transaction. Bảng vì vậy luôn nhỏ và
đúng nghĩa "active". Điều này khớp P1 §5.2: *"gỡ claim khi work item không active"*.

Lịch sử claim đã gỡ **không** nằm trong bảng này mà nằm ở `CSKH Assignment History`
(append-only) — P1 §5.3.

### 2.3. Trạng thái Work Item

P1 §1.3: *"trạng thái P1 thực sự sử dụng chỉ `queued`, `assigned`, `revoked`, `closed`"*.
Vì vậy:

| Trạng thái | Active? | Claim? | Owner |
|---|---|---|---|
| `queued` | ✅ có (đã vào kho, chờ giao) | ✅ giữ | ❌ rỗng |
| `assigned` | ✅ | ✅ giữ | ✅ có |
| `revoked` | ❌ | ❌ xoá | ❌ rỗng |
| `closed` | ❌ | ❌ xoá | ❌ rỗng |

`in_progress` / `on_hold` **không** được dùng ở P1 (P1 §1.3 nói rõ chỉ mở ở P2).

> **Lưu ý sửa draft:** bản nháp `p1_models.py` ban đầu dùng `ACTIVE_STATES = ("queued","assigned",
> "in_progress","on_hold")` — **sai** so với P1 §1.3. Đã sửa thành `("queued", "assigned")`.

---

## 3. Tính nguyên tử

Mọi thay đổi claim/work item/owner/history **phải** nằm trong một transaction Frappe
(`frappe.db.savepoint` / `frappe.db.rollback` theo khối). Cổng duy nhất là
`crm_cskh.services.ownership` (P1 §7.6) và `crm_cskh.services.distribution`.

Thứ tự khóa ổn định (P1 §7.5): **campaign/rule-state → Lead/active claim → Work Item**.

Với 2 executor cùng campaign:
- cả hai đều muốn claim Lead X ⇒ DB unique `lead` ⇒ **một thắng**, một thua bằng
  `IntegrityError` ⇒ thua được xử lý như `CONFLICT`, **không** tạo 2 Work Item active.

---

## 4. Hệ quả

- Đóng campaign có Work Item active ⇒ **không** xoá gì; hệ thống yêu cầu Admin chọn hành động
  kết thúc rõ ràng (P1 §7.7). Draft hiện tại `Campaign.validate` **throw** nếu còn work item
  active ⇒ fail-closed, đúng P1 §7.7.
- Work Item `queued` vẫn giữ claim ⇒ Lead đã vào kho P1 thì **không** campaign khác lấy được
  ⇒ đúng P1 §1.3 "mỗi Lead được tham gia nhiều campaign theo thời gian, nhưng chỉ một Work
  Item active trên toàn CRM".
- Revoke ⇒ claim xoá, Work Item về `revoked` hoặc `queued` (policy P1 §7.7 — chốt ở ADR-P1-05),
  lịch sử giữ nguyên.

---

## 5. Điều kiện chấp nhận

1. Insert hai claim cùng `lead` ⇒ `IntegrityError` từ DB.
2. Insert hai claim cùng `work_item` ⇒ `IntegrityError` từ DB.
3. Claim trỏ sang Work Item không active / khác campaign ⇒ validate throw.
4. 2 job distribution chạy song song cùng selection ⇒ **một** active claim mỗi Lead, Work Item
   active không trùng, lịch sử nhất quán (T1-12).
5. Revoke work item ⇒ claim bị xoá, Lead không còn claim ⇒ campaign khác claim được.

---

## 6. Nguồn

- P1 §1.3, §5.2, §5.3, §5.6, §6.3, §7.1, §7.3, §7.5, §7.7, §16, §17
- `docs/architecture/adr-p1-01-identity-index.md`
