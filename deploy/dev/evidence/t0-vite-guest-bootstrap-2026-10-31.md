# DEV bootstrap — khắc phục màn hình trắng trong phiên Chrome mới

Ngày kiểm tra: 2026-10-31. CRM source ghim `0d3910b456ef4fb913e77b29db011b3debc4f778`.

Người dùng quan sát `GET /cskh 403` rồi `CRM Vite boot failed: CSRF bootstrap
returned 403`. Helper DEV trước đây đợi CSRF từ `/cskh` trước khi mount Vue.
Route này từ chối Guest và tài khoản thiếu quyền CRM Lead, nên app không thể
đi tới router xử lý đăng nhập. Workbox navigation allowlist chỉ là log phụ.

Helper nay dùng `/desk` cùng origin với cookie hiện tại và `cache: no-store`.
Nếu Desk chuyển tới `/login`, browser đi tới login với đường dẫn CRM ban đầu.
Chỉ sau khi có CSRF hợp lệ và context DEV mới mount Vue. Lỗi khác hiển thị
thông báo phục hồi bằng DOM text, không ghép HTML từ exception. Helper cũng
hỗ trợ nâng cấp initializer đã patch và chạy lặp idempotent.

Kiểm chứng:

- Chạy helper trên source DEV hiện tại: lần đầu nâng cấp thành công, lần hai
  `already patched`, cả hai exit 0.
- 6 test thực thi chính JavaScript mà helper sinh ra: Guest redirect giữ route,
  authenticated bootstrap đúng thứ tự, thiếu/None CSRF không POST, Desk 403
  không lặp redirect và context lỗi hiện recovery UI. Tất cả PASS.
- HTTP thật: Guest `/desk` tới login; Staff `/desk` 200 và context POST 200;
  BOD `/cskh` vẫn 403 nhưng `/desk` và context POST 200. POST thiếu/sai CSRF
  vẫn 400 ở cả Staff và BOD. Không thay đổi quyền nghiệp vụ hoặc bỏ CSRF.
- Tải lại trang CRM trong trình duyệt hiện danh sách/controls thay vì trắng.
  Chrome người dùng cần tải lại mã Vite để nhận bản sửa.

Kết quả HTTP đã khử token/cookie nằm trong
[`logs/vite-guest-bootstrap-20261031.json`](logs/vite-guest-bootstrap-20261031.json).
Các test này là hồi quy DEV bổ sung, ngoài inventory 99 method bảo mật.
