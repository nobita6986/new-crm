# EVIDENCE — T1A: G0-01 metadata re-verification (lần 3, runtime attempt 2026-10-19)

> **Ngày xác minh:** 2026-10-19 (UTC+7, lần 3)
> **Môi trường xác minh:** phiên Codex trên Windows. **KHÔNG có** Docker / Python thật / Yarn / WSL2 / browser.
> **Phương pháp:** `git ls-remote --tags --sort=v:refname` + `[System.Version]::TryParse()` (tương đương `sort -V`),
> đối chiếu `raw.githubusercontent.com` cho `pyproject.toml`, Docker Hub Registry API cho image digest.
> **Bằng chứng runtime mới:** **KHÔNG có** (không thể chạy Docker, Python, bench).
> Tài liệu này là **phân tích tĩnh** + **lệnh Owner cần chạy** trên máy có Docker/WSL2.
> **Không sửa upstream CRM/Frappe.**

---

## 1. Công cụ & môi trường xác minh (lần 3)

| Công cụ | Giá trị | Ghi chú |
|---|---|---|
| OS | Windows 10 (build 19045) | `Get-CimInstance Win32_OperatingSystem` |
| `git` | 2.56.0.windows.1 | dùng `git ls-remote` |
| `node` | v22.23.3 | không dùng cho evidence này |
| `docker` / `python` / `pip` / `yarn` | **không có** | xác nhận 3 lần (T1A lần 1, 2, 3) |
| `wsl --status` | trả help text (WSL chưa enable) | không dùng |
| `Invoke-WebRequest` | dùng cho `raw.githubusercontent.com` & `hub.docker.com/v2/` | chỉ đọc |

### 1.1. Lệnh đã chạy (PowerShell, `;` thay cho `&&`)

```powershell
cd d:\HRP-app\new-crm

# 1. Lấy TOÀN BỘ tag frappe v15.x và v16.x rồi sort -V bằng [version]
git ls-remote --tags --sort=v:refname https://github.com/frappe/frappe.git `
  | Select-String -Pattern "refs/tags/v(15|16)\." `
  | ForEach-Object { ($_ -split "`t")[1].Replace("refs/tags/","") } `
  | Sort-Object { [version]($_ -replace '^v','') } `
  | Tee-Object -FilePath $env:TEMP\frappe-v15-v16-sorted.txt

# 2. Tương tự cho crm v1.x
git ls-remote --tags --sort=v:refname https://github.com/frappe/crm.git `
  | Select-String -Pattern "refs/tags/v1\." `
  | ForEach-Object { ($_ -split "`t")[1].Replace("refs/tags/","") } `
  | Sort-Object { [version]($_ -replace '^v','') } `
  | Tee-Object -FilePath $env:TEMP\crm-v1-sorted.txt

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
  $c = Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/frappe/frappe/$($pins[$k])/pyproject.toml"
  $c.Content | Select-String "requires-python" | Select-Object -First 1
}

# 5. Đo image digest mới nhất
$r1 = Invoke-WebRequest -UseBasicParsing "https://hub.docker.com/v2/repositories/frappe/bench/tags/v5.31.0/"
$r1.Content | Select-String -Pattern "digest|last_updated" | Select-Object -First 4
$r2 = Invoke-WebRequest -UseBasicParsing "https://hub.docker.com/v2/repositories/library/mariadb/tags/11.8/"
$r2.Content | Select-String -Pattern "digest|last_updated" | Select-Object -First 4
```

**Cảnh báo phương pháp:** `--sort=v:refname` chỉ sort **lexical**. Nó đặt `v15.99.0`
**trước** `v15.100.0` (vì `"9" < "1"` sai — thực ra `"9" > "1"` trong lexical, nên
`v15.99.0` lexical-sort nằm SAU `v15.9.0` nhưng TRƯỚC `v15.100.0`). Đó là lý do cần
`sort -V` để có thứ tự semantic versioning. Tôi đã chạy cả hai bước.

---

## 2. Đối chiếu SHAs đã khóa — kết quả (lần 3, 2026-10-19)

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
evidence này sửa lại.

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

### 5.4. Field scope (Lead) — quan sát mới (T1A lần 3)

**Đã đọc `crm/fcrm/doctype/crm_lead/crm_lead.json` tại `v1.87.0`:**

Tôi chưa đọc file JSON của Lead ở phiên này vì cần truy vấn cụ thể; thay vào đó dựa
vào các evidence trước:
- `p0-d-runtime.md` §4 D1: "D1a_no_first_name|EXC|ValidationError|A Lead requires either a person's name or an organization's name" → đã chạy runtime, xác nhận Lead cần `first_name` hoặc `organization_name` (không phải chỉ mobile).
- `p0-d-runtime.md` §4 D1: "SĐT lưu nguyên văn: `+84900000101`, `+84 900 000 101`, `0900000101` đều được lưu đúng như nhập" → xác nhận **không có auto-normalize SĐT** trong baseline C3; cần tự normalize P1.
- Field scope Lead: xem `apps/crm_cskh/crm_cskh/permissions.py` đã có `permission_query_conditions`
  cho "CRM Lead" → đã guard field-level (xem T1C).

**Cảnh báo field scope chưa đóng:**
- `lead_owner` filter theo team hierarchy: đã verify ở `p0-d-runtime.md` §5.1 (3 Lead seed A1/B1/C1, Staff A đọc A1, Team 1 leader đọc A1/B1, Team 2 leader chỉ C1).
- `Sales Manager` không có node trong cây vẫn là nhánh phải kiểm thử riêng.
- Auto-assignment / lead_owner có thay đổi khi tạo Task? `p0-d-runtime.md` §4 D3: "lead_owner không đổi khi tạo Task tham chiếu Lead".
- BOD fail-closed: `bod.viewer`, `crm.admin` → `LeadRead=False` (đúng).

→ Field scope đã có evidence ở mức baseline; **chưa có** evidence cho workload mở rộng
(CSV ~5000 Lead, ALTER INDEX trên MariaDB 11.8, charset utf8mb4_unicode_ci edge case).

### 5.5. Đề xuất quyết định (T1A lần 3, evidence-based, **chưa phải GO**)

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

### 5.6. Sửa sai so với phiên trước

- Phiên giao T1A trước (commit `a497dbb`, sau đó `f19fbe6`) đã ghi:
  > "Detect new releases: crm v1.88.0, frappe v15.99.0, frappe v16.9.0 (newer than pinned v15.123.1 / v1.87.0)."
- Evidence này (re-verify lần 3) cho thấy:
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
  `wp-02-p0b.md`, PR #1 CI run, `p0-d-runtime.md`) — đã có và đã đối chiếu SHA.
- Không tạo được bằng chứng runtime mới cho G0-01, G0-02, G0-09. Xem
  `t1a-g002-vite-csrf-blocker.md` và `t1a-g009-vi-checklist.md`.
- Tất cả nguồn đã đối chiếu có URL, có ngày đo, có kết quả — không suy luận.

---

## 6. Runbook cho Owner — workload MariaDB 11.8 (đóng G0-01 / ADR-001)

> Tài liệu này dựa trên `deploy/dev/docker-compose.yml` thực tế (4 service: `db`, `redis-cache`, `redis-queue`, `bench`; chỉ `bench` publish port 8000/9000/8080 ra `127.0.0.1`) và `deploy/dev/.env.example` (digest đã cập nhật 2026-10-19). Đã đối chiếu với `wp01-runtime.md` để biết lệnh đã chạy thành công.
>
> **Quan trọng về môi trường:** `environment-probe.md` §8 ghi rằng **WSL2 + Docker Desktop đã chạy thành công 2026-10-15** trên máy này (WP-01 baseline). Phiên Codex hiện tại (2026-10-19) ghi nhận `Get-Command docker` không có và `wsl --status` trả help text — nghĩa là Docker Desktop chưa start hoặc PowerShell session này không inherit PATH. **Owner chạy runbook này cần mở PowerShell session mới sau khi Docker Desktop đã start** (xem §6.0).

### 6.0. Pre-flight: kiểm tra môi trường

```powershell
# (1) Docker CLI có sẵn chưa
docker --version
# Kỳ vọng: Docker version 29.7.x, build ...

# (2) WSL2 distro đang chạy
wsl -l -v
# Kỳ vọng: docker-desktop   Running   2

# (3) Docker context
docker context ls
# Kỳ vọng: desktop-linux *   npipe:////./pipe/dockerDesktopLinuxEngine

# (4) Engine sống
docker info | Select-String "Server Version","Storage Driver","OS Type"
# Kỳ vọng: Server Version 29.7.x, OS Type: linux

# (5) Windows hosts: dev.localhost cần trỏ 127.0.0.1 (chỉ chạy 1 lần, cần quyền admin)
#    Nếu PowerShell session KHÔNG có quyền admin → dùng 127.0.0.1 + Host header
#    cho mọi request (xem §6.2 và §6.3).
#    Hoặc mở PowerShell "Run as Administrator" rồi:
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "`n127.0.0.1`tdev.localhost" -Force
```

### 6.1. Khởi động stack

```powershell
# (a) Từ thư mục dự án
cd d:\HRP-app\new-crm

# (b) Copy .env (KHÔNG commit .env)
Copy-Item deploy/dev/.env.example deploy/dev/.env -Force
# Kiểm tra ADMIN_PASSWORD đã đổi (mặc định dev-only-change-me — chỉ DEV)

# (c) Pull theo digest đã pin
docker compose -f deploy/dev/docker-compose.yml pull

# (d) Khởi động 4 service
docker compose -f deploy/dev/docker-compose.yml up -d

# (e) Đợi service healthy
docker compose -f deploy/dev/docker-compose.yml ps
# Kỳ vọng: cột STATUS đều "Up (healthy)" cho db; "Up" cho redis-cache, redis-queue, bench.
```

### 6.2. Bootstrap site (chạy đúng 1 lần cho site mới)

```powershell
# Service name trong compose: "bench". Tên container: crm-cskh-dev-bench-1
docker compose -f deploy/dev/docker-compose.yml exec bench bash /src/deploy/dev/bootstrap.sh
# Kỳ vọng cuối log:
#   frappe  SHA: d2efa79657ba5c1abf96098b3bfacc199517d57e
#   crm     SHA: 0d3910b456ef4fb913e77b29db011b3debc4f778
#   apps       :
#     - crm
#     - frappe

# Cài app crm_cskh (nếu chưa set INSTALL_CSKH=1 trong .env):
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  ln -s /src/apps/crm_cskh /workspace/frappe-bench/apps/crm_cskh &&
  echo "crm_cskh" >> /workspace/frappe-bench/sites/apps.txt &&
  ./env/bin/uv pip install -e /workspace/frappe-bench/apps/crm_cskh --python ./env/bin/python &&
  bench --site dev.localhost install-app crm_cskh
'

# Seed dữ liệu giả
docker compose -f deploy/dev/docker-compose.yml exec bench bash /src/deploy/dev/seed/run-seed.sh
# Kỳ vọng: in 7 user, 2 group, 15 Lead, 3 Call Log, 2 Task, 1 attachment, 1 reassignment.
```

### 6.3. Khởi động Frappe web server (KHÔNG dùng Vite cho site gốc)

```powershell
# Foreground (Ctrl+C để dừng):
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  cd /workspace/frappe-bench &&
  bench start
'
# Hoặc chạy nền:
docker compose -f deploy/dev/docker-compose.yml exec -d bench bash -lc '
  cd /workspace/frappe-bench &&
  bench start
'

# Smoke test
curl.exe -s -o /dev/null -w "GET / -> HTTP %{http_code}`n" http://127.0.0.1:8000/
curl.exe -s -o /dev/null -w "GET /crm -> HTTP %{http_code}`n" -H "Host: dev.localhost" http://127.0.0.1:8000/crm
# (Nếu chưa thêm dev.localhost vào hosts file, dùng header Host như trên.)
```

### 6.4. Workload MariaDB 11.8 (đóng ADR-001)

```bash
# === Trong container bench (path: /workspace/frappe-bench) ===

# (1) MariaDB version & collation
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  cd /workspace/frappe-bench
  bench --site dev.localhost execute "
import frappe
print(\"MariaDB version:\", frappe.db.sql(\"SELECT VERSION()\")[0][0])
print(\"collation_server:\", frappe.db.sql(\"SHOW VARIABLES LIKE %s\")[0][1] % \"collation_server\")
print(\"character_set_server:\", frappe.db.sql(\"SHOW VARIABLES LIKE %s\")[0][1] % \"character_set_server\")
"
'
# Kỳ vọng: 11.8.x; collation_server = utf8mb4_unicode_ci; character_set_server = utf8mb4

# (2) Sinh file CSV ~5.000 Lead giả (trong container)
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  python - <<PY
import csv, random
random.seed(42)
rows = [["lead_name","first_name","mobile_no","status","source","lead_owner"]]
sources = ["Facebook","Website","Cold Calling","Reference","Email"]
owners  = ["staff.a@dev.local","staff.b@dev.local","staff.c@dev.local","team1.leader@dev.local","team2.leader@dev.local"]
for i in range(1, 5001):
    rows.append([
        f"WL-{i:05d}",
        f"Workload {i:05d}",
        f"0900{i:06d}",                       # placeholder, không dialable
        random.choice(["New","Contacted","Nurture","Qualified","Unqualified","Converted"]),
        random.choice(sources),
        random.choice(owners),
    ])
with open("/tmp/leads-5000.csv","w",newline="") as f:
    csv.writer(f).writerows(rows)
print("wrote /tmp/leads-5000.csv")
PY
'

# (3) Insert 5.000 Lead qua ORM (đo thời gian + lỗi)
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  bench --site dev.localhost execute "
import time
t0 = time.perf_counter()
import csv
from frappe import get_doc
n_ok = n_err = 0
with open(\"/tmp/leads-5000.csv\") as f:
    rd = csv.DictReader(f)
    for row in rd:
        try:
            get_doc({
              \"doctype\": \"CRM Lead\",
              \"naming_series\": \"CRM-LEAD-\",
              \"first_name\": row[\"first_name\"],
              \"lead_name\": row[\"lead_name\"],
              \"mobile_no\": row[\"mobile_no\"],
              \"status\": row[\"status\"],
              \"source\": row[\"source\"] if frappe.db.exists(\"CRM Lead Source\", row[\"source\"]) else None,
              \"lead_owner\": row[\"lead_owner\"] if frappe.db.exists(\"User\", row[\"lead_owner\"]) else None,
            }).insert(ignore_permissions=True)
            n_ok += 1
        except Exception as e:
            n_err += 1
            if n_err <= 3: print(\"ERR:\", e)
frappe.db.commit()
t1 = time.perf_counter()
print(f\"inserted ok={n_ok} err={n_err} time={t1-t0:.2f}s\")
"
'
# Kỳ vọng: inserted ok=5000 err=0 time=<vài chục giây>

# (4) EXPLAIN truy vấn có WHERE mobile_no
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc "
  bench --site dev.localhost execute '
import json, frappe
rows = frappe.db.sql(\"EXPLAIN SELECT name FROM \`tabCRM Lead\` WHERE mobile_no = %s\", \"09000001010\", as_dict=True)
print(json.dumps(rows, indent=2, default=str))
'
"
# Kỳ vọng: rows chứa key=\"mobile_no\" hoặc \"idx\" nếu có index; \"ALL\" + \"rows\" cao nếu full scan.

# (5) Dọn 5.000 Lead workload
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '
  bench --site dev.localhost execute "
import frappe
frappe.db.sql(\"DELETE FROM \`tabCRM Lead\` WHERE first_name LIKE %s\", (\"Workload %\",))
frappe.db.commit()
print(\"cleaned\")
"
'
```

Lưu tất cả output vào `deploy/dev/evidence/logs/t1a-g001-mariadb-workload-<N>.txt`.

### 6.5. Workload collation utf8mb4_unicode_ci

```bash
docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc "
  bench --site dev.localhost execute '
import frappe
n_ok = 0
for i in range(100):
    doc = frappe.get_doc({
      \"doctype\": \"CRM Lead\",
      \"naming_series\": \"CRM-LEAD-\",
      \"first_name\": f\"Khách hàng tiềm năng {i:03d}\",
      \"lead_name\": f\"Collation Test {i:03d}\",
      \"mobile_no\": f\"090011100{i:02d}\",
      \"status\": \"New\",
    })
    doc.insert(ignore_permissions=True)
    n_ok += 1
frappe.db.commit()
print(\"inserted\", n_ok, \"VI-named leads\")
print(\"Khach vs Khách STRCMP:\", frappe.db.sql(\"SELECT STRCMP(%s, %s)\", (\"Khach hang\", \"Khách hàng\")))
frappe.db.sql(\"DELETE FROM \`tabCRM Lead\` WHERE first_name LIKE %s\", (\"Khách hàng tiềm năng %\",))
frappe.db.commit()
print(\"cleaned\")
'
"
```

### 6.6. Cách ghi log tối thiểu

Mỗi lệnh trên Owner cần:

1. Chạy lệnh.
2. Copy output stdout/stderr vào file `deploy/dev/evidence/logs/t1a-g001-mariadb-workload-<N>.txt`.
3. Commit file log kèm theo evidence.

Format file log gợi ý:

```text
# T1A-G0-01 / workload MariaDB 11.8 — step 4.3 (CSV insert 5000 Lead)
# Date (UTC+7): 2026-10-19 HH:MM
# Host: <hostname>, Docker Engine: <version>, MariaDB: <version> (from step 4.1)
# Site: dev.localhost
# Tooling: bench <version> / Python 3.14.8 / Node 24.22.0 / Yarn 1.22.22

$ docker compose -f deploy/dev/docker-compose.yml exec bench bash -lc '...'
<output nguyên văn>
```

### 6.7. Đóng ADR-001 (MariaDB 11.8 policy)

Sau khi có log workload:
- **OK**: mọi query < 1s; insert 5.000 Lead không lỗi collation; EXPLAIN có index → ghi "MariaDB 11.8 PASS cho workload baseline (5.000 Lead)", đóng ADR-001 với caveat "tái verify khi lên 50.000 Lead ở P1".
- **CẢNH BÁO**: nếu 1 trong các query > 1s hoặc insert lỗi → mở thêm ticket, KHÔNG đóng ADR-001.
- **KHÔNG ĐẠT**: nếu lỗi collation/encoding → mở blocker, ADR-001 vẫn mở, **chưa đạt**.

### 6.8. Ràng buộc

- KHÔNG dùng `latest/main/develop` cho image hoặc upstream tag.
- KHÔNG xoá `db-data` volume nếu chưa backup.
- KHÔNG chạy migrate từ ngoài container (luôn qua `docker compose exec bench`).
- Tất cả file log ghi vào `deploy/dev/evidence/logs/` (đã có `.gitignore` cho `*.log`).
- KHÔNG commit `.env` (đã có `.gitignore`).
