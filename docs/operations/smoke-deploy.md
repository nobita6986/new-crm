# Smoke và restore cô lập — P0

Runbook này tái sử dụng [workflow P0](../../.github/workflows/p0.yml). Smoke P0 là clean bootstrap, cài/migrate, HTTP và backup/restore trên stack DEV cô lập; không phải production rollout hoặc chứng nhận production image.

## Môi trường và cách chạy

Workflow chạy khi mở/cập nhật PR vào `main` hoặc push `main`; không có `workflow_dispatch`. Runner Ubuntu dùng project Compose `p0-ci-${GITHUB_RUN_ID}`, env tạm `p0.env`, named volumes riêng và site dữ liệu giả `p0-ci.localhost`. Suite chạy trên site riêng `p0-test.localhost`; restore trên `p0-restore.localhost`.

```bash
gh run list --workflow p0.yml --branch main --limit 5
gh run view <run-id>
gh run view <run-id> --log-failed
gh run download <run-id> --name p0-g007-evidence-<run-id> --dir <thu-muc-evidence-rieng>
```

Không dùng redirect PowerShell `gh api ... > file.zip` để tải binary; dùng `gh run download` hoặc binary-safe download và tự hash zip nếu đối chiếu API `artifact.digest`.

## Tiêu chí PASS

1. Bootstrap từ volume rỗng cài đủ `frappe`, `crm`, `crm_cskh` đúng revision/image lock; `INSTALL_CSKH=1` là bắt buộc, không override một template upstream-only để làm xanh CI.
2. Lint, các probe/DEV regression guards và migrate đạt. JUnit gate có expected/observed/passing 99/99/99; không thiếu method, FAIL, ERROR, SKIP hoặc parse error. Các auxiliary/helper tests được báo riêng.
3. Direct HTTP 18/18, File-oracle 9/9, remaining-gaps 74/74 PASS theo inventory probe hiện hành; giữ actor và assertion, không suy từ exit code app-suite tổng hợp.
4. Backup gồm DB, public files và private files. Workflow tạo site restore mới, restore đủ ba phần, migrate, kiểm tra installed apps/health, seed private file và counts `(CRM Lead, CRM Call Log, CRM Task) = (15, 3, 2)`.
5. Artifact có workflow metadata gắn đúng run/commit, module status, XML/log và probe JSON; sanitizer không được dùng thay cho phép đo redaction của app. Kiểm tra toàn bộ outcome được enforce, không chỉ `gate_passed`.
6. Workflow cleanup stack/volumes đúng project CI kể cả khi lỗi. Không xóa volume hay dừng stack DEV của Owner để tạo môi trường test sạch.

## Tái tạo cục bộ

Theo [dev-setup](dev-setup.md) nhưng dùng project Compose, env file, site và cổng riêng; không dùng `dev.localhost` có dữ liệu đang xem làm test site. Giữ image/SHA từ `.env.example`, password DEV riêng, DB/Redis không publish. Các lệnh đầy đủ cho từng bước nằm trong workflow để tránh duy trì một bản script restore khác bị lệch.

Kiểm tra project/site/volume mục tiêu trước khi cleanup. `docker compose down -v` chỉ dành cho stack test cô lập đã xác định; không dùng như thao tác sửa sự cố trên DEV có dữ liệu cần giữ. `bench migrate` không cung cấp downgrade tự động: khi migration lỗi, giữ log/backup và phục hồi vào site mới để xác minh trước khi thay site đang dùng.

## Mốc nghiệm thu đã có

PR #2 merge `584cbc2`; CI push `main` [#41320842991](https://github.com/nobita6986/new-crm/actions/runs/41320842991) thành công. [P0 exit report](../architecture/p0-exit-report.md) giữ mốc nghiệm thu #174 cố định và backlog P1. Run mới phục vụ kiểm chứng thay đổi mới, không tạo vòng commit tài liệu để liên tục thay số run nghiệm thu.
