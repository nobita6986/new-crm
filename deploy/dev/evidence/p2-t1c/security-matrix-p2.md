# P2 Security Matrix — CRM-CSKH (T1C Sở hữu & Kiểm định Độc lập)

- **Chủ sở hữu tài liệu:** **T1C** (Kiểm định độc lập Bảo mật, Chất lượng & Runtime — P2)
- **Tài liệu gốc đối chiếu:** [`docs/root-plan/P2.md`](../../../docs/root-plan/P2.md) (§0, §8, §10, §15.3, §17 `G2-10` / `G2-11` / `G2-12`)
- **Baseline nền đã khóa (`main`):** `1913d2bd5ebfdc894eaa492b46be502dbdf44808` (CI P0 `#41658199683`, CI P1 `#41658199861`)
- **Môi trường kiểm định cô lập của T1C:** Worktree `D:/HRP-app/t1c-p2-verify` · Docker project `crm-cskh-t1c-p2` · Site `t1cp2.localhost`
- **Trạng thái hiện hành (2026-11-03):** **`BASELINE_READY / PENDING_P2_CODE`** — nền P0/P1 (`E2-06`, `E2-07`) đạt `PASS` trên `1913d2b`; toàn bộ `SEC2-01 → SEC2-16` trên nghiệp vụ P2 giữ trạng thái **`NOT_IMPLEMENTED_YET`** (tuyệt đối không ký `PASS` trước khi đo runtime trên SHA bàn giao thực tế).

---

## 1. Nguyên tắc kiểm định bảo mật P2 của T1C

1. **Đúng actor nghiệp vụ (cấm dùng `Administrator` thay `CRM Admin`):**
   - `crm_admin`: `cskh.admin@example.test` — chỉ có role `CSKH CRM Admin` (**`0` `System Manager`**, không là `Administrator`).
   - `bod`: `cskh.bod@example.test` — chỉ có role `CSKH BOD Viewer` (**`0` `System Manager`**).
   - `leader_a` / `leader_b` / `leader_c`: `leader.{a,b,c}@example.test` — `Sales Manager` + `Sales User` gắn đúng node `CRM Sales Hierarchy` (`Team A`, `Team B`, `Team C`).
   - `staff_a1`, `staff_a2`, `staff_b1`, `staff_b2`, `staff_c1`, `staff_c2`: `Sales User` gắn đúng node đội.
   - `staff_disabled`: `staff.disabled@example.test` (`Sales User`, `enabled = 0`).
   - `staff_outside`: `staff.outside@example.test` (`Sales User`, không nằm trong `CRM Sales Hierarchy`).
   - `guest`: phiên HTTP không cookie / chưa đăng nhập.
2. **Kiểm tra đa mặt phẳng (Multi-Plane Enforcement — kế thừa bài học `L-29` của P1):**
   Mọi DocType và hành động P2 (`CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH Outcome Code`, `CSKH Policy`, cùng `CSKH Work Item`, `CRM Lead`, `CRM Task`, `CRM Call Log`, `FCRM Note`, `File`) phải được kiểm tra đồng thời trên **4 mặt phẳng**:
   - **Plane 1 — Custom API (`crm_cskh.api.*`):** JSON body, `application/x-www-form-urlencoded`, query string, thiếu CSRF / dùng `GET` cho mutation.
   - **Plane 2 — Generic Frappe REST:** `GET/POST/PUT/DELETE /api/resource/<DocType>[/<name>]` và `/api/v2/document/<DocType>[/<name>]`.
   - **Plane 3 — Generic `frappe.client.*`:** `get`, `get_list`, `get_count`, `get_value`, `insert`, `save`, `set_value`, `delete`, `submit`, `cancel`, `validate_link`.
   - **Plane 4 — Desk / List / Filter / Search / Export:** `frappe.desk.form.load.getdoc`, `frappe.desk.reportview.get`, `frappe.desk.reportview.get_count`, `frappe.desk.search.search_link`, `frappe.desk.search.search_widget`, `frappe.desk.reportview.export_query`, `frappe.core.doctype.data_export.exporter.export_data`, `frappe.desk.query_report.run`, và filter-oracle trên các trường `permlevel >= 1`.
3. **Kiểm raw JSON + Positive Control bắt buộc:**
   - Không kết luận `PASS` chỉ dựa trên HTTP status code `403`/`200`.
   - Mỗi phép thử âm (negative test) phải đi kèm: (a) **positive control khác `0`** với đúng actor có quyền trên cùng fixture để chứng minh endpoint không trả rỗng giả tạo; (b) quét **raw response text/JSON** bảo đảm `0` rò SĐT, `0` rò ghi chú liên hệ (`note`), `0` rò `/private/files/...`, `0` stacktrace nội bộ; (c) hậu kiểm **MariaDB** xác nhận `0` bản ghi mồ côi / `0` thay đổi ngoài phép.

---

## 2. Cảnh báo kiến trúc sớm từ Baseline `1913d2b` (`WP-OBS-01`)

> [!IMPORTANT]
> **Watchpoint `WP-OBS-01` (Đã chứng minh thực nghiệm trên `t1cp2.localhost` tại `1913d2b`):**
> Trên P1 (`apps/crm_cskh/crm_cskh/p1_permissions.py` dòng `224–234` & `344–364`), API tùy biến `crm_cskh.api.ownership.history` chặn cựu owner `staff.a1` (`HTTP 403`) sau khi `wi_ab` chuyển `A → B`, nhưng `assignment_history_query_conditions` và `assignment_history_has_permission` trên generic REST `/api/resource/CSKH Assignment History` lại lọc theo `to_user = <actor>` (trả `HTTP 200`, `1` dòng lịch sử giao cũ; không chứa PII ở P1).
>
> **Hệ quả bắt buộc cho P2 (`SEC2-01`, `SEC2-05`, `SEC2-07`, `ADR-P2-10`):**
> Trên **`CSKH Contact Attempt`** và **`CSKH Attempt Correction`** (có chứa ghi chú CSKH, kết quả liên hệ, liên kết `CRM Call Log` / `CRM Task` / `Lead`), `permission_query_conditions` và `has_permission` **TUYỆT ĐỐI KHÔNG** được lọc theo `performed_by = user` hay `requested_by = user` độc lập! Quyền đọc của `Staff` và `Leader` trên mọi bản ghi lịch sử P2 **phải neo vào chủ sở hữu hiện tại của `CSKH Work Item`** (`work_item.assigned_to` đang active trong scope của actor), sao cho ngay khi `reassign(A → B)` hoặc `revoke` hoàn tất, `Staff A` và `Leader A` mất quyền đọc cả qua custom API lẫn `/api/resource/CSKH Contact Attempt` / `reportview` / `getdoc`.

---

## 3. Ma trận Quyền × Đối tượng × Mặt phẳng truy cập (P2 Target Matrix)

*Ký hiệu kỳ vọng:* `ALLOW (Scoped)` = Cho phép đúng phạm vi server-derived; `ALLOW (No-PII)` = Cho phép xem tổng hợp/tiến độ không lộ SĐT/tên/ghi chú nhạy cảm theo policy; `DENY` = Từ chối (`403`/`404` chuẩn hóa, `0` side-effect).

| Bề mặt / Hành động | Guest | Staff (`Sales User`) | Former Owner (`Staff A` sau `A→B`) | Disabled Staff (`enabled=0`) | Team Leader (`Sales Manager`) | BOD (`CSKH BOD Viewer`, `0` SM) | CRM Admin (`CSKH CRM Admin`, `0` SM) |
|---|---|---|---|---|---|---|---|
| **Đọc Work Item & Timeline CSKH hiện hành** (Custom API) | `DENY` | `ALLOW` (chỉ `assigned_to = self`) | `DENY` (`0` PII, `0` note cũ) | `DENY` (session bị khóa) | `ALLOW` (chỉ subtree đội) | `ALLOW (No-PII)` (theo policy ADR-P2-10/11) | `ALLOW` (toàn hệ thống CSKH) |
| **Ghi nhận `CSKH Contact Attempt` mới** (Custom Orchestration API) | `DENY` | `ALLOW` (chỉ WI active của `self`; `performed_by = session.user`; `self_reported`) | `DENY` (`CONFLICT` / `403`) | `DENY` | `DENY` (trừ khi là owner hợp lệ hoặc policy ghi thay được ADR duyệt; không giả mạo `performed_by`) | `DENY` | Theo ADR-P2 (chỉ correction/admin flow có audit, không giả mạo nhân viên) |
| **Generic CRUD trên `CSKH Contact Attempt`** (`/api/resource`, `frappe.client.*`, `Desk`) | `DENY` | `READ` chỉ WI hiện hữu của `self`; **`CREATE/WRITE/DELETE/EXPORT = DENY`** | **`READ/WRITE = DENY`** | `DENY` | `READ` chỉ WI hiện hữu thuộc subtree đội; **`CREATE/WRITE/DELETE/EXPORT = DENY`** | **`WRITE/DELETE/EXPORT = DENY`**; `READ` không lộ PII trái policy | `READ` toàn cục; **`CREATE/WRITE/DELETE` trực tiếp = `DENY`** (bắt buộc qua service/correction) |
| **Yêu cầu `CSKH Attempt Correction`** (Custom API) | `DENY` | `ALLOW` (tạo request chờ duyệt cho attempt hợp lệ trong scope; không sửa gốc) | `DENY` (theo ADR-P2-10 trừ khi policy cho phép không lộ PII) | `DENY` | Theo ADR-P2 (xem/review trong đội nếu được cấp; không sửa gốc) | `DENY` | `ALLOW` (duyệt / từ chối / void qua correction service có reason + audit) |
| **Generic CRUD trên `CSKH Attempt Correction`** (`/api/resource`, `frappe.client.*`, `Desk`) | `DENY` | **`CREATE/WRITE/DELETE = DENY`**; `READ` chỉ trong scope hiện tại | `DENY` | `DENY` | **`CREATE/WRITE/DELETE = DENY`**; `READ` theo subtree hiện tại | `DENY` mutation | **`WRITE/DELETE` trực tiếp = `DENY`** |
| **Đặt / Đổi hạn / Hoàn tất / Hủy Callback (`CRM Task`)** (Custom Follow-up API) | `DENY` | `ALLOW` (chỉ WI active của `self`) | `DENY` (sau `A→B`, `B` sở hữu Task) | `DENY` | Theo ADR-P2 (xem đội; không cướp quyền ngoài scope) | `DENY` | `ALLOW` qua luồng điều phối được kiểm soát |
| **Generic CRUD trên `CRM Task` / `CRM Call Log` / `FCRM Note`** | `DENY` | `READ` chỉ Lead đang sở hữu (`E2-07` `PASS`); **`CREATE/WRITE/DELETE = DENY`** | **`READ/WRITE = DENY`** (`E2-07` `PASS`) | `DENY` | `READ` theo Lead đội; **`CREATE/WRITE/DELETE = DENY`** | **`READ/WRITE = DENY`** trên PII/mutation | **`CREATE/WRITE/DELETE` generic = `DENY`** |
| **Đóng (`close`) / Mở lại (`reopen`) `CSKH Work Item`** | `DENY` | `close`: chỉ WI active của `self` khi đủ điều kiện policy; `reopen`: `DENY` | `DENY` | `DENY` | `reopen`: chỉ khi ADR-P2 cấp quyền có reason; mặc định `CRM Admin` | `DENY` | `ALLOW` (`close`/`reopen` có reason + event audit, không xóa history) |
| **Cấu hình `CSKH Outcome Code` / `CSKH Policy`** | `DENY` | `READ` danh mục active cho form; **`WRITE = DENY`** | `DENY` write | `DENY` | `READ` danh mục; **`WRITE = DENY`** | `READ` cấu hình; **`WRITE = DENY`** | `ALLOW` qua `settings` API (không xóa cứng mã đã dùng, giữ snapshot lịch sử) |
| **Progress Overview P2** (Custom Reporting API) | `DENY` | Chỉ số cá nhân (`scope = self`), không mở rộng qua `user=`/`team=` | Chỉ phạm vi hiện hành của bản thân | `DENY` | Chỉ subtree đội (`scope = team`), `team=` ngoài nhánh trả rỗng/`403` | Toàn cục `scope = all`, **`0` PII**, không mutation | Toàn cục `scope = system` |

---

## 4. Đặc tả & Sổ theo dõi `SEC2-01 → SEC2-16`

| ID | Kịch bản tấn công (`P2.md §15.3`) | Mặt phẳng kiểm tra bắt buộc | Fixture & Actor dự kiến (`t1cp2.localhost`) | Kỳ vọng bảo mật & Hậu kiểm DB | Trạng thái trên `1913d2b` |
|---|---|---|---|---|---|
| **SEC2-01** | `Staff A` đọc danh sách / chi tiết Attempts của `Staff B` | Custom timeline API + `/api/resource/CSKH Contact Attempt` + `frappe.client.get_list` + `reportview.get` + `getdoc` | `staff.a1` truy vấn WI của `staff.b1` (cùng `CAMP-P2-SHARED` và khác `CAMP-P2-B`); positive control: `staff.b1` đọc `200` | `403`/`404` hoặc `items=[]` đúng chuẩn bề mặt; `0` PII/note rò rỉ trong raw JSON | `NOT_IMPLEMENTED_YET` |
| **SEC2-02** | `Staff A` gửi `POST` tạo Attempt / Follow-up với `work_item_id` của `Staff B` (hoặc WI `queued`/`revoked`/`closed`) | Custom workflow API (JSON + form) | `staff.a1` gửi `work_item = wi_b1`, `wi_queued_a`, `wi_rev`, `wi_closed` | `403` / `CONFLICT`; MariaDB `COUNT(CSKH Contact Attempt)` và `COUNT(CRM Task)` không tăng | `NOT_IMPLEMENTED_YET` |
| **SEC2-03** | `Staff A` gửi payload giả mạo `performed_by = staff.b1` / `employee = leader.a` / `actor = cskh.admin` | Custom workflow API + Follow-up API + Correction API | `staff.a1` trên `wi_aba` gửi thêm `performed_by`, `owner`, `modified_by`, `employee` | Bị từ chối (`400`/`403`) hoặc cưỡng bức tuyệt đối theo `frappe.session.user` theo contract đã chốt; `0` bản ghi mang tên `staff.b1` | `NOT_IMPLEMENTED_YET` |
| **SEC2-04** | `Staff A` gửi `verification_status = "verified"` / `verified_by` / `is_connected = 1` trái định nghĩa `Outcome Code` | Custom workflow API | `staff.a1` gửi `outcome = no_answer` kèm `verification_status = "verified"`, `is_connected = true` | Bị từ chối hoặc khóa cứng `verification_status = "self_reported"` và cờ lấy từ `CSKH Outcome Code` server-side; `0` record `verified` giả | `NOT_IMPLEMENTED_YET` |
| **SEC2-05** | `Staff A` / `Leader A` / `CRM Admin` chèn, sửa, xóa thẳng DocType P2 qua Frappe API chung | `/api/resource/*`, `/api/v2/document/*`, `frappe.client.insert/set_value/save/delete`, `delete_bulk_docs` | Thử trên `CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH Outcome Code`, `CSKH Policy`, `CSKH Work Item` | `403` trên toàn bộ đường generic mutation; nền `CSKH Work Item` đã `PASS` tại `E2-06` (`1913d2b`) | `PARTIAL_BASELINE` (`WI` `PASS`; DocType P2 `NOT_IMPLEMENTED_YET`) |
| **SEC2-06** | `Staff A` liên kết `CRM Call Log` / `CRM Task` / `File` của khách `B` (hoặc của Lead khác cùng owner) vào Attempt của `wi_aba` | Custom workflow / call_log_adapter / followup API | `staff.a1` truyền `call_log = call_ab` / `task = task_ab` / `lead = lead_ab` vào request của `wi_aba` | `403`/`400` FK mismatch & cross-owner check; không lộ nội dung `call_ab`/`task_ab`; `0` liên kết chéo | `NOT_IMPLEMENTED_YET` |
| **SEC2-07** | `Staff A` sau khi `reassign(A → B)` dùng lại URL/bookmark/API cũ để xem khách, timeline, ghi chú cũ, callback hoặc tạo Attempt | `/cskh/*`, Custom timeline/detail/workflow API, `/api/resource/CRM Lead`, `CRM Task`, `CRM Call Log`, `FCRM Note`, `File`, `CSKH Contact Attempt` | `wi_ab` (`staff.a1 → staff.b1`); positive control: `staff.b1` đọc `200` | `403`/`404` trên mọi mặt phẳng (kể cả generic REST — lưu ý **`WP-OBS-01`**!); nền `Lead/Task/Call/Note/File` đã `PASS` tại `E2-07` (`1913d2b`) | `PARTIAL_BASELINE` (`E2-07` `PASS`; P2 Timeline/Attempt `NOT_IMPLEMENTED_YET`) |
| **SEC2-08** | `Leader A` truyền `team = Team B`, `campaign = CAMP-P2-B`, `owner = staff.b1` trên Timeline, Follow-ups, Progress Overview và Desk `reportview`/`search_link` | Custom APIs + `reportview.get` + `search_link` + `export_query` | `leader.a` truy vấn dữ liệu `Team B` và các dòng thuộc `staff.b1` trong `CAMP-P2-SHARED` | `0` bản ghi ngoài subtree `Team A` (kể cả trong chiến dịch dùng chung `CAMP-P2-SHARED`!); positive control `leader.b` thấy đủ dữ liệu `Team B` | `NOT_IMPLEMENTED_YET` |
| **SEC2-09** | `BOD Viewer` cố gọi `POST` tạo Attempt, đổi hẹn, `close`/`reopen` Work Item, duyệt Correction hoặc sửa `Outcome Code`/`Policy`; đồng thời kiểm tra rò PII trên mọi API đọc | Custom APIs + Generic REST/Desk/Export | `cskh.bod@example.test` (`0` `System Manager`) | Mọi mutation `403`; mọi API đọc của BOD trả **`0` SĐT, `0` tên khách nhạy cảm/ghi chú raw** trái policy (`PHONE_RE` = `0`) | `NOT_IMPLEMENTED_YET` |
| **SEC2-10** | Stored / Reflected XSS trong `note`, `reason`, `outcome label`, tên khách (`<script>alert(1)</script>`, `<img src=x onerror=...>`, `javascript:...`) | Custom API lưu + UI render (Timeline, Follow-ups, Correction modal, Overview) | Payload XSS trên `wi_aba`, mở thật bằng trình duyệt/DOM check dưới `staff.a1`, `leader.a`, `cskh.admin` | Không thực thi script, không tạo thẻ/attribute nguy hiểm sống trong DOM; dữ liệu escape/sanitize đúng chuẩn | `NOT_IMPLEMENTED_YET` |
| **SEC2-11** | Gọi mutation bằng `GET` hoặc `POST` không có `X-Frappe-CSRF-Token` / token sai trên phiên cookie | Toàn bộ endpoint ghi P2 (`workflow`, `followup`, `correction`, `settings`) | Phiên cookie của `staff.a1` và `cskh.admin` gửi `GET` hoặc `POST` thiếu/sai CSRF | `400`/`403`/`405`; `0` thay đổi DB | `NOT_IMPLEMENTED_YET` |
| **SEC2-12** | Đoán ID / đường dẫn `File` private hoặc recording URL trong Timeline / Attempt / Call Log | `/private/files/...`, `/api/resource/File/<id>`, `frappe.client.get`, `get_attachments`, Timeline API | `staff.a1` và `guest` dò `file_ab` của `staff.b1`; kiểm tra byte-identical File-oracle P0 | Không lộ sự tồn tại hoặc nội dung file ngoài scope; giữ nguyên bất biến File-oracle P0 (`9/9 PASS` trên `1913d2b`) | `PARTIAL_BASELINE` (P0 File-oracle `PASS`; P2 Timeline `NOT_IMPLEMENTED_YET`) |
| **SEC2-13** | Kiểm tra response lỗi, `Error Log`, worker log và tracing khi request lỗi validation/conflict/permission | HTTP error envelope + bảng `tabError Log` + container logs | Gửi payload lỗi chứa SĐT giả & ghi chú nhạy cảm rồi ép lỗi `FOLLOWUP_REQUIRED` / `IDEMPOTENCY_CONFLICT` / `403` | Envelope chuẩn hóa, không lộ stacktrace SQL nội bộ, không ghi tràn raw PII/secret ra response lỗi ngoài quyền | `NOT_IMPLEMENTED_YET` |
| **SEC2-14** | `Staff A` gọi API tổng hợp tiến độ truyền `user=Administrator` / `user=staff.b1` / `scope=all` | Custom Progress / Overview / Counts API | `staff.a1` truyền tham số mở rộng phạm vi | Server phớt lờ hoặc từ chối mở rộng; con số trả về khớp chính xác SQL `assigned_to = staff.a1` | `NOT_IMPLEMENTED_YET` |
| **SEC2-15** | Nhân viên gửi `occurred_at` (backdate) trước thời điểm `ownership_start` hiện tại (ví dụ `Staff B` trên `wi_ab` ghi lùi giờ về khoảng thời gian của `Staff A`, hoặc `Staff A` trên `wi_aba` ghi lùi về khoảng giữa của `Staff B`!) | Custom workflow API | `wi_ab` (`A → B`) và **`wi_aba` (`A → B → A`, kiểm tra khoảng trống giữa 2 interval của A!)** | Bị từ chối (`400`/`422`) theo `ADR-P2-06`; không ghi lùi vượt trước `ownership_start` của interval hiện hành hoặc ngoài cửa sổ policy | `NOT_IMPLEMENTED_YET` |
| **SEC2-16** | `CRM Admin` (hoặc `System Manager` qua API nghiệp vụ) sửa trực tiếp lịch sử `CSKH Contact Attempt` hoặc sửa `CSKH Outcome Code` làm đổi nghĩa lịch sử cũ | `/api/resource/CSKH Contact Attempt`, `frappe.client.set_value`, `settings` API | `cskh.admin@example.test` thử `PUT` sửa `outcome`/`occurred_at`/`performed_by` trên Attempt đã lưu; đổi nhãn/cờ Outcome Code đã phát sinh Attempt | Sửa thẳng Attempt bị chặn (`403` / immutable controller); sửa sai chỉ đi qua `CSKH Attempt Correction` append-only; Attempt cũ giữ nguyên snapshot ngữ nghĩa (`T2-18`) | `NOT_IMPLEMENTED_YET` |

---

## 5. Mẫu ghi nhận Finding bắt buộc của T1C (Áp dụng cho mọi vòng kiểm định P2)

Mỗi lỗi phát hiện trong quá trình kiểm định delta phải ghi đủ **7 trường bắt buộc** (không nêu nhận xét mơ hồ):
1. **Mã Finding & Mức độ (`Critical` / `High` / `Medium` / `Low`) + Gate/Test/SEC bị chặn**
2. **SHA thực chạy:** `HEAD SHA`, `Code Tree/Ancestor SHA`, `Synthetic Merge SHA` (nếu qua CI PR), trạng thái migration.
3. **Actor & Vai trò thực tế:** email giả lập (`@example.test`), danh sách role thực tế (`0` dùng `Administrator` thay `CSKH CRM Admin`).
4. **Fixture tái hiện:** campaign, team hierarchy, Work Item state, lịch sử chuyển giao (`A → B` / `A → B → A`), Attempt/Task/Policy liên quan.
5. **Request cụ thể:** method, URL/endpoint (custom API vs generic REST/client/Desk), headers (cookie/CSRF), payload đã redact PII.
6. **Expected vs Actual:** HTTP status, raw JSON/SQL state mong đợi so với phản hồi và trạng thái DB thực tế (kèm positive control đối chứng).
7. **Ảnh hưởng nghiệp vụ/bảo mật & Điều kiện đóng (Closure Criteria):** điều kiện kiểm chứng runtime cụ thể để T1C tái chạy và đóng lỗi.
