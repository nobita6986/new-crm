# Báo Cáo Đo Kiểm Hiệu Năng, Chịu Tải & Khuyến Nghị Dung Lượng Môi Trường Cô Lập (`P4-06` — Cổng `G18`)

- **Mã tài liệu:** `P4-PERFORMANCE-REPORT` (`docs/p4/performance-report.md` — `P4.md §3.3`, `§9`, `§20.2`)
- **Bằng chứng JSON gốc đã kiểm chứng mã băm:** [`deploy/ops/evidence/p4-candidate-and-s1-20261109/benchmark-s1-report.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/benchmark-s1-report.json) (SHA-256 `7e7c2e000fea33fe41ddc0c43c43a486855157c9fc2cd359fd5d6461f1f82e3f`, GitHub Actions Run `42984867723` trên repo `nobita6986/new-crm`, hoàn tất `2026-11-11T08:43:23Z`)
- **Bộ sinh dữ liệu & Trình đo tải:** [`deploy/release/benchmark_p4_isolated.py`](../../deploy/release/benchmark_p4_isolated.py) (`schema_version = cskh-p4-isolated-benchmark-v2`)
- **Image bất biến, cấu hình worker & hạn mức tài nguyên thực cấp trong kết quả đo:**
  - **Registry Manifest Digest (`app_image_tested`):** `ghcr.io/nobita6986/crm-cskh@sha256:693a3cdcc758dab64b3ac61d8e6f066581d94b362c9649d709684b72d7d0a2e6`
  - **OCI Config / Local Image ID (`image_id`):** `sha256:4d43b76850b8caa34f96a4139b31e8bd17815706bf93edcb8a0dd18cec25e8df`
  - **Commit mã nguồn & Cây ứng dụng:** `bcdc419f9f95ad75449315572928e3d8c43817cc` (`apps_tree_sha = 4d361e68912f52243751ae5986cabbd13033f050`, `crm_cskh_tree_sha = a8737dbcce74f9095a6e76c5e93491e92843f3d0`)
  - **Tài nguyên thực cấp (`Confirmed Resource Allocation`):** Host Linux `7.0.0-1017-azure-x86_64` (`glibc 2.41`), **`4 vCPU` (`cpu_count = 4`)**, **`15.61 GiB RAM`**, Python `3.11.18`, stack cô lập `127.0.0.1` (`site = p4-s1-bench.localhost`).
  - **Cấu hình Worker (`Worker & Queue Configuration`):**
    - `backend` (Gunicorn): **`GUNICORN_WORKERS=4`, `GUNICORN_THREADS=4` (`16` luồng xử lý HTTP Python đồng thời)**
    - `queue-short`: **`1` tiến trình worker RQ `short,default`** (xử lý tác vụ tương tác nhanh)
    - `queue-long`: **`1` tiến trình worker RQ `long`** (tách riêng cho nhập CSV `50.000` dòng, phân phối có trọng số `10.000` đầu việc, và tính lại KPI toàn tháng)
    - Dịch vụ nền tảng đi kèm: `frontend` (Nginx), `websocket` (Node Socket.IO `:9000`), `scheduler` (Frappe cron), `db` (MariaDB `11.8` `utf8mb4_unicode_ci`), `redis-cache`, `redis-queue`.

> [!IMPORTANT]
> **Định khung quản trị bắt buộc (`P4.md §9.1`, `§9.2` & Chỉ đạo `T0`):**
> 1. **Tập dữ liệu `S1` (`50.000` Hồ sơ liên hệ / `75.000` Đầu việc / `250.000` Lần liên hệ / `150.000` Nghĩa vụ SLA / `40` nhân sự active) là tập dữ liệu tổng hợp gợi ý để đo hiệu năng/dung lượng trên môi trường cô lập (`SUGGESTED_S1_DATASET_NOT_MONTHLY_VOLUME_COMMITMENT`) — KHÔNG phải cam kết sản lượng `50.000 hồ sơ/tháng` và KHÔNG chứa bất kỳ dữ liệu cá nhân (PII) thật nào.**
> 2. **Các ngưỡng độ trễ mục tiêu là đề xuất SLO nội bộ (`UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL`), KHÔNG phải SLA sản xuất đã ký cam kết.**
> 3. **Tuyệt đối không bắn tải vào VPS đang phục vụ của Owner (`https://crm.hrpartner.vn` / `103.77.208.186`):** `isolation_guard` xác nhận `verified_isolated = true`, `owner_vps_touched = false`.

---

## 1. Cấu Hình Môi Trường Đo, Tài Nguyên Thực Cấp & Tập Dữ Liệu Tổng Hợp `S1` (`P4.md §9.1`, `§9.3`)

### 1.1. Phần cứng, Cấu hình Worker & Topology Container Cô lập
- **Máy chạy đo kiểm:** Linux `7.0.0-1017-azure-x86_64` (`glibc 2.41`), **`4 vCPU`**, **`15.61 GiB RAM`**, Python `3.11.18`.
- **Cấu hình Worker & Dịch vụ:** `frontend` Nginx, `backend` Gunicorn **`4 workers × 4 threads = 16 luồng xử lý HTTP đồng thời`**, `websocket`, `queue-short` (tác vụ nhanh), `queue-long` (tác vụ lô lớn: CSV `50k`, phân phối `10k`, tính lại KPI), `scheduler`, `db` MariaDB 11.8 (`utf8mb4_unicode_ci`), `redis-cache`, `redis-queue`.

### 1.2. Quy mô dữ liệu `S1` đã khởi tạo (`seed_elapsed_sec = 76.28 giây`, `ZERO_REAL_PII_SYNTHETIC_ONLY`)
- `CRM Lead` (`Hồ sơ liên hệ`): **`50.000`** (`cskh_identity_index = 50.000`)
- `CSKH Work Item` (`Đầu việc liên hệ`): **`75.000`**
- `CSKH Contact Attempt` (`Lần ghi nhận liên hệ`): **`250.000`**
- `CSKH SLA Obligation` (`Nghĩa vụ hạn xử lý`): **`150.000`**
- `CSKH Assignment History` (`Lịch sử phân công`): **`75.000`**
- Tổ chức nhân sự & chiến dịch: **`4` đội**, **`6` chiến dịch** (chiến dịch chính `uir9u2sp56`, chiến dịch phân phối `10k` `uirros0mc4`), **`40` Người tuyển dụng đang hoạt động (`active_staff_users = 40`)**, **`80` phiên đăng nhập thực phân biệt (`distinct_authenticated_sessions_created = 80`)**.

---

## 2. Ma Trận Nghiệm Thu `P4.md §9.2` (`11/11 PASS`) & Độ Trễ HTTP/SSR Trên Quy Mô `S1`

### 2.1. Tổng hợp Ma trận Nghiệm thu `P4.md §9.2` (`p4_9_2_acceptance_matrix.all_passed = true`)

| Hạng mục `P4.md §9.2` | Ngưỡng tham chiếu `P4.md` | Kết quả thực đo trên `@sha256:693a3cdcc758...` | Bảo chứng toàn vẹn & Đối soát DB | Kết luận |
| :--- | :---: | :---: | :--- | :---: |
| **1. `Staff` mở `My Customers` (`staff_my_customers_p95_le_1500ms`)** | `p95 <= 1.500 ms` | **`p95 = 73,585 ms`** (`p50 = 36,339 ms`, `290,10 RPS`) | `240 / 240` OK (`0,0%` lỗi) | **`PASS`** |
| **2. Mở chi tiết Lead + lịch sử Attempt (`open_lead_and_history_p95_le_2000ms`)** | `p95 <= 2.000 ms` | **`p95 = 184,981 ms`** (`get_work_item p95 = 109,379 ms`, `list_attempts p95 = 95,245 ms`) | `160 / 160` OK (`0,0%` lỗi) | **`PASS`** |
| **3. Ghi `Contact Attempt` + đặt `Callback` (`record_attempt_and_callback_p95_le_2000ms_zero_dup`)** | `p95 <= 2.000 ms`, `0` nhân đôi | **`p95 = 477,873 ms`** (`p50 = 289,996 ms`, `26,14 RPS`) | `attempts_created = 80`, `work_items_with_open_callback_task = 80`, `duplicate_idempotency_keys = 0`, `http_idempotent_replays_verified = 10` | **`PASS`** |
| **4. `BOD` mở Overview từ Snapshot (`bod_overview_snapshot_p95_le_3000ms`)** | `p95 <= 3.000 ms`, `0` quét bảng Attempt | **`p95 = 96,042 ms`** (`p50 = 53,166 ms`, `202,06 RPS`) | `EXPLAIN key = idx_cskh_kpisnap_lookup`, `contact_attempt_table_scans_during_read = 0` | **`PASS`** |
| **5. Drill-down KPI phân trang trên `250k` Attempts (`kpi_drilldown_paginated_p95_le_3000ms`)** | `p95 <= 3.000 ms` | Kỳ ngày/chiến dịch: **`p95 = 290,296 ms`**<br>Kỳ toàn tháng toàn công ty (`250k` Attempts): **`p95 = 203,350 ms`** | `120 / 120` OK mỗi kịch bản (`0,0%` lỗi); `Staff` gọi trái thẩm quyền phạm vi công ty bị chặn **`HTTP 403`** | **`PASS`** |
| **6. Xem trước phân phối có trọng số `10.000` đầu việc (`preview_weighted_distribution_10000_items`)** | Tham chiếu `<= 60.000 ms` | **`787,08 ms`** (`0,79 giây`) | `eligible_count = 10.000` đầu việc đủ điều kiện cho `40` nhân sự | **`PASS`** |
| **7. Thực thi phân phối có trọng số `10.000` đầu việc (`execute_weighted_distribution_10000_items`)** | Bất đồng bộ không khóa UI, `0` trùng | **`764.511,65 ms`** trên `queue-long` (luồng đọc `My Customers` đồng thời **`p95 = 3,696 ms`**) | `created_work_items = 10.000`, `duplicate_work_items = 0`, `ownership_reconciled = true`, `abort_cancel_guard_verified = true` | **`PASS`** |
| **8. Nhập CSV `50.000` dòng (`import_50000_csv_rows`)** | Bất đồng bộ có tiến độ & báo cáo lỗi | Preview **`650,63 ms`**, tổng preview + worker **`144.339,37 ms`** (`346,41 dòng/giây`, đọc `My Customers` đồng thời **`p95 = 5,988 ms`**) | `total_rows = 50.000` (`created = 10.000`, `reused = 5.000`, `duplicate = 39.500`, `error_rows = 500`), `error_artifact_present = true`, `HWM = 1.729,14 MiB` | **`PASS`** |
| **9. Tính lại KPI nền không chặn đọc & phát hành nguyên tử (`recompute_kpi_non_blocking_atomic_publish`)** | Không chặn UI, chỉ đổi revision khi hoàn tất | Toàn tháng (`75k` WI / `250k` Attempts / `150k` SLA): **`33.571,48 ms`** (`33,57s`); tăng trưởng ngày: **`2.953,42 ms`** (`2,95s`) | `revisions_seen_while_running = ["v9ik5mrdpu"]` -> sau hoàn tất `vl3kd78ihp` (`atomic_publish_verified = true`), `209` lượt đọc UI đồng thời **`p95 = 6,671 ms`** (`0%` lỗi) | **`PASS`** |
| **10. Sóng tải hỗn hợp `20 / 40 / 80` phiên người dùng đồng thời (`concurrent_20_40_80_mixed_user_sessions`)** | Đo rõ điểm nghẽn & trần phần cứng | **`20` phiên:** `p95 = 414,335 ms` (`0%` lỗi)<br>**`40` phiên:** `p95 = 906,052 ms` (`0%` lỗi)<br>**`80` phiên:** `p95 = 1.413,987 ms` (`0%` lỗi) | Tổng `1.000 / 1.000` requests hỗn hợp (Read + Drill-down + Write Attempt) hoàn tất với **`0,0%` lỗi HTTP** | **`PASS`** |
| **11. Trang SSR `Hồ sơ được giao` không lỗi ở `c=12` và `c=40` (`telesale_worklist_ssr_zero_errors_c12_and_c40`)** | `0` lỗi HTTP/SSR & `0` timeout | **`c=12` (shared session):** `240/240` OK (`p50 = 521,224 ms`, `p95 = 981,420 ms`)<br>**`c=12` (`40` phiên nhân sự):** `240/240` OK (`p50 = 809,810 ms`, `p95 = 1.432,297 ms`)<br>**`c=40` (`1` phiên):** `160/160` OK (`p50 = 1.773,552 ms`, `p95 = 2.659,025 ms`)<br>**`c=40` (`40` phiên nhân sự):** `160/160` OK (`p50 = 2.193,414 ms`, `p95 = 6.009,040 ms`) | `shared_new_session_c12_error_count = 0`, `staff_40_sessions_c40_error_count = 0`, `timeout_count = 0` | **`PASS`** |

### 2.2. Đợt tải hỗn hợp chuẩn 8 kịch bản (`concurrency = 12`, `240` requests/kịch bản, tổng `1.920` requests)
- **Tổng hợp toàn đợt (`overall_summary` trên `@sha256:693a3cdcc758...`):** **`1.920 / 1.920` thành công, tỷ lệ lỗi tổng `0,0000%` (`0` lỗi và `0` timeout trên cả 8 kịch bản, bao gồm `telesale_worklist_page` SSR ở cả `c=12` và `c=40` với 40 phiên nhân sự phân biệt)**, thông lượng tổng hợp **`49,785 RPS`**, `p50 = 114,235 ms`, `p95 = 765,308 ms`, `p99 = 1.090,273 ms`, `max = 1.353,790 ms`.

| Kịch bản (`scenario_id`) | Đường dẫn thực đo | Đồng thời | Tổng / OK | Lỗi (`%`) | RPS | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | `max` (`ms`) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `health_ping` | `GET /api/method/ping` | `12` | `240 / 240` | `0,00%` | `500,18` | `18,95` | **`57,75`** | `110,61` | `117,95` |
| `my_customers_worklist_api` | `GET /api/method/crm_cskh.api.customers.my_list?page=1&page_size=20` | `12` | `240 / 240` | `0,00%` | `566,28` | `20,40` | **`30,61`** | `37,84` | `39,77` |
| `bod_executive_overview_api` | `GET /api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key=2026-10` | `12` | `240 / 240` | `0,00%` | `246,01` | `45,20` | **`87,48`** | `99,73` | `109,33` |
| `kpi_metric_drilldown_250k_api` | `GET .../get_metric_drilldown?period_type=daily&period_key=2026-10-27&scope_type=campaign&scope_id=uir9u2sp56&metric_code=CONNECTED_RATE&page=1&page_size=20` | `12` | `240 / 240` | `0,00%` | `71,00` | `159,91` | **`269,47`** | `317,78` | `352,72` |
| `kpi_metric_drilldown_monthly_company_api` | `GET .../get_metric_drilldown?period_type=monthly&period_key=2026-10&scope_type=company&metric_code=CONNECTED_RATE&page=1&page_size=20` | `12` | `240 / 240` | `0,00%` | `147,33` | `75,50` | **`130,06`** | `141,05` | `153,93` |
| `campaigns_distribution_view` | `GET /cskh/campaigns` (SSR HTML) | `12` | `240 / 240` | `0,00%` | `22,76` | `474,22` | **`1088,61`** | `1260,67` | `1339,75` |
| `quality_overview_drilldown` | `GET /cskh/quality-overview` (SSR HTML) | `12` | `240 / 240` | `0,00%` | `23,40` | `497,39` | **`850,42`** | `1000,33` | `1241,27` |
| `telesale_worklist_page` | `GET /cskh/my-customers` (SSR HTML) | `12` | `240 / 240` | **`0,00%`** | `22,07` | `521,22` | **`981,42`** | `1236,00` | `1353,79` |

### 2.3. Đợt bùng nổ đỉnh đồng thời 40 nhân sự (`peak_40_staff_concurrent_wave`, `800 / 800` OK, `0,00%` lỗi), Tách biệt SSR `40` phiên & Sóng hỗn hợp `20 / 40 / 80` phiên

| Đợt đo đồng thời | Tổng / OK | Lỗi (`%`) | RPS | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | `max` (`ms`) | Nhận xét kỹ thuật thực tế |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`peak_40_staff` — `my_customers_worklist_api`** (`c=40`) | `160 / 160` | **`0,00%`** | `513,91` | `56,57` | **`105,96`** | `114,91` | `117,24` | Dưới xa ngưỡng `1.500 ms` ngay cả khi 40 nhân sự bấm cùng một mili-giây. |
| **`peak_40_staff` — `bod_executive_overview_api`** (`c=40`) | `160 / 160` | **`0,00%`** | `213,84` | `135,51` | **`296,20`** | `312,36` | `327,39` | Đọc trực tiếp từ `idx_cskh_kpisnap_lookup` (`0` quét bảng Attempt). |
| **`peak_40_staff` — `kpi_metric_drilldown_monthly_company_api`** (`c=40`) | `160 / 160` | **`0,00%`** | `144,93` | `226,85` | **`393,96`** | `409,49` | `412,09` | Tối ưu phân trang chỉ mục + định dạng ISO lười giúp drill-down toàn tháng `250k` Attempts đạt `p95 = 393,96 ms` ở `c=40`. |
| **`peak_40_staff` — `kpi_metric_drilldown_250k_api` (daily campaign)** (`c=40`) | `160 / 160` | **`0,00%`** | `68,00` | `493,57` | **`843,43`** | `868,56` | `883,85` | `0%` lỗi ở `c=40`, dưới xa ngưỡng `3.000 ms`. |
| **`peak_40_staff` — `telesale_worklist_page` (SSR — `case_2b` 1 phiên khởi tạo sẵn)** (`c=40`) | `160 / 160` | **`0,00%`** | `21,06` | `1773,55` | **`2659,03`** | `2998,82` | `3070,96` | `16` luồng Gunicorn (`4×4`) phục vụ `160/160` SSR thành công (`0%` lỗi). |
| **SSR `40` phiên nhân sự phân biệt (`case_2c_40_staff_sessions_c12`)** (`c=12`) | `240 / 240` | **`0,00%`** | `14,10` | `809,81` | **`1432,30`** | `1846,03` | `1901,90` | `40` phiên nhân sự độc lập truy cập `/cskh/my-customers` ở `c=12` (`0%` lỗi). |
| **SSR `40` phiên nhân sự phân biệt (`case_2c_40_staff_sessions_c40`)** (`c=40`) | `160 / 160` | **`0,00%`** | `14,02` | `2193,41` | **`6009,04`** | `6122,05` | `6248,62` | `40` phiên nhân sự độc lập đồng thời ở `c=40` (`0%` lỗi, `0` timeout). |
| **Sóng hỗn hợp `sessions_20`** (`20` phiên đồng thời) | `200 / 200` | **`0,00%`** | `69,26` | `139,89` | **`414,34`** | `682,55` | `869,08` | Hỗn hợp `My Customers`, `Lead Detail`, `Record Attempt`, `BOD Overview`, `Drill-down`. |
| **Sóng hỗn hợp `sessions_40`** (`40` phiên đồng thời) | `320 / 320` | **`0,00%`** | `111,81` | `219,75` | **`906,05`** | `1094,41` | `1455,09` | Đạt dưới `1 giây` ở quy mô đỉnh `40` nhân sự hoạt động đồng thời. |
| **Sóng hỗn hợp `sessions_80`** (`80` phiên đồng thời — gấp đôi `S1`) | `480 / 480` | **`0,00%`** | `110,07` | `508,74` | **`1413,99`** | `2072,68` | `2386,63` | Ở `80` phiên đồng thời (`5×` số luồng Gunicorn `16`), cụ thể: `my_customers_api p95 = 1200,77 ms`, `bod_overview_api p95 = 1314,88 ms`, `record_attempt_write_api p95 = 1346,67 ms`, `kpi_drilldown_api p95 = 1413,90 ms`, `lead_detail_history_api p95 = 1819,96 ms` (`0%` lỗi). |

---

## 3. Đo Kiểm Tác Vụ Nghiệp Vụ Lõi, Lô Lớn Bất Đồng Bộ & Tài Nguyên Thực Cấp

### 3.1. Độ trễ tầng dịch vụ nghiệp vụ trực tiếp trên tập `S1` (`domain_workloads_s1`)

| Tác vụ nghiệp vụ | Số mẫu | Lỗi | Ops/giây | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | Bảo chứng toàn vẹn |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| Mở danh sách `Hồ sơ được giao` (`list_work_items`) | `60` | `0` | `412,47` | `2,24` | **`2,47`** | `6,36` | Dùng index `cskh_lookup_0`. |
| Mở chi tiết đầu việc + dòng thời gian liên hệ | `60` | `0` | `71,46` | `12,90` | **`14,22`** | `33,38` | Dùng index `cskh_attempt_wi_occ_idx`. |
| Ghi `Contact Attempt` + đặt `CRM Task` hẹn lại | `50` | `0` | `18,73` | `40,91` | **`133,49`** | `239,20` | `idempotent_replay_verified = true` (`0` nhân đôi). |
| Đọc `BOD Executive Overview` từ Snapshot | `40` | `0` | `229,75` | `4,21` | **`5,55`** | `5,61` | `contact_attempt_table_scans_during_read = 0`. |
| Drill-down KPI phân trang theo ngày/chiến dịch (`250k` Attempts) | `40` | `0` | `37,73` | `26,16` | **`30,90`** | `37,06` | Phân trang qua chỉ mục + tính phút làm việc lười trên đúng trang trả về. |
| Drill-down KPI phân trang toàn tháng toàn công ty (`250k` Attempts) | `10` | `0` | `35,61` | `17,57` | **`71,44`** | `95,49` | Giảm từ `54.389 ms` (trước tối ưu) xuống **`71,44 ms`** (cải thiện hơn `760×`). |

### 3.2. Nhập CSV `50.000` dòng, Phân phối `10.000` đầu việc, Tính lại KPI & Hàng đợi RQ
- **Nhập CSV bất đồng bộ `50.000` dòng (`csv_import_50000_rows`):**
  - Cơ cấu lô: `50.000` dòng tổng (`10.000` dòng mới hợp lệ, `39.500` dòng trùng, `500` dòng lỗi cú pháp SĐT; kết quả `created_rows = 10.000`, `reused_rows = 5.000`, `skipped_rows = 500`, `error_artifact_present = true`, `progress_checkpoints_count = 76`).
  - Thời gian: Xem trước (`preview`) mất **`650,63 ms`**; worker `queue-long` xử lý hoàn tất (`COMPLETED_WITH_ERRORS`) trong **`143.688,74 ms`**, tổng cộng **`144.339,37 ms` (`144,34 giây`, thông lượng `346,41 dòng/giây`)**, đỉnh RAM `preview_hwm_mib = 1.729,14 MiB`, đọc `My Customers` đồng thời không bị chặn (`p95 = 5,988 ms`).
- **Phân phối có trọng số `10.000` đầu việc cho `40` nhân sự (`campaign_distribution_10000_work_items_40_staff`):**
  - Xem trước (`preview`): **`787,08 ms`** (đạt yêu cầu tham chiếu `<= 60.000 ms`).
  - Thực thi bất đồng bộ trên `queue-long`: **`764.511,65 ms`** (`completed`), tạo đúng `10.000` `CSKH Work Item`, `0` bản ghi trùng (`double_submit_idempotent = true`, `abort_cancel_guard_verified = true`, `stale_docshares = 0`, `stale_open_todos = 0`, `ownership_reconciled = true`), đọc `My Customers` đồng thời **`p95 = 3,696 ms`**.
- **Tính lại KPI (`kpi_recompute_s1`):**
  - Tính mới toàn tháng (`75.000` Work Items / `250.000` Attempts / `150.000` SLA): **`33.571,48 ms` (`33,57 giây`)**, trạng thái `completed`, RAM sau chạy `783,07 MiB` (`HWM = 1.729,14 MiB`); khi chạy nền trên `queue-long` (`32.616,23 ms`), `209` lượt đọc UI đồng thời đạt **`p95 = 6,671 ms`** (`0%` lỗi) và revision chỉ chuyển từ `v9ik5mrdpu` sang `vl3kd78ihp` nguyên tử khi hoàn tất.
  - Tính tăng trưởng theo ngày (`daily incremental`): **`2.953,42 ms` (`2,95 giây`)**, RAM `782,10 MiB`.
- **Xả bùng nổ `50` job trên `queue-short`:** Đẩy vào hàng đợi mất `15,88 ms` (`peak_queue_short_depth = 51`), xả hết về `0` tồn đọng trong `39.242,43 ms` (`1,27 job/giây` trên 1 tiến trình worker đơn).

### 3.3. Tài Nguyên Container Thực Đo (Trước tải / Trong sóng `80` phiên / Sau tải) & Sức khỏe MariaDB
- **Sức khỏe MariaDB & Redis sau toàn bộ bài tải (`queue_and_db_after`):**
  - `Slow_queries = 0`, `Max_used_connections = 18`, `Innodb_row_lock_waits = 58` (`avg = 86 ms` trong đợt bắn đồng thời `80` phiên), `redis_queue_rtt_ms = 1,123 ms`, hàng đợi sau bài đo `short = 0, default = 0, long = 0` (`0` tồn đọng).
- **Bảng tiêu thụ CPU & RAM thực tế của 9 container trên hạn mức `4 vCPU / 15.61 GiB RAM`:**

| Container dịch vụ | Trước bài tải HTTP (`CPU` / `RAM`) | Trong sóng đỉnh `80` phiên đồng thời (`CPU` / `RAM`) | Sau toàn bộ bài tải (`CPU` / `RAM`) |
| :--- | :---: | :---: | :---: |
| `backend-1` (`Gunicorn 4×4`) | `0,01%` / `153,4 MiB` | **`213,24%` / `483,6 MiB`** (`3,03%`) | `0,01%` / `531,9 MiB` (`3,33%`) |
| `db-1` (`MariaDB 11.8`) | `0,01%` / `353,4 MiB` | **`83,98%` / `422,3 MiB`** (`2,64%`) | `0,53%` / `426,9 MiB` (`2,67%`) |
| `queue-short-1` (`RQ short,default`) | `0,04%` / `40,23 MiB` | **`57,84%` / `163,0 MiB`** (`1,02%`) | `87,00%` / `1,627 GiB` (`10,42%`) |
| `queue-long-1` (`RQ long`) | `0,04%` / `35,26 MiB` | `0,03%` / `35,26 MiB` (`0,22%`) | `0,03%` / `35,26 MiB` (`0,22%`) |
| `scheduler-1` | `0,00%` / `61,21 MiB` | `0,00%` / `61,21 MiB` (`0,38%`) | `0,00%` / `61,21 MiB` (`0,38%`) |
| `websocket-1` (`Socket.IO`) | `0,00%` / `24,15 MiB` | `0,45%` / `24,15 MiB` (`0,15%`) | `0,00%` / `24,15 MiB` (`0,15%`) |
| `redis-cache-1` | `0,14%` / `10,25 MiB` | `11,28%` / `12,06 MiB` (`0,08%`) | `0,18%` / `12,28 MiB` (`0,08%`) |
| `redis-queue-1` | `0,14%` / `6,33 MiB` | `1,67%` / `9,53 MiB` (`0,06%`) | `0,14%` / `6,45 MiB` (`0,04%`) |
| `frontend-1` (`Nginx`) | `0,00%` / `4,36 MiB` | `3,95%` / `9,81 MiB` (`0,06%`) | `0,00%` / `9,23 MiB` (`0,06%`) |

---

## 4. Điểm Nghẽn Đã Xác Định, Khuyến Nghị Cấu Hình VPS & Đề Xuất SLO Chờ Duyệt

### 4.1. Bốn đặc tính & giới hạn thực tế đã xác nhận (`observed_environment_limits`)
1. **Trần luồng Gunicorn (`GUNICORN_WORKERS=4, GUNICORN_THREADS=4 = 16 luồng`):** Qua 3 sóng `20`, `40` và `80` phiên người dùng đồng thời không có thời gian nghỉ (`zero-think-time`), sóng `20` (`p95 = 414,335 ms`) và `40` (`p95 = 906,052 ms`) đều dưới `1 giây`; ở `80` phiên đồng thời (`5×` số luồng Gunicorn), việc xếp hàng chờ 16 luồng làm tăng đuôi `p95` lên `1.413,987 ms` với **`0%` lỗi HTTP 5xx**.
2. **Tối ưu `get_metric_drilldown` trên tập `250.000` Attempts:** Sau khi áp dụng truy vấn phân trang qua chỉ mục SQL kết hợp tính toán phút làm việc lười (`lazy business-minute`) và định dạng ISO lười cho đúng trang hiện hành, độ trễ HTTP `p95` đạt **`290,296 ms`** (kỳ ngày/chiến dịch) và **`203,350 ms`** (kỳ toàn tháng toàn công ty `250k` Attempts, `<= 3.000 ms`).
3. **Tính lại KPI toàn tháng (`33,57 giây`, HWM `1,73 GiB`) & tăng trưởng ngày (`2,95 giây`):** Chạy bất đồng bộ trên `queue-long` không gây chặn đọc UI (`p95 = 6,671 ms`) và chỉ xuất bản revision mới nguyên tử khi hoàn tất.
4. **Tách biệt hàng đợi `queue-short` và `queue-long`:** Lô nhập CSV `50.000` dòng (`144,34 giây`) và phân phối có trọng số `10.000` đầu việc (`764,51 giây`) chạy hoàn toàn trên `queue-long` với `0` bản ghi trùng và giữ nguyên độ trễ đọc `My Customers` ở mức `3,7–6,0 ms`.

### 4.2. Khuyến nghị cấu hình máy chủ VPS & Tham số vận hành (`Sizing Recommendation`)
- **Quy mô hiện tại & MVP đến ngưỡng `S1` (`<= 40` nhân sự đồng thời):**
  - Cấu hình khuyến nghị: **`4 vCPU / 8–16 GB RAM / >= 80–120 GB NVMe SSD`** với `GUNICORN_WORKERS=4`, `GUNICORN_THREADS=4`, tách biệt `queue-short` và `queue-long`, đặt `innodb_buffer_pool_size = 2G–4G`.
  - **Lưu ý bảo toàn môi trường khi dựng Staging cô lập trên cùng VPS `103.77.208.186`:** Do VPS `103.77.208.186` đang phục vụ trực tiếp `https://crm.hrpartner.vn`, khi phiên `T1A` được cấp SSH chỉ dựng Staging cô lập để kiểm tra isolation, pull/inspect và HTTPS/WSS smoke chức năng; bước đo `G18` chỉ thực hiện sau khi Owner duyệt SLO và trong hạn mức tài nguyên an toàn đã xác nhận từ bước tiền kiểm chỉ đọc để bảo toàn `Zero Live Impact` (`P4.md §9.1`).
- **Lộ trình nâng cấp nếu vượt ngưỡng `S1` tiến tới `S2` (`250k` khách / `1,25M` Attempts / `100` staff):** Nâng lên `8 vCPU / 16–32 GB RAM`, tăng số worker `queue-long` lên `2–4` tiến trình và tách máy chủ MariaDB riêng khi cần HA.

### 4.3. Bảng Đề Xuất SLO Nội Bộ Để Owner / BOD Xem Xét Phê Duyệt (`PROPOSED_ONLY_NOT_COMMITTED_SLA`)

| Chỉ số đề xuất (`proposed_slos_for_review`) | Kết quả thực đo trên `S1` (`@sha256:693a3cdcc758...`) | Ngưỡng SLO đề xuất trình duyệt | Trạng thái cam kết |
| :--- | :---: | :---: | :---: |
| `my_customers_worklist_api` (`p95`) | **`73,585 ms`** (`105,955 ms` ở `c=40`) | `<= 500 ms` (tham chiếu `P4.md`: `1.500 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Mở chi tiết Lead + lịch sử Attempt (`p95`) | **`184,981 ms`** | `<= 600 ms` (tham chiếu `P4.md`: `2.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Ghi `Contact Attempt` + `Follow-up` (`p95`) | **`477,873 ms`** (HTTP end-to-end) / `133,493 ms` (domain) | `<= 1.200 ms` (tham chiếu `P4.md`: `2.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| `bod_executive_overview_api` (`p95`) | **`96,042 ms`** (`296,195 ms` ở `c=40`) | `<= 800 ms` (tham chiếu `P4.md`: `3.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| `kpi_drilldown_paginated_api` (`p95`) | **`290,296 ms`** (ngày/chiến dịch) · **`203,350 ms`** (tháng/công ty `250k`) | `<= 1.200 ms` (tham chiếu `P4.md`: `3.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Tỷ lệ lỗi `HTTP 5xx` trên API nghiệp vụ | **`0,00%`** | `<= 0,50%` | `CHỜ OWNER PHÊ DUYỆT` |
| Độ trễ làm mới KPI tăng trưởng trong ngày | **`2,953 giây`** (`2.953,42 ms` + chu kỳ cron `5 phút`) | `<= 300 giây` (`5 phút`) | `CHỜ OWNER PHÊ DUYỆT` |

---

### 4.4. Bộ Đặc Tả SLO `v1.0.0` & Cơ Chế Phê Duyệt Tách Rời Cho Ứng Viên `@sha256:ba68cd22...` (`UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL`)

Nhằm khắc phục điểm nghẽn độ trễ drill-down tháng toàn công ty trên quy mô `250.000` attempts và chuẩn hóa quản trị kiểm định Cổng `G18` cho ứng viên mới `@sha256:ba68cd229ea3f3dc2566017e13251387b3fe398196acd4880a2a184201a00be8` (commit `3c040c85a44c4ad37e9d9152b5630a4596e593ee`), toàn bộ luồng tiền đo (`prepare-staging-g18`) và đối soát nghiệm thu (`reconcile-g18-slo`) đã được nâng cấp đồng bộ theo cơ chế tách rời phê duyệt (`decoupled approval`) từ `cd03af5`:

1. **Bộ đặc tả ngưỡng bất biến (`deploy/release/p4-s1-slo-spec.json`):**
   - Định danh gói: `slo_bundle_id = "cskh-p4-g18-s1-endpoint-slo"`, phiên bản `slo_version = "1.0.0"`, lược đồ `schema_version = "cskh-p4-s1-slo-spec-v1"`.
   - Mã băm SHA-256 bất biến chuẩn hóa LF: **`ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d`**. Tệp này tuyệt đối không bị chỉnh sửa byte khi Owner ký duyệt.
   - Hỗ trợ đầy đủ hai cấu hình tài nguyên: cấu hình chuẩn đo kiểm `s1_canonical_4x4` (`4 vCPU / 16 GiB RAM`, Gunicorn 4×4, `config_spec_sha256 = 683bc830dc7542c381fe2fb9016389a1d919c2b518f211cfe3ff5de5ff8324fe`) và cấu hình an toàn trên VPS đích `staging_vps_capped` (backend sử dụng `1,5 vCPU`, `1.536 MiB RAM` và `Gunicorn 2×4` [2 workers × 4 threads = 8 threads]; tổng stack giới hạn `7,0 vCPU` / `7.936 MiB RAM`, `config_spec_sha256 = 40254538242109f0304bf7f752e10cb68dbd0add5ba649e88505af10ab202254`, có kiểm soát headroom trước khi chạy).
2. **Phiếu phê duyệt riêng của Owner (`deploy/release/p4-s1-slo-approval.json`):**
   - Lược đồ: `schema_version = "cskh-p4-s1-slo-approval-v1"`.
   - Trạng thái khởi tạo: `approval_status = "PENDING_OWNER_APPROVAL"`, `approved_profiles = []`.
   - Ràng buộc mật mã học: Khóa chặt vào `slo_spec_sha256 = "ccd8f83c..."`, digest ứng viên `approved_image_digest = "...@sha256:ba68cd22..."`, và danh sách cấu hình được phê duyệt.
3. **Cơ chế từ chối nghiêm ngặt (`Fail-Closed Enforcement`):**
   - **Từ chối JSON digest cũ:** Bất kỳ tệp JSON nào thuộc digest cũ (`@sha256:693a3cdcc758...`) bao gồm ngưỡng v1.4, báo cáo đo kiểm, chẩn đoán G18, biên nhận Staging VPS, xác nhận T1C, hoặc phiếu duyệt Owner khi đưa vào luồng tiền đo hay chấm G18 của ứng viên `ba68cd22...` đều bị từ chối ngay lập tức (`FAIL_INVALID_THRESHOLDS`, `FAIL_DIGEST_MISMATCH`, `FAIL_INVALID_G18_DIAGNOSTIC_REPORT`, `FAIL_UNSAFE_OR_INVALID_STAGING_VPS_CONFIGURATION`, `FAIL_INVALID_T1C_CANDIDATE_CONFIRMATION`, hoặc `FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256`).
   - **Từ chối khi thiếu phê duyệt Owner:** Khi thiếu tệp phê duyệt (`owner_slo_approval=None`), tệp còn ở trạng thái `PENDING_OWNER_APPROVAL`, đường dẫn không tồn tại, danh sách profile rỗng, hoặc tự ý phê duyệt bởi `T1A`/`T1B`/`T1C`, cổng đo nghiệm thu và bộ chấm đều chặn đứng (`BLOCKED_PENDING_OWNER_SIGNED_CONFIG_SLO` / `BLOCKED_PENDING_OWNER_SLO_V1_4_SIGNATURE`), đảm bảo tuyệt đối không nghiệm thu Cổng G18 khi chưa có chữ ký hợp lệ từ Owner.

