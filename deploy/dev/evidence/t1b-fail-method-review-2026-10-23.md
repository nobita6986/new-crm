# T1B — Rà từng method FAIL/ERROR (run xác nhận 3 `#39895414507`)

**Ngày:** 2026-10-23 · **Phạm vi:** T1B (test-owned) · **PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) — `open`, **KHÔNG merge**
**Artifact:** run [`#39895414507`](https://github.com/nobita6986/new-crm/actions/runs/39895414507) · head `e3182ba` · PR merge `1678a973` · zip SHA-256 `b7b8ae4982be04bf2b3949f2513870a49c88055be3990d7a6833cdc2ceaac800`
**Nguồn đọc:** `test_docshare_lifecycle.log` · `test_docshare_lifecycle.junit.xml` · `test_bt1c5_matrix_gaps.junit.xml` · `g007-per-method-gate.json` (đã kiểm chứng 3 tầng ở `t1b-g007-method-triage-2026-10-23.md` §2.1)

> **Lưu ý về nguồn SHA:** các đường dẫn dòng cho **Frappe** (`d2efa796…`) trích từ file tải trực tiếp từ GitHub API tại SHA đã ghim trong lần rà này. Tài liệu gốc của `test_docshare_lifecycle.py` header ghi `assign_to.py L104-118` / `share.py L47-83` — **đã đối chiếu và khớp** với nội dung tại SHA này (`_add` L104-118; `add_docshare` L47-83). Các đường dẫn dòng cho **CRM v1.87.0** (`0d3910b4…`) trích từ bản copy trong `deploy/dev/evidence/logs/upstream-crm-v1.87.0/` đã có sẵn từ lần trước.

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

### 1.2 Phát hiện mới trong lần rà này — **7 canary có CÙNG một nguyên nhân gốc**

Bảng FAIL trên cho thấy 2 canary FAIL ngay ở assert (PIN) Q2, còn **5 canary FAIL ở tiền đề thiết lập** `assertIsNotNone(self._share_for(c))` (L627, L653, L697, L768, L819). Cả 7 đều dùng chung một bước thiết lập: `self._assign(self.users["c"])`.

**Bằng chứng runtime** (`test_docshare_lifecycle.log`, dòng 8-15):

```
[DocShare characterization] after assign_agent (assignee ngoài scope) — DocShare: None
[DocShare characterization] after assign_agent — cờ quyền DocShare: None
[DocShare characterization] after assign_agent (assignee trong scope) — DocShare: []
```

Nghĩa là `frappe.desk.form.assign_to.add()` **không tạo `DocShare` nào cho user `c`** (ngoài scope), trong khi pin Q2 ghi `current_behavior` là *"tạo `DocShare` khi assignee chưa có quyền"* ⇒ **hành vi thực tế KHÔNG khớp với pin ⇒ đây là DRIFT so với pin, không phải canary "đang chờ" đơn thuần**.

**Đường gọi và điểm chặn (đọc source tại SHA đã ghim):**

1. `test._assign()` (L446) → `frappe.desk.form.assign_to.add({...})` → `_add(args, ignore_permissions=False)` (`assign_to.py` L55-58).
2. ToDo insert (`assign_to.py` L85-98) với `ignore_permissions=True`.
3. `crm.api.todo.after_insert` (`crm/api/todo.py` L19-25) — hook upstream `doc_events["ToDo"]["after_insert"]` (`crm/hooks.py` L244-248) — chạy **ngay sau** ToDo insert và gọi `frappe.db.set_value("CRM Lead", reference_name, "lead_owner", doc.allocated_to)`.
4. `assign_to.py` L106-107: `if not frappe.has_permission(doc=doc, user=assign_to):`
5. … nhánh này **không** được chạy ⇒ `frappe.has_permission("CRM Lead", doc=lead, user="c")` đã trả **True**.

**Vì sao `c` lại "có quyền" sau bước 3 — ứng viên chính, CHƯA xác minh bằng runtime:** `crm_cskh/hooks.py` L147-156 đăng ký `permission_query_conditions` cho CRM Lead/Deal (và CRM v1.87.0 `crm/hooks.py` L135-139 đăng ký thêm `crm.permissions.org_hierarchy.get_lead_permission_query_conditions`). `has_controller_permissions` (`frappe/permissions.py` L455-473) chạy hook `has_permission` **trước**, và các hook này chỉ trả về `bool` theo điều kiện cây phân quyền. Điểm cần Owner xác minh: **liệu `crm_cskh.permissions.lead_has_permission` có thực sự chặn được sau khi ToDo đã gán**, hay để nhánh "gán việc" của upstream làm record rơi vào scope.

> **Mức độ bằng chứng: TĨNH, CHƯA CÓ RUNTIME CONFIRMATION.** Đây là **hypothesis cần kiểm chứng**, không phải kết luận. Việc xác minh cần thêm một method mới vào inventory 99 ⇒ **cần Owner duyệt** (xem `T1B-ext+3`, §3).

**Hệ quả nếu hypothesis đúng:** nếu `assign_agent` khiến assignee **ngoài scope** trở thành **có quyền đọc** record, thì đây là **mở rộng scope ngoài ý muốn** — và câu hỏi Q2 của Owner cần được trả lời **theo hướng khác** so với giả định ban đầu trong pin (pin giả định Frappe sẽ tạo share `read=1`; runtime cho thấy **không** có share ⇒ thay vào đó quyền đến từ đường khác). Vì vậy **giữ nguyên** toàn bộ canary là đúng: **chưa đủ bằng chứng** để kết luận.

### 1.3 Method RA-04 — chi tiết

| Thuộc tính | Giá trị |
|---|---|
| Failure | `Lists differ: ['crm.api.doc.remove_assignments', 'crm.api.doc.get_assigned_users'] != []` |
| Assertion | `test_bt1c5_matrix_gaps.py` L679-684: `assertEqual(reassign_routes, [])`, với `reassign_routes` lọc `override_whitelisted_methods` theo token `reassign`/`transfer_ownership`/`assign` (L674-678). Hai assert `assertFalse(hasattr(permissions, "reassign_lead"))` / `("transfer_lead_ownership")` (L665-672) **đã PASS** ⇒ tên hàm thực sự không tồn tại |
| Ý nghĩa | Canary khẳng định **ownership service chưa tồn tại**. Hai route bị bắt là **substring match** trên token `assign` — chúng là bản **fail-closed** đã được `crm_cskh` override (`hooks.py` L187, L189), **không** phải ownership service thật |
| Blocker | `hooks.py` không đăng ký endpoint reassign nào đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction (ADR-004 §2.2) |
| Quyết định Owner | Chốt có/không tạo ownership service; nếu **có** ⇒ viết service + đổi pin thành assert "service tồn tại và đồng bộ 3 bảng"; nếu **không** ⇒ ADR-004 §2.2 ghi nhận owner chấp nhận trạng thái hiện tại và pin được giữ **vĩnh viễn** dưới dạng characterization |
| Mức độ | **Đúng như kỳ vọng** — canary không đổi |

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

- **`ToDo`:** có `All` ⇒ **mọi** user (kể cả `b`, `Sales User`) đều có quyền `read` ở **tầng doctype**. Vì vậy `frappe.has_permission("ToDo", "read", doc)` với `doc` cho tới `get_doc_permissions` → `has_controller_permissions` (`frappe/permissions.py` L219-221, L455-473) → hook `crm_cskh.permissions.linked_lead_has_permission` (hooks.py L166), vốn **trả `None`** khi user **có** quyền đọc Lead ⇒ **không** chặn. Kết quả: ToDo đọc được **vì** quyền doctype `All`, **không phải** vì stale-access.
- **`Comment`:** chỉ `System Manager` / `Website Manager` ⇒ user `b` (`Sales User`) **không** có quyền doctype ⇒ `get_doc_permissions` trả `{ptype: 0}`; nhánh share fallback (`frappe/permissions.py` L189-191) cũng không có DocShare ⇒ **deny**. Comment đọc được **vì** thiếu quyền doctype, **không phải** vì stale-access.

⇒ **Hai doctype cho hai lý do KHÁC NHAU**, cùng biểu hiện "PermissionError". Đây chính là điểm cần tách bạch; tuyệt đối **không** diễn giải thành "đã chặn đúng stale access".

### 3.3 Dữ liệu có thể lộ

Ở fixture hiện tại: **không phát hiện rò rì** — `b` bị chặn ở **tầng doctype**, tức chặn **mạnh hơn** (chặn luôn ToDo hợp lệ trong scope nếu DocPerm đổi). Đây là **fail-closed** do cấu hình DocPerm upstream, **không phải** guard cố ý của `crm_cskh`.

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

**Trạng thái: `OPEN` — mới phát hiện trong lần rà này (§1.2).**

| Thuộc tính | Nội dung |
|---|---|
| Vấn đề | Pin Q2 ghi `current_behavior` = *"assign_to.add tạo DocShare khi assignee chưa có quyền"*, nhưng runtime cho thấy **không có DocShare nào được tạo** |
| Tác động | 7/7 canary DocShare FAIL; Q1/Q3 **không thể đánh giá được** vì tiền đề thiết lập (theo Q2) chưa đạt |
| Bằng chứng | Runtime: `after assign_agent (assignee ngoài scope) — DocShare: None`. Tĩnh: `assign_to.py` L106-107, `crm/api/todo.py` L19-25, `crm/hooks.py` L135-139 + `crm_cskh/hooks.py` L147-167 |
| Mức độ | **CHƯA xác minh** — chỉ có static analysis, **không** có runtime confirm |
| Test cần | Method xác minh `frappe.has_permission("CRM Lead", doc=lead, user="c")` ngay **trước** và **sau** `assign_agent`, đồng thời đọc `lead_owner` trước/sau (vì `crm/api/todo.py` L22-25 ghi đè `lead_owner` bằng `allocated_to`) |
| Ràng buộc | Method mới ⇒ **cần Owner duyệt** inventory 99 |
| Owner | **Owner/T0** (quyết định Q2) + T1B (viết test sau khi được duyệt) |

> **Không tự ý sửa pin Q2.** Pin sai về mặt mô tả là việc của Owner: nếu hành vi thực tế (không tạo share) là **đúng ý**, thì Q2 cần viết lại theo hướng đó và assertion đổi tương ứng — đây là **quyết định policy**, không phải sửa test.

---

## 5. Tổng hợp trạng thái

| Hạng mục | Trạng thái | Owner | Hành động lần này |
|---|---|---|---|
| Q1 (4 canary) | `OPEN` — chờ Owner; **thêm** blocker drift Q2 | T0/Owner | Giữ nguyên canary |
| Q2 (3 canary) | `OPEN` — chờ Owner; phát hiện **drift** | T0/Owner | Giữ nguyên canary; ghi nhận drift |
| Q3 (1 canary) | `OPEN` — chờ Owner; phụ thuộc Q2 | T0/Owner | Giữ nguyên canary |
| Q4 (chung với Q2) | `OPEN` — chờ Owner | T0/Owner | Giữ nguyên canary |
| RA-04 (1 canary) | `OPEN` — chờ ADR-004 §2.2 | T0/Owner | Giữ nguyên canary |
| `T1B-ext+1` (Dynamic Link) | `OPEN` — static-only, chưa có runtime evidence | Owner/T1C | Không sửa code |
| `T1B-ext+2` (ToDo/Comment) | `OPEN` — static-only, chưa có runtime evidence | Owner/T1C | Không sửa code |
| `T1B-ext+3` (drift pin Q2) — **mới** | `OPEN` — mới phát hiện, chưa xác minh | T0/Owner | Ghi nhận, không sửa |

**Kết luận:** 8/99 method FAIL vẫn là **canary chờ Owner**, `gate_passed=false`, **G0-07 / P0 vẫn `NO-GO`**. PR #2 **giữ mở, không merge**. Lần rà này **không sửa** assertion, **không** sửa upstream code, **không** thay đổi mức độ rủi ro.