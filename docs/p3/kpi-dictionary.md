# Từ Điển Chỉ Số KPI & Quy Tắc Tổng Hợp (`docs/p3/kpi-dictionary.md`)

- **Phiên bản công thức (`metric_version`):** `"cskh-metrics-v2"`
- **Múi giờ chuẩn (`time_basis_timezone`):** `"Asia/Ho_Chi_Minh"` (`UTC+07:00`, không có DST)
- **Tuyên bố cấu hình (`policy_notice`):**
  > **LƯU Ý QUAN TRỌNG (`DEV / configurable`):** Toàn bộ ngưỡng thời gian SLA (ví dụ `first_attempt_sla_minutes = 30`, `followup_grace_minutes = 15`, `handoff_min_grace_minutes = 15`, `min_exposure_business_minutes = 60`) và trọng số điểm quy trình (`25 / 20 / 20 / 20 / 15`) trong môi trường hiện tại là **giá trị mặc định `DEV / configurable` phục vụ kiểm chứng kỹ thuật**, **KHÔNG phải chính sách vận hành production đã được BOD phê duyệt**. Hệ thống cho phép cấu hình qua `CSKH Policy` mà không cần sửa code.

---

## 1. Quy tắc toán học bất biến áp dụng cho MỌI chỉ số

1. **Chia cho 0 (`denominator == 0`) & Chỉ số đếm (`unit == "count"`):**
   - Với chỉ số tỷ lệ (`unit == "percent"`), khi `denominator == 0`: tuyệt đối **không** trả về `0`, `0.0`, `100.0`, `NaN` hay `Infinity`; bắt buộc trả về:
     ```json
     {
       "value": null,
       "numerator": 0,
       "denominator": 0,
       "unit": "percent",
       "status": "insufficient_data",
       "reason": "no_eligible_records"
     }
     ```
   - Với chỉ số đếm nguyên (`unit == "count"`): trả `value = int`, `numerator = int`, **`denominator = null`** (tránh hiển thị nhầm kiểu `61/1` trên UI).
2. **Không cộng trung bình của phần trăm (`No Average of Percentages`):**
   - Mọi tổng hợp lên cấp cao hơn (`Employee -> Team -> Company`, hoặc `Daily -> Weekly -> Monthly -> Quarterly -> Yearly`) **bắt buộc** tính lại từ tập bản ghi nguồn hợp lệ (`SUM(numerator)` / `SUM(denominator)` hoặc `COUNT(DISTINCT work_item)` cho chỉ số không cộng tính), tuyệt đối không lấy trung bình cộng các tỷ lệ `%` ngày/nhân viên.
3. **Hai đơn vị thống kê phân biệt (`Lead/Work Item-level` vs `Ownership interval-level`):**
   - Một `CSKH Work Item` đi qua chuỗi chuyển giao `Staff A -> Staff B -> Staff A` trong cùng kỳ báo cáo:
     - Ở cấp **Ownership interval (`ASSIGNED_INTERVALS`, `FIRST_ATTEMPT_SLA_RATE`, `ELIGIBLE_COVERAGE`, `CONNECTED_RATE`)**: sinh **3** khoảng trách nhiệm riêng biệt.
     - Ở cấp **Work Item duy nhất (`UNIQUE_ASSIGNED_ITEMS`, `CONNECTED_ITEMS`, `OVERDUE_BACKLOG`, `CONVERSION_RATE`)**: đếm **`1` `DISTINCT work_item`** trong phạm vi truy vấn.
4. **Cohort SLA theo hạn đến hạn bất biến (`deadline_cohort`, `F-P3-02`):**
   - Một nghĩa vụ SLA `O` thuộc kỳ báo cáo `[period_start, period_end)` khi và chỉ khi **`period_start <= effective_due_at < period_end`** và `started_at <= as_of`.
   - Trạng thái của `O` được đánh giá **chặt chẽ tại thời điểm `as_of`**: cuộc gọi trễ diễn ra ở kỳ `M+1` **tuyệt đối không bao giờ xóa vi phạm SLA của kỳ `M`** hay đẩy nghĩa vụ sang kỳ `M+1`; cuộc gọi hoàn thành sớm ở cuối kỳ `M-1` cho nghĩa vụ có hạn trong kỳ `M` vẫn được ghi nhận `fulfilled_on_time` cho cohort hạn kỳ `M`.
5. **Nguồn sự kiện liên hệ & bảo toàn `verification_status` P2 (`F-P3-03`):**
   - Chỉ đếm **effective `CSKH Contact Attempt`** sau khi áp các `CSKH Attempt Correction` có `status = 'approved'` và `reviewed_at <= as_of` (`correct`, `void`, `restore`). Yêu cầu hiệu chỉnh `pending` chưa được duyệt không làm biến đổi trường hiệu lực hay `verification_status`.
   - Không bao giờ cộng trùng `CRM Call Log` hay `CRM Task` ngoài luồng CSKH.
   - Khi `qa_enabled = false`, hệ thống **không tự sinh `verified` giả**, nhưng **bảo toàn nguyên vẹn `CSKH Contact Attempt.verification_status` (`verified`, `self_reported`, `disputed`) hợp lệ từ P2** trên cả `attempt_breakdown` và `VERIFICATION_RATE`.
6. **Quy tắc làm tròn:**
   - Tỷ lệ phần trăm (`percent`), phân vị thời gian (`minutes`) và điểm số (`score_100`) làm tròn `ROUND(x, 2)` bằng `decimal.Decimal(..., ROUND_HALF_UP)`.

---

## 2. Danh mục chi tiết các chỉ số (`cskh-metrics-v2`, `P3.md` §§7.2–7.4)

### 2.1 Nhóm Khối lượng & Độ phủ (`Volume & Coverage`)
- **`ASSIGNED_INTERVALS` (`count`):** Số `ownership_interval` (`assign`, `reassign`, `reopen`) có `ownership_start` trong `[period_start, period_end)` và `<= as_of`. (`denominator = null`).
- **`UNIQUE_ASSIGNED_ITEMS` (`count`):** `COUNT(DISTINCT work_item)` trong tập `ASSIGNED_INTERVALS` của phạm vi và kỳ.
- **`CONTACT_ATTEMPT_COUNT` (`count`):** Số effective `CSKH Contact Attempt` hợp lệ (`!is_voided`, `is_contact_attempt = 1`) có `occurred_at` trong `[period_start, period_end)` và `<= as_of`, kèm `attempt_breakdown: {attempted, connected, self_reported, verified, disputed}`.
- **`CONNECTED_ATTEMPT_COUNT` (`count`):** Số effective `CSKH Contact Attempt` có `is_connected = 1` trong kỳ.
- **`CONNECTED_ITEMS` (`count`):** `COUNT(DISTINCT work_item)` có ít nhất 1 connected effective Attempt trong kỳ và phạm vi.
- **`WORK_ITEMS_TOUCHED` (`count`):** `COUNT(DISTINCT work_item)` có ít nhất 1 effective Attempt trong kỳ và phạm vi.
- **`ELIGIBLE_COVERAGE` (`percent`):**
  - **Mẫu số:** Số `ASSIGNED_INTERVALS` không bị hủy hợp lệ trước hạn (`!fa_canceled_validly`) và đã đủ điều kiện đánh giá tại cutoff (`>= 1` effective Attempt trước cutoff, hoặc nghĩa vụ `FIRST_ATTEMPT` đã đến hạn/waived, hoặc thời gian phơi sáng làm việc thực tế `business_minutes >= min_exposure_business_minutes`).
  - **Tử số:** Số interval trong mẫu số có `>= 1` effective Attempt trước cutoff kỳ.

### 2.2 Nhóm Tỷ lệ Kết nối & Chuyển đổi (`Connection & Conversion`, `F-P3-01`)
- **`CONNECTED_RATE` (`percent` — Grain: Ownership Interval):**
  - **Mẫu số (`tried_intervals`):** Số `ASSIGNED_INTERVALS` có `>= 1` effective Attempt trước cutoff và **không** mang kết quả sai số điện thoại hợp lệ (`!is_invalid_phone`). **Tuyệt đối không chia cho tổng số cuộc gọi `CONTACT_ATTEMPT_COUNT`.**
  - **Tử số (`connected_intervals`):** Số interval trong mẫu số có `>= 1` connected effective Attempt trước cutoff.
- **`CONVERSION_RATE` (`percent` — Grain: `DISTINCT Work Item`):**
  - **Mẫu số:** Số `UNIQUE_ASSIGNED_ITEMS` trong kỳ sau khi loại trừ các hồ sơ xác nhận sai số điện thoại (`is_invalid_phone`).
  - **Tử số:** Số hồ sơ trong mẫu số đạt kết quả chuyển đổi (`is_conversion = 1` trên `CSKH Outcome Code` hoặc thuộc `CSKH Campaign.conversion_outcome_codes`).

### 2.3 Nhóm Tuân thủ SLA & Tồn đọng vận hành (`SLA & Backlog`, `F-P3-02`)
- **`FIRST_ATTEMPT_SLA_RATE` (`percent`):**
  - **Cohort:** Nghĩa vụ `FIRST_ATTEMPT` có `period_start <= effective_due_at < period_end` và trạng thái tại `as_of` thuộc `('fulfilled_on_time', 'fulfilled_late', 'breached_open', 'breached_handoff')`.
  - **Tử số:** Số nghĩa vụ có trạng thái tại `as_of` là `'fulfilled_on_time'`.
- **`WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` (`percent`):** Tỷ lệ đạt SLA liên hệ lần đầu gom theo `DISTINCT work_item` trên cùng cohort hạn kỳ.
- **`FOLLOWUP_SLA_RATE` (`percent`):**
  - **Cohort:** Nghĩa vụ `FOLLOWUP` (`CRM Task` callback CSKH) có `period_start <= effective_due_at < period_end` và trạng thái tại `as_of` thuộc `('fulfilled_on_time', 'fulfilled_late', 'breached_open', 'breached_handoff')`.
  - **Tử số:** Số nghĩa vụ có trạng thái tại `as_of` là `'fulfilled_on_time'`.
- **`CALLBACK_COMPLETION_RATE` (`percent`):** Tử số là các nghĩa vụ `FOLLOWUP` trong cohort hạn kỳ đã được thực hiện liên hệ (`fulfilled_on_time` hoặc `fulfilled_late`).
- **`RESCHEDULE_RATE` (`percent`):** Số `CRM Task` callback có sự kiện dời lịch trên tổng số callback hoạt động trong kỳ.
- **`FIRST_CONNECTED_TRACKING_RATE` (`percent`, `informational_only: true`):** Chỉ số theo dõi kết nối lần đầu, không phạt nhân viên khi khách không bắt máy.
- **`MIN_ATTEMPTS_COMPLIANCE_RATE` (`percent`):** Chỉ kích hoạt khi `min_attempts_enabled = 1`; mặc định trả `status: "disabled"`.
- **`OVERDUE_BACKLOG` (`count`):** `COUNT(DISTINCT work_item)` đang mở quyền sở hữu tại `as_of` và có ít nhất 1 nghĩa vụ thực thi (`!informational_only`) đang ở trạng thái `breached_open` (`effective_due_at < as_of`). Nghĩa vụ lịch sử đã bàn giao và được người nhận sau xử lý xong vẫn giữ `SLA_BREACH_COUNT` lịch sử của người trước nhưng **không** còn nằm trong `OVERDUE_BACKLOG` hiện tại.
- **`OPEN_FOLLOWUPS_OVERDUE` (`count`):** Số nghĩa vụ `FOLLOWUP` đang mở quá hạn (`breached_open`) tại `as_of`.
- **`SLA_BREACH_COUNT` (`count`, alias drill-down/sort tương thích: `BREACH_COUNT`):** Tổng số nghĩa vụ thực thi (`FIRST_ATTEMPT`, `FOLLOWUP`) đến hạn trong kỳ bị vi phạm (`fulfilled_late`, `breached_open`, `breached_handoff`); `breached_open -> fulfilled_late` chỉ tính **1** vi phạm.
- **`SLA_WAIVER_COUNT` (`count`):** Số nghĩa vụ đến hạn trong kỳ được miễn trừ hợp lệ (`waived`) tại `as_of`.
- **`FIRST_ATTEMPT_LATENCY_P50_MINUTES` & `FIRST_ATTEMPT_LATENCY_P90_MINUTES` (`minutes`):** Trung vị (`P50`) và phân vị 90 (`P90`) số phút làm việc thực tế từ `ownership_start` đến effective Attempt đầu tiên (kèm metadata `wall_p50_minutes`, `wall_p90_minutes`).

### 2.4 Nhóm Chất lượng ghi nhận, Xác minh & QA (`Data Hygiene, Verification & QA`)
- **`VALID_OUTCOME_LOGGING_RATE` (`percent`):** Số effective Attempts trong kỳ đáp ứng đầy đủ trường bắt buộc theo snapshot/policy (`complete_fields`, `outcome_code`, `channel`, và `note` khi `requires_note = 1`) chia cho `CONTACT_ATTEMPT_COUNT`.
- **`VERIFICATION_RATE` (`percent`):** Số effective Attempts trong kỳ có trạng thái xác minh `verified` (từ P2 hoặc QA xác nhận) chia cho `CONTACT_ATTEMPT_COUNT`.
- **`CORRECTION_RATE` & `VOID_ATTEMPT_RATE` (`percent`):** Tỷ lệ bản ghi gốc phát sinh trong kỳ có yêu cầu đính chính / hủy (`void`) đã duyệt.
- **`DNC_COMPLIANCE_RATE` (`percent`):** Tỷ lệ khoảng trách nhiệm không phát sinh liên hệ trái phép sau mốc cờ `DNC` có hiệu lực.
- **`QA_PASS_RATE` (`percent`):**
  - Khi `qa_enabled = false`: trả `value: null, status: "disabled", reason: "qa_disabled_by_policy"`.
  - Khi `qa_enabled = true`: tính trên các `CSKH QA Review` hoàn tất (`published` / `approved`) thuộc **`cohort_type = 'random_sample'`** để chống thiên lệch chọn mẫu rủi ro (`risk_targeted` được tách riêng trong `qa_cohort_breakdown`).

---

## 3. Công thức `PROCESS_SCORE` (`0–100`, `P3.md` §7.5) & Golden Example bắt buộc

### 3.1 Năm thành phần chuẩn & Trọng số mặc định (`DEV / configurable`)

| Mã thành phần (`component_code`) | Chỉ số đầu vào (`r_i`, thang `0..100`) | Trọng số mặc định (`w_i`) | Điều kiện tham gia tổng hợp |
|---|---|---:|---|
| `C1_FIRST_ATTEMPT_SLA` | `FIRST_ATTEMPT_SLA_RATE` | `25` | `status == "ok"` (`denominator > 0`) |
| `C2_ELIGIBLE_COVERAGE` | `ELIGIBLE_COVERAGE` | `20` | `status == "ok"` (`denominator > 0`) |
| `C3_DATA_HYGIENE` | `VALID_OUTCOME_LOGGING_RATE` | `20` | `status == "ok"` (`denominator > 0`) |
| `C4_FOLLOWUP_SLA` | `FOLLOWUP_SLA_RATE` | `20` | `status == "ok"` (`denominator > 0`) |
| `C5_QA_QUALITY` | `QA_PASS_RATE` (`random_sample`) | `15` | `qa_enabled == true` **và** `random_sample_count >= min_qa_sample_size` (mặc định `5`) |

Mỗi phần tử trong `score_components` chuẩn hóa rõ trên **thang `0–100`** (`F-P3-09`):
- `weighted_points = ROUND((w_i * r_i) / 100, 2)` (nằm trong `[0, w_i]`, ví dụ `25 * 90 / 100 = 22.50` điểm trên thang `100`);
- `reweighted_points = ROUND((w_i * r_i) / valid_weight, 2)` (tổng các thành phần hợp lệ khớp điểm `PROCESS_SCORE`);
- `raw_weight_rate_product = ROUND(w_i * r_i, 2)` (giữ tích nguyên bản `2250.0` để đối soát công thức `7350 / 85`).

### 3.2 Golden Example bắt buộc (`7350 / 85 = 86.47` khi tắt QA, `85.50` khi đủ mẫu QA)

1. **Khi `qa_enabled = false`** với vector `FIRST_ATTEMPT_SLA_RATE = 90.0` (`9/10`), `ELIGIBLE_COVERAGE = 80.0` (`8/10`), `VALID_OUTCOME_LOGGING_RATE = 100.0` (`18/18`), `FOLLOWUP_SLA_RATE = 75.0` (`3/4`):
   - Tích trọng số: $25 \times 90 + 20 \times 80 + 20 \times 100 + 20 \times 75 = 2250 + 1600 + 2000 + 1500 = 7350$ (`numerator = 73.50` trên thang `100`).
   - Tổng trọng số hợp lệ: $W_{\text{valid}} = 85 \ge 60$.
   - Điểm tạm tính: $\text{ROUND}(7350 / 85, 2) = \mathbf{86.47}$, `status = "provisional"`, `missing_components = ["C5_QA_QUALITY"]`, `label_display_vi = "86,47 — Tạm tính; thiếu mẫu QA"`.
2. **Khi `qa_enabled = true`** và có `5` mẫu `random_sample` đạt `4/5 = 80.0%`:
   - Tích trọng số: $7350 + 15 \times 80 = 8550$, $W_{\text{valid}} = 100$.
   - Điểm chính thức: $\text{ROUND}(8550 / 100, 2) = \mathbf{85.50}$, `status = "final"`, `missing_components = []`, `label_display_vi = "85,50 — Đã chốt"`.
