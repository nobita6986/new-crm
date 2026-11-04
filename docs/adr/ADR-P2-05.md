# ADR-P2-05 — Tái sử dụng `CRM Call Log` Có Kiểm soát & Không Ngụy tạo Dữ liệu Thoại

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §9.4, §19 (`ADR-P2-05`); `docs/calllog-task-reuse.md`

## 1. Bối cảnh & Vấn đề
Ở `frappe/crm@v1.87.0` (`apps/crm/crm/fcrm/doctype/crm_call_log/crm_call_log.json`), `CRM Call Log` yêu cầu bắt buộc 4 trường: `from` (`Phone`), `to` (`Phone`), `type` (`Incoming|Outgoing`), và `status`. Trong giai đoạn P2 chưa tích hợp tổng đài tự động (P4/ngoài P2), nhân viên ghi nhận liên hệ chủ yếu trên web và thường chưa có số máy nhánh tổng đài gắn trên `User`.

## 2. Các Phương án Đã Xét
- **Phương án A:** Tự động tạo `CRM Call Log` cho mọi `Attempt` (kể cả khi thiếu số người gọi thì điền `"0000000000"` hoặc tạo cho cả Zalo/SMS).
  - *Loại bỏ:* Vi phạm trực tiếp quy tắc không tạo dữ liệu cuộc gọi giả (`P2.md` §9.4).
- **Phương án B:** Bỏ hoàn toàn `CRM Call Log` trong P2.
  - *Loại bỏ:* Không tận dụng được `CRM Call Log` native khi có cuộc gọi hợp lệ cần gắn kết.
- **Phương án C (Chọn):** Đường ghi chuẩn là `CSKH Contact Attempt`; `crm_cskh.services.call_log_adapter` cho phép **link 1–1 duy nhất** một `CRM Call Log` có sẵn hoặc **tạo `CRM Call Log` native** chỉ khi `channel == "phone_call"` và đủ dữ liệu thật hợp lệ (`from`, `to`, `type`, `status`).

## 3. Quyết định
Chọn **Phương án C**:
1. Cột `CSKH Contact Attempt.call_log` có ràng buộc `UNIQUE (call_log)` trên MariaDB: liên kết lần hai tới cùng một `CRM Call Log` bị từ chối với `409 ALREADY_LINKED`.
2. Kênh khác `phone_call` (`sms`, `zalo`, `facebook`, `other`) truyền `call_log` hoặc `call_log_data` bị từ chối `422 OUTCOME_NOT_COMPATIBLE`.
3. Khi không truyền `call_log` / `call_log_data`, hệ thống lưu `CSKH Contact Attempt` sạch với `call_log = NULL`. Khi truyền `call_log_data` thiếu dữ liệu bắt buộc hoặc sai SĐT, từ chối và rollback toàn bộ transaction.
4. Trạng thái `CRM Call Log.status == "Completed"` không bao giờ tự suy ra `is_connected = 1`.

## 4. Tác động Quyền, Kiểm thử & Chi phí Nâng cấp
- **Quyền:** `CRM Call Log` tiếp tục chịu `linked_lead_has_permission` và `guarded_crm.get_call_log` / `get_activities`.
- **Kiểm thử:** `T2-12`, `T2-13`, `SEC2-12`.
