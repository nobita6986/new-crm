# ADR-P2-11 — Chiến lược Concurrency (`revision` + `FOR UPDATE`), Idempotency `payload_hash` & Phục hồi Lỗi

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §8, §15.4, §19 (`ADR-P2-11`); `docs/race-retry-reconciliation.md`

## 1. Bối cảnh & Vấn đề
Nhân viên thao tác trên web có thể bấm gửi hai lần khi mạng chậm, mở cùng một khách hàng trên hai tab trình duyệt, hoặc bấm lưu `record_attempt` đúng tích tắc quản trị viên bấm `ownership.reassign(A -> B)`. Nếu không có khóa dòng nhất quán và kiểm tra `payload_hash`, hệ thống có thể tạo 2 `Attempt` trùng, mở 2 `CRM Task` callback song song, hoặc ghi `Attempt` vào sai khoảng sở hữu.

## 2. Quyết định
1. **Khóa dòng chuẩn hóa trên `CSKH Work Item` (`SELECT ... FOR UPDATE`):**
   - Mọi mutation của `workflow`, `correction` và `ownership` đều khóa dòng `CSKH Work Item` đầu tiên trong transaction, tuần tự hóa hoàn toàn các thao tác cạnh tranh trên cùng một Work Item.
2. **Kiểm soát đồng thời lạc quan (`expected_revision`):**
   - `CSKH Work Item.revision` bắt đầu từ `1` và tăng `+1` sau mỗi lần biến đổi trạng thái, sở hữu, lịch hẹn, ghi attempt hoặc duyệt hiệu chỉnh.
   - Client gửi `expected_revision`; nếu lệch với `revision` hiện tại dưới khóa dòng → trả `409 STALE_WORK_ITEM`.
3. **Idempotency hai tầng (Application + MariaDB Unique Constraint):**
   - Chuẩn hóa các tham số nghiệp vụ thành JSON canonical và băm SHA-256 (`payload_hash`).
   - Cùng `idempotency_key` + cùng `payload_hash`: trả lại kết quả gốc (`idempotent_replay = True`).
   - Cùng `idempotency_key` + khác `payload_hash`: từ chối `409 IDEMPOTENCY_CONFLICT`.
   - Ràng buộc cứng ở MariaDB (`UNIQUE(work_item, performed_by, idempotency_key)` trên `CSKH Contact Attempt`, `UNIQUE(requested_by, idempotency_key)` trên `CSKH Attempt Correction`, `UNIQUE(event_id)` trên `CSKH Assignment History`) chặn tuyệt đối mọi khe hở đua tranh song song.
4. **Atomicity & Post-Commit Side Effects:**
   - Sử dụng savepoint giao dịch (`frappe.db.savepoint`); mọi lỗi trung gian (kể cả lỗi `CRM Task` hay lỗi tiêm trong chaos test) đều rollback 100%.
   - Gửi thông báo (`CRM Notification`) tách hẳn ra sau `frappe.db.commit()` và có khả năng chạy lại idempotent.

## 3. Tác động Kiểm thử
- Được chứng minh bằng runtime test cho rollback giữa chừng, retry sau lỗi thông báo, double-submit cùng/khác payload, đua tranh `record_attempt` vs `reassign`, và đua tranh `schedule_followup` (`T2-18..T2-21`).
