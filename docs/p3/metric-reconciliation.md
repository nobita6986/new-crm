# Đặc Tả Đối Soát Chỉ Số & Bất Biến Bảo Toàn Số Học (`docs/p3/metric-reconciliation.md`)

Tài liệu này mô tả hệ thống bất biến đối soát (`Reconciliation Gate`) trong `crm_cskh/services/kpi_calculator.py::verify_snapshot_reconciliation` và job kiểm toán độc lập `crm_cskh/jobs/reconcile_metrics.py::reconcile_published_period` (`P3-04`, `P3-05`, `docs/root-plan/P3.md` §9.5).

---

## 1. Mục Tiêu & Nguyên Tắc `Fail-Closed`

Trước khi bất kỳ tiến trình `execute_kpi_recompute` nào được phép chuyển con trỏ `is_latest_published = 1` sang bộ `CSKH KPI Snapshot` mới, hàm `verify_snapshot_reconciliation` được thực thi bắt buộc trên toàn bộ tập bản chụp vừa tính. Nếu bất kỳ bất biến nào dưới đây bị vi phạm:
1. Giao dịch cơ sở dữ liệu được `ROLLBACK` ngay lập tức;
2. Bản ghi `CSKH KPI Run` mới được ghi nhận `status = "failed"`, `reconciliation_ok = 0` kèm chi tiết sai lệch tại `error_message`;
3. Bản chụp `is_latest_published = 1` hợp lệ gần nhất trước đó **được giữ nguyên 100%** để người dùng dashboard không bao giờ nhìn thấy số liệu sai lệch;
4. Ngoại lệ nghiệp vụ `DataReconciliationFailed` (`422 DATA_RECONCILIATION_FAILED`) được phát ra rõ ràng.

---

## 2. Năm Nhóm Bất Biến Đối Soát Cốt Lõi

### Bất biến R1 — Chống Chia Cho `0` & Ràng Buộc Biên Giá Trị (`Range & Zero-Denominator Guard`)
Với mọi `CSKH KPI Snapshot` thuộc các cấp `company`, `team`, `employee`, `campaign` và mọi mã chỉ số trong `cskh-metrics-v1`:
- Nếu `unit == "percent"` và `denominator == 0`: bắt buộc `value is None` và `status in ("insufficient_data", "disabled")` (tuyệt đối không sinh `0.0` hay `100.0` giả).
- Nếu `unit == "percent"` và `value is not None`: bắt buộc `0.0 <= value <= 100.0`.
- Nếu `unit == "score_100"` (`PROCESS_SCORE`) và `value is not None`: bắt buộc `0.0 <= value <= 100.0`.

### Bất biến R2 — Tính Trung Thực Khi Tắt QA (`Zero Fake QA / Verified Guard`)
Khi cấu hình chính sách hiệu lực có `qa_enabled == false` (mặc định P3):
- Mọi snapshot phải có `metrics["QA_PASS_RATE"]["status"] == "disabled"` và `value is None`;
- Mọi `attempt_breakdown["verified"] == 0` (không tự động gắn nhãn `verified` cho dữ liệu nhân viên tự báo cáo);
- `PROCESS_SCORE` có `missing_components` chứa `"C5_QA_QUALITY"` và không bao giờ ở trạng thái `"final"`.

### Bất biến R3 — Bảo Toàn Cộng Tuyến Tính Giữa `Employee`, `Team` và `Company`
Vì mọi sự kiện hợp lệ đều có `actor_user` / `owner_user` xác định, và mọi sự kiện không xác định được nhánh lịch sử đều được phân loại minh bạch vào nhóm `__unattributed_historical_team__`:
- Tổng `CONTACT_ATTEMPT_COUNT` của toàn bộ `employee` snapshots == `CONTACT_ATTEMPT_COUNT` của `company` snapshot;
- Tổng `CONNECTED_ATTEMPT_COUNT` của toàn bộ `employee` snapshots == `CONNECTED_ATTEMPT_COUNT` của `company` snapshot;
- Tổng `CONTACT_ATTEMPT_COUNT` của toàn bộ `team` snapshots (bao gồm `__unattributed_historical_team__` nếu có) == `CONTACT_ATTEMPT_COUNT` của `company` snapshot;
- Tổng `(numerator, denominator)` của `FIRST_ATTEMPT_SLA_RATE` trên toàn bộ `employee` snapshots == `(numerator, denominator)` của `FIRST_ATTEMPT_SLA_RATE` trên `company` snapshot;
- Tổng `(numerator, denominator)` của `FIRST_ATTEMPT_SLA_RATE` trên toàn bộ `team` snapshots == `(numerator, denominator)` của `FIRST_ATTEMPT_SLA_RATE` trên `company` snapshot.

### Bất biến R4 — Không Trung Bình Cộng Phần Trăm Khi Gộp Cấp (`Non-Averaging Rollup`)
Khi gộp từ `Employee -> Team -> Company` hoặc từ `Daily -> Weekly -> Monthly -> Quarterly -> Yearly`:
- Tỷ lệ cấp trên luôn được tính trực tiếp từ tổng tử số chia tổng mẫu số gốc (ví dụ nhân viên A `1/1 = 100%` và nhân viên B `5/10 = 50%` cho ra tỷ lệ nhóm `(1+5)/(1+10) = 6/11 = 54.55%`, tuyệt đối không lấy trung bình `(100% + 50%)/2 = 75%`).
- Các chỉ số đếm tập hợp (`WORK_ITEMS_TOUCHED`, `distinct_work_items`, `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`) luôn tính bằng tập `DISTINCT work_item` trên cửa sổ kỳ gộp (ví dụ 1 hồ sơ được gọi ở 3 ngày khác nhau trong tháng có tổng theo ngày là `1+1+1 = 3`, nhưng bản chụp tháng ghi nhận chính xác `1` hồ sơ duy nhất).

### Bất biến R5 — Phân Biệt `DISTINCT Work Item` và `Ownership Interval` (`A -> B -> A`)
Khi một `CSKH Work Item` được bàn giao qua chuỗi `Staff A -> Staff B -> Staff A`:
- Cấp công ty và chiến dịch ghi nhận `distinct_work_items = 1` nhưng `ownership_intervals_count = 3`;
- Chỉ số góc nhìn khách hàng `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` có mẫu số `1` (`DISTINCT work_item`), trong khi chỉ số góc nhìn trách nhiệm vận hành `FIRST_ATTEMPT_SLA_RATE` đánh giá độc lập từng khoảng sở hữu đủ điều kiện.

---

## 3. Kiểm Toán Độc Lập Định Kỳ (`jobs/reconcile_metrics.py`)

Hàm `crm_cskh.jobs.reconcile_metrics.reconcile_published_period` đọc bản chụp đang có `is_latest_published = 1` và tính lại trực tiếp từ bảng giao dịch gốc tại đúng mốc `run.as_of`, đối chiếu từng chỉ số trên mọi scope để phát hiện sớm mọi sai lệch lưu trữ.
