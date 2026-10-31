# T1B — BÀN GIAO GÓI CUỐI P0 + KẾT QUẢ CI (2026-10-31)

**Người gửi:** T1B · **Người nhận:** T1C (kiểm định độc lập) + T1A (tổng hợp P0)
**PR #2:** `state=open`, `mergeable=MERGEABLE`, **KHÔNG merge**. Head sau đóng gói: `2cb845c1045150f7cc0a5be0e8540630d0dc2f91` (chứa `ba49857` — bản vá lỗi cú pháp patcher của T1A Round 34).

---

## 1. Run CI của commit đóng gói cuối

> **Ghi chú hiệu chỉnh T1C Round 32b (2026-10-31):** ô `RA-05` (Actor = **CRM-ADMIN**) đã bị **hạ PASS → PARTIAL** vì 2 ca HTTP `ra05-*` đo actor `Administrator` (System Manager) ⇒ **evidence không đúng actor của ô**; kỳ vọng P0 của ô đổi `A` → **`D` tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`). 2 ca `ra05-*` được tách thành **positive control `RA-05-SM`** (actor System Manager) **ngoài lưới 71 ô** ⇒ **KHÔNG** nâng ô `RA-05`. Ô `RA-05` còn **PARTIAL** vì thiếu 1 ca HTTP đo trực tiếp actor `CSKH CRM Admin` (`crm.admin@dev.local`). **Tally hiện hành: 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; SEC tally giữ **28/28 PASS** ⇒ **verdict G0-07 = PASS không đổi** (ô hạ vẫn fail-closed đúng kỳ vọng P0 `D`). Chi tiết: `docs/architecture/p0-exit-report.md` mục "Hiệu chỉnh Round 32b" + `deploy/dev/evidence/t1c-ext2-matrix-gap-verification-2026-10-31.md` §4.3. **GO/NO-GO P0 thuộc T0/Owner** — T1A/T1B/T1C không tự ký.

| Trường | Giá trị |
|---|---|
| Run ID / run_number | **#41222726666** / 122 |
| Trạng thái | `success` (completed 2026-10-31T17:10:39Z) |
| Branch head SHA | `2cb845c1045150f7cc0a5be0e8540630d0dc2f91` (= PR #2 HEAD) |
| Merge SHA | `25c969ee8020fafba567e1a4b0ee5646922096fc` (`workflow-metadata.json` → `commit_sha`) |
| Artifact ID | `13050653283` — tên `p0-g007-evidence-41222726666` |
| Artifact size | **140 990 B** |
| Digest (API) | `sha256:5d85aa9dcd6edb4b7b49c91c834acbd6bde07f6e44b24e6defa66dc806ec4e87` |
| **SHA-256 T1B tự hash zip** | `495A8D2ED5C3A7FFBEFFA66BF26938301D199B01EFF38227A6BB35535A71D5D3` → **khớp 100%** |

> **Ghi chú về các run trước:** mọi run đóng gói đều **success** với kết quả parse **giống hệt** — #41183084515 (62, head `ddf817c`, artifact `13035534433`, digest `sha256:dc733082…`), #41183641044 (64, head `9fdeee6`, artifact `13036566067`, digest `sha256:d20ef778…`), #41184602987 (66, head `b50320a`, artifact `13035818049`, digest `sha256:de985d90…`), #41185474002 (68, head `10f479b`, artifact `13036987864`, digest `sha256:a778844c…`), #41186135804 (69, head `9dbda45`, artifact `13035909529`, digest `sha256:814b3bba…`), #41187312027 (71, head `75c37a7`, artifact `13038140741`, digest `sha256:673176b4…`), #41188127001 (73, head `50dab76`, artifact `13037926540`, digest `sha256:a80a2223…`), #41188863453 (74, head `d2e3634`, artifact `13038286054`, digest `sha256:c45fba0f…`), #41189626544 (75, head `1210adb`, artifact `13038222611`, digest `sha256:41726865…`), #41190382586 (76, head `7cfeaa5`, artifact `13038099371`, digest `sha256:8acc2923…`), #41191157121 (78, head `809c9cb`, artifact `13039190745`, digest `sha256:b43b335d…`), #41198231180 (79, head `8fbc03d`, artifact `13041856408`, digest `sha256:64be4203…`), #41199317193 (81, head `74e3764`, artifact `13041863349`, digest `sha256:7d87e31f…`), #41200134560 (83, head `ae690c8`, artifact `13042043665`, digest `sha256:cb365f53…`), #41200825236 (84, head `356ceeb`, artifact `13042607579`, digest `sha256:d391e971…`), #41201885507 (86, head `43d7fff`, artifact `13042933206`, digest `sha256:1d0d6e17…`), #41202547857 (87, head `eabe296`, artifact `13043193285`, digest `sha256:d001fdef…`), #41203517684 (89, head `74c608e`, artifact `13044051253`, digest `sha256:a73d8cc8…`), #41204474054 (91, head `6d7665a`, artifact `13043978075`, digest `sha256:d08ef86b…`), #41205596840 (93, head `8c4d8d3`, artifact `13043994871`, digest `sha256:97045256…`), #41206591321 (95, head `b7ecd8b`, artifact `13043719761`, digest `sha256:5e05003b…`), #41207511737 (97, head `ebfa43b`, artifact `13045760286`, digest `sha256:a5d4674f…`), #41208997647 (99, head `11f6dab`, artifact `13045484144`, digest `sha256:33c7c666…`), #41210391537 (101, head `2d1e722`, artifact `13045838633`, digest `sha256:9c6053c7…`), #41211925325 (104, head `c3035bd`, artifact `13046473893`, digest `sha256:b1c199319…`), #41213268348 (106, head `32b6a31`, artifact `13047027236`, digest `sha256:22942137e…`), #41214366200 (107, head `aa2f62c`, artifact `13046829344`, digest `sha256:3319889c9…`), #41215340404 (108, head `c4a0bde`, artifact `13048540101`, digest `sha256:b4218240d…`), #41216145009 (110, head `9407588`, artifact `13047783761`, digest `sha256:e512f94a0…`), #41217191623 (112, head `170e9c1`, artifact `13049010262`, digest `sha256:45083e471…`), #41218489232 (114, head `192064e`, artifact `13048968108`, digest `sha256:477fb9440…`), #41219625279 (117, head `a95dd7d`, artifact `13049756128`, digest `sha256:f9ad5a518…`), #41220714237 (119, head `2f4a3c4`, artifact `13049698802`, digest `sha256:495a8d2ed…`), #41222726666 (122, head `2cb845c`, artifact `13050653283`, digest `sha256:b280ee5b8…`), #41222726666 (122, head `2cb845c`, artifact `13050653283`, digest `sha256:5d85aa9dc…`). Run #41183398104 (63) #41184507863 (65) #41185396150 (67) #41187158951 (70) #41188046267 (72) #41191094848 (77) #41198983664 (80) #41199949324 (82) #41201698292 (85) #41203385379 (88) #41204331575 (90) #41205331804 (92) #41206351741 (94) #41207226868 (96) #41208645874 (98) #41209998958 (100) #41211477811 (102) #41211765380 (103) #41212930171 (105) #41215883282 (109) #41216808465 (111) #41218191480 (113) #41219139822 (115) #41219484415 (116) #41220642525 (118) #41221579928 (120) bị `cancelled` do bị supersede khi push commit mới — **không** phải lỗi CI. Revision đang bàn giao để kiểm định độc lập là run **#41222726666** trên head `2cb845c`.

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
| `2cb845c` | **T1B — revision đóng gói cuối**: đồng bộ báo cáo + bàn giao sang run #41222726666 |

Tổng: `git diff --stat e0ef02f..2cb845c` = **48 file, +3 987 / −89**.

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

- **Cho T1C:** artifact `13050653283` + digest `sha256:5d85aa9d8…` + revision head `2cb845c` / merge `25c969e` / run `#41222726666` — đề nghị T1C tự tải + tự hash + tự parse độc lập như Round 31/32.
- **Cho T1A:** commit đóng gói `2cb845c` + run CI #41222726666 + kết quả parse (đã ghi vào `docs/architecture/p0-exit-report.md` mục "T1B — ĐÓNG GÓI CUỐI + CI XÁC MINH").
- **Không còn lỗi/gap cụ thể** trong phạm vi P0: **9 ô matrix PARTIAL** (LEAD-12/13, **RA-05**, CL-08, TK-07, NT-07, LS-08/10, BD-01) và 3 ô DEFER P1 đều là nghiệp vụ P1 **chưa triển khai**, đang fail-closed (BOD scoped reporting ADR-005, ownership service ADR-004). **Tally hiện hành (T1C Round 32b): 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; SEC tally **28/28 PASS** ⇒ verdict **G0-07 = PASS không đổi**.

## 6. Bất biến

- **KHÔNG merge PR #2.**
- **KHÔNG tự ký P0 GO/NO-GO** — thuộc T0/Owner (đủ 10 gate mới ký).
- KHÔNG sửa file của T1A/T1C/ADR/code/test/workflow ngoài phạm vi đóng gói đã duyệt.
- Không mở rộng kiểm thử khi không còn lỗi hoặc gap cụ thể.
