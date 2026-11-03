# P1 Exit Report — CRM-CSKH Backend P1

**Ngày cập nhật:** 2026-11-03
**Vai trò:** T1B (hoàn tất backend P1, chứng minh runtime crash/rollback/retry, benchmark 10k & HTTP evidence)
**Nhánh:** `codex/p1-foundation` — worktree `D:/HRP-app/p1-foundation`
**Báo cáo đầu vào:** [`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md`](../../deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md) (L-01 … L-15)
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md) (§10, §12.4, §12.5, §16)
**PR:** [#4 — feat(p1): CSKH P1 backend](https://github.com/nobita6986/new-crm/pull/4) — **OPEN, KHÔNG merge, KHÔNG tự ký GO P2**
**Baseline đã xác minh trước revision này:** `3025495f58bc0391c3f8d092af500b0566a5f038` (CI P1 `#41423954824`: `174/174 PASS`; CI P0 `#41423954877`: `99/99 PASS`, auxiliary `8/8`, HTTP `18/18`, file-oracle `9/9`, remaining-gaps `74/74`)
**Dữ liệu & môi trường:** chỉ dữ liệu giả trên các site cô lập (`p1fresh.localhost`, `p1chaos.localhost`, `p1bench.localhost`, `p1http.localhost`) thuộc stack `crm-cskh-p1-dev-*`; không đụng `p1.localhost` đang phục vụ Owner và không xóa Docker volume.

---

## 0. Tóm tắt

Backend P1 trên nhánh `codex/p1-foundation` đã đóng toàn bộ **15/15** lỗi L-01 … L-15 từ báo cáo
kiểm định T1C cùng các lỗi phụ phát hiện trong quá trình kiểm chứng runtime/benchmark (**L-16 … L-22**),
đồng thời hoàn tất bộ bằng chứng runtime theo `P1 §12.4`:

* **Suite P1 riêng** (`deploy/dev/seed/p1_test_inventory.json`): **8 module / 184 method** (tăng từ
  174 của baseline nhờ bổ sung `test_p1_chaos` gồm 10 method kiểm chứng crash/rollback/retry + kiểm tra
  AST 0 nhánh gây lỗi trong code production), tách biệt hoàn toàn với inventory **99 method** của P0
  (`deploy/dev/seed/g007_test_inventory.json` giữ nguyên 99);
* **Runtime Chaos / Rollback / Retry (`P1 §12.4`):** tiêm lỗi hoàn toàn từ tầng test/harness
  (`unittest.mock.patch` + tiến trình con chết cứng `os._exit(137)`), **xóa bỏ hoàn toàn** module tiêm
  lỗi trong production (`services/chaos.py`), hậu kiểm trực tiếp trên MariaDB (không dựa log `"OK"`);
* **HTTP Transport P1 (`evidence_class = "http"`):** `deploy/dev/seed/ci_p1_http_probe.py` gọi HTTP thật
  tới `bench serve` với cookie phiên + `X-Frappe-CSRF-Token` trên **5 actor nghiệp vụ đã seed + `Guest`**
  (**52/52 case PASS**), phân biệt tường minh với lời gọi hàm service trong unit test;
* **Benchmark 10.000 dòng (`P1 §12.4`):** đo thực tế trên site riêng `p1bench.localhost` qua 2 đợt
  10.000 dòng (`0 → 10.000` và `10.000 → 20.000`) kèm lô hỗn hợp 100 dòng và phân phối giới hạn
  capacity (`30/42/28`), ghi nhận trung thực cả độ chính xác đối soát lẫn hai nút cổ chai hiệu năng
  thực tế (không tự đặt SLA, không coi việc chạy xong là đạt mọi tiêu chí hiệu năng);
* **API contract P1** ([`api-contract-p1.md`](./api-contract-p1.md)) và ghi chú bàn giao cho **T1A**
  (0 thay đổi phá vỡ schema/tham số trên cả 19 endpoint).

Tổng hợp kết quả kiểm chứng:

| Hạng mục | Lớp bằng chứng | Kết quả | Bằng chứng |
|---|---|---|---|
| Suite P1 (8 module, **184 method**) | `service_unit_test` (JUnit gate) | **184/184 PASS** | [`evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json) |
| Chaos / rollback / retry (unit + AST guard) | `service_unit_test` + `os_process_crash` | **10/10 PASS** | [`evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml) |
| Runtime Chaos Harness (`p1chaos.localhost`) | `runtime_chaos_harness` (`exit 137` + DB postcheck) | **3/3 kịch bản PASS** | [`evidence/p1-runtime-20261102/chaos/p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json) |
| HTTP transport probe P1 (`p1http.localhost`) | `http` (5 actor thật + `Guest`, 7 nhóm API) | **52/52 PASS**, 0 rò bí mật/PII | [`evidence/p1-runtime-20261102/http/p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json) |
| Benchmark 10k import + 10k phân phối (`p1bench.localhost`) | `runtime_benchmark` (2 đợt × 10k + mixed/capacity) | **Số đo thực tế** (`20.200` dòng, `0` lỗi, `0` trùng; xem §2.8) | [`evidence/p1-runtime-20261102/benchmark/`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/) |
| Suite P0 core (inventory giữ nguyên **99**) | `service_unit_test` (JUnit gate) | **99/99 PASS** | [`evidence/p1-runtime-20261102/p0/g007-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/p0/g007-per-method-gate.json) |
| Auxiliary P0 (3 module ngoài inventory, **15 method**) | `service_unit_test` | **15/15 PASS** | [`evidence/p1-runtime-20261102/p0/auxiliary/`](../../deploy/dev/evidence/p1-runtime-20261102/p0/auxiliary/) |
| `bench migrate` × 2 (`p1fresh.localhost`) | `migration_idempotency` | exit 0 cả hai lần, không lỗi/không đổi dữ liệu | [`evidence/p1-runtime-20261102/junit-p1/migrate-1.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-1.txt), [`migrate-2.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-2.txt) |
| Race đa tiến trình N=2 / N=5 (import & distribution) | `os_process_race` | 1 bản ghi thắng, `0` trùng (unique index MariaDB) | [`evidence/p1-t1b-20261102/race/`](../../deploy/dev/evidence/p1-t1b-20261102/race/) |
| Backup → restore sang site mới (`p1restore2.localhost`) | `backup_restore` | counts **khớp 100%**, suite P1 PASS | [`evidence/p1-t1b-20261102/restore/`](../../deploy/dev/evidence/p1-t1b-20261102/restore/) |
| Lint + format (`ruff==0.14.0`) & SHA-256 manifest | `static_gate` | **PASS** (`0` cảnh báo, `0` rò bí mật/PII) | [`evidence/p1-runtime-20261102/SHA256SUMS.txt`](../../deploy/dev/evidence/p1-runtime-20261102/SHA256SUMS.txt) |

### 0.1. Chi tiết chạy tuần tự các suite JUnit trên `p1fresh.localhost`

Toàn bộ suite được chạy **tuần tự** trên `p1fresh.localhost` sau khi đồng bộ mã nguồn mới nhất
(tránh tranh chấp lock giả do chạy song song trên cùng site):

| Suite | Module | Method | Kết quả |
|---|---|---:|---|
| P1 | `crm_cskh.tests.test_p1_api_permissions` | 30 | PASS |
| P1 | `crm_cskh.tests.test_p1_chaos` | 10 | PASS |
| P1 | `crm_cskh.tests.test_p1_concurrency` | 10 | PASS |
| P1 | `crm_cskh.tests.test_p1_distribution` | 17 | PASS |
| P1 | `crm_cskh.tests.test_p1_identity` | 13 | PASS |
| P1 | `crm_cskh.tests.test_p1_imports` | 16 | PASS |
| P1 | `crm_cskh.tests.test_p1_ownership` | 17 | PASS |
| P1 | `crm_cskh.tests.test_p1_regressions` | 71 | PASS |
| **P1 tổng** | **8 module** | **184** | **184/184 PASS** |
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

## 1. Bảng đóng lỗi L-01 … L-15 và các bản sửa bổ sung

| Mã | Lỗi T1C | Nguyên nhân gốc | Bản sửa | Test hồi quy |
|---|---|---|---|---|
| **L-01** | `reconcile()` đọc `assigned_to` (cột không tồn tại) trên `CSKH Assignment History` và cộng lịch sử toàn hệ thống | Cột của lịch sử là `to_user`; truy vấn không lọc campaign | `services/distribution.py::reconcile` đọc `h.to_user`, **nối** `CSKH Work Item` theo `work_item` và lọc `wi.campaign = %s`; trả thêm `distinct_leads` | `TestL01Reconcile` (3 method) |
| **L-02** | `hashlib.sha256` nhận `str` ⇒ `TypeError` khi `File.get_content()` trả `str` (file đã flush xuống disk) | Không chuẩn hóa `str`/`bytes` | `services/import_service.py::_checksum` chuẩn hóa về `bytes` trước khi băm | `TestL02Checksum` (4 method) — gồm CSV trên disk, UTF-8 BOM, XLSX thật |
| **L-03** | Mapping trả **giá trị ô** thay vì **tên cột**; mapping nhiều field đọc nhầm cột tên | `_resolve_column_name` trả `cells[column]` | Hàm trả **tên cột**; ưu tiên field chuẩn `phone`; fallback chỉ nhận cột đã map có **tên** là header điện thoại; preview **và** worker dùng chung hàm | `TestL03ColumnResolution` (5 method) |
| **L-04** | SQL alias sai: `` `wi.assigned_to` `` bị MariaDB hiểu là tên bảng `wi.assigned_to` | Backtick bao quanh cả alias | Backtick chỉ bọc **tên cột**, alias đứng ngoài: `` wi.`assigned_to` `` | `TestL04PoolQuery` (4 method) |
| **L-05** | Dashboard rò toàn hệ thống cho Leader: `active_claims`, `active_campaigns`, `failed_batches`, `failed_runs`, `identity_count` là phép đếm global | Chỉ `assigned_work_items` được scope | **Mọi** card, `per_user`, `assignment_events`, `identity_count` và drill-down đều có scope trong SQL; `campaign_summary.assigned` đếm **Work Item** chứ không phải user distinct | `TestL05DashboardScope` (10 method) |
| **L-06** | Bộ lọc `campaign` / `date_from` / `date_to` bị **nhận rồi bỏ qua**; campaign không có run trả lịch sử toàn hệ thống | Filter chỉ áp cho events | Filter `campaign` áp cho **mọi** card + `per_user`; filter ngày áp cho **mọi** card (theo `CSKH Work Item.creation`), `per_user`, `identity_count` và events (theo `event_at`); campaign không có work item ⇒ **0** sự kiện | `TestL06CampaignFilter` (4 method) |
| **L-07** | Leader đọc Rule/Run bằng cách so **tên campaign** với **tên node** của cây ⇒ không node nào khớp | Hai namespace khác nhau | `p1_permissions.py` so `team_scope` với node của **subtree** (`lft`/`rgt`); rule còn bị ràng buộc theo **assignee** trong subtree (kể cả campaign chung) | `TestL07LeaderRead` (6 method) — chạy bằng **leader thật**, không phải `Administrator` |
| **L-08** | Manual reassign/revoke ghi `distribution_run` sai (hoặc rỗng) | Không có nguồn cho giao dịch thủ công | `services/ownership.py::_original_distribution_run` đọc event `assign` **đầu tiên**; giao dịch thủ công giữ nguồn của **lần giao đầu**, và không có run thì **không** gán run giả | `TestL08AuditTrail` (5 method) |
| **L-09** | Patch `cskh_is_placeholder_name` không idempotent; worker tạo lead nhưng **không** đánh dấu placeholder | Patch không kiểm tra trạng thái; worker không gọi mark | `patches/v1_0/p1_placeholder_name_field.py` idempotent trên site mới **và** site đã có dữ liệu; worker đánh dấu đúng lead mình tạo (tra cứu theo canonical `+84…`) | `TestL09PlaceholderField` (4 method) |
| **L-10** | `capacity_limit = 0` (không giới hạn) bị gộp sai qua nhiều campaign bằng `max()` | Rule gắn **(campaign, user)** nhưng bị gộp như rule của user | `reporting._per_user_breakdown` gom theo `(user, campaign)`; `0` ⇒ **không giới hạn** ⇒ `capacity`/`remaining_capacity` = `null`; ngược lại capacity là **tổng** các campaign | `TestL10Capacity` (6 method) |
| **L-11** | Không có `P1-Exit-Report.md`; API contract còn là bản P0 | — | File này + [`api-contract-p1.md`](./api-contract-p1.md) (19 endpoint, mã lỗi, ma trận quyền, pagination, idempotency) | — (hồ sơ) |
| **L-12** | `vi.csv` chỉ có bản dịch P0 ⇒ UI P1 hiện mixed English | — | **+219 dòng** P1 (shell, pool, campaign, rule, dashboard, import, my customers, mã lỗi) vào `apps/crm_cskh/crm_cskh/translations/vi.csv` | — (hồ sơ) |
| **L-13** | Không có sanitization formula/HTML; file lỗi tải về không chống injection; log chứa PII | — | `import_service.sanitize_cell` (trung hòa tiền tố formula, gỡ HTML tag) + `looks_like_formula`; `allow_leading_sign=True` cho phép `+84…` hợp lệ; `write_error_artifact` tạo `File` **private** đã sanitize; `batch.error` không chứa số điện thoại | `TestL13Sanitization` (6 method) |
| **L-14** | `pool_list.total` không lọc theo scope ⇒ `total ≠ len(items)` và lộ số toàn hệ thống | Bộ lọc `total` khác bộ lọc `items` | `items` và `total` dùng **cùng** mệnh đề scope (`_pool_scope_filter`) | `TestL04PoolQuery` |
| **L-15** | `frappe.get_all` bỏ qua row-level permission trong `reporting` / `eligibility` / `ownership` / `campaigns` | Dùng API database-level cho dữ liệu có row-level permission | Chuyển sang `frappe.get_list` (có áp `permission_query_conditions`); `frappe.db.get_all` còn lại **chỉ** dùng cho state nội bộ, không trả cho caller | `TestL15NoPermissionBypass` (6 method) |

### Bản sửa bổ sung phát hiện trong lúc rà soát, chaos test và benchmark 10k

| Mã | Vấn đề phát hiện | Bản sửa | Kiểm chứng |
|---|---|---|---|
| **L-16** | `P1Error` chỉ khai báo `http_status`; Frappe đọc `getattr(e, "http_status_code", 500)` ⇒ mọi lỗi P1 trả HTTP 500 | `services/errors.py` khai báo cả `http_status` và `http_status_code` trên `P1Error` và 11 lớp con (400/403/404/409/413) | `ci_p1_http_probe.py` (52/52 HTTP) |
| **L-17** | Bộ lọc ngày của dashboard chỉ lọc events ⇒ dashboard hiển thị hai khoảng thời gian khác nhau cùng lúc | Bộ lọc ngày áp cho mọi card, `per_user` và `identity_count` | `TestL06CampaignFilter` |
| **L-18** | `pool_list`/`dashboard` dùng `frappe.db.count` với bộ lọc tuple/dict lẫn lộn | Chuẩn hóa hàm `scoped_count` với alias + clause độc lập | `TestL04PoolQuery`, `TestL05DashboardScope` |
| **L-19** | `import_worker.py` lặp trên `preview.rows` (đã bị cắt 200 dòng ở `create_preview`) ⇒ import > 200 dòng bị bỏ sót toàn bộ dòng từ 201 trở đi | Worker đọc trực tiếp `classification["rows"]` đầy đủ từ file gốc; `preview` 200 dòng chỉ dành cho UI | Benchmark 10k (`created_rows = 10.000`) |
| **L-20** | Con trỏ `progress` trong `import_worker.py` bị chunk sau không lỗi đẩy vượt qua dòng lỗi của chunk trước ⇒ khi retry bỏ sót dòng lỗi ở chunk đầu | Neo `safe_progress = max(resume_from, blocked_at - 1)` để không bao giờ tiến qua dòng lỗi đầu tiên chưa xử lý xong | `TestImportWorkerCrashAndRecovery.test_worker_crash_across_multiple_chunks_preserves_earliest_blocked_row` |
| **L-21** | `import_worker.py` cộng `classification["error_rows"]` với danh sách `error_rows` vốn đã chép sẵn các dòng `invalid` của preview ⇒ nhân đôi `batch.error_rows` (`15 → 30`) | Tách tập `preview_invalid_rows`, chỉ cộng thêm số lỗi mới phát sinh trong lúc chạy worker | `test_mixed_batch_does_not_double_count_preview_invalid_rows` + `p1-benchmark-mixed-capacity.json` (`error_rows = 15`) |
| **L-22** | `distribution._resume_run` ghi đè `assigned` chỉ bằng lượt resume, không khóa `FOR UPDATE` trên run, không kiểm tra `rule_version` và xóa mất lý do `no_eligible_assignee` | Khóa `SELECT status FROM \`tabCSKH Distribution Run\` WHERE name = %s FOR UPDATE`, kiểm tra `rule_version`, cộng dồn `assigned` (`previous + resumed`), bảo toàn các dòng `no_eligible_assignee` và không chuyển `running` nếu hết lead chờ | `TestDistributionCrashAndRollback.test_partial_distribution_run_resumes_only_remaining_leads_and_sums_counts` |

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
python3 deploy/dev/seed/p1_test_inventory.py --check

# 2. Suite P1 (8 module, 184 method) + JUnit gate riêng
bench --site p1fresh.localhost --verbose run-tests --module crm_cskh.tests.test_p1_<module> \
  --junit-xml-output /tmp/p1-evidence-final/<slug>.junit.xml
python3 deploy/dev/seed/ci_junit_gate.py \
  --inventory deploy/dev/seed/p1_test_inventory.json \
  --xml-dir /tmp/p1-evidence-final --out /tmp/p1-evidence-final/p1-per-method-gate.json

# 3. Suite P0 core (5 module, giữ nguyên inventory 99 method) + Auxiliary (15 method)
python3 deploy/dev/seed/ci_junit_gate.py \
  --inventory deploy/dev/seed/g007_test_inventory.json \
  --xml-dir /tmp/p0-evidence-final --out /tmp/p0-evidence-final/g007-per-method-gate.json

# 4. Runtime chaos harness (bao gồm tiến trình con os._exit(137))
bench --site p1chaos.localhost execute crm_cskh.tools.p1_chaos_harness.run \
  --kwargs "{'out_path': '/tmp/p1-chaos-report.json'}"

# 5. HTTP P1 transport probe (52 case, 5 actor thật + Guest)
P1_HTTP_SITE=p1http.localhost P1_HTTP_BASE_URL=http://127.0.0.1:18089 \
  FRAPPE_STREAM_LOGGING=1 python3 deploy/dev/seed/ci_p1_http_probe.py

# 6. Benchmark 10.000 dòng + supplementary mixed/capacity check
bench --site p1bench.localhost execute crm_cskh.tools.p1_benchmark.run \
  --kwargs "{'row_count': 10000, 'out_path': '/tmp/p1-benchmark-10k.json'}"
bench --site p1bench.localhost execute crm_cskh.tools.p1_benchmark.run_supplementary_checks \
  --kwargs "{'out_path': '/tmp/p1-benchmark-mixed-capacity.json'}"
```

### 2.2. Kết quả per-method JUnit gate

```text
g007-per-method-gate: PASS     # P1 (deploy/dev/seed/p1_test_inventory.json)
  expected_total : 184
  observed_total : 184
  passing_total  : 184
  PASS           : 184

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

* `bench --site p1fresh.localhost migrate` chạy 2 lần liên tiếp cả ở baseline
  ([`evidence/p1-t1b-20261102/migrate/`](../../deploy/dev/evidence/p1-t1b-20261102/migrate/)) lẫn
  revision hiện tại ([`migrate-1.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-1.txt),
  [`migrate-2.txt`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/migrate-2.txt)): đều `exit 0`,
  không làm rơi/đổi dữ liệu.
* Backup `p1fresh.localhost` → restore sang site mới `p1restore2.localhost`
  ([`evidence/p1-t1b-20261102/restore/`](../../deploy/dev/evidence/p1-t1b-20261102/restore/)): số dòng
  khớp 100% (`work_items=2718`, `history=1394`, `identity=2534`, `claims=1024`).

### 2.5. Xóa bỏ hoàn toàn đường tiêm lỗi trong production

Theo yêu cầu kiến trúc P1, mọi cơ chế tiêm lỗi chỉ được tồn tại ở tầng test/harness:

* Đã **xóa hẳn** file `apps/crm_cskh/crm_cskh/services/chaos.py` và gỡ 100% lệnh gọi tiêm lỗi khỏi
  `services/ownership.py`, `services/notifications.py`, `services/distribution.py`, `jobs/import_worker.py`.
* Test `TestZeroProductionFaultInjection.test_no_chaos_module_or_fault_hooks_in_production_packages`
  quét AST toàn bộ `api/`, `services/`, `jobs/`, `crm_cskh/doctype/` để chặn vĩnh viễn mọi import/gọi
  `chaos`, `maybe_fail`, `fault_inject`, `os._exit` trong code production.

### 2.6. Chứng minh Crash / Rollback / Retry bằng runtime (`P1 §12.4`)

Được chứng minh đồng thời qua **10 unit/process test** trong
[`test_p1_chaos.py`](../../apps/crm_cskh/crm_cskh/tests/test_p1_chaos.py)
([`test_p1_chaos.junit.xml`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/test_p1_chaos.junit.xml))
và **3 kịch bản end-to-end** của [`p1_chaos_harness.py`](../../apps/crm_cskh/crm_cskh/tools/p1_chaos_harness.py)
trên `p1chaos.localhost`
([`p1-chaos-report.json`](../../deploy/dev/evidence/p1-runtime-20261102/chaos/p1-chaos-report.json)):

| Kịch bản (`P1 §12.4`) | Cách tiêm lỗi (chỉ trong test/harness) | Kết quả hậu kiểm trực tiếp trên MariaDB |
|---|---|---|
| **1. Lỗi sau khi tạo `CSKH Work Item` + `CSKH Active Claim`, trước khi hoàn tất ownership** (`S1` + `TestDistributionCrashAndRollback`) | `mock.patch` ném lỗi tại `ownership.assign_work_item` (sau khi `Work Item` và `Active Claim` đã `insert` trong savepoint) và giữa `ownership._ensure_share` (sau khi `CSKH Assignment History` + `ToDo` đã `insert`) | Savepoint rollback trọn vẹn: `rolled_back_work_items_surviving_in_db = 0`, `orphan_work_items = 0`, `orphan_active_claims = 0`, `owner_mismatches = 0`, `todo_mismatches = 0`, `docshare_mismatches = 0`, `partial_history_rows = 0`. Khi resume run trên pool 10 lead (tỷ lệ `50/30/20`), run chuyển `completed_with_errors (7/10)` → `completed (10/10)` với phân bổ đúng `5/3/2`. |
| **2. Lỗi giữa giao dịch `reassign`** (`test_crash_mid_reassign_rolls_back_previous_history_close_and_keeps_original_owner`) | `mock.patch` ném lỗi ở `_ensure_share` sau khi dòng `CSKH Assignment History` cũ đã bị đóng `ownership_end` và `ToDo` cũ đã bị cancel | Toàn bộ giao dịch `reassign` rollback: `assigned_to` và `CRM Lead.lead_owner` giữ nguyên owner cũ, `ownership_end` của history cũ vẫn `NULL`, `ToDo` cũ vẫn `Open`, không sinh `DocShare` cho người nhận mới. |
| **3. Lỗi sau DB commit nhưng trước thông báo; retry không tạo trùng** (`S2` + `TestPostCommitNotificationRetry`) | `mock.patch` làm `CRM Notification.insert` ném lỗi sau khi `frappe.db.commit()` đã lưu ownership | `committed_work_items = 3`, `run_status = "completed"`, `crm_notifications_in_db = 0` (assignment không bị rollback vì lỗi thông báo). Gọi retry liên tiếp **2 lần**: `crm_notifications_in_db = 3`, `open_todos_in_db = 3`, `active_work_items = 3`, `active_claims = 3`, `assignment_events_total = 3` (**0 bản ghi trùng**). |
| **4. Worker bị dừng cứng giữa batch rồi phục hồi/retry** (`S3` + `TestImportWorkerCrashAndRecovery`) | Spawn **tiến trình con OS thật** chạy `import_worker.run_batch` với `chunk_size = 3` trên lô 9 dòng; sau khi commit xong chunk 1 (3 dòng), tiến trình con gọi `os._exit(137)` (tương đương `SIGKILL`, không chạy `finally` hay Python rollback) | Hậu kiểm DB ngay sau khi tiến trình con thoát mã `137`: `status = "RUNNING"`, `attempts = 1`, `progress = 4`, `committed_identities_before_crash = 3`. Chạy lại `run_batch` để phục hồi: `status = "COMPLETED"`, `attempts = 2`, `created_rows = 9`, `identity_index_count = 9`, `crm_lead_count = 9`, `duplicate_identities = 0`, `duplicate_leads = 0`. |

### 2.7. Bằng chứng HTTP P1 với actor thật & phân biệt lớp bằng chứng

Báo cáo này tách bạch hai lớp bằng chứng khác bản chất:

| Lớp bằng chứng (`evidence_class`) | Công cụ & cơ chế gọi | Những gì chứng minh được | Những gì KHÔNG chứng minh |
|---|---|---|---|
| **`http`** ([`p1-http-transport.json`](../../deploy/dev/evidence/p1-runtime-20261102/http/p1-http-transport.json), **52/52 PASS**) | `ci_p1_http_probe.py` mở kết nối TCP/HTTP thật tới `bench serve` trên `p1http.localhost`, đăng nhập qua `POST /api/method/login`, giữ cookie phiên `sid` riêng cho từng actor và gửi `X-Frappe-CSRF-Token` | Routing `@frappe.whitelist`, kiểm tra HTTP method (`GET`/`POST`), xác thực phiên (`Guest` bị từ chối), kiểm tra CSRF thật (`400 CSRFTokenError`), mã HTTP lỗi thật (`400`/`403`/`404`/`409`) từ `normalize_error_response`, phân quyền & lọc scope thật của từng actor | Cấu hình Nginx / TLS termination / CORS ở tầng reverse proxy phía trước `bench serve` |
| **`service_unit_test`** ([`p1-per-method-gate.json`](../../deploy/dev/evidence/p1-runtime-20261102/junit-p1/p1-per-method-gate.json), **184/184 PASS**) | `bench run-tests` gọi hàm Python trực tiếp trong cùng tiến trình với `frappe.set_user(...)` | Logic nghiệp vụ sâu, biên dữ liệu, savepoint rollback, fairness tích lũy, sanitization CSV/XLSX, idempotency chi tiết trên DB | Không đi qua HTTP parser, cookie jar, CSRF middleware hay serialization HTTP của Werkzeug |

Phạm vi 52 case HTTP thật trên `p1http.localhost`:

| Actor | Role nghiệp vụ | Các kiểm chứng HTTP đã thực thi (positive dữ liệu thật + negative bị chặn) |
|---|---|---|
| `Guest` (chưa đăng nhập) | Không có | Bị từ chối (`401`/`403`) khi gọi `campaigns.list` |
| `crm.admin@dev.local` | `CSKH CRM Admin` | Đăng nhập + lấy CSRF; bị chặn HTTP `400` khi gửi CSRF giả; thực thi thành công luồng thật có dữ liệu không rỗng qua `health.ping`, `campaigns.save`, `campaigns.save_rule`, `distribution.preview` → `execute` → `get_run` → `reconcile`, `ownership.reassign` → `history` → `revoke` → `assign`, `dashboard.distribution`, `imports.create_preview` → `confirm` → `get_status` |
| `bod.viewer@dev.local` | `CSKH BOD Viewer` | Đọc thành công `campaigns.list`, `campaigns.rules`, `customers.pool_list` (sau khi revoke về kho), `dashboard.distribution` (`scope = "all"`), `dashboard.campaigns` (`len(campaigns) >= 1`); **bị chặn HTTP `403`** trên mọi mutation (`campaigns.save`, `distribution.execute`, `ownership.reassign`, `imports.create_preview`, `imports.confirm`, `imports.get_status`) |
| `team1.leader@dev.local` | `Sales Manager` | Đọc thành công `campaigns.list` và `dashboard.distribution` (`scope = "team"`); **bị chặn HTTP `403`** khi cố ghi `campaigns.save` |
| `staff.a@dev.local` | `Sales User` | Đọc `customers.my_list` (`scope = "self"`, thấy đúng work item được giao cho mình); **bị chặn HTTP `403`** trên `campaigns.list`, `campaigns.save`, `distribution.execute`, `ownership.reassign`, `ownership.revoke`, `customers.pool_list`, `dashboard.distribution`, `imports.create_preview` |
| `staff.b@dev.local` | `Sales User` | Trước khi `reassign`: `customers.my_list` không thấy khách của `staff.a`; sau khi `crm.admin` gọi `ownership.reassign` sang `staff.b` qua HTTP: `customers.my_list` của `staff.b` thấy ngay work item vừa chuyển giao |

Toàn bộ mật khẩu (`pwd=`), cookie (`sid=`, `Set-Cookie`), `X-Frappe-CSRF-Token`, đường dẫn file private và
số điện thoại trong `p1-http-transport.json` đều đã được redact tự động (quét kiểm chứng `0` rò rỉ).

### 2.8. Benchmark 10.000 dòng theo `P1 §12.4`

Thực hiện trên site cô lập `p1bench.localhost` (container `crm-cskh-p1-dev-bench-1` chia sẻ tài nguyên
máy DEV Windows Docker Desktop WSL2, MariaDB 11.8). Không tự đặt SLA và **không coi việc chạy hoàn tất là
đạt mọi tiêu chí hiệu năng**.

#### Bảng số đo 2 đợt × 10.000 dòng (`50/30/20`) và lô bổ sung (`mixed` + `capacity`)

| Chỉ số đo | Đợt 1 (`0 → 10.000` lead mới) | Đợt 2 (`10.000 → 20.000` lead trên nền 10k sẵn có) | Lô bổ sung (100 dòng hỗn hợp + phân phối giới hạn capacity) |
|---|---|---|---|
| **Artifact** | [`p1-benchmark-10k-fresh.json`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/p1-benchmark-10k-fresh.json) | [`p1-benchmark-10k-on-20k.json`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/p1-benchmark-10k-on-20k.json) | [`p1-benchmark-mixed-capacity.json`](../../deploy/dev/evidence/p1-runtime-20261102/benchmark/p1-benchmark-mixed-capacity.json) |
| **Kích thước file CSV** | `110.021 B` (10.000 dòng hợp lệ) | `110.021 B` (10.000 dòng hợp lệ mới) | `1.151 B` (50 mới, 20 trùng CRM, 15 trùng trong file, 15 SĐT lỗi) |
| **Thời gian `create_preview`** | `5,731 s` | `5,825 s` | `0,234 s` |
| **Thời gian `import_worker`** | `241,462 s` (tổng import `247,193 s`, **`41,4 dòng/s`**) | `353,653 s` (tổng import `359,479 s`, **`28,3 dòng/s`**) | `2,646 s` (`COMPLETED_WITH_ERRORS`) |
| **Kết quả import (`created / reused / skipped / error`)** | `10.000 / 0 / 0 / 0` (`COMPLETED`) | `10.000 / 0 / 0 / 0` (`COMPLETED`) | `50 / 20 / 15 / 15` (`has_error_artifact = true`) |
| **Pool & cấu hình phân phối** | Pool `10.000`, trọng số `50 / 30 / 20`, không trần capacity | Pool `20.000` (10k đã claim + 10k đủ điều kiện), trọng số `50 / 30 / 20` | Pool `100` mới, trọng số `50 / 30 / 20`, trần capacity `staff-0 = 30`, `staff-1 = 0`, `staff-2 = 0` |
| **Thời gian `distribution.execute`** | **`4.802,380 s`** (`~80,0 phút`, **`2,1 items/s`**) | **`4.879,758 s`** (`~81,3 phút`, **`2,0 items/s`**) | `9,786 s` (`100` items, `~10,2 items/s`) |
| **Kết quả phân phối & Fairness** | `5.000 / 3.000 / 2.000` (`0,50 / 0,30 / 0,20` tuyệt đối), `errors = 0` | `5.000 / 3.000 / 2.000` (`0,50 / 0,30 / 0,20` tuyệt đối), `errors = 0` | **`30 / 42 / 28`** (`staff-0` chạm trần `30`, 70 item còn lại chia đúng tỷ lệ `30:20` = `42:28`), `errors = 0` |
| **Đối soát `reconcile()`** | `distinct_leads = 10.000`, `active_work_items = 10.000`, `active_claims = 10.000`, `owner_total = 10.000`, `event_total = 10.000` | `distinct_leads = 10.000`, `active_work_items = 10.000`, `active_claims = 10.000`, `owner_total = 10.000`, `event_total = 10.000` | `distinct_leads = 100`, `active_work_items = 100`, `active_claims = 100`, `owners = events = {30, 42, 28}` |
| **Peak RAM (RSS tiến trình Python)** | `88,4 MiB → 281,9 MiB` | `67,4 MiB → 277,8 MiB` | `67,3 MiB → 131,5 MiB` |
| **DB Queries (`Queries` delta)** | `805.796` (`~80,6 queries/dòng`) | `1.103.865` (`~110,4 queries/dòng`) | `8.449` |
| **InnoDB Row Lock Waits / Time** | `0 waits` / `0 ms` | `51 waits` / `1.383 ms` | `0 waits` / `0 ms` |

#### Đối soát hậu kiểm toàn cục trên `p1bench.localhost` sau cả 3 đợt

* `total_crm_leads = 20.200`, `total_identities = 20.200`, `total_work_items = 20.200`,
  `total_active_claims = 20.200`, `total_assignment_history = 20.200`.
* `duplicate_identities = 0`, `duplicate_active_claims = 0`, `duplicate_campaign_work_items = 0`.

#### Kiểm kê Index thực tế (`SHOW INDEX`) và phân tích nút cổ chai hiệu năng

1. **Các bảng P1 (`tabCSKH *`) đã có đầy đủ unique & lookup index theo thiết kế:**
   * `tabCSKH Work Item`: `PRIMARY(name)`, unique `cskh_domain_unique(campaign, lead)`,
     `cskh_lookup_0(assigned_to, status, assigned_at)`, `cskh_lookup_1(campaign, status)`,
     `cskh_lookup_2(lead, status)`.
   * `tabCSKH Active Claim`: `PRIMARY(name)`, unique `lead`, unique `work_item`.
   * `tabCSKH Assignment History`: `PRIMARY(name)`, unique `event_id`,
     `cskh_lookup_0(work_item, event_at)`, `cskh_lookup_1(to_user, ownership_start, ownership_end)`.
   * `tabCSKH Identity Index`: `PRIMARY(name)`, unique `normalized_phone`, unique `canonical_lead`.
   * `tabCSKH Import Batch` & `tabCSKH Distribution Run`: unique `idempotency_key`, lookup `(campaign, status)`.
2. **Nút cổ chai #1 — `distribution.execute` chạy đồng bộ từng dòng qua Frappe ORM (`~2,0–2,1 items/s`):**
   * Mỗi Work Item đi qua đầy đủ vòng đời Frappe Document (`Work Item.insert`, `Active Claim.insert`,
     `CRM Lead` load + save `lead_owner`, `DocShare`, `ToDo.insert`, `Assignment History.insert`,
     savepoint + post-commit `CRM Notification.insert`), phát sinh trung bình **80–110 câu SQL/item**
     (`805k–1,10M` queries cho 10.000 item).
   * Bộ nhớ giữ ổn định (`peak RSS ~278–282 MiB`, không rò bộ nhớ) và tính toàn vẹn dữ liệu đạt 100%,
     **nhưng thời gian tường `~80–81 phút` cho 10.000 item chắc chắn vượt ngưỡng timeout HTTP thông thường
     (`60–120s`) nếu gọi đồng bộ một lần trên web**. Ở P2 hoặc bản tối ưu tiếp theo cần chuyển
     `distribution.execute` cỡ lớn sang RQ background worker theo chunk tương tự `import_worker`.
3. **Nút cổ chai #2 — `tabCRM Lead` chưa có DB index trên cột `mobile_no`:**
   * Theo `SHOW INDEX FROM \`tabCRM Lead\`` (trong `p1-benchmark-mixed-capacity.json`), bảng gốc của
     Frappe CRM chỉ có index trên `PRIMARY`, `facebook_lead_id`, `lead_name`, `email`, `status`,
     `modified` — **không có index trên `mobile_no`** (do giữ nguyên tắc P0 ADR-001 không sửa trực tiếp
     schema upstream nếu chưa có quyết định kiến trúc).
   * Hệ quả đo được: khi bảng `tabCRM Lead` tăng từ `0 → 10.000` lên `10.000 → 20.000`, nhánh fallback
     dò lead theo SĐT làm thông lượng `import_worker` giảm từ **`41,4 dòng/s` (`241,5s`) xuống `28,3 dòng/s`
     (`353,7s`)**.

### 2.9. Báo cáo thay đổi API contract cho T1A (UI)

* **0 thay đổi phá vỡ contract:** Tất cả **19 endpoint**, HTTP method, tên tham số, mã lỗi và cấu trúc
  JSON trả về trong [`api-contract-p1.md`](./api-contract-p1.md) **giữ nguyên 100%** so với bản T1A đang
  tích hợp.
* **Các làm rõ hành vi phía server (đã ghi tại `api-contract-p1.md` §3.9, §6, §6.1):**
  1. `distribution.execute` hỗ trợ gọi lại với cùng `preview_id` khi run ở trạng thái
     `completed_with_errors` để chạy tiếp phần chưa giao và trả `assigned` cộng dồn đầy đủ (trả
     `409 RULE_CHANGED` nếu rule đã đổi).
  2. `imports.create_preview` vẫn chỉ trả tối đa 200 dòng đầu trong mảng `preview` (`preview_truncated = true`),
     còn worker xử lý toàn bộ số dòng của file gốc và giữ đúng `totals.error_rows` không bị nhân đôi.
  3. Lỗi thông báo sau commit không làm fail hay rollback giao dịch ownership; gọi lại sẽ tự bù thông báo
     còn thiếu một cách idempotent.

---

## 3. Ma trận quyền và phạm vi đã kiểm chứng

| Actor | Đọc được | Bị từ chối | Bằng chứng (`service_unit_test` + `http`) |
|---|---|---|---|
| `CSKH BOD Viewer` | Dashboard aggregate toàn hệ thống; `pool_list`; **không** đọc role-wide PII `CRM Lead` | Mọi thao tác ghi import / campaign / rule / distribution / ownership | `TestL05DashboardScope`, `TestApiContract`, `ci_p1_http_probe.py` (`bod.viewer@dev.local`) |
| `Sales Manager` (leader) | Dashboard **chỉ** nhánh cây con (`scope = "team"`); rule/run của campaign trong subtree | Ghi campaign/rule; đọc rule/run của nhánh khác | `TestL05DashboardScope`, `TestL07LeaderRead`, `ci_p1_http_probe.py` (`team1.leader@dev.local`) |
| `Sales User` (staff) | `my_list` của chính mình (`scope = "self"`), cập nhật tức thì sau `reassign` | Work item của người khác, `pool_list`, `dashboard`, `campaigns`, `distribution`, `imports` | `TestL15NoPermissionBypass`, `TestSecurityNegatives`, `ci_p1_http_probe.py` (`staff.a`, `staff.b`) |
| `CSKH CRM Admin` | Toàn bộ 19 endpoint nghiệp vụ P1 | Request thiếu/sai `X-Frappe-CSRF-Token` (`400`) | `TestApiContract`, `ci_p1_http_probe.py` (`crm.admin@dev.local`) |
| `Guest` | Không có | Toàn bộ endpoint P1 (`401`/`403`) | `ci_p1_http_probe.py` (`p1-http-guest-campaigns-denied`) |
| `Administrator` / `System Manager` | Đường quản trị hệ thống (không dùng làm bằng chứng cho actor nghiệp vụ) | — | `TestApiContract.test_the_business_roles_are_not_system_roles` |

---

## 4. CI & Inventory kiểm thử

Workflow [`.github/workflows/p1.yml`](../../.github/workflows/p1.yml) thực thi các cổng độc lập:

1. `bootstrap` — dựng stack ghim phiên bản trên site sạch từ volume rỗng;
2. `lint` — `ruff==0.14.0 check` + `format --check` trên `apps/crm_cskh`, `p1_test_inventory.py` và
   `ci_p1_http_probe.py`;
3. `inventory` — `p1_test_inventory.py --check` đối chiếu AST với
   [`deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json) (**8 module / 184 method**, fail-closed nếu thiếu/đổi tên method);
4. `seed` + `migrate` × 2 — kiểm chứng idempotency của migration trên site đã có dữ liệu seed;
5. `per_method` + `per_method_gate` — xuất 8 file `.junit.xml` riêng cho P1 và đối soát `184/184` qua
   `ci_junit_gate.py`;
6. `p1_http_transport` — khởi chạy `bench serve` và chạy `ci_p1_http_probe.py` (**52 case HTTP thật**);
7. `schema` & `sanitize` — xuất `SHOW INDEX` của 8 bảng P1 và redact toàn bộ mật khẩu (`pwd=`/`password=`),
   cookie (`sid=`), CSRF token, URL file private và số điện thoại trước khi tải lên artifact CI.

> **Bảo toàn ranh giới P0 / P1:** `deploy/dev/seed/g007_test_inventory.json` được giữ nguyên tuyệt đối ở
> **99 method** cho workflow `.github/workflows/p0.yml`; P1 dùng inventory riêng `p1_test_inventory.json`
> (**184 method**).

---

## 5. Phần còn lại / giới hạn thực tế đã ghi nhận

1. **Hiệu năng phân phối đồng bộ tập lớn (`distribution.execute`):** Đã đo thực tế 10.000 Work Item mất
   `~4.802–4.880s` (`~2,0–2,1 items/s`, `~80–110` SQL queries/item). Toàn vẹn dữ liệu, fairness `50/30/20`
   và RAM (`~282 MiB`) hoàn toàn ổn định, nhưng gọi đồng bộ 10.000 dòng trong một HTTP request sẽ vượt
   timeout của reverse proxy / Gunicorn; cần chuyển sang RQ worker theo chunk nếu vận hành lô lớn một lần.
2. **Chưa đánh index `mobile_no` trên `tabCRM Lead`:** Giữ nguyên ràng buộc P0 ADR-001 không sửa trực tiếp
   schema bảng gốc Frappe CRM; thông lượng import giảm từ `41,4 dòng/s` (`0 → 10k`) xuống `28,3 dòng/s`
   (`10k → 20k`). Có thể bổ sung Property Setter / custom index có kiểm soát nếu T0 phê duyệt mở ADR-001.
3. **Phạm vi giao vận HTTP probe:** `ci_p1_http_probe.py` kiểm chứng HTTP thật tới `bench serve`, chưa phủ
   lớp reverse proxy (Nginx/TLS/timeout) và CORS giữa nhiều domain.
4. **UI P1 (phạm vi của T1A):** Bản dịch backend/UI trong `translations/vi.csv` đã có, nhưng kiểm chứng
   hiển thị và screenshot từng màn hình UI P1 (ô `T1-20`) thuộc phạm vi bàn giao của T1A và kiểm định của T1C.
5. **Tải file lỗi & phân trang sâu:** `error_artifact` trả tên `File` private để tải qua route chuẩn của
   Frappe (chưa có wrapper endpoint riêng); danh sách dùng `page`/`page_size` theo offset (chưa dùng cursor);
   bộ lọc ngày dashboard chủ đích tách `CSKH Work Item.creation` và `CSKH Assignment History.event_at`
   (đã ghi rõ trong `api-contract-p1.md` §6).
6. **Quản trị nhánh & cổng giai đoạn:** PR #4 giữ trạng thái **OPEN** (không tự merge), không tự ký
   **GO P2** — chờ T1C kiểm định độc lập và T0 quyết định.

---

## 6. Tài liệu & Artifact liên quan

* [`api-contract-p1.md`](./api-contract-p1.md) — API contract P1 (19 endpoint, mã lỗi, ma trận quyền, ghi chú T1A)
* [`schema-p1.md`](./schema-p1.md) — 8 DocType P1, unique constraint & lookup index
* ADR: `adr-p1-01` … `adr-p1-08` (identity, claim, import, weighted-fair, ownership, UI, RBAC, import-limits)
* [`../../deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json) — inventory P1 riêng (**184 method** trên 8 module)
* [`../../deploy/dev/seed/g007_test_inventory.json`](../../deploy/dev/seed/g007_test_inventory.json) — inventory P0 giữ nguyên (**99 method** trên 5 module)
* Bộ bằng chứng runtime đầy đủ của revision này:
  [`../../deploy/dev/evidence/p1-runtime-20261102/`](../../deploy/dev/evidence/p1-runtime-20261102/)
  (kèm [`SHA256SUMS.txt`](../../deploy/dev/evidence/p1-runtime-20261102/SHA256SUMS.txt))
* Bộ bằng chứng baseline (race đa tiến trình & backup/restore):
  [`../../deploy/dev/evidence/p1-t1b-20261102/`](../../deploy/dev/evidence/p1-t1b-20261102/)
