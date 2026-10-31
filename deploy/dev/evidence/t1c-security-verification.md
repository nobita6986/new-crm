# EVIDENCE — T1C độc lập kiểm G0-07 (security)

## Snapshot hiện hành T1C — Round 32b (2026-10-31)

> **Round 32b (2026-10-31, bổ sung):** hạ ô `RA-05` **PASS → PARTIAL** vì 2 ca HTTP `ra05-*` đo actor `Administrator` (System Manager) trong khi ô `RA-05` có Actor = CRM-ADMIN ⇒ evidence **không đúng actor của ô**. Tách 2 ca `ra05-*` thành positive control `RA-05-SM` (§7.0.1 `permission-matrix.md`, **ngoài** lưới 71 ô). **Tally hiện hành: 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (T1A đếm lại từ cả 2 bảng: `permission-matrix.md` §§2–§9 và bảng audit 71 ô trong file này — **mismatch = 0**). SEC tally giữ **28/28 PASS** ⇒ **verdict G0-07 = PASS không đổi**. Xem mục **Round 32b** bên dưới.
>
> Round 32 (2026-10-31) — kiểm định độc lập **12 ca mới** (T1B-ext+2 "matrix gap probe"). Run `#41159273106` (run #61), head `e0ef02f`, merge `136a10c2` (= merge `e0ef02f` vào `b382fe4`), SUCCESS; artifact `13026861084` (140 924 B) digest `sha256:8cfe5f10…` — T1C tự tải + tự hash KHỚP. Diff `b1087bc…e0ef02f` = 2 commit: chỉ thêm probe `deploy/dev/seed/ci_g007_remaining_gaps_probe.py` (+563/−5) + doc `t1b-ext1-matrix-gap-probe-2026-10-30.md` (+90); **KHÔNG đổi code app** (`guarded_crm.py`/`hooks.py` nguyên).
>
> **Provenance độc lập T1C (tự làm, không tin số ghi sẵn):** xác minh run qua `GET /actions/runs/41159273106` (`success`, `head_sha=e0ef02f8619c…`, `pull_requests=[2]`) + `refs/pull/2/merge` = `136a10c2…`, parents = `[b382fe4…, e0ef02f…]`, `main` vẫn `b382fe4`; artifact `GET /actions/artifacts/13026861084` (`size_in_bytes=140924`, `digest=sha256:8cfe5f10…`, `expired=false`). **Tải zip binary-safe + tự hash: `8cfe5f107d38fca35bd2dec4e51ef036c521a6107cbc364cd48058916183304a` — KHỚP 100%.** Tự parse đủ 22 file: gate `gate_passed=true` / `expected_total=observed_total=passing_total=99` / `counts={PASS:99}` / mọi list `missing|failed|errored|skipped|parse_errors|missing_modules|empty_xml_files|unexpected_cases` **rỗng**; **99 `<testcase>` full-tree** (18+12+32+27+10 — bộ đếm đầu tiên chỉ đọc `testsuite` con đầu nên ra 33, T1C đã đếm lại toàn cây → 99); **18/18 HTTP** (`probe_result=PASS`, `failed_cases=[]`, `commit_sha=136a10c2…`, `coverage_scope` ghi rõ *không* phủ proxy/CORS); **9/9 file-oracle**; **74/74 remaining-gaps** (`failed_cases=[]`); auxiliary `test_error_response_normalization` **8/8**.
>
> **Đối chiếu regression run #59 → #61 (T1C tự tải cả hai artifact):** artifact run #59 (`12968528845`) tự hash `c6b298a8…` khớp giá trị T1C ghi ở Round 31 ⇒ **bằng chứng Round 31 còn hiệu lực**. So `remaining-gaps` case-id hai run: **+12 ca mới** (đúng 12 id đã nêu), **0 ca bị mất**, **0 ca nào đổi trạng thái assertion** ⇒ không regression trên 62 ca cũ. 74/74 assertion `PASS`.

### Round 32 — 12 ca mới & chốt matrix P0

- Tự parse: **99/99 JUnit** (18+12+32+27+10, 0 FAIL/ERROR/SKIP, gate xanh) + **18/18 HTTP** + **9/9 file-oracle** + **74/74 remaining-gaps** (`probe_result=PASS`, `failed_cases=[]`) — tăng **12 ca** so với run #59 (62 ca).
- **12 ca mới → 8 ô matrix PARTIAL → PASS:**
  - `lead16-ls06-get-data-field-scope` → **LEAD-16 + LS-06** (`get_data rows=[mobile_no,email]` trả field in-scope, `leaked_marker_count=0` — field Lead ngoài scope không lộ).
  - `lead11-team2-leader-list-scope` (positive) + `lead11-team2-leader-direct-read-denied` (403) → **LEAD-11**.
  - `ra05-cross-team-transfer-admin-set-owner` + `ra05-cross-team-transfer-new-owner-visible` (chủ mới thấy, chủ cũ chặn) → **positive control actor System Manager** (`RA-05-SM`, §7.0.1 matrix) — **KHÔNG nâng ô `RA-05`** (actor CRM-ADMIN).
  - `ra10-*` (4 ca) → **RA-10** — xem ghi chú chính xác bên dưới.
  - `bod-remove-assignments-denied` + `bod-set-lead-owner-denied` (403) → **RA-08 + BD-04** (BOD revoke/reassign fail-closed).
  - `bod-bulk-delete-denied` (403) → **BD-03**.
- **Caveat actor RA-05 (T1C tự phát hiện khi đọc source probe @ `e0ef02f`):** probe `ci_g007_remaining_gaps_probe.py` chỉ login **4 actor** — `team2.leader@dev.local`, `staff.b@dev.local`, `bod.viewer@dev.local`, `Administrator`. **Không** login `crm.admin@dev.local` (user mang role `CSKH CRM Admin` trong `seed_demo.py`). Vì vậy 2 ca `ra05-*` chứng minh **actor hệ thống** đổi `lead_owner` C→A qua HTTP → 200 và hệ quả bảo mật (chủ mới thấy / chủ cũ 403). User `CSKH CRM Admin` **không** được nâng PASS: `lead_has_permission` chặn `ptype write` cho non-system + `validate_lead_owner` chặn non-`_is_system` ⇒ fail-closed đúng `RA-04 = D` (Owner 2026-10-26), đã đo ở `Test #17`. **Caveat đã ghi trong cả 3 hồ sơ T1C + 1 hàng risk-register.** **[Cập nhật Round 32b:]** T1C đã **đổi trạng thái** ô `RA-05`: **PASS → PARTIAL**, kỳ vọng P0 của CRM-ADMIN = **D tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`); 2 ca `ra05-*` được tách thành **positive control `RA-05-SM`** (actor System Manager). **KHÔNG** mở lại quyết định Owner (`RA-04 = D` giữ nguyên).
- **Cơ chế chặn `write` trên child (đọc source, không suy đoán):** `linked_lead_has_permission` trả `False` cho mọi `ptype` ngoài `("read","select","email","print")` ⇒ `write` trên `FCRM Note`/`CRM Task`/`CRM Call Log` bị chặn với **mọi** non-system, kể cả chủ mới. `lead_has_permission` chặn `("create","write","delete","share","import","export")`. Vì vậy 403 ở `ra10-new-owner-writes-child-fail-closed` là **hành vi theo thiết kế**, `permission_error=true` ⇒ fail-closed đúng loại (không phải 500).
- **RA-10 (ghi chính xác theo yêu cầu T0):** chủ mới `staff.b` ĐỌC Lead A2 → PASS (positive `ra10-new-owner-reads-transferred-lead`); ĐỌC Note con → PASS (positive `ra10-new-owner-reads-child-note`); **GHI Note con → 403 fail-closed** (`ra10-new-owner-writes-child-fail-closed`); chủ cũ `staff.a` bị 403 sau chuyển giao (`ra10-old-owner-denied-after-transfer`). **KHÔNG chấm "được phép ghi child" là PASS** — 403 là kết quả BỊ TỪ CHỐI (fail-closed), không phải xác nhận ghi thành công. Phần "ghi child sau chuyển giao" chưa triển khai thuộc **backlog P1 (ownership service ADR-004 §2.2)**, phù hợp quyết định Owner (fail-closed trong P0).
- **Matrix hiện hành: 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71.** 9 PARTIAL còn lại (LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01) đều là **nghiệp vụ P1 chưa triển khai, đang fail-closed** (BOD scoped reporting ADR-005 / CRM-ADMIN controlled service ADR-004) — **KHÔNG còn gap đọc chéo/ghi trái quyền trong P0**. 3 DEFER P1 (AT-05/RA-06/RA-07) giữ nguyên.
- **Verdict không đổi:** SEC tally **28/28 PASS** (SEC-13 giữ `PASS` giới hạn topology P0); **G0-07 = PASS (T1C KÝ)**; P0 GO/NO-GO thuộc T0/Owner theo đủ 10 gate. KHÔNG merge PR #2.

### Round 32b — hiệu chỉnh actor ô `RA-05` (T1C tự phát hiện khi đọc source)

> **Vấn đề:** ô `RA-05` có Actor = **CRM-ADMIN**, nhưng 2 ca HTTP `ra05-*` run #61 login actor **`Administrator`** (System Manager) ⇒ bằng chứng **không đúng actor của ô**.

- **Chính sách repo áp dụng:** `test_bt1c5_matrix_gaps.py::TestRA04CrmAdminReassignBlocked` (docstring) nói rõ: *"Vì sao KHÔNG viết test 'cho phép': dùng `Administrator` để reassign sẽ **đại diện sai actor** (Administrator ≠ CRM-ADMIN) và tạo **bằng chứng giả**"*. T1C áp dụng đúng quy tắc đó cho `RA-05`.
- **Quyết định:** tách 2 actor —
  1. **Ô `RA-05` (CRM-ADMIN)** → **PARTIAL** (hạ từ PASS). Cột `Kỳ vọng` đổi `A` → **`D tạm thời`** (Owner 2026-10-26, cùng chính sách `RA-04`; cross-team cùng guard `validate_lead_owner` nên cũng bị chặn). Fail-closed **đã chứng minh** bằng 3 method `TestRA04CrmAdminReassignBlocked` + `Test #17` (PASS trong 99/99 run #61) — nhưng đó là **tầng unit/cơ chế**, chưa phải **ca HTTP đo trực tiếp** actor `CSKH CRM Admin` ⇒ **còn 1 ca để đóng ô**.
  2. **2 ca `ra05-*`** → **positive control `RA-05-SM`** (actor System Manager), đặt ở `permission-matrix.md` §7.0.1 — **ngoài lưới 71 ô**, không tính vào tally. Giới hạn: probe dùng `frappe.client.set_value` (đường generic), **không** phải ownership service ADR-004 §2.2 ⇒ không suy rộng thành bằng chứng ownership service đã triển khai.
- **Không có đường mở:** CRM-ADMIN vẫn bị `lead_has_permission` chặn `write` + `validate_lead_owner` chặn non-`_is_system`. **KHÔNG** mở lại quyết định Owner `RA-04 = D`.
- **Tally:** `RA-05` PASS → PARTIAL ⇒ **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**. 9 PARTIAL = LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01 (đều fail-closed, nghiệp vụ P1). SEC tally giữ **28/28 PASS**; **G0-07 = PASS (T1C KÝ)**; P0 GO/NO-GO thuộc T0/Owner.

### Round 32 — đồng bộ bảng §§2–§9 của `permission-matrix.md` (T1C tự phát hiện)

- **Mâu thuẫn phát hiện khi kiểm định:** bảng audit 71 ô trong file này đã là Round 32, nhưng **bảng ma trận §§2–§9 của `permission-matrix.md` còn 19 hàng ghi trạng thái lịch sử Round 28** (`NOT RUN` / `PARTIAL` / `CHƯA XÁC MINH`). Tally hai bảng lệch nhau: §§2–§9 = 44 PASS + 20 PARTIAL + 4 NOT RUN + 3 CHƯA XÁC MINH, trong khi bảng audit = 60 PASS + 8 PARTIAL + 3 DEFER P1.
- **Đã đồng bộ 19 hàng** (không đổi tiêu chí, chỉ sửa ô trạng thái + evidence trỏ đúng run): LEAD-11, LEAD-15, LEAD-16, CL-05, CL-09, TK-04, NT-04, AT-05, AT-06, RA-05, RA-06, RA-07, RA-08, RA-10, LS-04, LS-05, LS-06, BD-03, BD-04.
- **Sau đồng bộ: tally §§2–§9 = bảng audit = 60 PASS + 8 PARTIAL + 3 DEFER P1 = 71**, kiểm bằng script đếm lại từ cả hai bảng (mismatch = 0). **Con số 60/8/3 trong Round 32 là đúng**, nhưng chỉ sau khi sửa 19 hàng stale này — trước đó tài liệu tự mâu thuẫn. *(Đây là con số của **Round 32 — lịch sử**; hiện hành sau **Round 32b**: 59 PASS + 9 PARTIAL + 3 DEFER P1 = 71, vì `RA-05` bị hạ PASS → PARTIAL.)*
- **Lưu ý phạm vi (không nâng ô):** các ô được sửa lên PASS vẫn chỉ PASS **trong tiêu chí đã đo** (actor/fixture/route cụ thể); `AT-05`/`RA-06`/`RA-07` sửa từ `CHƯA XÁC MINH` → `DEFER P1` (không phải PASS) theo quyết định Owner 2026-10-29.

### Round 32 — phân loại 3 nhóm 8 ô PARTIAL còn lại (theo yêu cầu T0)

| Nhóm | Ô | Tiêu chí P0 đã được evidence đáp ứng | Gap đo còn ảnh hưởng P0 | Nghiệp vụ P1 chưa triển khai |
|---|---|---|---|---|
| **(1) P0 đã đáp ứng, chỉ thiếu nhánh *đọc* business** | LEAD-12, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01 (7 ô — BOD) | **Đã đáp ứng.** BOD `frappe.get_list("CRM Lead")` raise (`Test #16`); `data_export`/`reportview_export_query`/`query_report.run` raise cho BOD (`Test #5`); `guarded_reporting._require_system_manager()` chặn mọi non-system → **fail-closed đã chứng minh bằng runtime**, không có đường đọc chéo/trái quyền. | **Không có gap đo ảnh hưởng P0.** Thiếu là *nhánh đọc business* (positive path Team-1-scoped report / BOD scoped reporting) chưa tồn tại để đo — không phải lỗ hổng mở. | BOD scoped reporting theo ADR-005 (`guarded_reporting` chỉ cho System Manager ⇒ BOD chưa có API riêng). **DEFER P1**; mở lại khi service scoped có test âm/dương. |
| | LEAD-13 (CRM-ADMIN) | **Đã đáp ứng.** `Test #17`: CRM-ADMIN không có `System Manager`, `has_permission("CRM Lead","read"/"write") == False` → fail-closed. | **Không có gap đo ảnh hưởng P0.** | CRM-ADMIN controlled ownership/list service (ADR-004 §2.2). **DEFER P1**. |
| **(2) Gap đo còn ảnh hưởng P0** | *(không có ô nào)* | — | **Rỗng.** Sau khi đồng bộ 19 hàng, **không còn ô nào có tiêu chí P0 chưa được evidence đáp ứng** mà vẫn để trạng thái PARTIAL/NOT RUN. | — |
| **(3) Nghiệp vụ P1 chưa triển khai, đang fail-closed** | 8 PARTIAL + 3 DEFER P1 (AT-05, RA-06, RA-07) *(tally của Round 32 — lịch sử; hiện hành sau Round 32b: **9 PARTIAL** = 8 ô trên + `RA-05`)* | **Đã đáp ứng ở phía P0** (fail-closed đã chứng minh). | **Không có gap đo ảnh hưởng P0.** | 8 ô PARTIAL (BOD scoped reporting ADR-005 + CRM-ADMIN controlled service ADR-004) + 3 DEFER P1 (AT-05 BOD file scope ADR-005; RA-06/RA-07 TL1 reassignment ownership service ADR-004 §2.2). **Tất cả fail-closed, KHÔNG chấm PASS.** |

- **Kết luận về G0-07 (P0 §7.5):** cả 3 tiêu chí thoát đều đạt — (a) không còn Critical/High cross-access **đã chứng minh** trên bề mặt API P0; (b) có test âm chạy thật với **≥2 user** khác nhau (staff.a / staff.b / staff.c / team2.leader / bod.viewer / Administrator — 6 actor); (c) role bị ảnh hưởng **không truy cập được** và điều này chứng minh bằng negative test (403 `PermissionError` / `DoesNotExistError`).
- **Giữ nguyên các quyết định Owner (không mở lại):** Q1–Q4 `DECIDED` (registry verbatim `test_docshare_lifecycle.py` @ `9aec763`, `OWNER_DECISIONS_CONFIRMED = True`); RA-04 = `D`/fail-closed trong P0; SEC-13 `PASS` theo topology P0 (direct `bench serve` — **mở lại nếu thêm reverse-proxy/CORS**); AT-05/RA-06/RA-07 `DEFER P1`. **Không có bằng chứng mâu thuẫn mới** để mở lại bất kỳ quyết định nào.

## Round 31 — lịch sử (2026-10-30)

> **NGUỒN PHỤC HỒI (báo cáo theo yêu cầu T0):** bản Round 30 (2026-10-28) được phục hồi **byte-identical** từ snapshot `snapshot cuc bo ngoai repo (`t1b-pr2-review-e67`)` — `permission-matrix.md` 63 070 B (SHA-256 `39405C1E…`), `risk-register.md` 36 503 B (SHA-256 `AE7F8DA7…`), file này 60 217 B (SHA-256 `4C7DF92F…`) — lịch sử Round 1–30 bảo toàn (phần thân cũ 2026-10-19 giữ nguyên làm lịch sử). Round 31 áp evidence độc lập [`t1c-ext1-verification-2026-10-30.md`](./t1c-ext1-verification-2026-10-30.md) (kiểm định T1B-ext+1, run #59). KHÔNG stage/commit/push/merge.

### Round 31 — kiểm định độc lập T1B-ext+1 (Dynamic Link fail-closed + `get_activities` override)

- **Run mới nhất (tự xác minh GitHub API trước khi sửa):** `#41037431038` (run #59), head `b1087bc`, merge `682a9d93` (= merge `b1087bc` vào `b382fe4` — parents đã kiểm), SUCCESS. Artifact `12968528845` (139 781 B), digest API `sha256:c6b298a8…` — T1C tự tải + tự hash KHỚP. Diff `afa74b3…b1087bc` = 1 commit docs-only (`t1b-ext1-dynamic-link-fail-closed.md` +150 dòng).
- **Tự parse 5 JUnit:** 99/99 PASS (18+12+32+27+10), 0 FAIL/ERROR/SKIP, gate `gate_passed=true`, mọi list fail/error/skip/missing/parse rỗng; auxiliary `test_error_response_normalization` 8/8.
- **HTTP 18/18 PASS** (`coverage_scope` không đổi: direct `bench serve`, không phủ proxy/CORS); **file-oracle 9/9 PASS**; **remaining-gaps 62/62 PASS** (`probe_result=PASS`, `failed_cases=[]`).
- **Dynamic Link âm/dương:** `dl01-linked-calls-in-scope-actor` `leaked_marker_count=0`; `dl01-linked-calls-positive-control` `positive_call_visible=true` (2 call); `dl01-activities-in-scope-actor` `leaked_marker_count=0`. Source `b1087bc` xác nhận override `crm.api.activities.get_activities` (hooks L196) + wrapper `get_linked_calls`/`get_linked_notes`/`get_linked_tasks` + `_filter_linked_calls_for_scope`.
- **SEC-26 → PASS — đủ 4 tiêu chí Round 30:** (a) reflection File ID đã xử lý — file-oracle `client.get`/REST File out-of-scope trả **404 `DoesNotExistError`**, `echoes_supplied_file_id=false`, `contains_file_url=false`, existing-vs-unknown **indistinguishable** (không lộ cả sự tồn tại — mạnh hơn 403 về privacy); `getdoc` out-of-scope 403 `PermissionError` với `identifier_echo=false`, `exception_sanitized=true`; download out-of-scope/guest 403 (at06/private-download); (b) regression CI bền vững — `file_oracle_unit`/`file_oracle_http`/`remaining_gaps_http` là step workflow trong run #59 (đều success); (c) custom CRM redaction giữ — `sec26-staff-private` redact, `sec26-admin-private` retain, `get_activities` 4 leak site `file_url_absent`/`value_sha256=null`; (d) T1C độc lập xác minh — đã thực hiện.
- **SEC-16 → PASS:** 12 permlevel surfaces (in-scope + out-of-scope) `sentinel_present=false` cho non-system + **3 system positive control** `sentinel_present=true` (assertion không vacuous) + `sec16-field-cleanup`. `guarded_reporting` (BOD reporting) vẫn thuộc P1 (AT-05/BD-01), fail-closed trong P0.
- **SEC tally → 28/28 PASS** (SEC-13 giữ `PASS` theo P0 §7.5, giới hạn topology P0 — mở lại khi thêm reverse-proxy/CORS; SEC-16/26 → PASS).
- **Matrix → 52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (8 ô nâng: LEAD-15/CL-09/AT-06/LS-05 từ NOT RUN; TK-04/NT-04/LS-04/CL-05 từ PARTIAL — chi tiết từng ô trong bảng audit bên dưới).
- **G0-07 = PASS (T1C KÝ)** — dựa trên runtime độc lập (99/99 + 18/18 + 9/9 + 62/62 + Dynamic Link âm/dương), KHÔNG chỉ từ CI xanh. KHÔNG merge PR #2; GO/NO-GO P0 thuộc T0/Owner sau khi tổng hợp các gate.

## Round 30 — lịch sử (2026-10-28)

> Round 30 sửa cách ánh xạ SEC-13 theo đúng tiêu chí P0 §7.5 sau khi T1C tải và parse artifact CI #40514019123. Bổ sung các phép đo T1B Round 29 (echo controls, Deal/content_hash, Vite CORS, H-RFC-1(a)) nhưng không nới assertion, không nâng SEC-26 hoặc các ô matrix. **Trạng thái hiện hành là Round 31 ở trên; phần còn lại của section này là lịch sử.**

### Round 30 — SEC-13 và evidence bổ sung T1B

- **SEC-13 PASS theo đúng tiêu chí P0 §7.5:** Guest gọi endpoint đã xác thực phải bị từ chối. T1C tải artifact CI `12738607035` của run [#40514019123](https://github.com/nobita6986/new-crm/actions/runs/40514019123), digest API `sha256:31c707ad804895d9d71d57dd4c0fb7483722d19d9151a2c6723cf68bbd42b146`, head `b92f668`; case `sec13-guest` gọi `/api/method/crm_cskh.api.health.ping`, trả HTTP `403`, `PermissionError`, assertion `PASS`, không có transport error. Đây khớp định nghĩa duy nhất của SEC-13 trong P0 §7.5 và permission-matrix §11.3.
- Đo CORS trên Vite dev server `:8080` là **bằng chứng bổ sung**, không phải tiêu chí SEC-13 trong P0. Artifact CI direct-to-`bench serve` có ghi rõ không phủ proxy/CORS; không suy rộng phép đo Vite thành kiểm thử production ingress/TLS.
- T1C đã đối chiếu độc lập SHA-256 của tám artifact trong `t1b-followup-5-items-2026-10-28.md`; tất cả khớp bảng manifest. Control A–E và cross-check cho hai lỗi `frappe.client.get(File)`/REST File cho thấy identifier caller gửi xuất hiện trong chính hai response 403 của probe (6/6 body-hash pairs giống nhau), nhưng Owner không chấp nhận phản chiếu identifier. Hai case tiếp tục **FAIL** theo tiêu chí hiện hành; không gọi là false positive và không kết luận IDOR.
- Bổ sung T1B ghi nhận nhánh Deal (in-scope 200/out-of-scope 403), `content_hash` không được upstream chọn trong payload Lead/Deal và wrapper redaction có positive control. Đây chỉ là coverage cho các fixture/route đã đo.
- T1B ghi 65 static guards chạy local 65/65 và thêm step vào working-tree workflow; chưa có CI artifact chạy revision workflow đó, nên chưa xem là CI gate đã thực thi. H-RFC-1(a) đo ba file mẫu có URL ổn định/không có marker ký số hoặc hạn dùng; download anonymous bị 403. Không suy rộng ra mọi URL.
- **SEC tally hiện hành: 26 PASS + 2 PARTIAL (SEC-16/SEC-26) = 28/28.** SEC-13 đã đạt tiêu chí Guest-auth; CORS là phạm vi bổ sung. SEC-26 còn hai lỗi phản chiếu identifier và yêu cầu CI regression + T1C review; SEC-16 còn thiếu độ phủ field/report. **G0-07 PARTIAL / CHƯA KÝ; P0 NO-GO**; matrix Round 28 giữ nguyên.

### Provenance và các lớp runtime

- **CI gần nhất được T1C xác minh:** PR #2 run [#40514019123](https://github.com/nobita6986/new-crm/actions/runs/40514019123), head `b92f668dc6602df0c99983488e3c1240f31270aa`, merge `d299812d5e5a65bfb0e53a2342347f8f83650a`, artifact `12738607035`, SHA-256 `31c707ad804895d9d71d57dd4c0fb7483722d19d9151a2c6723cf68bbd42b146`: **99/99 PASS**, HTTP probe **18/18 PASS**. Probe này không gọi năm route Frappe chuẩn; không dùng nó làm bằng chứng cho các route dưới đây.
- **Suite DEV cục bộ do T1B chạy trên site sạch riêng** `t1b-g007-test-20261028-b92f668.localhost`, working tree tại HEAD `b92f668` nhưng có thay đổi chưa commit: 5 JUnit XML / gate / AST đối chiếu **99/99 PASS**, 0 FAIL/ERROR/SKIP/NOT RUN; gate JSON SHA-256 `80c9b6cc928d1c827b7d64591468911d93dc29be94002594716f20381b5cea07`. Chi tiết module/hashes ở `t1b-sec26-standard-file-routes-runtime-2026-10-28.md`. Đây là runtime local, không phải CI run.
- **HTTP DEV probe T1B** tại site riêng `t1b-sec26-probe-20261028-b92f668.localhost`, thời điểm `2026-10-28T03:46:50.806694Z` (10:46:50 ICT), source tree dirty: artifact `http-transport.json`, 24,503 byte, SHA-256 **`33af91d6af4f7d2394ba7009a1fd32564116b76228eafe6046fbbc6edbb345f2`**, `commit_sha=b92f668dc6602df0c99983488e3c1240f31270aa`, Frappe pin `d2efa79657ba5c1abf96098b3bfacc199517d57e`, CRM pin `0d3910b456ef4fb913e77b29db011b3debc4f778`. T1C đọc trực tiếp artifact trong container, tự xác minh kích thước/hash và parse 30 case: **28 PASS / 2 FAIL**, `failed_cases` đúng hai route File bên dưới. Phạm vi: direct HTTP `bench serve`; reverse-proxy/CORS không được đo.
- **Privacy audit T1C trên artifact:** không thấy URL `/private/files/` hoặc `/files/`, tên file seed, cookie/CSRF header, password, raw File ID, response body/headers; artifact chỉ lưu nhãn route/case, status, hash/độ dài, booleans và kết quả assertion. Hai FAIL chỉ được lưu qua boolean `out_of_scope_seed_identifier_present=true`, không lưu ID thực hoặc body thô.

### Owner policy — chuẩn Frappe so với endpoint CRM tùy biến

Owner quyết định ngày **2026-10-28**: trên route Desk/File Frappe chuẩn, caller trong scope được nhận `file_url` nếu kiểm tra quyền File/parent thông thường cho phép; quyền tải nội dung được kiểm riêng ở download. Quyết định này không coi URL là token, không chấp nhận phản chiếu File ID trong lỗi 403, không mở rộng ra ngoài route/actor/fixture đã đo và **không** nới contract redaction của endpoint CRM tùy biến. Với endpoint `crm_cskh`/CRM-owned như `get_activities`/`get_attachments`, private URL vẫn phải được redacted.

### SEC-26 — kết quả hiện hành và giới hạn

**Năm route Frappe chuẩn được đo trong artifact T1B (DEV runtime):**

| Route family | Staff A trong scope | Staff C ngoài scope | Private URL | File ID trong response ngoài scope | Đánh giá |
|---|---:|---:|---|---|---|
| `frappe.desk.form.load.getdoc` | 200 | 403 | Có trong response trong scope; vắng ngoài scope | Không | Đạt phép đo fixture theo policy |
| `frappe.desk.form.load.get_docinfo` | 200 | 403 | Có; vắng ngoài scope | Không | Đạt phép đo fixture theo policy |
| `frappe.desk.form.load.get_filtered_attachments` | 200 | 403 | Có; vắng ngoài scope | Không | Đạt phép đo fixture theo policy |
| `frappe.client.get(File)` | 200 | 403 | Có; vắng ngoài scope | **Có — assertion FAIL** | Còn lỗi phản chiếu identifier |
| REST `GET /api/resource/File/<id>` | 200 | 403 | Có; vắng ngoài scope | **Có — assertion FAIL** | Còn lỗi phản chiếu identifier |

**Download tách biệt khỏi việc URL có trong response:** caller trong scope tải file seed thành công (`200`, 90 byte, marker đúng); caller ngoài scope bị `403` có và không có `fid`, không nhận marker. Đây là dữ liệu giả trên các fixture/actor/site cụ thể, không phải chứng minh toàn bộ download path an toàn.

**Ý nghĩa hai FAIL:** cả `frappe.client.get(File)` và REST File trả 403, không có private URL, nhưng response phản chiếu identifier File do request cung cấp (`out_of_scope_seed_identifier_present=true`). Artifact không lưu body hoặc ID thô. Đây là lỗi assertion privacy/metadata cần xử lý; phép đo hiện có **không** chứng minh vượt quyền, đọc file, hay IDOR. Quyết định Owner về URL visibility trong scope không miễn trừ hai lỗi này.

**Endpoint CRM tùy biến:** CI #40514019123 ghi nhận các fixture `sec26-staff-private`/`sec26-activities-in-scope` không trả private URL ở các payload đã đo, gồm bốn vị trí lịch sử `get_activities`: `message[4].attachments[].file_url`, `message[0][attachment_log].data.file_url`, `docinfo.attachments[].file_url`, `docinfo.attachment_logs[].content` (HTML href). Caller ngoài scope ở fixture `get_activities` bị 403. Kết quả này giữ contract redaction cho các endpoint đã đo; không suy rộng sang mọi endpoint hoặc biến 18/18 CI transport thành kiểm tra năm route chuẩn.

**SEC-26 vẫn PARTIAL (lịch sử Round 30).** Đóng khi (a) T1B loại bỏ phản chiếu File ID ở hai route lỗi nhưng vẫn giữ 403, không private URL/body/ID nhạy cảm cho actor ngoài scope; (b) có regression CI bền vững ghi riêng năm route, actor trong/ngoài scope và direct download có/không `fid`; (c) custom CRM redaction vẫn được kiểm tra; (d) T1C độc lập xác minh provenance, privacy, actor/case và kết quả artifact sau remediation. Không gọi URL visibility trong scope là lỗ hổng sau quyết định Owner, và cũng không gọi cả hệ thống an toàn từ phép đo giới hạn này. **Round 31 (2026-10-30) — SEC-26 ĐÃ ĐÓNG → PASS:** đủ 4 tiêu chí (a)-(d) — file-oracle 9/9 (404 indistinguishable không echo + 403 sanitized + download 403), CI regression durable (`file_oracle_*`/`remaining_gaps_http` steps trong run #59), custom redaction giữ, T1C xác minh độc lập. Xem section Round 31 đầu file và [`t1c-ext1-verification-2026-10-30.md`](./t1c-ext1-verification-2026-10-30.md).

### SEC tally, matrix và gate

- **Round 32b HIỆN HÀNH (2026-10-31): SEC tally 28/28 PASS** (SEC-13 PASS theo P0 §7.5 giới hạn topology P0; SEC-16/26 PASS — xem section Round 32/31 đầu file). **Matrix 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (Round 32b hạ `RA-05` PASS → PARTIAL vì evidence đo sai actor — xem mục Round 32b bên dưới). **G0-07 = PASS (T1C KÝ); P0 GO/NO-GO thuộc T0/Owner.**
- Lịch sử Round 31: SEC tally 28/28 PASS; matrix **52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**.
- Lịch sử Round 30: SEC tally hiện hành là **26 PASS + 2 PARTIAL (SEC-16/SEC-26) = 28/28**; SEC-13 theo đúng P0 §7.5 PASS tại CI #40514019123, SEC-14 PASS. Đây là tally security requirement, khác với 99 test methods.
- Lịch sử Round 28: audit 71 ô **44 PASS + 20 PARTIAL + 4 NOT RUN = 68 mapped; 3 CHƯA XÁC MINH**. Artifact mới không tự đóng các ô matrix; không thay đổi trạng thái ô trong Round 29.
- Lịch sử Round 30: **SEC-26 PARTIAL; G0-07 PARTIAL / CHƯA KÝ; P0 NO-GO.** Còn SEC-16 criterion/coverage, hai assertion lỗi identifier và CI regression/review cho SEC-26; 27 ô matrix chưa PASS. SEC-13 Guest-auth đã PASS theo P0 §7.5; đo CORS trên Vite dev server là supplemental, không phải criterion P0 này.

### Kiểm toán đủ 71 ô permission matrix — Round 28

Đối chiếu từng hàng §1–§9 của `permission-matrix.md` theo actor, tiêu chí và evidence riêng. CI #40514019123 xác nhận 99/99 method PASS; điều này chỉ chứng minh assertion/fixture mà mỗi test gọi, không nâng ô thiếu nhánh hoặc policy. Kết quả Round 28: **44 PASS + 20 PARTIAL + 4 NOT RUN = 68 ô mapped; 3 CHƯA XÁC MINH = 71 ô**. **Round 31 (2026-10-30): 8 ô nâng → 52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71. Round 32 (2026-10-31): thêm 8 ô nâng (LEAD-11/16, LS-06, RA-05/08/10, BD-03/04) → 60 PASS + 8 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71. **Round 32b (2026-10-31): hạ `RA-05` PASS → PARTIAL (evidence đo actor `Administrator` ≠ CRM-ADMIN của ô) → hiện hành 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71.**

| Ô | Trạng thái | Evidence đã có | Gap còn lại | Owner hành động |
|---|---|---|---|---|
| `LEAD-01` | **PASS** | `Test #1` (`test_lead_list_and_get_data_only_return_owner_scope`, `test_permissions.py` L206) | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-02` | **PASS** | `Test #1`: `assertRaises(frappe.PermissionError)` khi gọi `client_get("CRM Lead", self.leads["b"])`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-03` | **PASS** | cùng assert như LEAD-02; hook `permissions.lead_has_permission` deny vì không phải owner scope. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-04` | **PASS** | `Test #1`: `set == {self.leads["a"]}`; `p0-isolated-verification.md` & 3 log 26/27/28. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-05` | **PASS** | `Test #1`: `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-06` | **PASS** | `Test #10` (`test_owner_change_and_assignment_bypass_are_denied`, L423): `lead.save(ignore_permissions=True)` raise; CLI ghi qua `frappe.client.set_value` cũng raise `Test #6` (L356). | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-07` | **PASS** | `TestLEAD07UnassignedLead` (3/3 methods) in `test_bt1c5_matrix_gaps.py`, CI run #40514019123: precondition, list, and detail checks for U1/unassigned Lead all pass. Bounded to this criterion. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-08` | **PASS** | `Test #5` (`test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers`, L319): `data_export` raise cho `A`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-09` | **PASS** | `Test #7` (`test_team_leader_sees_team_but_not_other_team`, L388): `visible` chứa `a` và `b`; assertion detail own OK. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-10` | **PASS** | `Test #7`: `assertNotIn(self.leads["c"], visible)`; `get_doc(...).check_permission("read")` raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-11` | **PASS** | Round 32 `lead11-team2-leader-list-scope` (positive: TL2 thấy Lead team 2) + `lead11-team2-leader-direct-read-denied` (403: TL2 đọc trực tiếp Lead team 1 bị chặn) — remaining-gaps 74/74 run #61. | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-12` | **PARTIAL** | **PARTIAL — BOD fail-closed cho CRM Lead list đã verify** (`Test #16`, `test_bod_viewer_cannot_read_leads_or_manage_users`, L528); **scope đọc báo cáo chưa build**. | BOD Lead list fail-closed đã test; scoped BOD reporting chưa build/chứng minh. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `LEAD-13` | **PARTIAL** | **PARTIAL — fail-closed** (`Test #17`, `test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537): `assertNotIn("System Manager", ...)` + `assertFalse(has_permission("CRM Lead", "read"))`. | CRM-ADMIN còn fail-closed; controlled ownership/list service chưa build. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `LEAD-14` | **PASS** | `Test #10`: hook `validate_lead_owner` raise khi đổi `lead_owner`; fail-closed cho đến khi ownership service sẵn sàng. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-15` | **PASS** | Round 31 `cross-doctype-get-data-vs-get-list-*` (6 doctype: CRM Deal/ToDo/CRM Call Log/File/User/Custom Field) trong remaining-gaps 62/62 — cùng permission boundary và visible name set như `frappe.client.get_list`. | Không còn gap trong tiêu chí đã đo; không suy rộng sang doctype khác ngoài 6 fixture. | T1C (review evidence) + T1B (duy trì regression) |
| `LEAD-16` | **PASS** | Round 32 `lead16-ls06-get-data-field-scope`: `get_data rows=[mobile_no,email]` trả field in-scope, `leaked_marker_count=0` (field Lead ngoài scope không lộ) — remaining-gaps 74/74 run #61. | Không còn gap trong tiêu chí đã đo; không suy rộng sang field khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-01` | **PASS** | `Test #6` (L356) cuối đoạn: `get_call_log` own OK; call của cross raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-02` | **PASS** | `Test #6`: `assertRaises(PermissionError)` cho `get_call_log(self.call_b)`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-03` | **PASS** | cùng assert cơ chế hook `crm_cskh.permissions.linked_lead_has_permission`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-04` | **PASS** | `Test #3` (L261): `("CRM Call Log", "reference_docname", {self.call_a})` — set ≤ allowed; **Test #9** (L403): `guarded_crm.get_linked_calls(self.leads["b"])` raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-05` | **PASS** | Round 31 `dl01-linked-calls-in-scope-actor` (`leaked_marker_count=0` — không lộ contact/phone/note/task/recording URL/link_name ngoài scope) + `dl01-linked-calls-positive-control` (`positive_call_visible=true`) trong remaining-gaps 62/62. | Không còn gap trong tiêu chí đã đo; không suy rộng sang fixture khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-06` | **PASS** | `TestCL0607TeamLeaderCallLog` (3/3 methods), CI #40514019123: TL1 Team 1 Call Log list/detail/endpoint checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-07` | **PASS** | `TestCL0607TeamLeaderCallLog` (3/3 methods), CI #40514019123: TL1 cross-team list/detail/endpoint denial checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `CL-08` | **PARTIAL** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | BOD fail-closed đã đo; scoped BOD Call Log read chưa build. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `CL-09` | **PASS** | Round 31 `cl09-get-call-log-guest` 403 / `cl09-get-call-log-out-of-scope` 403 / `cl09-get-call-log-in-scope` 200 (remaining-gaps 62/62). | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-01` | **PASS** | `Test #6` (L356) list (Call/Note/Task/File own) check_permission ok. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-02` | **PASS** | `Test #6`: `("CRM Task", self.task_b)` raise `PermissionError`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-03` | **PASS** | `Test #6`: `set_value("CRM Task", self.task_b, "title", ...)` raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-04` | **PASS** | Round 31 `tk04-read-out-of-scope-target` 403 + `tk04-create-task-out-of-scope` 403 `PermissionError`, không tạo ToDo (`matching_rows_before/after_insert/after_cleanup = 0`, `cleanup_verified=true`) — remaining-gaps 62/62. | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-05` | **PASS** | `TestTK0506TeamLeaderTask` (3/3 methods), CI #40514019123: TL1 Team 1 Task list/detail checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-06` | **PASS** | `TestTK0506TeamLeaderTask` (3/3 methods), CI #40514019123: TL1 cross-team Task denial checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `TK-07` | **PARTIAL** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | BOD fail-closed đã đo; scoped BOD Task read chưa build. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `NT-01` | **PASS** | `Test #6` (L356) `("FCRM Note", self.note_a)`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `NT-02` | **PASS** | `Test #6`: `("FCRM Note", self.note_b)` raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `NT-03` | **PASS** | `Test #6`: `("Comment", self.comment_b)` raise; `Test #3`: comment list trả rỗng (DocType-level block). | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `NT-04` | **PASS** | Round 31 `nt04-create-fcrm-note-out-of-scope` 403 + `nt04-create-comment-out-of-scope` 403 `PermissionError`, không tạo linked record (`matching_rows_before_cleanup=0`, `cleanup_verified=true`) — remaining-gaps 62/62. | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `NT-05` | **PASS** | `TestNT0506TeamLeaderNote` (3/3 methods), CI #40514019123: TL1 Team 1 Note read checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `NT-06` | **PASS** | `TestNT0506TeamLeaderNote` (3/3 methods), CI #40514019123: TL1 cross-team Note denial checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `NT-07` | **PARTIAL** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | BOD fail-closed đã đo; scoped BOD Note read chưa build. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `AT-01` | **PASS** | `Test #6` cho File own qua `check_permission`; `Test #4`: sau reassign, new owner `B` (`Test #4` cuối) `find_file_by_url(URL) is not None`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `AT-02` | **PASS** | `Test #4`: sau reassign, `a` (`frappe.set_user(self.users["a"])`) `find_file_by_url(...) is None`; cơ chế `clear_stale_lead_shares` DocShare gỡ + DocType-level permission file. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `AT-03` | **PASS** | `Test #4` cơ chế hook `linked_lead_has_permission` deny. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `AT-04` | **PASS** | CI #40514019123 `sec26-staff-private` and `sec26-activities-in-scope` show guarded custom endpoints redact private `file_url` for the tested Staff fixture; four historical `get_activities` URL sites are absent in that response. `Test #9` also denies cross-scope guarded attachment access. Standard Frappe Desk/File URL visibility follows Owner policy; Round 29 artifact has three route families passing the measured out-of-scope criteria and two 403 responses reflecting the request File ID (RISK-SEC-10). | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `AT-05` | **DEFER P1** | BOD chưa có scope đọc; chỉ fail-closed đã verify (`Test #16`). | BOD report/file scope chưa build (ADR-005); DEFER P1 theo Owner 2026-10-29 — KHÔNG chấm PASS. | T0/Owner (policy khi cần) + T1B (service/test) + T1C (review) |
| `AT-06` | **PASS** | Round 31 `at06-private-download-in-scope` 200 / `-out-of-scope` 403 / `-guest` 403 / `-out-of-scope-unknown` 403 (remaining-gaps 62/62). | Không còn gap trong tiêu chí đã đo; không suy rộng sang mọi download path. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-01` | **PASS** | `Test #10` (L423): `lead.lead_owner = self.users["c"]` + `lead.save(ignore_permissions=True)` raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-02` | **PASS** | `Test #14` (L490): tạo `ToDo` với `allocated_to=self.users["c"]` + `ignore_permissions=True` raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-03` | **PASS** | `Test #10`: `remove_assignments(...ignore_permissions=True)` raise; hook `validate_lead_assignment` deny. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-04` | **PASS** | Owner quyết định 2026-10-26: CRM-ADMIN bị fail-closed tới khi ownership service ADR-004 §2.2 tồn tại. Ba method `TestRA04CrmAdminReassignBlocked` đều PASS trong CI #40514019123. Không chứng minh ownership service đã triển khai; method mang tên lịch sử “pending owner decision” không còn phản ánh quyết định hiện hành. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-05` | **PARTIAL** | **Fail-closed đã chứng minh, chưa có ca HTTP đo trực tiếp actor CRM-ADMIN.** 3 method `TestRA04CrmAdminReassignBlocked` + `Test #17` đều PASS trong 99/99 run #61: CRM-ADMIN không `System Manager` ⇒ `_is_system == False` ⇒ `validate_lead_owner` chặn đổi `lead_owner` + `lead_has_permission` chặn `write`; owner giữ nguyên. Kỳ vọng P0 của CRM-ADMIN = **D tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`). | **Round 32b (T1C tự phát hiện khi đọc source):** 2 ca HTTP `ra05-*` run #61 đo actor `Administrator` (System Manager) ⇒ **không phải evidence của ô CRM-ADMIN**; đã tách sang positive control `RA-05-SM`. Chính sách repo (`TestRA04CrmAdminReassignBlocked` docstring) cấm dùng `Administrator` làm bằng chứng cho ô CRM-ADMIN: *"dùng `Administrator` để reassign sẽ **đại diện sai actor** (Administrator ≠ CRM-ADMIN) và tạo **bằng chứng giả**"*. **Còn thiếu 1 ca:** HTTP đo `crm.admin@dev.local` trên C1→A (kỳ vọng 403) để đóng ô. | T1C (review evidence) + T1B (bổ sung ca đo actor CRM-ADMIN) |
| `RA-06` | **DEFER P1** | chỉ có admin trong tests. Hook `validate_lead_owner` block TL1 (vì TL1 không phải `_is_system`). | TL1 in-team reassignment cần ownership service ADR-004 §2.2 (chưa xây); DEFER P1 theo Owner 2026-10-29 — KHÔNG chấm PASS. | T0/Owner (policy khi cần) + T1B (service/test) + T1C (review) |
| `RA-07` | **DEFER P1** | không có test case TL1 reassign. | TL1 cross-team reassignment cần ownership service ADR-004 §2.2 (chưa xây); DEFER P1 theo Owner 2026-10-29 — KHÔNG chấm PASS. | T0/Owner (policy khi cần) + T1B (service/test) + T1C (review) |
| `RA-08` | **PASS** | Round 32 `bod-remove-assignments-denied` (403) + `bod-set-lead-owner-denied` (403): BOD bị chặn revoke/reassign — remaining-gaps 74/74 run #61. | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-09` | **PASS** | `Test #12` (L450): `assertNotIn(lead_a, get_list(self.users["a"]))`; `.check_permission` raise cho Lead + child; `Test #4` (L285): tương tự + linked endpoints raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `RA-10` | **PASS (đọc) + P1 (ghi fail-closed)** | Round 32 `ra10-new-owner-reads-transferred-lead` (chủ mới đọc Lead A2 → PASS), `ra10-new-owner-reads-child-note` (chủ mới đọc Note con → PASS), `ra10-old-owner-denied-after-transfer` (chủ cũ 403 → PASS), `ra10-new-owner-writes-child-fail-closed` (chủ mới **GHI Note con → 403 fail-closed**). **KHÔNG chấm "được phép ghi child" là PASS** — 403 là kết quả bị từ chối. Phần "ghi child sau chuyển giao" thuộc **backlog P1 (ownership service ADR-004 §2.2)**. | Đọc Lead/Note sau chuyển giao đã đo (positive + negative); ghi child fail-closed, chưa triển khai (P1). | T1C (review evidence) + T0/Owner (P1) + T1B (duy trì regression) |
| `LS-01` | **PASS** | `Test #1` (L206): `client_get_list("CRM Lead", ...)` chỉ trả own. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-02` | **PASS** | `TestLS02GlobalSearchScope` (3/3 methods), CI #40514019123: global search/search-link scope and bypass checks pass. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-03` | **PASS** | `Test #5` (L319): `data_export` + `reportview_export_query` raise cho A. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-04` | **PASS** | Round 31 `ls04-search-link-in-scope-positive` (result_count=1, target present) + `ls04-search-link-out-of-scope` (result_count=0) — remaining-gaps 62/62. | Không còn gap trong tiêu chí đã đo; không suy rộng sang lookup surface khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-05` | **PASS** | Round 31 `cross-doctype-get-data-vs-get-list-*` (6 doctype) — cùng permission boundary và visible name set như `frappe.client.get_list`. | Không còn gap trong tiêu chí đã đo; không suy rộng sang doctype khác ngoài fixture. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-06` | **PASS** | Round 32 `lead16-ls06-get-data-field-scope`: field non-permlevel nhạy cảm (`mobile_no`/`email`) ngoài scope không lộ (`leaked_marker_count=0`) — remaining-gaps 74/74 run #61. | Không còn gap trong tiêu chí đã đo; không suy rộng sang field khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-07` | **PASS** | `Test #11` (L437): `delete_bulk_docs` cross-scope raise. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-08` | **PARTIAL** | `Test #5` in CI #40514019123 denies TL1 report execution generally; it does not prove the expected positive Team 1 scoped report path. No scoped Team 1 report is built/tested. | Report denial chung đã test; positive Team 1 scoped report chưa build/test. | T1B (bổ sung nhánh/test) + T1C (review) |
| `LS-09` | **PASS** | **PASS — same as LS-08:** TL1 chặn toàn bộ report, scope cụ thể Team 2 chưa build. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `LS-10` | **PARTIAL** | **PARTIAL — `Test #5`** BOD chặn export/report (fail-closed); **scope đọc báo cáo BOD chưa build**. | BOD export/report fail-closed; company-wide read-only report scope chưa build. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `BD-01` | **PARTIAL** | **PARTIAL — fail-closed verify** (`Test #16`, `test_bod_viewer_cannot_read_leads_or_manage_users`, L528): BOD `frappe.get_list("CRM Lead", pluck="name")` raise; **nhánh đọc báo cáo scoped (report API read-only) chưa build**. | BOD Lead list fail-closed đã test; read-only reporting API chưa build. | T0/Owner (policy/scope) + T1B (build/test) + T1C (review) |
| `BD-02` | **PASS** | **PASS — `Test #16`**: `has_permission("User", "create") == False` + `has_permission("Role", "create") == False` cho BOD; report API call `data_export` raise (`Test #5`). | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `BD-03` | **PASS** | Round 32 `bod-bulk-delete-denied` (403 `PermissionError`): BOD bị chặn bulk delete Lead — remaining-gaps 74/74 run #61. | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `BD-04` | **PASS** | Round 32 `bod-remove-assignments-denied` (403) + `bod-set-lead-owner-denied` (403): BOD bị chặn reassignment/revocation — remaining-gaps 74/74 run #61. | Không còn gap trong tiêu chí đã đo; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `BD-05` | **PASS** | **PASS — `Test #16`**: `assertNotIn("System Manager", frappe.get_roles(self.users["bod"]))`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |
| `BD-06` | **PASS** | **PASS — `Test #17`** (`test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537): CRM-ADMIN `assertNotIn("System Manager")` + `assertFalse(has_permission("CRM Lead", "read"))`. | Không còn gap trong tiêu chí hẹp đã test; không suy rộng sang route/actor khác. | T1C (review evidence) + T1B (duy trì regression) |

**Diễn giải:** `mapped=68` (Round 28) gồm PASS/PARTIAL/NOT RUN; **Round 32b hiện hành là 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**. Đây là trạng thái matrix, tách khỏi SEC tally (28/28 PASS). **9 PARTIAL hiện hành (LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01) đều là nghiệp vụ P1 chưa triển khai, đang fail-closed** — tiêu chí P0 (không đọc chéo/ghi trái quyền) đã được evidence fail-closed đáp ứng; KHÔNG còn gap đo ảnh hưởng P0. 3 DEFER P1 (AT-05/RA-06/RA-07) cần ownership service ADR-004 §2.2 / BOD scope ADR-005 ở P1.

> **Phạm vi lịch sử:** các mục §§1–9 bên dưới mô tả vòng rà 2026-10-19 và giữ nguyên để truy vết. Nếu chúng nói thiếu Docker, 18 method, 26/27/28 test, SEC-13/14/16/26 NOT RUN hoặc Q1–Q4/RA-04 đang chờ Owner thì đó là trạng thái lịch sử, đã được cập nhật ở snapshot Round 27.

### Trạng thái probe CI mới cho năm route chuẩn (Round 27)

Bản working tree của T1B đã bổ sung probe trong `deploy/dev/seed/ci_http_transport_probe.py` và kiểm tra cấu trúc trong `deploy/dev/seed/test_ci_http_probe_static.py`: dự kiến gọi đủ năm route với actor trong/ngoài scope, yêu cầu route ngoài scope trả 403 không lộ URL/ID, và thử direct download ngoài scope có/không `fid` nếu thu được URL. Đây hiện chỉ là **code trong working tree, chưa chạy runtime** vì Docker đang down; chưa có CI artifact cho các case này. Do đó không tính là negative coverage đạt và không đổi SEC-26/G0-07/P0. CI #40514019123 99/99 PASS không chạy các route này.


> **Chủ sở hữu (lịch sử 2026-10-19):** T1C — độc lập đối chiếu revision có kết quả T1B (security evidence ngày 2026-10-15).
> **Ngày chạy (lịch sử):** 2026-10-19 (phiên Codex p0-audit-refresh).
> **Trạng thái (lịch sử 2026-10-19 — trạng thái hiện hành là snapshot Round 31 đầu file):**
> - **Không chạy lại test site sạch** — máy phiên Codex **không có Docker/WSL2** (xem `environment-probe.md` §3–4). T1C tái tạo bằng **phân tích tĩnh** test code (`test_permissions.py`) + đối chiếu log T1B (`wp05-security-final.txt`, `p0-local-final.txt`) + đọc lại `p0-isolated-verification.md` từ PR #1 (`b382fe4`).
> - Bằng chứng runtime có sẵn vẫn là của T1B (revision `b382fe4`, log `wp05-security-final.txt` 26 test OK trên site `test_cskh.localhost`).
> - Kết quả T1C: từng SEC case / ô matrix được **ánh xạ** vào test method cụ thể và phân biệt trạng thái PASS/FAIL/NOT IMPLEMENTED/NOT RUN. Không suy rộng 18/26/28 test thành "PASS toàn matrix".

## 1. Phạm vi độc lập kiểm T1C

| Mục | Đã chạy | Chưa chạy / không thể chạy | Lý do |
|---|---|---|---|
| Đọc source code 18 `test_*` method | ✅ | — | Toàn bộ test file đã được đọc với `Read` tool (`apps/crm_cskh/crm_cskh/tests/test_permissions.py`, 543 dòng) |
| Đối chiếu `crm_cskh/hooks.py`, `permissions.py`, `api/guarded_crm.py`, `api/guarded_reporting.py` | ✅ | — | Override whitelist + permission hooks đã đọc đầy đủ |
| Đối chiếu log T1B: `wp05-security-final.txt` (26 OK), `p0-local-final.txt` (27 OK), `p0-isolated-verification.md` (28 OK) | ✅ | — | Ba log khác nhau về tổng test → phân biệt bằng revision/lệnh, không gộp |
| Tái chạy `bench --site test_cskh.localhost run-tests --app crm_cskh` | ❌ | **BLOCKED — không thể chạy** | Máy phiên không có `docker`/`wsl`/`bench` (xem §6) |
| Tái chạy qua site chạy `dev`/site sạch mới | ❌ | **BLOCKED — không thể chạy** | Cùng lý do |

## 2. Đối chiếu ba log có tổng test khác nhau (T1B ≠ T1A ≠ p0-isolated)

T1C **từ chối gộp** ba log thành "28 test" duy nhất. Mỗi log thuộc một revision/lệnh/site riêng:

| Log | Revision | Lệnh | Site | Tổng test | 18 permission-boundary methods | Ghi chú |
|---|---|---|---|---|---|---|
| `deploy/dev/evidence/wp05-security-final.txt` (đuôi file ngụ ý T1B đã tạo) | `b382fe4` (HEAD) | `bench --site test_cskh.localhost run-tests --app crm_cskh` | `test_cskh.localhost` | **26 OK** trong 7.964s | **có** trong 26 test | Không có `test_seed_owner_scope_and_hierarchy_are_applied` (18 test methods, có thể 1 method lỗi import tại thời điểm chạy → 26 thay vì 27). Khớp với mô tả "Ran 26 tests in 7.964s OK". |
| `deploy/dev/evidence/p0-local-final.txt` | `b382fe4` | cùng lệnh nhưng sau re-migrate `dev.localhost` rồi `test_cskh.localhost` | `dev.localhost` + `test_cskh.localhost` | **27 OK** trong 9.037s | **có** trong 27 test | Migrate chạy lại cả hai site; chạy thêm 1 test; có thể test #18 đã pass lần này. |
| `deploy/dev/evidence/p0-isolated-verification.md` §"Security and source reload" | `b382fe4` (PR #1 GitHub Actions run `#38712593755`) | CI job `p0` step `frappe tests` | GitHub-hosted runner site sạch | **28 OK** trong 8.201s | **có** trong 28 test | Phiên CI thứ ba; có thể đã chạy kèm test "source reload probe" hoặc helper test. Không có log thô trong repo — chỉ tổng kết trong `p0-isolated-verification.md`. |

**Kết luận độc lập:** ba số (26/27/28) là ba lượt chạy độc lập trên ba site/lần khác nhau; **không có bằng chứng trong repo** rằng chúng cùng revision nhưng khác pass count vì test #18 flaky (có thể do vì `crm_cskh.tests.test_permissions` import lỗi giữa hai lần chạy). T1C ghi nhận ba lượt, **không chọn 28 làm chuẩn duy nhất** và **không dùng số này để nói PASS toàn matrix**.

## 3. Mapping 18 permission-boundary methods → ô matrix + SEC case

Đọc từng test method trong `test_permissions.py` (18 methods, dòng 206–543):

| # | Test method | Endpoint/actor chính | Ô matrix đã verify | SEC case đã verify | Trạng thái T1C | Bằng chứng đối chiếu |
|---|---|---|---|---|---|---|
| 1 | `test_lead_list_and_get_data_only_return_owner_scope` (L206) | `frappe.client.get_list`, `frappe.client.get`, `crm.api.doc.get_data`, actor `A` own + `A→B` đoán | LEAD-01, LEAD-02, LEAD-04, LEAD-05 | SEC-01, SEC-02 | **PASS trong route đã test** | Test #1 của `wp05-security-final.txt`; có assertion cụ thể `PermissionError` cho cross-scope get; `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. |
| 2 | `test_get_data_does_not_return_permlevel_fields` (L227) | `crm.api.doc.get_data`, actor `A`, Custom Field `permlevel=1` synthetic | LEAD-16 | SEC-25 | **PASS** | Tạo Custom Field `custom_cskh_sec_restricted` permlevel=1, set giá trị, gọi `get_data(..., rows=["name", fieldname])`, assert `not result["data"][0].get(fieldname)`. |
| 3 | `test_child_lists_and_activity_reads_stay_with_parent_scope` (L261) | `frappe.get_list` per child doctype, `crm.api.activities.get_activities`, actor `A` cross | CL-04, TK-02 (list), NT-03 (Comment), AT-04 (File) | SEC-03, SEC-17, SEC-18 (partial, list only) | **PASS** | Comment doctype trả `set()` (DocType-level permission chặn ở Frappe, không phải `crm_cskh` hook); các child doctype khác assert `set(rows) ≤ allowed`. |
| 4 | `test_private_file_download_and_stale_docshare_are_revoked` (L285) | `frappe.core.doctype.file.utils.find_file_by_url`, `.check_permission`, `guarded_crm.get_linked_*`, actor sau reassign | AT-02, AT-04, RA-09, RA-10 | SEC-05, SEC-10, SEC-22 (partial) | **PASS** | Admin share Lead với `a`, đổi owner `a→b`, sau đó `find_file_by_url` cho `a` là `None`; mọi child doctype check_permission raise; endpoint guarded raise. |
| 5 | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) | `data_export`, `export_query`, `query_report.run`, `get_contact_by_phone_number`, actor `A`/`TL1`/`BOD`/`ADMIN` | LS-03, LS-10 (neg), BD-01 neg, RISK-SEC-09 neg | SEC-12, SEC-15 (phone lookup only), SEC-27 (phone lookup) | **PASS** cho người không phải Admin; **PARTIAL** vì cuối test Admin gọi phone lookup thành công (proxy "fail-open" cho Admin theo `_require_system_phone_lookup`, cố ý) | Test khẳng định staff/manager/BOD raise `PermissionError` cho mọi route. Đoạn cuối có `set_user("Administrator")` rồi gọi `get_contact_by_phone_number` và expect trả về dict `{"mobile_no": "P0-LOOKUP-NO-MATCH"}` — đây là **behavior cố ý**, không phải lỗ hổng, nhưng tài liệu phải ghi rõ. |
| 6 | `test_child_records_and_direct_private_file_are_record_scoped` (L356) | `.check_permission`, `get_call_log`, `frappe.client.set_value`, actor `A` cross | CL-02, CL-03, NT-02, TK-02, AT-01, AT-03 | SEC-03, SEC-17, SEC-18 (cross record) | **PASS** | Mỗi child doctype `assertRaises`; `get_call_log(self.call_b)` raise; `set_value` raise. |
| 7 | `test_team_leader_sees_team_but_not_other_team` (L388) | `frappe.get_list("CRM Lead")`, `.check_permission`, actor `TL1` cross team | LEAD-09, LEAD-10, CL-06, CL-07 | SEC-06 (TL) | **PASS** | `visible` chứa Lead Team 1 nhưng KHÔNG chứa Lead Team 2; detail Lead cross-team raise. |
| 8 | `test_sales_manager_without_hierarchy_is_denied` (L397) | `frappe.get_list`, `.check_permission`, actor `outside` Sales Manager **không có hierarchy node** | LEAD-10 (manager-ngoài-cây), RISK-SEC-06 | SEC-07 | **PASS** | List trả rỗng; detail raise. Đây là test quan trọng nhất cho `_manager_without_scope` fail-closed. |
| 9 | `test_linked_activity_endpoints_check_parent_before_returning_data` (L403) | `guarded_crm.get_linked_calls/_notes/_tasks/_attachments/_linked_docs_of_document/_assigned_users`, actor `A` cross | CL-04 (endpoint), NT, TK, AT-04, RA-09 (assigned), RA-09 (linked docs) | SEC-04, SEC-19, SEC-20 | **PASS** | Mọi endpoint raise `PermissionError` khi gọi với Lead cross-scope. Đây là test cốt lõi cho `guarded_crm.py` — verify rằng override whitelist của `crm_cskh` thực sự chặn call của CRM. |
| 10 | `test_owner_change_and_assignment_bypass_are_denied` (L423) | `lead.save(ignore_permissions=True)`, `remove_assignments(ignore_permissions=True)`, `frappe.share.add`, actor `A` | LEAD-14 (own lead_owner), RA-02, RA-03 | SEC-08, SEC-09, SEC-21 | **PASS** | Mọi override fail-closed nhờ `validate_lead_owner` + `validate_lead_assignment` hook. |
| 11 | `test_bulk_delete_and_unlink_cannot_touch_cross_scope_records` (L437) | `crm.api.doc.delete_bulk_docs`, `remove_linked_doc_reference`, actor `A` cross | LEAD-15 (bulk), RA-07 partial | SEC-12 (delete bulk) | **PASS** | `delete` raise `PermissionError`; `remove_linked_doc_reference` không thay đổi reference (assert bằng `db.get_value` sau). |
| 12 | `test_stale_todo_cannot_restore_access_after_owner_changes` (L450) | `frappe.get_list`, `.check_permission` cho Lead/ToDo/call/note/task/file, admin reassign | RA-09, RA-10, RISK-SEC-02 | SEC-10, SEC-22 | **PASS** | Assert `notIn` Lead từ list của `A` sau reassign; ToDo của `A` raise; Lead readable bởi `B` (new owner). |
| 13 | `test_cross_team_transfer_blocks_old_owner_and_leader` (L474) | `frappe.get_list`, `.check_permission`, admin cross-team reassign `c→a` | RA-09 (cross-team), RISK-SEC-02; **reassign dùng actor `Administrator` ⇒ positive control `RA-05-SM`, không map ô RA-05 (CRM-ADMIN)** | SEC-10, SEC-11 | **PASS** | `C` không còn thấy Lead; `A` thấy Lead; `TL2` (cũ) không thấy Lead; `TL1` (mới) thấy Lead. **Lưu ý actor (Round 32b):** phần reassign `c→a` chạy bằng `Administrator` — chỉ là positive control, không phải bằng chứng cho ô CRM-ADMIN (`RA-05`). |
| 14 | `test_direct_todo_assignment_is_denied_even_with_ignore_permissions` (L490) | `ToDo.insert(ignore_permissions=True)` với ref CRM Lead, actor `A` | RA-02, RA-03 | SEC-09, SEC-21, SEC-22 (partial) | **PASS** | Hook `validate_lead_assignment` raise `PermissionError`. |
| 15 | `test_staff_cannot_duplicate_assignment_rules` (L504) | `crm.api.assignment_rule.duplicate_assignment_rule`, actor `A` | LS-07 (config) | SEC-24 | **PASS** | Admin tạo rule, `set_user(A)` gọi duplicate → raise; assert rule duplicate không tồn tại trong DB. |
| 16 | `test_bod_viewer_cannot_read_leads_or_manage_users` (L528) | `frappe.get_list("CRM Lead")`, `frappe.has_permission(User/Role/Data Import, "create")`, `frappe.get_roles(BOD)`, actor BOD | LEAD-12 (BOD), BD-01, BD-05, RISK-SEC-09 | SEC-15, SEC-28 | **PASS** | BOD không đọc Lead; không có quyền create User/Role/Data Import; không có role `System Manager`. |
| 17 | `test_crm_admin_role_fails_closed_until_controlled_service_exists` (L537) | `frappe.has_permission("CRM Lead", "read"/"write")`, actor CRM-ADMIN | LEAD-13 fail-closed, RISK-SEC-09 fail-closed | SEC-15 (partial), SEC-28 (partial) | **PASS** | CRM-ADMIN **không** có `System Manager`, **không** có read/write Lead (thiết kế cố ý của ADR-005 — fail-closed cho tới khi reporting service được xây). |
| 18 | `test_seed_owner_scope_and_hierarchy_are_applied` (L543) | `permissions._user_owns_in_scope`, `permissions.user_in_scope` | (sanity check, không map matrix) | (sanity) | **PASS** (helper internal) | Test cuối, sanity: own scope True, manager hierarchy True, cross hierarchy False. |

**Tổng verification của T1C:**
- 18 test methods × mỗi method trung bình 4–12 subTest (assertion lồng) → coverage rộng hơn 18 ô matrix nhưng vẫn giới hạn vào route/file đã chạy.
- Tổng ô matrix **passing per route** (không phải tổng matrix): **trên dưới 32 ô** (LEAD 9, CL 5, TK 4, NT 4, AT 4, RA 6, LS 6, BD 2) — con số chính xác tuỳ cách đếm nhưng **không bao giờ = 70 ô**.
- Tổng SEC case **passing per route**: 19/28 (SEC-01…12, 15 phone-lookup, 17, 18, 19, 20, 21, 22, 24, 25, 27 phone-lookup, 28). **Không có test nào chạy SEC-13/14/26/16** ở `test_permissions.py`.

## 4. SEC-13, SEC-14, SEC-27 — đối chiếu độc lập

### SEC-13 — Guest gọi endpoint có xác thực

- **Test trong `test_permissions.py`**: KHÔNG có. Không test nào cover Guest.
- **Test trong `test_health.py`**: `test_ping_not_registered_as_guest_method` + `test_guest_is_denied` (L21, L27) — chỉ gọi `frappe.is_whitelisted(health.ping)` và `frappe.set_user("Guest")` rồi `frappe.is_whitelisted(...)` → raise `PermissionError`. Đây là **unit-level guard kiểm `frappe.guest_methods` set**, không phải HTTP round-trip thật.
- **T1C kết luận**: SEC-13 ở **runtime** vẫn **PARTIAL**. Transport-level (HTTP 401/403 từ reverse proxy + `frappe.handler`) **chưa được kiểm chứng** ở site sạch. Cần test riêng gọi endpoint qua HTTP client (vd. `requests` + `/api/method/crm_cskh.api.health.ping` không có header auth → expect `401`/`403`).

### SEC-14 — CSRF/session khi POST

- **Test trong `test_permissions.py`**: KHÔNG có.
- **Test trong `test_health.py`**: KHÔNG có.
- **T1C kết luận**: SEC-14 ở **runtime** vẫn **NOT RUN**. Browser observation trong `p0-exit-report.md` (G0-08): "POST CSRF hợp lệ" — đây là quan sát thủ công, **không** phải test tự động. Cần test: POST không có `X-Frappe-CSRF-Token` → expect `400`/`PermissionError`; POST có token sai → expect `400`; POST có token hợp lệ + cookie session → `200`. Đây là **blocker còn lại rõ ràng**.

### SEC-27 — `get_call_contact` không tham chiếu Lead

- **Test trong `test_permissions.py`**: chỉ `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` có nhánh `get_contact_by_phone_number("P0-LOOKUP-NO-MATCH")` cho 4 actor — verify **endpoint** bị chặn với role thấp. **KHÔNG** cover nhánh nội bộ `crm_call_log.get_call_contact` khi `Call Log` không tham chiếu Lead (xem `crm/fcrm/doctype/crm_call_log/crm_call_log.py:get_call_contact`).
- **T1C kết luận**: SEC-27 vẫn **PARTIAL** như T1B nói; nhánh fallback (`get_contact_by_phone_number` không tham chiếu Lead) **chưa có negative test**.

## 5. Trạng thái per-route T1C độc lập

> Cấu trúc: PASS/FAIL/NOT IMPLEMENTED/NOT RUN theo định nghĩa P0 §7.6.

| Route / endpoint | Actor thử | Kỳ vọng | Thực tế (từ test methods) | Trạng thái T1C | SEC/OS liên quan |
|---|---|---|---|---|---|
| `frappe.client.get("CRM Lead", own)` | A | A | OK | **PASS** | LEAD-01, SEC-01 |
| `frappe.client.get("CRM Lead", cross)` | A→B | D | `PermissionError` | **PASS** | LEAD-02, SEC-01 |
| `frappe.client.get_list("CRM Lead", filters spoofed)` | A | scope A | `set == {A1}` | **PASS** | LEAD-04, LEAD-05, SEC-02 |
| `crm.api.doc.get_data(..., rows=[...])` field-level exposure | A+permlevel1 | D (field bị filter) | field không xuất hiện | **PASS** | LEAD-16, SEC-25 |
| Lead edit field ngoài whitelist CSKH (P0 từ chối trực tiếp) | A own lead | D | `validate_lead_owner` raise | **PASS** (fail-closed vì hook) | LEAD-14, SEC-08 |
| `crm.api.doc.get_data(..., doctype=other)` | A | scope doctype | không có test riêng | **NOT RUN** | LEAD-15, SEC-25 |
| BOD get_list CRM Lead | BOD | D (read-only, chưa cấp scope) | `PermissionError` raise | **PASS** | LEAD-12, SEC-15, SEC-28 |
| CRM-ADMIN read CRM Lead | CRM-ADMIN | D (fail-closed) | `has_permission == False` | **PASS** | LEAD-13, SEC-15 |
| `crm_call_log.get_call_log(own)` | A own | C | OK | **PASS** | CL-01 |
| `crm_call_log.get_call_log(cross)` | A→B | D | `PermissionError` raise | **PASS** | CL-02/03, SEC-03, SEC-17 |
| `frappe.get_list("CRM Call Log", filter spoofed)` | A | D | không có value ngoài own | **PASS** | CL-04, SEC-03 |
| Task/Note/Comment/File cross record read | A→B | D | `check_permission raise` | **PASS** | TK-02, NT-02/03, AT-01/03 |
| `get_doc("CRM Task", cross).check_permission("read")` | A→B | D | raise | **PASS** | TK-02, SEC-18 |
| `frappe.client.set_value("CRM Task", cross, ...)` | A→B | D | raise | **PASS** | TK-03, SEC-04 |
| `frappe.core.doctype.file.utils.find_file_by_url` (own) | A | OK | OK sau reassign old owner | **PASS (own cũ mất URL)** | AT-01, AT-02, SEC-05 |
| `get_attachments("CRM Lead", cross)` | A→B | D | raise | **PASS** | AT-04, SEC-04, SEC-26 partial |
| **URL leak qua file_url khi perm được phép** | A own | bounded bởi perm | không có test riêng khẳng định URL file private **không** lộ trong `attachments` JSON | **NOT RUN** | SEC-26 |
| Owner change `lead.save(ignore_permissions=True)` | A | D | raise | **PASS** | RA-01, SEC-08 |
| `frappe.share.add("CRM Lead", own, write=1, user=cross)` | A | D | raise | **PASS** | RA-02, SEC-08 |
| `remove_assignments(ignore_permissions=True)` (Staff) | A | D | raise | **PASS** | RA-03, SEC-09, SEC-21 |
| Admin reassign `a→b`, A sau đó read Lead | A | D | raise (cả Lead & child) | **PASS** | RA-09, SEC-10 |
| Cross-team reassign `c→a` (actor `Administrator` = System Manager), C cũ check | C | D | raise | **PASS** (positive control `RA-05-SM`) | SEC-11; **không** map ô `RA-05` (CRM-ADMIN) |
| Stale ToDo (open, allocated=a) sau reassign | A | D | raise | **PASS** | RA-09, SEC-22 |
| ToDo insert cross-scope | A | D | raise | **PASS** | RA-02, SEC-21 |
| `delete_bulk_docs` cross-scope | A→B | D | raise | **PASS** | RA-07, SEC-12 (delete) |
| `data_export` / `export_query` (Staff/Manager/BOD) | staff | D | raise | **PASS** | LS-03, SEC-12 |
| `query_report.run` (Staff) | staff | D | raise | **PASS** | LS-10, SEC-12 |
| `crm.api.dashboard.get_dashboard` Manager without hierarchy | manager ngoài cây | D | raise | **PASS** | RISK-SEC-06, SEC-07 |
| `get_contact_by_phone_number` (Staff) | A | D | raise | **PASS** | SEC-27 (endpoint) |
| `get_contact_by_phone_number` Administrator | Admin | OK (proxy fail-open cố ý) | trả dict | **PASS (Admin behavior)** | SEC-27 (note) |
| `get_call_contact` **fallback nội bộ khi Call Log không ref Lead** | (không test) | (không rõ) | **NOT RUN** | **NOT RUN** | SEC-27 nhánh nội bộ |
| `get_assigned_users` cross-scope | A→B | D | raise | **PASS** | RA-09, SEC-19 |
| `get_linked_docs_of_document` cross-scope | A→B | D | raise | **PASS** | RA-09, SEC-20 |
| `duplicate_assignment_rule` (Staff) | A | D | raise + rule không tồn tại trong DB | **PASS** | LS-07, SEC-24 |
| Guest gọi `crm.api.doc.get_data` qua HTTP | Guest | 401/403 | **NOT RUN** — chỉ có `frappe.is_whitelisted` unit-level | **NOT RUN** | SEC-13 |
| POST không CSRF token | user | 400 / `PermissionError` | **NOT RUN** | **NOT RUN** | SEC-14 |
| POST sai CSRF token | user | 400 / `PermissionError` | **NOT RUN** | **NOT RUN** | SEC-14 |
| Reverse proxy/CORS cùng origin | (probe) | bounded | **NOT RUN** | **NOT RUN** | SEC-13 |

## 6. Blocker còn lại (T1C xác nhận độc lập)

| # | Blocker | Trạng thái T1C | Cần để gỡ |
|---|---|---|---|
| **B-T1C-1** | Máy phiên Codex hiện tại **không có Docker/WSL2/bench** — không thể tái chạy site sạch để verify lại độc lập | **MỞ** | Owner bật WSL2 + Docker Desktop như đã làm trước đó, cấp shell đã có `docker` cho phiên, hoặc cấp log raw của 3 lượt chạy khác nhau để T1C đối chiếu thay vì tái chạy |
| **B-T1C-2** | **SEC-13/14/26** chưa có automated test ở `test_permissions.py` hay `test_health.py` | **MỞ** | Thêm file `tests/security/test_sec_13_14_26.py` gọi `requests` thật qua socket `127.0.0.1:<port>` (Frappe bench bind loopback) cho Guest + CSRF + URL leak |
| **B-T1C-3** | **SEC-27 nhánh nội bộ** (`crm_call_log.get_call_contact` khi Call Log không ref Lead) chưa test | **MỞ** | Thêm test tạo Call Log không có `reference_doctype`, gọi `get_call_log`, gọi tiếp contact lookup path |
| **B-T1C-4** | **3 số tổng test khác nhau** (26 / 27 / 28) không có log phân tích chính thức về nguyên nhân chênh | **MỞ** | Chạy lại `pytest -v` hoặc `bench run-tests --verbose` trên site sạch, capture stdout đầy đủ, xác định test #18 có flaky hay không |
| **B-T1C-5** | **Toàn bộ ~70 ô matrix chưa fill per-cell test mapping đầy đủ** — chỉ có ~32 ô đang được route-based test chạm tới | **MỞ** | Từng ô matrix cần `[Test: name @ file:line]` annotation; mapping hiện nằm trong đầu T1B chứ không có manifest trong repo |

## 7. Kết luận độc lập T1C

- **Không chạy lại được site sạch** trong phiên này vì thiếu Docker/WSL2 (B-T1C-1).
- **Phân tích tĩnh 18 test methods** của `test_permissions.py` cho thấy T1B đã mapping đúng theo code; **không có test nào cho SEC-13/14/26** ở runtime transport.
- **Tổng test app (26/27/28) là ba lượt chạy** của ba site/lần/revision khác nhau; T1C giữ cả ba số và **không** dùng tổng để khẳng định "PASS toàn matrix".
- **G0-07** vẫn **PARTIAL** sau T1C:
  - Đã verify runtime các route đã code: **PASS có giới hạn** (đúng như T1B).
  - **NOT RUN**: SEC-13 (Guest HTTP), SEC-14 (CSRF), SEC-16 (`crm_cskh` API field exposure toàn diện), SEC-26 (URL leak toàn diện), SEC-27 nhánh nội bộ.
  - **NOT IMPLEMENTED coverage**: ~38/70 ô matrix chưa có test chạm tới (vd. LEAD-15 cross-doctype spoof, MỌI ô liên quan BOD reporting đọc scoped, MỌI ô reassignment với BOD, v.v.).
- **Hệ thống KHÔNG tuyên bố an toàn**. **G0-07 KHÔNG đạt**, **G0-10 (CI) đạt** nhưng không thay thế.
- **Hành động kế tiếp** (không thuộc T1C): thêm test transport (B-T1C-2), chạy lại 3 log ở site sạch duy nhất để confirm test #18 flaky hay stable (B-T1C-4), fill manifest test mapping cho từng ô matrix (B-T1C-5), xử lý SEC-27 nhánh nội bộ (B-T1C-3).

## 8. Phụ lục — các ký hiệu dùng trong file này

| Ký hiệu | Nghĩa | Nguồn |
|---|---|---|
| **PASS** | Test đã chạy và assert behavior đúng với kỳ vọng | `test_permissions.py` thực tế |
| **PARTIAL** | Một phần test đã chạy nhưng còn nhánh chưa cover | T1B + T1C |
| **NOT IMPLEMENTED** | Code có đường có thể exploit nhưng chưa có test cho nó (xem SEC-26 URL leak) | T1C |
| **NOT RUN** | Test cần environment (Docker/HTTP) chưa chạy | T1C |
| **BLOCKED** | Test không thể chạy trong phiên này | T1C |
| Test #n | Method thứ n trong `TestPermissionBoundaries` | file `test_permissions.py` |
| Site | `dev.localhost` (DEV) hoặc `test_cskh.localhost` (TEST) | `deploy/dev/seed/`, `bench` |

## 9. Liên kết minh chứng (cross-ref)

- `deploy/dev/evidence/wp05-security-final.txt` — log T1B 26 OK tại `test_cskh.localhost` sau WP-05.
- `deploy/dev/evidence/p0-local-final.txt` — log T1B 27 OK sau re-migrate `dev.localhost` và `test_cskh.localhost`.
- `deploy/dev/evidence/p0-isolated-verification.md` §"Security and source reload" — tổng kết 28 OK trên GitHub Actions run `#38712593755` (PR #1).
- `apps/crm_cskh/crm_cskh/tests/test_permissions.py` — 18 permission-boundary methods (đã phân tích dòng 1–543).
- `apps/crm_cskh/crm_cskh/tests/test_health.py` — 4 method; 2 method cover Guest ở mức `frappe.is_whitelisted`, không phải HTTP.
- `apps/crm_cskh/crm_cskh/crm_cskh/tests/test_poc_custom_field.py` — 3 method, **không** phải permission test.
- `apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_app_settings/test_cskh_app_settings.py` — 3 method, **không** phải permission test.
- `apps/crm_cskh/crm_cskh/permissions.py` — implementation hooks đã đọc.
- `apps/crm_cskh/crm_cskh/api/guarded_crm.py` — wrapper endpoint (override whitelist).
- `apps/crm_cskh/crm_cskh/api/guarded_reporting.py` — wrapper export/report.
- `apps/crm_cskh/crm_cskh/hooks.py` — đăng ký hook permission cho 7 DocType + override whitelist.
- `docs/architecture/permission-matrix.md` — tài liệu chính T1C sửa trong cùng change set.
- `docs/architecture/risk-register.md` — tài liệu chính T1C sửa trong cùng change set.
- GitHub Actions: PR #1 (`b382fe4`) runs `#38712593755` (CI pass) và `#39185122032` (push run pass). Origin tool không truy cập được namespace `nobita6986` nên T1C dùng link tài liệu + trích đoạn có sẵn trong repo.


## 10. Nhật ký Round 25 — 2026-10-28

Round 25 ghi nhận CI #40514019123 (99/99 test PASS) và lần đầu mở rộng SEC-26 sang năm route Frappe chuẩn theo phép đo DEV ad-hoc; route-level và download observations không có artifact lưu. Gate/tally không đổi.

## 11. Nhật ký Round 26 — 2026-10-28

Round 26 làm rõ hai mặt của phép đo DEV ad-hoc (không artifact): user trong scope nhận URL private; follow-up staff ngoài scope bị 403 trên từng route; owner download 200/90 B, ngoài scope download 403 có/không `fid`. T1C đề xuất scope calibration permission-governed cho standard Frappe UI, giữ custom CRM redaction contract. Không kết luận IDOR; SEC-26/G0-07 PARTIAL, P0 NO-GO, 71-cell matrix chưa audit đầy đủ.

## 12. Nhật ký Round 27 — 2026-10-28

Đối chiếu patch probe mới của T1B: code dự kiến phủ năm route chuẩn, cặp actor trong/ngoài scope, và direct download ngoài scope có/không `fid`; Docker đang down nên probe chưa chạy, chưa có runtime artifact. Ghi rõ URL visibility cho user trong scope là câu hỏi scope/policy, không tự nó là vulnerability; route-level 403/download denial ở fixture đã đo không chứng minh an toàn mọi route/site. T1C đề xuất scope calibration ít xáo trộn, giữ redaction contract cho endpoint CRM tùy biến. Giữ SEC-26 PARTIAL, G0-07 PARTIAL/CHƯA KÝ, P0 NO-GO, tally 25+3, matrix 71 ô chưa tái chấm toàn bộ. Không sửa T1A/T1B/code/workflow/policy; không stage/commit/push/merge.


## 13. Nhật ký Round 28 — 2026-10-28

Tái xác minh CI #40514019123 và digest artifact; tách 99/99 method khỏi SEC tally 25+3. Audit đủ 71 ô: 44 PASS, 20 PARTIAL, 4 NOT RUN, 3 CHƯA XÁC MINH. Tám ô owner-T1B và LS-02 PASS theo test riêng; AT-04 PASS chỉ cho custom CRM endpoint; RA-04 PASS theo kỳ vọng D đã quyết định; hạ TK-04/LS-08/BD-03 xuống PARTIAL vì test trước không đạt đủ criterion. Ghi nhận riêng DEV observation không artifact cho năm Frappe route, policy URL visibility chưa được Owner quyết, và probe code mới chưa chạy. Giữ SEC-26/G0-07 PARTIAL, P0 NO-GO. Không sửa policy/code/test/workflow, không stage/commit/push/merge.

## 14. Nhật ký Round 29 — 2026-10-28

Đối chiếu trực tiếp artifact DEV T1B 30 case, SHA-256 `33af91d6af4f7d2394ba7009a1fd32564116b76228eafe6046fbbc6edbb345f2`: 28 PASS/2 FAIL; hai 403 ở `frappe.client.get(File)` và REST File phản chiếu File ID theo boolean, không lưu ID/body thô. Ghi Owner decision 2026-10-28 cho phép URL visibility permission-governed trên standard routes in-scope, download auth riêng; custom CRM endpoints vẫn redacted. Download ngoài scope 403 có/không `fid`, in-scope 200/90 B. Không có bằng chứng IDOR từ hai lỗi, nhưng không được miễn trừ; giữ SEC-26 PARTIAL, G0-07 PARTIAL/CHƯA KÝ, P0 NO-GO. Matrix Round 28 không đổi. Không sửa code/test/workflow/T1A/T1B, không stage/commit/push/merge.

## 15. Nhật ký Round 30 — 2026-10-28

Tải và parse artifact CI #40514019123 (artifact `12738607035`, API digest `31c707ad…b146`): `sec13-guest` PASS với HTTP 403/`PermissionError`, đúng P0 §7.5; vì vậy SEC-13 chuyển từ PARTIAL sang PASS trong tally hiện hành. Vite dev CORS được giữ là phép đo bổ sung, không phải điều kiện SEC-13. Đối chiếu độc lập tám hash artifact T1B Round 29; giữ hai lỗi File-ID-reflection FAIL, SEC-26 PARTIAL, SEC-16 PARTIAL, matrix Round 28, G0-07 PARTIAL/CHƯA KÝ và P0 NO-GO. Ghi rõ 65 static guards mới chỉ có kết quả local, chưa có CI artifact của revision workflow mới. Không sửa code/test/workflow, không stage/commit/push/merge.

## 16. Nhật ký Round 31 — 2026-10-30

PHỤC HỒI TÀI LIỆU: bản Round 30 (2026-10-28) phục hồi byte-identical từ snapshot `snapshot cuc bo ngoai repo (`t1b-pr2-review-e67`)` (3 file, SHA-256 `39405C1E…`/`AE7F8DA7…`/`4C7DF92F…` — đã hash sau copy, khớp 100%); lịch sử Round 1–30 bảo toàn; Round 31 ghi trên nền đó. KIỂM ĐỊNH T1B-ext+1: tự xác minh run mới nhất `#41037431038` (run #59, head `b1087bc`, merge `682a9d93`, SUCCESS); tự tải + tự hash artifact `12968528845` (digest `c6b298a8…` khớp API); tự parse 99/99 JUnit (gate xanh) + HTTP 18/18 + file-oracle 9/9 + remaining-gaps 62/62 + Dynamic Link âm/dương (dl01 4 case); source `b1087bc` xác nhận override `get_activities` + wrapper `get_linked_calls`/`get_linked_notes`/`get_linked_tasks`. KẾT QUẢ: SEC-16 → PASS (12 permlevel surfaces + 3 system positive control); SEC-26 → PASS (đủ 4 tiêu chí Round 30: reflection đã xử lý 404-indistinguishable/403-sanitized, CI regression durable, custom redaction giữ, T1C verify); SEC tally 28/28 PASS (SEC-13 giữ PASS giới hạn topology P0); matrix 52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71; **G0-07 = PASS (T1C KÝ)**. KHÔNG merge PR #2; P0 GO/NO-GO thuộc T0/Owner. KHÔNG sửa T1A/T1B/code/test/workflow/policy; KHÔNG stage/commit/push.

## 17. Nhật ký Round 32 — 2026-10-31

KIỂM ĐỊNH T1B-ext+2 (12 ca mới "matrix gap probe"): tự xác minh run `#41159273106` (run #61, head `e0ef02f`, merge `136a10c2`, SUCCESS); tự tải + tự hash artifact `13026861084` (140 924 B, digest `8cfe5f10…` khớp API); tự parse 99/99 JUnit + 18/18 HTTP + 9/9 file-oracle + **74/74 remaining-gaps** (`failed_cases=[]`, tăng 12 ca). Diff `b1087bc…e0ef02f` = 2 commit (chỉ probe `ci_g007_remaining_gaps_probe.py` +563/−5 + doc; KHÔNG đổi code app). 12 ca → 8 ô PARTIAL → PASS: LEAD-16/LS-06 (field `mobile_no`/`email` scope), LEAD-11 (TL2 direct/list), RA-05 (cross-team transfer admin + new owner visible), RA-08/BD-04 (BOD revoke/reassign 403), BD-03 (BOD bulk delete 403), RA-10 (chủ mới ĐỌC Lead/Note PASS; **GHI child 403 fail-closed** — KHÔNG chấm "được phép ghi" PASS, phần ghi thuộc P1 ownership service ADR-004 §2.2). **Matrix hiện hành (Round 32b): 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71.** 9 PARTIAL còn lại (LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01) đều là nghiệp vụ P1 chưa triển khai, đang fail-closed — KHÔNG còn gap đọc chéo/ghi trái quyền trong P0. SEC tally 28/28 PASS; **G0-07 = PASS (T1C KÝ)**; P0 GO/NO-GO thuộc T0/Owner theo đủ 10 gate. KHÔNG merge PR #2; KHÔNG stage/commit/push.
