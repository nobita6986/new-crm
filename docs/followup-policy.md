# P2 — Chính sách Hẹn chăm sóc lại, Backdate & DNC (`Follow-up & Operational Policy`)

> **Tài liệu:** `docs/followup-policy.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§6.2, §6.5, §7, §8), [`docs/state-machine.md`](./state-machine.md), [`docs/adr/ADR-P2-04.md`](./adr/ADR-P2-04.md), [`docs/adr/ADR-P2-06.md`](./adr/ADR-P2-06.md), [`docs/adr/ADR-P2-07.md`](./adr/ADR-P2-07.md), [`docs/adr/ADR-P2-09.md`](./adr/ADR-P2-09.md)

---

## 1. Phân giải `CSKH Policy` theo Thứ tự Ưu tiên Xác định (`ADR-P2-09`)

Khi đánh giá chính sách cho một `CSKH Work Item` (thuộc `campaign` và đang do `owner` thuộc `team` phụ trách):

1. **Cấp Chiến dịch (`scope_type = "Campaign"`):** Policy đang bật (`enabled = 1`) có `campaign == work_item.campaign`.
2. **Cấp Đội nhóm (`scope_type = "Team"`):** Policy đang bật có `team` khớp `campaign.team_scope` (hoặc nút team quản lý trực tiếp của `assigned_to` trong `CRM Sales Hierarchy`).
3. **Cấp Toàn cục (`scope_type = "Global"`):** Policy mặc định `GLOBAL-DEFAULT` (`enabled = 1`, được seed tự động khi `bench migrate`).

> **Không bịa SLA/cadence doanh nghiệp:** P2 chỉ kiểm tra ràng buộc hợp lệ kỹ thuật và nghiệp vụ nền tảng (`allowed_backdate_minutes`, `max_open_followup_tasks_per_item = 1`, `require_attempt_before_close`, `enabled_channels`, `do_not_contact_behavior`). Các ngưỡng chấm điểm SLA/KPI thuộc phạm vi P3.

---

## 2. Quy tắc Một Lịch hẹn Mở Duy nhất (`max_open_followup_tasks_per_item = 1`)

1. Mỗi `CSKH Work Item` tại mọi thời điểm chỉ có tối đa **01** `CRM Task` (`cskh_is_followup = 1`, `status in ("Todo", "In Progress", "Backlog")`).
2. **Hạn hẹn hợp lệ (`due_at`):** Bắt buộc lớn hơn thời điểm hiện tại trên server (`due_at > now_datetime()`); nếu ở quá khứ hoặc hiện tại → trả `422 INVALID_FOLLOWUP_TIME`.
3. **Dời lịch hẹn (`Reschedule`):**
   - Gọi `workflow.schedule_followup` trên `Work Item` đang có `open_callback_task` sẽ **cập nhật trực tiếp `due_date` của chính Task đó**, tăng `cskh_reschedule_count += 1`, nối bản ghi `{at, by, old_due_at, new_due_at, reason}` vào `cskh_task_history_json`, và đồng bộ `Work Item.next_action_due = due_at`.
   - Bắt buộc nhập `reason` khi dời lịch (`422 NOTE_REQUIRED` nếu thiếu).
4. **Chuyển giao sở hữu `A -> B` (`ownership.reassign`):**
   - Nếu `Work Item` đang có `open_callback_task`: chuyển `CRM Task.assigned_to` từ `A` sang `B`, đóng `ToDo` của `A`, mở `ToDo` cho `B`, **giữ nguyên `due_date`** và `Work Item.next_action_due`, giữ `Work Item.status = "on_hold"`, ghi vết chuyển giao vào `cskh_task_history_json`.
   - Mọi `CSKH Contact Attempt` trước thời điểm chuyển giao giữ nguyên `performed_by = A` và `owner_at_attempt = A`.
5. **Thu hồi (`ownership.revoke`) hoặc Đóng (`workflow.close_work_item`):**
   - Trong **cùng DB transaction**, mọi `CRM Task` callback đang mở của `Work Item` được chuyển sang `status = "Canceled"`, đóng `ToDo`, ghi rõ `cskh_completion_reason`, xóa `open_callback_task = NULL` và `next_action_due = NULL`. Đảm bảo **0 Task mở mồ côi**.

---

## 3. Quy tắc Thời điểm Liên hệ & Backdate (`ADR-P2-06`)

Khi nhận `occurred_at` trong `record_attempt` (nếu bỏ trống thì server gán `now_datetime()`):

1. **Không ở tương lai:** Cho phép độ lệch đồng hồ tối đa `60 giây`; nếu `occurred_at > now + 60s` → trả `422 INVALID_FOLLOWUP_TIME`.
2. **Trong cửa sổ backdate của Policy:** Nếu `occurred_at < now - allowed_backdate_minutes` → trả `422 INVALID_FOLLOWUP_TIME`.
3. **Nằm trong khoảng sở hữu đang mở của Actor (`Ownership Interval Boundary`):**
   - Truy vấn bản ghi `CSKH Assignment History` đang mở (`ownership_end IS NULL`) của `work_item`.
   - Bắt buộc `occurred_at >= active_interval.ownership_start` (trừ hao làm tròn mili-giây `1s`). Nhân viên `B` vừa nhận bàn giao từ `A` **không thể** lùi `occurred_at` về khoảng thời gian khi `Work Item` còn thuộc `A` (`422 INVALID_FOLLOWUP_TIME` / `409 OWNERSHIP_CHANGED`).

---

## 4. Quy tắc Bền vững Cờ Từ chối Liên hệ (`Do Not Contact — DNC`, `ADR-P2-07`)

1. Khi ghi nhận (hoặc hiệu chỉnh được duyệt sang) một `Outcome Code` có `is_do_not_contact == 1`:
   - `CRM Lead.cskh_do_not_contact = 1`, lưu `cskh_dnc_reason`, `cskh_dnc_at`, `cskh_dnc_work_item`.
   - Hủy mọi callback `CRM Task` đang mở trên `Work Item` và đóng `Work Item` theo `policy.do_not_contact_behavior` (`block_and_auto_close` mặc định).
2. **Bảo vệ xuyên suốt vòng đời dữ liệu:**
   - **Khi Re-import:** `import_worker` reuse Lead canonical hiện có **không bao giờ** xóa hoặc ghi đè `cskh_do_not_contact`.
   - **Khi Phân phối Chiến dịch mới:** `eligibility._lead_exclusion_reason` loại ngay các Lead có `cskh_do_not_contact == 1` với mã lý do `"do_not_contact"`.
   - **Khi Tác nghiệp / Mở lại:** `record_attempt`, `schedule_followup`, và `reopen_work_item` trên Lead đang `cskh_do_not_contact == 1` bị chặn với `409 DO_NOT_CONTACT` (chỉ `CSKH CRM Admin` có thể gỡ cờ có lý do audit qua `settings.set_lead_dnc` hoặc `override_dnc=True` khi mở lại).
