# P2 Security Matrix — CRM-CSKH (T1C Sở hữu & Kiểm định Độc lập)

- **Chủ sở hữu tài liệu:** **T1C** (Kiểm định độc lập Bảo mật, Chất lượng & Runtime — P2)
- **Tài liệu gốc đối chiếu:** [`docs/root-plan/P2.md`](../../../../docs/root-plan/P2.md) (§0, §8, §10, §15.3, §17 `G2-10` / `G2-11` / `G2-12`)
- **Báo cáo nghiệm thu chính:** [`t1c-p2-verification-report.md`](./t1c-p2-verification-report.md)
- **Artifacts thực nghiệm độc lập của T1C:**
  - Entry Baseline (`1913d2b`, bảo toàn): [`artifacts/t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json)
  - Runtime Tích hợp P2 (`26b0237` / `ffe74df`, `apps/` tree `4a395ae619f95a1feb0fbf9ec189063af2938f76`): [`artifacts/t1c-p2-runtime-26b0237.json`](./artifacts/t1c-p2-runtime-26b0237.json)
- **Môi trường kiểm định cô lập của T1C:** Worktree `D:/HRP-app/t1c-p2-verify` · Docker project `crm-cskh-t1c-p2` (`WEB_PORT=18320`) · Site `t1cp2.localhost`
- **Trạng thái hiện hành (`2026-11-04` trên `26b0237` & `ffe74df`):**
  - **Kết quả `SEC2-01 → SEC2-16`:** **`14/16 PASS`**, **`2/16 FAIL`** (`SEC2-05` bị chặn bởi `F-P2-06` mức `HIGH`; `SEC2-09` bị chặn bởi `F-P2-01` mức `HIGH`).
  - **Kết luận cổng bảo mật `G2-12`:** **`FAIL`** *(giữ `GO P3 = NO-GO` đến khi đóng cả `F-P2-01` và `F-P2-06`)*.

---

## 1. Nguyên tắc & Tính Trung thực Actor đã Xác minh Runtime (`actor_fidelity = PASS`)

1. **Đúng actor nghiệp vụ (đã kiểm chứng SQL/HTTP trực tiếp trong `t1c-p2-runtime-26b0237.json`, `0` dùng `Administrator` thay `CRM Admin`):**
   - `crm_admin`: `cskh.admin@example.test` — `enabled = 1`, roles = `["CSKH CRM Admin"]` (**`0` `System Manager`**).
   - `bod`: `cskh.bod@example.test` — `enabled = 1`, roles = `["CSKH BOD Viewer"]` (**`0` `System Manager`**).
   - `leader_a` / `leader_b` / `leader_c`: `leader.{a,b,c}@example.test` — `enabled = 1`, roles = `["Sales Manager", "Sales User"]`, gắn đúng node `CRM Sales Hierarchy` (`Team A`, `Team B`, `Team C`).
   - `staff_a1`, `staff_a2`, `staff_b1`, `staff_b2`, `staff_c1`: `enabled = 1`, roles = `["Sales User"]`, gắn đúng nhánh đội.
   - `staff_disabled`: `staff.disabled@example.test` — `enabled = 0`, `POST /api/method/login` trả **`HTTP 401`** (phiên bị chặn ngay từ tầng xác thực).
   - `staff_outside`: `staff.outside@example.test` — `enabled = 1`, `["Sales User"]` không thuộc `CRM Sales Hierarchy`.
   - `guest`: phiên HTTP không cookie / chưa đăng nhập.
2. **Kiểm tra đa mặt phẳng (Multi-Plane Enforcement — kế thừa bài học `L-29` của P1):**
   Mọi DocType và hành động P2 (`CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH Outcome Code`, `CSKH Policy`, cùng `CSKH Work Item`, `CSKH Assignment History`, `CRM Lead`, `CRM Task`, `CRM Call Log`, `FCRM Note`, `File`) được kiểm tra đồng thời trên **5 mặt phẳng**:
   - **Plane 1 — Custom API (`crm_cskh.api.*`):** JSON body, `application/x-www-form-urlencoded`, query string, thiếu CSRF / dùng `GET` cho mutation.
   - **Plane 2 — Generic Frappe REST v1 & v2:** `GET/POST/PUT/DELETE /api/resource/<DocType>[/<name>]` và `/api/v2/document/<DocType>[/<name>]`.
   - **Plane 3 — Generic `frappe.client.*`:** `get`, `get_list`, `get_count`, `get_value`, `insert`, `save`, `set_value`, `delete`, `validate_link`.
   - **Plane 4 — Desk / List / Filter / Search / Export:** `frappe.desk.form.load.getdoc`, `frappe.desk.reportview.get`, `frappe.desk.reportview.get_count`, `frappe.desk.search.search_link`, `frappe.desk.reportview.export_query`, và filter-oracle trên các trường `permlevel >= 1`.
   - **Plane 5 — Private File & HTML Routes:** `/private/files/...`, `/api/resource/File/<id>`, `get_attachments`, `/cskh/*`.
3. **Positive Control + Quét Raw JSON + Hậu kiểm MariaDB:**
   - Mọi phép thử âm đều đi kèm positive control khác `0` của đúng actor sở hữu hợp lệ (`staff.b1`, `leader.b`, `cskh.admin`), quét raw JSON tìm SĐT/note/stacktrace, và đối soát trực tiếp bảng MariaDB. Nhờ kiểm tra đa mặt phẳng này, T1C đã phát hiện chính xác **2 lỗ hổng mức `HIGH`** (`F-P2-01` trên `CSKH Assignment History.reason` và `F-P2-06` trên `PUT /api/resource/CRM Lead/<id>`).

---

## 2. Kết quả Xác minh Watchpoint `WP-OBS-01` từ Baseline `1913d2b`

> [!NOTE]
> **Kết luận `WP-OBS-01` trên `26b0237` / `ffe74df`: ĐÃ ĐÓNG SẠCH (`CLOSED / PASS`) đối với cựu Owner & cựu Leader.**
> - Ở Entry `1913d2b`, `assignment_history_query_conditions` cho phép cựu owner đọc lại dòng giao cũ qua `/api/resource/CSKH Assignment History`.
> - Trên bản tích hợp P2 (`p2_permissions.py` & `p1_permissions.py` cập nhật), T1C đã kiểm thử trực tiếp trên `wi_xfer` sau khi chuyển `staff.a1 → staff.b1` khi đang mở callback `on_hold`:
>   - **Cựu owner `staff.a1` & cựu leader `leader.a`:** `workflow.get_work_item = 403`, `workflow.list_attempts = 403`, `workflow.record_attempt = 409`, `/api/resource/CSKH Contact Attempt/<id> = 403` (list `len = 0`), `/api/resource/CSKH Attempt Correction/<id> = 403` (list `len = 0`), `/api/resource/CRM Task/<id> = 403`, `/api/resource/CRM Call Log/<id> = 403`, `/api/resource/CRM Lead/<id> = 403`, `/api/v2/document/CSKH Contact Attempt/<id> = 403`, `desk.form.load.getdoc = 403`, `desk.reportview.get = []`, `client.validate_link = {}/403`, `/api/resource/File/<id> = 404`, `/private/files/<unique_xfer_file> = 403`.
>   - **Positive control:** Chủ sở hữu mới `staff.b1` đọc `200` toàn bộ Work Item, Timeline, `CRM Task` (giữ nguyên `due_date`) và file riêng tư; `leader.b` duyệt yêu cầu `CSKH Attempt Correction` thành công (`HTTP 200`).

---

## 3. Ma trận Quyền × Đối tượng × Mặt phẳng Thực tế trên `26b0237` / `ffe74df`

| Bề mặt / Hành động | Guest | Staff (`Sales User`) | Former Owner (`Staff A` sau `A→B`) | Disabled Staff (`enabled=0`) | Team Leader (`Sales Manager`) | BOD (`CSKH BOD Viewer`, `0` SM) | CRM Admin (`CSKH CRM Admin`, `0` SM) |
|---|---|---|---|---|---|---|---|
| **Đọc Work Item & Timeline CSKH hiện hành** | `403` (`PASS`) | `200` chỉ `assigned_to=self` (`PASS`) | `403` (`PASS`) | `401` login (`PASS`) | `200` chỉ subtree đội (`PASS`) | `403` trên PII; **NHƯNG LỘ PII QUA `ownership.history` (`F-P2-01` `FAIL`)** | `200` toàn hệ thống (`PASS`) |
| **Ghi nhận `CSKH Contact Attempt` mới** | `403` (`PASS`) | `200` chỉ WI active của `self`; chặn spoof (`PASS`) | `409`/`403` (`PASS`) | `401` (`PASS`) | `403` khi không là owner (`PASS`) | `403` (`PASS`) | Qua workflow/correction hợp lệ (`PASS`) |
| **Generic CRUD trên `CSKH Contact Attempt`** | `403` (`PASS`) | `READ` chỉ WI hiện hữu của `self`; `POST/PUT/DELETE = 403` (`PASS`) | `403` / `[]` trên mọi mặt phẳng (`PASS`) | `401` (`PASS`) | `READ` theo subtree hiện hữu; `POST/PUT/DELETE = 403` (`PASS`) | `READ/WRITE = 403` (`PASS`) | `READ = 200`; `POST/PUT/DELETE` trực tiếp = `403` (`PASS`) |
| **Generic CRUD trên `CSKH Assignment History`** | `403` (`PASS`) | `READ` chỉ WI hiện hữu của `self`; `WRITE = 403` (`PASS`) | `403` / `[]` (`PASS`) | `401` (`PASS`) | `READ` theo subtree hiện hữu; `WRITE = 403` (`PASS`) | `WRITE = 403`; **`READ = 200` LỘ SĐT, GHI CHÚ DNC & LÝ DO `permlevel=1` (`F-P2-01` `FAIL`)** | `READ = 200`; `WRITE = 403` (`PASS`) |
| **Generic CRUD trên `CRM Lead` (trường DNC P2)** | `403` (`PASS`) | **`PUT /api/resource/CRM Lead/<id>` = `200` GHI/XÓA THẲNG `cskh_do_not_contact` (`F-P2-06` `FAIL`)** | `403` (`PASS`) | `401` (`PASS`) | Tương tự nếu có `DocShare(write=1)` (`F-P2-06`) | `403` (`PASS`) | Cưỡng bức qua `reopen_work_item(override_dnc=1)` bị Staff bypass (`F-P2-06`) |
| **Yêu cầu & Duyệt `CSKH Attempt Correction`** | `403` (`PASS`) | `request_correction = 200`; tự duyệt/void = `403` (`PASS`) | `403` (`PASS`) | `401` (`PASS`) | Duyệt trong đội = `200`; `restore` voided = `403` (`PASS`) | `403` (`PASS`) | Duyệt / void / `restore` = `200`; hàng gốc Attempt bất biến 100% (`PASS`) |
| **Callback (`CRM Task`) & Generic CRUD `CRM Task`** | `403` (`PASS`) | Qua `followup.* = 200`; generic `POST/PUT/DELETE CRM Task = 403` (`PASS`) | `403` (`PASS`) | `401` (`PASS`) | Theo subtree; generic `WRITE = 403` (`PASS`) | `403` (`PASS`) | Generic `WRITE` ngoài service = `403` (`PASS`) |
| **Cấu hình `CSKH Outcome Code` / `CSKH Policy`** | `403` (`PASS`) | `list_outcome_codes = 200`; `save_* = 403` (`PASS`) | `403` write (`PASS`) | `401` (`PASS`) | `READ = 200`; `WRITE = 403` (`PASS`) | `READ = 200` (`0` PII); `WRITE = 403` (`PASS`) | `save_outcome_code` / `save_policy = 200`; xóa mã đã dùng = `403` (`PASS`) |
| **Progress Overview (`/cskh/progress`)** | `403` (`PASS`) | Khóa cứng `scope = self` (`PASS`) | Chỉ WI hiện hành của `self` (`PASS`) | `401` (`PASS`) | Khóa cứng subtree đội (`PASS`) | `200` (`0` PII keys, `0` SĐT) (`PASS`) | `200` toàn hệ thống (`PASS`) |

---

## 4. Sổ cái Kết quả Kiểm định Runtime `SEC2-01 → SEC2-16` (`26b0237` & `ffe74df`)

| ID | Kịch bản kiểm định (`P2.md §15.3`) | Mặt phẳng & Request đã thực thi trên `t1cp2.localhost:18320` | Kết quả Runtime Thực tế & Positive Control | Trạng thái |
|---|---|---|---|---|
| **SEC2-01** | `Staff A` đọc danh sách / chi tiết Attempts của `Staff B` | `staff.a1` gọi `workflow.list_attempts(wi_b1)` (`403`), `/api/resource/CSKH Contact Attempt/<att_b1>` (`403`), `/api/resource/CSKH Contact Attempt` list, `frappe.client.get_list`, `reportview.get`, `getdoc` | Toàn bộ trả `403` hoặc chỉ chứa WI của `staff.a1` (`0` dòng của `staff.b1`, `0` PII rò rỉ); positive control `staff.b1` đọc `200` | **`PASS`** |
| **SEC2-02** | `Staff A` gửi `POST` tạo Attempt / Follow-up với `work_item` của `Staff B`, `queued`, `revoked` hoặc `closed` | `staff.a1` gọi `workflow.record_attempt` & `followup.reschedule_followup` (cả JSON lẫn form-urlencoded) vào `wi_b1`, `wi_queued`, `wi_rev`, `wi_closed` | Chặn `403 FORBIDDEN` (ngoài quyền) và `409 CONFLICT` (`closed`/`revoked`); MariaDB `COUNT(CSKH Contact Attempt)` và `COUNT(CRM Task)` giữ nguyên không tăng | **`PASS`** |
| **SEC2-03** | `Staff A` giả mạo `performed_by = staff.b1` / `owner` / `employee` | `staff.a1` gửi `performed_by="staff.b1@example.test"` trong JSON body và `application/x-www-form-urlencoded` tới `workflow.record_attempt` | Bị từ chối cứng **`HTTP 403 FORBIDDEN`** (`0` bản ghi giả mạo tên nhân viên khác được ghi vào MariaDB) | **`PASS`** |
| **SEC2-04** | `Staff A` giả mạo `verification_status = "verified"` hoặc `is_connected = 1` trái `Outcome Code` | `staff.a1` gửi `verification_status="verified"` kèm `outcome_code="no_answer"` (JSON & form) | `verification_status="verified"` bị từ chối **`HTTP 403 FORBIDDEN`**; các cờ `is_connected`/`is_interested` luôn tính từ `CSKH Outcome Code` phía server (`0` record `verified` giả) | **`PASS`** |
| **SEC2-05** | Chèn, sửa, xóa thẳng DocType P2 & các trường bảo vệ P2 qua Frappe API chung | Thử `POST/PUT/DELETE /api/resource/*`, `/api/v2/document/*`, `frappe.client.insert/set_value/delete` dưới `staff.a1`, `leader.a`, `cskh.admin` trên `CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH Outcome Code`, `CSKH Policy`, `CRM Task`, **`CRM Lead`** | Các DocType P2 mới & `CRM Task` chặn `403` (`0` thay đổi DB); **TUY NHIÊN `staff.a1` gọi `PUT /api/resource/CRM Lead/CRM-LEAD-2026-00193` đạt `HTTP 200`, tự ý ghi `cskh_do_not_contact = 1` và xóa `= 0` trực tiếp trong DB!** Xem **[`F-P2-06`](./t1c-p2-verification-report.md#f-p2-06)** (`HIGH`) | **`FAIL` (`F-P2-06`)** |
| **SEC2-06** | `Staff A` liên kết `CRM Call Log` / `CRM Task` của khách khác vào Attempt của mình | `staff.a1` truyền `call_log` của Lead khác vào `workflow.record_attempt` và `followup.complete_followup` với `attempt_id` lệch Work Item | Chặn **`HTTP 422`** (`OUTCOME_NOT_COMPATIBLE` / mismatch), không lộ dữ liệu `Call Log` ngoại lai, `0` liên kết chéo trong DB | **`PASS`** |
| **SEC2-07** | `Staff A` & `Leader A` sau khi `reassign(A → B)` dùng lại URL/API cũ để đọc khách, timeline, ghi chú cũ, callback hoặc tạo Attempt | Kiểm đủ 14 điểm truy cập trên `wi_xfer` (`staff.a1 → staff.b1`): Custom APIs, REST v1/v2, `getdoc`, `reportview`, `validate_link`, `/api/resource/File/<id>`, `/private/files/<unique_xfer_file>` | Cả `staff.a1` lẫn `leader.a` bị chặn `403`/`404`/`[]` trên toàn bộ mặt phẳng (**đóng sạch `WP-OBS-01`**); positive control `staff.b1` đọc `200` | **`PASS`** |
| **SEC2-08** | `Leader A` truyền `team = Team B`, `campaign = CAMP-P2-B`, `assignee = staff.b1` trên Timeline, Follow-ups, Progress, `reportview`, `search_link` | `leader.a` truy vấn dữ liệu `Team B` và các dòng thuộc `staff.b1` ngay trong chiến dịch dùng chung `CAMP-P2-SHARED` | `0` bản ghi ngoài subtree `Team A` trên mọi bề mặt (kể cả trong `CAMP-P2-SHARED`); positive control `leader.b` đọc đủ dữ liệu `Team B` | **`PASS`** |
| **SEC2-09** | `BOD Viewer` cố gọi mutation P2 & kiểm tra bất biến **`BOD 0 PII`** trên mọi API đọc | `cskh.bod@example.test` (`0` SM) gọi toàn bộ mutation (`workflow`, `followup`, `correction`, `settings` → đều `403`) và đọc `progress_summary`, `ownership.history`, `/api/resource/CSKH Assignment History`, `frappe.client.get_list`, `reportview.get`, `getdoc` | Toàn bộ mutation bị chặn `403` và `progress_summary` trả `0` PII; **TUY NHIÊN trên `CSKH Assignment History` (`kqi4jaem0m`, `kqjmldeo4o`), cả 5 mặt phẳng đọc đều trả `HTTP 200` cho `cskh.bod` chứa nguyên văn ghi chú + SĐT khách (khi auto-close DNC) và `completion_reason`/`reopen_reason` (`permlevel=1`)!** Xem **[`F-P2-01`](./t1c-p2-verification-report.md#f-p2-01)** (`HIGH`) | **`FAIL` (`F-P2-01`)** |
| **SEC2-10** | Stored / Reflected XSS & CSV formula injection trong `note`, `reason`, `outcome label` | Gửi `<script>window.__p2_xss=1</script><img src=x onerror=alert(1)>` và `=CMD|' /C calc'!A0` vào `note`/`reason`; kiểm response + code render DOM (`esc()`/`textContent`) | Không thực thi script, công thức nguy hiểm được trung hòa an toàn (`'=...`), UI dùng `esc()`/DOM text node an toàn | **`PASS`** |
| **SEC2-11** | Gọi mutation bằng `GET` hoặc `POST` thiếu/sai `X-Frappe-CSRF-Token` trên phiên cookie | Phiên cookie của `staff.a1` và `cskh.admin` gửi `GET` tới `workflow.record_attempt`, `followup.reschedule_followup`, `correction.review_correction`, `settings.save_policy` và `POST` kèm CSRF sai | Toàn bộ bị chặn `HTTP 400`/`403`/`405` trước khi chạm DB (`0` side-effect trong MariaDB) | **`PASS`** |
| **SEC2-12** | Đoán ID / đường dẫn `File` private hoặc recording URL ngoài phạm vi | `staff.a1`, `leader.a`, `cskh.bod` và `guest` truy cập `/api/resource/File/<id>` (`404`), `get_attachments` (đã che URL), và `/private/files/<unique_xfer_file>` của `staff.b1` (`403`) | Chặn đúng quyền sở hữu hiện hành (`403`/`404`, `0` lộ file của người khác/đội cũ; xem thêm ghi nhận kiến trúc upstream Frappe tại `OBS-P2-03`) | **`PASS`** |
| **SEC2-13** | Kiểm tra response lỗi & `tabError Log` khi ép lỗi validation / conflict / permission | Gửi payload chứa chuỗi nhạy cảm rồi ép lỗi `422 FOLLOWUP_REQUIRED`, `409 IDEMPOTENCY_CONFLICT`, `403 FORBIDDEN`; quét HTTP response & `tabError Log` | Envelope lỗi chuẩn hóa `{ok: false, error: {code, message}}`, `0` stacktrace SQL nội bộ trả ra HTTP, `0` ghi tràn PII/secret vào `Error Log` | **`PASS`** |
| **SEC2-14** | `Staff A` truyền `assignee=staff.b1`, `team=Team B`, `scope=system` lên `workflow.progress_summary` | `staff.a1` và `staff.c1` gửi tham số mở rộng phạm vi; đối chiếu kết quả với SQL `assigned_to = session.user` | Server cưỡng bức `scope = "self"` (`staff_c1_self_on_hold_matches_sql = true`), phớt lờ mọi tham số mở rộng phạm vi | **`PASS`** |
| **SEC2-15** | Ghi lùi giờ (`occurred_at`) sang tương lai, quá cửa sổ `90` phút hoặc rơi vào khoảng sở hữu trước trên Lead `A → B` và **`A → B → A`** | Trên `wi_aba` (`staff.a1 → staff.b1 → staff.a1`), `staff.a1` gửi `occurred_at` rơi vào khoảng giữa của `staff.b1`, quá `90` phút trước, và ở tương lai | Toàn bộ bị chặn **`HTTP 422`** (`INVALID_FOLLOWUP_TIME` / interval violation), bảo toàn tuyệt đối ranh giới khoảng sở hữu hiện hành | **`PASS`** |
| **SEC2-16** | `CRM Admin` sửa/xóa trực tiếp lịch sử `CSKH Contact Attempt`, đổi `Outcome Code` làm lệch lịch sử, hoặc dò filter-oracle trên trường `permlevel=1` | `cskh.admin` gọi `PUT/DELETE /api/resource/CSKH Contact Attempt/<id>`; đổi `Outcome Code` lên `v2`; `staff.a1` dò filter `[["CSKH Work Item","completion_reason","like","%...%"]]` trên REST/Desk | `PUT/DELETE` Attempt bị chặn `403` kể cả với `CRM Admin`; filter-oracle `permlevel=1` bị chặn `403`; snapshot lịch sử giữ nguyên `100%` *(lưu ý: nhánh `Staff` tự gỡ `CRM Lead.cskh_do_not_contact` đã được chặn và theo dõi tại `SEC2-05` / `F-P2-06`)* | **`PASS` *(phần Attempt/Outcome/Oracle; nhánh Lead DNC ghi tại `F-P2-06`)*** |
