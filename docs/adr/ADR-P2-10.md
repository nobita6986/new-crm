# ADR-P2-10 — Phân quyền Đa lớp, Thu hồi PII Sau Chuyển giao `A → B` & Khóa Generic API

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §11, §15.2 (`SEC2-01..SEC2-16`), §19 (`ADR-P2-10`); `docs/security-matrix-p2.md`

## 1. Bối cảnh & Vấn đề
Trong Frappe, chỉ đặt kiểm tra quyền ở custom API là không đủ vì người dùng đã đăng nhập vẫn có thể gọi `/api/resource/<DocType>`, `frappe.client.get / get_list / insert / set_value`, `frappe.desk.reportview.get`, hoặc `frappe.desk.form.load.getdoc`. Ngoài ra, sau khi một `Work Item` chuyển từ `Staff A` sang `Staff B`, `Staff A` vẫn còn tên trong `CSKH Contact Attempt.performed_by` và `CSKH Assignment History.to_user` của khoảng sở hữu cũ — nếu lọc quyền theo `performed_by` hoặc `to_user`, `Staff A` sẽ tiếp tục đọc được timeline/PII của khách hàng đã chuyển giao!

## 2. Quyết định
1. **Quyền đọc gắn chặt với quyền hiện hành trên `CSKH Work Item` / `CRM Lead`:**
   - `CSKH Contact Attempt`, `CSKH Attempt Correction`, và `CSKH Assignment History` kiểm tra quyền đọc thông qua quyền hiện tại của caller trên `CSKH Work Item` (và áp cùng mệnh đề cô lập đội nhóm SQL `L-23` cho `Sales Manager`).
   - Hệ quả: ngay khi `ownership.reassign(A -> B)` hoặc `ownership.revoke` commit, `Staff A` lập tức mất quyền đọc trên cả `CRM Lead`, `CSKH Work Item`, `CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH Assignment History`, `CRM Task`, `CRM Call Log`, `FCRM Note`, và `File` (`SEC2-03`, `SEC2-10`, `SEC2-12`, `SEC2-13`), trong khi cột `performed_by = A` trong DB vẫn được bảo toàn nguyên vẹn cho đối soát lịch sử.
2. **Khóa mọi đường ghi tắt trên Generic API:**
   - `CSKH Contact Attempt` và `CSKH Attempt Correction`: `has_permission` trả `False` cho mọi `create/write/delete`; controller kiểm tra cờ ngữ cảnh nội bộ của service.
   - `CRM Task`: hook `doc_events["CRM Task"]` chặn tạo/sửa/xóa trực tiếp ngoài `followup` service đối với mọi Task gắn `cskh_is_followup = 1` hoặc `cskh_work_item` (`SEC2-05`).
   - `validate_p1_filter_fields` mở rộng bảo vệ các trường nhạy cảm của P2 trên `reportview` / `get_list`.
3. **BOD Viewer (`CSKH BOD Viewer`):**
   - `0 PII` tuyệt đối: chặn đọc trực tiếp `Work Item` / `Attempt` / `Correction` / `Lead` / `Task` / `Call Log` / `Note` / `File`; chỉ mở `workflow.progress_summary` và danh mục cấu hình tổng hợp không PII (`SEC2-04`, `SEC2-09`).
4. **Làm sạch Ghi chú & Ẩn PII trong Lỗi:**
   - Sanitize `note` / `reason` ở server trước khi lưu; trung hòa công thức CSV; không phản chiếu SĐT/nội dung nhạy cảm trong thông báo lỗi; giữ nguyên bảo vệ File-oracle `404` của P0 (`SEC2-11`, `SEC2-13..SEC2-15`).

## 3. Tác động Kiểm thử
- Được ghim bởi toàn bộ 16 nhóm kiểm thử bảo mật `SEC2-01..SEC2-16` trên cả unit test lẫn HTTP transport probe thật (`ci_p2_http_probe.py`), đồng thời giữ xanh `99/99` P0 và `209/209` P1.
