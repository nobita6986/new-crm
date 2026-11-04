# ADR-P2-09 — Thứ tự Ưu tiên Xác định của `CSKH Policy` (`Campaign > Team > Global`)

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §6.5, §19 (`ADR-P2-09`); `docs/followup-policy.md`

## 1. Bối cảnh & Vấn đề
Hệ thống cần cho phép cấu hình chính sách mặc định toàn công ty nhưng vẫn linh hoạt tinh chỉnh cho một đội nhóm cụ thể hoặc một chiến dịch đặc thù mà không phải sửa code, đồng thời đảm bảo kết quả phân giải chính sách luôn đơn trị (deterministic) và không bao giờ rơi vào trạng thái thiếu chính sách.

## 2. Quyết định
1. **Ba cấp phạm vi (`scope_type`):** `Campaign`, `Team`, `Global`. Mỗi tổ hợp `(scope_type, campaign, team)` chỉ có tối đa **01** chính sách đang bật (`enabled = 1`).
2. **Thứ tự ưu tiên phân giải cho `Work Item`:**
   - **Ưu tiên 1 (`Campaign`):** Policy đang bật có `campaign == work_item.campaign`.
   - **Ưu tiên 2 (`Team`):** Policy đang bật có `team == campaign.team_scope` (hoặc nếu chiến dịch chung `team_scope IS NULL`, xét nút team quản lý trực tiếp của `work_item.assigned_to` trong `CRM Sales Hierarchy`).
   - **Ưu tiên 3 (`Global`):** Policy mặc định `GLOBAL-DEFAULT` (luôn tồn tại nhờ seed idempotent trong migration; không cho phép tắt `GLOBAL-DEFAULT` nếu không có Global policy thay thế).
3. **Không bịa SLA/cadence doanh nghiệp:** Policy P2 chỉ chứa các tham số kiểm soát quy trình nền tảng (`allowed_backdate_minutes`, `max_open_followup_tasks_per_item = 1`, `require_attempt_before_close`, `allow_close_without_contact_for_invalid`, `do_not_contact_behavior`, `enabled_channels`, `allow_leader_reopen`, `allow_leader_review_correction`).

## 3. Tác động Kiểm thử
- Được kiểm chứng trong `T2-05`, `T2-08`, `T2-09`, `T2-22`, `T2-23`.
