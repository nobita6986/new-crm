# Đối chiếu Nghiệp vụ Tuyển dụng & Cung ứng Nhân lực — Nhãn Hiển thị, Bàn giao `T1A` và Khoảng trống Nghiệp vụ

> **Baseline đối chiếu:** `origin/main` (`92e14af3532876ad5bc2468bf0e502474cc95a53`)
> **Nhánh chỉnh tầng hiển thị backend (cô lập):** `codex/recruitment-backend-labels`
> **Nguyên tắc bất biến:** Giữ nguyên 100% mã trạng thái (`status`), mã kết quả (`outcome_code`), phân quyền (`permlevel`, role guards), công thức KPI (`cskh-metrics-v2`) và toàn bộ bản chụp / lịch sử kiểm toán đã ghi (`CSKH Contact Attempt.outcome_label_snapshot`, `CSKH Assignment History`, `CSKH KPI Snapshot`).

---

## 1. Phân biệt Ngữ nghĩa Trung thực của Trạng thái & Kết quả Liên hệ

Trong nghiệp vụ **tuyển dụng và cung ứng nhân lực**, hệ thống hiện quản lý **luồng tiếp cận và liên hệ đầu vào (`Outreach & Contact Workflow`)**, chưa quản lý vòng đời tuyển chọn hay bố trí nhận việc tại doanh nghiệp. Bảng dưới đây khóa chặt ranh giới ngữ nghĩa để không báo cáo hoặc giao diện nào gây hiểu nhầm:

| Mức độ thực tế | Thực thể / Mã hệ thống | Cờ hành vi cốt lõi | Ý nghĩa thực tế trong tuyển dụng & cung ứng nhân lực | Điều trạng thái này **KHÔNG** xác nhận |
|---|---|---|---|---|
| **1. Đã thử liên hệ, chưa kết nối** | `no_answer`, `busy`, `unreachable`, `message_sent_no_reply`, `wrong_number`, `other` | `is_contact_attempt = 1`, **`is_connected = 0`** | Nhân viên tuyển dụng đã bấm gọi hoặc gửi tin SMS/Zalo/MXH nhưng chưa có phản hồi hai chiều. | **Chưa liên hệ được** với người lao động / người liên hệ; tuyệt đối không tính vào `CONNECTED_ITEMS` hay `CONNECTED_RATE`. |
| **2. Đã liên hệ (kết nối hai chiều)** | Bất kỳ `CSKH Contact Attempt` nào có `is_connected = 1`; `CRM Lead.status = Contacted` | **`is_connected = 1`** | Đã trao đổi hai chiều thực sự với người liên hệ qua điện thoại hoặc tin nhắn có phản hồi. | Chưa đồng nghĩa người liên hệ có nhu cầu tìm việc, phù hợp tiêu chí hay đồng ý ứng tuyển. |
| **3. Có quan tâm** | `connected_interested` (`Đã trao đổi — Có quan tâm`); `CRM Lead.status = Qualified` | `is_connected = 1`, **`is_interested = 1`**, `is_terminal = 0` | Người liên hệ phản hồi tích cực, quan tâm thông tin vị trí / mức lương / ca làm việc và đang tiếp tục trao đổi (`in_progress`). | Chưa xác nhận đã nộp đủ hồ sơ, chưa đạt phỏng vấn, **chưa được tuyển, bố trí hoặc nhận việc**. |
| **4. Đã hẹn lại** | `callback_requested` (`Đã hẹn gọi / liên hệ lại`), `connected_needs_followup` (`Đã trao đổi — Cần liên hệ tiếp`); `CSKH Work Item.status = on_hold`; `CRM Task.cskh_is_followup = 1` | `is_connected = 1`, `requires_followup = 1`, `suggested_next_status = on_hold` | Đã kết nối hai chiều nhưng người liên hệ bận hoặc cần gửi thêm thông tin, đã ấn định thời điểm liên hệ lại có tính SLA (`FOLLOWUP_CALLBACK`). Hoàn tất `CRM Task` độc lập **không** tự sinh lượt liên hệ hay kết nối. | Chưa xác nhận người liên hệ đồng ý ứng tuyển hay chốt lịch nhận việc. |
| **5. Hoàn tất mục tiêu chiến dịch** | `converted` (`Hoàn tất mục tiêu chiến dịch`); `CSKH Work Item.status = closed`; `CRM Lead.status = Converted` | `is_connected = 1`, `is_interested = 1`, `is_terminal = 1`, **`is_conversion = 1`** | Đã hoàn tất tiêu chí đầu ra của **chiến dịch liên hệ** (ví dụ: xác nhận đồng ý nộp hồ sơ hoặc chốt lịch hẹn phỏng vấn sơ tuyển) và đóng đầu việc liên hệ. | **KHÔNG tự xác nhận ứng viên đã trúng tuyển (`hired`), đã bố trí về doanh nghiệp (`placed`) hoặc đã đi làm thực tế (`onboarded`).** |
| **6. Đã tuyển / Bố trí / Nhận việc** | *(Chưa có thực thể trong P0–P3 — xem Mục 5)* | — | Ứng viên vượt qua sàng lọc, được ghép vào yêu cầu cung ứng của doanh nghiệp và xác nhận nhận việc thực tế. | **Chưa được mô hình hóa trong hệ thống hiện tại.** |

---

## 2. Đối chiếu Thực thể, Trường và Trạng thái Hiện có với Nghiệp vụ Tuyển dụng & Cung ứng Nhân lực

| Thành phần hiện có | Mã nội bộ (giữ nguyên) | Cách gọi cũ (mang nghĩa bán hàng / CSKH) | Cách hiểu & nhãn chuẩn cho Tuyển dụng & Cung ứng nhân lực | Ghi chú trung thực nghiệp vụ |
|---|---|---|---|---|
| `CRM Lead` | `CRM Lead` | Khách hàng tiềm năng / Khách hàng | **Hồ sơ liên hệ** (`Người liên hệ`) | Dòng dữ liệu đầu vào có thể mới chỉ là SĐT từ danh sách chiến dịch, chưa đủ thông tin để khẳng định là "Hồ sơ ứng viên" hoàn chỉnh. |
| `CRM Lead.status` | `New / Contacted / Nurture / Qualified / Unqualified / Converted / Junk` | Mới / Đã liên hệ / Nuôi dưỡng / Đủ điều kiện / Không đủ điều kiện / Đã chuyển đổi / *(chưa dịch)* | Mới / Đã liên hệ / Cần theo dõi thêm / Đủ điều kiện sơ bộ / Chưa phù hợp / Đạt mục tiêu liên hệ / Dữ liệu rác | Chỉ phản ánh trạng thái sàng lọc liên hệ trên `CRM Lead`. |
| `CSKH Work Item` | `queued / assigned / in_progress / on_hold / closed / revoked` | Đang chờ / Đã giao / Đang chăm sóc / Chờ chăm sóc lại / Đã hoàn tất chăm sóc / Đã thu hồi | Hàng đợi phân công / Đã giao / Đang xử lý liên hệ / Chờ liên hệ lại / Hoàn tất xử lý liên hệ / Đã thu hồi | Đầu việc liên hệ của 1 `CRM Lead` trong 1 `CSKH Campaign`. |
| `CSKH Campaign` | `Draft / Active / Paused / Closed` | Chiến dịch CSKH | **Chiến dịch liên hệ / tạo nguồn** | Đơn vị gom danh sách và phân công gọi/nhắn; chưa phải Đơn hàng cung ứng của doanh nghiệp (`Job Order`). |
| `CRM Deal` | `Prospecting / Qualification / Negotiation / Won / Lost` | Cơ hội (bán hàng) | **Giao dịch CRM** (hoặc Hợp đồng / Cơ hội hợp tác B2B) | Thực thể gốc của Frappe CRM tại `/crm/deals`; không tự liên kết với việc bố trí từng ứng viên. |
| `CRM Organization` | `CRM Organization` | Tổ chức | **Doanh nghiệp / Tổ chức** | Có thể dùng làm hồ sơ doanh nghiệp đối tác, nhưng chưa có bảng Yêu cầu cung ứng nhân lực con. |
| Vai trò hệ thống | `Sales User` / `Sales Manager` / `CSKH BOD Viewer` / `CSKH CRM Admin` | Nhân viên bán hàng / Quản lý bán hàng / BOD CSKH / Quản trị CSKH | **Nhân viên tuyển dụng** / **Trưởng nhóm tuyển dụng** / **Ban Giám đốc** / **Quản trị hệ thống** | Giữ nguyên tên `Role` trong DB để không ảnh hưởng phân quyền. |

---

## 3. Các Nhãn Tầng Hiển thị Backend & Dữ liệu Mẫu Đã Cập nhật ở Commit Riêng (`codex/recruitment-backend-labels`)

Các mục dưới đây do backend Python phát thẳng ra API JSON, file CSV export, thông báo MariaDB hoặc bảng cấu hình DB nên **chỉnh `translations/vi.csv` ở giao diện không tự ghi đè được**:

| Hạng mục | Nguồn phát trong code | Nhãn cũ (trên `92e14af`) | Nhãn mới (trên `codex/recruitment-backend-labels`) | Bảo toàn mã, công thức & lịch sử |
|---|---|---|---|---|
| KPI `ASSIGNED_INTERVALS` | `services/kpi_calculator.py` (`METRIC_LABELS_VI`), `services/kpi_runs.py` (`unpack_snapshot_metrics`), `services/p3_export.py` | `Lượt nhận khách (Khoảng trách nhiệm)` | `Lượt nhận hồ sơ (Khoảng phụ trách)` | Mã chỉ số & công thức giữ nguyên; chiếu nhãn mới trên bản đọc bộ nhớ mà không sửa `tabCSKH KPI Snapshot` cũ. |
| KPI `UNIQUE_ASSIGNED_ITEMS` | `services/kpi_calculator.py` (`METRIC_LABELS_VI`) | `Khách được giao duy nhất` | `Hồ sơ liên hệ được giao duy nhất` | Giữ nguyên `COUNT(DISTINCT work_item)`. |
| KPI `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` | `services/kpi_calculator.py` (`METRIC_LABELS_VI`) | `Tỷ lệ đạt SLA liên hệ lần đầu (Cấp Hồ sơ khách hàng)` | `Tỷ lệ đạt SLA liên hệ lần đầu (Cấp hồ sơ liên hệ)` | Giữ nguyên `deadline_cohort` & SLA engine. |
| KPI `FIRST_ATTEMPT_SLA_RATE` & `FOLLOWUP_SLA_RATE` | `services/kpi_calculator.py` (`METRIC_LABELS_VI`) | `... (Khoảng trách nhiệm)` / `... gọi lại (Callback)` | `Tỷ lệ đạt SLA liên hệ lần đầu (Khoảng phụ trách)` / `Tỷ lệ đạt SLA lịch hẹn liên hệ lại (Callback)` | Giữ nguyên công thức. |
| KPI `CONNECTED_ATTEMPT_COUNT`, `CONNECTED_ITEMS`, `CONNECTED_RATE` | `services/kpi_calculator.py` (`METRIC_LABELS_VI`) | `Khách kết nối thành công duy nhất`, v.v. | `Tổng lượt kết nối hai chiều thành công`, `Hồ sơ đã kết nối hai chiều duy nhất`, `Tỷ lệ kết nối hai chiều (Cấp khoảng phụ trách)` | Làm rõ chỉ đo kết nối hai chiều (`is_connected=1`), không phải tuyển/nhận việc. |
| KPI `CONVERSION_RATE` | `services/kpi_calculator.py` (`METRIC_LABELS_VI`) | `Tỷ lệ chuyển đổi đạt mục tiêu chiến dịch` | `Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch` | Loại bỏ hàm ý chốt đơn bán hàng / đã tuyển dụng. |
| Thành phần điểm `FOLLOWUP_SLA_RATE` | `services/kpi_calculator.py` (`SCORE_COMPONENT_LABELS_VI`) | `SLA chăm sóc đúng lịch hẹn` | `SLA liên hệ lại đúng lịch hẹn` | Giữ nguyên trọng số `20%` và thang điểm `PROCESS_SCORE`. |
| Nhãn phạm vi công ty trên Snapshot | `services/kpi_calculator.py` (`COMPANY_SCOPE_LABEL`), `services/report_query.py` | `Toàn công ty CSKH` | `Toàn công ty` | Chuẩn hóa trên bản chụp mới và chiếu khi đọc bản chụp cũ. |
| 12 `CSKH Outcome Code` mặc định (`label_vi` & `description`) | `p2_models.py` (`DEFAULT_OUTCOME_CODES`, `ensure_p2_seeds`) | `Đã trao đổi — Cần chăm sóc tiếp`, `Khách hẹn gọi / liên hệ lại`, mô tả `khách hàng` / `mục tiêu chăm sóc / chốt thành công` | `Đã trao đổi — Cần liên hệ tiếp`, `Đã trao đổi — Có quan tâm`, `Đã hẹn gọi / liên hệ lại`, `Hoàn tất mục tiêu chiến dịch` (mô tả ghi rõ không tự xác nhận đã tuyển/bố trí/nhận việc) | Chỉ cập nhật danh mục gốc khi `version == 1` chưa tùy biến; giữ nguyên 100% `outcome_label_snapshot` lịch sử. |
| Tiêu đề `CSKH Policy` & `CSKH Working Calendar` mặc định | `p2_models.py` (`DEFAULT_GLOBAL_POLICY`), `p3_models.py` (`ensure_p3_seeds`) | `Chính sách Vận hành CSKH Mặc định` / `Lịch Hành Chính CSKH Mặc Định (DEV / Configurable)` | `Chính sách Vận hành Liên hệ & Tuyển dụng Mặc định` / `Lịch Hành Chính Mặc Định (DEV / Configurable)` | Giữ nguyên mã `GLOBAL-DEFAULT`, `VN-OFFICE-DEFAULT`, `version = 1`. |
| Chuông thông báo giao hồ sơ & lịch hẹn (`CRM Notification`) | `services/notifications.py` (`_ensure_notification`, `deliver_followup_notification`) | `You were assigned a CRM Lead {lead}.` / `Bạn có lịch hẹn chăm sóc lại #{task_ref}.` | `Bạn được phân công hồ sơ liên hệ {lead}.` / `Bạn có lịch hẹn liên hệ lại #{task_ref}.` | Giữ nguyên `ToDo.description = "CSKH assignment ({work_item})"` để không ảnh hưởng truy vấn đóng ToDo. |
| Lý do đóng tự động DNC & lý do đóng mặc định | `services/ownership.py` (`public_history_reason`), `services/contact_attempt.py` | `Tự động đóng Work Item do khách yêu cầu ngừng liên hệ (DNC)` / `Hoàn tất mục tiêu chăm sóc` | `Tự động đóng Work Item do người liên hệ yêu cầu ngừng liên hệ (DNC)` / `Hoàn tất mục tiêu liên hệ chiến dịch` | Giữ cả chuỗi cũ lẫn chuỗi mới trong `_STATIC_NEUTRAL_REASONS` để lịch sử cũ không bị migrate lại. |
| Thông báo tắt cờ báo cáo P3 | `api/p3_reporting.py` | `Hệ thống báo cáo chất lượng CSKH P3...` | `Hệ thống báo cáo chất lượng liên hệ & SLA P3...` | Giữ nguyên mã lỗi `POLICY_CONFLICT` (`P3DisabledError`). |
| Dữ liệu mẫu DEV & E2E không bị neo text | `deploy/dev/seed/seed_demo.py`, `tools/e2e_seed.py`, `tools/p3_e2e_seed.py` | `Khách chưa rõ tên`, `Có nhu cầu, cần tư vấn`, `Khách thiếu định danh SĐT`, `Chiến dịch Chăm sóc Khách hàng P3`, `Nhóm CSKH Miền Bắc/Nam`, `Gửi tài liệu tư vấn...` | `Người liên hệ chưa rõ tên`, `Quan tâm thông tin việc làm, cần trao đổi thêm`, `Hồ sơ thiếu định danh SĐT`, `Chiến dịch Tuyển dụng & Liên hệ P3`, `Nhóm Tuyển dụng Miền Bắc/Nam`, `Gửi thông tin vị trí & ca làm việc...` | Giữ nguyên SĐT giả, ID chiến dịch, công thức CSV injection `=CMD` và chuỗi con `Miền Bắc` / `Miền Nam`. |

---

## 4. Danh sách Bàn giao cho `T1A` (Tầng Giao diện, `*_strings.py`, `translations/vi.csv` & E2E UI)

### 4.1 Bảng Nhãn Giao diện `T1A` Cần Đổi / Đồng bộ Tiếp

| Nhóm | Nguồn phát (`msgid` hoặc tệp) | Hiện trạng trên `main` (`vi.csv` / JS / HTML) | Nhãn đề xuất cho `T1A` | Ý nghĩa thực tế & lý do cần đổi |
|---|---|---|---|---|
| **P3 — Điều hướng & Tiêu đề trang** | `p3_strings.py:11-25` → `translations/vi.csv:913-926` | `Tổng quan chất lượng CSKH (BOD)`, `Chiến dịch & nguồn khách` | `Tổng quan chất lượng liên hệ & SLA (BOD)`, `Chiến dịch & nguồn liên hệ` | Phản ánh đúng báo cáo chất lượng tiếp cận/liên hệ của đội tuyển dụng. |
| **P3 — Phụ đề & Disclaimer trung thực** | `p3_strings.py:27-45` → `translations/vi.csv:927-945` | `...chất lượng thực thi quy trình chăm sóc khách hàng...`, `...thông tin định danh khách hàng (PII) được ẩn...`, `Hiệu quả theo chiến dịch & nguồn khách hàng` | `...chất lượng thực thi quy trình liên hệ tuyển dụng...`, `...thông tin định danh người liên hệ (PII) được ẩn...`, `Hiệu quả theo chiến dịch & nguồn liên hệ` | Khử cách gọi CSKH và giữ thông điệp bảo vệ PII cho BOD. |
| **P3 — Tiêu đề Modal Drilldown KPI** | `p3_strings.py:76-115` → `translations/vi.csv:986-1013` (`Unique assigned work items`, `Eligible work-item coverage`, `Callback follow-up SLA on-time rate`, `Work-item first-contact SLA rate`, `Campaign goal conversion rate`) | `Khách được giao duy nhất`, `Tỷ lệ phủ liên hệ khách được giao`, `Tỷ lệ chăm sóc đúng lịch hẹn`, `Tỷ lệ hoàn tất mục tiêu chiến dịch` | `Hồ sơ liên hệ được giao duy nhất`, `Tỷ lệ phủ liên hệ trên hồ sơ đủ điều kiện`, `Tỷ lệ liên hệ lại đúng lịch hẹn`, `Tỷ lệ hoàn tất mục tiêu liên hệ chiến dịch` | Đồng bộ tiêu đề modal drilldown (`p3-widgets.js:1304` dùng `t(metric.label)`) khớp với thẻ KPI (`p3-widgets.js:930` dùng `m.label_vi` từ backend). |
| **P3 — Nhãn SLA & Cột bảng Drilldown** | `p3_strings.py:168-245` → `translations/vi.csv:1066-1150` (`FOLLOWUP_CALLBACK`, `Lead reference`, `Customer name`, `Lead source`, `Open My Customers`, `Open Callbacks`, `CSKH KPI Run`) | `SLA chăm sóc hẹn lại`, `Mã tham chiếu khách`, `Tên khách hàng`, `Nguồn khách hàng`, `Mở Khách được giao`, `Mở Lịch hẹn chăm sóc lại`, `Đợt tính toán KPI CSKH` | `SLA lịch hẹn liên hệ lại`, `Mã hồ sơ liên hệ`, `Tên người liên hệ`, `Nguồn liên hệ`, `Mở Hồ sơ được giao`, `Mở Lịch hẹn liên hệ lại`, `Đợt tính toán KPI` | **Lưu ý:** Chỉ dịch nhãn cột trên UI; **giữ nguyên khóa JSON `"customer_name"`** trong API drilldown. |
| **P1/P2 — Trạng thái & Bộ đếm còn sót** | `p1_translations.py` / `p2_translations.py` / `translations/vi.csv` (`Completed care`, `Customer`, `Unique customers in scope`, `Do-not-contact (DNC) customers`, `Close care work item with this outcome`, `Task completion without a contact attempt...`, `Operational volume metrics only...`) | `Đã hoàn tất chăm sóc`, `Khách hàng`, `Số khách hàng duy nhất trong phạm vi`, `Số khách hàng ngừng liên hệ (DNC)`, `Đóng công việc CSKH theo kết quả này`, `...chăm sóc thành công.`, `...không phải điểm chất lượng CSKH...` | `Hoàn tất xử lý liên hệ`, `Người liên hệ`, `Số hồ sơ không trùng trong phạm vi`, `Số hồ sơ ngừng liên hệ (DNC)`, `Đóng hồ sơ công việc theo kết quả này`, `...liên hệ hai chiều hay hoàn tất mục tiêu.`, `...không phải điểm chất lượng liên hệ hay xếp loại SLA.` | Hoàn tất các chuỗi P1/P2 trên `/cskh/dashboard`, `/cskh/progress`, `/cskh/my-customers` và form ghi kết quả; giữ nguyên `msgid` tiếng Anh trong JS/HTML. |
| **Upstream `/crm` — Nhãn bán hàng & trạng thái chưa dịch** | `translations/vi.csv:60-255` (`Salesperson`, `Sales Tasks`, `Sales trend`, `Funnel conversion`, `Won Deals`, `Deal Value`, `Forecasted Revenue`, `Annual Revenue`, `Products`; bổ sung `Junk`, `Prospecting`, `Qualification`, `Negotiation`) | `Nhân viên bán hàng`, `Công việc bán hàng`, `Xu hướng bán hàng`, `Chuyển đổi qua phễu bán hàng`, `Cơ hội thành công`, `Giá trị cơ hội`, `Doanh thu dự báo` + 4 trạng thái tiếng Anh chưa dịch | `Nhân viên phụ trách`, `Công việc liên hệ`, `Xu hướng giao dịch CRM`, `Chuyển đổi theo giai đoạn`, `Giao dịch thành công`, `Giá trị giao dịch`, `Giá trị dự kiến` + dịch `Junk -> Dữ liệu rác`, `Prospecting -> Tiếp cận ban đầu`, `Qualification -> Thẩm định nhu cầu`, `Negotiation -> Đàm phán` | Trung hòa thuật ngữ bán lẻ/chốt đơn khi người dùng mở `/crm/leads` hoặc `/crm/deals`. |
| **Dữ liệu mẫu E2E P2 có neo Browser test** | `tools/p2_e2e_seed.py:196` + `deploy/p2/verify_p2_browser_e2e.mjs:592` | `Khách hỏi bảng giá <script>...` | `Người liên hệ hỏi mức lương và ca làm việc <script>...` *(cập nhật đồng thời cả 2 tệp)* | Thay câu mẫu "hỏi bảng giá" bằng ngữ cảnh hỏi lương/ca làm việc mà vẫn giữ nguyên kiểm thử chống XSS. |

### 4.2 Cảnh báo Kỹ thuật Bắt buộc cho `T1A` (Tránh Vỡ CI / Gate Dịch thuật)

1. **Bẫy `target == source` trong `deploy/p1/check_translations_audit.py:89-93`:**
   - Gate kiểm tra dịch thuật đánh lỗi `untranslated (EN)` nếu bất kỳ chuỗi nào khai báo trong `p1_strings.py` / `p2_strings.py` có `target == source` trong `translations/vi.csv` (trừ chuỗi viết HOA toàn bộ).
   - Vì nhánh `codex/recruitment-backend-labels` đã cập nhật trực tiếp `DEFAULT_OUTCOME_CODES.label_vi` trong `p2_models.py` (ví dụ `Đã trao đổi — Cần liên hệ tiếp`, `Đã trao đổi — Có quan tâm`, `Đã hẹn gọi / liên hệ lại`, `Hoàn tất mục tiêu chiến dịch`), `T1A` **không nên** khai báo các chuỗi tiếng Việt gốc này vào `p2_strings.py` với bản dịch trùng chính nó; đồng thời cần bổ sung đầy đủ 11 `msgid` tiếng Anh mới của `p1_strings.py` vào `translations/vi.csv`.
2. **Không chạy trực tiếp `deploy/p1/rebuild_translations.py` hoặc `deploy/p2/rebuild_translations.py`:**
   - Hai script này dựng lại `vi.csv` từ commit P1/P2 cũ và **xóa mất toàn bộ các dòng dịch P3 (`vi.csv:913-1266`)**. Hãy cập nhật trực tiếp `p1_translations.py`, `p2_translations.py`, `p3_strings.py` và `translations/vi.csv`, sau đó kiểm tra bằng `python deploy/p1/check_translations.py` và `python deploy/p1/check_translations_audit.py`.
3. **Các assertion Browser E2E cần cập nhật đồng bộ khi `T1A` đổi `vi.csv`:**
   - `deploy/p1/verify_p1_browser_e2e.mjs:249`: `'Số khách hàng duy nhất'` (hiện sẽ lệch khi `Unique customers` đổi sang `'Số hồ sơ không trùng'`).
   - `deploy/p2/verify_p2_browser_e2e.mjs:267`: `'trạng thái cskh'` (hiện sẽ lệch khi `Care status` đổi sang `'Trạng thái liên hệ'`).
   - `deploy/p2/verify_p2_browser_e2e.mjs:1028, 1046`: `'không phải điểm chất lượng CSKH hay xếp loại SLA'` và `'Số khách hàng ngừng liên hệ (DNC)'` (nếu đổi 2 dòng này trong `vi.csv`).
   - Giữ nguyên `msgid` tiếng Anh `"Do-not-contact (DNC) customers"` trong `public/cskh/progress.js` vì `tests/test_ui_p2_pages_and_context.py:206` kiểm tra chuỗi mã nguồn này.
4. **Các hằng số neo kỹ thuật TUYỆT ĐỐI KHÔNG ĐỔI:**
   - `services/notifications.py:44, 102`: `ToDo.description = f"CSKH assignment ({work_item})"` (được dùng để truy vấn đóng ToDo khi thu hồi/chuyển giao; kiểm thử tại `test_p1_ownership.py:131`).
   - `jobs/import_worker.py:32`: `PLACEHOLDER_FIRST_NAME = "Chưa rõ tên"` (đã trung tính; kiểm thử tại `test_ui_p1_endpoints.py:1315` & `ci_p1_http_probe.py:1544`).
   - `services/report_query.py:1777`: Khóa JSON `"customer_name"` trong payload API drilldown (kiểm thử tại `test_p3_reporting_and_security.py:266, 355`).
   - `deploy/p2/verify_p2_http_e2e.py:949, 1390` & `deploy/p2/verify_p2_browser_e2e.mjs:1291`: Chuỗi canary kiểm thử rò rỉ dữ liệu mật `chốt gói dịch vụ`.
   - Giữ tối thiểu các từ khóa tiếng Việt neo smoke trong `deploy/images/verify_clean_site.py:619-664` (`"Tổng quan"`, `"Chất lượng"`, `"Múi giờ"`, `"Cuộc gọi"`, `"Công việc"`, `"Ghi chú"`) và `deploy/compose/scripts/run_isolated_smoke.py:1127-1144`.

---

## 5. Các Khoảng trống Nghiệp vụ Cần Owner / `T0` Quyết định (Ngoài Phạm vi Đổi Nhãn)

Hệ thống P0–P3 hiện giải quyết trọn vẹn bài toán **quản trị danh sách liên hệ, phân phối đầu việc chống trùng, nhật ký liên hệ trung thực, lịch hẹn lại và đo lường SLA/KPI quy trình liên hệ**. Để đáp ứng đầy đủ nghiệp vụ **tuyển dụng và cung ứng nhân lực**, còn **3 khoảng trống mô hình dữ liệu & quy trình cốt lõi** cần Owner / `T0` chốt thiết kế ở pha tiếp theo (không thể giải quyết chỉ bằng đổi nhãn):

### 5.1 `GAP-01` — Hồ sơ Ứng viên / Người lao động (`Candidate Profile`)
- **Hiện trạng:** `CRM Lead` chỉ lưu định danh liên hệ cơ bản (họ tên, SĐT chuẩn hóa, email, nguồn, cờ DNC) phục vụ gọi/nhắn tiếp cận ban đầu; không có cấu trúc dữ liệu nghiệp vụ lao động.
- **Quyết định cần chốt:**
  1. Khi nào một `Hồ sơ liên hệ` (`CRM Lead`) được nâng cấp / liên kết sang `Hồ sơ ứng viên` (`Candidate Profile`) — ngay khi `connected_interested`, khi `converted`, hay tạo độc lập?
  2. Tập trường bắt buộc của ứng viên cung ứng nhân lực: năm sinh / độ tuổi, giới tính, quê quán / nơi ở hiện tại, nhóm tay nghề / kỹ năng, kinh nghiệm, ca làm việc nhận được (hành chính / xoay ca / ca đêm), tình trạng giấy tờ định danh (CCCD, hồ sơ khám sức khỏe, lý lịch), trạng thái sẵn sàng đi làm và lịch sử bố trí trước đây.
  3. Chính sách bảo mật PII & quyền xem giấy tờ tùy thân giữa Nhân viên tạo nguồn, Nhân viên điều phối bố trí, Trưởng nhóm và BOD.

### 5.2 `GAP-02` — Yêu cầu Cung ứng Nhân lực của Doanh nghiệp (`Client Staffing Requisition / Job Order`)
- **Hiện trạng:** `CRM Organization` và `CRM Deal` là mô hình CRM thương mại tổng quát; `CSKH Campaign` chỉ là chiến dịch gọi ra trên tập danh sách import, không mang định mức tuyển dụng của khách hàng doanh nghiệp.
- **Quyết định cần chốt:**
  1. Mô hình hóa thực thể **Yêu cầu cung ứng / Đơn hàng nhân lực (`Staffing Requisition` / `Job Order`)** gắn với `CRM Organization` (nhà máy / doanh nghiệp nhận lao động) và tùy chọn liên kết `CRM Deal` (hợp đồng cung ứng).
  2. Cấu trúc chỉ tiêu đơn hàng: vị trí công việc, số lượng cần cung ứng (`target headcount`), số lượng đã bố trí / đã nhận việc / còn thiếu, điều kiện tuyển (độ tuổi, giới tính, sức khỏe, tay nghề), khung lương & phụ cấp, ca kíp, địa điểm làm việc, ngày cần người (`deadline`) và trạng thái đơn hàng (`Mở / Đang tuyển / Đủ chỉ tiêu / Tạm dừng / Đóng`).
  3. Quan hệ giữa `CSKH Campaign` và `Job Order`: một chiến dịch gọi ra phục vụ một đơn hàng cụ thể hay tạo nguồn chung cho nhiều đơn hàng cùng khu vực/ngành nghề?

### 5.3 `GAP-03` — Ghép Người với Yêu cầu & Vòng đời Bố trí / Nhận việc (`Candidate–Requisition Matching & Placement`)
- **Hiện trạng:** Không có thực thể trung gian nối `Người liên hệ / Ứng viên` với `Yêu cầu cung ứng của doanh nghiệp`. Mã kết quả `converted` (`Hoàn tất mục tiêu chiến dịch`) đóng `CSKH Work Item` ngay khi đạt mục tiêu liên hệ ban đầu, chưa phản ánh kết quả phỏng vấn hay đi làm thực tế.
- **Quyết định cần chốt:**
  1. Quy trình trạng thái chuẩn của bản ghi **Ứng tuyển / Điều phối (`Application / Placement`)**:
     `Đề xuất ghép (Shortlisted) -> Đã gửi doanh nghiệp / Hẹn phỏng vấn -> Đạt sơ tuyển / Phỏng vấn -> Đã bố trí lịch nhận việc (Placed) -> Đã nhận việc thực tế (Onboarded) -> Đạt thời gian bảo hành / Nghỉ việc sớm (Early Attrition) / Từ chối`.
  2. Quy tắc chống trùng ghép chéo: một ứng viên có được đề xuất đồng thời vào nhiều đơn hàng không, và ở bước nào (ví dụ `Đã chốt lịch nhận việc`) hệ thống phải khóa không cho điều phối viên khác ghép trùng?
  3. Bộ KPI giai đoạn sau liên hệ (tách biệt hoàn toàn với `PROCESS_SCORE` liên hệ P3): Tỷ lệ đến phỏng vấn (`Show-up Rate`), Tỷ lệ đạt phỏng vấn (`Offer/Pass Rate`), Tỷ lệ nhận việc thực tế (`Onboard Rate`), Tỷ lệ lấp đầy đơn hàng (`Fill Rate`) và Tỷ lệ duy trì sau `N` ngày nhận việc (`Retention Rate`).
