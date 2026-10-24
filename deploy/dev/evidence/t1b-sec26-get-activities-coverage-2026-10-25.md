# T1B — Điều tra coverage gap SEC-26: `get_activities` + `docinfo.attachments`

**Vòng:** T1B (theo yêu cầu Owner, sau T1C lần 22b §(7))
**Ngày:** 2026-10-25 (UTC+7)
**Loại báo cáo:** Điều tra coverage gap — **không** sửa policy/wrapper/assertion/tally, **không** thêm method thứ 100, **không** merge PR.
**Dữ liệu:** 100% giả (site seed `p0-ci.localhost`), **không** dùng dữ liệu thật.

---

## 0. Tóm tắt

Runtime characterization **đã thu được**: caller **đúng scope** nhận `file_url` của file **private** qua `crm.api.activities.get_activities` tại **4 đường lộ** — `message[4].attachments`, `message[0][attachment_log].data.file_url`, `docinfo.attachments`, và `docinfo.attachment_logs[].content` (HTML). **Không** đường nào qua wrapper redaction của `crm_cskh`. Đây là **bằng chứng quan sát**, **chưa** phải kết luận vulnerability (xem §6).

⚠️ **Phát hiện mới so với T1C lần 22b:** T1C lần 22b nêu **2** đường lộ (`message[4].attachments` + `docinfo.attachments`). Runtime cho thấy thực tế là **4** đường — 2 đường còn lại là **đường `attachment_log`** (URL nằm trong **HTML** của Comment type `Attachment`), đều **không** qua redaction.

---

## 1. Run / artifact mới nhất (đọc trước khi kết luận)

### 1.1 Run cuối — chứa đủ 4 đường

| Hạng mục | Giá trị |
|---|---|
| Run ID | [`#40134550329`](https://github.com/nobita6986/new-crm/actions/runs/40134550329) (run_number **31**, event `pull_request`) |
| PR head SHA | `997e68139cc62234f62ce42d45689538b8a307fd` |
| PR merge SHA | `285435423e8798cc789bc9e94dcf3fa85ee3447a` — `Merge 997e681… into b382fe42…`, parents `b382fe42…` + `997e681…` ⇒ xác minh qua `gh api git/commits` + `compare`: `status=ahead, ahead_by=1, behind_by=0` ⇒ merge **chứa** commit của tôi |
| `commit_sha` ghi trong probe | `285435423e8798cc789bc9e94dcf3fa85ee3447a` (= `GITHUB_SHA` = merge SHA) |
| Artifact id / name | `12575748769` / `p0-g007-evidence-40134550329` (`expired=false`) |
| **Artifact digest** | **`sha256:817ec470aa887061f3e5fba709424d9d2c2e8bd2dfc5d114337300dda22d65eb`** (132 689 B) — **khớp** digest GitHub trả về **và** sha256 tính lại từ zip tải xuống |
| Entry quan trọng | `http/http-transport.json` — sha256 `ded55c1cbb66421d79c5765e0bcaec95c99292e1f150f55a0ae6045bfd75162e` |
| `recorded_at_utc` | `2026-10-24T21:35:50.537371+00:00` |
| `frappe_sha` / `crm_sha` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` / `0d3910b456ef4fb913e77b29db011b3debc4f778` |
| Raw output (đã lưu) | `deploy/dev/evidence/logs/run40134550329/_raw_sec26_activities_4_sites.json` (sha256 `13add90d5d2c667b3034fd17e2d5ea20862d76c34e2e0ed2e13ba3e677618e1a`) |

### 1.2 Run trung gian — 2 đường (đã dùng để phát hiện 2 đường còn lại)

| Hạng mục | Giá trị |
|---|---|
| Run ID | [`#40133210954`](https://github.com/nobita6986/new-crm/actions/runs/40133210954) (run_number **30**) |
| PR head / merge SHA | `088c9d5d91d3632e355726cbd98a88df8be0dfaf` / `dac9cfa4208cd814d0c2743334b00aa0e64f39f8` (`Merge 088c9d5… into b382fe42…`) |
| Artifact id / digest | `12576216328` / `sha256:d3cd9134d795eb4bfb9cf028be1188f1c324c94c464f73a20763c1fc8f59b2b5` (132 589 B) — digest **khớp** |
| Entry `http/http-transport.json` | sha256 `8fa071b2f7ce8c98af6b34fd1678aaed7e30c2a02aad10f96d1abbd4206b9f3d` |
| Raw output (đã lưu) | `deploy/dev/evidence/logs/run40133210954/_raw_sec26_activities_observations.json` (sha256 `abd9a30203010c81703eb2aa410b7ac15d12342faf0eda9d685efd000feba7b0`) |

### 1.3 Run trước đó (đối chiếu)

`#40048752126` (head `30b17ae`, artifact id `12542801713`, digest `sha256:7874795a…`) — đã tải + đối chiếu trước khi sửa.

**Không có con số ghi sẵn nào được tin**: mọi digest ở trên đều **tự tải + tự kiểm**.

### 1.4 Bất biến trạng thái (run cuối **không** đổi gì)

| Chỉ số | `#40048752126` | `#40133210954` | `#40134550329` | Kết luận |
|---|---|---|---|---|
| `expected_total` / `observed_total` | 99 / 99 | 99 / 99 | 99 / 99 | không đổi |
| `gate_passed` | `false` | `false` | `false` | không đổi |
| PASS / FAIL | 91 / 8 | 91 / 8 | 91 / 8 | không đổi |
| `missing_methods` / `skipped_methods` / `parse_errors` | `[]` / `[]` / `[]` | `[]` / `[]` / `[]` | `[]` / `[]` / `[]` | không đổi |
| **FAIL set (8 method)** | 8 method | **giống 100%** | **giống 100%** | `only_in_* = ∅` |
| Probe `probe_result` | `PASS` (14 case) | `PASS` (15 case) | `PASS` (15 case) | không đổi |
| Probe `failed_cases` | `[]` | `[]` | `[]` | không đổi |
| Probe `observations` | không có | 2 | **4** | đo thêm (không ảnh hưởng gate) |
| `module-status.txt` | 3×`exit=0`, 2×`exit=1` | y hệt | y hệt | không đổi |

FAIL set 8/8 (giống nhau ở cả 3 run, `test_bt1c5_matrix_gaps` 1 + `test_docshare_lifecycle` 7):
`test_ra04_ownership_service_does_not_exist_yet`, `test_docshare_created_by_assign_agent_when_assignee_lacks_permission`, `test_docshare_flags_after_assign_agent`, `test_docshare_removed_by_lead_save_when_owner_changes`, `test_docshare_survives_lead_save_when_owner_unchanged`, `test_docshare_survives_remove_assignments`, `test_effective_permission_before_and_after_reassign`, `test_effective_permission_of_shared_child_records`.

⇒ **8 FAIL là canary chờ Owner (`Q1`–`Q4` + `RA-04`), KHÔNG phát sinh từ thay đổi của tôi.** Job `p0` kết thúc `failure` ở step `Enforce CI test and direct-HTTP probe gates` — nguyên nhân là 8 canary FAIL có sẵn, **không** phải probe (step `Run direct HTTP probes for SEC-13/14/16/26` = **success** ở cả 3 run).

---

## 2. SOURCE TĈNH (tách riêng)

> SHA-256 từng file upstream: `deploy/dev/evidence/logs/upstream-verify-2026-10-23/manifest.json`.
> Mọi dòng số dưới đây là **đọc lại từ file đã ghim**, không phải trích dẫn từ báo cáo trước.

### 2.1 Điểm vào

| Mắt xích | Source (đã ghim) | Nội dung |
|---|---|---|
| Endpoint được expose | `crm/api/activities.py` **L13–14** | `@frappe.whitelist()` / `def get_activities(name: str):` ⇒ `allow_guest` mặc định `False` ⇒ **không** cho Guest |
| Dispatch HTTP | `frappe/handler.py` **L46–62** | `handle()` → `execute_cmd(cmd)` (L53) → `frappe.response["message"] = data` (**L62**, **sau** `execute_cmd`) |
| `frappe.call` | `frappe/__init__.py` **L1765–1772** | trả **giá trị hàm** (không phải `frappe.response`) |
| Response JSON | `frappe/utils/response.py` **L154** | `json.dumps(frappe.local.response, …)` ⇒ dump **TOÀN BỘ** ⇒ `docinfo` (top-level) **cùng cấp** với `message` |
| Không bị override | `apps/crm_cskh/crm_cskh/hooks.py` **L177–195** | `override_whitelisted_methods` có **17** mục; **KHÔNG** có `crm.api.activities.get_activities` (có `get_linked_calls/_notes/_tasks/_attachments` ở L191–194) |
| Override chỉ đổi **tên** ở dispatch | `frappe/handler.py` **L67** + `frappe/__init__.py` **L2553–2556** | `cmd = frappe.override_whitelisted_method(cmd)` chỉ áp ở tầng dispatch ⇒ lời gọi **Python nội bộ** không qua `execute_cmd` ⇒ **không** bị override |

### 2.2 Bốn đường lộ `file_url` (đều `is_private` **không** được dùng để lọc)

| # | Đường | Source | Cơ chế |
|---|---|---|---|
| **1** | `message[4].attachments[].file_url` | CRM `activities.py` **L313** (gọi) → **L321–339** (định nghĩa) | `frappe.db.get_all("File", …)`; `fields` chọn `"file_url"` ở **L330** và `"is_private"` ở **L332** ⇒ `is_private` **có** mặt nhưng **không** lọc |
| **2** | `message[0][attachment_log].data.file_url` | CRM `activities.py` **L299–308** (vòng lặp `for attachment_log in docinfo.attachment_logs:`) → **L305** `parse_attachment_log(...)` → **L505–526** | L518 `if "private/files" in a_tag["href"]: is_private = True`; **L524** `"file_url": a_tag["href"]` ⇒ **trả URL thật** |
| **3** | `docinfo.attachments[].file_url` | CRM `activities.py` **L184–185** (gọi `get_docinfo`) → Frappe `load.py` **L128** → **L200–214** | `frappe.get_all("File", …)`; `fields` chọn `"file_url"` ở **L206** và `"is_private"` ở **L209** ⇒ **không** lọc |
| **4** | `docinfo.attachment_logs[].content` (HTML) | Frappe `file.py` **L898–906** `create_attachment_record()` → `add_comment_in_reference_doc("Attachment", f"<a href='{file_url}' …>")` → Frappe `load.py` **L150–184** (`add_comments`), **L175–176** đưa vào `docinfo.attachment_logs` | URL nằm trong **chuỗi HTML** của Comment type `Attachment`; `file.py` **L899** thêm icon `fa-lock text-warning` |

**Không có `redact` / `crm_cskh` / `guarded` trong `crm/api/activities.py`** ⇒ cả 4 đường **không** qua wrapper redaction.

### 2.3 `get_all` không kiểm quyền ⇒ đường 3/4 rộng hơn đường 1/2

| Source | Nội dung |
|---|---|
| `frappe/__init__.py` **L2043–2044** | `get_all`: *"List database query via `frappe.model.db_query`. Will **not** check for permissions."* |
| `frappe/desk/form/load.py` **L107–110** | `if not doc: doc = frappe.get_doc(doctype, name)` → `if not doc.has_permission("read"): raise frappe.PermissionError`. Vì `doc=""` là falsy ⇒ nhánh guard **có** chạy ⇒ **vẫn** là guard read-level |
| Frappe `comment.py` **L172–210** | `get_document_comments` lọc theo **comment_type** qua `permitted()` (**L196–204**) ⇒ **KHÔNG** lọc theo `is_private` của File ⇒ đường 4 lộ URL của file private |

⇒ Đường 1/2 lọc theo **read trên CRM Lead/Deal** (guard L24/L56/L181); đường 3/4 đi qua `get_docinfo` với guard read-level tương tự. **Cả 4** đều trả về nguyên `file_url` khi caller đúng scope.

### 2.4 Đường 4 — khác biệt then chốt về **tầng redaction**

| Tầng | Cơ chế | Áp cho đường nào |
|---|---|---|
| Probe (`ci_http_transport_probe.py` L73–82, L104–110) | `sanitize()`: `key.lower() in SENSITIVE_KEYS` ⇒ `file_url` ∈ `SENSITIVE_KEYS` ⇒ **giá trị** → `[REDACTED]` | đường **1, 2, 3** (key là `file_url`) |
| Probe (`redact_text` L99–100) | regex `(/private/files/)[^"'\s,}]+` → `/private/files/[REDACTED]` | áp cho **chuỗi** (chạy sau `sanitize`) |
| **Workflow CI** (`p0.yml` **L256–260**) | regex trên **text**: `(/private/files/)[^\s"'<>\\]+` → `\1[REDACTED_FILE_URL]`; `("file_url"\s*:\s*)"[^"]*"` → `\1"[REDACTED_FILE_URL]"` | đường **4** — vì ở đây URL nằm trong **HTML string** dưới key `content`, probe **không** redact được |

⇒ **Đã xác minh** `[REDACTED_FILE_URL]` là do **workflow** sinh ra, **KHÔNG** phải probe: probe không chứa chuỗi `REDACTED_FILE_URL` (quét toàn repo: chỉ xuất hiện ở `p0.yml` L256/257/260). ⇒ Đường 4 lọt qua **tầng redact của probe**; chỉ được che bởi regex của workflow, tức **artifact trong container** (trước bước sanitize của workflow) có URL thật.

### 2.5 UI gọi endpoint này (xác nhận đây là **đường chính**)

| Mắt xích | Source | Nội dung |
|---|---|---|
| CRM UI gọi | `frontend/src/components/Activities/Activities.vue` **L540–544** | `url: 'crm.api.activities.get_activities'`, `params: { name: props.docname }`, `transform: ([versions, calls, notes, tasks, attachments]) => …` |
| UI render `file_url` | `frontend/src/components/Activities/AttachmentArea.vue` **L16 / L39 / L93** | `:src="attachment.file_url"`, hiển thị `is_private`, `window.open(attachment.file_url, '_blank')` |

---

## 3. RUNTIME EVIDENCE (quan sát trực tiếp)

### 3.1 Case `sec26-activities-in-scope` — thông số

| Thuộc tính | Giá trị quan sát |
|---|---|
| Method / URL | `GET http://127.0.0.1:8000/api/method/crm.api.activities.get_activities?name=CRM-LEAD-00001` |
| HTTP status | **200** (`exc_type=None`, `transport_error=None`) |
| Caller | `staff.a@dev.local` — role `Sales User` (**KHÔNG** phải System Manager) |
| Scope của caller | **đúng scope** — `lead_owner` của `CRM-LEAD-00001` (`seed_demo.py` L44 `("A1", "staff.a@dev.local", …)`, `lead_owner` gán ở L155) ⇒ guard `activities.py` L24/L56/L181 **cho qua** ⇒ **kịch bản DƯƠNG** |
| File đo | `DEMO-CRM-LEAD-00001.txt` — **file giả** do `seed_demo.py::_ensure_attachment` (L215–227) tạo với `is_private=1` |
| Top-level keys | `docinfo` (dict, 23 keys) + `message` (list, len=5) |

### 3.2 Bốn quan sát runtime (run `#40134550329`)

| # | `leak_site` | `observation` | `file_url_present` |
|---|---|---|---|
| 1 | `message[4].attachments[].file_url` | `file_url_present` | **true** |
| 2 | `message[0][attachment_log].data.file_url` | `file_url_present` | **true** |
| 3 | `docinfo.attachments[].file_url` | `file_url_present` | **true** |
| 4 | `docinfo.attachment_logs[].content (HTML <a href>)` | `file_url_present` | **true** |

Cả 4 đều: `caller=staff.a@dev.local`, `caller_scope=in-scope (lead_owner of CRM-LEAD-00001)`, `file_is_private=true`, `source=synthetic seeded private attachment (p0-ci.localhost)`.

### 3.3 Raw payload

```
[1] message[4].attachments[].file_url       : [REDACTED]        | is_private: 1
    name=2b47f780b4  file_name=DEMO-CRM-LEAD-00001.txt  file_type=TXT  file_size=72

[2] message[0][attachment_log].data         : {'type': 'added', 'file_name': 'DEMO-CRM-LEAD-00001.txt',
                                              'file_url': '[REDACTED]', 'is_private': True}

[3] docinfo.attachments[].file_url          : [REDACTED]        | is_private: 1
    name=2b47f780b4  file_name=DEMO-CRM-LEAD-00001.txt  file_type=TXT  file_size=72
    attached_to_field=None  folder=Home/Attachments

[4] docinfo.attachment_logs[].content        :
    <a href="/private/files/[REDACTED_FILE_URL]" target="_blank">DEMO-CRM-LEAD-00001.txt</a>
    <i class="fa fa-lock text-warning"></i>
```

**Đối chiếu 2 đường attachments (1 & 3):** cùng `file_name`, cùng `file_url`, cùng `is_private=1`, cùng `name` (File doc `2b47f780b4`).

**Ghi chú `[REDACTED]` / `[REDACTED_FILE_URL]`:** đều là **giá trị** bị che, **key** vẫn còn ⇒ sự kiện đo được là *key `file_url` **có mặt** trong response thật*. Ở đường 4, che bằng **workflow** (không phải probe) — xem §2.4.

### 3.4 Bằng chứng phủ **trước** đó (làm căn cứ đối chiếu)

| Bằng chứng | Phạm vi | Phủ `get_activities`? |
|---|---|---|
| HTTP probe **14/14 `PASS`** (`#40048752126`) | 14 case: `sec13-guest`, `staff-login`, `sec13-user`, `csrf-page`, `sec14-get`, `sec14-post-nocsrf`, `sec14-post-badtok`, `sec14-post-ok`, `administrator-login`, `sec13-admin`, `sec16-permlevel-get-data`, `sec16-permlevel-sentinel-scan`, `sec26-staff-private`, `sec26-admin-private` | ❌ **KHÔNG** — `sec26-*` gọi `crm_cskh.api.guarded_crm.get_attachments` (endpoint **đã override**) |
| `TestSEC26FileURLLeak` **V16–V21** (6/6 `PASS`) | `guarded_crm.get_attachments` / `get_linked_docs_of_document` | ❌ **KHÔNG** — cùng endpoint **đã override** |
| Quét artifact **12 run** (`#39885152649` → `#40048752126`), regex `get_activities` (không phân biệt hoa thường) | toàn bộ file artifact | ❌ **0 hit** |
| Quét artifact **12 run**, regex `docinfo` | toàn bộ file artifact | ❌ **0 hit** |

⇒ Trước thay đổi này, **không tầng nào** (unit, HTTP transport, artifact) quan sát `get_activities` hay `docinfo.attachments`. Run `#40133210954` là **tầng HTTP transport đầu tiên** đo được; run `#40134550329` mở rộng lên **4 đường**.

---

## 4. THAY ĐỔI TỐI THIỂU

**3 file, +457 / −3 dòng.** Không đụng policy, wrapper, assertion hiện hữu, SEC tally, hay method nào trong inventory 99.

### 4.1 `deploy/dev/seed/ci_http_transport_probe.py` (+~200)

| # | Thay đổi | Mục đích |
|---|---|---|
| 1 | `ACTIVITIES_ENDPOINT` (module scope) | Kiểm chứng tĩnh được endpoint |
| 2 | `observations` list + `"observations"` trong `write_evidence()` | Kênh bằng chứng **riêng**, **KHÔNG** ảnh hưởng gate (`probe_result` vẫn chỉ dựa vào `failed_cases`) |
| 3 | Case `sec26-activities-in-scope` — `staff` (non-system, **đúng scope**) | Đo **kịch bản dương** qua HTTP transport thật |
| 4 | Tách **riêng** 4 đường (đường 2 & 3 & 4 thêm ở commit `997e681`) | Đo từng đường độc lập, không gộp |
| 5 | 4 quan sát: `…-message-attachments`, `…-message-attachment-log`, `…-docinfo-attachments`, `…-docinfo-attachment-logs` | Bằng chứng quan sát **tách riêng** từng đường + trường `leak_site` |
| 6 | **KHÔNG** assert cứng `file_url` có/không | **Characterization**: kết luận còn chờ Owner ⇒ probe **chỉ đo** |

**Thay đổi duy nhất ngoài probe:** `.github/workflows/p0.yml` — đổi **tên** step `… SEC-13/14/16` → `… /26` (step này vốn đã chạy case SEC-26). **KHÔNG** đổi logic gate, điều kiện, hay artifact path.

### 4.2 `deploy/dev/seed/test_ci_http_probe_static.py` (+~250)

Thêm class `TestProbeGetActivitiesCoverage` — **10 test** ghim coverage:

| Test | Chặn regression |
|---|---|
| `test_case_exists_and_is_labelled_sec26` | thiếu case / sai nhãn |
| `test_case_calls_get_activities_endpoint` | trỏ **sai endpoint** (đặc biệt: endpoint **đã override**) |
| `test_does_not_call_overridden_endpoint_for_activities` | gọi `guarded_crm.get_attachments` |
| `test_measures_both_paths_separately` | bỏ đo 1 trong 2 đường chính |
| `test_does_not_hardcode_file_url_expectation` | biến characterization thành **kết luận cứng** |
| `test_records_observations_for_each_path` | bỏ ghi quan sát |
| `test_observations_do_not_affect_gate` | quan sát làm đổi PASS/FAIL của gate |
| `test_uses_non_system_caller` | đo nhầm hành vi Administrator |
| `test_uses_seeded_synthetic_document` | dùng tài liệu không phải seed giả |
| `test_measures_attachment_log_paths` | bỏ đo đường **2** / **4** (kèm kiểm **AST**: `attachment_log_activity` phải = `next(...)`; `docinfo_attachment_logs` phải = `.get("attachment_logs")`) |

### 4.3 Kiểm chứng cục bộ

| Kiểm chứng | Kết quả |
|---|---|
| Test tĩnh | **31/31 `PASS`** (21 cũ + 10 mới) |
| **Mutation check** (8 mutation) | **8/8 `CAUGHT`** — endpoint sai, bỏ `docinfo`, assert cứng, bỏ `observations`, đổi caller admin, bỏ đường 2, bỏ đường 4, không nhận diện URL trong HTML. *(Lần đầu 2/8 và 3/8 `MISSED` ⇒ đã siết bằng kiểm tra **AST**, mới đạt 8/8.)* |
| **Simulation shape** (13 tình huống, dữ liệu 100% giả) | **13/13 `PASS`** — 4 đường độc lập, 5-tuple đúng/sai, `docinfo` không phải dict, thiếu key, `file_url` là `None`/số, `content` không phải str, response rỗng |
| `git diff --check` trên 3 file | `exit 0` |
| Probe còn **fail-closed** | ✅ `return 1 if failures else 0` giữ nguyên |

**KHÔNG đổi:** policy · wrapper (`guarded_crm.py`) · assertion hiện hữu · SEC tally · `g007_test_inventory.json` (vẫn `expected_total=99`, **không** thêm method thứ 100) · `test_permissions.py` / `test_security_remediation*.py` · `q1`-`q4`/`ra-04`.

---

## 5. Giới hạn bắt buộc của bằng chứng

1. **Tầng đo:** direct HTTP tới `bench serve` (`coverage_scope` trong artifact ghi rõ: *"reverse-proxy and CORS behavior are not covered"*). Chưa phủ reverse-proxy/CORS.
2. **Chỉ caller đúng scope.** Không suy ra hành vi của caller ngoài scope từ case này.
3. **Giá trị `file_url` không có trong artifact** (bị 2 tầng che) ⇒ chứng minh được *key có mặt*, **không** chứng minh được *URL dùng để tải được*.
4. **Đường 4 lọt tầng redact của probe** ⇒ URL thật **đã** nằm trong artifact trong container trước bước sanitize của workflow. Bằng chứng gián tiếp: `file_url` được sinh từ `a_tag["href"]` (`activities.py` L524) và `file_url` của Frappe (`file.py` L900), còn `set_is_private` (`file.py` L916–918) cho `is_private=1` ⇔ `file_url` bắt đầu bằng `/private` ⇒ URL là `/private/files/…`.
5. **Một tài liệu seed, một file, một caller.** Không khái quát hoá cho mọi doctype / mọi file.
6. **Không** kết luận IDOR: cả 4 đường đều nằm sau guard read-level (CRM L24/L181, Frappe `load.py` L109–110).

---

## 6. GIẢ THUYẾT CHƯA PHÂN BIỆT ĐƯỢC

> Không kết luận vulnerability khi chưa có bằng chứng; **cũng không** đóng RFC chỉ vì có permission guard.

### H-RFC-1 — caller đúng scope nhận `file_url` của file private qua `get_activities`

| | |
|---|---|
| Trước lần này | CHƯA đo ở tầng nào |
| Sau lần này | **ĐÃ ĐO** (runtime, §3.2–§3.3): **4/4 đường** `file_url_present`, `is_private=1` |
| **Còn lại chưa phân biệt** | (a) URL **có ký số / hạn dùng** hay tĩnh? (b) URL đó **tải được** với chính caller đó? (c) mức độ lộ có **tương đương** endpoint **đã override** (đã redaction) hay **nặng hơn** (vì có **4** đường, 1 đường lọt tầng redact của probe)? |

⇒ **H-RFC-1 được thu hẹp mạnh nhưng CHƯA đóng**: đã chứng minh *mặt cảnh* (URL xuất hiện ở 4 nơi), **chưa** chứng minh *tác động* (đọc được nội dung).

### H-RFC-2 — lộ `file_url` ≠ đọc được nội dung

**Source tĩnh (bổ sung ngoài 10 file §5.0 của báo cáo trước):**

| Mắt xích | Source | Nội dung |
|---|---|---|
| Endpoint tải | `frappe/handler.py` **L287–302** | `download_file(file_url)` → `find_file_by_url(file_url)` → `frappe.local.response.filecontent = file.get_content()` |
| `find_file_by_url` | `frappe/core/doctype/file/utils.py` **L453–466** | `frappe.get_all("File", filters={"file_url": path})` → với **mỗi** file: `if file.is_downloadable(): return file` ⇒ không có file tải được ⇒ trả `None` |
| `is_downloadable` | `frappe/core/doctype/file/file.py` **L891–892** | `return has_permission(self, "read")` |
| `File.has_permission` | `frappe/core/doctype/file/file.py` **L992–1031** | Administrator ⇒ `True` (L995–996); `not doc.is_private` ⇒ `True` (L1000–1001); `doc.owner == user` ⇒ `True` (L1003–1004); **L1014–1029**: nếu file gắn vào doc cha ⇒ `return ref_doc.has_permission("read", user=user)` |
| Hệ quả | — | ⇒ **URL lộ ≠ nội dung lộ**: `download_file` vẫn kiểm quyền read trên File **và** doc cha. Với caller đúng scope ⇒ download **có thể** thành công ⇒ **nguy cơ thực tế** cao hơn "chỉ lộ URL" |

⚠️ **Chưa đo runtime** ⇒ **H-RFC-2 chưa phân biệt được**. Cần probe tải file để biết URL có **ký số** hay tĩnh, và có bị từ chối không.

### H-RFC-3 — `docinfo` là đường **riêng**, không sửa được bằng cơ chế override

| | |
|---|---|
| Source | `frappe/desk/form/load.py` **L128** gọi hàm Frappe **riêng**; **KHÔNG** trong `override_whitelisted_methods` (`hooks.py` L177–195) ⇒ cơ chế override của `crm_cskh` **không** chạm được |
| Runtime | **ĐÃ ĐO** (§3.2): đường **3** (`docinfo.attachments`) **và** đường **4** (`docinfo.attachment_logs`) đều `file_url_present` |
| **Còn lại chưa phân biệt** | `docinfo` là payload dùng chung của Frappe desk cho **mọi** doctype ⇒ có thể xuất hiện ở **nhiều** endpoint khác (vd `/api/resource/<doctype>/<name>`) ⇒ **phạm vi lộ rộng hơn** `get_activities` ⇒ **chưa đo** các endpoint khác |

### H-RFC-4 — kịch bản **ngoài scope** (cross-scope IDOR)

| | |
|---|---|
| Runtime đã có | `test_permissions.py` L280–283 (`test_child_lists_and_activity_reads_stay_with_parent_scope`) — caller ngoài scope gọi `get_activities` ⇒ `PermissionError`, `PASS` 12/12 run, log `Message: Not permitted` (kiểm lại ở run mới: `test_permissions.log` L65) — **tầng Python**, không qua HTTP dispatch |
| **Còn lại chưa phân biệt** | Kịch bản ngoài scope **qua HTTP** (dispatch `execute_cmd`): `execute_cmd` chỉ đổi **tên** method ⇒ khả năng cao guard L24/L181 vẫn chặn, nhưng **chưa đo** ⇒ **không** coi là đã đóng |

### H-RFC-5 — `content_hash`

| | |
|---|---|
| Source | CRM `activities.py` L326–336 và Frappe `load.py` L203–212 **đều KHÔNG** chọn `content_hash` ⇒ redaction `content_hash` là **no-op** với 2 payload này |
| Runtime | **ĐÃ ĐO**: `content_hash` **không xuất hiện** trong `http-transport.json` (quét toàn file: `False`) ⇒ **không** phải payload leak ở 4 đường này |

---

## 7. Trạng thái (không đổi)

| Hạng mục | Trạng thái | Lý do |
|---|---|---|
| `SEC-26` | **`PARTIAL`** (giữ nguyên) | Đã có runtime evidence tầng HTTP cho **4 đường**, nhưng **chưa** phân biệt được H-RFC-2 ⇒ **không** đủ điều kiện đóng |
| `G0-07` | **`PARTIAL` / CHƯA KÝ** (giữ nguyên) | Chưa có ký |
| `P0` | **`NO-GO`** (giữ nguyên) | — |
| RFC | **`OPEN`** (giữ nguyên) | — |
| SEC tally | `25 PASS + 3 PARTIAL (SEC-13/16/26) = 28/28` (**giữ nguyên**) | Không có đối chiếu đủ căn cứ để đổi |
| Inventory | `expected_total=99` (**giữ nguyên**) | **KHÔNG** thêm method thứ 100 (cần Owner duyệt) |
| `Q1`–`Q4` + `RA-04` | `PENDING_T0_OWNER_DECISION` (giữ nguyên) | Vẫn chờ Owner |
| `OWNER_DECISIONS_CONFIRMED` | `False` (giữ nguyên) | — |
| `B-T1C-3` | giữ **`MỞ`** — lý do **thu hẹp thêm** | (i) redaction chỉ được chứng minh trên endpoint **đã override**; (ii) RFC `get_activities` — **đã có** runtime evidence kịch bản dương tầng HTTP cho **4 đường**, **còn** H-RFC-2 chưa đo; (iii) `docinfo` — **đã có** runtime evidence (2 đường), **còn** phạm vi endpoint khác chưa đo; (iv) `content_hash` chưa có assert |
| `B-T1C-2` | giữ **`MỞ`** | Probe **15/15** `PASS` nhưng `coverage_scope` **không đổi** (chỉ direct HTTP tới bench serve) ⇒ **không** đủ điều kiện đóng `SEC-13` |
| `B-T1C-4` | giữ **`ĐÓNG`** | 99/99 per-method, 0 `SKIP`, 0 `NOT_RUN`, 0 `parse_error` ở run mới |
| `B-T1C-6` | giữ **`MỞ`, `8`** | 8/99 method không xanh, FAIL set **giống 100%** ⇒ tất định lần thứ năm |
| PR #2 | **OPEN**, `mergeStateStatus=UNSTABLE` | **KHÔNG merge** |

---

## 8. Việc còn lại (đề xuất — **chưa** thực hiện, cần Owner duyệt)

1. **Đo H-RFC-2 ở tầng HTTP**: thêm case tải `download_file` bằng URL lấy từ `get_activities` với chính caller in-scope ⇒ biết URL có ký số không, có tải được không. *Bước quyết định để đóng hay giữ RFC.* Cần Owner duyệt vì có thể chạm assertion SEC-26 hiện hữu.
2. **Đo H-RFC-3 phạm vi rộng**: kiểm `docinfo` ở endpoint khác (`/api/resource/…`) ⇒ xác định `docinfo.attachments` là lỗ hổng riêng hay hệ quả của `get_activities`.
3. **Đo H-RFC-4 qua HTTP**: kịch bản ngoài scope qua dispatch `execute_cmd`.
4. **Cân nhắc đường 4** (`docinfo.attachment_logs[].content`): lọt tầng redact của probe ⇒ nếu Owner muốn, sanitizer của probe nên che URL private trong **chuỗi HTML**, không chỉ key `file_url`.
5. **`Q1`–`Q4` + `RA-04`**: vẫn chờ Owner — 8 canary FAIL **không** đóng được bằng sửa code.

---

## 9. Tệp bằng chứng

| Đường dẫn | Nội dung |
|---|---|
| `deploy/dev/evidence/logs/run40134550329/p0-g007-evidence-40134550329/` | artifact CI giải nén (16 entry) |
| `deploy/dev/evidence/logs/run40134550329/_raw_sec26_activities_4_sites.json` | **raw output 4 đường** + observations + meta (sha256 `13add90d…`) |
| `deploy/dev/evidence/logs/run40133210954/_raw_sec26_activities_observations.json` | raw output 2 đường (run trung gian) (sha256 `abd9a302…`) |
| `deploy/dev/evidence/logs/_dl_run40134550329.py`, `_dl_run40133210954.py` | tải artifact + **tự** kiểm digest |
| `deploy/dev/evidence/logs/_read_observations_4path.py`, `_read_observations.py` | đọc observations + payload raw |
| `deploy/dev/evidence/logs/_extract_raw_paths.py`, `_enumerate_leak_sites.py` | tách 4 đường + so sánh |
| `deploy/dev/evidence/logs/_verify_and_persist_4path.py`, `_verify_merge_sha.py` | xác minh merge SHA chứa commit của T1B; lưu raw evidence |
| `deploy/dev/evidence/logs/_compare_runs.py` | đối chiếu FAIL set / gate / probe giữa các run |
| `deploy/dev/evidence/logs/_mutation_check.py` | mutation check 8/8 |
| `deploy/dev/evidence/logs/_sim_probe_4paths.py`, `_sim_probe_parsing.py` | simulation shape (4 đường + 2 đường) |
| `deploy/dev/evidence/logs/_hunt_docinfo.py` | quét 12 artifact: `docinfo`/`get_activities` **0 hit** (trước thay đổi) |
| `deploy/dev/evidence/logs/_check_download_path.py`, `_check_file_perm.py`, `_check_find_file.py`, `_check_attachment_logs.py` | source tĩnh H-RFC-2 + đường 4 |
| `deploy/dev/evidence/logs/_find_redacted_origin3.py` | xác minh `[REDACTED_FILE_URL]` do **workflow** (`p0.yml` L256–260), **không** phải probe |
| `deploy/dev/evidence/logs/_check_override.py` | xác minh `get_activities` **không** bị override |
| `deploy/dev/evidence/logs/_check_activities_shape.py`, `_check_two_get_attachments.py`, `_check_parse_attachment_log.py` | xác minh 2 `get_attachments` độc lập + shape 5-tuple + `parse_attachment_log` |

> `deploy/dev/evidence/logs/` là thư mục **không track** trong git (không run dir nào được commit) — artifact đã xác minh digest, tải lại được từ run ID.
