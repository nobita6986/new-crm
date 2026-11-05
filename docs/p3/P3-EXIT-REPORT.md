# P3 EXIT REPORT — CRM-CSKH Giai đoạn P3 (`SLA, KPI, QA & BOD Quality Dashboard`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** [`docs/p3/P3-EXIT-REPORT.md`](./P3-EXIT-REPORT.md)
> **Kế hoạch gốc:** [`docs/root-plan/P3.md`](../root-plan/P3.md) (§14, §15, §16, §17, §18) & ADR giao diện [`docs/architecture/adr-ui-001.md`](../architecture/adr-ui-001.md), [`docs/architecture/adr-p1-06-ui-integration.md`](../architecture/adr-p1-06-ui-integration.md), [`docs/p3/adrs/ADR-P3-01-to-15.md`](./adrs/ADR-P3-01-to-15.md)
> **Ngày đóng gói hồ sơ tích hợp P3 (Round 2 — Khắc phục `F-P3-01..F-P3-10`):** 05/11/2026
> **Thực hiện bởi:**
> - **T1B (AI Coding — Backend, SLA/KPI/QA Engine, Benchmark & CI):** `P3-00..P3-06`, phần Backend/API của `P3-07/08` (`crm_cskh.api.p3_reporting.*`, `crm_cskh.api.sla.*`, `crm_cskh.api.qa.*`), phần Backend/Benchmark/CI của `P3-09`, và bản khắc phục Backend Round 2 (`160d0ef5` → `bafb6488`, `cskh-metrics-v2`)
> - **T1A (AI Coding — Giao diện `/cskh/*`, Việt hóa `vi_VN`, HTTP/Browser E2E & Đóng gói Exit):** `P3-07` (`6` màn chất lượng & SLA tiếng Việt), phần UI của `P3-08` (drill-down phân trang/tìm kiếm/sắp xếp server-side, xuất báo cáo có ủy quyền, lịch sử revision/restatement), phần E2E/đóng gói của `P3-09`, và bản khắc phục UI/Adapter/E2E Round 2 (`a01cfe6b` → `8b85acc8` → `c62b1c22`)
> - **T1C (Kiểm định Độc lập Bảo mật, Chất lượng & Runtime):** Đã kiểm định Vòng 1 trên `f92fa33` (`5/20 PASS: G01, G03, G06, G11, G19`; `14/20 FAIL: G02, G04, G05, G07..G10, G12..G18`; `1/20 BLOCKED: G20`) → **ĐANG CHỜ KIỂM ĐỊNH LẠI VÒNG 2 (`PENDING T1C VERIFICATION`)**
> **Baseline xuất phát (`main`):** `449fbc26374c3540b07ad86219fa0f33fc84feba` (`GO P3` đã được Owner phê duyệt tại [`docs/P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3)
> **Trạng thái cổng nghiệm thu `G01 .. G20`:** **`PENDING T1C VERIFICATION`** *(dẫn nguyên trạng phán quyết Vòng 1 của T1C trên `f92fa33` và bằng chứng khắc phục `F-P3-01..F-P3-10` trên `c62b1c22`; tuyệt đối không tự chấm `PASS` thay T1C và không tự ký `GO P4`)*

---

## 1. Thông tin Build, Phiên bản Nguồn, Migration & Cờ Cấu hình (`P3.md` §17.3.1)

| Hạng mục | Giá trị thực tế trong kho mã |
|---|---|
| **Baseline `main` đầu vào P3** | `449fbc26374c3540b07ad86219fa0f33fc84feba` *(quyết định điều hành `GO P3` ngày 04/11/2026 tại [`docs/P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3; mốc kỹ thuật P2 `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` giữ nguyên như lịch sử)* |
| **Frappe Framework (pinned)** | `v15.123.1` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`) |
| **Frappe CRM (pinned)** | `v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) |
| **Chuỗi commit Backend `T1B` đã tích hợp** | Vòng 1: PR #8 (`a50390ae`) → PR #9 (`54f62083`) → PR #10 (`4e8606d7`) → PR #11 (`6c3eccc4`) · **Vòng 2 (`codex/p3-backend-r2`):** `160d0ef55703e931e2b6d66a78eaa915d6a4b04d` → **`bafb6488990085ca034ee5561abcfdb6fb1ed95c`** *(khắc phục `F-P3-01..F-P3-10`, nâng cấp `cskh-metrics-v2`, `services/p3_flags.py`, đối soát `t1c_p3_golden_dataset_expected.json`)* |
| **Nhánh & Mốc tích hợp `T1A + T1B` (PR #12)** | Nhánh `codex/p3-ui` — **Source/E2E SHA:** `c62b1c22136970484a4cb44794235f46bf5f4a7e` \| **`apps` tree SHA:** `aa3610a8672d1bdee672f5ac5b02d833de6463b7` *(commit đóng gói tiếp theo chỉ bổ sung `deploy/p3/evidence/*`, `docs/p3/P3-EXIT-REPORT.md` và `docs/p3/ui-contract-and-flows.md`, bảo toàn `HEAD:apps == aa3610a8672d1bdee672f5ac5b02d833de6463b7`; xem manifest & băm SHA-256 tại [`deploy/p3/evidence/http-e2e-report.json`](../../deploy/p3/evidence/http-e2e-report.json), [`deploy/p3/evidence/browser-manifest.json`](../../deploy/p3/evidence/browser-manifest.json), [`deploy/p3/evidence/SHA256SUMS.txt`](../../deploy/p3/evidence/SHA256SUMS.txt))* |
| **Tổng hợp bộ kiểm thử tự động đã chạy trên `c62b1c22`** | **JUnit (`run_suites.py` + `test_ui_p3_pages_and_context`):** `30/30` module, `421` test methods PASS (`P0=107`, `P1=209`, `P1-UI=12`, `P2=38`, `P2-UI=9`, `P3=39` gồm `test_p3_t1c_remediation`, `P3-UI=7`, `failed_modules=[]`) · **HTTP E2E (`verify_p3_http_e2e.py`):** `196/196` assertion PASS · **Chromium Browser E2E (`verify_p3_browser_e2e.mjs`):** `84/84` assertion PASS qua `11` kịch bản & `3` múi giờ trình duyệt, `15` ảnh chụp toàn trang · **Audit dịch thuật (`check_translations_audit.py`):** `0` thiếu khai báo, `0` thiếu dịch, `0` tiếng Anh sót, `0` khai báo mồ côi |
| **Schema & Patches P3** | Bổ sung các DocType & trường cấu hình P3 trên `CSKH Policy`, chỉ mục MariaDB (`ensure_p3_indexes`), dữ liệu hạt giống lịch làm việc chuẩn Việt Nam (`ensure_p3_seeds`), baseline `migration_cutover` (`ensure_cutover_membership_baseline`) và vô hiệu hóa append-only snapshot `v1` cũ (`invalidate_stale_metric_version_snapshots`); `bench migrate` × 2 chạy idempotent `0` lỗi (xem [`migration-and-backfill.md`](./migration-and-backfill.md)) |
| **Feature Flags & Mặc định DEV (`services/p3_flags.py`)** | Bộ giải quyết cờ hợp nhất `resolve_p3_flags()`: cấp site (`cskh_p3_reporting_enabled`, `cskh_p3_sla_enabled`, `cskh_p3_qa_enabled`, `cskh_p3_enabled`) ưu tiên trên `CSKH Policy` (`p3_reporting_enabled = 1`, `p3_sla_enabled = 1`, **`qa_enabled = 0`**, **`min_attempts_enabled = 0`**); tắt cờ ẩn tức thời điều hướng P3, chặn route `/cskh/*` P3 (`403`), trả `503 P3_FEATURE_DISABLED` / `409 POLICY_CONFLICT` trên API P3 và dừng an toàn các RQ worker |
| **Tuyên bố Chính sách DEV (`DEV / configurable`)** | Mọi ngưỡng SLA (`first_attempt_sla_minutes`, `followup_sla_grace_minutes`, `handoff_min_grace_minutes`, `min_exposure_business_minutes`) và trọng số `PROCESS_SCORE` (`25 / 20 / 20 / 20 / 15`, `min_valid_score_weight = 60`) là **thông số mặc định kỹ thuật trên môi trường DEV có thể cấu hình qua `CSKH Policy`**, **không** coi là chính sách vận hành sản xuất đã được BOD phê duyệt chính thức |
| **Môi trường Docker cô lập phục vụ E2E UI** | [`deploy/p3/docker-compose.yml`](../../deploy/p3/docker-compose.yml) — E2E Vòng 2 chạy hoàn toàn trên stack tạm cô lập `cskh-p3-t1a-e2e` (`p3-e2e.localhost:18420`) và đã dọn sạch sau kiểm thử; stack `crm-cskh-p3-ui-*` (`p3ui.localhost:8320`) và `deploy/p3/.env` của Owner được bảo toàn nguyên vẹn `100%` |

---

## 2. Tái sử dụng Upstream (`Reuse`) & Mã nguồn Mới (`P3.md` §17.3.2)

- **Số file mã nguồn upstream (`apps/frappe`, `apps/crm`, `deploy/dev/patches/*`) bị chỉnh sửa thêm trong P3 (cả Backend lẫn UI):** **`0` file** (được xác minh tại [`version-and-upstream-diff.md`](./version-and-upstream-diff.md)).
- **Chiến lược giao diện giữ nguyên nhất quán P1 → P2 → P3 (`ADR-UI-001`, `ADR-P1-06`, `ADR-P3-09`):**
  - Toàn bộ `6` màn hình mới của P3 được triển khai ngay trên Frappe Website cùng site `/cskh/*` (`apps/crm_cskh/crm_cskh/www/cskh/{quality_overview,team_quality,employee_quality,campaign_quality,sla_operations,scorecard}.{py,html}` + `public/cskh/*.js`), dùng chung cookie phiên đăng nhập, CSRF token, cổng kiểm tra quyền phía máy chủ (`www/cskh_context.py`) và bộ từ điển dịch `translations/vi.csv`.
  - Không dựng SPA thứ hai tách rời, không sửa `CRM Dashboard` hay `Activities.vue` của upstream, và không dùng `CRM Lead.sla_status` gốc để chấm SLA CSKH.
- **Chi tiết tái sử dụng (`REUSE` / `EXTEND`) so với tạo mới trong `apps/crm_cskh` (xem đầy đủ tại [`reuse-matrix.md`](./reuse-matrix.md)):**
  1. **REUSE trực tiếp từ Frappe / Frappe CRM & P1–P2:**
     - `CRM Sales Hierarchy`: Cây tổ chức Team/Leader/Staff kết hợp hook `doc_events` ghi nhận lịch sử thành viên vào `CSKH Team Membership History`.
     - `CSKH Work Item` & `CSKH Assignment History`: Nguồn sự thật về khoảng sở hữu (`ownership_start`, `ownership_end`, `to_user`, `owner_at_close`).
     - `CSKH Contact Attempt` & `CSKH Attempt Correction`: Nguồn sự thật duy nhất để tính lượt liên hệ hiệu lực (`compute_effective_attempts`), tách biệt `attempted`, `connected`, `self_reported`, `verified`, `disputed`.
     - `CRM Task` (`cskh_is_followup = 1`): Đầu vào sự kiện đặt hẹn, dời hẹn (`cskh_reschedule_count`), bàn giao và hoàn tất callback cho `FOLLOWUP` SLA.
     - `CRM Call Log`: Đối chiếu thời lượng/băng ghi âm cho QA mà **không bao giờ cộng trùng** vào số lượt liên hệ.
     - `CRM Lead` (`cskh_do_not_contact`, `cskh_dnc_at`): Kiểm tra kỷ luật không liên hệ sau thời điểm DNC (`DNC_COMPLIANCE_RATE`).
  2. **EXTEND có kiểm soát:**
     - `CSKH Policy`: Mở rộng các trường cấu hình lịch làm việc, ngưỡng SLA, trọng số chấm điểm `PROCESS_SCORE` và cờ `qa_enabled` / `min_attempts_enabled` theo thứ tự ưu tiên `Campaign > Team > Global`.
  3. **Tạo mới trong custom app `crm_cskh` (có chứng minh khoảng trống kiến trúc tại [`adrs/ADR-P3-01-to-15.md`](./adrs/ADR-P3-01-to-15.md)):**
     - Lịch & Quy kết lịch sử: `CSKH Working Calendar` (kèm bảng con `CSKH Working Shift`, `CSKH Holiday`), `CSKH Team Membership History`.
     - Nghĩa vụ SLA: `CSKH SLA Obligation` (tách biệt từng nghĩa vụ `FIRST_ATTEMPT`, `FOLLOWUP`, `FIRST_CONNECTED`, `MIN_ATTEMPTS` theo khoảng trách nhiệm).
     - Báo cáo bất biến, tính lại & kiểm định: `CSKH KPI Run`, `CSKH KPI Snapshot`, `CSKH Dirty Scope`, `CSKH QA Review`, `CSKH Export Job`.

---

## 3. Từ điển Chỉ số (`Metric Dictionary`), Độ chính xác & Đủ dữ liệu (`P3.md` §17.3.3)

Toàn bộ danh mục chỉ số thuộc phiên bản công thức **`cskh-metrics-v2`** (`24` chỉ số lõi + các bí danh tương thích, `21` mã `DRILLDOWN_SUPPORTED_METRICS`) được định nghĩa chặt chẽ tại [`kpi-dictionary.md`](./kpi-dictionary.md) và [`scoring-and-data-sufficiency.md`](./scoring-and-data-sufficiency.md), thực thi tập trung duy nhất tại [`services/kpi_calculator.py`](../../apps/crm_cskh/crm_cskh/services/kpi_calculator.py) (**`0` phép tính công thức KPI ở trình duyệt**):

1. **Làm tròn, Chỉ số đếm & Kiểu số chuẩn xác:** Sử dụng `decimal.Decimal` với quy tắc `ROUND_HALF_UP` đến `2` chữ số thập phân (`round_half_up_2`). Với chỉ số đếm (`unit == "count"`), backend trả `denominator = null` và UI hiển thị số nguyên thuần túy (không hiển thị `(N / 1)`). Rollup từ `Nhân viên → Nhóm → Công ty` và từ `Ngày → Tuần/Tháng/Quý/Năm` luôn cộng tử số/mẫu số gốc và tính lại tập `DISTINCT` — **tuyệt đối không lấy trung bình cộng các tỷ lệ phần trăm** (đối chiếu khớp [`p3_golden_expectations.json`](../../apps/crm_cskh/crm_cskh/tests/fixtures/p3_golden_expectations.json) và [`t1c_p3_golden_dataset_expected.json`](../../apps/crm_cskh/crm_cskh/tests/fixtures/t1c_p3_golden_dataset_expected.json)).
2. **Phân biệt đơn vị tính (`Grain`) giữa Cấp Công ty/Chiến dịch và Cấp Nhân viên (`F-P3-01`):**
   - Ở cấp Công ty và Chiến dịch: `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `UNIQUE_ASSIGNED_ITEMS`, `CONNECTED_ITEMS`, `WORK_ITEMS_TOUCHED`, `CONVERSION_RATE`, `OVERDUE_BACKLOG` tính theo **`DISTINCT CSKH Work Item`**.
   - Ở cấp Nhân viên và Nhóm: `ASSIGNED_INTERVALS`, `ELIGIBLE_COVERAGE`, `FIRST_ATTEMPT_SLA_RATE` và **`CONNECTED_RATE` (`connected_intervals / tried_intervals`, loại trừ `is_invalid_phone`)** tính theo **khoảng sở hữu (`ownership_interval`)** — tuyệt đối không chia cho tổng số cuộc gọi `CONTACT_ATTEMPT_COUNT`. Khi một hồ sơ chuyển giao `A -> B -> A`, hệ thống ghi nhận chính xác `1` `DISTINCT Work Item` và `3` `ownership_intervals_count`.
3. **Cohort SLA theo hạn bất biến (`deadline_cohort`, `F-P3-02`) & Quy tắc `Denominator == 0 -> N/A`:**
   - Nghĩa vụ SLA được gán cố định theo kỳ của `effective_due_at` và đánh giá trạng thái tại `as_of`; cuộc gọi trễ ở kỳ `M+1` không bao giờ xóa vi phạm của kỳ `M` hay dịch chuyển nghĩa vụ sang kỳ `M+1`.
   - Khi mẫu số bằng `0` (ví dụ nhân viên `staff_c` chưa có nghĩa vụ đủ điều kiện), backend trả `value: null, numerator: 0, denominator: 0, status: "insufficient_data"`; giao diện [`p3-widgets.js`](../../apps/crm_cskh/crm_cskh/public/cskh/p3-widgets.js) hiển thị rõ **`N/A`** kèm huy hiệu `Chưa đủ dữ liệu (N/A)`.
4. **Điểm tuân thủ quy trình `PROCESS_SCORE` (0–100), Điểm đóng góp thành phần (`F-P3-09`) & Không trộn bảng xếp hạng:**
   - Gồm 5 thành phần chuẩn `P3.md` §7.5 (`DEV / configurable`): `C1_FIRST_ATTEMPT_SLA` (`25` — `FIRST_ATTEMPT_SLA_RATE`), `C2_ELIGIBLE_COVERAGE` (`20` — `ELIGIBLE_COVERAGE`), `C3_DATA_HYGIENE` (`20` — `VALID_OUTCOME_LOGGING_RATE`), `C4_FOLLOWUP_SLA` (`20` — `FOLLOWUP_SLA_RATE`), `C5_QA_QUALITY` (`15` — `QA_PASS_RATE` trên `random_sample`).
   - Trên thang `0–100`, mỗi thành phần tách rõ `weighted_points = (weight * rate) / 100` (`0..weight`, ví dụ `90% × 25 = 22,50 / 25`, không bao giờ phình `100x` thành `2.250`) và `reweighted_points = (weight * rate) / valid_weight` (`26,47`). Khi `qa_enabled = false`, tổng điểm trọng số thô vector chuẩn là **`73,50 / 85`** tương ứng **`PROCESS_SCORE = 86,47 — Tạm tính; thiếu mẫu QA`**.
   - Trên màn `/cskh/employee-quality` ([`employee-quality.js`](../../apps/crm_cskh/crm_cskh/public/cskh/employee-quality.js)), các nhóm **`Đã chốt` (`finalized`)**, **`Tạm tính` (`provisional`)** và **`Chưa đủ dữ liệu` (`insufficient_data`)** được tách thành 3 bảng độc lập — **tuyệt đối không xếp hạng chung điểm `provisional` với điểm `finalized`**.

---

## 4. SLA Giờ làm việc, Chuyển giao `A → B → A` & Quy kết Lịch sử (`P3.md` §17.3.4)

Được đặc tả tại [`calendars-and-holidays.md`](./calendars-and-holidays.md), [`sla-policy-spec.md`](./sla-policy-spec.md), [`ownership-attribution.md`](./ownership-attribution.md) và kiểm chứng bằng [`test_p3_calendar.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_calendar.py) (`9/9` PASS) + [`test_p3_sla_and_attribution.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_sla_and_attribution.py) (`7/7` PASS):

1. **Lịch làm việc & Ngày nghỉ (`services/calendar.py`):**
   - Hỗ trợ múi giờ chuẩn `Asia/Ho_Chi_Minh`, ca sáng/chiều (`08:00–12:00`, `13:00–17:00`), nghỉ cuối tuần và danh sách ngày lễ (`CSKH Holiday`); chụp cứng `calendar_snapshot_json` vào từng `CSKH SLA Obligation` để việc sửa lịch tương lai không làm lệch hạn quá khứ.
   - Quy tắc biên nhất quán: `occurred_at <= effective_due_at` được tính là **đúng hạn (`fulfilled_on_time`)**; `occurred_at > effective_due_at` là **hoàn tất trễ (`fulfilled_late`)**.
2. **Bất biến hạn gốc & Chống lách SLA khi bàn giao / dời hẹn (`services/sla_engine.py`):**
   - `original_due_at` bị khóa bất biến ngay trong `CSKH SLA Obligation.validate()` (`PolicyConflict` nếu cố sửa).
   - **Bàn giao trước hạn (`A -> B` khi chưa quá `effective_due_at`):** Nghĩa vụ của `A` đóng hợp lệ (`handoff_closed`, không phạt `A`); `B` nhận nghĩa vụ mới có thời gian ân hạn tối thiểu `handoff_min_grace_minutes` nhưng bị chặn trần `max_handoff_grace_resets` để không thể chuyền vòng `A -> B -> A` nhằm reset hạn vô tận.
   - **Bàn giao sau khi đã vi phạm (`A -> B` sau `effective_due_at`):** Nghĩa vụ của `A` giữ nguyên lỗi vi phạm (`breached_handoff`); `B` nhận nghĩa vụ khôi phục (`is_recovery_obligation = 1`) độc lập mà không gánh lỗi của `A` và không xóa lỗi của `A`.
   - **Dời lịch hẹn (`CRM Task` reschedule):** Dời hợp lệ trước hạn chuyển nghĩa vụ cũ sang `superseded` (giới hạn `max_reschedules_without_approval`); dời sau khi đã quá hạn vẫn giữ nguyên vi phạm cũ, không bao giờ xóa vết trễ hạn.
3. **Miễn trừ SLA có kiểm toán (`api/sla.py::waive_obligation`):**
   - Chỉ `Sales Manager` (trong phạm vi nhánh con của mình, **cấm tự miễn trừ nghĩa vụ cá nhân của chính mình** — `ob_doc.owner_user == actor -> 403 FORBIDDEN_SCOPE`) và `CSKH CRM Admin` được phép thực hiện qua `POST` kèm lý do bắt buộc, lưu vết `waived_by`, `waived_at`, `waive_reason` và `audit_events_json`.
4. **Quy kết `team-as-of` lịch sử (`services/attribution.py`):**
   - Mọi sự kiện gán/chuyển nhóm trong `CRM Sales Hierarchy` đều mở/đóng khoảng hiệu lực bất biến trong `CSKH Team Membership History`. Khi nhân viên chuyển từ `Team A` sang `Team B` giữa tháng, nghĩa vụ phát sinh lúc còn ở `Team A` giữ nguyên `team_at_obligation = Team A`, còn nghĩa vụ sau thời điểm chuyển thuộc `Team B`.
   - Với dữ liệu lịch sử trước thời điểm cắt P3 không có bằng chứng thành viên tại thời điểm sự kiện, hệ thống gắn nhãn trung thực `attribution_status = "historical_attribution_unknown"` và gom riêng vào nhóm minh bạch `__unattributed_historical_team__` thay vì suy đoán sai từ `CRM Lead.lead_owner` hiện tại.

---

## 5. Hệ thống Giao diện Báo cáo `/cskh/*`, Drill-down, Xuất dữ liệu & Phân quyền (`P3.md` §17.3.5)

Chi tiết thiết kế luồng màn hình và hợp đồng dữ liệu được ghi tại [`ui-contract-and-flows.md`](./ui-contract-and-flows.md) và [`authorization-matrix.md`](./authorization-matrix.md):

| Route `/cskh/*` | Đối tượng được phép (`HTTP 200`) | Đối tượng bị chặn (`HTTP 403`) | Chức năng cốt lõi |
|---|---|---|---|
| `/cskh/quality-overview` | `BOD`, `Leader` (phạm vi team), `CRM Admin` | `Staff`, `Outside`, `Guest` | Tổng quan chất lượng 3 trụ cột, thanh watermark `as_of`/`source_watermark`, cảnh báo `qa_enabled=false` & `DEV policy`, biểu đồ xu hướng SVG, tính lại KPI & lịch sử restatement |
| `/cskh/team-quality` | `BOD`, `Leader` (chỉ team mình), `CRM Admin` | `Staff`, `Outside`, `Guest` | So sánh các nhánh Team theo `team-as-of`, biểu đồ cột ngang SVG, tách biệt nghĩa vụ không rõ nhánh lịch sử |
| `/cskh/employee-quality` | `BOD` (`0` PII khách), `Leader` (chỉ nhân sự cây mình), `CRM Admin` | `Staff`, `Outside`, `Guest` | Đánh giá nhân sự tách bạch 3 bảng `Đã chốt` / `Tạm tính` / `Chưa đủ dữ liệu (N/A)`, bộ lọc trạng thái nhân sự & nhóm điểm |
| `/cskh/campaign-quality` | `BOD`, `Leader`, `CRM Admin` | `Staff`, `Outside`, `Guest` | Hiệu quả chiến dịch & nguồn khách; đối chiếu trực diện `DISTINCT Work Item` vs `Khoảng sở hữu` (`A -> B -> A`) và chỉ số tham khảo `FIRST_CONNECTED` |
| `/cskh/sla-operations` | `Leader` (team mình, có PII hợp pháp & miễn trừ SLA nhân viên cấp dưới), `CRM Admin`, `BOD` (chế độ tổng hợp `0` PII, `0` nút miễn trừ) | `Staff`, `Outside`, `Guest` | Giám sát nghĩa vụ quá hạn/vi phạm/đã miễn trừ, lọc theo loại & trạng thái nghĩa vụ, thực hiện miễn trừ SLA có lý do kiểm toán |
| `/cskh/scorecard` | `Staff` (chỉ cá nhân mình, `0` xếp hạng đồng nghiệp), `Leader` (cá nhân), `CRM Admin` | **`BOD` (`403`)**, `Outside`, `Guest` | Bảng điểm cá nhân, phân rã trọng số 5 thành phần `C1..C5`, danh sách nghĩa vụ SLA cần xử lý và drill-down cá nhân (tự ẩn PII các hồ sơ đã bàn giao) |

- **Đủ `9` thuộc tính giải thích trên mọi thẻ chỉ số (`renderMetricCard` trong [`p3-widgets.js`](../../apps/crm_cskh/crm_cskh/public/cskh/p3-widgets.js)):** đơn vị tính (`grain`), tử số / mẫu số, công thức tiếng Việt, kỳ báo cáo + cơ sở thời gian, phạm vi (`scope`), các khoản loại trừ hợp lệ (`exclusions`), dấu thời gian `as_of` / `calculated_at` / `source_watermark`, phiên bản `cskh-metrics-v2` + chính sách, và huy hiệu độ tin cậy dữ liệu (`dd` hiển thị ngang chuẩn, `0` lỗi chữ dọc).
- **Bộ lọc phân quyền thực & Drill-down phân trang/tìm kiếm/sắp xếp hoàn toàn ở phía máy chủ (`get_filter_options` + `get_metric_drilldown`, `F-P3-04`, `F-P3-10`):**
  - Danh mục bộ lọc Chiến dịch, Đội nhóm (`team_name` tường minh thay vì mã băm ID) và Nguồn khách (`lead_source`) tải động từ `get_filter_options` theo đúng phạm vi quyền actor và làm thay đổi kết quả báo cáo thực tế.
  - Hỗ trợ `21` chỉ số tại `DRILLDOWN_SUPPORTED_METRICS` của backend và mở trực tiếp trên giao diện cho toàn bộ các chỉ số trọng yếu; hỗ trợ lọc phân lớp (`all_denominator | numerator_only | breached_only | excluded`), từ khóa `search`, sắp xếp `sort_by`/`sort_dir` và phân trang `LIMIT/OFFSET` tại SQL **sau khi đã che PII** (chống dò PII qua bộ lọc `search` — `F-P3-04`).
  - Trên `/cskh/sla-operations`, vai trò `BOD` xem dữ liệu tổng hợp & danh sách nghĩa vụ đã ẩn PII (`0` PII khách hàng, `0` nút miễn trừ/cập nhật, kiểm chứng kèm đối chứng dương `>= 1` dòng dữ liệu thật — `B3E-17`).
- **Xuất báo cáo có ủy quyền ở phía máy chủ & Chặn kênh generic Frappe (`F-P3-05`):**
  - Không bao giờ tự ghép CSV/`Blob` trên trình duyệt; hiển thị rõ mã tiến trình, số dòng, thời điểm hết hạn (`expires_at`) và trạng thái quyền tải.
  - Sanitize chống CSV Formula Injection (`=`, `+`, `-`, `@`, `\t`, `\r` — `S18`).
  - Khóa quyền sống trên cả `download_export`, `/private/files/...`, `/api/resource/File` và chặn lộ chéo qua các RPC đọc chung của Frappe (`frappe.desk.reportview.*`, `frappe.client.get_list`, `frappe.desk.query_report.run` — `F-P3-05`).

---

## 6. Kiểm định Chất lượng (`QA`) & Nguồn Xác minh Trung thực (`P3.md` §17.3.6, `F-P3-03`)

Theo đúng chỉ đạo phạm vi P3 và đặc tả [`qa-checklist-and-sampling.md`](./qa-checklist-and-sampling.md):
1. **Chế độ mặc định `qa_enabled = false` minh bạch tuyệt đối & Bảo toàn xác minh hệ thống P2 (`F-P3-03`):**
   - Hệ thống **không tạo bất kỳ phiếu `CSKH QA Review` giả lập nào** và **không tự nâng khống các lượt `self_reported` thành `verified`**.
   - Đồng thời, các bản ghi `CSKH Contact Attempt` đã có chứng cứ hệ thống hợp lệ từ P2 (`verification_status = "verified"` gắn `CRM Call Log`, hoặc `"disputed"` khi đang hiệu chỉnh) được **bảo toàn trung thực** trên cả `attempt_breakdown` và chỉ số `VERIFICATION_RATE` (không bị xóa trắng về `0` khi tắt QA).
   - Trên giao diện báo cáo, băng cảnh báo `[data-banner="qa-disabled"]` giải thích rõ phân hệ chấm mẫu QA đang tắt theo cờ cấu hình; ô `QA_PASS_RATE` hiển thị `N/A — Chưa bật kiểm định QA` (`status = "disabled"`), và `PROCESS_SCORE` được gắn nhãn `Tạm tính; thiếu mẫu QA` (`valid_weight = 85`).
   - Mọi nỗ lực gọi API `create_sample` hoặc `submit_review` khi `qa_enabled = false` bị từ chối ở tầng server với HTTP `409 QA_DISABLED`.
2. **Sẵn sàng quy tắc chống tự duyệt & tách mẫu khi bật QA (`services/qa.py`):**
   - Khi chính sách bật `qa_enabled = 1`, hệ thống cưỡng chế tách biệt vai trò người chấm với người thực hiện cuộc gọi (`reviewer == performed_by -> 403 QA_CONFLICT`), tách mẫu ngẫu nhiên (`random_sample`) khỏi mẫu kiểm tra theo rủi ro (`risk_targeted` — không làm méo `PROCESS_SCORE`), và tự động đưa phạm vi vào hàng đợi tính lại khi phiếu QA được công bố.
3. **Tách bạch nguồn xác minh cho BOD:**
   - Báo cáo phân tách rõ số lượt `Tự báo cáo` (`self_reported`), `Đang tranh chấp / chờ duyệt hiệu chỉnh` (`disputed`), và `Đã xác minh` (`verified`), giúp BOD không bao giờ nhầm lẫn dữ liệu tự khai báo với dữ liệu có đối chứng hệ thống/QA.

---

## 7. Cơ chế Tính lại (`Recompute`), Lưu vết Phiên bản (`Restatement`), Cờ Rollback Hợp nhất & Hiệu năng (`P3.md` §17.3.7, `F-P3-06..08`)

1. **Vô hiệu hóa hậu giao dịch không chặn nhân viên (`schedule_post_commit_dirty_invalidation`, `F-P3-06`):**
   - Các thao tác tác nghiệp nóng (`record_attempt`, `review_correction`, `waive_obligation`, duyệt QA kỳ cũ, nâng cấp `metric_version`) ghi nhận `CSKH Dirty Scope` qua hook `frappe.db.after_commit` (**`0` câu SQL tính KPI trong giao dịch của nhân viên**, tự hủy sạch nếu giao dịch rollback).
2. **Bản chụp bất biến (`Append-Only`), Khóa tương tranh & Đính chính kỳ cũ (`Restatement`, `F-P3-06`, `F-P3-07`):**
   - Mỗi lần tính toán tạo mới một `CSKH KPI Run` (`cskh-metrics-v2`) kèm tập `CSKH KPI Snapshot` bất biến, chạy đối soát hậu kiểm (`reconcile_published_period`), rồi hoán đổi nguyên tử cờ `is_latest_published = 1` trong một giao dịch DB; khóa phạm vi chặn chạy chồng (`409 RECOMPUTE_IN_PROGRESS`) và bảo đảm tính nhất quán khi nhiều sự kiện xảy ra đồng thời (`F-P3-07`).
   - Khi nâng cấp `cskh-metrics-v1 -> cskh-metrics-v2` hoặc duyệt hiệu chỉnh/miễn trừ cho kỳ đã chốt, đợt chạy mới mang `run_mode = "restated"`, `is_restated = 1` và liên kết `restated_from_run` tới đợt chạy cũ để đối chiếu trên hộp thoại `Lịch sử phiên bản & đính chính`.
3. **Backfill có `dry_run` & Cờ Rollback Hợp nhất (`services/p3_flags.py`, `F-P3-08`):**
   - Công cụ `run_kpi_backfill` hỗ trợ `dry_run=True` (`0` thay đổi con trỏ công bố), chia lô theo ngày (`batch_days`) và lưu `checkpoint_json` ([`migration-and-backfill.md`](./migration-and-backfill.md)).
   - Toàn bộ hệ thống (UI `www/cskh_context.py`, API báo cáo/SLA/QA/Export, hook P1/P2 và RQ worker) dùng chung một bộ giải quyết cờ duy nhất [`services/p3_flags.py`](../../apps/crm_cskh/crm_cskh/services/p3_flags.py): tắt kill-switch cấp site hoặc `CSKH Policy` ẩn ngay thanh điều hướng P3, chặn route `/cskh/*` P3 (`403`), trả `503 P3_FEATURE_DISABLED` / `409 POLICY_CONFLICT` trên API P3 và dừng các job nền P3 mà bảo toàn `100%` luồng tác nghiệp P1/P2 (đã kiểm chứng tự động ở cả JUnit lẫn Browser E2E `B3E-34` — xem [`rollback.md`](./rollback.md)).
4. **Kết quả Đo kiểm Hiệu năng & Chỉ mục SQL:**
   - Toàn bộ truy vấn tổng hợp trong [`services/kpi_calculator.py`](../../apps/crm_cskh/crm_cskh/services/kpi_calculator.py) là truy vấn lô theo chỉ mục (`0` truy vấn `N+1` trên danh sách Work Item/Attempt), đọc dashboard trực tiếp từ `CSKH KPI Snapshot` đã công bố (giải quyết triệt để ghi nhận `OBS-P2-02` của P2).
   - Ở cả hai mốc dữ liệu giả lập `10k` (`25.000` Attempts, `20.000` SLA Obligations) và `100k` (`250.000` Attempts, `200.000` SLA Obligations), cả `5` API Dashboard đạt độ trễ đọc warm `p95 = 8,50 – 25,08 ms` với **`0` câu truy vấn quét `tabCSKH Contact Attempt`** (`obs_p2_02_verified_zero_attempt_scans_on_dashboard = true`); tác vụ tính lại tăng trưởng theo ngày (`Incremental Single-Day`) hoàn tất trong `420,40 ms` (`10k`, đỉnh heap `7,87 MiB`) và `5.622,11 ms` (`100k`, đỉnh heap `75,89 MiB`); `EXPLAIN` trên MariaDB `11.8.9` xác nhận `0` phép quét toàn bảng (`type = ALL`). Số liệu chi tiết và tuyên bố giới hạn môi trường DEV đơn máy chủ được trình bày tại [`benchmark-report.md`](./benchmark-report.md).

---

## 8. Hạn chế Hiện hữu, Rủi ro Kiểm soát & Hạng mục Chuyển giao Sang P4 (`P3.md` §17.3.8)

1. **Chính sách mặc định `DEV / configurable` chờ phê duyệt vận hành chính thức:** Các ngưỡng phút SLA và bộ trọng số `25/20/20/20/15` hiện là cấu hình mặc định trên DEV; trước khi go-live sản xuất ở P4, BOD và Trưởng bộ phận CSKH cần ký duyệt bảng tham số chính thức trên `CSKH Policy`.
2. **Phân hệ QA (`qa_enabled = false`) và Tối thiểu số lần gọi (`min_attempts_enabled = false`) đang tắt theo mặc định:** Đã có sẵn mô hình dữ liệu, guard phân quyền và công thức chuẩn hóa lại trọng số minh bạch (`provisional`), sẽ chỉ bật trong vận hành thật khi đội QA và quy chế lấy mẫu được phê duyệt.
3. **Dữ liệu tự khai báo (`self_reported`) chưa tích hợp tự động tổng đài/kênh tin nhắn:** Vì P3 chưa kết nối trực tiếp webhook SIP/VoIP hay Zalo OA/SMS Gateway hai chiều, các lượt liên hệ hiện do nhân viên ghi nhận và được gắn nhãn rõ `Nhân viên tự báo cáo`; tích hợp hạ tầng viễn thông/webhook và đối soát tự động thuộc lộ trình P4+.
4. **Quy kết lịch sử trước thời điểm cắt P3 (`historical_attribution_unknown`):** Các bản ghi phát sinh trước khi có bảng `CSKH Team Membership History` không được suy đoán ngược từ cây tổ chức hiện tại mà được tách riêng minh bạch vào nhóm `Chưa xác định nhánh lịch sử (Trước P3)`.
5. **Các ghi nhận kỹ thuật không chặn kế thừa sang P4 Hardening:**
   - **`OBS-P2-03`:** Hành vi mặc định `File.owner == user` của Frappe v15 đối với tệp gắn trên `CRM Lead` do chính user đó tải lên trước đây (riêng tệp xuất báo cáo P3 `CSKH Export Job` được tạo dưới quyền hệ thống và kiểm soát chặt trên cả `download_export` lẫn `/private/files/...`, trả `403` ngay khi thu hồi quyền `S12`).
   - **`O-01`:** Tối ưu chia chunk cho luồng import CSV dung lượng rất lớn của P1.
   - **Kiểm thử chịu tải HTTP/WSGI đa tiến trình cấp sản xuất:** Sẽ thực hiện trên cấu hình staging tương đương máy chủ thật trong giai đoạn P4 Production Readiness.

---

## 9. Tóm tắt Phán quyết Vòng 1 của `T1C` (`f92fa33`) & Ma trận Khắc phục `F-P3-01 .. F-P3-10`

Trên mốc Vòng 1 `f92fa33`, **T1C** đã thực hiện kiểm định độc lập và ghi nhận: **`5/20 PASS`** (`G01, G03, G06, G11, G19`), **`14/20 FAIL`** (`G02, G04, G05, G07, G08, G09, G10, G12, G13, G14, G15, G16, G17, G18`) và **`1/20 BLOCKED`** (`G20`), kèm `10` phát hiện kỹ thuật `F-P3-01 .. F-P3-10` và bộ vector chuẩn độc lập [`t1c_p3_golden_dataset_expected.json`](../../apps/crm_cskh/crm_cskh/tests/fixtures/t1c_p3_golden_dataset_expected.json) (`SHA-256: deb6be25a55a44deee4dcd21f9ee4fdada2ed38dfd2b14800c8cc1d375c49bdf` — bảo toàn bất biến `100%`).

| Mã Finding | Nội dung phát hiện Vòng 1 (`T1C`) | Bản khắc phục tích hợp (`T1B bafb648` + `T1A c62b1c2`) | Kiểm thử hồi quy chứng minh |
|---|---|---|---|
| **`F-P3-01`** | `CONNECTED_RATE` dùng sai mẫu số cuộc gọi, thiếu chỉ số bắt buộc (`ELIGIBLE_COVERAGE`, `CONVERSION_RATE`, `P50/P90`, v.v.) | Nâng cấp `cskh-metrics-v2` (`24` chỉ số lõi); `CONNECTED_RATE` tính theo `ownership_interval` (`connected_intervals / tried_intervals`, loại `is_invalid_phone`); UI hiển thị đủ nhãn/công thức `vi_VN`, `0` công thức tính ở frontend | `test_p3_t1c_remediation.py`, HTTP E2E `METRIC-V2-*`, Browser `B3E-04b/04c` |
| **`F-P3-02`** | Nghĩa vụ SLA trễ sang kỳ sau bị tính lệch cohort hoặc xóa vi phạm kỳ cũ | Khóa cứng `deadline_cohort` theo `effective_due_at` tại `as_of`; cuộc gọi trễ kỳ `M+1` giữ nguyên vi phạm của kỳ `M`; `OVERDUE_BACKLOG` tách rõ tồn mở hiện tại khỏi `SLA_BREACH_COUNT` lịch sử | `test_p3_t1c_remediation.py`, `t1c_p3_golden_dataset_expected.json` |
| **`F-P3-03`** | Khi `qa_enabled=false`, xóa nhầm `verified` hợp lệ từ `CRM Call Log` P2; thành phần `C2` lệch đặc tả | Bảo toàn `verification_status` hệ thống P2 (`verified`/`self_reported`/`disputed`) trong khi vẫn giữ `0` phiếu QA giả; chuẩn hóa `C2_ELIGIBLE_COVERAGE` (`20%`) khớp `P3.md` §7.5 | `test_p3_t1c_remediation.py`, HTTP E2E `QA-DISABLED-*`, Browser `B3E-02/03` |
| **`F-P3-04`** | Lộ KPI giai đoạn đội khác của nhân viên chuyển đội cho Leader; nguy cơ dò PII qua `search` drill-down | Tổng hợp theo lát cắt lịch sử `employee_team` (`user::team_node`) cho Leader; che PII (`REDACTED`) **trước** bước lọc `search` và `sort_by` trong `report_query.py` | `test_p3_t1c_remediation.py`, HTTP E2E `SEC-LEADER-*`, Browser `B3E-09/10` |
| **`F-P3-05`** | Kênh RPC đọc chung của Frappe (`reportview`, `frappe.client`, `/api/resource/File`) chưa khóa kín DocType P3 | Bổ sung `permission_query_conditions` & `has_permission` khóa chặt toàn bộ DocType báo cáo/xuất/SLA/QA P3 và tệp `CSKH Export Job` | `test_p3_t1c_remediation.py`, HTTP E2E `SEC-S11/S12/GENERIC-*`, Browser `B3E-28` |
| **`F-P3-06`** | Hiệu chỉnh/QA duyệt cho kỳ đã chốt hoặc nâng cấp công thức chưa kích hoạt restatement đồng bộ | Tự động ghi `CSKH Dirty Scope` (`prior_period_correction`, `metric_version_upgrade`) và sinh `CSKH KPI Run` mới (`run_mode="restated"`, `restated_from_run`); đồng bộ UI lịch sử phiên bản | `test_p3_t1c_remediation.py`, HTTP E2E `RESTATE-*`, Browser `B3E-24/25` |
| **`F-P3-07`** | Tương tranh cập nhật SLA / tính lại KPI khi nhiều sự kiện xảy ra sát nhau | Khóa hàng/phạm vi nhất quán trong `sla_engine.py` và `kpi_runs.py`, hoán đổi nguyên tử `is_latest_published` | `test_p3_t1c_remediation.py` |
| **`F-P3-08`** | Cờ thu hồi UI (`site_config`) và Backend (`CSKH Policy`) tách rời | Hợp nhất toàn bộ UI (`www/cskh_context.py`), API (`503 P3_FEATURE_DISABLED` / `409 POLICY_CONFLICT`), SLA hook và RQ worker vào `services/p3_flags.py` | `test_p3_t1c_remediation.py`, `test_ui_p3_pages_and_context.py`, Browser `B3E-34` |
| **`F-P3-09`** | Điểm đóng góp thành phần nhân nhầm `100x` (`2.250` thay vì `22,50`) trên bảng điểm | Chuẩn hóa thang `0–100`: tách cột `weighted_points` (`0..weight`, ví dụ `22,50 / 25`) và `reweighted_points` (`26,47`); khớp vector chuẩn `staff_b`: `73,50 / 85 -> 86,47` (`08b-staff-b-golden-scorecard-8647.png`) | `test_p3_ui_timezone_and_contracts.mjs`, HTTP E2E `SCORE-GOLDEN-8647`, Browser `B3E-19b` |
| **`F-P3-10`** | Trend mapping `N/A`, bộ lọc rỗng, hiện mã hash Team, thẻ đếm `(N/1)`, CSS chữ dọc `<dd>`, BOD SLA gọi nhầm API `403`, E2E thiếu đối chứng dương | Thêm `get_filter_options`, sửa mapping `trend_series`, hiển thị `team_name`, bỏ `/ 1` trên thẻ `count`, sửa grid `<dl>/<dd>`, giữ BOD SLA chỉ đọc `0` PII (`07b-bod-sla-operations-readonly.png`), bổ sung positive control trước mọi assertion âm | `verify_p3_http_e2e.py` (`196/196`), `verify_p3_browser_e2e.mjs` (`84/84`) |

---

## 10. Bảng Đối chiếu `20` Cổng Nghiệm thu `G01 .. G20` (`P3.md` §16 & §17.3.9)

> **Nguyên tắc trung thực & Phân tách thẩm quyền:** Bảng dưới đây dẫn chính xác phán quyết Vòng 1 của **T1C** trên `f92fa33` và cập nhật bằng chứng kỹ thuật tích hợp Vòng 2 (`c62b1c22`) của **T1B + T1A**. Toàn bộ các cổng chờ kiểm định lại đều giữ nguyên trạng thái **`PENDING T1C VERIFICATION`** (tuyệt đối không tự nâng `FAIL`/`BLOCKED` thành `PASS`); quyết định điều hành **`GO P4` / `NO-GO P4`** thuộc thẩm quyền của **T0 / Owner** sau khi có phán quyết Vòng 2 của **T1C**.

| Cổng (`P3.md` §16) | Điều kiện nghiệm thu | Bằng chứng kỹ thuật tích hợp (`T1B bafb648 + T1A c62b1c2`) | Phán quyết độc lập `T1C` |
|---|---|---|---|
| **G01** | Entry audit P0/P1/P2 đã được duyệt | [`entry-audit.md`](./entry-audit.md), [`reuse-matrix.md`](./reuse-matrix.md), khóa phiên bản `frappe v15.123.1` & `crm v1.87.0` trên baseline `449fbc2` (`GO P3` tại [`P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3) | **`PASS`** *(T1C Vòng 1 — `f92fa33`; giữ nguyên không đổi)* |
| **G02** | Tất cả KPI có dictionary, grain, denominator, exclusions và version | [`kpi-dictionary.md`](./kpi-dictionary.md) (`cskh-metrics-v2`, `24` chỉ số lõi, `CONNECTED_RATE` theo `ownership_interval`, `count` có `denominator=null`), [`scoring-and-data-sufficiency.md`](./scoring-and-data-sufficiency.md) (`F-P3-01`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G03** | SLA business-hour/holiday tính đúng | [`calendars-and-holidays.md`](./calendars-and-holidays.md), [`services/calendar.py`](../../apps/crm_cskh/crm_cskh/services/calendar.py), [`test_p3_calendar.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_calendar.py) (`9/9` PASS) | **`PASS`** *(T1C Vòng 1 — `f92fa33`; giữ nguyên)* |
| **G04** | First-touch đúng hạn/trễ đúng trên dữ liệu mẫu | [`services/sla_engine.py`](../../apps/crm_cskh/crm_cskh/services/sla_engine.py), khóa cứng `deadline_cohort` tại `as_of`, đối soát [`t1c_p3_golden_dataset_expected.json`](../../apps/crm_cskh/crm_cskh/tests/fixtures/t1c_p3_golden_dataset_expected.json) (`F-P3-02`, `F-P3-07`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G05** | Follow-up SLA xử lý reschedule/late/breach đúng | [`services/sla_engine.py`](../../apps/crm_cskh/crm_cskh/services/sla_engine.py), `test_p3_sla_and_attribution.py` + `test_p3_t1c_remediation.py` (`deadline_cohort`, tách `OVERDUE_BACKLOG` vs `SLA_BREACH_COUNT` — `F-P3-02`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G06** | Chuyển khách `A→B→A` không đánh lại lỗi và không reset deadline để “lách” | [`sla-policy-spec.md`](./sla-policy-spec.md), [`test_p3_sla_and_attribution.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_sla_and_attribution.py) (`F10..F12`: `original_due_at` bất biến, chặn trần `max_handoff_grace_resets`) | **`PASS`** *(T1C Vòng 1 — `f92fa33`; giữ nguyên)* |
| **G07** | Không double count `Call Log + Attempt`, `Task + Obligation` | [`metric-reconciliation.md`](./metric-reconciliation.md), bảo toàn `verification_status` P2 từ `CRM Call Log` mà `0` cộng trùng lượt gọi (`test_p3_t1c_remediation.py` — `F-P3-03`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G08** | `Team-as-of` và employee attribution lịch sử không méo khi transfer | [`ownership-attribution.md`](./ownership-attribution.md), lát cắt `employee_team` cô lập KPI từng giai đoạn cho Leader (`F-P3-04`), HTTP E2E `P3-TEAM-AS-OF` & Browser `B3E-08..10` | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G09** | Điểm 0–100 đúng công thức; `N/A` / `provisional` / `insufficient` chính xác kể cả chưa bật QA | [`scoring-and-data-sufficiency.md`](./scoring-and-data-sufficiency.md), `C2_ELIGIBLE_COVERAGE`, `weighted_points` (`22,50 / 25`) vs `reweighted_points`, vector chuẩn `73,50 / 85 = 86,47` (`F-P3-03`, `F-P3-09`), Browser `B3E-19b` | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G10** | QA: nếu chưa bật, không tạo review/verified giả và chấm điểm thiếu QA minh bạch; cấm tự duyệt khi bật | [`qa-checklist-and-sampling.md`](./qa-checklist-and-sampling.md), [`services/qa.py`](../../apps/crm_cskh/crm_cskh/services/qa.py) (`409 QA_DISABLED`, `0` fake QA review, bảo toàn `verified` hệ thống P2, tách `random_sample` vs `risk_targeted` — `F-P3-03`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G11** | Scheduler/retry và recompute có idempotency; có revision cũ/mới | [`services/kpi_runs.py`](../../apps/crm_cskh/crm_cskh/services/kpi_runs.py), append-only `CSKH KPI Run` (`cskh-metrics-v2`), `restated_from_run` (`F-P3-06`, `F-P3-07`), Browser `B3E-24`, `B3E-25` | **`PASS`** *(T1C Vòng 1 — `f92fa33`; củng cố thêm tại `c62b1c22`)* |
| **G12** | Dashboard BOD/Leader/Staff hiển thị đúng scope và giải thích KPI | `6` màn `/cskh/*`, `get_filter_options`, `trend_series`, `team_name`, BOD SLA chỉ đọc `0` PII (`07b`), thẻ `count` chuẩn & CSS `<dd>` ngang (`F-P3-10`), `15` ảnh chụp tại [`browser-manifest.json`](../../deploy/p3/evidence/browser-manifest.json) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G13** | Drill-down từ ít nhất 5 chỉ số trọng yếu tới source records đúng | [`report-api-contract.md`](./report-api-contract.md) §2.7 (`21` chỉ số `DRILLDOWN_SUPPORTED_METRICS`, `search`/`sort_by`/`sort_dir` phân trang server-side), HTTP E2E `DRILL-*` & Browser `B3E-06..07` (`F-P3-04`, `F-P3-10`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G14** | Không lộ chéo data qua report, generic API, export, file URL | [`authorization-matrix.md`](./authorization-matrix.md), khóa RPC chung Frappe (`F-P3-05`), che PII trước `search` (`F-P3-04`), khóa `download_export` + `/private/files/...`, HTTP E2E `196/196` & Browser `84/84` | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G15** | BOD phân biệt `self-reported`, `verified`, `disputed`; data stale được gắn nhãn | `attempt_breakdown` bảo toàn trung thực `self_reported` / `verified` / `disputed` (`F-P3-03`), băng cảnh báo `[data-banner="snapshot-stale"]` (`F-P3-06`), Browser `B3E-02`, `B3E-03`, `B3E-24` | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G16** | Report date/week/month/quarter/year không sai khi distinct/multi-interval | [`services/kpi_periods.py`](../../apps/crm_cskh/crm_cskh/services/kpi_periods.py), đối soát `t1c_p3_golden_dataset_expected.json` (`F-P3-01`, `F-P3-02`), Browser `B3E-14`, `B3E-29..33` (`3` múi giờ trình duyệt) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G17** | UI tiếng Việt tự nhiên, glossary chuẩn ở các luồng P3 | [`translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv) (`0` lỗi audit), `METRIC_LABELS_VI` (`24` chỉ số `cskh-metrics-v2`), sửa CSS `<dd>` & hiển thị `team_name` (`F-P3-10`) | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G18** | Migration/rollback được thử trên dữ liệu giả lập và CI sạch | [`services/p3_flags.py`](../../apps/crm_cskh/crm_cskh/services/p3_flags.py) (`F-P3-08`), [`rollback.md`](./rollback.md), `bench migrate` × 2, Browser `B3E-34`, GitHub Actions CI `P0 / P1 / P2 / P3` | `FAIL` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** (`c62b1c22`) |
| **G19** | Hiệu năng được đo và không có N+1/full scan nghiêm trọng | [`benchmark-report.md`](./benchmark-report.md), truy vấn lô có chỉ mục trong `services/kpi_calculator.py` + `CSKH KPI Snapshot` | **`PASS`** *(T1C Vòng 1 — `f92fa33`; giữ nguyên)* |
| **G20** | P3 Exit Report đủ điểm tồn tại, ADR, limitation và sign-off | Tài liệu này ([`docs/p3/P3-EXIT-REPORT.md`](./P3-EXIT-REPORT.md)) đã đồng bộ đầy đủ phán quyết Vòng 1, ma trận `F-P3-01..10`, manifest `c62b1c22` và bộ `ADR-P3-01..15` | `BLOCKED` Vòng 1 (`f92fa33`) → **`PENDING T1C VERIFICATION`** |

### Trạng thái Ký duyệt Hiện tại
- **T1B (Backend — `bafb6488`) & T1A (UI / E2E / Packaging — `c62b1c22`):** Đã hoàn tất khắc phục `F-P3-01 .. F-P3-10`, tích hợp và cập nhật bộ bằng chứng E2E + Exit Report cho Vòng 2.
- **T1C (Kiểm định Độc lập):** **`PENDING T1C VERIFICATION`** *(chờ replay kiểm định Vòng 2 trên môi trường cô lập của T1C)*.
- **Quyết định Điều hành (`T0 / Owner` — `GO P4` / `NO-GO P4`):** **CHƯA KÝ** *(chờ kết luận kiểm định độc lập Vòng 2 của T1C)*.
