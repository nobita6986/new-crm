# P1 Exit Report — CRM-CSKH Backend P1

**Ngày cập nhật:** 2026-11-03
**Vai trò:** T1B (hoàn tất backend P1, hòa giải `L-16 … L-22` với T1C, đóng `L-25`, bảo vệ `L-23`, `L-26` & `L-29` trên toàn bộ đường backend + generic Frappe API/Desk, kiểm tra trực tiếp mutation eligibility `assign`/`reassign`, phân phối nền qua RQ, chứng minh runtime crash/rollback/retry, benchmark 10k & HTTP evidence)
**Nhánh:** `codex/p1-foundation` — worktree `D:/HRP-app/p1-foundation`
**Báo cáo đầu vào:**
- Baseline T1C: [`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md`](../../deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md) (`L-01 … L-15`)
- Delta Round 2/3 T1C: `deploy/dev/evidence/t1c-p1-verification-report-round2-2026-11-02.md` (`L-16 … L-22` + `L-23`, `L-25`, `L-26` + yêu cầu phân phối nền qua RQ) và `deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-a9094b1.json` (`L-29` + `L-23` trên `pool_list` + direct mutation eligibility) phối hợp tích hợp PR #5 với T1A
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md) (§10, §12.4, §12.5, §16)
**PR:** [#4 — feat(p1): CSKH P1 backend](https://github.com/nobita6986/new-crm/pull/4) — **OPEN, KHÔNG merge, KHÔNG tự ký GO P2**
**Các mốc đã xác minh trước revision này:**
- `3025495f58bc0391c3f8d092af500b0566a5f038`: CI P1 `#41423954824` (`174/174 PASS`), CI P0 `#41423954877` (`99/99 PASS`, auxiliary `8/8`, HTTP `18/18`, file-oracle `9/9`, remaining-gaps `74/74`).
- `64b7486f29ad712106d167dfaea50e3b060a6845`: CI P1 `#41567123312` (`184/184 PASS` + `52/52 HTTP PASS`), CI P0 `#41567123348` (`99/99 PASS`).
- `c49f7f89d18cfbc3c21a3970d66b07c5d6c11279`: CI P1 `#41582468780` (`199/199 PASS` + `75/75 HTTP PASS`), CI P0 `#41582468667` (`99/99 PASS`).
- `c997df76b9a29a9f8d8be4a4ffd0875035762672`: CI P1 `#41589010999` (`203/203 PASS` + `86/86 HTTP PASS`), CI P0 `#41589011008` (`99/99 PASS`).
- `2235757b79bb9f1a7fb3ae2957b28deb088185b9`: CI P1 (`205/205 PASS` + `103/103 HTTP PASS`), CI P0 (`99/99 PASS`).
- `77e07dac7948c156039bc6a9746fe5d451fc0d71`: CI P1 `#41604730446` (`208/208 PASS` + `158/158 HTTP PASS`), CI P0 `#41604730477` (`99/99 PASS`).
**Dữ liệu & môi trường:** chỉ dữ liệu giả trên các site cô lập (`p1fresh.localhost`, `p1chaos.localhost`, `p1bench.localhost`, `p1http.localhost`) thuộc stack `crm-cskh-p1-dev-*`; không đụng `p1.localhost` đang phục vụ Owner và không xóa Docker volume.

---

## 0. Tóm tắt

Trên nhánh `codex/p1-foundation`, Backend P1 đã đóng toàn bộ **15/15** lỗi baseline `L-01 … L-15`, hoàn tất hòa giải và đóng **7/7** lỗi **`L-16 … L-22`**, hoàn tất **`L-25`**, bảo vệ trực tiếp **`L-23`** tại cả `reporting.customer_pool` / `customers.customer_pool` lẫn `reporting.pool_list` / `customers.pool_list`, đóng **`L-26`** trên toàn bộ đường backend, đóng **`L-29`** (quyền cấp DocType + Document + Field `permlevel = 1` trên `CSKH Campaign`, `CSKH Assignment Rule`, `CSKH Distribution Run` qua REST, `frappe.client.*` và Desk), cưỡng chế trực tiếp điều kiện hợp lệ trên **`ownership.assign` / `ownership.reassign`**, và giữ nguyên **`crm_cskh.api.distribution.execute` chạy nền qua RQ hiện có**:

* **Suite P1 riêng** ([`deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json)): **8 module / 209 method** (bổ sung `TestL29GenericFrappeApiPermissionAndRawRunProtection`, `TestDirectOwnershipMutationEligibility` và mở rộng `TestL23BackendCustomerPoolCrossTeamIsolation` lên 2 method phủ cả 3 API Kho trước phân trang), tách biệt hoàn toàn với inventory **99 method** của P0 ([`deploy/dev/seed/g007_test_inventory.json`](../../deploy/dev/seed/g007_test_inventory.json) giữ nguyên `99`);
* **Runtime Chaos / Rollback / Retry (`P1 §12.4`):** tiêm lỗi hoàn toàn từ tầng test/harness (`unittest.mock.patch` + tiến trình con chết cứng `os._exit(137)` cho **cả** `import_worker` lẫn `distribution_worker`), **0 nhánh gây lỗi trong code production**, **0 lệnh `db.set_value(..., "QUEUED")` nhảy cóc state machine**, hậu kiểm trực tiếp trên MariaDB (**11/11 unit PASS** & **4/4 kịch bản harness PASS**);
* **HTTP Transport P1 + RQ Workers (`evidence_class = "http"`):** [`deploy/dev/seed/ci_p1_http_probe.py`](../../deploy/dev/seed/ci_p1_http_probe.py) gọi HTTP thật tới `bench serve` đi kèm **RQ worker `short` và `long` chạy nền thật**, đăng nhập 6 actor nghiệp vụ (`Admin`, `BOD`, `Leader 1`, `Leader 2`, `Staff A`, `Staff B`) + `Guest`, kiểm chứng trực tiếp `L-16 … L-23`, `L-25`, `L-26`, `L-29` (`/api/resource/*`, `frappe.client.*`, `getdoc`, `reportview.get`, `search_link`, `validate_link`, filter-oracle `permlevel = 1`), direct mutation eligibility trên `ownership.assign`/`reassign`, File-oracle P0, import 250 dòng từ `confirm` lần đầu và phân phối nền 20 khách + double-submit + polling `get_run` đối chiếu SQL (**167/167 case PASS**);
* **Benchmark 10.000 dòng & RQ Enqueue (`P1 §12.4`):** giữ nguyên số đo thực tế 2 đợt × 10.000 dòng trên `p1bench.localhost` và bổ sung đo thực tế thời gian HTTP enqueue bất đồng bộ (`api_enqueue_seconds = 0,222s`, `status = "queued"`) tách khỏi thời gian thực thi nền của worker (`~2,0–2,1 items/s`);
* **API & Schema contract P1** ([`api-contract-p1.md`](./api-contract-p1.md), [`schema-p1.md`](./schema-p1.md)) đã cập nhật đầy đủ `L-23`, `L-25`, `L-26`, `L-29` và direct mutation eligibility để **T1A** tích hợp trực tiếp vào PR #5 không xung đột merge.

Tổng hợp kết quả kiểm chứng:

| Hạng mục | Lớp bằng chứng | Kết quả | Bằng chứng |
|---|---|---|---|
| Suite P1 (8 module, **209 method**) | `service_unit_test` (JUnit gate) | **209/209 PASS** | [`evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json) |
| Chaos / rollback / retry (unit + AST guard) | `service_unit_test` + `os_process_crash` | **11/11 PASS** | [`evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml) |
| Runtime Chaos Harness (`p1chaos.localhost`) | `runtime_chaos_harness` (`exit 137` import + distribution & DB postcheck) | **4/4 kịch bản PASS** | [`evidence/p1-runtime-20261102/chaos/p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json) |
| HTTP transport + RQ worker probe (`p1http.localhost`) | `http` (6 actor thật + `Guest` + RQ `short`/`long` + đối chiếu SQL) | **167/167 PASS**, 0 rò bí mật/PII | [`evidence/p1-runtime-20261102/http/p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) |
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
| P1 | `crm_cskh.tests.test_p1_regressions` | 90 | PASS |
| **P1 tổng** | **8 module** | **209** | **209/209 PASS** |
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
| **L-23 (Backend)** | `reporting.customer_pool` / `customers.customer_pool` và `reporting.pool_list` / `customers.pool_list` chưa chặn đầy đủ lịch sử `revoked`/`closed` (hoặc `revoke(return_to_pool=True)` tạo `queued` ở Đội A rồi khách được claim/assign sang Đội B) hoặc lệch bộ đếm `total`/`unique_customers` trên `pool_list` | Đồng bộ 100% policy với T1A ngay trong [`services/reporting.py`](../../apps/crm_cskh/crm_cskh/services/reporting.py): áp `_scoped_wi_predicate`, `_scoped_ac_predicate`, `_team_lead_authorization_clauses(scope, "l")`, `filter_team_authorized_leads` cho **cả** `reporting.customer_pool` lẫn `reporting.pool_list`. Tại `pool_list`, nối `INNER JOIN tabCRM Lead l ON l.name = wi.lead`, tách bí danh subquery `c_psf`, và dùng **chung một mệnh đề `FROM ... WHERE` duy nhất** cho `rows`, `total` và `unique_customers` **trước phân trang `LIMIT`/`OFFSET`** (không chỉ che `owner`/`campaign` hậu kiểm; bảo toàn positive control cho cả `Leader A`, `Leader B` và `BOD` `0` PII) | - Unit: `TestL23BackendCustomerPoolCrossTeamIsolation` (2 method phủ `revoke(True) → B`, `revoke(False) → B`, `closed → B`, phân trang `page_size=1`, positive control `queued` của Đội A, `BOD` `0` PII trên `pool_list`, `customer_pool` và `pool_ui.list`)<br>- HTTP thật: `p1-http-l23-*` (15 case gọi trực tiếp `customers.customer_pool` & `customers.pool_list` trước phân trang + `BOD` `0` PII) | **FIXED** |
| **L-25** | Import chưa áp `name`/`source` từ `mapping` cho `CRM Lead` mới (luôn gán placeholder), chưa lưu quan hệ `Import Batch ↔ Lead` nên lọc kho theo `batch` bỏ sót khách chưa có `Work Item` và khách canonical được reuse qua nhiều batch | - Mở rộng `resolve_import_columns` (`phone`/`name`/`source`, hỗ trợ cả XLSX dòng đầu `None`), sanitize formula/HTML, chỉ dùng `"Chưa rõ tên"` (`cskh_is_placeholder_name=1`) khi thiếu tên hợp lệ, link an toàn `CRM Lead Source`, chặn `409 CONFLICT` khi đổi `mapping` trên cùng `idempotency_key`.<br>- Giữ nguyên 100% dữ liệu nghiệp vụ của `CRM Lead` canonical đã tồn tại (không ghi đè).<br>- Thêm DocType độc lập `CSKH Import Batch Lead` (`UNIQUE (import_batch, lead)`, `outcome = created \| reused`), đối soát `created_rows`/`reused_rows` từ DB và cung cấp `reporting.customer_pool` / `customers.customer_pool` cho T1A. | - Unit: `TestL25ImportMappingAndBatchLink` (4 method phủ mapping CSV + sparse XLSX, placeholder, sanitization + `409 CONFLICT`, non-overwrite canonical Lead, lọc kho đa batch không cần Work Item cho Admin/BOD/Leader/Staff, crash-retry & multi-thread MariaDB race)<br>- HTTP + RQ thật: `p1-http-l25-*` (11 case HTTP + đối soát SQL trực tiếp qua 2 batch liên tiếp & `customers.customer_pool`) | **FIXED** |
| **L-26 (Backend)** | `ownership.history`, `customers.pool_list`, `customers.customer_pool` (và `customers.my_list`) dùng `int(...)` trần trên `limit`/`page`/`page_size`, sinh HTTP `500 ValueError` khi input không hợp lệ | Bổ sung `parse_int`, `parse_pagination` (`page ∈ 1..1_000_000`, `page_size ∈ 1..100`) và `parse_optional_str` vào [`api/_params.py`](../../apps/crm_cskh/crm_cskh/api/_params.py); áp dụng ở cả tầng API và service cho `ownership.history(limit ∈ 1..200)`, `customers.pool_list`, `customers.customer_pool`, `customers.my_list`, `campaigns.save_rule(version)`, `distribution.enqueue_run(expected_rule_version)` để luôn trả ổn định HTTP `400 INVALID_REQUEST` | - Unit: `TestL26BackendPaginationAndLimitValidation` (kiểm tra trực tiếp API + service + HTTP envelope normalizer)<br>- HTTP thật: `p1-http-l26-*` (11 case gọi trực tiếp `ownership.history`, `customers.pool_list`, `customers.customer_pool`, `customers.my_list`) | **FIXED** |
| **L-29** | Bypass quyền đọc trực tiếp document ngoài team và lộ JSON thô (`preview_json`, `actual_json`, `state_json`, `selection_json`, ...) của `CSKH Campaign`, `CSKH Assignment Rule`, `CSKH Distribution Run` qua generic Frappe API (`/api/resource/*`, `frappe.client.*`) và Desk (`getdoc`, `reportview.get`, `search_link`, `validate_link`) | - Thực thi `has_permission` cấp DocType (`doc is None`) và cấp Document (`doc` là chuỗi/`dict`/`Document`, kể cả `ptype=None` & từ chối mọi `ptype` ghi cho non-system) tại [`p1_permissions.py`](../../apps/crm_cskh/crm_cskh/p1_permissions.py) + kiểm tra quyền đọc document tại `api/campaigns.py::rules`.<br>- Đặt `permlevel = 1` cho `preview_json`, `actual_json`, `state_json`, `progress`, `error`, `lease_expires_at` (`CSKH Distribution Run`) và `selection_json` (`CSKH Campaign`), chỉ cấp `DocPerm` `permlevel = 1` cho `CSKH CRM Admin` (`read: 1`) và `System Manager` (`read: 1, write: 1`); thêm `auth_hooks` `enforce_p1_filter_field_permissions` chặn filter-oracle `permlevel = 1` (`403`).<br>- Ghi đè `search_link`/`search_widget` (ép `ignore_user_permissions=False`) và `validate_link` (trả `{}` khi ngoài scope) tại [`api/guarded_crm.py`](../../apps/crm_cskh/crm_cskh/api/guarded_crm.py). Giữ nguyên các API projection theo scope (`distribution.get_run`, `campaigns.*`, `dashboard.*`). | - Unit: `TestL29GenericFrappeApiPermissionAndRawRunProtection` (2 method phủ DocType/Doc perms, `frappe.client.*`, `getdoc`, `reportview.get`, `search_link`, `validate_link`, filter-oracle)<br>- HTTP thật: `p1-http-l29-*` (45 case HTTP thật qua `/api/resource/*` GET/list/POST/PUT/DELETE, `frappe.client.*`, `getdoc`, `reportview.get`, `search_link`, `validate_link`, filter-oracle và API projection trên đủ 6 actor: `Leader A`, `Leader B`, `Staff`, `BOD`, `CRM-ADMIN`, `Guest`) | **FIXED** |
| **Direct Mutation Eligibility** | Kiểm tra trực tiếp `ownership.assign` / `ownership.reassign` (không suy từ dropdown UI): trước đó thiếu kiểm tra `campaign.team_scope`, `campaign.status == "Closed"`, rule bị tắt/`weight <= 0`/`rule.team` lệch ở `version` mới nhất, `capacity_limit` trên `assign` (và `reassign` dùng `max()` qua lịch sử thay vì `version` mới nhất), cùng claim/assign chéo campaign | Bổ sung `campaign_team_scope_members` trong [`services/eligibility.py`](../../apps/crm_cskh/crm_cskh/services/eligibility.py) và `_require_campaign_target_eligibility` + chặn xung đột claim/assign chéo campaign trong [`services/ownership.py`](../../apps/crm_cskh/crm_cskh/services/ownership.py) cho **cả `assign` lẫn `reassign`** | - Unit: `TestDirectOwnershipMutationEligibility` (kiểm tra trực tiếp `ownership.assign` & `ownership.reassign` trên tất cả vector âm/dương)<br>- HTTP thật: `p1-http-mut-*` (9 case HTTP POST trực tiếp tới `ownership.assign` & `ownership.reassign`) | **FIXED** |
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
  expected_total : 209
  observed_total : 209
  passing_total  : 209
  PASS           : 209

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

### 2.7. Bằng chứng HTTP P1 + RQ Worker với actor thật (`167/167 PASS`)

[`p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) thực thi **167 case HTTP thật** tới `bench serve` kết hợp **2 tiến trình `bench worker` (`short` và `long`) thật**:

* **52 case gốc:** phủ đăng nhập, CSRF dương/âm, phân quyền dương/âm của `Guest`, `crm.admin@dev.local`, `bod.viewer@dev.local`, `team1.leader@dev.local`, `team2.leader@dev.local`, `staff.a@dev.local`, `staff.b@dev.local` trên cả 7 nhóm API P1.
* **115 case mở rộng xác minh trực tiếp `L-16 … L-23`, `L-25`, `L-26`, `L-29`, Direct Mutation Eligibility và Phân phối nền RQ:**
  - `L-16`: kiểm tra `code` / `p1_code` ổn định trên `400 INVALID_REQUEST`, `403 FORBIDDEN`, `404 NOT_FOUND`, `409 CONFLICT`, và xác nhận File-oracle P0 (`/api/resource/File/<real_private>` vs `/api/resource/File/<missing>`) trả `404` trùng khớp cả `content_length` lẫn `sha256`;
  - `L-17`: `crm.admin@dev.local` gọi `dashboard.distribution` và `customers.pool_list` trả `scope == "system"`, số liệu dương `> 0` và khớp 100% truy vấn SQL trực tiếp;
  - `L-18` + `L-19` + `L-20`: upload CSV **250 dòng** (`245` hợp lệ + `5` lỗi gồm các dòng `215, 251 > 200`), gọi `imports.confirm` **1 lần duy nhất** (`status == "QUEUED"`), chờ RQ worker `short` hoàn tất `COMPLETED_WITH_ERRORS`, đối soát DB có đủ `245/245` canonical lead, `error_rows == 5`, `error_artifact` (`is_private=1`) chứa đúng 5 dòng lỗi `[12, 95, 188, 215, 251]`;
  - `L-21`: gửi đủ 8 biến thể JSON & `application/x-www-form-urlencoded` (`false`, `"false"`, `0`, `"0"`, `true`, `"true"`, `1`, `"1"`) cộng 1 giá trị sai (`"maybe"` → `400 INVALID_REQUEST`) tới `ownership.revoke`, đối chiếu trực tiếp trạng thái DB `revoked`/`queued`, `lead_owner`, `CSKH Active Claim`, `ToDo`, `DocShare`, `CSKH Assignment History`;
  - `L-22`: tạo dữ liệu đa trạng thái (`assigned`, `queued`, `revoked`, `closed`) trên campaign của Team 1 và ngoài Team 1, đối chiếu HTTP `dashboard.distribution` của `team1.leader@dev.local` và `bod.viewer@dev.local` khớp 100% SQL trên cả 10 card (gồm `revoked_work_items`, `closed_work_items`, `active_claims` hàng chưa gán người và `unique_customers`);
  - `L-23` (15 case HTTP trực tiếp trên `customers.customer_pool` và `customers.pool_list`): tạo khách từng giao ở Team 1 → `revoke(return_to_pool=True)`, `revoke(return_to_pool=False)` và `closed` → giao sang `staff.c@dev.local` của Team 2 cùng 2 khách `queued` hợp lệ của Team 1; gọi HTTP trực tiếp với `page=1&page_size=1` và `page=2&page_size=1` cho `team1.leader@dev.local` (xác nhận `total == unique_customers == work_items == 2` trước phân trang, không lộ SĐT/tên/`lead`/metadata hay số đếm của Team 2), đối chứng dương với `team2.leader@dev.local`, và xác minh `0` PII (`phone=null`, `display_name=""`) cho `bod.viewer@dev.local`;
  - `L-25`: thực thi 2 đợt import liên tiếp qua HTTP + RQ worker với header tùy biến `SDT_KH`/`TEN_KH`/`KENH_KH`, kiểm chứng `409 CONFLICT` khi đổi `mapping` trên cùng `idempotency_key`, sanitize formula/HTML, placeholder chỉ dùng khi thiếu tên hợp lệ, không ghi đè `CRM Lead` canonical ở Batch 2, và lọc `GET customers.customer_pool?batch=<batch1|batch2>` cho `Admin`, `BOD` (ẩn PII), `Staff` (`403 FORBIDDEN`) khi khách chưa có `Work Item`;
  - `L-26` (trực tiếp trên các endpoint backend): gọi HTTP trực tiếp `ownership.history?limit=abc|0|201`, `customers.pool_list?page=abc|page_size=xyz|101`, `customers.customer_pool?page=abc|1.5|page_size=0`, `customers.my_list?page=abc` trên `Admin`/`Leader`/`BOD`/`Staff` — tất cả trả ổn định HTTP `400` kèm `code == "INVALID_REQUEST"` và `p1_code == "INVALID_REQUEST"`, kèm positive control `200`;
  - `L-29` (45 case HTTP qua REST + `frappe.client.*` + Desk + API projection): dựng fixture gồm campaign/run riêng Team 1 & Team 2, campaign/run chung 2 team (`team_scope = None`), và rules của nhân sự thuộc các team khác nhau; kiểm chứng `/api/resource/*` (`GET` đơn lẻ + `GET` list với `fields` tường minh + chặn `POST`/`PUT`/`DELETE` + chặn filter-oracle `permlevel = 1` trả `403`), `frappe.client.get` / `get_list` / `get_value` / `validate_link`, và Desk (`frappe.desk.form.load.getdoc`, `frappe.desk.reportview.get`, `frappe.desk.search.search_link` với `ignore_user_permissions=1`) cùng positive/negative control trên đủ 6 actor (`Leader A`, `Leader B`, `Staff`, `BOD`, `CRM-ADMIN`, `Guest`);
  - **Direct Mutation Eligibility (9 case HTTP POST trực tiếp):** gọi thẳng `POST ownership.assign` & `POST ownership.reassign` để xác minh từ chối `target_user` ngoài `team_scope` (`400 INVALID_REQUEST`), rule mới nhất bị tắt (`400 INVALID_REQUEST`), vượt `capacity_limit` của rule mới nhất (`409 CAPACITY_EXCEEDED`), campaign `Closed` (`409 CONFLICT`), và khách đã được claim/assign ở campaign khác (`409 ALREADY_ASSIGNED`), kèm positive control hợp lệ;
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
| `L-23` (Bảo vệ `reporting.customer_pool` / `customers.customer_pool` và `reporting.pool_list` / `customers.pool_list` bằng SQL trước phân trang theo đúng policy quyền của T1A) | **FIXED** | `TestL23BackendCustomerPoolCrossTeamIsolation` (2 method) + 15 case HTTP trực tiếp `customers.customer_pool` & `customers.pool_list` (`Leader 1`, `Leader 2`, `BOD` `0` PII) |
| `L-25` (Mapping `name`/`source` khi tạo Lead mới, không ghi đè Lead cũ, liên kết `Import Batch ↔ Lead` & lọc kho đa batch) | **FIXED** | `TestL25ImportMappingAndBatchLink` (4 method) + 11 case HTTP/RQ/SQL |
| `L-26` (Chặn `400 INVALID_REQUEST` cho `page`/`page_size`/`limit` không hợp lệ trên `ownership.history`, `customers.pool_list`, `customers.customer_pool`, `customers.my_list`) | **FIXED** | `TestL26BackendPaginationAndLimitValidation` + 11 case HTTP trực tiếp trên các endpoint backend |
| `L-29` (Quyền DocType/Document thật + bảo vệ trường raw `permlevel = 1` trên `CSKH Campaign`, `CSKH Assignment Rule`, `CSKH Distribution Run` qua REST, `frappe.client.*` & Desk) | **FIXED** | `TestL29GenericFrappeApiPermissionAndRawRunProtection` (2 method) + 45 case HTTP thật trên 6 actor |
| Direct Mutation Eligibility (`ownership.assign` & `ownership.reassign`: `team_scope`, `Closed`, rule `version` mới nhất, `capacity_limit`, xung đột chéo campaign) | **FIXED** | `TestDirectOwnershipMutationEligibility` + 9 case HTTP POST trực tiếp |
| Phân phối nền bằng RQ hiện có (Mục 5) | **FIXED** | Enqueue sớm, chunk checkpoint `50`, DB lock + lease, recheck quyền/rule/capacity, chống double-submit, crash `exit 137` recovery |
| `L-12` / `T1-20` / `G1-12` (Màn hình UI `/cskh/*` & nối `pool_ui.py` trên PR #5) | **OPEN (Thuộc T1A — PR #5)** | Tách khỏi phạm vi Backend PR #4; T1B đã bàn giao đầy đủ contract tại [`api-contract-p1.md` §6.1–§6.4](./api-contract-p1.md) |

---

## 4. Phần giới hạn thực tế đã ghi nhận

1. **Thông lượng ORM của worker phân phối nền trên lô 10.000 khách:** Dù HTTP `POST distribution.execute` đã trả ngay trong `~0,22s` (không còn nghẽn HTTP timeout), tiến trình nền `distribution_worker` vẫn thực thi đầy đủ vòng đời Frappe Document (`Work Item`, `Active Claim`, `CRM Lead`, `DocShare`, `ToDo`, `Assignment History`, `CRM Notification`) với thông lượng thực tế `~2,0–2,1 items/s` (`~80–81 phút` cho 10.000 khách trên 1 RQ worker máy DEV; bộ nhớ ổn định `~282 MiB`, `job_timeout = 14.400s`). Trong môi trường vận hành cần đảm bảo tiến trình `bench worker --queue long` và `short` luôn chạy nền.
2. **Chưa đánh index `mobile_no` trên `tabCRM Lead`:** Tuân thủ ràng buộc P0 ADR-001 không sửa trực tiếp schema bảng gốc Frappe CRM; thông lượng `import_worker` giảm từ `41,4 dòng/s` (`0 → 10k`) xuống `28,3 dòng/s` (`10k → 20k`).
3. **Phạm vi giao vận HTTP probe:** `ci_p1_http_probe.py` kiểm chứng HTTP thật tới `bench serve` + `bench worker`, chưa phủ lớp reverse proxy (Nginx/TLS) và CORS đa domain.
4. **Tải file lỗi & phân trang sâu:** `error_artifact` là `File` private tải qua route chuẩn của Frappe; danh sách dùng `page`/`page_size` theo offset; bộ lọc ngày dashboard tách `CSKH Work Item.creation` và `CSKH Assignment History.event_at` theo thiết kế.
5. **Quản trị nhánh & cổng giai đoạn:** PR #4 giữ trạng thái **OPEN** (không tự merge), không tự ký **GO P2** — chờ T1A cập nhật UI theo contract phân phối nền, T1C kiểm định độc lập trên SHA mới và T0 quyết định.

---

## 5. Tài liệu & Artifact liên quan

* [`api-contract-p1.md`](./api-contract-p1.md) — API contract P1 (20 endpoint, mã lỗi `code`/`p1_code`, phân phối nền RQ, `L-23` / `L-25` / `L-26` / `L-29` / direct mutation eligibility, ghi chú bàn giao T1A)
* [`schema-p1.md`](./schema-p1.md) — 9 DocType P1 (gồm `CSKH Import Batch Lead`), unique constraint & lookup index
* [`../../deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json) — inventory P1 riêng (**209 method** trên 8 module)
* [`../../deploy/dev/seed/g007_test_inventory.json`](../../deploy/dev/seed/g007_test_inventory.json) — inventory P0 giữ nguyên (**99 method** trên 5 module)
* Bộ bằng chứng runtime đầy đủ của revision này: [`../../deploy/dev/evidence/p1-runtime-20261102/`](../../deploy/dev/evidence/p1-runtime-20261102/) (kèm [`SHA256SUMS.txt`](../../deploy/dev/evidence/p1-runtime-20261102/SHA256SUMS.txt))
* Bộ bằng chứng baseline (race đa tiến trình & backup/restore): [`../../deploy/dev/evidence/p1-t1b-20261102/`](../../deploy/dev/evidence/p1-t1b-20261102/)

<!-- T1A-UI-CHAPTER -->

---

## 7. Chương T1A — Giao diện P1 hoàn chỉnh (5 màn hình P1 §9, Việt hóa & E2E thực tế)

**Ngày cập nhật:** 2026-11-03
**Vai trò:** T1A — CSKH P1 UI (cùng Frappe site/session với CRM theo ADR-UI-001 & ADR-P1-06)
**Nhánh:** `codex/p1-ui` — worktree `D:/HRP-app/p1-ui` ([PR #5](https://github.com/nobita6986/new-crm/pull/5))
**Revision mã nguồn tích hợp cuối (Đợt 4 — Hiện hành):** `9fdbb8dfdd9ad443ffe05057a969a1e039473989` (hợp nhất UI/docs `5c37bf3dc464a0d8e2b9bd94ba55df40da469559` + backend PR #4 `2235757b79bb9f1a7fb3ae2957b28deb088185b9` + `77e07dac7948c156039bc6a9746fe5d451fc0d71` — **`327/327` method PASS** qua `run_suites.py`: `208` P1 + `12` UI + `99` P0 + `8` transport; P1 HTTP probe `158/158`; `vi.csv` `613` dòng dịch / `409` khai báo, `0` lỗi — chi tiết tại **§7.11**)
**Backend baseline lịch sử Đợt 1 (bảo toàn đối chiếu):** `3025495f58bc0391c3f8d092af500b0566a5f038` (baseline ban đầu 174/174 P1, 99/99 P0) + `64b7486f29ad712106d167dfaea50e3b060a6845` (184/184 P1 PASS, 52/52 HTTP probe PASS, 99/99 P0 PASS)
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md) §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu)
**Hợp đồng API:** [`docs/architecture/api-contract-p1.md`](./api-contract-p1.md)
**Báo cáo backend:** chương 0–5 ở trên (do T1B biên soạn tại `77e07da` — giữ nguyên 100%, không chỉnh sửa chồng chéo)

> [!IMPORTANT]
> **Quy ước phân biệt số liệu lịch sử và số liệu hiện hành (`L-28`):**
> * **Số liệu hiện hành (Đợt 4 — Bản tích hợp cuối `9fdbb8d`):** xem **§7.11** ở cuối chương này (`208` P1 + `12` UI + `99` P0 + `8` transport = **`327/327`**; P1 HTTP probe **`158/158`**; `translations/vi.csv` **`613` dòng dịch** / `614` dòng tệp, **`409` chuỗi khai báo**, `210` chuỗi sử dụng, `0` lỗi; đã đóng đủ `L-23..L-29` trên cả UI lẫn Backend).
> * **Số liệu lịch sử được giữ nguyên trạng để truy vết kiểm toán (không sửa số cũ):** **§7.0–§7.8** là *Mốc lịch sử Đợt 1* (`0d3f2bc` / `d0842cc`); **§7.9** là *Mốc lịch sử Đợt 2* (`6351048` / `a75c093`, giữ nguyên văn UTF-8); **§7.10** là *Mốc lịch sử Đợt 3* (`3f07b9b` / `5c37bf3`).

### 7.0. Nguyên văn đề bài T1A ban đầu [Mốc lịch sử Đợt 1 — `0d3f2bc` / `d0842cc`]

```text
T1A, hoàn tất UI P1 trong D:/HRP-app/p1-ui, nhánh codex/p1-ui.

Nguồn yêu cầu:
D:/HRP-app/new-crm/docs/root-plan/P1.md

Backend baseline đã qua CI:
- PR #4, commit 3025495f58bc0391c3f8d092af500b0566a5f038.
- P1 174/174 PASS; P0 99/99 PASS.
- Contract: D:/HRP-app/p1-foundation/docs/architecture/api-contract-p1.md.

1. Đồng bộ backend có kiểm soát
- Kiểm kê staged/unstaged và bảo toàn UI đang làm.
- Tích hợp từ commit backend xác định; không copy lẫn source chưa commit
  giữa các worktree rồi tuyên bố đang chạy đúng SHA.
- Nếu T1B có commit mới, kiểm tra thay đổi contract trước khi tích hợp.
- Với hooks.py, vi.csv hoặc file dùng chung, hợp nhất có chủ đích,
  không ghi đè phần backend.

2. Hoàn thiện các màn hình
- Kho khách hàng.
- Nhập danh sách: upload, mapping, preview, confirm, tiến độ,
  kết quả và tải file lỗi.
- Chiến dịch/quy tắc: weight, capacity, preview và execute.
- Khách được giao: danh sách, chi tiết, reassign/revoke theo quyền.
- Dashboard và history: filter, totals, per-user, current owner
  và assignment events được phân biệt rõ.

3. Chạy E2E thật trên site P1 cô lập
- CSV/XLSX → preview → confirm → hoàn thành.
- Rule → preview → execute → dữ liệu xuất hiện ở đúng nhân viên.
- Reassign/revoke: người cũ mất quyền, người mới có quyền đúng policy.
- CRM-ADMIN, Leader A/B, Staff A/B và BOD dùng đúng tài khoản/role thật.
- Dashboard/filter khớp response và dữ liệu đối soát.
- File lỗi tải được với người có quyền, bị từ chối với người không có quyền.
- Kiểm tra stale preview, double-submit, lỗi job, forbidden và conflict.
- Không biến lỗi backend thành danh sách rỗng, số 0 hay thông báo thành công.

4. Việt hóa và kiểm tra trình duyệt
- Hoàn thiện toàn bộ nhãn, trạng thái, validation, thông báo và lỗi P1.
- Capacity=0 hiển thị “Không giới hạn”.
- Dữ liệu import render an toàn; không thực thi HTML/formula ngoài ý muốn.
- Kiểm tra CSRF, console/network, assets, màn trắng và bố cục.
- Thu screenshot các màn/trạng thái cần nghiệm thu bằng dữ liệu giả,
  ghi actor, URL, revision và liên kết evidence HTTP.

5. Bàn giao
- Commit UI/bản dịch/test thuộc phạm vi T1A; mở hoặc cập nhật PR UI riêng.
- Bàn giao cách dựng revision tích hợp backend + UI để T1C tái hiện.
- Gửi checklist đạt/chưa đạt và artifact; phối hợp cập nhật phần UI
  của P1-Exit-Report, tránh sửa đồng thời phần backend của T1B.
- Giữ ứng dụng đang chạy để Owner xem.

Không nới quyền backend để làm UI xanh; không dùng mock làm evidence PASS.
Không merge, không tự ký GO P2 và không gọi agent khác.
```

### 7.1. Đồng bộ backend có kiểm soát & cách dựng lại [Mốc lịch sử Đợt 1 — `0d3f2bc`; lệnh dựng hiện hành xem §7.11]

1. **Nguồn gốc tích hợp & kiểm tra contract khi T1B có commit mới:**
   * Bắt đầu từ baseline `3025495f58bc0391c3f8d092af500b0566a5f038` (`origin/codex/p1-foundation`), sau đó kiểm tra diff hợp đồng `api-contract-p1.md` §6.1 khi PR #4 có commit mới qua CI `64b7486f29ad712106d167dfaea50e3b060a6845` (**0 breaking change** trên toàn bộ 19 endpoint P1) rồi merge trực tiếp từ Git — tuyệt đối không sao chép tệp chưa commit giữa các worktree.
   * Cập nhật `campaigns.js` tương thích với ghi chú §6.1 của `64b7486`: hỗ trợ bấm chạy lại trực tiếp `distribution.execute` với cùng `preview_id` cho đợt chạy `completed_with_errors` (resume phần chưa giao), giữ `import_worker.py` bản `64b7486` (>200 dòng + crash recovery) kết hợp chuyển trạng thái hai bước `DRY_RUN -> CONFIRMED -> QUEUED` trong `import_service.py::confirm` để RQ worker chạy thật mượt mà.
2. **Bảo toàn tuyệt đối hợp đồng & phân quyền backend:**
   * `permissions.py` và `p1_permissions.py`: **0 dòng thay đổi**.
   * `hooks.py`: giữ nguyên `normalize_p1_error_response` bọc ngoài bộ chuẩn hóa P0.
   * `translations/vi.csv`: hợp nhất có chủ đích qua [`deploy/p1/rebuild_translations.py`](../../deploy/p1/rebuild_translations.py) — giữ nguyên toàn bộ dòng 3 cột có `context` từ P0 (`2cd4f60`), toàn bộ dòng backend từ `3025495` / `64b7486`, và toàn bộ danh mục P1 UI (**577 dòng**, `0` dòng lỗi cú pháp CSV).
   * `docs/architecture/P1-Exit-Report.md`: giữ nguyên 100% chương 0–6 của `64b7486`, chỉ nối thêm chương 7 phía sau `<!-- T1A-UI-CHAPTER -->`.
3. **Hướng dẫn dựng và tái hiện đầy đủ cho T1C (trên site cô lập `crm-cskh-p1`):**

```bash
# 1. Lấy đúng nhánh tích hợp backend (3025495 + 64b7486) + UI (codex/p1-ui)
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 3025495f58bc0391c3f8d092af500b0566a5f038 HEAD
git merge-base --is-ancestor 64b7486f29ad712106d167dfaea50e3b060a6845 HEAD

# 2. Khởi chạy stack cô lập P1 (cổng 8120 / 9120, không đụng stack P0 DEV 8010/8091)
cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

# 3. Chạy kiểm toán tĩnh (ruff 0.14.0 + inventory 184 method + audit dịch Việt 100%)
docker exec crm-cskh-p1-bench-1 bash -lc "uvx ruff==0.14.0 check --no-cache /src/apps/crm_cskh /src/deploy/dev/seed/p1_test_inventory.py /src/deploy/dev/seed/ci_p1_http_probe.py && uvx ruff==0.14.0 format --check --no-cache /src/apps/crm_cskh /src/deploy/dev/seed/p1_test_inventory.py /src/deploy/dev/seed/ci_p1_http_probe.py"
python -X utf8 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json
python -X utf8 deploy/p1/check_translations_audit.py

# 4. Chạy tuần tự toàn bộ suite tích hợp P1 (184) + UI (6) + P0 core (99 + 8) = 297 method
python -X utf8 deploy/p1/run_suites.py

# 5. Khởi chạy bench serve + bench worker rồi chạy E2E HTTP thật & E2E trình duyệt Chromium thật
docker exec -d crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost serve --port 8000 >/tmp/p1-serve.log 2>&1"
docker exec -d crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench worker --queue short,default,long >/tmp/p1-worker.log 2>&1"
python -X utf8 deploy/p1/verify_p1_http_e2e.py
node deploy/p1/verify_p1_browser_e2e.mjs
```

| Hạng mục môi trường | Giá trị đang chạy |
|---|---|
| Stack cô lập | `deploy/p1/docker-compose.yml` — project `crm-cskh-p1`, web `127.0.0.1:8120` (`http://p1.localhost:8120`), socketio `9120` |
| Site | `p1.localhost` (MariaDB 11.8, Redis cache + queue, `bench serve` + `bench worker --queue short,default,long` chạy nền) |
| Stack DEV P0 | Giữ nguyên trạng, không restart, không đổi cấu hình |

### 7.2. Năm màn hình P1 §9 & API đọc phụ trợ theo scope

| Màn | Đường dẫn | Template & JS | API phục vụ (cùng session + CSRF Frappe) |
|---|---|---|---|
| Trang 1 — Kho khách hàng | `/cskh/pool` | `www/cskh/pool.html` + `public/cskh/pool.js` | `crm_cskh.api.pool_ui.list` (giữ nguyên `customers.pool_list` của T1B cho `SEC-07`) |
| Trang 2 — Nhập danh sách | `/cskh/import` | `www/cskh/import.html` + `public/cskh/import.js` | `import_files.upload_import_file`, `imports.{create_preview,confirm,get_status}`, `import_history.{history,batch_detail,download_error_file}` |
| Trang 3 — Chiến dịch và quy tắc | `/cskh/campaigns` | `www/cskh/campaigns.html` + `public/cskh/campaigns.js` | `campaigns.{list,save,rules,save_rule}`, `distribution.{preview,execute,get_run}`, `run_history.runs` |
| Trang 4 — Khách được giao | `/cskh/my-customers` | `www/cskh/my-customers.html` + `public/cskh/my-customers.js` | `customers.my_list`, `ownership.{reassign,revoke,history}`, `ownership_ui.{assigned_list,assignees}` |
| Trang 5 — Bảng điều khiển phân phối | `/cskh/dashboard` | `www/cskh/dashboard.html` + `public/cskh/dashboard.js` | `dashboard.{distribution,campaigns}`, `run_history.runs`, `campaigns.rules` |

Các hoàn thiện trọng tâm theo §9.1–§9.6:

1. **Kho khách hàng (`/cskh/pool`):** Hiển thị đầy đủ trạng thái phân phối **suy ra** (`Chưa phân phối`, `Trong hàng đợi`, `Đã giao`, `Xung đột`, `Đã thu hồi`, `Đã đóng`, `Lỗi dữ liệu`) mà **không** ghi vào `CRM Lead.status`; tách biệt hai bộ đếm `Số khách hàng duy nhất` (`unique_customers`) và `Số công việc` (`work_items`); hỗ trợ lọc server-side theo `Chiến dịch`, `Trạng thái phân phối`, `Nguồn`, `Lô` và `Tìm kiếm` (tự động khớp cả đầu số `0...` lẫn chuẩn hóa `+84...`); ẩn hoàn toàn PII và trả `403 FORBIDDEN` nếu `CSKH BOD Viewer` cố gọi `search`.
2. **Nhập danh sách (`/cskh/import`):** Đủ 3 bước khép kín (Tải tệp riêng tư CSV/XLSX + ánh xạ 3 cột `số điện thoại` / `họ tên` / `nguồn` → Xem trước phân loại dòng tiếng Việt → Xác nhận + theo dõi tiến độ trực tiếp Bước 3 với `Số dòng tạo mới`, `Số dòng dùng lại`, `Số dòng bỏ qua`, `Số dòng lỗi`), bảng `Lịch sử nhập`, nút `Chạy lại công việc thất bại` (`FAILED → confirm`) và endpoint tải tệp lỗi `download_error_file` kiểm soát chặt `CSKH CRM Admin` (`200`) / mọi role khác (`403 FORBIDDEN`) mà không mở rộng quyền đọc `/private/files/...` của P0.
3. **Chiến dịch & quy tắc (`/cskh/campaigns`):** Hỗ trợ tạo, `Sửa chiến dịch`, `Đóng chiến dịch` (từ chối đóng ngầm nếu còn work item hoạt động), tính trực tiếp tỷ lệ `%` chuẩn hóa khi nhập trọng số, hiển thị **`Không giới hạn`** cho `capacity = 0`, bảng xem trước đủ 5 cột P1 §9.3, khóa chống bấm đúp (anti-double-submit), tra cứu `Mã đợt chạy` và nút `Chạy lại công việc thất bại`. `BOD` và `Sales Manager` mở được trang ở chế độ chỉ đọc theo đúng phạm vi.
4. **Khách được giao (`/cskh/my-customers`):** Lọc theo chiến dịch, tìm kiếm theo tên/SĐT, sắp xếp thời gian giao (`Mới nhất trước` / `Cũ nhất trước`), mở ngăn `Chi tiết quyền phụ trách` kèm bảng `Lịch sử phụ trách` chỉ ghi thêm (`Phân công`, `Chuyển người phụ trách`, `Thu hồi`). Bảng phạm vi quản lý cho phép `CSKH CRM Admin` chuyển giao / thu hồi kèm lý do bắt buộc & `expected_owner`, còn `Sales Manager` chỉ xem cây đội mình (không có nút ghi).
5. **Bảng điều khiển phân phối (`/cskh/dashboard`):** Lọc theo `Chiến dịch`, `Từ ngày` / `Đến ngày` (kiểm tra hợp lệ khoảng ngày), thu hẹp theo `Đội`, `Người được giao` và `Mã đợt chạy`; tách thành hai khu vực trực quan riêng biệt **`Quyền phụ trách hiện tại (công việc đang hoạt động)`** và **`Sự kiện phân công (nhật ký kiểm toán)`** kèm bảng `Theo chiến dịch` và lịch sử `Đợt phân phối`.

### 7.3. Bằng chứng HTTP E2E thực tế trên `p1.localhost` [Mốc lịch sử Đợt 1 — `http-e2e-report.json`]

Script tự động tái lập: [`deploy/p1/verify_p1_http_e2e.py`](../../deploy/p1/verify_p1_http_e2e.py) → báo cáo đã che PII/bí mật tại [`deploy/p1/evidence/http-e2e-report.json`](../../deploy/p1/evidence/http-e2e-report.json).

| Mã kiểm định | Kịch bản E2E thực tế qua HTTP + CSRF | Kết quả đo |
|---|---|---|
| `E2E-01-page-role-matrix` | Ma trận HTTP 6 route (`/cskh` + 5 màn) trên 8 phiên thật (`admin`, `bod`, `leader_a`, `leader_b`, `staff_a`, `staff_b`, `outside`, `guest`) + kiểm tra `lang="vi"` | **PASS** — Admin `200×6`; BOD/Leader `403` trên `/cskh/import`, `200` các trang có quyền; Staff `200` trên `/cskh` & `/cskh/my-customers`, `403` các trang quản trị/tổng hợp; Guest `403×6` |
| `E2E-02-csv-import-and-error-download-policy` | Upload CSV (chứa XSS `<script>`, công thức `=SUM(1,2)`, ký hiệu khoa học `9.1888E+08`, dòng trùng) → preview → confirm → `COMPLETED_WITH_ERRORS` + kiểm tra tải tệp lỗi | **PASS** — `total=10, valid=6, duplicate=1, error=3, created=6, skipped=3`; Admin tải tệp lỗi `200` (đã sanitize công thức), 7 actor còn lại bị chặn `403` cả ở API lẫn `/private/files/...` |
| `E2E-03-xlsx-import-and-failed-batch-retry` | Upload tệp `.xlsx` thật → preview → confirm → `COMPLETED` + chạy lại batch `FAILED` | **PASS** — `total=5, valid=4, duplicate=1, error=0`; batch `FAILED` chạy lại lên `COMPLETED` |
| `E2E-04-campaign-rules-stale-preview-and-execute` | Tạo/sửa/đóng chiến dịch, cấu hình rule `60/40` (`capacity=0` Không giới hạn & `capacity=10`), kiểm tra stale preview (`RULE_CHANGED`, `PREVIEW_EXPIRED`), chống double-submit và giao về đúng `my_list` | **PASS** — `RULE_CHANGED` → `409`, `PREVIEW_EXPIRED` → `409`; thực thi giao đúng `7` khách cho `staff_a`, `4` khách cho `staff_b`, không rò chéo |
| `E2E-05-reassign-revoke-conflict-and-reconciliation` | Chuyển giao `staff_a → staff_b` (kèm retry idempotent), thử ghi đè bằng `expected_owner` cũ, thu hồi `revoke`, đối soát quyền đọc `CRM Lead` và số liệu dashboard | **PASS** — `staff_a` chuyển từ `200 → 403`, `staff_b` từ `403 → 200`; `expected_owner` cũ bị chặn `409 CONFLICT`; sau chuyển giao `assigned_work_items` giữ nguyên `11` còn `assignment_events` tăng lên `12`; sau `revoke` người cũ nhận `403` |
| `E2E-06-team-isolation-bod-pii-csrf-and-404` | Cô lập Đội A (`leader_a`) vs Đội B (`leader_b`), BOD xem tổng hợp không lộ PII, từ chối mọi POST trái quyền / sai CSRF, và trả `404 NOT_FOUND` | **PASS** — `leader_a` chỉ thấy `{staff_a: 6, staff_b: 4}`, `leader_b` chỉ thấy `{staff_c: 2}`; BOD thấy tổng `12`, `phone=None`, `display_name=""`, `search` bị chặn `403`; sai CSRF → `400 CSRFTokenError`; sai mã run/batch → `404` |

### 7.4. Kiểm tra trình duyệt Chromium thật, an toàn XSS/PII & 11 Screenshot Baseline [Mốc lịch sử Đợt 1 — `0d3f2bcbbf9715a4c0c783903ce750de8646583d`]

Script tự động: [`deploy/p1/verify_p1_browser_e2e.mjs`](../../deploy/p1/verify_p1_browser_e2e.mjs) → manifest [`deploy/p1/evidence/browser-manifest.json`](../../deploy/p1/evidence/browser-manifest.json).
Toàn bộ 11 lượt chụp đều đạt: `console_errors = 0`, `page_errors = 0`, `asset_failures = 0`, `http_500 = 0`, `xss_triggered = false`, `unmasked_phones = 0`, `html[lang="vi"]`. Chuỗi độc hại `<img src="x">` hiển thị nguyên văn dạng chữ (không sinh thẻ `<img>`), số điện thoại luôn che dạng `*******0101` (hoặc ẩn hoàn toàn `—` với BOD).

| # | Screenshot | Actor (Role thật) | URL & HTTP | Nội dung nghiệm thu chính | Liên kết HTTP Evidence |
|---|---|---|---|---|---|
| 1 | [`01-pool-admin.png`](../../deploy/p1/evidence/screenshots/01-pool-admin.png) *(bản chuẩn [`01-pool.png`](../../deploy/p1/evidence/screenshots/01-pool.png))* | `cskh-p1-admin@example.test` (`CSKH CRM Admin`) | `http://p1.localhost:8120/cskh/pool` (`200`) | Kho khách hàng: tách `Số khách hàng duy nhất (15)` vs `Số công việc (13)`, đủ badge `Đã giao` / `Trong hàng đợi` / `Xung đột` / `Lỗi dữ liệu`, SĐT che 4 số cuối, `<img src="x">` hiển thị an toàn dạng chữ | `http-e2e-report.json#E2E-04-campaign-rules-stale-preview-and-execute` |
| 2 | [`02-import-admin-step3-and-history.png`](../../deploy/p1/evidence/screenshots/02-import-admin-step3-and-history.png) *(bản chuẩn [`02-import.png`](../../deploy/p1/evidence/screenshots/02-import.png))* | `cskh-p1-admin@example.test` (`CSKH CRM Admin`) | `http://p1.localhost:8120/cskh/import` (`200`) | Upload thật trên trình duyệt → Ánh xạ 3 cột → Bước 2 (`Trùng trong CRM`, `Không hợp lệ`) → Bước 3 (`Đã hoàn tất nhưng có lỗi`, `Tải tệp lỗi`) + bảng `Lịch sử nhập` có `Chạy lại công việc thất bại` | `http-e2e-report.json#E2E-02-csv-import-and-error-download-policy` |
| 3 | [`03-campaigns-admin.png`](../../deploy/p1/evidence/screenshots/03-campaigns-admin.png) *(bản chuẩn [`03-campaigns.png`](../../deploy/p1/evidence/screenshots/03-campaigns.png))* | `cskh-p1-admin@example.test` (`CSKH CRM Admin`) | `http://p1.localhost:8120/cskh/campaigns` (`200`) | Danh sách chiến dịch (`Sửa chiến dịch`, `Đóng chiến dịch`), quy tắc `60%` / `40%`, `capacity=0` hiển thị **`Không giới hạn`**, lịch sử đợt chạy Việt hóa 100% + nút `Chạy lại công việc thất bại` | `http-e2e-report.json#E2E-04-campaign-rules-stale-preview-and-execute` |
| 4 | [`04-campaigns-error-not-found.png`](../../deploy/p1/evidence/screenshots/04-campaigns-error-not-found.png) | `cskh-p1-admin@example.test` (`CSKH CRM Admin`) | `http://p1.localhost:8120/cskh/campaigns` (`200`) | Tra cứu `RUN-DOES-NOT-EXIST` hiển thị rõ cảnh báo lỗi tiếng Việt `Dữ liệu yêu cầu không tồn tại. · [NOT_FOUND]` (không biến thành danh sách rỗng hay số `0`) | `http-e2e-report.json#E2E-06-team-isolation-bod-pii-csrf-and-404` |
| 5 | [`05-my-customers-admin-ownership-history.png`](../../deploy/p1/evidence/screenshots/05-my-customers-admin-ownership-history.png) | `cskh-p1-admin@example.test` (`CSKH CRM Admin`) | `http://p1.localhost:8120/cskh/my-customers` (`200`) | Quản lý quyền phụ trách toàn hệ thống, mở `Chi tiết quyền phụ trách` & `Lịch sử phụ trách` (`Phân công` → `Chuyển người phụ trách`) kèm nút `Chuyển người phụ trách` / `Thu hồi` | `http-e2e-report.json#E2E-05-reassign-revoke-conflict-and-reconciliation` |
| 6 | [`06-my-customers-staff-a.png`](../../deploy/p1/evidence/screenshots/06-my-customers-staff-a.png) *(bản chuẩn [`04-my-customers.png`](../../deploy/p1/evidence/screenshots/04-my-customers.png))* | `cskh-p1-staff-a@example.test` (`Sales User`) | `http://p1.localhost:8120/cskh/my-customers` (`200`) | Nhân viên A1 chỉ thấy đúng `6` khách đang thuộc mình (sau khi chuyển 1 sang A2), xem được chi tiết & lịch sử phụ trách, ẩn hoàn toàn khung quản trị | `http-e2e-report.json#E2E-05-reassign-revoke-conflict-and-reconciliation` |
| 7 | [`07-forbidden-staff-b-import.png`](../../deploy/p1/evidence/screenshots/07-forbidden-staff-b-import.png) | `cskh-p1-staff-b@example.test` (`Sales User`) | `http://p1.localhost:8120/cskh/import` (`403`) | Nhân viên truy cập trái quyền `/cskh/import` nhận trang `HTTP 403` tiếng Việt (`Bạn không có quyền truy cập trang này.`) | `http-e2e-report.json#E2E-01-page-role-matrix` |
| 8 | [`08-my-customers-leader-a-team-scope.png`](../../deploy/p1/evidence/screenshots/08-my-customers-leader-a-team-scope.png) | `cskh-p1-leader-a@example.test` (`Sales Manager`) | `http://p1.localhost:8120/cskh/my-customers` (`200`) | Trưởng nhóm A xem đúng `10` khách của Đội A (`staff_a`, `staff_b`), không rò khách Đội B (`staff_c`), chỉ xem chi tiết chứ không có nút chuyển/thu hồi | `http-e2e-report.json#E2E-06-team-isolation-bod-pii-csrf-and-404` |
| 9 | [`09-dashboard-leader-b-team-isolation.png`](../../deploy/p1/evidence/screenshots/09-dashboard-leader-b-team-isolation.png) | `cskh-p1-leader-b@example.test` (`Sales Manager`) | `http://p1.localhost:8120/cskh/dashboard` (`200`) | Dashboard Trưởng nhóm B (`Phạm vi đội`): chỉ thấy `2` khách của `staff_c` và `Chiến dịch Chăm sóc Đội B`, không lộ dữ liệu Đội A | `http-e2e-report.json#E2E-06-team-isolation-bod-pii-csrf-and-404` |
| 10 | [`10-dashboard-bod-live-vs-events.png`](../../deploy/p1/evidence/screenshots/10-dashboard-bod-live-vs-events.png) *(bản chuẩn [`05-dashboard.png`](../../deploy/p1/evidence/screenshots/05-dashboard.png))* | `cskh-p1-bod@example.test` (`CSKH BOD Viewer`) | `http://p1.localhost:8120/cskh/dashboard` (`200`) | Dashboard BOD (`Toàn hệ thống`): tách rõ `Quyền phụ trách hiện tại (12 công việc đã giao)` vs `Sự kiện phân công (15 sự kiện)`, bộ lọc `Đội` / `Người được giao` / `Mã đợt chạy`, `Không giới hạn` | `http-e2e-report.json#E2E-06-team-isolation-bod-pii-csrf-and-404` |
| 11 | [`11-pool-bod-zero-pii.png`](../../deploy/p1/evidence/screenshots/11-pool-bod-zero-pii.png) | `cskh-p1-bod@example.test` (`CSKH BOD Viewer`) | `http://p1.localhost:8120/cskh/pool` (`200`) | Kho khách hàng của BOD: thấy số tổng hợp và mã `CRM-LEAD-...`, ẩn hoàn toàn ô tìm kiếm PII, không lộ tên khách, không lộ kể cả 4 số cuối SĐT, không có nút `Mở trong CRM` | `http-e2e-report.json#E2E-06-team-isolation-bod-pii-csrf-and-404` |

### 7.5. Việt hóa 100% (P1 §9.6, T1-20) [Số liệu lịch sử Đợt 1: `577` dòng / `372` khai báo — hiện hành xem §7.11: `613` dòng / `409` khai báo]

* Tệp [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv) có **577 dòng** hợp lệ (`check_translations.py`: `malformed = 0`), bao phủ toàn bộ **372 chuỗi khai báo P1** (shell, tiêu đề, 5 màn hình, trạng thái chiến dịch/lô/đợt chạy, mã lỗi ổn định P1 §10.1, lý do loại trừ eligibility và toàn bộ thông báo ngoại lệ từ backend/API).
* Kết quả [`deploy/p1/check_translations_audit.py`](../../deploy/p1/check_translations_audit.py):
  * `used strings = 204`, `declared strings = 372`, `missing declaration = 0`, `missing translation = 0`, `untranslated (EN) = 0`, `orphan declaration = 0`.
* Shell CSKH (`cskh_context.py`) ghim `frappe.local.lang = "vi"` và `context.boot["lang"] = "vi"` trước cả bước kiểm tra quyền truy cập, bảo đảm cả `<html lang="vi">` lẫn trang từ chối quyền `403` đều hiển thị tiếng Việt đồng nhất.

### 7.6. Kết quả bộ test đơn vị & tích hợp [Số liệu lịch sử Đợt 1: `297/297` — hiện hành xem §7.11: `327/327`]

Lệnh chạy tuần tự: [`deploy/p1/run_suites.py`](../../deploy/p1/run_suites.py) — **15/15 module PASS (`297/297` method)**:

| Nhóm suite | Module | Số method | Kết quả |
|---|---|---:|---|
| P1 backend | `test_p1_api_permissions` | 30 | **PASS** |
| P1 backend | `test_p1_chaos` | 10 | **PASS** |
| P1 backend | `test_p1_concurrency` | 10 | **PASS** |
| P1 backend | `test_p1_distribution` | 17 | **PASS** |
| P1 backend | `test_p1_identity` | 13 | **PASS** |
| P1 backend | `test_p1_imports` | 16 | **PASS** |
| P1 backend | `test_p1_ownership` | 17 | **PASS** |
| P1 backend | `test_p1_regressions` | 71 | **PASS** |
| **P1 UI endpoints (T1A)** | `test_ui_p1_endpoints` | 6 | **PASS** |
| P0 core | `test_permissions` | 18 | **PASS** |
| P0 core | `test_security_remediation` | 12 | **PASS** |
| P0 core | `test_security_remediation_v2` | 32 | **PASS** |
| P0 core | `test_docshare_lifecycle` | 10 | **PASS** |
| P0 core | `test_bt1c5_matrix_gaps` | 27 | **PASS** |
| P0 transport | `test_error_response_normalization` | 8 | **PASS** |

### 7.7. Lưu ý kỹ thuật & giới hạn tại thời điểm Đợt 1 [Mốc lịch sử Đợt 1 — đã đóng ở §7.9–§7.11]

1. **Điều chỉnh có chủ đích trên dịch vụ dùng chung (0 nới quyền, giữ xanh 184/184 P1 & 99/99 P0):**
   * `services/reporting.py`: bổ sung kiểm tra `allow_pii = scope.get("pii", True)` trong `reporting.pool_list()` để vai trò `CSKH BOD Viewer` không nhận `lead_name` và `mobile_no` (tuân thủ P1 §8.1), đồng thời giữ `_scope` toàn hệ thống cho `CSKH CRM Admin`.
   * `services/import_service.py`: trong `confirm()`, bước chuyển `DRY_RUN` đi tuần tự `DRY_RUN -> CONFIRMED -> QUEUED` (1 bước/lần lưu đúng bảng chuyển trạng thái `IMPORT_STATUS_TRANSITIONS`) để RQ background worker thực thi thật không bị chặn ở `CONFIRMED -> RUNNING`.
2. **Tách `pool_ui.list` cho màn `/cskh/pool`:** Hợp đồng `customers.pool_list` của T1B được khóa cho tập work item `queued` (`SEC-07`). Để màn Kho khách hàng hiển thị cả lead mới nhập chưa phân phối (`Chưa phân phối`), `Đã giao`, `Xung đột` và `Lỗi dữ liệu` kèm bộ lọc server-side mà không phá hợp đồng T1B, T1A bổ sung `crm_cskh.api.pool_ui.list` dùng chung chính hàm giải quyết scope `reporting._scope(actor)` (không nhận tham số `user`/`team`/`scope` từ client).
3. **CI GitHub Actions cho trình duyệt:** Workflow `.github/workflows/p0.yml` và `.github/workflows/p1.yml` hiện kiểm tra đầy đủ lint/format, inventory (`184` method P1 / `99` method P0), migration idempotency, P0 HTTP probe `18/18` và P1 HTTP probe `52/52`; job chạy Chromium Playwright (`deploy/p1/verify_p1_browser_e2e.mjs`) hiện chạy trên stack cô lập `crm-cskh-p1` và chưa gắn thành job riêng trong `.github/workflows/` để tránh sửa workflow CI backend của T1B.

### 7.8. Checklist nghiệm thu bàn giao T1A [Mốc lịch sử Đợt 1 — checklist tích hợp cuối hiện hành xem §7.11]

| Hạng mục nghiệm thu P1 (phạm vi T1A) | Trạng thái | Bằng chứng đối chiếu |
|---|---|---|
| Đồng bộ có kiểm soát từ commit backend `3025495` + `64b7486`, không copy bẩn, không nới quyền | ✅ ĐẠT | `git merge-base`, `0` diff trên `permissions.py` / `p1_permissions.py` |
| Trang 1 — Kho khách hàng (trạng thái suy ra, bộ lọc, tách bộ đếm lead vs work item) | ✅ ĐẠT | `pool_ui.py`, `pool.js`, [`01-pool-admin.png`](../../deploy/p1/evidence/screenshots/01-pool-admin.png), [`11-pool-bod-zero-pii.png`](../../deploy/p1/evidence/screenshots/11-pool-bod-zero-pii.png) |
| Trang 2 — Nhập danh sách (CSV/XLSX, mapping, preview, confirm, tiến độ Bước 3, lịch sử, tải file lỗi theo quyền, retry lỗi) | ✅ ĐẠT | `import_history.py`, `import.js`, `E2E-02`, `E2E-03`, [`02-import-admin-step3-and-history.png`](../../deploy/p1/evidence/screenshots/02-import-admin-step3-and-history.png) |
| Trang 3 — Chiến dịch & quy tắc (tạo/sửa/đóng, weight %, `capacity=0` = “Không giới hạn”, preview 5 cột, chống double-submit, stale preview, tra cứu & retry/resume run) | ✅ ĐẠT | `campaigns.js`, `E2E-04`, [`03-campaigns-admin.png`](../../deploy/p1/evidence/screenshots/03-campaigns-admin.png), [`04-campaigns-error-not-found.png`](../../deploy/p1/evidence/screenshots/04-campaigns-error-not-found.png) |
| Trang 4 — Khách được giao (danh sách, tìm kiếm/sắp xếp, chi tiết & lịch sử phụ trách, reassign/revoke đúng quyền & `expected_owner`) | ✅ ĐẠT | `ownership_ui.py`, `my-customers.js`, `E2E-05`, [`05-my-customers-admin-ownership-history.png`](../../deploy/p1/evidence/screenshots/05-my-customers-admin-ownership-history.png), [`06-my-customers-staff-a.png`](../../deploy/p1/evidence/screenshots/06-my-customers-staff-a.png), [`08-my-customers-leader-a-team-scope.png`](../../deploy/p1/evidence/screenshots/08-my-customers-leader-a-team-scope.png) |
| Trang 5 — Dashboard & lịch sử (filter chiến dịch/ngày/đội/nhân viên/run, phân biệt rõ current owner vs assignment events) | ✅ ĐẠT | `dashboard.js`, `E2E-05`, `E2E-06`, [`09-dashboard-leader-b-team-isolation.png`](../../deploy/p1/evidence/screenshots/09-dashboard-leader-b-team-isolation.png), [`10-dashboard-bod-live-vs-events.png`](../../deploy/p1/evidence/screenshots/10-dashboard-bod-live-vs-events.png) |
| Không biến lỗi backend thành danh sách rỗng, số `0` hay báo thành công giả | ✅ ĐẠT | `cskh.renderError` + `p1_errors`, kiểm chứng `403`/`404`/`409` trên HTTP & Chromium |
| Việt hóa 100% nhãn, trạng thái, validation, thông báo và lỗi P1 | ✅ ĐẠT | `vi.csv` 577 dòng, `check_translations_audit.py` (`372/372`, `0` chuỗi sót) |
| Render an toàn (chống XSS/formula injection), CSRF, 0 lỗi console/asset/500 | ✅ ĐẠT | `verify_p1_browser_e2e.mjs` → `browser-manifest.json` (`11/11` PASS) |
| Toàn bộ suite P1 + UI + P0 xanh tuần tự | ✅ ĐẠT | `run_suites.py` (`297/297` method PASS trên 15 module) |
| Gắn job Playwright UI vào `.github/workflows/` | ⏳ Chưa gắn workflow CI | Có sẵn script tự động `verify_p1_browser_e2e.mjs` để T1C chạy trực tiếp; chưa sửa `.github/workflows/p1.yml` của T1B |

> **Kết luận bàn giao của T1A:** Toàn bộ 5 màn hình P1 §9, bản dịch tiếng Việt 100%, bộ kiểm thử endpoint UI, E2E HTTP đa vai trò và E2E trình duyệt Chromium thật đã hoàn tất trên nhánh `codex/p1-ui` (PR #5) trên nền backend `3025495f58bc0391c3f8d092af500b0566a5f038` + `64b7486f29ad712106d167dfaea50e3b060a6845`, sẵn sàng để **T1C** kiểm định độc lập và để **Owner** trực tiếp duyệt trên `http://p1.localhost:8120`. Quyết định merge và ký GO P2 thuộc T0/Owner.
### 7.9. Bổ sung bàn giao Đợt 2 — Tích hợp phân phối nền RQ, bản sửa `L-16..L-22` & Cổng CI `test_ui_p1_endpoints`

#### Nguyên văn yêu cầu bổ sung (Verbatim Prompt Đợt 2)

```text
T1A, đã nhận bàn giao d0842cc trên PR #5.
Giữ bộ evidence hiện tại làm baseline, không chụp lại chỉ để đổi SHA.

Việc còn lại:
1. Phối hợp T1B tích hợp phân phối nền:
   execute trả run_id/trạng thái; UI polling tiến độ,
   phục hồi theo dõi sau reload và xử lý failed/completed_with_errors.
   Không coi enqueue thành công là phân phối hoàn tất.
2. Tích hợp các bản sửa backend còn lại vào PR #5 có kiểm soát.
3. Phối hợp đưa 6 test_ui_p1_endpoints vào CI;
   thống nhất người sửa workflow với T1B.
4. Chạy lại E2E phần bị ảnh hưởng, bàn giao SHA tích hợp cuối.
5. Giữ ứng dụng đang chạy cho Owner.

Evidence ghi revision 0d3f2bc là hợp lý: T0 đã đối chiếu,
d0842cc chỉ cập nhật evidence/docs, không đổi code app so với mốc đó.

Không merge, không tự ký GO P2, không gọi agent khác.
```

#### Kết quả thực hiện & đối soát trên revision tích hợp `63510483a0aa1ed4238b630ba384c5a362659450`

1. **Bảo toàn bộ bằng chứng baseline (`0d3f2bc` / `d0842cc`):**
   * Giữ nguyên `0` thay đổi trên toàn bộ `deploy/p1/evidence/screenshots/*.png` (11 ảnh + 5 alias chuẩn), `deploy/p1/evidence/browser-manifest.json` và `deploy/p1/evidence/http-e2e-report.json`.
   * Các báo cáo E2E chạy lại cho phần thay đổi được xuất riêng ra [`deploy/p1/evidence/async-distribution-delta-e2e.json`](../../deploy/p1/evidence/async-distribution-delta-e2e.json) và [`deploy/p1/evidence/http-e2e-delta-report.json`](../../deploy/p1/evidence/http-e2e-delta-report.json).
2. **UI Phân phối nền RQ (`/cskh/campaigns`, `/cskh/dashboard`, `run_history.py`):**
   * Khi `POST crm_cskh.api.distribution.execute` trả sớm `run_id` kèm `status = "queued"` (hoặc `"running"`), `campaigns.js` hiển thị khung thông tin `.cskh-alert.is-info` (`Đã xếp hàng đợi phân phối. Đang chờ tiến trình nền...` / `Đang thực thi phân phối nền...`) kèm `Mã đợt chạy`, `Tiến độ` (`progress / total (pct%)`), `Đã giao trong đợt này`, `Thất bại` và nút `Làm mới` — **tuyệt đối không báo `Đã phân phối xong.` khi mới enqueue**.
   * Single-flight polling `crm_cskh.api.distribution.get_run` mỗi `1000ms` cho tới khi đợt chạy sang trạng thái kết thúc (`completed`, `completed_with_errors`, `failed`).
   * **Phục hồi theo dõi sau reload (`recoverActiveRun`):** lưu đợt chạy đang hoạt động vào `sessionStorage` (`cskh_active_dist_run`) và query `?run_id=<id>`, đồng thời tự gắn lại từ `run_history.runs` nếu trong phạm vi có đợt chạy `queued`/`running`; khi F5/reload giữa lúc job đang chờ hoặc đang chạy, UI tự nối lại polling tới khi hoàn tất.
   * **Xử lý trạng thái kết thúc có lỗi / thất bại:** `completed_with_errors` hiển thị cảnh báo vàng `.is-warning` kèm bảng lý do bỏ qua tiếng Việt và nút `Chạy lại công việc thất bại`; `failed` hiển thị cảnh báo đỏ `.is-error` kèm thông điệp lỗi tiếng Việt chuẩn hóa + `[MÃ_LỖI]` (ví dụ `[RULE_CHANGED]`) và nút `Chạy lại công việc thất bại`.
   * Bổ sung tùy chọn lọc `Trong hàng đợi` (`queued`) ở lịch sử đợt chạy và cập nhật nhãn/badge trên `/cskh/dashboard`; bổ sung đầy đủ chuỗi mới vào `p1_strings.py`, `p1_translations.py` và `translations/vi.csv` (**587 dòng**, `383` chuỗi khai báo, `0` thiếu, `0` tiếng Anh sót).
3. **Tích hợp có kiểm soát backend `c49f7f89d18cfbc3c21a3970d66b07c5d6c11279` (`6ce0341` + `c49f7f8` từ PR #4):**
   * Merge trực tiếp từ Git (`git merge --no-ff c49f7f89d18cfbc3c21a3970d66b07c5d6c11279`) thành SHA mã nguồn tích hợp **`63510483a0aa1ed4238b630ba384c5a362659450`**; `0` dòng thay đổi trên `permissions.py` và `p1_permissions.py`; giữ nguyên 100% chương 0–5 của T1B ở trên.
   * Hợp nhất có chủ đích các tệp dùng chung: giữ `hooks.py`, `p1_errors.py`, `services/errors.py`, `services/import_service.py` của T1B (`L-16`, `L-18`, `L-19`, `L-20`), hợp nhất `services/reporting.py` để giữ đồng thời `L-17` + `L-22` của T1B và phần mở rộng không lộ PII BOD trên `pool_list` của UI.
4. **Đưa đủ `6` method `test_ui_p1_endpoints` vào cổng CI (`.github/workflows/p1.yml`):**
   * Phân chia rõ phần sửa `.github/workflows/p1.yml` không chồng chéo với T1B: T1B cập nhật bước `p1_http_transport` (khởi chạy RQ workers `short` + `long`), T1A bổ sung [`deploy/p1/ui_test_inventory.py`](../../deploy/p1/ui_test_inventory.py) + [`deploy/p1/ui_test_inventory.json`](../../deploy/p1/ui_test_inventory.json) (`expected_total = 6`), kiểm tra `check_translations_audit.py` ở bước `inventory`, chạy `crm_cskh.tests.test_ui_p1_endpoints` xuất JUnit XML riêng vào `/tmp/p1-evidence/ui/` và khóa cổng `ci_junit_gate.py` song song với `p1_test_inventory.json` (`199` method).
5. **Đối soát toàn bộ suite & E2E phần bị ảnh hưởng trên SHA `63510483a0aa1ed4238b630ba384c5a362659450`:**
   * `run_suites.py`: **`15/15` module PASS (`312/312` method)** — P1 backend `199/199` + P1 UI `6/6` + P0 core `99/99` + P0 transport `8/8`.
   * `verify_p1_async_dist_e2e.mjs` → [`async-distribution-delta-e2e.json`](../../deploy/p1/evidence/async-distribution-delta-e2e.json): **`4/4` nhóm PASS** trên Chromium thật + HTTP thật (`ASYNC-01` enqueue trả `queued` không báo xong sớm + F5 reload phục hồi theo dõi + polling tới `4 / 4 (100%)`; `ASYNC-02` `completed_with_errors`; `ASYNC-03` `failed` `[RULE_CHANGED]` tiếng Việt; `BACKEND-DELTA` kiểm chứng `L-20` CSV >200 dòng tải đủ 13 dòng lỗi, `L-21` `revoke(return_to_pool=false)` form-urlencoded → `revoked`, `L-22` cô lập thẻ dashboard `Leader A` vs `Leader B`).
   * `verify_p1_http_e2e.py` → [`http-e2e-delta-report.json`](../../deploy/p1/evidence/http-e2e-delta-report.json): **`6/6` nhóm HTTP E2E PASS** (đã cập nhật `poll_run` cho `distribution.execute` bất đồng bộ) và để lại dữ liệu giả sạch trên `http://p1.localhost:8120` để Owner kiểm tra trực tiếp.

### 7.10. Bổ sung bàn giao Đợt 3 — Đóng `L-23`, `L-24`, `L-25` (filter Import Batch), `L-26`, Đính chính Provenance & Regression `11/11` [Mốc lịch sử Đợt 3 — `3f07b9b` / `5c37bf3`; hiện hành xem §7.11]

> *[Mốc lịch sử Đợt 3 — `3f07b9b` / `5c37bf3`]*: Mục §7.10 giữ nguyên văn số liệu và danh sách các endpoint chờ backend T1B tại thời điểm kết thúc Đợt 3. Ở bản tích hợp cuối hiện hành (**§7.11**), toàn bộ các endpoint backend còn chờ (`L-23` `pool_list`, `L-26` phân trang/limit backend, direct ownership mutation eligibility, và `L-29` khóa quyền DocType/Document/`permlevel = 1`/`search_link`) đã được tích hợp từ `2235757` + `77e07da` và kiểm chứng đầy đủ.

#### Nguyên văn yêu cầu bổ sung (Verbatim Prompt Đợt 3 & Hoàn tất bàn giao)

```text
T1A, tiếp tục PR #5 từ a75c093. Giữ các bản sửa async/polling/CI đã đạt.

Ưu tiên:
1. Đóng L-23: pool_ui không được dùng lịch sử revoked/closed của Đội A
   để lộ Lead đang active ở Đội B. Kiểm tra cả phone, owner, campaign,
   items, total, search và filter theo quyền hiện tại.
2. Đóng L-24: run_history của campaign chung phải giới hạn dữ liệu
   theo actor, gồm assigned, skipped, preview, counters và polling.
3. Đóng L-26: pagination sai trả 400 có mã ổn định; assignees(campaign)
   áp eligibility/team đúng contract.
4. Phối hợp T1B nối filter Import Batch với quan hệ dữ liệu thực tế.
5. Thêm regression có dữ liệu hai team và positive control;
   cập nhật UI inventory/CI theo số method thực tế.

Provenance cần đính chính:
async-distribution-delta-e2e.json và generator ghi sai full SHA baseline:
0d3f2bc9da3e3e497a999baeb9a4dd19129224fb.
Commit thực tế là:
0d3f2bcbbf9715a4c0c783903ce750de8646583d.
Sửa generator và ghi đính chính, giữ nguyên kết quả đo đã lưu.

Bàn giao commit và evidence cho T1C.
Không merge, không gọi agent khác.
```

```text
T1A, tiếp tục hoàn tất bàn giao commit local aedb7b4;
không triển khai lại các bản sửa đã làm.

- Hoàn thiện evidence, PR body và P1-Exit-Report đang sửa.
- Khôi phục tiếng Việt §7.9 từ nội dung gốc, ghi UTF-8 nguyên vẹn.
- Phân biệt số liệu lịch sử với số liệu hiện hành; không thay lịch sử.
- Đính chính full SHA baseline đã ghi sai.
- Push revision tích hợp, kiểm tra CI gồm UI inventory 11 method
  và bàn giao SHA/artifact cho T1C.
- Ghi rõ endpoint nào đã sửa L-26, endpoint nào thuộc T1B còn chờ.

Không merge, không tự ký GO P2, không gọi agent khác.
```

#### Đối chiếu các mốc lịch sử (giữ nguyên) với mốc hiện hành Đợt 3

> **Ghi chú provenance mã ứng dụng Đợt 3:** Commit local `aedb7b4d407a284892f947b5e0c502aadd9a0bbf` chỉ được amend đúng 1 dòng destructuring tham số test trong script `deploy/p1/verify_p1_round3_l23_l26_e2e.mjs` để tạo SHA mã nguồn/test/CI **`3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b`** (`0` dòng thay đổi trên toàn bộ `apps/` và `.github/` so với `aedb7b4`). Mọi số liệu và tệp bằng chứng của Đợt 1 và Đợt 2 được giữ nguyên lịch sử, không bị ghi đè.

| Hạng mục / Chỉ số | Đợt 1 — Baseline lịch sử (§7.1–§7.8) | Đợt 2 — Lịch sử async/polling (§7.9) | Đợt 3 — Hiện hành (§7.10) |
|---|---|---|---|
| SHA mã ứng dụng / tích hợp | `0d3f2bcbbf9715a4c0c783903ce750de8646583d` (bàn giao docs `d0842cc`) | `63510483a0aa1ed4238b630ba384c5a362659450` (bàn giao docs `a75c093`) | **`3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b`** (amend E2E từ local `aedb7b4`; `0` diff `apps/` & `.github/`) |
| Commit backend PR #4 tích hợp | `3025495f58bc0391c3f8d092af500b0566a5f038` + `64b7486f29ad712106d167dfaea50e3b060a6845` | `c49f7f89d18cfbc3c21a3970d66b07c5d6c11279` (`6ce0341` + `c49f7f8`) | **`c997df76b9a29a9f8d8be4a4ffd0875035762672`** (merge tại `e85ec9f365a5f86fe29e1a69a71a4fb048ce63ab`) |
| Suite `run_suites.py` (`15` module) | `297/297` (`184` P1 + `6` UI + `99` P0 + `8` transport) | `312/312` (`199` P1 + `6` UI + `99` P0 + `8` transport) | **`321/321` (`203` P1 + `11` UI + `99` P0 + `8` transport)** |
| Cổng CI `ui_test_inventory.json` | Chưa gắn `.github/workflows/p1.yml` (`6` method local) | `expected_total = 6` trên CI | **`expected_total = 11` trên CI** |
| Bản dịch `translations/vi.csv` | `577` dòng (`372` chuỗi khai báo) | `587` dòng (`383` chuỗi khai báo) | **`588` dòng (`384` chuỗi khai báo, `210` sử dụng, `0` thiếu/sót/dư)** |
| Bằng chứng lưu trữ (không ghi đè lịch sử) | `screenshots/*.png`, `browser-manifest.json`, `http-e2e-report.json` | `async-distribution-delta-e2e.json` (đã đính chính SHA baseline), `http-e2e-delta-report.json` | **`round3-l23-l26-delta-e2e.json`**, **`round3-http-e2e-report.json`** |

#### Kết quả thực hiện & đối soát trên revision mã ứng dụng `3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b`

1. **Đóng `L-23` (`apps/crm_cskh/crm_cskh/api/pool_ui.py`):**
   * Áp 5 mệnh đề ủy quyền cấp SQL theo quyền **hiện tại** (`_team_lead_authorization_clauses`) cho `scope == "team"`: loại bỏ tuyệt đối mọi `CRM Lead` đang có `Work Item` trạng thái `assigned` ngoài `team_users`, đang `queued` trong chiến dịch riêng ngoài `team_nodes`, đang giữ `CSKH Active Claim` ngoài `team_nodes`/`team_users`, hoặc có `CRM Lead.lead_owner` ngoài `team_users`; đồng thời với `Work Item` trạng thái `revoked`/`closed` thuộc chiến dịch chung (`team_scope IS NULL`), chỉ nhận khi `CSKH Assignment History` xác nhận từng giao cho thành viên thuộc `team_users`.
   * Suy diễn trạng thái chính (`wi_cur`), đếm `total` / `unique_customers` / `work_items`, tìm kiếm `search` (`0...` & `+84...`) và phân trang `LIMIT %s OFFSET %s` trực tiếp trên MariaDB — **0 rò rỉ** `phone`, `display_name`, `owner`, `campaign`, `campaign_title`, `work_item`, `items`, `total`, `unique_customers`, `work_items` hay oracle `search`/filter từ lịch sử `revoked`/`closed` cũ của Đội A sang Lead đang hoạt động ở Đội B.
2. **Đóng `L-24` (`apps/crm_cskh/crm_cskh/api/run_history.py`, `apps/crm_cskh/crm_cskh/public/cskh/campaigns.js`):**
   * Trên cả chiến dịch chung (`team_scope IS NULL`) lẫn chiến dịch đội, khi `scope == "team"`, `run_history.runs` giới hạn chặt theo `team_users` / `team_nodes`: `assigned` (chỉ nhân sự thuộc đội, có đối chiếu fallback từ `tabCSKH Work Item`), `skipped` (lọc qua `filter_team_authorized_leads`), `preview` (`targets`, `simulated`, `excluded_assignees` theo đội, `eligible_count` theo tổng mục tiêu đội, `excluded_count = 0` trên chiến dịch chung và không bao giờ trả `preview_json["leads"]`), cùng toàn bộ bộ đếm `progress`, `total`, `created`, `errors` ở mọi trạng thái `planned` / `queued` / `running` / `completed` / `completed_with_errors`.
   * Trên `/cskh/campaigns`, `campaigns.js` bổ sung `fetchRunForActor(runId)` để `Admin`/`BOD` poll `distribution.get_run` còn `Sales Manager` poll `run_history.runs(run_id=...)` kèm tự động gắn lại và phục hồi sau F5/reload cho đúng phạm vi đội.
3. **Đóng `L-26` trong phạm vi UI PR #5 & Phân định rõ endpoint còn chờ T1B (PR #4):**
   * **Các endpoint thuộc T1A đã sửa hoàn tất `L-26` trên PR #5 (`3f07b9b`):**
     * `GET crm_cskh.api.pool_ui.list(page, page_size, ...)` (`api/pool_ui.py` + `api/_ui_params.py::parse_pagination`): trả HTTP `400` với `code = "INVALID_REQUEST"`, `p1_code = "INVALID_REQUEST"` ổn định khi `page`/`page_size` là boolean, chuỗi không phải số nguyên, `< 1`, `page > 1_000_000` hoặc `page_size > 100`.
     * `GET crm_cskh.api.ownership_ui.assigned_list(page, page_size, ...)` (`api/ownership_ui.py` + `parse_pagination`): trả HTTP `400 INVALID_REQUEST` ổn định cho mọi tham số phân trang sai.
     * `GET crm_cskh.api.import_history.history(page, page_size, ...)` (`api/import_history.py` + `parse_pagination`): trả HTTP `400 INVALID_REQUEST` ổn định cho mọi tham số phân trang sai.
     * `GET crm_cskh.api.run_history.runs(page, page_size, ...)` (`api/run_history.py` + `parse_pagination`): trả HTTP `400 INVALID_REQUEST` ổn định cho mọi tham số phân trang sai.
     * `GET crm_cskh.api.ownership_ui.assignees(campaign=None)` (`api/ownership_ui.py`, nối trên `campaigns.js` & `my-customers.js`): kiểm tra chiến dịch tồn tại (`404 NOT_FOUND` nếu sai mã), giới hạn trong cây `CRM Sales Hierarchy` của `campaign.team_scope`, chỉ giữ tài khoản `User.enabled = 1` có role `Sales User`, và loại trừ nhân sự bị quy tắc ở `current_rule_version` vô hiệu hóa (`enabled = 0`), `weight <= 0`, lệch `team`, hoặc chạm trần `capacity_limit > 0`.
   * **Các endpoint thuộc backend T1B (PR #4 `codex/p1-foundation`) còn chờ sửa `L-26`:**
     * `GET crm_cskh.api.customers.my_list(page=1, page_size=20, ...)` (`api/customers.py` → `services/reporting.py:245-246`): vẫn clamp âm thầm `page <= 0` / `page_size <= 0` / `page_size > 100`, nhận boolean và ném `ValueError` (`HTTP 500`) khi truyền chuỗi không phải số nguyên thay vì trả `400 INVALID_REQUEST` *(UI `/cskh/my-customers` gọi `ownership_ui.assigned_list`, không gọi `customers.my_list`)*.
     * `GET crm_cskh.api.customers.pool_list(page=1, page_size=20, ...)` (`api/customers.py` → `services/reporting.py:353-354`): vẫn clamp âm thầm biên phân trang / ném `ValueError` (`HTTP 500`) thay vì trả `400 INVALID_REQUEST`.
     * `GET crm_cskh.api.customers.customer_pool(page=1, page_size=20, ...)` (`api/customers.py` → `services/reporting.py:554-556`, thêm ở `c997df7`): vẫn clamp âm thầm `page`/`page_size` và ném `ValueError` (`HTTP 500`) với chuỗi không phải số nguyên thay vì trả `400 INVALID_REQUEST` *(UI `/cskh/pool` gọi `pool_ui.list` đã đóng đủ `L-23` + `L-25` + `L-26`, không gọi `customers.customer_pool`)*.
     * `GET crm_cskh.api.ownership.history(work_item=None, limit=50)` (`api/ownership.py:90`): tham số `limit` vẫn clamp âm thầm `1..200` / ném `ValueError` (`HTTP 500`) với chuỗi không phải số nguyên thay vì trả `400 INVALID_REQUEST`.
     * `POST crm_cskh.api.ownership.assign` & `POST crm_cskh.api.ownership.reassign` (`api/ownership.py` → `services/ownership.py:67-74,295,355,381`): tầng service của T1B hiện kiểm tra `User.enabled = 1` + role `Sales User` (và `_capacity_limit` ở `reassign`), chưa kiểm tra `target_user` thuộc `campaign.team_scope` hoặc trạng thái `enabled` của `CSKH Assignment Rule` ở tầng mutation trực tiếp (trên UI hộp chọn đã khóa qua `ownership_ui.assignees(campaign)`).
4. **Phối hợp T1B đóng `L-25` & nối bộ lọc `Import Batch` (`c997df76b9a29a9f8d8be4a4ffd0875035762672` + `pool_ui.py`):**
   * Hợp nhất có kiểm soát commit `c997df76b9a29a9f8d8be4a4ffd0875035762672` từ `origin/codex/p1-foundation` (bổ sung DocType `CSKH Import Batch Lead`, ánh xạ `phone`/`name`/`source` có sanitize công thức/HTML, không ghi đè `CRM Lead` canonical đã tồn tại).
   * Nối trực tiếp `_batch_lead_predicate` và `_batch_wi_predicate` trong `pool_ui.py` tới `reporting.lead_in_batch_clause` và `reporting.work_item_in_batch_clause`, giúp bộ lọc `Lô` (`#pool-batch` → `batch=`) khớp chính xác cả khách mới import chưa có `Work Item` (`state = "eligible"`), khách canonical được reuse qua nhiều lô (`outcome = "reused"`), và khách đã phân phối sang chiến dịch đội mà vẫn giữ nguyên lớp chắn cô lập `L-23`.
5. **Regression hai đội + positive control & cập nhật Inventory/CI (`11` method UI + `203` method P1):**
   * Mở rộng [`test_ui_p1_endpoints.py`](../../apps/crm_cskh/crm_cskh/tests/test_ui_p1_endpoints.py) từ `6` lên **`11` method** (`test_l23_...`, `test_l24_...`, `test_l25_...`, `test_l26_...`, `test_upload_import_file_...`) có đủ dữ liệu Đội A, Đội B, chiến dịch chung, và positive control cho `Leader A`, `Leader B`, `CRM Admin`, `BOD`.
   * Cập nhật [`deploy/p1/ui_test_inventory.json`](../../deploy/p1/ui_test_inventory.json) (`expected_total = 11`), `.github/workflows/p1.yml` (`11 methods` + kiểm tra index `tabCSKH Import Batch Lead`) và `translations/vi.csv` (**588 dòng**, `declared = 384`, `0` thiếu / `0` tiếng Anh sót).
   * `run_suites.py`: **`15/15` module PASS (`321/321` method)** — P1 backend `203/203` + P1 UI `11/11` + P0 core `99/99` + P0 transport `8/8`.
   * Xuất riêng bằng chứng Đợt 3 tại [`deploy/p1/evidence/round3-l23-l26-delta-e2e.json`](../../deploy/p1/evidence/round3-l23-l26-delta-e2e.json) (`3/3` nhóm PASS trên Chromium + HTTP + RQ thật) và [`deploy/p1/evidence/round3-http-e2e-report.json`](../../deploy/p1/evidence/round3-http-e2e-report.json) (`6/6` nhóm HTTP E2E PASS), không chạm vào `http-e2e-report.json` hay `http-e2e-delta-report.json`.
6. **Đính chính Provenance (`0d3f2bcbbf9715a4c0c783903ce750de8646583d`):**
   * Sửa [`deploy/p1/verify_p1_async_dist_e2e.mjs`](../../deploy/p1/verify_p1_async_dist_e2e.mjs) lấy SHA từ `git rev-parse --verify 0d3f2bcbbf9715a4c0c783903ce750de8646583d^{commit}` (thay vì chuỗi cứng sai `0d3f2bc9da3e3e497a999baeb9a4dd19129224fb`).
   * Ghi đính chính `baseline_evidence_sha_preserved = "0d3f2bcbbf9715a4c0c783903ce750de8646583d"` kèm khối `provenance_erratum` trong [`deploy/p1/evidence/async-distribution-delta-e2e.json`](../../deploy/p1/evidence/async-distribution-delta-e2e.json), bảo toàn nguyên vẹn `generated_at`, `integrated_git_sha` và toàn bộ kết quả đo `checks[]`.

### 7.11. Bàn giao Tích hợp Cuối (Đợt 4 — Hiện hành: UI `5c37bf3` + Backend `2235757` + `77e07da` / `L-29`)

#### Nguyên văn yêu cầu bàn giao cuối (Verbatim Prompt Đợt 4)

```text
T1A, chuẩn bị bản tích hợp cuối gồm:
- UI/docs hiện tại 5c37bf3;
- backend 2235757;
- bản sửa L-29 sắp bàn giao từ T1B.

Bảo toàn L-23/L-24 ở UI và các bản sửa UTF-8/provenance.
Kiểm tra UI Leader/BOD sau thay đổi quyền DocType/field;
không cấp quyền rộng để khắc phục lỗi hiển thị.

Gắn nhãn rõ các số liệu lịch sử trong Exit Report nếu còn thiếu.
Bàn giao một SHA code tích hợp cùng CI/artifact cho T1C.
Không merge PR vào main, không gọi agent khác.
```

#### Bảng tổng hợp phân biệt các mốc lịch sử (Đợt 1–3) và mốc hiện hành (Đợt 4)

| Hạng mục / Chỉ số | Đợt 1 — Lịch sử (§7.0–§7.8) | Đợt 2 — Lịch sử (§7.9) | Đợt 3 — Lịch sử (§7.10) | **Đợt 4 — Hiện hành (§7.11)** |
|---|---|---|---|---|
| SHA mã ứng dụng / tích hợp | `0d3f2bcbbf9715a4c0c783903ce750de8646583d` (docs `d0842cc`) | `63510483a0aa1ed4238b630ba384c5a362659450` (docs `a75c093`) | `3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b` (docs `5c37bf3`) | **`9fdbb8dfdd9ad443ffe05057a969a1e039473989`** |
| Nguồn gốc commit hợp nhất | Backend `3025495` + `64b7486` | Backend `c49f7f8` (`6ce0341` + `c49f7f8`) | Backend `c997df7` (`L-25`) | **UI/docs `5c37bf3` + Backend `2235757` + `77e07da` (`L-29`)** |
| Suite `run_suites.py` (`15` module) | `297/297` (`184` P1 + `6` UI + `99` P0 + `8` transport) | `312/312` (`199` P1 + `6` UI + `99` P0 + `8` transport) | `321/321` (`203` P1 + `11` UI + `99` P0 + `8` transport) | **`327/327` (`208` P1 + `12` UI + `99` P0 + `8` transport)** |
| Cổng CI inventory (`p1` / `ui` / `p0`) | `184` P1 / chưa gắn UI CI / `99` P0 | `199` P1 / `6` UI / `99` P0 | `203` P1 / `11` UI / `99` P0 | **`208` P1 / `12` UI / `99` P0** (`ci_p1_http_probe` **`158/158`**) |
| Bản dịch `translations/vi.csv` | `577` dòng (`372` khai báo) | `587` dòng (`383` khai báo) | `588` dòng (`384` khai báo, `210` dùng) | **`613` dòng dịch (`614` dòng tệp; `409` khai báo, `210` dùng, `0` lỗi)** |
| Bằng chứng lưu trữ (không ghi đè lịch sử) | `screenshots/*.png`, `browser-manifest.json`, `http-e2e-report.json` | `async-distribution-delta-e2e.json` (đã đính chính SHA baseline), `http-e2e-delta-report.json` | `round3-l23-l26-delta-e2e.json`, `round3-http-e2e-report.json` | **`round4-final-integration-e2e.json`**, **`round4-http-e2e-report.json`** |

#### Kết quả thực hiện & kiểm chứng trên SHA tích hợp cuối `9fdbb8dfdd9ad443ffe05057a969a1e039473989`

1. **Hợp nhất Git sạch một mốc mã nguồn tích hợp (`9fdbb8dfdd9ad443ffe05057a969a1e039473989`):**
   * Merge trực tiếp commit backend PR #4 **`77e07dac7948c156039bc6a9746fe5d451fc0d71`** (đã bao gồm cha **`2235757b79bb9f1a7fb3ae2957b28deb088185b9`**) lên trên **`5c37bf3dc464a0d8e2b9bd94ba55df40da469559`**:
     * **`0` dòng thay đổi** so với `77e07dac7948c156039bc6a9746fe5d451fc0d71` trên toàn bộ tệp phân quyền và schema DocType backend (`apps/crm_cskh/crm_cskh/permissions.py`, `apps/crm_cskh/crm_cskh/p1_permissions.py`, `apps/crm_cskh/crm_cskh/crm_cskh/doctype/**/*.json`); kiểm tra trực tiếp MariaDB xác nhận `tabCustom DocPerm = 0 dòng`, đủ `7` trường `permlevel = 1` (`CSKH Campaign.selection_json` và `CSKH Distribution Run.{actual_json, error, lease_expires_at, preview_json, progress, state_json}`), và `write = 0 / create = 0 / delete = 0` cho toàn bộ business role trên các DocType P1.
     * **`0` dòng thay đổi** so với `5c37bf3dc464a0d8e2b9bd94ba55df40da469559` trên các bản sửa cô lập dữ liệu UI `L-23` (`apps/crm_cskh/crm_cskh/api/pool_ui.py`), `L-24` (`apps/crm_cskh/crm_cskh/api/run_history.py`), cùng toàn bộ tệp bằng chứng lịch sử Đợt 1 / Đợt 2 / Đợt 3 (bao gồm bản đính chính provenance `0d3f2bcbbf9715a4c0c783903ce750de8646583d` trong `async-distribution-delta-e2e.json` và nguyên văn tiếng Việt UTF-8 của **§7.9**).
     * Hợp nhất có chủ đích `apps/crm_cskh/crm_cskh/hooks.py`: giữ đồng thời toàn bộ P0/UI hooks (`website_route_rules`, `before_request`, `after_request = normalize_p1_error_response`) và toàn bộ `L-29` hooks từ `77e07dac7948c156039bc6a9746fe5d451fc0d71` (`permission_query_conditions`, `has_permission`, `override_whitelisted_methods` khóa `frappe.client.get_list`/`get_count`/`frappe.desk.reportview.*`, `auth_hooks = enforce_p1_http_filter_guard`).
2. **Đóng trọn vẹn các mục từng chờ T1B ở §7.10 (`L-23` backend, `L-26` backend, Direct Ownership Mutation & `L-29`):**
   * **Phân trang & tham số giới hạn (`L-26` toàn diện cả UI lẫn Backend — `8/8` endpoint):** ngoài `4` endpoint UI (`pool_ui.list`, `ownership_ui.assigned_list`, `import_history.history`, `run_history.runs`), cả `4` endpoint backend (`customers.my_list`, `customers.pool_list`, `customers.customer_pool`, `ownership.history`) nay đều dùng `parse_strict_int` và trả HTTP `400` với `code = "INVALID_REQUEST"`, `p1_code = "INVALID_REQUEST"` ổn định.
   * **Cô lập đội trên mọi endpoint kho (`L-23` toàn diện):** cả `pool_ui.list`, `customers.customer_pool` và `customers.pool_list` đều áp điều kiện ủy quyền theo quyền hiện tại, không lộ Lead đang hoạt động ở Đội B qua lịch sử `revoked`/`closed` của Đội A.
   * **Mutation sở hữu trực tiếp (`ownership.assign` & `ownership.reassign`):** kiểm tra trực tiếp `campaign.status == "Active"`, cây `campaign.team_scope`, phiên bản mới nhất của `CSKH Assignment Rule` (`enabled = 1`, khớp `team`, còn sức chứa `capacity_limit`) và `CSKH Active Claim` chéo chiến dịch.
3. **Kiểm tra toàn diện giao diện `Sales Manager` (Leader A / Leader B), `CSKH BOD Viewer` (BOD) & `Sales User` (Staff) sau khóa quyền `L-29` (tuyệt đối không nới quyền backend):**
   * Rà soát toàn bộ `www/cskh/*.py`, `www/cskh/*.html` và `public/cskh/*.js` sau khi `L-29` khóa cứng `has_permission`, `permlevel = 1` và `search_link`:
     * Mọi màn hình CSKH gọi qua các API kiểm soát (`pool_ui.list`, `campaigns.list`, `campaigns.rules`, `run_history.runs`, `distribution.get_run`, `ownership_ui.assigned_list`, `ownership.history`, `dashboard.distribution`, `dashboard.campaigns`) — không phụ thuộc đọc thô `selection_json` hay `preview_json`/`actual_json`/`state_json` từ `/api/resource`.
     * **Tinh chỉnh sạch phía UI (0 thay đổi quyền backend):**
       1. `www/cskh/index.html`: đồng bộ các thẻ điều hướng trên trang chủ `/cskh` theo đúng `cskh_navigation` của từng vai trò (ẩn thẻ `/cskh/import` đối với `Leader` & `BOD`, và chỉ hiển thị thẻ `/cskh/my-customers` đối với `Staff`, khớp 100% thanh điều hướng `_shell.html`).
       2. `public/cskh/campaigns.js`: khởi tạo `canLookup` ngay từ `cskh.capabilities().admin || cskh.capabilities().bod` để ẩn tức thì ô `Mã đợt chạy` / nút `Tra cứu đợt chạy` đối với `Leader` ngay từ khung hình đầu tiên (không nhấp nháy UI trái quyền trước khi `loadRuns()` trả về), đồng thời hiển thị & polling lịch sử đợt chạy theo phạm vi đội (`L-24`) cho `Leader` và giữ nguyên chức năng `Tra cứu đợt chạy` (`distribution.get_run`) cho `BOD` & `Admin`; bổ sung fallback tạo preview mới khi bấm chạy lại đợt `completed_with_errors`.
       3. `public/cskh/my-customers.js`: chỉ gọi `dashboard.campaigns` và `ownership_ui.assigned_list` khi actor có quyền phạm vi (`admin || bod || leader`), còn `Sales User` (`Staff`) tự tổng hợp danh sách lọc chiến dịch từ các công việc của chính mình — loại bỏ hoàn toàn request nền bị `403` trên trình duyệt của nhân viên.
       4. `p1_strings.py`, `p1_translations.py`, `translations/vi.csv`: bổ sung đầy đủ **25** thông điệp ngoại lệ mới từ `2235757` + `77e07da` (`613` dòng dịch / `614` dòng tệp, `409` khai báo, `210` sử dụng, `0` thiếu / `0` tiếng Anh sót / `0` dư).
4. **Kết quả kiểm định thực tế trên `9fdbb8dfdd9ad443ffe05057a969a1e039473989`:**
   * **Ruff + Inventories + Audit dịch Việt:** `ruff==0.14.0 check` + `format --check` **PASS** (`105 + 3 = 108` tệp); `p1_test_inventory.py --check` **PASS** (`208` method); `ui_test_inventory.py --check` **PASS** (`12` method — thêm `test_l27_l29_leader_and_bod_ui_flows_under_strict_doctype_and_permlevel_rules`); `check_translations_audit.py` **PASS** (`0` lỗi).
   * **Suite tuần tự [`deploy/p1/run_suites.py`](../../deploy/p1/run_suites.py):** **`15/15` module PASS (`327/327` method)** — P1 backend `208/208` + P1 UI `12/12` + P0 core `99/99` + P0 transport `8/8`.
   * **E2E Chromium + HTTP + RQ Đợt 4 ([`deploy/p1/verify_p1_round4_final_e2e.mjs`](../../deploy/p1/verify_p1_round4_final_e2e.mjs) → [`deploy/p1/evidence/round4-final-integration-e2e.json`](../../deploy/p1/evidence/round4-final-integration-e2e.json)):** **`4/4` nhóm lớn PASS** (`L29-HTTP-DOCTYPE-PERMLEVEL-LOCKDOWN`, `LEADER-UI-FULL-SCREEN-AUDIT`, `BOD-UI-FULL-SCREEN-AUDIT-ZERO-PII`, `STAFF-ZERO-403-AND-L26-ALL-8-ENDPOINTS-AND-DIRECT-MUTATION`) với `console_errors = 0`, `page_errors = 0`, `http_500 = 0`, `xss_triggered = false`, `staff_background_403 = 0`, `unmasked_phones = 0`.
   * **HTTP E2E đa vai trò Đợt 4 ([`deploy/p1/verify_p1_http_e2e.py`](../../deploy/p1/verify_p1_http_e2e.py) → [`deploy/p1/evidence/round4-http-e2e-report.json`](../../deploy/p1/evidence/round4-http-e2e-report.json)):** **`6/6` nhóm PASS** và giữ nguyên ứng dụng + dữ liệu mẫu sạch trên `http://p1.localhost:8120` để Owner xem trực tiếp.

#### Lệnh dựng & tái hiện bản tích hợp cuối cho T1C

```bash
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 5c37bf3dc464a0d8e2b9bd94ba55df40da469559 HEAD
git merge-base --is-ancestor 2235757b79bb9f1a7fb3ae2957b28deb088185b9 HEAD
git merge-base --is-ancestor 77e07dac7948c156039bc6a9746fe5d451fc0d71 HEAD

cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

python -X utf8 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json
python -X utf8 deploy/p1/ui_test_inventory.py --check deploy/p1/ui_test_inventory.json
python -X utf8 deploy/p1/check_translations_audit.py
python -X utf8 deploy/p1/run_suites.py
node deploy/p1/verify_p1_round4_final_e2e.mjs
P1_REPORT_OUT=deploy/p1/evidence/round4-http-e2e-report.json python -X utf8 deploy/p1/verify_p1_http_e2e.py
```

#### Checklist nghiệm thu bàn giao cuối (Đợt 4 — Hiện hành)

| Hạng mục nghiệm thu cuối | Trạng thái | Bằng chứng đối chiếu |
|---|---|---|
| Tích hợp đầy đủ UI/docs `5c37bf3` + backend `2235757` + bản sửa `L-29` (`77e07da`) vào một SHA code tích hợp | ✅ ĐẠT | `9fdbb8dfdd9ad443ffe05057a969a1e039473989` (`git rev-list --parents -n 1 9fdbb8dfdd9ad443ffe05057a969a1e039473989`) |
| Không nới quyền DocType/field/row backend để chữa lỗi hiển thị UI | ✅ ĐẠT | `0` diff so với `77e07da` trên `permissions.py`, `p1_permissions.py`, `doctype/*.json`; `tabCustom DocPerm = 0` |
| Bảo toàn `L-23` / `L-24` ở UI và các bản sửa UTF-8 (`§7.9`) / provenance (`0d3f2bcbbf9715a4c0c783903ce750de8646583d`) | ✅ ĐẠT | `0` diff so với `5c37bf3` trên `pool_ui.py`, `run_history.py`, `§7.9` và toàn bộ evidence Đợt 1–3 |
| Giao diện `Leader` (`Sales Manager` A/B), `BOD` (`CSKH BOD Viewer`) & `Staff` hoạt động chuẩn sau khóa quyền `L-29` | ✅ ĐẠT | `test_ui_p1_endpoints` (`12/12`), `round4-final-integration-e2e.json` (`4/4` nhóm PASS, `0` lỗi console/500/403 nền) |
| Gắn nhãn rõ các số liệu lịch sử trong `P1-Exit-Report.md` (`L-28`), giữ nguyên chương 0–5 của T1B | ✅ ĐẠT | Chương 0–5 khớp 100% `77e07da`; nhãn lịch sử rõ trên §7.0–§7.10; bảng tổng hợp Đợt 1–4 tại §7.11 |
| Cổng CI GitHub Actions (`P0 DEV verification` + `P1 backend verification` gồm `208` P1 + `12` UI + `158` HTTP probe) | ✅ ĐẠT | PR #5 (`codex/p1-ui`), `run_suites.py` **`327/327` PASS** |
| Giữ ứng dụng đang chạy cho Owner & không merge PR vào `main` | ✅ ĐẠT | `http://p1.localhost:8120` (`HTTP 200`, `bench serve` + `bench worker` chạy nền) |
