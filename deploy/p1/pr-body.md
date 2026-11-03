# T1A — CSKH P1 UI & Đóng Gói Nghiệm Thu Cuối P1 (`14/14` Gate PASS, `29/29` Finding Đóng, `170/170` HTTP CI PASS)

## Tổng kết Nghiệm thu Tích hợp P1 & Phân tách Mốc Revision

* **Nguồn yêu cầu:** `docs/root-plan/P1.md` §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu), §14 (14 Gate).
* **Kết quả nghiệm thu kỹ thuật P1 (trình T0 / Owner quyết định `GO P2`):**
  * **`14/14` cổng nghiệm thu `G1-01 → G1-14` = `PASS`**, **`20/20` `T1-01 → T1-20` = `PASS`**, **`10/10` `SEC-01 → SEC-10` = `PASS`**, **`29/29` finding `L-01 → L-29` = `CLOSED`** (`0` blocker còn mở, `0` hồi quy P0).
  * Báo cáo **T1C** đã ký, artifact kiểm định cuối và các bằng chứng được tham chiếu đã được đóng gói **nguyên vẹn (`0` sửa đổi verdict)** tại:
    * `deploy/dev/evidence/t1c-p1-verification-report-round2-2026-11-02.md`
    * `deploy/dev/evidence/t1c-p1-round2-20261102/t1c-delta-65dd639.json`
    * `deploy/dev/evidence/t1c-p1-verification-baseline-2026-11-02.md` & `deploy/dev/evidence/t1c-p1-round2-20261102/*.json`
* **Phân tách rạch ròi các mốc trên PR #5 (`codex/p1-ui`):**
  1. **Mốc T1C kiểm định độc lập & ký `14/14` Gate PASS:** `65dd639c0a63011dee54bc2b973595dd69d7560a` (Code SHA `7bd7185a35680d4f716cfe0b0dc1d6b7bfb46cb9`, Backend PR #4 ancestor `6c457b327e59eead47cc9d0982c4ee1df4fe9e64`, Synthetic merge `d9cfd2c81d401f4278139fa556b71a9decb2a575`) — CI P1 `#41613509233` (`209/209` P1 + `12/12` UI + **`170/170` HTTP PASS**, Artifact `13220970780`) & CI P0 `#41613509298` (`99/99` P0 + probes PASS, Artifact `13220756000`).
  2. **Mốc Delta Refactor sau T1C đã được T0 rà:** `008bfd0ebefe77e47b2a71c9c863d15af4a22a74` (Code SHA **`a7cdd0f3da7f09c42c00e473e22b01cd857e24b3`**) — rút gọn `pool_ui.py` dùng chung 100% helper quyền & `reporting.customer_pool` của `crm_cskh.services.reporting` (`0` bản sao SQL cục bộ, `0` đổi hành vi nghiệp vụ/phân quyền) + mở rộng kiểm thử `totals` trước phân trang trên cả 3 API Kho (`pool_ui.list`, `customers.customer_pool`, `customers.pool_list`); CI P1 `#41649219699` (`209/209` P1 + `12/12` UI + **`170/170` HTTP PASS**) & CI P0 `#41649219609` (`99/99` P0 + probes PASS).
  3. **Commit đóng gói hồ sơ nghiệm thu cuối (trên nền `008bfd0`):** chỉ cập nhật hồ sơ/evidence, **giữ nguyên 100% code đã nghiệm thu `a7cdd0f3da7f09c42c00e473e22b01cd857e24b3`** (`0` diff trên `apps/`, `.github/`, `deploy/dev/seed/`, `deploy/p1/*.py`, `deploy/p1/*.mjs`).
* **Bảo toàn lịch sử & đính chính số HTTP CI (`170/170`):**
  * Trên nhánh tích hợp PR #5 (có `pool_ui.py`), `ci_p1_http_probe.py` thực thi **`170/170` HTTP cases PASS** (tăng thêm `3` case `pool_ui` so với `167/167` khi chạy nhánh Backend độc lập PR #4 chưa có `pool_ui.py`).
  * **`0` dòng thay đổi** trên toàn bộ tệp bằng chứng lịch sử Đợt 1 (`screenshots/*.png`, `browser-manifest.json`, `http-e2e-report.json`), Đợt 2 (`async-distribution-delta-e2e.json` kèm đính chính `provenance_erratum` về commit thật **`0d3f2bcbbf9715a4c0c783903ce750de8646583d`**, `http-e2e-delta-report.json`), Đợt 3 (`round3-l23-l26-delta-e2e.json`, `round3-http-e2e-report.json`) và Đợt 4 (`round4-final-integration-e2e.json`, `round4-http-e2e-report.json`), cũng như nguyên văn tiếng Việt UTF-8 của `P1-Exit-Report.md` **§7.9**.

## Phân biệt số liệu lịch sử (Đợt 1–3) và số liệu hiện hành (Đợt 4)

| Chỉ số đối soát | Đợt 1 — Lịch sử (`0d3f2bc` / `d0842cc`) | Đợt 2 — Lịch sử (`6351048` / `a75c093`) | Đợt 3 — Lịch sử (`3f07b9b` / `5c37bf3`) | **Đợt 4 — Hiện hành (`7bd7185` → `a7cdd0f`)** |
|---|---|---|---|---|
| Nguồn gốc backend PR #4 | `3025495` + `64b7486` | `c49f7f8` | `c997df7` (`L-25`) | **`2235757` + `77e07da` + `6c457b3` (`pool_list` + `L-29`)** |
| `run_suites.py` (`15` module) | `297/297` (`184` P1 + `6` UI + `99` P0 + `8` transport) | `312/312` (`199` P1 + `6` UI + `99` P0 + `8` transport) | `321/321` (`203` P1 + `11` UI + `99` P0 + `8` transport) | **`328/328` (`209` P1 + `12` UI + `99` P0 + `8` transport)** |
| Cổng CI (`p1` / `ui` / `p0`) | `184` P1 / `6` UI local / `99` P0 | `199` P1 / `6` UI / `99` P0 | `203` P1 / `11` UI / `99` P0 | **`209` P1 / `12` UI / `99` P0 (`170/170` HTTP probe PASS trên PR #5; `167/167` trên PR #4 độc lập)** |
| Bản dịch `translations/vi.csv` | `577` dòng (`372` khai báo) | `587` dòng (`383` khai báo) | `588` dòng (`384` khai báo, `210` dùng) | **`613` dòng dịch (`614` dòng tệp; `409` khai báo, `210` dùng, `0` lỗi)** |

## Năm màn hình hoàn thiện (P1 §9) & Kiểm tra Leader / BOD / Staff sau `L-29`

| Màn / Hạng mục | Đường dẫn / Tệp | Nội dung hoàn thiện & kiểm chứng cuối |
|---|---|---|
| Trang chủ CSKH (`/cskh`) | `www/cskh/index.html` | Đồng bộ các thẻ điều hướng trên trang chủ theo đúng `cskh_navigation` của từng vai trò: ẩn thẻ `/cskh/import` với `Leader` & `BOD`, chỉ hiển thị `/cskh/my-customers` với `Staff` (khớp 100% `_shell.html`) |
| Trang 1 — Kho khách hàng (`L-23`, `L-25`, `L-26`) | `/cskh/pool` (`pool_ui.py`, `pool.js`) | Cô lập chéo đội bằng SQL trước phân trang (`L-23`) đồng nhất trên cả `pool_ui.list`, `customers.customer_pool` và `customers.pool_list` (kể cả kịch bản `return_to_pool=True` ở Đội A rồi chuyển Đội B); lọc `Lô` (`batch`) qua `CSKH Import Batch Lead` (`L-25`); `400 INVALID_REQUEST` khi sai phân trang (`L-26`); `0` PII cho `BOD` |
| Trang 2 — Nhập danh sách (`L-25`, `L-26`) | `/cskh/import` (`import_files.py`, `import_history.py`, `import.js`) | Upload riêng tư CSV/XLSX → ánh xạ 3 cột có sanitize HTML/công thức → preview → confirm → tiến độ Bước 3, `Lịch sử nhập`, `Chạy lại công việc thất bại`, tải tệp lỗi chỉ cho `CSKH CRM Admin` (`200`) |
| Trang 3 — Chiến dịch & quy tắc (`L-24`, `L-26`, `L-29`) | `/cskh/campaigns` (`run_history.py`, `campaigns.js`) | `capacity = 0` = **`Không giới hạn`**; phân phối nền RQ + `L-24` giới hạn `assigned`/`skipped`/`preview`/counters/polling theo đội cho `Leader`; ẩn tức thì khối `Tra cứu đợt chạy` với `Leader` ngay từ khi khởi tạo, giữ `Tra cứu đợt chạy` (`distribution.get_run`) hoạt động chuẩn cho `BOD` & `Admin` dù các trường JSON thô của `CSKH Distribution Run` bị khóa `permlevel = 1` (`L-29`) |
| Trang 4 — Khách được giao (`L-26`, `L-29`) | `/cskh/my-customers` (`ownership_ui.py`, `my-customers.js`) | `Admin` chuyển giao/thu hồi (`assignees(campaign)` + kiểm tra trực tiếp ở `ownership.assign`/`reassign`); `Leader` xem phạm vi đội & mở `Chi tiết quyền phụ trách` (`ownership.history`); `BOD` xem phạm vi toàn hệ thống `0` PII & mở `Chi tiết quyền phụ trách`; `Staff` chỉ tải `customers.my_list` (không bắn bất kỳ request nền `403` nào) |
| Trang 5 — Bảng điều khiển phân phối (`L-29`) | `/cskh/dashboard` (`dashboard.js`) | Tách rõ **`Chủ sở hữu hiện tại`** (work item hoạt động) và **`Sự kiện phân công`** (audit trail) theo đúng phạm vi `Admin`/`BOD` (`all`) và `Leader A`/`Leader B` (`team`) |

## Trạng thái đóng trọn vẹn `29/29` Finding (`L-01 → L-29`) & Backlog Không Chặn (`O-01`, `O-02`)

* **`L-01 → L-29` (`29/29 CLOSED`):** Đã đóng đầy đủ trên cả Backend và UI, bao gồm `L-12` (5 màn hình `/cskh/*` & Việt hóa P1), `L-16..L-22` (envelope mã lỗi, scope Admin/Leader, import `>200` dòng, `parse_bool`, RQ async), `L-23` (SQL trước phân trang trên cả 3 API Kho), `L-24` (lịch sử & polling chiến dịch chung theo đội), `L-25` (`CSKH Import Batch Lead`), `L-26` (`parse_int`/`parse_pagination` trả `400 INVALID_REQUEST` trên đủ `8/8` endpoint + direct mutation eligibility chặn `campaign_doc.status == "Closed"`), `L-27` (cổng CI JUnit `209` P1 + `12` UI + `170` HTTP), `L-28` (nhất quán hồ sơ & nhãn lịch sử), và `L-29` (`p1_permissions.py` + `permlevel = 1` + `guarded_crm.py` + `enforce_p1_filter_field_permissions`).
* **Backlog kỹ thuật không chặn hậu P1 (`P1-Exit-Report.md` §6.4):**
  * **`O-01` (`TRIVIAL`):** Đồng bộ tên hàm thật (`enforce_p1_filter_field_permissions`, `parse_int`, `campaign_doc.status == "Closed"`) và số lượng HTTP probe CI tích hợp (`170/170`) trong văn xuôi hồ sơ — đã cập nhật ngay trong đợt đóng gói này.
  * **`O-02` (`LOW`):** Nếu ở P2 mở giao diện tạo đa version cho `CSKH Assignment Rule`, đồng bộ `ownership_ui._campaign_ineligible_users` lọc theo `latest_by_user` giống hệt `services/ownership._require_campaign_target_eligibility` (ở P1 tầng mutation backend đã khóa cứng theo `latest_version`, `0` rủi ro vượt quyền/sức chứa).

## Cách dựng & tái hiện

```bash
git fetch origin codex/p1-foundation:refs/remotes/origin/codex/p1-foundation codex/p1-ui:refs/remotes/origin/codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 5c37bf3dc464a0d8e2b9bd94ba55df40da469559 HEAD
git merge-base --is-ancestor 2235757b79bb9f1a7fb3ae2957b28deb088185b9 HEAD
git merge-base --is-ancestor 77e07dac7948c156039bc6a9746fe5d451fc0d71 HEAD
git merge-base --is-ancestor 6c457b327e59eead47cc9d0982c4ee1df4fe9e64 HEAD

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
