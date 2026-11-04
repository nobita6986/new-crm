# P3 Version Lock & Upstream Integrity Verification (`P3-09`)

> **Phụ trách:** T1B (Backend & CI)
> **Nguồn khóa phiên bản:** `docs/architecture/version-lock.md` & `deploy/dev/.env.example`

---

## 1. Bảng đối chiếu phiên bản đã ghim (Pinned Upstream & Infrastructure Lock)

Phase P3 giữ nguyên tuyệt đối toàn bộ phiên bản upstream và image hạ tầng đã khóa từ P0–P2 (`0` nâng cấp ngầm, `0` floating tag):

| Thành phần | Tag / Image đã khóa | Commit SHA / Digest bất biến | Trạng thái P3 |
|---|---|---|---|
| **Frappe Framework** | `v15.123.1` | `d2efa79657ba5c1abf96098b3bfacc199517d57e` | **GIỮ NGUYÊN (`0` diff)** |
| **Frappe CRM** | `v1.87.0` | `0d3910b456ef4fb913e77b29db011b3debc4f778` | **GIỮ NGUYÊN (`0` diff)** |
| **Frappe Docker** | `v4.0.1` | `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` | **GIỮ NGUYÊN** |
| **Bench Image** | `frappe/bench:v5.31.0` | `sha256:3b75943bf00a1db92723d99ec34fbfa92f4b67b9b643ae1e96750899b7fbd980` | **GIỮ NGUYÊN** |
| **MariaDB Image** | `mariadb:11.8` | `sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa` | **GIỮ NGUYÊN** |
| **Redis Image** | `redis:8.6-alpine` | `sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba` | **GIỮ NGUYÊN** |
| **P3 Baseline (`main`)** | `GO P3` (`docs/P2-Exit-Report.md` §12.3) | `449fbc26374c3540b07ad86219fa0f33fc84feba` | **VERIFIED** |

---

## 2. Xác minh `0` sửa đổi mã nguồn Upstream (`apps/frappe` & `apps/crm`)

Toàn bộ logic P3 được triển khai 100% bên trong custom app `apps/crm_cskh` thông qua các cơ chế mở rộng chính thống của Frappe (`DocType`, `Custom Field` qua patch idempotent `v3_0.p3_schema_and_seeds`, `doc_events`, `permission_query_conditions`, `has_permission`, `scheduler_events`, `whitelisted` APIs):

- **Không sửa `apps/frappe`:**
  - `git -C /workspace/frappe-bench/apps/frappe rev-parse HEAD` = `d2efa79657ba5c1abf96098b3bfacc199517d57e`
  - `git -C /workspace/frappe-bench/apps/frappe status --porcelain` = rỗng (`0` file thay đổi)
- **Không sửa `apps/crm`:**
  - `git -C /workspace/frappe-bench/apps/crm rev-parse HEAD` = `0d3910b456ef4fb913e77b29db011b3debc4f778`
  - `git -C /workspace/frappe-bench/apps/crm status --porcelain` = rỗng (`0` file thay đổi)

---

## 3. Tổng hợp phạm vi mở rộng trong `apps/crm_cskh` cho P3

1. **Tái sử dụng upstream có kiểm chứng (`docs/p3/reuse-matrix.md`):**
   - `CRM Sales Hierarchy` (cây đội nhóm trực tiếp + hook ghi lịch sử `CSKH Team Membership History`).
   - `CRM Holiday List` & `CRM Holiday` (danh mục ngày lễ cho engine lịch làm việc CSKH, được bảo vệ ghi qua `doc_events` `guard_crm_holiday_list_mutation`).
   - `CRM Task` (duy nhất 1 callback task cho mỗi Work Item từ P2; P3 không tạo callback task thứ hai).
   - `File` (`/private/files/...` với `owner = "Administrator"` + `attached_to_doctype = "CSKH Export Job"` để ủy quyền kiểm tra tải tệp qua `has_permission`).
   - `Notification Log` (cảnh báo SLA sắp đến hạn / quá hạn có khóa chống nhân đôi).
2. **DocType mới thuộc module `CRM CSKH` (`8` DocType + `1` Child Table):**
   - `CSKH Team Membership History`
   - `CSKH Working Calendar` & `CSKH Working Shift` (Child Table)
   - `CSKH SLA Obligation`
   - `CSKH Dirty Scope`
   - `CSKH KPI Run`
   - `CSKH KPI Snapshot`
   - `CSKH QA Review`
   - `CSKH Export Job`
3. **Cột mở rộng trên DocType `CRM CSKH` hiện hữu:**
   - `CSKH Assignment History`: `team_at_assignment`, `attribution_status`
   - `CSKH Policy`: các trường cấu hình SLA, trọng số điểm `PROCESS_SCORE`, và 3 cờ `p3_sla_enabled`, `p3_reporting_enabled`, `qa_enabled` (tất cả đều ghi rõ trạng thái `DEV / configurable`).
