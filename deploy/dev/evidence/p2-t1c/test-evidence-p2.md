# P2 Test & Runtime Evidence Ledger — CRM-CSKH (T1C Sở hữu & Kiểm định Độc lập)

- **Chủ sở hữu tài liệu:** **T1C** (Kiểm định độc lập Bảo mật, Chất lượng & Runtime — P2)
- **Tài liệu gốc đối chiếu:** [`docs/root-plan/P2.md`](../../../docs/root-plan/P2.md) (§2, §14, §15, §16, §17)
- **Baseline khóa phiên bản:** [`docs/architecture/version-lock.md`](../../../docs/architecture/version-lock.md)
- **Artifact thực nghiệm đầu kỳ của T1C:** [`artifacts/t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json)
- **Trạng thái hiện hành (2026-11-03):**
  - **Entry Gate (`G2-01` / `E2-01..E2-10`):** **`PASS`** trên `main` `1913d2bd5ebfdc894eaa492b46be502dbdf44808`.
  - **Functional / Security / Resilience / Performance P2 (`T2-01..T2-24`, `SEC2-01..SEC2-16`, `WP10`, `G2-02..G2-15`):** **`NOT_IMPLEMENTED_YET` / `NOT_MEASURED_YET`** (chờ các mốc bàn giao SHA thực tế từ T1B/T1A; tuyệt đối không ghi `PASS` sớm).

---

## 1. Sổ cái Provenance các Revision đã kiểm định (Tách biệt HEAD SHA và Synthetic Merge SHA)

| Mốc kiểm định | Nhánh / PR | Branch `HEAD SHA` | `Synthetic Merge SHA` (nếu PR) | Upstream `frappe` / `crm` SHA | Nguồn CI & SHA-256 Artifact (T1C tự tải & parse) | Môi trường local cô lập của T1C | Phạm vi kết luận |
|---|---|---|---|---|---|---|---|
| **Mốc 0 — Entry Baseline (`2026-11-03`)** | `origin/main` (sau Merge PR #5) | `1913d2bd5ebfdc894eaa492b46be502dbdf44808` | Không áp dụng (`push` trên `main`; `merge_sha == head_sha`) | `frappe`: `d2efa79657ba5c1abf96098b3bfacc199517d57e` (`v15.123.1`)<br>`crm`: `0d3910b456ef4fb913e77b29db011b3debc4f778` (`v1.87.0`) | **CI P0 `#41658199683`** — Artifact `13240019557` (`150,251` B)<br>SHA-256: `a326e0294435c05cb5980eb133d63df6ecb069f798af16c64f7462f8457e364d` (`99/99` P0 core + `8/8` aux + `18/18` HTTP + `9/9` file-oracle + `74/74` gaps + `backup_restore=success`)<br>**CI P1 `#41658199861`** — Artifact `13240299250` (`63,599` B)<br>SHA-256: `ef40ab2df6a27d7be404e3ffd11da3980d4ecc2382f7452cfd8bd9e42b74884b` (`209/209` P1 backend + `12/12` UI + `170/170` HTTP PASS) | Worktree `D:/HRP-app/t1c-p2-verify`<br>Project `crm-cskh-t1c-p2` (`WEB_PORT=18320`)<br>Site `t1cp2.localhost`<br>MariaDB `11.8.9`, TZ `Asia/Ho_Chi_Minh`<br>`migrate × 2` PASS + `21/21` HTTP/DB entry checks PASS + P1 backup snapshot | **`E2-01..E2-10` = `PASS`**<br>**`G2-01` = `PASS`**<br>`G2-02..G2-15` = `NOT_IMPLEMENTED_YET` |

---

## 2. Bằng chứng Nền P0/P1 Tái sử dụng Hợp lệ (Không chạy lại toàn bộ nếu không đổi code liên quan)

Theo chỉ đạo của T0, T1C bảo toàn và tái sử dụng các bằng chứng nền trên `1913d2bd5ebfdc894eaa492b46be502dbdf44808` khi không có diff đụng chạm:
1. **P0 Core & Transport Guards (`CI #41658199683`, đã tự tải & parse XML/JSON):**
   - `5` module JUnit core = `99/99 PASS` (`test_permissions` `18`, `test_security_remediation` `12`, `test_security_remediation_v2` `32`, `test_docshare_lifecycle` `10`, `test_bt1c5_matrix_gaps` `27`) + `test_error_response_normalization` `8/8 PASS` (`0` fail, `0` error, `0` skip).
   - `http-transport.json`: `18/18 PASS`; `file-oracle.json`: `9/9 PASS`; `remaining-gaps.json`: `74/74 PASS`; `backup_restore`: `success`.
2. **P1 Backend + UI + HTTP Transport (`CI #41658199861`, đã tự tải & parse XML/JSON):**
   - `8` module backend JUnit = `209/209 PASS` + `1` module UI JUnit = `12/12 PASS` (tổng `221/221` testcase XML, `0` fail, `0` error, `0` skip).
   - `p1-http-transport.json`: `170/170 PASS` trên `bench serve` + RQ worker thật.
   - **Điều kiện buộc tái kiểm hồi quy P0/P1 trong P2:** Bất kỳ thay đổi nào trên `hooks.py`, `permissions.py`, `p1_permissions.py`, `p1_models.py`, `services/ownership.py`, `services/eligibility.py`, `services/distribution.py`, `services/reporting.py`, `api/guarded_crm.py`, `api/ownership_ui.py`, `api/customers.py`, hoặc custom fields trên `CRM Lead` / `CRM Task` / `CRM Call Log` đều kích hoạt kiểm định delta hồi quy trực tiếp trên các đường bị chạm (đặc biệt là 24 vị trí `status == 'assigned'` đã lập danh mục tại báo cáo T1C).

---

## 3. Bộ Fixture Chuẩn Độc lập của T1C trên `t1cp2.localhost` (Đã khởi tạo & chụp Backup P1)

Được tạo bởi probe độc lập `t1c_p2_entry_and_fixture_probe.py` (kết quả lưu tại [`artifacts/t1c-p2-entry-baseline-1913d2b.json`](./artifacts/t1c-p2-entry-baseline-1913d2b.json), `100%` domain `@example.test`, `0` SĐT thật, đã chụp `bench --site t1cp2.localhost backup --with-files` để phục vụ `WP10` migrate trên dữ liệu P1 thật):

- **Cây tổ chức (`CRM Sales Hierarchy` bật `enable_sales_hierarchy = 1`):**
  - `vp.root@example.test` (node gốc) → 3 nhánh độc lập:
    - **`Team A`:** `leader.a@example.test` (`Sales Manager`, `Sales User`) quản lý `staff.a1@example.test`, `staff.a2@example.test`, và `staff.disabled@example.test` (`enabled = 0` sau khi tạo rule).
    - **`Team B`:** `leader.b@example.test` (`Sales Manager`, `Sales User`) quản lý `staff.b1@example.test`, `staff.b2@example.test`.
    - **`Team C`:** `leader.c@example.test` (`Sales Manager`, `Sales User`) quản lý `staff.c1@example.test`, `staff.c2@example.test`.
  - **User ngoài cây:** `staff.outside@example.test` (`Sales User`, không có node hierarchy).
  - **Vai trò quản trị/lãnh đạo tách biệt tuyệt đối (`0` `System Manager`):** `cskh.admin@example.test` (`["CSKH CRM Admin"]`), `cskh.bod@example.test` (`["CSKH BOD Viewer"]`), và `Guest`.
- **Chiến dịch & Dữ liệu P1 nền (`108` Leads, `108` Identities, `108` Work Items, `105` Active Claims):**
  - `CAMP-P2-SHARED` (`team_scope = NULL`, `40` Work Items): gồm **`wi_aba`** (`Lead A → B → A`: `staff.a1 → staff.b1 → staff.a1`, `3` khoảng `CSKH Assignment History` liền mạch, chỉ `1` khoảng mở, `DocShare = ['staff.a1@example.test']`), **`wi_ab`** (`Lead A → B`: `staff.a1` là cựu owner, `staff.b1` là đương nhiệm, đính kèm sẵn `CRM Task`, `CRM Call Log`, `FCRM Note`, private `File`), **`wi_rev`** (`revoked`, `return_to_pool=False`), **`wi_ret`** (`queued`, `return_to_pool=True`), **`wi_closed`** (`closed`), cùng các WI phân bổ cho `a1/b1/c1` và hàng chờ `queued`.
  - `CAMP-P2-A` (`team_scope = Team A`, `35` Work Items: `25` `assigned` cho `a1/a2`, `10` `queued`, có rule của `staff.disabled`).
  - `CAMP-P2-B` (`team_scope = Team B`, `33` Work Items: `25` `assigned` cho `b1/b2`, `8` `queued`).
- **Phần mở rộng Fixture sẽ nạp ngay khi nhận SHA schema/service P2 (`WP02–WP05`):**
  - `30+` `CSKH Contact Attempt` đa kênh (`phone_call`, `zalo`, `sms`, `facebook`, `other`) và đa kết quả (`no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `connected/interested`, `wrong_number`, `do_not_contact`, terminal completion).
  - Tập `CRM Call Log` khớp link, sai Lead, trùng link, và chưa link Attempt.
  - Tập `CRM Task` callback ở 5 trạng thái thời gian theo `Asia/Ho_Chi_Minh`: hôm qua (`Overdue`), hôm nay (`Due Today` sát biên `00:00:00+07` và `23:59:59+07`), tương lai (`Upcoming`), `Done`, `Canceled`, và bàn giao `A → B`.
  - `2` phiên bản `CSKH Outcome Code` / `CSKH Policy` đổi nhãn/cờ sau khi đã có Attempt lịch sử + các ca `CSKH Attempt Correction` (`pending`, `approved`, `rejected`/`void`).

---

## 4. Sổ theo dõi Bằng chứng Chức năng `T2-01 → T2-24` (`P2.md §15.2`)

| Test ID | Kịch bản kiểm định | Lớp bằng chứng yêu cầu (Unit + HTTP + Browser + SQL) | Gate liên kết | SHA đã kiểm | Trạng thái hiện hành |
|---|---|---|---|---|---|
| **T2-01** | `Staff` mở `My Customers` sau P1: hiển thị đúng Work Items hiện hành (`assigned`, `in_progress`, `on_hold`) + bộ lọc | HTTP actor `staff.a1`/`staff.b1` + Browser DOM + SQL count | `G2-03`, `G2-13` | `1913d2b` (chỉ phần P1 `assigned` = `PASS`) | `NOT_IMPLEMENTED_YET` (chưa có `in_progress`/`on_hold`/filters P2) |
| **T2-02** | Gọi điện `no_answer`: tạo `1` Attempt, `is_connected=false`, `Work Item.status = in_progress` (giữ nguyên `Active Claim` & capacity!) | HTTP POST + SQL đối chiếu `CSKH Contact Attempt`, `CSKH Work Item`, `CSKH Active Claim`, `_capacity_usage` | `G2-03`, `G2-05`, `G2-06` | — | `NOT_IMPLEMENTED_YET` |
| **T2-03** | Gửi SMS/Zalo `message_sent_no_reply`: Attempt hợp lệ, **không** tạo `CRM Call Log` giả, **không** tính `connected` | HTTP POST + SQL đối chiếu `CRM Call Log = 0`, `connected_count = 0` | `G2-04`, `G2-06`, `G2-08` | — | `NOT_IMPLEMENTED_YET` |
| **T2-04** | Khách quan tâm qua cuộc gọi: `is_connected=true`, `is_interested=true`, không tự đánh dấu converted/closed nếu chưa đóng | HTTP POST + SQL đối chiếu | `G2-04`, `G2-05`, `G2-06` | — | `NOT_IMPLEMENTED_YET` |
| **T2-05** | Outcome yêu cầu callback nhưng thiếu thời gian hẹn: trả `FOLLOWUP_REQUIRED`, rollback toàn bộ (không ghi Attempt lẻ) | HTTP POST negative + SQL count trước/sau bằng nhau | `G2-04`, `G2-07`, `G2-09` | — | `NOT_IMPLEMENTED_YET` |
| **T2-06** | Ghi Attempt + hẹn chăm sóc lại trong 1 thao tác: tạo Attempt + `CRM Task` + cập nhật `Work Item` (`on_hold`, `next_action_due`) nguyên tử | HTTP POST + fault-injection rollback test + SQL | `G2-03`, `G2-07`, `G2-08`, `G2-09` | — | `NOT_IMPLEMENTED_YET` |
| **T2-07** | Hoàn tất/hủy `CRM Task` mà chưa liên hệ thực tế: bắt buộc lý do hợp lệ, **không** tự sinh Attempt connected giả | HTTP POST + SQL đối chiếu `attempt_count`, `connected_count` | `G2-06`, `G2-08`, `G2-09` | — | `NOT_IMPLEMENTED_YET` |
| **T2-08** | Retry cùng `request_id` + cùng payload (tuần tự & song song N=2/N=5): không nhân đôi Attempt/Task, trả lại kết quả đã ghi | Multi-process HTTP/service race + MariaDB unique key check | `G2-07` | — | `NOT_IMPLEMENTED_YET` |
| **T2-09** | Tái sử dụng `request_id` cũ với payload khác (đổi outcome/note/due/work_item): trả `IDEMPOTENCY_CONFLICT` | HTTP POST negative + SQL không đổi bản ghi gốc | `G2-07` | — | `NOT_IMPLEMENTED_YET` |
| **T2-10** | Hai tab của cùng nhân viên đồng thời lưu Attempt / đóng `Work Item`: khóa dòng `FOR UPDATE` / version check, conflict rõ ràng, không mất cập nhật | Concurrent HTTP/process probe (`N=2`) | `G2-03`, `G2-07` | — | `NOT_IMPLEMENTED_YET` |
| **T2-11** | `Staff A` submit Attempt đồng thời `CRM Admin` chạy `ownership.reassign(A → B)`: serializable qua lock `CSKH Work Item`; không ghi sai owner/interval | Concurrent process race (`Staff A` vs `CRM Admin`) + hậu kiểm `CSKH Assignment History` & `performed_by` | `G2-07`, `G2-10` | — | `NOT_IMPLEMENTED_YET` |
| **T2-12** | Bàn giao `A → B` khi đang có callback `CRM Task` mở: `Task.assigned_to` chuyển sang `B`, **`due_date` giữ nguyên không reset**, `A` mất quyền đọc/sửa | HTTP `ownership.reassign` + kiểm quyền `A` vs `B` trên Task/Timeline + SQL `due_date` | `G2-09`, `G2-10`, `G2-12` | — | `NOT_IMPLEMENTED_YET` |
| **T2-13** | Đóng `Work Item` (`status = closed`): giải phóng/đóng `Active Claim` đúng chuẩn P1, không còn open callback `CRM Task` mồ côi, chặn `Staff` ghi Attempt mới | HTTP POST + SQL đối chiếu `CSKH Active Claim`, `CRM Task`, `CSKH Assignment History` | `G2-03`, `G2-09`, `G2-10` | — | `NOT_IMPLEMENTED_YET` |
| **T2-14** | `Reopen` `Work Item` đã `closed`: chỉ actor có quyền, bắt buộc reason, ghi audit event, khôi phục/kiểm tra `Active Claim` hợp lệ không phá bất biến P1 (`<=1` active WI/Lead!), giữ nguyên history cũ | HTTP POST (`CRM Admin` vs `Staff` negative) + kiểm tra đụng độ nếu Lead đã vào campaign khác! | `G2-03`, `G2-11`, `G2-12` | — | `NOT_IMPLEMENTED_YET` |
| **T2-15** | `wrong_number` / `do_not_contact` (DNC): không xóa `CRM Lead`, gắn cờ/trạng thái theo policy `ADR-P2-07`, chặn tiếp tục liên hệ/phân phối trái policy | HTTP POST + kiểm tra eligibility P1/P2 | `G2-04`, `G2-03` | — | `NOT_IMPLEMENTED_YET` |
| **T2-16** | Liên kết một `CRM Call Log` vào Attempt hai lần (hoặc từ hai Attempt): unique DB constraint chặn trùng, tiến độ không đếm đôi | Service/HTTP duplicate link test + `SHOW INDEX` MariaDB | `G2-07`, `G2-08`, `G2-14` | — | `NOT_IMPLEMENTED_YET` |
| **T2-17** | Tạo `FCRM Note` hoặc `CRM Task` độc lập: `attempt_count` và `attempted_work_items` giữ nguyên không tăng | SQL reconciliation trước/sau | `G2-08`, `G2-14` | — | `NOT_IMPLEMENTED_YET` |
| **T2-18** | Admin đổi nhãn/cờ `CSKH Outcome Code` hoặc `CSKH Policy` version sau khi đã có Attempt: lịch sử và thống kê Attempt cũ giữ nguyên ngữ nghĩa snapshot | HTTP settings mutation + đối chiếu Timeline & Progress trước/sau | `G2-04`, `G2-05`, `G2-14` | — | `NOT_IMPLEMENTED_YET` |
| **T2-19** | `Staff` gửi yêu cầu `CSKH Attempt Correction`: tạo sự kiện chờ duyệt, bản ghi `CSKH Contact Attempt` gốc bất biến (`modified`/nội dung gốc không bị ghi đè) | HTTP POST dưới `staff.a1` + SQL so sánh hash hàng gốc | `G2-05`, `G2-11` | — | `NOT_IMPLEMENTED_YET` |
| **T2-20** | `CRM Admin` duyệt / void `CSKH Attempt Correction`: effective read-model cập nhật đúng (ví dụ đổi `connected` → `no_answer` hoặc void Attempt nhầm), không double count, đủ audit actor/time/reason | HTTP POST dưới `cskh.admin` (`0` SM) + SQL đối soát Progress & Timeline | `G2-06`, `G2-11`, `G2-14` | — | `NOT_IMPLEMENTED_YET` |
| **T2-21** | Bộ lọc lịch hẹn `Hôm nay` / `Sắp đến hạn` / `Quá hạn` / `Đã hoàn tất` / `Đã hủy`: chuẩn `Asia/Ho_Chi_Minh` (`UTC+07`), đúng tại ranh giới `23:59:59+07` ↔ `00:00:00+07` | HTTP GET theo từng filter + SQL đối chiếu trực tiếp | `G2-09`, `G2-14` | — | `NOT_IMPLEMENTED_YET` |
| **T2-22** | Đối soát tổng hợp `10` Leads / `15` Attempts (kèm correction + multi-attempt trên 1 WI + Call Log): tách bạch `work_item_count`, `attempted_count`, `attempt_count`, `connected_count`, `open_callbacks`, `closed_count` | Script đối soát SQL độc lập của T1C theo 5 scope (`Staff`, `Leader A/B`, `BOD`, `CRM Admin`) | `G2-06`, `G2-14` | — | `NOT_IMPLEMENTED_YET` |
| **T2-23** | Hồi quy P1 khi chuyển chiến dịch / import lại Lead đã qua quy trình P2 (`in_progress`, `on_hold`, `closed`, `reopen`, `DNC`): không vỡ `CSKH Active Claim`, không lọt cross-team `L-23` trên 3 API Kho | Replay probe `L-23` + `L-25` + `distribution.preview/execute` trên Work Items P2 | `G2-03`, `G2-10`, `G2-15` | — | `NOT_IMPLEMENTED_YET` |
| **T2-24** | Việt hóa toàn bộ UI P2 (`vi_VN`): form Ghi nhận kết quả, Timeline CSKH, Danh sách hẹn lại, Overview tiến độ, Cấu hình Outcome/Policy, toast & thông báo lỗi | Kiểm tra DOM/i18n + ảnh chụp màn hình 4 vai trò (`Staff`, `Leader`, `CRM Admin`, `BOD`) | `G2-13` | — | `NOT_IMPLEMENTED_YET` |

---

## 5. Sổ theo dõi Độ bền, Di trú & Hiệu năng `WP10` (`P2.md §15.4`, `§16`, `G2-02`, `G2-07`, `G2-15`)

> **Nguyên tắc đo hiệu năng của T1C:** Ghi nhận trung thực thời gian thực chạy (`p50`/`p95`/wall-clock), kế hoạch truy vấn `EXPLAIN`, slow queries và mức tiêu thụ tài nguyên trên môi trường cô lập `crm-cskh-t1c-p2`; **không tự bịa ngưỡng SLA không có trong `P2.md`**.

| Mã hạng mục WP10 | Nội dung kiểm định bắt buộc | Phương pháp & Quy mô dữ liệu trên `crm-cskh-t1c-p2` | Trạng thái hiện hành |
|---|---|---|---|
| **WP10-MIG-01** | `install` mới + `bench migrate × 2` trên site sạch và trên **bản sao lưu dữ liệu P1 thật (`108` Work Items)** đã chụp tại `t1cp2.localhost` | Restore snapshot P1 `1913d2b` → checkout SHA P2 → chạy `bench migrate` lần 1 & lần 2 → kiểm tra `0` lỗi, giữ nguyên toàn bộ `108` Lead/WI/History/Claim của P1, kiểm tra indexes MariaDB | `BASELINE_SNAPSHOT_READY` (`NOT_MEASURED_ON_P2_YET`) |
| **WP10-BAK-02** | `backup` → `restore` sang site cô lập mới (`t1cp2-restore.localhost`) khi đã có đầy đủ `Attempts`, `CRM Tasks`, `Corrections`, `Outcome Codes`, `Policies` và khóa `idempotency` | Đối chiếu count từng bảng + hash mẫu + thử gửi lại `request_id` cũ sau restore (phải giữ nguyên tính idempotent!) + `bench migrate` sau restore | `NOT_MEASURED_YET` |
| **WP10-TXN-03** | Fault injection lỗi tạo `CRM Task` / lỗi adapter `CRM Call Log` sau khi tạo `Attempt`, và lỗi thông báo realtime sau commit | Inject exception ở tầng test/probe (không sửa code nguồn): (a) lỗi tạo Task → rollback sạch `Attempt` + `Work Item.status`; (b) lỗi socket sau commit → dữ liệu nghiệp vụ vẫn bền vững | `NOT_MEASURED_YET` |
| **WP10-PAG-04** | Phân trang Timeline trên **1 Work Item có `100+` Attempts** (`>= 125` Attempts giả lập) | Đo kích thước payload JSON, số bản ghi mỗi trang (`page=1`, `page=2`, ...), thứ tự thời gian ổn định, không trả toàn bộ `125` ghi chú trong 1 response | `NOT_MEASURED_YET` |
| **WP10-BMK-05** | Benchmark dữ liệu giả quy mô **`10.000+` `CSKH Work Item` và `50.000+` `CSKH Contact Attempt`** (kèm `CRM Task` hẹn lại) trên site benchmark cô lập (`t1cp2-bench.localhost`) | Đo thời gian thực tế + `EXPLAIN` cho: (1) `My Customers` (`assigned_to`, status filter); (2) `Timeline` theo `work_item`/`occurred_at`; (3) `Follow-up list` theo `due_date` (`Today`/`Overdue`); (4) `Progress Overview` cho `Leader` & `BOD`; (5) Tải đồng thời `20–50` phiên giả lập đọc/ghi hỗn hợp (ghi nhận latency, lỗi, CPU/RAM container) | `NOT_MEASURED_YET` |
