# Môi Trường Staging / DR HTTPS Cô Lập Cho UAT, Kiểm Quyền, Khởi Động Lại & Quay Lui (`P4` — Cổng `G09`–`G17`)

- **Mã tài liệu:** `P4-ISOLATED-STAGING-DR-HTTPS` (`docs/p4/isolated-staging-dr-https.md`)
- **Thành phần cấu hình & điều phối:**
  - Compose override cô lập: [`deploy/compose/overrides/compose.cskh-dr-https.yaml`](../../deploy/compose/overrides/compose.cskh-dr-https.yaml)
  - Mẫu cấu hình môi trường: [`deploy/compose/env/staging-dr-https.env.example`](../../deploy/compose/env/staging-dr-https.env.example)
  - Trình điều phối & diễn tập tự động: [`deploy/ops/manage_isolated_staging_dr.py`](../../deploy/ops/manage_isolated_staging_dr.py)
  - Smoke HTTPS & đối soát digest: [`deploy/ops/staging_https_smoke_and_reconcile.py`](../../deploy/ops/staging_https_smoke_and_reconcile.py) (`--mode dr-https`)
  - Giám sát container, cron & bất biến: [`deploy/ops/check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py) (`--mode dr-https`)

> [!IMPORTANT]
> **Nguyên tắc bảo vệ tuyệt đối Site Live của Owner (`https://crm.hrpartner.vn`):**
> Tuyệt đối **không** dùng project `crm-cskh-vps-staging` hoặc site `crm-staging.localhost` để tạo fixture tổng hợp, đổi khóa mã hóa, thử tắt dịch vụ, thử `restart/retry` hay thử `bench --force restore`. Toàn bộ nghiệm thu vai trò có dữ liệu mẫu và diễn tập phá hủy phải chạy trên stack `dr-https` cô lập hoàn toàn này.

---

## 1. Kiến Trúc Cô Lập & Bảo Mật TLS / Cookie / WebSocket

| Thuộc tính | Stack Live của Owner (`crm.hrpartner.vn`) | Stack Staging / DR HTTPS Cô Lập (`dr-https`) |
| :--- | :--- | :--- |
| **`COMPOSE_PROJECT_NAME`** | `crm-cskh-vps-staging` | `crm-cskh-staging-dr-https` *(hoặc `crm-cskh-dr-https-<run_id>`)* |
| **`SITE_NAME`** | `crm-staging.localhost` | `staging-dr-uat.crm-internal.localhost` |
| **Mạng & Volume Docker** | Riêng của `crm-cskh-vps-staging` | Riêng 100%: `*_edge_net`, `*_app_net` (`internal`), `*_data_net` (`internal`), `*_sites`, `*_logs`, `*_db-data`, `*_redis-queue-data`, `*_cert_data` |
| **Cổng Host xuất bản** | `80/tcp`, `443/tcp` công khai | Chỉ `127.0.0.1:18081 -> 80` (HTTP redirect `:18443`) và `127.0.0.1:18443 -> 443` (HTTPS/WSS) |
| **Chứng chỉ TLS (`X.509`)** | Let's Encrypt công khai | Root CA tạm thời 2048-bit (`ca.key` bị xóa ngay sau khi ký `server.crt` có SAN `DNS:<SITE_NAME>,IP:127.0.0.1`), nạp vào `/letsencrypt/dynamic` trong volume `*_cert_data` |
| **Chính sách Cookie & HSTS** | `Secure; HttpOnly; SameSite=Lax`, `HSTS` | `FORWARDED_ALLOW_IPS=*` trên `backend` nội bộ giúp Gunicorn/Frappe gắn đúng `Secure; HttpOnly; SameSite=Lax` trên `sid`; Nginx gắn sẵn `Strict-Transport-Security` |
| **Chặn gửi thư ra ngoài** | Cấu hình theo vận hành thật | Khóa cứng `mute_emails = 1` trong `site_config.json`, chỉ dùng tài khoản `@example.test` / `@example.invalid` |

Hàm `assert_isolated_from_owner_stack()` trong [`manage_isolated_staging_dr.py`](../../deploy/ops/manage_isolated_staging_dr.py) và [`seed_staging_synthetic.py`](../../deploy/ops/seed_staging_synthetic.py) chặn cứng từ trước khi gọi Docker nếu phát hiện tên project/site/đường dẫn trùng với `crm-cskh-vps-staging`, `crm-staging.localhost`, `crm.hrpartner.vn`, `/opt/crm-cskh-staging`, wildcard `0.0.0.0` hoặc các cổng `80`/`443`/`8080`/`8443`/`3306`/`6379`.

---

## 2. Chế Độ 1 — Diễn Tập Tự Động Toàn Phần & Dọn Sạch Không Dư Lượng (`full-rehearsal`)

Thực thi trọn gói trong một lệnh duy nhất: dựng stack HTTPS cô lập → kiểm tra HTTP redirect, `TLSv1.3`, SAN, cookie `sid` (`Secure; HttpOnly; SameSite=Lax`), tiêu đề tiếng Việt, CSRF và `wss://` (`101 Switching Protocols`) → kiểm tra 15 màn hình `/cskh/*` + ma trận phân quyền 6 vai trò + thu hồi phiên tức thì khi `User.enabled = 0` → diễn tập `restart` `queue-short`/`queue-long`/`scheduler` + chạy lặp 4 cron ×2 idempotent → diễn tập quay lui Cấp độ 1, Cấp độ 2 và khôi phục Cấp độ 4 → `docker compose down --volumes --remove-orphans` + đối chứng `0` dư lượng container/volume/network:

```bash
python3 deploy/ops/manage_isolated_staging_dr.py full-rehearsal \
  --app-image sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9 \
  --out /var/lib/crm-cskh/dr-rehearsal/dr-https-full-rehearsal.json
```

---

## 3. Chế Độ 2 — Dựng Môi Trường Bền Vững Phục Vụ Phiên UAT Tương Tác Qua Trình Duyệt

### Bước 1 — Khởi tạo stack Staging/DR HTTPS cô lập
```bash
python3 deploy/ops/manage_isolated_staging_dr.py up \
  --state-dir /var/lib/crm-cskh/staging-dr-state \
  --app-image sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9 \
  --project-name crm-cskh-staging-dr-https \
  --site-name staging-dr-uat.crm-internal.localhost \
  --bind-ip 127.0.0.1 \
  --http-port 18081 \
  --https-port 18443
```
Sau khi `READY`, thư mục `/var/lib/crm-cskh/staging-dr-state` lưu:
- `staging-dr-https.env` (`0600`) và `db_root_password.txt` (`0600`, LF-only)
- `ca.crt` (`0644` — chứng chỉ công khai để trình duyệt hoặc `curl --cacert` tin cậy; **khóa riêng `ca.key` đã bị hủy ngay khi ký xong**)
- `dr-credentials.local.json` (`0600` — thông tin tài khoản synthetic `BOD`, `Admin`, `Leader A/B`, `Staff A/B/C/D`, `Outside`)

### Bước 2 — Mở kết nối an toàn cho Người kiểm thử UAT (qua SSH Port-Forward, không mở cổng công khai)
Trên máy trạm của người kiểm thử:
```bash
ssh -L 18443:127.0.0.1:18443 <operator>@<host-dr>
# Mở Chrome/Edge với host resolver cục bộ (không cần sửa /etc/hosts toàn máy):
# --host-resolver-rules="MAP staging-dr-uat.crm-internal.localhost 127.0.0.1"
# Truy cập: https://staging-dr-uat.crm-internal.localhost:18443/cskh/dashboard
```

### Bước 3 — Chạy từng bài kiểm định nghiệp vụ & vận hành trên stack đang mở
```bash
# 1. Kiểm tra 15 màn hình tiếng Việt + RBAC 6 vai trò + khóa tài khoản tức thì qua HTTPS:
python3 deploy/ops/manage_isolated_staging_dr.py verify-uat-and-permissions \
  --state-dir /var/lib/crm-cskh/staging-dr-state \
  --out /var/lib/crm-cskh/staging-dr-state/uat-permissions-report.json

# 2. Diễn tập khởi động lại worker/scheduler và chạy lặp 4 cron ×2 idempotent (G17):
python3 deploy/ops/manage_isolated_staging_dr.py rehearse-restart-and-retry \
  --state-dir /var/lib/crm-cskh/staging-dr-state \
  --out /var/lib/crm-cskh/staging-dr-state/restart-retry-report.json

# 3. Diễn tập quay lui Cấp độ 1 (Kill Switch), Cấp độ 2 (Digest) & Cấp độ 4 (Backup/Delta/Restore):
python3 deploy/ops/manage_isolated_staging_dr.py rehearse-rollback \
  --state-dir /var/lib/crm-cskh/staging-dr-state \
  --out /var/lib/crm-cskh/staging-dr-state/rollback-report.json
```

### Bước 4 — Thu hồi sạch môi trường sau khi kết thúc đợt UAT / Diễn tập
```bash
python3 deploy/ops/manage_isolated_staging_dr.py down \
  --state-dir /var/lib/crm-cskh/staging-dr-state \
  --remove-state-dir
```
