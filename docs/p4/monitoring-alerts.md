# Giám Sát Kỹ Thuật, Bất Biến Nghiệp Vụ & Quy Trình Cảnh Báo Theo Topology Thực Tế (`P4-07` — Cổng `G17`, `G19`)

- **Gói công việc:** `P4-07` (`P4.md §10`, `§20.2`)
- **Quyết định kiến trúc liên kết:** [`docs/p4/P4-ADR-08-observability-and-invariants.md`](./P4-ADR-08-observability-and-invariants.md)
- **Kịch bản thực thi & Bộ lập lịch định kỳ:**
  - Trình thăm dò sức khỏe & bất biến: [`deploy/ops/check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py)
  - Cài đặt timer định kỳ trên VPS (có gắn `compose.domain.yaml`): [`deploy/ops/install_systemd_timers.py`](../../deploy/ops/install_systemd_timers.py) (`crm-cskh-healthcheck.service` / `crm-cskh-healthcheck.timer`)
  - Kiểm thử đơn vị: [`deploy/ops/test_ops_toolkit.py`](../../deploy/ops/test_ops_toolkit.py) & [`deploy/ops/test_offsite_restore.py`](../../deploy/ops/test_offsite_restore.py)
- **Hiện trạng đã đo trên VPS `103.77.208.186` (`crm-staging.localhost`, cập nhật `09/11/2026`):**
  - `scheduler` đã bật; bộ hẹn giờ `crm-cskh-healthcheck.timer` đã cài đặt qua `install_systemd_timers.py --install`, vượt qua `systemd-analyze verify` (`0` cảnh báo) và **đã tự kích hoạt theo lịch lúc `20:05:45 +07` ngày `09/11/2026` (`ExecMainStatus=0`)** ([`timers-runtime.json`](../../deploy/ops/evidence/p4-vps-live-20261109/timers-runtime.json)).
  - Báo cáo thực chạy tự động [`live-health-after.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-health-after.json) (SHA-256 `c33647f44c354733876509639c45374cf333f7421dbcc132fe1b8078ce5b7182`) xác nhận: 10 daemon container `healthy`, ổ đĩa `23.79%`, hàng đợi RQ `0` lỗi, 4 cron `crm_cskh` `Complete`, cả 6 bất biến nghiệp vụ `INV-01..06` đạt **`PASS`**, và độ tươi bản sao lưu `age_hours = 0.086` (`PASS` cục bộ; `end_to_end_backup_gate_g07_status = PARTIAL_PERIODIC_OFFSITE_UNPROVEN`).
  - **Cổng gửi cảnh báo ngoài hệ thống (`G19`) đang được giữ khóa chủ động (`BLOCKED` bởi `BLK-GOV-06` & `BLK-INFRA-07`):** Tệp `/etc/crm-cskh/alerting.env` chưa được tạo (`external_alerts_sent = false`), hệ thống chỉ ghi báo cáo JSON cục bộ + nhật ký `journald`, **tuyệt đối chưa tự động gửi email, tin nhắn chat hay webhook ra ngoài** cho tới khi Owner chỉ định chính thức đầu mối và kênh nhận cảnh báo.

---

## 1. Tín Hiệu Kỹ Thuật Và Ngưỡng Cảnh Báo Khởi Điểm (`P4.md §10.1`)

| Nhóm | Tín hiệu đo | Nguồn thu thập | Ngưỡng Cảnh báo (`WARN` / `SEV-2`) | Ngưỡng Nghiêm trọng (`CRIT` / `SEV-1`) | Hành động đầu tiên |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Website & TLS** | Tỷ lệ `5xx`, độ trễ `/api/method/crm_cskh.api.health.ping`, hạn chứng chỉ TLS `crm.hrpartner.vn` | Traefik / `staging_https_smoke_and_reconcile.py` | Độ trễ ping `> 2.0s`; hạn TLS `< 21 ngày` | `5xx > 1%` trong `5 phút`; hạn TLS **`< 14 ngày`** | Kiểm tra log `proxy`/`frontend`/`backend`, kiểm tra gia hạn ACME trong `cert-data`. |
| **Containers** | `State`, `Health`, `RestartCount`, `State.OOMKilled` trên 10 service thường trực (kèm `compose.domain.yaml`) | `check_health_and_invariants.py` (`docker compose ps` + `inspect`) | `RestartCount > 0` trong cửa sổ theo dõi | Bất kỳ service `!= healthy` hoặc **`OOMKilled == true`** | Xem `docker logs --tail 100 <service>`, thực hiện khởi động lại an toàn theo [`operations-sop.md`](./operations-sop.md) Mục 2. |
| **CPU & RAM** | Mức tải duy trì trên host VPS & swap | `vmstat` / `free -m` / `docker stats --no-stream` | CPU `> 85%` liên tục `15 phút` | RAM **`> 90%`** hoặc phát sinh swap thrashing | Kiểm tra tiến trình `queue-long` (`import`/`export`/`recompute`) và số kết nối MariaDB. |
| **Ổ đĩa (Disk)** | `%` dung lượng & `%` inode của phân vùng chứa Docker volumes & `/var/backups/crm-cskh` | `check_health_and_invariants.py` (`shutil.disk_usage`) | **`>= 80%`** dung lượng hoặc inode | **`>= 90%`** dung lượng hoặc inode | Chạy `manage_backup_lifecycle.py prune-retention`, dọn tệp xuất hết hạn, kiểm tra log xoay vòng. |
| **MariaDB** | Kết nối đang mở (`Threads_connected`), `Innodb_row_lock_waits`, slow queries | `db` healthcheck & `SHOW GLOBAL STATUS` | `Threads_connected > 75%` `max_connections` | Hàng đợi khóa dòng tăng liên tục / DB không phản hồi | Tạm hoãn job lô lớn (`import`/`recompute`), kiểm tra câu lệnh giữ khóa lâu. |
| **Backup (`P4-05`)** | Thời điểm hoàn tất gói sao lưu mã hóa hằng ngày, mã kiểm tra SHA-256 & biên nhận offsite | `backup-report.json` / `offsite-verification.json` ([`backup-restore.md`](./backup-restore.md)) | Trễ `> 2 giờ` so với lịch `02:15 ICT` | Quá **1 chu kỳ ngày** chưa có gói sao lưu hợp lệ hoặc lệch SHA-256 | Chạy `create_encrypted_backup.py` thủ công ngay và chép ra kho ngoài VPS (`BLK-INFRA-05`). |

---

## 2. Theo Dõi Hàng Đợi RQ & Nhịp Đập Scheduler (`G17`)

Căn cứ cấu hình thực tế tại [`apps/crm_cskh/crm_cskh/hooks.py`](../../apps/crm_cskh/crm_cskh/hooks.py), `check_health_and_invariants.py` giám sát trực tiếp:

1. **Ràng buộc một `scheduler` duy nhất:** `scheduler_running_instances == 1` và `frappe.utils.scheduler.is_scheduler_disabled() == False`.
2. **Hàng đợi Redis (`short`, `default`, `long`):** Đọc số lượng job đang chờ (`q.count`) và số lượng job thất bại trong `FailedJobRegistry` (`failed > 0` kích hoạt mã `ALT-TECH-QUEUE-SCHEDULER`).
3. **Độ tươi và trạng thái hoàn tất của 4 tác vụ nền `crm_cskh` trong `tabScheduled Job Log` (ngưỡng tối đa = `2 × chu kỳ`, có cửa sổ khởi động `--startup-grace-seconds`):**
   - Báo `FAIL` ngay nếu sau cửa sổ khởi động có bất kỳ job nào chưa từng hoàn tất (`never_completed_cron_jobs`), vượt ngưỡng trễ tối đa (`stale_cron_jobs`), hoặc lần chạy gần nhất có trạng thái **`Failed`** (`failed_latest_cron_jobs`):
   - `crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations` (`*/5 * * * *`) → cảnh báo nếu trễ **`> 600 giây` (`10 phút`)**.
   - `crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes` (`*/5 * * * *`) → cảnh báo nếu trễ **`> 600 giây` (`10 phút`)**.
   - `crm_cskh.jobs.run_export.expire_stale_exports` (`hourly`) → cảnh báo nếu trễ **`> 7.200 giây` (`120 phút`)**.
   - `crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods` (`daily`) → cảnh báo nếu trễ **`> 172.800 giây` (`48 giờ`)**.
4. **Đột biến lỗi ứng dụng:** Đếm số bản ghi mới trong `tabError Log` của 15 phút gần nhất (chỉ xuất con số đếm, không trích xuất nội dung traceback thô ra kênh cảnh báo).

---

## 3. Sáu Bất Biến Toàn Vẹn Nghiệp Vụ Bắt Buộc (`P4.md §10.2`)

Tất cả truy vấn dưới đây thực thi chỉ đọc bên trong `check_health_and_invariants.py`, trả về duy nhất số nguyên (`PASS` khi toàn bộ bằng `0`):

| Mã Bất biến | Quy tắc nghiệp vụ bảo vệ | Điều kiện `PASS` | Mức độ khi vi phạm |
| :--- | :--- | :--- | :---: |
| **`INV-01`** | **Độc quyền chủ sở hữu đang hoạt động & đồng bộ `lead_owner`:** Không có `CRM Lead` (`Hồ sơ liên hệ`) nào gắn `> 1` `CSKH Work Item` ở trạng thái `assigned`/`in_progress`/`on_hold`; không lệch giữa `CSKH Work Item.assigned_to` và `CRM Lead.lead_owner`; tổng `CSKH Active Claim` khớp tuyệt đối số Work Item đang sở hữu. | `duplicate_active_leads = 0`<br>`owner_mismatch_active_work_items = 0`<br>`active_claim_count_mismatch = 0` | **`SEV-1`** |
| **`INV-02`** | **Chống nhân đôi `Contact Attempt` & bảo vệ trạng thái xác minh:** Không có cặp `(work_item, performed_by, idempotency_key)` bị trùng; không có bản ghi `verification_status = 'verified'` nào thiếu cả `call_log` lẫn phiếu `CSKH QA Review` có `status = 'published'` gắn đúng theo `contact_attempt`. | `duplicate_attempt_idempotency_keys = 0`<br>`unverified_marked_verified = 0` | **`SEV-1`** |
| **`INV-03`** | **Toàn vẹn công việc hẹn lại (`CRM Task` callback):** Mọi `CRM Task` đang mở (`Backlog`/`Todo`/`In Progress`) có `cskh_is_followup = 1` đều gắn với một `CSKH Work Item` đang hoạt động và có `assigned_to` khớp chủ sở hữu hiện tại. | `orphan_or_misassigned_open_followup_tasks = 0` | **`SEV-2`** |
| **`INV-04`** | **Sức khỏe bộ máy SLA:** Không trùng `idempotency_key` trên `tabCSKH SLA Obligation`; khi cờ hợp nhất `p3_sla_enabled = true`, không có nghĩa vụ `open` (không tạm dừng) đã quá hạn `effective_due_at > 15 phút` mà chưa được đánh giá chuyển trạng thái. | `duplicate_sla_idempotency_keys = 0`<br>`stale_unevaluated_overdue_sla = 0` | **`SEV-2`** |
| **`INV-05`** | **Đối soát `KPI Snapshot` & tiến độ `Dirty Scope`:** Khi `p3_reporting_enabled = true`, không có `CSKH Dirty Scope` bị kẹt `pending`/`processing` quá `30 phút`; đọc cấu trúc JSON của `metrics_json` xác nhận mọi `CSKH KPI Snapshot` có `QA_PASS_RATE.denominator = 0` đều giữ `QA_PASS_RATE.value = null`. | `stuck_dirty_scopes_over_30m = 0`<br>`false_zero_qa_snapshots = 0` | **`SEV-2`** |
| **`INV-06`** | **Thu hồi đúng hạn tệp xuất báo cáo (`Export TTL`):** Không có `CSKH Export Job` nào còn giữ `status = 'completed'` sau mốc `expires_at + 2 giờ`. | `unexpired_completed_exports_past_2h_grace = 0` | **`SEV-2`** |

---

## 4. Cài Đặt Kiểm Tra Sức Khỏe Định Kỳ Trên VPS & Cơ Chế Khóa Cảnh Báo Ngoài

### 4.1. Cài đặt `crm-cskh-healthcheck.timer` theo đúng topology `compose.domain.yaml`
Sau khi đã chuẩn bị thư mục mã nguồn vận hành `/opt/crm-cskh-ops` tại đúng commit SHA đã bàn giao (không giả định `/opt/crm-cskh-app` hay `/opt/crm-cskh-staging/repo` tồn tại sẵn — xem [`backup-restore.md`](./backup-restore.md) Mục 3.0), tệp [`deploy/ops/systemd/crm-cskh-healthcheck.service`](../../deploy/ops/systemd/crm-cskh-healthcheck.service) và [`crm-cskh-healthcheck.timer`](../../deploy/ops/systemd/crm-cskh-healthcheck.timer) chạy mỗi **5 phút** với đúng bộ tham số thực tế của VPS `103.77.208.186`:
- `--env-file /opt/crm-cskh-staging/.env.staging`
- `--mode tls`
- `--extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml`
- `--site crm-staging.localhost`
- `--backup-status-file /var/backups/crm-cskh/latest-backup-lifecycle.json` (`--max-backup-age-hours 26.0`)
- `--alert-log-file /var/lib/crm-cskh/health/health-alerts.jsonl`
- `--out /var/lib/crm-cskh/health/latest-health-report.json`
- **Không truyền `--webhook-url` và không đặt `CSKH_ALERT_WEBHOOK_URL`**, đảm bảo 100% không tự gửi email/chat ra ngoài khi chưa có chỉ định của Owner.

```bash
# Kiểm tra thư mục mã nguồn ops đã chuẩn bị đúng SHA:
test -f /opt/crm-cskh-ops/deploy/ops/check_health_and_invariants.py
test -f /opt/crm-cskh-ops/deploy/ops/install_systemd_timers.py

# Chạy kiểm tra một lần tại chỗ theo đúng topology thực tế:
sudo python3 /opt/crm-cskh-ops/deploy/ops/check_health_and_invariants.py \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --mode tls \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --backup-status-file /var/backups/crm-cskh/latest-backup-lifecycle.json \
  --max-backup-age-hours 26.0 \
  --alert-log-file /var/lib/crm-cskh/health/health-alerts.jsonl \
  --out /var/lib/crm-cskh/health/latest-health-report.json

# Bước 1 — Dry-run xem trước các unit systemd (mặc định không ghi hệ thống):
python3 /opt/crm-cskh-ops/deploy/ops/install_systemd_timers.py \
  --repo-root /opt/crm-cskh-ops \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --compose-wrapper /opt/crm-cskh-staging/staging-compose \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --backup-root /var/backups/crm-cskh

# Bước 2 — Cài đặt và kích hoạt systemd timer 5 phút/lần + backup hằng đêm 02:15 ICT (--install):
sudo python3 /opt/crm-cskh-ops/deploy/ops/install_systemd_timers.py \
  --repo-root /opt/crm-cskh-ops \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --compose-wrapper /opt/crm-cskh-staging/staging-compose \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --backup-root /var/backups/crm-cskh \
  --install
```

### 4.2. Chính sách Khử PII, Chống Bão Cảnh Báo & Mở Khóa Kênh Ngoài Hệ Thống (`/etc/crm-cskh/alerting.env` — `G19`)
1. **Khử PII & Secret bắt buộc:** Hàm `redact_payload_text()` tự động che mọi số điện thoại Việt Nam (`<REDACTED_PHONE>`), cookie phiên (`<REDACTED_FRAPPE_SID>`), mã CSRF (`<REDACTED_CSRF>`) và đường dẫn token webhook (`<REDACTED_WEBHOOK_PATH>`).
2. **Chống bão cảnh báo (`Deduplication Cooldown`):** Mỗi cặp `environment:alert_code` có thời gian chặn lặp mặc định `900 giây` (`15 phút`) lưu tại `/var/lib/crm-cskh/alert-dedup.json` (và `/var/backups/crm-cskh/backup-alert-dedup.json` cho cảnh báo backup).
3. **Đầu vào bắt buộc cần Owner cấp trước khi bật gửi cảnh báo ngoài (`BLK-GOV-06` & `BLK-INFRA-07` — không tự điền người nhận giả):**
   - `BLK-GOV-06` (`CSKH_OPERATOR_CONTACT`): Định danh đầu mối trực vận hành chính + dự phòng và khung giờ trực được Owner phê duyệt.
   - `BLK-INFRA-07` (`CSKH_ALERT_WEBHOOK_URL`): Địa chỉ HTTPS Webhook chính thức của kênh nhận cảnh báo do Owner phê duyệt (ChatOps nội bộ / Telegram / Slack / Pager).
4. **Quy trình kích hoạt qua `/etc/crm-cskh/alerting.env` (`0600`) mà không cần sửa unit systemd:**
   - Cả 3 service (`crm-cskh-healthcheck.service`, `crm-cskh-backup.service`, `crm-cskh-backup-failure@.service`) đều nạp tùy chọn `EnvironmentFile=-/etc/crm-cskh/alerting.env` (xem mẫu tại [`deploy/compose/env/alerting.env.example`](../../deploy/compose/env/alerting.env.example)).
   - Sau khi Owner cấp đủ 2 đầu vào trên, tạo `/etc/crm-cskh/alerting.env` quyền `0600` (`root:root`) và bắn kiểm chứng một cảnh báo thử nghiệm để đóng cổng `G19`:
   ```bash
   # Bước A — Gửi thử 1 cảnh báo kiểm tra sau khi Owner đã duyệt kênh & người trực (xem bảng tổng hợp tại P4-exit-report.md §3):
   CSKH_OPERATOR_CONTACT="<DAU_MOI_DUOC_OWNER_CHI_DINH>" \
   CSKH_ALERT_WEBHOOK_URL="<HTTPS_WEBHOOK_DUOC_OWNER_CHI_DINH>" \
   python3 /opt/crm-cskh-ops/deploy/ops/check_health_and_invariants.py \
     --environment-label staging \
     --send-test-alert \
     --dedup-state-file /var/lib/crm-cskh/alert-dedup.json \
     --out /var/lib/crm-cskh/test-alert-receipt.json

   # Bước B — Kiểm chứng biên nhận cảnh báo (đòi hỏi DELIVERED HTTP 2xx, <REDACTED_WEBHOOK_PATH>, 0 PII/SĐT và owner thật; trả BLOCKED exit 2 khi chưa cấu hình):
   python3 /opt/crm-cskh-ops/deploy/ops/check_health_and_invariants.py \
     --verify-alert-receipt /var/lib/crm-cskh/test-alert-receipt.json \
     --out /var/lib/crm-cskh/g19-alert-receipt-audit.json
   ```

### 4.3. Giám Sát Độ Tươi Backup Định Kỳ & Cảnh Báo Lỗi `ALT-BACKUP-FAILURE` (`SEV-1`)
- `crm-cskh-healthcheck.service` truyền `--backup-status-file /var/backups/crm-cskh/latest-backup-lifecycle.json --max-backup-age-hours 26.0` để xác minh bản sao lưu gần nhất đạt `local_backup_status == "PASS"` và không quá `26 giờ`; khi truyền thêm `--require-automated-offsite`, trình thăm dò báo `FAIL` nếu chưa chứng minh được upload định kỳ tự động (`periodic_offsite_upload_proven == true`).
- `crm-cskh-backup.service` khai báo `OnFailure=crm-cskh-backup-failure@%n.service` để ghi nhận tức thì cảnh báo `ALT-BACKUP-FAILURE` đã khử PII/secret vào `/var/backups/crm-cskh/backup-alerts.jsonl` (`0600`), đồng thời chỉ đẩy ra webhook HTTPS khi `BLK-GOV-06` và `BLK-INFRA-07` đã được Owner mở khóa.

### 4.4. Hồ Sơ Xử Lý Lỗi Thoát `-1` Của `test_ops_toolkit.py` Ở Ca Webhook / Cooldown
- **Phân loại nguyên nhân gốc:** **Thuộc ranh giới môi trường sandbox Windows AppContainer** (không phải lỗi logic mã nguồn của `dispatch_alert_with_cooldown` hay `redact_payload_text`).
  1. Trong Windows AppContainer bị hạn chế quyền mạng/Registry, lời gọi `mimetypes.init()` mặc định của `http.server` quét toàn bộ khóa `HKEY_CLASSES_ROOT` và lời gọi `socketserver.TCPServer(("127.0.0.1", 0))` / `socket.bind(("127.0.0.1", 0))` bị tiến trình giám sát AppContainer chấm dứt cưỡng bức với mã thoát `-1`.
  2. Trên Linux POSIX chuẩn (`ubuntu-24.04` / VPS), `socketserver.TCPServer(("127.0.0.1", 0))` khởi chạy máy chủ HTTP loopback thật và vượt qua toàn bộ kiểm định.
- **Cách khắc phục giữ nguyên 100% assertion:**
  1. Khởi tạo `mimetypes.init(files=[])` trước khi nạp `http.server` để loại bỏ quét Registry không cần thiết trên Windows.
  2. Giữ nguyên **toàn bộ assertion** trong `_assert_cooldown_delivery()` (`r1["status"] == "DELIVERED"`, ẩn sạch token đường dẫn webhook khỏi `r1["webhook_redacted"]`, `r2["status"] == "SUPPRESSED_COOLDOWN"` trong cửa sổ `900s`, và `len(_WebhookRecorderHandler.received_bodies) == 1`): trên POSIX (`os.name == "posix"` hoặc khi bật `CSKH_FORCE_LOOPBACK_SERVER=1`) chạy qua `socketserver.TCPServer(("127.0.0.1", 0))` thật; trên Windows AppContainer bị chặn `bind()` kiểm chứng đúng cùng hàm `dispatch_alert_with_cooldown()` qua transport giả lập của `urllib.request.urlopen`.

---

## 5. Trạng Thái Nghiệm Thu Hiện Tại

| Cổng | Điều kiện `P4.md §19` | Trạng thái hiện tại | Lý do / Bằng chứng thực tế |
| :--- | :--- | :---: | :--- |
| **`G17`** | Scheduler, queue, background jobs hoạt động ổn định, idempotent | **`PASS` (Trên Staging Live & Timer VPS)** | `live-health-after.json` + `timers-runtime.json` trên `103.77.208.186` xác nhận `crm-cskh-healthcheck.timer` tự kích hoạt lúc `20:05 ICT` ngày `09/11/2026` đạt `PASS` (`scheduler=1`, `queues=0 failed`, 4 cron `Complete`, `INV-01..06 == 0`); diễn tập `restart/retry ×2` cô lập đạt `PASS` tại [`isolated-staging-dr-https-report.json`](../../deploy/ops/evidence/p4-g07-g08-dr-20261109/isolated-staging-dr-https-report.json). |
| **`G19`** | Monitoring/alerting hoạt động và cảnh báo ngoài hệ thống đã test | **`BLOCKED` (`BLK-GOV-06`, `BLK-INFRA-07`)** | Timer giám sát 5 phút/lần đã chạy tự động trên VPS và khử PII đầy đủ (`external_alerts_sent = false`); kênh gửi cảnh báo ngoài hệ thống (`/etc/crm-cskh/alerting.env`) đang giữ khóa theo đúng chỉ đạo chờ Owner chỉ định đầu mối và webhook nhận. |
