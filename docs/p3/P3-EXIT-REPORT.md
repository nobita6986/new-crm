# P3 EXIT REPORT — CRM-CSKH Giai đoạn P3 (`SLA, KPI, QA & BOD Quality Dashboard`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** [`docs/p3/P3-EXIT-REPORT.md`](./P3-EXIT-REPORT.md)
> **Kế hoạch gốc:** [`docs/root-plan/P3.md`](../root-plan/P3.md) (§14, §15, §16, §17, §18) & ADR giao diện [`docs/architecture/adr-ui-001.md`](../architecture/adr-ui-001.md), [`docs/architecture/adr-p1-06-ui-integration.md`](../architecture/adr-p1-06-ui-integration.md), [`docs/p3/adrs/ADR-P3-01-to-15.md`](./adrs/ADR-P3-01-to-15.md)
> **Ngày đóng gói hồ sơ tích hợp P3:** 04/11/2026
> **Thực hiện bởi:**
> - **T1B (AI Coding — Backend, SLA/KPI/QA Engine, Benchmark & CI):** `P3-00..P3-06`, phần Backend/API của `P3-07/08` (`crm_cskh.api.p3_reporting.*`, `crm_cskh.api.sla.*`, `crm_cskh.api.qa.*`) và phần Backend/Benchmark/CI của `P3-09`
> - **T1A (AI Coding — Giao diện `/cskh/*`, Việt hóa `vi_VN`, HTTP/Browser E2E & Đóng gói Exit):** `P3-07` (`6` màn chất lượng & SLA tiếng Việt), phần UI của `P3-08` (drill-down phân trang server-side, xuất báo cáo có ủy quyền, lịch sử revision/restatement) và phần E2E/đóng gói của `P3-09`
> - **T1C (Kiểm định Độc lập Bảo mật, Chất lượng & Runtime):** **ĐANG CHỜ KIỂM ĐỊNH ĐỘC LẬP (`PENDING T1C VERIFICATION`)**
> **Baseline xuất phát (`main`):** `449fbc26374c3540b07ad86219fa0f33fc84feba` (`GO P3` đã được Owner phê duyệt tại [`docs/P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3)
> **Trạng thái cổng nghiệm thu `G01 .. G20`:** **`PENDING T1C VERIFICATION`** *(T1A/T1B cung cấp đầy đủ bằng chứng kỹ thuật tích hợp; tuyệt đối không tự chấm `PASS` thay T1C và không tự ký `GO P4`)*

---

## 1. Thông tin Build, Phiên bản Nguồn, Migration & Cờ Cấu hình (`P3.md` §17.3.1)

| Hạng mục | Giá trị thực tế trong kho mã |
|---|---|
| **Baseline `main` đầu vào P3** | `449fbc26374c3540b07ad86219fa0f33fc84feba` *(quyết định điều hành `GO P3` ngày 04/11/2026 tại [`docs/P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3; mốc kỹ thuật P2 `7e789eb1137fd8ee6c07a1971bf6b3151287b8e4` giữ nguyên như lịch sử)* |
| **Frappe Framework (pinned)** | `v15.123.1` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`) |
| **Frappe CRM (pinned)** | `v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) |
| **Chuỗi commit Backend `T1B` đã tích hợp** | PR #8 (`a50390ae263190a3d27ea71ea9750c0d748407a6` — contracts, KPI dictionary, ADRs) → PR #9 (`54f620838bd0617b036f47690567ff48e09097b7` — attribution, calendar, SLA engine) → PR #10 (`4e8606d724df774a317b6209cb68067ff22266bd` — KPI calculator, snapshots, backfill, QA) → PR #11 (`e96b60e9aa982cefc6c0e59b6efc5b89b477ebd9` → `6c3eccc4551fe97714e2d2177a390b29e83d89c7` — reporting/drilldown/export API, benchmark, CI `p3.yml`) |
| **Nhánh & Mốc tích hợp `T1A + T1B`** | Nhánh `codex/p3-ui` — **Source/E2E SHA:** `707bb2fec7efccb32e5040ec507f6ad3891d02d6` \| **`apps` tree SHA:** `bd4b30056f7d16d184713fe5046e79a2575c7853` *(commit đóng gói tiếp theo chỉ bổ sung `deploy/p3/evidence/*` và `docs/p3/P3-EXIT-REPORT.md`, giữ nguyên `HEAD:apps == bd4b30056f7d16d184713fe5046e79a2575c7853`; xem manifest & băm SHA-256 tại [`deploy/p3/evidence/http-e2e-report.json`](../../deploy/p3/evidence/http-e2e-report.json), [`deploy/p3/evidence/browser-manifest.json`](../../deploy/p3/evidence/browser-manifest.json), [`deploy/p3/evidence/SHA256SUMS.txt`](../../deploy/p3/evidence/SHA256SUMS.txt))* |
| **Tổng hợp bộ kiểm thử tự động đã chạy trên `707bb2f`** | **JUnit (`run_suites.py`):** `29/29` module, `413` test methods PASS (`P0=107`, `P1=209`, `P1-UI=12`, `P2=38`, `P2-UI=9`, `P3=31`, `P3-UI=7`, `failed_modules=[]`) · **HTTP E2E (`verify_p3_http_e2e.py`):** `186/186` assertion PASS · **Chromium Browser E2E (`verify_p3_browser_e2e.mjs`):** `61/61` assertion PASS qua `10` kịch bản & `3` múi giờ trình duyệt, `13` ảnh chụp toàn trang · **Audit dịch thuật (`check_translations_audit.py`):** `0` thiếu khai báo, `0` thiếu dịch, `0` tiếng Anh sót |
| **Schema & Patches P3** | Bổ sung các DocType & trường cấu hình P3 trên `CSKH Policy`, chỉ mục MariaDB (`ensure_p3_indexes`), dữ liệu hạt giống lịch làm việc chuẩn Việt Nam (`ensure_p3_seeds`) và baseline `migration_cutover` (`ensure_cutover_membership_baseline`); `bench migrate` × 2 chạy idempotent `0` lỗi (xem [`migration-and-backfill.md`](./migration-and-backfill.md)) |
| **Feature Flags & Mặc định DEV** | `site_config.cskh_p3_enabled = 1` (mặc định bật giao diện P3; đặt `0` để quay lui tức thời về P2 mà không mất dữ liệu); `CSKH Policy.p3_reporting_enabled = 1`; `CSKH Policy.p3_sla_enabled = 1`; **`CSKH Policy.qa_enabled = 0` (`false`)**; **`CSKH Policy.min_attempts_enabled = 0` (`false`)** |
| ** Tuyên bố Chính sách DEV (`DEV / configurable`)** | Mọi ngưỡng SLA (`first_attempt_sla_minutes`, `followup_sla_grace_minutes`, `handoff_min_grace_minutes`) và trọng số `PROCESS_SCORE` (`25 / 20 / 20 / 20 / 15`, `min_valid_score_weight = 60`) là **thông số mặc định kỹ thuật trên môi trường DEV có thể cấu hình qua `CSKH Policy`**, **không** coi là chính sách vận hành sản xuất đã được BOD phê duyệt chính thức |
| **Môi trường Docker cô lập phục vụ E2E UI** | [`deploy/p3/docker-compose.yml`](../../deploy/p3/docker-compose.yml) (`crm-cskh-p3-ui-*`, site `p3ui.localhost`, cổng `127.0.0.1:8320`, tách rời hoàn toàn khỏi `p1.localhost:8120`, `p2.localhost:8220` và stack `18320` của T1B) |

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

Toàn bộ `17` chỉ số thuộc phiên bản công thức **`cskh-metrics-v1`** được định nghĩa chặt chẽ tại [`kpi-dictionary.md`](./kpi-dictionary.md) và [`scoring-and-data-sufficiency.md`](./scoring-and-data-sufficiency.md), thực thi tập trung duy nhất tại [`services/kpi_calculator.py`](../../apps/crm_cskh/crm_cskh/services/kpi_calculator.py) (**`0` phép tính công thức KPI ở trình duyệt**):

1. **Làm tròn & Kiểu số chuẩn xác:** Sử dụng `decimal.Decimal` với quy tắc `ROUND_HALF_UP` đến `2` chữ số thập phân (`round_half_up_2`). Rollup từ `Nhân viên → Nhóm → Công ty` và từ `Ngày → Tuần/Tháng/Quý/Năm` luôn cộng tử số/mẫu số gốc và tính lại tập `DISTINCT` — **tuyệt đối không lấy trung bình cộng các tỷ lệ phần trăm** (được kiểm chứng đối chiếu với [`p3_golden_expectations.json`](../../apps/crm_cskh/crm_cskh/tests/fixtures/p3_golden_expectations.json): `1/1 (100%) + 5/10 (50%) -> 6/11 = 54,55%`, không phải `75%`).
2. **Phân biệt đơn vị tính (`Grain`) giữa Cấp Công ty/Chiến dịch và Cấp Nhân viên:**
   - Ở cấp Công ty và Chiến dịch: `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `distinct_work_items`, `WORK_ITEMS_TOUCHED` tính theo **`DISTINCT CSKH Work Item`**.
   - Ở cấp Nhân viên và Nhóm: `FIRST_ATTEMPT_SLA_RATE` và `ownership_intervals_count` tính theo **khoảng sở hữu (`CSKH Assignment History` interval)**. Khi một hồ sơ chuyển giao `A -> B -> A`, hệ thống ghi nhận chính xác `1` `DISTINCT Work Item` và `3` `ownership_intervals_count` (`0` đếm trùng khách hàng cho công ty, `0` xóa lịch sử trách nhiệm cá nhân).
3. **Quy tắc `Denominator == 0 -> N/A` (không bao giờ biến thành `0%`):**
   - Khi mẫu số bằng `0` (ví dụ nhân viên `staff_c` mới nhận việc hoặc đang nghỉ phép chưa có nghĩa vụ đến hạn), backend trả về `value: null, numerator: 0, denominator: 0, status: "insufficient_data", reason: "no_eligible_records"`.
   - Giao diện [`p3-widgets.js`](../../apps/crm_cskh/crm_cskh/public/cskh/p3-widgets.js) hiển thị rõ **`N/A`** kèm huy hiệu `Chưa đủ dữ liệu (N/A)`; chỉ khi `denominator > 0` và `numerator == 0` mới hiển thị `0,00%`.
4. **Điểm tuân thủ quy trình `PROCESS_SCORE` (0–100) & Không trộn bảng xếp hạng:**
   - Gồm 5 thành phần cấu hình (`DEV / configurable`): `C1_FIRST_ATTEMPT_SLA` (`25`), `C2_FOLLOWUP_SLA` (`20`), `C3_DATA_HYGIENE` (`20`), `C4_CALLBACK_DNC_DISCIPLINE` (`20`), `C5_QA_QUALITY` (`15`).
   - Khi `qa_enabled = false`, thành phần `C5_QA_QUALITY` mang trạng thái `"disabled"` (không tính là `0` điểm); tổng trọng số hợp lệ là `85 >= min_valid_score_weight (60)` nên điểm được chuẩn hóa trên mẫu số `85` với trạng thái **`"provisional"`** (`reason = "qa_component_excluded_reweighted"` — ví dụ chuẩn Golden `7350 / 85 = 86,47`).
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

- **Đủ `9` thuộc tính giải thích trên mọi thẻ chỉ số (`renderMetricCard` trong [`p3-widgets.js`](../../apps/crm_cskh/crm_cskh/public/cskh/p3-widgets.js)):** đơn vị tính (`grain`), tử số / mẫu số, công thức tiếng Việt, kỳ báo cáo + cơ sở thời gian, phạm vi (`scope`), các khoản loại trừ hợp lệ (`exclusions`), dấu thời gian `as_of` / `calculated_at` / `source_watermark`, phiên bản `cskh-metrics-v1` + chính sách, và huy hiệu độ tin cậy dữ liệu.
- **Drill-down phân trang/lọc/sắp xếp hoàn toàn ở phía máy chủ (`get_metric_drilldown`):**
  - Đã kết nối trên giao diện cho `>= 7` chỉ số trọng yếu (`FIRST_ATTEMPT_SLA_RATE`, `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `CONNECTED_RATE`, `VALID_OUTCOME_LOGGING_RATE`, `CALLBACK_COMPLETION_RATE`, `DNC_COMPLIANCE_RATE`, `CORRECTION_RATE`, `FIRST_CONNECTED_TRACKING_RATE`, `CONTACT_ATTEMPT_COUNT`) và hỗ trợ `12` chỉ số ở tầng backend.
  - Khớp đối soát tuyệt đối giữa thẻ tổng hợp và danh sách nguồn (`all_denominator == numerator_only + breached_only`), phân trang `LIMIT/OFFSET` tại SQL trước khi trả JSON, ẩn PII cho `BOD` và cho hồ sơ đã chuyển chủ (`pii_redacted_due_to_transfer`).
- **Xuất báo cáo có ủy quyền ở phía máy chủ (`request_export` → `get_export_status` → `download_export`):**
  - Không bao giờ tự ghép CSV/`Blob` trên trình duyệt; hiển thị rõ mã tiến trình, số dòng, thời điểm hết hạn (`expires_at`) và trạng thái quyền tải.
  - Sanitize chống CSV Formula Injection (`=`, `+`, `-`, `@`, `\t`, `\r` — `S18`).
  - Kiểm tra lại quyền tại thời điểm tải (`download_export`) **và** khóa quyền tệp riêng tư (`/private/files/...`): ngay sau khi thu hồi quyền Team của `Leader A`, cả đường dẫn API `download_export` lẫn đường dẫn tĩnh `/private/files/...` cũ đều lập tức trả HTTP `403` (`S12`).
- **Việt hóa `vi_VN` & Kiểm chứng lệch múi giờ trình duyệt:**
  - Kiểm toán [`check_translations_audit.py`](../../deploy/p1/check_translations_audit.py): **`0` thiếu khai báo, `0` thiếu bản dịch, `0` chuỗi tiếng Anh sót, `0` khai báo mồ côi**; cập nhật đầy đủ thuật ngữ P3 tại [`docs/glossary-vi.md`](../glossary-vi.md) (Mục 8–12) và danh mục UAT tại [`uat-bod-checklist.md`](./uat-bod-checklist.md), [`uat-staff-checklist.md`](./uat-staff-checklist.md).
  - Cả unit test Node ([`test_p3_ui_timezone_and_contracts.mjs`](../../deploy/p3/test_p3_ui_timezone_and_contracts.mjs)) lẫn trình duyệt Chromium thật đặt múi giờ `America/Los_Angeles` (`UTC-7/-8`) và `Asia/Tokyo` (`UTC+9`) đều neo chính xác biên kỳ `Hàng ngày / Tuần / Tháng / Quý / Năm / Tùy chỉnh` theo múi giờ site `Asia/Ho_Chi_Minh`.

---

## 6. Kiểm định Chất lượng (`QA`) & Nguồn Xác minh Trung thực (`P3.md` §17.3.6)

Theo đúng chỉ đạo phạm vi P3 và đặc tả [`qa-checklist-and-sampling.md`](./qa-checklist-and-sampling.md):
1. **Chế độ mặc định `qa_enabled = false` minh bạch tuyệt đối:**
   - Hệ thống **không tạo bất kỳ phiếu `CSKH QA Review` giả lập nào** và **không tự nâng `CSKH Contact Attempt.verification_status` lên `verified`** (`attempt_breakdown.verified == 0`).
   - Trên giao diện báo cáo, băng cảnh báo `[data-banner="qa-disabled"]` giải thích rõ phân hệ QA đang tắt theo cờ cấu hình; ô `QA_PASS_RATE` hiển thị `N/A — Chưa bật kiểm định QA` (không bao giờ biến thành `0%`), và `PROCESS_SCORE` được gắn nhãn `Tạm tính; thiếu mẫu QA` (`valid_weight = 85`).
   - Mọi nỗ lực gọi API `create_sample` hoặc `submit_review` khi `qa_enabled = false` bị từ chối ở tầng server với HTTP `409 QA_DISABLED`.
2. **Sẵn sàng quy tắc chống tự duyệt khi bật QA (`services/qa.py`):**
   - Khi chính sách bật `qa_enabled = 1` trong bộ kiểm thử tích hợp, hệ thống cưỡng chế tách biệt vai trò người chấm với người thực hiện cuộc gọi (`reviewer == performed_by -> 403 QA_CONFLICT` — `F24`/`S09`), lưu điểm theo thang chuẩn `rubric_version` và tự động đưa phạm vi vào hàng đợi tính lại khi phiếu QA được công bố.
3. **Tách bạch nguồn xác minh cho BOD:**
   - Báo cáo phân tách rõ số lượt `Tự báo cáo` (`self_reported`), `Đang tranh chấp / chờ duyệt hiệu chỉnh` (`disputed`), và `Đã xác minh QA` (`verified`), giúp BOD không bao giờ nhầm lẫn dữ liệu tự khai báo với dữ liệu đã kiểm định độc lập.

---

## 7. Cơ chế Tính lại (`Recompute`), Lưu vết Phiên bản (`Restatement`), Migration & Hiệu năng (`P3.md` §17.3.7)

1. **Vô hiệu hóa hậu giao dịch không chặn nhân viên (`schedule_post_commit_dirty_invalidation`):**
   - Các thao tác tác nghiệp nóng (`record_attempt`, `review_correction`, `waive_obligation`) chỉ đệm sự kiện trong bộ nhớ phiên và ghi vào `CSKH Dirty Scope` qua hook `frappe.db.after_commit` (**`0` câu SQL tính KPI trong giao dịch của nhân viên**, tự hủy sạch nếu giao dịch rollback).
2. **Bản chụp bất biến (`Append-Only`) & Đính chính kỳ cũ (`Restatement`):**
   - Mỗi lần tính toán tạo mới một `CSKH KPI Run` kèm tập bản ghi `CSKH KPI Snapshot`, chạy đối soát bất biến hậu kiểm (`reconcile_published_period`), rồi hoán đổi nguyên tử cờ `is_latest_published = 1` trong một giao dịch DB; khóa Redis theo phạm vi chặn chạy chồng (`409 RECOMPUTE_IN_PROGRESS`).
   - Khi có `CSKH Attempt Correction` hoặc miễn trừ SLA được duyệt cho dữ liệu đã công bố trước đó, đợt chạy mới mang `run_mode = "restated"`, `is_restated = 1` và liên kết `restated_from_run` tới đợt chạy cũ để đối chiếu trước/sau trên hộp thoại `Lịch sử phiên bản & đính chính`.
3. **Backfill có `dry_run` & Rollback an toàn:**
   - Công cụ `run_kpi_backfill` hỗ trợ `dry_run=True` (`0` thay đổi con trỏ công bố), chia lô theo ngày (`batch_days`) và lưu `checkpoint_json` để chạy tiếp an toàn khi gián đoạn ([`migration-and-backfill.md`](./migration-and-backfill.md)).
   - Quy trình quay lui ưu tiên tắt cờ `site_config.cskh_p3_enabled = 0`, `p3_reporting_enabled = 0` và `p3_sla_enabled = 0` (`code/config rollback` trước, bảo toàn 100% luồng P1/P2 mà không xóa bảng lịch sử — xem [`rollback.md`](./rollback.md)).
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

## 9. Bảng Đối chiếu `20` Cổng Nghiệm thu `G01 .. G20` (`P3.md` §16 & §17.3.9)

> **Nguyên tắc trung thực & Phân tách thẩm quyền:** Bảng dưới đây tổng hợp đầy đủ bằng chứng kỹ thuật do **T1B** và **T1A** thực hiện. Cột phán quyết độc lập được giữ nguyên trạng thái **`PENDING T1C VERIFICATION`** để **T1C** kiểm định độc lập trên môi trường cô lập của T1C; quyết định điều hành **`GO P4` / `NO-GO P4`** thuộc thẩm quyền của **T0 / Owner** sau khi có báo cáo đã ký của T1C.

| Cổng (`P3.md` §16) | Điều kiện nghiệm thu | Bằng chứng kỹ thuật tích hợp (`T1B + T1A`) | Phán quyết độc lập `T1C` |
|---|---|---|---|
| **G01** | Entry audit P0/P1/P2 đã được duyệt | [`entry-audit.md`](./entry-audit.md), [`reuse-matrix.md`](./reuse-matrix.md), khóa phiên bản `frappe v15.123.1` & `crm v1.87.0` trên baseline `449fbc2` (`GO P3` tại [`P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3) | **`PENDING T1C VERIFICATION`** |
| **G02** | Tất cả KPI có dictionary, grain, denominator, exclusions và version | [`kpi-dictionary.md`](./kpi-dictionary.md) (`17` chỉ số `cskh-metrics-v1`), [`scoring-and-data-sufficiency.md`](./scoring-and-data-sufficiency.md), [`adrs/ADR-P3-01-to-15.md`](./adrs/ADR-P3-01-to-15.md) | **`PENDING T1C VERIFICATION`** |
| **G03** | SLA business-hour/holiday tính đúng | [`calendars-and-holidays.md`](./calendars-and-holidays.md), [`services/calendar.py`](../../apps/crm_cskh/crm_cskh/services/calendar.py), [`test_p3_calendar.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_calendar.py) (`9/9` PASS — kiểm đủ ví dụ chuẩn §14.3, nghỉ trưa, cuối tuần, ngày lễ, ca qua đêm) | **`PENDING T1C VERIFICATION`** |
| **G04** | First-touch đúng hạn/trễ đúng trên dữ liệu mẫu | [`services/sla_engine.py`](../../apps/crm_cskh/crm_cskh/services/sla_engine.py), [`test_p3_sla_and_attribution.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_sla_and_attribution.py) (`F01..F04`, biên `<= effective_due_at`), [`p3_golden_expectations.json`](../../apps/crm_cskh/crm_cskh/tests/fixtures/p3_golden_expectations.json) | **`PENDING T1C VERIFICATION`** |
| **G05** | Follow-up SLA xử lý reschedule/late/breach đúng | [`services/sla_engine.py`](../../apps/crm_cskh/crm_cskh/services/sla_engine.py), [`test_p3_sla_and_attribution.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_sla_and_attribution.py) (`F14..F17`: `superseded` trước hạn, không xóa breach sau hạn, giới hạn số lần dời) | **`PENDING T1C VERIFICATION`** |
| **G06** | Chuyển khách `A→B→A` không đánh lại lỗi và không reset deadline để “lách” | [`sla-policy-spec.md`](./sla-policy-spec.md), [`test_p3_sla_and_attribution.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_sla_and_attribution.py) (`F10..F12`: `original_due_at` bất biến, `handoff_closed` / `breached_handoff`, chặn trần `max_handoff_grace_resets`) | **`PENDING T1C VERIFICATION`** |
| **G07** | Không double count `Call Log + Attempt`, `Task + Obligation` | [`metric-reconciliation.md`](./metric-reconciliation.md), [`test_p3_kpi_and_snapshots.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_kpi_and_snapshots.py) (`F05..F07`, `1` `CRM Call Log` rời + `1` gắn Attempt vẫn tính đúng `Attempt` hiệu lực, không cộng trùng) | **`PENDING T1C VERIFICATION`** |
| **G08** | `Team-as-of` và employee attribution lịch sử không méo khi transfer | [`ownership-attribution.md`](./ownership-attribution.md), `CSKH Team Membership History`, [`test_p3_sla_and_attribution.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_sla_and_attribution.py), HTTP E2E `P3-TEAM-AS-OF` & Browser E2E `B3E-08..10` | **`PENDING T1C VERIFICATION`** |
| **G09** | Điểm 0–100 đúng công thức; `N/A` / `provisional` / `insufficient` chính xác kể cả chưa bật QA | [`scoring-and-data-sufficiency.md`](./scoring-and-data-sufficiency.md), [`test_p3_kpi_and_snapshots.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_kpi_and_snapshots.py) (`86,47` `provisional` khi `qa_enabled=false`, `87,0` `final`, `0/0 -> null`), Browser `B3E-02`, `B3E-03`, `B3E-11..13`, `B3E-22` | **`PENDING T1C VERIFICATION`** |
| **G10** | QA: nếu chưa bật, không tạo review/verified giả và chấm điểm thiếu QA minh bạch; cấm tự duyệt khi bật | [`qa-checklist-and-sampling.md`](./qa-checklist-and-sampling.md), [`services/qa.py`](../../apps/crm_cskh/crm_cskh/services/qa.py), [`test_p3_kpi_and_snapshots.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_kpi_and_snapshots.py) (`409 QA_DISABLED`, `0` fake verified, `403 QA_CONFLICT` chống tự duyệt), Browser `B3E-02`, `B3E-03` | **`PENDING T1C VERIFICATION`** |
| **G11** | Scheduler/retry và recompute có idempotency; có revision cũ/mới | [`services/kpi_runs.py`](../../apps/crm_cskh/crm_cskh/services/kpi_runs.py), [`jobs/reconcile_metrics.py`](../../apps/crm_cskh/crm_cskh/jobs/reconcile_metrics.py), [`test_p3_kpi_and_snapshots.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_kpi_and_snapshots.py) (append-only `CSKH KPI Run`, `restated_from_run`, `409 RECOMPUTE_IN_PROGRESS`), Browser `B3E-24`, `B3E-25` | **`PENDING T1C VERIFICATION`** |
| **G12** | Dashboard BOD/Leader/Staff hiển thị đúng scope và giải thích KPI | `6` màn `/cskh/*`, [`ui-contract-and-flows.md`](./ui-contract-and-flows.md), [`test_ui_p3_pages_and_context.py`](../../apps/crm_cskh/crm_cskh/tests/test_ui_p3_pages_and_context.py), [`browser-manifest.json`](../../deploy/p3/evidence/browser-manifest.json) + ảnh chụp `01..13` (`B3E-01`, `B3E-04`) | **`PENDING T1C VERIFICATION`** |
| **G13** | Drill-down từ ít nhất 5 chỉ số trọng yếu tới source records đúng | [`report-api-contract.md`](./report-api-contract.md) §3.6, HTTP E2E `DRILL-*` (`7` chỉ số đối soát `all == met + unmet == snapshot.denominator`) & Browser E2E `B3E-06..07` (`6` chỉ số trọng yếu + phân trang server `page=1 -> page=2`) | **`PENDING T1C VERIFICATION`** |
| **G14** | Không lộ chéo data qua report, generic API, export, file URL | [`authorization-matrix.md`](./authorization-matrix.md), [`security-test-report.md`](./security-test-report.md), HTTP E2E `SEC-S01..S17` + `SEC-S11/S12` (`403` trên cả `download_export` và `/private/files/...` sau thu hồi quyền) + Browser `B3E-05`, `B3E-09..10`, `B3E-17..21`, `B3E-23`, `B3E-28` | **`PENDING T1C VERIFICATION`** |
| **G15** | BOD phân biệt `self-reported`, `verified`, `disputed`; data stale được gắn nhãn | `attempt_breakdown` (`self_reported` / `verified = 0` / `disputed`), thanh watermark & băng cảnh báo `[data-banner="snapshot-stale"]`, Browser `B3E-02`, `B3E-03`, `B3E-24` | **`PENDING T1C VERIFICATION`** |
| **G16** | Report date/week/month/quarter/year không sai khi distinct/multi-interval | [`services/kpi_periods.py`](../../apps/crm_cskh/crm_cskh/services/kpi_periods.py), [`test_p3_kpi_and_snapshots.py`](../../apps/crm_cskh/crm_cskh/tests/test_p3_kpi_and_snapshots.py), [`test_p3_ui_timezone_and_contracts.mjs`](../../deploy/p3/test_p3_ui_timezone_and_contracts.mjs), Browser `B3E-14`, `B3E-29..33` | **`PENDING T1C VERIFICATION`** |
| **G17** | UI tiếng Việt tự nhiên, glossary chuẩn ở các luồng P3 | [`translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv) (`0` lỗi audit), [`docs/glossary-vi.md`](../glossary-vi.md), [`uat-bod-checklist.md`](./uat-bod-checklist.md), [`uat-staff-checklist.md`](./uat-staff-checklist.md) | **`PENDING T1C VERIFICATION`** |
| **G18** | Migration/rollback được thử trên dữ liệu giả lập và CI sạch | [`migration-and-backfill.md`](./migration-and-backfill.md), [`rollback.md`](./rollback.md), `bench migrate` × 2 idempotent, GitHub Actions CI `P0 / P1 / P2 / P3` | **`PENDING T1C VERIFICATION`** |
| **G19** | Hiệu năng được đo và không có N+1/full scan nghiêm trọng | [`benchmark-report.md`](./benchmark-report.md), truy vấn lô có chỉ mục trong `services/kpi_calculator.py` + `CSKH KPI Snapshot` | **`PENDING T1C VERIFICATION`** |
| **G20** | P3 Exit Report đủ điểm tồn tại, ADR, limitation và sign-off | Tài liệu này ([`docs/p3/P3-EXIT-REPORT.md`](./P3-EXIT-REPORT.md)) + bộ `ADR-P3-01..15` ([`adrs/ADR-P3-01-to-15.md`](./adrs/ADR-P3-01-to-15.md)) | **`PENDING T1C VERIFICATION`** |

### Trạng thái Ký duyệt Hiện tại
- **T1B (Backend) & T1A (UI / E2E / Packaging):** Đã hoàn tất tích hợp kỹ thuật và bộ bằng chứng đầu vào cho kiểm định.
- **T1C (Kiểm định Độc lập):** **`PENDING T1C VERIFICATION`**.
- **Quyết định Điều hành (`T0 / Owner` — `GO P4` / `NO-GO P4`):** **CHƯA KÝ** *(chờ kết luận kiểm định độc lập của T1C)*.
