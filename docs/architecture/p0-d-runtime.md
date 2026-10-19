# P0-D / WP-03 — RUNTIME SPIKE LOG (D1–D4)

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B
> **Ngày chạy:** 2026-10-15
> **Trạng thái:** Đã chạy **runtime** trên DEV với **dữ liệu giả**; **không** phải security test đầy đủ; **chưa** kiểm tra UI trình duyệt.
> **Tài liệu liên quan:** [`data-model-spike.md`](./data-model-spike.md) §9, [`version-lock.md`](./version-lock.md), [`deploy/dev/`](../../deploy/dev/), [`P0.md`](../root-plan/P0.md) §6

---

## 1. BASELINE & MÔI TRƯỜNG (đã kiểm chứng)

| Hạng mục | Giá trị quan sát |
|---|---|
| Frappe Framework | `15.123.1` — SHA `d2efa79657ba5c1abf96098b3bfacc199517d57e` |
| Frappe CRM | `1.87.0` — SHA `0d3910b456ef4fb913e77b29db011b3debc4f778` |
| Bench image | `frappe/bench:v5.31.0@sha256:1b27bb30…` |
| DB | MariaDB `11.8@sha256:6422478c…` |
| Redis | `8.6-alpine@sha256:ac2da09b…` |
| Site tại lúc WP-03 chạy | `dev.localhost` (`frappe`, `crm`; app POC `crm_cskh` chưa cài trên site tại thời điểm đó) |
| Compose project | `crm-cskh-dev` (4 service: `db`, `redis-cache`, `redis-queue`, `bench`) |

Lệnh xác minh:

```bash
docker compose -f deploy/dev/docker-compose.yml ps
docker compose -f deploy/dev/docker-compose.yml exec -T bench bash -lc "cd /workspace/frappe-bench && bench --site dev.localhost list-apps && bench version"
docker compose -f deploy/dev/docker-compose.yml exec -T bench bash -lc "cd /workspace/frappe-bench && git -C apps/frappe rev-parse HEAD && git -C apps/crm rev-parse HEAD"
```

Kết quả then chốt: `list-apps` → `frappe 15.123.1`, `crm 1.87.0`; SHA khớp pin.

---

## 2. PHƯƠNG PHÁP CHẠY SCRIPT

- `bench --site dev.localhost execute <method>` **không dùng được** cho module tùy ý: `frappe.get_attr` yêu cầu segment đầu là **app đã cài** (`p0d_probe` → `AppNotInstalledError`), kể cả khi đã đặt `PYTHONPATH`.
- Cách dùng được: **`bench --site dev.localhost console` đọc stdin** từ file script, dữ liệu giả, tiền tố `P0D-T1B`:

```bash
docker compose -f deploy/dev/docker-compose.yml cp .p0d-console.py bench:/workspace/frappe-bench/p0d_console.py
docker compose -f deploy/dev/docker-compose.yml exec -T bench bash -lc "cd /workspace/frappe-bench && bench --site dev.localhost console < p0d_console.py"
docker compose -f deploy/dev/docker-compose.yml exec -T bench bash -lc "rm -f /workspace/frappe-bench/p0d_console.py"
```

- **Không** sửa upstream; script tạm đã xóa sau khi chạy. Không chạy `migrate`, không đổi schema.
- Ghi chú kỹ thuật: `CRM Call Log.from` **không** gán được bằng `c["from"] = …` (`TypeError: 'CRMCallLog' object does not support item assignment`) — phải dùng `c.set("from", …)`. Ngoài ra **không** gọi `frappe.db.rollback()` giữa các bước (rollback xóa luôn các insert chưa commit trước đó).

---

## 3. KẾT QUẢ D1–D4 (log thô, lọc `P0D|`)

```text
P0D|frappe_version|15.123.1
P0D|site|dev.localhost
P0D|system_tz|                      <-- System Settings.time_zone RỖNG
P0D|conf_tz|Asia/Ho_Chi_Minh        <-- site_config time_zone
P0D|now|2026-10-15 12:12:59.406694

# D1a — Lead chỉ có SĐT
P0D|D1a_no_first_name|EXC|ValidationError|A Lead requires either a person's name or an organization's name

# D1b — placeholder
P0D|D1b_name|CRM-LEAD-2026-00002
P0D|D1b_status|New
P0D|D1b_mobile_no|+84900000101
P0D|D1b_lead_owner|Administrator
P0D|D1b_lead_name|Chua ro ten [P0D-T1B]

# D1c — số tương đương, khác định dạng
P0D|D1c_names|CRM-LEAD-2026-00003 , CRM-LEAD-2026-00004
P0D|D1c_l2_mobile|+84 900 000 101
P0D|D1c_l3_mobile|0900000101
P0D|has_doctype_Data_Import|True

# D2 — manual Call Log
P0D|D2_c1_name|ca0598636ffa
P0D|D2_c1_status|No Answer
P0D|D2_c1_medium|Manual
P0D|D2_c1_ref|CRM Lead:CRM-LEAD-2026-00002
P0D|D2_c2_name|1268eef1ff43
P0D|D2_c2_status|Completed
P0D|D2_linked_timeline|calls=2 notes=0 tasks=0

# D3 — Task + status
P0D|D3_task_name|3
P0D|D3_task_status|Todo
P0D|D3_task_due|2026-10-16 12:13:00.230772
P0D|D3_owner_before|Administrator
P0D|D3_owner_after|Administrator
P0D|D3_status_change_log|final=Nurture rows=New->Contacted (->) 2026-10-15 13:42:59..2026-10-15 13:43:00 | Contacted->Nurture (->) 2026-10-15 13:43:00..2026-10-15 13:43:00 | Nurture-> (->) 2026-10-15 13:43:00..None

# D4 — SLA
P0D|D4_sla_existing_count|1
P0D|D4_our_sla_exists|True

# Quyền — sơ bộ (has_permission + get_list)
P0D|PERM|staff.a@dev.local|LeadRead=False|TaskRead=True|GetList=EXC:NameError
P0D|PERM|team1.leader@dev.local|LeadRead=True|TaskRead=True|GetList=EXC:NameError
P0D|PERM|team2.leader@dev.local|LeadRead=True|TaskRead=True|GetList=EXC:NameError
P0D|PERM|bod.viewer@dev.local|LeadRead=False|TaskRead=False|GetList=EXC:PermissionError
P0D|PERM|crm.admin@dev.local|LeadRead=False|TaskRead=False|GetList=EXC:PermissionError
P0D|hierarchy_enabled|False
P0D|hierarchy_rows|5
```

> `GetList=EXC:NameError` ở 3 vai trò sales là **lỗi trong script dò** (biểu thức generator), **không** phải lỗi quyền — cần chạy lại khi làm WP-05. `LeadRead`/`TaskRead` lấy từ `frappe.has_permission(...)` nên đáng tin hơn.

---

## 4. DIỄN GIẢI THEO KỊCH BẢN

### D1 — Lead chỉ có SĐT
- **Không tạo được** Lead chỉ có `mobile_no`: chặn ở `set_lead_name` bằng `ValidationError` (thông điệp "A Lead requires either a person's name or an organization's name"). Đây **không** phải `MandatoryError` của `first_name` — guard tên thắng trước. → Placeholder tên là **bắt buộc**, không tắt mandatory.
- Với placeholder `Chua ro ten [P0D-T1B]` (viết không dấu để tránh rắc rối log): tạo OK, `status=New`, `lead_owner` lưu được.
- **SĐT lưu nguyên văn**: `+84900000101`, `+84 900 000 101`, `0900000101` đều được lưu **đúng như nhập**; cùng một số logic nhưng **không** bị chuẩn hóa hay chặn trùng → **xác nhận** P1 phải tự normalize/dedupe; P0 không thêm unique.
- DocType `Data Import` **tồn tại**; **chưa** chạy import file thật (cần UI/HTTP).

### D2 — Manual Call Log
- Tạo 2 Call Log gắn Lead; `telephony_medium` tự gán `Manual`; `name` sinh hash 12 ký tự.
- `CRM Call Log.status` chỉ là **trạng thái kỹ thuật** (`No Answer`, `Completed`); **không** có field outcome. `Completed` **không** được diễn giải thành "khách quan tâm/kết nối".
- Backend timeline (`crm.api.activities.get_linked_calls`) trả `calls=2` → call log gắn Lead hiển thị được ở lớp API. **UI trình duyệt chưa kiểm tra.**

### D3 — Task & status
- Tạo `CRM Task` (id `3`) gắn Lead, assignee `Administrator`, `due_date` tương lai, `status=Todo`.
- `lead_owner` **không đổi** khi tạo Task tham chiếu Lead (Task gán cho chính nó; ToDo `reference_type=CRM Task`, không phải `CRM Lead`) → phù hợp mô hình: Task = việc cần làm, không phải kết quả liên hệ.
- Đổi Lead `New→Contacted→Nurture`: sinh `status_change_log`, nhưng **`from_type`/`to_type` đều rỗng** → **xác nhận** quan sát `crm_status_change_log.py:46` tra `CRM Deal Status` nên không suy ra `type` cho `CRM Lead Status`.
- **Timezone lệch chuẩn tại probe ban đầu**: `frappe.utils.now()` ≈ `12:12` trong khi dòng log ≈ `13:43` (lệch 1h30). Khi đó `System Settings.time_zone` **rỗng**; Frappe mặc định **Asia/Kolkata**, còn container `TZ=Asia/Ho_Chi_Minh`. T0 đã đặt và kiểm tra lại setting ở §5.1.

### D4 — SLA (một phần)
- Tạo nhanh được `CRM Service Level Agreement` tối giản (`P0D-T1B SLA`), baseline trước đó `count=0`.
- **Chưa** kiểm chứng `response_by`/`communication_status` apply vì cần thêm `CRM Service Level Priority`/`Service Day` và đổi `communication_status`. Giữ ranh giới: SLA gốc = SLA giao tiếp, **không** phải first-touch CSKH.

---

## 5. QUYỀN — QUAN SÁT SƠ BỘ BAN ĐẦU (KHÔNG THAY WP-05)

Các kết quả dưới đây là trạng thái tại lần probe WP-03 ban đầu, khi `enable_sales_hierarchy = False`:

- `staff.a`: `LeadRead=False` (đúng), nhưng `TaskRead=True` → **`CRM Task` không có bảo vệ cấp record** (khớp cảnh báo R-06).
- `team1.leader` / `team2.leader`: `LeadRead=True` (kể cả team không sở hữu) → **Sales Manager ngoài/không có cây đọc rộng**, khớp R-01. Cần cấu hình hierarchy + re-test.
- `bod.viewer`, `crm.admin`: `LeadRead=False`, `TaskRead=False` → role PoC chưa có quyền đọc Lead/Task trực tiếp.

> Đây là quan sát **sơ bộ** bằng `has_permission`, **không** phải ma trận `SEC-01..16`. Ma trận đầy đủ + negative tests thuộc **WP-05** (`docs/architecture/permission-matrix.md`).

### 5.1. Cấu hình DEV và probe phạm vi sau cập nhật của T0 (2026-10-15)

Seed DEV được cập nhật để đặt `System Settings.time_zone=Asia/Ho_Chi_Minh` và bật
`FCRM Settings.enable_sales_hierarchy=1`. Chạy seed hai lần: lần đầu cập nhật hai setting; lần hai báo cả hai đã đúng, không thêm bản ghi seed.

Probe trên `dev.localhost` sau cập nhật:

```text
VERIFY|time_zone=Asia/Ho_Chi_Minh
VERIFY|system_timezone=Asia/Ho_Chi_Minh
VERIFY|frappe_now=2026-10-15 14:20:42.340030
VERIFY|container_local_now=2026-10-15 14:20:42.357642
VERIFY|hierarchy_enabled=1
SCOPE|staff.a@dev.local|visible=A1
SCOPE|team1.leader@dev.local|visible=A1,B1
SCOPE|team2.leader@dev.local|visible=C1
```

Trong ba Lead seed A1/B1/C1, Staff A chỉ đọc A1; Team 1 leader đọc A1/B1; Team 2 leader chỉ đọc C1. Đây là probe mẫu cho owner/cross-team sau khi bật cây, **không** phải kết quả WP-05. Chưa kiểm tra đủ role, child DocType, API, reassignment, export hoặc toàn bộ SEC-01..16. Sales Manager không có node trong cây vẫn là nhánh phải kiểm thử riêng.

---

## 6. DỮ LIỆU GIẢ ĐÃ TẠO (tiền tố `P0D-T1B`)

| Loại | Bản ghi |
|---|---|
| CRM Lead | `CRM-LEAD-2026-00002`, `00003`, `00004` (`lead_name` chứa `P0D-T1B`), `status=Nurture` cho `00002` |
| CRM Call Log | `ca0598636ffa` (`No Answer`), `1268eef1ff43` (`Completed`) |
| CRM Task | id `3` (`P0D-T1B callback`) |
| CRM Service Level Agreement | `P0D-T1B SLA` |

Không xóa/sửa fixture WP-01. Có thể dọn các bản ghi trên khi cần.

---

## 7. GIỚI HẠN & CHƯA KIỂM CHỨNG

- **UI trình duyệt chưa kiểm tra** (không có browser automation trong phiên này): Timeline/Lead Detail/danh sách Call Logs/Tasks theo UI **chưa** xác nhận. Chỉ có bằng chứng **backend**.
- **Import file thật** (CSV/XLSX chỉ có cột số) **chưa** chạy.
- **SLA apply semantics** (`response_by`, priority, working hours) **chưa** kiểm chứng.
- **Ma trận quyền đầy đủ** (owner/leader/other, cross-team, Call Log/Note/File) **chưa** chạy — thuộc WP-05.
- Chỉ chạy trên **một** site DEV, dữ liệu giả; không suy rộng cho production.

## 8. CAVEAT MariaDB 11.8

- Kết quả trên **MariaDB 11.8**. Chưa thêm unique/collation mới nên **chưa** kiểm chứng hành vi `unique + NULL` hay `utf8mb4_unicode_ci` cho trường định danh (P1). Compose đã đặt `character-set-server=utf8mb4` / `collation-server=utf8mb4_unicode_ci` — cần kiểm chứng cụ thể khi làm dedup P1.
- Không dùng partial index (PostgreSQL) — thiết kế P1 phải dùng unique/index tương thích MariaDB.
