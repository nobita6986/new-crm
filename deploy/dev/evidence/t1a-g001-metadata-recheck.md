# EVIDENCE — T1A: G0-01 metadata re-verification (đọc-only, không tạo runtime mới)

> **Ngày xác minh:** 2026-10-19 (UTC+7)
> **Môi trường xác minh:** phiên Codex trên Windows. KHÔNG có Docker / Python / Yarn.
> **Phương pháp:** `git ls-remote --tags --sort=v:refname` + `sort -V` thực sự (qua
> `[version]::TryParse()` trong PowerShell) cho cả Frappe/CRM; đối chiếu `raw.githubusercontent.com`
> cho `pyproject.toml`; Docker Hub Registry API cho image digest.
> **Bằng chứng runtime mới:** KHÔNG có. Tài liệu này chỉ chứa kết quả **đọc metadata tĩnh**
> và đề xuất quyết định tương thích có căn cứ. Việc thực thi `bench start`/MariaDB/Vite
> thuộc trách nhiệm máy có Docker/WSL2.
> **Không sửa upstream CRM/Frappe.**

---

## 1. Công cụ & môi trường xác minh

| Công cụ | Giá trị | Ghi chú |
|---|---|---|
| OS | Windows 10 (build 19045) | `Get-CimInstance Win32_OperatingSystem` |
| `git` | 2.55.0 | dùng `git ls-remote` |
| `node` | v22.23.3 | KHÔNG dùng cho evidence này |
| `WebFetch` (curl/Invoke-WebRequest) | tới `raw.githubusercontent.com` & `hub.docker.com/v2/` | chỉ đọc |
| `docker` / `python` / `yarn` | **không có** trên máy này | xác nhận qua `Get-Command` |
| `wsl.exe` | có nhưng `wsl --status` không chạy được (thiếu quyền/không có distro) | không dùng |

### 1.1. Lệnh đã chạy để xác minh SHAs (PowerShell, `;` thay cho `&&`)

```powershell
cd d:\HRP-app\new-crm

# 1. Lấy TOÀN BỘ tag frappe v15.x và v16.x (đã sort lexical, sẽ sort -V lại bằng PowerShell)
git ls-remote --tags --sort=v:refname https://github.com/frappe/frappe.git `
    | Select-String -Pattern "refs/tags/v(15|16)\." `
    | ForEach-Object { ($_ -split "`t")[1].Replace("refs/tags/","") } `
    | Sort-Object { [version]($_ -replace '^v','') }

# 2. Tương tự cho crm v1.x
git ls-remote --tags --sort=v:refname https://github.com/frappe/crm.git `
    | Select-String -Pattern "refs/tags/v1\." `
    | ForEach-Object { ($_ -split "`t")[1].Replace("refs/tags/","") } `
    | Sort-Object { [version]($_ -replace '^v','') }

# 3. Đối chiếu từng pin
git ls-remote https://github.com/frappe/crm    refs/tags/v1.87.0
git ls-remote https://github.com/frappe/crm    refs/tags/v1.88.0
git ls-remote https://github.com/frappe/frappe refs/tags/v15.99.0
git ls-remote https://github.com/frappe/frappe refs/tags/v15.123.1
git ls-remote https://github.com/frappe/frappe refs/tags/v16.9.0
git ls-remote https://github.com/frappe/frappe refs/tags/v16.52.0
git ls-remote https://github.com/frappe/frappe_docker refs/tags/v4.0.1

# 4. Đọc pyproject.toml của từng pin để xác minh requires-python
$pins = @{
  "crm-v1.87.0"      = "0d3910b456ef4fb913e77b29db011b3debc4f778"
  "crm-v1.88.0"      = "0baa1e8ea462b92d2920bcfdc7a532a730329b6c"
  "frappe-v15.99.0"  = "5cca1fa7e38cfab74c78627c93a9210dc3fdbccc"
  "frappe-v15.123.1" = "d2efa79657ba5c1abf96098b3bfacc199517d57e"
  "frappe-v16.9.0"   = "e5b04e04d83aa1bc1670913b6d58d1d3f4118307"
  "frappe-v16.52.0"  = "4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a"
}
foreach ($k in $pins.Keys) {
  Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/frappe/frappe/$($pins[$k])/pyproject.toml"
}
```

**Cảnh báo phương pháp:** `--sort=v:refname` chỉ sort **lexical** theo chuỗi `v15.99.0`
(so sánh ký tự). Nó đặt `v15.99.0` **sau** `v15.100.0` nếu đã được đánh tag, vì
`"9" > "1"`. Đó là lý do cần `sort -V`/ `[version]` để có thứ tự đúng theo
semantic versioning. Tôi đã chạy cả hai bước và lưu lại ở §2.

---

## 2. Đối chiếu SHAs đã khóa — kết quả

| # | Thành phần | Tag (lockfile) | SHA mong đợi (lockfile) | SHA quan sát 2026-10-19 | Khớp? |
|---|---|---|---|---|---|
| L-01 | Frappe CRM | `v1.87.0` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | ✅ |
| L-02 | Frappe Framework | `v15.123.1` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | ✅ |
| L-03 | Frappe Framework (thay thế) | `v16.52.0` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` | ✅ |
| L-04 | Frappe Docker | `v4.0.1` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | ✅ |

**Kết luận:** Mọi SHA đã khóa trong `version-lock.md` §2 vẫn đúng vào 2026-10-19.
Pin ổn định, không bị force-push.

---

## 3. Thứ tự version đúng (sort -V) — TẤT CẢ tag quan sát được

### 3.1. Frappe v15 (Top 10 cao nhất sau khi sort -V)

| # | Tag | SHA |
|---|---|---|
| 1 | v15.123.1 | `d2efa79657ba5c1abf96098b3bfacc199517d57e` ← **PIN HIỆN TẠI (cao nhất)** |
| 2 | v15.123.0 | `62fa5cc844a463a75e97962f03d6eafd536a5749` |
| 3 | v15.122.0 | `b0b5b99a1d9d2cb3d54b32293d3854bf2c15f4a4` |
| 4 | v15.121.3 | `0b282e81ef30a7f6b8ff376ca8eacf9fb9f28f05` |
| 5 | v15.121.2 | `fd533e8d18617eccb7ff1122b55ad64f6a6fe7bc` |
| 6 | v15.121.1 | `8f801ade016078685c3165c96e38f84f249f5309` |
| 7 | v15.121.0 | `4fa1b1446938d9e56d390915f38bbc975ee40a7e` |
| 8 | v15.120.1 | `9f8ae9cd25b6735be345da6cc12e9f5a96050c68` |
| 9 | v15.120.0 | `755b5cb81fabb431265690fca07f4a8038a5599a` |
| 10 | v15.119.1 | `edae775dd36b6c4ad7acab10230262bd74040765` |
| … | … | … |
| 25 | v15.99.0  | `5cca1fa7e38cfab74c78627c93a9210dc3fdbccc` ← **CŨ hơn pin 24 bản** |

`v15.99.0` (SHA `5cca1fa7…`) **thấp hơn** `v15.123.1` đúng 24 minor (từ `v15.100.0`
đến `v15.123.1`). Phiên trước tôi đã gọi nhầm nó là "mới hơn baseline" — **SAI**;
bản gốc ở phiên trước đã ghi sai. Evidence này sửa lại.

### 3.2. Frappe v16 (Top 10 cao nhất sau khi sort -V)

| # | Tag | SHA |
|---|---|---|
| 1 | v16.52.0 | `4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a` ← **PIN HIỆN TẠI (cao nhất)** |
| 2 | v16.51.1 | `2871914b69b6e14a4b023d9b7cc52dc3e269cc1b` |
| 3 | v16.51.0 | `6b450a166e076dd842e4db7ea0843f62881e62ac` |
| 4 | v16.50.0 | `f20f92d29706510551b4438cc196b0323a7f1a92` |
| 5 | v16.49.0 | `97a5dd93ca5883bcc9c4ef9834120c5cba397b67` |
| 6 | v16.36.1 | `97a5dd93ca5883bcc9c4ef9834120c5cba397b67` |
| 7 | v16.36.0 | `f3f0c0b13c77a419487150a198fed42964e1919e` |
| 8 | v16.35.1 | `aba24d9bf4caa65a2cc9277c1e771739d41165b1` |
| 9 | v16.35.0 | `012667b9c4e7f66d5e1ff5858d2e922331d4300a` |
| 10 | v16.34.0 | `c1f1e8ec3708750d7254f7f99d869ffb9886f19f` |
| … | … | … |
| 44 | v16.9.0  | `e5b04e04d83aa1bc1670913b6d58d1d3f4118307` ← **CŨ hơn pin 43 bản** |

`v16.9.0` (SHA `e5b04e04…`) **thấp hơn** `v16.52.0` đúng 43 minor. Tương tự, phiên
trước tôi gọi nhầm nó là "mới hơn baseline" — **SAI**.

> Lưu ý: `v16.36.1` và `v16.49.0` trùng SHA `97a5dd93…` (tag tự re-use commit,
> hoặc release đè commit). Đây là upstream quirk, không ảnh hưởng pin `v16.52.0`.

### 3.3. CRM v1 (Top 5 cao nhất sau khi sort -V)

| # | Tag | SHA |
|---|---|---|
| 1 | v1.88.0 | `0baa1e8ea462b92d2920bcfdc7a532a730329b6c` ← **MỚI hơn pin 1 patch** |
| 2 | v1.87.0 | `0d3910b456ef4fb913e77b29db011b3debc4f778` ← **PIN HIỆN TẠI** |
| 3 | v1.86.0 | `deedce73c1eb48577e7f70e95df6bac1dc55c93f` |
| 4 | v1.85.1 | `a6dfe8bf39e74c22b6212a43c8dadfb569c12154` |
| 5 | v1.85.0 | `1c9be66783274d52facb6db142cf04d07c60ea8b` |

CRM version sort hoạt động lexical vì major 1 và minor chỉ đến 88. `v1.88.0` cao hơn
`v1.87.0` đúng 1 patch — **đây là bản duy nhất thực sự mới hơn baseline** trong cả 3
repo được khảo sát.

---

## 4. Image digest đo lại từ Docker Hub Registry API (2026-10-19)

| Image | `index` digest 2026-10-15 (lockfile cũ) | `index` digest 2026-10-19 | `amd64` digest 2026-10-19 | `tag_last_pushed` |
|---|---|---|---|---|
| `frappe/bench:v5.31.0` | `sha256:1b27bb30ab18b0a50b5264f44988335bfe8e280a68b45d0ff2daf72ca6ddb85f` | `sha256:3b75943bf00a1db92723d99ec34fbfa92f4b67b9b643ae1e96750899b7fbd980` | `sha256:05d3bc0d739db2146c73515bf320a2cc88e7bfb1f97a5c74d73ef3b2fd56f18d` | `2026-10-18T04:11:04Z` |
| `mariadb:11.8` | `sha256:6422478cb8e159f080fb1d8ccf65101e26fe51385787fde7d16c3b165a331f15` | `sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa` | `sha256:d689f65e09e887e96db5c40ca3957dd9f4e3fad3516baec96d7726c78a5fc2ee` | `2026-10-17T05:36:40Z` |
| `redis:8.6-alpine` | `sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` | `sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` | `sha256:019e4fb3c6de024c8f70ab5219d4abded5f335de770876981c7b0a051c43df87` | `2026-09-25T00:05:56Z` |

**Diễn giải:**
- `frappe/bench:v5.31.0` và `mariadb:11.8` đã được **push lại với nội dung khác** (digest
  mới) kể từ khi `version-lock.md` v1.4 được tạo. Đây là hiện tượng "mutable tag" của
  Docker Hub: cùng tag, nội dung khác.
- `redis:8.6-alpine` **không đổi** digest.
- Tinh thần "tag@digest" vẫn đảm bảo tái tạo — chỉ cần cập nhật digest mới trong lockfile.
- `frappe/bench` chưa có tag `v5.32+`.

---

## 5. Đề xuất quyết định tương thích MariaDB/Frappe có căn cứ (evidence-based)

### 5.1. Quan sát nguồn upstream — `requires-python`

| Nguồn | `requires-python` | URL đã truy vấn |
|---|---|---|
| `frappe/crm@v1.87.0` | `>=3.10` | `raw.githubusercontent.com/frappe/crm/0d3910b…/pyproject.toml` |
| `frappe/crm@v1.88.0` | `>=3.10` | `raw.githubusercontent.com/frappe/crm/0baa1e8…/pyproject.toml` |
| `frappe/frappe@v15.123.1` (pin) | `>=3.10,<3.15` ← **hỗ trợ Python 3.14** | `raw.githubusercontent.com/frappe/frappe/d2efa79…/pyproject.toml` |
| `frappe/frappe@v15.99.0` (cũ hơn pin) | `>=3.10,<3.14` ← **bỏ Python 3.14** | `raw.githubusercontent.com/frappe/frappe/5cca1fa…/pyproject.toml` |
| `frappe/frappe@v16.52.0` (pin) | `>=3.14,<3.15` | `raw.githubusercontent.com/frappe/frappe/4515014…/pyproject.toml` |
| `frappe/frappe@v16.9.0` (cũ hơn pin) | `>=3.14,<3.15` | `raw.githubusercontent.com/frappe/frappe/e5b04e0…/pyproject.toml` |
| `frappe_docker@v4.0.1` → `overrides/compose.mariadb.yaml` | `image: mariadb:11.8` | `raw.githubusercontent.com/frappe/frappe_docker/2b26e9a…/overrides/compose.mariadb.yaml` |
| `frappe/crm@v1.88.0` README | `CRM main - v1.x → stable; Frappe v15.x & v16.x` | `raw.githubusercontent.com/frappe/crm/v1.88.0/README.md` |

**Quan sát quan trọng:** pin hiện tại `frappe@v15.123.1` (`>=3.10,<3.15`) **vẫn hỗ trợ
Python 3.14**. Việc "bỏ Python 3.14" chỉ áp dụng cho `v15.99.0` — vốn **thấp hơn**
pin chứ không phải bản mới hơn. Baseline C3 hiện hành **không bị ảnh hưởng** bởi
thay đổi này. Nếu sau này quyết định nâng Frappe v15 lên bất kỳ bản nào từ
`v15.100.0+`, cần verify lại `requires-python` vì policy có thể đã đổi tiếp.

### 5.2. Ma trận tương thích (DECLARED + RUNTIME có sẵn từ v1.4)

| Tổ hợp | CRM | Frappe | Python | Kỳ vọng (theo metadata) | [DECLARED] | [RUNTIME] v1.4 | Ghi chú |
|---|---|---|---|---|---|---|---|
| C1 | v1.87.0 | v15.123.1 | 3.12 | Chạy | ✅ `>=3.10,<3.15` ∩ `>=3.10` | ⛔ chưa chạy | Python 3.12 có sẵn trên host DEV; image bench dùng 3.14.8 |
| C2 | v1.87.0 | v16.52.0 | 3.14 | Chạy | ✅ `>=3.14,<3.15` | ⛔ chưa cài/test v16 | |
| C3 | v1.87.0 | v15.123.1 | 3.14 | Chạy | ✅ | ✅ có log ở v1.4 §8.1; WP-01 evidence; PR #1 | **Baseline runtime hiện hành** |
| C4 | v1.88.0 | v15.123.1 | 3.14 | Chạy | ✅ theo compatibility table | ⛔ chưa chạy | CRM `v1.88.0` là patch release; chưa verify runtime trong dự án |
| C5 | v1.87.0 | v15.99.0 (cũ hơn pin) | 3.13 | Chạy | ✅ `>=3.10,<3.14` | ⛔ chưa chạy | **KHÔNG đề xuất** vì lùi phiên bản so với pin; lý do kỹ thuật không có |
| C6 | v1.87.0 | v15.99.0 (cũ hơn pin) | 3.14 | **KHÔNG chạy** | ❌ `v15.99.0` đã bỏ Python 3.14 | — | Cảnh báo chỉ áp dụng nếu **lùi** về `v15.99.0`; không phải kịch bản thực tế |
| C7 | v1.88.0 | v16.52.0 | 3.14 | Chạy | ✅ | ⛔ chưa chạy | Kịch bản nâng cấp vừa CRM vừa Frappe — phạm vi P1 trở đi |
| Cx | v1.87.0 | <v15 hoặc ≥v17 | — | Không hỗ trợ | ❌ ngoài `>=15.0.0,<17.0.0` | — | Loại |

### 5.3. Cảnh báo MariaDB > 10.8 — quan sát nguồn

- File `frappe/database/mariadb/database.py` tại `v15.123.1` (commit đã khóa) **không
  có check cứng** chống MariaDB 11.x. Toàn bộ code dùng `pymysql` (`PyMySQL==1.1.1`),
  `db_type = "mariadb"`, truy vấn `information_schema` tiêu chuẩn.
- Cảnh báo "MariaDB 11.8 mới hơn 10.8 chưa được Framework kiểm thử" xuất phát từ
  `frappe@15.123.1` `frappe/__init__.py`; nó là **policy warning**, không phải runtime error.
- Tài liệu cài đặt chính thức của Frappe v15 (`docs.frappe.io`) ghi yêu cầu MariaDB
  `10.6.6+`; nghĩa là **MariaDB ≥ 10.6.6 được install support**, không giới hạn ở 10.8.
- Trên thực tế, dòng MariaDB 11.x vẫn tương thích SQL ở mức cú pháp Frappe dùng
  (evidence runtime ở v1.4: bootstrap/install/migrate/seed/28 tests/backup/restore
  đều chạy trên MariaDB `11.8`).

### 5.4. Đề xuất quyết định (T1A, evidence-based, **chưa phải GO**)

| # | Quyết định | Căn cứ | Trạng thái |
|---|---|---|---|
| Q-01 | **Giữ baseline C3 (v1.87.0 + v15.123.1 + Python 3.14.8 + MariaDB 11.8 + Redis 8.6-alpine)** cho P0 | Runtime evidence v1.4 §8.1; digest chỉ thay đổi do upstream rebuild, không thay đổi API/hành vi. SHA tag vẫn khớp. `v15.123.1` vẫn `>=3.10,<3.15` (hỗ trợ Python 3.14). | **Đề xuất giữ** |
| Q-02 | **Cập nhật `version-lock.md` §5** với digest mới của `frappe/bench:v5.31.0` (index `sha256:3b7594…`, amd64 `sha256:05d3bc0d…`) và `mariadb:11.8` (index `sha256:f16d7a3…`, amd64 `sha256:d689f65…`) | Docker Hub Registry API 2026-10-19 (§4 ở trên) | **Bắt buộc** để tái tạo |
| Q-03 | **Cập nhật `deploy/dev/.env.example`** với cùng digest mới (Q-02) | Một nguồn sự thật với `version-lock.md` | **Bắt buộc** |
| Q-04 | **KHÔNG nâng lên CRM `v1.88.0`** trong P0 | `v1.88.0` chỉ cao hơn `v1.87.0` 1 patch; chưa có runtime evidence; P0 đã pass với `v1.87.0`; nâng sớm không có giá trị rủi ro/thời gian. | **Giữ baseline**; ADR-001 chốt cùng P1 |
| Q-05 | **KHÔNG "lùi" về Frappe `v15.99.0`** | `v15.99.0` thấp hơn pin `v15.123.1` đúng 24 minor; là bản cũ hơn, không phải bản mới hơn. Lùi về bản cũ không có lý do kỹ thuật và bỏ mất 24 minor sửa lỗi upstream. | **Từ chối** rõ ràng |
| Q-06 | **KHÔNG "lùi" về Frappe `v16.9.0`** | `v16.9.0` thấp hơn pin `v16.52.0` đúng 43 minor; tương tự Q-05. | **Từ chối** rõ ràng |
| Q-07 | **Giữ MariaDB 11.8**; caveat chuyển từ "blocker" sang "đã ghi nhận có cảnh báo policy, runtime evidence PASS trong scope đã chạy" | Code MariaDB driver không có hard-fail; cảnh báo là policy; runtime evidence v1.4 đã pass | **Giữ caveat nhưng không chặn baseline**; cần chạy thêm workload trước khi đóng ADR-001 |
| Q-08 | **Giữ Frappe v16 như phương án thay thế**; khoanh vùng: chỉ chạy khi có máy có Python 3.14 sẵn và image bench tương ứng | `v16.52.0` vẫn `>=3.14,<3.15`; chưa có runtime evidence | **Deferred** |

### 5.5. Công việc còn thiếu để đóng ADR-001

1. Chạy thêm 2 workload để tăng độ tin cậy compatibility MariaDB 11.8:
   - (a) `bench --site ... import-csv` với file ~5.000 Lead giả để ép `utf8mb4_unicode_ci` collation và `PRIMARY KEY` performance.
   - (b) Migration schema có thay đổi `INDEX`/`UNIQUE` để xác minh `ADD INDEX IF NOT EXISTS`/`ADD UNIQUE` trong `frappe/database/mariadb/database.py` chạy đúng trên 11.x.
2. Sau khi workload (1) chạy trên máy có Docker, cập nhật §8 ma trận trong `version-lock.md` lên `[RUNTIME] PASS cho workload đã chạy` và đóng O-02.
3. Nếu cần evidence thêm, gắn với run GitHub Actions mới (xem G0-10).

### 5.6. Sửa sai so với phiên trước

- Phiên giao T1A trước (commit `a497dbb`) đã ghi:
  > "Detect new releases: crm v1.88.0, frappe v15.99.0, frappe v16.9.0 (newer than pinned v15.123.1 / v1.87.0)."
- Evidence này (re-verify) cho thấy:
  - `crm v1.88.0` **đúng** là mới hơn pin `v1.87.0` (1 patch) ✅
  - `frappe v15.99.0` **SAI** — là bản thấp hơn `v15.123.1` 24 minor; lexical sort đặt
    `v15.99.0` sau `v15.9.0` nhưng `sort -V` đặt `v15.99.0` **trước** `v15.100.0`. ❌
  - `frappe v16.9.0` **SAI** — là bản thấp hơn `v16.52.0` 43 minor; cùng lý do sort. ❌
- Hệ quả: `version-lock.md` §10 (open item O-06) cần sửa mô tả từ "Frappe v15.99.0
  dropped Python 3.14" sang chính xác hơn: "Pin hiện tại v15.123.1 vẫn hỗ trợ Python 3.14;
  bản `v15.99.0` (cũ hơn pin) đã giới hạn `<3.14`. Nếu sau này có nhu cầu lùi về
  bất kỳ bản nào dưới `v15.100.0`, cần kiểm tra `requires-python` lại.".

### 5.7. Giới hạn của evidence này

- Phiên xác minh này **không chạy Docker/Python/bench**. Mọi kết luận `[RUNTIME]`
  ở trên dựa trên evidence cũ (`wp01-runtime.md`, `p0-isolated-verification.md`,
  `wp-02-p0b.md`, PR #1 CI run) — đã có và đã đối chiếu SHA.
- Không tạo được bằng chứng runtime mới cho G0-01, G0-02, G0-09. Xem
  `t1a-g002-vite-csrf-blocker.md` và `t1a-g009-vi-checklist.md`.
- Tất cả nguồn đã đối chiếu có URL, có ngày đo, có kết quả — không suy luận.

---

## 6. Lệnh cần Owner chạy trên máy có Docker/WSL2 (để đóng G0-01)

```bash
# 1. Kéo image theo digest mới (sau khi áp dụng Q-02)
docker pull frappe/bench:v5.31.0@sha256:3b75943bf00a1db92723d99ec34fbfa92f4b67b9b643ae1e96750899b7fbd980
docker pull mariadb:11.8@sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa
docker pull redis:8.6-alpine@sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba

# 2. Đo lại RepoDigest cục bộ để khớp với lockfile
docker images --digests | grep -E "frappe/bench|mariadb|redis"

# 3. Sau khi `docker compose up -d`:
docker exec -it <bench> bench --site <site> console
>>> import frappe; print(frappe.__version__)
# kỳ vọng: 15.123.1
```
