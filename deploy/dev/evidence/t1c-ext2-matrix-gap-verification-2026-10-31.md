# EVIDENCE — T1C kiểm định độc lập 12 ca mới (T1B-ext+2 matrix-gap probe)

- **ROUND 32 (2026-10-31) — T1C tự xác minh GitHub API + tự tải/hash/parse artifact, KHÔNG tin số ghi sẵn.**

## 1. Run mới nhất (xác minh qua API trước khi sửa)

| Run | Status | head_sha | merge_sha | Kết luận |
|---|---|---|---|---|
| `#41159273106` (run #61) | `success` (completed 2026-10-31T02:22:22Z) | `e0ef02f8619c428ef4b2457fc5ce5f04cb0d7baa` | `136a10c20740cff0a2bab5254843e94763aff5ae` | **MỚI NHẤT** |

- PR #2 `state=open`, `base=main`; parents của merge `136a10c2` = `[b382fe4…, e0ef02f…]` ⇒ artifact đúng là merge ref PR #2 (head `e0ef02f` vào `b382fe4`). `main` vẫn `b382fe4`.
- Diff `b1087bc…e0ef02f` = 2 commit: `deploy/dev/seed/ci_g007_remaining_gaps_probe.py` (+563/−5) + `deploy/dev/evidence/t1b-ext1-matrix-gap-probe-2026-10-30.md` (+90). **KHÔNG đổi code app** (`guarded_crm.py`/`hooks.py` nguyên).

## 2. Artifact — tự tải + tự hash

- Artifact id `13026861084`, size 140 924 B, digest API `sha256:8cfe5f107d38fca35bd2dec4e51ef036c521a6107cbc364cd48058916183304a`.
- T1C tự tải zip + tự hash: `8CFE5F107D38FCA35BD2DEC4E51EF036C521A6107CBC364CD48058916183304A` — **KHỚP 100%**.

## 3. Tự parse độc lập

- **99/99 JUnit** (18+12+32+27+10), 0 FAIL/ERROR/SKIP, gate `gate_passed=true`, mọi list fail/error/skip/missing/parse rỗng; auxiliary 8/8.
- **18/18 HTTP** (`coverage_scope` không đổi: direct `bench serve`, không phủ proxy/CORS).
- **9/9 file-oracle** (`probe_result=PASS`, `failed_cases=[]`).
- **74/74 remaining-gaps** (`probe_result=PASS`, `failed_cases=[]`) — tăng **12 ca** so với run #59 (62 ca).
- **Đếm full-tree `<testcase>` (99)**, không chỉ `testsuite` con đầu — bộ đếm ban đầu chỉ đọc suite con đầu ra 33, T1C đã sửa và đếm lại toàn cây.

### 3.1. Đối chiếu regression run #59 → #61 (T1C tự tải cả hai artifact)

- Artifact run #59 (`12968528845`) tự hash `c6b298a8040b0c6a306c6b75e91393fe1715afa91a354fc36282ea26732e9425` — **khớp** giá trị T1C đã ghi ở Round 31 ⇒ **bằng chứng Round 31 còn hiệu lực**.
- So `remaining-gaps` case-id hai run: **+12 ca mới** (đúng 12 id đã nêu), **0 ca bị mất**, **0 ca nào đổi trạng thái assertion** ⇒ không regression trên 62 ca cũ. 74/74 assertion `PASS`.

## 4. 12 ca mới (đối chiếu assertion + positive control + cleanup + source)

Source probe `ci_g007_remaining_gaps_probe.py` @ `e0ef02f` xác nhận mỗi ca có assertion rõ, positive control và cleanup (vd `cleanup_permlevel_field`, `matching_rows_after_cleanup`/`cleanup_verified`).

| Case | Ô matrix | Kết quả (T1C tự đọc) |
|---|---|---|
| `lead16-ls06-get-data-field-scope` | **LEAD-16 + LS-06** | 200, `leaked_marker_count=0` — `get_data rows=[mobile_no,email]` trả field in-scope, field Lead ngoài scope không lộ |
| `lead11-team2-leader-list-scope` | **LEAD-11** | 200 (positive: TL2 thấy Lead team 2, không thấy Lead team 1) |
| `lead11-team2-leader-direct-read-denied` | **LEAD-11** | 403 `PermissionError` |
| `ra05-cross-team-transfer-admin-set-owner` | **RA-05-SM** (positive control, actor System Manager) | 200 (actor `Administrator` chuyển `lead_owner` C→A cross-team) — **không map ô `RA-05` (CRM-ADMIN)** |
| `ra05-cross-team-transfer-new-owner-visible` | **RA-05-SM** (positive control, actor System Manager) | 200 (`new_owner_visible=true`, `old_owner_denied=true`, `old_owner_status=403`) — **không map ô `RA-05` (CRM-ADMIN)** |
| `ra10-new-owner-reads-transferred-lead` | **RA-10** | 200 (positive: chủ mới đọc Lead A2) |
| `ra10-new-owner-reads-child-note` | **RA-10** | 200 (positive: chủ mới đọc Note con) |
| `ra10-new-owner-writes-child-fail-closed` | **RA-10** | **403 `PermissionError` (fail-closed)** |
| `ra10-old-owner-denied-after-transfer` | **RA-10** | 403 (chủ cũ bị chặn) |
| `bod-remove-assignments-denied` | **RA-08 + BD-04** | 403 (BOD revoke/reassign fail-closed) |
| `bod-set-lead-owner-denied` | **RA-08 + BD-04** | 403 (BOD đổi lead_owner fail-closed) |
| `bod-bulk-delete-denied` | **BD-03** | 403 (BOD bulk delete fail-closed) |

### 4.1. Caveat actor của `ra05-cross-team-transfer-admin-set-owner` (T1C tự phát hiện khi đọc source)

- Probe `ci_g007_remaining_gaps_probe.py` @ `e0ef02f` đăng nhập **4 actor**: `team2.leader@dev.local`, `staff.b@dev.local`, `bod.viewer@dev.local`, **`Administrator`** (`ADMIN_PASSWORD` env). **Không có** login `crm.admin@dev.local` (user mang role `CSKH CRM Admin` trong `seed_demo.py`).
- Vì vậy 2 ca `ra05-*` chứng minh: **một actor hệ thống (`Administrator`) đổi `lead_owner` C→A qua HTTP `frappe.client.set_value` → 200, và hệ quả bảo mật đo được là chủ mới thấy / chủ cũ bị chặn (403)**.
- **KHÔNG chứng minh** actor có role `CSKH CRM Admin` (`crm.admin@dev.local`) được đổi `lead_owner` — user đó theo `lead_has_permission` bị chặn `write` (`ptype in ("create","write",...) → False`) và `validate_lead_owner` chặn non-`_is_system`. Đây chính là **fail-closed của `RA-04`** (`CRM-ADMIN = D` theo Owner 2026-10-26). **[Cập nhật Round 32b — xem §4.3:]** cột `Kỳ vọng` của ô `RA-05` **đã đổi `A` → `D tạm thời`** (cùng chính sách `RA-04`) và trạng thái ô **hạ `PASS` → `PARTIAL`**; 2 ca `ra05-*` được tách thành **positive control `RA-05-SM`** (actor System Manager). Kỳ vọng `A` ban đầu chỉ đúng cho **actor hệ thống**, không đúng cho actor `CRM-ADMIN` của ô.
- Cột `Actor` của ô `RA-05` trong matrix ghi `CRM-ADMIN` — T1C **hiệu chỉnh mô tả evidence** (không đổi tiêu chí) để phản ánh đúng actor đã đo. **[Cập nhật Round 32b — xem §4.3:]** việc hiệu chỉnh này **ĐÃ dẫn tới đổi trạng thái**: ô `RA-05` PASS → **PARTIAL**, vì evidence của actor khác không đủ để đóng ô CRM-ADMIN.
- **Giới hạn còn lại:** chưa có ca nào đo user `crm.admin@dev.local` (role `CSKH CRM Admin`) qua HTTP; nhánh đó đã được `Test #17` (`test_crm_admin_role_fails_closed_until_controlled_service_exists`) chứng minh fail-closed ở tầng unit ⇒ **fail-closed đã chứng minh, không có đường mở**. **[Round 32b — xem §4.3:]** vì thiếu ca HTTP đo trực tiếp actor CRM-ADMIN, ô `RA-05` được hạ **PASS → PARTIAL** (không phải lỗ hổng — vẫn fail-closed).

### 4.2. Cơ chế chặn `write` trên child (RA-10) — đọc source, không suy đoán

- `linked_lead_has_permission` (`apps/crm_cskh/crm_cskh/permissions.py`): với `ptype` **không thuộc** `("read","select","email","print")` → `return False` ⇒ `write` trên `FCRM Note`/`CRM Task`/`CRM Call Log` bị chặn với mọi non-system, kể cả chủ mới.
- `lead_has_permission` chặn `ptype in ("create","write","delete","share","import","export")` cho non-system ⇒ `frappe.client.set_value` trên `CRM Lead` của chủ mới cũng bị chặn.
- Vì vậy `403 PermissionError` ở `ra10-new-owner-writes-child-fail-closed` là **hành vi thiết kế theo cơ chế hiện tại**, không phải lỗi cấu hình. `permission_error=true` trong JSON ⇒ fail-closed đúng loại, không phải 500.

## 5. Ghi chú RA-10 (chính xác theo yêu cầu T0)

- Chủ mới `staff.b` ĐỌC Lead A2 → PASS; ĐỌC Note con → PASS; chủ cũ `staff.a` bị 403 sau chuyển giao → PASS.
- **GHI Note con → 403 fail-closed.** KHÔNG chấm "được phép ghi child" là PASS — 403 là kết quả bị từ chối, không phải xác nhận ghi thành công.
- Phần "ghi child sau chuyển giao" chưa triển khai thuộc **backlog P1 (ownership service ADR-004 §2.2)**, phù hợp quyết định Owner (fail-closed trong P0).

## 6. Đối chiếu 4 trục của từng ca mới (assertion · positive control · cleanup · source revision)

T1C đọc **source probe** tại `deploy/dev/seed/ci_g007_remaining_gaps_probe.py` @ `e0ef02f` (đã hash-normalize: SHA-256 `d32356f7…` **khớp 100%** git blob `e0ef02f`; chỉ khác line-ending CRLF do `core.autocrlf=true`) và đối chiếu từng ca:

| Case | Assertion (predicate trong source) | Positive control (không vacuous) | Cleanup (không lại dữ liệu seed) | Nguồn |
|---|---|---|---|---|
| `lead16-ls06-get-data-field-scope` | `error is None and status==200 and not leaked and positive` — `leaked` = 3 marker (tên Lead ngoài scope, SĐT ngoài scope, email ngoài scope) | **Có** — `positive` đòi `mobile_no` + `email` + tên Lead **trong scope** xuất hiện trong body | `atexit.register(revert_emails)` + `finally: revert_emails()` — trả `email` về `""` cho cả A1 và Lead ngoài scope | `lead16_ls06_get_data_field_scope_case` |
| `lead11-team2-leader-list-scope` | `not team1_lead_visible and team2_lead_visible` | **Có** — `team2_lead_visible=true` (Team 2 Leader thấy Lead của team mình) | Read-only, không mutate | `lead11_team2_leader_cross_team_case` |
| `lead11-team2-leader-direct-read-denied` | `status==403 and LEAD_NAME not in text` | Không cần (ca âm) | Read-only | cùng hàm |
| `ra05-cross-team-transfer-admin-set-owner` | `transfer_ok = t_error is None and t_status == 200` | — (hành vi hệ thống) | Lead **giả** (`P0-RA05-<uuid>`) do probe tự tạo + `atexit.register(delete_synthetic)` + `finally: delete_synthetic()`; xoá `CRM Notification` trước rồi xoá Lead (tránh `LinkExistsError`). **Không đụng seed** | `ra05_ra10_new_owner_after_transfer_case` |
| `ra05-cross-team-transfer-new-owner-visible` | `new_owner_visible and old_owner_denied` | **Có** — `new_owner_visible=true` (chủ mới thấy Lead trong list) **và** `old_owner_denied=true` (`old_owner_status=403`) — đo **cả hai** chiều | cùng cơ chế xoá Lead giả | cùng hàm |
| `ra10-new-owner-reads-transferred-lead` | `b_error is None and b_status==200 and a2_name in b_text` | **Có** — phải **thấy** `A2` trong body | Read-only trên seed `A2` | `ra05_ra10_new_owner_after_transfer_case` |
| `ra10-new-owner-reads-child-note` | `note_error is None and note_status==200 and bool(note_names)` | **Có** — `child_note_count=2` (danh sách Note con **không rỗng**) | Read-only | cùng hàm |
| `ra10-new-owner-writes-child-fail-closed` | `set_error is None and set_status==403 and write_marker not in set_text` — đòi **mọi** dấu hiệu ghi thành công đều vắng mặt | — (ca âm) | `write_marker = P0-RA10-WRITE-<uuid>`; vì bị 403 nên **không ghi được** gì ⇒ không lại dữ liệu | cùng hàm |
| `ra10-old-owner-denied-after-transfer` | `a_error is None and a_status==403 and a2_name not in a_text` | Không cần (ca âm) | Read-only | cùng hàm |
| `bod-remove-assignments-denied` | `revoke_error is None and revoke_status==403 and "PermissionError" in revoke_text` | **Có** — đòi đúng lớp lỗi `PermissionError`, không phải 500 | BOD bị chặn ⇒ không revoke được | `bod_mutation_denied_case` |
| `bod-set-lead-owner-denied` | `set_error is None and set_status==403` (+ `permission_error=true`) | **Có** — `permission_error=true` | BOD bị chặn ⇒ `lead_owner` không đổi | cùng hàm |
| `bod-bulk-delete-denied` | `del_error is None and del_status==403 and "PermissionError" in del_text` | **Có** — đúng `PermissionError` | BOD bị chặn ⇒ Lead `A1` **không** bị xoá | cùng hàm |

**Kết luận 4 trục:** (1) **assertion** — mọi ca dùng predicate rõ ràng, không dựa vào "status 200 ⇒ PASS" đơn thuần; (2) **positive control** — 6/12 ca có control dương tường minh (trong đó `lead16` đòi dữ liệu trong scope **phải xuất hiện**, `ra05`/`ra10` đòi **cả hai chiều** chủ mới/chủ cũ); (3) **cleanup** — 3 ca có mutate (ghi email 2 Lead, tạo+xoá Lead giả + Notification, ghi Note con) đều có cơ chế hoàn tác (`atexit` + `finally`), và ca ghi Note con **không ghi được** nên không cần hoàn tác; 3 ca có trường `cleanup_verified` (NT-04/TK-04) vẫn `true` với `matching_rows_after_cleanup=0`; (4) **source revision** — probe đã hash-khớp git blob `e0ef02f`, artifact `head_sha=e0ef02f` / `merge_sha=136a10c2` khớp metadata API.


### 4.3. Hòa giải actor `RA-05` — Round 32b (T1C tự phát hiện khi đọc source)

> **Kết luận:** 2 ca `ra05-*` **KHÔNG** là evidence của ô `RA-05` (Actor = CRM-ADMIN). Chúng đo actor `Administrator` (System Manager) ⇒ được tách thành **positive control `RA-05-SM`** (ngoài lưới 71 ô). Ô `RA-05` (CRM-ADMIN) hạ **PASS → PARTIAL**, kỳ vọng P0 = **D tạm thời** (Owner 2026-10-26, cùng chính sách `RA-04`).

- **Bằng chứng fail-closed cho CRM-ADMIN đã có** (PASS trong 99/99 run #61): 3 method `TestRA04CrmAdminReassignBlocked` (`test_ra04_crm_admin_is_not_system_and_is_blocked_by_validate_lead_owner`, `test_ra04_ownership_service_does_not_exist_yet`, `test_ra04_stays_unclosed_pending_owner_decision`) + `Test #17` (`test_crm_admin_role_fails_closed_until_controlled_service_exists`, L537). Cả 4 đều assert `CRM-ADMIN` **không** có `System Manager` ⇒ `_is_system == False` ⇒ `validate_lead_owner` chặn đổi `lead_owner` (`PermissionError`) + `lead_has_permission` chặn `write`; `lead_owner` giữ nguyên (assert bằng `db.get_value`).
- **Vì sao vẫn PARTIAL chứ không PASS theo kỳ vọng D:** các test trên là **unit/tầng cơ chế** với fixture nội bộ; ô `RA-05` yêu cầu **ca HTTP đo trực tiếp** actor `CSKH CRM Admin` (`crm.admin@dev.local`) trên chuyển C1: C→A ⇒ kỳ vọng **403 `PermissionError`** + `lead_owner` giữ nguyên. Ca đó **chưa có** trong run #61.
- **Chính sách repo áp dụng:** `test_bt1c5_matrix_gaps.py::TestRA04CrmAdminReassignBlocked` (docstring) nói rõ: *"Vì sao KHÔNG viết test 'cho phép': dùng `Administrator` để reassign sẽ **đại diện sai actor** (Administrator ≠ CRM-ADMIN) và tạo **bằng chứng giả**"*. T1C áp dụng đúng quy tắc đó cho `RA-05`.
- **Điều kiện đóng ô:** có 1 ca HTTP đo trực tiếp `crm.admin@dev.local` trên C1→A (kỳ vọng 403) ⇒ `RA-05` → **PASS theo kỳ vọng D**. **KHÔNG** nâng ô chỉ với bằng chứng của `Administrator`. **KHÔNG** mở lại quyết định Owner `RA-04 = D`.
- **Tally sau Round 32b:** 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71 (9 PARTIAL = LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01). SEC tally giữ **28/28 PASS**; **G0-07 = PASS (T1C KÝ)**; P0 GO/NO-GO thuộc T0/Owner.
## 7. Đồng bộ ma trận §§2–§9 (T1C tự phát hiện trong lượt kiểm định)

- **Mâu thuẫn:** bảng audit 71 ô trong `t1c-security-verification.md` đã là Round 32, nhưng **bảng ma trận §§2–§9 của `permission-matrix.md` còn 19 hàng ghi trạng thái lịch sử Round 28**. Tally lệch: §§2–§9 = 44 PASS + 20 PARTIAL + 4 NOT RUN + 3 CHƯA XÁC MINH ≠ bảng audit 59 PASS + 9 PARTIAL + 3 DEFER P1.
- **Đã đồng bộ 19 hàng** (LEAD-11, LEAD-15, LEAD-16, CL-05, CL-09, TK-04, NT-04, AT-05, AT-06, RA-05, RA-06, RA-07, RA-08, RA-10, LS-04, LS-05, LS-06, BD-03, BD-04) — chỉ sửa ô trạng thái + trỏ evidence đúng run, **không đổi tiêu chí**.
- **Sau đồng bộ: tally §§2–§9 = bảng audit = 60 PASS + 8 PARTIAL + 3 DEFER P1 = 71**, kiểm bằng script đếm lại từ cả hai bảng → **mismatch = 0**. *(Con số của **Round 32 — lịch sử**; hiện hành sau **Round 32b**: 59 PASS + 9 PARTIAL + 3 DEFER P1 = 71 — xem §4.3.)*
- `AT-05`/`RA-06`/`RA-07` sửa từ `CHƯA XÁC MINH` → **`DEFER P1`** (KHÔNG phải PASS), theo quyết định Owner 2026-10-29.

## 8. Phân loại các nhóm ô PARTIAL còn lại

| Nhóm | Ô | Tiêu chí P0 đã đáp ứng | Gap đo còn ảnh hưởng P0 | Nghiệp vụ P1 chưa triển khai |
|---|---|---|---|---|
| **(1) P0 đã đáp ứng** | LEAD-12, LEAD-13, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01 (BOD / CRM-ADMIN read scope) | BOD `get_list("CRM Lead")` raise (`Test #16`); `data_export`/`reportview_export_query`/`query_report.run` raise (`Test #5`); `guarded_reporting._require_system_manager()` chặn non-system; CRM-ADMIN `has_permission("CRM Lead","read"/"write") == False` (`Test #17`) | **Không** | BOD scoped reporting (ADR-005) — DEFER P1; CRM-ADMIN controlled service (ADR-004 §2.2) — DEFER P1 |
| | **RA-05 (CRM-ADMIN)** — hạ PASS → PARTIAL ở Round 32b | Fail-closed đã chứng minh ở tầng cơ chế: 3 method `TestRA04CrmAdminReassignBlocked` + `Test #17` đều PASS (CRM-ADMIN không `System Manager` ⇒ `validate_lead_owner` chặn `lead_owner`, `lead_has_permission` chặn `write`) | **Không** — vẫn fail-closed đúng kỳ vọng P0 `D tạm thời`; **nhưng thiếu 1 ca HTTP đo trực tiếp actor `CSKH CRM Admin`** để đóng ô (xem §4.3) | CRM-ADMIN controlled service (ADR-004 §2.2) — DEFER P1 |
| **(2) Gap đo ảnh hưởng P0** | *(không có ô nào)* | — | **Rỗng** — sau đồng bộ 19 hàng, không còn ô nào để PARTIAL/NOT RUN mà thiếu evidence P0 | — |
| **(3) Nghiệp vụ P1, fail-closed** | **9 PARTIAL** (LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01) + 3 DEFER P1 (AT-05, RA-06, RA-07) | Fail-closed đã chứng minh | **Không** | 12 ô — tất cả fail-closed, KHÔNG chấm PASS |

## 9. Verdict T1C (Round 32b — hiệu chỉnh Round 32)

- **SEC tally 28/28 PASS** (SEC-13 giữ `PASS` giới hạn topology P0 — mở lại khi thêm proxy/CORS; SEC-16/26 PASS từ Round 31).
- **Matrix → 59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (Round 32b — xem §4.3; tally §§2–§9 = bảng audit). 9 PARTIAL còn lại đều là **nghiệp vụ P1 chưa triển khai, đang fail-closed** — KHÔNG còn gap đọc chéo/ghi trái quyền trong P0.
- **G0-07 = PASS (T1C KÝ)** — dựa trên runtime độc lập (99/99 + 18/18 + 9/9 + 74/74 + 12 ca mới), KHÔNG chỉ từ CI xanh.
- Giữ nguyên quyết định Owner: Q1–Q4 DECIDED; RA-04 = `D`/fail-closed (P0); SEC-13 theo topology P0; AT-05/RA-06/RA-07 defer P1. KHÔNG mở lại nếu chưa có bằng chứng mâu thuẫn mới.

## 10. Bàn giao residual P1 (owner + điều kiện mở lại)

| Residual | Owner | Điều kiện mở lại |
|---|---|---|
| Ownership service (ADR-004 §2.2) — RA-04 (D), **RA-05 (CRM-ADMIN, PARTIAL — hạ ở Round 32b)**, RA-06/RA-07, LEAD-13 (CRM-ADMIN read scope), **RA-10 phần ghi child** (đã đo → 403 fail-closed) | T0/Owner (policy) + T1B (service + test âm/dương) → T1C review | Service tồn tại + test âm (non-owner bị chặn) **và** test dương (chủ mới ghi child thành công) trên CI + Owner chốt policy. **Đóng ô `RA-05`:** chỉ cần 1 ca HTTP đo trực tiếp `crm.admin@dev.local` trên C1→A (kỳ vọng 403) — **KHÔNG** mở lại quyết định Owner `RA-04 = D` |
| BOD scoped reporting (ADR-005) — 7 ô PARTIAL (LEAD-12, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01) + AT-05 | T0/Owner (scope) + T1B (API BOD riêng + test) → T1C review | API reporting theo scope cho BOD + test chứng minh đúng phạm vi và không đọc ngoài phạm vi + `Test #5`/`Test #16` vẫn xanh |
| SEC-13 topology constraint | T0/Owner (topology P1) + T1B (probe tại proxy) → T1C review | Thêm reverse-proxy/CORS ⇒ SEC-13 revert PARTIAL, đo lại tại proxy boundary |

## 11. Bất biến / KHÔNG làm

- **KHÔNG merge PR #2.** KHÔNG tự ký `P0` (GO/NO-GO thuộc T0/Owner theo đủ 10 gate).
- KHÔNG đổi policy/workflow/code/test; KHÔNG sửa file T1A/T1B/Owner/ADR; KHÔNG stage/commit/push.
- Bàn giao verdict + residual P1 cho **T1A** để tổng hợp P0.

## 12. Hồ sơ đã cập nhật + kiểm tra bất biến (T1C)

| Hồ sơ | Nội dung Round 32 |
|---|---|
| `deploy/dev/evidence/t1c-security-verification.md` | Snapshot Round 32; 12 ca mới; caveat actor RA-05; cơ chế chặn `write` child; đồng bộ 19 hàng; phân loại 3 nhóm; nhật ký §17 |
| `docs/architecture/permission-matrix.md` | Snapshot Round 32; **19 hàng §§2–§9 đồng bộ**; ô RA-05 làm rõ actor đã đo; §11.6 SEC 28/28; changelog |
| `docs/architecture/risk-register.md` | Snapshot Round 32; §4.1 blocker + 3 hàng residual P1 + hàng caveat actor RA-05; changelog |
| `deploy/dev/evidence/t1c-ext2-matrix-gap-verification-2026-10-31.md` | File này — bằng chứng chi tiết Round 32 |

**Bất biến đã kiểm bằng script (`_recovery/r31/final_verify.py`, 25/25 PASS):**

1. Bảng ma trận §§2–§9 và bảng audit 71 ô có **cùng tập ID** và **cùng trạng thái** từng ô (mismatch = 0).
2. Tally cả hai bảng = **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**; không còn ô `NOT RUN`/`CHƯA XÁC MINH` trong thân ma trận.
3. §11.6 có đúng **28 dòng SEC, tất cả PASS** (28/28).
4. Ô `RA-10` ghi rõ phần **ghi child = 403 fail-closed → P1**, không chấm "được phép ghi" là PASS.
5. Các quyết định Owner còn nguyên: Q1–Q4 `DECIDED`; RA-04 = `D`/fail-closed; SEC-13 theo topology P0; AT-05/RA-06/RA-07 `DEFER P1`.
6. `risk-register.md` có 2 hàng residual P1 + điều kiện mở lại + `G0-07 = PASS` + câu **KHÔNG merge PR #2**.
7. `git diff --check` **exit 0** (không lỗi whitespace); 4 file T1C-owned line-ending **thuần nhất** (CRLF, khớp quy ước repo `core.autocrlf=true`), diff **không** có churn line-ending (235/3 · 66/42 · 51/10).

**Lưu ý về `git diff --check`:** còn 1 warning `LF will be replaced by CRLF` cho `deploy/dev/apply-crm-vite-dev-patch.py` — file **không thuộc** phạm vi T1C (thuộc T1A), T1C không sửa.
