# P4-00 — Kiểm tra đầu vào

**Ngày rà soát:** 05/11/2026. **Quyết định chuyển pha:** Owner duyệt GO P4 và đóng G20 của P3. Bước hiện tại là chuẩn bị phát hành và kiểm tra đầu vào trên baseline đã hợp nhất. [Kế hoạch P4](../root-plan/P4.md)

## 1. Mốc nguồn và bằng chứng

- **Baseline `main` sau PR #12:** `92e14af3532876ad5bc2468bf0e502474cc95a53`. Mốc T1C Vòng 2 là `453ce2f0d975182414a251c5cc49ceb462404f83` với cây `apps` `aa3610a8672d1bdee672f5ac5b02d833de6463b7`. Cây `apps` trên baseline mới là `b37e2cbdd99c88c7bd5620912de4519b53b2c159`. Đã đối chiếu diff và xác nhận thay đổi duy nhất trong `apps/` là fixture kiểm thử KPI phụ thuộc giờ chạy. Commit ký duyệt `5406ed7` và commit fixture `2da22aa` có mặt trong lịch sử merge. [PR #12](https://github.com/nobita6986/new-crm/pull/12)
- **P0 — GO P1.** 10/10 cổng trong phạm vi DEV đã ký. Giữ caveat tương thích MariaDB, hiệu năng và ranh giới proxy/CORS; đo lại ở topology Staging. [P0 Exit Report](../architecture/p0-exit-report.md) · [ADR UI cùng site](../architecture/adr-ui-001.md)
- **P1 — 14/14 cổng kỹ thuật, 29/29 phát hiện đóng.** T1C kiểm định kho khách, import, phân phối, quyền và lịch sử phụ trách. PR #5 đã merge `1913d2bd5ebfdc894eaa492b46be502dbdf44808`. Ghi nhận P1 GO theo E2-01 của hồ sơ mở P2; trạng thái OPEN trong các mục lịch sử P1 thuộc mốc trước merge. [P1 Exit Report](../architecture/P1-Exit-Report.md) · [P2 Entry Report](../P2-entry-report.md) · [PR #5](https://github.com/nobita6986/new-crm/pull/5)
- **P2 — GO P3.** 15/15 cổng đạt, 16/16 ca bảo mật và 6/6 phát hiện đóng. Evidence gồm Contact Attempt, lifecycle, handover và quyền. [P2 Exit Report](../P2-Exit-Report.md)
- **P3 — GO P4.** 20/20 cổng, gồm 19 cổng kỹ thuật do T1C kiểm định và G20 do Owner duyệt. KPI `cskh-metrics-v2`, SLA, drill-down BOD và phân quyền được kiểm trên mốc code đã ghim. Manifest tích hợp 17/17 hash và hồ sơ T1C cục bộ 18/18 hash khớp khi rà lại; phạm vi tệp T1C được ghi trong Exit Report. [P3 Exit Report](../p3/P3-EXIT-REPORT.md) · [Manifest E2E](../../deploy/p3/evidence/SHA256SUMS.txt)
- **CI trên `main`:** 4/4 workflow thành công trên `92e14af`. Cây Git khớp synthetic merge đã chạy trước merge `721ebb2d1ef1c697d83ac8301e152adf337bfcd0`. Hồ sơ đóng gói ghi HTTP E2E 196/196 và Browser E2E 84/84 trên code ứng dụng đã kiểm định. [P0](https://github.com/nobita6986/new-crm/actions/runs/42040341612) · [P1](https://github.com/nobita6986/new-crm/actions/runs/42040341559) · [P2](https://github.com/nobita6986/new-crm/actions/runs/42040341564) · [P3](https://github.com/nobita6986/new-crm/actions/runs/42040341587)
- **Artifact P3 trên `main`:** Đối chiếu SHA trong metadata, kiểm manifest SHA-256 và tự parse `39/39` test backend + `7/7` test UI. Metadata ghi 9/9 bước thành công, gồm HTTP probe, schema và backup/restore; số bản ghi kiểm tra sau restore khớp. Ca KPI đã sửa cũng PASS trong JUnit thô trước merge. [Run P3 trên main](https://github.com/nobita6986/new-crm/actions/runs/42040341587) · [Run P3 trước merge](https://github.com/nobita6986/new-crm/actions/runs/42039174507)

## 2. Khóa phiên bản

- Frappe `v15.123.1` → `d2efa79657ba5c1abf96098b3bfacc199517d57e` và CRM `v1.87.0` → `0d3910b456ef4fb913e77b29db011b3debc4f778`. `crm_cskh` khai báo `0.0.1`, do đó nhận diện bản build bằng source SHA và digest, tránh dùng riêng số phiên bản app. Ba app và hai SHA upstream đã đối chiếu với metadata runtime T1C Vòng 2.
- `frappe_docker` `v4.0.1` → `2b26e9ab8d751e5ea33bb7b930aea3550bb81ded` là mốc khảo sát. Khóa DEV của Bench, MariaDB và Redis nằm ở §5; production image, digest và tương thích DB phải được chứng minh ở P4-01 và Staging. [Version lock](../architecture/version-lock.md) · [Inventory hạ tầng](./infra-inventory.md)

## 3. Schema, quyền và jobs

Đã đọc 23 định nghĩa `CSKH*` từ DocType JSON tại cây app đã ghim. Đây là kiểm kê mã nguồn; kiểm kê MariaDB trên site Staging và đối chiếu `list-apps`, field, index vật lý còn chờ diễn tập.

- **P0 (1):** `CSKH App Settings` (Single). CRM Lead/Deal và các chứng từ liên kết dùng hook phạm vi quyền, lớp API guarded và chuẩn hóa lỗi theo baseline P0.
- **P1 (9):** `CSKH Campaign`, `Assignment Rule`, `Work Item`, `Assignment History`, `Import Batch`, `Import Batch Lead`, `Distribution Run`, `Identity Index`, `Active Claim`. Khóa chính nghiệp vụ gồm `normalized_phone`, `(campaign, lead)`, `(campaign, user, version)`, `(import_batch, lead)` và một active claim cho mỗi Lead. `Assignment History` giữ `event_id` và cửa sổ ownership. [Định nghĩa index P1](../../apps/crm_cskh/crm_cskh/p1_models.py)
- **P2 (4):** `CSKH Outcome Code`, `Policy`, `Contact Attempt`, `Attempt Correction`. Đối chiếu `verification_status`, `policy_version` trên Work Item, idempotency theo `(work_item, performed_by, idempotency_key)`, liên kết Call Log duy nhất và correction theo `(requested_by, idempotency_key)`. Các field DNC của Lead và liên kết follow-up trên CRM Task được cài bằng patch. [Schema P2](../../apps/crm_cskh/crm_cskh/p2_models.py)
- **P3 (9):** `CSKH Team Membership History`, `Working Calendar`, `Working Shift` (bảng con), `SLA Obligation`, `Dirty Scope`, `KPI Run`, `KPI Snapshot`, `QA Review`, `Export Job`. Giữ đúng `metric_version`, `original_due_at`/`effective_due_at`, các khóa idempotency, lịch sử quy kết và snapshot khi migrate/recompute. [Schema P3](../../apps/crm_cskh/crm_cskh/p3_models.py) · [Thứ tự migration](../../apps/crm_cskh/crm_cskh/patches.txt)
- **Phạm vi truy cập:** Staff theo hồ sơ được giao, Leader theo phạm vi đội, BOD theo báo cáo đã che PII và CRM Admin theo phạm vi quản trị nghiệp vụ. Kiểm lại REST/RPC/Desk, file và export trong P4-04. Scheduler chạy đánh giá SLA và xử lý Dirty Scope mỗi 5 phút, hết hạn export mỗi giờ, làm mới kỳ chuẩn hằng ngày. [Hook quyền và scheduler](../../apps/crm_cskh/crm_cskh/hooks.py) · [Ma trận quyền P3](../p3/authorization-matrix.md)

## 4. Việc cần khóa trước khi dùng dữ liệu thật

- **Dữ liệu.** Dùng bộ seed tổng hợp có guard site kiểm thử. Lập kiểm kê PII cấp field, log, export và nơi lưu bản sao trước Staging/UAT. Điểm xuất phát gồm số điện thoại trên Lead/Identity Index, file import và attachment, nội dung lịch sử liên hệ và file export. [Seed E2E](../../apps/crm_cskh/crm_cskh/tools/p3_e2e_seed.py)
- **Hạ tầng và trách nhiệm.** Chưa có bằng chứng cho VPS/DNS, kho secret, đích backup ngoài VPS hay danh sách người chịu trách nhiệm triển khai, nghiệm thu UAT và quyết định rollback. Xác nhận những mục này trước khi dựng Production. [Inventory hạ tầng](./infra-inventory.md)
- **Bảo mật và DR.** P4-04 đo lại quyền sau khi qua proxy HTTPS và rà hành vi `File.owner` còn ghi ở OBS-P2-03. P4-05 phải restore thành công DB, public/private files và site config/encryption key trên site cô lập.
- **Hiệu năng.** P4-06 đo drill-down trên >50k Work Item/tháng, giữ che PII trước search/sort và đối soát với snapshot khi chuyển sang phân trang SQL. Đo import chunking, dung lượng DB và concurrency trên topology Staging.
- **Chính sách vận hành.** Giữ cấu hình QA và số lần thử tối thiểu ở trạng thái DEV hiện tại. Duyệt ngưỡng SLA, trọng số và quy tắc lấy mẫu với BOD và bộ phận CSKH trước pilot. [Giới hạn chuyển giao P3, mục 8](../p3/P3-EXIT-REPORT.md)
