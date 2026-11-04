# Từ điển Thuật ngữ Tiếng Việt Chuẩn CSKH (P0–P2)

> **Tài liệu:** `docs/glossary-vi.md`
> **Chủ sở hữu:** T1A (UI, bản dịch `vi_VN` & đóng gói WP11) phối hợp cùng T1B (danh mục `CSKH Outcome Code` & thực thể Backend)
> **Nguồn tham chiếu:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§12.5, §18.2), [`docs/outcome-catalog.md`](./outcome-catalog.md), [`apps/crm_cskh/crm_cskh/p2_strings.py`](../apps/crm_cskh/crm_cskh/p2_strings.py), [`apps/crm_cskh/crm_cskh/p2_translations.py`](../apps/crm_cskh/crm_cskh/p2_translations.py), [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../apps/crm_cskh/crm_cskh/translations/vi.csv)

Tài liệu này khóa chuẩn thuật ngữ tiếng Việt dùng thống nhất giữa **Backend (`crm_cskh`)**, **UI CSKH (`/cskh/*`)**, **thông báo lỗi/validation** và **báo cáo vận hành** từ P0 đến P2.

---

## 1. Nguyên tắc Việt hóa & Múi giờ ở P2

1. **Không sửa trực tiếp `crm/locale/vi.po` của upstream Frappe CRM:** Toàn bộ chuỗi giao diện, trạng thái, mã kết quả, thông báo kiểm tra hợp lệ (validation), thông báo nổi (toast), trạng thái rỗng (empty state) và giải thích lỗi P2 đi qua cơ chế dịch cấp ứng dụng của Frappe (`_()`) và được xuất ra danh mục `cskh_translations` trên trình duyệt (`cskh.t()`).
2. **Kiểm toán tự động fail-closed (`deploy/p1/check_translations_audit.py`):** Mọi chuỗi `_("...")` trong HTML Jinja và `cskh.t('...')` trong JavaScript tại `/cskh/*` đều được quét tự động để bảo đảm `missing = 0`, `untranslated = 0`, `orphan = 0` trong `translations/vi.csv`.
3. **Chuẩn múi giờ (`Asia/Ho_Chi_Minh`, UTC+07):**
   - `cskh_context.timezone_context` xuất `site_timezone` (`System Settings.time_zone`, chuẩn `Asia/Ho_Chi_Minh`), `user_timezone` (`User.time_zone` nếu cấu hình hợp lệ) và `server_now_iso` xuống trình duyệt.
   - `cskh.js` phân loại hạn xử lý (`Đến hạn hôm nay` / `Sắp đến hạn` / `Quá hạn`) và chuyển đổi `<input type="datetime-local">` theo đúng múi giờ hiệu lực của người dùng/hệ thống, gửi chuỗi thời gian chuẩn của site (`YYYY-MM-DD HH:mm:ss` theo `Asia/Ho_Chi_Minh`), giữ chính xác ranh giới `23:59` so với `00:00` mà **không phụ thuộc giờ máy trạm cá nhân**.

---

## 2. Đối tượng & Vai trò Nghiệp vụ (P0–P2)

| Thuật ngữ Kỹ thuật / Gốc | Nhãn Tiếng Việt Chuẩn | Giải thích & Ranh giới |
|---|---|---|
| `CRM Lead` | Khách hàng | Hồ sơ khách hàng chuẩn (canonical) trong Frappe CRM; một số điện thoại chuẩn hóa Việt Nam chỉ có một `CRM Lead` chính trong `CSKH Identity Index`. |
| `CSKH Campaign` | Chiến dịch CSKH | Đơn vị chiến dịch chăm sóc/tiếp cận khách hàng theo phạm vi toàn hệ thống hoặc theo Đội/Nhóm. |
| `CSKH Work Item` | Công việc CSKH / Đầu việc chăm sóc | Đơn vị giao việc chăm sóc của một khách hàng trong một chiến dịch cụ thể (`UNIQUE(campaign, lead)`). Không đánh đồng với `CRM Lead.status`. |
| `CSKH Active Claim` | Khóa phụ trách đang hiệu lực | Khóa đảm bảo tại một thời điểm mỗi khách hàng chỉ thuộc một đầu việc chăm sóc đang hoạt động. |
| `CSKH Assignment History` | Lịch sử phân công | Nhật ký bất biến ghi lại các khoảng thời gian phụ trách (`ownership_start` → `ownership_end`) khi giao, điều chuyển, thu hồi, đóng hoặc mở lại. |
| `CSKH Contact Attempt` | Lần liên hệ CSKH / Ghi nhận liên hệ | Sự kiện bất biến ghi lại một lần nhân viên thực hiện liên hệ với khách hàng qua một kênh xác định. |
| `CSKH Attempt Correction` | Yêu cầu hiệu chỉnh nhật ký | Bản ghi kiểm toán yêu cầu sửa nội dung (`correct`), hủy hiệu lực (`void`) hoặc khôi phục (`restore`) một lần liên hệ mà không sửa/xóa bản ghi gốc. |
| `CSKH Outcome Code` | Mã kết quả liên hệ | Danh mục chuẩn hóa kết quả liên hệ kèm cờ ngữ nghĩa (`is_contact_attempt`, `is_connected`, `requires_note`, `requires_followup`, ...). |
| `CSKH Policy` | Chính sách vận hành CSKH | Cấu hình quy tắc chăm sóc kế thừa theo thứ tự `Chiến dịch > Đội/Nhóm > Mặc định toàn hệ thống`. |
| `CRM Call Log` | Nhật ký cuộc gọi (CRM) | Bản ghi cuộc gọi gốc của Frappe CRM, liên kết 1–1 tùy chọn với `CSKH Contact Attempt` ở kênh gọi thoại. |
| `CRM Task` (`cskh_is_followup = 1`) | Lịch hẹn chăm sóc lại | Tác vụ hẹn gọi/nhắn lại gốc của Frappe CRM được gắn với `CSKH Work Item` (tối đa 1 lịch hẹn mở trên mỗi đầu việc). |
| `FCRM Note` | Ghi chú nội bộ (CRM) | Ghi chú tự do trên hồ sơ khách hàng; **tuyệt đối không cộng vào số lần liên hệ (`attempt_count`)**. |
| `CSKH CRM Admin` | Quản trị viên CSKH | Vai trò quản trị nghiệp vụ CSKH (cấu hình danh mục/chính sách, phân phối, điều chuyển/thu hồi, duyệt hiệu chỉnh toàn hệ thống — không cấp `System Manager`). |
| `CSKH BOD Viewer` | Ban Giám đốc (BOD) | Vai trò xem số liệu tiến độ tổng hợp toàn hệ thống với **0 PII** (không xem số điện thoại, tên khách, ghi chú hay chi tiết từng khách). |
| `Sales Manager` (Team Leader) | Trưởng nhóm CSKH | Quản lý phạm vi cây đội/nhóm của mình (`CRM Sales Hierarchy`), duyệt hiệu chỉnh và mở lại đầu việc trong nhóm. |
| `Sales User` (Staff) | Nhân viên CSKH | Nhân viên trực tiếp phụ trách và ghi nhận kết quả chăm sóc các đầu việc đang được giao cho mình. |

---

## 3. Trạng thái Đầu việc (`CSKH Work Item.status`) & Bộ lọc Hàng đợi (`P2.md` §12.1 & §12.5)

| Giá trị Lưu DB / API (`msgid` UI) | Nhãn Tiếng Việt Chuẩn | Ý nghĩa Nghiệp vụ |
|---|---|---|
| `queued` | Chờ phân công | Đầu việc nằm trong kho chờ, chưa có nhân viên phụ trách. |
| `assigned` (`Newly assigned`) | Mới được giao | Đã giao cho nhân viên phụ trách nhưng chưa có lần liên hệ hiệu lực nào và chưa đặt lịch hẹn. |
| `in_progress` (`In progress`) | Đang chăm sóc | Đã có ít nhất một lần liên hệ (hoặc vừa mở lại), không có lịch hẹn mở đang chờ. |
| `on_hold` (`On hold`) | Chờ chăm sóc lại | Đang có đúng 1 lịch hẹn chăm sóc lại (`CRM Task`) ở trạng thái mở (`Todo` / `In Progress` / `Backlog`). |
| `closed` (`Completed care` / `Close work item`) | Hoàn tất / Hoàn tất chăm sóc | Đầu việc đã kết thúc chăm sóc trong chiến dịch, đã giải phóng `CSKH Active Claim`. |
| `revoked` | Đã thu hồi | Đầu việc bị thu hồi khỏi nhân viên phụ trách mà không đẩy lại vào kho chờ của chiến dịch. |
| `uncontacted` *(queue filter / `Not contacted`)* | Chưa liên hệ | Đầu việc đang mở nhưng `attempt_count = 0`. |
| `attempted` *(UI filter / `Attempted`)* | Đã thử | Đầu việc đã có ít nhất một lần liên hệ (`attempt_count > 0`). |
| `unreached` *(queue filter / `Attempted, not connected`)* | Đã thử nhưng chưa kết nối | Đã có `attempt_count > 0` nhưng `connected_attempt_count = 0`. |
| `connected` *(queue filter / `Connected`)* | Đã kết nối | Đã có ít nhất một lần tương tác hai chiều thực sự (`connected_attempt_count > 0`). |
| `needs_followup` *(queue filter)* | Cần chăm sóc lại | Đầu việc đang ở trạng thái `on_hold` có lịch hẹn mở. |
| `due_today` *(queue filter / `Due today`)* | Đến hạn hôm nay | Có lịch hẹn mở với hạn xử lý trong ngày hiện tại theo múi giờ chuẩn. |
| `overdue` *(queue filter / `Overdue`)* | Quá hạn | Có lịch hẹn mở với `due_date < hiện tại`. |
| `do_not_contact` *(queue filter / `Do not contact`)* | Không liên hệ nữa | Khách hàng có cờ từ chối liên hệ (`cskh_do_not_contact = 1`). |

---

## 4. Kênh Liên hệ, Trạng thái Xác minh, Lịch hẹn & Dòng thời gian

| Giá trị Kỹ thuật / Thực thể | Nhãn Tiếng Việt Chuẩn trên UI | Quy tắc Hiển thị & Kiểm chứng Trung thực |
|---|---|---|
| `phone_call` (`Phone call`) | Gọi điện thoại | Kênh duy nhất được phép gắn hoặc tạo `CRM Call Log`. |
| `sms` (`SMS message`) | Tin nhắn SMS | Kênh văn bản; gửi tin chưa phản hồi không bao giờ tính là `Đã kết nối`. |
| `zalo` (`Zalo channel`) | Zalo / Kênh Zalo | Kênh nhắn tin/tương tác qua Zalo (nhân viên ghi nhận thủ công ở P2). |
| `facebook` (`Facebook channel`) | Facebook / Kênh Facebook | Kênh nhắn tin/tương tác qua Facebook (nhân viên ghi nhận thủ công ở P2). |
| `other` (`Other channel`) | Kênh khác | Bắt buộc nhập ghi chú mô tả rõ kênh/ngữ cảnh liên hệ. |
| `self_reported` (`Self-reported`) | Nhân viên tự báo cáo | Mặc định cho mọi lần liên hệ do nhân viên nhập tay hoặc `CRM Call Log` thủ công (`Manual`); **không được hiển thị như đã xác minh tự động**. |
| `verified` (`Verified`) | Đã xác minh | Chỉ gán khi hệ thống tổng đài tích hợp xác nhận hoặc Quản trị viên xác minh qua luồng kiểm toán. |
| `disputed` / `rejected` | Đang tranh chấp / Từ chối xác minh | Nhật ký cần đối chiếu thêm hoặc bị xác định không hợp lệ qua kiểm toán. |
| `Due` / `today` | Đến hạn / Hôm nay | Hạn xử lý nằm trong ngày hiện tại theo múi giờ hiệu lực. |
| `Upcoming` / `upcoming` | Sắp đến hạn | Hạn xử lý từ `00:00:00` ngày mai trở đi theo múi giờ hiệu lực. |
| `Overdue` / `overdue` | Quá hạn | Hạn xử lý nhỏ hơn thời điểm hiện tại và tác vụ vẫn đang mở. |
| `Done` | Đã hoàn tất | Tác vụ hẹn lại chỉ được chuyển sang `Đã hoàn tất` khi gắn với một `CSKH Contact Attempt` thực tế (`cskh_completed_attempt`). |
| `Canceled` | Đã hủy | Hủy lịch hẹn bắt buộc nhập lý do (`cskh_completion_reason`); không tăng `attempt_count` và không hiển thị là `Đã kết nối`. |
| `CRM Call Log` chưa gắn | Nhật ký cuộc gọi (CRM) · *Chưa gắn lần liên hệ* | Không tự cộng hai lần vào `attempt_count`. |
| `Pending correction review` | Đang chờ duyệt hiệu chỉnh | Yêu cầu của nhân viên chờ Trưởng nhóm / Quản trị CRM duyệt; bản ghi gốc không bị sửa trực tiếp. |
| `Voided — excluded from counts` | Đã vô hiệu — không tính vào bộ đếm | Giữ nguyên dấu vết kiểm toán trên timeline nhưng loại khỏi bộ đếm. |

---

## 5. Danh mục 12 Mã Kết quả Chuẩn (`CSKH Outcome Code`)

| Mã (`code`) | Nhãn Tiếng Việt (`label_vi`) | Nhóm Kết quả | Đếm Liên hệ | Đếm Đã kết nối |
|---|---|---|:---:|:---:|
| `no_answer` | Không nghe máy | Chưa gặp được khách (`unreached`) | Có | **Không** |
| `busy` | Máy bận | Chưa gặp được khách (`unreached`) | Có | **Không** |
| `unreachable` | Không liên lạc được / thuê bao | Chưa gặp được khách (`unreached`) | Có | **Không** |
| `wrong_number` | Sai số | Sai thông tin liên hệ (`invalid_contact`) | Có | **Không** |
| `message_sent_no_reply` | Đã gửi tin, chưa phản hồi | Chưa gặp được khách (`unreached`) | Có | **Không** |
| `callback_requested` | Khách hẹn gọi lại | Đã trao đổi (`engaged`) | Có | Có |
| `connected_interested` | Đã kết nối — quan tâm | Tín hiệu tích cực (`positive`) | Có | Có |
| `connected_needs_followup` | Đã kết nối — cần chăm sóc thêm | Đã trao đổi (`engaged`) | Có | Có |
| `connected_not_interested` | Đã kết nối — không nhu cầu | Từ chối / không nhu cầu (`negative`) | Có | Có |
| `converted` | Chốt thành công / chuyển đổi | Chuyển đổi thành công (`converted`) | Có | Có |
| `do_not_contact` | Từ chối liên hệ (DNC) | Tuân thủ / chặn liên hệ (`compliance`) | Có | Có |
| `other` | Kết quả khác | Khác (`other`) | Có | **Không** *(mặc định)* |

---

## 6. Danh mục Thông báo Lỗi Nghiệp vụ P2 (`vi_VN`)

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

---

## 7. Ranh giới Ngôn ngữ Báo cáo P2 vs P3 (`ADR-P2-11`)

- Các chỉ số ở màn hình **Tiến độ CSKH (`workflow.progress_summary`)** trong P2 chỉ dùng thuật ngữ **Số lượng đầu việc**, **Số lần liên hệ hiệu lực**, **Số lần đã kết nối**, **Số lịch hẹn đang mở / đến hạn hôm nay / quá hạn**.
- **Nghiêm cấm** gọi các bộ đếm tiến độ P2 là *“Điểm chất lượng CSKH”*, *“Tỷ lệ đạt SLA”* hay *“Xếp hạng KPI nhân viên”* — các khái niệm chấm điểm SLA/KPI thuộc phạm vi **P3**.
