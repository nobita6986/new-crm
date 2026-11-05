# Khảo sát Giao diện P1/P2, Luồng Nghiệp vụ UI P3 & Hợp đồng Kết nối API (`P3-07`, `P3-08`, `P3-09`)

> **Dự án:** CRM-CSKH — Quản trị kho khách hàng, phân phối lead và chất lượng chăm sóc khách hàng
> **Tài liệu:** `docs/p3/ui-contract-and-flows.md`
> **Chủ sở hữu:** **T1A** (Giao diện `/cskh/*`, Việt hóa `vi_VN`, Drill-down/Export UI, HTTP/Browser E2E & Đóng gói `P3-09`)
> **Baseline xuất phát (`main`):** `449fbc26374c3540b07ad86219fa0f33fc84feba` (P2 đã đạt `GO P3` tại [`docs/P2-Exit-Report.md`](../P2-Exit-Report.md) §12.3)
> **Tham chiếu gốc:** [`docs/root-plan/P3.md`](../root-plan/P3.md) (§0, §4, §7, §8, §9, §10, §11, §12, §14, §15.3, §16, §17), [`docs/architecture/adr-ui-001.md`](../architecture/adr-ui-001.md), [`docs/architecture/adr-p1-06-ui-integration.md`](../architecture/adr-p1-06-ui-integration.md)

---

## 1. Khảo sát Thành phần & Điều hướng Hiện có từ P0–P2 (`ADR-P3-09`)

### 1.1. Kiến trúc giao diện cùng Frappe site (`/cskh/*`) được giữ nguyên
Qua khảo sát thực tế cây mã nguồn `apps/crm_cskh/crm_cskh` trên baseline `449fbc26`:
- **Không dựng SPA thứ hai, không thêm backend/database thứ hai, không sửa mã nguồn upstream `apps/frappe` hay `apps/crm`:** Frappe CRM `v1.87.0` chỉ gọi cứng `get_<chart>` trong `crm.api.dashboard` và chưa có hook `crm_dashboard_charts` ổn định. Do đó P3 tiếp tục dùng chiến lược trang web Frappe cùng site `/cskh/*` (`ADR-UI-001`, `ADR-P1-06`, `ADR-P3-09`), chia sẻ cùng phiên đăng nhập cookie `sid` và token `X-Frappe-CSRF-Token`.
- **Các thành phần nền tảng tái sử dụng trực tiếp từ P1/P2:**
  1. [`www/cskh_context.py`](../../apps/crm_cskh/crm_cskh/www/cskh_context.py): Cổng kiểm tra vai trò phía máy chủ trước khi render HTML (`Guest` và vai trò ngoài danh mục nhận HTTP `403 PermissionError`), xuất `window.cskhCapabilities`, `window.cskhTimezone` (`site`, `user`, `effective`, `server_now_iso`), `window.cskhI18n` và `window.cskhErrorTexts`.
  2. [`templates/cskh/_shell.html`](../../apps/crm_cskh/crm_cskh/templates/cskh/_shell.html): Khung trang thống nhất hiển thị thanh điều hướng theo quyền, phiên người dùng, múi giờ hiệu lực, lối quay về CRM Leads và vùng thông báo nổi (`#cskh-toast-host`).
  3. [`public/cskh/cskh.js`](../../apps/crm_cskh/crm_cskh/public/cskh/cskh.js) & [`public/cskh/cskh.css`](../../apps/crm_cskh/crm_cskh/public/cskh/cskh.css): Bộ hàm dựng DOM an toàn chống XSS (`cskh.el` dùng `textContent`/`createTextNode`, tuyệt đối không dùng `innerHTML` cho dữ liệu), bộ chuyển đổi múi giờ chuẩn `Intl.DateTimeFormat(..., { timeZone })`, `cskh.get`/`cskh.post` kèm CSRF, `cskh.pager` phân trang từ `total` của server, `cskh.poll` và `cskh.renderError`.
  4. [`p1_strings.py`](../../apps/crm_cskh/crm_cskh/p1_strings.py), [`p2_strings.py`](../../apps/crm_cskh/crm_cskh/p2_strings.py), [`translations/vi.csv`](../../apps/crm_cskh/crm_cskh/translations/vi.csv) & cổng kiểm toán tĩnh fail-closed [`deploy/p1/check_translations_audit.py`](../../deploy/p1/check_translations_audit.py).

### 1.2. Phân tách ranh giới màn hình P2 vs P3
- Màn `/cskh/dashboard` (P1) giữ nguyên vai trò **Bảng điều hành phân phối Lead**.
- Màn `/cskh/progress` (P2) giữ nguyên vai trò **Tiến độ hoạt động cơ bản trong ca** (bộ đếm vận hành P2).
- Hệ thống **6 màn hình Chất lượng, SLA & Bảng điểm P3** được bổ sung thành các route độc lập có kiểm quyền server-side rõ ràng, kèm thanh điều hướng phụ (`templates/cskh/_p3_subnav.html`) và cờ tính năng thu hồi an toàn `cskh_p3_enabled` (`P3.md` §15.3).

---

## 2. Ma trận Route, Điều hướng & Cổng Quyền Cấp Trang P3

| Route `/cskh/*` | Màn hình (`P3.md` §10) | `Sales User` (Staff) | `Sales Manager` (Leader) | `CSKH CRM Admin` | `CSKH BOD Viewer` | Quy tắc Bảo vệ Cấp Trang & Dữ liệu |
|---|---|:---:|:---:|:---:|:---:|---|
| `/cskh/quality-overview` | **1. Tổng quan chất lượng** (§10.1) | Chặn (`403`) | Cho phép *(chỉ scope đội mình)* | Cho phép | Cho phép *(toàn công ty, 0 PII)* | Theo `report-api-contract.md` §2: Staff bị từ chối `403` (`S02`), Leader xem tổng quan đội mình, BOD xem toàn công ty `0` PII. |
| `/cskh/team-quality` | **2. So sánh đội nhóm** (§10.2) | Chặn (`403`) | Cho phép *(chỉ cây đội mình)* | Cho phép | Cho phép | Team-as-of theo kỳ; Leader không xem được đội khác (`S03`). |
| `/cskh/employee-quality` | **3. Chất lượng nhân viên** (§10.3) | Chặn (`403` — dùng `/cskh/scorecard`) | Cho phép *(nhân viên thuộc scope)* | Cho phép | Cho phép | Tách nhóm `Đã chốt` vs `Tạm tính` vs `N/A`; Staff không xem bảng đồng nghiệp (`S01`). |
| `/cskh/campaign-quality` | **4. Chiến dịch & Nguồn khách** (§10.4) | Chặn (`403`) | Cho phép *(campaign thuộc scope)* | Cho phép | Cho phép | Tách chất lượng nguồn khách khỏi điểm tuân thủ nhân viên; gắn nhãn `policy_version`. |
| `/cskh/sla-operations` | **5. Vận hành SLA & Kiểm định QA** (§10.5) | Chặn (`403`) | Cho phép *(scope đội)* | Cho phép | Cho phép *(0 PII thô, chỉ đọc / theo quyền)* | Tra cứu nghĩa vụ quá hạn/vi phạm/miễn trừ/cảnh báo; thao tác miễn trừ/tính lại kiểm quyền + lý do kiểm toán; QA hiển thị chế độ `qa_enabled=false` minh bạch. |
| `/cskh/scorecard` | **6. Bảng điểm cá nhân** (§10.6) | Cho phép *(chỉ chính mình)* | Cho phép *(cá nhân)* | Cho phép | Chặn (`403`) | Scope khóa cứng từ phiên đăng nhập; không lộ PII khách đã chuyển giao cho người khác (`S04`). |

- **Cờ thu hồi hợp nhất (`Unified Rollback Feature Flag` — `services/p3_flags.py`, `F-P3-08`):** `www/cskh_context.py` dùng chung bộ giải quyết cờ tính năng P3 của Backend (`crm_cskh.services.p3_flags.resolve_p3_flags()` đọc `site_config.cskh_p3_reporting_enabled` / `cskh_p3_sla_enabled` / `cskh_p3_enabled` và `CSKH Policy.p3_reporting_enabled` / `p3_sla_enabled`). Khi tắt cờ P3, toàn bộ mục điều hướng P3 tự ẩn, các route `/cskh/*` P3 trả HTTP `403`, và các API báo cáo P3 trả `HTTP 503 P3_FEATURE_DISABLED` / `409 POLICY_CONFLICT` đồng bộ với [`docs/p3/rollback.md`](./rollback.md), bảo toàn nguyên vẹn toàn bộ luồng P1/P2.
- **Chế độ BOD trên `/cskh/sla-operations` (`F-P3-10`):** Giữ nguyên quyền xem tổng hợp/nghĩa vụ đã ẩn PII (`[data-banner="bod-sla-readonly"]`, đi qua `get_executive_overview` + `get_metric_drilldown` `0` PII), hỗ trợ lọc loại/trạng thái nghĩa vụ, hiển thị trạng thái dữ liệu/empty/error rõ ràng và tuyệt đối `0` nút thao tác miễn trừ/cập nhật.

---

## 3. Quy ước Hiển thị Bắt buộc Trên Mọi Chỉ số P3 (`P3.md` §4, §7, §8, §10)

**Nguyên tắc bất biến của Frontend P3:** Trình duyệt **tuyệt đối không tự định nghĩa hay tự tính công thức KPI/SLA**, không tải toàn bộ bản ghi về lọc cục bộ, và không tạo CSV/XLSX cục bộ từ mảng dữ liệu trên trình duyệt. Mọi con số, tử số, mẫu số, trạng thái độ tin cậy và danh sách drill-down đều đọc từ payload có thẩm quyền của Backend (`T1B`).

### 3.1. Chín thuộc tính hiển thị bắt buộc trên mỗi thẻ/ô chỉ số (`p3-widgets.js`)
1. **Đơn vị thống kê (`grain` / `unit`):** Ghi nhãn tường minh `Đầu việc duy nhất (Work Item)` vs `Khoảng phụ trách (Ownership Interval)` vs `Nghĩa vụ SLA (SLA Obligation)` vs `Lần liên hệ hiệu lực (Effective Attempt)` vs `Phiếu kiểm định QA` vs `Điểm quy trình (0–100)`. Hiển thị chú thích rõ: *Tổng khoảng phụ trách của các nhân viên không bằng số khách hàng/đầu việc duy nhất toàn công ty khi có bàn giao*.
2. **Giá trị & Tử số / Mẫu số (`numerator / denominator`):**
   - Mọi tỷ lệ luôn hiển thị kèm phân số gốc ngay cạnh phần trăm, ví dụ **`80,0%` (`80 / 100`)**.
   - **Quy tắc `Denominator = 0` (`F21`):** Khi `denominator === 0` hoặc `value === null` hoặc `status === "insufficient_data"`, giao diện bắt buộc hiển thị **`N/A` (`0 / 0 — Chưa có mẫu đủ điều kiện`)**, **tuyệt đối không hiển thị `0%`, `0,0%` hay `100%`**.
3. **Công thức tiếng Việt (`formula`):** Tra cứu được ngay qua nút `Công thức & Nguồn` trên từng thẻ/cột bảng.
4. **Kỳ báo cáo & Cơ sở thời gian (`period` & `time_basis`):** Khoảng nửa mở `[from, to_exclusive)` theo `Asia/Ho_Chi_Minh` kèm nhãn cơ sở (`Theo hạn nghĩa vụ — Deadline cohort`, `Theo thời điểm sự kiện — Event time`, hoặc `Ảnh chụp tồn kho tại mốc chốt — Point-in-time as_of`).
5. **Phạm vi (`scope`):** `Toàn công ty` / `Đội nhóm (team-as-of)` / `Nhân viên` / `Chiến dịch`.
6. **Loại trừ & Miễn trừ (`exclusions` / `excluded_count` / `waiver_count` / `historical_attribution_unknown`):** Hiển thị rõ số lượng loại trừ hợp lệ và cảnh báo chất lượng dữ liệu nếu có bản ghi thiếu chứng cứ lịch sử (`historical_attribution_unknown`).
7. **Mốc chốt & Độ tươi (`as_of`, `calculated_at`, `source_watermark`):** Luôn hiển thị trên thanh trạng thái báo cáo và hộp chi tiết chỉ số (*“Dữ liệu chốt đến ... · Tính lúc ...”*).
8. **Phiên bản công thức & chính sách (`metric_version`, `policy_version`):** Hiển thị kèm thông báo cấu hình: *“Ngưỡng SLA & trọng số đang hiển thị là cấu hình DEV/có thể điều chỉnh — không mặc định là chính sách Production đã được BOD phê duyệt”*.
9. **Trạng thái độ tin cậy (`status` / `freshness`):** Huy hiệu phân biệt rõ `fresh` (`Mới cập nhật`), `calculating` (`Đang tính toán`), `stale` (`Cần tính lại`), `provisional` (`Tạm tính`), `finalized` (`Đã chốt`), `restated` (`Đã tính lại kỳ cũ`), `insufficient_data` (`Chưa đủ dữ liệu — N/A`), `failed` (`Lỗi đối soát`).

### 3.2. Tách bạch Ba Trụ cột, Chế độ `qa_enabled = false` & Bảng Xếp hạng Không Trộn
1. **Ba trụ cột hiển thị tách rời trên mọi màn hình (`cskh-metrics-v2`):**
   - **Nhóm A — Tuân thủ quy trình & SLA:** `PROCESS_SCORE`, `ASSIGNED_INTERVALS`, `UNIQUE_ASSIGNED_ITEMS`, `ELIGIBLE_COVERAGE`, `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `MIN_ATTEMPTS_COMPLIANCE_RATE`, `CALLBACK_COMPLETION_RATE`, `RESCHEDULE_RATE`, `DNC_COMPLIANCE_RATE`, `OVERDUE_BACKLOG`, `OPEN_FOLLOWUPS_OVERDUE`, `SLA_BREACH_COUNT`, `SLA_WAIVER_COUNT`, `FIRST_ATTEMPT_LATENCY_P50_MINUTES`, `FIRST_ATTEMPT_LATENCY_P90_MINUTES`.
   - **Nhóm B — Hiệu quả kết nối & chuyển đổi:** `WORK_ITEMS_TOUCHED`, `CONNECTED_ITEMS`, `CONTACT_ATTEMPT_COUNT` (chỉ số hoạt động tham khảo kèm phân rã `attempted / connected / self_reported / verified / disputed`, `denominator = null`), `CONNECTED_ATTEMPT_COUNT`, `CONNECTED_RATE` (đơn vị `ownership_interval`: `connected_intervals / tried_intervals`), `CONVERSION_RATE`, `FIRST_CONNECTED_TRACKING_RATE` (theo dõi riêng, `informational_only: true`, **không phạt vi phạm SLA** khi khách không nghe máy).
   - **Nhóm C — Độ tin cậy dữ liệu & Kiểm định QA:** `VALID_OUTCOME_LOGGING_RATE`, `VERIFICATION_RATE`, `CORRECTION_RATE`, `VOID_ATTEMPT_RATE`, và `QA_PASS_RATE` (tách riêng `verified`, `self_reported`, `disputed`).
2. **Chế độ `qa_enabled = false` & Điểm đóng góp thành phần (`F22`, `F-P3-09`, `G09`, `G10`):**
   - Hiển thị hộp thông báo minh bạch khi backend trả `qa_enabled: false`: không sinh phiếu QA hay trạng thái `Đã xác minh` giả; `QA_PASS_RATE` hiển thị `N/A — Chưa bật kiểm định QA` (`status: "disabled"`, tách biệt với `N/A — Chính sách đang tắt` của `MIN_ATTEMPTS`); `PROCESS_SCORE` hiển thị rõ **`86,47 — Tạm tính; thiếu mẫu QA`** (tổng điểm trọng số thô `73,50` trên tổng trọng số hợp lệ `85/100`).
   - Trên bảng thành phần `/cskh/scorecard`, tách riêng cột **`Điểm trọng số thô`** (`0..weight`, ví dụ `90% × 25 = 22,50 / 25`) và **`Điểm đóng góp sau chuẩn hóa (0–100)`** (`26,47`).
3. **Không xếp chung `provisional` với `finalized` (`P3.md` §0.12, §7.5, §10.3):**
   - Trên màn `/cskh/employee-quality` và `/cskh/team-quality`, các dòng được phân nhóm rõ ràng thành:
     1. **Nhóm Đã chốt (`finalized`)** — chỉ sắp xếp thứ tự trong nội bộ nhóm Đã chốt;
     2. **Nhóm Tạm tính (`provisional`)** — hiển thị tách nhóm riêng với nhãn `Tạm tính`, không gán hạng chung với nhóm Đã chốt;
     3. **Nhóm Chưa đủ dữ liệu (`insufficient_data` / `N/A`)** — không hiển thị điểm số và không xếp hạng.

---

## 4. Hợp đồng Luồng Drill-Down (`>= 5` Chỉ số Trọng yếu) & Xuất Báo cáo (`P3-08`)

### 4.1. Luồng Drill-Down Phân trang Server-Side (`p3-drilldown` trong `p3-widgets.js`)
- Hỗ trợ mở danh sách bản ghi gốc có thẩm quyền trên đầy đủ các chỉ số cốt lõi của `report-api-contract.md` §3.6 (`DRILLDOWN_SUPPORTED_METRICS`):
  1. `FIRST_ATTEMPT_SLA_RATE` & `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` (Cohort `FIRST_ATTEMPT` obligations đến hạn: `all_denominator` = `numerator_only` + `breached_only`; ví dụ `80/100` mở đúng `100` nghĩa vụ gồm `80` đạt và `20` không đạt, kèm phân lớp `excluded`).
  2. `FOLLOWUP_SLA_RATE` (Cohort `FOLLOWUP` callback obligations đến hạn: `all_denominator`, `numerator_only`, `breached_only`, `excluded`).
  3. `FIRST_CONNECTED_TRACKING_RATE` (Theo dõi kết nối lần đầu, `informational_only: true`).
  4. `CONNECTED_RATE` (`Ownership Intervals`: khoảng phụ trách có kết nối hai chiều vs đã thử nhưng chưa kết nối) & `CONTACT_ATTEMPT_COUNT` (`Effective Attempts`).
  5. `VALID_OUTCOME_LOGGING_RATE`, `CORRECTION_RATE` & `VOID_ATTEMPT_RATE` (`Raw/Effective Attempts`: đạt chuẩn cập nhật vs đã hiệu chỉnh / vô hiệu).
  6. `CALLBACK_COMPLETION_RATE` (`Due FOLLOWUP obligations` đã hoàn tất bằng lần liên hệ hợp lệ).
  7. `DNC_COMPLIANCE_RATE` (`Ownership Intervals` tuân thủ quy tắc DNC và đóng hợp lệ).
- **Ràng buộc kỹ thuật bắt buộc:**
  - Mọi thao tác chuyển trang (`page`, `page_size`), chọn phân lớp cohort (`segment`: `all | met | unmet | excluded`), hoặc đổi sắp xếp (`sort_by`, `sort_dir`) đều gửi tham số lên API drill-down của server để lọc/sắp xếp **trước `LIMIT/OFFSET`**.
  - Hiển thị cột theo đúng dữ liệu đã được backend kiểm quyền/che mặt nạ (BOD `0` PII thô; Staff sau bàn giao `0` PII khách cũ).

### 4.2. Luồng Xuất Dữ liệu Qua Job/API Được Phân quyền (`p3-export` trong `p3-widgets.js`)
1. Người dùng bấm **Xuất báo cáo**, chọn định dạng được backend cho phép (`CSV` / `XLSX`) và nhập mục đích kiểm toán (nếu yêu cầu) → UI gửi `1` POST `request_export` kèm CSRF + `idempotency_key`.
2. Hộp trạng thái xuất hiển thị mã tác vụ (`job_id` / `export_id`), trạng thái (`queued | running | completed | expired | forbidden | failed`), số dòng (`row_count`), danh sách cột cho phép theo vai trò, và thời điểm hết hạn `expires_at` (`Asia/Ho_Chi_Minh`).
3. Nút **Tải tệp** gọi endpoint tải có thẩm quyền của backend (tái kiểm tra phiên, vai trò, phạm vi đội/nhân viên và hạn `expires_at` ngay tại thời điểm tải). Nếu quyền đã bị thu hồi hoặc tệp hết hạn, UI hiển thị rõ thông báo lỗi tiếng Việt tương ứng (`[FORBIDDEN]` / `[FORBIDDEN_SCOPE]` / `[EXPORT_EXPIRED]`).

---

## 5. Ma trận Nhu cầu API Backend (`p3-api.js` — Đã Khớp 100% Với Contract Đã Commit `bafb6488` của T1B)

Bộ điều hợp [`public/cskh/p3-api.js`](../../apps/crm_cskh/crm_cskh/public/cskh/p3-api.js) chỉ đóng vai trò kết nối giao thức HTTP/CSRF và chuẩn hóa cấu trúc hiển thị (tuyệt đối không tính toán lại tử số/mẫu số/điểm số), đã khớp 1-1 với [`docs/p3/report-api-contract.md`](./report-api-contract.md) và [`docs/p3/kpi-dictionary.md`](./kpi-dictionary.md) (`cskh-metrics-v2`, SHA `bafb6488990085ca034ee5561abcfdb6fb1ed95c`):

| Nhóm chức năng UI | Endpoint Backend (`report-api-contract.md` §2) | Phương thức | Tham số chính UI truyền lên | Dữ liệu UI nhận từ Server |
|---|---|---|---|---|
| **Danh mục bộ lọc phân quyền** | `crm_cskh.api.p3_reporting.get_filter_options` | `GET` | `period_type`, `period_key` / `from_date`+`to_date` | `campaigns[]`, `teams[]` (`team_id`, `team_name`), `employees[]`, `lead_sources[]` theo đúng phạm vi actor |
| **Tổng quan BOD / Leader** | `crm_cskh.api.p3_reporting.get_executive_overview` | `GET` | `period_type` (`daily\|weekly\|monthly\|quarterly\|yearly\|custom`), `period_key` / `from_date`+`to_date`, `campaign`, `team`, `lead_source` | `actor_scope`, `period`, `freshness` (`run_id`, `snapshot_status`, `as_of`, `source_watermark`, `is_restated`, `metric_version`), `policy_notice`, `exclusion_counts`, `metrics` (`24` chỉ số `cskh-metrics-v2`), `teams_summary`, `alerts`, `trend_series` |
| **So sánh đội nhóm** | `crm_cskh.api.p3_reporting.get_team_comparison` | `GET` | `period_type`, `period_key`/`from_date`+`to_date`, `campaign`, `lead_source`, `sort_by`, `sort_dir`, `page`, `page_size` | `rows[]` (`team_id`, `team_name`, `is_unattributed_bucket`, `active_staff_count`, `overdue_work_item_backlog`, `metrics.*`), `pagination` |
| **Chất lượng nhân viên** | `crm_cskh.api.p3_reporting.get_employee_quality` | `GET` | `period_type`, `period_key`/`from_date`+`to_date`, `team`, `employee`, `campaign`, `lead_source`, `sort_by`, `sort_dir`, `page`, `page_size` | `rows[]` (`employee_id`, `employee_name`, `team_id`, `team_name`, `intervals_count`, `work_items_touched`, `attempt_breakdown`, `metrics.*` với `PROCESS_SCORE.status` tách rõ `final`/`provisional`/`insufficient_data` và lát cắt `employee_team` cho Leader) |
| **Chiến dịch & nguồn** | `crm_cskh.api.p3_reporting.get_campaign_quality` | `GET` | `period_type`, `period_key`/`from_date`+`to_date`, `campaign`, `team`, `lead_source`, `sort_by`, `sort_dir`, `page`, `page_size` | `rows[]` (`campaign_id`, `campaign_name`, `lead_source`, `distinct_work_items` đối chiếu `ownership_intervals_count`, `metrics.*`) |
| **Bảng điểm cá nhân** | `crm_cskh.api.p3_reporting.get_my_scorecard` | `GET` | `period_type`, `period_key`/`from_date`+`to_date`, `campaign`, `lead_source` *(server khóa `employee = session.user`)* | `metrics`, `score_components` (thang `0–100`: `weighted_points` `0..weight`, `reweighted_points` `0..100`, `valid_weight`), `active_obligations` |
| **Drill-down chỉ số** | `crm_cskh.api.p3_reporting.get_metric_drilldown` | `GET` | `metric_code` (`21` mã `DRILLDOWN_SUPPORTED_METRICS`), `scope_type`, `scope_id`, `filter_bucket` (`all_denominator\|numerator_only\|breached_only\|excluded`), `campaign`, `lead_source`, `search`, `sort_by`, `sort_dir`, `page`, `page_size` | Dòng sự kiện gốc đã che PII trước `search`/`sort_by` và phân trang server-side (`lead_ref: "REDACTED"` cho BOD; `pii_redacted_due_to_transfer: true` khi đã bàn giao) |
| **Vận hành SLA & Miễn trừ** | `crm_cskh.api.sla.list_obligations`, `crm_cskh.api.sla.waive_obligation` | `GET` / `POST` | `work_item`, `status`, `obligation_type`, `page`, `page_size`; POST `waive_obligation(obligation_id, reason)` *(BOD dùng luồng aggregate + drilldown `0` PII)* | Danh sách nghĩa vụ SLA (`original_due_at`, `effective_due_at`, trạng thái), kết quả miễn trừ ghi `audit_events_json` |
| **Tính lại & Trạng thái Run** | `crm_cskh.api.p3_reporting.request_recompute`, `crm_cskh.api.p3_reporting.get_run_status` | `POST` / `GET` | `period_type`, `period_key`/`from_date`+`to_date`, `scope_type`, `scope_id`, `reason`; hoặc `run_id` | Khóa chống chạy trùng (`RECOMPUTE_IN_PROGRESS`), metadata `CSKH KPI Run` & `restated_from_run` |
| **Xuất báo cáo phân quyền** | `crm_cskh.api.p3_reporting.request_export`, `get_export_status`, `download_export` | `POST` / `GET` | `report_type`, `period_type`, `period_key`/`from_date`+`to_date`, `scope_type`, `scope_id`, `metric_code`, `format` (`csv`) | `export_id`, `status` (`queued\|running\|completed\|expired\|revoked`), `row_count`, `expires_at`, `download_endpoint` kiểm quyền sống 3 chặng |

---

## 6. Chuẩn Múi giờ & Biên Kỳ Báo cáo (`Asia/Ho_Chi_Minh`)

Hàm thuần `cskh.computePeriodBounds(periodType, anchorValue, customToValue, tz)` trong [`public/cskh/cskh.js`](../../apps/crm_cskh/crm_cskh/public/cskh/cskh.js) tính khoảng nửa mở `[from, to_exclusive)` (`YYYY-MM-DD`) theo múi giờ chuẩn của site (`Asia/Ho_Chi_Minh`), độc lập hoàn toàn với múi giờ hệ điều hành trình duyệt:
- **Ngày (`day`):** `[YYYY-MM-DD, nextDay(YYYY-MM-DD))` (ví dụ `2026-10-15` → `["2026-10-15", "2026-10-16")`).
- **Tuần (`week` — Thứ Hai đến Chủ Nhật):** `[mondayOfWeek, addDays(mondayOfWeek, 7))`.
- **Tháng (`month`):** `[YYYY-MM-01, firstDayOfNextMonth)` (xử lý chính xác tháng 28/29/30/31 ngày và chuyển năm `12 -> 01`).
- **Quý (`quarter`):** `Q1: [YYYY-01-01, YYYY-04-01)`, `Q2: [YYYY-04-01, YYYY-07-01)`, `Q3: [YYYY-07-01, YYYY-10-01)`, `Q4: [YYYY-10-01, (YYYY+1)-01-01)`.
- **Năm (`year`):** `[YYYY-01-01, (YYYY+1)-01-01)`.
- **Tùy chọn (`custom`):** Người dùng chọn từ ngày `D1` đến hết ngày `D2` → chuẩn hóa thành khoảng nửa mở `[D1, nextDay(D2))` (không tự cộng giờ `23:59:59` gây hở mili-giây cuối ngày).
