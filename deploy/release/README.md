# Bộ Công cụ Phát hành Bất biến `P4-02` (`deploy/release`)

Thư mục này chứa toàn bộ trình kiểm toán cô lập PR, bộ sinh SBOM, cổng quét lỗ hổng/secret, bộ ký số Ed25519 cho `release-manifest.json` và trình chứng thực quảng bá cùng một digest `@sha256` (`Build-Once, Promote-By-Digest`) từ lúc build tới kiểm thử và triển khai Staging/Production.

## 1. Thành phần

| Tệp | Chức năng |
| :--- | :--- |
| [`verify_pr_isolation.py`](./verify_pr_isolation.py) | Kiểm toán tĩnh và runtime kiểu `fail-closed`: cấm `pull_request_target`, bắt buộc `permissions: contents: read` và không gắn `environment:` / `secrets.*` trên workflow PR, bắt buộc ghim SHA 40 ký tự cho toàn bộ GitHub Actions trong `p4-*.yml`, đối chiếu `release-lock.json` và 10/10 mã băm `SHA256SUMS.txt`. |
| [`generate_sbom.py`](./generate_sbom.py) | Sinh tất định hai bản kê phần mềm chuẩn **CycloneDX 1.5** (`sbom.cyclonedx.json`) và **SPDX 2.3** (`sbom.spdx.json`) trực tiếp từ `release-metadata` của image (`442` gói Debian Bookworm, `154` gói Python, `3` tệp `yarn.lock`, `3` ứng dụng Git đã ghim SHA). |
| [`evaluate_security_scan.py`](./evaluate_security_scan.py) + [`cve-exceptions.json`](./cve-exceptions.json) | Đánh giá báo cáo JSON của Trivy (`vuln,secret`): dừng khẩn cấp (`exit != 0`) nếu phát hiện bất kỳ secret nào rò rỉ trong image, hoặc nếu có lỗ hổng `CRITICAL` đã có bản vá nhưng chưa có ngoại lệ còn hạn trong `cve-exceptions.json`. |
| [`release_manifest.py`](./release_manifest.py) | Tạo khóa, sinh `release-manifest.json` chuẩn hóa theo `P4.md §4.1`, ký số **Ed25519** (`.sig` + `.envelope.json`) không ghi lộ khóa riêng ra log, và xác minh chữ ký kèm phân tách phạm vi `environment_scope` (`ci_ephemeral_rehearsal` so với `production_candidate`). |
| [`verify_digest_promotion.py`](./verify_digest_promotion.py) | Chứng minh mật mã học rằng `build-once` (`image-inspect-summary.json`), `verify-by-digest` (`clean-site-verify.json`) và tầng chạy thực tế (`isolated-smoke-report.json` hoặc `docker inspect` 6 service trên Staging/Production) dùng **chính xác cùng một `@sha256:<64-hex>`** (`zero_digest_drift: true`, không bind-mount `/src`). |
| [`deploy_staging_by_digest.sh`](./deploy_staging_by_digest.sh) | Điểm vào triển khai Staging/Production theo digest: xác minh chữ ký Ed25519 (`production_candidate`) trước khi chạm Docker, kiểm `PULL_POLICY=always` & `MIGRATE_SITES=false`, kéo đúng digest, chạy one-shot `migrator`, cuộn các service và xuất `staging-digest-attestation.json`. |
| [`test_release_pipeline.py`](./test_release_pipeline.py) | Bộ unit test kiểm chứng toàn bộ ranh giới bảo mật PR, SBOM, cổng CVE/secret, chữ ký Ed25519 và chống trôi digest. |

## 2. Lệnh Kiểm tra Nhanh Ở Local / CI

```bash
# 1. Kiểm toán workflow, ranh giới 0-secret cho PR và khóa nguồn P4-01
python3 deploy/release/verify_pr_isolation.py --force-pr-env-check

# 2. Chạy toàn bộ unit test của bộ công cụ phát hành
python3 -m unittest discover -s deploy/release -p "test_*.py" -v
```

## 3. Biến & Secret Cần Cấp Phát Khi Mở Cổng Registry & Staging (`BLK-INFRA-01`, `BLK-INFRA-03`)

- **Repository Variables (công khai nội bộ repo, không chứa secret):**
  - `CSKH_REGISTRY_IMAGE`: Đường dẫn repo image được duyệt (ví dụ `ghcr.io/<org>/crm-cskh`).
  - `RELEASE_SIGNING_PUBLIC_KEY`: Khóa công khai PEM Ed25519 dùng để xác minh `release-manifest.json.sig` ở mọi tầng.
- **GitHub Environment `registry-build` (chỉ mở cho `p4-release.yml` trên nhánh/tag phát hành, KHÔNG có khóa SSH VPS):**
  - Secrets: `REGISTRY_PUSH_USERNAME`, `REGISTRY_PUSH_TOKEN`, `RELEASE_SIGNING_PRIVATE_KEY`.
- **GitHub Environment `staging` (chỉ quyền kéo image + SSH máy chủ Staging):**
  - Variables: `STAGING_DEPLOY_PATH`, `STAGING_ENV_FILE`, `STAGING_SSH_PORT`.
  - Secrets: `STAGING_SSH_HOST`, `STAGING_SSH_USER`, `STAGING_SSH_PRIVATE_KEY`, `STAGING_KNOWN_HOSTS`.
- **GitHub Environment `production` (`Required reviewers` bắt buộc bởi `T0`/Owner):**
  - Variables: `PROD_DEPLOY_PATH`, `PROD_ENV_FILE`, `PROD_SSH_PORT`, `PROD_PRE_RELEASE_BACKUP_CMD`.
  - Secrets: `PROD_SSH_HOST`, `PROD_SSH_USER`, `PROD_SSH_PRIVATE_KEY`, `PROD_KNOWN_HOSTS`.
