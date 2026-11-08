# P2 — Danh mục Kết quả Liên hệ Chuẩn (`CSKH Outcome Code`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** `docs/outcome-catalog.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§6.2), [`docs/state-machine.md`](./state-machine.md), [`docs/adr/ADR-P2-08.md`](./adr/ADR-P2-08.md), [`docs/adr/ADR-P2-09.md`](./adr/ADR-P2-09.md)

---

## 1. Nguyên tắc thiết kế

1. **Mã máy bất biến (`code`):** viết thường `snake_case`, duy nhất toàn hệ thống, **không được đổi hoặc xóa** sau khi đã phát sinh `CSKH Contact Attempt`.
2. **Tách rõ nỗ lực liên hệ (`is_contact_attempt`) và kết nối hai chiều (`is_connected`):**
   - `no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `wrong_number` đều có **`is_connected = 0`**.
   - Tuyệt đối không tính "đã bấm gọi" hoặc "đã gửi tin nhắn Zalo/SMS nhưng khách chưa phản hồi" là `connected` (`P2.md` §0.9, §6.2).
3. **Phiên bản hóa & Snapshot bất biến:**
   - Khi `CSKH CRM Admin` cập nhật nhãn hiển thị hoặc cờ cấu hình qua `crm_cskh.api.settings.save_outcome`, trường `version` tự động tăng (`version + 1`).
   - Mỗi `CSKH Contact Attempt` chụp cứng tại thời điểm ghi: `outcome_version_snapshot`, `outcome_label_snapshot`, `outcome_group_snapshot`, `connected_snapshot`, `interested_snapshot`, `terminal_snapshot`, `dnc_snapshot`. Lịch sử cũ không bao giờ bị trôi ngữ nghĩa khi Admin chỉnh cấu hình về sau.
4. **Ràng buộc theo kênh (`allowed_channels`):**
   - Ví dụ `no_answer`, `busy` chỉ hợp lệ cho `phone_call`; `message_sent_no_reply` chỉ hợp lệ cho kênh nhắn tin (`sms`, `zalo`, `facebook`, `other`). Gửi sai tổ hợp trả `422 OUTCOME_NOT_COMPATIBLE`.

---

## 2. Bộ từ điển 12 mã kết quả mặc định (Seed chuẩn khi `bench migrate`)

Các kênh chuẩn trong P2: `phone_call`, `sms`, `zalo`, `facebook`, `other`.

| TT | `code` | `label_vi` | `result_group` | `attempt` | `connected` | `interested` | `terminal` | `dnc` | `req_note` | `req_followup` | `allow_followup` | `req_close_reason` | `allowed_channels` | `suggested_next_status` |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | `no_answer` | Không nghe máy | `unreached` | 1 | **0** | 0 | 0 | 0 | 0 | 0 | 1 | 0 | `phone_call` | `in_progress` |
| 2 | `busy` | Máy bận | `unreached` | 1 | **0** | 0 | 0 | 0 | 0 | 0 | 1 | 0 | `phone_call` | `in_progress` |
| 3 | `unreachable` | Thuê bao không liên lạc được | `unreached` | 1 | **0** | 0 | 0 | 0 | 0 | 0 | 1 | 0 | `phone_call,sms,zalo,facebook,other` | `in_progress` |
| 4 | `message_sent_no_reply` | Đã gửi tin nhắn, chưa phản hồi | `unreached` | 1 | **0** | 0 | 0 | 0 | 0 | 0 | 1 | 0 | `sms,zalo,facebook,other` | `in_progress` |
| 5 | `wrong_number` | Sai số / Không đúng người | `invalid_contact` | 1 | **0** | 0 | 1 | 0 | 1 | 0 | 0 | 1 | `phone_call,sms,zalo,other` | `closed` |
| 6 | `connected_needs_followup` | Đã trao đổi — Cần liên hệ tiếp | `engaged` | 1 | **1** | 0 | 0 | 0 | 1 | 1 | 1 | 0 | `phone_call,sms,zalo,facebook,other` | `on_hold` |
| 7 | `connected_interested` | Đã trao đổi — Có quan tâm | `positive` | 1 | **1** | 1 | 0 | 0 | 1 | 0 | 1 | 0 | `phone_call,sms,zalo,facebook,other` | `in_progress` |
| 8 | `callback_requested` | Đã hẹn gọi / liên hệ lại | `engaged` | 1 | **1** | 0 | 0 | 0 | 0 | 1 | 1 | 0 | `phone_call,sms,zalo,facebook,other` | `on_hold` |
| 9 | `connected_not_interested` | Đã trao đổi — Không nhu cầu | `negative` | 1 | **1** | 0 | 1 | 0 | 0 | 0 | 0 | 0 | `phone_call,sms,zalo,facebook,other` | `closed` |
| 10 | `converted` | Hoàn tất mục tiêu chiến dịch | `converted` | 1 | **1** | 1 | 1 | 0 | 1 | 0 | 0 | 0 | `phone_call,sms,zalo,facebook,other` | `closed` |
| 11 | `do_not_contact` | Yêu cầu ngừng liên hệ (DNC) | `compliance` | 1 | **1** | 0 | 1 | **1** | 1 | 0 | 0 | 1 | `phone_call,sms,zalo,facebook,other` | `closed` |
| 12 | `other` | Khác (ghi rõ nội dung) | `other` | 1 | **0** | 0 | 0 | 0 | 1 | 0 | 1 | 0 | `phone_call,sms,zalo,facebook,other` | `in_progress` |

*(Ghi chú cột: `attempt` = `is_contact_attempt`, `connected` = `is_connected`, `interested` = `is_interested`, `terminal` = `is_terminal`, `dnc` = `is_do_not_contact`, `req_note` = `requires_note`, `req_followup` = `requires_followup`, `allow_followup` = `allows_followup`, `req_close_reason` = `requires_completion_reason`).*

> **Ranh giới ngữ nghĩa trong nghiệp vụ tuyển dụng & cung ứng nhân lực (`docs/recruitment-staffing-domain-alignment.md`):**
> - **Đã liên hệ / kết nối (`is_connected = 1`)** chỉ xác nhận đã trao đổi hai chiều với người liên hệ (khác hẳn `no_answer`, `busy`, `unreachable`, `message_sent_no_reply` có `is_connected = 0`).
> - **Có quan tâm (`connected_interested`, `is_interested = 1`)** chỉ xác nhận người liên hệ phản hồi tích cực về thông tin việc làm / nhân lực.
> - **Đã hẹn lại (`callback_requested` / `connected_needs_followup`, `suggested_next_status = on_hold`)** xác nhận cần liên hệ tiếp theo khung giờ đã hẹn.
> - **Hoàn tất mục tiêu chiến dịch (`converted`, `is_conversion = 1`)** chỉ xác nhận hoàn tất tiêu chí đầu ra của chiến dịch liên hệ (ví dụ đồng ý nộp hồ sơ hoặc chốt lịch phỏng vấn). **Không một mã kết quả liên hệ nào tự động xác nhận ứng viên đã được tuyển, bố trí hoặc nhận việc.**

---

## 3. Quy tắc kiểm tra hợp lệ phía Server khi ghi Attempt

Khi nhận yêu cầu `record_attempt` (hoặc `request_correction` đổi `outcome_code`):

1. **Tồn tại & đang bật:** `CSKH Outcome Code` phải tồn tại và `enabled == 1`; nếu không tồn tại hoặc bị tắt → trả `422 INVALID_OUTCOME`.
2. **Tương thích kênh (`channel`):**
   - `channel` phải nằm trong `policy.enabled_channels` của `CSKH Policy` hiệu lực; nếu không → trả `400 INVALID_CHANNEL`.
   - `channel` phải nằm trong danh sách `outcome.allowed_channels`; nếu không → trả `422 OUTCOME_NOT_COMPATIBLE`.
3. **Bắt buộc ghi chú (`requires_note`):**
   - Nếu `outcome.requires_note == 1` và `note` sau khi cắt khoảng trắng bị rỗng → trả `422 NOTE_REQUIRED`.
4. **Ràng buộc lịch hẹn lại (`requires_followup` / `allows_followup`):**
   - Nếu `outcome.requires_followup == 1` và request không truyền `followup.due_at` hợp lệ → trả `422 FOLLOWUP_REQUIRED`.
   - Nếu `outcome.allows_followup == 0` mà request lại truyền `followup.due_at` → trả `422 OUTCOME_NOT_COMPATIBLE`.
5. **Hành vi `do_not_contact` (`is_do_not_contact == 1`):**
   - Gắn cờ bền vững trên `CRM Lead`: `cskh_do_not_contact = 1`, `cskh_dnc_reason = note`, `cskh_dnc_at = occurred_at`, `cskh_dnc_work_item = work_item`.
   - Nếu `policy.do_not_contact_behavior == "block_and_auto_close"` (mặc định): tự động đóng `CSKH Work Item` (`status = "closed"`, hủy mọi callback mở trong cùng transaction) và chặn tạo lịch hẹn lại hoặc ghi liên hệ mới trên Lead này (`409 DO_NOT_CONTACT`) cho đến khi `CSKH CRM Admin` gỡ cờ có kiểm toán.
   - Nếu `policy.do_not_contact_behavior == "block_future_outreach"`: giữ cờ `cskh_do_not_contact = 1`, hủy mọi callback mở, và chặn các nỗ lực liên hệ/phân phối tiếp theo (`409 DO_NOT_CONTACT`).
6. **Bảo vệ bất biến ngữ nghĩa cốt lõi khi Admin chỉnh sửa (`save_outcome`):**
   - Không cho phép xóa `CSKH Outcome Code` đã được tham chiếu bởi bất kỳ `CSKH Contact Attempt` nào (chỉ cho phép `enabled = 0`).
   - Không cho phép đặt `is_connected = 1` cho các mã `unreached` chuẩn (`no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `wrong_number`), đảm bảo quy tắc số 8 của dự án không thể bị bật nhầm qua cấu hình.
