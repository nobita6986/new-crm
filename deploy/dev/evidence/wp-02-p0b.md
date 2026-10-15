# EVIDENCE — WP-02 / P0-B (scaffold `crm_cskh`, PoC field/DocType/API, tests)

> Thời điểm: **2026-10-15**. Môi trường: `crm-cskh-dev` (Docker/WSL2) — bench container `frappe/bench:v5.31.0`.
> Phạm vi: WP-02/P0-B. **Không** commit/push, **không** sửa upstream `frappe`/`crm`.

---

## 1. Baseline trong container (đối chiếu `version-lock.md`)

```
$ git -C apps/frappe rev-parse HEAD   -> d2efa79657ba5c1abf96098b3bfacc199517d57e   (v15.123.1)
$ git -C apps/crm    rev-parse HEAD   -> 0d3910b456ef4fb913e77b29db011b3debc4f778   (v1.87.0)
```

Python trong image bench: **3.14.8** (nằm trong dải `>=3.10,<3.15` của Frappe v15).
`bench --version` → `5.31.0`.

## 2. `list-apps` — site DEV và site TEST

```
$ docker compose exec bench bash -lc 'cd /workspace/frappe-bench && bench --site dev.localhost list-apps'
frappe   15.123.1 HEAD
crm      1.87.0   HEAD
crm_cskh 0.0.1    UNVERSIONED

$ ... --site test_cskh.localhost list-apps
frappe   15.123.1 HEAD
crm      1.87.0   HEAD
crm_cskh 0.0.1    UNVERSIONED
```

App được đưa vào bench bằng **symlink** (repo là nguồn duy nhất; `/src` mount read-only):

```
$ ln -s /src/apps/crm_cskh /workspace/frappe-bench/apps/crm_cskh
$ bench --site dev.localhost install-app crm_cskh
Installing crm_cskh...
Updating Dashboard for crm_cskh
```

## 3. Migrate (lặp lại, idempotent)

| Lần | Site | Trạng thái site | Kết quả |
|---|---|---|---|
| #1 | dev.localhost | trước seed | OK (`Updating DocTypes for frappe / crm / crm_cskh`) |
| #2 | dev.localhost | trước seed | OK — không lỗi |
| #3 | dev.localhost | **đã có seed** | OK — không lỗi, dữ liệu seed còn nguyên |

Bằng chứng dữ liệu seed không bị migrate làm hỏng: chạy lại seed (idempotent) sau migrate #3 →
**không tạo bản ghi mới** (không có dòng `+`), tức các user/lead/task/call log cũ vẫn tồn tại.

```
$ docker compose exec bench bash -lc 'bash /src/deploy/dev/seed/run-seed.sh'
== Roles ==
== Users ==
== Sales Hierarchy ==
== Leads ==
== Call Log (kỹ thuật, KHÔNG phải outcome CSKH) ==
== Task ==
== Attachment ==
== Reassignment / team change (state-level + audit note) ==
== XONG ==
Seed hoàn tất cho site 'dev.localhost'.
```

> Lưu ý format fixture: `import_file_by_path` yêu cầu mỗi record có `"doctype"`. Bản đầu thiếu
> khóa này → migrate #0 lỗi `KeyError: 'doctype'`; đã sửa trong `fixtures/custom_field.json`.

## 4. Health API (đăng nhập) — gọi thật

```
$ bench --site dev.localhost execute crm_cskh.api.health.ping
{"app": "crm_cskh", "status": "ok", "app_version": "0.0.1", "user": "Administrator"}
```

Không trả DB credentials / site config / đường dẫn.

## 5. Test suite (site sạch `test_cskh.localhost`)

Tạo site sạch + cài đủ 3 app, bật `allow_tests`:

```
$ bench new-site test_cskh.localhost --db-root-password ... --admin-password admin \
    --no-mariadb-socket --mariadb-user-host-login-scope=%
$ bench --site test_cskh.localhost install-app crm
$ bench --site test_cskh.localhost install-app crm_cskh
$ bench --site test_cskh.localhost set-config allow_tests true
```

Kết quả:

```
$ bench --site test_cskh.localhost run-tests --app crm_cskh
.........
----------------------------------------------------------------------
Ran 9 tests in 2.307s

OK
```

Chi tiết theo module:

| Module | Số test | Kết quả |
|---|---|---|
| `crm_cskh.crm_cskh.tests.test_health` | 4 | OK |
| `crm_cskh.crm_cskh.tests.test_poc_custom_field` | 2 | OK |
| `crm_cskh.crm_cskh.doctype.cskh_app_settings.test_cskh_app_settings` | 3 | OK |

**Phạm vi đã kiểm chứng thực tế (không suy rộng):**

- API health: `Guest` bị từ chối qua `frappe.is_whitelisted()` (hàm mà HTTP handler dùng để chặn Guest);
  endpoint không nằm trong `frappe.guest_methods`; user đã đăng nhập gọi được; response đúng shape an toàn.
- Custom Field PoC tồn tại trên `CRM Lead` và đọc/ghi được — đây là **kiểm chứng lưu trữ/metadata**,
  **KHÔNG** phải kiểm chứng quyền: test dùng `ignore_permissions=True` và `frappe.db.set_value` nên
  bỏ qua tầng quyền. Không tính là bằng chứng cho yêu cầu permission của P0-B (security matrix = WP-05).
- DocType PoC `CSKH App Settings` tồn tại sau migrate; Administrator ghi được; user không quyền **không** có `write`.

**CHƯA kiểm chứng:** đường HTTP đầy đủ (transport, CSRF, reverse proxy) cho endpoint —
test chỉ chạm tầng whitelist của Frappe. Security matrix đầy đủ (SEC-01…SEC-16) là **WP-05**, chưa làm.

## 6. Upstream diff — đã phát hiện và ĐÃ KHÔI PHỤC theo quyết định T0

Trước khôi phục, `apps/crm` có 2 file **sinh tự động** bị sửa:

```
$ git -C apps/crm status --porcelain
 M frontend/auto-imports.d.ts
 M frontend/yarn.lock

$ git -C apps/crm diff --numstat frontend/auto-imports.d.ts frontend/yarn.lock
3   1   frontend/auto-imports.d.ts
88  80  frontend/yarn.lock
```

- mtime **2026-10-15 12:47:21–23 (+07:00)**; container `bench` hiện tại khởi động **13:00:53**,
  WP-02 chạy sau ~13:39 ⇒ các thay đổi này **có trước WP-02**, sinh khi cài/build frontend CRM
  (`yarn.lock` đổi phiên bản Cropper, `auto-imports.d.ts` đổi), không phải sửa mã nguồn có chủ đích.

**Hành động (T0 quyết định — khôi phục về commit đã ghim):**

```
$ git -C apps/crm checkout -- frontend/yarn.lock frontend/auto-imports.d.ts
$ git -C apps/crm status --porcelain        # (rỗng)
$ git -C apps/frappe status --porcelain     # (rỗng)
$ git -C apps/crm diff --numstat            # (rỗng)
```

Kết quả: **cả `apps/frappe` và `apps/crm` đều sạch**, SHA không đổi:
`frappe d2efa79657ba5c1abf96098b3bfacc199517d57e`, `crm 0d3910b456ef4fb913e77b29db011b3debc4f778`.

## 7. License

`crm_cskh` đặt `app_license = "agpl-3.0"` (xem `apps/crm_cskh/crm_cskh/hooks.py`).
**Cần rà soát** nghĩa vụ AGPL-3.0 khi chạy cùng Frappe CRM (AGPL-3.0). **Chưa kết luận pháp lý.**

## 8. Phần chưa làm / blocker

- **Fresh-bench CHƯA xác minh.** `bootstrap.sh` (đường symlink `INSTALL_CSKH=1`, dòng 119) **chưa**
  được chạy trên bench volume mới. Vì vậy kết quả hiện tại được tính là:
  **app cài được trên bench HIỆN CÓ**, KHÔNG phải "reproducible từ clean setup".
- Chưa có CI (WP-07); chưa có bước test tự động trong pipeline.
- Chưa chạy security matrix (WP-05); **không** có API đọc Lead ⇒ **không** có negative permission test cho Lead.
- `deploy/dev/seed/seed_demo.py` (WP-01) chỉ chạy đúng khi gọi qua `run-seed.sh`; gọi `./env/bin/python`
  trực tiếp từ stdin gặp lỗi logging `/workspace/logs/database.log` — chưa điều tra gốc.

## 9. Rà soát đã xử lý (theo review WP-02)

| # | Vấn đề | Trạng thái |
|---|---|---|
| 1 | `.pyc`/`__pycache__` lọt vào deliverable; app thiếu `.gitignore` | **Đã sửa**: xóa toàn bộ `__pycache__`/`*.pyc` khỏi `apps/crm_cskh`; thêm `apps/crm_cskh/.gitignore` (bỏ qua `__pycache__/`, `*.py[cod]`, `node_modules/`, …) |
| 2 | `apps/crm` bẩn (`frontend/yarn.lock`, `auto-imports.d.ts`) | **Đã sửa** theo quyết định T0 — khôi phục về commit ghim; cả `frappe` và `crm` sạch (§6) |
| 3 | Fresh-bench bootstrap chưa chạy | **Ghi nhận, KHÔNG tuyên bố reproducible** (§8) |
| 4 | Test Custom Field dùng `ignore_permissions=True` → không chứng minh quyền | **Đã sửa cách diễn giải**: docstring test ghi rõ đây là kiểm chứng lưu trữ/metadata, không phải permission (§5) |
