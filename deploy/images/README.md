# `deploy/images` — Công thức Build Production Image & Bộ Kiểm định Site Sạch (`P4-01`)

- **Gói công việc:** `P4-01` (Custom Production Image, Release Lock & Clean-Site Verification)
- **Cổng nghiệm thu gắn kết:** `G02`, `G04`, `G05` ([`docs/root-plan/P4.md`](../../docs/root-plan/P4.md) §4, §18, §19)
- **Người sở hữu kỹ thuật:** `T1B` (bàn giao cho `T1A` Compose, `P4-02` CI/CD và `T1C` kiểm định độc lập)
- **Tài liệu kiến trúc:**
  - [`docs/p4/P4-ADR-02-custom-image-and-lock.md`](../../docs/p4/P4-ADR-02-custom-image-and-lock.md)
  - [`docs/p4/p4-01-image-build-and-lock.md`](../../docs/p4/p4-01-image-build-and-lock.md)
  - [`docs/p4/p4-02-pipeline-input-contract.md`](../../docs/p4/p4-02-pipeline-input-contract.md)

## Cấu trúc Thư mục

| Tệp / Thư mục | Mục đích |
| :--- | :--- |
| [`release-lock.json`](./release-lock.json) | Khóa phiên bản máy đọc (`cskh-p4-release-lock-v1`): ghim base image `@sha256`, công cụ build, SHA 40 ký tự của `frappe`/`crm`/`frappe_docker`, SHA-256 `yarn.lock`, `apps_tree_sha`, `crm_cskh_tree_sha` và hợp đồng biến Compose `APP_IMAGE`. |
| [`Containerfile`](./Containerfile) | Công thức BuildKit đa tầng (`base -> builder -> backend`) chạy user non-root `frappe` (`1000:0`), biên dịch sẵn Frappe Desk + Frappe CRM SPA (`/crm`) + CSKH (`/cskh/*`) tiếng Việt và đóng gói `/home/frappe/frappe-bench/release-metadata/*`. |
| [`Containerfile.dockerignore`](./Containerfile.dockerignore) | Bộ lọc ngữ cảnh build trắng danh sách (deny-by-default): chặn toàn bộ `.git`, `.env*`, khóa bí mật và bằng chứng cũ khỏi tầng image. |
| [`resources/`](./resources) | Các tệp runtime trích xuất từ `frappe/frappe_docker@v4.0.1` (`2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`), đã gia cố liên kết nguyên tử `sites/assets` và thứ tự tải app chuẩn `frappe -> crm -> crm_cskh`. |
| [`build-image.sh`](./build-image.sh) | Trình biên dịch `build-once`: kiểm tra tính toàn vẹn cây `apps/`, build image, trích xuất khóa phụ thuộc (`pip-freeze.txt`, `yarn-locks.sha256`, `dpkg-packages.txt`, `build-manifest.json`) và xuất `image-inspect-summary.json`. |
| [`compose.verify.yaml`](./compose.verify.yaml) | Bộ Compose kiểm thử cô lập tương thích hợp đồng `APP_IMAGE` + `PULL_POLICY` của `T1A` (`deploy/compose/overrides/compose.cskh-common.yaml`), chỉ mở cổng loopback `127.0.0.1:18780` trên `frontend`. |
| [`verify_clean_site.py`](./verify_clean_site.py) | Kịch bản kiểm định tự động cổng `G02` (45/45 assertions): tạo site sạch mới trên volume rỗng (không mount `/src`), cài `crm` + `crm_cskh`, chạy `migrate` x2, đối chiếu `list-apps`, upstream SHA, nhãn tiếng Việt, kiểm toán 23 DocType `CRM CSKH` và tải thử toàn bộ bundle JS/CSS qua `frontend` (Nginx) → `backend` (Gunicorn). |
| `evidence/` | Hồ sơ bằng chứng thực nghiệm của `P4-01`: `build.log`, `build-manifest.json`, `image-inspect-summary.json`, `pip-freeze.txt`, `yarn-locks.sha256`, `dpkg-packages.txt`, `upstream-patches.diff`, `crm-cskh-files.sha256`, `clean-site-verify.json` và `SHA256SUMS.txt`. |

## Cách Chạy Lại Cục bộ / CI Cô lập

```bash
# 1. Build image từ release-lock.json và trích xuất hồ sơ khóa
bash deploy/images/build-image.sh \
  --out-dir deploy/images/evidence \
  --tag crm-cskh:0.0.1-92e14af

# 2. Kiểm định site sạch + HTTP frontend assets (G02)
APP_IMAGE="$(jq -r '.local_image_id' deploy/images/evidence/image-inspect-summary.json)"
python3 deploy/images/verify_clean_site.py \
  --app-image "${APP_IMAGE}" \
  --pull-policy never \
  --port 18780 \
  --out deploy/images/evidence/clean-site-verify.json
```
