# ADR-005 — PHẠM VI ROLE PoC: `CRM_ADMIN` & `BOD_VIEWER`

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B
> **Ngày:** 2026-10-15
> **Trạng thái:** **Quyết định kiến trúc đã được T0 chốt** — đây **KHÔNG** phải bằng chứng runtime hay kết quả security test. WP-01 baseline đã chạy; role permission tests chưa có.
> **Tài liệu liên quan:** [`reuse-audit.md`](./reuse-audit.md) §7 · §12, [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md), [`version-lock.md`](./version-lock.md), [`P0.md`](../root-plan/P0.md) §7, [`master-plan.md`](../root-plan/master-plan.md) §2.3

---

## 1. BỐI CẢNH

P0 §7.1 dựng tài khoản/role giả để kiểm tra ranh giới quyền, trong đó có `BOD_VIEWER` và `CRM_ADMIN`. Master plan (§2.3) chốt: BOD **không** cần role `System Manager` mặc định để xem báo cáo; quyền theo nguyên tắc least privilege. Bảng RBAC mục tiêu (P0 §7.3) yêu cầu ghi rõ `BOD Viewer` chỉ đọc theo phạm vi, `CRM Admin` quản trị nghiệp vụ.

Ràng buộc liên quan: quyền hiệu lực phải kiểm ở backend theo record ([`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)); trạng thái kiểm chứng hiện là `[CẦN TEST DEV]`.

---

## 2. QUYẾT ĐỊNH (T0 đã chốt)

P0 dùng **`CRM_ADMIN`** và **`BOD_VIEWER`** làm **role PoC**.

### 2.1. `BOD_VIEWER`

- **Chỉ đọc** báo cáo toàn công ty và phần **drill-down tối thiểu được phép**.
- **Không** quyền: ghi, import, reassign/chuyển-thu hồi owner, quản trị.
- **Không** sử dụng role `System Manager`.

### 2.2. `CRM_ADMIN`

- Giới hạn trong **quản trị nghiệp vụ CSKH** (kho khách, import, campaign, phân phối, thao tác ownership được audit).
- **Không** quản trị **cấu hình hệ thống Frappe** (user/role/system settings của Framework).

---

## 3. HỆ QUẢ

- Cấp quyền theo **least privilege**; BOD không được nâng lên `System Manager` để xem dữ liệu.
- Endpoint báo cáo của `crm_cskh` phải **ràng buộc scope ở server** (không nhận `user`/`team` tùy ý từ client vượt quyền).
- UI không phải lớp bảo mật: ẩn/hiện nút chỉ là hỗ trợ; quyền thực thi ở backend.
- Role ghi (write) nghiệp vụ CSKH và quyền quản trị Framework phải **tách biệt** rõ ràng.

---

## 4. PHẦN CẦN KIỂM CHỨNG (RUNTIME / SECURITY)

> Tất cả mục dưới đây **CHƯA XÁC MINH**. ADR này là **quyết định thiết kế**, không phải kết quả test.

- `SEC-15` (P0 §7.5): `BOD_VIEWER` gọi API quản trị user/role/import → phải bị từ chối; không hưởng `System Manager` ngầm.
- BOD gọi API ghi/reassign/close/policy → bị từ chối (`FORBIDDEN`).
- Leader/Admin không vượt scope team; aggregate report không rò rỉ dữ liệu ngoài phạm vi.
- Seed đã tạo hai role PoC với tên Frappe `CSKH CRM Admin` và `CSKH BOD Viewer`; việc ánh xạ các tên này sang nhãn quyết định `CRM_ADMIN`/`BOD_VIEWER`, cấu hình quyền least-privilege và kiểm chứng bằng nhiều tài khoản — **chưa chạy**.
- Baseline CRM `v1.87.0` + Frappe Framework `v15.123.1` đã cài/migrate trên Docker/WSL2; MariaDB 11.8 còn cảnh báo tương thích. Kiểm chứng role/security trên runtime — **chưa chạy**.
