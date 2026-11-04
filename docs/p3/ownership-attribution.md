# Quy Thuộc Trách Nhiệm Lịch Sử (`team-as-of` & Ownership Attribution) (`docs/p3/ownership-attribution.md`)

---

## 1. Bài toán & Nguyên tắc Chống Sai Lệch Lịch Sử

Trong P1–P2:
- `CSKH Assignment History` đã ghi nhận chính xác khoảng thời gian nhân viên sở hữu Work Item (`to_user`, `ownership_start`, `ownership_end`), kể cả chuỗi `Staff A -> Staff B -> Staff A`.
- Tuy nhiên, `CRM Sales Hierarchy` chỉ lưu cây phân cấp **hiện tại** (`user`, `reports_to`, `lft`, `rgt`). Nếu tháng 4 `Staff A` thuộc `Team 1`, sang tháng 5 `Staff A` chuyển sang `Team 2`, việc JOIN `CSKH Assignment History.to_user` với `CRM Sales Hierarchy` hiện tại sẽ làm toàn bộ kết quả tháng 4 của `Team 1` bị chuyển nhầm sang `Team 2`.

**Bất biến bắt buộc của P3:**
1. **Tuyệt đối không hồi tính (`back-calculate`) team hoặc owner quá khứ từ trạng thái hiện tại** của `CRM Lead.lead_owner`, `CSKH Work Item.assigned_to` hay `CRM Sales Hierarchy`.
2. **Dữ liệu cũ thiếu bằng chứng xác thực phải gắn cờ `historical_attribution_unknown`**, có giải thích và bộ đếm loại trừ tường minh (`exclusion_counts.historical_attribution_unknown`), **không bao giờ tự suy đoán**.

---

## 2. Mô hình Dữ liệu Quy Thuộc Có Thẩm Quyền trong P3

### 2.1 `CSKH Team Membership History` (Append-only)
Lưu trữ lịch sử khoảng thời gian một `User` trực thuộc một node quản lý/team trong `CRM Sales Hierarchy`:
- `user` (`Link -> User`, indexed)
- `hierarchy_node` (`Link -> CRM Sales Hierarchy`)
- `team_leader_node` / `parent_node` (`Link -> CRM Sales Hierarchy` — xác định đơn vị Team quản lý trực tiếp)
- `team_key` (`Data`, indexed — mã định danh node Team để group báo cáo ổn định)
- `effective_from` (`Datetime`, indexed)
- `effective_to` (`Datetime | None`, indexed — `NULL` nghĩa là đang có hiệu lực)
- `source_type` (`Select`: `hierarchy_hook | admin_assignment | migration_cutover`)
- `recorded_by` (`Link -> User`)

**Cơ chế tự động cập nhật (`doc_events` trên `CRM Sales Hierarchy`):**
- Khi thêm mới hoặc đổi `reports_to` / `user` trên `CRM Sales Hierarchy`, hook `on_update` đóng bản ghi mở cũ (`effective_to = now`) và mở bản ghi `CSKH Team Membership History` mới (`effective_from = now`, `effective_to = NULL`, `source_type = 'hierarchy_hook'`).
- Khi chạy patch nâng cấp lên P3 (`patches/v3_0/p3_schema_and_seeds.py`), hệ thống chụp trạng thái cây hiện tại thành các dòng `source_type = 'migration_cutover'` với **`effective_from = migration_executed_at`** (chỉ có hiệu lực từ thời điểm nâng cấp trở đi, **không giả mạo `effective_from = 1970-01-01`** cho dữ liệu quá khứ!).

### 2.2 Snapshot trực tiếp trên Sự kiện Giao việc & Nghĩa vụ SLA
1. **`CSKH Assignment History`:** Bổ sung 2 cột:
   - `team_at_assignment` (`Link -> CRM Sales Hierarchy`, nullable)
   - `attribution_status` (`Select`: `authoritative | historical_attribution_unknown`, mặc định `authoritative` khi giải quyết được từ nguồn xác thực tại thời điểm `ownership_start`).
2. **`CSKH SLA Obligation` & Projection Effective Attempt:**
   - Khi mở nghĩa vụ SLA hoặc chiếu attempt sang kỳ báo cáo, hệ thống lấy `team_at_event` theo thứ tự thẩm quyền:
     1. Snapshot `team_at_assignment` trên `CSKH Assignment History` của interval chứa sự kiện (nếu `attribution_status == 'authoritative'`);
     2. Hoặc dòng `CSKH Team Membership History` có `source_type != 'migration_cutover'` (hoặc `ownership_start >= effective_from`) bao phủ thời điểm sự kiện `effective_from <= event_at < COALESCE(effective_to, '9999-12-31')`.
   - Nếu bản ghi lịch sử xảy ra **trước** `migration_executed_at` và không có bất kỳ bằng chứng `team-as-of` xác thực nào tại thời điểm `event_at`:
     - Gán `team_at_event = NULL`, `attribution_status = 'historical_attribution_unknown'`.

---

## 3. Hành vi Báo cáo đối với `historical_attribution_unknown`

| Cấp báo cáo (`scope_type`) | Cách xử lý các bản ghi `historical_attribution_unknown` |
|---|---|
| `employee` (Scorecard cá nhân) | **VẪN TÍNH ĐẦY ĐỦ**, vì `to_user` / `actor` trên `CSKH Assignment History` và `CSKH Contact Attempt` là xác thực 100% từ P1/P2. |
| `company` / `campaign` (Toàn công ty / Chiến dịch) | **VẪN TÍNH ĐẦY ĐỦ** vào tổng công ty và chiến dịch, đồng thời hiển thị dòng phân rã riêng `"Chưa xác định team lịch sử (trước P3)"` (`scope_id = "__unattributed_historical_team__"`) trên bảng so sánh Team của BOD/Admin để tổng các nhóm luôn khớp đối soát. |
| `team` (Báo cáo của một Team cụ thể) | **LOẠI TRỪ** khỏi tử số và mẫu số của bất kỳ Team cụ thể nào (không đoán), đồng thời trả về số lượng bản ghi bị loại trừ trong `exclusion_counts.historical_attribution_unknown` kèm chú thích tiếng Việt rõ ràng. |

---

## 4. Bất biến Bảo mật Quyền Xem Lịch Sử (`S03`, `S15`)

- **Nhân viên cũ (`Former Owner Staff A`):** Sau khi Lead/Work Item đã chuyển sang `Staff B`, `Staff A` xem `get_my_scorecard` vẫn thấy các chỉ số tổng hợp của khoảng thời gian mình từng phụ trách (ví dụ: `1` nghĩa vụ hoàn thành đúng hạn, `2` cuộc gọi), nhưng **tuyệt đối KHÔNG xem được tên khách hàng, số điện thoại, ghi chú hay drill-down PII của khách hàng đã chuyển giao** (`redacted_lead = true`, chỉ hiển thị mã tham chiếu ẩn danh hoặc chặn truy cập hồ sơ gốc).
- **Leader của Team cũ (`Team 1 Leader`):** Khi `Staff A` chuyển sang `Team 2` vào ngày `15/05`:
  - `Team 1 Leader` truy vấn kỳ `01/05–14/05` thấy phần KPI đóng góp cho `Team 1` trong khoảng `Staff A` thuộc `Team 1` (và không thấy hoạt động của `Staff A` tại `Team 2` từ `15/05` trở đi).
  - Mọi dòng drill-down luôn kiểm tra lại quyền truy cập hồ sơ trực tiếp hiện hành trước khi trả bất kỳ trường định danh khách hàng nào; nếu Lead hiện không còn thuộc phạm vi quản lý được phép của `Team 1 Leader`, thông tin khách hàng bị ẩn danh (`0` PII leak).
