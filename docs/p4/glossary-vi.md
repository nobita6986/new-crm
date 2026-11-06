# Từ Điển Thuật Ngữ Tiếng Việt Chuẩn Hóa & Quy Chuẩn Hiển Thị Giao Diện (`P4-08` — Cổng `G20`)

- **Mã tài liệu:** `P4-GLOSSARY-VI` (`docs/p4/glossary-vi.md` — `P4.md §12.1`, `§20.2`)
- **Đầu vào khóa cứng:** Baseline ứng dụng `92e14af` / hồ sơ tích hợp `492423c`
  - Từ điển ứng dụng [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv): **`1.266` dòng** (`check_translations_audit.py`: `missing = 0`, `untranslated = 0`, `orphan = 0`)
  - Bản vá biên dịch giao diện đã đóng gói sẵn trong Production Image (`4de3dc8`): [`apply-frappe-vi-patch.py`](../../deploy/dev/apply-frappe-vi-patch.py) & [`apply-crm-ui-vi-patch.py`](../../deploy/dev/apply-crm-ui-vi-patch.py)
  - Kế thừa và chốt từ [`docs/root-plan/master-plan.md`](../root-plan/master-plan.md) và [`docs/architecture/glossary-vi.md`](../architecture/glossary-vi.md)

---

## 1. Nguyên Tắc Bất Biến Về Việt Hóa (`P4.md §12.1`)

1. **Tái sử dụng cơ chế chuẩn của Frappe / Frappe CRM:** Sử dụng `frappe._()` ở Python/Jinja và `__()` ở Vue SPA kết hợp `apps/crm_cskh/crm_cskh/translations/vi.csv` (ưu tiên cao hơn `crm/locale/vi.po` nhờ thứ tự `sites/apps.txt`: `frappe` → `crm` → `crm_cskh` đã khóa bởi `F-P4-01`). Tuyệt đối không xây framework đa ngôn ngữ riêng.
2. **Triệt tiêu lỗi dịch sai ngữ cảnh từ upstream (`vi.po`):** Đã ghi đè hoàn toàn các lỗi dịch tự động gây hiểu nhầm trong `crm@v1.87.0` (`Lead` không bao giờ hiển thị là *"Chì"*; `Note` không hiển thị là *"Lưu ý"*; `Assigned To` không hiển thị là *"Được giao Cho"*).
3. **Minh bạch ý nghĩa số liệu quản trị:** Tuyệt đối không dùng thuật ngữ gây nhầm lẫn giữa *tự báo cáo* với *đã xác minh*, hoặc giữa *tạm tính* với *đã chốt*.

---

## 2. Bảng Thuật Ngữ Nghiệp Vụ Cốt Lõi (Thực Thể, Vai Trò & Phân Phối)

| Thuật ngữ gốc / Mã kỹ thuật | Thuật ngữ Tiếng Việt chuẩn hóa trên UI | Ngữ cảnh áp dụng & Quy tắc hiển thị |
| :--- | :--- | :--- |
| `Lead` / `CRM Lead` | **Khách hàng tiềm năng** *(màn nhân viên `/cskh/my-customers`: **Khách được giao**)* | Hồ sơ khách gốc trong kho dữ liệu; tuyệt đối không dịch là *"Chì"*. |
| `Deal` / `CRM Deal` | **Cơ hội** | Hồ sơ cơ hội chuyển đổi trong Frappe CRM. |
| `Lead Owner` / `Assigned To` | **Nhân viên phụ trách** / **Người phụ trách** | Nhân sự đang nắm quyền sở hữu hiện hành trên khách hàng / đầu việc. |
| `Placeholder Name` (`cskh_is_placeholder_name`) | **Tên tạm (chỉ có SĐT)** | Gắn nhãn rõ cho khách import chỉ có số điện thoại, không biến SĐT thành tên thật. |
| `CSKH Import Batch` | **Đợt nhập dữ liệu** | Lô tải lên tệp CSV cùng thống kê hợp lệ / trùng / lỗi định dạng. |
| `CSKH Campaign` | **Chiến dịch CSKH** | Đợt khai thác/chăm sóc khách hàng gắn với chính sách SLA riêng. |
| `CSKH Assignment Rule` | **Quy tắc phân phối (trọng số / trần nhận)** | Cấu hình tỷ lệ chia số (ví dụ `50/30/20`) và hạn mức công suất nhận việc. |
| `CSKH Work Item` | **Đầu việc CSKH** | Nhiệm vụ chăm sóc một khách hàng trong một chiến dịch cụ thể. |
| `CSKH Assignment History` | **Lịch sử phân phối & bàn giao** | Nhật ký bất biến (`append-only`) ghi lại mọi lần giao mới, chuyển giao, thu hồi. |
| `CSKH BOD Viewer` | **Ban Giám đốc (BOD)** | Vai trò chỉ đọc báo cáo toàn công ty đã che PII, không mang quyền `System Manager`. |
| `CSKH CRM Admin` | **Quản trị viên nghiệp vụ CSKH** | Vai trò quản lý nhập kho, chiến dịch, phân phối, điều chuyển và yêu cầu tính lại KPI. |
| `Sales Manager` (theo cây đội) | **Trưởng nhóm CSKH** | Quản lý nhân sự và đầu việc thuộc phạm vi nhánh `CRM Sales Hierarchy` được giao. |
| `Sales User` | **Nhân viên CSKH / Tuyển dụng** | Nhân sự trực tiếp liên hệ và chăm sóc danh sách khách được giao. |

---

## 3. Bảng Trạng Thái Đầu Việc & 12 Mã Kết Quả Liên Hệ Chuẩn (`P2` / `P4`)

### 3.1. Vòng đời `CSKH Work Item` (`status`)

| Mã trạng thái | Nhãn Tiếng Việt chuẩn | Ý nghĩa vận hành |
| :--- | :--- | :--- |
| `queued` | **Chờ phân phối** | Đã vào chiến dịch nhưng chưa giao cho nhân viên cụ thể. |
| `assigned` | **Mới giao (Chưa liên hệ)** | Đã giao người phụ trách, đồng hồ hạn xử lý liên hệ đầu (`FIRST_ATTEMPT`) đang chạy. |
| `in_progress` | **Đang chăm sóc** | Đã có ít nhất một lần thử liên hệ, đang tiếp tục theo dõi. |
| `on_hold` | **Chờ chăm sóc lại (Có lịch hẹn)** | Đang có công việc hẹn lại (`CRM Task` callback) chờ đến hạn. |
| `closed` | **Đã hoàn tất / Đóng đầu việc** | Đã kết thúc hợp lệ theo chính sách kết quả chiến dịch. |
| `revoked` | **Đã thu hồi** | Quản trị viên thu hồi quyền phụ trách khỏi nhân viên cũ. |

### 3.2. Danh mục 12 Mã Kết Quả Liên Hệ (`CSKH Outcome Code`)

| Mã (`code`) | Nhãn Tiếng Việt chuẩn (`label_vi`) | Nhóm kết quả | Tính là Đã kết nối (`is_connected`) | Trạng thái gợi ý tiếp theo |
| :--- | :--- | :--- | :---: | :--- |
| `no_answer` | **Không nghe máy** | Chưa tiếp cận | Không (`0`) | Đang chăm sóc |
| `busy` | **Máy bận** | Chưa tiếp cận | Không (`0`) | Đang chăm sóc |
| `unreachable` | **Thuê bao không liên lạc được** | Chưa tiếp cận | Không (`0`) | Đang chăm sóc |
| `message_sent_no_reply` | **Đã gửi tin nhắn, chưa phản hồi** | Chưa tiếp cận | Không (`0`) | Đang chăm sóc |
| `wrong_number` | **Sai số / Không đúng người** | Sai thông tin | Không (`0`) | Đóng đầu việc *(bắt buộc ghi chú & lý do)* |
| `connected_needs_followup` | **Đã trao đổi — Cần chăm sóc tiếp** | Đã tương tác | **Có (`1`)** | Chờ chăm sóc lại *(bắt buộc lịch hẹn & ghi chú)* |
| `connected_interested` | **Đã trao đổi — Quan tâm** | Tích cực | **Có (`1`)** | Đang chăm sóc *(bắt buộc ghi chú)* |
| `callback_requested` | **Khách hẹn gọi / liên hệ lại** | Đã tương tác | **Có (`1`)** | Chờ chăm sóc lại *(bắt buộc đặt lịch hẹn)* |
| `connected_not_interested` | **Đã trao đổi — Không nhu cầu** | Từ chối | **Có (`1`)** | Đóng đầu việc |
| `converted` | **Hoàn tất mục tiêu chiến dịch** | Chuyển đổi | **Có (`1`)** | Đóng đầu việc *(bắt buộc ghi chú)* |
| `do_not_contact` | **Yêu cầu ngừng liên hệ (DNC)** | Tuân thủ DNC | **Có (`1`)** | Đóng & khóa DNC toàn cục *(bắt buộc lý do)* |
| `other` | **Khác (ghi rõ nội dung)** | Khác | Không (`0`) | Đang chăm sóc *(bắt buộc ghi chú)* |

---

## 4. Thuật Ngữ SLA, Kiểm Định QA & Báo Cáo Chất Lượng KPI (`P3` / `P4`)

| Thuật ngữ / Trạng thái kỹ thuật | Nhãn Tiếng Việt chuẩn hóa | Quy tắc diễn giải bắt buộc cho BOD / Leader / Staff |
| :--- | :--- | :--- |
| `Contact Attempt` | **Lần liên hệ** | Một sự kiện nhân viên chủ động liên hệ qua Điện thoại, SMS, Zalo, Facebook hoặc kênh khác. |
| `Follow-up` / `Callback` | **Chăm sóc lại / Lịch hẹn liên hệ lại** | Công việc `CRM Task` có thời hạn cụ thể gắn với `CSKH Work Item`. |
| `SLA` (`CSKH SLA Obligation`) | **Hạn xử lý (SLA)** | Nghĩa vụ thời hạn theo lịch làm việc (`Liên hệ lần đầu` hoặc `Chăm sóc đúng lịch hẹn`). |
| `self_reported` | **Nhân viên tự báo cáo** | Trạng thái mặc định khi nhân viên tự nhập kết quả mà chưa có bản ghi tổng đài/QA xác thực. |
| `verified` | **Đã xác minh** | Chỉ hiển thị khi có liên kết `CRM Call Log` hợp lệ hoặc phiếu kiểm định `CSKH QA Review` đã duyệt. |
| `disputed` | **Đang tranh chấp / Chờ rà soát** | Kết quả liên hệ đang có yêu cầu hiệu chỉnh hoặc phản hồi QA chưa chốt. |
| `provisional` (`process_score_status`) | **Tạm tính** *(ví dụ: `Tạm tính; thiếu mẫu QA`)* | Điểm tuân thủ quy trình được chuẩn hóa trên các trọng số hiện có khi tắt QA hoặc chưa đủ cỡ mẫu QA; **không xếp chung bảng xếp hạng với điểm `Đã chốt`**. |
| `final` (`process_score_status`) | **Đã chốt** | Đủ cỡ mẫu trên toàn bộ thành phần trọng số theo chính sách hiệu lực. |
| `insufficient_data` / `denominator = 0` | **N/A — Chưa đủ dữ liệu** | Hiển thị rõ `N/A (0 / 0 — Chưa có mẫu đủ điều kiện)`, **tuyệt đối không hiển thị `0%` hay `100%`**. |
| `is_restated = 1` | **Đã tính lại kỳ cũ (Có điều chỉnh)** | Kỳ báo cáo lịch sử được tính lại có kiểm toán sau khi duyệt hiệu chỉnh dữ liệu; giữ nguyên bản chụp cũ để đối chiếu. |
| `historical_attribution_unknown` | **Chưa xác định quy gán lịch sử** | Cảnh báo cho dữ liệu trước thời điểm cắt chuyển chưa có lịch sử thành viên đội đầy đủ; không đoán theo đội hiện tại. |
| `Ownership Interval` vs `Distinct Work Item` | **Khoảng phụ trách nhân viên** vs **Đầu việc duy nhất** | Khi có bàn giao `A → B`, tổng số khoảng phụ trách của các nhân viên lớn hơn số đầu việc duy nhất toàn công ty. |

---

## 5. Quy Chuẩn Định Dạng Thời Gian, Con Số, Accessibility & Giới Hạn Chấp Nhận (`P4.md §12.2`, `§19.2`)

1. **Múi giờ & Kỳ báo cáo:** Toàn bộ giao diện hiển thị theo `Asia/Ho_Chi_Minh` (`UTC+7`), định dạng ngày `DD/MM/YYYY HH:mm`; mọi bộ lọc kỳ hiển thị rõ khoảng nửa mở `[Từ ngày, Đến ngày)` theo giờ Việt Nam.
2. **Định dạng số & tỷ lệ:** Mọi chỉ số phần trăm trên Dashboard và Bảng điểm cá nhân đều hiển thị kèm **`tử số / mẫu số`** (ví dụ `90,00% (9 / 10)`), dùng dấu phân cách thập phân/hàng nghìn nhất quán tiếng Việt.
3. **Tiêu chuẩn Accessibility cơ bản (`G20`):**
   - Độ phân giải laptop chuẩn `1366×768` và `1920×1080`: không tràn ngang mất nút hành động chính (`Ghi nhận liên hệ`, `Đặt lịch hẹn`, `Xuất báo cáo`, `Xem công thức`) khi chuỗi tiếng Việt dài hơn tiếng Anh.
   - Điều hướng bàn phím (`Tab`, `Shift+Tab`, `Enter`, `Escape` đóng hộp thoại): vòng focus hiển thị rõ ràng; các nút biểu tượng (`Toggle navigation`, `Sidebar`) đã gắn nhãn dịch tiếng Việt qua `apply-frappe-vi-patch.py`.
   - Độ tương phản màu chữ trạng thái (`Đạt`, `Quá hạn`, `Tạm tính`, `N/A`) đạt tối thiểu chuẩn đọc rõ kèm nhãn văn bản (không chỉ phân biệt bằng màu sắc).
4. **Giới hạn đã biết trên trang cấu hình hệ thống nội bộ (`P4.md §12.2`, `§19.2`):** Một số màn hình cấu hình sâu của Frappe Desk chỉ dành cho `System Manager` kỹ thuật có thể còn thuật ngữ tiếng Anh gốc của framework; toàn bộ **10 nhóm màn hình vận hành hằng ngày của Nhân viên, Trưởng nhóm, Admin nghiệp vụ và BOD (`/crm` & `/cskh/*`) bắt buộc đạt 100% tiếng Việt chuẩn nghiệp vụ**.
