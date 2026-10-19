# EVIDENCE — T1A: G0-02 Vite hot reload + CSRF (BLOCKER runtime + sửa phân tích tĩnh)

> **Ngày xác minh:** 2026-10-19 (UTC+7, lần 3)
> **Trạng thái:** ⛔ **BLOCKER runtime** — phiên Codex này **KHÔNG có** Docker / Python thật / Yarn / WSL2 / browser.
> Bằng chứng dưới đây là **phân tích tĩnh** (đọc source code đã khóa + cấu hình upstream) + **lệnh Owner cần chạy** trên máy có Docker/WSL2.
> **Không sửa upstream CRM/Frappe.** Không bật `ignore_csrf=1`. **KHÔNG đánh dấu PASS** ở phiên này.

---

## 1. Probe môi trường (lần 3, 2026-10-19 ~09:37 UTC+7)

```powershell
PS> Get-Command docker
CommandType     Name
-----------     ----
# (không có output — command not found)

PS> docker --version
docker : The term 'docker' is not recognized as the name of a cmdlet, function, script file, or operable program.

PS> Get-Command python
Application     python.exe        0.0.0.0    C:\Users\Admin\AppData\Local\Microsoft\...
# python --version trả: "Python was not found; run without arguments to install from the Microsoft Store..."
# → chỉ là App Execution Alias stub, không phải Python thật

PS> Get-Command yarn
yarn : The term 'yarn' is not recognized as the name of a cmdlet, function, script file, or operable program.

PS> Get-Command pip
pip : The term 'pip' is not recognized ...

PS> wsl --status
# Trả help text thay vì status — WSL feature chưa enable / không có distro

PS> wsl -l -v
# Trả help text — không có distro WSL

PS> Get-Command node
Application     node.exe   22.23.3.0

PS> Get-Command npm
ExternalScript  npm.ps1    # 10.9.9

PS> Get-Command git
Application     git.exe    # 2.56.0.windows.1
```

| Thành phần | Trạng thái | Ghi chú |
|---|---|---|
| `docker` | ❌ không có | xác nhận 3 lần (T1A lần 1, 2, 3) |
| `docker-compose` | ❌ không có | (legacy v1) cũng không có |
| `python` / `python3` | ❌ App Execution Alias stub trỏ Microsoft Store | không phải Python thật |
| `pip` | ❌ không có | |
| `yarn` | ❌ không có | |
| `wsl --status` | ⚠️ trả help text | WSL feature chưa enable / không có distro |
| `node` v22.23.3 | ✅ có | nhưng Vite phải chạy trong container |
| `npm` 10.9.9 | ✅ có | dùng để cài yarn global nếu muốn; không dùng cho runtime |
| `git` 2.56.0.windows.1 | ✅ có | dùng cho static analysis |
| Browser | ❌ không có quyền lưu file ảnh ra host | |

→ **Kết luận:** KHÔNG thể chạy runtime. Toàn bộ G0-02 runtime evidence **chưa tồn tại**.

---

## 2. Sửa lại phân tích tĩnh (lần 3 — phát hiện lỗi của T1A lần 1/2)

### 2.1. Trang `/cskh` KHÔNG phải SPA Vue — là Jinja template

**Phát hiện mới (T1A lần 3):** kiểm tra thực tế trong working tree:

```text
apps/crm_cskh/crm_cskh/www/
├── cskh.html          ← Jinja template (1067 bytes)
├── cskh.py            ← get_context (558 bytes)
└── __init__.py

apps/crm_cskh/crm_cskh/frontend/   ← KHÔNG TỒN TẠI
```

`apps/crm_cskh/crm_cskh/hooks.py` **không** khai báo `app_include_js` (toàn comment, không có dòng nào uncomment). Nội dung `cskh.html`:

```jinja
{% extends "templates/web.html" %}
{% block page_content %}
<main class="cskh-page">
  <h1>{{ _("CSKH") }}</h1>
  <p>{{ _("Signed in as") }} <strong>{{ current_user }}</strong></p>
  ...
</main>
<script>
  document.getElementById("cskh-session-check").addEventListener("click", async () => {
    const response = await fetch("/api/method/crm_cskh.api.health.ping", {
      method: "POST", credentials: "same-origin",
      headers: { "X-Frappe-CSRF-Token": "{{ csrf_token }}" },
    });
  });
</script>
{% endblock %}
```

→ Trang `/cskh` được phục vụ bởi **Frappe web template engine** (server-side render), **không qua Vite**. Chỉ có 1 fetch POST tới `/api/method/crm_cskh.api.health.ping`.

**Sai trong T1A lần 1/2:** đã viết "Trang `/cskh` qua Vite"; "cần cấu hình Vite proxy"; "cập nhật `crm_cskh/frontend/vite.config.js`". Tất cả dựa trên giả định sai. T1A lần 3 sửa lại:

- `/cskh` **không** cần Vite dev server.
- `/cskh` cần Vite **chỉ** nếu muốn HMR cho phần `<script>` inline (tức là thay vanilla JS bằng module Vue). Hiện tại không có module nào như vậy.
- `/cskh` chạy trực tiếp qua Frappe web server (port 8000). Cùng origin với `/api/method/...` → cookie `sid` và `X-Frappe-CSRF-Token` đi cùng request, **không cần Vite proxy** vì không có cross-origin.
- Vấn đề G0-02 thực sự là với **CRM frontend** (Vue SPA ở `/crm/...`), không phải `/cskh`.

### 2.2. CRM frontend dùng plugin `frappe-ui/vite` với `frappeProxy: true`

Đã đọc `crm/frontend/vite.config.js` tại tag `v1.87.0` (đã khóa) qua `raw.githubusercontent.com`:

```js
// https://raw.githubusercontent.com/frappe/crm/v1.87.0/frontend/vite.config.js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import path from 'path'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(async ({ mode }) => {
  const isDev = mode === 'development'
  const config = { plugins: [vue(), vueJsx(), VitePWA({...})], resolve: {...}, ... }
  const frappeui = await importFrappeUIPlugin(isDev, config)
  config.plugins.unshift(
    frappeui({
      frappeProxy: true,           // ← plugin tự cấu hình proxy
      lucideIcons: true,
      jinjaBootData: true,
      buildConfig: {
        indexHtmlPath: '../crm/www/crm.html',
        emptyOutDir: true,
        sourcemap: true,
      },
    }),
  )
  return config
})
```

**Quan sát quan trọng:**
- Upstream CRM **KHÔNG** cấu hình `server.proxy` thủ công trong `vite.config.js`.
- Thay vào đó dùng plugin `frappe-ui/vite` với option `frappeProxy: true`. Plugin này tự động cấu hình proxy `/api` → `http://127.0.0.1:8000` (mặc định).
- Plugin này tự cấu hình CORS và HMR WebSocket cho phù hợp.
- Plugin cũng tự resolve `@framework/ui` → `../../frappe/ui/src` (linked package).

**Chi tiết plugin `frappeProxy` (đã đọc source `unpkg.com/frappe-ui@1.0.0-beta.29/vite/frappeProxy.js`):**

```js
export function frappeProxy({
  port,
  source = '^/(desk|app|login|api|assets|files|private)',
} = {}) {
  // ...
  let proxy = {}
  proxy[source] = {
    target: `http://127.0.0.1:${webserver_port}`,  // mặc định 8000
    ws: true,                                       // ← Hỗ trợ WebSocket (HMR)
    router: function (req) {
      const site_name = req.headers.host.split(':')[0]
      return `http://${site_name}:${webserver_port}`  // ← route theo host header
    },
  }
  return { name: 'frappeui-proxy-plugin', config: () => ({ server: { port: port, proxy: proxy } }) }
}
```

→ Plugin proxy tất cả path matching `^/(desk|app|login|api|assets|files|private)` sang
Frappe webserver (mặc định 8000), bao gồm cả WebSocket (HMR). Vite port mặc định = 8080
(nếu webserver 8000) hoặc webserver_port + 80 (nếu đổi port).

→ Vậy việc chạy CRM Vue qua Vite trong dev đã được upstream giải quyết sẵn bằng plugin. **Không cần patch upstream `vite.config.js`**.

### 2.3. Lỗi của T1A lần 1/2 cần sửa

| T1A lần 1/2 viết | T1A lần 3 sửa lại |
|---|---|
| "Trang `/cskh` qua Vite" | Sai — `/cskh` là Jinja template, server-side render. Vấn đề Vite (nếu có) chỉ liên quan CRM frontend. |
| "Cần cấu hình Vite proxy trong `crm_cskh/frontend/vite.config.js`" | Sai — `crm_cskh/frontend/` không tồn tại; CRM upstream đã có `frappe-ui` plugin với `frappeProxy: true`. |
| "Cập nhật `host_name = http://127.0.0.1:8080`" | Không cần — nếu chỉ test CRM frontend qua Vite upstream, host_name mặc định `http://127.0.0.1:8000` vẫn dùng được vì proxy tự forward. |
| "Cấu hình V-A: Vite proxy same-origin" | Áp dụng upstream qua plugin `frappe-ui`, không cần tự viết. |
| "POST hợp lệ → 200, thiếu token → 403, sai token → 403" | Phương án test vẫn đúng cho `/cskh` vì `/cskh` gọi `/api/method/crm_cskh.api.health.ping` thực sự. |

---

## 3. Phân tích tĩnh — đã xác minh bằng `git show` trên commit khóa + `raw.githubusercontent.com`

### 3.1. Cấu trúc `/cskh` (cùng-origin với Frappe)

| Mục | Giá trị | Nguồn |
|---|---|---|
| Template | `apps/crm_cskh/crm_cskh/www/cskh.html` | đã đọc ở phiên này |
| Backend | `apps/crm_cskh/crm_cskh/www/cskh.py` (`get_context`) | đã đọc ở phiên này |
| Endpoint | `/cskh` (cùng origin với Frappe port 8000) | hooks.py không khai báo route; đây là route mặc định của `www/cskh.html` |
| API gọi từ JS | `POST /api/method/crm_cskh.api.health.ping` | `cskh.html` line 18 |
| CSRF contract | `csrf_token` được nhúng từ server-side (`frappe.sessions.get_csrf_token()` trong `cskh.py`); client dùng `X-Frappe-CSRF-Token` header | `cskh.py` + `cskh.html` |
| Cookie | Set tại `http://127.0.0.1:8000` với `SameSite=Lax`; client `credentials: "same-origin"` nghĩa là gửi cookie khi cùng origin | `cskh.html` line 17 |
| Endpoint `ping` | `crm_cskh.api.health.ping` trả `{app, status, app_version, user}`; `@frappe.whitelist()` (không `allow_guest`) → Frampe từ chối Guest | `apps/crm_cskh/crm_cskh/api/health.py` đã đọc |

### 3.2. Cấu trúc CRM frontend (Vue SPA qua Vite, upstream đã xử lý proxy)

| Mục | Giá trị | Nguồn |
|---|---|---|
| Vite cổng | `8080` (mặc định qua plugin) | `frappe-ui/vite` plugin; upstream không hardcode |
| Frappe dev server cổng | `8000` | image `frappe/bench:v5.31.0` mặc định |
| Proxy | `/api` → `http://127.0.0.1:8000` (qua plugin `frappe-ui`, không phải tự viết) | `frappeProxy: true` trong `vite.config.js` upstream |
| CORS | Plugin tự cấu hình | `frappe-ui/vite` source |
| HMR WebSocket | Plugin tự cấu hình | `frappe-ui/vite` source |
| Build output | `apps/crm/crm/public/frontend/index.html` → `apps/crm/crm/www/crm.html` (copy) | `package.json` script `copy-html-entry` |

### 3.3. Vấn đề gốc G0-02

- Từ WP-01/PR-#1 đã có: "Vite page hiện trắng nên chưa chứng minh Vue hot reload" (xem `p0-d-runtime.md` §7 và `p0-exit-report.md`).
- Nguyên nhân có thể (cần runtime verify):
  1. Plugin `frappe-ui/vite` chưa tải vì thiếu linked package `@framework/ui` (cần `apps/frappe` checkout local — trong docker image bench đã có sẵn).
  2. Vite không khởi động nếu thiếu `frappe-ui` npm package version mismatch.
  3. Port 8080 chưa được compose publish ra host ngoài.
  4. CSRF/session issue nếu truy cập từ origin khác (ít khả năng vì plugin đã xử lý).

→ Đây là **giả thuyết**; cần runtime mới xác minh được.

---

## 4. Lệnh Owner cần chạy trên máy có Docker/WSL2

> **Tách rõ 2 entry-point** trong compose `deploy/dev/docker-compose.yml`:
> - **Port 8000** (publish ra `127.0.0.1:8000`): Frappe web server chính (do `bench start` chạy trong container `bench`). Phục vụ **Jinja template `/cskh`** (server-side render), `/api/method/...`, `/crm` (production build nếu đã build), `/desk`, `/login`. Tất cả request đi cùng origin `http://127.0.0.1:8000` (hoặc `http://dev.localhost:8000` sau khi thêm hosts).
> - **Port 8080** (publish ra `127.0.0.1:8080`): Vite dev server (chỉ khởi động khi Owner chạy `yarn dev` trong `apps/crm/frontend`). Phục vụ **CRM frontend SPA** (Vue qua Vite) với HMR. Plugin `frappe-ui/vite` (upstream CRM đã khai báo `frappeProxy: true`) tự proxy `/api`, `/files`, `/assets` sang port 8000.
>
> Vì vậy:
> - **Test `/cskh`** → dùng port **8000**, không cần Vite, curl có thể chạy trực tiếp.
> - **Test CRM Vue + HMR + CSRF retention** → cần Vite chạy ở port **8080**; test qua port 8080 (plugin proxy sang 8000).
> - **Cả hai cùng chia sẻ session** qua cookie `sid` Frappe.
>
> **Tên service trong compose:** `bench` (KHÔNG phải `backend` — chỗ này T1A lần 1/2 viết sai).

### 4.1. Khởi động stack (sau khi `up -d`)

```powershell
cd d:\HRP-app\new-crm
Copy-Item deploy/dev/.env.example deploy/dev/.env -Force
docker compose -f deploy/dev/docker-compose.yml pull
docker compose -f deploy/dev/docker-compose.yml up -d
docker compose -f deploy/dev/docker-compose.yml ps
# kỳ vọng: 4 service đều "Up (healthy)": db, redis-cache, redis-queue, bench
```

Nếu chưa có site, chạy bootstrap (xem `t1a-g001-metadata-recheck.md` §6.2):

```powershell
docker compose -f deploy/dev/docker-compose.yml exec bench bash /src/deploy/dev/bootstrap.sh
docker compose -f deploy/dev/docker-compose.yml exec bench bash /src/deploy/dev/seed/run-seed.sh
```

### 4.2. Khởi động Frappe web server (cho `/cskh` Jinja + port 8000)

**Mở terminal PowerShell thứ nhất**, chạy nền:

```powershell
docker compose -f deploy/dev/docker-compose.yml exec -d bench bash -lc '
  cd /workspace/frappe-bench &&
  bench start
'
# Hoặc chạy foreground (Ctrl+C để dừng):
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  cd /workspace/frappe-bench &&
  bench start
'
```

**Smoke test ngay** (terminal PowerShell thứ hai):

```powershell
# (a) Root
curl.exe -s -o /dev/null -w "GET / -> HTTP %{http_code}`n" http://127.0.0.1:8000/

# (b) /cskh (KHÔNG có Host header — dùng 127.0.0.1 thẳng)
curl.exe -s -o /dev/null -w "GET /cskh -> HTTP %{http_code}`n" http://127.0.0.1:8000/cskh

# (c) /crm production (nếu đã build, nếu chưa sẽ 404 — OK)
curl.exe -s -o /dev/null -w "GET /crm -> HTTP %{http_code}`n" http://127.0.0.1:8000/crm

# (d) /desk (cùng host)
curl.exe -s -o /dev/null -w "GET /desk -> HTTP %{http_code}`n" http://127.0.0.1:8000/desk
```

**Kỳ vọng (a-d)**: tất cả trả 200 trừ (c) có thể 404 nếu chưa build CRM production.

### 4.3. Khởi động CRM frontend (Vite, port 8080)

**Mở terminal PowerShell thứ ba**, chạy nền:

```powershell
docker compose -f deploy/dev/docker-compose.yml exec -d bench bash -lc '
  cd /workspace/frappe-bench/apps/crm/frontend &&
  yarn install --check-files &&
  yarn dev --host 0.0.0.0 --port 8080
'
# Hoặc foreground (Ctrl+C để dừng):
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  cd /workspace/frappe-bench/apps/crm/frontend &&
  yarn install --check-files &&
  yarn dev --host 0.0.0.0 --port 8080
'
```

**Lưu log Vite startup** vào file:

```powershell
docker compose -f deploy/dev/docker-compose.yml logs bench | Tee-Object deploy/dev/evidence/logs/t1a-g002-vite-startup.txt
```

**Kỳ vọng log Vite:**

```text
  VITE v8.2.0  ready in NNN ms
  frappe-ui vite plugin: frappeProxy enabled
  ➜  Local:   http://localhost:8080/
  ➜  Network: http://0.0.0.0:8080/
```

### 4.4. Verify Vite page không trắng (CRM frontend ở port 8080)

**Smoke test bằng curl** (PowerShell thứ hai):

```powershell
# (a) Vite root
curl.exe -s -o /dev/null -w "GET / -> HTTP %{http_code}`n" http://127.0.0.1:8080/

# (b) Vite proxy CRM frontend
curl.exe -s -o /dev/null -w "GET /crm -> HTTP %{http_code}`n" -H "Host: dev.localhost" http://127.0.0.1:8080/crm

# (c) Vite proxy API
curl.exe -s -o /dev/null -w "GET /api/method/frappe.sessions.get_csrf_token -> HTTP %{http_code}`n" -H "Host: dev.localhost" http://127.0.0.1:8080/api/method/frappe.sessions.get_csrf_token
```

**Kỳ vọng**: (a) 200 (Vite shell); (b) 200 (SPA shell hoặc HTML rỗng); (c) 200 trả JSON `{"message":"<csrf-token>"}` nếu cookie session đã có.

**Mở Chrome** trên máy Owner, truy cập `http://127.0.0.1:8080/crm`:

- Tab Network: SPA shell HTTP 200; request `/api/method/...` được proxy sang 8000 (status 200, không CORS error).
- Tab Console: KHÔNG có lỗi `WebSocket connection to ws://... failed` hoặc `CORS policy`.
- DOM: `<div id="app">` có markup Vue render (form login CRM).

Nếu thấy trắng:
- Kiểm tra log Vite có dòng "frappe-ui vite plugin" không.
- Kiểm tra Network tab: request `/api/method/frappe.sessions.get_csrf_token` có response 200 qua proxy.
- Plugin tự động include `@framework/ui` từ `apps/frappe/ui/src` — nếu checkout thiếu, plugin fallback npm; vẫn chạy được.

### 4.5. Verify Vite hot reload (HMR)

**Trong PowerShell thứ tư**, sửa 1 component CRM:

```powershell
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  cat > /tmp/probe.vue <<EOF
<template><div>Probe T1A G0-02 HMR — {{ ts }}</div></template>
<script setup>const ts = new Date().toISOString();</script>
EOF
  cp /tmp/probe.vue /workspace/frappe-bench/apps/crm/frontend/src/components/Probe.vue
  echo "Probe.vue created at $(date)"
'
```

Trong Chrome DevTools Network tab, xác nhận có message `WebSocket message: 'update'` cho file `Probe.vue`. DOM phải tự cập nhật timestamp trong < 2 giây.

Lưu screenshot vào `deploy/dev/evidence/screenshots/t1a-g002-01-hmr.png`.

### 4.6. Verify CSRF retention (qua Vite proxy ở port 8080)

```bash
# (1) Lấy CSRF token qua Vite proxy
TOKEN=$(curl -s -c /tmp/cookies.txt http://127.0.0.1:8080/api/method/frappe.sessions.get_csrf_token \
  -H "Host: dev.localhost" -H "X-Requested-With: XMLHttpRequest" \
  | python -c "import json,sys;print(json.load(sys.stdin)['message'])")
echo "CSRF token: $TOKEN" | tee deploy/dev/evidence/logs/t1a-g002-csrf-token.txt

# (2) Login (sử dụng ADMIN_PASSWORD trong .env, mặc định dev-only-change-me)
ADMIN_PWD=$(grep ^ADMIN_PASSWORD deploy/dev/.env | cut -d= -f2)
curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -X POST \
  http://127.0.0.1:8080/api/method/login \
  -H "Host: dev.localhost" -H "X-Frappe-CSRF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"usr\":\"Administrator\",\"pwd\":\"$ADMIN_PWD\"}" \
  | tee deploy/dev/evidence/logs/t1a-g002-login.txt
# kỳ vọng: {"message":"Logged In"}

# (3) POST hợp lệ — frappe.ping (kỳ vọng HTTP 200, body {"message":"pong"})
curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "Host: dev.localhost" -H "X-Frappe-CSRF-Token: $TOKEN" \
  -w "\nHTTP %{http_code}\n" \
  | tee deploy/dev/evidence/logs/t1a-g002-curl-ok.txt

# (4) POST hợp lệ — crm_cskh.api.health.ping
curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/crm_cskh.api.health.ping \
  -H "Host: dev.localhost" -H "X-Frappe-CSRF-Token: $TOKEN" \
  -w "\nHTTP %{http_code}\n" \
  | tee deploy/dev/evidence/logs/t1a-g002-health-ok.txt

# (5) POST thiếu token (kỳ vọng HTTP 403)
curl -s -o /dev/null -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "Host: dev.localhost" \
  -w "HTTP %{http_code}\n" \
  | tee deploy/dev/evidence/logs/t1a-g002-curl-no-token.txt

# (6) POST sai token (kỳ vọng HTTP 403)
curl -s -o /dev/null -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "Host: dev.localhost" -H "X-Frappe-CSRF-Token: WRONG-TOKEN-12345" \
  -w "HTTP %{http_code}\n" \
  | tee deploy/dev/evidence/logs/t1a-g002-curl-bad-token.txt
```

### 4.7. Verify `/cskh` (Jinja ở port 8000 — KHÔNG qua Vite)

`/cskh` là Jinja template server-side, **không cần Vite**. Test thẳng port 8000:

```bash
# (1) Lấy CSRF token trực tiếp port 8000
TOKEN=$(curl -s -c /tmp/cookies-cskh.txt http://127.0.0.1:8000/api/method/frappe.sessions.get_csrf_token \
  -H "X-Requested-With: XMLHttpRequest" \
  | python -c "import json,sys;print(json.load(sys.stdin)['message'])")
echo "CSRF token: $TOKEN" | tee deploy/dev/evidence/logs/t1a-g002-cskh-csrf-token.txt

# (2) Login
ADMIN_PWD=$(grep ^ADMIN_PASSWORD deploy/dev/.env | cut -d= -f2)
curl -s -b /tmp/cookies-cskh.txt -c /tmp/cookies-cskh.txt -X POST \
  http://127.0.0.1:8000/api/method/login \
  -H "X-Frappe-CSRF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d "{\"usr\":\"Administrator\",\"pwd\":\"$ADMIN_PWD\"}" \
  | tee deploy/dev/evidence/logs/t1a-g002-cskh-login.txt
# kỳ vọng: {"message":"Logged In"}

# (3) POST hợp lệ tới crm_cskh.api.health.ping (kỳ vọng 200)
curl -s -b /tmp/cookies-cskh.txt -X POST http://127.0.0.1:8000/api/method/crm_cskh.api.health.ping \
  -H "X-Frappe-CSRF-Token: $TOKEN" -w "\nHTTP %{http_code}\n" \
  | tee deploy/dev/evidence/logs/t1a-g002-cskh-health.txt
# kỳ vọng: {"message":{"app":"crm_cskh","status":"ok","app_version":"0.0.1","user":"Administrator"}}

# (4) GET /cskh (HTML)
curl -s -b /tmp/cookies-cskh.txt -o /tmp/cskh.html -w "HTTP %{http_code}\n" \
  http://127.0.0.1:8000/cskh
# Kỳ vọng: HTTP 200; file có chứa "csrf-token" trong <meta> hoặc inline.
grep -c "csrf" /tmp/cskh.html
# Kỳ vọng: >= 1

# (5) POST thiếu token (kỳ vọng 403)
curl -s -o /dev/null -b /tmp/cookies-cskh.txt -X POST \
  http://127.0.0.1:8000/api/method/crm_cskh.api.health.ping \
  -w "HTTP %{http_code}\n" \
  | tee deploy/dev/evidence/logs/t1a-g002-cskh-no-token.txt
```

**Trên Chrome** (PowerShell không cần):

- Mở `http://127.0.0.1:8000/cskh` → trang render server-side, có nút "Kiểm tra phiên đăng nhập và CSRF".
- Bấm nút → DevTools Network: `POST /api/method/crm_cskh.api.health.ping` trả 200, body `{"message":{"status":"ok",...,"user":"Administrator"}}`.
- Nếu thiếu session: response là "Request denied" (Jinja cũng render message).
- Chụp screenshot: `deploy/dev/evidence/screenshots/t1a-g002-02-cskh-jinja.png`.

### 4.8. Không bật `ignore_csrf`

```powershell
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc 'cat /workspace/frappe-bench/sites/dev.localhost/site_config.json' \
  | Select-String "ignore_csrf" | Tee-Object deploy/dev/evidence/logs/t1a-g002-site-config.txt
# kỳ vọng: (không có dòng nào) → OK: ignore_csrf not set
```

### 4.9. Lưu log

Tất cả output đã được lưu trong các lệnh trên vào:

```
deploy/dev/evidence/logs/t1a-g002-vite-startup.txt
deploy/dev/evidence/logs/t1a-g002-csrf-token.txt
deploy/dev/evidence/logs/t1a-g002-login.txt
deploy/dev/evidence/logs/t1a-g002-curl-ok.txt
deploy/dev/evidence/logs/t1a-g002-curl-no-token.txt
deploy/dev/evidence/logs/t1a-g002-curl-bad-token.txt
deploy/dev/evidence/logs/t1a-g002-health-ok.txt
deploy/dev/evidence/logs/t1a-g002-cskh-csrf-token.txt
deploy/dev/evidence/logs/t1a-g002-cskh-login.txt
deploy/dev/evidence/logs/t1a-g002-cskh-health.txt
deploy/dev/evidence/logs/t1a-g002-cskh-no-token.txt
deploy/dev/evidence/logs/t1a-g002-site-config.txt
deploy/dev/evidence/screenshots/t1a-g002-01-hmr.png
deploy/dev/evidence/screenshots/t1a-g002-02-cskh-jinja.png
```

---

## 5. Checklist kết quả (CHƯA điền — chờ runtime)

- [ ] (a) Stack `db/redis-cache/redis-queue/bench` đều `Up (healthy)`.
- [ ] (b) `crm_cskh` cài thành công (`bench --site dev.localhost list-apps` có `crm_cskh`).
- [ ] (c) Vite dev server khởi động, log có dòng "frappe-ui vite plugin" và "VITE ready".
- [ ] (d) Truy cập `http://127.0.0.1:8080/crm` render CRM SPA (không trắng).
- [ ] (e) Sửa component `Probe.vue` → DOM tự cập nhật timestamp trong < 2 giây (HMR).
- [ ] (f) `GET /api/method/frappe.sessions.get_csrf_token` qua Vite proxy trả token.
- [ ] (g) `POST /api/method/frappe.ping` với token hợp lệ → HTTP 200 `{"message":"pong"}`.
- [ ] (h) `POST /api/method/frappe.ping` không token → HTTP 403.
- [ ] (i) `POST /api/method/frappe.ping` sai token → HTTP 403.
- [ ] (j) `POST /api/method/crm_cskh.api.health.ping` với token hợp lệ → HTTP 200 `{"message":{"status":"ok",...}}`.
- [ ] (k) `POST /api/method/crm_cskh.api.health.ping` không token → HTTP 403.
- [ ] (l) Truy cập `http://127.0.0.1:8000/cskh` render Jinja; bấm nút "Kiểm tra phiên" → response ok.
- [ ] (m) `site_config.json` KHÔNG chứa `ignore_csrf`.
- [ ] (n) Không có lỗi CORS/WebSocket trong browser console.

**Trạng thái hiện tại:** 0/14 — KHÔNG đánh dấu PASS.

---

## 6. Blocker log — điều kiện cần để chạy runtime

| # | Blocker | Owner cần |
|---|---|---|
| B-1 | `docker` không có trong PowerShell session Codex này (xác nhận 3 lần) | Mở PowerShell session mới sau khi Docker Desktop đã start; theo `environment-probe.md` §8, Docker Desktop + WSL2 đã chạy thành công 2026-10-15 nên có thể tái dựng |
| B-2 | `python` / `pip` không phải binary thật (chỉ App Execution Alias) | Cài Python 3.12+ thật nếu cần chạy ngoài container; container `bench` đã có Python 3.14.8 |
| B-3 | `yarn` không có trên host | Container `frappe/bench` đã có Yarn 1.22.22 (xem `wp01-runtime.md`); không cần cài trên host |
| B-4 | `wsl --status` trả help text trong session này (WSL2 đã chạy 2026-10-15 nhưng PowerShell session hiện không inherit WSL) | Mở PowerShell mới hoặc chạy `wsl -l -v` sau khi Docker Desktop start xong |
| B-5 | Không có GUI browser để chụp screenshot và verify Vite render | Owner dùng Chrome/Edge trên máy local; container đã publish port 8080 ra `127.0.0.1:8080` nên browser truy cập trực tiếp được |
| B-6 | `dev.localhost` không resolve trên Windows host | Thêm `127.0.0.1 dev.localhost` vào `C:\Windows\System32\drivers\etc\hosts` (cần quyền admin) — HOẶC dùng `127.0.0.1:port` + `Host: dev.localhost` header cho mọi curl request (xem §4.6, §4.7) |

---

## 7. Kết luận G0-02 (T1A lần 3)

- **Trạng thái:** ⛔ BLOCKER runtime. Không có bằng chứng runtime trong phiên này.
- **Phân tích tĩnh đã sửa:** T1A lần 1/2 viết sai rằng `/cskh` qua Vite; thực tế `/cskh` là Jinja template, cùng-origin với Frappe. Vấn đề Vite (nếu có) chỉ liên quan CRM frontend.
- **CRM upstream:** đã xử lý Vite proxy qua plugin `frappe-ui/vite` với `frappeProxy: true`. Không cần patch upstream `vite.config.js`.
- **Lệnh chạy runtime:** §4 — Owner cần chạy trên máy có Docker + browser.
- **Khi runtime chạy xong:** điền lại checklist §5 (14 mục) và đánh dấu lại G0-02 trong `p0-exit-report.md`. Đến lúc đó mới có thể nói "đạt" hay "không đạt".
- **KHÔNG tuyên bố PASS** ở phiên này.
