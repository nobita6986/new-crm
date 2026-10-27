# T1A — Xác minh cuối P0 trên DEV (2026-10-27)

**Checkout:** PR #2 review worktree, HEAD `0301264bfcd27460ce9511a365b956693a789ede` + working tree chưa commit.

**Dữ liệu:** toàn bộ bản ghi kiểm thử là dữ liệu tổng hợp giả; workload đã cleanup. Báo cáo này phân biệt runtime local working tree với CI đã chạy trên commit.

## G0-01 — baseline DEV

- Site sạch: `p0-final-verify-20261027.localhost`; Frappe `15.123.1`, CRM `1.87.0`, `crm_cskh 0.0.1 (working tree)`.
- MariaDB `11.8.9-MariaDB-ubu2404`; server/site/table dùng `utf8mb4_unicode_ci`; charset server `utf8mb4`.
- Tạo 5.000 CRM Lead giả qua Frappe ORM: 0 lỗi; 476,94 giây; cleanup: 0 hàng còn lại. `STRCMP('Khach hang','Khách hàng')=0`.
- `mobile_no` chưa có index; EXPLAIN: `type=ALL`, `possible_keys=NULL`, `key=NULL`, ước lượng 5.036 dòng. Không thay schema/index. Ghi nhận là follow-up hiệu năng P1, không cản đóng baseline runtime P0.
- Quyết định phạm vi: đóng G0-01 cho đúng baseline DEV đã thực chạy; không tuyên bố MariaDB 11.8 đã được kiểm thử tổng quát hoặc production-ready. Hướng dẫn chính thức Frappe nêu MariaDB 10.6.6+ cho v14/v15, còn runtime pin cảnh báo >10.8 chưa được kiểm thử rộng.

## G0-07 — kiểm thử local, chờ CI/T1C

Site test sạch, HEAD `0301264` cộng working tree hiện hành:

| Suite | PASS |
|---|---:|
| `test_permissions` | 18 |
| `test_security_remediation` | 12 |
| `test_security_remediation_v2` | 32 |
| `test_bt1c5_matrix_gaps` | 27 |
| `test_docshare_lifecycle` | 10 |
| **Tổng** | **99/99** |

JUnit parse độc lập: 99 PASS, 0 FAIL, 0 ERROR, 0 SKIP; gate JSON `expected=99`, `observed=99`, `passing=99`, `gate_passed=true`. HTTP transport probe local: 18/18 PASS, `failed_cases=[]`; direct-to-`bench serve`, không phủ reverse-proxy/CORS. Private `file_url` bị loại khỏi cả bốn điểm payload được probe; case download không gửi request khi URL đã bị redaction.

Đây là runtime trên working tree, chưa phải CI. CI mới nhất hiện có `#40285825943` chạy trên commit `0301264` trước các chỉnh sửa chưa commit: 91 PASS / 8 FAIL / 0 ERROR. Cần CI mới sau khi đưa thay đổi vào PR, và T1C review độc lập trước khi ký G0-07. Không nâng SEC tally hoặc permission matrix trong báo cáo này.

SHA-256 artifact local:

- Gate JSON: `80c9b6cc928d1c827b7d64591468911d93dc29be94002594716f20381b5cea07`
- HTTP probe rerun: `1f639b661921a5dd7dcd4d4e10327e81344179d79bf7f33feec7642044cc09f3`
- Workload summary: `94571ee3548a061c6ab72ee0df1ede98dfd7843ae6b511e73390010ae0524063`

## G0-02 — DEV/CSRF

Docker bench/DB/Redis đang chạy; DB healthy. `GET /crm/dashboard` qua Vite `127.0.0.1:8080` trả HTTP 200. Các vòng trước đã xác minh SPA mount, CSS HMR và CSRF round-trip; kiểm tra thật nút `/cskh` và console browser chưa hoàn tất. Browser control hiện không khả dụng trong phiên này, nên không ghi ảnh hoặc tuyên bố pass trực quan. G0-02 giữ PARTIAL.

## G0-09 — bản dịch tiếng Việt

`vi.csv`: 196 khóa duy nhất, 0 trùng, 0 bản dịch rỗng. Các mục đã có bản dịch gồm Lead, Note, Assigned To, Lead Owner, Phone, Mobile No., Deal/Deals, Call Logs, Last Modified, `stage`, `source`, cùng các thông báo CSKH. Patcher CRM/Frappe/frappe-ui ghim revision; CRM frontend build đã thành công sau patch. Ảnh checklist vẫn 1/10 và chưa chạy PII sweep toàn bộ ảnh sau patch. G0-09 giữ PARTIAL; không tuyên bố đã rà hết mọi nhãn trên mọi màn hình.

## Trạng thái tổng

| Gate | Trạng thái sau xác minh |
|---|---|
| G0-01 | **PASS — baseline DEV đã chạy**; caveat rộng/prod và full scan là follow-up |
| G0-02 | **PARTIAL** — thiếu thao tác browser/console cuối |
| G0-07 | **PARTIAL / CHƯA KÝ** — local 99/99, chờ CI mới + T1C review |
| G0-09 | **PARTIAL** — bản dịch tăng lên 196; thiếu screenshot/PII sweep |
| G0-10 | **PARTIAL** — build local đạt, CI mới chưa chạy trên thay đổi hiện hành |
| P0 | **NO-GO cho P1** |

Không stage/commit/push/merge trong bước đo lường này.

## CI follow-up — run #40479841377 (2026-10-27)

Sau khi commit `12d4229888fc36b002451339ebaca5c57dea9b9a` được đẩy lên PR #2, workflow `p0` hoàn tất `success` trên synthetic merge commit `8c2fde0ec3953afe8b86338c6957e60d6bf8b135`. GitHub API xác nhận merge commit có base `b382fe425b3f1f6c2037cd3bed20e65e5df2d928` và PR head `12d4229888fc36b002451339ebaca5c57dea9b9a` làm hai parent.

Artifact gốc `p0-g007-evidence-40479841377`: ID `12723270292`, 132.748 byte, SHA-256 `2a7d59245b16fc7c72f844e77004726235971a2ce519b7a61dd6a0ab4df9929d`; digest và kích thước khớp GitHub API.

- Gate JSON: `expected_total=99`, `observed_total=99`, `passing_total=99`, `gate_passed=true`; `PASS=99`, không có FAIL/ERROR/SKIP/NOT RUN, method thiếu/trùng/thừa hay lỗi parse.
- Đọc lại độc lập 5 JUnit XML: 27 + 10 + 18 + 12 + 32 = **99 PASS**, 0 FAIL/ERROR/SKIP; tập `(class, method)` khớp gate 99/99, 0 thiếu/thừa và 0 lệch trạng thái.
- HTTP probe: **18/18 PASS**, `failed_cases=[]`. Giới hạn tự khai: `direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered`. Case `sec26-activities-download-in-scope` không gửi request khi URL đã bị wrapper khử; không tính đây là bằng chứng tải file thành công.
- Audit artifact: không phát hiện giá trị cookie, CSRF token, authorization hay password chưa khử; không ghi giá trị nhạy cảm vào báo cáo.
- Workflow gồm clean bootstrap, build/lint, migration, suites, probe, backup/restore, sanitizer và cleanup; tất cả step bắt buộc thành công. Đây là CI evidence tại commit trên, khác với runtime local ở phần trước.

### Trạng thái gate sau CI

| Gate | Trạng thái | Lý do còn mở |
|---|---|---|
| G0-01 | **PASS có giới hạn** | Chỉ baseline DEV MariaDB đã chạy; không suy rộng production. |
| G0-02 | **PARTIAL** | Cần click kiểm tra luồng trên browser và rà console CORS/WebSocket. |
| G0-07 | **PARTIAL / CHƯA KÝ** | CI per-method xanh; T1C cần rà lại matrix/risk theo code mới. Probe direct-to-bench không bao phủ reverse-proxy/CORS. |
| G0-09 | **PARTIAL** | Chưa có đủ 10 ảnh đã rà và PII sweep toàn bộ ảnh sau bản dịch hiện hành. |
| G0-10 | **PASS trong phạm vi workflow PR #2** | Run #40479841377 xác nhận clean bootstrap, test gate, probe, backup/restore và cleanup trên CI. |
| P0 | **NO-GO cho P1** | G0-02, G0-07 và G0-09 vẫn chưa đạt điều kiện nghiệm thu. |

PR #2 vẫn mở, chưa merge. Kết quả này cập nhật trạng thái CI; không tự thay đổi SEC tally, không ký G0-07 và không biến probe transport thành bằng chứng reverse-proxy/CORS.
