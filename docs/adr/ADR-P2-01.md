# ADR-P2-01 — Mở rộng Trạng thái `CSKH Work Item` (`in_progress`, `on_hold`, `revision`)

- **Trạng thái:** Đã chốt (Accepted) — 03/11/2026
- **Tham chiếu kế hoạch:** `docs/root-plan/P2.md` §5, §19 (`ADR-P2-01`); `docs/state-machine.md`

## 1. Bối cảnh & Vấn đề
Trong P1 (`apps/crm_cskh/crm_cskh/p1_models.py:26,143` và `cskh_work_item.json:31`), `CSKH Work Item.status` chỉ có `queued | assigned | closed | revoked`, và nhiều truy vấn (`ownership.py:95,455,549`, `eligibility.py:102,258`, `distribution.py:111,125,989`, `reporting.py:252,368,447,670,1046`) giả định mọi Work Item đang do nhân viên giữ đều có `status == "assigned"`. P2 cần phân biệt rõ khách mới nhận chưa liên hệ (`assigned`), khách đang chăm sóc (`in_progress`), và khách đang chờ lịch hẹn gọi lại (`on_hold`), đồng thời không phá vỡ bất biến 1 `CSKH Active Claim` / Lead của P1.

## 2. Các Phương án Đã Xét
- **Phương án A:** Giữ nguyên `status = "assigned"` cho toàn bộ giai đoạn chăm sóc và thêm một cột phụ `workflow_stage`.
  - *Nhược điểm:* Tách đôi máy trạng thái, dễ sinh tổ hợp bất hợp lệ (`status="closed"` nhưng `workflow_stage="on_hold"`), trái với `P2.md` §5.1.
- **Phương án B (Chọn):** Mở rộng trực tiếp `CSKH Work Item.status` thành `queued | assigned | in_progress | on_hold | closed | revoked`, định nghĩa hai tập hằng ngữ nghĩa rõ ràng (`OWNED_ACTIVE_WORK_ITEM_STATES` và `ACTIVE_WORK_ITEM_STATES`), bổ sung cột `revision` (`Int`, mặc định `1`), và cập nhật chính xác từng điểm truy vấn trong P1.
- **Phương án C:** Tạo DocType `CSKH Workflow State` tách rời `CSKH Work Item`.
  - *Nhược điểm:* Tạo bảng trùng lặp với P1, tốn `JOIN` trên mọi danh sách và tăng nguy cơ lệch khóa.

## 3. Quyết định
Chọn **Phương án B**:
1. `OWNED_ACTIVE_WORK_ITEM_STATES = ("assigned", "in_progress", "on_hold")`: yêu cầu `assigned_to` & `assigned_at`, tính vào `capacity_limit`, được phép `reassign`/`revoke`/`close`, và áp vào mệnh đề cô lập đội nhóm `L-23` (`reporting._team_lead_authorization_clauses`).
2. `ACTIVE_WORK_ITEM_STATES = ("queued", "assigned", "in_progress", "on_hold")`: giữ `CSKH Active Claim` (1 active claim / Lead) và chặn đóng `CSKH Campaign`.
3. Mọi thay đổi trên `CSKH Work Item` tăng `revision += 1` dưới khóa `SELECT ... FOR UPDATE`.

## 4. Tác động Quyền, Kiểm thử & Chi phí Nâng cấp
- **Quyền & P1 Regression:** Toàn bộ `209` test P1, `12` test UI P1 và `99` test P0 tương thích ngược 100% vì item mới giao vẫn bắt đầu ở `"assigned"`.
- **Kiểm thử P2:** Được ghim bởi `T2-01..T2-09`, `T2-18`, `SEC2-03`, `SEC2-10` và hàm đối soát `reconcile_p2_invariants`.
- **Chi phí nâng cấp:** Bằng 0 đối với core Frappe/CRM vì chỉ mở rộng DocType của `crm_cskh`.
