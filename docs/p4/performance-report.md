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

### 1.2. Quy mô dữ liệu `S1` đã khởi tạo (`seed_elapsed_sec = 64.10 giây`, `ZERO_REAL_PII_SYNTHETIC_ONLY`)
- `CRM Lead` (`Hồ sơ liên hệ`): **`50.000`** (`cskh_identity_index = 50.000`)
- `CSKH Work Item` (`Đầu việc liên hệ`): **`75.000`**
- `CSKH Contact Attempt` (`Lần ghi nhận liên hệ`): **`250.000`**
- `CSKH SLA Obligation` (`Nghĩa vụ hạn xử lý`): **`150.000`**
- `CSKH Assignment History` (`Lịch sử phân công`): **`75.000`**
- Tổ chức nhân sự & chiến dịch: **`4` đội**, **`6` chiến dịch** (chiến dịch chính `8povrbi2tl`, chiến dịch phân phối `10k` `8pofn90j2i`), **`40` Người tuyển dụng đang hoạt động (`active_staff_users = 40`)**, **`80` phiên đăng nhập thực phân biệt (`distinct_authenticated_sessions_created = 80`)**.

---

## 2. Ma Trận Nghiệm Thu `P4.md §9.2` (`10/10 PASS`) & Độ Trễ HTTP Trên Quy Mô `S1`

### 2.1. Tổng hợp Ma trận Nghiệm thu `P4.md §9.2` (`p4_9_2_acceptance_matrix.all_passed = true`)

| Hạng mục `P4.md §9.2` | Ngưỡng tham chiếu `P4.md` | Kết quả thực đo trên `@sha256:5ea736da...` | Bảo chứng toàn vẹn & Đối soát DB | Kết luận |
| :--- | :---: | :---: | :--- | :---: |
| **1. `Staff` mở `My Customers` (`staff_my_customers_p95_le_1500ms`)** | `p95 <= 1.500 ms` | **`p95 = 53,603 ms`** (`p50 = 28,246 ms`, `371,71 RPS`) | `240 / 240` OK (`0,0%` lỗi) | **`PASS`** |
| **2. Mở chi tiết Lead + lịch sử Attempt (`open_lead_and_history_p95_le_2000ms`)** | `p95 <= 2.000 ms` | **`p95 = 130,815 ms`** (`get_work_item p95 = 78,458 ms`, `list_attempts p95 = 61,617 ms`) | `160 / 160` OK (`0,0%` lỗi) | **`PASS`** |
| **3. Ghi `Contact Attempt` + đặt `Callback` (`record_attempt_and_callback_p95_le_2000ms_zero_dup`)** | `p95 <= 2.000 ms`, `0` nhân đôi | **`p95 = 363,193 ms`** (`p50 = 224,160 ms`, `34,35 RPS`) | `attempts_created = 80`, `work_items_with_open_callback_task = 80`, `duplicate_idempotency_keys = 0`, `http_idempotent_replays_verified = 10` | **`PASS`** |
| **4. `BOD` mở Overview từ Snapshot (`bod_overview_snapshot_p95_le_3000ms`)** | `p95 <= 3.000 ms`, `0` quét bảng Attempt | **`p95 = 68,903 ms`** (`p50 = 45,892 ms`, `245,18 RPS`) | `EXPLAIN key = idx_cskh_kpisnap_lookup`, `contact_attempt_table_scans_during_read = 0` | **`PASS`** |
| **5. Drill-down KPI phân trang trên `250k` Attempts (`kpi_drilldown_paginated_p95_le_3000ms`)** | `p95 <= 3.000 ms` | Kỳ ngày/chiến dịch: **`p95 = 202,298 ms`**<br>Kỳ toàn tháng toàn công ty (`250k` Attempts): **`p95 = 113,294 ms`** | `120 / 120` OK mỗi kịch bản (`0,0%` lỗi); `Staff` gọi trái thẩm quyền phạm vi công ty bị chặn **`HTTP 403`** | **`PASS`** |
| **6. Xem trước phân phối có trọng số `10.000` đầu việc (`preview_weighted_distribution_10000_items`)** | Tham chiếu `<= 60.000 ms` | **`546,25 ms`** (`0,55 giây`) | `eligible_count = 10.000` đầu việc đủ điều kiện cho `40` nhân sự | **`PASS`** |
| **7. Thực thi phân phối có trọng số `10.000` đầu việc (`execute_weighted_distribution_10000_items`)** | Bất đồng bộ không khóa UI, `0` trùng | **`566.328,03 ms`** trên `queue-long` (luồng đọc `My Customers` đồng thời **`p95 = 3,136 ms`**) | `created_work_items = 10.000`, `duplicate_work_items = 0`, `ownership_reconciled = true`, `abort_cancel_guard_verified = true` | **`PASS`** |
| **8. Nhập CSV `50.000` dòng (`import_50000_csv_rows`)** | Bất đồng bộ có tiến độ & báo cáo lỗi | Preview **`509,77 ms`**, tổng preview + worker **`100.968,90 ms`** (`495,2 dòng/giây`, đọc `My Customers` đồng thời **`p95 = 5,965 ms`**) | `total_rows = 50.000` (`created = 10.000`, `reused = 5.000`, `duplicate = 39.500`, `error_rows = 500`), `error_artifact_present = true`, `HWM = 1.729,26 MiB` | **`PASS`** |
| **9. Tính lại KPI nền không chặn đọc & phát hành nguyên tử (`recompute_kpi_non_blocking_atomic_publish`)** | Không chặn UI, chỉ đổi revision khi hoàn tất | Toàn tháng (`75k` WI / `250k` Attempts / `150k` SLA): **`27.168,71 ms`** (`27,17s`); tăng trưởng ngày: **`2.316,80 ms`** (`2,32s`) | `revisions_seen_while_running = ["9d4qg8ou0b"]` -> sau hoàn tất `9me6r79leh` (`atomic_publish_verified = true`), `175` lượt đọc UI đồng thời **`p95 = 5,853 ms`** (`0%` lỗi) | **`PASS`** |
| **10. Sóng tải hỗn hợp `20 / 40 / 80` phiên người dùng đồng thời (`concurrent_20_40_80_mixed_user_sessions`)** | Đo rõ điểm nghẽn & trần phần cứng | **`20` phiên:** `p95 = 337,392 ms` (`0%` lỗi)<br>**`40` phiên:** `p95 = 625,039 ms` (`0%` lỗi)<br>**`80` phiên:** `p95 = 1.046,346 ms` (`0%` lỗi) | Tổng `1.000 / 1.000` requests hỗn hợp (Read + Drill-down + Write Attempt) hoàn tất với **`0,0%` lỗi HTTP** | **`PASS`** |

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

### 2.3. Đợt bùng nổ đỉnh đồng thời 40 nhân sự (`peak_40_staff_concurrent_wave`, `800 / 800` OK, `0,00%` lỗi) & Sóng hỗn hợp `20 / 40 / 80` phiên

| Đợt đo đồng thời | Tổng / OK | Lỗi (`%`) | RPS | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | `max` (`ms`) | Nhận xét kỹ thuật thực tế |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **`peak_40_staff` — `my_customers_worklist_api`** (`c=40`) | `160 / 160` | **`0,00%`** | `647,90` | `46,87` | **`81,24`** | `85,60` | `88,34` | Dưới xa ngưỡng `1.500 ms` ngay cả khi 40 nhân sự bấm cùng một mili-giây. |
| **`peak_40_staff` — `bod_executive_overview_api`** (`c=40`) | `160 / 160` | **`0,00%`** | `289,44` | `119,26` | **`207,28`** | `225,45` | `231,23` | Đọc trực tiếp từ `idx_cskh_kpisnap_lookup` (`0` quét bảng Attempt). |
| **`peak_40_staff` — `kpi_metric_drilldown_monthly_company_api`** (`c=40`) | `160 / 160` | **`0,00%`** | `198,17` | `170,40` | **`285,14`** | `306,59` | `335,17` | Tối ưu phân trang chỉ mục + định dạng ISO lười giúp drill-down toàn tháng `250k` Attempts đạt `p95 = 285,14 ms` ở `c=40`. |
| **`peak_40_staff` — `kpi_metric_drilldown_250k_api` (daily campaign)** (`c=40`) | `160 / 160` | **`0,00%`** | `90,85` | `364,01` | **`581,34`** | `645,91` | `658,84` | `0%` lỗi ở `c=40`, dưới xa ngưỡng `3.000 ms`. |
| **`peak_40_staff` — `telesale_worklist_page` (SSR)** (`c=40`) | `160 / 160` | **`0,00%`** | `27,76` | `1023,44` | **`2636,44`** | `2777,99` | `3023,99` | `16` luồng Gunicorn (`4×4`) phục vụ `160/160` SSR thành công (`0%` lỗi). |
| **Sóng hỗn hợp `sessions_20`** (`20` phiên đồng thời) | `200 / 200` | **`0,00%`** | `70,60` | `104,67` | **`337,39`** | `544,83` | `670,34` | Hỗn hợp `My Customers`, `Lead Detail`, `Record Attempt`, `BOD Overview`, `Drill-down`. |
| **Sóng hỗn hợp `sessions_40`** (`40` phiên đồng thời) | `320 / 320` | **`0,00%`** | `114,14` | `169,73` | **`625,04`** | `766,86` | `932,57` | Đạt dưới `1 giây` ở quy mô đỉnh `40` nhân sự hoạt động đồng thời. |
| **Sóng hỗn hợp `sessions_80`** (`80` phiên đồng thời — gấp đôi `S1`) | `480 / 480` | **`0,00%`** | `138,51` | `472,66` | **`1046,35`** | `1337,19` | `1606,96` | Ở `80` phiên đồng thời (`5×` số luồng Gunicorn `16`), cụ thể: `my_customers_api p95 = 706,15 ms`, `bod_overview_api p95 = 769,92 ms`, `kpi_drilldown_api p95 = 906,86 ms`, `record_attempt_write_api p95 = 1099,93 ms`, `lead_detail_history_api p95 = 1310,40 ms` (`0%` lỗi). |

---

## 3. Đo Kiểm Tác Vụ Nghiệp Vụ Lõi, Lô Lớn Bất Đồng Bộ & Tài Nguyên Thực Cấp

### 3.1. Độ trễ tầng dịch vụ nghiệp vụ trực tiếp trên tập `S1` (`domain_workloads_s1`)

| Tác vụ nghiệp vụ | Số mẫu | Lỗi | Ops/giây | `p50` (`ms`) | `p95` (`ms`) | `p99` (`ms`) | Bảo chứng toàn vẹn |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| Mở danh sách `Hồ sơ được giao` (`list_work_items`) | `60` | `0` | `562,54` | `1,61` | **`1,69`** | `6,17` | Dùng index `cskh_lookup_0`. |
| Mở chi tiết đầu việc + dòng thời gian liên hệ | `60` | `0` | `94,95` | `9,93` | **`11,55`** | `25,80` | Dùng index `cskh_attempt_wi_occ_idx`. |
| Ghi `Contact Attempt` + đặt `CRM Task` hẹn lại | `50` | `0` | `29,81` | `30,46` | **`36,60`** | `102,43` | `idempotent_replay_verified = true` (`0` nhân đôi). |
| Đọc `BOD Executive Overview` từ Snapshot | `40` | `0` | `293,10` | `2,93` | **`5,96`** | `6,22` | `contact_attempt_table_scans_during_read = 0`. |
| Drill-down KPI phân trang theo ngày/chiến dịch (`250k` Attempts) | `40` | `0` | `47,62` | `20,13` | **`24,33`** | `39,29` | Phân trang qua chỉ mục + tính phút làm việc lười trên đúng trang trả về. |
| Drill-down KPI phân trang toàn tháng toàn công ty (`250k` Attempts) | `10` | `0` | `38,60` | `26,07` | **`51,04`** | `58,72` | Giảm từ `54.389 ms` (trước tối ưu) xuống **`51,04 ms`** (cải thiện hơn `1.000×`). |

### 3.2. Nhập CSV `50.000` dòng, Phân phối `10.000` đầu việc, Tính lại KPI & Hàng đợi RQ
- **Nhập CSV bất đồng bộ `50.000` dòng (`csv_import_50000_rows`):**
  - Cơ cấu lô: `50.000` dòng tổng (`10.000` dòng mới hợp lệ, `39.500` dòng trùng, `500` dòng lỗi cú pháp SĐT; kết quả `created_rows = 10.000`, `reused_rows = 5.000`, `skipped_rows = 500`, `error_artifact_present = true`, `progress_checkpoints_count = 74`).
  - Thời gian: Xem trước (`preview`) mất **`509,77 ms`**; worker `queue-long` xử lý hoàn tất (`COMPLETED_WITH_ERRORS`) trong **`100.459,13 ms`**, tổng cộng **`100.968,90 ms` (`100,97 giây`, thông lượng `495,2 dòng/giây`)**, đỉnh RAM `preview_hwm_mib = 1.729,26 MiB`, đọc `My Customers` đồng thời không bị chặn (`p95 = 5,965 ms`).
- **Phân phối có trọng số `10.000` đầu việc cho `40` nhân sự (`campaign_distribution_10000_work_items_40_staff`):**
  - Xem trước (`preview`): **`546,25 ms`** (đạt yêu cầu tham chiếu `<= 60.000 ms`).
  - Thực thi bất đồng bộ trên `queue-long`: **`566.328,03 ms`** (`completed`), tạo đúng `10.000` `CSKH Work Item`, `0` bản ghi trùng (`double_submit_idempotent = true`, `abort_cancel_guard_verified = true`, `stale_docshares = 0`, `stale_open_todos = 0`, `ownership_reconciled = true`), đọc `My Customers` đồng thời **`p95 = 3,136 ms`**.
- **Tính lại KPI (`kpi_recompute_s1`):**
  - Tính mới toàn tháng (`75.000` Work Items / `250.000` Attempts / `150.000` SLA): **`27.168,71 ms` (`27,17 giây`)**, trạng thái `completed`, RAM sau chạy `783,99 MiB` (`HWM = 1.729,26 MiB`); khi chạy nền trên `queue-long` (`27.094,45 ms`), `175` lượt đọc UI đồng thời đạt **`p95 = 5,853 ms`** (`0%` lỗi) và revision chỉ chuyển từ `9d4qg8ou0b` sang `9me6r79leh` nguyên tử khi hoàn tất.
  - Tính tăng trưởng theo ngày (`daily incremental`): **`2.316,80 ms` (`2,32 giây`)**, RAM `783,03 MiB`.
- **Xả bùng nổ `50` job trên `queue-short`:** Đẩy vào hàng đợi mất `9,09 ms`, xả hết về `0` tồn đọng trong `60.036,06 ms` (`0,83 job/giây` trên 1 tiến trình worker đơn).

### 3.3. Tài Nguyên Container Thực Đo (Trước tải / Trong sóng `80` phiên / Sau tải) & Sức khỏe MariaDB
- **Sức khỏe MariaDB & Redis sau toàn bộ bài tải (`queue_and_db_after`):**
  - `Slow_queries = 0`, `Max_used_connections = 17`, `Innodb_row_lock_waits = 61` (`avg = 65 ms` trong đợt bắn đồng thời `80` lệnh ghi Attempt), `redis_queue_rtt_ms = 1,11 ms`, hàng đợi sau bài đo `short = 0, default = 0, long = 0` (`0` tồn đọng).
- **Bảng tiêu thụ CPU & RAM thực tế của 9 container trên hạn mức `4 vCPU / 15.61 GiB RAM`:**

| Container dịch vụ | Trước bài tải HTTP (`CPU` / `RAM`) | Trong sóng đỉnh `80` phiên đồng thời (`CPU` / `RAM`) | Sau toàn bộ bài tải (`CPU` / `RAM`) |
| :--- | :---: | :---: | :---: |
| `backend-1` (`Gunicorn 4×4`) | `0,01%` / `165,8 MiB` | **`198,09%` / `469,1 MiB`** (`2,93%`) | `0,01%` / `468,0 MiB` (`2,93%`) |
| `db-1` (`MariaDB 11.8`) | `0,02%` / `334,8 MiB` | **`93,55%` / `406,9 MiB`** (`2,55%`) | `17,65%` / `409,5 MiB` (`2,56%`) |
| `queue-short-1` (`RQ short,default`) | `100,97%` / `1,676 GiB` | **`69,05%` / `241,3 MiB`** (`1,51%`) | `97,46%` / `370,0 MiB` (`2,31%`) |
| `queue-long-1` (`RQ long`) | `0,05%` / `35,27 MiB` | `0,03%` / `35,27 MiB` (`0,22%`) | `0,04%` / `35,27 MiB` (`0,22%`) |
| `scheduler-1` | `0,00%` / `61,20 MiB` | `0,00%` / `61,20 MiB` (`0,38%`) | `0,00%` / `61,20 MiB` (`0,38%`) |
| `websocket-1` (`Socket.IO`) | `0,00%` / `23,43 MiB` | `0,40%` / `23,43 MiB` (`0,15%`) | `0,00%` / `23,43 MiB` (`0,15%`) |
| `redis-cache-1` | `0,16%` / `10,41 MiB` | `8,43%` / `11,35 MiB` (`0,07%`) | `1,81%` / `11,05 MiB` (`0,07%`) |
| `redis-queue-1` | `0,17%` / `6,32 MiB` | `0,42%` / `6,78 MiB` (`0,04%`) | `3,12%` / `6,61 MiB` (`0,04%`) |
| `frontend-1` (`Nginx`) | `0,00%` / `4,39 MiB` | `4,07%` / `10,01 MiB` (`0,06%`) | `0,00%` / `8,82 MiB` (`0,06%`) |

---

## 4. Điểm Nghẽn Đã Xác Định, Khuyến Nghị Cấu Hình VPS & Đề Xuất SLO Chờ Duyệt

### 4.1. Bốn đặc tính & giới hạn thực tế đã xác nhận (`observed_environment_limits`)
1. **Trần luồng Gunicorn (`GUNICORN_WORKERS=4, GUNICORN_THREADS=4 = 16 luồng`):** Qua 3 sóng `20`, `40` và `80` phiên người dùng đồng thời không có thời gian nghỉ (`zero-think-time`), sóng `20` (`p95 = 337,39 ms`) và `40` (`p95 = 625,04 ms`) đều dưới `1 giây`; ở `80` phiên đồng thời (`5×` số luồng Gunicorn), việc xếp hàng chờ 16 luồng làm tăng đuôi `p95` lên `1.046,35 ms` với **`0%` lỗi HTTP 5xx**.
2. **Tối ưu `get_metric_drilldown` trên tập `250.000` Attempts:** Sau khi áp dụng truy vấn phân trang qua chỉ mục SQL kết hợp tính toán phút làm việc lười (`lazy business-minute`) và định dạng ISO lười cho đúng trang hiện hành, độ trễ HTTP `p95` đạt **`202,30 ms`** (kỳ ngày/chiến dịch) và **`113,29 ms`** (kỳ toàn tháng toàn công ty `250k` Attempts, `<= 3.000 ms`).
3. **Tính lại KPI toàn tháng (`27,17 giây`, HWM `1,73 GiB`) & tăng trưởng ngày (`2,32 giây`):** Chạy bất đồng bộ trên `queue-long` không gây chặn đọc UI (`p95 = 5,853 ms`) và chỉ xuất bản revision mới nguyên tử khi hoàn tất.
4. **Tách biệt hàng đợi `queue-short` và `queue-long`:** Lô nhập CSV `50.000` dòng (`100,97 giây`) và phân phối có trọng số `10.000` đầu việc (`566,33 giây`) chạy hoàn toàn trên `queue-long` với `0` bản ghi trùng và giữ nguyên độ trễ đọc `My Customers` ở mức `3–6 ms`.

### 4.2. Khuyến nghị cấu hình máy chủ VPS & Tham số vận hành (`Sizing Recommendation`)
- **Quy mô hiện tại & MVP đến ngưỡng `S1` (`<= 40` nhân sự đồng thời):**
  - Cấu hình khuyến nghị: **`4 vCPU / 8–16 GB RAM / >= 80–120 GB NVMe SSD`** với `GUNICORN_WORKERS=4`, `GUNICORN_THREADS=4`, tách biệt `queue-short` và `queue-long`, đặt `innodb_buffer_pool_size = 2G–4G`.
  - **Lưu ý bảo toàn môi trường khi dựng Staging cô lập trên cùng VPS `103.77.208.186`:** Do VPS `103.77.208.186` đang phục vụ trực tiếp `https://crm.hrpartner.vn`, khi phiên `T1A` được cấp SSH chỉ dựng Staging cô lập để kiểm tra isolation, pull/inspect và HTTPS/WSS smoke chức năng; tuyệt đối không chạy bài bắn tải `S1` nặng trên cùng máy chủ vật lý đang phục vụ Production để bảo toàn `Zero Live Impact` (`P4.md §9.1`).
- **Lộ trình nâng cấp nếu vượt ngưỡng `S1` tiến tới `S2` (`250k` khách / `1,25M` Attempts / `100` staff):** Nâng lên `8 vCPU / 16–32 GB RAM`, tăng số worker `queue-long` lên `2–4` tiến trình và tách máy chủ MariaDB riêng khi cần HA.

### 4.3. Bảng Đề Xuất SLO Nội Bộ Để Owner / BOD Xem Xét Phê Duyệt (`PROPOSED_ONLY_NOT_COMMITTED_SLA`)

| Chỉ số đề xuất (`proposed_slos_for_review`) | Kết quả thực đo trên `S1` (`@sha256:5ea736da...`) | Ngưỡng SLO đề xuất trình duyệt | Trạng thái cam kết |
| :--- | :---: | :---: | :---: |
| `my_customers_worklist_api` (`p95`) | **`53,603 ms`** (`81,235 ms` ở `c=40`) | `<= 500 ms` (tham chiếu `P4.md`: `1.500 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Mở chi tiết Lead + lịch sử Attempt (`p95`) | **`130,815 ms`** | `<= 600 ms` (tham chiếu `P4.md`: `2.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Ghi `Contact Attempt` + `Follow-up` (`p95`) | **`363,193 ms`** (HTTP end-to-end) / `36,595 ms` (domain) | `<= 950 ms` (tham chiếu `P4.md`: `2.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| `bod_executive_overview_api` (`p95`) | **`68,903 ms`** (`207,281 ms` ở `c=40`) | `<= 800 ms` (tham chiếu `P4.md`: `3.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| `kpi_drilldown_paginated_api` (`p95`) | **`202,298 ms`** (ngày/chiến dịch) · **`113,294 ms`** (tháng/công ty `250k`) | `<= 1.200 ms` (tham chiếu `P4.md`: `3.000 ms`) | `CHỜ OWNER PHÊ DUYỆT` |
| Tỷ lệ lỗi `HTTP 5xx` trên API nghiệp vụ | **`0,00%`** | `<= 0,50%` | `CHỜ OWNER PHÊ DUYỆT` |
| Độ trễ làm mới KPI tăng trưởng trong ngày | **`2,317 giây`** (`2.316,8 ms` + chu kỳ cron `5 phút`) | `<= 300 giây` (`5 phút`) | `CHỜ OWNER PHÊ DUYỆT` |
