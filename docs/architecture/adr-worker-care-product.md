# ADR — Sản phẩm chăm sóc người lao động cho bộ phận marketing

Ngày: 26/11/2026. Quyết định sản phẩm theo chỉ đạo của chủ dự án: lấy người lao động và công việc chăm sóc làm trung tâm trên toàn bộ luồng làm việc chính.

## Vấn đề

Tổng quan Frappe CRM gốc đo giao dịch, giá trị giao dịch và doanh thu. Các trang `/cskh` đã có nghiệp vụ phân công, lịch hẹn, SLA và ghi nhận liên hệ nhưng trước đây chỉ là một mục bổ sung bên cạnh CRM bán hàng. Điều này làm người dùng marketing đi vào một luồng không phản ánh công việc của họ.

## Quyết định

1. `/cskh` là tổng quan vận hành chăm sóc người lao động. Trang phải cho biết khối lượng hồ sơ, phần chưa liên hệ/chưa kết nối, lịch hẹn đến hạn/quá hạn và hành động tiếp theo theo vai trò. Mỗi chỉ số có phạm vi, đơn vị và thời điểm tính rõ ràng.
2. Các điểm vào `/crm`, dashboard, deals, forecast và revenue dẫn người dùng có quyền chăm sóc về `/cskh`. Khách chưa đăng nhập, request API, lỗi xác thực và các thao tác ghi không bị chuyển hướng như trang HTML.
3. Điều hướng, app switcher và điểm vào sau đăng nhập ưu tiên chăm sóc người lao động. Loại bỏ lối tắt và hành động tạo/chuyển sang giao dịch bán hàng khỏi giao diện marketing. Hồ sơ liên hệ, lịch sử cuộc gọi và công việc gốc vẫn có thể được dùng làm công cụ hỗ trợ.
4. Ngôn ngữ chính: **người lao động**, **nhân viên marketing**, **trưởng nhóm marketing**, **chiến dịch chăm sóc**, **lịch hẹn chăm sóc**, **kết quả liên hệ**. Từ điển giao diện tập trung tại `worker_care_strings.py` và `worker_overview_strings.py`, qua pipeline dịch hiện có.
5. Giữ nguyên tên DocType/role, API, mã kết quả, snapshot và dữ liệu lịch sử. Không đổi `CRM Deal` thành bản ghi đi làm. Ẩn/đổi điều hướng là lựa chọn sản phẩm; các API tiếp tục có kiểm quyền độc lập.
6. Người có vai trò BOD chỉ nhận tổng hợp được phép; tổng quan mới không xuất tên, số điện thoại hay danh sách hồ sơ cá nhân. Staff và Leader dùng scope hiện có, không nhận tổng công ty ngoài quyền.
7. Không dùng số liệu tiền, Deal hoặc dự báo doanh thu làm số liệu chăm sóc. Không dựng các số 0 cho nhận việc/duy trì khi chưa có dữ liệu nghiệp vụ.

## Đơn vị và ngữ nghĩa

- **Hồ sơ người lao động không trùng**: đếm theo định danh Lead trong phạm vi được phép; chưa khẳng định kho số đã có hồ sơ ứng viên hoàn chỉnh.
- **Đầu việc chăm sóc**: Work Item theo chiến dịch; một người có thể có nhiều đầu việc trong lịch sử. Không cộng Work Item thành số người mà không ghi rõ đơn vị.
- **Đã kết nối**: có trao đổi hai chiều theo kết quả liên hệ hợp lệ. Không tự xác nhận phù hợp việc làm hoặc đã đi làm.
- **Hoàn tất mục tiêu chiến dịch**: giữ ngữ nghĩa outcome `converted` hiện có. Nhận việc/đi làm là sự kiện riêng cần người có thẩm quyền xác nhận.
- **Lịch hẹn hôm nay**: phân loại theo ngày của người dùng; dữ liệu Frappe lưu theo timezone của site. Nhãn múi giờ và thời điểm tính phải có trên tổng quan.
- **Quá hạn**: nghĩa vụ/hẹn mở đã qua hạn, không tính lịch đã hoàn thành/hủy.

## Nâng cấp và bảo toàn dữ liệu

- Fixture của action `CSKH - Open CSKH Page from Deal` được giữ cùng tên nhưng tắt. Patch `v4_0.worker_care_workspace` tắt action trên site cũ, chỉ cập nhật `User.default_app` từ `crm` sang `crm_cskh` cho người dùng giữ vai trò chăm sóc/quản trị hệ thống (không sửa tài khoản ngoài nhóm chăm sóc hay `default_app` hợp lệ khác), và xóa cache để nhãn/website mới có hiệu lực. Hook `after_migrate` không ghi đè cấu hình `System Settings.default_app` trên các lần `migrate` về sau.
- Trong kịch bản hạ cấp nhanh chỉ cuộn image ứng dụng (`Tier 3 N -> N-1`, không khôi phục DB), `System Settings.default_app = 'crm_cskh'` khiến đăng nhập mới trên `N-1` fallback an toàn về `/app` (`200 OK`, không lỗi `403`); nếu muốn quay hẳn về trang đích `/crm` của `N-1`, quản trị viên đặt lại `System Settings.default_app = 'crm'`.
- Dữ liệu Deal cũ vẫn tồn tại để bảo toàn lịch sử. Không có migration xóa Lead/Deal/Work Item/Attempt/Task hoặc đổi role của người dùng.
- Triển khai qua image/nhánh ứng viên có kiểm chứng; không sửa trực tiếp container đang chạy. Cần chạy migrate, làm mới cache và khởi động lại worker theo quy trình phát hành hiện có.
- Thay đổi nằm trong custom app; giữ điều kiện upstream sạch của quy trình build đã khóa.

## Kiểm tra bắt buộc

- Staff, Leader, Admin và BOD vào trang sau đăng nhập, app switcher, URL cũ và back/forward đều có luồng chăm sóc phù hợp.
- Không còn thẻ doanh thu/giá trị giao dịch, shortcut Deal hay thao tác Convert to Deal trong luồng marketing.
- Không redirect API, POST, khách chưa đăng nhập, trang lỗi, nguồn bên ngoài hoặc đường dẫn giả giống `/crm`.
- Số tổng quan lấy từ toàn bộ tập dữ liệu có quyền, không từ trang 20/50 dòng. BOD không nhận PII và Leader không nhận dữ liệu ngoài team.
- Sai timezone, tab nền, lỗi API, chưa có dữ liệu và lỗi một phần phải có trạng thái rõ; không hiện số 0 thành công khi tải lỗi.
- Kiểm tra dịch thuật, routing host, DOM bridge, API scope và preview giao diện. UAT Frappe/browser trong môi trường cô lập vẫn là điều kiện trước phát hành.

## Phần nghiệp vụ tiếp theo

Worker Profile, Job Order và Application/Placement vẫn cần phát triển như mô hình riêng theo GAP-01/02/03. Từ đó bổ sung lịch chăm sóc sau đi làm D1/D3/D7/D30, nghỉ sớm/tái tuyển và báo cáo nguồn tạo ra người đi làm thực tế. Bản chuyển trọng tâm giao diện không tự tạo các dữ liệu đó.
