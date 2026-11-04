# P2 — Kiểm soát Đua tranh, Idempotency, Phục hồi Lỗi & Đối soát (`Race, Retry & Reconciliation`)

> **Tài liệu:** `docs/race-retry-reconciliation.md`
> **Phụ thuộc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§8, §15.4, §15.5), [`docs/adr/ADR-P2-11.md`](./adr/ADR-P2-11.md)

---

## 1. Chiến lược Transaction & Khóa Dòng (`SELECT ... FOR UPDATE`)

Mọi thao tác ghi trên một `CSKH Work Item` (`record_attempt`, `schedule_followup`, `complete_followup`, `close_work_item`, `reopen_work_item`, `request_correction`, `review_correction`, `ownership.assign`, `ownership.reassign`, `ownership.revoke`) tuân thủ đúng một thứ tự khóa để loại bỏ race condition và tránh deadlock:

1. **Bước 1 — Khóa dòng `CSKH Work Item` trước tiên:**
   ```sql
   SELECT name, lead, campaign, status, assigned_to, revision, open_callback_task
   FROM `tabCSKH Work Item`
   WHERE name = %s
   FOR UPDATE
   ```
2. **Bước 2 — Kiểm tra Idempotency dưới khóa dòng:**
   - Tra cứu bản ghi theo khóa idempotent (`(work_item, performed_by, idempotency_key)` cho `Attempt`, `(requested_by, idempotency_key)` cho `Correction`, hoặc bảng/cột khóa idempotent của thao tác).
   - **Cùng `idempotency_key` + cùng `payload_hash`:** trả lại kết quả đã lưu (`idempotent_replay = True`), **không ghi thêm bất kỳ dòng mới nào**.
   - **Cùng `idempotency_key` + khác `payload_hash`:** từ chối `409 IDEMPOTENCY_CONFLICT`.
3. **Bước 3 — Kiểm tra Quyền sở hữu & `expected_revision` dưới khóa dòng:**
   - Nếu `Work Item` vừa bị `reassign` sang `B` hoặc `revoke` bởi tiến trình khác trước khi tiến trình hiện tại lấy được khóa:
     - Với `Staff A`: `assigned_to != A` → từ chối ngay **`409 OWNERSHIP_CHANGED`** (không bao giờ ghi `Attempt` vào khoảng sở hữu đã đóng của `A` hay khoảng sở hữu mới của `B`!).
     - Với cùng owner nhưng tab trình duyệt cũ: `revision != expected_revision` → từ chối ngay **`409 STALE_WORK_ITEM`**.
4. **Bước 4 — Ghi Nguyên tử Toàn bộ Thay đổi (All-or-Nothing):**
   - Tạo savepoint giao dịch (`frappe.db.savepoint(...)`).
   - Thực hiện tuần tự: `CRM Call Log` adapter → giải quyết/tạo `CRM Task` callback + `ToDo` → chèn `CSKH Contact Attempt` / `CSKH Attempt Correction` → cập nhật `CRM Lead` DNC (nếu có) → cập nhật `CSKH Assignment History` (nếu `close`/`reopen`) → cập nhật read-model & `revision = revision + 1` trên `CSKH Work Item`.
   - Nếu bất kỳ bước nào ném ngoại lệ (ví dụ `CRM Task` hoặc `CRM Call Log` validation lỗi, hoặc lỗi DB được tiêm trong chaos test): `frappe.db.rollback(save_point=...)` khôi phục sạch 100% — không để lại orphan `Attempt`, orphan `CRM Task`, trạng thái `Work Item` dở dang hay `revision` lệch.
5. **Bước 5 — Hậu Commit (`Post-Commit Notification`):**
   - Chỉ sau khi `frappe.db.commit()` hoàn tất (trong ngữ cảnh request thực thi), `crm_cskh.services.notifications` mới gửi `CRM Notification` một cách idempotent và bọc `try/except` an toàn. Lỗi gửi thông báo chỉ ghi error log, không bao giờ làm hỏng kết quả nghiệp vụ đã lưu, và retry cùng `idempotency_key` sẽ đi vào Bước 2 (`idempotent_replay = True`) mà không tạo bản ghi lần hai.

---

## 2. Xử lý Các Tình huống Đua tranh Điển hình (`T2-18`, `T2-19`, `T2-21`)

| Tình huống đồng thời | Kết quả đảm bảo |
|---|---|
| Double-click / mạng chập chờn gửi 2 POST `record_attempt` cùng `idempotency_key` và cùng payload | Request đầu tiên lấy khóa `FOR UPDATE` và ghi DB; request thứ hai hoặc chờ khóa rồi thấy bản ghi đã tồn tại (trả `idempotent_replay=True`), hoặc đụng `UNIQUE(work_item, performed_by, idempotency_key)` được bắt gọn và trả kết quả gốc. Số `Attempt` trong DB = `1`, số `CRM Task` mở = tối đa `1`. |
| Hai tab mở cùng `Work Item` (`expected_revision=1`), gửi 2 POST khác `idempotency_key` | Tab thắng cuộc nâng `revision` lên `2`; tab đến sau thấy `revision (2) != expected_revision (1)` và nhận `409 STALE_WORK_ITEM`. |
| Hai request `schedule_followup` chạy song song trên cùng `Work Item` | Tuần tự hóa qua khóa `FOR UPDATE` trên `CSKH Work Item`; luôn duy trì bất biến `count(open CRM Task where cskh_work_item=X and cskh_is_followup=1) <= 1`. |
| `Staff A` bấm `record_attempt` đúng lúc `Admin` bấm `ownership.reassign(A -> B)` | Cả `ownership.reassign` và `record_attempt` đều khóa `FOR UPDATE` trên cùng dòng `CSKH Work Item`:<br>- Nếu `record_attempt` vào trước: `Attempt` gắn đúng khoảng sở hữu đang mở của `A`, tăng `revision`; sau đó `reassign` chuyển `Work Item` (và callback nếu có) sang `B`.<br>- Nếu `reassign` vào trước: khoảng sở hữu của `A` đã đóng và `assigned_to == B`; `record_attempt` của `A` thấy `assigned_to != A` và bị từ chối `409 OWNERSHIP_CHANGED`. |

---

## 3. Bộ Kiểm tra Đối soát Toàn vẹn Dữ liệu P2 (`Reconciliation Checks` — `P2.md` §15.5)

Hàm `crm_cskh.services.read_model.reconcile_p2_invariants(campaign=None)` kiểm tra trực tiếp trên MariaDB và yêu cầu **0 sai lệch** trên mọi bài test và cổng CI:

1. **Khớp `attempt_count` & `connected_attempt_count`:** Với mọi `CSKH Work Item`, các cột `attempt_count`, `connected_attempt_count`, `first_attempt_at`, `last_attempt_at`, `last_connected_at`, `last_outcome_code` khớp 100% với kết quả tính từ tập `CSKH Contact Attempt` + `CSKH Attempt Correction(status='approved')` (loại các bản ghi đã `void`).
2. **Bất biến Callback Mở (`<= 1`):** Không có `CSKH Work Item` nào có `> 1` bản ghi `CRM Task` (`cskh_is_followup = 1`, `status in ('Todo', 'In Progress', 'Backlog')`).
3. **Đồng bộ `on_hold` ↔ `open_callback_task`:**
   - Mọi `Work Item` có `status == 'on_hold'` đều có đúng `1` `CRM Task` callback đang mở trùng với `open_callback_task`, `assigned_to` của Task trùng `Work Item.assigned_to`, và `due_date == next_action_due`.
   - Mọi `Work Item` có `status in ('queued', 'assigned', 'in_progress', 'closed', 'revoked')` đều có **0** `CRM Task` callback đang mở và `open_callback_task IS NULL`.
4. **Ràng buộc Khoảng Sở hữu (`Ownership Interval Integrity`):** Mọi `CSKH Contact Attempt` đều tham chiếu tới một `CSKH Assignment History` thuộc cùng `work_item`, có `owner_at_attempt == ownership_event.to_user`, và `occurred_at >= ownership_event.ownership_start - 1s` (và `<= ownership_event.ownership_end + 1s` nếu khoảng sở hữu đã đóng sau đó).
5. **Liên kết `CRM Call Log` Duy nhất & Đúng Kênh:** Không có 2 `CSKH Contact Attempt` cùng trỏ tới 1 `call_log`, và không có `Attempt` nào có `channel != 'phone_call'` mà `call_log IS NOT NULL`.
6. **Bất biến P1 Bảo toàn:** Mỗi `CRM Lead` có tối đa `1` `CSKH Active Claim`, chỉ các `Work Item` ở `ACTIVE_WORK_ITEM_STATES` mới giữ `CSKH Active Claim`, và `CRM Lead.lead_owner` khớp tuyệt đối với `Work Item` đang thuộc `OWNED_ACTIVE_WORK_ITEM_STATES`.
