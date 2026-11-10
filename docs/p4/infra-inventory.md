# Kiểm Kê Hạ Tầng, Topology Compose & Ma Trận Tách Biệt `DEV / CI / STAGING-DR / STAGING-LIVE / PROD` (`P4-00` / `P4-01` — Cổng `G03`)

- **Mã tài liệu:** `P4-INFRA-INVENTORY` (`docs/p4/infra-inventory.md` — `P4.md §3`, `§5`, `§19 G03`, `§20.2`)
- **Ngày cập nhật:** `10/11/2026` (`Asia/Ho_Chi_Minh`)
- **Nguyên tắc vận hành (`G03`):**
  1. **Tận dụng môi trường cô lập sẵn có, không tự thuê thêm hạ tầng trả phí.**
  2. **Tách biệt tuyệt đối 6 chiều** giữa `DEV`, `CI`, `STAGING-DR-HTTPS` (cô lập thử nghiệm phá hủy/UAT), `STAGING-LIVE` (`https://crm.hrpartner.vn` đang phục vụ) và `PRODUCTION`: `COMPOSE_PROJECT_NAME`, `networks`, `ports`, `volumes`, `secrets` và `SITE_NAME`.
  3. **Tuyệt đối không sửa code trực tiếp trên VPS (`git pull` / sửa tay trong container bị cấm):** mọi dịch vụ ứng dụng chỉ chạy từ artifact image bất biến khóa `@sha256`.

---

## 1. Ma Trận Phân Biệt Cấu Hình 5 Môi Trường (`DEV / CI / STAGING-DR / STAGING-LIVE / PROD`)

| Chiều cách ly | `DEV` (Cục bộ phát triển) | `CI Isolated Smoke` (Runner tạm thời) | `STAGING-DR HTTPS` (Cô lập UAT / Rollback / DR) | `STAGING-LIVE VPS` (Đang phục vụ Owner) | `PRODUCTION` (Mẫu mục tiêu khi Go-Live) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. `COMPOSE_PROJECT_NAME`** | `crm-cskh-p3-ui` (hoặc `crm-cskh-dev`) | `crm-cskh-p4-smoke` (`crm-cskh-p4-smoke-<run_id>`) | `crm-cskh-staging-dr-https` (`crm-cskh-dr-https-<run_id>`) | `crm-cskh-vps-staging` (clone DR tạm thời: `crm-p4-dr-<ts>`) | `crm-cskh-prod` (hoặc mẫu `crm-cskh-staging` khi tách host) |
| **2. `SITE_NAME` & Tên miền** | `p3ui.localhost` / `dev.localhost` | `p4-smoke.localhost` (`p4-smoke-<run_id>.localhost`) | `staging-dr-uat.crm-internal.localhost` | `crm-staging.localhost` sau TLS domain `https://crm.hrpartner.vn` (`103.77.208.186`) | `<prod-domain-do-Owner-cap>` (mẫu `crm.example.invalid`) |
| **3. Tệp cấu hình mẫu / thực tế** | [`deploy/p3/docker-compose.yml`](../../deploy/p3/docker-compose.yml) | [`isolated-smoke.env.example`](../../deploy/compose/env/isolated-smoke.env.example) | [`staging-dr-https.env.example`](../../deploy/compose/env/staging-dr-https.env.example) + [`compose.cskh-dr-https.yaml`](../../deploy/compose/overrides/compose.cskh-dr-https.yaml) | `/opt/crm-cskh-staging/.env.staging` (`0600`) + wrapper `/opt/crm-cskh-staging/staging-compose` + `compose.domain.yaml` | [`production.env.example`](../../deploy/compose/env/production.env.example) (`/etc/crm-cskh/prod/production.env`, `0600`) |
| **4. Mạng Docker (`networks`)** | Mạng bridge nội bộ riêng của project `crm-cskh-p3-ui_*` | `crm-cskh-p4-smoke_edge_net`, `_app_net`, `_data_net` (`internal: true`) | `crm-cskh-staging-dr-https_edge_net`, `_app_net`, `_data_net` (`internal: true`) | `crm-cskh-vps-staging_edge_net`, `_app_net`, `_data_net` (`internal: true`) | `crm-cskh-prod_edge_net`, `_app_net`, `_data_net` (`internal: true`) |
| **5. Cổng mở trên Host (`ports`)** | Loopback `127.0.0.1` (`8000`/`8320`, đang dừng khi không dùng) | Chỉ `127.0.0.1:18080->80/tcp` (`proxy`); **`0` cổng DB/Redis/Worker** | Chỉ `127.0.0.1:18081->80/tcp` (301) & `127.0.0.1:18443->443/tcp` (TLS 1.3); **`0` cổng DB/Redis** | Chỉ `80/tcp` (redirect HTTPS) & `443/tcp` (TLS `crm.hrpartner.vn`) qua `proxy`; **`0` cổng `3306`/`6379`/`8000`/`9000` ra Internet** | Chỉ `0.0.0.0:80->80/tcp` & `0.0.0.0:443->443/tcp` (`proxy`); mẫu Staging phụ: `127.0.0.1:8080/8443` |
| **6. Docker Volumes (`volumes`)** | Volume cục bộ riêng của `crm-cskh-p3-ui` | `crm-cskh-p4-smoke_{db_data,sites,redis_queue_data,logs}` (xóa sạch sau run) | `crm-cskh-staging-dr-https_{db_data,sites,redis_queue_data,logs,cert_data}` (xóa sạch sau diễn tập) | `crm-cskh-vps-staging_{db_data,sites,redis_queue_data,logs}` + chứng chỉ TLS riêng | `crm-cskh-prod_{db_data,sites,redis_queue_data,logs}` + volume TLS ACME riêng |
| **7. Secrets & Khóa mã hóa (`secrets`)** | Mật khẩu giả lập cục bộ không dùng lại cho môi trường khác | Tệp tạm ngoài Git (`0600`, LF-only) sinh ngẫu nhiên mỗi lượt (`secrets.token_urlsafe`) | Tệp tạm `db_root_password.txt` (`0600`, LF), CA key xóa ngay sau khi ký `server.crt` (`0600`) | Docker secret `crm-cskh-vps-staging_db_password` từ tệp host `0600`; `/etc/crm-cskh/{offsite-backup,alerting}.env` (`0600`); `backup-key` lưu ngoài VPS | Docker secret `crm-cskh-prod_db_password` tại `/etc/crm-cskh/secrets/prod/db_root_password.txt` (`0600`) + `encryption_key` lưu ký kho riêng |
| **8. Dữ liệu & PII Policy** | Chỉ dữ liệu tổng hợp phát triển | Chỉ dữ liệu tổng hợp kiểm thử tự động | Chỉ dữ liệu tổng hợp `@example.test` / `@example.invalid` (chặn cứng PII thật) | Dữ liệu nghiệm thu & vận hành của Owner (cấm chạy test phá hủy / bắn tải) | Dữ liệu thật Production (cấm chép thô chưa che PII ngược về DEV/CI/Staging) |

---

## 2. Kiến Trúc 10 Dịch Vụ Daemon + 2 Dịch Vụ One-Shot Trên Stack Chuẩn P4

Toàn bộ các stack `CI`, `STAGING-DR-HTTPS`, `STAGING-LIVE VPS` và `PRODUCTION` ghép từ cùng bộ tệp gốc đã ghim phiên bản trong [`deploy/compose/`](../../deploy/compose/) (được kiểm tra tự động bằng [`validate_compose_config.py`](../../deploy/compose/scripts/validate_compose_config.py) — bằng chứng tại [`compose-validation-4-stacks.json`](../../deploy/ops/evidence/p4-https-delta-20261109/compose-validation-4-stacks.json)):

1. **10 dịch vụ chạy thường trực (`restart: unless-stopped` + healthcheck):**
   - Tầng biên (`edge_net` + `app_net`): `proxy` (`traefik:v3.7@sha256:68ae5fe5...`), `frontend` (Nginx reverse proxy gắn `security_headers.conf`).
   - Tầng ứng dụng (`app_net` + `data_net`): `backend` (Gunicorn), `websocket` (Node Socket.IO), `queue-short`, `queue-long`, `scheduler` (duy nhất `1` bản thể chạy).
   - Tầng dữ liệu cô lập (`data_net` với `internal: true` — không định tuyến ra ngoài): `db` (`mariadb:11.8@sha256:f16d7a34...`), `redis-cache`, `redis-queue` (`redis:8.6-alpine@sha256:ac2da09b...`).
2. **2 dịch vụ one-shot có kiểm soát:**
   - `configurator`: khởi tạo `common_site_config.json` và thứ tự `apps.txt` (`frappe`, `crm`, `crm_cskh`).
   - `migrator` (chỉ chạy khi bật `--profile migrate` và `MIGRATE_SITES=true`; ở trạng thái thường trực `MIGRATE_SITES=false` để không tự chạy migration ngầm khi container khởi động lại).
3. **Thứ tự ghép overlay trên VPS `103.77.208.186` (`crm-cskh-vps-staging`):**
   - Wrapper `/opt/crm-cskh-staging/staging-compose` nạp `/opt/crm-cskh-staging/.env.staging` (`0600`) và ghép `compose.domain.yaml` ở **cuối cùng** để giữ nguyên cấu hình HTTPS/domain `crm.hrpartner.vn` mà không phải sửa tệp Compose trong Git ([`live-preservation-check.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-preservation-check.json)).

---

## 3. Các Rào Chắn Kỹ Thuật Ngăn Đụng Độ Giữa Môi Trường Cô Lập Và Site Của Owner (`G03`)

1. **Chặn đụng độ Project / Site / Port / Secret (`assert_isolated_from_owner_stack` & `validate_staging_seed_target`):**
   - [`manage_isolated_staging_dr.py`](../../deploy/ops/manage_isolated_staging_dr.py) và [`seed_staging_synthetic.py`](../../deploy/ops/seed_staging_synthetic.py) từ chối ngay từ bước preflight nếu:
     - `COMPOSE_PROJECT_NAME` trùng `crm-cskh-vps-staging`, `crm-cskh-prod`, `crm-cskh-dev`, `crm-cskh-p3-ui`...
     - `SITE_NAME` trùng `crm-staging.localhost`, `crm.hrpartner.vn`, `dev.localhost`, `p3ui.localhost`...
     - `INGRESS_BIND_IP` là `0.0.0.0`/`::` hoặc cổng trùng `80`/`443` của reverse proxy chính.
     - `DB_PASSWORD_SECRETS_FILE` nằm trong `/opt/crm-cskh-staging` hoặc `/etc/crm-cskh/secrets/prod`.
     - Trong DB tồn tại bất kỳ dòng `CRM Lead` nào có email khác rỗng và không thuộc `@example.test` / `@example.invalid`.
2. **Chặn thư mục cùng VPS giả danh Offsite (`st_dev` Guard):**
   - [`manage_backup_lifecycle.py`](../../deploy/ops/manage_backup_lifecycle.py) kiểm tra `os.stat().st_dev` và từ chối mọi đường dẫn nằm cùng thiết bị lưu trữ của VPS nguồn, đồng thời cấm tuyệt đối việc tải `backup-key` lên cùng kho chứa `backup.tar.gz.enc`.
3. **Kiểm toán tự động trong CI (`verify_pr_isolation.py`):**
   - Quét toàn bộ `.github/workflows/*.yml`, `release-lock.json`, ràng buộc Python (`22` gói hệ thống + `154` gói bench) và `108+` tệp P4 bảo đảm `0` khóa bí mật/PEM rò rỉ vào Git ([`pr-isolation-audit.json`](../../deploy/ops/evidence/p4-https-delta-20261109/pr-isolation-audit.json)).

---

## 4. Hiện Trạng Kiểm Kê Trên VPS `103.77.208.186` & Các Hạng Mục Chờ Owner Cấp

- **Đã xác nhận thực tế trên VPS `103.77.208.186` (`ops_sha = 801be22b3be2c8199704c4da7442b0c2e0edf352`):**
  - Đang phục vụ `https://crm.hrpartner.vn` trên project `crm-cskh-vps-staging`, site `crm-staging.localhost`, image `crm-cskh:0.0.1-4672041` (`sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9`), `10/10` container daemon `healthy`, `scheduler` đã bật, `crm-cskh-healthcheck.timer` tự chạy mỗi 5 phút (`PASS`), `crm-cskh-backup.timer` đã `enabled`.
  - Clone khôi phục cô lập của `verify_offsite_restore.py` sử dụng project ngẫu nhiên riêng `crm-p4-dr-<ts>`, cổng loopback riêng và đã dọn sạch `0` container/volume/network dư sau khi đo RTO Pha A `142,598s` ([`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json)).
- **Đầu vào hạ tầng ngoài VPS còn chờ Owner cấp (xem bảng tổng hợp duy nhất tại [`P4-exit-report.md`](./P4-exit-report.md) Mục 3):**
  - `BLK-INFRA-05`: Đích lưu trữ offsite tự động ngoài VPS `103.77.208.186` + kho lưu ký khóa tách biệt (`Key Escrow`).
  - `BLK-INFRA-07` & `BLK-GOV-06`: Kênh HTTPS Webhook cảnh báo đã duyệt + danh sách người trực vận hành / người ký UAT.
