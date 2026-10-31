# T1B — Runtime evidence per-method G0-07 từ CI (kết quả THẬT)

**Ngày:** 2026-10-22 · **Sở hữu:** T1B · **PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) · **Trạng thái:** **CÓ runtime evidence** — CI đã chạy thật, **không** còn là kế hoạch.

> **Tài liệu này chỉ ghi kết quả đo được trên site CI.** Không suy ra bất kỳ method nào là PASS từ log tổng hợp. Mọi dòng trạng thái dưới đây đến từ `g007-per-method-gate.json` — đối chiếu từng `<testcase>` trong 5 file `*.junit.xml` với inventory 99 method.

---

## 0. Tóm tắt

| # | Câu hỏi | Kết luận |
|---|---|---|
| 1 | CI có chạy được 99/99 method không? | **CÓ.** `observed_total = 99`, `SKIP = 0`, `NOT_RUN = 0`. |
| 2 | Bao nhiêu PASS? | **63 / 99.** Còn **12 FAIL** + **24 ERROR**. |
| 3 | Gate có đạt không? | **KHÔNG** (`gate_passed = false`) ⇒ workflow đỏ **đúng như thiết kế**. |
| 4 | Root cause của 36 FAIL/ERROR là gì? | Phần lớn là **lỗi harness/test**, **không** phải phát hiện bảo mật — xem §4. |
| 5 | HTTP probe? | Case đầu `sec13-guest` **PASS** (403 đúng kỳ vọng); probe crash ở case thứ 2 do `KeyError` — **đã sửa** (§5). |

---

## 1. Môi trường đo

| Hạng mục | Giá trị |
|---|---|
| Run CI (có evidence per-method đầy đủ) | [#39793215900](https://github.com/nobita6986/new-crm/actions/runs/39793215900) |
| Commit đo | `fb1b6ba` (PR #2, merge ref) |
| Frappe SHA | `d2efa79657ba5c1abf96098b3bfacc199517d57e` (v15.123.1) |
| CRM SHA | `0d3910b456ef4fb913e77b29db011b3debc4f778` (v1.87.0) |
| Bench image | `frappe/bench:v5.31.0@sha256:3b75943b…` |
| Site test | `p0-test.localhost` (sạch, `allow_tests true`) |
| Site probe HTTP | `p0-ci.localhost` (**đã seed**, có user giả) |
| Artifact | `p0-g007-evidence-39793215900` |

---

## 2. Kết quả per-method — 99/99 đã chạy

**Nguồn:** `g007-per-method-gate.json` (`gate_passed = false`).

```
expected_total : 99
observed_total : 99
passing_total  : 63
counts         : {PASS: 63, FAIL: 12, ERROR: 24}
SKIP           : 0        ← không method nào bị @unittest.skip
NOT_RUN        : 0        ← không method nào bị che mất
missing_modules: []       ← đủ 5 file XML
empty_xml      : []       ← không XML rỗng
unexpected     : 0        ← không testcase ngoài inventory
parse_errors   : 0        ← XML sạch
```

### 2.1 Theo module

| Module | Kỳ vọng | PASS | FAIL | ERROR | SKIP | NOT_RUN |
|---|---:|---:|---:|---:|---:|---:|
| `crm_cskh.tests.test_permissions` | 18 | **18** | 0 | 0 | 0 | 0 |
| `crm_cskh.tests.test_security_remediation` | 12 | 9 | 1 | 2 | 0 | 0 |
| `crm_cskh.tests.test_security_remediation_v2` | 32 | 16 | 2 | 14 | 0 | 0 |
| `crm_cskh.tests.test_bt1c5_matrix_gaps` | 27 | 17 | 2 | 8 | 0 | 0 |
| `crm_cskh.tests.test_docshare_lifecycle` | 10 | 3 | 7 | 0 | 0 | 0 |
| **Tổng** | **99** | **63** | **12** | **24** | **0** | **0** |

### 2.2 Kết luận quan trọng

- **`test_permissions` đạt 18/18 PASS** — nhóm ràng buộc owner-scope cơ bản chạy xanh trên site sạch.
- **62/99 method FAIL hoặc ERROR** ⇒ `G0-07` **vẫn PARTIAL**, `P0` **vẫn NO-GO**.
- **Không** có method nào `SKIP` hay `NOT_RUN` ⇒ gate đang đo đúng 99 method, không có "che mất" nào.

---

## 3. Hai `duplicate_methods` — hành vi gate ĐÚNG, không phải bug

```
crm_cskh.tests.test_security_remediation_v2::TestSEC15BODAndRISKSEC12BulkReassign
    ::test_bod_calling_crm_cskh_api_endpoints_denied
    ::test_bod_calling_guarded_reporting_via_execute_cmd_denied
```

Hai method này dùng `subTest(endpoint=...)` ⇒ `xmlrunner` sinh **nhiều `<testcase>`** cùng tên gốc (mỗi endpoint một entry). Gate quy tất cả về **method gốc** và giữ trạng thái nặng nhất.

Đây chính là hành vi §9.3 của [`t1b-g007-ci-plan-review-2026-10-22.md`](./t1b-g007-ci-plan-review-2026-10-22.md) thiết kế sẵn: nếu chỉ đếm `<testcase>` thì 2 method này có thể bị tính là "nhiều PASS" — gate đếm theo **method gốc** nên `passing_total` vẫn đúng 63.

---

## 4. Phân loại 36 FAIL/ERROR

> **Không** tự động coi FAIL/ERROR là phát hiện bảo mật. Phần lớn là **harness hoặc kỳ vọng test sai**, cần T1B sửa hoặc T1C/Owner ra quyết định.

### 4.1 Nhóm A — harness thiếu request context (16 ERROR)

```
AttributeError: request        (werkzeug.local → ngoài request context)
```

| Nhóm test | Số method |
|---|---:|
| `TestSEC15BODAndRISKSEC12BulkReassign` (subTest endpoint) | 2 (sinh ~11 entry) |
| `TestSEC27CallLogFallbackContact` | 2 |
| `TestSEC27CallLogFallbackContactInternal` | 2 |
| `TestCL0607TeamLeaderCallLog` (`*_can_call_*`) | 2 |
| `TestTK0506TeamLeaderTask` (`*_can_call_*`) | 2 |
| `TestNT0506TeamLeaderNote` (`*_can_call_*`) | 2 |

**Nguyên nhân:** các method này gọi wrapper (`guarded_crm.get_call_log`, `guarded_reporting.*`) đọc `frappe.local.request`. Chạy bằng `bench run-tests` (không qua HTTP server) thì `frappe.local.request` là `None` ⇒ `AttributeError: request`.

**Không phải** phát hiện bảo mật: đây là **giới hạn của tầng unit test**. Tầng này cần `frappe.app` application/request context; các ca HTTP tương ứng đã được probe ở §5.

### 4.2 Nhóm B — ERROR vì setup thiếu dữ liệu / thiếu quyền (8 ERROR)

| Method | Lỗi | Ghi chú |
|---|---|---|
| `TestSEC26FileURLLeak.*` (4) | `PermissionError: Not permitted` | Fixture của `TestSEC16FieldExposure` không tạo dữ liệu cần cho SEC-26. **Cần T1B rà lại fixture.** |
| `TestSEC16FieldExposure::test_get_linked_docs_of_document_returns_only_allowed` | `PermissionError: Not permitted` | như trên |
| `TestSEC22And23StaleAfterReassign.*` (2) | `PermissionError` (has_permission / raise_no_permission_to) | Test mong `PermissionError` cho **old owner** nhưng chạm nhầm nhánh; cần tách bạch setup. |
| `TestLEAD07UnassignedLead::test_staff_list_does_not_return_unassigned_lead` | `'str' object has no attribute 'name'` | **Lỗi test**: dùng `pluck="name"` rồi lại `.name` từng phần tử (đã là `str`). |
| `TestSEC14CSRFAndSessionHTTP::test_post_via_execute_cmd_without_csrf_rejected` | `CSRFTokenError: Invalid Request` (ném, không assert) | Test gọi `execute_cmd` mà chưa bọc `assertRaises` ⇒ rò thành ERROR thay vì PASS. |
| `TestSEC13GuestTransportHTTP::test_authenticated_user_via_execute_cmd_health_ping_succeeds` | `'NoneType' object has no attribute 'method'` | `frappe.local.request` là `None` — cùng gốc với Nhóm A. |

### 4.3 Nhóm C — kỳ vọng sai so với hành vi thật của CRM v1.87.0 (4 FAIL)

| Method | Kỳ vọng trong test | Thực tế đo được |
|---|---|---|
| `TestSEC27CallLogFallbackContact::test_get_call_contact_orphan_routes_to_lookup_path` | `full_name` có trong contact | `{'mobile_no': '0909876543'}` — upstream `get_call_contact` **không** trả `full_name` |
| `TestSEC27CallLogFallbackContactInternal::test_upstream_get_call_contact_orphan_routes_to_phone_lookup` | như trên | như trên |
| `TestSEC16FieldExposure::test_get_linked_calls_does_not_leak_permlevel_field` | `assertNotIn("_caller", call)` | payload có key `_caller` ⇒ **khác với kỳ vọng** |
| `TestRA04CrmAdminReassignBlocked::test_ra04_ownership_service_does_not_exist_yet` | danh sách endpoint reassign rỗng `[]` | `['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users']` — upstream CRM **đã** có các endpoint này |

> Hai dòng cuối là **điểm cần Owner/T1C chú ý**: chúng liên quan RA-04 và SEC-16. T1B **không** tự đổi kỳ vọng và **không** kết luận có/không có lỗ hổng — chỉ ghi nhận **khoảng cách giữa test và hành vi runtime**.

### 4.4 Nhóm D — DocShare lifecycle (7 FAIL) — **cần Owner, KHÔNG tự sửa**

Cả 7 method fail với `unexpectedly None` tại `self.assertIsNotNone(self._share_for(...))`:

| Method | Nội dung |
|---|---|
| `test_docshare_created_by_assign_agent_when_assignee_lacks_permission` | PINNED BEHAVIOR **[Q2]** — pending T0/Owner decision |
| `test_docshare_flags_after_assign_agent` | PINNED BEHAVIOR **[Q2]** |
| `test_docshare_removed_by_lead_save_when_owner_changes` | share không được tạo |
| `test_docshare_survives_lead_save_when_owner_unchanged` | share không được tạo |
| `test_docshare_survives_remove_assignments` | share không được tạo |
| `test_effective_permission_before_and_after_reassign` | share không được tạo |
| `test_effective_permission_of_shared_child_records` | share không được tạo |

**Diễn giải:** `assign_agent` **không** tạo `DocShare` trên app hiện tại ⇒ 5 test phụ thuộc share đó đều hỏng theo dây chuyền. Đây chính là **canary drift** mà các assert PIN được viết để bắt.

> **T1B KHÔNG sửa, KHÔNG chốt Q1–Q4.** `OWNER_DECISIONS_CONFIRMED` vẫn `False`. Đây là câu hỏi **chính sách** thuộc T0/Owner, không phải lỗi kỹ thuật để T1B tự quyết.

---

## 5. HTTP transport probe

**Run [#39806532380](https://github.com/nobita6986/new-crm/actions/runs/39806532380) — `probe_result = PASS`, 14/14 case.**

Đây là run đầu tiên probe **PASS trọn vẹn** sau 4 lần sửa liên tiếp, mỗi lần dựa trên log CI thật (§5.2–§5.4).

| Case | HTTP | Kết quả | Kỳ vọng đã đạt |
|---|---:|---|---|
| `sec13-guest` | **403** | ✅ PASS | Guest bị chặn, `exc_type = PermissionError` — **negative control có giá trị** |
| `staff-login` | 200 | ✅ PASS | login HTTP 200 |
| `sec13-user` | 200 | ✅ PASS | user đã đăng nhập nhận đúng danh tính của mình |
| `csrf-page` | 200 | ✅ PASS | `/cskh` trả CSRF token |
| `sec14-get` | 200 | ✅ PASS | GET **không** cần CSRF header |
| `sec14-post-nocsrf` | **400** | ✅ PASS | POST thiếu token → `CSRFTokenError` |
| `sec14-post-badtok` | **400** | ✅ PASS | POST token sai → `CSRFTokenError` |
| `sec14-post-ok` | 200 | ✅ PASS | POST với token hợp lệ → thành công |
| `administrator-login` | 200 | ✅ PASS | login HTTP 200 |
| `sec13-admin` | 200 | ✅ PASS | Administrator nhận đúng danh tính |
| `sec16-permlevel-get-data` | 200 | ✅ **PASS** | `data[0]` **không** có giá trị cho field `permlevel=1` |
| `sec16-permlevel-sentinel-scan` | 200 | ✅ **PASS** | sentinel không xuất hiện ở **bất kỳ** chỗ nào trong raw response |
| `sec26-staff-private` | 200 | ✅ PASS | non-system **không** có key `file_url` cho file private |
| `sec26-admin-private` | 200 | ✅ PASS | system caller **giữ** `file_url` |

### 5.1 Bằng chứng SEC-16 — permlevel thật sự bị lọc

Response thật của `sec16-permlevel-get-data` (staff, không System Manager):

```json
{"message": {"data": [{"name": "CRM-LEAD-00001", "modified": "...", "_seen": null}],
             "rows": ["name", "custom_cskh_sec16_restricted", "modified", ...], ...}}
```

`rows` **có** `custom_cskh_sec16_restricted` nhưng `data[0]` **không** có giá trị cho field đó ⇒ `get_data` đã lọc theo permlevel. Khớp đúng semantics test nguồn `test_crm_api_get_data_filters_permlevel_field` (`assertFalse(result["data"][0].get(fieldname))`).

Không assert key bị loại — chỉ assert **giá trị** vắng/falsy, như test nguồn.

### 5.2 Chặng 1 — `sec16-setup` FAIL: Frappe tìm `logs/` theo **CWD** (commit `fab729f`)

Run #39796261932 lỗi `[Errno 2] No such file or directory: '/logs/database.log'`.

Giả định ban đầu "CWD sai" ⇒ thêm `cd $BENCH_DIR`. Run #39800240716 đổi lỗi thành `/workspace/logs/database.log` — **càng xa hơn**.

Quan sát đủ 2 điểm dữ liệu ⇒ Frappe dựng `logs/` từ **thư mục cha của CWD**, không phải `sites_path`:

| CWD | Đường dẫn Frappe cố mở |
|---|---|
| `/workspace` | `/logs/database.log` |
| `/workspace/frappe-bench` | `/workspace/logs/database.log` |
| `/workspace/frappe-bench/sites` | `/workspace/frappe-bench/logs/database.log` ✅ |

`run-seed.sh` không gặp lỗi này vì L18 bật `FRAPPE_STREAM_LOGGING=1` (Frappe ghi log ra stdout, không mở file). Probe không bật cờ đó.

**Đã sửa**: probe tự `chdir` vào `<bench>/sites` trước `frappe.init()`, trả CWD về gốc trong `finally`/`except`.

### 5.3 Chặng 2 — `get_data` thiếu `filters`/`order_by` bắt buộc (commit `89e0b42`)

Run #39802331716: `sec16-setup` PASS (chặng 1 có hiệu lực) nhưng 2 case SEC-16 trả HTTP 500:

```
TypeError: get_data() missing 2 required positional arguments: 'filters' and 'order_by'
```

Đọc source thật tại CRM v1.87.0 (SHA `0d3910b4`, `crm/api/doc.py`) xác nhận `filters` và `order_by` là positional bắt buộc, không có default.

### 5.4 Chặng 3 — `filters` phải là dict, không phải chuỗi (commit `0034c35`)

Run #39804284753: truyền trong query string hết lỗi 500, nhưng ra HTTP 417:

```
FrappeTypeError: Argument 'filters' should be of type 'dict' but got 'str' instead
```

Nguyên nhân — `make_form_dict` trong Frappe (SHA `d2efa796`, `app.py`):

```python
request_data = request.get_data(as_text=True)
if request_data and request.is_json:
    args = json.loads(request_data)
else:
    args = {}
args.update(request.args or {})
```

Chỉ **body** được `json.loads`; mọi tham số trong query string đều là chuỗi ⇒ pydantic validate theo annotation `filters: dict` và từ chối.

**Đã sửa**: đổi 2 case SEC-16 sang **POST kèm JSON body** + `Content-Type: application/json` + `X-Frappe-CSRF-Token` — cùng cách desk gọi endpoint. Run #39806532380 ⇒ `probe_result = PASS`.

### 5.5 Việc kiểm tra còn lại

- **Không** gắn nhãn `attachment`/`file_url` là SEC-16 — `get_attachments` + `file_url` thuộc SEC-26 (`sec26-*`), đúng yêu cầu.
- Case `get_attachments` của SEC-26 **giữ riêng**, không gộp vào SEC-16.
- Probe chạy trên site **đã seed** (`p0-ci.localhost`, có user giả), không dùng site sạch cho các ca cần tài khoản.
- Không có credential/sid/CSRF token trong log hay artifact: mọi `cookie`/`Set-Cookie` đều `[REDACTED]`, `redaction_replacements = 4`. Đã quét toàn bộ artifact — không khớp mẫu rò rỉ nào.

> **Ghi chú attribution.** `commit_sha` trong artifact là `df0e504` = merge commit của PR #2 (`Merge 0034c35 into b382fe4`), có `0034c35` (bản sửa của T1B) làm parent. Đã xác minh qua GitHub API.

---

## 6. Hai blocker đã đóng trong quá trình chạy CI

### 6.1 `./env/bin/bench` không tồn tại (commit `435f2c7`)

Run #39790982457: `bash: line 1: ./env/bin/bench: No such file or directory` ⇒ **0/99**, gate báo đúng 99 `NOT_RUN`.

`bench init` v5.31 tạo venv bằng `uv venv env --seed` ⇒ `env/bin/` có `python` nhưng **không** có entry point `bench`; `bench` nằm trên `PATH`.

Bằng chứng đối chiếu trong chính run đó: step `backup_restore` (dùng `bench` trần) **SUCCESS**, các step dùng `./env/bin/bench` **FAIL**.

### 6.2 `KeyError` trong probe (§5.2, commit `8e4f694`)

### 6.3 CWD ảnh hưởng `logs/` của Frappe (commit `fab729f`)

Run #39796261932 `#39800240716` — xem §5.2. Ba lỗi liên tiếp ở SEC-16 đã đóng xong; probe PASS trọn vẹn ở #39806532380.

---

## 7. Trạng thái sau khi có evidence — **không** tự nâng

| Hạng mục | Trước | **Sau (đo được)** |
|---|---|---|
| Runtime evidence per-method | 0/99 | **63/99 PASS** · 12 FAIL · 24 ERROR · 0 SKIP · 0 NOT_RUN |
| HTTP transport probe | chưa chạy được | **14/14 PASS** · `probe_result = PASS` (run #39806532380) |
| `test_permissions` | PARTIAL | **18/18 PASS** |
| `SEC-13` | PARTIAL | **PASS (HTTP)** — 3/3 case: guest **403**, user **200**, admin **200** |
| `SEC-14` | PARTIAL | **PASS (HTTP)** — 4/4 case: GET 200, POST no-CSRF 400, bad-token 400, valid 200 |
| `SEC-16` | PARTIAL | **PASS (HTTP)** — 2/2 case: permlevel value vắng, sentinel không lộ |
| `SEC-26` | PARTIAL | **PASS (HTTP)** — 2/2 case: staff không `file_url`, admin giữ `file_url` |
| `G0-07` | PARTIAL — CHƯA KÝ | **PARTIAL — CHƯA KÝ** |
| `P0` | NO-GO | **NO-GO** |
| `OWNER_DECISIONS_CONFIRMED` | `False` | **`False`** |

**Không** chốt Q1–Q4 · **không** đổi policy RA-04 · **không** tự nâng SEC-26, G0-07, P0.

---

## 8. Việc còn lại (T1B, không cần Owner quyết)

| # | Việc | Nhóm |
|---|---|---|
| 1 | Sửa test lỗi: `pluck="name"` rồi `.name` (`test_staff_list_does_not_return_unassigned_lead`) | B |
| 2 | Bọc `assertRaises` cho `test_post_via_execute_cmd_without_csrf_rejected` | B |
| 3 | Bổ sung request context cho 16 method gọi wrapper (`frappe.local.request`) | A |
| 4 | Rà lại fixture của `TestSEC16FieldExposure` / `TestSEC26FileURLLeak` (4 ERROR `Not permitted`) | B |
| 5 | Tách setup của `TestSEC22And23StaleAfterReassign` (2 ERROR) | B |
| 6 | Đối chiếu 4 kỳ vọng sai với CRM v1.87.0 thật (§4.3) | C — **cần T1C/Owner** |

## 9. Việc **cần Owner/T0** (T1B không tự quyết)

| # | Việc |
|---|---|
| 1 | **Q1–Q4 DocShare** — 7 FAIL ở §4.4 đều dồn về "assign_agent có tạo DocShare không". `OWNER_DECISIONS_CONFIRMED = False` |
| 2 | **RA-04** — `remove_assignments` / `get_assigned_users` đã có trong upstream CRM v1.87.0; cần xác nhận policy |
| 3 | **SEC-16 `_caller`** — payload thực tế có key `_caller`; cần xác nhận đây là rò rỉ thật hay tên field hợp lệ |

---

## 10. Artifact

Run [#39793215900](https://github.com/nobita6986/new-crm/actions/runs/39793215900) → artifact `p0-g007-evidence-39793215900`:

| File | Nội dung |
|---|---|
| `g007-per-method-gate.json` / `.txt` | trạng thái **từng** method trong 99 |
| `test_permissions.junit.xml` + `.log` | 18 method |
| `test_security_remediation.junit.xml` + `.log` | 12 method |
| `test_security_remediation_v2.junit.xml` + `.log` | 32 method |
| `test_bt1c5_matrix_gaps.junit.xml` + `.log` | 27 method |
| `test_docshare_lifecycle.junit.xml` + `.log` | 10 method |
| `module-status.txt` | exit code từng module |
| `http/http-transport.json` | probe HTTP (1/13 case) |
| `workflow-metadata.json` | SHA + outcome từng step |

---

## 11. Gửi T1C để rà soát độc lập

T1B **không** tự kết luận cho các mục sau; nhờ T1C xác nhận độc lập:

1. §4.3 — 4 kỳ vọng test lệch với hành vi CRM v1.87.0 thật (`full_name`, `_caller`, RA-04 endpoints).
2. §4.1 — 16 ERROR `AttributeError: request`: xác nhận đây là giới hạn tầng unit test chứ **không** phải phát hiện bảo mật.
3. §4.4 — 7 FAIL DocShare: xác nhận canary drift, **không** coi là xác nhận chính sách.
4. §5.1 — probe crash sau case đầu: xác nhận negative control `sec13-guest` (403) là bằng chứng có giá trị.

Tài liệu T1A/T1C/Owner **không** bị T1B sửa.

---

## 12. Lịch sử

| Phiên bản | Ngày | Nội dung |
|---|---|---|
| v1–v3 | 2026-10-22 | Kế hoạch CI + triển khai gate + vá lỗ hổng SKIP + đóng blocker lint — xem [`t1b-g007-ci-plan-review-2026-10-22.md`](./t1b-g007-ci-plan-review-2026-10-22.md) §1–§11 |
| **v4 (tài liệu này)** | **2026-10-22** | **Runtime evidence THẬT từ CI run #39793215900**: 99/99 đã chạy · **63 PASS / 12 FAIL / 24 ERROR / 0 SKIP / 0 NOT_RUN** · gate đỏ đúng thiết kế · phân loại 36 FAIL/ERROR thành 4 nhóm · 2 blocker CI đã đóng (`./env/bin/bench`, `KeyError` probe). **`G0-07` vẫn PARTIAL, `P0` vẫn NO-GO, `OWNER_DECISIONS_CONFIRMED = False`.** |