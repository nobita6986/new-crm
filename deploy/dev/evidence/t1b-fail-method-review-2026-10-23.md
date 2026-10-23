# T1B — Rà từng method FAIL/ERROR (run mới nhất `#39900046839`)

**Ngày:** 2026-10-23 · **Phạm vi:** T1B (test-owned) · **PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) — `open`, **KHÔNG merge**
**Artifact (run mới nhất):** run [`#39900046839`](https://github.com/nobita6986/new-crm/actions/runs/39900046839) · head `8613e86` · PR merge `8dfa1b7f` · zip SHA-256 `d394ae92f6fb54a6bb8196c04605eb8766ca27d881f55ea42eb683438072c682` (131.728 bytes — **khớp** digest GitHub API)
**Nguồn đọc:** `test_docshare_lifecycle.log` · `test_docshare_lifecycle.junit.xml` · `test_bt1c5_matrix_gaps.junit.xml` · `g007-per-method-gate.json` — tất cả thuộc artifact của run `#39900046839`

> **Lưu ý về nguồn SHA:** các đường dẫn dòng cho **Frappe** (`d2efa796…`) trích từ file tải trực tiếp từ GitHub API tại SHA đã ghim trong lần rà này. Tài liệu gốc của `test_docshare_lifecycle.py` header ghi `assign_to.py L104-118` / `share.py L47-83` — **đã đối chiếu và khớp** với nội dung tại SHA này (`_add` L104-118; `add_docshare` L47-83). Các đường dẫn dòng cho **CRM v1.87.0** (`0d3910b4…`) trích từ bản copy trong `deploy/dev/evidence/logs/upstream-crm-v1.87.0/` đã có sẵn từ lần trước.

---

## 0. Xác minh counts từ artifact của run `#39900046839`

**Không** suy ra từ nhãn "docs-only" của commit `8613e86`. Ba tầng đọc độc lập:

| Tầng | Phương pháp | Kết quả |
|---|---|---|
| **A. Gate JSON** | Đọc `g007-per-method-gate.json`, tự đếm lại `results[]` | `counts(declared)={PASS:91, FAIL:8}`; `counts(recomputed)={PASS:91, FAIL:8}`; tổng 99; `passing_total=91`; `gate_passed=false` |
| **B. 5 JUnit XML** | Parse lại `<testcase>` bằng `ElementTree`, **không** tin gate | `bt1c5_matrix_gaps` 27 (1 FAIL) · `docshare_lifecycle` 10 (7 FAIL) · `permissions` 18 (0) · `security_remediation` 12 (0) · `security_remediation_v2` 32 (0) ⇒ **99 `<testcase>`** |
| **C. Inventory nguồn** | AST quét `apps/crm_cskh/crm_cskh/tests/test_*.py` | **99** method `test_*` trong class `Test*` |

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
| `run_id` | `39900046839` (khớp `workflow-metadata.json`) |
| `head_sha` (branch) | `8613e86d03edec0273756a38e7b852d15b3d29b3` |
| `commit_sha` (artifact) | `8dfa1b7f8ebd61493d3bdef7d4952605f92141be` = **PR merge commit tổng hợp** (`refs/pull/2/merge`), **không** phải head branch |
| `frappe_sha` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` (v15.123.1) |
| `crm_sha` | `0d3910b456ef4fb913e77b29db011b3debc4f778` (v1.87.0) |
| Artifact zip SHA-256 | `d394ae92f6fb54a6bb8196c04605eb8766ca27d881f55ea42eb683438072c682` — **khớp chính xác** digest GitHub API |
| Content digest (path + SHA-256 từng file) | `5013a4093d562ba2898c69a98441b2cbcea47b0533c005d8c0f25006d12466ef` |
| `g007-per-method-gate.json` SHA-256 | `196ba75cd27720c9226a7ed67427aba2e6efb124363fc7acc62c447649770637` |
| `http-transport.json` SHA-256 | `60f1615efc534c96c276ec1da9382e2dc43aae4add07fdba34fe5c59ac6f7292` |
| `test_docshare_lifecycle.junit.xml` SHA-256 | `8dc1b6335f226d6168468710f93d5abc8cb806b323c16e33c7b3ed9371ffcbd2` |
| `test_docshare_lifecycle.log` SHA-256 | `cd838faf1360c6915db908a572680d034f38d195b70a2bbd17495526bdf42d1e` |
| `test_bt1c5_matrix_gaps.junit.xml` SHA-256 | `f2982215e491fb6a93d49c6b9ea5ba501e98166f73b656c1f0b525a8b63de9d4` |
| `test_permissions.junit.xml` SHA-256 | `e8dc36df7c81398f06f641d25e39b40afce76b788f34da9a10b0d870ff49c4cd` |
| `test_security_remediation.junit.xml` SHA-256 | `3538cd68ab0cb07126f8aa35a7d200c6d08a2046158dcb7a67f9e875f4a1aa19` |
| `test_security_remediation_v2.junit.xml` SHA-256 | `da354397b3ccddc187c2ed5062944d3de2e208ab55c4f371b6800b10877a9702` |
| `workflow-metadata.json` SHA-256 | `3e137ef4f4c962737facc4da45fc0475f894687f0e76a54f7ba1d022742c07c5` |

**Workflow outcomes:** `bootstrap=success` · `lint=success` · `seed=success` · `per_method=failure` · `per_method_gate=failure` · `http_transport=success` · `backup_restore=success` · `redaction_replacements=4`.

**Ghi chú về tính xác định của gate JSON:** `g007-per-method-gate.json` của run này **byte-identical** với run `#39895414507` (`196ba75c…`). File này **không** chứa commit SHA hay timestamp, và `results[]` chỉ ghi `module` / `class` / `method` / `status` / `source_xml` / `raw_name` / `match_reason` ⇒ khi test code không đổi, byte-identical là **kết quả mong đợi**, không phải dấu hiệu artifact bị cache lại. Đây vẫn là bằng chứng **độc lập** vì các file XML/log của run này có SHA-256 **khác** run trước.

> **Kết luận:** run `#39900046839` **tái lập** 91 PASS / 8 FAIL / 0 ERROR / 0 SKIP, HTTP 14/14 — **đã xác minh từ artifact**, không phải suy luận từ nhãn commit.

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

**Nguồn:** artifact của run [`#39900046839`](https://github.com/nobita6986/new-crm/actions/runs/39900046839) (head `8613e86`), file `p0-g007-evidence-39900046839/test_docshare_lifecycle.log`, SHA-256 `cd838faf1360c6915db908a572680d034f38d195b70a2bbd17495526bdf42d1e`.

Ba dòng do helper `observe()` in ra (test_docshare_lifecycle.py L293-298):

```
[DocShare characterization] after assign_agent (assignee ngoài scope) — DocShare: None
[DocShare characterization] after assign_agent — cờ quyền DocShare: None
[DocShare characterization] after assign_agent (assignee trong scope) — DocShare: []
```

**Điều quan sát được:** sau `frappe.desk.form.assign_to.add()` với assignee user `c` (Team 2, **ngoài scope**), `_share_for(c)` trả `None` — tức **không tồn tại bản ghi `DocShare` nào** cho user `c` trên Lead đó. Dòng thứ ba cho thấy nhánh "assignee trong scope" trả `[]` (đúng kỳ vọng của Q2 nhánh bù, method này **PASS**).

**Đối chiếu với pin:** `PINNED_BEHAVIOR["Q2"]["current_behavior"]` ghi *"tạo `DocShare` khi assignee chưa có quyền"* (registry in ra trong cùng log). Quan sát `None` **không khớp** với mô tả đó ⇒ đây là **drift giữa pin và hành vi quan sát được**.

> **Phạm vi của quan sát:** log chỉ ghi **kết quả** (`DocShare: None`), **không** ghi giá trị `frappe.has_permission(...)` hay `lead_owner` tại các thời điểm trước/sau. Vì vậy quan sát **không** tự chứng minh được *vì sao* không có share. Việc suy luận cơ chế là mục §1.2.2 và **mang nhãn giả thuyết**.

**Tính lặp lại:** quan sát `DocShare: None` xuất hiện **giống hệt** trong **cả 6 run** đã tải (`#39885152649`, `#39892127563`, `#39893839646`, `#39894784269`, `#39895414507`, `#39900046839`) ⇒ hiện tượng **tái lập xuyên suốt**, không phải nhiễu của một lần chạy.

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

**Nguồn:** run `#39900046839`, `test_bt1c5_matrix_gaps.junit.xml` (SHA-256 `f2982215e491fb6a93d49c6b9ea5ba501e98166f73b656c1f0b525a8b63de9d4`), testcase `TestRA04CrmAdminReassignBlocked::test_ra04_ownership_service_does_not_exist_yet`, timestamp `2026-10-23T11:27:52`.

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

**Đánh giá: hai route này có liên quan gì đến ownership service không? → KHÔNG.**

| Tiêu chí | Đánh giá |
|---|---|
| Có phải ownership service không? | **Không.** Cả hai đã được `crm_cskh` **override** thành bản **fail-closed**: `crm.api.doc.remove_assignments` → `crm_cskh.permissions.remove_assignments` (`hooks.py` L187, guard `_is_system` tại `permissions.py` L165-170); `crm.api.doc.get_assigned_users` → `crm_cskh.api.guarded_crm.get_assigned_users` (`hooks.py` L189, `_check_read` + lọc `user_in_scope`). Không route nào đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction |
| Vì sao bị bắt? | **Substring match**, không phải ngữ nghĩa. Token `assign` khớp trong cả `remove_assignments` **và** `get_assigned_users` ⇒ test bắt nhầm hai endpoint có tên chứa "assign" nhưng **không** phải reassign endpoint |
| Hai assert kèm theo | `assertFalse(hasattr(permissions, "reassign_lead"))` (L665-668) và `assertFalse(hasattr(permissions, "transfer_lead_ownership"))` (L669-672) — **PASS** ⇒ tên hàm ownership service thực sự **không** tồn tại trong `crm_cskh.permissions` |
| Kết luận | Canary **đúng nghĩa**: ownership service **chưa** tồn tại. Cơ chế phát hiện có **độ nhạy thấp** (substring) nên FAIL do route không liên quan — nhưng đây là **vấn đề độ chính xác của phép kiểm**, **không** phải phát hiện lỗ hổng |
| Mức độ | Canary giữ nguyên chức năng; **không** diễn giải thành lỗi bảo mật đã xác nhận |

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

> **Cách diễn đạt bắt buộc:** `PermissionError` quan sát được ở đây là **bằng chứng chưa đủ** để kết luận **có** hay **không có** vulnerability:
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
| Quan sát (runtime, **đã xác minh**) | Run [`#39900046839`](https://github.com/nobita6986/new-crm/actions/runs/39900046839), `test_docshare_lifecycle.log` (SHA-256 `cd838faf…`): `after assign_agent (assignee ngoài scope) — DocShare: None`. Giống hệt ở **cả 6 run** đã tải ⇒ **tái lập xuyên suốt** |
| Đối chiếu pin | `PINNED_BEHAVIOR["Q2"]["current_behavior"]` = *"assign_to.add tạo DocShare khi assignee chưa có quyền"* ⇒ quan sát **không khớp** mô tả |
| Tác động | 7/7 canary DocShare FAIL; Q1/Q3 **không đánh giá được** vì tiền đề thiết lập (theo Q2) chưa đạt |
| **Giả thuyết cơ chế (chưa xác minh)** | H1 `lead_owner` bị `crm/api/todo.py` L22-25 ghi đè thành `c` · H2 nhánh ToDo của `crm.permissions.org_hierarchy._permission_query_conditions` L42-51 cho phép đọc · H3 tương tác hai hệ scope. Chi tiết tại §1.2.2. **Cả ba đều chưa được phân biệt bằng runtime** |
| Mức độ | Quan sát = **runtime evidence**. Cơ chế = **static hypothesis**. **Không** kết luận có/không có lỗ hổng scope |
| Test cần | Method xác minh `frappe.has_permission("CRM Lead", doc=lead, user="c")` ngay **trước** và **sau** `assign_agent`, đồng thời đọc `lead_owner` trước/sau, để phân biệt H1 với H2/H3 |
| Ràng buộc | Method mới ⇒ **cần Owner duyệt** trước khi nâng inventory 99 → 100. **Hiện chưa thêm** |
| Owner | **Owner/T0** (quyết định Q2) + T1B (viết test sau khi được duyệt) |

> **Không tự ý sửa pin Q2.** Pin sai về mặt mô tả là việc của Owner: nếu hành vi thực tế (không tạo share) là **đúng ý**, thì Q2 cần viết lại theo hướng đó và assertion đổi tương ứng — đây là **quyết định policy**, không phải sửa test.

---

## 5. Tổng hợp trạng thái

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

**Trạng thái thoát (exit status):**

| Mốc | Trạng thái | Lý do |
|---|---|---|
| **G0-07** | **`PARTIAL` / `CHƯA KÝ`** | 91/99 PASS xác minh từ artifact run `#39900046839`; `gate_passed=false` vì 8 canary chưa có quyết định Owner; `T1B-ext+1/+2/+3` đều `OPEN` |
| **P0** | **`NO-GO`** | G0-07 chưa ký ⇒ P0 chưa đủ điều kiện thoát |

**Kết luận:** 8/99 method FAIL vẫn là **canary chờ Owner** (Q1–Q4 + RA-04), `gate_passed=false`. PR #2 **giữ mở, không merge**. Lần cập nhật này **không sửa** assertion, **không** sửa policy, **không** sửa upstream code, **không** thêm method thứ 100, **không** thay đổi mức độ rủi ro.

**Phối hợp:** `T1C` rà tiếp `T1B-ext+2` (ToDo/Comment). `T1A` chờ `T1C` cập nhật rồi đồng bộ báo cáo P0.