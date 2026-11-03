# ADR-P1-01 — Canonical phone identity: `CSKH Identity Index` với DB unique

> **Dự án:** CRM-CSKH — Kho khách hàng, Team & Weighted Distribution
> **Tài liệu:** `docs/architecture/adr-p1-01-identity-index.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (chốt theo P1 §1.3 + §16 mặc định khuyến nghị)
> **Work package:** P1-WP02 (schema) + P1-WP03 (identity)
> **Tài liệu liên quan:** [`../root-plan/P1.md`](../root-plan/P1.md) §5.4, §5.6, §16, [`p0-exit-report.md`](./p0-exit-report.md), [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)

---

## 1. Câu hỏi

Canonical identity của một khách hàng được lưu ở đâu, và cơ chế nào **ngăn tạo trùng
số điện thoại** khi hai Admin import đồng thời?

P1 §5.4 đưa ra hai phương án:

- **A.** Custom field `CRM Lead.cskh_normalized_phone` với DB unique.
- **B.** DocType riêng `CSKH Identity Index` với `normalized_phone` unique ở DB.

P1 §5.4 yêu cầu rõ: *"**Không dựa vào kiểm tra tồn tại trên app mà thiếu unique constraint tại DB**"*.

---

## 2. Bối cảnh đã kiểm chứng (source thật, revision đã ghim)

| Mục | Bằng chứng |
|---|---|
| Baseline | Frappe `v15.123.1` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`), Frappe CRM `v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) — `docs/architecture/version-lock.md` §2 (L-01/L-02) |
| `CRM Lead` là entity gốc | `crm/fcrm/doctype/crm_lead/crm_lead.py` — `mobile_no` là `DF.Data | None`; `lead_owner` là owner duy nhất |
| Lead **không** có unique phone | `crm/fcrm/doctype/crm_lead/crm_lead.json` — không có `unique` trên `mobile_no`/`phone`; CRM cho phép nhiều Lead cùng SĐT |
| Tạo Lead qua nhiều đường | `CRM Lead` insert từ: Desk/UI (`frappe.desk.form.save`), API REST (`frappe.client.insert`), Data Import (`frappe.core.doctype.data_import`), webhook/CRM form (`crm.api.form.enrich_form_submission`), script ngoài, và import P1 |
| P0 đã chặn Lead không có guard | `crm_cskh/permissions.py::lead_has_permission` — non-system bị chặn `create`; `crm_cskh/permissions.py::validate_lead_owner` — chỉ System Manager đổi được `lead_owner` |
| `frappe.db.add_unique` tồn tại | `frappe/database/mariadb/database.py::add_unique` — tạo `ALTER TABLE ... ADD UNIQUE` thật, idempotent theo tên constraint |
| `unique: 1` trên DocField → unique index | `frappe/database/mariadb/schema.py` — `ADD UNIQUE INDEX IF NOT EXISTS {col.fieldname}` cho field có `unique` |

---

## 3. Quyết định

**Chọn phương án B: DocType `CSKH Identity Index` với `normalized_phone` unique ở DB.**

Lý do:

1. **Schema nhỏ hơn, không đụng core.** Phương án A bắt buộc thêm custom field vào `CRM Lead`
   (core DocType của upstream CRM). P0 đã chốt nguyên tắc "không sửa core vì tiện tay"
   (P1 §0.4) và P0 hiện chỉ có **một** custom field POC (`cskh_poc_marker`) được chấp nhận.
   Phương án B giữ nguyên `CRM Lead` và đặt toàn bộ invariant P1 trong app của dự án.
2. **Không phụ thuộc vào việc "kiểm soát mọi đường tạo Lead".** P1 §5.4 nêu rõ điều kiện
   của phương án A: chỉ dùng được **nếu** mọi create/update Lead liên quan đều đi qua
   validation/hook kiểm tra. Với CRM upstream có `enrich_form_submission`, Data Import,
   webhook và các script tùy ý, "mọi đường" là **bất khả thi đảm bảo 100%**. Phương án B
   đặt ràng buộc ở **DB**, nơi không thể bypass bằng app code.
3. **Nullability đúng.** `CRM Lead` có thể được tạo không có SĐT (P1 §5.6: *"nullable khi Lead
   không có số và được phép tạo theo đường khác"*). Một `unique` trên `CRM Lead.mobile_no`
   (nullable) cũng được, nhưng nó **gắn canonical phone vào mọi Lead có SĐT** — bao gồm Lead
   được tạo ngoài P1 (CRM form, Data Import cũ, dữ liệu legacy). Điều đó tạo rủi ro backfill
   hàng loạt và biến một tính năng P1 thành ràng buộc toàn hệ thống. `CSKH Identity Index`
   chỉ chứa identity do P1 quản lý.
4. **Tách được "canonical" khỏi "thuộc P1".** P1 §1.3: *"Một Lead canonical cho một số điện thoại
   chính đã chuẩn hóa; danh tính nhập chưa chắc chắn không được tự merge nếu xung đột."*
   Identity Index là nơi duy nhất ánh xạ `normalized_phone → canonical_lead`, và nó cho phép
   **giữ một Lead ngoài P1 mà không có identity** (để lịch sử sở hữu không bị gắn nhầm).

### 3.1. Cơ chế đồng bộ (không để "đường tạo Lead" nào lách được)

Vì phương án B không dựa vào việc chặn mọi đường tạo Lead, invariant được bảo vệ bằng **hai lớp**:

- **Lớp 1 — DB unique (tuyệt đối).** `CSKH Identity Index.normalized_phone` có `unique: 1`
  ⇒ `ALTER TABLE tabCSKH Identity Index ADD UNIQUE INDEX normalized_phone`.
  Hai request đồng thời cùng `+84…` ⇒ **một request thắng, một request thua bằng
  `IntegrityError`**, không bao giờ tạo hai Lead canonical.
- **Lớp 2 — guard tập trung trong app.** Mọi đường ghi của P1 (import, distribution,
  ownership) **phải** đi qua `crm_cskh.services.identity` (`claim_canonical_identity` /
  `release_canonical_identity`). Service này là **cổng duy nhất** được phép insert
  `CSKH Identity Index`, và nó là nơi bắt `IntegrityError` để chuyển thành lỗi nghiệp vụ
  `DUPLICATE`/`CONFLICT` (P1 §10.1 code ổn định).

**Lớp 2 bổ sung, không thay Lớp 1.** Lớp 1 là bất di bất dịch; Lớp 2 là để lỗi trả về client
có mã ổn định thay vì 500, và để dọn dữ liệu một cách có kiểm soát.

### 3.2. Đường tạo Lead ngoài P1

Một Lead được tạo ngoài P1 (CRM form, Data Import của CRM, script) **không** tự động có
identity index. Hệ quả đã được chấp nhận và ghi rõ:

- Khi P1 import gặp SĐT đã tồn tại trên một Lead **không** có identity index ⇒ policy
  **"adopt"**: P1 tạo index trỏ tới Lead đó (không tạo Lead mới, không merge, không sửa
  `lead_owner`/status/ghi chú — P1 §6.3). Việc này **không thay đổi dữ liệu Lead**.
- Khi P1 import gặp SĐT đã có identity index trỏ tới Lead khác ⇒ reuse canonical Lead đó.
- `CRM Lead` mới mà không qua P1 vẫn tồn tại và không bị chặn. Đây là **giới hạn đã biết**,
  được ghi vào `P1 Exit Report` (mục known limitations) và vào `docs/architecture/permission-matrix.md`
  khi đóng gate.

> **Không chấp nhận phương án "thêm hook `CRM Lead` để tự tạo identity"** vì điều đó biến mọi
> Lead ngoài P1 thành P1 identity, và vì `crm_cskh` P0 đã cố ý **không** can thiệp vào
> `CRM Lead` write path ngoài guard quyền (`permissions.py::validate_lead_owner`).

---

## 4. Hệ quả

### Tích cực

- **DB unique thật** ⇒ race test (2/5 job import đồng thời) cho kết quả deterministic.
- Schema P1 không đụng core CRM.
- Identity là **append-mostly**: index trỏ canonical Lead; nếu Lead bị xoá (không khuyến nghị
  theo P1 §1.3) thì index được release qua service, có audit.
- Có thể dùng chính bảng này cho báo cáo "số khách duy nhất" mà không cộng lẫn Work Item
  (P1 §9.1, §12.5).

### Tiêu cực / đánh đổi

- **Một Lead có thể có 0 index** nếu tạo ngoài P1 → số "khách duy nhất trong kho" phải báo cáo
  bằng Work Item/Lead theo scope, không dựa vào Identity Index làm nguồn duy nhất.
- Cần có **service duy nhất** ghi index ⇒ mọi code path mới phải đi qua service (rủi ro quy trình).
- Cần `on_doctype_update`/patch để tạo unique index ⇒ phải test **migrate hai lần** (P1 §5.6).

### Rủi ro đã giảm

| Rủi ro P1 §17 | Giảm bằng |
|---|---|
| Dedupe chỉ làm phía UI, hai request tạo 2 Lead | DB unique + service claim + race test |
| Import tạo Lead trùng khi retry | `idempotency_key` unique trên `CSKH Import Batch` + recheck dưới transaction |

---

## 5. Điều kiện chấp nhận (sẽ được kiểm bằng test)

1. `bench --site <test-site> migrate` chạy **hai lần** không lỗi ⇒ unique index idempotent.
2. Insert `CSKH Identity Index` hai lần cùng `normalized_phone` ⇒ `IntegrityError` từ DB.
3. Import **2 job** và **5 job** song song cùng một SĐT ⇒ đúng **một** canonical Lead,
   các job thua báo `DUPLICATE`/`CONFLICT`, không tạo Lead trùng.
4. Retry cùng `idempotency_key` + cùng checksum ⇒ **không** tạo thêm Lead/Index/History.
5. Cùng `idempotency_key` nhưng checksum khác ⇒ từ chối (`CONFLICT`).
6. `normalized_phone` luôn ở canonical E.164 (`+84…`), sinh bởi
   `crm_cskh.services.phone_identity.normalize_vn_phone` (pure function).

---

## 6. Nguồn

- P1 §1.3, §5.4, §5.6, §6.3, §6.4, §7.5, §10.1, §16, §17
- `docs/architecture/version-lock.md` §2
- `docs/architecture/p0-exit-report.md` (P0 GO, core inventory 99)
- Upstream: `crm/fcrm/doctype/crm_lead/crm_lead.py`, `crm/fcrm/doctype/crm_lead/crm_lead.json`,
  `frappe/database/mariadb/database.py::add_unique`, `frappe/database/mariadb/schema.py`
