# P4-00 — Kiểm kê hạ tầng và nguồn tái sử dụng

**Phạm vi:** 05/11/2026, baseline `92e14af3532876ad5bc2468bf0e502474cc95a53`. Kiểm kê bằng cấu hình repo, Docker host và source upstream đã ghim.

## DEV đã đối chiếu

- `docker compose --env-file deploy/p3/.env.example -f deploy/p3/docker-compose.yml config --services` liệt kê `bench`, `db`, `redis-cache`, `redis-queue`. Web, Socket.IO và Vite bind loopback trong Compose. Docker host `desktop-linux`, Docker 29.9.2, Compose v5.6.1. Lúc rà soát, các container Owner của project `crm-cskh-p3-ui` đang dừng. [Compose DEV P3](../../deploy/p3/docker-compose.yml)
- Bản DEV dùng Bench, MariaDB và Redis ghim theo lock. Backup/restore DEV đã có evidence; quyền đọc backup ngoài VPS, phục hồi file và cấu hình ở đích Staging cần diễn tập theo P4-05. [Version lock](../architecture/version-lock.md) · [Smoke restore P2](../../deploy/dev/evidence/p2-runtime-20261103/restore/p2-backup-restore-smoke-20261103.json)

## Nguồn production để khảo sát P4-01

- **Compose.** `compose.yaml` tại SHA `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` khai báo `configurator`, `backend`, `frontend`, `websocket`, `queue-short`, `queue-long`, `scheduler`. Repository có override MariaDB secrets, Redis và HTTPS. Xác nhận tên service thực tế của cấu hình ghép trước khi viết lệnh vận hành. [Compose upstream đã ghim](https://github.com/frappe/frappe_docker/blob/2b26e9ab8d751e5ea33bb7b930aea3550bb81ded/compose.yaml) · [Overrides](https://github.com/frappe/frappe_docker/tree/2b26e9ab8d751e5ea33bb7b930aea3550bb81ded/overrides)
- **Image và release.** Tái sử dụng đường build custom/layered image; build đủ Frappe, CRM, `crm_cskh` và frontend assets, kiểm qua digest của cùng một artifact ở CI và Staging. [Containerfile upstream](https://github.com/frappe/frappe_docker/tree/2b26e9ab8d751e5ea33bb7b930aea3550bb81ded/images)
- **Cơ chế có sẵn.** Dùng quyền và session Frappe, scheduler/RQ và `bench backup --with-files`/`bench restore`. Bổ sung cấu hình mạng, lưu secret, mã hóa/offsite, quan sát hàng đợi và các kiểm thử ở ranh giới quyền. [Kế hoạch tái sử dụng P4](../root-plan/P4.md)

## Các đầu vào chờ bằng chứng

- VPS/OS và quyền quản trị; domain/DNS/TLS và proxy được chọn; dung lượng và topology Staging.
- Registry và quyền push/pull image, nơi lưu/luân chuyển secret, kho backup mã hóa ngoài VPS và quyền restore. Chỉ ghi định danh đích và đầu mối quản trị trong hồ sơ.
- Chủ trách nhiệm triển khai, UAT, rollback và vận hành; ngưỡng RPO/RTO và chính sách lưu giữ được phê duyệt.
- Đường dẫn kiểm chứng site Staging, schema vật lý, load test và restore drill. Trạng thái Production readiness chờ bằng chứng thực tế cho các hạng mục trên.
