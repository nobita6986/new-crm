# Báo cáo Kiểm định Độc lập P2 — Bản Tích hợp Backend + UI & Delta CI (T1C)

- **Vai trò:** **T1C** — Kiểm định độc lập Bảo mật, Chất lượng & Runtime cho **P2 (`CRM-CSKH — Quy trình CSKH, Nhật ký liên hệ & Hẹn chăm sóc lại`)**
- **Tài liệu gốc đối chiếu:** [`docs/root-plan/P2.md`](../../../../docs/root-plan/P2.md) (§0–§22) và [`docs/architecture/version-lock.md`](../../../../docs/architecture/version-lock.md)
- **Môi trường kiểm định P2 cô lập của T1C:**
  - Worktree: `D:/HRP-app/t1c-p2-verify` (detached `HEAD` tại `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce`, sau đó đối chiếu delta trên `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10`; tách rời tuyệt đối khỏi `p2-foundation` của T1B, `p2-ui` của T1A và stack `crm-cskh-p1` cổng `8120` của Owner)
  - Docker Compose Project: `crm-cskh-t1c-p2` (`WEB_PORT=18320`, `SOCKETIO_PORT=19320`, `VITE_PORT=18380`) · Site: `t1cp2.localhost` (và site phục hồi tạm `t1cp2-restore.localhost`)
- **Bộ hồ sơ do T1C sở hữu (`deploy/dev/evidence/p2-t1c/`):**
  1. Báo cáo kiểm định độc lập P2 (tài liệu này): [`t1c-p2-verification-report.md`](./t1c-p2-verification-report.md)
  2. Ma trận bảo mật P2: [`security-matrix-p2.md`](./security-matrix-p2.md)
  3. Sổ cái bằng chứng kiểm thử & hiệu năng P2: [`test-evidence-p2.md`](./test-evidence-p2.md)
  4. Artifact thực nghiệm Entry Baseline (`1913d2b`, bảo toàn nguyên trạng): [`artifacts/t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json)
  5. Artifact thực nghiệm Runtime Tích hợp P2 (`26b0237` ≡ `ffe74df`, cùng `apps` tree `4a395ae619f95a1feb0fbf9ec189063af2938f76`): [`artifacts/t1c-p2-runtime-26b0237.json`](./artifacts/t1c-p2-runtime-26b0237.json)
  6. Artifact kiểm toán phép đo & CI trên `ffe74df`: [`artifacts/t1c-p2-ffe74df-audit.json`](./artifacts/t1c-p2-ffe74df-audit.json)
  7. Manifest & Bảng băm toàn vẹn hồ sơ T1C: [`manifest.json`](./manifest.json) · [`SHA256SUMS.txt`](./SHA256SUMS.txt)
- **Khuyến nghị Nghiệm thu Hiện hành (`2026-11-04` trên `26b0237` và `ffe74df`):**
  - **Cổng đạt (`PASS`):** **`9/15`** (`G2-01`, `G2-02`, `G2-05`, `G2-06`, `G2-07`, `G2-08`, `G2-09`, `G2-10`, `G2-11`).
  - **Cổng chưa đạt (`FAIL`):** **`6/15`** (`G2-03`, `G2-04`, `G2-12`, `G2-13`, `G2-14`, `G2-15`).
  - **Tổng hợp Finding:** **`5` lỗi đang mở** (**`2` `HIGH`**: `F-P2-01`, `F-P2-06`; **`3` `MEDIUM`**: `F-P2-02`, `F-P2-04`, `F-P2-05`) + **`1` lỗi CI đã xác minh `CLOSED` trên `ffe74df`** (`F-P2-03`) + **`3` ghi nhận không chặn** (`OBS-P2-01..OBS-P2-03`).
  - **Khuyến nghị chuyển pha:** **`GO P3 = NO-GO`** *(chờ T1B/T1A khắc phục `F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06` trên 1 SHA tích hợp để T1C tái kiểm đúng phần delta)*.

---

## 1. Phân tách Bạch bạch Provenance Revision & 3 Tầng Bằng chứng

### 1.1. Sổ cái Revision đã kiểm định (tách `HEAD SHA`, `App Tree SHA` và `CI Synthetic Merge SHA`)

| Mốc bàn giao | Branch / PR | `HEAD SHA` | `apps/` Tree SHA | `CI Synthetic Merge SHA` (`parents`) | Phạm vi & Nhận định Provenance của T1C |
|---|---|---|---|---|---|
| **Entry Baseline** | `origin/main` | `1913d2bd5ebfdc894eaa492b46be502dbdf44808` | `64fb67df...` (code `a7cdd0f3`) | Không áp dụng (`push` trên `main`) | Nền P0/P1 đã đóng; Entry Gate `G2-01` (`E2-01..E2-10`) **`PASS`**. |
| **Backend P2 (`PR #6`)** | `codex/p2-foundation` | `6ef2e2e21c9e8265efe43fd5413074b70849a674` → `580a45f0d823267f6845fec083d56ead00d29681` | `21f2fc434a031d50256d5302a0c8247c12a788b8` (không đổi giữa `6ef2e2e` và `580a45f`) | `4030ddf...` → `105718e...` | `580a45f` chỉ sửa `ci_p2_http_probe.py`, `test_ci_p2_http_probe.py`, `.github/workflows/p2.yml` (`0` thay đổi trong `apps/`). |
| **Code E2E UI + Backend** | `codex/p2-ui` | `d3da46b16a6d800d5d8cf866fc06415f9f02253f` | `4a395ae619f95a1feb0fbf9ec189063af2938f76` | — | Commit thực chạy bộ E2E HTTP/Browser tự đo của T1A trên `p2.localhost:8220`. |
| **Tích hợp P2 đợt 1 (`PR #7`)** | `codex/p2-ui` | `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` | `4a395ae619f95a1feb0fbf9ec189063af2938f76` | `6b27fa38b46bc85bc876bc6b0afcc205de9326b2` (`1913d2b` + `26b0237`) | T1C đo toàn trình runtime độc lập (`t1c-p2-runtime-26b0237.json`); CI HTTP thiếu assertion case thứ `61` (`F-P2-03`). |
| **Tích hợp P2 đợt 2 (`PR #7` hiện hành)** | `codex/p2-ui` | `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10` (gộp code gate `4953731` / `580a45f`) | `4a395ae619f95a1feb0fbf9ec189063af2938f76` (**khớp 100% `26b0237:apps`**) | `faa6dfb33e4972a9aa5488858cfd0a46e0e45b04` (`1913d2b` + `ffe74df`) | `git diff 26b0237..ffe74df -- apps/` = **`0` byte**; đóng **`F-P2-03`** (`61/61` HTTP assertions + `9/9` unit selftests); bảo toàn 100% kết quả runtime `apps/` của T1C. |

### 1.2. Phân tách 3 Tầng Bằng chứng (T1C tự tải, tự băm SHA-256 và tự parse)

1. **Tầng 1 — CI GitHub Actions (T1C tự tải nhị phân, kiểm chứng `digest` GitHub & `SHA256SUMS.txt` = `0` lệch):**
   - **Trên `1913d2b` (`main` Entry):**
     - P0 `#41658199683` — Artifact `13240019557` (`150,251` B, SHA-256 `a326e0294435c05cb5980eb133d63df6ecb069f798af16c64f7462f8457e364d`): `99/99` core + `8/8` aux JUnit `PASS`, `18/18` HTTP, `9/9` file-oracle, `74/74` gaps, `backup_restore = success`.
     - P1 `#41658199861` — Artifact `13240299250` (`63,599` B, SHA-256 `ef40ab2df6a27d7be404e3ffd11da3980d4ecc2382f7452cfd8bd9e42b74884b`): `209/209` backend + `12/12` UI JUnit `PASS`, `170/170` HTTP `PASS`.
   - **Trên `26b0237` (`PR #7` synthetic merge `6b27fa38`):**
     - P0 `#41708187912` — Artifact `13267792105` (`150,231` B, SHA-256 `9b15e135c021daa6f4dd1b23dfe7ba6ed42070734fe125f8f62e7bfd9bc6c1f9`): `99/99` core + `8/8` aux `PASS`.
     - P1 `#41708187949` — Artifact `13266323049` (`63,609` B, SHA-256 `a54b31ff2ae0df103215165b084172f0ab5202b0f9f6b6d8d8093c358f209039`): `209/209` backend + `12/12` UI + `170/170` HTTP `PASS`.
     - P2 `#41708187948` — Artifact `13266761682` (`35,355` B, SHA-256 `db061682192bfdf0b768b1fc98acbe0310d4f75252c6cdfeda7fe0efd9ca77e3`): AST ≡ Inventory ≡ JUnit `33/33` backend + `8/8` UI `PASS`; `schema/reconcile/restore PASS`; HTTP có `61` bản ghi nhưng `60` assertion (`missing: ["p2-http-staff-b-close-wi-final"]` → `F-P2-03`).
   - **Trên `ffe74df` (`PR #7` hiện hành, synthetic merge `faa6dfb3`):**
     - P0 `#41759646326` — Artifact `13290306458` (`157,446` B, SHA-256 `4978a6134d092efa6147550976ce9c4eb94b638eb775686071a9ef7e8b55212d`): `107/107` XML `PASS`.
     - P1 `#41759646264` — Artifact `13289439265` (`61,106` B, SHA-256 `000d22866b49063fad9f661fa4a74a9f30cdc413237467c9ff34afc2cfc29892`): `221/221` XML `PASS`.
     - P2 `#41759646273` — Artifact `13289404489` (`36,781` B, SHA-256 `f57b251d58b46c7cc2f092c26459be9858142efdb69b8724be0986ebf4784193`): `33/33` backend + `8/8` UI JUnit `PASS`; **`61/61` HTTP verification cases có assertion `PASS`** (`p2-http-completeness-gate.json` `status = "PASS"`, `final_close_case_asserted_pass = true`); `0` lệch `SHA256SUMS.txt`.

2. **Tầng 2 — Bằng chứng tự đo của T1A & T1B trong repo (T1C kiểm toán độc lập, không gộp lẫn làm runtime của T1C):**
   - **T1A E2E (`deploy/p2/evidence/`):** `http-e2e-report.json` (SHA-256 `ff73e1fe953b01addc2e3c3ca637390f1974f6bf437bbed0e3deaa3309fb0bc1`) & `browser-manifest.json` (SHA-256 `f786a0c2db364f7803060385840bc63c8ce871ca76ad772af1047354117285bc`) đo trên `http://p2.localhost:8220` tại `worktree_head_sha = d3da46b16a6d800d5d8cf866fc06415f9f02253f` (`17/17` ảnh PNG thực trên đĩa `>= 17,290` bytes, `0` rò SĐT).
   - **T1B Runtime & Benchmark (`deploy/dev/evidence/p2-runtime-20261103/`):** `11/11` mục khớp `SHA256SUMS.txt`. Riêng `benchmark/p2-benchmark-20261103.json` (SHA-256 `81c8b2afd4bc2d4279e0d0351099509f795d6cf4b7f2942261f7a84f219ae383`, đo lúc `2026-11-04 00:59:53` trên `p2bench.localhost`, commit đưa vào tại `23a782bd58edaaef3e406fbc2b7e40622fb98d58`, thiếu trường `commit_sha` trong JSON) **gọi trực tiếp hàm Python service qua `bench execute` + `ThreadPoolExecutor(25)` (KHÔNG đi qua live HTTP/WSGI)** — chi tiết phân tích `EXPLAIN` tại `OBS-P2-02` và [`test-evidence-p2.md`](./test-evidence-p2.md).

3. **Tầng 3 — Bằng chứng Runtime Độc lập của T1C trên `crm-cskh-t1c-p2` (`t1cp2.localhost:18320`):**
   - Thực thi trên `apps/` tree `4a395ae619f95a1feb0fbf9ec189063af2938f76` (`26b0237` ≡ `ffe74df`):
     - Di trú trực tiếp từ bản chụp P1 thật (`108` `CRM Lead`, `108` `CSKH Work Item`, `105` `CSKH Active Claim`, `83` `CSKH Assignment History`) qua `bench migrate × 2` (`0` mất dữ liệu, `12` `CSKH Outcome Code` tiếng Việt + `GLOBAL-DEFAULT` `CSKH Policy`, đủ unique/lookup indexes MariaDB).
     - Kiểm thử đa luồng/HTTP sống (`N=6` concurrent same-key retry, race `record_attempt` vs `ownership.reassign`, 2 tab `STALE_WORK_ITEM`), savepoint rollback + post-commit notification isolation, nhánh retry MariaDB `1213`/`1020` (`QueryDeadlockError`), ma trận `12` actor thật (`cskh.admin@example.test` và `cskh.bod@example.test` đều có **`0` `System Manager`**), `125` Attempts phân trang, đối soát SQL độc lập `843` Attempts / `209` Work Items, kiểm tra đa múi giờ (`UTC`, `America/Los_Angeles`, `Asia/Ho_Chi_Minh`), chạy local `test_ci_p2_http_probe.py` (`9/9 PASS`), và `bench backup` → `new-site` → `restore` → `migrate` → replay idempotency trên `t1cp2-restore.localhost` (`PASS`).

---

## 2. Kết luận Nghiệm thu 15 Cổng `G2-01 → G2-15` (Đánh số chuẩn theo `docs/root-plan/P2.md` §17)

*(Lưu ý provenance: Bảng mục 11 trong `docs/P2-Exit-Report.md` của T1A đánh số lệch thứ tự một số gate so với `docs/root-plan/P2.md` §17; T1C áp dụng nghiêm ngặt đánh số gốc của `docs/root-plan/P2.md` §17).*

| Gate (`P2.md §17`) | Tiêu chí cốt lõi | Bằng chứng xác minh độc lập của T1C | Phán quyết |
|---|---|---|---|
| **G2-01 — Entry provenance** | P0/P1 `GO`, đúng version lock (`frappe 15.123.1`, `crm 1.87.0`), `E2-01..E2-10` đạt | CI `main` `#41658199683` & `#41658199861` + `21/21` kiểm chứng sống tại [`t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json) | **`PASS`** |
| **G2-02 — Schema/migration** | DocType P2, custom fields, indexes/unique keys MariaDB, `migrate × 2` trên dữ liệu P1 & `backup/restore` an toàn | `migrate × 2` trên `108` WI P1 (`0` lỗi), `SHOW INDEX` đủ `cskh_attempt_idem_uq`, `cskh_attempt_call_log_uq`, `cskh_corr_idem_uq`; `backup/restore` sang `t1cp2-restore.localhost` khớp `1:1` (`203` Leads, `209` WIs, `843` Attempts) + replay idempotency `PASS` | **`PASS`** |
| **G2-03 — Lifecycle** | Vòng đời `assigned` / `in_progress` / `on_hold` / `closed` + `reopen` đồng bộ toàn hệ thống, tách khỏi `CRM Lead.status` | State machine core, `CSKH Active Claim`, `capacity_usage` (`17 == 17`) và `CRM Lead.status` đạt; **NHƯNG BỊ CHẶN BỞI [`F-P2-02`](#f-p2-02)** (`api/run_history.py:97` lọc cứng `status = 'assigned'`) và **[`F-P2-04B`](#f-p2-04)** (`list_work_items` lọc `status='closed'` kèm `assignee` trả `0` dòng vì `assigned_to` bị xóa `NULL` khi đóng) | **`FAIL`** |
| **G2-04 — Outcome taxonomy** | Danh mục tiếng Việt chuẩn hóa, ràng buộc `note`/`followup`/kênh, quản trị DNC đúng policy `ADR-P2-07` & giữ snapshot lịch sử | `12` mã tiếng Việt, `422` `FOLLOWUP_REQUIRED` / `NOTE_REQUIRED` / `OUTCOME_NOT_COMPATIBLE`, đổi `v1 → v2` giữ nguyên snapshot cũ; **NHƯNG BỊ CHẶN BỞI [`F-P2-06`](#f-p2-06)** (`Sales User` sửa/xóa trực tiếp `CRM Lead.cskh_do_not_contact` qua REST `PUT` không cần Attempt DNC và vượt mặt cổng `override_dnc` của `CSKH CRM Admin`) | **`FAIL`** |
| **G2-05 — Contact Attempt** | Sự kiện bất biến (append-only), `performed_by = session.user`, chặn giả mạo & chặn backdate sai khoảng sở hữu | `before_save`/`on_trash` chặn sửa/xóa kể cả `CRM Admin` (`403`); giả mạo `performed_by` / `verification_status` (JSON & form) bị chặn `403`; tương lai/`>90p` bị chặn `422 INVALID_FOLLOWUP_TIME` | **`PASS`** |
| **G2-06 — Unsuccessful ≠ Connected** | `no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `wrong_number`, hủy hẹn không bao giờ tăng `connected` | `5` lần liên hệ không gặp trên `phone_call`/`sms`/`zalo` → `attempt_count = 5`, `connected_attempt_count = 0`, `0` `CRM Call Log` giả; hủy callback giữ nguyên `connected_attempt_count` | **`PASS`** |
| **G2-07 — Atomic & idempotent** | Ghi nguyên tử `Attempt + CRM Task + Work Item`, idempotent khi retry, `409` khi đổi payload/lệch revision, an toàn với `1020`/`1213` | `N=6` luồng HTTP cùng key → đúng `1` Attempt + `1` Task; đổi payload → `409 IDEMPOTENCY_CONFLICT`; 2 tab → `409 STALE_WORK_ITEM`; lỗi giữa giao dịch rollback sạch `0` bản ghi mồ côi; lỗi `CRM Notification` sau commit không làm mất dữ liệu; nhánh `_run_with_tx_retry` xử lý sạch cả `1213` lẫn `1020` (`QueryDeadlockError`) | **`PASS`** |
| **G2-08 — Native reuse** | Tái sử dụng nguyên bản `CRM Call Log`, `CRM Task`, `FCRM Note` (`0` sửa upstream `frappe`/`crm`), không đếm đôi | `git status` upstream sạch; `UNIQUE(call_log)` chặn gắn trùng (`409 ALREADY_LINKED`); Call Log ngoại lai bị chặn `422`; Call Log tổng đài `Twilio` (`Completed`, `duration=52`) đạt `verified`, Call Log thủ công buộc đủ `from`/`to` và giữ `self_reported`; thêm `FCRM Note` / `CRM Task` thường giữ `attempt_count = 0` | **`PASS`** |
| **G2-09 — Follow-up flow** | Tối đa `1` open callback `CRM Task`/WI, đổi hạn/hủy hẹn bắt buộc lý do, `complete` bắt buộc `attempt_id`, đúng ranh giới ngày `UTC+07` | Đổi hạn không lý do → `422 NOTE_REQUIRED`; `complete` thiếu `attempt_id` → `422`; kiểm ranh giới `23:59:55` hôm nay (`today`), `00:00:05` ngày mai (`upcoming`), `23:59:55` hôm qua (`overdue`) khớp chính xác trên cả `UTC`, `America/Los_Angeles`, `Asia/Ho_Chi_Minh` | **`PASS`** |
| **G2-10 — Transfer safety** | Chuyển `A → B` giữ nguyên `due_date` và trạng thái `in_progress`/`on_hold`, thu hồi sạch quyền của `A`, chặn backdate `A → B → A` | Race `record_attempt` vs `ownership.reassign` bảo toàn `reconcile_p2_invariants().ok = true`; `wi_xfer` chuyển `staff.a1 → staff.b1` giữ nguyên `due_date` đến từng giây và `status = on_hold`; đóng sạch `WP-OBS-01` trên mọi mặt phẳng; `wi_aba` (`A → B → A`) chặn `staff.a1` ghi lùi vào khoảng của `staff.b1` (`422`); `L-23` chặn `leader.a` mở lại WI cũ khi khách đã sang Đội B (`403`) | **`PASS`** |
| **G2-11 — Correction/audit** | `CSKH Contact Attempt` gốc bất biến 100%, sửa sai chỉ qua `CSKH Attempt Correction` append-only (`approve`/`reject`/`void`/`restore`) | So sánh SQL hàng gốc `CSKH Contact Attempt` trước và sau chuỗi `request → approve → void → admin restore` khớp `100%` không đổi 1 ký tự; `Leader` gọi `restore` bị chặn `403`; bộ đếm cập nhật chính xác không đếm đôi | **`PASS`** |
| **G2-12 — Permissions & PII** | Đạt trọn vẹn `SEC2-01..SEC2-16` trên cả Custom API và REST/client/Desk/filter/search/export (`0` High/Critical, `BOD` `0` PII) | `14/16` kịch bản `SEC2` đạt; **NHƯNG BỊ CHẶN BỞI 2 LỖI MỨC `HIGH`:** **[`F-P2-01`](#f-p2-01)** (`SEC2-09`: `BOD` đọc trọn ghi chú khách + SĐT + lý do `permlevel=1` trên cả 5 mặt phẳng qua `CSKH Assignment History.reason`) và **[`F-P2-06`](#f-p2-06)** (`SEC2-05`: `Sales User` ghi/xóa trực tiếp `cskh_do_not_contact` trên `CRM Lead` qua REST `PUT`) | **`FAIL`** |
| **G2-13 — UI tiếng Việt** | Toàn bộ màn `/cskh/*` hiển thị tiếng Việt tự nhiên, lọc/phân trang chính xác, không rò mã kỹ thuật | `282/282` chuỗi P2 có bản dịch `vi.csv` và phân quyền HTML route đúng; **NHƯNG BỊ CHẶN BỞI [`F-P2-04A`](#f-p2-04)** (bộ lọc trạng thái chăm sóc trên `/cskh/my-customers` lọc client-side trên 20 dòng trang hiện tại, làm ẩn khách trang 2+) và **[`F-P2-01`](#f-p2-01)** (rò tiền tố băm `[H:<16hex>]` thô lên giao diện dòng thời gian) | **`FAIL`** |
| **G2-14 — Progress basic** | Trang Tiến độ khớp bảng nguồn SQL, đúng lọc ngày giờ `UTC+07`, tách rõ số lượng KH đã/chưa liên hệ/kết nối (`P2.md §12.4`) | Đối soát không lọc ngày khớp `1:1` với SQL; **NHƯNG BỊ CHẶN BỞI [`F-P2-05`](#f-p2-05)** (`date_to="YYYY-MM-DD"` từ `<input type="date">` làm rơi `100%` giao dịch trong ngày: `843` Attempts thực tế → API trả `0`; đồng thời thiếu bộ đếm distinct Work Item đã/chưa liên hệ/đã kết nối và gắn sai nhãn `dnc_leads`) | **`FAIL`** |
| **G2-15 — Handoff P3** | Toàn bộ gate `G2-01..G2-14` đạt `PASS`, CI xanh đủ assertion, hoàn tất `WP10` & hồ sơ bàn giao P3 | `F-P2-03` đã đóng trên `ffe74df` (`CI #41759646273`: `61/61` HTTP assertions `PASS`), nhưng còn **`5` finding mở (`F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06`)** chặn nghiệm thu bàn giao sang P3 | **`FAIL`** |

---

## 3. Chi tiết Các Finding Bắt buộc Khắc phục (Đủ 7 Trường Chuẩn T1C)

### <a id="f-p2-01"></a>`F-P2-01` — Rò rỉ PII (SĐT, tên khách, ghi chú liên hệ) & lý do `permlevel=1` sang `CSKH BOD Viewer` trên cả 5 mặt phẳng qua `CSKH Assignment History.reason`, kèm lộ tiền tố `[H:<16hex>]` trên UI

1. **Mã Finding, Mức độ & Gate bị chặn:** `F-P2-01` · **`HIGH`** · Chặn **`G2-12`** (`SEC2-09`), **`G2-13`**, **`G2-15`**.
2. **SHA thực chạy:** `PR #7` `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` & `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10` (`apps` tree `4a395ae619f95a1feb0fbf9ec189063af2938f76`, backend `6ef2e2e2` / `580a45f0`), site `t1cp2.localhost` sau `migrate × 2`.
3. **Actor & Vai trò thực tế:** `cskh.bod@example.test` (chỉ có role `["CSKH BOD Viewer"]`, **`0` `System Manager`**) và `staff.a1@example.test` (`["Sales User"]`).
4. **Fixture tái hiện:**
   - Work Item DNC tự động đóng (`wi_dnc_leak` thuộc `CAMP-P2-A`): `staff.a1` gọi `workflow.record_attempt` với `outcome_code = "do_not_contact"` và ghi chú chứa tên + SĐT khách (không truyền `completion_reason`).
   - Work Item đóng/mở lại (`wi_cr_leak` thuộc `CAMP-P2-A`): `staff.a1` gọi `workflow.close_work_item` kèm `completion_reason` nhạy cảm (`permlevel=1` trên `CSKH Work Item`), sau đó `leader.a` gọi `workflow.reopen_work_item` kèm `reason` nhạy cảm (`permlevel=1`).
5. **Request cụ thể:**
   - Plane 1 (Custom API): `GET /api/method/crm_cskh.api.ownership.history?work_item=<wi_dnc_leak|wi_cr_leak>` dưới phiên `cskh.bod@example.test`.
   - Plane 2 (REST): `GET /api/resource/CSKH Assignment History?fields=["name","work_item","action","reason"]&filters=[["work_item","in",["<wi_dnc_leak>","<wi_cr_leak>"]]]`.
   - Plane 3 (`frappe.client`): `GET /api/method/frappe.client.get_list?doctype=CSKH Assignment History&fields=["name","work_item","action","reason"]...`.
   - Plane 4 (Desk `reportview` & `getdoc`): `POST /api/method/frappe.desk.reportview.get` và `GET /api/method/frappe.desk.form.load.getdoc?doctype=CSKH Assignment History&name=<close_hist_name>`.
   - UI Timeline: `GET /api/method/crm_cskh.api.workflow.list_attempts?work_item=<wi_cr_leak>&include_milestones=1` dưới phiên `staff.a1@example.test`.
6. **Expected vs Actual (kèm nguyên nhân gốc trong code):**
   - **Expected:** `CSKH BOD Viewer` tuyệt đối không đọc được ghi chú liên hệ tự do (`note`), SĐT khách hàng hay trường `permlevel=1` (`completion_reason`, `reopen_reason`) trên bất kỳ mặt phẳng nào (`P2.md` §0.9, §10.1, `SEC2-09`); đồng thời chuỗi hiển thị trên UI không lộ tiền tố kỹ thuật `[H:...]`.
   - **Actual:** Cả **5/5 mặt phẳng** đều trả `HTTP 200` cho `cskh.bod@example.test` chứa nguyên văn ghi chú liên hệ + SĐT khách của ca DNC tự động đóng **và** nguyên văn `completion_reason` + `reopen_reason`! Đồng thời `ownership_milestones[].reason` trên Timeline trả nguyên chuỗi `[H:<16hex>] <reason>`.
   - **Nguyên nhân gốc:** Trong `apps/crm_cskh/crm_cskh/services/contact_attempt.py` (`L159`, `L492–494`, `L850–856`, `L1059–1074`), khi đóng tự động do `do_not_contact` (`L493: auto_reason = clean_comp_reason or clean_note or ...`), hàm chép thẳng `clean_note` (ghi chú khách hàng) và `completion_reason`/`reopen_reason` (vốn được bảo vệ `permlevel=1` trên `CSKH Work Item`) sang `CSKH Assignment History.reason` (`permlevel=0`), trong khi `CSKH Assignment History` cấp quyền `read=1` ở `permlevel=0` cho `CSKH BOD Viewer` (`p1_permissions.py:224–226, 353–354`). Ngoài ra tiền tố idempotency `[H:<16hex>] ` nhúng trực tiếp vào `reason` thay vì tách cột riêng hoặc cắt bỏ khi trả về `read_model.py:704` / `ownership.py:108`.
7. **Ảnh hưởng & Điều kiện đóng (Closure Criteria):**
   - **Ảnh hưởng:** Phá vỡ bất biến `BOD 0 PII` của P0/P1/P2 trên mọi kênh đọc (`ownership.history`, REST, `frappe.client`, `reportview`, `getdoc`).
   - **Điều kiện đóng:** (a) Không chép `clean_note` tự do vào `CSKH Assignment History.reason` khi auto-close DNC; (b) Bảo vệ nội dung lý do nhạy cảm khỏi `CSKH BOD Viewer` trên tất cả 5 mặt phẳng (`permlevel=1` trên `CSKH Assignment History.reason` kèm chặn tại `ownership.history` và `enforce_p1_filter_field_permissions`, hoặc che sạch cho BOD); (c) Không trả tiền tố thô `[H:<16hex>]` ra dữ liệu hiển thị người dùng trên Timeline / Lịch sử chuyển giao.

---

### <a id="f-p2-06"></a>`F-P2-06` — `Sales User` / `Sales Manager` tự ý bật hoặc gỡ cờ `cskh_do_not_contact` trực tiếp trên `CRM Lead` qua Generic Frappe REST / `frappe.client`, vượt mặt quy trình P2 và quyền độc quyền `override_dnc` của `CSKH CRM Admin`

1. **Mã Finding, Mức độ & Gate bị chặn:** `F-P2-06` · **`HIGH`** · Chặn **`G2-04`** (`T2-15`), **`G2-12`** (`SEC2-05`, `SEC2-16`), **`G2-15`**.
2. **SHA thực chạy:** `PR #7` `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` & `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10` (`apps` tree `4a395ae619f95a1feb0fbf9ec189063af2938f76`).
3. **Actor & Vai trò thực tế:** `staff.a1@example.test` (`["Sales User"]`, `0` `CSKH CRM Admin`, `0` `System Manager`).
4. **Fixture tái hiện:** Bất kỳ `CRM Lead` nào đang do `staff.a1@example.test` phụ trách (ví dụ `CRM-LEAD-2026-00193` gắn với Work Item active của `staff.a1`, hoặc Lead vừa được Admin gỡ DNC).
5. **Request cụ thể:**
   - Bật DNC không qua Attempt: `PUT /api/resource/CRM Lead/CRM-LEAD-2026-00193` với JSON `{"cskh_do_not_contact": 1, "cskh_dnc_reason": "Staff bypassed workflow via REST PUT"}` kèm `X-Frappe-CSRF-Token` của `staff.a1`.
   - Tự ý gỡ DNC không cần Admin: `PUT /api/resource/CRM Lead/CRM-LEAD-2026-00193` với JSON `{"cskh_do_not_contact": 0, "cskh_dnc_reason": "Staff cleared DNC without Admin override!"}`.
6. **Expected vs Actual (kèm nguyên nhân gốc trong code):**
   - **Expected:** `HTTP 403` (hoặc chặn tuyệt đối ở `permlevel >= 1` + `CRM Lead.validate`), `CRM Lead.cskh_do_not_contact` và `cskh_dnc_*` chỉ được thay đổi thông qua `services/contact_attempt.py` (`record_attempt` với `outcome_code = "do_not_contact"`, hoặc `reopen_work_item` với `override_dnc = True` bởi `CSKH CRM Admin`).
   - **Actual:** Cả hai lệnh `PUT` của `staff.a1` đều trả **`HTTP 200`**, ghi thẳng `cskh_do_not_contact = 1` rồi `0` và ghi đè `cskh_dnc_reason` trong bảng `tabCRM Lead` mà không sinh bất kỳ `CSKH Contact Attempt` hay audit trail nào! (Đối chứng: `PUT /api/resource/CRM Task/<id>` bị chặn `403` nhờ `validate_cskh_followup_task`).
   - **Nguyên nhân gốc:** `fixtures/custom_field.json` khai báo 4 trường P2 mới trên `CRM Lead` (`cskh_do_not_contact`, `cskh_dnc_reason`, `cskh_dnc_at`, `cskh_dnc_work_item`) với `permlevel: 0`, `read_only: 0`. Khi giao việc, `ownership._sync_docshares` (`services/ownership.py:269`) cấp `DocShare(read=1, write=1)` trên `CRM Lead` cho owner hiện tại, trong khi `hooks.py:217–220` chỉ gắn `validate_lead_owner` cho `CRM Lead.validate` mà quên gắn guard chặn sửa `cskh_do_not_contact` / `cskh_dnc_*` ngoài `cskh_in_attempt_service`!
7. **Ảnh hưởng & Điều kiện đóng (Closure Criteria):**
   - **Ảnh hưởng:** Nhân viên có thể tự gắn cờ DNC khống làm sai lệch `dnc_leads` trên trang Tiến độ và khóa phân phối khách hàng mà không cần gọi điện/ghi Attempt, hoặc tự gỡ cờ DNC trái phép vượt qua kiểm soát của `CSKH CRM Admin`.
   - **Điều kiện đóng:** Bổ sung `doc_events["CRM Lead"]["validate"]` (tương tự `validate_cskh_followup_task` trên `CRM Task`) chặn mọi thay đổi trên `cskh_do_not_contact`, `cskh_dnc_reason`, `cskh_dnc_at`, `cskh_dnc_work_item` khi `not getattr(frappe.flags, "cskh_in_attempt_service", False)` (kể cả từ REST, `frappe.client.set_value`, `save`, Desk form), đồng thời cân nhắc bảo vệ `cskh_dnc_reason` khỏi filter-oracle ngoài phạm vi.

---

### <a id="f-p2-02"></a>`F-P2-02` — Bỏ sót điểm `WP-STATIC-01` tại `api/run_history.py:97` (`status = 'assigned'`), làm rơi số lượng phân bổ theo thành viên của `Sales Manager` khi Work Item chuyển sang `in_progress` / `on_hold`

1. **Mã Finding, Mức độ & Gate bị chặn:** `F-P2-02` · **`MEDIUM`** · Chặn **`G2-03`**, **`G2-15`**.
2. **SHA thực chạy:** `PR #7` `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` & `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10`.
3. **Actor & Vai trò thực tế:** `leader.a@example.test` (`["Sales Manager", "Sales User"]`) và `staff.a2@example.test` (`["Sales User"]`).
4. **Fixture tái hiện:** Đợt phân phối `CSKH Distribution Run` hoàn tất trên `CAMP-P2-A` gắn với `wi_run_test` của `staff.a2` (nhánh fallback đọc DB `_db_team_assigned_by_run`).
5. **Request cụ thể:**
   - Gọi `GET /api/method/crm_cskh.api.run_history.runs?campaign=<camp_a>&run_id=<run_id>` dưới `leader.a` trước và ngay sau khi `staff.a2` gọi `POST /api/method/crm_cskh.api.workflow.record_attempt` (`no_answer`, chuyển `wi_run_test` từ `assigned → in_progress`).
6. **Expected vs Actual:**
   - **Expected:** Bản đồ `assigned` của đợt chạy giữ nguyên `{"staff.a2@example.test": 1}` cho các trạng thái sở hữu đang hoạt động (`assigned`, `in_progress`, `on_hold`).
   - **Actual:** Khi `status == 'assigned'`, API trả `{"staff.a2@example.test": 1}`; ngay sau khi `staff.a2` lưu Attempt đầu tiên (`status == 'in_progress'`), API trả `{}` (`0`!) do dòng `97` của [`apps/crm_cskh/crm_cskh/api/run_history.py`](../../../../apps/crm_cskh/crm_cskh/api/run_history.py) viết cứng `where allocation_run in (...) and status = 'assigned'`.
7. **Ảnh hưởng & Điều kiện đóng (Closure Criteria):**
   - **Ảnh hưởng:** Sai lệch lịch sử phân bổ của Team Leader khi nhân viên bắt đầu chăm sóc khách.
   - **Điều kiện đóng:** Mở rộng điều kiện tại `api/run_history.py:97` sang `OWNED_ACTIVE_WORK_ITEM_STATES` (`('assigned', 'in_progress', 'on_hold')`) — hoặc đọc từ `CSKH Assignment History` gốc của đợt chạy để không rơi cả khi đóng — và xác minh runtime lại với `leader.a`.

---

### <a id="f-p2-04"></a>`F-P2-04` — Màn `/cskh/my-customers` lọc trạng thái chăm sóc ở phía trình duyệt trên từng trang 20 dòng (làm mất khách ở trang 2+) & `list_work_items` lọc `status='closed'` kèm `assignee` luôn trả `0` bản ghi

1. **Mã Finding, Mức độ & Gate bị chặn:** `F-P2-04` · **`MEDIUM`** · Chặn **`G2-03`**, **`G2-13`**, **`G2-15`**.
2. **SHA thực chạy:** `PR #7` `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` & `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10`.
3. **Actor & Vai trò thực tế:** `staff.a1@example.test` (`Sales User`) và `leader.a@example.test` (`Sales Manager`).
4. **Fixture tái hiện:**
   - **(A)** `staff.a1` đang phụ trách `72` Work Items active (`> 20` dòng/trang); mục `7mikecehjd` nằm ở trang 2 (`page=2, page_size=20`) và đã ghi nhận kết quả `connected_interested` (`connected_attempt_count = 1`).
   - **(B)** Đội A có `18` Work Items ở trạng thái `closed` (trong đó nhiều mục do `staff.a1` đóng).
5. **Request cụ thể:**
   - **(A)** Mở `/cskh/my-customers` (`public/cskh/my-customers.js:55–102, 424–435`), chọn bộ lọc chăm sóc `Đã kết nối` (`connected`) / `Đã liên hệ` (`attempted`) / `Đã liên hệ chưa gặp` (`attempted_not_connected`) / `Ngừng liên hệ (DNC)` (`do_not_contact`).
   - **(B)** `GET /api/method/crm_cskh.api.workflow.list_work_items?status=closed&assignee=staff.a1@example.test&page=1&page_size=20` dưới phiên `leader.a@example.test` (so với không truyền `assignee`).
6. **Expected vs Actual:**
   - **Expected:** (A) Bộ lọc chăm sóc phải lọc ở tầng SQL (`services/read_model.py::list_work_items`) trước khi `LIMIT/OFFSET` để hiển thị đủ khách thỏa điều kiện trên mọi trang và tính đúng tổng số trang; (B) Lọc `status='closed'` theo `assignee` phải khớp người đóng/phụ trách cuối (`closed_by` / `last_activity_by`) thay vì trả `0`.
   - **Actual:** (A) `workflow.list_work_items` không có tham số lọc care-state; `my-customers.js` chỉ gọi `page=1, page_size=20` (`status='active'`), lọc mảng `rawItems` trên RAM rồi gọi `pager.setTotal(filteredCount)` (`18` mục trên trang 1), khiến toàn bộ `17` mục `connected` ở trang 2 (gồm `7mikecehjd`) bị che khuất hoàn toàn và thanh chuyển trang bị khóa ở 1 trang! (B) Khi `leader.a` lọc `status=closed` không kèm `assignee` trả `total = 18`, nhưng thêm `assignee=staff.a1@example.test` trả **`total = 0`** do `read_model.py:523` lọc `wi.assigned_to = %s` trong khi `_close_work_item_in_tx` xóa `assigned_to = None`.
7. **Ảnh hưởng & Điều kiện đóng (Closure Criteria):**
   - **Ảnh hưởng:** Nhân viên có `> 20` khách bị sót khách trên màn tác nghiệp chính `Khách của tôi`; Team Leader/Admin không lọc được các hồ sơ đã đóng theo nhân viên.
   - **Điều kiện đóng:** Đưa bộ lọc care-state (`attempted`, `attempted_not_connected`, `connected`, `do_not_contact`) xuống SQL server-side trước phân trang trong `read_model.list_work_items`, bỏ `pager.setTotal(items.length)` cắt cụt ở client, và hỗ trợ lọc `closed` theo `closed_by` (hoặc owner cuối trước khi đóng).

---

### <a id="f-p2-05"></a>`F-P2-05` — Trang Tiến độ (`/cskh/progress` & `workflow.progress_summary`) làm rơi `100%` phát sinh trong ngày `date_to` khi truyền ngày không giờ (`YYYY-MM-DD`), thiếu bộ đếm distinct Work Item đã/chưa liên hệ/đã kết nối (`P2.md` §12.4) và gắn sai nhãn `dnc_leads`

1. **Mã Finding, Mức độ & Gate bị chặn:** `F-P2-05` · **`MEDIUM`** · Chặn **`G2-14`**, **`G2-15`**.
2. **SHA thực chạy:** `PR #7` `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce` & `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10`.
3. **Actor & Vai trò thực tế:** `cskh.bod@example.test` (`CSKH BOD Viewer`), `leader.a@example.test`, `staff.a1@example.test`, `cskh.admin@example.test`.
4. **Fixture tái hiện:** Site `t1cp2.localhost` có **`843` bản ghi `CSKH Contact Attempt`** phát sinh trong ngày `2026-11-04` (giờ Việt Nam `Asia/Ho_Chi_Minh`).
5. **Request cụ thể:**
   - Chọn Từ ngày = `2026-11-04`, Đến ngày = `2026-11-04` trên `/cskh/progress` (`<input type="date">` tại `public/cskh/progress.js:159`), gửi `GET /api/method/crm_cskh.api.workflow.progress_summary?date_from=2026-11-04&date_to=2026-11-04`.
6. **Expected vs Actual:**
   - **Expected:** (A) `date_to = "2026-11-04"` phải bao trọn đến cuối ngày `2026-11-04 23:59:59.999999` theo `Asia/Ho_Chi_Minh`, trả đủ `843` Attempts thực tế trong ngày; (B) Theo `docs/root-plan/P2.md` §12.4 & §15.4 (`T2-22`), tổng quan tiến độ phải tách bạch số lượng **Work Item đã có ít nhất 1 liên hệ (`attempted_work_items`), Work Item chưa liên hệ (`unattempted_work_items`), và Work Item đã kết nối thành công (`connected_work_items`)** bên cạnh tổng lượt `effective_attempts`/`connected_attempts`; (C) Nhãn thẻ DNC phải khớp đơn vị đếm thực tế.
   - **Actual:** (A) `services/read_model.py:734, 846` gọi `get_datetime("2026-11-04")` thành `2026-11-04 00:00:00` rồi so sánh `occurred_at <= '2026-11-04 00:00:00'`, khiến API trả **`effective_attempts = 0`** (rơi sạch `843/843` bản ghi trong ngày!); (B) `cards` chỉ có `effective_attempts`, `connected_attempts`, `unreached_attempts`, hoàn toàn thiếu bộ đếm distinct Work Item đã liên hệ / chưa liên hệ / đã kết nối; (C) `progress.js:51` gắn nhãn `cards.dnc_leads` (`count(distinct CRM Lead)`) thành `"Hồ sơ ngừng liên hệ (DNC)"` (`Do-not-contact (DNC) work items`).
7. **Ảnh hưởng & Điều kiện đóng (Closure Criteria):**
   - **Ảnh hưởng:** Lãnh đạo/BOD/Leader lọc tiến độ trong ngày hiện tại nhận toàn số `0`; không xem được tỷ lệ bao phủ khách hàng đã chăm sóc / đã kết nối theo đơn vị Work Item như yêu cầu `P2.md` §12.4.
   - **Điều kiện đóng:** Chuẩn hóa chuỗi ngày không giờ trên `date_to` về cuối ngày `23:59:59.999999` (`Asia/Ho_Chi_Minh`); bổ sung các bộ đếm distinct Work Item (`attempted_work_items`, `unattempted_work_items`, `connected_work_items`) đối soát khớp 1-1 với SQL nguồn; đồng bộ đúng nhãn hiển thị DNC.

---

### <a id="f-p2-03"></a>`F-P2-03` *(Đã xác minh `CLOSED` trên `ffe74df`)* — Thiếu `check()` trên request thứ 61 `p2-http-staff-b-close-wi-final` tại `26b0237`

1. **Mã Finding & Trạng thái theo Revision:** `F-P2-03` · `MEDIUM` · **`OPEN` trên `26b02379adbc8a50a2bee6eb3bd91ec5d5b6b6ce`** (`CI #41708187948`: `61` bản ghi HTTP nhưng `60` assertion) → **`CLOSED` trên `ffe74df22b85c1d5ecda9dc2f1bdc3015b5bae10`** (backend `580a45f0d823267f6845fec083d56ead00d29681`).
2. **Xác minh độc lập của T1C trên `ffe74df` (`580a45f`):**
   - T1C tự tải/hash/parse gói `p2-evidence-41759646273` (`13289404489`, `36,781` B, SHA-256 `f57b251d58b46c7cc2f092c26459be9858142efdb69b8724be0986ebf4784193`, khớp `digest` GitHub và `0` lệch `SHA256SUMS.txt`): `total_records = 61`, `verification_records = 61`, `setup_observation_records = 0`, `asserted_cases = 61`, `passed_assertions = 61`, `unasserted_cases = 0`, `final_close_case_asserted_pass = true` (`p2-http-completeness-gate.json` `status = "PASS"`).
   - Rà mã `ci_p2_http_probe.py` (`L136–237`, `L521–800`, `L1694–1704`) & `.github/workflows/p2.yml` (`L225–297`, `L546`): `run_close_work_item_case("p2-http-staff-b-close-wi-final")` kiểm tra đồng thời **hợp đồng HTTP 200 (§4.10)** và **trạng thái DB thực tế** (`status = 'closed'`, `assigned_to`/`assigned_at`/`open_callback_task`/`next_action_due`/`lead_owner` đã xóa sạch, `0` open callbacks, `0` active claims trên WI & Lead, `0` open ownership intervals, có event `close` trong `CSKH Assignment History`, `0` open `ToDo`). Mọi thiếu sót assertion, `setup_observation` đội lốt assertion, HTTP `200` sai payload hoặc sai trạng thái DB đều làm `ci_p2_http_probe.py` và `p2-http-completeness-gate.json` trả `exit 1` (gate đỏ).
   - **Phân định rõ phạm vi:** `9` method tự kiểm trong `deploy/dev/seed/test_ci_p2_http_probe.py` (T1C đã chạy độc lập đạt `9/9 PASS`) chỉ kiểm chứng *dụng cụ đo* (mock HTTP + mock DB fetcher), được tách bạch hoàn toàn khỏi độ phủ runtime nghiệp vụ P2 (`33` backend JUnit + `8` UI JUnit + `61` live HTTP cases trong CI, và runtime độc lập của T1C trên `t1cp2.localhost:18320`).

---

## 4. Các Ghi nhận Kỹ thuật & Provenance Không chặn (`OBS-P2-01 → OBS-P2-03`)

1. **`OBS-P2-01` (`api/ownership.py:47–58` chưa bọc `_run_with_tx_retry` như `api/workflow.py`):**
   - Trong phép đua song song thực tế (`threading.Barrier(2)`) giữa `workflow.record_attempt` và `ownership.reassign` trên `t1cp2.localhost`, khi `record_attempt` commit trước trong lúc `ownership.reassign` đang giữ snapshot `REPEATABLE READ`, MariaDB sinh `(1020, Record has changed since last read)` (`QueryDeadlockError`). Do `api/ownership.py` chưa dùng `_run_with_tx_retry`, ngoại lệ bị `except Exception` bọc thành `HTTP 400 INVALID_REQUEST` sau khi rollback sạch (`reconcile_p2_invariants().ok == True`), và gửi lại cùng `request_id` thành công `HTTP 200`. Khuyến nghị bọc `_run_with_tx_retry` cho `assign`/`reassign`/`revoke` tại `api/ownership.py` để đồng nhất hành vi tự phục hồi như `api/workflow.py`.
2. **`OBS-P2-02` (Provenance & `EXPLAIN` của `p2-benchmark-20261103.json`):**
   - File `deploy/dev/evidence/p2-runtime-20261103/benchmark/p2-benchmark-20261103.json` thiếu trường `commit_sha` nội tại (được commit tại `23a782bd58edaaef3e406fbc2b7e40622fb98d58`), đo ở tầng hàm Python service trong 1 tiến trình `bench execute` (`ThreadPoolExecutor(25)` — chịu nghẽn GIL nên `record_attempt_with_followup` `25` worker có `p50 = 8,864.4 ms` dù `innodb_row_lock_waits_delta = 0`). Ngoài ra `explain_plans.attempt_summary_by_campaign_occurred` đang quét toàn bảng `type = "ALL"` (`48,650` dòng, `key = null`) khiến `bod_system_progress_summary_50k_attempts` tốn `p50 = 2,519.4 ms` / `p95 = 2,728.9 ms` (`58` câu SQL/lần gọi) — nên tối ưu truy vấn gom nhóm SQL trước khi sang P3 SLA/KPI quy mô lớn.
3. **`OBS-P2-03` (Hành vi mặc định `doc.owner == user` của Frappe `File`):**
   - Trên file riêng tư gắn với `CRM Lead`, P0 `SEC-26` đã che sạch `/private/files/...` khỏi `get_attachments`/`get_activities`, `/api/resource/File/<id>` trả `404` cho cựu owner/leader, và `/private/files/...` trả `403` cho cựu Leader hoặc file do hệ thống/người khác tải lên; tuy nhiên hàm gốc `frappe.permissions.has_permission` của Frappe v15 vẫn cho phép người tạo gốc (`File.owner == user`) nếu tự lưu URL `/private/files/...` cá nhân từ trước. Ghi nhận đúng ranh giới kiến trúc Frappe v15 (P2 không sửa module `File` của P0).

---

## 5. Phạm vi Kiểm định Delta Tiếp theo để Đề nghị `GO P3`

Khi T1B và T1A bàn giao 1 SHA tích hợp sửa `5` finding đang mở (`F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06`):
1. T1C **bảo toàn toàn bộ các kết luận `PASS` (`G2-01`, `G2-02`, `G2-05..G2-11` và `F-P2-03`)** nếu diff không chạm lại logic liên quan.
2. Chỉ tái chạy đúng các phép thử delta cho `F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06` + tự tải/hash/parse bộ artifact CI mới trên SHA tích hợp cuối trước khi chuyển khuyến nghị sang `GO P3`.
