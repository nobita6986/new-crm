# P2 Entry Report & Reuse Matrix (`P2-WP00`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** `docs/P2-entry-report.md`
> **Ngày xác minh:** 03/11/2026
> **Thực hiện bởi:** T1B (Backend P2)
> **Baseline `main` đã nghiệm thu:** `1913d2bd5ebfdc894eaa492b46be502dbdf44808` (CI P0 `#41658199683` `success`, CI P1 `#41658199861` `success`)
> **Nhánh & Worktree cô lập:** `codex/p2-foundation` tại `D:/HRP-app/p2-foundation`
> **Môi trường DEV kiểm chứng cô lập:** Docker project `crm-cskh-p2-dev`, site `p2dev.localhost` (`127.0.0.1:18220`), **không đụng chạm** stack Owner `crm-cskh-p1-*` (`p1.localhost:8120`).

---

## 1. Xác minh Khóa Phiên bản (Version Lock) trên Runtime Cô lập `p2dev.localhost`

| Thành phần | Phiên bản / Tag | Full Commit SHA / Digest đã kiểm chứng | Trạng thái |
|---|---|---|---|
| **Frappe Framework** | `v15.123.1` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | **KHỚP 100% `version-lock.md`** |
| **Frappe CRM** | `v1.87.0` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | **KHỚP 100% `version-lock.md`** |
| **App mở rộng `crm_cskh`** | `0.0.1` | Baseline `1913d2bd5ebfdc894eaa492b46be502dbdf44808` | **ĐÃ CÀI & MIGRATE SẠCH** |
| **MariaDB / Redis / Bench** | `11.8` / `8.6-alpine` / `v5.31.0` | Ghim cứng `@sha256` theo `deploy/dev/.env.example` | **KHỚP 100%** |

---

## 2. Kết quả Kiểm chứng Cổng Đầu vào `E2-01..E2-10` (`P2.md` §3.2)

Mọi mục dưới đây được đối chiếu trực tiếp từ mã nguồn thực tế trong `apps/crm_cskh` (`1913d2b`), upstream `apps/crm` (`0d3910b4`), `apps/frappe` (`d2efa796`) và probe runtime trên `p2dev.localhost`:

| Mã | Hạng mục kiểm chứng | Nguồn mã thực tế (`file:line`) & Kết quả Runtime | Kết luận & Hành động P2 |
|---|---|---|---|
| **E2-01** | P1 `GO` & P1 Exit Report tồn tại, không còn blocker mở | `docs/architecture/P1-Exit-Report.md` (Chương 6, §6.4 `O-01`/`O-02` đã đóng), `main` `1913d2b`, CI P0 `#41658199683` (`99/99`) + CI P1 `#41658199861` (`209/209` + `12/12` UI + `170/170` HTTP) đều `success`. | **ĐẠT** — Đủ điều kiện mở P2; bảo toàn nguyên vẹn hai cổng CI P0 & P1. |
| **E2-02** | Ownership service, `CSKH Work Item` & `CSKH Assignment History` | `services/ownership.py:322-615`, `p1_models.py:137-266`. `CSKH Assignment History` ghi đầy đủ `ownership_start`, `ownership_end`, `from_user`, `to_user`, `event_id` (unique), append-only (`before_save`/`on_trash` chặn sửa/xóa). `ownership.py` đồng bộ nguyên tử `CRM Lead.lead_owner`, `DocShare`, `ToDo`, `CSKH Active Claim`. | **ĐẠT** — Mở rộng `CSKH Work Item` (`in_progress`, `on_hold`, `revision`, read-model) và mở rộng `CSKH Assignment History.action` thêm `reopen` (`close` đã có sẵn trong `cskh_assignment_history.json:19`, cần mở trong `p1_models.py:253`). |
| **E2-03** | Schema thực tế `CRM Call Log` ở `frappe/crm@v1.87.0` | `apps/crm/crm/fcrm/doctype/crm_call_log/crm_call_log.json` & `.py:50-343`: `autoname = "field:id"`, trường `reqd=1` gồm **`from`**, **`to`**, **`type`** (`Incoming\|Outgoing`), **`status`** (`Initiated\|Ringing\|In Progress\|Completed\|Failed\|Busy\|No Answer\|Queued\|Canceled`); tùy chọn `start_time`, `end_time`, `duration`, `caller`, `receiver`, `reference_doctype`, `reference_docname`, `telephony_medium` (`""\|Manual\|Twilio\|Exotel`, mặc định `"Manual"` ở dòng `54`). | **ĐẠT — REUSE CÓ ADAPTER (`ADR-P2-05`)**: Vì `from` và `to` bắt buộc kiểu `Phone`, **không bao giờ tự bịa SĐT người gọi giả** và **không tạo `CRM Call Log` cho kênh `sms`/`zalo`/`facebook`/`other`**. Chỉ link hoặc tạo `CRM Call Log` khi `channel == "phone_call"` và đủ dữ liệu thật hợp lệ; 1 `CRM Call Log` tối đa gắn 1 `Attempt` (`UNIQUE(call_log)`). |
| **E2-04** | Schema & hook thực tế `CRM Task` ở `frappe/crm@v1.87.0` | `apps/crm/crm/fcrm/doctype/crm_task/crm_task.json` & `.py:30-55`, `crm/api/todo.py:20-53`:<br>1) `autoname = "autoincrement"` (`task.name` là `int` lúc vừa `insert`, cần ép `str(task.name)`).<br>2) `status` có tập giá trị **`Backlog \| Todo \| In Progress \| Done \| Canceled`** (**không phải `"Open"`**; trạng thái mở chuẩn là `"Todo"`).<br>3) `todo.py:45-47` xác nhận hủy `ToDo` của `CRM Task` **không xóa `CRM Lead.lead_owner`**.<br>4) **Phát hiện quan trọng trên runtime `p2dev.localhost`:** `CRMTask.after_insert`/`validate` gọi `frappe.desk.form.assign_to.add/remove` vốn (a) bắn `CRM Notification` **ngay trong transaction** (`todo.py:28-30`) và (b) gây `PermissionError` khi `CSKH CRM Admin` chạy `ownership.reassign` (vì `CSKH CRM Admin` không có `DocPerm` trực tiếp trên `CRM Task` để bảo vệ lưới P0). | **ĐẠT — EXTEND + QUẢN LÝ `ToDo` CÓ KIỂM SOÁT (`ADR-P2-04`)**: Reuse `CRM Task` với Custom Fields `cskh_*` (**không** tạo bảng `Followup Task` mới). Trong service `followup`, quản lý `CRM Task.assigned_to` + `ToDo` trực tiếp trong transaction (bỏ qua `assign_to.add` nội bộ gây lỗi quyền/bắn thông báo sớm) và đẩy `CRM Notification` ra **sau `frappe.db.commit()`**. |
| **E2-05** | Schema & hiển thị `FCRM Note` ở `frappe/crm@v1.87.0` | `apps/crm/crm/fcrm/doctype/fcrm_note/fcrm_note.json` & `.py:1-34`: chỉ có `title` (`reqd=1`), `content` (`Text Editor` HTML), `reference_doctype`, `reference_docname`; có thể sửa/xóa tự do theo DocPerm của `Sales User`. | **ĐẠT — TÁCH VAI TRÒ (`ADR-P2-02`)**: Giữ `FCRM Note` làm ghi chú tham khảo tự do của Lead; **không dùng `FCRM Note` làm nguồn sự thật của `Contact Attempt`**. Nội dung ghi chú của lần liên hệ lưu trong `CSKH Contact Attempt.note` (đã sanitize, bất biến). |
| **E2-06** | Permission hooks từ P0/P1 trên `CRM Lead`, `CSKH Work Item`, `CRM Task`, `CRM Call Log`, `FCRM Note`, `File` | `permissions.py:99-248`, `p1_permissions.py:203-391`, `api/guarded_crm.py:1-310`:<br>- `CRM Lead` dùng `lead_query_conditions` + `lead_has_permission` (Staff chỉ thấy `lead_owner = user`; Leader theo cây `CRM Sales Hierarchy`; BOD = `1=0`).<br>- `CRM Call Log`, `CRM Task`, `FCRM Note`, `Comment`, `File` thừa kế quyền đọc từ `CRM Lead` liên kết (`linked_lead_has_permission`), từ chối fail-closed nếu thiếu liên kết Lead.<br>- **Khoảng trống cần khóa thêm ở P2:** (a) `DocPerm` gốc của `CRM Task`/`CRM Call Log` cho phép `Sales User` `create=1` ở cấp DocType nên cần `doc_events` chặn tạo/sửa trực tiếp ngoài orchestration; (b) `p1_permissions.assignment_history_*` đang cho phép `to_user = user` đọc dòng lịch sử cũ kể cả sau khi `Work Item` đã chuyển sang `B` — cần siết lại để sau `A -> B`, `A` mất hoàn toàn quyền đọc timeline/lịch sử của `Work Item` (`P2.md` §11.2 #3). | **ĐẠT — MỞ RỘNG TRONG `p2_permissions.py` (`ADR-P2-10`)**: Bổ sung query/doc permission cho 4 DocType P2, khóa `doc_events` trên `CRM Task` callback, và cập nhật quyền đọc `CSKH Assignment History` theo quyền hiện hành trên `CSKH Work Item` (không làm gãy bất kỳ test P0/P1 nào). |
| **E2-07** | Quy tắc chuẩn hóa SĐT & Dedupe / DNC khi re-import | `services/phone_identity.py`, `services/identity.py`, `services/import_service.py`, `jobs/import_worker.py`, `services/eligibility.py:249-277`. Khi Lead canonical đã tồn tại, `import_worker` reuse Lead hiện có và không ghi đè dữ liệu nghiệp vụ. | **ĐẠT — BỔ SUNG CỜ `DNC` (`ADR-P2-07`)**: Thêm `CRM Lead.cskh_do_not_contact` (`default=0`). `import_worker` tuyệt đối không reset cờ này; `eligibility._lead_exclusion_reason` loại Lead có `cskh_do_not_contact == 1` với mã lý do `"do_not_contact"`. |
| **E2-08** | Cơ chế UI extension từ P0/P1 | `www/cskh/*`, `public/cskh/*`, `templates/cskh/*`, `api/*_ui.py` (ADR-003 P0 & ADR-P1-06). T1B không sửa file UI của T1A; cung cấp hợp đồng JSON + capability flags + error codes chuẩn qua `crm_cskh.api.workflow.*` và `crm_cskh.api.settings.*`. | **ĐẠT** — Bàn giao sớm `docs/api-contract-p2.md` cho T1A. |
| **E2-09** | Timezone server / site / DB | `bootstrap.sh:113` & `seed_demo.py:101-106` đặt `time_zone = "Asia/Ho_Chi_Minh"`. Frappe lưu `Datetime` naive theo timezone site (`Asia/Ho_Chi_Minh`). | **ĐẠT** — Chuẩn hóa mọi phép so sánh `occurred_at`, `due_at`, `allowed_backdate_minutes`, `due_today` bằng `frappe.utils.get_datetime` và `frappe.utils.now_datetime()` theo `Asia/Ho_Chi_Minh`. |
| **E2-10** | Seed data & Test suite P0/P1 chạy xanh | P0 (`99/99` JUnit + `18` HTTP + `9` File-oracle + `74` remaining-gaps) và P1 (`209/209` JUnit + `12/12` UI + `170/170` HTTP) đã xanh trên `1913d2b`. | **ĐẠT** — Giữ tách biệt suite P2 (`test_p2_*.py`, `p2_test_inventory.json`, `ci_p2_http_probe.py`, `.github/workflows/p2.yml`). |

---

## 3. Ma trận Tái sử dụng P2 (`REUSE / CONFIGURE / EXTEND / BUILD NEW`)

| Thành phần | Phân loại | Quyết định kỹ thuật & Lý do |
|---|---|---|
| `CRM Lead` | **EXTEND** | Giữ làm hồ sơ khách hàng canonical duy nhất; chỉ thêm 4 Custom Fields tuân thủ `cskh_do_not_contact`, `cskh_dnc_reason`, `cskh_dnc_at`, `cskh_dnc_work_item`. Không tự động ghi đè `CRM Lead.status`. |
| `CSKH Work Item` (P1) | **EXTEND** | Mở rộng trực tiếp từ P1 với trạng thái `in_progress`, `on_hold`, bộ đếm `revision` và các cột read-model hiệu lực (`attempt_count`, `connected_attempt_count`, `last_attempt_at`, `last_outcome_code`, `open_callback_task`, `next_action_due`, `closed_*`, `reopened_*`). |
| `CSKH Assignment History` (P1) | **EXTEND** | Tái sử dụng sổ cái khoảng thời gian sở hữu (`ownership_start`..`ownership_end`); mở đầy đủ hành động `close` và `reopen` để kiểm chứng `Attempt.occurred_at >= ownership_start` và audit vòng đời. |
| `crm_cskh.services.ownership` (P1) | **EXTEND** | Giữ làm đường chuyển giao/thu hồi sở hữu duy nhất; nâng cấp `reassign` (chuyển `CRM Task` callback mở từ `A -> B` giữ nguyên `due_date`, tăng `revision`) và `revoke` (hủy `CRM Task` callback mở trong cùng transaction). |
| `CRM Task` (Frappe CRM) | **EXTEND** | Tái sử dụng làm tác vụ hẹn chăm sóc lại (callback) qua Custom Fields `cskh_work_item`, `cskh_is_followup`, `cskh_source_attempt`, `cskh_completed_attempt`, `cskh_completion_reason`, `cskh_reschedule_count`, `cskh_task_history_json`. **Không tạo DocType `Followup Task` riêng.** |
| `CRM Call Log` (Frappe CRM) | **REUSE** | Liên kết 1–1 tùy chọn từ `CSKH Contact Attempt.call_log` qua `call_log_adapter` khi `channel == "phone_call"` và đủ dữ liệu thoại thật (`from`, `to`, `type`, `status`). |
| `FCRM Note` (Frappe CRM) | **REUSE** | Giữ nguyên cho ghi chú tự do trên hồ sơ Lead (đã được bảo vệ bởi `linked_lead_has_permission` và `guarded_crm.get_activities`); không đếm là `Contact Attempt`. |
| `CSKH Outcome Code` | **BUILD NEW** | Từ điển mã kết quả liên hệ chuẩn tiếng Việt có `version`, cờ `is_connected`/`is_terminal`/`is_do_not_contact`, và ràng buộc kênh/trường bắt buộc. |
| `CSKH Policy` | **BUILD NEW** | Cấu hình chính sách vận hành P2 phân cấp `Campaign > Team > Global` có `version`. |
| `CSKH Contact Attempt` | **BUILD NEW** | Sự kiện liên hệ đa kênh bất biến (append-only), lưu đầy đủ snapshot của Outcome, Policy và khoảng sở hữu tại thời điểm diễn ra. |
| `CSKH Attempt Correction` | **BUILD NEW** | Luồng yêu cầu & phê duyệt hiệu chỉnh / hủy hiệu lực (`correct \| void \| restore`) tách riêng khỏi bản ghi `Attempt` gốc. |
| `crm_cskh.p1_errors` & `errors` | **EXTEND** | Tái sử dụng pipeline chuẩn hóa lỗi `after_request` để phát mã lỗi P2 ổn định (`code` + `p1_code`) và bảo toàn tuyệt đối File-oracle `404` của P0. |

---

## 4. Rà soát Toàn diện Giả định `status == "assigned"` trong P1 (`P2-WP02`)

Kết quả kiểm toán từng dòng mã P1 để nâng cấp lên `OWNED_ACTIVE_WORK_ITEM_STATES = ("assigned", "in_progress", "on_hold")` mà không thay chuỗi mù quáng:

| Tệp & Vị trí | Giả định hiện tại ở P1 | Ngữ nghĩa đúng trong P2 |
|---|---|---|
| `p1_models.py:26,109,158` | `ACTIVE_WORK_ITEM_STATES = ("queued", "assigned")` | Đổi thành `("queued", "assigned", "in_progress", "on_hold")`: cả 4 trạng thái này đều giữ `CSKH Active Claim` (1 active claim / Lead) và chặn đóng `CSKH Campaign`. |
| `p1_models.py:143-149` | `WorkItem.validate` chỉ kiểm `assigned_to`/`assigned_at` cho `"assigned"` | Kiểm cho toàn bộ `OWNED_ACTIVE_WORK_ITEM_STATES`; kiểm `open_callback_task` đồng bộ với `"on_hold"`; kiểm `"queued"`, `"revoked"`, `"closed"` không có `assigned_to`. |
| `p1_models.py:253-257` | `AssignmentHistory.before_save` chỉ cho `assign`, `reassign`, `revoke` | Cho phép `assign`, `reassign`, `revoke`, `close`, `reopen` (`assign`/`reassign`/`reopen` yêu cầu `to_user`; `revoke`/`close` yêu cầu `not to_user`). |
| `services/ownership.py:95` | `_capacity_usage` chỉ đếm `status == "assigned"` | Đếm `status in OWNED_ACTIVE_WORK_ITEM_STATES` (`in_progress` và `on_hold` vẫn đang chiếm sức chứa của nhân viên!). |
| `services/ownership.py:370` | `assign` kiểm tra xung đột `other_assigned` theo `status == "assigned"` | Kiểm theo `status in OWNED_ACTIVE_WORK_ITEM_STATES`. |
| `services/ownership.py:455,480` | `reassign` chỉ nhận `status == "assigned"` và ghi cứng `status = "assigned"` | Cho phép `reassign` trên mọi `OWNED_ACTIVE_WORK_ITEM_STATES`; bảo toàn trạng thái hiện tại (`assigned` / `in_progress` / `on_hold`), chuyển `CRM Task` callback mở từ `A -> B` giữ nguyên `due_date`, tăng `revision`. |
| `services/ownership.py:549` | `revoke` chỉ nhận `status == "assigned"` | Cho phép `revoke` trên mọi `OWNED_ACTIVE_WORK_ITEM_STATES`; hủy `CRM Task` callback mở trong cùng transaction, tăng `revision`. |
| `services/eligibility.py:102,258` | `capacity_usage` và `_lead_exclusion_reason` chỉ xét `status == "assigned"` | Xét `status in OWNED_ACTIVE_WORK_ITEM_STATES` + bổ sung loại trừ `cskh_do_not_contact == 1` (`"do_not_contact"`). |
| `services/distribution.py:111,125,989` | `_campaign_loads`, `_reconcile_run_db_counts`, `reconcile` chỉ đếm `status == "assigned"` | Đếm `status in ('assigned', 'in_progress', 'on_hold')` để tải chiến dịch và đối soát không bị hụt sau khi nhân viên bắt đầu chăm sóc. |
| `services/reporting.py:252,300` | `my_list` mặc định lọc cứng `status = "assigned"` | Khi `status=None`, trả toàn bộ `OWNED_ACTIVE_WORK_ITEM_STATES` kèm các trường P2; khi truyền `status` cụ thể, lọc đúng trạng thái đó. |
| `services/reporting.py:368,447,452` | `_scoped_wi_predicate` và `_team_lead_authorization_clauses` (`L-23`) chỉ xét `status = 'assigned'` | **Bắt buộc nâng lên `status in ('assigned', 'in_progress', 'on_hold')`** để Leader Đội A tiếp tục bị chặn hoàn toàn khi khách chuyển sang Đội B và chuyển trạng thái `in_progress` / `on_hold`! |
| `services/reporting.py:670,799,812,826,873,1046,1087,1152,1431` | Pool derived state & Distribution Dashboard chỉ xét `status = 'assigned'` | Gộp `('assigned', 'in_progress', 'on_hold')` vào trạng thái phân phối đang giao của Kho & Dashboard P1 (bổ sung thêm `work_item_status` chi tiết trên dòng Kho). |
| `api/ownership_ui.py:114,162` | File UI P1 của T1A lọc `status = 'assigned'` | Giữ nguyên không chạm file UI của T1A; đã ghi rõ trong `docs/api-contract-p2.md` §4.1 và cung cấp `crm_cskh.api.workflow.list_work_items` đầy đủ cho T1A. |
