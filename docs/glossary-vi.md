# P2 — Từ điển Thuật ngữ Tiếng Việt Chuẩn Giao diện (`vi_VN` Glossary)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và nghiệp vụ chăm sóc khách hàng
> **Chủ sở hữu:** T1A (UI, bản dịch & đóng gói WP11)
> **Tài liệu liên quan:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§12.5, §18.2), [`docs/architecture/glossary-vi.md`](./architecture/glossary-vi.md) (khảo sát nền P0), [`apps/crm_cskh/crm_cskh/p2_strings.py`](../apps/crm_cskh/crm_cskh/p2_strings.py), [`apps/crm_cskh/crm_cskh/p2_translations.py`](../apps/crm_cskh/crm_cskh/p2_translations.py), [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../apps/crm_cskh/crm_cskh/translations/vi.csv)

---

## 1. Nguyên tắc Việt hóa & Múi giờ ở P2

1. **Không sửa trực tiếp `crm/locale/vi.po` của upstream Frappe CRM:** Toàn bộ chuỗi giao diện, trạng thái, mã kết quả, thông báo kiểm tra hợp lệ (validation), thông báo nổi (toast), trạng thái rỗng (empty state) và giải thích lỗi P2 đi qua cơ chế dịch cấp ứng dụng của Frappe (`_()`) và được xuất ra danh mục `cskh_translations` trên trình duyệt (`cskh.t()`).
2. **Kiểm toán tự động fail-closed (`deploy/p1/check_translations_audit.py`):** Mọi chuỗi `_("...")` trong HTML Jinja và `cskh.t('...')` trong JavaScript tại `/cskh/*` đều được quét tự động để bảo đảm `missing = 0`, `untranslated = 0`, `orphan = 0` trong `translations/vi.csv`.
3. **Chuẩn múi giờ (`Asia/Ho_Chi_Minh`, UTC+07):**
   - `cskh_context.timezone_context` xuất `site_timezone` (`System Settings.time_zone`, chuẩn `Asia/Ho_Chi_Minh`), `user_timezone` (`User.time_zone` nếu cấu hình hợp lệ) và `server_now_iso` xuống trình duyệt.
   - `cskh.js` phân loại hạn xử lý (`Đến hạn hôm nay` / `Sắp đến hạn` / `Quá hạn`) và chuyển đổi `<input type="datetime-local">` theo đúng múi giờ hiệu lực của người dùng/hệ thống, giữ chính xác ranh giới `23:59` so với `00:00` mà **không phụ thuộc giờ máy trạm cá nhân**.

---

## 2. Bảng Thuật ngữ Nghiệp vụ Cốt lõi (`P2.md` §12.5)

| Thuật ngữ gốc (English `msgid`) | Nhãn Tiếng Việt Chuẩn UI (`vi_VN`) | Ngữ cảnh sử dụng |
|---|---|---|
| `Contact attempt (CSKH)` / `Quick record contact result` | **Lần liên hệ CSKH** / **Ghi nhận kết quả liên hệ nhanh** | Dòng thời gian CSKH, biểu mẫu ghi nhanh |
| `Contact channel` | **Kênh liên hệ** | Biểu mẫu ghi nhận liên hệ & cấu hình chính sách |
| `Contact outcome` / `Last outcome` | **Kết quả liên hệ** / **Kết quả gần nhất** | Biểu mẫu ghi nhận, bảng Khách được giao |
| `Contact count` / `Effective contact attempts` | **Số lần liên hệ** / **Tổng lần liên hệ hiệu lực** | Bảng Khách được giao, Tiến độ chăm sóc |
| `Care status` / `Care filter` | **Trạng thái CSKH** / **Bộ lọc CSKH** | Bảng Khách được giao, Hồ sơ chăm sóc |
| `Newly assigned` (`assigned`) | **Mới được giao** | Work Item đã giao, chưa phát sinh chăm sóc |
| `Not contacted` | **Chưa liên hệ** | Chưa có bất kỳ `Attempt` hiệu lực nào |
| `In progress` (`in_progress`) | **Đang chăm sóc** | Đã có liên hệ, đang theo dõi |
| `On hold` (`on_hold`) | **Chờ chăm sóc lại** | Đang có lịch hẹn liên hệ lại đang mở |
| `Completed care` (`closed`) | **Hoàn tất** | Đã đóng công việc chăm sóc có lý do |
| `Attempted` | **Đã thử** | Đã có ít nhất 1 lần liên hệ |
| `Attempted, not connected` | **Đã thử nhưng chưa kết nối** | Có `Attempt` nhưng chưa lần nào `is_connected = true` |
| `Connected` / `Not connected` | **Đã kết nối** / **Chưa kết nối** | Huy hiệu trung thực trên kết quả & dòng thời gian |
| `Counts as connected` / `Does not count as connected` | **Tính là đã kết nối** / **Không tính là đã kết nối** | Giải thích trực quan ngay khi chọn mã kết quả |
| `Callbacks schedule` / `Next callback` | **Lịch hẹn chăm sóc** / **Hẹn tiếp theo** | Thanh điều hướng, bảng Khách được giao |
| `Due` / `Due today` / `Upcoming` / `Overdue` | **Đến hạn** / **Đến hạn hôm nay** / **Sắp đến hạn** / **Quá hạn** | Phân loại hạn `CRM Task` theo múi giờ chuẩn |
| `Self-reported` | **Nhân viên tự báo cáo** | Huy hiệu bắt buộc cho lần liên hệ do nhân viên nhập tay |
| `Verified` | **Đã xác minh** | Chỉ cấp bởi luồng xác minh máy chủ hợp lệ (không tự gắn từ client) |
| `Do not contact` | **Không liên hệ nữa** | Cờ tuân thủ từ chối liên hệ (`DNC`) |
| `Close work item` / `Reopen work item` | **Hoàn tất chăm sóc** / **Mở lại công việc** | Thao tác vòng đời trên Hồ sơ chăm sóc |

---

## 3. Kênh Liên hệ & Danh mục Mã Kết quả (`CSKH Outcome Code`)

### 3.1. Kênh liên hệ (`channel`)

| Mã kênh | `msgid` | Nhãn Tiếng Việt Chuẩn |
|---|---|---|
| `phone_call` | `Phone call` | **Gọi điện thoại** |
| `zalo` | `Zalo channel` | **Kênh Zalo** |
| `sms` | `SMS message` | **Tin nhắn SMS** |
| `facebook` | `Facebook channel` | **Kênh Facebook** |
| `other` | `Other channel` | **Kênh khác** |

### 3.2. Nhãn kết quả liên hệ mặc định

| Mã kết quả (`code`) | Tính `Đã kết nối`? | Nhãn Tiếng Việt Chuẩn |
|---|---:|---|
| `no_answer` | Không | **Không nghe máy** |
| `busy` | Không | **Máy bận** |
| `unreachable` | Không | **Không liên lạc được** |
| `message_sent_no_reply` | **Không** *(G2-06)* | **Đã gửi tin, chưa phản hồi** |
| `wrong_number` | Không | **Sai số điện thoại** |
| `interested` / `connected_interested` | Có | **Quan tâm** / **Quan tâm (qua cuộc gọi)** |
| `not_interested` / `connected_not_interested` | Có | **Chưa có nhu cầu** / **Chưa có nhu cầu (qua cuộc gọi)** |
| `callback_requested` | Có | **Khách hẹn liên hệ lại** |
| `converted` | Có | **Chốt thành công / Hoàn tất** |
| `do_not_contact` | Có / Tùy kênh | **Từ chối / Không liên hệ nữa** |
| `message_replied_interested` | Có | **Đã phản hồi — Quan tâm** |
| `message_replied_not_interested` | Có | **Đã phản hồi — Chưa có nhu cầu** |
| `message_replied_callback` | Có | **Đã phản hồi — Hẹn liên hệ lại** |

---

## 4. Phân biệt Nguồn Sự kiện trên Dòng thời gian & Hiệu chỉnh Bất biến

| Thực thể / Trạng thái | Nhãn Tiếng Việt trên UI | Quy tắc trung thực dữ liệu |
|---|---|---|
| `CSKH Contact Attempt` | **Lần liên hệ CSKH** | Nguồn sự thật duy nhất của bộ đếm số lần liên hệ; không có nút Sửa/Xóa trực tiếp |
| `CRM Call Log` (chưa gắn) | **Nhật ký cuộc gọi (CRM)** · *Chưa gắn lần liên hệ (không tự tính số lần liên hệ)* | Không tự cộng hai lần vào `attempt_count` |
| `CRM Call Log` (đã gắn) | **Nhật ký cuộc gọi (CRM)** · *Đã gắn lần liên hệ: #...* | Liên kết 1–1 duy nhất với một `Attempt` |
| `CRM Task` | **Lịch hẹn / Nhiệm vụ (CRM)** · *Nhiệm vụ — không tự tính là lần liên hệ hay đã kết nối* | Đóng/hủy Task khi chưa gọi không bao giờ hiển thị là đã kết nối |
| `FCRM Note` | **Ghi chú nội bộ (CRM)** · *Ghi chú nội bộ — không tính vào số lần liên hệ* | Ghi chú văn bản thuần, vô hiệu hóa HTML/script/công thức |
| `Pending correction review` | **Đang chờ duyệt hiệu chỉnh** | Yêu cầu của nhân viên chờ Trưởng nhóm / Quản trị CRM duyệt |
| `Corrected` + `Original snapshot` | **Đã hiệu chỉnh** + **Bản ghi gốc** | Hiển thị song song bản chụp gốc bất biến và giá trị hiệu lực mới |
| `Voided — excluded from counts` | **Đã vô hiệu — không tính vào bộ đếm** | Giữ nguyên dấu vết kiểm toán trên timeline nhưng loại khỏi bộ đếm |

---

## 5. Danh mục Thông báo Lỗi Nghiệp vụ P2 (`vi_VN`)

| Mã lỗi ổn định | Giải thích Tiếng Việt hiển thị cho người vận hành |
|---|---|
| `OWNERSHIP_CHANGED` | Khách hàng này đã được chuyển giao cho nhân viên khác. Vui lòng làm mới danh sách. |
| `WORK_ITEM_NOT_ACTIVE` | Công việc chăm sóc không còn ở trạng thái hoạt động (chưa giao, đã thu hồi hoặc đã hoàn tất). |
| `STALE_WORK_ITEM` | Công việc vừa được cập nhật ở tab hoặc phiên làm việc khác. Hãy tải lại dữ liệu mới nhất trước khi lưu. |
| `POLICY_CHANGED` | Chính sách hoặc cấu hình mã kết quả vừa thay đổi. Hãy tải lại biểu mẫu rồi thử lại. |
| `IDEMPOTENCY_CONFLICT` | Mã yêu cầu (`request_id`) này đã được dùng với nội dung dữ liệu khác. |
| `ALREADY_LINKED` | Nhật ký cuộc gọi hoặc lịch hẹn này đã được gắn với một lần liên hệ khác. |
| `DO_NOT_CONTACT` | Khách hàng đang ở trạng thái Không liên hệ nữa (DNC); chính sách chặn tạo liên hệ/hẹn mới. |
| `INVALID_CHANNEL` | Kênh liên hệ đã chọn không hợp lệ hoặc bị giới hạn bởi chính sách. |
| `INVALID_OUTCOME` | Mã kết quả liên hệ không tồn tại hoặc đã ngừng kích hoạt. |
| `NOTE_REQUIRED` | Kết quả liên hệ này bắt buộc phải nhập ghi chú trước khi lưu. |
| `FOLLOWUP_REQUIRED` | Kết quả liên hệ này bắt buộc phải chọn thời điểm hẹn chăm sóc lại. |
| `INVALID_FOLLOWUP_TIME` | Thời điểm liên hệ hoặc thời điểm hẹn lại nằm ngoài khoảng hợp lệ của chính sách. |
| `OUTCOME_NOT_COMPATIBLE` | Kết quả đã chọn không tương thích với kênh liên hệ hoặc chính sách đóng công việc. |
