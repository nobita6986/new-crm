# Hợp đồng API — P0

Tài liệu mô tả API đang có trong baseline P0, không tuyên bố các API phân phối/reporting P1 đã được triển khai. Căn cứ thực thi: [hooks](../../apps/crm_cskh/crm_cskh/hooks.py), [permissions](../../apps/crm_cskh/crm_cskh/permissions.py), [guarded CRM](../../apps/crm_cskh/crm_cskh/api/guarded_crm.py), [guarded reporting](../../apps/crm_cskh/crm_cskh/api/guarded_reporting.py) và [error normalization](../../apps/crm_cskh/crm_cskh/errors.py).

## Auth, session và CSRF

App cùng site/session với Frappe CRM. RPC dùng `/api/method/<dotted.method>`; REST document kế thừa Frappe. Phiên đăng nhập dùng cookie của site hoặc token API theo cơ chế Frappe. Không ghi password, sid, CSRF token vào log/artifact.

Vite DEV lấy token từ `/desk` trước khi gọi `crm.www.crm.get_context_for_dev`; Guest được chuyển tới `/login` và giữ URL quay lại. `/cskh` là trang Jinja có yêu cầu đọc CRM Lead, không dùng làm bootstrap cho mọi role. POST trong phiên hợp lệ cần `X-Frappe-CSRF-Token`; thiếu/sai token trả 400 `CSRFTokenError` ở baseline đã đo. Không bật `ignore_csrf`.

## Response và lỗi

RPC thành công được Frappe bọc trong `message`. `crm_cskh.api.health.ping` yêu cầu đăng nhập, trả `app`, `status`, `app_version` và chính user đang gọi. Endpoint không trả database credentials, site config hoặc đường dẫn hệ thống.

Các wrapper CRM giữ shape upstream, đồng thời áp quyền và lọc dữ liệu tại server. Ví dụ `get_linked_calls` trả `calls`/`notes`/`tasks`; `get_activities` trả tuple/list năm phần theo CRM đã ghim và có side-channel `docinfo`. Không suy một shape thành công chung cho mọi endpoint Frappe.

Lỗi có HTTP status và `exc_type` theo Frappe. Permission denial thường là 403. Hai đường File trực tiếp `/api/resource/File/<name>` và `frappe.client.get?doctype=File` chuẩn hóa cả existing-out-of-scope và unknown ID về cùng JSON 404 `DoesNotExistError`; không echo identifier, positive control in-scope 200 giữ nguyên. Không áp quy tắc 404 này cho mọi permission denial. Các trường thông báo/traceback khác được app scrub theo hook; timing không nằm trong cam kết bất khả phân biệt.

## Scope, field và URL

- Quyền quyết định ở backend theo actor thực tế. `Administrator`/`System Manager` không thay actor CRM-ADMIN trong evidence matrix.
- Guest và caller ngoài scope bị từ chối trên những route đã kiểm chứng; list ngoài scope có thể trả tập rỗng theo contract upstream.
- Field `permlevel=1` không trả sentinel/value cho non-system trên các surface đã đo; metadata có tên field không đồng nghĩa value bị lộ.
- Các surface `get_attachments`/`get_activities` được override redact private `file_url`; bộ lọc `get_linked_calls` loại private `recording_url` cho non-system. Không suy thành cam kết redaction cho mọi field của mọi endpoint. System Manager giữ contract privileged. Standard File route cho phép URL/nội dung với caller in-scope theo quyết định Owner. Quyền download được kiểm tra riêng; biết URL không tự chứng minh tải file vượt quyền.
- Reporting/export chưa có scope BOD và ownership service chưa triển khai tiếp tục fail-closed theo ADR-004/ADR-005.

## Paging, mutation và versioning

P0 tái sử dụng filter, rows/fields, paging và response upstream trên revision đã khóa. Không chấp nhận doctype/field từ client như bằng chứng được cấp quyền; permission query và record/field checks vẫn phải áp tại server. P0 không đặt một schema paging mới lên mọi RPC.

Wrapper hiện hành kế thừa HTTP-method semantics của upstream; tài liệu này không tạo allowlist method mới. Quy ước cho mutation P1: dùng request ghi phù hợp, validate input/permission phía server, transaction đầy đủ, rollback khi lỗi; service ownership phải có locking, idempotency và audit trước khi mở quyền. Không tự commit trung gian từng bước chuyển chủ từ frontend.

P0 chưa có namespace API version riêng. Breaking changes phải được ghi vào API contract/ADR và có test tương thích hoặc endpoint version mới trước khi client dùng; không tự nâng dependency/upstream để đổi shape API. Toàn bộ API nghiệp vụ P1–P3 nằm ngoài phạm vi tài liệu này.

## Evidence và giới hạn

[P0 exit report](p0-exit-report.md) là nguồn verdict và mốc nghiệm thu; [permission matrix](permission-matrix.md) là nguồn actor/expectation từng ô. CI cung cấp 99 method core, 8 auxiliary File-error tests và các probe riêng; static/helper tests không được cộng vào 99 method.

SEC-13 PASS trong topology P0 đã đo. Thêm reverse-proxy/CORS/production ingress yêu cầu đo lại boundary đó. Dữ liệu thật và production cần nghiệm thu triển khai riêng.
