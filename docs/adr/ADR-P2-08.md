# ADR-P2-08 — Phiên bản hóa & Chụp Cứng (`Snapshot`) cho `CSKH Outcome Code` và `CSKH Policy`

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §6.2, §6.5, §19 (`ADR-P2-08`, `ADR-P2-09`)

## 1. Bối cảnh & Vấn đề
Nếu báo cáo lịch sử hoặc P3 KPI chỉ `JOIN` động từ `CSKH Contact Attempt.outcome_code` sang bảng `CSKH Outcome Code` hiện tại, bất kỳ thay đổi nhãn hay cờ (`requires_followup`, `is_interested`, `is_terminal`) của Admin trong tương lai sẽ làm trôi ý nghĩa của hàng vạn bản ghi lịch sử cũ.

## 2. Quyết định
1. Cả `CSKH Outcome Code` và `CSKH Policy` đều có cột `version` (`Int`, khởi tạo `= 1`), tự động tăng `version += 1` mỗi khi có thay đổi qua `settings.save_outcome` / `settings.save_policy`.
2. Mã máy `CSKH Outcome Code.code` là bất biến; không cho xóa mã đã từng được tham chiếu (chỉ cho tắt `enabled = 0`); các mã `unreached` chuẩn (`no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `wrong_number`) bị khóa cứng `is_connected = 0` ở cả controller lẫn service.
3. Mỗi `CSKH Contact Attempt` lưu cứng bộ snapshot tại thời điểm ghi (`outcome_version_snapshot`, `outcome_label_snapshot`, `outcome_group_snapshot`, `contact_attempt_snapshot`, `connected_snapshot`, `interested_snapshot`, `terminal_snapshot`, `dnc_snapshot`, `policy_name_snapshot`, `policy_version_snapshot`).
4. Mọi phép tính read-model (`attempt_count`, `connected_attempt_count`, `progress_summary`) đọc từ **snapshot hiệu lực** (snapshot gốc của `Attempt` hoặc snapshot mới lưu trong `CSKH Attempt Correction` đã được `approved`), không phụ thuộc cấu hình hiện tại của bảng danh mục.

## 3. Tác động Kiểm thử
- Được ghim bởi `T2-22` (thay đổi Outcome/Policy sang `v2` không làm trôi lịch sử `v1`).
