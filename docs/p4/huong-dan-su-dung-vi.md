# Hướng Dẫn Sử Dụng Nhanh Tiếng Việt Theo Vai Trò (`P4-08` — Bản Triển Khai `crm.hrpartner.vn`)

- **Mã tài liệu:** `P4-USER-GUIDE-VI` (`docs/p4/huong-dan-su-dung-vi.md` — `P4.md §12.2`, `§16.4`)
- **Phiên bản giao diện đối chiếu:** Bản Việt hóa Không gian tuyển dụng (`467204176326f046442b5e20c381f9a895b6e176`) kết hợp 5 nhãn tùy chỉnh cấp site của Owner trên `https://crm.hrpartner.vn`
- **Tài liệu liên kết:** [`glossary-vi.md`](./glossary-vi.md) · [`uat-report.md`](./uat-report.md)

---

## 1. Sơ Đồ Luồng Công Việc & Giải Nghĩa Nhãn Quan Trọng

Hệ thống tách bạch rõ **luồng tiếp nhận & liên hệ đầu vào** tại **Không gian tuyển dụng (`/cskh`)** với **luồng quản trị CRM chuẩn (`/crm`)**:

1. **Quy trình 6 bước tại Không gian tuyển dụng (`/cskh`):**
   `1. Nhập danh sách liên hệ` (`/cskh/import`) → `2. Kho hồ sơ liên hệ` (`/cskh/pool`) → `3. Phân công & chiến dịch` (`/cskh/campaigns`) → `4. Hồ sơ được giao` (`/cskh/my-customers`) → `5. Ghi nhận liên hệ` (`/cskh/work-item`) → `6. Lịch hẹn liên hệ lại` (`/cskh/callbacks`).
2. **Các nhãn hiển thị chuẩn trên hệ thống (theo yêu cầu của Owner):**
   - **`Hồ sơ liên hệ`** (`CRM Lead`): Hồ sơ người lao động / ứng viên cần liên hệ.
   - **`Không gian tuyển dụng`** (`/cskh`): Không gian tác nghiệp chia số, ghi nhận liên hệ, hẹn gọi lại và theo dõi SLA/KPI.
   - **`Giao dịch CRM`** (`CRM Deal`): Nghiệp vụ giao dịch gốc của Frappe CRM; trên thanh điều hướng `/crm`, mục danh sách hiển thị theo nhãn tùy chỉnh của Owner là **`Doanh thu hàng tháng / năm`**, mục đơn vị/đối tác hiển thị là **`Các dự án`**, và hai thẻ tổng quan hiển thị **`NLĐ đang tiếp cận`** / **`NLD đã tiếp cận`**.
   - **`Người tuyển dụng`** (`Sales User`): Vai trò nhân sự trực tiếp tiếp nhận hồ sơ liên hệ và thực hiện cuộc gọi/tin nhắn.
   - **`Hoàn tất mục tiêu liên hệ chiến dịch`** (`converted`): Kết quả xác nhận đã hoàn thành mục tiêu bước liên hệ đầu vào của chiến dịch để đóng đầu việc liên hệ — **lưu ý: kết quả này chỉ đóng bước liên hệ chiến dịch, chưa tự xác nhận ứng viên đã trúng tuyển, đi làm hay bố trí thành công**.

---

## 2. Hướng Dẫn Dành Cho Người Tuyển Dụng (`Sales User`)

### Bước 1 — Nhận danh sách hồ sơ đầu ca
1. Đăng nhập tại `https://crm.hrpartner.vn`, mở **Không gian tuyển dụng (`/cskh`)** → chọn **Hồ sơ được giao (`/cskh/my-customers`)**.
2. Ưu tiên xử lý các hồ sơ có trạng thái **Mới được giao** trước mốc **Hạn liên hệ lần đầu (SLA)**.
3. Mở **Lịch hẹn liên hệ lại (`/cskh/callbacks`)** để kiểm tra các cuộc hẹn **Đến hạn hôm nay** hoặc **Quá hạn**.

### Bước 2 — Liên hệ & ghi nhận đúng kết quả thực tế
1. Bấm **Mở chi tiết hồ sơ** trên từng dòng để vào màn **Chi tiết hồ sơ liên hệ (`/cskh/work-item`)**.
2. Sau khi gọi điện hoặc nhắn tin, chọn đúng **Kênh liên hệ** (`Gọi điện thoại`, `Kênh Zalo`, `Tin nhắn SMS`, `Kênh Facebook`, `Kênh khác`) và **Kết quả liên hệ**:
   - Nếu không bắt máy / máy bận / thuê bao / mới gửi tin Zalo chưa có phản hồi: chọn đúng **`Không nghe máy`**, **`Bận máy`**, **`Không liên lạc được / Thuê bao`** hoặc **`Đã gửi tin nhắn, chưa phản hồi`**. Hệ thống đã ghi nhận bạn hoàn thành nghĩa vụ phản hồi lần đầu đúng hạn — **tuyệt đối không chọn nhầm sang nhóm Đã trao đổi**.
   - Nếu đã trao đổi hai chiều về mức lương, ca làm việc, khu vực làm việc: chọn **`Đã trao đổi — Có quan tâm`**, **`Đã trao đổi — Cần liên hệ tiếp`** hoặc **`Đã hẹn gọi / liên hệ lại`** và nhập tóm tắt rõ ràng vào ô **Ghi chú liên hệ**.
   - Nếu người nghe từ chối hoặc yêu cầu không làm phiền: chọn **`Đã trao đổi — Không nhu cầu`** hoặc **`Yêu cầu ngừng liên hệ (DNC)`** (hệ thống sẽ khóa liên hệ lại để tuân thủ DNC).
   - Khi đạt mục tiêu bước liên hệ của chiến dịch: chọn **`Hoàn tất mục tiêu liên hệ chiến dịch`**, nhập ghi chú và lý do hoàn tất để đóng đầu việc liên hệ.

### Bước 3 — Đặt / đổi lịch hẹn & xử lý khi ghi nhầm
1. Với kết quả cần gọi lại, chọn **Thời điểm hẹn lại** chính xác theo giờ Việt Nam. Khi cần dời lịch trên `/cskh/callbacks`, bấm **Đổi lịch hẹn** và nhập rõ **Lý do đặt / đổi lịch** (việc chỉ bấm đóng công việc mà không ghi nhận lần liên hệ thực tế sẽ không được tính là đã kết nối).
2. Lịch sử liên hệ là bất biến (`append-only`): nếu chọn nhầm kết quả hoặc kênh, không thể xóa tay — hãy bấm **Yêu cầu hiệu chỉnh / vô hiệu** ngay trên dòng lịch sử và ghi rõ lý do để Trưởng nhóm hoặc Quản trị CRM xét duyệt.
3. Cuối ca, mở **Bảng điểm cá nhân (`/cskh/scorecard`)** để tự kiểm tra tỷ lệ liên hệ đúng hạn, tỷ lệ chăm sóc đúng lịch hẹn và các đầu việc cần lưu ý.

---

## 3. Hướng Dẫn Dành Cho Trưởng Nhóm (`Sales Manager`)

1. **Điều hành SLA trong ca làm việc:**
   - Mở **Vận hành SLA (`/cskh/sla-operations`)** và **Tiến độ liên hệ (`/cskh/progress`)** đầu ca sáng và đầu ca chiều.
   - Rà các hồ sơ sắp chạm hạn hoặc đã quá hạn trong phạm vi đội mình quản lý để nhắc nhở hoặc đề xuất điều phối kịp thời.
2. **Xét duyệt hiệu chỉnh & miễn trừ hợp lệ:**
   - Khi thành viên gửi yêu cầu đính chính lần liên hệ ghi nhầm, kiểm tra đối chứng rồi bấm **Duyệt áp dụng** hoặc **Từ chối** kèm **Ghi chú phê duyệt** bắt buộc.
   - Chỉ thực hiện miễn trừ SLA (`waive`) khi có lý do khách quan đúng chính sách công ty; mọi thao tác đều lưu vết kiểm toán và tự động xếp hàng tính lại chỉ số liên quan.
3. **Đánh giá chất lượng đội & cá nhân:**
   - Mở **Chất lượng theo đội (`/cskh/team-quality`)** và **Chất lượng theo nhân viên (`/cskh/employee-quality`)**.
   - Bấm trực tiếp vào con số trên thẻ chỉ số để mở danh sách truy vết (`drill-down`) từng đầu việc Đạt / Không đạt. Bạn chỉ xem được dữ liệu thuộc nhánh đội mình phụ trách.

---

## 4. Hướng Dẫn Dành Cho Quản Trị CRM (`CSKH CRM Admin`)

1. **Nhập danh sách liên hệ (`/cskh/import`):**
   - Tải lên tệp `.csv` hoặc `.xlsx` (tối đa `10 MB`, giữ cột số điện thoại ở định dạng văn bản để không mất số `0` đầu).
   - Hệ thống lưu tệp ở chế độ riêng tư, tự động chuẩn hóa đầu số di động Việt Nam (`0...`, `84...`, `+84...`), gắn cờ tên tạm cho dòng chỉ có SĐT và đối chiếu chống trùng với `Hồ sơ liên hệ` hiện có.
   - Luôn rà kỹ bảng **Xem trước** (`Số dòng hợp lệ`, `Số dòng mới`, `Số dòng trùng`, `Số dòng lỗi`) trước khi bấm **Xác nhận nhập**.
2. **Tạo chiến dịch & Phân công trọng số (`/cskh/campaigns`):**
   - Chọn hoặc tạo chiến dịch **Đang hoạt động**, thiết lập danh sách **Người tuyển dụng** đủ điều kiện nhận việc, **Trọng số** và **Sức chứa** tối đa.
   - **Bắt buộc chạy `Xem trước phân phối`** để kiểm tra số lượng dự kiến giao cho từng người rồi mới bấm **`Xác nhận phân phối`**.
3. **Điều chuyển / Thu hồi hồ sơ & Tính lại KPI:**
   - Khi nhân sự nghỉ phép, nghỉ việc hoặc quá tải, thực hiện **Thu hồi** hoặc **Điều chuyển** ngay trên giao diện Không gian tuyển dụng để hệ thống đồng bộ cả người phụ trách `Hồ sơ liên hệ`, lịch hẹn đang mở và thu hồi quyền truy cập cũ (**không sửa tay trường chủ sở hữu thô**).
   - Sau khi duyệt hiệu chỉnh dữ liệu của kỳ đã chốt, dùng chức năng tính lại KPI có nhập lý do kiểm toán; bản chụp mới sẽ gắn nhãn **Đã tính lại kỳ cũ** và giữ nguyên lịch sử đợt chạy trước.

---

## 5. Hướng Dẫn Dành Cho Ban Giám Đốc (`CSKH BOD Viewer`)

1. **Theo dõi nguồn hồ sơ & phân bổ (`/cskh/dashboard`):**
   - Kiểm tra tổng số hồ sơ không trùng trong kho, số đã giao, chưa giao và tiến độ theo từng chiến dịch/đội.
2. **Đọc Báo cáo Chất lượng Toàn công ty (`/cskh/quality-overview`, `/cskh/campaign-quality`, `/cskh/team-quality`, `/cskh/employee-quality`):**
   - Các chỉ số giữ nguyên công thức chuẩn đã khóa (`cskh-metrics-v2`), luôn hiển thị kèm **`tử số / mẫu số`** để đối chiếu minh bạch.
   - **Lưu ý 4 quy ước đọc số liệu quản trị:**
     * **`Nhân viên tự báo cáo` vs `Đã xác minh`:** Cuộc gọi/tin nhắn do nhân viên tự nhập mặc định là *Nhân viên tự báo cáo*; chỉ những lần có nhật ký cuộc gọi hệ thống hoặc phiếu QA đã công bố mới tính là *Đã xác minh*.
     * **`Đã kết nối` vs `Hoàn tất mục tiêu liên hệ chiến dịch`:** *Đã kết nối* xác nhận đã tương tác hai chiều; *Hoàn tất mục tiêu liên hệ chiến dịch* xác nhận đã khép bước liên hệ đầu vào của chiến dịch, tách biệt với kết quả tuyển dụng/nhận việc thực tế hoặc **Giao dịch CRM**.
     * **`Tạm tính` vs `Đã chốt`:** Khi chưa bật chấm mẫu QA hoặc chưa đủ cỡ mẫu QA, điểm tuân thủ hiển thị nhãn **Tạm tính** (chuẩn hóa trên các trọng số đang hoạt động) và tách riêng nhóm xếp hạng.
     * **`N/A — Chưa đủ dữ liệu`:** Khi mẫu số bằng `0` (chưa phát sinh nghĩa vụ đến hạn trong kỳ), hệ thống hiển thị `N/A`, không hiển thị `0%` hay `100%` gây sai lệch đánh giá.
   - Màn BOD là chế độ **chỉ đọc toàn công ty có bảo vệ PII**: khi bấm truy vết chi tiết hoặc xuất CSV/XLSX, số điện thoại và nội dung nhạy cảm tự động được che giấu theo chính sách bảo mật.
