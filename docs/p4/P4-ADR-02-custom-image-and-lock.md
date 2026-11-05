# P4-ADR-02: Chiến lược Build Custom Production Image, Khóa Phiên bản/Digest và Biên dịch Frontend Assets

- **Mã quyết định:** `P4-ADR-02`
- **Ngày lập:** 2026-11-05
- **Gói công việc:** `P4-01` (liên kết trực tiếp đầu vào cho `P4-02` và cổng nghiệm thu `G02`, `G04`, `G05`)
- **Baseline ứng dụng:** `main` tại `92e14af3532876ad5bc2468bf0e502474cc95a53` (`apps/` tree `b37e2cbdd99c88c7bd5620912de4519b53b2c159`, `apps/crm_cskh` tree `da6373a01ad963301877f69c413fdda9a361f718`)
- **Hồ sơ đầu vào đối chiếu:** `P4-00` tại `be33e6c931337c42b94184b2fe292e094bd51c97` (`docs/p4/entry-audit.md`, `docs/p4/infra-inventory.md`)
- **Upstream đã ghim:**
  - `frappe/frappe_docker`: `v4.0.1` → `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded`
  - `frappe/frappe`: `v15.123.1` → `d2efa79657ba5c1abf96098b3bfacc199517d57e`
  - `frappe/crm`: `v1.87.0` → `0d3910b456ef4fb913e77b29db011b3debc4f778`
- **Người phê duyệt:** `PENDING T0` (Kiến trúc sư trưởng / Chủ sở hữu phát hành)

---

## 1. Bối cảnh

Theo [`docs/root-plan/P4.md`](../root-plan/P4.md) (§4, §18, §19 `G02`/`G05`, §21 `P4-ADR-02`) và [`docs/architecture/version-lock.md`](../architecture/version-lock.md):

1. Ở các pha P0–P3, môi trường DEV và CI nội bộ sử dụng `frappe/bench:v5.31.0` kèm bind-mount mã nguồn trực tiếp từ máy chủ và khởi chạy bằng `bench start` + Vite dev server. Mô hình này không tạo ra artifact bất biến và bị cấm tuyệt đối trên Staging/Production (`P4.md §4.2`: không `git pull`, không `bench update`, không sửa code trực tiếp trên VPS).
2. Qua rà soát mã nguồn thực tế của `frappe/frappe_docker@2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` và `frappe/crm@0d3910b456ef4fb913e77b29db011b3debc4f778`, việc dùng nguyên xi các tệp mẫu upstream gặp **5 khoảng trống kỹ thuật nghiêm trọng**:
   - **Tag nền trôi nổi & xóa `.git` trong `images/layered/Containerfile`:** Tệp `layered` kéo `frappe/build:${FRAPPE_BRANCH}` và `frappe/base:${FRAPPE_BRANCH}` theo nhánh động, cài app qua `bench init --apps_path=/opt/frappe/apps.json` (chỉ nhận URL Git từ xa, không nạp được `apps/crm_cskh` nằm trong thư mục con của monorepo hiện tại), và chạy `find apps -mindepth 1 -path "*/.git" | xargs rm -fr` làm mất khả năng truy vết `git rev-parse HEAD` lúc runtime (`G02`).
   - **Lệch ma trận phiên bản mặc định trong `images/custom/Containerfile`:** Ở `frappe_docker@v4.0.1`, các `ARG` mặc định trong `images/custom/Containerfile` đã chuyển sang Frappe v16 (`PYTHON_VERSION=3.14`, `NODE_VERSION=24`, `DEBIAN_BASE=trixie`), trong khi ma trận chuẩn cho Frappe v15 tại `.github/workflows/core-build-stable.yml` (dòng 49–52) là `python_version=3.11`, `node_version=22`, `debian_base=bookworm`.
   - **Lỗi tương thích BuildKit secret trong workflow mẫu của `frappe/crm@v1.87.0`:** Tệp `.github/workflows/builds.yml` của `crm` vẫn truyền `--build-arg APPS_JSON_BASE64=...`, trong khi `frappe_docker@v4.0.1` đã bỏ `ARG APPS_JSON_BASE64` và chuyển sang `--mount=type=secret,id=apps_json`. Nếu sao chép công thức đó, BuildKit sẽ lặng lẽ tạo ra một image **chỉ có `frappe` mà thiếu hoàn toàn `crm`**.
   - **Biên dịch Frontend SPA & tách bạch bản vá Việt hóa Production vs Vite DEV (`ADR-UI-001`):** Trong `frappe/crm@v1.87.0`, tệp `crm/www/crm.html` không được lưu trong Git mà chỉ sinh ra khi chạy `CI=true bench build --production --force` (`vite build --base=/assets/crm/frontend/ && yarn copy-html-entry`). Trong quá trình đóng gói production image:
     - `deploy/dev/apply-frappe-vi-patch.py` phải được áp lên các template Jinja của `apps/frappe` (`navbar.html`, `doc.html`).
     - `deploy/dev/apply-crm-ui-vi-patch.py` phải được áp lên `apps/crm/frontend/node_modules/frappe-ui` (`1.0.0-beta.29`) **sau khi cài đặt frontend dependencies (`yarn install`) và trước khi chạy `bench build --production`**, đồng thời giữ nguyên cây nguồn Git của `apps/crm` sạch 100%.
     - Ngược lại, `deploy/dev/apply-crm-vite-dev-patch.py` là bản vá phục vụ riêng Vite dev server trong quy trình DEV (`deploy/dev/bootstrap.sh`) nên **không đưa vào production image**.
   - **Symlink tài sản tĩnh đứt gãy & thứ tự `sites/apps.txt`:** Hàm `make_asset_dirs()` trong `frappe/build.py` tạo symlink `apps/<app>/<app>/public/node_modules -> .../apps/<app>/node_modules`. Khi dọn dẹp `apps/crm/node_modules` để giảm dung lượng image mà không xóa symlink đứt gãy (`find -xtype l -delete`), lệnh phân quyền `chmod -R g=u` ở tầng cuối sẽ báo lỗi. Đồng thời, container `configurator` của `frappe_docker/compose.yaml` chạy `ls -1 apps > sites/apps.txt` theo thứ tự bảng chữ cái (`crm`, `crm_cskh`, `frappe`), làm đảo lộn thứ tự ưu tiên hook/override `frappe -> crm -> crm_cskh`.

---

## 2. Các Phương án Đã So sánh & Đánh đổi

| Phương án | Mô tả | Ưu điểm | Nhược điểm / Rủi ro | Kết luận |
| :--- | :--- | :--- | :--- | :--- |
| **A. Dùng `images/layered/Containerfile` gốc + tách repo riêng cho `crm_cskh`** | Kéo `frappe/base:version-15` và `frappe/build:version-15`, tách `apps/crm_cskh` sang một Git repo riêng để truyền qua BuildKit secret `apps_json`. | File build ngắn. | Phụ thuộc tag nền trôi nổi `version-15`; phá vỡ cấu trúc monorepo (`P4.md §4.4`); không thể áp các bản vá Việt hóa UI trước bước `bench build` tích hợp trong `bench init`; xóa `.git` khiến kiểm toán viên không xác minh được commit SHA lúc runtime. | **Loại bỏ** |
| **B. Fork riêng cả `frappe/frappe` và `frappe/crm` để commit cứng bản vá UI** | Tạo 2 fork nội bộ cho `frappe` và `crm`, đẩy các sửa đổi Việt hóa vào nhánh fork rồi nạp qua `apps_json`. | Không cần bước chạy script patch trong `Containerfile`. | Trái quy định `P4.md §18.2` và `ADR-UI-001` ("không fork toàn bộ frontend CRM để sửa nhỏ"); tăng gánh nặng đồng bộ bảo mật mỗi lần nâng cấp patch `v15.x` / `v1.87.x`. | **Loại bỏ làm mặc định** *(vẫn giữ khả năng tương thích qua cấu hình nếu cần hotfix khẩn cấp)* |
| **C. Đa tầng từ `images/custom/Containerfile` (`frappe_docker@v4.0.1` ma trận v15), khóa cứng SHA-256 qua `release-lock.json`** | Kê khai [`deploy/images/Containerfile`](../../deploy/images/Containerfile) kế thừa trực tiếp cấu trúc 3 tầng (`base -> builder -> backend`) và bộ script runtime (`nginx-entrypoint.sh`, `entrypoint.sh`, `start.sh`) của `frappe_docker@2b26e9ab8d`, điều khiển hoàn toàn bằng [`deploy/images/release-lock.json`](../../deploy/images/release-lock.json) và [`deploy/images/build-image.sh`](../../deploy/images/build-image.sh). | Khóa chặt 100% digest từ OS/Python base, nhị phân tải về, Git SHA 40 ký tự, `yarn.lock` SHA-256 đến `crm_cskh` tree SHA; giữ nguyên monorepo; biên dịch sẵn toàn bộ Desk + `/crm` Vue SPA + `/cskh/*` tiếng Việt; giữ `.git` nông và `/home/frappe/frappe-bench/release-metadata` để kiểm toán độc lập bất cứ lúc nào. | Thời gian build lạnh lần đầu khoảng 6–12 phút do biên dịch đầy đủ môi trường Python/Node/Vite từ base sạch (được tối ưu bằng BuildKit layer cache trong CI `build-once`). | **Lựa chọn áp dụng** |

---

## 3. Nội dung Quyết định Kỹ thuật

### 3.1. Kiến trúc Đa tầng & Khóa Nguồn (`deploy/images/Containerfile` + `release-lock.json`)

1. **Tầng `base` (Runtime OS + Nginx + Node + Chromium + wkhtmltopdf):**
   - Xuất phát từ `python:3.11.18-slim-bookworm@sha256:6cbe849bdc876ecd413a4a09a919a0d861d908589de9eba61e886c29af793a07` (`linux/amd64` digest `sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944`).
   - Kiểm tra `sha256sum` bắt buộc trước khi thực thi/cài đặt mọi tệp tải ngoài: `nvm v0.40.6` (`2ef7e8d4...`) và `wkhtmltox_0.12.6.1-3.bookworm_amd64.deb` (`98ba0d15...`).
   - Cài đặt cố định Node `22.23.3` (symlink tại `/home/frappe/.nvm/current/bin` nằm sẵn trong `ENV PATH` cho mọi container non-interactive và healthcheck), Yarn `1.22.22`, `frappe-bench==5.31.0`, `chromium-headless-shell`, `nginx`, `mariadb-client`, `wait-for-it`, `curl`, `jq`, `restic`.
   - Tiến trình mặc định chạy dưới user non-root `frappe` (`UID 1000`, `GID 0`), tương thích phân quyền OpenShift/Docker rootless của `frappe_docker@v4.0.1`.
2. **Tầng `builder` (Khóa Upstream SHA, Áp Patch UI, Biên dịch Production Assets & Xuất Lock):**
   - Khởi tạo bench với `frappe@v15.123.1`, ép `git checkout --force --detach d2efa79657ba5c1abf96098b3bfacc199517d57e`, đối chiếu `test "$(git rev-parse HEAD)" = "${FRAPPE_SHA}"` và kiểm tra SHA-256 của `apps/frappe/yarn.lock` (`ec8d4dfd...`).
   - Kéo `crm@v1.87.0`, ép `git checkout --force --detach 0d3910b456ef4fb913e77b29db011b3debc4f778`, đối chiếu `HEAD` và kiểm tra SHA-256 của cả `apps/crm/yarn.lock` (`49e1cb52...`) lẫn `apps/crm/frontend/yarn.lock` (`b0d89f3b...`).
   - Cài đặt phụ thuộc JS bằng `yarn install --frozen-lockfile --check-files` (cấm tự động sửa đổi lockfile).
   - Nạp `apps/crm_cskh` từ thư mục con của monorepo qua ngữ cảnh build đã lọc chặt bởi [`Containerfile.dockerignore`](../../deploy/images/Containerfile.dockerignore) (loại bỏ hoàn toàn `.git`, `.env*`, `apply-crm-vite-dev-patch.py`, `deploy/dev/evidence`, khóa bí mật và tệp tạm).
   - Thực thi `apply-frappe-vi-patch.py` trên Jinja của `apps/frappe` và `apply-crm-ui-vi-patch.py` trên `apps/crm/frontend` (sau `yarn install`, trước `bench build`), kiểm tra `git -C apps/crm status --porcelain` rỗng ngay sau khi vá `frappe-ui` trong `node_modules` (khi `bench build --production --force` chạy, chỉ có tệp sinh tự động `frontend/auto-imports.d.ts` của Vite được cập nhật), cài đặt `./env/bin/pip install --no-cache-dir -e ./apps/crm_cskh`, và biên dịch toàn bộ tài sản tĩnh bằng `CI=true bench build --production --force`.
   - Trích xuất bộ hồ sơ khóa phụ thuộc bất biến vào `/home/frappe/frappe-bench/release-metadata/`:
     - `build-manifest.json` (provenance toàn diện của image, SHAs, phiên bản công cụ và mã băm các tệp khóa)
     - `pip-freeze.txt` (danh sách toàn bộ gói Python thực tế trong virtualenv)
     - `yarn-locks.sha256` (mã băm 3 tệp `yarn.lock`)
     - `dpkg-packages.txt` (danh sách gói hệ điều hành Debian Bookworm)
     - `upstream-patches.diff` (`git diff` chính xác của các bản vá Việt hóa trên `apps/frappe` và `apps/crm`)
     - `crm-cskh-files.sha256` (mã băm SHA-256 từng tệp nguồn của `apps/crm_cskh`)
   - Dọn dẹp ~600 MB `apps/crm/{,frontend/}node_modules` chỉ dùng lúc build nhưng **giữ nguyên** `apps/frappe/node_modules` (cần cho tiến trình `websocket` chạy `apps/frappe/socketio.js`) và **giữ nguyên** `.git` nông (~42 MB tổng cộng) của `apps/frappe` + `apps/crm` để phục vụ kiểm toán runtime.
3. **Tầng `backend` (Artifact Thực thi Bất biến cho cả 8 Service Ứng dụng):**
   - Tách `/home/frappe/frappe-bench/sites/assets` sang `/home/frappe/frappe-bench/assets` (nằm ngoài điểm gắn volume `sites`), đồng thời kiểm tra `test -z "$(find /home/frappe/frappe-bench -xtype l)"` bảo đảm không còn bất kỳ symlink đứt gãy nào trong image.
   - Khi khởi động container, [`main-entrypoint.sh`](../../deploy/images/resources/main-entrypoint.sh) thực hiện hai chốt chặn idempotent trên volume `sites`:
     1. Liên kết nguyên tử `ln -sfn /home/frappe/frappe-bench/assets /home/frappe/frappe-bench/sites/assets` để mọi bản cập nhật image mới tự động phơi bày đúng bộ assets mới cho `frontend` (Nginx) và `backend` (Gunicorn) mà không bị kẹt bản cũ trong volume.
     2. Khôi phục `sites/apps.txt` từ `/home/frappe/frappe-bench/apps-order.txt` (`frappe` → `crm` → `crm_cskh`) ngay cả sau khi container `configurator` của `frappe_docker` vừa chạy `ls -1 apps > sites/apps.txt`.

### 3.2. Quy tắc Định danh Phiên bản `crm_cskh` & Tương thích Fork Tùy chọn

- Vì `apps/crm_cskh/crm_cskh/__init__.py` đang khai báo tĩnh `__version__ = "0.0.1"` từ P0–P3 (không sửa `apps/` trong P4-01 để bảo toàn chữ ký cây mã nguồn nghiệp vụ), mọi công cụ phát hành và kiểm toán **cấm dùng chuỗi `"0.0.1"` làm định danh bản phát hành**. Bản phát hành chỉ được định danh duy nhất bằng bộ ba bất biến:
  1. `source_commit_sha` + `crm_cskh_tree_sha` (`da6373a01ad963301877f69c413fdda9a361f718` trên baseline `92e14af`),
  2. `OCI image @sha256:<64-hex>` digest,
  3. Nhãn OCI (`io.cskh.*`) và `/home/frappe/frappe-bench/release-metadata/build-manifest.json` đóng sẵn trong image.
- **Tương thích fork tùy chọn (Optional Fork Compatibility):** Toàn bộ URL kho nguồn (`apps.frappe.repository`, `apps.crm.repository`), tag, commit SHA 40 ký tự và SHA-256 của `yarn.lock` đều được tham số hóa trong [`deploy/images/release-lock.json`](../../deploy/images/release-lock.json). Nếu tương lai phát sinh lỗ hổng bảo mật khẩn cấp trên upstream chưa kịp phát hành bản vá chính thức, kiến trúc sư trưởng (`T0`) chỉ cần duyệt ADR cập nhật URL sang kho fork nội bộ và ghim SHA mới trong `release-lock.json` mà không phải viết lại `Containerfile` hay pipeline CI/CD.

### 3.3. Hợp đồng Biến Compose Thống nhất với `T1A` (`P4-ADR-01`)

Image duy nhất này phục vụ đồng thời cả 8 service ứng dụng trong bộ Compose của `T1A` (`configurator`, `backend`, `frontend`, `websocket`, `queue-short`, `queue-long`, `scheduler`, và `migrator`) thông qua biến:
- **`APP_IMAGE`** (khai báo bắt buộc tại `deploy/compose/overrides/compose.cskh-common.yaml`):
  - Trên **Staging / Production** (sau khi có registry): `APP_IMAGE=<registry>/<namespace>/crm-cskh@sha256:<64-hex>` kèm `PULL_POLICY=always`.
  - Trên **Isolated Local Smoke** (khi kiểm thử cục bộ chưa đẩy registry): `APP_IMAGE=sha256:<local_image_config_id>` (hoặc tag cục bộ đã nạp từ `docker buildx build --load`) kèm `PULL_POLICY=never` hoặc `missing`.
- Biến giả tương thích `ERPNEXT_VERSION=unused-overridden-by-app-image` được giữ trong `.env` để triệt tiêu cảnh báo nội suy của tệp gốc `frappe_docker/compose.yaml` mà không ảnh hưởng tới image thực thi.

---

## 4. Tác động Lên Nâng cấp Upstream

Khi cần nâng cấp `frappe` (ví dụ lên bản vá bảo mật `v15.x` mới), `crm`, `frappe_docker`, hoặc `python` base image:
1. Chỉ chỉnh sửa các trường tương ứng (`commit_sha`, `tag`, `yarn_lock_sha256`, hoặc `base_image.pinned_ref`) trong [`deploy/images/release-lock.json`](../../deploy/images/release-lock.json) trên nhánh PR riêng.
2. Chạy [`deploy/images/build-image.sh`](../../deploy/images/build-image.sh): nếu bản vá Việt hóa (`apply-*-patch.py`) không còn khớp ngữ cảnh mã nguồn mới, script patch sẽ dừng lỗi tường minh ngay ở tầng `builder` (fail-closed), buộc kỹ sư rà soát lại diff trong `upstream-patches.diff` thay vì âm thầm tạo ra giao diện lỗi.
3. Chạy [`deploy/images/verify_clean_site.py`](../../deploy/images/verify_clean_site.py) và toàn bộ cổng hồi quy P0–P3 trên Staging trước khi xin phê duyệt lên Production.

---

## 5. Quy trình Đảo Quyết định / Rollback Khi Có Sự cố

1. **Sự cố ở bản phát hành mới `N` (Class A — chỉ đổi code/image, tương thích schema):**
   - Cập nhật `APP_IMAGE` trong tệp `.env` của môi trường về đúng `@sha256:<64-hex>` digest của bản `N-1` đã lưu trong `release-manifest.json` liền trước.
   - Chạy lại `docker compose ... up -d` (không bật profile `migrate`). Khi các container khởi động lại, `entrypoint.sh` tự động trỏ nguyên tử `sites/assets` về bộ tài sản tĩnh của `N-1` đóng sẵn trong image `N-1`.
2. **Sự cố sau khi đã chạy schema migration không tương thích ngược (Class B/C/D):**
   - Áp dụng cây quyết định khôi phục của `P4-05` / `P4-10`: dừng ghi dữ liệu, khôi phục đồng bộ điểm sao lưu pre-release (`DB + public/private files + site_config.json`) và ghim lại `APP_IMAGE` về digest `N-1`.
3. **Đảo quyết định kiến trúc build (nếu cần chuyển sang kho fork hoặc base image nội bộ):**
   - Thay giá trị `base_image.pinned_ref` hoặc `apps.{frappe,crm}.repository` trong `release-lock.json`, giữ nguyên giao diện đầu ra `APP_IMAGE@sha256` để toàn bộ tầng Compose (`P4-ADR-01`) và pipeline triển khai (`P4-02`) không phải thay đổi cấu trúc.
