# Risk Register — Security (P0)

> **Owner:** T1C — Thiết kế & kiểm chứng bảo mật P0
> **Phiên bản:** 1.0 (independent verification, T1C) — 2026-10-19
> **Trạng thái (T1C đối chiếu độc lập):** runtime/security đã có evidence — **ba lượt chạy có tổng test khác nhau** đã ghi nhận (26 / 27 / 28 OK trên `wp05-security-final.txt`, `p0-local-final.txt`, `p0-isolated-verification.md` CI run `#38712593755`); 18 permission-boundary methods đã phân tích từng assertion, mapping sang SEC case và ~32/70 ô matrix. RISK-SEC-12 vẫn mở; **SEC-13/14/16/26 vẫn `NOT RUN`**; **nhánh nội bộ SEC-27 vẫn `NOT RUN`**; **~38 ô matrix còn `CHƯA XÁC MINH`**. Xem [`permission-matrix.md`](./permission-matrix.md) §11.6, §12 và [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) để đối chiếu.
> **Đối chiếu checkout (1.0):** code đã đọc (`apps/crm_cskh/crm_cskh/tests/test_permissions.py` 543 dòng; `permissions.py`; `hooks.py`; `api/guarded_crm.py`; `api/guarded_reporting.py`). Phiên Codex p0-audit-refresh **không có Docker/WSL2** nên T1C **không chạy lại** site sạch mà tái tạo bằng phân tích tĩnh + đối chiếu log T1B ở revision `b382fe4`. B-2/B-4 đã gỡ trước đó; B-3 ghi ADR; B-5 theo role PoC. **B-T1C-1…5** mở (xem §4 và `t1c-security-verification.md` §6).
> **ADR-004/005:** ADR-004 — thực thi quyền owner/team ở **backend/record-level** + ownership service tập trung ([`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)); ADR-005 — `BOD_VIEWER` **read-only**, **không** System Manager ([`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md)). Runtime đã chạy cho route đã test (qua 18 test methods); route chưa test vẫn còn rủi ro tiềm ẩn.
> **Tài liệu cha:** [`../root-plan/master-plan.md`](../root-plan/master-plan.md) §16 · [`../root-plan/P0.md`](../root-plan/P0.md) §13
> **Tài liệu liên quan:** [`permission-matrix.md`](./permission-matrix.md); [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) (đối chiếu độc lập).

---

## 0. Nguồn đúng phiên bản

| Thành phần | Revision | Trạng thái |
|---|---|---|
| Frappe CRM | tag **`v1.87.0`** (09/10/2026) | ĐÃ KHÓA |
| Frappe Framework `v15.123.1` (SHA `d2efa796…`) · frappe_docker `v4.0.1` (SHA `2b26e9ab…`) | ĐÃ KHÓA metadata (`version-lock.md`) | Baseline install/migrate đã chạy; MariaDB 11.8 tương thích còn caveat; permission/security CHƯA XÁC MINH |
| Custom app `crm_cskh` | Scaffold `0.0.1`, cài trên bench/site hiện có; 9 test POC đạt; security behavior chưa kiểm chứng | CHƯA XÁC MINH |
| **ADR-004/005** (quyết định T0) | ADR-004 enforcement backend/record-level ([link](./adr-004-owner-team-permissions.md)) · ADR-005 BOD read-only ([link](./adr-005-bod-viewer-scope.md)) | ĐÃ GHI tài liệu (T0 chốt); runtime CHƯA XÁC MINH |
| `master-plan.md` v1.0 (14/10/2026), `P0.md` v1.0 (15/10/2026) | — | Nguồn đặc tả |

> **Nguyên tắc bằng chứng:** "Giả thuyết (code)" là nhận định từ source `v1.87.0`. `ĐANG GIẢM THIỂU` chỉ nói test đã chặn được đường truy cập cụ thể; không khẳng định mọi endpoint/ô matrix đều an toàn.

---

## 1. Phân loại severity

| Mức | Định nghĩa |
|---|---|
| **Critical** | Truy cập chéo PII khách (số điện thoại/tên) hoặc ghi chéo ở quy mô bulk bởi actor quyền thấp; leo thang lên `System Manager`; lộ toàn bộ kho số. |
| **High** | Đọc chéo đối tượng con (Call Log/Note/Task/attachment) của khách ngoài phạm vi; reassignment không thu hồi quyền người cũ; bypass assignment; đọc file private ngoài phạm vi. |
| **Medium** | Lộ metadata (assignee, tên rule, cấu hình), lộ field trong phạm vi được phép, thiếu audit, list vượt phạm vi nhưng không lộ PII trực tiếp. |
| **Low** | Lộ thông tin cấu hình/hệ thống không nhạy cảm; vấn đề UX. |

**Trạng thái:** `MỞ — CHƯA XÁC MINH` · `ĐANG GIẢM THIỂU` · `ĐÃ ĐÓNG (có test)` · `CHẤP NHẬN (có lý do)`.

**Lưu ý:** severity ghi ở đây là **dự kiến/điều kiện** — sau khi có bằng chứng runtime có thể tăng/giảm. Việc một rủi ro có "hướng đã chốt" (ADR) **không** được tính là đã đóng khi chưa có test.

---

## 2. Bảng rủi ro

| ID | Rủi ro | Nhóm | Severity (dự kiến) | Bằng chứng | Trạng thái | Test | Giảm thiểu |
|---|---|---|---|---|---|---|---|
| RISK-SEC-01 | Call Log/Note/Task/File có nguy cơ đọc chéo theo `name` | Cross-access | **Critical** / High | Giả thuyết code `v1.87.0`; `crm_cskh/hooks.py` đã thêm hook cho 5 child doctype + `permissions.linked_lead_has_permission` deny cross-record; 18 test methods đã verify (xem `permission-matrix.md` §11.6 mapping cho SEC-03/17/18); test methods `Test #3` (L261), `Test #6` (L356), `Test #9` (L403) đều assert cross-scope `PermissionError` cho Call/Note/Task/Comment/File | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-03, SEC-17, SEC-18, CL-02/03/05, NT-02/03, TK-02 | Giữ hook + extend thêm route nếu mở rộng; không đủ để đóng — vẫn còn ~38 ô matrix chưa có test mapping |
| RISK-SEC-02 | Reassignment desync `lead_owner`/`ToDo`/`DocShare` có thể giữ quyền cũ | Reassignment | **Critical** | Tests chuyển owner và xác minh người cũ mất Lead/child/file/ToDo; assignment mutation Staff bị chặn; `Test #12` (L450) cho direction ToDo-cũ; `Test #13` (L474) cho cross-team reassign. **CHƯA XÁC MINH** direction ngược lại (DocShare-cũ còn khi reassign qua lead_owner — xem SEC-23). | ĐANG GIẢM THIỂU — service chưa triển khai; SEC-22 partial, SEC-23 partial | SEC-10, SEC-11, SEC-22, SEC-23, RA-04/05/09 | Fail-closed với role thấp (`Test #10`, `Test #14`); ownership service ADR-004 vẫn cần thiết trước khi bật reassignment nghiệp vụ |
| RISK-SEC-03 | `get_assigned_users` có thể lộ assignee | Info disclosure | Medium | Test cho Lead owner và chặn Lead ngoài scope; `Test #9` (L403) `guarded_crm.get_assigned_users` raise cho cross-scope | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-19 | Giữ wrapper + filter user_in_scope (xem `guarded_crm.py` L36) |
| RISK-SEC-04 | `get_linked_docs_of_document` có thể lộ `from`/`to` | Info disclosure | **High** | Test route từ chối Lead ngoài scope; `Test #9` (L403) `guarded_crm.get_linked_docs_of_document` raise; wrapper `guarded_crm.py` L40 filter linked docs qua `frappe.has_permission`. **CHƯA XÁC MINH** field-level: `Call Log.from/to` có lộ trong dict trả về cho call **trong** scope không. | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-20, NT-04 | Giữ wrapper + xác minh field exposure trong scope (Medium) |
| RISK-SEC-05 | `remove_assignments` nhận `ignore_permissions` từ client | Assignment bypass | **High** | Test gọi `ignore_permissions=true` từ Staff và bị từ chối; `Test #10` (L423) raise `PermissionError`. **CHƯA XÁC MINH** cùng pattern cho System Manager (proxy fail-open cố ý cho Admin không có negative). | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-09, SEC-21, RA-03 | Chỉ System Manager được gọi (qua `permissions.remove_assignments` L168 strip `ignore_permissions=False`); không tin cờ permission client |
| RISK-SEC-06 | Sales Manager ngoài cây hierarchy có thể thấy mọi Lead | Over-broad | **High** | Test manager không có node trong cây nhận danh sách rỗng và bị từ chối detail; `Test #8` (L397) chứng minh. CHƯA XÁC MINH: manager **có** hierarchy nhưng reassign Lead ngoài scope; `Test #7` (L388) chỉ cross-team cho TL1. | ĐANG GIẢM THIỂU — runtime đã pass per-route cho manager-without-hierarchy | SEC-07, RISK-SEC-06, LEAD-09/10/11 | Giữ fail-closed khi user không có hierarchy node; `_manager_without_scope` xem `permissions.py` L17 |
| RISK-SEC-07 | `get_data` có thể trả field vượt permlevel | Field exposure | Medium→High | Test field synthetic `permlevel=1` không xuất hiện cho Staff (`Test #2` L227). CHƯA XÁC MINH: `rows=["mobile_no"]` ngoài scope; cross-doctype spoof; LS-06, LEAD-15, LEAD-16 partial | ĐANG GIẢM THIỂU — runtime đã pass per-route cho permlevel=1 | SEC-25, LEAD-16, LS-05/06 | Rà soát thêm mọi field list/report trước khi cấp rộng quyền |
| RISK-SEC-08 | `duplicate_assignment_rule` có thể sửa cấu hình | Config tamper | Medium | Test Staff bị từ chối và rule duplicate không được tạo; `Test #15` (L504) chứng minh | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-24 | Giữ quyền create theo role hệ thống |
| RISK-SEC-09 | BOD có thể cần `System Manager` nếu thiếu role read-only | Least privilege | **High** | Tests xác minh BOD role không có System Manager và không thể tạo User/Role/Data Import; report API chưa xây; `Test #16` (L528), `Test #17` (L537) | ĐANG GIẢM THIỂU — fail-closed đã pass per-route; reporting scope chưa build | SEC-15, SEC-28, BD-01/02/03/05 | Giữ BOD không có System Manager; xây reporting scope riêng ở P1/P3 |
| RISK-SEC-10 | `file_url` private có thể lộ qua activities/attachments | File exposure | Medium | Test file direct và get_attachments theo quyền Lead; `Test #4` (L285), `Test #9` (L403) deny cross-scope. **CHƯA XÁC MINH** field-level: dict `get_attachments(lead own)` có lộ `file_url` private trong JSON response hay không (xem `crm.api.activities.get_attachments`) | ĐANG GIẢM THIỂU — runtime partial; SEC-26 `NOT RUN` | SEC-05, SEC-26, AT-02/03/04 | Kiểm tra khi thêm storage/CDN hoặc API tải file mới; bổ sung test response shape |
| RISK-SEC-11 | `get_call_contact` có thể tra contact theo số không tham chiếu | Info disclosure | Medium→High | Phone lookup APIs bị Staff chặn (`Test #5` L319 với `P0-LOOKUP-NO-MATCH`). **CHƯA XÁC MINH** nhánh nội bộ `crm_call_log.get_call_contact` khi `Call Log` không tham chiếu Lead. | ĐANG GIẢM THIỂU — endpoint đã pass per-route; nhánh internal **NOT RUN** | SEC-27 | Bổ sung test cho fallback nội bộ (B-T1C-3) trước P1 |
| RISK-SEC-12 | `remove_assignments`/`set_status` huỷ ToDo → **clear owner** của Lead đa-assignee | Data integrity | Medium | Giả thuyết (code): comment "accepted wrinkle" trong `todo.clear_owner_on_unassign` | MỞ — CHƯA XÁC MINH | SEC-22, RA-05 | Owner single-valued có chủ đích; ghi chính sách co-assignee |
| RISK-SEC-13 | Child records có thể rộng quyền hơn Lead | Cross-access | **Critical** | Negative tests xác minh Staff không đọc child record ngoài scope; `Test #3` (L261), `Test #6` (L356). CHƯA XÁC MINH: ~38 ô matrix liên quan chưa có test mapping | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-03, SEC-04, SEC-17, SEC-18 | Mở rộng test route trước khi đóng rủi ro |
| RISK-SEC-14 | Staff có thể tự gán quyền hoặc ghi chéo | Assignment bypass | **High** | Owner change (`Test #10` L423), ToDo assignment (`Test #14` L490), `ignore_permissions` mutation bị từ chối trong tests | ĐANG GIẢM THIỂU — runtime đã pass per-route | SEC-08, SEC-09, RA-01/02/03 | Giữ assignment fail-closed cho tới ownership service |

> RISK-SEC-13/14 là **ánh xạ** các rủi ro R-03/R-05 trong P0 §13 sang ID cục bộ để truy vết test. R-01, R-02, R-06…R-12 (hạ tầng, i18n, packaging) thuộc owner khác, không lặp ở đây.

---

## 3. Chi tiết ứng viên Critical/High

### RISK-SEC-01 / RISK-SEC-13 — Call Log/Note/Task/File không record-level
- **Bằng chứng (code, tag v1.87.0):** `crm/hooks.py` đăng ký `permission_query_conditions`/`has_permission` **chỉ** cho `CRM Lead`, `CRM Deal`, `CRM Notification`. `crm/fcrm/doctype/crm_call_log/crm_call_log.py::get_call_log` chỉ gọi `frappe.has_permission("CRM Call Log","read",name)`.
- **Giả thuyết:** nếu role `Sales User` (hoặc role tương đương) có quyền read trên DocType `CRM Call Log`, user có thể đọc call log bất kỳ theo `name` bất kể lead mẹ thuộc ai.
- **Vì sao Critical/High:** Call Log chứa `from`/`to` (số điện thoại) và `recording_url` → PII.
- **Đã chạy:** tests chặn đọc Call Log/Note/Task/Comment/File ngoài Lead scope, kể cả lookup theo tên; residual API transport vẫn cần rà riêng.

### RISK-SEC-02 — Desync reassignment
- **Bằng chứng (code, tag v1.87.0):**
  - `crm/fcrm/doctype/crm_lead/crm_lead.py::share_with_agent` thêm DocShare `write=1` cho agent mới và **gỡ** DocShare của user ≠ agent; `assign_agent` thêm ToDo nhưng **không** huỷ ToDo cũ.
  - `crm/api/todo.py::after_insert` set `lead_owner` bằng `frappe.db.set_value` (**bypass controller** → không chạy `CRMLead.validate` → **không** đồng bộ DocShare).
- **Giả thuyết:** cả hai hướng chuyển giao đều để lại **một** đường quyền cũ (ToDo cũ hoặc DocShare cũ) → người cũ có thể vẫn truy cập.
- **Hướng đã chốt (ADR-004, T0):** ownership service tập trung vẫn là đích. Trong P0, non-system owner/assignment mutations đang bị từ chối; tests chuyển Lead rồi xác minh old owner mất quyền trên Lead, child records, file và ToDo.
- **Giới hạn:** admin reassignment chỉ được kiểm tra các đường đã nêu; ownership service chưa được xây để bật reassignment nghiệp vụ.

### RISK-SEC-05 — `ignore_permissions` client-controlled
- **Bằng chứng (code):** `crm/api/doc.py::remove_assignments(doctype, name, assignees, ignore_permissions=False)` — tham số do client truyền, chuyển vào `frappe.desk.form.assign_to.set_status(...)`.
- **Đã chạy:** Staff gọi override với `ignore_permissions=true` bị từ chối; không cho Staff thực hiện assignment mutation.

### RISK-SEC-06 — Sales Manager ngoài cây
- **Bằng chứng (code):** `crm/permissions/org_hierarchy.py::_permission_query_conditions` — `if "Sales Manager" in roles and not in_tree: return ""`.
- **Giả thuyết:** Sales Manager không nằm trong `CRM Sales Hierarchy` nhận **toàn bộ** dữ liệu (over-broad), trái nguyên tắc least privilege.
- **Đã chạy:** manager không có node hierarchy nhận danh sách rỗng và bị từ chối detail trong test.

### RISK-SEC-09 — BOD viewer / ADR-005
- **Bằng chứng (code):** không có role/API báo cáo read-only trong `v1.87.0`.
- **Tác động:** yêu cầu master-plan §2.3 ("BOD xem báo cáo **không** cần System Manager") **chưa có đường thỏa mãn** trên baseline → nếu cấp `System Manager` cho BOD sẽ vi phạm least privilege và mở SEC-15.
- **Quyết định ADR-005 (T0):** không cấp `System Manager` cho BOD. Tests xác minh role PoC không có System Manager và không thể tạo User/Role/Data Import.
- **Giới hạn:** API báo cáo BOD chưa được xây; hiện role fail-closed nên chưa có quyền xem báo cáo.

---

## 4. Blockers & trạng thái gỡ

> Cập nhật theo T0: **B-2 đã gỡ**; B-4 seed chạy; B-3 ghi ADR; B-5 role PoC. WP-05 suite chạy ba lần với **tổng test khác nhau** (26 / 27 / 28 OK trên ba site/lần/revision tại `b382fe4`, xem `t1c-security-verification.md` §2); 18 permission-boundary methods đã được T1C đối chiếu per-cell. **B-1 còn mở** cho SEC-13/14/16/26, nhánh nội bộ SEC-27, RISK-SEC-12, và ~38 ô matrix chưa có test mapping. GitHub Actions PR #1 đã pass. Không tuyên bố toàn hệ thống an toàn.

| # | Blocker | Ảnh hưởng | Cần để gỡ |
|---|---|---|---|
| B-1 | WP-05 suite đã chạy + remediation fail-closed cho route đã test; **SEC-13/14/16/26 và nhánh nội bộ SEC-27 vẫn chưa có test automation**; **~38/70 ô matrix chưa có test mapping**; **RISK-SEC-12 còn mở**; ba lượt chạy tổng test khác nhau (26/27/28) chưa có lời giải | Không ký G0-07 tới khi transport/full-path gap được xử lý | Xem [`permission-matrix.md`](./permission-matrix.md) §11.6, §12; [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §6 (B-T1C-2…5) |
| B-2 | **ĐÃ GỠ trên host hiện tại:** WSL2 backend hoạt động; Docker Engine 29.7.2 chạy Linux; các service DEV lên khỏe ([`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md)) | Setup trên máy thứ hai/clean machine chưa chứng minh | Giữ cấu hình; Linux DEV VM là dự phòng nếu engine hiện tại hỏng |
| B-3 | **HOÀN TẤT ở mức ghi tài liệu** — ADR-004/005 đã được T0 chốt và có file trong checkout: [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md), [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md); **runtime đã chạy route đã test** (xem §2 và `permission-matrix.md` §0.1) | Quyết định + tài liệu đã có; **không** thay thế route chưa test | — (đã xong ở mức ghi tài liệu; runtime còn route chưa phủ) |
| B-4 | **ĐÃ CHẠY:** seed tạo hai nhóm sales, user giả, 15 Lead, 3 Call Log, 2 Task, attachment; seed lần hai idempotent. T0 bổ sung timezone + bật hierarchy; probe mẫu owner/team có evidence ở [`p0-d-runtime.md`](./p0-d-runtime.md) §5.1. | Fixture/config DEV đã có; không phải kết quả permission/security test | Dùng test suite WP-05 để kiểm chứng đầy đủ (hiện ~32/70 ô matrix đã có test mapping) |
| B-5 | ~~Thiếu role chuẩn `CRM-ADMIN`/`BOD`~~ → **chốt là role PoC** (`CRM_ADMIN`, `BOD_VIEWER` read-only) theo P0 §7.1 | Kỳ vọng ô `C` đã có cơ sở (PoC); runtime fail-closed đã verify cho `Test #16`, `Test #17` | Quyết định T0 (đã chốt); reporting scope BOD chưa build |
| **B-T1C-1** (mới T1C 19/10/2026) | Máy phiên Codex p0-audit-refresh **không có Docker/WSL2/bench** — không thể tái chạy site sạch để verify lại độc lập | T1C dùng phân tích tĩnh + đối chiếu log T1B; block tái kiểm | Owner bật lại WSL2 + Docker Desktop trên máy phiên, hoặc cấp log raw của 3 lượt chạy |
| **B-T1C-2** (mới) | **SEC-13/14/16/26** không có automated test ở `crm_cskh/tests/` | G0-07 PARTIAL → không ký khi thiếu transport test | Thêm `tests/security/test_sec_13_14_16_26.py` dùng `requests` qua `127.0.0.1:<port>` |
| **B-T1C-3** (mới) | **SEC-27 nhánh nội bộ** (`crm_call_log.get_call_contact` khi Call Log không tham chiếu Lead) chưa test | Info-disclosure Medium→High có thể còn open | Thêm test tạo `CRM Call Log` không có `reference_doctype`, gọi lookup path |
| **B-T1C-4** (mới) | **3 số tổng test khác nhau (26/27/28)** không có lời giải về nguyên nhân chênh | Không xác nhận test #18 có flaky hay không | Chạy lại `bench run-tests --verbose` site sạch, capture stdout |
| **B-T1C-5** (mới) | **~38/70 ô matrix chưa có `[Test: name @ file:line]`** | Không có manifest test mapping per-cell | Mapping hiện nằm trong đầu T1B; cần chuyển vào `permission-matrix.md` (đã có một phần trong §2–§9 bản 1.0) |

---

## 5. Bằng chứng hiện có (evidence log)

| Loại | Nội dung | Giá trị |
|---|---|---|
| Đọc source (T1C) | 8 file upstream trên tag `v1.87.0` (hooks, org_hierarchy, todo, crm_lead, doc, activities, crm_call_log, assignment_rule) | Cơ sở cho giả thuyết & test plan |
| Đọc source (đối chiếu) | [`reuse-audit.md`](./reuse-audit.md), [`data-model-spike.md`](./data-model-spike.md) — file:line tại commit `0d3910b` | Củng cố giả thuyết RISK-SEC-01/02/03/05/06 |
| Đọc source (T1C, mới 19/10/2026) | `apps/crm_cskh/crm_cskh/tests/test_permissions.py` 543 dòng (18 methods), `apps/crm_cskh/crm_cskh/permissions.py`, `hooks.py`, `api/guarded_crm.py`, `api/guarded_reporting.py`, `apps/crm_cskh/crm_cskh/tests/test_health.py`, `test_poc_custom_field.py`, `doctype/cskh_app_settings/test_cskh_app_settings.py` | Phân tích tĩnh từng test method + override whitelist + hook; mapping per-cell T1C |
| Khóa version | [`version-lock.md`](./version-lock.md) (metadata/ref + image digest); `deploy/dev/evidence/environment-probe.md` | Provenance **metadata** — **không** phải bằng chứng runtime |
| Quyết định ADR | [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) (enforcement backend/record-level), [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md) (BOD read-only) | Quyết định có thẩm quyền; là **tài liệu thiết kế** — runtime đã chạy cho route đã test, **không thay thế route chưa test** |
| WP-01 runtime | [`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md) + raw logs trong `deploy/dev/evidence/` | Docker/WSL2, app baseline install/migrate, seed idempotence, authenticated UI/API smoke đã chạy trên host hiện tại; MariaDB 11.8 caveat còn mở |
| WP-02/03 runtime | [`wp-02-p0b.md`](../../deploy/dev/evidence/wp-02-p0b.md) + [`p0-d-runtime.md`](./p0-d-runtime.md) §5.1 | App POC/test 9 ca; timezone và owner/team probe mẫu; không phải WP-05 |
| WP-05 security (log T1B) | `wp05-security-final.txt` — **26 OK** tại `b382fe4`, site `test_cskh.localhost`, lệnh `bench --site test_cskh.localhost run-tests --app crm_cskh`; `p0-local-final.txt` — **27 OK** tại `b382fe4`, `dev.localhost` + `test_cskh.localhost` (re-migrate); `p0-isolated-verification.md` §"Security and source reload" — **28 OK** trên CI run `#38712593755` (GHA clean site sạch). **T1C từ chối gộp** thành một tổng duy nhất; xem [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §2. | 18 permission-boundary methods có code đọc được; mapping per-cell tại `permission-matrix.md` §2–§9 (bản 1.0). Không có stdcapture per-method trong repo. |
| T1C evidence | [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) (mới 19/10/2026) | Đối chiếu độc lập: phân tích 18 test methods; per-cell test mapping; per-route status PASS/PARTIAL/NOT RUN/CHƯA XÁC MINH; 5 blocker mới |
| CI log | [GitHub Actions PR #1 run `#38712593755`](https://github.com/nobita6986/new-crm/actions/runs/38712593755) — clean bootstrap, lint/format, 28 tests, migrate, backup/restore đều pass; [push run `#39185122032`](https://github.com/nobita6986/new-crm/actions/runs/39185122032) cũng pass nhưng chỉ xác nhận workflow | Xác nhận G0-04/G0-10 trong phạm vi workflow; không thay thế security transport/full matrix |
| Screenshot/demo | Trang `/cskh` tiếng Việt, cùng phiên đăng nhập và POST CSRF hợp lệ đã quan sát trong browser; chưa lưu ảnh vào repo | Còn thiếu file screenshot bền vững và checklist đủ màn |

**Kết luận bằng chứng:** runtime tests đã giảm thiểu các route được gọi; SEC-13/14/27 và các API/ô chưa chạy vẫn mở. Chỉ kết luận theo test cụ thể, không suy rộng thành bảo đảm an toàn.

---

## 6. Tổng hợp Critical/High & khuyến nghị

| Ưu tiên | ID | Lý do |
|---|---|---|
| 1 | RISK-SEC-02 | Reassignment không thu hồi quyền → rò rỉ trực tiếp; ảnh hưởng toàn bộ vòng đời giao khách (ADR-004 đã chốt hướng; **SEC-23 vẫn partial**) |
| 2 | RISK-SEC-01 / RISK-SEC-13 | Child doctype không record-level → đọc chéo Call Log/Note/Task (PII); đã có hook + override của `crm_cskh`, **partial** — chưa phủ ~38 ô matrix |
| 3 | RISK-SEC-05 | `ignore_permissions` client-controlled → nguy cơ bypass assignment; Admin proxy fail-open cố ý chưa có negative |
| 4 | RISK-SEC-06 | Sales Manager ngoài cây → over-broad (manager-without-hierarchy đã verify, manager-có-hierarchy cross-scope chưa) |
| 5 | RISK-SEC-09 | BOD read-only (ADR-005 đã chốt hướng) — nguy cơ lạm quyền System Manager; runtime fail-closed đã pass, **reporting scope chưa build** |
| 6 | RISK-SEC-04 | `get_linked_docs` lộ SĐT (đã guard, **field exposure CHƯA XÁC MINH**) |
| 7 | RISK-SEC-14 | Staff ghi chéo/tự gán (đã guard fail-closed) |
| **MỚI T1C** | 5 blocker mới (B-T1C-1…5) | Mỗi cái cần shell có Docker để chạy + thêm test transport/security ở site sạch (xem `t1c-security-verification.md` §6) |

---

## 7. Changelog

| Ngày | Người | Nội dung |
| 15/10/2026 | T0 | Bản 0.10: ghi nhận GitHub Actions PR #1 pass clean bootstrap, lint, 28 tests, migration và backup/restore; SEC-13/14/27, full matrix và RISK-SEC-12 vẫn mở. |
|---|---|---|
| 15/10/2026 | T0 | Bản 0.8: đồng bộ WP-02/03; B-1 đổi từ thiếu app sang thiếu security suite/negative tests/remediation. Giữ mọi rủi ro `MỞ — CHƯA XÁC MINH`. |
| 15/10/2026 | T0 | Bản 0.7: ghi nhận WP-01 runtime trên host hiện tại; B-2 gỡ, B-4 seed chạy, B-1 còn thiếu `crm_cskh`/test harness. Giữ `0/28` security cases và mọi rủi ro bảo mật `CHƯA XÁC MINH`; ghi caveat MariaDB 11.8. |
| 15/10/2026 | T0 | Bản 0.6: làm rõ B-2 — WSL2 là prerequisite còn thiếu cho backend đã chọn; chưa có Docker log xác nhận quan hệ trực tiếp với HTTP 500. Giữ trạng thái runtime BLOCKED và mọi rủi ro `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.1: 14 rủi ro (12 mới từ đọc code `v1.87.0` + 2 ánh xạ R-03/R-05 của P0). Tất cả `MỞ — CHƯA XÁC MINH`. 5 blocker. |
| 15/10/2026 | T1C | Bản 0.2: đối chiếu checkout (version-lock/reuse-audit/data-model-spike/ui-extension-spike, deploy/dev). Sửa B-1/B-2/B-3/B-4; ghi nhận B-2 chuyển từ "thiếu version-lock" sang "runtime bị chặn". Giữ toàn bộ `CHƯA XÁC MINH`; vẫn không rủi ro nào đóng. |
| 15/10/2026 | T1C | Bản 0.3: đồng bộ theo ADR-004/005 (T0 chốt) — thêm tham chiếu ADR-004 (RISK-SEC-02) và ADR-005 (RISK-SEC-09), B-3/B-5 chuyển sang "đã chốt theo T0" (giữ B-1/B-2/B-4 mở), ghi rõ **file ADR chưa có trong checkout**. Giữ `0/28` ca chạy, không PASS/FAIL, không đóng rủi ro. |
| 15/10/2026 | T1C | Bản 0.4: ADR-004/005 đã có file — thêm link hai ADR, bỏ ghi chú "file ADR chưa có trong checkout"; B-3 đánh dấu **hoàn tất ở mức ghi tài liệu** (kèm link). Giữ B-1/B-2/B-4 mở, B-5 role PoC, `0/28` SEC, mọi trạng thái `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.5: đồng bộ bằng chứng WP-00/01 (T1A) — B-2 làm rõ Docker Desktop **đã cài và đang chạy** nhưng Linux engine trả **HTTP 500**, WSL2 chưa cài; cập nhật cách gỡ (Owner cài WSL2 + thành phần Windows, bật WSL2 backend trên Docker Desktop hiện có, reboot; Linux DEV VM dự phòng), cập nhật dòng evidence log (§5), thêm link `environment-probe.md`/`dev-setup.md`. Giữ B-1/B-2/B-4 mở, B-3 hoàn tất ở mức ghi ADR, B-5 role PoC, `0/28` SEC, `CHƯA XÁC MINH`. |
| 19/10/2026 | T1C | **Bản 1.0 — độc lập kiểm chứng (risk-register).** Sửa câu "28/28 test app đạt" thành "**ba lượt chạy có tổng test khác nhau** (26 / 27 / 28 OK)" với phân biệt revision + lệnh + site. Cập nhật §2 per-risk row evidence column với test mapping (`Test #n @ file:line`). Sửa §4: B-1 mở rộng thêm SEC-16/26, nhánh nội bộ SEC-27, RISK-SEC-12, ~38 ô matrix; **5 blocker mới B-T1C-1…5** cho env (no Docker/WSL2), SEC-13/14/16/26 transport, SEC-27 nội bộ, 3 tổng khác nhau, manifest test mapping. §5 evidence log bổ sung `t1c-security-verification.md` và phân biệt 3 log. §6 mở thêm dòng khuyến nghị cho B-T1C. Không tuyên bố an toàn; **G0-07 vẫn PARTIAL**. |
