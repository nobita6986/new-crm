# ADR-P1-07 — Chia quyền BOD / Leader / Admin: least privilege, scope do backend quyết định

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-07-rbac.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §8, §16; kế thừa `adr-005-bod-viewer-scope.md` của P0)
> **Work package:** P1-WP07/WP08

---

## 1. Cơ sở P0 (đã chốt, không mở lại)

`docs/architecture/adr-005-bod-viewer-scope.md` đã chốt:

- **`CSKH BOD Viewer`** = role nghiệp vụ, chỉ **đọc** báo cáo toàn công ty, **không** dùng
  `System Manager`.
- **`CSKH CRM Admin`** = role quản trị **nghiệp vụ CSKH** (kho, import, campaign, phân phối,
  ownership có audit), **không** quản trị cấu hình hệ thống Frappe.

P0 đã chứng minh fail-closed cho hai role này:
`test_bod_viewer_cannot_read_leads_or_manage_users` và
`test_crm_admin_role_fails_closed_until_controlled_service_exists` (trong
`apps/crm_cskh/crm_cskh/tests/test_permissions.py`).

**⇒ Yêu cầu của người dùng cho P1 — "CRM Admin là role nghiệp vụ, không dùng Administrator thay
actor CRM-ADMIN và không cấp System Manager cho BOD" — chính là giữ đúng ADR-005 P0.** Không
được nới.

---

## 2. Ma trận quyền P1 (P1 §8.1)

| Tác vụ | BOD Viewer | CRM Admin | Leader | Staff |
|---|---|---|---|---|
| Xem tổng quan phân phối | Toàn hệ thống | Toàn hệ thống | Đội mình | Cá nhân |
| Xem số khách chưa giao / pool | Tổng số (không PII) | Có chi tiết | Phạm vi ủy quyền | Không |
| Import file | Không | Có | Chỉ nếu ủy quyền riêng | Không |
| Xem file import thô / lỗi | Không mặc định | Có | Không mặc định | Không |
| Tạo/sửa campaign/rule | Không | Có | Không mặc định | Không |
| Preview & execute phân phối | Không | Có | Chỉ nếu ủy quyền rõ | Không |
| Reassign/revoke | Không | Có | Nội bộ team nếu được ủy quyền | Không |
| Xem danh sách khách có PII | Theo quyền BOD cụ thể | Có | Team mình | Chỉ đang sở hữu |
| Xem assignment history | Drill-down được duyệt | Có | Trong team | Giới hạn hiện tại |
| Export danh sách khách | Theo quyền export riêng | Theo quyền được cấp | Theo policy team | Mặc định tắt |

---

## 3. Quyết định cơ chế

### 3.1. Không có "role gộp" — tách 2 trục

Quyền P1 được quyết định bởi **hai trục độc lập**:

1. **Vai trò nghiệp vụ** (`CSKH CRM Admin`, `CSKH BOD Viewer`, `Sales Manager`, `Sales User`).
2. **Scope** (campaign, team, work item mà user được phép chạm).

⇒ `CSKH CRM Admin` **không** đồng nghĩa "mọi thứ trong CRM". Nó chỉ mở quyền trên **DocType P1**
và **API P1**, không mở `CRM Lead`/`User`/`Role`/`System Settings`.

### 3.2. Scope do backend quyết định, không phải tham số client

P1 §8.2: *"**Không dùng `frappe.get_all` trả kết quả trực tiếp cho user** khi dữ liệu yêu cầu
row-level permission; `get_all` có thể bypass filter quyền. Nếu nội bộ service phải dùng, tự
giới hạn output theo actor và policy đã test."*

P1 §10.1: *"API list/dashboard trả `scope` được áp dụng… **Không chấp nhận tham số `user/team`
rộng hơn quyền.**"*

⇒ **Mọi endpoint P1 nhận scope từ token của actor** (role + campaign được cấp + team hierarchy),
**không** nhận `user=`/`team=` tùy ý. Tham số client chỉ được dùng để **thu hẹp**, và bị từ chối
nếu rộng hơn scope.

### 3.3. Role `CSKH CRM Admin` / `CSKH BOD Viewer` đã tồn tại

Role đã có sẵn trên site (kiểm tra runtime: `CSKH BOD Viewer`, `CSKH CRM Admin`). P1 **không**
tạo role mới; chỉ gắn quyền lên DocType P1 và route API.

> **Lưu ý:** các role này do seed P0 tạo, **không** nằm trong repo dưới dạng DocType Role.
> P1 cần bổ sung fixture/patch để site mới có chúng ⇒ xem `docs/architecture/api-contract-p1.md`
> §"Bootstrap prerequisites".

### 3.4. DocType P1: permission skeleton theo vai trò

DocType P1 (`CSKH Campaign`, `CSKH Assignment Rule`, `CSKH Work Item`,
`CSKH Assignment History`, `CSKH Import Batch`, `CSKH Distribution Run`,
`CSKH Identity Index`, `CSKH Active Claim`) hiện chỉ có role `System Manager` (fail-closed).

Trong gói P1 này, quyền được mở **có kiểm soát**:

| DocType | `CSKH CRM Admin` | `CSKH BOD Viewer` | `Sales Manager` (leader) | `Sales User` |
|---|---|---|---|---|
| `CSKH Campaign` | read/write | read | read (trong team) | ❌ |
| `CSKH Assignment Rule` | read/write | read | read (trong team) | ❌ |
| `CSKH Work Item` | read/write | read (không PII) | read (trong team) | read (chỉ của mình) |
| `CSKH Assignment History` | read | read (drill-down được duyệt) | read (trong team) | read (giới hạn hiện tại) |
| `CSKH Import Batch` | read/write | ❌ | ❌ | ❌ |
| `CSKH Distribution Run` | read/write | read | read (trong team) | ❌ |
| `CSKH Identity Index` | **system only** | ❌ | ❌ | ❌ |
| `CSKH Active Claim` | **system only** | ❌ | ❌ | ❌ |

**`CSKH Identity Index` và `CSKH Active Claim` giữ system-only** vì chúng chứa dữ liệu nhạy cảm
(số điện thoại canonical) và là **cơ chế bảo vệ**, không phải dữ liệu nghiệp vụ để role nghiệp vụ
đọc trực tiếp.

Ngoài role permission, **luôn** có `permission_query_conditions` + `has_permission` để ràng
scope theo actor (P1 §8.2). Role permission **không đủ** — nó chỉ là lớp thứ hai.

### 3.5. Không mở generic mutation

P1 §8.2: *"**Không cho Staff mutate `lead_owner`, `ToDo`, `DocShare`/Work Item bằng generic Frappe
endpoints** nếu khiến vượt quyền."*

⇒ `CSKH Work Item` **không** có `write` cho `Sales User`. `CSKH Assignment History` không có
`write`/`delete` cho **bất kỳ ai** (append-only).

`lead_owner` vẫn bị P0 chặn (`validate_lead_owner`) — ownership service là đường duy nhất.

### 3.6. BOD không cần System Manager

BOD chỉ cần:
- `read` trên `CSKH Campaign`/`Rule`/`Work Item`/`History`/`Distribution Run`,
- `read` trên `CRM Lead` **chỉ qua API scope** (không bật `CRM Lead` read role-wide).

⇒ BOD **không** có `System Manager`, **không** có quyền đọc `CRM Lead` toàn hệ thống. Số liệu
dashboard BOD đến từ **aggregate trên Work Item/Claim**, không phải từ danh sách Lead.

---

## 4. Điều kiện chấp nhận (test)

| Test | Kỳ vọng |
|---|---|
| SEC-03 | Staff gọi `distribution.execute` / `ownership.reassign` / `ownership.revoke` ⇒ `FORBIDDEN` |
| SEC-04 | Leader X đọc aggregate và chuyển giao team Y ⇒ `FORBIDDEN` |
| SEC-05 | BOD viewer ghi/sửa/xoá Campaign/Rule/History ⇒ `FORBIDDEN` |
| SEC-07 | Staff tự truyền `team`/`user` khác trong list/dashboard ⇒ **không** mở rộng scope |
| §8.3 #7 | BOD viewer sửa Campaign/Rule hoặc cấp role Admin ⇒ bị chặn |
| §8.3 #9 | Số aggregate trên dashboard = số query không giới hạn (không leak) |
| P0 regression | `test_bod_viewer_cannot_read_leads_or_manage_users` và `test_crm_admin_role_fails_closed_until_controlled_service_exists` **vẫn PASS** |

> **Ghi chú P0 regression:** test P0 `test_crm_admin_role_fails_closed_until_controlled_service_exists`
> được viết cho trạng thái P0 (chưa có controlled service). Khi P1 dựng xong controlled service,
> test đó **phải được cập nhật theo contract mới** — nhưng **không được** nới thành "cho phép
> CRM Admin đọc Lead toàn hệ thống". Đây là thay đổi **có chủ đích, có ADR**, và sẽ được nêu
> rõ trong bàn giao gói P1 (không được làm âm thầm).

---

## 5. Nguồn

- P1 §8.1, §8.2, §8.3, §10.1, §16
- `docs/architecture/adr-005-bod-viewer-scope.md` (P0)
- `docs/architecture/adr-004-owner-team-permissions.md` (P0, scope theo team)
- P0 tests: `apps/crm_cskh/crm_cskh/tests/test_permissions.py`
