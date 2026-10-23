# T1B — Rà từng method FAIL/ERROR (run mới nhất `#39976477275`)

**Ngày:** 2026-10-23 · **Phạm vi:** T1B (test-owned) · **PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) — `open`, **KHÔNG merge**
**Run mới nhất:** [`#39976477275`](https://github.com/nobita6986/new-crm/actions/runs/39976477275) — event `pull_request`, head `138f6677006c978eee0823033c4e8a9634cf6435`, conclusion `failure`. Đây là run **mới nhất** của PR #2 tại thời điểm kiểm tra.
**Artifact:** `p0-g007-evidence-39976477275` · artifact **ID `12512295421`** · 131.623 bytes · SHA-256 `f17dd94fc5b67929027db3769831e99589768af4542d86295c5e3ab8cf83c6e0` — **khớp chính xác** digest GitHub API (`digest=sha256:f17dd94f…`, tải zip qua `gh api` và băm lại cục bộ)
**Merge commit SHA:** `08170e954f63af209f393e066cd85cfff4329844` (= `commit_sha` trong `http-transport.json`; khớp `refs/pull/2/merge` đọc trực tiếp từ GitHub API)
**Nguồn đọc:** `g007-per-method-gate.json` · 5 file `*.junit.xml` · `test_docshare_lifecycle.log` · `test_bt1c5_matrix_gaps.junit.xml` · `http/http-transport.json` · `workflow-metadata.json` — tất cả thuộc artifact của run `#39976477275`

> **Tương thích ngược:** run trước [`#39916992699`](https://github.com/nobita6986/new-crm/actions/runs/39916992699) (head `85fbf7c`, merge `1c91b860`) cho **cùng** kết quả 91/8/0 và **cùng** tập 8 method FAIL ⇒ **không** regression. Đối chiếu tập FAIL hai run: `identical = True`, `new = []`, `gone = []` (§0.1).

> **Lưu ý về nguồn SHA:** các đường dẫn dòng cho **Frappe** (`d2efa796…`) trích từ file tải trực tiếp từ GitHub API tại SHA đã ghim trong lần rà này. Tài liệu gốc của `test_docshare_lifecycle.py` header ghi `assign_to.py L104-118` / `share.py L47-83` — **đã đối chiếu và khớp** với nội dung tại SHA này (`_add` L104-118; `add_docshare` L47-83). Các đường dẫn dòng cho **CRM v1.87.0** (`0d3910b4…`) trích từ bản copy trong `deploy/dev/evidence/logs/upstream-crm-v1.87.0/` đã có sẵn từ lần trước.
>
> **Lưu ý về bản copy CRM trong repo:** file `deploy/dev/evidence/logs/upstream-crm-v1.87.0/crm_api_activities.py` được lưu ở **UTF-16 + CRLF** (28.100 bytes) — **nội dung sau khi chuẩn hóa (`\n`, bỏ newline cuối) giống hệt** bản tải mới tại SHA `0d3910b4…` (SHA-256 chuẩn hóa `9f348b6dc42366cf6b8e2439b08c44a62ad8df53633b921fbbf0e398a4aaadb3`). Khi đọc file này bằng `Read`/script, **phải** decode UTF-16 và chuẩn hóa EOL, nếu không sẽ ra hash khác và dễ tưởng là khác source.

---

## 0. Xác minh counts từ artifact của run `#39976477275`

**Không** suy ra từ commit message, nhãn "docs-only", hay `conclusion` tổng quát của run. Ba tầng đọc độc lập, parse trực tiếp file trong artifact:

| Tầng | Phương pháp | Kết quả |
|---|---|---|
| **A. Gate JSON** | Đọc `g007-per-method-gate.json`, tự đếm lại `results[]` | `counts(declared)={PASS:91, FAIL:8}`; `counts(recomputed)={PASS:91, FAIL:8}`; tổng 99; `passing_total=91`; `gate_passed=false` |
| **B. 5 JUnit XML** | Parse lại `<testcase>` bằng `ElementTree`, **không** tin gate | `bt1c5_matrix_gaps` 27 (1 FAIL) · `docshare_lifecycle` 10 (7 FAIL) · `permissions` 18 (0) · `security_remediation` 12 (0) · `security_remediation_v2` 32 (0) ⇒ **99 `<testcase>`**; sum thuộc tính `<testsuite>` = `tests=99, failures=8, errors=0, skipped=0` |
| **C. Inventory nguồn** | AST quét `apps/crm_cskh/crm_cskh/tests/test_*.py` | **99** method `test_*` trong class `Test*` |

**Đối chiếu tập method 1:1 (không chỉ đếm):**

| So sánh | Kết quả |
|---|---|
| AST (99) vs Gate `results[]` (99) | `EXACT 1:1 MATCH: True` · `AST-only=[]` · `Gate-only=[]` |
| XML `<testcase>` (99) vs Gate (99) | `unique(classname::name)=99` ⇒ **không** trùng tên, **không** thiếu |
| Gate vs XML theo từng status | **0** status disagreement |
| Tất cả case có status | `PASS=91`, `FAIL=8`, `ERROR=0`, `SKIP=0` (đếm per-`<testcase>`) |
| Gate hygiene | `missing_modules=[]` · `empty_xml_files=[]` · `missing_methods=[]` · `unexpected_cases=[]` · `duplicate_methods=[]` · `parse_errors=[]` · `errored_methods=[]` · `skipped_methods=[]` |
| `xml_module_binding` | Cả 5 XML đều map đúng module (`bt1c5_matrix_gaps`→`…test_bt1c5_matrix_gaps`, `docshare_lifecycle`→`…test_docshare_lifecycle`, `permissions`→`…test_permissions`, `security_remediation`→`…test_security_remediation`, `security_remediation_v2`→`…test_security_remediation_v2`) |

**Counts thực tế (từ artifact, cả 3 tầng khớp nhau):**

> **PASS = 91 · FAIL = 8 · ERROR = 0 · SKIP = 0 · NOT RUN = 0 · Tổng = 99**
> Gate JSON: `expected_total=99` · `observed_total=99` · `passing_total=91` · `gate_passed=false`
> Sanity: `missing_methods=[]` · `unexpected_cases=[]` · `duplicate_methods=[]` · `parse_errors=[]` · `missing_modules=[]` · `empty_xml_files=[]` · `errored_methods=[]` · `skipped_methods=[]` · `failed_methods=8`

**Đối chiếu 3 tầng:** `gate == xml` (99 ≡ 99, `gate-only=[]`, `xml-only=[]`) · `gate == source` (99 ≡ 99, `source-only=[]`) · **0** status disagreement giữa gate và XML.

**Danh sách 8 FAIL (lấy từ raw XML — ground truth, không qua gate):**

1. `TestDocShareAssignAgent.test_docshare_created_by_assign_agent_when_assignee_lacks_permission`
2. `TestDocShareAssignAgent.test_docshare_flags_after_assign_agent`
3. `TestDocShareLeadSave.test_docshare_removed_by_lead_save_when_owner_changes`
4. `TestDocShareLeadSave.test_docshare_survives_lead_save_when_owner_unchanged`
5. `TestDocShareRemoveAssignments.test_docshare_survives_remove_assignments`
6. `TestDocShareEffectivePermission.test_effective_permission_before_and_after_reassign`
7. `TestDocShareEffectivePermission.test_effective_permission_of_shared_child_records`
8. `TestRA04CrmAdminReassignBlocked.test_ra04_ownership_service_does_not_exist_yet`

**Module-status (exit code từng module):** `test_permissions` 18/18 exit=0 · `test_security_remediation` 12/12 exit=0 · `test_security_remediation_v2` 32/32 exit=0 · `test_bt1c5_matrix_gaps` 27 (exit=1) · `test_docshare_lifecycle` 10 (exit=1).

**HTTP transport probe:** `probe_result="PASS"` · `failed_cases=[]` · **14/14** case `assertion.result == "PASS"` · `transport_error` khác null: **0**.
**Phạm vi coverage (nguyên văn từ artifact):** `"direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered"`.

**Định danh bằng chứng:**

| Trường | Giá trị |
|---|---|
| `run_id` | `39976477275` (khớp `workflow-metadata.json`) |
| Event | `pull_request` |
| `head_sha` (branch) | `138f6677006c978eee0823033c4e8a9634cf6435` |
| **Merge commit SHA** | `08170e954f63af209f393e066cd85cfff4329844` — đây là `commit_sha` ghi trong `workflow-metadata.json` **và** `http/http-transport.json`, tức **PR synthetic merge commit** (`refs/pull/2/merge`), **không phải** head branch. Xác minh `refs/pull/2/merge` → `08170e95…` |
| `frappe_sha` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` (v15.123.1) |
| `crm_sha` | `0d3910b456ef4fb913e77b29db011b3debc4f778` (v1.87.0) |
| Artifact name | `p0-g007-evidence-39976477275` |
| **Artifact ID** | `12512295421` |
| **Artifact zip SHA-256** | `f17dd94fc5b67929027db3769831e99589768af4542d86295c5e3ab8cf83c6e0` — **khớp chính xác** digest GitHub API (`digest=sha256:f17dd94f…`); size 131.623 bytes cũng khớp |
| `g007-per-method-gate.json` SHA-256 | `196ba75cd27720c9226a7ed67427aba2e6efb124363fc7acc62c447649770637` |
| `http/http-transport.json` SHA-256 | `0771db2891f8368e32af3923d0386d4651036ea3332b89ea9b4b269b2058f40c` |
| `test_docshare_lifecycle.log` SHA-256 | `f7b089fdb54d0ebfca74ef026222fb3719ccbeac1d915d0d6c17860257b67349` |
| `test_bt1c5_matrix_gaps.junit.xml` SHA-256 | `ac9a5a375b4e41ced6643642e7f846fcca7b17ba35e731106bdb119f2cca9beb` |
| `test_permissions.junit.xml` SHA-256 | `093e71d60088a361a47fd016939feb3d8a1128dff8d60fe6dff08b410314aa64` |
| `test_security_remediation_v2.junit.xml` SHA-256 | `812b1e1e7ab52acf33e1ad55b467955e1becd1938a34383b375d708a99fd6d5f` |
| `workflow-metadata.json` SHA-256 | `a353048e2f2294f36387a5ace40a5e15e25ad355842d0deaaec78b6835d2bbb7` |

**Workflow outcomes:** `bootstrap=success` · `lint=success` · `seed=success` · `per_method=failure` · `per_method_gate=failure` · `http_transport=success` · `backup_restore=success` · `redaction_replacements=4`.

**Ghi chú về tính xác định của gate JSON:** `g007-per-method-gate.json` của run này **byte-identical** với các run `#39895414507`, `#39900046839`, `#39906441722`, `#39907255611` và `#39916992699` (`196ba75c…`). File này **không** chứa commit SHA hay timestamp, và `results[]` chỉ ghi `module` / `class` / `method` / `status` / `source_xml` / `raw_name` / `match_reason` ⇒ khi test code không đổi, byte-identical là **kết quả mong đợi**, không phải dấu hiệu artifact bị cache lại. Đây vẫn là bằng chứng **độc lập** vì zip artifact của run này có SHA-256 **khác** (`f17dd94f…` ≠ `7314993c…`/`62acc51e…`/`21831855…`/`d394ae92…`), và **mọi** file XML/log đều có SHA-256 **khác** run trước.

> **Kết luận:** run `#39976477275` **tái lập** 91 PASS / 8 FAIL / 0 ERROR / 0 SKIP, HTTP 14/14 — **đã xác minh từ artifact**, không phải suy luận từ commit message hay trạng thái tổng quát của run.

### 0.1 So sánh tập FAIL giữa hai run liên tiếp — không regression

Đối chiếu máy (so tập `failed_methods` trong `g007-per-method-gate.json` của hai run):

| So sánh | Kết quả |
|---|---|
| `prev` = run `#39916992699` (head `85fbf7c`) | 8 method FAIL |
| `cur` = run `#39976477275` (head `138f667`) | 8 method FAIL |
| Tập FAIL **giống hệt**? | **`True`** |
| Method FAIL **mới** xuất hiện | `[]` (không) |
| Method FAIL **biến mất** | `[]` (không) |

⇒ `138f667` (commit chỉ bổ sung tài liệu RFC) **không** làm thay đổi hành vi test. Cả 8 FAIL đều là **canary PIN** chờ quyết định Owner (§1) — **không** phải hồi quy mới.

**Nguyên nhân 8 FAIL (trích nguyên văn `<failure>` từ raw XML của run này):**

| # | Method | Lỗi | Nguyên nhân |
|---|---|---|---|
| 1 | `TestDocShareAssignAgent.test_docshare_created_by_assign_agent_when_assignee_lacks_permission` | `AssertionError: unexpectedly None : PINNED BEHAVIOR [Q2] …` | Canary Q2 — chờ Owner |
| 2 | `TestDocShareAssignAgent.test_docshare_flags_after_assign_agent` | `AssertionError: unexpectedly None : PINNED BEHAVIOR [Q2] …` | Canary Q2 — chờ Owner |
| 3 | `TestDocShareLeadSave.test_docshare_removed_by_lead_save_when_owner_changes` | `AssertionError: unexpectedly None` (L627) | **Tiền đề** `assertIsNotNone(self._share_for(c))` — dựa trên Q2 |
| 4 | `TestDocShareLeadSave.test_docshare_survives_lead_save_when_owner_unchanged` | `AssertionError: unexpectedly None` (L653) | **Tiền đề** — dựa trên Q2 |
| 5 | `TestDocShareRemoveAssignments.test_docshare_survives_remove_assignments` | `AssertionError: unexpectedly None` (L697) | **Tiền đề** — dựa trên Q2 |
| 6 | `TestDocShareEffectivePermission.test_effective_permission_before_and_after_reassign` | `AssertionError: unexpectedly None` (L768) | **Tiền đề** — dựa trên Q2 |
| 7 | `TestDocShareEffectivePermission.test_effective_permission_of_shared_child_records` | `AssertionError: unexpectedly None` (L819) | **Tiền đề** — dựa trên Q2 |
| 8 | `TestRA04CrmAdminReassignBlocked.test_ra04_ownership_service_does_not_exist_yet` | `AssertionError: Lists differ: ['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users'] != []` | Canary RA-04 — chờ ADR (§1.3) |

### 0.2 Chuỗi run đã xác minh

| Run | Head | Merge commit (`commit_sha` trong artifact) | Kết quả | HTTP |
|---|---|---|---|---|
| `#39885152649` | `fb55dfa` | — | 77 PASS / 12 FAIL / 10 ERROR | 14/14 |
| `#39892127563` | `658780e` | `1e0fc26c` | 91 / 8 / 0 | 14/14 |
| `#39893839646` | `da62569` | `db21338e` | 91 / 8 / 0 | 14/14 |
| `#39894784269` | `af83589` | `ca945f30` | 91 / 8 / 0 | 14/14 |
| `#39895414507` | `e3182ba` | `1678a973` | 91 / 8 / 0 | 14/14 |
| `#39900046839` | `8613e86` | `8dfa1b7f` | 91 / 8 / 0 | 14/14 |
| `#39906441722` | `7ea105c` | `0119c3ff` | 91 / 8 / 0 | 14/14 |
| `#39907255611` | `7905195` | `e87b65e1` | 91 / 8 / 0 | 14/14 |
| `#39916992699` | `85fbf7c` | `1c91b860` | 91 / 8 / 0 | 14/14 |
| **`#39976477275`** | **`138f667`** | **`08170e95`** | **91 / 8 / 0** | **14/14** |

⇒ **9 run liên tiếp** sau-fix đều cho cùng 91/8/0 và HTTP 14/14.

---

## 1. Bảng 8 method FAIL — phân loại và quyết định Owner cần có

| # | Method | Nhóm | Assertion đang FAIL | Dạng canary | Quyết định Owner cần để thành regression test | Owner |
|---|---|---|---|---|---|---|
| 1 | `TestDocShareAssignAgent::test_docshare_created_by_assign_agent_when_assignee_lacks_permission` | **Q2** | `assert_pinned_is_not_none(share, "Q2", …)` | PIN — chờ policy | **Q2:** `assign_agent` có được tự tạo `DocShare read=1` cho assignee **ngoài scope** không, khi `crm_cskh` chủ động chặn scope? | T0/Owner |
| 2 | `TestDocShareAssignAgent::test_docshare_flags_after_assign_agent` | **Q2 + Q4** | `assert_pinned_is_not_none(share, "Q2", …)` (dừng trước khi tới assert Q4) | PIN — chờ policy | **Q2** (như trên) **và** **Q4:** có cần path nâng quyền share (`write`/`share`) cho `crm_cskh` không? | T0/Owner |
| 3 | `TestDocShareLeadSave::test_docshare_removed_by_lead_save_when_owner_changes` | **Q1** | `assertIsNotNone(self._share_for(c))` (tiền đề thiết lập) | PIN — chờ policy | **Q1:** khi `lead_owner` đổi, `DocShare` cũ nên bị xoá **hết** hay giữ lại share đã cấp hợp lệ? | T0/Owner |
| 4 | `TestDocShareLeadSave::test_docshare_survives_lead_save_when_owner_unchanged` | **Q1** | `assertIsNotNone(self._share_for(c))` (tiền đề thiết lập) | PIN — chờ policy | **Q1** — nhánh bù (không đổi owner ⇒ share giữ nguyên). Phụ thuộc Q1 | T0/Owner |
| 5 | `TestDocShareRemoveAssignments::test_docshare_survives_remove_assignments` | **Q3** | `assertIsNotNone(self._share_for(c))` (tiền đề thiết lập) | PIN — chờ policy | **Q3:** `remove_assignments` hủy `ToDo` nhưng không gỡ `DocShare` — assignee cũ còn đọc được Lead không? | T0/Owner |
| 6 | `TestDocShareEffectivePermission::test_effective_permission_before_and_after_reassign` | **Q1** | `assertIsNotNone(self._share_for(c))` (tiền đề thiết lập) | PIN — chờ policy | **Q1** — phụ thuộc bước thiết lập Q2 | T0/Owner |
| 7 | `TestDocShareEffectivePermission::test_effective_permission_of_shared_child_records` | **Q1** | `assertIsNotNone(self._share_for(c))` (tiền đề thiết lập) | PIN — chờ policy | **Q1** — phụ thuộc bước thiết lập Q2 | T0/Owner |
| 8 | `TestRA04CrmAdminReassignBlocked::test_ra04_ownership_service_does_not_exist_yet` | **RA-04** | `assertEqual(reassign_routes, [])` — `reassign_routes` = `['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users']` | PIN — chờ ADR | **RA-04:** có tạo ownership service (`reassign_lead` / `transfer_lead_ownership` + endpoint đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction theo ADR-004 §2.2) không? | T0/Owner |

### 1.1 Trạng thái canary — **giữ nguyên, không sửa**

- `OWNER_DECISIONS_CONFIRMED = False` (test_docshare_lifecycle.py L154) — **giữ nguyên**.
- Cả 4 mục `PINNED_BEHAVIOR["Q1..Q4"]["status"]` = `"PENDING_T0_OWNER_DECISION"` — **giữ nguyên**.
- **Không** sửa assertion, **không** thay bằng `xfail`/`skip`, **không** nới lỏng, **không** chuyển thành lỗi bảo mật đã xác nhận.

**Cơ chế tự bảo vệ đã có sẵn (không cần sửa):** mọi assert (PIN) đi qua helper `assert_pinned_*` (L300-352) với message ghi rõ `PINNED BEHAVIOR [Qn] — pending T0/Owner decision — … Assert này PIN hành vi hiện trạng để phát hiện drift; KHÔNG phải tiêu chí PASS chính sách.` Ngoài ra `TestDocSharePolicyPinRegistry::test_policy_pin_registry_is_consistent` (**PASS** trong run này) kiểm tra registry `pinned_by` khớp code thực tế.

**Điều kiện chuyển thành regression test** (theo L146-153): Owner ghi quyết định vào `t1b-g007-runtime-evidence-2026-10-21.md` §8.4 **kèm ngày + người quyết + nội dung**; mọi mục `PINNED_BEHAVIOR` phải có `status = "DECIDED"` và đủ `decision` / `decided_by` / `decided_on`; chuyển `OWNER_DECISIONS_CONFIRMED = True`. **Thứ tự phụ thuộc:** Q2 phải quyết trước Q1/Q3 vì 4/7 canary dừng ở tiền đề thiết lập theo Q2 (§1.2).

### 1.2 Quan sát runtime: 7 canary có cùng một tiền đề thiết lập chưa đạt

Bảng FAIL ở §1 cho thấy 2 canary FAIL ngay tại assert (PIN) Q2, còn **5 canary FAIL ở tiền đề thiết lập** `assertIsNotNone(self._share_for(c))` (L627, L653, L697, L768, L819). Cả 7 đều dùng chung một bước thiết lập: `self._assign(self.users["c"])`.

#### 1.2.1 QUAN SÁT (runtime evidence — dẫn nguồn đầy đủ)

**Nguồn:** artifact của run [`#39976477275`](https://github.com/nobita6986/new-crm/actions/runs/39976477275) (head `138f667`, merge `08170e95`), artifact ID `12512295421`, file `p0-g007-evidence-39976477275/test_docshare_lifecycle.log`, SHA-256 `f7b089fdb54d0ebfca74ef026222fb3719ccbeac1d915d0d6c17860257b67349`.

Ba dòng do helper `observe()` in ra (test_docshare_lifecycle.py L293-298):

```
[DocShare characterization] after assign_agent (assignee ngoài scope) — DocShare: None
[DocShare characterization] after assign_agent — cờ quyền DocShare: None
[DocShare characterization] after assign_agent (assignee trong scope) — DocShare: []
```

**Điều quan sát được:** sau `frappe.desk.form.assign_to.add()` với assignee user `c` (Team 2, **ngoài scope**), `_share_for(c)` trả `None` — tức **không tồn tại bản ghi `DocShare` nào** cho user `c` trên Lead đó. Dòng thứ ba cho thấy nhánh "assignee trong scope" trả `[]` (đúng kỳ vọng của Q2 nhánh bù, method này **PASS**).

**Đối chiếu với pin:** `PINNED_BEHAVIOR["Q2"]["current_behavior"]` ghi *"tạo `DocShare` khi assignee chưa có quyền"* (registry in ra trong cùng log). Quan sát `None` **không khớp** với mô tả đó ⇒ đây là **drift giữa pin và hành vi quan sát được**.

> **Phạm vi của quan sát:** log chỉ ghi **kết quả** (`DocShare: None`), **không** ghi giá trị `frappe.has_permission(...)` hay `lead_owner` tại các thời điểm trước/sau. Vì vậy quan sát **không** tự chứng minh được *vì sao* không có share. Việc suy luận cơ chế là mục §1.2.2 và **mang nhãn giả thuyết**.

**Tính lặp lại:** quan sát `DocShare: None` xuất hiện **giống hệt** trong **cả 10 run** đã tải (`#39885152649`, `#39892127563`, `#39893839646`, `#39894784269`, `#39895414507`, `#39900046839`, `#39906441722`, `#39907255611`, `#39916992699`, `#39976477275`) ⇒ hiện tượng **tái lập xuyên suốt**, không phải nhiễu của một lần chạy.

#### 1.2.2 GIẢ THUYẾT cơ chế (static analysis — **CHƯA** xác minh bằng runtime)

> **Nhãn: GIẢ THUYẾT.** Dựa trên đọc source tại SHA đã ghim, **không** có runtime confirm. Không được trích dẫn như kết luận.

**Đường gọi và điểm chặn (tĩnh):**

1. `test._assign()` (L446) → `frappe.desk.form.assign_to.add({...})` → `_add(args, ignore_permissions=False)` (`assign_to.py` L55-58).
2. ToDo insert (`assign_to.py` L85-98) với `ignore_permissions=True`.
3. `crm.api.todo.after_insert` (`crm/api/todo.py` L19-25) — hook upstream `doc_events["ToDo"]["after_insert"]` (`crm/hooks.py` L244-248) — chạy **ngay sau** ToDo insert, gọi `frappe.db.set_value("CRM Lead", reference_name, "lead_owner", doc.allocated_to, update_modified=False)`.
4. `assign_to.py` L106-107: `if not frappe.has_permission(doc=doc, user=assign_to):` → nhánh này **không** chạy ⇒ `has_permission("CRM Lead", doc=lead, user="c")` đã trả `True` tại thời điểm đó.

**Ứng viên giải thích (chưa loại trừ được ứng viên nào):**

- **(H1) `lead_owner` bị ghi đè làm `c` trở thành owner.** `crm/api/todo.py` L22-25 đặt `lead_owner = allocated_to`. Nếu `lead_owner` thành `c` thì `_user_owns_in_scope("c", "c")` (`crm_cskh/permissions.py` L39-55) trả `True` ⇒ đọc được, **không cần** share. *Lưu ý phản chứng:* `frappe.db.set_value` **không** gọi Document events (docstring `frappe/database/database.py` L942-945: *"do not call the ORM triggers"*) ⇒ hook `on_update → clear_stale_lead_shares` (`hooks.py` L172) **không** chạy ⇒ giả thuyết này **tương thích** với việc không có share nào bị xoá.
- **(H2) Nhánh "gán việc" của upstream CRM cho phép đọc.** `crm/hooks.py` L135-139 đăng ký `permission_query_conditions` → `crm.permissions.org_hierarchy._permission_query_conditions` (L42-51) có nhánh `q2 = DT.name.isin(... ToDo ... allocated_to == user ...)` ⇒ **record được gán ToDo cho `c` là đọc được** với `c`, **bất kể** `lead_owner`.
- **(H3) Tương tác hai hệ scope.** `has_controller_permissions` (`frappe/permissions.py` L455-473) chạy hook `has_permission` **trước** share fallback; `crm_cskh/permissions.py` `lead_has_permission` (L99-107) và `crm.permissions.org_hierarchy.has_lead_permission` (L98-99 → `_has_permission` L73-95) đều có thể trả về giá trị cho phép.

> **Cả H1, H2, H3 đều chưa được phân biệt.** Quan sát `DocShare: None` tương thích với cả ba. **Không** kết luận cơ chế nào là nguyên nhân.

#### 1.2.3 Khoảng trống cần Owner/T0 xem xét (nếu bất kỳ giả thuyết nào đúng)

Nếu `assign_agent` khiến assignee **ngoài scope** trở thành **có quyền đọc** record mà **không** thông qua `DocShare`, thì câu hỏi Q2 cần được trả lời **khác** so với giả định trong pin (pin giả định cơ chế là *share `read=1`*; runtime thì **không** có share). Đây là **quyết định policy của Owner**, không phải sửa test.

**Vì sao vẫn giữ nguyên canary:** chưa có bằng chứng runtime phân biệt được H1/H2/H3 ⇒ **chưa đủ cơ sở** để kết luận có hoặc không có vấn đề scope. Xem `T1B-ext+3` (§4) cho hạng mục theo dõi.

### 1.3 Method RA-04 — chi tiết

**Nguồn:** run `#39976477275`, artifact ID `12512295421`, `test_bt1c5_matrix_gaps.junit.xml` (SHA-256 `ac9a5a375b4e41ced6643642e7f846fcca7b17ba35e731106bdb119f2cca9beb`), testcase `TestRA04CrmAdminReassignBlocked::test_ra04_ownership_service_does_not_exist_yet` (traceback tại dòng 679).

**Assertion:** `test_bt1c5_matrix_gaps.py` L679-684 — `assertEqual(reassign_routes, [])`, trong đó `reassign_routes` (L674-678) lọc key của `frappe.get_hooks("override_whitelisted_methods")` theo token substring `reassign` / `transfer_ownership` / `assign`.

**Traceback thực tế (nguyên văn từ `<failure>`):**

```
AssertionError: Lists differ: ['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users'] != []

First list contains 2 additional elements.
First extra element 0:
'crm.api.doc.remove_assignments'

- ['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users']
+ [] : Không được đăng ký endpoint reassign/transfer khi ownership service chưa có
      (nếu có, phải xem lại guard theo ADR-004 §2.2).
```

⇒ **`reassign_routes` thực tế = `['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users']`** — **2 phần tử**.

**Đánh giá từng route: có thực hiện ownership service, hay chỉ liên quan tới assignment?**

Đọc source của **cả hai route** (không suy từ tên):

**Route 1 — `crm.api.doc.remove_assignments`** (key override trong `override_whitelisted_methods`)

| Mặt | Source | Nội dung |
|---|---|---|
| Handler của `crm_cskh` | `apps/crm_cskh/crm_cskh/permissions.py` L165-170 | `if not _is_system(frappe.session.user): frappe.throw(..., frappe.PermissionError)` — **fail-closed**, chặn non-system. Sau đó delegate `frappe.get_attr("crm.api.doc.remove_assignments")(doctype, name, assignees, ignore_permissions=False)` |
| Handler upstream | CRM v1.87.0 `crm/api/doc.py` L590-604 (SHA `0d3910b4…`) | `for assign_to in assignees: set_status(doctype, name, todo=None, assign_to=assign_to, status="Cancelled", ...)` |
| Có đồng bộ `lead_owner`? | **Không** — không có `frappe.db.set_value`/`doc.save` lên `CRM Lead` | |
| Có đụng `DocShare`? | **Không** — không có `frappe.share.*` | |
| Có transaction 3 bảng (`lead_owner` + `ToDo` + `DocShare`)? | **Không** — chỉ `ToDo.status = "Cancelled"` | |

⇒ **Phân loại: CHỈ LIÊN QUAN TỚI ASSIGNMENT.** Đây là thao tác hủy ToDo của assignee, không phải chuyển quyền sở hữu.

**Route 2 — `crm.api.doc.get_assigned_users`**

| Mặt | Source | Nội dung |
|---|---|---|
| Handler của `crm_cskh` | `apps/crm_cskh/crm_cskh/api/guarded_crm.py` L31-34 | `_check_read(doctype, name)` rồi delegate, sau đó `[user for user in users if user_in_scope(frappe.session.user, user)]` — **chỉ đọc**, lọc theo scope |
| Handler upstream | CRM v1.87.0 `crm/api/doc.py` L615-632 (SHA `0d3910b4…`) | `frappe.get_all("ToDo", fields=["allocated_to"], filters={"reference_type": doctype, "reference_name": name, "status": ("not in", ("Closed","Cancelled"))}, pluck="allocated_to")` |
| Có **ghi** bất kỳ bảng nào không? | **Không** — `frappe.get_all` là SELECT thuần, không `set_value`/`save`/`share` | |
| Có đồng bộ `lead_owner` / `DocShare`? | **Không** | |

⇒ **Phân loại: CHỈ LIÊN QUAN TỚI ASSIGNMENT (thuần đọc).** Tên hàm có `get_` — không thay đổi state.

**Kết luận đánh giá từng route:**

| Tiêu chí | Đánh giá |
|---|---|
| Có phải ownership service không? | **Không — không route nào.** Cả hai đã được `crm_cskh` **override** thành bản **fail-closed**: `crm.api.doc.remove_assignments` → `crm_cskh.permissions.remove_assignments` (`hooks.py` L187, guard `_is_system` tại `permissions.py` L165-170); `crm.api.doc.get_assigned_users` → `crm_cskh.api.guarded_crm.get_assigned_users` (`hooks.py` L189, `_check_read` + lọc `user_in_scope`). **Không** route nào đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction |
| Vì sao bị bắt? | **Substring match**, không phải ngữ nghĩa. Token `assign` khớp trong cả `remove_assignments` **và** `get_assigned_users` ⇒ test bắt nhầm hai endpoint có tên chứa "assign" nhưng **không** phải reassign endpoint |
| Hai assert kèm theo | `assertFalse(hasattr(permissions, "reassign_lead"))` (L665-668) và `assertFalse(hasattr(permissions, "transfer_lead_ownership"))` (L669-672) — **PASS** ⇒ tên hàm ownership service thực sự **không** tồn tại trong `crm_cskh.permissions` |
| Kết luận | Canary **đúng nghĩa**: ownership service **chưa** tồn tại. Cơ chế phát hiện có **độ nhạy thấp** (substring) nên FAIL do route không liên quan — nhưng đây là **vấn đề độ chính xác của phép kiểm**, **không** phải phát hiện lỗ hổng |
| Mức độ | Canary giữ nguyên chức năng; **không** diễn giải thành lỗi bảo mật đã xác nhận |

> **Lưu ý về mức độ khẳng định:** kết luận "không phải ownership service" ở trên dựa trên **đọc source tĩnh** tại SHA đã ghim — **không** có runtime trace chứng minh các route này được gọi và hành vi thực tế. Đó là giới hạn của bằng chứng hiện có, cần Owner/T1C lưu ý khi chốt.

**Quyết định Owner cần có:** chốt có/không tạo ownership service theo ADR-004 §2.2.
- Nếu **có** ⇒ viết service + đổi pin thành assert "service tồn tại và đồng bộ 3 bảng", **và** thu hẹp phép lọc `reassign_routes` để tránh bắt nhầm.
- Nếu **không** ⇒ ADR-004 §2.2 ghi nhận owner chấp nhận trạng thái hiện tại; pin được giữ **vĩnh viễn** dưới dạng characterization, **không** sửa.

---

## 2. Điều tra riêng: `T1B-ext+1` — nhánh Dynamic Link trong `get_linked_calls`

**Trạng thái: `OPEN` — chưa có runtime evidence. Không hạ mức rủi ro.**

### 2.1 Đường gọi

```
cskh front-end → guarded_crm.get_linked_calls(name)      # hooks.py L191 override
                    ├─ _check_read("CRM Lead", name)      # guarded_crm.py L100-101
                    └─ crm.api.activities.get_linked_calls(name)   # CRM v1.87.0 L379
                          ├─ L380-400  frappe.db.get_all("CRM Call Log", filters={"reference_docname": name})
                          ├─ L402-404 frappe.db.get_all("Dynamic Link", filters={"link_name": name,
                          │                                 "parenttype": "CRM Call Log"}, pluck="parent")
                          ├─ L409-447 query JOIN Dynamic Link → gộp thêm CallLog không có reference_docname
                          └─ L473     calls = [parse_call_log(call) for call in calls]
                                       └─ get_call_contact(call, phone)   # crm_call_log.py L196-208
                                             ├─ nhánh A: reference_doctype ∈ {CRM Lead, CRM Deal} + có name
                                             │            → has_permission("read") → get_reference_contact()
                                             └─ nhánh B: còn lại (kể cả reference_doctype rỗng)
                                                          → get_contact_by_phone_number(phone)   ← KHÔNG có permission check
```

### 2.2 Dữ liệu có thể lộ (nhánh B)

`get_contact_by_phone_number` → `crm/integrations/api.py::get_contact` query child table `Contact Phone` **không** có permission check ⇒ trả `Contact.full_name` (và `image`) của **bất kỳ ai** khớp số điện thoại. Giá trị này đi thẳng vào `_receiver.label` (call `type == "Outgoing"`) hoặc `_caller.label` (call `type == "Incoming"`).

**Bằng chứng tĩnh cho thấy shape này khả thi:**

- `crm/api/activities.py` L402-404 lấy Call Log qua **Dynamic Link** (`link_name = name`, `parenttype = "CRM Call Log"`), **không** lọc `reference_docname`.
- Query L428 chọn `CallLog.reference_doctype` **từ chính row của Call Log** — **không** suy ra từ Dynamic Link.
- `CRMCallLog.link_with_reference_doc()` (crm_call_log.py L135-139) **chỉ** append vào child table `links` (`link_doctype`, `link_name`), **không** gán `reference_doctype`/`reference_docname`.
- Call site upstream: `crm/integrations/api.py` L94 (`FCRM Note`), L134 (`CRM Task`), `crm_call_log.py` L340 (`CRM Lead`, trong `create_lead_from_call_log`).

⇒ Một Call Log có `links` trỏ Lead nhưng `reference_doctype` **rỗng** là shape **không bị upstream loại trừ** ⇒ đi nhánh B.

### 2.3 Vì sao `guarded_crm.get_linked_calls` hiện **chưa** chặn được

`guarded_crm.get_linked_calls` chỉ `_check_read("CRM Lead", name)` rồi **delegate thẳng**. Nó **không** có guard tương tự `guarded_crm.get_call_log` (guarded_crm.py L150-162), vốn kiểm tra `reference_doctype` và chặn non-system khi ref không phải Lead/Deal.

### 2.4 Đối chiếu với run hiện tại

`TestSEC16FieldExposure::test_get_linked_calls_does_not_leak_permlevel_field` **PASS**. Nhưng test này dùng fixture có `reference_doctype = "CRM Lead"` ⇒ đi **nhánh A** (có `has_permission` gate), **không** chạm nhánh B. Vì vậy PASS này **không** phủ định được rủi ro nhánh B.

### 2.5 Test / evidence còn thiếu

| Hạng mục | Nội dung |
|---|---|
| Fixture cần | Call Log có child `links` trỏ `CRM Lead` **nhưng** `reference_doctype` **rỗng** (tạo bằng `link_with_reference_doc("CRM Lead", lead)`, **không** set `reference_docname`) |
| Kịch bản | non-system user đọc được Lead A trong scope; gọi `get_linked_calls(A)`; quan sát `_receiver.label` / `_caller.label` |
| Tiêu chí FAIL (nếu rò rỉ) | `_receiver.label` chứa `Contact.full_name` hoặc `Lead.lead_name` **ngoài scope** |
| Ràng buộc | Cần thêm method mới ⇒ **cần Owner duyệt** để nâng inventory 99 → 100 |
| Evidence hiện có | **Static-only** (đường dẫn + call site ở trên). Chưa có log runtime |

### 2.6 Remediation cần thiết (chưa thực hiện)

Guard trong `guarded_crm.get_linked_calls`, tương tự `get_call_log`: với non-system caller, lọc bỏ call có `reference_doctype` không thuộc `{"CRM Lead", "CRM Deal"}` trước khi trả về.

> **KHÔNG sửa production code ở vòng này** — chưa có bằng chứng runtime chứng minh lỗ hổng thực sự tồn tại, và nằm ngoài tiêu chí "lỗi production đã chứng minh". Không đủ điều kiện đưa vào PR remediation hiện tại.

### 2.7 Owner

**Owner / T1C** (thuộc phạm vi bảo mật, không thuộc T1B test-owned): quyết định có cần guard hay không, và duyệt method mới nếu tách thành PR remediation.

---

## 3. Điều tra riêng: `T1B-ext+2` — quyền tầng doctype đối với ToDo / Comment

**Trạng thái: `OPEN` — câu hỏi mang tính mô tả, chưa được biến thành tiêu chí PASS.**

### 3.1 Bối cảnh phát sinh

Trong lần triage 22 method (commit `658780e`), nhóm C4/C5 đã **bỏ** hai assert ngoài phạm vi:
- `test_stale_todo_after_reassign_raises_for_old_owner` — bỏ assert user `b` (new owner) đọc được ToDo
- `test_stale_comment_after_reassign_raises_for_old_owner` — bỏ assert user `b` đọc được Comment

Lý do ghi nhầm ban đầu: `check_permission("read")` đi qua `frappe.has_permission` ⇒ PermissionError cho `b`, tưởng là hành vi mong muốn. Điều này cần được giải thích đúng ở tầng doctype.

### 3.2 Điều tra: quyền tầng doctype

| Doctype | Role có quyền `read` ở **DocPerm** (mức doctype) | Nguồn |
|---|---|---|
| `ToDo` | **`All`** (read=1, write=1) + `System Manager` | `frappe/desk/doctype/todo/todo.json` tại `d2efa796…` |
| `Comment` | `System Manager`, `Website Manager` | `frappe/core/doctype/comment/comment.json` tại `d2efa796…` |
| `CRM Lead` | `System Manager`, `Sales Manager`, `Sales User` | `crm/fcrm/doctype/crm_lead/crm_lead.json` tại `0d3910b4…` |

**Diễn giải cần Owner/T1C xác nhận — chưa kết luận:**

- **`ToDo`:** có `All` ⇒ **mọi** user (kể cả `b`, `Sales User`) đều có quyền `read` ở **tầng doctype**. Vì vậy `frappe.has_permission("ToDo", "read", doc)` với `doc` cho tới `get_doc_permissions` → `has_controller_permissions` (`frappe/permissions.py` L219-221, L455-473) → hook `crm_cskh.permissions.linked_lead_has_permission` (hooks.py L166), vốn **trả `None`** khi user **có** quyền đọc Lead ⇒ **không** chặn. Nếu đúng như vậy, ToDo đọc được **vì** quyền doctype `All`, **không phải** vì stale-access.
- **`Comment`:** chỉ `System Manager` / `Website Manager` ⇒ user `b` (`Sales User`) **không** có quyền doctype ⇒ `get_doc_permissions` trả `{ptype: 0}`; nhánh share fallback (`frappe/permissions.py` L189-191) cũng không có DocShare ⇒ **deny**. Nếu đúng như vậy, Comment bị chặn **vì** thiếu quyền doctype, **không phải** vì stale-access.

> **Mức độ bằng chứng: TĨNH (đọc DocPerm JSON + source tại SHA đã ghim).** Hai vế trên là **suy luận từ cấu hình**, **chưa** có runtime confirm cho tình huống cụ thể.

⇒ **Hai doctype, hai lý do KHÁC NHAU**, cùng biểu hiện `PermissionError`. Đây là điểm cần tách bạch.

> **Cách diễn đạt bắt buộc:** `PermissionError` quan sát được ở đây là **bằng chứng giới hạn theo actor / doctype / document / fixture / request context**, **chưa đủ** để kết luận **có** hay **không có** vulnerability:
> - **Phạm vi actor:** quan sát chỉ ghi nhận hành vi của user `b` (`Sales User`, Team 2) trong scenario cụ thể — **không** suy rộng được sang user khác, role khác, hoặc `Administrator` / system user.
> - **Phạm vi doctype:** hai kết quả `PermissionError` thuộc **hai doctype khác nhau** (`ToDo` vs `Comment`) với **hai lý do chặn khác nhau** — không gộp chung thành một kết luận.
> - **Phạm vi document:** chỉ trên Lead đã tạo trong fixture; **không** kiểm tra tài liệu thuộc scope khác, tài liệu cha/con, hay tình huống nhiều bản ghi.
> - **Phạm vi fixture:** các assert liên quan đã được **bỏ** trong commit `658780e` khi phát hiện ghi nhầm ⇒ phần lớn bằng chứng `PermissionError` hiện có **không** đến từ một `assertRaises` còn sống trong inventory 99.
> - **Phạm vi request context:** đường gọi qua `frappe.has_permission` trong test context (permission query condition của session), **không** bao hàm đường HTTP qua reverse proxy / CORS — phạm vi mà artifact `http-transport.json` tự ghi là **không** được cover.
>
> Vì vậy:
> - **Không** phải bằng chứng rằng stale-access **đã** được chặn đúng — vì lý do chặn có thể là cấu hình DocPerm, không phải guard.
> - **Cũng không** phải bằng chứng rằng **không có** lỗ hổng — vì chưa có kịch bản nào thử đọc dữ liệu ngoài scope qua đường này.
> - Kết luận đúng ở thời điểm này: **chưa đủ evidence để kết luận theo hướng nào.**

### 3.3 Dữ liệu có thể lộ

Ở fixture hiện tại: **không quan sát thấy** dữ liệu bị lộ qua đường ToDo/Comment với user `b` — nhưng đây là **"không quan sát thấy trong kịch bản đã chạy"**, **không** phải kết luận "không có rủi ro". Cơ chế bị chặn là hệ quả của cấu hình DocPerm upstream, **không phải** guard cố ý của `crm_cskh`.

**Rủi ro tiềm năng (chưa xác minh):** nếu ai đó thêm DocPerm `read` cho `ToDo`/`Comment` cho `Sales User` trong tương lai (ví dụ để hiện task trong UI), thì:
- `ToDo` sẽ mở cho mọi Sales User **ngoài scope** ⇒ rò rỉ tiêu đề/nội dung task của Lead ngoài scope — **trừ khi** hook `linked_lead_has_permission` chặn được.
- `Comment` tương tự.
⇒ Đây là **rủi ro cấu hình**, cần Owner/T1C quyết định có chủ động pin hành vi này thành regression test hay không.

### 3.4 Test / evidence còn thiếu

| Hạng mục | Nội dung |
|---|---|
| Cần xác minh | Với user **đúng scope**, liệu `ToDo`/`Comment` của Lead đó có đọc được **không**? Nếu **không** ⇒ UI không hiển thị task/comment cho owner ⇒ **vấn đề chức năng**, không phải bảo mật |
| Cần xác minh | `linked_lead_has_permission` có chặn đúng khi DocPerm được mở rộng cho `Sales User` không |
| Evidence hiện có | **Static-only** (DocPerm JSON tại SHA đã ghim + `frappe/permissions.py` L162-191/L219-221/L455-473 + `crm_cskh/permissions.py` L119-130). Chưa có log runtime cho tình huống "user đúng scope đọc ToDo/Comment" |
| Ràng buộc | Cần method mới ⇒ **cần Owner duyệt** inventory 99 |

### 3.5 Remediation cần thiết (chưa thực hiện)

**Chưa xác định** — phụ thuộc kết quả §3.4:
- Nếu user đúng scope **không** đọc được ⇒ cần Owner chọn: mở DocPerm (chấp nhận rủi ro, cần guard record-level) **hoặc** giữ nguyên (chấp nhận thiếu chức năng).
- Nếu đọc được ⇒ chỉ cần **pin** hành vi này thành regression test, **không** sửa code.

### 3.6 Owner

**Owner / T1C** — liên quan `permission-matrix.md` §3.5/§3.6 và cấu hình DocPerm, không thuộc phạm vi T1B test-owned.

---

## 4. Hạng mục mới: `T1B-ext+3` — truy nguyên drift của pin Q2

**Trạng thái: `OPEN` — mới phát hiện. Quan sát đã có runtime; cơ chế chỉ là giả thuyết.**

| Thuộc tính | Nội dung |
|---|---|
| Quan sát (runtime, **đã xác minh**) | Run [`#39976477275`](https://github.com/nobita6986/new-crm/actions/runs/39976477275), artifact ID `12512295421`, `test_docshare_lifecycle.log` (SHA-256 `f7b089fd…`): `after assign_agent (assignee ngoài scope) — DocShare: None`. Giống hệt ở **cả 10 run** đã tải ⇒ **tái lập xuyên suốt** |
| Đối chiếu pin | `PINNED_BEHAVIOR["Q2"]["current_behavior"]` = *"assign_to.add tạo DocShare khi assignee chưa có quyền"* ⇒ quan sát **không khớp** mô tả |
| Tác động | 7/7 canary DocShare FAIL; Q1/Q3 **không đánh giá được** vì tiền đề thiết lập (theo Q2) chưa đạt |
| **Giả thuyết cơ chế (chưa xác minh)** | H1 `lead_owner` bị `crm/api/todo.py` L22-25 ghi đè thành `c` · H2 nhánh ToDo của `crm.permissions.org_hierarchy._permission_query_conditions` L42-51 cho phép đọc · H3 tương tác hai hệ scope. Chi tiết tại §1.2.2. **Cả ba đều chưa được phân biệt bằng runtime** |
| Mức độ | Quan sát = **runtime evidence**. Cơ chế = **static hypothesis**. **Không** kết luận có/không có lỗ hổng scope |
| Test cần | Method xác minh `frappe.has_permission("CRM Lead", doc=lead, user="c")` ngay **trước** và **sau** `assign_agent`, đồng thời đọc `lead_owner` trước/sau, để phân biệt H1 với H2/H3 |
| Ràng buộc | Method mới ⇒ **cần Owner duyệt** trước khi nâng inventory 99 → 100. **Hiện chưa thêm** |
| Owner | **Owner/T0** (quyết định Q2) + T1B (viết test sau khi được duyệt) |

> **Không tự ý sửa pin Q2.** Pin sai về mặt mô tả là việc của Owner: nếu hành vi thực tế (không tạo share) là **đúng ý**, thì Q2 cần viết lại theo hướng đó và assertion đổi tương ứng — đây là **quyết định policy**, không phải sửa test.

---

## 5. RFC — đường gọi nội bộ `get_activities` và redaction SEC-26

**Trạng thái: `OPEN` — RFC (Request For Change) của T1B. Kết quả tĩnh đã xác minh; runtime evidence cho `get_activities` chưa tồn tại. SEC-26 giữ `PARTIAL`, G0-07 giữ `PARTIAL` / CHƯA KÝ, P0 giữ `NO-GO`.**

**Nguồn pin (đã tải trực tiếp từ GitHub API tại SHA, không dùng bản copy trong repo):**

| Nguồn | SHA | Nội dung đã đối chiếu |
|---|---|---|
| CRM v1.87.0 | `0d3910b456ef4fb913e77b29db011b3debc4f778` | `crm/api/activities.py` (530 dòng). SHA-256 nội dung chuẩn hóa (`\n`, bỏ newline cuối): `9f348b6dc42366cf6b8e2439b08c44a62ad8df53633b921fbbf0e398a4aaadb3`. **Đối chiếu chéo:** nội dung **giống hệt** bản `deploy/dev/evidence/logs/upstream-crm-v1.87.0/crm_api_activities.py` đã có (bản đó lưu UTF-16 + CRLF, cần chuẩn hóa mới khớp) |
| Frappe v15.123.1 | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | `frappe/handler.py`, `frappe/__init__.py`, `frappe/api/__init__.py`, `frappe/api/v1.py`, `frappe/api/v2.py`, `frappe/desk/form/load.py`, `frappe/core/doctype/file/file.py`, `frappe/core/doctype/file/utils.py`, `frappe/utils/response.py` |
| `crm_cskh` (local) | nhánh PR #2 | `apps/crm_cskh/crm_cskh/hooks.py`, `apps/crm_cskh/crm_cskh/api/guarded_crm.py`, `apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py` |

### 5.1 Mắt xích tĩnh — xác minh từng bước

**Mắt xích A — `get_activities` CÓ được expose qua whitelist không?**

| Kiểm tra | Kết quả | Nguồn |
|---|---|---|
| Có decorator `@frappe.whitelist()`? | **Có** — L13 `@frappe.whitelist()`, L14 `def get_activities(name: str):` | CRM `crm/api/activities.py` L13-14 |
| Cơ chế đăng ký whitelist | `frappe.whitelist()` → `innerfn` → `whitelisted.append(fn)` (L864), `allowed_http_methods_for_whitelisted_func[fn] = methods` (L865). Mặc định `methods = ["GET","POST","PUT","DELETE"]` (L844-845) | Frappe `frappe/__init__.py` L823-873 |
| Có cho phép Guest không? | **Không** — `allow_guest=False` mặc định ⇒ `guest_methods` **không** chứa hàm này ⇒ `is_whitelisted` chặn Guest | Frappe `frappe/__init__.py` L844, L867-868, L881-886 |
| Đường HTTP tới được? | `/api/method/crm.api.activities.get_activities` → `frappe/api/v1.py` L34-40 `handle_rpc_call` → `frappe.handler.handle()` → `execute_cmd(cmd)` | Frappe `frappe/api/v1.py` L34-40, `frappe/handler.py` L46-53 |

⇒ **Kết luận A:** `get_activities` **là endpoint HTTP công khai (whitelisted)**, tới được bằng session đăng nhập (không Guest).

**Mắt xích B — CÓ override trong `crm_cskh` không?**

| Kiểm tra | Kết quả | Nguồn |
|---|---|---|
| Trong `override_whitelisted_methods` của `crm_cskh`? | **KHÔNG.** Hook (L177-195) override 18 route, trong đó có `crm.api.activities.get_linked_calls` (L191), `get_linked_notes` (L192), `get_linked_tasks` (L193), `get_attachments` (L194) — nhưng **không** có `crm.api.activities.get_activities` | `apps/crm_cskh/crm_cskh/hooks.py` L177-195 |
| Trong `crm/hooks.py` upstream? | **KHÔNG** — khối `override_whitelisted_methods` của CRM v1.87.0 bị **comment** (`# override_whitelisted_methods = {`) | CRM `crm/hooks.py` L345-347 |
| Hook của app khác trong `apps/`? | **KHÔNG** — `apps/crm_cskh/crm_cskh/hooks.py` là file `hooks.py` **duy nhất** có `override_whitelisted_methods` | quét `apps/**/*.py` |
| Có monkey-patch `crm.api.activities` không? | **KHÔNG** — không có `setattr(...)`/`monkeypatch` nào trong `apps/crm_cskh`; wrapper gọi ngược qua `frappe.get_attr("crm.api.activities.<fn>")` (L101/L107/L113/L119) — tức **gọi hàm upstream gốc**, không thay thế binding | `apps/crm_cskh/crm_cskh/api/guarded_crm.py` L101-119 |

**Cơ chế override của Frappe — mắt xích then chốt:**

| Kiểm tra | Kết quả | Nguồn |
|---|---|---|
| Override áp dụng ở đâu? | **Ở tầng dispatch HTTP, bằng đổi TÊN chuỗi** — `cmd = frappe.override_whitelisted_method(cmd)` | Frappe `frappe/handler.py` L67 (trong `execute_cmd`) |
| Hàm override làm gì? | `overrides = get_hooks("override_whitelisted_methods", {}).get(original_method, [])` → `return overrides[-1] if overrides else original_method` — chỉ **đổi chuỗi đường dẫn** | Frappe `frappe/__init__.py` L2553-2556 |
| Có patch attribute của module không? | **KHÔNG.** Quét toàn bộ repo Frappe tại SHA: `override_whitelisted` chỉ xuất hiện ở **2** điểm — `frappe/handler.py:67` và `frappe/api/v2.py:36`. **Không** có chỗ nào gán/monkey-patch `crm.api.activities.get_attachments` | quét cây file Frappe tại SHA (1.427 file `.py`) |
| Điểm vào HTTP khác? | `frappe/api/v2.py` L36 (`method = frappe.override_whitelisted_method(method)`) — cùng cơ chế đổi tên | Frappe `frappe/api/v2.py` L36 |

⇒ **Kết luận B:** `crm.api.activities.get_activities` **không** được override. Điểm này quyết định toàn bộ mắt xích tiếp theo: override chỉ có tác dụng khi **chính endpoint đó** được gọi qua HTTP dispatch; các lời gọi **nội bộ** trong Python không đi qua `execute_cmd` nên **không** bị override.

**Mắt xích C — `get_activities` gọi `get_attachments` nào?**

| Bước | Nội dung | Nguồn |
|---|---|---|
| `get_activities(name)` | `if frappe.db.exists("CRM Deal", name): return get_deal_activities(name)` / `elif frappe.db.exists("CRM Lead", name): return get_lead_activities(name)` | CRM `crm/api/activities.py` L14-21 |
| `get_deal_activities` | L172 `attachments = attachments + get_attachments("CRM Deal", name)`; thêm L131 `get_attachments("Comment", comment.name)`, L150 `get_attachments("Communication", communication.name)` | CRM `crm/api/activities.py` L131, L150, L172 |
| `get_lead_activities` | L313 `attachments = get_attachments("CRM Lead", name)`; thêm L272 `get_attachments("Comment", comment.name)`, L291 `get_attachments("Communication", communication.name)` | CRM `crm/api/activities.py` L272, L291, L313 |
| **Đây là hàm nào?** | **Hàm cùng module** — `get_attachments` được định nghĩa tại L321 **ngay trong `crm/api/activities.py`**. Không có `import` nào khác (quét toàn file: **0** dòng import `get_attachments`) | CRM `crm/api/activities.py` L321 |
| Có phải bản đã override không? | **KHÔNG.** Lời gọi là **bare name** (tên hàm cục bộ trong module) ⇒ Python resolve thẳng binding module-level `get_attachments` của `crm.api.activities`. Override của `crm_cskh` chỉ thay **tên ở dispatch HTTP** ⇒ **không** ảnh hưởng binding này | Frappe `frappe/__init__.py` L2553-2556 + `frappe/handler.py` L67 |
| `get_linked_calls`/`get_linked_notes`/`get_linked_tasks` | Cũng là bare name, resolve về bản upstream L379/L477/L486 — **không** qua wrapper `guarded_crm` | CRM `crm/api/activities.py` L310-311, L169-171 |

⇒ **Kết luận C:** `get_activities` gọi **hàm upstream gốc** `crm.api.activities.get_attachments` (L321), **không** đi qua `crm_cskh.api.guarded_crm.get_attachments`.

**Mắt xích D — Response có giữ field `file_url` không?**

| Trường | `crm.api.activities.get_attachments` (L321-339) | `guarded_crm.get_attachments` (wrapper đã override) |
|---|---|---|
| Cách query | `frappe.db.get_all("File", filters={"attached_to_doctype": doctype, "attached_to_name": name}, fields=[...])` | `frappe.get_attr("crm.api.activities.get_attachments")(doctype, name)` — cùng payload upstream |
| `fields` | `["name","file_name","file_type","file_url","file_size","is_private","modified","creation","owner"]` — **CÓ `file_url`** (L330) | giữ nguyên payload upstream rồi redact |
| `content_hash` | **KHÔNG** có trong `fields` (đã xác minh, khớp `t1b-sec26-upstream-contract-v1.87.0.md` §2) | redaction `content_hash` là **no-op** với payload này |
| Có filter quyền không? | `frappe.db.get_all` → `frappe.get_all` → docstring: *"List database query via `frappe.model.db_query`. Will **not** check for permissions."* ⇒ **KHÔNG** kiểm tra quyền ở tầng query | wrapper `guarded_crm` thêm `_check_read(doctype, name)` L118 |

⇒ **Kết luận D:** payload upstream **giữ `file_url`**, kể cả khi `is_private=1`.

**Mắt xích E — Đường gọi đó có đi qua wrapper redaction không?**

| Kiểm tra | Kết quả |
|---|---|
| `guarded_crm.get_attachments` (đã override) | CÓ redaction — `return _redact_private_file_urls(attachments)` (L125) |
| `guarded_crm.get_linked_docs_of_document` | CÓ redaction — L95 |
| **`get_activities` → `get_lead_activities`/`get_deal_activities` → `get_attachments`** | **KHÔNG.** Chuỗi này gọi hàm upstream gốc (mắt xích C) ⇒ `_redact_private_file_urls` **không** được gọi |

⇒ **Kết luận E:** theo **source tĩnh**, chuỗi `get_activities` **không** đi qua wrapper redaction, trong khi payload `get_attachments` upstream **có** `file_url`.

### 5.2 Runtime observation — tìm trong artifact run mới nhất

**Artifact đã kiểm tra:** run [`#39976477275`](https://github.com/nobita6986/new-crm/actions/runs/39976477275) · head `138f6677006c978eee0823033c4e8a9634cf6435` · merge `08170e954f63af209f393e066cd85cfff4329844` · artifact **ID `12512295421`** · SHA-256 `f17dd94fc5b67929027db3769831e99589768af4542d86295c5e3ab8cf83c6e0` (131.623 bytes — **khớp** digest GitHub API).

**Tìm kiếm runtime evidence trực tiếp cho `get_activities`:**

| Phạm vi tìm | Kết quả |
|---|---|
| Toàn bộ artifact của run `#39976477275` (mọi file, regex `get_activities`, không phân biệt hoa thường) | **0 hit** |
| **Tất cả 10 run đã tải** (`run39885152649` … `run39976477275`) | **0 hit** |
| 14 case của HTTP transport probe (`http/http-transport.json`) | **KHÔNG** có case nào gọi `get_activities`. Danh sách case: `sec13-guest`, `staff-login`, `sec13-user`, `csrf-page`, `sec14-get`, `sec14-post-nocsrf`, `sec14-post-badtok`, `sec14-post-ok`, `administrator-login`, `sec13-admin`, `sec16-permlevel-get-data`, `sec16-permlevel-sentinel-scan`, `sec26-staff-private`, `sec26-admin-private` |
| Probe script `deploy/dev/seed/ci_http_transport_probe.py` | Chỉ dựng `attachments_path = "/api/method/crm_cskh.api.guarded_crm.get_attachments?"` (L446). **Không** có path nào trỏ `crm.api.activities.get_activities` |

> **Lưu ý về 2 hit `get_activities` trong thư mục run `#39976477275`:** đến từ **script kiểm tra cục bộ** do T1B tạo (`_http_check.py`), **không** phải từ artifact CI. Script đó **không** được đưa vào artifact ⇒ **không** làm thay đổi kết luận "0 hit trong artifact".
>
> **Lưu ý về 7 hit `crm.api.activities` trong `test_security_remediation_v2.junit.xml`:** đó là **docstring** của test SEC-16/SEC-26 tham chiếu `crm.api.activities.get_attachments` (hàm upstream) để **mô tả contract**, **không** phải test gọi `get_activities`. Đã đọc lại từng hit: L141, L315, L332, L340, L354, L365 (docstring) và L291 (`attachments = frappe.get_attr("crm.api.activities.get_attachments")(doctype, name)` — đây là **lời gọi** `get_attachments`, nằm trong test SEC-16 `test_get_attachments_does_not_leak_permlevel_field`, gọi hàm upstream **trực tiếp**, không qua `get_activities`).

⇒ **Kết luận runtime:** **KHÔNG có** runtime evidence trực tiếp cho endpoint `get_activities` trong artifact đã kiểm tra (10/10 run).

### 5.2.1 Bổ sung tĩnh — endpoint này nằm **trên đường chính của sản phẩm** (không phải API thừa)

Quét toàn bộ frontend CRM v1.87.0 (507 file `.js`/`.vue`/`.ts` tại SHA `0d3910b4…`) cho `get_activities` → **3 hit**, tất cả trong `frontend/src/components/Activities/Activities.vue`:

| Line | Nội dung | Ý nghĩa |
|---|---|---|
| L540 | `url: 'crm.api.activities.get_activities'` (trong `createResource`) | **Đây là endpoint mà UI chính của CRM gọi** khi mở tab Activity của một Lead/Deal |
| L541 | `params: { name: props.docname }` | Truyền `name` = tài liệu đang xem |
| L544-546 | `transform: ([versions, calls, notes, tasks, attachments]) => …` | UI **nhận và dùng** cả 5 phần tử, trong đó có `attachments` |
| L635/L645 | `function get_activities()` / `_activities = get_activities()` | Hàm cục bộ cùng tên, **không** phải endpoint |

⇒ **Hệ quả:** `get_activities` **không** phải endpoint "để quên" — nó là **đường dẫn chính** để tải danh sách attachment của Lead/Deal trong UI. Bất kỳ khoảng trống redaction nào trên chuỗi này ảnh hưởng **ngay** tới payload mà sản phẩm dùng.

**UI render `file_url` trực tiếp** (`frontend/src/components/Activities/AttachmentArea.vue`, tại SHA `0d3910b4…`):

| Line | Nội dung |
|---|---|
| L16 | `:src="attachment.file_url"` — ảnh thumbnail dùng `file_url` làm `src` |
| L27 | `{{ attachment.file_name }}` |
| L39/L48 | `attachment.is_private ? __('Make Public') : __('Make Private')` — UI **hiển thị** trạng thái private |
| L92-94 | `function openFile(attachment) { window.open(attachment.file_url, '_blank') }` — bấm là mở URL file |

⇒ Nếu `file_url` của file private có mặt trong payload, UI **hiện** nó (thumbnail + link mở tab mới). Đây là **bằng chứng tĩnh về hành vi UI**, **không** phải runtime evidence, và **không** nói gì về việc URL đó có tải được hay không (xem H-RFC-2).

### 5.2.2 Bổ sung tĩnh — đường thứ hai cùng lộ `file_url`: `docinfo.attachments`

Khi `get_lead_activities` gọi `get_docinfo("", "CRM Lead", name)` (CRM L183), Frappe ghi `frappe.response["docinfo"] = docinfo` (`frappe/desk/form/load.py` L147). `frappe.response` là `local("response")` (`frappe/__init__.py` L188, `frappe.local` = `werkzeug.local.Local` — thread-local dict), và `as_json()` serialize **toàn bộ** `frappe.local.response` (`frappe/utils/response.py` L154) ⇒ `docinfo` **có** trong body JSON.

Trong `docinfo` có `attachments` lấy từ `frappe/desk/form/load.py` L128 → `get_attachments(doc.doctype, doc.name)` (L200-214), với `fields` **bao gồm** `"file_url"` (L206) — **không** qua bất kỳ wrapper redaction nào của `crm_cskh`.

| Đường | Nguồn `file_url` | Có redaction? |
|---|---|---|
| `message[4].attachments` (phần tử thứ 5 của tuple trả về) | CRM `activities.py` L321-339 | **KHÔNG** |
| `docinfo.attachments` | Frappe `load.py` L200-214 | **KHÔNG** |

⇒ **Hai** đường trong cùng một response đều có thể chứa `file_url` của file private mà không đi qua `_redact_private_file_urls`.

**Lưu ý giới hạn quan trọng (làm giảm mức độ, không tăng):** `get_docinfo` gọi `add_comments` → `get_document_comments` (`frappe/core/doctype/comment/comment.py` L172-210), hàm này **lọc theo quyền read của từng `comment_type`** qua `has_controller_permissions` (L196-209) ⇒ phần **Comment** trong `docinfo` **đã** được lọc quyền. Vì vậy **không** thể kết luận rằng `get_activities` lộ dữ liệu Comment ngoài scope; chỉ có thể nói phần **`attachments`** là đường không qua redaction.

### 5.2.3 Bổ sung tĩnh — **ranh giới quyền** của chính `get_activities` (giới hạn mức độ)

`get_activities` **không** phải endpoint không kiểm tra quyền. Ngay ở đầu chuỗi:

| Vị trí | Source | Nội dung |
|---|---|---|
| `get_deal_activities` | CRM `activities.py` **L24-25** | `if not frappe.has_permission("CRM Deal", "read", name): frappe.throw(_("Not permitted"), frappe.PermissionError)` |
| `get_lead_activities` | CRM `activities.py` **L181-182** | `if not frappe.has_permission("CRM Lead", "read", name): frappe.throw(_("Not permitted"), frappe.PermissionError)` |

⇒ **Caller phải có quyền `read` trên chính Lead/Deal** mới đi qua được `get_activities`. Đây là **guard thật**, chạy **trước** khi dựng payload.

**Hệ quả đối với việc phân loại mức độ — KHÔNG được diễn giải quá mức:**

1. Đây **không** phải IDOR (không đọc chéo Lead của người khác): caller ngoài scope bị chặn ngay ở L181/L24 bằng `PermissionError`.
2. Khoảng trống redaction **chỉ** liên quan tới file private **đính kèm trên chính tài liệu mà caller đã có quyền `read`**. Câu hỏi cần trả lời là: *"caller có quyền `read` trên Lead, nhưng không nên thấy `file_url` của file private đính kèm"* — tức là **mức độ thấp hơn** so với việc lộ tài liệu ngoài scope.
3. Vẫn **không** được coi là `PASS`: `_redact_private_file_urls` tồn tại **chính vì** chính sách này — payload của file private **không** nên chứa `file_url` khi caller không phải system. Việc một đường đi vòng wrapper là **lệch hợp đồng redaction**, dù mức độ thấp.
4. Chưa có runtime evidence ⇒ **không** nâng lên `CONFIRMED VULNERABILITY`. Kết luận đúng là **coverage gap** của SEC-26 (xem §5.4).

### 5.2.4 Đối chiếu với `get_attachments` đã override — vì sao 2 chuỗi khác nhau

| | `guarded_crm.get_attachments` (đã override) | `crm.api.activities.get_activities` (**không** override) |
|---|---|---|
| Guard quyền | `_check_read(doctype, name)` → `PermissionError` (L117-119) | `frappe.has_permission(..., "read", name)` ngay trong upstream (L24/L181) |
| Nguồn `file_url` | upstream `crm.api.activities.get_attachments` (L321-339) | **cùng** hàm upstream, gọi nội bộ |
| Redaction | **CÓ** — `_redact_private_file_urls` (L125) | **KHÔNG** |
| Payload `attachments` | list thuần | list thuần **+** `docinfo.attachments` (L128/147 `load.py`) |

⇒ Hai chuỗi **dùng chung** hàm upstream `get_attachments` (L321). Chỉ khác ở chỗ chuỗi thứ hai **không** đi qua wrapper ⇒ **cùng một nguồn `file_url`, hai chính sách redaction khác nhau**. Đây là **bất nhất** về mặt hợp đồng, và là lý do đề xuất ở §5.4 là đưa `get_activities` vào cùng cơ chế override sẵn có (không phải viết lại logic redaction).

**Case SEC-26 có sẵn — CHỈ phủ endpoint đã override (không suy ra cho `get_activities`):**

| Case | Request | Response (đã redact theo artifact) | Assertion |
|---|---|---|---|
| `sec26-staff-private` | `GET http://127.0.0.1:8000/api/method/crm_cskh.api.guarded_crm.get_attachments?doctype=CRM+Lead&name=CRM-LEAD-00001` · `cookie` = `[REDACTED]` | **200** · `{"message":[{"name":"9349f349d9","file_name":"DEMO-CRM-LEAD-00001.txt","file_type":"TXT","file_size":72,"is_private":1,"modified":"2026-10-23 22:16:38.396225","creation":"2026-10-23 22:16:38.396225","owner":"Administrator"}]}` — **KHÔNG có `file_url`** | `result=PASS` (staff non-system bị redact) |
| `sec26-admin-private` | Cùng URL, session Administrator | **200** · `{"message":[{"name":"9349f349d9","file_name":"DEMO-CRM-LEAD-00001.txt","file_type":"TXT","file_url":"[REDACTED]","file_size":72,"is_private":1,"modified":"2026-10-23 22:16:38.396225","creation":"2026-10-23 22:16:38.396225","owner":"Administrator"}]}` — **CÓ `file_url`** (giá trị đã redact trong artifact) | `result=PASS` (system caller giữ URL) |

> **Lưu ý về `name` của file:** `9349f349d9` ở run này vs `eb0fb1d699` ở run trước — **khác** ⇒ file được seed lại mỗi run (hash tên file), **không** phải artifact bị cache. Hành vi redaction **giống hệt**: staff ⇒ không `file_url`; Administrator ⇒ có `file_url`.

**Giới hạn phạm vi của bằng chứng runtime này — KHÔNG được suy rộng:**

1. Cả hai case gọi **`crm_cskh.api.guarded_crm.get_attachments`** — endpoint **đã** được override. Chúng **không** chứng minh điều gì về `crm.api.activities.get_activities`.
2. Chỉ phủ **một** file private của **một** Lead seed (`CRM-LEAD-00001`), **một** doctype (`CRM Lead`), **hai** actor (staff non-system + Administrator).
3. Chỉ phủ tầng **direct HTTP tới `bench serve`** — `coverage_scope` trong artifact tự ghi: *"direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered"*.
4. Artifact **không** ghi giá trị `file_url` thật (đã `[REDACTED]`) ⇒ không dùng để đối chiếu nội dung URL.
5. Method unit `TestSEC26FileURLLeak` **6/6 PASS** tại run này — nhưng 6 method đó gọi **wrapper đã override**, **không** gọi `get_activities`.
6. Không có kịch bản nào trong artifact đo hành vi của `get_activities` với **caller ngoài scope** ⇒ **chưa** biết `get_activities` có thực sự trả `file_url` cho non-system hay không.

### 5.3 Phân tách tĩnh / runtime / giả thuyết

| Tầng | Nội dung | Trạng thái |
|---|---|---|
| **SOURCE TĨNH** (đã xác minh, có line reference) | A: `get_activities` là `@frappe.whitelist()` (CRM L13-14), không cho Guest · B: **không** có override (hooks L177-195; CRM hooks L345-347 bị comment; Frappe chỉ có 2 điểm dispatch L67/L36, không patch attribute) · C: gọi hàm upstream gốc L321 (bare name, 0 import) · D: payload **có** `file_url` (L330), `content_hash` **không** có · E: chuỗi này **không** đi qua `_redact_private_file_urls` · **F: hai** đường lộ `file_url` (`message[4].attachments` + `docinfo.attachments` — Frappe `load.py` L128/L147/L206) · **G: đây là endpoint UI chính** (CRM `Activities.vue` L540) và UI **render** `file_url` (`AttachmentArea.vue` L16, L93) | **Xác minh** — đọc source tại SHA đã ghim |
| **RUNTIME OBSERVATION** (đã xác minh) | Probe `sec26-staff-private` → 200, **không** `file_url`; `sec26-admin-private` → 200, **có** `file_url`. `TestSEC26FileURLLeak` **6/6 PASS**. **0 hit** `get_activities` trong **mọi** artifact đã tải (10/10 run) | **Xác minh** — nhưng **chỉ** cho endpoint đã override |
| **GIẢ THUYẾT** (chưa xác minh) | H-RFC-1: một caller **đã đăng nhập** **có quyền `read` trên Lead** gọi `GET /api/method/crm.api.activities.get_activities?name=<Lead trong scope>` sẽ nhận payload chứa `file_url` của file **private** đính kèm Lead đó, **không** qua redaction | **GIẢ THUYẾT** — dựa trên A+B+C+D+E+F+G + §5.2.1–§5.2.4; **chưa** có runtime confirm. **Phạm vi giới hạn:** caller ngoài scope bị chặn ở L24/L181 ⇒ không phải IDOR |
| **GIẢ THUYẾT** (chưa xác minh) | H-RFC-2: lộ `file_url` **tự nó** chưa đủ để tải nội dung — `frappe/handler.py` L287-302 `download_file` → `find_file_by_url` → `File.is_downloadable()` → `has_permission(file,"read")` (`file.py` L891-892, L992-1031) vẫn kiểm tra quyền read trên File / doc cha ⇒ việc lộ URL **có thể** không tự động cấp quyền tải | **GIẢ THUYẾT** — đọc source Frappe; **chưa** có runtime confirm |

### 5.4 Đánh giá mức độ — KHÔNG kết luận có vulnerability, cũng KHÔNG kết luận an toàn

**Những gì bằng chứng hiện có KHÔNG cho phép kết luận:**

- **KHÔNG** được kết luận *có vulnerability* chỉ vì thấy chuỗi gọi trông như bypass wrapper. Lý do: (i) chưa có runtime nào quan sát `get_activities`; (ii) endpoint này vẫn **kiểm tra quyền read** ở đầu vào — `get_lead_activities` L181 `if not frappe.has_permission("CRM Lead","read",name): throw PermissionError` và `get_deal_activities` L24 tương tự ⇒ caller **ngoài scope** bị chặn trước khi tới `get_attachments`; (iii) H-RFC-2 cho thấy lộ URL **chưa chắc** đồng nghĩa đọc được nội dung.
- **KHÔNG** được kết luận *an toàn* chỉ vì 6/6 method SEC-26 PASS và probe 14/14 PASS: những bằng chứng đó **chỉ** phủ `guarded_crm.get_attachments` và `guarded_crm.get_linked_docs_of_document` — **không** phủ `get_activities`.
- **KHÔNG** được nâng SEC-26 từ `PARTIAL`: tiêu chí đủ điều kiện đóng chưa có, và RFC này **mở** thêm một phạm vi phủ chưa được đo.

**Kết luận T1B ở thời điểm này:** đây là **một khoảng trống phạm vi phủ (coverage gap)** đã được xác định bằng source tĩnh, **chưa** phải vulnerability đã chứng minh. Mức độ bằng chứng: **static-only cho `get_activities`; runtime cho endpoint đã override.**

**Mức độ cụ thể mà bằng chứng hiện có cho phép nói (không hơn):**

| Khía cạnh | Mức độ | Cơ sở |
|---|---|---|
| Đọc chéo tài liệu ngoài scope (IDOR) | **KHÔNG** — đã có guard `has_permission` ở L24/L181 | §5.2.3 |
| Lộ `file_url` cho caller **có** quyền `read` trên tài liệu | **Chưa đo** — static cho thấy 2 đường không qua redaction | §5.2.1, §5.2.2, §5.2.4 |
| Tải được **nội dung** file private nhờ URL lộ | **Chưa đo** — `File.is_downloadable()` vẫn kiểm tra quyền | H-RFC-2 |
| Lộ dữ liệu Comment ngoài scope | **KHÔNG** — `get_document_comments` đã lọc theo quyền từng `comment_type` | §5.2.2 |

Vì đường lộ nằm **trên đường chính của UI** (§5.2.1) và có **2** đường (§5.2.2), khoảng trống này **không** phải loại "API thừa không ai dùng" — nhưng ranh giới quyền ở L24/L181 **giữ** nó ở mức **thấp**, không phải IDOR.

### 5.5 Đề xuất kiểm chứng tiếp theo

| # | Kiểm chứng | Mục đích | Cần method mới? |
|---|---|---|---|
| RFC-1 | Probe HTTP cho `GET /api/method/crm.api.activities.get_activities?name=CRM-LEAD-00001` với **staff non-system** + **Administrator**, ghi **có/không** `file_url` trong payload và trong `docinfo.attachments` | Đo trực tiếp đường gọi chưa được phủ | **Có** — thêm **case probe** (không phải method test) |
| RFC-2 | Nếu RFC-1 cho thấy `file_url` lộ: đo tiếp `GET /api/method/download_file?file_url=<url>` với cùng staff ⇒ xác minh H-RFC-2 (URL lộ **có** hay **không** tự cấp quyền tải) | Phân biệt "lộ URL" với "đọc được nội dung" | **Có** — thêm **case probe** |
| RFC-3 | Unit test ở tầng Python: gọi `crm.api.activities.get_activities(name)` với user **đúng scope** trong khi file private đính kèm ⇒ assert payload **có** `file_url` | Pin hành vi thực tế của chuỗi nội bộ | **Có** — cần method test ⇒ **cần Owner duyệt** nâng inventory 99 → 100 |
| RFC-4 | Nếu RFC-3 xác nhận lộ `file_url`: Owner/T1C chọn remediation — override `crm.api.activities.get_activities` trong `hooks.py` sang wrapper có redaction, **hoặc** thu hẹp filter `fields` của `get_attachments` upstream | Đóng khoảng trống phủ | **Không** — nhưng là **thay đổi code production**, cần Owner duyệt |
| RFC-5 | Method test chứng minh `guarded_crm.get_attachments` vẫn redact sau mọi thay đổi (chống regression) | Bảo vệ endpoint đã cover | **Không** — đã có V16/V17 |
| **RFC-6** | Probe HTTP: **tách riêng** `message[4].attachments` và `docinfo.attachments` trong response `get_activities` — ghi rõ `file_url` xuất hiện ở đường nào | Xác định chính xác đường lộ (2 đường: upstream L321 vs Frappe `load.py` L200-214) | **Không** — mở rộng case RFC-1 |
| **RFC-7** | Probe HTTP: caller **ngoài scope** gọi `get_activities` ⇒ xác minh response là `PermissionError` (L181/L24), **không** phải payload | Chứng minh ranh giới quyền bằng runtime, **giảm** mức độ từ "chưa biết" xuống "đã đo" | **Không** — mở rộng case RFC-1 |

**Kết luận về nhu cầu method mới:** RFC-1, RFC-2, RFC-6 và RFC-7 là **case probe HTTP** — mở rộng `deploy/dev/seed/ci_http_transport_probe.py`, **không** đụng inventory 99 method. **RFC-3 là method unit mới ⇒ cần Owner duyệt** trước khi nâng inventory 99 → 100; **hiện chưa thêm**. RFC-4 là thay đổi code production ⇒ cần Owner/T1C quyết định, **T1B không tự thực hiện** trong vòng này.

> **Ưu tiên đề xuất cho vòng kiểm chứng tiếp theo:** RFC-1 + RFC-6 + RFC-7 nên gộp **một** lần chạy probe (cùng site, cùng fixture) ⇒ chi phí thấp, đóng được cả ba câu hỏi: *có lộ không? lộ ở đường nào? ngoài scope có bị chặn không?* RFC-2 chỉ chạy **nếu** RFC-1 trả lời "có lộ".

### 5.6 Phạm vi việc đã làm trong vòng này

| Hạng mục | Kết quả |
|---|---|
| Sửa code app / test | **KHÔNG** |
| Sửa workflow / probe script | **KHÔNG** |
| Sửa policy / tài liệu T1A / T1C / Owner | **KHÔNG** |
| Thêm method thứ 100 | **KHÔNG** — inventory vẫn 99 (AST recheck: `gate==source: True`, `source-only=[]`) |
| Merge PR | **KHÔNG** |
| Tài liệu T1B đã cập nhật | `deploy/dev/evidence/t1b-fail-method-review-2026-10-23.md` (§0, §0.1, §0.2, §5) — bảo toàn toàn bộ nội dung trước đó |
| Bổ sung tĩnh trong vòng này | §5.2.1 (endpoint là **đường chính của UI**: `Activities.vue` L540; UI render `file_url` ở `AttachmentArea.vue` L16/L93) · §5.2.2 (đường lộ thứ hai: `docinfo.attachments` — `load.py` L128/L147/L206) · §5.2.3 (**ranh giới quyền**: L24/L181 `has_permission` ⇒ không phải IDOR) · §5.2.4 (so sánh 2 chuỗi dùng chung hàm upstream L321) · RFC-6/RFC-7 |
| Trạng thái giữ nguyên | **SEC-26 `PARTIAL`** · **G0-07 `PARTIAL` / CHƯA KÝ** · **P0 `NO-GO`** · Q1–Q4 + RA-04 chờ Owner |

---

## 6. Tổng hợp trạng thái

| Hạng mục | Trạng thái | Owner | Hành động lần này |
|---|---|---|---|
| Q1 (4 canary) | `OPEN` — chờ Owner; **thêm** blocker drift Q2 | T0/Owner | Giữ nguyên canary |
| Q2 (3 canary) | `OPEN` — chờ Owner; quan sát drift đã có runtime | T0/Owner | Giữ nguyên canary |
| Q3 (1 canary) | `OPEN` — chờ Owner; phụ thuộc Q2 | T0/Owner | Giữ nguyên canary |
| Q4 (chung với Q2) | `OPEN` — chờ Owner | T0/Owner | Giữ nguyên canary |
| RA-04 (1 canary) | `OPEN` — chờ ADR-004 §2.2 | T0/Owner | Giữ nguyên canary |
| `T1B-ext+1` (Dynamic Link) | `OPEN` — static-only, chưa có runtime evidence | Owner/T1C | Không sửa code |
| `T1B-ext+2` (ToDo/Comment) | `OPEN` — static-only, chưa có runtime evidence | **T1C rà tiếp** | Không sửa code |
| `T1B-ext+3` (drift pin Q2) | `OPEN` — quan sát có runtime; cơ chế là giả thuyết | T0/Owner | Ghi nhận, không sửa |
| **`RFC` §5 (`get_activities` / SEC-26)** | **`OPEN`** — source tĩnh xác minh chuỗi nội bộ **không** qua wrapper redaction; **2** đường lộ `file_url`; **0** runtime evidence cho `get_activities`; **KHÔNG** kết luận có/không có vulnerability | **Owner/T1C** | Ghi nhận RFC; **không** sửa code, **không** thêm method thứ 100 |

**Trạng thái thoát (exit status):**

| Mốc | Trạng thái | Lý do |
|---|---|---|
| **G0-07** | **`PARTIAL` / `CHƯA KÝ`** | 91/99 PASS xác minh từ artifact run `#39976477275` (artifact ID `12512295421`, SHA-256 `f17dd94f…`); `gate_passed=false` vì 8 canary chưa có quyết định Owner; `T1B-ext+1/+2/+3` + `RFC` §5 đều `OPEN` |
| **P0** | **`NO-GO`** | G0-07 chưa ký ⇒ P0 chưa đủ điều kiện thoát |
| **SEC-26** | **`PARTIAL`** | Remediation có + runtime evidence **chỉ** cho endpoint đã override (`guarded_crm.get_attachments`, `guarded_crm.get_linked_docs_of_document`); phạm vi phủ chưa đủ — xem RFC §5 |

**Kết luận:** 8/99 method FAIL vẫn là **canary chờ Owner** (Q1–Q4 + RA-04), `gate_passed=false`. PR #2 **giữ mở, không merge**. Lần cập nhật này **không sửa** assertion, **không** sửa policy, **không** sửa upstream code, **không** thêm method thứ 100, **không** thay đổi mức độ rủi ro.

**Phối hợp:** `T1C` rà tiếp `T1B-ext+2` (ToDo/Comment). `T1A` chờ `T1C` cập nhật rồi đồng bộ báo cáo P0.