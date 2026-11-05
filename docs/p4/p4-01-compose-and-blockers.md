# P4-01: Hồ sơ Kiểm định Compose, Cấu hình Môi trường & Danh mục Blocker Hạ tầng

- **Phạm vi bàn giao:** `P4-01` (phần Docker Compose, env templates, kịch bản kiểm định/smoke và tài liệu vận hành)
- **Nhánh làm việc:** `codex/p4-01-compose` (tách từ baseline `main` `92e14af3532876ad5bc2468bf0e502474cc95a53`)
- **Hồ sơ đầu vào đối chiếu:** `P4-00` dự thảo tại `be33e6c931337c42b94184b2fe292e094bd51c97` (`docs/p4/entry-audit.md`, `docs/p4/infra-inventory.md`)
- **Upstream `frappe_docker` đã ghim:** `v4.0.1` (`2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`)
- **Trạng thái tổng hợp:**
  - Kiểm định tĩnh cấu hình Compose, phân tách mạng/volume/site/secrets, kiểm toán cổng và negative gates: **`PASS`** (`docker compose v5.6.1`)
  - Chạy thực tế container smoke trên dữ liệu giả (`P4-01 §4`): **`BLOCKED`** *(chờ `T0` chốt `P4-00` và chờ `T1B` bàn giao `APP_IMAGE@sha256:...` — kịch bản chạy tự động `run_isolated_smoke.py` đã sẵn sàng)*
  - Triển khai lên Staging / Production thật: **`BLOCKED`** *(thiếu các đầu vào hạ tầng ngoài kho mã liệt kê tại Mục 3)*

---

## 1. Danh mục Tệp Bàn giao (`deploy/compose/` & `docs/p4/`)

| Nhóm | Đường dẫn | Nội dung |
| :--- | :--- | :--- |
| **Upstream nguyên bản (`frappe_docker@v4.0.1`)** | [`deploy/compose/upstream/frappe_docker/`](../../deploy/compose/upstream/frappe_docker/) | `compose.yaml`, `LICENSE`, 7 tệp `overrides/compose.*.yaml` nguyên vẹn byte-for-byte từ commit `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`, kèm `UPSTREAM-PROVENANCE.json` khóa SHA-1/SHA-256. |
| **Overrides dự án** | [`deploy/compose/overrides/compose.cskh-common.yaml`](../../deploy/compose/overrides/compose.cskh-common.yaml) | Khóa digest `@sha256` (`APP_IMAGE`, `MARIADB_IMAGE`, `REDIS_IMAGE`), mạng 3 lớp (`edge_net`, `app_net`, `data_net`), named volumes/secrets theo `COMPOSE_PROJECT_NAME`, khóa `FRAPPE_SITE_NAME_HEADER`, giới hạn CPU/RAM, healthchecks và profile `migrate`. |
| | [`deploy/compose/overrides/compose.cskh-tls.yaml`](../../deploy/compose/overrides/compose.cskh-tls.yaml) | Ingress TLS Traefik `v3.7@sha256` cho Staging & Production; chỉ mở cổng `80`/`443` trên `INGRESS_BIND_IP`. |
| | [`deploy/compose/overrides/compose.cskh-isolated-smoke.yaml`](../../deploy/compose/overrides/compose.cskh-isolated-smoke.yaml) | Ingress loopback `127.0.0.1:18080:80` cho kiểm thử smoke cô lập bằng dữ liệu giả (không cần DNS/ACME công cộng). |
| **Mẫu biến môi trường (chỉ chứa mẫu/placeholder)** | [`deploy/compose/env/production.env.example`](../../deploy/compose/env/production.env.example) | Mẫu biến môi trường Production (`crm-cskh-prod`, `crm.example.invalid`, cổng `0.0.0.0:80/443`). |
| | [`deploy/compose/env/staging.env.example`](../../deploy/compose/env/staging.env.example) | Mẫu biến môi trường Staging (`crm-cskh-staging`, `staging-crm.example.invalid`, mặc định bind `127.0.0.1:8080/8443` và giới hạn tài nguyên chống ảnh hưởng chéo). |
| | [`deploy/compose/env/isolated-smoke.env.example`](../../deploy/compose/env/isolated-smoke.env.example) | Mẫu biến môi trường Smoke cô lập (`crm-cskh-p4-smoke`, `p4-smoke.localhost`, cổng `127.0.0.1:18080`). |
| **Vệ sinh Git & Kịch bản kiểm định** | [`deploy/compose/.gitignore`](../../deploy/compose/.gitignore), [`.gitattributes`](../../deploy/compose/.gitattributes) | Chặn commit `.env` thật, `.secrets/`, `*.key`, `*.pem`, `acme.json`; khóa dòng kết thúc `LF`. |
| | [`deploy/compose/scripts/validate_compose_config.py`](../../deploy/compose/scripts/validate_compose_config.py) | Kiểm định tĩnh provenance upstream, render `config --services` / `config --format json`, kiểm mạng/volume/secret/port và 3 ca negative fail-closed. |
| | [`deploy/compose/scripts/run_isolated_smoke.py`](../../deploy/compose/scripts/run_isolated_smoke.py) | Kịch bản chạy smoke cô lập đầy đủ ngay khi `T0` chốt `P4-00` và `T1B` bàn giao `APP_IMAGE@sha256`. |
| **Tài liệu kiến trúc & vận hành** | [`docs/p4/P4-ADR-01-compose-and-isolation.md`](./P4-ADR-01-compose-and-isolation.md) | Quyết định kiến trúc `P4-ADR-01` & `P4-ADR-04`, bảng reuse/override và ma trận cô lập môi trường. |

---

## 2. Kết quả Kiểm định `config --services`, Healthcheck & Cổng Công khai

### 2.1. Kết quả `docker compose ... config --services` (Đã Xác thực trên Cả 3 Môi trường)

Thực thi bằng `docker compose v5.6.1` với `0` cảnh báo trên `stderr`:

- **Chế độ mặc định (11 service — không tự chạy `migrator`):**
  ```text
  configurator
  backend
  frontend
  websocket
  queue-short
  queue-long
  scheduler
  db
  redis-cache
  redis-queue
  proxy
  ```
- **Chế độ cửa sổ bảo trì `--profile migrate` (12 service):**
  Bổ sung thêm duy nhất `migrator` (chỉ chạy `bench --site "$SITE_NAME" migrate` khi truyền tường minh `MIGRATE_SITES=true`).

### 2.2. Đặc tả Healthcheck Đã Áp đặt trên Toàn bộ Service

| Service | Loại tiến trình | Biểu thức Healthcheck / Điều kiện hoàn tất | Ngưỡng (`start_period` / `interval` / `timeout` / `retries`) |
| :--- | :--- | :--- | :--- |
| `configurator` | One-shot khởi tạo | Hoàn tất lệnh ghi `sites/apps.txt` & `common_site_config.json` với `exit_code == 0` (`service_completed_successfully`). | Phụ thuộc `db`, `redis-cache`, `redis-queue` đạt `healthy`. |
| `migrator` | One-shot theo profile `migrate` | Chỉ chạy khi được gọi tường minh trong quy trình deploy/migrate; kiểm tra `sites/$SITE_NAME/site_config.json` tồn tại và thoát `0`. | Không chạy nền ở chế độ thường trực. |
| `db` | Thường trực | `["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]` *(tái sử dụng từ `compose.mariadb.yaml`)* | `5s` / `5s` / `5s` / `5` |
| `redis-cache` | Thường trực | `["CMD", "redis-cli", "ping"]` | `5s` / `10s` / `3s` / `5` |
| `redis-queue` | Thường trực | `["CMD", "redis-cli", "ping"]` | `5s` / `10s` / `3s` / `5` |
| `backend` | Thường trực | `curl -fsS -H "Host:${SITE_NAME}" http://127.0.0.1:8000/api/method/ping \| grep -q pong` | `60s` / `15s` / `5s` / `5` |
| `frontend` | Thường trực | `curl -fsS -H "Host:${SITE_NAME}" http://127.0.0.1:${NGINX_LISTEN_PORT:-8080}/api/method/ping \| grep -q pong` | `30s` / `15s` / `5s` / `5` |
| `websocket` | Thường trực | Kết nối TCP nội bộ bằng `node` tới `127.0.0.1:9000` | `30s` / `15s` / `5s` / `5` |
| `queue-short` | Thường trực | Kiểm `PID 1` là tiến trình `bench` (`/proc/1/cmdline`) **và** `wait-for-it -t 3 redis-queue:6379` | `30s` / `20s` / `5s` / `5` |
| `queue-long` | Thường trực | Kiểm `PID 1` là tiến trình `bench` (`/proc/1/cmdline`) **và** `wait-for-it -t 3 redis-queue:6379` | `30s` / `20s` / `5s` / `5` |
| `scheduler` | Thường trực | Kiểm `PID 1` là tiến trình `bench` (`/proc/1/cmdline`) **và** `wait-for-it -t 3 redis-queue:6379` | `30s` / `20s` / `5s` / `5` |
| `proxy` | Thường trực | `["CMD", "traefik", "healthcheck", "--ping"]` | `10s` / `15s` (`10s` smoke) / `5s` / `5` |

### 2.3. Kết quả Kiểm toán Cổng Xuất bản Ra Host (`Port Exposure Audit`)

Kết xuất từ `docker compose config --format json` và kiểm tra tự động trong `validate_compose_config.py`:

| Môi trường | Service duy nhất mở cổng | Danh sách cổng host xuất bản | Các service nội bộ (`db:3306`, `redis-*:6379`, `backend:8000`, `websocket:9000`, `frontend:8080`, `queue-*`, `scheduler`) |
| :--- | :--- | :--- | :--- |
| **Production** | `proxy` | `0.0.0.0:80->80/tcp` *(chỉ ACME HTTP-01 & redirect 301 sang HTTPS)*, `0.0.0.0:443->443/tcp` | **`0` cổng host xuất bản** |
| **Staging** | `proxy` | `127.0.0.1:8080->80/tcp`, `127.0.0.1:8443->443/tcp` *(mặc định khóa loopback/VPN IP để không lộ Staging công khai)* | **`0` cổng host xuất bản** |
| **Isolated Smoke** | `proxy` | `127.0.0.1:18080->80/tcp` *(loopback cô lập, không đụng cổng `8000`/`8120`/`8220`/`8320` của Owner)* | **`0` cổng host xuất bản** |

---

## 3. Danh mục Đầu vào Hạ tầng & Điều kiện Chạy Còn thiếu (`BLOCKED`)

Theo yêu cầu `P4.md` (§0.12, §§1–5, §18) và đối chiếu hồ sơ `P4-00` (`be33e6c`), mọi hạng mục chưa có bằng chứng xác thực được đánh dấu **`BLOCKED`** rõ ràng dưới đây:

### 3.1. Nhóm Đầu vào Hạ tầng Ngoài Kho Mã (Bắt buộc trước khi mở cổng triển khai Staging / Production)

| Mã Blocker | Hạng mục hạ tầng | Trạng thái | Chi tiết đầu vào còn thiếu | Bằng chứng nghiệm thu cần Owner / T0 cung cấp để gỡ `BLOCKED` |
| :--- | :--- | :---: | :--- | :--- |
| **`BLK-INFRA-01`** | **VPS, Hệ điều hành, SSH quản trị & Tương thích MariaDB** | **`BLOCKED`** | Chưa có IP/cấu hình VPS Staging & Production (CPU/RAM/SSD), phiên bản Ubuntu/Debian LTS, cơ chế truy cập SSH key/VPN không dùng mật khẩu, cấu hình tường lửa biên (UFW / Cloud Security Group, lưu ý quy tắc chặn Docker vượt tường lửa `ufw` trên cổng không mong muốn), và quyết định ký nhận dùng `MariaDB 11.8` của `frappe_docker@v4.0.1` so với lưu ý `10.6/10.11` tại `docs/architecture/version-lock.md` §8.2. | Thông tin máy chủ, output kiểm tra cấu hình OS/firewall/disk và biên bản chốt phiên bản MariaDB của `T0`/Owner. |
| **`BLK-INFRA-02`** | **DNS, TLS, Tên miền & Kiểm soát Truy cập Staging** | **`BLOCKED`** | Chưa có FQDN thật cho Production và Staging, chưa có bản ghi DNS `A`/`AAAA`, chưa có địa chỉ email quản trị thật cho Let's Encrypt ACME (`LETSENCRYPT_EMAIL`) hoặc chứng chỉ TLS tổ chức, chưa chốt dứt điểm dùng Traefik trong Compose hay TLS proxy sẵn có bên ngoài VPS, và chưa có phương án giới hạn truy cập mạng cho Staging (VPN / IP allowlist). | Tên miền thật phân giải đúng IP đích, email/chứng chỉ TLS hợp lệ và cấu hình chặn truy cập công khai vào Staging. |
| **`BLK-INFRA-03`** | **Container Registry & Quyền Phân phối Image theo Digest** | **`BLOCKED`** | Chưa có địa chỉ Container Registry thật (ví dụ GHCR hoặc registry riêng), chưa có thông tin xác thực tách quyền tối thiểu giữa tài khoản **CI push** và tài khoản **VPS pull-only**, chưa chốt chính sách lưu giữ digest cũ phục vụ rollback. | URL registry, bằng chứng `docker pull <repo>@sha256:...` bằng token read-only và quy định retention. |
| **`BLK-INFRA-04`** | **Kho Secret, Lưu ký `encryption_key` & Quy trình Xoay vòng** | **`BLOCKED`** | Chưa có kho lưu trữ bí mật chính thức ngoài Git để cấp phát `/etc/crm-cskh/secrets/{prod,staging}/db_root_password.txt` (quyền `0600`), mật khẩu `Administrator`, SMTP/integration tokens; chưa có kho lưu ký độc lập ngoài VPS cho khóa `encryption_key` trong `site_config.json` (mất khóa này sẽ không giải mã được trường nhạy cảm khi khôi phục backup). | Quy chế lưu ký secret của Owner, đường dẫn host đã phân quyền `0600` và biên bản sao lưu `encryption_key` ngoài VPS. |
| **`BLK-INFRA-05`** | **Đích Backup Mã hóa Ngoài VPS & Quyền Restore** | **`BLOCKED`** | Chưa có kho lưu trữ đích ngoài VPS chính (S3 / Backblaze B2 / Cloudflare R2 / SFTP tách biệt vật lý), chưa có khóa mã hóa bản sao lưu (`restic` password / `gpg` key), chưa tách biệt khóa ghi backup hằng ngày với khóa xóa/khôi phục chống ransomware, và chưa có chỉ tiêu **RPO / RTO / lịch giữ bản sao** được Owner phê duyệt. | URI kho offsite, credential phân tách quyền và chữ ký chốt RPO/RTO phục vụ `P4-02`. |
| **`BLK-GOV-06`** | **Đầu mối Vận hành, Ký UAT & Quyền Go-Live / Rollback** | **`BLOCKED`** | Theo `entry-audit.md` (`be33e6c` §4), chưa ghi nhận danh tính cụ thể của người chịu trách nhiệm vận hành ngày 2, đầu mối ký UAT nghiệp vụ và đầu mối quyết định cutover/rollback. | Danh sách vai trò & đầu mối được Owner/T0 xác nhận trong `P4-00`. |

### 3.2. Nhóm Điều kiện Tiên quyết Để Thực thi Smoke Cô lập Runtime (`P4-01 §4`)

| Mã Blocker | Phụ thuộc phối hợp nội bộ | Trạng thái hiện tại | Ghi nhận thực tế tại thời điểm bàn giao | Điều kiện gỡ để chạy live smoke ngay |
| :--- | :--- | :---: | :--- | :--- |
| **`BLK-RUNTIME-01`** | **`T0` chốt hồ sơ `P4-00`** | **`BLOCKED`** | Hồ sơ `P4-00` đang ở bản dự thảo `be33e6c931337c42b94184b2fe292e094bd51c97` trên nhánh `codex/p4-entry-audit` (`P4-00: IN PROGRESS`), chưa được `T0` chốt/merge. | `T0` cung cấp SHA `P4-00` đã chốt (`--p4-00-approved-sha <SHA>`). |
| **`BLK-RUNTIME-02`** | **`T1B` bàn giao custom image theo `@sha256` digest** | **`BLOCKED`** | Nhánh `codex/p4-01-image` đang ở baseline `92e14af` và trên Docker Engine máy trạm (`v29.9.2`) chưa có image ứng dụng tùy biến đóng gói `frappe` + `crm` + `crm_cskh` + assets đã build. | `T1B` bàn giao digest bất biến `<repo>@sha256:<64-hex>` (`--app-image <DIGEST>`). |

> [!IMPORTANT]
> Docker Engine trên máy trạm hiện đang hoạt động (`v29.9.2`), các container cũ của Owner đều giữ nguyên không can thiệp. Ngay khi `BLK-RUNTIME-01` và `BLK-RUNTIME-02` được cung cấp, lệnh một bước tại Mục 4.2 sẽ tự động dựng stack cô lập `crm-cskh-p4-smoke` trên cổng loopback `127.0.0.1:18080`, nạp dữ liệu giả và xuất báo cáo JSON đầy đủ.

---

## 4. Lệnh Vận hành & Kiểm thử Đầy đủ

### 4.1. Kiểm định Tĩnh Ngoại tuyến & Preflight (Chạy được ngay, không chạm container Owner)

```bash
# 1. Kiểm định toàn bộ provenance upstream, config --services, mạng, volume, secret, cổng và negative gates:
python deploy/compose/scripts/validate_compose_config.py

# 2. Kiểm tra trạng thái preflight và danh sách blocker runtime hiện thời:
python deploy/compose/scripts/run_isolated_smoke.py --preflight-only
```

### 4.2. Chạy Kiểm thử Smoke Cô lập Dữ liệu Giả Ngay khi `T0` Chốt `P4-00` & `T1B` Có Image

```bash
python deploy/compose/scripts/run_isolated_smoke.py \
  --p4-00-approved-sha <P4_00_APPROVED_SHA> \
  --app-image <REPO>@sha256:<64_HEX_DIGEST_FROM_T1B> \
  --http-port 18080 \
  --report-out /tmp/p4-01-isolated-smoke-report.json
```

Kịch bản `run_isolated_smoke.py` thực hiện tuần tự và tự dọn sạch volume tạm khi kết thúc (`down --volumes --remove-orphans`, chỉ tác động project `crm-cskh-p4-smoke`):

1. Kết xuất và đối chiếu `docker compose config --services`.
2. Khởi chạy stack cô lập `crm-cskh-p4-smoke`, chờ `db` / `redis-*` đạt `healthy` và `configurator` thoát `0`, khởi tạo site dữ liệu giả `p4-smoke.localhost` cài `frappe` + `crm` + `crm_cskh` (múi giờ `Asia/Ho_Chi_Minh`, ngôn ngữ `vi`), rồi xác nhận **cả 10 service thường trực đều đạt `health == "healthy"`**.
3. Kiểm toán `Publishers` thực tế của toàn bộ container đang chạy: khẳng định **chỉ duy nhất `proxy`** mở `127.0.0.1:18080->80/tcp`.
4. Chạy thử `migrator` có kiểm soát (`--profile migrate`, `MIGRATE_SITES=true`) **2 lần liên tiếp** để xác nhận tính idempotent.
5. Kiểm tra từ chối truy cập Guest trên `/crm` và `/cskh/quality-overview`; kiểm tra đăng nhập HTTP `POST /api/method/login`, cờ cookie `sid` và `frappe.auth.get_logged_user`.
6. Kiểm tra giao diện tiếng Việt trên `/cskh/quality-overview` và `/crm`, xác nhận hiện diện nhãn tiếng Việt và cấu hình `Asia/Ho_Chi_Minh`.
7. Kiểm tra bảo vệ **CSRF**: gửi `POST /api/method/crm_cskh.api.p3_reporting.request_recompute` với `X-Frappe-CSRF-Token` giả mạo bắt buộc bị từ chối (`400`/`403`); gửi request kèm CSRF token thật bắt buộc thành công (`200`).
8. Kiểm tra **Socket.IO (`EIO=4`)** đi xuyên suốt `proxy` → `frontend` → `websocket` (kèm callback xác thực nội bộ từ `websocket` về alias `p4-smoke.localhost` trên `proxy`): kết nối không xác thực bị từ chối (`44/p4-smoke.localhost`), kết nối kèm phiên đăng nhập hợp lệ được chấp nhận (`40/p4-smoke.localhost`).
