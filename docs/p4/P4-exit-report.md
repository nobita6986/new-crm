# P4 EXIT & INTEGRATED DELTA REPORT — CRM-CSKH (`P4-11` — Cổng `G01`–`G24`)

- **Mã tài liệu:** `P4-EXIT-REPORT` (`docs/p4/P4-exit-report.md` — `P4.md §19`, `§20.2`, `§20.3`)
- **Ngày tổng hợp:** `09/11/2026` (`Asia/Ho_Chi_Minh`)
- **Phạm vi lượt bàn giao này:** Tích hợp phần vận hành của **`T1A`** (trên nền đối chiếu runtime VPS `801be22` + `08c97e0`) với nhánh phát hành & đo tải của **`T1B`** (`427af4834eda48a15ca86c15e88342c3c40d5f2f`), đóng các khoảng trống tài liệu/kiểm thử delta do **`T1C`** xác nhận và gom **một danh sách đầu vào duy nhất cần Owner cấp**.
- **Ràng buộc an toàn tuyệt đối đã giữ nguyên:** **Không triển khai candidate lên môi trường đang phục vụ tại `https://crm.hrpartner.vn` (`103.77.208.186`) trong lượt này**; không bắn tải, không seed dữ liệu tổng hợp và không thử nghiệm gây gián đoạn trên VPS của Owner.

---

## 1. Thông Tin Định Danh Phiên Bản & Tách Biệt Môi Trường (`P4.md §20.3 §1`)

> [!IMPORTANT]
> Ba môi trường dưới đây mang định danh bất biến độc lập theo đúng chu kỳ của từng môi trường (xem phân loại tại [`t0-critical-cve-dossier.md`](../../deploy/release/t0-critical-cve-dossier.md) §1 — không so chéo dẫn tới kết luận nhầm "digest drift"):

| Môi trường | Vai trò hiện tại | Định danh Image / Digest bất biến | Mã nguồn ứng dụng (`apps`) | Trạng thái hiện hành |
| :--- | :--- | :--- | :--- | :--- |
| **Live Serving VPS (`https://crm.hrpartner.vn`)** | Đang phục vụ bản Việt hóa đã duyệt trên `103.77.208.186` (`crm-staging.localhost`) | `crm-cskh:0.0.1-4672041` (`sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9`) | `467204176326f046442b5e20c381f9a895b6e176` | **Đang hoạt động ổn định (`10` daemon `healthy`, `INV-01..06 == 0`) — Không chạm trong lượt này.** |
| **GHCR Verified Staging Candidate (`T1B` Run `42625295772`)** | Image bất biến đóng gói từ `release-lock.json` đã qua `migrate ×2`, `421/421` JUnit & benchmark `S1` | `ghcr.io/nobita6986/crm-cskh@sha256:b1329ef7c26a9861a07d25d425eb4438150d065d2548c464e7d3395e568a201d` (OCI Config `sha256:9f295546fc78c061d950322b9c8f60d40312016b802f325b15f834524f500829`) | `apps_tree_sha = 14b5387296dad815a6b9f663411f265fa7d9f910` (`frappe v15.126.0`, `crm v1.87.0`, `crm_cskh` `f51095ec...`) | **Đạt `staging_candidate`; giữ `production_candidate = BLOCKED` do 3 mã CVE gói nguồn Debian 13 chưa có bản vá upstream.** |
| **Isolated Staging-DR HTTPS & G07/G08 CI (`T1A`)** | Môi trường container TLS cô lập diễn tập `migrate ×2`, `S01`–`S20`, `restart/retry ×2`, `rollback` Cấp 1–4 & vòng đời backup | Build cục bộ từ cùng khóa `release-lock.json` trong `.github/workflows/p4-ops-dr-rehearsal.yml` | `apps_tree_sha = 14b5387296dad815a6b9f663411f265fa7d9f910` | **Đạt `PASS` toàn trình trên runner cô lập (`0` tác động lên VPS Owner).** |

- **Phiên bản công thức KPI khóa cứng:** `cskh-metrics-v2` (giữ nguyên 100%, không đổi trọng số hay quy tắc `N/A` / `Tạm tính 86,47`).
- **Ngữ cảnh tiếng Việt đã duyệt:** Giữ nguyên **Không gian tuyển dụng**, **Hồ sơ liên hệ**, **Người tuyển dụng**, **Giao dịch CRM**, **Hoàn tất mục tiêu liên hệ chiến dịch** và 5 bản ghi `Translation` cấp site của Owner.

---

## 2. Tổng Hợp Bằng Chứng Kiểm Thử Kỹ Thuật, Bảo Mật, Hiệu Năng & DR (`P4.md §20.3 §2–§5`)

### 2.1. CI, Hồi quy Nghiệp vụ `P0`–`P3` & Diễn tập HTTPS Cô lập
- **Chuỗi kiểm thử candidate (`Run 42625295772`, [`p4-candidate-and-s1-20261109`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/)):**
  - Kiểm định site sạch `migrate ×2` + đối soát `pip check == 0` + kiểm tra 10 dịch vụ smoke cô lập: **`PASS`** ([`clean-site-verify.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/clean-site-verify.json), [`isolated-smoke-report.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/isolated-smoke-report.json)).
  - Toàn bộ **`421/421` bài kiểm thử JUnit trên 30 module** (`g007=99`, `p1=209`, `p2=38`, `p3=39`, `p3_ui=7`, `auxiliary=29`): **`0` failures, `0` errors (`PASS`)**.
- **Diễn tập Staging/DR qua HTTPS thật ([`p4-https-delta-20261109`](../../deploy/ops/evidence/p4-https-delta-20261109/isolated-staging-dr-https-report.json)):**
  - Diễn tập `migrator ×2` + đối soát `BLG-P4-MIG-01` (`CRM CSKH = 22`, `Crm Cskh = 1`, `0` lệch dòng, `0` biến đổi sai `verified`/`QA=0`): **`PASS`**.
  - Kiểm chứng qua TLS thật: Security headers (`HSTS`, `X-Frame-Options`, `nosniff`, `Referrer-Policy`), `/crm` + 15 trang `/cskh/*` tiếng Việt (`lang="vi"`, `0` `Traceback`), ma trận âm tính `S01`–`S20` (`Staff A`/`Staff B`/`Leader`/`BOD`/`Guest`), thu hồi tức thì `A → B → A`, chống giả mạo `performed_by`, khử `<script>` XSS, kẹp `page_size <= 100`, ủy quyền `/private/files/*` & `CSKH Export Job`: **`PASS`**.
  - Diễn tập `restart/retry ×2` worker/scheduler & quay lui Cấp 1–4 (`all_post_restore_deltas_zero = true`, cả 8 bộ đếm lệch sau restore đều bằng **`0`**, kèm rào chắn quyết định `forward-fix` Cấp 3): **`PASS`**.

### 2.2. Hiệu năng trên Tập Dữ Liệu Gợi Ý `S1` ([`performance-report.md`](./performance-report.md))
- Trên quy mô gợi ý `S1` (`50.000` Hồ sơ / `75.000` Đầu việc / `250.000` Lần liên hệ / `150.000` SLA / `40` nhân sự — **không phải cam kết sản lượng tháng hay SLA đã chốt**):
  - `my_customers_worklist_api`: `p95 = 38,39 ms` (`c=12`) và `105,00 ms` (`c=40`), `0%` lỗi.
  - `bod_executive_overview_api`: `p95 = 89,88 ms` (`c=12`) và `221,70 ms` (`c=40`), `0%` lỗi (`0` quét bảng Attempt).
  - Ghi `Contact Attempt` + `Follow-up`: `p95 = 30,24 ms`, replay cùng `idempotency_key` không nhân đôi.
  - Nhập CSV `500` dòng nền: `15,64s` (`31,96 dòng/s`); phân phối `500` đầu việc cho `40` nhân sự: `19,26s`; tính lại KPI tháng đầy đủ: `68,59s` vs tăng trưởng ngày: `16,32s`.

### 2.3. Sao lưu, Khôi phục Cô lập trên VPS & Giám sát Định kỳ ([`backup-restore.md`](./backup-restore.md), [`monitoring-alerts.md`](./monitoring-alerts.md))
- **Tái sử dụng kết quả Restore cô lập đã đạt `PASS` trên VPS `103.77.208.186` (`G08`):** [`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json) xác nhận Pha A khôi phục `p4-20261109-pre-scheduler` đạt **`PASS` trong `142,598 giây`** *(chỉ tính clone cô lập trên cùng VPS, chưa gồm truyền WAN, cấp máy chủ mới hay chuyển DNS)*, Pha B Positive/Negative Control Fernet + `migrate ×2` + bảo toàn `5/5 Translation` đạt `PASS`, dọn sạch `0` tài nguyên dư và giữ nguyên site live (`create_encrypted_backup.py` và `verify_offsite_restore.py` không có thay đổi ảnh hưởng).
- **Giám sát sức khỏe tự động trên VPS (`G17`):** `crm-cskh-healthcheck.timer` đã tự kích hoạt theo lịch lúc `20:05 ICT` ngày `09/11/2026` (`PASS`, `INV-01..06 == 0`).
- **Kiểm toán tự động cổng `G07` (`manage_backup_lifecycle.py audit-g07-gate`):** Giữ trạng thái trung thực **`BLOCKED`** trên VPS cho tới khi đủ 4 bằng chứng thực chạy trên đích lưu trữ thật do Owner cấp (`timer_triggered_backup_proven`, `automated_offsite_upload_proven`, `remote_checksum_readback_verified`, `separate_key_escrow_proven`).

---

## 3. Danh Sách Duy Nhất Các Đầu Vào & Phê Duyệt Cần Owner Cấp Để Mở Khóa Go-Live

> [!WARNING]
> **Quy tắc trung thực quản trị:** Tuyệt đối không tự điền họ tên người trực/người ký nghiệm thu, không tự chọn dịch vụ lưu trữ trả phí và không phát cảnh báo tới kênh chưa được duyệt. Toàn bộ các cổng đang `BLOCKED` chỉ chờ đúng bảng đầu vào tập trung dưới đây từ **Owner / T0**:

| STT | Mã Blocker / Cổng | Hạng mục cần Owner cung cấp / quyết định | Nơi áp dụng sau khi được cấp | Trạng thái hiện tại |
| :---: | :--- | :--- | :--- | :---: |
| **1** | **`BLK-INFRA-05`** (`G07`, `G24`) | **Đích lưu trữ Offsite tự động ngoài VPS & Kho lưu ký khóa tách biệt (`Key Escrow`):**<br>- Loại kho ngoài VPS `103.77.208.186` (SFTP/NAS khác máy vật lý hoặc S3/Rclone sẵn có của Owner), địa chỉ `host/bucket/prefix` và khóa xác thực ít đặc quyền (chỉ ghi + đọc kiểm tra SHA-256).<br>- Định danh kho bí mật độc lập (Password Vault của Owner) để lưu `backup-key` từng gói + `encryption_key` của `site_config.json` (tách rời hoàn toàn khỏi nơi chứa `backup.tar.gz.enc`).<br>- Phê duyệt hạn mức dung lượng GFS `14d/8w/3m` và ngưỡng RPO/RTO mục tiêu. | Điền `/etc/crm-cskh/offsite-backup.env` (`0600`), chờ `crm-cskh-backup.timer` tự chạy lúc `02:15 ICT`, chạy `record-key-escrow` và xác nhận bằng `manage_backup_lifecycle.py audit-g07-gate` → `PASS`. | **`BLOCKED` (Chờ Owner cấp)** |
| **2** | **`BLK-GOV-06` (Trực vận hành)** (`G19`, `G24`) | **Danh sách Người trực vận hành (`On-Call Operators`):**<br>- Họ tên, tài khoản định danh riêng và khung giờ trực của **Operator kỹ thuật chính**, **Operator dự phòng / Tech Owner** và **Quản trị CRM nghiệp vụ**. | Cập nhật `CSKH_OPERATOR_CONTACT` trong `/etc/crm-cskh/alerting.env` (`0600`) và ký biên bản bàn giao [`operations-sop.md`](./operations-sop.md) Mục 7. | **`BLOCKED` (Chờ Owner cấp)** |
| **3** | **`BLK-INFRA-07`** (`G19`, `G24`) | **Kênh nhận cảnh báo ngoài hệ thống đã duyệt (`Approved Alert Channel`):**<br>- URL HTTPS Webhook chính thức (ChatOps nội bộ / Telegram / Slack / Pager của Owner). | Điền `CSKH_ALERT_WEBHOOK_URL` vào `/etc/crm-cskh/alerting.env` (`0600`), bắn thử `--send-test-alert` và kiểm chứng biên nhận bằng `check_health_and_invariants.py --verify-alert-receipt` → `PASS`. | **`BLOCKED` (Chờ Owner cấp)** |
| **4** | **`BLK-GOV-06` (Nghiệm thu UAT)** (`G22`) | **Danh sách Người nghiệm thu UAT theo 5 vai trò đại diện thật:**<br>- Đại diện **Ban Giám đốc (`BOD`)**, **Quản trị CRM**, **Trưởng nhóm** (2 đội), **Người tuyển dụng** và **Operator kỹ thuật** thực hiện ký biên bản sau khi chạy kịch bản tiếng Việt. | Ghi nhận kết quả và chữ ký thật vào Mục 6 của [`uat-report.md`](./uat-report.md). | **`BLOCKED` (Chờ Owner cấp)** |
| **5** | **Quyết định Phát hành & CVE OS Upstream** (`G04`, `G05`, `G18`, `G23`, `G24`) | **Phê duyệt SLO hiệu năng đề xuất & Cửa sổ phát hành chính thức:**<br>- Xem xét ngưỡng SLO đề xuất tại [`performance-report.md`](./performance-report.md) Mục 4.3.<br>- Chọn phương án cho 3 mã CVE gói nguồn Debian 13 chưa có bản vá (`PA1` chờ gói `deb13u*` từ Debian trước khi nâng cấp live, hoặc `PA2` xem xét ngoại lệ có thời hạn 30 ngày từng mục theo [`t0-critical-cve-dossier.md`](../../deploy/release/t0-critical-cve-dossier.md) §4) và duyệt cửa sổ bảo trì cutover nếu triển khai. | Chỉ thực thi [`release-runbook.md`](./release-runbook.md) sau khi có lệnh phê duyệt rõ ràng của Owner/T0. | **`BLOCKED` (Chờ Owner duyệt)** |

---

## 4. Bảng Tổng Hợp 24 Cửa Nghiệm Thu `G01`–`G24` (`P4.md §19` & `§20.3 §8`)

> **Quy tắc đọc bảng:** Mỗi cổng tách rõ kết quả kiểm định kỹ thuật trên **CI / Candidate / Staging-DR cô lập** và trạng thái thực tế đối với **Go-Live Production**, không gộp chung và không dùng `N/A`.

| Cổng | Điều kiện nghiệm thu (`P4.md §19`) | Trạng thái Hiện Tại | Bằng chứng xác thực (Tách rõ CI / Candidate / VPS Live) | Đầu mối ký duyệt |
| :---: | :--- | :---: | :--- | :--- |
| **G01** | P0–P3 Exit Reports có thật và blocker kỹ thuật P0–P3 đã đóng | **`PASS`** | [`entry-audit.md`](./entry-audit.md), `421/421` JUnit tests `PASS` trên `apps_tree_sha = 14b5387296dad815...` | Tech Lead / `T1C` |
| **G02** | Custom image chứa đủ Frappe (`v15.126.0`), CRM (`v1.87.0`), `crm_cskh` đúng version lock | **`PASS`** | `ghcr.io/nobita6986/crm-cskh@sha256:b1329ef7...` ([`candidate-provenance.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/candidate-provenance.json), [`clean-site-verify.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/clean-site-verify.json)) | `T1B` / CI Attestation |
| **G03** | DEV/CI/STAGING/PROD tách rõ dữ liệu, secrets, volumes; không sửa code trực tiếp trên VPS | **`PASS`** | [`infra-inventory.md`](./infra-inventory.md), `verify_pr_isolation.py`, [`live-preservation-check.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-preservation-check.json) | `T1A` / `T1B` |
| **G04** | Pipeline CI pass test/lint/build/security và artifact tái lập được | **`BLOCKED`** *(CI/Test/SBOM `PASS`; `strict` CVE OS chờ bản vá Debian hoặc quyết định mục 5)* | Run `42625295768` & `42625295772` `SUCCESS`, `0` fixable `CRITICAL`, còn `3` mã Debian `trixie` `fixed_version: null` ([`t0-critical-cve-dossier.md`](../../deploy/release/t0-critical-cve-dossier.md)) | Chờ upstream Debian / Owner |
| **G05** | Staging chạy được cùng `@sha256` digest dự kiến lên Production | **`BLOCKED`** *(Đã chứng minh trên Staging cô lập CI; chưa nâng cấp lên VPS live trong lượt này & cổng `production_candidate` đang khóa bởi `G04`)* | [`ghcr-digest-promotion-attestation.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/ghcr-digest-promotion-attestation.json), [`isolated-smoke-report.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/isolated-smoke-report.json) | Chờ Owner duyệt phát hành |
| **G06** | Schema migration & chạy lại `migrate ×2` an toàn trên bản sao `N-1` | **`PASS`** | `verify_offsite_restore.py` trên VPS (`migrate ×2` `PASS`), `rehearse_migration_x2_and_reconcile` (`BLG-P4-MIG-01` `22/1`, `0` lệch dòng) tại [`isolated-staging-dr-https-report.json`](../../deploy/ops/evidence/p4-https-delta-20261109/isolated-staging-dr-https-report.json) | `T1A` / `T1B` |
| **G07** | Backup DB + public/private files + config/key đầy đủ, timer tự chạy & upload offsite tự động | **`BLOCKED`** (`BLK-INFRA-05`) | Pipeline & CI drill `PASS`; trên VPS đã có 2 bản chép thủ công khớp SHA-256, đang chờ lượt timer `02:15 ICT` + đích offsite tự động + biên nhận lưu ký khóa riêng ([`g07-gate-vps-audit.json`](../../deploy/ops/evidence/p4-https-delta-20261109/g07-gate-vps-audit.json)) | Chờ Owner cấp `BLK-INFRA-05` |
| **G08** | Restore drill `PASS` trên site cô lập kèm đo RTO thực tế | **`PASS`** | Thực thi trực tiếp trên clone cô lập tại VPS `103.77.208.186`: Pha A `PASS` (`142,598s` clone cùng VPS), Pha B Fernet Dương/Âm `PASS`, `0` chạm site live ([`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json)) | `T0` / `T1A` (Đã đo trên VPS) |
| **G09** | Cây quyết định Rollback / Feature disable / Forward-fix / Restore đã diễn tập | **`PASS`** | [`rollback-runbook.md`](./rollback-runbook.md) + diễn tập HTTPS cô lập Cấp 1–4 (`all_post_restore_deltas_zero = true`) tại [`isolated-staging-dr-https-report.json`](../../deploy/ops/evidence/p4-https-delta-20261109/isolated-staging-dr-https-report.json) | `T1A` |
| **G10** | Không có lỗ hổng truy cập chéo PII theo P0–P3 và `S01`–`S20` | **`PASS`** | [`security-review.md`](./security-review.md), `g007=99/99 PASS`, ma trận HTTPS `S01`–`S20` `PASS` | `T1A` / `T1B` / `T1C` |
| **G11** | Tài khoản bị khóa / điều chuyển `A → B → A` / đổi đội không để sót quyền thừa | **`PASS`** | `test_docshare_lifecycle`, `test_p1_ownership`, kiểm chứng HTTPS `A → B → A` thu hồi tức thì (`403`) | `T1A` / `T1B` |
| **G12** | `113` API, tệp riêng tư & xuất báo cáo có ủy quyền, kẹp trang `<=100`, kiểm toán và khử PII | **`PASS`** | [`security-review.md`](./security-review.md) Mục 3 (`113/113` endpoints), [`api-contract.md`](./api-contract.md) | `T1A` / `T1B` |
| **G13** | Nhập CSV và phân phối trọng số không nhân đôi Lead, không sinh 2 owner active | **`PASS`** | `p1=209/209 PASS` (`F03`–`F11`), `INV-01 == 0` trên cả VPS live và CI | `T1B` / `T1A` |
| **G14** | `Contact Attempt`, `Call Log`, `CRM Task` hẹn lại đúng nguồn và không đếm đôi | **`PASS`** | `p2=38/38 PASS` (`F13`–`F20`), replay `idempotency_key` HTTPS `PASS`, `INV-02..03 == 0` | `T1B` / `T1A` |
| **G15** | SLA, QA, KPI `cskh-metrics-v2` (`N/A`, `Tạm tính 86,47`) và quy gán lịch sử chính xác | **`PASS`** | `p3=39/39 PASS` (`F21`–`F27`), kiểm chứng HTTPS `formula_version == cskh-metrics-v2` & `86.47`, `INV-04..05 == 0` | `T1B` / `T1A` |
| **G16** | Dashboard & drill-down của `BOD`, `Leader`, `Staff` đúng phạm vi dữ liệu | **`PASS`** | `p3_ui=7/7 PASS`, kiểm chứng HTTPS `BOD` (`include_pii=1` → `403`), `Leader` chéo đội → `403`, `Staff` scorecard riêng | `T1B` / `T1A` |
| **G17** | `scheduler`, hàng đợi RQ và tác vụ nền khởi động lại / chạy lại idempotent | **`PASS`** | Trên VPS: `crm-cskh-healthcheck.timer` tự chạy `20:05 ICT` `PASS` ([`live-health-after.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-health-after.json)); trên CI HTTPS: `restart/retry ×2` `PASS` | `T0` / `T1A` |
| **G18** | Benchmark `S1` đo thực tế đầy đủ và trình ngưỡng SLO + sizing để phê duyệt | **`PASS` (Kỹ thuật đo kiểm) / Chờ Owner duyệt SLO** | [`performance-report.md`](./performance-report.md) & [`benchmark-s1-report.json`](../../deploy/ops/evidence/p4-candidate-and-s1-20261109/benchmark-s1-report.json) (`50k`/`75k`/`250k`/`40` staff trên candidate `@sha256:b1329ef7...`) | Chờ Owner duyệt SLO đề xuất |
| **G19** | Giám sát & cảnh báo hoạt động, đã kiểm chứng biên nhận trên kênh thực | **`BLOCKED`** (`BLK-GOV-06`, `BLK-INFRA-07`) | Timer giám sát 5 phút chạy thật trên VPS `PASS`; CLI `--verify-alert-receipt` sẵn sàng; giữ khóa gửi cảnh báo ngoài cho tới khi Owner cấp người trực & webhook ([`monitoring-alerts.md`](./monitoring-alerts.md)) | Chờ Owner cấp `BLK-GOV-06` & `BLK-INFRA-07` |
| **G20** | Màn hình trọng yếu tiếng Việt đúng thuật ngữ tuyển dụng & khả năng truy cập cơ bản | **`PASS`** | [`glossary-vi.md`](./glossary-vi.md), [`uat-report.md`](./uat-report.md) Mục 4, kiểm chứng 15 route `/cskh/*` (`lang="vi"`) + `5/5 Translation` của Owner trên cả VPS live & HTTPS cô lập | `T0` (đã duyệt bản Việt hóa `4672041`) / `T1A` |
| **G21** | Tài liệu hóa hợp đồng `113` API & chính sách tương thích schema migration | **`PASS`** | [`api-contract.md`](./api-contract.md), kiểm thử AST tự động `113/113` endpoints trong `test_ops_toolkit.py` | `T1A` |
| **G22** | Đại diện `BOD` / `Quản trị CRM` / `Trưởng nhóm` / `Người tuyển dụng` / `Operator` ký UAT | **`BLOCKED`** (`BLK-GOV-06`) | Kịch bản, hướng dẫn [`huong-dan-su-dung-vi.md`](./huong-dan-su-dung-vi.md) và biên bản mẫu [`uat-report.md`](./uat-report.md) đã hoàn tất; chờ Owner phân công người thật ký nghiệm thu | Chờ đại diện 5 vai trò ký |
| **G23** | Phát hành Production bằng artifact bất biến đã duyệt, smoke & theo dõi sau phát hành đạt | **`BLOCKED`** *(Chưa thực hiện cutover Production theo chỉ đạo)* | [`release-runbook.md`](./release-runbook.md) đã đồng bộ wrapper `/opt/crm-cskh-staging/staging-compose` & `.env.staging` (`0600`); giữ nguyên không triển khai candidate lên live trong lượt này | Chờ quyết định Go-Live của Owner |
| **G24** | Bàn giao vận hành cho Operator kèm bộ SOP, quyền hạn, sổ tay DR/sự cố và P4 Exit Report | **`BLOCKED`** (`BLK-INFRA-05`, `BLK-GOV-06`, `BLK-INFRA-07`) | Toàn bộ sổ tay Day-2 & script kiểm định đã sẵn sàng trong repo; chờ mở khóa đích offsite tự động, kênh cảnh báo và ký nhận bàn giao của Operator do Owner chỉ định | Chờ Owner & Operator ký nhận |

---

## 5. Sổ Tay Bàn Giao Vận Hành Day-2 Cho Operator (`P4.md §20.3 §9`)

Ngay khi Owner chỉ định **Operator kỹ thuật** (`BLK-GOV-06`) và cấp các đầu vào ở **Mục 3**, Operator tiếp nhận vận hành theo đúng 6 bước khép kín sau:

1. **Đồng bộ thư mục mã nguồn vận hành bất biến `/opt/crm-cskh-ops` trên VPS:** Đóng gói từ đúng commit SHA bàn giao và xác minh mã băm theo [`backup-restore.md`](./backup-restore.md) Mục 3.0.
2. **Kích hoạt Offsite tự động & Kiểm toán đóng cổng `G07`:**
   - Khởi tạo `/etc/crm-cskh/offsite-backup.env` (`0600`, `root:root`) từ mẫu [`offsite-backup.env.example`](../../deploy/compose/env/offsite-backup.env.example).
   - Lưu ký `backup-key` vào Password Vault độc lập của Owner và ghi biên nhận không chứa khóa:
     ```bash
     sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py record-key-escrow \
       --bundle-dir /var/backups/crm-cskh/<TEN_BUNDLE_MOI_NHAT> \
       --vault-label "<TEN_KHO_KHOA_DOC_LAP_CUA_OWNER>"
     ```
   - Sau chu kỳ `02:15 ICT` của `crm-cskh-backup.timer`, kiểm tra toàn bộ 4 bằng chứng `G07`:
     ```bash
     sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py audit-g07-gate \
       --lifecycle-file /var/backups/crm-cskh/latest-backup-lifecycle.json \
       --timers-runtime-file /var/lib/crm-cskh/health/timers-runtime.json \
       --key-escrow-receipt-file /var/backups/crm-cskh/<TEN_BUNDLE_MOI_NHAT>/key-escrow-receipt.json \
       --restore-report-file /opt/crm-p4-restore-input-20261109/restore-drill-report.json \
       --out /var/backups/crm-cskh/g07-gate-audit.json
     ```
3. **Kích hoạt Kênh Cảnh báo Ngoài & Kiểm chứng Biên nhận đóng cổng `G19`:**
   - Khởi tạo `/etc/crm-cskh/alerting.env` (`0600`, `root:root`) với `CSKH_OPERATOR_CONTACT` và `CSKH_ALERT_WEBHOOK_URL` đã duyệt.
   - Bắn thử 1 cảnh báo kiểm tra và chạy lệnh xác thực biên nhận:
     ```bash
     sudo python3 /opt/crm-cskh-ops/deploy/ops/check_health_and_invariants.py \
       --verify-alert-receipt /var/lib/crm-cskh/test-alert-receipt.json
     ```
4. **Tổ chức Phiên Ký Nghiệm Thu UAT 5 Vai Trò đóng cổng `G22`:** Thực hiện trên môi trường Staging/DR HTTPS cô lập theo [`uat-report.md`](./uat-report.md) và [`huong-dan-su-dung-vi.md`](./huong-dan-su-dung-vi.md).
5. **Vận hành Hằng ngày / Hằng tuần / Hằng tháng:** Tuân thủ lịch kiểm tra, quy trình khởi động lại dịch vụ an toàn, quy trình xử lý sự cố dữ liệu 6 bước và checklist thu hồi quyền nhân sự nghỉ việc tại [`operations-sop.md`](./operations-sop.md).
6. **Phát hành & Quay lui An toàn khi có Phê duyệt:** Thực hiện theo [`release-runbook.md`](./release-runbook.md) và [`rollback-runbook.md`](./rollback-runbook.md).
