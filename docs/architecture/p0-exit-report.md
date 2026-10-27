# P0 Exit Report

**Ngày đánh giá hiện hành:** 2026-10-27 (CI run `#40512221066`, head `bef36e1`; DEV được khởi động lại và build lại)
**Kết luận hiện hành:** **NO-GO cho P1** — các gate chặn được liệt kê ở phần chốt hiện trạng bên dưới.
**Dữ liệu:** chỉ dữ liệu giả; không dùng production.

> Phần “Gate G0-01…G0-10” và các kết luận phía dưới là hồ sơ lịch sử, bắt đầu từ ảnh chụp 2026-10-19. Trạng thái mới nhất và quyết định hiện hành nằm ở bảng sau; không dùng các số liệu lịch sử đó thay cho bằng chứng mới.

## Chốt P0 — 2026-10-27

| Gate | Trạng thái hiện hành | Căn cứ và việc còn lại |
|---|---|---|
| G0-01 — Baseline | **PASS (baseline DEV đã kiểm chứng)** | Trên site sạch `p0-final-verify-20261027.localhost`, Frappe 15.123.1 / CRM 1.87.0 chạy MariaDB 11.8.9, `utf8mb4_unicode_ci`; 5.000 Lead giả được tạo qua Frappe ORM, 0 lỗi, cleanup còn 0, `STRCMP` tiếng Việt = 0. Frappe v15 có prerequisite MariaDB 10.6.6+ trong [hướng dẫn cài đặt chính thức](https://docs.frappe.io/framework/user/en/installation); Frappe vẫn cảnh báo >10.8 chưa được kiểm thử rộng, nên quyết định này chỉ chấp nhận đúng baseline DEV đã chạy, không tuyên bố tương thích production. `mobile_no` không có index và EXPLAIN vẫn full scan (~5.036 dòng); ghi nhận là theo dõi hiệu năng P1, không tự thêm index khi chưa có kế hoạch truy vấn được duyệt. Xem [`t1a-p0-final-validation-2026-10-27.md`](../../deploy/dev/evidence/t1a-p0-final-validation-2026-10-27.md) và [`g001-workload.json`](../../deploy/dev/evidence/logs/p0-final-20261027/clean-site-validation-20261027/g001-workload.json). |
| G0-02 — DEV/Vite/CSRF | **PARTIAL** | DEV vừa được khởi động lại an toàn (không xóa volume): DB healthy, `bench start` và Vite đang chạy; Frappe ping và `/crm/dashboard` qua Vite đều HTTP 200. Patcher dịch Vite chạy idempotent; lần trước browser đã xác nhận SPA, HMR và nút kiểm tra phiên `/cskh`. Chưa có lần kiểm tra browser/console sạch sau khởi động lại; phiên trước ghi nhận một số API trả HTML khiến JSON parse lỗi, chưa truy nguyên. Không kết luận CORS/WebSocket sạch; giữ `PARTIAL`. |
| G0-07 — Bảo mật | **PARTIAL / CHƯA KÝ** | Run [#40512221066](https://github.com/nobita6986/new-crm/actions/runs/40512221066), head `bef36e1`: gate **99/99 PASS**, 5 JUnit XML (99 testcase, 0 FAIL/ERROR/SKIP), HTTP probe **18/18 PASS**. Artifact `12737708206`, 132.892 byte, digest API `sha256:7ccc47862219c0350aad86cb169dd812cd40e3e91cb59d07277fe9aa0937caeb`. Probe direct tới `bench serve`, không bao phủ reverse-proxy/CORS. Quan sát mới trên fixture: 4 vị trí `get_activities` ghi `file_url_absent`, caller ngoài phạm vi nhận 403, không gửi request tải; điều này khác fingerprint `file_url` có mặt trong T1C Round 24. Commit `12d4229` có override/redaction `get_activities`; cần T1C đối chiếu revision, fixture và probe trước khi kết luận SEC-26. Tài liệu T1C trong checkout này còn cũ và RA-04 vẫn lệch (`A` trong matrix, `D` dự kiến fail-closed trong test). CI xanh không tự nâng SEC tally/matrix. |
| G0-09 — Tiếng Việt | **PARTIAL** | Đếm CSV trực tiếp: `vi.csv` có **195 khóa duy nhất**, 0 khóa trùng, 0 bản dịch rỗng (con số 191/196 trong các báo cáo cũ không khớp snapshot hiện tại). Patcher Vite đã chạy idempotent; CRM frontend build thành công sau bản vá (Vite 8.2.0, 3.147 modules). Các nhãn chính và nhãn biểu đồ có bản dịch trong patch DEV; browser trước đó đã thấy các trang chính bằng tiếng Việt. Chưa xác nhận trực quan sau patch cuối trên đủ 10 màn và chưa chạy PII sweep cho toàn bộ ảnh; checkout có 1/10 ảnh. Giữ `PARTIAL`. Xem [`t1a-p0-final-validation-2026-10-27.md`](../../deploy/dev/evidence/t1a-p0-final-validation-2026-10-27.md). |
| G0-10 — CI/packaging | **PASS trong phạm vi workflow PR #2** | Run [#40512221066](https://github.com/nobita6986/new-crm/actions/runs/40512221066) hoàn tất xanh trên head `bef36e1`: clean bootstrap, lint/format, migration, per-method gate 99/99, HTTP probe 18/18, backup/restore, khử dữ liệu nhạy cảm trong artifact và cleanup đều thành công. Đây là PASS cho workflow/packaging tại revision này; không thay thế G0-02/G0-07/G0-09 và không chứng minh reverse-proxy/CORS. |

**Quyết định:** G0-01 PASS trong phạm vi baseline DEV đã chạy; cảnh báo MariaDB 11.8 rộng hơn và full scan `mobile_no` được giữ làm giới hạn/theo dõi hiệu năng. G0-10 PASS trong phạm vi workflow PR #2 tại `bef36e1`, đã xác nhận qua run `#40512221066`. P0 vẫn **NO-GO cho P1**: G0-02 cần kiểm tra lại browser/console sau bản vá và truy nguyên lỗi JSON parse; G0-07 cần T1C hòa giải quan sát SEC-26 với Round 24, cập nhật matrix theo chính sách hiện hành (đặc biệt RA-04) và ký độc lập; G0-09 cần chụp đủ 10 màn và chạy PII sweep sau bản dịch mới. Các việc này không thể được thay thế bằng build hoặc CI xanh. Chi tiết phép đo mới xem [`t1a-p0-continuation-2026-10-27.md`](../../deploy/dev/evidence/t1a-p0-continuation-2026-10-27.md).

## Gate G0-01…G0-10 — ảnh chụp lịch sử 2026-10-19

| Gate | Trạng thái | Bằng chứng / phần còn thiếu |
|---|---|---|
| G0-01 — Baseline | **PARTIAL** (T1A 2026-10-19 lần 2: re-verify version sort, phát hiện phiên trước ghi nhầm `v15.99.0`/`v16.9.0` là "mới hơn pin" — thực tế thấp hơn; pin hiện tại `v15.123.1` vẫn hỗ trợ Python 3.14, baseline C3 không bị ảnh hưởng) | CRM `v1.87.0` / Frappe `15.123.1`, Python `3.14.8`, MariaDB `11.8`; clean bootstrap, migrate, test, backup/restore đều chạy. T1A 2026-10-19: (a) SHA 4 ref (L-01…L-04) không đổi; (b) digest `frappe/bench:v5.31.0` (`sha256:3b7594…`) và `mariadb:11.8` (`sha256:f16d7a3…`) đã thay đổi (đã cập nhật `version-lock.md` §5 và `.env.example`); (c) re-verify bằng `sort -V` cho thấy chỉ có CRM `v1.88.0` thực sự mới hơn pin (1 patch); `v15.99.0` thấp hơn pin 24 minor; `v16.9.0` thấp hơn pin 43 minor; (d) pin `v15.123.1` vẫn `>=3.10,<3.15` → hỗ trợ Python 3.14, không bị ảnh hưởng bởi policy thay đổi ở `v15.99.0`; (e) đề xuất Q-01…Q-08 ở [`t1a-g001-metadata-recheck.md`](../../deploy/dev/evidence/t1a-g001-metadata-recheck.md) §5.4 (giữ baseline C3, KHÔNG nâng `v1.88.0`, **KHÔNG lùi** về `v15.99.0`/`v16.9.0` vì là bản cũ hơn pin). Caveat MariaDB 11.8 vẫn mở (Q-07). |
| G0-02 — Local DEV và hot reload | **PARTIAL** (T1A 2026-10-19 lần 2: phiên Codex không có Docker/Yarn/browser, không tạo được runtime evidence; có lệnh curl + checklist 9 mục cho Owner) | Bench bootstrap từ volume rỗng và Python auto-reload đã xác minh. Vue production build chạy; trang qua Vite hiện trắng nên chưa xác minh Vue hot reload. Cổng Vite `8080` đã bind loopback trong compose. T1A 2026-10-19 (lần 2): xem [`t1a-g002-vite-csrf-blocker.md`](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md) cho (a) blocker runtime chi tiết (B-1…B-5), (b) đề xuất **V-A** (Vite proxy same-origin `/api` → 8000), **V-B** (SameSite=None) và **V-C** (ignore_csrf) đã bị loại/cấm, (c) **lệnh curl 5 bước** để verify CSRF (token hợp lệ → 200, thiếu token → 403, sai token → 403), (d) checklist 9 mục cần Owner điền khi chạy runtime. `dev-setup.md` §7.6 cập nhật với snippet `vite.config.js` + script curl. `vite.config.js` chưa được áp dụng trong phiên này (không có Docker để test). **KHÔNG đánh dấu PASS.** |
| G0-03 — Custom app | **PASS** | App cài cùng CRM; Custom Field, DocType, API; 28/28 Frappe tests và Ruff lint/format đạt trên test site sạch. |
| G0-04 — Migration | **PASS** | Bootstrap site sạch, migrate lặp trên site seeded, restore sang site mới rồi migrate; cùng gates đã pass trên runner GitHub Actions sạch. |
| G0-05 — Lead có SĐT | **PASS** | CSV Data Import thật trên site giả nhập thành công 1/1 bản ghi với tên placeholder và số điện thoại; không tắt mandatory validation. |
| G0-06 — Call Log / Task / status | **PASS backend** | WP-03 tạo Call Log thủ công, Task gắn Lead và status history; log runtime ghi trong [`p0-d-runtime.md`](./p0-d-runtime.md). |
| G0-07 — Security | **PARTIAL** (T1C 2026-10-19: phân biệt 3 lượt chạy có tổng test khác nhau; phân tích tĩnh 18 permission-boundary methods; per-cell PASS/PARTIAL/NOT RUN mapping) | 18 permission-boundary tests chạy trong app suite (xem `test_permissions.py` 543 dòng đã đọc). **Ba lượt chạy tổng test khác nhau đã ghi nhận** — 26 OK (`wp05-security-final.txt`), 27 OK (`p0-local-final.txt`), 28 OK (`p0-isolated-verification.md` CI run `#38712593755`) — đều tại revision `b382fe4`, ba site/lệnh khác nhau; **T1C từ chối gộp** thành một con số. Ranh giới owner/team, child records, file, export, reassignment, field scope và BOD fail-closed được kiểm tra per-route cho **~32/70 ô matrix**; **~38 ô matrix còn `CHƯA XÁC MINH`**, **SEC-13/14/16/26 vẫn `NOT RUN`** (transport/url leak chưa test), **SEC-15/22/23/27 còn `PARTIAL`**; chi tiết per-cell mapping ở [`permission-matrix.md`](./permission-matrix.md) §2–§9 (bản 1.0), bằng chứng đối chiếu độc lập ở [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md). Phiên T1C không có Docker/WSL2 nên **không chạy lại** site sạch. |
| G0-08 — UI extension | **PASS cho PoC cùng site** | ADR-UI-001: route `/cskh` + CRM list action, cùng session, POST CSRF hợp lệ và tải lại direct route thành công; không fork CRM. Vue dev qua Vite còn mở. |
| G0-09 — Tiếng Việt | **PARTIAL** (T1A 2026-10-19 lần 2: checklist hoàn thiện + lệnh Owner cần chạy; thiếu runtime evidence) | Glossary và trang CSKH mới hiển thị tiếng Việt; ảnh quan sát trong phiên browser nhưng chưa lưu vào repo; chưa rà đủ các màn P0 yêu cầu. T1A 2026-10-19 (lần 2): (a) [`t1a-g009-vi-checklist.md`](../../deploy/dev/evidence/t1a-g009-vi-checklist.md) có checklist 10 màn + 6 checks chuyên sâu + quy tắc khử PII khi chụp screenshot + **§6 lệnh Owner cần chạy trên máy có Docker + browser** (chuẩn bị seed, đặt ngôn ngữ user, chụp 10 ảnh, scan hard-code); (b) [`screenshots/README.md`](../../deploy/dev/evidence/screenshots/README.md) ghi quy ước đặt tên file `t1a-g009-NN-<screen>.png`; (c) `glossary-vi.md` v1.5 liên kết tới checklist T1A. Phiên này không có Docker/Yarn/browser nên chưa chụp được ảnh nào. |
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

### Lần 1 (phiên Codex đầu, ghi sai metadata)
- **Phạm vi T1A:** Owner giao đóng G0-01, G0-02, G0-09; KHÔNG sửa upstream CRM/Frappe.
- **Môi trường phiên:** chỉ có `git 2.55.0` + `node v22.23.3` + `WebFetch` MCP. **Không** có Docker / Python / Yarn / browser. Bằng chứng runtime mới **không thể** tạo trong phiên này.
- **Đã làm (lần 1):**
  - T1A-G0-01: đối chiếu lại `git ls-remote` cho 4 ref; cập nhật digest 2 image (bench, mariadb) trong `version-lock.md` §5 + `deploy/dev/.env.example`; đề xuất giữ baseline C3 trong P0. **Ghi nhầm:** `v15.99.0`/`v16.9.0` là "bản mới hơn" pin `v15.123.1`/`v16.52.0`. Evidence: `t1a-g001-metadata-recheck.md` (lần 1, commit `a497dbb`).
  - T1A-G0-02: ghi nhận blocker runtime; phân tích tĩnh (CSRF/session contract của `/cskh`, Vite port 8080); đề xuất V-A (Vite proxy `/api` same-origin) làm đường đi chính; cấm V-C (`ignore_csrf=1`); checklist V-01…V-10 + mẫu lệnh curl; cập nhật `dev-setup.md` §7.6. Evidence: `t1a-g002-vite-csrf-blocker.md` (lần 1).
  - T1A-G0-09: checklist 10 màn; 6 checks chuyên sâu; 5 checks ngôn ngữ/locale; 5 checks phía app mới; quy tắc khử PII; 10 screenshot cần thu (`t1a-g009-NN-*.png`); thư mục `screenshots/` đã có `.gitkeep` + `README.md`. Evidence: `t1a-g009-vi-checklist.md` (lần 1).

### Lần 2 (sửa theo phản hồi T0, ngày 2026-10-19)
- **Phản hồi T0:** `v15.99.0`/`v16.9.0` thực ra **thấp hơn** pin, không phải bản mới hơn. T0 yêu cầu: (a) xác minh lại bằng version sort; (b) lưu lệnh/output; (c) không gọi các tag thấp hơn là "bản mới hơn"; (d) hoàn tất bằng chứng runtime cho G0-02/G0-09 nếu có máy Docker, nếu không thì ghi rõ blocker + lệnh Owner cần chạy.
- **Đã sửa (lần 2):**
  - **G0-01 (re-verify version sort):** chạy lại `git ls-remote --tags --sort=v:refname` + `[version]::TryParse()` (tương đương `sort -V`):
    - Frappe v15: cao nhất `v15.123.1` (pin); `v15.99.0` xếp hạng 25/123 (thấp hơn pin 24 minor). 
    - Frappe v16: cao nhất `v16.52.0` (pin); `v16.9.0` xếp hạng 44/52 (thấp hơn pin 43 minor).
    - CRM v1: cao nhất `v1.88.0`; `v1.87.0` (pin) xếp hạng 2/88. **Đây là bản duy nhất thực sự mới hơn pin** (1 patch).
  - Đã verify `requires-python` qua `raw.githubusercontent.com`:
    - `v15.123.1` (pin) = `>=3.10,<3.15` → **hỗ trợ Python 3.14** (đúng biên).
    - `v15.99.0` (cũ hơn pin) = `>=3.10,<3.14` → giới hạn Python ≤ 3.13.
    - Baseline C3 (`v15.123.1` + Python 3.14.8) **không bị ảnh hưởng** bởi policy thay đổi ở `v15.99.0`.
  - Đã sửa `t1a-g001-metadata-recheck.md`: ghi rõ `v15.99.0`/`v16.9.0` là bản thấp hơn pin; Q-04/Q-05/Q-06 được viết lại: **KHÔNG lùi về `v15.99.0`/`v16.9.0`** vì là bản cũ hơn; **KHÔNG nâng CRM `v1.88.0`** vì chưa có runtime evidence.
  - Đã sửa `version-lock.md` v1.6: §8 ma trận C4–C7 mô tả đúng (`v15.99.0`/`v16.9.0` là bản thấp hơn pin); §10 O-06 viết lại để khẳng định pin hiện tại vẫn hỗ trợ Python 3.14.
  - **G0-02 (runtime):** phiên Codex vẫn không có Docker/Yarn/browser; đã thêm §3 "Lệnh Owner cần chạy" với script curl 5 bước cụ thể (lấy token → login → POST 200 → POST thiếu token 403 → POST sai token 403); đã thêm §4 checklist 9 mục PASS/FAIL chờ Owner điền; đã thêm §5 blocker log B-1…B-5. Vẫn ⛔ BLOCKER runtime, **KHÔNG đánh dấu PASS**.
  - **G0-09 (runtime):** phiên Codex vẫn không có Docker/browser; đã thêm §6 "Lệnh Owner cần chạy" với 6 bước (seed mask PII → đặt ngôn ngữ user → chụp 10 ảnh → probe i18n → scan hard-code → blocker log). Vẫn ⛔ BLOCKER runtime, **KHÔNG đánh dấu PASS**.
  - `dev-setup.md` §7.6 được rewrite với snippet `vite.config.js` cụ thể + script curl 5 bước.
- **Phần còn chưa đạt (cần máy có Docker/WSL2 + browser thật để chạy):**
  - G0-02: chưa áp dụng `vite.config.js` proxy; chưa có runtime evidence cho Vue hot reload + CSRF retention; chưa có 9 mục checklist được điền; chưa có `t1a-g002-runtime.md` với log thật.
  - G0-09: chưa có ảnh screenshot UI thật; chưa xác minh 6 lỗi ưu tiên đã được sửa trên UI thật; chưa có `crm_cskh.i18n_audit.probe_ui_strings` API.
  - G0-01: chưa chạy workload MariaDB 11.8 bổ sung (CSV import ~5.000 Lead, ALTER INDEX) để đóng ADR-001; chưa cập nhật ma trận lên `[RUNTIME] PASS cho workload`.
- **Báo cáo lệnh, môi trường/phiên bản, kết quả quan sát, đường dẫn ảnh/log, phần còn chưa đạt:** xem 3 file evidence (đã sửa lần 2). Ảnh/log runtime mới: **CHƯA CÓ** trong phiên này (lý do: thiếu Docker). Đường dẫn dự kiến sau khi Owner chạy: `deploy/dev/evidence/t1a-g002-runtime.md` (mới) + `deploy/dev/evidence/screenshots/t1a-g009-NN-*.png` + `deploy/dev/evidence/logs/t1a-g002-*.txt`.
- **Tuyên bố:** **KHÔNG tuyên bố GO P1** sau T1A (cả lần 1 và lần 2). G0-01 có thêm evidence metadata đúng (sau khi sửa) nhưng runtime evidence còn thiếu để đóng ADR-001; G0-02 vẫn PARTIAL (blocker runtime + chưa áp dụng V-A); G0-09 vẫn PARTIAL (thiếu screenshot + chưa render UI thật). P0 còn **NO-GO**.

## T0 review follow-up — 2026-10-19

- Đối chiếu độc lập `git ls-remote` xác nhận các ref: Frappe `v15.123.1` = `d2efa796…`, `v15.99.0` = `5cca1fa7…`, `v16.52.0` = `4515014c…`, `v16.9.0` = `e5b04e04…`. Theo thứ tự phiên bản, `v15.123.1` cao hơn `v15.99.0`, và `v16.52.0` cao hơn `v16.9.0`; vì vậy các câu T1A ghi `v15.99.0`/`v16.9.0` là bản mới hơn cần sửa trong version-lock/evidence trước khi dùng làm căn cứ quyết định. Không thay đổi baseline P0 từ metadata này.
- Đối chiếu thống kê T1C: bảng SEC §11.6 đánh dấu PASS cho SEC-01…12, SEC-17…22, SEC-24, SEC-25 và SEC-28 (21 dòng), trong khi phần tổng hợp ghi 19/28; cần T1C giải thích tiêu chí đếm và đồng bộ trạng thái tổng với từng dòng. Các nhánh PARTIAL/NOT RUN vẫn phải giữ nguyên cho đến khi có test tương ứng.
- Các cập nhật trên chưa bổ sung runtime evidence. G0-01, G0-02, G0-07 và G0-09 chưa được ký đóng; P0 vẫn **NO-GO**.
