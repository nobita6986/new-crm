# ADR-P1-08 — Hạn mức import và retention file gốc

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-08-import-limits.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §16)
> **Work package:** P1-WP04

---

## 1. Quyết định

| Hạng mục | Mặc định | Cấu hình ở |
|---|---|---|
| Kích thước file tối đa | **10 MB** | `CSKH App Settings.max_import_file_bytes` |
| Số dòng tối đa | **50.000** | `CSKH App Settings.max_import_rows` |
| Định dạng | CSV (UTF-8, có BOM) hoặc XLSX | `.xls` **không** hỗ trợ |
| Lưu trữ file gốc | Frappe `File` với `is_private=1` | — |
| Retention file gốc | **90 ngày** sau khi batch `COMPLETED` | `CSKH App Settings.import_file_retention_days` |
| File lỗi (error artifact) | `is_private=1`, retention **30 ngày** | `CSKH App Settings.error_artifact_retention_days` |

---

## 2. Cơ sở

- P1 §16 ADR-P1-08: *"10MB/50k dòng mặc định, private file và retention cấu hình; xem lại sau
  benchmark."*
- P1 §6.2: *"Hạn mức mặc định ban đầu: file **≤ 10 MB**, **≤ 50.000 dòng**, cấu hình được; phải
  benchmark trước khi nâng."*
- P1 §6.2: *"Parse CSV/XLSX ở worker, theo chunks; không tải toàn bộ workbook quá lớn vào RAM."*
- P1 §13.4: *"có đường dự phòng cho migration thất bại/DB unique conflicts; không xóa hoặc
  overwrite Lead/PII lịch sử tự động."*

---

## 3. Ghi nhận "chưa có benchmark"

Mức 10 MB / 50.000 dòng là **mặc định bảo thủ theo đặc tả**, chưa phải kết quả đo. P1 §12.4 yêu
cầu benchmark 10.000 dòng giả; kết quả đo sẽ được ghi vào `P1 Exit Report` và mức hạn mức sẽ
được **đánh giá lại** (tăng/giảm) bằng ADR mới nếu có khác biệt đáng kể. **Không** hứa SLA tốc
độ khi chưa đo (P1 §12.4).

---

## 4. Bảo vệ dữ liệu nhạy cảm

| Rủi ro | Biện pháp |
|---|---|
| File gốc bị đọc bởi user khác | `File.is_private=1`; API chỉ trả `File` cho Admin có quyền import; endpoint tải file **kiểm lại** quyền mỗi lần |
| Số điện thoại lọt vào log/artifact | File lỗi xuất ra **private**; cột SĐT trong file lỗi được **mask** theo policy; log chỉ ghi `batch_id`/`row_number`, không ghi SĐT |
| CSV formula injection khi xuất file lỗi (SEC-08) | Escape ô bắt đầu bằng `= + - @` bằng `'` prefix khi ghi CSV/XLSX |
| Upload file quá lớn / sai định dạng | Kiểm tra **trước** khi đọc: `file_size`, `file_extension` |
| Path traversal | Chỉ đọc nội dung qua `frappe.get_doc("File", name).get_content()`; không dựng path từ tên file |

---

## 5. Điều kiện chấp nhận

1. File > 10 MB ⇒ từ chối với mã lỗi ổn định, **không** ghi file vào disk.
2. File > 50.000 dòng ⇒ từ chối (đếm dòng **streaming**, không nạp hết vào RAM).
3. `.xls` ⇒ từ chối với thông báo rõ (`UNSUPPORTED_FORMAT`).
4. File không `is_private` ⇒ từ chối ở tầng service.
5. File lỗi xuất ra là private và cell công thức được escape.
6. Retention: job dọn file gốc cũ hơn 90 ngày **không** xoá Lead/History/Work Item
   (chỉ xoá `File` và link `error_artifact`).

---

## 6. Nguồn

- P1 §6.2, §6.5, §8.2, §12.3 (SEC-08, SEC-09), §12.4, §13.4, §16
- `docs/architecture/adr-p1-03-import-engine.md`
