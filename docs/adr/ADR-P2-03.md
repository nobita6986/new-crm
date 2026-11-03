# ADR-P2-03 — Bất biến `CSKH Contact Attempt` & Cơ chế Hiệu chỉnh Lớp phủ (`CSKH Attempt Correction`)

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §6.3, §6.4, §9.5, §19 (`ADR-P2-03`); `docs/attempt-immutability-correction.md`

## 1. Bối cảnh & Vấn đề
Trong vận hành CSKH thực tế, nhân viên có thể chọn nhầm mã kết quả hoặc nhập nhầm một lần liên hệ. Nếu cho phép `UPDATE` hoặc `DELETE` trực tiếp `CSKH Contact Attempt`, lịch sử gốc bị xóa dấu vết và nhân viên có thể làm đẹp số liệu trước kỳ đánh giá KPI P3. Ngược lại, nếu cấm sửa hoàn toàn mà không có cơ chế bù trừ, dữ liệu báo cáo sẽ sai lệch vĩnh viễn.

## 2. Các Phương án Đã Xét
- **Phương án A:** Cho phép sửa trực tiếp `CSKH Contact Attempt` trong 15 phút đầu và bật `track_changes = 1` của Frappe.
  - *Nhược điểm:* `Version` log của Frappe là JSON thô không có cấu trúc phê duyệt, khó truy vấn đối soát và vẫn thay đổi bản ghi gốc.
- **Phương án B (Chọn):** Khóa bất biến tuyệt đối `CSKH Contact Attempt` (`before_save` chặn sửa khi `not is_new()`, `on_trash` chặn xóa), và xây dựng `CSKH Attempt Correction` (`action`: `correct | void | restore`, `status`: `pending | approved | rejected`) để tạo `effective view` + tính lại các cột read-model trên `CSKH Work Item`.

## 3. Quyết định
Chọn **Phương án B**:
1. `Staff` chỉ được tạo yêu cầu `pending` (`correct` hoặc `void`) trên Attempt do chính mình ghi thuộc Work Item mình đang sở hữu; không được tự duyệt.
2. `Team Leader` (trong phạm vi team khi `policy.allow_leader_review_correction = 1`) và `CSKH CRM Admin` thực hiện `review_correction` (`approve` / `reject`). Riêng `restore` chỉ dành cho `CSKH CRM Admin`.
3. Khi `approved`, hệ thống áp các bản hiệu chỉnh theo thứ tự thời gian phê duyệt để tính lại chính xác `attempt_count`, `connected_attempt_count`, `first_attempt_at`, `last_attempt_at`, `last_outcome_code`, **không bao giờ cộng đúp số lần liên hệ**.

## 4. Tác động Quyền, Kiểm thử & Chi phí Nâng cấp
- **Quyền & Kiểm toán:** Giữ trọn cả bản ghi gốc (`original`) lẫn giá trị hiệu lực (`effective`) trên timeline kiểm toán.
- **Kiểm thử:** `T2-10`, `T2-11`, `SEC2-02`, và kiểm tra đối soát `reconcile_p2_invariants`.
