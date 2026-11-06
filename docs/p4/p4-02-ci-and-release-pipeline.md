# Hồ sơ Triển khai & Bàn giao `P4-02`: CI Cô lập PR, Build-Once, SBOM, Ký Manifest & Quảng bá Cùng Digest (`G02` / `G04` / `G05`)

- **Gói công việc:** `P4-02` (kèm trạng thái sẵn sàng & lộ trình kỹ thuật cho `P4-03`, `P4-04`, `P4-05`, `P4-06`, `P4-09`)
- **Đơn vị thực hiện:** `T1B`
- **Nhánh triển khai:** `codex/p4-02-ci-pipeline` (tách từ nhánh tích hợp `codex/p4-01-integration` tại `492423c43a3ddb15e8f5fea23124c4bc70a6dbb8`, ứng dụng baseline `main` `92e14af3532876ad5bc2468bf0e502474cc95a53`)
- **Phạm vi thay đổi:** Chỉ thêm mới `.github/workflows/p4-*.yml`, `deploy/release/*` và `docs/p4/*`; **không chạm** `apps/**`, `deploy/images/**`, `deploy/compose/**` hay các workflow hiện hữu `p0.yml`..`p3.yml`.

---

## 1. Danh mục Hiện vật Bàn giao Trong PR `P4-02`

| Hạng mục yêu cầu (`P4.md §4` / `G02` / `G04`) | Hiện vật trong Repo | Cơ chế Bảo đảm (`Fail-Closed`) |
| :--- | :--- | :--- |
| **Kiến trúc CI, Registry & Promotion (`P4-ADR-03`)** | [`docs/p4/P4-ADR-03-ci-registry-and-promotion.md`](./P4-ADR-03-ci-registry-and-promotion.md) | Phân tách 3 workflow theo đặc quyền tối thiểu (`p4-ci.yml`, `p4-release.yml`, `p4-production.yml`) và khóa phạm vi chữ ký `environment_scope`. |
| **Kiểm tra PR trên runner cô lập, 0 secret phát hành** | [`.github/workflows/p4-ci.yml`](../../.github/workflows/p4-ci.yml) + [`deploy/release/verify_pr_isolation.py`](../../deploy/release/verify_pr_isolation.py) | `permissions: contents: read`, cấm `pull_request_target`, cấm `environment:` và cấm `secrets.*` trên PR; ghim SHA 40 ký tự cho toàn bộ GitHub Actions; kiểm tra biến môi trường runtime của PR. |
| **Sinh SBOM tất định từ khóa image** | [`deploy/release/generate_sbom.py`](../../deploy/release/generate_sbom.py) | Xuất đồng thời **CycloneDX 1.5** (`sbom.cyclonedx.json`) và **SPDX 2.3** (`sbom.spdx.json`) cho `442` gói Debian, `154` gói Python, `3` tệp `yarn.lock` và `3` ứng dụng Git đã ghim SHA. |
| **Quét lỗ hổng CVE & rò rỉ secret** | [`deploy/release/evaluate_security_scan.py`](../../deploy/release/evaluate_security_scan.py) + [`deploy/release/cve-exceptions.json`](../../deploy/release/cve-exceptions.json) | Chạy Trivy (`vuln,secret`); chặn cứng mọi secret phát hiện trong image ở cả PR lẫn Release, và chặn cứng mọi lỗ hổng `CRITICAL` đã có bản vá chưa được duyệt ngoại lệ có thời hạn khi phát hành. |
| **`release-manifest.json` có chữ ký số Ed25519** | [`deploy/release/release_manifest.py`](../../deploy/release/release_manifest.py) | Tự động điền `git_sha`, `frappe_framework_ref`, `frappe_crm_ref`, `crm_cskh_ref`, `image` (`@sha256`), `migration_revision` (`crm_cskh.patches.v3_0.p3_schema_and_seeds`), `kpi_formula_version` (`cskh-metrics-v2`), ký & xác minh Ed25519, từ chối mọi tag/placeholder/`BLOCKED`. |
| **Chứng minh kéo cùng `@sha256` qua kiểm định & Staging** | [`deploy/release/verify_digest_promotion.py`](../../deploy/release/verify_digest_promotion.py) + [`deploy/release/deploy_staging_by_digest.sh`](../../deploy/release/deploy_staging_by_digest.sh) | Xác minh chữ ký Ed25519 và đối chiếu cùng một `@sha256:<64-hex>` từ `build-once` -> `verify_clean_site.py` (`45/45`) -> `run_isolated_smoke.py` / `docker inspect` 6 service ứng dụng trên Staging (`zero_digest_drift: true`, 0 bind-mount `/src`). |
| **Workflow phát hành chuẩn & thăng cấp Production** | [`.github/workflows/p4-release.yml`](../../.github/workflows/p4-release.yml) + [`.github/workflows/p4-production.yml`](../../.github/workflows/p4-production.yml) | Sẵn sàng kích hoạt ngay khi cấp phát `CSKH_REGISTRY_IMAGE` và GitHub Environments (`registry-build`, `staging`, `production`); nếu chưa cấp phát sẽ báo cáo `BLOCKED` tường minh thay vì giả lập thành công. |
| **Bộ kiểm thử đơn vị cho pipeline phát hành** | [`deploy/release/test_release_pipeline.py`](../../deploy/release/test_release_pipeline.py) | `11` ca kiểm thử bao phủ kiểm toán workflow, ranh giới 0-secret PR, toàn vẹn `SHA256SUMS.txt` (`10/10`), SBOM, cổng CVE/secret, chữ ký Ed25519, chống giả mạo phạm vi và chống trôi digest. |

---

## 2. Trạng thái Các Cổng Nghiệm thu `G02` / `G04` / `G05` & Các Hạng mục `BLOCKED` Ngoài Repo

| Mã cổng | Nội dung kiểm định | Trạng thái trên Runner CI Cô lập (`p4-ci.yml`) | Trạng thái Hạ tầng Đích Từ Xa (`p4-release.yml`) | Mã Blocker & Hành động Mở khóa (Chủ sở hữu / `T0`) |
| :---: | :--- | :---: | :---: | :--- |
| **`G02`** | Build custom image một lần từ lockfile, không mount `/src`, đủ `frappe` + `crm` + `crm_cskh`, đúng SHA và tải đủ HTTP assets tiếng Việt trên site sạch (`45/45` assertions). | **PASS** *(trên runner cô lập + OCI Registry loopback)* | **BLOCKED** *(chưa đẩy lên Registry từ xa được duyệt)* | **`BLK-INFRA-03`**: Cấp phát repo Container Registry được duyệt (`vars.CSKH_REGISTRY_IMAGE`), token push/pull tách quyền và khóa ký `RELEASE_SIGNING_PRIVATE_KEY` / `vars.RELEASE_SIGNING_PUBLIC_KEY`. |
| **`G04`** | PR chạy trên runner cô lập 0 secret; có lint, test tĩnh/unit, SBOM CycloneDX/SPDX, quét Trivy vuln+secret, ký Ed25519 `release-manifest.json` và chứng minh kéo lại đúng cùng `@sha256` sau khi xóa cache cục bộ (`zero_digest_drift: true`). | **PASS** *(cơ chế & kiểm định tự động hoàn chỉnh)* | **BLOCKED** *(chờ kích hoạt `p4-release.yml` với Registry thật)* | **`BLK-INFRA-03`**: Cấu hình GitHub Environment `registry-build` để chạy lượt phát hành đầu tiên sinh `release-manifest.json` phạm vi `production_candidate`. |
| **`G05`** | Máy chủ Staging chạy chính xác cùng `@sha256` digest đã qua cổng kiểm thử (`PULL_POLICY=always`, one-shot `migrator`, 0 bind-mount code). | **REHEARSED** *(qua `run_isolated_smoke.py` 10-service bằng `@sha256`)* | **BLOCKED** *(chưa có VPS Staging)* | **`BLK-INFRA-01`**: Cấp phát VPS Staging, DNS/TLS và cấu hình GitHub Environment `staging` (`STAGING_SSH_*`, `STAGING_DEPLOY_PATH`, `STAGING_ENV_FILE`). |

---

## 3. Kế hoạch Thực thi Tuần tự Các Gói Tiếp Theo Do `T1B` Phụ Trách (`P4-03` → `P4-09`)

Theo nguyên tắc chia nhỏ PR dễ kiểm định tại `P4.md §18.1`, ngay sau khi PR `P4-01` (#13) và PR `P4-02` được review/merge và hạ tầng Registry + Staging (`BLK-INFRA-01..05`) sẵn sàng, `T1B` triển khai tuần tự các gói sau trên nhánh riêng:

1. **`P4-03` — Hỗ trợ Diễn tập Migration `N-1`, Phân loại Tương thích & Đóng `BLG-P4-MIG-01` (`G06`):**
   - Dựng bản sao dữ liệu `N-1` (gồm cả kịch bản site cũ có `CSKH Export Job` mang `module = "Crm Cskh"` theo [`docs/p4/staging-migration-backlog.md`](./staging-migration-backlog.md)).
   - Phân loại thay đổi schema/patch theo Class A/B/C/D, đo đối soát số bản ghi + tổng KPI + quyền đọc tệp trước/sau, chạy `migrate × 2` chứng minh tính lũy đẳng (`idempotent`), kiểm chứng không mất bảng/cột/hàng của `CSKH Export Job`, và diễn tập quy trình phục hồi từ bản sao pre-migrate.
2. **`P4-04` — Kiểm định Bảo mật `S01`–`S20` Trên Staging & Test Hồi quy (`G10`–`G12`):**
   - Thực thi trọn bộ ma trận `S01`–`S20` trên môi trường Staging chạy custom image theo digest: truy cập chéo PII giữa Telesale/Team Lead/QA, đổi role/team/ownership tức thời, thu hồi phiên đăng nhập, kiểm tra điểm cuối API/files/export (`X-Accel-Redirect`, chống dò tồn tại tệp), rate limiting và nhật ký kiểm toán (`Audit Log`).
   - Mọi lỗi tái hiện được sẽ kèm bản sửa tối thiểu + regression test tự động và tổng hợp đầy đủ trong `docs/p4/security-review.md`.
3. **`P4-05` — Tự động hóa Backup Mã hóa Offsite, Diễn tập Restore Cô lập & Đo RPO/RTO (`G07` / `G08`):**
   - Đóng gói script backup tự động mã hóa (`restic` / `gpg` đã có sẵn trong base image `P4-01`) đẩy ra kho lưu trữ ngoài VPS bao gồm: bản dump MariaDB nhất quán, `public/files`, `private/files`, `site_config.json` và quản trị tách biệt `encryption_key`.
   - Khôi phục thực tế lên site DR cô lập, đối soát toàn vẹn dữ liệu + giải mã trường nhạy cảm + tải tệp đính kèm, đo thực nghiệm RPO/RTO so với ngưỡng được duyệt và bàn giao `docs/p4/backup-restore.md`.
4. **`P4-06` — Đo Kiểm Hiệu năng & Headroom VPS Trên Staging (`G18`):**
   - Tạo tập dữ liệu mô phỏng quy mô thực tế (`50.000` Work Item/tháng cùng lịch sử Contact Attempt, SLA Obligation và KPI Snapshot tương ứng) trên Staging.
   - Đo latency p50/p95/p99 và tài nguyên CPU/RAM/IOPS cho luồng import lead, phân phối, danh sách việc cần làm và drill-down báo cáo chất lượng; đối chiếu số đo trước/sau tối ưu đồng thời bảo toàn 100% độ đúng công thức KPI (`cskh-metrics-v2`) và phạm vi phân quyền, bàn giao `docs/p4/performance-report.md`.
5. **`P4-09` — Khóa Hợp đồng API & Kiểm thử Tương thích Hậu Bảo mật/UAT (`G21`):**
   - Sau khi hoàn tất `P4-04` (bảo mật) và UAT, chốt tài liệu `docs/p4/api-contract.md` cùng bộ compatibility tests tự động khóa chặt: cơ chế xác thực & phạm vi dữ liệu (`auth/scope`), cấu trúc mã lỗi chuẩn hóa, quy ước phiên bản (`versioning`), khóa chống ghi trùng (`idempotency`) và chính sách tương thích khi nâng cấp (`migration policy`).
