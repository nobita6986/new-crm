# P0 Exit Report

**Ngày đánh giá:** 2026-10-19 (T1A: cập nhật metadata G0-01; checklist G0-09; ghi nhận blocker G0-02)
**Kết luận hiện tại:** **NO-GO cho P1** — chưa đạt mọi gate của P0 §12.
**Dữ liệu:** chỉ dữ liệu giả; không dùng production.

## Gate G0-01…G0-10

| Gate | Trạng thái | Bằng chứng / phần còn thiếu |
|---|---|---|
| G0-01 — Baseline | **PASS trong phạm vi đã chạy** (T1A: cập nhật digest; ma trận mở rộng; vẫn còn caveat MariaDB) | CRM `v1.87.0` / Frappe `15.123.1`, Python `3.14.8`, MariaDB `11.8`; clean bootstrap, migrate, test, backup/restore đều chạy. T1A 2026-10-19: (a) SHA tag không đổi; (b) digest `frappe/bench:v5.31.0` và `mariadb:11.8` đã thay đổi (đã cập nhật `version-lock.md` §5 và `.env.example`); (c) đề xuất Q-01…Q-06 ở [`t1a-g001-metadata-recheck.md`](../../deploy/dev/evidence/t1a-g001-metadata-recheck.md) §5.4 (giữ baseline C3, KHÔNG nâng v1.88.0/v15.99.0 trong P0); (d) ma trận tương thích mở rộng C4–C7 ở `version-lock.md` §8 (đáng chú ý: C5 = v15.99.0 + Python 3.14 = **không tương thích**). Caveat MariaDB 11.8 vẫn mở (Q-05: ghi nhận policy warning + runtime evidence đã pass trong scope, cần thêm workload). |
| G0-02 — Local DEV và hot reload | **PARTIAL** (T1A: không tạo được bằng chứng runtime mới; có kế hoạch V-A) | Bench bootstrap từ volume rỗng và Python auto-reload đã xác minh. Vue production build chạy; trang qua Vite hiện trắng nên chưa xác minh Vue hot reload. Cổng Vite `8080` đã bind loopback trong compose. T1A 2026-10-19: phiên Codex không có Docker/Yarn nên không chạy được `yarn dev`; xem [`t1a-g002-vite-csrf-blocker.md`](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md) cho (a) blocker runtime, (b) đề xuất V-A (Vite proxy same-origin `/api` → 8000), (c) checklist V-01…V-10 cần Owner chạy trên máy có Docker. `dev-setup.md` §7.6 đã cập nhật checklist chi tiết. |
| G0-03 — Custom app | **PASS** | App cài cùng CRM; Custom Field, DocType, API; 28/28 Frappe tests và Ruff lint/format đạt trên test site sạch. |
| G0-04 — Migration | **PASS** | Bootstrap site sạch, migrate lặp trên site seeded, restore sang site mới rồi migrate; cùng gates đã pass trên runner GitHub Actions sạch. |
| G0-05 — Lead có SĐT | **PASS** | CSV Data Import thật trên site giả nhập thành công 1/1 bản ghi với tên placeholder và số điện thoại; không tắt mandatory validation. |
| G0-06 — Call Log / Task / status | **PASS backend** | WP-03 tạo Call Log thủ công, Task gắn Lead và status history; log runtime ghi trong [`p0-d-runtime.md`](./p0-d-runtime.md). |
| G0-07 — Security | **PARTIAL** | 18 permission-boundary tests chạy trong app suite; 28/28 test tổng thể pass. Ranh giới owner/team, child records, file, export, reassignment, field scope và BOD fail-closed được kiểm tra. SEC-13/14/27 và các ô ngoài API được test chưa phủ hết transport/full matrix; chi tiết ở [`permission-matrix.md`](./permission-matrix.md). |
| G0-08 — UI extension | **PASS cho PoC cùng site** | ADR-UI-001: route `/cskh` + CRM list action, cùng session, POST CSRF hợp lệ và tải lại direct route thành công; không fork CRM. Vue dev qua Vite còn mở. |
| G0-09 — Tiếng Việt | **PARTIAL** (T1A: hoàn thiện checklist + quy tắc khử PII; thiếu runtime evidence) | Glossary và trang CSKH mới hiển thị tiếng Việt; ảnh quan sát trong phiên browser nhưng chưa lưu vào repo; chưa rà đủ các màn P0 yêu cầu. T1A 2026-10-19: (a) [`t1a-g009-vi-checklist.md`](../../deploy/dev/evidence/t1a-g009-vi-checklist.md) có checklist 10 màn + 6 checks chuyên sâu + quy tắc khử PII khi chụp screenshot; (b) [`screenshots/README.md`](../../deploy/dev/evidence/screenshots/README.md) ghi quy ước đặt tên file `t1a-g009-NN-<screen>.png`; (c) `glossary-vi.md` v1.5 liên kết tới checklist T1A. Phiên này không có Docker/Yarn nên chưa chụp được ảnh. |
| G0-10 — CI/packaging | **PASS trong phạm vi workflow** | Run mới nhất trên merge commit `b382fe425b3f1f6c2037cd3bed20e65e5df2d928` hoàn tất thành công: bootstrap sạch, lint/format, seed/migrate, cài test site + Frappe tests, backup/restore và cleanup. [GitHub Actions run #39185122032](https://github.com/nobita6986/new-crm/actions/runs/39185122032). Kết quả này chỉ xác nhận các bước workflow, không đóng các gate còn mở. |

## Kết luận bảo mật

Các test âm hiện có cho thấy những đường truy cập được thử đều bị giới hạn hoặc fail-closed. Đây không phải tuyên bố toàn bộ khoảng 70 ô permission matrix đã PASS. Đặc biệt, UI/API transport, invalid CSRF, và một nhánh tra cứu contact nội bộ vẫn cần chứng cứ trực tiếp; không được mở rộng quyền user thường cho tới khi kiểm tra xong.

## Điều kiện để ký GO P1

1. Hoàn tất SEC-13/14/27 và đánh dấu rõ phạm vi các ô permission matrix chưa chạy.
2. Chốt field scope/compatibility còn mở: trường Lead được ghi ở P0/P1 và cảnh báo MariaDB 11.8 của baseline.
3. Chứng minh Vue hot reload chạy qua Vite mà vẫn giữ CSRF hoặc ghi nhận phương án kỹ thuật được chấp nhận.
4. Lưu screenshot tiếng Việt vào repo và hoàn thành checklist màn P0.

## Bằng chứng chính

- [`P0 isolated verification`](../../deploy/dev/evidence/p0-isolated-verification.md)
- [`WP-02 app evidence`](../../deploy/dev/evidence/wp-02-p0b.md)
- [`WP-01 runtime`](../../deploy/dev/evidence/wp01-runtime.md)
- [`Permission Matrix`](./permission-matrix.md)
- [`ADR-UI-001`](./adr-ui-001.md)
- [GitHub Actions run #39185122032](https://github.com/nobita6986/new-crm/actions/runs/39185122032) — push run trên `main`, job `p0` completed/success; mọi bước bắt buộc đều completed/success.
- [GitHub Actions PR check](https://github.com/nobita6986/new-crm/pull/1/checks) — lịch sử PR #1.

## Đối chiếu trạng thái ngày 2026-10-19

- GitHub `main` trỏ đúng merge commit `b382fe425b3f1f6c2037cd3bed20e65e5df2d928` (PR #1). Run `P0 DEV verification` mới nhất cho commit này là #39185122032, event `push`, kết luận `success`.
- Lần kiểm tra này xác nhận trạng thái repo và workflow từ GitHub. Máy chạy phiên Codex hiện tại không có lệnh Docker, nên không chạy thêm kiểm tra DEV cục bộ và không tạo bằng chứng runtime mới.
- Cần đồng bộ tài liệu: `permission-matrix.md` §0.2 còn ghi chưa có security suite, trái với evidence WP-05 và các phần sau của matrix/risk register ghi suite đã chạy. `adr-004`/`adr-005` cũng còn câu trạng thái trước khi security suite được thêm. Không dùng các câu lịch sử này làm căn cứ phủ nhận evidence mới hơn; chủ sở hữu tài liệu cần sửa và gắn từng kết luận với run/test tương ứng.
- Các evidence lưu test totals khác nhau ở những thời điểm/phạm vi khác nhau. Trước khi báo cáo lại tổng số test, cần đối chiếu log của run cụ thể với revision và lệnh đã chạy; kết quả Actions API lần này chỉ xác nhận test step thành công, không cung cấp output đếm test.

**Kết luận vẫn NO-GO cho P1.** G0-02, G0-07, G0-09 cùng việc chốt tương thích MariaDB/field scope còn thiếu bằng chứng hoặc quyết định; G0-10 thành công không thay thế các gate đó.

## Đối chiếu T1A ngày 2026-10-19

- **Phạm vi T1A:** Owner giao đóng G0-01, G0-02, G0-09; KHÔNG sửa upstream CRM/Frappe.
- **Môi trường phiên:** chỉ có `git 2.55.0` + `node v22.23.3` + `WebFetch` MCP. **Không** có Docker / Python / Yarn / browser. Bằng chứng runtime mới **không thể** tạo trong phiên này.
- **Đã làm:**
  - T1A-G0-01: đối chiếu lại `git ls-remote` cho 4 ref (CRM/Frappe v15/Frappe v16/frappe_docker) — SHA không đổi; đã có CRM `v1.88.0`, Frappe `v15.99.0`/`v16.9.0` mới hơn; cập nhật digest 2 image (bench, mariadb) trong `version-lock.md` §5 + `deploy/dev/.env.example`; mở rộng ma trận tương thích C4–C7 (đáng chú ý: v15.99.0 đã bỏ Python 3.14); đề xuất Q-01…Q-06 (giữ baseline C3, không nâng trong P0, MariaDB caveat chuyển sang "đã ghi nhận" thay vì "blocker"). Evidence: [`t1a-g001-metadata-recheck.md`](../../deploy/dev/evidence/t1a-g001-metadata-recheck.md).
  - T1A-G0-02: ghi nhận blocker runtime; phân tích tĩnh (CSRF/session contract của `/cskh`, Vite port 8080); đề xuất phương án V-A (Vite proxy `/api` same-origin) làm đường đi chính; cấm V-C (`ignore_csrf=1`); checklist V-01…V-10 + mẫu lệnh curl; cập nhật `dev-setup.md` §7.6. Evidence: [`t1a-g002-vite-csrf-blocker.md`](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md).
  - T1A-G0-09: checklist 10 màn (Login, Preferences, Leads List, Lead Detail, Call Logs, Tasks, Data Import, Dashboard, `/cskh`, Error/Validation); 6 checks chuyên sâu (chuỗi ưu tiên cao `Lead/Chì`, `Note/Lưu ý`, v.v.); 5 checks ngôn ngữ/locale; 5 checks phía app mới; quy tắc khử PII khi chụp screenshot; 10 screenshot cần thu (`t1a-g009-NN-*.png`); thư mục `deploy/dev/evidence/screenshots/` đã có `.gitkeep` + `README.md`. Evidence: [`t1a-g009-vi-checklist.md`](../../deploy/dev/evidence/t1a-g009-vi-checklist.md).
- **Phần còn chưa đạt (cần máy có Docker/WSL2 để chạy):**
  - G0-02: chưa có runtime evidence cho Vue hot reload qua Vite + retention CSRF; chưa đọc `crm/frontend/vite.config.js` tại tag `v1.87.0` để xác nhận upstream đã có proxy rule hay chưa.
  - G0-09: chưa có ảnh screenshot UI thật; chưa xác minh `Lead → Chì` (và 5 lỗi ưu tiên khác) đã được sửa trên UI thật.
  - G0-01: chưa chạy workload MariaDB 11.8 bổ sung (CSV import ~5.000 Lead, ALTER INDEX) để đóng ADR-001; chưa cập nhật ma trận lên `[RUNTIME] PASS cho workload`.
- **Báo cáo lệnh, môi trường/phiên bản, kết quả quan sát, đường dẫn ảnh/log, phần còn chưa đạt:** xem 3 file evidence ở `deploy/dev/evidence/`. Ảnh/log runtime mới: **CHƯA CÓ** trong phiên này (lý do: thiếu Docker). Đường dẫn dự kiến sau khi Owner chạy: `deploy/dev/evidence/t1a-g002-runtime.md` + `deploy/dev/evidence/screenshots/t1a-g009-NN-*.png`.
- **Tuyên bố:** **KHÔNG tuyên bố GO P1** sau T1A. G0-01 có thêm evidence metadata nhưng runtime evidence còn thiếu để đóng ADR-001; G0-02 vẫn PARTIAL (blocker runtime + chưa áp dụng V-A); G0-09 vẫn PARTIAL (thiếu screenshot + chưa render UI thật). P0 còn NO-GO.
