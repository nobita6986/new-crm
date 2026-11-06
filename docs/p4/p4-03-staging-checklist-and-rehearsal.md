# P4-03: Checklist Sẵn Sàng Staging, DNS/TLS, Firewall, Secrets & Quy Trình Diễn Tập Migration (`G03`, `G05`, `G06`)

- **Gói công việc:** `P4-03` (Staging Provisioning, HTTPS Smoke & Migration Rehearsal `N-1 → N`)
- **Đầu vào khóa cứng:** Hồ sơ `P4-01` tích hợp tại `492423c43a3ddb15e8f5fea23124c4bc70a6dbb8` (`492423c`, baseline ứng dụng `92e14af3532876ad5bc2468bf0e502474cc95a53`)
  - Compose thực chạy smoke local: `2c1e7594471b119f2c41a85fa12d30a85b356785` (`2c1e759`)
  - Custom image thực đo local: `4de3dc8656b0077cabd4220afc6636da543e34f6` (`4de3dc8`, `APP_IMAGE=sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`)
- **Công cụ kiểm định tự động đi kèm:** [`deploy/ops/staging_https_smoke_and_reconcile.py`](../../deploy/ops/staging_https_smoke_and_reconcile.py)
- **Bằng chứng tiền kiểm hiện tại:** [`deploy/ops/evidence/p4-03-staging-preflight-blocked.json`](../../deploy/ops/evidence/p4-03-staging-preflight-blocked.json) (`status = "BLOCKED"`)

---

## 1. Danh Mục Đầu Vào Đang Chặn Thực Thi Live (`BLOCKED`)

Theo nguyên tắc `P4.md §0.12` và `§19`, không giả lập kết quả Staging khi chưa có hạ tầng và artifact từ xa. Toàn bộ đầu vào còn thiếu được khóa mã theo dõi dưới đây:

| Mã Blocker | Hạng mục bị chặn | Tác động tới gói / cổng | Đầu vào bắt buộc từ Owner / `T0` / `T1B` để gỡ `BLOCKED` | Trạng thái |
| :--- | :--- | :--- | :--- | :---: |
| **`BLK-INFRA-01`** | **Máy chủ Staging & Production (VPS, OS, SSH, MariaDB sign-off)** | `P4-03`, `P4-10` (`G03`, `G05`, `G23`) | Cấp IP/host Ubuntu/Debian LTS tách biệt (ưu tiên khác VPS với Production; nếu dùng chung tạm thời phải khóa giới hạn CPU/RAM của Staging), SSH key-only, và ký nhận dùng `MariaDB 11.8` của `frappe_docker@v4.0.1`. | **`BLOCKED`** |
| **`BLK-INFRA-02`** | **Tên miền FQDN, DNS `A`/`AAAA`, TLS/ACME & Giới hạn Mạng Staging** | `P4-03`, `P4-10` (`G05`, `G23`) | Cấp FQDN thật cho Staging & Production, trỏ bản ghi DNS `A`/`AAAA`, cấp `LETSENCRYPT_EMAIL` thật và danh sách IP VPN/văn phòng được phép truy cập Staging (không mở Staging công khai ra Internet). | **`BLOCKED`** |
| **`BLK-INFRA-03`** | **Container Registry được duyệt & Digest `APP_IMAGE` do `T1B` bàn giao** | `P4-02`, `P4-03`, `P4-10` (`G02` remote, `G04`, `G05`, `G23`) | Cấp Registry URL + token tách quyền (`push` cho CI `build-once`, `pull` read-only cho Staging/Prod) và bàn giao định danh bất biến `<registry>/<namespace>/crm-cskh@sha256:<64-hex>` từ `T1B`. | **`BLOCKED`** |
| **`BLK-INFRA-04`** | **Kho Secret trên Host (`0600` LF-only) & Lưu ký `encryption_key` ngoài VPS** | `P4-03`, `P4-05`, `P4-10` (`G03`, `G07`, `G08`, `G23`) | Khởi tạo `/etc/crm-cskh/secrets/{staging,prod}/db_root_password.txt` (quyền `0600`, kết thúc bằng `\n`, không có `\r`), mật khẩu `Administrator` riêng từng môi trường, và kho lưu ký mã hóa độc lập ngoài VPS cho `site_config.json` (`encryption_key`). | **`BLOCKED`** |
| **`BLK-INFRA-05`** | **Đích Backup Mã hóa Ngoài VPS & Phê duyệt RPO/RTO** | `P4-05`, `P4-10` (`G07`, `G08`, `G09`, `G23`) | Cấp bucket S3/B2/R2/SFTP tách biệt vật lý khỏi VPS, khóa mã hóa `restic`/`gpg`, tách quyền ghi hằng ngày với quyền xóa/restore, và chốt ngưỡng RPO/RTO mục tiêu. | **`BLOCKED`** |
| **`BLK-GOV-06`** | **Danh tính Đầu mối Vận hành, Người ký UAT 4 vai trò & Người duyệt Go-Live/Rollback** | `P4-07`, `P4-08`, `P4-10`, `P4-11` (`G09`, `G19`, `G22`, `G23`, `G24`) | Cấp danh sách định danh cá nhân (họ tên, tài khoản, email/điện thoại trực) cho: `Release Approver`, `Technical Operator`, `BOD Reviewer`, `CRM Admin UAT`, `Team Leader A/B UAT`, `Staff UAT`. | **`BLOCKED`** |
| **`BLK-INFRA-07`** | **Kênh Nhận Cảnh Báo Vận Hành (`CSKH_ALERT_WEBHOOK_URL` / SMTP nội bộ)** | `P4-07` (`G19`) | Cấp HTTPS Webhook (ChatOps/Telegram/Slack/Pager nội bộ) hoặc relay cảnh báo chính thức của đội vận hành để gửi và xác nhận nhận cảnh báo thử. | **`BLOCKED`** |
| **`DEP-WP-04..06`** | **Hoàn tất Kiểm định Bảo mật (`P4-04`), Restore Drill (`P4-05`) & Benchmark (`P4-06`)** | Chặn mở cổng `P4-08` UAT chính thức & `P4-10` Go-Live (`G07`–`G12`, `G18`) | Chỉ chạy UAT chính thức với người dùng cuối và trình ký Go-Live sau khi `P4-04`, `P4-05`, `P4-06` đạt `PASS` trên cùng topology/digest của Staging. | **`BLOCKED`** |

---

## 2. Checklist Kỹ Thuật Trước Khi Bật Staging (`G03` & `G05`)

### 2.1. Mạng Biên & Tường Lửa Host (`ufw` + Chuỗi `DOCKER-USER`)

> [!IMPORTANT]
> Trên Linux, Docker tự ghi quy tắc `DNAT` vào `iptables` có thể đi vòng qua `ufw` nếu cổng publish bind `0.0.0.0`. Bắt buộc kết hợp **khóa `INGRESS_BIND_IP`** trong `.env` và **khóa chuỗi `DOCKER-USER`**.

- [ ] **SSH quản trị:** Chỉ nhận SSH key (`PasswordAuthentication no`, `PermitRootLogin no`), giới hạn nguồn từ IP VPN/văn phòng quản trị.
- [ ] **Cổng công khai trên Production:** Chỉ mở `443/tcp` và `80/tcp` (chỉ phục vụ ACME HTTP-01 & redirect `301` sang HTTPS).
- [ ] **Chặn công khai trên Staging (`P4.md §5.7`):**
  - Phương án ưu tiên 1 (VPN / Bastion): Đặt `INGRESS_BIND_IP=<TAILSCALE_OR_WG_IP>` (hoặc `127.0.0.1` qua SSH tunnel), `HTTP_PUBLISH_PORT=80`, `HTTPS_PUBLISH_PORT=443`.
  - Phương án ưu tiên 2 (IP Allowlist trên `DOCKER-USER`): Chỉ cho phép dải CIDR văn phòng/VPN tới cổng `80`/`443` của Staging:
    ```bash
    # Ví dụ khóa cứng chuỗi DOCKER-USER cho giao diện mạng ngoài eth0:
    sudo iptables -I DOCKER-USER -i eth0 -p tcp -m multiport --dports 80,443 ! -s <OFFICE_VPN_CIDR> -j DROP
    ```
- [ ] **Cấm tuyệt đối xuất bản cổng nội bộ:** Xác minh bằng `python3 deploy/compose/scripts/validate_compose_config.py` và `ss -tulpn` rằng `3306` (MariaDB), `6379` (Redis), `8000` (Gunicorn), `8080` (Nginx nội bộ), `9000` (Socket.IO nội bộ) có **`0` cổng host xuất bản**.

### 2.2. DNS, TLS / ACME & Chính Sách Cookie Phiên (`sid`)

- [ ] Bản ghi DNS `A` (và `AAAA` nếu bật IPv6) của `SITE_NAME` phân giải đúng về IP ingress của Staging (`dig +short "$SITE_NAME"`), TTL `<= 300s` trước đợt diễn tập.
- [ ] `LETSENCRYPT_EMAIL` là hộp thư vận hành thật để nhận cảnh báo hết hạn chứng chỉ (nếu Staging khóa sau VPN không mở cổng `80` ra Internet, sử dụng DNS-01 challenge hoặc chứng chỉ nội bộ tin cậy nạp vào Traefik).
- [ ] Volume `${COMPOSE_PROJECT_NAME}_cert_data` lưu `acme.json` với quyền `0600`.
- [ ] Mọi truy cập `http://<SITE_NAME>/...` trả `HTTP 301/308` chuyển hướng sang `https://<SITE_NAME>/...`.
- [ ] Phản hồi `POST /api/method/login` qua HTTPS trả dòng header `Set-Cookie: sid=...` có đủ **`Secure`**, **`HttpOnly`**, **`SameSite=Lax`**, **`Path=/`** (kiểm tra tự động bằng `staging_https_smoke_and_reconcile.py https-smoke`).

### 2.3. Secrets, Lưu Ký `encryption_key` & Khóa Tác Động Phụ (`Side-Effect Lockdown`)

- [ ] Tạo tệp mật khẩu gốc MariaDB bằng Python ép dòng kết thúc `LF` (tuyệt đối không dùng trình soạn thảo chèn `CRLF`):
  ```bash
  sudo install -d -m 0700 /etc/crm-cskh/secrets/staging
  sudo python3 -c 'import secrets, pathlib; p = pathlib.Path("/etc/crm-cskh/secrets/staging/db_root_password.txt"); p.write_bytes((secrets.token_urlsafe(32) + "\n").encode("ascii")); p.chmod(0o600)'
  ```
- [ ] Sao lưu trường `encryption_key` trong `sites/${SITE_NAME}/site_config.json` ngay sau khi khởi tạo site vào kho lưu ký bí mật độc lập ngoài VPS (`BLK-INFRA-04`), phân quyền giải mã tách biệt.
- [ ] **Khóa gửi thông báo/email thật từ Staging (`P4.md §5.4`):** Không cấu hình SMTP thật trên Staging (hoặc ép `mail_server = "127.0.0.1"`, tắt toàn bộ outgoing email account và webhook ngoài hệ thống).
- [ ] **Cấm dữ liệu PII thật trên Staging (`P4.md §5.3`):** Chỉ nạp tập dữ liệu giả lập có kiểm soát (`*.example.invalid`, đầu số giả lập `090000xxxx`).

### 2.4. Bộ Tài Khoản Kiểm Thử & Dữ Liệu Giả Lập Bắt Buộc Trên Staging (`P4.md §5.5–§5.6`)

| Nhóm vai trò | Tài khoản Staging chuẩn (dữ liệu giả) | Role Frappe được cấp | Phạm vi kiểm thử bắt buộc |
| :--- | :--- | :--- | :--- |
| **Quản trị kỹ thuật** | `Administrator` | `System Manager` | Chỉ dùng cho bootstrap/vận hành kỹ thuật; **không** dùng làm tài khoản nghiệp vụ của BOD/Staff. |
| **Ban Giám đốc (BOD)** | `bod.uat@example.invalid` | `CSKH BOD Viewer` *(không cấp `System Manager`)* | Xem toàn công ty `/cskh/quality-overview`, team/employee/campaign quality, drill-down `0` PII thô. |
| **Admin nghiệp vụ** | `admin.cskh@example.invalid` | `CSKH CRM Admin`, `Sales Manager` | Import CSV, cấu hình chiến dịch/trọng số `50/30/20`, phân phối, thu hồi, điều chuyển, recompute KPI. |
| **Trưởng nhóm (2 đội)** | `leader.a@example.invalid`, `leader.b@example.invalid` | `Sales Manager` (gắn cây `TEAM-A` và `TEAM-B` trong `CRM Sales Hierarchy`) | Giám sát trong phạm vi đội, duyệt hiệu chỉnh/miễn trừ theo policy, bị chặn `403` khi sang đội khác. |
| **Nhân viên CSKH (2 đội)** | `staff.a1@example.invalid`, `staff.a2@example.invalid`, `staff.b1@example.invalid` | `Sales User` | Chỉ thấy `CSKH Work Item` đang sở hữu trên `/cskh/my-customers`, ghi liên hệ, đặt hẹn, xem `/cskh/scorecard`. |
| **Nhân viên ngừng nhận số / đạt trần** | `staff.capped@example.invalid` | `Sales User` (rule `enabled=0` hoặc đạt `capacity`) | Không được giao khách mới khi chạy preview/execute phân phối (`F09`). |
| **Tài khoản bị khóa** | `staff.locked@example.invalid` | `Sales User` (`User.enabled = 0`) | Phiên đăng nhập cũ bị từ chối ngay lập tức (`F30` / `S15`). |

Tập dữ liệu seed giả lập (kế thừa có kiểm soát từ `crm_cskh.tools.p3_e2e_seed` với allowlist tên site Staging) phải bao phủ đủ 9 tình huống của `P4.md §5.6`: số trùng nội batch & trùng DB, lead phone-only (`cskh_is_placeholder_name=1`), một khách qua nhiều chiến dịch, bàn giao `A → B → A` + chuyển đội giữa kỳ, lead chưa liên hệ, nhiều lần thử đa kênh (`phone_call`/`zalo`/`sms`), lịch hẹn quá hạn, SLA xuyên giờ nghỉ/cuối tuần/ngày lễ, `QA = N/A` (`86,47` tạm tính) và kỳ cũ `is_restated = 1`.

### 2.5. Checklist Sẵn Sàng Rollback Trước Khi Triển Khai / Nâng Cấp (`G09`)

Chi tiết cây quyết định 4 tầng nằm tại [`docs/p4/rollback-runbook.md`](./rollback-runbook.md). Trước mọi lượt nâng cấp trên Staging hoặc Production, bắt buộc tích đủ 6 mục sau:

- [ ] **Định danh digest `N-1`:** Ghi nhận rõ `APP_IMAGE_PREV=<registry>/<repo>@sha256:<64-hex>` đang chạy ổn định và xác minh image còn khả dụng trên Registry / local cache (`docker image inspect "$APP_IMAGE_PREV"`).
- [ ] **Phân loại tương thích Schema (`Class A / B / C / D`):** Đã ghi rõ bản phát hành thuộc nhóm tương thích ngược (`Class A/B` — cho phép Tier 2 lùi image trực tiếp) hay phá vỡ tương thích ngược (`Class C/D` — bắt buộc đi Tier 4 khôi phục bản sao lưu nếu lùi).
- [ ] **Điểm khôi phục tiền nâng cấp (`Pre-Upgrade Backup`):** Đã chạy `bench --site "$SITE_NAME" backup --with-files`, kiểm chứng checksum SHA-256 và đồng bộ bản mã hóa ra đích ngoài VPS (`BLK-INFRA-05`), kèm xác nhận `encryption_key` khớp kho lưu ký (`BLK-INFRA-04`).
- [ ] **Sẵn sàng công tắc ngắt khẩn cấp (`Tier 1 Kill Switch`):** Đã xác nhận lệnh ghi `cskh_p3_enabled = false` vào `sites/${SITE_NAME}/site_config.json` hoạt động tức thời không cần đổi image hay chạy lại migration.
- [ ] **Công cụ đo chênh lệch dữ liệu trước khi Restore (`Tier 4`):** Đã ghi mốc UTC `T_BACKUP` để chạy `check_health_and_invariants.py --since-utc "$T_BACKUP"` đối soát các giao dịch phát sinh sau backup trước khi ghi đè DB.
- [ ] **Thẩm quyền kích hoạt (`BLK-GOV-06`):** Đã chốt danh tính `Technical Operator` và `Release Approver` trực trong cửa sổ phát hành, cùng ngưỡng kích hoạt dừng khẩn cấp (`STOP RULE`).

---

## 3. Quy Trình Thực Thi `P4-03` Ngay Khi Có Registry Digest & Staging (`G03`, `G05`, `G06`)

### Bước 1 — Tiền kiểm cấu hình & gỡ `BLOCKED` (`Preflight`)

```bash
python3 deploy/compose/scripts/validate_compose_config.py
python3 deploy/ops/staging_https_smoke_and_reconcile.py preflight \
  --env-file /etc/crm-cskh/staging.env \
  --confirm-encryption-key-escrow \
  --out deploy/ops/evidence/p4-03-staging-preflight.json
# Bắt buộc kiểm tra trường `"status": "PREFLIGHT_READY"` trong JSON trước khi đi tiếp.
```

### Bước 2 — Triển khai đúng `APP_IMAGE` digest do `T1B` bàn giao (`G05`)

```bash
COMPOSE_CMD="docker compose --env-file /etc/crm-cskh/staging.env \
  -f deploy/compose/upstream/frappe_docker/compose.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.redis.yaml \
  -f deploy/compose/overrides/compose.cskh-common.yaml \
  -f deploy/compose/overrides/compose.cskh-tls.yaml"

# Kéo đúng digest bất biến (không build lại trên VPS)
$COMPOSE_CMD pull
$COMPOSE_CMD up -d db redis-cache redis-queue
$COMPOSE_CMD run --rm configurator
MIGRATE_SITES=true $COMPOSE_CMD --profile migrate run --rm migrator
$COMPOSE_CMD up -d backend frontend websocket queue-short queue-long scheduler proxy
```

### Bước 3 — Chạy Smoke HTTPS, Cookie `sid` `Secure`/`HttpOnly`, CSRF, `wss://` WebSocket & Queue/Scheduler

```bash
CSKH_SMOKE_ADMIN_PASSWORD='<STAGING_ADMIN_PASSWORD>' \
python3 deploy/ops/staging_https_smoke_and_reconcile.py https-smoke \
  --env-file /etc/crm-cskh/staging.env \
  --out deploy/ops/evidence/p4-03-staging-https-smoke.json

python3 deploy/ops/check_health_and_invariants.py \
  --env-file /etc/crm-cskh/staging.env \
  --mode tls \
  --environment-label staging \
  --out deploy/ops/evidence/p4-03-staging-health-invariants.json
```

### Bước 4 — Phối hợp `T1B` Diễn tập `migrate × 2` Trên Bản Sao `N-1` & Đối Soát Dữ Liệu (`G06` + `BLG-P4-MIG-01`)

1. Khởi tạo bản sao cô lập của site Staging đang ở trạng thái `N-1` và tạo bản sao lưu chuẩn `bench --site "$SITE_NAME" backup --with-files`.
2. Chụp snapshot đếm bản ghi và bất biến trước nâng cấp (`0` PII):
   ```bash
   python3 deploy/ops/staging_https_smoke_and_reconcile.py snapshot-counts \
     --env-file /etc/crm-cskh/staging.env \
     --site "$SITE_NAME" \
     --out deploy/ops/evidence/p4-03-mig-before.json
   ```
3. Chạy one-shot `migrator` **lần 1** trên digest `N` (đo thời gian thực thi & thời gian xả hàng đợi), sau đó chụp `p4-03-mig-after-run1.json`.
4. Chạy lại one-shot `migrator` **lần 2** trong điều kiện không đổi để chứng minh tính lặp lại (`idempotency`), sau đó chụp `p4-03-mig-after-run2.json`.
5. Chạy đối soát tự động (kiểm tra bảo toàn dòng `CRM Lead` / `Work Item` / `Assignment History` / `Contact Attempt` / `KPI Snapshot`, `0` chuyển đổi ngầm `self_reported -> verified`, `0` `QA = 0` giả, và kiểm tra `BLG-P4-MIG-01` trên `tabDocType` / `tabModule Def` theo [`docs/p4/staging-migration-backlog.md`](./staging-migration-backlog.md)):
   ```bash
   python3 deploy/ops/staging_https_smoke_and_reconcile.py compare-counts \
     --before deploy/ops/evidence/p4-03-mig-before.json \
     --after-run1 deploy/ops/evidence/p4-03-mig-after-run1.json \
     --after-run2 deploy/ops/evidence/p4-03-mig-after-run2.json \
     --out deploy/ops/evidence/p4-03-mig-reconcile.json
   ```
