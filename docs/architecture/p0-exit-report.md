# P0 Exit Report

**Ngày đánh giá:** 2026-10-15
**Kết luận hiện tại:** **NO-GO cho P1** — chưa đạt mọi gate của P0 §12.
**Dữ liệu:** chỉ dữ liệu giả; không dùng production.

## Gate G0-01…G0-10

| Gate | Trạng thái | Bằng chứng / phần còn thiếu |
|---|---|---|
| G0-01 — Baseline | **PASS trong phạm vi đã chạy** | CRM `v1.87.0` / Frappe `15.123.1`, Python `3.14.8`, MariaDB `11.8`; clean bootstrap, migrate, test, backup/restore đều chạy. Frappe vẫn cảnh báo MariaDB mới hơn `10.8` chưa được Framework kiểm thử; xem [`version-lock.md`](./version-lock.md) và [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md). |
| G0-02 — Local DEV và hot reload | **PARTIAL** | Bench bootstrap từ volume rỗng và Python auto-reload đã xác minh. Vue production build chạy; trang qua Vite hiện trắng nên chưa xác minh Vue hot reload. Cổng Vite `8080` đã bind loopback trong compose. |
| G0-03 — Custom app | **PASS** | App cài cùng CRM; Custom Field, DocType, API; 28/28 Frappe tests và Ruff lint/format đạt trên test site sạch. |
| G0-04 — Migration | **PASS cục bộ; CI chờ PR** | Bootstrap site sạch, migrate lặp trên site seeded, restore sang site mới rồi migrate. GitHub Actions phải xác nhận cùng gates trước khi đánh dấu CI pass. |
| G0-05 — Lead có SĐT | **PASS** | CSV Data Import thật trên site giả nhập thành công 1/1 bản ghi với tên placeholder và số điện thoại; không tắt mandatory validation. |
| G0-06 — Call Log / Task / status | **PASS backend** | WP-03 tạo Call Log thủ công, Task gắn Lead và status history; log runtime ghi trong [`p0-d-runtime.md`](./p0-d-runtime.md). |
| G0-07 — Security | **PARTIAL** | 18 permission-boundary tests chạy trong app suite; 28/28 test tổng thể pass. Ranh giới owner/team, child records, file, export, reassignment, field scope và BOD fail-closed được kiểm tra. SEC-13/14/27 và các ô ngoài API được test chưa phủ hết transport/full matrix; chi tiết ở [`permission-matrix.md`](./permission-matrix.md). |
| G0-08 — UI extension | **PASS cho PoC cùng site** | ADR-UI-001: route `/cskh` + CRM list action, cùng session, POST CSRF hợp lệ và tải lại direct route thành công; không fork CRM. Vue dev qua Vite còn mở. |
| G0-09 — Tiếng Việt | **PARTIAL** | Glossary và trang CSKH mới hiển thị tiếng Việt; ảnh quan sát trong phiên browser nhưng chưa lưu vào repo; chưa rà đủ các màn P0 yêu cầu. |
| G0-10 — CI/packaging | **PENDING GitHub Actions** | Workflow tạo CI PR chạy bootstrap sạch, build, lint, test, migrate, backup/restore. Chỉ PASS khi PR check hoàn tất xanh; link run sẽ được ghi sau khi chạy. |

## Kết luận bảo mật

Các test âm hiện có cho thấy những đường truy cập được thử đều bị giới hạn hoặc fail-closed. Đây không phải tuyên bố toàn bộ khoảng 70 ô permission matrix đã PASS. Đặc biệt, UI/API transport, invalid CSRF, và một nhánh tra cứu contact nội bộ vẫn cần chứng cứ trực tiếp; không được mở rộng quyền user thường cho tới khi kiểm tra xong.

## Điều kiện để ký GO P1

1. GitHub Actions trên PR xanh, bao gồm test site sạch và backup/restore.
2. Hoàn tất SEC-13/14/27 và đánh dấu rõ phạm vi các ô permission matrix chưa chạy.
3. Chứng minh Vue hot reload chạy qua Vite mà vẫn giữ CSRF hoặc ghi nhận phương án kỹ thuật được chấp nhận.
4. Lưu screenshot tiếng Việt vào repo và hoàn thành checklist màn P0.

## Bằng chứng chính

- [`P0 isolated verification`](../../deploy/dev/evidence/p0-isolated-verification.md)
- [`WP-02 app evidence`](../../deploy/dev/evidence/wp-02-p0b.md)
- [`WP-01 runtime`](../../deploy/dev/evidence/wp01-runtime.md)
- [`Permission Matrix`](./permission-matrix.md)
- [`ADR-UI-001`](./adr-ui-001.md)
- GitHub Actions: pending PR run.
