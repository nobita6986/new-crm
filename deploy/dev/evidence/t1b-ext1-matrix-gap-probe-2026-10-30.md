# T1B — Chốt khoảng trống matrix (LEAD-16/LS-06, LEAD-11, RA-05/RA-10, BOD) + CI cuối

**Ngày:** 2026-10-30 (UTC+7)
**Worktree:** `D:\HRP-app\pr2-ext1`
**PR:** [#2](https://github.com/nobita6986/new-crm/pull/2) — `open`, **KHÔNG merge**

---

## 0. Prompt (công việc T0 giao)

- LEAD-16 / LS-06: yêu cầu trực tiếp `mobile_no`, `email` của Lead ngoài scope, kèm
  positive control trong scope.
- LEAD-11: kiểm trực tiếp Team Leader ngoài team.
- RA-05 / RA-10: quyền của chủ mới sau chuyển giao.
- RA-08 / BD-04 / BD-03: BOD bị chặn reassignment, revoke và bulk delete.
- Giữ inventory 99 method; chỉ bổ sung HTTP probe. Dynamic Link và File-oracle
  đã xử lý; duy trì regression cho chúng.

## 1. Đối chiếu artifact hiện có

Probe `ci_g007_remaining_gaps_probe.py` trước khi sửa (62 case) **không** phủ:
get_data field-level cho CRM Lead, Team Leader cross-team, chủ mới sau chuyển
giao, và BOD mutation. Các case này chỉ có JUnit gián tiếp hoặc `CHƯA XÁC MINH`
theo `permission-matrix.md`. Chỉ bổ sung probe — **không** thêm method JUnit
(inventory giữ 99).

## 2. Thay đổi (chỉ `deploy/dev/seed/ci_g007_remaining_gaps_probe.py`)

| Case mới | Nội dung |
|---|---|
| `lead16-ls06-get-data-field-scope` | Đặt email giả riêng cho A1/C1 qua admin, staff gọi `crm.api.doc.get_data` với `rows=["name","mobile_no","email"]`; trong scope CÓ mobile/email (positive), ngoài scope KHÔNG lộ (leaked=0). Revert email sau khi đo. |
| `lead11-team2-leader-list-scope` | Team 2 Leader: list thấy C1 (team mình, positive) và KHÔNG thấy A1 (Team 1). |
| `lead11-team2-leader-direct-read-denied` | Team 2 Leader đọc trực tiếp A1 → 403. |
| `ra10-new-owner-reads-transferred-lead` | staff.b (chủ mới của A2 sau seed A→B) đọc A2 → 200 (positive). |
| `ra10-new-owner-reads-child-note` | staff.b đọc Note con của A2 → 200 (positive). |
| `ra10-new-owner-writes-child-fail-closed` | staff.b `set_value` title Note con → 403 — pin hành vi fail-closed hiện hành (`linked_lead_has_permission` chỉ cho read/select/email/print với non-system; ownership service chưa có, RA-04 = D). |
| `ra10-old-owner-denied-after-transfer` | staff.a (chủ cũ) đọc A2 → 403 (negative). |
| `ra05-cross-team-transfer-admin-set-owner` | admin chuyển `lead_owner` Lead giả C→A (cross-team) → 200. |
| `ra05-cross-team-transfer-new-owner-visible` | chủ mới thấy Lead giả trong list; chủ cũ bị 403; dọn sạch (xoá CRM Notification trước — CRM tự tạo khi insert). |
| `bod-remove-assignments-denied` | BOD `remove_assignments` → 403 PermissionError (RA-08/BD-04). |
| `bod-set-lead-owner-denied` | BOD `set_value` `lead_owner` → 403 (RA-08). |
| `bod-bulk-delete-denied` | BOD `delete_bulk_docs` → 403 PermissionError (BD-03). |

Bổ trợ: `csrf_token()` thêm fallback `/desk` cho actor không vào được `/cskh`
(BOD — `/cskh` trả 403 với role này).

## 3. Verification (cục bộ, cùng revision)

| Hạng mục | Kết quả |
|---|---|
| `ruff check` + `ruff format --check` | PASS |
| Static AST guards | 62/62 PASS |
| 5 module JUnit | 99/99 PASS (18+12+32+27+10) |
| `ci_junit_gate.py` | `gate_passed=true`, 99/99 |
| HTTP transport probe | 18/18 PASS (regression) |
| File-oracle HTTP | 9/9 PASS (regression) |
| Remaining-gaps probe | **74/74 PASS**, `failed_cases=[]` |
| dl01 Dynamic Link regression | 4/4 PASS |
| Cleanup fixture | email A1/C1 revert `''`; lead giả P0-RA05 xoá sạch; không Note P0-RA10 sót |
| `git diff --check` | PASS |
| Secret scan | 0 password/URL/cookie value/sid (chỉ header name, cookie value `[REDACTED]`) |

## 4. Commit & CI

| Hạng mục | Giá trị |
|---|---|
| Commit | `ac93d2c` — `T1B-ext+1: probe chot khoang trong LEAD-16/LS-06, LEAD-11, RA-05/RA-10, BOD` |
| Push | `origin codex/p0-t1a-g001-g002-g009` (`b1087bc..ac93d2c`) |
| CI run | [`#41158645330`](https://github.com/nobita6986/new-crm/actions/runs/41158645330) — **`success`** |
| Run head SHA | `ac93d2c9290b8f24b639d6d03166a9040edc50d0` |
| Merge SHA | `6c9c7b992fae31fa074fcf32ee53a603ab649186` |
| Artifact id | `13025728421` / `p0-g007-evidence-41158645330` (140999 B) |
| Digest | `sha256:9b0f53267199e8431819105746088354a51175a38b219f98c5c63ca511879f66` — SHA-256 tính lại **khớp** |
| `g007-per-method-gate.json` | `gate_passed=true`, 99/99/99, `counts={PASS:99}` |
| 5 JUnit XML | 99 `<testcase>` |
| `remaining-gaps.json` | `probe_result=PASS`, `case_count=74`, `failed_cases=[]` |
| Workflow outcomes | 10/10 `success` (bootstrap, lint, seed, per_method, per_method_gate, http_transport, file_oracle_unit, file_oracle_http, remaining_gaps_http, backup_restore) |

## 5. Trạng thái governance (không đổi)

| Hạng mục | Trạng thái |
|---|---|
| Inventory method | **99** |
| SEC tally | 26 PASS + 2 PARTIAL (SEC-16, SEC-26) |
| G0-07 | PARTIAL / CHƯA KÝ |
| P0 | NO-GO |
| PR #2 | open — KHÔNG merge |

**Bàn giao:** ca âm/dương của các ô khoảng trống đều đạt + CI xanh. Không đề
nghị G0-07 PASS.
