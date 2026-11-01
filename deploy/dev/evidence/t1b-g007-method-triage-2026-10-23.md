# T1B — Rà 22 method FAIL/ERROR (CI run #39885152649) → triage + fix

**Ngày:** 2026-10-23 · **Phạm vi:** T1B (test-owned) · **PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) · **Branch:** `codex/p0-t1a-g001-g002-g009`
**Trạng thái PR:** `open` — **KHÔNG merge**.

---

## 1. Mã định danh bằng chứng

| Mục | Giá trị |
|---|---|
| Run **trước** (input triage) | [`#39885152649`](https://github.com/nobita6986/new-crm/actions/runs/39885152649) · head `fb55dfa` |
| Run **sau** (verify) | [`#39892127563`](https://github.com/nobita6986/new-crm/actions/runs/39892127563) · head `658780e` · PR merge `1e0fc26c` |
| Run **xác nhận** (re-run) | [`#39893839646`](https://github.com/nobita6986/new-crm/actions/runs/39893839646) · head `da62569` — **tái lập nguyên vẹn** 91 PASS / 8 FAIL |
| Run **xác nhận 2** | [`#39894784269`](https://github.com/nobita6986/new-crm/actions/runs/39894784269) · head `af83589` — **tái lập lần 3** 91 PASS / 8 FAIL |
| Run **xác nhận 3** | [`#39895414507`](https://github.com/nobita6986/new-crm/actions/runs/39895414507) · head `e3182ba` — **tái lập lần 4** 91 PASS / 8 FAIL |
| Commit sửa (T1B) | `658780e` — "T1B(G0-07): triage 22 method FAIL/ERROR" |
| `frappe_sha` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` (v15.123.1) |
| `crm_sha` | `0d3910b456ef4fb913e77b29db011b3debc4f778` (v1.87.0) |
| Artifact SHA-256 (run xác nhận 3, `#39895414507`) | Zip `p0-g007-evidence-39895414507` = `b7b8ae4982be04bf2b3949f2513870a49c88055be3990d7a6833cdc2ceaac800` (**khớp digest GitHub API**)<br>Content digest (thứ tự path + SHA-256 từng file) = `dc2d777dcd9f3007b5c17073026db2dbf6fdadac9d1a4f71779d26ba314f8b6b`<br>`g007-per-method-gate.json` = `196ba75cd27720c9226a7ed67427aba2e6efb124363fc7acc62c447649770637`<br>`http-transport.json` = `562f77d5b0aa9d8bf798d59afba176dbf77743f2f2e3c5939fb08c8ec67267f4`<br>`test_security_remediation_v2.junit.xml` = `3657a74af1d485931e10ef42196581502b0bb3906f7e863015e3f25aebe37d3b`<br>`test_bt1c5_matrix_gaps.junit.xml` = `0de37c77e352a9578ccd65d1aaecc368a24460fe1de289402b5466524d0001f1`<br>`test_docshare_lifecycle.junit.xml` = `1f3bf38baf790685c84bdb240551d230759b9592bf88d6d23ea5778a9ee65a65`<br>`test_permissions.junit.xml` = `f5894718b53b9473815b611064744cf38c1e0811044e61e2aa98b0f01b4ed660`<br>`test_security_remediation.junit.xml` = `d0078c14672c9eae39f2d30bc14f4059878425ad418f178c605c2a0956a75dc8`<br>`workflow-metadata.json` = `8be3ebb05ae1a9160376da911bce6eae6cfe5c155b36fc58aba168942ab2a6bf` |

---

## 2. Kết quả gate

| Run | PASS | FAIL | ERROR | SKIP | NOT_RUN | Tổng | `gate_passed` |
|---|---|---|---|---|---|---|---|
| #39885152649 (trước) | 77 | 12 | 10 | 0 | 0 | 99 | `false` |
| **#39892127563 (sau)** | **91** | **8** | **0** | 0 | 0 | 99 | **`false`** |
| #39893839646 (xác nhận) | 91 | 8 | 0 | 0 | 0 | 99 | `false` |
| #39894784269 (xác nhận 2) | 91 | 8 | 0 | 0 | 0 | 99 | `false` |
| #39895414507 (xác nhận 3) | 91 | 8 | 0 | 0 | 0 | 99 | `false` |

> Bốn run sau-fix (#39892127563, #39893839646, #39894784269, #39895414507) cho **cùng** kết quả 91/8/0 và **cùng** danh sách 8 method FAIL (đối chiếu tập method exact, `identical = True`). `missing_methods` / `unexpected_cases` / `duplicate_methods` / `parse_errors` / `missing_modules` / `empty_xml_files` đều `[]` ở cả bốn run. HTTP probe 14/14 PASS ở cả bốn run (cùng tập `case id`, cùng vector kết quả).

### 2.1 Xác minh độc lập run xác nhận 3 (`#39895414507`, head `e3182ba`)

Không suy luận "docs-only ⇒ kết quả y hệt" mà **đọc lại artifact từ đầu** và tự kiểm chứng ba tầng:

| Tầng kiểm chứng | Phương pháp | Kết quả |
|---|---|---|
| **A. Gate JSON** | Đọc `g007-per-method-gate.json`, tự đếm lại `results[]` | `counts={PASS:91, FAIL:8}` · tổng 99 · `gate_passed=false` — **khớp** với `counts` khai báo |
| **B. 5 JUnit XML** | Parse lại `<testcase>` bằng `ElementTree`, **không** tin gate | `bt1c5_matrix_gaps` 27 (1 FAIL) · `docshare_lifecycle` 10 (7 FAIL) · `permissions` 18 (0) · `security_remediation` 12 (0) · `security_remediation_v2` 32 (0) ⇒ **99 `<testcase>`, 91 PASS / 8 FAIL / 0 ERROR / 0 SKIP / 0 NOT RUN** |
| **C. Inventory nguồn** | AST quét `apps/crm_cskh/crm_cskh/tests/test_*.py` | **99** method `test_*` trong class `Test*` (27+10+18+12+32) — **trùng khớp 1:1** với gate |

**Đối chiếu 3 tầng:** sau khi chuẩn hoá `module.Class` → `Class`:
- Tập method của **gate** (99) ≡ tập method của **XML** (99) ⇒ `gate-only = []`, `xml-only = []`.
- **0** status disagreement giữa gate và XML (99/99 trùng khớp).
- Tập method của **gate** ≡ **inventory nguồn** (99) ⇒ `source-not-in-gate = []`, `gate-not-in-source = []` ⇒ gate **phủ đúng toàn bộ** inventory, không bỏ sót method nào.

**Sanity gate:** `expected_total=99` · `observed_total=99` · `missing_methods=[]` · `unexpected_cases=[]` · `duplicate_methods=[]` · `parse_errors=[]` · `missing_modules=[]` · `empty_xml_files=[]` · `errored_methods=[]` · `skipped_methods=[]` (`failed_methods` = 8).

**Module-status (exit code từng module):** `test_permissions` 18/18 exit=0 · `test_security_remediation` 12/12 exit=0 · `test_security_remediation_v2` 32/32 exit=0 · `test_bt1c5_matrix_gaps` 27 (exit=1) · `test_docshare_lifecycle` 10 (exit=1).

**Trạng thái theo method (module → PASS/FAIL):**

| Module | Tổng | PASS | FAIL | ERROR | SKIP |
|---|---|---|---|---|---|
| `TestPermissionBoundaries` | 18 | 18 | 0 | 0 | 0 |
| `TestSEC27CallLogFallbackContactInternal` | 4 | 4 | 0 | 0 | 0 |
| `TestSEC27CallLogFallbackContact` | 4 | 4 | 0 | 0 | 0 |
| `TestSEC26FileURLLeak` | 6 | 6 | 0 | 0 | 0 |
| `TestSEC22And23StaleAfterReassign` | 3 | 3 | 0 | 0 | 0 |
| `TestSEC16FieldExposure` | 6 | 6 | 0 | 0 | 0 |
| `TestSEC15BODAndRISKSEC12BulkReassign` | 4 | 4 | 0 | 0 | 0 |
| `TestSEC14CSRFAndSessionHTTP` | 6 | 6 | 0 | 0 | 0 |
| `TestSEC14CSRFAndSession` | 5 | 5 | 0 | 0 | 0 |
| `TestSEC13GuestTransportHTTP` | 3 | 3 | 0 | 0 | 0 |
| `TestSEC13GuestTransport` | 3 | 3 | 0 | 0 | 0 |
| `TestNT0506TeamLeaderNote` | 6 | 6 | 0 | 0 | 0 |
| `TestTK0506TeamLeaderTask` | 6 | 6 | 0 | 0 | 0 |
| `TestLS02GlobalSearchScope` | 3 | 3 | 0 | 0 | 0 |
| `TestLEAD07UnassignedLead` | 3 | 3 | 0 | 0 | 0 |
| `TestCL0607TeamLeaderCallLog` | 6 | 6 | 0 | 0 | 0 |
| `TestDocSharePolicyPinRegistry` | 1 | 1 | 0 | 0 | 0 |
| `TestDocShareBaseline` | 1 | 1 | 0 | 0 | 0 |
| `TestDocShareAssignAgent` | 3 | 1 | **2** | 0 | 0 |
| `TestDocShareEffectivePermission` | 2 | 0 | **2** | 0 | 0 |
| `TestDocShareLeadSave` | 2 | 0 | **2** | 0 | 0 |
| `TestDocShareRemoveAssignments` | 1 | 0 | **1** | 0 | 0 |
| `TestRA04CrmAdminReassignBlocked` | 3 | 2 | **1** | 0 | 0 |
| **Tổng** | **99** | **91** | **8** | **0** | **0** |

**HTTP transport probe (`http-transport.json`):** `probe_result="PASS"` · `failed_cases=[]` · **14/14** case `assertion.result == "PASS"` · `transport_error=null` ở cả 14 case.
Phạm vi case (14): `sec13-guest` · `staff-login` · `sec13-user` · `csrf-page` · `sec14-get` · `sec14-post-nocsrf` · `sec14-post-badtok` · `sec14-post-ok` · `administrator-login` · `sec13-admin` · `sec16-permlevel-get-data` · `sec16-permlevel-sentinel-scan` · `sec26-staff-private` · `sec26-admin-private`.
**Phạm vi coverage (trích nguyên văn từ artifact):** `"direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered"` — probe chỉ bắt được lỗi ở tầng HTTP trực tiếp tới `bench serve`, **không** phủ hành vi reverse-proxy/CORS.

**Định danh (Mã định danh bằng chứng):**
- `run_id` = `39895414507` (khớp `workflow-metadata.json` `run_id`).
- `head_sha` (branch commit) = `e3182ba67230e4c6e8a41d3af2b2ca85977e060e`.
- `commit_sha` trong `workflow-metadata.json` và `http-transport.json` = `1678a9738aeba4c2589ea1c0e8589a5db0ac3e09` ⇒ đây là **PR merge commit tổng hợp** (`refs/pull/2/merge`), **không** phải head branch. Đã xác minh: `gh api .../git/ref/pull/2/merge` → `sha = 1678a973…`; `compare/e3182ba...1678a973` → `ahead_by=1`, `files=[]` ⇒ **nội dung cây thư mục y hệt** head. Đây là hành vi đúng cho event `pull_request` (mỗi push sinh merge commit mới); `commit_sha` **đổi theo mỗi push** ở cả bốn run (`1e0fc26c` → `db21338e` → `ca945f30` → `1678a973`) — vì vậy **không** dùng `commit_sha` làm khoá so sánh giữa các run.
- `frappe_sha` = `d2efa79657ba5c1abf96098b3bfacc199517d57e` (v15.123.1) · `crm_sha` = `0d3910b456ef4fb913e77b29db011b3debc4f778` (v1.87.0) — **không đổi** ở cả bốn run.
- `bench_image_ref` = `frappe/bench:v5.31.0@sha256:3b75943bf00a1db92723d99ec34fbfa92f4b67b9b643ae1e96750899b7fbd980` · `bench_container_image_id` = `sha256:a096b5b275c5fff5c869093684bb7bfac292a23f862b99d53aaf5874236e390e`.
- **Tính toàn vẹn artifact:** tải qua **hai đường độc lập** (`gh run download` và `GET /actions/artifacts/{id}/zip`) ⇒ **16/16 file byte-identical**; SHA-256 zip tải lại = `b7b8ae4982be04bf2b3949f2513870a49c88055be3990d7a6833cdc2ceaac800` = **khớp chính xác** digest do GitHub API báo (`sha256:b7b8ae…`) và size 131.763 bytes.

**Workflow outcomes (run xác nhận 3):** `bootstrap=success` · `lint=success` · `seed=success` · `per_method=failure` · `per_method_gate=failure` · `http_transport=success` · `backup_restore=success` · `redaction_replacements=4`.

**Đối chiếu phạm vi code giữa các run:** `git diff --stat 658780e e3182ba -- apps/` ⇒ **rỗng** (không thay đổi code app/test); `git diff --stat 658780e e3182ba` ⇒ **chỉ 1 file** `deploy/dev/evidence/t1b-g007-method-triage-2026-10-23.md` (+208 dòng). Tuy vậy kết luận "tái lập" trong báo cáo này dựa trên **artifact đọc lại và kiểm chứng độc lập**, không dựa trên nhãn "docs-only".

**Nguyên nhân 8 FAIL (đọc trực tiếp từ `<failure>` trong XML):**
- 7 × DocShare — thông điệp `unexpectedly None : PINNED BEHAVIOR [Q2] — pending T0/Owner decision…` và `assertIsNotNone(self._share_for(self.users["c"]))` ⇒ **canary chờ Owner**, không phải hồi quy.
- 1 × RA-04 — `Lists differ: ['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users'] != []` ⇒ canary ownership-service chưa tồn tại.

⇒ 8 FAIL **giống hệt** danh sách E1–E8 ở §3.5 (canary, không phải hồi quy mới).

**HTTP transport probe:** 14/14 PASS (`probe_result: "PASS"`, `failed_cases: []`) — không đổi so với run trước.

**Sanity gate (run mới):** `expected_total=99` · `observed_total=99` · `missing_methods=[]` · `unexpected_cases=[]` · `duplicate_methods=[]` · `parse_errors=[]`.
⇒ **Không có method nào bị đổi tên, mất, hay thêm**; `subTest` vẫn không sinh duplicate.

**Workflow outcomes (run mới):** `bootstrap=success` · `lint=success` · `seed=success` · `per_method=failure` · `per_method_gate=failure` · `http_transport=success` · `backup_restore=success`.

> **G0-07 / P0: VẪN `NO-GO`.** 8 method FAIL còn lại là canary chờ Owner ⇒ gate đỏ là **đúng**. Không nâng trạng thái G0-07.

---

## 3. Phân loại 22 method FAIL/ERROR (theo chữ ký lỗi đọc từ traceback XML thô)

### 3.1. Nhóm A — Lỗi harness/context (5) — ĐÃ SỬA

| # | Method | Lỗi gốc | Nguyên nhân | Sửa |
|---|---|---|---|---|
| A1 | `test_staff_bulk_delete_cross_scope_denied` | `Lead assignments are disabled until the ownership service is available.` | Fixture `_make_lead()` tạo Lead khi session user = staff ⇒ `crm_lead.after_insert` → `assign_agent` → hook `validate_lead_assignment` chặn (fail-closed **đúng thiết kế**) | `test_security_remediation_v2.py::_make_lead` — tạo fixture dưới `Administrator`, restore user trong `finally` |
| A2 | `test_bod_calling_crm_cskh_api_endpoints_denied` | `CRM Call Log orphan not found` | Truyền `name="orphan"` — tên **không tồn tại** ⇒ `DoesNotExistError` **trước** khi tới tầng guard | Fixture `cls.orphan_call = _make_orphan_call(...)` thật |
| A3 | `test_get_attachments_redacts_file_url_for_non_system_user` | `PermissionError: Not permitted` | **Ô nhiễm trạng thái**: `test_find_file_by_url_returns_none_for_old_owner_after_reassign` (chạy **trước** theo thứ tự alphabet) đổi `lead_a.lead_owner` `a`→`b` và **không restore** | Restore `lead_owner` về `a` trong `finally` |
| A4 | `test_get_attachments_does_not_redact_non_private_file` | `PermissionError: Not permitted` | Cùng nguyên nhân A3 | Như A3 |
| A5 | `test_get_linked_docs_of_document_shape_probe_only` | `PermissionError: Not permitted` | Cùng nguyên nhân A3 | Như A3 |

### 3.2. Nhóm B — Lỗi fixture (4) — ĐÃ SỬA

| # | Method | Lỗi gốc | Nguyên nhân | Sửa |
|---|---|---|---|---|
| B1 | `test_ls02_search_widget_returns_only_own_leads_for_staff` | `'CRM-LEAD-2026-00060' not found in set()` | `search_widget` **không** lọc theo `searchfield`; filter thật dựng từ `name` / `meta.title_field` (`lead_name`) / `meta.get_search_fields()`. Fixture tìm bằng `mobile_no="091"` ⇒ **không field nào searchable chứa "091"** ⇒ list rỗng | Fixture LS-02 riêng với `first_name` = `cskh-ls02-team1` / `cskh-ls02-team2` (nằm trong `lead_name`, `search_index=1`) |
| B2 | `test_get_call_contact_orphan_routes_to_lookup_path` (v2) | `'full_name' not found in {'mobile_no': '0909876543'}` | Upstream `crm/integrations/api.py::get_contact` query child table **`Contact Phone`** (`ContactPhone.phone`), **không** query `Contact.mobile_no` ⇒ Contact thiếu child row không được match | Thêm `phone_nos: [{"phone": ..., "is_primary_mobile_no": 1}]` vào fixture Contact (cả `test_security_remediation.py` và `test_security_remediation_v2.py::_make_orphan_call`) |
| B3 | `test_upstream_get_call_contact_orphan_routes_to_phone_lookup` | idem B2 | idem B2 | idem B2 |
| B4 | `test_ls02_search_with_ignore_user_permissions_is_still_scope_bound` | `PermissionError` (ERROR) | Bản trước kỳ vọng **list rỗng**, nhưng hành vi thật là **raise** | Xem §3.4 (C1) |

### 3.3. Nhóm C — Assertion không khớp contract **đã xác minh** (5) — ĐÃ SỬA (siết chặt, không nới lỏng)

| # | Method | Kỳ vọng cũ (sai) | Contract thật đã xác minh | Sửa |
|---|---|---|---|---|
| C1 | `test_ls02_search_with_ignore_user_permissions_is_still_scope_bound` | `search_widget(...)` trả list rỗng | Frappe v15.123.1 `frappe/desk/search.py`: `ignore_permissions = doctype=="DocType" or (cint(ignore_user_permissions) and has_permission(...))`. CRM-ADMIN **không** có DocPerm read ⇒ `ignore_permissions=False` ⇒ `frappe.get_list` → `db_query.check_read_permission` → **raise `frappe.PermissionError`** | `assertRaises(frappe.PermissionError)` — **mạnh hơn** "list rỗng" (chặn hẳn request) |
| C2 | `test_get_attachments_does_not_leak_cross_scope_file_url` | `get_attachments("CRM Lead", lead_b)` == `[]` | `guarded_crm.get_attachments` gọi `_check_read(doctype, name)` **trước** khi gọi upstream ⇒ `frappe.throw("Not permitted", frappe.PermissionError)` | `assertRaises(frappe.PermissionError)` — **mạnh hơn** "list rỗng" |
| C3 | `test_get_linked_docs_of_document_returns_only_allowed` | `linked == []` | Cùng `_check_read` ⇒ raise | `assertRaises(frappe.PermissionError)` |
| C4 | `test_stale_todo_after_reassign_raises_for_old_owner` | assert user `b` (new owner) đọc được ToDo | `check_permission("read")` cho ToDo đi qua `frappe.has_permission(doctype, throw=True)` ⇒ PermissionError cho `b` (tầng doctype-permission của Frappe/CRM cho ToDo, **không** thuộc phạm vi SEC-22 = stale access của **old** owner) | Bỏ assert ngoài phạm vi; **giữ nguyên** assert old-owner phải raise |
| C5 | `test_stale_comment_after_reassign_raises_for_old_owner` | assert user `b` đọc được Comment | Idem C4 (tầng doctype-permission cho `Comment`) | Bỏ assert ngoài phạm vi; **giữ nguyên** assert old-owner phải raise |

> C4/C5 để lại **TODO T1B-ext+2** trong docstring: xác minh riêng vì sao ToDo/Comment read bị chặn cho user **đúng scope** — chưa được biến thành tiêu chí PASS.

### 3.4. Nhóm D — Hành vi code cần remediation

**KHÔNG có case nào thuộc nhóm D trong 22 method này.** Các fail-closed đã được xác minh là **đúng thiết kế** (`_check_read`, `validate_lead_assignment`, `search_widget` permission check, `get_call_log` non-system block) ⇒ **không sửa production code**.

### 3.5. Nhóm E — Chờ quyết định policy (8) — **GIỮ NGUYÊN, KHÔNG SỬA**

| # | Method | Nhóm | Lý do giữ nguyên |
|---|---|---|---|
| E1 | `test_docshare_created_by_assign_agent_when_assignee_lacks_permission` | DocShare **Q2** | `OWNER_DECISIONS_CONFIRMED = False` |
| E2 | `test_docshare_flags_after_assign_agent` | DocShare **Q2/Q4** | idem |
| E3 | `test_docshare_removed_by_lead_save_when_owner_changes` | DocShare **Q1** | idem |
| E4 | `test_docshare_survives_lead_save_when_owner_unchanged` | DocShare **Q1** | idem |
| E5 | `test_docshare_survives_remove_assignments` | DocShare **Q3** | idem |
| E6 | `test_effective_permission_before_and_after_reassign` | DocShare **Q1** | idem |
| E7 | `test_effective_permission_of_shared_child_records` | DocShare **Q1** | idem |
| E8 | `test_ra04_ownership_service_does_not_exist_yet` | **RA-04** | Chờ Owner quyết định về ownership service endpoints (ADR-004 §2.2) |

**Xác minh bằng diff:** `git diff fb55dfa 658780e -- apps/crm_cskh/crm_cskh/tests/test_docshare_lifecycle.py` ⇒ **rỗng**. `test_docshare_lifecycle.py` **không bị chạm** trong commit `658780e`. `test_bt1c5_matrix_gaps.py` chỉ sửa trong class `TestLS02GlobalSearchScope`; `TestRA04CrmAdminReassignBlocked` **không bị chạm**.

---

## 4. Điều tra `_caller` — khả năng lộ dữ liệu

### 4.1. Bằng chứng runtime (run #39885152649, `test_security_remediation_v2.log:462`)

```
AssertionError: '_caller' unexpectedly found in {
  'name': '76525f10d856', 'caller': None, 'receiver': None,
  'from': '0900000001', 'to': '0900000002', 'duration': None, ...
  'reference_doctype': 'CRM Lead', 'reference_docname': 'CRM-LEAD-2026-00057',
  'show_recording': False, '_duration': '0s', 'activity_type': 'outgoing_call',
  '_caller': {'label': None, 'image': None},
  '_receiver': {'label': 'CSKH Sec V2', 'image': None}
}
```

### 4.2. Truy vết source upstream (CRM v1.87.0, `crm_sha 0d3910b4`)

**Bước 1 — `crm/api/activities.py::get_linked_calls` (L379-473) gọi `parse_call_log`:**

```python
calls = [parse_call_log(call) for call in calls] if calls else []
return {"calls": calls, "notes": notes, "tasks": tasks}
```

⇒ Luận điểm trong docstring test cũ ("wrapper KHÔNG gọi `parse_call_log` ⇒ không có `_caller`") **SAI**.

**Bước 2 — `crm/fcrm/doctype/crm_call_log/crm_call_log.py::parse_call_log` (L158-194) gán `_caller`/`_receiver`:**

```python
elif call.get("type") == "Outgoing":
    contact = get_call_contact(call, call.get("to"))
    caller = frappe.db.get_values("User", call.get("caller"), ["full_name", "user_image"])[0] \
             if call.get("caller") else [None, None]
    call["_caller"] = {"label": caller[0], "image": caller[1]}
    call["_receiver"] = {"label": contact.get("full_name", "Unknown"), "image": contact.get("image")}
```

**Bước 3 — `get_call_contact` (L197-208) chọn nhánh theo `reference_doctype`:**

```python
doctype, name = call.get("reference_doctype"), call.get("reference_docname")
if doctype not in ("CRM Lead", "CRM Deal") or not name:
    return get_contact_by_phone_number(phone_number)     # ← NHÁNH PHONE LOOKUP
if not frappe.has_permission(doctype, "read", name):
    return {}
return get_reference_contact(doctype, name) or {}
```

**Bước 4 — `get_reference_contact` (L211-226)** với `doctype == "CRM Lead"`:
```python
lead = frappe.db.get_value("CRM Lead", name, ["lead_name", "image"], as_dict=True)
return lead and {"full_name": lead.lead_name, "image": lead.image}
```

### 4.3. Đánh giá theo từng nhánh — mức độ

| Nhánh | Nội dung lộ | Đánh giá |
|---|---|---|
| **`reference_doctype` = CRM Lead/Deal** (fixture hiện tại) | `_receiver.label` = `lead_name` / `full_name` của chính Lead/Deal trong scope; `_caller.label` = `User.full_name` của user nội bộ | **Trong scope.** `get_call_contact` đã gọi `frappe.has_permission(doctype, "read", name)` và trả `{}` nếu không có quyền ⇒ upstream **tự chặn** cross-scope ở đây. Payload quan sát được (`'CSKH Sec V2'`) = `lead_name` của `lead_a` mà caller `a` **đã** có quyền đọc. **Không có bằng chứng rò rỉ ngoài scope.** |
| **`reference_doctype` rỗng / khác Lead/Deal** | `get_contact_by_phone_number(phone_number)` → `get_contact()` (L302-380) query `Contact Phone` **không** có permission check ⇒ trả `Contact.full_name` / `Lead.lead_name` của **bất kỳ** ai khớp số điện thoại | **Đây là đường rò rỉ thật.** Đây chính là **SEC-27**. Đã được xử lý ở tầng entry point bằng override `crm_cskh.api.guarded_crm.get_call_log` (`hooks.py:191` map `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log`): non-system + `reference_doctype not in ("CRM Lead","CRM Deal")` ⇒ `frappe.throw(..., frappe.PermissionError)`. |

### 4.4. Kết luận về `_caller` (nêu rõ mức độ, **không** tự kết luận "an toàn")

1. **Assertion cũ sai về mặt contract** — `_caller`/`_receiver` là **derived field** do upstream `parse_call_log` tự tính, **không phải** field `permlevel` của doctype. Method tên `..._does_not_leak_permlevel_field` vì vậy **không đo đúng thứ nó tên** — nhưng **tên method giữ nguyên** để không phá inventory 99 method của gate G0-07.
2. **Với `reference_doctype` = Lead/Deal**: KHÔNG quan sát thấy rò rỉ ngoài scope; upstream có `has_permission` gate riêng. **Mức độ: không phát hiện lỗ hổng.** Nhưng đây là kết luận cho **fixture hiện tại**, không phải phát hiện đầy đủ.
3. **Nhánh phone-lookup (`reference_doctype` rỗng/khác)**: đường rò rỉ **thật**, đã được `guarded_crm.get_call_log` chặn ở tầng entry point (SEC-27).
4. **Khoảng trống còn lại (CHƯA đóng, cần Owner/T1C quyết):** `guarded_crm.get_linked_calls` **không** có guard tương tự `get_call_log`. Nó chỉ `_check_read("CRM Lead", name)` rồi delegate thẳng `crm.api.activities.get_linked_calls`. Vì `get_linked_calls` lấy calls qua `frappe.db.get_all("CRM Call Log", filters={"reference_docname": name})` — **lọc theo `reference_docname`**, tức mọi Call Log tham chiếu Lead đó — nên `reference_doctype` **luôn** là Lead ⇒ **hiện chưa** đi qua nhánh phone-lookup.
   - **NHƯNG** nhánh **Dynamic Link** (`crm/api/activities.py:402-404`) lấy `frappe.db.get_all("Dynamic Link", filters={"link_name": name, "parenttype": "CRM Call Log"}, pluck="parent")` ⇒ các Call Log **không** có `reference_docname` nhưng có Dynamic Link trỏ tới Lead cũng được nối vào response. Query này select `CallLog.reference_doctype` từ chính row của Call Log (L428) — **không** suy ra `reference_doctype` từ Dynamic Link.
   - **Bằng chứng tĩnh cho thấy shape này khả thi:** `CRMCallLog.link_with_reference_doc(reference_doctype, reference_name)` (`crm/fcrm/doctype/crm_call_log/crm_call_log.py:135-139`) **chỉ** append vào child table `links`:
     ```python
     def link_with_reference_doc(self, reference_doctype, reference_name):
         if self.has_link(reference_doctype, reference_name):
             return
         self.append("links", {"link_doctype": reference_doctype, "link_name": reference_name})
     ```
     ⇒ **không** gán `reference_doctype` / `reference_docname`. Các call site upstream: `crm/integrations/api.py:94` (`FCRM Note`), `:134` (`CRM Task`), `crm_call_log.py:340` (`CRM Lead`, trong `create_lead_from_call_log` — call site này **có** set `reference_docname` qua `reference_doctype`/`reference_docname` của Lead). Do đó một Call Log có `links` trỏ Lead nhưng `reference_doctype` **rỗng** là shape **không bị upstream loại trừ** ⇒ `get_call_contact` đi nhánh `get_contact_by_phone_number` ⇒ `_caller`/`_receiver` lộ Contact/Lead ngoài scope.
   - **Mức độ: CHƯA XÁC MINH bằng runtime.** Cần một test riêng tạo Call Log có Dynamic Link trỏ Lead nhưng `reference_doctype` rỗng, rồi gọi `guarded_crm.get_linked_calls` bằng non-system user và quan sát `_receiver.label`. Đây là **hạng mục T1B-ext+1** (chưa có owner authorization để thêm method mới vào inventory 99 — cần Owner duyệt).
   - **Khuyến nghị (chưa thực hiện):** cân nhắc guard trong `guarded_crm.get_linked_calls` tương tự `get_call_log` — chặn/loại bỏ call có `reference_doctype` không thuộc Lead/Deal khi caller non-system. **Không sửa production code trong vòng này** vì chưa có bằng chứng runtime và nằm ngoài tiêu chí "lỗi production đã chứng minh".
5. **`_caller`/`_receiver` không phải `permlevel` field** ⇒ không có `permlevel` nào bị lộ trong payload quan sát. Schema `CRM Call Log` (v1.87.0) không có field nào `permlevel > 0`.

### 4.5. Assertion mới của `test_get_linked_calls_does_not_leak_permlevel_field` (siết chặt, có allowlist hữu hạn ⇒ không vacuous)

- **Positive control:** assert `calls` **không rỗng** (nếu response rỗng mọi assert sau vô nghĩa) — kèm message chỉ rõ fixture.
- **Pin shape upstream:** assert `_caller`/`_receiver` **có mặt** (phát hiện drift khi upstream đổi shape).
- **Allowlist field (hữu hạn ⇒ không vacuous):** `assertLessEqual(set(call.keys()), upstream_allowlist)` — bắt được trường hợp upstream thêm field mới (ví dụ field `permlevel`) vào `fields=[...]`.
- **Shape con:** `_caller`/`_receiver` chỉ chứa `{"label", "image"}`.

---

## 5. Thay đổi trong commit `658780e`

| File | Nội dung |
|---|---|
| `apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py` | A1, A2, A3–A5, B2/B3, C1–C5, §4.5 |
| `apps/crm_cskh/crm_cskh/tests/test_bt1c5_matrix_gaps.py` | B1, B4/C1 (chỉ class `TestLS02GlobalSearchScope`) |
| `apps/crm_cskh/crm_cskh/tests/test_security_remediation.py` | B2 |

**Không chạm:** `test_docshare_lifecycle.py` (toàn bộ), `TestRA04CrmAdminReassignBlocked`, upstream CRM/Frappe, production code `crm_cskh/api/guarded_crm.py`, tài liệu T1A/T1C/Owner.

**Lint/format:** `ruff check` → *All checks passed!* · `ruff format` → *6 files already formatted*.
**Inventory:** xác minh bằng AST — 5 module, **99/99 method**, khác 0 so với `g007_test_inventory.json` (`expected_total=99`).

---

## 6. Blocker còn lại

1. **DocShare Q1–Q4 (7 method)** + **RA-04 (1 method)** — **chờ Owner quyết định policy**. Không sửa, không đổi assertion, không merge.
2. **T1B-ext+1** — nhánh **Dynamic Link** trong `get_linked_calls` có thể đưa Call Log `reference_doctype` rỗng vào nhánh phone-lookup của `get_call_contact` ⇒ nghi vấn rò rỉ Contact ngoài scope. **Chưa có runtime evidence.** Cần (a) Owner duyệt thêm method vào inventory 99, hoặc (b) tách thành PR remediation riêng.
3. **T1B-ext+2** — vì sao `check_permission("read")` cho `ToDo`/`Comment` lại chặn user **đúng scope** (tầng doctype-permission của Frappe). Chưa biến thành tiêu chí PASS.
4. **G0-07 / P0:** vẫn `NO-GO` — 8/99 method FAIL ⇒ `gate_passed=false`.
5. **PR #2:** vẫn `open`, `merged=false`, `mergeable=true`, `mergeStateStatus=UNSTABLE` — **không merge** theo yêu cầu Owner. Bốn run liên tiếp đã xác nhận 91/99; vấn đề còn lại **không** là flakiness mà là 8 canary chờ quyết định policy.
