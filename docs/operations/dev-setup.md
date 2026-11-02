# DEV SETUP — Hướng dẫn dựng & vận hành môi trường DEV (WP-01 / P0-A)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/operations/dev-setup.md`
> **Phiên bản:** 1.7 — 02/11/2026 (đối chiếu P0 đã nghiệm thu, bắt buộc security app, sửa lệnh Vite/CSRF/Socket.IO)
> **Tài liệu cha:** [`../root-plan/P0.md`](../root-plan/P0.md) (§3 P0-A) · [`../architecture/version-lock.md`](../architecture/version-lock.md)
> **Đối tượng:** người thứ hai cần setup lại từ repo mà không cần hướng dẫn miệng.

> **TRẠNG THÁI HIỆN HÀNH:** P0 đã nghiệm thu GO P1 ngày 2026-11-01, PR #2 merge `584cbc2`; CI main [#41320842991](https://github.com/nobita6986/new-crm/actions/runs/41320842991) PASS. DEV/Vite/CSRF/Socket.IO và 10 ảnh tiếng Việt đã được kiểm chứng; xem [P0 exit report](../architecture/p0-exit-report.md).
> **Bắt buộc:** `INSTALL_CSKH=1` và đủ ba app `frappe`, `crm`, `crm_cskh`. Bootstrap từ chối cấu hình bỏ security app hoặc source app bị thiếu trước khi gọi Bench/DB.
> **Giới hạn:** chỉ baseline DEV/dữ liệu giả; tương thích production, proxy/CORS mới và hiệu năng import/index thuộc backlog riêng. Các snapshot §9/§11/§12 giữ lịch sử WP-01, không thay verdict hiện hành.

---

## 1. Mục tiêu môi trường DEV

Theo P0-A và master plan §4.3: DEV chạy **cục bộ**, chỉ dữ liệu giả, cho phép sửa code Vue/Python và
thấy kết quả mà **không cần deploy VPS**. Stack:

```
[trình duyệt]  ->  127.0.0.1:8000  ->  container bench (bench start)
                                          |-- db            (MariaDB 11.8)
                                          |-- redis-cache   (Redis 8.6)
                                          |-- redis-queue   (Redis 8.6)
```

- App bắt buộc trên site: `frappe` (Framework), `crm` (Frappe CRM), `crm_cskh` (permission/API guards).
- Phiên bản **ghim** ở [`version-lock.md`](../architecture/version-lock.md) §2/§5 và trong `deploy/dev/.env.example`.

---

## 2. Điều kiện tiên quyết

### 2.1. Bắt buộc

| Thành phần | Yêu cầu | Ghi chú |
|---|---|---|
| Docker Engine | Chạy **Linux container** | Trên Windows: **Docker Desktop + WSL2 backend** |
| Docker Compose | v2 (`docker compose`) | |
| RAM cho Docker | ≥ 4 GB (khuyến nghị 8 GB+) | Stack DEV 4 container |
| Git | bất kỳ bản gần đây | đã xác minh `2.55.0` |
| Node | `^20.19.0 \|\| >=22.12.0` | Host `v22.22.1`; pinned bench image `v24.22.0` — cả hai đạt |
| Yarn | 1.x (classic) | Pinned bench image có Yarn `1.22.22`; không cần Yarn trên host để bootstrap |

### 2.2. Trên Windows (quan trọng)

Docker Desktop cho Linux container cần **WSL2**. Windows 11 **Home** **hỗ trợ WSL2** — việc không có
**Hyper-V role đầy đủ** **không** đồng nghĩa WSL2 không khả dụng (WSL2 dùng `VirtualMachinePlatform`).
Nếu `wsl --status` báo chưa cài:

```powershell
# Chạy trong PowerShell CÓ QUYỀN ADMIN, sau đó khởi động lại máy:
wsl --install
# Sau reboot, kiểm tra:
wsl --status ; wsl -l -v
```

Sau đó trong Docker Desktop → Settings → General: bật **"Use the WSL 2 based engine"**.

**Khuyến nghị I/O:** giữ bench trong **volume Linux** (cấu hình trong `docker-compose.yml` đã làm sẵn).
Không bind-mount source lên ổ NTFS cho đường nóng vì chậm I/O (P0 §1.2).

> Owner đã cài WSL2 và bật backend WSL2 trên Docker Desktop cho lần chạy WP-01 ngày 2026-10-15. Bằng chứng hiện tại ở §9 và `deploy/dev/evidence/wp01-runtime.md`.

---

## 3. Lấy mã nguồn

```bash
git clone https://github.com/nobita6986/new-crm.git
cd new-crm
```

Repo dự án **chỉ chứa docs + cấu hình + custom app** (`apps/crm_cskh` ở WP-02). **Không** chứa source
`frappe`/`crm`: các app này được `bootstrap.sh` lấy đúng SHA vào volume bench.

---

## 4. Setup lần đầu

```bash
cd deploy/dev
cp .env.example .env      # .env đã bị .gitignore — KHÔNG commit
```

`.env.example` đã chứa sẵn tag/SHA/digest đã khóa. Chỉ đổi nếu có lý do (và ghi lại vào version-lock).

```bash
# 4.1 Dựng hạ tầng + container bench
docker compose up -d db redis-cache redis-queue bench

# 4.2 Sửa quyền cho named volume (chỉ cần lần đầu, tránh lỗi permission khi bench init)
docker compose exec -u root bench bash -lc 'mkdir -p /workspace && chown -R frappe:frappe /workspace'

# 4.3 Bootstrap: bench init + get-app (ghim SHA) + new-site + install-app + migrate
docker compose exec bench bash /src/deploy/dev/bootstrap.sh
```

`bootstrap.sh` làm, theo thứ tự và **idempotent** (chạy lại an toàn):

1. `bench init --skip-redis-config-generation --frappe-branch <FRAPPE_TAG>` nếu chưa có bench.
2. `git checkout <FRAPPE_SHA>` và **đối chiếu SHA** (lệch → dừng).
3. `bench set-config -g` trỏ `db_host=db`, `redis_cache/queue` tới service của compose.
4. `bench get-app --branch <CRM_TAG> https://github.com/frappe/crm.git` rồi `git checkout <CRM_SHA>` + đối chiếu.
5. `bench new-site <SITE> --no-mariadb-socket --mariadb-user-host-login-scope=% ...`.
6. `developer_mode=1`, `time_zone=Asia/Ho_Chi_Minh` cho site DEV.
7. `install-app crm`; bắt buộc `INSTALL_CSKH=1`: symlink `/src/apps/crm_cskh` → `apps/crm_cskh`, cài editable package rồi `install-app crm_cskh`.
8. `bench migrate`.
9. In ra SHA thực tế của `frappe`/`crm` + `list-apps` để đối chiếu version-lock.

**Kỳ vọng sau bước này**: `bench --site dev.localhost list-apps --format json` chứa đủ `frappe`, `crm`, `crm_cskh`. `.env` cũ có `INSTALL_CSKH=0` cần đổi thành `1`; không chạy seed/server khi thiếu guard app.

---

## 5. Seed dữ liệu giả

```bash
docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh
```

`seed_demo.py` (idempotent, deterministic) tạo theo P0 §7:

- Cấu hình DEV: `System Settings.time_zone=Asia/Ho_Chi_Minh` và bật
  `FCRM Settings.enable_sales_hierarchy` để probe dùng đúng timezone và cây sales đã seed.
  Đây là cấu hình môi trường thử nghiệm, **không** phải bằng chứng ma trận quyền đã an toàn.

- **User**: `bod.viewer@dev.local`, `crm.admin@dev.local`, `team1.leader@dev.local`,
  `staff.a@dev.local`, `staff.b@dev.local`, `team2.leader@dev.local`, `staff.c@dev.local`
  (mật khẩu DEV giả `DevSeed-Only-2026!`).
- **Role PoC**: `CSKH BOD Viewer`, `CSKH CRM Admin` (least-privilege; **không** cấp System Manager).
  Leader/Staff dùng role gốc `Sales Manager` / `Sales User`.
- **2 team** qua `CRM Sales Hierarchy`: Team1 (Leader + Staff A/B), Team2 (Leader + Staff C).
- **15 Lead giả** (A1–A4 / B1–B4 / C1–C4 + U1–U3 chưa giao). Số điện thoại là placeholder
  `0900000xx` — **không thể gọi được**.
- Lead chỉ có SĐT (A4, U1) dùng tên tạm `Khách chưa rõ tên - DEMOx` (kịch bản P0 D1).
- **Call Log** với status kỹ thuật `No Answer` / `Busy` / `Completed` — **không** phải kết quả CSKH.
- **Task**, **FCRM Note**, **File private** đính kèm, `status_change_log`.
- Một case chuyển giao trạng thái A2: `staff.a → staff.b` + note audit.

> Lịch sử phân phối hạng nhất (Assignment History) là việc của P1/ADR-007 — seed chỉ tạo **trạng thái** + note.

---

## 6. Start / Stop / Migrate

```bash
# START server DEV (giữ container, chạy honcho)
docker compose exec bench bash -lc 'cd /workspace/frappe-bench && bench start'

# Terminal thứ hai: Vite trên frontend CRM đã ghim
docker compose exec bench bash -lc 'cd /workspace/frappe-bench/apps/crm/frontend && node node_modules/.bin/vite --host 0.0.0.0 --port "$VITE_DEV_PORT"'

# STOP server DEV: Ctrl+C trong phiên bench start
# STOP toàn bộ container (giữ dữ liệu)
docker compose stop

# XOÁ container nhưng GIỮ volume dữ liệu
docker compose down

# XOÁ luôn dữ liệu DEV (reset hoàn toàn) — CHỈ trên DEV
docker compose down -v

# MIGRATE khi có metadata/schema/patch mới
docker compose exec bench bash -lc 'cd /workspace/frappe-bench && bench --site dev.localhost migrate'

# Xem log
docker compose logs -f bench
```

> **Không** chạy `migrate` chỉ vì sửa nhãn UI (P0-A §A4).

---

## 7. Truy cập UI / API và kiểm chứng

### 7.1. UI

```
http://dev.localhost:8080/crm/leads/view/list  (Vite: VITE_PORT trong .env)
http://dev.localhost:8000/cskh                 (Jinja, cần quyền đọc CRM Lead)

Sau seed: staff.a@dev.local / DevSeed-Only-2026! (dữ liệu giả)
```

Nếu `dev.localhost` không resolve trên Windows, thêm bằng quyền admin vào `C:\Windows\System32\drivers\etc\hosts`:

```
127.0.0.1 dev.localhost
```

Khả năng resolve `*.localhost` tùy browser/công cụ. Chỉ xử lý hosts khi thực sự không resolve; với CLI có thể gọi `127.0.0.1` và đặt đúng Host header. Nếu cổng mặc định đã dùng, đặt `WEB_PORT=8010`, `SOCKETIO_PORT=9010`, `VITE_PORT=8091` trong `.env`, recreate bench rồi chạy lại cả hai tiến trình; xem [quy tắc cổng DEV](../../deploy/dev/README.md).

Các màn hình cần kiểm (P0-A §A3): Login, `/crm` (Lead List), Lead Detail, Tasks, Call Logs, Dashboard,
Preferences (đổi ngôn ngữ/timezone — nền tảng cho P0-F).

### 7.2. Kiểm tra HTTP từ máy host

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: dev.localhost' http://127.0.0.1:8000/login
# kỳ vọng: 200; /crm cần đăng nhập trước
```

Vite SPA chuyển Guest tới `/login` rồi quay lại route CRM sau đăng nhập. Guest gọi trang Jinja `/cskh` hoặc API app có auth vẫn bị 403; không dùng `/cskh` làm bootstrap CSRF cho mọi role. Các số đo Guest `/crm` tại §9 là lịch sử của đường web server, không phải flow Vite hiện hành.

### 7.3. Kiểm tra API có xác thực

Tạo API key/secret: UI → User → API Access → Generate Keys. Rồi:

```bash
curl -s -H 'Host: dev.localhost' \
     -H 'Authorization: token <api_key>:<api_secret>' \
     'http://127.0.0.1:8000/api/method/frappe.auth.get_logged_user'
# kỳ vọng: {"message":"<user>"}
```

### 7.4. Xác minh phiên bản đã cài

```bash
docker compose exec bench bash -lc 'cd /workspace/frappe-bench \
  && echo "frappe $(git -C apps/frappe rev-parse HEAD)" \
  && echo "crm    $(git -C apps/crm rev-parse HEAD)" \
  && bench --site dev.localhost list-apps'
```

Kỳ vọng khớp [`version-lock.md`](../architecture/version-lock.md) §2: `frappe` = `d2efa796...`,
`crm` = `0d3910b4...`.

### 7.5. Test runner (`crm_cskh` — WP-02)

Site test **riêng**, không chạy destructive test trên site DEV (P0 §9.2). Trình tự đã kiểm chứng:

```bash
# 1) Site sạch + cài đủ 3 app
docker compose exec bench bash -lc 'cd /workspace/frappe-bench \
  && bench new-site test_cskh.localhost --db-root-password "$DB_ROOT_PASSWORD" --admin-password admin \
     --no-mariadb-socket --mariadb-user-host-login-scope=% \
  && bench --site test_cskh.localhost install-app crm \
  && bench --site test_cskh.localhost install-app crm_cskh'

# 2) Bật test trên site test (bắt buộc, mặc định tắt)
docker compose exec bench bash -lc 'cd /workspace/frappe-bench \
  && bench --site test_cskh.localhost set-config allow_tests true'

# 3) Chạy test
docker compose exec bench bash -lc 'cd /workspace/frappe-bench \
  && bench --site test_cskh.localhost run-tests --app crm_cskh'
```

Test hiện có của `crm_cskh` (WP-02): `crm_cskh/crm_cskh/tests/test_health.py`,
`crm_cskh/crm_cskh/tests/test_poc_custom_field.py`,
`crm_cskh/crm_cskh/crm_cskh/doctype/cskh_app_settings/test_cskh_app_settings.py`.

### 7.6. Frontend hot reload, CSRF và Socket.IO

Bootstrap áp [helper Vite DEV](../../deploy/dev/apply-crm-vite-dev-patch.py) lên CRM SHA đã ghim. Không tạo `crm_cskh/frontend` hoặc dùng service `backend`: frontend nằm ở `/workspace/frappe-bench/apps/crm/frontend`, service Compose là `bench`.

1. Chạy `bench start` và Vite như §6. `VITE_PORT` host phải bằng `VITE_DEV_PORT` trong container; URL browser dùng `dev.localhost` và đúng cổng Vite.
2. Proxy `/socket.io` phải giữ `changeOrigin: false`. Namespace xác thực Host/Origin; handshake 200 hoặc Engine.IO polling 200 chưa chứng minh namespace join.
3. Không bật `ignore_csrf`, không mở wildcard Origin/CORS để sửa lỗi DEV. Helper lấy CSRF sau đăng nhập qua `/desk`, giữ token cho context POST; thiếu/sai token phải bị 400 `CSRFTokenError`, token đúng 200 trên case đã đo.
4. `/cskh` là Jinja, không có Vue `$socket`. SPA gắn socket ở `app.config.globalProperties.$socket`, không phải `window.$socket`.
5. Kiểm chứng namespace bằng công cụ hiện có, chạy trong container từ thư mục frontend (default ports):

```bash
docker compose exec bench bash -lc 'cd /workspace/frappe-bench/apps/crm/frontend && node /src/deploy/dev/verify-socket-namespace.mjs --origin "http://dev.localhost:$VITE_DEV_PORT" --web http://dev.localhost:8000'
```

PASS cần `connected=true`, namespace `/dev.localhost`, transport `websocket` và `pong=true`. `--web` dùng cổng bên trong container 8000, không phải cổng publish host 8010. Helper lấy dependency từ frontend; có thể đặt `CRM_FRONTEND` nếu chạy từ thư mục khác.

Regression bootstrap Guest chạy từ repo root: `node --test deploy/dev/test-crm-vite-bootstrap.cjs`. Tests bootstrap/redaction bổ sung chạy từ `/src` trong bench: `python -m unittest -v deploy.dev.test_bootstrap_contract deploy.dev.test_guarded_crm_payloads`; đây không phải thêm method vào inventory 99.

HMR và checklist 14/14 đã được nghiệm thu ở [evidence G0-02](../../deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md). Chỉ đổi nhãn thử/hoàn tác và ghi evidence mới khi thực sự thay code/frontend hoặc cần tái hiện regression; không reset verdict chỉ vì đọc snapshot cũ.

---

## 8. Reset / backup dữ liệu giả

```bash
# Backup site DEV (DB + files) vào volume, rồi copy ra host
docker compose exec bench bash -lc 'cd /workspace/frappe-bench \
  && bench --site dev.localhost backup --with-files'
docker compose cp bench:/workspace/frappe-bench/sites/dev.localhost/private/backups ./backups

# Restore
docker compose exec bench bash -lc 'cd /workspace/frappe-bench \
  && bench --site dev.localhost restore /path/to/<db>.sql.gz --with-private-files /path/to/<files>.tar'
```

Đây là **dữ liệu giả** — không cần chính sách backup offsite (đó là P4/production).
Reset nhanh nhất khi cần sạch: `docker compose down -v` rồi chạy lại §4–§5.

---

## 9. Snapshot lịch sử WP-01 — 2026-10-15

> Phần này giữ kết quả và các blocker tại thời điểm WP-01. Những trạng thái Vite/SEC/ảnh còn thiếu bên dưới đã được hồ sơ nghiệm thu P0 thay thế; không dùng như checklist hiện hành.

### 9.1. Môi trường tại thời điểm WP-01

| Hạng mục | Kết quả ngày 2026-10-15 |
|---|---|
| WSL2 / Docker Linux Engine | ✅ Docker Engine 29.7.2 trên `desktop-linux`; `docker-desktop` Running với WSL version 2 |
| Docker Compose | ✅ v5.6.0; cấu hình kiểm tra hợp lệ |
| Services | ✅ db healthy; Redis cache/queue và bench đang chạy |
| Recreate container | ✅ `docker compose down` không `-v` + `up -d`; site/apps và 15 Lead giả còn nguyên |
| Runtime image | ✅ bench 5.31.0, Python 3.14.8, Node 24.22.0, Yarn 1.22.22 |
| Revisions | ✅ Frappe `d2efa796…`; CRM `0d3910b…`; `list-apps` khớp version-lock |
| Site, bootstrap, migrate | ✅ `dev.localhost` tạo/cài/migrate; bootstrap chạy lại được; migrate sau seed thành công |
| Seed | ✅ hai nhóm sales giả, 15 Lead, Call Log/Task/File; chạy lại không trùng |
| HTTP/API | ✅ đăng nhập Administrator, `/crm` 200 có session, API trả đúng user; Guest `/crm` 403 |

Chi tiết command/evidence: [`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md) và [`environment-probe.md`](../../deploy/dev/evidence/environment-probe.md) §8.

### 9.2. Các mục từng còn mở ở WP-01

- Bootstrap trên volume bench rỗng, clean test site, repeated migration và backup/restore đã chạy; xem [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md).
- Python source reload đã xác minh trên `bench start`; Vue build pass nhưng Vite route hiển thị trắng nên chưa chứng minh hot reload.
- MariaDB 11.8 phát cảnh báo từ pinned Frappe v15 vì mới hơn 10.8; stack đã qua test P0 nhưng compatibility ngoài các thao tác đó còn cần kiểm tra.
- GitHub Actions trên PR #1 đã pass bootstrap sạch, lint/format, 28 tests, migrate và backup/restore ([run](https://github.com/nobita6986/new-crm/actions/runs/38712593755)); SEC-13/14/27 và ảnh checklist tiếng Việt còn thiếu.

**Docker/WSL2 blockers đã gỡ trên máy hiện tại; Yarn đã có trong bench image nên không cần cài trên host.** Linux DEV VM vẫn là phương án dự phòng nếu engine này hỏng.

---

## 10. Xử lý sự cố thường gặp (Windows/WSL)

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `failed to connect to the docker API ... dockerDesktopLinuxEngine` | Docker Desktop/engine chưa chạy, hoặc WSL2 thiếu | Bật Docker Desktop; `wsl --install` + reboot |
| `wsl --status` → "not installed" | WSL2 chưa cài | PowerShell admin: `wsl --install` |
| `docker compose up` báo không có Linux container | Đang ở Windows container mode | Docker Desktop → Settings → "Use the WSL 2 based engine" |
| `bench init` lỗi `Permission denied` ở `/workspace` | Named volume do root tạo | Chạy bước 4.2 (`chown -R frappe:frappe /workspace`) |
| `bench new-site` lỗi kết nối DB qua socket | Frappe mặc định dùng socket | Dùng `--no-mariadb-socket` (bootstrap đã có) |
| `dev.localhost` không mở được | Windows không resolve `*.localhost` | Thêm dòng vào `hosts` (§7.1) |
| Build frontend lỗi engine | Node quá cũ | `nvm use 22` (cần ≥ 20.19 / ≥ 22.12) |
| `yarn: command not found` trong container bench | Image/toolchain sai hoặc PATH chưa nạp | Kiểm tra `docker compose exec bench yarn --version`; pinned image hiện có Yarn 1.22.22 |

---

## 11. Snapshot lịch sử công việc ngoài WP-01

> Các trạng thái NO-GO/chưa chạy trong mục này là lịch sử, đã được [quyết định nghiệm thu P0](../architecture/p0-exit-report.md) thay thế.

- WP-02 app, fixture và 28 Frappe tests chạy trên clean site; xem [`wp-02-p0b.md`](../../deploy/dev/evidence/wp-02-p0b.md) và [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md).
- WP-03 primitives đã chạy; CSV import và SLA apply follow-up đạt. XLSX, SLA calendar/holiday semantics, dedup/canonical phone thuộc phần còn lại.
- WP-04 PoC `/cskh` cùng site/session đã đạt; Vue SPA dev server còn trắng, hot reload chưa đạt.
- WP-05 security tests chặn các route đã gọi; SEC-13/14/27 và các ô matrix chưa chạy còn mở.
- WP-07 GitHub Actions trên PR #1 đã pass bootstrap sạch, lint/format, test, migrate và backup/restore; [xem run](https://github.com/nobita6986/new-crm/actions/runs/38712593755).
- WP-06 glossary và trang CSKH tiếng Việt có; cần screenshot lưu vào repo và checklist các màn P0.
- P0 hiện **NO-GO cho P1** theo [`p0-exit-report.md`](../architecture/p0-exit-report.md); không dùng DEV với dữ liệu thật.

---

## 12. Bàn giao WP-01 — lịch sử

> Bàn giao P0 hiện hành: 10/10 gate PASS, GO P1; các mục fail-closed và hiệu năng được phân owner trong P0 exit report. Các dòng dưới giữ nguyên bàn giao WP-01 ban đầu.

- **WP-01 runtime: PASS** cho bootstrap, migrate, seed, clean volume, restore và HTTP/API smoke. P0-A tổng thể: **PARTIAL** vì Vue hot reload chưa được xác minh.
- Bằng chứng tổng hợp: [`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md). Raw logs ở `deploy/dev/evidence/*.log` bị loại khỏi Git theo `.gitignore`.
- P0 vẫn **NO-GO cho P1** tới khi G0-02/G0-07/G0-09 cùng các điểm tương thích/field-scope còn mở được đóng theo [`p0-exit-report.md`](../architecture/p0-exit-report.md); CI PR #1 đã xanh.

> Cập nhật 1.4: ghi nhận PR CI đã pass; P0 vẫn NO-GO cho P1 do security transport/full-matrix, Vue hot reload và checklist UI còn mở.
