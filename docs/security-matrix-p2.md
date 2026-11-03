# P2 — Ma trận Phân quyền, Bảo vệ PII & Khóa Generic API (`Security Matrix P2`)

> **Tài liệu:** `docs/security-matrix-p2.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§11, §15.2 `SEC2-01..SEC2-16`), [`docs/architecture/permission-matrix.md`](./architecture/permission-matrix.md), [`docs/adr/ADR-P2-10.md`](./adr/ADR-P2-10.md)

---

## 1. Nguyên tắc Bảo mật Cốt lõi

1. **Ba lớp bảo vệ đồng thời:** (a) `permission_query_conditions` cho danh sách/báo cáo/tìm kiếm, (b) `has_permission` cho truy cập tài liệu trực tiếp (`GET /api/resource`, `frappe.client.get`, `frappe.desk.form.load.getdoc`), và (c) kiểm tra vai trò + khoảng sở hữu ngay trong tầng `services/*` kèm `doc_events` chặn ghi tắt.
2. **Quyền theo người sở hữu hiện tại (Current-Owner Revocation):**
   - Sau khi `CSKH Work Item` chuyển từ `A -> B` (`reassign`) hoặc bị thu hồi (`revoke`) hay đóng rồi giao sang đội khác:
     - `A` mất ngay quyền đọc `CRM Lead`, `CSKH Work Item`, `CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH Assignment History`, `CRM Task`, `CRM Call Log`, `FCRM Note`, và `File` đính kèm trên cả API tùy chỉnh lẫn generic Frappe REST/RPC/Desk!
     - Trong DB, các bản ghi `CSKH Contact Attempt` cũ vẫn giữ nguyên `performed_by = A` và `owner_at_attempt = A` để phục vụ đối soát lịch sử/KPI công bằng ở P3.
3. **Cô lập Đội nhóm chuẩn `L-23` cho Team Leader (`Sales Manager`):**
   - `Leader A` chỉ đọc/tác nghiệp trên `Work Item`, `Attempt`, `Correction`, `CRM Task`, `CRM Call Log` thuộc nhân viên dưới cây `CRM Sales Hierarchy` của Đội A **và** khách hàng chưa bị chuyển giao sang Đội B.
4. **BOD Viewer (`CSKH BOD Viewer`) 0 PII & Read-Only Tuyệt đối:**
   - Được đọc `workflow.progress_summary`, `workflow.get_outcome_codes`, `settings.list_policies`, `settings.list_outcomes` (**0 PII**).
   - Bị từ chối `403 FORBIDDEN` (hoặc `PermissionError` / `1=0` trên generic API) đối với `CSKH Work Item`, `CSKH Contact Attempt`, `CSKH Attempt Correction`, `CRM Lead`, `CRM Task`, `CRM Call Log`, `FCRM Note`, `File` và mọi endpoint mutation.
5. **Chống XSS, CSV Formula Injection & Rò rỉ PII qua Lỗi:**
   - Trường `note`, `reason`, `completion_reason`, `review_note` được làm sạch thẻ nguy hiểm (`<script>`, `<iframe>`, `<object>`, `javascript:`, event handlers `on*=`) ở tầng server trước khi lưu DB và trung hòa tiền tố công thức (`=`, `+`, `-`, `@`) khi xuất CSV/artifact.
   - Không phản chiếu SĐT, nội dung ghi chú hoặc đường dẫn file riêng tư trong thông báo lỗi hay log hệ thống; bảo toàn 100% cơ chế File-oracle `404` byte-identical của P0.

---

## 2. Ma trận Quyền cấp DocType & Generic Frappe API (`/api/resource/*`, `frappe.client.*`, Desk)

| DocType | `Guest` | `Sales User` (Staff) | `Sales Manager` (Leader) | `CSKH BOD Viewer` | `CSKH CRM Admin` | Ghi chú Cưỡng chế |
|---|---|---|---|---|---|---|
| `CSKH Work Item` | Chặn (`403`) | Đọc: chỉ `assigned_to == user` hiện tại. Ghi/Tạo/Xóa: **Chặn** | Đọc: chỉ phạm vi `team` hiện tại. Ghi/Tạo/Xóa: **Chặn** | **Chặn (`1=0` / `False`)** | Đọc: `all`. Ghi trực tiếp: **Chặn** (chỉ qua service) | Mọi thay đổi trạng thái phải qua `ownership` / `workflow` service. |
| `CSKH Contact Attempt` | Chặn (`403`) | Đọc: chỉ của `Work Item` đang sở hữu hiện tại. Tạo/Sửa/Xóa trực tiếp: **Chặn** | Đọc: chỉ `Work Item` thuộc `team` hiện tại. Tạo/Sửa/Xóa trực tiếp: **Chặn** | **Chặn (`1=0` / `False`)** | Đọc: `all`. Tạo/Sửa/Xóa trực tiếp: **Chặn** | Append-only cứng ở `before_save` & `on_trash`; chỉ chèn qua `contact_attempt` service. |
| `CSKH Attempt Correction` | Chặn (`403`) | Đọc: yêu cầu trên `Work Item` đang sở hữu hiện tại. Ghi trực tiếp: **Chặn** | Đọc: phạm vi `team` hiện tại. Ghi trực tiếp: **Chặn** | **Chặn (`1=0` / `False`)** | Đọc: `all`. Ghi trực tiếp: **Chặn** | Chỉ tạo/duyệt qua `correction` service; `cskh_task_history_json` / metadata nhạy cảm kiểm soát chặt. |
| `CSKH Assignment History` | Chặn (`403`) | Đọc: chỉ khi đang có quyền đọc `CSKH Work Item` tương ứng. Ghi/Xóa: **Chặn** | Đọc: chỉ của `Work Item` thuộc `team` hiện tại. Ghi/Xóa: **Chặn** | Đọc: metadata không PII (SĐT/tên không nằm trên bảng này). Ghi/Xóa: **Chặn** | Đọc: `all`. Ghi/Xóa: **Chặn** | Cắt hoàn toàn quyền đọc lịch sử của `Staff A` sau khi `Work Item` đã chuyển sang `Staff B`. |
| `CSKH Outcome Code` | Chặn (`403`) | Đọc (`enabled=1`). Ghi/Xóa: **Chặn** | Đọc (`enabled=1`). Ghi/Xóa: **Chặn** | Đọc. Ghi/Xóa: **Chặn** | Đọc: `all`. Ghi qua `settings.save_outcome` | Không chứa PII. |
| `CSKH Policy` | Chặn (`403`) | Đọc (Global / scope áp dụng). Ghi/Xóa: **Chặn** | Đọc (Global + Team mình). Ghi/Xóa: **Chặn** | Đọc. Ghi/Xóa: **Chặn** | Đọc: `all`. Ghi qua `settings.save_policy` | Không chứa PII. |
| `CRM Task` | Chặn (`403`) | Đọc: chỉ Task gắn `CRM Lead` đang sở hữu. Tạo/Sửa/Xóa Task `cskh_is_followup=1` trực tiếp: **Chặn** | Đọc: Task gắn `CRM Lead` thuộc `team`. Tạo/Sửa/Xóa Task `cskh_is_followup=1` trực tiếp: **Chặn** | **Chặn** | Giữ nguyên lưới P0 trên generic API; thao tác callback qua `workflow` / `ownership` | Khóa bằng cả `linked_lead_has_permission` lẫn `doc_events["CRM Task"]`. |
| `CRM Call Log` / `FCRM Note` / `File` | Chặn (`403`/`404`) | Đọc: chỉ bản ghi gắn `CRM Lead` đang sở hữu hiện tại | Đọc: chỉ bản ghi gắn `CRM Lead` thuộc `team` hiện tại | **Chặn** | Giữ nguyên lưới P0 (`File` giữ nguyên chuẩn `404` File-oracle) | Sau `A -> B`, `A` mất ngay quyền đọc trên cả 3 bảng và `guarded_crm.get_activities`. |
