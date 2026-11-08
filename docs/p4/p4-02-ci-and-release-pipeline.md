# Hồ sơ Triển khai & Bàn giao `P4-02`: CI Cô lập PR, Khóa Phụ thuộc Bắc cầu, Gate Bảo mật Strict, Ký Manifest & Quảng bá Cùng Digest (`G02` / `G04` / `G05`)

- **Gói công việc:** `P4-02` (kèm khắc phục toàn diện `F-P4-REL-01A/01B`, `F-P4-REL-02`, `F-P4-REL-03`, `F-P4-REL-04`)
- **Đơn vị thực hiện:** `T1B`
- **Nhánh triển khai:** `codex/p4-02-ci-pipeline` (PR #14 → `codex/p4-01-integration`, baseline ứng dụng `main` `92e14af3532876ad5bc2468bf0e502474cc95a53`)
- **Ranh giới bảo toàn:** **Không chạm** `apps/**`, không chạm `.github/workflows/p0.yml..p3.yml` và giữ nguyên toàn bộ bằng chứng khóa gốc `deploy/images/evidence/*` (`10/10` `SHA256SUMS.txt`).

---

## 1. Danh mục Hiện vật Bàn giao Trong PR `P4-02`

| Hạng mục yêu cầu (`P4.md §4` / `REL-01..04`) | Hiện vật trong Repo | Cơ chế Bảo đảm (`Fail-Closed`) |
| :--- | :--- | :--- |
| **Kiến trúc CI, Registry & Promotion (`P4-ADR-03`)** | [`docs/p4/P4-ADR-03-ci-registry-and-promotion.md`](./P4-ADR-03-ci-registry-and-promotion.md) | Phân tách 3 workflow (`p4-ci.yml`, `p4-release.yml`, `p4-production.yml`) và 4 GitHub Environments (`registry-build`, `registry-verify`, `staging`, `production`). |
| **Khóa phụ thuộc Python bắc cầu & kiểm chứng 3 lần build sạch (`REL-03`)** | [`deploy/images/resources/python-constraints.txt`](../../deploy/images/resources/python-constraints.txt) + [`system-python-constraints.txt`](../../deploy/images/resources/system-python-constraints.txt) + [`Containerfile`](../../deploy/images/Containerfile) | Ghim SHA-256 cho `154` gói PyPI của bench virtualenv và `22` gói Python hệ thống; áp `PIP_CONSTRAINT`, `UV_CONSTRAINT`, `UV_BUILD_CONSTRAINT` và `--no-build-isolation`; CI chạy song song 3 lần `--no-cache` trên 2 VM độc lập và đối chiếu khớp 100% mã băm `pip-freeze.txt`. |
| **Kiểm định POSIX `@sha256` URI dùng chung (`REL-02`)** | [`deploy/release/validate_digest_uri.sh`](../../deploy/release/validate_digest_uri.sh) + [`deploy_staging_by_digest.sh`](../../deploy/release/deploy_staging_by_digest.sh) | Dùng lớp ký tự chuẩn POSIX `[:space:]` (thay cho `[^@\s]` làm rơi ký tự `s` trong `crm-cskh`), kiểm thử tự động cả URI hợp lệ lẫn khoảng trắng, xuống dòng, tag trôi và digest sai. |
| **Khóa mẫu JWT duy nhất, vá gói tương thích & cổng Release `--strict` (`REL-01A/01B`)** | [`deploy/release/evaluate_security_scan.py`](../../deploy/release/evaluate_security_scan.py) + [`cve-exceptions.json`](../../deploy/release/cve-exceptions.json) + [`docs/p4/p4-02-cve-exception-requests.md`](./p4-02-cve-exception-requests.md) | Trích `pyjwt-2.13.0.dist-info/METADATA` từ image `@sha256`, khóa đúng dòng `76` + SHA-256 mẫu doctest (JWT khác trong cùng tệp làm gate `FAIL`); nâng `uv 0.13.7` + `form-data 4.0.5`, loại bỏ `esbuild`/`loader-utils`/`shell-quote` hậu build; giữ `"exceptions": []` và trình riêng từng mục trong `10` CVE `CRITICAL` còn lại để `T0` duyệt. |
| **Đăng nhập `pull-only` cho registry riêng & đồng bộ verify harness (`REL-04`)** | [`.github/workflows/p4-release.yml`](../../.github/workflows/p4-release.yml) + [`deploy/images/compose.verify.yaml`](../../deploy/images/compose.verify.yaml) + [`deploy/compose/scripts/run_isolated_smoke.py`](../../deploy/compose/scripts/run_isolated_smoke.py) | Bổ sung môi trường `registry-verify` (`pull-only`) và cờ `--registry-pull-user` / `--registry-pull-token-file` kèm `docker logout` tức thời; đồng bộ `--no-mariadb-socket` (TCP MariaDB) và `apps-order.txt` (`frappe -> crm -> crm_cskh`); bổ sung bước chờ hội tụ router Traefik `/api/method/ping` hậu `--force-recreate`. |
| **SBOM, ký số Ed25519 `release-manifest.json` & chứng thực cùng digest** | [`deploy/release/generate_sbom.py`](../../deploy/release/generate_sbom.py) + [`release_manifest.py`](../../deploy/release/release_manifest.py) + [`verify_digest_promotion.py`](../../deploy/release/verify_digest_promotion.py) | Xuất CycloneDX 1.5 + SPDX 2.3; chặn sinh manifest `production_candidate` nếu chưa qua `gate_mode='strict'`; chứng thực cùng một `@sha256` xuyên suốt `build -> verify_clean_site (45/45) -> run_isolated_smoke (10-service)`. |
| **Bộ kiểm thử đơn vị cho pipeline phát hành** | [`deploy/release/test_release_pipeline.py`](../../deploy/release/test_release_pipeline.py) | Bao phủ kiểm toán workflow, ranh giới 0-secret PR, bộ kiểm POSIX digest URI (dương + âm), khóa mẫu JWT trong cùng tệp `METADATA`, cổng `--strict`, chữ ký Ed25519 và chống trôi digest. |

---

## 2. Phân tách Tường minh Bằng chứng Loopback OCI Registry Với Hạ tầng Từ Xa (`BLOCKED`)

| Phạm vi kiểm chứng | Trạng thái | Chi tiết bằng chứng / Mã Blocker |
| :--- | :---: | :--- |
| **1. Runner CI cô lập + Ephemeral Loopback OCI v2 Registry (`127.0.0.1:15000`)** | **PASS (Đã kiểm chứng trọn chuỗi)** | Build sạch `--no-cache` ×3 khớp mã băm `pip-freeze.txt`; đẩy `127.0.0.1:15000/cskh/crm-cskh@sha256:<64-hex>`; sinh SBOM CycloneDX/SPDX; xuất cả `security-scan-strict.json` lẫn `security-scan-summary.json`; ký & xác minh Ed25519; xóa cache cục bộ ×2 và kéo lại đúng `@sha256` qua `verify_clean_site.py` (`45/45`) và `run_isolated_smoke.py` (10-service, `zero_digest_drift: true`). |
| **2. Container Registry từ xa được phê duyệt (`p4-release.yml`)** | **BLOCKED (`BLK-INFRA-03`)** | Chờ Chủ sở hữu / Quản trị hạ tầng cấp phát `vars.CSKH_REGISTRY_IMAGE`, `vars.RELEASE_SIGNING_PUBLIC_KEY`, các GitHub Environments `registry-build` / `registry-verify` (tách token `push` và `pull-only`) và phê duyệt các ngoại lệ CVE trong [`p4-02-cve-exception-requests.md`](./p4-02-cve-exception-requests.md). |
| **3. Máy chủ Staging & Production từ xa (`deploy-staging` / `deploy-production`)** | **BLOCKED (`BLK-INFRA-01` / `BLK-INFRA-02`)** | Chờ cấp phát VPS Staging/Production, DNS/TLS và cấu hình thông tin kết nối trong GitHub Environments `staging` / `production` theo [`infra-inventory.md`](./infra-inventory.md). |

---

## 3. Lộ trình Thực thi Tuần tự Các Gói Kế tiếp (`P4-03` → `P4-09`)

Ngay sau khi PR `P4-01` (#13) và PR `P4-02` (#14) được `T0` merge và hạ tầng `BLK-INFRA-01..05` mở khóa, `T1B` thực thi tuần tự trên nhánh riêng:
1. **`P4-03` (`G06`):** Diễn tập migration `N-1`, phân loại Class A–D, kiểm chứng `BLG-P4-MIG-01` (`CSKH Export Job`), chạy `migrate × 2` và phục hồi từ bản sao pre-migrate.
2. **`P4-04` (`G10`–`G12`):** Chạy `S01`–`S20` trên Staging theo digest (PII chéo, đổi role/ownership, thu hồi phiên, API/files/export, rate limit, audit) kèm regression tests và `docs/p4/security-review.md`.
3. **`P4-05` (`G07` / `G08`):** Tự động hóa backup mã hóa offsite (DB, public/private files, `site_config.json`, `encryption_key`), diễn tập restore cô lập, đo RPO/RTO và bàn giao `docs/p4/backup-restore.md`.
4. **`P4-06` (`G18`):** Benchmark trên Staging với quy mô `50.000` Work Item/tháng, đo p50/p95/p99 & headroom VPS, bảo toàn công thức KPI `cskh-metrics-v2` và quyền truy cập.
5. **`P4-09` (`G21`):** Chốt `docs/p4/api-contract.md` và compatibility tests hậu bảo mật/UAT.
