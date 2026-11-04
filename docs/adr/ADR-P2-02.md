# ADR-P2-02 — Tách bạch `CSKH Contact Attempt` (Sự kiện Nghiệp vụ) với `CRM Call Log` & `FCRM Note`

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §6.3, §9, §19 (`ADR-P2-02`)

## 1. Bối cảnh & Vấn đề
Kiểm chứng mã nguồn `frappe/crm@v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) cho thấy:
- `CRM Call Log` (`apps/crm/crm/fcrm/doctype/crm_call_log/crm_call_log.json`) chỉ biểu diễn cuộc gọi thoại với `from`/`to` (`reqd=1`, kiểu `Phone`) và `type` (`Incoming|Outgoing`), không hỗ trợ `sms`/`zalo`/`facebook`/`other`, không có từ điển kết quả phiên bản hóa, và cho phép sửa/xóa theo `DocPerm` mặc định.
- `FCRM Note` (`apps/crm/crm/fcrm/doctype/fcrm_note/fcrm_note.json`) chỉ là ghi chú HTML tự do gắn Lead, không có khái niệm kênh, kết quả, khoảng sở hữu hay bất biến kiểm toán.

## 2. Các Phương án Đã Xét
- **Phương án A:** Nhồi thêm Custom Fields vào `CRM Call Log` và ép tạo `CRM Call Log` giả cho cả Zalo/SMS.
  - *Nhược điểm:* Làm bẩn bảng thoại native của Frappe CRM, phải bịa số `from`/`to` giả khi thiếu dữ liệu thoại, vi phạm nguyên tắc không ngụy tạo dữ liệu (`P2.md` §9.4).
- **Phương án B:** Dùng `FCRM Note` để lưu nhật ký liên hệ.
  - *Nhược điểm:* Dữ liệu có thể bị sửa/xóa tự do, không thể làm nguồn tính KPI đáng tin ở P3.
- **Phương án C (Chọn):** Xây `CSKH Contact Attempt` làm sổ cái sự kiện liên hệ đa kênh bất biến (append-only) gắn `CSKH Work Item` + `CSKH Assignment History`, liên kết 1–1 tùy chọn sang `CRM Call Log` khi kênh là `phone_call`, và giữ `FCRM Note` cho ghi chú tham khảo tự do.

## 3. Quyết định
Chọn **Phương án C**:
- `CSKH Contact Attempt` là nguồn sự thật duy nhất cho số lần liên hệ, kết quả, kênh, người thực hiện (`performed_by`), khoảng sở hữu (`ownership_event`) và trạng thái xác minh (`verification_status = "self_reported"`).
- `CRM Call Log` và `FCRM Note` được giữ nguyên schema gốc (zero monkey-patch) để tương thích hoàn toàn khi nâng cấp Frappe CRM và tích hợp tổng đài ở giai đoạn sau.

## 4. Tác động Quyền, Kiểm thử & Chi phí Nâng cấp
- **Quyền:** `CSKH Contact Attempt` khóa chặt `create/write/delete` trên generic API; chỉ đọc được bởi người đang có quyền hợp lệ trên `CSKH Work Item`.
- **Kiểm thử:** `T2-02..T2-05`, `T2-12`, `T2-13`, `SEC2-01..SEC2-06`.
