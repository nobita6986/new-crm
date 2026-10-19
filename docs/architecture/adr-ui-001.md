# ADR-UI-001 — Tích hợp UI CSKH cùng site Frappe CRM

**Trạng thái:** Chấp nhận cho P0; chưa chốt cách xây các màn nghiệp vụ Vue ở P1.
**Ngày:** 2026-10-15

## Bối cảnh

CRM `v1.87.0` chưa có extension point ổn định để app khác thêm route vào CRM SPA. Fork frontend upstream làm tăng chi phí nâng cấp. P0 cần chứng minh một lối vào CSKH cùng site, giữ phiên đăng nhập và kiểm tra quyền ở server.

## Quyết định

- Dùng **UI-A + UI-B**: trang Frappe `/cskh` do `crm_cskh` phục vụ, kèm action trong danh sách `CRM Lead` để mở trang đó.
- Giữ CRM, app CSKH, đăng nhập và session trên cùng site; trang kiểm tra quyền đọc Lead trước khi render và dùng CSRF token Frappe cho POST.
- Không fork hoặc sửa source `frappe`/`crm` để thêm route.
- Màn Vue nghiệp vụ của P1 sẽ đánh giá lại sau khi có PoC Vite dev được mà vẫn giữ CSRF.

## Bằng chứng runtime

- Fixture [`crm_form_script.json`](../../apps/crm_cskh/crm_cskh/fixtures/crm_form_script.json) thêm action mở `/cskh` từ danh sách Lead.
- [`cskh.py`](../../apps/crm_cskh/crm_cskh/www/cskh.py) từ chối Guest và user không có quyền đọc Lead.
- Trình duyệt vào trực tiếp `/cskh`, tải lại route, hiển thị user `staff.a@dev.local`; POST kiểm tra session/CSRF trả `ok: staff.a@dev.local`.
- Trang và lối quay về CRM hiển thị nhãn tiếng Việt. Ảnh được quan sát trong browser của phiên Codex nhưng chưa lưu thành file bằng chứng trong repo.

## Giới hạn

Vite dev server chạy trên cổng loopback `8080`, nhưng trang CRM qua Vite hiện hiển thị trắng; chưa chứng minh Vue hot reload. Dự án không bật `ignore_csrf`. Vì vậy ADR này chốt đường tích hợp cùng site cho P0, không xác nhận trải nghiệm Vue dev đã đạt G0-02.
