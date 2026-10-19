# DATA MODEL SPIKE — FRAPPE CRM `v1.87.0` (P0)

> **Dự án:** CRM-CSKH
> **Chủ sở hữu tài liệu:** T1B
> **Trạng thái:** Khảo sát **tĩnh** + **runtime spike WP-03/P0-D** đã chạy trên bản ghim (CRM `v1.87.0` + Framework `v15.123.1`); **chưa** chốt schema hoặc code model CSKH. Log đầy đủ: [`p0-d-runtime.md`](./p0-d-runtime.md).
> **Tài liệu liên quan:** [`reuse-audit.md`](./reuse-audit.md), [`ui-extension-spike.md`](./ui-extension-spike.md), [`glossary-vi.md`](./glossary-vi.md), [`p0-d-runtime.md`](./p0-d-runtime.md), [`master-plan.md`](../root-plan/master-plan.md) §5–§6, [`P1.md`](../root-plan/P1.md) §5, [`P2.md`](../root-plan/P2.md) §4–§7

---

## 0. PROVENANCE

- Nguồn: `https://github.com/frappe/crm`
- Tag: `v1.87.0` — Commit: **`0d3910b456ef4fb913e77b29db011b3debc4f778`**
- Xác minh: `git ls-remote`, `git describe --tags --always`, `git rev-parse HEAD` (2026-10-15).
- **Runtime:** WP-03/P0-D đã chạy trên DEV (`frappe 15.123.1` + `crm 1.87.0`, MariaDB 11.8, site `dev.localhost`) với **dữ liệu giả** — kết quả ở **§9** và [`p0-d-runtime.md`](./p0-d-runtime.md).
- **Giới hạn:** WP-02 chỉ tạo POC `CSKH App Settings` và custom field `cskh_poc_marker`; chưa tạo business schema CSKH. Phần mô tả schema/controller là đọc tĩnh; kết quả runtime §9 **không** phải security test và **chưa** kiểm tra UI trình duyệt. Các giả định chưa chạy vẫn đánh dấu `[CẦN TEST DEV]`.

### 0.1. QUYẾT ĐỊNH ĐÃ CHỐT (T0) — THIẾT KẾ, CHƯA XÁC MINH RUNTIME

> Các mục này là **quyết định thiết kế**, không phải bằng chứng runtime. WP-02 đã tạo DocType POC `CSKH App Settings` và custom field `cskh_poc_marker`; chưa tạo DocType nghiệp vụ, trường canonical/placeholder hay unique constraint CSKH. WP-03 chạy runtime spike D1–D4, không phải model-specific security tests.

- **ADR-001 (framework runtime):** mục tiêu P0 là **Frappe Framework `v15.123.1`** — lựa chọn để kiểm chứng, **chưa** phải baseline cuối; v16 giữ làm phương án khảo sát sau.
- **Canonical phone / dedup:** **chốt ở P1**; **P0 không tạo unique constraint**; **không tự merge** trường hợp mơ hồ (§6.2).
- **KPI attribution:** **chốt ở P3**; P1/P2 phải lưu `CSKH Assignment History` đủ khoảng sở hữu (§7 mục 4).
- **License:** AGPL-3.0 (`crm/hooks.py:6`) — cần rà soát tuân thủ trước phát hành (xem `reuse-audit.md` §0.3); không kết luận pháp lý.

---

## 1. BỐN KHÁI NIỆM TÁCH BIỆT

| Khái niệm | Bản chất | Nguồn ở `v1.87.0` | Kết luận |
|---|---|---|---|
| **Lead** | Danh tính khách (canonical) | `CRM Lead` (`crm_lead.json`) | REUSE |
| **Work Item** | Một nhiệm vụ CSKH trong một campaign | *không có* | BUILD NEW (`CSKH Work Item`) |
| **Assignment History** | Ai sở hữu trong khoảng thời gian nào | *chỉ có `lead_owner` single* | BUILD NEW (`CSKH Assignment History`) |
| **Contact Attempt** | Mỗi lần liên hệ thực tế (kênh + kết quả) | *không có* | BUILD NEW (`CSKH Contact Attempt`) |

---

## 2. VÌ SAO KHÔNG THỂ CHỈ DỰA TRÊN `lead_owner`

Bằng chứng tại commit `0d3910b`:

1. `lead_owner` là **một giá trị duy nhất** (`crm_lead.json:168`, `Link → User`) — không lưu lịch sử.
2. `CRM Lead` có `track_changes: 1` (JSON) nhưng đó là **Version log** của Frappe, không phải mô hình ownership interval; không dùng để tính KPI theo thời gian sở hữu.
3. `lead_owner` bị **ghi đè tự động** bởi ToDo:
   - `crm/api/todo.py:20`–`26`: khi ToDo được thêm, `frappe.db.set_value(reference, "lead_owner", allocated_to)` — "the latest assignment owns the record".
   - `crm/api/todo.py:32`–`41` + `:44`–`51`: khi ToDo `Cancelled`, owner bị **set về `None`**.
4. Khi `lead_owner` đổi, Lead tự tạo ToDo + DocShare (`crm_lead.py:99`–`101`, `:103`–`...`, `assign_agent` `:167`, `share_with_agent` `:180`), và `share_with_agent` **gỡ DocShare của user khác**.
5. Quyền đọc Lead phụ thuộc `lead_owner` HOẶC ToDo còn hiệu lực (`crm/permissions/org_hierarchy.py:38`–`60`).

**Hệ quả:** chuyển chủ A→B không chỉ là đổi một cột; nó kéo theo ToDo + DocShare và ảnh hưởng quyền. Lịch sử giao nhận và "ai chịu trách nhiệm trong khoảng nào" **bắt buộc** phải có bảng riêng, append-only.

---

## 3. CALL LOG ≠ KẾT QUẢ CSKH (bằng chứng schema)

Trường `status` của `CRM Call Log` là enum **kỹ thuật**:

```text
crm/fcrm/doctype/crm_call_log/crm_call_log.json:54
"options": "Initiated\nRinging\nIn Progress\nCompleted\nFailed\nBusy\nNo Answer\nQueued\nCanceled"
```

- Không có bất kỳ field "outcome"/"disposition"/"disconnected reason"/"interested" → **không có chỗ** cho kết quả nghiệp vụ trong Call Log.
- `from`, `to`, `type` là bắt buộc (`crm_call_log.json:47`, `:78`, `:87`) → **không thể** dùng Call Log cho kênh Zalo/SMS/Facebook (không có số hai chiều).
- `note` là `Link → FCRM Note` (`crm_call_log.json`), không phải nơi mã hóa kết quả.

**Quy tắc rút ra (khớp master plan §3.2.3, §6.2):**

| | CRM Call Log | CSKH Contact Attempt |
|---|---|---|
| Đại diện | Trạng thái cuộc gọi | Kết quả liên hệ (mọi kênh) |
| `Completed` | Cuộc gọi kết thúc bình thường | — |
| `No Answer` | attempted | attempted, `is_connected=false` |
| `Connected/Interested/Converted` | — | Có (qua `CSKH Outcome Code`) |

→ Khi `channel = phone_call`, tạo **một** Contact Attempt và (tùy chọn) link 1-1 tới Call Log để chống đếm trùng (P2 §9.4).

---

## 4. TRƯỜNG UPSTREAM TÁI SỬ DỤNG ĐƯỢC

### 4.1. CRM Lead (`crm_lead.json`, `crm_lead.py`)

| Trường | Ghi chú | Dùng cho |
|---|---|---|
| `first_name` (reqd) `:92`,`:95` | bắt buộc | placeholder tên |
| `status` (reqd, Link `CRM Lead Status`) `:114`,`:120` | trạng thái CRM tổng quát | chỉ đồng bộ có mapping, không lạm dụng |
| `mobile_no`, `phone` `:137` | Data/Phone, **không unique** | canonical phone phải tự thêm |
| `lead_owner` `:168` | owner hiện tại | hiển thị, không phải lịch sử |
| `source` (Link `CRM Lead Source`) | nguồn | campaign/source |
| `converted`, `lead_temperature`, `lead_score` | — | tham khảo |
| `facebook_lead_id` (**unique**) | `crm_lead.json` cuối file | khóa danh tính Facebook |
| `status_change_log` (Table `CRM Status Change Log`) | `istable` | lịch sử trạng thái (bổ trợ) |
| `sla`, `sla_status`, `response_by`, `first_responded_on`, `first_response_time`, `communication_status` | SLA giao tiếp | **không** dùng cho first-touch CSKH |

### 4.2. CRM Task (`crm_task.json`, `crm_task.py`)

- `assigned_to` `:49`, `due_date` `:64`, `status` `:56`,`:61`, `priority` `:33`, `reference_doctype`/`reference_docname` `:79`,`:85`.
- Gán/hủy qua `frappe.desk.form.assign_to` (`crm_task.py:4`,`:5`,`:30`,`:33`,`:41`,`:45`) → thừa hưởng luôn chuỗi ToDo→owner của Lead nếu `reference` là Lead → cần lưu ý.

### 4.3. CRM Status Change Log

- Child table (`crm_status_change_log.json:81`), `from/to` là `Data` (`:22`,`:44`), có `from_date/to_date/duration/log_owner`.
- Sinh tự động trong `crm_lead.py:101` → `crm_status_change_log.py:45`.
- **Lưu ý `[CẦN TEST DEV]`**: `crm_status_change_log.py:46` tra `"CRM Deal Status"` kể cả khi log thuộc `CRM Lead`.

### 4.4. CRM Sales Hierarchy

- Tree (`crm_sales_hierarchy.json`: `is_tree: 1`, `user` **unique** `:40`), `NestedSet` (`crm_sales_hierarchy.py:8`), index `lft/rgt` + `user` (`:55`–`56`).
- Có `enabled` (Check) trong type hints (`crm_sales_hierarchy.py:17`).

---

## 5. SLA — RANH GIỚI TÁI SỬ DỤNG

- Upstream `CRM Service Level Agreement` đo **first/rolling response** theo `communication_status` (`crm_service_level_agreement.py:74`,`:84`,`:134`,`:173`), dùng `response_by`/`first_responded_on`.
- Có utility **lịch làm việc/ngày nghỉ** tái sử dụng: `calc_time` (`:224`), `calc_elapsed_time` (`:263`), `CRM Service Day` (child), `CRM Holiday List` (`get_holidays` `:352`).
- **Không** đo `assigned_at → first_attempt_at`; `CRM Lead` không có `assigned_at`.
- **Hiệu năng:** `calc_elapsed_time` (`:280`–`291`) tăng theo từng giây → không dùng trực tiếp cho số liệu lớn.

→ SLA CSKH (first attempt, follow-up, overdue) là **BUILD NEW** (`CSKH SLA Obligation` ở P3), chỉ REUSE phần lịch làm việc qua adapter/bản pure-function có test.

---

## 6. HƯỚNG MÔ HÌNH ĐỀ XUẤT (P1/P2 — để T0 xác nhận)

> Đây là **định hướng khảo sát**, không phải quyết định schema cuối. Tên/trường có thể đổi theo ADR P1/P2.

```text
CRM Lead (canonical danh tính)  ──1:N──►  CSKH Work Item (campaign × lead)
                                              ├─ CSKH Assignment History (append-only, ownership interval)
                                              ├─ CSKH Contact Attempt (mỗi lần liên hệ, có outcome snapshot)
                                              │      └─ (optional) link 1-1 CRM Call Log khi channel=phone_call
                                              └─ CRM Task (callback; custom field link Work Item)
CSKH Campaign ──1:N── CSKH Work Item
CSKH Campaign ──1:N── CSKH Assignment Rule (weight/capacity)
CSKH Outcome Code  ◄── CSKH Contact Attempt
```

### 6.1. Khóa/index tối thiểu (định hướng)

| Phạm vi | Ràng buộc đề xuất |
|---|---|
| Danh tính | canonical phone của Lead (unique, nullable) **hoặc** bảng identity — chọn 1 (ADR-P1-01) |
| Work Item | unique `(campaign, lead)`; index `(assigned_to, status, assigned_at)` |
| Active claim | unique `lead` trong claim table (một lead không 2 active owner) |
| Assignment History | unique `event_id`; index `(work_item, event_at)`, `(to_user, ownership_start/end)` |
| Contact Attempt | index `(work_item, occurred_at)`, `(performed_by, occurred_at)`; unique `call_log` khi không null; unique idempotency key |
| Outcome snapshot | lưu `outcome_code` + snapshot nhãn/cờ để đổi nhãn không làm trôi lịch sử |

**Lưu ý DB:** MariaDB — không dùng partial index/PostgreSQL; unique có NULL cần kiểm chứng hành vi `[CẦN TEST DEV]`.

### 6.2. Canonical phone & tạo Lead chỉ có SĐT

- Thêm custom field `cskh_normalized_phone` (unique) trên `CRM Lead` **hoặc** DocType identity — **chọn 1** (ADR-P1-01). **Quyết định (T0):** canonical phone/dedup **chốt ở P1**; **P0 không tạo unique constraint**; **không tự merge** trường hợp mơ hồ.
- Placeholder tên: `first_name` = `Chưa rõ tên` + `cskh_is_placeholder_name = 1`. Không dùng `ignore_mandatory` (`crm_lead.py:135`).
- Mọi đường tạo Lead (UI, import, API, call-log→lead `crm_call_log.py:294`) phải đi qua cùng validation chuẩn hóa.

---

## 7. CÁC ĐIỂM CẦN T0 CHỐT (BLOCKER MỀM)

1. **ADR-P1-01**: unique phone bằng custom Lead field hay Identity DocType?
2. **Chuẩn canonical**: lưu E.164 (`+84...`) hay 0-đầu? Xử lý bộ đầu số có cập nhật?
3. **Work Item grain**: mặc định P1 "1 Lead tối đa 1 Work Item active toàn CRM" — xác nhận.
4. **Đơn vị KPI**: company unique Work Item vs employee ownership interval (P3) — cần chốt sớm để schema P1/P2 không phải đổi.
5. **`assignment interval` vs `lead_owner`**: xác nhận mọi mutation đi qua ownership service.
6. **SLA first-touch**: đo theo `assignment interval.start_at` hay `work_item.assigned_at`?

---

## 8. CHƯA THỂ XÁC MINH / ĐÃ XÁC MINH MỘT PHẦN

> Cập nhật sau WP-03 (§9): một số mục nay đã có bằng chứng runtime. Mục còn lại giữ `[CẦN TEST DEV]`.

- **[ĐÃ XÁC MINH — §9]** Tạo Lead chỉ có SĐT **bị chặn** đúng; placeholder tạo được; SĐT **không** chuẩn hóa và **không** chặn trùng (kiểm chứng trên DB thật).
- **[ĐÃ XÁC MINH — §9]** `crm_status_change_log.py:46`: `from_type`/`to_type` **rỗng** khi đổi status `CRM Lead`.
- **[CHƯA]** Ràng buộc unique/index: P0 **không** thêm unique nên **chưa** kiểm chứng hành vi `unique + NULL` trên MariaDB.
- **[CHƯA]** Trường nghiệp vụ `cskh_is_placeholder_name`: chưa tạo. Chỉ có custom field POC `cskh_poc_marker`; field POC không chứng minh phân quyền.
- **[CHƯA]** Hiệu năng `calc_elapsed_time`.
- **[CHƯA — thuộc WP-05]** Tính đúng của `ToDo`→`lead_owner`→DocShare khi bỏ qua service; **ma trận quyền đầy đủ** (`reuse-audit.md` R-02/R-04/R-05).
- **[ĐÃ XÁC MINH — T0 §10]** Import CSV một Lead giả và SLA `response_by`.
- **[CHƯA]** Import XLSX; unique/NULL cho schema định danh P1; hiệu năng SLA; permission matrix đầy đủ; Vue CRM UI qua Vite.

---

## 9. KẾT QUẢ RUNTIME WP-03 / P0-D (DỮ LIỆU GIẢ)

> Chạy trong container `bench` của `deploy/dev` trên bản ghim: Frappe `15.123.1` (`d2efa796…`) + CRM `1.87.0` (`0d3910b4…`), **MariaDB 11.8**, site `dev.localhost`. **Không** sửa upstream; chỉ tạo record giả tiền tố `[P0D-T1B]`. Log thô + lệnh: [`p0-d-runtime.md`](./p0-d-runtime.md).

| Kịch bản | Kết quả thực thi | Ý nghĩa / hệ quả |
|---|---|---|
| **D1a** — Lead chỉ có SĐT | Insert **bị chặn**: `ValidationError: A Lead requires either a person's name or an organization's name` (`set_lead_name`) | **Không** tạo được Lead chỉ có số mà không bypass; placeholder tên là bắt buộc |
| **D1b** — placeholder | Tạo OK (`CRM-LEAD-2026-00002`); `status=New`; `mobile_no` lưu **nguyên văn**; `lead_owner=Administrator` | CONFIGURE + placeholder khả thi; `first_name` bắt buộc vẫn giữ |
| **D1c** — số tương đương | Tạo **thêm 2** Lead với `+84 900 000 101` và `0900000101`, lưu **đúng chuỗi nhập** | **Không** chuẩn hóa, **không** dedup → P1 phải tự làm; P0 không thêm unique |
| **D1** — Import tại WP-03 | DocType `Data Import` tồn tại; lượt WP-03 chưa chạy import file thật | CSV follow-up của T0 ở §10 |
| **D2** — Call Log | 2 Call Log (`No Answer`, `Completed`) gắn Lead; `telephony_medium` mặc định `Manual`; `get_linked_calls` → `calls=2` | `status` là **kỹ thuật**; **không** có field outcome; `Completed` ≠ kết quả CSKH |
| **D3** — Task | `CRM Task` id `3` gắn Lead, assignee `Administrator`, `due_date` tương lai, `status=Todo`; `lead_owner` **không đổi** | Task = việc cần làm; gắn Lead **không** kéo `lead_owner` |
| **D3** — Status history | `New→Contacted→Nurture` sinh log; **`from_type`/`to_type` rỗng** | **Xác nhận R-10**: tra `CRM Deal Status` nên không map type cho Lead |
| **D3** — Timezone | Lần probe ban đầu: `frappe.utils.now()≈12:12` vs dòng log `≈13:43` (lệch 1h30); setting rỗng làm Frappe dùng **Asia/Kolkata**, container ở **Asia/Ho_Chi_Minh**. T0 đã đặt timezone và xác minh giờ Frappe/container khớp trên site hiện tại. | Sai lệch ban đầu đã được xử lý cho site DEV này; seed đặt lại timezone trên site DEV được seed. Bằng chứng cập nhật: [`p0-d-runtime.md` §5.1](./p0-d-runtime.md) |
| **D4** — SLA tại WP-03 | Tạo nhanh `CRM Service Level Agreement` tối giản (`P0D-T1B SLA`) | Apply follow-up của T0 ở §10 |
| **Quyền (probe ban đầu)** | `has_permission`: staff.a `LeadRead=False`/`TaskRead=True`; team1+team2 leader `LeadRead=True`; bod.viewer & crm.admin `LeadRead=False`/`TaskRead=False`; lúc đó `enable_sales_hierarchy=False` | Trạng thái ban đầu trước khi bật cây. Probe owner/cross-team sau cập nhật xem [`p0-d-runtime.md` §5.1](./p0-d-runtime.md); **không thay WP-05**; (a) `CRM Task` không bảo vệ cấp record → R-06 |

**Dữ liệu giả đã tạo:** Lead `CRM-LEAD-2026-00002/00003/00004`, Call Log `ca0598636ffa`/`1268eef1ff43`, CRM Task id `3`, SLA `P0D-T1B SLA`. Không xóa/sửa fixture WP-01.

> **Cập nhật cấu hình DEV (T0, 2026-10-15):** seed đặt `System Settings.time_zone=Asia/Ho_Chi_Minh` và `FCRM Settings.enable_sales_hierarchy=1`; giờ Frappe/container khớp và probe `get_list` mẫu cho thấy owner/team scope đúng trên ba Lead seed. Bảng giữ kết quả ban đầu tại thời điểm WP-03. Đây không phải kết quả WP-05; xem [`p0-d-runtime.md` §5.1](./p0-d-runtime.md).

## 10. T0 FOLLOW-UP RUNTIME (2026-10-15)

Các kiểm tra dưới đây chạy sau WP-03 trên site test/restore mới, cùng baseline CRM `v1.87.0`, Frappe `15.123.1`, MariaDB `11.8`; chỉ dùng dữ liệu giả.

| Kiểm tra | Kết quả | Giới hạn |
|---|---|---|
| Data Import CSV | Importer báo `1 of 1` bản ghi thành công; Lead lưu `09000008888`, tên placeholder và `status=New`. | Chưa chạy XLSX; P0 không thêm canonical phone/dedup. |
| SLA apply | `response_by` được đặt sau 3600 giây; `sla_status=First Response Due`. | Probe tối giản; chưa kiểm hết working hours, holidays hoặc priority. |
| Permission suite | 28 app tests pass; test field synthetic `permlevel=1` xác nhận `get_data` không trả giá trị cho Staff. | Không thay kiểm tra từng endpoint/ô SEC; xem [`permission-matrix.md`](./permission-matrix.md). |
| Browser | Trang `/cskh` cùng site/session và POST CSRF hợp lệ hoạt động. | CRM SPA qua Vite hiển thị trắng; Vue hot reload và CRM Timeline UI chưa xác minh. |
