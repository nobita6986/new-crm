# T1A — CSKH P1 UI & Bản Tích Hợp Cuối (5 màn hình P1 §9, Phân phối nền RQ, Đóng trọn vẹn L-23..L-29, Việt hóa 100% & E2E thực tế)

## Bối cảnh & nguồn gốc tích hợp cuối (Đợt 4 — Hiện hành)

* **Nguồn yêu cầu:** `docs/root-plan/P1.md` §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu).
* **Một mốc mã nguồn tích hợp cuối (`9fdbb8dfdd9ad443ffe05057a969a1e039473989`):** Hợp nhất trực tiếp từ Git giữa:
  * UI/docs Đợt 3: **`5c37bf3dc464a0d8e2b9bd94ba55df40da469559`** (`5c37bf3`);
  * Backend PR #4 (`codex/p1-foundation`): **`2235757b79bb9f1a7fb3ae2957b28deb088185b9`** (`2235757` — đóng `L-23` + `L-26` phía backend) và **`77e07dac7948c156039bc6a9746fe5d451fc0d71`** (`77e07da` — đóng `L-29` khóa quyền DocType/Document/`permlevel = 1`/`search_link` + direct ownership mutation eligibility).
* **Không nới quyền backend:**
  * **`0` dòng thay đổi** so với `77e07dac7948c156039bc6a9746fe5d451fc0d71` trên `permissions.py`, `p1_permissions.py` và toàn bộ `doctype/**/*.json` (`tabCustom DocPerm = 0 dòng`).
  * Giữ nguyên 100% chương 0–5 của T1B tại `77e07dac7948c156039bc6a9746fe5d451fc0d71` và gắn nhãn phân biệt rõ toàn bộ các mốc lịch sử Đợt 1–3 (`L-28`) trong `docs/architecture/P1-Exit-Report.md` (giữ nguyên văn tiếng Việt UTF-8 của **§7.9** khớp 100% `5c37bf3dc464a0d8e2b9bd94ba55df40da469559`).
* **Bảo toàn lịch sử & Provenance SHA baseline:**
  * **`0` dòng thay đổi** trên toàn bộ tệp bằng chứng lịch sử Đợt 1 (`screenshots/*.png`, `browser-manifest.json`, `http-e2e-report.json`), Đợt 2 (`async-distribution-delta-e2e.json` kèm đính chính `provenance_erratum` về commit thật **`0d3f2bcbbf9715a4c0c783903ce750de8646583d`**, `http-e2e-delta-report.json`) và Đợt 3 (`round3-l23-l26-delta-e2e.json`, `round3-http-e2e-report.json`).
  * Bằng chứng mới của Đợt 4 được xuất riêng tại `deploy/p1/evidence/round4-final-integration-e2e.json` và `deploy/p1/evidence/round4-http-e2e-report.json`.

## Phân biệt số liệu lịch sử (Đợt 1–3) và số liệu hiện hành (Đợt 4)

| Chỉ số đối soát | Đợt 1 — Lịch sử (`0d3f2bc` / `d0842cc`) | Đợt 2 — Lịch sử (`6351048` / `a75c093`) | Đợt 3 — Lịch sử (`3f07b9b` / `5c37bf3`) | **Đợt 4 — Hiện hành (`9fdbb8d`)** |
|---|---|---|---|---|
| Nguồn gốc backend PR #4 | `3025495` + `64b7486` | `c49f7f8` | `c997df7` (`L-25`) | **`2235757` + `77e07da` (`L-29`)** |
| `run_suites.py` (`15` module) | `297/297` (`184` P1 + `6` UI + `99` P0 + `8` transport) | `312/312` (`199` P1 + `6` UI + `99` P0 + `8` transport) | `321/321` (`203` P1 + `11` UI + `99` P0 + `8` transport) | **`327/327` (`208` P1 + `12` UI + `99` P0 + `8` transport)** |
| Cổng CI (`p1` / `ui` / `p0`) | `184` P1 / `6` UI local / `99` P0 | `199` P1 / `6` UI / `99` P0 | `203` P1 / `11` UI / `99` P0 | **`208` P1 / `12` UI / `99` P0 (`158/158` HTTP probe)** |
| Bản dịch `translations/vi.csv` | `577` dòng (`372` khai báo) | `587` dòng (`383` khai báo) | `588` dòng (`384` khai báo, `210` dùng) | **`613` dòng dịch (`614` dòng tệp; `409` khai báo, `210` dùng, `0` lỗi)** |

## Năm màn hình hoàn thiện (P1 §9) & Kiểm tra Leader / BOD / Staff sau `L-29`

| Màn / Hạng mục | Đường dẫn / Tệp | Nội dung hoàn thiện & kiểm chứng cuối |
|---|---|---|
| Trang chủ CSKH (`/cskh`) | `www/cskh/index.html` | Đồng bộ các thẻ điều hướng trên trang chủ theo đúng `cskh_navigation` của từng vai trò: ẩn thẻ `/cskh/import` với `Leader` & `BOD`, chỉ hiển thị `/cskh/my-customers` với `Staff` (khớp 100% `_shell.html`) |
| Trang 1 — Kho khách hàng (`L-23`, `L-25`, `L-26`) | `/cskh/pool` (`pool_ui.py`, `pool.js`) | Cô lập chéo đội theo quyền hiện tại (`L-23`) đồng nhất trên cả `pool_ui.list`, `customers.customer_pool` và `customers.pool_list`; lọc `Lô` (`batch`) qua `CSKH Import Batch Lead` (`L-25`); `400 INVALID_REQUEST` khi sai phân trang (`L-26`); `0` PII cho `BOD` |
| Trang 2 — Nhập danh sách (`L-25`, `L-26`) | `/cskh/import` (`import_files.py`, `import_history.py`, `import.js`) | Upload riêng tư CSV/XLSX → ánh xạ 3 cột có sanitize HTML/công thức → preview → confirm → tiến độ Bước 3, `Lịch sử nhập`, `Chạy lại công việc thất bại`, tải tệp lỗi chỉ cho `CSKH CRM Admin` (`200`) |
| Trang 3 — Chiến dịch & quy tắc (`L-24`, `L-26`, `L-29`) | `/cskh/campaigns` (`run_history.py`, `campaigns.js`) | `capacity = 0` = **`Không giới hạn`**; phân phối nền RQ + `L-24` giới hạn `assigned`/`skipped`/`preview`/counters/polling theo đội cho `Leader`; ẩn tức thì khối `Tra cứu đợt chạy` với `Leader` ngay từ khi khởi tạo, giữ `Tra cứu đợt chạy` (`distribution.get_run`) hoạt động chuẩn cho `BOD` & `Admin` dù các trường JSON thô của `CSKH Distribution Run` bị khóa `permlevel = 1` (`L-29`) |
| Trang 4 — Khách được giao (`L-26`, `L-29`) | `/cskh/my-customers` (`ownership_ui.py`, `my-customers.js`) | `Admin` chuyển giao/thu hồi (`assignees(campaign)` + kiểm tra trực tiếp ở `ownership.assign`/`reassign`); `Leader` xem phạm vi đội & mở `Chi tiết sở hữu` (`ownership.history`); `BOD` xem phạm vi toàn hệ thống `0` PII & mở `Chi tiết sở hữu`; `Staff` chỉ tải `customers.my_list` (không bắn bất kỳ request nền `403` nào) |
| Trang 5 — Bảng điều khiển phân phối (`L-29`) | `/cskh/dashboard` (`dashboard.js`) | Tách rõ **`Chủ sở hữu hiện tại`** (work item hoạt động) và **`Sự kiện phân công`** (audit trail) theo đúng phạm vi `Admin`/`BOD` (`all`) và `Leader A`/`Leader B` (`team`) |

## Trạng thái đóng trọn vẹn `L-23..L-29` (UI + Backend)

* **`L-23` (Cô lập chéo đội trên kho khách hàng):** Đã đóng trên cả `pool_ui.list` (UI) lẫn `customers.customer_pool` & `customers.pool_list` (Backend).
* **`L-24` (Cô lập lịch sử & live polling đợt chạy chiến dịch chung):** Đã đóng trên `run_history.runs` và `campaigns.js`.
* **`L-25` (Lưu `name`/`source` có sanitize + nối filter `Import Batch`):** Đã đóng trên `CSKH Import Batch Lead`, `reporting.py` và `pool_ui.py`.
* **`L-26` (Phân trang/limit trả `400 INVALID_REQUEST` ổn định + `assignees` & direct mutation eligibility):** Đã đóng trên **toàn bộ `8/8` endpoint** (`pool_ui.list`, `ownership_ui.assigned_list`, `import_history.history`, `run_history.runs`, `customers.my_list`, `customers.pool_list`, `customers.customer_pool`, `ownership.history`) cùng `ownership_ui.assignees(campaign)` và `ownership.assign`/`ownership.reassign`.
* **`L-28` (Gắn nhãn rõ số liệu lịch sử vs hiện hành trong `P1-Exit-Report.md`):** Đã gắn nhãn rõ toàn bộ các mục lịch sử Đợt 1–3 (§7.0–§7.10) và tổng hợp đối chiếu tại §7.11.
* **`L-29` (Khóa quyền DocType/Document/`permlevel = 1`/`search_link`):** Đã đóng tại `77e07dac7948c156039bc6a9746fe5d451fc0d71` và kiểm chứng thực tế trên toàn bộ màn hình `Leader` / `BOD` / `Staff` / `Admin` với **`0` nới quyền backend**.

## Kết quả kiểm chứng trên mã nguồn tích hợp `9fdbb8dfdd9ad443ffe05057a969a1e039473989`

* **Kiểm toán tĩnh & Cổng CI (`.github/workflows/p1.yml` & `.github/workflows/p0.yml`):**
  * `ruff==0.14.0 check` + `format --check`: **PASS** (`108` tệp Python chuẩn format).
  * `p1_test_inventory.py --check`: **PASS** (`208` method P1 trong `8` module; P0 core giữ nguyên `99` method).
  * `deploy/p1/ui_test_inventory.py --check`: **PASS** (`12` method trong `crm_cskh.tests.test_ui_p1_endpoints`).
  * `deploy/p1/check_translations_audit.py`: **PASS** (`used=210`, `declared=409`, `vi.csv=613 rows`, `missing=0`, `untranslated=0`, `orphan=0`).
* **Suite đơn vị & tích hợp (`deploy/p1/run_suites.py`):** **15/15 module PASS (`327/327` method)** — P1 `208/208` + UI `12/12` + P0 core `99/99` + transport `8/8`.
* **E2E Chromium + HTTP + RQ Đợt 4 (`deploy/p1/verify_p1_round4_final_e2e.mjs` → `deploy/p1/evidence/round4-final-integration-e2e.json`):** **4/4 nhóm lớn PASS** (`0` lỗi console/page/500, `0` background `403` ở Staff, `0` XSS, `0` lộ SĐT thô).
* **HTTP E2E đa vai trò Đợt 4 (`deploy/p1/verify_p1_http_e2e.py` → `deploy/p1/evidence/round4-http-e2e-report.json`):** **6/6 nhóm E2E PASS**.

## Cách dựng & tái hiện cho T1C

```bash
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 5c37bf3dc464a0d8e2b9bd94ba55df40da469559 HEAD
git merge-base --is-ancestor 2235757b79bb9f1a7fb3ae2957b28deb088185b9 HEAD
git merge-base --is-ancestor 77e07dac7948c156039bc6a9746fe5d451fc0d71 HEAD

cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

python -X utf8 deploy/dev/seed/p1_test_inventory.py --check deploy/dev/seed/p1_test_inventory.json
python -X utf8 deploy/p1/ui_test_inventory.py --check deploy/p1/ui_test_inventory.json
python -X utf8 deploy/p1/check_translations_audit.py
python -X utf8 deploy/p1/run_suites.py
node deploy/p1/verify_p1_round4_final_e2e.mjs
P1_REPORT_OUT=deploy/p1/evidence/round4-http-e2e-report.json python -X utf8 deploy/p1/verify_p1_http_e2e.py
```

Quyết định merge và ký GO P2 thuộc T0/Owner.
