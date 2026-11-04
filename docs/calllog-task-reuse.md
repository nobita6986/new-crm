# P2 — Adapter Tái sử dụng `CRM Call Log`, `CRM Task` & `FCRM Note`

> **Tài liệu:** `docs/calllog-task-reuse.md`
> **Phụ thuộc:** [`docs/P2-entry-report.md`](./P2-entry-report.md) (`E2-03..E2-05`), [`docs/adr/ADR-P2-02.md`](./adr/ADR-P2-02.md), [`docs/adr/ADR-P2-04.md`](./adr/ADR-P2-04.md), [`docs/adr/ADR-P2-05.md`](./adr/ADR-P2-05.md)

---

## 1. Tái sử dụng `CRM Call Log` (`crm_cskh.services.call_log_adapter`)

### 1.1. Schema thực tế đã kiểm chứng ở `frappe/crm@v1.87.0`
- `apps/crm/crm/fcrm/doctype/crm_call_log/crm_call_log.json`:
  - `autoname = "field:id"` (tự sinh hash 12 ký tự nếu trống).
  - Các trường bắt buộc (`reqd = 1`): `from` (`Phone`), `to` (`Phone`), `type` (`Incoming | Outgoing`), `status` (`Initiated | Ringing | In Progress | Completed | Failed | Busy | No Answer | Queued | Canceled`).
  - `telephony_medium`: `"" | Manual | Twilio | Exotel` (mặc định `"Manual"`).

### 1.2. Quy tắc Adapter Không Bịa Dữ liệu & Chống Đếm Trùng
1. **Chỉ dành cho kênh `phone_call`:** Nếu `channel != "phone_call"` mà truyền `call_log` hoặc `call_log_data`, trả ngay `422 OUTCOME_NOT_COMPATIBLE` — tuyệt đối không tạo `CRM Call Log` giả cho `sms`, `zalo`, `facebook`, `other`.
2. **Chế độ 1 — Liên kết `CRM Call Log` có sẵn (`call_log = "<id>"`):**
   - Kiểm tra `CRM Call Log` tồn tại và tham chiếu đúng `CRM Lead` của `Work Item` (hoặc khớp số điện thoại chuẩn hóa của Lead khi chưa gắn `reference_docname` và người gọi/nhận thuộc quyền hợp lệ).
   - Kiểm tra `CSKH Contact Attempt` chưa có dòng nào gắn `call_log` này (`UNIQUE (call_log)`). Nếu đã gắn → trả `409 ALREADY_LINKED`.
   - Gắn `reference_doctype = "CRM Lead"`, `reference_docname = work_item.lead` nếu bản ghi gốc chưa gắn.
   - **Không suy diễn `is_connected` từ `CRM Call Log.status`:** ngay cả khi `CRM Call Log.status == "Completed"`, cờ `connected` của lần liên hệ vẫn tuân theo `CSKH Outcome Code` mà nhân viên chọn (ví dụ hộp thư thoại tự bắt máy vẫn là `no_answer` / `unreachable`, `is_connected = 0`).
3. **Chế độ 2 — Tạo `CRM Call Log` thủ công qua `call_log_data`:**
   - Chỉ tạo khi `call_log_data` cung cấp trung thực đủ trường bắt buộc (`type`, `status`, số bên gọi `from` và số bên nhận `to` hợp lệ; với cuộc gọi đi `Outgoing`, `to` mặc định lấy từ `CRM Lead.mobile_no` và `from` lấy từ `call_log_data.from` hoặc SĐT thật đã cấu hình trên hồ sơ `User`).
   - Nếu thiếu SĐT người gọi (`from`) hoặc dữ liệu không hợp lệ → từ chối `400 INVALID_REQUEST` / `422 OUTCOME_NOT_COMPATIBLE` (rollback toàn bộ), **không bao giờ tự điền số giả như `0000000000`**.
   - Nếu nhân viên ghi nhận cuộc gọi qua form CSKH bình thường mà không truyền `call_log` hay `call_log_data` → chỉ lưu `CSKH Contact Attempt` (`call_log = NULL`).
4. **`CRM Call Log` chưa liên kết:** Không tự động cộng vào `attempt_count`; có thể tra cứu qua `workflow.list_unlinked_call_logs(work_item)` để nhân viên đối soát và liên kết chính thức.

---

## 2. Tái sử dụng `CRM Task` làm Callback (`crm_cskh.services.followup`)

1. **Không tạo DocType `Followup Task` trùng lặp:** Dùng trực tiếp `CRM Task` với `cskh_is_followup = 1`, `cskh_work_item = <work_item>`, `reference_doctype = "CRM Lead"`, `reference_docname = <lead>`.
2. **Tập trạng thái native chính xác:**
   - Mở: `Todo` (mặc định khi tạo), `In Progress`, `Backlog`.
   - Đóng hoàn tất (gắn với Attempt thực): `Done` (`cskh_completed_attempt` bắt buộc).
   - Đóng hủy (có lý do audit): `Canceled` (`cskh_completion_reason` bắt buộc).
3. **Kiểm soát Side-Effect của `CRMTask.assign_to()`:**
   - Upstream `CRMTask.after_insert`/`validate` gọi `frappe.desk.form.assign_to.add/remove` làm bắn `CRM Notification` ngay bên trong DB transaction và gây `PermissionError` khi actor là `CSKH CRM Admin`.
   - Service `followup` chèn `CRM Task` với cờ nội bộ và đồng bộ `assigned_to` + `ToDo` (`reference_type="CRM Task"`, `reference_name=str(task.name)`) trực tiếp trong transaction (tương tự `ownership._sync_todo`), còn `CRM Notification` chỉ được phát **sau khi `frappe.db.commit()` thành công**.
4. **Chặn bypass qua Generic API:** Hook `doc_events["CRM Task"]` chặn mọi thao tác tạo/sửa/xóa trực tiếp từ bên ngoài `followup` service đối với các bản ghi `cskh_is_followup == 1` (hoặc cố tự gán `cskh_work_item`).

---

## 3. Vai trò của `FCRM Note`

- `FCRM Note` tiếp tục phục vụ ghi chú tự do trên hồ sơ khách hàng Frappe CRM và được bảo vệ bởi `linked_lead_has_permission` + `guarded_crm.get_activities`.
- Tạo/sửa `FCRM Note` không bao giờ làm thay đổi `CSKH Work Item.status`, `attempt_count`, hay `connected_attempt_count`.
