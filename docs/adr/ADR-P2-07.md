# ADR-P2-07 — Cờ Tuân thủ `Do Not Contact (DNC)` Toàn cục & Tách biệt `CRM Lead.status`

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §5.3, §6.2, §19 (`ADR-P2-07`)

## 1. Bối cảnh & Vấn đề
Một khách hàng (`CRM Lead` canonical) có thể tham gia nhiều chiến dịch theo thời gian hoặc xuất hiện lại trong các tệp CSV/XLSX import mới. Nếu chỉ lưu trạng thái từ chối liên hệ (`do_not_contact`) ở cấp `CSKH Work Item` hoặc gộp lẫn vào `CRM Lead.status` (`Unqualified` / `Junk`), hệ thống gặp hai rủi ro lớn:
1. Lần phân phối chiến dịch tiếp theo không biết khách đã yêu cầu ngừng liên hệ nên tiếp tục giao cho nhân viên gọi làm phiền khách.
2. Ghi đè `CRM Lead.status` làm hỏng phân loại phễu bán hàng chung của Frappe CRM.

## 2. Quyết định
1. **Thêm 4 Custom Fields tường minh trên `CRM Lead`:** `cskh_do_not_contact` (`Check`, default `0`), `cskh_dnc_reason` (`Small Text`), `cskh_dnc_at` (`Datetime`), `cskh_dnc_work_item` (`Link -> CSKH Work Item`).
2. **Không tự động đồng bộ hai chiều sang `CRM Lead.status`:** Mọi chuyển đổi trạng thái chiến dịch (`assigned`, `in_progress`, `on_hold`, `closed`, `revoked`) sống trên `CSKH Work Item.status`.
3. **Bảo vệ cờ DNC xuyên suốt toàn bộ luồng P1 + P2:**
   - **Khi ghi nhận `do_not_contact`:** gắn `cskh_do_not_contact = 1` trên `CRM Lead`, hủy mọi callback mở và tự đóng `Work Item` theo `policy.do_not_contact_behavior`.
   - **Khi Re-import (`jobs/import_worker.py`):** reuse Lead canonical hiện có, tuyệt đối không xóa hay ghi đè `cskh_do_not_contact`.
   - **Khi Phân phối (`services/eligibility.py`):** `_lead_exclusion_reason` loại Lead có `cskh_do_not_contact == 1` với mã `"do_not_contact"`.
   - **Khi Tác nghiệp / Mở lại:** chặn `record_attempt`, `schedule_followup`, `reopen_work_item` với mã lỗi `409 DO_NOT_CONTACT`, trừ khi `CSKH CRM Admin` gỡ cờ có lý do audit qua `settings.set_lead_dnc` (hoặc truyền `override_dnc=True` có lý do).

## 3. Tác động Kiểm thử
- Được ghim bởi `T2-15` (bao gồm kiểm chứng sau re-import và chiến dịch mới).
