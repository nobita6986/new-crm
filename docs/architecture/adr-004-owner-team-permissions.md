# ADR-004 — QUYỀN OWNER/TEAM THEO RECORD & NGUỒN SỰ THẬT ASSIGNMENT

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B
> **Ngày:** 2026-10-15
> **Trạng thái:** **Quyết định kiến trúc đã được T0 chốt** — đây **KHÔNG** phải bằng chứng runtime hay kết quả security test đầy đủ. WP-01 baseline đã chạy. `crm_cskh` đã scaffold và security suite **đã chạy** ở ba lượt khác nhau (26 / 27 / 28 OK trên `wp05-security-final.txt`, `p0-local-final.txt`, `p0-isolated-verification.md` CI run `#38712593755`) — xem [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §2 và [`risk-register.md`](./risk-register.md) §5. Runtime/security đã pass per-route cho ~32/70 ô matrix (xem [`permission-matrix.md`](./permission-matrix.md) §2–§9 bản 1.0); SEC-13/14/16/26 vẫn `NOT RUN`; ~38 ô matrix còn `CHƯA XÁC MINH`. **T1C đối chiếu độc lập 2026-10-19** dùng phân tích tĩnh + log T1B (không chạy lại site sạch vì phiên không có Docker/WSL2).
> **Tài liệu liên quan:** [`reuse-audit.md`](./reuse-audit.md) §7 · §12, [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md), [`version-lock.md`](./version-lock.md), [`P0.md`](../root-plan/P0.md) §7, [`P1.md`](../root-plan/P1.md) §7.6

---

## 1. BỐI CẢNH

Ở Frappe CRM `v1.87.0` (commit `0d3910b456ef4fb913e77b29db011b3debc4f778`), quyền theo owner/team và việc đổi người phụ trách không nằm gọn trong một cơ chế:

- Quyền đọc Lead/Deal phụ thuộc `lead_owner` **hoặc** ToDo còn hiệu lực (`crm/permissions/org_hierarchy.py:38`–`60`); `Sales Manager` ngoài cây mặc định thấy toàn bộ (`:30`–`32`, `:87`–`89`).
- `ToDo` **ghi đè** `lead_owner`/`deal_owner` khi thêm và **xóa owner** khi hủy assignment (`crm/api/todo.py:20`–`26`, `:32`–`51`).
- `CRM Lead` tự tạo ToDo + DocShare khi đổi owner (`crm_lead.py:167`,`:178`,`:180`); `share_with_agent` **gỡ DocShare của user khác**.
- API mutation `remove_assignments` **nhận `ignore_permissions` từ client** (`crm/api/doc.py:589`–`604`); `get_assigned_users` dùng `frappe.get_all` không kiểm quyền parent (`:614`).
- `permission_query_conditions` **chỉ** phủ `CRM Lead`/`CRM Deal`/`CRM Notification` (`crm/hooks.py:135`–`139`) — **không** phủ `CRM Call Log`, `CRM Task`, `FCRM Note`, `File` (cấp quyền ở mức DocType).

> Các dòng trên là **bằng chứng tĩnh** (đọc mã tại commit đã khóa), **không phải** kết quả security test. Toàn bộ trạng thái kiểm chứng vẫn là `[CẦN TEST DEV]`.

---

## 2. QUYẾT ĐỊNH (T0 đã chốt)

1. **Quyền owner/team được kiểm ở backend, theo record** (row-level, server-side) cho mọi list/detail/mutation/export — **không** dựa vào việc ẩn nút trên UI.
2. **Ownership service trong `crm_cskh` là đường DUY NHẤT để đổi owner/assignee** (assign / reassign / revoke), chạy trong một transaction có kiểm soát quyền, locking và audit.
3. **`CSKH Assignment History` là nguồn truy vết** (append-only; lưu khoảng sở hữu `ownership_start/end`, `from_user`, `to_user`, actor, lý do).
4. **`lead_owner`, `ToDo` và `DocShare` chỉ là biểu diễn tương thích**, phải được **đồng bộ qua service**; **không phải nguồn quyền độc lập**. Việc cập nhật rời rạc các cơ chế này bị cấm.
5. Quyền hiệu lực của người dùng được **tính ở server** từ ownership service + role/team scope; các generic Frappe endpoint liên quan phải được guard để không bypass service.

---

## 3. HỆ QUẢ

- Cấm cập nhật `lead_owner` trực tiếp; cấm tạo/hủy `ToDo`/`DocShare` ngoài ownership service.
- DocType `CSKH *` mới cần `has_permission` / `permission_query_conditions` (hoặc API riêng kiểm quyền) — một role có `read=1` trên DocType **không** đồng nghĩa xem được mọi bản ghi.
- Các endpoint `crm/api/doc.py` (`remove_assignments`, `get_assigned_users`, `delete_bulk_docs`) và `/api/resource` của DocType mới phải được kiểm/khóa ở P0 security gate.
- P1/P2 **bắt buộc** ghi `CSKH Assignment History` đủ khoảng sở hữu — điều kiện tiên quyết cho KPI attribution ở P3 (xem [`data-model-spike.md`](./data-model-spike.md) §7 mục 4).
- Không dùng `ignore_permissions=True` cho request của user thường.

---

## 4. PHẦN CẦN KIỂM CHỨNG (RUNTIME / SECURITY)

> ADR này là **quyết định thiết kế**; runtime check cho route đã gọi đã có (xem đối chiếu T1C 2026-10-19 trong [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) và [`permission-matrix.md`](./permission-matrix.md) §11.6). Mục còn dưới đây **vẫn CHƯA XÁC MINH** hoặc **còn mở một phần** sau T1C.

- Bộ ca âm P0 §7.5 (`SEC-01`…`SEC-16`) và P1 §12.3/P2 §11.3: truy cập chéo Lead/Call Log/Task/Note/attachment; đổi `lead_owner`/`assigned_to` trái quyền; `ignore_permissions` boundary. **19/28 SEC case đã pass per-route**; `SEC-13/14/16` vẫn `NOT RUN`; `SEC-15/22/23/27` còn `PARTIAL`.
- Rủi ro R-01, R-02, R-04, R-05, R-06, R-07 trong [`reuse-audit.md`](./reuse-audit.md) §12 — phải chứng minh không khai thác được (hoặc đã guard).
- Kiểm chứng thứ tự đồng bộ ToDo → `lead_owner` → DocShare và hành vi thu hồi quyền sau chuyển giao (A → B), kể cả đường đọc phụ trợ. **Direction ToDo-cũ (SEC-22) đã verify** (`Test #12`); **Direction ngược (DocShare-cũ, SEC-23) vẫn partial**.
- Kiểm chứng trên runtime pin: baseline CRM `v1.87.0` + Frappe Framework `v15.123.1` đã cài/migrate; `crm_cskh` đã cài và security suite đã chạy (ba lượt 26/27/28 OK tại revision `b382fe4`). **T1C không tái chạy** site sạch trong phiên này vì thiếu Docker/WSL2 (xem B-T1C-1); ~38/70 ô matrix còn `CHƯA XÁC MINH`.
