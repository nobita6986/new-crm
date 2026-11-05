# Quy Tắc Chấm Điểm Quy Trình, Đủ Dữ Liệu & Khử Nhiễu (`docs/p3/scoring-and-data-sufficiency.md`)

> **Tuyên bố cấu hình (`DEV / configurable`):** Các trọng số (`25/20/20/20/15`), ngưỡng trọng số hợp lệ tối thiểu (`min_valid_score_weight = 60`), thời gian phơi sáng tối thiểu (`min_exposure_business_minutes = 60`) và cỡ mẫu QA tối thiểu (`min_qa_sample_size = 5`) là thông số kỹ thuật mặc định trên môi trường DEV, điều chỉnh qua `CSKH Policy` khi BOD phê duyệt quy chế vận hành chính thức.

---

## 1. Năm Thành Phần Chuẩn Của `PROCESS_SCORE` & Chuẩn Thang `0–100` (`F-P3-03`, `F-P3-09`)

Theo `docs/root-plan/P3.md` §7.5, `PROCESS_SCORE` gồm 5 thành phần:
1. `C1_FIRST_ATTEMPT_SLA` (`25%`) — `FIRST_ATTEMPT_SLA_RATE`
2. `C2_ELIGIBLE_COVERAGE` (`20%`) — `ELIGIBLE_COVERAGE`
3. `C3_DATA_HYGIENE` (`20%`) — `VALID_OUTCOME_LOGGING_RATE`
4. `C4_FOLLOWUP_SLA` (`20%`) — `FOLLOWUP_SLA_RATE`
5. `C5_QA_QUALITY` (`15%`) — `QA_PASS_RATE` (chỉ tính mẫu `cohort_type = "random_sample"` khi `qa_enabled = true` và `sample_count >= min_qa_sample_size`)

Mọi dòng trong `score_components` đều công bố rõ đơn vị đóng góp trên thang `0–100`:
- `weighted_points`: `(weight * rate) / 100` (luôn nằm trong đoạn `[0, weight]`, tuyệt đối không phình gấp `100x`).
- `reweighted_points`: `(weight * rate) / valid_weight` (tổng đóng góp sau chuẩn hóa bằng đúng `PROCESS_SCORE.value`).
- `raw_weight_rate_product`: `weight * rate` (phục vụ đối soát toán học `7350 / 85 = 86.47`).

---

## 2. Trạng Thái Kết Quả & Chống Sai Lệch Mẫu Nhỏ

| Giá trị `status` | Ý nghĩa nghiệp vụ | Giá trị `value` | Khi nào xuất hiện |
|---|---|---|---|
| `"ok"` / `"final"` | Đủ dữ liệu và hội đủ cả 5 thành phần cấu hình (bao gồm QA `>= min_qa_sample_size`) | `float` (`0.00..100.00`) | `valid_weight == 100` và đủ cỡ mẫu sự kiện hợp lệ. |
| `"provisional"` | Điểm tạm tính chuẩn hóa trên `valid_weight` do thiếu thành phần chưa bật/chưa đủ mẫu | `float` (`0.00..100.00`) | `valid_weight >= min_valid_score_weight` (ví dụ `85 >= 60` khi tắt QA, hoặc `65 >= 60` khi chiến dịch chưa có lịch hẹn `FOLLOWUP`) nhưng còn thành phần thiếu mẫu. |
| `"insufficient_data"` | Không đủ dữ liệu để công bố điểm tổng hay xếp hạng | `null` | `valid_weight < min_valid_score_weight` (ví dụ nhân viên `staff.b2` có `0` hoạt động đủ điều kiện -> `valid_weight = 0 < 60`) hoặc tổng sự kiện đủ điều kiện `< min_eligible_events_for_score`. |
| `"disabled"` | Chỉ số đơn thuộc tính năng đang tắt theo policy | `null` | `QA_PASS_RATE` khi `qa_enabled == false`, `MIN_ATTEMPTS_COMPLIANCE_RATE` khi `min_attempts_enabled == false`. |

- Khi sắp xếp bảng nhân viên/đội nhóm theo điểm số hoặc tỷ lệ, các dòng `value == null` (`insufficient_data`) luôn được xếp ở cuối (`NULLS LAST`) và hiển thị `N/A`, không bị biến thành `0%` hay `100%`.
- Với QA Review: mẫu kiểm tra chủ đích theo rủi ro (`cohort_type = "risk_targeted"`) được thống kê riêng trong `qa_cohort_breakdown`, không trộn vào `QA_PASS_RATE` của `PROCESS_SCORE` để tránh thiên lệch phạt các hồ sơ khó.

---

## 3. Nâng Cấp Công Thức (`metric_version`) & Restatement Append-Only (`F-P3-06`)

1. `CSKH KPI Run` và `CSKH KPI Snapshot` là **bất biến (append-only)** — controller chặn mọi thao tác sửa trực tiếp hoặc xóa dòng cũ.
2. Khi nâng cấp công thức từ `"cskh-metrics-v1"` lên `"cskh-metrics-v2"` hoặc khi có `CSKH Attempt Correction` / `CSKH QA Review` duyệt cho kỳ đã chốt:
   - Migration patch (`p3_schema_and_seeds.py` -> `invalidate_stale_metric_version_snapshots()`) và tầng truy vấn báo cáo tự động ghi nhận `CSKH Dirty Scope` với `reason = "metric_version_upgrade"` / `"prior_period_correction"`, **không bao giờ sửa đè snapshot `v1` cũ**.
   - Tiến trình recompute sinh một `CSKH KPI Run` mới mang `metric_version = "cskh-metrics-v2"`, `run_mode = "restated"`, `restated_from_run = <run_cũ>`, ghi bộ snapshot mới và chuyển con trỏ `is_latest_published = 1` một cách nguyên tử.
