# ADR-P1-03 — Import: wrapper riêng quanh Frappe File + stdlib CSV/openpyxl (không dùng Data Import native)

> **Dự án:** CRM-CSKH
> **Tài liệu:** `docs/architecture/adr-p1-03-import-engine.md`
> **Ngày:** 2026-11-02
> **Trạng thái:** **Accepted** (P1 §6.1, §16)
> **Work package:** P1-WP04

---

## 1. Vấn đề

P1 §6.1 yêu cầu POC để quyết định: dùng **native Data Import** của Frappe, hay viết
**wrapper riêng** cho `CSKH Import Batch`.

Điều kiện bắt buộc của P1 §6:
- preview có **chuẩn hóa số** trước khi quyết định,
- phân loại **trùng trong file / trùng CRM / đang active campaign khác / lỗi**,
- gắn **`CSKH Import Batch`** + `idempotency_key` + checksum,
- **private file**, không công khai file gốc,
- worker **chunked**, không giữ HTTP request xuyên suốt,
- reconciliation, retry an toàn.

---

## 2. Đánh giá native Data Import (đọc source upstream)

| Nhu cầu P1 | Data Import native | Kết luận |
|---|---|---|
| Upload CSV/XLSX, map cột | Có (`frappe.core.doctype.data_import`) | ✅ REUSE phần parse/mapping |
| Tạo `CRM Lead` theo schema | Có | ✅ |
| **Chuẩn hóa SĐT trước khi tạo** | Không — validate theo field type `Data` | ❌ phải can thiệp |
| **Phân loại trùng / xung đột claim** | Không — không biết `CSKH Identity Index`/`Active Claim` | ❌ |
| **Gắn `CSKH Import Batch` + idempotency** | Không — mỗi lần import là một Data Import riêng | ❌ |
| **Private file + checksum + retention** | Có `File`, nhưng không có checksum theo batch | ⚠️ phải bổ sung |
| **Reconciliation / retry theo dòng** | Không có khái niệm batch domain | ❌ |
| Số điện thoại mất số 0 đầu do Excel | Không có cảnh báo | ❌ (P1 §9.2 yêu cầu cảnh báo) |

**Kết luận:** native Data Import **không đáp ứng** các yêu cầu bắt buộc (chuẩn hóa, dedupe,
claim conflict, idempotency theo batch, reconciliation). P1 §6.1 cho phép wrapper riêng khi
cần; ở đây là **cần**.

---

## 3. Quyết định

**Viết pipeline riêng cho `CSKH Import Batch`, nhưng TÁI SỬ DỤNG tối đa thư viện có sẵn:**

| Tầng | Dùng gì | Ghi chú |
|---|---|---|
| File lưu trữ | **Frappe `File`** (`is_private=1`) | P1 §6.2 "Upload private file"; `File` của Frappe đã xử lý quyền `private file` |
| Checksum | `hashlib.sha256` trên bytes file | Ghi vào `CSKH Import Batch.checksum` |
| CSV | **stdlib `csv`** + `utf-8-sig` (BOM) | P1 §6.2 yêu cầu UTF-8 có BOM |
| XLSX | **`openpyxl`** (read-only) | P1 §6.2: *"tái sử dụng Frappe File, Data Import helper/`openpyxl`/stdlib `csv`"*; không viết parser XLSX mới |
| `.xls` cũ | **không hỗ trợ** | P1 §6.2 nói rõ: "`.xls` cũ không hỗ trợ trừ khi có lý do rõ ràng" |
| Kiểm tra loại/kích thước | `File.file_size`, `file_extension` | Chặn trước khi đọc |
| Parse chunked | generator theo batch | P1 §6.2 "Parse CSV/XLSX ở worker, theo chunks" |

### 3.1. Vì sao không dùng `frappe.core.doctype.data_import` làm lõi

Data Import tạo Lead theo **schema validation của CRM**, tức nó sẽ gọi `CRM Lead.validate` —
điều đó **đúng** (P1 §5.5: *"Dùng Frappe save/insert + validation đúng cách, không bypass
`ignore_mandatory` hay viết SQL insert thẳng vào Lead"*). Nhưng Data Import **không** có chỗ để
cài chuẩn hóa + dedupe + batch id. Wrapper của ta gọi `frappe.get_doc({...}).insert()` cho từng
dòng hợp lệ ⇒ vẫn đi qua đúng validation của CRM, nhưng **kiểm soát được** phần chuẩn hóa/trùng.

### 3.2. `openpyxl` dependency

P1 §6.1 cho phép dùng `openpyxl`. Ràng buộc bắt buộc: **pin version** và khai báo trong
`apps/crm_cskh/pyproject.toml` (P1 §13.5 "pin dependency versions"). `openpyxl` là dependency
đã có sẵn của Frappe (`frappe` yêu cầu `openpyxl` cho Data Import) ⇒ không thêm dependency
mới vào bộ cài hạ tầng.

### 3.3. Hạn mức (ADR-P1-08)

- file ≤ **10 MB**
- ≤ **50.000 dòng**
- cấu hình được qua `CSKH App Settings` (Single DocType sẵn có của P0)

---

## 4. Luồng (P1 §6.2)

```
Upload private file (Frappe File, is_private=1)
  → checksum + size/type validation
  → Parse streaming/chunked (csv | openpyxl read-only)
  → Header mapping + row classification
  → Normalization + duplicates snapshot
  → Preview + explicit confirm
  → Background job với idempotency_key
  → Recheck từng dòng dưới transaction + unique index
  → Create/attach canonical Lead
  → Persist totals + errors + batch status
  → Show result / retry failed rows safely
```

### 4.1. Status machine (P1 §6.2)

`DRY_RUN → CONFIRMED → QUEUED → RUNNING → {COMPLETED | COMPLETED_WITH_ERRORS | FAILED}`

Chuyển trạng thái được **validate** trong `p1_models.py::ImportBatch` (transition table),
không cho nhảy cóc.

### 4.2. Idempotency (P1 §6.4)

- `CSKH Import Batch.idempotency_key` **UNIQUE** ở DB.
- Cùng key + cùng checksum/mapping ⇒ trả **cùng batch/result**, không chạy lại job.
- Cùng key + checksum khác ⇒ `CONFLICT`.
- Cùng file ở đợt mới (key khác) ⇒ batch mới hợp lệ; canonical Lead vẫn không trùng.
- Retry sau crash ⇒ recheck row + unique identity, **reconcile count từ DB** (không cộng dồn event).

---

## 5. Hệ quả

- **Tích cực:** đáp ứng đủ yêu cầu bắt buộc; không sửa core; không viết parser XLSX.
- **Tiêu cực:** phải tự quản lý chunking, error row, retry — nhiều code hơn native.
- **Rủi ro:** phải bảo đảm **không** đọc file không kiểm soát (path traversal) ⇒ chỉ đọc qua
  `frappe.get_doc("File", ...).get_content()` với `is_private` đã kiểm tra.
- **CSV formula injection (SEC-08):** khi xuất file lỗi, escape cell bắt đầu bằng `= + - @`
  bằng `'` prefix. Áp dụng cả khi preview trả về text.

---

## 6. Điều kiện chấp nhận

1. CSV chỉ cột SĐT, có `0…` / `84…` / `+84…` tương đương ⇒ **một** Lead canonical, tổng nhập đúng (T1-01).
2. XLSX có cột numeric / text / ô trống / dòng trùng ⇒ **không** tạo số từ scientific notation
   hỏng; thống kê lỗi/trùng đúng (T1-02).
3. Upload lại cùng key + cùng file ⇒ không tạo Lead/Batch/History trùng (T1-03).
4. Upload cùng file ở đợt khác ⇒ batch mới, canonical Lead không nhân đôi (T1-04).
5. 2 và 5 import job đồng thời cùng SĐT ⇒ DB unique thắng, reconcile đúng (T1-05, ADR-P1-01 §5).
6. Import trùng Lead có owner ⇒ **không** đổi owner/status/ghi chú (T1-06).
7. File ≥ 10 MB hoặc > 50.000 dòng ⇒ từ chối với mã lỗi ổn định.

---

## 7. Nguồn

- P1 §6.1, §6.2, §6.3, §6.4, §6.5, §9.2, §12.2 (T1-01..T1-06), §13.5, §16
- Upstream: `frappe/core/doctype/data_import/`, `frappe/model/document.py` (`Document.insert`)
- `docs/architecture/adr-p1-01-identity-index.md`
