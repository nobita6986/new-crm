# T1B — BÀN GIAO GÓI CUỐI P0 + KẾT QUẢ CI (2026-10-31)

**Người gửi:** T1B · **Người nhận:** T1C (kiểm định độc lập) + T1A (tổng hợp P0)
**PR #2:** `state=open`, `mergeable=MERGEABLE`, **KHÔNG merge**. Head sau đóng gói: `9fdeee6a1140a4eeb90d5774b53ee27aae5219be` (chứa `ba49857` — bản vá lỗi cú pháp patcher của T1A Round 34).

---

## 1. Run CI của commit đóng gói cuối

| Trường | Giá trị |
|---|---|
| Run ID / run_number | **#41183641044** / 64 |
| Trạng thái | `success` (completed 2026-10-31T08:20:15Z) |
| Branch head SHA | `9fdeee6a1140a4eeb90d5774b53ee27aae5219be` (= PR #2 HEAD) |
| Merge SHA | `f65fec1b71d6503ffef8920f65a65a3175ba0510` (`workflow-metadata.json` → `commit_sha`) |
| Artifact ID | `13036566067` — tên `p0-g007-evidence-41183641044` |
| Artifact size | **140 947 B** |
| Digest (API) | `sha256:d20ef778ca8b09eb562a121b976522352aae13f676b480b5432350c43370a0fb` |
| **SHA-256 T1B tự hash zip** | `D20EF778CA8B09EB562A121B976522352AAE13F676B480B5432350C43370A0FB` → **khớp 100%** |

> **Ghi chú về các run trước:** run #41183084515 (run_number 62, head `ddf817c`, merge `1dbf58af`, artifact `13035534433` 140 913 B, digest `sha256:dc733082…`) cũng **success** với kết quả parse giống hệt. Run #41183398104 (run_number 63, head `ba49857`) bị `cancelled` do bị supersede khi T1B push commit `9fdeee6` — **không** phải lỗi CI. Revision đang bàn giao để kiểm định độc lập là run **#41183641044**.

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
| `9fdeee6` | **T1B — revision đóng gói cuối**: báo cáo đóng gói + bàn giao artifact cho T1C/T1A (file này) |

Tổng: `git diff --stat e0ef02f..9fdeee6` = **44 file, +3 686 / −73**.

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

- **Cho T1C:** artifact `13036566067` + digest `sha256:d20ef778…` + revision head `9fdeee6` / merge `f65fec1b` / run `#41183641044` — đề nghị T1C tự tải + tự hash + tự parse độc lập như Round 31/32.
- **Cho T1A:** commit đóng gói `9fdeee6` + run CI #41183641044 + kết quả parse (đã ghi vào `docs/architecture/p0-exit-report.md` mục "T1B — ĐÓNG GÓI CUỐI + CI XÁC MINH").
- **Không còn lỗi/gap cụ thể** trong phạm vi P0: 8 ô matrix PARTIAL còn lại và 3 ô DEFER P1 đều là nghiệp vụ P1 **chưa triển khai**, đang fail-closed (BOD scoped reporting ADR-005, ownership service ADR-004).

## 6. Bất biến

- **KHÔNG merge PR #2.**
- **KHÔNG tự ký P0 GO/NO-GO** — thuộc T0/Owner (đủ 10 gate mới ký).
- KHÔNG sửa file của T1A/T1C/ADR/code/test/workflow ngoài phạm vi đóng gói đã duyệt.
- Không mở rộng kiểm thử khi không còn lỗi hoặc gap cụ thể.
