# Risk Register — Security (P0)

> **Owner:** T1C — Thiết kế & kiểm chứng bảo mật P0
> **Phiên bản:** 0.10 (runtime evidence) — 15/10/2026
> **Trạng thái:** 28/28 test app đạt trên test site sạch; 18 permission-boundary methods. Rủi ro chỉ được giảm thiểu trong các route đã test; RISK-SEC-12 còn mở.
> **Đối chiếu checkout (0.10):** GitHub Actions PR #1 đã pass bootstrap sạch, lint/format, 28 tests, migration và backup/restore ([run](https://github.com/nobita6986/new-crm/actions/runs/38712593755)). Security transport-level SEC-13/14/27 và full matrix vẫn chưa xong ([`permission-matrix.md`](./permission-matrix.md)). B-2/B-4 đã gỡ; B-3 ghi ADR; B-5 theo role PoC.
> **ADR-004/005:** ADR-004 — thực thi quyền owner/team ở **backend/record-level** + ownership service tập trung ([`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)); ADR-005 — `BOD_VIEWER` **read-only**, **không** System Manager ([`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md)). Runtime/security **CHƯA XÁC MINH**.
> **Tài liệu cha:** [`../root-plan/master-plan.md`](../root-plan/master-plan.md) §16 · [`../root-plan/P0.md`](../root-plan/P0.md) §13
> **Tài liệu liên quan:** [`permission-matrix.md`](./permission-matrix.md)

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
| RISK-SEC-01 | Call Log/Note/Task/File có nguy cơ đọc chéo theo `name` | Cross-access | **Critical** / High | Giả thuyết code `v1.87.0`; record hooks + negative tests hiện chặn route đã thử | ĐANG GIẢM THIỂU — test API/record scope | SEC-03, SEC-17, SEC-18, CL-02/03/05, NT-02/03, TK-02 | Giữ backend hook; mở rộng qua mọi route/file endpoint trước khi nới quyền |
| RISK-SEC-02 | Reassignment desync `lead_owner`/`ToDo`/`DocShare` có thể giữ quyền cũ | Reassignment | **Critical** | Tests chuyển owner và xác minh người cũ mất Lead/child/file/ToDo; assignment mutation Staff bị chặn | ĐANG GIẢM THIỂU — service chưa triển khai | SEC-10, SEC-22, SEC-23, RA-09 | Fail-closed với role thấp; ownership service ADR-004 vẫn cần thiết trước khi bật reassignment |
| RISK-SEC-03 | `get_assigned_users` có thể lộ assignee | Info disclosure | Medium | Test cho Lead owner và chặn Lead ngoài scope | ĐANG GIẢM THIỂU — route đã test | SEC-19 | Giữ check quyền gốc và lọc assignee theo scope |
| RISK-SEC-04 | `get_linked_docs_of_document` có thể lộ `from`/`to` | Info disclosure | **High** | Test route từ chối Lead ngoài scope | ĐANG GIẢM THIỂU — route đã test | SEC-20 | Giữ check quyền doc gốc và từng linked doc |
| RISK-SEC-05 | `remove_assignments` nhận `ignore_permissions` từ client | Assignment bypass | **High** | Test gọi `ignore_permissions=true` từ Staff và bị từ chối | ĐANG GIẢM THIỂU — mutation Staff fail-closed | SEC-09, SEC-21, RA-03 | Chỉ System Manager được gọi; không tin cờ permission client |
| RISK-SEC-06 | Sales Manager ngoài cây hierarchy có thể thấy mọi Lead | Over-broad | **High** | Test manager không có node trong cây nhận danh sách rỗng và bị từ chối detail | ĐANG GIẢM THIỂU — cấu hình/tình huống đã test | SEC-07 | Giữ fail-closed khi user không có hierarchy node |
| RISK-SEC-07 | `get_data` có thể trả field vượt permlevel | Field exposure | Medium→High | Test field synthetic `permlevel=1` không xuất hiện cho Staff | ĐANG GIẢM THIỂU — field test hẹp | SEC-25, LEAD-16, LS-06 | Rà soát thêm mọi field list/report trước khi cấp rộng quyền |
| RISK-SEC-08 | `duplicate_assignment_rule` có thể sửa cấu hình | Config tamper | Medium | Test Staff bị từ chối và rule duplicate không được tạo | ĐANG GIẢM THIỂU — route đã test | SEC-24 | Giữ quyền create theo role hệ thống |
| RISK-SEC-09 | BOD có thể cần `System Manager` nếu thiếu role read-only | Least privilege | **High** | Tests xác minh BOD role không có System Manager và không thể tạo User/Role/Data Import; report API chưa xây | ĐANG GIẢM THIỂU — fail-closed, reporting chưa có | SEC-15, SEC-28, BD-01/05 | Giữ BOD không có System Manager; xây reporting scope riêng ở P1/P3 |
| RISK-SEC-10 | `file_url` private có thể lộ qua activities/attachments | File exposure | Medium | Test file direct và get_attachments theo quyền Lead | ĐANG GIẢM THIỂU — private files đã test | SEC-26, AT-04 | Kiểm tra lại khi thêm lưu trữ/CDN hoặc API tải file mới |
| RISK-SEC-11 | `get_call_contact` có thể tra contact theo số không tham chiếu | Info disclosure | Medium→High | Phone lookup APIs bị Staff chặn; fallback nội bộ cho call không ref Lead chưa test trực tiếp | ĐANG GIẢM THIỂU — một phần; SEC-27 partial | SEC-27 | Giữ lookup fail-closed và thêm test direct cho fallback trước P1 |
| RISK-SEC-12 | `remove_assignments`/`set_status` huỷ ToDo → **clear owner** của Lead đa-assignee | Data integrity | Medium | Giả thuyết (code): comment "accepted wrinkle" trong `todo.clear_owner_on_unassign` | MỞ — CHƯA XÁC MINH | SEC-22, RA-05 | Owner single-valued có chủ đích; ghi chính sách co-assignee |
| RISK-SEC-13 | Child records có thể rộng quyền hơn Lead | Cross-access | **Critical** | Negative tests xác minh Staff không đọc child record ngoài scope | ĐANG GIẢM THIỂU — test routes đã map | SEC-03, SEC-04, SEC-17, SEC-18 | Giữ hooks ở backend và mở rộng test route |
| RISK-SEC-14 | Staff có thể tự gán quyền hoặc ghi chéo | Assignment bypass | **High** | Owner change, ToDo assignment và `ignore_permissions` mutation bị từ chối trong tests | ĐANG GIẢM THIỂU — test routes đã map | SEC-08, SEC-09 | Giữ assignment fail-closed cho tới ownership service |

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

> Cập nhật theo T0: **B-2 đã gỡ**; B-4 seed chạy; B-3 ghi ADR; B-5 role PoC. WP-05 suite chạy **28/28** gồm 18 permission-boundary methods; B-1 còn mở cho SEC-13/14/27 và full matrix. GitHub Actions PR #1 đã pass. Không tuyên bố toàn hệ thống an toàn.

| # | Blocker | Ảnh hưởng | Cần để gỡ |
|---|---|---|---|
| B-1 | WP-05 suite/remediation đã chạy cục bộ và trên CI; **SEC-13/14/27 còn partial, full matrix chưa điền** | Không ký G0-07 tới khi transport/full-path gap được xử lý | Hoàn thiện test mapping; xem [`permission-matrix.md`](./permission-matrix.md) |
| B-2 | **ĐÃ GỠ trên host hiện tại:** WSL2 backend hoạt động; Docker Engine 29.7.2 chạy Linux; các service DEV lên khỏe ([`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md)) | Setup trên máy thứ hai/clean machine chưa chứng minh | Giữ cấu hình; Linux DEV VM là dự phòng nếu engine hiện tại hỏng |
| B-3 | **HOÀN TẤT ở mức ghi tài liệu** — ADR-004/005 đã được T0 chốt và có file trong checkout: [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md), [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md) | Quyết định + tài liệu đã có; **không** phải bằng chứng runtime/security | — (đã xong) |
| B-4 | **ĐÃ CHẠY:** seed tạo hai nhóm sales, user giả, 15 Lead, 3 Call Log, 2 Task, attachment; seed lần hai idempotent. T0 bổ sung timezone + bật hierarchy; probe mẫu owner/team có evidence ở [`p0-d-runtime.md`](./p0-d-runtime.md) §5.1. | Fixture/config DEV đã có; không phải kết quả permission/security test | Dùng test suite WP-05 để kiểm chứng đầy đủ |
| B-5 | ~~Thiếu role chuẩn `CRM-ADMIN`/`BOD`~~ → **chốt là role PoC** (`CRM_ADMIN`, `BOD_VIEWER` read-only) theo P0 §7.1 | Kỳ vọng ô `C` đã có cơ sở (PoC); runtime chưa xác minh | Quyết định T0 (đã chốt) |

---

## 5. Bằng chứng hiện có (evidence log)

| Loại | Nội dung | Giá trị |
|---|---|---|
| Đọc source (T1C) | 8 file upstream trên tag `v1.87.0` (hooks, org_hierarchy, todo, crm_lead, doc, activities, crm_call_log, assignment_rule) | Cơ sở cho giả thuyết & test plan |
| Đọc source (đối chiếu) | [`reuse-audit.md`](./reuse-audit.md), [`data-model-spike.md`](./data-model-spike.md) — file:line tại commit `0d3910b` | Củng cố giả thuyết RISK-SEC-01/02/03/05/06 |
| Khóa version | [`version-lock.md`](./version-lock.md) (metadata/ref + image digest); `deploy/dev/evidence/environment-probe.md` | Provenance **metadata** — **không** phải bằng chứng runtime |
| Quyết định ADR | [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) (enforcement backend/record-level), [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md) (BOD read-only) | Quyết định có thẩm quyền; là **tài liệu thiết kế** — **không** phải bằng chứng runtime/security |
| WP-01 runtime | [`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md) + raw logs trong `deploy/dev/evidence/` | Docker/WSL2, app baseline install/migrate, seed idempotence, authenticated UI/API smoke đã chạy trên host hiện tại; MariaDB 11.8 caveat còn mở |
| WP-02/03 runtime | [`wp-02-p0b.md`](../../deploy/dev/evidence/wp-02-p0b.md) + [`p0-d-runtime.md`](./p0-d-runtime.md) §5.1 | App POC/test 9 ca; timezone và owner/team probe mẫu; không phải WP-05 |
| Security tests | 28/28 app tests; 18 permission-boundary methods. Một số SEC case partial; không đồng nhất số test app với 28 SEC scenarios. | Test report [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md); map ở `permission-matrix.md` |
| CI log | [GitHub Actions PR #1](https://github.com/nobita6986/new-crm/actions/runs/38712593755) — bootstrap sạch, 28 tests, migrate, backup/restore đều pass | Xác nhận G0-04/G0-10 trong phạm vi workflow; không thay thế security transport/full matrix |
| Screenshot/demo | Trang `/cskh` tiếng Việt, cùng phiên đăng nhập và POST CSRF hợp lệ đã quan sát trong browser; chưa lưu ảnh vào repo | Còn thiếu file screenshot bền vững và checklist đủ màn |

**Kết luận bằng chứng:** runtime tests đã giảm thiểu các route được gọi; SEC-13/14/27 và các API/ô chưa chạy vẫn mở. Chỉ kết luận theo test cụ thể, không suy rộng thành bảo đảm an toàn.

---

## 6. Tổng hợp Critical/High & khuyến nghị

| Ưu tiên | ID | Lý do |
|---|---|---|
| 1 | RISK-SEC-02 | Reassignment không thu hồi quyền → rò rỉ trực tiếp; ảnh hưởng toàn bộ vòng đời giao khách (ADR-004 đã chốt hướng; chưa xác minh) |
| 2 | RISK-SEC-01 / RISK-SEC-13 | Child doctype không record-level → đọc chéo Call Log/Note/Task (PII) |
| 3 | RISK-SEC-05 | `ignore_permissions` client-controlled → nguy cơ bypass assignment |
| 4 | RISK-SEC-06 | Sales Manager ngoài cây → over-broad |
| 5 | RISK-SEC-09 | BOD read-only (ADR-005 đã chốt hướng) — nguy cơ lạm quyền System Manager; runtime chưa xác minh |
| 6 | RISK-SEC-04 | `get_linked_docs` lộ SĐT |
| 7 | RISK-SEC-14 | Staff ghi chéo/tự gán |

**Khuyến nghị cho T0:** không mở quyền Lead/child doctype cho user thường **trước khi** SEC-01…SEC-28 chạy thật; ưu tiên WP-05 remediation như một gate của P0.

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
