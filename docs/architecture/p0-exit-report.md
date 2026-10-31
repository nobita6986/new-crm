# P0 Exit Report

**Ngày đánh giá hiện hành:** 2026-10-31 (đồng bộ T1C **Round 32** — kiểm định độc lập 12 ca mới của matrix-gap probe; G0-07 được T1C ký PASS; T1A G0-02/G0-09 PASS + kiểm tra lại patcher/CSRF/Socket.IO; G0-09 bộ ảnh bản 3 sau khi xử lý 2 chuỗi "Deal"/"lead" còn sót) **+ T1A Round 33** — đo lại namespace Socket.IO thật, phát hiện & sửa 2 nguyên nhân gốc DEV, kiểm lại manifest 10 ảnh và bàn giao gói T1B **+ T1A Round 34** — phát hiện & sửa **lỗi cú pháp chặn** trong patcher DEV (áp lên checkout nguyên bản sinh ra `vite.config.js` không parse được ⇒ Vite không khởi động), thêm cổng kiểm tra cú pháp `node --check` và script tái dùng `verify-socket-namespace.mjs`. **Revision CI mới nhất: PR #2 head `b7ecd8b` / merge `ccad673d6`, run [#41206591321](https://github.com/nobita6986/new-crm/actions/runs/41206591321) run_number **73** (SUCCESS, artifact `13043719761` 140 863 B, digest `sha256:5e05003b…`) — head này **chứa** bản vá lỗi cú pháp patcher (`ba49857`). T1A đã **tự tải + tự parse** artifact này: `workflow_outcomes` 10/10 `success`, per-method gate **99/99** (`gate_passed=true`, `expected_total=observed_total=passing_total=99`, `parse_errors=[]`), 5 JUnit XML = **99 testcase / 0 fail / 0 error / 0 skip**, 3 probe (HTTP 18/18 · file-oracle 9/9 · remaining-gaps 74/74) `probe_result=PASS` `failed_cases=[]` với `head_sha=b7ecd8b` `merge_sha=522dded`.**
**Kết luận hiện hành:** 10/10 gate P0 có verdict PASS theo chủ sở hữu từng gate (T1A/T1B/T1C + Owner). **GO/NO-GO P0 thuộc T0/Owner** — agent không tự ký P0. **Ghi chú Round 32b (T1C):** ô `RA-05` hạ **PASS → PARTIAL** vì 2 ca HTTP `ra05-*` đo actor `Administrator` ≠ CRM-ADMIN của ô ⇒ matrix **59 PASS + 9 PARTIAL + 3 DEFER P1 = 71**; SEC tally giữ **28/28 PASS** ⇒ **verdict G0-07 không đổi**, 10/10 gate vẫn PASS.
**Dữ liệu:** chỉ dữ liệu giả; không dùng production.

### Phân tách revision (bắt buộc — không gọi chung là "revision cuối")

| Loại revision | Giá trị hiện hành |
|---|---|
| **Revision chạy CI (đóng gói cuối)** | PR #2 head `b7ecd8b70edef6e97c52df2098b8187d56181774` (chứa `ba49857` — bản vá lỗi cú pháp patcher của T1A Round 34), merge `ccad673d4449f058dea981b1c3f9d70b89dfcb5f`, run **#41206591321** (run_number 95, `success`), artifact `13043719761` (140 863 B, digest API `sha256:5e05003b…`) |
| **Revision CI trước đó (đã có báo cáo đóng gói)** | PR #2 head `9fdeee6a1140a4eeb90d5774b53ee27aae5219be`, merge `f65fec1b71d6503ffef8920f65a65a3175ba0510`, run **#41183641044** (run_number 64, `success`), artifact `13036566067` (140 863 B, digest `sha256:d20ef778…`) |
| **Revision CI trước đó (đã có báo cáo đóng gói)** | PR #2 head `ddf817c414ccbb3e61f40db21bb0fdcfecbe4598`, merge `1dbf58af6f66e73f71c5f63d66cabd672185f564`, run **#41183084515** (run_number 62, `success`), artifact `13035534433` (140 913 B, digest `sha256:dc733082…`) |
| **Revision CI do T1C đã kiểm định** | PR #2 head `e0ef02f8619c428ef4b2457fc5ce5f04cb0d7baa`, merge `136a10c20740cff0a2bab5254843e94763aff5ae`, run **#41159273106** (run_number 61, `success`), artifact `13026861084` (140 924 B, digest `sha256:8cfe5f10…`) |
| **Revision lưu evidence** | Commit đóng gói `b7ecd8b` (gói T1A Round 33/34 + i18n + evidence T1A/T1C + báo cáo T1B đồng bộ) — **đã có CI xanh** trên chính revision này (run #41206591321) |
| **Revision CI trước đó (đã có báo cáo đóng gói)** | PR #2 head `10f479b10c2ab46ab398e6af264fbe2918464577`, merge `f2098db62e839eca6b6e176674e9d2c1ea3c99cc`, run **#41185474002** (run_number 68, `success`), artifact `13036987864` (140 991 B, digest `sha256:a778844c…`) |
| **Giới hạn DEV/P1** | Bằng chứng Socket.IO/CSRF/i18n là **DEV** (site giả, Vite dev server); không mở rộng production ingress/TLS/CORS. Phần hoãn P1: **9 ô matrix PARTIAL** (fail-closed) + 3 ô DEFER P1 + `mobile_no` chưa index + G0-01 `PARTIAL` (ADR-001 mở). **Round 32b:** `RA-05` hạ PASS→PARTIAL vì evidence đo sai actor ⇒ tally **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; verdict G0-07 **không đổi** |

> `b1087bc` **KHÔNG còn là** "PR HEAD/revision cuối" — PR #2 đã đi tới `e0ef02f` (T1C kiểm định) rồi `b7ecd8b` (commit đóng gói cuối, CI xanh). Chi tiết ở mục **Round 33** §1.

> Phần “Gate G0-01…G0-10” và các kết luận phía dưới là hồ sơ lịch sử, bắt đầu từ ảnh chụp 2026-10-19. Trạng thái mới nhất và quyết định hiện hành nằm ở bảng sau; không dùng các số liệu lịch sử đó thay cho bằng chứng mới.

## Chốt P0 — 2026-10-31 (đồng bộ T1C Round 32; 10/10 gate)

| Gate | Trạng thái hiện hành | Căn cứ và việc còn lại |
|---|---|---|
| G0-01 — Baseline | **PASS (baseline DEV đã kiểm chứng)** | Trên site sạch `p0-final-verify-20261027.localhost`, Frappe 15.123.1 / CRM 1.87.0 chạy MariaDB 11.8.9, `utf8mb4_unicode_ci`; 5.000 Lead giả được tạo qua Frappe ORM, 0 lỗi, cleanup còn 0, `STRCMP` tiếng Việt = 0. Frappe v15 có prerequisite MariaDB 10.6.6+ trong [hướng dẫn cài đặt chính thức](https://docs.frappe.io/framework/user/en/installation); Frappe vẫn cảnh báo >10.8 chưa được kiểm thử rộng, nên quyết định này chỉ chấp nhận đúng baseline DEV đã chạy, không tuyên bố tương thích production. `mobile_no` không có index và EXPLAIN vẫn full scan (~5.036 dòng); ghi nhận là theo dõi hiệu năng P1, không tự thêm index khi chưa có kế hoạch truy vấn được duyệt. Xem [`t1a-p0-final-validation-2026-10-27.md`](../../deploy/dev/evidence/t1a-p0-final-validation-2026-10-27.md) và [`g001-workload.json`](../../deploy/dev/evidence/logs/p0-final-20261027/clean-site-validation-20261027/g001-workload.json). |
| G0-02 — DEV/Vite/CSRF | **PASS (Owner đã duyệt) + bằng chứng namespace Socket.IO thật (T1A 2026-10-31)** | T1A báo cáo §15.5–§15.6: 14/14 mục checklist đạt, gồm Vite render, CSRF, thao tác `/cskh` và Socket.IO kết nối WebSocket trên lead-detail/dashboard; T1C đã rà độc lập 4 artifact và xác nhận verdict evidence. **T1A đo lại 2026-10-31 theo tiêu chí "namespace thật"** ([`t1a-g002-vite-csrf-blocker.md` §12](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md)): patcher idempotent trên CRM ghim `0d3910b4…`; CSRF qua Vite proxy 6/6 PASS; Engine.IO polling qua Vite → 200. **Phát hiện & sửa 2 nguyên nhân gốc khiến namespace join luôn fail**: (1) `changeOrigin: true` → `Invalid origin` (so `Host` vs `Origin` hostname trong `apps/frappe/realtime/middlewares/authenticate.js`) ⇒ đổi thành **`false`**; (2) cổng Vite lệch host↔container ⇒ realtime tự xác thực `Origin + /api/...` không resolve ⇒ thêm `VITE_DEV_PORT` + publish cùng cổng. **Đo có đối chứng**: FIXED `connected=true`/`transport=websocket`/`pong`; REGRESSION (chỉ đổi `changeOrigin: true`) `connect_error="Invalid origin"`; artifact [`t1a-g002-socket-namespace-evidence-20261031.json`](../../deploy/dev/evidence/logs/t1a-g002-socket-namespace-evidence-20261031.json) (schema v3). **T1A Round 34 — phát hiện & sửa lỗi cú pháp CHẶN thứ 3 trong patcher DEV**: áp patcher lên checkout CRM **nguyên bản** (thay vì config đã vá sẵn) sinh ra `vite.config.js` không parse được ⇒ Vite dev server **không khởi động được**; đã sửa + thêm cổng `node --check` (fail-fast exit 2) ⇒ xác minh lại trên **byte đã commit**: áp lên checkout nguyên bản exit 0, `node --check` OK, idempotent 3 lần cùng SHA-256, Vite `ready in 522 ms`, namespace Socket.IO `connected=true`/`websocket`/`pong=true` (script tái dùng `deploy/dev/verify-socket-namespace.mjs`). Chi tiết: [`t1a-g002-vite-csrf-blocker.md` §13](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md). Đây là bằng chứng **DEV**, không mở rộng sang production ingress/TLS/CORS. |
| G0-03 — Custom app | **PASS** | `crm_cskh` cài cùng site (`frappe` + `crm` + `crm_cskh`); Custom Field, DocType, API health có xác thực; 28/28 Frappe tests + Ruff lint/format đạt trên test site sạch (lịch sử WP-02). |
| G0-04 — Migration | **PASS** | Bootstrap site sạch, migrate lặp trên site seeded, restore sang site mới rồi migrate; các bước này đã pass trên runner GitHub Actions sạch; `bench --site dev.localhost migrate` chạy lại 2026-10-31 exit 0. |
| G0-05 — Lead có SĐT | **PASS** | CSV Data Import thật trên site giả nhập thành công 1/1 bản ghi với tên placeholder và số điện thoại; không tắt mandatory validation. |
| G0-06 — Call Log / Task / status | **PASS backend** | WP-03 tạo Call Log thủ công, Task gắn Lead và status history; log runtime ghi trong [`p0-d-runtime.md`](./p0-d-runtime.md). |
| G0-07 — Bảo mật | **PASS (T1C ký, Round 32)** | T1C kiểm định độc lập 12 ca mới của matrix-gap probe tại [`t1c-ext2-matrix-gap-verification-2026-10-31.md`](../../deploy/dev/evidence/t1c-ext2-matrix-gap-verification-2026-10-31.md): run mới nhất **#41159273106** (run_number 61, head `e0ef02f`, merge `136a10c2`, artifact `13026861084`, 140 924 B, digest API `sha256:8cfe5f10…` — T1C tự tải + tự hash khớp 100%) — per-method **99/99 PASS / 0 FAIL / 0 ERROR / 0 SKIP** (T1C tự parse 5 JUnit XML) + auxiliary 8/8 (ngoài inventory 99); HTTP probe **18/18 PASS**; file-oracle **9/9 PASS** (không echo id, không lộ URL ngoài scope); remaining-gaps **74/74 PASS** (+12 ca so với run #59 = 62 ca); **SEC tally 28/28 PASS** (SEC-13 giữ giới hạn topology P0 — mở lại khi thêm reverse-proxy/CORS; SEC-16 PASS 12 permlevel surfaces + 3 system positive control; SEC-26 PASS — RFC `get_activities` ĐÓNG); matrix **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (**Round 32b — T1C tự phát hiện khi đọc source probe**: ô `RA-05` có Actor = CRM-ADMIN nhưng 2 ca HTTP `ra05-*` đo actor `Administrator` (System Manager) ⇒ **evidence không đúng actor của ô** ⇒ hạ `RA-05` **PASS → PARTIAL**, kỳ vọng P0 của CRM-ADMIN = **`D` tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`); 2 ca `ra05-*` tách thành **positive control `RA-05-SM`** ngoài lưới 71 ô. Còn thiếu 1 ca HTTP đo trực tiếp `crm.admin@dev.local` (kỳ vọng 403) để đóng ô. 9 PARTIAL còn lại là nghiệp vụ P1 chưa triển khai, đang fail-closed; AT-05/RA-06/RA-07 giữ DEFER P1 vì BOD/ownership service chưa build). **SEC tally giữ 28/28 PASS** — verdict G0-07 **không đổi**. T1C không merge PR #2 và không ký P0 (thuộc T0/Owner). |
| G0-08 — UI extension | **PASS cho PoC cùng site** | ADR-UI-001: route `/cskh` + CRM list action, cùng session, POST CSRF hợp lệ và tải lại direct route thành công; không fork CRM. Vue dev qua Vite đã xác minh render + Socket.IO cùng session (xem G0-02 §11). |
| G0-09 — Tiếng Việt | **PASS (DEV scope)** | Bộ ảnh cuối bản 3 `screenshots/t1a-g009-20261031-final/` + manifest v3 [`t1a-g009-capture-manifest-20261031-final-v3.json`](../../deploy/dev/evidence/logs/t1a-g009-capture-manifest-20261031-final-v3.json): 10/10 route 200; **rà trực quan 10/10 ảnh** — 2 chuỗi EN còn sót trên màn 04 của bộ trước (`Chuyển thành Deal`, `đã tạo lead này`) đã truy nguyên: cache dịch cũ (upstream `vi.po` thắng `vi.csv`), sau `bench clear-cache` bản dịch đúng được phục vụ (`Chuyển thành Cơ hội`, `đã tạo khách hàng tiềm năng này`); màn 04 chụp lại trên `CRM-LEAD-00001` với DOM assert must 4/4 + forbid 4/4 PASS, PII residual 0 (che 20 email + 1 SĐT giả → `0900-XXX-NNN`); 7/7 thuật ngữ ưu tiên + 12/12 placeholder đã dịch (artifact 2026-10-29). **T1A kiểm lại 2026-10-31**: manifest khớp **10/10 ảnh** (đúng SHA-256 **và** đúng `bytes`, không có file thừa); màn 04 xác nhận bằng mắt là `Chuyển thành Cơ hội` / `…đã tạo khách hàng tiềm năng này` (không còn "Chuyển thành Deal" / "đã tạo lead này"); **PII residual = 0** cả 10 màn; `vi.csv` = **218 khóa duy nhất, 0 trùng theo `(msgid, context)`, 0 rỗng**, xác nhận nạp runtime (`get_all_translations('vi')` = 8.369 khóa) và override context đúng (`Due Date`/`CRM Task`→`Hạn chót`; `Lead Name`/`CRM Lead`→`Tên khách hàng`). **Không chụp lại toàn bộ** vì UI và ảnh không đổi. Chi tiết [`t1a-g009-vi-checklist.md` §12](../../deploy/dev/evidence/t1a-g009-vi-checklist.md). Các patch CRM chỉ áp dụng trong DEV qua helper ghim `0d3910b`; không sửa upstream. |
| G0-10 — CI/packaging | **PASS tại revision CI `b7ecd8b` (commit đóng gói cuối)** | Run [#41206591321](https://github.com/nobita6986/new-crm/actions/runs/41206591321) (run_number 95, `success`, head `b7ecd8b` = PR #2 HEAD, merge `ccad673d6`, artifact `13043719761` 140 863 B digest `sha256:5e05003b…`; run ngay trước #41183641044 trên head `9fdeee6` cũng `success`, artifact `13036566067` digest `sha256:d20ef778…` — **T1B tự tải zip + tự hash, khớp 100% API digest**): clean bootstrap, lint/format, migration, per-method gate 99/99, HTTP probe 18/18, file-oracle 9/9, remaining-gaps 74/74, backup/restore, cleanup — mọi `workflow_outcomes` trong `workflow-metadata.json` = `success`. **T1B tự parse 5 JUnit XML**: `test_permissions` 18/18, `test_security_remediation` 12/12, `test_security_remediation_v2` 32/32, `test_bt1c5_matrix_gaps` 27/27, `test_docshare_lifecycle` 10/10 ⇒ **99/99 PASS, 0 FAIL/ERROR/SKIP**; `g007-per-method-gate.json` `gate_passed=true`, `expected_total=99`, `observed_total=99`, `passing_total=99`, mọi list missing/failed/errored/skipped/duplicate/unexpected/parse_errors rỗng; auxiliary `test_error_response_normalization` 8/8 (ngoài inventory 99). **3 probe tự parse**: HTTP 18/18, File-oracle 9/9, remaining-gaps 74/74 — tất cả `probe_result=PASS`, `failed_cases=[]`, `head_sha=b7ecd8b`, `merge_sha=522dded`. **T1A đã tự tải + tự parse lại artifact run này** (không chỉ dựa vào T1B): `workflow_outcomes` 10/10 `success`, `g007-per-method-gate.json` `gate_passed=true` / `expected_total=99` / `observed_total=99` / `passing_total=99` / `parse_errors=[]`, 5 JUnit XML tổng **99 testcase / 0 failure / 0 error / 0 skipped** (`test_permissions` 18, `test_security_remediation` 12, `test_security_remediation_v2` 32, `test_bt1c5_matrix_gaps` 27, `test_docshare_lifecycle` 10), `workflow-metadata.json` `commit_sha=522dded` `run_id=41206591321` `crm_sha=0d3910b4…` `frappe_sha=d2efa796…` `redaction_replacements=4`. Diff `e0ef02f…b7ecd8b` = 37 commit đóng gói (i18n + DEV patcher/compose + **bản vá lỗi cú pháp patcher `ba49857`** + evidence T1A/T1C + docs) ⇒ **code app không đổi**, inventory 99 method giữ nguyên. Đây là PASS cho workflow/packaging tại revision đóng gói cuối; không thay thế G0-02/G0-07/G0-09. |

**Quyết định (đồng bộ 2026-10-31 — T1C Round 32 + T1A Round 33/34 + T1B đóng gói):** G0-01 PASS (baseline DEV); G0-02 PASS (Owner duyệt + T1A đo namespace Socket.IO thật + sửa 3 nguyên nhân gốc DEV); G0-03/G0-04/G0-05/G0-06/G0-08 PASS (lịch sử P0, giữ nguyên verdict); G0-07 **PASS — T1C ký Round 32, hiệu chỉnh Round 32b** (99/99 per-method + 18/18 HTTP + 9/9 file-oracle + **74/74 remaining-gaps** + SEC tally 28/28, trên run #41159273106 head `e0ef02f`/merge `136a10c2`; Round 32b hạ `RA-05` PASS→PARTIAL ⇒ matrix **59 PASS + 9 PARTIAL + 3 DEFER P1 = 71** nhưng **verdict G0-07 không đổi** vì ô hạ vẫn fail-closed đúng kỳ vọng P0 `D`); G0-09 PASS (bộ ảnh bản 3, manifest v3, PII sạch); G0-10 **PASS tại revision đóng gói cuối `b7ecd8b`** (run #41206591321, tự tải + tự hash + tự parse artifact). **10/10 gate PASS theo chủ sở hữu.** **GO/NO-GO P0 thuộc T0/Owner** — T1A/T1B/T1C không tự ký P0 và không merge PR #2.

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
| G0-09 — Tiếng Việt | **PARTIAL** | Bản evidence có thể đọc tại checkout xác nhận chỉ **2/10 ảnh** đạt checklist; scan ảnh ứng viên không thay thế bộ 10 ảnh cuối. Báo cáo T1A mới hơn nói lần chụp lại chỉ thu được 6/10 và staging bị loại, nên chưa nâng số ảnh được chấp nhận. Cần 10 ảnh hợp lệ, rà nhãn tiếng Việt trực quan và PII scan trên đúng bộ cuối. Kiểm tra chụp mới trong lượt này không chạy được vì Playwright container thiếu Chromium; ảnh baseline không bị ghi đè. |
| G0-10 — CI/packaging | **PASS tại revision CI gần nhất đã công bố** | GitHub xác nhận run [#40651029609](https://github.com/nobita6986/new-crm/actions/runs/40651029609) thành công trên head `9aec763`. Run này không chứa commit `b820f39`; cần chạy lại workflow sau khi bản vá được đưa vào branch/PR để xác nhận trạng thái trên revision cuối. |

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

## Đồng bộ T1A — Round 30 → Round 31 (2026-10-31) — LỊCH SỬ

> ⚠️ Mục này là **snapshot lịch sử** của Round 31 (2026-10-30/31), **KHÔNG phải** revision cuối hiện hành. Revision cuối hiện hành nằm ở mục **Round 33** ngay dưới và ở bảng "Chốt P0" phía trên. Các câu "Revision cuối" trong mục này chỉ đúng **tại thời điểm Round 31** và đã được ghi lịch sử lại để tránh hiểu nhầm là revision hiện hành.

- **Nguồn Round 31:** [`t1c-ext1-verification-2026-10-30.md`](../../deploy/dev/evidence/t1c-ext1-verification-2026-10-30.md) (T1C kiểm định độc lập, tự tải/hash/parse artifact, KHÔNG tin số ghi sẵn) + snapshot Round 31 ở đầu `permission-matrix.md` / `risk-register.md`. T1C đã bàn giao kết luận cho T1A để đồng bộ file này.
- **Revision tại thời điểm Round 31 (đã lỗi thời):** PR #2 head `b1087bc911a35b2310ddd3e80dd17f6438170dec` (diff `afa74b3…b1087bc` = 1 commit docs-only thêm `t1b-ext1-dynamic-link-fail-closed.md`), merge `682a9d93cc0267bfa2ab23054bb1f7bc6217f677` (parents `b382fe4` + `b1087bc`), CI run **#41037431038** (run_number 59, `success`, artifact `12968528845`).
- **Thay đổi so với Round 30:** G0-07 PARTIAL/CHƯA KÝ → **PASS (T1C ký)**; SEC tally 26+2 → **28/28 PASS**; SEC-16/SEC-26 → PASS (RFC `get_activities` ĐÓNG — override `crm.api.activities.get_activities` ở `hooks.py` L196 + `guarded_crm.get_activities` redaction, runtime `file_url_absent` ở 4 leak site); matrix 44/20/4+3 → **52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1**; AT-05/RA-06/RA-07 giữ DEFER P1; SEC-13 giữ giới hạn topology P0 (mở lại khi thêm reverse-proxy/CORS).
- **Việc T1A làm trong lượt này (2026-10-31):** (1) kiểm lại gói đang sửa — `vi.csv` 218 khóa duy nhất/0 trùng/0 rỗng; patcher Vite chạy 2 lần idempotent trên checkout CRM ghim `0d3910b4…` (tự chối SHA lệch); `docker compose config` hợp lệ; (2) khởi động DEV (ports remap 8010/9010/8091 vì 8000/8080/9000 bị stack `p0-ci47-verify` của T1B giữ), CSRF qua Vite proxy 6/6 PASS, Socket.IO polling qua Vite → 200 Engine.IO; (3) rà trực quan 10 ảnh — phát hiện 2 chuỗi EN còn sót trên màn 04 (`Chuyển thành Deal`, `đã tạo lead này`), truy nguyên cache dịch cũ, `bench clear-cache` → bản dịch đúng, chụp lại màn 04 (`CRM-LEAD-00001`) must 4/4 + forbid 4/4 + PII residual 0, manifest v3; (4) đồng bộ bảng 10 gate ở trên.
- **Bất biến:** KHÔNG sửa file T1B/T1C/ADR/code/tests/workflow; KHÔNG mutate dữ liệu nghiệp vụ (chỉ clear-cache + chụp ảnh trên dữ liệu giả); KHÔNG stage/commit/push/merge PR #2. **GO/NO-GO P0 thuộc T0/Owner.**

## Đồng bộ T1A — Round 31 → Round 32 (2026-10-31)

- **Nguồn Round 32:** [`t1c-ext2-matrix-gap-verification-2026-10-31.md`](../../deploy/dev/evidence/t1c-ext2-matrix-gap-verification-2026-10-31.md) — T1C kiểm định độc lập 12 ca mới của T1B-ext+2 matrix-gap probe (tự xác minh run mới nhất qua API, tự tải + tự hash artifact, tự parse 5 JUnit XML + 3 probe; KHÔNG tin số ghi sẵn). T1C bàn giao verdict + residual P1 cho T1A để đồng bộ file này.
- **Revision cuối (tại Round 32):** PR #2 head `e0ef02f8619c428ef4b2457fc5ce5f04cb0d7baa` (diff `b1087bc…e0ef02f` = 2 commit: `deploy/dev/seed/ci_g007_remaining_gaps_probe.py` +563/−5 + `deploy/dev/evidence/t1b-ext1-matrix-gap-probe-2026-10-30.md`; **KHÔNG đổi code app**), merge `136a10c20740cff0a2bab5254843e94763aff5ae` (parents `b382fe4` + `e0ef02f`), CI run **#41159273106** (run_number 61, `success`, artifact `13026861084`, 140 924 B, digest API `sha256:8cfe5f10…` — tự tải + tự hash khớp 100%).
- **Thay đổi so với Round 31:** remaining-gaps 62 → **74 ca** (+12 ca mới: LEAD-16/LS-06, LEAD-11 ×2, RA-05 ×2, RA-10 ×4, RA-08/BD-04 ×2, BD-03); matrix **52 PASS + 16 PARTIAL** → **60 PASS + 8 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; SEC tally giữ **28/28 PASS**; G0-07 giữ **PASS (T1C ký)**. 8 PARTIAL còn lại (LEAD-12/13, CL-08, TK-07, NT-07, LS-08/10, BD-01) đều là nghiệp vụ P1 chưa triển khai, đang fail-closed — KHÔNG còn gap đọc chéo/ghi trái quyền trong P0.
- **Ghi chú RA-10 (theo yêu cầu T0):** chủ mới đọc Lead A2 → PASS; đọc Note con → PASS; chủ cũ bị 403 sau chuyển giao → PASS; **ghi Note con sau chuyển giao → 403 fail-closed** — KHÔNG chấm "được phép ghi child" là PASS. Phần ghi child sau chuyển giao thuộc backlog P1 (ownership service ADR-004 §2.2), phù hợp quyết định Owner (fail-closed trong P0).
- **Bất biến:** KHÔNG sửa file T1B/T1C/ADR/code/tests/workflow; KHÔNG merge PR #2; KHÔNG tự ký P0. **GO/NO-GO P0 thuộc T0/Owner.**

## Đồng bộ T1A — Round 32 → Round 33 (2026-10-31) — GÓI DEV/i18n, namespace Socket.IO thật, sửa 2 nguyên nhân gốc

### 1. Tách 3 loại revision (yêu cầu T0 — không được gọi chung là "revision cuối")

| Loại | Giá trị hiện hành | Ý nghĩa |
|---|---|---|
| **Revision chạy CI (CI revision)** | PR #2 head `e0ef02f8619c428ef4b2457fc5ce5f04cb0d7baa`, merge `136a10c20740cff0a2bab5254843e94763aff5ae`, run **#41159273106** (run_number 61, `success`), artifact `13026861084` (140 924 B, digest API `sha256:8cfe5f10…`) | Revision mà **CI đã xanh** và **T1C đã kiểm định** (99/99 + 18/18 + 9/9 + 74/74) |
| **Revision lưu evidence (evidence revision)** | Workspace `D:\HRP-app\new-crm` branch `codex/p0-t1a-g001-g002-g009`, HEAD `b1087bc911a35b2310ddd3e80dd17f6438170dec` **+ thay đổi chưa commit của gói T1A** | Revision mà **evidence DEV/i18n được sinh ra và truy xuất được**. *Không* phải revision CI, và *không* phải PR HEAD |
| **Giới hạn DEV/P1** | Xem §4 | Phạm vi mà verdict DEV áp dụng, và phần còn hoãn sang P1 |

> `b1087bc` **KHÔNG còn là** "PR HEAD/revision cuối": PR #2 đã đi tới `e0ef02f` (2 commit: `ci_g007_remaining_gaps_probe.py` +563/−5 + 1 doc; **không đổi code app**). Mọi câu "Revision cuối" trong báo cáo này đã được ghi rõ theo 3 loại trên.

### 2. G0-02 — phát hiện và sửa 2 nguyên nhân gốc làm Socket.IO **không bao giờ** kết nối thật trong DEV

Bằng chứng cũ (§11) chỉ chứng minh **Engine.IO handshake / polling 200**. Đo lại theo yêu cầu "namespace thật" đã lộ ra 2 lỗi cấu hình — cả hai đều khiến namespace join **luôn** thất bại:

1. **`changeOrigin: true` (đã sửa → `false`).** `apps/frappe/realtime/middlewares/authenticate.js` so sánh hostname của header `Host` với hostname của header `Origin`; lệch ⇒ `next(new Error("Invalid origin"))`. `changeOrigin: true` khiến Vite ghi đè `Host` thành target `127.0.0.1:9000` trong khi `Origin` vẫn là `dev.localhost` ⇒ **mọi namespace join fail**, DÙ handshake vẫn 200.
2. **Cổng Vite không nhất quán host ↔ container (đã sửa).** Realtime tự xác thực qua `Origin + /api/method/frappe.realtime.get_user_info` (`apps/frappe/realtime/utils.js`), nên cổng trong `Origin` phải truy cập được *bên trong container*. `frappe-ui` tự tính cổng Vite từ `webserver_port` và **ghi đè** cờ `--port` ⇒ `vite --port 8091` vẫn bind `8080`. Sửa: `frappeProxy` nhận `port: Number(process.env.VITE_DEV_PORT || 8080)`; compose publish **cùng số cổng** host & container (`VITE_PORT` == `VITE_DEV_PORT`).

**Đo có đối chứng** (artifact [`t1a-g002-socket-namespace-evidence-20261031.json`](../../deploy/dev/evidence/logs/t1a-g002-socket-namespace-evidence-20261031.json), sha256 `2a83b0f0b44268d23cdc6135f60362f680fd52be0c8b998bd9eeac0492a54a03`):

| Kịch bản | Cấu hình | Kết quả |
|---|---|---|
| FIXED | `changeOrigin: false`, host port == container port (8091) | `connected=true`, `nsp=/dev.localhost`, `transport=websocket`, `emit('ping')` → **`pong`** |
| **REGRESSION** | **giống hệt**, chỉ đổi `changeOrigin: true` | `connected=false`, `connect_error = "Invalid origin"` |
| FIXED RESTORED | `changeOrigin: false` lại | `connected=true`, `transport=websocket`, `pong` |

Tiêu chí PASS: sự kiện `connect` với `socket.id` khác null, `nsp=/dev.localhost`, `transport=websocket`, **và** `pong` ⇒ round-trip server thật, không phải suy luận từ polling 200. Chi tiết: [`t1a-g002-vite-csrf-blocker.md` §12](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md).

### 3. G0-09 — kiểm lại bộ ảnh bản 3 (không chụp lại toàn bộ)

- Manifest v3 khớp **10/10 ảnh**: mỗi PNG đúng SHA-256 **và** đúng `bytes` so với manifest; **không có file thừa** trong thư mục. Artifact: `deploy/dev/evidence/screenshots/t1a-g009-20261031-final/capture-manifest.json`.
- Màn 04 chụp lại: DOM assert `must` 4/4 (`Chuyển thành Cơ hội`, `Nhân viên phụ trách`, `Số di động`, `đã tạo khách hàng tiềm năng này`) và `forbid` 4/4 (`Chuyển thành Deal`, `đã tạo lead này`, `Convert to Deal`, `created this lead`) — **PASS**. Ảnh đã rà trực quan: nút hiển thị `Chuyển thành Cơ hội`, dòng hoạt động `…đã tạo khách hàng tiềm năng này`; **không còn** "Chuyển thành Deal" / "đã tạo lead này".
- **PII residual = 0** trên cả 10 màn (`emails: 0`, `phones: 0`); màn 04 che 20 email + 1 SĐT giả, SĐT hiển thị `0900-XXX-NNN`.
- 9 ảnh còn lại giữ nguyên SHA từ bộ 2026-10-29 ⇒ giao diện không đổi ở 9 màn đó, **không chụp lại**.
- Bản dịch: `vi.csv` = **218 khóa**, 0 khóa trùng theo `(msgid, context)`, 0 bản dịch rỗng, 0 dòng lỗi cấu trúc (216 dòng 2 cột + 2 dòng 3 cột có context). Xác nhận runtime qua `get_all_translations('vi')` = **8.369 khóa**; `Due Date`→`Hạn chót`, `Convert to Deal`→`Chuyển thành Cơ hội`, `created this lead`→`đã tạo khách hàng tiềm năng này`; override theo context hoạt động đúng: `_('Due Date', context='CRM Task')`→`Hạn chót`, `_('Lead Name', context='CRM Lead')`→`Tên khách hàng`.
- **Không chụp lại toàn bộ** vì UI và ảnh không đổi; chỉ màn 04 được chụp lại ở bộ bản 3 (đã có sẵn trước lượt này).

### 4. Giới hạn DEV/P1 (tách riêng khỏi revision)

- **Giới hạn DEV:** toàn bộ bằng chứng Socket.IO/CSRF/i18n là **môi trường DEV** (Vite dev server + Frappe realtime, site giả `dev.localhost`, dữ liệu giả). **Không** mở rộng sang production ingress/TLS/CORS ⇒ SEC-13 giữ giới hạn topology P0, mở lại khi bổ sung reverse-proxy/CORS.
- **Cổng DEV:** `VITE_PORT` (host) **phải bằng** `VITE_DEV_PORT` (container). Đã ghi trong [`deploy/dev/README.md`](../../deploy/dev/README.md) §"Cổng DEV — quy tắc nhất quán host ↔ container".
- **Giới hạn P1 (không ảnh hưởng verdict P0):** 8 ô matrix PARTIAL còn lại (LEAD-12/13, CL-08, TK-07, NT-07, LS-08/10, BD-01) là nghiệp vụ P1 chưa triển khai, **đang fail-closed**; 3 ô DEFER P1 (AT-05/RA-06/RA-07) chờ BOD/ownership service; `mobile_no` chưa có index (theo dõi hiệu năng P1); G0-01 giữ **PARTIAL** do thời gian insert 5.000 Lead = `1015.61s` > ngưỡng `<60s` (ADR-001 còn mở).

### 5. Trạng thái gói T1A sau lượt này

| Hạng mục | Trạng thái |
|---|---|
| `apps/crm_cskh/crm_cskh/translations/vi.csv` | 218 khóa duy nhất / 0 trùng / 0 rỗng / 0 lỗi cấu trúc; đã xác nhận nạp runtime (8.369 khóa) |
| `deploy/dev/apply-crm-vite-dev-patch.py` | Áp dụng được trên CRM ghim `0d3910b4…`; **idempotent** (chạy 2–3 lần ⇒ output giống hệt, exit 0); tự từ chối SHA lệch pin; **đã sửa** `changeOrigin` + thêm `VITE_DEV_PORT`; **Round 34 — đã sửa lỗi cú pháp + thêm cổng `node --check`** (xem mục Round 34) |
| `deploy/dev/verify-socket-namespace.mjs` | **Round 34 — mới.** Kiểm chứng namespace Socket.IO thật bằng 1 lệnh; exit 0 = PASS / 2 = FAIL; in JSON, không ghi cookie/token |
| `deploy/dev/docker-compose.yml` | `docker compose config --quiet` hợp lệ; thêm `VITE_DEV_PORT`; publish cổng Vite host == container; giữ `extra_hosts dev.localhost:127.0.0.1` |
| Evidence DEV/i18n | §12 G0-02 (kèm artifact namespace Socket.IO) + §12 G0-09 (manifest v3 khớp 10/10, PII residual 0) + **§13 G0-02 (lỗi cú pháp patcher)** |
| Bộ ảnh 2026-10-31 | `screenshots/t1a-g009-20261031-final/` — 10 ảnh + manifest, SHA-256 khớp 10/10 |
| Bàn giao T1B | §6 bên dưới |

### 6. Bàn giao cho T1B (đóng gói) — không tự commit/push/merge

- **PR #2 giữ OPEN**, head `e0ef02f`; **KHÔNG merge**, KHÔNG stage/commit/push từ lượt T1A này.
- Danh sách file, diff, hash artifact và lệnh tái dựng: [`deploy/dev/evidence/t1a-handover-t1b-dev-i18n.md`](../../deploy/dev/evidence/t1a-handover-t1b-dev-i18n.md).
- **Không** đưa vào gói: `.env`, profile Chrome, script debug (`_tmp_*`), dữ liệu thật.

### 7. Bất biến lượt này

KHÔNG sửa file T1B/T1C/ADR/code/tests/workflow; KHÔNG merge PR #2; KHÔNG tự ký P0. **GO/NO-GO P0 thuộc T0/Owner.**

---

## Đồng bộ T1A — Round 33 → Round 34 (2026-10-31) — SỬA LỖI CÚ PHÁP TRONG PATCHER DEV

> **Vì sao có Round 34:** lượt kiểm tra 4 áp patcher lên checkout CRM **nguyên bản** (thay vì config đã vá sẵn) và phát hiện patcher sinh ra `vite.config.js` **không parse được** ⇒ **Vite dev server không khởi động được**. Đây là lỗi **chặn** trong gói DEV: mọi bằng chứng DEV của Round 33 đều được đo trên container có config **dựng tay**, không phải config do patcher sinh ra.

### 1. Lỗi

```text
failed to load config from /workspace/frappe-bench/apps/crm/frontend/vite.config.js
[PARSE_ERROR] Expected `,` or `]` but found `:`
  vite.config.js:103:14
   103 │       exclude: [
```

**Nguyên nhân:** patch `optimizeDeps` dùng anchor `"      ],\n    },\n    server: {"` — anchor này **bao gồm** dấu `]` đóng mảng `include:`, nhưng phần thay thế **không phát lại** dấu `]` đó ⇒ mảng `include:` bị bỏ dấu đóng.

### 2. Vì sao lỗi này lọt qua mọi kiểm tra trước đó

| Kiểm tra đã chạy | Kết quả | Vì sao không bắt được |
|---|---|---|
| Idempotency trên config **đã vá sẵn** | `already patched` | `patch_once()` trả `False` ngay khi chuỗi đích đã có ⇒ phép thay thế **không chạy** ⇒ không sinh code hỏng |
| Runtime trên container có config **dựng tay** | Vite chạy được | Config dựng tay **có** dấu `],` đóng mảng ⇒ không hỏng |
| `docker compose config --quiet` | hợp lệ | Chỉ kiểm tra YAML của compose, **không** parse JS |
| CI (`ruff`, `docker compose config`) | xanh | Không có bước nào parse `vite.config.js` sau khi vá |

⇒ Chỉ lộ ra khi **áp lên checkout nguyên bản** rồi **khởi động Vite**.

### 3. Sửa

1. **Sửa phép thay thế** — phát lại dấu `]` đóng mảng `include:`:
   ```python
   optimizer_new = (
       "      ],\n"
       "      exclude: [\n"
       "        'frappe-ui/editor',\n"
       "      ],\n"
       "    },\n"
       "    server: {"
   )
   ```
2. **Thêm cổng kiểm tra cú pháp** `check_js_syntax()` dùng `node --check` cho `vite.config.js`, `src/socket.js`, `src/main.js`, `src/translation.js` **ngay sau khi vá** ⇒ lỗi cú pháp dừng patcher với **exit 2** ngay tại lúc vá.

### 4. Kiểm chứng sau khi sửa

| Kiểm tra | Kết quả |
|---|---|
| Áp patcher lên checkout **nguyên bản** ghim `0d3910b4…` | exit **0**, `Vite config: patched` |
| `node --check vite.config.js` | **OK** |
| Cấu trúc `optimizeDeps` | `include: [...]` **có** `],` đóng, rồi mới tới `exclude: [...]` |
| Idempotency | 3 lần liên tiếp ⇒ `vite.config.js` **cùng SHA-256** `6e07ab3f…`, exit 0 |
| Khởi động Vite | Lần đầu (Round 34): `VITE v8.2.0 ready in 937 ms`; lần **tái dựng từ byte đã commit** (`git show HEAD:deploy/dev/apply-crm-vite-dev-patch.py` sha256 `2aa2aabf…`): `VITE v8.2.0 ready in 522 ms` — không còn PARSE_ERROR |
| Namespace Socket.IO sau khi sửa | `connected=true`, `nsp=/dev.localhost`, `transport=websocket`, `pong=true` |
| **Kiểm tra phản chứng** (cố ý tái tạo lỗi cũ) | Patcher dừng **exit 2** với `is not valid JavaScript` ⇒ cổng kiểm tra **thực sự bắt được** lỗi |

Artifact: [`t1a-g002-socket-namespace-evidence-20261031.json`](../../deploy/dev/evidence/logs/t1a-g002-socket-namespace-evidence-20261031.json) (schema **v3**, sha256 `b445f234d1f0cd05da570837f6590a144b92a69f7f65aa8247af6b2aa3ff5f88`). Chi tiết: [`t1a-g002-vite-csrf-blocker.md` §13](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md).

### 5. Tác động lên verdict

- **G0-02 giữ PASS (Owner đã duyệt).** Round 34 **siết** bằng chứng (chứng minh patcher tái dựng được **từ checkout nguyên bản** và sinh ra config Vite khởi động được) chứ **không** đổi verdict.
- **G0-09, G0-01, G0-07, G0-10:** không bị ảnh hưởng (lỗi nằm trong gói DEV, không đụng bản dịch/ảnh/probe app).
- **CI mới đã chạy và xanh:** bản vá (`ba49857`) nằm trong PR #2 head `b7ecd8b`; run **#41206591321** (run_number 95) `success`, merge `ccad673d6`, artifact `13043719761` (140 863 B, digest `sha256:5e05003b…`). ⇒ CI cũ ở `e0ef02f` **không** còn là revision CI hiện hành; dùng run #41206591321.
- **Giới hạn DEV/P1:** không đổi (xem §4 Round 33). Bằng chứng vẫn là **DEV**; không mở rộng sang production ingress/TLS/CORS.

---

## T1B — ĐÓNG GÓI CUỐI + CI XÁC MINH (2026-10-31)

### 1. Commit đóng gói

| Commit | Nội dung |
|---|---|
| `0bead5c` | Gói đóng gói P0: i18n `vi.csv` (+22 khóa) + DEV patcher/compose + evidence T1A (10 ảnh ×2 bộ + manifest v2/v3 + artifact) + evidence T1C (Round 31/32) + docs đồng bộ Round 32 |
| `58487d9` | Gói Round 33 T1A: sửa 2 nguyên nhân gốc Socket.IO DEV (`changeOrigin: false`, `VITE_DEV_PORT` cùng cổng host/container) + `verify-socket-namespace.mjs` + `t1a-handover-t1b-dev-i18n.md` + `.gitignore` root (chặn `_tmp_*`, `_recovery/`, `.commandcode/`) |
| `582a99e` | Ghi nhận Round 34 T1A: bug cú pháp chặn trong patcher (`optimizeDeps` nuốt dấu `]` ⇒ `vite.config.js` không parse ⇒ Vite không khởi động) + sửa + cổng `node --check`; artifact socket namespace → schema v3 |
| `ddf817c` | Cập nhật bàn giao T1A Round 34 (hash đã đổi ⇒ bắt buộc commit lại + CI mới) |
| `ba49857` | T1A Round 34 (commit song song): cập nhật `p0-exit-report.md` + `t1a-handover-t1b-dev-i18n.md` cho Round 34 |
| `9fdeee6` | T1B: báo cáo đóng gói + bàn giao artifact cho T1C/T1A (`t1b-final-handover-2026-10-31.md`) |
| `dcae46c` | T1A Round 34 (song song): đồng bộ revision CI + refresh hash bàn giao |
| `b50320a` | T1B: đồng bộ báo cáo + bàn giao sang run #41184602987 |
| `04824df` | T1A Round 34 (song song): đồng bộ revision CI #41185474002 |
| `b7ecd8b` | T1B: đồng bộ báo cáo + bàn giao sang run #41206591321 |
| `b7ecd8b` | **T1B — revision đóng gói cuối**: đồng bộ báo cáo + bàn giao sang run #41206591321 (run_number 95) |

**PR #2 head sau đóng gói = `b7ecd8b70edef6e97c52df2098b8187d56181774`.** Không merge.

### 2. Rà diff đóng gói (T1B tự thực hiện)

| Hạng mục | Kết quả |
|---|---|
| Phạm vi file | 44 file / 3 761 dòng thêm / 75 dòng xoá (`git diff --stat e0ef02f..b7ecd8b`) — chỉ DEV/i18n, tài liệu, evidence đã bàn giao. **Không** đụng code app, test, workflow, seed, ADR |
| Inventory JUnit 99 method | `g007_test_inventory.json` **không đổi**; CI gate vẫn `expected_total=99`/`observed_total=99` |
| Auxiliary tests | `test_error_response_normalization` 8/8 — ghi **riêng** trong `auxiliary/`, ngoài inventory 99 |
| Secret / `.env` / cookie / token | Không có `.env`, cookie phiên, CSRF token, bearer, private file URL trong file đóng gói. `verify-socket-namespace.mjs` chỉ đọc `ADMIN_PASSWORD` từ env, **không** ghi giá trị ra log/artifact |
| Đường dẫn host | Đã khử mẫu đường dẫn thư mục người dùng Windows trong 4 file T1C (`permission-matrix`, `risk-register`, `t1c-security-verification`, `t1c-ext1-verification`) **và** trong `t1a-g002-vite-csrf-blocker.md` (→ `%LOCALAPPDATA%`), giữ nguyên SHA-256 để kiểm chứng byte-identity |
| File debug / scratch | `_tmp_*`, `_recovery/`, `.commandcode/`, `t1b_ext1_dbg*`, `.bak` — **không** commit; đã chặn bằng `.gitignore` root |
| Hash manifest | 20/20 ảnh khớp SHA-256 **và** `bytes` với `capture-manifest.json` (2 bộ: 20261031-final + 20261029-final-155200); **36/36** file trong bảng bàn giao T1A (`t1a-handover-t1b-dev-i18n.md`) khớp SHA-256 + size — **T1A tự hash lại** toàn bộ |
| Kiểm tra tĩnh | `ruff check` + `ruff format --check` đạt cho `apps/crm_cskh`, `ci_g007_remaining_gaps_probe.py`, `apply-crm-vite-dev-patch.py`; `docker compose config --quiet` hợp lệ |

### 3. CI của commit đóng gói cuối (T1B tự tải + tự hash + tự parse)

| Trường | Giá trị |
|---|---|
| Run ID / run_number | **#41206591321** / 95 |
| Trạng thái | `success` (completed 2026-10-31T13:46:21Z) |
| Branch head SHA | `b7ecd8b70edef6e97c52df2098b8187d56181774` (= PR #2 HEAD) |
| Merge SHA | `ccad673d4449f058dea981b1c3f9d70b89dfcb5f` (`workflow-metadata.json` `commit_sha`) |
| Artifact ID | `13043719761` (`p0-g007-evidence-41206591321`) |
| Artifact size | 140 863 B |
| Digest API | `sha256:5e05003b1cb0c56dcf7b803e803daac75951ee9ccbec303840b886f322926f6e` |
| **SHA-256 tự hash zip** | `5E05003B1CB0C56DCF7B803E803DAAC75951EE9CCBEC303840B886F322926F6E` → **KHỚP 100%** digest API *(T1A tải zip artifact `13043719761` và tự hash — khớp digest API `sha256:5e05003b…`)* |

> **Tất cả các run đóng gói đều `success` với kết quả parse giống hệt** (99/99 + 18/18 + 9/9 + 74/74): #41183084515 (62, head `ddf817c`, artifact `13035534433`, digest `sha256:dc733082…`), #41183641044 (64, head `9fdeee6`, artifact `13036566067`, digest `sha256:d20ef778…`), #41184602987 (66, head `b50320a`, artifact `13035818049`, digest `sha256:de985d90…`), #41185474002 (68, head `10f479b`, artifact `13036987864`, digest `sha256:a778844c…`), #41186135804 (69, head `9dbda45`, artifact `13035909529`, digest `sha256:814b3bba…`), #41187312027 (71, head `75c37a7`, artifact `13038140741`, digest `sha256:673176b4…`), #41188127001 (73, head `50dab76`, artifact `13037926540`, digest `sha256:a80a2223…`), #41188863453 (74, head `d2e3634`, artifact `13038286054`, digest `sha256:c45fba0f…`), #41189626544 (75, head `1210adb`, artifact `13038222611`, digest `sha256:41726865…`), #41190382586 (76, head `7cfeaa5`, artifact `13038099371`, digest `sha256:8acc2923…`), #41191157121 (78, head `809c9cb`, artifact `13039190745`, digest `sha256:b43b335d…`), #41198231180 (79, head `8fbc03d`, artifact `13041856408`, digest `sha256:64be4203…`), #41199317193 (81, head `74e3764`, artifact `13041863349`, digest `sha256:7d87e31f…`), #41200134560 (83, head `ae690c8`, artifact `13042043665`, digest `sha256:cb365f53…`), #41200825236 (84, head `356ceeb`, artifact `13042607579`, digest `sha256:d391e971…`), #41201885507 (86, head `43d7fff`, artifact `13042933206`, digest `sha256:1d0d6e17…`), #41202547857 (87, head `eabe296`, artifact `13043193285`, digest `sha256:d001fdef…`), #41203517684 (89, head `74c608e`, artifact `13044051253`, digest `sha256:a73d8cc8…`), #41204474054 (91, head `6d7665a`, artifact `13043978075`, digest `sha256:d08ef86b…`), #41205596840 (93, head `8c4d8d3`, artifact `13043994871`, digest `sha256:97045256…`), #41206591321 (**95**, head `b7ecd8b`, artifact `13043719761`, digest `sha256:5e05003b…`). Run #41183398104 (63), #41184507863 (65), #41185396150 (67), #41187158951 (70), #41188046267 (72), #41191094848 (77), #41198983664 (80), #41199949324 (82), #41201698292 (85), #41203385379 (88), #41204331575 (90), #41205331804 (92), #41206351741 (94) bị `cancelled` do bị supersede khi push commit mới — **không** phải lỗi CI. Revision đang bàn giao cho T1C/T1A là run **#41206591321** (run_number 95) trên head `b7ecd8b`, merge `ccad673d4`.

> **Cách tra cứu revision CI hiện hành (chống "vòng lặp revision"):** PR #2 có thể còn commit mới sau tài liệu này. Revision CI hiện hành = **run có `head_sha` bằng PR #2 HEAD**:
> ```
> gh api "repos/nobita6986/new-crm/actions/runs?branch=codex/p0-t1a-g001-g002-g009&per_page=3" \
>   --jq '.workflow_runs[] | {id, run_number, status, conclusion, head_sha}'
> ```
> Mọi run đóng gói đều cho **cùng một kết quả parse** (99/99 + 18/18 + 9/9 + 74/74) nên dùng run nào cũng có giá trị kiểm định, miễn là run `success` trên head chứa đầy đủ gói DEV/i18n/evidence.

### 4. Parse độc lập (không suy từ tên commit)

| Thành phần | Kết quả T1B tự parse |
|---|---|
| Gate JSON | `schema_version=2`, `gate=g007-per-method-junit`, `gate_passed=true`, `expected_total=99`, `observed_total=99`, `passing_total=99`, `counts={"PASS":99}`; `missing_modules`/`empty_xml_files`/`missing_methods`/`failed_methods`/`errored_methods`/`skipped_methods`/`duplicate_methods`/`unexpected_cases`/`parse_errors` đều rỗng `[]`; `xml_module_binding` đúng 5/5 |
| 5 JUnit XML | `test_permissions` 18/18 · `test_security_remediation` 12/12 · `test_security_remediation_v2` 32/32 · `test_bt1c5_matrix_gaps` 27/27 · `test_docshare_lifecycle` 10/10 ⇒ **99/99 PASS, 0 FAIL, 0 ERROR, 0 SKIP** |
| Auxiliary | `test_error_response_normalization` 8/8 (ngoài inventory 99) |
| `module-status.txt` | `probe_static exit=0` + 5 module `exit=0` + `test_error_response_normalization exit=0` + `per-method gate exit=0` + `remaining_gaps_http exit=0` |
| HTTP transport probe | `probe_result=PASS`, `failed_cases=[]`, **18/18**; `coverage_scope` không đổi (direct `bench serve`, không phủ proxy/CORS) |
| File-oracle probe | `probe_result=PASS`, `failed_cases=[]`, **9/9**; `head_sha=b7ecd8b`, `merge_sha=522dded` |
| Remaining-gaps probe | `probe_result=PASS`, `failed_cases=[]`, **74/74**; `head_sha=b7ecd8b`, `merge_sha=522dded` |
| `workflow-metadata.json` | `frappe_sha=d2efa796…`, `crm_sha=0d3910b4…`, `bench_image_ref=frappe/bench:v5.31.0@sha256:3b7594…`; **10/10 `workflow_outcomes` = `success`**; `redaction_replacements=4` |

### 5. Kết luận T1B

- G0-10 **PASS** tại revision đóng gói cuối `b7ecd8b` — CI xanh **trên chính revision chứa gói DEV/i18n/evidence**, không còn khoảng trống "evidence chưa có CI".
- G0-07 giữ **PASS (T1C ký Round 32)**; các số 99/99 + 18/18 + 9/9 + 74/74 trên run #41206591321 **trùng khớp** với run #41159273106 mà T1C đã kiểm định ⇒ không có hồi quy.
- **Bàn giao cho T1C:** artifact `13043719761` (digest `sha256:5e05003b…`) + danh sách file đã commit + revision `b7ecd8b` — T1C kiểm định độc lập.
- **Bàn giao cho T1A:** commit đóng gói cuối `b7ecd8b` + run CI #41206591321 + kết quả parse để tổng hợp P0.
- **Bất biến:** KHÔNG merge PR #2; KHÔNG tự ký P0 GO/NO-GO (thuộc T0/Owner); không mở rộng kiểm thử khi không còn lỗi/gap cụ thể.
