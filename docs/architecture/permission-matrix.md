# Permission Matrix — RBAC / ACL (P0)

> **Owner:** T1C — Thiết kế & kiểm chứng bảo mật P0
> **Phiên bản:** 0.9 (runtime coverage) — 15/10/2026
> **Trạng thái:** 18 permission-boundary tests chạy trong suite app (28/28 test tổng thể đạt). Bảng ~70 ô vẫn `CHƯA XÁC MINH` trừ khi từng ô có test mapping cụ thể.
> **Đối chiếu checkout (0.9):** GitHub Actions PR #1 đã xác nhận clean bootstrap, lint/format và suite 28 tests ([run](https://github.com/nobita6986/new-crm/actions/runs/38712593755)). Security suite có scope owner/team, child records, private file, export, assignment, BOD và field-level ([`test_permissions.py`](../../apps/crm_cskh/crm_cskh/tests/test_permissions.py), [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md)). SEC-13/14/27 còn thiếu xác minh transport/full path; không suy rộng 28 test app thành 28 SEC case hoàn chỉnh.
> **ADR-004/005:** quyết định đã được T0 chốt — ADR-004: thực thi quyền ở **backend/record-level** + ownership service tập trung ([`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)); ADR-005: `BOD_VIEWER` **read-only**, **không** System Manager ([`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md)). Runtime/security **CHƯA XÁC MINH**.
> **Tài liệu cha:** [`../root-plan/master-plan.md`](../root-plan/master-plan.md) §7, §9, §14 · [`../root-plan/P0.md`](../root-plan/P0.md) §7, §12
> **Phạm vi lượt này:** T0 cập nhật bằng chứng WP-05 trên app/test site cô lập; không sửa upstream.

---

## 0. Quy tắc nguồn & bằng chứng (bắt buộc đọc)

### 0.1. Nguồn đúng phiên bản

| Thành phần | Phiên bản / revision | Trạng thái |
|---|---|---|
| Frappe CRM (baseline khảo sát) | tag **`v1.87.0`** (09/10/2026) | ĐÃ KHÓA (theo tài liệu cha) |
| Frappe Framework | tag **`v15.123.1`** (SHA `d2efa796…`) | Baseline install/migrate chạy trên host hiện tại; permission behavior **CHƯA XÁC MINH** |
| frappe_docker | tag **`v4.0.1`** (SHA `2b26e9ab…`) | Docker/Bench stack chạy trên host hiện tại; full clean-machine reproducibility **CHƯA XÁC MINH** |
| Custom app `crm_cskh` | Scaffold `0.0.1`, cài trên bench/site hiện có; 9 test POC đạt; security behavior chưa kiểm chứng | CHƯA XÁC MINH |
| **ADR-004 / ADR-005** (quyết định T0) | ADR-004 enforcement backend/record-level ([link](./adr-004-owner-team-permissions.md)) · ADR-005 BOD read-only ([link](./adr-005-bod-viewer-scope.md)) | ĐÃ GHI tài liệu (T0 chốt); runtime CHƯA XÁC MINH |
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

### 0.2. Trạng thái repo hiện tại (blocker nền)

Checkout có `apps/crm_cskh/`, `docs/` và `deploy/dev/`. WP-02 đã cài app POC trên bench/site hiện có, tạo test site và chạy 9 test; WP-03 đã probe owner/team sau khi bật hierarchy. **Chưa có security suite/negative tests WP-05**. MariaDB 11.8 còn caveat tương thích. Vì vậy:

- **Không ô "Thực tế" nào được điền PASS/FAIL.** Tất cả = `CHƯA XÁC MINH`.
- Mọi nhận định từ đọc code chỉ là **giả thuyết cần kiểm chứng**, KHÔNG phải kết luận lỗ hổng (tuân thủ P0 §7.4 ghi chú cuối).
- **Không tuyên bố hệ thống "an toàn".** Trạng thái an toàn chỉ được ghi khi `SEC-*` tương ứng chạy thật và có log.
- `deploy/dev/` là **cấu hình/script**, **không** đồng nghĩa đã có app/site/seed/test harness.

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

| Case | Actor | Dữ liệu (team/owner) | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm bắt buộc |
|---|---|---|---|---|---|---|
| LEAD-01 | A | A1 (Team1/A) | detail `frappe.client.get` | A | CHƯA XÁC MINH | SEC-01 |
| LEAD-02 | A | B1 (Team1/B) | detail by đoán name | **D** | CHƯA XÁC MINH | SEC-01 |
| LEAD-03 | A | C1 (Team2/C) | detail chéo team | **D** | CHƯA XÁC MINH | SEC-01, SEC-06 |
| LEAD-04 | A | list | `get_data` (list/search) | A (chỉ A1–A4) | CHƯA XÁC MINH | SEC-02 |
| LEAD-05 | A | list | `get_data` filter bị sửa/bỏ | A scope | CHƯA XÁC MINH | SEC-02 |
| LEAD-06 | A | B1 | ghi trực tiếp `CRM Lead` (kể cả field CSKH) | **D** (P0: từ chối sửa trực tiếp) | CHƯA XÁC MINH | SEC-04 |
| LEAD-07 | A | U1 (chưa giao) | detail | **D** | CHƯA XÁC MINH | SEC-01 |
| LEAD-08 | A | A1 (own) | export danh sách SĐT | **D** mặc định | CHƯA XÁC MINH | SEC-12 |
| LEAD-09 | TL1 | A1–A4, B1–B4 | list/detail Team 1 | A (Team 1) | CHƯA XÁC MINH | SEC-06 |
| LEAD-10 | TL1 | C1 (Team2/C) | list/detail chéo team | **D** | CHƯA XÁC MINH | SEC-06 |
| LEAD-11 | TL2 | A1 (Team1/A) | list/detail chéo team | **D** | CHƯA XÁC MINH | SEC-06 |
| LEAD-12 | BOD | toàn công ty | list/detail (report scope, read-only) | **C** (read-only, ADR-005) | CHƯA XÁC MINH | SEC-15 |
| LEAD-13 | CRM-ADMIN | toàn kho | list/detail/import | **C** | CHƯA XÁC MINH | — |
| LEAD-14 | A | A1 (own) | ghi trực tiếp `CRM Lead` (field ngoài whitelist CSKH) | **D** (P0: từ chối sửa trực tiếp) | CHƯA XÁC MINH | SEC-08 |
| LEAD-15 | Bất kỳ | — | `get_data` đổi `doctype` sang doctype khác | bị bound bởi quyền doctype | CHƯA XÁC MINH | SEC-25 |
| LEAD-16 | A | B1 | `get_data` chọn `rows`/`columns` = `mobile_no`,`email` ngoài phạm vi | **D** | CHƯA XÁC MINH | SEC-25 |

**Cơ chế đã đọc trên `v1.87.0` (giả thuyết, cần test):** `crm/hooks.py` chỉ đăng ký `permission_query_conditions`/`has_permission` cho `CRM Lead`, `CRM Deal`, `CRM Notification`; logic nằm ở `crm/permissions/org_hierarchy.py` với `_OWNER_FIELD = {"CRM Lead": "lead_owner", "CRM Deal": "deal_owner"}`.

> **Chính sách P0 cho LEAD-06/14 (theo T0):** P0 **từ chối mọi sửa trực tiếp `CRM Lead`** từ nhân viên. Field CSKH chỉ được ghi qua **whitelist + endpoint server-side** trong `crm_cskh`, và chỉ sau khi **schema cụ thể được chốt** (P1/P2). Vì vậy kỳ vọng P0 cho cả hai ca là **D**; việc mở whitelist sẽ được đặc tả và test riêng khi có schema. Cả hai ca vẫn `CHƯA XÁC MINH` ở runtime.

---

## 3. MA TRẬN B — Call Log (`CRM Call Log`)

Endpoint: `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log`, `get_data`, `get_linked_calls` (trong `activities.py`).

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|---|
| CL-01 | A | Call của A1 | `get_call_log` | C (own) | CHƯA XÁC MINH | — |
| CL-02 | A | Call của B1 | `get_call_log` by name | **D** | CHƯA XÁC MINH | SEC-03 |
| CL-03 | A | Call của C1 | `get_call_log` chéo team | **D** | CHƯA XÁC MINH | SEC-03 |
| CL-04 | A | B1 | `get_data("CRM Call Log")` list | **D** (không thấy call của B) | CHƯA XÁC MINH | SEC-03 |
| CL-05 | A | mọi call | `get_call_log` + đọc `from`/`to`/`recording_url` | **D** ngoài scope | CHƯA XÁC MINH | SEC-17 |
| CL-06 | TL1 | call Team 1 | list/detail | A (Team 1) | CHƯA XÁC MINH | SEC-06 |
| CL-07 | TL1 | call Team 2 | list/detail | **D** | CHƯA XÁC MINH | SEC-06 |
| CL-08 | BOD | toàn công ty | read call (read-only) | **C** | CHƯA XÁC MINH | SEC-15 |
| CL-09 | Guest | — | `get_call_log` | **D** | CHƯA XÁC MINH | SEC-13 |

**Giả thuyết cần test (R-03):** `crm/hooks.py` **không** đăng ký hook permission cho `CRM Call Log`; `get_call_log` chỉ gọi `frappe.has_permission("CRM Call Log","read",name)` → kiểm quyền **DocType/role**, không phải record-level. Nếu role `Sales User` (hoặc role tương đương) có quyền read trên DocType, khả năng đọc call log bất kỳ theo `name` là **cần kiểm chứng** (SEC-03/SEC-17). Không kết luận khi chưa chạy.

---

## 4. MA TRẬN C — Task (`CRM Task`)

Endpoint: `crm.api.activities.get_linked_tasks`, `get_data`, `frappe.client.*`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|---|
| TK-01 | A | Task của A1 | detail/list | C (own) | CHƯA XÁC MINH | — |
| TK-02 | A | Task của B1 | detail by name | **D** | CHƯA XÁC MINH | SEC-04, SEC-18 |
| TK-03 | A | B1 | sửa Task ngoài scope | **D** | CHƯA XÁC MINH | SEC-04 |
| TK-04 | A | U1 | tạo Task gắn lead ngoài scope | **D** | CHƯA XÁC MINH | SEC-04 |
| TK-05 | TL1 | Task Team 1 | list/detail | A | CHƯA XÁC MINH | SEC-06 |
| TK-06 | TL1 | Task Team 2 | list/detail | **D** | CHƯA XÁC MINH | SEC-06 |
| TK-07 | BOD | toàn công ty | read (read-only) | **C** | CHƯA XÁC MINH | SEC-15 |

**Liên quan:** `crm/api/todo.py` `after_insert`/`on_update` xử lý `CRM Task` (notify), và với Lead/Deal thì **ghi đè `lead_owner`**. `clear_owner_on_unassign` **chỉ** áp dụng Lead/Deal (comment trong code: "Owner concept only exists for Lead/Deal, not Task").

**Giả thuyết cần test (R-03):** không có hook permission cho `CRM Task` → quyền record-level phụ thuộc DocType role + DocShare/ToDo. Cần test đọc Task theo `name` của user khác (SEC-04/SEC-18).

---

## 5. MA TRẬN D — Note / Comment (`FCRM Note`, `Comment`)

Endpoint: `crm.api.activities.get_linked_notes`, `get_attachments("Comment", ...)`, `get_data`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|---|
| NT-01 | A | Note của A1 | read | C (own) | CHƯA XÁC MINH | — |
| NT-02 | A | Note của B1 | read by name | **D** | CHƯA XÁC MINH | SEC-04, SEC-18 |
| NT-03 | A | Comment của B1 | read | **D** | CHƯA XÁC MINH | SEC-04 |
| NT-04 | A | B1 | tạo Note/Comment ngoài scope | **D** | CHƯA XÁC MINH | SEC-04 |
| NT-05 | TL1 | Note Team 1 | read | A | CHƯA XÁC MINH | SEC-06 |
| NT-06 | TL1 | Note Team 2 | read | **D** | CHƯA XÁC MINH | SEC-06 |
| NT-07 | BOD | toàn công ty | read (read-only) | **C** | CHƯA XÁC MINH | SEC-15 |

**Ghi chú:** `FCRM Note` là DocType bổ trợ (không thay kết quả CSKH có cấu trúc — master-plan §3.1). Quyền note/comment **cần khớp** với quyền lead mẹ.

---

## 6. MA TRẬN E — Attachment / File private (`File`, `is_private=1`)

Endpoint: `get_attachments` (trong `activities.py`), download URL `/private/files/...` (`frappe.client`/`File` API).

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|---|
| AT-01 | A | File của A1 | tải qua URL/token hợp lệ | C (own) | CHƯA XÁC MINH | — |
| AT-02 | A | File private của B1 | tải trực tiếp URL | **D** | CHƯA XÁC MINH | SEC-05 |
| AT-03 | A | File của C1 | tải chéo team | **D** | CHƯA XÁC MINH | SEC-05 |
| AT-04 | A | B1 | `get_attachments`/`get_data` trả `file_url` ngoài scope | **D** (không lộ URL) | CHƯA XÁC MINH | SEC-26 |
| AT-05 | BOD | toàn công ty | tải file theo scope báo cáo (read-only) | **C** | CHƯA XÁC MINH | SEC-16 |
| AT-06 | Guest | bất kỳ | tải `/private/files/...` | **D** | CHƯA XÁC MINH | SEC-13 |

**Giả thuyết cần test:** `get_attachments` dùng `frappe.db.get_all("File", ...)` trả cả `file_url` cho file private. Cần xác minh (a) URL private có thực sự bị chặn khi tải ngoài scope, (b) API có lộ URL trước khi kiểm quyền không (SEC-05/SEC-26).

---

## 7. MA TRẬN F — Chuyển giao / Reassignment & Assignment endpoints

Endpoint: `crm.api.doc.remove_assignments`, `crm.api.doc.get_assigned_users`, `crm.api.assignment_rule.*`, hook `crm/api/todo.py`, `crm_lead.assign_agent`/`share_with_agent`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|---|
| RA-01 | A | A1 | tự đổi `lead_owner` A1→B | **D** | CHƯA XÁC MINH | SEC-08 |
| RA-02 | A | B1 | assign cho người khác (ToDo) | **D** | CHƯA XÁC MINH | SEC-08 |
| RA-03 | A | A1 | gọi mutation với `ignore_permissions=true` | **D** | CHƯA XÁC MINH | SEC-09, SEC-21 |
| RA-04 | CRM-ADMIN | A1 | chuyển A1: A→B (nội bộ Team 1) | A | CHƯA XÁC MINH | SEC-10 |
| RA-05 | CRM-ADMIN | C1 | chuyển C1: C→A (Team2→Team1) | A | CHƯA XÁC MINH | SEC-11 |
| RA-06 | TL1 | Lead Team 1 | chuyển trong team | **C** | CHƯA XÁC MINH | SEC-06 |
| RA-07 | TL1 | Lead Team 2 | chuyển chéo team | **D** | CHƯA XÁC MINH | SEC-06 |
| RA-08 | BOD | bất kỳ | chuyển/thu hồi | **D** mặc định (read-only) | CHƯA XÁC MINH | SEC-15 |
| RA-09 | A (cũ) | sau A1: A→B | đọc lại Lead/Call/Note/Task/File | **D** (theo policy) | CHƯA XÁC MINH | SEC-10, SEC-22, SEC-23 |
| RA-10 | B (mới) | sau A1: A→B | đọc/ghi Lead/Call/Note/Task/File | A | CHƯA XÁC MINH | SEC-10 |

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

| Case | Actor | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|
| LS-01 | A | `frappe.client.get_list("CRM Lead", ...)` | scope A | CHƯA XÁC MINH | SEC-02 |
| LS-02 | A | global search trả lead ngoài scope | **D** | CHƯA XÁC MINH | SEC-02 |
| LS-03 | A | export/CSV danh sách SĐT | **D** mặc định | CHƯA XÁC MINH | SEC-12 |
| LS-04 | A | `get_data` link lookup / dropdown ngoài scope | **D** | CHƯA XÁC MINH | SEC-02 |
| LS-05 | A | `get_data` `doctype` tùy biến (KV khác) | bound bởi quyền doctype | CHƯA XÁC MINH | SEC-25 |
| LS-06 | A | `get_data` `rows`/`columns` chọn field nhạy cảm ngoài scope | **D** | CHƯA XÁC MINH | SEC-25 |
| LS-07 | A | bulk (`delete_bulk_docs`) chéo scope | **D** | CHƯA XÁC MINH | SEC-12 |
| LS-08 | TL1 | report Team 1 | Team 1 | CHƯA XÁC MINH | SEC-06 |
| LS-09 | TL1 | report Team 2 | **D** | CHƯA XÁC MINH | SEC-06 |
| LS-10 | BOD | report toàn công ty (read-only) | **C** | CHƯA XÁC MINH | SEC-15 |

**Giả thuyết:** `get_data` nhận `doctype`, `filters`, `default_filters`, `rows`, `columns` từ client. `rows`/`columns` cho phép chọn field tùy ý (`mobile_no`, `email`…) — cần test field-level exposure (SEC-25), kể cả trong scope (Medium) và ngoài scope (High nếu lọt).

---

## 9. MA TRẬN H — Báo cáo/Admin & BOD viewer (ADR-005)

| Case | Actor | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|
| BD-01 | BOD | xem báo cáo/kho (read-only) | **C** (không cần System Manager) | CHƯA XÁC MINH | SEC-15 |
| BD-02 | BOD | API quản trị user/role | **D** | CHƯA XÁC MINH | SEC-15 |
| BD-03 | BOD | import/xoá dữ liệu | **D** | CHƯA XÁC MINH | SEC-15 |
| BD-04 | BOD | reassign/thu hồi | **D** mặc định | CHƯA XÁC MINH | SEC-15 |
| BD-05 | BOD | `System Manager` ngầm qua role khác | **D** | CHƯA XÁC MINH | SEC-15 |
| BD-06 | CRM-ADMIN | cấu hình quyền hệ thống | **D** (trừ System Manager) | CHƯA XÁC MINH | — |

**Phân tích BOD (ADR-005, T0 chốt):** trên `v1.87.0`, hook `permission_query_conditions`/`has_permission` chỉ đăng ký cho Lead/Deal/Notification; **không có role/API báo cáo read-only đóng gói sẵn**. Master-plan §2.3 yêu cầu BOD xem báo cáo **không** cần `System Manager`. **Quyết định ADR-005:** `BOD_VIEWER` là role **read-only** xem báo cáo theo phạm vi, **không** cấp `System Manager`; role + permission + API báo cáo được xây trong `crm_cskh` (không dựa vào quyền rộng của baseline). Trạng thái runtime/security **CHƯA XÁC MINH** — phải chứng minh bằng SEC-15/SEC-28 sau khi có app. Chi tiết: `risk-register.md` `RISK-SEC-09`.

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

## 11. Test plan (chưa thực thi — chờ app/API + baseline)

### 11.1. Điều kiện tiên quyết (blocker)

1. Baseline Frappe CRM `v1.87.0` + Framework `v15.123.1` **đã bootstrap/install/migrate/seed và smoke UI/API trên host hiện tại** ([`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md)); MariaDB 11.8 compatibility caveat còn mở.
2. App `crm_cskh` cài cùng site; có test site riêng `test_cskh.localhost`.
3. Seed đủ 2 team + tài khoản mục §1, deterministic, reset được.
4. Test harness Frappe; thư mục `tests/security/`.

### 11.2. Tổ chức test

- Đặt tại `apps/crm_cskh/crm_cskh/tests/security/` (theo Frappe conventions), tên `test_sec_*.py`.
- Chạy: `bench --site test_cskh.localhost run-tests --app crm_cskh`.
- Dùng API key/session của **tài khoản test** qua `frappe.set_user(...)`; **không** commit token/cookie.
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

| SEC | Kết quả | Bằng chứng / giới hạn |
|---|---|---|
| 01–09 | PASS theo các route được gọi trong suite | Lead scope/get_data, child reads, file, team, manager ngoài cây, owner change, `ignore_permissions`; xem test names trong `test_permissions.py`. |
| 10–12 | PASS trong kịch bản reassignment/export đã chạy | Cũ/mới owner, child records, ToDo, private file, team transfer, export và bulk denial. Chưa bao phủ mọi endpoint không được liệt kê trong inventory. |
| 13 | PARTIAL | Health API Guest bị chặn ở whitelist/auth gate; chưa kiểm tra reverse proxy/HTTP status end-to-end trong CI. |
| 14 | PARTIAL | Browser POST dùng session + CSRF token hợp lệ thành công; request thiếu/sai token chưa có negative test riêng. |
| 15–26 | PASS theo test ánh xạ hiện tại | BOD fail-closed, linked activity/assignment APIs, assignment rule, private attachments và `get_data` permlevel field. Đây không phải PASS mọi ô matrix. |
| 27 | PARTIAL | Phone lookup API bị chặn cho role thấp; nhánh `get_call_contact` nội bộ khi Call Log không tham chiếu Lead chưa được gọi trực tiếp trong negative test. |
| 28 | PASS trong test role PoC | BOD không có `System Manager`; User/Role/Data Import create bị từ chối. |

---

## 12. Trạng thái xác minh tổng hợp

- Số ca thiết kế: **~70 ô ma trận**, 28 ca test âm (`SEC-01…SEC-28`).
- 18 permission-boundary test methods chạy trong suite app; toàn suite **28/28 PASS** trên test site sạch.
- SEC coverage: **PASS có giới hạn** cho các case đã mapping; SEC-13/14/27 `PARTIAL`; ~70 ô matrix chưa có từng kết quả vẫn `CHƯA XÁC MINH`.
- Không kết luận toàn hệ thống an toàn; CI PR đã pass nhưng security transport checks và các ô matrix chưa chạy vẫn mở.

## 13. Việc cần T0 quyết / blocker

1. **WP-05 đã có suite và remediation fail-closed cho các API đã test; GitHub Actions PR #1 đã pass.** Còn phải hoàn thiện SEC-13/14/27 và kiểm tra đầy đủ các ô matrix; trạng thái chi tiết ở [`p0-exit-report.md`](./p0-exit-report.md) và `risk-register.md` §4.
2. **ADR-004/005: đã ghi tài liệu** — T0 chốt nội dung; file có trong checkout: [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) (thực thi quyền owner/team ở backend/record-level + ownership service tập trung) và [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md) (`BOD_VIEWER` read-only, không System Manager). Runtime/security **CHƯA XÁC MINH**.
3. Role `CRM_ADMIN`/`BOD_VIEWER`: **giữ là role PoC** theo P0 §7.1 (BOD read-only). Whitelist field CSKH cho Staff + endpoint server-side (LEAD-06/14) sẽ **chốt cùng schema cụ thể ở P1/P2** — P0 từ chối sửa trực tiếp `CRM Lead`.
4. Không cấp thêm quyền Staff/BOD/CRM_ADMIN hoặc triển khai dữ liệu thật trước khi các gate còn partial được đóng.

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
