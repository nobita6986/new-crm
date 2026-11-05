# Backlog Kiểm thử Migration Staging (`P4` — Cổng `G06`)

- **Phạm vi áp dụng:** Diễn tập migration và kiểm định tương thích schema trên Staging ([`docs/root-plan/P4.md`](../root-plan/P4.md) §6.3, cổng `G06`)
- **Mốc image smoke đang khóa (`P4-01`):** Commit `4de3dc8656b0077cabd4220afc6636da543e34f6` (`4de3dc8`) — `APP_IMAGE=sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` (`crm-cskh:0.0.1-92e14af`)
- **Trạng thái Remote Registry Digest:** **`BLOCKED`** (chưa có Container Registry được duyệt theo `P4-00` [`docs/p4/infra-inventory.md`](./infra-inventory.md) dòng 19)

---

## Mục đang mở

### `BLG-P4-MIG-01` — Sai khác hoa/thường trường `module` của DocType `CSKH Export Job` (`"Crm Cskh"` vs `"CRM CSKH"`)

| Thuộc tính | Chi tiết |
| :--- | :--- |
| **Mã hạng mục** | `BLG-P4-MIG-01` |
| **Nguồn phát hiện** | Kiểm toán site sạch `P4-01` (`T1B`) tại [`docs/p4/p4-01-image-build-and-lock.md`](./p4-01-image-build-and-lock.md) (§6) và [`deploy/images/evidence/clean-site-verify.json`](../../deploy/images/evidence/clean-site-verify.json) (`schema_audit`) |
| **Vị trí mã nguồn** | [`apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_export_job/cskh_export_job.json`](../../apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_export_job/cskh_export_job.json) dòng 171 (`"module": "Crm Cskh"`) so với [`apps/crm_cskh/crm_cskh/modules.txt`](../../apps/crm_cskh/crm_cskh/modules.txt) dòng 1 (`CRM CSKH`) và 22/23 DocType `CSKH *` còn lại (`"module": "CRM CSKH"`) |
| **Mức độ ảnh hưởng hiện tại** | Không chặn `install-app crm_cskh` hay `bench migrate` trên MariaDB 11.8 collation mặc định `utf8mb4_unicode_ci` (`tabCSKH Export Job` vẫn được tạo đầy đủ), nhưng lưu `tabDocType.module = 'Crm Cskh'` tách khỏi `'CRM CSKH'` khi truy vấn phân biệt hoa/thường (`BINARY`) hoặc gom nhóm `dict` trong Python |
| **Quyết định điều phối (`T0`)** | Giữ nguyên mã nguồn `apps/crm_cskh` và giữ nguyên image mốc smoke tại `4de3dc8` (`sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`); đưa vào backlog kiểm thử migration Staging (`G06`). Chỉ mở bản sửa image ở `P4-01` nếu `T1A` tái hiện lỗi thuộc phạm vi image |
| **Trạng thái** | `OPEN — STAGING MIGRATION TEST BACKLOG` |

#### Các bước kiểm chứng bắt buộc khi diễn tập Migration trên Staging (`P4.md §6.3` / `G06`)

1. **Đối chiếu Collation & Tính lặp lại (`Idempotency`) của One-Shot `migrator`:**
   - Xác nhận collation của `tabModule Def.name` và `tabDocType.module` trên MariaDB Staging khớp `utf8mb4_unicode_ci` (không dùng collation `*_bin` gây lỗi khóa ngoại khi đồng bộ `cskh_export_job.json`).
   - Chạy `bench --site <STAGING_SITE> migrate` 2 lần liên tiếp từ container `migrator`; xác nhận thoát `0`, bảng `tabCSKH Export Job` và các index bảo toàn nguyên vẹn.
2. **Truy vấn Đối soát Metadata `tabDocType` Trước/Sau Migration:**
   ```sql
   SELECT CAST(module AS CHAR CHARACTER SET utf8mb4) AS module_exact, COUNT(*) AS doctype_count
   FROM `tabDocType`
   WHERE name LIKE 'CSKH %'
   GROUP BY BINARY module
   ORDER BY doctype_count DESC;
   ```
   - Khi chưa đổi JSON: kết quả đối soát phải khớp chính xác trạng thái đã biết (`CRM CSKH = 22`, `Crm Cskh = 1`), không phát sinh DocType lệch module mới.
3. **Kiểm chứng Luồng Nghiệp vụ & Phân quyền `CSKH Export Job` Sau Migration (`F-P3-05`):**
   - Thực thi tạo tiến trình xuất dữ liệu, chạy worker kết xuất, tải qua endpoint được ủy quyền, kiểm tra job hết hạn theo giờ (`expire_exports`) và xác nhận chặn truy cập trái phép qua `/api/resource/CSKH Export Job`, `/api/resource/File`, `/private/files/...` trên topology Staging.
4. **Tiêu chí Kiểm thử Nếu Chuẩn hóa Sang `"module": "CRM CSKH"` Ở Bản Phát hành Kế tiếp:**
   - Diễn tập trên bản sao dữ liệu Staging đang có sẵn bản ghi `tabCSKH Export Job` và tệp xuất private: sau khi chạy `bench migrate`, `GROUP BY BINARY module` phải hội tụ về duy nhất `CRM CSKH = 23` (`0` dòng `Crm Cskh`), bảo toàn `100%` số dòng `tabCSKH Export Job`, lịch sử kiểm toán và ràng buộc quyền tải file.
