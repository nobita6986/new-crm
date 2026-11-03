# P1 Exit Report — CRM-CSKH P1 (backend + UI)

**Ngày:** 2026-11-02
**Vai trò:** T1B (chương 0–6: sửa backend P1 theo báo cáo kiểm định T1C) · T1A (chương 7: giao diện P1)
**Nhánh:** `codex/p1-foundation` — worktree `D:/HRP-app/p1-foundation` (chương 0–6) · `codex/p1-ui` — worktree `D:/HRP-app/p1-ui` (chương 7, **đã merge** backend của T1B)
**Báo cáo đầu vào:** [`deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md`](../../deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md) (L-01 … L-15)
**Kế hoạch:** [`docs/root-plan/P1.md`](../root-plan/P1.md)
**PR backend:** [#4 — feat(p1): CSKH P1 backend](https://github.com/nobita6986/new-crm/pull/4) — **OPEN, KHÔNG merge**
**PR UI (đầy đủ P1):** [#5 — feat(p1): CSKH P1 UI](https://github.com/nobita6986/new-crm/pull/5) — **OPEN, KHÔNG merge**
**SHA bàn giao backend:** `96ef842` — `fix(p1): close L-01..L-15 verification findings of the P1 backend` (HEAD của `codex/p1-foundation`, đã push lên `origin/codex/p1-foundation`)
**SHA bàn giao UI:** `ec5b4f7` — `fix(p1): publish the CSRF header of the session so the direct-HTTP probe can read the token` (mã nguồn bàn giao, đã push lên `origin/codex/p1-ui`; các commit sau đó chỉ là tài liệu)
**CI (PR #5):** `P0 DEV verification` = run [`41461874235`](https://github.com/nobita6986/new-crm/actions/runs/41461874235) — **SUCCESS** (probe HTTP 18/18 PASS) · `P1 backend verification` = run [`41461874336`](https://github.com/nobita6986/new-crm/actions/runs/41461874336) — **SUCCESS** (mã nguồn bàn giao `ec5b4f7`; các commit sau chỉ là tài liệu)
**Dữ liệu:** chỉ dữ liệu giả trên site kiểm định; không dùng production.

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

### 4.1. CI của PR #5 (nhánh `codex/p1-ui`)

| Workflow | Run | Kết quả | Ghi chú |
|---|---|---|---|
| `P0 DEV verification` | [`41461874235`](https://github.com/nobita6986/new-crm/actions/runs/41461874235) | **SUCCESS** | probe HTTP trực tiếp **18/18 PASS** (`probe_result=PASS`, `failed_cases=[]`); 99 method P0 vẫn xanh |
| `P1 backend verification` | [`41461874336`](https://github.com/nobita6986/new-crm/actions/runs/41461874336) | **SUCCESS** | per-method gate 174 method P1 + lint/format + schema |

Run `P0 DEV verification` trước đó (`41450325670`, SHA `c32e48f`) **FAIL** ở
case `csrf-page`: trang `/cskh` chỉ xuất `frappe.csrf_token = "…"`, còn probe
trực tiếp đọc token qua mẫu `"X-Frappe-CSRF-Token": "<token>"` ⇒ token rỗng ⇒
`sec14-post-ok` và 2 case `sec16-*` cũng fail (POST không có token hợp lệ).
Sửa tại `ec5b4f7`: trang `/cskh` **xuất header CSRF của session** dưới dạng
object JSON (`window.cskhCsrfHeader`), `window.frappe.csrf_token` đọc lại từ
đó — một nguồn sự thật duy nhất cho cả trình duyệt lẫn probe.

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

<!-- T1A-UI-CHAPTER -->

---

## 7. Chương T1A — Giao diện P1 hoàn chỉnh (5 màn hình P1 §9, Việt hóa & E2E thực tế)

**Ngày cập nhật:** 2026-11-03
**Vai trò:** T1A — CSKH P1 UI (cùng Frappe site/session với CRM theo ADR-UI-001 & ADR-P1-06)
**Nhánh:** `codex/p1-ui` — worktree `D:/HRP-app/p1-ui` ([PR #5](https://github.com/nobita6986/new-crm/pull/5))
**Backend baseline tích hợp:** `3025495f58bc0391c3f8d092af500b0566a5f038` (PR #4 — `codex/p1-foundation`, 174/174 P1 PASS, 99/99 P0 PASS)
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

1. **Nguồn gốc tích hợp:** Nhánh `codex/p1-ui` merge trực tiếp commit backend đã qua CI `3025495f58bc0391c3f8d092af500b0566a5f038` (`origin/codex/p1-foundation`), không sao chép tệp chưa commit giữa các worktree.
2. **Bảo toàn tuyệt đối hợp đồng & phân quyền backend:**
   * `permissions.py` và `p1_permissions.py`: **0 dòng thay đổi**.
   * `hooks.py`: giữ nguyên `normalize_p1_error_response` bọc ngoài bộ chuẩn hóa P0.
   * `translations/vi.csv`: hợp nhất có chủ đích qua [`deploy/p1/rebuild_translations.py`](../../deploy/p1/rebuild_translations.py) — giữ nguyên toàn bộ dòng 3 cột có `context` từ P0 (`2cd4f60`), toàn bộ dòng backend từ `3025495`, và toàn bộ danh mục P1 UI (**577 dòng**, `0` dòng lỗi cú pháp CSV).
3. **Hướng dẫn dựng và tái hiện đầy đủ cho T1C (trên site cô lập `crm-cskh-p1`):**

```bash
# 1. Lấy đúng nhánh tích hợp backend (3025495) + UI (codex/p1-ui)
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 3025495f58bc0391c3f8d092af500b0566a5f038 HEAD

# 2. Khởi chạy stack cô lập P1 (cổng 8120 / 9120, không đụng stack P0 DEV 8010/8091)
cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

# 3. Chạy kiểm toán tĩnh (ruff 0.14.0 + inventory 174 method + audit dịch Việt 100%)
docker exec crm-cskh-p1-bench-1 bash -lc "uvx ruff==0.14.0 check --no-cache /src/apps/crm_cskh && uvx ruff==0.14.0 format --check --no-cache /src/apps/crm_cskh"
python -X utf8 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json
python -X utf8 deploy/p1/check_translations_audit.py

# 4. Chạy tuần tự toàn bộ suite tích hợp P1 (174) + UI (6) + P0 core (99 + 8) = 287 method
python -X utf8 deploy/p1/run_suites.py

# 5. Chạy E2E HTTP thật đa vai trò + E2E trình duyệt Chromium thật (sinh báo cáo JSON & 11 screenshot)
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

Lệnh chạy tuần tự: [`deploy/p1/run_suites.py`](../../deploy/p1/run_suites.py) — **14/14 module PASS (`287/287` method)**:

| Nhóm suite | Module | Số method | Kết quả |
|---|---|---:|---|
| P1 backend | `test_p1_api_permissions` | 30 | **PASS** |
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

1. **Điều chỉnh duy nhất trong `services/reporting.py` so với `3025495`:** Bổ sung kiểm tra `allow_pii = scope.get("pii", True)` trong `reporting.pool_list()` để vai trò `CSKH BOD Viewer` không nhận `lead_name` và `mobile_no` (tuân thủ P1 §8.1: BOD xem tổng hợp không lộ PII mặc định), đồng thời giữ nguyên `_scope` cho `CSKH CRM Admin` và giữ xanh 100% `174/174` method P1.
2. **Tách `pool_ui.list` cho màn `/cskh/pool`:** Hợp đồng `customers.pool_list` của T1B được khóa cho tập work item `queued` (`SEC-07`). Để màn Kho khách hàng hiển thị cả lead mới nhập chưa phân phối (`Chưa phân phối`), `Đã giao`, `Xung đột` và `Lỗi dữ liệu` kèm bộ lọc server-side mà không phá hợp đồng T1B, T1A bổ sung `crm_cskh.api.pool_ui.list` dùng chung chính hàm giải quyết scope `reporting._scope(actor)` (không nhận tham số `user`/`team`/`scope` từ client).
3. **CI GitHub Actions cho trình duyệt:** Workflow `.github/workflows/p0.yml` và `.github/workflows/p1.yml` hiện kiểm tra đầy đủ lint/format, inventory, migration idempotency, P0 HTTP probe 18/18 và 174 method P1; job chạy Chromium Playwright (`deploy/p1/verify_p1_browser_e2e.mjs`) hiện chạy trên stack cô lập `crm-cskh-p1` và chưa gắn thành job riêng trong `.github/workflows/` để tránh sửa workflow CI backend của T1B.
4. **Đo hiệu năng import 10.000 dòng:** Vẫn theo ghi nhận mở tại ADR-001 / chương backend, chưa benchmark tải lớn từ trình duyệt.

### 7.8. Checklist nghiệm thu bàn giao T1A

| Hạng mục nghiệm thu P1 (phạm vi T1A) | Trạng thái | Bằng chứng đối chiếu |
|---|---|---|
| Đồng bộ có kiểm soát từ commit backend `3025495`, không copy bẩn, không nới quyền | ✅ ĐẠT | `git merge-base`, `0` diff trên `permissions.py` / `p1_permissions.py` |
| Trang 1 — Kho khách hàng (trạng thái suy ra, bộ lọc, tách bộ đếm lead vs work item) | ✅ ĐẠT | `pool_ui.py`, `pool.js`, [`01-pool-admin.png`](../../deploy/p1/evidence/screenshots/01-pool-admin.png), [`11-pool-bod-zero-pii.png`](../../deploy/p1/evidence/screenshots/11-pool-bod-zero-pii.png) |
| Trang 2 — Nhập danh sách (CSV/XLSX, mapping, preview, confirm, tiến độ Bước 3, lịch sử, tải file lỗi theo quyền, retry lỗi) | ✅ ĐẠT | `import_history.py`, `import.js`, `E2E-02`, `E2E-03`, [`02-import-admin-step3-and-history.png`](../../deploy/p1/evidence/screenshots/02-import-admin-step3-and-history.png) |
| Trang 3 — Chiến dịch & quy tắc (tạo/sửa/đóng, weight %, `capacity=0` = “Không giới hạn”, preview 5 cột, chống double-submit, stale preview, tra cứu & retry run) | ✅ ĐẠT | `campaigns.js`, `E2E-04`, [`03-campaigns-admin.png`](../../deploy/p1/evidence/screenshots/03-campaigns-admin.png), [`04-campaigns-error-not-found.png`](../../deploy/p1/evidence/screenshots/04-campaigns-error-not-found.png) |
| Trang 4 — Khách được giao (danh sách, tìm kiếm/sắp xếp, chi tiết & lịch sử phụ trách, reassign/revoke đúng quyền & `expected_owner`) | ✅ ĐẠT | `ownership_ui.py`, `my-customers.js`, `E2E-05`, [`05-my-customers-admin-ownership-history.png`](../../deploy/p1/evidence/screenshots/05-my-customers-admin-ownership-history.png), [`06-my-customers-staff-a.png`](../../deploy/p1/evidence/screenshots/06-my-customers-staff-a.png), [`08-my-customers-leader-a-team-scope.png`](../../deploy/p1/evidence/screenshots/08-my-customers-leader-a-team-scope.png) |
| Trang 5 — Dashboard & lịch sử (filter chiến dịch/ngày/đội/nhân viên/run, phân biệt rõ current owner vs assignment events) | ✅ ĐẠT | `dashboard.js`, `E2E-05`, `E2E-06`, [`09-dashboard-leader-b-team-isolation.png`](../../deploy/p1/evidence/screenshots/09-dashboard-leader-b-team-isolation.png), [`10-dashboard-bod-live-vs-events.png`](../../deploy/p1/evidence/screenshots/10-dashboard-bod-live-vs-events.png) |
| Không biến lỗi backend thành danh sách rỗng, số `0` hay báo thành công giả | ✅ ĐẠT | `cskh.renderError` + `p1_errors`, kiểm chứng `403`/`404`/`409` trên HTTP & Chromium |
| Việt hóa 100% nhãn, trạng thái, validation, thông báo và lỗi P1 | ✅ ĐẠT | `vi.csv` 577 dòng, `check_translations_audit.py` (`372/372`, `0` chuỗi sót) |
| Render an toàn (chống XSS/formula injection), CSRF, 0 lỗi console/asset/500 | ✅ ĐẠT | `verify_p1_browser_e2e.mjs` → `browser-manifest.json` (`11/11` PASS) |
| Toàn bộ suite P1 + UI + P0 xanh tuần tự | ✅ ĐẠT | `run_suites.py` (`287/287` method PASS) |
| Gắn job Playwright UI vào `.github/workflows/` | ⏳ Chưa gắn workflow CI | Có sẵn script tự động `verify_p1_browser_e2e.mjs` để T1C chạy trực tiếp; chưa sửa `.github/workflows/p1.yml` của T1B |

> **Kết luận bàn giao của T1A:** Toàn bộ 5 màn hình P1 §9, bản dịch tiếng Việt 100%, bộ kiểm thử endpoint UI, E2E HTTP đa vai trò và E2E trình duyệt Chromium thật đã hoàn tất trên nhánh `codex/p1-ui` (PR #5) trên nền backend `3025495f58bc0391c3f8d092af500b0566a5f038`, sẵn sàng để **T1C** kiểm định độc lập và để **Owner** trực tiếp duyệt trên `http://p1.localhost:8120`. Quyết định merge và ký GO P2 thuộc T0/Owner.
