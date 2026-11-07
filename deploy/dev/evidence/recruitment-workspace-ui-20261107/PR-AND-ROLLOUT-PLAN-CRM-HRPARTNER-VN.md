# Hồ sơ chuẩn bị Pull Request & Runbook cập nhật `crm.hrpartner.vn` (`crm-staging.localhost`)

- **Nhánh nguồn:** `codex/recruitment-workspace-ui`
- **Nhánh đích:** `main` (`92e14af3532876ad5bc2468bf0e502474cc95a53`)
- **Commit chốt mã nguồn & bằng chứng 3 bên:** `11f7605f15a7455670d3d8b27ff2d1bd81d0101a` (trên merge `391b4845be504f7f094a855d1f1aaf56f3d92867`)
- **Tree SHA `apps/crm_cskh` cuối cùng:** `f51095ec245e103ed3a487654b5580636424ea36` *(AST tương đương 100% với `0248d8954b9a9314e907151f911373d48546e8b6` sau khi chạy `ruff==0.14.0 format`)*
- **Ánh xạ hạ tầng VPS hiện hành:**
  - **Tên miền công khai (HTTPS Ingress):** `crm.hrpartner.vn` (`https://crm.hrpartner.vn`)
  - **Tên site Frappe nội bộ (`SITE_NAME` / `sites/<site>`):** `crm-staging.localhost`
  - **Compose wrapper vận hành trên VPS:** `/opt/crm-cskh-staging/staging-compose`
  - **Tệp cấu hình môi trường của wrapper:** `/opt/crm-cskh-staging/.env.staging` (giữ quyền truy cập **`0600`**)
- **Phân loại thay đổi vận hành:** **Class A+ — Zero-DDL, Fixture & Catalog Seed Update** (`0` thay đổi cấu trúc bảng/cột/index, `patches.txt` giữ nguyên; có cập nhật `CRM Form Script` và nhãn mặc định `version == 1` qua `migrate`)

---

## Phần 1 — Chuẩn bị Pull Request (`codex/recruitment-workspace-ui` → `main`)

### 1.1. Tiêu đề PR đề xuất

```text
feat(ui,i18n,p3): đồng bộ Không gian tuyển dụng 6 bước, chỉ số P3 và bộ kiểm thử độc lập
```

### 1.2. Chuỗi commit tích hợp trên nhánh (đã kiểm chứng bằng `git rev-parse`)

| Thứ tự | Commit SHA đầy đủ (`git rev-parse`) | Vai trò | Nội dung chính |
| :-: | :--- | :---: | :--- |
| 1 | `8549a62180343ae48511c9b0910c8c529ee65a97` | `T1A` | Việt hóa luồng cơ sở Không gian tuyển dụng, lối vào `/crm` ↔ `/cskh`, giữ nhãn **Giao dịch CRM** cho `CRM Deal`, tài liệu tách bạch `GAP-01..03` |
| 2 | `b36b6c402c4b83167fe83bf95e37ca47cb0fea1a` | `T1A` | Đồng bộ điều hướng 6 bước tuyển dụng, bộ từ điển `vi.csv` (`1.331` dòng, bảo toàn khối P3), cập nhật assertion E2E P1/P2 & ghi chú mẫu lương/ca làm việc (bảo toàn payload chống XSS) |
| 3 | `32b53fb51a87b85b7e04da4fbd1c62259affe616` | `T1B` | Đồng bộ nhãn tiếng Việt danh mục kết quả liên hệ P2, `label_vi` của `29` chỉ số + `5` điểm thành phần P3 và ánh xạ tương thích snapshot cũ |
| 4 | `6912c78c1c74483c1a0063379d94d8c9b8bdb30b` | `T1A` | Merge tích hợp giao diện `b36b6c402c4b83167fe83bf95e37ca47cb0fea1a` + backend `32b53fb51a87b85b7e04da4fbd1c62259affe616` |
| 5 | `14960ebd0031a8170e35a1847a01aabdb8b10815` | `T1A` | Đóng gói bộ kiểm thử Việt hóa tái lập (`12/12` suite, `48/48` JUnit methods, `17` ảnh Chromium E2E, `SHA256SUMS.txt`) |
| 6 | `6a751286e7c4b699bebe3fa9d2c685879d94e326` | `T1C` | Kiểm định độc lập đa vai trò (`Admin` / `Staff` / `BOD`), đối chứng `migrate × 2` bất biến lịch sử & snapshot KPI |
| 7 | `2556090767fe820c29e4f05d1808702bf31ee19d` | `T1C` | Kiểm định tải CSV qua HTTP xác thực của `BOD` (`EXP-BE8FDEFBDD98`, `29/29` chỉ số, `0` PII) và `4/4` cổng chặn `HTTP 403` |
| 8 | `a973e3a69530edb9441480c3b2862b381656b36b` + `8184c8050868da74fc0e30edff0c7dc390c19c2b` | `T1B` | Cô lập fixture `test_p3_reporting_and_security` với cuộc gọi tổng đài PBX hợp lệ `SEC2-02` + bằng chứng chạy độc lập đạt **`7/7 PASS`** trên site sạch & hồi quy **`19/19 PASS`** |
| 9 | `391b4845be504f7f094a855d1f1aaf56f3d92867` | `T1A` | Merge tích hợp nhánh bằng chứng `8184c8050868da74fc0e30edff0c7dc390c19c2b` (`T1B`) cùng kết quả HTTP CSV `2556090767fe820c29e4f05d1808702bf31ee19d` (`T1C`) |
| 10 | `11f7605f15a7455670d3d8b27ff2d1bd81d0101a` | `T1A` | Dọn khoảng trắng cuối dòng 2 script kiểm định `T1C` (giữ nguyên từng byte CSV HTTP `92bb90d5be2b...`), cập nhật manifest SHA-256 và hồ sơ bàn giao |
| 11 | `6d5cb74afe2ca5556f377ab98dda66ebdcd37eb9` | `T1A` | Hiệu chỉnh runbook cập nhật `crm.hrpartner.vn` theo hạ tầng VPS hiện hành (`/opt/crm-cskh-staging/staging-compose`, site `crm-staging.localhost`, kiểm tra xả RQ worker thực tế & phân tách rollback) |
| 12 | `c88af347a4e0d88a13cb695d8352222643f315c9` | `T1A` | Hiệu chỉnh đường dẫn cấu hình `/opt/crm-cskh-staging/.env.staging` (giữ quyền `0600`), đối chiếu toàn bộ lệnh với wrapper `staging-compose` và đồng bộ manifest SHA-256 |
| 13 | `HEAD` *(trên `c88af34`)* | `T1A` | Chuẩn hóa định dạng `ruff==0.14.0 format` cho `p1_translations.py` & `p3_models.py` (`ast.dump` bất biến 100%) theo cổng lint CI `P0..P3` |

### 1.3. Đối chiếu phạm vi mã nguồn

- **So với baseline `origin/main` (`92e14af3532876ad5bc2468bf0e502474cc95a53`) không tính `deploy/dev/evidence/`:** **`46` tệp (`+1.181 / -555` dòng)**, **`0` thay đổi DDL/schema**, `apps/crm_cskh/crm_cskh/patches.txt` không đổi.
- **So với SHA tích hợp UI+Backend `6912c78c1c74483c1a0063379d94d8c9b8bdb30b`:** **`0` tệp / `0` dòng thay đổi trên mã nguồn runtime sản phẩm**; chỉ có `+47 / -22` dòng trên 2 tệp test (`apps/crm_cskh/crm_cskh/tests/p2_base.py` và `apps/crm_cskh/crm_cskh/tests/test_p3_reporting_and_security.py`) từ `a973e3a69530edb9441480c3b2862b381656b36b`.

### 1.4. Nội dung mô tả PR (sẵn sàng dán khi mở PR)

```markdown
## Mục tiêu
Đồng bộ thuật ngữ và luồng thao tác tiếng Việt của **Không gian tuyển dụng** (`/cskh` và lối vào từ `/crm`) theo thực tế vận hành tiếp nhận hồ sơ, liên hệ và hẹn gọi lại của HR Partner, đồng thời giữ nguyên chức năng gốc của **Giao dịch CRM** (`CRM Deal`) và bảo toàn toàn bộ kiến trúc DocType, dữ liệu lịch sử, phân quyền và công thức KPI P1–P3.

## Phạm vi thay đổi chính (`46` tệp code/test/tài liệu từ `92e14af3532876ad5bc2468bf0e502474cc95a53`, `0` DDL)
1. **Lối vào `/crm` ↔ `/cskh` & luồng 6 bước Không gian tuyển dụng:**
   - Lối vào trên danh sách/chi tiết `CRM Lead` (**Hồ sơ liên hệ**) và danh sách `CRM Deal` (**Giao dịch CRM**) qua `fixtures/crm_form_script.json`.
   - Đồng bộ 6 bước trên `/cskh`: **Nhập danh sách liên hệ** (`/cskh/import`) → **Kho hồ sơ liên hệ** (`/cskh/pool`) → **Phân công** (`/cskh/campaigns`) → **Hồ sơ được giao** (`/cskh/my-customers`) → **Ghi nhận liên hệ** (`/cskh/work-item`) → **Lịch hẹn liên hệ lại** (`/cskh/callbacks`).
2. **Bảo toàn ngữ nghĩa nghiệp vụ trung thực:**
   - `CRM Deal` giữ nguyên chức năng thương mại gốc dưới nhãn **Giao dịch CRM** (**Chuyển thành Giao dịch CRM**).
   - Kết quả `converted` hiển thị nhất quán là **Hoàn tất mục tiêu liên hệ chiến dịch** kèm hộp lưu ý trên biểu mẫu ghi nhận liên hệ (không gây hiểu nhầm là đã trúng tuyển hoặc đã bố trí nhân sự).
   - Tách bạch 3 khoảng trống nghiệp vụ cần thiết kế riêng (`GAP-01..03`) tại `docs/recruitment-staffing-domain-alignment.md`.
3. **Đồng bộ 3 chiều báo cáo P3 & tương thích ngược dữ liệu cũ:**
   - Khớp 100% `29/29` chỉ số và `5/5` điểm thành phần giữa thẻ giao diện, bảng chi tiết, modal truy vết và tệp CSV xuất ra.
   - Chiếu nhãn mới trong bộ nhớ qua `unpack_snapshot_metrics()` cho `CSKH KPI Snapshot` cũ mà không sửa `metrics_json` lịch sử; chỉ cập nhật nhãn mặc định cho `CSKH Outcome Code` còn ở `version == 1`, bảo toàn các mã Quản trị đã tùy chỉnh (`version > 1`) và `outcome_label_snapshot` lịch sử.
4. **Cô lập hoàn toàn kiểm thử bảo mật P3 (`a973e3a69530edb9441480c3b2862b381656b36b`):**
   - `crm_cskh.tests.test_p3_reporting_and_security` tự nạp cuộc gọi tổng đài PBX hợp lệ `SEC2-02` và dọn sạch bảng runtime trong `setUp()`, đạt **`7/7 PASS` độc lập** trên site sạch và **`19/19 PASS`** hồi quy liên module P2+P3.

## Kết quả kiểm định 3 bên (`T1A` · `T1B` · `T1C`)
- **Cổng tĩnh & hợp đồng (`T1A`):** `vi.csv` `1.331` dòng (`0` lỗi cú pháp, `missing=0`, `conflict=0`, `orphan=0`); đối chiếu `29/29` chỉ số P3 `mismatches=0`; hợp đồng UI & múi giờ P2/P3 PASS (`deploy/dev/evidence/recruitment-workspace-ui-20261107/`).
- **Frappe Unit & Bảo mật độc lập (`T1A` + `T1B`):** `48/48` unit test methods PASS qua `ci_junit_gate.py`; `test_p3_reporting_and_security` chạy **độc lập trên site sạch đạt `7/7 PASS`** (`0` fail/error/skip) và hồi quy P2+P3 đạt **`19/19 PASS`** (`deploy/dev/evidence/p3-reporting-test-isolation-20261107/`).
- **Trình duyệt đa vai trò & Tải CSV qua HTTP xác thực (`T1A` + `T1C`):**
  - Chromium `153.0.8010.12` E2E trên **Quản trị CRM**, **Nhân viên** và **BOD** (`17` ảnh của `T1A` + `12` ảnh của `T1C`, `0` lỗi console/pageerror/HTTP 5xx, khử XSS an toàn).
  - `migrate × 2` lũy đẳng, bảo toàn mã tùy chỉnh (`version > 1`), `outcome_label_snapshot` và `metrics_json` thô.
  - Phiên HTTP xác thực của `BOD` tải `GET /api/method/crm_cskh.api.p3_reporting.download_export` đạt `HTTP 200 OK` (`EXP-BE8FDEFBDD98.csv`, `3.018` bytes, SHA-256 `92bb90d5be2b924edd36851c381d130345a9cb0c1bbb59509bd7a2c7275b75c0`, `29/29` chỉ số tiếng Việt, `0` PII) và chặn đúng **`4/4` kịch bản trái phép với `HTTP 403`** (`deploy/dev/evidence/t1c-recruitment-multirole-audit-20261107/`).
```

---

## Phần 2 — Runbook cập nhật `crm.hrpartner.vn` theo hạ tầng VPS hiện hành

### 2.1. Thông số định danh hạ tầng & quy ước biến bắt buộc

Trên VPS hiện hành, lưu lượng HTTPS công khai đi vào tên miền **`crm.hrpartner.vn`**, thư mục site Frappe thực tế trong volume `sites` là **`crm-staging.localhost`**, và toàn bộ lệnh Docker Compose được gọi qua wrapper **`/opt/crm-cskh-staging/staging-compose`** gắn với tệp cấu hình **`/opt/crm-cskh-staging/.env.staging`** (quyền **`0600`**).

> [!IMPORTANT]
> - **Giữ nguyên cấu hình domain/routing & quyền tệp `0600`:** Bảo toàn toàn bộ thông số hiện hành trong `/opt/crm-cskh-staging/.env.staging` (`SITES_RULE` / proxy TLS cho `crm.hrpartner.vn`, `SITE_NAME=crm-staging.localhost`, `MIGRATE_SITES=false`, `DB_PASSWORD_SECRETS_FILE=...`). Khi nâng cấp hoặc quay lui chỉ cập nhật duy nhất dòng **`APP_IMAGE=`**, đồng thời giữ nguyên quyền truy cập **`0600`** cho cả `/opt/crm-cskh-staging/.env.staging` lẫn bản sao lưu của nó.
> - **Ngữ cảnh thực thi wrapper `staging-compose`:** Vì `/opt/crm-cskh-staging/.env.staging` và tệp secret MariaDB đều ở quyền `0600`, phiên bảo trì cần mở dưới quyền chủ sở hữu tệp (thông thường `sudo -i` / `root`) để `staging-compose` đọc trực tiếp `--env-file /opt/crm-cskh-staging/.env.staging` và Docker socket mà không bị `sudo` lọc biến môi trường. Khi chạy `migrator`, luôn truyền kèm `-e MIGRATE_SITES=true -e SITE_NAME="${FRAPPE_SITE}"` trực tiếp cho `run --rm` để biến vào thẳng container `migrator` kể cả khi wrapper nạp `MIGRATE_SITES=false` từ `.env.staging`.
> - **Mọi lệnh `bench --site ...`, Python `frappe.init(...)` và đường dẫn dữ liệu/backup bên trong container** đều phải dùng tên site Frappe đã xác minh thực tế (`FRAPPE_SITE="crm-staging.localhost"`), **không** truyền `crm.hrpartner.vn` vào `--site`.

Bảng tham chiếu biến dùng xuyên suốt runbook:

| Biến vận hành | Giá trị / Cách xác định trên VPS |
| :--- | :--- |
| `PUBLIC_DOMAIN` | `crm.hrpartner.vn` (kiểm tra ngoài HTTPS: `https://crm.hrpartner.vn`) |
| `COMPOSE_BIN` | `/opt/crm-cskh-staging/staging-compose` |
| `ENV_FILE` | `/opt/crm-cskh-staging/.env.staging` *(bắt buộc giữ quyền truy cập **`0600`**)* |
| `FRAPPE_SITE` | `crm-staging.localhost` *(bắt buộc kiểm chứng khớp `SITE_NAME` trong `/opt/crm-cskh-staging/.env.staging` và tồn tại `sites/crm-staging.localhost/site_config.json` ở Bước 0)* |
| `PREV_APP_IMAGE` | Giá trị `APP_IMAGE` đang cấu hình trước khi nâng cấp *(ghi lại nguyên văn từ `/opt/crm-cskh-staging/.env.staging` & đối chiếu `docker inspect`)* |
| `PREV_IMAGE_ID` | Image ID thực chạy của `backend` hiện tại (`sha256:<64-hex>`, ví dụ bản `92e14af` local là `sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` — bắt buộc lấy thực tế trên VPS ở Bước 0) |
| `NEW_APP_IMAGE` | Định danh `APP_IMAGE` bất biến mới của bản phát hành (`<registry>/<namespace>/crm-cskh@sha256:<NEW_REPO_DIGEST>` hoặc image đã nạp cố định trên VPS) |
| `NEW_IMAGE_ID` | Image ID thực tế của `NEW_APP_IMAGE` (`docker image inspect --format '{{.Id}}' "${NEW_APP_IMAGE}"`, dạng `sha256:<64-hex>`) |

---

### 2.2. Đóng gói Image bất biến mới trước giờ cắt chuyển

Sau khi `T0` merge PR vào `main` (tạo `<MAIN_MERGE_SHA>`, giữ `apps/crm_cskh` tree `f51095ec245e103ed3a487654b5580636424ea36`):

1. Build image `linux/amd64` theo đúng quy trình `Containerfile` / `build-image.sh`:
   - Thứ tự ứng dụng trong `/home/frappe/frappe-bench/apps-order.txt`: `frappe` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`) → `crm` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) → `crm_cskh` (`<MAIN_MERGE_SHA>`).
   - Chạy `python deploy/dev/apply-frappe-vi-patch.py` và `python deploy/dev/apply-crm-ui-vi-patch.py` trước khi build tài sản (**tuyệt đối không chạy** `apply-crm-vite-dev-patch.py`).
   - Chạy `CI=true bench build --production --force` để biên dịch tĩnh bundle `/crm` Vue SPA, `/assets/crm_cskh/cskh/*` và nạp `translations/vi.csv` (`1.331` dòng).
2. Phát hành image lên registry (hoặc chuyển gói tar đã kiểm SHA-256 sang VPS) và ghi nhận rõ cả **`NEW_APP_IMAGE`** lẫn **`NEW_IMAGE_ID`**.

---

### 2.3. Bước 0 — Tiền kiểm chỉ đọc trên VPS (`T-1` / Trước giờ bảo trì)

Mở phiên quản trị (`sudo -i`) trên VPS (chưa tác động dịch vụ đang chạy) để khóa thông tin thực tế của wrapper và tệp cấu hình `/opt/crm-cskh-staging/.env.staging`:

```bash
export COMPOSE_BIN="/opt/crm-cskh-staging/staging-compose"
export ENV_FILE="/opt/crm-cskh-staging/.env.staging"
export PUBLIC_DOMAIN="crm.hrpartner.vn"
export FRAPPE_SITE="crm-staging.localhost"

# 1. Xác minh tệp cấu hình /opt/crm-cskh-staging/.env.staging tồn tại, giữ đúng quyền 0600,
#    khớp SITE_NAME=crm-staging.localhost và wrapper staging-compose giải mã sạch cấu hình + profile migrate:
sudo test -f "${ENV_FILE}"
test "$(sudo stat -c '%a' "${ENV_FILE}")" = "600"
sudo grep -E '^(COMPOSE_PROJECT_NAME|SITE_NAME|APP_IMAGE|PULL_POLICY|MIGRATE_SITES|SITES_RULE|DB_PASSWORD_SECRETS_FILE)=' "${ENV_FILE}"
test "$(sudo grep '^SITE_NAME=' "${ENV_FILE}" | cut -d= -f2- | tr -d "\"'\r")" = "${FRAPPE_SITE}"
"${COMPOSE_BIN}" config --services
"${COMPOSE_BIN}" --profile migrate config --services | grep -qx migrator
"${COMPOSE_BIN}" ps

# 2. Xác minh thư mục site Frappe thực tế trong container backend khớp crm-staging.localhost:
"${COMPOSE_BIN}" exec -T backend bash -lc '
  set -eu
  ls -1 /home/frappe/frappe-bench/sites/*/site_config.json
  test -f "/home/frappe/frappe-bench/sites/'"${FRAPPE_SITE}"'/site_config.json"
  echo "VERIFIED_FRAPPE_SITE='"${FRAPPE_SITE}"'"
'

# 3. Ghi lại PREV_APP_IMAGE (từ .env.staging & container backend), PREV_IMAGE_ID và RepoDigests thực chạy:
PREV_ENV_APP_IMAGE="$(sudo grep '^APP_IMAGE=' "${ENV_FILE}" | cut -d= -f2- | tr -d "\"'\r")"
PREV_BACKEND_CID="$("${COMPOSE_BIN}" ps -q backend)"
export PREV_APP_IMAGE="$(docker inspect --format '{{.Config.Image}}' "${PREV_BACKEND_CID}")"
export PREV_IMAGE_ID="$(docker inspect --format '{{.Image}}' "${PREV_BACKEND_CID}")"
PREV_REPO_DIGESTS="$(docker image inspect --format '{{json .RepoDigests}}' "${PREV_IMAGE_ID}")"
install -m 0600 /dev/null /tmp/cskh-pre-upgrade-image.txt
printf 'PREV_ENV_APP_IMAGE=%s\nPREV_APP_IMAGE=%s\nPREV_IMAGE_ID=%s\nPREV_REPO_DIGESTS=%s\n' \
  "${PREV_ENV_APP_IMAGE}" "${PREV_APP_IMAGE}" "${PREV_IMAGE_ID}" "${PREV_REPO_DIGESTS}" | tee /tmp/cskh-pre-upgrade-image.txt

# 4. Kiểm tra dung lượng đĩa trống (yêu cầu >= 5 GB trống) và xác nhận encryption_key đã lưu ký ngoài VPS:
df -h / /var/lib/docker /opt/crm-cskh-staging
"${COMPOSE_BIN}" exec -T backend /home/frappe/frappe-bench/env/bin/python -c "
import json, pathlib
cfg = json.loads(pathlib.Path('/home/frappe/frappe-bench/sites/${FRAPPE_SITE}/site_config.json').read_text())
assert cfg.get('encryption_key'), 'MISSING encryption_key!'
print('encryption_key present (length =', len(cfg['encryption_key']), ') — confirm matches offsite escrow before proceeding')
"

# 5. Kéo/nạp sẵn NEW_APP_IMAGE trước khi bật bảo trì và ghi nhận NEW_IMAGE_ID:
export NEW_APP_IMAGE="<REGISTRY_OR_REPO>/crm-cskh@sha256:<NEW_REPO_DIGEST>"
docker pull "${NEW_APP_IMAGE}"
export NEW_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${NEW_APP_IMAGE}")"
printf 'NEW_APP_IMAGE=%s\nNEW_IMAGE_ID=%s\n' "${NEW_APP_IMAGE}" "${NEW_IMAGE_ID}" | tee -a /tmp/cskh-pre-upgrade-image.txt
```

---

### 2.4. Quy trình cập nhật trong khung bảo trì (`~10–15` phút)

> [!CAUTION]
> **QUY TẮC DỪNG KHẨN CẤP (`STOP RULE`):** Nếu kiểm tra xả worker RQ quá hạn vẫn còn job đang chạy, hoặc bước `backup --with-files` thất bại / không khớp SHA-256, hoặc `migrator` trả mã thoát `!= 0`, **DỪNG NGAY LẬP TỨC**, không chạy tiếp các bước sau và chuyển sang Mục 2.5 để mở lại an toàn hoặc quay lui có kiểm soát.

#### Bước 1 — Bật chế độ bảo trì, tạm dừng scheduler và kiểm tra chắc chắn toàn bộ job RQ đang chạy đã kết thúc

```bash
# 1. Khóa nhận tác vụ mới trên site thực tế crm-staging.localhost:
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" set-maintenance-mode on
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" scheduler pause

# 2. Kiểm tra thực tế hàng đợi RQ (short, default, long) và trạng thái tất cả RQ workers
#    cho tới khi queued == 0, started == 0 và busy_workers == 0 (timeout fail-closed 180s):
"${COMPOSE_BIN}" exec -T backend /home/frappe/frappe-bench/env/bin/python - <<'PY'
import json, os, sys, time
import frappe
from rq import Worker
from rq.registry import StartedJobRegistry
from frappe.utils.background_jobs import get_queue

site = "crm-staging.localhost"
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    deadline = time.monotonic() + 180
    while True:
        q_objs = {qname: get_queue(qname, is_async=True) for qname in ("short", "default", "long")}
        conn = next(iter(q_objs.values())).connection
        busy_workers = [
            {"name": w.name, "state": w.get_state(), "job_id": w.get_current_job_id()}
            for w in Worker.all(connection=conn)
            if w.get_state() == "busy" or w.get_current_job_id() is not None
        ]
        queues = {}
        active_total = 0
        for qname, q in q_objs.items():
            started_ids = StartedJobRegistry(queue=q).get_job_ids()
            queued_cnt = int(q.count)
            queues[qname] = {
                "queued": queued_cnt,
                "started": len(started_ids),
                "started_job_ids": started_ids,
            }
            active_total += queued_cnt + len(started_ids)
        print(json.dumps({"queues": queues, "busy_workers": busy_workers}, ensure_ascii=False), flush=True)
        if active_total == 0 and not busy_workers:
            print("RQ_DRAIN_VERIFIED: 0 queued, 0 started, 0 busy workers.")
            break
        if time.monotonic() >= deadline:
            print("ERROR: RQ jobs still running/queued after 180s; aborting before backup/migrate.", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
finally:
    frappe.destroy()
PY

# 3. Chụp số lượng bản ghi đối chiếu trước khi backup & migrate:
"${COMPOSE_BIN}" exec -T backend /home/frappe/frappe-bench/env/bin/python - <<'PY' > /tmp/cskh-pre-migrate-counts.json
import json, os, frappe
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="crm-staging.localhost", sites_path=".")
frappe.connect()
try:
    dts = [
        "CRM Lead", "CRM Deal", "CSKH Import Batch", "CSKH Work Item",
        "CSKH Active Claim", "CSKH Assignment History", "CSKH Contact Attempt",
        "CSKH SLA Obligation", "CSKH KPI Snapshot", "CRM Form Script",
    ]
    counts = {dt: int(frappe.db.count(dt)) for dt in dts if frappe.db.exists("DocType", dt)}
    print(json.dumps(counts, indent=2, ensure_ascii=False))
finally:
    frappe.destroy()
PY
cat /tmp/cskh-pre-migrate-counts.json
```

#### Bước 2 — Sao lưu toàn phần `crm-staging.localhost` và kiểm chứng SHA-256

```bash
# Ghi mốc UTC ngay trước thời điểm backup để làm chuẩn đối soát nếu cần đánh giá cửa sổ dữ liệu:
export T_BACKUP_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "T_BACKUP_UTC=${T_BACKUP_UTC}" | tee /tmp/cskh-backup-marker.txt

# Chạy backup DB + public/private files trên site crm-staging.localhost:
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" backup --with-files

# Sao chép bộ backup mới nhất từ sites/crm-staging.localhost/private/backups/ ra thư mục lưu trữ trên host/offsite:
BACKUP_HOST_DIR="/var/backups/crm-cskh/pre-vi-rollout-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -m 0700 "${BACKUP_HOST_DIR}"
LATEST_PREFIX="$("${COMPOSE_BIN}" exec -T backend bash -lc '
  ls -1t /home/frappe/frappe-bench/sites/crm-staging.localhost/private/backups/*-crm_staging_localhost-database.sql.gz | head -n1 | sed "s/-database\.sql\.gz$//"
' | tr -d '\r')"
echo "LATEST_BACKUP_PREFIX=${LATEST_PREFIX}" | tee -a /tmp/cskh-backup-marker.txt

for suffix in database.sql.gz files.tar private-files.tar site_config_backup.json; do
  if "${COMPOSE_BIN}" exec -T backend test -f "${LATEST_PREFIX}-${suffix}"; then
    docker cp "$("${COMPOSE_BIN}" ps -q backend):${LATEST_PREFIX}-${suffix}" "${BACKUP_HOST_DIR}/"
  fi
done

# Bắt buộc có đủ database.sql.gz và kiểm tra SHA-256:
sudo bash -lc "cd '${BACKUP_HOST_DIR}' && ls -lh *-database.sql.gz && sha256sum * > SHA256SUMS.txt && sha256sum -c SHA256SUMS.txt"
# (Đồng bộ thư mục ${BACKUP_HOST_DIR} sang kho lưu trữ ngoài VPS theo quy trình backup hiện hành trước khi qua Bước 3)
```

#### Bước 3 — Sao lưu `/opt/crm-cskh-staging/.env.staging` (`0600`), cập nhật `APP_IMAGE`, chạy `configurator` và `migrator`

```bash
# 1. Sao lưu /opt/crm-cskh-staging/.env.staging với quyền 0600 trước khi đổi APP_IMAGE
#    (CHỈ cập nhật dòng APP_IMAGE=${NEW_APP_IMAGE}; giữ nguyên quyền 0600 + chủ sở hữu và toàn bộ cấu hình domain crm.hrpartner.vn & SITE_NAME=crm-staging.localhost):
ENV_BACKUP="${ENV_FILE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -m 0600 "${ENV_FILE}" "${ENV_BACKUP}"
test "$(sudo stat -c '%a' "${ENV_BACKUP}")" = "600"

sudo python3 - "${ENV_FILE}" "${NEW_APP_IMAGE}" <<'PY'
import os, pathlib, re, stat, sys
env_path = pathlib.Path(sys.argv[1])
new_img = sys.argv[2]
st = env_path.stat()
text = env_path.read_text(encoding="utf-8")
updated, n = re.subn(r"(?m)^APP_IMAGE=.*$", f"APP_IMAGE={new_img}", text)
assert n == 1, f"Expected exactly 1 APP_IMAGE= line in {env_path}, found {n}"
env_path.write_text(updated, encoding="utf-8", newline="\n")
os.chmod(env_path, 0o600)
os.chown(env_path, st.st_uid, st.st_gid)
assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
print(f"Updated {env_path} APP_IMAGE -> {new_img} (mode=0600 preserved)")
PY
test "$(sudo stat -c '%a' "${ENV_FILE}")" = "600"

# 2. Chạy configurator qua wrapper staging-compose để xác thực thứ tự sites/apps.txt (frappe -> crm -> crm_cskh):
"${COMPOSE_BIN}" run --rm configurator

# 3. Chạy one-shot migrator qua wrapper với --profile migrate và truyền tường minh -e MIGRATE_SITES=true -e SITE_NAME=${FRAPPE_SITE}
#    (bảo đảm MIGRATE_SITES=true vào thẳng container migrator kể cả khi wrapper nạp MIGRATE_SITES=false từ .env.staging;
#     TUYỆT ĐỐI KHÔNG dùng --skip-failing; kiểm tra fail-closed log đã chạy migration cho crm-staging.localhost):
(
  set -euo pipefail
  MIGRATE_SITES=true "${COMPOSE_BIN}" --profile migrate run --rm \
    -e MIGRATE_SITES=true -e SITE_NAME="${FRAPPE_SITE}" migrator 2>&1 | tee /tmp/cskh-migrator.log
  grep -Fq "running controlled migration for site ${FRAPPE_SITE}" /tmp/cskh-migrator.log
)
```

#### Bước 4 — Cập nhật các container runtime, đối chiếu `NEW_IMAGE_ID`, xóa cache và mở lưu lượng

```bash
# 1. Đưa các service ứng dụng lên image mới (giữ nguyên db, redis, proxy & cấu hình domain hiện hành):
"${COMPOSE_BIN}" up -d backend frontend websocket queue-short queue-long scheduler

# 2. Xác minh 100% các container ứng dụng đang chạy đúng NEW_IMAGE_ID:
for svc in backend frontend websocket queue-short queue-long scheduler; do
  cid="$("${COMPOSE_BIN}" ps -q "${svc}")"
  actual_iid="$(docker inspect --format '{{.Image}}' "${cid}")"
  printf '%-12s %s\n' "${svc}" "${actual_iid}"
  test "${actual_iid}" = "${NEW_IMAGE_ID}"
done

# 3. Xóa cache Redis & website cache trên site crm-staging.localhost:
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" clear-cache
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" clear-website-cache

# 4. Bật lại scheduler và tắt chế độ bảo trì trên site crm-staging.localhost:
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" scheduler resume
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" set-maintenance-mode off
```

#### Bước 5 — Kiểm tra sau cập nhật trên `crm-staging.localhost` & `https://crm.hrpartner.vn`

1. **Đối chiếu số lượng bản ghi DB nội bộ (`crm-staging.localhost`):**
   - Chạy lại đoạn đếm bản ghi ở Bước 1.3 sang `/tmp/cskh-post-migrate-counts.json` và xác nhận:
     - Các bảng dữ liệu nghiệp vụ (`CRM Lead`, `CRM Deal`, `CSKH Import Batch`, `CSKH Work Item`, `CSKH Active Claim`, `CSKH Assignment History`, `CSKH Contact Attempt`, `CSKH SLA Obligation`, `CSKH KPI Snapshot`) **bảo toàn 100% số dòng**.
     - `CRM Form Script` có đủ `3` bản ghi (`CSKH - Open CSKH Page`, `CSKH - Open Lead CSKH Workspace`, `CSKH - Open CSKH Page from Deal`).
2. **Kiểm tra giao diện HTTPS công khai trên `https://crm.hrpartner.vn`:**
   - `https://crm.hrpartner.vn/crm/leads`: hiển thị **Hồ sơ liên hệ** và nút **Không gian tuyển dụng**; chi tiết hồ sơ có **Chuyển thành Giao dịch CRM** và **Mở trong Không gian tuyển dụng**.
   - `https://crm.hrpartner.vn/crm/deals`: hiển thị **Giao dịch CRM** và nút **Không gian tuyển dụng**.
   - `https://crm.hrpartner.vn/cskh`: thanh điều hướng 6 bước (`Nhập danh sách liên hệ` → `Kho hồ sơ liên hệ` → `Phân công` → `Hồ sơ được giao` → `Ghi nhận liên hệ` → `Lịch hẹn liên hệ lại`); trên biểu mẫu **Ghi nhận liên hệ**, chọn `Hoàn tất mục tiêu liên hệ chiến dịch` hiển thị đúng hộp lưu ý ngữ nghĩa; `/cskh/settings` giữ nguyên các mã kết quả `version > 1` đã tùy chỉnh.
   - `https://crm.hrpartner.vn/cskh/quality-overview` (tài khoản `BOD` / `Quản trị CRM`): tiêu đề `<h1>` hiển thị **Tổng quan chất lượng liên hệ & SLA (BOD)**; thực hiện **Xuất CSV** và tải tệp qua HTTPS đạt `HTTP 200`, tiêu đề & `29` dòng chỉ số tiếng Việt đồng bộ giao diện, `0` cột PII, `0` lỗi console/HTTP `5xx`.

---

### 2.5. Phương án quay lại bản trước (Rollback) — Đã hiệu chỉnh theo hành vi thực tế của DB & dữ liệu phát sinh

#### Tầng 1 — Quay lại Image cũ (`APP_IMAGE` → `PREV_APP_IMAGE` / `PREV_IMAGE_ID`, không restore DB)

- **Khi nào áp dụng:** Phát hiện lỗi hiển thị giao diện/JS/tài sản tĩnh sau nâng cấp nhưng dữ liệu nghiệp vụ vẫn toàn vẹn; cần quay lui nhanh (`~3–5` phút) mà **không làm mất các giao dịch phát sinh sau thời điểm nâng cấp**.
- **Lưu ý kỹ thuật quan trọng về nhãn danh mục lưu trong DB:**
  1. Trên image cũ (`92e14af3532876ad5bc2468bf0e502474cc95a53`), các hàm `ensure_p2_seeds()` và `ensure_p3_seeds()` chỉ chèn bản ghi khi chưa tồn tại (`if not frappe.db.exists(...)`) chứ **không** ghi đè ngược bản ghi đã có. Vì vậy, khi quay lại `PREV_APP_IMAGE` (kể cả có chạy lại `migrator`), **các nhãn danh mục mặc định `version == 1` trong MariaDB đã được cập nhật ở Bước 3 sẽ vẫn giữ nhãn tiếng Việt mới** (`CSKH Outcome Code`: `Đã trao đổi — Cần liên hệ tiếp`, `Đã trao đổi — Có quan tâm`, `Đã hẹn gọi / liên hệ lại`, `Hoàn tất mục tiêu chiến dịch`; `CSKH Policy.title`; `CSKH Working Calendar.title`). Các mã hành vi (`code`, `is_connected`, `is_conversion`, công thức KPI) không đổi nên runtime của image cũ vẫn hoạt động bình thường với các nhãn này.
  2. Đối với `tabCRM Form Script`: chạy lại `migrator` trên `PREV_APP_IMAGE` sẽ đồng bộ lại 2 script trên `CRM Lead` về `__('CSKH')` / `__('Care workspace')` (khớp với `vi.csv` của image cũ), nhưng Frappe `sync_fixtures` không tự xóa bản ghi thứ 3 `CSKH - Open CSKH Page from Deal` mới thêm. Để tránh nút thứ 3 trên `/crm/deals` hiển thị chuỗi tiếng Anh chưa có trong `vi.csv` cũ, cần tắt `enabled = 0` cho `CSKH - Open CSKH Page from Deal`.

```bash
export COMPOSE_BIN="/opt/crm-cskh-staging/staging-compose"
export ENV_FILE="/opt/crm-cskh-staging/.env.staging"
export FRAPPE_SITE="crm-staging.localhost"

# 1. Bật chế độ bảo trì ngắn & tạm dừng scheduler, sau đó chạy lại đoạn kiểm tra RQ ở Bước 1.2
#    cho tới khi in ra "RQ_DRAIN_VERIFIED: 0 queued, 0 started, 0 busy workers.":
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" set-maintenance-mode on
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" scheduler pause

# 2. Đổi APP_IMAGE trong /opt/crm-cskh-staging/.env.staging về lại PREV_APP_IMAGE đã ghi ở Bước 0 (giữ nguyên quyền 0600):
sudo python3 - "${ENV_FILE}" "${PREV_APP_IMAGE}" <<'PY'
import os, pathlib, re, stat, sys
env_path = pathlib.Path(sys.argv[1])
prev_img = sys.argv[2]
st = env_path.stat()
text = env_path.read_text(encoding="utf-8")
updated, n = re.subn(r"(?m)^APP_IMAGE=.*$", f"APP_IMAGE={prev_img}", text)
assert n == 1, f"Expected 1 APP_IMAGE= line in {env_path}, found {n}"
env_path.write_text(updated, encoding="utf-8", newline="\n")
os.chmod(env_path, 0o600)
os.chown(env_path, st.st_uid, st.st_gid)
assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
print(f"Reverted {env_path} APP_IMAGE -> {prev_img} (mode=0600 preserved)")
PY
test "$(sudo stat -c '%a' "${ENV_FILE}")" = "600"

# 3. Chạy configurator + migrator trên image cũ qua wrapper staging-compose để khôi phục 2 CRM Form Script của CRM Lead,
#    sau đó đưa các service về PREV_IMAGE_ID và tắt script thứ 3 trên CRM Deal:
"${COMPOSE_BIN}" run --rm configurator
(
  set -euo pipefail
  MIGRATE_SITES=true "${COMPOSE_BIN}" --profile migrate run --rm \
    -e MIGRATE_SITES=true -e SITE_NAME="${FRAPPE_SITE}" migrator 2>&1 | tee /tmp/cskh-rollback-migrator.log
  grep -Fq "running controlled migration for site ${FRAPPE_SITE}" /tmp/cskh-rollback-migrator.log
)
"${COMPOSE_BIN}" up -d backend frontend websocket queue-short queue-long scheduler

for svc in backend frontend websocket queue-short queue-long scheduler; do
  actual_iid="$(docker inspect --format '{{.Image}}' "$("${COMPOSE_BIN}" ps -q "${svc}")")"
  test "${actual_iid}" = "${PREV_IMAGE_ID}"
done

"${COMPOSE_BIN}" exec -T backend /home/frappe/frappe-bench/env/bin/python -c "
import os, frappe
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site='${FRAPPE_SITE}', sites_path='.')
frappe.connect()
try:
    if frappe.db.exists('CRM Form Script', 'CSKH - Open CSKH Page from Deal'):
        frappe.db.set_value('CRM Form Script', 'CSKH - Open CSKH Page from Deal', 'enabled', 0, update_modified=False)
        frappe.db.commit()
finally:
    frappe.destroy()
"

# 4. Xóa cache, bật lại scheduler và mở lại lưu lượng:
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" clear-cache
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" clear-website-cache
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" scheduler resume
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" set-maintenance-mode off
```

#### Tầng 2 — Khôi phục toàn phần DB & Files từ bản Backup (`bench --site crm-staging.localhost --force restore`)

> [!WARNING]
> **TÁC ĐỘNG MẤT DỮ LIỆU PHÁT SINH SAU BACKUP (`DATA-LOSS IMPACT`):**
> - Lệnh `bench --site crm-staging.localhost --force restore` **ghi đè toàn bộ cơ sở dữ liệu MariaDB và cây tệp `public/files` + `private/files` quay về đúng thời điểm `T_BACKUP_UTC` ở Bước 2**.
> - **Mọi dữ liệu nghiệp vụ tạo mới hoặc cập nhật sau `T_BACKUP_UTC` sẽ bị xóa bỏ hoàn toàn khỏi hệ thống sau khi restore**, bao gồm: `CRM Lead` mới/chỉnh sửa, `CRM Deal` mới/chuyển giai đoạn, lô nhập CSV (`CSKH Import Batch` + tệp gốc), phân công/thu hồi/chuyển giao (`CSKH Work Item`, `CSKH Active Claim`, `CSKH Assignment History`), nhật ký liên hệ & hiệu chỉnh (`CSKH Contact Attempt`, `CSKH Attempt Correction`, `CRM Call Log`), lịch hẹn (`CRM Task`), nghĩa vụ SLA (`CSKH SLA Obligation`), kỳ chốt `CSKH KPI Run`/`Snapshot`, tệp xuất báo cáo (`CSKH Export Job`) và mọi tệp đính kèm tải lên sau `T_BACKUP_UTC`.
> - **Chỉ thực hiện Tầng 2 khi có phê duyệt rõ ràng của `T0` / Owner sau khi đã xem bảng kiểm đếm số bản ghi phát sinh sau `T_BACKUP_UTC` và đã chụp một bản backup hiện trạng (`pre-restore safety backup`) để lưu vết đối chiếu.**

```bash
export COMPOSE_BIN="/opt/crm-cskh-staging/staging-compose"
export ENV_FILE="/opt/crm-cskh-staging/.env.staging"
export FRAPPE_SITE="crm-staging.localhost"

# 1. Khóa ghi ngay lập tức và dừng workers + scheduler:
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" set-maintenance-mode on
"${COMPOSE_BIN}" stop queue-short queue-long scheduler

# 2. Kiểm đếm chính xác các bản ghi phát sinh hoặc biến đổi sau T_BACKUP_UTC để trình duyệt tác động mất dữ liệu:
"${COMPOSE_BIN}" exec -T backend /home/frappe/frappe-bench/env/bin/python - "${T_BACKUP_UTC}" <<'PY' > /tmp/cskh-post-backup-delta-audit.json
import json, os, sys, frappe
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from frappe.utils import get_system_timezone

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="crm-staging.localhost", sites_path=".")
frappe.connect()
try:
    parsed_utc = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    if parsed_utc.tzinfo is None:
        parsed_utc = parsed_utc.replace(tzinfo=timezone.utc)
    cutoff_local = parsed_utc.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
    dts = [
        "CRM Lead", "CRM Deal", "CSKH Import Batch", "CSKH Work Item",
        "CSKH Assignment History", "CSKH Contact Attempt", "CSKH Attempt Correction",
        "CRM Call Log", "CRM Task", "CSKH SLA Obligation", "CSKH KPI Run", "CSKH Export Job",
    ]
    report = {"t_backup_utc": sys.argv[1], "cutoff_site_local": cutoff_local, "post_backup_deltas": {}}
    for dt in dts:
        if frappe.db.exists("DocType", dt):
            created = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{dt}` WHERE creation > %s", (cutoff_local,))[0][0]
            modified = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{dt}` WHERE creation <= %s AND modified > %s", (cutoff_local, cutoff_local))[0][0]
            report["post_backup_deltas"][dt] = {"created_since_backup": int(created), "updated_since_backup": int(modified)}
    print(json.dumps(report, indent=2, ensure_ascii=False))
finally:
    frappe.destroy()
PY
cat /tmp/cskh-post-backup-delta-audit.json

# 3. Chụp thêm 1 bản safety backup của trạng thái trước khi ghi đè (phục vụ đối chiếu thủ công sau sự cố):
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" backup --with-files

# 4. Sau khi T0/Owner phê duyệt cửa sổ dữ liệu ở Bước 2:
#    - Đặt lại APP_IMAGE=${PREV_APP_IMAGE} trong /opt/crm-cskh-staging/.env.staging (giữ nguyên quyền 0600)
#    - Kiểm tra SHA-256 của bộ tệp backup tại ${BACKUP_HOST_DIR}
#    - Dừng frontend/websocket, nạp tệp backup vào backend và chạy --force restore với mật khẩu root DB đọc từ DB_PASSWORD_SECRETS_FILE trong /opt/crm-cskh-staging/.env.staging:
sudo python3 - "${ENV_FILE}" "${PREV_APP_IMAGE}" <<'PY'
import os, pathlib, re, stat, sys
env_path = pathlib.Path(sys.argv[1])
prev_img = sys.argv[2]
st = env_path.stat()
text = env_path.read_text(encoding="utf-8")
updated, n = re.subn(r"(?m)^APP_IMAGE=.*$", f"APP_IMAGE={prev_img}", text)
assert n == 1, f"Expected 1 APP_IMAGE= line in {env_path}, found {n}"
env_path.write_text(updated, encoding="utf-8", newline="\n")
os.chmod(env_path, 0o600)
os.chown(env_path, st.st_uid, st.st_gid)
assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
print(f"Reverted {env_path} APP_IMAGE -> {prev_img} (mode=0600 preserved)")
PY
test "$(sudo stat -c '%a' "${ENV_FILE}")" = "600"
sudo bash -lc "cd '${BACKUP_HOST_DIR}' && sha256sum -c SHA256SUMS.txt"

"${COMPOSE_BIN}" stop frontend websocket
BACKEND_CID="$("${COMPOSE_BIN}" ps -q backend)"
for f in "${BACKUP_HOST_DIR}"/*-crm_staging_localhost-database.sql.gz \
         "${BACKUP_HOST_DIR}"/*-crm_staging_localhost-files.tar \
         "${BACKUP_HOST_DIR}"/*-crm_staging_localhost-private-files.tar; do
  [ -f "${f}" ] && docker cp "${f}" "${BACKEND_CID}:/tmp/"
done

DB_SECRET_FILE="$(sudo grep '^DB_PASSWORD_SECRETS_FILE=' "${ENV_FILE}" | cut -d= -f2- | tr -d "\"'\r")"
test -n "${DB_SECRET_FILE}" && sudo test -f "${DB_SECRET_FILE}"
DB_ROOT_PW="$(sudo cat "${DB_SECRET_FILE}" | tr -d '\r\n')"
"${COMPOSE_BIN}" exec -T -e DB_ROOT_PW="${DB_ROOT_PW}" backend bash -lc '
  set -eu
  SQL_FILE="$(ls -1t /tmp/*-crm_staging_localhost-database.sql.gz | head -n1)"
  PUB_TAR="$(ls -1t /tmp/*-crm_staging_localhost-files.tar 2>/dev/null | head -n1 || true)"
  PRIV_TAR="$(ls -1t /tmp/*-crm_staging_localhost-private-files.tar 2>/dev/null | head -n1 || true)"
  ARGS=(--force restore "${SQL_FILE}" --db-root-password "${DB_ROOT_PW}")
  [ -n "${PUB_TAR}" ] && ARGS+=(--with-public-files "${PUB_TAR}")
  [ -n "${PRIV_TAR}" ] && ARGS+=(--with-private-files "${PRIV_TAR}")
  bench --site "'"${FRAPPE_SITE}"'" "${ARGS[@]}"
  rm -f /tmp/*-crm_staging_localhost-*
'
unset DB_ROOT_PW

# 5. Đảm bảo encryption_key trong sites/crm-staging.localhost/site_config.json khớp bản lưu ký,
#    chạy configurator + migrator và khởi động lại toàn bộ stack trên PREV_APP_IMAGE (PREV_IMAGE_ID), xóa cache và mở chế độ bảo trì sau khi kiểm tra:
"${COMPOSE_BIN}" run --rm configurator
(
  set -euo pipefail
  MIGRATE_SITES=true "${COMPOSE_BIN}" --profile migrate run --rm \
    -e MIGRATE_SITES=true -e SITE_NAME="${FRAPPE_SITE}" migrator 2>&1 | tee /tmp/cskh-restore-migrator.log
  grep -Fq "running controlled migration for site ${FRAPPE_SITE}" /tmp/cskh-restore-migrator.log
)
"${COMPOSE_BIN}" up -d backend frontend websocket queue-short queue-long scheduler
for svc in backend frontend websocket queue-short queue-long scheduler; do
  actual_iid="$(docker inspect --format '{{.Image}}' "$("${COMPOSE_BIN}" ps -q "${svc}")")"
  test "${actual_iid}" = "${PREV_IMAGE_ID}"
done
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" clear-cache
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" clear-website-cache
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" scheduler resume
"${COMPOSE_BIN}" exec -T backend bench --site "${FRAPPE_SITE}" set-maintenance-mode off
```

---

### 2.6. Lưu ý phối hợp liên nhánh với P4 (`codex/p4-01-compose` / `codex/p4-ops-readiness`)

- Trên trang `/cskh/quality-overview`, tiêu đề `<h1>` đã chuyển từ `"Tổng quan chất lượng CSKH (BOD)"` sang **`"Tổng quan chất lượng liên hệ & SLA (BOD)"`**.
- Khi rebase các nhánh vận hành P4 lên sau PR này, cập nhật chuỗi kiểm tra smoke HTTP tương ứng trong `run_isolated_smoke.py` / `staging_https_smoke_and_reconcile.py` để khớp với tiêu đề mới.
