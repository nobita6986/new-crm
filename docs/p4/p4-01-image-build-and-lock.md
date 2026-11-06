# Báo cáo Thực thi & Hồ sơ Khóa Image `P4-01`: Custom Production Image, Release Lock & Kiểm định Site Sạch (`G02`)

- **Gói công việc:** `P4-01` (Custom Production Image, Source/Dependency Lock, Clean-Site Verification & `P4-02` Input Contract)
- **Đơn vị thực hiện:** `T1B`
- **Nhánh làm việc cô lập:** `codex/p4-01-image`
- **Baseline ứng dụng:** `main` tại `92e14af3532876ad5bc2468bf0e502474cc95a53`
  - `git rev-parse HEAD:apps`: `b37e2cbdd99c88c7bd5620912de4519b53b2c159` (`0` thay đổi trong `apps/*` và `.github/workflows/*`)
  - `git rev-parse HEAD:apps/crm_cskh`: `da6373a01ad963301877f69c413fdda9a361f718`
- **Hồ sơ đầu vào `P4-00`:** `be33e6c931337c42b94184b2fe292e094bd51c97` (`docs/p4/entry-audit.md`, `docs/p4/infra-inventory.md`)
- **Đối chiếu nhánh Compose `T1A`:** `codex/p4-01-compose` tại `e2235559aa5f31669ff11a2d264e785819a7612a` (`docs/p4/P4-ADR-01-compose-and-isolation.md`)
- **Tài liệu liên kết:**
  - [`docs/p4/P4-ADR-02-custom-image-and-lock.md`](./P4-ADR-02-custom-image-and-lock.md)
  - [`docs/p4/p4-02-pipeline-input-contract.md`](./p4-02-pipeline-input-contract.md)
  - [`docs/p4/staging-migration-backlog.md`](./staging-migration-backlog.md) (`BLG-P4-MIG-01`)
  - [`deploy/images/release-lock.json`](../../deploy/images/release-lock.json)
  - [`deploy/images/Containerfile`](../../deploy/images/Containerfile)
- **Quy tắc khóa mốc image smoke (`T0` chốt):** Giữ nguyên image đã build tại commit `4de3dc8656b0077cabd4220afc6636da543e34f6` (`4de3dc8`, `APP_IMAGE=sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`) làm mốc smoke cho `T1A` / `T1C`. Chỉ mở bản sửa image khi `T1A` tái hiện lỗi thuộc phạm vi image (khi đó bàn giao SHA mới, image ID mới và chạy lại [`verify_clean_site.py`](../../deploy/images/verify_clean_site.py)). Remote registry digest tiếp tục giữ trạng thái **`BLOCKED`**.

---

## 1. Tóm tắt Kết quả & Định danh Image Bất biến (`APP_IMAGE`)

| Hạng mục | Giá trị Xác thực Thực tế | Trạng thái |
| :--- | :--- | :---: |
| **Tag cục bộ trên Docker Engine (`v29.9.2`)** | `crm-cskh:0.0.1-92e14af` | `READY` |
| **Image ID bất biến cục bộ (`APP_IMAGE` cho smoke)** | `sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` | `READY` |
| **Local containerd `RepoDigests[0]`** | `crm-cskh@sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` | `READY` |
| **OCI Config Digest (bên trong manifest)** | `sha256:ea7c24011f9b8bdac989a3ba2aca1032379a7728df2f4bc96ad1a41f4c8fcd7f` | `VERIFIED` |
| **Kiến trúc / Hệ điều hành / User mặc định** | `linux/amd64` / Debian 12 (`bookworm`) / `frappe` (`UID 1000`, `GID 0` — non-root) | `VERIFIED` |
| **Dung lượng giải nén image** | `5,238,141,558` bytes (`~4.88 GiB` — đã loại bỏ ~600 MB `apps/crm/{,frontend/}node_modules`) | `VERIFIED` |
| **Kiểm định site sạch + HTTP assets + nhãn tiếng Việt (`G02`)** | **`45/45` assertions `PASS`** ([`deploy/images/evidence/clean-site-verify.json`](../../deploy/images/evidence/clean-site-verify.json)) | `PASS` |
| **Kiểm chứng đẩy & kéo Digest từ Remote Registry** | `approved_registry_repo_digest: null` — chưa có Container Registry URL và tài khoản `push`/`pull` được phê duyệt trong `P4-00` (`infra-inventory.md` dòng 19) | **`BLOCKED`** |

---

## 2. Rà soát Đường Build từ `frappe_docker@v4.0.1` & Bảng Khóa Phiên bản (`release-lock.json`)

### 2.1. Kết quả Rà soát `frappe/frappe_docker@v4.0.1` (`2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`)

1. **`images/layered/Containerfile` không đạt yêu cầu kiểm toán P4:** Kéo tag động `frappe/base:${FRAPPE_BRANCH}`, chỉ nạp app qua URL Git từ xa trong `apps.json` (không nạp được `apps/crm_cskh` từ thư mục con monorepo), không cho phép chèn bước vá Việt hóa `frappe-ui` sau `yarn install` / trước `bench build`, và xóa toàn bộ `.git` trong `apps/*` khiến mất khả năng đối chiếu `git rev-parse HEAD` lúc runtime.
2. **`images/custom/Containerfile` cần ghim lại ma trận Frappe v15:** Ở `v4.0.1`, các `ARG` mặc định đã đổi sang Frappe v16 (`PYTHON_VERSION=3.14`, `NODE_VERSION=24`, `DEBIAN_BASE=trixie`). Căn cứ tệp `.github/workflows/core-build-stable.yml` (dòng 49–52) của chính `frappe_docker@2b26e9ab8d`, ma trận chuẩn cho nhánh `version-15` là `python=3.11`, `node=22`, `debian=bookworm`.
3. **Quyết định triển khai (`P4-ADR-02`):** Đóng gói [`deploy/images/Containerfile`](../../deploy/images/Containerfile) kế thừa trực tiếp cấu trúc 3 tầng (`base -> builder -> backend`) và bộ tài nguyên runtime của `frappe_docker@2b26e9ab8d`, khóa cứng 100% qua [`deploy/images/release-lock.json`](../../deploy/images/release-lock.json).

### 2.2. Bảng Khóa Nguồn & Khóa Phụ thuộc Thực tế Đã Đóng Gói Vào Image

| Thành phần | Phiên bản / Tag | Định danh Khóa Bất biến (Commit SHA / Digest / SHA-256) | Bằng chứng Đối chiếu |
| :--- | :--- | :--- | :--- |
| **Builder upstream (`frappe_docker`)** | `v4.0.1` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | `release-lock.json`, `io.cskh.frappe_docker_sha` |
| **Base OS & Python image** | `python:3.11.18-slim-bookworm` | Index: `sha256:6cbe849bdc876ecd413a4a09a919a0d861d908589de9eba61e886c29af793a07`<br>`linux/amd64`: `sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944` | `Containerfile` dòng 20, `build-manifest.json` |
| **Frappe Framework (`frappe`)** | `v15.123.1` | Commit: `d2efa79657ba5c1abf96098b3bfacc199517d57e`<br>`apps/frappe/yarn.lock`: `ec8d4dfd0fd9ed82b102167768ff6d09b50184153335d12bcad4a1017a660c10` | `clean-site-verify.json` (`runtime.frappe_sha`), `yarn-locks.sha256` |
| **Frappe CRM (`crm`)** | `v1.87.0` | Commit: `0d3910b456ef4fb913e77b29db011b3debc4f778`<br>`apps/crm/yarn.lock`: `49e1cb522c1350c51654c391a30dfc1bd2cf1f40f2342dfba6048a4e744ea5b3`<br>`apps/crm/frontend/yarn.lock`: `b0d89f3bed314eb1fb7b7cfbab8c6a95855583196e0f327326f1f9c69cd299ab` | `clean-site-verify.json` (`runtime.crm_sha`), `yarn-locks.sha256` |
| **Custom App (`crm_cskh`)** | Khai báo `0.0.1` *(cấm dùng làm release ID)* | Baseline `main`: `92e14af3532876ad5bc2468bf0e502474cc95a53`<br>`apps/` tree SHA: `b37e2cbdd99c88c7bd5620912de4519b53b2c159`<br>`apps/crm_cskh` tree SHA: `da6373a01ad963301877f69c413fdda9a361f718`<br>Manifest 279 tệp nguồn: `e0042f63a06d432bd8d75ad77a3c19dcf71d8d135df84b1c578cd4adae0b6957` | `crm-cskh-files.sha256`, `clean-site-verify.json` (`runtime.cskh_tree_sha`) |
| **Toolchain trong image** | Python `3.11.18`, Node `v22.23.3`, Yarn `1.22.22`, Bench `5.31.0` | `nvm v0.40.6` installer SHA-256: `2ef7e8d4373c1ffd70daa55f919f629e98a619543ffc0a8d892d77a5247e50e4`<br>`wkhtmltox_0.12.6.1-3.bookworm_amd64.deb`: `98ba0d157b50d36f23bd0dedf4c0aa28c7b0c50fcdcdc54aa5b6bbba81a3941d` | `build-manifest.json`, `Containerfile` |
| **Khóa gói Python (`pip freeze`)** | `155` gói Python | SHA-256: `b95703859aabb0b288b4e72330949c00a3d1e81db0915643a6ee20ca10479f8e` | [`deploy/images/evidence/pip-freeze.txt`](../../deploy/images/evidence/pip-freeze.txt) |
| **Khóa gói OS (`dpkg-query`)** | `442` gói Debian Bookworm | SHA-256: `ca7d8013b43b5af77f78e3a2468f3994f630f18b37f1268d93070438a2a79be3` | [`deploy/images/evidence/dpkg-packages.txt`](../../deploy/images/evidence/dpkg-packages.txt) |
| **Biên dịch Production Assets** | Desk + Web + `/crm` Vue SPA + `/cskh/*` | `sites/assets/assets.json` SHA-256: `d5f25ba7ceef8dd337b31db9eb896d53981633d402357c941f578b16a4df1d08`<br>`apps/crm/crm/www/crm.html` SHA-256: `94b0d51a060966e39538beb0bb01bc0a936c67b7597c0005e9650cda73f45d5c` | `build-manifest.json` |

---

## 3. Tách Bạch Ranh giới Bản vá Giao diện Tiếng Việt (Production Image vs Quy trình DEV)

Đúng theo yêu cầu kỹ thuật của `P4-01` và `ADR-UI-001`:

1. **`deploy/dev/apply-frappe-vi-patch.py` (Đưa vào Production Image):** Áp vào 2 dòng Jinja của `apps/frappe` (`templates/includes/navbar/navbar.html` và `www/doc.html`: `{{ _('Toggle navigation')|e }}`, `{{ _('Sidebar')|e }}`) trước khi chạy `bench build --production --force`. Được kiểm chứng trong [`upstream-patches.diff`](../../deploy/images/evidence/upstream-patches.diff) và assertion `runtime.frappe_jinja_vi_patched = PASS`.
2. **`deploy/dev/apply-crm-ui-vi-patch.py` (Đưa vào Production Image):** Thực thi **sau khi** chạy `yarn --cwd apps/crm/frontend install --frozen-lockfile --check-files` và **trước khi** chạy `CI=true bench build --production --force`, vá trực tiếp các nhãn cứng trong `node_modules/frappe-ui` (`1.0.0-beta.29`) để gói bundle biên dịch `/assets/crm/frontend/assets/index-CRiPnyne.js` mang đầy đủ chuỗi dịch tiếng Việt mà không làm bẩn cây Git của `apps/crm`.
3. **`deploy/dev/apply-crm-vite-dev-patch.py` (Giữ nguyên trong quy trình DEV — Loại khỏi Production Image):** Bị loại trừ tường minh trong [`Containerfile.dockerignore`](../../deploy/images/Containerfile.dockerignore) và [`release-lock.json`](../../deploy/images/release-lock.json) (`dev_only_patchers_excluded`). Được kiểm chứng thực nghiệm bằng assertion `runtime.crm_no_vite_dev_patch = PASS` (`git -C apps/crm diff --name-only` chỉ ghi nhận duy nhất tệp `frontend/auto-imports.d.ts` do Vite tự sinh khoảng trắng lúc build, hoàn toàn không có sửa đổi Vite dev server).

---

## 4. Kết quả Kiểm định Site Sạch, Standalone Container & Nhãn Tiếng Việt (`45/45 PASS`)

Chạy kịch bản tự động [`deploy/images/verify_clean_site.py`](../../deploy/images/verify_clean_site.py) trên bộ Compose cô lập [`deploy/images/compose.verify.yaml`](../../deploy/images/compose.verify.yaml) (project `cskh-p4-verify-d22bd6`, site sạch `p4-clean-verify.localhost`, loopback `127.0.0.1:18780`, đã dọn sạch 100% container/volume tạm sau khi kết thúc):

- **Không bind-mount mã nguồn `/src` (`topology.no_host_bind_mounts = PASS`):** Cả 7 service runtime (`backend`, `frontend`, `websocket`, `queue-short`, `queue-long`, `scheduler`, `configurator`) chỉ gắn named volumes rỗng (`sites`, `logs`), tự khởi chạy độc lập từ image `sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`.
- **Thứ tự và danh sách ứng dụng trên site sạch (`site.list_apps` & `site.apps_txt_order = PASS`):**
  - `bench --site p4-clean-verify.localhost list-apps --format json` → `["frappe", "crm", "crm_cskh"]`
  - `sites/apps.txt` → `["frappe", "crm", "crm_cskh"]` (được bảo vệ bởi `entrypoint.sh` chống lỗi `ls -1 apps` đảo thứ tự bảng chữ cái).
  - `migrate` chạy 2 lần liên tiếp thành công (`idempotent`), `developer_mode = false`.
- **Tải tài sản tĩnh & động qua `frontend` (Nginx) → `backend` (Gunicorn) (`HTTP 200`):**
  - Frappe Desk & Web: `/assets/frappe/dist/js/desk.bundle.DWR4TLZJ.js` (`1,369,723` B), `/assets/frappe/dist/css/desk.bundle.NY3NG2BJ.css` (`599,739` B), `/assets/frappe/dist/js/frappe-web.bundle.BNEBYLYP.js` (`834,360` B).
  - Frappe CRM Vue SPA (`/crm` → `HTTP 200`, `925,940` B): `/assets/crm/frontend/assets/index-CRiPnyne.js` (`612,615` B), `/assets/crm/frontend/assets/Button-ug8Vt_Qh.css` (`2,094` B).
  - CSKH custom bundles: `cskh.js` (`36,328` B), `cskh.css` (`15,410` B), `p2-api.js` (`34,698` B), `p3-api.js` (`59,575` B), `quality-overview.js` (`6,077` B).
- **Kiểm chứng phân quyền & nhãn tiếng Việt lúc runtime (`PASS`):**
  - Guest truy cập `/cskh/dashboard` bị chặn đúng chính sách (`HTTP 403` — `http.guest.cskh_denied = PASS`).
  - Sau xác thực với ngôn ngữ `vi`, `/cskh/dashboard` và `/cskh/quality-overview` trả `HTTP 200` kèm múi giờ `Asia/Ho_Chi_Minh` và các nhãn điều hướng tiếng Việt (`Tổng quan`, `Chất lượng`, `Danh sách việc`, `Đăng xuất`).
  - Trang `/crm` nạp bảng dịch `translated_messages` tiếng Việt trong payload khởi động (`Khách tiềm năng`, `Cơ hội`, `Cuộc gọi`, `Công việc`, `Ghi chú` — `http.vi_labels.crm_boot_messages = PASS`).

---

## 5. Thống nhất Biến Compose Với `T1A` & Trạng thái `BLOCKED` của Remote Registry Digest

### 5.1. Hợp đồng Biến `APP_IMAGE` + `PULL_POLICY`

Đã đối chiếu trực tiếp với nhánh `codex/p4-01-compose` (`e2235559aa5f31669ff11a2d264e785819a7612a`) của `T1A` tại `deploy/compose/overrides/compose.cskh-common.yaml` và `deploy/compose/scripts/validate_compose_config.py`:

- **Biến nhận image duy nhất cho toàn bộ 8 service ứng dụng:** `APP_IMAGE`
  - **Chạy kiểm thử smoke cô lập ngay trên Docker Engine hiện tại (`v29.9.2`):**
    - `APP_IMAGE=sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` *(hoặc local repo digest `crm-cskh@sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`)*
    - `PULL_POLICY=never` *(hoặc `missing`)*
  - **Chạy trên CI `image-check`, Staging và Production (sau khi mở khóa registry):**
    - `APP_IMAGE=<registry>/<namespace>/crm-cskh@sha256:<64-hex>`
    - `PULL_POLICY=always`
- **Trạng thái Remote Registry Digest (`BLOCKED`):**
  - Do `P4-00` (`docs/p4/infra-inventory.md` dòng 19) ghi nhận chưa có Container Registry được duyệt và chưa cấp phát thông tin xác thực `push`/`pull`, bước đẩy lên remote registry và xác minh kéo lại theo `@sha256:<remote-manifest-digest>` được đánh dấu tường minh là **`BLOCKED`** trong [`image-inspect-summary.json`](../../deploy/images/evidence/image-inspect-summary.json) (`registry_verification.status = "BLOCKED"`, `approved_registry_repo_digest = null`). Tuyệt đối không tạo digest giả.

### 5.2. Ghi chú Phối hợp Tích hợp cho `T1A` / `T1C` Khi Chạy `run_isolated_smoke.py` Trên Host Windows

Khi thử nghiệm nạp trực tiếp `APP_IMAGE=sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` vào bộ Compose của `T1A` (`e223555`), toàn bộ 10 container khởi tạo hợp lệ, `configurator` thoát `0`, `db` và `redis-*` đều lên `healthy`. Tuy nhiên, trên Python chạy trực tiếp ở Windows (ngoài WSL/Linux), dòng 538 của `deploy/compose/scripts/run_isolated_smoke.py` gọi `sec_file.write_text(val + "\n", encoding="utf-8")` không truyền `newline="\n"`, khiến Python trên Windows ghi `\r\n` vào tệp `db_root_password.txt`; MariaDB nạp mật khẩu có ký tự `\r` ở cuối qua `MYSQL_ROOT_PASSWORD_FILE`, dẫn tới bước `bench new-site --db-root-password` bị lỗi `1045 Access denied` trên Windows (trên Linux/CI Ubuntu không bị ảnh hưởng; nếu chạy trực tiếp từ PowerShell trên Windows chỉ cần thêm `newline="\n"` ở hàm ghi secret tạm của `run_isolated_smoke.py`).

---

## 6. Báo cáo Riêng: Rà soát Cảnh báo / Sai khác Schema Module của `CSKH Export Job`

Theo chỉ đạo của `P4-01` (*"Rà cảnh báo module của `CSKH Export Job` khi cài và migrate... Nếu phát hiện lỗi schema, báo riêng với ca tái hiện"*, đồng thời **không sửa `apps/*` trong nhánh `P4-01`**):

### 6.1. Hiện tượng & Nguyên nhân Gốc trong Mã nguồn

- Trong [`apps/crm_cskh/crm_cskh/modules.txt`](../../apps/crm_cskh/crm_cskh/modules.txt) (dòng 1), tên module duy nhất được khai báo là chữ in hoa **`CRM CSKH`**.
- Trong tổng số **23** DocType thuộc `apps/crm_cskh/crm_cskh/crm_cskh/doctype/*/*.json`:
  - **22/23** DocType khai báo `"module": "CRM CSKH"`.
  - Riêng **1/23** DocType là [`apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_export_job/cskh_export_job.json`](../../apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_export_job/cskh_export_job.json) (dòng 171) khai báo lệch hoa-thường: **`"module": "Crm Cskh"`** (Title Case thay vì `CRM CSKH`).

### 6.2. Hành vi Thực tế Khi `install-app crm_cskh` và `migrate` Trên Site Sạch

Kết quả trích xuất trực tiếp tại mục `schema_audit` của [`deploy/images/evidence/clean-site-verify.json`](../../deploy/images/evidence/clean-site-verify.json) (dòng 39–86):

1. Khi cài đặt ứng dụng, `modules.txt` tạo bản ghi `tabModule Def` với `name = 'CRM CSKH'` (`app_name = 'crm_cskh'`).
2. Do MariaDB 11.8 cấu hình collation mặc định không phân biệt hoa-thường (`utf8mb4_unicode_ci`) trên cột khóa chính `tabModule Def.name`, câu truy vấn kiểm tra khóa ngoại `frappe.db.exists("Module Def", "Crm Cskh")` trả về **`True`** (`'Crm Cskh' = 'CRM CSKH'`), và hàm `frappe.scrub("Crm Cskh")` vẫn quy đổi về thư mục `"crm_cskh"`. Vì vậy:
   - `bench install-app crm_cskh` và `bench migrate` **không văng ngoại lệ dừng tiến trình**, không in dòng cảnh báo lỗi ra stdout/stderr, và vẫn tạo bảng vật lý `tabCSKH Export Job` (`cskh_export_job_table_exists = true`).
   - Tuy nhiên, cột `module` trong bảng metadata `tabDocType` bị ghi nhận giá trị lệch chuẩn **`'Crm Cskh'`**, tách rời khỏi 22 DocType còn lại mang giá trị `'CRM CSKH'`.

### 6.3. Ca Tái hiện Cụ thể (Reproduction Case)

Chạy trên bất kỳ site sạch nào vừa cài `crm_cskh` từ baseline `92e14af` (ví dụ trong container `backend` của image `sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`):

```bash
# Ca tái hiện 1 — Truy vấn phân biệt hoa-thường (BINARY) trên MariaDB:
bench --site p4-clean-verify.localhost mariadb -e \
  "SELECT CAST(module AS CHAR CHARACTER SET utf8mb4) AS module_exact, COUNT(*) AS doctype_count
   FROM \`tabDocType\`
   WHERE name LIKE 'CSKH %'
   GROUP BY BINARY module
   ORDER BY doctype_count DESC;"
# Kết quả thực tế:
# +--------------+---------------+
# | module_exact | doctype_count |
# +--------------+---------------+
# | CRM CSKH     |            22 |
# | Crm Cskh     |             1 |  <-- CSKH Export Job bị tách nhóm
# +--------------+---------------+

# Ca tái hiện 2 — Gom nhóm hoặc kiểm tra schema trong Python (phân biệt hoa-thường):
/home/frappe/frappe-bench/env/bin/python -c '
import frappe
frappe.init(site="p4-clean-verify.localhost", sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
rows = frappe.get_all("DocType", filters=[["name", "like", "CSKH %"]], fields=["name", "module"])
non_canonical = [r for r in rows if r["module"] != "CRM CSKH"]
print("Non-canonical module rows:", non_canonical)
assert len(non_canonical) == 1 and non_canonical[0] == {"name": "CSKH Export Job", "module": "Crm Cskh"}
'
```

- **Quyết định điều phối & Backlog Migration Staging (`BLG-P4-MIG-01`):** Sai khác hoa/thường `"module": "Crm Cskh"` vs `"CRM CSKH"` được ghi chính thức vào [`docs/p4/staging-migration-backlog.md`](./staging-migration-backlog.md) (`BLG-P4-MIG-01`) để kiểm chứng trong diễn tập migration Staging (`P4.md §6.3`, cổng `G06`). Trong `P4-01`, `apps/crm_cskh` và mốc image smoke `4de3dc8` (`sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`) được giữ nguyên 100%.

---

## 7. Kiểm tra Bảo toàn Nghiệp vụ & Assertion P0–P3

Toàn bộ kiểm tra tính toàn vẹn kho kiểm thử và hợp đồng hiện hữu trên nhánh `codex/p4-01-image` đều đạt `PASS` với `git diff origin/main -- apps/ .github/` rỗng tuyệt đối:

- `python deploy/p1/p1_test_inventory.py --check` → `OK: inventory matches 209 discovered P1 tests.`
- `python deploy/p2/p2_test_inventory.py --check` → `OK: inventory matches 38 discovered P2 tests.`
- `python deploy/p3/p3_test_inventory.py --check` → `OK: inventory matches 39 discovered P3 tests.`
- `python deploy/p3/ui_test_inventory.py --check` → `OK: UI inventory matches 7 discovered P3 UI specs.`
- `node deploy/p3/test_p3_ui_timezone_and_contracts.mjs` → `ALL CHECKS PASSED.`
- `python deploy/p1/check_translations_audit.py` → `Missing in vi.csv: 0`, `Untranslated identical: 0`, `Orphan keys: 0`.
- `ruff check deploy/images/verify_clean_site.py && ruff format --check deploy/images/verify_clean_site.py` → `All checks passed!`.
