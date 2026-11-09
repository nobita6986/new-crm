# Giám Sát Kỹ Thuật, Bất Biến Nghiệp Vụ & Quy Trình Cảnh Báo Theo Topology Thực Tế (`P4-07` — Cổng `G17`, `G19`)

- **Gói công việc:** `P4-07` (`P4.md §10`, `§20.2`)
- **Quyết định kiến trúc liên kết:** [`docs/p4/P4-ADR-08-observability-and-invariants.md`](./P4-ADR-08-observability-and-invariants.md)
- **Kịch bản thực thi & Bộ lập lịch định kỳ:**
  - Trình thăm dò sức khỏe & bất biến: [`deploy/ops/check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py)
  - Cài đặt timer định kỳ trên VPS (có gắn `compose.domain.yaml`): [`deploy/ops/install_systemd_timers.py`](../../deploy/ops/install_systemd_timers.py) (`crm-cskh-healthcheck.service` / `crm-cskh-healthcheck.timer`)
  - Kiểm thử đơn vị: [`deploy/ops/test_ops_toolkit.py`](../../deploy/ops/test_ops_toolkit.py) & [`deploy/ops/test_offsite_restore.py`](../../deploy/ops/test_offsite_restore.py)
- **Hiện trạng đã đo trên VPS `103.77.208.186` (`crm-staging.localhost`):**
  - `scheduler` đã bật; `live-health-handoff.json` xác nhận toàn bộ container, hàng đợi RQ, 4 cron `crm_cskh` và cả 6 bất biến nghiệp vụ `INV-01..06` đạt **`PASS`**.
  - **Cổng gửi cảnh báo ngoài hệ thống (`G19`) đang được giữ khóa chủ động (`BLOCKED` bởi `BLK-GOV-06` & `BLK-INFRA-07`):** Hệ thống chỉ ghi báo cáo JSON cục bộ + nhật ký `journald`, **tuyệt đối chưa tự động gửi email, tin nhắn chat hay webhook ra ngoài** cho tới khi Owner chỉ định chính thức đầu mối và kênh nhận cảnh báo.

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
Tệp [`deploy/ops/systemd/crm-cskh-healthcheck.service`](../../deploy/ops/systemd/crm-cskh-healthcheck.service) và [`crm-cskh-healthcheck.timer`](../../deploy/ops/systemd/crm-cskh-healthcheck.timer) chạy mỗi **5 phút** với đúng bộ tham số thực tế của VPS `103.77.208.186`:
- `--env-file /opt/crm-cskh-staging/.env.staging`
- `--mode tls`
- `--extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml`
- `--site crm-staging.localhost`
- `--out /var/lib/crm-cskh/latest-health-report.json`
- **Không truyền `--alert-webhook-url` và không đặt `CSKH_ALERT_WEBHOOK_URL`**, đảm bảo 100% không tự gửi email/chat ra ngoài khi chưa có chỉ định của Owner.

```bash
# Chạy kiểm tra một lần tại chỗ theo đúng topology thực tế:
python3 /opt/crm-cskh-app/deploy/ops/check_health_and_invariants.py \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --mode tls \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --out /var/lib/crm-cskh/latest-health-report.json

# Cài đặt systemd timer chạy định kỳ 5 phút/lần + backup hằng đêm 02:15 ICT:
sudo python3 /opt/crm-cskh-app/deploy/ops/install_systemd_timers.py \
  --repo-root /opt/crm-cskh-app \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --compose-wrapper /opt/crm-cskh-staging/staging-compose \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --backup-root /var/backups/crm-cskh \
  --backup-key-file /etc/crm-cskh/secrets/backup-passphrase.key \
  --apply
```

### 4.2. Chính sách Khử PII, Chống Bão Cảnh Báo & Mở Khóa Kênh Ngoài Hệ Thống (`G19`)
1. **Khử PII & Secret bắt buộc:** Hàm `redact_payload_text()` tự động che mọi số điện thoại Việt Nam (`<REDACTED_PHONE>`), cookie phiên (`<REDACTED_FRAPPE_SID>`), mã CSRF (`<REDACTED_CSRF>`) và đường dẫn token webhook (`<REDACTED_WEBHOOK_PATH>`).
2. **Chống bão cảnh báo (`Deduplication Cooldown`):** Mỗi cặp `environment:alert_code` có thời gian chặn lặp mặc định `900 giây` (`15 phút`) lưu tại `/var/lib/crm-cskh/alert-dedup.json`.
3. **Quy trình mở khóa cảnh báo ngoài hệ thống (chỉ thực hiện sau khi Owner phê duyệt đầu mối `BLK-GOV-06` và kênh `BLK-INFRA-07`):**
   ```bash
   CSKH_OPERATOR_CONTACT="<DAU_MOI_DUOC_OWNER_CHI_DINH>" \
   CSKH_ALERT_WEBHOOK_URL="<HTTPS_WEBHOOK_DUOC_OWNER_CHI_DINH>" \
   python3 /opt/crm-cskh-app/deploy/ops/check_health_and_invariants.py \
     --environment-label staging \
     --send-test-alert \
     --dedup-state-file /var/lib/crm-cskh/alert-dedup.json \
     --out /var/lib/crm-cskh/test-alert-receipt.json
   ```

---

## 5. Trạng Thái Nghiệm Thu Hiện Tại

| Cổng | Điều kiện `P4.md §19` | Trạng thái hiện tại | Lý do / Bằng chứng thực tế |
| :--- | :--- | :---: | :--- |
| **`G17`** | Scheduler, queue, background jobs hoạt động ổn định, idempotent | **`PASS` (Trên Staging Live)** | `live-health-handoff.json` trên `103.77.208.186` xác nhận `scheduler` đã bật, container/queue/cron và `INV-01..06` đạt `PASS`; systemd timer gắn `compose.domain.yaml` đã đóng gói. |
| **`G19`** | Monitoring/alerting hoạt động và cảnh báo ngoài hệ thống đã test | **`BLOCKED` (`BLK-GOV-06`, `BLK-INFRA-07`)** | Giám sát nội bộ đã chạy và khử PII đầy đủ; phần gửi cảnh báo ngoài hệ thống đang được giữ khóa theo đúng chỉ đạo chờ Owner chỉ định đầu mối và kênh nhận. |
