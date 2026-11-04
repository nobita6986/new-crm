# Từ Điển Chỉ Số KPI & Quy Tắc Tổng Hợp (`docs/p3/kpi-dictionary.md`)

- **Phiên bản công thức (`metric_version`):** `"cskh-metrics-v1"`
- **Múi giờ chuẩn (`time_basis_timezone`):** `"Asia/Ho_Chi_Minh"` (`UTC+07:00`, không có DST)
- ** Tuyên bố cấu hình (`policy_notice`):**
  > **LƯU Ý QUAN TRỌNG (`DEV / configurable`):** Toàn bộ ngưỡng thời gian SLA (ví dụ `first_attempt_sla_minutes = 30`, `followup_grace_minutes = 15`, `handoff_min_grace_minutes = 15`) và trọng số điểm quy trình (`25 / 20 / 20 / 20 / 15`) trong môi trường hiện tại là **giá trị mặc định `DEV / configurable` phục vụ kiểm chứng kỹ thuật**, **KHÔNG phải chính sách vận hành production đã được BOD phê duyệt**. Hệ thống cho phép cấu hình qua `CSKH Policy` mà không cần sửa code.

---

## 1. Quy tắc toán học bất biến áp dụng cho MỌI chỉ số

1. **Chia cho 0 (`denominator == 0`):**
   - Tuyệt đối **không** trả về `0`, `0.0`, `100.0`, `NaN` hay `Infinity`.
   - Bắt buộc trả về cấu trúc chuẩn:
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
2. **Không cộng trung bình của phần trăm (`No Average of Percentages`):**
   - Mọi tổng hợp lên cấp cao hơn (`Employee -> Team -> Company`, hoặc `Daily -> Weekly -> Monthly -> Quarterly -> Yearly`) **bắt buộc** tính lại từ tổng tử số và tổng mẫu số đủ điều kiện (hoặc truy vấn `COUNT(DISTINCT ...)` đối với chỉ số không cộng tính), tuyệt đối không lấy trung bình cộng các tỷ lệ `%` ngày/nhân viên.
3. **Khác biệt giữa Grain Công ty / Chiến dịch và Grain Nhân viên / Team (`Company DISTINCT Work Item` != `Employee Ownership Interval`):**
   - Một `CSKH Work Item` đi qua chuỗi chuyển giao `Staff A -> Staff B -> Staff A` trong cùng kỳ báo cáo:
     - Ở cấp **Employee interval**: sinh **3** khoảng trách nhiệm (`interval_1` của A, `interval_2` của B, `interval_3` của A) trên **2** nhân sự phân biệt (`A`, `B`).
     - Ở cấp **Company / Campaign Work Item**: chỉ được đếm là **1** `DISTINCT work_item` (không cộng `1 + 1 + 1 = 3` thành 3 hồ sơ công việc của công ty).
   - Vì vậy bảng chỉ số tách rõ hai họ metric SLA:
     - `WORK_ITEM_*_SLA_RATE` (grain: `DISTINCT work_item` hoặc `obligation` gốc của khách hàng đối với công ty/chiến dịch);
     - `EMPLOYEE_*_SLA_RATE` / `FIRST_ATTEMPT_SLA_RATE` ở scope `employee`/`team` (grain: nghĩa vụ gắn với từng `ownership interval` của nhân viên/team tại thời điểm phát sinh trách nhiệm).
4. **Nguồn sự kiện liên hệ duy nhất:**
   - Chỉ đếm **effective `CSKH Contact Attempt`** sau khi đã áp các bản ghi `CSKH Attempt Correction` có `status = 'approved'` (`correct`, `void`, `restore`) có `reviewed_at <= as_of`.
   - Các attempt có `effective_status == 'voided'` bị loại khỏi tử số/mẫu số hoạt động liên hệ hợp lệ và được theo dõi riêng ở `VOID_ATTEMPT_RATE`.
   - **Không bao giờ** cộng thêm bản ghi `CRM Call Log` (vốn chỉ là log kỹ thuật 1-1 hoặc log cuộc gọi thô của upstream).
5. **Tách biệt minh bạch mức độ xác thực kết quả:**
   - Mọi bộ đếm kết quả tách rõ: `attempted` (đã thực hiện liên hệ hợp lệ), `connected` (`effective_is_connected = 1`), `self_reported` (nhân viên tự ghi nhận chưa qua QA), `verified` (đã có `CSKH QA Review` hợp lệ xác nhận khi `qa_enabled = 1`), và `disputed` (đang có yêu cầu đính chính `pending` hoặc bị QA gắn cờ tranh chấp). Khi `qa_enabled = 0`, `verified = 0` trung thực (không bao giờ tự gán `self_reported` thành `verified`).
6. **Quy tắc làm tròn:**
   - Tỷ lệ phần trăm (`percent`) và điểm số (`score_100`) làm tròn `ROUND(x, 2)` bằng `decimal.Decimal(..., ROUND_HALF_UP)` ở bước hiển thị cuối cùng sau khi đã nhân `100`, giữ nguyên `numerator` và `denominator` nguyên gốc để đối soát.

---

## 2. Danh mục chi tiết các chỉ số (`cskh-metrics-v1`)

### 2.1 `FIRST_ATTEMPT_SLA_RATE` — Tỷ lệ đạt SLA liên hệ lần đầu (Trách nhiệm thực thi)
- **Đơn vị:** `percent` (`0.00` – `100.00`)
- **Grain:**
  - Scope `employee` / `team`: Các `CSKH SLA Obligation` có `obligation_type = 'FIRST_ATTEMPT'` thuộc nhân viên / `team_at_obligation` xác thực.
  - Scope `company` / `campaign` (`WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`): `DISTINCT work_item` có nghĩa vụ `FIRST_ATTEMPT` gốc kết thúc hạn hoặc hoàn thành trong kỳ (đo trải nghiệm khách hàng theo `original_due_at`).
- **Cohort & Time basis:** Các nghĩa vụ đủ điều kiện có mốc chốt đánh giá `evaluation_anchor_at = COALESCE(fulfilled_at, breached_at, closed_at)` nằm trong `[period_start, period_end]` (theo `Asia/Ho_Chi_Minh`) và `<= as_of`, đã đến hạn hoặc đã hoàn thành.
- **Tử số (`numerator`):** Số nghĩa vụ có `status = 'fulfilled_on_time'`.
- **Mẫu số (`denominator`):** Số nghĩa vụ có `status IN ('fulfilled_on_time', 'fulfilled_late', 'breached_open', 'breached_handoff')`.
- **Loại trừ (`exclusions`):**
  - `open` nhưng chưa đến hạn tại `as_of` (`effective_due_at > as_of`);
  - `waived` hợp lệ có thẩm quyền;
  - `canceled_validly` trước hạn (ví dụ khách hàng yêu cầu `DNC` trước `effective_due_at`, hoặc thu hồi hành chính trước hạn khi chưa vi phạm);
  - `handoff_closed` trước hạn (nhân viên A được chuyển giao sang B khi vẫn còn hạn hợp lệ và chưa quá hạn);
  - Ở scope `team`: loại trừ các dòng có `attribution_status = 'historical_attribution_unknown'` (đếm riêng trong `exclusion_counts.historical_attribution_unknown`).
- **Nguồn dữ liệu:** `tabCSKH SLA Obligation`, `tabCSKH Assignment History`.
- **Test đối chứng:** `T-KPI-FIRST-ATTEMPT-01`, `T-SLA-ABA-03`, `T-SLA-HANDOFF-BEFORE-BREACH-04`.

### 2.2 `FOLLOWUP_SLA_RATE` — Tỷ lệ đạt SLA lịch hẹn gọi lại (`Callback`)
- **Đơn vị:** `percent` (`0.00` – `100.00`)
- **Grain:** Các `CSKH SLA Obligation` có `obligation_type = 'FOLLOWUP'` gắn với `CRM Task` (`cskh_is_followup = 1`).
- **Cohort & Time basis:** Các nghĩa vụ `FOLLOWUP` hoàn thành hoặc quá hạn trong `[period_start, period_end]` (`<= as_of`).
- **Tử số (`numerator`):** Số nghĩa vụ `FOLLOWUP` có `status = 'fulfilled_on_time'` (được giải quyết bằng một effective `CSKH Contact Attempt` hợp lệ tại `attempt_at <= effective_due_at`).
- **Mẫu số (`denominator`):** Số nghĩa vụ `FOLLOWUP` có `status IN ('fulfilled_on_time', 'fulfilled_late', 'breached_open', 'breached_handoff')`.
- **Quy tắc bất biến khi dời lịch (`reschedule`):**
  - Dời lịch **trước khi quá hạn** (trong giới hạn cho phép hoặc có duyệt của Leader): nghĩa vụ hiện tại chuyển sang `superseded` (loại khỏi mẫu số vi phạm), mở nghĩa vụ `FOLLOWUP` mới theo hạn mới.
  - Dời lịch **sau khi đã quá hạn** (`now > effective_due_at`): nghĩa vụ cũ giữ nguyên vi phạm (`breached_open` / `fulfilled_late`) trong mẫu số; lịch hẹn mới sinh nghĩa vụ theo dõi tiếp theo nhưng **không xóa vi phạm cũ**.
- **Loại trừ (`exclusions`):** `open` chưa đến hạn, `superseded` hợp lệ trước hạn, `waived`, `canceled_validly` (ví dụ `DNC` trước hạn), `handoff_closed` trước hạn, `historical_attribution_unknown` (khi lọc theo team).
- **Nguồn dữ liệu:** `tabCSKH SLA Obligation`, `tabCRM Task`, `tabCSKH Contact Attempt`.
- **Test đối chứng:** `T-KPI-FOLLOWUP-02`, `T-SLA-RESCHEDULE-AFTER-BREACH-06`.

### 2.3 `FIRST_CONNECTED_TRACKING_RATE` — Tỷ lệ kết nối thành công trong ngưỡng theo dõi (`Informational Only`)
- **Đơn vị:** `percent` (`0.00` – `100.00`) — **Chỉ số theo dõi chiến dịch/khung giờ, KHÔNG dùng để phạt nhân viên hay tính vào `PROCESS_SCORE` mặc định.**
- **Grain:** `CSKH SLA Obligation` có `obligation_type = 'FIRST_CONNECTED'` (theo dõi cấp `Work Item` / `Campaign` / `Team`).
- **Cohort & Time basis:** Các nghĩa vụ `FIRST_CONNECTED` đến mốc theo dõi hoặc đã có kết nối đầu tiên trong `[period_start, period_end]`.
- **Tử số (`numerator`):** Số nghĩa vụ có effective attempt đầu tiên đạt `is_connected = 1` tại `attempt_at <= effective_due_at`.
- **Mẫu số (`denominator`):** Tổng số nghĩa vụ `FIRST_CONNECTED` đã đạt kết nối hoặc đã qua mốc theo dõi (`status IN ('fulfilled_on_time', 'fulfilled_late', 'breached_open')`).
- **Quy tắc nghiệp vụ:** Khách hàng không nghe máy không phải lỗi quy trình của nhân viên nếu nhân viên đã gọi đúng hạn `FIRST_ATTEMPT`; do đó chỉ số này gắn cờ `"informational_only": true`.
- **Nguồn dữ liệu:** `tabCSKH SLA Obligation`, `tabCSKH Contact Attempt`.
- **Test đối chứng:** `T-KPI-FIRST-CONNECTED-INFO-03`.

### 2.4 `MIN_ATTEMPTS_COMPLIANCE_RATE` — Tỷ lệ tuân thủ số lần liên hệ tối thiểu (Chỉ kích hoạt theo Policy)
- **Đơn vị:** `percent` (`0.00` – `100.00`)
- **Điều kiện kích hoạt:** Chỉ sinh nghĩa vụ khi `CSKH Policy.min_attempts_enabled = 1` và `min_attempts_before_close > 0`. Khi tắt (mặc định `0`), mẫu số bằng `0` -> trả về `N/A` (`status: "insufficient_data", reason: "policy_disabled_or_no_eligible_records"`).
- **Grain:** `CSKH SLA Obligation` có `obligation_type = 'MIN_ATTEMPTS'` trên các Work Item đóng với lý do không kết nối được (`unreachable` / `no_answer_exhausted`).
- **Tử số (`numerator`):** Số nghĩa vụ đủ số lượng effective attempts hợp lệ theo khoảng cách tối thiểu quy định trước khi đóng.
- **Mẫu số (`denominator`):** Tổng số nghĩa vụ `MIN_ATTEMPTS` phát sinh trong kỳ.
- **Loại trừ:** Work Item đóng do khách hàng `DNC`, số điện thoại không tồn tại (`invalid_number` có xác nhận), hoặc khách hàng đã kết nối thành công.
- **Test đối chứng:** `T-KPI-MIN-ATTEMPTS-POLICY-04`.

### 2.5 `CONTACT_ATTEMPT_COUNT` & `CONNECTED_ATTEMPT_COUNT` & `CONNECTED_RATE`
- **`CONTACT_ATTEMPT_COUNT` (`count`):** Tổng số effective `CSKH Contact Attempt` (`effective_status != 'voided'`) có `attempt_at` trong `[period_start, period_end]` (`<= as_of`). Không bao giờ trả `N/A` cho bộ đếm nguyên (trả `0` khi không có attempt), đồng thời trả kèm phân rã `{attempted, connected, self_reported, verified, disputed}`.
- **`CONNECTED_ATTEMPT_COUNT` (`count`):** Tổng số effective `CSKH Contact Attempt` có `effective_is_connected = 1` trong `[period_start, period_end]`.
- **`CONNECTED_RATE` (`percent`):**
  - `numerator = CONNECTED_ATTEMPT_COUNT`
  - `denominator = CONTACT_ATTEMPT_COUNT`
  - Nếu `CONTACT_ATTEMPT_COUNT == 0` -> `value: null, status: "insufficient_data", reason: "no_eligible_records"`.
- **Nguồn dữ liệu:** Projection effective attempts từ `tabCSKH Contact Attempt` + `tabCSKH Attempt Correction`.
- **Test đối chứng:** `T-KPI-ATTEMPTS-CORRECTION-05` (kiểm chứng `CRM Call Log` không bị cộng trùng và `void` attempt bị trừ chính xác).

### 2.6 `WORK_ITEMS_TOUCHED` — Số lượng hồ sơ công việc đã tác nghiệp
- **Đơn vị:** `count`
- **Grain & Khác biệt cộng tính:**
  - Scope `company` / `campaign`: `COUNT(DISTINCT work_item)` có ít nhất 1 effective attempt trong `[period_start, period_end]`.
  - Scope `employee`: `COUNT(DISTINCT work_item)` mà nhân viên đó thực hiện effective attempt trong kỳ (kèm trường phụ `intervals_touched` đếm số `ownership_interval` đã tác nghiệp để phân biệt `A -> B -> A`).
  - Scope `team`: `COUNT(DISTINCT work_item)` có effective attempt được quy thuộc xác thực cho `team` trong kỳ.
- **Test đối chứng:** `T-KPI-DISTINCT-WI-VS-INTERVAL-06`.

### 2.7 `DATA_HYGIENE_SCORE` / `VALID_OUTCOME_LOGGING_RATE` — Tỷ lệ ghi nhận kết quả chuẩn xác (Không bị void/sửa lỗi do sai sót)
- **Đơn vị:** `percent` (`0.00` – `100.00`)
- **Cohort:** Toàn bộ `CSKH Contact Attempt` gốc phát sinh trong `[period_start, period_end]` của scope tương ứng.
- **Tử số (`numerator`):** Số attempt hợp lệ có đầy đủ outcome + ghi chú theo policy và **không** bị `approved` correction loại `void` hoặc `correct` do lỗi nhập liệu của nhân viên.
- **Mẫu số (`denominator`):** Tổng số `CSKH Contact Attempt` gốc phát sinh trong kỳ của scope đó.
- **Chỉ số phụ đi kèm:**
  - `CORRECTION_RATE = approved_corrected_or_voided_attempts / raw_attempts * 100`
  - `VOID_ATTEMPT_RATE = approved_voided_attempts / raw_attempts * 100`
- **Test đối chứng:** `T-KPI-HYGIENE-07`.

### 2.8 `CALLBACK_COMPLETION_RATE` & `RESCHEDULE_RATE` — Kỷ luật xử lý lịch hẹn
- **`CALLBACK_COMPLETION_RATE` (`percent`):**
  - **Cohort:** Các nghĩa vụ/nhiệm vụ callback đến hạn hoặc hoàn thành trong kỳ (loại trừ `waived`, `canceled_validly`, `handoff_closed` trước hạn).
  - **Tử số:** Số callback được giải quyết bằng một effective `CSKH Contact Attempt` hợp lệ (`fulfilled_on_time` hoặc `fulfilled_late`), không bị bỏ rơi quá hạn mở (`breached_open`) hay hủy tùy tiện.
  - **Mẫu số:** Tổng số callback đến hạn hoặc đã kết thúc xử lý trong kỳ.
- **`RESCHEDULE_RATE` (`percent`):** Số lần dời lịch (`reschedule_events`) chia cho tổng số callback tạo mới hoặc hoạt động trong kỳ (chỉ số giám sát chống lạm dụng dời lịch).
- **Test đối chứng:** `T-KPI-CALLBACK-DISCIPLINE-08`.

### 2.9 `DNC_COMPLIANCE_RATE` — Tỷ lệ tuân thủ Do-Not-Contact & quy trình đóng hồ sơ
- **Đơn vị:** `percent` (`0.00` – `100.00`)
- **Cohort:** Các khoảng trách nhiệm (`ownership_interval`) có phát sinh tác nghiệp hoặc quyết định đóng/DNC trong `[period_start, period_end]`.
- **Tử số (`numerator`):** Số khoảng trách nhiệm tuân thủ đúng quy tắc DNC và quy tắc đóng hồ sơ (0 cuộc gọi chủ động trái phép sau thời điểm cờ `DNC` có hiệu lực, 0 thao tác đóng thiếu điều kiện bắt buộc).
- **Mẫu số (`denominator`):** Tổng số khoảng trách nhiệm được đánh giá trong kỳ.
- **Test đối chứng:** `T-KPI-DNC-COMPLIANCE-09`.

### 2.10 `QA_PASS_RATE` — Tỷ lệ đạt kiểm định chất lượng QA (Mặc định tắt `qa_enabled = 0`)
- **Đơn vị:** `percent` (`0.00` – `100.00`)
- **Trạng thái mặc định trong P3:** `qa_enabled = false` (`0`).
  - Khi `qa_enabled = false`: Hệ thống **luôn** trả `value: null, numerator: 0, denominator: 0, status: "disabled", reason: "qa_disabled_by_policy"`. Tuyệt đối không sinh review giả và không chấm `0%`.
  - Khi `qa_enabled = true` (trong kịch bản test tương lai): `numerator = SUM(qa_weighted_points_achieved)`, `denominator = SUM(qa_max_points)` trên các `CSKH QA Review` có `status = 'published'` trong kỳ; nếu số mẫu `< min_qa_sample_size` (mặc định `5`) thì trả `status: "insufficient_data", reason: "qa_sample_below_minimum"`.
- **Test đối chứng:** `T-KPI-QA-DISABLED-10`.

---

## 3. Công thức `PROCESS_SCORE` (`0–100`) & Golden Example bắt buộc

### 3.1 Thành phần & Trọng số mặc định (`DEV / configurable`)

| Mã thành phần (`component_code`) | Chỉ số đầu vào (`r_i`, thang `0..100`) | Trọng số cấu hình mặc định (`w_i`) | Điều kiện hợp lệ của thành phần |
|---|---|---:|---|
| `C1_FIRST_ATTEMPT_SLA` | `FIRST_ATTEMPT_SLA_RATE` | `25` | `denominator > 0` |
| `C2_FOLLOWUP_SLA` | `FOLLOWUP_SLA_RATE` | `20` | `denominator > 0` |
| `C3_DATA_HYGIENE` | `VALID_OUTCOME_LOGGING_RATE` | `20` | `denominator > 0` |
| `C4_CALLBACK_DNC_DISCIPLINE` | Tổ hợp tỷ lệ hoàn thành callback hợp lệ & tuân thủ DNC (tính trên các sự kiện đủ điều kiện; mặc định `CALLBACK_DISCIPLINE_RATE` trừ điểm phạt vi phạm DNC nếu có) | `20` | `denominator > 0` |
| `C5_QA_QUALITY` | `QA_PASS_RATE` | `15` | Chỉ hợp lệ khi `qa_enabled == true` **và** `qa_sample_count >= min_qa_sample_size` |

### 3.2 Quy tắc tổng hợp chuẩn hóa lại trọng số (`Re-weighted Provisional Score`)

Gọi $V$ là tập các thành phần có dữ liệu hợp lệ (`status == "ok"`), và $W_{\text{valid}} = \sum_{i \in V} w_i$:

1. **Ngưỡng đủ dữ liệu tối thiểu (`Data Sufficiency Gate`):**
   - Nếu tổng số sự kiện SLA/tác nghiệp đủ điều kiện nhỏ hơn ngưỡng tối thiểu cấu hình (`min_eligible_events_for_score`, mặc định `DEV = 1` cho kiểm thử đơn vị hoặc theo cấu hình policy, ví dụ `3` trên bảng xếp hạng) **hoặc** $W_{\text{valid}} < W_{\min}$ (`min_valid_score_weight`, mặc định `DEV = 60`):
     - Trả về `value: null`, `status: "insufficient_data"`, `reason: "insufficient_valid_weight_or_sample"`.
2. **Khi tất cả thành phần (kể cả QA) đều hợp lệ ($W_{\text{valid}} = 100$):**
   $$\text{PROCESS\_SCORE} = \text{ROUND}\left(\frac{\sum_{i \in V} w_i \cdot r_i}{100}, 2\right), \quad \text{status} = \text{"final"}$$
3. **Khi `qa_enabled = false` hoặc chưa đủ mẫu QA ($C_5 \notin V$, nhưng $W_{\text{valid}} \ge W_{\min}$):**
   - **TUYỆT ĐỐI KHÔNG** gán $r_5 = 0$.
   - Tính điểm chuẩn hóa trên tổng trọng số hợp lệ:
     $$\text{PROCESS\_SCORE} = \text{ROUND}\left(\frac{\sum_{i \in V} w_i \cdot r_i}{\sum_{i \in V} w_i}, 2\right), \quad \text{status} = \text{"provisional"}, \quad \text{missing\_components} = [\text{"C5\_QA\_QUALITY"}]$$

### 3.3 Golden Example bắt buộc (`7350 / 85 = 86.47`)

Giả sử `qa_enabled = false` (thành phần `C5_QA_QUALITY` có trọng số `15` không tham gia chấm điểm) và 4 thành phần `C1..C4` đạt:
- `C1_FIRST_ATTEMPT_SLA`: $r_1 = 90.0$, $w_1 = 25 \Rightarrow 25 \times 90 = 2250$
- `C2_FOLLOWUP_SLA`: $r_2 = 80.0$, $w_2 = 20 \Rightarrow 20 \times 80 = 1600$
- `C3_DATA_HYGIENE`: $r_3 = 100.0$, $w_3 = 20 \Rightarrow 20 \times 100 = 2000$
- `C4_CALLBACK_DNC_DISCIPLINE`: $r_4 = 75.0$, $w_4 = 20 \Rightarrow 20 \times 75 = 1500$

Khi đó:
- Tổng tử số trọng số: $2250 + 1600 + 2000 + 1500 = 7350$
- Tổng trọng số hợp lệ: $W_{\text{valid}} = 25 + 20 + 20 + 20 = 85$ ($\ge 60$)
- Điểm quy trình:
  $$\text{PROCESS\_SCORE} = \text{ROUND}\left(\frac{7350}{85}, 2\right) = \text{ROUND}(86.470588\dots, 2) = \mathbf{86.47}$$
- Metadata trả kèm:
  ```json
  {
    "metric_code": "PROCESS_SCORE",
    "value": 86.47,
    "numerator": 7350.0,
    "denominator": 85.0,
    "unit": "score_100",
    "status": "provisional",
    "reason": "qa_component_excluded_reweighted",
    "valid_weight": 85,
    "total_configured_weight": 100,
    "missing_components": ["C5_QA_QUALITY"]
  }
  ```
- **Test đối chứng:** `T-SCORE-GOLDEN-8647`.
