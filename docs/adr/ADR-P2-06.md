# ADR-P2-06 — Quy tắc Thời điểm Liên hệ (`occurred_at`), Cửa sổ Backdate & Ràng buộc Khoảng Sở hữu

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §8.1, §9.1, §19 (`ADR-P2-06`)

## 1. Bối cảnh & Vấn đề
Nhân viên có thể ghi nhật ký ngay sau khi vừa kết thúc cuộc gọi vài phút, nhưng nếu cho phép lùi `occurred_at` tùy ý (hoặc lùi về trước lúc được giao khách, hoặc đặt giờ ở tương lai), dữ liệu thời gian phản hồi đầu tiên (`first_attempt_at`) và phân bổ theo ca/người sở hữu ở P3 sẽ bị sai lệch.

## 2. Quyết định
Mọi thao tác ghi nhận hoặc hiệu chỉnh `occurred_at` được kiểm chứng nghiêm ngặt theo múi giờ chuẩn của site (`Asia/Ho_Chi_Minh`):
1. **Tách hai mốc thời gian:**
   - `recorded_at`: luôn do server gán cứng `= now_datetime()` (không thể sửa).
   - `occurred_at`: mặc định `= recorded_at` nếu không truyền; nếu truyền phải thỏa đồng thời cả 3 điều kiện dưới đây.
2. **Không nằm ở tương lai:** `occurred_at <= recorded_at + 60 giây` (dung sai đồng hồ nhỏ); vi phạm trả `422 INVALID_FOLLOWUP_TIME`.
3. **Trong giới hạn `CSKH Policy.allowed_backdate_minutes`:** `occurred_at >= recorded_at - allowed_backdate_minutes` (mặc định seed `120` phút; khi `allowed_backdate_minutes == 0`, chỉ chấp nhận sai số tối đa `60 giây`); vi phạm trả `422 INVALID_FOLLOWUP_TIME`.
4. **Không vượt qua ranh giới khoảng sở hữu (`Ownership Interval`):**
   - Khi ghi mới (`record_attempt`): `occurred_at >= active_ownership_event.ownership_start - 1 giây`.
   - Khi hiệu chỉnh (`request_correction` đổi `occurred_at`): `occurred_at` phải nằm gọn trong `[attempt_ownership_event.ownership_start, attempt_ownership_event.ownership_end or now_datetime()]`.

## 3. Tác động Kiểm thử
- Được ghim bởi `T2-05`, `T2-16`, `T2-19` và kiểm tra đối soát số 4 trong `reconcile_p2_invariants`.
