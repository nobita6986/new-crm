# T1A — Tiếp tục chốt P0: CI mới nhất, khởi động DEV và bản dịch

**Ngày:** 2026-10-27 (Asia/Bangkok)

**Checkout:** `codex/p0-t1a-g001-g002-g009`, HEAD `e0e0519` trước lượt này.
**Môi trường:** Docker DEV; dữ liệu giả. Không xóa hoặc tạo lại volume/site.

## CI mới nhất — xác minh độc lập

`gh run list --workflow p0.yml --limit 5` xác nhận run mới nhất là [#40481457445](https://github.com/nobita6986/new-crm/actions/runs/40481457445), không phải run #40479841377.

| Trường | Giá trị |
|---|---|
| Head | `e0e0519fe64257829e606049212e2103f973df84` |
| Kết quả | `completed / success` |
| Artifact | `12723162687`, `p0-g007-evidence-40481457445`, 132.845 byte, chưa hết hạn |
| Digest artifact từ GitHub API | `sha256:46201693f8aed9c25ff75819bf520b29ed76ba636211d726c9d1349c10b9cf44` |

Đã tải artifact rồi parse lại: `g007-per-method-gate.json` ghi `expected_total=99`, `observed_total=99`, `PASS=99`, `gate_passed=true`; 5 JUnit XML có tổng 99 `<testcase>`, 0 failure, 0 error, 0 skipped; HTTP probe `PASS`, 18/18 case, 0 failed. Phạm vi probe tự ghi là HTTP trực tiếp tới `bench serve`, **không** bao phủ reverse-proxy/CORS. CI PASS chứng minh các bước và ca kiểm tra có trong workflow ở revision này; không thay thế rà soát độc lập T1C hoặc mở rộng phạm vi probe.

Các SHA-256 của hai JSON trong artifact sau giải nén:

- `g007-per-method-gate.json`: `80c9b6cc928d1c827b7d64591468911d93dc29be94002594716f20381b5cea07`.
- `http-transport.json`: `a600c82ee794eccc81f1ea484a66680c1cb2d281ba5a029f4e4c3debedea46aa`.

## DEV runtime sau khởi động lại

- Docker Desktop đã được bật; `docker compose up -d db redis-cache redis-queue bench` khởi động lại dịch vụ mà không xóa volume. DB `healthy`, ba dịch vụ còn lại `Up`.
- `bench start` và CRM Vite dev server được chạy nền trong container.
- Kiểm tra mới: Frappe `GET /api/method/ping` với `Host: dev.localhost` → HTTP 200; Vite `GET /crm/dashboard` → HTTP 200.
- Patcher `deploy/dev/apply-crm-vite-dev-patch.py` chạy lại thành công và báo các mục đã được áp dụng. Bản vá khởi tạo `window.__` trước khi các import module CRM đánh giá nhãn dịch, đồng thời giữ import `translation` đứng trước import ứng dụng. Đây là bản vá DEV có ghim revision, không sửa upstream remote.
- CRM frontend `yarn build` chạy trong container thành công: Vite 8.2.0, 3.147 modules transformed, build 10,75 giây; PWA assets sinh xong. Có cảnh báo Browserslist cũ, thời gian plugin và bundle lớn; không có lỗi build.
- Browser trước đó đã hiển thị CRM bằng tiếng Việt và HMR; thao tác nút kiểm tra phiên `/cskh` đã xác nhận thông báo phiên hợp lệ. Lượt này không làm mới ảnh hoặc console browser. Lần xem console trước có một số lỗi JSON parse do response bắt đầu bằng HTML; chưa truy nguyên và chưa xác nhận chúng còn tồn tại sau restart.

## Bản dịch

Đếm lại `apps/crm_cskh/crm_cskh/translations/vi.csv` bằng CSV parser: **195 khóa duy nhất**, 0 khóa trùng, 0 bản dịch rỗng. Các số 191/196 trong evidence cũ không khớp snapshot file hiện tại; dùng 195 cho trạng thái hiện hành.

Patcher có bản dịch cho nhãn CRM được chọn, navbar, command palette và nhãn/tooltip biểu đồ `stage`/`source`; build thành công sau patch. Lượt này chưa có browser capture sau patch cuối để xác nhận mọi nhãn trên giao diện.

Checkout có 1/10 screenshot checklist; chưa chạy PII sweep trên đủ 10 màn. Không tuyên bố toàn bộ nhãn tiếng Anh đã được loại bỏ.

## Chênh lệch cần T1C rà soát trước khi ký bảo mật

Trong checkout này, ba tài liệu T1C (`t1c-security-verification.md`, `permission-matrix.md`, `risk-register.md`) vẫn ghi bản 2026-10-19 và chưa phản ánh code/CI hiện hành. Cụ thể, `permission-matrix.md` row RA-04 còn kỳ vọng `A`, trong khi `test_bt1c5_matrix_gaps.py` hiện pin `D` tạm thời (fail-closed). `test_docshare_lifecycle.py` ghi Q1–Q4 `DECIDED` theo ủy quyền Owner ngày 2026-10-26 và `OWNER_DECISIONS_CONFIRMED = True`; vì thế các tài liệu còn ghi Q1–Q4 đang chờ Owner đã lỗi thời.

Không tự sửa hoặc ký thay T1C trong evidence này. Cần T1C đối chiếu quyết định, test, matrix và phạm vi SEC hiện hành; đặc biệt xác nhận cách ghi RA-04 và phần RFC/SEC-26. G0-07 giữ **PARTIAL / CHƯA KÝ** cho đến khi rà soát đó được ghi nhận.

## Trạng thái P0 sau lượt này

| Gate | Trạng thái | Việc còn lại |
|---|---|---|
| G0-01 | `PASS` trong phạm vi baseline DEV | MariaDB 11.8 và full scan `mobile_no` là giới hạn/theo dõi hiệu năng, không đổi DB/index trong lượt này. |
| G0-02 | `PARTIAL` | Xác nhận browser/console sau restart và truy nguyên JSON parse lỗi đã thấy trước đó. |
| G0-07 | `PARTIAL / CHƯA KÝ` | T1C đồng bộ/review security docs với code và run mới; probe không bao phủ reverse-proxy/CORS. |
| G0-09 | `PARTIAL` | Chụp đủ 10 màn, quét PII, kiểm tra trực quan nhãn sau patch cuối. |
| G0-10 | `PASS` trong phạm vi CI PR #2 | Run #40481457445 xanh; không suy rộng sang các gate khác. |

**P0 vẫn `NO-GO cho P1`.** Không có thay đổi policy ngoài các quyết định Q1–Q4 đã được ghi trong source; không đổi SEC tally/matrix hoặc ký thay T1C. Không chạy workload phá hủy, không xóa volume/site.
