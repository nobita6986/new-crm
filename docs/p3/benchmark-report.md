# P3 Synthetic Performance & Index Benchmark Report (`P3-09`, `OBS-P2-02`)

> **Phụ trách:** T1B (Backend & CI)
> **Công cụ chạy lại:** `apps/crm_cskh/crm_cskh/tools/p3_benchmark.py`
> **Bằng chứng dữ liệu thô:** `deploy/dev/evidence/p3-t1b-20261104/p3-benchmark-results.json`
> **Tham chiếu:** `docs/root-plan/P3.md` §14.5, `docs/architecture/ADR-P3-07-read-model-and-incremental-recompute.md`

> [!IMPORTANT]
> **Tuyên bố trung thực về môi trường đo (DEV Single-Host Disclaimer):**
> Toàn bộ số liệu dưới đây được đo bằng dữ liệu giả lập định sẵn trên một máy chủ Docker DEV đơn lẻ (`WSL2 Linux 6.18.54`, `12` vCPU, `MariaDB 11.8.9`, `Frappe v15.123.1`, `Python 3.14.8`) tại site tạm thời cô lập `p3bench.localhost` (đã xóa sạch ngay sau khi đo, tuyệt đối không chạm dữ liệu Owner). Số liệu dùng để kiểm chứng độ phức tạp thuật toán, kế hoạch thực thi index (`EXPLAIN`), và đóng điểm quan sát `OBS-P2-02` — **KHÔNG phải cam kết SLA hạ tầng production** (sẽ được đo tải thực tế ở Phase P4).

---

## 1. Cấu hình dữ liệu giả lập hai mốc `10k` và `100k` Work Items

Dữ liệu trải đều trong kỳ tháng `2026-10` qua `4` Team (`20` nhân viên CSKH, `4` Team Leader, `1` BOD Viewer, `1` CRM Admin) và `5` Chiến dịch (`4` chiến dịch theo team + `1` chiến dịch dùng chung), có chuyển giao quyền sở hữu (`reassign` trên `10%` hồ sơ):

| Hạng mục bản ghi | Mốc 1 (`10k_work_items`) | Mốc 2 (`100k_work_items`) |
|---|---:|---:|
| `CRM Lead` | `10.000` | `100.000` |
| `CSKH Work Item` | `10.000` | `100.000` |
| `CSKH Assignment History` (Ownership Intervals) | `11.000` | `110.000` |
| `CSKH Contact Attempt` (`2,5` lượt/hồ sơ) | `25.000` | `250.000` |
| `CSKH SLA Obligation` (`2,0` nghĩa vụ/hồ sơ) | `20.000` | `200.000` |

---

## 2. Xác minh xử lý triệt để `OBS-P2-02` — Độ trễ đọc Dashboard (`N = 20` lượt warm)

Nhờ tầng Read Model `CSKH KPI Snapshot` và các composite index `idx_cskh_kpisnap_lookup` / `idx_cskh_kpisnap_rank`, **cả 5 API Dashboard đều thực thi `0` câu truy vấn lên bảng `tabCSKH Contact Attempt`** (`obs_p2_02_verified_zero_attempt_scans_on_dashboard = true`), giữ độ trễ đọc phẳng `O(1)` (`~8–25 ms`) bất kể quy mô `25.000` hay `250.000` Contact Attempts:

| Endpoint | Actor | Mốc `10k` `p50` (`ms`) | Mốc `10k` `p95` (`ms`) | Mốc `100k` `p50` (`ms`) | Mốc `100k` `p95` (`ms`) | Số SQL / request | Quét `tabCSKH Contact Attempt`? |
|---|---|---:|---:|---:|---:|---:|---|
| `get_executive_overview` (`monthly`) | `BOD Viewer` | `9,919` | `12,044` | `7,528` | `8,498` | `5` | **Không (`false`)** |
| `get_team_comparison` (`monthly`) | `BOD Viewer` | `13,762` | `16,440` | `10,480` | `11,933` | `6` | **Không (`false`)** |
| `get_employee_quality` (`monthly`, sort `PROCESS_SCORE`) | `BOD Viewer` | `16,198` | `25,080` | `13,363` | `17,776` | `5` | **Không (`false`)** |
| `get_campaign_quality` (`monthly`) | `BOD Viewer` | `12,571` | `17,192` | `8,951` | `11,659` | `5` | **Không (`false`)** |
| `get_my_scorecard` (`monthly`) | `Staff` | `8,361` | `9,574` | `9,446` | `13,293` | `6` | **Không (`false`)** |
| `get_metric_drilldown` (`daily`, `company`, `page_size=20`) | `BOD Viewer` | `248,750` | `270,632` | `4.003,514` | `4.247,462` | `19` / `33` | Có (đối soát bản ghi nguồn theo kỳ) |
| `get_metric_drilldown` (`monthly`, `campaign`, `page_size=20`, `N=10`) | `CRM Admin` | `728,629` | `870,945` | `7.408,398` | `7.599,389` | `21` / `52` | Có (đối soát bản ghi nguồn theo kỳ) |

---

## 3. Thời gian & bộ nhớ tiến trình Recompute (Full vs Incremental)

Mọi tiến trình tính toán được thực thi bất đồng bộ ở background worker (tuyệt đối không chạy đồng bộ trong transaction `record_attempt`), kiểm tra đối soát tự động sau tính toán (`reconciliation_ok = true` trên `100%` lượt chạy):

| Tác vụ Recompute | Mốc | Wall-clock (`ms`) | Python Peak Heap `tracemalloc` (`MiB`) | Linux `VmRSS` Trước → Sau (`MiB`) | `VmHWM` (`MiB`) | Số Snapshot ghi | Đối soát (`reconciliation_ok`) |
|---|---|---:|---:|---:|---:|---:|---|
| **Full Monthly (`2026-10`, toàn công ty)** | `10k` | `5.143,83` | `144,92` | `145,49 → 256,72` | `301,92` | `34` | `true` |
| **Single Campaign Monthly (`2026-10`)** | `10k` | `981,66` | — | `339,83 → 339,84` | `434,74` | `30` | `true` |
| **Incremental Single-Day (`2026-10-15`)** | `10k` | **`420,40`** (`nhanh gấp 12,2x`) | **`7,87`** (`nhẹ gấp 18,4x`) | `339,84 → 338,84` | `434,74` | `34` | `true` |
| **Dirty-Scope Multi-Grain Drain (`5` chu kỳ)** | `10k` | `16.301,53` | — | `338,84 → 382,85` | `434,74` | `5` runs | `true` |
| **Full Monthly (`2026-10`, toàn công ty)** | `100k` | `52.079,85` | `1.431,67` | `273,94 → 685,45` | `1.791,96` | `34` | `true` |
| **Single Campaign Monthly (`2026-10`)** | `100k` | `10.491,68` | — | `976,25 → 1.038,23` | `3.043,16` | `30` | `true` |
| **Incremental Single-Day (`2026-10-15`)** | `100k` | **`5.622,11`** (`nhanh gấp 9,3x`) | **`75,89`** (`nhẹ gấp 18,9x`) | `984,24 → 984,25` | `3.043,16` | `34` | `true` |
| **Dirty-Scope Multi-Grain Drain (`5` chu kỳ)** | `100k` | `173.234,06` | — | `983,25 → 1.086,24` | `3.043,16` | `5` runs | `true` |

---

## 4. Kế hoạch thực thi MariaDB `EXPLAIN` (`0` quét toàn bảng `type = ALL`)

Kết quả `EXPLAIN` thực tế trên `MariaDB 11.8.9` ở cả hai mốc `10k` và `100k`:

| Nhóm truy vấn | Bảng đích | `type` | Index được chọn (`key`) | `rows` ước tính ở `10k` | `rows` ước tính ở `100k` | `Extra` |
|---|---|---|---|---:|---:|---|
| Đọc snapshot tổng quan / so sánh | `tabCSKH KPI Snapshot` | `ref` | `idx_cskh_kpisnap_lookup` | `24` | `24` | `Using index condition` |
| Xếp hạng `PROCESS_SCORE` có phân trang | `tabCSKH KPI Snapshot` | `ref` | `idx_cskh_kpisnap_rank` | `48` | `48` | `Using where` |
| Nạp `Contact Attempt` theo khoảng ngày | `tabCSKH Contact Attempt` | `range` | `idx_cskh_attempt_occ_rec` | `925` | `18.962` | `Using index condition` |
| Quét nghĩa vụ SLA quá hạn cho cron | `tabCSKH SLA Obligation` | `range` | `idx_cskh_sla_status_due` | `1` | `1` | `Using index condition; Using where` |
| Nạp nghĩa vụ SLA theo chiến dịch & mốc neo | `tabCSKH SLA Obligation` | `range` | `idx_cskh_sla_camp_anchor` | `7.866` | `75.962` | `Using where; Using index` |

---

## 5. Nhận xét kỹ thuật & khuyến nghị chuyển giao sang Phase P4

1. **Dashboard mở tức thời (`OBS-P2-02` đóng hoàn toàn):** việc đọc từ `CSKH KPI Snapshot` với con trỏ `is_latest_published = 1` giúp mọi lượt tải trang tổng quan BOD, bảng so sánh Team, bảng xếp hạng Nhân viên và Scorecard cá nhân đạt `p95 < 26 ms` ngay cả khi bảng lịch sử có `250.000` lượt liên hệ.
2. **Tuyến tính hóa chi phí tính lại (`O(N)`):** thời gian tính lại toàn tháng tăng tuyến tính chính xác `10,1x` khi dữ liệu tăng `10x` (`5,14 s` ở `10k` → `52,08 s` ở `100k`), không có hiện tượng bùng nổ bậc hai `O(N^2)` hay `N+1` query. Tính lại tăng trưởng theo ngày (`daily_single_day_incremental`) giảm `~10x` thời gian và `~19x` bộ nhớ Python heap.
3. **Điểm lưu ý trung thực cho Phase P4:**
   - Truy vấn `get_metric_drilldown` trên cửa sổ rộng (`monthly` ở quy mô `100k` Work Items / tháng) hiện đối soát trực tiếp tập bản ghi hiệu lực trong cửa sổ thời gian để bảo đảm khớp tuyệt đối tử/mẫu số tại `as_of` và kiểm tra quyền PII từng dòng, dẫn tới `p95 ≈ 4,25 s` (daily company) – `7,60 s` (monthly campaign) ở mốc `100k`. Ở quy mô vừa/nhỏ (`10k` Work Items / tháng), `p95` đạt `271 ms` – `871 ms`. Khi chuẩn bị vận hành tải lớn ở Phase P4, có thể bổ sung bảng ánh xạ thành viên mẫu số (`snapshot_drilldown_index`) hoặc đẩy phân trang SQL cho các chỉ số SLA thuần nếu lưu lượng vượt ngưỡng `50k+` hồ sơ/tháng.
   - Worker chạy batch `monthly`/`quarterly`/`yearly` ở quy mô `100k` hồ sơ/tháng tiêu thụ đỉnh `~1,43 GiB` Python heap (`VmHWM ~1,79–3,04 GiB`), do đó cấu hình container worker ở P4 cần giới hạn RAM tối thiểu `>= 4 GiB` hoặc tách tính toán theo phân đoạn ngày nếu quy mô tiếp tục tăng gấp nhiều lần.
