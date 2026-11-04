# Hồ Sơ Quyết Định Kiến Trúc Phase P3 (`ADR-P3-01` .. `ADR-P3-15`)

Tài liệu này tập hợp đầy đủ 15 quyết định kiến trúc cốt lõi của Phase P3 theo `docs/root-plan/P3.md` §18, dựa trên kiểm chứng trực tiếp mã nguồn P0–P2 (`449fbc26`) và upstream đã ghim (`frappe@v15.123.1`, `crm@v1.87.0`).

---

## `ADR-P3-01` — Kiến trúc Nghĩa vụ SLA: `CSKH SLA Obligation` thay vì gắn cột đơn trên `CRM Lead` / `CSKH Work Item`
- **Bối cảnh:** Upstream `CRM Service Level Agreement` ghi trực tiếp `response_by`, `first_responded_on`, `sla_status` lên `CRM Lead`/`CRM Deal` (`crm_service_level_agreement.py:74-188`), chỉ lưu được 1 trạng thái hiện hành cho toàn hồ sơ. Trong khi đó P3 cần theo dõi đồng thời `FIRST_ATTEMPT`, nhiều chu kỳ `FOLLOWUP`, `FIRST_CONNECTED` và trách nhiệm riêng của từng khoảng `A -> B -> A`.
- **Các phương án xem xét:** (A) Thêm cột SLA lên `CSKH Work Item`; (B) Dùng `CRM Service Level Agreement` của upstream; (C) Tạo DocType chuyên biệt `CSKH SLA Obligation` gắn với `(work_item, interval_id, task_id, obligation_type)`.
- **Quyết định:** Chọn **(C) `CSKH SLA Obligation`**.
- **Đánh đổi & Kiểm chứng:** Tốn thêm 1 bảng có chỉ mục composite, đổi lại bảo toàn `original_due_at` bất biến, lưu đầy đủ lịch sử `A -> B -> A` và mọi lần callback mà không sửa upstream. Kiểm chứng bằng `test_p3_sla_engine.py`.

## `ADR-P3-02` — Lịch Làm Việc & Ngày Nghỉ Lễ: Tái sử dụng `CRM Holiday List` + Xây hàm thuần `services/calendar.py`
- **Bối cảnh:** Kiểm tra `crm_service_level_agreement.py:240-293,318-321` phát hiện `CRM Service Day` không hỗ trợ giờ nghỉ trưa hay ca qua đêm, `calc_elapsed_time` lặp từng giây (`O(seconds)`), và `calc_time` giữ nguyên giờ trong ngày khi nhảy qua ngày nghỉ. Ngược lại `CRM Holiday List` (`crm_holiday_list.py:12-88`) có cấu trúc bảng ngày nghỉ sạch nhưng mặc định mở quyền ghi cho `Sales User`.
- **Quyết định:** Tái sử dụng `CRM Holiday List` làm nguồn ngày lễ (kèm hook khóa quyền ghi chỉ cho `CSKH CRM Admin` / `System Manager`); xây dựng `CSKH Working Calendar` (hỗ trợ nhiều ca/ngày và ca qua đêm) cùng 2 hàm thuần `business_minutes_between` và `add_business_minutes` có độ phức tạp $O(\text{days} \cdot \text{shifts})$.
- **Kiểm chứng:** Bộ golden test `T-CAL-01..T-CAL-09` và compatibility test đọc trực tiếp từ `CRM Holiday List`.

## `ADR-P3-03` — Quy ước Lưu trữ Thời gian & Biên Múi giờ `Asia/Ho_Chi_Minh`
- **Bối cảnh:** P1/P2 và Frappe v15 lưu mọi cột `DATETIME(6)` dưới dạng naive local datetime theo `System Settings.time_zone` (`Asia/Ho_Chi_Minh`). Đổi đơn phương sang lưu UTC sẽ phá vỡ toàn bộ truy vấn P1/P2.
- **Quyết định:** Giữ nguyên quy ước lưu `DATETIME(6)` theo `Asia/Ho_Chi_Minh`; đảm bảo patch migration đồng bộ `System Settings.time_zone = "Asia/Ho_Chi_Minh"` khi còn giá trị mặc định cài đặt (`Asia/Kolkata`); chuẩn hóa mọi input aware (`Z`, `+07:00`) về `ZoneInfo("Asia/Ho_Chi_Minh")` trước khi tính lịch và lưu DB; gắn `"timezone": "Asia/Ho_Chi_Minh"` và ISO-8601 `+07:00` trong metadata API.

## `ADR-P3-04` — Phân định Trách nhiệm SLA khi Chuyển giao (`A -> B`, `A -> B -> A`) & Handoff Grace Có Trần
- **Bối cảnh:** Nếu tính SLA theo `lead_owner` cuối cùng, người nhận cuối phải gánh lỗi quá hạn của người trước hoặc nhân sự có thể chuyền bóng liên tục để reset SLA.
- **Quyết định:** Tách `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE` (trải nghiệm khách hàng theo `work_item_original_due_at`) và `FIRST_ATTEMPT_SLA_RATE` theo `interval_id`. Chuyển giao trước hạn đóng nghĩa vụ của `A` ở `handoff_closed` và cấp cho `B` `max(remaining, handoff_min_grace_minutes)` nhưng giới hạn tối đa `max_handoff_grace_resets` lần trên mỗi Work Item. Chuyển giao sau hạn chốt lỗi `breached_handoff` cho `A` và mở nghĩa vụ khắc phục riêng cho `B`.

## `ADR-P3-05` — Nguồn Quy thuộc Team Lịch sử (`team-as-of`) & Xử lý Dữ liệu Thiếu Chứng Cứ
- **Bối cảnh:** `CRM Sales Hierarchy` chỉ lưu cây hiện tại; `CSKH Assignment History` ở P1/P2 chưa lưu `team_at_assignment`.
- **Quyết định:** Tạo `CSKH Team Membership History` (append-only, gắn `doc_events` vào `CRM Sales Hierarchy`) và thêm `team_at_assignment` + `attribution_status` lên `CSKH Assignment History`. Bản ghi lịch sử trước thời điểm cutover P3 nếu không có chứng cứ `team-as-of` xác thực bắt buộc gán `attribution_status = "historical_attribution_unknown"`, loại khỏi mẫu số của từng team riêng lẻ và báo cáo minh bạch trong `exclusion_counts.historical_attribution_unknown` + dòng phân rã công ty `__unattributed_historical_team__`. Tuyệt đối không đoán từ cây hiện tại.

## `ADR-P3-06` — Mô hình Read-Model: Snapshot Theo Kỳ Kèm Append-Only Run Revisions (`CSKH KPI Run` + `CSKH KPI Snapshot`)
- **Bối cảnh:** Truy vấn trực tiếp `CSKH Contact Attempt` kèm chiếu `CSKH Attempt Correction` bằng Python gây độ trễ `2.52s` ở 50k attempts (`OBS-P2-02`). Tuy nhiên chỉ dùng snapshot tĩnh sẽ không thấy dữ liệu mới hoặc mất dấu vết khi đính chính kỳ cũ.
- **Quyết định:** Kết hợp `CSKH KPI Run` + `CSKH KPI Snapshot` (append-only revisions, chuyển cờ `is_latest_published` nguyên tử trong DB transaction) với `CSKH Dirty Scope` để báo trạng thái độ tươi (`fresh` / `stale_recomputing`) cùng `as_of` và `source_watermark`. Dashboard chỉ đọc `CSKH KPI Snapshot` có chỉ mục composite, đạt thời gian đọc ổn định không phụ thuộc tổng số dòng `CSKH Contact Attempt`.

## `ADR-P3-07` — Tách Luồng Ghi Tác Nghiệp Khỏi Luồng Tính Báo Cáo (`Post-Commit Dirty Invalidation`)
- **Bối cảnh:** Nếu tính lại KPI đồng bộ ngay bên trong transaction `record_attempt`, giao dịch ghi cuộc gọi sẽ bị chậm và dễ gây lock contention trên các bảng tổng hợp.
- **Quyết định:** `record_attempt`, `correction`, `ownership`, `followup` chỉ cập nhật nghĩa vụ SLA trực tiếp liên quan tới Work Item đang giữ khóa và đăng ký callback `frappe.db.after_commit` để ghi nhận `CSKH Dirty Scope` + đẩy job RQ bất đồng bộ (`jobs/recompute_kpi.py`) có cơ chế gộp (coalesce) theo `(period, scope)`.

## `ADR-P3-08` — Vận hành Module QA ở Chế độ Tắt Mặc Định (`qa_enabled = false`)
- **Bối cảnh:** Quy trình QA vận hành thực tế chưa kích hoạt ở giai đoạn hiện tại; việc sinh review giả sẽ làm sai lệch số liệu, còn việc chấm `QA = 0` sẽ phạt oan toàn bộ nhân sự.
- **Quyết định:** Đặt mặc định `qa_enabled = 0` trong `CSKH Policy`, không tạo bất kỳ bản ghi `CSKH QA Review` hay `verified` giả nào. Giữ schema `CSKH QA Review` và guard anti-self-review sẵn sàng cho tương lai; khi `qa_enabled = 0`, API ghi QA trả `409 QA_DISABLED` và `QA_PASS_RATE` trả `status = "disabled"`.

## `ADR-P3-09` — Công thức `PROCESS_SCORE` Chuẩn Hóa Lại Trọng Số (`Provisional Re-weighting`) & Ngưỡng Đủ Dữ Liệu
- **Bối cảnh:** Khi `qa_enabled = false` (thiếu trọng số `15` của QA) hoặc nhân viên chưa có lịch hẹn đến hạn, điểm tổng cần phản ánh công bằng các thành phần hiện có mà không phóng đại mẫu quá nhỏ.
- **Quyết định:** Tính $\text{PROCESS\_SCORE} = \text{ROUND}(\sum_{i \in V} w_i r_i / \sum_{i \in V} w_i, 2)$ với `status = "provisional"` khi $W_{\min} \le \sum_{i \in V} w_i < 100$ (khớp tuyệt đối Golden Example `7350 / 85 = 86.47`), và trả `value = null, status = "insufficient_data"` khi $\sum_{i \in V} w_i < W_{\min}$ hoặc `denominator == 0`. Gắn nhãn rõ mọi ngưỡng/trọng số mặc định là `DEV / configurable`.

## `ADR-P3-10` — Quy tắc Tổng hợp Đa Cấp Thời Gian & Tổ Chức (Cấm Trung Bình Cộng Phần Trăm & Tách Grain Công Ty)
- **Bối cảnh:** Lấy trung bình cộng `%` ngày để ra `%` tháng hoặc cộng số interval nhân viên để ra số hồ sơ công ty sẽ gây sai số toán học nghiêm trọng khi sản lượng các ngày chênh lệch hoặc có chuyển giao `A -> B -> A`.
- **Quyết định:** Mọi rollup thời gian (`Daily -> Weekly -> Monthly -> Quarterly -> Yearly`) và tổ chức (`Employee -> Team -> Company`) đều tính trực tiếp từ tổng tử số / mẫu số đủ điều kiện của kỳ đó; đồng thời tách rõ chỉ số grain `DISTINCT work_item` của công ty/chiến dịch khỏi chỉ số grain `ownership interval` của nhân viên.

## `ADR-P3-11` — Bảo toàn Quyền Riêng Tư Trên Báo Cáo BOD & Dữ Liệu Lịch Sử Nhân Viên Cũ (`Zero Retroactive PII`)
- **Bối cảnh:** BOD cần xem chất lượng toàn công ty mà không được cấp `System Manager` và không thấy PII khách hàng (`ADR-005`); nhân viên cũ / Leader cũ cần xem KPI lịch sử của mình nhưng không được tiếp tục đọc SĐT/ghi chú của khách hàng đã chuyển sang đơn vị khác.
- **Quyết định:** Snapshot chỉ lưu số liệu thống kê không chứa PII khách hàng. Trên API drill-down, hệ thống chặn hoàn toàn trường định danh khách hàng đối với BOD, và tự động che (`null` + `pii_redacted_due_to_transfer = true`) đối với Staff/Leader nếu tại thời điểm truy vấn họ không còn quyền đọc trực tiếp `CRM Lead` đó.

## `ADR-P3-12` — Tái sử dụng `CRM Task` Cho Nghĩa Vụ `FOLLOWUP` & Bất Biến Dời Lịch Trước/Sau Quá Hạn
- **Bối cảnh:** P2 đã chuẩn hóa tối đa 01 callback `CRM Task` mở cho mỗi Work Item (`services/followup.py`). Tạo thêm DocType Task thứ hai sẽ gây lệch trạng thái với màn hình tác nghiệp P2.
- **Quyết định:** Liên kết trực tiếp `CSKH SLA Obligation (type='FOLLOWUP')` với `CRM Task.name`. Dời lịch trước hạn chuyển nghĩa vụ cũ sang `superseded` và tạo nghĩa vụ mới; dời lịch sau khi đã quá hạn giữ nguyên bản ghi vi phạm cũ và chỉ tạo nghĩa vụ theo dõi cho mốc hẹn mới.

## `ADR-P3-13` — Xử lý Đính Chính Kỳ Cũ Bằng Phiên Bản `restated` Có Nguồn Gốc (`Provenance`)
- **Bối cảnh:** `CSKH Attempt Correction` có thể được duyệt sau khi kỳ ngày/tháng trước đã chốt số.
- **Quyết định:** Không khóa cứng việc đính chính hợp lệ và cũng không ghi đè mất lịch sử báo cáo cũ; mỗi đính chính tác động kỳ đã publish sẽ sinh một `CSKH KPI Run` mới có `run_mode = "restated"`, tham chiếu `restated_from_run` và `trigger_reference`, sau đó chuyển con trỏ `is_latest_published` nguyên tử sang run mới.

## `ADR-P3-14` — Kiến trúc Xuất Báo Cáo An Toàn & Bảo Vệ Đường Dẫn `/private/files/...`
- **Bối cảnh:** Kiểm tra `frappe/core/doctype/file/file.py:1003-1029` phát hiện nếu `File.owner == user`, Frappe cho phép tải `/private/files/...` mà không gọi hook `has_permission`, dẫn đến rủi ro người dùng vẫn tải được file cũ sau khi bị thu hồi quyền (`S12`, `OBS-P2-03`).
- **Quyết định:** Lưu file xuất với `is_private = 1`, **`owner = "Administrator"`**, `attached_to_doctype = "CSKH Export Job"`, `attached_to_name = job.name`. Nhờ đó cả endpoint `download_export` lẫn đường dẫn `/private/files/...` đều bắt buộc đi qua hàm kiểm tra quyền sống (`live role + live hierarchy + status == 'completed' + now < expires_at`) tại thời điểm tải, kết hợp tiền tố `'` chống CSV formula injection.

## `ADR-P3-15` — Chiến lược Chỉ Mục, Đối Soát Bất Biến & Rollback An Toàn Bằng Feature Flag
- **Bối cảnh:** Hệ thống cần vận hành mượt ở quy mô `10k`–`100k` Work Items và có khả năng tắt nhanh tính năng P3 nếu phát sinh sự cố mà không làm gián đoạn luồng tác nghiệp P0–P2.
- **Quyết định:**
  1. Thiết lập chỉ mục composite định hướng truy vấn trên `(period_type, period_key, scope_type, scope_id, is_latest_published)` của `CSKH KPI Snapshot`, `(status, effective_due_at)` và `(work_item, obligation_type, status)` của `CSKH SLA Obligation`, `(work_item, attempt_at)` của `CSKH Contact Attempt`, `(to_user, ownership_start, ownership_end)` của `CSKH Assignment History`.
  2. Mọi tiến trình recompute/backfill chạy bộ đối soát bất biến trước khi bật `is_latest_published = 1` (nếu lệch -> raise `DATA_RECONCILIATION_FAILED`, giữ nguyên snapshot tốt trước đó).
  3. Cung cấp cờ `p3_sla_enabled` và `p3_reporting_enabled` (cùng `qa_enabled = 0`) để có thể tắt tức thời việc sinh nghĩa vụ/recompute P3 mà không cần xóa bảng dữ liệu hay ảnh hưởng tới P0–P2.
