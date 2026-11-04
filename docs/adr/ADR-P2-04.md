# ADR-P2-04 — Tái sử dụng `CRM Task` làm Callback, Chuyển giao `A → B` & Đồng bộ Thông báo Hậu Commit

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §7, §19 (`ADR-P2-04`); `docs/P2-entry-report.md` (`E2-04`)

## 1. Bối cảnh & Phát hiện Runtime trên `frappe/crm@v1.87.0`
Kiểm chứng trực tiếp `apps/crm/crm/fcrm/doctype/crm_task/crm_task.{json,py}` và `apps/crm/crm/api/todo.py` trên `p2dev.localhost` ghi nhận 4 sự thật kỹ thuật quan trọng:
1. `CRM Task.name` dùng `autoincrement` (`int` khi vừa `insert`, cần ép `str(task.name)`).
2. `CRM Task.status` có tập giá trị **`Backlog | Todo | In Progress | Done | Canceled`** (`"Todo"` là trạng thái mở chuẩn, không phải `"Open"`).
3. `crm/api/todo.py:45-47` (`clear_owner_on_unassign`) bỏ qua `CRM Task`, nên đóng/chuyển `ToDo` của `CRM Task` **không làm mất `CRM Lead.lead_owner`**.
4. Tuy nhiên, `CRMTask.after_insert` (`crm_task.py:30-31`) và `CRMTask.validate` (`crm_task.py:33-40`) gọi `frappe.desk.form.assign_to.add/remove`, dẫn tới hai hệ quả không chấp nhận được nếu gọi thô:
   - Bắn `CRM Notification` **ngay bên trong DB transaction** (`crm/api/todo.py:28-30`), trái quy tắc *"Notification chỉ sau commit"* (`P2.md` §8.1).
   - Gây `PermissionError` khi `CSKH CRM Admin` thực hiện `ownership.reassign` (vì `CSKH CRM Admin` không được cấp `DocPerm` trực tiếp trên `CRM Task` nhằm bảo vệ lưới quyền P0).

## 2. Các Phương án Đã Xét
- **Phương án A:** Tạo DocType mới `CSKH Followup Task`.
  - *Loại bỏ:* Trái quy tắc bắt buộc *"Reuse `CRM Task` làm callback, không tạo bảng Followup Task mới"* (`P2.md` §0.7, §7.1).
- **Phương án B:** Cấp `Custom DocPerm` `write=1` cho `CSKH CRM Admin` trên `CRM Task` và dùng nguyên `CRMTask.assign_to()`.
  - *Loại bỏ:* Làm thay đổi mặt phẳng quyền generic CRM của P0 và vẫn bị bắn notification bên trong transaction.
- **Phương án C (Chọn):** Reuse `CRM Task` mở rộng bằng Custom Fields `cskh_*`; trong `crm_cskh.services.followup` quản lý nguyên tử `CRM Task.assigned_to` + `ToDo` trực tiếp trong transaction (tắt side-effect `assign_to.add` nội bộ qua cờ kiểm soát), phát `CRM Notification` sau `frappe.db.commit()`, và gắn `doc_events["CRM Task"]` chặn mọi chỉnh sửa tắt từ generic API.

## 3. Quyết định
Chọn **Phương án C**:
1. Tối đa **01** `CRM Task` mở (`cskh_is_followup = 1`, `status in ("Todo", "In Progress", "Backlog")`) trên mỗi `CSKH Work Item`.
2. Reschedule cập nhật trực tiếp cùng `CRM Task` đang mở, tăng `cskh_reschedule_count` và nối lịch sử vào `cskh_task_history_json`.
3. Khi `ownership.reassign(A -> B)`: nếu có `open_callback_task`, chuyển `CRM Task.assigned_to` và `ToDo` từ `A` sang `B`, **giữ nguyên `due_date`**, giữ `Work Item.status = "on_hold"`, giữ nguyên `performed_by = A` trên các `Attempt` cũ, và thu hồi toàn bộ quyền đọc của `A`.
4. Khi `ownership.revoke` hoặc `workflow.close_work_item`: chuyển mọi callback `CRM Task` đang mở sang `status = "Canceled"` kèm lý do ngay trong **cùng DB transaction**.
5. Cấm đánh dấu `CRM Task` `Done` mà không gắn `cskh_completed_attempt` thực tế.

## 4. Tác động Quyền, Kiểm thử & Chi phí Nâng cấp
- **Quyền:** Giữ nguyên 100% lưới quyền P0 (`99/99`); chặn Staff tự sửa/đóng `CRM Task` callback qua `/api/resource/CRM Task` (`SEC2-05`).
- **Kiểm thử:** `T2-06..T2-09`, `T2-14`, `T2-16`, `T2-17`, `T2-20`, `SEC2-05`, `SEC2-10`, `SEC2-12`.
