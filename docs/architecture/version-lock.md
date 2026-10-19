# VERSION LOCK — CRM-CSKH (WP-00 / P0)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng CSKH
> **Tài liệu:** `docs/architecture/version-lock.md`
> **Phiên bản:** 1.5 — 19/10/2026 (T1A: cập nhật digest 2 image; bổ sung ma trận tương thích rộng với Python range; liệt kê tag mới hơn upstream; bổ sung Q-01…Q-06)
> **Tài liệu cha:** [`../root-plan/P0.md`](../root-plan/P0.md) (§2, ADR-001) · [`../root-plan/master-plan.md`](../root-plan/master-plan.md) (§3, §19)
> **Trạng thái:** Metadata/tag/digest đã khóa (đã cập nhật digest mới cho `frappe/bench:v5.31.0` và `mariadb:11.8` ngày 2026-10-19); baseline CRM/Frappe đã cài, migrate và smoke trên máy DEV cùng GitHub runner sạch. **MariaDB 11.8 compatibility vẫn cần chốt** vì Framework cảnh báo phiên bản này chưa được kiểm thử — xem §8.

---

## 1. Phạm vi và phương pháp xác minh

Tài liệu này khóa tập phiên bản nền tảng cho DEV của dự án: **Frappe CRM**, **Frappe Framework**, **Frappe Docker** và các **image hạ tầng** (MariaDB, Redis). Mọi con số dưới đây được lấy từ nguồn upstream ở **đúng phiên bản đang khóa**, không suy luận từ nhánh `develop`.

### 1.1. Cách xác minh (provenance)

| Nguồn | Dùng để | Ghi chú |
|---|---|---|
| `git ls-remote` tới `github.com/frappe/{crm,frappe,frappe_docker}` | Lấy **full commit SHA** của tag, kiểm tra tag nào là mới nhất bằng version-sort | Chỉ đọc ref, không clone thêm upstream vào repo dự án |
| Checkout cục bộ `frappe/crm@v1.87.0` | Đối chiếu `git describe`, đọc `pyproject.toml`, `LICENSE`, `frontend/package.json`, `docker/docker-compose.yml` **đúng tag** | Lúc xác minh nằm ở `.survey-cache/crm`; **thư mục này nay KHÔNG còn trong working tree**. Xem §9.2; đây là nguồn "file:line" cho các kết luận về CRM |
| `raw.githubusercontent.com` @ tag cụ thể | Đọc metadata Frappe v15/v16 và `frappe_docker` ở đúng revision | |
| Docker Hub Registry API | Lấy **tag + manifest digest** thật của image hạ tầng | Không đoán tag |

### 1.2. Môi trường thực thi lệnh xác minh

- OS: Windows 11 Home Single Language, build `26200` (CIM báo product name "Windows 10 Home Single Language")
- Git: `2.55.0.windows.5`
- Docker client: `29.7.2`, Compose `v5.6.0` (runtime WP-01; probe ban đầu ghi `v5.4.0`), context `desktop-linux`
- Thời điểm xác minh: **2026-10-15**

> Bằng chứng thô của các lệnh dò môi trường được lưu tại [`deploy/dev/evidence/environment-probe.md`](../../deploy/dev/evidence/environment-probe.md).

---

## 2. KHÓA PHIÊN BẢN BẮT BUỘC (release lock)

Không dùng `latest`, `main`, `develop` làm bản phát hành. Tất cả mục dưới đây là **tag bất biến + full commit SHA**.

| # | Thành phần | Repository | Tag | Full commit SHA | Trạng thái |
|---|---|---|---|---|---|
| L-01 | **Frappe CRM** (baseline) | `github.com/frappe/crm` | `v1.87.0` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | ✅ VERIFIED |
| L-02 | **Frappe Framework** (chính) | `github.com/frappe/frappe` | `v15.123.1` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | ✅ VERIFIED |
| L-03 | Frappe Framework (thay thế, xem §4) | `github.com/frappe/frappe` | `v16.52.0` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` | ✅ VERIFIED (chưa chọn) |
| L-04 | **Frappe Docker** (tooling DEV) | `github.com/frappe/frappe_docker` | `v4.0.1` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | ✅ VERIFIED |
| L-05 | Custom app `crm_cskh` | `github.com/nobita6986/new-crm` | `0.0.1` | Có trong PR #1 (`codex/p0-ci-exit`); GitHub Actions clean bootstrap + 28 tests + backup/restore pass ([run](https://github.com/nobita6986/new-crm/actions/runs/38712593755)) |
| L-06 | Repo dự án (docs + app) | `github.com/nobita6986/new-crm` | `main` | `bc114d21cd099593c2dcb4396b040838f36a3cce` | ✅ VERIFIED |

**Ghi chú xác minh quan trọng:**

- Tag `v15.123.1` **trùng chính xác** với đầu nhánh `version-15` → `d2efa79657ba5c1abf96098b3bfacc199517d57e`. Tương tự, `v16.52.0` trùng đầu nhánh `version-16` → `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a`. Vì vậy "pin tag" và "pin branch tip" hiện cho cùng một commit — nhưng **ta pin tag**, không pin branch.
- `v1.87.0` **là bản release mới nhất của dòng `v1.x`** tại thời điểm khóa (263 tag `v1.*`; `v1.87.0` đứng cuối theo version-sort). Khớp với baseline mà master-plan đã chọn.
- Các tag của frappe (v15/v16) là **lightweight tag** trỏ trực tiếp vào commit (không có dòng peeled `^{}`), nên tag SHA chính là commit SHA.
- **Đồng bộ repo SHA (bản 1.1):** repo dự án hiện ở commit `bc114d21cd099593c2dcb4396b040838f36a3cce`; lịch sử đã được **amend** từ `fc30134f2833fa2aff43b3c1aecd707bddc95688` (cùng thông điệp "Add CRM CSKH project plans"). `HEAD` = `origin/main`. **Các tag/SHA upstream ở L-01…L-04 KHÔNG đổi.**

---

## 3. RÀNG BUỘC TOOLCHAIN & DEPENDENCY

### 3.1. Python

| Nguồn | Ràng buộc | Ý nghĩa |
|---|---|---|
| `frappe/crm@v1.87.0` → `pyproject.toml` | `requires-python = ">=3.10"` | CRM cần ≥ 3.10 |
| `frappe/frappe@v15.123.1` → `pyproject.toml` | `requires-python = ">=3.10,<3.15"` | **Frappe v15 chấp nhận Python 3.10 – 3.14** |
| `frappe/frappe@v16.52.0` → `pyproject.toml` | `requires-python = ">=3.14,<3.15"` | **Frappe v16 chỉ chấp nhận Python 3.14** |

➡️ Giao của CRM + Frappe v15: **Python 3.10 – 3.14**. Host có Python `3.12.12`; lần chạy WP-01 dùng Python `3.14.8` trong image bench — cả hai nằm trong dải khai báo.

### 3.2. Node / Yarn / package build

| Nguồn | Ràng buộc | Ý nghĩa |
|---|---|---|
| `frappe/crm@v1.87.0/frontend/package.json` → `engines.node` | `^20.19.0 \|\| >=22.12.0` | Frontend CRM cần **Node 20.19+ hoặc 22.12+** |
| `frappe/crm@v1.87.0/package.json` → `scripts` | `postinstall: cd frontend && yarn install --check-files`, `dev/build` đều gọi `yarn` | **Yarn là bắt buộc** để build frontend CRM |

➡️ Host có **Node v22.22.1**; image bench chạy **Node v24.22.0 + Yarn 1.22.22**. Build CRM frontend đã chạy thành công trong bootstrap.

### 3.3. Dependency runtime của CRM

| Package | Phiên bản ghim | Nguồn |
|---|---|---|
| `twilio` | `==8.5.0` | `crm/pyproject.toml` (dependencies) |
| `frappe` (bench-managed) | `>=15.0.0,<17.0.0` | `crm/pyproject.toml` → `[tool.bench.frappe-dependencies]` |
| `frappe-ui` | `1.0.0-beta.29` | `crm/frontend/package.json` |
| `vite` | `8.2.0` (có `resolutions` ghim cứng) | `crm/frontend/package.json` |
| `vue` | `^3.5.13` | `crm/frontend/package.json` |

**Quy tắc P0:** không tự nâng major version của các package trên trong giai đoạn P0.

### 3.4. Framework test có sẵn (tái sử dụng, không dựng mới)

| Tầng | Công cụ | Nguồn |
|---|---|---|
| Python/Frappe | `bench run-tests` (Frappe test runner) | `P0.md` §9.2 |
| Frontend CRM | **Vitest** (`"test": "vitest"`, `vitest ^4.1.10`) | `crm/frontend/package.json` |
| E2E | **Playwright** (`@playwright/test ^1.49.0`, `playwright.config.ts` ở repo CRM) | `crm/package.json`, `crm/playwright.config.ts` |

➡️ P0 §9.2 yêu cầu "reuse test framework đang có" → Vitest (unit) + Playwright (E2E) **đã có sẵn trong CRM**, không cần thêm framework mới.

---

## 4. QUYẾT ĐỊNH FRAMEWORK: v15 (chính) vs v16 (thay thế)

**Tiêu chí P0 (§2.2 master plan):** chọn nhánh stable tương thích với tag CRM, ưu tiên **khả năng tái tạo** và **không cưỡng ép**.

| Tiêu chí | Frappe `v15.123.1` | Frappe `v16.52.0` |
|---|---|---|
| CRM `v1.87.0` khai báo hỗ trợ | Có (`>=15.0.0,<17.0.0`) | Có (`>=15.0.0,<17.0.0`) |
| README CRM (bảng Compatibility) | Có (`v15.x`) | Có (`v16.x`) |
| Python yêu cầu | **3.10 – 3.14** (rộng) | **chỉ 3.14** (hẹp) |
| Độ chín / hệ sinh thái image | Dòng ổn định lâu năm, nhiều image dựng sẵn | Dòng mới, yêu cầu Python 3.14 mới |
| Rủi ro cho DEV tái tạo được | Thấp | Cao hơn (phụ thuộc Python 3.14 trong image) |
| Khớp Python máy DEV (3.12.12) | ✅ | ❌ (cần 3.14) |

**Quyết định (đề xuất ADR-001):** Khóa **Frappe Framework `v15.123.1`** làm baseline DEV cho P0/P1.

**Lý do:** (a) cả hai đều được khai báo tương thích, nhưng v15 phủ dải Python rộng 3.10–3.14 nên **dễ tái tạo bằng image dựng sẵn** và khớp toolchain hiện có; (b) v16 giới hạn **chỉ Python 3.14** — đúng ngay biên, rủi ro cao cho một môi trường DEV cần ổn định; (c) P0 yêu cầu "ma trận tương thích có ghi lại kết quả", nên v16 vẫn được giữ như **phương án thay thế phải test sau** khi baseline v15 xanh.

> **Hệ quả bắt buộc:** mọi tài liệu/branch P1+ mặc định v15. Nếu sau này đổi sang v16, phải chạy lại toàn bộ ma trận §8 và ghi lại kết quả, **không sửa core để cưỡng ép chạy**.

---

## 5. IMAGE & HẠ TẦNG (digest lock)

Nguyên tắc: **không tag trôi**. Mọi image dùng cho DEV được ghim `tag@digest` (manifest-list digest). Các image dưới đây lấy digest trực tiếp từ Docker Hub Registry API; digest cho `frappe/bench:v5.31.0` và `mariadb:11.8` đã được **cập nhật 2026-10-19** do upstream push lại image với cùng tag. `redis:8.6-alpine` giữ nguyên digest.

| Vai trò | Image | Tag | Manifest digest (multi-arch) | amd64 digest | Ghi chú |
|---|---|---|---|---|---|
| Bench DEV (tooling) | `frappe/bench` | `v5.31.0` | `sha256:3b75943bf00a1db92723d99ec34fbfa92f4b67b9b643ae1e96750899b7fbd980` | `sha256:05d3bc0d739db2146c73515bf320a2cc88e7bfb1f97a5c74d73ef3b2fd56f18d` | Chứa bench + pyenv + nvm; ta `bench init` bên trong. **Digest index/amd64 thay đổi so với v1.4**; `tag_last_pushed = 2026-10-18T04:11:04Z`. |
| Database | `mariadb` | `11.8` | `sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa` | `sha256:d689f65e09e887e96db5c40ca3957dd9f4e3fad3516baec96d7726c78a5fc2ee` | Khớp `frappe_docker@v4.0.1` override. **Digest index/amd64 thay đổi so với v1.4**; `tag_last_pushed = 2026-10-17T05:36:40Z`. |
| Redis cache + queue | `redis` | `8.6-alpine` | `sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` | `sha256:019e4fb3c6de024c8f70ab5219d4abded5f335de770876981c7b0a051c43df87` | Khớp `frappe_docker@v4.0.1` override. Digest **không đổi** kể từ v1.4. |

**Cơ sở chọn phiên bản image:** `frappe_docker@v4.0.1` dùng `mariadb:11.8` và `redis:8.6-alpine` (xem `overrides/compose.mariadb.yaml`, `overrides/compose.redis.yaml`). Ta bám theo để giảm sai lệch với cấu hình được upstream kiểm thử. Trên `main` của `frappe_docker` (commit `11d6062…` ngày 2026-10-19), override vẫn dùng `mariadb:11.8` — chưa có tag mới được phát hành kể từ `v4.0.1`.

**Đã đo trong lần WP-01 runtime (linux/amd64):**
- `frappe/bench:v5.31.0`: Bench 5.31.0, Python 3.14.8, Node 24.22.0, Yarn 1.22.22.
- RepoDigests của bench/MariaDB/Redis khớp các digest khóa ở §5 (cập nhật 2026-10-19).

**Ghi chú về việc thay đổi digest:** hai image `frappe/bench:v5.31.0` và `mariadb:11.8` đã được
upstream rebuild và push lại cùng tag. Đây là thực tiễn bình thường của Docker Hub
("cùng tag, nội dung khác"). Sau khi Owner chạy `docker compose pull` trên host DEV
và cập nhật `.env.example` tương ứng (xem `deploy/dev/.env.example`), `RepoDigest` cục bộ
sẽ khớp digest mới. Bằng chứng đo lại: [`t1a-g001-metadata-recheck.md`](../../deploy/dev/evidence/t1a-g001-metadata-recheck.md) §4.

---

## 6. KIỂM TRA LICENSE

| Thành phần | License | Nguồn xác minh |
|---|---|---|
| Frappe CRM `v1.87.0` | **AGPL-3.0** | `LICENSE` file tại tag: *"GNU AFFERO GENERAL PUBLIC LICENSE Version 3"* |
| Frappe Framework | MIT | repo `frappe/frappe` |
| `crm_cskh` (code tự phát triển) | **AGPL-3.0** (`app_license` + `license.txt`, theo WP-02) | Cần rà soát tuân thủ trước phát hành; không phải kết luận pháp lý ([WP-02 evidence](../../deploy/dev/evidence/wp-02-p0b.md) §7) |

**Cảnh báo cần lưu ý (phát hiện khi đối chiếu):** `package.json` gốc của CRM `v1.87.0` ghi `"license": "GPL-3.0"`, **không khớp** với file `LICENSE` là **AGPL-3.0**. Khi lập luận về nghĩa vụ pháp lý, **file `LICENSE` (AGPL-3.0) là nguồn có thẩm quyền**, không dùng field `package.json`.

**Nghĩa vụ cần T0 ghi nhận (chưa phải kết luận pháp lý):** AGPL-3.0 áp nghĩa vụ cung cấp mã nguồn cho người dùng tương tác qua mạng đối với bản sửa đổi/triển khai. Không mặc định rằng fork kín không phát sinh nghĩa vụ. Việc chọn license cho `crm_cskh` phải chốt cùng bối cảnh này trước khi phát hành bản có sửa core.

---

## 7. LỆNH TÁI TẠO & CÁCH XÁC MINH

### 7.1. Xác minh lại khóa phiên bản (chỉ đọc, không cần Docker)

```bash
# Full SHA của tag (phải khớp bảng §2)
git ls-remote https://github.com/frappe/crm            refs/tags/v1.87.0
git ls-remote https://github.com/frappe/frappe         refs/tags/v15.123.1
git ls-remote https://github.com/frappe/frappe         refs/tags/v16.52.0
git ls-remote https://github.com/frappe/frappe_docker  refs/tags/v4.0.1

# Đối chiếu hash repo dự án với bảng §2 (L-05/L-06)
git rev-parse HEAD          # -> bc114d21cd099593c2dcb4396b040838f36a3cce
git rev-parse origin/main   # -> bc114d21cd099593c2dcb4396b040838f36a3cce
git status --short          # -> deploy/, docs/... còn untracked (chưa commit)

# Kiểm tra checkout CRM cục bộ — KHÔNG bắt buộc.
# LƯU Ý: `.survey-cache/crm` dùng lúc xác minh ban đầu NAY KHÔNG CÒN trong working tree.
# Muốn đối chiếu lại, clone tạm RA NGOÀI repo dự án rồi kiểm:
#   git clone --depth 1 --branch v1.87.0 https://github.com/frappe/crm.git /tmp/crm
#   git -C /tmp/crm describe --tags --always   # -> v1.87.0
#   git -C /tmp/crm rev-parse HEAD             # -> 0d3910b456ef4fb913e77b29db011b3debc4f778
```

### 7.2. Tái tạo môi trường DEV

Toàn bộ quy trình setup/start/stop/migrate/seed nằm ở [`docs/operations/dev-setup.md`](../operations/dev-setup.md) và [`deploy/dev/`](../../deploy/dev/). Biến phiên bản ở §2–§5 được đưa vào `deploy/dev/.env.example` để bootstrap ghim đúng.

### 7.3. Lệnh kiểm chứng bắt buộc sau khi có môi trường (P0 §2.3)

```bash
bench --site dev.localhost list-apps    # có crm_cskh nếu bootstrap với INSTALL_CSKH=1
bench --site dev.localhost migrate
git -C apps/frappe rev-parse HEAD       # đối chiếu d2efa796...
git -C apps/crm rev-parse HEAD          # đối chiếu 0d3910b4...
```

---

## 8. MA TRẬN TƯƠNG THÍCH — TRẠNG THÁI XÁC MINH

Phân biệt rõ hai mức độ bằng chứng:

- **[DECLARED]** — suy ra từ metadata upstream ở đúng phiên bản (nguồn đáng tin, nhưng chưa chạy).
- **[RUNTIME]** — đã chạy thật và quan sát kết quả (có log).

| Tổ hợp | CRM | Framework | Python | Kỳ vọng | [DECLARED] | [RUNTIME] |
|---|---|---|---|---|---|---|
| C1 | v1.87.0 | v15.123.1 | 3.12 | Chạy được | ✅ hợp lệ (`>=3.10,<3.15`) | ⛔ Chưa chạy trên Python 3.12 |
| C2 | v1.87.0 | v16.52.0 | 3.14 | Chạy được | ✅ hợp lệ (đúng biên) | ⛔ Chưa cài/test Frappe v16 trong bench này |
| C3 | v1.87.0 | v15.123.1 | 3.14 | Chạy được | ✅ hợp lệ | ✅ Python 3.14.8: clean bootstrap/install/migrate/seed, 28 tests, import, SLA, backup/restore; GitHub Actions clean runner cũng pass. MariaDB 11.8 vẫn có cảnh báo framework >10.8 chưa được thử rộng |
| Cx | v1.87.0 | bất kỳ <v15 hoặc ≥v17 | — | Không hỗ trợ | ❌ ngoài `>=15.0.0,<17.0.0` | — (loại) |

**Tổ hợp bổ sung (T1A, ngày 2026-10-19):**

| Tổ hợp | CRM | Framework | Python | Kỳ vọng | [DECLARED] | [RUNTIME] |
|---|---|---|---|---|---|---|
| C4 | v1.87.0 | v15.99.0 | 3.13 | Chạy được | ✅ hợp lệ (`>=3.10,<3.14`) | ⛔ Chưa chạy; v15.99.0 chưa được verify trong dự án |
| C5 | v1.87.0 | v15.99.0 | 3.14 | **KHÔNG chạy** | ❌ v15.99.0 đã bỏ Python 3.14 khỏi dải cho phép (`>=3.10,<3.14`) | — (không nên thử; image bench hiện dùng 3.14.8) |
| C6 | v1.88.0 | v15.123.1 | 3.14 | Chạy được | ✅ theo compatibility table README CRM v1.88.0 (`v15.x & v16.x`); CRM v1.88.0 thêm dependency `requests>=2.28.0` + `tldextract>=5.0.0` | ⛔ Chưa chạy; CRM v1.88.0 chưa được verify runtime trong dự án |
| C7 | v1.87.0 | v16.9.0 | 3.14 | Chạy được | ✅ hợp lệ (`>=3.14,<3.15`) | ⛔ Chưa cài/test v16.9.0 |

**Bằng chứng cho C5** (Frappe v15.99.0 không còn hỗ trợ Python 3.14): trích từ
`raw.githubusercontent.com/frappe/frappe/v15.99.0/pyproject.toml`:
`requires-python = ">=3.10,<3.14"`. Nếu sau này muốn nâng Frappe v15 lên bản ≥ v15.99.0,
phải giảm Python runtime xuống ≤ 3.13 (không thực hiện trong P0). Chi tiết và đề xuất
quyết định Q-01…Q-06 ở [`t1a-g001-metadata-recheck.md`](../../deploy/dev/evidence/t1a-g001-metadata-recheck.md) §5.

### 8.1. Runtime đã đo trên máy DEV hiện tại

- `wsl --status`: default version 2; `wsl -l -v`: `docker-desktop` Running, version 2.
- Docker Engine 29.7.2 trên context `desktop-linux`, Linux kernel `...microsoft-standard-WSL2`; Docker Compose v5.6.0.
- Các image đã pull trên `linux/amd64`, RepoDigests khớp digest trong §5. DB healthy; Redis cache/queue và bench chạy.
- Bootstrap tạo `dev.localhost`, cài `frappe` + `crm`, migrate thành công; lần chạy lại bootstrap thành công. Seed 15 Lead giả và các fixture khác chạy hai lần không trùng. Migrate sau seed thành công.
- `docker compose down` không dùng `-v` rồi `up -d` đã dựng lại containers; site/apps và 15 Lead giả vẫn tồn tại.
- Authenticated `/crm` trả HTTP 200; API login thành công và `get_logged_user` trả Administrator. Guest `/crm` trả 403.
- Bằng chứng chi tiết: [`wp01-runtime.md`](../../deploy/dev/evidence/wp01-runtime.md).

**Caveat tương thích:** khi tạo site, Frappe v15 cảnh báo rằng MariaDB 11.8 vượt phiên bản 10.8 chưa được Framework kiểm thử. Install/migrate thành công chứng minh stack chạy được cho WP-01, nhưng không xóa bỏ caveat này; cần chốt MariaDB tương thích trước khi đóng ADR-001.

➡️ Docker/WSL2 blocker đã gỡ trên host hiện tại; CI trên GitHub runner đã kiểm tra clean bootstrap và backup/restore. P0-A còn Vue hot reload; cảnh báo MariaDB và các gate bảo mật/UI vẫn cần xử lý. Kết quả này **không** phải bằng chứng G0-01..G0-10 đều PASS.

---

## 9. KHÔNG DÙNG TAG TRÔI — BẰNG CHỨNG TỪ UPSTREAM

### 9.1. `frappe_docker@v4.0.1` — `devcontainer-example/docker-compose.yml`

Dùng `image: docker.io/frappe/bench:latest` và `docker.io/redis:alpine` → **cả hai đều là tag trôi**. Ta giữ *cách làm* của frappe_docker (devcontainer + bench) nhưng **thay bằng tag+digest cụ thể** trong `deploy/dev/docker-compose.yml`.

### 9.2. `frappe/crm@v1.87.0` — `docker/docker-compose.yml` (compose demo)

| Dòng | Nội dung | Vấn đề |
|---|---|---|
| `image: mariadb:10.8` | MariaDB cũ | Lệch với `frappe_docker@v4.0.1` (11.8) |
| `image: redis:alpine` | Tag trôi | Không tái tạo được |
| `image: frappe/bench:latest` | **Tag trôi** | Vi phạm trực tiếp quy tắc khóa phiên bản |
| `MYSQL_ROOT_PASSWORD: 123` | Mật khẩu mẫu | Không dùng cho bất kỳ môi trường thật nào |
| `ports: [8000:8000, 9000:9000]` | Publish ra mọi interface | DEV chỉ nên bind `127.0.0.1` |

➡️ Đúng như `P0.md` §2.1 cảnh báo: **không dùng nguyên trạng `docker/docker-compose.yml` của CRM**. Ta chỉ tham khảo và thay bằng cấu hình ghim phiên bản trong `deploy/dev/`.

---

## 10. ĐIỂM MỞ — CẦN T0 CHỐT

| # | Điểm mở | Ảnh hưởng | Đề xuất |
|---|---|---|---|
| O-01 | **ĐÃ GỠ trên host hiện tại:** Docker Desktop Linux Engine hoạt động qua WSL2; Docker/Compose và bốn service chạy | Setup trên máy khác/clean machine chưa thử | Giữ DEV stack; Linux DEV VM vẫn là phương án dự phòng nếu host này không ổn định |
| O-02 | ADR-001: chốt Framework + DB tương thích | Ảnh hưởng toàn bộ P1+ | Giữ Frappe v15.123.1 làm baseline P0; quyết định cuối chờ xử lý caveat MariaDB 11.8 chưa được Frappe kiểm thử |
| O-03 | Python/Node/Yarn thực tế trong `frappe/bench:v5.31.0` | Tính tái tạo | **ĐÃ GHI:** Python 3.14.8, Node 24.22.0, Yarn 1.22.22, Bench 5.31.0 (§8.1) |
| O-04 | `package.json` CRM ghi `GPL-3.0` vs `LICENSE` AGPL-3.0 | Nghĩa vụ pháp lý | Dùng **AGPL-3.0** (file LICENSE) làm căn cứ |
| O-05 | (ĐÃ ĐÓNG) Trước đây repo có `.survey-cache/crm/` — checkout upstream **untracked**. **Nay thư mục này KHÔNG còn trong working tree.** | Trước đây có nguy cơ commit nhầm source upstream | Đã hết. Khi commit: **loại trừ** `.commandcode/`, mọi cache và checkout upstream; đưa `apps/crm_cskh/`, `docs/` và `deploy/` vào phạm vi review. |
| O-06 | **T1A — 2026-10-19:** (a) digest `frappe/bench:v5.31.0` và `mariadb:11.8` đã thay đổi; §5 và `deploy/dev/.env.example` đã cập nhật. (b) Đã có CRM `v1.88.0`, Frappe `v15.99.0` và `v16.9.0` mới hơn; **không nâng** trong P0 vì (i) chưa có runtime evidence mới và (ii) v15.99.0 đã bỏ Python 3.14. (c) `crm/frontend/vite.config.js` upstream chưa đọc trong phiên này — cần đọc trước khi chạy Vue hot reload. | Ảnh hưởng tái tạo + lộ trình nâng version | Xem [`t1a-g001-metadata-recheck.md`](../../deploy/dev/evidence/t1a-g001-metadata-recheck.md) §5.4 (Q-01…Q-06). Khi có máy Docker, chạy lại ma trận workload MariaDB (xem §5.5) trước khi đóng ADR-001. |

---

## 11. ĐỊNH NGHĨA "ĐÃ KHÓA" CHO P1+

Một phiên bản được coi là **đã khóa** khi và chỉ khi:
1. Có **tag bất biến** + **full commit SHA** trong bảng §2 (hoặc `tag@digest` cho image ở §5), và
2. Đã có **[RUNTIME] PASS** trong §8 trên môi trường DEV tái tạo được, và
3. Kết quả được ghi lại kèm log/lệnh ở [`docs/operations/dev-setup.md`](../operations/dev-setup.md).

Hiện tại: điều kiện (1) **đạt**; điều kiện (2)(3) có bằng chứng trên máy DEV và GitHub-hosted runner ([CI run](https://github.com/nobita6986/new-crm/actions/runs/38712593755)). Phạm vi đã chạy là baseline Frappe v15/Python 3.14.8/MariaDB 11.8; cảnh báo MariaDB >10.8 vẫn mở, còn Python 3.12 và Frappe v16 chưa được thử.
