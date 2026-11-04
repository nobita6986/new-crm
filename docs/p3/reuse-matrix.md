# P3 Reuse Matrix (`REUSE / EXTEND / BUILD NEW`)

Tài liệu này xác lập ranh giới tái sử dụng so với Frappe `v15.123.1` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`), Frappe CRM `v1.87.0` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) và `crm_cskh` P0–P2 (`449fbc26374c3540b07ad86219fa0f33fc84feba`). Nguyên tắc bất biến: **0 sửa đổi mã nguồn upstream**, **0 tạo mô hình trùng lặp** với Lead, User, Role, Call Log, Task, Note hoặc Ownership interval đã có.

---

## 1. Ma trận quyết định thành phần

| Nhóm chức năng | Thành phần có sẵn (Upstream / P0–P2) | Nguồn kiểm chứng (`file:line`) | Quyết định | Lý do kỹ thuật & cách tích hợp trong P3 |
|---|---|---|---|---|
| Hồ sơ khách hàng & cờ DNC | `CRM Lead` + custom fields `cskh_*` (`cskh_do_not_contact`, v.v.) | `crm_cskh/p1_models.py`, `p2_models.py` | **REUSE** | Giữ `CRM Lead` là hồ sơ gốc duy nhất; SLA/KPI liên kết qua `CSKH Work Item.lead`. |
| Đơn vị công việc CSKH | `CSKH Work Item` | `cskh_work_item.json`, `p1_models.py`, `p2_models.py` | **EXTEND (nhẹ)** | Giữ nguyên state machine P1/P2; chỉ đọc/cập nhật projection chỉ số SLA tóm tắt khi cần cho vận hành, không phá vỡ optimistic lock/state P2. |
| Khoảng trách nhiệm nhân viên | `CSKH Assignment History` + `services/ownership.py` | `crm_cskh/services/ownership.py:118-560`, `cskh_assignment_history.json` | **EXTEND** | Tái sử dụng khoảng `[ownership_start, ownership_end)` bất biến cho `A -> B -> A`; bổ sung `team_at_assignment` và `attribution_status` (`authoritative` \| `historical_attribution_unknown`) ngay khi mở interval. |
| Cây tổ chức & lịch sử thuộc team | `CRM Sales Hierarchy` (`NestedSet`) | `crm/fcrm/doctype/crm_sales_hierarchy/crm_sales_hierarchy.py:8-56` | **REUSE + BUILD NEW (`CSKH Team Membership History`)** | Giữ `CRM Sales Hierarchy` cho phân quyền cây hiện tại (ADR-004); bổ sung `CSKH Team Membership History` (append-only) qua `doc_events` để lưu lịch sử thành viên team theo thời gian thực mà không hồi tính sai lịch sử. |
| Sự kiện liên hệ & đính chính | `CSKH Contact Attempt`, `CSKH Attempt Correction`, `CRM Call Log` | `crm_cskh/services/contact_attempt.py`, `correction.py`, `read_model.py:96-256` | **REUSE** | KPI chỉ đếm trên effective `CSKH Contact Attempt` (đã áp `CSKH Attempt Correction` được duyệt). Không bao giờ cộng trùng `CRM Call Log`. |
| Lịch hẹn gọi lại (Callback) | `CRM Task` (`cskh_is_followup=1`, `cskh_reschedule_count`, `cskh_task_history_json`) | `crm_cskh/services/followup.py:1-620` | **REUSE** | Tái sử dụng `CRM Task` 100%; **không tạo DocType callback Task thứ hai**. Nghĩa vụ `FOLLOWUP` tham chiếu trực tiếp `CRM Task.name`. |
| Danh sách ngày nghỉ lễ | `CRM Holiday List` & `CRM Holiday` | `crm/fcrm/doctype/crm_holiday_list/crm_holiday_list.py:12-88` | **REUSE (kèm guard quyền)** | Cấu trúc bảng ngày nghỉ của `CRM Holiday List` sạch và phù hợp để quản trị ngày lễ VN; khóa quyền ghi của `Sales User`/`Sales Manager` không thuộc `CSKH CRM Admin` qua `doc_events` + `has_permission`, đồng thời chụp snapshot danh sách ngày lễ vào từng `CSKH SLA Obligation`. |
| Bộ tính SLA của Frappe CRM | `CRM Service Level Agreement` & `CRM Service Day` | `crm/fcrm/doctype/crm_service_level_agreement/crm_service_level_agreement.py:63-69,240-242,263-293,318-321` | **KHÔNG DÙNG TRỰC TIẾP -> BUILD NEW (`services/calendar.py`, `CSKH Working Calendar`, `CSKH SLA Obligation`)** | Upstream khóa cứng `apply_on` ở `CRM Lead`/`CRM Deal`, chỉ cho 1 ca/thứ (không có giờ nghỉ trưa/ca qua đêm), `calc_elapsed_time` lặp từng giây (`O(seconds)`), và `calc_time` giữ nguyên giờ khi nhảy qua ngày nghỉ lễ/cuối tuần. P3 xây hàm thuần `O(days/shifts)` + `CSKH Working Calendar` đọc ngày lễ từ `CRM Holiday List`. |
| Chính sách nghiệp vụ theo phạm vi | `CSKH Policy` (`global` / `team` / `campaign`) | `crm_cskh/services/policies.py:1-190`, `cskh_policy.json` | **EXTEND** | Bổ sung các tham số SLA & trọng số KPI (`DEV / configurable`) và cơ chế `p3_sla_enabled`, `qa_enabled=0` trên cùng cơ chế phân giải `campaign -> team -> global` của P2. |
| Dashboard mặc định của Frappe CRM | `crm.api.dashboard` (`CRM Dashboard`) | `crm/crm/api/dashboard.py:25-140` | **GIỮ GUARD P0/P1 + BUILD NEW (`api/p3_reporting.py`, `CSKH KPI Run`, `CSKH KPI Snapshot`)** | `crm.api.dashboard` tính trực tiếp trên `CRM Lead.lead_owner` hiện tại và phân quyền theo `Sales Manager`/`Sales User`, không hỗ trợ interval attribution, SLA giờ làm việc hay BOD `0` PII. Giữ nguyên wrapper bảo vệ `guarded_reporting.py` và xây API báo cáo/drill-down/export riêng cho P3 trên bảng snapshot. |
| Hạ tầng hàng đợi nền & khóa phân tán | Frappe RQ (`frappe.enqueue`), `frappe.db.after_commit`, Redis cache/lock | `crm_cskh/services/distribution.py`, `import_pipeline.py` | **REUSE** | Tái sử dụng RQ worker (`short`/`default`/`long`) cho đánh giá SLA định kỳ, recompute KPI bất đồng bộ hậu commit, backfill có checkpoint và xuất CSV nền. |
| Lưu trữ file xuất dữ liệu | Frappe `File` (`/private/files/...`) + `CSKH Export Job` | `frappe/core/doctype/file/file.py:891-1031`, `crm_cskh/permissions.py:123-135` | **REUSE (kèm `owner="Administrator"` + `CSKH Export Job`)** | Lưu file riêng tư với `owner="Administrator"` gắn vào `CSKH Export Job` để bắt buộc mọi truy cập (kể cả URL `/private/files/...` gốc) đi qua kiểm tra quyền sống, TTL và audit tải xuống. |
| Khung phân quyền & chuẩn hóa lỗi HTTP | `p1_permissions.py`, `p2_permissions.py`, `p1_errors.py`, `services/errors.py` | `crm_cskh/p1_errors.py:1-240`, `crm_cskh/services/errors.py` | **EXTEND** | Mở rộng danh mục mã lỗi P3 trên cùng pipeline chuẩn hóa JSON + ẩn PII của P1/P2; giữ nguyên 404 anti-oracle cho `File`/resource ngoài phạm vi. |

---

## 2. Danh mục DocType mới trong `crm_cskh` (Phase P3)

Chỉ bổ sung đúng các DocType chuyên biệt chưa tồn tại trong upstream và P0–P2:

1. **`CSKH Team Membership History`** — Lịch sử khoảng thời gian nhân sự thuộc node `CRM Sales Hierarchy` (append-only, phục vụ `team-as-of` xác thực).
2. **`CSKH Working Calendar`** (+ bảng con **`CSKH Working Shift`**) — Lịch ca làm việc hỗ trợ nhiều khung giờ/ngày (nghỉ trưa), ca qua đêm, múi giờ `Asia/Ho_Chi_Minh`, liên kết trực tiếp `CRM Holiday List` của upstream.
3. **`CSKH SLA Obligation`** (+ nhật ký sự kiện chuyển trạng thái/waiver/pause) — Nghĩa vụ SLA cụ thể gắn với `(work_item, interval_id, task_id, obligation_type)`, giữ `original_due_at` bất biến và snapshot policy/calendar.
4. **`CSKH KPI Run`** — Phiên tính toán/recompute/backfill/restatement bất biến theo kỳ, lưu `source_watermark`, `as_of`, `metric_version`, `restated_from_run`.
5. **`CSKH KPI Snapshot`** — Bản ghi chỉ số đã tính sẵn theo `(run_id, period_type, period_key, scope_type, scope_id, metric_code)` kèm tử số, mẫu số, trạng thái đủ dữ liệu và cờ `is_latest_published`.
6. **`CSKH Dirty Scope`** — Hàng đợi đánh dấu phạm vi/kỳ cần tính lại sau khi giao dịch nguồn commit thành công.
7. **`CSKH QA Review`** — Schema sẵn sàng cho tương lai nhưng mặc định tắt (`qa_enabled=0`), không tạo dữ liệu giả, khóa chặt anti-self-review ở tầng backend nếu được bật thử nghiệm.
8. **`CSKH Export Job`** — Quản lý vòng đời xuất báo cáo/drill-down bất đồng bộ có kiểm tra lại quyền 3 chặng (`enqueue`, `execute`, `download`), TTL hết hạn và chống CSV formula injection.
