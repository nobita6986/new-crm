# deploy/dev — Môi trường DEV cục bộ (WP-01 / P0-A)

Cấu hình DEV cho Frappe CRM + Frappe Framework + (sau này) app `crm_cskh`.
Mọi phiên bản được **ghim tag + full SHA / tag@digest** — xem [`../../docs/architecture/version-lock.md`](../../docs/architecture/version-lock.md).

> ⚠️ **Chỉ dùng cho DEV với DỮ LIỆU GIẢ.** Không dùng cho staging/production.
> ✅ **WP-01 đã chạy trên máy DEV hiện tại:** Docker/WSL2, bootstrap, migrate, seed và HTTP/API smoke thành công.
> **P0-A chưa hoàn tất:** hot reload/source-edit, clean-machine setup/restore và MariaDB compatibility còn mở. Xem evidence ở
> [`../../docs/operations/dev-setup.md`](../../docs/operations/dev-setup.md) §9 và
> [`evidence/wp01-runtime.md`](evidence/wp01-runtime.md).

## Nội dung

| File | Vai trò |
|---|---|
| `docker-compose.yml` | Stack DEV: `db` (MariaDB), `redis-cache`, `redis-queue`, `bench`. Image ghim `tag@digest`; chỉ web/realtime bind `127.0.0.1`; DB/Redis không publish |
| `.env.example` | Mẫu biến môi trường (mật khẩu là mặt định DEV, **không phải bí mật**). Sao chép thành `.env` |
| `.gitignore` | Chặn commit `.env`, dumps, logs |
| `bootstrap.sh` | Khởi tạo bench + lấy app theo SHA ghim + tạo site + install + migrate. **Idempotent** |
| `seed/seed_demo.py` | Seed dữ liệu giả (user/team/lead/call/task/note/file). **Idempotent, deterministic** |
| `seed/run-seed.sh` | Wrapper chạy seed trong container `bench` |
| `evidence/` | Bằng chứng dò môi trường và (sau này) log chạy thật |

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
# UI: http://dev.localhost:8000/crm   (đăng nhập Administrator / mật khẩu trong .env)
```

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

Kiểm tra hồi quy riêng cho luồng này: `node --test deploy/dev/test-crm-vite-bootstrap.cjs`.

## Nguyên tắc bất biến

1. Không commit `.env` hay bất kỳ bí mật thật nào.
2. Không dùng tag trôi (`latest`, `main`, `develop`) — chỉ tag@digest / SHA trong `.env.example`.
3. Không trỏ DEV vào DB/volume production.
4. Không dùng dữ liệu khách hàng thật trong seed.
5. Không sửa source `frappe`/`crm` trong container; dùng custom app `crm_cskh` (WP-02).
