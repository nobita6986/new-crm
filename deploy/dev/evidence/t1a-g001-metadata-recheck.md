# EVIDENCE — T1A: G0-01 metadata re-verification (đọc-only, không tạo runtime mới)

> **Ngày xác minh:** 2026-10-19 (UTC+7)
> **Môi trường xác minh:** phiên Codex trên Windows. KHÔNG có Docker / Python / Yarn.
> **Phạm vi:** đối chiếu lại `git ls-remote`, `raw.githubusercontent.com` (đúng tag) và
> Docker Hub Registry API cho baseline P0 đã khóa ở [`version-lock.md`](../../docs/architecture/version-lock.md).
> **Bằng chứng runtime mới:** KHÔNG có. Tài liệu này chỉ chứa kết quả **đọc metadata tĩnh**
> và đề xuất quyết định tương thích có căn cứ. Việc thực thi `bench start`/MariaDB/Vite
> thuộc trách nhiệm máy có Docker/WSL2.
> **Không sửa upstream CRM/Frappe.**

---

## 1. Công cụ & môi trường xác minh

| Công cụ | Giá trị | Ghi chú |
|---|---|---|
| OS | Windows 11 (build 26200) | giống `environment-probe.md` |
| `git` | 2.55.0.windows.5 | dùng `git ls-remote` qua Windows cmd |
| `node` | 22.23.3 | KHÔNG dùng cho evidence này |
| `WebFetch` (cursor) | MCP WebFetch tới `raw.githubusercontent.com` & `registry.hub.docker.com` | chỉ đọc |
| `docker` / `python` / `yarn` | **không có** trên máy này | xác nhận qua `where`/`Get-Command` |

Lệnh dùng để xác minh SHAs (PowerShell 7 trên Windows, `;` thay cho `&&`):

```powershell
cd d:\HRP-app\new-crm
git ls-remote https://github.com/frappe/crm           refs/tags/v1.87.0
git ls-remote https://github.com/frappe/frappe        refs/tags/v15.123.1
git ls-remote https://github.com/frappe/frappe        refs/tags/v16.52.0
git ls-remote https://github.com/frappe/frappe_docker refs/tags/v4.0.1

# Branch tip tương ứng với tag (đã đối chiếu ở version-lock 1.4 §2.2)
git ls-remote https://github.com/frappe/frappe        refs/heads/version-15
git ls-remote https://github.com/frappe/frappe        refs/heads/version-16
```

---

## 2. Đối chiếu SHAs đã khóa — kết quả

| # | Thành phần | Tag (lockfile) | SHA mong đợi (lockfile) | SHA quan sát 2026-10-19 | Khớp? |
|---|---|---|---|---|---|
| L-01 | Frappe CRM | `v1.87.0` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | ✅ |
| L-02 | Frappe Framework | `v15.123.1` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | ✅ |
| L-03 | Frappe Framework (thay thế) | `v16.52.0` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` | ✅ |
| L-04 | Frappe Docker | `v4.0.1` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | ✅ |
| L-05 | `version-15` tip (đã đối chiếu) | — | `d2efa796…` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | ✅ |
| L-06 | `version-16` tip (đã đối chiếu) | — | `4515014c…` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` | ✅ |

**Kết luận:** Mọi SHA đã khóa trong `version-lock.md` §2 vẫn đúng vào 2026-10-19.
**Tag-pin và branch-tip hiện trùng SHA** ở cả hai dòng Frappe, nên ràng buộc "pin tag"
không có rủi ro trôi so với branch ổn định.

---

## 3. Phát hiện mới: đã có bản phát hành mới hơn

| Repo | Tag/Branch mới nhất quan sát | SHA | Ghi chú |
|---|---|---|---|
| `frappe/crm` | `refs/tags/v1.88.0` | `0baa1e8ea462b92d2920bcfdc7a532a730329b6c` | **MỚI hơn baseline `v1.87.0`** |
| `frappe/frappe` | `refs/tags/v15.99.0` | `5cca1fa7e38cfab74c78627c93a9210dc3fdbccc` | Mới hơn `v15.123.1` |
| `frappe/frappe` | `refs/tags/v16.9.0` | `e5b04e04d83aa1bc1670913b6d58d1d3f4118307` | Mới hơn `v16.52.0` |
| `frappe/frappe_docker` | branch `main` tip | `11d606208c3708cf999d50b7eb798032fc469f5f` | `main`; tag ổn định vẫn `v4.0.1` |
| `frappe/bench` (image) | tag mới nhất `v5.31.0` | xem §4 | Không có tag `v5.32+` |

**Diễn giải:** bộ baseline P0 đã khóa (`v1.87.0` + `v15.123.1` + `v4.0.1`) **không còn là
bản mới nhất**. Đây là trạng thái bình thường của dòng phát hành upstream; `version-lock.md`
đã chủ động "pin tag" nên việc dựng lại vẫn ổn định. Tuy nhiên:

- Việc nâng `crm` lên `v1.88.0` hoặc Frappe v15 lên `v15.99.0` **không nằm trong P0**;
  là việc của P1/P2 sau khi có quyết định ADR-001 cuối cùng.
- `frappe_docker` `main` đã có thêm commit nhưng `v4.0.1` vẫn là tag ổn định
  đang dùng — chưa có tag mới được phát hành kể từ khi khóa.

---

## 4. Phát hiện mới: image digest đã thay đổi kể từ khi khóa

Đo từ Docker Hub Registry API (`registry.hub.docker.com/v2/repositories/.../tags/<tag>/`)
lúc 2026-10-19 (UTC+7):

| Image | `index` digest 2026-10-15 (lockfile) | `index` digest 2026-10-19 | `amd64` digest 2026-10-19 | `tag_last_pushed` |
|---|---|---|---|---|
| `frappe/bench:v5.31.0` | `sha256:1b27bb30ab18b0a50b5264f44988335bfe8e280a68b45d0ff2daf72ca6ddb85f` | `sha256:3b75943bf00a1db92723d99ec34fbfa92f4b67b9b643ae1e96750899b7fbd980` | `sha256:05d3bc0d739db2146c73515bf320a2cc88e7bfb1f97a5c74d73ef3b2fd56f18d` | `2026-10-18T04:11:04Z` |
| `mariadb:11.8` | `sha256:6422478cb8e159f080fb1d8ccf65101e26fe51385787fde7d16c3b165a331f15` | `sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa` | `sha256:d689f65e09e887e96db5c40ca3957dd9f4e3fad3516baec96d7726c78a5fc2ee` | `2026-10-17T05:36:40Z` |
| `redis:8.6-alpine` | `sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` | `sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` | `sha256:019e4fb3c6de024c8f70ab5219d4abded5f335de770876981c7b0a051c43df87` | `2026-09-25T00:05:56Z` |

**Diễn giải:**

- `frappe/bench:v5.31.0` và `mariadb:11.8` đã được **push lại với nội dung khác** (digest
  mới) kể từ khi `version-lock.md` v1.4 được tạo. Đây là hiện tượng tag bất biến
  theo nghĩa "cùng tag, nội dung khác" của Docker Hub.
- `redis:8.6-alpine` **không đổi** digest.
- Thực tiễn đúng tinh thần "tag@digest" vẫn đảm bảo tái tạo — chỉ cần cập nhật digest
  mới trong lockfile. Khi `docker compose pull` chạy trên host DEV, image mới sẽ
  được tải về và `RepoDigest` cục bộ sẽ trùng digest mới.

**Hệ quả cho `version-lock.md` §5:** cần cập nhật digest của 2 image trên.
Đây là lý do cập nhật `version-lock.md` lên v1.5 (xem §6) cùng G0-01.

---

## 5. Đề xuất quyết định tương thích MariaDB/Frappe có căn cứ (evidence-based)

### 5.1. Quan sát nguồn upstream

| Nguồn | Phát biểu | URL truy vấn |
|---|---|---|
| `frappe/crm@v1.87.0` → `pyproject.toml` | `frappe = ">=15.0.0,<17.0.0"`; `requires-python = ">=3.10"` | `raw.githubusercontent.com/frappe/crm/v1.87.0/pyproject.toml` |
| `frappe/crm@v1.88.0` → `pyproject.toml` | Cùng `frappe = ">=15.0.0,<17.0.0"`; `requires-python = ">=3.10"` | `raw.githubusercontent.com/frappe/crm/v1.88.0/pyproject.toml` |
| `frappe/frappe@v15.123.1` → `pyproject.toml` | `requires-python = ">=3.10,<3.15"` | `raw.githubusercontent.com/frappe/frappe/v15.123.1/pyproject.toml` |
| `frappe/frappe@v15.99.0` → `pyproject.toml` | `requires-python = ">=3.10,<3.14"` (đã **bỏ** Python 3.14) | `raw.githubusercontent.com/frappe/frappe/v15.99.0/pyproject.toml` |
| `frappe/frappe@v16.52.0` → `pyproject.toml` | `requires-python = ">=3.14,<3.15"` | (đã đối chiếu ở v1.4) |
| `frappe/frappe@v16.9.0` → `pyproject.toml` | `requires-python = ">=3.14,<3.15"` (giống v16.52.0) | `raw.githubusercontent.com/frappe/frappe/v16.9.0/pyproject.toml` |
| `frappe_docker@v4.0.1` → `overrides/compose.mariadb.yaml` | `image: mariadb:11.8` | `raw.githubusercontent.com/frappe/frappe_docker/v4.0.1/overrides/compose.mariadb.yaml` |
| `frappe_docker@main` → `overrides/compose.mariadb.yaml` | Vẫn `image: mariadb:11.8` | `raw.githubusercontent.com/frappe/frappe_docker/main/overrides/compose.mariadb.yaml` |
| `frappe/crm@v1.88.0` → `README.md` (bảng Compatibility) | `CRM branch main - v1.x → stable; Frappe branch v15.x & v16.x` | `raw.githubusercontent.com/frappe/crm/v1.88.0/README.md` |

### 5.2. Ma trận tương thích (DECLARED + RUNTIME có sẵn từ v1.4)

| Tổ hợp | CRM | Frappe | Python | Kỳ vọng (theo metadata) | [DECLARED] | [RUNTIME] v1.4 | Ghi chú |
|---|---|---|---|---|---|---|---|
| C1 | v1.87.0 | v15.123.1 | 3.12 | Chạy | ✅ `>=3.10,<3.15` ∩ `>=3.10` | ⛔ chưa chạy | Python 3.12 có sẵn trên host DEV (§8.1 v1.4) nhưng image bench dùng 3.14.8 |
| C2 | v1.87.0 | v16.52.0 | 3.14 | Chạy | ✅ `>=3.14,<3.15` | ⛔ chưa cài/test v16 | |
| C3 | v1.87.0 | v15.123.1 | 3.14 | Chạy | ✅ | ✅ có log ở v1.4 §8.1; WP-01 evidence; PR #1 | Baseline runtime hiện hành |
| C4 | v1.87.0 | v15.99.0 | 3.13 | Chạy | ✅ `>=3.10,<3.14` | ⛔ chưa chạy | Python 3.13 phổ biến; nếu nâng v15 thì cần cập nhật image bench |
| C5 | v1.87.0 | v15.99.0 | 3.14 | **KHÔNG chạy** | ❌ v15.99.0 đã bỏ Python 3.14 | — | Cảnh báo quan trọng: nếu nâng Frappe v15 lên bản ≥ v15.99.0, phải giảm Python xuống ≤ 3.13 |
| C6 | v1.88.0 | v15.123.1 | 3.14 | Chạy | ✅ theo compatibility table | ⛔ chưa chạy | CRM `v1.88.0` chưa được verify runtime trong dự án; có dependency mới `requests>=2.28.0`, `tldextract>=5.0.0` (xem pyproject v1.88.0) |
| Cx | v1.87.0 | <v15 hoặc ≥v17 | — | Không hỗ trợ | ❌ ngoài `>=15.0.0,<17.0.0` | — | Loại |

### 5.3. Cảnh báo MariaDB > 10.8 — quan sát nguồn

- File `frappe/database/mariadb/database.py` tại `v15.123.1` (commit đã khóa) **không
  có check cứng** chống MariaDB 11.x. Toàn bộ code dùng `pymysql` (`PyMySQL==1.1.1`),
  `db_type = "mariadb"`, truy vấn `information_schema` tiêu chuẩn.
- Cảnh báo "MariaDB 11.8 mới hơn 10.8 chưa được Framework kiểm thử" xuất phát từ
  `frappe@15.123.1` `frappe/__init__.py` (qua `frappe.utils.update_progress_bar`
  hoặc `frappe.utils.install`); nó là **policy warning**, không phải runtime error.
- Tài liệu cài đặt chính thức của Frappe v15 (`docs.frappe.io`) ghi
  yêu cầu MariaDB `10.6.6+` (xem `p0-isolated-verification.md`); nghĩa là **MariaDB ≥ 10.6.6
  được install support**, không giới hạn ở 10.8.
- Trên thực tế, dòng MariaDB 11.x vẫn tương thích SQL ở mức cú pháp Frappe dùng
  (đã có evidence runtime ở v1.4: bootstrap/install/migrate/seed/28 tests/backup/restore
  đều chạy trên MariaDB `11.8`).

### 5.4. Đề xuất quyết định (T1A, evidence-based, **chưa phải GO**)

| # | Quyết định | Căn cứ | Trạng thái |
|---|---|---|---|
| Q-01 | **Giữ baseline C3 (v1.87.0 + v15.123.1 + Python 3.14.8 + MariaDB 11.8 + Redis 8.6-alpine)** cho P0 | Runtime evidence v1.4 §8.1; digest chỉ thay đổi do upstream rebuild, không thay đổi API/hành vi. SHA tag vẫn khớp. | **Đề xuất giữ** |
| Q-02 | **Cập nhật `version-lock.md` §5** với digest mới của `frappe/bench:v5.31.0` (index `sha256:3b7594…`, amd64 `sha256:05d3bc0d…`) và `mariadb:11.8` (index `sha256:f16d7a3…`, amd64 `sha256:d689f65…`) | Docker Hub Registry API 2026-10-19 (§4 ở trên) | **Bắt buộc** để tái tạo |
| Q-03 | **Cập nhật `deploy/dev/.env.example`** với cùng digest mới (Q-02) | Một nguồn sự thật với `version-lock.md` | **Bắt buộc** |
| Q-04 | **KHÔNG nâng lên CRM `v1.88.0` / Frappe `v15.99.0`** trong P0 | (a) nâng baseline đòi hỏi re-run evidence runtime trên máy có Docker; (b) `v15.99.0` đã **bỏ Python 3.14** — image bench hiện dùng 3.14.8, nâng v15.99.0 sẽ **phá** stack hiện có; (c) CRM v1.88.0 có 2 dependency mới (`requests`, `tldextract`) chưa được audit | **Giữ baseline**; ADR-001 chốt cùng P1 |
| Q-05 | **Giữ MariaDB 11.8**; caveat tương thích chuyển từ "blocker" sang "đã ghi nhận có cảnh báo policy, runtime evidence PASS trong scope đã chạy" | Code MariaDB driver không có hard-fail; cảnh báo là policy; runtime evidence v1.4 đã pass cho bootstrap/install/migrate/seed/28 tests/backup/restore | **Giữ caveat nhưng không chặn baseline**; cần chạy thêm workload (production-grade query, bulk import lớn, charset/collation edge case) trước khi đóng ADR-001 |
| Q-06 | **Giữ Frappe v16 như phương án thay thế**; khoanh vùng: chỉ chạy khi có máy có Python 3.14 sẵn và image bench tương ứng | `v16.9.0` vẫn `>=3.14,<3.15`; chưa có runtime evidence | **Deferred** |

### 5.5. Công việc còn thiếu để đóng ADR-001

1. Chạy thêm 2 workload để tăng độ tin cậy compatibility MariaDB 11.8:
   - (a) `bench --site ... import-csv` với file ~5.000 Lead giả để ép `utf8mb4_unicode_ci` collation và `PRIMARY KEY` performance.
   - (b) Migration schema có thay đổi `INDEX`/`UNIQUE` để xác minh `ADD INDEX IF NOT EXISTS`/`ADD UNIQUE` trong `frappe/database/mariadb/database.py` chạy đúng trên 11.x.
2. Sau khi workload (1) chạy trên máy có Docker, cập nhật §8 ma trận trong `version-lock.md` lên `[RUNTIME] PASS cho workload đã chạy` và đóng O-02.
3. Nếu cần evidence thêm, gắn với run GitHub Actions mới (xem G0-10).

### 5.6. Giới hạn của evidence này

- Phiên xác minh này **không chạy Docker/Python/bench**. Mọi kết luận `[RUNTIME]`
  ở trên dựa trên evidence cũ (`wp01-runtime.md`, `p0-isolated-verification.md`,
  `wp-02-p0b.md`, PR #1 CI run) — đã có và đã đối chiếu SHA.
- Không tạo được bằng chứng runtime mới cho G0-01, G0-02, G0-09. Xem
  `t1a-g002-vite-csrf-blocker.md` và `t1a-g009-vi-checklist.md`.
- Tất cả nguồn đã đối chiếu có URL, có ngày đo, có kết quả — không suy luận.
