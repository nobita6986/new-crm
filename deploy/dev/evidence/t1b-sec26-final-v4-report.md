# T1B — Báo cáo SEC-26 Final v4 (Static Review)

**Commit SHA:** `030569cecddfd8a16d0ee1ffc1255b5e18967845` (`030569c`)
**Branch:** `codex/p0-t1a-g001-g002-g009`
**Loại báo cáo:** Static review, không sửa code (không phát hiện lỗi mới)
**Nguồn verify:** `git rev-parse HEAD` tại 030569c; `git log --oneline -6`
**Ngày:** 2026-10-19 (UTC+7)

---

## 1. Tổng T1B (6 commit)

```
030569c T1B-SEC26-test: drop content_hash assertion — contract upstream unverified
f98e28c T1B-SEC26-test: match CRM v1.87.0 source — upstream get_attachments returns file_url
8aaf930 docs(p0): T1A hoan tat runbook cho Owner (G0-01/G0-02/G0-09)
f73adce T1B-SEC26-test: assert entry by name, mark NOT RUN until runtime evidence
a78923b docs(p0): T1A runtime probe lan 3 + sua phan tich tinh
43980d5 T1B-SEC26: redact file_url for non-system caller in get_attachments + get_linked_docs_of_document
```

Test count tại HEAD: v1 = 12 method, v2 = 32 method, **tổng = 44 method** (`Select-String '^\tdef test_'`).

---

## 2. Static review SEC-26 tại SHA `030569cecddfd8a16d0ee1ffc1255b5e18967845`

### 2.1 TestSEC26FileURLLeak — 6 method

| # | Method | Caller | File | Test purpose |
|---|---|---|---|---|
| 1 | `test_get_attachments_redacts_file_url_for_non_system_user` | non-system `users["a"]` | private | Wrapper redact `file_url` |
| 2 | `test_get_attachments_preserves_file_url_for_system_caller` | Administrator | private | Wrapper giữ URL nguyên bản |
| 3 | `test_get_attachments_does_not_redact_non_private_file` | non-system `users["a"]` | public | Wrapper giữ URL public |
| 4 | `test_get_linked_docs_of_document_shape_probe_only` | non-system `users["a"]` | (n/a) | Shape regression (NOT redaction proof) |
| 5 | `test_get_attachments_does_not_leak_cross_scope_file_url` | non-system `users["a"]` | private (cross-scope) | Cross-scope filter |
| 6 | `test_find_file_by_url_returns_none_for_old_owner_after_reassign` | Administrator | (utility) | Reassign cleanup |

### 2.2 Assertions liên quan `file_url` (6 statements)

| # | Method | Statement | Purpose |
|---|---|---|---|
| 1 | `test_get_attachments_redacts_file_url_for_non_system_user` | `self.assertNotIn("file_url", private_entry)` | non-system + private → URL bị redact |
| 2 | `test_get_attachments_preserves_file_url_for_system_caller` | `self.assertIn("file_url", private_entry)` | system caller → URL tồn tại |
| 3 | `test_get_attachments_preserves_file_url_for_system_caller` | `self.assertEqual(private_entry["file_url"], self.file_url_value)` | system caller → URL khớp fixture từ upstream |
| 4 | `test_get_attachments_does_not_redact_non_private_file` | `self.assertIn("file_url", public_entry)` | public file → URL tồn tại |
| 5 | `test_get_attachments_does_not_redact_non_private_file` | `self.assertEqual(public_entry["file_url"], public_url)` | public file → URL khớp fixture từ upstream |
| 6 | `test_get_linked_docs_of_document_shape_probe_only` (conditional, trong `if entry.get("is_private"):`) | `self.assertNotIn("file_url", entry)` | Shape probe defense-in-depth (chỉ fire khi upstream tương lai trả File trong shape này) |

**Tổng: 6 assertion statements `file_url`** (5 ở 3 test chính + 1 conditional ở shape probe).

Cả 5 assertion ở 3 test chính đều pin theo contract CRM v1.87.0 đã verify: upstream `crm.api.activities.get_attachments` trả `file_url` (signed URL cho private, public URL cho non-private). Wrapper `_redact_private_file_urls` (commit `43980d5`) redact khi `is_private=1` + non-system caller; giữ nguyên cho system caller hoặc public file.

### 2.3 Assertions `content_hash` — KHÔNG CÒN

| Location | Status |
|---|---|
| `test_get_attachments_redacts_file_url_for_non_system_user` | ~~`assertNotIn("content_hash", private_entry)`~~ — **ĐÃ BỎ** |
| `test_get_attachments_preserves_file_url_for_system_caller` | ~~`assertIn("content_hash", private_entry)`~~ — **ĐÃ BỎ** |
| `test_get_linked_docs_of_document_shape_probe_only` (conditional) | ~~`assertNotIn("content_hash", entry)`~~ — **ĐÃ BỎ** |
| `test_get_attachments_does_not_redact_non_private_file` | Không bao giờ assert (chỉ pin `file_url`) |
| `test_get_attachments_does_not_leak_cross_scope_file_url` | Không assert `content_hash` |
| `test_find_file_by_url_returns_none_for_old_owner_after_reassign` | Không liên quan `content_hash` |

**0 assertion `content_hash`** tại HEAD. Mọi reference còn lại (8 chỗ) chỉ trong docstring/comment giải thích lý do bỏ và TODO T1B-ext+1.

### 2.4 Docstring audit — không tuyên bố upstream `activities.get_attachments` trả `content_hash`

Tất cả reference `content_hash` trong docstring (4 method + 1 class docstring) đều theo một trong các ngữ cảnh sau:

| Ngữ cảnh | Số chỗ | Ví dụ |
|---|---|---|
| "Contract upstream chưa được verify" | 3 | "Test này KHÔNG chứng minh được việc redact `content_hash` vì contract upstream CRM v1.87.0 chưa được verify chắc chắn..." |
| "Tránh vacuous truth / false negative" | 3 | "Nếu upstream không trả `content_hash` thì `assertNotIn` sẽ pass một cách vô nghĩa" |
| "TODO T1B-ext+1 cần seed upstream" | 3 | "TODO T1B-ext+1: viết test riêng có seed upstream trả `content_hash` để pin redaction này" |
| "Wrapper vẫn redact `content_hash` (defense-in-depth)" | 2 | "Wrapper code vẫn redact `content_hash` (xem `_FILE_URL_FIELDS`) khi upstream trả; chỉ là test chưa pin" |
| "Upstream `get_linked_docs_of_document` KHÔNG trả `content_hash`" | 1 | "Upstream v1.87.0 KHÔNG trả `file_url` / `content_hash` (theo user direction)" — **đúng với `doc.get_linked_docs_of_document`**, không phải `activities.get_attachments` |

**Không có docstring nào khẳng định upstream `crm.api.activities.get_attachments` trả `content_hash`.**

### 2.5 `test_get_linked_docs_of_document_shape_probe_only` — chỉ shape probe

| Đặc điểm | Xác nhận |
|---|---|
| Method được đánh tên `..._shape_probe_only` | ✓ (rõ ràng) |
| Docstring nói "shape probe (NOT proof of redaction)" | ✓ |
| Docstring nói "**KHÔNG** coi test này là PASS cho SEC-26 redaction" | ✓ |
| Chỉ assert `assertIsInstance(linked, list)` | ✓ (shape) |
| Conditional `assertNotIn("file_url", entry)` chỉ fire khi `entry.get("is_private")` truthy | ✓ (defense-in-depth) |
| STATUS ghi "NOT RUN" | ✓ |

Test này được pin làm shape regression (wrapper gọi upstream đúng cách + trả list) — KHÔNG phải bằng chứng redaction.

---

## 3. Diff tại SHA `030569cecddfd8a16d0ee1ffc1255b5e18967845`

### 3.1 Diff so với `f98e28c` (commit T1B trước)

```diff
 apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py | 53 ++++++++++++++++------
 1 file changed, 38 insertions(+), 15 deletions(-)
```

(`git diff --stat apps/crm_cskh/`)

### 3.2 Diff toàn bộ T1B (6 commit, `158e0fd~1..HEAD`)

```diff
 apps/crm_cskh/crm_cskh/api/guarded_crm.py                       |  70 ++-
 apps/crm_cskh/crm_cskh/hooks.py                                 |   1 +
 apps/crm_cskh/crm_cskh/tests/test_security_remediation.py       |  387 ++++++
 apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py    | 1207 ++++++++++++++++++++
 4 files changed, 1663 insertions(+), 2 deletions(-)
```

Patch file: `t1b-final-g0-07-sec-remediation.patch` (149 KB).

### 3.3 Files đã sửa (chỉ T1B)

| File | Status | Trong HEAD? |
|---|---|---|
| `apps/crm_cskh/crm_cskh/api/guarded_crm.py` | modified | Có (T1B code, KHÔNG sửa tại `030569c`) |
| `apps/crm_cskh/crm_cskh/hooks.py` | modified | Có (T1B code, KHÔNG sửa tại `030569c`) |
| `apps/crm_cskh/crm_cskh/tests/test_security_remediation.py` | added | Có (T1B v1) |
| `apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py` | modified | Có (T1B v2 — sửa tại `030569c`) |

**Báo cáo này KHÔNG sửa file test nào.** Tất cả số liệu lấy từ `git show 030569c` + static review trực tiếp.

---

## 4. Runtime Evidence — VẪN NOT RUN

| Mục | Trạng thái |
|---|---|
| Runtime evidence cho `030569c` | **CHƯA CÓ** |
| Runtime evidence cho toàn bộ 6 commit T1B | **CHƯA CÓ** |
| Docker trên host | **KHÔNG CÓ** (đã verify `where docker` rỗng) |
| Bench trên host | **KHÔNG CÓ** (đã verify `where bench` rỗng) |
| Site `test_cskh.localhost` running | **KHÔNG CÓ** |
| Push lên remote | **KHÔNG** (theo user direction) |
| PR | **KHÔNG tạo** (theo user direction) |

### 4.1 Baseline runtime (CHƯA áp dụng cho T1B)

| Log | Revision | Site | Tổng test |
|---|---|---|---|
| `wp05-security-final.txt` | `b382fe4` | `test_cskh.localhost` | 26 OK / 7.964s |
| `p0-local-final.txt` | `b382fe4` | `dev.localhost` + `test_cskh.localhost` | 27 OK / 9.037s |
| `p0-isolated-verification.md` | `b382fe4` (PR #1) | GitHub Actions `#38712593755` | 28 OK / 8.201s |

**Tất cả baseline đều tại `b382fe4` — trước 6 commit T1B.** SEC-26 risk chưa có runtime evidence.

---

## 5. Trạng thái risk & per-cell

### 5.1 SEC-26 tại HEAD

| Thuộc tính | Trạng thái |
|---|---|
| Wrapper code `_redact_private_file_urls` | **Implemented** (commit `43980d5`) |
| Test method count | 6 (trong class `TestSEC26FileURLLeak`) |
| Assertion `file_url` | 6 statements (5 chính + 1 conditional shape probe) |
| Assertion `content_hash` | 0 |
| Docstring về upstream trả `content_hash` | KHÔNG có (đúng — chưa verify) |
| Linked-doc test là bằng chứng redaction? | KHÔNG (chỉ shape probe) |
| Runtime evidence | **NOT RUN** |
| Risk | **PARTIAL** (code implemented, runtime pending) |

### 5.2 G0-07 per-cell summary tại HEAD

| SEC case | Per-cell | Runtime |
|---|---|---|
| SEC-13 Guest | PASS (logic) | NOT RUN |
| SEC-14 CSRF | PASS (logic) | NOT RUN |
| SEC-16 Field exposure | PASS (logic) | NOT RUN |
| **SEC-26 file_url leak** | **PARTIAL** (logic sharp, runtime pending) | **NOT RUN** |
| SEC-27 Call Log fallback | PASS (logic) | NOT RUN |
| SEC-15 BOD Viewer | PASS (logic) | NOT RUN |
| SEC-22 Stale ToDo | PASS (logic) | NOT RUN |
| SEC-23 Stale Comment | PASS (logic) | NOT RUN |
| RISK-SEC-12 Bulk delete | PASS (logic) | NOT RUN |
| RISK-SEC-02 Cross-team | PASS (logic) | NOT RUN |

**SEC-26 KHÔNG đóng risk.** G0-07 tổng thể chưa ký.

---

## 6. Tuyên bố (theo user direction)

- **Không sửa code test** trong prompt này (static review không phát hiện lỗi mới).
- **Không push** lên remote.
- **Không tạo PR**.
- **Không tuyên bố test PASS** — tất cả 6 method trong `TestSEC26FileURLLeak` ghi `STATUS: NOT RUN`.
- **SEC-26 = PARTIAL** (remediation code implemented, assertions sharpened, runtime NOT RUN).
- **G0-07 = chưa ký** (còn 1 SEC PARTIAL ở runtime).
- **Không touch** T1A/T1C/Owner files (xem §7).
- Bàn giao diff (§3) + xác nhận runtime vẫn NOT RUN (§4).

---

## 7. Cross-ref — Files KHÔNG touch (working tree của các tác nhân khác)

```
M  deploy/dev/evidence/t1a-g001-metadata-recheck.md
M  deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md
M  deploy/dev/evidence/t1a-g009-vi-checklist.md
M  deploy/dev/evidence/t1c-security-verification.md
M  docs/architecture/adr-004-owner-team-permissions.md
M  docs/architecture/adr-005-bod-viewer-scope.md
M  docs/architecture/p0-exit-report.md
M  docs/architecture/permission-matrix.md
M  docs/architecture/risk-register.md
```

Tôi KHÔNG sửa, KHÔNG stage, KHÔNG commit các file này.

---

## 8. Tham chiếu nhanh

| Mục | Giá trị |
|---|---|
| Commit SHA | `030569cecddfd8a16d0ee1ffc1255b5e18967845` |
| Short SHA | `030569c` |
| Branch | `codex/p0-t1a-g001-g002-g009` |
| Test count | 44 method (v1=12 + v2=32) |
| SEC-26 method count | 6 |
| `file_url` assertion statements | **6** (5 chính + 1 conditional) |
| `content_hash` assertion statements | **0** |
| Runtime | **NOT RUN** |
| Risk | **PARTIAL** |
| Files modified bởi T1B | 4 files (API guarded + hooks + 2 test) |
| Files modified bởi báo cáo này | 0 (chỉ ghi báo cáo) |
| Push | KHÔNG |
| PR | KHÔNG |
