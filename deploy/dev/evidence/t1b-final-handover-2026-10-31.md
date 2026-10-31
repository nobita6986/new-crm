# T1B — BÀN GIAO GÓI CUỐI P0 + KẾT QUẢ CI (2026-10-31)

**Người gửi:** T1B · **Người nhận:** T1C (kiểm định độc lập) + T1A (tổng hợp P0)
**PR #2:** `state=open`, `mergeable=MERGEABLE`, **KHÔNG merge**. Head sau đóng gói: `60b6a2b123b3cedd50bd498512938b885c0b8869` (chứa `ba49857` — bản vá lỗi cú pháp patcher của T1A Round 34).

---

## 1. Run CI của commit đóng gói cuối

> **Ghi chú hiệu chỉnh T1C Round 32b (2026-10-31):** ô `RA-05` (Actor = **CRM-ADMIN**) đã bị **hạ PASS → PARTIAL** vì 2 ca HTTP `ra05-*` đo actor `Administrator` (System Manager) ⇒ **evidence không đúng actor của ô**; kỳ vọng P0 của ô đổi `A` → **`D` tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`). 2 ca `ra05-*` được tách thành **positive control `RA-05-SM`** (actor System Manager) **ngoài lưới 71 ô** ⇒ **KHÔNG** nâng ô `RA-05`. Ô `RA-05` còn **PARTIAL** vì thiếu 1 ca HTTP đo trực tiếp actor `CSKH CRM Admin` (`crm.admin@dev.local`). **Tally hiện hành: 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; SEC tally giữ **28/28 PASS** ⇒ **verdict G0-07 = PASS không đổi** (ô hạ vẫn fail-closed đúng kỳ vọng P0 `D`). Chi tiết: `docs/architecture/p0-exit-report.md` mục "Hiệu chỉnh Round 32b" + `deploy/dev/evidence/t1c-ext2-matrix-gap-verification-2026-10-31.md` §4.3. **GO/NO-GO P0 thuộc T0/Owner** — T1A/T1B/T1C không tự ký.

| Trường | Giá trị |
|---|---|
| Run ID / run_number | **#41226473464** / 129 |
| Trạng thái | `success` (completed 2026-10-31T17:56:00Z) |
| Branch head SHA | `60b6a2b123b3cedd50bd498512938b885c0b8869` (= PR #2 HEAD) |
| Merge SHA | `84613327b6b3fb154085826c617a145b309ecd24` (`workflow-metadata.json` → `commit_sha`) |
| Artifact ID | `13050939745` — tên `p0-g007-evidence-41226473464` |
| Artifact size | **140 839 B** |
| Digest (API) | `sha256:43e1c70522957ef37fbb93cfba85ac13de10ec66a39c056cb92ca9eac466ca1a` |
| **SHA-256 T1B tự hash zip** | `43E1C70522957EF37FBB93CFBA85AC13DE10EC66A39C056CB92CA9EAC466CA1A` → **khớp 100%** |

> **Ghi chú về các run trước:** mọi run đóng gói đều **success** với kết quả parse **giống hệt** — #41183084515 (62, head `ddf817c`, artifact `13035534433`, digest `dc7330829a…`), #41183641044 (64, head `9fdeee6`, artifact `13036566067`, digest `d20ef778ca…`), #41184602987 (66, head `b50320a`, artifact `13035818049`, digest `de985d90b6…`), #41185474002 (68, head `10f479b`, artifact `13036987864`, digest `a778844c04…`), #41186135804 (69, head `9dbda45`, artifact `13035909529`, digest `814b3bba9e…`), #41187312027 (71, head `75c37a7`, artifact `13038140741`, digest `673176b4ce…`), #41188127001 (73, head `50dab76`, artifact `13037926540`, digest `a80a222300…`), #41188863453 (74, head `d2e3634`, artifact `13038286054`, digest `c45fba0fd5…`), #41189626544 (75, head `1210adb`, artifact `13038222611`, digest `41726865b3…`), #41190382586 (76, head `7cfeaa5`, artifact `13038099371`, digest `8acc2923f7…`), #41191157121 (78, head `809c9cb`, artifact `13039190745`, digest `b43b335dc1…`), #41198231180 (79, head `8fbc03d`, artifact `13041856408`, digest `64be420397…`), #41199317193 (81, head `74e3764`, artifact `13041863349`, digest `7d87e31fb4…`), #41200134560 (83, head `ae690c8`, artifact `13042043665`, digest `cb365f5389…`), #41200825236 (84, head `356ceeb`, artifact `13042607579`, digest `d391e97188…`), #41201885507 (86, head `43d7fff`, artifact `13042933206`, digest `1d0d6e172a…`), #41202547857 (87, head `eabe296`, artifact `13043193285`, digest `d001fdef41…`), #41203517684 (89, head `74c608e`, artifact `13044051253`, digest `a73d8cc85c…`), #41204474054 (91, head `6d7665a`, artifact `13043978075`, digest `d08ef86b88…`), #41205596840 (93, head `8c4d8d3`, artifact `13043994871`, digest `9704525647…`), #41206591321 (95, head `b7ecd8b`, artifact `13043719761`, digest `5e05003b1c…`), #41207511737 (97, head `ebfa43b`, artifact `13045760286`, digest `a5d4674f0d…`), #41208997647 (99, head `11f6dab`, artifact `13045484144`, digest `33c7c666a6…`), #41210391537 (101, head `2d1e722`, artifact `13045838633`, digest `9c6053c7f3…`), #41211925325 (104, head `c3035bd`, artifact `13046473893`, digest `b1c1993190…`), #41213268348 (106, head `32b6a31`, artifact `13047027236`, digest `22942137e7…`), #41214366200 (107, head `aa2f62c`, artifact `13046829344`, digest `3319889c9d…`), #41215340404 (108, head `c4a0bde`, artifact `13048540101`, digest `b4218240df…`), #41216145009 (110, head `9407588`, artifact `13047783761`, digest `e512f94a01…`), #41217191623 (112, head `170e9c1`, artifact `13049010262`, digest `45083e4715…`), #41218489232 (114, head `192064e`, artifact `13048968108`, digest `477fb94402…`), #41219625279 (117, head `a95dd7d`, artifact `13049756128`, digest `f9ad5a5189…`), #41220714237 (119, head `2f4a3c4`, artifact `13049698802`, digest `495a8d2ed5…`), #41221826649 (121, head `bc9c366`, artifact `13049724001`, digest `b280ee5b87…`), #41222726666 (122, head `2cb845c`, artifact `13050653283`, digest `5d85aa9dcd…`), #41223678163 (124, head `7c86f50`, artifact `13051320796`, digest `05283b20f8…`), #41224698737 (126, head `f5044bc`, artifact `13051552552`, digest `f6dc34a954…`), #41225551988 (127, head `8e4708f`, artifact `13051582521`, digest `47435ccd83…`), #41226473464 (129, head `60b6a2b`, artifact `13050939745`, digest `43e1c70522…`). Run #41183398104 (63) #41184507863 (65) #41185396150 (67) #41187158951 (70) #41188046267 (72) #41191094848 (77) #41198983664 (80) #41199949324 (82) #41201698292 (85) #41203385379 (88) #41204331575 (90) #41205331804 (92) #41206351741 (94) #41207226868 (96) #41208645874 (98) #41209998958 (100) #41211477811 (102) #41211765380 (103) #41212930171 (105) #41215883282 (109) #41216808465 (111) #41218191480 (113) #41219139822 (115) #41219484415 (116) #41220642525 (118) #41221579928 (120) #41223656553 (123) #41224621499 (125) #41226172422 (128) bị `cancelled` do bị supersede khi push commit mới — **không** phải lỗi CI. Revision đang bàn giao để kiểm định độc lập là run **#41226473464** trên head `60b6a2b`.

> **Cách tra cứu revision CI hiện hành:** PR #2 có thể còn commit mới sau tài liệu này. Revision CI hiện hành = **run có `head_sha` bằng PR #2 HEAD**:
> ``
> gh api "repos/nobita6986/new-crm/actions/runs?branch=codex/p0-t1a-g001-g002-g009&per_page=3" \
>   --jq '.workflow_runs[] | {id, run_number, status, conclusion, head_sha}'
> ``
> Mọi run đóng gói đều cho **cùng một kết quả parse** (99/99 + 18/18 + 9/9 + 74/74) nên dùng run nào cũng có giá trị kiểm định, miễn là run `success` trên head chứa đầy đủ gói DEV/i18n/evidence.

## 2. Parse độc lập của T1B (không suy từ tên commit)

| Thành phần | Kết quả |
|---|---|
| `g007-per-method-gate.json` | `gate_passed=true`, `expected_total=99`, `observed_total=99`, `passing_total=99`, `counts={"PASS":99}`; 9 list lỗi đều rỗng `[]`; `xml_module_binding` 5/5 |
| `test_permissions.junit.xml` | 18/18 |
| `test_security_remediation.junit.xml` | 12/12 |
| `test_security_remediation_v2.junit.xml` | 32/32 |
| `test_bt1c5_matrix_gaps.junit.xml` | 27/27 |
| `test_docshare_lifecycle.junit.xml` | 10/10 |
| **Tổng JUnit** | **99/99 PASS, 0 FAIL, 0 ERROR, 0 SKIP** |
| `auxiliary/test_error_response_normalization.junit.xml` | 8/8 (ngoài inventory 99) |
| `http/http-transport.json` | `probe_result=PASS`, `failed_cases=[]`, **18/18** |
| `http/file-oracle/file-oracle.json` | `probe_result=PASS`, `failed_cases=[]`, **9/9** |
| `http/remaining-gaps/remaining-gaps.json` | `probe_result=PASS`, `failed_cases=[]`, **74/74** |
| `module-status.txt` | `probe_static exit=0`, 5 module `exit=0`, auxiliary `exit=0`, `per-method gate exit=0`, `remaining_gaps_http exit=0` |
| `workflow-metadata.json` | 10/10 `workflow_outcomes` = `success`; `redaction_replacements=4` |

> Lưu ý cho T1C: `coverage_scope` của HTTP probe **không đổi** — *"direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered"*. SEC-13 giữ giới hạn topology P0.

## 3. Danh sách file đã commit (4 commit)

| Commit | Nội dung |
|---|---|
| `0bead5c` | Gói đóng gói P0: `vi.csv` (+22 khóa) · `apply-crm-vite-dev-patch.py` · `docker-compose.yml` · 10 ảnh G0-09 ×2 bộ + `capture-manifest.json` ×2 + 5 artifact JSON · `t1a-g002-vite-csrf-blocker.md` · `t1a-g009-vi-checklist.md` · `t1c-ext1-verification-2026-10-30.md` · `t1c-ext2-matrix-gap-verification-2026-10-31.md` · `t1c-security-verification.md` · `p0-exit-report.md` · `permission-matrix.md` · `risk-register.md` |
| `58487d9` | Gói Round 33 T1A: sửa `changeOrigin` → `false` + `VITE_DEV_PORT` (cùng cổng host/container) · `check_js_syntax()` (`node --check`) · `verify-socket-namespace.mjs` · `t1a-g002-socket-namespace-evidence-20261031.json` · `t1a-handover-t1b-dev-i18n.md` · `deploy/dev/README.md` · `.gitignore` root |
| `582a99e` | Round 34 T1A: `t1a-g002-vite-csrf-blocker.md` §13 (lỗi cú pháp chặn patcher) + artifact socket namespace → schema **v3** (`b445f234d1f0cd05da570837f6590a144b92a69f7f65aa8247af6b2aa3ff5f88`) |
| `ddf817c` | Cập nhật bảng bàn giao T1A Round 34 |
| `ba49857` | T1A Round 34 (commit song song): cập nhật `p0-exit-report.md` + `t1a-handover-t1b-dev-i18n.md` |
| `9fdeee6` | T1B: báo cáo đóng gói + bàn giao artifact cho T1C/T1A (file này) |
| `dcae46c` | T1A Round 34 (song song): đồng bộ revision CI + refresh hash bàn giao |
| `b50320a` | T1B: đồng bộ báo cáo + bàn giao sang run #41184602987 |
| `04824df` | T1A Round 34 (song song): đồng bộ revision CI #41184602987 |
| `9407588` | **T1A — đồng bộ `p0-exit-report.md` theo T1C Round 32b**: ô `RA-05` hạ **PASS → PARTIAL** (2 ca `ra05-*` đo actor `Administrator` ≠ CRM-ADMIN của ô) ⇒ tally hiện hành **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; 2 ca `ra05-*` tách thành **positive control `RA-05-SM`** (System Manager) **ngoài lưới 71 ô**; SEC tally giữ **28/28 PASS** ⇒ verdict **G0-07 không đổi**; số Round 32 cũ (60/8/0/3) giữ **nhãn lịch sử** |
| `170e9c1` | T1A: chỉnh merge SHA + run_number cho run CI #41216145009 |
| `192064e` | T1A: đồng bộ run CI #41217191623 (run_number 112) + ghi chú Round 32b vào bàn giao T1B |
| `5ba358a` | T1A: đồng bộ run CI #41218489232 (run_number 114) |
| `60b6a2b` | **T1B — revision đóng gói cuối**: đồng bộ báo cáo + bàn giao sang run #41226473464 |

Tổng: `git diff --stat e0ef02f..60b6a2b` = **48 file, +3 987 / −89**.

## 4. Rà đóng gói (T1B tự thực hiện)

| Hạng mục | Kết quả |
|---|---|
| Phạm vi | Chỉ DEV/i18n + tài liệu + evidence bàn giao. **Không** đụng code app, test, workflow, `deploy/dev/seed/*`, ADR |
| Inventory 99 method | `g007_test_inventory.json` **không đổi**; gate vẫn 99/99 |
| Auxiliary | 8/8, ghi riêng ở `auxiliary/` |
| Secret / `.env` / cookie / token | Sạch. `verify-socket-namespace.mjs` chỉ đọc `ADMIN_PASSWORD` từ env, không ghi giá trị ra log/artifact |
| Đường dẫn host | Đã khử mẫu đường dẫn thư mục người dùng Windows ở `permission-matrix.md`, `risk-register.md`, `t1c-security-verification.md`, `t1c-ext1-verification-2026-10-30.md` (giữ SHA-256 byte-identity) và `t1a-g002-vite-csrf-blocker.md` (→ `%LOCALAPPDATA%`) |
| File debug / scratch | `_tmp_*`, `_recovery/`, `.commandcode/`, `t1b_ext1_dbg*`, `.bak` — không commit; đã chặn bằng `.gitignore` root |
| Hash manifest | 20/20 ảnh khớp SHA-256 + `bytes`; 13/13 file khớp bảng bàn giao T1A |
| Kiểm tra tĩnh | `ruff check` + `ruff format --check` đạt; `docker compose config --quiet` hợp lệ |

## 5. Bàn giao

- **Cho T1C:** artifact `13050939745` + digest `sha256:43e1c705…` + revision head `60b6a2b` / merge `84613327` / run `#41226473464` — đề nghị T1C tự tải + tự hash + tự parse độc lập như Round 31/32.
- **Cho T1A:** commit đóng gói `60b6a2b` + run CI #41226473464 + kết quả parse (đã ghi vào `docs/architecture/p0-exit-report.md` mục "T1B — ĐÓNG GÓI CUỐI + CI XÁC MINH").
- **Không còn lỗi/gap cụ thể** trong phạm vi P0: **9 ô matrix PARTIAL** (LEAD-12/13, **RA-05**, CL-08, TK-07, NT-07, LS-08/10, BD-01) và 3 ô DEFER P1 đều là nghiệp vụ P1 **chưa triển khai**, đang fail-closed (BOD scoped reporting ADR-005, ownership service ADR-004). **Tally hiện hành (T1C Round 32b): 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; SEC tally **28/28 PASS** ⇒ verdict **G0-07 = PASS không đổi**.

## 6. Bất biến

- **KHÔNG merge PR #2.**
- **KHÔNG tự ký P0 GO/NO-GO** — thuộc T0/Owner (đủ 10 gate mới ký).
- KHÔNG sửa file của T1A/T1C/ADR/code/test/workflow ngoài phạm vi đóng gói đã duyệt.
- Không mở rộng kiểm thử khi không còn lỗi hoặc gap cụ thể.
