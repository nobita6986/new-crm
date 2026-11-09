# Báo Cáo Đo Kiểm Hiệu Năng, Chịu Tải & Khuyến Nghị Dung Lượng Môi Trường Cô Lập (`P4-06` — Cổng `G18`)

- **Mã tài liệu:** `P4-PERFORMANCE-REPORT` (`docs/p4/performance-report.md` — `P4.md §3.3`, `§9`, `§20.2`)
- **Bằng chứng JSON gốc đã kiểm chứng mã băm:** [`deploy/ops/evidence/p4-candidate-and-s1-20261109/benchmark-s1-report.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/benchmark-s1-report.json) (GitHub Actions Run `42625295772` trên repo `nobita6986/new-crm`, hoàn tất `2026-11-09T13:53:15Z`)
- **Bộ sinh dữ liệu & Trình đo tải:** [`deploy/release/run_isolated_benchmark.py`](../../deploy/release/run_isolated_benchmark.py)
- **Image bất biến được đo (đúng candidate đã qua kiểm thử `421/421` JUnit):**
  - Registry Manifest Digest: `ghcr.io/nobita6986/crm-cskh@sha256:b1329ef7c26a9861a07d25d425eb4438150d065d2548c464e7d3395e568a201d`
  - OCI Config / Local Image ID: `sha256:9f295546fc78c061d950322b9c8f60d40312016b802f325b15f834524f500829`
  - Commit mã nguồn: `427af4834eda48a15ca86c15e88342c3c40d5f2f` (`apps_tree_sha = 14b5387296dad815a6b9f663411f265fa7d9f910`)

> [!IMPORTANT]
> **Định khung quản trị bắt buộc (`P4.md §9.1`, `§9.2` & Chỉ đạo `T0`):**
> 1. **Tập dữ liệu `S1` (`50.000` Hồ sơ liên hệ / `75.000` Đầu việc / `250.000` Lần liên hệ / `150.000` Nghĩa vụ SLA / `40` nhân sự active) là tập dữ liệu tổng hợp gợi ý để đo hiệu năng/dung lượng trên môi trường cô lập (`SUGGESTED_S1_DATASET_NOT_MONTHLY_VOLUME_COMMITMENT`) — KHÔNG phải cam kết sản lượng `50.000 hồ sơ/tháng` và KHÔNG chứa bất kỳ dữ liệu cá nhân (PII) thật nào.**
> 2. **Các ngưỡng độ trễ mục tiêu là đề xuất SLO nội bộ (`UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL`), KHÔNG phải SLA sản xuất đã ký cam kết.**
> 3. **Tuyệt đối không bắn tải vào VPS đang phục vụ của Owner (`https://crm.hrpartner.vn` / `103.77.208.186`):** `isolation_guard` xác nhận `verified_isolated = true`, `owner_vps_touched = false`, chạy hoàn toàn trên stack cô lập `cskh-p4-bench-e47f6bbe` (`127.0.0.1`, site `p4-s1-bench.localhost`).

---

## 1. Cấu Hình Môi Trường Đo & Tập Dữ Liệu Tổng Hợp `S1` (`P4.md §9.1`, `§9.3`)

### 1.1. Phần cứng & Topology Container Cô lập
- **Máy chạy đo kiểm:** Linux `7.0.0-1017-azure-x86_64` (`glibc 2.41`), **`4 vCPU`**, **`15.61 GiB RAM`**, Python `3.11.18`.
- **Topology Compose:** Đầy đủ dịch vụ sản xuất (`frontend` Nginx, `backend` Gunicorn `2 workers × 4 threads = 8 luồng xử lý đồng thời`, `websocket`, `queue-short`, `queue-long`, `scheduler`, `db` MariaDB 11.8 `utf8mb4_unicode_ci`, `redis-cache`, `redis-queue`).

### 1.2. Quy mô dữ liệu `S1` đã khởi tạo (`seed_elapsed_sec = 57.57 giây`, `ZERO_REAL_PII_SYNTHETIC_ONLY`)
- `CRM Lead` (`Hồ sơ liên hệ`): **`50.000`**
- `CSKH Work Item` (`Đầu việc liên hệ`): **`75.000`**
- `CSKH Contact Attempt` (`Lần ghi nhận liên hệ`): **`250.000`**
- `CSKH SLA Obligation` (`Nghĩa vụ hạn xử lý`): **`150.000`**
- `CSKH Assignment History` (`Lịch sử phân công`): **`75.000`**
- Tổ chức nhân sự & chiến dịch: **`4` đội**, **`5` chiến dịch** (chiến dịch chính `vgvok2vgnm`), **`40` Người tuyển dụng đang hoạt động (`active_staff_users = 40`)**.

---

## 2. Kết Quả Đo Độ Trễ & Thông Lượng HTTP Trên Quy Mô `S1` (`P4.md §9.2`)

### 2.1. Đợt tải hỗn hợp chuẩn (`concurrency = 12`, `240` requests/kịch bản, tổng `1.680` requests)
- **Tổng hợp toàn đợt:** `1.673 / 1.680` thành công, tỷ lệ lỗi tổng **`0.4167%`** (`0%` lỗi trên toàn bộ 6 kịch bản JSON API & trang báo cáo), thông lượng tổng hợp `5.523 RPS`.

| Kịch bản (`scenario_id`) | Đường dẫn thực đo | Đồng thời | Tổng / OK | Lỗi (`%`) | RPS | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | `max` (`ms`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `health_ping` | `GET /api/method/ping` | `12` | `240 / 240` | `0.00%` | `544.56` | `19.99` | **`42.36`** | `71.68` | `75.59` |
| `my_customers_worklist_api` | `GET /api/method/crm_cskh.api.customers.my_list?page=1&page_size=20` | `12` | `240 / 240` | `0.00%` | `502.98` | `22.31` | **`38.39`** | `45.79` | `54.30` |
| `bod_executive_overview_api` | `GET /api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key=2026-10` | `12` | `240 / 240` | `0.00%` | `216.74` | `53.46` | **`89.88`** | `100.75` | `109.01` |
| `telesale_worklist_page` | `GET /cskh/my-customers` (SSR HTML) | `12` | `233 / 240` | `2.92%` | `19.33` | `598.89` | **`887.19`** | `1086.83` | `1105.51` |
| `campaigns_distribution_view` | `GET /cskh/campaigns` (SSR HTML) | `12` | `240 / 240` | `0.00%` | `18.26` | `682.05` | **`1081.17`** | `1136.04` | `1204.00` |
| `quality_overview_drilldown` | `GET /cskh/quality-overview` (SSR HTML) | `12` | `240 / 240` | `0.00%` | `19.05` | `641.40` | **`1104.50`** | `1273.29` | `1327.95` |
| `kpi_metric_drilldown_250k_api` | `GET .../get_metric_drilldown?period_type=daily&period_key=2026-10-27&scope_type=campaign&metric_code=CONNECTED_RATE&page=1&page_size=20` | `12` | `240 / 240` | `0.00%` | `0.91` | `12523.03` | **`18865.25`** | `25555.10` | `28350.54` |

### 2.2. Đợt bùng nổ đỉnh đồng thời 40 nhân sự không có thời gian nghỉ (`peak_40_staff_concurrent_wave`, `concurrency = 40`, `160` requests/kịch bản)

| Kịch bản (`concurrency = 40`) | Tổng / OK | Lỗi (`%`) | RPS | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | Nhận xét kỹ thuật thực tế |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `my_customers_worklist_api` | `160 / 160` | **`0.00%`** | `494.75` | `68.13` | **`105.00`** | `110.14` | Vượt xa mục tiêu tham khảo `<= 1.500 ms` ngay cả khi 40 nhân sự bấm cùng một mili-giây. |
| `bod_executive_overview_api` | `160 / 160` | **`0.00%`** | `208.56` | `179.58` | **`221.70`** | `232.12` | Đọc trực tiếp từ `CSKH KPI Snapshot` (`0` quét bảng Attempt), dưới xa ngưỡng `3.000 ms`. |
| `telesale_worklist_page` (SSR) | `160 / 160` | **`0.00%`** | `16.23` | `2378.06` | **`2913.37`** | `2996.30` | `0%` lỗi HTTP; độ trễ tăng do xếp hàng chờ 8 luồng Gunicorn (`2 workers × 4 threads`). |
| `kpi_metric_drilldown_250k_api` | `24 / 160` | `85.00%` (timeout `30s` phía client) | `1.33` | `30029.97` | `30032.72` | `30034.25` | Điểm nghẽn đã xác định rõ: 40 truy vấn `load_period_raw_dataset()` đồng thời làm nghẽn 8 luồng Gunicorn (xem Mục 4). |

---

## 3. Đo Kiểm Tác Vụ Nghiệp Vụ Lõi, Bất Đồng Bộ & Tài Nguyên Hệ Thống

### 3.1. Độ trễ tầng dịch vụ nghiệp vụ trực tiếp trên tập `S1` (`domain_workloads_s1`)

| Tác vụ nghiệp vụ | Số mẫu | Lỗi | Ops/giây | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | Bảo chứng toàn vẹn |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| Mở danh sách `Hồ sơ được giao` (`list_work_items`) | `60` | `0` | `636.87` | `1.34` | **`1.56`** | `5.58` | Dùng index `cskh_lookup_0` (`38` dòng ước tính). |
| Mở chi tiết đầu việc + dòng thời gian liên hệ | `60` | `0` | `99.99` | `9.53` | **`10.29`** | `25.01` | Dùng index `cskh_attempt_wi_occ_idx` (`4` dòng ước tính). |
| Ghi `Contact Attempt` + đặt `CRM Task` hẹn lại | `50` | `0` | `35.05` | `27.11` | **`30.24`** | `77.69` | `idempotent_replay_verified = true` (`0` nhân đôi). |
| Đọc `BOD Executive Overview` từ Snapshot | `40` | `0` | `336.14` | `3.05` | **`3.50`** | `3.63` | `contact_attempt_table_scans_during_read = 0`. |
| Drill-down KPI phân trang theo ngày/chiến dịch | `40` | `0` | `0.264` | `3789.25` | **`4247.95`** | `4303.90` | Khớp 100% tử/mẫu số snapshot. |
| Drill-down KPI toàn tháng không lọc (`250k` Attempts) | `2` | `0` | `0.018` | `54278.12` | **`54389.47`** | `54399.37` | Chạy trọn vẹn không OOM; xác định rõ giới hạn kỳ rộng. |

### 3.2. Nhập CSV, Phân phối Chiến dịch, Tính lại KPI & Hàng đợi RQ
- **Nhập CSV bất đồng bộ (`500` dòng hợp lệ):** Xem trước + xác nhận + worker xử lý hoàn tất (`COMPLETED`) trong **`15.644,50 ms` (`15,64 giây`)**, thông lượng **`31,96 dòng/giây`**, tạo đúng `500` bản ghi.
- **Phân phối có trọng số cho `40` nhân sự (`500` đầu việc đủ điều kiện):** Xem trước (`preview`) mất **`554,91 ms`**; thực thi + worker hoàn tất (`completed`) mất **`19.263,19 ms` (`19,26 giây`)**, `0` chủ sở hữu kép.
- **Tính lại KPI (`kpi_recompute_s1`):**
  - Tính mới toàn tháng (`75.000` Work Items / `250.000` Attempts / `150.000` SLA): **`68.592,29 ms` (`68,59 giây`)**, trạng thái `completed`, RAM sau chạy `783,16 MiB` (đỉnh `HWM = 1.724,92 MiB`).
  - Tính tăng trưởng theo ngày (`daily incremental`): **`16.315,07 ms` (`16,32 giây`)**, RAM `786,57 MiB`.
- **Xả bùng nổ `50` job trên `queue-short`:** Đẩy vào hàng đợi mất `13,31 ms`, xả hết về `0` tồn đọng trong `60.040,22 ms` (`0,83 job/giây` trên 1 tiến trình worker đơn).

### 3.3. Kế hoạch thực thi MariaDB (`EXPLAIN`) & Tài nguyên Container
- **`EXPLAIN` truy vấn nóng:**
  - `worklist_by_owner` (`tabCSKH Work Item`): `type = ref`, dùng index **`cskh_lookup_0`**, `rows = 38`.
  - `attempt_timeline_by_work_item` (`tabCSKH Contact Attempt`): `type = ref`, dùng index **`cskh_attempt_wi_occ_idx`**, `rows = 4`.
  - `kpi_snapshot_lookup` (`tabCSKH KPI Snapshot`): `type = ALL`, `rows = 915` (`3,5 ms` ở quy mô `S1`; khuyến nghị thêm composite index `(period_type, period_key, scope_type, is_latest_published)` khi số snapshot tăng lên hàng chục nghìn kỳ).
- **Sức khỏe MariaDB & Container sau toàn bộ bài tải:**
  - `Slow_queries = 0`, `Max_used_connections = 10`, `Innodb_row_lock_waits = 3` (`avg = 3 ms`), `redis_queue_rtt_ms = 0.984 ms`, hàng đợi sau bài đo `short=0, default=0, long=0`.
  - Mức RAM sau tải: `backend = 597.8 MiB`, `db = 510.2 MiB`, `scheduler = 61.19 MiB`, `queue-short = 36.12 MiB`, `websocket = 23.97 MiB`, `redis-cache = 8.30 MiB`, `frontend = 7.88 MiB` (`0` container restart, `0` OOMKilled).

---

## 4. Điểm Nghẽn Đã Xác Định, Khuyến Nghị Cấu Hình VPS & Đề Xuất SLO Chờ Duyệt

### 4.1. Bốn giới hạn thực tế đã đo được (`observed_environment_limits`)
1. **Trần luồng Gunicorn (`2 workers × 4 threads = 8 luồng`):** Ở `concurrency = 12`, API danh sách (`38,39 ms`) và tổng quan BOD (`89,88 ms`) phản hồi cực nhanh. Khi cả 40 nhân sự gọi SSR/drill-down nặng đúng cùng một mili-giây, các request xếp hàng chờ 8 luồng Gunicorn làm tăng đuôi `p95`/`p99`.
2. **`get_metric_drilldown` trên tập `250.000` Attempts:** Hàm nạp tập dữ liệu thô của kỳ qua `load_period_raw_dataset()` trước khi cắt trang trong Python (`p95 = 4,25s` đơn luồng; `18,87s` khi `12` người bấm drill-down cùng lúc; `54,39s` nếu mở toàn công ty cả tháng không lọc). Trong vận hành thực tế ở quy mô `S1`, thao tác truy vết trực tiếp nên mặc định lọc theo kỳ **Ngày/Tuần** hoặc theo **Chiến dịch/Đội**, và chỉ do `BOD`/`Leader`/`Admin` sử dụng (không phải thao tác lặp mỗi giây của 40 nhân viên).
3. **Tính lại KPI toàn tháng (`68,59 giây`, HWM `1,72 GiB`):** Phải luôn chạy bất đồng bộ trên `queue-long` hoặc lịch ban đêm; ban ngày chỉ chạy `drain_dirty_kpi_scopes` tăng trưởng (`16,32 giây`).
4. **Tách biệt hàng đợi `queue-short` và `queue-long`:** Mọi lô nhập CSV lớn, chạy phân phối chiến dịch và tính lại KPI kỳ rộng bắt buộc đi qua `queue-long` để không bao giờ làm trễ các job ngắn tương tác trên `queue-short`.

### 4.2. Khuyến nghị cấu hình máy chủ VPS & Tham số vận hành (`Sizing Recommendation`)
- **Quy mô hiện tại & MVP đến ngưỡng `S1` (`<= 40` nhân sự đồng thời):**
  - Cấu hình tối thiểu khuyến nghị: **`4 vCPU / 8–16 GB RAM / >= 80–120 GB NVMe SSD`** (VPS hiện tại `103.77.208.186` đang dùng `23,79%` ổ đĩa và có dư địa RAM/CPU rộng cho quy mô vận hành hiện hành).
  - Tinh chỉnh biến môi trường khi số nhân sự active tăng từ nhóm pilot lên `30–40` người: nâng `GUNICORN_WORKERS=3` hoặc `4` (`× 4 threads = 12–16` luồng web), giữ nguyên tách biệt `queue-short` và `queue-long`, đặt `innodb_buffer_pool_size = 2G–4G` trên host `8–16 GB RAM`.
- **Lộ trình nâng cấp nếu vượt ngưỡng `S1` tiến tới `S2` (`250k` khách / `1,25M` Attempts / `100` staff):** Chuyển truy vấn `get_metric_drilldown` sang phân trang SQL trực tiếp có composite index theo kỳ + nâng lên `8 vCPU / 16–32 GB RAM` và tách máy chủ MariaDB riêng khi cần HA.

### 4.3. Bảng Đề Xuất SLO Nội Bộ Để Owner / BOD Xem Xét Phê Duyệt (`PROPOSED_ONLY_NOT_COMMITTED_SLA`)

| Chỉ số đề xuất | Kết quả thực đo trên `S1` (`c=12`) | Ngưỡng SLO đề xuất trình duyệt | Trạng thái cam kết |
| :--- | :---: | :---: | :---: |
| `my_customers_worklist_api` (`p95`) | **`38,39 ms`** (`105,00 ms` ở `c=40`) | `<= 500 ms` (tham chiếu `P4.md`: `1.500 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Ghi `Contact Attempt` + `Follow-up` (`p95`) | **`30,24 ms`** (tầng giao dịch DB) | `<= 600 ms` (tham chiếu `P4.md`: `2.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| `bod_executive_overview_api` (`p95`) | **`89,88 ms`** (`221,70 ms` ở `c=40`) | `<= 800 ms` (tham chiếu `P4.md`: `3.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| `kpi_metric_drilldown` theo ngày/chiến dịch (`p95`) | `4.247,95 ms` (đơn) / `18.865,25 ms` (`12` luồng drill-down đồng thời) | `<= 5.000 ms` (tác vụ quản lý đơn lẻ) / `<= 47.200 ms` (dưới tải `c=12` đồng thời trên `250k` Attempts) | `CHỜ OWNER PHÊ DUYỆT` |
| Tỷ lệ lỗi `HTTP 5xx` trên API nghiệp vụ | **`0,00%`** | `<= 0,50%` | `CHỜ OWNER PHÊ DUYỆT` |
| Độ trễ làm mới KPI tăng trưởng trong ngày | **`16,32 giây`** (+ chu kỳ cron `5 phút`) | `<= 300 giây` (`5 phút`) | `CHỜ OWNER PHÊ DUYỆT` |
