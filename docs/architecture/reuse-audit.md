# REUSE AUDIT — FRAPPE CRM `v1.87.0` (P0)

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B (khảo sát reuse/UI & tính khả thi nghiệp vụ P0)
> **Trạng thái:** Kết quả khảo sát **tĩnh** tại tag `v1.87.0`; WP-02 đã scaffold/cài app POC `crm_cskh` trên bench hiện có và chạy 9 test; WP-03 đã chạy runtime spike. Chưa có business schema, UI PoC hoặc security gate WP-05.
> **Tài liệu liên quan:** [`master-plan.md`](../root-plan/master-plan.md), [`P0.md`](../root-plan/P0.md), [`data-model-spike.md`](./data-model-spike.md), [`ui-extension-spike.md`](./ui-extension-spike.md), [`glossary-vi.md`](./glossary-vi.md)

---

## 0. PROVENANCE VÀ PHƯƠNG PHÁP KIỂM CHỨNG

| Hạng mục | Giá trị đã kiểm chứng |
|---|---|
| Repository | `https://github.com/frappe/crm` |
| Tag baseline | `v1.87.0` (lightweight tag) |
| **Commit SHA** | **`0d3910b456ef4fb913e77b29db011b3debc4f778`** |
| Ngày khảo sát | 2026-10-15 |
| Ngày phát hành tag (theo master plan §3.2) | 2026-10-09 |

**Lệnh kiểm chứng đã chạy:**

```text
git ls-remote https://github.com/frappe/crm.git refs/tags/v1.87.0
  -> 0d3910b456ef4fb913e77b29db011b3debc4f778  refs/tags/v1.87.0
git clone --depth 1 --branch v1.87.0 https://github.com/frappe/crm.git
  -> HEAD detached at 0d3910b456ef4fb913e77b29db011b3debc4f778
git -C <clone> rev-parse HEAD  -> 0d3910b456ef4fb913e77b29db011b3debc4f778
git -C <clone> describe --tags --always  -> v1.87.0
```

**Phương pháp:** đọc mã nguồn tĩnh tại đúng revision trên và trích dẫn `file:line`. Mọi số dòng dưới đây ứng với commit `0d3910b`.

### 0.1. GIỚI HẠN — PHẦN CHƯA THỂ XÁC MINH

Khảo sát này là đọc source tĩnh. Sau đó WP-01 dựng baseline ([runtime evidence](../../deploy/dev/evidence/wp01-runtime.md)); WP-02 tạo/cài scaffold app POC và chạy 9 test chức năng ([evidence](../../deploy/dev/evidence/wp-02-p0b.md)); WP-03 chạy spike D1–D4. Chưa có security suite WP-05 hoặc CI, và app POC chưa phải business model CSKH. Vì vậy:

- **Chưa xác minh hành vi nghiệp vụ/quyền**: chưa kiểm thử phân quyền bằng nhiều tài khoản, chưa import CSV/XLSX thật, chưa render UI tiếng Việt.
- Các kết luận về **permission** dưới đây là suy ra từ mã `permission_query_conditions` / `has_permission` / `check_permission`; **phải được kiểm chứng bằng test thực tế** ở WP-05 (P0 security gate).
- Baseline **CRM `v1.87.0` + Frappe Framework `v15.123.1`** đã cài/migrate trên Python 3.14.8. Frappe cảnh báo MariaDB 11.8 chưa được Framework kiểm thử; do đó **ADR-001 chưa chốt cuối cùng** cho tới khi xử lý caveat tương thích. v16 giữ làm phương án khảo sát sau.
- Chưa xác minh được các lớp ngoài repo CRM: bundle `frappe-ui` (component `DataImport`, `Sidebar`), hành vi engine Frappe core (`frappe.desk.form.assign_to`, `DocShare`, `NestedSet`).
- Chưa xác minh nhánh `develop` khác gì; mọi kết luận **chỉ** áp dụng cho tag `v1.87.0`.

> Kết luận nào phụ thuộc runtime đều được đánh dấu `[CẦN TEST DEV]`.

### 0.2. Quy ước phân loại

| Nhãn | Nghĩa |
|---|---|
| **REUSE** | Dùng nguyên trạng upstream, không thay đổi. |
| **CONFIGURE** | Dùng upstream nhưng thay cấu hình/metadata/permission/property setter — **không sửa code**. |
| **EXTEND** | Viết thêm service/adapter/DocType/UI trong app `crm_cskh` dựa trên upstream, **không sửa core**. |
| **BUILD NEW** | Viết mới hoàn toàn trong `crm_cskh` vì upstream không có hoặc không phù hợp. |

### 0.3. QUYẾT ĐỊNH ĐÃ CHỐT (T0) — THIẾT KẾ, CHƯA XÁC MINH RUNTIME

> Phân định rõ: các mục dưới đây là **quyết định thiết kế** do T0 chốt. Chúng **không** phải bằng chứng runtime. WP-01 chứng minh baseline; WP-02 chỉ chứng minh scaffold/POC và test chức năng; WP-03 chứng minh các probe D1–D4. UI extension và ma trận phân quyền đầy đủ vẫn chưa được kiểm chứng.

| ADR / chủ đề | Quyết định (T0) | Trạng thái |
|---|---|---|
| **ADR-001 — Framework runtime** | Metadata pin cho P0: **CRM `v1.87.0`**, **Frappe Framework `v15.123.1`**, **frappe_docker `v4.0.1`** (tham chiếu [`version-lock.md`](./version-lock.md) §2). Bootstrap/install/migrate đã chạy; Frappe cảnh báo MariaDB 11.8 chưa được kiểm thử. **ADR-001 chưa chốt cuối cùng** cho tới khi xử lý caveat tương thích. Giữ **v16** làm **phương án khảo sát sau**. | Baseline smoke đã chạy; compatibility còn mở |
| **ADR-003 — UI extension** | Cho phép **PoC UI-A + UI-B trên cùng site/session**; **không fork UI** ở bước này. **Kết luận cuối phụ thuộc kết quả PoC** (xem [`ui-extension-spike.md`](./ui-extension-spike.md)). | Chưa PoC `[CẦN TEST DEV]` |
| **ADR-004 — Owner/team permission** | Quyền owner/team kiểm **server-side theo record**; **ownership service** trong `crm_cskh` là **đường duy nhất** đổi owner/assignee; **`CSKH Assignment History` là nguồn truy vết**; `lead_owner`/ToDo/DocShare chỉ là **biểu diễn tương thích** đồng bộ qua service (xem [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)). | Quyết định kiến trúc `[CẦN TEST DEV]` |
| **ADR-005 — Role PoC BOD/Admin** | **P0 dùng `CRM_ADMIN` và `BOD_VIEWER` làm role PoC.** `BOD_VIEWER` chỉ **đọc** báo cáo toàn công ty + drill-down tối thiểu, **không** ghi/import/reassign/quản trị, **không** dùng `System Manager`. `CRM_ADMIN` chỉ quản trị **nghiệp vụ CSKH**, **không** quản trị cấu hình hệ thống Frappe (xem [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md)). | Quyết định kiến trúc `[CẦN TEST DEV]` |
| Canonical phone / dedup | **Chốt ở P1**; **P0 không tạo unique constraint**; **không tự merge** trường hợp mơ hồ (xem [`data-model-spike.md`](./data-model-spike.md) §6.2). | Thiết kế |
| KPI attribution | **Chốt ở P3**; P1/P2 **phải** lưu `CSKH Assignment History` đủ khoảng sở hữu để P3 tính về sau. | Thiết kế |
| **License AGPL-3.0** | Repo ghi `app_license = "AGPLv3"` (`crm/hooks.py:6`). **Yêu cầu rà soát tuân thủ trước phát hành.** Tài liệu này **không** đưa kết luận pháp lý thay cho rà soát đó. | Cần rà soát |

---

## 1. BẢNG PHÂN LOẠI TỔNG HỢP

| # | Nhu cầu P0/P1/P2 | Upstream (đúng tag) | Kết luận |
|---|---|---|---|
| 1 | Hồ sơ khách/lead | `crm_lead.{json,py}` | **REUSE** + custom fields tối thiểu |
| 2 | Tạo Lead chỉ có SĐT | `crm_lead.py` `set_lead_name` | **REUSE** (placeholder hợp lệ, không tắt validation) |
| 3 | Danh sách/chi tiết Lead | `frontend/src/pages/{Leads,Lead}.vue` | **REUSE** |
| 4 | Nhật ký cuộc gọi | `crm_call_log.{json,py}`, `CallLogs.vue` | **REUSE** |
| 5 | Kết quả CSKH có cấu trúc | *không có* | **BUILD NEW** (`CSKH Outcome Code` / `Contact Attempt`) |
| 6 | Công việc hẹn lại | `crm_task.{json,py}` | **REUSE** (+ custom field link nhẹ) |
| 7 | Lịch sử trạng thái | `crm_status_change_log` | **REUSE** (không lấy làm nguồn sự thật chính) |
| 8 | SLA/giờ làm việc/ngày nghỉ | `crm_service_level_agreement`, `crm_service_day`, `crm_holiday_list` | **REUSE utility** — semantics khác → **EXTEND** cho SLA CSKH |
| 9 | Phân phối mặc định | `crm/api/assignment_rule.py` + `Assignment Rule` của Framework | **REUSE** cho use case mặc định |
| 10 | Phân phối theo trọng số | *không có* | **BUILD NEW** (`CSKH Weighted Assignment`) |
| 11 | Đồng bộ ToDo ↔ `lead_owner` ↔ DocShare | `crm/api/todo.py`, `crm_lead.py::assign_agent/share_with_agent` | **EXTEND** bằng ownership service (adapter) |
| 12 | Quyền theo cây quản lý | `crm/permissions/org_hierarchy.py`, `crm_sales_hierarchy` | **REUSE + HARDEN** |
| 13 | Import CSV/XLSX | `frontend/src/pages/DataImport.vue` → `frappe-ui/frappe` `DataImport` | **REUSE** (native-first) → **EXTEND** normalization/dedupe |
| 14 | Dashboard | `crm/api/dashboard.py`, `crm_dashboard` | **REUSE UI primitives**; dashboard CSKH = **EXTEND/BUILD NEW** (không có hook ở tag này) |
| 15 | Timeline hoạt động | `crm/api/activities.py`, `Activities.vue` | **REUSE**; chèn Contact Attempt = **EXTEND** (tab riêng) |
| 16 | i18n tiếng Việt | `crm/locale/vi.po`, `frontend/src/translation.js`, `crm/api/__init__.py` | **REUSE** + vá bản dịch |

---

## 2. CRM LEAD — TRƯỜNG BẮT BUỘC VÀ VALIDATION

### 2.1. Bằng chứng

| Sự kiện | Evidence |
|---|---|
| Cho phép import | `crm/fcrm/doctype/crm_lead/crm_lead.json:3` — `"allow_import": 1` |
| `first_name` là trường **bắt buộc** | `crm_lead.json:92` (`"fieldname": "first_name"`) + `crm_lead.json:95` (`"reqd": 1`) |
| `status` là trường **bắt buộc** | `crm_lead.json:114` + `crm_lead.json:120` (`"reqd": 1`) |
| `mobile_no` kiểu `Data`/`Phone`, **không** bắt buộc, **không unique** | `crm_lead.json:137` |
| `lead_owner` là `Link → User`, không bắt buộc | `crm_lead.json:168` |
| Chuỗi validate | `crm_lead.py:90` `def validate(self)` gọi `validate_status / set_full_name / set_lead_name / set_title / validate_email / validate_lost_reason`, rồi `add_status_change_log` khi status đổi (`crm_lead.py:101`) |
| Mặc định status theo `CRM Lead Status` | `crm_lead.py:112` `def validate_status` (mặc định `New` nếu tồn tại, ngược lại bản ghi `type = Open` đầu tiên) |

### 2.2. Tạo Lead chỉ có số điện thoại (không bypass validation)

`set_lead_name` (`crm_lead.py:132`–`142`) là điểm mấu chốt:

```python
132  def set_lead_name(self):
133      if not self.lead_name:
134          # Check for leads being created through data import
135          if not self.organization and not self.email and not self.flags.ignore_mandatory:
136              frappe.throw(_("A Lead requires either a person's name or an organization's name"))
137          elif self.organization:
138              self.lead_name = self.organization
139          elif self.email:
140              self.lead_name = self.email.split("@")[0]
141          else:
142              self.lead_name = "Unnamed Lead"
```

**Kết luận — CONFIGURE:**

- Không được tắt `reqd` của `first_name`; không truyền `ignore_mandatory` (đây là **cờ nguy hiểm** đúng như master plan §3.2.1 cảnh báo).
- Chiến lược hợp lệ: đặt `first_name` = placeholder có kiểm soát (ví dụ `Chưa rõ tên`) + **custom field** `cskh_is_placeholder_name` (Check) để đánh dấu. Đây là **CONFIGURE** (custom field + client gọi `doc.insert()` chuẩn), **không** phải bypass.
- Ghi nhận: nếu chỉ có SĐT và không có `first_name`, upstream **throw** ngay (`crm_lead.py:136`) — nên placeholder là bắt buộc, không phải tùy chọn.
- `after_insert` (`crm_lead.py:103`) và nhánh `validate` khi `lead_owner` đổi (`crm_lead.py:99`–`101`) tự gọi `share_with_agent` + `assign_agent` → tạo `ToDo`/`DocShare` kèm theo; cần tính đến khi tạo Lead hàng loạt.
- `[CẦN TEST DEV]` Xác nhận `first_name` placeholder + `mobile_no` tạo được Lead hợp lệ và không bị chặn bởi `validate_email`/`validate_lost_reason`.

### 2.3. Trùng lặp định danh

- Upstream **không** có unique cho `mobile_no` (`crm_lead.json:137`) — dedup phải làm ở `crm_cskh` (**BUILD NEW**), không dựa vào core.
- Upstream có sẵn cơ chế chống trùng **Contact** (không phải Lead) trong `crm_lead.py:157` `contact_exists` — chỉ dùng khi convert; **không** dùng được cho dedup kho lead.
- `create_lead_from_call_log` (`crm_call_log.py:294`) tạo Lead với `first_name` fallback `"Lead from call {0}"` (`crm_call_log.py:335`) — tham chiếu pattern placeholder, nhưng đây là đường của call log, không phải import.

---

## 3. CALL LOG THỦ CÔNG

### 3.1. Bằng chứng

| Sự kiện | Evidence |
|---|---|
| Trạng thái kỹ thuật cuộc gọi (enum cố định) | `crm_call_log.json:50` (`status`) + `crm_call_log.json:54` — `Initiated / Ringing / In Progress / Completed / Failed / Busy / No Answer / Queued / Canceled` |
| `status`, `from`, `to`, `type` bắt buộc | `crm_call_log.json:47`, `:55`, `:78`, `:87` (`"reqd": 1`) |
| `type` Incoming/Outgoing | `crm_call_log.json:72` |
| Kênh gọi: Manual/Twilio/Exotel | `crm_call_log.json:149` (`telephony_medium`) |
| Mặc định "Manual" khi tạo tay | `crm_call_log.py:50` `before_insert` → `crm_call_log.py:54` `self.telephony_medium = "Manual"` |
| **Không có trường "kết quả nghiệp vụ"** | Toàn bộ `fields` của `crm_call_log.json` chỉ có trạng thái kỹ thuật + `note` (Link `FCRM Note`) |
| API đọc chi tiết có kiểm quyền | `crm_call_log.py:230` `get_call_log` → `crm_call_log.py:231` `has_permission("CRM Call Log","read")`; con `FCRM Note`/`CRM Task` lọc qua `get_permitted_docs` (`crm_call_log.py:285`–`292`) |
| Liên kết Lead/Deal qua Dynamic Link | `crm_call_log.json` `reference_doctype`/`reference_docname` + bảng `links` |

### 3.2. Kết luận — REUSE (không đánh đồng với kết quả CSKH)

- `CRM Call Log` dùng cho **sự kiện cuộc gọi** và là nguồn chi tiết kỹ thuật.
- Bảng enum trạng thái **chỉ mô tả cuộc gọi**; `Completed` **không** đồng nghĩa "khách quan tâm/kết nối" — đúng như master plan §3.2.3. Đây là bằng chứng trực tiếp: không tồn tại field outcome/disposition trong schema.
- `No Answer`/`Busy` = *attempted*, không phải *connected*.
- Kênh Zalo/SMS/Facebook **không** được tạo Call Log giả (schema buộc `from`/`to`/`type` — `crm_call_log.json:47/78/87`).
- → Cần một DocType **`CSKH Contact Attempt`** riêng (**BUILD NEW**, P2) với `channel` (`phone_call|sms|zalo|facebook|other`) và `outcome_code`; khi `channel=phone_call` thì link 1-1 tới Call Log để chống đếm trùng.
- `[CẦN TEST DEV]` Kiểm chứng quyền cấp bản ghi: schema `crm_call_log.json` cấp `read/write/export` cho `Sales User`/`Sales Manager` ở mức **DocType**, nên **không tự bảo đảm** lọc theo Lead; phải test truy cập chéo (SEC-03).

---

## 4. CRM TASK (HẸN CHĂM SÓC LẠI)

| Sự kiện | Evidence |
|---|---|
| Trạng thái | `crm_task.json:56` + `:61` — `Backlog / Todo / In Progress / Done / Canceled` |
| `assigned_to` (Link User), `due_date` (Datetime) | `crm_task.json:49`, `crm_task.json:64` |
| Liên kết tài liệu | `crm_task.json:79` `reference_doctype` + `:85` `reference_docname` (Dynamic Link) |
| Gán/hủy qua `frappe.desk.form.assign_to` | `crm_task.py:4`–`5`; `after_insert` (`:30`), `validate` (`:33`), `unassign_from_previous_user` (`:41`), `assign_to` (`:45`) |

**Kết luận — REUSE (+ EXTEND nhẹ):**

- Dùng `CRM Task` làm callback/reminder; **không** tạo bảng "Followup Task" mới (đúng P2 §7.5).
- Cần: custom field liên kết `CSKH Work Item` + marker `task_type=cskh_followup` để phân biệt với Task sales tự do (EXTEND, custom field, không sửa core).
- **Task Done ≠ đã liên hệ**: schema không có outcome; không suy ra kết nối từ việc đóng Task.

---

## 5. STATUS HISTORY

| Sự kiện | Evidence |
|---|---|
| Là **child table** (`istable`) | `crm_status_change_log.json:81` (`"istable": 1`) |
| Trường `from` / `to` kiểu `Data` (không Link), `from_date`/`to_date`/`duration`/`log_owner` | `crm_status_change_log.json:22,44,28,50,34,62,65` |
| Sinh log khi status đổi | `crm_status_change_log.py:45` `add_status_change_log`; gọi từ `crm_lead.py:101` |
| **Truy type hard-code `CRM Deal Status`** | `crm_status_change_log.py:46` — `frappe.db.get_value("CRM Deal Status", doc.status, "type")` |

**Kết luận — REUSE (bổ trợ, KHÔNG phải nguồn sự thật chính):**

- Không dùng `status_change_log` để suy ra lịch sử giao nhận/liên hệ; lịch sử giao nhận phải là `CSKH Assignment History` append-only (P1) và lần liên hệ là `CSKH Contact Attempt` (P2).
- **Quan sát rủi ro:** `crm_status_change_log.py:46` tra `"CRM Deal Status"` kể cả khi log gắn vào `CRM Lead` (Lead dùng `CRM Lead Status`). Cần `[CẦN TEST DEV]` để xác định đây là lỗi thật hay chỉ trùng tên bản ghi giữa hai bảng status; nếu sai, đây là hạn chế của upstream (ghi nhận, không sửa core vội).

---

## 6. SLA — KHẢ NĂNG TÁI SỬ DỤNG

| Sự kiện | Evidence |
|---|---|
| SLA gắn vào `apply_on` (Lead/Deal), có `condition`, `priorities`, `working_hours`, `holiday_list` | `crm_service_level_agreement.py:35`–`46` (types); `.json` |
| `apply()` theo `communication_status` | `crm_service_level_agreement.py:74` `apply()` → `handle_communication_status` (`:84`), `handle_targets` (`:134`), `handle_sla_status` (`:173`) |
| Hàm tính giờ làm việc | `calc_time` (`:224`), `calc_elapsed_time` (`:263`) — lặp **từng giây** (`:280`–`291`) |
| Ngày làm việc | `crm_service_day` (child table) + `CRM Holiday List` (`get_holidays` `:352`) |
| Chọn SLA | `crm_service_level_agreement/utils.py:9` `get_sla` (lọc `apply_on`, `enabled`, khoảng ngày, priority, `safe_eval(condition)`) |
| Lead áp SLA | `crm_lead.py:391` `set_sla`, `crm_lead.py:405` `apply_sla` |

**Kết luận — REUSE logic lịch làm việc / EXTEND cho CSKH:**

- Upstream SLA đo **thời gian phản hồi giao tiếp** (`communication_status`, `first_responded_on`, `response_by`) — **không** đo `assigned_at → first_attempt_at`.
- Được tái sử dụng: tư duy **working hours + holiday** (`calc_time`, `CRM Service Day`, `CRM Holiday List`) và adapter có test.
- **Cảnh báo hiệu năng:** `calc_elapsed_time` (`crm_service_level_agreement.py:280`–`291`) duyệt theo giây → phải tránh dùng trực tiếp cho khối lượng lớn; P3 nên viết pure function `business_minutes_between`/`add_business_minutes` có test (EXTEND), không sửa core.
- `condition` dùng `frappe.safe_eval` (`utils.py:44`) — kiểm tra an toàn khi dùng trong custom app.
- `[CẦN TEST DEV]` Xác nhận lịch làm việc/ngày nghỉ tính đúng với timezone `Asia/Ho_Chi_Minh`.

---

## 7. ASSIGNMENT / TODO / DOCSHARE / HIERARCHY / PERMISSION

### 7.1. Bằng chứng

| Sự kiện | Evidence |
|---|---|
| ToDo `after_insert` **ghi đè `lead_owner`/`deal_owner`** bằng `frappe.db.set_value` | `crm/api/todo.py:20`–`26` (nhánh `:21`–`26`) |
| ToDo `on_update` khi status `Cancelled` → **xóa owner** | `crm/api/todo.py:32`–`41`; `clear_owner_on_unassign` `:44`–`51` (`set_value(..., None)`) |
| ToDo `validate` đòi quyền `write` khi gán trực tiếp | `crm/api/todo.py:7`–`17` |
| Lead tự gán agent (ToDo) với `ignore_permissions=True` | `crm_lead.py:167` `assign_agent` → `crm_lead.py:178` |
| Lead tự chia sẻ DocShare với agent | `crm_lead.py:180` `share_with_agent` (thêm DocShare cho agent mới, **xóa DocShare của user khác**) |
| DocType hooks ToDo đăng ký | `crm/hooks.py:244`–`248` (`validate`/`after_insert`/`on_update`) |
| Quyền Lead: query điều kiện | `crm/permissions/org_hierarchy.py:17` `_permission_query_conditions`; `:63`/`:68` export cho Lead/Deal; đăng ký `crm/hooks.py:135`–`145` |
| **Sales Manager ngoài cây = thấy tất cả** | `crm/permissions/org_hierarchy.py:30`–`32` (comment + `return ""`) và `:87`–`89` (`has_permission` trả `True`) |
| `has_permission` cho `create` luôn `True` | `crm/permissions/org_hierarchy.py:84`–`85` |
| Cây quản lý | `crm_sales_hierarchy.json` (`is_tree`, `user` unique) + `crm_sales_hierarchy.py:8` `NestedSet`, index `lft/rgt` + `user` (`:55`–`56`) |
| Assignment Rule mặc định | `crm/api/assignment_rule.py:5` `get_assignment_rules_list`, `:27` `duplicate_assignment_rule` |
| Mutation assignment nhận **`ignore_permissions` từ client** | `crm/api/doc.py:589`–`604` `remove_assignments(doctype, name, assignees, ignore_permissions=False)` |
| API gán/đọc assignee | `crm/api/doc.py:614` `get_assigned_users` (dùng `frappe.get_all` trên ToDo, không kiểm quyền parent) |
| API list tổng quát | `crm/api/doc.py:256` `get_data` → dùng `frappe.get_list` (áp `permission_query_conditions`) `:361`–`370` |
| Xóa hàng loạt | `crm/api/doc.py:802` `delete_bulk_docs` |

### 7.2. Kết luận

- **EXTEND (adapter), không cập nhật rời rạc:** `lead_owner` là trường **single-valued** và bị `ToDo` ghi đè/xóa tự động. Vì vậy đổi chủ **phải** đi qua một `ownership service` trong `crm_cskh` theo thứ tự đã kiểm chứng ToDo → owner → DocShare, trong cùng transaction (đúng P1 §7.6).
- **REUSE + HARDEN `org_hierarchy`:** logic subtree dùng `lft/rgt` join; nhưng hai điểm rủi ro cần khóa ở P0:
  1. `Sales Manager` ngoài cây (`org_hierarchy.py:31`) mặc định **nhìn toàn bộ** — đúng như master plan §3.2.5 và R-03.
  2. `create` luôn được phép (`org_hierarchy.py:84`) — kiểm soát tạo Lead phải ở tầng service/role, không dựa vào `has_permission`.
- **RỦI RO CAO — `remove_assignments` nhận `ignore_permissions` từ HTTP** (`crm/api/doc.py:590`, `:603`). `[CẦN TEST DEV]` SEC-09: user thường không được phép đổi owner/assignee qua tham số này. Nếu khai thác được → phải chặn ở custom app/permission hook (hoặc upstream patch tối thiểu có ADR).
- **Phạm vi quyền hẹp:** `permission_query_conditions` chỉ áp cho `CRM Lead`/`CRM Deal`/`CRM Notification` (`crm/hooks.py:135`–`139`) — **không** áp cho `CRM Call Log`, `CRM Task`, `FCRM Note`, `File`. Các đối tượng này cấp quyền ở mức DocType cho `Sales User`/`Sales Manager` → **không tự bảo đảm** lọc theo Lead. Đây là lý do cốt lõi phải bổ sung guard ở service `crm_cskh` và test chéo (SEC-03/04/05).
- **Assignment Rule mặc định:** REUSE cho use case Round Robin/Load Balancing; engine **trọng số** là BUILD NEW (P1).
- **ADR-004 (T0):** quyền owner/team kiểm **server-side theo record**; **ownership service** trong `crm_cskh` là **đường duy nhất** đổi owner/assignee; `lead_owner`/ToDo/DocShare là **biểu diễn tương thích** — xem [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md). *(Quyết định kiến trúc, chưa kiểm chứng runtime.)*

---

## 8. DATA IMPORT

### 8.1. Bằng chứng

| Sự kiện | Evidence |
|---|---|
| Route SPA | `frontend/src/router.js:106`–`121` (`/data-import`, `/data-import/doctype/:doctype`, `/data-import/:importName`) |
| Trang import tái sử dụng component `frappe-ui` | `frontend/src/pages/DataImport.vue:11` `import { DataImport } from 'frappe-ui/frappe'`; `:18`–`47` `doctypeMap` cho `CRM Lead`, `CRM Deal`, `Contact`, `CRM Task`, `CRM Organization`, `CRM Call Log` |
| Lead cho phép import | `crm_lead.json:3` `allow_import: 1` |

### 8.2. Kết luận — REUSE (native-first) → EXTEND

- Import dùng **component `DataImport` của `frappe-ui`** (bọc luồng Frappe Data Import), **không** phải engine riêng của CRM → ưu tiên tái sử dụng, không viết parser mới (đúng P1 ADR-P1-03).
- Phần **phải EXTEND**: preview chuẩn hóa số, phân loại trùng, gắn `CSKH Import Batch`, idempotency, xử lý lỗi/lỗi tải lại — là wrapper/service trong `crm_cskh`.
- **Chưa xác minh:** nội bộ component `frappe-ui/frappe` `DataImport` (bundle ngoài repo CRM) — cần `[CẦN TEST DEV]` để biết nó cho phép hook precheck/dedupe hay không, hoặc có phải viết wizard riêng.
- **Cạm bẫy Excel:** số dạng scientific notation/mất số 0 đầu — phải chuẩn hóa ở custom app (P1 §5.4), không phụ thuộc core.

---

## 9. DASHBOARD

| Sự kiện | Evidence |
|---|---|
| Dashboard gốc gọi **resolver động** `get_<name>` | `crm/api/dashboard.py:53`–`61` (lặp `layout`, `method_name = f"get_{l['name']}"`), và `get_chart` `:64`–`88` |
| Layout lưu trong DocType `CRM Dashboard` | `crm/fcrm/doctype/crm_dashboard/crm_dashboard.py:12` `default_manager_dashboard_layout`, `:19` `create_default_manager_dashboard` (name `Manager Dashboard`) |
| Dashboard thiên về **sales** | `crm/api/dashboard.py:91`+ (`get_total_leads`, `get_ongoing_deals`, `get_won_deals`, `get_funnel_conversion`, ...) |
| `reset_to_default` chỉ System Manager | `crm/api/dashboard.py:19`–`22` |
| Toàn bộ `get_*` bị chặn bởi role sales | `crm/api/dashboard.py:25`–`27` `@sales_user_only` |
| **KHÔNG có hook `crm_dashboard_charts`** | `grep -r "crm_dashboard_charts\|dashboard_chart\|render_dashboard"` trên repo tag → **0 match** |

**Kết luận:**

- **REUSE UI primitives** (component chart, layout JSON) nhưng **KHÔNG** có điểm mở rộng hook ở `v1.87.0`. Cơ chế `crm_dashboard_charts` (nếu có trên `develop`) **không tồn tại** tại commit này — xác nhận bằng grep.
- Dashboard CSKH Quality là **BUILD NEW/EXTEND**: page riêng cùng site + endpoint `crm_cskh.api.reporting.*` kiểm quyền server-side (chi tiết trong [`ui-extension-spike.md`](./ui-extension-spike.md)).
- Không ghi đè `Manager Dashboard` gốc (name cố định `Manager Dashboard`, `create_default_manager_dashboard`).

---

## 10. TIMELINE / HOẠT ĐỘNG

| Sự kiện | Evidence |
|---|---|
| API timeline cho Lead/Deal | `crm/api/activities.py:14` `get_activities` → `get_deal_activities` (`:23`) / `get_lead_activities` (`:180`) |
| Kiểm quyền đọc | `crm/api/activities.py:24`, `:181` (`has_permission(...,"read")`) |
| Tập `activity_type` cố định | `crm/api/activities.py` — `creation/changed/added/removed/comment/communication/attachment_log` + `calls/notes/tasks` (tách riêng) |
| Frontend dispatch theo `activity_type` + icon map tĩnh | `frontend/src/components/Activities/Activities.vue:96`–`234` (`v-else-if` theo `activity_type`), `:671` `timelineIcon(...)` |
| Có `CallArea/NoteArea/TaskArea/...` | `frontend/src/components/Activities/` (18 file) |

**Kết luận — REUSE, chèn CSKH bằng tab riêng (EXTEND):**

- Timeline là danh sách `activity_type` **hard-code**; muốn chèn "CSKH Contact Attempt" phải sửa cả `get_lead_activities` (backend) **và** `Activities.vue` (frontend) → tức **fork core**. Tránh.
- Khuyến nghị: **tab "Hoạt động CSKH" riêng** cùng Frappe site, hoặc section riêng; dùng lại `frappe-ui` controls. (Xem `ui-extension-spike.md`.)

---

## 11. i18n TIẾNG VIỆT (cơ chế)

| Sự kiện | Evidence |
|---|---|
| Backend trả bản dịch theo ngôn ngữ user | `crm/api/__init__.py:12`–`19` `get_translations()` → `frappe.translate.get_all_translations(language)` (guest dùng `System Settings.language`) |
| Boot nhúng bản dịch vào SPA | `crm/www/crm.py:84` `"translated_messages": get_messages_for_boot()`; frontend nhận `window.translated_messages` (`frontend/src/App.vue:45`) |
| Hàm dịch `__()` + hỗ trợ context | `frontend/src/translation.js:14`–`35` (`translate(message, replace, context)`, key `message:context`) |
| File từ điển | `crm/locale/vi.po` — **1782** `msgid`, **236** `msgstr ""` (chưa dịch), **0** fuzzy |

**Kết luận — REUSE cơ chế, vá bản dịch trong app:** chi tiết và danh sách lỗi ưu tiên ở [`glossary-vi.md`](./glossary-vi.md). Không xây hệ i18n mới; bổ sung bản dịch qua `crm_cskh`.

---

## 12. RỦI RO UPSTREAM ĐÃ XÁC NHẬN BẰNG MÃ (cần test ở P0)

| ID | Rủi ro | Evidence | Mức |
|---|---|---|---|
| R-01 | `Sales Manager` ngoài cây thấy toàn bộ Lead/Deal | `org_hierarchy.py:30`–`32`, `:87`–`89` | High `[CẦN TEST DEV]` |
| R-02 | `remove_assignments` nhận `ignore_permissions` từ client | `crm/api/doc.py:590`, `:603` | High `[CẦN TEST DEV]` |
| R-03 | `has_permission` cho `create` luôn `True` | `org_hierarchy.py:84`–`85` | Medium |
| R-04 | `ToDo` ghi đè/xóa `lead_owner` bằng `db.set_value` | `crm/api/todo.py:24`–`26`, `:51` | High |
| R-05 | `DocShare` cũ của user khác bị xóa khi chia sẻ agent mới | `crm_lead.py:180`–`...` `share_with_agent` | High |
| R-06 | Permission-query **không** phủ Call Log/Task/Note/File | `crm/hooks.py:135`–`139`; schema quyền DocType của `crm_call_log.json`, `crm_task.json`, `fcrm_note.json` | High |
| R-07 | `get_assigned_users` dùng `frappe.get_all`, không kiểm parent | `crm/api/doc.py:614`–`632` | Medium |
| R-08 | `CRM Call Log.status` dễ bị hiểu là outcome nghiệp vụ | `crm_call_log.json:54` | Medium (thiết kế) |
| R-09 | `calc_elapsed_time` duyệt theo giây | `crm_service_level_agreement.py:280`–`291` | Medium (hiệu năng) |
| R-10 | `status_change_log` tra `CRM Deal Status` cho cả Lead | `crm_status_change_log.py:46` | Low–Medium `[CẦN TEST DEV]` |
| R-11 | Không có hook dashboard ở tag này | grep `crm_dashboard_charts` → 0 match | Thông tin (đã chốt trong master plan) |

---

## 13. TỔNG HỢP: NHỮNG GÌ CHƯA THỂ XÁC MINH (THIẾU DEV)

1. **WP-01..03 đã có runtime evidence:** baseline CRM/Frappe, app POC + 9 test chức năng, và spike D1–D4 đã chạy. Chưa có business schema, security suite WP-05 hoặc CI; clean-bench bootstrap chưa xác minh.
2. **Ma trận quyền thực tế** (SEC-01..16 của P0 §7.5) chưa chạy — toàn bộ R-01..R-07 mới chỉ là suy luận từ mã.
3. **Import thật** CSV/XLSX (chỉ SĐT, số 0 đầu, trùng) chưa chạy → chưa biết component `DataImport` của `frappe-ui` cho phép precheck đến đâu.
4. **i18n render** thực tế chưa chụp; danh sách lỗi dựa trên `vi.po` tĩnh.
5. **Framework/dependency compatibility:** tổ hợp pin ở ADR-001 đã chạy trên host hiện tại; MariaDB 11.8 cảnh báo chưa được Framework kiểm thử, nên compatibility cuối cùng còn mở (xem [`version-lock.md`](./version-lock.md) §8).
6. **License/AGPL-3.0**: theo §0.3 — đã ghi nhận **AGPL-3.0** (`crm/hooks.py:6`) và **yêu cầu rà soát tuân thủ trước phát hành**; tài liệu **không** đưa kết luận pháp lý.
7. **UI extension** chưa PoC (chưa deep-link/refresh/session/CSRF); xem `ui-extension-spike.md`.

---

## 14. KẾT LUẬN & ĐỀ XUẤT CHO T0

- Có thể **tái sử dụng đáng kể** upstream: Lead, Call Log, Task, Note, status log, Sales Hierarchy, SLA calendar utilities, Data Import component, i18n pipeline, UI primitives.
- Các phần **buộc BUILD NEW** trong `crm_cskh`: weighted assignment, import batch/dedup, `CSKH Work Item`/`Assignment History`, `Contact Attempt`/`Outcome Code`, SLA obligation CSKH, dashboard chất lượng, ownership service.
- Hai **gate bắt buộc trước P1**: (a) chứng minh R-01/R-02/R-06 không khai thác được (hoặc đã guard); (b) chốt phương án UI extension trên **tag ổn định** (không dùng hook `develop`).
- Các kết luận về quyền, import, UI extension và i18n vẫn cần kiểm chứng; WP-01 hiện chỉ cung cấp baseline DEV để bắt đầu các kiểm chứng đó.
