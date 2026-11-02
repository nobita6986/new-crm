# ADR-P1-06 — Tích hợp UI: trang mới trong site Frappe hiện tại (theo ADR-003 P0)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-06-ui-integration.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §3.2, §16) — *phạm vi hẹp: backend contract trước, UI ở PR riêng*
> **Work package:** P1-WP08 (UI) — **không** nằm trong gói backend hiện tại

---

## 1. Quyết định

**Dùng trang mới trong cùng Frappe site và cùng đăng nhập**, tái sử dụng Frappe UI/components và
API của app `crm_cskh`. Đường dẫn dự kiến: `/cskh/distribution`, `/cskh/my-customers`.

Không dựng SPA/backend thứ hai.

---

## 2. Cơ sở

- P1 §3.2: *"**Ưu tiên:** trang mới trong cùng Frappe site và cùng đăng nhập… tái sử dụng Frappe
  UI/components và API; gắn navigation theo lựa chọn ADR-003."*
- P1 §16 ADR-P1-06: *"Sử dụng ADR-003 P0, không phụ thuộc hook `develop` chưa có bản stable."*

---

## 3. Vì sao backend contract chốt trước (gate của gói hiện tại)

P1 §10 mở đầu bằng câu: *"**BACKEND API CONTRACT — THỐNG NHẤT TRƯỚC KHI LÀM FRONTEND**"*.
Vì vậy gói P1 hiện tại (WP01–WP07 + API) **phải**:

1. Chốt `docs/architecture/api-contract-p1.md` (endpoint, request/response, mã lỗi, quyền).
2. Có **test API thật** trên site cô lập (không mock) để chứng minh contract đúng.
3. Không mở UI nào chưa có backend tương ứng.

⇒ T1A nối UI sau khi contract được bàn giao và **đóng băng** (chỉ đổi khi có ADR mới).

---

## 4. Điều kiện cho UI (nằm ngoài gói này, ghi lại để T1A dùng)

| Màn hình | API backend đã chốt |
|---|---|
| Kho khách hàng | `crm_cskh.api.customers.pool_list` (scope theo quyền) |
| Nhập danh sách | `crm_cskh.api.imports.create_preview` / `confirm` / `get_status` |
| Chiến dịch & quy tắc | `crm_cskh.api.campaigns.list` / `save`, `crm_cskh.api.rules.*` |
| Xem trước & chạy | `crm_cskh.api.distribution.preview` / `execute` / `get_run` |
| Khách được giao | `crm_cskh.api.customers.my_list` |
| Dashboard BOD | `crm_cskh.api.dashboard.distribution` |

Mọi màn hình dùng **tiếng Việt** theo `docs/architecture/glossary-vi.md` (P1 §9.6), format
`vi-VN`, timezone `Asia/Ho_Chi_Minh`.

---

## 5. Hệ quả

- **Tích cực:** không fork upstream CRM; không chạy thêm backend; auth/CSRF của Frappe dùng lại.
- **Rủi ro:** phụ thuộc `frappe-ui` version đã ghim; phải giữ diff upstream tối thiểu nếu
  cần patch (P1 §3.2). P0 đã có PoC patch Vite DEV (`deploy/dev/apply-crm-vite-dev-patch.py`) và
  bản dịch `frappe-ui` (`apply-crm-ui-vi-patch.py`) — tái sử dụng, không viết mới.
- **Ngoài phạm vi gói backend:** mọi thứ UI (Vue page, navigation, i18n) nằm ở PR UI riêng.

---

## 6. Nguồn

- P1 §3.2, §9.6, §10, §16
- `docs/architecture/ui-extension-spike.md` (PoC ADR-003), `docs/architecture/adr-ui-001.md`
- `docs/architecture/glossary-vi.md`
