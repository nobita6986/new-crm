# T1A — CSKH P1 UI hoàn chỉnh (5 màn hình P1 §9, Phân phối nền RQ, Đóng L-23/L-24/L-25/L-26, Việt hóa 100% & E2E thực tế)

## Bối cảnh & nguồn gốc tích hợp

* **Nguồn yêu cầu:** `docs/root-plan/P1.md` §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu).
* **Đồng bộ backend có kiểm soát:** Nhánh `codex/p1-ui` tích hợp trực tiếp từ Git cả baseline `3025495f58bc0391c3f8d092af500b0566a5f038`, `64b7486f29ad712106d167dfaea50e3b060a6845`, `c49f7f89d18cfbc3c21a3970d66b07c5d6c11279` và commit `L-25` mới nhất của PR #4 (`codex/p1-foundation`): **`c997df76b9a29a9f8d8be4a4ffd0875035762672`**, tạo SHA mã nguồn ứng dụng tích hợp **`3f07b9b3f2e68f9bf3eea005c634c7a11393ec8b`**.
* **Giữ nguyên bộ evidence baseline (`0d3f2bcbbf9715a4c0c783903ce750de8646583d` / `d0842cc`):**
  * Bảo toàn nguyên vẹn `0` thay đổi trên 11 ảnh chụp nghiệm thu (`deploy/p1/evidence/screenshots/*.png`), `browser-manifest.json` và `http-e2e-report.json`.
  * Đính chính provenance trong `deploy/p1/verify_p1_async_dist_e2e.mjs` và ghi `provenance_erratum` trong `deploy/p1/evidence/async-distribution-delta-e2e.json` (`0d3f2bc9da3e3e497a999baeb9a4dd19129224fb` → `0d3f2bcbbf9715a4c0c783903ce750de8646583d`), giữ nguyên 100% các số đo `checks[]` đã lưu.
  * Xuất riêng báo cáo E2E Đợt 3 cho `L-23..L-26` tại `deploy/p1/evidence/round3-l23-l26-delta-e2e.json` và đối soát HTTP tại `deploy/p1/evidence/http-e2e-delta-report.json`.
* **Không nới quyền backend:** `0` dòng thay đổi so với PR #4 trên `permissions.py` và `p1_permissions.py`; giữ nguyên 100% chương 0–6 của T1B trong `docs/architecture/P1-Exit-Report.md` và hợp nhất có chủ đích `translations/vi.csv` (**588 dòng**, `0` dòng lỗi).

## Năm màn hình hoàn thiện (P1 §9), Phân phối nền RQ & Bản sửa Đợt 3 (`L-23..L-26`)

| Màn / Hạng mục | Đường dẫn / Tệp | Nội dung hoàn thiện chính |
|---|---|---|
| Trang 1 — Kho khách hàng (`L-23`, `L-25`, `L-26`) | `/cskh/pool` (`pool_ui.py`, `pool.js`) | Phân trang SQL `LIMIT/OFFSET` + `parse_pagination` (`400 INVALID_REQUEST`); chặn tuyệt đối rò rỉ chéo đội qua lịch sử `revoked`/`closed` (`L-23`: kiểm tra đủ 5 mệnh đề quyền hiện tại trên `assigned`, `queued`, `CSKH Active Claim`, `lead_owner`, `CSKH Assignment History`); nối bộ lọc `Lô` (`batch`) trực tiếp vào `CSKH Import Batch Lead` qua `reporting.lead_in_batch_clause` / `work_item_in_batch_clause` (`L-25`); ẩn hoàn toàn PII và chặn `search` (`403`) với BOD |
| Trang 2 — Nhập danh sách (`L-25`, `L-26`) | `/cskh/import` (`import_files.py`, `import_history.py`, `import.js`) | Upload riêng tư CSV/XLSX → ánh xạ 3 cột (`số điện thoại`, `họ tên`, `nguồn`) có sanitize công thức/HTML & lưu `CSKH Import Batch Lead` → preview tiếng Việt → confirm → tiến độ Bước 3 trực tiếp, bảng `Lịch sử nhập` (`400 INVALID_REQUEST` khi sai phân trang), `Chạy lại công việc thất bại` và `download_error_file` chỉ cho phép `CSKH CRM Admin` (`200`) |
| Trang 3 — Chiến dịch & quy tắc (`L-24`, `L-26`) | `/cskh/campaigns` (`run_history.py`, `campaigns.js`) | Tạo/Sửa/Đóng chiến dịch, tính trực tiếp `%` chuẩn hóa trọng số, `capacity = 0` = **`Không giới hạn`**, tải lại `ownership_ui.assignees(campaign)` theo chiến dịch đang chọn (`L-26`); **phân phối nền RQ + `L-24`**: cô lập theo actor toàn bộ `assigned`, `skipped`, `preview`, `progress`, `total`, `created`, `errors` trên cả chiến dịch chung (`team_scope IS NULL`) lẫn live polling `run_id=` của `Sales Manager`, phục hồi theo dõi sau F5/reload |
| Trang 4 — Khách được giao (`L-26`) | `/cskh/my-customers` (`ownership_ui.py`, `my-customers.js`) | Lọc chiến dịch, tìm kiếm tên/SĐT, ngăn `Chi tiết quyền phụ trách` + `Lịch sử phụ trách`; `ownership_ui.assignees(campaign)` áp đúng `team_scope` + điều kiện hợp lệ quy tắc/sức chứa (`404 NOT_FOUND` nếu chiến dịch không tồn tại) cho hộp chọn `Chuyển người phụ trách` |
| Trang 5 — Bảng điều khiển phân phối | `/cskh/dashboard` (`dashboard.js`) | Bộ lọc `Chiến dịch`, `Từ ngày` / `Đến ngày`, `Đội`, `Người được giao`, `Mã đợt chạy`; phân biệt rõ **`Quyền phụ trách hiện tại (công việc đang hoạt động)`** và **`Sự kiện phân công (nhật ký kiểm toán)`** |

## Kết quả kiểm chứng thực tế trên nhánh `codex/p1-ui`

* **Kiểm toán tĩnh & Cổng CI (`.github/workflows/p1.yml`):**
  * `ruff==0.14.0 check` + `format --check`: **PASS** (`108` tệp Python chuẩn format).
  * `p1_test_inventory.py --check`: **PASS** (`203` method P1 trong `8` module; P0 core giữ nguyên `99` method).
  * `deploy/p1/ui_test_inventory.py --check`: **PASS** (`11` method trong `crm_cskh.tests.test_ui_p1_endpoints` — gồm regression hai đội + positive control cho `L-23`, `L-24`, `L-25`, `L-26` và `upload_import_file` — khóa bằng `ci_junit_gate.py`).
  * `deploy/p1/check_translations_audit.py`: **PASS** (`used=210`, `declared=384`, `vi.csv=588 rows`, `missing=0`, `untranslated=0`, `orphan=0`).
* **Suite đơn vị & tích hợp (`deploy/p1/run_suites.py`):** **15/15 module PASS (`321/321` method)** — P1 `203/203` + UI `11/11` + P0 core `99/99` + transport `8/8`.
* **E2E Đợt 3 (`L-23..L-26`) trên Chromium + HTTP + RQ thật (`deploy/p1/verify_p1_round3_l23_l26_e2e.mjs` → `deploy/p1/evidence/round3-l23-l26-delta-e2e.json`):** **PASS** (0 lỗi console/page/500, 0 XSS, 0 lộ SĐT thô).
* **E2E phân phối nền (`deploy/p1/evidence/async-distribution-delta-e2e.json`):** **4/4 nhóm PASS** (đã ghi `provenance_erratum` sang `0d3f2bcbbf9715a4c0c783903ce750de8646583d`, giữ nguyên kết quả đo).
* **HTTP E2E đa vai trò thật (`deploy/p1/verify_p1_http_e2e.py` → `deploy/p1/evidence/http-e2e-delta-report.json`):** **6/6 nhóm E2E PASS**.

## Cách dựng & tái hiện cho T1C

```bash
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 3025495f58bc0391c3f8d092af500b0566a5f038 HEAD
git merge-base --is-ancestor c997df76b9a29a9f8d8be4a4ffd0875035762672 HEAD

cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

python -X utf8 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json
python -X utf8 deploy/p1/ui_test_inventory.py --check deploy/p1/ui_test_inventory.json
python -X utf8 deploy/p1/check_translations_audit.py
python -X utf8 deploy/p1/run_suites.py
node deploy/p1/verify_p1_round3_l23_l26_e2e.mjs
P1_REPORT_OUT=deploy/p1/evidence/http-e2e-delta-report.json python -X utf8 deploy/p1/verify_p1_http_e2e.py
```

Quyết định merge và ký GO P2 thuộc T0/Owner.
