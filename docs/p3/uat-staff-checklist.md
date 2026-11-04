# Danh mục Kiểm thử Nghiệm thu Người dùng Dành cho Nhân viên CSKH (`UAT Staff Checklist — P3`)

> **Dự án:** CRM-CSKH — Giai đoạn P3 (`SLA, KPI, QA & Personal Scorecard`)
> **Tài liệu:** `docs/p3/uat-staff-checklist.md`
> **Chủ sở hữu:** **T1A** (UI `/cskh/*`, Việt hóa & E2E `P3-09`)
> **Đối tượng sử dụng:** Nhân viên CSKH (`Sales User`)
> **Tham chiếu:** [`docs/root-plan/P3.md`](../root-plan/P3.md) (§10.6, §12, §14.4 `S01..S09`, §16 `G12..G17`), [`docs/p3/ui-contract-and-flows.md`](./ui-contract-and-flows.md), [`docs/glossary-vi.md`](../glossary-vi.md)

---

## 1. Điều hướng & Bảng điểm Cá nhân (`/cskh/scorecard` — `G12`, `G17`)

- [ ] **UAT-STAFF-01 (Điều hướng gọn đúng vai trò `Sales User`):** Trên thanh điều hướng `/cskh/*`, Nhân viên thấy đúng các mục tác nghiệp cá nhân (`Khách được giao`, `Lịch hẹn chăm sóc lại`, `Tiến độ CSKH`, `Bảng điểm cá nhân`) và liên kết chuyển nhanh từ `Bảng điểm cá nhân` sang `/cskh/my-customers` & `/cskh/callbacks`.
- [ ] **UAT-STAFF-02 (Hiển thị minh bạch Điểm cá nhân, Tử/Mẫu số & Công thức):**
  - Màn `/cskh/scorecard` hiển thị rõ từng thành phần tạo nên `Điểm tuân thủ quy trình` (`PROCESS_SCORE`): `C1_FIRST_ATTEMPT_SLA` (`FIRST_ATTEMPT_SLA_RATE`), `C2_FOLLOWUP_SLA` (`FOLLOWUP_SLA_RATE`), `C3_DATA_HYGIENE` (`VALID_OUTCOME_LOGGING_RATE`), `C4_CALLBACK_DNC_DISCIPLINE` (`Hoàn tất lịch hẹn & tuân thủ DNC`), `C5_QA_QUALITY` (`QA_PASS_RATE`) kèm phân số `tử / mẫu` và trọng số từng phần.
  - Khi `qa_enabled = false` hoặc chưa đủ mẫu QA, hiển thị rõ nhãn **`Tạm tính; thiếu mẫu QA`** (không tính `QA = 0` làm tụt điểm oan của nhân viên — `F22`, `G09`).
  - Khi nhân viên mới nhận việc chưa có nghĩa vụ nào đến hạn (`denominator = 0`), hiển thị **`N/A — Chưa có mẫu đủ điều kiện`**, không hiển thị `0%` (`F21`).
- [ ] **UAT-STAFF-03 (Tách biệt nghĩa vụ sắp đến hạn / quá hạn & Liên hệ thực tế):**
  - Nhân viên thấy rõ số đầu việc đang phụ trách cần xử lý ngay, nghĩa vụ `FIRST_ATTEMPT` / `FOLLOWUP` sắp đến hạn hoặc đang quá hạn.
  - Màn hình giải thích rõ: gửi tin SMS/Zalo chưa có phản hồi được ghi nhận là **đã thử liên hệ** (`Attempt`), không tính là **đã kết nối** (`Connected`); khách không bắt máy không tự tạo lỗi vi phạm `FIRST_CONNECTED` (`F05`, `G07`).

---

## 2. Phân quyền Chặt chẽ & Thu hồi PII Sau Bàn giao (`S01..S09`, `S12`, `G14`)

- [ ] **UAT-STAFF-04 (Chặn xem dữ liệu đồng nghiệp & màn quản trị — `S01`, `S02`):**
  - Nhân viên mở trực tiếp URL `/cskh/quality-overview`, `/cskh/team-quality`, `/cskh/employee-quality`, `/cskh/campaign-quality`, `/cskh/sla-operations` đều nhận trang từ chối quyền `403` bằng tiếng Việt.
  - Cố gắn `?user=<đồng_nghiệp>` hoặc `?scope=company` trên `/cskh/scorecard` hoặc API báo cáo không bao giờ trả dữ liệu của người khác.
- [ ] **UAT-STAFF-05 (Bảo toàn KPI lịch sử cá nhân nhưng thu hồi sạch PII sau bàn giao `A → B` — `S04`, `G06`, `G08`, `G14`):**
  - Sau khi một `CSKH Work Item` được bàn giao từ `Nhân viên A` sang `Nhân viên B`:
    - `Nhân viên A` vẫn xem được số liệu thống kê tổng hợp về khoảng phụ trách lịch sử của mình trên `/cskh/scorecard`.
    - Khi `Nhân viên A` mở hộp truy vết (`drill-down`) trên `/cskh/scorecard`, các ca đã chuyển sang người khác **không hiển thị tên/số điện thoại/ghi chú PII gốc** của khách hàng; bấm lại URL lịch sử `/cskh/work-item?id=...` bị chặn `[FORBIDDEN]` với `0` rò rỉ dữ liệu.
- [ ] **UAT-STAFF-06 (Chặn tự sửa điểm, hạn SLA hoặc trạng thái xác minh — `S06..S09`):** Nhân viên không có giao diện và bị từ chối `403` ở backend nếu cố gọi API generic để tự sửa điểm KPI, hạn `due_at`, miễn trừ `waived`, hay nâng `self_reported` thành `verified`.
- [ ] **UAT-STAFF-07 (Xuất dữ liệu cá nhân theo quyền & Chặn URL tải cũ sau thu hồi — `S12`):** Nếu chính sách cho phép xuất bảng điểm tổng hợp cá nhân, tệp chỉ chứa chỉ số hợp lệ của chính nhân viên; mọi liên kết tải hết hạn hoặc vượt quyền sau khi đổi vai trò/bàn giao đều bị từ chối ở máy chủ.

---

## 3. Ngôn ngữ Tiếng Việt & Múi giờ (`G16`, `G17`)

- [ ] **UAT-STAFF-08 (Múi giờ chuẩn & Biên kỳ):** Hạn nghĩa vụ SLA, mốc chốt `as_of` và bộ lọc kỳ Ngày / Tuần / Tháng / Quý / Năm trên `/cskh/scorecard` hiển thị thống nhất bằng định dạng tiếng Việt theo múi giờ người dùng/site (`Asia/Ho_Chi_Minh`), không bị lệch ngày khi trình duyệt đặt múi giờ khác.
