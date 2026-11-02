# deploy/dev — Môi trường DEV cục bộ (WP-01 / P0-A)

Cấu hình DEV cho Frappe CRM + Frappe Framework + app `crm_cskh` chứa permission/API guards.
Mọi phiên bản được **ghim tag + full SHA / tag@digest** — xem [`../../docs/architecture/version-lock.md`](../../docs/architecture/version-lock.md).

> ⚠️ **Chỉ dùng cho DEV với DỮ LIỆU GIẢ.** Không dùng cho staging/production.
> **P0 đã nghiệm thu GO P1:** DEV/Vite/CSRF/Socket.IO, bộ ảnh tiếng Việt và CI clean bootstrap/migration/restore đã đạt trong phạm vi baseline DEV. Xem [quyết định nghiệm thu](../../docs/architecture/p0-exit-report.md); `wp01-runtime.md` và các snapshot đầu kỳ là lịch sử.
> **`INSTALL_CSKH=1` bắt buộc.** `.env` cũ có giá trị `0` sẽ bị bootstrap từ chối trước khi gọi Bench/DB; không chạy DEV thiếu app chứa các guard.

## Nội dung

| File | Vai trò |
|---|---|
| `docker-compose.yml` | Stack DEV: `db` (MariaDB), `redis-cache`, `redis-queue`, `bench`. Image ghim `tag@digest`; chỉ web/realtime bind `127.0.0.1`; DB/Redis không publish |
| `.env.example` | Mẫu biến môi trường (mật khẩu là mặt định DEV, **không phải bí mật**). Sao chép thành `.env` |
| `.gitignore` | Chặn commit `.env`, dumps, logs |
| `bootstrap.sh` | Khởi tạo bench + lấy app theo SHA ghim + tạo site + install + migrate. **Idempotent** |
| `seed/seed_demo.py` | Seed dữ liệu giả (user/team/lead/call/task/note/file). **Idempotent, deterministic** |
| `seed/run-seed.sh` | Wrapper chạy seed trong container `bench` |
| `evidence/` | Hồ sơ nghiệm thu, log kiểm chứng và manifest ảnh P0 |

## Quickstart (sau khi đã có Docker Linux container — xem điều kiện ở dev-setup §2)

```bash
cd deploy/dev
cp .env.example .env

# 1) Lần đầu: sửa quyền cho named volume
docker compose up -d db redis-cache redis-queue bench
docker compose exec -u root bench bash -lc 'mkdir -p /workspace && chown -R frappe:frappe /workspace'

# 2) Bootstrap (bench init + get-app ghim SHA + new-site + install + migrate)
docker compose exec bench bash /src/deploy/dev/bootstrap.sh

# 3) Seed dữ liệu giả
docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh

# 4) Chạy server DEV
docker compose exec bench bash -lc 'cd /workspace/frappe-bench && bench start'
# 5) Terminal thứ hai, cùng thư mục deploy/dev: chạy Vite
docker compose exec bench bash -lc 'cd /workspace/frappe-bench/apps/crm/frontend && node node_modules/.bin/vite --host 0.0.0.0 --port "$VITE_DEV_PORT"'
# UI DEV: http://dev.localhost:8080/crm/leads/view/list (VITE_PORT trong .env)
# Sau seed: staff.a@dev.local / DevSeed-Only-2026! (dữ liệu giả)
```

Các lệnh trên dùng Bash/WSL hoặc terminal tương đương. `.env.example` mặc định cài đủ `frappe`, `crm`, `crm_cskh`; kiểm tra bằng `bench --site dev.localhost list-apps --format json` trong bench. Bootstrap áp các helper Vite/i18n có ghim revision; không sửa tay upstream để thay cho helper.

## Cổng DEV — quy tắc nhất quán host ↔ container

`VITE_PORT` (host) **phải bằng** cổng Vite chạy trong container (`VITE_DEV_PORT`):

```bash
# .env
WEB_PORT=8000
SOCKETIO_PORT=9000
VITE_PORT=8080        # host
# -> compose truyền VITE_DEV_PORT=8080 cho container, publish 127.0.0.1:8080:8080
```

Lý do: realtime tự xác thực bằng request tới `Origin + /api/method/frappe.realtime.get_user_info`
(`apps/frappe/realtime/utils.js`). `Origin` là cổng trình duyệt dùng, nên cổng đó phải truy cập được
**bên trong container**. Nếu `VITE_PORT` (host) ≠ cổng Vite trong container, namespace Socket.IO
fail với `ECONNREFUSED` dù Engine.IO handshake vẫn trả HTTP 200.

Nếu phải đổi cổng (ví dụ khi cổng mặc định đã bị chiếm), dùng **cùng một số** cho host và container
và chạy Vite với `VITE_DEV_PORT` tương ứng:

```bash
# ví dụ dùng 8091
WEB_PORT=8010 SOCKETIO_PORT=9010 VITE_PORT=8091 docker compose up -d --force-recreate bench
docker compose exec bench bash -lc 'cd /workspace/frappe-bench/apps/crm/frontend && VITE_DEV_PORT=8091 node node_modules/.bin/vite --host 0.0.0.0 --port 8091'
```

Sau khi recreate container, chạy lại `bench start` ở terminal riêng. Để giữ cấu hình qua những lần `up`, lưu cả ba cổng vào `.env`; ví dụ trên chỉ đặt biến cho một lệnh Bash. URL tương ứng là `http://dev.localhost:8091/crm/leads/view/list`.

> `frappe-ui` tự tính cổng Vite từ `webserver_port` và ghi đè cờ `--port`; helper
> [`apply-crm-vite-dev-patch.py`](apply-crm-vite-dev-patch.py) đã chèn
> `port: Number(process.env.VITE_DEV_PORT || 8080)` vào khối `frappeProxy` để tránh điều đó.
> Proxy `/socket.io` phải giữ `changeOrigin: false` — xem
> [`evidence/t1a-g002-vite-csrf-blocker.md`](evidence/t1a-g002-vite-csrf-blocker.md) §12.

## Lệnh thường dùng

```bash
docker compose ps                         # trạng thái service
docker compose logs -f bench              # log container bench
docker compose exec bench bash            # shell trong bench
docker compose stop                       # dừng (giữ dữ liệu)
docker compose down                       # xoá container (giữ volume)
docker compose down -v                    # XOÁ CẢ DỮ LIỆU DEV (reset hoàn toàn)
```

Chi tiết đầy đủ (start/stop/migrate/reset/backup, kiểm chứng UI/API, xử lý sự cố Windows/WSL):
[`../../docs/operations/dev-setup.md`](../../docs/operations/dev-setup.md).

## Phiên Chrome mới và lỗi màn hình trắng

Vite DEV lấy CSRF từ trang Desk cùng site, không phụ thuộc quyền đọc trang
`/cskh`. Khi chưa đăng nhập, app chuyển tới `/login` và giữ đường dẫn CRM
để quay lại sau đăng nhập. Lỗi khởi tạo khác sẽ hiện thông báo và liên kết
đăng nhập lại thay vì để màn hình trắng. Sau khi cập nhật helper, chạy lại
`apply-crm-vite-dev-patch.py` rồi tải lại trang Chrome. Cảnh báo HTTP localhost
và log Workbox không thay thế lỗi bootstrap thực tế trong Console.

Từ repo root: `node --test deploy/dev/test-crm-vite-bootstrap.cjs`.
Các kiểm tra bootstrap fail-closed và redaction chạy trong bench bằng `python -m unittest -v deploy.dev.test_bootstrap_contract deploy.dev.test_guarded_crm_payloads` từ `/src`; CI thực thi chúng ngoài inventory 99 method.

## Nguyên tắc bất biến

1. Không commit `.env` hay bất kỳ bí mật thật nào.
2. Không dùng tag trôi (`latest`, `main`, `develop`) — chỉ tag@digest / SHA trong `.env.example`.
3. Không trỏ DEV vào DB/volume production.
4. Không dùng dữ liệu khách hàng thật trong seed.
5. Logic nghiệp vụ/quyền nằm trong `crm_cskh`. Chỉ dùng helper DEV đã ghim revision cho những patch Vite/i18n được quản lý trong repo; không sửa tay hoặc mở rộng patch upstream tùy ý.
