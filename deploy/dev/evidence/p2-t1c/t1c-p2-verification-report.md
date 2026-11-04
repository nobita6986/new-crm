# Báo cáo Kiểm định Độc lập P2 — Bản Tích hợp Cuối & Nghiệm thu Delta trên `PR #7` `HEAD` `7e789eb` (T1C)

- **Vai trò:** **T1C** — Kiểm định độc lập Bảo mật, Chất lượng & Runtime cho **P2 (`CRM-CSKH — Quy trình CSKH, Nhật ký liên hệ & Hẹn chăm sóc lại`)**
- **Tài liệu gốc đối chiếu:** [`docs/root-plan/P2.md`](../../../../docs/root-plan/P2.md) (§0–§22) và [`docs/architecture/version-lock.md`](../../../../docs/architecture/version-lock.md)
- **Môi trường kiểm định P2 cô lập của T1C:**
  - Worktree: `D:/HRP-app/t1c-p2-verify` (clean detached `HEAD` tại `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4`, `apps/` tree `f3b6db3ff80b3371ea5b991d7e5aaa408533007b`; tách rời tuyệt đối khỏi worktree T1A/T1B và stack `crm-cskh-p1` cổng `8120` của Owner; đã dừng sạch container sau khi trích xuất bằng chứng)
  - Docker Compose Project: `crm-cskh-t1c-p2` (`WEB_PORT=18320`, `SOCKETIO_PORT=19320`, `VITE_PORT=18380`) · Site: `t1cp2.localhost` (và site phục hồi tạm `t1cp2-restore.localhost`)
- **Bộ hồ sơ do T1C sở hữu (`deploy/dev/evidence/p2-t1c/`):**
  1. Báo cáo kiểm định độc lập P2 (tài liệu này): [`t1c-p2-verification-report.md`](./t1c-p2-verification-report.md)
  2. Ma trận bảo mật P2: [`security-matrix-p2.md`](./security-matrix-p2.md)
  3. Sổ cái bằng chứng kiểm thử & hiệu năng P2: [`test-evidence-p2.md`](./test-evidence-p2.md)
  4. Artifact thực nghiệm Entry Baseline (`1913d2b`, bảo toàn nguyên trạng): [`artifacts/t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json)
  5. Artifact thực nghiệm Runtime Tích hợp Đợt 1 (`26b0237` ≡ `ffe74df`, bảo toàn nguyên trạng): [`artifacts/t1c-p2-runtime-26b0237.json`](./artifacts/t1c-p2-runtime-26b0237.json)
  6. Artifact kiểm toán Phép đo & CI Đợt 1b (`ffe74df`, bảo toàn nguyên trạng): [`artifacts/t1c-p2-ffe74df-audit.json`](./artifacts/t1c-p2-ffe74df-audit.json)
  7. Artifact thực nghiệm Runtime Delta Đợt 2 trên `7e789eb` (`migrate × 2` trên dữ liệu cũ `ffe74df` + replay `F-P2-01/02/04/05/06` + `OBS-P2-01` + `backup/restore`): [`artifacts/t1c-p2-delta-runtime-7e789eb.json`](./artifacts/t1c-p2-delta-runtime-7e789eb.json)
  8. Manifest & Bảng băm toàn vẹn hồ sơ T1C: [`manifest.json`](./manifest.json) · [`SHA256SUMS.txt`](./SHA256SUMS.txt)
- **Kết luận Kiểm định Độc lập Hiện hành của T1C (`2026-11-04` trên `PR #7` `HEAD` `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4`):**
  - **Cổng đạt (`PASS`):** **`15/15`** (`G2-01` → `G2-15`).
  - **Cổng chưa đạt (`FAIL`):** **`0/15`**.
  - **Tổng hợp Finding:** **`0` lỗi đang mở** (`0` `CRITICAL`, `0` `HIGH`, `0` `MEDIUM`, `0` `LOW`); **`6/6` finding lịch sử đã xác minh `CLOSED` bằng bằng chứng runtime độc lập** (`F-P2-01`, `F-P2-02`, `F-P2-03`, `F-P2-04`, `F-P2-05`, `F-P2-06`).
  - **Ghi nhận kỹ thuật không chặn:** `OBS-P2-01` đã **`RESOLVED`** trên `16e69438` / `7e789eb`; còn **`2` ghi nhận kiến trúc/hiệu năng không chặn** (`OBS-P2-02`, `OBS-P2-03`) chuyển giao minh bạch cho P3.
  - **Khuyến nghị của T1C gửi T0 / Owner:** **`RECOMMEND GO P3 (ĐỦ ĐIỀU KIỆN NGHIỆM THU P2)`** *(T1C giữ đúng thẩm quyền kiểm định độc lập: **không tự ký `GO P3` thay Owner** và **không merge PR**).*

---

## 1. Phân tách Minh bạch Provenance Revision & 3 Tầng Bằng chứng

### 1.1. Sổ cái Revision đã kiểm định (tách `HEAD SHA`, `apps/` Tree SHA và `CI Synthetic Merge SHA`)

| Mốc bàn giao | Branch / PR | `HEAD SHA` | `apps/` Tree SHA | `CI Synthetic Merge SHA` (`parents`) | Phạm vi & Nhận định Provenance của T1C |
|---|---|---|---|---|---|
| **Mốc 0 — Entry Baseline** | `origin/main` | `1913d2bd5ebfdc894eaa492b46be502dbdf44808` | `64fb67df...` (code `a7cdd0f3`) | Không áp dụng (`push` trên `main`) | Nền P0/P1 đã đóng; Entry Gate `G2-01` (`E2-01..E2-10`) **`PASS`** (bảo toàn). |
| **Mốc 1 — Tích hợp P2 Đợt 1 (`PR #7`)** | `codex/p2-ui` (backend `6ef2e2e2`, E2E `d3da46b1`) | `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` | `4a395ae619f95a1feb0fbf9ec189063af2938f76` | `6b27fa38b46bc85bc876bc6b0afcc205de9326b2` (`1913d2b` + `26b0237`) | T1C đo toàn trình runtime độc lập (`t1c-p2-runtime-26b0237.json`); phát hiện `F-P2-01..F-P2-06` (`9/15 PASS`, `6/15 FAIL`). |
| **Mốc 1b — Tích hợp P2 Đợt 1b (`PR #7`)** | `codex/p2-ui` (backend `580a45f0` / `49537317`) | `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10` | `4a395ae619f95a1feb0fbf9ec189063af2938f76` (`0` byte diff trong `apps/` so với `26b0237`) | `faa6dfb33e4972a9aa5488858cfd0a46e0e45b04` (`1913d2b` + `ffe74df`) | Đóng **`F-P2-03`** (`61/61` HTTP assertions + `9/9` probe selftests); còn mở `F-P2-01, 02, 04, 05, 06`. |
| **Mốc 2a — Backend Remediation (`PR #6`)** | `codex/p2-foundation` | `16e69438a30deaa8de723594e3beaea92e91fecd` | `3c3a8a6e8c29cfca49e8628ba104bec1dd2aec99` | — | T1B sửa phần Backend cho `F-P2-01/02/04/05/06` + `OBS-P2-01`, thêm `test_p2_t1c_regressions.py` (`5/5`) và nâng CI HTTP lên `81/81` assertions. |
| **Mốc 2b — Code E2E Tích hợp Đợt 2** | `codex/p2-ui` (`ffe74df` + `16e69438`) | `0f3c7311c75aa3c49c3953a9b8cd64e45809d008` | `f3b6db3ff80b3371ea5b991d7e5aaa408533007b` | — | T1A gộp `16e69438` + sửa UI `F-P2-04A`/`F-P2-05`/`F-P2-01` + `O-02` (`ownership_ui.py`) và đo mới HTTP E2E (`6/6`) + Browser E2E (`22/22`). |
| **Mốc 2c — Bản Tích hợp Cuối cần Nghiệm thu (`PR #7` `HEAD`)** | `codex/p2-ui` | **`7e789eb1137fd8ee6c07a1971bf6b3151287b8e4`** | **`f3b6db3ff80b3371ea5b991d7e5aaa408533007b`** (**khớp 100% `0f3c731:apps`**, `0` byte diff trong `apps/`) | **`634606710626aa389b1a5e2af846ea03ff91b421`** (`1913d2b` + `7e789eb`) | T1C xác minh `195/195` file `crm_cskh` thực chạy trong container khớp băm SHA-256 `13fa30f831c4d149602e3aa09e89884a54b44415fa2cbad8b4b177b11f42b7d9`, chạy `migrate × 2` trên snapshot `ffe74df` và đóng toàn bộ `F-P2-01..F-P2-06` (**`15/15 PASS`**). |

### 1.2. Phân tách 3 Tầng Bằng chứng trên `7e789eb` (T1C tự tải, tự băm SHA-256 và tự parse)

1. **Tầng 1 — CI GitHub Actions trên `PR #7` `HEAD` `7e789eb` (synthetic merge `634606710626aa389b1a5e2af846ea03ff91b421`):**
   - **P0 `#41784039800`:** Artifact `13301205353` (`157,529` B, SHA-256 `3d1e72170f6f81a1bd970bfc4e348d77f2242ef05c76a5a0482d06ef398f40e7`) — `107/107` JUnit XML (`99` core + `8` aux) `PASS`.
   - **P1 `#41784039850`:** Artifact `13300257742` (`61,296` B, SHA-256 `d8962c07eb9df2c5c3ecdab0c92d71cf7c2c77094f953484a6d262c98052d1ce`) — `221/221` JUnit XML (`209` backend + `12` UI) + `170/170` HTTP `PASS`.
   - **P2 `#41784039795`:** Artifact `13300721301` (`46,905` B, SHA-256 `2baa6e5a97a7c168ffbe288027a4932239095050329d5f4e0e1ae343e73ac0bd`):
     - `30/30` dòng trong `SHA256SUMS.txt` khớp tuyệt đối (`0` mismatch); `workflow-metadata.json` ghi nhận `commit_sha = 634606710626aa389b1a5e2af846ea03ff91b421` (`parents = 1913d2b + 7e789eb`).
     - AST ≡ Inventory ≡ JUnit XML: **`38/38` P2 backend methods** (`8` modules, gồm `5/5` method mới trong `TestP2T1cRegressions`) + **`9/9` P2 UI methods** (`1` module) = **`47/47 PASS`** (`0` fail, `0` error, `0` skip).
     - `p2-http-transport.json` & `p2-http-completeness-gate.json`: **`81/81` HTTP verification cases có assertion `PASS`** (`setup_observation = 0`, `unasserted = 0`, `final_close_case_asserted_pass = true`).
     - `p2-reconcile-test-site.json`, `p2-reconcile-ci-site.json` và `p2-backup-restore-smoke.json`: đều **`PASS`** (`ok = true`).
2. **Tầng 2 — Bằng chứng E2E tự đo của T1A/T1B trên `0f3c731` (`0f3c731:apps == 7e789eb:apps`, T1C kiểm toán độc lập):**
   - [`deploy/p2/evidence/http-e2e-report.json`](../../../../deploy/p2/evidence/http-e2e-report.json) (`6,533` B, SHA-256 `f2c0db36f1f689d1049f1e74f953282fd07a4d608855bf50e1a86deef859631f`): `worktree_head_sha = 0f3c7311c75aa3c49c3953a9b8cd64e45809d008`, `6/6` cụm kiểm tra HTTP E2E `PASS`, `0` SĐT thô, `0` tiền tố `[H:...]`.
   - [`deploy/p2/evidence/browser-manifest.json`](../../../../deploy/p2/evidence/browser-manifest.json) (`10,981` B, SHA-256 `08ace23260ea7dc1d1e68d43e9d814886c80b7dc258f72e57d26262db8f0ddbc`): **`22/22` kịch bản Chromium `B2E-01..B2E-22` `PASS`** (bổ sung `B2E-18..22` cho phân trang server-side nhiều trang, lọc `connected` tìm thấy mục ở trang 2, lọc `Hoàn tất` theo nhân viên, tiến độ cùng ngày khác múi giờ trình duyệt, và timeline đóng/mở lại sạch `[H:...]`); cả `22/22` ảnh PNG tồn tại thực trên đĩa (`17,290`–`312,531` B), `0` SĐT thô.
3. **Tầng 3 — Bằng chứng Runtime Độc lập của T1C trên `crm-cskh-t1c-p2` (`t1cp2.localhost:18320`):**
   - Trước khi đồng bộ code hay migrate, T1C đã chụp cứng bản sao lưu dữ liệu `ffe74df` (`20261104_134426-t1cp2_localhost-database.sql.gz`, SHA-256 `cf721bd307948760901934f34ffb345f3b939ac5d5583b0b225eba4f3838aab4`: `203` Leads, `209` WIs, `843` Attempts, `284` History rows chứa `39` dòng `[H:<16hex>]` và `40` dòng có SĐT khách cũ).
   - Đồng bộ `apps/crm_cskh` tại `7e789eb` vào `/workspace/frappe-bench/apps/crm_cskh` trong container, xác minh `195` file khớp băm SHA-256 `13fa30f831c4d149602e3aa09e89884a54b44415fa2cbad8b4b177b11f42b7d9`, chạy **`bench --site t1cp2.localhost migrate` × 2** trực tiếp trên dữ liệu cũ, sau đó thực thi toàn bộ kịch bản kiểm định HTTP/ORM/Concurrency/Restore lưu tại [`artifacts/t1c-p2-delta-runtime-7e789eb.json`](./artifacts/t1c-p2-delta-runtime-7e789eb.json) (`all_passed = true`, `0` PII/secret).

---

## 2. Kết luận Nghiệm thu 15 Cổng `G2-01 → G2-15` (Đánh số chuẩn `docs/root-plan/P2.md` §17)

| Gate (`P2.md §17`) | Tiêu chí cốt lõi | Bằng chứng Xác minh Độc lập của T1C (Baseline bảo toàn + Delta trên `7e789eb`) | Phán quyết |
|---|---|---|---|
| **G2-01 — Entry provenance** | P0/P1 `GO`, đúng version lock (`frappe 15.123.1`, `crm 1.87.0`), `E2-01..E2-10` đạt | Bảo toàn từ `main` `1913d2b`: CI `#41658199683` & `#41658199861` + `21/21` kiểm chứng sống tại [`t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json) | **`PASS`** |
| **G2-02 — Schema/migration** | DocType P2, custom fields, indexes/unique keys MariaDB, `migrate × 2` trên dữ liệu cũ & `backup/restore` an toàn | Bảo toàn `migrate × 2` từ P1 (`108` WIs) + xác minh delta trên `7e789eb`: `migrate × 2` trên dữ liệu `ffe74df` (`284` History rows, `31` closed WIs) tạo đủ `private_reason`, `payload_hash`, `owner_at_close`, làm sạch `39/39` `[H:...]` và `40/40` SĐT trong `reason`, bảo toàn idempotency cũ/mới; `backup/restore` bản dump `610.7 KiB` sang `t1cp2-restore.localhost` đạt `reconcile_p2_invariants().ok = true` | **`PASS`** |
| **G2-03 — Lifecycle** | Vòng đời `assigned` / `in_progress` / `on_hold` / `closed` + `reopen` đồng bộ toàn hệ thống, tách khỏi `CRM Lead.status` | Đã đóng **[`F-P2-02`](#f-p2-02)** (`run_history.runs` giữ nguyên phân bổ lịch sử `{a1: 3, a2: 1}` qua `in_progress`/`on_hold`/`reassign`/`closed`/`revoke` và tách bạch `active_owned = {a1: 2}`) và **[`F-P2-04B`](#f-p2-04)** (`list_work_items` lọc `closed` theo `owner_at_close` vs `closed_by` + thu hồi tức thời khi Lead sang đội khác) | **`PASS`** |
| **G2-04 — Outcome taxonomy** | Danh mục `12` mã tiếng Việt chuẩn hóa, ràng buộc `note`/`followup`/kênh, quản trị DNC đúng `ADR-P2-07` & giữ snapshot lịch sử | Bảo toàn `12` mã tiếng Việt + snapshot `v1 → v2`; đã đóng **[`F-P2-06`](#f-p2-06)**: chặn **`75/75` (`HTTP 403`)** mọi thao tác insert/set/clear/spoof trực tiếp 4 trường `cskh_dnc_*` qua REST v1/v2, `frappe.client.*`, Desk `savedocs` trên cả `Staff`/`Leader`/`BOD`/`CRM Admin`/`Administrator`, giữ nguyên sửa thông tin thường của `CRM Lead` (`8/8 HTTP 200`) và kênh DNC có audit (`record_attempt`, `settings.set_lead_dnc`, `reopen_work_item(override_dnc=1)`) | **`PASS`** |
| **G2-05 — Contact Attempt** | Sự kiện bất biến (append-only), `performed_by = session.user`, chặn giả mạo & chặn backdate sai khoảng sở hữu | Bảo toàn từ `26b0237` + tái kiểm delta trên `7e789eb`: `PUT`/`DELETE` trên `/api/resource/CSKH Contact Attempt/<id>` dưới `cskh.admin` đều bị chặn `403`; giả mạo `performed_by` / `verification_status` bị chặn `403`; backdate lùi vào khoảng cũ trên `A → B → A` bị chặn `422 INVALID_FOLLOWUP_TIME` | **`PASS`** |
| **G2-06 — Unsuccessful ≠ Connected** | `no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `wrong_number`, hủy hẹn không bao giờ tăng `connected` | Bảo toàn từ `26b0237` + kiểm chứng thêm trên `7e789eb` (`wi_start`: `no_answer` + `message_sent_no_reply` → `unreached_attempts = 2`, `connected_attempts = 0`, `connected_work_items = 0`) | **`PASS`** |
| **G2-07 — Atomic & idempotent** | Ghi nguyên tử `Attempt + CRM Task + Work Item`, idempotent khi retry, `409` khi đổi payload/lệch revision, an toàn với `1020`/`1213` | Bảo toàn từ `26b0237` + tái kiểm delta trên `7e789eb`: `N=6` luồng `Barrier(6)` cùng key → `1` fresh `200` + `5` replay `200` (`1` hàng DB); tiêm lỗi giữa giao dịch rollback sạch `0` Attempt/`0` Task mồ côi; `api/ownership.py` đã bọc `_run_with_tx_retry` đóng luôn `OBS-P2-01` (`3/3` race `Barrier(2)` đạt `0` HTTP `500`/`400`) | **`PASS`** |
| **G2-08 — Native reuse** | Tái sử dụng nguyên bản `CRM Call Log`, `CRM Task`, `FCRM Note` (`0` sửa upstream `frappe`/`crm`), không đếm đôi | `git status` trên `frappe` (`v15.123.1`) & `crm` (`v1.87.0`) sạch `100%` (`0` file upstream bị sửa); bảo toàn toàn bộ bằng chứng `Call Log` / `Task` / `Note` từ `26b0237` | **`PASS`** |
| **G2-09 — Follow-up flow** | Tối đa `1` open callback `CRM Task`/WI, đổi hạn/hủy hẹn bắt buộc lý do, `complete` bắt buộc `attempt_id`, đúng ranh giới ngày `UTC+07` | Bảo toàn runtime đa múi giờ từ `26b0237` + chạy local `node deploy/p2/test_p2_ui_timezone_and_contracts.mjs` trên `7e789eb` đạt `PASS` | **`PASS`** |
| **G2-10 — Transfer safety** | Chuyển `A → B` giữ nguyên `due_date` và trạng thái `in_progress`/`on_hold`, thu hồi sạch quyền của `A`, chặn backdate `A → B → A` | Bảo toàn kiểm chứng 14 mặt phẳng `WP-OBS-01` + kiểm chứng mới trên `7e789eb`: kể cả với Work Item đã `closed`, ngay khi Lead phát sinh Work Item mới sang Đội B, cả `staff.a2` (`owner_at_close`) lẫn `leader.a` (`closed_by` & Team A leader) lập tức bị thu hồi quyền đọc (`403` trên cả 5 điểm truy cập `L-23` / `SEC2-07`) | **`PASS`** |
| **G2-11 — Correction/audit** | `CSKH Contact Attempt` gốc bất biến 100%, sửa sai chỉ qua `CSKH Attempt Correction` append-only (`approve`/`reject`/`void`/`restore`) | Bảo toàn từ `26b0237` + tái kiểm chuỗi `correct` (`no_answer → converted`) → `void` → `admin restore` trên `7e789eb` cập nhật chính xác bộ đếm `progress_summary` (`connected_work_items: 2 → 3`, `attempted_work_items: 3 → 2 → 3`) | **`PASS`** |
| **G2-12 — Permissions & PII** | Đạt trọn vẹn `SEC2-01..SEC2-16` trên cả Custom API và REST/client/Desk/filter/search/export (`0` High/Critical, `BOD` `0` PII) | **`16/16` kịch bản `SEC2-01..SEC2-16` đạt `PASS`**! Đã đóng cả 2 lỗi `HIGH`: **[`F-P2-01`](#f-p2-01)** (`cskh.bod` nhận `0` PII / `0` `private_reason` / `0` `payload_hash` trên cả 5+ mặt phẳng + `12/12` filter/order/group/search oracles trả `403`) và **[`F-P2-06`](#f-p2-06)** (`75/75` bypass vectors trả `403`) | **`PASS`** |
| **G2-13 — UI tiếng Việt** | Toàn bộ màn `/cskh/*` hiển thị tiếng Việt tự nhiên, lọc/phân trang chính xác, không rò mã kỹ thuật | `check_translations_audit.py` đạt `0` lỗi (`446` chuỗi dùng, `912` dòng `vi.csv`); đã đóng **[`F-P2-04A`](#f-p2-04)** (`11/11` `care_state` phân trang SQL nhiều trang khớp `COUNT(*)` 100%, mục `7mikecehjd` truy xuất đầy đủ trên `connected` `5` trang) và **[`F-P2-01`](#f-p2-01)** (`0` `[H:...]` trên cả dữ liệu cũ và mới; Browser `B2E-18..22` `PASS`) | **`PASS`** |
| **G2-14 — Progress basic** | Trang Tiến độ khớp bảng nguồn SQL, đúng lọc ngày giờ `UTC+07`, tách rõ số lượng KH/WI đã/chưa liên hệ/kết nối (`P2.md §12.4`) | Đã đóng **[`F-P2-05`](#f-p2-05)**: `date_from=2026-10-15&date_to=2026-10-15` chuẩn hóa nửa mở `[2026-10-15 00:00:00, 2026-10-16 00:00:00)` (`Asia/Ho_Chi_Minh`), bao trọn `00:00:00`, `12:30:00` (WI tạo trước kỳ), `23:59:59`, loại trừ `2026-10-14 23:59:59` & `2026-10-16 00:00:00`; tách bạch `attempted_work_items`/`unattempted_work_items`/`connected_work_items` với `effective_attempts`, tách `dnc_leads` (`24`) với `dnc_work_items` (`27`), khớp lớp phủ `correct`/`void`/`restore` | **`PASS`** |
| **G2-15 — Handoff P3** | Toàn bộ gate `G2-01..G2-14` đạt `PASS`, CI xanh đủ assertion, hoàn tất `WP10` & hồ sơ bàn giao P3 | `G2-01..G2-14 = 14/14 PASS`; CI P0 `#41784039800` (`107/107`), P1 `#41784039850` (`221/221` + `170` HTTP), P2 `#41784039795` (`38+9` XML + `81/81` HTTP assertions + `30/30` SHA-256 `PASS`); `docs/P2-Exit-Report.md` §11 đã đồng bộ đúng đánh số `G2-01..G2-15` theo `P2.md §17` | **`PASS`** |

---

## 3. Sổ cái Xác minh Đóng Toàn bộ 6 Finding (`F-P2-01 → F-P2-06` = `6/6 CLOSED`)

### <a id="f-p2-01"></a>`F-P2-01` (`HIGH` → **`CLOSED` trên `7e789eb`**) — Chặn triệt để rò rỉ PII & `private_reason`/`payload_hash` sang `CSKH BOD Viewer` trên cả 5+ mặt phẳng + làm sạch dữ liệu cũ & tiền tố `[H:<16hex>]`

- **Phát hiện gốc (`26b0237` / `ffe74df`):** Auto-close `do_not_contact` chép `clean_note` (chứa tên/SĐT khách) sang `CSKH Assignment History.reason` (`permlevel=0`), đồng thời `close_work_item` / `reopen_work_item` chép lý do `permlevel=1` kèm tiền tố `[H:<16hex>]` sang `reason`, khiến `cskh.bod@example.test` đọc được trên cả 5 mặt phẳng và lộ `[H:...]` trên UI.
- **Bản sửa đã kiểm chứng (`16e69438` + `0f3c731` → `7e789eb`):**
  1. Tách schema `CSKH Assignment History`: `reason` (`permlevel=0`, chỉ lưu nhãn nghiệp vụ trung tính tiếng Việt), `private_reason` (`permlevel=1`, `read_only=1`), `payload_hash` (`permlevel=1`, `hidden=1`, `read_only=1`); auto-close DNC không bao giờ chép `note` tự do của khách sang `reason`/`private_reason`/`completion_reason`/`cskh_dnc_reason`.
  2. Migration `migrate_p2_assignment_history_privacy()` trong `patches/v2_0/p2_schema_and_seeds.py` tách tiền tố `[H:<16hex>]` sang `payload_hash`, di chuyển lý do cũ sang `private_reason` và thay thế `reason` bằng nhãn trung tính.
  3. Đưa `private_reason` & `payload_hash` vào `PERMLEVEL_PROTECTED_FIELDS["CSKH Assignment History"]` (`p1_permissions.py`), đồng thời siết chặt `client_get_value`, `client_get_count` (`guarded_crm.py`), `reportview.*` (`guarded_reporting.py`) và `api/ownership.py::history`.
- **Bằng chứng Runtime Độc lập của T1C (`02_f_p2_01_privacy_and_migration = PASS`):**
  - Chạy `migrate × 2` trên snapshot `ffe74df` thật (`284` dòng lịch sử): toàn bộ **`39/39` dòng `[H:<16hex>]` còn `0`**, **`40/40` dòng có SĐT trong `reason` công khai còn `0`**.
  - Kiểm tra dưới `cskh.bod@example.test` (`0` `System Manager`) trên cả dòng cũ đã di trú (`kqn0kkf01l`, `kqorngpu29`, `ig1nup16gb`) và dòng mới tạo sau di trú (`DNC` auto-close không truyền `completion_reason`, `reopen_work_item`, `close_work_item` chứa chuỗi sentinel PII) qua **5+ mặt phẳng** (`ownership.history`, `/api/resource/...` list+doc, `/api/v2/document/...` list+doc, `frappe.client.get_list/get/get_value`, `reportview.get`, `form.load.getdoc`, `export_query=403`, `data_export=403`) trên cả chuỗi JSON thô lẫn JSON đã giải mã Unicode: **`0` rò sentinel/SĐT/tên khách/`payload_hash`**, không có oracle phân biệt có/không có `private_reason`.
  - Kiểm tra **`12/12` kênh filter / `or_filters` / `order_by` / `group_by` / `get_value` / `get_count` / `reportview` aggregate / `search_link` / `search_widget`** trên `private_reason` & `payload_hash` dưới `cskh.bod`: **toàn bộ `12/12` trả `HTTP 403`**.
  - **Positive controls (`cskh.admin`, `leader.a`, `staff.a1`):** đọc đầy đủ lý do điều hành trên `ownership.history` & `list_attempts(include_milestones=1)` với **`0` tiền tố `[H:<16hex>]`** trên cả dữ liệu cũ và mới; replay idempotency trên dòng cũ di trú và dòng mới đều đạt `200` (cùng payload) và `409 IDEMPOTENCY_CONFLICT` (đổi payload).

---

### <a id="f-p2-06"></a>`F-P2-06` (`HIGH` → **`CLOSED` trên `7e789eb`**) — Khóa cứng mọi thao tác chèn/sửa/xóa trực tiếp 4 trường DNC trên `CRM Lead` qua Generic APIs, bảo toàn sửa Lead thông thường & kênh DNC có kiểm toán

- **Phát hiện gốc (`26b0237` / `ffe74df`):** `Sales User` có `DocShare(write=1)` trên `CRM Lead` gọi `PUT /api/resource/CRM Lead/<id>` bật/gỡ trực tiếp `cskh_do_not_contact` (`HTTP 200`) không qua `record_attempt` và vượt mặt quyền `override_dnc` của `CSKH CRM Admin`.
- **Bản sửa đã kiểm chứng (`16e69438` → `7e789eb`):** Đặt `read_only=1` cho cả 4 trường (`cskh_do_not_contact`, `cskh_dnc_reason`, `cskh_dnc_at`, `cskh_dnc_work_item`), khóa bằng `ContextVar` `_CSKH_AUTHORIZED_LEAD_DNC_MUTATION` + `doc_events["CRM Lead"]["validate"]` (`validate_crm_lead_dnc_fields`) + `auth_hooks` (`enforce_lead_dnc_request_guard`) + override `client_set_value` / `client_bulk_update`.
- **Bằng chứng Runtime Độc lập của T1C (`03_f_p2_06_dnc_lockdown_and_lead_writes = PASS`):**
  - Thử **`15` vector ghi/xóa/giả mạo trực tiếp** (REST v1 `POST` insert từng trường trong 4 trường DNC, REST v1 `PUT` set `0→1`, REST v1 `PUT` full-roundtrip kèm cờ giả mạo `cskh_in_attempt_service=1` & `_CSKH_AUTHORIZED_LEAD_DNC_MUTATION=1`, REST v1 `PUT` full-roundtrip clear `1→0`, REST v2 `POST` & `PATCH`, `frappe.client.insert`, `set_value` đơn & dict, `client.save` full-doc clear, `client.bulk_update`, Desk `form.save.savedocs` full-doc clear) trên **cả `5` actor** (`staff.a1`, `leader.a`, `cskh.bod`, `cskh.admin`, và cả **`Administrator`**): **`75/75` request trả `HTTP 403`**, **`0` thay đổi trái phép trong MariaDB**!
  - **Không làm hỏng sửa `CRM Lead` thông thường:** Cập nhật `first_name` qua cả `4` kênh (`PUT` từng phần, `GET → PUT` full-roundtrip, `frappe.client.set_value`, `GET → savedocs` full-roundtrip) trên **cả Lead không DNC lẫn Lead đang bật DNC (`cskh_do_not_contact = 1` có microsecond thực trong DB)** đều đạt **`8/8 HTTP 200`**.
  - **Các luồng DNC chính thống có audit hoạt động trơn tru:** `workflow.record_attempt(outcome_code="do_not_contact")` (`200`), `workflow.reopen_work_item(override_dnc=True)` dưới `cskh.admin` (`200`), và `settings.set_lead_dnc` (`403` với `staff`/`leader`/`bod`; `200` bật & `200` gỡ có audit dưới `cskh.admin`).

---

### <a id="f-p2-02"></a>`F-P2-02` (`MEDIUM` → **`CLOSED` trên `7e789eb`**) — Bảo toàn số lượng phân bổ lịch sử theo đợt chạy (`run_history.runs`) đồng thời tách bạch `active_owned` theo đúng phạm vi đội

- **Bằng chứng Runtime Độc lập của T1C (`04_f_p2_02_run_history = PASS`):**
  - Trên chiến dịch riêng Đội A dùng nhánh fallback DB (`actual_json = "{}"`, `3` mục giao `staff.a1`, `1` mục giao `staff.a2`), sau khi chuyển trạng thái lần lượt sang `in_progress`, `on_hold`, `reassign(staff.a1 → staff.a2)` rồi `close`, và `revoke`: `run_history.runs` trả `allocation_semantics = "historical_run_allocation"`, **giữ nguyên `assigned = {"staff.a1@example.test": 3, "staff.a2@example.test": 1}`** cho cả `cskh.admin` và `leader.a`, đồng thời trả chính xác sở hữu hiện hành **`active_owned = {"staff.a1@example.test": 2}` (`active_owned_total = 2`)**.
  - Trên chiến dịch dùng chung (`team_scope = NULL`, `2` mục Đội A + `2` mục Đội B, sau đó `reassign` `1` mục từ `staff.a1` sang `staff.b1`): `cskh.admin` thấy `assigned = {a1: 2, b1: 2}` & `active_owned = {a1: 1, b1: 3}`; `leader.a` chỉ thấy `{a1: 2}` & `{a1: 1}` (`0` lộ `staff.b1`); `leader.b` chỉ thấy `{b1: 2}` & `{b1: 3}` (`0` lộ `staff.a1`).

---

### <a id="f-p2-04"></a>`F-P2-04` (`MEDIUM` → **`CLOSED` trên `7e789eb`**) — Phân trang SQL server-side cho mọi `care_state` trên `/cskh/my-customers` & lọc `closed` phân biệt `owner_at_close` vs `closed_by` (giữ chặt thu hồi quyền cross-team `L-23`)

- **Bằng chứng Runtime Độc lập của T1C (`05_f_p2_04_care_state_and_closed_filtering = PASS`):**
  - Duyệt toàn bộ các trang (`page_size = 20`) dưới `staff.a1` trên đủ **`11` giá trị `care_state`** (`all: 126 / 7 trang`, `unattempted: 23 / 2 trang`, `not_contacted: 23 / 2 trang`, `attempted: 103 / 6 trang`, `attempted_not_connected: 17 / 1 trang`, `connected: 86 / 5 trang`, `on_hold: 21 / 2 trang`, `followup_due: 14 / 1 trang`, `followup_overdue: 1 / 1 trang`, `closed: 44 / 3 trang`, `do_not_contact: 25 / 2 trang`): **tổng hợp các phần tử duy nhất qua các trang khớp tuyệt đối `100%` với `total` (`COUNT(*)`)**, mục `7mikecehjd` (bị che khuất ở Đợt 1) được trả đầy đủ trong tập trang `connected`, và `care_state` sai trả `400 INVALID_REQUEST`.
  - Kiểm chứng đầy đủ 3 ca đóng hồ sơ: (1) `staff.a1` tự đóng; (2) `leader.a` đóng thay `staff.a2` (`owner_at_close = staff.a2`, `closed_by = leader.a`); (3) chuyển `staff.a1 → staff.a2` trước khi `staff.a2` đóng (`staff.a1` bị từ chối `403` không đọc lại được WI đã chuyển trước khi đóng; bộ lọc `owner_at_close`, `closed_by` và `status=closed&assignee=staff.a2` dưới `leader.a` trả chính xác từng tập tương ứng).
  - **Không mở lại quyền cross-team (`L-23` / `SEC2-07`):** Ngay khi Lead của hồ sơ đã đóng ở Đội A được phân bổ Work Item mới sang `staff.b1` (Đội B), cả `staff.a2` (`owner_at_close`) lẫn `leader.a` (`closed_by` & Team A leader) lập tức biến mất khỏi danh sách `closed` và nhận **`HTTP 403` trên cả 5 kênh** (`get_work_item`, `list_attempts`, `ownership.history`, `/api/resource/CSKH Work Item/<id>`, `/api/resource/CRM Lead/<id>`), trong khi positive control `staff.b1` & `cskh.admin` đọc `200`.

---

### <a id="f-p2-05"></a>`F-P2-05` (`MEDIUM` → **`CLOSED` trên `7e789eb`**) — Biên ngày nửa mở `[00:00:00, next_day 00:00:00)` theo `Asia/Ho_Chi_Minh`, bộ đếm `DISTINCT Work Item` vs `Attempt` vs `DNC Lead` & đối soát lớp phủ `Correction`

- **Bằng chứng Runtime Độc lập của T1C (`06_f_p2_05_progress_summary = PASS`):**
  - Dựng tập dữ liệu định thời chính xác theo `Asia/Ho_Chi_Minh` quanh ngày mục tiêu `2026-10-15`:
    - `2026-10-14 23:59:59` (`1` giây trước đầu ngày → bị loại khỏi lượt liên hệ trong kỳ);
    - `2026-10-15 00:00:00` & `08:30:00` (`2` lần chưa kết nối trên cùng `1` WI → tính vào kỳ);
    - Work Item tạo trước kỳ (`2026-10-05 09:00:00`) có `1` lần kết nối lúc `2026-10-15 12:30:00` → đưa vào `cohort_work_items` và `connected_work_items`;
    - `2026-10-15 23:59:59` (`1` giây cuối ngày → tính vào kỳ);
    - `2026-10-16 00:00:00` (đầu ngày kế tiếp → bị loại chính xác nhờ `end_bound_mode = "exclusive"`).
  - Truy vấn cùng ngày `date_from=2026-10-15&date_to=2026-10-15`: `period` trả `["2026-10-15 00:00:00", "2026-10-16 00:00:00")` (`exclusive`); bộ đếm của `staff.a1` & `leader.a` khớp tuyệt đối toán học (`cohort_work_items = 5`, `attempted_work_items = 3` $\neq$ `effective_attempts = 4`, `unattempted_work_items = 2`, `connected_work_items = 2`, `connected_attempts = 2`, `unreached_attempts = 2`); `cskh.bod` gộp đúng Đội A + Đội B (`cohort = 6`, `attempted_wis = 4`, `connected_wis = 3`, `effective_attempts = 5`, `0` PII).
  - Chuỗi datetime tường minh + ISO UTC (`2026-10-15T05:30:00Z` $\equiv$ `12:30:00+07`) dùng biên `inclusive` chính xác (`effective_attempts = 3`, `attempted_work_items = 2`); ngày không hợp lệ (`2026-02-30`, `not-a-date`, `date_from > date_to`) đều bị chặn `400 INVALID_REQUEST`.
  - Khi `1` `CRM Lead` DNC có `2` Work Item đóng ở 2 chiến dịch: `dnc_leads` (`24`) tách biệt chính xác với `dnc_work_items` (`27`), và UI `progress.js` gắn đúng nhãn `Số khách hàng ngừng liên hệ (DNC)`.
  - Chuỗi hiệu chỉnh `correct` (`no_answer → converted` làm `connected_work_items` tăng `2 → 3`) → `void` cả 2 attempt (`attempted_work_items` giảm `3 → 2`, `unattempted_work_items` tăng `2 → 3`) → `admin restore` (`attempted_work_items` quay về `3`) cập nhật tức thời không đếm trùng.

---

### <a id="f-p2-03"></a>`F-P2-03` (`MEDIUM` → **`CLOSED` từ `ffe74df`, bảo toàn trên `7e789eb`**) — Cổng đầy đủ assertion HTTP CI

- Bảo toàn kết luận đóng từ `ffe74df` (`61/61`) và xác minh nâng cấp trên `7e789eb` (`CI P2 #41784039795`): **`total_records = 81`, `verification_records = 81`, `asserted_cases = 81`, `passed_assertions = 81`, `setup_observation = 0`, `unasserted = 0`, `final_close_case_asserted_pass = true`**, cộng `9/9` unit selftests của `test_ci_p2_http_probe.py` (tách riêng khỏi độ phủ nghiệp vụ).

---

## 4. Trạng thái Các Ghi nhận Không chặn (`OBS-P2-01 → OBS-P2-03` & `O-01 / O-02`)

1. **`OBS-P2-01` (`RESOLVED` trên `16e69438` / `7e789eb`):** `api/ownership.py` đã bọc `_run_with_tx_retry` cho `assign`, `reassign`, `revoke`. T1C chạy `3` lượt đua `Barrier(2)` trực tiếp qua HTTP giữa `workflow.record_attempt` và `ownership.reassign` trên `7e789eb`: cả `3/3` lượt đều kết thúc sạch (`[(409, 200), (409, 200), (200, 200)]`, **`0` HTTP `500` / `0` HTTP `400`**).
2. **`OBS-P2-02` *(Không chặn — chuyển giao đầu vào tối ưu cho P3)*:** Hồ sơ benchmark `10k` WIs / `50k` Attempts (`p2-benchmark-20261103.json`) đo ở tầng Python service trong `bench execute` (`0` lỗi, `reconcile ok = true`, các truy vấn tác nghiệp Staff/Leader đi đúng index `7–26 ms`), nhưng truy vấn `progress_summary` toàn kỳ `50k` attempts của `BOD` vẫn quét bảng (`type = "ALL"`, `~2.52s`) và chiếu overlay `Correction` trên Python — khuyến nghị dùng bảng tổng hợp/chỉ mục theo kỳ khi triển khai báo cáo SLA/KPI quy mô lớn ở P3.
3. **`OBS-P2-03` *(Không chặn — ranh giới kiến trúc upstream Frappe v15)*:** P0/P2 đã che sạch `/private/files/...` khỏi `get_attachments`/`get_activities` và trả `403`/`404` cho cựu Leader và file do hệ thống/người khác tạo; hành vi mặc định `File.owner == user` của Frappe v15 được giữ nguyên theo đúng nguyên tắc `0` sửa core upstream.
4. **Mục theo dõi từ P1 (`O-02` & `O-01`):** T1C xác nhận `O-02` (`ownership_ui.py` xét `latest_version` theo từng `user` và tính tải trên `('assigned', 'in_progress', 'on_hold')`) đã được sửa và kiểm chứng trên `7e789eb` (và được mô tả trung thực trong `docs/P2-Exit-Report.md` §12.2); `O-01` (import chunking) giữ nguyên trong backlog kỹ thuật không chặn.

---

## 5. Khuyến nghị Nghiệm thu của T1C gửi T0 / Owner

- **Kết luận độc lập của T1C trên `PR #7` `HEAD` `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` (`apps/` tree `f3b6db3ff80b3371ea5b991d7e5aaa408533007b`, CI synthetic merge `634606710626aa389b1a5e2af846ea03ff91b421`):**
  - Toàn bộ **`15/15` cổng `G2-01 → G2-15` đạt `PASS`**.
  - Toàn bộ **`24/24` kịch bản `T2-01..T2-24`** và **`16/16` kịch bản `SEC2-01..SEC2-16` đạt `PASS`** (`0` lỗi `Critical`/`High`/`Medium`/`Low` tồn đọng).
  - Hồi quy P0 (`#41784039800`) & P1 (`#41784039850`) và CI P2 (`#41784039795`) xanh toàn diện, khớp băm SHA-256 `100%`.
- **Đề xuất:** **T1C kính đề nghị T0 / Owner phê duyệt nghiệm thu Giai đoạn P2, merge theo trình tự quản trị nhánh và ký mở cổng `GO P3`** *(trường quyết định điều hành `GO P3` do **T0 / Owner** trực tiếp ký duyệt).*
