# P2 Exit Report — CRM-CSKH Giai đoạn P2 (Hồ sơ Tích hợp Backend `WP00–WP06` + Giao diện `WP07–WP09` + Kiểm chứng `WP10` & Đóng gói `WP11`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** [`docs/P2-Exit-Report.md`](./P2-Exit-Report.md)
> **Kế hoạch gốc:** [`docs/root-plan/P2.md`](./root-plan/P2.md) (§14, §15, §16, §17, §18) & ADR giao diện [`docs/architecture/adr-ui-001.md`](./architecture/adr-ui-001.md), [`docs/architecture/adr-p1-06-ui-integration.md`](./architecture/adr-p1-06-ui-integration.md)
> **Ngày đóng gói hồ sơ nghiệm thu:** 04/11/2026
> **Thực hiện bởi:**
> - **T1B (AI Coding — Backend):** `P2-WP00..WP06`, phần Backend/CI/Benchmark/Restore của `P2-WP10` (`6ef2e2e2` → `580a45f0`) & bản sửa khắc phục Backend Đợt 2 cho `F-P2-01/02/04/05/06` + `OBS-P2-01` (commit `16e69438a30deaa8de723594e3beaea92e91fecd`, `apps/` tree `3c3a8a6e8c29cfca49e8628ba104bec1dd2aec99`, nhánh `codex/p2-foundation` / PR #6)
> - **T1A (AI Coding — UI, i18n, Browser E2E & WP11):** `P2-WP07..WP09`, bản sửa khắc phục UI Đợt 2 cho `F-P2-04A`/`F-P2-05`/`F-P2-01`, E2E HTTP/Chromium thực tế (Source & E2E code commit `0f3c7311c75aa3c49c3953a9b8cd64e45809d008`) & đóng gói tích hợp `P2-WP11` (mốc nghiệm thu cố định `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4`, `apps/` tree `f3b6db3ff80b3371ea5b991d7e5aaa408533007b`, nhánh `codex/p2-ui`, PR #7)
> - **T1C (Kiểm định Độc lập Bảo mật, Chất lượng & Runtime):** Thực thi kiểm định độc lập toàn trình & delta trên `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` (`crm-cskh-t1c-p2` / `t1cp2.localhost:18320`, bộ hồ sơ ký tại [`deploy/dev/evidence/p2-t1c/`](../deploy/dev/evidence/p2-t1c/))
> **Baseline xuất phát (`main`):** `1913d2bd5ebfdc894eaa492b46be502dbdf44808` (CI P0 `#41658199683` `success`, CI P1 `#41658199861` `success`)
> **Mốc nghiệm thu kỹ thuật cố định đã được T1C xác nhận:** `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` (`apps/` tree `f3b6db3ff80b3371ea5b991d7e5aaa408533007b`, CI merge `634606710626aa389b1a5e2af846ea03ff91b421`: P0 `#41784039800`, P1 `#41784039850`, P2 `#41784039795` đều `success`)
> **Kết luận kiểm định độc lập của T1C:** **`G2-01..G2-15 = 15/15 PASS` · `T2-01..24 = 24/24 PASS` · `SEC2-01..16 = 16/16 PASS` · `F-P2-01..06 = 6/6 CLOSED` · `OBS-P2-01 = RESOLVED` (`OBS-P2-02`, `OBS-P2-03` giữ ghi chú phạm vi & backlog P3) → T1C đề nghị `RECOMMEND GO P3`**
> **Quyết định điều hành (`T0 / Owner`):** **CHƯA KÝ `GO P3` — PR #7 ĐANG `OPEN`, CHƯA MERGE VÀO `main`** *(tách biệt nghiêm ngặt phán quyết kiểm định kỹ thuật của T1C với quyết định điều hành của T0/Owner)*

---

## Phán quyết Kiểm định Độc lập của T1C, Phê duyệt Cổng P2 → P3 & Truy vết SHA

| Hạng mục quản trị | Tài liệu / Đầu mối liên kết thực tế trong repo | Trạng thái hiện tại |
|---|---|---|
| **Bộ hồ sơ kiểm định độc lập đã ký của T1C (`9` tệp nguyên bản, `0` biên tập)** | [`deploy/dev/evidence/p2-t1c/t1c-p2-verification-report.md`](../deploy/dev/evidence/p2-t1c/t1c-p2-verification-report.md), [`security-matrix-p2.md`](../deploy/dev/evidence/p2-t1c/security-matrix-p2.md), [`test-evidence-p2.md`](../deploy/dev/evidence/p2-t1c/test-evidence-p2.md), [`manifest.json`](../deploy/dev/evidence/p2-t1c/manifest.json), [`SHA256SUMS.txt`](../deploy/dev/evidence/p2-t1c/SHA256SUMS.txt) + `4` JSON artifacts: [`t1c-p2-entry-baseline-1913d2b.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-entry-baseline-1913d2b.json), [`t1c-p2-runtime-26b0237.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-runtime-26b0237.json), [`t1c-p2-ffe74df-audit.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-ffe74df-audit.json), [`t1c-p2-delta-runtime-7e789eb.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-delta-runtime-7e789eb.json) (`sha256:232e4b0e75c5c6ae95325489d7ed708739e1a57e92970d26c47d8f39253015a4`) | Đã xác minh độc lập trên `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4`: **`15/15` cổng `PASS`**, **`24/24` `T2` `PASS`**, **`16/16` `SEC2` `PASS`**, **`6/6` finding `CLOSED`**, `OBS-P2-01 RESOLVED`; **T1C đề nghị `GO P3`** |
| **Truy vết bảo mật & bằng chứng kỹ thuật (`T1B + T1A`)** | [`docs/security-matrix-p2.md`](./security-matrix-p2.md), [`docs/test-evidence-p2.md`](./test-evidence-p2.md), [`deploy/p2/evidence/http-e2e-report.json`](../deploy/p2/evidence/http-e2e-report.json), [`deploy/p2/evidence/browser-manifest.json`](../deploy/p2/evidence/browser-manifest.json) | Ghim đúng vào Source SHA thực chạy `0f3c7311c75aa3c49c3953a9b8cd64e45809d008` và mốc nghiệm thu `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` (`apps` tree `f3b6db3ff80b3371ea5b991d7e5aaa408533007b` không đổi): `6/6` cụm HTTP E2E `PASS` + `22/22` kịch bản Chromium `B2E-01..B2E-22` `PASS` (`0` SĐT thô, `0` secret) |
| **Quyết định điều hành chuyển pha (`GO P3` / `NO-GO P3`)** | Thẩm quyền phê duyệt điều hành của **T0 / Owner** trên cơ sở đề nghị `RECOMMEND GO P3` của **T1C** | **CHƯA KÝ QUYẾT ĐỊNH ĐIỀU HÀNH `GO P3` (CHỜ T0 / OWNER PHÊ DUYỆT & MERGE)** |

### Bảng Truy vết Revision Thực tế (`Runtime SHA`, `Verified Milestone SHA`, `Final Docs Packaging SHA`, `App Tree SHA` & `CI Merge SHA`)

| Mốc revision | Commit SHA / Tree SHA thực tế | Nội dung & ý nghĩa kiểm chứng |
|---|---|---|
| **Mốc 0 — Baseline `main` nghiệm thu P0/P1** | `1913d2bd5ebfdc894eaa492b46be502dbdf44808` | Điểm xuất phát của `codex/p2-foundation` và `codex/p2-ui`; Entry Gate `G2-01` (`E2-01..E2-10`) được T1C xác minh `PASS` ([`t1c-p2-entry-baseline-1913d2b.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-entry-baseline-1913d2b.json)). |
| **Mốc 1 — Tích hợp P2 Đợt 1 & 1b (`PR #7` lịch sử)** | `6ef2e2e2` / `580a45f0` + `d3da46b1` → `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` → `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10`<br>(`apps` tree: `4a395ae619f95a1feb0fbf9ec189063af2938f76`; CI merge `6b27fa38` → `faa6dfb3`) | T1C đo toàn trình runtime Đợt 1 (`9/15 PASS`, `6/15 FAIL` tại [`t1c-p2-runtime-26b0237.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-runtime-26b0237.json)) và đóng `F-P2-03` trên `ffe74df` (`61/61` HTTP assertions tại [`t1c-p2-ffe74df-audit.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-ffe74df-audit.json)). |
| **Mốc 2a — Backend P2 Remediation (`T1B`, PR #6)** | `16e69438a30deaa8de723594e3beaea92e91fecd`<br>(`apps` tree: `3c3a8a6e8c29cfca49e8628ba104bec1dd2aec99`) | Khắc phục phần Backend của `F-P2-01`, `F-P2-02`, `F-P2-04A/04B`, `F-P2-05`, `F-P2-06` + `OBS-P2-01`, thêm `test_p2_t1c_regressions.py` (`5/5`) và nâng `ci_p2_http_probe.py` lên `81/81` assertions (CI PR #6: P0 `#41779348055`, P1 `#41779348111`, P2 `#41779347989` `success`). |
| **Mốc 2b — Source & Live E2E Runtime SHA (`T1A + T1B`)** | **`0f3c7311c75aa3c49c3953a9b8cd64e45809d008`**<br>(`HEAD^1 = ffe74df2`, `HEAD^2 = 16e69438`; `apps` tree: **`f3b6db3ff80b3371ea5b991d7e5aaa408533007b`**) | Hợp nhất trực tiếp `16e69438` với bản sửa UI `F-P2-04A`/`F-P2-05`/`F-P2-01` + `O-02`; commit thực chạy `verify_p2_http_e2e.py` (`6/6 PASS`) & `verify_p2_browser_e2e.mjs` (`22/22 PASS`, ảnh `01..22`). |
| **Mốc 2c — Mốc Nghiệm thu Cố định đã được T1C kiểm định (`PR #7`)** | **`7e789eb1137fd8ee6c07a1971bf6b3151287b8e4`**<br>(`apps` tree: **`f3b6db3ff80b3371ea5b991d7e5aaa408533007b`**, `0` byte diff `apps/` vs `0f3c731`)<br>`CI Merge SHA`: **`634606710626aa389b1a5e2af846ea03ff91b421`** (`1913d2b` + `7e789eb`) | Mốc được **T1C** kiểm định độc lập toàn diện (`195/195` file `crm_cskh` khớp SHA-256 `13fa30f831c4d149602e3aa09e89884a54b44415fa2cbad8b4b177b11f42b7d9`, `migrate × 2` trên dữ liệu `ffe74df`, [`t1c-p2-delta-runtime-7e789eb.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-delta-runtime-7e789eb.json), CI P0 `#41784039800` / P1 `#41784039850` / P2 `#41784039795` `success`) → **`15/15 PASS`, `6/6` finding `CLOSED`**. |
| **Mốc 2d — Commit Đóng gói Hồ sơ T1C Cuối cùng (`HEAD` docs-only trên `PR #7`)** | Commit `HEAD` ngay trên `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` (`HEAD:apps == 7e789eb1:apps == f3b6db3ff80b3371ea5b991d7e5aaa408533007b`) | **Chỉ cập nhật `deploy/dev/evidence/p2-t1c/*` (nguyên bản từ T1C) và `3` tệp tài liệu `docs/{P2-Exit-Report.md,test-evidence-p2.md,security-matrix-p2.md}`**; `git diff 7e789eb1..HEAD -- apps/ deploy/dev/seed/ deploy/p2/ .github/workflows/` = **`0` byte** (không ghi đè SHA đã chạy runtime bằng SHA docs-only; SHA đóng gói cuối & CI trên `HEAD` cuối được ghi trên mô tả PR #7 và biên bản bàn giao). |

---

## Sổ cái Khắc phục & Đóng Toàn bộ `6/6` Finding (`F-P2-01 .. F-P2-06` = `6/6 CLOSED`) + `OBS-P2-01 .. OBS-P2-03`

| Mã Finding / Ghi nhận | Giải pháp Kỹ thuật Thực tế trên Đĩa (`16e69438` + `0f3c731` → `7e789eb`) | Bằng chứng Kiểm thử & Xác minh Độc lập của T1C | Trạng thái T1C |
|---|---|---|---|
| **`F-P2-01`** (`HIGH` — `G2-12`, `G2-13`, `G2-15`) | Tách `reason` trung tính (`permlevel=0`), `private_reason` (`permlevel=1`), `payload_hash` (`permlevel=1, hidden=1`) trên `cskh_assignment_history.json`; không chép `note` khách khi auto-close DNC (`services/contact_attempt.py`); migration `migrate_p2_assignment_history_privacy()` trong `patches/v2_0/p2_schema_and_seeds.py`; khóa `BOD` trên cả 5+ mặt phẳng qua `p1_permissions.py` (`PERMLEVEL_PROTECTED_FIELDS`), `api/guarded_crm.py`, `api/guarded_reporting.py`, `api/ownership.py`; UI `public/cskh/work-item.js` (`formatMilestoneReason`) hiển thị sạch `0` `[H:<16hex>]` và không dùng ẩn DOM thay backend. | `TestP2T1cRegressions.test_f_p2_01_*`, CI HTTP `p2-http-f01-*`, Browser `B2E-12` & `B2E-22`; T1C runtime `02_f_p2_01_privacy_and_migration = PASS` (`migrate × 2` làm sạch `39/39` `[H:...]` & `40/40` SĐT, `0` rò sang `BOD`, `12/12` oracles trả `403`). | **`CLOSED`** |
| **`F-P2-02`** (`MEDIUM` — `G2-03`, `G2-15`) | `api/run_history.py::runs` bảo toàn số lượng phân bổ lịch sử theo đợt chạy (`allocation_semantics = "historical_run_allocation"`) qua `in_progress`, `on_hold`, `closed`, `reassign`, `revoke`, đồng thời trả `active_owned` / `active_owned_total` theo `OWNED_ACTIVE_WORK_ITEM_STATES` và giữ chặt phạm vi đội `L-24`. | `TestP2T1cRegressions.test_f_p2_02_*`, CI HTTP `p2-http-f02-*`, `P2-E2E-05`; T1C runtime `04_f_p2_02_run_history = PASS` (bảo toàn `{a1: 3, a2: 1}` & `active_owned = {a1: 2}`, cô lập chiến dịch dùng chung `0` lộ chéo đội). | **`CLOSED`** |
| **`F-P2-03`** (`MEDIUM` — `G2-15`) | `deploy/dev/seed/ci_p2_http_probe.py` bổ sung `check()` + hậu kiểm SQL cho `p2-http-staff-b-close-wi-final`, tách bạch `counts`, gác bằng `p2-http-completeness-gate.json` và `9/9` kiểm chứng âm trong `deploy/dev/seed/test_ci_p2_http_probe.py`. | Đóng từ `ffe74df` (`61/61`) và bảo toàn nâng cấp trên `7e789eb` (`CI #41784039795`: **`81/81` HTTP assertions `PASS`**, `0` unasserted). | **`CLOSED`** |
| **`F-P2-04`** (`04A` & `04B` · `MEDIUM` — `G2-03`, `G2-13`, `G2-15`) | `services/read_model.py::list_work_items` & `api/workflow.py` lọc SQL `care_state` trước `COUNT(*)` và `LIMIT/OFFSET`; bổ sung `CSKH Work Item.owner_at_close` để lọc `closed` theo `owner_at_close` vs `closed_by` (thu hồi ngay khi Lead sang đội khác — `L-23`). UI `public/cskh/my-customers.js` + `public/cskh/p2-api.js` bỏ lọc 20 dòng client-side, dùng `serverTotal`, reset trang `1` khi đổi filter/search/campaign, thêm `#mine-scope-care-filter` (`Hoàn tất`). | `TestP2T1cRegressions.test_f_p2_04_*`, Node `test_p2_ui_timezone_and_contracts.mjs`, Browser `B2E-18..20`; T1C runtime `05_f_p2_04_care_state_and_closed_filtering = PASS` (duyệt đủ `11/11` `care_state` nhiều trang khớp `100%` `COUNT(*)`, tìm thấy `7mikecehjd` trên `connected` `5` trang, thu hồi `403` cross-team). | **`CLOSED`** |
| **`F-P2-05`** (`MEDIUM` — `G2-14`, `G2-15`) | `services/read_model.py::progress_summary` chuẩn hóa `date_to` dạng ngày sang biên nửa mở `[start_of_day, start_of_next_day)` (`exclusive`) theo `Asia/Ho_Chi_Minh`, chặn ngày sai (`400`), bổ sung `cohort_work_items`, `attempted_work_items`, `unattempted_work_items`, `connected_work_items`, `dnc_leads`, `dnc_work_items`. UI `www/cskh/progress.html` & `public/cskh/progress.js` tách rõ độ bao phủ `Work Item`, số `Lần liên hệ hiệu lực`, và thẻ `Số khách hàng ngừng liên hệ (DNC)` + `#prog-tz-badge`. | `TestP2T1cRegressions.test_f_p2_05_*`, Browser `B2E-12` & `B2E-21`; T1C runtime `06_f_p2_05_progress_summary = PASS` (cùng ngày `2026-10-15` bao trọn `00:00:00`..`23:59:59`, loại trừ `2026-10-14 23:59:59` & `2026-10-16 00:00:00`, tách `dnc_leads=24` vs `dnc_work_items=27`, khớp overlay `Correction`). | **`CLOSED`** |
| **`F-P2-06`** (`HIGH` — `G2-04`, `G2-12`, `G2-15`) | Đặt `read_only=1` cho 4 trường `cskh_dnc_*` trên `CRM Lead` (`fixtures/custom_field.json`); khóa bằng `_CSKH_AUTHORIZED_LEAD_DNC_MUTATION` + `p2_models.validate_crm_lead_dnc_fields` + `p2_models.enforce_lead_dnc_request_guard` (`auth_hooks`) + `guarded_crm.client_set_value`/`client_bulk_update`; ghi `Comment` kiểm toán khi Admin đặt/gỡ DNC qua `settings.set_lead_dnc` hoặc `reopen_work_item(override_dnc=True)`. | `TestP2T1cRegressions.test_f_p2_06_*`, CI HTTP `p2-http-f06-*`; T1C runtime `03_f_p2_06_dnc_lockdown_and_lead_writes = PASS` (**`75/75` vector generic trả `403`** trên cả `Staff`/`Leader`/`BOD`/`CRM Admin`/`Administrator`, sửa Lead thường `8/8 HTTP 200`, kênh DNC có audit `200`). | **`CLOSED`** |
| **`OBS-P2-01`** *(Ghi nhận đua giao dịch)* | `api/ownership.py` bọc `_run_with_tx_retry` cho `assign`, `reassign`, `revoke` đồng nhất với `api/workflow.py`. | T1C runtime `07D`: `3/3` lượt đua HTTP `Barrier(2)` giữa `workflow.record_attempt` & `ownership.reassign` đạt `0` HTTP `500`/`400`. | **`RESOLVED`** |
| **`OBS-P2-02` & `OBS-P2-03`** *(Ghi nhận phạm vi & backlog P3)* | `OBS-P2-02`: `p2-benchmark-20261103.json` đo ở tầng Python service (`bench execute` + `ThreadPoolExecutor(25)`, không gọi là tải HTTP); `progress_summary` toàn kỳ `50k` attempts của `BOD` (`~2,52s`) chuyển giao tối ưu bảng tổng hợp/chỉ mục sang P3.<br>`OBS-P2-03`: `File.owner == user` theo baseline Frappe v15/P0–P1 (`0` sửa core upstream) — xem chi tiết Mục 7 & Mục 12.2. | Đã ghi nhận đúng phạm vi thực tế và đưa vào backlog/đầu vào kiến trúc P3. | **Giữ ghi chú phạm vi & backlog P3** |

---

## Tóm tắt Điều hành & Phạm vi Tích hợp Thực tế (`16e69438` → `0f3c731` / `7e789eb`)

Trên nhánh tích hợp `codex/p2-ui` (hợp nhất có kiểm soát Backend `16e69438a30deaa8de723594e3beaea92e91fecd` của **T1B** với Giao diện/Việt hóa/E2E `0f3c7311c75aa3c49c3953a9b8cd64e45809d008` → `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` của **T1A**), phạm vi khác biệt trong `apps/` giữa nhánh Backend và nhánh tích hợp được xác định minh bạch như sau:
- **Các module backend giữ nguyên `0` dòng diff so với `16e69438`:** toàn bộ `permissions.py`, `p1_permissions.py`, `p2_permissions.py`, `p1_models.py`, `p2_models.py`, `services/*`, `crm_cskh/doctype/**`, `fixtures/custom_field.json`, `patches/**`, `tests/test_p2_*.py` và mọi endpoint trong `api/` ngoại trừ `api/ownership_ui.py`.
- **Ngoại lệ có chủ đích trong tầng API & hook (`diff 16e69438..7e789eb -- apps/`):**
  1. [`apps/crm_cskh/crm_cskh/api/ownership_ui.py`](../apps/crm_cskh/crm_cskh/api/ownership_ui.py) (`+21 / -24` dòng):
     - **Mở rộng tập trạng thái đang sở hữu (`active states`):** đổi điều kiện SQL từ `status = 'assigned'` sang `status in ('assigned', 'in_progress', 'on_hold')` trong cả truy vấn đếm tải `_campaign_ineligible_users` lẫn danh sách `assigned_list` (trả thêm `status` và `work_item_status`), giúp khách đang chăm sóc/hẹn lại không biến mất khỏi màn quản trị sở hữu và được tính đúng vào tải chiến dịch.
     - **Sửa điều kiện nhận khách theo phiên bản rule mới nhất của từng user (`rule eligibility` / `O-02`):** trong `_campaign_ineligible_users(campaign_name, allowed_nodes)`, thay cách lọc `max_version` toàn chiến dịch bằng gom nhóm `by_user` và tính `latest_version` riêng trên tập rule của từng `user`, rồi kiểm tra `enabled`, `weight > 0`, `team in allowed_nodes` và `capacity_limit` trên `enabled_latest` — được chứng minh bằng test `TestUiP2PagesAndContext.test_o02_ownership_ui_assignees_respects_per_user_latest_rule_version` tại [`test_ui_p2_pages_and_context.py`](../apps/crm_cskh/crm_cskh/tests/test_ui_p2_pages_and_context.py).
     - **Các helper thực tế đang dùng trong `ownership_ui.py`:** `assigned_list` dùng `reporting._scope(actor)` và `reporting._scope_users(scope)` (kèm `parse_pagination`, `parse_optional_str` từ `api._ui_params`); phần kiểm tra vai trò và lọc `assignees(campaign)` dùng các hàm nội bộ ngay trong module (`_roles`, `_is_system`, `_require_read`, `_can_mutate`, `_require_admin`, `_campaign_team_scope`, `_campaign_ineligible_users`), **chưa** gộp sang helper tập trung của `services/eligibility.py` hay `services/ownership.py`.
  2. [`apps/crm_cskh/crm_cskh/hooks.py`](../apps/crm_cskh/crm_cskh/hooks.py) (`+4 / -1` dòng) & [`crm_form_script.json`](../apps/crm_cskh/crm_cskh/fixtures/crm_form_script.json): chỉ bổ sung tên `"CSKH - Open Lead CSKH Workspace"` vào bộ lọc fixture `CRM Form Script`.

Các kết quả kỹ thuật cốt lõi đã hoàn tất từ tầng dữ liệu đến trải nghiệm trình duyệt tiếng Việt:

1. **Bảo toàn khóa phiên bản & chiến lược giao diện cùng Frappe site `/cskh/*`:** Giữ nguyên Frappe Framework `v15.123.1` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`) và Frappe CRM `v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`); **`0` tệp vá core/upstream mới** trong P2, không dựng SPA tách rời và không sửa `Activities.vue` của upstream. Kết nối từ trang chi tiết `CRM Lead` sang hồ sơ CSKH dùng điểm mở rộng chuẩn `CRM Form Script` (`"CSKH - Open Lead CSKH Workspace"` → `/cskh/work-item?lead=<lead_id>`).
2. **Mô hình sự kiện liên hệ bất biến, trung thực & giao diện thao tác nguyên tử:**
   - **Backend (`WP00–WP06` + Đợt 2):** `CSKH Outcome Code` (`12` mã tiếng Việt chuẩn), `CSKH Policy` (`Campaign > Team > Global`), `CSKH Contact Attempt` (append-only, snapshot phiên bản Outcome + Policy + khoảng sở hữu), `CSKH Attempt Correction` (`correct | void | restore`), mở rộng trực tiếp `CSKH Work Item` (kèm `owner_at_close`) + `CSKH Assignment History` (tách `reason` trung tính / `private_reason` `permlevel=1` / `payload_hash` `permlevel=1, hidden=1`) + `CRM Lead` (`DNC` khóa `read_only=1` & `validate_crm_lead_dnc_fields` + `enforce_lead_dnc_request_guard`) + `CRM Task` (`callback`).
   - **Giao diện `/cskh/*` (`WP07–WP09` + Đợt 2):** Nâng cấp `/cskh/my-customers` (lọc `care_state` và `status` hoàn toàn ở phía server với `total` thực từ DB cho cả nhiều trang + bảng phạm vi quản lý hỗ trợ lọc `Hoàn tất` theo nhân viên; form ghi kết quả nhanh gọi **đúng `1` POST orchestration** `workflow.record_attempt`), `/cskh/work-item` (dòng thời gian hợp nhất phân biệt rõ `Attempt` / `CRM Call Log` / `CRM Task` / `FCRM Note`, hiển thị `Nhân viên tự báo cáo`, không lộ `[H:<16hex>]`, không có nút sửa/xóa trực tiếp Attempt, xử lý rõ `409 STALE_WORK_ITEM` & `OWNERSHIP_CHANGED`), `/cskh/callbacks` (lịch hẹn theo biên múi giờ `23:59`/`00:00` site/user), `/cskh/progress` (hiển thị tách biệt số `Work Item` đã thử / chưa thử / đã thử chưa kết nối / đã kết nối, số `Lần liên hệ hiệu lực`, và `Số khách hàng ngừng liên hệ (DNC)`, đúng biên ngày `date_from == date_to` kể cả khi trình duyệt khác múi giờ site, `BOD` `0` PII), và `/cskh/settings` (cấu hình Outcome Code & Policy theo `capabilities` từ server).
3. **Kiểm chứng thực nghiệm đa tầng & xác nhận độc lập `15/15 PASS` của T1C:**
   - **`375/375` Frappe JUnit test method trên `24` module** (`P2 Backend` `38/38` trên `8` module gồm `test_p2_t1c_regressions` `5/5`, `P2 UI` `9/9`, `P1 Backend` `209/209`, `P1 UI` `12/12`, `P0 Core+Aux` `107/107`; *lịch sử Đợt 1 trên `ffe74df` là `369/369` gồm `33/33` + `8/8`*), `72/72` AST guard P0, `6/6` Vite bootstrap test, Node timezone/contract unit suite (`PASS`), và `9/9` kiểm chứng âm cổng đo HTTP [`test_ci_p2_http_probe.py`](../deploy/dev/seed/test_ci_p2_http_probe.py).
   - **CI HTTP Transport Probe (`ci_p2_http_probe.py` trên `7e789eb` — CI `#41784039795`):** **`total_records = 81`** (`14` HTTP record đăng nhập/CSRF có `check()` + `67` HTTP record kiểm chứng nghiệp vụ/bảo mật/khắc phục P2; `verification_records = 81`, `setup_observation_records = 0`, `asserted_cases = 81`, `passed_assertions = 81`, `unasserted_cases = 0`, khóa cứng bằng `p2-http-completeness-gate.json`; *lịch sử: snapshot cục bộ `03/11/2026` có `61`/`60`, Đợt 1b `ffe74df` có `61`/`61`*).
   - **Live HTTP E2E (`verify_p2_http_e2e.py`) & Real-Chromium Browser E2E (`verify_p2_browser_e2e.mjs`) trên `0f3c7311c75aa3c49c3953a9b8cd64e45809d008` (`apps` tree `f3b6db3ff80b3371ea5b991d7e5aaa408533007b`):** **`6/6` cụm kiểm tra HTTP E2E `PASS`** & **`22/22` kịch bản Chromium `B2E-01..B2E-22` `PASS`**; cộng thêm runtime độc lập của **T1C** trên `t1cp2.localhost:18320` ([`t1c-p2-delta-runtime-7e789eb.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-delta-runtime-7e789eb.json)) xác nhận đóng `6/6` finding.

---

## Trả lời Đầy đủ `12` Câu hỏi Nghiệm thu Bắt buộc (`P2.md` §18.3)

### 1. Tái sử dụng trực tiếp bao nhiêu thành phần upstream, chỉnh sửa thực tế bao nhiêu file upstream, và lý do kiến trúc

- **Số file mã nguồn upstream (`apps/frappe`, `apps/crm`, `deploy/dev/patches/*`) bị chỉnh sửa thêm trong P2 (cả Backend lẫn UI):** **`0` file**. Toàn bộ P2 được triển khai 100% bên trong custom app `apps/crm_cskh` thông qua cơ chế chính thống của Frappe (`DocType`, `Custom Field`, `CRM Form Script` fixture, `www/cskh/*` website routes, `patches.txt`, `hooks.py`).
- **Các thực thể & cơ chế upstream được REUSE / EXTEND trực tiếp (xem chi tiết tại [`docs/P2-entry-report.md`](./P2-entry-report.md) §3):**
  1. `CRM Lead` (`crm/fcrm/doctype/crm_lead/crm_lead.json`) — **EXTEND** bằng `4` Custom Field tuân thủ `cskh_do_not_contact`, `cskh_dnc_reason`, `cskh_dnc_at`, `cskh_dnc_work_item` (`read_only=1` + khóa ghi trực tiếp qua `p2_models.validate_crm_lead_dnc_fields` & `p2_models.enforce_lead_dnc_request_guard`); không sửa `CRMLead` controller và không tự động ghi đè `CRM Lead.status`.
  2. `CRM Task` (`crm/fcrm/doctype/crm_task/crm_task.json`) — **EXTEND** bằng `7` Custom Field `cskh_*` làm tác vụ hẹn chăm sóc lại (callback); không tạo DocType `Followup Task` trùng lặp.
  3. `CRM Call Log` (`crm/fcrm/doctype/crm_call_log/crm_call_log.json`) — **REUSE trực tiếp** qua [`services/call_log_adapter.py`](../apps/crm_cskh/crm_cskh/services/call_log_adapter.py) với liên kết 1–1 tùy chọn từ `CSKH Contact Attempt.call_log` (`UNIQUE`).
  4. `FCRM Note` (`crm/fcrm/doctype/fcrm_note/fcrm_note.json`) — **REUSE trực tiếp** làm ghi chú nội bộ trên hồ sơ Lead, hiển thị tách biệt trên timeline `/cskh/work-item` với nhãn rõ `Ghi chú nội bộ — không tính vào số lần liên hệ`.
  5. `CRM Form Script` (`crm/fcrm/doctype/crm_form_script`) — **REUSE điểm mở rộng UI sạch của Frappe CRM** thông qua fixture [`crm_form_script.json`](../apps/crm_cskh/crm_cskh/fixtures/crm_form_script.json) (`"CSKH - Open Lead CSKH Workspace"`) gắn nút `"Mở hồ sơ CSKH"` trên màn chi tiết `CRM Lead` điều hướng sang `/cskh/work-item?lead=<lead_id>` mà không sửa `Activities.vue` hay `Lead.vue`.
  6. `CRM Sales Hierarchy`, `ToDo`, `DocShare`, `CRM Notification` — **REUSE trực tiếp** cùng hệ thống sở hữu `services/ownership.py` và phân quyền P0/P1.
- **Lý do tạo mới đúng `4` DocType trong `crm_cskh` (`CSKH Outcome Code`, `CSKH Policy`, `CSKH Contact Attempt`, `CSKH Attempt Correction`):** Frappe CRM `v1.87.0` chỉ có `CRM Call Log` (bắt buộc SĐT `from`/`to` cho kênh thoại, cho phép sửa trực tiếp, không có từ điển kết quả đa kênh hay phiên bản chính sách) và `FCRM Note` (văn bản tự do không cấu trúc). Bốn DocType P2 giải quyết đúng khoảng trống nghiệp vụ đa kênh, bất biến và kiểm toán hiệu chỉnh.

---

### 2. Tác động lên dữ liệu & migration P1; chứng minh tính lặp lại và khôi phục (Backup/Restore)

- **Không phá vỡ dữ liệu P1:**
  - Mọi cột mới trên `CSKH Work Item` (gồm `owner_at_close`) và `CSKH Assignment History` (`private_reason`, `payload_hash`) đều nullable hoặc có giá trị mặc định an toàn (`revision=1`, `attempt_count=0`, `connected_attempt_count=0`, `policy_version=1`).
  - Các trạng thái P1 cũ (`queued`, `assigned`, `revoked`, `closed`) giữ nguyên tên và ý nghĩa; chỉ bổ sung thêm `in_progress` và `on_hold` vào tập đang sở hữu `OWNED_ACTIVE_WORK_ITEM_STATES = ("assigned", "in_progress", "on_hold")`.
  - Mọi kiểm tra P1 đã được rà soát từng dòng (capacity, Active Claim, distribution load/reconcile, `api/run_history.py::runs`, `my_list`, Pool, Dashboard, `L-23`, `L-29`) và chứng minh bằng **`209/209` test Backend P1 PASS** + **`12/12` test UI P1 PASS** + **`99/99` test P0 PASS** + cập nhật `ownership_ui.py` nhận diện đủ `('assigned', 'in_progress', 'on_hold')` và xét `latest_version` theo từng `user` (`O-02`).
- **Idempotency của Migration (`bench migrate` × 2):**
  - Patch [`patches/v2_0/p2_schema_and_seeds.py`](../apps/crm_cskh/crm_cskh/patches/v2_0/p2_schema_and_seeds.py) kiểm tra tồn tại trước khi tạo từng Custom Field (`13` trường `cskh_*`), từng chỉ mục MariaDB (`16` chỉ mục P2), từng bản ghi seed (`12` Outcome Code + Policy `GLOBAL-DEFAULT`), đồng thời chạy idempotent bước làm sạch `migrate_p2_assignment_history_privacy()` (tách `[H:<16hex>]` sang `payload_hash`, chuyển lý do nhạy cảm sang `private_reason`, backfill `owner_at_close`); đã được T1C kiểm chứng độc lập trên cả dữ liệu P1 (`108` WIs) lẫn dữ liệu P2 Đợt 1 (`284` History rows: `39/39` `[H:...]` → `0`, `40/40` SĐT → `0`).
- **Chứng minh Backup & Restore thực tế ([`p2-backup-restore-smoke-20261103.json`](../deploy/dev/evidence/p2-runtime-20261103/restore/p2-backup-restore-smoke-20261103.json), cổng CI `p2-backup-restore-smoke.json` & kiểm định độc lập của T1C trên `t1cp2-restore.localhost`):**
  - Cả trên `p2restore.localhost` (local), `p2-restore.localhost` (CI `#41784039795`), và `t1cp2-restore.localhost` (bản sao lưu `610.7 KiB` trên schema `7e789eb` do T1C thực hiện): khôi phục sạch, chạy `bench migrate`, xác nhận đủ các cột mới (`private_reason`, `payload_hash`, `owner_at_close`), số lượng bản ghi khớp 100%, bảo toàn khóa `idempotency_key`/`payload_hash`, và `reconcile_p2_invariants().ok == True`.

---

### 3. Minh chứng vòng đời `Attempts + Follow-up + Handover + Correction + Close` từ API đến Trình duyệt

- **Phía Backend API & HTTP thật:**
  1. **Unit/Integration (`test_p2_lifecycle.py` & `test_p2_t1c_regressions.py`):** Đi trọn chu trình từ `assigned` → Attempt #1 (`no_answer`, `connected=0`, chuyển `in_progress`) → Attempt #2 kèm hẹn gọi lại (`callback_requested`, tạo `CRM Task` `Todo`, chuyển `on_hold`, đồng bộ `next_action_due`) → Attempt #3 (`connected_interested`, `complete_open_followup=True` chuyển `CRM Task` sang `Done`, về `in_progress`) → `close_work_item` (`status="closed"`, ghi `owner_at_close`, giải phóng `CSKH Active Claim`, đóng khoảng sở hữu `ownership_end`, xóa `lead_owner`/`DocShare`/`ToDo`).
  2. **HTTP Transport & Live E2E (`ci_p2_http_probe.py` trên `7e789eb`: `81` verification HTTP record / `81` assertion `PASS`, `0` `setup_observation`, `0` `unasserted`, kèm `9/9` kiểm chứng âm [`test_ci_p2_http_probe.py`](../deploy/dev/seed/test_ci_p2_http_probe.py); [`verify_p2_http_e2e.py`](../deploy/p2/verify_p2_http_e2e.py) trên `0f3c731`: `6/6` nhóm kiểm tra lớn `PASS`; và runtime độc lập của T1C [`t1c-p2-delta-runtime-7e789eb.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-delta-runtime-7e789eb.json) `7/7` khối `PASS`):** Đi trọn từ P1 CSV upload/preview/confirm → campaign rules → async distribution sang Work Item P2 → lọc server-side đủ `11` `care_state` nhiều trang & lọc `closed` theo `owner_at_close`/`closed_by` → ghi kết quả đa kênh → liên kết `CRM Call Log` → lịch hẹn callback → `reassign(A -> B)` → hiệu chỉnh/void/restore → đóng/mở lại không lộ `[H:<16hex>]` hay `private_reason` sang `BOD` → chặn `75/75` vector sửa trực tiếp `cskh_dnc_*` → tiến độ cùng ngày `date_from == date_to` → đối soát `reconcile_p2_invariants ok=true` ([`http-e2e-report.json`](../deploy/p2/evidence/http-e2e-report.json)). *(Lịch sử: snapshot cục bộ `03/11/2026` có `61`/`60`; Đợt 1b `ffe74df` có `61`/`61`).*
- **Phía Giao diện Trình duyệt Chromium Thật ([`verify_p2_browser_e2e.mjs`](../deploy/p2/verify_p2_browser_e2e.mjs) — `22/22 PASS` trên `0f3c731`, manifest tại [`browser-manifest.json`](../deploy/p2/evidence/browser-manifest.json)):**
  - Danh sách nhiều trang (`31` khách active), phân trang server-side tìm thấy khách `connected` chỉ nằm ở trang 2, lọc `Hoàn tất` (`closed`) theo nhân viên & ghi kết quả nhanh **1 POST duy nhất**: [`01-staff-a-my-customers-upgraded.png`](../deploy/p2/evidence/screenshots/01-staff-a-my-customers-upgraded.png), [`18-staff-a-multipage-page2.png`](../deploy/p2/evidence/screenshots/18-staff-a-multipage-page2.png), [`19-staff-a-connected-filter-finds-page2.png`](../deploy/p2/evidence/screenshots/19-staff-a-connected-filter-finds-page2.png), [`20-staff-a-closed-completed-filter.png`](../deploy/p2/evidence/screenshots/20-staff-a-closed-completed-filter.png), [`02-staff-a-after-quick-record.png`](../deploy/p2/evidence/screenshots/02-staff-a-after-quick-record.png).
  - Hồ sơ CSKH, dòng thời gian hợp nhất không lộ `[H:<16hex>]`, trung thực `Chưa kết nối` & an toàn XSS: [`03-staff-a-work-item-happy.png`](../deploy/p2/evidence/screenshots/03-staff-a-work-item-happy.png), [`04-staff-a-honesty-and-xss-safe.png`](../deploy/p2/evidence/screenshots/04-staff-a-honesty-and-xss-safe.png), [`22-leader-a-closed-reopened-milestones-clean.png`](../deploy/p2/evidence/screenshots/22-leader-a-closed-reopened-milestones-clean.png).
  - Xung đột `409 STALE_WORK_ITEM` & bàn giao `A -> B` (`0` lộ PII khi mở lại URL cũ, `Staff B` hoàn tất lịch hẹn nhận bàn giao): [`05-staff-a-stale-revision-conflict.png`](../deploy/p2/evidence/screenshots/05-staff-a-stale-revision-conflict.png), [`06-staff-a-after-transfer-forbidden-zero-leak.png`](../deploy/p2/evidence/screenshots/06-staff-a-after-transfer-forbidden-zero-leak.png), [`07-staff-b-handover-completed-callback.png`](../deploy/p2/evidence/screenshots/07-staff-b-handover-completed-callback.png).
  - Hiệu chỉnh bất biến (`pending` → `Leader A` duyệt → `Admin` vô hiệu `void`), lịch hẹn biên `23:59`/`00:00` & tiến độ cùng ngày khác múi giờ trình duyệt: [`08-staff-a-correction-pending.png`](../deploy/p2/evidence/screenshots/08-staff-a-correction-pending.png), [`09-leader-a-correction-approved.png`](../deploy/p2/evidence/screenshots/09-leader-a-correction-approved.png), [`10-admin-voided-attempt-excluded.png`](../deploy/p2/evidence/screenshots/10-admin-voided-attempt-excluded.png), [`11-staff-a-callbacks-schedule-boundaries.png`](../deploy/p2/evidence/screenshots/11-staff-a-callbacks-schedule-boundaries.png), [`21-staff-a-progress-sameday-cross-tz.png`](../deploy/p2/evidence/screenshots/21-staff-a-progress-sameday-cross-tz.png).

---

### 4. Sơ đồ Kiến trúc API Tạo Attempt / Task / Call Log & Cơ chế Idempotency

```mermaid
sequenceDiagram
    participant UI as Giao diện /cskh/* (p2-attempt-form.js)
    participant API as crm_cskh.api.workflow
    participant Svc as services.contact_attempt / followup
    participant DB as MariaDB (InnoDB Row Lock + Savepoint)
    participant Notif as services.notifications (Post-Commit)

    UI->>API: 1 POST duy nhất record_attempt(..., idempotency_key, expected_revision)
    API->>Svc: Chống giả mạo performed_by / verification_status & chuẩn hóa tham số
    Svc->>DB: Kiểm tra tồn tại (work_item, actor, idempotency_key)
    alt Trùng idempotency_key (double-click / retry mạng)
        DB-->>Svc: Khớp payload_hash -> trả bản ghi cũ (idempotent_replay=true)
        DB-->>Svc: Lệch payload_hash -> 409 IDEMPOTENCY_CONFLICT
    else Khóa mới
        Svc->>DB: SAVEPOINT + SELECT ... FOR UPDATE trên tabCSKH Work Item
        Svc->>DB: Kiểm tra owner hiện tại, expected_revision, Policy & Outcome Code
        Svc->>DB: Kiểm tra khoảng sở hữu mở (occurred_at >= ownership_start & trong cửa sổ backdate)
        opt Có call_log / call_log_data (channel == phone_call)
            Svc->>DB: call_log_adapter kiểm tra UNIQUE(call_log) / tạo CRM Call Log hợp lệ
        end
        Svc->>DB: INSERT CSKH Contact Attempt (chụp cứng snapshot Outcome + Policy)
        opt Giải quyết hoặc tạo mới lịch hẹn (followup / complete_open_followup)
            Svc->>DB: Đóng CRM Task cũ (Done) và/hoặc tạo CRM Task callback mới (Todo) + ToDo
        end
        Svc->>DB: Tính lại Read Model trên CSKH Work Item & tăng revision (+1)
        Svc->>DB: COMMIT
        Svc->>Notif: Phát CRM Notification sau commit (lỗi thông báo không làm rollback DB)
    end
    Svc-->>API: Payload JSON chuẩn hóa
    API-->>UI: HTTP 200 OK (hoặc 409 STALE_WORK_ITEM / OWNERSHIP_CHANGED có nút khôi phục an toàn)
```

- **Kỷ luật phía UI ([`p2-attempt-form.js`](../apps/crm_cskh/crm_cskh/public/cskh/p2-attempt-form.js)):**
  - Một thao tác lưu luôn gọi **đúng một API orchestration** (`workflow.record_attempt`), tuyệt đối không nối chuỗi nhiều API ghi từ trình duyệt rồi coi là giao dịch (`B2E-02` kiểm chứng bộ đếm request mạng `recordPosts.length === 1`).
  - Khóa `idempotency_key` được giữ cố định trong suốt quá trình gửi/thử lại cùng dữ liệu (chống double-click `B2E-05`), và tự động xoay khóa mới ngay khi người dùng chỉnh sửa bất kỳ trường dữ liệu nào trên biểu mẫu.
- **Lược đồ Idempotency hai tầng phía Server:**
  1. **Tầng DB Constraint:** Chỉ mục duy nhất `cskh_attempt_idem_uq (work_item, performed_by, idempotency_key)` trên `tabCSKH Contact Attempt`, `cskh_corr_req_idem_uq (requested_by, idempotency_key)` trên `tabCSKH Attempt Correction`, và cột `payload_hash` riêng biệt trên `tabCSKH Assignment History` (không nhúng `[H:<16hex>]` vào chuỗi hiển thị).
  2. **Tầng Fingerprint (`payload_hash` SHA-256):** Gửi lại đúng payload cũ trả về HTTP `200` (`idempotent_replay: true`); gửi lại cùng khóa nhưng đổi tham số nghiệp vụ bị chặn ngay bằng HTTP `409 IDEMPOTENCY_CONFLICT`.

---

### 5. Chứng minh `Call Log != Connected`, `Gửi tin chưa phản hồi != Connected` & Timeline Không Đếm Trùng

1. **Bất biến cứng ở cấp Model & Seed (`p2_models.py`):**
   - Hằng số `UNREACHED_DISCONNECTED_CODES = {"no_answer", "busy", "unreachable", "message_sent_no_reply", "wrong_number"}` cưỡng chế ngay trong `CSKHOutcomeCode.validate()`: mọi nỗ lực bật `is_connected = 1` trên 5 mã này đều ném `ValidationError` kể cả qua Admin API hay ORM.
2. **`CRM Call Log`, `CRM Task` & `FCRM Note` không tự biến thành `Connected` (`call_log_adapter.py` & `work-item.js`):**
   - Tạo `CRM Call Log` rời (kể cả `status = "Completed"`), thêm `FCRM Note`, hay hủy `CRM Task` có lý do giữ nguyên `attempt_count == 0` / `connected_attempt_count == 0`.
   - Giao diện `/cskh/work-item` và `/cskh/callbacks` chặn hoàn tất lịch hẹn rỗng: nút `Ghi kết quả & hoàn tất` mở form `record_attempt(complete_open_followup=true)`, còn nhánh hủy bắt buộc nhập lý do kiểm toán và hiển thị rõ thông báo `Đã đóng lịch hẹn theo lý do hủy (không tính là đã kết nối)` cùng huy hiệu cảnh báo trên từng thẻ timeline (`Nhiệm vụ — không tự tính là lần liên hệ hay đã kết nối`, `Chưa gắn lần liên hệ (không tự tính số lần liên hệ)`, `Ghi chú nội bộ — không tính vào số lần liên hệ` — đối chứng trực quan tại [`04-staff-a-honesty-and-xss-safe.png`](../deploy/p2/evidence/screenshots/04-staff-a-honesty-and-xss-safe.png)).
   - Ràng buộc `cskh_attempt_call_log_uq UNIQUE (call_log)` chặn tuyệt đối việc liên kết một `CRM Call Log` vào 2 Attempt (`409 ALREADY_LINKED`).
3. **Timeline hợp nhất không đếm trùng (`services/read_model.py` + `public/cskh/work-item.js`):**
   - Bộ đếm trên đầu trang đọc duy nhất từ các `CSKH Contact Attempt` hiệu lực (`is_voided == false`), trong khi danh sách dòng thời gian hiển thị phân loại trực quan từng loại bản ghi (`Lần liên hệ CSKH`, `Nhật ký cuộc gọi (CRM)`, `Lịch hẹn / Nhiệm vụ (CRM)`, `Ghi chú nội bộ (CRM)`, `Lịch sử phụ trách`) kèm bộ lọc theo loại mà không bao giờ cộng gộp nhầm sang số lần liên hệ.

---

### 6. Kiểm thử Đua Đồng thời `A -> B` Trong Lúc `A` Submit, Chuyển Callback Task & Quyền Trước/Sau

- **Đua song song trên 2 kết nối MariaDB thật (`test_p2_concurrency_chaos.py`, `api/workflow.py` & `api/ownership.py`):**
  - Luồng 1 (`Staff A`) gọi `contact_attempt.record_attempt` đồng thời với Luồng 2 (`CRM Admin`) gọi `ownership.reassign(A -> B)` cùng rào chắn `threading.Barrier(2)`; cả `api/workflow.py` lẫn `api/ownership.py` đều đã bọc `_run_with_tx_retry` xử lý sạch `1020`/`1213` (`OBS-P2-01 = RESOLVED`, được T1C xác minh `3/3` lượt đua HTTP `Barrier(2)` đạt `0` HTTP `500`/`400`).
  - Nhờ `SELECT ... FOR UPDATE` trên cùng hàng `tabCSKH Work Item`:
    - Nếu `A` vào khóa trước: Attempt của `A` được ghi hợp lệ vào khoảng sở hữu của `A` (`performed_by = A`), ngay sau đó `reassign` đóng khoảng sở hữu của `A` và mở khoảng mới cho `B`.
    - Nếu `reassign` vào khóa trước: `A` đọc thấy `assigned_to == B != A` và bị từ chối sạch với `409 OWNERSHIP_CHANGED` (`0` bản ghi mồ côi).
- **Chuyển giao Callback `CRM Task` & kiểm chứng trình duyệt (`B2E-06`, `B2E-07`):**
  - Khi `Work Item` đang `on_hold` được `reassign(A -> B)`, callback `CRM Task` đang mở được chuyển sang `assigned_to = B`, đóng `ToDo` của `A`, mở `ToDo` cho `B`, ghi sự kiện `reassigned_handover` và **giữ nguyên tuyệt đối `due_date` gốc**.
  - Ngay sau khi chuyển giao, `Staff A` tải lại đúng URL `/cskh/work-item?id=...` đang mở trước đó nhận thông báo `[FORBIDDEN]`, ẩn sạch toàn bộ thẻ khách hàng và timeline (`0` rò rỉ PII — [`06-staff-a-after-transfer-forbidden-zero-leak.png`](../deploy/p2/evidence/screenshots/06-staff-a-after-transfer-forbidden-zero-leak.png)); `Staff B` mở cùng hồ sơ nhìn thấy lịch sử Attempt của `Staff A` kèm hạn callback gốc và hoàn tất thành công dưới tên `Staff B` ([`07-staff-b-handover-completed-callback.png`](../deploy/p2/evidence/screenshots/07-staff-b-handover-completed-callback.png)).

---

### 7. Kiểm chứng Quyền trên Native `CRM Call Log` / `CRM Task` / `FCRM Note` / `CRM Lead` / `File` API, Cổng Website `/cskh/*` & Ranh giới Kiến trúc

- **Cơ chế bảo vệ đa tầng (Backend + Website Route Gate):**
  1. **Đọc (`permission_query_conditions` + `has_permission` + `api/guarded_crm.py` + `api/guarded_reporting.py`):** Tái sử dụng lưới P0 (`linked_lead_query_conditions`, `linked_lead_has_permission` trong `permissions.py` và `crm_cskh.api.guarded_crm.get_activities`) cho `CRM Call Log`, `CRM Task`, `FCRM Note`, `Comment`, `File`, kết hợp [`p2_permissions.py`](../apps/crm_cskh/crm_cskh/p2_permissions.py) cho 4 DocType P2, khóa quyền đọc lịch sử chủ cũ / team cũ sau chuyển giao (`L-23` cho cả hồ sơ active lẫn `closed`), và tách `CSKH Assignment History.private_reason` (`permlevel=1`) + `payload_hash` (`permlevel=1, hidden=1`) để chặn hoàn toàn `CSKH BOD Viewer` nhận lý do riêng tư/PII trên cả 5+ mặt phẳng (`F-P2-01` = `CLOSED`).
  2. **Cổng phân quyền cấp trang Website (`/cskh/*` — `www/cskh_context.py`):** Mọi route mới đều kiểm tra quyền ở phía máy chủ trước khi render HTML: `BOD` bị từ chối HTTP `403` ngay trên các route chứa PII (`/cskh/work-item`, `/cskh/callbacks` — [`14-bod-work-item-403-denied.png`](../deploy/p2/evidence/screenshots/14-bod-work-item-403-denied.png)), `Sales User` bị từ chối HTTP `403` trên `/cskh/settings`; frontend chỉ dùng `capabilities` để ẩn/hiện nút hợp lệ chứ không thay thế kiểm tra quyền backend.
  3. **Ghi/Xóa trực tiếp qua Generic Frappe API (`/api/resource/*`, `/api/v2/*`, `frappe.client.*`, Desk):**
     - `CSKH Contact Attempt` & `CSKH Attempt Correction`: tắt hoàn toàn `create/write/delete` + controller `before_save`/`on_trash` chặn sửa/xóa kể cả khi gọi nội bộ với `ignore_permissions=True`.
     - `CRM Task`: hook `doc_events["CRM Task"]` (`p2_models.validate_cskh_followup_task`, `prevent_cskh_followup_task_trash`) chặn mọi thao tác tạo/sửa/đóng/xóa trực tiếp trên callback CSKH (`cskh_is_followup == 1`) từ bên ngoài `services/followup.py`.
     - `CRM Lead` (`cskh_do_not_contact` & `cskh_dnc_*`): khóa `read_only=1` + `p2_models.validate_crm_lead_dnc_fields` + `p2_models.enforce_lead_dnc_request_guard` + `guarded_crm.client_set_value`/`client_bulk_update` chặn mọi thao tác bật/gỡ DNC trái phép ngoài service CSKH được ủy quyền (`F-P2-06` = `CLOSED`).
- **Ranh giới thiết kế & kiến trúc không chặn (`0` lỗi Critical/High/Medium/Low mở):**
  - **`ADR-P2-05`:** Nhân viên sở hữu hợp pháp một `CRM Lead` vẫn có thể tạo `CRM Call Log` hoặc `FCRM Note` gốc của Frappe CRM trên chính Lead mình đang giữ, nhưng cả Backend lẫn UI đều gắn nhãn phân biệt rõ và **không bao giờ tự động cộng** các bản ghi rời này vào `attempt_count` hay `connected_attempt_count`.
  - **`OBS-P2-03` (Ranh giới `File.owner` theo baseline Frappe v15 / P0–P1):** Trên tệp riêng tư gắn với `CRM Lead`, P0/P1 (`SEC-26`) đã che sạch `/private/files/...` khỏi `guarded_crm.get_attachments` / `get_activities`, `/api/resource/File/<id>` trả `404` đồng nhất từng byte cho cựu owner / cựu leader / người ngoài phạm vi, và `/private/files/...` trả `403` cho cựu Leader hoặc tệp do hệ thống / người khác tải lên; **tuy nhiên**, theo hành vi mặc định `frappe.permissions.has_permission` của Frappe v15 (`0` sửa core upstream), người tải lên gốc (`File.owner == user`) nếu tự lưu URL trực tiếp `/private/files/...` cá nhân từ trước thì đường dẫn tĩnh đó vẫn đi theo quy tắc `File.owner` của Frappe v15 (không suy rộng thành mọi đường tải file đều bị chặn).

---

### 8. Cơ chế Hiệu chỉnh (`Correction`), Kiểm toán (`Audit`), `Effective View` & Quyền Phê duyệt

- **Bất biến bản ghi gốc & giao diện không sửa/xóa trực tiếp:** Dòng `CSKH Contact Attempt` không bao giờ bị `UPDATE` hay `DELETE`; trên `/cskh/work-item` hoàn toàn không có nút Sửa/Xóa Attempt mà chỉ có nút `Yêu cầu hiệu chỉnh / vô hiệu` (`B2E-03`).
- **Ba hành động kiểm toán trong `CSKH Attempt Correction` (`services/correction.py`):**
  1. `correct`: Đề xuất sửa một tập trường cho phép (`outcome_code`, `channel`, `occurred_at`, `note`, `call_log`); sau khi duyệt, UI hiển thị đồng thời giá trị hiệu lực mới và hộp `Bản ghi gốc` để đối chiếu minh bạch ([`09-leader-a-correction-approved.png`](../deploy/p2/evidence/screenshots/09-leader-a-correction-approved.png)).
  2. `void`: Hủy hiệu lực một Attempt ghi nhầm; dòng gốc vẫn nằm trên timeline với huy hiệu `Đã vô hiệu — không tính vào bộ đếm` nhưng bị loại khỏi `attempt_count`, `connected_attempt_count`, `last_outcome_code` và `progress_summary` ([`10-admin-voided-attempt-excluded.png`](../deploy/p2/evidence/screenshots/10-admin-voided-attempt-excluded.png)).
  3. `restore`: Khôi phục hiệu lực cho một Attempt đã bị `void` trước đó (chỉ dành cho `CSKH CRM Admin`).
- **Quyền yêu cầu & phê duyệt:** `Sales User` chỉ tạo yêu cầu `pending`; `Sales Manager` duyệt trong phạm vi team khi chính sách bật `allow_leader_review_correction == 1`; `CSKH CRM Admin` có quyền duyệt toàn hệ thống.

---

### 9. Việt hóa `vi_VN`, Xử lý Biên Múi giờ `23:59`/`00:00` & Bảng Thuật ngữ (`docs/glossary-vi.md`)

- **Từ điển dịch & kiểm toán tự động (`0` lỗi):**
  - Hợp nhất toàn bộ chuỗi Backend (`T1B`) và chuỗi Giao diện P1/P2 (`T1A` trong `p1_strings.py` + `p2_strings.py`) vào [`apps/crm_cskh/crm_cskh/translations/vi.csv`](../apps/crm_cskh/crm_cskh/translations/vi.csv) (**`912` dòng**) và chuẩn hóa bảng thuật ngữ xuyên suốt P0–P2 tại [`docs/glossary-vi.md`](./glossary-vi.md).
  - Kiểm toán tự động `python deploy/p1/check_translations_audit.py` đạt: `used strings: 446`, `declared strings: 696`, `vi.csv rows: 912`, **`missing declaration: 0`**, **`missing translation: 0`**, **`untranslated (EN): 0`**, **`orphan declaration: 0`**.
  - Kiểm chứng trình duyệt trên cả `5` nhân vật thuộc `4` nhóm vai trò (`Staff A`, `Staff B`, `Leader A`, `BOD`, `CRM Admin` — `B2E-01..B2E-22`): toàn bộ tiêu đề, điều hướng, cột bảng, chip lọc server-side, nhãn độ bao phủ `Work Item` vs `Số khách hàng ngừng liên hệ (DNC)`, trạng thái lịch hẹn, lịch sử phụ trách sạch (không có `[H:<16hex>]`), hộp thoại hiệu chỉnh, empty state và toast đều hiển thị tiếng Việt tự nhiên, `html[lang="vi"]` đạt `90/90` kiểm tra HTTP route.
- **Biên ngày giờ `23:59:59` / `00:00:00` & lọc cùng ngày theo đúng múi giờ Site/User (không lệ thuộc giờ máy tính):**
  - Tầng JS dùng chung [`cskh.js`](../apps/crm_cskh/crm_cskh/public/cskh/cskh.js) đọc `cskhBoot.userTimezone || cskhBoot.siteTimezone || 'Asia/Ho_Chi_Minh'` từ server context và định dạng/phân loại qua `Intl.DateTimeFormat(..., { timeZone })`; phía server `services/read_model.py::progress_summary` chuẩn hóa `date_from`/`date_to` theo khoảng nửa mở `[start_of_day, start_of_next_day)` trên cùng múi giờ site/user.
  - Đã kiểm chứng bằng Node unit suite ([`test_p2_ui_timezone_and_contracts.mjs`](../deploy/p2/test_p2_ui_timezone_and_contracts.mjs)), Chromium thật (`B2E-11` & `B2E-21`) và runtime độc lập của T1C (`06_f_p2_05_progress_summary`).

---

### 10. Báo cáo Hiệu năng Thực nghiệm ở Tầng Python Service (`10.000` Work Item / `50.000` Attempt / `25` Worker), CPU/RAM & Slow Queries

> **Nguyên tắc trung thực & Giới hạn phép đo (`P2.md` §15.4 & `OBS-P2-02`):** Toàn bộ con số dưới đây được trích nguyên bản từ tệp đo thực tế [`deploy/dev/evidence/p2-runtime-20261103/benchmark/p2-benchmark-20261103.json`](../deploy/dev/evidence/p2-runtime-20261103/benchmark/p2-benchmark-20261103.json) (commit đưa vào repo tại `23a782bd58edaaef3e406fbc2b7e40622fb98d58`, SHA-256 `81c8b2afd4bc2d4279e0d0351099509f795d6cf4b7f2942261f7a84f219ae383`) do **T1B** thực hiện trên site cô lập `p2bench.localhost` (container DEV MariaDB `11.8` + Python `3.14`); **không tự đặt ngưỡng SLA giả định**. Lưu ý minh bạch: phép đo này **gọi trực tiếp hàm Python service trong tiến trình `bench execute` + `ThreadPoolExecutor(25)` — tuyệt đối không gọi đây là tải HTTP/WSGI**.

#### 10.1. Quy mô dữ liệu & Tài nguyên tổng thể
- **Tập dữ liệu trên `p2bench.localhost`:** `10.000` `CSKH Work Item`, `50.000` `CSKH Contact Attempt` (trong đó `P2B-WI-00000` có **`120` attempt**), `2.020` open callback `CRM Task`, `520` approved `CSKH Attempt Correction`, phân bổ đều trên `2` Team / **`25` nhân viên (`Staff`)** (thời gian nạp bulk: `22,089s`; tổng thời gian chạy toàn bộ kịch bản benchmark + đối soát: `82,61s`).
- **Tài nguyên & MariaDB toàn phiên:** RSS khởi điểm `128,28 MiB` → **Peak RSS `326,0 MiB`**; Python `tracemalloc` peak heap **`105,45 MiB`**; CPU `66,938s user` / `4,479s sys`; tổng truy vấn `14.284`; **`slow_queries_delta = 0`**; **`innodb_row_lock_waits_delta = 0` (`0 ms`)**.
- **Đối soát bất biến hậu kiểm (`reconcile_p2_invariants` trên toàn bộ `10.000` Work Item & `50.025` Attempt):** **`ok = true`** trong **`11,321s`** (`0` vi phạm trên cả 9 hạng mục kiểm tra).

#### 10.2. Độ trễ Ghi Giao dịch ở Tầng Python Service (Single-Worker Microbenchmark)

| Thao tác dịch vụ Python (kèm `frappe.db.commit()`) | Số mẫu | `min` (ms) | `p50` (ms) | `p95` (ms) | `max` (ms) | `mean` (ms) |
|---|---:|---:|---:|---:|---:|---:|
| `contact_attempt.record_attempt` | `100` | `40,844` | **`51,054`** | `88,301` | `349,347` | `59,714` |
| `followup.schedule_followup` (tạo & dời lịch) | `40` | `28,724` | **`39,231`** | `51,963` | `115,879` | `40,190` |
| `request_correction` + `review_correction(approve)` | `20` | `83,321` | **`94,050`** | `138,190` | `222,107` | `104,259` |

#### 10.3. Độ trễ Đọc, Lọc & Phân trang ở Tầng Python Service trên DB `10.000` Work Item / `50.000` Attempt

| Hàm dịch vụ (`services/read_model.py` / `services/followup.py`) | Actor | `p50` (ms) | `p95` (ms) | Số câu SQL / lần gọi | Kích thước JSON (bytes) |
|---|---|---:|---:|---:|---:|
| `list_work_items` (mặc định trang 1, `page_size=20`) | `Staff` | **`14,858`** | `20,312` | `2` | `17.449` |
| `list_work_items(followup_filter="overdue")` | `Staff` | **`8,712`** | `11,334` | `2` | `17.448` |
| `list_work_items(status="in_progress")` | `Leader` | **`77,076`** | `85,034` | `4` | `16.855` |
| `list_work_items(followup_filter="today")` | `CRM Admin` | **`13,772`** | `20,375` | `2` | `17.694` |
| `get_work_item_detail` (`P2B-WI-00000`) | `Staff` | **`26,100`** | `36,877` | `13` | `2.059` |
| `list_attempts_timeline` (`120` attempt, `page=1`, `size=20`) | `Staff` | **`22,192`** | `53,559` | `4` | `26.983` |
| `list_attempts_timeline` (`120` attempt, `page=6`, `size=20`) | `Staff` | **`21,991`** | `28,435` | `4` | `26.881` |
| `list_followups(bucket="overdue", page=1, size=20)` | `Leader` | **`11,717`** | `12,601` | `4` | `11.489` |
| `list_followups(bucket="today", page=1, size=20)` | `Leader` | **`11,602`** | `12,555` | `4` | `11.529` |
| `progress_summary` (`~2.000` attempt cá nhân) | `Staff` | **`118,532`** | `119,316` | `11` | `872` |
| `progress_summary` (`~25.000` attempt cấp Team) | `Leader` | **`1.321,960`** | `1.323,481` | `41` | `3.414` |
| `progress_summary` (`50.000` attempt toàn hệ thống, `0` PII) | `BOD` | **`2.519,388`** | `2.728,906` | `58` | `6.418` |

- **Nhận xét kỹ thuật từ `EXPLAIN` & backlog `OBS-P2-02` cho P3:**
  - Hàng đợi nhân viên (`cskh_wi_queue_due_idx`), timeline 120 attempt (`cskh_attempt_wi_occ_idx`), lớp phủ hiệu chỉnh (`cskh_corr_attempt_status_idx`) và lịch hẹn quá hạn (`cskh_task_followup_due_idx`) đều đi đúng `range` / `ref` index scan với số hàng quét nhỏ (`1` đến `650` hàng).
  - Với `progress_summary` toàn kỳ không lọc ngày trên tập `25k–50k` attempt, truy vấn `attempt_summary_by_campaign_occurred` hiện quét bảng (`type = "ALL"`, `48.650` dòng) và chiếu lớp phủ `CSKH Attempt Correction` trong bộ nhớ Python nên mất `~1,32s` (Team `25k`) và `~2,52s` (System `50k`). Ghi nhận vào backlog đầu vào của P3 (`OBS-P2-02`) để dùng bảng tổng hợp/chỉ mục theo kỳ khi triển khai báo cáo SLA/KPI quy mô lớn.

#### 10.4. Đa luồng Python Service `25` Worker (`ThreadPoolExecutor(25)`, `25` kết nối MariaDB song song) & Giới hạn Phép đo
- **Tổng số thao tác:** `100` (`25` worker × `4` lời gọi hàm service Python: đọc hàng đợi → ghi `record_attempt` kèm tạo callback `CRM Task` → đọc timeline → đọc danh sách lịch hẹn) hoàn tất trong **`10,762s` wall-clock**.
- **Độ tin cậy & khóa DB:** **`error_count = 0`**, **`slow_queries_delta = 0`**, **`innodb_row_lock_waits_delta = 0`**, Peak RSS **`326,0 MiB`**.
- **Giới hạn phép đo (`OBS-P2-02`):** Tệp `p2-benchmark-20261103.json` (commit gốc `23a782bd58edaaef3e406fbc2b7e40622fb98d58`, chưa nhúng trường `commit_sha` nội tại) chạy trực tiếp các hàm Python service trong 1 tiến trình `bench execute` với `ThreadPoolExecutor(25)` — do đó phản ánh hiệu năng tầng dịch vụ Python + MariaDB (có chịu tranh chấp GIL Python ở luồng ghi đồng thời), **không phải** phép đo tải qua tầng HTTP/WSGI hay reverse proxy TLS.

---

### 11. Tổng hợp Phán quyết `15/15 PASS` của T1C trên `15` Cổng Nghiệm thu (`G2-01 .. G2-15` — Đánh số chuẩn `P2.md` §17)

> **Nguyên tắc phân quyền nghiệm thu:** Cột `Phán quyết độc lập T1C (trên 7e789eb)` trích nguyên bản kết luận đã ký của **T1C** tại [`t1c-p2-verification-report.md`](../deploy/dev/evidence/p2-t1c/t1c-p2-verification-report.md) (**`15/15 PASS`, `6/6` finding `CLOSED`, đề nghị `RECOMMEND GO P3`**). Quyết định điều hành ký mở **`GO P3`** và merge PR thuộc thẩm quyền riêng của **T0 / Owner** (hiện chưa ký).

| Cổng (`P2.md` §17) | Hồ sơ kỹ thuật tích hợp (`T1B` `16e69438` + `T1A` `0f3c731` / `7e789eb`) | Phán quyết độc lập `T1C` (`7e789eb`) |
|---|---|---|
| **G2-01 — Entry provenance** | [`docs/P2-entry-report.md`](./P2-entry-report.md), `ADR-P2-01..11`, khóa phiên bản `frappe 15.123.1` & `crm 1.87.0` trên baseline `1913d2b` | **`PASS`** *([`t1c-p2-entry-baseline-1913d2b.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-entry-baseline-1913d2b.json))* |
| **G2-02 — Schema/migration** | `4` DocType, `13` Custom Field, `16` Index MariaDB, `migrate_p2_assignment_history_privacy` & backfill `owner_at_close` idempotent (`bench migrate` × 2 exit `0`, `backup/restore` `PASS`) | **`PASS`** *([`t1c-p2-delta-runtime-7e789eb.json`](../deploy/dev/evidence/p2-t1c/artifacts/t1c-p2-delta-runtime-7e789eb.json))* |
| **G2-03 — Lifecycle** | Máy trạng thái `assigned`/`in_progress`/`on_hold`/`closed`/`revoked`, đóng `F-P2-02` (`api/run_history.py::runs`) và `F-P2-04B` (`services/read_model.py::list_work_items` với `owner_at_close` vs `closed_by` + `L-23`) | **`PASS`** *(`F-P2-02` & `F-P2-04B` `CLOSED`)* |
| **G2-04 — Outcome taxonomy** | `12` mã kết quả tiếng Việt chuẩn, snapshot `v1 -> v2`, đóng `F-P2-06` (`75/75` bypass vectors trên 4 trường `cskh_dnc_*` trả `403`, bảo toàn sửa `CRM Lead` thường `8/8 HTTP 200` & kênh DNC có audit) | **`PASS`** *(`F-P2-06` `CLOSED`)* |
| **G2-05 — Contact Attempt** | Sự kiện bất biến (append-only), chặn giả mạo `performed_by`/`verification_status`, kiểm tra khoảng sở hữu & backdate `A -> B -> A` (`test_p2_corrections.py`, `B2E-03`) | **`PASS`** |
| **G2-06 — Unsuccessful ≠ Connected** | `UNREACHED_DISCONNECTED_CODES`, gửi tin chưa phản hồi & hủy callback không bao giờ tăng `connected` (`B2E-02`, `B2E-04`) | **`PASS`** |
| **G2-07 — Atomic & idempotent** | `1` POST orchestration duy nhất (`B2E-02`), chống double-submit `Barrier(6)` & `409 STALE_WORK_ITEM` (`B2E-05`), retry MariaDB `1020`/`1213` trên cả `api/workflow.py` & `api/ownership.py` (`OBS-P2-01 RESOLVED`) | **`PASS`** |
| **G2-08 — Native reuse** | Reuse trực tiếp `CRM Call Log`, `CRM Task`, `FCRM Note` + `CRM Form Script` (`0` file upstream bị sửa, `B2E-04`, `B2E-17`) | **`PASS`** |
| **G2-09 — Follow-up flow** | Tối đa `01` open callback `CRM Task`/WI, phân loại biên `23:59`/`00:00` đúng múi giờ site/user (`test_p2_ui_timezone_and_contracts.mjs`, `B2E-07`, `B2E-11`) | **`PASS`** |
| **G2-10 — Transfer safety** | `reassign(A -> B)` giữ hạn `due_date`, thu hồi tức thời quyền `A` & đội cũ trên cả WI active lẫn WI `closed` chuyển đội (`L-23`), mở lại URL cũ `0` lộ PII (`B2E-06`, `B2E-07`) | **`PASS`** |
| **G2-11 — Correction/audit** | `0` nút sửa/xóa thẳng `Attempt`; hiệu chỉnh `correct` / `void` / `restore` lưu vết bản gốc + hiệu lực, đồng bộ tức thời `progress_summary` (`B2E-08..10`) | **`PASS`** |
| **G2-12 — Permissions & PII** | **`SEC2-01..SEC2-16 = 16/16 PASS`**; đóng cả 2 finding `HIGH` `F-P2-01` (`BOD` `0` PII / `0` `private_reason` / `12/12` oracles `403`) & `F-P2-06` (`75/75` vectors `403`); bảo toàn P0 `107/107` & P1 `221/221` + `170` HTTP | **`PASS`** *(`F-P2-01` & `F-P2-06` `CLOSED`)* |
| **G2-13 — UI tiếng Việt** | `vi.csv` `912` dòng (`0` lỗi), đóng `F-P2-04A` (`11/11` `care_state` phân trang SQL nhiều trang khớp `COUNT(*)`, `B2E-18..20`) & `F-P2-01` (`0` `[H:<16hex>]` trên dữ liệu cũ/mới, `B2E-22`) | **`PASS`** *(`F-P2-04A` & `F-P2-01` `CLOSED`)* |
| **G2-14 — Progress basic** | Đóng `F-P2-05`: biên ngày nửa mở `[00:00:00, next_day 00:00:00)` theo `Asia/Ho_Chi_Minh`, tách bạch `Work Item` đã/chưa thử/đã kết nối vs `effective_attempts` và `dnc_leads` vs `dnc_work_items` (`B2E-12`, `B2E-21`) | **`PASS`** *(`F-P2-05` `CLOSED`)* |
| **G2-15 — Handoff P3** | CI P0 `#41784039800`, P1 `#41784039850`, P2 `#41784039795` (`38+9` XML, `81/81` HTTP assertions, `30/30` SHA-256) `success`; đóng `6/6` finding (`F-P2-01..06`); đầy đủ hồ sơ T1C + tài liệu §18.2 | **`PASS`** *(T1C đề nghị `RECOMMEND GO P3`; chờ `T0 / Owner` ký duyệt)* |

---

### 12. Dữ liệu Nền P2 Bàn giao Cho Giai đoạn P3 (SLA, QA & KPI), Giới hạn Phạm vi & Bước Tiếp theo

#### 12.1. Các trường & bất biến dữ liệu P2 đã sẵn sàng để P3 tiêu thụ trực tiếp (không cần migrate lại lịch sử)
1. **Đo thời gian phản hồi lần đầu (First Response / Speed-to-Lead SLA ở P3):**
   - Khoảng sở hữu chính xác trên `CSKH Assignment History` (`ownership_start`, `ownership_end`, `to_user`, `action`, `payload_hash` tách biệt) kết hợp `CSKH Contact Attempt.occurred_at`, `recorded_at`, `ownership_event`, `performed_by`, `CSKH Work Item.first_attempt_at` và `owner_at_close`.
2. **Đo tỷ lệ tiếp cận & chuyển đổi thực chất (không nhiễu bởi gọi nhỡ / gửi tin một chiều):**
   - Cờ chụp cứng bất biến trên từng `Attempt`: `contact_attempt_snapshot`, `connected_snapshot`, `interested_snapshot`, `terminal_snapshot`, `dnc_snapshot`, `outcome_group_snapshot`, `outcome_version_snapshot`, `policy_version_snapshot`, cộng lớp phủ `CSKH Attempt Correction` đã được chuẩn hóa qua `services/read_model.py::compute_effective_attempts()`.
3. **Đo kỷ luật thực hiện lịch hẹn (Callback Punctuality SLA ở P3):**
   - Trên `CRM Task` (`cskh_is_followup = 1`): `due_date`, `cskh_reschedule_count`, `cskh_source_attempt`, `cskh_completed_attempt`, `cskh_completion_reason` và nhật ký JSON `cskh_task_history_json` lưu đầy đủ lịch sử đặt hẹn, dời hẹn (kèm `old_due`, `new_due`, `reason`, `actor`), bàn giao `A -> B` (không reset hạn) và thời điểm hoàn tất.
4. **Đầu vào cho Kiểm định Chất lượng / Hậu kiểm Tổng đài (QA Verification ở P3):**
   - Trường `CSKH Contact Attempt.verification_status` (`self_reported | verified | disputed`) đã có sẵn và bị khóa cứng `self_reported` đối với nhân viên ở P2 (hiển thị nhãn `Nhân viên tự báo cáo` trên UI), sẵn sàng cho quy trình QA/tổng đài ở P3 đối chiếu băng ghi âm `CRM Call Log` để nâng lên `verified` hoặc đánh dấu `disputed`.

#### 12.2. Giới hạn phạm vi có chủ đích (`P2.md` §2.2) & Backlog kỹ thuật không chặn chuyển giao sang P3
- **Phạm vi nghiệp vụ P2:** Chưa chấm điểm SLA, chưa xếp hạng nhân viên hay tính thưởng/phạt KPI (thuộc P3); chưa tích hợp tự động tổng đài VoIP/SIP, SMS Gateway, Zalo OA hay Facebook Webhook (thuộc P3/P4).
- **Ghi nhận không chặn từ T1C & P1:**
  - **`OBS-P2-01` (`RESOLVED`):** Đã đóng bằng `_run_with_tx_retry` trong `api/ownership.py` (xác minh bởi T1C trên `7e789eb`).
  - **`OBS-P2-02` (Giới hạn benchmark tầng Python service & tối ưu báo cáo P3):** `p2-benchmark-20261103.json` đo ở tầng hàm Python service (`bench execute` + `ThreadPoolExecutor(25)`, không phải tải HTTP); truy vấn `progress_summary` toàn kỳ không lọc ngày trên `50k` attempts của `BOD` (`~2,52s`) được đưa vào backlog kiến trúc P3 để dùng bảng tổng hợp/chỉ mục theo kỳ.
  - **`OBS-P2-03` (Ranh giới `File.owner` theo baseline Frappe/P1):** Đã che `/private/files/...` trên `guarded_crm.get_attachments`/`get_activities`, trả `404` trên `/api/resource/File/<id>` và trả `403` trên `/private/files/...` cho cựu Leader hoặc tệp của hệ thống/người khác; riêng người tạo gốc (`File.owner == user`) nếu giữ URL trực tiếp `/private/files/...` cá nhân từ trước vẫn đi theo quy tắc mặc định `frappe.permissions.has_permission` của Frappe v15 (`0` sửa core upstream).
  - **`O-02` (`CLOSED` trên `7e789eb`):** Đã xử lý tại [`api/ownership_ui.py`](../apps/crm_cskh/crm_cskh/api/ownership_ui.py) (`_campaign_ineligible_users` xét `latest_version` theo từng `user` và tính tải theo `('assigned', 'in_progress', 'on_hold')`; `assigned_list` gọi `reporting._scope` & `reporting._scope_users`, còn `assignees` dùng các hàm nội bộ trong `ownership_ui.py`).
  - **`O-01` (Backlog không chặn từ P1):** Tối ưu import chunking P1 giữ nguyên trong backlog kỹ thuật không chặn.

#### 12.3. Hành động tiếp theo dành cho T0 / Owner
- Hồ sơ kiểm định độc lập đã ký của **T1C** (`15/15 PASS`, `6/6` finding `CLOSED`, `RECOMMEND GO P3`) cùng toàn bộ bằng chứng trên mốc nghiệm thu `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` đã được đóng gói nguyên vẹn vào **PR #7**.
- Kính trình **T0 / Owner** xem xét phê duyệt điều hành, thực hiện merge theo quy trình quản trị nhánh và ký mở Giai đoạn P3 (`GO P3`).
