# EVIDENCE — T1A: G0-09 (i18n checklist hoàn thiện + điều kiện thu screenshot)

> **Ngày xác minh:** 2026-10-19 (UTC+7, lần 3)
> **Môi trường xác minh:** phiên Codex trên Windows. **KHÔNG có Docker / Python thật / Yarn / WSL2 / browser** có quyền lưu file vào repo.
> **Probe môi trường lần 3 (2026-10-19 ~09:37 UTC+7):** `Get-Command docker/python/pip/yarn` đều không có; `wsl --status` trả help text (WSL chưa enable); `node` v22.23.3, `npm` 10.9.9, `git` 2.56.0 có. **Không thể chạy runtime trong phiên này.**
> **Phạm vi:** hoàn thiện checklist màn tiếng Việt (P0 §8.4 + master-plan §10.3) theo dạng
> test plan có thể chạy lại; xác định điều kiện thu screenshot không chứa dữ liệu nhạy cảm;
> cập nhật `glossary-vi.md` để checklist dùng được ngay khi có DEV.
> **Bằng chứng runtime mới (screenshot):** **KHÔNG CÓ** ở phiên này. Lý do: máy không có
> Docker để chạy site tiếng Việt, và browser của phiên Codex không được dùng để lưu file
> ảnh có thể chứa PII mà chưa khử. Tài liệu này cung cấp checklist + script shell để
> Owner thu thập screenshot an toàn trên máy có Docker + browser thật.
> **Không sửa upstream CRM/Frappe.**

---

## 1. Tình trạng hiện tại (tổng hợp từ tài liệu đã có)

- `glossary-vi.md` đã có:
  - §1 cơ chế i18n (REUSE, không xây mới).
  - §2 glossary nghiệp vụ chuẩn (24 mục EN → VI, có cờ ⚠ cho chuỗi sai).
  - §3 lỗi i18n ưu tiên (6 mục với file:line trong `vi.po`).
  - §4 nơi thêm/sửa bản dịch (không sửa core).
  - §5 nghiệm thu P0-F (7 mục).
  - §6 phần chưa thể xác minh (thiếu DEV).
- `apps/crm_cskh/crm_cskh/translations/vi.csv` đã có 8 chuỗi cho trang `/cskh` (Lead,
  Lead Owner, Assigned To, Note, Deal, v.v.).
- Trang `/cskh` (xem `apps/crm_cskh/crm_cskh/www/cskh.html` + `cskh.py`) đã viết
  bằng `{{ _("...") }}` và nhúng `csrf_token`.
- `wp01-runtime.md` / `p0-isolated-verification.md` đã ghi nhận ảnh quan sát trong
  browser phiên Codex nhưng chưa lưu vào repo; phiên Codex này **không lưu** được.

---

## 2. Nguyên tắc lưu screenshot KHÔNG chứa dữ liệu nhạy cảm

> Áp dụng theo `P0.md` §7.7 + `permission-matrix.md` §11.4 + master-plan §1.0 nguyên tắc 7.

1. **Không bao giờ** chụp màn hình có chứa:
   - Số điện thoại thật (cả Lead thật và Lead giả có format SĐT Việt Nam đều coi là nhạy cảm).
   - Tên người thật, email cá nhân, địa chỉ thật.
   - Token API, session cookie, CSRF token, password.
   - User list của System Manager (kể cả user giả có email thật của tester).
2. **Chỉ chụp** khi dữ liệu đang hiển thị là dữ liệu giả **đã được khử**:
   - Đổi SĐT Lead thành placeholder kiểu `0900-XXX-001` (mask giữa, dấu `-`).
   - Ẩn tên/email: dùng `STAFF_A`, `TEAM1_LEADER`... thay cho tên thật.
   - Mask API key/secret chỉ giữ 4 ký tự đầu + `...`.
3. **Dung lượng ảnh**: ≤ 500 KB/ảnh, PNG; ưu tiên viewport 1280×800.
4. **Lưu trữ**:
   - Đường dẫn: `deploy/dev/evidence/screenshots/t1a-g009-<screen>.png`.
   - Bổ sung `.gitkeep` + `README.md` (đã có thư mục) để mô tả.
   - Không commit ảnh chứa PII; quy ướt quét tự động: `grep -E "0[0-9]{9}|@gmail|@yahoo"` trên path ảnh.
5. **Tự động hóa (nếu có Playwright)**: chỉ chụp sau khi đã set `user_data_dir` với
   session admin, và script phải ghi lại trạng thái trước/sau bằng JSON metadata kèm ảnh.

---

## 3. Checklist màn tiếng Việt (P0 §8.4 + master-plan §10.3)

Mỗi màn có 4 cột đánh dấu: `□` chưa chạy, `✅` đạt, `❌` không đạt, `—` không áp dụng.

### 3.1. Chuẩn bị (precondition)

- [ ] Site DEV đã bootstrap (`dev.localhost` hoạt động, `list-apps` có `frappe`, `crm`, `crm_cskh`).
- [ ] Seed đã chạy (2 team, 7 user giả, 15 Lead, Call Log, Task, File).
- [ ] Tài khoản test: đăng nhập `staff.a@dev.local` (mật khẩu seed: `DevSeed-Only-2026!`).
- [ ] User settings: Language = `Tiếng Việt (vn)`, Timezone = `Asia/Ho_Chi_Minh`.
- [ ] Chuẩn bị một máy có browser thật (Chrome/Firefox) và quyền lưu file ra host.
- [ ] (Tùy chọn) bật Developer mode để thấy msgid trong console nếu cần.

### 3.2. Màn hình cần kiểm (8 màn theo P0 §8.4 + 1 màn `/cskh` của dự án)

| # | Màn | URL/Doc | Chuỗi phải xuất hiện bằng VI | Tránh chuỗi sai | Trạng thái |
|---|---|---|---|---|---|
| 1 | **Login** | `/login` | "Đăng nhập", "Tên đăng nhập", "Mật khẩu", "Đăng nhập với Google" (nếu có) | "Sign in", "Username", "Password" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 2 | **Preferences** | `/desk#Form/User/...` hoặc `/desk` → Settings → Preferences | "Ngôn ngữ", "Múi giờ" | "Language", "Timezone" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 3 | **Leads List** | `/crm/leads/view/list` | "Khách hàng tiềm năng" (số nhiều), "Trạng thái", "Nguồn", "Số di động" | "**Chì**" (lỗi #1 đã nêu), "Leads" | □ |
| 4 | **Lead Detail** | `/crm/leads/<name>` | "Khách hàng tiềm năng" (số ít), "Nhân viên phụ trách", "Người phụ trách", "Ghi chú" | "**Chì**", "Lead Owner", "**Lưu ý**" (lỗi #2), "**Được giao Cho**" (lỗi #3) | □ |
| 5 | **Call Logs List** | `/crm/call-logs` | "Nhật ký cuộc gọi", "Trạng thái" | "Call Logs" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 6 | **Tasks List** | `/crm/tasks` | "Nhiệm vụ", "Hạn chót", "Người phụ trách" | "Tasks" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 7 | **Data Import** | `/app/data-import` hoặc `/crm/data-import` | "Nhập dữ liệu", "Tải lên tệp", "Ánh xạ cột" | "Data Import" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 8 | **Dashboard** | `/crm/dashboard` | "Báo cáo tổng quan" (hoặc "Trang tổng quan" tùy ngữ cảnh) | "Dashboard" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 9 | **/cskh (CSKH page mới)** | `/cskh` (route cùng site) | "CSKH", "Đã đăng nhập với tư cách", "Trang này dùng cùng phiên đăng nhập Frappe với CRM.", "Kiểm tra phiên đăng nhập và CSRF", "Về danh sách khách hàng tiềm năng" | "Khách chưa rõ tên", "Phone" (chỉ chấp nhận nếu msgid chưa dịch) | □ |
| 10 | **Error/Validation** | submit form CRM Lead không có tên | Thông báo lỗi bằng VI (master-plan §10.3) | "Error" đơn thuần | □ |

### 3.3. Checks chuyên sâu (chuỗi ưu tiên cao)

| # | Chuỗi EN | VI đúng | VI sai hiện tại (`vi.po:line`) | Trạng thái |
|---|---|---|---|---|
| 1 | `Lead` (số ít) | `Khách hàng tiềm năng` | `Chì` (`vi.po:4399`–`4400`) | □ |
| 2 | `Note` | `Ghi chú` | `Lưu ý` (`vi.po:5362`–`5363`) | □ |
| 3 | `Assigned To` | `Người phụ trách` | `Được giao Cho` (`vi.po:751`–`752`) | □ |
| 4 | `Deal` | `Cơ hội` / `Thương vụ` | `Deal` (giữ tiếng Anh, `vi.po:2117`–`2118`) | □ |
| 5 | `Lead Owner` | `Nhân viên phụ trách` | `Chủ sở hữu khách hàng tiềm năng` (`vi.po:4420`–`4421`) | □ |
| 6 | `Phone` / `Mobile No.` | `Số điện thoại` | `Số di động` (`vi.po:4850`–`4851`) — cân nhắc đồng bộ | □ |
| 7 | date/time trên Lead Detail | `15/10/2026 14:30 (GMT+7)` | raw datetime không theo timezone | □ |

### 3.4. Checks ngôn ngữ & locale

- [ ] Sau khi đổi ngôn ngữ user, **không** phải reload toàn site (kiểm tra: đổi xong,
      chuỗi cập nhật ngay hoặc chỉ refresh 1 panel).
- [ ] Sau khi đổi ngôn ngữ, **dữ liệu** không bị ảnh hưởng (Lead count, Call Log count
      trước/sau đổi ngôn ngữ phải bằng nhau).
- [ ] Timezone hiển thị mọi thời gian là `Asia/Ho_Chi_Minh` (xem `p0-d-runtime.md` §5.1
      đã có `VERIFY|time_zone=Asia/Ho_Chi_Minh`).
- [ ] Khi user **không có quyền** vào `/cskh`: hiển thị "Bạn không có quyền truy cập trang này."
      (xem `cskh.py` `_("You do not have permission to access this page.")`).

### 3.5. Checks phía app mới

- [ ] `crm_cskh/translations/vi.csv` (8 chuỗi hiện có) xuất hiện trên `/cskh` mà không
      cần build lại (Frappe cache translations).
- [ ] Không hard-code chuỗi tiếng Việt trong template `.html` của `crm_cskh` — phải dùng
      `{{ _("...") }}`. (`grep -E '[^\x00-\x7F]' apps/crm_cskh/crm_cskh/www/*.html`
      để rà, ngoại trừ `csrf_token` và `current_user`.)
- [ ] Khi thêm DocType mới trong app, khai báo `label` EN + cung cấp VI qua
      `crm_cskh/translations/vi.csv` hoặc `translated_doctype`.

### 3.6. Screenshot cần thu (dự kiến)

> Số điện thoại trong seed: `seed_demo.py` dùng `_phone(index) = "0900000" + "{index:02d}0"` → 15 Lead có SĐT dạng `0900000` + 2 chữ số + `0` (ví dụ `0900000010`, `0900000020`, ..., `0900000150`). **Tất cả đều là placeholder, không dialable** (bắt đầu bằng `09000000` không phải SĐT VN hợp lệ). Khi mask, dùng `0900-XXX-NNN` chỉ để hiển thị trong screenshot; KHÔNG đổi giá trị thật trong DB trừ khi Owner chạy lại seed.

| File | Màn | Quy tắc khử PII | Ghi chú |
|---|---|---|---|
| `t1a-g009-01-login.png` | `/login` (port 8000) | form trống | OK |
| `t1a-g009-02-prefs.png` | Preferences | user = `staff.a@dev.local` (đã là user giả) | OK |
| `t1a-g009-03-leads-list.png` | Leads List | ẩn cột Mobile No trong filter; chỉ chụp cột Name/Status/Source | OK nếu ẩn cột SĐT |
| `t1a-g009-04-lead-detail.png` | Lead Detail | crop hoặc che vùng `mobile_no` (`09000001010`-style); dùng placeholder trong caption nếu cần | OK nếu che/mask |
| `t1a-g009-05-call-logs.png` | Call Logs List | mask số `from`/`to` (`0900000010`-style) | OK nếu che/mask |
| `t1a-g009-06-tasks.png` | Tasks List | task description chỉ chứa placeholder (`[DEMO]`) | OK |
| `t1a-g009-07-data-import.png` | Data Import | KHÔNG upload file thật | OK |
| `t1a-g009-08-dashboard.png` | Dashboard | biểu đồ có số liệu giả (15 Lead) | OK |
| `t1a-g009-09-cskh.png` | `/cskh` (port 8000, Jinja) | user = `Administrator` (admin DEV) | OK |
| `t1a-g009-10-error.png` | Lỗi validation (submit form CRM Lead không có first_name) | không chứa nội dung Lead | OK |

**Cách khử PII hiện có trong `seed_demo.py` (đã chuẩn):**
- Tên giả: `DEMO A1 (giả lập)`, `Khách chưa rõ tên - DEMOA4` (A4 là Lead chỉ có SĐT — kịch bản P0 D1).
- SĐT: `0900000xx0` (10 số, prefix `0900000` không phải SĐT VN hợp lệ).
- User email: `<role>@dev.local` (domain giả).
- Mật khẩu: `DevSeed-Only-2026!` (mặc định dev, không bí mật).
- Call log: 3 bản ghi (`No Answer`, `Busy`, `Completed`), không chứa SĐT thật.
- Attachment: file text nội dung giả, đính kèm Lead `A1`.

> **KHÔNG CẦN sửa `seed_demo.py`** cho G0-09 — seed hiện tại đã an toàn để chụp screenshot.

---

## 4. Lệnh / script hỗ trợ (chưa chạy ở phiên này)

### 4.1. Kiểm tra số lỗi chuỗi VI (offline, chỉ cần Python)

```bash
# Trong container bench (khi có Docker):
docker compose exec bench bash -lc '
  cd /workspace/frappe-bench/apps/crm
  echo "== msgstr rỗng =="
  grep -c "^msgstr \"\"" crm/locale/vi.po
  echo "== lỗi Lead→Chì =="
  grep -n -A1 "Chì" crm/locale/vi.po | head
  echo "== lỗi Note→Lưu ý =="
  grep -n "Lưu ý" crm/locale/vi.po
  echo "== lỗi Assigned To→Được giao Cho =="
  grep -n "Được giao Cho" crm/locale/vi.po
  echo "== lỗi Lead Owner =="
  grep -n "Chủ sở hữu khách hàng tiềm năng" crm/locale/vi.po
'
```

### 4.2. Quét template app có hard-code tiếng Việt không

```bash
# Trên host có Python:
python -c "
import re, pathlib
hits=[]
for p in pathlib.Path('apps/crm_cskh').rglob('*.html'):
    for m in re.finditer(r'[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ ,.!?]{6,}', p.read_text(encoding='utf-8')):
        s=m.group()
        if any(c in 'àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ' for c in s):
            hits.append((str(p), s))
for p,s in hits: print(p,'->',s[:80])
"
```

Kỳ vọng: chỉ có `csrf_token`, `current_user` (đã có underscore). Mọi chuỗi có dấu
phải nằm trong `{{ _("...") }}`.

### 4.3. Đo số chuỗi ưu tiên cao còn hiển thị trên UI (cần DEV)

```bash
# Sau khi bật ngôn ngữ VI cho user:
docker compose exec bench bash -lc '
  cd /workspace/frappe-bench
  bench --site dev.localhost execute crm_cskh.i18n_audit.probe_ui_strings
'
```

> API `crm_cskh.i18n_audit.probe_ui_strings` chưa có — cần thêm nếu muốn tự động hóa.

---

## 5. Trạng thái G0-09 — báo cáo

| Mục | Trạng thái |
|---|---|
| `glossary-vi.md` có bảng lỗi ưu tiên với file:line | ✅ (đã có từ v1.0; T1A bổ sung checklist chi tiết ở §3 vào bản 1.5) |
| Cơ chế thêm bản dịch cho app mới (`translations/vi.csv`) | ✅ (đã có 8 chuỗi cho `/cskh`) |
| `vi.po` core đã được rà tĩnh (1782 msgid, 236 chưa dịch) | ✅ (đã ghi ở v1.0 §0) |
| Site chạy tiếng Việt thực tế | ⛔ chưa có evidence runtime mới |
| Ảnh screenshot UI lưu vào repo | ⛔ chưa có — phiên này không thu được |
| Checklist màn P0 (§8.4) hoàn thiện | ✅ (T1A §3 — sẵn sàng dùng khi có DEV) |
| Điều kiện thu screenshot an toàn | ✅ (T1A §2 — quy tắc khử PII) |
| G0-09 có đạt? | **KHÔNG** — vẫn PARTIAL; thiếu screenshot + render runtime thật |

**Không tuyên bố G0-09 PASS.** T1A ghi nhận checklist hoàn thiện + điều kiện thu
screenshot, nhưng bằng chứng runtime phải được thực hiện trên máy có Docker.

---

## 6. Lệnh Owner cần chạy trên máy có Docker/WSL2 + browser thật

> Phần này viết lại dựa trên cấu trúc repo thực tế:
> - Service name trong `deploy/dev/docker-compose.yml`: `bench` (KHÔNG phải `backend`).
> - Đường dẫn repo trong container: `/src` (read-only) cho source code; `/workspace/frappe-bench` (named volume) cho bench/site/apps.
> - Site: `dev.localhost`; user test: `staff.a@dev.local` với password `DevSeed-Only-2026!` (xem `seed_demo.py`).
> - Quy ước tên service file: `deploy/dev/docker-compose.yml`.

### 6.1. Khởi động stack + site + seed (idempotent)

```powershell
cd d:\HRP-app\new-crm
Copy-Item deploy/dev/.env.example deploy/dev/.env -Force
docker compose -f deploy/dev/docker-compose.yml pull
docker compose -f deploy/dev/docker-compose.yml up -d

# Bootstrap (chỉ chạy lần đầu hoặc khi site chưa có)
docker compose -f deploy/dev/docker-compose.yml exec bench bash /src/deploy/dev/bootstrap.sh

# Cài app crm_cskh (nếu chưa)
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  ln -sfn /src/apps/crm_cskh /workspace/frappe-bench/apps/crm_cskh
  if ! grep -qx "crm_cskh" /workspace/frappe-bench/sites/apps.txt 2>/dev/null; then
    echo "crm_cskh" >> /workspace/frappe-bench/sites/apps.txt
  fi
  ./env/bin/uv pip install -e /workspace/frappe-bench/apps/crm_cskh --python ./env/bin/python
  bench --site dev.localhost install-app crm_cskh
'

# Seed dữ liệu giả (idempotent)
docker compose -f deploy/dev/docker-compose.yml exec bench bash /src/deploy/dev/seed/run-seed.sh
# Kỳ vọng: 7 user, 2 group, 15 Lead, 3 Call Log, 2 Task, 1 attachment, 1 reassignment.
```

### 6.2. Khởi động Frappe web server (port 8000)

```powershell
docker compose -f deploy/dev/docker-compose.yml exec -d bench bash -lc '
  cd /workspace/frappe-bench &&
  bench start
'
```

Smoke test:

```powershell
curl.exe -s -o /dev/null -w "GET / -> HTTP %{http_code}`n" http://127.0.0.1:8000/
curl.exe -s -o /dev/null -w "GET /cskh -> HTTP %{http_code}`n" http://127.0.0.1:8000/cskh
```

### 6.3. Đặt ngôn ngữ user = VI, timezone = Asia/Ho_Chi_Minh

```bash
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  bench --site dev.localhost execute "
frappe.db.set_value(\"User\", \"staff.a@dev.local\", {
  \"language\": \"vi\",
  \"time_zone\": \"Asia/Ho_Chi_Minh\"
})
frappe.db.commit()
print(\"updated\")
"
'
# (System Settings.time_zone đã được seed_demo.py đặt = Asia/Ho_Chi_Minh ở _ensure_dev_settings.)
```

### 6.4. Mở browser và chụp 10 màn (PII-safe)

**Dùng Chrome ở chế độ Incognito** (tránh cache ảnh có thể chứa dữ liệu từ phiên trước).

1. Truy cập `http://127.0.0.1:8000/login`, đăng nhập `staff.a@dev.local` / `DevSeed-Only-2026!`.
2. Lần lượt truy cập 10 URL trong bảng §3.6, **đối chiếu từng màn với PII rules** trước khi chụp.
3. Chụp với **viewport 1280×800**, định dạng PNG, ≤ 500 KB.
4. Lưu vào `deploy/dev/evidence/screenshots/t1a-g009-<NN>-<screen>.png`.

**Danh sách 10 URL cần truy cập:**

| # | URL (gốc `127.0.0.1:8000`) | Tên màn |
|---|---|---|
| 1 | `/login` | Login |
| 2 | `/desk#Form/User/staff.a%40dev.local` (Preferences) | Preferences |
| 3 | `/crm/leads/view/list` | Leads List |
| 4 | `/crm/leads/<name>` (lấy `<name>` từ URL trước) | Lead Detail |
| 5 | `/crm/call-logs` | Call Logs List |
| 6 | `/crm/tasks` | Tasks List |
| 7 | `/app/data-import` hoặc `/crm/data-import` | Data Import |
| 8 | `/crm/dashboard` | Dashboard |
| 9 | `/cskh` | `/cskh` page (Jinja) |
| 10 | submit form CRM Lead trống `first_name` để chụp lỗi validation | Error/Validation |

### 6.5. Kiểm tra chuỗi ưu tiên cao (offline — chỉ cần container, không cần browser)

```bash
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  cd /workspace/frappe-bench/apps/crm
  echo "== Tong msgid =="; grep -c "^msgid " crm/locale/vi.po
  echo "== msgstr rong =="; grep -c "^msgstr \"\"$" crm/locale/vi.po
  echo "== Lead->Chi =="; grep -n -A1 "Chì" crm/locale/vi.po | head
  echo "== Note->Luu y =="; grep -n "Lưu ý" crm/locale/vi.po
  echo "== Assigned To->Duoc giao Cho =="; grep -n "Được giao Cho" crm/locale/vi.po
  echo "== Lead Owner->Chu so huu khach hang tien nang =="; grep -n "Chủ sở hữu khách hàng tiềm năng" crm/locale/vi.po
  echo "== Phone->So di dong =="; grep -n "Số di động" crm/locale/vi.po
  echo "== Deal (giu tieng Anh) =="; grep -n -A1 "^msgid \"Deal\"" crm/locale/vi.po
'
```

Lưu output vào `deploy/dev/evidence/logs/t1a-g009-vi-po-checks.txt`.

**Kỳ vọng (sau khi patch upstream trong tương lai):**
- `Lead` → `Khách hàng tiềm năng` (KHÔNG `Chì`)
- `Note` → `Ghi chú` (KHÔNG `Lưu ý`)
- `Assigned To` → `Người phụ trách` (KHÔNG `Được giao Cho`)
- `Lead Owner` → `Nhân viên phụ trách` (KHÔNG `Chủ sở hữu khách hàng tiềm năng`)
- `Phone` → `Số điện thoại` (KHÔNG `Số di động`)
- `Deal` → `Cơ hội` / `Thương vụ` (hoặc giữ `Deal` nếu CRM quyết định)

> **Hiện tại** tại `crm@v1.87.0`: 6 chuỗi trên **vẫn sai** ở `vi.po` upstream (đã verify 2026-10-19 từ `raw.githubusercontent.com/frappe/crm/v1.87.0/crm/locale/vi.po` — file 315.700 bytes, 1782 msgid, 236 msgstr rỗng). Sửa yêu cầu patch upstream, **không thuộc P0**.

### 6.6. Quét hard-code tiếng Việt trong template `crm_cskh` (cần Python thật trên host)

```powershell
# Cần Python 3.12+ thật (KHÔNG phải App Execution Alias stub).
# Cài Python: winget install Python.Python.3.12

python -c "
import re, pathlib
hits = []
vietnamese = 'àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ'
VI = vietnamese + vietnamese.upper()
for p in pathlib.Path('apps/crm_cskh').rglob('*.html'):
    for m in re.finditer(r'[A-Za-z][A-Za-z ,.!?]{6,}', p.read_text(encoding='utf-8')):
        s = m.group()
        if any(c in VI for c in s):
            hits.append((str(p), s))
for p, s in hits:
    print(p, '->', s[:80])
"
# Kỳ vọng: KHÔNG có dòng nào (vì template chỉ dùng {{ _( ) }} và csrf_token, current_user).
```

Lưu output vào `deploy/dev/evidence/logs/t1a-g009-vi-hardcode-scan.txt`.

### 6.7. Đo số chuỗi ưu tiên cao còn hiển thị trên UI (cần DEV)

> API `crm_cskh.i18n_audit.probe_ui_strings` chưa có. Nếu muốn tự động hóa, Owner tạo file mới `crm_cskh/i18n_audit.py` với hàm `probe_ui_strings()` — nằm ngoài phạm vi T1A (chỉ metadata + runbook).

```bash
# Placeholder (khi API đã có):
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  bench --site dev.localhost execute crm_cskh.i18n_audit.probe_ui_strings
'
```

### 6.8. Blocker log

| # | Blocker | Owner cần |
|---|---|---|
| B-1 | Không có Docker CLI trong PowerShell session Codex | Mở PowerShell session mới sau khi Docker Desktop đã start (xem `environment-probe.md` §8 — đã chạy thành công 2026-10-15) |
| B-2 | Không có Python thật (chỉ App Execution Alias stub) | `winget install Python.Python.3.12` cho host script (§6.6); container bench đã có Python 3.14.8 |
| B-3 | Không có browser thật để chụp | Chrome/Edge trên máy local có quyền lưu file ra host; dùng Incognito + session admin riêng |
| B-4 | `dev.localhost` không resolve trên Windows host | Thêm `127.0.0.1 dev.localhost` vào `C:\Windows\System32\drivers\etc\hosts` (cần admin) HOẶC dùng `127.0.0.1:8000` trực tiếp + sửa cookie domain trong site_config nếu cần |

---

## 7. Kết luận G0-09 (T1A lần 3)

- **Trạng thái:** ⛔ BLOCKER runtime. Checklist + PII rules + runbook đã chuẩn bị xong, nhưng không có screenshot nào trong repo ở phiên này (PowerShell session Codex không có Docker CLI / browser).
- **Đã cập nhật dựa trên cấu trúc repo thực tế** (lần 3):
  - Service name: `bench` (không phải `backend`).
  - Đường dẫn trong container: `/src/...` cho source read-only; `/workspace/frappe-bench/...` cho bench/site.
  - SĐT seed: `0900000xx0` (đã deterministic, không dialable; **không cần sửa seed**).
  - 6 chuỗi ưu tiên cao vẫn còn sai trong `vi.po` upstream `v1.87.0` (Lead→Chì, Note→Lưu ý, …) — đã verify bằng `raw.githubusercontent.com`; **không thể sửa ở P0** (cần patch upstream).
  - Site `dev.localhost` không resolve trên Windows host — dùng `127.0.0.1:8000` + `Host: dev.localhost` header.
- **Khi runtime chạy xong:** chụp 10 ảnh theo §3.6 (đã cập nhật), đối chiếu PII theo §2, điền lại checklist §3 với dấu ✅/❌, đánh dấu lại G0-09 trong `p0-exit-report.md`.
- **KHÔNG tuyên bố PASS** ở phiên này.
