# Bàn giao T1A → T1B — gói DEV/i18n (T1A Round 33+34, 2026-10-31)

**Người gửi:** T1A · **Người nhận:** T1B · **Mục đích:** đóng gói + CI trên commit đóng gói cuối.
**PR #2:** `state=open`, head `e0ef02f8619c428ef4b2457fc5ce5f04cb0d7baa`, merge `136a10c20740cff0a2bab5254843e94763aff5ae`, CI run **#41159273106** (run_number 61, `success`), artifact `13026861084` (140 924 B, digest API `sha256:8cfe5f10…`).

> **KHÔNG merge PR #2.** T1A lượt này **không** stage/commit/push. Việc commit + chạy CI thuộc T1B.
>
> ⚠️ **Round 34 (lượt 4) — SỬA LỖI CHẶN trong patcher.** Lượt kiểm tra này áp patcher lên checkout CRM **nguyên bản** (không phải config đã vá sẵn) và phát hiện patcher sinh ra `vite.config.js` **không parse được** ⇒ Vite dev server **không khởi động được**. Đã sửa + thêm cổng kiểm tra cú pháp `node --check`. Chi tiết: [`evidence/t1a-g002-vite-csrf-blocker.md`](t1a-g002-vite-csrf-blocker.md) **§13**. **⇒ Bắt buộc commit lại gói patcher (hash đã đổi) và chạy CI mới.**

---

## 0. Tóm tắt Round 34 — thay đổi so với Round 33

| # | Thay đổi | Tác động |
|---|---|---|
| 1 | **Sửa lỗi cú pháp patcher** (`optimizeDeps` nuốt dấu `]` đóng mảng `include:`) | `vite.config.js` hỏng ⇒ Vite không khởi động ⇒ **G0-02 DEV không tái dựng được**. Đây là lỗi **chặn**, không phải cosmetic |
| 2 | **Thêm cổng kiểm tra cú pháp** `check_js_syntax()` (dùng `node --check`) cho `vite.config.js`, `socket.js`, `main.js`, `translation.js` | Lỗi cú pháp ⇒ patcher dừng **exit 2** ngay tại lúc vá, không đợi tới lúc khởi động Vite |
| 3 | **Thêm script tái dùng** [`verify-socket-namespace.mjs`](../verify-socket-namespace.mjs) | Kiểm chứng namespace Socket.IO thật bằng **một lệnh**, in JSON kết quả, exit 0 = PASS / 2 = FAIL; không ghi cookie/token |
| 4 | Cập nhật evidence: `t1a-g002-vite-csrf-blocker.md` §13, artifact socket namespace → **v3** | Ghi rõ lỗi, nguyên nhân, tại sao trước đây không phát hiện, và cách kiểm chứng (kể cả **kiểm tra phản chứng**) |

> **Vì sao lỗi này lọt qua:** `patch_once()` trả `False` ngay khi chuỗi đích đã có trong file ⇒ khi chạy trên config **đã vá sẵn** thì phép thay thế **không bao giờ chạy** ⇒ không sinh code hỏng. `docker compose config --quiet` chỉ kiểm tra YAML, không parse JS. CI (`ruff`, `docker compose config`) không có bước nào parse `vite.config.js` sau khi vá. Lỗi chỉ lộ ra khi **áp lên checkout nguyên bản** rồi **khởi động Vite**.

---

## 1. Phân biệt revision (bắt buộc khi viết báo cáo)

| Loại | Giá trị | Ghi chú |
|---|---|---|
| **Revision chạy CI** | `e0ef02f` (merge `136a10c2`), run `#41159273106` | Đã xanh; T1C đã kiểm định độc lập (99/99 + 18/18 + 9/9 + 74/74) |
| **Revision lưu evidence (gói T1A)** | workspace `b1087bc911a35b2310ddd3e80dd17f6438170dec` **+ thay đổi chưa commit dưới đây** | Evidence DEV/i18n được sinh ở đây. **Chưa** có CI run trên revision này |
| **Giới hạn DEV/P1** | Bằng chứng là DEV (site giả `dev.localhost`, Vite dev server); P1 còn hoãn | Xem `docs/architecture/p0-exit-report.md` mục "Round 33" §4 |

> `b1087bc` **KHÔNG còn là** "PR HEAD/revision cuối". Mọi câu "revision cuối" trong báo cáo phải ghi rõ loại revision theo bảng trên.

---

## 2. Danh sách file thuộc gói T1A (35 file)

### 2.1 Mã nguồn / cấu hình DEV (5 file)

| File | Bytes | SHA-256 |
|---|---|---|
| `apps/crm_cskh/crm_cskh/translations/vi.csv` | 10,065 | `7e7dbbc219af9164c144d7a59161c46dc78bcaa313ef25e5a30d4464cd24d779` |
| `deploy/dev/apply-crm-vite-dev-patch.py` | 36,480 | `2aa2aabf7e3f13ba23dd5bccde64529edae8f81c5c4da876493bd327a11464c9` |
| `deploy/dev/docker-compose.yml` | 4,056 | `a9d47ea4f4c8ca37e2435898d8579c9a155cf89677ac48693633386891c48ae1` |
| `deploy/dev/README.md` | 5,169 | `4691afb93dfe1480219162d70ec3b23945135f0beec57b0aaa98255ac13ad81f` |
| `deploy/dev/verify-socket-namespace.mjs` | 4,287 | `dafc4145f4da2365a83bb4b2f0e3031d2ce272ac98e83d7e1d88a57de802a4a2` |

### 2.2 Evidence T1A (5 file)

| File | Bytes | SHA-256 |
|---|---|---|
| `deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md` | 44,176 | `fc9dba6aae7c60275fb8432da52735abe75174281a44156c5ac63807f6f9bc4a` |
| `deploy/dev/evidence/t1a-g009-vi-checklist.md` | 36,877 | `2856d3bd8aa489fd56307e6a40f989f5ab73038eef67f206989b3636d641b5e4` |
| `deploy/dev/evidence/t1a-handover-t1b-dev-i18n.md` | *(tự tham chiếu)* | Hash của chính file này thay đổi mỗi lần sửa ⇒ **không** ghi hash tĩnh. Xác minh bằng `git hash-object` / `sha256sum` tại thời điểm đóng gói |
| `deploy/dev/evidence/logs/t1a-g002-socket-namespace-evidence-20261031.json` | 5,810 | `b445f234d1f0cd05da570837f6590a144b92a69f7f65aa8247af6b2aa3ff5f88` |
| `deploy/dev/evidence/logs/t1a-g009-capture-manifest-20261031-final-v3.json` | 9,690 | `a183bdc28710998d47adc44c7f0bb82fafff963d96bc83d94e9a12c74ced55c6` |

### 2.3 Bộ ảnh cuối 2026-10-31 — `screenshots/t1a-g009-20261031-final/` (11 file: 10 ảnh + manifest)

| File | Bytes | SHA-256 |
|---|---|---|
| `capture-manifest.json` | 9,690 | `a183bdc28710998d47adc44c7f0bb82fafff963d96bc83d94e9a12c74ced55c6` |
| `01-login.png` | 18,265 | `a6a900f3317b993833fed6da48d801b003decfa92fc9f45d1adfb2395e546734` |
| `02-preferences.png` | 55,569 | `1fb196900ee81cb5d0f04fe75d8e6f323d3b318815d5d259b7afc3bfd3ee4d6e` |
| `03-leads-list.png` | 98,460 | `f485429eb2541b1f03cefb8e6d6cc8e26fc032b958805ded19ad8e9da4ad4cd3` |
| `04-lead-detail.png` | 91,872 | `936e546d4e639d7fc55aa092d6eb8553d9e41fcada227b26046d3ff0f6255adb` |
| `05-call-logs.png` | 40,674 | `5307fef669cdccd095eadda6640f70ebdb9801c610f98974fd1684a06acf4f89` |
| `06-tasks.png` | 40,914 | `0198078703b046758dd1c6f8dabf14849cd300ac9e477a705b73082cebcd33e4` |
| `07-data-import.png` | 25,248 | `9ebf182a267aff43271502997ade351ea9625bd4988bfa4f5d3cad9e6202bb2a` |
| `08-dashboard.png` | 61,031 | `698a0f57038e79b99df049be833ad244020a72cd80729fda0ce25ca858b7afb7` |
| `09-cskh.png` | 25,320 | `de69f91bf8aabad715962ceec731035a052b7674c9ae385fccce10ef9af40ae7` |
| `10-validation-error.png` | 72,157 | `3492144900d63628744e667a210d2b658cca5df26f619ffc84ed2997bd04648c` |

### 2.4 Bộ ảnh trước 2026-10-29 (giữ để đối chiếu lịch sử) — 11 file

| File | Bytes | SHA-256 |
|---|---|---|
| `screenshots/t1a-g009-20261029-final-155200/capture-manifest.json` | 8,265 | `04605cbf000260650271656060ffb95d72c4b58a13eb18c507902721a13ac3de` |
| `01-login.png` | 18,265 | `a6a900f3317b993833fed6da48d801b003decfa92fc9f45d1adfb2395e546734` |
| `02-preferences.png` | 55,569 | `1fb196900ee81cb5d0f04fe75d8e6f323d3b318815d5d259b7afc3bfd3ee4d6e` |
| `03-leads-list.png` | 98,460 | `f485429eb2541b1f03cefb8e6d6cc8e26fc032b958805ded19ad8e9da4ad4cd3` |
| `04-lead-detail.png` | 71,120 | `10c2ed9094fc54e772e08215c983ec951be2bc5dad5662d83fc1ff260d68a642` |
| `05-call-logs.png` | 40,674 | `5307fef669cdccd095eadda6640f70ebdb9801c610f98974fd1684a06acf4f89` |
| `06-tasks.png` | 40,914 | `0198078703b046758dd1c6f8dabf14849cd300ac9e477a705b73082cebcd33e4` |
| `07-data-import.png` | 25,248 | `9ebf182a267aff43271502997ade351ea9625bd4988bfa4f5d3cad9e6202bb2a` |
| `08-dashboard.png` | 61,031 | `698a0f57038e79b99df049be833ad244020a72cd80729fda0ce25ca858b7afb7` |
| `09-cskh.png` | 25,320 | `de69f91bf8aabad715962ceec731035a052b7674c9ae385fccce10ef9af40ae7` |
| `10-validation-error.png` | 72,157 | `3492144900d63628744e667a210d2b658cca5df26f619ffc84ed2997bd04648c` |

### 2.5 Artifact i18n phụ (3 file)

| File | Bytes | SHA-256 |
|---|---|---|
| `logs/t1a-g009-capture-manifest-20261029-final-v2.json` | 8,265 | `04605cbf000260650271656060ffb95d72c4b58a13eb18c507902721a13ac3de` |
| `logs/t1a-g009-placeholder-translation-20261029.json` | 1,445 | `17b8ce22f4302cfb9711b9d56dfe10c3fc4620e3ac6e4c620949059ddcff6a33` |
| `logs/t1a-g009-priority-terms-20261029.json` | 2,361 | `ff162bbe96b4d94a62312bfd1f4514a0fdfccba52e7879eb978c3bd982dc64c9` |
| `logs/t1a-g009-recapture-04-20261031.json` | 849 | `f6b3e0a372533220a20824a59e51d003c87417a35fe6dcfecfc04cf88bc0085a` |

---

## 3. File của T1B/T1C — **BẢO TOÀN, KHÔNG SỬA**

T1A lượt này **không đụng** các file sau. T1B giữ nguyên khi đóng gói:

> **Hash cập nhật ở Round 34:** T1B/T1C đã cập nhật 4 file dưới đây sau Round 33 (`t1c-security-verification.md`, `t1c-ext2-…`, `permission-matrix.md`, `risk-register.md`). Hash ở bảng này là **trạng thái hiện hành** sau các cập nhật đó — không phải hash ghi ở Round 33.

| File | Bytes | SHA-256 |
|---|---|---|
| `deploy/dev/evidence/t1c-security-verification.md` | 84,777 | `d291a02e4fa8a9f1bee2458431ae40d9e92753dffb4cba2da9666edb3c42ddd8` **(cập nhật Round 34)** |
| `deploy/dev/evidence/t1c-ext1-verification-2026-10-30.md` | 10,397 | `1d5ac86d304576311f0cf34c41f3a701286ab74bc255ed84cd757026cfe24b03` |
| `deploy/dev/evidence/t1c-ext2-matrix-gap-verification-2026-10-31.md` | 21,254 | `ab29f5cc3f01de1c189eeef8d8b806b54e1503b29bf463ad8a574a59832e2c84` **(cập nhật Round 34)** |
| `docs/architecture/permission-matrix.md` | 74,101 | `177f12fb075f18488627737384a1b2dab98ba6bc596262174b6d4855d1f4c221` **(cập nhật Round 34)** |
| `docs/architecture/risk-register.md` | 46,739 | `4c215b44439595b3499dd1c9f84838b0e7c42b93e9176191371c358e5aa3c12b` **(cập nhật Round 34)** |

Ngoài ra **KHÔNG sửa**: code app (`apps/crm_cskh/crm_cskh/**/*.py`), test, workflow (`.github/workflows/*`), ADR (`docs/architecture/adr-*`), `deploy/dev/seed/*`.

---

## 4. Diff gói T1A so với `b1087bc`

```bash
git diff --stat b1087bc -- \
  apps/crm_cskh/crm_cskh/translations/vi.csv \
  deploy/dev/apply-crm-vite-dev-patch.py \
  deploy/dev/docker-compose.yml \
  deploy/dev/README.md \
  deploy/dev/evidence/t1a-g002-vite-csrf-blocker.md \
  deploy/dev/evidence/t1a-g009-vi-checklist.md \
  deploy/dev/evidence/logs/ \
  deploy/dev/evidence/screenshots/t1a-g009-20261031-final/ \
  deploy/dev/evidence/screenshots/t1a-g009-20261029-final-155200/ \
  docs/architecture/p0-exit-report.md
```

### 4.1 Tóm tắt thay đổi ý nghĩa

| # | Thay đổi | Vì sao |
|---|---|---|
| 1 | `apply-crm-vite-dev-patch.py`: proxy `/socket.io` **`changeOrigin: true` → `false`** (tự nâng cấp từ bản cũ) | `changeOrigin: true` khiến Vite ghi đè `Host` thành `127.0.0.1:9000`, còn `Origin` là `dev.localhost` ⇒ realtime từ chối namespace join với **`Invalid origin`**. Handshake/polling vẫn 200 nên bằng chứng cũ bỏ sót. |
| 2 | `apply-crm-vite-dev-patch.py`: thêm `port: Number(process.env.VITE_DEV_PORT \|\| 8080)` vào khối `frappeProxy` | `frappe-ui` tự tính cổng Vite từ `webserver_port` và **ghi đè** `--port` của CLI. Realtime tự xác thực qua `Origin + /api/...` ⇒ cổng trong `Origin` phải truy cập được trong container. |
| 3 | `docker-compose.yml`: thêm `VITE_DEV_PORT: ${VITE_PORT:-8080}`; publish `127.0.0.1:${VITE_PORT}:${VITE_PORT}` (thay `…:${VITE_PORT}:8080`) | Giữ **cổng Vite host == container**. Lệch ⇒ `ECONNREFUSED` ⇒ namespace join fail. |
| 4 | `docker-compose.yml`: `extra_hosts: dev.localhost:127.0.0.1` cho service `bench` | Realtime so sánh hostname `Host`/`Origin`; `dev.localhost` phải resolve trong container. |
| 5 | `vi.csv`: +22 khóa (218 tổng) | Bổ sung nhãn UI tiếng Việt còn sót + 2 override theo context (`Due Date:CRM Task`, `Lead Name:CRM Lead`). |
| 6 | `README.md`: mục "Cổng DEV — quy tắc nhất quán host ↔ container" | Ghi rõ quy tắc + lệnh remap để lượt sau không lặp lại lỗi. |
| 7 | `t1a-g002-vite-csrf-blocker.md` §12 | Bằng chứng namespace Socket.IO thật + 2 nguyên nhân gốc + đo có đối chứng. |
| 8 | `p0-exit-report.md` | Tách 3 loại revision; cập nhật G0-02/G0-09/G0-10; thêm mục Round 33. |
| 9 | **`apply-crm-vite-dev-patch.py`: sửa lỗi cú pháp patch `optimizeDeps`** | Anchor cũ nuốt mất dấu `]` đóng mảng `include:` ⇒ `vite.config.js` không parse được ⇒ Vite không khởi động. Sửa: phát lại `],` trong phần thay thế |
| 10 | **`apply-crm-vite-dev-patch.py`: thêm `check_js_syntax()`** (cổng `node --check`) | Lỗi cú pháp do patch ⇒ exit 2 **ngay lúc vá**, thay vì lộ ra muộn khi khởi động Vite |
| 11 | **Thêm `deploy/dev/verify-socket-namespace.mjs`** | Kiểm chứng namespace Socket.IO thật bằng 1 lệnh, tái dùng được cho mọi lượt kiểm tra; không ghi cookie/token |
| 9 | **`apply-crm-vite-dev-patch.py`: sửa lỗi cú pháp patch `optimizeDeps`** | Anchor `"      ],\n    },\n    server: {"` **bao gồm** dấu `]` đóng mảng `include:` nhưng phần thay thế **không phát lại** ⇒ mảng `include:` bị bỏ dấu đóng ⇒ `vite.config.js` không parse ⇒ **Vite không khởi động được**. Xem §13 evidence |
| 10 | **`apply-crm-vite-dev-patch.py`: thêm `check_js_syntax()`** (`node --check`) | Lỗi cú pháp ⇒ patcher dừng **exit 2** ngay lúc vá, thay vì đợi tới lúc khởi động Vite mới lộ |
| 11 | **`verify-socket-namespace.mjs` (file mới)** | Kiểm chứng namespace Socket.IO thật bằng 1 lệnh; in JSON kết quả; exit 0 = PASS / 2 = FAIL; không ghi cookie/token |

---

## 5. Lệnh tái dựng (reproducible rebuild)

### 5.1 Tái dựng môi trường DEV từ đầu

```bash
cd deploy/dev
cp .env.example .env          # sửa mật khẩu DEV, KHÔNG commit
# Cổng: VITE_PORT (host) PHẢI BẰNG VITE_DEV_PORT (container) — xem README.md
docker compose up -d db redis-cache redis-queue bench
docker compose exec -u root bench bash -lc 'mkdir -p /workspace && chown -R frappe:frappe /workspace'
docker compose exec bench bash /src/deploy/dev/bootstrap.sh
docker compose exec bench bash /src/deploy/dev/seed/run-seed.sh
```

`bootstrap.sh` đã gọi sẵn cả 3 patcher theo thứ tự:

```
python $SRC_DIR/deploy/dev/apply-crm-ui-vi-patch.py   $CRM_FRONTEND
python $SRC_DIR/deploy/dev/apply-crm-vite-dev-patch.py $BENCH_DIR/apps/crm
python $SRC_DIR/deploy/dev/apply-frappe-vi-patch.py    $BENCH_DIR/apps/frappe
bench --site $SITE clear-cache
```

### 5.2 Chạy DEV

```bash
docker compose exec bench bash -lc 'cd /workspace/frappe-bench && bench start'
# realtime
docker compose exec -d bench bash -lc 'cd /workspace/frappe-bench && node apps/frappe/socketio.js'
# vite (VITE_DEV_PORT phải khớp cổng publish trên host)
docker compose exec -d bench bash -lc \
  'cd /workspace/frappe-bench/apps/crm/frontend && VITE_DEV_PORT=$VITE_DEV_PORT node node_modules/.bin/vite --host 0.0.0.0 --port $VITE_DEV_PORT'
```

### 5.3 Nếu phải dùng cổng khác (ví dụ 8000/8080/9000 đã bị chiếm)

```bash
# HOST
WEB_PORT=8010 SOCKETIO_PORT=9010 VITE_PORT=8091 docker compose up -d --force-recreate bench
# trong container
docker compose exec -d bench bash -lc \
  'cd /workspace/frappe-bench/apps/crm/frontend && VITE_DEV_PORT=8091 node node_modules/.bin/vite --host 0.0.0.0 --port 8091'
```

### 5.4 Kiểm tra lại (kiểm chứng idempotent + namespace thật)

> **Quan trọng:** kiểm tra idempotency **phải chạy trên checkout CRM nguyên bản**, không phải trên config đã vá sẵn. Nếu chạy trên config đã vá, `patch_once()` trả `False` ngay ⇒ phép thay thế **không chạy** ⇒ bỏ sót lỗi cú pháp (§0).

```bash
# 1) Áp patcher lên checkout CRM NGUYÊN BẢN (đây là bước bắt được lỗi cú pháp)
docker compose exec bench bash -lc \
  'cd /workspace/frappe-bench/apps/crm && git checkout -- . && git clean -fdq frontend/src'
docker compose exec bench bash -lc \
  'python /src/deploy/dev/apply-crm-vite-dev-patch.py /workspace/frappe-bench/apps/crm'
# kỳ vọng: exit 0, "Vite config: patched"
# nếu exit 2 với "is not valid JavaScript" -> patcher đã bắt được lỗi cú pháp

# 2) Cổng cú pháp: config sau khi vá phải parse được
docker compose exec bench bash -lc \
  'cd /workspace/frappe-bench/apps/crm/frontend && node --check vite.config.js && echo SYNTAX_OK'
# kỳ vọng: SYNTAX_OK

# 3) Idempotent: chạy lại 2 lần, cấu trúc file phải không đổi
docker compose exec bench bash -lc \
  'python /src/deploy/dev/apply-crm-vite-dev-patch.py /workspace/frappe-bench/apps/crm'
docker compose exec bench bash -lc \
  'cd /workspace/frappe-bench/apps/crm/frontend && sha256sum vite.config.js'
# kỳ vọng: "already patched" + cùng SHA-256 qua cả 3 lần

# 4) Config đúng sau khi vá
docker compose exec bench grep -n -A 3 'frappeProxy:' /workspace/frappe-bench/apps/crm/frontend/vite.config.js
docker compose exec bench grep -n changeOrigin /workspace/frappe-bench/apps/crm/frontend/vite.config.js
# kỳ vọng: changeOrigin: false
docker compose exec bench grep -n -A 4 'optimizeDeps:' /workspace/frappe-bench/apps/crm/frontend/vite.config.js
# kỳ vọng: mảng include: có dấu '],' đóng TRƯỚC khi tới exclude:

# 5) compose hợp lệ
docker compose config --quiet && echo COMPOSE_OK

# 6) Namespace Socket.IO THẬT (không chỉ polling 200) — dùng script tái dùng
docker compose exec bench bash -lc \
  'cp /src/deploy/dev/verify-socket-namespace.mjs /workspace/frappe-bench/apps/crm/frontend/'
docker compose exec bench bash -lc \
  'cd /workspace/frappe-bench/apps/crm/frontend && node verify-socket-namespace.mjs \
     --origin http://dev.localhost:8091 --web http://dev.localhost:8000 --site dev.localhost'
# kỳ vọng: {"result":"PASS",...,"connected":true,"nsp":"/dev.localhost",
#           "transport":"websocket","pong":true,"error":null}   và exit 0
```

### 5.5 Kiểm tra manifest 10 ảnh

```bash
python - <<'PY'
import hashlib, json, os
d = "deploy/dev/evidence/screenshots/t1a-g009-20261031-final"
m = json.load(open(os.path.join(d, "capture-manifest.json"), encoding="utf-8"))
ok = True
for s in m["screenshots"]:
    p = os.path.join(d, f"{s['id']}-{s['name']}.png")
    b = open(p, "rb").read()
    good = hashlib.sha256(b).hexdigest() == s["sha256"] and len(b) == s["bytes"]
    ok &= good
    print(s["id"], s["name"], "OK" if good else "MISMATCH")
print("MANIFEST_OK" if ok else "MANIFEST_FAIL")
PY
```

---

## 6. Kết quả kiểm chứng (T1A đã chạy, 2026-10-31)

| Kiểm tra | Kết quả |
|---|---|
| Patcher trên CRM ghim `0d3910b4…` | Chạy được; tự từ chối SHA lệch pin |
| **Patcher áp lên checkout NGUYÊN BẢN (Round 34)** | exit **0**, `Vite config: patched` — trước Round 34 bước này **sinh ra config hỏng** |
| **`node --check vite.config.js` sau khi vá (Round 34)** | **OK** — syntax hợp lệ |
| **Kiểm tra phản chứng (Round 34)** | Cố ý tái tạo lỗi cũ ⇒ patcher dừng **exit 2** với `is not valid JavaScript` ⇒ cổng kiểm tra **thực sự bắt được** lỗi |
| Patcher idempotent | Chạy 3 lần trên checkout nguyên bản ⇒ `vite.config.js` **cùng SHA-256** `6e07ab3f…` qua cả 3 lần, exit 0 |
| Patcher tự nâng cấp config cũ | `changeOrigin: true` → `false`; thêm `port: …VITE_DEV_PORT…` |
| `docker compose config --quiet` | Hợp lệ; `VITE_DEV_PORT` = `VITE_PORT` |
| `VITE_DEV_PORT` truyền vào container | `8091` khi `VITE_PORT=8091` |
| Vite bind đúng cổng trong container | `8091` (không còn bị frappe-ui ghi đè về 8080) |
| **Vite khởi động từ config vừa vá (Round 34)** | `VITE v8.2.0 ready in 937 ms` — **không** còn PARSE_ERROR |
| **Namespace Socket.IO (FIXED)** | `connected=true`, `nsp=/dev.localhost`, `transport=websocket`, `emit('ping')` → **`pong`** |
| **Namespace Socket.IO sau khi sửa lỗi cú pháp (Round 34)** | `connected=true`, `nsp=/dev.localhost`, `transport=websocket`, `pong=true` — xác nhận lại bằng script tái dùng `verify-socket-namespace.mjs` |
| **Namespace Socket.IO (REGRESSION, chỉ đổi `changeOrigin: true`)** | `connected=false`, `connect_error="Invalid origin"` |
| Engine.IO polling qua Vite | HTTP 200 + open packet (không còn HTML SPA fallback) |
| CSRF qua Vite proxy | 6/6 PASS (token hợp lệ 200 / thiếu 400 / sai 400) |
| `vi.csv` | **218 khóa**, 0 trùng theo `(msgid, context)`, 0 rỗng, 0 lỗi cấu trúc |
| `vi.csv` nạp runtime | `get_all_translations('vi')` = **8.369 khóa**; override context đúng |
| Manifest 10 ảnh | **10/10** đúng SHA-256 **và** đúng `bytes`; không có file thừa |
| Màn 04 | `must` 4/4 + `forbid` 4/4 PASS; rà mắt: `Chuyển thành Cơ hội` / `…đã tạo khách hàng tiềm năng này` — không còn `Chuyển thành Deal` / `đã tạo lead này` |
| PII residual | **0** email / **0** phone trên cả 10 màn |

---

## 7. KHÔNG đưa vào gói

| Loại | Ví dụ | Lý do |
|---|---|---|
| Bí mật | `deploy/dev/.env`, `deploy/dev/.env.local` | `.gitignore` đã chặn; chứa mật khẩu DEV |
| Profile Chrome | `sites/g009-chrome-profile/` trong container, `*chrome-profile*` | Dữ liệu phiên trình duyệt |
| Script debug | `_tmp_*.py`, `_tmp_*.sh`, `_tmp_*.js`, `_tmp_*.json`, `_tmp_crop/`, `_tmp_out.txt`, `_tmp_head_patcher.py` | Bản làm việc tạm của T1A, **không** đưa vào repo |
| Thư mục tạm của T1B/T1C | `_recovery/`, `.commandcode/` | Ngoài phạm vi gói T1A |
| Dữ liệu nhạy cảm | dump DB, `sites/*/private/`, log có token/cookie | Chỉ dữ liệu giả; log đã redact |
| Kết quả dò tạm | `_tmp_t1a_socket_results*.json`, `_tmp_t1a_translations.json` | Đã tổng hợp vào artifact chính thức |

**Lưu ý:** `.gitignore` ở `deploy/dev/` chặn `.env`, `workspace/`, `dumps/`, `backups/`, `*.log` — nhưng **KHÔNG** chặn `_tmp_*` hay `_recovery/`. T1B nên thêm pattern ignore cho các thư mục tạm này hoặc tự xóa trước khi commit, để tránng lọt vào commit đóng gói.

---

## 8. Việc T1B cần làm

1. **Dọn thư mục tạm** theo §7 trước khi stage.
2. **Stage đúng** danh sách §2 + bản `p0-exit-report.md` đã cập nhật; **giữ nguyên** các file T1B/T1C ở §3.
3. **Commit** trên branch hiện tại (`codex/p0-t1a-g001-g002-g009`) — PR #2 giữ **OPEN**, **KHÔNG merge**.
4. **Chạy CI** trên commit đóng gói cuối; ghi lại **run id + head SHA + merge SHA + artifact id + digest** (T1C sẽ kiểm định độc lập).
5. **Bàn giao T1C**: kết quả CI + danh sách file đã commit + hash artifact.
6. **Cập nhật `p0-exit-report.md`** sau khi có CI trên commit đóng gói cuối:
   - Ghi **revision chạy CI** (run mới) tách khỏi **revision lưu evidence** (commit đóng gói).
   - Ghi rõ **giới hạn DEV/P1** (§1 của `p0-exit-report.md` mục Round 33 §4).
   - **Không** gọi `b1087bc` là "PR HEAD/revision cuối".
7. **GO/NO-GO P0 thuộc T0/Owner** — T1A/T1B/T1C không tự ký.

### 8.1 Yêu cầu bổ sung cho Round 34 — **ĐÃ XONG**

Vì patcher đã đổi (sửa lỗi cú pháp), CI cũ ở `e0ef02f` **KHÔNG còn phản ánh** gói DEV hiện tại:

| # | Việc | Trạng thái |
|---|---|---|
| A | **Commit lại** `deploy/dev/apply-crm-vite-dev-patch.py` (hash mới `2aa2aabf…`, thay `063be77f…`) + `verify-socket-namespace.mjs` + evidence §13 | ✅ Đã commit (`ddf817c` — patcher + script; `ba49857` — p0-exit-report + handover) |
| B | **Chạy CI mới** trên commit đóng gói cuối | ✅ Run **#41183641044** (run_number 64, `success`), head `9fdeee6`, merge `f65fec1b`, artifact `13036566067` (140 947 B, digest `sha256:d20ef778…`) |
| C | **Tái chạy bước §5.4** (đặc biệt bước 1–2) | ✅ Đã chạy trên **byte đã commit** (`git show HEAD:deploy/dev/apply-crm-vite-dev-patch.py` → sha256 `2aa2aabf…`): áp lên checkout nguyên bản exit 0, `node --check` OK, idempotent 3 lần cùng SHA-256 `6e07ab3f…`, Vite `ready in 522 ms`, namespace Socket.IO `PASS` (`connected=true`, `nsp=/dev.localhost`, `transport=websocket`, `pong=true`) |
| D | **Cân nhắc thêm cổng CI** cho `node --check vite.config.js` sau khi vá | ⬜ Chưa thêm trong CI. Cổng kiểm tra đã có **trong patcher** (fail-fast exit 2), nhưng CI chưa gọi nó ⇒ nên thêm ở lượt tiếp theo |

> **Không** tự kết luận G0-02 tăng/giảm verdict. G0-02 đang **PASS (Owner đã duyệt)**; Round 34 chỉ **siết** bằng chứng (chứng minh patcher tái dựng được từ checkout nguyên bản) và **sửa một lỗi chặn** trong gói DEV.
>
> **PR #2 vẫn OPEN, KHÔNG merge.**

---

## 9. Bất biến T1A lượt này

- KHÔNG sửa file T1B/T1C/ADR/code app/test/workflow/seed.
- KHÔNG mutate dữ liệu nghiệp vụ (chỉ `clear-cache` + đăng nhập + chụp ảnh trên dữ liệu giả).
- KHÔNG stage/commit/push/merge PR #2.
- KHÔNG tự ký P0.
- Không log/đưa vào gói cookie phiên, CSRF token hay mật khẩu.
