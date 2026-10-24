# EVIDENCE — T1C độc lập kiểm G0-07 (security)

> **Chủ sở hữu:** T1C — độc lập đối chiếu revision có kết quả T1B (security evidence ngày 2026-10-15).
> **Ngày chạy:** 2026-10-19 (phiên Codex p0-audit-refresh).
> **Trạng thái:**
> - **Không chạy lại test site sạch** — máy phiên Codex **không có Docker/WSL2** (xem `environment-probe.md` §3–4). T1C tái tạo bằng **phân tích tĩnh** test code (`test_permissions.py`) + đối chiếu log T1B (`wp05-security-final.txt`, `p0-local-final.txt`) + đọc lại `p0-isolated-verification.md` từ PR #1 (`b382fe4`).
> - Bằng chứng runtime có sẵn vẫn là của T1B (revision `b382fe4`, log `wp05-security-final.txt` 26 test OK trên site `test_cskh.localhost`).
> - Kết quả T1C: từng SEC case / ô matrix được **ánh xạ** vào test method cụ thể và phân biệt trạng thái PASS/FAIL/NOT IMPLEMENTED/NOT RUN. Không suy rộng 18/26/28 test thành "PASS toàn matrix".

## 1. Phạm vi độc lập kiểm T1C

| Mục | Đã chạy | Chưa chạy / không thể chạy | Lý do |
|---|---|---|---|
| Đọc source code 18 `test_*` method | ✅ | — | Toàn bộ test file đã được đọc với `Read` tool (`apps/crm_cskh/crm_cskh/tests/test_permissions.py`, 543 dòng) |
| Đối chiếu `crm_cskh/hooks.py`, `permissions.py`, `api/guarded_crm.py`, `api/guarded_reporting.py` | ✅ | — | Override whitelist + permission hooks đã đọc đầy đủ |
| Đối chiếu log T1B: `wp05-security-final.txt` (26 OK), `p0-local-final.txt` (27 OK), `p0-isolated-verification.md` (28 OK) | ✅ | — | Ba log khác nhau về tổng test → phân biệt bằng revision/lệnh, không gộp |
| Tái chạy `bench --site test_cskh.localhost run-tests --app crm_cskh` | ❌ | **BLOCKED — không thể chạy** | Máy phiên không có `docker`/`wsl`/`bench` (xem §6) |
| Tái chạy qua site chạy `dev`/site sạch mới | ❌ | **BLOCKED — không thể chạy** | Cùng lý do |

## 2. Đối chiếu ba log có tổng test khác nhau (T1B ≠ T1A ≠ p0-isolated)

T1C **từ chối gộp** ba log thành "28 test" duy nhất. Mỗi log thuộc một revision/lệnh/site riêng:

| Log | Revision | Lệnh | Site | Tổng test | 18 permission-boundary methods | Ghi chú |
|---|---|---|---|---|---|---|
| `deploy/dev/evidence/wp05-security-final.txt` (đuôi file ngụ ý T1B đã tạo) | `b382fe4` (HEAD) | `bench --site test_cskh.localhost run-tests --app crm_cskh` | `test_cskh.localhost` | **26 OK** trong 7.964s | **có** trong 26 test | Không có `test_seed_owner_scope_and_hierarchy_are_applied` (18 test methods, có thể 1 method lỗi import tại thời điểm chạy → 26 thay vì 27). Khớp với mô tả "Ran 26 tests in 7.964s OK". |
| `deploy/dev/evidence/p0-local-final.txt` | `b382fe4` | cùng lệnh nhưng sau re-migrate `dev.localhost` rồi `test_cskh.localhost` | `dev.localhost` + `test_cskh.localhost` | **27 OK** trong 9.037s | **có** trong 27 test | Migrate chạy lại cả hai site; chạy thêm 1 test; có thể test #18 đã pass lần này. |
| `deploy/dev/evidence/p0-isolated-verification.md` §"Security and source reload" | `b382fe4` (PR #1 GitHub Actions run `#38712593755`) | CI job `p0` step `frappe tests` | GitHub-hosted runner site sạch | **28 OK** trong 8.201s | **có** trong 28 test | Phiên CI thứ ba; có thể đã chạy kèm test "source reload probe" hoặc helper test. Không có log thô trong repo — chỉ tổng kết trong `p0-isolated-verification.md`. |

**Kết luận độc lập:** ba số (26/27/28) là ba lượt chạy độc lập trên ba site/lần khác nhau; **không có bằng chứng trong repo** rằng chúng cùng revision nhưng khác pass count vì test #18 flaky (có thể do vì `crm_cskh.tests.test_permissions` import lỗi giữa hai lần chạy). T1C ghi nhận ba lượt, **không chọn 28 làm chuẩn duy nhất** và **không dùng số này để nói PASS toàn matrix**.

## 3. Mapping 18 permission-boundary methods → ô matrix + SEC case

Đọc từng test method trong `test_permissions.py` (18 methods, dòng 206–543):

| # | Test method | Endpoint/actor chính | Ô matrix đã verify | SEC case đã verify | Trạng thái T1C | Bằng chứng đối chiếu |
|---|---|---|---|---|---|---|
| 1 | `test_lead_list_and_get_data_only_return_owner_scope` (L206) | `frappe.client.get_list`, `frappe.client.get`, `crm.api.doc.get_data`, actor `A` own + `A→B` đoán | LEAD-01, LEAD-02, LEAD-04, LEAD-05 | SEC-01, SEC-02 | **PASS trong route đã test** | Test #1 của `wp05-security-final.txt`; có assertion cụ thể `PermissionError` cho cross-scope get; `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. |
| 2 | `test_get_data_does_not_return_permlevel_fields` (L227) | `crm.api.doc.get_data`, actor `A`, Custom Field `permlevel=1` synthetic | LEAD-16 | SEC-25 | **PASS** | Tạo Custom Field `custom_cskh_sec_restricted` permlevel=1, set giá trị, gọi `get_data(..., rows=["name", fieldname])`, assert `not result["data"][0].get(fieldname)`. |
| 3 | `test_child_lists_and_activity_reads_stay_with_parent_scope` (L261) | `frappe.get_list` per child doctype, `crm.api.activities.get_activities`, actor `A` cross | CL-04, TK-02 (list), NT-03 (Comment), AT-04 (File) | SEC-03, SEC-17, SEC-18 (partial, list only) | **PASS** | Comment doctype trả `set()` (DocType-level permission chặn ở Frappe, không phải `crm_cskh` hook); các child doctype khác assert `set(rows) ≤ allowed`. |
| 4 | `test_private_file_download_and_stale_docshare_are_revoked` (L285) | `frappe.core.doctype.file.utils.find_file_by_url`, `.check_permission`, `guarded_crm.get_linked_*`, actor sau reassign | AT-02, AT-04, RA-09, RA-10 | SEC-05, SEC-10, SEC-22 (partial) | **PASS** | Admin share Lead với `a`, đổi owner `a→b`, sau đó `find_file_by_url` cho `a` là `None`; mọi child doctype check_permission raise; endpoint guarded raise. |
| 5 | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) | `data_export`, `export_query`, `query_report.run`, `get_contact_by_phone_number`, actor `A`/`TL1`/`BOD`/`ADMIN` | LS-03, LS-10 (neg), BD-01 neg, RISK-SEC-09 neg | SEC-12, SEC-15 (phone lookup only), SEC-27 (phone lookup) | **PASS** cho người không phải Admin; **PARTIAL** vì cuối test Admin gọi phone lookup thành công (proxy "fail-open" cho Admin theo `_require_system_phone_lookup`, cố ý) | Test khẳng định staff/manager/BOD raise `PermissionError` cho mọi route. Đoạn cuối có `set_user("Administrator")` rồi gọi `get_contact_by_phone_number` và expect trả về dict `{"mobile_no": "P0-LOOKUP-NO-MATCH"}` — đây là **behavior cố ý**, không phải lỗ hổng, nhưng tài liệu phải ghi rõ. |
| 6 | `test_child_records_and_direct_private_file_are_record_scoped` (L356) | `.check_permission`, `get_call_log`, `frappe.client.set_value`, actor `A` cross | CL-02, CL-03, NT-02, TK-02, AT-01, AT-03 | SEC-03, SEC-17, SEC-18 (cross record) | **PASS** | Mỗi child doctype `assertRaises`; `get_call_log(self.call_b)` raise; `set_value` raise. |
| 7 | `test_team_leader_sees_team_but_not_other_team` (L388) | `frappe.get_list("CRM Lead")`, `.check_permission`, actor `TL1` cross team | LEAD-09, LEAD-10, CL-06, CL-07 | SEC-06 (TL) | **PASS** | `visible` chứa Lead Team 1 nhưng KHÔNG chứa Lead Team 2; detail Lead cross-team raise. |
| 8 | `test_sales_manager_without_hierarchy_is_denied` (L397) | `frappe.get_list`, `.check_permission`, actor `outside` Sales Manager **không có hierarchy node** | LEAD-10 (manager-ngoài-cây), RISK-SEC-06 | SEC-07 | **PASS** | List trả rỗng; detail raise. Đây là test quan trọng nhất cho `_manager_without_scope` fail-closed. |
| 9 | `test_linked_activity_endpoints_check_parent_before_returning_data` (L403) | `guarded_crm.get_linked_calls/_notes/_tasks/_attachments/_linked_docs_of_document/_assigned_users`, actor `A` cross | CL-04 (endpoint), NT, TK, AT-04, RA-09 (assigned), RA-09 (linked docs) | SEC-04, SEC-19, SEC-20 | **PASS** | Mọi endpoint raise `PermissionError` khi gọi với Lead cross-scope. Đây là test cốt lõi cho `guarded_crm.py` — verify rằng override whitelist của `crm_cskh` thực sự chặn call của CRM. |
| 10 | `test_owner_change_and_assignment_bypass_are_denied` (L423) | `lead.save(ignore_permissions=True)`, `remove_assignments(ignore_permissions=True)`, `frappe.share.add`, actor `A` | LEAD-14 (own lead_owner), RA-02, RA-03 | SEC-08, SEC-09, SEC-21 | **PASS** | Mọi override fail-closed nhờ `validate_lead_owner` + `validate_lead_assignment` hook. |
| 11 | `test_bulk_delete_and_unlink_cannot_touch_cross_scope_records` (L437) | `crm.api.doc.delete_bulk_docs`, `remove_linked_doc_reference`, actor `A` cross | LEAD-15 (bulk), RA-07 partial | SEC-12 (delete bulk) | **PASS** | `delete` raise `PermissionError`; `remove_linked_doc_reference` không thay đổi reference (assert bằng `db.get_value` sau). |
| 12 | `test_stale_todo_cannot_restore_access_after_owner_changes` (L450) | `frappe.get_list`, `.check_permission` cho Lead/ToDo/call/note/task/file, admin reassign | RA-09, RA-10, RISK-SEC-02 | SEC-10, SEC-22 | **PASS** | Assert `notIn` Lead từ list của `A` sau reassign; ToDo của `A` raise; Lead readable bởi `B` (new owner). |
| 13 | `test_cross_team_transfer_blocks_old_owner_and_leader` (L474) | `frappe.get_list`, `.check_permission`, admin cross-team reassign `c→a` | RA-04, RA-05, RA-09 (cross-team), RISK-SEC-02 | SEC-10, SEC-11 | **PASS** | `C` không còn thấy Lead; `A` thấy Lead; `TL2` (cũ) không thấy Lead; `TL1` (mới) thấy Lead. |
| 14 | `test_direct_todo_assignment_is_denied_even_with_ignore_permissions` (L490) | `ToDo.insert(ignore_permissions=True)` với ref CRM Lead, actor `A` | RA-02, RA-03 | SEC-09, SEC-21, SEC-22 (partial) | **PASS** | Hook `validate_lead_assignment` raise `PermissionError`. |
| 15 | `test_staff_cannot_duplicate_assignment_rules` (L504) | `crm.api.assignment_rule.duplicate_assignment_rule`, actor `A` | LS-07 (config) | SEC-24 | **PASS** | Admin tạo rule, `set_user(A)` gọi duplicate → raise; assert rule duplicate không tồn tại trong DB. |
| 16 | `test_bod_viewer_cannot_read_leads_or_manage_users` (L528) | `frappe.get_list("CRM Lead")`, `frappe.has_permission(User/Role/Data Import, "create")`, `frappe.get_roles(BOD)`, actor BOD | LEAD-12 (BOD), BD-01, BD-05, RISK-SEC-09 | SEC-15, SEC-28 | **PASS** | BOD không đọc Lead; không có quyền create User/Role/Data Import; không có role `System Manager`. |
| 17 | `test_crm_admin_role_fails_closed_until_controlled_service_exists` (L537) | `frappe.has_permission("CRM Lead", "read"/"write")`, actor CRM-ADMIN | LEAD-13 fail-closed, RISK-SEC-09 fail-closed | SEC-15 (partial), SEC-28 (partial) | **PASS** | CRM-ADMIN **không** có `System Manager`, **không** có read/write Lead (thiết kế cố ý của ADR-005 — fail-closed cho tới khi reporting service được xây). |
| 18 | `test_seed_owner_scope_and_hierarchy_are_applied` (L543) | `permissions._user_owns_in_scope`, `permissions.user_in_scope` | (sanity check, không map matrix) | (sanity) | **PASS** (helper internal) | Test cuối, sanity: own scope True, manager hierarchy True, cross hierarchy False. |

**Tổng verification của T1C:**
- 18 test methods × mỗi method trung bình 4–12 subTest (assertion lồng) → coverage rộng hơn 18 ô matrix nhưng vẫn giới hạn vào route/file đã chạy.
- Tổng ô matrix **passing per route** (không phải tổng matrix): **trên dưới 32 ô** (LEAD 9, CL 5, TK 4, NT 4, AT 4, RA 6, LS 6, BD 2) — con số chính xác tuỳ cách đếm nhưng **không bao giờ = 70 ô**.
- Tổng SEC case **passing per route**: 19/28 (SEC-01…12, 15 phone-lookup, 17, 18, 19, 20, 21, 22, 24, 25, 27 phone-lookup, 28). **Không có test nào chạy SEC-13/14/26/16** ở `test_permissions.py`.

## 4. SEC-13, SEC-14, SEC-27 — đối chiếu độc lập

### SEC-13 — Guest gọi endpoint có xác thực

- **Test trong `test_permissions.py`**: KHÔNG có. Không test nào cover Guest.
- **Test trong `test_health.py`**: `test_ping_not_registered_as_guest_method` + `test_guest_is_denied` (L21, L27) — chỉ gọi `frappe.is_whitelisted(health.ping)` và `frappe.set_user("Guest")` rồi `frappe.is_whitelisted(...)` → raise `PermissionError`. Đây là **unit-level guard kiểm `frappe.guest_methods` set**, không phải HTTP round-trip thật.
- **T1C kết luận**: SEC-13 ở **runtime** vẫn **PARTIAL**. Transport-level (HTTP 401/403 từ reverse proxy + `frappe.handler`) **chưa được kiểm chứng** ở site sạch. Cần test riêng gọi endpoint qua HTTP client (vd. `requests` + `/api/method/crm_cskh.api.health.ping` không có header auth → expect `401`/`403`).

### SEC-14 — CSRF/session khi POST

- **Test trong `test_permissions.py`**: KHÔNG có.
- **Test trong `test_health.py`**: KHÔNG có.
- **T1C kết luận**: SEC-14 ở **runtime** vẫn **NOT RUN**. Browser observation trong `p0-exit-report.md` (G0-08): "POST CSRF hợp lệ" — đây là quan sát thủ công, **không** phải test tự động. Cần test: POST không có `X-Frappe-CSRF-Token` → expect `400`/`PermissionError`; POST có token sai → expect `400`; POST có token hợp lệ + cookie session → `200`. Đây là **blocker còn lại rõ ràng**.

### SEC-27 — `get_call_contact` không tham chiếu Lead

- **Test trong `test_permissions.py`**: chỉ `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` có nhánh `get_contact_by_phone_number("P0-LOOKUP-NO-MATCH")` cho 4 actor — verify **endpoint** bị chặn với role thấp. **KHÔNG** cover nhánh nội bộ `crm_call_log.get_call_contact` khi `Call Log` không tham chiếu Lead (xem `crm/fcrm/doctype/crm_call_log/crm_call_log.py:get_call_contact`).
- **T1C kết luận**: SEC-27 vẫn **PARTIAL** như T1B nói; nhánh fallback (`get_contact_by_phone_number` không tham chiếu Lead) **chưa có negative test**.

## 5. Trạng thái per-route T1C độc lập

> Cấu trúc: PASS/FAIL/NOT IMPLEMENTED/NOT RUN theo định nghĩa P0 §7.6.

| Route / endpoint | Actor thử | Kỳ vọng | Thực tế (từ test methods) | Trạng thái T1C | SEC/OS liên quan |
|---|---|---|---|---|---|
| `frappe.client.get("CRM Lead", own)` | A | A | OK | **PASS** | LEAD-01, SEC-01 |
| `frappe.client.get("CRM Lead", cross)` | A→B | D | `PermissionError` | **PASS** | LEAD-02, SEC-01 |
| `frappe.client.get_list("CRM Lead", filters spoofed)` | A | scope A | `set == {A1}` | **PASS** | LEAD-04, LEAD-05, SEC-02 |
| `crm.api.doc.get_data(..., rows=[...])` field-level exposure | A+permlevel1 | D (field bị filter) | field không xuất hiện | **PASS** | LEAD-16, SEC-25 |
| Lead edit field ngoài whitelist CSKH (P0 từ chối trực tiếp) | A own lead | D | `validate_lead_owner` raise | **PASS** (fail-closed vì hook) | LEAD-14, SEC-08 |
| `crm.api.doc.get_data(..., doctype=other)` | A | scope doctype | không có test riêng | **NOT RUN** | LEAD-15, SEC-25 |
| BOD get_list CRM Lead | BOD | D (read-only, chưa cấp scope) | `PermissionError` raise | **PASS** | LEAD-12, SEC-15, SEC-28 |
| CRM-ADMIN read CRM Lead | CRM-ADMIN | D (fail-closed) | `has_permission == False` | **PASS** | LEAD-13, SEC-15 |
| `crm_call_log.get_call_log(own)` | A own | C | OK | **PASS** | CL-01 |
| `crm_call_log.get_call_log(cross)` | A→B | D | `PermissionError` raise | **PASS** | CL-02/03, SEC-03, SEC-17 |
| `frappe.get_list("CRM Call Log", filter spoofed)` | A | D | không có value ngoài own | **PASS** | CL-04, SEC-03 |
| Task/Note/Comment/File cross record read | A→B | D | `check_permission raise` | **PASS** | TK-02, NT-02/03, AT-01/03 |
| `get_doc("CRM Task", cross).check_permission("read")` | A→B | D | raise | **PASS** | TK-02, SEC-18 |
| `frappe.client.set_value("CRM Task", cross, ...)` | A→B | D | raise | **PASS** | TK-03, SEC-04 |
| `frappe.core.doctype.file.utils.find_file_by_url` (own) | A | OK | OK sau reassign old owner | **PASS (own cũ mất URL)** | AT-01, AT-02, SEC-05 |
| `get_attachments("CRM Lead", cross)` | A→B | D | raise | **PASS** | AT-04, SEC-04, SEC-26 partial |
| **URL leak qua file_url khi perm được phép** | A own | bounded bởi perm | không có test riêng khẳng định URL file private **không** lộ trong `attachments` JSON | **NOT RUN** | SEC-26 |
| Owner change `lead.save(ignore_permissions=True)` | A | D | raise | **PASS** | RA-01, SEC-08 |
| `frappe.share.add("CRM Lead", own, write=1, user=cross)` | A | D | raise | **PASS** | RA-02, SEC-08 |
| `remove_assignments(ignore_permissions=True)` (Staff) | A | D | raise | **PASS** | RA-03, SEC-09, SEC-21 |
| Admin reassign `a→b`, A sau đó read Lead | A | D | raise (cả Lead & child) | **PASS** | RA-09, SEC-10 |
| Cross-team reassign `c→a` (admin), C cũ check | C | D | raise | **PASS** | RA-05, SEC-11 |
| Stale ToDo (open, allocated=a) sau reassign | A | D | raise | **PASS** | RA-09, SEC-22 |
| ToDo insert cross-scope | A | D | raise | **PASS** | RA-02, SEC-21 |
| `delete_bulk_docs` cross-scope | A→B | D | raise | **PASS** | RA-07, SEC-12 (delete) |
| `data_export` / `export_query` (Staff/Manager/BOD) | staff | D | raise | **PASS** | LS-03, SEC-12 |
| `query_report.run` (Staff) | staff | D | raise | **PASS** | LS-10, SEC-12 |
| `crm.api.dashboard.get_dashboard` Manager without hierarchy | manager ngoài cây | D | raise | **PASS** | RISK-SEC-06, SEC-07 |
| `get_contact_by_phone_number` (Staff) | A | D | raise | **PASS** | SEC-27 (endpoint) |
| `get_contact_by_phone_number` Administrator | Admin | OK (proxy fail-open cố ý) | trả dict | **PASS (Admin behavior)** | SEC-27 (note) |
| `get_call_contact` **fallback nội bộ khi Call Log không ref Lead** | (không test) | (không rõ) | **NOT RUN** | **NOT RUN** | SEC-27 nhánh nội bộ |
| `get_assigned_users` cross-scope | A→B | D | raise | **PASS** | RA-09, SEC-19 |
| `get_linked_docs_of_document` cross-scope | A→B | D | raise | **PASS** | RA-09, SEC-20 |
| `duplicate_assignment_rule` (Staff) | A | D | raise + rule không tồn tại trong DB | **PASS** | LS-07, SEC-24 |
| Guest gọi `crm.api.doc.get_data` qua HTTP | Guest | 401/403 | **NOT RUN** — chỉ có `frappe.is_whitelisted` unit-level | **NOT RUN** | SEC-13 |
| POST không CSRF token | user | 400 / `PermissionError` | **NOT RUN** | **NOT RUN** | SEC-14 |
| POST sai CSRF token | user | 400 / `PermissionError` | **NOT RUN** | **NOT RUN** | SEC-14 |
| Reverse proxy/CORS cùng origin | (probe) | bounded | **NOT RUN** | **NOT RUN** | SEC-13 |

## 6. Blocker còn lại (T1C xác nhận độc lập)

| # | Blocker | Trạng thái T1C | Cần để gỡ |
|---|---|---|---|
| **B-T1C-1** | Máy phiên Codex hiện tại **không có Docker/WSL2/bench** — không thể tái chạy site sạch để verify lại độc lập | **MỞ** | Owner bật WSL2 + Docker Desktop như đã làm trước đó, cấp shell đã có `docker` cho phiên, hoặc cấp log raw của 3 lượt chạy khác nhau để T1C đối chiếu thay vì tái chạy |
| **B-T1C-2** | **SEC-13/14/26** chưa có automated test ở `test_permissions.py` hay `test_health.py` | **MỞ** | Thêm file `tests/security/test_sec_13_14_26.py` gọi `requests` thật qua socket `127.0.0.1:<port>` (Frappe bench bind loopback) cho Guest + CSRF + URL leak |
| **B-T1C-3** | **SEC-27 nhánh nội bộ** (`crm_call_log.get_call_contact` khi Call Log không ref Lead) chưa test | **MỞ** | Thêm test tạo Call Log không có `reference_doctype`, gọi `get_call_log`, gọi tiếp contact lookup path |
| **B-T1C-4** | **3 số tổng test khác nhau** (26 / 27 / 28) không có log phân tích chính thức về nguyên nhân chênh | **MỞ** | Chạy lại `pytest -v` hoặc `bench run-tests --verbose` trên site sạch, capture stdout đầy đủ, xác định test #18 có flaky hay không |
| **B-T1C-5** | **Toàn bộ ~70 ô matrix chưa fill per-cell test mapping đầy đủ** — chỉ có ~32 ô đang được route-based test chạm tới | **MỞ** | Từng ô matrix cần `[Test: name @ file:line]` annotation; mapping hiện nằm trong đầu T1B chứ không có manifest trong repo |

## 7. Kết luận độc lập T1C

- **Không chạy lại được site sạch** trong phiên này vì thiếu Docker/WSL2 (B-T1C-1).
- **Phân tích tĩnh 18 test methods** của `test_permissions.py` cho thấy T1B đã mapping đúng theo code; **không có test nào cho SEC-13/14/26** ở runtime transport.
- **Tổng test app (26/27/28) là ba lượt chạy** của ba site/lần/revision khác nhau; T1C giữ cả ba số và **không** dùng tổng để khẳng định "PASS toàn matrix".
- **G0-07** vẫn **PARTIAL** sau T1C:
  - Đã verify runtime các route đã code: **PASS có giới hạn** (đúng như T1B).
  - **NOT RUN**: SEC-13 (Guest HTTP), SEC-14 (CSRF), SEC-16 (`crm_cskh` API field exposure toàn diện), SEC-26 (URL leak toàn diện), SEC-27 nhánh nội bộ.
  - **NOT IMPLEMENTED coverage**: ~38/70 ô matrix chưa có test chạm tới (vd. LEAD-15 cross-doctype spoof, MỌI ô liên quan BOD reporting đọc scoped, MỌI ô reassignment với BOD, v.v.).
- **Hệ thống KHÔNG tuyên bố an toàn**. **G0-07 KHÔNG đạt**, **G0-10 (CI) đạt** nhưng không thay thế.
- **Hành động kế tiếp** (không thuộc T1C): thêm test transport (B-T1C-2), chạy lại 3 log ở site sạch duy nhất để confirm test #18 flaky hay stable (B-T1C-4), fill manifest test mapping cho từng ô matrix (B-T1C-5), xử lý SEC-27 nhánh nội bộ (B-T1C-3).

## 8. Phụ lục — các ký hiệu dùng trong file này

| Ký hiệu | Nghĩa | Nguồn |
|---|---|---|
| **PASS** | Test đã chạy và assert behavior đúng với kỳ vọng | `test_permissions.py` thực tế |
| **PARTIAL** | Một phần test đã chạy nhưng còn nhánh chưa cover | T1B + T1C |
| **NOT IMPLEMENTED** | Code có đường có thể exploit nhưng chưa có test cho nó (xem SEC-26 URL leak) | T1C |
| **NOT RUN** | Test cần environment (Docker/HTTP) chưa chạy | T1C |
| **BLOCKED** | Test không thể chạy trong phiên này | T1C |
| Test #n | Method thứ n trong `TestPermissionBoundaries` | file `test_permissions.py` |
| Site | `dev.localhost` (DEV) hoặc `test_cskh.localhost` (TEST) | `deploy/dev/seed/`, `bench` |

## 9. Liên kết minh chứng (cross-ref)

- `deploy/dev/evidence/wp05-security-final.txt` — log T1B 26 OK tại `test_cskh.localhost` sau WP-05.
- `deploy/dev/evidence/p0-local-final.txt` — log T1B 27 OK sau re-migrate `dev.localhost` và `test_cskh.localhost`.
- `deploy/dev/evidence/p0-isolated-verification.md` §"Security and source reload" — tổng kết 28 OK trên GitHub Actions run `#38712593755` (PR #1).
- `apps/crm_cskh/crm_cskh/tests/test_permissions.py` — 18 permission-boundary methods (đã phân tích dòng 1–543).
- `apps/crm_cskh/crm_cskh/tests/test_health.py` — 4 method; 2 method cover Guest ở mức `frappe.is_whitelisted`, không phải HTTP.
- `apps/crm_cskh/crm_cskh/crm_cskh/tests/test_poc_custom_field.py` — 3 method, **không** phải permission test.
- `apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_app_settings/test_cskh_app_settings.py` — 3 method, **không** phải permission test.
- `apps/crm_cskh/crm_cskh/permissions.py` — implementation hooks đã đọc.
- `apps/crm_cskh/crm_cskh/api/guarded_crm.py` — wrapper endpoint (override whitelist).
- `apps/crm_cskh/crm_cskh/api/guarded_reporting.py` — wrapper export/report.
- `apps/crm_cskh/crm_cskh/hooks.py` — đăng ký hook permission cho 7 DocType + override whitelist.
- `docs/architecture/permission-matrix.md` — tài liệu chính T1C sửa trong cùng change set.
- `docs/architecture/risk-register.md` — tài liệu chính T1C sửa trong cùng change set.
- GitHub Actions: PR #1 (`b382fe4`) runs `#38712593755` (CI pass) và `#39185122032` (push run pass). Origin tool không truy cập được namespace `nobita6986` nên T1C dùng link tài liệu + trích đoạn có sẵn trong repo.
