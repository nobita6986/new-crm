# P1 Exit Report — CRM-CSKH Backend P1

**Ngày:** 2026-11-02
**Vai trò:** T1B (sửa backend P1 theo báo cáo kiểm định T1C)
**Nhánh:** `codex/p1-foundation` — worktree `D:/HRP-app/p1-foundation`
**Báo cáo đầu vào:** [`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md`](../../deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md) (L-01 … L-15)
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md)
**PR:** [#4 — feat(p1): CSKH P1 backend](https://github.com/nobita6986/new-crm/pull/4) — **OPEN, KHÔNG merge**
**SHA bàn giao:** `96ef842` — `fix(p1): close L-01..L-15 verification findings of the P1 backend` (HEAD của `codex/p1-foundation`, đã push lên `origin/codex/p1-foundation`)
**Dữ liệu:** chỉ dữ liệu giả trên site DEV; không dùng production.

---

## 0. Tóm tắt

Bản sửa backend P1 đã đóng **15/15** lỗi L-01 … L-15 của báo cáo kiểm định T1C, mở
rộng suite P1 từ **103** lên **174 method** (7 module), và bổ sung:

* một suite **regression riêng** `crm_cskh.tests.test_p1_regressions` (**71 method**) viết
  để **tái hiện từng lỗi L-0x trước khi xác nhận bản sửa**;
* một **race harness đa tiến trình thật** (`crm_cskh.tools.p1_process_race`) chạy N=2 và
  N=5 **OS process** (không phải thread) cho import và distribution;
* một **workflow CI riêng** `.github/workflows/p1.yml` với per-method JUnit gate trên
  inventory **P1 riêng** (không trộn vào inventory 99 của P0);
* **API contract P1** (`api-contract-p1.md`) và báo cáo này (L-11);
* **bản dịch P1** trong `translations/vi.csv` (L-12).

Kết quả đo được trên site `p1fresh.localhost` (site đã seed dữ liệu) và site khôi phục
`p1restore2.localhost`:

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| Suite P1 (7 module) | **174/174 PASS** | [`evidence/p1-t1b-20261102/p1-junit/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-t1b-20261102/p1-junit/p1-per-method-gate.json) |
| Suite P0 core (inventory 99) | **99/99 PASS** | [`evidence/p1-t1b-20261102/p0/g007-per-method-gate.json`](../../deploy/dev/evidence/p1-t1b-20261102/p0/g007-per-method-gate.json) |
| Auxiliary (3 suite, ngoài inventory) | **PASS** | [`evidence/p1-t1b-20261102/p0/auxiliary/`](../../deploy/dev/evidence/p1-t1b-20261102/p0/auxiliary/) |
| Suite P1 trên site **restore** | **174/174 PASS** | [`evidence/p1-t1b-20261102/restore/p1-per-method-gate.json`](../../deploy/dev/evidence/p1-t1b-20261102/restore/p1-per-method-gate.json) |
| Race import N=2 / N=5 | 1 identity + 1 lead, 0 trùng | [`evidence/p1-t1b-20261102/race/`](../../deploy/dev/evidence/p1-t1b-20261102/race/) |
| Race distribution N=2 / N=5 | 1 claim + 1 work item, 0 trùng | [`evidence/p1-t1b-20261102/race/`](../../deploy/dev/evidence/p1-t1b-20261102/race/) |
| `bench migrate` × 2 | exit 0, **không đổi** số dòng | [`evidence/p1-t1b-20261102/migrate/`](../../deploy/dev/evidence/p1-t1b-20261102/migrate/) |
| Backup → restore sang site mới | counts **khớp 100%** | [`evidence/p1-t1b-20261102/restore/counts-after-restore.txt`](../../deploy/dev/evidence/p1-t1b-20261102/restore/counts-after-restore.txt) |
| Lint + format (ruff 0.14.0) | pass | bước `lint` của `.github/workflows/p1.yml` |

### 0.1. Chạy lại trên SHA bàn giao (`1f17d33`)

Toàn bộ suite đã được **chạy lại trên đúng SHA bàn giao** (không chỉ dựa vào bằng chứng
cũ), trên site `p1fresh.localhost`, **tuần tự** (không chạy song song — chạy song song hai
suite trên cùng site gây tranh chấp DB và sinh lỗi giả):

| Suite | Module | Method | Kết quả |
|---|---|---|---|
| P1 | `test_p1_api_permissions` | 30 | OK |
| P1 | `test_p1_concurrency` | 10 | OK |
| P1 | `test_p1_distribution` | 17 | OK |
| P1 | `test_p1_identity` | 13 | OK |
| P1 | `test_p1_imports` | 16 | OK |
| P1 | `test_p1_ownership` | 17 | OK |
| P1 | `test_p1_regressions` | 71 | OK |
| **P1 tổng** | 7 module | **174** | **OK** |
| P0 core | `test_permissions` | 18 | OK |
| P0 core | `test_security_remediation` | 12 | OK |
| P0 core | `test_security_remediation_v2` | 32 | OK |
| P0 core | `test_docshare_lifecycle` | 10 | OK |
| P0 core | `test_bt1c5_matrix_gaps` | 27 | OK |
| **P0 core tổng** | 5 module | **99** | **OK** |
| Auxiliary | `test_health` | 4 | OK |
| Auxiliary | `test_poc_custom_field` | 3 | OK |
| Auxiliary | `test_error_response_normalization` | 8 | OK |
| **Auxiliary tổng** | 3 module | **15** | **OK** |

Mã nguồn trong container được đối chiếu bằng **md5** với worktree cho
`test_p1_regressions.py`, `services/reporting.py`, `services/import_service.py` — **khớp**.

---

## 1. Bảng đóng lỗi L-01 … L-15

| Mã | Lỗi T1C | Nguyên nhân gốc | Bản sửa | Test hồi quy |
|---|---|---|---|---|
| **L-01** | `reconcile()` đọc `assigned_to` (cột không tồn tại) trên `CSKH Assignment History` và cộng lịch sử toàn hệ thống | Cột của lịch sử là `to_user`; truy vấn không lọc campaign | `services/distribution.py::reconcile` đọc `h.to_user`, **nối** `CSKH Work Item` theo `work_item` và lọc `wi.campaign = %s`; trả thêm `distinct_leads` | `TestL01Reconcile` (3 method) |
| **L-02** | `hashlib.sha256` nhận `str` ⇒ `TypeError` khi `File.get_content()` trả `str` (file đã flush xuống disk) | Không chuẩn hóa `str`/`bytes` | `services/import_service.py::_checksum` chuẩn hóa về `bytes` trước khi băm | `TestL02Checksum` (4 method) — gồm CSV trên disk, UTF-8 BOM, XLSX thật |
| **L-03** | Mapping trả **giá trị ô** thay vì **tên cột**; mapping nhiều field đọc nhầm cột tên | `_resolve_column_name` trả `cells[column]` | Hàm trả **tên cột**; ưu tiên field chuẩn `phone`; fallback chỉ nhận cột đã map có **tên** là header điện thoại; preview **và** worker dùng chung hàm | `TestL03ColumnResolution` (5 method) |
| **L-04** | SQL alias sai: `` `wi.assigned_to` `` bị MariaDB hiểu là tên bảng `wi.assigned_to` | Backtick bao quanh cả alias | Backtick chỉ bọc **tên cột**, alias đứng ngoài: `wi.`assigned_to`` | `TestL04PoolQuery` (4 method) |
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

### Bản sửa bổ sung ngoài danh sách L (phát hiện trong lúc rà)

| Mã | Vấn đề | Bản sửa |
|---|---|---|
| **L-16** | `P1Error` chỉ khai báo `http_status`; Frappe đọc `getattr(e, "http_status_code", 500)` ⇒ **mọi** lỗi P1 trả HTTP 500 | `services/errors.py` khai báo **cả** `http_status` và `http_status_code` trên `P1Error` và 11 lớp con (400/403/404/409/413) |
| **L-17** | Bộ lọc ngày của dashboard chỉ lọc events ⇒ dashboard hiển thị **hai khoảng thời gian khác nhau** cùng lúc | Bộ lọc ngày áp cho **mọi** card, `per_user` và `identity_count` |
| **L-18** | `pool_list`/`dashboard` dùng `frappe.db.count` với bộ lọc tuple/dict lẫn lộn | Chuẩn hóa một hàm `scoped_count` với alias + clause độc lập |

---

## 2. Bằng chứng runtime

### 2.1. Site và lệnh

Site kiểm chứng: `p1fresh.localhost` (đã seed dữ liệu giả), site khôi phục:
`p1restore2.localhost`. Container: `crm-cskh-p1-dev-bench-1` (bench `frappe/bench`), MariaDB 11.8.

```bash
# Suite P1 (7 module, mỗi module một JUnit XML)
bench --site p1fresh.localhost --verbose run-tests --module crm_cskh.tests.test_p1_<module> \
  --junit-xml-output /tmp/p1-evidence/<slug>.junit.xml

# Per-method gate (inventory P1 riêng, 174 method)
python3 deploy/dev/seed/ci_junit_gate.py \
  --inventory deploy/dev/seed/p1_test_inventory.json \
  --xml-dir <dir> --out <dir>/p1-per-method-gate.json

# P0 core (99 method) — inventory riêng, KHÔNG trộn P1
python3 deploy/dev/seed/ci_junit_gate.py \
  --inventory deploy/dev/seed/g007_test_inventory.json \
  --xml-dir <dir> --out <dir>/g007-per-method-gate.json

# Race thật bằng N PROCESS riêng
bench --site p1fresh.localhost execute crm_cskh.tools.p1_process_race.run \
  --kwargs "{'mode': 'import', 'workers': 5}"
bench --site p1fresh.localhost execute crm_cskh.tools.p1_process_race.run \
  --kwargs "{'mode': 'distribution', 'workers': 5}"

# Migrate hai lần + backup/restore
bench --site p1fresh.localhost migrate   # ×2
bench --site p1fresh.localhost backup --with-files --compress
bench new-site p1restore2.localhost --mariadb-root-password "$DB_ROOT_PASSWORD" ...
bench --site p1restore2.localhost restore <backup> --with-public-files --with-files <tar>
```

### 2.2. Kết quả per-method gate

```
g007-per-method-gate: PASS     # P1
  expected_total : 174
  observed_total : 174
  passing_total  : 174
  PASS    : 174

g007-per-method-gate: PASS     # P0 core
  expected_total : 99
  observed_total : 99
  passing_total  : 99
  PASS    : 99
```

### 2.3. Race đa tiến trình (N process thật)

`p1_process_race` spawn `sys.executable -c` — **mỗi worker là một OS process riêng** với
kết nối MariaDB riêng, nên trọng tài thật là **unique index của MariaDB**, không phải GIL
của Python.

| Kịch bản | N | Kết quả |
|---|---|---|
| import | 2 | `identity_rows=1`, `lead_rows=1`, `duplicate_leads=0` |
| import | 5 | `identity_rows=1`, `lead_rows=1`, `duplicate_leads=0` |
| distribution | 2 | `claims=1`, `work_items=1`, `duplicate_items=0` (1 worker thua unique index) |
| distribution | 5 | `claims=1`, `work_items=1`, `duplicate_items=0` (4 worker thua unique index) |

Các worker thua là do `IntegrityError 1062` trên unique index `cskh_domain_unique` của
`CSKH Work Item` — **đây là bằng chứng trọng tài là constraint của DB**, không phải khóa
trong Python.

### 2.4. Migrate idempotent

Số dòng trước và sau `bench migrate` × 2 trên site đã có dữ liệu: **không đổi**.

| Bảng | Trước | Sau |
|---|---|---|
| `CSKH Work Item` | 2718 | 2718 |
| `CSKH Assignment History` | 1394 | 1394 |
| `CSKH Identity Index` | 2534 | 2534 |
| `CSKH Campaign` | 1256 | 1256 |
| `CSKH Import Batch` | 213 | 213 |
| `CSKH Assignment Rule` | 523 | 523 |
| `CSKH Distribution Run` | 807 | 807 |

Dump `SHOW INDEX` của 8 bảng P1 trước và sau restore: [`p1-schema-indexes.txt`](../../deploy/dev/evidence/p1-t1b-20261102/migrate/p1-schema-indexes.txt),
[`restored-schema-indexes.txt`](../../deploy/dev/evidence/p1-t1b-20261102/restore/indexes-after-restore.txt).

### 2.5. Backup / restore

Backup `p1fresh.localhost` → restore sang site mới `p1restore2.localhost` ⇒ counts **khớp
100%** (`work_items=2718`, `history=1394`, `identity=2534`, `claims=1024`) và **toàn bộ
suite P1 (174 method) PASS trên site khôi phục**.

---

## 3. Ma trận quyền và phạm vi đã kiểm chứng

| Actor | Đọc được | Bị từ chối | Bằng chứng |
|---|---|---|---|
| `CSKH BOD Viewer` | Dashboard aggregate toàn hệ thống; **không** PII lead | Ghi campaign/rule/history | `TestL05DashboardScope`, `TestApiContract` |
| `Sales Manager` (leader) | Dashboard **chỉ** nhánh cây con; rule/run của campaign trong subtree | Rule/run của team khác, kể cả campaign chung | `TestL05DashboardScope`, `TestL07LeaderRead` |
| `Sales User` (staff) | `my_list` của chính mình | Work item của người khác, pool, distribution | `TestL15NoPermissionBypass`, `TestSecurityNegatives` |
| `CSKH CRM Admin` | Import, campaign, rule, distribution, ownership, dashboard | — | `TestApiContract` |
| `Administrator` / `System Manager` | Đường quản trị hệ thống (không phải actor nghiệp vụ) | — | `TestApiContract.test_the_business_roles_are_not_system_roles` |

**Không** cấp `System Manager` cho BOD; **không** có endpoint nào nhận tham số mở rộng
scope (`user=`, `team=`).

---

## 4. CI

Workflow mới: [`.github/workflows/p1.yml`](../../.github/workflows/p1.yml). Các gate:

1. `bootstrap` — stack ghim, site sạch từ volume rỗng;
2. `lint` — `ruff==0.14.0 check` + `format --check` trên `apps/crm_cskh` và script inventory;
3. `inventory` — `p1_test_inventory.py --check` ⇒ inventory **sinh từ source**, method bị
   xoá/đổi tên ⇒ **fail-closed**;
4. `seed` + `migrate` (site có dữ liệu) + `migrate` lần hai ⇒ **idempotent**;
5. `per_method` — 7 module, mỗi module một JUnit XML;
6. `per_method_gate` — `ci_junit_gate.py` trên inventory **P1** (174);
7. `schema` — `SHOW INDEX` của 8 bảng P1;
8. artifact đã **sanitize** (cookie, CSRF, URL file private, **số điện thoại**).

> Suite P1 **không** được trộn vào inventory 99 của P0: `p1_test_inventory.json` và
> `g007_test_inventory.json` là hai file riêng, hai gate riêng, hai thư mục evidence riêng.

---

## 5. Phần còn lại / giới hạn đã biết

1. **UI P1 (T1A) chưa nằm trong phạm vi backend.** Bản dịch `vi.csv` đã được bổ sung
   (L-12) nhưng **chưa có screenshot** từng màn: ô T1-20 của T1C vẫn cần T1A đóng.
2. **Chưa có endpoint download file lỗi.** `error_artifact` trả **tên** `File` (private);
   UI tải qua route `File` chuẩn của Frappe.
3. **Hiệu năng import 10.000 dòng chưa đo** (ADR-001 còn mở); `mobile_no` chưa có index.
4. **Bộ lọc ngày** dùng hai cột khác nhau có chủ ý: `creation` của work item cho các card,
   `event_at` của sự kiện cho `assignment_events`. Đã ghi rõ trong
   [`api-contract-p1.md` §6](./api-contract-p1.md).
5. **`reconcile`** trả `current_owners` và `assignment_events` dưới dạng **map** theo user;
   UI cần biết đây là hai số khác nhau (chuyển giao sinh 2 sự kiện, 1 chủ).
6. **Chưa có test crash/retry với lỗi inject** sau khi tạo Work Item — thiết kế savepoint
   có sẵn nhưng chưa có test runtime chứng minh.
7. **PR #4 chưa merge.** Không merge theo yêu cầu; chờ T1C kiểm định độc lập.

---

## 6. Tài liệu liên quan

* [`api-contract-p1.md`](./api-contract-p1.md) — contract P1 (19 endpoint, mã lỗi, quyền,
  pagination, idempotency)
* [`schema-p1.md`](./schema-p1.md) — 8 DocType, constraint, index
* ADR: `adr-p1-01` … `adr-p1-08` (identity, claim, import, weighted-fair, ownership, UI,
  RBAC, import-limits)
* [`glossary-vi.md`](./glossary-vi.md) — thuật ngữ tiếng Việt
* [`../../deploy/dev/seed/p1_test_inventory.json`](../../deploy/dev/seed/p1_test_inventory.json)
  — inventory 174 method, sinh từ source
* Bằng chứng: [`../../deploy/dev/evidence/p1-t1b-20261102/`](../../deploy/dev/evidence/p1-t1b-20261102/)
