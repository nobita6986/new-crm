# G18 distribution worker: chẩn đoán SQL trong site cô lập

## Mục đích và giới hạn

Tài liệu này mô tả phép đo chẩn đoán một job phân phối giả lập 10.000 Lead. Recorder đếm truy vấn và thời gian, rồi ghi dấu vân tay SHA-256 của câu SQL đã chuẩn hóa. Recorder không lưu SQL thô, tham số, tên Lead, số điện thoại hay run ID nguyên văn.

Phép đo có thêm overhead trong worker. **Không dùng thời gian chẩn đoán để chấm G18 hoặc so với SLO.** Sau khi xác định nút thắt và có thay đổi được kiểm thử, phải chạy lại benchmark nghiệm thu nguyên trạng trên đúng candidate digest và cùng SLO đã duyệt.

Chỉ chạy trên site dùng riêng cho benchmark, đúng `p4-s1-bench.localhost`, với campaign giả lập `S1-CAMP-DIST-10K`. Tạo image chẩn đoán dùng một lần từ đúng candidate làm nền và nhánh có recorder; ghi riêng digest của image này. Đây không phải candidate nghiệm thu hoặc phát hành. Không bật các biến chẩn đoán trên VPS live, staging dùng chung dữ liệu, hoặc worker nhận job nghiệp vụ.

## Chuẩn bị một run mới

1. Khởi tạo site benchmark cô lập và nạp fixture S1 theo workflow P4 đã khóa. Xác minh project/network/volume tách biệt và candidate digest trước khi tạo run.
2. Tạo preview và run trên site đó, nhưng không enqueue worker thường:

   ```python
   from crm_cskh.services import distribution

   preview = distribution.preview(
       campaign_name,
       actor="Administrator",
       idempotency_key="g18-diag-preview-<unique-token>",
   )
   assert int((preview.get("summary") or {}).get("eligible_count") or 0) == 10_000

   queued = distribution.enqueue_run(
       preview["preview_id"],
       expected_rule_version=preview.get("rule_version"),
       idempotency_key="g18-diag-run-<unique-token>",
       actor="Administrator",
       enqueue_job=False,
   )
   run_id = queued["run_id"]
   ```

3. Truyền đúng `run_id` vào một queue worker tạm thời duy nhất. Bật các biến môi trường **chỉ** trong worker chẩn đoán trên site cô lập:

   ```text
   CSKH_P4_G18_DISTRIBUTION_DIAGNOSTICS=1
   CSKH_P4_G18_DISTRIBUTION_DIAGNOSTIC_SITE=p4-s1-bench.localhost
   CSKH_P4_G18_DISTRIBUTION_DIAGNOSTIC_RUN_ID=<run_id chính xác vừa tạo>
   ```

4. Enqueue callable nội bộ `crm_cskh.jobs.distribution_diagnostic_worker.run_distribution_with_diagnostics` với `run_id`, `actor="Administrator"`, queue `long` và job ID riêng `cskh-dist-diag-<run_id>`. Không gọi endpoint công khai.

Worker từ chối chạy nếu cờ, site, run ID, actor, campaign, trạng thái mới, số lượng 10.000 hoặc metadata không khớp; worker cũng từ chối nếu RQ đã có job phân phối thường `cskh-dist-<run_id>`. Nếu điều kiện không đạt, bỏ run và tạo run mới với idempotency key duy nhất trên site cô lập.

## Thu thập và rà artifact

- Thu các dòng log có tiền tố `CSKH_P4_G18_DIST_SQL_DIAG` từ worker benchmark.
- Chỉ giữ `run_id_sha256`, tiến độ, thời gian đã trôi qua, tổng số/thời gian SQL, số lần/thời gian lớn nhất theo fingerprint và bộ đếm overflow.
- Kiểm tra artifact không chứa SQL, giá trị bind, tên Lead, số điện thoại, cookie, SID, CSRF token hoặc run ID nguyên văn.
- So chênh lệch giữa các checkpoint để tìm lô chậm; dùng fingerprint để nhóm truy vấn lặp. Không khôi phục câu SQL bằng cách dò dữ liệu nhạy cảm.
- Tắt và xóa các biến chẩn đoán khỏi worker tạm sau run; hủy site/volume benchmark theo quy trình dọn dẹp cô lập.

## Tiêu chí nghiệm thu

Recorder chỉ cung cấp dữ liệu nguyên nhân. Nó không đóng G18, không sửa ngưỡng SLO và không thay thế ma trận 35 quy tắc. Mọi tối ưu nghiệp vụ tiếp theo phải giữ nguyên quyền, validation, savepoint, idempotency, retry, checkpoint và side effect; sau đó chạy suite hồi quy đầy đủ và benchmark không bật recorder trên cùng profile SLO đã duyệt.
