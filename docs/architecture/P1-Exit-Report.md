# P1 Exit Report — CRM-CSKH Backend P1

**Ngày cập nhật:** 2026-11-03
**Vai trò:** T1B (hoàn tất backend P1, hòa giải `L-16 … L-22` với T1C, đóng `L-25` phối hợp T1A, phân phối nền qua RQ, chứng minh runtime crash/rollback/retry, benchmark 10k & HTTP evidence)
**Nhánh:** `codex/p1-foundation` — worktree `D:/HRP-app/p1-foundation`
**Báo cáo đầu vào:**
- Baseline T1C: [`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md`](../../deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md) (`L-01 … L-15`)
- Delta Round 2 T1C: `deploy/dev/evidence/t1c-p1-verification-report-round2-2026-11-02.md` (`L-16 … L-22` theo hệ mã chuẩn của T1C + yêu cầu phân phối nền qua RQ) + yêu cầu `L-25` phối hợp tích hợp PR #5 với T1A
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md) (§10, §12.4, §12.5, §16)
**PR:** [#4 — feat(p1): CSKH P1 backend](https://github.com/nobita6986/new-crm/pull/4) — **OPEN, KHÔNG merge, KHÔNG tự ký GO P2**
**Các mốc đã xác minh trước revision này:**
- `3025495f58bc0391c3f8d092af500b0566a5f038`: CI P1 `#41423954824` (`174/174 PASS`), CI P0 `#41423954877` (`99/99 PASS`, auxiliary `8/8`, HTTP `18/18`, file-oracle `9/9`, remaining-gaps `74/74`).
- `64b7486f29ad712106d167dfaea50e3b060a6845`: CI P1 `#41567123312` (`184/184 PASS` + `52/52 HTTP PASS`), CI P0 `#41567123348` (`99/99 PASS`).
- `c49f7f89d18cfbc3c21a3970d66b07c5d6c11279`: CI P1 `#41582468780` (`199/199 PASS` + `75/75 HTTP PASS`), CI P0 `#41582468667` (`99/99 PASS`).
**Dữ liệu & môi trường:** chỉ dữ liệu giả trên các site cô lập (`p1fresh.localhost`, `p1chaos.localhost`, `p1bench.localhost`, `p1http.localhost`) thuộc stack `crm-cskh-p1-dev-*`; không đụng `p1.localhost` đang phục vụ Owner và không xóa Docker volume.

---

## 0. Tóm tắt

Trên nhánh `codex/p1-foundation`, Backend P1 đã đóng toàn bộ **15/15** lỗi baseline `L-01 … L-15`, hoàn tất hòa giải và đóng **7/7** lỗi **`L-16 … L-22`**, hoàn tất **`L-25`** (áp mapping `name`/`source` có sanitize khi tạo `CRM Lead` mới, chỉ dùng placeholder khi thiếu tên hợp lệ, không ghi đè `CRM Lead` canonical đã tồn tại, ghi quan hệ `CSKH Import Batch Lead` để lọc kho chính xác cho cả khách chưa có `Work Item` và khách được reuse qua nhiều batch), đồng thời giữ nguyên **`crm_cskh.api.distribution.execute` chạy nền qua RQ hiện có**:

* **Suite P1 riêng** ([`deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json)): **8 module / 203 method** (tăng từ `199` lên `203` nhờ bổ sung 4 method `TestL25ImportMappingAndBatchLink`), tách biệt hoàn toàn với inventory **99 method** của P0 ([`deploy/dev/seed/g007_test_inventory.json`](../../deploy/dev/seed/g007_test_inventory.json) giữ nguyên `99`);
* **Runtime Chaos / Rollback / Retry (`P1 §12.4`):** tiêm lỗi hoàn toàn từ tầng test/harness (`unittest.mock.patch` + tiến trình con chết cứng `os._exit(137)` cho **cả** `import_worker` lẫn `distribution_worker`), **0 nhánh gây lỗi trong code production**, **0 lệnh `db.set_value(..., "QUEUED")` nhảy cóc state machine**, hậu kiểm trực tiếp trên MariaDB (**11/11 unit PASS** & **4/4 kịch bản harness PASS**);
* **HTTP Transport P1 + RQ Workers (`evidence_class = "http"`):** [`deploy/dev/seed/ci_p1_http_probe.py`](../../deploy/dev/seed/ci_p1_http_probe.py) gọi HTTP thật tới `bench serve` đi kèm **RQ worker `short` và `long` chạy nền thật**, đăng nhập 5 actor nghiệp vụ + `Guest`, kiểm chứng trực tiếp `L-16 … L-22`, `L-25`, File-oracle P0, import 250 dòng từ `confirm` lần đầu và phân phối nền 20 khách + double-submit + polling `get_run` đối chiếu SQL (**86/86 case PASS**);
* **Benchmark 10.000 dòng & RQ Enqueue (`P1 §12.4`):** giữ nguyên số đo thực tế 2 đợt × 10.000 dòng trên `p1bench.localhost` và bổ sung đo thực tế thời gian HTTP enqueue bất đồng bộ (`api_enqueue_seconds = 0,222s`, `status = "queued"`) tách khỏi thời gian thực thi nền của worker (`~2,0–2,1 items/s`);
* **API & Schema contract P1** ([`api-contract-p1.md`](./api-contract-p1.md), [`schema-p1.md`](./schema-p1.md)) đã cập nhật đầy đủ `L-25` (`CSKH Import Batch Lead`, `imports.create_preview` mapping `phone`/`name`/`source`, `customers.customer_pool`, `reporting.customer_pool`) để **T1A** tích hợp trực tiếp vào PR #5 không xung đột merge.

Tổng hợp kết quả kiểm chứng:

| Hạng mục | Lớp bằng chứng | Kết quả | Bằng chứng |
|---|---|---|---|
| Suite P1 (8 module, **203 method**) | `service_unit_test` (JUnit gate) | **203/203 PASS** | [`evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json) |
| Chaos / rollback / retry (unit + AST guard) | `service_unit_test` + `os_process_crash` | **11/11 PASS** | [`evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml) |
| Runtime Chaos Harness (`p1chaos.localhost`) | `runtime_chaos_harness` (`exit 137` import + distribution & DB postcheck) | **4/4 kịch bản PASS** | [`evidence/p1-runtime-20261102/chaos/p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json) |
| HTTP transport + RQ worker probe (`p1http.localhost`) | `http` (5 actor thật + `Guest` + RQ `short`/`long` + đối chiếu SQL) | **86/86 PASS**, 0 rò bí mật/PII | [`evidence/p1-runtime-20261102/http/p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) |
| Benchmark 10k import + 10k phân phối + RQ enqueue (`p1bench.localhost`) | `runtime_benchmark` (2 đợt × 10k + mixed/RQ capacity) | **Số đo thực tế** (`20.200+` dòng, `0` lỗi, `0` trùng; `api_enqueue = 0,222s`; xem §2.8) | [`evidence/p1-runtime-20261102/benchmark/`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/) |
| Suite P0 core (inventory giữ nguyên **99**) | `service_unit_test` (JUnit gate) | **99/99 PASS** | [`evidence/p1-runtime-20261102/p0/g007-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/p0/g007-per-method-gate.json) |
| Auxiliary P0 (3 module ngoài inventory, **15 method**) | `service_unit_test` (gồm `test_error_response_normalization` 8/8 trên `hooks.py` mới) | **15/15 PASS** | [`evidence/p1-runtime-20261102/p0/auxiliary/`](../../deploy/dev/evidence/p1-runtime-20261102/p0/auxiliary/) |
| `bench migrate` × 2 (`p1fresh.localhost`) | `migration_idempotency` | exit 0 cả hai lần, không lỗi/không đổi dữ liệu | [`evidence/p1-runtime-20261102/junit-p1/migrate-1.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-1.txt), [`migrate-2.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-2.txt) |
| Race đa tiến trình N=2 / N=5 (import & distribution) | `os_process_race` | 1 bản ghi thắng, `0` trùng (unique index MariaDB) | [`evidence/p1-t1b-20261102/race/`](../../deploy/dev/evidence/p1-t1b-20261102/race/) |
| Backup → restore sang site mới (`p1restore2.localhost`) | `backup_restore` | counts **khớp 100%**, suite P1 PASS | [`evidence/p1-t1b-20261102/restore/`](../../deploy/dev/evidence/p1-t1b-20261102/restore/) |
| Lint + format (`ruff==0.14.0`), 72 P0 AST guards & SHA-256 manifest | `static_gate` | **PASS** (`0` cảnh báo, `30/30` LF SHA-256, `0` rò bí mật/PII) | [`evidence/p1-runtime-20261102/SHA256SUMS.txt`](../../deploy/dev/evidence/p1-runtime-20261102/SHA256SUMS.txt) |

### 0.1. Chi tiết các suite JUnit trên `p1fresh.localhost`

| Suite | Module | Method | Kết quả |
|---|---|---:|---|
| P1 | `crm_cskh.tests.test_p1_api_permissions` | 30 | PASS |
| P1 | `crm_cskh.tests.test_p1_chaos` | 11 | PASS |
| P1 | `crm_cskh.tests.test_p1_concurrency` | 10 | PASS |
| P1 | `crm_cskh.tests.test_p1_distribution` | 21 | PASS |
| P1 | `crm_cskh.tests.test_p1_identity` | 13 | PASS |
| P1 | `crm_cskh.tests.test_p1_imports` | 17 | PASS |
| P1 | `crm_cskh.tests.test_p1_ownership` | 17 | PASS |
| P1 | `crm_cskh.tests.test_p1_regressions` | 84 | PASS |
| **P1 tổng** | **8 module** | **203** | **203/203 PASS** |
| P0 core | `crm_cskh.tests.test_permissions` | 18 | PASS |
| P0 core | `crm_cskh.tests.test_security_remediation` | 12 | PASS |
| P0 core | `crm_cskh.tests.test_security_remediation_v2` | 32 | PASS |
| P0 core | `crm_cskh.tests.test_docshare_lifecycle` | 10 | PASS |
| P0 core | `crm_cskh.tests.test_bt1c5_matrix_gaps` | 27 | PASS |
| **P0 core tổng** | **5 module** | **99** | **99/99 PASS** |
| Auxiliary | `crm_cskh.crm_cskh.tests.test_health` | 4 | PASS |
| Auxiliary | `crm_cskh.crm_cskh.tests.test_poc_custom_field` | 3 | PASS |
| Auxiliary | `crm_cskh.tests.test_error_response_normalization` | 8 | PASS |
| **Auxiliary tổng** | **3 module** | **15** | **15/15 PASS** |

---

## 1. Bảng đóng lỗi `L-01 … L-15`, Hòa giải `L-16 … L-22` (Hệ mã chuẩn T1C) và Phân phối Nền RQ

### 1.1. Baseline `L-01 … L-15`

| Mã | Lỗi T1C | Nguyên nhân gốc | Bản sửa | Trạng thái & Test hồi quy |
|---|---|---|---|---|
| **L-01** | `reconcile()` đọc `assigned_to` (cột không tồn tại) trên `CSKH Assignment History` và cộng lịch sử toàn hệ thống | Cột của lịch sử là `to_user`; truy vấn không lọc campaign | `services/distribution.py::reconcile` đọc `h.to_user`, nối `CSKH Work Item` theo `work_item` và lọc `wi.campaign = %s`; trả đủ `distinct_leads` | **FIXED** — `TestL01Reconcile` (3 method) |
| **L-02** | `hashlib.sha256` nhận `str` ⇒ `TypeError` khi `File.get_content()` trả `str` | Không chuẩn hóa `str`/`bytes` | `services/import_service.py::_checksum` chuẩn hóa về `bytes` trước khi băm | **FIXED** — `TestL02Checksum` (4 method) |
| **L-03** | Mapping trả **giá trị ô** thay vì **tên cột**; mapping nhiều field đọc nhầm cột tên | `_resolve_column_name` trả `cells[column]` | Hàm trả **tên cột**; ưu tiên field chuẩn `phone`; preview và worker dùng chung hàm | **FIXED** — `TestL03ColumnResolution` (5 method) |
| **L-04** | SQL alias sai: `` `wi.assigned_to` `` bị MariaDB hiểu là tên bảng | Backtick bao quanh cả alias | Backtick chỉ bọc tên cột (`wi.\`assigned_to\``) + kết hợp bản sửa `L-17` cho `CRM-ADMIN` | **FIXED** — `TestL04PoolQuery` + `TestL17CrmAdminReportingScope` |
| **L-05** | Dashboard rò toàn hệ thống hoặc lệch scope theo actor | Scope chưa đồng nhất trên mọi card | Mọi card, `per_user`, `assignment_events`, `identity_count` áp scope trong SQL (kết hợp bản sửa `L-17` & `L-22`) | **FIXED** — `TestL05DashboardScope`, `TestL17CrmAdminReportingScope`, `TestL22LeaderUnassignedAndUniqueCustomers` |
| **L-06** | Bộ lọc `campaign` / `date_from` / `date_to` bị nhận rồi bỏ qua | Filter chỉ áp cho events | Filter `campaign` và ngày áp cho **mọi** card, `per_user`, `identity_count` và events | **FIXED** — `TestL06CampaignFilter` (4 method) |
| **L-07** | Leader đọc Rule/Run bằng cách so tên campaign với tên node cây | Hai namespace khác nhau | `p1_permissions.py` so `team_scope` với node của **subtree** (`lft`/`rgt`) | **FIXED** — `TestL07LeaderRead` (6 method) |
| **L-08** | Manual reassign/revoke ghi `distribution_run` sai/rỗng | Không tra nguồn giao đầu tiên | `services/ownership.py::_original_distribution_run` giữ nguồn từ event `assign` đầu tiên | **FIXED** — `TestL08AuditTrail` (5 method) |
| **L-09** | Patch `cskh_is_placeholder_name` không idempotent; worker không đánh dấu placeholder | Thiếu kiểm tra trạng thái & mark | Patch idempotent; worker đánh dấu `cskh_is_placeholder_name = 1` cho lead mới tạo | **FIXED** — `TestL09PlaceholderField` (4 method) |
| **L-10** | `capacity_limit = 0` (không giới hạn) bị gộp sai qua nhiều campaign bằng `max()` | Gộp theo user thay vì `(user, campaign)` | `reporting._per_user_breakdown` gom theo `(user, campaign)`; `0` ⇒ `capacity = null` | **FIXED** — `TestL10Capacity` (6 method) |
| **L-11** | Hồ sơ `P1-Exit-Report.md`, API contract, chaos & benchmark 10k | — | Tài liệu này + [`api-contract-p1.md`](./api-contract-p1.md) + gói `deploy/dev/evidence/p1-runtime-20261102/` (`SHA256SUMS.txt` 30 file LF) | **FIXED** (phần Backend; ghép SHA tích hợp UI thuộc bước của T1A/T1C) |
| **L-12** | Việt hóa UI P1 (`/cskh/*`) | — | Đã nạp **+219 dòng** bản dịch P1 vào `translations/vi.csv` phía Backend; phần màn hình UI `/cskh/*` và bọc dịch JS thuộc phạm vi PR UI của **T1A** | **OPEN (Phạm vi T1A UI — tách biệt khỏi Backend PR #4)** |
| **L-13** | Sanitization formula/HTML CSV/XLSX & chống rò SĐT trong log/error | — | `sanitize_cell` + `write_error_artifact` (`is_private=1`) + redact PII | **FIXED** — `TestL13Sanitization` (6 method) |
| **L-14** | `pool_list.total` không lọc theo scope khi phân trang | Bộ lọc `total` khác `items` | `items` và `total` dùng chung `_pool_scope_filter` (kể cả `CRM-ADMIN` sau `L-17`) | **FIXED** — `TestL04PoolQuery` + `p1-http-l17-pool-list-admin-matches-db` |
| **L-15** | `frappe.get_all` bỏ qua row-level permission | Dùng API DB-level cho dữ liệu trả ra | Dùng `frappe.get_list` trên toàn bộ đường đọc hướng người dùng (`reporting`, `eligibility`, `ownership`, `campaigns`) | **FIXED** — `TestL15NoPermissionBypass` (6 method) |

### 1.2. Hòa giải `L-16 … L-22` theo đúng hệ mã chuẩn của T1C + Phân phối nền RQ

> **Ghi chú hòa giải ký hiệu:** Trong bản `64b7486` trước đây, bảng nội bộ từng dùng nhãn `L-16..L-22` cho các lỗi T1B tự phát hiện khi viết chaos/benchmark. Dưới đây chuẩn hóa **100% theo hệ mã `L-16 … L-22` của T1C** tại báo cáo `t1c-p1-verification-report-round2-2026-11-02.md` (các bản sửa nội bộ lúc làm chaos/resume được tách riêng xuống bảng `B-01 … B-04` ở §1.3 để không trùng mã).

| Mã (Chuẩn T1C) | Khoảng trống T1C chỉ ra trên `64b7486` | Bản sửa trên revision hiện tại | Bằng chứng Regression (`service_unit_test`) & HTTP + RQ (`http`) | Trạng thái |
|---|---|---|---|---|
| **L-16** | HTTP `4xx` trả đúng status code nhưng thiếu khóa mã lỗi ổn định `code`/`p1_code` trên JSON envelope | Thêm [`p1_errors.py`](../../apps/crm_cskh/crm_cskh/p1_errors.py) (`record_p1_error`, `clear_p1_error_context`, `normalize_p1_error_response`), nối sau `crm_cskh.errors.normalize_error_response` trong [`hooks.py`](../../apps/crm_cskh/crm_cskh/hooks.py). Bỏ qua mọi route trực tiếp của `File` (`_request_targets_file`) để **giữ nguyên tuyệt đối File-oracle `404` byte-for-byte của P0** | - Unit: `TestL16ErrorEnvelopeAndFileOracle` (2 method), `test_error_response_normalization` (`8/8 PASS`)<br>- HTTP: `p1-http-l16-not-found-code`, `p1-http-l21-revoke-invalid-bool-rejected` (`INVALID_REQUEST`), `p1-http-*-denied-*` (`FORBIDDEN`), `p1-http-l16-file-oracle-real-private` == `p1-http-l16-file-oracle-missing` (`sha256` & `content_length` khớp tuyệt đối) | **FIXED** |
| **L-17** | `reporting._scope()` không xét `CSKH CRM Admin` ⇒ `CRM-ADMIN` bị hạ về `scope="self"`, toàn bộ 10 card dashboard & `pool_list` trả `0` | Tại [`services/reporting.py`](../../apps/crm_cskh/crm_cskh/services/reporting.py) (`_scope`), cấp `{"scope": "system", "users": None, "teams": None, "pii": True}` cho role nghiệp vụ `CSKH CRM Admin` (**tuyệt đối không gán `System Manager`**) | - Unit: `TestL17CrmAdminReportingScope` (`test_crm_admin_without_system_manager_receives_system_scope_matching_db`)<br>- HTTP + SQL: `p1-http-l17-dashboard-admin-matches-db`, `p1-http-l17-pool-list-admin-matches-db` (dữ liệu dương khác `0`, khớp 10/10 card & `pool_list.total` với SQL trực tiếp) | **FIXED** |
| **L-18** | `imports.confirm()` lần đầu chỉ chuyển `DRY_RUN → CONFIRMED`, làm RQ worker thật ném `ValidationError: cannot move from CONFIRMED to RUNNING` | Tại [`services/import_service.py`](../../apps/crm_cskh/crm_cskh/services/import_service.py) (`confirm`), khóa `SELECT ... FOR UPDATE`, đi trọn chuyển trạng thái hợp lệ `DRY_RUN → CONFIRMED → QUEUED` ngay ở **lần gọi đầu tiên**, enqueue với `deduplicate=True`; gỡ sạch `db.set_value(..., "QUEUED")` khỏi toàn bộ test/harness/benchmark | - Unit: `TestL18FirstConfirmToWorker`, `TestImportWorker` (`test_p1_imports.py`)<br>- HTTP + RQ thật: `p1-http-imports-confirm-admin` → `p1-http-imports-get-status-admin` (`COMPLETED`) và `p1-http-l18-first-confirm-250rows` → `p1-http-l19-l20-completed-250rows` (`0` `db.set_value`, `1` lần gọi `confirm` duy nhất) | **FIXED** |
| **L-19** | Import `> 200` dòng không được làm rơi các dòng hợp lệ nằm sau lát cắt `preview[:200]` | `import_service._classify` trả đầy đủ `resolvable` & `invalid` cho toàn bộ file (chỉ cắt `preview` 200 dòng cho UI); `import_worker` xử lý toàn bộ `resolvable` và bảo toàn các bộ đếm phân loại gốc qua các lần crash-resume | - Unit: `TestImportWorker.test_over_200_rows_imports_every_valid_row_and_records_every_error` (`250` dòng), `TestL19L20LargeImportAndErrorArtifact` (`260` dòng)<br>- HTTP + RQ thật: `p1-http-l19-l20-completed-250rows` (`250` dòng = `245` lead hợp lệ mới + `5` lỗi, tạo đủ `245/245` canonical lead trên DB) | **FIXED** |
| **L-20** | Import `> 200` dòng phải ghi đủ mọi dòng lỗi (kể cả sau dòng 200) vào `error_artifact` private và không đếm lỗi hai lần | `import_worker` nạp toàn bộ `classification["invalid"]` (không giới hạn 200 dòng), khử trùng theo `row_number` (`_dedupe_error_rows`), và tách `preview_invalid_rows` khỏi lỗi phát sinh thêm ở worker | - Unit: `TestImportWorker.test_over_200_rows_imports_every_valid_row_and_records_every_error`, `TestL19L20LargeImportAndErrorArtifact`<br>- HTTP + RQ thật: `p1-http-l19-l20-completed-250rows` (`error_rows == 5`, `error_artifact` `is_private=1` chứa đúng dòng lỗi `[12, 95, 188, 215, 251]`) | **FIXED** |
| **L-21** | `api.ownership.revoke` dùng `bool(return_to_pool)` khiến `"false"` và `"0"` qua `application/x-www-form-urlencoded` thành `True` (`queued` thay vì `revoked`) | Thêm [`api/_params.py`](../../apps/crm_cskh/crm_cskh/api/_params.py) (`parse_bool`) và áp dụng cho `api/ownership.py::revoke`, `services/ownership.py::revoke`, `api/campaigns.py::save_rule`. Pin chặt 8 giá trị `False`/`"false"`/`0`/`"0"` và `True`/`"true"`/`1`/`"1"`; từ chối giá trị rác với `400 INVALID_REQUEST` | - Unit: `TestL21RevokeBooleanParsing` (2 method kiểm tra đủ 8 biến thể + hậu kiểm `status`, `lead_owner`, `CSKH Active Claim`, `ToDo`, `DocShare`, `CSKH Assignment History`)<br>- HTTP (JSON + Form + SQL): `p1-http-l21-revoke-json-false-bool`, `form-false-str`, `json-zero-int`, `form-zero-str`, `json-true-bool`, `form-true-str`, `json-one-int`, `form-one-str`, `invalid-bool-rejected` (9 case HTTP PASS) | **FIXED** |
| **L-22** | Dashboard `Leader` (`scope="team"`) bỏ sót `revoked_work_items`, `closed_work_items`, `active_claims` của Work Item có `assigned_to IS NULL`; lệch định nghĩa `unique_customers` giữa `BOD` và `Leader` | Tại [`services/reporting.py`](../../apps/crm_cskh/crm_cskh/services/reporting.py), hợp nhất mệnh đề `_work_item_scope_clause` (`assigned_to ∈ team_users` **hoặc** `assigned_to IS NULL AND campaign ∈ readable_campaigns`) cho mọi card Work Item, `active_claims`, `unique_customers`, `identity_count`; thống nhất `unique_customers = COUNT(DISTINCT wi.lead)` trên toàn bộ vai trò | - Unit: `TestL22LeaderUnassignedAndUniqueCustomers`, `TestL05DashboardScope`<br>- HTTP + SQL: `p1-http-l22-dashboard-leader-matches-db`, `p1-http-l22-dashboard-bod-matches-db`, `p1-http-l17-dashboard-admin-matches-db` (đối chiếu khớp 100% SQL khi có đủ hàng `assigned`, `queued`, `revoked`, `closed`) | **FIXED** |
| **L-25** | Import chưa áp `name`/`source` từ `mapping` cho `CRM Lead` mới (luôn gán placeholder), chưa lưu quan hệ `Import Batch ↔ Lead` nên lọc kho theo `batch` bỏ sót khách chưa có `Work Item` và khách canonical được reuse qua nhiều batch | - Mở rộng `resolve_import_columns` (`phone`/`name`/`source`, hỗ trợ cả XLSX dòng đầu `None`), sanitize formula/HTML, chỉ dùng `"Chưa rõ tên"` (`cskh_is_placeholder_name=1`) khi thiếu tên hợp lệ, link an toàn `CRM Lead Source`, chặn `409 CONFLICT` khi đổi `mapping` trên cùng `idempotency_key`.<br>- Giữ nguyên 100% dữ liệu nghiệp vụ của `CRM Lead` canonical đã tồn tại (không ghi đè).<br>- Thêm DocType độc lập `CSKH Import Batch Lead` (`UNIQUE (import_batch, lead)`, `outcome = created \| reused`), đối soát `created_rows`/`reused_rows` từ DB và cung cấp `reporting.customer_pool` / `customers.customer_pool` cho T1A. | - Unit: `TestL25ImportMappingAndBatchLink` (4 method phủ mapping CSV + sparse XLSX, placeholder, sanitization + `409 CONFLICT`, non-overwrite canonical Lead, lọc kho đa batch không cần Work Item cho Admin/BOD/Leader/Staff, crash-retry & multi-thread MariaDB race)<br>- HTTP + RQ thật: `p1-http-l25-*` (11 case HTTP + đối soát SQL trực tiếp qua 2 batch liên tiếp & `customers.customer_pool`) | **FIXED** |
| **Mục 5 (RQ Distribution)** | `api.distribution.execute` chạy đồng bộ (~80 phút / 10.000 khách), chạm timeout HTTP | Chuyển `api.distribution.execute` sang `distribution.enqueue_run` + RQ worker [`jobs/distribution_worker.py`](../../apps/crm_cskh/crm_cskh/jobs/distribution_worker.py) (`queue="long"`, `deduplicate=True`), thêm cột `progress` & `lease_expires_at` + trạng thái `queued` trên `CSKH Distribution Run`, checkpoint mỗi chunk (`50` dòng), recheck quyền/`rule_version`/eligibility/capacity lúc chạy, chống double-submit và phục hồi sau `os._exit(137)` | - Unit: `TestBackgroundDistributionRQ` (4 method), `TestDistributionWorkerCrashAndRecovery` (`os._exit(137)` + lease recovery)<br>- Chaos harness: `S4_distribution_worker_hard_kill_exit_137_mid_batch_and_lease_recovery`<br>- HTTP + RQ thật: `p1-http-rq20-distribution-preview` → `execute-first` (`queued`) → `execute-double-submit` → `get-run-completed` (`10/6/4` khớp DB) | **FIXED** |

### 1.3. Các bản sửa nội bộ bổ trợ cho Chaos / Worker Recovery (`B-01 … B-04`)

| Mã nội bộ | Vấn đề kỹ thuật đã đóng | Bản sửa | Kiểm chứng |
|---|---|---|---|
| **B-01** | `P1Error` cần cả `http_status` lẫn `http_status_code` để Frappe không trả HTTP 500 | `services/errors.py` khai báo đồng thời `http_status` và `http_status_code` trên `P1Error` và 11 lớp con | `TestL16ErrorEnvelopeAndFileOracle` + `ci_p1_http_probe.py` (75/75 HTTP) |
| **B-02** | Con trỏ `progress` trong `import_worker.py` có nguy cơ vượt qua dòng lỗi ở chunk sớm | Neo `safe_progress = max(resume_from, blocked_at - 1)` | `TestImportWorkerCrashAndRecovery.test_failed_row_in_early_chunk_blocks_progress_advance_and_recovers_on_retry` |
| **B-03** | Khi resume `CSKH Import Batch` sau crash giữa chừng, `_classify` chạy lại nhìn thấy các lead đã tạo ở lần 1 như `duplicate_in_crm` | Giữ nguyên các bộ đếm phân loại preview ban đầu (`total_rows`, `valid_rows`, `new_rows`, `duplicate_rows`) trên `CSKH Import Batch` khi `total_rows` đã được chốt từ bước `create_preview` | `TestL19L20LargeImportAndErrorArtifact` |
| **B-04** | Thu hồi `CSKH Distribution Run` bị kẹt `running` sau khi worker chết cứng (`os._exit(137)`) và hết hạn lease | Cho phép chuyển trạng thái `running → queued` trong `RUN_STATUS_TRANSITIONS` khi `lease_expires_at` hết hạn, cấp lease mới và chỉ xử lý các lead chưa được giao | `TestDistributionWorkerCrashAndRecovery` + `S4` trong `p1_chaos_harness.py` |

---

## 2. Bằng chứng runtime

### 2.1. Site cô lập và lệnh tái lập

Mọi kiểm chứng chạy trên container `crm-cskh-p1-dev-bench-1` (MariaDB 11.8, Python 3.14, Frappe v15)
với các site tách biệt theo mục đích (`p1fresh.localhost`, `p1chaos.localhost`, `p1http.localhost`,
`p1bench.localhost`); hoàn toàn không tác động tới `p1.localhost` của stack `crm-cskh-p1-*`.

```bash
# 1. Lint & inventory check (fail-closed)
ruff check apps/crm_cskh deploy/dev/seed/p1_test_inventory.py deploy/dev/seed/ci_p1_http_probe.py
ruff format --check apps/crm_cskh deploy/dev/seed/p1_test_inventory.py deploy/dev/seed/ci_p1_http_probe.py
python3 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json

# 2. Suite P1 (8 module, 199 method) + JUnit gate riêng
bench --site p1fresh.localhost --verbose run-tests --module crm_cskh.tests.test_p1_<module> \
  --junit-xml-output /tmp/p1-evidence-r3/junit-p1/<slug>.junit.xml
python3 deploy/dev/seed/ci_junit_gate.py \
  --inventory deploy/dev/seed/p1_test_inventory.json \
  --xml-dir /tmp/p1-evidence-r3/junit-p1 --out /tmp/p1-evidence-r3/junit-p1/p1-per-method-gate.json

# 3. Suite P0 core (giữ nguyên inventory 99 method) + Auxiliary (15 method)
bench --site p1fresh.localhost --verbose run-tests --module crm_cskh.tests.test_error_response_normalization

# 4. Runtime chaos harness (4 kịch bản, gồm os._exit(137) cho cả import & distribution worker)
P1_CHAOS_OUTPUT=/tmp/p1-evidence-r3/chaos/p1-chaos-report.json \
  bench --site p1chaos.localhost execute crm_cskh.tools.p1_chaos_harness.run_all

# 5. HTTP P1 + RQ worker transport probe (75 case, 5 actor thật + Guest + bench worker short/long)
bench --site p1http.localhost serve --port 18089 --noreload --nothreading &
bench worker --queue short &
bench worker --queue long &
P1_HTTP_SITE=p1http.localhost P1_HTTP_BASE_URL=http://127.0.0.1:18089 \
  FRAPPE_STREAM_LOGGING=1 python3 deploy/dev/seed/ci_p1_http_probe.py

# 6. Benchmark 10.000 dòng + supplementary mixed/RQ capacity check
bench --site p1bench.localhost execute crm_cskh.tools.p1_benchmark.run_supplementary_checks
```

### 2.2. Kết quả per-method JUnit gate

```text
g007-per-method-gate: PASS     # P1 (deploy/dev/seed/p1_test_inventory.json)
  expected_total : 203
  observed_total : 203
  passing_total  : 203
  PASS           : 203

g007-per-method-gate: PASS     # P0 core (deploy/dev/seed/g007_test_inventory.json)
  expected_total : 99
  observed_total : 99
  passing_total  : 99
  PASS           : 99

auxiliary-total: 15/15 PASS    # test_health (4), test_poc_custom_field (3), test_error_response_normalization (8)
```

### 2.3. Race đa tiến trình (N OS process thật)

`crm_cskh.tools.p1_process_race` spawn `sys.executable -c` — mỗi worker là một tiến trình OS độc lập
với kết nối MariaDB riêng (trọng tài là unique index của MariaDB, không phải GIL Python):

| Kịch bản | N | Kết quả đối soát DB |
|---|---:|---|
| `import` | 2 | `identity_rows=1`, `lead_rows=1`, `duplicate_leads=0` |
| `import` | 5 | `identity_rows=1`, `lead_rows=1`, `duplicate_leads=0` |
| `distribution` | 2 | `claims=1`, `work_items=1`, `duplicate_items=0` (1 worker thua unique index `1062`) |
| `distribution` | 5 | `claims=1`, `work_items=1`, `duplicate_items=0` (4 worker thua unique index `1062`) |

### 2.4. Migrate idempotent & Backup / Restore

* `bench --site p1fresh.localhost migrate` chạy 2 lần liên tiếp cả sau khi bổ sung schema `progress` / `lease_expires_at` / trạng thái `queued` của `CSKH Distribution Run` ([`migrate-1.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-1.txt), [`migrate-2.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-2.txt)): đều `exit 0`, không lỗi, không đổi dữ liệu.
* Backup `p1fresh.localhost` → restore sang site mới `p1restore2.localhost` ([`evidence/p1-t1b-20261102/restore/`](../../deploy/dev/evidence/p1-t1b-20261102/restore/)): số dòng khớp 100%.

### 2.5. Không có đường tiêm lỗi trong production

* Đã xóa vĩnh viễn `apps/crm_cskh/crm_cskh/services/chaos.py` và không có bất kỳ tham số hay biến môi trường gây lỗi nào trong `api/`, `services/`, `jobs/`, `crm_cskh/doctype/`.
* `TestZeroProductionFaultInjection` quét AST toàn bộ các gói production (bao gồm cả `jobs/distribution_worker.py`, `api/_params.py`, `p1_errors.py`) để chặn mọi import/lời gọi `chaos`, `maybe_fail`, `fault_inject`, `os._exit`.

### 2.6. Chứng minh Crash / Rollback / Retry bằng runtime (`P1 §12.4`)

Được chứng minh qua **11 unit/process test** trong [`test_p1_chaos.py`](../../apps/crm_cskh/crm_cskh/tests/test_p1_chaos.py)
([`test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml))
và **4 kịch bản end-to-end** của [`p1_chaos_harness.py`](../../apps/crm_cskh/crm_cskh/tools/p1_chaos_harness.py)
trên `p1chaos.localhost` ([`p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json)):

| Kịch bản (`P1 §12.4`) | Cách tiêm lỗi (chỉ trong test/harness) | Kết quả hậu kiểm trực tiếp trên MariaDB |
|---|---|---|
| **1. Lỗi sau khi tạo `Work Item` + `Active Claim`, trước khi hoàn tất ownership** (`S1`) | `mock.patch` ném lỗi tại `ownership.assign` và giữa `ownership._ensure_share` | Savepoint rollback trọn vẹn (`orphan_work_items = 0`, `orphan_active_claims = 0`, `owner_mismatches = 0`, `todo_mismatches = 0`, `docshare_mismatches = 0`, `partial_history_rows = 0`); resume đạt đúng `5/3/2`. |
| **2. Lỗi giữa giao dịch `reassign`** | `mock.patch` ném lỗi ở `_ensure_share` sau khi đóng history cũ & cancel ToDo cũ | Giao dịch `reassign` rollback hoàn toàn, giữ nguyên owner cũ, `ownership_end IS NULL`, ToDo cũ `Open`. |
| **3. Lỗi sau DB commit nhưng trước thông báo; retry không tạo trùng** (`S2`) | `mock.patch` làm `CRM Notification.insert` ném lỗi sau khi ownership đã `commit` | Assignment giữ nguyên `completed`; gọi retry liên tiếp 2 lần bù đủ `3` thông báo, **0 bản ghi trùng**. |
| **4. Import worker chết cứng `os._exit(137)` giữa batch (đi qua `confirm()` thật, 0 `db.set_value`)** (`S3`) | Tiến trình con OS chạy `import_worker.run_batch` (`chunk_size = 3`) sau `confirm()` lần 1, gọi `os._exit(137)` ngay sau chunk 1 | Sau crash: `status = "RUNNING"`, `progress = 4`, `3` identity đã commit. Sau phục hồi: `status = "COMPLETED"`, `attempts = 2`, `9/9` lead & identity, `0` trùng. |
| **5. Distribution RQ worker chết cứng `os._exit(137)` giữa batch + bảo vệ lease & phục hồi** (`S4`) | Tiến trình con OS chạy `distribution_worker.run_distribution` (`chunk_size = 3`) trên 10 khách, gọi `os._exit(137)` ở `progress = 6` | Khi lease còn hiệu lực: double-submit `enqueue_run` **không** enqueue trùng và worker phụ không cướp job. Khi lease hết hạn: `enqueue_run` chuyển `running → queued`, worker phục hồi đúng từ `progress = 6` → `10/10` (`completed`), tỷ lệ đúng `5/3/2`, `0` orphan/duplicate. |

### 2.7. Bằng chứng HTTP P1 + RQ Worker với actor thật (`86/86 PASS`)

[`p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) thực thi **86 case HTTP thật** tới `bench serve` kết hợp **2 tiến trình `bench worker` (`short` và `long`) thật**:

* **52 case gốc:** phủ đăng nhập, CSRF dương/âm, phân quyền dương/âm của `Guest`, `crm.admin@dev.local`, `bod.viewer@dev.local`, `team1.leader@dev.local`, `staff.a@dev.local`, `staff.b@dev.local` trên cả 7 nhóm API P1.
* **34 case mới xác minh trực tiếp `L-16 … L-22`, `L-25` và Phân phối nền RQ:**
  - `L-16`: kiểm tra `code` / `p1_code` ổn định trên `400 INVALID_REQUEST`, `403 FORBIDDEN`, `404 NOT_FOUND`, `409 CONFLICT`, và xác nhận File-oracle P0 (`/api/resource/File/<real_private>` vs `/api/resource/File/<missing>`) trả `404` trùng khớp cả `content_length` lẫn `sha256`;
  - `L-17`: `crm.admin@dev.local` gọi `dashboard.distribution` và `customers.pool_list` trả `scope == "system"`, số liệu dương `> 0` và khớp 100% truy vấn SQL trực tiếp;
  - `L-18` + `L-19` + `L-20`: upload CSV **250 dòng** (`245` hợp lệ + `5` lỗi gồm các dòng `215, 251 > 200`), gọi `imports.confirm` **1 lần duy nhất** (`status == "QUEUED"`), chờ RQ worker `short` hoàn tất `COMPLETED_WITH_ERRORS`, đối soát DB có đủ `245/245` canonical lead, `error_rows == 5`, `error_artifact` (`is_private=1`) chứa đúng 5 dòng lỗi `[12, 95, 188, 215, 251]`;
  - `L-21`: gửi đủ 8 biến thể JSON & `application/x-www-form-urlencoded` (`false`, `"false"`, `0`, `"0"`, `true`, `"true"`, `1`, `"1"`) cộng 1 giá trị sai (`"maybe"` → `400 INVALID_REQUEST`) tới `ownership.revoke`, đối chiếu trực tiếp trạng thái DB `revoked`/`queued`, `lead_owner`, `CSKH Active Claim`, `ToDo`, `DocShare`, `CSKH Assignment History`;
  - `L-22`: tạo dữ liệu đa trạng thái (`assigned`, `queued`, `revoked`, `closed`) trên campaign của Team 1 và ngoài Team 1, đối chiếu HTTP `dashboard.distribution` của `team1.leader@dev.local` và `bod.viewer@dev.local` khớp 100% SQL trên cả 10 card (gồm `revoked_work_items`, `closed_work_items`, `active_claims` hàng chưa gán người và `unique_customers`);
  - `L-25`: thực thi 2 đợt import liên tiếp qua HTTP + RQ worker với header tùy biến `SDT_KH`/`TEN_KH`/`KENH_KH`, kiểm chứng `409 CONFLICT` khi đổi `mapping` trên cùng `idempotency_key`, sanitize formula/HTML, placeholder chỉ dùng khi thiếu tên hợp lệ, không ghi đè `CRM Lead` canonical ở Batch 2, và lọc `GET customers.customer_pool?batch=<batch1|batch2>` cho `Admin`, `BOD` (ẩn PII), `Staff` (`403 FORBIDDEN`) khi khách chưa có `Work Item`;
  - **Phân phối nền RQ (Mục 5):** tạo campaign 20 khách tỷ lệ `50/30/20`, gọi `POST distribution.execute` trả ngay `status == "queued"` + `run_id`, bắn tiếp double-submit ngay lập tức (không tạo run/job trùng), poll `GET distribution.get_run` đến khi RQ worker `long` hoàn tất `completed`, đối chiếu `progress == 20`, `total == 20`, `assigned == {10, 6, 4}` khớp 100% SQL.

### 2.8. Benchmark 10.000 dòng & Độ trễ Enqueue Phân phối Nền (`P1 §12.4`)

| Chỉ số đo | Đợt 1 (`0 → 10.000` lead mới) | Đợt 2 (`10.000 → 20.000` lead trên nền 10k sẵn có) | Lô bổ sung (100 dòng hỗn hợp + phân phối nền RQ có trần capacity) |
|---|---|---|---|
| **Artifact** | [`p1-benchmark-10k-fresh.json`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/p1-benchmark-10k-fresh.json) | [`p1-benchmark-10k-on-20k.json`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/p1-benchmark-10k-on-20k.json) | [`p1-benchmark-mixed-capacity.json`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/p1-benchmark-mixed-capacity.json) |
| **Thời gian `create_preview` + `import_worker`** | `5,731 s` + `241,462 s` (**`41,4 dòng/s`**) | `5,825 s` + `353,653 s` (**`28,3 dòng/s`**) | `0,352 s` + `2,976 s` (`50/20/15/15`, `COMPLETED_WITH_ERRORS`, qua `confirm()` thật) |
| **Độ trễ API `distribution.execute` (RQ enqueue)** | *(đo ở cơ chế đồng bộ cũ trước khi tách RQ)* | *(đo ở cơ chế đồng bộ cũ trước khi tách RQ)* | **`0,222 s` (`api_initial_status = "queued"`)** |
| **Thời gian worker thực thi phân phối** | **`4.802,380 s`** (`~80,0 phút`, **`2,1 items/s`**) | **`4.879,758 s`** (`~81,3 phút`, **`2,0 items/s`**) | `10,169 s` (`100` items qua `distribution_worker`, `~9,8 items/s`) |
| **Kết quả Fairness & Đối soát DB** | `5.000 / 3.000 / 2.000` (`0` lỗi, `0` trùng) | `5.000 / 3.000 / 2.000` (`0` lỗi, `0` trùng) | **`30 / 42 / 28`** (`progress = 100/100`, `0` lỗi, `0` trùng) |
| **Peak RAM (RSS tiến trình Python)** | `88,4 MiB → 281,9 MiB` | `67,4 MiB → 277,8 MiB` | `67,3 MiB → 132 MiB` |

* **Ý nghĩa vận hành sau khi chuyển sang RQ:** Request HTTP `POST distribution.execute` nay chỉ mất **`~0,22 giây`** để khóa kiểm tra và đẩy job vào RQ `long` (loại bỏ hoàn toàn lỗi HTTP gateway timeout `60–120s`). Phần xử lý ORM nặng (`~80–110` SQL/item, `~2,0–2,1 items/s`, tương đương `~80 phút` cho 10.000 khách trên máy DEV) chạy an toàn trong tiến trình `bench worker --queue long` với checkpoint mỗi `50` khách để UI poll tiến độ qua `distribution.get_run`.

### 2.9. Báo cáo bàn giao thay đổi API contract cho T1A (UI)

Chi tiết đầy đủ đã cập nhật tại [`api-contract-p1.md` §2, §3.2, §3.9, §3.10, §3.14, §3.18 và §6.1](./api-contract-p1.md). Tóm tắt các điểm T1A cần cập nhật/đồng bộ trên UI:

1. **`POST crm_cskh.api.distribution.execute` chuyển sang trả sớm qua RQ (`status = "queued"`):**
   - Không còn giữ kết nối HTTP chờ phân phối xong. Response của `execute` và `get_run` nay có thêm `total` (`int`), `progress` (`int`), `error` (`string | null`), và `status` có thêm `"queued"` / `"running"` / `"failed"`.
   - UI cần poll `GET crm_cskh.api.distribution.get_run?run_id=<run_id>` khi `status in ("queued", "running")` để hiển thị tiến độ `progress / total` và kết quả cộng dồn `assigned` / `created`, dừng poll khi `status in ("completed", "completed_with_errors", "failed")`.
2. **Mã lỗi ổn định trên HTTP `4xx` (`L-16`):** Mọi lỗi `P1Error` hiện có sẵn `code` và `p1_code` ở top-level JSON (`"FORBIDDEN"`, `"INVALID_REQUEST"`, `"RULE_CHANGED"`, `"PREVIEW_EXPIRED"`, `"CAPACITY_EXCEEDED"`, `"NOT_FOUND"`, ...).
3. **`POST crm_cskh.api.imports.confirm` (`L-18`):** Trả `status = "QUEUED"` ngay từ lần gọi đầu tiên và tự kích hoạt RQ worker `short`.
4. **Boolean form/JSON (`L-21`) & Scope Dashboard (`L-17`, `L-22`):** Hoạt động nhất quán, không đổi tên tham số của UI.
5. **Import `name`/`source` mapping & Lọc kho theo `batch` (`L-25` — tích hợp PR #5):**
   - `imports.create_preview` áp dụng đầy đủ `mapping: {phone, name, source}` (mà `import.js` của PR #5 đang gửi sẵn), trả thêm `mapping` & `resolved_mapping`, chỉ dùng placeholder khi thiếu tên hợp lệ, không ghi đè `CRM Lead` canonical đã tồn tại, và ghi `CSKH Import Batch Lead`.
   - `pool_ui.list` trên PR #5 chỉ cần ủy quyền trực tiếp sang `reporting.customer_pool(...)` (hoặc dùng `reporting.lead_in_batch_clause` + `reporting.work_item_in_batch_clause`; xem [`api-contract-p1.md` §6.2](./api-contract-p1.md)) để lọc `batch` chính xác cho cả khách `eligible` chưa có `Work Item` và khách được reuse qua nhiều batch.

---

## 3. Danh mục Trạng thái `FIXED` / `OPEN` Bàn giao

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| `L-01 … L-11`, `L-13 … L-15` (Baseline Backend) | **FIXED** | Đã có unit test hồi quy + HTTP/SQL evidence |
| `L-16` (Error envelope `code`/`p1_code` + bảo toàn File-oracle P0) | **FIXED** | `TestL16ErrorEnvelopeAndFileOracle` + `test_error_response_normalization` (`8/8`) + HTTP File-oracle `404` khớp SHA-256 |
| `L-17` (`CSKH CRM Admin` `scope="system"`, không cấp `System Manager`) | **FIXED** | `TestL17CrmAdminReportingScope` + HTTP `CRM-ADMIN` khớp SQL khác `0` |
| `L-18` (`imports.confirm()` lần đầu vào thẳng `QUEUED`, 0 `db.set_value`) | **FIXED** | `TestL18FirstConfirmToWorker` + HTTP 250 dòng qua RQ worker thật |
| `L-19` & `L-20` (Import `> 200` dòng đủ dữ liệu, đủ `error_artifact`, không đếm lỗi đúp) | **FIXED** | `TestL19L20LargeImportAndErrorArtifact` (`260` dòng) + HTTP `250` dòng qua RQ worker thật |
| `L-21` (Parse boolean `revoke` / `save_rule` trên JSON & `form-urlencoded`) | **FIXED** | `TestL21RevokeBooleanParsing` + 9 case HTTP JSON/form + đối soát DB |
| `L-22` (Dashboard `Leader` hàng `assigned_to IS NULL` + thống nhất `unique_customers`) | **FIXED** | `TestL22LeaderUnassignedAndUniqueCustomers` + HTTP `Leader`/`BOD`/`Admin` khớp SQL |
| `L-25` (Mapping `name`/`source` khi tạo Lead mới, không ghi đè Lead cũ, liên kết `Import Batch ↔ Lead` & lọc kho đa batch) | **FIXED (Backend PR #4)** | `TestL25ImportMappingAndBatchLink` (4 method) + 11 case HTTP/RQ/SQL; bàn giao contract/hàm `reporting.customer_pool` để T1A nối vào `pool_ui.py` trên PR #5 |
| Phân phối nền bằng RQ hiện có (Mục 5) | **FIXED** | Enqueue sớm, chunk checkpoint `50`, DB lock + lease, recheck quyền/rule/capacity, chống double-submit, crash `exit 137` recovery |
| `L-12` / `T1-20` / `G1-12` (Màn hình UI `/cskh/*` & nối `pool_ui.py` trên PR #5) | **OPEN (Thuộc T1A — PR #5)** | Tách khỏi phạm vi Backend PR #4; T1B đã bàn giao đầy đủ contract tại [`api-contract-p1.md` §6.1–§6.2](./api-contract-p1.md) |

---

## 4. Phần giới hạn thực tế đã ghi nhận

1. **Thông lượng ORM của worker phân phối nền trên lô 10.000 khách:** Dù HTTP `POST distribution.execute` đã trả ngay trong `~0,22s` (không còn nghẽn HTTP timeout), tiến trình nền `distribution_worker` vẫn thực thi đầy đủ vòng đời Frappe Document (`Work Item`, `Active Claim`, `CRM Lead`, `DocShare`, `ToDo`, `Assignment History`, `CRM Notification`) với thông lượng thực tế `~2,0–2,1 items/s` (`~80–81 phút` cho 10.000 khách trên 1 RQ worker máy DEV; bộ nhớ ổn định `~282 MiB`, `job_timeout = 14.400s`). Trong môi trường vận hành cần đảm bảo tiến trình `bench worker --queue long` và `short` luôn chạy nền.
2. **Chưa đánh index `mobile_no` trên `tabCRM Lead`:** Tuân thủ ràng buộc P0 ADR-001 không sửa trực tiếp schema bảng gốc Frappe CRM; thông lượng `import_worker` giảm từ `41,4 dòng/s` (`0 → 10k`) xuống `28,3 dòng/s` (`10k → 20k`).
3. **Phạm vi giao vận HTTP probe:** `ci_p1_http_probe.py` kiểm chứng HTTP thật tới `bench serve` + `bench worker`, chưa phủ lớp reverse proxy (Nginx/TLS) và CORS đa domain.
4. **Tải file lỗi & phân trang sâu:** `error_artifact` là `File` private tải qua route chuẩn của Frappe; danh sách dùng `page`/`page_size` theo offset; bộ lọc ngày dashboard tách `CSKH Work Item.creation` và `CSKH Assignment History.event_at` theo thiết kế.
5. **Quản trị nhánh & cổng giai đoạn:** PR #4 giữ trạng thái **OPEN** (không tự merge), không tự ký **GO P2** — chờ T1A cập nhật UI theo contract phân phối nền, T1C kiểm định độc lập trên SHA mới và T0 quyết định.

---

## 5. Tài liệu & Artifact liên quan

* [`api-contract-p1.md`](./api-contract-p1.md) — API contract P1 (20 endpoint, mã lỗi `code`/`p1_code`, phân phối nền RQ, `L-25` mapping & batch pool, ghi chú bàn giao T1A)
* [`schema-p1.md`](./schema-p1.md) — 9 DocType P1 (gồm `CSKH Import Batch Lead`), unique constraint & lookup index
* [`../../deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json) — inventory P1 riêng (**203 method** trên 8 module)
* [`../../deploy/dev/seed/g007_test_inventory.json`](../../deploy/dev/seed/g007_test_inventory.json) — inventory P0 giữ nguyên (**99 method** trên 5 module)
* Bộ bằng chứng runtime đầy đủ của revision này: [`../../deploy/dev/evidence/p1-runtime-20261102/`](../../deploy/dev/evidence/p1-runtime-20261102/) (kèm [`SHA256SUMS.txt`](../../deploy/dev/evidence/p1-runtime-20261102/SHA256SUMS.txt))
* Bộ bằng chứng baseline (race đa tiến trình & backup/restore): [`../../deploy/dev/evidence/p1-t1b-20261102/`](../../deploy/dev/evidence/p1-t1b-20261102/)
