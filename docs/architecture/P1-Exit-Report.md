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

<!-- T1A-UI-CHAPTER -->

---

## 7. Chương T1A — Giao diện P1 hoàn chỉnh (5 màn hình P1 §9, Việt hóa & E2E thực tế)

**Ngày cập nhật:** 2026-11-03
**Vai trò:** T1A — CSKH P1 UI (cùng Frappe site/session với CRM theo ADR-UI-001 & ADR-P1-06)
**Nhánh:** `codex/p1-ui` — worktree `D:/HRP-app/p1-ui` ([PR #5](https://github.com/nobita6986/new-crm/pull/5))
**Backend baseline tích hợp:** `3025495f58bc0391c3f8d092af500b0566a5f038` (baseline ban đầu 174/174 P1, 99/99 P0) và đã hợp nhất có kiểm soát commit mới nhất qua CI của PR #4: `64b7486f29ad712106d167dfaea50e3b060a6845` (`codex/p1-foundation` — 184/184 P1 PASS, 52/52 HTTP probe PASS, 99/99 P0 PASS)
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md) §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu)
**Hợp đồng API:** [`docs/architecture/api-contract-p1.md`](./api-contract-p1.md)
**Báo cáo backend:** chương 0–6 ở trên (do T1B biên soạn — giữ nguyên 100%, không chỉnh sửa chồng chéo)

### 7.0. Nguyên văn đề bài T1A (Verbatim Prompt)

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

### 7.1. Đồng bộ backend có kiểm soát & cách dựng lại cho T1C

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

### 7.3. Bằng chứng HTTP E2E thực tế trên `p1.localhost`

Script tự động tái lập: [`deploy/p1/verify_p1_http_e2e.py`](../../deploy/p1/verify_p1_http_e2e.py) → báo cáo đã che PII/bí mật tại [`deploy/p1/evidence/http-e2e-report.json`](../../deploy/p1/evidence/http-e2e-report.json).

| Mã kiểm định | Kịch bản E2E thực tế qua HTTP + CSRF | Kết quả đo |
|---|---|---|
| `E2E-01-page-role-matrix` | Ma trận HTTP 6 route (`/cskh` + 5 màn) trên 8 phiên thật (`admin`, `bod`, `leader_a`, `leader_b`, `staff_a`, `staff_b`, `outside`, `guest`) + kiểm tra `lang="vi"` | **PASS** — Admin `200×6`; BOD/Leader `403` trên `/cskh/import`, `200` các trang có quyền; Staff `200` trên `/cskh` & `/cskh/my-customers`, `403` các trang quản trị/tổng hợp; Guest `403×6` |
| `E2E-02-csv-import-and-error-download-policy` | Upload CSV (chứa XSS `<script>`, công thức `=SUM(1,2)`, ký hiệu khoa học `9.1888E+08`, dòng trùng) → preview → confirm → `COMPLETED_WITH_ERRORS` + kiểm tra tải tệp lỗi | **PASS** — `total=10, valid=6, duplicate=1, error=3, created=6, skipped=3`; Admin tải tệp lỗi `200` (đã sanitize công thức), 7 actor còn lại bị chặn `403` cả ở API lẫn `/private/files/...` |
| `E2E-03-xlsx-import-and-failed-batch-retry` | Upload tệp `.xlsx` thật → preview → confirm → `COMPLETED` + chạy lại batch `FAILED` | **PASS** — `total=5, valid=4, duplicate=1, error=0`; batch `FAILED` chạy lại lên `COMPLETED` |
| `E2E-04-campaign-rules-stale-preview-and-execute` | Tạo/sửa/đóng chiến dịch, cấu hình rule `60/40` (`capacity=0` Không giới hạn & `capacity=10`), kiểm tra stale preview (`RULE_CHANGED`, `PREVIEW_EXPIRED`), chống double-submit và giao về đúng `my_list` | **PASS** — `RULE_CHANGED` → `409`, `PREVIEW_EXPIRED` → `409`; thực thi giao đúng `7` khách cho `staff_a`, `4` khách cho `staff_b`, không rò chéo |
| `E2E-05-reassign-revoke-conflict-and-reconciliation` | Chuyển giao `staff_a → staff_b` (kèm retry idempotent), thử ghi đè bằng `expected_owner` cũ, thu hồi `revoke`, đối soát quyền đọc `CRM Lead` và số liệu dashboard | **PASS** — `staff_a` chuyển từ `200 → 403`, `staff_b` từ `403 → 200`; `expected_owner` cũ bị chặn `409 CONFLICT`; sau chuyển giao `assigned_work_items` giữ nguyên `11` còn `assignment_events` tăng lên `12`; sau `revoke` người cũ nhận `403` |
| `E2E-06-team-isolation-bod-pii-csrf-and-404` | Cô lập Đội A (`leader_a`) vs Đội B (`leader_b`), BOD xem tổng hợp không lộ PII, từ chối mọi POST trái quyền / sai CSRF, và trả `404 NOT_FOUND` | **PASS** — `leader_a` chỉ thấy `{staff_a: 6, staff_b: 4}`, `leader_b` chỉ thấy `{staff_c: 2}`; BOD thấy tổng `12`, `phone=None`, `display_name=""`, `search` bị chặn `403`; sai CSRF → `400 CSRFTokenError`; sai mã run/batch → `404` |

### 7.4. Kiểm tra trình duyệt Chromium thật, an toàn XSS/PII & danh mục 11 Screenshot

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

### 7.5. Việt hóa 100% (P1 §9.6, T1-20)

* Tệp [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv) có **577 dòng** hợp lệ (`check_translations.py`: `malformed = 0`), bao phủ toàn bộ **372 chuỗi khai báo P1** (shell, tiêu đề, 5 màn hình, trạng thái chiến dịch/lô/đợt chạy, mã lỗi ổn định P1 §10.1, lý do loại trừ eligibility và toàn bộ thông báo ngoại lệ từ backend/API).
* Kết quả [`deploy/p1/check_translations_audit.py`](../../deploy/p1/check_translations_audit.py):
  * `used strings = 204`, `declared strings = 372`, `missing declaration = 0`, `missing translation = 0`, `untranslated (EN) = 0`, `orphan declaration = 0`.
* Shell CSKH (`cskh_context.py`) ghim `frappe.local.lang = "vi"` và `context.boot["lang"] = "vi"` trước cả bước kiểm tra quyền truy cập, bảo đảm cả `<html lang="vi">` lẫn trang từ chối quyền `403` đều hiển thị tiếng Việt đồng nhất.

### 7.6. Kết quả bộ test đơn vị & tích hợp trên nhánh `codex/p1-ui`

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

### 7.7. Lưu ý kỹ thuật & giới hạn còn mở ngoài phạm vi UI hiện tại

1. **Điều chỉnh có chủ đích trên dịch vụ dùng chung (0 nới quyền, giữ xanh 184/184 P1 & 99/99 P0):**
   * `services/reporting.py`: bổ sung kiểm tra `allow_pii = scope.get("pii", True)` trong `reporting.pool_list()` để vai trò `CSKH BOD Viewer` không nhận `lead_name` và `mobile_no` (tuân thủ P1 §8.1), đồng thời giữ `_scope` toàn hệ thống cho `CSKH CRM Admin`.
   * `services/import_service.py`: trong `confirm()`, bước chuyển `DRY_RUN` đi tuần tự `DRY_RUN -> CONFIRMED -> QUEUED` (1 bước/lần lưu đúng bảng chuyển trạng thái `IMPORT_STATUS_TRANSITIONS`) để RQ background worker thực thi thật không bị chặn ở `CONFIRMED -> RUNNING`.
2. **Tách `pool_ui.list` cho màn `/cskh/pool`:** Hợp đồng `customers.pool_list` của T1B được khóa cho tập work item `queued` (`SEC-07`). Để màn Kho khách hàng hiển thị cả lead mới nhập chưa phân phối (`Chưa phân phối`), `Đã giao`, `Xung đột` và `Lỗi dữ liệu` kèm bộ lọc server-side mà không phá hợp đồng T1B, T1A bổ sung `crm_cskh.api.pool_ui.list` dùng chung chính hàm giải quyết scope `reporting._scope(actor)` (không nhận tham số `user`/`team`/`scope` từ client).
3. **CI GitHub Actions cho trình duyệt:** Workflow `.github/workflows/p0.yml` và `.github/workflows/p1.yml` hiện kiểm tra đầy đủ lint/format, inventory (`184` method P1 / `99` method P0), migration idempotency, P0 HTTP probe `18/18` và P1 HTTP probe `52/52`; job chạy Chromium Playwright (`deploy/p1/verify_p1_browser_e2e.mjs`) hiện chạy trên stack cô lập `crm-cskh-p1` và chưa gắn thành job riêng trong `.github/workflows/` để tránh sửa workflow CI backend của T1B.

### 7.8. Checklist nghiệm thu bàn giao T1A

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
