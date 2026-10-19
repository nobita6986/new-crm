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

### 4.1. Khởi động stack (sau khi áp dụng `version-lock.md` §5 digest)

```bash
cd /path/to/new-crm
docker compose -f deploy/dev/docker-compose.yml pull
docker compose -f deploy/dev/docker-compose.yml up -d
docker compose ps
# kỳ vọng: 4 service đều "Up (healthy)": db, redis-cache, redis-queue, bench

docker compose exec backend bench --site dev.localhost install-app crm_cskh
docker compose exec backend bench --site dev.localhost migrate
```

### 4.2. Khởi động CRM frontend (Vite, port 8080)

```bash
docker compose exec backend bash -lc '
  cd /home/frappe/frappe-bench/apps/crm/frontend &&
  yarn install --check-files &&
  yarn dev --host 0.0.0.0
'
# kỳ vọng: "VITE v8.2.0  ready in NNN ms  ➜  Local: http://localhost:8080/"
# kỳ vọng: "frappe-ui vite plugin: frappeProxy enabled"
```

### 4.3. Verify Vite page không trắng (CRM frontend)

Mở Chrome trên máy Owner, truy cập `http://127.0.0.1:8080/crm`:

- Tab Network: SPA shell HTTP 200, request `/api/...` được proxy sang 8000 (status 200, không CORS error).
- Tab Console: KHÔNG có lỗi "WebSocket connection to ws://... failed" hoặc "CORS policy".
- DOM: `<div id="app">` có markup Vue render (form login CRM).

Nếu thấy trắng:
- Kiểm tra log Vite có dòng "frappe-ui vite plugin" không.
- Kiểm tra container có `apps/frappe` checkout tại `../../frappe` không (cần cho `@framework/ui` alias).
- Kiểm tra Network tab: request `/api/method/frappe.sessions.get_csrf_token` có response 200 qua proxy.

### 4.4. Verify Vite hot reload

Trong container, sửa 1 component CRM:

```bash
docker compose exec backend bash -lc '
  echo "<template><div>Probe T1A G0-02 — {{ ts }}</div></template>" > /tmp/probe.vue
  echo "<script setup>const ts = new Date().toISOString();</script>" >> /tmp/probe.vue
  cp /tmp/probe.vue /home/frappe/frappe-bench/apps/crm/frontend/src/components/Probe.vue
'
```

Trong browser DevTools Network tab, xác nhận có message `WebSocket message: 'update'` cho file `Probe.vue`. DOM phải tự cập nhật timestamp trong < 2 giây.

### 4.5. Verify CSRF retention (qua proxy upstream)

```bash
# Lấy CSRF token qua Vite proxy (port 8080)
TOKEN=$(curl -s -c /tmp/cookies.txt http://127.0.0.1:8080/api/method/frappe.sessions.get_csrf_token \
  -H 'X-Requested-With: XMLHttpRequest' \
  | python -c 'import json,sys;print(json.load(sys.stdin)["message"])')
echo "CSRF token: $TOKEN"

# Login
curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt -X POST \
  http://127.0.0.1:8080/api/method/login \
  -H "X-Frappe-CSRF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"usr":"Administrator","pwd":"admin"}' | tee /tmp/login.json
# kỳ vọng: {"message":"Logged In"}

# POST hợp lệ — frappe.ping (kỳ vọng HTTP 200, body {"message":"pong"})
curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "X-Frappe-CSRF-Token: $TOKEN" -w "\nHTTP %{http_code}\n" | tee /tmp/ping-ok.txt

# POST hợp lệ — crm_cskh.api.health.ping (kỳ vọng HTTP 200, body {"message":{...,"status":"ok",...}})
curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/crm_cskh.api.health.ping \
  -H "X-Frappe-CSRF-Token: $TOKEN" -w "\nHTTP %{http_code}\n" | tee /tmp/health-ok.txt

# POST thiếu token (kỳ vọng HTTP 403)
curl -s -o /dev/null -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -w "HTTP %{http_code}\n" | tee /tmp/ping-no-token.txt

# POST sai token (kỳ vọng HTTP 403)
curl -s -o /dev/null -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "X-Frappe-CSRF-Token: WRONG-TOKEN-12345" \
  -w "HTTP %{http_code}\n" | tee /tmp/ping-bad-token.txt
```

### 4.6. Verify `/cskh` (Jinja, không qua Vite)

Mở Chrome truy cập `http://127.0.0.1:8000/cskh` (KHÔNG qua Vite — dùng port Frappe 8000):

- Trang render server-side, có nút "Kiểm tra phiên đăng nhập và CSRF".
- Bấm nút → DevTools Network: `POST /api/method/crm_cskh.api.health.ping` trả 200, body `{"message":{"status":"ok",...,"user":"Administrator"}}`.
- Nếu thiếu session/CSRF: response là "Request denied".

Có thể test riêng CSRF bằng curl trực tiếp port 8000:

```bash
TOKEN=$(curl -s -c /tmp/cookies.txt http://127.0.0.1:8000/api/method/frappe.sessions.get_csrf_token \
  -H 'X-Requested-With: XMLHttpRequest' \
  | python -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:8000/api/method/crm_cskh.api.health.ping \
  -H "X-Frappe-CSRF-Token: $TOKEN" -w "\nHTTP %{http_code}\n"
```

### 4.7. Không bật `ignore_csrf`

```bash
docker compose exec backend bash -lc 'cat /home/frappe/frappe-bench/sites/dev.localhost/site_config.json' \
  | grep -E '"ignore_csrf"|"ignore_csrf_for_ssl"' || echo "OK: ignore_csrf not set"
# kỳ vọng: OK: ignore_csrf not set
```

### 4.8. Lưu log

Lưu tất cả output curl vào:

```
deploy/dev/evidence/logs/t1a-g002-curl-{ok,no-token,bad-token}.txt
deploy/dev/evidence/logs/t1a-g002-health-ok.txt
deploy/dev/evidence/logs/t1a-g002-login.txt
deploy/dev/evidence/logs/t1a-g002-vite-startup.txt
deploy/dev/evidence/logs/t1a-g002-site-config.txt
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
| B-1 | `docker` không có trên máy phiên Codex (xác nhận 3 lần) | Chạy G0-02 trên máy có Docker Desktop / WSL2 + Docker |
| B-2 | `python` / `pip` không phải binary thật (chỉ App Execution Alias) | Cài Python 3.12 hoặc 3.13 thật nếu cần chạy ngoài container |
| B-3 | `yarn` không có | Container `frappe/bench` đã có yarn sẵn; không cần cài trên host |
| B-4 | `wsl --status` không chạy (WSL feature chưa enable) | Owner chạy trực tiếp trên máy có Docker Desktop hoặc WSL2 distro |
| B-5 | Không có GUI browser để chụp screenshot và verify Vite render | Owner dùng Chrome/Edge trên máy local; container cần port 8080 mapped ra host |

---

## 7. Kết luận G0-02 (T1A lần 3)

- **Trạng thái:** ⛔ BLOCKER runtime. Không có bằng chứng runtime trong phiên này.
- **Phân tích tĩnh đã sửa:** T1A lần 1/2 viết sai rằng `/cskh` qua Vite; thực tế `/cskh` là Jinja template, cùng-origin với Frappe. Vấn đề Vite (nếu có) chỉ liên quan CRM frontend.
- **CRM upstream:** đã xử lý Vite proxy qua plugin `frappe-ui/vite` với `frappeProxy: true`. Không cần patch upstream `vite.config.js`.
- **Lệnh chạy runtime:** §4 — Owner cần chạy trên máy có Docker + browser.
- **Khi runtime chạy xong:** điền lại checklist §5 (14 mục) và đánh dấu lại G0-02 trong `p0-exit-report.md`. Đến lúc đó mới có thể nói "đạt" hay "không đạt".
- **KHÔNG tuyên bố PASS** ở phiên này.
