# Permission Matrix — RBAC / ACL (P0)

> **Owner:** T1C — Thiết kế & kiểm chứng bảo mật P0
> **Phiên bản:** 1.0 (independent verification, T1C) — 2026-10-19
> **Trạng thái (T1C kiểm độc lập):** 18 permission-boundary test methods (code đọc từ `test_permissions.py` dòng 1–543) đã được T1C đối chiếu với từng ô matrix và SEC case. Ba lượt chạy tổng test khác nhau đã ghi nhận — **26 OK** (`wp05-security-final.txt`, revision `b382fe4`, site `test_cskh.localhost`), **27 OK** (`p0-local-final.txt`, revision `b382fe4`, site `dev.localhost` + `test_cskh.localhost`) và **28 OK** (GitHub Actions PR #1 run `#38712593755`, GHA clean bootstrap, site sạch) — T1C **từ chối gộp** thành một con số. Ma trận vẫn còn khoảng **~38/70 ô chưa có test chạm tới** (`CHƯA XÁC MINH`); SEC-13/14/16/26 và nhánh nội bộ SEC-27 vẫn `NOT RUN`. Xem [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §3, §4, §5 và `§6` cho blocker.
> **Đối chiếu checkout (1.0, độc lập bởi T1C):** test suite đã đọc và đối chiếu trực tiếp (`apps/crm_cskh/crm_cskh/tests/test_permissions.py`); hook implementation đã đọc (`apps/crm_cskh/crm_cskh/permissions.py`, `hooks.py`, `api/guarded_crm.py`, `api/guarded_reporting.py`); không có log thô mới so với revision `b382fe4`. Phiên Codex p0-audit-refresh **không có Docker/WSL2** (xem `environment-probe.md` §3, §4), nên T1C **không chạy lại** site sạch mà tái tạo bằng phân tích tĩnh + đối chiếu log T1B. CI run `#38712593755` đã xác nhận clean bootstrap + 28 tests + backup/restore + migrate; CI run `#39185122032` (push) cũng pass nhưng chỉ xác nhận workflow, không mở rộng security transport.
> **ADR-004/005:** quyết định đã được T0 chốt — ADR-004: thực thi quyền ở **backend/record-level** + ownership service tập trung ([`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)); ADR-005: `BOD_VIEWER` **read-only**, **không** System Manager ([`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md)). Runtime bộ ba hook (`permission_query_conditions`/`has_permission` cho 7 DocType + `validate_lead_owner` + `validate_lead_assignment`) đã có implementation đọc được; override whitelist (`override_whitelisted_methods`) cho 13 endpoint CRM baseline đã chặn. Behavior runtime đã được 18 test methods assert, **nhưng chỉ trong route đã gọi** — không suy rộng ra toàn bộ ô matrix hay toàn bộ SEC case.
> **Tài liệu cha:** [`../root-plan/master-plan.md`](../root-plan/master-plan.md) §7, §9, §14 · [`../root-plan/P0.md`](../root-plan/P0.md) §7, §12
> **Tài liệu liên quan:** [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) — đối chiếu độc lập; [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md); [`adr-004`](./adr-004-owner-team-permissions.md), [`adr-005`](./adr-005-bod-viewer-scope.md).
> **Phạm vi lượt này:** T1C độc lập đối chiếu revision `b382fe4` có evidence T1B; sửa câu lịch sử "chưa có security suite" và "0/28 SEC" còn ở §0.2 cũ và changelog, đồng thời phân biệt ba lượt chạy có tổng test khác nhau (26 / 27 / 28) bằng **revision + lệnh + site**.

---

## 0. Quy tắc nguồn & bằng chứng (bắt buộc đọc)

### 0.1. Nguồn đúng phiên bản

| Thành phần | Phiên bản / revision | Trạng thái |
|---|---|---|
| Frappe CRM (baseline khảo sát) | tag **`v1.87.0`** (09/10/2026) | ĐÃ KHÓA (theo tài liệu cha) |
| Frappe Framework | tag **`v15.123.1`** (SHA `d2efa796…`) | Baseline install/migrate **đã chạy trên host hiện tại + GHA CI**; hành vi permission riêng của baseline `v1.87.0` (3 hook cho Lead/Deal/Notification) **CHƯA XÁC MINH** vì `crm_cskh` override gần như toàn bộ qua 7 hook + 13 override whitelist |
| frappe_docker | tag **`v4.0.1`** (SHA `2b26e9ab…`) | Docker/Bench stack chạy trên host hiện tại; full clean-machine reproducibility **CHƯA XÁC MINH** |
| Custom app `crm_cskh` | Scaffold `0.0.1` (revision `b382fe4`); 18 permission-boundary + 4 health + 3 cskh_app_settings + 3 poc_custom_field = 28 test methods (đếm từ source); ba lượt chạy log: 26/27/28 OK | Security behavior **ĐÃ CHẠY** trong route đã gọi (~32/70 ô matrix); phần còn lại **CHƯA XÁC MINH** |
| **ADR-004 / ADR-005** (quyết định T0) | ADR-004 enforcement backend/record-level ([link](./adr-004-owner-team-permissions.md)) · ADR-005 BOD read-only ([link](./adr-005-bod-viewer-scope.md)) | ĐÃ GHI tài liệu (T0 chốt); **runtime đã chạy cho route đã gọi** (xem `permissions.py` + `guarded_crm.py` + 18 test methods), **SEC-13/14/16/26 và ~38 ô matrix chưa chạy** |
| `master-plan.md` | v1.0 — 14/10/2026 | Nguồn định hướng |
| `P0.md` | v1.0 — 15/10/2026 | Nguồn đặc tả P0 |

Các file upstream dưới đây được đọc trực tiếp trên **tag `v1.87.0`** (`https://raw.githubusercontent.com/frappe/crm/v1.87.0/...`). Tham chiếu ở mức **file + function**. Bản [`reuse-audit.md`](./reuse-audit.md)/[`data-model-spike.md`](./data-model-spike.md) (cùng checkout) bổ sung **file:line** tại commit `0d3910b456ef4fb913e77b29db011b3debc4f778`.

- `crm/hooks.py` — đăng ký `permission_query_conditions`, `has_permission`, `doc_events`.
- `crm/permissions/org_hierarchy.py` — `_permission_query_conditions`, `_has_permission`, `_in_hierarchy`.
- `crm/api/todo.py` — `validate`, `after_insert`, `on_update`, `clear_owner_on_unassign`.
- `crm/fcrm/doctype/crm_lead/crm_lead.py` — `validate`, `after_insert`, `assign_agent`, `share_with_agent`.
- `crm/api/doc.py` — `get_data`, `get_assigned_users`, `get_linked_docs_of_document`, `remove_assignments`, `delete_bulk_docs`, `add_seen`.
- `crm/api/activities.py` — `get_activities`, `get_lead_activities`, `get_linked_calls`, `get_attachments`.
- `crm/fcrm/doctype/crm_call_log/crm_call_log.py` — `get_call_log`, `get_permitted_docs`, `get_call_contact`.
- `crm/api/assignment_rule.py` — `get_assignment_rules_list`, `duplicate_assignment_rule`.

### 0.2. Trạng thái repo hiện tại (đối chiếu T1C độc lập 2026-10-19)

Checkout có `apps/crm_cskh/`, `docs/` và `deploy/dev/`. **WP-05 security suite đã chạy** (xem `wp05-security-final.txt` 26 OK, `p0-local-final.txt` 27 OK, `p0-isolated-verification.md` 28 OK trên CI). 18 permission-boundary test methods có trong `apps/crm_cskh/crm_cskh/tests/test_permissions.py` (đã đọc dòng 1–543); cùng app suite có thêm 4 health + 3 poc_custom_field + 3 cskh_app_settings = 10 test không phải permission.

- **Tổng test app đã ghi nhận ở ba lần khác nhau (T1C TỪ CHỐI gộp):**
  - **26 OK** tại `wp05-security-final.txt` (revision `b382fe4`, lệnh `bench --site test_cskh.localhost run-tests --app crm_cskh`).
  - **27 OK** tại `p0-local-final.txt` (revision `b382fe4`, lệnh sau re-migrate `dev.localhost` + `test_cskh.localhost`).
  - **28 OK** tại `p0-isolated-verification.md` §"Security and source reload" (CI run `#38712593755` trên PR #1, GHA clean bootstrap site sạch).
- **Đối chiếu độc lập 18 permission-boundary methods** vs ~70 ô matrix + 28 SEC case: tham khảo [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §3, §5. **Khoảng 32 ô matrix có test mapping (route đã chạy)**; **~38 ô matrix còn `CHƯA XÁC MINH`** do chưa có test chạm tới.
- **SEC case** (28 ca): 19 đã verify runtime qua `test_permissions.py` (SEC-01–12, 15 endpoint phone-lookup, 17, 18, 19, 20, 21, 22, 24, 25, 27 endpoint phone-lookup, 28). **SEC-13, SEC-14, SEC-16, SEC-26 vẫn `NOT RUN`** — không có test HTTP transport hay URL leak trong app suite. **Nhánh nội bộ SEC-27** (`crm_call_log.get_call_contact` khi Call Log không ref Lead) vẫn `NOT RUN`. **SEC-15** chỉ verify nhánh phone-lookup chứ chưa verify toàn bộ API quản trị.
- **MariaDB 11.8** còn caveat tương thích từ baseline Frappe v15 (xem `version-lock.md`).
- **G0-07**: PARTIAL — không đóng. Các ô đã test cho `PASS có giới hạn` (route đã gọi); không mở rộng quyền user thường cho tới khi B-T1C-2 (SEC-13/14/26 transport) được đóng.
- **Không tuyên bố hệ thống "an toàn".** Trạng thái an toàn chỉ được ghi khi SEC-* tương ứng chạy thật và có log HTTP round-trip; ở phiên này mới có unit-level guard (`frappe.is_whitelisted`) trong `test_health.py` chứ không phải HTTP thật.
- **`deploy/dev/`** là **cấu hình/script**, **không** đồng nghĩa app/site đã chạy transport-level; phải có Docker/WSL2 mới chạy được.

### 0.3. Ký hiệu

| Ký hiệu | Nghĩa |
|---|---|
| **A** | Allowed — cho phép |
| **D** | Denied — từ chối |
| **C** | Controlled — cho phép **có kiểm soát** theo quyền/phạm vi (phải mô tả chính xác phạm vi ở P1/P3) |
| `CHƯA XÁC MINH` | Chưa chạy test (giá trị mặc định lượt này) |
| `PASS` / `FAIL` / `NOT IMPLEMENTED` / `BLOCKED` | Kết quả chỉ được điền sau khi chạy test thật |
| **Test âm** | Ca bắt buộc có để chứng minh điều ngược lại (out-of-scope phải bị chặn) |

---

## 1. Actor, team giả và dữ liệu seed

Theo P0 §7.1–§7.2.

| Alias | Tài khoản seed | Role dự kiến | Team |
|---|---|---|---|
| `ADM-SYS` | Administrator | System Manager | — |
| `CRM-ADMIN` | CRM_ADMIN | **CRM Admin — role PoC** (P0 §7.1) | — |
| `BOD` | BOD_VIEWER | **BOD Viewer — role PoC, read-only** (P0 §7.1; **không** System Manager — ADR-005) | — |
| `TL1` | TEAM1_LEADER | Sales Manager | Team 1 |
| `A` | STAFF_A | Sales User | Team 1 |
| `B` | STAFF_B | Sales User | Team 1 |
| `TL2` | TEAM2_LEADER | Sales Manager | Team 2 |
| `C` | STAFF_C | Sales User | Team 2 |

**Dữ liệu seed tối thiểu (giả, không PII thật):**

- Team 1: `A` sở hữu `A1–A4`; `B` sở hữu `B1–B4`.
- Team 2: `C` sở hữu `C1–C4`.
- Chưa giao: `U1–U4` (không `lead_owner`).
- Mỗi lead đã giao có ≥1 Call Log, 1 Note, 1 Task; ít nhất 1 lead có attachment **private**.
- Ca chuyển giao: `A1: A → B` (nội bộ Team 1); `C1: C → A` (chuyển team Team 2 → Team 1).

**Bảng dữ liệu ↔ team (để phân loại ô "dữ liệu thuộc team nào"):**

| Lead | Owner | Team | Thuộc TL |
|---|---|---|---|
| A1–A4 | A | Team 1 | TL1 |
| B1–B4 | B | Team 1 | TL1 |
| C1–C4 | C | Team 2 | TL2 |
| U1–U4 | (trống) | — | — |

---

## 2. MA TRẬN A — CRM Lead (`CRM Lead`)

Endpoint tham chiếu: `crm.api.doc.get_data` (list/search), `frappe.client.get` / Lead detail, `frappe.client.get_list`, export.

| Case | Actor | Dữ liệu (team/owner) | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| LEAD-01 | A | A1 (Team1/A) | detail `frappe.client.get` | A | **PASS** — `Test #1` (`test_lead_list_and_get_data_only_return_owner_scope`, `test_permissions.py` L206) | SEC-01 |
| LEAD-02 | A | B1 (Team1/B) | detail by đoán name | **D** | **PASS** — `Test #1`: `assertRaises(frappe.PermissionError)` khi gọi `client_get("CRM Lead", self.leads["b"])`. | SEC-01 |
| LEAD-03 | A | C1 (Team2/C) | detail chéo team | **D** | **PASS** — cùng assert như LEAD-02; hook `permissions.lead_has_permission` deny vì không phải owner scope. | SEC-01, SEC-06 |
| LEAD-04 | A | list | `get_data` (list/search) | A (chỉ A1–A4) | **PASS** — `Test #1`: `set == {self.leads["a"]}`; `p0-isolated-verification.md` & 3 log 26/27/28. | SEC-02 |
| LEAD-05 | A | list | `get_data` filter bị sửa/bỏ | A scope | **PASS** — `Test #1`: `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. | SEC-02 |
| LEAD-06 | A | B1 | ghi trực tiếp `CRM Lead` (kể cả field CSKH) | **D** | **PASS** — `Test #10` (`test_owner_change_and_assignment_bypass_are_denied`, L423): `lead.save(ignore_permissions=True)` raise; CLI ghi qua `frappe.client.set_value` cũng raise `Test #6` (L356). | SEC-04 |
| LEAD-07 | A | U1 (chưa giao) | detail | **D** | **CHƯA XÁC MINH — chưa có test riêng cho lead chưa giao.** Có thể suy ra vì `lead_owner` rỗng → `_user_owns_in_scope` False (xem `permissions.py` L39) nhưng đây là đọc code, chưa có test mapping. | SEC-01 |
| LEAD-08 | A | A1 (own) | export danh sách SĐT | **D** mặc định | **PASS** — `Test #5` (`test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers`, L319): `data_export` raise cho `A`. | SEC-12 |
| LEAD-09 | TL1 | A1–A4, B1–B4 | list/detail Team 1 | A (Team 1) | **PASS** — `Test #7` (`test_team_leader_sees_team_but_not_other_team`, L388): `visible` chứa `a` và `b`; assertion detail own OK. | SEC-06 |
| LEAD-10 | TL1 | C1 (Team2/C) | list/detail chéo team | **D** | **PASS** — `Test #7`: `assertNotIn(self.leads["c"], visible)`; `get_doc(...).check_permission("read")` raise. | SEC-06 |
| LEAD-11 | TL2 | A1 (Team1/A) | list/detail chéo team | **D** | **PARTIAL — chỉ có test gián tiếp** `Test #13` (`test_cross_team_transfer_blocks_old_owner_and_leader`, L474): `set_user(TL2)` → `assertNotIn(lead_c, visible)` và detail raise. | SEC-06 |
| LEAD-12 | BOD | toàn công ty | list/detail (report scope, read-only) | **C** (read-only, ADR-005) | **PARTIAL — BOD fail-closed cho CRM Lead list đã verify** (`Test #16`, `test_bod_viewer_cannot_read_leads_or_manage_users`, L528); **scope đọc báo cáo chưa build**. | SEC-15 |
| LEAD-13 | CRM-ADMIN | toàn kho | list/detail/import | **C** | **PARTIAL — fail-closed** (`Test #17`, `test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537): `assertNotIn("System Manager", ...)` + `assertFalse(has_permission("CRM Lead", "read"))`. | — |
| LEAD-14 | A | A1 (own) | ghi trực tiếp `CRM Lead` (field ngoài whitelist CSKH) | **D** | **PASS** — `Test #10`: hook `validate_lead_owner` raise khi đổi `lead_owner`; fail-closed cho đến khi ownership service sẵn sàng. | SEC-08 |
| LEAD-15 | Bất kỳ | — | `get_data` đổi `doctype` sang doctype khác | bị bound bởi quyền doctype | **NOT RUN** — không có test mapping cho cross-doctype spoofing. | SEC-25 |
| LEAD-16 | A | B1 | `get_data` chọn `rows`/`columns` = `mobile_no`,`email` ngoài phạm vi | **D** | **PARTIAL** — `Test #2` (`test_get_data_does_not_return_permlevel_fields`, L227) chỉ cover `permlevel=1` (Staff không thấy field cấm). Field-level spoof với `rows=["mobile_no","email"]` ngoài scope **chưa có test riêng**. | SEC-25 |

**Cơ chế đã đọc trên `v1.87.0` (giả thuyết, cần test):** `crm/hooks.py` chỉ đăng ký `permission_query_conditions`/`has_permission` cho `CRM Lead`, `CRM Deal`, `CRM Notification`; logic nằm ở `crm/permissions/org_hierarchy.py` với `_OWNER_FIELD = {"CRM Lead": "lead_owner", "CRM Deal": "deal_owner"}`.

> **Chính sách P0 cho LEAD-06/14 (theo T0):** P0 **từ chối mọi sửa trực tiếp `CRM Lead`** từ nhân viên. Field CSKH chỉ được ghi qua **whitelist + endpoint server-side** trong `crm_cskh`, và chỉ sau khi **schema cụ thể được chốt** (P1/P2). Vì vậy kỳ vọng P0 cho cả hai ca là **D**; việc mở whitelist sẽ được đặc tả và test riêng khi có schema. Cả hai ca vẫn `CHƯA XÁC MINH` ở runtime.

---

## 3. MA TRẬN B — Call Log (`CRM Call Log`)

Endpoint: `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log`, `get_data`, `get_linked_calls` (trong `activities.py`).

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| CL-01 | A | Call của A1 | `get_call_log` | C (own) | **PASS** — `Test #6` (L356) cuối đoạn: `get_call_log` own OK; call của cross raise. | — |
| CL-02 | A | Call của B1 | `get_call_log` by name | **D** | **PASS** — `Test #6`: `assertRaises(PermissionError)` cho `get_call_log(self.call_b)`. | SEC-03 |
| CL-03 | A | Call của C1 | `get_call_log` chéo team | **D** | **PASS** — cùng assert cơ chế hook `crm_cskh.permissions.linked_lead_has_permission`. | SEC-03 |
| CL-04 | A | B1 | `get_data("CRM Call Log")` list | **D** (không thấy call của B) | **PASS** — `Test #3` (L261): `("CRM Call Log", "reference_docname", {self.call_a})` — set ≤ allowed; **Test #9** (L403): `guarded_crm.get_linked_calls(self.leads["b"])` raise. | SEC-03 |
| CL-05 | A | mọi call | `get_call_log` + đọc `from`/`to`/`recording_url` | **D** ngoài scope | **PARTIAL** — `Test #3` và `Test #6` deny access; **CHƯA XÁC MINH** việc endpoint trả URL/file body cho call **trong** scope (giả định CRM không lộ PII cho call own). | SEC-17 |
| CL-06 | TL1 | call Team 1 | list/detail | A (Team 1) | **CHƯA XÁC MINH** — chỉ có test scope list CRM Lead cho TL1. Hook cho Call Log vẫn áp dụng được nhưng chưa có call test riêng. | SEC-06 |
| CL-07 | TL1 | call Team 2 | list/detail | **D** | **CHƯA XÁC MINH** — lý do trên. | SEC-06 |
| CL-08 | BOD | toàn công ty | read call (read-only) | **C** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | SEC-15 |
| CL-09 | Guest | — | `get_call_log` | **D** | **NOT RUN** — chỉ có `test_guest_is_denied` cho `health.ping`; HTTP round-trip cho `get_call_log` chưa có. | SEC-13 |

**Giả thuyết cần test (R-03):** `crm/hooks.py` **không** đăng ký hook permission cho `CRM Call Log`; `get_call_log` chỉ gọi `frappe.has_permission("CRM Call Log","read",name)` → kiểm quyền **DocType/role**, không phải record-level. Nếu role `Sales User` (hoặc role tương đương) có quyền read trên DocType, khả năng đọc call log bất kỳ theo `name` là **cần kiểm chứng** (SEC-03/SEC-17). Không kết luận khi chưa chạy.

---

## 4. MA TRẬN C — Task (`CRM Task`)

Endpoint: `crm.api.activities.get_linked_tasks`, `get_data`, `frappe.client.*`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| TK-01 | A | Task của A1 | detail/list | C (own) | **PASS** — `Test #6` (L356) list (Call/Note/Task/File own) check_permission ok. | — |
| TK-02 | A | Task của B1 | detail by name | **D** | **PASS** — `Test #6`: `("CRM Task", self.task_b)` raise `PermissionError`. | SEC-04, SEC-18 |
| TK-03 | A | B1 | sửa Task ngoài scope | **D** | **PASS** — `Test #6`: `set_value("CRM Task", self.task_b, "title", ...)` raise. | SEC-04 |
| TK-04 | A | U1 | tạo Task gắn lead ngoài scope | **D** | **PASS (one direction) — `Test #14` (`test_direct_todo_assignment_is_denied_even_with_ignore_permissions`, L490)**: tạo `ToDo` ref Lead own với allocated_to khác raise; **Test #4** (L285) verify guard cho linked tasks endpoint. **CHƯA XÁC MINH** cho Task gắn lead ngoài scope (chỉ lead own). | SEC-04 |
| TK-05 | TL1 | Task Team 1 | list/detail | A | **CHƯA XÁC MINH** — không có test mapping cho TL1 + Task. | SEC-06 |
| TK-06 | TL1 | Task Team 2 | list/detail | **D** | **CHƯA XÁC MINH** — không có test mapping cho TL1 + Task. | SEC-06 |
| TK-07 | BOD | toàn công ty | read (read-only) | **C** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | SEC-15 |

**Liên quan:** `crm/api/todo.py` `after_insert`/`on_update` xử lý `CRM Task` (notify), và với Lead/Deal thì **ghi đè `lead_owner`**. `clear_owner_on_unassign` **chỉ** áp dụng Lead/Deal (comment trong code: "Owner concept only exists for Lead/Deal, not Task").

**Giả thuyết cần test (R-03):** không có hook permission cho `CRM Task` → quyền record-level phụ thuộc DocType role + DocShare/ToDo. Cần test đọc Task theo `name` của user khác (SEC-04/SEC-18).

---

## 5. MA TRẬN D — Note / Comment (`FCRM Note`, `Comment`)

Endpoint: `crm.api.activities.get_linked_notes`, `get_attachments("Comment", ...)`, `get_data`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| NT-01 | A | Note của A1 | read | C (own) | **PASS** — `Test #6` (L356) `("FCRM Note", self.note_a)`. | — |
| NT-02 | A | Note của B1 | read by name | **D** | **PASS** — `Test #6`: `("FCRM Note", self.note_b)` raise. | SEC-04, SEC-18 |
| NT-03 | A | Comment của B1 | read | **D** | **PASS** — `Test #6`: `("Comment", self.comment_b)` raise; `Test #3`: comment list trả rỗng (DocType-level block). | SEC-04 |
| NT-04 | A | B1 | tạo Note/Comment ngoài scope | **D** | **PARTIAL** — `Test #6` deny read+set_value; không có test cho việc tạo Note/Comment với `reference_docname=self.leads["b"]` từ actor A. | SEC-04 |
| NT-05 | TL1 | Note Team 1 | read | A | **CHƯA XÁC MINH** — không có test mapping. | SEC-06 |
| NT-06 | TL1 | Note Team 2 | read | **D** | **CHƯA XÁC MINH** — không có test mapping. | SEC-06 |
| NT-07 | BOD | toàn công ty | read (read-only) | **C** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | SEC-15 |

**Ghi chú:** `FCRM Note` là DocType bổ trợ (không thay kết quả CSKH có cấu trúc — master-plan §3.1). Quyền note/comment **cần khớp** với quyền lead mẹ.

---

## 6. MA TRẬN E — Attachment / File private (`File`, `is_private=1`)

Endpoint: `get_attachments` (trong `activities.py`), download URL `/private/files/...` (`frappe.client`/`File` API).

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| AT-01 | A | File của A1 | tải qua URL/token hợp lệ | C (own) | **PASS** — `Test #6` cho File own qua `check_permission`; `Test #4`: sau reassign, new owner `B` (`Test #4` cuối) `find_file_by_url(URL) is not None`. | — |
| AT-02 | A | File private của B1 | tải trực tiếp URL | **D** | **PASS** — `Test #4`: sau reassign, `a` (`frappe.set_user(self.users["a"])`) `find_file_by_url(...) is None`; cơ chế `clear_stale_lead_shares` DocShare gỡ + DocType-level permission file. | SEC-05 |
| AT-03 | A | File của C1 | tải chéo team | **D** | **PASS** — `Test #4` cơ chế hook `linked_lead_has_permission` deny. | SEC-05 |
| AT-04 | A | B1 | `get_attachments`/`get_data` trả `file_url` ngoài scope | **D** (không lộ URL) | **PARTIAL** — `Test #9` (L403): `guarded_crm.get_attachments("CRM Lead", self.leads["b"])` raise; `Test #3`: list scope chỉ trả own. **CHƯA XÁC MINH** field-level: `get_attachments(lead own)` có lộ `file_url` private trong response dict không (xem `crm.api.activities.get_attachments`). | SEC-26 |
| AT-05 | BOD | toàn công ty | tải file theo scope báo cáo (read-only) | **C** | **CHƯA XÁC MINH** — BOD chưa có scope đọc; chỉ fail-closed đã verify (`Test #16`). | SEC-16 |
| AT-06 | Guest | bất kỳ | tải `/private/files/...` | **D** | **NOT RUN** — không có HTTP round-trip; chỉ có `test_guest_is_denied` cho `health.ping`. | SEC-13 |

**Giả thuyết cần test:** `get_attachments` dùng `frappe.db.get_all("File", ...)` trả cả `file_url` cho file private. Cần xác minh (a) URL private có thực sự bị chặn khi tải ngoài scope, (b) API có lộ URL trước khi kiểm quyền không (SEC-05/SEC-26).

---

## 7. MA TRẬN F — Chuyển giao / Reassignment & Assignment endpoints

Endpoint: `crm.api.doc.remove_assignments`, `crm.api.doc.get_assigned_users`, `crm.api.assignment_rule.*`, hook `crm/api/todo.py`, `crm_lead.assign_agent`/`share_with_agent`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| RA-01 | A | A1 | tự đổi `lead_owner` A1→B | **D** | **PASS** — `Test #10` (L423): `lead.lead_owner = self.users["c"]` + `lead.save(ignore_permissions=True)` raise. | SEC-08 |
| RA-02 | A | B1 | assign cho người khác (ToDo) | **D** | **PASS** — `Test #14` (L490): tạo `ToDo` với `allocated_to=self.users["c"]` + `ignore_permissions=True` raise. | SEC-08 |
| RA-03 | A | A1 | gọi mutation với `ignore_permissions=true` | **D** | **PASS** — `Test #10`: `remove_assignments(...ignore_permissions=True)` raise; hook `validate_lead_assignment` deny. | SEC-09, SEC-21 |
| RA-04 | CRM-ADMIN | A1 | chuyển A1: A→B (nội bộ Team 1) | A | **CHƯA XÁC MINH** — không có test case admin/A→B nội bộ Team. Có `Test #12` (lead_owner old A → new B bởi admin) và `Test #13` (C→A cross-team). Hành vi admin thay đổi lead_owner đã qua hook fail-open (`_is_system` True), nhưng chưa có assertion `PASS` riêng cho role admin nội bộ. | SEC-10 |
| RA-05 | CRM-ADMIN | C1 | chuyển C1: C→A (Team2→Team1) | A | **PARTIAL** — `Test #13` (L474): `set_user(admin)` save change owner C→A `pass`; sau đó `C` cũ không thấy Lead (PASS chặn). Pass trên admin chưa có assertion riêng ở ô "C→A hiện trong list của A" — verify ngầm. | SEC-11 |
| RA-06 | TL1 | Lead Team 1 | chuyển trong team | **C** | **CHƯA XÁC MINH** — chỉ có admin trong tests. Hook `validate_lead_owner` block TL1 (vì TL1 không phải `_is_system`). | SEC-06 |
| RA-07 | TL1 | Lead Team 2 | chuyển chéo team | **D** | **CHƯA XÁC MINH** — không có test case TL1 reassign. | SEC-06 |
| RA-08 | BOD | bất kỳ | chuyển/thu hồi | **D** mặc định (read-only) | **PARTIAL** — fail-closed qua hook (`Test #10` chung cho non-system); không có test riêng cho BOD. | SEC-15 |
| RA-09 | A (cũ) | sau A1: A→B | đọc lại Lead/Call/Note/Task/File | **D** (theo policy) | **PASS** — `Test #12` (L450): `assertNotIn(lead_a, get_list(self.users["a"]))`; `.check_permission` raise cho Lead + child; `Test #4` (L285): tương tự + linked endpoints raise. | SEC-10, SEC-22, SEC-23 |
| RA-10 | B (mới) | sau A1: A→B | đọc/ghi Lead/Call/Note/Task/File | A | **PARTIAL** — `Test #12` cuối: `B` thấy Lead trong list. **CHƯA XÁC MINH** write của B trên call/note/task/file con; chưa có test riêng. | SEC-10 |

### 7.1. Bảng sự thật đồng bộ 3 cơ chế (giả thuyết từ code `v1.87.0`)

Thuật ngữ: `lead_owner` (field trên Lead), `ToDo` (assignment, nguồn của `_assign`), `DocShare` (share quyền ghi trên Lead).

| Đường chuyển giao | `lead_owner` | `ToDo` mới | `ToDo` cũ | `DocShare` mới | `DocShare` cũ | Thu hồi quyền người cũ? | Test |
|---|---|---|---|---|---|---|---|
| Cập nhật `lead_owner` rồi `save()` (`crm_lead.validate` → `share_with_agent`+`assign_agent`) | Cập nhật | Thêm (`assign_agent`) | **Không huỷ → còn treo** | Thêm (`write=1`) | **Gỡ** (`share_with_agent` remove user≠agent) | Phụ thuộc: DocShare gỡ, nhưng `ToDo` cũ có thể còn giữ quyền | SEC-10, SEC-23 |
| Assign qua `ToDo` (`todo.after_insert`) | Ghi đè bằng `frappe.db.set_value` (**bypass controller**) | Tạo | **Không huỷ** | **Không cập nhật** (db_set không chạy `CRMLead.validate`) | **Không gỡ** | **Không đầy đủ** — DocShare cũ còn → người cũ có thể vẫn truy cập | SEC-22 |
| `remove_assignments` (`doc.py`) | `set_status(...Cancelled)` → `todo.on_update` → `clear_owner_on_unassign` (chỉ Lead/Deal) | — | Huỷ (Cancelled) | **Không tạo** | **Không gỡ** | Một phần | SEC-09, SEC-21 |

**Kết luận tạm (giả thuyết, CHƯA XÁC MINH):** tồn tại **bất đối xứng** giữa đường "đổi `lead_owner`" (đồng bộ DocShare, treo ToDo) và đường "assign qua ToDo" (đồng bộ `lead_owner`, treo DocShare). Khớp với rủi ro R-04 (master-plan §16). Đây là **ứng viên Critical/High** — xem `risk-register.md` `RISK-SEC-02`.

**Hướng đã chốt (ADR-004, T0):** quyền owner/team thực thi ở **backend/record-level**; mọi mutation owner/assignee phải đi qua **ownership service tập trung** trong `crm_cskh` (đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction, có reconciliation), thay vì cập nhật rời rạc. Đây là **quyết định thiết kế**; hành vi runtime của baseline vẫn `CHƯA XÁC MINH` cho tới khi SEC-10/22/23 chạy thật.

### 7.2. `get_assigned_users` — không thấy kiểm quyền

`crm/api/doc.py: get_assigned_users(doctype,name)` dùng `frappe.get_all("ToDo", ...)`. `get_all` mặc định **không** kiểm permission. Người dùng đã đăng nhập có thể tra assignee của bất kỳ doc theo `name` → **rò rỉ metadata** (danh tính người phụ trách). Test: SEC-19.

---

## 8. MA TRẬN G — List/Search/Export & API đọc dữ liệu chéo

| Case | Actor | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|
| LS-01 | A | `frappe.client.get_list("CRM Lead", ...)` | scope A | **PASS** — `Test #1` (L206): `client_get_list("CRM Lead", ...)` chỉ trả own. | SEC-02 |
| LS-02 | A | global search trả lead ngoài scope | **D** | **CHƯA XÁC MINH** — chưa có test cho global search (qua Frappe search). | SEC-02 |
| LS-03 | A | export/CSV danh sách SĐT | **D** mặc định | **PASS** — `Test #5` (L319): `data_export` + `reportview_export_query` raise cho A. | SEC-12 |
| LS-04 | A | `get_data` link lookup / dropdown ngoài scope | **D** | **PARTIAL** — `Test #1` cho filter spoofed cho `CRM Lead`. CHƯA XÁC MINH cho dropdown lookup (qua `frappe.client.get_link_options`). | SEC-02 |
| LS-05 | A | `get_data` `doctype` tùy biến (KV khác) | bound bởi quyền doctype | **NOT RUN** — không có test mapping cho cross-doctype spoof. | SEC-25 |
| LS-06 | A | `get_data` `rows`/`columns` chọn field nhạy cảm ngoài scope | **D** | **PARTIAL** — `Test #2` (L227) cho permlevel=1. Field nhạy cảm non-permlevel (vd. `mobile_no`) chưa có negative. | SEC-25 |
| LS-07 | A | bulk (`delete_bulk_docs`) chéo scope | **D** | **PASS** — `Test #11` (L437): `delete_bulk_docs` cross-scope raise. | SEC-12 |
| LS-08 | TL1 | report Team 1 | Team 1 | **PASS — `Test #5`** (c) cho `query_report.run` raise cho TL1 (chặn tất cả report). CHƯA XÁC MINH cho report scoped Team 1 (chưa build). | SEC-06 |
| LS-09 | TL1 | report Team 2 | **D** | **PASS — same as LS-08:** TL1 chặn toàn bộ report, scope cụ thể Team 2 chưa build. | SEC-06 |
| LS-10 | BOD | report toàn công ty (read-only) | **C** | **PARTIAL — `Test #5`** BOD chặn export/report (fail-closed); **scope đọc báo cáo BOD chưa build**. | SEC-15 |

**Giả thuyết:** `get_data` nhận `doctype`, `filters`, `default_filters`, `rows`, `columns` từ client. `rows`/`columns` cho phép chọn field tùy ý (`mobile_no`, `email`…) — cần test field-level exposure (SEC-25), kể cả trong scope (Medium) và ngoài scope (High nếu lọt).

---

## 9. MA TRẬN H — Báo cáo/Admin & BOD viewer (ADR-005)

| Case | Actor | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|
| BD-01 | BOD | xem báo cáo/kho (read-only) | **C** (không cần System Manager) | **PARTIAL — fail-closed verify** (`Test #16`, `test_bod_viewer_cannot_read_leads_or_manage_users`, L528): BOD `frappe.get_list("CRM Lead", pluck="name")` raise; **nhánh đọc báo cáo scoped (report API read-only) chưa build**. | SEC-15 |
| BD-02 | BOD | API quản trị user/role | **D** | **PASS — `Test #16`**: `has_permission("User", "create") == False` + `has_permission("Role", "create") == False` cho BOD; report API call `data_export` raise (`Test #5`). | SEC-15 |
| BD-03 | BOD | import/xoá dữ liệu | **D** | **PASS — `Test #16`**: `has_permission("Data Import", "create") == False`. **CHƯA XÁC MINH** cho path delete (qua `delete_bulk_docs`) — không có test riêng cho BOD + bulk delete (kỳ vọng raise qua override `guarded_crm.delete_bulk_docs` chưa verify). | SEC-15 |
| BD-04 | BOD | reassign/thu hồi | **D** mặc định | **PARTIAL — fail-closed qua hook** (`Test #10` chung cho non-system); **không có test riêng BOD + reassign**. | SEC-15 |
| BD-05 | BOD | `System Manager` ngầm qua role khác | **D** | **PASS — `Test #16`**: `assertNotIn("System Manager", frappe.get_roles(self.users["bod"]))`. | SEC-15 |
| BD-06 | CRM-ADMIN | cấu hình quyền hệ thống | **D** (trừ System Manager) | **PASS — `Test #17`** (`test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537): CRM-ADMIN `assertNotIn("System Manager")` + `assertFalse(has_permission("CRM Lead", "read"))`. | — |

**Phân tích BOD (ADR-005, T0 chốt + đối chiếu T1C 2026-10-19):** trên `v1.87.0`, hook `permission_query_conditions`/`has_permission` chỉ đăng ký cho Lead/Deal/Notification; **không có role/API báo cáo read-only đóng gói sẵn**. Master-plan §2.3 yêu cầu BOD xem báo cáo **không** cần `System Manager`. **Quyết định ADR-005:** `BOD_VIEWER` là role **read-only** xem báo cáo theo phạm vi, **không** cấp `System Manager`; role + permission + API báo cáo được xây trong `crm_cskh` (không dựa vào quyền rộng của baseline). Runtime: **fail-closed đã verify** qua `Test #16`, `Test #17` (xem `test_permissions.py` L528, L537) cho BD-02/03/05/06; BD-01/04 còn ở mức PARTIAL — **reporting scope BOD chưa build**. Chi tiết: `risk-register.md` `RISK-SEC-09` và [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §5.

---

## 10. Endpoint inventory & cơ chế quyền (đọc trên `v1.87.0`)

| Endpoint / file | Cơ chế quyền đã đọc | Giả thuyết rủi ro | Test |
|---|---|---|---|
| `crm/api/doc.get_data` | `frappe.get_list` (permission-aware) + `rows/columns/doctype` từ client | field-level exposure; doctype client-controlled | SEC-02, SEC-25 |
| `crm/api/doc.get_assigned_users` | `frappe.get_all` — **không** kiểm permission | rò rỉ assignee | SEC-19 |
| `crm/api/doc.get_linked_docs_of_document` | **không** kiểm permission trên doc đầu vào; trả title, đặc biệt Call Log `from`/`to` | rò rỉ số điện thoại | SEC-20 |
| `crm/api/doc.remove_assignments` | tham số `ignore_permissions` **từ client** → truyền vào `set_status` | bypass assignment | SEC-09, SEC-21 |
| `crm/api/doc.add_seen` | `doc.check_permission("read")` | (đã có guard) | — |
| `crm/api/doc.delete_bulk_docs` | `frappe.desk.reportview.delete_bulk` | bulk chéo scope | SEC-12 |
| `crm/api/doc.remove_linked_doc_reference` | `frappe.has_permission(...,"write",name)` rồi `save(ignore_permissions=True)` | guard ở entry | SEC-12 |
| `crm/api/activities.get_activities` | nhánh tới `get_lead_activities`/`get_deal_activities` → `has_permission(...,"read",name)` | phụ thuộc model Lead yếu | SEC-04 |
| `crm/api/activities.get_linked_calls/_notes/_tasks` | `frappe.db.get_all` (không perm) nhưng gated sau `has_permission` lead | OK nếu lead-perm đúng | SEC-04 |
| `crm/api/activities.get_attachments` | `frappe.db.get_all("File", ...)` trả `file_url` private | lộ URL file private | SEC-26 |
| `crm/.../crm_call_log.get_call_log` | `frappe.has_permission("CRM Call Log","read",name)` — **DocType-level** | đọc call chéo record | SEC-03, SEC-17 |
| `crm/.../crm_call_log.get_call_contact` | `get_contact_by_phone_number` khi call không ref lead/deal | tra contact theo số ĐT → rò rỉ | SEC-27 |
| `crm/api/assignment_rule.get_assignment_rules_list` | `frappe.get_all` — không kiểm permission | lộ cấu hình rule | SEC-24 |
| `crm/api/assignment_rule.duplicate_assignment_rule` | `frappe.get_doc` + `insert()` — **không** kiểm permission tường minh | user thấp tạo/nhân bản rule | SEC-24 |
| `crm/hooks.py` | hook chỉ cho Lead/Deal/Notification | child doctype không record-level | SEC-17, SEC-18 |
| `crm/api/todo.py` | mirror `lead_owner`; `clear_owner_on_unassign` | desync ToDo/DocShare (ADR-004) | SEC-22, SEC-23 |
| `crm/.../crm_lead.py` (`share_with_agent`) | DocShare `write=1`; gỡ DocShare user≠agent | bất đối xứng với ToDo (ADR-004) | SEC-10, SEC-23 |
| `crm/permissions/org_hierarchy.py` | Sales Manager **ngoài cây** → `return ""` (thấy tất cả) | over-broad | SEC-07 |

---

## 11. Test plan (đã thực thi một phần sau WP-02/03/05; còn mở cho SEC-13/14/16/26, nhánh nội bộ SEC-27 và ~38 ô matrix)

### 11.1. Điều kiện tiên quyết (đã đạt / còn mở)

1. Baseline Frappe CRM `v1.87.0` + Framework `v15.123.1` **đã bootstrap/install/migrate/seed và smoke UI/API trên host hiện tại + GitHub Actions CI run `#38712593755`** ([`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md), [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md)); MariaDB 11.8 compatibility caveat còn mở.
2. App `crm_cskh` cài cùng site (`dev.localhost` + `test_cskh.localhost`); test site dùng cho ba lượt chạy 26/27/28 OK (xem §0.2).
3. Seed đủ 2 team + tài khoản mục §1, deterministic, reset được (xem [`environment-probe.md`](../../deploy/dev/evidence/environment-probe.md) §8 và [`p0-d-runtime.md`](./p0-d-runtime.md) §5.1).
4. Test harness Frappe; **file `tests/test_permissions.py` đã viết** (18 methods) — KHÔNG dùng `tests/security/` (đặt tại `tests/`). Riêng `tests/test_health.py` 4 methods (đa số không phải permission); `test_poc_custom_field.py` 3 methods (không phải permission); `doctype/cskh_app_settings/test_cskh_app_settings.py` 3 methods (không phải permission).

### 11.2. Tổ chức test

- File đã viết: `apps/crm_cskh/crm_cskh/tests/test_permissions.py` (18 permission-boundary methods, đã đọc 543 dòng), **KHÔNG** theo convention `tests/security/test_sec_*.py` ban đầu. Mapping test ↔ matrix được T1C gắn vào từng ô `Thực tế` ở §2–§9 (bản 1.0) và trong [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §3.
- Lệnh chạy: `bench --site test_cskh.localhost run-tests --app crm_cskh` (đã chạy 26 OK ở `wp05-security-final.txt`; 27 OK ở `p0-local-final.txt`; 28 OK ở CI run `#38712593755`).
- Dùng API key/session của **tài khoản test** qua `frappe.set_user(...)` (xem `test_permissions.py` L20–L42); **không** commit token/cookie.
- Mỗi ca phải có **cả** positive (trong scope) và negative (ngoài scope) — tuân thủ P0 §13.1.4.

### 11.3. Danh mục ca test âm

Kế thừa P0 §7.5 (SEC-01…SEC-16) **cộng** các giả thuyết từ đọc code:

| ID | Mô tả ngắn | Nguồn |
|---|---|---|
| SEC-01 | A xem Lead của B bằng ID đoán được | P0 §7.5 |
| SEC-02 | A query `get_data` với filter sửa/bỏ | P0 §7.5 |
| SEC-03 | A truy cập `CRM Call Log` của lead B | P0 §7.5 |
| SEC-04 | A đọc Note/Task/Comment ngoài phạm vi | P0 §7.5 |
| SEC-05 | A tải file private của lead B qua URL trực tiếp | P0 §7.5 |
| SEC-06 | TL1 xem Lead/Call của Team 2 | P0 §7.5 |
| SEC-07 | Sales Manager ngoài cây hierarchy | P0 §7.5 |
| SEC-08 | A sửa `lead_owner`/assign người khác | P0 §7.5 |
| SEC-09 | A gọi mutation với `ignore_permissions=true` | P0 §7.5 |
| SEC-10 | Reassign A→B; thử lại toàn bộ API/read/export với A | P0 §7.5 |
| SEC-11 | Chuyển Team1→Team2; leader cũ/mới | P0 §7.5 |
| SEC-12 | Staff export/bulk query ngoài phạm vi | P0 §7.5 |
| SEC-13 | Guest gọi endpoint `crm_cskh` có xác thực | P0 §7.5 |
| SEC-14 | CSRF/session khi POST từ web | P0 §7.5 |
| SEC-15 | BOD Viewer gọi API quản trị user/role/import | P0 §7.5 |
| SEC-16 | API `crm_cskh` trả field nhạy cảm | P0 §7.5 |
| **SEC-17** | A đọc `CRM Call Log` bất kỳ theo `name` (thiếu hook record-level) | MỚI (T1C, `hooks.py`+`crm_call_log.get_call_log`) |
| **SEC-18** | A đọc `FCRM Note`/`CRM Task` bất kỳ theo `name` | MỚI (T1C, `hooks.py`) |
| **SEC-19** | `get_assigned_users` trả assignee cho doc tùy ý | MỚI (T1C, `doc.py`) |
| **SEC-20** | `get_linked_docs_of_document` trả linked docs kèm `from`/`to` | MỚI (T1C, `doc.py`) |
| **SEC-21** | `remove_assignments` với `ignore_permissions=true` unassign chéo scope | MỚI (T1C, `doc.py`) |
| **SEC-22** | Reassign qua ToDo → DocShare cũ còn → người cũ vẫn truy cập | MỚI (T1C, `todo.py`+`crm_lead.py`) |
| **SEC-23** | Reassign qua `lead_owner` → ToDo cũ còn → người cũ vẫn truy cập | MỚI (T1C, `crm_lead.py`+`todo.py`) |
| **SEC-24** | `duplicate_assignment_rule` bởi user quyền thấp | MỚI (T1C, `assignment_rule.py`) |
| **SEC-25** | `get_data` field-level exposure (`rows`/`columns`) | MỚI (T1C, `doc.py.get_data`) |
| **SEC-26** | Lộ `file_url` private qua activities/attachments | MỚI (T1C, `activities.py`) |
| **SEC-27** | `get_call_contact` tra contact theo số ĐT | MỚI (T1C, `crm_call_log.py`) |
| **SEC-28** | BOD không có `System Manager` ngầm; role read-only theo ADR-005 (runtime chưa xác minh) | MỚI (T1C, `hooks.py`) |

### 11.4. Định dạng bằng chứng (P0 §7.7)

Mỗi ca lưu: user/role, Lead ID giả, endpoint/method, HTTP status, response **đã khử dữ liệu nhạy cảm**, tên test + vị trí code, link CI job, kết quả trước/sau, severity, quyết định. **Không** commit token/cookie/PII.

### 11.5. Tiêu chí thoát (G0-07)

- Không còn `Critical/High` mở với kho Lead và đối tượng liên quan.
- Có test âm chạy thật với **≥2 user** khác nhau (không chỉ admin).
- Nếu endpoint quá rộng không sửa được: **hạn chế đường gọi** để role bị ảnh hưởng **không truy cập được**, chứng minh bằng negative tests.

### 11.6. Kết quả runtime theo SEC case

> T1C ghi nhận **ba lượt chạy khác nhau** (log trong repo); 19/28 SEC case đã có method đọc trong `test_permissions.py`. Mỗi dòng dưới đây phân biệt bằng `evidence source · revision · lệnh/site`. Không suy rộng 18 / 26 / 27 / 28 test app thành "PASS toàn SEC matrix".

| SEC | Trạng thái T1C | Bằng chứng / giới hạn (evidence source · revision · site/lệnh) |
|---|---|---|
| 01 | **PASS** (route đã test) | `test_lead_list_and_get_data_only_return_owner_scope` (L206, `test_permissions.py`); `wp05-security-final.txt` 26 OK tại `test_cskh.localhost` · `p0-local-final.txt` 27 OK tại `dev.localhost`+`test_cskh.localhost` · `p0-isolated-verification.md` 28 OK tại GHA PR #1 `#38712593755` (CI). |
| 02 | **PASS** (route đã test) | Cùng test trên + `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. |
| 03 | **PASS** | `test_child_lists_and_activity_reads_stay_with_parent_scope` (L261), `test_child_records_and_direct_private_file_are_record_scoped` (L356) — `frappe.get_list` cho Call Log/Note/Task và `.check_permission` raise. |
| 04 | **PASS** (route `get_linked_*` đã test) | `test_linked_activity_endpoints_check_parent_before_returning_data` (L403) — `guarded_crm.get_linked_calls/_notes/_tasks/_attachments/_linked_docs_of_document` raise khi cross-scope. |
| 05 | **PASS** (route `find_file_by_url` đã test) | `test_private_file_download_and_stale_docshare_are_revoked` (L285) — sau reassign, `find_file_by_url` cho old owner trả `None`. |
| 06 | **PASS** (TL scope test) | `test_team_leader_sees_team_but_not_other_team` (L388). |
| 07 | **PASS** (manager-without-hierarchy fail-closed) | `test_sales_manager_without_hierarchy_is_denied` (L397). |
| 08 | **PASS** | `test_owner_change_and_assignment_bypass_are_denied` (L423) — `lead.save(ignore_permissions=True)` raise. |
| 09 | **PASS** | Cùng test trên + `test_direct_todo_assignment_is_denied_even_with_ignore_permissions` (L490). |
| 10 | **PASS** | `test_stale_todo_cannot_restore_access_after_owner_changes` (L450), `test_cross_team_transfer_blocks_old_owner_and_leader` (L474) — old owner mất quyền Lead + child + ToDo. |
| 11 | **PASS** | `test_cross_team_transfer_blocks_old_owner_and_leader` (L474) — TL2 cũ không thấy Lead cross-team. |
| 12 | **PASS** (delete bulk đã test, report block đã test) | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) — `data_export`, `export_query`, `query_report.run` raise cho Staff/Manager/BOD. `test_bulk_delete_and_unlink_cannot_touch_cross_scope_records` (L437). |
| **13** | **NOT RUN** | Chỉ có `frappe.is_whitelisted(health.ping)` unit-level trong `test_health.py::test_guest_is_denied` (L27); chưa có test HTTP round-trip gọi `/api/method/...` không auth. |
| **14** | **NOT RUN** | Không có automated test nào cho POST CSRF trong `crm_cskh/tests/`. Browser quan sát trong `p0-exit-report.md` G0-08 **không** phải test. |
| 15 | **PARTIAL** (pass cho nhánh phone-lookup; fail-closed cho User/Role/Data Import; **chưa** cover admin/manage-user paths đầy đủ) | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) + `test_bod_viewer_cannot_read_leads_or_manage_users` (L528). |
| **16** | **NOT RUN** | Chưa có test cho `crm_cskh` API field exposure toàn diện; `test_get_data_does_not_return_permlevel_fields` (L227) chỉ cover `crm.api.doc.get_data` permlevel=1. |
| 17 | **PASS** | `test_child_records_and_direct_private_file_are_record_scoped` (L356) — `get_call_log(self.call_b)` raise. |
| 18 | **PASS** (cross record qua `.check_permission`) | Cùng test trên + `test_child_lists_and_activity_reads_stay_with_parent_scope` (L261) cho list scope. |
| 19 | **PASS** | `test_linked_activity_endpoints_check_parent_before_returning_data` (L403) — `guarded_crm.get_assigned_users` raise cho cross-scope. |
| 20 | **PASS** | Cùng test trên — `guarded_crm.get_linked_docs_of_document` raise. |
| 21 | **PASS** | `test_owner_change_and_assignment_bypass_are_denied` (L423) + `test_direct_todo_assignment_is_denied_even_with_ignore_permissions` (L490). |
| 22 | **PASS** (one direction: ToDo cũ không khôi phục quyền Lead sau reassign) | `test_stale_todo_cannot_restore_access_after_owner_changes` (L450); **chưa** có test cho direction ngược lại (DocShare cũ còn khi reassign qua lead_owner). |
| 23 | **PARTIAL** | Đã chạy `validate_lead_owner` hook ngăn `lead.save(ignore_permissions=True)`; **chưa** có test trực tiếp cho `crm_lead.share_with_agent` desync; xem §7.1 và `risk-register.md` §3 RISK-SEC-02. |
| 24 | **PASS** | `test_staff_cannot_duplicate_assignment_rules` (L504). |
| 25 | **PASS** | `test_get_data_does_not_return_permlevel_fields` (L227). |
| **26** | **NOT IMPLEMENTED coverage** | `guarded_crm.get_attachments` raise `PermissionError` cho cross-scope (PASS cho ngăn lộ); **chưa** có test nào khẳng định `file_url` của file private **không** xuất hiện trong response cho scope được phép. Owner test phải tự gọi `get_attachments(lead own)` rồi grep `file_url` trong dict trả về để chặt chẽ. |
| **27** | **PARTIAL** (endpoint đã chặn cho staff; nhánh nội bộ `crm_call_log.get_call_contact` khi Call Log không ref Lead **chưa test**) | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) chỉ cover endpoint `get_contact_by_phone_number` cho 4 actor với số "P0-LOOKUP-NO-MATCH". |
| 28 | **PASS** (BOD không System Manager, không tạo User/Role/Data Import; CRM-ADMIN fail-closed trên Lead read/write) | `test_bod_viewer_cannot_read_leads_or_manage_users` (L528), `test_crm_admin_role_fails_closed_until_controlled_service_exists` (L537). |

**Chú thích evidence T1C:**
- 26 OK ở `wp05-security-final.txt` do T1B tạo, revision `b382fe4` (HEAD trên `codex/p0-audit-refresh`), site `test_cskh.localhost`. Không thấy stdcapture method-level.
- 27 OK ở `p0-local-final.txt`, cùng revision, site `dev.localhost` + `test_cskh.localhost` (re-migrate). Không thấy stdcapture method-level.
- 28 OK ở `p0-isolated-verification.md` §"Security and source reload", CI run `#38712593755` trên `b382fe4`, GHA clean bootstrap. Không thấy stdcapture method-level.
- **T1C không tái tạo được** tại sao 26 ≠ 27 ≠ 28; nghi vấn test #18 (`test_seed_owner_scope_and_hierarchy_are_applied`) hoặc phụ thuộc vào thứ tự test discovery. Không có manifest phân biệt pass/fail per method trong repo.

---

## 12. Trạng thái xác minh tổng hợp (T1C, 2026-10-19)

- Số ca thiết kế: **~70 ô ma trận**, 28 ca test âm (`SEC-01…SEC-28`).
- 18 permission-boundary test methods có mã đọc được trong `test_permissions.py` (T1C đọc 543 dòng). Ba lượt chạy tổng đã ghi nhận — **26 / 27 / 28 tests OK** — không thể chọn một con số làm "tổng duy nhất" (xem §0.2 và `t1c-security-verification.md` §2).
- Đối chiếu per-cell (T1C, xem `t1c-security-verification.md` §5): **khoảng 32 ô matrix có mapping test**; **~38 ô matrix còn `CHƯA XÁC MINH`**.
- SEC coverage: **19/28 có pass per route** (xem §11.6); **SEC-13, SEC-14, SEC-16 vẫn `NOT RUN`**; **SEC-26 `NOT IMPLEMENTED coverage`**; **SEC-15, 22, 23, 27 `PARTIAL`**. **0 ca đạt FAIL** (không có test nào fail trong ba lượt chạy có log, nhưng đây **không** đồng nghĩa "không có lỗ hổng" — các route chưa chạy có thể còn lỗi chưa phát hiện).
- T1C **không chạy lại site sạch** trong phiên này (máy thiếu Docker/WSL2); phụ thuộc vào log T1B tại revision `b382fe4` và các link tài liệu. Kết luận runtime cuối cùng vẫn là **G0-07 PARTIAL**.
- Hệ thống **không** được tuyên bố an toàn; CI PR #1 đã pass nhưng security transport/full matrix vẫn mở.

## 13. Việc cần T0 quyết / blocker

1. **WP-05 đã có suite** (xem `wp05-security-final.txt`, `p0-local-final.txt`, `p0-isolated-verification.md`) và remediation fail-closed cho các API đã test; GitHub Actions PR #1 (`#38712593755`, revision `b382fe4`) đã pass. **SEC-13/14/16/26 vẫn `NOT RUN`/`NOT IMPLEMENTED`**; **~38/70 ô matrix chưa có test mapping**; **nhánh nội bộ SEC-27 chưa test**; `risk-register.md` §6 vẫn duy trì RISK-SEC-12 mở. Trạng thái chi tiết ở [`p0-exit-report.md`](./p0-exit-report.md) và `risk-register.md` §4, §6.
2. **ADR-004/005: đã ghi tài liệu và runtime hook đã chạy** — T0 chốt nội dung; file có trong checkout: [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) (thực thi quyền owner/team ở backend/record-level + ownership service tập trung) và [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md) (`BOD_VIEWER` read-only, không System Manager). Implementation đã đọc: `apps/crm_cskh/crm_cskh/permissions.py` hook 7 DocType + `apps/crm_cskh/crm_cskh/api/guarded_crm.py` + `guarded_reporting.py` override whitelist. Behavior runtime đã được 18 test methods xác nhận **trong route đã gọi**.
3. Role `CRM_ADMIN`/`BOD_VIEWER`: **giữ là role PoC** theo P0 §7.1 (BOD read-only). Whitelist field CSKH cho Staff + endpoint server-side (LEAD-06/14) sẽ **chốt cùng schema cụ thể ở P1/P2** — P0 từ chối sửa trực tiếp `CRM Lead`.
4. Không cấp thêm quyền Staff/BOD/CRM_ADMIN hoặc triển khai dữ liệu thật trước khi các gate còn partial được đóng.
5. **Blocker mới T1C mở 2026-10-19** (xem `t1c-security-verification.md` §6): B-T1C-1 (env), B-T1C-2 (SEC-13/14/26 transport test), B-T1C-3 (SEC-27 nội bộ), B-T1C-4 (3 tổng test khác nhau), B-T1C-5 (manifest test mapping cho ~38 ô còn lại).

## 14. Changelog

| Ngày | Người | Nội dung |
| 15/10/2026 | T0 | Bản 0.9: ghi nhận CI PR #1 pass cùng clean bootstrap và backup/restore; 18 permission-boundary methods, suite 28/28 pass; SEC-13/14/27 và full matrix vẫn mở. |
|---|---|---|
| 15/10/2026 | T0 | Bản 0.7: đồng bộ WP-02/03 — app POC, test site và 9 test chức năng đã chạy; ghi probe owner/team sau bật hierarchy; giữ `0/28` SEC, mọi ô quyền `CHƯA XÁC MINH`. |
| 15/10/2026 | T0 | Bản 0.6: ghi nhận baseline/site/seed/UI/API smoke WP-01 trên host hiện tại; cập nhật blocker còn thiếu `crm_cskh`/test harness. Giữ `0/28` SEC, 0 ô PASS/FAIL, toàn bộ permission thực tế `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.1 thiết kế: ma trận A–H, phân tích đặc biệt, endpoint inventory, test plan, 28 ca âm. Tất cả `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.2: đối chiếu checkout — sửa trạng thái version (Framework `v15.123.1`/docker `v4.0.1` đã khóa metadata, runtime chưa chạy), sửa câu "repo chỉ có docs/", cập nhật tiền đề test và §13 (ADR-004/005 vẫn thiếu). Giữ toàn bộ `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.3: đồng bộ theo ADR-004/005 (T0 chốt) — cập nhật tham chiếu ADR, actor PoC (`CRM_ADMIN`, `BOD_VIEWER` read-only/không System Manager), chính sách P0 cho LEAD-06/14 (từ chối sửa trực tiếp `CRM Lead`; whitelist + endpoint server-side chốt sau), hướng ADR-004 cho reassignment, §9 BOD, §11.3 SEC-28, §13. Ghi rõ file ADR chưa có trong checkout. Giữ `0/28` ca chạy, không PASS/FAIL, không đóng rủi ro. |
| 15/10/2026 | T1C | Bản 0.4: ADR-004/005 đã có file — thêm link [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) / [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md); bỏ ghi chú "file ADR chưa có trong checkout"; đánh dấu B-3 hoàn tất ở mức ghi tài liệu. Giữ B-1/B-2/B-4 mở, B-5 role PoC, `0/28` SEC, mọi trạng thái `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.5: đồng bộ bằng chứng WP-00/01 (T1A) — làm rõ Docker Desktop **đã cài và đang chạy** nhưng Linux engine trả **HTTP 500**, WSL2 chưa cài; cập nhật câu runtime/blocker tương ứng (§0.2, §11.1), thêm link `environment-probe.md`/`dev-setup.md`. Giữ B-1/B-2/B-4 mở, B-3 hoàn tất ở mức ghi ADR, B-5 role PoC, `0/28` SEC, mọi trạng thái `CHƯA XÁC MINH`. |
| 19/10/2026 | T1C | **Bản 1.0 — độc lập kiểm chứng.** Sửa câu lịch sử "chưa có security suite" trong §0.2 và heading; **phân biệt ba lượt chạy có tổng test khác nhau (26 / 27 / 28)** bằng revision + lệnh + site (xem bảng §0.2 mới, §11.6 mới). Thay §11.6 để mỗi SEC case có `evidence source · revision · lệnh/site` riêng; **không** gộp thành 28. Mở rộng §12: **~38 ô matrix còn `CHƯA XÁC MINH`**, **SEC-13/14/16 vẫn `NOT RUN`**, **SEC-26 `NOT IMPLEMENTED coverage`**, SEC-15/22/23/27 `PARTIAL`. Thêm 5 blocker mới (B-T1C-1…5) trong §13 và link [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md). Không tuyên bố hệ thống an toàn; **G0-07 vẫn PARTIAL**, không P0 GO. Phiên Codex p0-audit-refresh **không có Docker/WSL2**, nên tái kiểm bằng phân tích tĩnh + đối chiếu log T1B tại revision `b382fe4`. |
