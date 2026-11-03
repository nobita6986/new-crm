# P2 — Đặc tả Mô hình Dữ liệu, Schema & Ràng buộc Cơ sở Dữ liệu

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** `docs/data-model-p2.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§6, §7, §8), [`docs/architecture/schema-p1.md`](./architecture/schema-p1.md), [`docs/state-machine.md`](./state-machine.md), [`docs/outcome-catalog.md`](./outcome-catalog.md)

---

## 1. Tổng quan Kiến trúc Dữ liệu P2 (Không tạo bảng trùng)

| Thực thể | Phân loại | Nguồn sự thật cho | Lý do thiết kế |
|---|---|---|---|
| `CSKH Outcome Code` | **BUILD NEW** (DocType) | Từ điển kết quả liên hệ có phiên bản (`version`), cờ ngữ nghĩa (`is_connected`, `is_terminal`, `is_do_not_contact`, ...) và ràng buộc kênh/trường bắt buộc. | Upstream `CRM Call Log` / `CRM Lead` không có từ điển kết quả đa kênh có phiên bản hóa. |
| `CSKH Policy` | **BUILD NEW** (DocType) | Quy tắc vận hành P2 theo cấp `Global` / `Team` / `Campaign` (`version`, `allowed_backdate_minutes`, `require_attempt_before_close`, `enabled_channels`, ...). | Cho phép cấu hình linh hoạt không sửa code, đóng băng phiên bản trên từng `Attempt`. |
| `CSKH Contact Attempt` | **BUILD NEW** (DocType) | Bản ghi sự kiện liên hệ bất biến (append-only) gắn với `CSKH Work Item` và `CSKH Assignment History` (ownership interval). | Tách bạch nỗ lực liên hệ đa kênh khỏi `CRM Call Log` (chỉ dành cho thoại) và `FCRM Note` (ghi chú tự do). |
| `CSKH Attempt Correction` | **BUILD NEW** (DocType) | Yêu cầu và quyết định hiệu chỉnh / hủy hiệu lực (`correct` / `void` / `restore`) cho một `CSKH Contact Attempt`. | Bảo toàn tính bất biến của `Attempt` gốc đồng thời cho phép sửa sai có kiểm toán. |
| `CSKH Work Item` | **EXTEND** (P1 DocType) | Trạng thái hành trình chăm sóc (`queued`, `assigned`, `in_progress`, `on_hold`, `closed`, `revoked`), `revision`, và các cột read-model hiệu lực. | Mở rộng trực tiếp thực thể P1; không tạo bảng Work Queue thứ hai. |
| `CSKH Assignment History` | **EXTEND** (P1 DocType) | Khoảng thời gian sở hữu (`ownership_start`..`ownership_end`) với đầy đủ hành động `assign`, `reassign`, `revoke`, `close`, `reopen`. | Tái sử dụng sổ cái sở hữu bất biến của P1 để đối chiếu `Attempt.occurred_at` và quyền lịch sử. |
| `CRM Task` | **EXTEND** (Native Frappe CRM) | Lịch hẹn chăm sóc lại (callback) qua Custom Fields `cskh_*`. | **Không tạo DocType `CSKH Followup Task` mới** (`P2.md` §0.7, §7.1, ADR-P2-04). |
| `CRM Call Log` | **REUSE** (Native Frappe CRM) | Chi tiết cuộc gọi thoại native (liên kết 1–1 tùy chọn từ `CSKH Contact Attempt.call_log`). | Không sao chép bảng cuộc gọi; chỉ liên kết khi có dữ liệu thoại thật hợp lệ (ADR-P2-05). |
| `CRM Lead` | **EXTEND** (Native Frappe CRM) | Hồ sơ khách hàng canonical + cờ tuân thủ toàn cục `cskh_do_not_contact` (`DNC`). | Bảo vệ trạng thái từ chối liên hệ xuyên suốt qua import lại và chiến dịch mới (ADR-P2-07). |

---

## 2. Chi tiết Schema các DocType Mới trong `crm_cskh`

### 2.1. `CSKH Outcome Code` (`tabCSKH Outcome Code`)

- **Naming:** `field:code` (`allow_rename = 0`)
- **Quyền DocType:** `CSKH CRM Admin` đọc/ghi qua API `settings.save_outcome`; `Sales User`, `Sales Manager`, `CSKH BOD Viewer` chỉ đọc (`read = 1`, `create/write/delete = 0`).

| Trường | Kiểu Frappe | Bắt buộc | Mặc định | Ràng buộc / Ghi chú |
|---|---|---|---|---|
| `code` | `Data` | Có | — | `unique=1`, `^[a-z][a-z0-9_]{1,63}$`, bất biến sau khi tạo |
| `label_vi` | `Data` | Có | — | Nhãn tiếng Việt chuẩn hiển thị trên UI |
| `description` | `Small Text` | Không | — | Hướng dẫn sử dụng cho nhân viên |
| `result_group` | `Select` | Có | `unreached` | `unreached \| engaged \| positive \| negative \| converted \| invalid_contact \| compliance \| other` |
| `is_contact_attempt` | `Check` | Không | `1` | Tính vào mẫu số nỗ lực liên hệ |
| `is_connected` | `Check` | Không | `0` | **Bắt buộc `0`** cho các mã không kết nối hai chiều |
| `is_interested` | `Check` | Không | `0` | Khách thể hiện quan tâm |
| `is_terminal` | `Check` | Không | `0` | Cho phép kết thúc hành trình chăm sóc |
| `is_do_not_contact` | `Check` | Không | `0` | Kích hoạt cờ tuân thủ `DNC` trên `CRM Lead` |
| `requires_note` | `Check` | Không | `0` | Bắt buộc nhập `note` khác rỗng |
| `requires_followup` | `Check` | Không | `0` | Bắt buộc đặt lịch hẹn lại (`followup.due_at`) |
| `allows_followup` | `Check` | Không | `1` | Có cho phép gắn lịch hẹn lại hay không |
| `requires_completion_reason` | `Check` | Không | `0` | Bắt buộc nhập `completion_reason` khi đóng Work Item |
| `allowed_channels` | `Data` | Có | `phone_call,sms,zalo,facebook,other` | Danh sách kênh phân tách dấu phẩy |
| `suggested_next_status` | `Select` | Có | `in_progress` | `in_progress \| on_hold \| closed` |
| `version` | `Int` | Có | `1` | Tự động tăng mỗi lần cập nhật cấu hình |
| `enabled` | `Check` | Không | `1` | Tắt mềm thay vì xóa cứng |
| `sort_order` | `Int` | Không | `100` | Thứ tự hiển thị trên UI |

---

### 2.2. `CSKH Policy` (`tabCSKH Policy`)

- **Naming:** `field:policy_code` (`allow_rename = 0`)
- **Độ ưu tiên giải phân giải (Precedence):** `Campaign` đang bật > `Team` đang bật (theo `campaign.team_scope` hoặc team của owner hiện tại) > `Global` đang bật (`DEFAULT-GLOBAL`).

| Trường | Kiểu Frappe | Bắt buộc | Mặc định | Ràng buộc / Ghi chú |
|---|---|---|---|---|
| `policy_code` | `Data` | Có | — | `unique=1` (ví dụ `GLOBAL-DEFAULT`, `TEAM-...`, `CAMP-...`) |
| `title` | `Data` | Có | — | Tên chính sách tiếng Việt |
| `scope_type` | `Select` | Có | `Global` | `Global \| Team \| Campaign` |
| `team` | `Link` (`CRM Sales Hierarchy`) | Theo scope | — | Bắt buộc khi `scope_type == "Team"` |
| `campaign` | `Link` (`CSKH Campaign`) | Theo scope | — | Bắt buộc khi `scope_type == "Campaign"` |
| `enabled` | `Check` | Không | `1` | Chỉ một policy đang bật cho mỗi `(scope_type, team, campaign)` |
| `version` | `Int` | Có | `1` | Tự động tăng khi chỉnh sửa |
| `allowed_backdate_minutes` | `Int` | Có | `120` | Tối đa số phút lùi `occurred_at` so với `now` (`>= 0`) |
| `max_open_followup_tasks_per_item` | `Int` | Có | `1` | Cố định `= 1` trong P2 |
| `require_attempt_before_close` | `Check` | Không | `1` | Cấm đóng Work Item khi chưa có attempt hiệu lực |
| `allow_close_without_contact_for_invalid` | `Check` | Không | `1` | Cho phép đóng không cần attempt khi lý do thuộc invalid/duplicate/DNC có kiểm toán |
| `do_not_contact_behavior` | `Select` | Có | `block_and_auto_close` | `block_and_auto_close \| block_future_outreach` |
| `enabled_channels` | `Data` | Có | `phone_call,sms,zalo,facebook,other` | Kênh cho phép ghi nhận |
| `allow_leader_reopen` | `Check` | Không | `1` | Cho phép `Sales Manager` mở lại Work Item trong phạm vi team |
| `allow_leader_review_correction` | `Check` | Không | `1` | Cho phép `Sales Manager` duyệt yêu cầu hiệu chỉnh Attempt của team |

---

### 2.3. `CSKH Contact Attempt` (`tabCSKH Contact Attempt`)

- **Naming:** `hash` (`allow_rename = 0`)
- **Tính bất biến (Immutability):**
  - `before_save`: chặn mọi lệnh lưu khi `not self.is_new()` (`frappe.ValidationError` / `FORBIDDEN`), **không ngoại trừ `ignore_permissions=True`**. Dữ liệu hiệu lực sau sửa đổi được đọc qua lớp phủ `CSKH Attempt Correction`!
  - `on_trash`: chặn xóa tuyệt đối.
  - `has_permission`: `create/write/delete = False` cho mọi actor qua generic REST/Desk; chỉ được tạo bên trong context có cờ nội bộ của orchestration service (`crm_cskh.services.contact_attempt`).

| Trường | Kiểu Frappe | Bắt buộc | Mặc định | Ràng buộc / Ghi chú |
|---|---|---|---|---|
| `work_item` | `Link` (`CSKH Work Item`) | Có | — | Index truy vấn chính |
| `lead` | `Link` (`CRM Lead`) | Có | — | Phải khớp `work_item.lead` |
| `campaign` | `Link` (`CSKH Campaign`) | Có | — | Phải khớp `work_item.campaign` |
| `ownership_event` | `Link` (`CSKH Assignment History`) | Có | — | Khoảng sở hữu đang mở tại thời điểm ghi |
| `owner_at_attempt` | `Link` (`User`) | Có | — | Người sở hữu Work Item tại thời điểm ghi (`ownership_event.to_user`) |
| `performed_by` | `Link` (`User`) | Có | — | Server gán cứng `= frappe.session.user`; không nhận từ client |
| `occurred_at` | `Datetime` | Có | — | Đã qua kiểm tra không tương lai, `allowed_backdate_minutes`, và `>= ownership_start` |
| `recorded_at` | `Datetime` | Có | — | Thời điểm máy chủ lưu thực tế (`now_datetime()`) |
| `channel` | `Select` | Có | `phone_call` | `phone_call \| sms \| zalo \| facebook \| other` |
| `outcome_code` | `Link` (`CSKH Outcome Code`) | Có | — | Mã kết quả gốc lúc ghi |
| `outcome_version_snapshot` | `Int` | Có | `1` | Phiên bản `CSKH Outcome Code` tại thời điểm ghi |
| `outcome_label_snapshot` | `Data` | Có | — | Nhãn tiếng Việt chụp cứng |
| `outcome_group_snapshot` | `Data` | Có | — | `result_group` chụp cứng |
| `contact_attempt_snapshot` | `Check` | Không | `1` | `is_contact_attempt` chụp cứng |
| `connected_snapshot` | `Check` | Không | `0` | `is_connected` chụp cứng |
| `interested_snapshot` | `Check` | Không | `0` | `is_interested` chụp cứng |
| `terminal_snapshot` | `Check` | Không | `0` | `is_terminal` chụp cứng |
| `dnc_snapshot` | `Check` | Không | `0` | `is_do_not_contact` chụp cứng |
| `policy_name_snapshot` | `Link` (`CSKH Policy`) | Có | — | Chính sách áp dụng lúc ghi |
| `policy_version_snapshot` | `Int` | Có | `1` | Phiên bản chính sách lúc ghi |
| `note` | `Text` | Không | — | Đã làm sạch HTML nguy hiểm / tiền tố công thức |
| `verification_status` | `Select` | Có | `self_reported` | `self_reported \| verified \| disputed` (Staff chỉ được `self_reported`) |
| `call_log` | `Link` (`CRM Call Log`) | Không | — | `unique=1` (nullable unique trên MariaDB: 1 `CRM Call Log` tối đa gắn 1 `Attempt`) |
| `callback_task` | `Link` (`CRM Task`) | Không | — | Task hẹn lại được tạo từ Attempt này (nếu có) |
| `resolved_callback_task` | `Link` (`CRM Task`) | Không | — | Task hẹn lại trước đó được Attempt này giải quyết (nếu có) |
| `idempotency_key` | `Data` | Có | — | Khóa chống ghi trùng từ client |
| `payload_hash` | `Data` | Có | — | SHA-256 của payload chuẩn hóa để phát hiện `IDEMPOTENCY_CONFLICT` |
| `work_item_revision` | `Int` | Có | — | `Work Item.revision` mới sau khi ghi Attempt |

---

### 2.4. `CSKH Attempt Correction` (`tabCSKH Attempt Correction`)

- **Naming:** `hash` (`allow_rename = 0`)
- **Tính kiểm toán:** Không cho xóa (`on_trash` bị chặn). Chỉ cho phép một lần chuyển trạng thái từ `pending -> approved | rejected` thông qua `crm_cskh.services.correction.review_correction`; sau khi đã `approved` hoặc `rejected`, bản ghi trở thành bất biến hoàn toàn!

| Trường | Kiểu Frappe | Bắt buộc | Mặc định | Ràng buộc / Ghi chú |
|---|---|---|---|---|
| `attempt` | `Link` (`CSKH Contact Attempt`) | Có | — | Bản ghi gốc cần hiệu chỉnh / hủy hiệu lực |
| `work_item` | `Link` (`CSKH Work Item`) | Có | — | Phải khớp `attempt.work_item` |
| `lead` | `Link` (`CRM Lead`) | Có | — | Phải khớp `attempt.lead` |
| `campaign` | `Link` (`CSKH Campaign`) | Có | — | Phải khớp `attempt.campaign` |
| `action` | `Select` | Có | `correct` | `correct \| void \| restore` |
| `status` | `Select` | Có | `pending` | `pending \| approved \| rejected` |
| `corrected_fields_json` | `Long Text` | Không | — | JSON allowlist (`outcome_code`, `channel`, `occurred_at`, `note`, `call_log`) + snapshot outcome mới |
| `reason` | `Small Text` | Có | — | Lý do hiệu chỉnh / hủy bắt buộc |
| `requested_by` | `Link` (`User`) | Có | — | Người tạo yêu cầu (`frappe.session.user`) |
| `requested_at` | `Datetime` | Có | — | Thời điểm yêu cầu |
| `reviewed_by` | `Link` (`User`) | Không | — | Leader / CRM Admin phê duyệt hoặc từ chối |
| `reviewed_at` | `Datetime` | Không | — | Thời điểm phê duyệt |
| `review_note` | `Small Text` | Không | — | Ghi chú phê duyệt / lý do từ chối |
| `idempotency_key` | `Data` | Có | — | Khóa idempotent cho tạo yêu cầu |
| `payload_hash` | `Data` | Có | — | SHA-256 của yêu cầu |
| `review_idempotency_key` | `Data` | Không | — | Khóa idempotent cho bước duyệt |
| `applied_revision` | `Int` | Không | — | `Work Item.revision` khi `approved` |

---

## 3. Mở rộng DocType P1 & Custom Fields trên Frappe CRM

### 3.1. Mở rộng `CSKH Work Item`

Giữ nguyên toàn bộ trường P1 (`lead`, `campaign`, `status`, `assigned_to`, `assigned_at`, `distribution_run`, `import_batch`, `claim_key`), nâng cấp `status` và bổ sung các cột read-model hiệu lực:

- `status` (`Select`): `queued | assigned | in_progress | on_hold | closed | revoked`
- `revision` (`Int`, default `1`, `reqd=1`): bộ đếm kiểm soát đồng thời lạc quan (optimistic concurrency)
- `first_attempt_at` (`Datetime`)
- `last_attempt_at` (`Datetime`)
- `last_outcome_code` (`Link -> CSKH Outcome Code`)
- `last_outcome_label` (`Data`)
- `last_connected_at` (`Datetime`)
- `attempt_count` (`Int`, default `0`) — số attempt hiệu lực (`is_contact_attempt=1` và chưa `void`)
- `connected_attempt_count` (`Int`, default `0`) — số attempt hiệu lực có `connected=1`
- `open_callback_task` (`Link -> CRM Task`) — đúng Task callback đang mở (nếu `status == "on_hold"`)
- `next_action_due` (`Datetime`) — hạn xử lý tiếp theo của `open_callback_task`
- `last_activity_by` (`Link -> User`)
- `closed_at` (`Datetime`)
- `closed_by` (`Link -> User`)
- `completion_reason` (`Small Text`)
- `reopened_at` (`Datetime`)
- `reopened_by` (`Link -> User`)
- `reopen_reason` (`Small Text`)
- `policy_version` (`Int`, default `1`)

### 3.2. Mở rộng `CSKH Assignment History`

- Mở rộng `action` (`Select`): `assign | reassign | revoke | close | reopen`.
- Bất biến:
  - `assign`, `reassign`, `reopen`: bắt buộc `to_user != NULL`, `ownership_start != NULL`.
  - `revoke`, `close`: bắt buộc `to_user == NULL`, đóng `ownership_end` trên event mở liền trước (và ghi `ownership_end` trên event kết thúc).

### 3.3. Custom Fields trên `CRM Lead` (idempotent qua Patch + `fixtures/custom_field.json`)

| Fieldname | Fieldtype | Options / Default | Mục đích |
|---|---|---|---|
| `cskh_do_not_contact` | `Check` | Default `0` | Cờ tuân thủ ngừng liên hệ toàn cục; không bị `import` hoặc chuyển chiến dịch xóa mất |
| `cskh_dnc_reason` | `Small Text` | — | Lý do khách yêu cầu ngừng liên hệ / Admin cập nhật |
| `cskh_dnc_at` | `Datetime` | — | Thời điểm đánh dấu DNC gần nhất |
| `cskh_dnc_work_item` | `Link` | `CSKH Work Item` | Work Item nguồn phát sinh cờ DNC |

### 3.4. Custom Fields trên `CRM Task` (idempotent qua Patch + `fixtures/custom_field.json`)

| Fieldname | Fieldtype | Options / Default | Mục đích |
|---|---|---|---|
| `cskh_work_item` | `Link` | `CSKH Work Item` | Gắn Task callback với đúng Work Item |
| `cskh_is_followup` | `Check` | Default `0` | Đánh dấu Task thuộc quy trình hẹn lại CSKH P2 |
| `cskh_source_attempt` | `Link` | `CSKH Contact Attempt` | Attempt tạo ra lịch hẹn (nếu tạo từ `record_attempt`) |
| `cskh_completed_attempt` | `Link` | `CSKH Contact Attempt` | Attempt giải quyết lịch hẹn khi `status == "Done"` |
| `cskh_completion_reason` | `Small Text` | — | Lý do hoàn tất / hủy Task |
| `cskh_reschedule_count` | `Int` | Default `0` | Số lần dời lịch hẹn |
| `cskh_task_history_json` | `Long Text` | `permlevel=1` | Nhật ký tạo / dời lịch / chuyển giao A→B / đóng Task |

---

## 4. Ràng buộc Duy nhất & Chỉ mục Cơ sở Dữ liệu (Version-Controlled MariaDB Indexes)

Được đồng bộ tự động qua `crm_cskh.p2_models.ensure_p2_indexes` (`on_doctype_update`) và patch `crm_cskh.patches.v2_0.p2_schema_and_seeds`:

1. **`tabCSKH Outcome Code`:**
   - `UNIQUE (code)` (`name` = `code`)
   - `INDEX (enabled, sort_order)`
2. **`tabCSKH Policy`:**
   - `UNIQUE (policy_code)` (`name` = `policy_code`)
   - `INDEX (scope_type, enabled, campaign, team)`
3. **`tabCSKH Contact Attempt`:**
   - `UNIQUE (work_item, performed_by, idempotency_key)` — chống double-submit tuyệt đối ở tầng DB
   - `UNIQUE (call_log)` — trên MariaDB InnoDB, cột `NULL` cho phép nhiều dòng không gắn Call Log, nhưng một `call_log` khác `NULL` chỉ được gắn tối đa **01** `CSKH Contact Attempt` (`ALREADY_LINKED`)
   - `INDEX (work_item, occurred_at)` — phục vụ timeline và tính lại read-model
   - `INDEX (lead, occurred_at)` — phục vụ tra cứu theo khách hàng được phép
   - `INDEX (campaign, occurred_at)` — phục vụ `progress_summary`
   - `INDEX (performed_by, occurred_at)` — phục vụ báo cáo hoạt động theo nhân viên
   - `INDEX (outcome_code, occurred_at)` — phục vụ tổng hợp theo kết quả
4. **`tabCSKH Attempt Correction`:**
   - `UNIQUE (requested_by, idempotency_key)` — chống gửi trùng yêu cầu hiệu chỉnh
   - `INDEX (attempt, status, creation)` — ghép nhanh lớp phủ hiệu lực (`effective view`)
   - `INDEX (work_item, status, creation)` — hàng đợi duyệt hiệu chỉnh theo Work Item
5. **`tabCSKH Work Item` (bổ sung cho P2):**
   - Giữ nguyên các index P1 `(campaign, status)`, `(assigned_to, status)`, `(lead, campaign)`
   - Bổ sung `INDEX (assigned_to, status, next_action_due)` — phục vụ hàng đợi `My Customers` & callback quá hạn/hôm nay
   - Bổ sung `INDEX (campaign, status, last_attempt_at)` — phục vụ `progress_summary`
6. **`tabCRM Task` (bổ sung cho P2):**
   - `INDEX cskh_task_wi_open_idx (cskh_work_item, cskh_is_followup, status)` — tra cứu tức thời callback đang mở của Work Item
   - `INDEX cskh_task_assignee_due_idx (assigned_to, cskh_is_followup, status, due_date)` — danh sách lịch hẹn của nhân viên
   - `INDEX cskh_task_followup_due_idx (cskh_is_followup, status, due_date)` — danh sách lịch hẹn quá hạn / hôm nay / sắp tới cấp Team & Hệ thống
