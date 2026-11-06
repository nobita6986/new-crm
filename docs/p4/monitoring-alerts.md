# Giám Sát Kỹ Thuật, Bất Biến Nghiệp Vụ & Quy Trình Cảnh Báo (`P4-07` — Cổng `G17`, `G19`)

- **Gói công việc:** `P4-07` (`P4.md §10`, `§20.2`)
- **Quyết định kiến trúc liên kết:** [`docs/p4/P4-ADR-08-observability-and-invariants.md`](./P4-ADR-08-observability-and-invariants.md)
- **Kịch bản thực thi:** [`deploy/ops/check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py) (unit tests tại [`deploy/ops/test_ops_toolkit.py`](../../deploy/ops/test_ops_toolkit.py))
- **Bằng chứng tiền kiểm hiện tại:** [`deploy/ops/evidence/p4-07-alert-preflight-blocked.json`](../../deploy/ops/evidence/p4-07-alert-preflight-blocked.json) (`status = "BLOCKED"` do đang chờ `BLK-GOV-06` & `BLK-INFRA-07`)

---

## 1. Tín Hiệu Kỹ Thuật Và Ngưỡng Cảnh Báo Khởi Điểm (`P4.md §10.1`)

> Các ngưỡng dưới đây là ngưỡng khởi điểm vận hành an toàn cho một VPS và sẽ được hiệu chỉnh thêm sau báo cáo benchmark `P4-06`.

| Nhóm | Tín hiệu đo | Nguồn thu thập | Ngưỡng Cảnh báo (`WARN` / `SEV-2`) | Ngưỡng Nghiêm trọng (`CRIT` / `SEV-1`) | Hành động đầu tiên |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Website & TLS** | Tỷ lệ `5xx`, độ trễ `/api/method/crm_cskh.api.health.ping`, hạn chứng chỉ TLS | Traefik / `staging_https_smoke_and_reconcile.py` | Độ trễ ping `> 2.0s`; hạn TLS `< 21 ngày` | `5xx > 1%` trong `5 phút`; hạn TLS **`< 14 ngày`** | Kiểm tra log `proxy`/`frontend`/`backend`, kiểm tra gia hạn ACME trong `cert-data`. |
| **Containers** | `State`, `Health`, `RestartCount`, `State.OOMKilled` trên 10 service thường trực | `check_health_and_invariants.py` (`docker compose ps` + `inspect`) | `RestartCount > 0` trong cửa sổ theo dõi | Bất kỳ service `!= healthy` hoặc **`OOMKilled == true`** | Xem `docker logs --tail 100 <service>`, kiểm tra giới hạn RAM và truy vấn nặng. |
| **CPU & RAM** | Mức tải duy trì trên host VPS & swap | `vmstat` / `free -m` / `docker stats --no-stream` | CPU `> 85%` liên tục `15 phút` | RAM **`> 90%`** hoặc phát sinh swap thrashing | Kiểm tra tiến trình `queue-long` (import/export/recompute) và số kết nối MariaDB. |
| **Ổ đĩa (Disk)** | `%` dung lượng & `%` inode của phân vùng chứa Docker volumes & staging backup | `check_health_and_invariants.py` (`shutil.disk_usage`) | **`>= 80%`** dung lượng hoặc inode | **`>= 90%`** dung lượng hoặc inode | Dọn tệp xuất hết hạn, xoay vòng log cũ, kiểm tra dung lượng trống trước khi chạy backup. |
| **MariaDB** | Kết nối đang mở (`Threads_connected`), `Innodb_row_lock_waits`, slow queries | `db` healthcheck & `SHOW GLOBAL STATUS` | `Threads_connected > 75%` `max_connections` | Hàng đợi khóa dòng tăng liên tục / DB không phản hồi | Tạm hoãn job lô lớn (`import`/`recompute`), kiểm tra câu lệnh giữ khóa lâu. |
| **Backup (`P4-05`)** | Thời điểm hoàn tất bản sao lưu mã hóa offsite gần nhất & mã kiểm tra SHA-256 | Manifest backup của `P4-05` | Trễ `> 2 giờ` so với lịch định kỳ | Quá **1 chu kỳ backup** chưa upload offsite thành công hoặc lệch checksum | Chạy lại backup thủ công ngay, kiểm tra kết nối/quyền ghi kho lưu trữ ngoài VPS (`BLK-INFRA-05`). |

---

## 2. Theo Dõi Hàng Đợi RQ & Nhịp Đập Scheduler (`G17`)

Căn cứ cấu hình thực tế tại [`apps/crm_cskh/crm_cskh/hooks.py`](../../apps/crm_cskh/crm_cskh/hooks.py) (dòng 421–434), `check_health_and_invariants.py` giám sát trực tiếp:

1. **Ràng buộc một `scheduler` duy nhất:** `scheduler_running_instances == 1` và `frappe.utils.scheduler.is_scheduler_disabled() == False`.
2. **Hàng đợi Redis (`short`, `default`, `long`):** Đọc số lượng job đang chờ (`q.count`) và số lượng job thất bại trong `FailedJobRegistry` (`failed > 0` kích hoạt cảnh báo `ALT-TECH-QUEUE-SCHEDULER`).
3. **Độ tươi thực thi của 4 tác vụ nền `crm_cskh` trong `tabScheduled Job Log` (ngưỡng tối đa = `2 × chu kỳ`):**
   - `crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations` (lịch `*/5 * * * *`) → cảnh báo nếu trễ **`> 600 giây` (`10 phút`)**.
   - `crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes` (lịch `*/5 * * * *`) → cảnh báo nếu trễ **`> 600 giây` (`10 phút`)**.
   - `crm_cskh.jobs.run_export.expire_stale_exports` (lịch `hourly`) → cảnh báo nếu trễ **`> 7.200 giây` (`120 phút`)**.
   - `crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods` (lịch `daily`) → cảnh báo nếu trễ **`> 172.800 giây` (`48 giờ`)**.
4. **Đột biến lỗi ứng dụng:** Đếm số bản ghi mới trong `tabError Log` của 15 phút gần nhất (chỉ xuất con số đếm, không trích xuất nội dung traceback thô ra kênh cảnh báo).

---

## 3. Sáu Bất Biến Toàn Vẹn Nghiệp Vụ Bắt Buộc (`P4.md §10.2`)

Tất cả truy vấn dưới đây thực thi chỉ đọc bên trong `check_health_and_invariants.py`, trả về duy nhất số nguyên (`PASS` khi toàn bộ bằng `0`):

| Mã Bất biến | Quy tắc nghiệp vụ bảo vệ | Điều kiện `PASS` | Mức độ khi vi phạm |
| :--- | :--- | :--- | :---: |
| **`INV-01`** | **Độc quyền chủ sở hữu đang hoạt động & đồng bộ `lead_owner`:** Không có `CRM Lead` nào gắn `> 1` `CSKH Work Item` ở trạng thái `assigned`/`in_progress`/`on_hold`; không lệch giữa `CSKH Work Item.assigned_to` và `CRM Lead.lead_owner`; tổng `CSKH Active Claim` khớp tuyệt đối số Work Item đang sở hữu. | `duplicate_active_leads = 0`<br>`owner_mismatch_active_work_items = 0`<br>`active_claim_count_mismatch = 0` | **`SEV-1`** |
| **`INV-02`** | **Chống nhân đôi `Contact Attempt` & bảo vệ trạng thái xác minh:** Không có cặp `(work_item, performed_by, idempotency_key)` bị trùng; không có bản ghi `verification_status = 'verified'` nào thiếu cả `call_log` lẫn phiếu `CSKH QA Review` hợp lệ (`self_reported` không bao giờ tự biến thành `verified`). | `duplicate_attempt_idempotency_keys = 0`<br>`unverified_marked_verified = 0` | **`SEV-1`** |
| **`INV-03`** | **Toàn vẹn công việc hẹn lại (`CRM Task` callback):** Mọi `CRM Task` đang mở (`Backlog`/`Todo`/`In Progress`) có `cskh_is_followup = 1` đều phải gắn với một `CSKH Work Item` đang hoạt động và có `assigned_to` khớp chủ sở hữu hiện tại sau bàn giao `A → B`. | `orphan_or_misassigned_open_followup_tasks = 0` | **`SEV-2`** |
| **`INV-04`** | **Sức khỏe bộ máy SLA:** Không trùng `idempotency_key` trên `tabCSKH SLA Obligation`; khi `p3_sla_enabled = true`, không có nghĩa vụ `open` (không tạm dừng) đã quá hạn `effective_due_at > 15 phút` mà chưa được đánh giá chuyển trạng thái. | `duplicate_sla_idempotency_keys = 0`<br>`stale_unevaluated_overdue_sla = 0` | **`SEV-2`** |
| **`INV-05`** | **Đối soát `KPI Snapshot` & tiến độ `Dirty Scope`:** Khi `p3_reporting_enabled = true`, không có `CSKH Dirty Scope` bị kẹt `pending`/`processing` quá `30 phút`; không có `CSKH KPI Snapshot` nào ghi `QA_PASS_RATE = 0` khi mẫu số bằng `0` (`denominator = 0`). | `stuck_dirty_scopes_over_30m = 0`<br>`false_zero_qa_snapshots = 0` | **`SEV-2`** |
| **`INV-06`** | **Thu hồi đúng hạn tệp xuất báo cáo (`Export TTL`):** Không có `CSKH Export Job` nào còn giữ `status = 'completed'` sau mốc `expires_at + 2 giờ`. | `unexpired_completed_exports_past_2h_grace = 0` | **`SEV-2`** |

---

## 4. Chính Sách Khử PII, Lưu Giữ Log & Vệ Sinh Cảnh Báo (`P4.md §10.3–§10.4`)

1. **Khử PII & Secret bắt buộc:** Hàm `redact_payload_text()` trong [`check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py) tự động che mọi chuỗi khớp mẫu số điện thoại Việt Nam (`0...`, `84...`, `+84...` → `<REDACTED_PHONE>`), cookie phiên (`sid=...` → `<REDACTED_FRAPPE_SID>`) và đường dẫn token của URL webhook (`<REDACTED_WEBHOOK_PATH>`).
2. **Quyền đọc nhật ký kiểm toán:** Chỉ cấp cho tài khoản vận hành kỹ thuật (`Operator`) và `BOD` được ủy quyền; không mở cho `Sales User` hay `Sales Manager` ngoài phạm vi đội.
3. **Chống bão cảnh báo (`Deduplication Cooldown`):** Mỗi cặp `environment:alert_code` có thời gian chặn lặp mặc định `900 giây` (`15 phút`) lưu tại tệp trạng thái cục bộ `--dedup-state-file /var/lib/crm-cskh/alert-dedup.json`.
4. **Đề xuất thời gian lưu giữ (chờ Owner duyệt chính thức):** Container/system logs xoay vòng `30 ngày` (`max-size: 50m`, `max-file: 5` trên Docker daemon); `Scheduled Job Log` & `Error Log` dọn tự động `30–90 ngày`; lịch sử kiểm toán nghiệp vụ (`Assignment History`, `Attempt Correction`, `KPI Run`/`Snapshot`) lưu bền vững theo cơ sở dữ liệu chính.

---

## 5. Kiểm Thử Cảnh Báo End-to-End & Sổ Ghi Bằng Chứng (`G17`, `G19`)

### Lệnh cài đặt định kỳ trên Staging / Production và gửi cảnh báo thử

```bash
# 1. Chạy kiểm tra sức khỏe + 6 bất biến nghiệp vụ tại chỗ:
python3 deploy/ops/check_health_and_invariants.py \
  --env-file /etc/crm-cskh/staging.env \
  --mode tls \
  --environment-label staging \
  --out /var/lib/crm-cskh/latest-health-report.json

# 2. Gửi cảnh báo thử nghiệm tới đầu mối vận hành để nghiệm thu cổng G19 (sau khi gỡ BLK-GOV-06 & BLK-INFRA-07):
CSKH_OPERATOR_CONTACT="<EMAIL_HOAC_ID_DAU_MOI_VAN_HANH>" \
CSKH_ALERT_WEBHOOK_URL="<HTTPS_WEBHOOK_DUOC_DUYET>" \
python3 deploy/ops/check_health_and_invariants.py \
  --environment-label staging \
  --send-test-alert \
  --dedup-state-file /var/lib/crm-cskh/alert-dedup.json \
  --out deploy/ops/evidence/p4-07-test-alert-receipt.json
```

### Trạng Thái Nghiệm Thu Hiện Tại

| Cổng | Điều kiện `P4.md §19` | Trạng thái hiện tại | Lý do / Bằng chứng |
| :--- | :--- | :---: | :--- |
| **`G17`** | Scheduler, queue, background jobs restart/retry idempotent | **`BLOCKED` (Chờ Staging `P4-03`)** | Kịch bản kiểm tra queue/scheduler + unit tests đã `PASS` local; cần chạy thực nghiệm restart worker/scheduler trên Staging chạy đúng registry digest. |
| **`G19`** | Monitoring/alerting hoạt động và cảnh báo đã test | **`BLOCKED` (`BLK-GOV-06`, `BLK-INFRA-07`)** | Tiền kiểm [`p4-07-alert-preflight-blocked.json`](../../deploy/ops/evidence/p4-07-alert-preflight-blocked.json) xác nhận thiếu thông tin đầu mối nhận cảnh báo (`CSKH_OPERATOR_CONTACT`) và URL kênh cảnh báo (`CSKH_ALERT_WEBHOOK_URL`). |
