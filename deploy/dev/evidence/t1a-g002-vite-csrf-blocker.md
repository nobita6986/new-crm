# EVIDENCE — T1A: G0-02 Vite hot reload + CSRF (BLOCKER runtime, không đánh dấu PASS)

> **Ngày xác minh:** 2026-10-19 (UTC+7)
> **Trạng thái:** ⛔ **BLOCKER runtime** — phiên Codex này KHÔNG có Docker/Python/Yarn.
> Chưa chạy được `bench start`, chưa thấy Vite page, chưa thực hiện được hot reload,
> chưa thực hiện được POST thật. Bằng chứng dưới đây là **phân tích tĩnh** (source code
> + cấu hình) + **lệnh Owner cần chạy** trên máy có Docker/WSL2.
> **Không sửa upstream CRM/Frappe.** Không bật `ignore_csrf=1`.

---

## 1. Công cụ & môi trường xác minh

| Công cụ | Giá trị | Ghi chú |
|---|---|---|
| `node` | v22.23.3 | có, nhưng Vite/dev server phải chạy trong container |
| `docker` | **không có** | xác nhận `Get-Command docker` |
| `python` / `python3` | **không có** | App Execution Alias stub trỏ Microsoft Store |
| `yarn` | **không có** | xác nhận `Get-Command yarn` |
| `wsl.exe` | có (10.0.19…) nhưng `wsl --status` không chạy được | không dùng |
| `curl` | dùng cho probe tới `raw.githubusercontent.com` & `hub.docker.com` | chỉ đọc |

→ Kết luận: KHÔNG thể khởi động stack `frappe/bench + mariadb + redis + crm_cskh/frontend`
trong phiên này. Toàn bộ G0-02 runtime evidence **chưa tồn tại**.

---

## 2. Phân tích tĩnh — đã xác minh bằng `git show` trên commit khóa

### 2.1. Vite dev server

| Mục | Giá trị | Nguồn |
|---|---|---|
| Vite cổng | `8080` | `crm_cskh/frontend/vite.config.js` (khai báo `server.port = 8080`, `server.host = '0.0.0.0'`) |
| Frappe dev server cổng | `8000` | image `frappe/bench:v5.31.0` mặc định |
| Đường dẫn build Vite | `/cskh/assets/crm_cskh/frontend/` | `crm_cskh/hooks.py` → `app_include_js` |
| Template HTML | `crm_cskh/www/cskh.html` (extends `templates/web.html`) | |
| Backend hook | `crm_cskh/www/cskh.py` (`get_context` → render); API qua `/api/method/...` | |

Vite HMR dùng WebSocket trên `ws://127.0.0.1:8080`. Trình duyệt sẽ chỉ chấp nhận
kết nối nếu Vite cho phép origin của Frappe; nếu Vite không có `server.cors` cho
`http://127.0.0.1:8000`, dev console sẽ log `WebSocket connection to ws://... failed`.

### 2.2. CSRF contract (đã đọc trong `crm_cskh/frontend/src/utils/cookie.js`)

- Cookie `sid` (session) do Frappe set tại `http://127.0.0.1:8000` với `SameSite=Lax`,
  `HttpOnly`, `Secure=false` (dev). Trình duyệt **không** gửi cookie này sang origin
  `http://127.0.0.1:8080` (Vite) vì khác origin.
- CSRF token lấy từ `frappe.sessions.get_csrf_token()` → gắn vào meta `csrf-token` ở
  `cskh.html`, sau đó client `cookie.js` đọc và set header `X-Frappe-CSRF-Token` cho
  mọi request.
- Frappe server (`frappe/auth.py`) kiểm tra:
  - Origin header phải khớp với host trong `site_config.host_name` (mặc định
    `http://127.0.0.1:8000` cho dev);
  - Header `X-Frappe-CSRF-Token` phải khớp session token;
  - Nếu 1 trong 2 thiếu → 400/403.
- Đây là lý do gốc rễ của G0-02: **trình duyệt không gửi cookie Frappe sang Vite vì
  khác origin**, kéo theo server không thấy session → POST bị từ chối.

### 2.3. Ba phương án xử lý (đã phân tích, chưa chạy runtime)

| # | Phương án | Cơ chế | Ưu | Nhược | Trạng thái |
|---|---|---|---|---|---|
| V-A | **Vite proxy same-origin**: cấu hình Vite proxy `127.0.0.1:8080/api` và `127.0.0.1:8080/files` sang `127.0.0.1:8000`; cập nhật `site_config` `host_name = http://127.0.0.1:8080`; truy cập qua `http://127.0.0.1:8080/cskh` | Tất cả request đi cùng origin, cookie `sid` gửi tự động, CSRF hợp lệ | Khớp production flow; không cần đổi upstream Frappe; `SameSite` mặc định OK; HMR vẫn chạy | Cần đổi 1 dòng `host_name` khi dev; cần 1 proxy block trong `vite.config.js` | **Đề xuất** |
| V-B | Cookie `SameSite=None; Secure=true` | Cho phép cookie sang origin khác | Cấu hình ít | Cần HTTPS cho dev; mọi API/UI phải dùng `https://`; cookie có thể bị Safari chặn | Từ chối (HTTPS dev gây phiền) |
| V-C | `ignore_csrf=1` | Tắt kiểm tra CSRF | Test nhanh | **CẤM**: tạo lỗ hổng CSRF; tài liệu này **không bật** dưới bất kỳ hình thức nào | **CẤM** |

### 2.4. Cấu hình V-A — snippet dự kiến (chỉ `vite.config.js` của app `crm_cskh`)

```js
// crm_cskh/frontend/vite.config.js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 8080,
    strictPort: true,
    cors: {
      origin: ['http://127.0.0.1:8000'],
      credentials: true,
    },
    proxy: {
      // Forward API + static + login session to Frappe, preserving cookies
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, secure: false },
      '/files': { target: 'http://127.0.0.1:8000', changeOrigin: true, secure: false },
      '/assets': { target: 'http://127.0.0.1:8000', changeOrigin: true, secure: false },
      '/socket.io': { target: 'http://127.0.0.1:8000', ws: true, changeOrigin: true },
    },
  },
})
```

Và `site_config.json` (chỉ dev) — đổi `host_name` để Frampe chấp nhận origin Vite:

```json
{
  "host_name": "http://127.0.0.1:8080"
}
```

> **Cảnh báo:** snippet trên chưa được áp dụng (phiên này không có Docker).
> Nó sẽ được Owner áp dụng trong lần chạy runtime (xem §3).

---

## 3. Lệnh Owner cần chạy trên máy có Docker/WSL2

### 3.1. Khởi động stack (sau khi đã áp dụng Q-02 từ `t1a-g001-…`)

```bash
# từ d:\HRP-app\new-crm (PowerShell) hoặc /workspace (WSL)
cd d:\HRP-app\new-crm

# 1. Kéo image theo digest đã verify ở G0-01
docker compose -f deploy/dev/docker-compose.yml pull

# 2. Khởi động stack
docker compose -f deploy/dev/docker-compose.yml up -d

# 3. Đợi bench healthy
docker compose ps
# kỳ vọng cột "STATUS" cả 3 service đều "Up (healthy)"

# 4. Cài app crm_cskh + migrate (nếu chưa)
docker compose exec backend bench --site dev.localhost install-app crm_cskh
docker compose exec backend bench --site dev.localhost migrate
```

### 3.2. Khởi động Vite dev server

```bash
docker compose exec backend bash -lc '
  cd /home/frappe/frappe-bench/apps/crm_cskh/frontend &&
  yarn install --frozen-lockfile &&
  yarn dev --host 0.0.0.0 --port 8080
'
# kỳ vọng log: "VITE vX.Y.Z  ready in NNN ms  ➜  Local: http://localhost:8080/"
```

### 3.3. Verify Vite page không còn trắng

```bash
# 1. Truy cập từ browser
# Mở http://127.0.0.1:8080/cskh trong Chrome
# - Tab Network: status 200 cho /cskh và /assets/crm_cskh/frontend/...
# - Tab Console: KHÔNG có lỗi "WebSocket connection to ws://... failed"
#   (nếu thấy, chứng tỏ Vite server.cors chưa cho phép origin 127.0.0.1:8000)
# - DOM: <div id="app"> có chứa markup Vue render

# 2. Chụp screenshot đầu tiên
#   lưu vào deploy/dev/evidence/screenshots/t1a-g002-01-vite-initial.png
```

### 3.4. Verify hot reload (sửa file → thấy ngay)

```bash
# Trong container, sửa 1 component Vue
docker compose exec backend bash -lc '
  cat > /tmp/probe.vue <<EOF
<template><div>Probe T1A G0-02 — {{ ts }}</div></template>
<script setup>const ts = new Date().toISOString();</script>
EOF
  cp /tmp/probe.vue /home/frappe/frappe-bench/apps/crm_cskh/frontend/src/components/Probe.vue
'

# Trong browser, tab Network sẽ log "WebSocket message: 'update'" cho file Probe.vue
# DOM tự cập nhật timestamp. Chụp screenshot:
#   deploy/dev/evidence/screenshots/t1a-g002-02-hmr-update.png
```

### 3.5. Verify CSRF — POST hợp lệ (kỳ vọng 200) và POST thiếu token (kỳ vọng 403)

```bash
# Lấy CSRF token
TOKEN=$(curl -s -c /tmp/cookies.txt http://127.0.0.1:8080/api/method/frappe.sessions.get_csrf_token \
  -H 'X-Requested-With: XMLHttpRequest' | python -c 'import json,sys;print(json.load(sys.stdin)["message"])')
echo "CSRF token: $TOKEN"

# Đăng nhập (nếu site có user admin)
curl -s -b /tmp/cookies.txt -c /tmp/cookies.txt \
  -X POST http://127.0.0.1:8080/api/method/login \
  -H "X-Frappe-CSRF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"usr":"Administrator","pwd":"admin"}' | tee /tmp/login.json
# kỳ vọng: {"message":"Logged In"}

# POST hợp lệ — ping 1 method không thay đổi dữ liệu
curl -s -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "X-Frappe-CSRF-Token: $TOKEN" | tee /tmp/ping-ok.json
# kỳ vọng: {"message":"pong"}

# POST thiếu token — kỳ vọng 403
curl -s -o /tmp/ping-bad.txt -w "HTTP %{http_code}\n" \
  -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping
# kỳ vọng: HTTP 403 (Forbidden, CSRF missing)

# POST sai token — kỳ vọng 403
curl -s -o /tmp/ping-bad2.txt -w "HTTP %{http_code}\n" \
  -b /tmp/cookies.txt -X POST http://127.0.0.1:8080/api/method/frappe.ping \
  -H "X-Frappe-CSRF-Token: WRONG-TOKEN-12345"
# kỳ vọng: HTTP 403
```

Lưu lại 3 file `/tmp/ping-ok.json`, `/tmp/ping-bad.txt`, `/tmp/ping-bad2.txt` vào
`deploy/dev/evidence/logs/t1a-g002-curl-{ok,no-token,bad-token}.txt`.

### 3.6. Không bật `ignore_csrf`

```bash
# Verify KHÔNG có ignore_csrf trong site_config
docker compose exec backend bash -lc 'cat /home/frappe/frappe-bench/sites/dev.localhost/site_config.json' \
  | grep -E '"ignore_csrf"|"ignore_csrf_for_ssl"' || echo "OK: ignore_csrf not set"
# kỳ vọng: OK: ignore_csrf not set
```

---

## 4. Checklist kết quả (CHƯA điền — chờ runtime)

- [ ] (V-A) Vite proxy config được áp dụng trong `vite.config.js` của `crm_cskh/frontend`.
- [ ] (V-A) `host_name` được đổi sang `http://127.0.0.1:8080` trong `site_config.json`.
- [ ] (1) Truy cập `http://127.0.0.1:8080/cskh` không còn trắng; Vue render đúng.
- [ ] (2) Sửa `Probe.vue` → DOM tự cập nhật timestamp trong < 2 giây (HMR ok).
- [ ] (3) `GET /api/method/frappe.sessions.get_csrf_token` trả token qua Vite proxy.
- [ ] (4) `POST /api/method/frappe.ping` với token hợp lệ → HTTP 200, body `{"message":"pong"}`.
- [ ] (5) `POST /api/method/frappe.ping` không có token → HTTP 403.
- [ ] (6) `POST /api/method/frappe.ping` với token sai → HTTP 403.
- [ ] (7) `site_config.json` KHÔNG chứa `ignore_csrf=1` hay `ignore_csrf_for_ssl=1`.
- [ ] (8) Screenshot `t1a-g002-01-vite-initial.png` và `t1a-g002-02-hmr-update.png` được
      lưu vào `deploy/dev/evidence/screenshots/`.

**Trạng thái hiện tại:** 0/9 — KHÔNG đánh dấu PASS.

---

## 5. Blocker log — điều kiện cần để chạy runtime

| # | Blocker | Owner cần |
|---|---|---|
| B-1 | `docker` không có trên máy phiên Codex | Chạy G0-02 trên máy có Docker Desktop / WSL2 + Docker |
| B-2 | `python` / `python3` chỉ là App Execution Alias stub trỏ Microsoft Store | Cài Python 3.12 hoặc 3.13 thật nếu cần chạy ngoài container |
| B-3 | `yarn` không có | Container `frappe/bench` đã có yarn sẵn; không cần cài trên host |
| B-4 | Phiên Codex không có quyền WSL (`wsl --status` không chạy) | Owner chạy trực tiếp trên máy có WSL2 distro đã enable |
| B-5 | Không có GUI browser để chụp screenshot và verify Vite render | Owner dùng Chrome/Edge trên máy local; container cần port 8080 mapped ra host |

---

## 6. Kết luận G0-02 (T1A)

- **Trạng thái:** ⛔ BLOCKER runtime. Không có bằng chứng runtime trong phiên này.
- **Phân tích tĩnh:** đã xác minh contract Vite ↔ Frappe (cookie, CSRF, origin).
- **Phương án đề xuất:** V-A (Vite proxy same-origin). Hai phương án khác (V-B
  SameSite=None, V-C ignore_csrf) **CẤM** vì lý do bảo mật/UX.
- **Lệnh chạy runtime:** §3 — Owner cần chạy trên máy có Docker + browser.
- **Khi runtime chạy xong:** điền lại checklist §4 và đánh dấu lại G0-02 trong
  `p0-exit-report.md`. Đến lúc đó mới có thể nói "đạt" hay "không đạt".
- **KHÔNG tuyên bố PASS** ở phiên này.
