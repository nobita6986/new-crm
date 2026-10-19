# T1B Runtime Evidence — SEC-26 Final v4

**Commit SHA (T1B code):** `030569cecddfd8a16d0ee1ffc1255b5e18967845` (`030569c`)
**Commit SHA (báo cáo này):** `0080c17` (báo cáo static review)
**HEAD tại thời điểm chạy:** `0080c178f6b3471614b07c82d75295e9194367a0`
**Ngày:** 2026-10-19 13:45 (UTC+7)
**Người chạy:** T1B agent
**Mục tiêu:** Chạy bộ 44 test bảo mật T1B (12 v1 + 32 v2) trên test site sạch, cô lập

---

## 1. Environment Check

| Tool | Command | Result | Blocking |
|---|---|---|---|
| Docker | `docker --version` | DOCKER_NOT_FOUND | **YES — BLOCKS ALL** |
| bench | `bench --version` | BENCH_NOT_FOUND | **YES — BLOCKS ALL** |
| frappe | `frappe --version` | FRAPPE_NOT_FOUND | **YES — BLOCKS ALL** |
| MariaDB client | `mariadb --version` | MARIADB_NOT_FOUND | **YES — BLOCKS ALL** |
| Python | `python --version` | PYTHON_NOT_FOUND | **YES — BLOCKS ALL** |
| Git | `git --version` | **AVAILABLE** | — |
| PowerShell | `$PSVersionTable` | **AVAILABLE** (10.0.19045) | — |

```
docker --version         → DOCKER_NOT_FOUND
bench --version          → BENCH_NOT_FOUND
frappe --version         → FRAPPE_NOT_FOUND
mariadb --version        → MARIADB_NOT_FOUND
python --version         → PYTHON_NOT_FOUND (exit code 9009)
git --version            → available (cwd: D:\HRP-app\new-crm)
OS: win32 10.0.19045
Shell: powershell
```

### 1.1 Blocker Assessment

**Primary blocker:** Docker không có trên host Windows. Frappe/bench/MariaDB cũng không có — tất cả phụ thuộc vào Docker container hoặc cài đặt Frappe trực tiếp.

**Secondary blocker:** Không có Python standalone để chạy test trực tiếp. Frappe test suite cần `bench` CLI và Frappe framework.

**Tertiary blocker:** Không có MariaDB client để kiểm tra database state (nếu có DB server).

**Conclusion:** Host Windows này chỉ có Git + PowerShell. Không đủ để chạy Frappe test suite.

---

## 2. Test Inventory tại SHA `030569c`

| File | Version | Method count | SEC coverage |
|---|---|---|---|
| `test_security_remediation.py` (v1) | `030569c` | **12** | SEC-13, SEC-14, SEC-15, SEC-16, SEC-22, SEC-23, SEC-26 (partial), SEC-27, RISK-SEC-02, RISK-SEC-12 |
| `test_security_remediation_v2.py` (v2) | `030569c` | **32** | SEC-26 (6 methods), others (26 methods) |
| **Tổng** | — | **44** | — |

Xác nhận bằng:
```
git show 030569c:apps/crm_cskh/crm_cskh/tests/test_security_remediation.py | grep '^\tdef test_' | wc -l
→ 12

git show 030569c:apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py | grep '^\tdef test_' | wc -l
→ 32

Tổng: 44
```

---

## 3. Runtime Attempt — NOT RUN

### 3.1 SEC-26 group (6 method, 2 file)

| # | Method | File | Expected command | Result | Runtime |
|---|---|---|---|---|---|
| 1 | `test_get_attachments_redacts_file_url_for_non_system_user` | v2 | `bench --site test_cskh.test run-tests --app crm_cskh --module test_security_remediation_v2.TestSEC26FileURLLeak` | NOT RUN | Blocker: no bench |
| 2 | `test_get_attachments_preserves_file_url_for_system_caller` | v2 | same | NOT RUN | Blocker: no bench |
| 3 | `test_get_attachments_does_not_redact_non_private_file` | v2 | same | NOT RUN | Blocker: no bench |
| 4 | `test_get_linked_docs_of_document_shape_probe_only` | v2 | same | NOT RUN | Blocker: no bench |
| 5 | `test_get_attachments_does_not_leak_cross_scope_file_url` | v2 | same | NOT RUN | Blocker: no bench |
| 6 | `test_find_file_by_url_returns_none_for_old_owner_after_reassign` | v2 | same | NOT RUN | Blocker: no bench |

### 3.2 HTTP Transport Evidence — SEC-13/14/16

| SEC | Evidence type | Expected source | Result | Runtime |
|---|---|---|---|---|
| SEC-13 Guest | HTTP response | `curl http://test_cskh.localhost/api/method/crm_cskh.api...` | NOT RUN | Blocker: no HTTP server |
| SEC-14 CSRF | HTTP request/response | Browser DevTools Network tab | NOT RUN | Blocker: no browser + server |
| SEC-16 Field exposure | HTTP response | `curl http://test_cskh.localhost/api/method/crm_cskh.api...` | NOT RUN | Blocker: no HTTP server |

**HTTP transport evidence blocked by:** Docker không có → Frappe site không chạy → không có HTTP endpoint để curl/test.

### 3.3 Other SEC groups (38 method)

| Group | Count | Result | Runtime |
|---|---|---|---|
| SEC-13 Guest | ~3 | NOT RUN | Blocker: no bench |
| SEC-14 CSRF | ~3 | NOT RUN | Blocker: no bench |
| SEC-15 BOD Viewer | ~2 | NOT RUN | Blocker: no bench |
| SEC-16 Field exposure | ~2 | NOT RUN | Blocker: no bench |
| SEC-22 Stale ToDo | ~3 | NOT RUN | Blocker: no bench |
| SEC-23 Stale Comment | ~3 | NOT RUN | Blocker: no bench |
| SEC-26 (v1) | ~6 | NOT RUN | Blocker: no bench |
| SEC-27 Call Log | ~2 | NOT RUN | Blocker: no bench |
| RISK-SEC-02 Cross-team | ~3 | NOT RUN | Blocker: no bench |
| RISK-SEC-12 Bulk delete | ~2 | NOT RUN | Blocker: no bench |
| Others | ~9 | NOT RUN | Blocker: no bench |

---

## 4. Log đầy đủ

```
=== T1B Runtime Evidence Collection ===
Ngay: 2026-10-19 13:45 (UTC+7)
Commit T1B code: 030569cecddfd8a16d0ee1ffc1255b5e18967845
Commit HEAD: 0080c178f6b3471614b07c82d75295e9194367a0
Working dir: D:\HRP-app\new-crm
OS: win32 10.0.19045
Shell: powershell

[ENV CHECK]
docker --version         → DOCKER_NOT_FOUND (CommandNotFoundException)
bench --version          → BENCH_NOT_FOUND (CommandNotFoundException)
frappe --version         → FRAPPE_NOT_FOUND (CommandNotFoundException)
mariadb --version        → MARIADB_NOT_FOUND (CommandNotFoundException)
python --version         → PYTHON_NOT_FOUND (exit 9009)
git --version            → available

[TEST INVENTORY at 030569c]
v1 (test_security_remediation.py): 12 method
v2 (test_security_remediation_v2.py): 32 method
Total: 44 method

[SEC-26 at 030569c — Static verification]
Assertion content_hash: 0
Assertion file_url: 6 (5 chinh + 1 conditional shape probe)
  1. test_get_attachments_redacts_file_url_for_non_system_user: assertNotIn("file_url", ...)
  2. test_get_attachments_preserves_file_url_for_system_caller: assertIn("file_url", ...)
  3. test_get_attachments_preserves_file_url_for_system_caller: assertEqual(file_url, fixture)
  4. test_get_attachments_does_not_redact_non_private_file: assertIn("file_url", ...)
  5. test_get_attachments_does_not_redact_non_private_file: assertEqual(file_url, fixture)
  6. test_get_linked_docs_of_document_shape_probe_only: assertNotIn("file_url", ...) [conditional]

[RESULT]
SEC-26 (6 method): NOT RUN — Blocker: no Docker, no bench, no frappe
SEC-26 (v1, ~6 method): NOT RUN — Blocker: no Docker, no bench, no frappe
SEC-13/14/16 HTTP: NOT RUN — Blocker: no Docker, no HTTP server
All other groups (38 method): NOT RUN — Blocker: no Docker, no bench, no frappe

[TONG KET]
Tong test: 44
Da chay: 0
NOT RUN: 44
PASS: 0
FAIL: 0
ERROR: 0
Runtime: NONE — Docker not available on Windows host
```

---

## 5. Runtime Evidence Baseline (trước T1B — KHÔNG dùng cho SEC-26 claim)

| Log file | Revision | Site | Tổng test | SEC-26 runtime |
|---|---|---|---|---|
| `wp05-security-final.txt` | `b382fe4` | `test_cskh.localhost` | 26 OK / 7.964s | NOT RUN |
| `p0-local-final.txt` | `b382fe4` | `dev + test_cskh` | 27 OK / 9.037s | NOT RUN |
| `p0-isolated-verification.md` | `b382fe4` (PR #1) | GitHub Actions `#38712593755` | 28 OK / 8.201s | NOT RUN |

**Tất cả baseline tại `b382fe4` — trước 6 commit T1B.** Không dùng để claim SEC-26 PASS.

---

## 6. SEC-26 Risk Status

| Thuộc tính | Giá trị |
|---|---|
| SEC-26 per-cell | **PARTIAL** |
| SEC-26 runtime | **NOT RUN** |
| SEC-26 risk | **CHƯA ĐÓNG** |
| G0-07 overall | **CHƯA KÝ** (1 SEC PARTIAL) |

**SEC-26 không đóng risk.** Cần runtime evidence trên test site sạch (Docker + bench + Frappe site).

---

## 7. Blockers chi tiết

### 7.1 Docker — Primary Blocker

Docker không được cài trên Windows host này. Tất cả Frappe development/deployment dựa trên Docker:

- Frappe bench chạy trong Docker container
- MariaDB chạy trong Docker container  
- Test site `test_cskh.localhost` chạy trong Docker

**Không có Docker → không có Frappe site → không chạy được test.**

### 7.2 bench CLI — Secondary Blocker

`bench` là Frappe CLI để chạy test: `bench --site test_cskh.test run-tests --app crm_cskh`. Không có trên host.

### 7.3 MariaDB — Tertiary Blocker

Database server chạy trong Docker. Không có MariaDB client để kiểm tra state nếu có server.

### 7.4 Python — Quaternary Blocker

Frappe test suite cần Python 3 (Frappe framework). Không có standalone Python trên host.

### 7.5 HTTP Server — Blocks HTTP transport evidence

SEC-13/14/16 HTTP evidence cần Frappe site chạy (`test_cskh.localhost`). Không có Docker → không có site → không có HTTP endpoint.

---

## 8. Tuyên bố (theo user direction)

- **Tất cả 44 test: NOT RUN** (blocker: Docker + bench + frappe + MariaDB + Python)
- **HTTP transport evidence: NOT RUN** (blocker: no HTTP server)
- **SEC-26: PARTIAL / runtime NOT RUN**
- **SEC-26 risk: CHƯA ĐÓNG**
- **G0-07: CHƯA KÝ**
- Không tuyên bố PASS
- Không dùng static reasoning thay runtime
- Không sửa T1A/T1C/Owner docs
- Không push, không tạo PR
- **Báo cáo này:** NOT RUN evidence file (commit `0080c17`)

---

## 9. Next Steps (để đóng SEC-26 risk)

Để có runtime evidence cho SEC-26, cần:

1. **Docker** trên máy chạy test (Windows host hoặc WSL2/Linux VM)
2. **Frappe bench** có thể chạy (`bench init` hoặc trong Docker)
3. **Site sạch** (`test_cskh.localhost`) với code tại `030569c` đã applied
4. **Chạy test:**
   ```bash
   bench --site test_cskh.localhost run-tests \
     --app crm_cskh \
     --module crm_cskh.tests.test_security_remediation_v2.TestSEC26FileURLLeak
   ```
5. **Thu thập log** + báo cáo

**Hiện tại:** Không có môi trường. SEC-26 risk chưa đóng.

---

## 10. Files liên quan

| File | Trong HEAD? | Ghi chú |
|---|---|---|
| T1B code `guarded_crm.py` | Có (`030569c`) | Implement redaction |
| T1B code `hooks.py` | Có (`158e0fd`) | Permission hook |
| T1B v1 test | Có (`030569c`) | 12 method |
| T1B v2 test | Có (`030569c`) | 32 method |
| Evidence file này | Có (`0080c17`) | NOT RUN log |
| T1A docs | Có (working tree) | KHÔNG sửa |
| T1C docs | Có (working tree) | KHÔNG sửa |
| Owner docs | Có (working tree) | KHÔNG sửa |
