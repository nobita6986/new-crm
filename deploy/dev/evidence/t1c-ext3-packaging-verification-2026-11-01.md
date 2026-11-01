# EVIDENCE — T1C kiểm định độc lập gói đóng gói cuối (T1B) — Round 33

- **ROUND 33 (2026-11-01) — T1C tự xác minh GitHub API + tự tải/tự hash/tự parse artifact, KHÔNG tin số ghi sẵn.**

## 1. Run mới nhất (xác minh qua API trước khi sửa)

| Run | Status | head_sha | merge_sha | Kết luận |
|---|---|---|---|---|
| `#41251487302` (run_number **174**) | `success` (completed 2026-10-31T23:18:02Z) | `b5cae7dc92afb6453f69efce1ed4db4c53c8b06c` | `035a8016eb0e804cd26b368165543ac91b2792ce` | **MỚI NHẤT** |
| `#41250998995` (run_number 173) | `success` | `754d41c93fd76df3dacd621d17dd7cba67d12b71` | `e7f447f077af4a5fcb55864c9bc4825011c82a09` | Revision bàn giao trong `t1b-final-handover-2026-10-31.md` |

- PR #2: `state=open`, `merged=false`, `mergeable=true`, `head=b5cae7d`, `base=b382fe4` (`main` **chưa đổi**).
- `refs/pull/2/merge` = `035a8016…`; parents của `035a8016` = `[b382fe4…, b5cae7d…]` ⇒ artifact **đúng là merge ref PR #2** (head `b5cae7d` vào `main` `b382fe4`), không phải commit tự ý.
- `workflow-metadata.json` (run #174) → `commit_sha=035a8016eb0e804cd26b368165543ac91b2792ce` ⇒ **khớp** merge SHA T1C tự xác minh.
- **Run #174 là MỚI HƠN revision bàn giao của T1B** (run #173 / `754d41c`) ⇒ T1C kiểm định run mới nhất, không kiểm lại bản cũ.

## 2. Artifact — tự tải + tự hash

| Trường | Giá trị |
|---|---|
| Artifact id | `13061049294` (`p0-g007-evidence-41251487302`) |
| Size (B) | **141 006** (API `size_in_bytes=141006`) |
| Digest (GitHub API) | `sha256:42afa789fabdcf8d6f691af3c791d0a060e1c3d88598078a0e10ba7b60bca3b2` |
| **SHA-256 T1C tự hash zip** | `42AFA789FABDCF8D6F691AF3C791D0A060E1C3D88598078A0E10BA7B60BCA3B2` ✅ **KHỚP 100%** |

> **Lưu ý phương pháp:** lần tải đầu bằng `gh api … > file.zip` (PowerShell redirect) làm **hỏng binary** (284 196 B / hash `1405678C…` ≠ digest API) — T1C **loại bỏ** kết quả đó và tải lại bằng `curl.exe -o` (binary-safe) ⇒ 141 006 B, hash khớp API. Đây là bài học về provenance: **phải hash lại từ zip tải được, không được tin hash của lần tải lỗi.**

## 3. Tự parse độc lập (KHÔNG tin `gate_passed`)

### 3.1. Per-method gate

- `g007-per-method-gate.json`: `schema_version=2`, `gate=g007-per-method-junit`, `gate_passed=true`, `expected_total=99`, `observed_total=99`, `passing_total=99`, `counts={"PASS":99}`.
- 9 list lỗi (`missing_modules`, `empty_xml_files`, `missing_methods`, `failed_methods`, `errored_methods`, `skipped_methods`, `duplicate_methods`, `unexpected_cases`, `parse_errors`) — **rỗng `[]`**.
- `xml_module_binding` — 5/5 đúng ánh xạ module.

### 3.2. JUnit — T1C tự đếm **full-tree `<testcase>`**

| Module XML | Số `<testcase>` T1C tự đếm | FAIL | ERROR | SKIP |
|---|---:|---:|---:|---:|
| `test_permissions.junit.xml` | 18 | 0 | 0 | 0 |
| `test_security_remediation.junit.xml` | 12 | 0 | 0 | 0 |
| `test_security_remediation_v2.junit.xml` | 32 | 0 | 0 | 0 |
| `test_bt1c5_matrix_gaps.junit.xml` | 27 | 0 | 0 | 0 |
| `test_docshare_lifecycle.junit.xml` | 10 | 0 | 0 | 0 |
| **Tổng** | **99** | **0** | **0** | **0** |

- Auxiliary `auxiliary/test_error_response_normalization.junit.xml`: **8/8 PASS** (ngoài inventory 99).

### 3.3. HTTP transport probe — 18/18 PASS

`probe_result=PASS`, `failed_cases=[]`, T1C tự đếm lại `assertion.result` ⇒ **18/18 PASS**. `coverage_scope` **không đổi**: *"direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered"* ⇒ SEC-13 giữ giới hạn topology P0.

18 case: `sec13-guest` · `staff-login` · `sec13-user` · `csrf-page` · `sec14-get` · `sec14-post-nocsrf` · `sec14-post-badtok` · `sec14-post-ok` · `administrator-login` · `sec13-admin` · `sec16-permlevel-get-data` · `sec16-permlevel-sentinel-scan` · `sec26-staff-private` · `sec26-admin-private` · `sec26-activities-in-scope` · `staffc-login` · `sec26-activities-out-of-scope` · `sec26-activities-download-in-scope`.

**T1C tự đọc 4 observation của `sec26-activities-in-scope`** (`crm.api.activities.get_activities`, caller `staff.a@dev.local` in-scope, file `DEMO-CRM-LEAD-00001.txt`):

| Observation | `payload_entry_present` | `observation` | `file_url_present` | `value_sha256` |
|---|---|---|---|---|
| `sec26-activities-message-attachments` | true | `file_url_absent` | **false** | `null` |
| `sec26-activities-message-attachment-log` | true | `file_url_absent` | **false** | `null` |
| `sec26-activities-docinfo-attachments` | true | `file_url_absent` | **false** | `null` |
| `sec26-activities-docinfo-attachment-logs` | true | `file_url_absent` | **false** | `null` |

⇒ đường `get_activities` **vẫn redaction private `file_url`** ở tầng ứng dụng (không hồi quy so với Round 31/32).

### 3.4. File oracle — 9/9 PASS

`probe_result=PASS`, `failed_cases=[]`, `case_count=9`. T1C tự đọc từng case:

| Case | Kết quả T1C tự đọc |
|---|---|
| `client-get-out-of-scope-existing-file` | 404 `DoesNotExistError`, `echoes_supplied_file_id=false`, `contains_file_url=false` |
| `client-get-out-of-scope-unknown-id` | 404 `DoesNotExistError`, `echoes_supplied_file_id=false`, `contains_file_url=false` |
| `client-get-out-of-scope-indistinguishable` | `result=true` (existing vs unknown **không phân biệt được**) |
| `client-get-in-scope-positive` | 200, `private_file_url_present=true` (positive control) |
| `rest-get-out-of-scope-existing-file` | 404 `DoesNotExistError`, không echo id, không lộ URL |
| `rest-get-out-of-scope-unknown-id` | 404 `DoesNotExistError`, không echo id, không lộ URL |
| `rest-get-out-of-scope-indistinguishable` | `result=true` |
| `rest-get-in-scope-positive` | 200, `private_file_url_present=true` |
| `getdoc-out-of-scope-denial-scrubs-name-and-traceback` | 403 `PermissionError`, `identifier_echo=false`, `exception_sanitized=true` |

### 3.5. Remaining gaps — 74/74 PASS

`probe_result=PASS`, `failed_cases=[]`, `case_count=74`; T1C tự đếm `assertion.result` ⇒ **74/74 PASS**. `head_sha=b5cae7d`, `merge_sha=035a8016` — khớp provenance ở §1.

**SEC-16 (12 permlevel surfaces × in/out-of-scope)** — T1C tự đọc `sentinel_present`:

- 12 surface in-scope: `sentinel_present=false` (non-system không thấy field permlevel=1).
- 12 surface out-of-scope: `sentinel_present=false` (8 case 403, 4 case 200 nhưng vẫn không lộ sentinel).
- **3 system positive control** (`sec16-system-positive-control-frappe-client-get` / `-rest-resource-get` / `-desk-getdoc`): `sentinel_present=true` ⇒ **assertion non-vacuous**.

**`ra05-*` — actor vẫn là `Administrator`:** T1C đọc lại source probe @ `b5cae7d` (`deploy/dev/seed/ci_g007_remaining_gaps_probe.py`) — probe **không** login `crm.admin@dev.local` (user mang role `CSKH CRM Admin` trong `seed_demo.py`). ⇒ **2 ca `ra05-*` vẫn là positive control `RA-05-SM`, KHÔNG phải evidence của ô `RA-05`** ⇒ ô `RA-05` **giữ PARTIAL** (xem §5).

### 3.6. Module status & workflow metadata

`module-status.txt`: `probe_static exit=0` + 5 module `exit=0` + auxiliary `exit=0` + `per-method gate exit=0` + `remaining_gaps_http exit=0`.
`workflow-metadata.json`: **10/10** `workflow_outcomes` = `success` (`bootstrap`, `lint`, `seed`, `per_method`, `per_method_gate`, `http_transport`, `file_oracle_unit`, `file_oracle_http`, `remaining_gaps_http`, `backup_restore`); `redaction_replacements=4`; Frappe pin `d2efa796…`, CRM pin `0d3910b4…`.
T1C đọc `jobs` của run #174 qua API: **mọi step `conclusion=success`**, chỉ step "Show stack logs if a gate fails" = `skipped` (đúng thiết kế).

## 4. Đối chiếu regression run #173 → #174 (T1C tự tải cả hai artifact)

| Artifact | SHA-256 T1C tự hash | Kết quả |
|---|---|---|
| run #173 `13061551280` | `121C8F0A6FFE15A63F3D8E51CD693BF9D7B12ECA6F491B968091FC652EA22423` | khớp digest API mà T1B đã ghi ⇒ **bằng chứng Round 32/32b còn hiệu lực** |
| run #174 `13061049294` | `42AFA789FABDCF8D6F691AF3C791D0A060E1C3D88598078A0E10BA7B60BCA3B2` | khớp digest API |

- So `case-id` 3 probe: **http 18→18, file-oracle 9→9, remaining-gaps 74→74 — 0 ca thêm, 0 ca mất.**
- So `assertion.result` từng case: **0 ca nào đổi trạng thái** ⇒ **không regression** trên toàn bộ 101 ca runtime.
- So JUnit `<testcase>`: **99 → 99**, cùng phân bố 18/12/32/27/10.

## 5. Diff code giữa revision đã kiểm định (run #61) và run #174

- `git diff --stat e0ef02f b5cae7d` = **49 file, +4 067 / −93**.
- **Không đổi code app:** `git diff --stat e0ef02f b5cae7d -- apps/crm_cskh/crm_cskh/` chỉ có `translations/vi.csv` (+22 khóa i18n) — `guarded_crm.py` / `hooks.py` / `permissions.py` **nguyên trạng**.
- **Không đổi test app:** `git diff --stat e0ef02f b5cae7d -- apps/crm_cskh/crm_cskh/tests/` — **rỗng**.
- **Không đổi probe seed:** `git diff --stat e0ef02f b5cae7d -- deploy/dev/seed/` — **rỗng**.
- **Đổi đúng 1 dòng workflow** (`.github/workflows/p0.yml`, commit `c3035bd` "test(dev): gate fresh-session CRM bootstrap"): step `probe_static` nay chạy thêm `node deploy/dev/test-crm-vite-bootstrap.cjs` sau `unittest test_ci_http_probe_static`. File `deploy/dev/test-crm-vite-bootstrap.cjs` **đã tồn tại** trong revision `b5cae7d` (`git cat-file -e` OK) ⇒ step mới **không làm hỏng pipeline**, và run #174 vẫn `success` ở step này.
- **Đối chiếu source tại `b5cae7d` (T1C tự đọc, không suy đoán):** `hooks.py` `override_whitelisted_methods` **vẫn** có `crm.api.activities.get_activities → crm_cskh.api.guarded_crm.get_activities` (+ `get_linked_calls`/`get_linked_notes`/`get_linked_tasks`/`get_attachments`); `guarded_crm.py` **vẫn** có `_link_target_is_readable`, `_filter_linked_calls_for_scope`, `_filter_linked_calls_payload`, `_redact_private_file_urls`, `_redact_private_file_payload`, `get_activities` (gọi upstream rồi redact). ⇒ **cơ chế bảo mật mà Round 31/32 ký không bị sửa đổi**.

## 6. Privacy audit artifact (T1C tự quét)

- Quét toàn bộ 22 file artifact bằng regex `password|cookie|csrf|secret|token|/private/files/|C:\Users|/home/`: **không** có giá trị bí mật thô.
  - `http-transport.json`: `request/cookie` = `[REDACTED]`, `response/headers/Set-Cookie` = `[REDACTED]`, `X-Frappe-CSRF-Token` = `[REDACTED]`.
  - `/private/files/` chỉ xuất hiện **1 lần** ở `observations[5].endpoint` dưới dạng **placeholder** `"GET /private/files/<seeded private file>"` — không phải URL thật.
  - `csrf`/`CSRFTokenError` chỉ là **tên assertion/exception type**, không phải token.
- Artifact không lưu body thô của response ngoài-scope (chỉ `body_length`, `body_sha256`, `exc_type`, boolean) — đúng nguyên tắc privacy của Round 29–32.

## 7. Tally matrix — T1C tự đếm lại từ CẢ HAI bảng

T1C tự viết script đếm từ `docs/architecture/permission-matrix.md` §§2–§9 **và** bảng audit 71 ô trong `deploy/dev/evidence/t1c-security-verification.md` (parse ô theo ID `LEAD-xx/CL-xx/TK-xx/NT-xx/AT-xx/RA-xx/LS-xx/BD-xx`, đủ **71/71** ở mỗi bảng, không thừa không thiếu):

| Bảng | PASS | PARTIAL | NOT RUN | DEFER P1 | Tổng |
|---|---:|---:|---:|---:|---:|
| `permission-matrix.md` §§2–§9 | **59** | **9** | **0** | **3** | **71** |
| `t1c-security-verification.md` (bảng audit) | **59** | **9** | **0** | **3** | **71** |

- **mismatch = 0** ⇒ tài liệu **không còn mâu thuẫn nội bộ**.
- 9 PARTIAL: LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01 — đều là **nghiệvụ P1 chưa triển khai, đang fail-closed** ⇒ **không có gap đọc chéo / ghi trái quyền trong P0**.
- 3 DEFER P1: AT-05, RA-06, RA-07 — giữ nguyên theo quyết định Owner 2026-10-29.
- **SEC tally giữ 28/28 PASS** (SEC-13 giữ `PASS` **giới hạn topology P0** — `coverage_scope` của HTTP probe không đổi).

## 8. Verdict T1C — Round 33

- **SEC-16 → PASS (giữ).** 12 permlevel surfaces + 3 system positive control (`sentinel_present=true`) ⇒ assertion non-vacuous.
- **SEC-26 → PASS (giữ).** `get_activities` 4 leak site `file_url_absent`/`value_sha256=null`; residual File routes đo qua file-oracle 9/9 (404 indistinguishable, không echo id; `getdoc` 403 sanitized) + `sec26-*`/`at06-*` trong remaining-gaps.
- **SEC tally → 28/28 PASS.**
- **Matrix → 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (không đổi so với Round 32b) — vì run #174 **không thêm ca nào** và **không sửa code app**.
- **G0-07 → PASS (T1C KÝ, giữ nguyên verdict).**
- **G0-10 → PASS tại revision `b5cae7d` / run #41251487302** (T1C tự tải + tự hash + tự parse artifact).

## 9. Bất biến / KHÔNG làm

- **KHÔNG merge PR #2.** **KHÔNG** tự ký `P0` (GO/NO-GO P0 thuộc T0/Owner sau khi tổng hợp 10 gate).
- **KHÔNG** sửa code app, test app, probe seed, workflow, policy.
- **KHÔNG** sửa file của T1A/T1B/Owner/ADR.
- **KHÔNG** stage/commit/push — các file T1C-owned sửa ở Round 33 để **unstaged** cho T1A/T0 review.
- **KHÔNG** coi 62/74 static guards là runtime PASS (static tách riêng).
- Bàn giao kết luận cho **T1A** để đồng bộ `p0-exit-report.md` (P0 gate) với revision CI mới nhất.
