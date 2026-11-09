# Từ Điển Thuật Ngữ Tiếng Việt Chuẩn Hóa & Quy Chuẩn Hiển Thị Giao Diện (`P4-08` — Cổng `G20`)

- **Mã tài liệu:** `P4-GLOSSARY-VI` (`docs/p4/glossary-vi.md` — `P4.md §12.1`, `§20.2`)
- **Đầu vào khóa cứng:** Bản Việt hóa Không gian tuyển dụng `467204176326f046442b5e20c381f9a895b6e176` (`sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` trên `https://crm.hrpartner.vn`)
  - Từ điển ứng dụng [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv): **`1.331` dòng**
  - Bản vá biên dịch giao diện đóng gói sẵn trong Image: [`apply-frappe-vi-patch.py`](../../deploy/dev/apply-frappe-vi-patch.py) & [`apply-crm-ui-vi-patch.py`](../../deploy/dev/apply-crm-ui-vi-patch.py)
  - **`5` bản ghi ghi đè cấp Site (`Translation` DocType) theo yêu cầu của Owner** đang áp dụng trực tiếp trên `crm-staging.localhost` (bảo toàn qua backup, restore và `migrate ×2`)

---

## 1. Nguyên Tắc Bất Biến Về Việt Hóa & Bảo Toàn Nhãn Owner (`P4.md §12.1`)

1. **Tái sử dụng cơ chế chuẩn của Frappe / Frappe CRM:** Sử dụng `frappe._()` ở Python/Jinja, `__()` ở Vue SPA, từ điển ứng dụng `apps/crm_cskh/crm_cskh/translations/vi.csv` (ưu tiên cao hơn `crm/locale/vi.po` nhờ thứ tự `sites/apps.txt`: `frappe` → `crm` → `crm_cskh`) và lớp ghi đè cao nhất của Frappe là bảng `tabTranslation` cấp site.
2. **Bảo toàn tuyệt đối các nhãn Owner đã yêu cầu:** Giữ nguyên tên luồng **`Hồ sơ liên hệ → Phân công → Ghi nhận liên hệ → Lịch hẹn liên hệ lại`**, giữ **`Giao dịch CRM`** cho nghiệp vụ `CRM Deal` gốc, giữ đúng nghĩa của **`Hoàn tất mục tiêu liên hệ chiến dịch`**, và bảo toàn cả 5 nhãn tùy chỉnh cấp site của Owner (xem Mục 2.2).
3. **Không đổi công thức KPI hoặc mở thêm nghiệp vụ:** Mọi tên chỉ số trên thẻ tổng quan, bảng chi tiết và bản xuất CSV/XLSX đồng bộ 100% tiếng Việt nhưng **giữ nguyên công thức tính toán đã khóa (`cskh-metrics-v2`)**.

---

## 2. Bảng Thuật Ngữ Nghiệp Vụ Cốt Lõi & Nhãn Tùy Chỉnh Của Owner

### 2.1. Thực thể, Không gian làm việc & Vai trò chuẩn (`vi.csv` — `4672041`)

| Thuật ngữ gốc / Mã kỹ thuật | Thuật ngữ Tiếng Việt chuẩn hóa trên UI | Ngữ cảnh áp dụng & Quy tắc hiển thị |
| :--- | :--- | :--- |
| `CSKH` / `Recruitment workspace` (`/cskh`) | **Không gian tuyển dụng** | Không gian tác nghiệp nhập danh sách, chia hồ sơ, ghi nhận liên hệ, hẹn gọi lại và báo cáo SLA/KPI. |
| `CRM Lead` / `Lead` / `Contact record` | **Hồ sơ liên hệ** | Hồ sơ người lao động / ứng viên cần liên hệ; màn `/cskh/my-customers` hiển thị **Hồ sơ được giao**; `/cskh/pool` hiển thị **Kho hồ sơ liên hệ**; `/cskh/import` hiển thị **Nhập danh sách liên hệ**. |
| `CRM Deal` / `Deal` | **Giao dịch CRM** | Chức năng giao dịch gốc của Frappe CRM (`/crm/deals`), tách biệt rõ với bước liên hệ đầu vào tại Không gian tuyển dụng. |
| `Lead Owner` / `Owner` / `Assignee` | **Người phụ trách** / **Người được giao** | Nhân sự đang nắm quyền sở hữu hiện hành trên hồ sơ liên hệ / đầu việc liên hệ. |
| `Placeholder Name` (`cskh_is_placeholder_name`) | **Tên tạm (chỉ có SĐT)** | Gắn nhãn rõ cho hồ sơ nhập vào chỉ có số điện thoại, không biến SĐT thành tên thật. |
| `CSKH Import Batch` | **Lô nhập danh sách** | Lô tải lên tệp CSV/XLSX riêng tư cùng thống kê dòng hợp lệ / dòng mới / dòng trùng / dòng lỗi. |
| `CSKH Campaign` | **Chiến dịch** | Chiến dịch phân công và liên hệ gắn với quy tắc phân bổ và chính sách SLA riêng. |
| `CSKH Assignment Rule` | **Quy tắc phân công** | Cấu hình trọng số phân bổ và sức chứa tối đa nhận hồ sơ của từng nhân sự. |
| `CSKH Work Item` | **Đầu việc liên hệ** | Nhiệm vụ tiếp nhận và liên hệ một hồ sơ trong một chiến dịch cụ thể. |
| `CSKH Assignment History` | **Lịch sử phân phối & bàn giao** | Nhật ký bất biến (`append-only`) ghi lại mọi lần giao mới, điều chuyển, thu hồi. |
| `CSKH BOD Viewer` | **Ban Giám đốc (BOD)** | Vai trò chỉ đọc báo cáo toàn công ty đã che PII, không mang quyền `System Manager`. |
| `CSKH CRM Admin` | **Quản trị CRM** | Vai trò quản lý nhập danh sách, chiến dịch, phân công, điều chuyển/thu hồi và yêu cầu tính lại KPI. |
| `Sales Manager` (theo cây đội) | **Trưởng nhóm** | Quản lý nhân sự và đầu việc liên hệ thuộc phạm vi nhánh `CRM Sales Hierarchy` được giao. |

### 2.2. Năm nhãn tùy chỉnh cấp Site của Owner (`tabTranslation` trên `crm-staging.localhost`)

| Nguồn gốc (`source_text`) | Nhãn Tiếng Việt của Owner (`translated_text`) | Vị trí hiển thị & Quy tắc bảo toàn |
| :--- | :--- | :--- |
| `Sales User` | **Người tuyển dụng** | Nhãn vai trò nhân sự trực tiếp tiếp nhận hồ sơ và thực hiện liên hệ. |
| `Organizations` | **Các dự án** | Nhãn điều hướng mục đơn vị/tổ chức trên thanh menu CRM (`/crm/organizations`). |
| `Deals` | **Doanh thu hàng tháng / năm** | Nhãn điều hướng mục danh sách trên thanh menu CRM (`/crm/deals`), trong khi tên thực thể/chức năng đơn lẻ `CRM Deal` giữ là **Giao dịch CRM**. |
| `Ongoing deals` | **NLĐ đang tiếp cận** | Thẻ chỉ số trên trang Tổng quan CRM (`/crm`). |
| `Won deals` | **NLD đã tiếp cận** | Thẻ chỉ số trên trang Tổng quan CRM (`/crm`). |

---

## 3. Bảng Trạng Thái Đầu Việc & Danh Mục Kết Quả Liên Hệ Chuẩn (`P2` / `P4`)

### 3.1. Vòng đời `CSKH Work Item` (`status`)

| Mã trạng thái | Nhãn Tiếng Việt chuẩn | Ý nghĩa vận hành |
| :--- | :--- | :--- |
| `queued` | **Trong hàng đợi** | Đã đưa vào chiến dịch nhưng chưa giao cho nhân sự cụ thể. |
| `assigned` | **Mới được giao** | Đã giao người phụ trách, đồng hồ hạn phản hồi lần đầu (`FIRST_ATTEMPT`) đang chạy. |
| `in_progress` | **Đang xử lý liên hệ** | Đã có ít nhất một lần thử liên hệ, đang tiếp tục theo dõi. |
| `on_hold` | **Chờ liên hệ lại** | Đang có lịch hẹn liên hệ lại (`CRM Task` callback) chờ đến hạn. |
| `closed` | **Đã đóng / Hoàn tất xử lý liên hệ** | Đã kết thúc hợp lệ theo chính sách kết quả của chiến dịch. |
| `revoked` | **Đã thu hồi** | Quản trị CRM thu hồi quyền phụ trách khỏi người được giao cũ. |

### 3.2. Danh mục 12 Mã Kết Quả Liên Hệ (`CSKH Outcome Code`)

| Mã (`code`) | Nhãn Tiếng Việt chuẩn (`label_vi`) | Nhóm kết quả | Tính là Đã kết nối (`is_connected`) | Quy tắc & Ý nghĩa bắt buộc |
| :--- | :--- | :--- | :---: | :--- |
| `no_answer` | **Không nghe máy** | Chưa tiếp cận | Không (`0`) | Ghi nhận đã thực hiện cuộc gọi; giữ đầu việc đang xử lý. |
| `busy` | **Bận máy** / **Máy bận** | Chưa tiếp cận | Không (`0`) | Ghi nhận đã thực hiện cuộc gọi; giữ đầu việc đang xử lý. |
| `unreachable` | **Không liên lạc được / Thuê bao** | Chưa tiếp cận | Không (`0`) | Ghi nhận đã thử liên hệ; giữ đầu việc đang xử lý. |
| `message_sent_no_reply` | **Đã gửi tin nhắn, chưa phản hồi** | Chưa tiếp cận | Không (`0`) | **Không bao giờ** tính là đã kết nối khi chưa có phản hồi hai chiều. |
| `wrong_number` | **Số sai / Không tồn tại** | Sai thông tin | Không (`0`) | Bắt buộc ghi chú & lý do khi đóng đầu việc liên hệ. |
| `connected_needs_followup` | **Đã trao đổi — Cần liên hệ tiếp** | Đã tương tác | **Có (`1`)** | Bắt buộc nhập ghi chú (ví dụ trao đổi mức lương, ca làm việc) & đặt lịch hẹn lại. |
| `connected_interested` | **Đã trao đổi — Có quan tâm** | Tích cực | **Có (`1`)** | Đã trao đổi hai chiều có quan tâm (bắt buộc ghi chú); chưa đồng nghĩa đã tuyển/bố trí. |
| `callback_requested` | **Đã hẹn gọi / liên hệ lại** | Đã tương tác | **Có (`1`)** | Bắt buộc chọn thời điểm hẹn liên hệ lại cụ thể. |
| `connected_not_interested` | **Đã trao đổi — Không nhu cầu** | Từ chối | **Có (`1`)** | Đóng đầu việc liên hệ theo kết quả từ chối. |
| `converted` | **Hoàn tất mục tiêu liên hệ chiến dịch** | Hoàn tất liên hệ | **Có (`1`)** | **Chỉ đóng bước liên hệ đầu vào của chiến dịch** (bắt buộc ghi chú & lý do); **không tự xác nhận ứng viên đã trúng tuyển hay nhận việc**. |
| `do_not_contact` | **Yêu cầu ngừng liên hệ (DNC)** | Tuân thủ DNC | **Có (`1`)** | Đóng đầu việc & đặt khóa `Không liên hệ nữa` toàn cục (bắt buộc lý do). |
| `other` | **Khác (ghi rõ nội dung)** | Khác | Không (`0`) | Bắt buộc nhập ghi chú chi tiết. |

---

## 4. Thuật Ngữ SLA, Kiểm Định QA & Báo Cáo Chất Lượng KPI (`P3` / `P4`)

| Thuật ngữ / Trạng thái kỹ thuật | Nhãn Tiếng Việt chuẩn hóa | Quy tắc diễn giải bắt buộc cho BOD / Trưởng nhóm / Người tuyển dụng |
| :--- | :--- | :--- |
| `Contact Attempt` | **Lần ghi nhận liên hệ** | Một sự kiện liên hệ thực tế qua Gọi điện thoại, Kênh Zalo, Tin nhắn SMS, Kênh Facebook hoặc Kênh khác. |
| `Follow-up` / `Callback` | **Lịch hẹn liên hệ lại** | Công việc `CRM Task` (`cskh_is_followup = 1`) có thời hạn cụ thể gắn với đầu việc liên hệ. |
| `Distinct two-way connected records` | **Hồ sơ đã kết nối hai chiều duy nhất** | Đếm số hồ sơ không trùng đã có tương tác hai chiều (nhất quán giữa thẻ tổng quan, bảng chi tiết và CSV xuất). |
| `First-touch latency (p50 / p90)` | **Trung vị / Phân vị P90 thời gian phản hồi lần đầu (Phút làm việc - P50 / P90)** | Tính theo phút làm việc thực tế trên `CSKH Working Calendar` (`Asia/Ho_Chi_Minh`). |
| `self_reported` | **Nhân viên tự báo cáo** | Trạng thái mặc định khi nhân sự tự nhập kết quả mà chưa có nhật ký tổng đài hoặc phiếu QA xác thực. |
| `verified` | **Đã xác minh** | Chỉ hiển thị khi gắn `CRM Call Log` hợp lệ hoặc phiếu `CSKH QA Review` có `status = 'published'`. |
| `provisional` (`process_score_status`) | **Tạm tính** *(ví dụ: `Tạm tính; thiếu mẫu QA`)* | Điểm tuân thủ quy trình chuẩn hóa trên các trọng số đang hoạt động khi tắt QA hoặc chưa đủ mẫu QA; tách riêng nhóm xếp hạng. |
| `final` (`process_score_status`) | **Đã chốt** | Đủ cỡ mẫu trên toàn bộ thành phần trọng số theo chính sách hiệu lực. |
| `insufficient_data` / `denominator = 0` | **N/A — Chưa đủ dữ liệu** | Hiển thị `N/A`, tuyệt đối không hiển thị `0%` hay `100%` giả. |
| `is_restated = 1` | **Đã tính lại kỳ cũ** | Bản chụp lịch sử được tính lại có kiểm toán sau khi duyệt hiệu chỉnh; giữ nguyên bản chụp cũ để đối chiếu. |

---

## 5. Quy Chuẩn Định Dạng Thời Gian, Con Số & Khả Năng Truy Cập (`P4.md §12.2`, `§19.2`)

1. **Múi giờ & Kỳ báo cáo:** Toàn bộ giao diện hiển thị theo `Asia/Ho_Chi_Minh` (`UTC+7`), định dạng `DD/MM/YYYY HH:mm`; bộ lọc kỳ hiển thị rõ khoảng nửa mở `[Từ ngày, Đến ngày)`.
2. **Định dạng số & tỷ lệ:** Mọi chỉ số phần trăm trên báo cáo chất lượng và bảng điểm cá nhân hiển thị kèm **`tử số / mẫu số`**.
3. **Độ phân giải & Điều hướng bàn phím (`G20`):** Không vỡ bố cục hoặc mất nút hành động chính trên màn `1366×768` và `1920×1080`; hỗ trợ `Tab`, `Shift+Tab`, `Enter`, `Escape` và nhãn văn bản đi kèm màu trạng thái.
