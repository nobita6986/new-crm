# Hồ Sơ Kiểm Thử E2E (`F01`–`F30`), Kịch Bản UAT Theo Vai Trò & Biên Bản Nghiệm Thu (`P4-08` — Cổng `G13`–`G16`, `G20`, `G22`)

- **Mã tài liệu:** `P4-UAT-REPORT` (`docs/p4/uat-report.md` — `P4.md §12.2`, `§13.1`, `§16`, `§20.2`)
- **Đầu vào khóa cứng:** Bản Việt hóa Không gian tuyển dụng `467204176326f046442b5e20c381f9a895b6e176` (`sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` đang phục vụ tại `https://crm.hrpartner.vn`)
- **Tài liệu đối chiếu:** [`glossary-vi.md`](./glossary-vi.md) · [`huong-dan-su-dung-vi.md`](./huong-dan-su-dung-vi.md)
- **Kết quả kiểm thử tự động & trình duyệt đã nghiệm thu trên source `4672041`:**
  - Hồi quy backend P1–P3: **`19/19 PASS`**; suite bảo mật & báo cáo P3 chạy độc lập: **`7/7 PASS`**; kiểm thử đa vai trò trên trình duyệt + đối chiếu tải CSV của BOD qua HTTP: **`PASS`** (`17` ảnh bằng chứng giao diện tiếng Việt).
- **Trạng thái ký nghiệm thu người dùng đại diện chính thức (`G13`–`G16`, `G22`):** **`BLOCKED`** *(Bộ kịch bản UAT theo 5 vai trò, hướng dẫn sử dụng tiếng Việt và các ca kỹ thuật `UAT-P4-OPS-01..02` trên VPS `103.77.208.186` đã đo đạt `PASS`; bước tổ chức phiên ký nghiệm thu của đại diện 5 vai trò thật đang chờ Owner phân công danh tính người ký `BLK-GOV-06` — tuyệt đối không điền tên người ký giả)*

---

## 1. Điều Kiện & Nguyên Tắc Thực Hiện UAT Trên Staging (`P4.md §16.1`)

1. **Bảo toàn tuyệt đối môi trường đang phục vụ tại `https://crm.hrpartner.vn`:** Không chạy script seed tự động xóa dữ liệu, không khôi phục đè hay chạy thử phá hủy trên site `crm-staging.localhost` đang phục vụ của Owner. Toàn bộ phiên UAT có dữ liệu tổng hợp, kiểm quyền chéo, diễn tập `restart/retry` và `rollback` thực hiện trên **môi trường Staging/DR HTTPS cô lập riêng** theo hướng dẫn tại [`isolated-staging-dr-https.md`](./isolated-staging-dr-https.md) (`deploy/ops/manage_isolated_staging_dr.py`).
2. **Giữ nguyên nhãn đã duyệt & công thức KPI:** Kiểm tra đúng bộ nhãn tiếng Việt trên luồng `Hồ sơ liên hệ → Phân công → Ghi nhận liên hệ → Lịch hẹn liên hệ lại`, `Giao dịch CRM`, `Hoàn tất mục tiêu liên hệ chiến dịch` và 5 nhãn tùy chỉnh cấp site của Owner (`Người tuyển dụng`, `Các dự án`, `Doanh thu hàng tháng / năm`, `NLĐ đang tiếp cận`, `NLD đã tiếp cận`); không thay đổi công thức KPI (`cskh-metrics-v2`).
3. **Đại diện đủ 5 nhóm vai trò (`BLK-GOV-06`):** `Ban Giám đốc (BOD)`, `Quản trị CRM`, `Trưởng nhóm` (2 đội độc lập), `Người tuyển dụng` và `Vận hành kỹ thuật (Operator)`.

---

## 2. Ma Trận Kiểm Thử Hành Trình E2E Đầy Đủ (`F01`–`F30`, `P4.md §13.1`)

| Mã | Kịch bản kiểm thử | Vai trò thực hiện | Route / API kiểm chứng | Kết quả kỳ vọng bắt buộc | Kết quả Auto-Test / Browser (`4672041`) | Ký UAT Người dùng (`P4-08`) |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: |
| **F01** | Người tuyển dụng đăng nhập, xem danh sách hồ sơ được giao | `Người tuyển dụng` (`Sales User`) | `/cskh/my-customers`, `/crm` | Hiển thị đúng **Không gian tuyển dụng**, **Hồ sơ được giao** và chỉ thấy hồ sơ giao cho mình. | `PASS` | `PENDING UAT` |
| **F02** | BOD đăng nhập xem quản trị toàn công ty | `BOD` (`CSKH BOD Viewer`) | `/cskh/quality-overview` | Xem tổng hợp toàn công ty `0` PII thô; không có quyền `System Manager`. | `PASS` (`7/7` Sec) | `PENDING UAT` |
| **F03** | Nhập CSV chỉ có số điện thoại (`phone-only`) | `Quản trị CRM` (`CSKH CRM Admin`) | `/cskh/import`, `imports.*` | Chuẩn hóa SĐT, tạo/ghép `Hồ sơ liên hệ` với `cskh_is_placeholder_name=1`. | `PASS` | `PENDING UAT` |
| **F04** | Nhập tệp có `0...`, `+84...`, `84...` và khoảng trắng | `Quản trị CRM` | `/cskh/import` | Quy đổi về duy nhất 1 khóa `normalized_phone` trên `CSKH Identity Index`. | `PASS` | `PENDING UAT` |
| **F05** | Nhập trùng nội bộ tệp và trùng hồ sơ hiện có | `Quản trị CRM` | `/cskh/import` | Không nhân đôi `CRM Lead`; báo rõ `Số dòng hợp lệ`, `Số dòng mới`, `Số dòng trùng`. | `PASS` | `PENDING UAT` |
| **F06** | Nhập tệp sai định dạng hoặc vượt giới hạn `10 MB` | `Quản trị CRM` | `/cskh/import` | Báo lỗi tiếng Việt rõ ràng; không ghi dở dang bản ghi lỗi vào kho chính. | `PASS` | `PENDING UAT` |
| **F07** | Xem trước phân phối trọng số `50/30/20` | `Quản trị CRM` | `/cskh/campaigns`, `distribution.preview` | Phân bổ đúng tỷ lệ mục tiêu khi không bị chặn `Sức chứa` hoặc trạng thái nghỉ. | `PASS` | `PENDING UAT` |
| **F08** | Phân phối liên tiếp nhiều lô nhỏ | `Quản trị CRM` | `distribution.execute` | Thuật toán weighted fairness có nhớ lịch sử giữ tỷ lệ lũy kế bám sát trọng số. | `PASS` | `PENDING UAT` |
| **F09** | Nhân sự bị tắt nhận hồ sơ hoặc chạm trần `Sức chứa` | `Quản trị CRM` | `/cskh/campaigns` | Không giao hồ sơ mới cho nhân sự chạm trần / `User.enabled=0`. | `PASS` | `PENDING UAT` |
| **F10** | Mạng chập chờn / client gửi lại lệnh xác nhận phân phối | `Quản trị CRM` | `distribution.execute` | Khóa idempotency giữ nguyên kết quả lần đầu, không tạo lượt giao thứ hai. | `PASS` | `PENDING UAT` |
| **F11** | Hai Quản trị CRM bấm phân phối cạnh tranh | `Quản trị CRM` ×2 | `distribution.execute` | Khóa DB & `CSKH Active Claim` giữ đúng duy nhất 1 người phụ trách active cho mỗi hồ sơ. | `PASS` | `PENDING UAT` |
| **F12** | Người tuyển dụng nhận hồ sơ mới được giao | `Người tuyển dụng` | `/cskh/my-customers`, `/cskh/work-item` | Thấy ngay đầu việc ở trạng thái **Mới được giao**, hạn `FIRST_ATTEMPT` và lịch sử phân công. | `PASS` | `PENDING UAT` |
| **F13** | Ghi nhận cuộc gọi `Không nghe máy` (`no_answer`) | `Người tuyển dụng` | `workflow.record_attempt` | Lưu lần ghi nhận liên hệ, tăng số lần thử, **không** tăng số lần đã kết nối. | `PASS` | `PENDING UAT` |
| **F14** | Ghi nhận cuộc gọi `Đã trao đổi — Có quan tâm` (ngữ cảnh lương & ca làm việc) | `Người tuyển dụng` | `workflow.record_attempt` | Lưu đúng kết quả, `is_connected=1`, ghi chú được làm sạch chống XSS. | `PASS` | `PENDING UAT` |
| **F15** | Ghi nhận liên hệ qua `Kênh Zalo` / `Tin nhắn SMS` / `Kênh Facebook` | `Người tuyển dụng` | `workflow.record_attempt` | Lưu đúng kênh; `Đã gửi tin nhắn, chưa phản hồi` không bao giờ tính là đã kết nối. | `PASS` | `PENDING UAT` |
| **F16** | Gắn `Nhật ký cuộc gọi (CRM)` vào lần ghi nhận liên hệ | `Người tuyển dụng` | `workflow.record_attempt`, KPI engine | Liên kết 1-1 qua `call_log`, tuyệt đối không đếm đôi vào KPI. | `PASS` | `PENDING UAT` |
| **F17** | Đặt lịch hẹn liên hệ lại (`Đã hẹn gọi / liên hệ lại`) | `Người tuyển dụng` | `/cskh/callbacks`, `CRM Task` | Tạo `CRM Task` (`cskh_is_followup=1`) đúng người phụ trách, chuyển sang `Chờ liên hệ lại`. | `PASS` | `PENDING UAT` |
| **F18** | Đổi lịch hẹn với lý do hợp lệ | `Người tuyển dụng` | `/cskh/callbacks` | Tăng `cskh_reschedule_count`, lưu vết lịch sử, giữ nguyên `original_due_at` trên SLA. | `PASS` | `PENDING UAT` |
| **F19** | Lịch hẹn quá hạn và `scheduler` chạy lặp lại nhiều chu kỳ | Hệ thống (`scheduler`) | `evaluate_sla_obligations` | Đánh dấu vi phạm đúng 1 lần, chạy lại idempotent không nhân đôi lỗi vi phạm. | `PASS` | `PENDING UAT` |
| **F20** | Điều chuyển `Người tuyển dụng A → B` khi đang có lịch hẹn mở | `Quản trị CRM`, `A`, `B` | `ownership.transfer` | Quyền sở hữu và lịch hẹn chuyển đồng bộ sang `B`; `A` mất quyền đọc/sửa chi tiết ngay (`403`). | `PASS` | `PENDING UAT` |
| **F21** | Điều chuyển vòng lặp `A → B → A` | `Quản trị CRM` | `/cskh/employee-quality` | Tách đúng 3 khoảng phụ trách độc lập, bảo toàn quy gán lịch sử từng chặng. | `PASS` | `PENDING UAT` |
| **F22** | Đóng đầu việc với `Hoàn tất mục tiêu liên hệ chiến dịch` | `Người tuyển dụng` | `workflow.close_work_item` | Đóng bước liên hệ đầu vào hợp lệ kèm cảnh báo rõ không đồng nghĩa đã tuyển/nhận việc. | `PASS` | `PENDING UAT` |
| **F23** | Hạn `FIRST_ATTEMPT` rơi qua đêm, cuối tuần và ngày nghỉ lễ | Hệ thống | `CSKH Working Calendar` | Cộng tịnh tiến đúng phút làm việc thực tế theo lịch `Asia/Ho_Chi_Minh`. | `PASS` | `PENDING UAT` |
| **F24** | Chạy tính lại KPI (`recompute`) 2 lần trên cùng dữ liệu đầu vào | `Quản trị CRM` | `recompute_kpi` | Số liệu trùng khớp 100%, quản lý `is_latest_published` đúng hợp đồng. | `PASS` | `PENDING UAT` |
| **F25** | Đối soát KPI theo Ngày / Tuần / Tháng / Quý / Năm | `BOD` | `/cskh/quality-overview` | Chỉ số `Hồ sơ đã kết nối hai chiều duy nhất` tính `DISTINCT` chính xác trên cả thẻ, bảng và CSV. | `PASS` | `PENDING UAT` |
| **F26** | Trạng thái KPI `N/A` / `Chưa đủ dữ liệu` / `Tạm tính (86,47)` | `BOD`, `Người tuyển dụng` | `/cskh/employee-quality`, `/cskh/scorecard` | Mẫu số `0` hiển thị `N/A`; tắt QA hiển thị `Tạm tính; thiếu mẫu QA` (`86,47`), không tính `0%` giả. | `PASS` | `PENDING UAT` |
| **F27** | Bấm truy vết (`drill-down`) từ thẻ KPI xuống danh sách gốc | `BOD`, `Trưởng nhóm` | `p3_reporting.get_metric_drilldown` | Tổng dòng Đạt + Không đạt khớp tuyệt đối tử/mẫu số; BOD bị che sạch PII thô. | `PASS` (`7/7` Sec) | `PENDING UAT` |
| **F28** | Trưởng nhóm A xem Đội A và thử đổi bộ lọc sang Đội B | `Trưởng nhóm A` | `/cskh/team-quality` | Xem đủ Đội A; đổi tham số sang `TEAM-B` nhận `403 FORBIDDEN_SCOPE`. | `PASS` (`7/7` Sec) | `PENDING UAT` |
| **F29** | Xuất tệp báo cáo CSV qua HTTP & kiểm tra trung hòa công thức / TTL | `BOD`, `Trưởng nhóm` | `CSKH Export Job` | Tải CSV tiếng Việt khớp nhãn thẻ/bảng, che PII cho BOD, trung hòa `=,+,-,@` và tự hết hạn đúng `expires_at`. | `PASS` (HTTP CSV) | `PENDING UAT` |
| **F30** | Mở nhiều tab trình duyệt & khóa tài khoản đang mở phiên | `Người tuyển dụng` | Session / WebSocket | Nhiều tab hợp lệ hoạt động ổn định; ngay khi `User.enabled=0`, phiên cũ bị từ chối tức thì. | `PASS` | `PENDING UAT` |

### 2.2. Ma Trận Toàn Vẹn Dữ Liệu, Migration & Khôi Phục Thảm Họa (`D01`–`D15`, `P4.md §13.3`)

| Mã | Kịch bản kiểm thử toàn vẹn & DR | Cơ chế / Công cụ thực thi | Kết quả kỳ vọng bắt buộc | Bằng chứng thực đo |
| :--- | :--- | :--- | :--- | :---: |
| **D01** | Chạy migration trên bản sao `N-1` | `verify_offsite_restore.py`, `manage_isolated_staging_dr.py` | Thoát `0`, đối soát số dòng & SHA-256 bảng khớp 100%, bảo toàn `5/5 Translation`. | **`PASS`** |
| **D02** | Chạy lại migration lần 2 (`migrate ×2`) | `rehearse_migration_x2_and_reconcile` (`BLG-P4-MIG-01`) | Idempotent: không nhân đôi schema/records, `CRM CSKH=22, Crm Cskh=1`, `INV-01..06 == 0`. | **`PASS`** |
| **D03** | Migration gặp lỗi / không tương thích | Cây quyết định Cấp 3–4 ([`rollback-runbook.md`](./rollback-runbook.md)) | Dừng ngay theo Stop Rule, không dùng `--skip-failing`, bảo vệ bởi `tier3_forward_fix_decision_guard`. | **`PASS`** |
| **D04** | Thử lại (`retry`) tác vụ nhập CSV (`CSKH Import Batch`) | `test_p1_imports`, `test_p1_chaos` | Không đếm đôi và không tạo trùng `CRM Lead` / `CSKH Identity Index`. | **`PASS`** |
| **D05** | Thử lại (`retry`) tác vụ phân phối chiến dịch | `test_p1_distribution`, `test_p1_concurrency` | Khóa `CSKH Active Claim` giữ duy nhất 1 owner active (`INV-01 == 0`). | **`PASS`** |
| **D06** | Thử lại (`retry`) tác vụ tính lại KPI (`recompute`) | `test_p3_kpi_and_snapshots`, `rehearse_worker_restart_and_cron_retry` | Chỉ công bố snapshot khi `status == completed`; không phát sinh snapshot dở dang. | **`PASS`** |
| **D07** | Đổi đội trong `CRM Sales Hierarchy` sau khi kỳ KPI đã chốt | `test_p3_sla_and_attribution` (`CSKH Team Membership History`) | Quy gán lịch sử `team-as-of` của kỳ cũ giữ nguyên bất biến. | **`PASS`** |
| **D08** | Thiếu mẫu chấm QA (`denominator = 0` hoặc tắt QA) | `test_p3_kpi_and_snapshots`, `INV-05` | `QA_PASS_RATE.value = null`, điểm chuẩn hóa `Tạm tính = 86,47`, không bao giờ biến thành `0%` giả. | **`PASS`** |
| **D09** | Bù dữ liệu lịch sử thiếu chủ sở hữu | `test_p3_sla_and_attribution`, `test_p3_t1c_remediation` | Đánh dấu không xác định theo hợp đồng, tuyệt đối không suy đoán bằng owner hiện tại. | **`PASS`** |
| **D10** | Sao lưu cơ sở dữ liệu có mã hóa & kiểm tra mã băm | `create_encrypted_backup.py`, `manage_backup_lifecycle.py` | Gói `backup.tar.gz.enc` (`AES-256-CBC` PBKDF2) giải mã vòng khép kín khớp SHA-256. | **`PASS`** |
| **D11** | Khôi phục tệp công khai (`public/files`) | `verify_offsite_restore.py` (Pha A & B trên VPS) | Byte/SHA-256 khớp 100%, quyền POSIX an toàn, Guest đọc qua HTTP đạt `200`. | **`PASS`** |
| **D12** | Khôi phục tệp riêng tư (`private/files`) + `encryption_key` | `verify_offsite_restore.py` (Pha B Positive/Negative Control) | Giải mã Fernet thành công, Negative Control chặn mất khóa, Guest `403` / Auth `200`. | **`PASS`** |
| **D13** | Quay lui mã nguồn (`Code Rollback`) trên schema tương thích | `rehearse_rollback_tiers_over_https` (Cấp 1–2) | Chuyển cờ tính năng & quay lui image an toàn, dữ liệu và bất biến không suy suyển. | **`PASS`** |
| **D14** | Khôi phục toàn phần trên site DR cô lập | `restore-drill-report.json` (VPS `142,598s`) & `dr-https` (Cấp 4) | Đăng nhập, đọc/ghi bản ghi, kiểm tra KPI & `all_post_restore_deltas_zero = true` (`8/8` bộ đếm `= 0`). | **`PASS`** |
| **D15** | Khởi động lại worker / `scheduler` giữa chu kỳ xử lý | `rehearse_worker_restart_and_cron_retry` (`×2`) | Không nhân đôi tác dụng phụ, tự động tiếp tục và giữ `INV-01..06 == 0`. | **`PASS`** |

---

## 3. Danh Sách Ca Nghiệm Thu UAT Theo Từng Vai Trò (`P4.md §16.2–§16.3`)

### 3.1. Vai trò 1 — Ban Giám Đốc (`BOD` · Role `CSKH BOD Viewer`)

| Mã Ca UAT | Thao tác thực nghiệm của BOD | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-BOD-01`** | Đăng nhập tài khoản BOD riêng, mở `/cskh/dashboard` và 5 màn báo cáo chất lượng (`/cskh/quality-overview`, `team-quality`, `employee-quality`, `campaign-quality`, `sla-operations`). | Xem đầy đủ số liệu toàn công ty tiếng Việt; thử mở `/app` cấu hình hệ thống hoặc `/cskh/work-item` chi tiết cá nhân bị chặn `403`. | `PENDING UAT` |
| **`UAT-P4-BOD-02`** | Kiểm tra xuyên suốt một chiến dịch từ tổng hồ sơ nhập → đã giao → chưa giao → đã thử liên hệ → `Hồ sơ đã kết nối hai chiều duy nhất` → `Hoàn tất mục tiêu liên hệ chiến dịch`. | Tên chỉ số và con số nhất quán giữa thẻ tổng quan, bảng chi tiết và tệp CSV tải về; giữ đúng ranh giới ngữ nghĩa tuyển dụng. | `PENDING UAT` |
| **`UAT-P4-BOD-03`** | Bấm xem giải thích công thức và bấm truy vết (`Drill-down`) trên tối thiểu 5 chỉ số trọng yếu (`Liên hệ lần đầu đúng hạn`, `Chăm sóc đúng lịch hẹn`, `Tỷ lệ kết nối`, `Cập nhật kết quả đầy đủ`, `Tuân thủ DNC`). | Đủ thông tin giải thích tiếng Việt; tổng dòng danh sách khớp tử/mẫu số; **`0` số điện thoại thô hay ghi chú nhạy cảm bị lộ**. | `PENDING UAT` |
| **`UAT-P4-BOD-04`** | Kiểm tra hiển thị điểm `Tạm tính`, ô `N/A` khi mẫu số bằng `0`, và phân tách `Nhân viên tự báo cáo` vs `Đã xác minh`. | Không ghép chung bảng xếp hạng `Tạm tính` với `Đã chốt`; không hiển thị `0%` giả; không biến tự báo cáo thành đã xác minh. | `PENDING UAT` |
| **`UAT-P4-BOD-05`** | Tải báo cáo CSV/XLSX tổng hợp và kiểm tra kỳ `Đã tính lại kỳ cũ`. | Tệp mở chuẩn tiếng Việt có dấu, không chứa SĐT thô, khóa tải sau `expires_at`. | `PENDING UAT` |

### 3.2. Vai trò 2 — Quản Trị CRM (`CSKH CRM Admin`)

| Mã Ca UAT | Thao tác thực nghiệm của Quản trị CRM | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-ADM-01`** | Tại `/cskh/import` (**Nhập danh sách liên hệ**), tải lên tệp mẫu có số đầu `0`/`+84`, dòng chỉ có SĐT, dòng trùng và dòng lỗi. | Chuẩn hóa về một `Hồ sơ liên hệ` duy nhất, gắn cờ tên tạm cho dòng chỉ có SĐT, bảng xem trước tiếng Việt rõ ràng. | `PENDING UAT` |
| **`UAT-P4-ADM-02`** | Tại `/cskh/campaigns` (**Phân công & chiến dịch**), cấu hình trọng số cho **Người tuyển dụng**, chạy **Xem trước phân phối** rồi **Xác nhận phân phối**. | Xem trước hiển thị đúng số lượng dự kiến, loại trừ người chạm sức chứa/tạm khóa; xác nhận giao hồ sơ chính xác và ghi kiểm toán. | `PENDING UAT` |
| **`UAT-P4-ADM-03`** | Thực hiện **Điều chuyển** một hồ sơ đang có lịch hẹn từ `Người tuyển dụng A` sang `B` và **Thu hồi** một hồ sơ chưa liên hệ. | `B` nhận cả đầu việc lẫn lịch hẹn; `A` mất ngay quyền mở hồ sơ (`403`); lịch sử bàn giao ghi đủ người thực hiện & lý do. | `PENDING UAT` |
| **`UAT-P4-ADM-04`** | Đối chiếu các nhãn CRM tại `/crm`: `Hồ sơ liên hệ`, `Giao dịch CRM`, cùng 5 nhãn của Owner (`Người tuyển dụng`, `Các dự án`, `Doanh thu hàng tháng / năm`, `NLĐ đang tiếp cận`, `NLD đã tiếp cận`). | Hiển thị đầy đủ, đúng chính xác từng nhãn Owner đã yêu cầu. | `PENDING UAT` |

### 3.3. Vai trò 3 — Trưởng Nhóm (`Sales Manager` — Đội A & Đội B)

| Mã Ca UAT | Thao tác thực nghiệm của Trưởng nhóm | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-LDR-01`** | Mở `/cskh/progress`, `/cskh/sla-operations`, `/cskh/team-quality` và `/cskh/employee-quality`. | Thấy đầy đủ các đầu việc sắp quá hạn/đã quá hạn và bảng chỉ số của các **Người tuyển dụng** thuộc đội mình. | `PENDING UAT` |
| **`UAT-P4-LDR-02`** | Thử đổi bộ lọc/URL sang đội khác (`team=TEAM-B` hoặc nhân sự ngoài nhánh). | Bị chặn `403`, không lộ bất kỳ con số tổng hợp hay SĐT/ghi chú của đội khác. | `PENDING UAT` |
| **`UAT-P4-LDR-03`** | Xét duyệt một yêu cầu hiệu chỉnh lần liên hệ (`Duyệt áp dụng` / `Từ chối` có ghi chú phê duyệt). | Lưu vết kiểm toán đầy đủ và tự động xếp hàng cập nhật lại chỉ số liên quan (`CSKH Dirty Scope`). | `PENDING UAT` |

### 3.4. Vai trò 4 — Người Tuyển Dụng (`Sales User`)

| Mã Ca UAT | Thao tác thực nghiệm của Người tuyển dụng | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-STF-01`** | Mở `/cskh/my-customers` (**Hồ sơ được giao**), tìm kiếm/lọc và mở `/cskh/work-item` (**Chi tiết hồ sơ liên hệ**). | Giao diện 100% tiếng Việt rõ nghĩa, hiển thị đúng giờ Việt Nam và chỉ thấy hồ sơ đang giao cho mình. | `PENDING UAT` |
| **`UAT-P4-STF-02`** | Ghi nhận lần lượt: `Không nghe máy`, `Đã gửi tin nhắn, chưa phản hồi` (Zalo), `Đã hẹn gọi / liên hệ lại` (kèm ghi chú mức lương/ca làm việc & giờ hẹn), và đổi lịch hẹn có lý do trên `/cskh/callbacks`. | Hai lần đầu không tính đã kết nối; lần hẹn gọi lại tạo đúng lịch hẹn trên `/cskh/callbacks`; đổi lịch lưu đầy đủ lý do. | `PENDING UAT` |
| **`UAT-P4-STF-03`** | Ghi nhận `Hoàn tất mục tiêu liên hệ chiến dịch` và `Yêu cầu ngừng liên hệ (DNC)`, kiểm tra `/cskh/scorecard`. | Hiển thị rõ lưu ý chỉ đóng bước liên hệ đầu vào; hồ sơ DNC bị khóa liên hệ tiếp; bảng điểm cá nhân hiển thị rõ tử/mẫu số. | `PENDING UAT` |
| **`UAT-P4-STF-04`** | Sau khi Quản trị CRM chuyển hồ sơ sang người khác, bấm lại đường dẫn cũ `/cskh/work-item?id=...`. | Bị từ chối `403` ngay lập tức (`0` lộ SĐT/ghi chú mới), trong khi điểm lịch sử khoảng mình từng phụ trách vẫn được bảo toàn. | `PENDING UAT` |

### 3.5. Vai trò 5 — Vận Hành Kỹ Thuật (`Operator` · Role `System Manager` + Host Ops)

| Mã Ca UAT | Thao tác thực nghiệm của Người vận hành | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-OPS-01`** | Chạy `check_health_and_invariants.py --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml` và kiểm tra timer định kỳ. | Toàn bộ 10 container `healthy`, hàng đợi RQ + 4 cron `PASS`, 6 bất biến `INV-01..06 == 0`, `crm-cskh-healthcheck.timer` tự kích hoạt lúc `20:05 ICT` ngày `09/11/2026` (`PASS`), không tự phát cảnh báo ngoài khi chưa mở khóa ([`live-health-after.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-health-after.json), [`timers-runtime.json`](../../deploy/ops/evidence/p4-vps-live-20261109/timers-runtime.json)). | `PASS (Measured on VPS — Chờ ký nhận BLK-GOV-06)` |
| **`UAT-P4-OPS-02`** | Thực thi diễn tập khôi phục cô lập `verify_offsite_restore.py` với gói `p4-20261109-pre-scheduler` (`d6f1a0a1600f92d9b0b486665da486f74f209b676e43c15835e6c4a74d877e11`). | Pha A (`UNINITIALIZED_NO_ENCRYPTED_DATA` + `migrate ×2` + `5/5 Translation` overrides + HTTP/CSRF + RTO clone cùng VPS `142.598s`) và Pha B (Positive/Negative Control `ValidationError` + dọn sạch `0` tài nguyên dư) đạt `PASS`, site live không bị chạm ([`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json)). | `PASS (Measured on VPS — Chờ ký nhận BLK-GOV-06)` |
| **`UAT-P4-OPS-03`** | Rà quy trình khởi động lại dịch vụ ([`operations-sop.md`](./operations-sop.md)), xử lý backup lỗi ([`backup-restore.md`](./backup-restore.md) Mục 3.4), quay lui Cấp độ 1–4 và dựng lại khi mất VPS ([`rollback-runbook.md`](./rollback-runbook.md)). | Đầy đủ lệnh chuẩn theo `/opt/crm-cskh-staging/staging-compose` và `.env.staging` (`0600`); đã diễn tập `migrate ×2`, `restart/retry ×2` & `rollback` Cấp 1–4 (`all_post_restore_deltas_zero = true`) trên môi trường `dr-https` cô lập ([`isolated-staging-dr-https-report.json`](../../deploy/ops/evidence/p4-https-delta-20261109/isolated-staging-dr-https-report.json)). | `READY (In-Repo — Chờ ký nhận BLK-GOV-06)` |

---

## 4. Checklist Kiểm Tra Trình Duyệt, Việt Hóa & Khả Năng Truy Cập Trên 10 Nhóm Màn Hình Trọng Yếu (`G20` — `P4.md §12`)

- [x] **1. Đăng nhập & Đổi mật khẩu (`/login`):** Nhãn đăng nhập, đổi mật khẩu và thông báo lỗi/khóa tài khoản hiển thị tiếng Việt chuẩn (đã kiểm chứng trên `crm.hrpartner.vn` và `dr-https`).
- [x] **2. Nhập danh sách liên hệ (`/cskh/import`):** Nhãn tải tệp riêng tư, thông báo lỗi CSV, lý do trùng SĐT, bảng xem trước và xác nhận nhập tiếng Việt rõ nghĩa.
- [x] **3. Kho hồ sơ & Phân công chiến dịch (`/cskh/pool`, `/cskh/campaigns`):** Trọng số `%`, hạn mức `Sức chứa`, xem trước kết quả phân bổ, xác nhận giao hồ sơ và điều chuyển/thu hồi không tràn vỡ bố cục trên màn hình laptop `1366×768`.
- [x] **4. Hồ sơ được giao (`/cskh/my-customers`):** Tìm kiếm, lọc, trạng thái rỗng/đang tải/lỗi hiển thị đúng `Hồ sơ liên hệ` / `Hồ sơ được giao` (triệt tiêu hoàn toàn lỗi dịch máy).
- [x] **5. Chi tiết hồ sơ & Ghi nhận liên hệ (`/cskh/work-item`):** Đủ 5 kênh liên hệ, 12 kết quả tiếng Việt, ô ghi chú, thông báo kiểm tra hợp lệ và cảnh báo rõ `Hoàn tất mục tiêu liên hệ chiến dịch` không đồng nghĩa ứng viên đã nhận việc.
- [x] **6. Lịch hẹn liên hệ lại (`/cskh/callbacks`):** Đặt lịch hẹn, lịch sử dời hẹn có lý do, cảnh báo quá hạn hiển thị theo múi giờ `Asia/Ho_Chi_Minh` (lưu UTC/chuẩn hệ thống).
- [x] **7. Báo cáo Chất lượng, SLA & Bảng điểm (`/cskh/dashboard`, `quality-overview`, `team-quality`, `employee-quality`, `campaign-quality`, `sla-operations`, `scorecard`):** Hiển thị rõ tử số / mẫu số, giải thích công thức, dấu phân cách số nhất quán và phân biệt minh bạch `0%`, `Tạm tính (86,47)` và `N/A`.
- [x] **8. Chấm mẫu QA khi bật cờ (`QA Sampling` — `p3_qa_enabled`):** Mặc định tắt (`false`) và hiển thị nhãn `Tạm tính; thiếu mẫu QA`; khi bật kiểm tra đúng quyền chấm/xem phiếu `published`, chặn tự chấm (`QA_CONFLICT`) và ghi kiểm toán (`test_ui_p3_pages_and_context`).
- [x] **9. Tải tệp riêng tư & Xuất báo cáo CSV (`Export & Private Files`):** Hiển thị trạng thái xử lý `queued`/`completed`/`expired`, cảnh báo phạm vi, từ chối `403`/`410` khi thiếu quyền hoặc quá hạn `expires_at`, và giữ đồng bộ nhãn tiếng Việt trên `/crm` (`Giao dịch CRM` + 5 nhãn `Translation` của Owner).
- [x] **10. Cài đặt/Vai trò, Điều hướng Bàn phím & Header Bảo mật HTTPS (`Settings/Roles, A11y & TLS`):** Nhãn vai trò `BOD` / `Trưởng nhóm` / `Người tuyển dụng` / `Quản trị CRM` rõ ràng tại `/cskh/settings`; hỗ trợ `Tab`, `Shift+Tab`, `Enter`, `Escape`, độ tương phản dễ đọc; `/crm` và toàn bộ 15 đường dẫn `/cskh/*` trả `HTTP 200`, `lang="vi"`, `0` `Traceback`, kèm `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`.
  - *Ghi nhận giới hạn được phép theo `P4.md §12` & `§19.2`:* Một số màn cấu hình Frappe Desk hệ thống cấp thấp (`/app/*` chỉ dành cho `System Manager`, nhân viên và `BOD` không sử dụng hằng ngày) vẫn giữ thuật ngữ kỹ thuật gốc của Frappe Framework; luồng tác nghiệp `/cskh/*` và `/crm` đã Việt hóa nhất quán 100%.

---

## 5. Tài Liệu Hướng Dẫn Sử Dụng Đi Kèm (`P4.md §16.4`)

Xem bản hướng dẫn sử dụng tiếng Việt chi tiết theo từng vai trò tại: **[`docs/p4/huong-dan-su-dung-vi.md`](./huong-dan-su-dung-vi.md)**.

---

## 6. Biểu Mẫu Ký Nghiệm Thu UAT Chính Thức (`G13`–`G16`, `G20`, `G22` — `P4.md §16.3`, Chờ `BLK-GOV-06`)

> **Quy tắc trung thực & Hồ sơ tối thiểu mỗi ca (`P4.md §16.3`):** Khi thực hiện ký nghiệm thu thật, mỗi ca ghi đầy đủ: người kiểm thử, vai trò/đội, bộ dữ liệu sử dụng, các bước thực hiện, kết quả kỳ vọng, kết quả thực tế, ảnh chụp/nhật ký đã che PII, mức độ lỗi (nếu có), commit sửa lỗi, ngày kiểm tra lại và chữ ký xác nhận. Tuyệt đối không điền chữ ký giả khi chưa có phân công `BLK-GOV-06` từ Owner (xem danh sách đầu vào duy nhất tại [`P4-exit-report.md`](./P4-exit-report.md) Mục 3).

| Vai trò nghiệm thu | Họ tên / Tài khoản (`BLK-GOV-06`) | Phạm vi ca nghiệm thu | Image Digest kiểm thử | Kết quả ký nhận | Ngày ký |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Ban Giám đốc (`BOD`)** | `PENDING (BLK-GOV-06)` | `F02`, `F25..F27`, `F29`, `UAT-P4-BOD-01..05` | `sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` | **`BLOCKED` (Chờ người ký)** | — |
| **Quản trị CRM (`CSKH CRM Admin`)** | `PENDING (BLK-GOV-06)` | `F03..F11`, `F20..F21`, `F24`, `UAT-P4-ADM-01..04` | `sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` | **`BLOCKED` (Chờ người ký)** | — |
| **Trưởng nhóm Đội A & Đội B** | `PENDING (BLK-GOV-06)` | `F27..F29`, `UAT-P4-LDR-01..03` | `sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` | **`BLOCKED` (Chờ người ký)** | — |
| **Người tuyển dụng đại diện** | `PENDING (BLK-GOV-06)` | `F01`, `F12..F20`, `F22`, `F30`, `UAT-P4-STF-01..04` | `sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` | **`BLOCKED` (Chờ người ký)** | — |
| **Vận hành kỹ thuật (`Operator`)** | `PENDING (BLK-GOV-06)` | `UAT-P4-OPS-01..03`, Đào tạo & Bàn giao | `sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9` | **`BLOCKED` (Chờ người ký)** | — |
