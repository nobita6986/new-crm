# Quy Tắc Chấm Điểm Quy Trình, Đủ Dữ Liệu & Khử Nhiễu (`docs/p3/scoring-and-data-sufficiency.md`)

> **Tuyên bố cấu hình (`DEV / configurable`):** Các trọng số (`25/20/20/20/15`), ngưỡng trọng số hợp lệ tối thiểu (`min_valid_score_weight = 60`) và kích thước mẫu tối thiểu (`min_eligible_events_for_score`, `min_qa_sample_size = 5`) là thông số kỹ thuật mặc định trên môi trường DEV, có thể điều chỉnh trong `CSKH Policy` khi BOD ban hành quy chế chính thức.

---

## 1. Ba trạng thái kết quả chuẩn hóa của mọi KPI & Điểm số

Mỗi ô chỉ số trong `CSKH KPI Snapshot` và API response đều mang trường `status` thuộc tập đóng:

| Giá trị `status` | Ý nghĩa nghiệp vụ | Giá trị `value` | Khi nào xuất hiện |
|---|---|---|---|
| `"ok"` / `"final"` | Đủ dữ liệu và đầy đủ toàn bộ thành phần cấu hình | `float` (`0.00..100.00`) hoặc `int` | `denominator > 0` (với KPI đơn); hoặc tất cả thành phần bao gồm QA đều hợp lệ ($W_{\text{valid}} = 100$) với `PROCESS_SCORE`. |
| `"provisional"` | Điểm tạm tính có chuẩn hóa lại trọng số do thiếu thành phần chưa bật/chưa đủ mẫu (ví dụ `qa_enabled = false`) | `float` (`0.00..100.00`) | `PROCESS_SCORE` có $W_{\text{valid}} \ge \text{min\_valid\_score\_weight}$ (ví dụ `85 >= 60`) nhưng thiếu ít nhất 1 thành phần cấu hình (như `C5_QA_QUALITY`). |
| `"insufficient_data"` | Không đủ mẫu hoặc mẫu số bằng `0` (`N/A`) | `null` | `denominator == 0`; hoặc tổng trọng số hợp lệ $W_{\text{valid}} < \text{min\_valid\_score\_weight}$; hoặc tổng sự kiện đủ điều kiện nhỏ hơn ngưỡng tối thiểu. |
| `"disabled"` | Chỉ số thuộc module đang tắt theo chính sách | `null` | `QA_PASS_RATE` khi `qa_enabled == false`, hoặc `MIN_ATTEMPTS_COMPLIANCE_RATE` khi `min_attempts_enabled == false`. |

---

## 2. Chống Sai Lệch Mẫu Nhỏ & Bảng Xếp Hạng

1. **Nhân viên mới nhận việc hoặc nghỉ phép (`0` nghĩa vụ đến hạn trong kỳ):**
   - `FIRST_ATTEMPT_SLA_RATE` trả `value: null, status: "insufficient_data", reason: "no_eligible_records"`.
   - Không bao giờ xếp nhân viên có `0/0` vào nhóm `0%` (cuối bảng) hay `100%` (đầu bảng). Khi sort theo điểm/tỷ lệ trên API báo cáo, các dòng `value IS NULL` luôn được xếp tách riêng ở cuối danh sách (`NULLS LAST`) kèm nhãn `N/A (Chưa đủ dữ liệu)`.
2. **Bảo vệ khi thiếu `FOLLOWUP` trong kỳ nhưng có `FIRST_ATTEMPT`:**
   - Nếu một nhân viên trong ngày chỉ có `FIRST_ATTEMPT` (`w_1 = 25`), `DATA_HYGIENE` (`w_3 = 20`) và `DNC_DISCIPLINE` (`w_4 = 20`) mà không phát sinh lịch hẹn `FOLLOWUP` nào đến hạn (`C2` có `denominator == 0`), thì $W_{\text{valid}} = 25 + 20 + 20 = 65 \ge 60$ -> hệ thống tự động chuẩn hóa trên mẫu số `65` với `status = "provisional"`, `missing_components = ["C2_FOLLOWUP_SLA", "C5_QA_QUALITY"]`.
   - Ngược lại, nếu chỉ có duy nhất 1 thành phần hợp lệ với trọng số `< 60` (ví dụ chỉ có `C3_DATA_HYGIENE = 20 < 60`), hệ thống trả `value: null, status: "insufficient_data"` thay vì phóng đại 1 thành phần phụ thành điểm tổng 100.

---

## 3. Tính Toán Lại Khi Có Đính Chính Kỳ Cũ (`Prior-Period Restatement`)

1. Các bản ghi `CSKH KPI Run` và `CSKH KPI Snapshot` là **append-only** (không ghi đè lịch sử).
2. Khi một `CSKH Attempt Correction` được Leader/Admin phê duyệt vào ngày `05/05` cho một `CSKH Contact Attempt` diễn ra ngày `28/04`:
   - Hook `frappe.db.after_commit` đẩy phạm vi `(period = 2026-04-28, month = 2026-04, employee, team, campaign, company)` vào `CSKH Dirty Scope` với cờ `reason = "prior_period_correction"`.
   - Worker `jobs/recompute_kpi.py` tạo một `CSKH KPI Run` mới có:
     - `run_mode = "restated"`
     - `restated_from_run = <run_id_cũ>`
     - `trigger_reference = <attempt_correction_name>`
     - `as_of = <thời điểm chạy recompute>`
   - Chuyển cờ `is_latest_published = 1` từ `run_id` cũ sang `run_id` mới một cách nguyên tử trong 1 transaction DB. Cả hai phiên bản đều còn nguyên để kiểm toán chênh lệch trước/sau đính chính.
