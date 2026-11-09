# Quy Trình Vận Hành Chuẩn Sau Go-Live, Khởi Động Lại Dịch Vụ & Bàn Giao Vận Hành (`P4-11` — Cổng `G24`)

- **Mã tài liệu:** `P4-OPERATIONS-SOP` (`docs/p4/operations-sop.md` — `P4.md §3.3`, `§17`, `§20.2`)
- **Đầu vào hạ tầng thực tế đã đo (`https://crm.hrpartner.vn`):**
  - VPS: `103.77.208.186` · Project Compose: `crm-cskh-vps-staging` · Site Frappe: `crm-staging.localhost`
  - Wrapper chuẩn: `/opt/crm-cskh-staging/staging-compose` · Tệp cấu hình: `/opt/crm-cskh-staging/.env.staging` (`0600`)
  - Overlay tên miền thực tế: `/opt/crm-cskh-staging/compose.domain.yaml` (nối sau 8 tệp Compose TLS gốc)
  - Công cụ kiểm tra & vận hành: [`check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py), [`manage_backup_lifecycle.py`](../../deploy/ops/manage_backup_lifecycle.py), [`install_systemd_timers.py`](../../deploy/ops/install_systemd_timers.py)
  - Sổ tay đi kèm: [`backup-restore.md`](./backup-restore.md), [`release-runbook.md`](./release-runbook.md), [`rollback-runbook.md`](./rollback-runbook.md), [`monitoring-alerts.md`](./monitoring-alerts.md)
- **Trạng thái ký bàn giao vận hành (`G24`) hiện tại:** **`BLOCKED`** *(Tài liệu SOP và các unit systemd đã sẵn sàng; bước ký bàn giao tài khoản vận hành riêng và kênh cảnh báo ngoài hệ thống đang chờ Owner chỉ định đầu mối `BLK-GOV-06` / `BLK-INFRA-07` và kho lưu trữ offsite tự động `BLK-INFRA-05`)*.

---

## 1. Lịch Kiểm Tra Vận Hành Định Kỳ Theo Topology Thực Tế (`P4.md §17.1`)

| Chu kỳ | Danh mục kiểm tra cụ thể | Lệnh / Màn hình chuẩn | Vai trò chịu trách nhiệm |
| :--- | :--- | :--- | :--- |
| **Mỗi 5 phút (Tự động trên VPS)** | 1. 10 container thường trực `healthy`, `0` `OOMKilled`, ổ đĩa `< 80%`.<br>2. Hàng đợi RQ (`short`/`default`/`long`) `failed == 0`, 4 cron `crm_cskh` chạy đúng nhịp.<br>3. Cả 6 bất biến nghiệp vụ (`INV-01`..`INV-06`) đạt `PASS`.<br>*(Lưu cục bộ ra JSON + `journald`; **chưa tự gửi email/chat ra ngoài** khi chờ `BLK-GOV-06`)* | `crm-cskh-healthcheck.timer` gọi `check_health_and_invariants.py --env-file /opt/crm-cskh-staging/.env.staging --mode tls --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml --site crm-staging.localhost` | `systemd` + **Operator kỹ thuật** (`BLK-GOV-06`) |
| **Hằng ngày (`02:15 ICT` & Đầu giờ sáng)** | 1. `crm-cskh-backup.timer` tạo bản sao lưu mã hóa đêm trước và kiểm tra SHA-256 cục bộ.<br>2. Đầu giờ sáng: rà `/var/lib/crm-cskh/latest-health-report.json` và kéo bản sao lưu mã hóa về kho ngoài VPS + chạy `record-offsite` *(cho tới khi có upload tự động `BLK-INFRA-05`)*. | `manage_backup_lifecycle.py verify-bundle` & `record-offsite` ([`backup-restore.md`](./backup-restore.md)) | **Operator kỹ thuật** (`BLK-GOV-06`) |
| **Hằng ngày (Nghiệp vụ)** | 1. Dòng lỗi khi nhập CSV và số lượng hồ sơ chờ phân công.<br>2. Danh sách đầu việc sắp vi phạm / đã vi phạm SLA liên hệ đầu & lịch hẹn.<br>3. Độ tươi (`as_of`) của bảng chỉ số KPI ngày hiện tại. | `/cskh/import`, `/cskh/pool`, `/cskh/sla-operations`, `/cskh/quality-overview` | **Quản trị CRM & Trưởng nhóm** |
| **Hằng tuần** | 1. Rà soát `Error Log`, truy vấn MariaDB chậm và dung lượng phân vùng Docker.<br>2. Rà soát nhật ký thay đổi phân quyền, `CSKH Export Job` và yêu cầu tính lại KPI.<br>3. Lấy mẫu đối soát ngẫu nhiên 5 đầu việc từ Dashboard xuống lịch sử gốc. | Báo cáo tuần từ `check_health_and_invariants.py` + kiểm toán DocType quản trị | **Tech Owner + Quản trị CRM** |
| **Hằng tháng** | 1. **Diễn tập khôi phục cô lập (`Restore Drill`)** bằng `verify_offsite_restore.py` (`P4.md §7.2`).<br>2. Đối chiếu danh sách tài khoản `User.enabled = 1` với nhân sự thực tế.<br>3. Rà soát toàn vẹn 5 bản ghi `Translation` tùy chỉnh của Owner và dung lượng lưu trữ. | [`backup-restore.md`](./backup-restore.md) Mục 4 + kiểm tra user active | **Operator + Tech Owner** |

---

## 2. Sổ Tay Khởi Động Lại Dịch Vụ An Toàn (`Safe Service Restart Runbook`)

> [!IMPORTANT]
> Luôn chạy qua wrapper `/opt/crm-cskh-staging/staging-compose` (đã nạp sẵn `.env.staging` và `compose.domain.yaml`). **Tuyệt đối không dùng `docker compose down -v`** (cờ `-v` sẽ xóa sạch dữ liệu MariaDB và tệp đính kèm).

### 2.1. Khởi động lại dịch vụ Web không trạng thái (`frontend`, `websocket`, `backend`)
Áp dụng khi làm mới kết nối tĩnh/socket hoặc giải phóng bộ nhớ worker Gunicorn mà không chạm tới MariaDB hay hàng đợi nền:

```bash
# Khởi động lại tuần tự từng thành phần web:
/opt/crm-cskh-staging/staging-compose restart backend websocket frontend

# Xóa cache Redis để đồng bộ bản dịch & cấu hình (nếu vừa thay đổi cấu hình cấp site):
/opt/crm-cskh-staging/staging-compose exec -T backend \
  bench --site crm-staging.localhost clear-cache

# Kiểm tra ngay trạng thái healthy và phản hồi ping HTTPS:
/opt/crm-cskh-staging/staging-compose ps backend websocket frontend proxy
curl -fsSI https://crm.hrpartner.vn/api/method/crm_cskh.api.health.ping
```

### 2.2. Xả hàng đợi và khởi động lại RQ Workers (`queue-short`, `queue-long`)
Áp dụng khi worker nền tiêu thụ RAM cao hoặc cần nạp lại cấu hình mà **không cắt ngang tác vụ import / phân phối / tính KPI đang chạy**:

```bash
# Bước 1: Tạm ngưng scheduler nạp thêm job định kỳ mới trong lúc chờ xả:
/opt/crm-cskh-staging/staging-compose exec -T backend \
  bench --site crm-staging.localhost scheduler pause

# Bước 2: Kiểm tra thực tế số job đang thực thi (StartedJobRegistry == 0 trên cả 3 queue) — KHÔNG đoán bằng sleep mù:
/opt/crm-cskh-staging/staging-compose exec -T backend \
  /home/frappe/frappe-bench/env/bin/python -c '
import os, frappe
from rq import Queue
from rq.registry import StartedJobRegistry
from frappe.utils.background_jobs import get_redis_conn
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="crm-staging.localhost", sites_path=".")
frappe.connect()
conn = get_redis_conn()
active = {q: StartedJobRegistry(queue=Queue(q, connection=conn)).count for q in ("short", "default", "long")}
print("ACTIVE_RUNNING_JOBS=", active)
assert sum(active.values()) == 0, f"Workers still busy: {active}"
frappe.destroy()
'

# Bước 3: Khi ACTIVE_RUNNING_JOBS đều bằng 0, khởi động lại worker và mở lại scheduler:
/opt/crm-cskh-staging/staging-compose restart queue-short queue-long
/opt/crm-cskh-staging/staging-compose exec -T backend \
  bench --site crm-staging.localhost scheduler resume
```

### 2.3. Khởi động lại `scheduler` an toàn
Áp dụng khi nhịp cron trong `tabScheduled Job Log` bị trễ quá ngưỡng nhưng container chưa tự phục hồi:

```bash
# Đảm bảo chỉ có đúng 1 container scheduler chạy và cờ scheduler được bật:
/opt/crm-cskh-staging/staging-compose up -d --force-recreate scheduler
/opt/crm-cskh-staging/staging-compose exec -T backend \
  bench --site crm-staging.localhost scheduler enable
/opt/crm-cskh-staging/staging-compose exec -T backend \
  bench --site crm-staging.localhost scheduler resume
```

### 2.4. Khởi động lại toàn bộ Stack (ví dụ sau bảo trì OS hoặc khởi động lại máy chủ VPS)
```bash
# Đảm bảo quyền tệp cấu hình giữ đúng 0600:
chmod 600 /opt/crm-cskh-staging/.env.staging

# Dựng lại theo đúng trật tự phụ thuộc của Compose (bao gồm compose.domain.yaml):
/opt/crm-cskh-staging/staging-compose up -d

# Chạy kiểm tra tổng thể container + queue + cron + 6 bất biến nghiệp vụ:
python3 /opt/crm-cskh-app/deploy/ops/check_health_and_invariants.py \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --mode tls \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --startup-grace-seconds 600 \
  --out /var/lib/crm-cskh/post-restart-health.json
```

---

## 3. Phân Loại Và Ứng Phó Sự Cố (`P4.md §17.2`)

> **Lưu ý minh bạch nguồn lực:** Công ty phê duyệt khung giờ trực và kênh nhận cảnh báo khi chốt `BLK-GOV-06` / `BLK-INFRA-07`; tài liệu này không mặc định cam kết trực `24/7` hay tự gửi email/chat khi chưa được Owner chỉ định đầu mối.

| Mức độ | Dấu hiệu nhận diện | Hành động cô lập đầu tiên (trong 15 phút đầu sau khi tiếp nhận) | Lộ trình leo thang |
| :--- | :--- | :--- | :--- |
| **`SEV-1` (Khẩn cấp)** | - Phát hiện lộ SĐT/ghi chú/tệp đính kèm chéo quyền.<br>- Vi phạm `INV-01` (nhiều owner active / lệch chủ sở hữu) hoặc `INV-02`.<br>- Mất kết nối MariaDB, hỏng dữ liệu, mất VPS hoặc nghi ngờ lộ tài khoản quản trị. | 1. Giữ nguyên bằng chứng log.<br>2. Khóa ngay tính năng gây lộ (`cskh_p3_export_enabled=false` / `cskh_p3_enabled=false` hoặc `set-maintenance-mode on` nếu lộ ở lõi P1/P2).<br>3. Thu hồi phiên người dùng liên quan nếu nghi lộ tài khoản. | Báo ngay **Tech Owner + BOD/Owner**; kích hoạt [`rollback-runbook.md`](./rollback-runbook.md) hoặc Quy trình Sự cố Dữ liệu Mục 4. |
| **`SEV-2` (Cao)** | - `scheduler` hoặc `queue-short`/`queue-long` ngừng chạy; vi phạm `INV-03`..`INV-06`.<br>- Dashboard KPI sai lệch diện rộng hoặc chức năng nhập/phân công lỗi.<br>- Ổ đĩa vượt `80%` hoặc sao lưu đêm thất bại. | 1. Ưu tiên bảo toàn dữ liệu gốc.<br>2. Nếu lỗi chỉ ở báo cáo/SLA P3, tạm tắt cờ P3 tương ứng để **Người tuyển dụng** vẫn tác nghiệp bình thường ở P1/P2.<br>3. Thực hiện khởi động lại an toàn theo Mục 2 / giải phóng dung lượng tạm. | Báo **Tech Owner** xử lý trong ngày làm việc; lập PR sửa lỗi có kiểm thử. |
| **`SEV-3` (Thông thường)** | - Lỗi hiển thị nhỏ trên giao diện phụ, nhãn dịch chưa tự nhiên không làm sai số liệu hay quyền hạn. | Ghi nhận phiếu lỗi kèm ảnh chụp màn hình đã che PII. | Đưa vào nhánh sửa lỗi và phát hành theo chu kỳ chuẩn. |

---

## 4. Quy Trình Xử Lý Sự Cố Dữ Liệu 6 Bước — Cấm Sửa Tay Lịch Sử (`P4.md §17.3`)

Khi phát hiện sai lệch dữ liệu nghiệp vụ (phân công, lần liên hệ, hạn SLA hoặc điểm KPI):

1. **Cô lập & Khoanh vùng thời gian:** Tạm dừng tác vụ nền gây sai lệch (nếu còn đang diễn tiến) và ghi nhận chính xác khoảng thời gian `[t_start, t_end]` cùng phiên bản image digest.
2. **Xác định tập bản ghi ảnh hưởng (Chỉ đọc):** Truy vấn danh sách ID của `CRM Lead`, `CSKH Work Item`, `CSKH Assignment History`, `CSKH Contact Attempt`, `CRM Task`, `CSKH KPI Run` liên quan.
3. **Xuất bảng đối chiếu từ nguồn sự thật:** Lưu kết quả đối chiếu ra tệp kiểm toán nội bộ; **tuyệt đối không chạy câu lệnh `UPDATE`/`DELETE` trực tiếp sửa lịch sử trong MariaDB**.
4. **Thực thi hiệu chỉnh có kiểm toán (`Audited Correction / Recompute`):**
   - Với lần liên hệ ghi nhầm: dùng luồng chuẩn `CSKH Attempt Correction` (`correct` / `void` / `restore`) có người yêu cầu, người duyệt và lý do.
   - Với chủ sở hữu: dùng service điều chuyển/thu hồi chuẩn để sinh bản ghi `CSKH Assignment History` mới.
   - Với điểm KPI đã công bố: trình `BOD` / `QA` phê duyệt trước khi chạy `recompute` sinh bản chụp `is_restated = 1` (**giữ nguyên công thức KPI đã khóa `cskh-metrics-v2`**).
5. **Bảo toàn nhãn tùy chỉnh cấp Site của Owner (`Translation` DocType):** Tuyệt đối không xóa bảng `tabTranslation` (đang lưu 5 nhãn hiển thị theo yêu cầu của Owner: `Người tuyển dụng`, `Các dự án`, `Doanh thu hàng tháng / năm`, `NLĐ đang tiếp cận`, `NLD đã tiếp cận`).
6. **Chạy đối soát hậu kiểm & Ứng phó vi phạm dữ liệu cá nhân (nếu có):** Thực thi `check_health_and_invariants.py` xác nhận toàn bộ `INV-01`..`INV-06` trở về `0` vi phạm; nếu có truy cập trái phép vào dữ liệu cá nhân thật, báo cáo ngay cho Ban Giám đốc và bộ phận pháp chế.

---

## 5. Quy Trình Quản Lý Tài Khoản, Chuyển Đội & Thu Hồi Quyền Nhân Sự Nghỉ Việc (`P4.md §17.4`)

### 5.1. Nguyên tắc cấp phát tài khoản (`Least Privilege`)
- Mỗi cá nhân sử dụng **một tài khoản định danh riêng**; cấm dùng chung tài khoản `Administrator` cho công việc hằng ngày của BOD, Trưởng nhóm, Quản trị CRM hay Người tuyển dụng.
- `BOD` chỉ cấp role `CSKH BOD Viewer` (không cấp `System Manager`).

### 5.2. Nhân sự nghỉ việc hoặc ngừng phụ trách hồ sơ (`Offboarding Checklist`)
1. **Khóa đăng nhập & Hủy toàn bộ phiên đang mở:** Đặt `User.enabled = 0` cho tài khoản nhân sự và xóa phiên trong Redis (`clear-cache` / session revoke) để mọi tab trình duyệt đang mở mất hiệu lực tức thì.
2. **Ngừng chia hồ sơ mới:** Tắt cờ `enabled = 0` trên mọi `CSKH Assignment Rule` của nhân sự đó.
3. **Thu hồi / Điều chuyển đầu việc đang mở bằng Service chuẩn:** Trên `/cskh/pool` hoặc `/cskh/campaigns`, Quản trị CRM thực hiện **Thu hồi (`Revoke`)** hoặc **Điều chuyển (`Transfer`)** toàn bộ `CSKH Work Item` trạng thái `assigned`/`in_progress`/`on_hold` sang người tiếp nhận (hệ thống tự động chuyển giao `CRM Task` hẹn lại và xóa sạch `DocShare`/`ToDo` cũ).
4. **Hậu kiểm `0` quyền tồn đọng:** Chạy `check_health_and_invariants.py` xác nhận `INV-01 == 0`, `INV-03 == 0` và mọi `CSKH Export Job` cũ của tài khoản đã bị chặn tải (`403`).

### 5.3. Nhân sự hoặc Trưởng nhóm chuyển đội (`Team Transfer`)
- Cập nhật nhánh quản lý trong `CRM Sales Hierarchy` thông qua giao diện quản trị chuẩn để hook `crm_cskh.services.attribution.on_sales_hierarchy_change` tự động đóng khoảng cũ và mở khoảng mới trong `CSKH Team Membership History` (`team-as-of`).
- **Tuyệt đối không** sửa lùi ngày hiệu lực quá khứ làm biến dạng điểm KPI đã chốt của đội cũ và đội mới.

---

## 6. Giới Hạn Chịu Lỗi Của Kiến Trúc Một VPS & Quy Tắc Xoay Vòng Bí Mật (`P4.md §3.3–§3.4`)

1. **Giới hạn Single-VPS (SPOF):** Hệ thống hiện chạy trên một máy chủ VPS duy nhất (`103.77.208.186`) — **không phải kiến trúc High Availability (HA) hay Zero-Downtime**. Nếu máy chủ gặp sự cố phần cứng/trung tâm dữ liệu, thực hiện khôi phục máy chủ mới theo **Mục 5 của [`rollback-runbook.md`](./rollback-runbook.md)** từ gói sao lưu mã hóa offsite gần nhất.
2. **Quy tắc bảo vệ tệp cấu hình & khóa giải mã:**
   - Tệp `/opt/crm-cskh-staging/.env.staging`, tệp `db_root_password.txt` (chỉ kết thúc bằng `\n` `LF`) và tệp khóa sao lưu `backup-passphrase.key` bắt buộc giữ quyền **`0600`**.
   - Khóa `encryption_key` của `site_config.json` (ngay khi được khởi tạo) và khóa giải mã gói backup phải luôn có bản lưu ký tại kho độc lập ngoài VPS; mất khóa đồng nghĩa các trường mã hóa trong bản sao lưu không thể giải mã.

---

## 7. Biên Bản Bàn Giao Vận Hành Cho Cổng `G24` (`Trạng Thái Hiện Tại: BLOCKED`)

| Hạng mục bàn giao bắt buộc (`P4.md §19` — `G24`, `§20.3` §9) | Trạng thái | Ghi chú / Đầu vào chờ mở khóa |
| :--- | :---: | :--- |
| Bộ sổ tay vận hành (`backup-restore.md`, `release-runbook.md`, `rollback-runbook.md`, `monitoring-alerts.md`, `operations-sop.md`, `huong-dan-su-dung-vi.md`) | **`READY (In-Repo)`** | Đã cập nhật đồng bộ với topology `compose.domain.yaml`, bản Việt hóa `4672041` và các nhãn của Owner. |
| Cài đặt timer kiểm tra sức khỏe & backup cục bộ có kiểm tra checksum/retention | **`READY TO INSTALL`** | Đã đóng gói tại [`install_systemd_timers.py`](../../deploy/ops/install_systemd_timers.py) & [`deploy/ops/systemd/`](../../deploy/ops/systemd/). |
| Tự động đẩy gói sao lưu mã hóa ra kho ngoài VPS (`Offsite Upload Automation`) | **`BLOCKED`** | Chờ cấp phát kho lưu trữ ngoài VPS (`BLK-INFRA-05`); hiện đang chép offsite có kiểm tra SHA-256 theo quy trình bán tự động. |
| Cảnh báo chủ động ra kênh ngoài hệ thống & bàn giao tài khoản `Operator` riêng | **`BLOCKED`** | Chờ Owner chỉ định đầu mối và kênh nhận cảnh báo (`BLK-GOV-06`, `BLK-INFRA-07`); hiện giữ chế độ tắt gửi ngoài theo đúng yêu cầu. |
