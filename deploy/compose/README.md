# Cấu hình Docker Compose & Môi trường cho Staging / Production / Smoke (`P4-01`)

Thư mục này chứa lớp triển khai container chuẩn của **CRM-CSKH Giai đoạn P4 (`P4-01`)**, tái sử dụng nguyên bản [`frappe/frappe_docker@v4.0.1`](https://github.com/frappe/frappe_docker/tree/2b26e9ab8d751e5ea33bb7b930aea3550bb81ded) (`2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`) kết hợp các override khóa digest, tách mạng 3 lớp, tách volume/site/secrets và kiểm soát cổng ingress.

> **Cảnh báo an toàn:**
> - **Không commit** bất kỳ tệp `.env` thật, khóa mã hóa `encryption_key`, mật khẩu DB/Admin hay chứng chỉ TLS vào Git.
> - **Không đụng chạm** các stack DEV của Owner (`deploy/dev`, `deploy/p3` — `dev.localhost`, `p3ui.localhost:8320`).
> - Mọi thao tác migrate schema ở Staging/Production bắt buộc đi qua quy trình backup + preflight (`MIGRATE_SITES=false` ở chế độ chạy thường trực).

---

## 1. Cấu trúc Thư mục

- `upstream/frappe_docker/` — Bản sao nguyên vẹn byte-for-byte từ `frappe/frappe_docker@v4.0.1` (`2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`), kiểm chứng bằng `UPSTREAM-PROVENANCE.json`.
- `overrides/compose.cskh-common.yaml` — Khóa `APP_IMAGE@sha256`, `MARIADB_IMAGE@sha256`, `REDIS_IMAGE@sha256`, mạng 3 lớp (`edge_net`, `app_net`, `data_net`), named volumes/secrets theo `COMPOSE_PROJECT_NAME`, healthcheck và giới hạn tài nguyên.
- `overrides/compose.cskh-tls.yaml` — Ingress TLS (Traefik `v3.7@sha256`) cho Staging & Production; chỉ mở cổng ingress `80` (redirect/ACME) và `443` (HTTPS) trên `INGRESS_BIND_IP`.
- `overrides/compose.cskh-isolated-smoke.yaml` — Ingress cô lập loopback (`127.0.0.1:18080:80`) dùng kiểm thử smoke bằng dữ liệu giả mà không cần DNS công cộng hay Let's Encrypt.
- `env/{production,staging,isolated-smoke}.env.example` — Mẫu biến môi trường chỉ chứa giá trị mẫu/placeholder.
- `scripts/validate_compose_config.py` — Trình kiểm định tĩnh ngoại tuyến (kiểm tra provenance, `config --services`, mạng, cổng, volume, secret, negative gates).
- `scripts/run_isolated_smoke.py` — Trình chạy smoke cô lập (`config --services`, healthcheck, kiểm cổng, đăng nhập, UI tiếng Việt, CSRF, Socket.IO) ngay khi `P4-00` được T0 chốt và `T1B` bàn giao `APP_IMAGE@sha256:...`.

---

## 2. Lệnh Kiểm tra Tĩnh (`config --services` & Kiểm toán Cấu hình)

Chạy kiểm định tự động cả 3 môi trường (`production`, `staging`, `isolated-smoke`):

```bash
python deploy/compose/scripts/validate_compose_config.py
```

Lệnh kết xuất danh sách service chuẩn cho **Production / Staging**:

```bash
docker compose \
  --env-file /etc/crm-cskh/prod/production.env \
  -f deploy/compose/upstream/frappe_docker/compose.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb-secrets.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.redis.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.https.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.migrator.yaml \
  -f deploy/compose/overrides/compose.cskh-common.yaml \
  -f deploy/compose/overrides/compose.cskh-tls.yaml \
  config --services
```

Lệnh kết xuất danh sách service cho **Isolated Smoke (`crm-cskh-p4-smoke`)**:

```bash
docker compose \
  --env-file deploy/compose/env/isolated-smoke.env.example \
  -f deploy/compose/upstream/frappe_docker/compose.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.mariadb-secrets.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.redis.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.proxy.yaml \
  -f deploy/compose/upstream/frappe_docker/overrides/compose.migrator.yaml \
  -f deploy/compose/overrides/compose.cskh-common.yaml \
  -f deploy/compose/overrides/compose.cskh-isolated-smoke.yaml \
  config --services
```

---

## 3. Lệnh Chạy Smoke Cô lập Khi Nhận Image từ `T1B` (`APP_IMAGE`)

```bash
# 1. Kiểm tra preflight & danh sách blocker hiện tại:
python deploy/compose/scripts/run_isolated_smoke.py --preflight-only

# 2. Chạy full smoke trên site giả lập p4-smoke.localhost (cổng loopback 127.0.0.1:18080)
#    Chấp nhận cả <repo>@sha256:<64-hex> lẫn local image ID sha256:<64-hex> do T1B bàn giao:
python deploy/compose/scripts/run_isolated_smoke.py \
  --app-image <REPO_AT_SHA256_OR_LOCAL_SHA256_ID_FROM_T1B> \
  --report-out /tmp/p4-01-isolated-smoke-report.json
```

Xem chi tiết kiến trúc, hợp đồng biến nhận image với `T1B`, danh mục PII cấp trường và danh mục blocker hạ tầng tại [`docs/p4/P4-ADR-01-compose-and-isolation.md`](../../docs/p4/P4-ADR-01-compose-and-isolation.md), [`docs/p4/pii-field-catalog.md`](../../docs/p4/pii-field-catalog.md) và [`docs/p4/p4-01-compose-and-blockers.md`](../../docs/p4/p4-01-compose-and-blockers.md).
