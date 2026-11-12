# Bộ Công cụ Phát hành Bất biến `P4-02` (`deploy/release`)

Thư mục này chứa toàn bộ trình kiểm toán cô lập PR & phân tách đặc quyền, bộ sinh SBOM, cổng quét lỗ hổng/secret nghiêm ngặt (`--strict`), bộ ký số Ed25519 cho `release-manifest.json`, trình chứng thực quảng bá cùng một digest `@sha256` (`Build-Once, Promote-By-Digest`), bộ lập kế hoạch kiểm thử theo `diff-impact` và khung benchmark cô lập `P4-06`.

## 1. Thành phần

| Tệp | Chức năng |
| :--- | :--- |
| [`verify_pr_isolation.py`](./verify_pr_isolation.py) | Kiểm toán tĩnh và runtime kiểu `fail-closed`: cấm `pull_request_target`, khóa `permissions: contents: read` trên PR, ghim SHA 40 ký tự cho toàn bộ GitHub Actions trong `p4-*.yml`, cưỡng chế phân tách đặc quyền giữa `push` (`registry-build`), `pull` (`registry-verify`) và `deploy` (`staging` / `production`), và kiểm 10/10 mã băm `SHA256SUMS.txt` + `release-lock.json`. |
| [`audit_registry_and_signing_config.py`](./audit_registry_and_signing_config.py) | Kiểm tra cấu hình và quyền sẵn có (`GITHUB_TOKEN` `packages: write`/`read` trên `ghcr.io` so với external registry) **trước khi** yêu cầu cấp secret mới; xác thực khóa công khai Ed25519 và kiểm tra ranh giới không chồng chéo secret theo từng vai trò (`preflight`, `push`, `pull`, `deploy-staging`, `deploy-production`). |
| [`generate_sbom.py`](./generate_sbom.py) | Sinh tất định hai bản kê phần mềm chuẩn **CycloneDX 1.5** (`sbom.cyclonedx.json`) và **SPDX 2.3** (`sbom.spdx.json`) trực tiếp từ `release-metadata` của image `@sha256`. |
| [`evaluate_security_scan.py`](./evaluate_security_scan.py) + [`cve-exceptions.json`](./cve-exceptions.json) | Đánh giá báo cáo JSON của Trivy (`vuln,secret`): ở chế độ mặc định `--strict` (bắt buộc cho phát hành), dừng khẩn cấp (`exit != 0`) nếu phát hiện bất kỳ secret nào, bất kỳ ngoại lệ sai phạm vi/hết hạn nào (bắt buộc khớp đồng thời `image_digest`, `cve_id`, `package`/`packages`, `installed_version`, `expires_on >= reference_date`, không tự duyệt bởi `T1B`) hoặc bất kỳ CVE `CRITICAL` nào chưa có ngoại lệ hợp lệ trong `cve-exceptions.json`. |
| [`t0-critical-cve-dossier.md`](./t0-critical-cve-dossier.md) | Hồ sơ trình `T0` đối với 4 CVE `CRITICAL` upstream Debian Bookworm chưa có bản vá (sau khi `T1B` đã vá triệt để 6/6 CVE Python `PyJWT==2.14.0` & `pillow==13.0.0` và gỡ gói thừa khỏi runtime). |
| [`release_manifest.py`](./release_manifest.py) | Sinh `release-manifest.json`, cưỡng chế `production_candidate` **chỉ được sinh và ký số Ed25519** khi truyền kèm `--security-scan` và `--exceptions` đạt `gate_mode == "strict"`, `status == "PASS"`, khớp tuyệt đối `artifact_name == manifest.image`, khóa mã băm `cve_exceptions_sha256` và tái xác minh trọn vẹn 5 điều kiện phạm vi/hạn hiệu lực ngoại lệ ngay trước cả hai bước `generate` và `sign`. |
| [`verify_digest_promotion.py`](./verify_digest_promotion.py) | Chứng minh mật mã học rằng `build-once`, `verify-by-digest` và tầng chạy thực tế (`isolated-smoke-report.json` hoặc `docker inspect` 6 service trên Staging/Production) dùng **chính xác cùng một `@sha256:<64-hex>`** (`zero_digest_drift: true`, không bind-mount code). |
| [`deploy_staging_by_digest.sh`](./deploy_staging_by_digest.sh) | Triển khai Staging/Production theo digest: xác minh chữ ký Ed25519 (`production_candidate`), cấm mọi khối `build:` trong Compose, chạy `docker compose ... --no-build` hoàn toàn trên digest đã kéo và xuất `staging-digest-attestation.json`. |
| [`run_diff_impact_verification.py`](./run_diff_impact_verification.py) | Nạp 5 inventory kiểm thử chuẩn (`g007`, `p1`, `p2`, `p3`, `p3_ui`) + module phụ trợ, ánh xạ `git diff` sang phạm vi hồi quy (tự động nâng lên toàn bộ P0–P3 khi đổi image/lock/hooks/schema) và chạy trong `compose.verify.yaml` kèm `ci_junit_gate.py`. |
| [`benchmark_p4_isolated.py`](./benchmark_p4_isolated.py) | Khung benchmark cô lập `P4-06` (`isolated_smoke_1k`, `isolated_staging_10k`, `isolated_p4_target_50k` 0-PII): khóa cứng cấm trỏ vào VPS của Owner/Production, đo `p50/p95/p99`, `throughput_rps`, `error_rate_pct`, `queue_lag`, `CPU/RAM/DB` và giữ `sla_commitment_status = UNCOMMITTED_PENDING_MEASURED_RUN_AND_OWNER_APPROVAL`. |
| [`test_release_pipeline.py`](./test_release_pipeline.py) | Bộ unit test kiểm chứng toàn bộ ranh giới bảo mật PR, phân tách quyền, SBOM, cổng CVE/secret, khóa ký `production_candidate`, chống trôi digest, lập kế hoạch `diff-impact` và khóa an toàn benchmark. |

## 2. Ma Trận Phân Tách Quyền (`Push` / `Pull` / `Deploy`)

| Môi trường / Job | Quyền được cấp | Quyền & Secret bị cấm tuyệt đối (kiểm tự động) |
| :--- | :--- | :--- |
| PR CI (`p4-ci.yml`) | `contents: read`, loopback OCI `127.0.0.1:15000`, khóa Ed25519 tạm thời (`ci_ephemeral_rehearsal`) | Mọi quyền `: write`, mọi `environment:`, mọi `secrets.*` ngoài `GITHUB_TOKEN` mặc định |
| `registry-build` (`build-once-sign-and-push`) | `packages: write` (tự dùng `GITHUB_TOKEN` nếu repo thuộc `ghcr.io/<owner>/...`, hoặc `REGISTRY_PUSH_*`), `RELEASE_SIGNING_PRIVATE_KEY` | `REGISTRY_PULL_*`, `STAGING_SSH_*`, `PROD_SSH_*` |
| `registry-verify` (`verify-by-digest`) | `packages: read` (tự dùng `GITHUB_TOKEN` trên `ghcr.io` hoặc `REGISTRY_PULL_*` chỉ đọc) | Mọi quyền `: write`, `REGISTRY_PUSH_*`, `RELEASE_SIGNING_PRIVATE_KEY`, `STAGING_SSH_*`, `PROD_SSH_*`, `docker build` |
| `staging` (`deploy-staging`) | `REGISTRY_PULL_*` (nếu registry riêng), `STAGING_SSH_*` | `packages: write`, `REGISTRY_PUSH_*`, `RELEASE_SIGNING_PRIVATE_KEY`, `PROD_SSH_*`, `docker build` |
| `production` (`deploy-production`, cần `T0`/Owner duyệt) | `REGISTRY_PULL_*` (nếu registry riêng), `PROD_SSH_*`, backup bắt buộc trước khi cuộn | `packages: write`, `REGISTRY_PUSH_*`, `RELEASE_SIGNING_PRIVATE_KEY`, `STAGING_SSH_*`, `docker build` |

## 3. Lệnh Kiểm tra Nhanh Ở Local / CI

```bash
# 1. Kiểm toán workflow, phân tách quyền, ranh giới 0-secret cho PR và khóa nguồn
python3 deploy/release/verify_pr_isolation.py --force-pr-env-check

# 2. Kiểm tra cấu hình Registry & khóa ký sẵn có trước khi xin cấp secret
python3 deploy/release/audit_registry_and_signing_config.py --role preflight

# 3. Xem kế hoạch hồi quy diff-impact & cấu hình benchmark cô lập (không chạm Docker/VPS)
python3 deploy/release/run_diff_impact_verification.py --plan-only --full-suite
python3 deploy/release/benchmark_p4_isolated.py --plan-only --confirm-isolated-target --out /tmp/bench-plan.json

# 4. Chạy toàn bộ unit test của bộ công cụ phát hành
python3 -m unittest discover -s deploy/release -p "test_*.py" -v
```

## 4. Phương Án Rollback Tất Định Theo Digest (`G06` / `G23`)

1. **Khi chưa chạy migration phá hủy (`additive-only` / tương thích ngược `N-1`):**
   - Lấy `previous_release_id` cùng cặp `release-manifest.json` + `release-manifest.json.sig` của bản ổn định liền trước (`N-1`).
   - Gán lại `APP_IMAGE=<digest_N_minus_1>`, `PULL_POLICY=always`, `MIGRATE_SITES=false` trong tệp `.env` đích và gọi lại `deploy_staging_by_digest.sh` (tự động xác minh chữ ký Ed25519 của bản `N-1`, kéo lại đúng `@sha256` cũ với `--no-build`, cuộn lại 6 container app và xuất biên bản `zero_digest_drift: true`).
2. **Khi bản phát hành có thay đổi dữ liệu cần hoàn trả:**
   - Khôi phục bản sao lưu mã hóa bắt buộc đã chụp ngay trước phát hành (`PROD_PRE_RELEASE_BACKUP_CMD` theo quy trình `P4-05`), sau đó cuộn container về đúng `@sha256` của `previous_release_id` và chạy lại kiểm chứng sức khỏe + đối soát KPI.
