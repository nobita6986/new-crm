# Hồ sơ chuẩn bị Pull Request & Phương án cập nhật `crm.hrpartner.vn`

- **Nhánh nguồn:** `codex/recruitment-workspace-ui`
- **Nhánh đích:** `main` (`92e14afef376d9bd68beea702ab057af77d70b5b`)
- **Merge SHA tích hợp 3 bên (`T1A` + `T1B` + `T1C`):** `391b4845be504f7f094a855d1f1aaf56f3d92867`
- **Tree SHA `apps/crm_cskh` cuối cùng:** `0248d8954b9a9314e907151f911373d48546e8b6`
- **Phân loại thay đổi vận hành:** **Class A+ — Zero-DDL, Fixture & Idempotent Catalog Sync** (`0` thay đổi schema/bảng/cột/index, `patches.txt` giữ nguyên)

---

## Phần 1 — Chuẩn bị Pull Request (`codex/recruitment-workspace-ui` → `main`)

### 1.1. Tiêu đề PR đề xuất

```text
feat(ui,i18n,p3): đồng bộ Không gian tuyển dụng 6 bước, chỉ số P3 và bộ kiểm thử độc lập
```

### 1.2. Chuỗi commit tích hợp trên nhánh

| Thứ tự | Commit SHA | Vai trò | Nội dung chính |
| :-: | :--- | :---: | :--- |
| 1 | `8549a6289811b6cd3ba639ebddfcda2d55bd1f00` | `T1A` | Việt hóa luồng cơ sở Không gian tuyển dụng, lối vào `/crm` ↔ `/cskh`, giữ nhãn **Giao dịch CRM** cho `CRM Deal`, tài liệu tách bạch `GAP-01..03` |
| 2 | `b36b6c4a47f2db1b299b327f036515b91a8301e6` | `T1A` | Đồng bộ điều hướng 6 bước tuyển dụng, bộ từ điển `vi.csv` (`1.331` dòng, bảo toàn khối P3), cập nhật assertion E2E P1/P2 & ghi chú mẫu lương/ca làm việc (bảo toàn payload chống XSS) |
| 3 | `32b53fb51a87b85b7e04da4fbd1c62259affe616` | `T1B` | Đồng bộ nhãn tiếng Việt danh mục kết quả liên hệ P2, `label_vi` của `29` chỉ số + `5` điểm thành phần P3 và ánh xạ tương thích snapshot cũ |
| 4 | `6912c78c1c74483c1a0063379d94d8c9b8bdb30b` | `T1A` | Merge tích hợp giao diện `b36b6c4` + backend `32b53fb` |
| 5 | `14960ebd0031a8170e35a1847a01aabdb8b10815` | `T1A` | Đóng gói bộ kiểm thử Việt hóa tái lập (`12/12` suite, `48/48` JUnit methods, `17` ảnh Chromium E2E, `SHA256SUMS.txt`) |
| 6 | `6a751286e7c4b699bebe3fa9d2c685879d94e326` | `T1C` | Kiểm định độc lập đa vai trò (`Admin` / `Staff` / `BOD`), đối chứng `migrate × 2` bất biến lịch sử & snapshot KPI |
| 7 | `2556090767fe820c29e4f05d1808702bf31ee19d` | `T1C` | Kiểm định tải CSV qua HTTP xác thực của `BOD` (`EXP-BE8FDEFBDD98`, `29/29` chỉ số, `0` PII) và `4/4` cổng chặn `HTTP 403` |
| 8 | `a973e3a69530edb9441480c3b2862b381656b36b` + `8184c8050868da74fc0e30edff0c7dc390c19c2b` | `T1B` | Cô lập fixture `test_p3_reporting_and_security` với cuộc gọi tổng đài PBX hợp lệ `SEC2-02` + bằng chứng chạy độc lập đạt **`7/7 PASS`** trên site sạch & hồi quy **`19/19 PASS`** |
| 9 | `391b4845be504f7f094a855d1f1aaf56f3d92867` | `T1A` | Merge tích hợp nhánh bằng chứng `8184c80` của `T1B` cùng kết quả HTTP CSV `2556090` của `T1C` (không xung đột) |
| 10 | `HEAD` *(commit chốt trên `391b484`)* | `T1A` | Dọn khoảng trắng cuối dòng 2 script kiểm định `T1C` (giữ nguyên từng byte CSV HTTP `92bb90d5be2b...`), cập nhật manifest SHA-256, hồ sơ bàn giao và phương án triển khai `crm.hrpartner.vn` |

### 1.3. Đối chiếu diff sau tích hợp `8184c80` (`6912c78..391b484`)

- **Thay đổi trên mã nguồn runtime sản phẩm:** **`0` tệp / `0` dòng** (`services/`, `api/`, `doctype/`, `www/`, `public/`, `templates/`, `translations/vi.csv`, `fixtures/`, `hooks.py`, `patches.txt` giữ nguyên 100% như tại `6912c78`).
- **Thay đổi trên mã kiểm thử (`a973e3a`, `+47/-22` dòng trên 2 tệp test):**
  1. `apps/crm_cskh/crm_cskh/tests/p2_base.py`: bổ sung tham số tùy chọn `telephony_medium: str | None = None` cho `make_native_call_log()` (mặc định giữ nguyên `""` cho các suite P2 cũ).
  2. `apps/crm_cskh/crm_cskh/tests/test_p3_reporting_and_security.py`: dọn sạch các bảng runtime CSKH trong `setUp()` và tạo tường minh 1 cuộc gọi tổng đài `telephony_medium="Twilio"` (`verification_status="verified"`) + 2 cuộc gọi `Manual` (`verification_status="self_reported"`) trong `_seed_two_team_activity()`, kiểm tra chính xác `verified == 1` và `self_reported == 2`.
- **Tổng phạm vi thay đổi so với `main` (`92e14af`) không tính thư mục `deploy/dev/evidence/`:** **`46` tệp (`+1.181 / -555` dòng)**.

### 1.4. Nội dung mô tả PR (sẵn sàng dán khi mở PR)

```markdown
## Mục tiêu
Đồng bộ thuật ngữ và luồng thao tác tiếng Việt của **Không gian tuyển dụng** (`/cskh` và lối vào từ `/crm`) theo đúng thực tế vận hành tiếp nhận hồ sơ, liên hệ và hẹn gọi lại của HR Partner, đồng thời giữ nguyên chức năng gốc của **Giao dịch CRM** (`CRM Deal`) và bảo toàn toàn bộ kiến trúc DocType, dữ liệu lịch sử, phân quyền và công thức KPI P1–P3.

## Phạm vi thay đổi chính (`46` tệp code/test/tài liệu, `0` thay đổi DDL/schema)
1. **Lối vào `/crm` ↔ `/cskh` & luồng 6 bước Không gian tuyển dụng:**
   - Lối vào rõ ràng trên danh sách/chi tiết `CRM Lead` (**Hồ sơ liên hệ**) và danh sách `CRM Deal` (**Giao dịch CRM**) qua `fixtures/crm_form_script.json`.
   - Đồng bộ xuyên suốt 6 bước trên `/cskh`: **Nhập danh sách liên hệ** (`/cskh/import`) → **Kho hồ sơ liên hệ** (`/cskh/pool`) → **Phân công** (`/cskh/campaigns`) → **Hồ sơ được giao** (`/cskh/my-customers`) → **Ghi nhận liên hệ** (`/cskh/work-item`) → **Lịch hẹn liên hệ lại** (`/cskh/callbacks`).
2. **Bảo toàn ngữ nghĩa nghiệp vụ trung thực:**
   - `CRM Deal` giữ nguyên chức năng thương mại gốc dưới nhãn **Giao dịch CRM** (**Chuyển thành Giao dịch CRM**).
   - Kết quả `converted` hiển thị nhất quán là **Hoàn tất mục tiêu liên hệ chiến dịch** kèm hộp lưu ý ngữ nghĩa ngay trên biểu mẫu ghi nhận liên hệ (không gây hiểu nhầm là đã tuyển hoặc đã bố trí nhân sự).
   - Ghi nhận riêng các yêu cầu nghiệp vụ mở rộng (`GAP-01..03`: phân loại ứng viên/doanh nghiệp, đối chiếu yêu cầu cung ứng, trạng thái đặt người/onboard) tại `docs/recruitment-staffing-domain-alignment.md`.
3. **Đồng bộ 3 chiều báo cáo P3 & tương thích ngược dữ liệu cũ:**
   - Đồng bộ `29/29` chỉ số và `5/5` điểm thành phần giữa thẻ giao diện, bảng chi tiết và tệp CSV xuất ra (`/cskh/quality-overview`, `/cskh/campaign-quality`, `/cskh/my-scorecard`).
   - Ánh xạ nhãn in-memory qua `unpack_snapshot_metrics()` đối với `CSKH KPI Snapshot` cũ mà không ghi đè `metrics_json` lịch sử; chỉ cập nhật nhãn mặc định cho `CSKH Outcome Code` còn ở `version == 1`, bảo toàn các mã do Quản trị tùy chỉnh (`version > 1`) và `outcome_label_snapshot` lịch sử.
4. **Cô lập hoàn toàn kiểm thử bảo mật P3 (`a973e3a`):**
   - `crm_cskh.tests.test_p3_reporting_and_security` nay tự nạp dữ liệu tổng đài PBX hợp lệ `SEC2-02` và chạy độc lập đạt **`7/7 PASS`** trên site sạch hoàn toàn.

## Kết quả kiểm định 3 bên (`T1A` · `T1B` · `T1C`)
- **Cổng tĩnh & hợp đồng (`T1A`):** `vi.csv` `1.331` dòng (`0` lỗi cú pháp, `missing=0`, `conflict=0`, `orphan=0`); đối chiếu `29/29` chỉ số P3 `mismatches=0`; hợp đồng UI & múi giờ P2/P3 PASS (`deploy/dev/evidence/recruitment-workspace-ui-20261107/`).
- **Frappe Unit & Bảo mật độc lập (`T1A` + `T1B`):** `48/48` unit test methods PASS qua `ci_junit_gate.py`; riêng `test_p3_reporting_and_security` chạy **độc lập trên site sạch** đạt **`7/7 PASS`** (`0` fail, `0` error, `0` skip) và chạy hồi quy liên module P2+P3 đạt **`19/19 PASS`** (`deploy/dev/evidence/p3-reporting-test-isolation-20261107/`).
- **Trình duyệt đa vai trò & Tải CSV qua HTTP xác thực (`T1A` + `T1C`):**
  - Chromium `153.0.8010.12` E2E trên các vai trò **Quản trị CRM**, **Nhân viên** và **BOD** (`17` ảnh của `T1A` + `12` ảnh của `T1C`, `0` lỗi console/pageerror/HTTP 5xx, chặn XSS an toàn).
  - `migrate × 2` idempotent, giữ nguyên mã kết quả đã tùy chỉnh (`version > 1`), `outcome_label_snapshot` và `metrics_json` thô.
  - Phiên HTTP xác thực của `BOD` tải `GET /api/method/crm_cskh.api.p3_reporting.download_export` đạt `HTTP 200 OK` (`EXP-BE8FDEFBDD98.csv`, `3.018` bytes, `29/29` chỉ số tiếng Việt, `0` PII) và chặn đúng **`4/4` kịch bản trái phép với `HTTP 403`** (`include_pii=1`, `my_scorecard` bởi BOD, tải chéo bởi Nhân viên, tải không xác thực bởi Guest) (`deploy/dev/evidence/t1c-recruitment-multirole-audit-20261107/`).
```

---

## Phần 2 — Phương án cập nhật `crm.hrpartner.vn`

### 2.1. Đánh giá tác động kỹ thuật trên môi trường thật

1. **Cơ sở dữ liệu (`MariaDB`):** **Zero-DDL (`0` thay đổi cấu trúc bảng).** Không thêm/xóa/đổi kiểu bất kỳ DocType, cột hay chỉ mục nào; `apps/crm_cskh/crm_cskh/patches.txt` không thay đổi.
2. **Vì sao cần đồng thời (a) Build Image mới VÀ (b) Chạy `bench migrate` + `clear-cache`:**
   - **Image mới (`crm-cskh:<TAG>`):** Đóng gói tĩnh bản dịch `translations/vi.csv` (`1.331` dòng), tài sản `/assets/crm_cskh/cskh/*`, các bản vá Việt hóa `apply-frappe-vi-patch.py` + `apply-crm-ui-vi-patch.py` và gói biên dịch Vue SPA `/crm` (`CI=true bench build --production --force`).
   - **`bench --site crm.hrpartner.vn migrate`:** Đồng bộ `fixtures/crm_form_script.json` vào bảng `tabCRM Form Script` (cập nhật 2 nút lối vào trên `CRM Lead` và thêm nút lối vào từ danh sách `CRM Deal`), đồng thời kích hoạt hook `after_migrate` (`ensure_p2_seeds`, `ensure_p3_seeds`) để cập nhật nhãn mặc định cho các `CSKH Outcome Code` chưa bị chỉnh sửa (`version == 1`).
   - **An toàn dữ liệu hiện hữu trên `crm.hrpartner.vn`:**
     - Các mã kết quả do Quản trị viên đã chỉnh sửa trên môi trường thật (`version > 1`) **được giữ nguyên 100%**.
     - Cột `outcome_label_snapshot` trên toàn bộ `CSKH Contact Attempt` cũ **bất biến**.
     - Cột `metrics_json` trên các `CSKH KPI Snapshot` đã chốt kỳ **không bị ghi đè** (tầng đọc tự động chiếu sang nhãn mới).

### 2.2. Kiểm tra trước giờ cập nhật (`T-1` — chỉ đọc, không tác động dịch vụ)

Thực hiện trên VPS của `crm.hrpartner.vn` trước khi mở khung bảo trì:

1. **Xác định chính xác project Compose, tệp cấu hình và image digest đang chạy:**
   ```bash
   docker compose ls
   docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
   # Ghi lại <PREV_IMAGE_REF> và <PREV_DIGEST> thực tế đang chạy của container web/worker:
   docker inspect <WEB_CONTAINER> --format '{{.Config.Image}} -> {{.Image}}'
   docker image inspect <IMAGE_ID> --format '{{json .RepoDigests}}'
   ```
2. **Kiểm tra dung lượng đĩa và khóa mã hóa (`encryption_key`):**
   - Đảm bảo phân vùng chứa Docker volumes và thư mục backup còn trống tối thiểu **5 GB** (`df -h`).
   - Xác nhận giá trị `encryption_key` trong `sites/crm.hrpartner.vn/site_config.json` đã được lưu trữ an toàn ngoài VPS (offsite escrow).
3. **Kiểm tra trạng thái hàng đợi nền hiện tại:**
   - Xác nhận không có lô phân phối lớn hoặc kỳ chốt KPI tháng đang treo dở trước giờ bảo trì.

### 2.3. Quy trình đóng gói Image bất biến trước giờ cắt chuyển

Sau khi `T0` merge PR vào `main` (tạo `<MAIN_MERGE_SHA>`):

1. Build image sản phẩm từ `<MAIN_MERGE_SHA>` theo đúng quy trình đóng gói production:
   - Giữ nguyên thứ tự cài đặt `frappe` (`d2efa79657ba5c1abf96098b3bfacc199517d57e`) → `crm` (`0d3910b456ef4fb913e77b29db011b3debc4f778`) → `crm_cskh` (`<MAIN_MERGE_SHA>`).
   - Thực thi hai script vá giao diện tiếng Việt trước bước build tài sản:
     - `python deploy/dev/apply-frappe-vi-patch.py`
     - `python deploy/dev/apply-crm-ui-vi-patch.py`
   - **Tuyệt đối KHÔNG chạy** `deploy/dev/apply-crm-vite-dev-patch.py` trên image production.
   - Chạy `CI=true bench build --production --force` để biên dịch mới cả bundle Vue `/crm` lẫn `/assets/crm_cskh/cskh/*`.
2. Đẩy image lên registry nội bộ/GHCR và ghi nhận mã băm bất biến `sha256:<NEW_DIGEST>`.

### 2.4. Các bước cập nhật trong khung bảo trì (`~10–15` phút)

> [!CAUTION]
> **Quy tắc dừng khẩn cấp (`STOP RULE`):** Nếu bước sao lưu (`Bước 2`) không tạo ra đủ tệp dump SQL + files hoặc kiểm tra mã băm thất bại, **dừng ngay lập tức**, tắt chế độ bảo trì (`set-maintenance-mode off`), bật lại scheduler và **không** cập nhật container.

#### Bước 1 — Bật chế độ bảo trì, tạm dừng scheduler và chụp số liệu đối chiếu (`~2` phút)

```bash
# 1. Bật chế độ bảo trì và tạm dừng lập lịch trên site crm.hrpartner.vn
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn set-maintenance-mode on
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn scheduler pause

# 2. Chờ 15-30 giây để RQ workers hoàn tất các job ngắn đang xử lý, sau đó ghi nhận baseline bản ghi:
docker exec <WEB_CONTAINER> /home/frappe/frappe-bench/env/bin/python -c "
import frappe, json
frappe.init(site='crm.hrpartner.vn', sites_path='/home/frappe/frappe-bench/sites')
frappe.connect()
counts = {dt: frappe.db.count(dt) for dt in [
    'CRM Lead', 'CRM Deal', 'CSKH Work Item', 'CSKH Contact Attempt', 'CSKH KPI Snapshot', 'CRM Form Script'
] if frappe.db.exists('DocType', dt)}
print(json.dumps(counts, ensure_ascii=False))
"
```

#### Bước 2 — Sao lưu toàn phần và kiểm chứng SHA-256 (`~2–3` phút)

```bash
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn backup --with-files
# Sao chép các tệp backup mới nhất từ sites/crm.hrpartner.vn/private/backups/ ra thư mục lưu trữ ngoài container + tạo SHA256SUMS:
# sha256sum <BACKUP_DIR>/*crm_hrpartner_vn* > <BACKUP_DIR>/SHA256SUMS.txt && sha256sum -c <BACKUP_DIR>/SHA256SUMS.txt
```

#### Bước 3 — Kéo image mới, cập nhật biến image digest và chạy `migrate` (`~3–5` phút)

```bash
# 1. Kéo trước image mới theo digest để tối thiểu hóa thời gian gián đoạn:
docker pull <REGISTRY>/crm-cskh@sha256:<NEW_DIGEST>

# 2. Cập nhật biến IMAGE/TAG trong tệp .env của stack VPS sang @sha256:<NEW_DIGEST>
# (giữ lại dòng ghi chú <PREV_DIGEST> để sẵn sàng rollback tức thì).

# 3. Chạy configurator (nếu stack dùng service configurator) để đảm bảo sites/apps.txt = frappe -> crm -> crm_cskh,
#    sau đó chạy migrate một lần (TUYỆT ĐỐI KHÔNG dùng cờ --skip-failing):
docker compose <COMPOSE_FILES> run --rm migrator \
  bench --site crm.hrpartner.vn migrate
```
*(Nếu stack hiện hành trên VPS chạy trực tiếp không tách service `migrator`, thực hiện `docker compose up -d` với image mới rồi chạy ngay `docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn migrate` trong khi `maintenance_mode` vẫn đang `on`).*

#### Bước 4 — Khởi động lại dịch vụ runtime, xóa cache và mở lại lưu lượng (`~2` phút)

```bash
# 1. Cập nhật toàn bộ dịch vụ runtime lên image mới (web, websocket, worker-short/default/long, scheduler, proxy nếu dùng chung):
docker compose <COMPOSE_FILES> up -d

# 2. Xóa toàn bộ Redis/website cache để nạp từ điển vi.csv (1.331 dòng) và CRM Form Script mới:
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn clear-cache
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn clear-website-cache

# 3. Bật lại scheduler và mở chế độ bảo trì:
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn scheduler resume
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn set-maintenance-mode off
```

#### Bước 5 — Kiểm tra đối chứng sau triển khai trên `https://crm.hrpartner.vn` (`~3` phút)

1. **Đối chiếu số lượng bản ghi DB:** Chạy lại đoạn lệnh đếm ở Bước 1 — xác nhận `CRM Lead`, `CRM Deal`, `CSKH Work Item`, `CSKH Contact Attempt`, `CSKH KPI Snapshot` **bảo toàn 100%**, và `CRM Form Script` có đủ 3 bản ghi (`CSKH - Open CSKH Page`, `CSKH - Open Lead CSKH Workspace`, `CSKH - Open CSKH Page from Deal`).
2. **Kiểm tra `/crm` (SPA):**
   - Mở `https://crm.hrpartner.vn/crm/leads`: tiêu đề **Hồ sơ liên hệ**, có nút **Không gian tuyển dụng**; mở 1 hồ sơ thấy nút **Chuyển thành Giao dịch CRM** và **Mở trong Không gian tuyển dụng**.
   - Mở `https://crm.hrpartner.vn/crm/deals`: tiêu đề **Giao dịch CRM**, có nút **Không gian tuyển dụng**.
3. **Kiểm tra luồng 6 bước `/cskh`:**
   - Với tài khoản **Quản trị CRM**: kiểm tra thanh điều hướng 6 bước (`Nhập danh sách liên hệ` → `Kho hồ sơ liên hệ` → `Phân công` → `Hồ sơ được giao` → `Ghi nhận liên hệ` → `Lịch hẹn liên hệ lại`) và `/cskh/settings` (xác nhận các mã kết quả đã tùy chỉnh trước đó giữ nguyên nhãn).
   - Với tài khoản **Nhân viên**: mở 1 hồ sơ được giao, kiểm tra biểu mẫu **Ghi nhận liên hệ** hiển thị hộp lưu ý cho kết quả **Hoàn tất mục tiêu liên hệ chiến dịch**.
4. **Kiểm tra báo cáo P3 & Tải CSV qua HTTPS (vai trò `BOD` hoặc `Quản trị CRM`):**
   - Mở `https://crm.hrpartner.vn/cskh/quality-overview`: tiêu đề `<h1>` hiển thị **Tổng quan chất lượng liên hệ & SLA (BOD)**, không có lỗi console/HTTP `5xx`.
   - Bấm **Xuất CSV** trên trang báo cáo, xác nhận tải tệp `.csv` qua HTTPS thành công (`HTTP 200`, tiêu đề cột & nhãn chỉ số tiếng Việt đồng bộ với thẻ và bảng chi tiết, `0` cột PII).

---

### 2.5. Phương án Rollback 2 tầng

#### Tầng 1 — Fast Image & Fixture Rollback (`< 3` phút — Khuyến nghị mặc định)

Do bản cập nhật này **không có bất kỳ thay đổi DDL/schema nào** và không ghi đè dữ liệu lịch sử (`outcome_label_snapshot`, `metrics_json`), nếu phát hiện lỗi hiển thị giao diện sau khi mở lưu lượng, thực hiện hoàn nguyên nóng mà **không làm mất dữ liệu giao dịch mới phát sinh**:

```bash
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn set-maintenance-mode on
# Đổi lại biến image trong .env về @sha256:<PREV_DIGEST>
docker compose <COMPOSE_FILES> up -d
# Chạy migrate trên image cũ để đồng bộ lại fixtures/crm_form_script.json cũ:
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn migrate
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn clear-cache
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn clear-website-cache
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn set-maintenance-mode off
```

#### Tầng 2 — Full Backup Restore (`5–10` phút — Chỉ dùng khi cần khôi phục toàn bộ DB về thời điểm Bước 2)

```bash
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn set-maintenance-mode on
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn scheduler pause
# Đổi lại biến image trong .env về @sha256:<PREV_DIGEST> và docker compose up -d db redis-cache redis-queue web
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn --force restore \
  /path/in/container/to/<SQL_BACKUP.sql.gz> \
  --with-public-files /path/in/container/to/<PUBLIC_FILES.tar> \
  --with-private-files /path/in/container/to/<PRIVATE_FILES.tar>
docker compose <COMPOSE_FILES> up -d
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn clear-cache
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn scheduler resume
docker exec <WEB_CONTAINER> bench --site crm.hrpartner.vn set-maintenance-mode off
```

---

### 2.6. Lưu ý phối hợp liên nhánh với P4 (`codex/p4-01-compose` / `codex/p4-ops-readiness`)

- Trên trang `/cskh/quality-overview`, tiêu đề `<h1>` đã chuyển từ `"Tổng quan chất lượng CSKH (BOD)"` sang **`"Tổng quan chất lượng liên hệ & SLA (BOD)"`**.
- Khi rebase các nhánh vận hành P4 lên sau PR này, cập nhật chuỗi kiểm tra smoke HTTP tương ứng trong script smoke P4 (`run_isolated_smoke.py` / `staging_https_smoke_and_reconcile.py`) để khớp với tiêu đề mới.
