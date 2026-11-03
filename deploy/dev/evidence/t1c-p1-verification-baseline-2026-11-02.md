# T1C — Báo cáo kiểm định độc lập P1 (Baseline Round 1)

> **Vai trò:** T1C — Independent Security & Quality Verification
> **Ngày:** 2026-11-02
> **Phạm vi:** `docs/root-plan/P1.md` §12–§14 (14 gate `G1-01` → `G1-14`, T1-01→T1-20, SEC-01→SEC-10)
> **Baseline P0:** `main` @ `6db1f3b55afece6fba21c71200fbd55df1200e29` (CI run `41372213683`, SUCCESS) — **giữ nguyên, không đụng**
> **Kết luận:** **KHÔNG gate nào của P1 đạt PASS.** `G1-01` = PASS (có thể ký). `G1-02` = PARTIAL. `G1-03` → `G1-14` = PARTIAL hoặc NOT RUN.

---

## 0. Tóm tắt điều hành

T1C đã **không** chỉ đọc tài liệu. T1C dựng một site Frappe sạch độc lập, cài đúng 3 app theo version lock, chạy suite P1 của T1B, và **tự viết probe runtime** để kiểm chứng từng invariant. Kết quả:

- **T1B draft chưa merge, chưa commit, chưa có CI** → mọi thứ trong `D:\HRP-app\p1-foundation` là **scaffold chưa kiểm thử** ở mức nghiệm thu.
- **5 lỗi runtime High/Critical đã tái hiện được**, trong đó có lỗi làm **hỏng hoàn toàn luồng Import** (`G1-03`) và lỗi **rò scope Dashboard theo team** (`G1-09`/`G1-11`).
- **P0 regression 99 method vẫn xanh** khi chạy trên nền code P1 → không có regression P0 do phần P1.
- **3 suite P1 của T1B FAIL** ngay trên site sạch: `test_p1_distribution` (1 FAIL + 1 ERROR), còn `test_p1_identity` (13/13) và `test_p1_ownership` (17/17) PASS.

**Bảng gate cuối:**

| Gate | Trạng thái T1C | Lý do |
|---|---|---|
| G1-01 P0 provenance | **PASS (T1C ký)** | main = 6db1f3b, CI #41372213683 success, version lock L-01..L-04 khớp |
| G1-02 DB schema | **PARTIAL** | Unique/index đúng trong DB, nhưng `migrate` lặp lại **không** tái tạo index sau khi app đã cài; `patches.txt` rỗng; `ensure_indexes` chỉ chạy qua `on_doctype_update` |
| G1-03 Phone-only import | **NOT RUN → FAIL (chặn)** | Import pipeline **hỏng**: `TypeError: Strings must be encoded before hashing` và `_resolve_column` trả về *giá trị* thay vì *tên cột* ⇒ 100% dòng bị đánh dấu `no_phone_column` |
| G1-04 Canonical identity | **PARTIAL** | `normalized_phone` UNIQUE + `canonical_lead` UNIQUE có thật trong DB; race import **chưa** kiểm chứng được vì import hỏng |
| G1-05 Campaign/rules | **PARTIAL** | Rule validation OK; nhưng Leader **không** đọc được Rule/Run qua list (0/196, 0/164) → scope campaign↔hierarchy không khớp |
| G1-06 Weighted fair | **PARTIAL** | Allocator thuần toán PASS (1000→500/300/200; 100 batch→50/30/20). Nhưng chưa có bằng chứng 1000 lead thật trên DB |
| G1-07 Atomic assignment | **PARTIAL** | Race 2/5 job: **không** giao trùng, **không** trùng claim/owner — đạt. Nhưng `reconcile()` **luôn** lỗi SQL ⇒ không có đường đối soát |
| G1-08 Reassign/revoke | **PARTIAL** | `test_p1_ownership` 17/17 PASS (ToDo/DocShare/History/owner trong transaction). Thiếu: HTTP runtime, append-only thật qua generic endpoint |
| G1-09 BOD dashboard | **FAIL (chặn)** | `reconcile()` lỗi SQL; dashboard **lọc campaign** lỗi `KeyError`/`ValueError`; **Leader thấy số toàn hệ thống** (scope leak) |
| G1-10 My Customers | **PARTIAL** | `my_list` lọc đúng `assigned_to = session.user`; `pool_list` **lỗi SQL** ⇒ màn "Kho chưa giao" hỏng |
| G1-11 Security | **FAIL (chặn)** | Xem §4 lỗi L-01..L-05. Chưa có HTTP runtime evidence cho P1 |
| G1-12 Tiếng Việt | **NOT RUN** | UI P1 ở `D:\HRP-app\p1-ui`, chưa có CI, chưa chụp screenshot |
| G1-13 Stability | **NOT RUN** | Chưa có CI P1, chưa có restore smoke P1, chưa benchmark |
| G1-14 P1 handoff | **NOT RUN** | **Chưa có `P1-Exit-Report.md`**; `api-contract.md` vẫn nội dung P0 |

**GO P2: NO-GO.** Quyết định thuộc T0/Owner.

---

## 1. G1-01 — Entry gate / version lock / P0 provenance (PASS)

| Kiểm tra | Kết quả |
|---|---|
| `main` HEAD | `6db1f3b55afece6fba21c71200fbd55df1200e29` ✅ khớp yêu cầu |
| PR #3 | merged, `Merge pull request #3 from nobita6986/codex/p0-post-merge-review` ✅ |
| CI run gần nhất trên `main` | `41372213683` — `success`, 7m33s, 2026-11-02T01:23:09Z ✅ |
| `version-lock.md` L-01 Frappe CRM `v1.87.0` | `0d3910b456ef4fb913e77b29db011b3debc4f778` ✅ |
| `version-lock.md` L-02 Frappe `v15.123.1` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` ✅ (container chạy `frappe 15.123.1 HEAD` — khớp) |
| `version-lock.md` L-03 Frappe `v16.52.0` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` ✅ |
| `version-lock.md` L-04 frappe_docker `v4.0.1` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` ✅ |
| P0 mốc nghiệm thu (#174, 99 JUnit, SEC 28/28, matrix 71) | **Không đụng, giữ nguyên** ✅ |

**Ghi nhận (không chặn):** `version-lock.md` §2 L-06 vẫn ghi repo SHA cũ `bc114d21cd099593c2dcb4396b040838f36a3cce`, trong khi `main` hiện tại là `6db1f3b`. Đây là **hồ sơ provenance lỗi thời**, không phải lỗi version lock upstream (L-01..L-04 đều đúng). Nên sửa ở round handoff P1.

### 1.1 Bằng chứng provenance của bản thân lần kiểm định này

| Hạng mục | Giá trị |
|---|---|
| Site kiểm định | `p1c.localhost` (DB `_1286594db4344a27`), tạo **mới** trên container `crm-cskh-p1-dev-bench-1` |
| Nguồn code được test | `/src` → `D:\HRP-app\p1-foundation` (worktree `codex/p1-foundation`, base `6db1f3b`, **toàn bộ file P1 untracked**) |
| Apps cài | `frappe` 15.123.1, `crm`, `crm_cskh` (P1 draft) |
| Lệnh tạo site | `bench new-site p1c.localhost --mariadb-root-password … --admin-password admin` |
| Lệnh migrate | `bench --site p1c.localhost migrate` (chạy **2 lần**, không lỗi) |
| Test runner | `bench --site p1c.localhost run-tests --module …` (tuần tự, không song song) |

> ⚠️ **Cảnh báo về caveat:** bản T1B ở `p1-foundation` **không có commit nào**. Mọi kết quả dưới đây gắn với **worktree state không commit**, không gắn với SHA. Khi T1B commit, phải **chạy lại toàn bộ probe này trên SHA đó** — kết luận hiện tại **không tự động chuyển**.

---

## 2. Rà ADR / schema / API contract — phân biệt *thiết kế* và *runtime evidence*

### 2.1 ADR (thiết kế — **không** phải bằng chứng)

T1B viết 8 ADR: `adr-p1-01-identity-index`, `-02-active-claim`, `-03-import-engine`, `-04-weighted-fair`, `-05-ownership-sync`, `-06-ui-integration`, `-07-rbac`, `-08-import-limits`.

| ADR | Quyết định thiết kế | Runtime evidence của T1C | Đánh giá |
|---|---|---|---|
| ADR-P1-01 | Canonical identity index, `normalized_phone` UNIQUE, E.164 | `tabCSKH Identity Index` có `normalized_phone[U]` + `canonical_lead[U]` thật trong MariaDB ✅; `test_p1_identity` 13/13 PASS ✅ | **Thiết kế đúng, runtime cơ bản đúng** |
| ADR-P1-02 | Active Claim UNIQUE theo Lead, trong cùng transaction | `tabCSKH Active Claim`: `lead[U]`, `work_item[U]` ✅; race 2/5 job không tạo claim trùng ✅ | **Đúng** |
| ADR-P1-03 | Import: wrapper riêng quanh `File`, stdlib `csv` + `openpyxl`, server re-read & re-normalize | **Đường đi thực thi KHÔNG chạy được** (L-02, L-03) ❌ | **Thiết kế hợp lý, runtime hỏng** |
| ADR-P1-04 | Cumulative deficit, state persisted theo epoch, `capacity_limit=0` = không giới hạn | Allocator thuần toán PASS ✅; race OK ✅; nhưng `reconcile` hỏng ❌ | **PARTIAL** |
| ADR-P1-05 | Atomic ownership: lock → owner → ToDo → DocShare → commit; re-assert `lead_owner` | `test_p1_ownership` 17/17 PASS ✅ (owner, ToDo, DocShare, History, interval) | **Đúng ở unit/DB level**; thiếu HTTP runtime |
| ADR-P1-06 | Tích hợp UI theo ADR-003 P0 | UI ở `D:\HRP-app\p1-ui` (untracked), **chưa build, chưa CI** ❌ | **NOT RUN** |
| ADR-P1-07 | `CSKH CRM Admin` là *business role*; BOD read-only; scope theo hierarchy | `has_permission` chặn đúng: staff mutate → `PermissionError`; BOD write campaign → `PermissionError`; staff gọi `api.distribution.execute` → `Forbidden` ✅. **Nhưng** Dashboard rò scope ❌ | **PARTIAL / có lỗi leak** |
| ADR-P1-08 | Giới hạn import (bytes/rows) | `DEFAULT_MAX_FILE_BYTES=10MB`, `DEFAULT_MAX_ROWS=50000` — code có, nhưng không đường nào chạy tới được ❌ | **NOT RUN** |

### 2.2 Schema (8 DocType) — runtime thật trên MariaDB

Đọc trực tiếp `information_schema` trên DB kiểm định:

```
tabCSKH Active Claim       => lead[U] | work_item[U] | PRIMARY[U:name]
tabCSKH Assignment History => event_id[U] | cskh_lookup_0[I:work_item,event_at] | cskh_lookup_1[I:to_user,ownership_start,ownership_end]
tabCSKH Assignment Rule    => cskh_domain_unique[U:campaign,user,version]
tabCSKH Campaign           => code[U]
tabCSKH Distribution Run   => idempotency_key[U] | cskh_lookup_0[I:campaign,status]
tabCSKH Identity Index     => normalized_phone[U] | canonical_lead[U]
tabCSKH Import Batch       => idempotency_key[U]
tabCSKH Work Item          => cskh_domain_unique[U:campaign,lead] | cskh_lookup_0[I:assigned_to,status,assigned_at] | cskh_lookup_1[I:campaign,status] | cskh_lookup_2[I:lead,status]
```

✅ Đáp ứng §5.6: unique canonical phone, composite `(campaign, lead)`, composite `(campaign, user, version)`, unique `event_id`, unique `idempotency_key` (Import Batch + Distribution Run), `Active Claim` unique theo Lead.

⚠️ **Ghi nhận G1-02:** `patches.txt` **rỗng** (chỉ có mốc `[pre_model_sync]`/`[post_model_sync]`). Toàn bộ index composite được tạo bởi `on_doctype_update` → `ensure_indexes()`. Nếu index bị mất (restore DB cũ, dump không kèm index, hoặc `migrate` trên schema đã tồn tại mà `on_doctype_update` **không** re-fire), **không có patch nào dựng lại**. P1 §5.6 yêu cầu *"Đặt các unique/index trong schema patch hoặc controller `on_doctype_update` được version-control"* — controller được version-control ✅, nhưng **chưa có test "migrate hai lần"** cho path này, và `patches.txt` rỗng là một khoảng trống cho `PR-01`.

⚠️ **Ghi nhận:** `CSKH Assignment History` **không có** `distribution_run` được ghi (0/304 dòng có giá trị). Trường tồn tại trong schema nhưng `ownership.assign()` không set ⇒ **mất liên kết audit giữa lịch sử và run phân phối** (P1 §12.5 yêu cầu đối soát run).

### 2.3 API contract — **không** tồn tại cho P1

`docs/architecture/api-contract.md` (bản trong `p1-foundation`) nội dung vẫn là **baseline P0** ("Hợp đồng API … baseline P0"), tự nó nói: *"Reporting/export chưa có scope BOD vì ownership service chưa triển khai — tiếp tục fail-closed theo ADR-004/ADR-005"*. Trong khi code P1 **đã** triển khai reporting + ownership ⇒ **hồ sơ API contract lệch với runtime**.

T1C đối chiếu endpoint P1 thực tế vs §8.2/§10.1:

| Endpoint | File | Method | Role gate | Ghi nhận |
|---|---|---|---|---|
| `crm_cskh.api.imports.create_preview` | `api/imports.py` | POST | `CSKH CRM Admin` | ✅ |
| `crm_cskh.api.imports.confirm` | `api/imports.py` | POST | `CSKH CRM Admin` | ✅ |
| `crm_cskh.api.imports.get_status` | `api/imports.py` | GET | `CSKH CRM Admin` | ✅ |
| `crm_cskh.api.distribution.preview` | `api/distribution.py` | POST | `CSKH CRM Admin` | ✅ |
| `crm_cskh.api.distribution.execute` | `api/distribution.py` | POST | `CSKH CRM Admin` | ✅ |
| `crm_cskh.api.distribution.get_run` | `api/distribution.py` | GET | Admin/BOD | ✅ |
| `crm_cskh.api.distribution.reconcile` | `api/distribution.py` | GET | Admin | ❌ lỗi SQL (L-01) |
| `crm_cskh.api.customers.my_list` | `api/customers.py` | GET | 4 role | ✅ |
| `crm_cskh.api.customers.pool_list` | `api/customers.py` | GET | Manager/Admin/BOD | ❌ lỗi SQL (L-04) |
| `crm_cskh.api.dashboard.distribution` | `api/dashboard.py` | GET | BOD/Admin/Manager | ⚠️ rò scope (L-05) |
| `crm_cskh.api.dashboard.campaigns` | `api/dashboard.py` | GET | BOD/Admin/Manager | ✅ |
| `crm_cskh.api.ownership.assign/reassign/revoke` | `api/ownership.py` | POST | `CSKH CRM Admin` | ✅ |
| `crm_cskh.api.ownership.history` | `api/ownership.py` | GET | có check `has_permission` work item | ✅ |

**Đáp ứng §8.2:** mutation dùng POST, GET chỉ đọc ✅. **Thiếu:** chưa có tài liệu contract P1, chưa có rate limit import/preview/export (§8.2 yêu cầu), chưa có `version` namespace API.

---

## 3. Bảng mapping 14 gate (owner / actor / revision / criterion / artifact / trạng thái)

> `Revision` = `6db1f3b` (P0 base) + worktree `codex/p1-foundation` **uncommitted**. Không có SHA cho P1 ⇒ không gate nào ký được trên SHA.

| Gate | Owner | Actor kiểm định | Revision | Criterion (P1 §14) | Artifact bắt buộc | Evidence T1C có | Trạng thái |
|---|---|---|---|---|---|---|---|
| **G1-01** | T1A/T1B | T1C | `6db1f3b` | P0 GO + version lock + ADR | P0 Exit Report + version-lock + CI log | `main`=6db1f3b; CI `41372213683` success; `version-lock.md` L-01..L-04 | **PASS** |
| **G1-02** | T1B | T1C | 6db1f3b + worktree | DocTypes, indexes/unique, install/migrate lặp lại | Migration/test report | 8 bảng + unique/index đúng; `migrate` ×2 OK; **`patches.txt` rỗng** | **PARTIAL** |
| **G1-03** | T1B | T1C | worktree | CSV/XLSX số không tên; valid/trùng/lỗi | Fixtures + preview + commit screenshots | **Import hỏng 2 chỗ** (L-02, L-03) ⇒ không tạo được preview hợp lệ | **NOT RUN / FAIL** |
| **G1-04** | T1B | T1C | worktree | Không trùng normalized phone qua concurrent import | DB unique test + race test | Unique index có thật ✅; `test_p1_identity` 13/13 ✅; **race import chưa chạy được** (import hỏng) | **PARTIAL** |
| **G1-05** | T1B | T1C | worktree | Campaign/rule scope, weight, capacity | API + UI role tests | Rule validate OK; **Leader không đọc được Rule/Run** (0/196, 0/164); UI chưa có | **PARTIAL** |
| **G1-06** | T1B | T1C | worktree | 1.000 = 500/300/200; batch nhỏ đúng | Deterministic allocation test | Allocator thuần toán PASS (1000→500/300/200; 100 batch→50/30/20); **chưa có 1.000 lead thật trên DB** | **PARTIAL** |
| **G1-07** | T1B | T1C | worktree | Không trùng owner/work item khi 2+ job; retry an toàn | Parallel/concurrency/rollback | Race 2 & 5 job: 0 trùng work item / 0 trùng claim / 0 lead 2 owner ✅; **`reconcile()` hỏng** ⇒ không đối soát được | **PARTIAL** |
| **G1-08** | T1B | T1C | worktree | A→B/thu hồi có history đủ + dọn quyền cũ | Audit + cross-access test | `test_p1_ownership` 17/17 PASS ✅; thiếu HTTP runtime, thiếu append-only qua generic endpoint | **PARTIAL** |
| **G1-09** | T1B | T1C | worktree | BOD thấy kho/giao/chưa giao, totals chính xác | E2E + count reconciliation | Cards khớp DB khi không filter (assigned 299=299, claims 297=297) ✅; **filter campaign lỗi** ❌; **Leader thấy số toàn hệ thống** ❌ | **FAIL** |
| **G1-10** | T1B | T1C | worktree | Staff chỉ thấy khách được giao | E2E + negative API | `my_list` lọc đúng ✅; staff list generic đúng ✅; **`pool_list` hỏng SQL** ❌ | **PARTIAL** |
| **G1-11** | T1B | T1C | worktree | 0 High/Critical cross-access / unauthorized mutation | RBAC matrix + security CI | Có lỗi High (L-05 scope leak) + Critical-ish (L-01/L-04 endpoint chết); **chưa có HTTP runtime evidence P1** | **FAIL** |
| **G1-12** | T1B/UI | T1C | worktree | Màn hình P1 đủ tiếng Việt, 0 lỗi dịch nghiêm trọng | Screenshots + UI checklist | `p1-ui` untracked; `vi.csv` **chỉ là bản dịch P0** (không có mục P1: "My customers", "Campaign", "Assigned at"…) | **NOT RUN** |
| **G1-13** | T1B | T1C | worktree | CI xanh; retry batch; migration/restore | Actions logs + restore report | **Không có workflow P1**; `p0.yml` chỉ chạy 5 module P0 (99 method) | **NOT RUN** |
| **G1-14** | T1B | T1C | worktree | Đủ docs/schema/API/ADR + P1 Exit Report | Artifact checklist | 8 ADR ✅; schema ✅; **API contract = P0** ❌; **P1-Exit-Report.md không tồn tại** ❌ | **NOT RUN** |

---

## 4. Lỗi bàn giao cho T0 (T1C không tự sửa code)

> Format: severity / actor / input / expected / actual / SHA-run-artifact / cách tái hiện.

### L-01 — `distribution.reconcile()` luôn lỗi SQL ⇒ mất đường đối soát (G1-07, G1-09)

- **Severity:** **High** (chặn G1-07/G1-09; là endpoint `GET /api/method/crm_cskh.api.distribution.reconcile` — tức **mọi** lời gọi HTTP tới endpoint này đều 500)
- **Actor:** `CSKH CRM Admin` / `Administrator` (đã qua role gate)
- **Input:** `campaign = <bất kỳ campaign có work item>`
- **Expected:** trả `{current_owners, assignment_events, active_work_items, active_claims, as_of}` đúng với DB
- **Actual:** `OperationalError: (1054, "Unknown column 'assigned_to' in 'SELECT'")`
- **Nguyên nhân:** `services/distribution.py:429` — query đọc `assigned_to` từ `tabCSKH Assignment History`, nhưng cột đó **không tồn tại** trên bảng đó (History dùng `to_user`). Tên cột đúng ở bảng History là `to_user`.
- **Tác động thứ cấp:** suite `test_p1_distribution` **FAIL** ở `test_reconcile_reports_the_database_truth` (ERROR) ⇒ G1-07 **không** có bằng chứng đối soát.
- **Artifact:** `services/distribution.py:429-433`; probe T1C trên site `p1c.localhost`.
- **Tái hiện:**
  ```
  bench --site p1c.localhost console
  >>> from crm_cskh.services import distribution
  >>> distribution.reconcile(camp, actor="Administrator")
  OperationalError: (1054, "Unknown column 'assigned_to' in 'SELECT'")
  ```

### L-02 — Import pipeline chết ngay ở bước checksum (`TypeError`) (G1-03, G1-04)

- **Severity:** **Critical** (chặn G1-03/G1-04; **100%** lần import thất bại, không có preview nào tạo được)
- **Actor:** `CSKH CRM Admin`
- **Input:** bất kỳ file CSV/XLSX **đã upload thật** (private) — đường đi thực tế, không phải `content=` inline
- **Expected:** `create_preview()` trả `{batch, status, totals, preview}`
- **Actual:** `TypeError: Strings must be encoded before hashing`
- **Nguyên nhân:** `services/import_service.py:69` `_checksum(content)` gọi `hashlib.sha256(content)`. `File.get_content()` (Frappe `file.py:699-726`) **trả `str`** khi file đọc từ disk là text-decodable (`self._content = self._content.decode()`), nhưng **trả `bytes`** khi `content` field còn trong memory. T1C đã xác nhận cả hai nhánh:
  - file nạp qua `content=` (chưa flush) ⇒ `get_content()` → `bytes` ⇒ checksum OK;
  - file nạp qua upload thật (đã lưu disk) ⇒ `get_content()` → `str` ⇒ **vỡ**.
  Với `str` ⇒ `content.decode()` ở `_read_rows` cũng vỡ (`'str' object has no attribute 'decode'`).
- **Tác động thứ cấp:** `jobs/import_worker.py:58` `_process()` gọi `file_doc.get_content()` rồi `_read_rows(content, …)` ⇒ **cùng một lỗi**, worker không bao giờ commit được batch nào. ⇒ **G1-03/G1-04 không thể kiểm chứng**, và cả `ADR-P1-08` (giới hạn import) không bao giờ chạy.
- **Artifact:** `services/import_service.py:58-69`, `services/import_service.py:79-97`, `jobs/import_worker.py:56-62`.
- **Tái hiện:**
  ```python
  # trong bench console, site p1c.localhost
  f = frappe.get_doc({"doctype":"File","file_name":"x.csv","is_private":1,
                      "attached_to_doctype":"CRM Lead","attached_to_name":"Administrator",
                      "content": b"Số điện thoại\n0911111001\n"}).insert(ignore_permissions=True)
  frappe.get_doc("File", f.name).get_content()   # -> str  (sau khi đọc lại từ DB)
  # hoặc: import_service.create_preview(file_name=f.name, actor="<crm-admin>")
  # -> TypeError: Strings must be encoded before hashing
  ```

### L-03 — `_resolve_column` trả **giá trị** thay vì **tên cột** ⇒ 100% dòng `no_phone_column` (G1-03)

- **Severity:** **Critical** (chặn G1-03; kể cả sau khi L-02 được sửa, import **vẫn** cho ra 0 lead hợp lệ)
- **Actor:** `CSKH CRM Admin`
- **Input:** CSV header `Số điện thoại` / `mobile_no` / bất kỳ header hợp lệ nào
- **Expected:** preview `{total_rows: 6, valid_rows: 3, error_rows: 2, duplicate_rows: 1}`, canonical `+84911111001`
- **Actual:** preview `{total_rows: 2, valid_rows: 0, error_rows: 2}` với reason `no_phone_column` cho **mọi** dòng (kể cả XLSX)
- **Nguyên nhân:** `services/import_service.py:171-172` gọi
  ```python
  phone_column = _resolve_column(rows[0]["cells"] if rows else {}, mapping, _phone_candidates(header))
  ```
  nhưng `_resolve_column` (`import_service.py:123-142`) trả về **giá trị của ô** (`cells[column]`), không phải **tên cột**. Rồi `_classify(rows, mapping, phone_column)` lại dùng tham số này như **tên cột** (`_resolve_column(row["cells"], mapping, {phone_column})` ⇒ `{<số điện thoại>}`, không match header nào ⇒ `raw is None` ⇒ `no_phone_column`).
- **Tác động thứ cấp:** `jobs/import_worker.py:67` cũng gọi `_classify(rows, mapping, None)` ⇒ **luôn** ra `no_phone_column` cho mọi dòng ⇒ worker không tạo lead nào dù checksum đã sửa.
- **Bằng chứng phân biệt (T1C chạy trực tiếp, không sửa code):**
  ```
  _resolve_column(cells, None, cands)            -> "0911113001"   # giá trị  (SAI)
  _classify(rows, None, <tên cột>)               -> valid_rows=1, canonical=+84911113001  (ĐÚNG)
  _classify(rows, None, <giá trị>)               -> valid_rows=0, reason=no_phone_column  (SAI)
  ```
- **Tái hiện:** xem `services/import_service.py:171-172` và `services/import_service.py:220-221`.

### L-04 — `reporting.pool_list()` lỗi SQL do backtick trước **tên bảng** (G1-10)

- **Severity:** **High** (chặn G1-10 — endpoint `GET /api/method/crm_cskh.api.customers.pool_list` luôn 500 ⇒ màn "Kho chưa giao" không dùng được)
- **Actor:** `Sales Manager` / `CSKH CRM Admin` / `CSKH BOD Viewer`
- **Input:** bất kỳ lời gọi `pool_list(page=1)`
- **Expected:** `{scope, items, total, page, page_size}`
- **Actual:** `OperationalError: (1054, "Unknown column 'wi.assigned_to' in 'WHERE'")`
- **Nguyên nhân:** `services/reporting.py:75` `_user_filter()` sinh fragment `` `wi.assigned_to` in (...) `` rồi dán vào SQL có alias `wi` (bảng gốc là `tabCSKH Work Item`). Backtick quanh **tên cột có tiền tố alias** ⇒ MariaDB hiểu là tên *bảng* `wi.assigned_to` không tồn tại.
- **Tác động thứ cấp:** cùng lỗi với `scope="team"` lẫn `scope="self"` — **không** phụ thuộc actor. Cũng làm hỏng `G1-10` vì "Kho" là một trong hai màn chính.
- **Artifact:** `services/reporting.py:64-77` (`_user_filter`), `services/reporting.py:163-179` (`pool_list`).
- **Tái hiện:**
  ```
  >>> from crm_cskh.services import reporting
  >>> reporting.pool_list("t1c-lead-a@x.test", campaign=camp)
  OperationalError: (1054, "Unknown column 'wi.assigned_to' in 'WHERE'")
  ```

### L-05 — Dashboard rò scope: Leader nhìn thấy số toàn hệ thống (G1-09 + G1-11) — **High**

- **Severity:** **High** (vi phạm §10.1 "Aggregate count theo team phải tính qua scope được phép", §8.1, và là **cross-access data exposure** ⇒ NO-GO theo §14)
- **Actor:** `Sales Manager` (Leader của team A) — **không phải** `CSKH BOD Viewer`, **không phải** `System Manager`
- **Input:** `GET /api/method/crm_cskh.api.dashboard.distribution` (không truyền `campaign`)
- **Expected:** chỉ số của **subtree team A**: `assigned_work_items` = số work item `assigned` của 3 user trong team A; `per_user` chỉ gồm user trong team A
- **Actual:** `scope: "team"` **nhưng số liệu toàn hệ thống** — `assigned_work_items = 299` (DB toàn hệ thống = 299) trong khi **số đúng của team A = 9**; `per_user` gồm **6 user** bao gồm `t1c-staff-b1@x.test` (team B) và `cskh-p1-staff-b/c`; `unique_customers = 315` (toàn hệ thống) thay vì 9; `active_campaigns = 85` (toàn hệ thống).
- **Nguyên nhân:** `services/reporting.py:206-266` `distribution_dashboard()` tính `scope = _scope(user)` nhưng **không hề dùng** `scope` cho các `frappe.db.count(...)` (card) và **không** lọc `per_user` theo team — chỉ `_scope` được trả về trong payload. Scope được *tính* nhưng **không được *áp dụng***.
- **Tác động thứ cấp:** `services/reporting.py:229-231` `unique_leads` **đếm `CSKH Work Item`** chứ không đếm **lead duy nhất** ⇒ một lead có work item ở 2 campaign sẽ bị đếm 2 lần (P1 §12.5 yêu cầu "unique customers"). Hiện tại `count(distinct lead)` = 315 = `count(*)` = 315 nên **chưa lộ**, nhưng **thiết kế sai** ⇒ không đạt §12.5.
- **Bằng chứng đối chiếu (T1C, site sạch):**
  ```
  team A users = [t1c-lead-a, t1c-staff-a1, t1c-staff-a2]
  DB truth  : assigned_work_items trong team A = 9 ; toàn hệ thống = 299
  Dashboard (leader A, scope="team") : assigned_work_items = 299  ← SAI (phải 9)
  per_user : 6 user, gồm t1c-staff-b1@x.test (team B)  ← LEAK
  ```
- **Artifact:** `services/reporting.py:206-266`.

### L-06 — Dashboard/reconcile với tham số `campaign` luôn lỗi (G1-09)

- **Severity:** **Medium-High** (chặn filter/drill-down của BOD dashboard — §9.5)
- **Actor:** `CSKH BOD Viewer` / `CSKH CRM Admin`
- **Input:** `GET /api/method/crm_cskh.api.dashboard.distribution?campaign=<name>`
- **Expected:** số liệu thuộc campaign đó
- **Actual:** `KeyError: 'pvut967pm7'` (khi `campaign_filters` = `{"campaign": name}` bị truyền nhầm vào `frappe.db.count("CSKH Distribution Run", {"status": "failed"})` không liên quan) và `ValueError: not enough values to unpack (expected 2, got 1)` tại `frappe/database/query.py:147 apply_dict_filters` — do `frappe.db.count("CSKH Assignment History", ({"distribution_run": [...]},))` truyền **tuple** chứ không phải dict filters ⇒ Frappe coi tuple là cặp `(operator, value)`.
- **Nguyên nhân:** `services/reporting.py:263-266`
  ```python
  history_events = frappe.db.count(
      "CSKH Assignment History",
      ({"distribution_run": frappe.db.get_all("CSKH Distribution Run", filters=campaign_filters, pluck="name")} if campaign else {}),
  )
  ```
  → filter là **tuple** `({...},)` thay vì **dict**.
- **Artifact:** `services/reporting.py:263-266`.

### L-07 — Leader không đọc được Rule/Run của campaign mình (G1-05)

- **Severity:** **Medium**
- **Actor:** `Sales Manager` (Leader team A)
- **Input:** `frappe.get_list("CSKH Assignment Rule")` / `frappe.get_list("CSKH Distribution Run")` (list view của Desk)
- **Expected:** Leader thấy Rule/Run thuộc campaign trong scope team mình
- **Actual:** `0 / 196` rule và `0 / 164` run hiện hữu (leader có `Sales Manager` role và `read` permission trong DocType JSON)
- **Nguyên nhân:** `p1_permissions.py:109-115` `rule_query_conditions()` dùng `_campaign_scope_condition(user, "campaign")` — tức so sánh **cột `campaign`** (giá trị là *tên `CSKH Campaign`*) với **tên node `CRM Sales Hierarchy`**. Cùng kiểu lỗi ở `distribution_run_query_conditions()` (`p1_permissions.py:157`). Trong khi `campaign_query_conditions()` dùng `team_scope` — đúng — nên campaign mới lọc được.
- **Bằng chứng đối chiếu:** `leaderA_get_list_campaign` = 85/85 (không lọc vì `team_scope` null); `leaderA_get_list_rule` = 0/196; `leaderA_get_list_run` = 0/164.
- **Ghi nhận thêm:** `has_permission` cho Rule/Run vẫn trả `True` khi mở **trực tiếp 1 doc** (`leader_direct_read_rule: has_perm=true`) ⇒ **không nhất quán** giữa list-view (bị chặn) và direct-doc (được phép) ⇒ nguy cơ đường đọc khác (API `get_value`/`get_doc`) lọt scope.
- **Artifact:** `p1_permissions.py:109-115`, `p1_permissions.py:157-162`.

### L-08 — `CSKH Assignment History` không ghi `distribution_run` ⇒ mất liên kết audit (G1-07, G1-09, §12.5)

- **Severity:** **Medium**
- **Actor:** hệ thống (`ownership.assign()` từ `distribution.execute`)
- **Input:** chạy 1 distribution run bất kỳ
- **Expected:** mỗi event `assign` thuộc run ⇒ đối soát "1.000 event = 1.000 work item" (§12.5)
- **Actual:** `0 / 304` dòng History có `distribution_run` khác rỗng
- **Nguyên nhân:** `services/ownership.py:280-295` (`assign`) và `370-378` (`reassign`) không truyền `distribution_run` khi tạo History, dù `distribution.execute` (`services/distribution.py:305-309`) gọi `ownership.assign(..., request_id=f"run:{run.name}:{lead}")` — thông tin run chỉ nằm trong `request_id`/`reason` dạng text, không nằm ở cột có thể query.
- **Artifact:** `services/ownership.py:280-295`, `services/ownership.py:370-378`, `services/distribution.py:305-309`.

### L-09 — `cskh_is_placeholder_name` không tồn tại ⇒ worker import vỡ khi tạo Lead (G1-03, §6.2)

- **Severity:** **Medium** (chặn G1-03 — nhưng bị che bởi L-02/L-03; sẽ lộ ngay khi 2 lỗi trên được sửa)
- **Actor:** `CSKH CRM Admin` (qua worker)
- **Input:** worker tạo Lead mới
- **Expected:** Lead có `first_name = "Chưa rõ tên"` và `cskh_is_placeholder_name = 1` (P1 §6.2, ADR-006 P0)
- **Actual:** `OperationalError: (1054, "Unknown column 'cskh_is_placeholder_name' in 'SELECT'")` — trường **không** tồn tại trong `tabCRM Lead`
- **Nguyên nhân:** `jobs/import_worker.py:143-149` set `cskh_is_placeholder_name: 1`, nhưng **không có Custom Field nào** tạo trường đó. `fixtures/custom_field.json` chỉ có `cskh_poc_marker` (marker PoC của P0, không phải trường nghiệp vụ). `docs/architecture/data-model-spike.md:181` đã ghi rõ *"Trường nghiệp vụ `cskh_is_placeholder_name`: chưa tạo"* ⇒ **tài liệu và code đã lệch nhau từ trước**, T1B chưa đóng.
- **Artifact:** `jobs/import_worker.py:143-149`, `fixtures/custom_field.json`, `docs/architecture/data-model-spike.md:181`.

### L-10 — `capacity_limit = 0` = **không giới hạn**, mâu thuẫn §7.2 + `eligibility` loại user khi `capacity` = 0 (G1-05, G1-06)

- **Severity:** **Medium** (rủi ro logic công bằng; hiện chưa gây sai số vì test dùng `capacity=0` cho "không giới hạn")
- **Actor:** `CSKH CRM Admin` cấu hình rule
- **Input:** rule với `capacity_limit = 0`
- **Expected (theo P1 §7.2 + ADR-P1-04 §2.6):** `0` = **không giới hạn**
- **Actual (theo code):** `weighted_allocator.eligible_rules()` coi `capacity` truthy ⇒ `capacity=0` **không** giới hạn ✅ (khớp), **nhưng** `services/eligibility.py:105-107`:
  ```python
  if rule["capacity"] and capacity_usage(campaign, user) >= rule["capacity"]:
      return REASON_CAPACITY_FULL
  ```
  cũng coi `0` là không giới hạn ✅. **Tuy nhiên** `services/ownership.py:93-104` `_capacity_limit()` trả `max(capacity_limit)` và `if limit:` ⇒ `0` ⇒ không giới hạn ✅. Vậy **3 nơi nhất quán**. **Nhưng** `reporting.distribution_dashboard` (`reporting.py:255-260`) hiển thị `capacity: null` khi `capacity == 0` ⇒ **UI hiển thị "không giới hạn"** — nhất quán.
  ⇒ **T1C hạ xuống mức "cần chốt bằng văn bản"**, không phải lỗi runtime. Nêu ở đây vì `P1 §7.2` ghi *"không vượt capacity"* mà **không** nói rõ `0` nghĩa là gì, còn `p1_models.AssignmentRule.validate` chỉ check `capacity_limit >= 0`.
- **Hành động đề xuất (cho T1B/T0):** chốt rõ `0 = unlimited` trong `P1.md` §7.2 và trong `description` của field (đã có) + thêm test.

### L-11 — Thiếu `P1-Exit-Report.md` và API contract vẫn là bản P0 (G1-14)

- **Severity:** **Medium** (hồ sơ, không phải runtime)
- **Artifact:** `docs/architecture/api-contract.md` (nội dung P0, tự nói ownership service chưa triển khai — đã lỗi thời); **không có** `docs/architecture/P1-Exit-Report.md`; `docs/root-plan/P1.md` trong `p1-foundation` **rỗng** (không copy sang worktree).

### L-12 — `vi.csv` không có mục P1 (G1-12)

- **Severity:** **Low-Medium**
- **Artifact:** `apps/crm_cskh/crm_cskh/translations/vi.csv` — 209 dòng nhưng **chỉ là bản dịch P0** (`Lead`, `Lead Owner`, `Deal`, `Call Logs`…). Không có `My customers`, `Campaign`, `Assigned at`, `Work status`, `Apply filters`, `Loading...` — trong khi template `p1-ui` dùng đúng các chuỗi này.
- **Tác động:** P1 §12.2 T1-20 (Việt hóa UI) **không thể** đạt; UI P1 sẽ hiện **mixed English**.

### L-13 — Không có import sanitization formula/HTML (SEC-08, G1-11)

- **Severity:** **Medium** (thiếu hẳn một nhánh yêu cầu, chưa chứng minh là exploitable vì import đang hỏng)
- **Artifact:** grep toàn `apps/crm_cskh` của `p1-foundation`: **không có** `sanitize`/`escape`/`bleach`/xử lý `=cmd`/`+cmd`/HTML trong `services/` hay `jobs/`. `import_service._classify()` chỉ giữ `canonical` (số điện thoại) và **không** giữ tên/HTML ⇒ giảm rủi ro, nhưng **không có**:
  - sanitize tên/HTML nếu P1 mở rộng import cột tên;
  - công cứ / guard chống formula injection trong **file lỗi tải về** (`error_artifact` là `Link File` nhưng **không có code nào tạo file lỗi**);
  - log redaction: `import_worker` ghi `batch.error = str(exc)[:500]` — **không** redact, và `batch.error` là nội dung exception có thể chứa PII.
- **Ghi nhận:** SEC-08 của P1 §12.3 hiện **chưa có** testcase, chưa có artifact.

### L-14 — `pool_list` `total` không lọc theo scope (phụ thuộc L-04) (G1-10)

- **Severity:** **Low-Medium** (gộp vào L-04 để sửa một lần)
- **Artifact:** `services/reporting.py:170` — `frappe.db.count("CSKH Work Item", {"status": "queued", "campaign": campaign} if campaign else {"status": "queued"})` **không** áp scope team, trong khi `items` thì có ⇒ **total ≠ len(items) theo page** và số `total` lộ toàn hệ thống.

### L-15 — `frappe.get_all()` bỏ qua permission trong `reporting`/`eligibility` (P1 §8.2 cảnh báo)

- **Severity:** **Low** (hiện chưa thành leak vì output đã tự giới hạn theo actor, nhưng là điểm dễ vỡ)
- **Artifact:** `services/eligibility.py:35-42` (`frappe.get_all("CSKH Assignment Rule", …)`), `services/reporting.py:293-303` (`frappe.get_all("CSKH Campaign", …)`), `services/ownership.py:94-101` (`frappe.get_all("CSKH Assignment Rule", …)`), `api/ownership.py:96` (`frappe.get_all("CSKH Assignment History", …)`).
- **Ghi nhận:** P1 §8.2 nói rõ *"Không dùng `frappe.get_all` trả kết quả trực tiếp cho user khi dữ liệu yêu cầu row-level permission"*. Ở đây output **đã** giới hạn theo actor ở tầng trên, nhưng `api/ownership.py:96` `history()` dùng `frappe.get_all` sau khi chỉ kiểm `has_permission` cho **work item** ⇒ về nguyên tắc nên dùng `frappe.get_list` để áp `permission_query_conditions` của History.

---

## 5. Ma trận kiểm định T1-01 → T1-20

| ID | Kỳ vọng P1 | T1C evidence | Trạng thái |
|---|---|---|---|
| T1-01 | CSV chỉ cột SĐT (`0/84/+84` tương đương) → 1 Lead canonical, placeholder hợp lệ | Normalizer thuần toán PASS (`0912345678`→`+84912345678`, `84912345678`→`+84912345678`) ✅; **nhưng** import hỏng (L-02/L-03) ⇒ **không có** Lead/placeholder thật | **PARTIAL** (normalizer PASS / pipeline FAIL) |
| T1-02 | XLSX numeric/text/ô trống/dup → không tin scientific notation, thống kê lỗi & trùng đúng | Scientific notation bị từ chối ✅ (`9.12346E+08` → `INVALID_PHONE`); ô trống bị bỏ ✅; **nhưng** preview XLSX trả `valid_rows=0, error_rows=2` (L-03) ⇒ **FAIL** | **FAIL** |
| T1-03 | Upload lại cùng key + cùng file → không tạo Lead/Batch/History trùng | `idempotency_key` UNIQUE ✅ + code `create_preview` trả batch cũ khi key trùng ✅ — **nhưng** không chạy tới được do L-02 | **PARTIAL** (thiết kế đúng, chưa chạy) |
| T1-04 | Upload cùng file ở đợt khác → batch mới, không nhân đôi canonical Lead | Key mặc định = `import:{checksum}:{mode}` ⇒ **cùng file ⇒ cùng key ⇒ trả batch cũ**, tức **T1-04 mâu thuẫn với T1-03** với cùng cơ chế. Cần `import_mode`/batch khác để tách — **thiết kế chưa tách được** | **NOT RUN / thiết kế cần chốt** |
| T1-05 | Hai import đồng thời cùng canonical phone → unique DB chặn, reconcile đúng | Unique `normalized_phone` có thật ✅; **race import không chạy được** (L-02); `reconcile` hỏng (L-01) | **NOT RUN** |
| T1-06 | Imported Lead có owner và trường gốc; import trùng không đổi owner/status | `_ensure_lead` tái dùng lead, không ghi đè owner/status ✅ (thiết kế); **chưa chạy được** | **PARTIAL** |
| T1-07 | Preview 1.000 lead 50/30/20 → dự kiến 500/300/200 | `planned_distribution()` thuần toán đúng ✅; **chưa có preview 1.000 lead thật trên DB** | **PARTIAL** |
| T1-08 | Execute 1.000 lead 50/30/20 → actual 500/300/200, history/ToDo/Lead owner nhất quán | Allocator 1.000 trên DB **chưa** chạy; run thật T1C mới 6 lead → 3/2/1 ✅; **owner/ToDo/history** verify trong `test_p1_ownership` ✅ | **PARTIAL** |
| T1-09 | 100 batch × 1 lead → tổng 50/30/20 | `test_p1_distribution::TestPersistedFairness::test_100_single_lead_batches_keep_the_cumulative_ratio` **PASS trên DB thật** ✅ | **PASS (riêng)** |
| T1-10 | A hết quota giữa batch → không vượt quota, phần còn lại chia lại, có lý do | `test_the_capacity_limit_cuts_the_distribution_off` — **FAIL** trên site sạch (`created=2 != 1`) ❌ | **FAIL** |
| T1-11 | Staff disabled / sang team khác → không còn eligible | `eligibility._assignee_exclusion_reason` có `REASON_USER_DISABLED` / `REASON_USER_WRONG_TEAM` ✅ (thiết kế); **chưa có test runtime** trong suite P1 | **PARTIAL** |
| T1-12 | Hai run cùng chọn 1 Lead → 1 active claim, 1 owner | **Race 2 & 5 job T1C tự chạy**: 0 work item trùng lead, 0 claim trùng, 0 lead 2 owner ✅ | **PASS (T1C đo)** |
| T1-13 | Retry job khi đã commit một phần → không double assignment/history/counter | `execute()` idempotent qua `status` + `idempotency_key` + compare-and-swap ✅; `ownership` idempotent qua `event_id` ✅; **retry worker sau commit một phần chưa test** | **PARTIAL** |
| T1-14 | Rule/selection đổi sau preview → execute bị từ chối | `test_a_changed_rule_version_makes_the_preview_stale` + `test_a_changed_fairness_state_makes_the_preview_stale` **PASS** ✅ | **PASS (riêng)** |
| T1-15 | Chuyển A→B rồi B→C → history đủ 3 phiên, current owner C | `test_p1_ownership` 17/17 ✅ (interval, from/to, actor, event_id) | **PASS (unit/DB)** |
| T1-16 | Thu hồi → owner trống, claim/work item theo policy, A mất quyền | `test_p1_ownership` ✅ (DocShare gỡ, ToDo cancel, claim delete, owner None) | **PASS (unit/DB)**; thiếu HTTP |
| T1-17 | Đóng campaign khi có work item active → không mất lịch sử, yêu cầu xử lý rõ | `Campaign.validate` chặn close khi còn active work item ✅; **chưa có test** | **PARTIAL** |
| T1-18 | My Customers của A → chỉ việc A đang sở hữu | `my_list` lọc `assigned_to = session.user` ✅; staff list generic ra đúng 3/3 của mình ✅; `leaderA_my_list` = 0 (leader không có work item riêng) ✅ | **PASS (riêng)** |
| T1-19 | BOD Distribution Dashboard → tử/mẫu đúng, filter/scope/drill-down đúng, không gọi nhầm KPI CSKH | Cards khớp DB khi **không** filter ✅; **filter campaign lỗi** ❌ (L-06); **scope Leader rò** ❌ (L-05); `unique_customers` đếm work item chứ không đếm lead duy nhất ❌ | **FAIL** |
| T1-20 | Việt hóa UI P1 → không mixed English | `vi.csv` thiếu toàn bộ chuỗi P1 ❌ (L-12); UI chưa build/chưa CI | **NOT RUN** |

---

## 6. Ma trận kiểm định SEC-01 → SEC-10

| ID | Kỳ vọng P1 | T1C evidence | Trạng thái |
|---|---|---|---|
| SEC-01 | Staff A đọc Lead B bằng direct ID / generic API → từ chối | `staffA1_direct_read_teamB_workitem` → `has_perm=False` ✅; `frappe.get_list("CSKH Work Item", filters={"assigned_to": staff_b1})` → 0 ✅ (row-level OK) | **PASS (P1 DocType)**; Lead P0 đã PASS từ trước |
| SEC-02 | Staff A xem Work Item B / import errors / raw file → từ chối | Work Item B bị chặn ✅; `import_batch_query_conditions` = `1=0` cho staff ✅ (staff list = 0) ✅; **raw file**: `CSKH Import Batch.source_file` → `File` private ✅; `import_service._file_content` chặn file không private ✅ — **nhưng** chưa có HTTP test tải raw file (P0 đã có file-oracle cho File, chưa cho file import P1) | **PARTIAL** |
| SEC-03 | Staff gọi `distribution.execute` / `ownership.reassign` / `ownership.revoke` → từ chối | `api.distribution.execute` → `Forbidden: Distributing the customers requires the CRM Admin business role.` ✅; `api.ownership.*` cùng gate ✅; **nhưng** `distribution.execute` **`@frappe.whitelist(methods=["POST"])`** — cần xác nhận CSRF enforcement của Frappe cho POST ✔ (mặc định có) | **PASS (service level)** |
| SEC-04 | Leader X đọc aggregate và chuyển giao team Y → từ chối | Chuyển giao: `ownership.reassign` yêu cầu `CSKH CRM Admin` ⇒ Leader bị chặn ✅; **đọc aggregate: Lỗi L-05 — Leader đọc được aggregate toàn hệ thống** ❌ | **FAIL** (L-05) |
| SEC-05 | BOD viewer ghi/sửa/xóa Campaign/Rule/History → từ chối | `bod_write_campaign` → `PermissionError` ✅; `rule_has_permission`/`assignment_history_has_permission` chặn write/delete ✅; `AssignmentHistory.before_save` + `on_trash` chặn sửa/xóa ✅ | **PASS (service level)**; thiếu HTTP |
| SEC-06 | Owner A sau thu hồi không còn xem Lead & vật đính kèm; DocShare/ToDo cũ không tạo cửa hậu | `test_p1_ownership` PASS ✅ (`_sync_docshares` gỡ share cũ, `_cancel_todo`); `_assert_final_owner` chặn cửa hậu ✅ | **PASS (unit/DB)**; thiếu HTTP |
| SEC-07 | Staff dùng export/list endpoint & tự gán `team`/`user` khác → không mở rộng scope | Staff list generic bị lọc ✅; `api.customers.my_list` **không nhận** tham số `user`/`team` ✅; `api.dashboard.distribution` cũng không nhận ✅ — **nhưng** `pool_list` (L-04) và `distribution_dashboard` (L-05) lộ ⇒ **chưa đạt** | **FAIL** (L-05) |
| SEC-08 | Imported file chứa CSV formula / HTML → preview & tải lỗi không thực thi script/formula | **Chưa có** bất kỳ xử lý sanitization nào (L-13); `error_artifact` chưa có code tạo ⇒ **không có** đường tải lỗi để kiểm | **NOT RUN** |
| SEC-09 | User không có quyền đoán ID / lấy direct link tới File raw → từ chối | `CSKH Identity Index` + `CSKH Active Claim` **system-only** ✅ (`1=0` cho non-system, đã đo: staff & leader list = 0) ✅; file import private ✅ | **PARTIAL** (chưa có HTTP oracle cho P1) |
| SEC-10 | Admin thao tác đồng thời với Staff/current owner bị đổi → optimistic conflict không overwrite | `ownership.reassign` có `expected_owner` check ⇒ `Conflict` nếu lệch ✅; `_lock_work_item` `FOR UPDATE` ✅; **`ownership.assign` không có expected-version** (chỉ yêu cầu status == queued) — race giữa 2 admin trên cùng work item **chưa** có bằng chứng runtime | **PARTIAL** |

---

## 7. Race / concurrency / resilience / performance (§12.4)

| Yêu cầu | Cách T1C đo | Kết quả |
|---|---|---|
| **2 parallel distribution runs** chạm lead chung | 2 process `bench console` đồng thời, cùng campaign `T1C-RACE`, 5 lead, 3 rule 50/30/20 | Worker 0: `created=5, errors=0` ✅ · Worker 1: `created=0, errors=5` ✅ (bị chặn đúng) · DB: 5 work item, 0 lead trùng, 0 claim trùng, 0 lead 2 owner ✅ · 2 run: `completed` + `completed_with_errors` ✅ |
| **5 parallel distribution runs** | 5 process đồng thời | Cả 5 → `Conflict: No lead is eligible for this campaign.` ✅ (pool đã hết) — **không** có lead nào bị giao trùng ✅. *Lưu ý:* đây là hành vi "fail-fast", **không** phải "chờ & chia sẻ" — cần chốt kỳ vọng theo §7.5 |
| **2/5 parallel import jobs** cùng normalized phone | — | **KHÔNG chạy được**: L-02 chặn ở checksum ⇒ **T1-05 chưa có evidence** |
| **Artificial failure sau tạo Work Item, trước update owner** | Savepoint mỗi dòng (`services/distribution.py:292-319`) | **Thiết kế có** (`frappe.db.savepoint` + `rollback(save_point=…)` + khôi phục `state`/`loads`) ✅ — **nhưng T1C chưa inject lỗi để chứng minh** ⇒ **PARTIAL** |
| **Failure sau DB commit trước thông báo** (retry notification) | — | `event_id` UNIQUE + `ownership` idempotent ✅ (thiết kế); **chưa có test runtime** ⇒ **PARTIAL** |
| **Worker restart giữa batch 10.000 dòng** | — | `CSKH Import Batch` state machine (`IMPORT_STATUS_TRANSITIONS`) + `FAILED → QUEUED` retry ✅ (thiết kế); **chưa chạy được** (L-02) ⇒ **NOT RUN** |
| **`bench migrate` 2 lần trên DB có Work Items/History** | `bench --site p1c.localhost migrate` ×2 trên DB đã có 315 work item / 304 history | ✅ **PASS** — không lỗi, không mất dữ liệu |
| **Install site mới / backup+restore** | `bench new-site p1c.localhost` + `install-app crm` + `install-app crm_cskh` + `migrate` | ✅ **PASS** — clean-site bootstrap OK, 8 bảng + unique/index tạo đúng |
| **Benchmark 10.000 dòng import / 10.000 work item** | — | **KHÔNG** chạy (L-02). §12.4 nói *"không đặt cam kết SLA tốc độ khi chưa đo"* — T1C **không** đòi SLA, nhưng **gate G1-13** cần artifact benchmark |
| **Regression P0 sau khi thêm `crm_cskh`** | Chạy 5 module P0 + auxiliary trên site có P1 | ✅ `test_permissions` 18/18 · `test_security_remediation` 12/12 · `test_security_remediation_v2` 32/32 · `test_bt1c5_matrix_gaps` 27/27 · `test_docshare_lifecycle` 10/10 · `test_error_response_normalization` 8/8 ⇒ **99 method vẫn xanh** |

---

## 8. Kiểm định đúng của số liệu (§12.5)

Kịch bản chuẩn của P1: sau phân phối 1.000 khách ⇒ 1.000 work item / 1.000 owner / 1.000 claim / 1.000 event / 1.000 dòng run; khi chuyển 10 khách A→B thì **event +10** nhưng **"đang được giao" vẫn 1.000**.

T1C đối chiếu trên DB thật:

| Kiểm tra | Kết quả |
|---|---|
| `cards.assigned_work_items` vs `count(status='assigned')` | 299 = 299 ✅ |
| `cards.active_claims` vs `count(CSKH Active Claim)` | 297 = 297 ✅ |
| `cards.unique_customers` vs `count(distinct lead)` | 315 = 315 ✅ (**nhưng** code đếm `count(*)`, đúng **vì tình cờ** chưa có lead 2 campaign — xem L-05) |
| Dashboard **có** cộng history thành số khách đang giao? | ✅ **KHÔNG** — `current_owners` đọc từ `CSKH Work Item`, `assignment_events` là số riêng. Đúng tinh thần §12.5 |
| `reconcile()` phân biệt sự kiện vs owner hiện tại? | ❌ **Lỗi L-01** — endpoint chết ⇒ **không** chứng minh được |
| `assignment_events` khi filter campaign | ❌ **Lỗi L-06** — filter tuple sai ⇒ **không** chứng minh được |
| `history` ghi `distribution_run`? | ❌ **Lỗi L-08** — 0/304 ⇒ mất liên kết audit run ↔ event |

**Kết luận §12.5: PARTIAL.** Phần "không cộng history" đúng; phần "reconcile được" và "filter được" hỏng.

---

## 9. Kiểm định cô lập P0 ↔ P1 (P1 §6, yêu cầu T0)

- **T1C xác nhận: 99 method P0 vẫn là 99 method P0.** Không cộng suite P1.
- Bằng chứng: `deploy/dev/seed/g007_test_inventory.json` + `ci_junit_gate.py` trong `p0.yml` **chưa** được sửa; T1C **không** sửa file nào của P0.
- Suite P1 của T1B nằm ở `crm_cskh/tests/test_p1_*.py` — **tách biệt** đúng như `p1_base.py` tự ghi ("The P1 tests are a **separate suite** from the P0 core inventory (99 methods)"). ✅
- T1C **không** thêm `test_p1_identity` (13) / `test_p1_distribution` (12) / `test_p1_ownership` (17) vào inventory P0 — và **không** nên thêm. Đề xuất cho T1B: workflow CI **riêng** cho P1 với inventory P1 riêng + gate per-method riêng.
- **T1C không sửa code T1A/T1B**, không commit, không merge, không mở round mới. Working tree `new-crm` chỉ còn lại file scratch đã dọn.

---

## 10. Kế hoạch kiểm định cho các vòng tiếp theo (để T1B chuẩn bị, không phải mở round)

### 10.1 Thứ tự ưu tiên sửa (đề xuất cho T0/T1B)

1. **L-02 + L-03 + L-09** (Import) — 3 lỗi này chặn G1-03, G1-04, G1-05(UI), và làm T1-02/03/04/05/06 không chạy được. Sửa **trước**, rồi mới nói về race import.
2. **L-01 + L-06** (reconcile + dashboard filter) — mở khóa đường đối soát §12.5 và G1-07/G1-09.
3. **L-05** (dashboard scope) — đây là lỗi **High cross-access**, NO-GO theo §14. Sửa bằng cách áp `scope` vào **mọi** `frappe.db.count` và lọc `per_user` theo team.
4. **L-04 + L-14** (pool_list) — 1 dòng sửa backtick + 1 dòng sửa `total` ⇒ mở khóa G1-10.
5. **L-07** (leader rule/run scope) — thống nhất `campaign` ↔ `team_scope` mapping.
6. **L-08, L-12, L-13, L-11** — audit linkage, i18n, sanitization, hồ sơ.

### 10.2 Tiêu chí T1C sẽ dùng để ký lần sau (để T1B tự chuẩn bị)

Mỗi gate chỉ được ký khi có **đủ**:
- **SHA** của commit P1 (không dùng worktree untracked) + **CI run ID** + **artifact path**.
- **JUnit XML** theo từng method P1 (P1 suite tách riêng, **không** trộn vào inventory 99 của P0).
- **HTTP runtime evidence** cho mọi case có actor thật (không chỉ gọi hàm service trong Python).
- **DB evidence** cho mọi invariant DB: unique index, composite index, claim/owner, `event_id`, `idempotency_key` — chụp `information_schema` + `SHOW CREATE TABLE` của bảng thật trên site đã seed.
- **Race evidence**: N=2 và N=5 job thật (process riêng, không phải thread trong 1 process), kèm query hậu kiểm (`count(*) group by lead having count(*)>1` = 0).
- **Crash/retry evidence**: inject lỗi sau khi tạo Work Item (monkeypatch ở test, không sửa prod code), chứng minh không orphan.
- **Số liệu**: đối chiếu dashboard với query không giới hạn quyền, riêng cho từng actor (BOD / Leader / Staff / Admin).
- **i18n**: screenshot từng màn P1 (Import, Campaign, Rule, Preview, Execute, My Customers, Pool, Dashboard, History) + bảng glossary khớp `glossary-vi.md`.
- **Migration/restore**: `migrate` ×2 trên site có dữ liệu, `backup` + `restore` sang site mới, đối chiếu count trước/sau.

### 10.3 Định nghĩa "PASS" của T1C

T1C **không** ký gate chỉ vì:
- có ADR / có schema JSON đẹp / có code trông đúng;
- suite test của tác giả tự viết PASS mà **chưa** chạy trên CI với SHA cố định;
- có screenshot UI mà **không** có HTTP evidence phía sau.

---

## 11. Trạng thái cuối và đề nghị

- **P0:** giữ nguyên toàn bộ mốc nghiệm thu. `main` = `6db1f3b` ✅, CI `41372213683` success ✅, 99 method xanh **kể cả khi chạy trên nền code P1** ✅.
- **P1:** **không gate nào ngoài G1-01 đạt PASS.** 2 gate có lỗi chặn ở mức **Critical/High runtime** (G1-03/G1-09/G1-11), 8 gate PARTIAL, 4 gate NOT RUN.
- **GO P2:** **NO-GO** — thuộc T0/Owner.
- **T1C không sửa code, không commit, không merge, không mở round mới, không spawn agent khác.** Tất cả lỗi trên đã bàn giao để T0/T1B xử lý theo §10.1.
