# P2 — Bất biến Nhật ký Liên hệ & Quy trình Hiệu chỉnh (`Attempt Immutability & Correction`)

> **Tài liệu:** `docs/attempt-immutability-correction.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§6.3, §6.4, §9.5), [`docs/data-model-p2.md`](./data-model-p2.md), [`docs/adr/ADR-P2-03.md`](./adr/ADR-P2-03.md)

---

## 1. Quy tắc Bất biến của `CSKH Contact Attempt`

1. **Append-only tuyệt đối ở tầng Model & API:**
   - Bản ghi `CSKH Contact Attempt` chỉ được chèn mới (`insert`) từ bên trong context giao dịch được ủy quyền của `crm_cskh.services.contact_attempt`.
   - Trong controller `ContactAttempt.before_save`: nếu `not self.is_new()`, ném `frappe.ValidationError` ngay lập tức (không cho phép `save()` kể cả khi gọi với `ignore_permissions=True`).
   - Trong controller `ContactAttempt.on_trash`: luôn ném `frappe.ValidationError` chặn xóa cứng.
   - Trên generic REST (`/api/resource/CSKH Contact Attempt`) và RPC (`frappe.client.insert/set_value/save/delete`): `has_permission` trả `False` cho mọi `ptype in ("create", "write", "delete")`.
2. **Không cho giả mạo người thực hiện hoặc trạng thái xác minh:**
   - `performed_by` luôn do server gán cứng `= actor` (`frappe.session.user`); tham số `performed_by` gửi từ client (nếu khác `actor`) bị từ chối `403 FORBIDDEN`.
   - `verification_status` mặc định là `"self_reported"`; `Sales User` hoặc `Sales Manager` truyền `"verified"` bị từ chối `403 FORBIDDEN` (chỉ quy trình QA/Admin ở P3 hoặc Admin có kiểm toán mới được xác minh).
3. **Đóng băng ngữ nghĩa lịch sử (Snapshot Isolation):**
   - Mỗi `Attempt` lưu cứng `outcome_version_snapshot`, `outcome_label_snapshot`, `outcome_group_snapshot`, `contact_attempt_snapshot`, `connected_snapshot`, `interested_snapshot`, `terminal_snapshot`, `dnc_snapshot`, `policy_name_snapshot`, `policy_version_snapshot`, `ownership_event` và `owner_at_attempt`.
   - Khi `CSKH Outcome Code` hoặc `CSKH Policy` tăng `version`, các `Attempt` đã ghi giữ nguyên snapshot tại thời điểm diễn ra.

---

## 2. Mô hình Hiệu chỉnh Có Kiểm toán (`CSKH Attempt Correction`)

Khi nhân viên nhập nhầm kết quả, kênh, thời điểm hoặc tạo nhầm một lần liên hệ:

1. **Ba loại hành động (`action`):**
   - `correct`: đè lớp phủ hiệu lực (`effective view`) lên một tập trường cho phép (`outcome_code`, `channel`, `occurred_at`, `note`, `call_log`). Khi đổi `outcome_code`, hệ thống kiểm tra lại đầy đủ tính tương thích kênh, `requires_note`, `occurred_at` trong khoảng sở hữu của `Attempt` gốc (`ownership_event`), và chụp bộ snapshot mới của `Outcome Code` vào `corrected_fields_json`.
   - `void`: hủy hiệu lực thống kê của `Attempt` nhập nhầm. Bản ghi gốc vẫn hiện trên timeline với cờ `is_voided = True` kèm lý do và người duyệt, nhưng **không tính** vào `attempt_count`, `connected_attempt_count`, `first_attempt_at`, `last_attempt_at`, `last_outcome_code`.
   - `restore`: khôi phục lại hiệu lực cho một `Attempt` đã bị `void` nhầm (chỉ `CSKH CRM Admin` được thực hiện, bắt buộc có `reason`).
2. **Phân quyền Yêu cầu & Phê duyệt:**
   - `Sales User`: chỉ được gửi `request_correction` (`action in ("correct", "void")`) cho `Attempt` do chính mình thực hiện (`performed_by == actor`) trên `Work Item` mình đang sở hữu hiện tại → tạo bản ghi `status = "pending"`. Không được tự duyệt (`auto_approve = True` → `403 FORBIDDEN`).
   - `Sales Manager` (Team Leader): khi `policy.allow_leader_review_correction == 1`, được duyệt (`approve`/`reject`) yêu cầu `pending` hoặc tạo hiệu chỉnh cho `Work Item` thuộc phạm vi `team` hợp lệ.
   - `CSKH CRM Admin`: có toàn quyền tạo và phê duyệt `correct`, `void`, `restore` kèm lý do kiểm toán.
3. **Quy tắc Ghép `Effective View` & Tính lại Read-Model:**
   - Với mỗi `CSKH Contact Attempt`, hệ thống áp tuần tự các `CSKH Attempt Correction` có `status == "approved"` theo thứ tự thời gian phê duyệt (`reviewed_at asc, creation asc`).
   - **Không bao giờ nhân đôi số lần liên hệ:** 1 `Attempt` + N `Correction` vẫn chỉ tối đa là **1** sự kiện liên hệ hiệu lực (hoặc **0** nếu trạng thái cuối là `void`).
   - Ngay trong cùng DB transaction khi `Correction` chuyển sang `approved`, `recompute_work_item_read_model(work_item)` tính lại chính xác `attempt_count`, `connected_attempt_count`, `first_attempt_at`, `last_attempt_at`, `last_connected_at`, `last_outcome_code`, `last_outcome_label` và tăng `Work Item.revision`.
