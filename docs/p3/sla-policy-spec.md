# Đặc Tả Chính Sách & Vòng Đời Nghĩa Vụ SLA (`docs/p3/sla-policy-spec.md`)

> **Tuyên bố cấu hình (`DEV / configurable`):** Các tham số ngưỡng phút mặc định dưới đây là cấu hình mẫu cho môi trường DEV/test, **không phải** chính sách production do BOD ban hành. Mọi ngưỡng được đọc từ `CSKH Policy` (thứ tự ưu tiên `campaign -> team -> global`) và chụp snapshot bất biến vào từng bản ghi `CSKH SLA Obligation` tại thời điểm sinh nghĩa vụ.

---

## 1. Bốn loại nghĩa vụ SLA (`obligation_type`)

| Loại nghĩa vụ | Trạng thái kích hoạt mặc định | Mốc bắt đầu (`started_at`) | Hạn gốc (`original_due_at`) | Ảnh hưởng điểm nhân viên (`PROCESS_SCORE`) |
|---|---|---|---|---|
| `FIRST_ATTEMPT` | **Bật** (`p3_sla_enabled = 1`) | Thời điểm mở `ownership_interval` chưa có effective attempt hợp lệ đầu tiên trên Work Item | `add_business_minutes(started_at, first_attempt_sla_minutes, calendar)` (cho Work Item gốc); với chuyển giao xem §3 | **Có** (`C1_FIRST_ATTEMPT_SLA`) |
| `FOLLOWUP` | **Bật** (`p3_sla_enabled = 1`) | Thời điểm tạo/dời hợp lệ callback `CRM Task` (`cskh_is_followup = 1`) | `add_business_minutes(task.due_date, followup_grace_minutes, calendar)` | **Có** (`C2_FOLLOWUP_SLA`) |
| `FIRST_CONNECTED` | **Theo dõi riêng** (`track_first_connected = 1`) | Thời điểm giao Work Item lần đầu | `add_business_minutes(started_at, first_connected_target_minutes, calendar)` | **KHÔNG** (`informational_only = 1`; khách không nghe máy không bị tính lỗi nhân viên) |
| `MIN_ATTEMPTS` | **Tắt mặc định** (`min_attempts_enabled = 0`); chỉ sinh khi bật rõ ràng trong `CSKH Policy` | Thời điểm mở Work Item thuộc policy bật `min_attempts_enabled = 1` | Kiểm tra tại sự kiện đóng Work Item với lý do không liên hệ được | Chỉ tham gia khi policy bật rõ ràng |

---

## 2. Máy trạng thái `CSKH SLA Obligation` & Bất biến `original_due_at`

- **Các trạng thái hợp lệ (`status`):**
  - `open`: Đang hoạt động, chưa hoàn thành và chưa quá `effective_due_at` (hoặc đang tạm dừng hợp lệ `is_paused = 1`).
  - `fulfilled_on_time`: Hoàn thành bởi effective `CSKH Contact Attempt` có `attempt_at <= effective_due_at` (và chưa từng rơi vào `breached_open`).
  - `fulfilled_late`: Đã quá `effective_due_at`, sau đó mới được giải quyết bởi effective `CSKH Contact Attempt` (`attempt_at > effective_due_at`). Vẫn được tính là hoàn thành tác nghiệp ở `CALLBACK_COMPLETION_RATE`, nhưng tính là **trễ hạn** (không nằm trong tử số `*_SLA_RATE`).
  - `breached_open`: Đã quá `effective_due_at` và chưa có effective `CSKH Contact Attempt` giải quyết.
  - `breached_handoff`: Nghĩa vụ của người giao `A` đã quá hạn trước thời điểm chuyển giao sang `B` (`reassigned_at > effective_due_at`); đóng lại cho interval của `A` với trạng thái vi phạm không thể xóa.
  - `handoff_closed`: Nghĩa vụ của `A` được chuyển giao sang `B` **trước khi quá hạn** (`reassigned_at <= effective_due_at`); không tính lỗi cho `A`.
  - `superseded`: Nghĩa vụ `FOLLOWUP` được dời lịch hợp lệ **trước khi quá hạn** (`rescheduled_at <= effective_due_at`).
  - `waived`: Được miễn trừ bởi cấp có thẩm quyền (`CSKH Team Leader` trong phạm vi team với chính sách cho phép, hoặc `CSKH CRM Admin`), bắt buộc có `waive_reason` và ghi nhật ký bất biến. Staff **không bao giờ** tự waive được.
  - `canceled_validly`: Đóng hợp lệ do sự kiện khách hàng `DNC` hoặc thu hồi chiến dịch xảy ra **trước khi quá hạn** (`event_at <= effective_due_at`). Nếu `DNC` xảy ra **sau khi đã quá hạn** (`event_at > effective_due_at`), nghĩa vụ đã vi phạm giữ trạng thái vi phạm lịch sử nhưng ngừng sinh nghĩa vụ mới từ thời điểm `DNC`.

- **Bất biến `original_due_at`:**
  - `original_due_at` được gán đúng 1 lần khi insert `CSKH SLA Obligation` và bị khóa ở `validate()` (`p3_models.py`): mọi nỗ lực sửa `original_due_at` đều bị từ chối bằng `PolicyConflict` / `ValidationError`.
  - Mọi điều chỉnh hợp lệ (tạm dừng `pause`/`resume` có thẩm quyền) chỉ cộng bù số phút làm việc bị tạm dừng vào `effective_due_at` và lưu vết đầy đủ trong `audit_events_json`.
  - **Worker đánh giá trễ (`Delayed Worker Invariant`):** Việc đánh giá quá hạn luôn so sánh `attempt_at` (hoặc `as_of`) với `effective_due_at`, tuyệt đối không lấy `worker_run_at` để dời `effective_due_at` hay biến một attempt đúng hạn (`attempt_at <= effective_due_at < worker_run_at`) thành vi phạm.

---

## 3. Quy tắc chuyển giao quyền sở hữu (`A -> B`, `A -> B -> A`) & Chống lạm dụng Handoff Grace

1. **Tách hai góc nhìn đo lường:**
   - **Work Item SLA (Góc nhìn khách hàng/công ty):** Theo dõi hạn gốc của Work Item (`work_item_original_due_at`) từ lần giao đầu tiên đến lần liên hệ hợp lệ đầu tiên của bất kỳ nhân viên nào.
   - **Interval Accountability SLA (Góc nhìn trách nhiệm nhân viên):** Gắn với `interval_id` cụ thể trên `CSKH Assignment History`.
2. **Chuyển giao `A -> B` TRƯỚC khi `A` quá hạn (`reassigned_at <= A.effective_due_at`) và chưa có attempt:**
   - Nghĩa vụ interval của `A` chuyển sang `handoff_closed` (loại khỏi mẫu số lỗi của `A`).
   - Mở nghĩa vụ `FIRST_ATTEMPT` mới cho interval của `B`:
     - Thời gian còn lại của hạn gốc: $R = \text{business\_minutes\_between}(\text{reassigned\_at}, \text{work\_item\_original\_due\_at})$.
     - Nếu số lần cấp bù `handoff_grace_reset_count < max_handoff_grace_resets` (mặc định `DEV = 2`):
       $$\text{target\_minutes}_B = \max(R, \text{handoff\_min\_grace\_minutes})$$
     - Nếu đã chạm trần `max_handoff_grace_resets` (chống chuyền bóng `A -> B -> C -> A` để né SLA): `B` không được cộng thêm grace mới ngoài thời hạn còn lại (hoặc ngưỡng tối thiểu chống kẹt kỹ thuật `0`, đồng thời gắn cờ `handoff_cap_reached = 1` để cảnh báo quản trị).
3. **Chuyển giao `A -> B` SAU khi `A` đã quá hạn (`reassigned_at > A.effective_due_at`):**
   - Nghĩa vụ của `A` chốt ở `breached_handoff` (`breached_at = A.effective_due_at`) -> **ghi nhận lỗi SLA cho `A` và team của `A` tại thời điểm đó**.
   - `B` nhận một nghĩa vụ khắc phục (`is_recovery_obligation = 1`) với hạn `add_business_minutes(reassigned_at, handoff_min_grace_minutes, calendar)` tính từ `reassigned_at` — `B` **không bị gánh lỗi quá hạn trước đó của `A`**, nhưng phải chịu trách nhiệm trên hạn khắc phục mới của mình.
4. **Vòng lặp `A -> B -> A`:**
   - Sinh 3 dòng `CSKH Assignment History` (`INT-1` của A, `INT-2` của B, `INT-3` của A) và tối đa 3 nghĩa vụ interval độc lập theo `idempotency_key = sha256(obligation_type + work_item + interval_id + task_id + seq)`.

---

## 4. Chống nhân đôi cảnh báo (`Idempotent Alerts & Retries`)

- Mỗi cảnh báo sắp đến hạn (`due_soon`) hoặc quá hạn (`breached`) tạo khóa duy nhất `alert_dedupe_key = "{obligation_name}:{stage}"` lưu trong bảng/trường trạng thái thông báo đã gửi (`last_alert_stage`, `alerted_at`).
- Khi RQ worker `jobs/evaluate_sla.py` chạy lặp lại hoặc retry do lỗi mạng/DB tạm thời, hệ thống kiểm tra `SELECT ... FOR UPDATE` trên `CSKH SLA Obligation` và bỏ qua mọi cảnh báo đã phát hành (`0` duplicate notifications).
