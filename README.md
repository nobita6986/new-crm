# CRM-CSKH

CRM chạy trên Frappe Framework + Frappe CRM, mở rộng bằng app `crm_cskh` trong cùng site và database MariaDB.

Sản phẩm phục vụ **bộ phận marketing quản lý và chăm sóc người lao động**. Điểm vào làm việc chính là `/cskh`: tổng quan hồ sơ cần chăm sóc, kết nối và lịch hẹn trong đúng phạm vi người dùng. Các trang tổng quan, doanh thu và giao dịch bán hàng gốc trong `/crm` không còn là luồng làm việc chính; người dùng được chuyển về tổng quan chăm sóc.

- [Quyết định chuyển trọng tâm sản phẩm và tiêu chí nghiệm thu](docs/architecture/adr-worker-care-product.md)
- Giữ nguyên tên kỹ thuật `CRM Lead`, `Sales User`, `Sales Manager` để bảo toàn dữ liệu và phân quyền. Trên giao diện dùng người lao động, nhân viên marketing và trưởng nhóm marketing.
- Các số liệu hiện có phản ánh **liên hệ và chăm sóc**. Số người nhận việc, đi làm và duy trì D7/D30 chỉ được bổ sung khi có mô hình và dữ liệu xác nhận riêng.

Các mốc nghiệm thu bên dưới là lịch sử của từng giai đoạn; chúng không thay thế quyết định phát hành cho bản ứng viên hiện tại.

**P0 đã nghiệm thu GO P1 ngày 2026-11-01.** PR #2 được merge tại `584cbc2`; CI trên `main` [#41320842991](https://github.com/nobita6986/new-crm/actions/runs/41320842991) đạt 99/99 JUnit, 8 auxiliary, HTTP 18/18, File-oracle 9/9 và remaining-gaps 74/74. Đây là mốc nghiệm thu DEV đã kiểm chứng, không phải xác nhận triển khai production.

- [Kế hoạch và phạm vi P0](docs/root-plan/P0.md)
- [Quyết định nghiệm thu và backlog P1](docs/architecture/p0-exit-report.md)
- [Kế hoạch P1](docs/root-plan/P1.md)
- [Version lock](docs/architecture/version-lock.md)

## Chạy DEV

Theo [quickstart](deploy/dev/README.md) hoặc [runbook đầy đủ](docs/operations/dev-setup.md). Dùng Docker Linux containers; bootstrap lấy upstream theo SHA đã ghim vào volume Linux. Repo được mount chỉ đọc để cung cấp app `crm_cskh` và các helper DEV có kiểm soát.

`INSTALL_CSKH=1` là bắt buộc: app chứa các permission/API guards, không phải thành phần tùy chọn. Nếu dùng `.env` cũ từ giai đoạn scaffold, cập nhật giá trị này trước khi bootstrap.

Hai tiến trình DEV cần chạy: `bench start` và Vite trong `apps/crm/frontend`. Cổng Vite host phải bằng cổng trong container; xem quickstart trước khi đổi cổng.

Tài khoản dữ liệu giả: `staff.a@dev.local`, mật khẩu `DevSeed-Only-2026!`. Chỉ dùng sau khi chạy seed DEV; quyền của Staff, Leader, CRM Admin và BOD khác nhau.

## Kiểm thử và API

- [CI và smoke/restore cô lập](docs/operations/smoke-deploy.md)
- [Hợp đồng API hiện hành](docs/architecture/api-contract.md)
- [Ma trận quyền](docs/architecture/permission-matrix.md)
- [Glossary tiếng Việt](docs/architecture/glossary-vi.md)

Chỉ dùng dữ liệu giả. Ownership service, BOD reporting, RA-05 HTTP đúng actor và hiệu năng import/index thuộc backlog P1 đã được phân owner; quyền chưa triển khai tiếp tục fail-closed.
