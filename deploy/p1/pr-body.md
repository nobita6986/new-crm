# T1A — CSKH P1 UI hoàn chỉnh (5 màn hình P1 §9, Phân phối nền RQ, Việt hóa 100% & E2E thực tế)

## Bối cảnh & nguồn gốc tích hợp

* **Nguồn yêu cầu:** `docs/root-plan/P1.md` §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu).
* **Đồng bộ backend có kiểm soát:** Nhánh `codex/p1-ui` tích hợp trực tiếp từ Git cả baseline `3025495f58bc0391c3f8d092af500b0566a5f038`, mốc `64b7486f29ad712106d167dfaea50e3b060a6845` và commit mới nhất đã qua CI của PR #4 (`codex/p1-foundation`): **`c49f7f89d18cfbc3c21a3970d66b07c5d6c11279`** (gồm phân phối nền RQ `6ce0341` + các bản sửa `L-16..L-22` + khóa RQ worker CI `c49f7f8`), tạo SHA mã nguồn tích hợp **`63510483a0aa1ed4238b630ba384c5a362659450`**.
* **Giữ nguyên bộ evidence baseline (`0d3f2bc` / `d0842cc`):** Bảo toàn nguyên vẹn 11 ảnh chụp nghiệm thu (`deploy/p1/evidence/screenshots/*.png`), `browser-manifest.json` và `http-e2e-report.json`; xuất riêng báo cáo E2E phần cập nhật tại `deploy/p1/evidence/async-distribution-delta-e2e.json` và `deploy/p1/evidence/http-e2e-delta-report.json`.
* **Không nới quyền backend:** `0` dòng thay đổi trên `permissions.py` và `p1_permissions.py`; giữ nguyên 100% chương 0–5 của T1B trong `docs/architecture/P1-Exit-Report.md` và hợp nhất có chủ đích `translations/vi.csv` (**587 dòng**, `0` dòng lỗi).

## Năm màn hình hoàn thiện (P1 §9) & Phân phối nền RQ

| Màn | Đường dẫn | Nội dung hoàn thiện chính |
|---|---|---|
| Trang 1 — Kho khách hàng | `/cskh/pool` | Trạng thái suy ra (`Chưa phân phối`, `Trong hàng đợi`, `Đã giao`, `Xung đột`, `Đã thu hồi`, `Đã đóng`, `Lỗi dữ liệu`) không chạm `CRM Lead.status`; tách `Số khách hàng duy nhất` vs `Số công việc`; bộ lọc chiến dịch/trạng thái/nguồn/lô/tìm kiếm (`0...` & `+84...`); ẩn hoàn toàn PII và chặn `search` (`403`) với BOD |
| Trang 2 — Nhập danh sách | `/cskh/import` | Upload riêng tư CSV/XLSX → ánh xạ 3 cột (`số điện thoại`, `họ tên`, `nguồn`) → preview tiếng Việt → confirm → tiến độ Bước 3 trực tiếp (`Số dòng tạo mới / dùng lại / bỏ qua / lỗi`), bảng `Lịch sử nhập`, `Chạy lại công việc thất bại`, và `download_error_file` chỉ cho phép `CSKH CRM Admin` (`200`), từ chối mọi role khác (`403`) |
| Trang 3 — Chiến dịch & quy tắc | `/cskh/campaigns` | Tạo/Sửa/Đóng chiến dịch, tính trực tiếp `%` chuẩn hóa trọng số, `capacity = 0` hiển thị **`Không giới hạn`**, preview đủ 5 cột, chống bấm đúp, kiểm tra stale preview (`RULE_CHANGED`, `PREVIEW_EXPIRED`); **phân phối nền RQ**: nhận `run_id` + `queued`/`running` hiển thị tiến độ `.is-info` (`progress / total (pct%)`) **không coi enqueue là hoàn tất**, tự polling `distribution.get_run`, **phục hồi theo dõi sau F5/reload** (`sessionStorage` + `?run_id=` + `run_history.runs`), xử lý rõ `completed_with_errors` (`.is-warning`) và `failed` (`.is-error` tiếng Việt + `[MÃ_LỖI]` + nút chạy lại) |
| Trang 4 — Khách được giao | `/cskh/my-customers` | Lọc chiến dịch, tìm kiếm tên/SĐT, sắp xếp thời gian giao, ngăn `Chi tiết quyền phụ trách` + `Lịch sử phụ trách` chỉ ghi thêm; `CSKH CRM Admin` chuyển giao / thu hồi kèm lý do bắt buộc & `expected_owner`; `Sales Manager` chỉ xem phạm vi đội |
| Trang 5 — Bảng điều khiển phân phối | `/cskh/dashboard` | Bộ lọc `Chiến dịch`, `Từ ngày` / `Đến ngày`, `Đội`, `Người được giao`, `Mã đợt chạy`; phân biệt rõ **`Quyền phụ trách hiện tại (công việc đang hoạt động)`** và **`Sự kiện phân công (nhật ký kiểm toán)`** + bảng lịch sử `Đợt phân phối` (gồm trạng thái `Trong hàng đợi`) |

## Kết quả kiểm chứng thực tế trên nhánh `codex/p1-ui`

* **Kiểm toán tĩnh & Cổng CI (`.github/workflows/p1.yml`):**
  * `ruff==0.14.0 check` + `format --check`: **PASS** (`104` tệp Python chuẩn format).
  * `p1_test_inventory.py --check`: **PASS** (`199` method P1 trong `8` module; P0 core giữ nguyên `99` method).
  * `deploy/p1/ui_test_inventory.py --check`: **PASS** (`6` method `test_ui_p1_endpoints` được đưa vào CI và khóa bằng `ci_junit_gate.py` tại `/tmp/p1-evidence/ui/`).
  * `deploy/p1/check_translations_audit.py`: **PASS** (`used=209`, `declared=383`, `vi.csv=587 rows`, `missing=0`, `untranslated=0`, `orphan=0`).
* **Suite đơn vị & tích hợp (`deploy/p1/run_suites.py`):** **15/15 module PASS (`312/312` method)** — P1 `199/199` + UI `test_ui_p1_endpoints` `6/6` + P0 core `99/99` + transport `8/8`.
* **E2E phân phối nền & các bản sửa delta trên Chromium + HTTP thật (`deploy/p1/verify_p1_async_dist_e2e.mjs` → `deploy/p1/evidence/async-distribution-delta-e2e.json`):** **4/4 nhóm PASS** (`ASYNC-01` enqueue + không báo xong sớm + reload recovery + polling `100%`; `ASYNC-02` `completed_with_errors`; `ASYNC-03` `failed` `[RULE_CHANGED]`; `BACKEND-DELTA` `L-20`/`L-21`/`L-22`).
* **HTTP E2E đa vai trò thật (`deploy/p1/verify_p1_http_e2e.py` → `deploy/p1/evidence/http-e2e-delta-report.json`, giữ nguyên baseline `http-e2e-report.json`):** **6/6 nhóm E2E PASS** trên 8 phiên thật (`admin`, `bod`, `leader_a`, `leader_b`, `staff_a`, `staff_b`, `outside`, `guest`).
* **Baseline trình duyệt Chromium thật (`0d3f2bc` → `deploy/p1/evidence/browser-manifest.json` + 11 screenshots):** **11/11 lượt PASS** được bảo toàn nguyên vẹn.

## Cách dựng & tái hiện cho T1C

```bash
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 3025495f58bc0391c3f8d092af500b0566a5f038 HEAD
git merge-base --is-ancestor c49f7f89d18cfbc3c21a3970d66b07c5d6c11279 HEAD

cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

python -X utf8 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json
python -X utf8 deploy/p1/ui_test_inventory.py --check deploy/p1/ui_test_inventory.json
python -X utf8 deploy/p1/check_translations_audit.py
python -X utf8 deploy/p1/run_suites.py
node deploy/p1/verify_p1_async_dist_e2e.mjs
P1_REPORT_OUT=deploy/p1/evidence/http-e2e-delta-report.json python -X utf8 deploy/p1/verify_p1_http_e2e.py
```

## Giới hạn còn mở ngoài phạm vi UI hiện tại

1. `test_ui_p1_endpoints` (`6/6` method) và `check_translations_audit.py` đã được gắn vào cổng CI `.github/workflows/p1.yml`; các script Chromium Playwright (`verify_p1_browser_e2e.mjs`, `verify_p1_async_dist_e2e.mjs`) chạy tự động trên stack cô lập `crm-cskh-p1` để T1C tái hiện trực tiếp.

Quyết định merge và ký GO P2 thuộc T0/Owner.
