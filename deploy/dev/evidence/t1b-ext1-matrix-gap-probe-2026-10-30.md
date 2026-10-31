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
| `ra05-cross-team-transfer-admin-set-owner` | **System Manager (`Administrator`)** chuyển `lead_owner` Lead giả C→A (cross-team) → 200. **Đây là positive control `RA-05-SM`, KHÔNG phải evidence của ô `RA-05` (CRM-ADMIN)** — xem ghi chú actor bên dưới. |
| `ra05-cross-team-transfer-new-owner-visible` | chủ mới thấy Lead giả trong list; chủ cũ bị 403; dọn sạch (xoá CRM Notification trước — CRM tự tạo khi insert). Cũng là **positive control `RA-05-SM`** (actor `Administrator`), **không** map ô `RA-05`. |
| `bod-remove-assignments-denied` | BOD `remove_assignments` → 403 PermissionError (RA-08/BD-04). |
| `bod-set-lead-owner-denied` | BOD `set_value` `lead_owner` → 403 (RA-08). |
| `bod-bulk-delete-denied` | BOD `delete_bulk_docs` → 403 PermissionError (BD-03). |

Bổ trợ: `csrf_token()` thêm fallback `/desk` cho actor không vào được `/cskh`
(BOD — `/cskh` trả 403 với role này).

> **Hiệu chỉnh actor của 2 ca `ra05-*` (T1C Round 32b, 2026-10-31):** 2 ca này login
> actor **`Administrator` (System Manager)**, trong khi ô `RA-05` của lưới 71 ô có
> **Actor = CRM-ADMIN** (`crm.admin@dev.local`). ⇒ 2 ca `ra05-*` **chỉ chứng minh
> positive control `RA-05-SM`** (đường HTTP `frappe.client.set_value` hoạt động với
> actor hệ thống + hệ quả chuyển giao đúng), **KHÔNG phải** evidence HTTP của ô
> `RA-05`; ô `RA-05` đã bị T1C hạ **PASS → PARTIAL** (tally hiện hành
> **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71**).
> Chi tiết: [`t1c-ext2-matrix-gap-verification-2026-10-31.md`](t1c-ext2-matrix-gap-verification-2026-10-31.md) §4.1/§4.3,
> [`t1c-security-verification.md`](t1c-security-verification.md) §"Round 32b",
> [`permission-matrix.md`](../../docs/architecture/permission-matrix.md) §7.0.1.
> **Điều kiện đóng ô `RA-05`:** có 1 ca HTTP đo trực tiếp `crm.admin@dev.local`
> trên C1→A (kỳ vọng 403) ⇒ ô chuyển PASS theo kỳ vọng `D tạm thời`.

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

## 5. Trạng thái governance tại thời điểm báo cáo (2026-10-30 — ảnh chụp lịch sử)

> ⚠️ **Bảng dưới là ảnh chụp lịch sử tại revision `ac93d2c` (2026-10-30)**, giữ nguyên
> để không sửa lịch sử. Trạng thái **hiện hành** đã tiến triển sau đó — xem
> [`t1c-ext2-matrix-gap-verification-2026-10-31.md`](t1c-ext2-matrix-gap-verification-2026-10-31.md)
> (T1C Round 32/32b) và [`t1b-final-handover-2026-10-31.md`](t1b-final-handover-2026-10-31.md).

| Hạng mục | Trạng thái (tại 2026-10-30) |
|---|---|
| Inventory method | **99** |
| SEC tally | 26 PASS + 2 PARTIAL (SEC-16, SEC-26) |
| G0-07 | PARTIAL / CHƯA KÝ |
| P0 | NO-GO |
| PR #2 | open — KHÔNG merge |

**Bàn giao:** ca âm/dương của các ô khoảng trống đều đạt + CI xanh. Không đề
nghị G0-07 PASS.

### 5.1 Trạng thái hiện hành (T1C Round 32 + 32b — cập nhật sau báo cáo này)

| Hạng mục | Trạng thái hiện hành |
|---|---|
| Inventory method | **99** (không đổi) |
| Matrix 71 ô | **59 PASS + 9 PARTIAL + 0 NOT RUN + 3 DEFER P1 = 71** (Round 32b) |
| 9 ô PARTIAL | LEAD-12, LEAD-13, **RA-05**, CL-08, TK-07, NT-07, LS-08, LS-10, BD-01 — đều fail-closed, nghiệp vụ P1 chưa triển khai |
| SEC tally | **28/28 PASS** |
| G0-07 | **PASS (T1C ký Round 32)** — verdict không đổi sau Round 32b |
| GO/NO-GO P0 | Thuộc **T0/Owner** — T1A/T1B/T1C không tự ký |
| PR #2 | open — KHÔNG merge |

**Điểm không đồi nhất giữa 2 ca `ra05-*` của báo cáo này và hiệu chỉnh Round 32b:**
các **probe/assertion không đổi** (vẫn 74/74 PASS, `ra05-*` vẫn đo actor
`Administrator`); chỉ **diễn giải evidence** trong tài liệu được hiệu chỉnh để 2 ca
đó được tính là **positive control `RA-05-SM`** thay vì evidence của ô `RA-05`.
