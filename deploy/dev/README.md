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

## Nguyên tắc bất biến

1. Không commit `.env` hay bất kỳ bí mật thật nào.
2. Không dùng tag trôi (`latest`, `main`, `develop`) — chỉ tag@digest / SHA trong `.env.example`.
3. Không trỏ DEV vào DB/volume production.
4. Không dùng dữ liệu khách hàng thật trong seed.
5. Không sửa source `frappe`/`crm` trong container; dùng custom app `crm_cskh` (WP-02).
