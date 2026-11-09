# Sổ Tay Phát Hành Production Có Điểm Dừng & Quy Tắc Chặn Khẩn Cấp (`P4-10` — Cổng `G23`)

- **Mã tài liệu:** `P4-RELEASE-RUNBOOK` (`docs/p4/release-runbook.md` — `P4.md §14`, `§20.2`)
- **Đầu vào khóa cứng:** Bộ Compose & image contract của `P4-01` tại `492423c` (`frappe_docker@v4.0.1` + `deploy/compose/overrides/compose.cskh-*.yaml`)
- **Tài liệu đi kèm bắt buộc:** [`backup-restore.md`](./backup-restore.md), [`rollback-runbook.md`](./rollback-runbook.md), [`monitoring-alerts.md`](./monitoring-alerts.md), [`operations-sop.md`](./operations-sop.md)
- **Hiện trạng hạ tầng VPS đang phục vụ (`https://crm.hrpartner.vn`):**
  - VPS `103.77.208.186` · Project `crm-cskh-vps-staging` · Site `crm-staging.localhost`
  - Wrapper thực tế: `/opt/crm-cskh-staging/staging-compose` · Cấu hình: `/opt/crm-cskh-staging/.env.staging` (`0600`)
  - Overlay tên miền thực tế: `/opt/crm-cskh-staging/compose.domain.yaml` (nối sau 8 tệp Compose gốc qua `--extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml`)
  - Image đang chạy: `sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` (source `467204176326f046442b5e20c381f9a895b6e176`)
- **Trạng thái thực thi Go-Live Production hiện tại:** **`BLOCKED`** *(Diễn tập khôi phục bản clone cô lập trên VPS đã `PASS` tại `142.598s` và timer sức khỏe 5 phút đã tự kích hoạt `PASS`; Go-Live toàn bộ P4 vẫn `BLOCKED` chờ Owner cấp đích lưu trữ offsite tự động `BLK-INFRA-05` cho `G07`, đầu mối/webhook cảnh báo `BLK-GOV-06`/`BLK-INFRA-07` cho `G19`/`G24`, ký nhận UAT 5 vai trò `G13..G22` và phê duyệt cắt chuyển — tuyệt đối chưa tự nâng P4 thành `PASS` hoặc tự cutover live)*.

---

## 1. Biến Môi Trường & Chuỗi Lệnh Compose Chuẩn Của Dự Án (`492423c`)

Mọi thao tác trên máy chủ Production bắt buộc dùng đúng bộ 8 tệp Compose đã ghim (tuyệt đối không chạy `git pull && bench update`, không gọi `docker build` trên VPS và không dùng tag động):

```bash
export ENV_FILE="/etc/crm-cskh/production.env"
export COMPOSE_CMD="docker compose --env-file ${ENV_FILE} \
  -f deploy/compose/upstream/frappe_docker/compose.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb-secrets.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.redis.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.https.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.migrator.yaml \
  -f deploy/compose/overrides/compose.cskh-common.yaml \
  -f deploy/compose/overrides/compose.cskh-tls.yaml"
```

> **Lưu ý cho topology có Domain Overlay trên VPS (`crm.hrpartner.vn`):** Khi tên miền công khai khác `SITE_NAME` nội bộ (`crm-staging.localhost`), wrapper `/opt/crm-cskh-staging/staging-compose` tự động gắn thêm tệp overlay `/opt/crm-cskh-staging/compose.domain.yaml` vào cuối danh sách và dùng tệp `/opt/crm-cskh-staging/.env.staging` (luôn giữ quyền `0600`). Khi gọi các công cụ Python kiểm tra trực tiếp, truyền kèm `--extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml` và `--public-domain crm.hrpartner.vn`.

---

## 2. Chuẩn Bị Trước Ngày Phát Hành (`T-7` đến `T-1`, `P4.md §14.1`)

- [ ] **Đóng nhánh phát hành:** Không nhận thêm tính năng mới ngoài phạm vi P1–P3 đã duyệt; mọi bản vá lỗi phải đi qua PR có test hồi quy và CI `P0`–`P3` xanh.
- [ ] **Đối chiếu bất biến Digest (`Staging == Production`):** Giá trị `APP_IMAGE=<registry>/<namespace>/crm-cskh@sha256:<64-hex>` trong `/etc/crm-cskh/production.env` **trùng khớp từng ký tự** với digest đã vượt qua cổng `G05`/`G06` trên Staging và được ghi trong `docs/p4/release-manifest.json`.
- [ ] **Phân loại Migration:** Xác nhận rõ bản phát hành thuộc `Class A` (chỉ code), `Class B` (thêm cột/bảng/index tương thích), `Class C` (backfill có idempotent checkpoint) hay `Class D` (cần ADR riêng).
- [ ] **Xác nhận RPO/RTO & Tài nguyên:** Đã có báo cáo diễn tập restore DR (`G08`) và báo cáo dung lượng/headroom (`G18`).
- [ ] **Đào tạo & Vai trò trực phát hành (`BLK-GOV-06`):** Đã ghi danh tính cụ thể cho 5 vị trí: `Release Owner`, `DB/Backup Owner`, `QA/UAT Owner`, `Decision Maker (Owner/BOD)`, `On-call Operator`; thông báo khung giờ bảo trì theo múi giờ `Asia/Ho_Chi_Minh`.

---

## 3. Tiền Kiểm Trước Giờ Cắt Chuyển (`T-0 Preflight`) & Quy Tắc `STOP RULE` (`P4.md §14.2`)

Thực thi tuần tự các lệnh tiền kiểm sau trước khi chạm vào dịch vụ đang chạy:

```bash
# 1. Kiểm định tĩnh cấu hình Compose, phân tách mạng, volume logs, secret và cổng xuất bản:
python3 deploy/compose/scripts/validate_compose_config.py

# 2. Kiểm tra điều kiện FQDN, digest @sha256, byte LF 0600 của db_root_password.txt và lưu ký encryption_key:
python3 deploy/ops/staging_https_smoke_and_reconcile.py preflight \
  --env-file "${ENV_FILE}" \
  --confirm-encryption-key-escrow \
  --out /var/lib/crm-cskh/releases/t0-preflight.json

# 3. Kiểm tra sức khỏe hiện hành, dung lượng đĩa (< 80% dùng, đủ chỗ cho image + backup) và kênh cảnh báo:
python3 deploy/ops/check_health_and_invariants.py \
  --env-file "${ENV_FILE}" \
  --mode tls \
  --environment-label production \
  --out /var/lib/crm-cskh/releases/t0-pre-health.json
```

> [!CAUTION]
> **QUY TẮC DỪNG KHẨN CẤP (`STOP RULE`):** Nếu bất kỳ lệnh nào ở trên trả `status != "PREFLIGHT_READY"` / `"PASS"`, hoặc bản sao lưu mã hóa offsite gần nhất chưa xác thực được checksum, hoặc lộ cổng `3306`/`6379` trên host (`ss -tulpn`), **DỪNG NGAY LẬP TỨC — tuyệt đối không chạy migration hay nâng cấp container**. Lưu nhật ký lỗi và báo cáo `Release Owner` dời cửa sổ phát hành.

---

## 4. Trình Tự Thực Thi Phát Hành Trên Production (`11 Bước Có Checkpoint`, `P4.md §14.3`)

### Bước 1 — Bật chế độ bảo trì & tạm ngưng nhận tác vụ ghi mới
```bash
SITE_NAME="$(grep '^SITE_NAME=' "${ENV_FILE}" | cut -d= -f2- | tr -d '\"'\'')"
$COMPOSE_CMD exec -T backend bench --site "${SITE_NAME}" set-maintenance-mode on
$COMPOSE_CMD exec -T backend bench --site "${SITE_NAME}" scheduler pause
```

### Bước 2 — Xác nhận worker không còn job đang thực thi & chụp đối chứng số liệu trước nâng cấp (`Pre-Upgrade Snapshot`)
- Kiểm tra thực tế `StartedJobRegistry == 0` trên cả 3 hàng đợi (`short`, `default`, `long`) thay vì chờ mù theo giây, sau đó chụp đối chứng `0` PII:
```bash
$COMPOSE_CMD exec -T backend /home/frappe/frappe-bench/env/bin/python -c '
import os, frappe
from rq import Queue
from rq.registry import StartedJobRegistry
from frappe.utils.background_jobs import get_redis_conn
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="'"${SITE_NAME}"'", sites_path=".")
frappe.connect()
conn = get_redis_conn()
active = {q: StartedJobRegistry(queue=Queue(q, connection=conn)).count for q in ("short", "default", "long")}
print("ACTIVE_RUNNING_JOBS=", active)
assert sum(active.values()) == 0, f"Workers still executing jobs: {active}"
frappe.destroy()
'

python3 deploy/ops/staging_https_smoke_and_reconcile.py snapshot-counts \
  --env-file "${ENV_FILE}" \
  --site "${SITE_NAME}" \
  --out /var/lib/crm-cskh/releases/prod-pre-migrate-counts.json
```

### Bước 3 — Tạo bản sao lưu Pre-Release toàn phần & xác nhận bản sao ngoài VPS (`G07`)
- Sao lưu tệp cấu hình hiện hành (`cp -p "${ENV_FILE}" "${ENV_FILE}.bak.$(date -u +%Y%m%dT%H%M%SZ)" && chmod 600 "${ENV_FILE}"*`) và chạy công cụ chuẩn [`create_encrypted_backup.py`](../../deploy/ops/create_encrypted_backup.py) ([`backup-restore.md`](./backup-restore.md)).
- **Checkpoint 1:** Chỉ đi tiếp sang Bước 4 khi kho lưu trữ ngoài VPS xác nhận đã nhận đủ gói `.tar.gz.enc` và đối chiếu khớp SHA-256 hai đầu (`manage_backup_lifecycle.py record-offsite`).

### Bước 4 — Kéo đúng Image Digest đã nghiệm thu trên Staging
```bash
$COMPOSE_CMD pull backend frontend websocket queue-short queue-long scheduler configurator
```

### Bước 5 — Đồng bộ `configurator` & chạy One-Shot `migrator` (có chốt chặn thứ tự `sites/apps.txt`)
```bash
# Ghi sites/apps.txt đúng thứ tự frappe -> crm -> crm_cskh từ apps-order.txt:
$COMPOSE_CMD run --rm configurator

# Chạy migration đúng một lần duy nhất (GHI CẤM dùng --skip-failing):
time MIGRATE_SITES=true $COMPOSE_CMD --profile migrate run --rm migrator
```
- **Checkpoint 2:** Nếu `migrator` thoát `!= 0`, **DỪNG NGAY** và chuyển sang cây quyết định tại [`rollback-runbook.md`](./rollback-runbook.md).

### Bước 6 — Khởi động lại có trật tự các service ứng dụng & mở lại `scheduler`
```bash
$COMPOSE_CMD up -d backend websocket frontend queue-short queue-long scheduler proxy
$COMPOSE_CMD exec -T backend bench --site "${SITE_NAME}" clear-cache
$COMPOSE_CMD exec -T backend bench --site "${SITE_NAME}" scheduler resume
```

### Bước 7 — Xác thực toàn bộ container chạy đúng Digest & kiểm tra nội bộ trước khi mở cổng
```bash
python3 deploy/ops/staging_https_smoke_and_reconcile.py snapshot-counts \
  --env-file "${ENV_FILE}" \
  --site "${SITE_NAME}" \
  --out /var/lib/crm-cskh/releases/prod-post-migrate-counts.json

python3 deploy/ops/check_health_and_invariants.py \
  --env-file "${ENV_FILE}" \
  --mode tls \
  --environment-label production \
  --out /var/lib/crm-cskh/releases/prod-post-migrate-health.json
```
- **Checkpoint 3:** Đảm bảo `status == "PASS"` trên cả 10 container và cả 6 bất biến `INV-01`..`INV-06`.

### Bước 8 — Tắt chế độ bảo trì (Mở lưu lượng người dùng)
```bash
$COMPOSE_CMD exec -T backend bench --site "${SITE_NAME}" set-maintenance-mode off
```

### Bước 9 — Chạy Smoke HTTPS sau phát hành (Tối thiểu 15 phút thao tác kiểm chứng, `P4.md §14.4`)
```bash
CSKH_SMOKE_ADMIN_PASSWORD='<PROD_SMOKE_PASSWORD>' \
python3 deploy/ops/staging_https_smoke_and_reconcile.py https-smoke \
  --env-file "${ENV_FILE}" \
  --out /var/lib/crm-cskh/releases/prod-https-smoke.json
```

| Hạng mục Smoke (`P4.md §14.4`) | Tiêu chí đạt (`PASS`) |
| :--- | :--- |
| **URL / TLS / Redirect** | HTTP `80` chuyển `301` sang HTTPS `443`, chứng chỉ hợp lệ (`>= 14 ngày`), mọi container khớp `@sha256`. |
| **Đăng nhập & Cookie `sid`** | `BOD`, `Admin`, `Staff` đăng nhập đúng quyền; `Set-Cookie: sid` có đủ `Secure`, `HttpOnly`, `SameSite=Lax`. |
| **Luồng P1 / P2 / P3** | Xem danh sách khách trong phạm vi; thử tạo liên hệ/lịch hẹn trên bản ghi kiểm thử synthetic tách biệt; BOD xem dashboard, bấm drill-down 1 chỉ số và đối chiếu 1 hành trình khách. |
| **Workers / Scheduler / Logs** | Hàng đợi xử lý mượt, nhịp `scheduler` cập nhật đúng hạn 5 phút, `0` lỗi OOM/DB lock/5xx bất thường. |
| **Bảo vệ PII & Quyền** | Che PII đúng vai trò, tệp `private` và nhật ký kiểm toán khóa đúng quyền. |

### Bước 10 — Mở tính năng theo lớp (`Progressive Rollout`, `P4.md §14.5`)
Khi phát hành lần đầu (Pilot → Mở rộng), ưu tiên mở tuần tự không "big bang":
1. Xác nhận đăng nhập & phân quyền người dùng ổn định.
2. Mở nhập dữ liệu và báo cáo chỉ đọc cho `Admin`/`BOD`.
3. Mở chia số và ghi nhận `Contact Attempt` / `Callback` cho nhóm nhân sự **Pilot** (giới hạn hạn mức `capacity`).
4. Đối soát kỳ đầu của `KPI Snapshot` (`check_health_and_invariants.py` `PASS`), sau đó mở rộng sang các đội còn lại theo quyết định của Owner. *(Giữ `qa_enabled = 0` mặc định cho tới khi bộ phận QA được duyệt vận hành).*

### Bước 11 — Cửa sổ theo dõi tăng cường (`Post-Release Watch Window`) & Đóng biên bản `G23`
- Trực theo dõi liên tục tối thiểu **2 giờ cao điểm đầu tiên** và rà soát lại sau **24 giờ** qua `check_health_and_invariants.py`.
- Lưu trọn bộ JSON/log đã khử secret kèm chữ ký xác nhận của `Release Owner` và `BOD/Operational Owner`.

---

## 5. Nhật Ký Thực Thi Phát Hành Production (`G23` — Trạng Thái Hiện Tại: `BLOCKED`)

| Trường thông tin | Giá trị ghi nhận |
| :--- | :--- |
| `release_id` | `PENDING — BLOCKED (Chờ Staging G05/G06, Gates G07..G22 & Phê duyệt Go-Live của Owner)` |
| `approved_image_digest` | `PENDING (BLK-INFRA-03)` |
| `maintenance_window_vn` | `PENDING (BLK-GOV-06)` |
| `post_release_smoke_and_watch` | **`BLOCKED`** |
