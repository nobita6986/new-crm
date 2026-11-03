# P2 — Hợp đồng API Backend (`crm_cskh.api.workflow.*` & `crm_cskh.api.settings.*`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** `docs/api-contract-p2.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§10, §11), [`docs/architecture/api-contract-p1.md`](./architecture/api-contract-p1.md), [`docs/state-machine.md`](./state-machine.md), [`docs/outcome-catalog.md`](./outcome-catalog.md), [`docs/data-model-p2.md`](./data-model-p2.md)
> **Mục tiêu:** Hợp đồng cố định (frozen contract) bàn giao sớm cho T1A (UI P2) và T1C (QA/Security).

---

## 1. Quy ước Chung về Giao vận, Xác thực & Phân trang

1. **Đường dẫn RPC chuẩn Frappe:** `/api/method/<dotted_path>`
   - Mọi endpoint `POST` chấp nhận cả `application/json` lẫn `application/x-www-form-urlencoded` và yêu cầu phiên đăng nhập + `X-Frappe-CSRF-Token` hợp lệ.
   - Boolean đầu vào đi qua `crm_cskh.api._params.parse_bool` (`true`/`"true"`/`1`/`"1"` → `True`, `false`/`"false"`/`0`/`"0"` → `False`; giá trị rác → `400 INVALID_REQUEST`).
   - Số nguyên phân trang `page` (mặc định `1`, `>= 1`) và `page_size` (mặc định `20`, `1..100`) đi qua `crm_cskh.api._params.parse_pagination`; giá trị không hợp lệ (`"abc"`, `0`, `-1`, `101`, `True`) trả `400 INVALID_REQUEST` ổn định.
2. **Scope hoàn toàn quyết định ở Server (`SEC-07`, `SEC2-16`):**
   - Không có bất kỳ endpoint đọc nào nhận tham số `scope`, `team` hay `user` để mở rộng phạm vi dữ liệu.
   - Bộ lọc thu hẹp (`assignee` trong `list_work_items` / `list_followups`) chỉ được giao cắt (`INTERSECT`) với tập user nằm trong scope hợp pháp mà server đã tính.
3. **Envelope thành công:** Frappe bọc kết quả trả về trong `{"message": <payload>}`.
4. **Envelope lỗi chuẩn hóa (`crm_cskh.p1_errors.normalize_p1_error_response`):**
   - Mọi lỗi nghiệp vụ P1/P2 trả đúng HTTP status (`400`, `403`, `404`, `409`, `413`, `422`) kèm khóa ổn định ở cấp cao nhất của JSON body:
     ```json
     {
       "code": "FOLLOWUP_REQUIRED",
       "p1_code": "FOLLOWUP_REQUIRED",
       "exc_type": "FOLLOWUP_REQUIRED",
       "message": "Kết quả này yêu cầu đặt lịch hẹn liên hệ lại.",
       "_server_messages": "..."
     }
     ```
   - Không bao giờ phản chiếu SĐT, email hoặc nội dung ghi chú nhạy cảm trong chuỗi lỗi khi từ chối truy cập; giữ nguyên 100% bảo vệ File-oracle `404` của P0 (`crm_cskh.errors.normalize_error_response`).

---

## 2. Danh mục Mã lỗi Ổn định P2 (`P2.md` §10.2)

| Mã lỗi (`code` / `p1_code`) | HTTP Status | Tình huống kích hoạt |
|---|---:|---|
| `FORBIDDEN` | `403` | Không đủ vai trò, ngoài phạm vi sở hữu/team, cố giả mạo performer/verified, hoặc BOD gọi endpoint chi tiết PII / mutation. |
| `NOT_FOUND` | `404` | Không tìm thấy tài nguyên hợp lệ đối với actor có quyền quản trị/tra cứu toàn cục. *(Với Staff/Leader ngoài quyền: trả `403 FORBIDDEN` để không rò rỉ oracle tồn tại).* |
| `INVALID_REQUEST` | `400` | Thiếu tham số bắt buộc, sai kiểu dữ liệu (`page`, `page_size`, boolean, JSON), hoặc tham số ngoài miền cho phép. |
| `OWNERSHIP_CHANGED` | `409` | Work Item đã bị chuyển người phụ trách (`reassign`) hoặc thu hồi (`revoke`) trong lúc form đang mở; actor không còn nằm trong khoảng sở hữu đang mở. |
| `WORK_ITEM_NOT_ACTIVE` | `409` | Work Item đang ở `queued`, `closed`, hoặc `revoked` (hoặc cố `reopen` một Work Item đang active). |
| `STALE_WORK_ITEM` | `409` | `expected_revision` gửi lên khác `CSKH Work Item.revision` hiện tại trong DB. |
| `POLICY_CHANGED` | `409` | `expected_policy_version` (khi gửi kèm) lệch với `version` hiện tại của `CSKH Policy` hiệu lực. |
| `IDEMPOTENCY_CONFLICT` | `409` | Trùng `idempotency_key` nhưng `payload_hash` khác với yêu cầu gốc đã lưu. |
| `ALREADY_LINKED` | `409` | `CRM Call Log` đã được liên kết với một `CSKH Contact Attempt` khác. |
| `ALREADY_ASSIGNED` | `409` | Mở lại (`reopen_work_item`) khi `CRM Lead` đã có `CSKH Active Claim` ở một Work Item khác. |
| `CAPACITY_EXCEEDED` | `409` | Mở lại (`reopen_work_item`) hoặc chuyển giao cho nhân viên đã đầy `capacity_limit`. |
| `DO_NOT_CONTACT` | `409` | Khách hàng đang có cờ `CRM Lead.cskh_do_not_contact = 1` và chính sách chặn liên hệ / hẹn lại / mở lại. |
| `INVALID_CHANNEL` | `400` | `channel` không hợp lệ hoặc không nằm trong `policy.enabled_channels`. |
| `INVALID_OUTCOME` | `422` | `outcome_code` không tồn tại hoặc đang `enabled = 0`. |
| `NOTE_REQUIRED` | `422` | Outcome yêu cầu `requires_note = 1` (hoặc đóng/dời lịch/hiệu chỉnh yêu cầu lý do) nhưng thiếu nội dung hợp lệ. |
| `FOLLOWUP_REQUIRED` | `422` | Outcome yêu cầu `requires_followup = 1` nhưng không truyền `followup.due_at`. |
| `INVALID_FOLLOWUP_TIME` | `422` | `due_at` không đúng định dạng thời gian hoặc `<= now_datetime()` (hoặc `occurred_at` ở tương lai / vượt quá `allowed_backdate_minutes` / trước `ownership_start`). |
| `OUTCOME_NOT_COMPATIBLE` | `422` | Tổ hợp `channel` + `outcome_code` không nằm trong `allowed_channels`, truyền `followup` cho outcome có `allows_followup = 0`, truyền `call_log` cho kênh khác `phone_call`, hoặc đóng Work Item trái quy tắc policy/outcome. |

---

## 3. Ma trận Quyền theo Actor (`Capability Matrix`)

| Endpoint | Method | `Guest` | `Sales User` (Staff) | `Sales Manager` (Team Leader) | `CSKH BOD Viewer` | `CSKH CRM Admin` |
|---|---|---|---|---|---|---|
| `workflow.list_work_items` | `GET` | `403` | Chỉ `self` (owned active + closed do mình xử lý nếu vẫn trong quyền; **mất quyền ngay nếu Lead chuyển sang người khác**) | Chỉ `team` (áp chuẩn SQL L-23) | `403 FORBIDDEN` (chứa PII) | Toàn hệ thống (`all`) |
| `workflow.get_work_item` | `GET` | `403` | Chỉ Work Item đang sở hữu hiện tại | Chỉ Work Item thuộc `team` hợp lệ | `403 FORBIDDEN` (chứa PII) | Toàn hệ thống |
| `workflow.list_attempts` | `GET` | `403` | Chỉ Work Item đang sở hữu hiện tại | Chỉ Work Item thuộc `team` hợp lệ | `403 FORBIDDEN` (chứa PII) | Toàn hệ thống |
| `workflow.list_unlinked_call_logs` | `GET` | `403` | Chỉ Work Item đang sở hữu hiện tại | Chỉ Work Item thuộc `team` hợp lệ | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.record_attempt` | `POST` | `403` | Chỉ trên Work Item đang sở hữu (`performed_by = actor`, `self_reported`) | Trên Work Item thuộc `team` (`performed_by = actor`) | `403 FORBIDDEN` | Toàn hệ thống (`performed_by = actor`) |
| `workflow.get_outcome_codes` | `GET` | `403` | Đọc các mã `enabled = 1` | Đọc các mã `enabled = 1` | Đọc các mã `enabled = 1` | Đọc tất cả (`include_disabled`) |
| `workflow.schedule_followup` | `POST` | `403` | Chỉ trên Work Item đang sở hữu | Trên Work Item thuộc `team` | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.list_followups` | `GET` | `403` | Chỉ callback `assigned_to = self` của Work Item đang sở hữu | Chỉ callback thuộc `team` hợp lệ | `403 FORBIDDEN` (chứa PII) | Toàn hệ thống |
| `workflow.complete_followup` | `POST` | `403` | Chỉ trên Work Item đang sở hữu | Trên Work Item thuộc `team` | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.close_work_item` | `POST` | `403` | Chỉ trên Work Item đang sở hữu | Trên Work Item thuộc `team` | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.reopen_work_item` | `POST` | `403` | `403 FORBIDDEN` | Trong `team` khi `policy.allow_leader_reopen == 1` | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.request_correction` | `POST` | `403` | Chỉ cho Attempt do chính mình ghi trên Work Item đang sở hữu (`status="pending"`, không được `restore`) | Trên Work Item thuộc `team` (có thể duyệt luôn nếu `allow_leader_review_correction == 1`) | `403 FORBIDDEN` | Toàn hệ thống (`correct \| void \| restore`) |
| `workflow.review_correction` | `POST` | `403` | `403 FORBIDDEN` | Trong `team` khi `policy.allow_leader_review_correction == 1` | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.list_corrections` | `GET` | `403` | Chỉ yêu cầu của mình trên Work Item đang sở hữu | Yêu cầu thuộc `team` hợp lệ | `403 FORBIDDEN` | Toàn hệ thống |
| `workflow.progress_summary` | `GET` | `403` | Tổng hợp `self` (**0 PII**) | Tổng hợp `team` (**0 PII**) | Tổng hợp `all` (**0 PII**) | Tổng hợp `all` (**0 PII**) |
| `settings.list_policies` | `GET` | `403` | Đọc policy hiệu lực | Đọc policy `Global` + `Team` của mình | Đọc tất cả | Đọc tất cả |
| `settings.save_policy` | `POST` | `403` | `403 FORBIDDEN` | `403 FORBIDDEN` | `403 FORBIDDEN` | Cho phép (`version + 1`) |
| `settings.list_outcomes` | `GET` | `403` | Đọc mã bật | Đọc mã bật | Đọc tất cả | Đọc tất cả |
| `settings.save_outcome` | `POST` | `403` | `403 FORBIDDEN` | `403 FORBIDDEN` | `403 FORBIDDEN` | Cho phép (`version + 1`) |
| `settings.set_lead_dnc` | `POST` | `403` | `403 FORBIDDEN` | `403 FORBIDDEN` | `403 FORBIDDEN` | Cho phép (có lý do audit) |

---

## 4. Đặc tả Chi tiết Từng Endpoint

### 4.1. Nâng cấp tương thích ngược trên `GET crm_cskh.api.customers.my_list`

- **Chữ ký giữ nguyên 100% P1:** `my_list(page=1, page_size=20, status=None, campaign=None)`
- **Ngữ nghĩa nâng cấp P2:**
  - Khi `status` bỏ trống (`None`): trả tất cả Work Item đang thuộc quyền sở hữu active của caller (`status in ("assigned", "in_progress", "on_hold")`).
  - Khi truyền `status` cụ thể (`"assigned"`, `"in_progress"`, `"on_hold"`): lọc đúng trạng thái đó.
  - Mỗi phần tử trong `items` giữ nguyên khóa P1 (`work_item`, `lead`, `display_name`, `phone`, `source`, `campaign`, `status`, `assigned_at`) và bổ sung các khóa P2:
    ```json
    {
      "work_item": "a1b2c3d4e5",
      "lead": "CRM-LEAD-2026-00001",
      "display_name": "Nguyễn Văn An",
      "phone": "0912345678",
      "source": "Website",
      "campaign": "CAMP-001",
      "campaign_title": "Chiến dịch Q4",
      "status": "on_hold",
      "revision": 3,
      "assigned_at": "2026-11-03 09:00:00",
      "attempt_count": 1,
      "connected_attempt_count": 0,
      "last_attempt_at": "2026-11-03 09:15:00",
      "last_outcome_code": "no_answer",
      "last_outcome_label": "Không nghe máy",
      "open_callback_task": "TASK-0001",
      "next_action_due": "2026-11-04 10:00:00",
      "is_followup_overdue": false,
      "do_not_contact": false
    }
    ```
- **Lưu ý bàn giao cho T1A:** File UI P1 `apps/crm_cskh/crm_cskh/api/ownership_ui.py` (dòng 114 & 162) thuộc phạm vi UI của T1A hiện đang lọc cứng `status = 'assigned'`. Khi T1A làm UI P2, có thể dùng trực tiếp `crm_cskh.api.workflow.list_work_items` dưới đây hoặc mở rộng điều kiện ở `ownership_ui.py` sang `('assigned', 'in_progress', 'on_hold')`.

---

### 4.2. `GET crm_cskh.api.workflow.list_work_items`

Hàng đợi công việc CSKH theo scope (`self` / `team` / `all`), hỗ trợ bộ lọc trạng thái P2, tình trạng lịch hẹn và tìm kiếm an toàn.

- **Query params:**
  - `page` (`int`, default `1`), `page_size` (`int`, default `20`, max `100`)
  - `status` (`str`, optional): `active` (mặc định: `assigned,in_progress,on_hold`) hoặc một trong `assigned | in_progress | on_hold | closed | queued | revoked | all`
  - `campaign` (`str`, optional)
  - `followup_filter` (`str`, optional): `overdue | today | upcoming | has_open | none`
  - `assignee` (`str`, optional): chỉ thu hẹp trong phạm vi `team`/`all`; không bao giờ mở rộng scope
  - `search` (`str`, optional): tìm theo SĐT chuẩn hóa, tên hiển thị, hoặc mã Lead/Work Item trong phạm vi được phép
- **Response (`200`):**
  ```json
  {
    "scope": "self",
    "items": [ /* cùng cấu trúc mở rộng như my_list + assigned_to + closed_at */ ],
    "total": 12,
    "page": 1,
    "page_size": 20,
    "as_of": "2026-11-03 22:30:00"
  }
  ```

---

### 4.3. `GET crm_cskh.api.workflow.get_work_item`

- **Query params:** `work_item` (`str`, required)
- **Response (`200`):**
  ```json
  {
    "work_item": {
      "name": "a1b2c3d4e5",
      "campaign": "CAMP-001",
      "campaign_code": "P2-Q4",
      "campaign_title": "Chiến dịch Chăm sóc Q4",
      "campaign_status": "Active",
      "status": "on_hold",
      "revision": 3,
      "assigned_to": "staff.a@dev.local",
      "assigned_at": "2026-11-03 09:00:00",
      "first_attempt_at": "2026-11-03 09:15:00",
      "last_attempt_at": "2026-11-03 09:15:00",
      "last_outcome_code": "no_answer",
      "last_outcome_label": "Không nghe máy",
      "last_connected_at": null,
      "attempt_count": 1,
      "connected_attempt_count": 0,
      "open_callback_task": "TASK-0001",
      "next_action_due": "2026-11-04 10:00:00",
      "last_activity_by": "staff.a@dev.local",
      "closed_at": null,
      "closed_by": null,
      "completion_reason": null,
      "reopened_at": null,
      "reopened_by": null,
      "reopen_reason": null,
      "policy_version": 1
    },
    "lead": {
      "name": "CRM-LEAD-2026-00001",
      "display_name": "Nguyễn Văn An",
      "phone": "0912345678",
      "email": "an@example.test",
      "source": "Website",
      "is_placeholder_name": false,
      "do_not_contact": false,
      "dnc_reason": null,
      "dnc_at": null
    },
    "active_ownership": {
      "event_name": "9f8e7d6c5b",
      "owner": "staff.a@dev.local",
      "ownership_start": "2026-11-03 09:00:00"
    },
    "open_followup": {
      "task_id": "TASK-0001",
      "title": "Hẹn liên hệ lại — Nguyễn Văn An",
      "description": "Gọi lại buổi sáng",
      "due_at": "2026-11-04 10:00:00",
      "status": "Todo",
      "assigned_to": "staff.a@dev.local",
      "source_attempt": "att-0001",
      "reschedule_count": 0,
      "is_overdue": false
    },
    "effective_policy": {
      "name": "GLOBAL-DEFAULT",
      "scope_type": "Global",
      "version": 1,
      "allowed_backdate_minutes": 120,
      "max_open_followup_tasks_per_item": 1,
      "require_attempt_before_close": true,
      "allow_close_without_contact_for_invalid": true,
      "do_not_contact_behavior": "block_and_auto_close",
      "enabled_channels": ["phone_call", "sms", "zalo", "facebook", "other"],
      "allow_leader_reopen": true,
      "allow_leader_review_correction": true
    },
    "capabilities": {
      "can_record_attempt": true,
      "can_schedule_followup": true,
      "can_complete_followup": true,
      "can_close": true,
      "can_reopen": false,
      "can_request_correction": true,
      "can_review_correction": false,
      "can_reassign_or_revoke": false
    },
    "as_of": "2026-11-03 22:30:00"
  }
  ```

---

### 4.4. `GET crm_cskh.api.workflow.list_attempts`

- **Query params:** `work_item` (required), `page=1`, `page_size=20`, `include_milestones=true`
- **Response (`200`):**
  ```json
  {
    "work_item": "a1b2c3d4e5",
    "items": [
      {
        "attempt_id": "att-0001",
        "work_item": "a1b2c3d4e5",
        "ownership_event": "9f8e7d6c5b",
        "owner_at_attempt": "staff.a@dev.local",
        "performed_by": "staff.a@dev.local",
        "recorded_at": "2026-11-03 09:15:05",
        "verification_status": "self_reported",
        "is_voided": false,
        "has_approved_correction": false,
        "pending_correction_count": 0,
        "effective": {
          "occurred_at": "2026-11-03 09:15:00",
          "channel": "phone_call",
          "outcome_code": "no_answer",
          "outcome_label": "Không nghe máy",
          "outcome_group": "unreached",
          "outcome_version": 1,
          "is_contact_attempt": true,
          "is_connected": false,
          "is_interested": false,
          "is_terminal": false,
          "is_do_not_contact": false,
          "note": "Gọi đổ chuông hết hồi không bắt máy.",
          "call_log": null
        },
        "original": {
          "occurred_at": "2026-11-03 09:15:00",
          "channel": "phone_call",
          "outcome_code": "no_answer",
          "outcome_label": "Không nghe máy",
          "outcome_version": 1,
          "is_connected": false,
          "note": "Gọi đổ chuông hết hồi không bắt máy.",
          "call_log": null
        },
        "policy_snapshot": {
          "name": "GLOBAL-DEFAULT",
          "version": 1
        },
        "callback_task": "TASK-0001",
        "resolved_callback_task": null,
        "corrections": []
      }
    ],
    "ownership_milestones": [
      {
        "event_id": "assign:a1b2c3d4e5:req-1",
        "action": "assign",
        "from_user": null,
        "to_user": "staff.a@dev.local",
        "actor": "admin@dev.local",
        "event_at": "2026-11-03 09:00:00",
        "ownership_start": "2026-11-03 09:00:00",
        "ownership_end": null,
        "reason": "Phân phối chiến dịch"
      }
    ],
    "total": 1,
    "page": 1,
    "page_size": 20,
    "as_of": "2026-11-03 22:30:00"
  }
  ```

---

### 4.5. `POST crm_cskh.api.workflow.record_attempt`

Orchestration nguyên tử trong một DB transaction: kiểm tra quyền & `expected_revision` dưới khóa dòng `FOR UPDATE` → kiểm tra policy/outcome/backdate/ownership interval → tạo/link `CRM Call Log` (nếu có) → giải quyết callback mở (nếu `complete_open_followup=true`) → tạo callback `CRM Task` mới (nếu có `followup`) → lưu `CSKH Contact Attempt` → cập nhật `CRM Lead` DNC (nếu outcome DNC) → đóng `Work Item` (nếu `close_work_item=true` hoặc auto-close DNC) → cập nhật read-model & `revision` → commit → gửi thông báo sau commit.

- **Request body:**
  ```json
  {
    "work_item": "a1b2c3d4e5",
    "channel": "phone_call",
    "outcome_code": "no_answer",
    "occurred_at": "2026-11-03 09:15:00",
    "note": "Khách chưa bắt máy, hẹn gọi lại sáng mai.",
    "call_log": null,
    "call_log_data": null,
    "followup": {
      "due_at": "2026-11-04 10:00:00",
      "title": "Gọi lại sáng mai",
      "description": "Khách bận giờ hành chính"
    },
    "complete_open_followup": true,
    "close_work_item": false,
    "completion_reason": null,
    "expected_revision": 1,
    "expected_policy_version": 1,
    "idempotency_key": "att-req-0001"
  }
  ```
  *(Hỗ trợ cả tham số phẳng `followup_due_at`, `followup_title`, `followup_note` khi gửi `application/x-www-form-urlencoded`).*
- **Response (`200`):**
  ```json
  {
    "status": "recorded",
    "idempotent_replay": false,
    "attempt_id": "att-0001",
    "work_item": "a1b2c3d4e5",
    "work_item_status": "on_hold",
    "revision": 2,
    "attempt_count": 1,
    "connected_attempt_count": 0,
    "call_log": null,
    "callback_task": "TASK-0001",
    "resolved_callback_task": null,
    "do_not_contact": false
  }
  ```

---

### 4.6. `GET crm_cskh.api.workflow.get_outcome_codes`

- **Query params:** `channel` (optional), `work_item` (optional), `campaign` (optional), `include_disabled` (`bool`, default `false` — chỉ `CSKH CRM Admin` được truyền `true`).
- **Response (`200`):** `{ "items": [ ... ], "effective_policy": { ... }, "total": 12, "as_of": "..." }`.

---

### 4.7. `POST crm_cskh.api.workflow.schedule_followup`

Tạo mới (nếu chưa có callback mở) hoặc dời lịch (reschedule trên chính `CRM Task` đang mở, không sinh Task thứ hai).

- **Request body:**
  ```json
  {
    "work_item": "a1b2c3d4e5",
    "due_at": "2026-11-05 14:00:00",
    "title": "Dời lịch gọi lại chiều thứ Năm",
    "note": "Khách nhắn bận họp sáng",
    "reason": "Khách xin dời sang chiều 05/11",
    "expected_revision": 2,
    "idempotency_key": "fu-sched-001"
  }
  ```
  *(Khi đang có `open_callback_task`, đây là thao tác dời lịch nên `reason` bắt buộc khác rỗng; thiếu `reason` trả `422 NOTE_REQUIRED`).*
- **Response (`200`):**
  ```json
  {
    "status": "rescheduled",
    "idempotent_replay": false,
    "task_id": "TASK-0001",
    "work_item": "a1b2c3d4e5",
    "work_item_status": "on_hold",
    "due_at": "2026-11-05 14:00:00",
    "reschedule_count": 1,
    "revision": 3
  }
  ```

---

### 4.8. `GET crm_cskh.api.workflow.list_followups`

- **Query params:** `page=1`, `page_size=20`, `work_item=None`, `campaign=None`, `bucket=None` (`overdue | today | upcoming | all`), `status="open"` (`open | done | canceled | all`), `assignee=None`.
- **Response (`200`):** `{ "scope": "self", "items": [ ... ], "total": 1, "page": 1, "page_size": 20, "as_of": "..." }`.

---

### 4.9. `POST crm_cskh.api.workflow.complete_followup`

- **Request body:**
  ```json
  {
    "work_item": "a1b2c3d4e5",
    "task_id": "TASK-0001",
    "action": "cancel",
    "attempt_id": null,
    "completion_reason": "Khách chủ động gọi lại vào tổng đài trước giờ hẹn",
    "expected_revision": 3,
    "idempotency_key": "fu-comp-001"
  }
  ```
  - Nếu `action == "complete"`: **bắt buộc** truyền `attempt_id` hợp lệ thuộc cùng `work_item` (không cho đóng Task suông không có Attempt; thiếu `attempt_id` trả `422 OUTCOME_NOT_COMPATIBLE`).
  - Nếu `action == "cancel"`: **bắt buộc** truyền `completion_reason` khác rỗng (`422 NOTE_REQUIRED` nếu thiếu).
- **Response (`200`):** `{ "status": "canceled", "idempotent_replay": false, "task_id": "TASK-0001", "work_item": "a1b2c3d4e5", "work_item_status": "in_progress", "revision": 4 }`.

---

### 4.10. `POST crm_cskh.api.workflow.close_work_item`

- **Request body:**
  ```json
  {
    "work_item": "a1b2c3d4e5",
    "completion_reason": "Đã tư vấn thành công gói gia hạn năm",
    "close_category": "normal",
    "expected_revision": 4,
    "idempotency_key": "wi-close-001"
  }
  ```
  *(Nếu đóng không có attempt khi `allow_close_without_contact_for_invalid=1`, truyền `close_category="invalid_or_duplicate"` kèm `completion_reason` bắt buộc).*
- **Response (`200`):**
  ```json
  {
    "status": "closed",
    "idempotent_replay": false,
    "work_item": "a1b2c3d4e5",
    "work_item_status": "closed",
    "closed_at": "2026-11-03 15:00:00",
    "closed_by": "staff.b@dev.local",
    "canceled_followup_task": null,
    "revision": 5
  }
  ```

---

### 4.11. `POST crm_cskh.api.workflow.reopen_work_item`

- **Request body:**
  ```json
  {
    "work_item": "a1b2c3d4e5",
    "target_user": "staff.b@dev.local",
    "reason": "Khách liên hệ lại cần hỗ trợ bổ sung hợp đồng",
    "override_dnc": false,
    "expected_revision": 5,
    "idempotency_key": "wi-reopen-001"
  }
  ```
- **Response (`200`):**
  ```json
  {
    "status": "reopened",
    "idempotent_replay": false,
    "work_item": "a1b2c3d4e5",
    "work_item_status": "in_progress",
    "assigned_to": "staff.b@dev.local",
    "revision": 6
  }
  ```

---

### 4.12. `POST crm_cskh.api.workflow.request_correction` & `POST crm_cskh.api.workflow.review_correction`

- **`request_correction` Request:**
  ```json
  {
    "attempt": "att-0001",
    "action": "correct",
    "corrected_fields": {
      "outcome_code": "busy",
      "note": "Chọn nhầm Không nghe máy, thực tế báo máy bận"
    },
    "reason": "Chọn nhầm mã kết quả lúc thao tác nhanh",
    "auto_approve": false,
    "expected_revision": 2,
    "idempotency_key": "corr-req-001"
  }
  ```
  - `Staff` gửi yêu cầu luôn tạo `status = "pending"`; truyền `auto_approve=true` với tư cách `Staff` bị từ chối `403 FORBIDDEN`.
  - `Sales Manager` (trong team khi `allow_leader_review_correction=1`) hoặc `CSKH CRM Admin` có thể truyền `auto_approve=true` để áp dụng ngay trong 1 bước hoặc duyệt yêu cầu `pending` qua `review_correction`.
  - `action = "restore"` chỉ cho phép `CSKH CRM Admin`.
- **`review_correction` Request:**
  ```json
  {
    "correction_id": "corr-0001",
    "decision": "approve",
    "review_note": "Đã đối chiếu nhật ký cuộc gọi, đồng ý hiệu chỉnh",
    "expected_revision": 2,
    "idempotency_key": "corr-rev-001"
  }
  ```

---

### 4.13. `GET crm_cskh.api.workflow.progress_summary`

Thống kê vận hành P2 theo scope (`self` / `team` / `all`), **0 PII**, **không chấm điểm KPI/SLA P3**.

- **Query params:** `campaign=None`, `date_from=None`, `date_to=None`
- **Response (`200`):**
  ```json
  {
    "scope": "all",
    "cards": {
      "total_active_owned": 18,
      "assigned": 6,
      "in_progress": 8,
      "on_hold": 4,
      "closed": 5,
      "revoked": 1,
      "queued": 2,
      "effective_attempts": 34,
      "connected_attempts": 14,
      "unreached_attempts": 20,
      "open_followups": 4,
      "overdue_followups": 1,
      "due_today_followups": 2,
      "pending_corrections": 1,
      "dnc_leads": 1
    },
    "by_outcome_group": {
      "unreached": 20,
      "engaged": 6,
      "positive": 4,
      "negative": 1,
      "converted": 2,
      "invalid_contact": 0,
      "compliance": 1,
      "other": 0
    },
    "by_channel": {
      "phone_call": 26,
      "sms": 2,
      "zalo": 5,
      "facebook": 1,
      "other": 0
    },
    "per_user": [
      {
        "user": "staff.a@dev.local",
        "team": "CRM-SH-0001",
        "assigned": 3,
        "in_progress": 4,
        "on_hold": 2,
        "closed": 3,
        "effective_attempts": 19,
        "connected_attempts": 8,
        "open_followups": 2,
        "overdue_followups": 1
      }
    ],
    "as_of": "2026-11-03 22:30:00"
  }
  ```

---

### 4.14. Endpoints Quản trị Cấu hình (`crm_cskh.api.settings.*`)

1. **`GET crm_cskh.api.settings.list_policies(scope_type=None, campaign=None, team=None)`**
2. **`POST crm_cskh.api.settings.save_policy(policy_code, title, scope_type="Global", team=None, campaign=None, enabled=True, allowed_backdate_minutes=120, require_attempt_before_close=True, allow_close_without_contact_for_invalid=True, do_not_contact_behavior="block_and_auto_close", enabled_channels="phone_call,sms,zalo,facebook,other", allow_leader_reopen=True, allow_leader_review_correction=True, idempotency_key=None)`** — chỉ `CSKH CRM Admin`; mỗi lần lưu thay đổi tăng `version += 1`.
3. **`GET crm_cskh.api.settings.list_outcomes(include_disabled=True)`**
4. **`POST crm_cskh.api.settings.save_outcome(code, label_vi, result_group, description=None, is_contact_attempt=True, is_connected=False, is_interested=False, is_terminal=False, is_do_not_contact=False, requires_note=False, requires_followup=False, allows_followup=True, requires_completion_reason=False, allowed_channels="phone_call,sms,zalo,facebook,other", suggested_next_status="in_progress", enabled=True, sort_order=100, idempotency_key=None)`** — chỉ `CSKH CRM Admin`; mỗi lần lưu thay đổi tăng `version += 1`.
5. **`POST crm_cskh.api.settings.set_lead_dnc(lead, do_not_contact, reason, idempotency_key)`** — chỉ `CSKH CRM Admin`; ghi/gỡ cờ `CRM Lead.cskh_do_not_contact` kèm lý do kiểm toán bắt buộc.
