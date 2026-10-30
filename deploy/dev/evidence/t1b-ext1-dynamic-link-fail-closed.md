# T1B-ext+1 — Fail-closed Dynamic Link + Call Log orphan cho `get_linked_calls` / `get_activities`

**Ngày:** 2026-10-30 (UTC+7)
**Worktree:** `D:\HRP-app\pr2-ext1`
**PR head gốc:** `0f2538179b87b898152e8b29977d7a7de963b2a1`
**PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) — `open`, **KHÔNG merge**
**Chủ sở hữu:** T1B (code/test/evidence)

---

## 0. Prompt (công việc T0 giao)

1. Rà bản vá hiện tại trên **đúng response thô**. Fixture phải kiểm cả nhãn
   Contact, số điện thoại `from`/`to`, `recording_url`, nội dung Note/Task và tên
   Dynamic Link trỏ tới Lead/Deal ngoài scope. Thêm positive control chứng minh
   Call Log tham chiếu Lead hợp lệ vẫn xuất hiện (200 + list rỗng **không** được
   tính là positive control).
2. Thử Call Log có **nhiều Dynamic Link**: một link tới Lead trong scope, một link
   tới Lead/Deal ngoài scope. Upstream join mọi link của Call Log; response không
   được trả `link_name` ngoài scope. Nếu giữ Call Log thiếu `reference_doctype`,
   phải chứng minh mọi trường trả về an toàn; nếu không, loại bản ghi đó cho
   non-system caller theo fail-closed và giữ positive control hợp lệ.
3. Kiểm cả `get_linked_calls` và `get_activities` (upstream gọi `get_linked_calls`
   nội bộ). Giữ inventory JUnit 99 method; dùng HTTP probe hiện có, không thêm
   method thứ 100, không nới assertion.
4. Sau khi sửa: lint/static guards, đủ 5 module JUnit, File-oracle và toàn bộ
   HTTP probe trên cùng revision. Xác minh cleanup fixture, `git diff --check`,
   quét secret trong artifact.
5. Commit/push đúng file T1B lên PR #2, chờ CI mới. Tải artifact gốc, đối chiếu
   SHA-256, head/merge SHA, gate JSON với 5 XML, ghi counts thực tế. Chỉ bàn giao
   cho T1C khi ca Dynamic Link âm/dương đều đạt và CI xanh.

---

## 1. Phát hiện khi rà bản vá (trước khi sửa)

Bản vá sẵn có trong worktree `pr2-ext1` còn 3 lỗ hổng:

1. **`_call_log_in_scope` giữ row có reference in-scope nhưng `link_name` ngoài
   scope.** Upstream `crm.api.activities.get_linked_calls` (CRM v1.87.0
   `0d3910b4…`) join Dynamic Link thành nhiều row cho cùng một Call Log. Hàm cũ
   kiểm `reference_doctype`/`reference_docname` trước rồi `return True`, nên row
   Dynamic Link trỏ Lead ngoài scope (nhưng cùng reference in-scope) vẫn lọt ⇒
   lộ `link_name` ngoài scope.
2. **Call Log orphan (thiếu `reference_doctype`) được giữ qua Dynamic Link nhưng
   `from`/`to` raw phone không bị loại.** `parse_call_log` → `get_call_contact`
   rơi vào `get_contact_by_phone_number` khi reference rỗng ⇒ lộ Contact ngoài
   scope theo số điện thoại; đồng thời `from`/`to` thô vẫn được trả nguyên văn.
3. **Probe `dl01` thiếu phủ:** chưa kiểm `from`/`to` phone, chưa có case
   multi-Dynamic-Link (in-scope + out-of-scope), và case `get_activities` chưa
   kiểm `OUT_OF_SCOPE_LEAD_NAME`.

---

## 2. Thay đổi

### 2.1 `apps/crm_cskh/crm_cskh/api/guarded_crm.py`

- Viết lại `_call_log_in_scope` theo **fail-closed**:
  - Bỏ mọi row mang `link_doctype`/`link_name` (row do upstream join Dynamic Link).
  - Chỉ giữ Call Log có `reference_doctype`/`reference_docname` trỏ Lead/Deal mà
    caller đọc được.
  - Call Log orphan (chỉ nối Dynamic Link) bị loại vì không chứng minh được
    `from`/`to`, `note`, `recording_url` và nhãn contact (phone lookup) an toàn.
- Xoá `_scoped_contact_for_call` (không còn nhánh phone-lookup cho kept call).
- `_filter_linked_calls_for_scope`: giữ shape `calls`/`notes`/`tasks`; gỡ
  `recording_url` trỏ `/private/files/` cho kept call; lọc `notes`/`tasks` bằng
  `frappe.has_permission` (cùng tiêu chí hook `linked_lead_has_permission`).
- `get_linked_calls` và `get_activities` đều áp bộ lọc cho non-system caller.

### 2.2 `deploy/dev/seed/ci_g007_remaining_gaps_probe.py`

- `dl01_dynamic_link_case`: thêm marker `from_phone`, dùng `from_phone` riêng cho
  Call Log orphan (được bỏ) để pin rằng `from`/`to` orphan không lộ.
- Thêm fixture `multi_link_call`: reference → Lead in-scope + nhiều Dynamic Link
  (một link in-scope, một link out-of-scope) — pin `link_name` ngoài scope không
  được trả về.
- Mở rộng leak markers cho cả `get_linked_calls` lẫn `get_activities`:
  `contact_marker`, `contact_phone`, `from_phone`, `note_marker`, `task_marker`,
  `recording_url`, `OUT_OF_SCOPE_LEAD_NAME`.

---

## 3. Verification (cục bộ, cùng revision)

| Hạng mục | Kết quả |
|---|---|
| `ruff check` (2 file sửa) | PASS |
| `ruff format --check` | PASS |
| Static AST guards `test_ci_http_probe_static.py` | **62/62 PASS** |
| 5 module JUnit (site `p0-test.localhost`) | **99/99 PASS** (18+12+32+27+10) |
| `ci_junit_gate.py` | `gate_passed=true`, 99/99, 0 missing/failed/errored |
| File-oracle HTTP (`ci_file_oracle_probe.py`) | PASS (9 case) |
| File-oracle unit (`test_error_response_normalization`) | PASS (8 method, ngoài inventory 99) |
| HTTP transport (`ci_http_transport_probe.py`) | 18/18 PASS |
| Remaining-gaps (`ci_g007_remaining_gaps_probe.py`) | 62/62 PASS, `failed_cases=[]` |
| dl01 linked-calls negative | PASS — `leaked_marker_count=0`, `out_of_scope_lead_echo=false` |
| dl01 linked-calls positive control | PASS — `positive_call_visible=true` |
| dl01 activities | PASS — `leaked_marker_count=0` |
| Cleanup fixture | Call Log của probe sạch (`CRM Call Log: []`); Contact/Note/Task còn sót là của script debug `t1b_ext1_dbg*.py` (không thuộc probe) |
| `git diff --check` | PASS |
| Secret scan artifact | 0 password / 0 URL private-public / 0 cookie / 0 sid / 0 CSRF value |

---

## 4. Commit & CI

| Hạng mục | Giá trị |
|---|---|
| Commit 1 | `8e6aa9f` — `T1B-ext+1(SEC-16/SEC-27): fail-closed Dynamic Link + orphan Call Log` |
| Commit 2 | `afa74b3` — `T1B-ext+1: ruff format probe ci_g007_remaining_gaps` |
| Push | `origin codex/p0-t1a-g001-g002-g009` (`0f253817..afa74b3`) |
| CI run | [`#41036251872`](https://github.com/nobita6986/new-crm/actions/runs/41036251872) — **`success`** |
| Run head SHA | `afa74b382282e9dcf5908b51aad83d4c330e21af` |
| Merge SHA (metadata `commit_sha`) | `173a1759e41f2e3c313badd46802323338fa17b4` |
| `frappe_sha` / `crm_sha` | `d2efa796…` / `0d3910b4…` (khớp pin) |

### 4.1 Artifact gốc (tự tải + đối chiếu)

| Hạng mục | Giá trị |
|---|---|
| Artifact id / name | `12967408725` / `p0-g007-evidence-41036251872` |
| Size | 139779 B |
| Digest GitHub API | `sha256:e415edc6dfbe78c9720b36ac1d004bff3bb3822fa7a71357a44241c11fced012` |
| SHA-256 tính lại từ ZIP | `e415edc6dfbe78c9720b36ac1d004bff3bb3822fa7a71357a44241c11fced012` — **khớp** |
| `g007-per-method-gate.json` | `gate_passed=true`, `expected_total=99`, `observed_total=99`, `passing_total=99`, `counts={PASS:99}`, failed/errored/missing `[]` |
| 5 JUnit XML | `18 + 12 + 32 + 27 + 10 = 99` `<testcase>` |
| `remaining-gaps.json` | `probe_result=PASS`, `case_count=62`, `failed_cases=[]` |
| dl01 trong artifact CI | 4 case PASS; negative `leaked=0`/`echo=false`; positive `visible=true`; activities `leaked=0` |

---

## 5. Trạng thái governance (không đổi)

| Hạng mục | Trạng thái |
|---|---|
| Inventory method | **99** (không thêm method thứ 100) |
| SEC tally | **26 PASS + 2 PARTIAL (SEC-16, SEC-26) = 28/28** (theo T1C lần 30) |
| `SEC-26` | `PARTIAL` (giữ nguyên — H-RFC-1(a) chưa đo, nhánh Deal chưa đo, `content_hash` chưa assert) |
| `G0-07` | `PARTIAL` / CHƯA KÝ (giữ nguyên) |
| `P0` | `NO-GO` (giữ nguyên) |
| RFC `get_activities` | `OPEN` (giữ nguyên) |
| PR #2 | `open` — **KHÔNG merge** |

**Bàn giao T1C:** các ca Dynamic Link âm/dương đều đạt và CI xanh ⇒ đủ điều kiện
bàn giao. **Không** đề nghị G0-07 PASS.

> ⚠️ **Workspace chính** `D:\HRP-app\new-crm` vẫn ở `e67e9ea` (cũ hơn PR head
> `0f253817`); công việc này thực hiện ở worktree `D:\HRP-app\pr2-ext1`. Nếu
> tiếp tục SEC-26, phải đồng bộ từ PR #2 head, không dùng workspace gốc.
