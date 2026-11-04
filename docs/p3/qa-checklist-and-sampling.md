# Đặc Tả Module Kiểm Định Chất Lượng QA (`qa_enabled = false` Mặc Định) (`docs/p3/qa-checklist-and-sampling.md`)

---

## 1. Trạng thái Vận hành Mặc định trong Phase P3

Theo đúng chỉ đạo của T0 và `docs/root-plan/P3.md` §7:
- **Cờ cấu hình mặc định:** `CSKH Policy.qa_enabled = 0` (`false`) trên toàn bộ chính sách mặc định và dữ liệu seed DEV.
- **Cam kết trung thực dữ liệu (`Zero Fake Data`):**
  1. Hệ thống **không tự động sinh** bất kỳ bản ghi `CSKH QA Review` giả nào trong seed mặc định hay trong luồng `record_attempt`.
  2. Mọi `CSKH Contact Attempt` do nhân viên ghi nhận khi `qa_enabled = false` đều giữ trạng thái xác thực trung thực `verification_state = "self_reported"` (`verified_count = 0`).
  3. Trên API báo cáo và thẻ điểm, `QA_PASS_RATE` trả `status = "disabled"`, `value = null`; `PROCESS_SCORE` tự động loại trọng số `15` của QA khỏi mẫu số và trả `status = "provisional"` (xem Golden Example `7350 / 85 = 86.47` tại `docs/p3/kpi-dictionary.md`).

---

## 2. Thiết kế Sẵn Sàng Cho Tương Lai (`Future-Ready Schema & Guardrails`)

Để đảm bảo khi doanh nghiệp sẵn sàng vận hành QA ở giai đoạn sau không phải sửa lại cấu trúc bảng hay API báo cáo, P3 định nghĩa sẵn DocType `CSKH QA Review` và endpoint bảo vệ `crm_cskh.api.qa.*` với các bất biến cứng ngay từ đầu:

### 2.1 Hành vi khi `qa_enabled == false`
- Các API ghi QA (`create_qa_sample`, `submit_qa_review`) từ chối ngay với mã lỗi chuẩn `QA_DISABLED` (`HTTP 409` / `PolicyConflict`), thông báo rõ *"Chức năng chấm điểm QA hiện đang tắt theo chính sách (qa_enabled=false)."*
- API đọc danh sách QA (`list_qa_reviews`) trả danh sách rỗng kèm metadata `{"qa_enabled": false, "status": "disabled"}`.

### 2.2 Các bất biến bắt buộc khi bật thử nghiệm `qa_enabled == true` (trong bộ kiểm thử bảo mật/tích hợp)
1. **Chống tự chấm (`Anti-Self-Review` — `S06`):**
   - Nếu `reviewer == reviewed_staff` (kể cả người đó kiêm vai trò Leader/Admin): backend từ chối cứng với mã lỗi `QA_CONFLICT` (`HTTP 403` / `409`).
2. **Phạm vi thẩm quyền (`Scope Enforcement`):**
   - `CSKH Staff`: Không có quyền tạo/sửa/duyệt `CSKH QA Review`; chỉ được xem kết quả review `published` của chính mình (không lộ ghi chú nội bộ nhạy cảm của reviewer nếu đánh dấu `internal_note`).
   - `CSKH Team Leader`: Chỉ được review các attempt thuộc nhân sự trong cây team được phép quản lý và khác bản thân mình.
   - `CSKH BOD Viewer`: Chỉ đọc tổng hợp (`0` quyền ghi, `0` PII khách hàng).
   - `CSKH CRM Admin`: Quản trị cấu hình rubric và xử lý khiếu nại (`disputed`), vẫn chịu ràng buộc `reviewer != reviewed_staff`.
3. **Liên kết sự kiện xác thực:**
   - Mỗi `CSKH QA Review` phải tham chiếu một `CSKH Contact Attempt` hợp lệ (`effective_status != 'voided'`), lưu `rubric_version`, bảng điểm chi tiết và cập nhật dirty scope hậu commit để tính lại `QA_PASS_RATE` khi đạt `min_qa_sample_size`.
