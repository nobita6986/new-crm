# DEV SETUP — Hướng dẫn dựng & vận hành môi trường DEV (WP-01 / P0-A)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/operations/dev-setup.md`
> **Phiên bản:** 1.4 — 15/10/2026
> **Tài liệu cha:** [`../root-plan/P0.md`](../root-plan/P0.md) (§3 P0-A) · [`../architecture/version-lock.md`](../architecture/version-lock.md)
> **Đối tượng:** người thứ hai cần setup lại từ repo mà không cần hướng dẫn miệng.

> **TRẠNG THÁI:** WP-01 runtime có bootstrap trên volume rỗng, migrate, seed, UI/API và backup/restore; WP-02/P0 suite chạy trên test site sạch.
> **P0-A còn PARTIAL:** Python reload đã xác minh; Vite page hiện trắng nên chưa chứng minh Vue hot reload. MariaDB 11.8 có cảnh báo Framework >10.8 chưa được kiểm thử; xem [`p0-exit-report.md`](../architecture/p0-exit-report.md).

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

- App cài trên site: `frappe` (Framework), `crm` (Frappe CRM). `crm_cskh` thêm ở WP-02.
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
7. `install-app crm`; nếu `INSTALL_CSKH=1`: symlink `/src/apps/crm_cskh` → `apps/crm_cskh` rồi `install-app crm_cskh`.
8. `bench migrate`.
9. In ra SHA thực tế của `frappe`/`crm` + `list-apps` để đối chiếu version-lock.

**Kỳ vọng sau bước này**: `bench --site dev.localhost list-apps` in ra `frappe` và `crm`.

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
http://dev.localhost:8000/crm      (đăng nhập: Administrator / mật khẩu trong .env)
```

Nếu `dev.localhost` không resolve trên Windows, thêm bằng quyền admin vào `C:\Windows\System32\drivers\etc\hosts`:

```
127.0.0.1 dev.localhost
```

Trên máy DEV hiện tại, `dev.localhost` chưa resolve ở host; thao tác hosts-file cần quyền admin.

Các màn hình cần kiểm (P0-A §A3): Login, `/crm` (Lead List), Lead Detail, Tasks, Call Logs, Dashboard,
Preferences (đổi ngôn ngữ/timezone — nền tảng cho P0-F).

### 7.2. Kiểm tra HTTP từ máy host

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: dev.localhost' http://127.0.0.1:8000/login
# kỳ vọng: 200; /crm cần đăng nhập trước
```

Guest gọi `/crm` nhận HTTP 403. Sau khi đăng nhập, `/crm` trả HTTP 200; API login và `frappe.auth.get_logged_user` được kiểm tra ở runtime evidence §9.

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

### 7.6. Frontend hot reload (Vue)

```bash
docker compose exec bench bash -lc 'cd /workspace/frappe-bench/apps/crm/frontend \
  && node --version && yarn --version && yarn install --check-files && yarn dev --host 0.0.0.0'
```

`VITE_PORT` mặc định bind vào `127.0.0.1:8080`; mở `http://dev.localhost:8080/crm`. README upstream CRM lưu ý Vite proxy có thể cần `ignore_csrf=1`; dự án **không** bật chế độ bỏ CSRF. Luồng Vue qua Vite vì vậy chưa được nghiệm thu cho tới khi xác minh được proxy giữ nguyên CSRF. Image bench đang dùng có Node 24.22.0 và Yarn 1.22.22; không cần cài Yarn trên host.

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

## 9. TRẠNG THÁI CHẠY THẬT & BLOCKER

### 9.1. Môi trường hiện tại

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

### 9.2. Còn mở trước khi P0-A hoàn tất

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

## 11. Còn lại ngoài WP-01

- WP-02 app, fixture và 28 Frappe tests chạy trên clean site; xem [`wp-02-p0b.md`](../../deploy/dev/evidence/wp-02-p0b.md) và [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md).
- WP-03 primitives đã chạy; CSV import và SLA apply follow-up đạt. XLSX, SLA calendar/holiday semantics, dedup/canonical phone thuộc phần còn lại.
- WP-04 PoC `/cskh` cùng site/session đã đạt; Vue SPA dev server còn trắng, hot reload chưa đạt.
- WP-05 security tests chặn các route đã gọi; SEC-13/14/27 và các ô matrix chưa chạy còn mở.
- WP-07 GitHub Actions trên PR #1 đã pass bootstrap sạch, lint/format, test, migrate và backup/restore; [xem run](https://github.com/nobita6986/new-crm/actions/runs/38712593755).
- WP-06 glossary và trang CSKH tiếng Việt có; cần screenshot lưu vào repo và checklist các màn P0.
- P0 hiện **NO-GO cho P1** theo [`p0-exit-report.md`](../architecture/p0-exit-report.md); không dùng DEV với dữ liệu thật.

---

## 12. Bàn giao

- **WP-01 runtime: PASS** cho bootstrap, migrate, seed, clean volume, restore và HTTP/API smoke. P0-A tổng thể: **PARTIAL** vì Vue hot reload chưa được xác minh.
- Bằng chứng tổng hợp: [`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md). Raw logs ở `deploy/dev/evidence/*.log` bị loại khỏi Git theo `.gitignore`.
- P0 vẫn **NO-GO cho P1** tới khi G0-02/G0-07/G0-09 cùng các điểm tương thích/field-scope còn mở được đóng theo [`p0-exit-report.md`](../architecture/p0-exit-report.md); CI PR #1 đã xanh.

> Cập nhật 1.4: ghi nhận PR CI đã pass; P0 vẫn NO-GO cho P1 do security transport/full-matrix, Vue hot reload và checklist UI còn mở.
