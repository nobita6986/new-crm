# Danh mục Kiểm thử Nghiệm thu Người dùng Dành cho BOD, Quản trị viên & Trưởng nhóm (`UAT BOD / Leader Checklist — P3`)

> **Dự án:** CRM-CSKH — Giai đoạn P3 (`SLA, KPI, QA & BOD Quality Dashboard`)
> **Tài liệu:** `docs/p3/uat-bod-checklist.md`
> **Chủ sở hữu:** **T1A** (UI `/cskh/*`, Việt hóa & E2E `P3-09`)
> **Đối tượng sử dụng:** Ban Giám đốc (`CSKH BOD Viewer`), Quản trị viên CSKH (`CSKH CRM Admin`), Trưởng nhóm CSKH (`Sales Manager`)
> **Tham chiếu:** [`docs/root-plan/P3.md`](../root-plan/P3.md) (§10.1–§10.5, §10.7–§10.8, §12, §16 `G12..G17`), [`docs/p3/ui-contract-and-flows.md`](./ui-contract-and-flows.md), [`docs/glossary-vi.md`](../glossary-vi.md)

---

## 1. Điều hướng, Phân quyền Phạm vi & Bảo vệ PII (`G12`, `G14`)

- [ ] **UAT-BOD-01 (Quyền truy cập BOD không cần `System Manager` — `S18`):** Tài khoản chỉ mang vai trò `CSKH BOD Viewer` mở được đầy đủ 5 màn quản trị chất lượng (`/cskh/quality-overview`, `/cskh/team-quality`, `/cskh/employee-quality`, `/cskh/campaign-quality`, `/cskh/sla-operations`) nhưng bị chặn `403` trên `/cskh/work-item`, `/cskh/callbacks`, `/cskh/scorecard` và không được sửa chính sách hay miễn trừ trái quyền.
- [ ] **UAT-BOD-02 (Bảo vệ `0` PII thô trên toàn bộ thẻ tổng hợp & drill-down của BOD):** Trên mọi màn hình, hộp truy vết (`drill-down`) và tệp xuất dành cho `BOD` (theo cấu hình mặc định P0–P3), không xuất hiện số điện thoại thô, ghi chú nhạy cảm hay đường dẫn `/private/files/...`.
- [ ] **UAT-BOD-03 (Cô lập phạm vi Trưởng nhóm — `S03`, `S11`):** Tài khoản `Sales Manager` (Đội A) bị chặn `403` trên `/cskh/quality-overview` (tổng quan toàn công ty); trên `/cskh/team-quality`, `/cskh/employee-quality`, `/cskh/campaign-quality`, `/cskh/sla-operations` chỉ nhìn thấy dữ liệu thuộc phạm vi đội được cấp quyền; cố truyền `team=TEAM-B` qua URL/API không mở rộng được dữ liệu.
- [ ] **UAT-BOD-04 (Quy gán lịch sử `team-as-of` khi nhân viên chuyển đội — `F13`, `G08`):** Khi nhân viên chuyển từ Đội A sang Đội B giữa tháng, số liệu lịch sử giai đoạn thuộc Đội A vẫn giữ nguyên ở Đội A theo `team-as-of`, không bị kéo lùi toàn bộ sang Đội B; các bản ghi cũ thiếu chứng cứ hiển thị rõ cảnh báo `Chưa xác định quy gán lịch sử` (`historical_attribution_unknown`).

---

## 2. Giải thích Chỉ số, Công thức, Độ tươi & Tách Ba Trụ cột (`G02`, `G09`, `G10`, `G15`)

- [ ] **UAT-BOD-05 (Đầy đủ 9 thuộc tính giải thích trên từng chỉ số):** Bấm `Công thức & Nguồn` trên bất kỳ thẻ KPI nào đều hiển thị rõ bằng tiếng Việt: **Đơn vị thống kê (`grain`)**, **Tử số / Mẫu số**, **Công thức**, **Kỳ & Cơ sở thời gian (`[from, to_exclusive)` · `Asia/Ho_Chi_Minh`)**, **Phạm vi**, **Số lượng loại trừ (`exclusions`) & miễn trừ (`waivers`)**, **Mốc chốt (`as_of`) / Tính lúc (`calculated_at`) / Watermark nguồn**, **Phiên bản công thức & chính sách**, và **Trạng thái độ tin cậy**.
- [ ] **UAT-BOD-06 (Khác biệt giữa `Đầu việc duy nhất` toàn công ty và `Khoảng phụ trách` nhân viên — `F28`, `G06`):** Trên màn Tổng quan BOD vs Chất lượng nhân viên, khi có ca bàn giao `A → B` hoặc `A → B → A`, tổng `Số lượt nhận phụ trách` theo nhân viên lớn hơn `Số đầu việc được giao duy nhất` toàn công ty và giao diện giải thích rõ đơn vị thống kê để BOD không hiểu nhầm là đếm trùng.
- [ ] **UAT-BOD-07 (Mẫu số bằng `0` hiển thị `N/A`, không bao giờ hiển thị `0%` — `F21`, `G09`):** Khi lọc vào kỳ/chiến dịch/nhân viên chưa có nghĩa vụ đến hạn (`denominator = 0`), ô tỷ lệ hiển thị **`N/A` (`0 / 0 — Chưa có mẫu đủ điều kiện`)**, tuyệt đối không hiển thị `0%` hay `100%`.
- [ ] **UAT-BOD-08 (Chế độ tắt QA `qa_enabled = false` & Điểm Tạm tính `86,47` — `F22`, `G09`, `G10`):**
  - Màn hình hiển thị thông báo rõ ràng rằng kiểm định mẫu QA đang tắt theo cấu hình (`qa_enabled = false`), không tạo phiếu QA giả và không tự biến `Nhân viên tự báo cáo` thành `Đã xác minh`.
  - Chỉ số `Tỷ lệ kiểm định QA đạt chuẩn` hiển thị `N/A (Chưa bật QA)`.
  - Với ca mẫu chuẩn (`First-touch 90%`, `Phủ khách 80%`, `Cập nhật đầy đủ 100%`, `Follow-up 75%`, thiếu mẫu QA), `Điểm tuân thủ quy trình` hiển thị **`86,47 — Tạm tính; thiếu mẫu QA`** (`7.350 / 85`).
- [ ] **UAT-BOD-09 (Không xếp chung điểm `Tạm tính` với `Đã chốt` — `G09`, `G12`):** Trên bảng `/cskh/employee-quality`, các nhân viên có điểm `Đã chốt` (`finalized`), `Tạm tính` (`provisional`), và `Chưa đủ dữ liệu` (`insufficient_data` / `N/A`) được tách nhóm riêng biệt, không trộn chung thứ hạng.
- [ ] **UAT-BOD-10 (Tách riêng Ba Trụ cột & Phân biệt `Nhân viên tự báo cáo` / `Đã xác minh` / `Đang tranh chấp` — `G15`):**
  - Giao diện tách riêng 3 khối: **(1) Tuân thủ quy trình & SLA**, **(2) Hiệu quả kết nối & chuyển đổi**, **(3) Độ tin cậy dữ liệu & QA**.
  - Không xếp hạng nhân viên bằng tổng số cuộc gọi/tin nhắn (`attempts_count`).
  - Hiển thị tách biệt tỷ lệ/số lượng `Đã xác minh`, `Nhân viên tự báo cáo`, `Đang tranh chấp`; khi dữ liệu bị cũ (`stale`), hiển thị cảnh báo `Cần tính lại (Dữ liệu cũ)` và không gắn nhãn thành tích.
- [ ] **UAT-BOD-11 (Ghi chú cấu hình DEV):** Màn hình thể hiện rõ ngưỡng SLA và bộ trọng số chấm điểm hiện tại là cấu hình DEV/có thể điều chỉnh, chưa mặc định là chính sách Production đã ký duyệt.

---

## 3. Truy vết Dữ liệu Gốc (`Drill-down >= 5` Chỉ số), Tính lại (`Recompute`) & Xuất Báo cáo (`G11`, `G13`, `G14`)

- [ ] **UAT-BOD-12 (Drill-down khớp tuyệt đối tử số/mẫu số trên ít nhất 5 chỉ số trọng yếu — `G13`):**
  1. `Tỷ lệ liên hệ lần đầu đúng hạn` (`first_touch_on_time`, ví dụ `80/100`): bấm mở danh sách nghĩa vụ `FIRST_ATTEMPT` đủ điều kiện thấy tổng `100` dòng, lọc tab `Đạt` = `80`, tab `Không đạt` = `20` (phân trang từ server trước `LIMIT/OFFSET`).
  2. `Tỷ lệ chăm sóc đúng lịch hẹn` (`followup_on_time`): truy được các nghĩa vụ `FOLLOWUP` và đối chiếu hạn `CRM Task`.
  3. `Tỷ lệ phủ liên hệ` (`eligible_coverage`): truy được các khoảng phụ trách (`Ownership Interval`) đã liên hệ / chưa liên hệ.
  4. `Tỷ lệ kết nối thành công` (`connected_rate`): truy được các khoảng đã thử có kết nối hai chiều vs chưa kết nối (gửi Zalo/SMS chưa phản hồi nằm ở nhóm chưa kết nối).
  5. `Đầu việc tồn đọng quá hạn` / `Số vi phạm SLA` (`overdue_backlog` / `breach_count`): truy được danh sách nghĩa vụ quá hạn/vi phạm thực tế.
- [ ] **UAT-BOD-13 (Tính lại có kiểm toán & Hiển thị `Đã tính lại kỳ cũ — Restated` — `F08`, `F30`, `G11`):**
  - Tải lại trang nhiều lần (`F5`) không tự sinh `CSKH KPI Run` mới (`F33`).
  - Khi `CRM Admin` bấm `Yêu cầu tính lại` có nhập lý do kiểm toán (hoặc sau khi duyệt hiệu chỉnh `void`/`correct` ở kỳ đã chốt), trạng thái cập nhật minh bạch và lịch sử phiên bản hiển thị nhãn `Đã tính lại kỳ cũ (Có điều chỉnh)` kèm đối chiếu phiên bản cũ/mới.
- [ ] **UAT-BOD-14 (Xuất báo cáo qua Job phân quyền & Chặn tải lại sau khi mất quyền / hết hạn — `S12`, `F36`, `G14`):**
  - Bấm `Xuất báo cáo` tạo tác vụ phía máy chủ, hiển thị trạng thái, số dòng, thời điểm hết hạn `expires_at` và nút tải tệp CSV/XLSX có cột tiếng Việt (các ô bắt đầu bằng `=`, `+`, `-`, `@` đã được trung hòa chống CSV formula injection).
  - Khi quyền phạm vi của người dùng thay đổi/thu hồi hoặc tệp quá hạn `expires_at`, mở lại URL tải cũ (kể cả URL tệp gốc) bị từ chối rõ ràng (`403`/`410`).

---

## 4. Múi giờ & Biên Kỳ Báo cáo (`G16`, `G17`)

- [ ] **UAT-BOD-15 (Biên Ngày / Tuần / Tháng / Quý / Năm & Khác múi giờ trình duyệt):** Khi máy tính người dùng đặt múi giờ khác `Asia/Ho_Chi_Minh` (ví dụ `America/Los_Angeles` `UTC-8`), chọn kỳ Ngày / Tháng / Quý / Năm vẫn gửi đúng khoảng nửa mở `[from, to_exclusive)` theo `Asia/Ho_Chi_Minh` và số liệu `DISTINCT Work Item` cấp tháng/quý khớp với đối soát nguồn (không cộng thô số ngày).
- [ ] **UAT-BOD-16 (Ngôn ngữ Tiếng Việt tự nhiên):** Toàn bộ tiêu đề, thanh lọc, tên chỉ số, công thức, tooltip, trạng thái rỗng, cột xuất tệp và thông báo lỗi đều hiển thị tiếng Việt chuẩn theo [`docs/glossary-vi.md`](../glossary-vi.md).
