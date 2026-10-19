# EVIDENCE — T1A: G0-02 (Vue hot reload qua Vite + CSRF) — runtime blocker

> **Ngày xác minh:** 2026-10-19 (UTC+7)
> **Môi trường xác minh:** phiên Codex trên Windows. **KHÔNG có Docker / Python / Yarn** —
> xác nhận qua `where docker` / `Get-Command python` / `Get-Command yarn` trả về
> "command not found" (tương tự `environment-probe.md` §2).
> **Phạm vi:** chứng minh Vue hot reload qua Vite + CSRF retention.
> **Kết quả runtime mới:** **KHÔNG thể chạy** tại máy này. Tài liệu này ghi (a) blocker
> và kết quả dò, (b) đường chạy runtime được đề xuất, (c) checklist bằng chứng cần thu
> trên máy có Docker/WSL2, (d) phân tích tĩnh còn có thể làm từ metadata.
> **Không sửa upstream CRM/Frappe.**

---

## 1. Dò môi trường (lần này)

| Công cụ | Trạng thái | Bằng chứng |
|---|---|---|
| `docker` | **NOT FOUND** | `Get-Command docker` → CommandNotFoundException |
| `python` | **NOT FOUND** | `Get-Command python` → "Python was not found; run without arguments to install from the Microsoft Store..." |
| `python3` | **NOT FOUND** | cùng lỗi trên |
| `yarn` | **NOT FOUND** | `Get-Command yarn` → CommandNotFoundException |
| `node` | OK (v22.23.3) | `node --version` |
| `git` | OK (2.55.0.windows.5) | `git --version` |
| `WebFetch` (cursor MCP) | OK | dùng được cho `raw.githubusercontent.com` |

Lệnh dò:

```powershell
cd d:\HRP-app\new-crm
where docker        # → CommandNotFoundException
where python        # → CommandNotFoundException
where yarn          # → CommandNotFoundException
node --version      # → v22.23.3
git --version       # → git version 2.55.0.windows.5
```

**Hệ quả:** phiên Codex hiện tại **không thể** chạy:

- `docker compose up -d db redis-cache redis-queue bench` (thiếu Docker)
- `bench start` (thiếu Python + bench)
- `yarn dev` / `yarn install` trong `apps/crm/frontend` (thiếu Yarn, node có nhưng
  CRM frontend yêu cầu Yarn 1.x do `package.json` cố định `postinstall: yarn install --check-files`)
- `curl` tới `127.0.0.1:8080` (thiếu cả Vite dev server)
- `curl` POST tới `/api/method/login` + CSRF (thiếu bench/site)

Do đó, **toàn bộ** G0-02 evidence runtime mới (Vue reload qua Vite + POST/CSRF từ
Vite origin) **không thể thu thập** ở phiên này.

---

## 2. Bằng chứng tĩnh vẫn có thể làm (từ metadata upstream + repo)

### 2.1. Vite/Node/Yarn toolchain

Đọc `crm/frontend/package.json` tại tag đã khóa (qua `raw.githubusercontent.com`):

- `engines.node: "^20.19.0 || >=22.12.0"` — host DEV có Node 22.22.1 / 22.23.3 (đạt).
- `packageManager: "yarn@1.22.22"` (P0 §9.2 đã ghi nhận yarn 1.x classic là bắt buộc).
- Script `dev: "vite"` chạy Vite dev server; host `0.0.0.0` để browser từ host truy cập.
- README CRM lưu ý: vì CRM dùng session + CSRF của Frappe, khi Vite proxy
  sang backend, có thể cần `ignore_csrf=1` — dự án **không** bật.

### 2.2. CSRF/session contract (đã có trong code của `crm_cskh`)

- Page `/cskh` (xem [`apps/crm_cskh/crm_cskh/www/cskh.py`](../../apps/crm_cskh/crm_cskh/www/cskh.py)):
  server render `csrf_token` qua `frappe.sessions.get_csrf_token()`, nhúng vào
  HTML, JS đọc và gắn vào header `X-Frappe-CSRF-Token` cho mọi POST.
- Page `/cskh` (xem [`apps/crm_cskh/crm_cskh/www/cskh.html`](../../apps/crm_cskh/crm_cskh/www/cskh.html)):
  `fetch("/api/method/crm_cskh.api.health.ping", { method: "POST", credentials: "same-origin", headers: { "X-Frappe-CSRF-Token": "{{ csrf_token }}" } })`.
- API `crm_cskh.api.health.ping` (`@frappe.whitelist()`, allow_guest=False) — Frappe
  whitelist gate chặn Guest **trước** khi gọi hàm; CSRF check Frappe mặc định áp dụng
  cho POST từ session-bound client.

**Diễn giải:** cùng một pattern (cookie session + CSRF token + same-origin) sẽ
được dùng khi trang Vite chạy tại `127.0.0.1:8080` và gọi API tại
`127.0.0.1:8000` — nếu cùng host và dev server proxy đúng, browser gửi cookie
cho cả hai origin (nếu cùng site), hoặc Vite phải proxy với `changeOrigin: false`
để giữ cookie gốc Frappe.

### 2.3. Cấu hình Vite — đã có sẵn (đọc từ upstream CRM)

- Vite config mặc định ở `crm/frontend/vite.config.js` (chưa đọc chi tiết trong
  phiên này vì cần checkout local; cần chạy runtime để xác minh `server.proxy`
  có preserve cookie/CSRF hay không).
- Port Vite mặc định 8080; `deploy/dev/docker-compose.yml` đã bind
  `127.0.0.1:8080:8080` từ container bench.

### 2.4. Quan sát cũ đã có

- `wp01-runtime.md` §"Caveat and remaining scope" và `p0-isolated-verification.md` §"Limits":
  Vite page **hiển thị trắng** khi probe bằng browser trong phiên Codex trước;
  chưa chứng minh Vue hot reload. Đây là **evidence cũ** (2026-10-15), không phải
  bằng chứng mới trong T1A, nhưng cho thấy đây là blocker kéo dài.
- `dev-setup.md` §7.6 đã ghi: "Luồng Vue qua Vite vì vậy chưa được nghiệm thu cho tới
  khi xác minh được proxy giữ nguyên CSRF."

---

## 3. Kế hoạch chạy runtime cần thực hiện (KHÔNG thực hiện ở phiên này)

### 3.1. Điều kiện tiên quyết

- Máy có Docker Desktop + WSL2 đang hoạt động (theo `environment-probe.md` đã thấy
  Owner đã cài WSL2 ngày 2026-10-15 và engine Linux đã lên).
- Repo ở HEAD `b382fe4…` (main) hoặc branch `codex/p0-t1a-g001-g002-g009`.
- Image đã pull về digest mới (xem `t1a-g001-metadata-recheck.md` §4) sau khi
  cập nhật `.env.example`.

### 3.2. Lệnh dự kiến (chép lại từ `dev-setup.md` §4 + §6 + §7.6, có chỉnh)

```bash
# 1) Chuẩn bị
cd deploy/dev
cp .env.example .env
# Đã cập nhật digest ở t1a-g001-metadata-recheck.md; .env giữ nguyên INSTALL_CSKH=1

# 2) Dựng stack
docker compose up -d db redis-cache redis-queue bench
docker compose exec -u root bench bash -lc 'mkdir -p /workspace && chown -R frappe:frappe /workspace'
docker compose exec bench bash /src/deploy/dev/bootstrap.sh
docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh

# 3) Khởi động bench + Vite
docker compose exec -d bench bash -lc 'cd /workspace/frappe-bench && bench start'
#   - cổng 8000 (HTTP Frappe) bind 127.0.0.1
#   - cổng 9000 (socketio) bind 127.0.0.1
docker compose exec bench bash -lc 'cd /workspace/frappe-bench/apps/crm/frontend && yarn --version && yarn install --check-files && yarn dev --host 0.0.0.0'
#   - cổng 8080 (Vite) bind 127.0.0.1
```

### 3.3. Bằng chứng cần thu (checklist)

| # | Bằng chứng | Lệnh/đường | Kỳ vọng |
|---|---|---|---|
| V-01 | Vite dev server log | tail log `yarn dev` | in ra `Local: http://localhost:8080/` |
| V-02 | Vite phục vụ SPA CRM | `curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: dev.localhost' http://127.0.0.1:8080/` | 200 (HTML shell) |
| V-03 | SPA tải được JS từ Vite | xem Network tab trong browser | JS request tới 8080 có 200 |
| V-04 | API qua Vite origin giữ cookie session | `curl -c jar -b jar -X POST ...` (xem §3.4) | Set-Cookie + Authorization hoạt động |
| V-05 | POST từ Vite origin cần CSRF | gửi POST thiếu token → 403; có token → 200 | Frappe mặc định: `X-Frappe-CSRF-Token` header hoặc `csrf_token` form-data |
| V-06 | **Vue hot reload**: sửa một nhãn trong `crm/frontend/src/...` rồi quan sát browser | chỉnh một chuỗi dễ thấy (ví dụ trong `Leads.vue`), xem Vite HMR ping, browser cập nhật | "vite hmr update" trong log + UI đổi không cần reload |
| V-07 | POST CSRF vẫn hợp lệ sau khi hot reload | chạy lại V-05 | giữ nguyên pass |
| V-08 | Screenshot/log lưu vào repo | `deploy/dev/evidence/screenshots/t1a-g002-*.png` | ảnh không chứa PII |
| V-09 | Không `ignore_csrf=1` được bật | grep `common_site_config.json` của site | xác nhận giá trị không tồn tại |
| V-10 | Comment + giữ nguyên upstream CRM (crm/, frappe/) | `git -C apps/crm status --porcelain`, `git -C apps/frappe status --porcelain` | rỗng |

### 3.4. Mẫu lệnh kiểm CSRF (chưa chạy ở phiên này)

```bash
# Lấy CSRF token + session cookie
curl -sS -c /tmp/jar.txt -H 'Host: dev.localhost' http://127.0.0.1:8000/api/method/frappe.csrf_token -o /tmp/csrf.json
TOKEN=$(jq -r .message /tmp/csrf.json)  # jq cần có trong container; nếu không, dùng python -c

# Login + giữ cookie
curl -sS -b /tmp/jar.txt -c /tmp/jar.txt -H 'Host: dev.localhost' \
  -H 'X-Frappe-CSRF-Token: '"$TOKEN" \
  -d 'usr=Administrator&pwd=<pwd>' \
  http://127.0.0.1:8000/api/method/login

# POST từ Vite origin (8080) gọi API bench (8000) — yêu cầu cookie + CSRF
curl -sS -b /tmp/jar.txt -c /tmp/jar.txt \
  -H 'Origin: http://127.0.0.1:8080' \
  -H 'X-Frappe-CSRF-Token: '"$TOKEN" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8080/api/method/crm_cskh.api.health.ping
# kỳ vọng: {"message":{"app":"crm_cskh","status":"ok","app_version":"0.0.1","user":"Administrator"}}
```

> **Lưu ý quan trọng về same-origin:** `127.0.0.1:8080` và `127.0.0.1:8000` là
> **khác origin** theo spec (port khác nhau). Cookie Frappe mặc định không có
> `SameSite=None; Secure` nên **có thể không gửi** từ Vite origin. Cần một trong:
> (a) Vite proxy `/api/*` → `127.0.0.1:8000` để SPA gọi cùng origin 8080;
> (b) Set `cookie_samesite="None"` + `secure=1` (chỉ khả thi với HTTPS dev);
> (c) Frappe `ignore_csrf=1` (dự án **không** dùng).
> Kế hoạch runtime cần xác minh (a) — đã có sẵn trong CRM `vite.config.js`? — **cần đọc
> trực tiếp `vite.config.js` tại tag đã khóa** trước khi chạy.

---

## 4. Đề xuất phương án kỹ thuật cho G0-02

Dựa trên phân tích tĩnh ở §2 và kinh nghiệm Frappe, có 3 hướng khả thi:

| Phương án | Cách làm | Ưu | Nhược | Đề xuất cho T1A |
|---|---|---|---|---|
| **V-A: Vite proxy giữ same-origin** | Sửa `crm/frontend/vite.config.js` (hoặc bản fork trong `crm_cskh/`) để `server.proxy['/api'] = 'http://127.0.0.1:8000'`, `changeOrigin: false`, `cookieDomainRewrite: '127.0.0.1'`. SPA vẫn ở 8080 nhưng request `/api/*` đi qua 8080 → 8000 cùng origin cho browser. | Không cần sửa backend/CSRF; giữ nguyên security baseline. | Phải đụng vào `crm/frontend/vite.config.js` upstream (1 file), cần ghi lại diff và chiến lược rebase. | **Đề xuất chính** — cần xác minh upstream `vite.config.js` có sẵn proxy hay không. |
| **V-B: Set SameSite=None; Secure trên cookie Frappe** | Đặt `cookie_samesite=None` + `secure=1` trong `common_site_config.json`; serve Vite qua HTTPS self-signed trong DEV. | SPA gọi thẳng 8000 từ 8080. | HTTPS self-signed + cert; `secure=1` không hoạt động với HTTP loopback. | **Không khuyến nghị** trong P0. |
| **V-C: Bật `ignore_csrf=1`** | Set `ignore_csrf=1` trong site config. | Đơn giản. | **Vi phạm** quy tắc bảo mật (master-plan §1.0 nguyên tắc 7) và `dev-setup.md` §7.6 ghi rõ "dự án **không** bật chế độ bỏ CSRF". | **CẤM**. |

Kết luận: **V-A** là đường đi có căn cứ. Trước khi chạy, cần:
1. Đọc `crm/frontend/vite.config.js` tại tag `v1.87.0` để biết upstream đã có proxy chưa.
2. Nếu có → chỉ cần thêm/bổ sung rule cho `/api` để Vite không phá cookie Frappe.
3. Nếu không → chuẩn bị patch tối thiểu + ADR cho rebase, **không sửa upstream CRM**.

---

## 5. Trạng thái G0-02 — báo cáo

| Mục | Trạng thái |
|---|---|
| Bằng chứng Vue hot reload qua Vite | **CHƯA CÓ BẰNG CHỨNG MỚI** — phiên này không chạy được Docker/Yarn |
| Bằng chứng POST + CSRF từ `/cskh` (cùng site) | Có (wp01-runtime, p0-isolated-verification) — đã thấy `ok: staff.a@dev.local` |
| Bằng chứng POST + CSRF từ Vite origin (8080) | **CHƯA CÓ** |
| Đề xuất kỹ thuật | V-A (Vite proxy same-origin) — §4 |
| Phương án V-A đã verify trên upstream? | **CHƯA** — cần checkout `crm/frontend/vite.config.js` |
| G0-02 có đạt? | **KHÔNG** — vẫn PARTIAL như `p0-exit-report.md` G0-02 hiện tại |

Không tuyên bố G0-02 PASS. T1A ghi nhận blocker runtime và đề xuất phương án V-A
cho lần chạy tiếp theo trên máy có Docker.
