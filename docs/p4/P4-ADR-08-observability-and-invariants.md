# P4-ADR-08: Kiến Trúc Quan Sát Vận Hành Nhẹ (`Zero-Agent`), Các Bất Biến Nghiệp Vụ & Ngưỡng Cảnh Báo

- **Mã ADR:** `P4-ADR-08`
- **Gói công việc:** `P4-07` (`P4.md §10`, `§18.2`, `§21`)
- **Trạng thái:** `PROPOSED FOR T0 / OWNER SIGN-OFF` *(Triển khai mã nguồn thăm dò đã sẵn sàng; kích hoạt kênh gửi thực tế đang `BLOCKED` bởi `BLK-GOV-06` & `BLK-INFRA-07`)*
- **Đầu vào đối chiếu:** Hồ sơ `P4-01` tại `492423c` (`deploy/compose/*`, `apps/crm_cskh/crm_cskh/hooks.py`)

---

## 1. Bối Cảnh

Hệ thống CRM-CSKH triển khai trên mô hình **một VPS Docker Compose** (dự toán khởi điểm `8 vCPU / 16 GB RAM`, chạy 10 container thường trực gồm Traefik, Nginx, Gunicorn, Node Socket.IO, 2 RQ workers, Scheduler, MariaDB và 2 Redis). `P4.md` đặt ra đồng thời hai yêu cầu:

1. **Quan sát toàn diện cả kỹ thuật lẫn nghiệp vụ (`P4.md §10.1–§10.2`):** Phải phát hiện sớm lỗi 5xx, hết hạn TLS, container restart/`OOMKilled`, đầy ổ đĩa, nghẽn hàng đợi RQ, trễ nhịp `scheduler`, trễ backup và đặc biệt là **6 bất biến toàn vẹn dữ liệu nghiệp vụ CSKH** (độc quyền chủ sở hữu active, đồng bộ `lead_owner`, chống nhân đôi `Contact Attempt`, đồng bộ `CRM Task` hẹn lại, đồng hồ SLA và đối soát `KPI Snapshot` / `Export Job`).
2. **Không làm nặng VPS (`P4.md §10.3`, `§18.2`):** Không mua nền tảng thương mại và không nhồi thêm các agent giám sát tiêu tốn nhiều RAM/CPU/disk I/O trên cùng VPS nếu Docker/Frappe/SQL nhẹ đáp ứng được mục tiêu đo lường.

---

## 2. So Sánh Các Phương Án

| Tiêu chí | Phương án A: Dựng full stack Prometheus + Grafana + Loki/ELK ngay trên cùng VPS | Phương án B: Mua SaaS APM thương mại gắn agent vào container | **Phương án C (Chọn): Trình thăm dò không-agent (`check_health_and_invariants.py`) chạy qua `systemd` timer + Docker Healthcheck + Webhook/SMTP có khử PII** |
| :--- | :--- | :--- | :--- |
| **Chi phí tài nguyên trên VPS** | Cao (`~1.5–3.0 GB RAM` thường trực + ghi TSDB/log index liên tục tranh chấp NVMe với MariaDB) | Trung bình (`200–500 MB RAM`), phát sinh chi phí thuê bao ngoài phạm vi `P4.md §0.2` | **Rất thấp (`0 MB` tiến trình nền thường trực; chạy mỗi `5 phút` trong `< 3 giây`, tốn `< 1%` CPU)** |
| **Kiểm tra bất biến nghiệp vụ CSKH (`INV-01..06`)** | Vẫn phải tự viết exporter SQL/Python riêng | Không hiểu mô hình `Work Item` / `SLA` / `KPI` | **Tích hợp trực tiếp các truy vấn đếm vô hướng (`scalar COUNT(*)`, `0` PII) khớp chính xác schema `492423c`** |
| **Rủi ro lộ PII ra hệ thống thứ ba** | Dễ hút nhầm log thô chứa SĐT nếu cấu hình Promtail sai | Rủi ro đẩy payload nhạy cảm ra đám mây bên ngoài | **Chỉ xuất số đếm tổng hợp và mã cảnh báo; qua bộ lọc `redact_payload_text()` trước khi ghi file/gửi cảnh báo** |
| **Tác động tới image upstream / Compose** | Phải mở thêm 4–6 container và mạng giám sát | Phải sửa `Containerfile` cài thư viện APM | **`0` sửa đổi `Containerfile` hay `apps/*`; gọi `docker compose ps` + `exec -T backend` chỉ đọc** |

---

## 3. Quyết Định

Chọn **Phương án C**:

1. **Tầng Container Runtime:** Dùng trực tiếp bộ `healthcheck` đã khóa trong `deploy/compose/overrides/compose.cskh-common.yaml` & `compose.cskh-tls.yaml` (`492423c`) cho toàn bộ 10 service thường trực.
2. **Tầng Thăm Dò Định Kỳ & Bất Biến Nghiệp Vụ:** Dùng kịch bản chuẩn hóa [`deploy/ops/check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py) kích hoạt qua `systemd` timer mỗi `5 phút` trên host VPS để kiểm tra:
   - Trạng thái `running`/`healthy`, `RestartCount` và cờ `OOMKilled` của 10 service thường trực; đảm bảo đúng `1` thực thể `scheduler` đang chạy.
   - Dung lượng đĩa cứng (`WARN >= 80%`, `CRITICAL >= 90%`).
   - Độ sâu hàng đợi RQ (`short`, `default`, `long`), tổng số job `failed`, số bản ghi `Error Log` trong 15 phút gần nhất, và độ trễ hoàn tất thực tế của cả 4 job định kỳ trong `hooks.py` (`evaluate_sla_obligations`, `drain_dirty_kpi_scopes`, `expire_stale_exports`, `refresh_current_standard_periods` — cảnh báo khi trễ `> 2 × cadence`).
   - **6 bất biến toàn vẹn nghiệp vụ (`INV-01`..`INV-06`)** chỉ trả về số nguyên vô hướng (`0` PII).
3. **Tầng Cảnh Báo Vệ Sinh (`Alert Hygiene`):** Mọi cảnh báo phát ra đều đính kèm `alert_code`, `severity`, `owner`, `first_action`, đường dẫn `runbook`, khóa chống gửi lặp (`cooldown = 900s` mặc định) và khử toàn bộ SĐT/cookie/token/URL bí mật.

---

## 4. Tác Động Lên Nâng Cấp Upstream & Cách Đảo Ngược Quyết Định

- **Tác động nâng cấp upstream:** Bằng `0` — không can thiệp mã nguồn `frappe`, `crm` hay `crm_cskh`, chỉ đọc bảng hệ thống (`tabScheduled Job Log`, `tabError Log`) và các bảng `tabCSKH *` đã khóa.
- **Cách đảo ngược / mở rộng khi quy mô tăng:**
  - Nếu cần tắt tạm thời trình thăm dò khi bảo trì: `sudo systemctl stop crm-cskh-monitor.timer` (không ảnh hưởng bất kỳ container ứng dụng nào).
  - Khi quy mô vượt một VPS hoặc công ty trang bị máy chủ giám sát riêng ngoài VPS CRM, JSON đầu ra của `check_health_and_invariants.py --out /var/lib/crm-cskh/metrics.json` có thể chuyển đổi trực tiếp sang định dạng textfile collector cho Prometheus bên ngoài mà không phải viết lại truy vấn bất biến.
