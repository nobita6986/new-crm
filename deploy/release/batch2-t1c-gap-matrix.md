# P4 Đợt 2 — Ma trận đối chiếu Tái sử dụng & Kiểm chứng Delta theo Gap `T1C` (`G06`, `G09`, `G11–G15`, `G17`, `G18`)

- **Ứng viên image Đợt 1 được khóa (không build lại trong Đợt 2):**
  - Nhánh Đợt 1: `codex/p4-t1b-release-hardening` — Commit SHA: `427af4834eda48a15ca86c15e88342c3c40d5f2f`
  - `apps` tree SHA: `14b5387296dad815a6b9f663411f265fa7d9f910` (`crm_cskh` tree SHA: `f51095ec245e103ed3a487654b5580636424ea36`)
  - Registry Manifest Digest đã kiểm định: `ghcr.io/nobita6986/crm-cskh@sha256:b1329ef7c26a9861a07d25d425eb4438150d065d2548c464e7d3395e568a201d`
  - Local Image ID (daemon CI): `sha256:9f295546fc78c061d950322b9c8f60d40312016b802f325b15f834524f500829`
  - OCI Config Digest: `sha256:9f295546fc78c061d950322b9c8f60d40312016b802f325b15f834524f500829`
- **Phạm vi an toàn:**
  - Không sửa `apps/**`, `deploy/compose/**`, `deploy/ops/**`, `deploy/images/**`.
  - Không hạ ngưỡng security, không nới assertion, không tự duyệt ngoại lệ CVE Critical, không tác động `crm.hrpartner.vn` (`acb95057…`).

---

## 1. Ma trận Tái sử dụng Evidence hợp lệ từ Đợt 1 & Kiểm chứng Delta Đợt 2

| Gate | Phạm vi theo `P4.md` & Gap `T1C` | Evidence hợp lệ tái sử dụng từ Đợt 1 (`Run 42625295772`) | Kiểm chứng Delta bổ sung tại Đợt 2 (`p4-batch2-delta.yml`) |
|---|---|---|---|
| **`G06`** | Migration từ clone `N-1`, `migrate` lặp, đối soát và `BLG-P4-MIG-01` | `G02` clean-site install + `60/60` smoke (`Artifact 13679851357`) | `verify_g06_g09_migration_and_rollback.py`: Dựng site `N-1` (`origin/main` `46783019`, Frappe `v15.123.1`) có dữ liệu P1/P2/P3 + `[H:<16hex>]` + Outcome/Policy `v2` + Export CSV -> backup `D10` -> nâng lên `N` (`@sha256:b1329ef7...`, Frappe `v15.126.0`) -> `migrator` lần 1 & lần 2 -> kiểm `BLG-P4-MIG-01`, SHA-256 bảng bất biến, phân loại tương thích schema (`Class A/B`) và đối soát P1/P2/P3. |
| **`G09`** | Rollback 4 tầng & đường forward-fix / restore | Unit/service tests cờ tắt P3 (`test_f_p3_08`) | `verify_g06_g09_migration_and_rollback.py`: Diễn tập HTTP thực tế Tier 2 (`cskh_p3_export_enabled=false`), Tier 1 (`cskh_p3_enabled=false` -> `/cskh/quality-overview` `403`, API P3 `503`, P1/P2 `200`), chặn fail-closed `D03` trước khi cắt luồng, **Tier 3 image rollback `N -> N-1` không hạ DB** (bảo toàn bản ghi ghi trên `N`), và **Tier 4 DR restore** trên site cô lập kèm kiểm kê delta sau backup. |
| **`G11`** | Khóa user, chuyển chủ sở hữu, đổi role/team không để lại quyền cũ trên topology hiện tại | Unit/RBAC tests `S07–S09` trong suite `421/421` | `verify_g11_g12_security_topology.py`: Đo trực tiếp trên **topology 10-service đơn VPS hiện tại** (không dựng thêm distributed cluster ngoài plan) qua HTTP `sid`, `redis-cache` (`tabSessions` + Redis hash `session`), và Socket.IO Engine.IO v4 + RFC 6455 WebSocket (`44/<site>` từ chối `sid` bị khóa, `doc_subscribe` `0` rò rỉ PII). |
| **`G12`** | Phân quyền, rate limit / payload limit, audit log và file/export (`S01–S20`) | Security/RBAC tests trong `421/421` (`Artifact 13679851357`) | `verify_g11_g12_security_topology.py`: Kiểm chứng live HTTP `S01–S20`: Guest/Outside/Cross-staff/Cross-team/BOD `0`-PII, CSRF `S10`, Desk bypass `S14`, giả mạo actor `S19`, giới hạn payload `26 MiB -> 413` (Nginx) & `11 MiB -> 413` (App) & `.exe`/corrupt `.xlsx` (`S11`), khóa đăng nhập sai liên tiếp + Traefik `HTTP 429` (`compose.cskh-edge-ratelimit.yaml`, `S20`) không làm nghẽn người dùng khác, tính bất biến bảng audit (`S15`), file/export TTL `410` + trung hòa CSV injection (`S12`, `S13`), header bảo mật (`S17`) và cô lập cổng nội bộ (`S18`). |
| **`G13`** | E2E P1 (`F03–F11`) trên Compose + `queue-long` | `F01`, `F02`, `F12` từ `G03` smoke + P1 Python tests (`121` tests) | `verify_g13_g14_g15_g17_delta.py`: Live HTTP + `queue-long` cho CSV (BOM, `+84`, dòng lỗi) & XLSX (`F03`, `F04`), RBAC file lỗi, replay idempotent (`F05`), `409 RULE_CHANGED` & `PREVIEW_EXPIRED` (`F07`), chia `1.000` hồ sơ tỷ lệ `50/30/20 -> 500/300/200` + cộng dồn `3 x 10 -> 15/9/6` (`F08`), RBAC kho/danh sách (`F09`), và 2 Admin bấm `execute` đồng thời (`F11`/`D08`). |
| **`G14`** | E2E P2 (`F13–F20`) trên Compose + `scheduler` | `F12` từ `G03` smoke + P2 Python tests (`174` tests) | `verify_g13_g14_g15_g17_delta.py`: Đa kênh (`no_answer` + Manual Call Log -> `self_reported`; PBX `Twilio` Call Log -> `verified` theo `SEC2-02`; `zalo` `call_log IS NULL`), chặn gắn trùng Call Log `409`, loại trừ `FCRM Note` (`F13`, `F14`), dời lịch giữ nguyên `original_due_at` + đánh giá SLA lặp `0` nhân bản (`F15`), replay/conflict/stale revision (`F16`), đóng/mở lại & chặn đóng khi chưa liên hệ (`F17`), DNC `409` (`F18`), đính chính `void`/`restore` & bất biến snapshot nhãn Outcome (`F19`, `F20`). |
| **`G15`** | E2E P3 (`F21–F27`) bằng Golden Dataset & `cskh-metrics-v2` | Suite Python `TestP3T1CRemediation` (`Artifact 13679851357`) | `verify_g13_g14_g15_g17_delta.py`: Nạp `t1c_p3_golden_dataset_expected.json` (`SHA256 deb6be25...`) trên site Compose sạch -> kiểm 9 vector lịch làm việc + `EMPTY_CALENDAR`, khớp `16/16` scope `cskh-metrics-v2` (`qa_enabled=false` `86.47` provisional & `qa_enabled=true` `87.0` final, `null` `insufficient_data`, `A->B->A` interval vs distinct), tính lại 2 lần append-only, đối soát đủ **5 độ mịn kỳ** (`daily`, `weekly`, `monthly`, `quarterly`, `yearly`), cùng live HTTP các API báo cáo, vòng đời SLA (`pause`/`resume`/`waive`) và xuất CSV bất đồng bộ qua `queue-long`. |
| **`G17`** | Giết worker đúng lúc đang xử lý job, retry & đối soát (`D04–D06`, `D12`, `D14`, `D15`) | Đối soát DB ở mức service test | `verify_g13_g14_g15_g17_delta.py`: Bắn `docker kill -s KILL` (`ExitCode 137`) trực tiếp vào container `queue-long` giữa lô import `1.200` dòng (`D04` -> resume `1.200` hồ sơ, `0` trùng SĐT), vào `queue-long` giữa lần phân bổ `600` hồ sơ (`D05` -> hết hạn lease, tiếp quản qua HTTP `execute`, đối soát `600/600`), và vào `queue-short` giữa lúc tính KPI (`D06` -> snapshot `R1` nguyên vẹn `200`, khóa Redis trả `409`, retry hoán đổi nguyên tử sang `R3`), kèm restart `redis-cache`/`redis-queue`/`scheduler`/`queue-*` (`D14`, `D15`) và kiểm bất biến SQL `D12`. |
| **`G18`** | Benchmark cô lập trên dataset tham chiếu `S1` | **Đã chạy hoàn tất ở Job 3 `Run 42625295772`** (`Artifact 13680670793`, ZIP SHA256 `792c6f72...`) | Tái sử dụng nguyên vẹn báo cáo thực đo `benchmark-s1-report.json` (`SHA256 04409d7a...`) trên đúng digest `@sha256:b1329ef7...`; công bố rõ giới hạn đo và đề xuất SLO bên dưới. |

---

## 2. Hợp đồng vận hành Rollback Tier 3 vs Forward-Fix / Tier 4 Restore (`G09`)

1. **Tier 1 & Tier 2 (Cờ tính năng P3 — RTO < 60 giây, `0` mất dữ liệu):**
   - Dùng khi lỗi khu trú ở báo cáo P3, SLA, QA hoặc Export (`cskh_p3_enabled=false` hoặc `cskh_p3_export_enabled=false` + `bench clear-cache`).
   - Toàn bộ luồng P1/P2 tiếp tục ghi bình thường và tự xếp hàng `CSKH Dirty Scope` để tính bù khi bật lại.
2. **Tier 3 (Quay lui Application Image `N -> N-1` theo `@sha256` — áp dụng cho thay đổi `Class A` / `Class B`, `0` mất dữ liệu):**
   - Áp dụng khi bản `N` chỉ thay đổi code/dependency/OS (`Class A`) hoặc chỉ thêm bảng/cột cho phép `NULL`/có `DEFAULT` (`Class B` — như ứng viên Đợt 1 hiện tại).
   - Quy trình: Trỏ `APP_IMAGE` về digest `N-1`, chạy `docker compose up -d --no-build --force-recreate backend frontend websocket queue-short queue-long scheduler` và `bench --site <site> clear-cache`. **Tuyệt đối không** hạ cấp MariaDB hay xóa cột/bảng thủ công.
3. **Đường Forward-Fix hoặc Tier 4 Restore (bắt buộc khi migration thuộc `Class C` biến đổi dữ liệu phá hủy hoặc `Class D` xóa/đổi kiểu cột không tương thích ngược):**
   - **Ưu tiên 1 — Forward-Fix (`N+1`):** Bật chế độ bảo trì / tạm dừng ghi nếu có nguy cơ lệch dữ liệu -> đóng gói image bản vá tiến (`N+1`) kèm patch sửa dữ liệu idempotent -> kiểm định và promote theo `@sha256` để không mất bất kỳ giao dịch nào phát sinh sau cutover.
   - **Phương án cuối — Tier 4 Full DR Restore (chỉ thực hiện khi có phê duyệt tường minh của Owner `T0`):**
     1. Truy vấn và xuất biên bản đối soát toàn bộ giao dịch phát sinh sau mốc `pre_migrate_backup_utc` (`CSKH Contact Attempt`, `CRM Call Log`, `CRM Task`, `CSKH Assignment History`).
     2. Khôi phục bản sao lưu trước nâng cấp (`bench --site <site> --force restore <db.sql.gz> --with-private-files <priv.tar> --with-public-files <pub.tar>` + `encryption_key` khớp).
     3. Khởi chạy lại trên image `N-1`, chạy đối soát P1/P2/P3 và phối hợp nghiệp vụ nhập bù cửa sổ delta đã kiểm kê.

---

## 3. Giới hạn đo trên Topology hiện tại (`G11`, `G12`, `G18`)

1. **Giới hạn kiến trúc phiên Socket.IO của upstream Frappe v15 (`G11`):**
   - Trên topology 10-service đơn VPS hiện tại (`proxy` -> `frontend` -> `backend` + `websocket` + `redis-cache` + `redis-queue`), khi khóa user (`enabled=0`) hoặc thu hồi phiên:
     - Mọi request HTTP dùng cookie `sid` cũ bị chặn **ngay lập tức** (`401`/`403`/`302`), `tabSessions` về `0`, và hash `session` trên `redis-cache` bị xóa.
     - Mọi kết nối hoặc tự động kết nối lại (reconnect) Socket.IO bằng `sid` cũ bị từ chối **ngay lập tức** ở bước bắt tay namespace (`44/<site>`).
     - Một kết nối TCP WebSocket đã mở sẵn từ trước khi khóa user không bị Frappe tự đóng cưỡng bức từ phía server nếu chỉ rảnh (idle), nhưng mọi yêu cầu đăng ký tài liệu mới (`doc_subscribe`) đều gọi lại `can_subscribe_doc` sang `backend` với `sid` đã thu hồi nên bị chặn và **không rò rỉ PII**. Nếu về sau yêu cầu ngắt tức thời cả TCP socket đang mở, cần bổ sung cơ chế phát sự kiện ngắt phiên tới tiến trình Node `websocket` (ngoài phạm vi code `apps/**` của đợt này).
2. **Tập dữ liệu tham chiếu `S1` & Giới hạn năng lực thực đo (`G18` — Artifact `13680670793`):**
   - `S1` là tập dữ liệu trạng thái tích lũy tham chiếu: **`50.000` hồ sơ (`CRM Lead`), `75.000` `CSKH Work Item`, `250.000` `CSKH Contact Attempt`, `150.000` `CSKH SLA Obligation`, `40` nhân sự hoạt động** — **KHÔNG phải** cam kết thông lượng `50.000` hồ sơ/tháng.
   - Môi trường đo cô lập: Runner `4 vCPU / 15.6 GiB RAM`, cấu hình mặc định `GUNICORN_WORKERS=2, GUNICORN_THREADS=4` (`8` luồng Gunicorn), `Slow_queries = 0`.
   - Số đo tiêu biểu:
     - Mức đồng thời thực tế `c=12`: `customers.my_list` `p95 = 38.39 ms` (`502.98 rps`, `0%` lỗi); `get_executive_overview` `p95 = 89.88 ms` (`216.74 rps`, `0%` lỗi); ghi nhận liên hệ + tạo lịch hẹn `p95 = 30.24 ms`; SSR `/cskh/my-customers` `p95 = 887.19 ms`.
     - Mức dồn xung `c=40` (`0` think-time): API JSON đọc danh sách & tổng quan vẫn `0%` lỗi (`p95 = 105.00 ms` và `221.70 ms`), SSR `/cskh/my-customers` `p95 = 2.913,37 ms` (`0%` lỗi). Riêng truy vấn `get_metric_drilldown` quét trực tiếp `250.000` lượt liên hệ khi bắn đồng thời `40` request không nghỉ qua `8` luồng Gunicorn chạm ngưỡng chờ client `30s` (`85%` client timeout, `0%` HTTP 5xx; ở `c=12` đạt `0%` lỗi với `p95 = 18.865,25 ms`).
     - Tác vụ nền trên `S1`: Tính lại KPI đầy đủ kỳ tháng (`75k WI / 250k Attempts`) mất `68,59s` (`HWM 1.724,92 MiB`); tính lại kỳ ngày mất `16,32s`; import `500` dòng mất `15,64s` (`31,96 dòng/s`); phân bổ `500` hồ sơ cho `40` nhân sự mất `19,26s`.
   - **Đề xuất SLO thực tế khi vận hành (chờ Owner `T0` phê duyệt, không tự cam kết SLA khi chưa đo trên VPS đích):**
     - Với cấu hình `2 workers x 4 threads` trên máy chủ `>= 4 vCPU / 16 GiB RAM`: API JSON tác nghiệp (`my_list`, `get_executive_overview`, `record_attempt`) đề xuất SLO `p95 <= 300 ms` ở tải thường trực (`c <= 12`) và `p95 <= 600 ms` ở xung ngắn (`c <= 40`), tỷ lệ lỗi HTTP 5xx `< 0,5%`; SSR trang `/cskh/*` `p95 <= 1.500 ms` ở `c <= 12`.
     - Với `get_metric_drilldown` trên kỳ lớn (`>= 250k` attempts): giới hạn đồng thời ở tầng proxy (`compose.cskh-edge-ratelimit.yaml`) hoặc nâng `GUNICORN_WORKERS=4` khi triển khai trên VPS đủ RAM, kết hợp lọc theo ngày/chiến dịch/nhóm.
