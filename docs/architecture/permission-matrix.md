# Permission Matrix — RBAC / ACL (P0)

> **Snapshot hiện hành T1C — Round 32b (2026-10-31):** kiểm định độc lập 12 ca mới (T1B-ext+2 matrix-gap probe): run #61 `#41159273106` (head `e0ef02f`, merge `136a10c2`), artifact `13026861084` (140 924 B, digest `8cfe5f10…` khớp API); 99/99 JUnit + 18/18 HTTP + 9/9 file-oracle + **74/74 remaining-gaps**; diff `b1087bc…e0ef02f` = 2 commit (chỉ probe `ci_g007_remaining_gaps_probe.py` +563/−5 + doc, KHÔNG đổi code app). **SEC tally 28/28 PASS. Matrix 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71.** 9 PARTIAL còn lại (LEAD-12/13, **RA-05**, CL-08, TK-07, NT-07, LS-08/10, BD-01) là nghiệp vụ P1 chưa triển khai, đang fail-closed. **Round 32 đã đồng bộ 19 hàng §§2–§9 còn ghi trạng thái lịch sử Round 28 (LEAD-11/15/16, CL-05/09, TK-04, NT-04, AT-05/06, RA-05/06/07/08/10, LS-04/05/06, BD-03/04) — sau đồng bộ tally §§2–§9 = bảng audit = 71 ô, không còn ô mâu thuẫn.** **G0-07 = PASS (T1C KÝ); P0 GO/NO-GO thuộc T0/Owner.** KHÔNG merge PR #2. Chi tiết [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) Round 32.
>
> **Snapshot Round 31 — lịch sử (2026-10-30):** PHỤC HỒI từ snapshot Round 30 (2026-10-28) tại `snapshot cuc bo ngoai repo (`t1b-pr2-review-e67`)` (byte-identical, SHA-256 `39405C1E…`; lịch sử Round 1–30 bảo toàn). Round 31 áp kiểm định độc lập T1B-ext+1 run #59 (`#41037431038`, head `b1087bc`, merge `682a9d93`, artifact `12968528845` digest `c6b298a8…`): 99/99 JUnit + 18/18 HTTP + 9/9 file-oracle + 62/62 remaining-gaps + Dynamic Link âm/dương PASS. **SEC tally 28/28 PASS** (SEC-16/26 → PASS; SEC-13 PASS theo P0 §7.5 giới hạn topology P0). **Matrix 52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71.** **G0-07 = PASS (T1C KÝ); P0 GO/NO-GO thuộc T0/Owner.** KHÔNG merge PR #2; KHÔNG stage/commit/push. Chi tiết [`t1c-ext1-verification-2026-10-30.md`](../../deploy/dev/evidence/t1c-ext1-verification-2026-10-30.md) + `t1c-security-verification.md` Round 31.
>
> **Snapshot Round 30 — lịch sử (2026-10-28):** CI #40514019123 xác nhận 99/99 test và HTTP probe 18/18; T1C tải artifact `12738607035` (API digest `31c707ad…b146`) và xác nhận `sec13-guest` HTTP 403 `PermissionError`/assertion PASS, đúng P0 §7.5. SEC tally hiện hành **26 PASS + 2 PARTIAL (SEC-16/SEC-26) = 28/28**. T1B DEV probe 30 case riêng vẫn **28 PASS / 2 FAIL** (artifact SHA-256 `33af91d6af4f7d2394ba7009a1fd32564116b76228eafe6046fbbc6edbb345f2`, 24,503 B); hai lỗi File route trả 403 nhưng phản chiếu identifier caller gửi, không có body/ID thô và chưa có bằng chứng IDOR. Round 30 ghi nhận thêm T1B control A–E, Deal/content_hash, Vite CORS và H-RFC-1(a); hai FAIL vẫn chưa giải quyết, SEC-26 PARTIAL, G0-07 PARTIAL/CHƯA KÝ, P0 NO-GO.
>
> **Matrix Round 28 giữ nguyên (lịch sử):** 71 ô: **44 PASS + 20 PARTIAL + 4 NOT RUN = 68 mapped; 3 CHƯA XÁC MINH**. CI/test/probe mới không tự nâng ô. RA-04 vẫn PASS theo kỳ vọng D tạm thời sau Owner decision; TK-04, LS-08, BD-03 vẫn PARTIAL. Chi tiết từng ô ở bảng matrix và `t1c-security-verification.md` Round 28. **Round 32 (LỊCH SỬ): 60 PASS + 8 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71.** **Round 32b hiện hành: 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (RA-05 hạ PASS → PARTIAL vì evidence đo sai actor; xem changelog 31/10/2026 (bổ sung)) (xem snapshot Round 32 ở trên và bảng audit trong `t1c-security-verification.md`).
>
> **Owner decision 2026-10-28:** URL visibility trên standard Frappe Desk/File routes được quản lý bằng permission; caller in-scope có thể nhận URL, kiểm tra download riêng. Quyết định này không chấp nhận lỗi phản chiếu ID ở hai 403 và không nới private-URL redaction cho custom CRM endpoints. Probe đo năm route fixture: Staff A in-scope nhận URL; Staff C ngoài scope 403 trên cả năm, nhưng hai route File trên phản ánh request File ID. Download ngoài scope 403 có/không `fid`; in-scope 200/90 B. Không có bằng chứng IDOR từ phép đo này; chưa chứng minh an toàn toàn diện. Xem Round 29–30 trong `t1c-security-verification.md`.
>
> **Owner:** Q1–Q4 DECIDED 2026-10-26 (`OWNER_DECISIONS_CONFIRMED=True`); RA-04 tạm `D` fail-closed tới ownership service ADR-004 §2.2. Không còn ghi các quyết định này là chờ Owner.
>
> **Toàn bộ phần thân dưới đây (metadata + §§1–9 bảng matrix + §12/§13) là lịch sử 2026-10-19.** Dùng snapshot Round 31 ở đầu tài liệu, bảng audit 71 ô trong `t1c-security-verification.md` Round 31 và §11.6 cho trạng thái hiện hành. Các trạng thái `NOT RUN`/`CHƯA XÁC MINH` trong §§2–9 là của vòng rà 2026-10-19, đã được cập nhật bởi Round 27–31.
> **Owner:** T1C — Thiết kế & kiểm chứng bảo mật P0
> **Phiên bản:** 1.0 (independent verification, T1C) — 2026-10-19
> **Trạng thái (T1C kiểm độc lập):** 18 permission-boundary test methods (code đọc từ `test_permissions.py` dòng 1–543) đã được T1C đối chiếu với từng ô matrix và SEC case. Ba lượt chạy tổng test khác nhau đã ghi nhận — **26 OK** (`wp05-security-final.txt`, revision `b382fe4`, site `test_cskh.localhost`), **27 OK** (`p0-local-final.txt`, revision `b382fe4`, site `dev.localhost` + `test_cskh.localhost`) và **28 OK** (GitHub Actions PR #1 run `#38712593755`, GHA clean bootstrap, site sạch) — T1C **từ chối gộp** thành một con số. Ma trận vẫn còn khoảng **~38/70 ô chưa có test chạm tới** (`CHƯA XÁC MINH`); SEC-13/14/16/26 và nhánh nội bộ SEC-27 vẫn `NOT RUN`. Xem [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §3, §4, §5 và `§6` cho blocker.
> **Đối chiếu checkout (1.0, độc lập bởi T1C):** test suite đã đọc và đối chiếu trực tiếp (`apps/crm_cskh/crm_cskh/tests/test_permissions.py`); hook implementation đã đọc (`apps/crm_cskh/crm_cskh/permissions.py`, `hooks.py`, `api/guarded_crm.py`, `api/guarded_reporting.py`); không có log thô mới so với revision `b382fe4`. Phiên Codex p0-audit-refresh **không có Docker/WSL2** (xem `environment-probe.md` §3, §4), nên T1C **không chạy lại** site sạch mà tái tạo bằng phân tích tĩnh + đối chiếu log T1B. CI run `#38712593755` đã xác nhận clean bootstrap + 28 tests + backup/restore + migrate; CI run `#39185122032` (push) cũng pass nhưng chỉ xác nhận workflow, không mở rộng security transport.
> **ADR-004/005:** quyết định đã được T0 chốt — ADR-004: thực thi quyền ở **backend/record-level** + ownership service tập trung ([`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md)); ADR-005: `BOD_VIEWER` **read-only**, **không** System Manager ([`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md)). Runtime bộ ba hook (`permission_query_conditions`/`has_permission` cho 7 DocType + `validate_lead_owner` + `validate_lead_assignment`) đã có implementation đọc được; override whitelist (`override_whitelisted_methods`) cho 13 endpoint CRM baseline đã chặn. Behavior runtime đã được 18 test methods assert, **nhưng chỉ trong route đã gọi** — không suy rộng ra toàn bộ ô matrix hay toàn bộ SEC case.
> **Tài liệu cha:** [`../root-plan/master-plan.md`](../root-plan/master-plan.md) §7, §9, §14 · [`../root-plan/P0.md`](../root-plan/P0.md) §7, §12
> **Tài liệu liên quan:** [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) — đối chiếu độc lập; [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md); [`adr-004`](./adr-004-owner-team-permissions.md), [`adr-005`](./adr-005-bod-viewer-scope.md).
> **Phạm vi lượt này:** T1C độc lập đối chiếu revision `b382fe4` có evidence T1B; sửa câu lịch sử "chưa có security suite" và "0/28 SEC" còn ở §0.2 cũ và changelog, đồng thời phân biệt ba lượt chạy có tổng test khác nhau (26 / 27 / 28) bằng **revision + lệnh + site**.

---

## 0. Quy tắc nguồn & bằng chứng (bắt buộc đọc)

### 0.1. Nguồn đúng phiên bản

| Thành phần | Phiên bản / revision | Trạng thái |
|---|---|---|
| Frappe CRM (baseline khảo sát) | tag **`v1.87.0`** (09/10/2026) | ĐÃ KHÓA (theo tài liệu cha) |
| Frappe Framework | tag **`v15.123.1`** (SHA `d2efa796…`) | Baseline install/migrate **đã chạy trên host hiện tại + GHA CI**; hành vi permission riêng của baseline `v1.87.0` (3 hook cho Lead/Deal/Notification) **CHƯA XÁC MINH** vì `crm_cskh` override gần như toàn bộ qua 7 hook + 13 override whitelist |
| frappe_docker | tag **`v4.0.1`** (SHA `2b26e9ab…`) | Docker/Bench stack chạy trên host hiện tại; full clean-machine reproducibility **CHƯA XÁC MINH** |
| Custom app `crm_cskh` | Scaffold `0.0.1` (revision `b382fe4`); 18 permission-boundary + 4 health + 3 cskh_app_settings + 3 poc_custom_field = 28 test methods (đếm từ source); ba lượt chạy log: 26/27/28 OK | Security behavior **ĐÃ CHẠY** trong route đã gọi (~32/70 ô matrix); phần còn lại **CHƯA XÁC MINH** |
| **ADR-004 / ADR-005** (quyết định T0) | ADR-004 enforcement backend/record-level ([link](./adr-004-owner-team-permissions.md)) · ADR-005 BOD read-only ([link](./adr-005-bod-viewer-scope.md)) | ĐÃ GHI tài liệu (T0 chốt); **runtime đã chạy cho route đã gọi** (xem `permissions.py` + `guarded_crm.py` + 18 test methods), **SEC-13/14/16/26 và ~38 ô matrix chưa chạy** |
| `master-plan.md` | v1.0 — 14/10/2026 | Nguồn định hướng |
| `P0.md` | v1.0 — 15/10/2026 | Nguồn đặc tả P0 |

Các file upstream dưới đây được đọc trực tiếp trên **tag `v1.87.0`** (`https://raw.githubusercontent.com/frappe/crm/v1.87.0/...`). Tham chiếu ở mức **file + function**. Bản [`reuse-audit.md`](./reuse-audit.md)/[`data-model-spike.md`](./data-model-spike.md) (cùng checkout) bổ sung **file:line** tại commit `0d3910b456ef4fb913e77b29db011b3debc4f778`.

- `crm/hooks.py` — đăng ký `permission_query_conditions`, `has_permission`, `doc_events`.
- `crm/permissions/org_hierarchy.py` — `_permission_query_conditions`, `_has_permission`, `_in_hierarchy`.
- `crm/api/todo.py` — `validate`, `after_insert`, `on_update`, `clear_owner_on_unassign`.
- `crm/fcrm/doctype/crm_lead/crm_lead.py` — `validate`, `after_insert`, `assign_agent`, `share_with_agent`.
- `crm/api/doc.py` — `get_data`, `get_assigned_users`, `get_linked_docs_of_document`, `remove_assignments`, `delete_bulk_docs`, `add_seen`.
- `crm/api/activities.py` — `get_activities`, `get_lead_activities`, `get_linked_calls`, `get_attachments`.
- `crm/fcrm/doctype/crm_call_log/crm_call_log.py` — `get_call_log`, `get_permitted_docs`, `get_call_contact`.
- `crm/api/assignment_rule.py` — `get_assignment_rules_list`, `duplicate_assignment_rule`.

### 0.2. Trạng thái repo — lịch sử 2026-10-19 (được snapshot Round 27 phía trên cập nhật)

Checkout có `apps/crm_cskh/`, `docs/` và `deploy/dev/`. **WP-05 security suite đã chạy** (xem `wp05-security-final.txt` 26 OK, `p0-local-final.txt` 27 OK, `p0-isolated-verification.md` 28 OK trên CI). 18 permission-boundary test methods có trong `apps/crm_cskh/crm_cskh/tests/test_permissions.py` (đã đọc dòng 1–543); cùng app suite có thêm 4 health + 3 poc_custom_field + 3 cskh_app_settings = 10 test không phải permission.

- **Tổng test app đã ghi nhận ở ba lần khác nhau (T1C TỪ CHỐI gộp):**
  - **26 OK** tại `wp05-security-final.txt` (revision `b382fe4`, lệnh `bench --site test_cskh.localhost run-tests --app crm_cskh`).
  - **27 OK** tại `p0-local-final.txt` (revision `b382fe4`, lệnh sau re-migrate `dev.localhost` + `test_cskh.localhost`).
  - **28 OK** tại `p0-isolated-verification.md` §"Security and source reload" (CI run `#38712593755` trên PR #1, GHA clean bootstrap site sạch).
- **Đối chiếu độc lập 18 permission-boundary methods** vs ~70 ô matrix + 28 SEC case: tham khảo [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §3, §5. **Khoảng 32 ô matrix có test mapping (route đã chạy)**; **~38 ô matrix còn `CHƯA XÁC MINH`** do chưa có test chạm tới.
- **SEC case** (28 ca): 19 đã verify runtime qua `test_permissions.py` (SEC-01–12, 15 endpoint phone-lookup, 17, 18, 19, 20, 21, 22, 24, 25, 27 endpoint phone-lookup, 28). **SEC-13, SEC-14, SEC-16, SEC-26 vẫn `NOT RUN`** — không có test HTTP transport hay URL leak trong app suite. **Nhánh nội bộ SEC-27** (`crm_call_log.get_call_contact` khi Call Log không ref Lead) vẫn `NOT RUN`. **SEC-15** chỉ verify nhánh phone-lookup chứ chưa verify toàn bộ API quản trị.
- **MariaDB 11.8** còn caveat tương thích từ baseline Frappe v15 (xem `version-lock.md`).
- **G0-07**: PARTIAL — không đóng. Các ô đã test cho `PASS có giới hạn` (route đã gọi); không mở rộng quyền user thường cho tới khi B-T1C-2 (SEC-13/14/26 transport) được đóng.
- **Không tuyên bố hệ thống "an toàn".** Trạng thái an toàn chỉ được ghi khi SEC-* tương ứng chạy thật và có log HTTP round-trip; ở phiên này mới có unit-level guard (`frappe.is_whitelisted`) trong `test_health.py` chứ không phải HTTP thật.
- **`deploy/dev/`** là **cấu hình/script**, **không** đồng nghĩa app/site đã chạy transport-level; phải có Docker/WSL2 mới chạy được.

### 0.3. Ký hiệu

| Ký hiệu | Nghĩa |
|---|---|
| **A** | Allowed — cho phép |
| **D** | Denied — từ chối |
| **C** | Controlled — cho phép **có kiểm soát** theo quyền/phạm vi (phải mô tả chính xác phạm vi ở P1/P3) |
| `CHƯA XÁC MINH` | Chưa chạy test (giá trị mặc định lượt này) |
| `PASS` / `FAIL` / `NOT IMPLEMENTED` / `BLOCKED` | Kết quả chỉ được điền sau khi chạy test thật |
| **Test âm** | Ca bắt buộc có để chứng minh điều ngược lại (out-of-scope phải bị chặn) |

---

## 1. Actor, team giả và dữ liệu seed

Theo P0 §7.1–§7.2.

| Alias | Tài khoản seed | Role dự kiến | Team |
|---|---|---|---|
| `ADM-SYS` | Administrator | System Manager | — |
| `CRM-ADMIN` | CRM_ADMIN | **CRM Admin — role PoC** (P0 §7.1) | — |
| `BOD` | BOD_VIEWER | **BOD Viewer — role PoC, read-only** (P0 §7.1; **không** System Manager — ADR-005) | — |
| `TL1` | TEAM1_LEADER | Sales Manager | Team 1 |
| `A` | STAFF_A | Sales User | Team 1 |
| `B` | STAFF_B | Sales User | Team 1 |
| `TL2` | TEAM2_LEADER | Sales Manager | Team 2 |
| `C` | STAFF_C | Sales User | Team 2 |

**Dữ liệu seed tối thiểu (giả, không PII thật):**

- Team 1: `A` sở hữu `A1–A4`; `B` sở hữu `B1–B4`.
- Team 2: `C` sở hữu `C1–C4`.
- Chưa giao: `U1–U4` (không `lead_owner`).
- Mỗi lead đã giao có ≥1 Call Log, 1 Note, 1 Task; ít nhất 1 lead có attachment **private**.
- Ca chuyển giao: `A1: A → B` (nội bộ Team 1); `C1: C → A` (chuyển team Team 2 → Team 1).

**Bảng dữ liệu ↔ team (để phân loại ô "dữ liệu thuộc team nào"):**

| Lead | Owner | Team | Thuộc TL |
|---|---|---|---|
| A1–A4 | A | Team 1 | TL1 |
| B1–B4 | B | Team 1 | TL1 |
| C1–C4 | C | Team 2 | TL2 |
| U1–U4 | (trống) | — | — |

---

## 2. MA TRẬN A — CRM Lead (`CRM Lead`)

Endpoint tham chiếu: `crm.api.doc.get_data` (list/search), `frappe.client.get` / Lead detail, `frappe.client.get_list`, export.

| Case | Actor | Dữ liệu (team/owner) | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| LEAD-01 | A | A1 (Team1/A) | detail `frappe.client.get` | A | **PASS** — `Test #1` (`test_lead_list_and_get_data_only_return_owner_scope`, `test_permissions.py` L206) | SEC-01 |
| LEAD-02 | A | B1 (Team1/B) | detail by đoán name | **D** | **PASS** — `Test #1`: `assertRaises(frappe.PermissionError)` khi gọi `client_get("CRM Lead", self.leads["b"])`. | SEC-01 |
| LEAD-03 | A | C1 (Team2/C) | detail chéo team | **D** | **PASS** — cùng assert như LEAD-02; hook `permissions.lead_has_permission` deny vì không phải owner scope. | SEC-01, SEC-06 |
| LEAD-04 | A | list | `get_data` (list/search) | A (chỉ A1–A4) | **PASS** — `Test #1`: `set == {self.leads["a"]}`; `p0-isolated-verification.md` & 3 log 26/27/28. | SEC-02 |
| LEAD-05 | A | list | `get_data` filter bị sửa/bỏ | A scope | **PASS** — `Test #1`: `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. | SEC-02 |
| LEAD-06 | A | B1 | ghi trực tiếp `CRM Lead` (kể cả field CSKH) | **D** | **PASS** — `Test #10` (`test_owner_change_and_assignment_bypass_are_denied`, L423): `lead.save(ignore_permissions=True)` raise; CLI ghi qua `frappe.client.set_value` cũng raise `Test #6` (L356). | SEC-04 |
| LEAD-07 | A | U1 (chưa giao) | detail | **D** | **PASS** — `TestLEAD07UnassignedLead` (3/3 methods) in `test_bt1c5_matrix_gaps.py`, CI run #40514019123: precondition, list, and detail checks for U1/unassigned Lead all pass. Bounded to this criterion. | SEC-01 |
| LEAD-08 | A | A1 (own) | export danh sách SĐT | **D** mặc định | **PASS** — `Test #5` (`test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers`, L319): `data_export` raise cho `A`. | SEC-12 |
| LEAD-09 | TL1 | A1–A4, B1–B4 | list/detail Team 1 | A (Team 1) | **PASS** — `Test #7` (`test_team_leader_sees_team_but_not_other_team`, L388): `visible` chứa `a` và `b`; assertion detail own OK. | SEC-06 |
| LEAD-10 | TL1 | C1 (Team2/C) | list/detail chéo team | **D** | **PASS** — `Test #7`: `assertNotIn(self.leads["c"], visible)`; `get_doc(...).check_permission("read")` raise. | SEC-06 |
| LEAD-11 | TL2 | A1 (Team1/A) | list/detail chéo team | **D** | **PASS** — Round 32 `lead11-team2-leader-list-scope` (TL2 thấy Lead team 2, không thấy Lead team 1) + `lead11-team2-leader-direct-read-denied` (403 `PermissionError`) — remaining-gaps 74/74 run #61. | SEC-06 |
| LEAD-12 | BOD | toàn công ty | list/detail (report scope, read-only) | **C** (read-only, ADR-005) | **PARTIAL — BOD fail-closed cho CRM Lead list đã verify** (`Test #16`, `test_bod_viewer_cannot_read_leads_or_manage_users`, L528); **scope đọc báo cáo chưa build**. | SEC-15 |
| LEAD-13 | CRM-ADMIN | toàn kho | list/detail/import | **C** | **PARTIAL — fail-closed** (`Test #17`, `test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537): `assertNotIn("System Manager", ...)` + `assertFalse(has_permission("CRM Lead", "read"))`. | — |
| LEAD-14 | A | A1 (own) | ghi trực tiếp `CRM Lead` (field ngoài whitelist CSKH) | **D** | **PASS** — `Test #10`: hook `validate_lead_owner` raise khi đổi `lead_owner`; fail-closed cho đến khi ownership service sẵn sàng. | SEC-08 |
| LEAD-15 | Bất kỳ | — | `get_data` đổi `doctype` sang doctype khác | bị bound bởi quyền doctype | **PASS** — Round 31 `cross-doctype-get-data-vs-get-list-*` (6 doctype: CRM Deal/ToDo/CRM Call Log/File/User/Custom Field): cùng permission boundary và visible name set như `frappe.client.get_list` — remaining-gaps 62/62 run #59. | SEC-25 |
| LEAD-16 | A | B1 | `get_data` chọn `rows`/`columns` = `mobile_no`,`email` ngoài phạm vi | **D** | **PASS** — Round 32 `lead16-ls06-get-data-field-scope`: `get_data rows=["mobile_no","email"]` trả field in-scope, field Lead ngoài scope không lộ (`leaked_marker_count=0`) — remaining-gaps 74/74 run #61. | SEC-25 |

**Cơ chế đã đọc trên `v1.87.0` (giả thuyết, cần test):** `crm/hooks.py` chỉ đăng ký `permission_query_conditions`/`has_permission` cho `CRM Lead`, `CRM Deal`, `CRM Notification`; logic nằm ở `crm/permissions/org_hierarchy.py` với `_OWNER_FIELD = {"CRM Lead": "lead_owner", "CRM Deal": "deal_owner"}`.

> **Chính sách P0 cho LEAD-06/14 (theo T0):** P0 **từ chối mọi sửa trực tiếp `CRM Lead`** từ nhân viên. Field CSKH chỉ được ghi qua **whitelist + endpoint server-side** trong `crm_cskh`, và chỉ sau khi **schema cụ thể được chốt** (P1/P2). Vì vậy kỳ vọng P0 cho cả hai ca là **D**; việc mở whitelist sẽ được đặc tả và test riêng khi có schema. Cả hai ca vẫn `CHƯA XÁC MINH` ở runtime.

---

## 3. MA TRẬN B — Call Log (`CRM Call Log`)

Endpoint: `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log`, `get_data`, `get_linked_calls` (trong `activities.py`).

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| CL-01 | A | Call của A1 | `get_call_log` | C (own) | **PASS** — `Test #6` (L356) cuối đoạn: `get_call_log` own OK; call của cross raise. | — |
| CL-02 | A | Call của B1 | `get_call_log` by name | **D** | **PASS** — `Test #6`: `assertRaises(PermissionError)` cho `get_call_log(self.call_b)`. | SEC-03 |
| CL-03 | A | Call của C1 | `get_call_log` chéo team | **D** | **PASS** — cùng assert cơ chế hook `crm_cskh.permissions.linked_lead_has_permission`. | SEC-03 |
| CL-04 | A | B1 | `get_data("CRM Call Log")` list | **D** (không thấy call của B) | **PASS** — `Test #3` (L261): `("CRM Call Log", "reference_docname", {self.call_a})` — set ≤ allowed; **Test #9** (L403): `guarded_crm.get_linked_calls(self.leads["b"])` raise. | SEC-03 |
| CL-05 | A | mọi call | `get_call_log` + đọc `from`/`to`/`recording_url` | **D** ngoài scope | **PASS** — Round 31 `dl01-linked-calls-in-scope-actor` (`leaked_marker_count=0` — không lộ contact/phone/note/task/recording URL/link_name ngoài scope) + `dl01-linked-calls-positive-control` (`positive_call_visible=true`, chống assertion vacuous) — remaining-gaps 62/62 run #59. | SEC-17 |
| CL-06 | TL1 | call Team 1 | list/detail | A (Team 1) | **PASS** — `TestCL0607TeamLeaderCallLog` (3/3 methods), CI #40514019123: TL1 Team 1 Call Log list/detail/endpoint checks pass. | SEC-06 |
| CL-07 | TL1 | call Team 2 | list/detail | **D** | **PASS** — `TestCL0607TeamLeaderCallLog` (3/3 methods), CI #40514019123: TL1 cross-team list/detail/endpoint denial checks pass. | SEC-06 |
| CL-08 | BOD | toàn công ty | read call (read-only) | **C** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | SEC-15 |
| CL-09 | Guest | — | `get_call_log` | **D** | **PASS** — Round 31 `cl09-get-call-log-guest` 403 / `cl09-get-call-log-out-of-scope` 403 / `cl09-get-call-log-in-scope` 200 — remaining-gaps 62/62 run #59. | SEC-13 |

**Giả thuyết cần test (R-03):** `crm/hooks.py` **không** đăng ký hook permission cho `CRM Call Log`; `get_call_log` chỉ gọi `frappe.has_permission("CRM Call Log","read",name)` → kiểm quyền **DocType/role**, không phải record-level. Nếu role `Sales User` (hoặc role tương đương) có quyền read trên DocType, khả năng đọc call log bất kỳ theo `name` là **cần kiểm chứng** (SEC-03/SEC-17). Không kết luận khi chưa chạy.

---

## 4. MA TRẬN C — Task (`CRM Task`)

Endpoint: `crm.api.activities.get_linked_tasks`, `get_data`, `frappe.client.*`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| TK-01 | A | Task của A1 | detail/list | C (own) | **PASS** — `Test #6` (L356) list (Call/Note/Task/File own) check_permission ok. | — |
| TK-02 | A | Task của B1 | detail by name | **D** | **PASS** — `Test #6`: `("CRM Task", self.task_b)` raise `PermissionError`. | SEC-04, SEC-18 |
| TK-03 | A | B1 | sửa Task ngoài scope | **D** | **PASS** — `Test #6`: `set_value("CRM Task", self.task_b, "title", ...)` raise. | SEC-04 |
| TK-04 | A | U1 | tạo Task gắn lead ngoài scope | **D** | **PASS** — Round 31 `tk04-read-out-of-scope-target` 403 + `tk04-create-task-out-of-scope` 403 `PermissionError`, không tạo ToDo (`matching_rows_before/after_insert/after_cleanup = 0`, `cleanup_verified=true`) — remaining-gaps 62/62 run #59. | SEC-04 |
| TK-05 | TL1 | Task Team 1 | list/detail | A | **PASS** — `TestTK0506TeamLeaderTask` (3/3 methods), CI #40514019123: TL1 Team 1 Task list/detail checks pass. | SEC-06 |
| TK-06 | TL1 | Task Team 2 | list/detail | **D** | **PASS** — `TestTK0506TeamLeaderTask` (3/3 methods), CI #40514019123: TL1 cross-team Task denial checks pass. | SEC-06 |
| TK-07 | BOD | toàn công ty | read (read-only) | **C** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | SEC-15 |

**Liên quan:** `crm/api/todo.py` `after_insert`/`on_update` xử lý `CRM Task` (notify), và với Lead/Deal thì **ghi đè `lead_owner`**. `clear_owner_on_unassign` **chỉ** áp dụng Lead/Deal (comment trong code: "Owner concept only exists for Lead/Deal, not Task").

**Giả thuyết cần test (R-03):** không có hook permission cho `CRM Task` → quyền record-level phụ thuộc DocType role + DocShare/ToDo. Cần test đọc Task theo `name` của user khác (SEC-04/SEC-18).

---

## 5. MA TRẬN D — Note / Comment (`FCRM Note`, `Comment`)

Endpoint: `crm.api.activities.get_linked_notes`, `get_attachments("Comment", ...)`, `get_data`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| NT-01 | A | Note của A1 | read | C (own) | **PASS** — `Test #6` (L356) `("FCRM Note", self.note_a)`. | — |
| NT-02 | A | Note của B1 | read by name | **D** | **PASS** — `Test #6`: `("FCRM Note", self.note_b)` raise. | SEC-04, SEC-18 |
| NT-03 | A | Comment của B1 | read | **D** | **PASS** — `Test #6`: `("Comment", self.comment_b)` raise; `Test #3`: comment list trả rỗng (DocType-level block). | SEC-04 |
| NT-04 | A | B1 | tạo Note/Comment ngoài scope | **D** | **PASS** — Round 31 `nt04-create-fcrm-note-out-of-scope` 403 + `nt04-create-comment-out-of-scope` 403 `PermissionError`, không tạo linked record (`matching_rows_before_cleanup=0`, `cleanup_verified=true`) — remaining-gaps 62/62 run #59. | SEC-04 |
| NT-05 | TL1 | Note Team 1 | read | A | **PASS** — `TestNT0506TeamLeaderNote` (3/3 methods), CI #40514019123: TL1 Team 1 Note read checks pass. | SEC-06 |
| NT-06 | TL1 | Note Team 2 | read | **D** | **PASS** — `TestNT0506TeamLeaderNote` (3/3 methods), CI #40514019123: TL1 cross-team Note denial checks pass. | SEC-06 |
| NT-07 | BOD | toàn công ty | read (read-only) | **C** | **PARTIAL — BOD fail-closed** (`Test #16`); **scope đọc chưa build**. | SEC-15 |

**Ghi chú:** `FCRM Note` là DocType bổ trợ (không thay kết quả CSKH có cấu trúc — master-plan §3.1). Quyền note/comment **cần khớp** với quyền lead mẹ.

---

## 6. MA TRẬN E — Attachment / File private (`File`, `is_private=1`)

Endpoint: `get_attachments` (trong `activities.py`), download URL `/private/files/...` (`frappe.client`/`File` API).

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| AT-01 | A | File của A1 | tải qua URL/token hợp lệ | C (own) | **PASS** — `Test #6` cho File own qua `check_permission`; `Test #4`: sau reassign, new owner `B` (`Test #4` cuối) `find_file_by_url(URL) is not None`. | — |
| AT-02 | A | File private của B1 | tải trực tiếp URL | **D** | **PASS** — `Test #4`: sau reassign, `a` (`frappe.set_user(self.users["a"])`) `find_file_by_url(...) is None`; cơ chế `clear_stale_lead_shares` DocShare gỡ + DocType-level permission file. | SEC-05 |
| AT-03 | A | File của C1 | tải chéo team | **D** | **PASS** — `Test #4` cơ chế hook `linked_lead_has_permission` deny. | SEC-05 |
| AT-04 | A | B1 | `get_attachments`/`get_data` trả `file_url` ngoài scope | **D** (không lộ URL) | **PASS (custom CRM routes only)** — CI #40514019123 `sec26-staff-private` and `sec26-activities-in-scope` show guarded custom endpoints redact private `file_url` for the tested Staff fixture; four historical `get_activities` URL sites are absent in that response. `Test #9` also denies cross-scope guarded attachment access. Standard Frappe Desk/File routes are separate policy/CI gap (RISK-SEC-10). | SEC-26 |
| AT-05 | BOD | toàn công ty | tải file theo scope báo cáo (read-only) | **C** | **DEFER P1** — BOD chưa có scope đọc; chỉ fail-closed đã verify (`Test #16`). BOD report/file scope chưa build (ADR-005) — DEFER P1 theo Owner 2026-10-29, KHÔNG chấm PASS. | SEC-16 |
| AT-06 | Guest | bất kỳ | tải `/private/files/...` | **D** | **PASS** — Round 31 `at06-private-download-in-scope` 200 / `-out-of-scope` 403 / `-guest` 403 / `-out-of-scope-unknown` 403 — remaining-gaps 62/62 run #59. | SEC-13 |

**Giả thuyết cần test:** `get_attachments` dùng `frappe.db.get_all("File", ...)` trả cả `file_url` cho file private. Cần xác minh (a) URL private có thực sự bị chặn khi tải ngoài scope, (b) API có lộ URL trước khi kiểm quyền không (SEC-05/SEC-26).

---

## 7. MA TRẬN F — Chuyển giao / Reassignment & Assignment endpoints

Endpoint: `crm.api.doc.remove_assignments`, `crm.api.doc.get_assigned_users`, `crm.api.assignment_rule.*`, hook `crm/api/todo.py`, `crm_lead.assign_agent`/`share_with_agent`.

| Case | Actor | Dữ liệu | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|---|
| RA-01 | A | A1 | tự đổi `lead_owner` A1→B | **D** | **PASS** — `Test #10` (L423): `lead.lead_owner = self.users["c"]` + `lead.save(ignore_permissions=True)` raise. | SEC-08 |
| RA-02 | A | B1 | assign cho người khác (ToDo) | **D** | **PASS** — `Test #14` (L490): tạo `ToDo` với `allocated_to=self.users["c"]` + `ignore_permissions=True` raise. | SEC-08 |
| RA-03 | A | A1 | gọi mutation với `ignore_permissions=true` | **D** | **PASS** — `Test #10`: `remove_assignments(...ignore_permissions=True)` raise; hook `validate_lead_assignment` deny. | SEC-09, SEC-21 |
| RA-04 | CRM-ADMIN | A1 | chuyển A1: A→B (nội bộ Team 1) | **D tạm thời** (Owner 2026-10-26; fail-closed đến ownership service ADR-004 §2.2) | **PASS theo kỳ vọng D** — Owner quyết định 2026-10-26: CRM-ADMIN bị fail-closed tới khi ownership service ADR-004 §2.2 tồn tại. Ba method `TestRA04CrmAdminReassignBlocked` đều PASS trong CI #40514019123. Không chứng minh ownership service đã triển khai; method mang tên lịch sử “pending owner decision” không còn phản ánh quyết định hiện hành. | SEC-10 |
| RA-05 | CRM-ADMIN | C1 | chuyển C1: C→A (Team2→Team1) | **D tạm thời** (Owner 2026-10-26 — cùng chính sách `RA-04`; cross-team cũng bị chặn vì cùng guard `validate_lead_owner`) | **PARTIAL — fail-closed đã chứng minh, chưa có ca HTTP đo trực tiếp actor CRM-ADMIN** — 3 method `TestRA04CrmAdminReassignBlocked` (`test_ra04_crm_admin_is_not_system_and_is_blocked_by_validate_lead_owner`, `test_ra04_ownership_service_does_not_exist_yet`, `test_ra04_stays_unclosed_pending_owner_decision`) + `Test #17` (`test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537) đều **PASS** trong 99/99 run #61: CRM-ADMIN **không** có `System Manager` ⇒ `_is_system == False` ⇒ `validate_lead_owner` chặn đổi `lead_owner` (`PermissionError`) và `lead_has_permission` chặn `write`; owner giữ nguyên (assert bằng `db.get_value`). **Hai ca HTTP `ra05-*` run #61 KHÔNG phải evidence của ô này** — chúng đo actor `Administrator` (System Manager) ⇒ đã tách sang hàng positive control `RA-05-SM` (§7.0.1). Chính sách trong repo (`TestRA04CrmAdminReassignBlocked` docstring) cấm: *"dùng `Administrator` để reassign sẽ đại diện sai actor (Administrator ≠ CRM-ADMIN) và tạo bằng chứng giả"*. **Còn thiếu 1 ca:** HTTP đo trực tiếp actor `CSKH CRM Admin` (`crm.admin@dev.local`) trên chuyển C1: C→A ⇒ kỳ vọng 403 `PermissionError` để đóng ô. | SEC-11 |
| RA-06 | TL1 | Lead Team 1 | chuyển trong team | **C** | **DEFER P1** — chỉ có admin trong tests; hook `validate_lead_owner` chặn TL1 (vì TL1 không phải `_is_system`) — fail-closed. Cần ownership service ADR-004 §2.2 (chưa xây) — DEFER P1 theo Owner 2026-10-29, KHÔNG chấm PASS. | SEC-06 |
| RA-07 | TL1 | Lead Team 2 | chuyển chéo team | **D** | **DEFER P1** — không có test case TL1 reassign; `validate_lead_owner` chặn non-system (fail-closed). Cần ownership service ADR-004 §2.2 (chưa xây) — DEFER P1 theo Owner 2026-10-29, KHÔNG chấm PASS. | SEC-06 |
| RA-08 | BOD | bất kỳ | chuyển/thu hồi | **D** mặc định (read-only) | **PASS** — Round 32 `bod-remove-assignments-denied` (403 `PermissionError`) + `bod-set-lead-owner-denied` (403): BOD bị chặn revoke/reassign — remaining-gaps 74/74 run #61. | SEC-15 |
| RA-09 | A (cũ) | sau A1: A→B | đọc lại Lead/Call/Note/Task/File | **D** (theo policy) | **PASS** — `Test #12` (L450): `assertNotIn(lead_a, get_list(self.users["a"]))`; `.check_permission` raise cho Lead + child; `Test #4` (L285): tương tự + linked endpoints raise. | SEC-10, SEC-22, SEC-23 |
| RA-10 | B (mới) | sau A1: A→B | đọc/ghi Lead/Call/Note/Task/File | A | **PASS (đọc) + P1 (ghi fail-closed)** — Round 32 `ra10-new-owner-reads-transferred-lead` (chủ mới đọc Lead A2 → 200), `ra10-new-owner-reads-child-note` (đọc Note con → 200), `ra10-old-owner-denied-after-transfer` (chủ cũ 403). **GHI Note con → 403 fail-closed** (`ra10-new-owner-writes-child-fail-closed`) — KHÔNG chấm "được phép ghi child" là PASS; phần ghi child thuộc backlog P1 (ownership service ADR-004 §2.2). | SEC-10 |


### 7.0.1. Positive control tách riêng — actor System Manager (KHÔNG thuộc lưới 71 ô)

> Các dòng dưới là **positive control**, **không phải** ô matrix của lưới 71 ô (vì vậy không tính vào tally). Chúng tồn tại để chứng minh **đường đo hoạt động** và **hệ quả chuyển giao đúng**, đồng thời để **tách khỏi** ô `RA-05` (actor `CRM-ADMIN`).

| Ô positive control (bổ sung, **ngoài lưới 71 ô**) | Case | Actor | Kết quả run #61 | Ý nghĩa | Giới hạn |
|---|---|---|---|---|---|
| `RA-05-SM` | `ra05-cross-team-transfer-admin-set-owner` | System Manager (`Administrator`) | **200** | Đường HTTP `frappe.client.set_value` đổi `lead_owner` C→A cross-team **hoạt động** (không bị chặn ở tầng transport/hook) | Dùng đường **generic**, **không** phải ownership service ADR-004 §2.2 ⇒ không suy rộng thành bằng chứng ownership service đã triển khai |
| `RA-05-SM` | `ra05-cross-team-transfer-new-owner-visible` | System Manager (`Administrator`) | **200** — `new_owner_visible=true`, `old_owner_denied=true`, `old_owner_status=403` | Hệ quả chuyển giao đúng: **chủ mới thấy** Lead, **chủ cũ bị chặn 403** (phục vụ tiêu chí read của RA-09/RA-10 và SEC-11) | `System Manager` ≠ `CRM-ADMIN` ⇒ **không nâng ô `RA-05`** |

### 7.1. Bảng sự thật đồng bộ 3 cơ chế (giả thuyết từ code `v1.87.0`)

Thuật ngữ: `lead_owner` (field trên Lead), `ToDo` (assignment, nguồn của `_assign`), `DocShare` (share quyền ghi trên Lead).

| Đường chuyển giao | `lead_owner` | `ToDo` mới | `ToDo` cũ | `DocShare` mới | `DocShare` cũ | Thu hồi quyền người cũ? | Test |
|---|---|---|---|---|---|---|---|
| Cập nhật `lead_owner` rồi `save()` (`crm_lead.validate` → `share_with_agent`+`assign_agent`) | Cập nhật | Thêm (`assign_agent`) | **Không huỷ → còn treo** | Thêm (`write=1`) | **Gỡ** (`share_with_agent` remove user≠agent) | Phụ thuộc: DocShare gỡ, nhưng `ToDo` cũ có thể còn giữ quyền | SEC-10, SEC-23 |
| Assign qua `ToDo` (`todo.after_insert`) | Ghi đè bằng `frappe.db.set_value` (**bypass controller**) | Tạo | **Không huỷ** | **Không cập nhật** (db_set không chạy `CRMLead.validate`) | **Không gỡ** | **Không đầy đủ** — DocShare cũ còn → người cũ có thể vẫn truy cập | SEC-22 |
| `remove_assignments` (`doc.py`) | `set_status(...Cancelled)` → `todo.on_update` → `clear_owner_on_unassign` (chỉ Lead/Deal) | — | Huỷ (Cancelled) | **Không tạo** | **Không gỡ** | Một phần | SEC-09, SEC-21 |

**Kết luận tạm (giả thuyết, CHƯA XÁC MINH):** tồn tại **bất đối xứng** giữa đường "đổi `lead_owner`" (đồng bộ DocShare, treo ToDo) và đường "assign qua ToDo" (đồng bộ `lead_owner`, treo DocShare). Khớp với rủi ro R-04 (master-plan §16). Đây là **ứng viên Critical/High** — xem `risk-register.md` `RISK-SEC-02`.

**Hướng đã chốt (ADR-004, T0):** quyền owner/team thực thi ở **backend/record-level**; mọi mutation owner/assignee phải đi qua **ownership service tập trung** trong `crm_cskh` (đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction, có reconciliation), thay vì cập nhật rời rạc. Đây là **quyết định thiết kế**; hành vi runtime của baseline vẫn `CHƯA XÁC MINH` cho tới khi SEC-10/22/23 chạy thật.

### 7.2. `get_assigned_users` — không thấy kiểm quyền

`crm/api/doc.py: get_assigned_users(doctype,name)` dùng `frappe.get_all("ToDo", ...)`. `get_all` mặc định **không** kiểm permission. Người dùng đã đăng nhập có thể tra assignee của bất kỳ doc theo `name` → **rò rỉ metadata** (danh tính người phụ trách). Test: SEC-19.

---

## 8. MA TRẬN G — List/Search/Export & API đọc dữ liệu chéo

| Case | Actor | Thao tác / Endpoint | Kỳ vọng | Thực tế (T1C đối chiếu) | Test âm |
|---|---|---|---|---|---|
| LS-01 | A | `frappe.client.get_list("CRM Lead", ...)` | scope A | **PASS** — `Test #1` (L206): `client_get_list("CRM Lead", ...)` chỉ trả own. | SEC-02 |
| LS-02 | A | global search trả lead ngoài scope | **D** | **PASS** — `TestLS02GlobalSearchScope` (3/3 methods), CI #40514019123: global search/search-link scope and bypass checks pass. | SEC-02 |
| LS-03 | A | export/CSV danh sách SĐT | **D** mặc định | **PASS** — `Test #5` (L319): `data_export` + `reportview_export_query` raise cho A. | SEC-12 |
| LS-04 | A | `get_data` link lookup / dropdown ngoài scope | **D** | **PASS** — Round 31 `ls04-search-link-in-scope-positive` (`result_count=1`, target present) + `ls04-search-link-out-of-scope` (`result_count=0`) — remaining-gaps 62/62 run #59. | SEC-02 |
| LS-05 | A | `get_data` `doctype` tùy biến (KV khác) | bound bởi quyền doctype | **PASS** — Round 31 `cross-doctype-get-data-vs-get-list-*` (6 doctype): cùng permission boundary và visible name set như `frappe.client.get_list` — remaining-gaps 62/62 run #59. | SEC-25 |
| LS-06 | A | `get_data` `rows`/`columns` chọn field nhạy cảm ngoài scope | **D** | **PASS** — Round 32 `lead16-ls06-get-data-field-scope`: field non-permlevel nhạy cảm (`mobile_no`/`email`) ngoài scope không lộ (`leaked_marker_count=0`) — remaining-gaps 74/74 run #61. | SEC-25 |
| LS-07 | A | bulk (`delete_bulk_docs`) chéo scope | **D** | **PASS** — `Test #11` (L437): `delete_bulk_docs` cross-scope raise. | SEC-12 |
| LS-08 | TL1 | report Team 1 | Team 1 | **PARTIAL** — `Test #5` in CI #40514019123 denies TL1 report execution generally; it does not prove the expected positive Team 1 scoped report path. No scoped Team 1 report is built/tested. | SEC-06 |
| LS-09 | TL1 | report Team 2 | **D** | **PASS — same as LS-08:** TL1 chặn toàn bộ report, scope cụ thể Team 2 chưa build. | SEC-06 |
| LS-10 | BOD | report toàn công ty (read-only) | **C** | **PARTIAL — `Test #5`** BOD chặn export/report (fail-closed); **scope đọc báo cáo BOD chưa build**. | SEC-15 |

**Giả thuyết:** `get_data` nhận `doctype`, `filters`, `default_filters`, `rows`, `columns` từ client. `rows`/`columns` cho phép chọn field tùy ý (`mobile_no`, `email`…) — cần test field-level exposure (SEC-25), kể cả trong scope (Medium) và ngoài scope (High nếu lọt).

---

## 9. MA TRẬN H — Báo cáo/Admin & BOD viewer (ADR-005)

| Case | Actor | Thao tác / Endpoint | Kỳ vọng | Thực tế | Test âm |
|---|---|---|---|---|---|
| BD-01 | BOD | xem báo cáo/kho (read-only) | **C** (không cần System Manager) | **PARTIAL — fail-closed verify** (`Test #16`, `test_bod_viewer_cannot_read_leads_or_manage_users`, L528): BOD `frappe.get_list("CRM Lead", pluck="name")` raise; **nhánh đọc báo cáo scoped (report API read-only) chưa build**. | SEC-15 |
| BD-02 | BOD | API quản trị user/role | **D** | **PASS — `Test #16`**: `has_permission("User", "create") == False` + `has_permission("Role", "create") == False` cho BOD; report API call `data_export` raise (`Test #5`). | SEC-15 |
| BD-03 | BOD | import/xoá dữ liệu | **D** | **PASS** — Round 32 `bod-bulk-delete-denied` (403 `PermissionError`): BOD bị chặn bulk delete Lead; `Test #16` chặn Data Import create — remaining-gaps 74/74 run #61. | SEC-15 |
| BD-04 | BOD | reassign/thu hồi | **D** mặc định | **PASS** — Round 32 `bod-remove-assignments-denied` (403) + `bod-set-lead-owner-denied` (403): BOD bị chặn reassignment/revocation — remaining-gaps 74/74 run #61. | SEC-15 |
| BD-05 | BOD | `System Manager` ngầm qua role khác | **D** | **PASS — `Test #16`**: `assertNotIn("System Manager", frappe.get_roles(self.users["bod"]))`. | SEC-15 |
| BD-06 | CRM-ADMIN | cấu hình quyền hệ thống | **D** (trừ System Manager) | **PASS — `Test #17`** (`test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537): CRM-ADMIN `assertNotIn("System Manager")` + `assertFalse(has_permission("CRM Lead", "read"))`. | — |

**Phân tích BOD (ADR-005, T0 chốt + đối chiếu T1C 2026-10-19):** trên `v1.87.0`, hook `permission_query_conditions`/`has_permission` chỉ đăng ký cho Lead/Deal/Notification; **không có role/API báo cáo read-only đóng gói sẵn**. Master-plan §2.3 yêu cầu BOD xem báo cáo **không** cần `System Manager`. **Quyết định ADR-005:** `BOD_VIEWER` là role **read-only** xem báo cáo theo phạm vi, **không** cấp `System Manager`; role + permission + API báo cáo được xây trong `crm_cskh` (không dựa vào quyền rộng của baseline). Runtime: **fail-closed đã verify** qua `Test #16`, `Test #17` (xem `test_permissions.py` L528, L537) cho BD-02/03/05/06; BD-01/04 còn ở mức PARTIAL — **reporting scope BOD chưa build**. Chi tiết: `risk-register.md` `RISK-SEC-09` và [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §5.

---

## 10. Endpoint inventory & cơ chế quyền (đọc trên `v1.87.0`)

| Endpoint / file | Cơ chế quyền đã đọc | Giả thuyết rủi ro | Test |
|---|---|---|---|
| `crm/api/doc.get_data` | `frappe.get_list` (permission-aware) + `rows/columns/doctype` từ client | field-level exposure; doctype client-controlled | SEC-02, SEC-25 |
| `crm/api/doc.get_assigned_users` | `frappe.get_all` — **không** kiểm permission | rò rỉ assignee | SEC-19 |
| `crm/api/doc.get_linked_docs_of_document` | **không** kiểm permission trên doc đầu vào; trả title, đặc biệt Call Log `from`/`to` | rò rỉ số điện thoại | SEC-20 |
| `crm/api/doc.remove_assignments` | tham số `ignore_permissions` **từ client** → truyền vào `set_status` | bypass assignment | SEC-09, SEC-21 |
| `crm/api/doc.add_seen` | `doc.check_permission("read")` | (đã có guard) | — |
| `crm/api/doc.delete_bulk_docs` | `frappe.desk.reportview.delete_bulk` | bulk chéo scope | SEC-12 |
| `crm/api/doc.remove_linked_doc_reference` | `frappe.has_permission(...,"write",name)` rồi `save(ignore_permissions=True)` | guard ở entry | SEC-12 |
| `crm/api/activities.get_activities` | nhánh tới `get_lead_activities`/`get_deal_activities` → `has_permission(...,"read",name)` | phụ thuộc model Lead yếu | SEC-04 |
| `crm/api/activities.get_linked_calls/_notes/_tasks` | `frappe.db.get_all` (không perm) nhưng gated sau `has_permission` lead | OK nếu lead-perm đúng | SEC-04 |
| `crm/api/activities.get_attachments` | `frappe.db.get_all("File", ...)` trả `file_url` private | lộ URL file private | SEC-26 |
| `crm/.../crm_call_log.get_call_log` | `frappe.has_permission("CRM Call Log","read",name)` — **DocType-level** | đọc call chéo record | SEC-03, SEC-17 |
| `crm/.../crm_call_log.get_call_contact` | `get_contact_by_phone_number` khi call không ref lead/deal | tra contact theo số ĐT → rò rỉ | SEC-27 |
| `crm/api/assignment_rule.get_assignment_rules_list` | `frappe.get_all` — không kiểm permission | lộ cấu hình rule | SEC-24 |
| `crm/api/assignment_rule.duplicate_assignment_rule` | `frappe.get_doc` + `insert()` — **không** kiểm permission tường minh | user thấp tạo/nhân bản rule | SEC-24 |
| `crm/hooks.py` | hook chỉ cho Lead/Deal/Notification | child doctype không record-level | SEC-17, SEC-18 |
| `crm/api/todo.py` | mirror `lead_owner`; `clear_owner_on_unassign` | desync ToDo/DocShare (ADR-004) | SEC-22, SEC-23 |
| `crm/.../crm_lead.py` (`share_with_agent`) | DocShare `write=1`; gỡ DocShare user≠agent | bất đối xứng với ToDo (ADR-004) | SEC-10, SEC-23 |
| `crm/permissions/org_hierarchy.py` | Sales Manager **ngoài cây** → `return ""` (thấy tất cả) | over-broad | SEC-07 |

---

## 11. Test plan (đã thực thi một phần sau WP-02/03/05; còn mở cho SEC-13/14/16/26, nhánh nội bộ SEC-27 và ~38 ô matrix)

### 11.1. Điều kiện tiên quyết (đã đạt / còn mở)

1. Baseline Frappe CRM `v1.87.0` + Framework `v15.123.1` **đã bootstrap/install/migrate/seed và smoke UI/API trên host hiện tại + GitHub Actions CI run `#38712593755`** ([`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md), [`p0-isolated-verification.md`](../../deploy/dev/evidence/p0-isolated-verification.md)); MariaDB 11.8 compatibility caveat còn mở.
2. App `crm_cskh` cài cùng site (`dev.localhost` + `test_cskh.localhost`); test site dùng cho ba lượt chạy 26/27/28 OK (xem §0.2).
3. Seed đủ 2 team + tài khoản mục §1, deterministic, reset được (xem [`environment-probe.md`](../../deploy/dev/evidence/environment-probe.md) §8 và [`p0-d-runtime.md`](./p0-d-runtime.md) §5.1).
4. Test harness Frappe; **file `tests/test_permissions.py` đã viết** (18 methods) — KHÔNG dùng `tests/security/` (đặt tại `tests/`). Riêng `tests/test_health.py` 4 methods (đa số không phải permission); `test_poc_custom_field.py` 3 methods (không phải permission); `doctype/cskh_app_settings/test_cskh_app_settings.py` 3 methods (không phải permission).

### 11.2. Tổ chức test

- File đã viết: `apps/crm_cskh/crm_cskh/tests/test_permissions.py` (18 permission-boundary methods, đã đọc 543 dòng), **KHÔNG** theo convention `tests/security/test_sec_*.py` ban đầu. Mapping test ↔ matrix được T1C gắn vào từng ô `Thực tế` ở §2–§9 (bản 1.0) và trong [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md) §3.
- Lệnh chạy: `bench --site test_cskh.localhost run-tests --app crm_cskh` (đã chạy 26 OK ở `wp05-security-final.txt`; 27 OK ở `p0-local-final.txt`; 28 OK ở CI run `#38712593755`).
- Dùng API key/session của **tài khoản test** qua `frappe.set_user(...)` (xem `test_permissions.py` L20–L42); **không** commit token/cookie.
- Mỗi ca phải có **cả** positive (trong scope) và negative (ngoài scope) — tuân thủ P0 §13.1.4.

### 11.3. Danh mục ca test âm

Kế thừa P0 §7.5 (SEC-01…SEC-16) **cộng** các giả thuyết từ đọc code:

| ID | Mô tả ngắn | Nguồn |
|---|---|---|
| SEC-01 | A xem Lead của B bằng ID đoán được | P0 §7.5 |
| SEC-02 | A query `get_data` với filter sửa/bỏ | P0 §7.5 |
| SEC-03 | A truy cập `CRM Call Log` của lead B | P0 §7.5 |
| SEC-04 | A đọc Note/Task/Comment ngoài phạm vi | P0 §7.5 |
| SEC-05 | A tải file private của lead B qua URL trực tiếp | P0 §7.5 |
| SEC-06 | TL1 xem Lead/Call của Team 2 | P0 §7.5 |
| SEC-07 | Sales Manager ngoài cây hierarchy | P0 §7.5 |
| SEC-08 | A sửa `lead_owner`/assign người khác | P0 §7.5 |
| SEC-09 | A gọi mutation với `ignore_permissions=true` | P0 §7.5 |
| SEC-10 | Reassign A→B; thử lại toàn bộ API/read/export với A | P0 §7.5 |
| SEC-11 | Chuyển Team1→Team2; leader cũ/mới | P0 §7.5 |
| SEC-12 | Staff export/bulk query ngoài phạm vi | P0 §7.5 |
| SEC-13 | Guest gọi endpoint `crm_cskh` có xác thực | P0 §7.5 |
| SEC-14 | CSRF/session khi POST từ web | P0 §7.5 |
| SEC-15 | BOD Viewer gọi API quản trị user/role/import | P0 §7.5 |
| SEC-16 | API `crm_cskh` trả field nhạy cảm | P0 §7.5 |
| **SEC-17** | A đọc `CRM Call Log` bất kỳ theo `name` (thiếu hook record-level) | MỚI (T1C, `hooks.py`+`crm_call_log.get_call_log`) |
| **SEC-18** | A đọc `FCRM Note`/`CRM Task` bất kỳ theo `name` | MỚI (T1C, `hooks.py`) |
| **SEC-19** | `get_assigned_users` trả assignee cho doc tùy ý | MỚI (T1C, `doc.py`) |
| **SEC-20** | `get_linked_docs_of_document` trả linked docs kèm `from`/`to` | MỚI (T1C, `doc.py`) |
| **SEC-21** | `remove_assignments` với `ignore_permissions=true` unassign chéo scope | MỚI (T1C, `doc.py`) |
| **SEC-22** | Reassign qua ToDo → DocShare cũ còn → người cũ vẫn truy cập | MỚI (T1C, `todo.py`+`crm_lead.py`) |
| **SEC-23** | Reassign qua `lead_owner` → ToDo cũ còn → người cũ vẫn truy cập | MỚI (T1C, `crm_lead.py`+`todo.py`) |
| **SEC-24** | `duplicate_assignment_rule` bởi user quyền thấp | MỚI (T1C, `assignment_rule.py`) |
| **SEC-25** | `get_data` field-level exposure (`rows`/`columns`) | MỚI (T1C, `doc.py.get_data`) |
| **SEC-26** | Lộ `file_url` private qua activities/attachments | MỚI (T1C, `activities.py`) |
| **SEC-27** | `get_call_contact` tra contact theo số ĐT | MỚI (T1C, `crm_call_log.py`) |
| **SEC-28** | BOD không có `System Manager` ngầm; role read-only theo ADR-005 (runtime chưa xác minh) | MỚI (T1C, `hooks.py`) |

### 11.4. Định dạng bằng chứng (P0 §7.7)

Mỗi ca lưu: user/role, Lead ID giả, endpoint/method, HTTP status, response **đã khử dữ liệu nhạy cảm**, tên test + vị trí code, link CI job, kết quả trước/sau, severity, quyết định. **Không** commit token/cookie/PII.

### 11.5. Tiêu chí thoát (G0-07)

- Không còn `Critical/High` mở với kho Lead và đối tượng liên quan.
- Có test âm chạy thật với **≥2 user** khác nhau (không chỉ admin).
- Nếu endpoint quá rộng không sửa được: **hạn chế đường gọi** để role bị ảnh hưởng **không truy cập được**, chứng minh bằng negative tests.

### 11.6. Kết quả runtime theo SEC case

> Bảng bên dưới giữ mapping từng SEC; SEC-13 được hiệu chỉnh theo Round 30, các ô còn lại giữ evidence/gap đã rà riêng. **Round 32 (2026-10-31) HIỆN HÀNH: SEC tally 28/28 PASS** — SEC-16/26 đã PASS ở Round 31 (evidence run #59, xem `t1c-ext1-verification-2026-10-30.md`); SEC-14/15/27 cũng PASS; Round 32 bổ sung 12 ca HTTP transport (run #61 `#41159273106`) làm bằng chứng bổ trợ cho SEC-02/04/13/15/17/25. Lịch sử Round 31: tally **28/28 PASS**. Lịch sử Round 30: tally là **26 PASS + 2 PARTIAL (SEC-16/26) = 28/28**. Artifact CI #40514019123 có 99/99 method PASS và 18/18 HTTP probe; case `sec13-guest` cụ thể trả 403 `PermissionError`, khớp SEC-13 trong P0 §7.5. Vite CORS là supplemental, không phải tiêu chí SEC-13. Probe DEV Round 29 năm route chuẩn ghi riêng hai FAIL phản chiếu File ID (đã xử lý ở Round 31). Không suy rộng số test app thành PASS toàn SEC/matrix.
>
> **Đồng bộ §2–§9 (Round 32):** 19 hàng bảng ma trận §§2–9 còn ghi trạng thái lịch sử Round 28 (`NOT RUN` / `PARTIAL` / `CHƯA XÁC MINH`) trong khi bảng audit 71 ô đã là Round 32 — đã đồng bộ lại cho khớp (LEAD-11/15/16, CL-05/09, TK-04, NT-04, AT-05/06, RA-05/06/07/08/10, LS-04/05/06, BD-03/04). Sau đồng bộ, tally §§2–§9 **= bảng audit = 60 PASS + 8 PARTIAL + 3 DEFER P1 = 71**, không còn ô nào ghi trạng thái mâu thuẫn. **Round 32b (T1C tự phát hiện actor không khớp ô):** hạ `RA-05` PASS → PARTIAL ⇒ **hiện hành 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**.

| SEC | Trạng thái T1C | Bằng chứng / giới hạn (evidence source · revision · site/lệnh) |
|---|---|---|
| 01 | **PASS** (route đã test) | `test_lead_list_and_get_data_only_return_owner_scope` (L206, `test_permissions.py`); `wp05-security-final.txt` 26 OK tại `test_cskh.localhost` · `p0-local-final.txt` 27 OK tại `dev.localhost`+`test_cskh.localhost` · `p0-isolated-verification.md` 28 OK tại GHA PR #1 `#38712593755` (CI). |
| 02 | **PASS** (route đã test) | Cùng test trên + `spoofed = get_data({"lead_owner": self.users["b"]}, ...) == []`. |
| 03 | **PASS** | `test_child_lists_and_activity_reads_stay_with_parent_scope` (L261), `test_child_records_and_direct_private_file_are_record_scoped` (L356) — `frappe.get_list` cho Call Log/Note/Task và `.check_permission` raise. |
| 04 | **PASS** (route `get_linked_*` đã test) | `test_linked_activity_endpoints_check_parent_before_returning_data` (L403) — `guarded_crm.get_linked_calls/_notes/_tasks/_attachments/_linked_docs_of_document` raise khi cross-scope. |
| 05 | **PASS** (route `find_file_by_url` đã test) | `test_private_file_download_and_stale_docshare_are_revoked` (L285) — sau reassign, `find_file_by_url` cho old owner trả `None`. |
| 06 | **PASS** (TL scope test) | `test_team_leader_sees_team_but_not_other_team` (L388). |
| 07 | **PASS** (manager-without-hierarchy fail-closed) | `test_sales_manager_without_hierarchy_is_denied` (L397). |
| 08 | **PASS** | `test_owner_change_and_assignment_bypass_are_denied` (L423) — `lead.save(ignore_permissions=True)` raise. |
| 09 | **PASS** | Cùng test trên + `test_direct_todo_assignment_is_denied_even_with_ignore_permissions` (L490). |
| 10 | **PASS** | `test_stale_todo_cannot_restore_access_after_owner_changes` (L450), `test_cross_team_transfer_blocks_old_owner_and_leader` (L474) — old owner mất quyền Lead + child + ToDo. |
| 11 | **PASS** | `test_cross_team_transfer_blocks_old_owner_and_leader` (L474) — TL2 cũ không thấy Lead cross-team. |
| 12 | **PASS** (delete bulk đã test, report block đã test) | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) — `data_export`, `export_query`, `query_report.run` raise cho Staff/Manager/BOD. `test_bulk_delete_and_unlink_cannot_touch_cross_scope_records` (L437). |
| **13** | **PASS (P0 §7.5 Guest-auth)** | CI #40514019123 artifact `12738607035`, case `sec13-guest`: Guest gọi `/api/method/crm_cskh.api.health.ping` không auth → HTTP 403 `PermissionError`, assertion PASS. Artifact coverage chỉ direct `bench serve`; CORS/Vite là phép đo bổ sung, không thuộc tiêu chí SEC-13 trong P0 §7.5. |
| **14** | **PASS** | HTTP probe CI 18/18 (run #59): `sec14-get` 200, `sec14-post-nocsrf` 400 `CSRFTokenError`, `sec14-post-badtok` 400, `sec14-post-ok` 200; unit `TestSEC14CSRFAndSession` + `TestSEC14CSRFAndSessionHTTP` (9 method) PASS trong 99/99. |
| 15 | **PASS** (nhánh phone-lookup + BOD bulk đã test; BOD reporting scope thuộc P1 — AT-05/BD-01 DEFER) | `test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers` (L319) + `test_bod_viewer_cannot_read_leads_or_manage_users` (L528) + `TestSEC15BODAndRISKSEC12BulkReassign` (4 method PASS trong 99/99 run #59). |
| **16** | **PASS — ĐÓNG Round 31** | remaining-gaps 62/62 (run #59): 12 permlevel surfaces (`crm-get-data`, `frappe-client-get`, `rest-resource-get`, `desk-getdoc`, `desk-get-docinfo`, `frappe-client-get-list`, `frappe-client-get-value`, `crm-activities`, `crm-linked-calls/notes/tasks/docs`) × in-scope + out-of-scope `sentinel_present=false` cho non-system + **3 system positive control** `sentinel_present=true` (non-vacuous) + `sec16-field-cleanup`. `guarded_reporting` (BOD reporting) thuộc P1 (AT-05/BD-01 DEFER). |
| 17 | **PASS** | `test_child_records_and_direct_private_file_are_record_scoped` (L356) — `get_call_log(self.call_b)` raise. |
| 18 | **PASS** (cross record qua `.check_permission`) | Cùng test trên + `test_child_lists_and_activity_reads_stay_with_parent_scope` (L261) cho list scope. |
| 19 | **PASS** | `test_linked_activity_endpoints_check_parent_before_returning_data` (L403) — `guarded_crm.get_assigned_users` raise cho cross-scope. |
| 20 | **PASS** | Cùng test trên — `guarded_crm.get_linked_docs_of_document` raise. |
| 21 | **PASS** | `test_owner_change_and_assignment_bypass_are_denied` (L423) + `test_direct_todo_assignment_is_denied_even_with_ignore_permissions` (L490). |
| 22 | **PASS** (one direction: ToDo cũ không khôi phục quyền Lead sau reassign) | `test_stale_todo_cannot_restore_access_after_owner_changes` (L450); **chưa** có test cho direction ngược lại (DocShare cũ còn khi reassign qua lead_owner). |
| 23 | **PASS per-route** (giới hạn test) | Trạng thái tally hiện hành là PASS; các test reassign được ánh xạ vào SEC-23 đều xanh trong CI #40514019123. Không suy rộng sang mọi nhánh `share_with_agent`/DocShare hoặc coi RISK-SEC-02 đã đóng; các tình huống còn thiếu được nêu trong snapshot Round 25 phía đầu tài liệu. |
| 24 | **PASS** | `test_staff_cannot_duplicate_assignment_rules` (L504). |
| 25 | **PASS** | `test_get_data_does_not_return_permlevel_fields` (L227). |
| **26** | **PASS — ĐÓNG Round 31** | Round 31 run #59: file-oracle 9/9 (out-of-scope `client.get`/REST File → **404 `DoesNotExistError`** indistinguishable, `echoes_supplied_file_id=false`, `contains_file_url=false`; `getdoc` → 403 `PermissionError` sanitized; download out-of-scope/guest 403) + remaining-gaps `sec26-*` (get_filtered_attachments/get_docinfo/getdoc/client-get-list in+out scope) + HTTP 18/18 (custom redaction giữ: `sec26-staff-private` redact, `sec26-admin-private` retain, `get_activities` 4 leak site `file_url_absent`/`value_sha256=null`). Đủ 4 tiêu chí đóng Round 30 (a)-(d). Lịch sử: CI #40514019123 xác nhận custom CRM redaction trên fixture nhưng không gọi năm route chuẩn; T1B DEV artifact Round 29 (30 case, 28 PASS/2 FAIL, SHA-256 `33af91d6af4f7d2394ba7009a1fd32564116b76228eafe6046fbbc6edbb345f2`; 24,503 B; source tree dirty tại `b92f668`) đo 5 route: hai route `frappe.client.get(File)`/REST File phản chiếu request File ID — **đã xử lý ở Round 31**. Owner decision 2026-10-28 không miễn trừ identifier reflection. |
| **27** | **PASS** | 8 method `TestSEC27CallLogFallbackContact` + `TestSEC27CallLogFallbackContactInternal` PASS trong 99/99 run #59 (wrapper `guarded_crm.get_call_log` fail-closed cho non-system; reproduction upstream chỉ pin hành vi, KHÔNG dùng làm bằng chứng leak). Endpoint `get_contact_by_phone_number` chặn Staff/Manager/BOD (L319). |
| 28 | **PASS** (BOD không System Manager, không tạo User/Role/Data Import; CRM-ADMIN fail-closed trên Lead read/write) | `test_bod_viewer_cannot_read_leads_or_manage_users` (L528), `test_crm_admin_role_fails_closed_until_controlled_service_exists` (L537). |

**Chú thích evidence T1C:**
- 26 OK ở `wp05-security-final.txt` do T1B tạo, revision `b382fe4` (HEAD trên `codex/p0-audit-refresh`), site `test_cskh.localhost`. Không thấy stdcapture method-level.
- 27 OK ở `p0-local-final.txt`, cùng revision, site `dev.localhost` + `test_cskh.localhost` (re-migrate). Không thấy stdcapture method-level.
- 28 OK ở `p0-isolated-verification.md` §"Security and source reload", CI run `#38712593755` trên `b382fe4`, GHA clean bootstrap. Không thấy stdcapture method-level.
- **T1C không tái tạo được** tại sao 26 ≠ 27 ≠ 28; nghi vấn test #18 (`test_seed_owner_scope_and_hierarchy_are_applied`) hoặc phụ thuộc vào thứ tự test discovery. Không có manifest phân biệt pass/fail per method trong repo.

---

## 12. Trạng thái xác minh tổng hợp — lịch sử (2026-10-19)

- Số ca thiết kế: **~70 ô ma trận**, 28 ca test âm (`SEC-01…SEC-28`).
- 18 permission-boundary test methods có mã đọc được trong `test_permissions.py` (T1C đọc 543 dòng). Ba lượt chạy tổng đã ghi nhận — **26 / 27 / 28 tests OK** — không thể chọn một con số làm "tổng duy nhất" (xem §0.2 và `t1c-security-verification.md` §2).
- Đối chiếu per-cell (T1C, xem `t1c-security-verification.md` §5): **khoảng 32 ô matrix có mapping test**; **~38 ô matrix còn `CHƯA XÁC MINH`**.
- SEC coverage: **19/28 có pass per route** (xem §11.6); **SEC-13, SEC-14, SEC-16 vẫn `NOT RUN`**; **SEC-26 `NOT IMPLEMENTED coverage`**; **SEC-15, 22, 23, 27 `PARTIAL`**. **0 ca đạt FAIL** (không có test nào fail trong ba lượt chạy có log, nhưng đây **không** đồng nghĩa "không có lỗ hổng" — các route chưa chạy có thể còn lỗi chưa phát hiện).
- T1C **không chạy lại site sạch** trong phiên này (máy thiếu Docker/WSL2); phụ thuộc vào log T1B tại revision `b382fe4` và các link tài liệu. Kết luận runtime cuối cùng vẫn là **G0-07 PARTIAL**.
- Hệ thống **không** được tuyên bố an toàn; CI PR #1 đã pass nhưng security transport/full matrix vẫn mở.

## 13. Việc cần T0 quyết / blocker — lịch sử 2026-10-19, thay bằng snapshot Round 31

1. **WP-05 đã có suite** (xem `wp05-security-final.txt`, `p0-local-final.txt`, `p0-isolated-verification.md`) và remediation fail-closed cho các API đã test; GitHub Actions PR #1 (`#38712593755`, revision `b382fe4`) đã pass. **SEC-13/14/16/26 vẫn `NOT RUN`/`NOT IMPLEMENTED`**; **~38/70 ô matrix chưa có test mapping**; **nhánh nội bộ SEC-27 chưa test**; `risk-register.md` §6 vẫn duy trì RISK-SEC-12 mở. Trạng thái chi tiết ở [`p0-exit-report.md`](./p0-exit-report.md) và `risk-register.md` §4, §6.
2. **ADR-004/005: đã ghi tài liệu và runtime hook đã chạy** — T0 chốt nội dung; file có trong checkout: [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) (thực thi quyền owner/team ở backend/record-level + ownership service tập trung) và [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md) (`BOD_VIEWER` read-only, không System Manager). Implementation đã đọc: `apps/crm_cskh/crm_cskh/permissions.py` hook 7 DocType + `apps/crm_cskh/crm_cskh/api/guarded_crm.py` + `guarded_reporting.py` override whitelist. Behavior runtime đã được 18 test methods xác nhận **trong route đã gọi**.
3. Role `CRM_ADMIN`/`BOD_VIEWER`: **giữ là role PoC** theo P0 §7.1 (BOD read-only). Whitelist field CSKH cho Staff + endpoint server-side (LEAD-06/14) sẽ **chốt cùng schema cụ thể ở P1/P2** — P0 từ chối sửa trực tiếp `CRM Lead`.
4. Không cấp thêm quyền Staff/BOD/CRM_ADMIN hoặc triển khai dữ liệu thật trước khi các gate còn partial được đóng.
5. **Blocker mới T1C mở 2026-10-19** (xem `t1c-security-verification.md` §6): B-T1C-1 (env), B-T1C-2 (SEC-13/14/26 transport test), B-T1C-3 (SEC-27 nội bộ), B-T1C-4 (3 tổng test khác nhau), B-T1C-5 (manifest test mapping cho ~38 ô còn lại).

## 14. Changelog

| Ngày | Người | Nội dung |
|---|---|---|
| 31/10/2026 | T1C | **Round 32b (bổ sung):** hạ ô `RA-05` **PASS → PARTIAL** — 2 ca HTTP `ra05-*` đo actor `Administrator` (System Manager) trong khi ô `RA-05` có Actor = CRM-ADMIN ⇒ evidence **không đúng actor của ô**; chính sách trong repo (`TestRA04CrmAdminReassignBlocked` docstring) cấm đúng việc này. Tách 2 ca `ra05-*` thành **positive control `RA-05-SM`** (§7.0.1, ngoài lưới 71 ô). Ô `RA-05` giữ kỳ vọng P0 = **D tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`); fail-closed đã chứng minh bằng 3 method `TestRA04CrmAdminReassignBlocked` + `Test #17` (PASS trong 99/99 run #61), còn thiếu 1 ca HTTP đo trực tiếp actor `CSKH CRM Admin`. **Tally hiện hành: 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; SEC tally giữ **28/28 PASS**; **G0-07 = PASS (T1C KÝ)**; P0 GO/NO-GO thuộc T0/Owner. KHÔNG merge PR #2. |
| 31/10/2026 | T1C | **Round 32:** kiểm định độc lập 12 ca mới (T1B-ext+2 matrix-gap probe): run #61 `#41159273106` (head `e0ef02f`, merge `136a10c2`, artifact `13026861084` digest `8cfe5f10…`); 99/99 JUnit + 18/18 HTTP + 9/9 file-oracle + 74/74 remaining-gaps; diff chỉ probe + doc (KHÔNG đổi code app). 12 ca → 8 ô PARTIAL → PASS *(con số của Round 32 — lịch sử)*: LEAD-16/LS-06, LEAD-11, ~~RA-05~~, RA-08/BD-04, BD-03, RA-10 *(ô `RA-05` đã bị hạ PASS → PARTIAL ở Round 32b — xem hàng Round 32b)* (đọc Lead/Note PASS; **ghi child 403 fail-closed → P1**). Matrix **60 PASS + 8 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** *(con số của Round 32 — lịch sử; hiện hành sau Round 32b: 59/9/0/3, xem hàng Round 32b ở trên)*; 8 PARTIAL còn lại là nghiệp vụ P1 fail-closed. Tally 28/28 PASS; **G0-07 = PASS (T1C KÝ)**; P0 chờ T0/Owner. KHÔNG merge PR #2; KHÔNG stage/commit/push. |
| 30/10/2026 | T1C | **Round 31:** phục hồi byte-identical bản Round 30 từ snapshot `t1b-pr2-review-e67` (SHA-256 `39405C1E…`, lịch sử bảo toàn) rồi áp kiểm định T1B-ext+1 run #59 (`#41037431038`, head `b1087bc`, merge `682a9d93`, artifact `12968528845` digest `c6b298a8…`): 99/99 JUnit + 18/18 HTTP + 9/9 file-oracle + 62/62 remaining-gaps + Dynamic Link âm/dương. SEC-16 → PASS (12 permlevel surfaces + 3 system positive control); SEC-26 → PASS (đủ 4 tiêu chí: reflection → 404-indistinguishable/403-sanitized, CI regression durable, custom redaction giữ, T1C verify); đồng bộ §11.6 hàng SEC-14/15/27 (hết mâu thuẫn NOT RUN). Tally 28/28 PASS; matrix 52 PASS + 16 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71; **G0-07 = PASS (T1C KÝ)**; P0 chờ T0/Owner. KHÔNG merge PR #2; KHÔNG stage/commit/push. |
| 28/10/2026 | T1C | Round 29: ghi Owner policy cho phép URL visibility permission-governed trên standard Frappe routes; xác minh T1B artifact 30 case (28 PASS/2 FAIL) với SHA-256 `33af91d6af4f7d2394ba7009a1fd32564116b76228eafe6046fbbc6edbb345f2`. Hai FAIL là 403 phản chiếu request File ID ở `frappe.client.get(File)` và REST File; không có body/ID thô, không chứng minh IDOR. Download ngoài scope 403 có/không `fid`; custom CRM redaction giữ nguyên. Matrix Round 28 không đổi; SEC-26/G0-07 PARTIAL, P0 NO-GO. |
| 28/10/2026 | T1C | Round 30: tải và parse CI #40514019123 artifact `12738607035`; xác nhận `sec13-guest` HTTP 403/`PermissionError`/PASS, đúng P0 §7.5, nên SEC tally hiện hành là 26 PASS + 2 PARTIAL (SEC-16/26). Tách Vite CORS (supplemental) khỏi SEC-13. Đối chiếu 8 hash artifact T1B follow-up; hai File-ID reflection FAIL giữ nguyên, cùng Deal/content_hash/H-RFC-1(a) bounded evidence; 65 static guards chỉ local pass, chưa có CI artifact revision mới. G0-07 PARTIAL, P0 NO-GO, matrix Round 28 giữ nguyên. |
| 28/10/2026 | T1C | Round 29: ghi Owner policy cho phép URL visibility permission-governed trên standard Frappe routes; xác minh T1B artifact 30 case (28 PASS/2 FAIL) với SHA-256 `33af91d6af4f7d2394ba7009a1fd32564116b76228eafe6046fbbc6edbb345f2`. Hai FAIL là 403 phản chiếu request File ID ở `frappe.client.get(File)` và REST File; không có body/ID thô, không chứng minh IDOR. Download ngoài scope 403 có/không `fid`; custom CRM redaction giữ nguyên. Matrix Round 28 không đổi; SEC-26/G0-07 PARTIAL, P0 NO-GO. |
| 28/10/2026 | T1C | Round 28: xác minh CI #40514019123 (99/99 PASS) và artifact digest; audit 71 ô (44 PASS/20 PARTIAL/4 NOT RUN/3 CHƯA XÁC MINH); cập nhật 8 ô test riêng + AT-04, hạ TK-04/LS-08/BD-03. Năm route chuẩn chỉ có DEV no-artifact observation; probe mới chưa runtime, URL visibility policy chờ Owner. SEC-26/G0-07 PARTIAL, P0 NO-GO.
| 28/10/2026 | T1C | Round 27: tách URL visibility của standard Frappe route cho in-scope user khỏi cross-scope denial và download permission; ghi đề xuất scope calibration (chưa phải Owner policy), probe code mới của T1B chưa chạy do Docker down. Không gọi URL visibility là IDOR; giữ 25+3 tally, SEC-26 PARTIAL, G0-07 PARTIAL/CHƯA KÝ; không chấm lại 71 ô. |
| 28/10/2026 | T1C | Round 26: bổ sung follow-up DEV ad-hoc cùng phiên — Staff C bị 403 trên từng standard route và direct download; owner download 200/90 B. Không có artifact; không kết luận IDOR, giữ SEC-26 PARTIAL. |
| 28/10/2026 | T1C | Round 25: thêm snapshot CI #40514019123 (99/99 PASS), cập nhật Q1–Q4 đã chốt và RA-04 kỳ vọng D tạm thời; mở rộng ghi nhận SEC-26 sang năm route Frappe chuẩn theo DEV observation ad-hoc (không artifact), nêu rõ owner 200/90 B và ngoài scope 403, không gọi là IDOR. Giữ 25+3 tally, SEC-26 PARTIAL, G0-07 PARTIAL/CHƯA KÝ; không chấm lại toàn bộ 71 ô matrix. |
| 15/10/2026 | T0 | Bản 0.9: ghi nhận CI PR #1 pass cùng clean bootstrap và backup/restore; 18 permission-boundary methods, suite 28/28 pass; SEC-13/14/27 và full matrix vẫn mở. |
| 15/10/2026 | T0 | Bản 0.7: đồng bộ WP-02/03 — app POC, test site và 9 test chức năng đã chạy; ghi probe owner/team sau bật hierarchy; giữ `0/28` SEC, mọi ô quyền `CHƯA XÁC MINH`. |
| 15/10/2026 | T0 | Bản 0.6: ghi nhận baseline/site/seed/UI/API smoke WP-01 trên host hiện tại; cập nhật blocker còn thiếu `crm_cskh`/test harness. Giữ `0/28` SEC, 0 ô PASS/FAIL, toàn bộ permission thực tế `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.1 thiết kế: ma trận A–H, phân tích đặc biệt, endpoint inventory, test plan, 28 ca âm. Tất cả `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.2: đối chiếu checkout — sửa trạng thái version (Framework `v15.123.1`/docker `v4.0.1` đã khóa metadata, runtime chưa chạy), sửa câu "repo chỉ có docs/", cập nhật tiền đề test và §13 (ADR-004/005 vẫn thiếu). Giữ toàn bộ `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.3: đồng bộ theo ADR-004/005 (T0 chốt) — cập nhật tham chiếu ADR, actor PoC (`CRM_ADMIN`, `BOD_VIEWER` read-only/không System Manager), chính sách P0 cho LEAD-06/14 (từ chối sửa trực tiếp `CRM Lead`; whitelist + endpoint server-side chốt sau), hướng ADR-004 cho reassignment, §9 BOD, §11.3 SEC-28, §13. Ghi rõ file ADR chưa có trong checkout. Giữ `0/28` ca chạy, không PASS/FAIL, không đóng rủi ro. |
| 15/10/2026 | T1C | Bản 0.4: ADR-004/005 đã có file — thêm link [`adr-004-owner-team-permissions.md`](./adr-004-owner-team-permissions.md) / [`adr-005-bod-viewer-scope.md`](./adr-005-bod-viewer-scope.md); bỏ ghi chú "file ADR chưa có trong checkout"; đánh dấu B-3 hoàn tất ở mức ghi tài liệu. Giữ B-1/B-2/B-4 mở, B-5 role PoC, `0/28` SEC, mọi trạng thái `CHƯA XÁC MINH`. |
| 15/10/2026 | T1C | Bản 0.5: đồng bộ bằng chứng WP-00/01 (T1A) — làm rõ Docker Desktop **đã cài và đang chạy** nhưng Linux engine trả **HTTP 500**, WSL2 chưa cài; cập nhật câu runtime/blocker tương ứng (§0.2, §11.1), thêm link `environment-probe.md`/`dev-setup.md`. Giữ B-1/B-2/B-4 mở, B-3 hoàn tất ở mức ghi ADR, B-5 role PoC, `0/28` SEC, mọi trạng thái `CHƯA XÁC MINH`. |
| 19/10/2026 | T1C | **Bản 1.0 — độc lập kiểm chứng.** Sửa câu lịch sử "chưa có security suite" trong §0.2 và heading; **phân biệt ba lượt chạy có tổng test khác nhau (26 / 27 / 28)** bằng revision + lệnh + site (xem bảng §0.2 mới, §11.6 mới). Thay §11.6 để mỗi SEC case có `evidence source · revision · lệnh/site` riêng; **không** gộp thành 28. Mở rộng §12: **~38 ô matrix còn `CHƯA XÁC MINH`**, **SEC-13/14/16 vẫn `NOT RUN`**, **SEC-26 `NOT IMPLEMENTED coverage`**, SEC-15/22/23/27 `PARTIAL`. Thêm 5 blocker mới (B-T1C-1…5) trong §13 và link [`t1c-security-verification.md`](../../deploy/dev/evidence/t1c-security-verification.md). Không tuyên bố hệ thống an toàn; **G0-07 vẫn PARTIAL**, không P0 GO. Phiên Codex p0-audit-refresh **không có Docker/WSL2**, nên tái kiểm bằng phân tích tĩnh + đối chiếu log T1B tại revision `b382fe4`. |
