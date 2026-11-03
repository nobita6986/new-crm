# Khảo sát UI, Luồng Màn hình & Yêu cầu API P2 (T1A — WP07–WP09)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/p2-ui-flow-and-api-requirements.md`
> **Phụ trách:** T1A (UI, Việt hóa, Browser E2E, đóng gói WP11)
> **Baseline nghiệm thu P1:** `1913d2bd5ebfdc894eaa492b46be502dbdf44808` (`main`)
> **Nhánh triển khai UI P2:** `codex/p2-ui` (`D:/HRP-app/p2-ui`)
> **Tài liệu gốc:** [`../root-plan/P2.md`](../root-plan/P2.md) (§0, §4–§15), [`adr-ui-001.md`](./adr-ui-001.md), [`adr-p1-06-ui-integration.md`](./adr-p1-06-ui-integration.md), [`ui-extension-spike.md`](./ui-extension-spike.md), [`glossary-vi.md`](./glossary-vi.md)

---

## 1. Kết quả khảo sát hiện trạng trên baseline `1913d2bd`

### 1.1. Kiến trúc Website `/cskh/*` & Lý do giữ nguyên chiến lược UI-A + UI-B (`ADR-P2-01`)

1. **Upstream Frappe CRM `v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`):**
   - `frontend/src/router.js` và `frontend/src/utils/navigation.js` đóng cứng danh sách route/menu của SPA `/crm/*`.
   - `frontend/src/components/Activities/Activities.vue` (`:96`–`234`) rẽ nhánh cứng theo `activity_type` (`communication`, `comment`, `call`, `note`, `task`, `attachment`, ...) và đọc dữ liệu qua `crm.api.activities.get_activities` (đang được bọc bảo mật bởi `crm_cskh.api.guarded_crm.get_activities`). Không có plugin/slot đăng ký loại sự kiện `CSKH Contact Attempt`, badge `self_reported`, hay chuỗi `CSKH Attempt Correction` nếu không fork/patch trực tiếp `Activities.vue`.
   - Ngược lại, `CRM Form Script` (`dt = "CRM Lead"`, `view = "List"` và `view = "Form"`) là extension point chính thức của Frappe CRM cho phép gắn nút điều hướng sang `/cskh/*` mà **0 dòng sửa upstream**.
2. **Hạ tầng `/cskh/*` đã nghiệm thu ở P0/P1 (`apps/crm_cskh/crm_cskh/`):**
   - `www/cskh_context.py` + `templates/cskh/_shell.html`: dùng chung Frappe site, cookie session `sid` và `X-Frappe-CSRF-Token` (`window.cskhCsrfHeader`), khóa ngôn ngữ hiển thị `vi` trước cả trang lỗi `403`, tự động cache-busting assets qua `_asset_version()`.
   - `public/cskh/cskh.js` + `public/cskh/cskh.css`: bộ helper không phụ thuộc framework ngoài, tách bạch `cskh.get` (GET) và `cskh.post` (POST kèm CSRF), dựng DOM 100% qua `textContent` / `createTextNode` (chống XSS tuyệt đối), che số điện thoại `cskh.maskPhone(...)` (chỉ hiện 4 số cuối trên giao diện), ánh xạ mã lỗi ổn định `code` / `p1_code` sang thông báo tiếng Việt có nhãn `[CODE]`.
   - `www/cskh/my-customers.html` + `public/cskh/my-customers.js`: đang hiển thị danh sách `crm_cskh.api.customers.my_list` (bản thân người đăng nhập) và danh sách theo scope `crm_cskh.api.ownership_ui.assigned_list` (cho Admin/Leader/BOD) cùng panel lịch sử phụ trách & chuyển giao/thu hồi (`reassign`/`revoke` của Admin).
   - `fixtures/crm_form_script.json`: đang có `CSKH - Open CSKH Page` trên `CRM Lead` (`view = "List"`).

**Quyết định kiến trúc UI P2:**
- Tiếp tục chiến lược **UI-A + UI-B trên cùng Frappe site `/cskh/*`**. Không tạo SPA thứ hai, không sửa `Activities.vue` hay bất kỳ file upstream nào ngoài các bản vá i18n/dev đã ghim từ P0.
- Bổ sung 1 `CRM Form Script` chuẩn trên `CRM Lead` (`view = "Form"`, tên `CSKH - Open Lead CSKH Workspace`) để từ màn chi tiết Lead `/crm/leads/<lead_id>` nhảy thẳng sang hồ sơ CSKH `/cskh/work-item?lead=<lead_id>` trong cùng phiên đăng nhập.

---

## 2. Sơ đồ Điều hướng & Luồng Màn hình P2 (`/cskh/*`)

### 2.1. Danh mục Route & Quyền truy cập trang (Page Gate khớp API Gate)

| Route | Key nav | Nhãn tiếng Việt | Vai trò truy cập trang | Chức năng chính |
|---|---|---|---|---|
| `/cskh` | — | CSKH | `Sales User`, `Sales Manager`, `CSKH CRM Admin`, `CSKH BOD Viewer` | Trang chủ điều hướng theo quyền; nút kiểm tra phiên & CSRF |
| `/cskh/pool` | `pool` | Kho khách hàng | `Sales Manager`, `CSKH CRM Admin`, `CSKH BOD Viewer` | (Giữ nguyên P1) Kho khách hàng canonical theo scope |
| `/cskh/import` | `import` | Nhập danh sách | `CSKH CRM Admin` | (Giữ nguyên P1) Upload, ánh xạ, preview, confirm, tải file lỗi |
| `/cskh/campaigns` | `campaigns` | Chiến dịch và quy tắc | `CSKH CRM Admin`, `CSKH BOD Viewer`, `Sales Manager` | (Giữ nguyên P1) Chiến dịch, trọng số, sức chứa, phân phối nền |
| `/cskh/my-customers` | `my-customers` | Khách được giao | `Sales User`, `Sales Manager`, `CSKH CRM Admin`, `CSKH BOD Viewer` | **(Nâng cấp P2 — WP07)** Cột trạng thái CSKH, liên hệ cuối, kết quả, số lần, lịch hẹn tiếp; bộ lọc CSKH; form ghi kết quả nhanh; giữ nguyên panel quyền phụ trách P1 |
| `/cskh/work-item` | *(deep-link)* | Hồ sơ chăm sóc khách hàng | `Sales User`, `Sales Manager`, `CSKH CRM Admin`, `CSKH BOD Viewer` *(kiểm quyền theo từng Work Item/Lead)* | **(Mới P2 — WP07/WP08)** Header Lead/Work Item, ghi kết quả nhanh, hẹn lại, đóng/mở lại công việc, Timeline hợp nhất (Attempt / Call Log / Task / Note), badge `self_reported`, yêu cầu & duyệt hiệu chỉnh/vô hiệu (`Correction`) |
| `/cskh/callbacks` | `callbacks` | Lịch hẹn chăm sóc | `Sales User`, `Sales Manager`, `CSKH CRM Admin`, `CSKH BOD Viewer` | **(Mới P2 — WP08)** Lịch/danh sách callback theo scope (`Đến hạn hôm nay`, `Sắp đến hạn`, `Quá hạn`, `Đã hoàn tất`, `Đã hủy`), thao tác `Ghi nhận & hoàn tất`, `Đổi lịch hẹn`, `Hủy lịch hẹn (có lý do)` |
| `/cskh/progress` | `progress` | Tiến độ chăm sóc | `Sales User`, `Sales Manager`, `CSKH CRM Admin`, `CSKH BOD Viewer` | **(Mới P2 — WP09)** Thống kê tiến độ CSKH cơ bản theo scope (Staff cá nhân, Leader đội, Admin/BOD toàn CRM); tách rõ số khách/Work Item với số Attempt và `self_reported` vs `verified`; không chấm điểm KPI/SLA |
| `/cskh/dashboard` | `dashboard` | Bảng điều khiển phân phối | `CSKH BOD Viewer`, `CSKH CRM Admin`, `Sales Manager` | (Giữ nguyên P1 + liên kết nhanh sang `/cskh/progress`) |
| `/cskh/settings` | `settings` | Cấu hình kết quả & chính sách | `CSKH CRM Admin`, `CSKH BOD Viewer`, `Sales Manager` *(chỉ `CSKH CRM Admin` có quyền ghi)* | **(Mới P2 — WP09)** Xem/cấu hình danh mục `CSKH Outcome Code` (bật/tắt/thêm/cập nhật, không xóa mã đã dùng) và `CSKH Policy` phiên bản hóa |

### 2.2. Các luồng tương tác trọng tâm & Quy tắc trung thực dữ liệu

1. **Luồng ghi nhận kết quả nhanh (`Quick Attempt Form` — trên `/cskh/my-customers`, `/cskh/work-item`, và từ `/cskh/callbacks`):**
   - Chọn `Kênh liên hệ` (`phone_call`, `zalo`, `sms`, `facebook`, `other`) → lọc danh sách `Kết quả liên hệ` hợp lệ theo kênh và chính sách hiệu lực.
   - Hiển thị rõ ngữ nghĩa của mã kết quả ngay trên form: **`Đã kết nối`** hay **`Chưa kết nối`**, có bắt buộc ghi chú không, có bắt buộc đặt lịch hẹn lại không, có đặt cờ **`Không liên hệ nữa (DNC)`** hay đóng công việc không.
   - **Một thao tác Lưu gọi duy nhất một API orchestration (`record_attempt`)** kèm `work_item_id`, `expected_revision`, và `request_id` (UUID).
   - **Chống bấm đúp & Retry an toàn:** khóa nút khi request đang chạy; giữ nguyên `request_id` nếu lỗi mạng/tạm thời để bấm `Thử lại` không nhân đôi Attempt/Task; tự sinh `request_id` mới ngay khi người dùng sửa bất kỳ trường nào trên form.
   - **Xử lý xung đột tường minh:**
     - `OWNERSHIP_CHANGED` / `FORBIDDEN`: báo rõ khách đã chuyển người phụ trách hoặc không còn quyền, ẩn form ghi, xóa dữ liệu nhạy cảm nếu mất quyền đọc.
     - `STALE_WORK_ITEM`: báo rõ hồ sơ vừa được cập nhật ở phiên/tab khác (`expected_revision` lệch), cung cấp nút `Tải lại trạng thái mới nhất` để đồng bộ `revision` mới trước khi gửi lại.
     - `POLICY_CHANGED`: báo rõ cấu hình kết quả/chính sách vừa thay đổi, cung cấp nút `Tải lại biểu mẫu theo chính sách mới`.
     - `DO_NOT_CONTACT` / `WORK_ITEM_NOT_ACTIVE`: báo rõ khách từ chối liên hệ tiếp hoặc công việc đã đóng/thu hồi.
2. **Luồng Timeline hợp nhất & Bất biến lịch sử (`Timeline & Correction`):**
   - Hiển thị phân biệt rõ 4 loại mục bằng nhãn và màu riêng, có bộ lọc loại mục:
     1. **`Lần liên hệ CSKH`** (`CSKH Contact Attempt`) — nguồn duy nhất tính vào `Số lần liên hệ` (khi chưa bị `void`). Luôn gắn nhãn **`Nhân viên tự báo cáo`** (`self_reported`) trừ khi server trả `verification_status = "verified"` (**`Đã xác minh`**).
     2. **`Nhật ký cuộc gọi (CRM)`** (`CRM Call Log`) — hiển thị trạng thái đã gắn hay chưa gắn với Attempt; **không đếm lặp** thành lần liên hệ thứ hai.
     3. **`Lịch hẹn / Nhiệm vụ (CRM)`** (`CRM Task`) — hiển thị hạn xử lý và trạng thái; kể cả khi `Done` vẫn ghi rõ **`Nhiệm vụ — không tự tính là đã kết nối`**.
     4. **`Ghi chú (CRM)`** (`FCRM Note`) — ghi rõ **`Ghi chú nội bộ — không tính là lần liên hệ`**.
   - Kết quả `Đã gửi tin, chưa phản hồi` (`message_sent_no_reply`) và `Không nghe máy` / `Máy bận` / `Không liên lạc được` luôn gắn nhãn **`Chưa kết nối`**, không bao giờ hiển thị như đã kết nối hay chăm sóc thành công.
   - **Tuyệt đối không có nút Sửa hoặc Xóa trực tiếp Attempt.** Chỉ có thao tác `Yêu cầu hiệu chỉnh` (gửi `request_correction`) và `Duyệt / Từ chối hiệu chỉnh` (gửi `review_correction` theo capability từ server). Lịch sử gốc và bản hiệu chỉnh/vô hiệu (`Đã hiệu chỉnh` / `Đã vô hiệu`) cùng hiển thị minh bạch (ai, lúc nào, lý do, giá trị cũ → giá trị hiệu lực).
3. **Luồng Chuẩn hóa Timezone (`Asia/Ho_Chi_Minh`) & Biên ngày `23:59` / `00:00`:**
   - Server truyền `window.cskhTimezone = { site, user, effective, server_now_iso }` trong `_shell.html`.
   - Frontend phân tích mọi chuỗi thời gian Frappe không kèm offset theo đúng `site` timezone (không bao giờ dùng `new Date(naiveStr)` theo giờ máy trạm trình duyệt), định dạng và tính biên ngày `YYYY-MM-DD` (`23:59:59` vs `00:00:00`) theo `effective` timezone và đồng hồ tham chiếu của server, đồng thời gửi `occurred_at` / `next_followup_at` kèm offset ISO-8601 tường minh (`+07:00`).

---

## 3. Yêu cầu API Contract đối với Backend P2 (T1B)

> T1A thiết kế tầng adapter gọi API trong `public/cskh/` bám sát bảng endpoint tại `P2.md` §10 để tích hợp ngay từ SHA commit của T1B mà không sửa permission/policy phía backend.

### 3.1. Mở rộng Read Model danh sách khách được giao (`GET crm_cskh.api.customers.my_list` & `ownership_ui.assigned_list` hoặc endpoint danh sách P2 tương đương)

- **Tham số lọc bổ sung (tất cả tùy chọn, giữ tương thích ngược P1):**
  - `cskh_filter` (hoặc `derived_state` / `status`): hỗ trợ lọc server-side cho:
    - `assigned` (`Mới được giao`)
    - `not_contacted` (`Chưa liên hệ` — `attempt_count == 0` trên công việc đang mở)
    - `attempted` (`Đã thử` — `attempt_count >= 1`)
    - `attempted_not_connected` (`Chưa kết nối` — đã có attempt hiệu lực nhưng chưa có attempt nào `is_connected = true`)
    - `connected` (`Đã kết nối` — có ít nhất 1 attempt hiệu lực `is_connected = true` hoặc kết quả hiệu lực hiện tại `is_connected = true`)
    - `on_hold` (`Chờ chăm sóc lại`)
    - `followup_due` (`Đến hạn` — có callback mở đến hạn trong ngày hiện tại theo timezone chuẩn)
    - `followup_overdue` (`Quá hạn` — có callback mở `due_at < server_now`)
    - `closed` (`Hoàn tất`)
    - `do_not_contact` (`Không liên hệ nữa` — DNC)
  - `search`: tìm theo tên hoặc SĐT trong phạm vi quyền (chặn khi `pii = false`).
  - `sort_by` / `sort_dir`: sắp xếp theo `assigned_at`, `last_attempt_at`, `next_action_due`.
- **Trường trả về trên mỗi dòng `items[]` (bổ sung cạnh các trường P1 hiện có):**
  - `work_item`, `lead`, `display_name` (ẩn nếu `!pii`), `phone` (ẩn nếu `!pii`), `campaign`, `campaign_title`, `campaign_code`, `source`, `assigned_to`, `assigned_at`
  - `work_item_status`: `"queued" | "assigned" | "in_progress" | "on_hold" | "closed" | "revoked"`
  - `derived_state`: mã trạng thái dẫn xuất phục vụ hiển thị/đối soát
  - `do_not_contact`: `bool`
  - `attempt_count`: `int` (chỉ đếm Attempt hiệu lực không bị `void`)
  - `connected_attempt_count` / `has_connected`: `int` / `bool`
  - `last_attempt_at`: ISO-8601 datetime hoặc `null`
  - `last_channel`: `"phone_call" | "zalo" | "sms" | "facebook" | "other" | null`
  - `last_outcome_code`: `str | null`
  - `last_outcome_label`: `str | null` (nhãn snapshot tiếng Việt tại thời điểm ghi/hiệu chỉnh)
  - `last_is_connected`: `bool | null`
  - `last_verification_status`: `"self_reported" | "verified" | null`
  - `next_action_due` (hoặc `next_followup_at`): ISO-8601 datetime của `CRM Task` callback đang mở hoặc `null`
  - `open_callback_task_id`: `str | null`
  - `followup_state`: `"due_today" | "upcoming" | "overdue" | null`
  - `revision`: `int` (phục vụ `expected_revision` khi ghi nhanh ngay từ danh sách)
  - `can_record_attempt`: `bool` (server tính sẵn theo active owner + trạng thái + DNC)

### 3.2. Chi tiết Work Item, Timeline hợp nhất & Danh mục Outcome (`GET`)

1. **`GET crm_cskh.api.workflow.get_work_item`** (`work_item_id` hoặc `lead` trong scope):
   - Trả về thông tin Work Item + Lead (ẩn PII cho BOD), `revision`, `work_item_status`, `derived_state`, `do_not_contact`, `completion_reason`, `closed_at`, `attempt_count`, `connected_count`, `last_attempt` summary, `open_callback` (`task_id`, `due_at`, `status`, `title`, `followup_state`), `policy` hiệu lực (`policy_id`, `policy_version`, `max_backdate_minutes`, `require_note_on_all`, ...), và object `capabilities`:
     - `can_record_attempt`, `can_schedule_followup`, `can_complete_followup`, `can_close_work_item`, `can_reopen_work_item`, `can_request_correction`, `can_review_correction`, `can_copy_phone`.
2. **`GET crm_cskh.api.workflow.list_attempts`** (`work_item_id`, `page`, `page_size`, tùy chọn `include_activities=1` hoặc tách rõ `attempts`, `call_logs`, `tasks`, `notes`):
   - Phân trang chuẩn (`page`, `page_size`, `total`, `items`).
   - Mỗi Attempt gồm: `attempt_id`, `work_item_id`, `performed_by`, `performed_by_name`, `occurred_at`, `recorded_at`, `channel`, `outcome_code`, `outcome_label` (snapshot), `is_connected`, `is_interested`, `verification_status` (`self_reported` / `verified`), `note`, `call_log_id`, `callback_task_id`, `is_void`, `has_correction`, `original_snapshot` (nếu đã hiệu chỉnh), và danh sách `corrections[]` (`correction_id`, `status`: `pending`/`approved`/`rejected`, `correction_type`, `proposed_changes`, `reason`, `requested_by`, `requested_at`, `reviewed_by`, `reviewed_at`, `review_note`).
   - Dữ liệu đối chiếu native cùng Lead/Work Item (nếu caller có quyền đọc):
     - `call_logs[]`: `call_log_id`, `type`/`direction`, `status`, `duration`, `started_at`, `linked_attempt_id`.
     - `tasks[]`: `task_id`, `title`, `due_at`, `status` (`Open`/`Todo`/`In Progress`/`Done`/`Canceled`), `assigned_to`, `linked_attempt_id`, `close_reason`.
     - `notes[]`: `note_id`, `title`, `content` (text an toàn), `created_by`, `created_at`.
3. **`GET crm_cskh.api.workflow.get_outcome_codes`** (tùy chọn `channel`, `campaign`, `work_item_id`, `include_disabled`):
   - Trả về `items[]`: `code`, `label_vi` (hoặc `title`), `allowed_channels[]`, `is_connected`, `is_interested`, `requires_note`, `requires_followup`, `allows_followup`, `is_terminal`, `sets_do_not_contact`, `default_completion_reason`, `enabled`, `sort_order`, kèm `policy_version`.

### 3.3. Các API Ghi Nguyên tử (`POST` + CSRF + `request_id`)

1. **`POST crm_cskh.api.workflow.record_attempt`**:
   - Input: `work_item_id`, `channel`, `outcome_code`, `occurred_at` (ISO-8601 có offset), `note`, `next_followup_at` (tùy chọn), `callback_task_id` (tùy chọn khi thực hiện từ lịch hẹn), `call_log_id` (tùy chọn), `close_work_item` / `completion_reason` (tùy chọn khi kết quả kết thúc), `expected_revision`, `request_id`.
   - Output: `attempt_id`, `work_item_id`, `work_item_status`, `derived_state`, `is_connected`, `verification_status`, `callback_task_id`, `next_action_due`, `revision`.
2. **`POST crm_cskh.api.workflow.schedule_followup`**:
   - Input: `work_item_id`, `due_at` (ISO-8601 có offset), `title` / `note`, `reason` (bắt buộc khi đổi lịch), `expected_revision`, `request_id`.
3. **`POST crm_cskh.api.workflow.complete_followup`**:
   - Input: `work_item_id`, `task_id`, `resolution` (`"completed_by_attempt"` kèm `attempt_id` HOẶC `"canceled"` kèm `reason` bắt buộc), `expected_revision`, `request_id`.
   - Bất biến: đóng/hủy Task không có Attempt **không bao giờ** sinh `is_connected = true` hay tự chuyển thành chăm sóc thành công.
4. **`POST crm_cskh.api.workflow.close_work_item`** & **`POST crm_cskh.api.workflow.reopen_work_item`**:
   - Input: `work_item_id`, `reason` (bắt buộc; `completion_reason` khi đóng), `expected_revision`, `request_id`.
5. **`POST crm_cskh.api.workflow.request_correction`** & **`POST crm_cskh.api.workflow.review_correction`**:
   - `request_correction`: `attempt_id`, `work_item_id`, `correction_type` (`change_outcome`, `change_channel`, `change_occurred_at`, `void_attempt`, `add_clarification`), `new_outcome_code` / `new_channel` / `new_occurred_at` / `clarification_note`, `reason` (bắt buộc), `request_id`.
   - `review_correction`: `correction_id`, `decision` (`"approve"` | `"reject"`), `review_note` (bắt buộc khi từ chối hoặc duyệt void), `request_id`.

### 3.4. Lịch hẹn, Tiến độ & Cấu hình (`GET` / `POST`)

1. **`GET crm_cskh.api.workflow.list_followups`**:
   - Input: `bucket` (`due_today` | `upcoming` | `overdue` | `done` | `canceled` | `all`), `campaign`, `page`, `page_size` (scope tự suy từ actor trên server).
   - Output: `scope`, `total`, `items[]` (`task_id`, `work_item_id`, `lead`, `display_name`, `phone`, `campaign`, `campaign_title`, `assigned_to`, `due_at`, `status`, `followup_state`, `linked_attempt_id`, `close_reason`, `work_item_revision`, `can_complete`, `can_reschedule`).
2. **`GET crm_cskh.api.workflow.progress_summary`**:
   - Input: `campaign`, `date_from`, `date_to` (không nhận `user`/`team` mở rộng scope).
   - Output: `scope`, `as_of`, `cards` (`active_work_items`, `uncontacted_work_items`, `attempted_work_items`, `connected_work_items`, `attempted_not_connected_work_items`, `on_hold_work_items`, `closed_work_items`, `dnc_work_items`, `effective_attempts`, `self_reported_attempts`, `verified_attempts`, `unanswered_message_attempts`, `open_followups`, `due_today_followups`, `overdue_followups`), `by_campaign[]`, `by_user[]` (trong scope), `closed_by_reason[]`.
3. **`GET` / `POST crm_cskh.api.settings.*`**:
   - Đọc cấu hình Outcome & Policy (ví dụ `get_settings` / `list_policies` + `get_outcome_codes(include_disabled=1)`), kèm `can_manage: bool`.
   - `POST crm_cskh.api.settings.save_outcome`: tạo mới hoặc cập nhật/bật/tắt `CSKH Outcome Code` (chỉ `CSKH CRM Admin`; không xóa mã đã phát sinh dữ liệu).
   - `POST crm_cskh.api.settings.save_policy`: lưu phiên bản mới của `CSKH Policy` theo scope `Global` / `Team` / `Campaign` (chỉ `CSKH CRM Admin`).

### 3.5. Mã lỗi ổn định bắt buộc (`code` / `p2_code` / `p1_code` trên HTTP 4xx)

| Mã lỗi | HTTP khuyến nghị | Hiển thị & Hành động trên UI tiếng Việt |
|---|---|---|
| `FORBIDDEN` | 403 | Cảnh báo không có quyền thực hiện hoặc xem hồ sơ; ẩn dữ liệu nhạy cảm |
| `NOT_FOUND` | 404 | Hồ sơ không tồn tại hoặc ngoài phạm vi truy cập |
| `OWNERSHIP_CHANGED` | 409 | Khách hàng đã được chuyển cho nhân viên khác; khóa form ghi, điều hướng về danh sách |
| `WORK_ITEM_NOT_ACTIVE` | 409 | Công việc chưa giao, đã thu hồi hoặc đã hoàn tất; khóa ghi liên hệ mới |
| `STALE_WORK_ITEM` | 409 | Dữ liệu công việc đã thay đổi ở phiên khác (`expected_revision` lệch); bấm tải lại để đồng bộ |
| `POLICY_CHANGED` | 409 | Chính sách hoặc danh mục kết quả vừa thay đổi; tải lại biểu mẫu trước khi lưu |
| `DO_NOT_CONTACT` | 409 / 422 | Khách hàng ở trạng thái Không liên hệ nữa (DNC); chặn liên hệ/đặt hẹn trái chính sách |
| `IDEMPOTENCY_CONFLICT` | 409 | Trùng khóa yêu cầu (`request_id`) nhưng khác nội dung dữ liệu |
| `ALREADY_LINKED` | 409 | Nhật ký cuộc gọi hoặc lịch hẹn đã được gắn với một lần liên hệ khác |
| `INVALID_CHANNEL` | 400 / 422 | Kênh liên hệ không hợp lệ hoặc không được chính sách cho phép |
| `INVALID_OUTCOME` | 400 / 422 | Mã kết quả không tồn tại hoặc đã ngừng kích hoạt |
| `OUTCOME_NOT_COMPATIBLE` | 400 / 422 | Mã kết quả không tương thích với kênh liên hệ hoặc lý do đóng công việc |
| `NOTE_REQUIRED` | 400 / 422 | Kết quả liên hệ này bắt buộc nhập ghi chú |
| `FOLLOWUP_REQUIRED` | 400 / 422 | Kết quả liên hệ này bắt buộc chọn thời điểm hẹn chăm sóc lại |
| `INVALID_FOLLOWUP_TIME` | 400 / 422 | Thời điểm hẹn lại hoặc thời điểm liên hệ không hợp lệ (quá khứ/vượt khung backdate/ngoài chính sách) |
