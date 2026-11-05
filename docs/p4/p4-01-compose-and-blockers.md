# P4-01: Hồ sơ Kiểm định Compose, Cấu hình Môi trường & Danh mục Blocker Hạ tầng

- **Phạm vi bàn giao:** `P4-01` (phần Docker Compose, env templates, kịch bản kiểm định/smoke và tài liệu vận hành)
- **Nhánh làm việc:** `codex/p4-01-compose` (tách từ baseline `main` `92e14af3532876ad5bc2468bf0e502474cc95a53`)
- **Hồ sơ đầu vào đối chiếu:** `P4-00` dự thảo tại `be33e6c931337c42b94184b2fe292e094bd51c97` (`docs/p4/entry-audit.md`, `docs/p4/infra-inventory.md`)
- **Upstream `frappe_docker` đã ghim:** `v4.0.1` (`2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`)
- **Trạng thái tổng hợp:**
  - Kiểm định tĩnh cấu hình Compose, phân tách mạng/volume/site/secrets, kiểm toán cổng và 4 negative gates: **`PASS`** (`docker compose v5.6.1`)
  - Danh mục PII cấp trường theo mã nguồn thực tế (DB, log, attachment, import/export, nơi lưu bản sao): **`COMPLETED`** ([`docs/p4/pii-field-catalog.md`](./pii-field-catalog.md))
  - Chạy thực tế container smoke trên dữ liệu giả (`P4-01 §4`): **`BLOCKED`** *(chờ `T1B` hoàn tất build & bàn giao `APP_IMAGE` bất biến — kịch bản chạy tự động `run_isolated_smoke.py` đã khớp sẵn cả `<repo>@sha256:<64-hex>` lẫn local image ID `sha256:<64-hex>`)*
  - Triển khai lên Staging / Production thật: **`BLOCKED`** *(thiếu các đầu vào hạ tầng ngoài kho mã: DNS/TLS, Container Registry, Kho Secret + `encryption_key`, VPS/Firewall và Đích Backup Offsite liệt kê tại Mục 3)*

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
| | [`deploy/compose/scripts/validate_compose_config.py`](../../deploy/compose/scripts/validate_compose_config.py) | Kiểm định tĩnh provenance upstream, render `config --services` / `config --format json`, kiểm mạng/volume/secret/port và 4 ca negative fail-closed. |
| | [`deploy/compose/scripts/run_isolated_smoke.py`](../../deploy/compose/scripts/run_isolated_smoke.py) | Kịch bản chạy smoke cô lập tạo project riêng từng lượt (`crm-cskh-p4-smoke-<run_id>`), từ chối tài nguyên đã tồn tại, giới hạn `down --volumes` vào đúng stack do chính lượt đó tạo, tách báo cáo Socket.IO `polling` và RFC 6455 `websocket`, xuất JSON + log đã khử secret. |
| | [`deploy/compose/scripts/test_smoke_runner_safety.py`](../../deploy/compose/scripts/test_smoke_runner_safety.py) | Bộ 5 unit test kiểm chứng tự động cơ chế cô lập project từng lượt, từ chối volume/cổng sẵn có mà không gọi `down --volumes`, đọc `status` JSON của preflight và khử secret. |
| | [`deploy/compose/evidence/`](../../deploy/compose/evidence/) | Thư mục lưu báo cáo JSON và log đã khử secret phục vụ `T1C` đối chiếu. |
| **Tài liệu kiến trúc & bảo vệ dữ liệu** | [`docs/p4/P4-ADR-01-compose-and-isolation.md`](./P4-ADR-01-compose-and-isolation.md) | Quyết định kiến trúc `P4-ADR-01` & `P4-ADR-04`, hợp đồng biến nhận image phối hợp với `T1B`, bảng reuse/override và ma trận cô lập môi trường. |
| | [`docs/p4/pii-field-catalog.md`](./pii-field-catalog.md) | Danh mục PII cấp trường kiểm chứng trực tiếp từ mã nguồn (Frappe + `crm` + 23 DocType `CSKH *`, log, attachment, import/export và 9 nơi lưu bản sao). |

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

### 3.2. Điều kiện Tiên quyết Để Thực thi Smoke Cô lập Runtime Cục bộ (`P4-01 §4`)

| Mã Blocker | Phụ thuộc phối hợp nội bộ | Trạng thái hiện tại | Ghi nhận thực tế tại thời điểm bàn giao | Điều kiện gỡ để chạy live smoke ngay |
| :--- | :--- | :---: | :--- | :--- |
| **`BLK-RUNTIME-01`** | **`T1B` hoàn tất build & bàn giao `APP_IMAGE` bất biến** | **`BLOCKED`** | `T1B` đang thực thi bản dựng trong `codex/p4-01-image` (`deploy/images/`); trên Docker Engine máy trạm (`v29.9.2`) hiện chưa có custom image hoàn tất đóng gói `frappe` + `crm` + `crm_cskh` + assets đã build. Hợp đồng biến `APP_IMAGE` & `PULL_POLICY` giữa `T1A` và `T1B` đã khớp 100%. | `T1B` bàn giao digest `<repo>@sha256:<64-hex>` hoặc local immutable ID `sha256:<64-hex>` (`--app-image <IMMUTABLE_IMAGE>`). |

> [!IMPORTANT]
> Docker Engine trên máy trạm hiện đang hoạt động (`v29.9.2`), các container cũ của Owner đều giữ nguyên không can thiệp. Ngay khi `T1B` bàn giao `APP_IMAGE`, lệnh một bước tại Mục 4.2 sẽ tự động dựng stack cô lập `crm-cskh-p4-smoke` trên cổng loopback `127.0.0.1:18080`, nạp dữ liệu giả và xuất báo cáo JSON đầy đủ.

---

## 4. Lệnh Vận hành & Kiểm thử Đầy đủ

### 4.1. Kiểm định Tĩnh Ngoại tuyến, Unit Test An toàn Runner & Preflight (Không chạm container Owner)

> [!WARNING]
> **Quy tắc đọc kết quả `--preflight-only`:** Khi chạy kèm `--preflight-only`, mã thoát `exit 0` chỉ có nghĩa là bước đánh giá preflight đã thực thi xong và **có thể đi kèm `"status": "BLOCKED"`**. Mọi người gọi / script kiểm chứng **bắt buộc đọc trường `status` trong JSON** (`"PREFLIGHT_READY"` so với `"BLOCKED"`).

```bash
# 1. Kiểm định toàn bộ provenance upstream, config --services, mạng, volume, secret, cổng và 4 negative gates:
python -X utf8 deploy/compose/scripts/validate_compose_config.py

# 2. Chạy 5 unit test bảo vệ cô lập project từng lượt, từ chối tài nguyên sẵn có và khử secret:
python -m unittest discover -s deploy/compose/scripts -p "test_*.py" -v

# 3. Kiểm tra preflight hiện thời và xuất JSON + log đã khử secret (đọc trường `status` trong JSON):
python -X utf8 deploy/compose/scripts/run_isolated_smoke.py \
  --preflight-only \
  --report-out deploy/compose/evidence/p4-01-preflight-report.json \
  --log-out deploy/compose/evidence/p4-01-preflight.log
```

### 4.2. Chạy Kiểm thử Smoke Cô lập Dữ liệu Giả Ngay khi Nhận `APP_IMAGE` từ `T1B`

```bash
python -X utf8 deploy/compose/scripts/run_isolated_smoke.py \
  --app-image <REPO_AT_SHA256_OR_LOCAL_SHA256_ID_FROM_T1B> \
  --image-git-sha <T1B_COMMIT_SHA> \
  --http-port 18080 \
  --report-out deploy/compose/evidence/p4-01-isolated-smoke-report.json \
  --log-out deploy/compose/evidence/p4-01-isolated-smoke.log
```

Kịch bản `run_isolated_smoke.py` đảm bảo an toàn tài nguyên tuyệt đối và ghi nhận đầy đủ bằng chứng cho `T1C`:

1. **Cô lập từng lượt & từ chối tài nguyên sẵn có:** Sinh định danh riêng cho từng lượt chạy (`crm-cskh-p4-smoke-<run_id>`, `p4-smoke-<run_id>.localhost`). Nếu phát hiện bất kỳ container, volume, network trùng tên hoặc cổng `127.0.0.1:<http_port>` đang bận, runner dừng ngay với `exit 3` (`status: "REFUSED_PREEXISTING_RESOURCES"`, `cleanup_invoked: false`) mà **tuyệt đối không gọi `down --volumes`**. Lệnh `down --volumes --remove-orphans` chỉ chạy khi chính lượt đó đã khởi tạo stack (`owned_stack_created == True`) và kiểm chứng `0` tài nguyên tồn đọng sau khi dọn.
2. **Truy vết nguồn gốc (`Provenance`):** Ghi rõ `compose_provenance` (`head_sha`, `branch`, `worktree_clean`), `image_provenance` (`image_git_sha`, `app_image`, `image_id`, `repo_digests`, OCI `labels`).
3. **Khởi tạo dữ liệu giả, Healthcheck & Kiểm toán cổng:** Cài đặt `frappe` + `crm` + `crm_cskh` với `language=vi`, `time_zone=Asia/Ho_Chi_Minh` và bản ghi `CRM Lead` giả lập (`0900000123`, `synthetic-smoke@example.invalid`); xác nhận cả 10 service thường trực đạt `health == "healthy"`, `configurator` thoát `0`, và chỉ duy nhất `proxy` mở `127.0.0.1:<http_port>->80/tcp`.
4. **Diễn tập `migrator` x2:** Chạy `--profile migrate run --rm migrator` 2 lần liên tiếp (`MIGRATE_SITES=true`), ghi mã thoát `0` và thời gian thực thi từng lượt.
5. **Đăng nhập, Giao diện tiếng Việt & CSRF:** Kiểm chặn Guest (`302`/`403`), đăng nhập `Administrator`, trích xuất danh sách nhãn tiếng Việt thực tế + `Asia/Ho_Chi_Minh`, kiểm từ chối CSRF giả mạo + thiếu CSRF (`400`/`403`) và chấp nhận CSRF hợp lệ (`200`).
6. **Báo cáo tách bạch Socket.IO `polling` và RFC 6455 `websocket`:**
   - `socketio_polling`: kiểm tra `transport=polling` từ chối phiên chưa đăng nhập (`44/<site>`) và chấp nhận phiên hợp lệ (`40/<site>`).
   - `socketio_websocket`: chỉ ghi `PASS` kèm bằng chứng HTTP `101 Switching Protocols`, xác thực chữ ký `Sec-WebSocket-Accept` và nhật ký trao đổi frame thực tế (`2probe` → `3probe` → `5`, `0{...}` → `40/<site>,` → `40/<site>,{"sid":...}`, và Ping/Pong RFC 6455 `0x9` → `0xA`).
7. **Khử secret tự động (`SmokeLogger`):** Toàn bộ mật khẩu tạm của DB/Admin, cookie `sid`, token CSRF và mã phiên Socket.IO đều được khử thành `<REDACTED_*>` và kiểm chứng không rò rỉ trước khi ghi JSON & log.
