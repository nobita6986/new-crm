# Hồ Sơ Kiểm Thử E2E (`F01`–`F30`), Kịch Bản UAT Theo Vai Trò, Hướng Dẫn Sử Dụng & Biên Bản Nghiệm Thu (`P4-08` — Cổng `G13`–`G16`, `G20`, `G22`)

- **Mã tài liệu:** `P4-UAT-REPORT` (`docs/p4/uat-report.md` — `P4.md §12.2`, `§13.1`, `§16`, `§20.2`)
- **Đầu vào khóa cứng:** Hồ sơ `P4-01` tích hợp tại `492423c` (`main` `92e14af`, CI `P0`–`P3` `4/4 PASS` trên PR #13)
- **Từ điển chuẩn đối chiếu:** [`docs/p4/glossary-vi.md`](./glossary-vi.md)
- **Trạng thái thực thi UAT live hiện tại:** **`BLOCKED`** *(Kịch bản E2E `F01`–`F30`, danh sách ca UAT theo 5 vai trò và tài liệu hướng dẫn đã hoàn thiện 100%; bước chạy UAT trên Staging HTTPS và ký nghiệm thu người dùng thật đang chờ mở khóa `P4-03` [`BLK-INFRA-01..04`], `P4-04` Bảo mật, `P4-05` Backup/Restore, `P4-06` Benchmark và danh tính người ký `BLK-GOV-06`)*.

---

## 1. Điều Kiện Tiên Quyết Trước Khi Mở Phiên UAT Chính Thức

Theo đúng thứ tự phụ thuộc của `P4.md §16.1`, `§18` và chỉ đạo điều phối:

1. **Hạ tầng & Artifact (`P4-03` / `G05`):** Staging HTTPS phải đang chạy **chính xác cùng `@sha256` registry digest** dự kiến phát hành Production, khóa truy cập công khai bằng VPN/IP allowlist và đã tắt toàn bộ kênh gửi email/thông báo ra ngoài.
2. **An toàn dữ liệu & Hiệu năng (`P4-04`, `P4-05`, `P4-06`):** Đã hoàn tất kiểm thử phá ranh giới quyền `S01`–`S20` (`G10`–`G12`), diễn tập khôi phục DR (`G07`–`G08`) và đo kiểm hiệu năng (`G18`). Tuyệt đối không đưa dữ liệu khách hàng thật vào môi trường chưa vượt cổng bảo mật.
3. **Nhân sự nghiệm thu (`BLK-GOV-06`):** Có đầy đủ đại diện thật cho 5 nhóm vai trò: `BOD`, `Admin nghiệp vụ`, `2 Trưởng nhóm` (2 đội độc lập), `Nhân viên CSKH` và `Vận hành kỹ thuật (Operator)`.

---

## 2. Ma Trận Kiểm Thử Hành Trình E2E Đầy Đủ (`F01`–`F30`, `P4.md §13.1`)

Toàn bộ 30 ca chức năng cốt lõi được ánh xạ trực tiếp tới route/API thực tế của `492423c` và bộ test tự động nền tảng (`P1`–`P3`):

| Mã | Kịch bản kiểm thử | Vai trò thực hiện | Route / API kiểm chứng | Kết quả kỳ vọng bắt buộc | Đối chiếu Auto-Test nền (`92e14af`) | Kết quả Staging (`P4-08`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :---: |
| **F01** | Staff đăng nhập, xem trang chủ tác nghiệp | `staff.a1` (`Sales User`) | `/cskh/my-customers`, `/crm` | Chỉ hiển thị menu tác nghiệp cá nhân và danh sách khách đang giao cho `staff.a1`. | `test_ui_p1_endpoints.py` (`PASS` CI) | `BLOCKED` |
| **F02** | BOD đăng nhập xem quản trị | `bod.uat` (`CSKH BOD Viewer`) | `/cskh/quality-overview` | Xem tổng hợp toàn công ty `0` PII thô; **không** có quyền `System Manager` hay cấu hình hệ thống. | `test_p3_reporting_and_security.py` (`PASS` CI) | `BLOCKED` |
| **F03** | Import CSV chỉ có số điện thoại (`phone-only`) | `admin.cskh` (`CSKH CRM Admin`) | `/cskh/import`, `imports.*` | Chuẩn hóa SĐT, tạo/ghép `CRM Lead` với `cskh_is_placeholder_name=1`, không đòi tên thật. | `test_p1_imports.py` (`PASS` CI) | `BLOCKED` |
| **F04** | Import tệp có `0...`, `+84...`, `84...` và khoảng trắng | `admin.cskh` | `/cskh/import` | Quy đổi về duy nhất 1 khóa `normalized_phone` trên `CSKH Identity Index`. | `test_p1_identity.py` (`PASS` CI) | `BLOCKED` |
| **F05** | Import trùng nội bộ tệp và trùng DB hiện có | `admin.cskh` | `/cskh/import` | Không nhân đôi `CRM Lead`; hiển thị rõ thống kê dòng hợp lệ / dòng trùng / lý do loại. | `test_p1_imports.py` (`PASS` CI) | `BLOCKED` |
| **F06** | Import tệp sai định dạng hoặc vượt giới hạn | `admin.cskh` | `/cskh/import` | Báo lỗi tiếng Việt rõ ràng; không ghi dở dang bản ghi lỗi vào kho chính. | `test_p1_imports.py` (`PASS` CI) | `BLOCKED` |
| **F07** | Xem trước phân phối trọng số `50/30/20` trên lô `1.000` | `admin.cskh` | `/cskh/campaigns`, `distribution.preview` | Phân bổ chuẩn `500 / 300 / 200` khi không bị chặn trần `capacity` hoặc trạng thái nghỉ. | `test_p1_distribution.py` (`PASS` CI) | `BLOCKED` |
| **F08** | Phân phối liên tiếp nhiều lô nhỏ | `admin.cskh` | `distribution.execute` | Thuật toán weighted fairness có nhớ lịch sử giữ tỷ lệ lũy kế bám sát trọng số mục tiêu. | `test_p1_distribution.py` (`PASS` CI) | `BLOCKED` |
| **F09** | Nhân viên bị tắt nhận số hoặc chạm trần `capacity` | `admin.cskh` | `/cskh/campaigns` | Không giao đầu việc mới cho `staff.capped` / tài khoản `enabled=0`. | `test_p1_distribution.py` (`PASS` CI) | `BLOCKED` |
| **F10** | Mạng chập chờn / client gửi lại lệnh `execute` | `admin.cskh` | `distribution.execute` | Khóa idempotency giữ nguyên kết quả lần đầu, không tạo lượt giao thứ hai. | `test_p1_concurrency.py` (`PASS` CI) | `BLOCKED` |
| **F11** | Hai Admin bấm thực thi phân phối cạnh tranh | `admin.cskh` ×2 | `distribution.execute` | Ràng buộc khóa DB & `CSKH Active Claim` giữ đúng duy nhất 1 owner active cho mỗi khách. | `test_p1_concurrency.py` (`PASS` CI) | `BLOCKED` |
| **F12** | Staff A nhận khách mới từ Admin | `staff.a1` | `/cskh/my-customers`, `/cskh/work-item` | Thấy ngay `CSKH Work Item` ở trạng thái `Mới giao`, hạn `FIRST_ATTEMPT` và lịch sử giao. | `test_ui_p2_pages_and_context.py` (`PASS` CI) | `BLOCKED` |
| **F13** | Staff A ghi cuộc gọi `Không nghe máy` (`no_answer`) | `staff.a1` | `workflow.record_attempt` | Lưu `CSKH Contact Attempt`, tăng `attempt_count`, **không** tăng `connected_attempt_count`. | `test_p2_outcomes_policy.py` (`PASS` CI) | `BLOCKED` |
| **F14** | Staff A ghi cuộc gọi `Đã trao đổi — Quan tâm` | `staff.a1` | `workflow.record_attempt` | Lưu đúng kết quả, `is_connected=1`, đúng người thực hiện từ phiên và thời điểm chuẩn. | `test_p2_lifecycle.py` (`PASS` CI) | `BLOCKED` |
| **F15** | Staff A ghi liên hệ qua kênh Zalo / SMS / Facebook | `staff.a1` | `workflow.record_attempt` | Lưu đúng `channel` (`zalo`/`sms`/`facebook`), không tạo bản ghi `CRM Call Log` giả. | `test_p2_calllog_followup.py` (`PASS` CI) | `BLOCKED` |
| **F16** | Cùng một cuộc gọi có cả `CRM Call Log` và `Attempt` | `staff.a1` | `workflow.record_attempt`, KPI engine | Liên kết 1-1 qua trường `call_log`, tuyệt đối không đếm đôi vào KPI. | `test_p2_calllog_followup.py` (`PASS` CI) | `BLOCKED` |
| **F17** | Staff A đặt lịch hẹn chăm sóc lại (`callback_requested`) | `staff.a1` | `/cskh/callbacks`, `CRM Task` | Tạo `CRM Task` (`cskh_is_followup=1`) đúng `staff.a1`, đúng hạn, chuyển đầu việc sang `on_hold`. | `test_p2_calllog_followup.py` (`PASS` CI) | `BLOCKED` |
| **F18** | Dời lịch hẹn với lý do hợp lệ | `staff.a1` | `/cskh/callbacks` | Tăng `cskh_reschedule_count`, lưu vết `cskh_task_history_json`, giữ nguyên `original_due_at` trên SLA. | `test_p3_sla_and_attribution.py` (`PASS` CI) | `BLOCKED` |
| **F19** | Lịch hẹn quá hạn và `scheduler` chạy lặp lại nhiều chu kỳ | Hệ thống (`scheduler`) | `evaluate_sla_obligations` | Đánh dấu vi phạm đúng 1 lần, chạy lại không nhân đôi lỗi vi phạm hay thông báo. | `test_p3_sla_and_attribution.py` (`PASS` CI) | `BLOCKED` |
| **F20** | Bàn giao `Staff A → Staff B` khi đang có lịch hẹn mở | `admin.cskh`, `staff.a1`, `staff.b1` | `ownership.transfer` | Quyền sở hữu và `CRM Task` chuyển đồng bộ sang `staff.b1`; `staff.a1` mất quyền đọc/sửa chi tiết ngay. | `test_p2_permissions_sec.py` (`PASS` CI) | `BLOCKED` |
| **F21** | Bàn giao vòng lặp `A → B → A` | `admin.cskh` | `/cskh/employee-quality` | Tách đúng 3 khoảng phụ trách (`Ownership Interval`) độc lập, bảo toàn quy gán lịch sử từng chặng. | `test_p3_sla_and_attribution.py` (`PASS` CI) | `BLOCKED` |
| **F22** | Đóng `CSKH Work Item` theo kết quả đầu cuối | `staff.b1` | `workflow.close_work_item` | Chỉ cho đóng khi thỏa điều kiện `CSKH Policy` (hoặc mã kết quả kết thúc hợp lệ); giữ nguyên lịch sử. | `test_p2_lifecycle.py` (`PASS` CI) | `BLOCKED` |
| **F23** | Hạn `FIRST_ATTEMPT` rơi qua đêm, cuối tuần và ngày nghỉ lễ | Hệ thống | `CSKH Working Calendar` | Hạn `effective_due_at` cộng tịnh tiến đúng phút làm việc thực tế theo lịch chuẩn `Asia/Ho_Chi_Minh`. | `test_p3_calendar.py` (`PASS` CI) | `BLOCKED` |
| **F24** | Chạy tính lại KPI (`recompute`) 2 lần trên cùng dữ liệu đầu vào | `admin.cskh` | `recompute_kpi` | Số liệu trùng khớp 100%, quản lý `is_latest_published` và lịch sử `CSKH KPI Run` đúng hợp đồng. | `test_p3_kpi_and_snapshots.py` (`PASS` CI) | `BLOCKED` |
| **F25** | Đối soát KPI theo Ngày / Tuần / Tháng / Quý / Năm | `bod.uat` | `/cskh/quality-overview` | Số `Đầu việc duy nhất` cấp Tháng/Quý tính `DISTINCT` chính xác, không cộng gộp thô các ngày. | `test_p3_kpi_and_snapshots.py` (`PASS` CI) | `BLOCKED` |
| **F26** | Trạng thái KPI `N/A` / `Chưa đủ dữ liệu` / `Tạm tính (86,47)` | `bod.uat`, `staff.a1` | `/cskh/employee-quality`, `/cskh/scorecard` | Mẫu số `0` hiển thị `N/A`; tắt QA hiển thị `Tạm tính; thiếu mẫu QA` (`86,47`), không tính `0%` hay `100%` giả. | `test_p3_kpi_and_snapshots.py` (`PASS` CI) | `BLOCKED` |
| **F27** | Bấm truy vết (`drill-down`) từ thẻ KPI xuống danh sách gốc | `bod.uat`, `leader.a` | `p3_reporting.get_metric_drilldown` | Tổng số dòng Đạt + Không đạt khớp tuyệt đối tử số/mẫu số thẻ KPI; BOD bị che sạch PII thô. | `test_p3_reporting_and_security.py` (`PASS` CI) | `BLOCKED` |
| **F28** | Trưởng nhóm A xem đội A và thử đổi bộ lọc sang đội B | `leader.a` (`Sales Manager`) | `/cskh/team-quality` | Xem đầy đủ Đội A; đổi URL/API sang `TEAM-B` nhận `403 FORBIDDEN_SCOPE`, không rò tổng hợp đội B. | `test_p3_reporting_and_security.py` (`PASS` CI) | `BLOCKED` |
| **F29** | Xuất tệp báo cáo được phép & kiểm tra hết hạn / thu hồi | `bod.uat`, `leader.a` | `CSKH Export Job` | Ghi kiểm toán, trung hòa công thức CSV (`=,+,-,@`), chỉ tải được trong phạm vi quyền và trước `expires_at`. | `test_p3_t1c_remediation.py` (`PASS` CI) | `BLOCKED` |
| **F30** | Mở đồng thời nhiều tab trình duyệt & khóa tài khoản đang mở phiên | `staff.a1`, `staff.locked` | Session / WebSocket | Nhiều tab của user hợp lệ hoạt động ổn định; ngay khi `User.enabled=0`, phiên cũ bị từ chối tức thì. | `test_security_remediation_v2.py` (`PASS` CI) | `BLOCKED` |

---

## 3. Danh Sách Ca Nghiệm Thu UAT Theo Từng Vai Trò (`P4.md §16.2–§16.3`)

Mỗi ca UAT dưới đây được thực hiện bởi người dùng thật đại diện vai trò trên Staging HTTPS và ghi đầy đủ vào biểu mẫu ký duyệt tại Mục 6:

### 3.1. Vai trò 1 — Ban Giám Đốc (`BOD` · Role `CSKH BOD Viewer`)

| Mã Ca UAT | Thao tác thực nghiệm của BOD | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-BOD-01`** | Đăng nhập tài khoản BOD riêng (không có `System Manager`), duyệt qua `/cskh/dashboard` (kho & phân phối P1) và 5 màn chất lượng P3 (`/cskh/quality-overview`, `team-quality`, `employee-quality`, `campaign-quality`, `sla-operations`). | Xem trọn vẹn số liệu toàn công ty; thử mở `/app` cấu hình hệ thống hoặc `/cskh/work-item` chi tiết cá nhân bị từ chối `403`. | `BLOCKED` |
| **`UAT-P4-BOD-02`** | Kiểm tra một chiến dịch cụ thể từ tổng số khách nhập → đã phân phối → chưa phân phối → tỷ lệ tiếp cận và kết nối theo đội/nhân viên (`P4.md §0.1`). | Con số nhất quán xuyên suốt từ màn chiến dịch sang màn đội và nhân viên; phân biệt rõ `Đã thử liên hệ`, `Đã kết nối` và `Chuyển đổi`. | `BLOCKED` |
| **`UAT-P4-BOD-03`** | Bấm `Công thức & Nguồn` và bấm `Drill-down` trên ít nhất 5 chỉ số trọng yếu (`Liên hệ lần đầu đúng hạn`, `Chăm sóc đúng lịch hẹn`, `Tỷ lệ kết nối`, `Cập nhật kết quả đầy đủ`, `Tuân thủ DNC`). | Đủ 9 thuộc tính giải thích tiếng Việt; tổng dòng danh sách truy vết khớp chính xác tử/mẫu số; **`0` số điện thoại thô hay ghi chú nhạy cảm bị lộ**. | `BLOCKED` |
| **`UAT-P4-BOD-04`** | Đối chiếu hiển thị điểm `Tạm tính` (vector chuẩn `86,47` khi tắt QA), ô `N/A` khi mẫu số bằng `0`, tách nhóm xếp hạng và tỷ lệ `Nhân viên tự báo cáo` vs `Đã xác minh`. | Không ghép chung bảng xếp hạng `Tạm tính` với `Đã chốt`; không hiển thị `0%` giả; không biến dữ liệu tự khai thành đã xác minh. | `BLOCKED` |
| **`UAT-P4-BOD-05`** | Kiểm tra ca nhân sự chuyển đội giữa tháng (`Team A → Team B`), kỳ báo cáo `Đã tính lại kỳ cũ (Restated)` và xuất báo cáo tổng hợp CSV/XLSX. | Dữ liệu lịch sử giữ đúng `team-as-of`; tệp xuất tải về mở tốt tiếng Việt có dấu, không chứa SĐT thô và tự khóa sau khi hết hạn. | `BLOCKED` |

### 3.2. Vai trò 2 — Quản Trị Viên Nghiệp Vụ (`Admin CRM` · Role `CSKH CRM Admin`)

| Mã Ca UAT | Thao tác thực nghiệm của Admin nghiệp vụ | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-ADM-01`** | Tải lên tệp CSV hỗn hợp (chỉ có SĐT, đầu `0`/`+84`, khoảng trắng, trùng nội tệp, trùng kho cũ, dòng lỗi định dạng) tại `/cskh/import`. | Hệ thống chuẩn hóa về một danh tính duy nhất, gắn cờ tên tạm cho khách chỉ có SĐT, hiển thị bảng kết quả đối chiếu rõ ràng tiếng Việt. | `BLOCKED` |
| **`UAT-P4-ADM-02`** | Tạo chiến dịch, cấu hình trọng số `50/30/20`, gắn 1 nhân viên chạm trần/tạm ngưng, chạy `Xem trước phân phối` rồi `Thực thi phân phối`. | Bản xem trước hiển thị đúng số lượng dự kiến, loại trừ nhân viên không đủ điều kiện; bấm thực thi giao việc chính xác và lưu nhật ký kiểm toán. | `BLOCKED` |
| **`UAT-P4-ADM-03`** | Thực hiện `Điều chuyển` một đầu việc đang có lịch hẹn từ `Nhân viên A` sang `Nhân viên B` và `Thu hồi` một đầu việc chưa xử lý. | `Nhân viên B` nhận cả đầu việc lẫn `CRM Task` hẹn lại; `Nhân viên A` mất ngay quyền mở hồ sơ/tệp đính kèm; lịch sử bàn giao ghi đủ người thực hiện & lý do. | `BLOCKED` |
| **`UAT-P4-ADM-04`** | Kích hoạt `Yêu cầu tính lại KPI` có nhập lý do kiểm toán cho một kỳ vừa có duyệt hiệu chỉnh kết quả. | Tác vụ chạy nền không làm đơ trình duyệt; sau khi hoàn tất, bản chụp mới gắn nhãn `Đã tính lại kỳ cũ` và giữ nguyên bản chụp trước để truy vết. | `BLOCKED` |

### 3.3. Vai trò 3 — Trưởng Nhóm CSKH (`Team Leader A & B` · Role `Sales Manager`)

| Mã Ca UAT | Thao tác thực nghiệm của Trưởng nhóm | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-LDR-01`** | `Trưởng nhóm A` đăng nhập xem `/cskh/progress`, `/cskh/sla-operations`, `/cskh/team-quality` và `/cskh/employee-quality`. | Thấy đầy đủ danh sách đầu việc sắp quá hạn/đã quá hạn và bảng điểm nhân sự thuộc `Đội A`; giải thích được nguyên nhân điểm từng thành viên. | `BLOCKED` |
| **`UAT-P4-LDR-02`** | `Trưởng nhóm A` cố đổi tham số lọc/URL sang `Đội B` (`team=TEAM-B`, `user=staff.b1`) và `Trưởng nhóm B` thử mở hồ sơ đã đóng của nhân viên chuyển từ `Đội A` sang. | Hệ thống từ chối `403` bằng thông báo chuẩn hóa, không lộ bất kỳ con số tổng hợp hay SĐT/ghi chú lịch sử của đội kia. | `BLOCKED` |
| **`UAT-P4-LDR-03`** | Duyệt một yêu cầu hiệu chỉnh lần liên hệ (`CSKH Attempt Correction`) và thực hiện miễn trừ SLA (`waive`) có lý do kiểm toán trong phạm vi chính sách cho phép. | Hành động lưu đầy đủ người duyệt, thời gian, lý do; tự động đánh dấu `CSKH Dirty Scope` để cập nhật lại chỉ số liên quan. | `BLOCKED` |

### 3.4. Vai trò 4 — Nhân Viên Tuyển Dụng / CSKH (`Staff` · Role `Sales User`)

| Mã Ca UAT | Thao tác thực nghiệm của Nhân viên | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-STF-01`** | Mở `/cskh/my-customers`, tìm kiếm/lọc khách đang phụ trách, mở `/cskh/work-item` xem thông tin liên hệ và hạn SLA lần đầu. | Tốc độ phản hồi mượt, hiển thị đúng tiếng Việt và giờ Việt Nam; chỉ thấy khách được giao cho chính mình. | `BLOCKED` |
| **`UAT-P4-STF-02`** | Ghi nhận lần lượt: (a) cuộc gọi `Không nghe máy`, (b) tin nhắn Zalo `Đã gửi tin nhắn, chưa phản hồi`, (c) cuộc gọi `Khách hẹn gọi / liên hệ lại` kèm đặt lịch hẹn, (d) dời lịch hẹn có lý do. | Các mục (a)/(b) chỉ tính là đã thử liên hệ (không tính đã kết nối); mục (c) tạo đúng lịch hẹn trên `/cskh/callbacks`; mục (d) lưu lịch sử dời hẹn. | `BLOCKED` |
| **`UAT-P4-STF-03`** | Ghi nhận kết quả kết thúc hợp lệ (`Hoàn tất mục tiêu chiến dịch` và `Yêu cầu ngừng liên hệ (DNC)`), đóng đầu việc và kiểm tra `/cskh/scorecard`. | Đầu việc đóng đúng quy tắc; khách DNC bị khóa liên hệ tiếp; bảng điểm cá nhân giải thích rõ từng thành phần tử/mẫu số và nhãn `Tạm tính`. | `BLOCKED` |
| **`UAT-P4-STF-04`** | Sau khi Admin chuyển một khách từ mình sang đồng nghiệp, bấm lại đường dẫn cũ `/cskh/work-item?id=...` và xem lại `/cskh/scorecard`. | Đường dẫn chi tiết cũ trả `403` (`0` lộ SĐT/ghi chú mới); bảng điểm cá nhân vẫn bảo toàn công bằng phần thống kê của khoảng thời gian mình từng phụ trách. | `BLOCKED` |

### 3.5. Vai trò 5 — Quản Trị Kỹ Thuật / Vận Hành (`Operator` · Role `System Manager` + Host Ops)

| Mã Ca UAT | Thao tác thực nghiệm của Người vận hành | Tiêu chí đạt (`PASS`) | Trạng thái |
| :--- | :--- | :--- | :---: |
| **`UAT-P4-OPS-01`** | Chạy `check_health_and_invariants.py`, kiểm tra hàng đợi RQ/scheduler và kích hoạt `--send-test-alert`. | Toàn bộ 10 service `healthy`, 6 bất biến nghiệp vụ `PASS`, nhận được cảnh báo thử thực tế trên kênh trực kèm mã kiểm chứng. | `BLOCKED` |
| **`UAT-P4-OPS-02`** | Thực hành quy trình khóa nhân sự nghỉ việc (`Offboarding` theo [`operations-sop.md`](./operations-sop.md)) trên tài khoản giả lập `staff.locked`. | Phiên đăng nhập đang mở bị vô hiệu ngay, ngừng chia số mới và thu hồi sạch đầu việc/tệp xuất cũ. | `BLOCKED` |
| **`UAT-P4-OPS-03`** | Thực hành bật/tắt cờ khẩn cấp Cấp độ 1A (`cskh_p3_enabled=false` → `true`), diễn tập lùi phiên bản digest Cấp độ 2 và diễn tập khôi phục DR Cấp độ 4. | Đúng thời gian dự kiến trong [`rollback-runbook.md`](./rollback-runbook.md), không làm gián đoạn luồng P1/P2 khi tắt cờ P3, đối soát dữ liệu sau khôi phục khớp 100%. | `BLOCKED` |

---

## 4. Checklist Kiểm Tra Việt Hóa & Accessibility Trên 10 Màn Hình Trọng Yếu (`P4.md §12.2`, `G20`)

- [ ] **1. Đăng nhập / Đổi mật khẩu (`/login`):** Nhãn ô nhập, thông báo sai mật khẩu và thông báo tài khoản bị khóa hiển thị tiếng Việt chuẩn.
- [ ] **2. Nhập dữ liệu (`/cskh/import`):** Nhãn tải tệp, cảnh báo lỗi CSV, lý do trùng lặp, bảng xem trước và xác nhận hoàn tất tiếng Việt rõ nghĩa.
- [ ] **3. Chiến dịch & Chia số (`/cskh/campaigns`, `/cskh/pool`):** Tỷ lệ trọng số `%`, trần nhận, nút xem trước/thực thi/thu hồi/chuyển giao không tràn vỡ trên màn `1366×768`.
- [ ] **4. Khách được giao (`/cskh/my-customers`):** Thanh tìm kiếm, bộ lọc trạng thái, trạng thái đang tải / trống / lỗi đều tiếng Việt tự nhiên (không có từ *"Chì"*).
- [ ] **5. Ghi nhận liên hệ (`/cskh/work-item`):** Đủ 5 kênh liên hệ, 12 mã kết quả tiếng Việt, kiểm tra lỗi thiếu ghi chú/thiếu lịch hẹn đọc dễ hiểu.
- [ ] **6. Lịch hẹn chăm sóc lại (`/cskh/callbacks`):** Hạn giờ `Asia/Ho_Chi_Minh`, lịch sử dời hẹn và nhãn `Quá hạn` có cả chữ lẫn màu tương phản tốt.
- [ ] **7. Dashboard BOD & Trưởng nhóm (`/cskh/dashboard`, `/cskh/quality-overview`, `team-quality`, `employee-quality`, `campaign-quality`, `sla-operations`):** Tử/mẫu số rõ ràng, hộp giải thích 9 thuộc tính, tách bạch `Tạm tính` / `Đã chốt` / `N/A`.
- [ ] **8. Bảng điểm cá nhân (`/cskh/scorecard`):** Hiển thị rõ điểm thô và điểm chuẩn hóa `0–100`, không gây hiểu nhầm thiệt điểm khi tắt QA.
- [ ] **9. Xuất báo cáo (`CSKH Export Job`):** Cảnh báo phạm vi, trạng thái xử lý nền, thời hạn tải và thông báo từ chối khi hết quyền đều bằng tiếng Việt.
- [ ] **10. Điều hướng bàn phím & Tiêu điểm (`Keyboard & Focus`):** Nhấn `Tab` đi tuần tự qua thanh điều hướng, bộ lọc, bảng và hộp thoại; nhấn `Escape` đóng modal an toàn.

---

## 5. Tài Liệu Hướng Dẫn Sử Dụng Ngắn Gọn Theo Vai Trò (`P4.md §16.4`)

### 5.1. Dành cho Nhân viên CSKH (`Staff`)
1. **Nhận việc đầu ca:** Mở `/cskh/my-customers` để xem các đầu việc trạng thái **Mới giao** (ưu tiên xử lý trước mốc *Hạn liên hệ lần đầu*) và mở `/cskh/callbacks` để kiểm tra các lịch hẹn đến hạn trong ngày.
2. **Ghi nhận trung thực từng lần liên hệ:** Mở đầu việc → chọn đúng kênh thực tế (`Điện thoại`, `Zalo`, `SMS`, `Facebook`, `Khác`) → chọn đúng 1 trong 12 kết quả. Lưu ý: nếu khách không bắt máy hoặc mới gửi tin Zalo chưa thấy trả lời, chọn đúng **Không nghe máy** / **Đã gửi tin nhắn, chưa phản hồi** (hệ thống ghi nhận bạn đã thực hiện nghĩa vụ liên hệ lần đầu đúng hạn, tuyệt đối không chọn bừa kết quả *Đã trao đổi*).
3. **Đặt lịch hẹn & Hoàn tất:** Khi khách bận hoặc cần tư vấn tiếp, chọn kết quả tương ứng và đặt giờ hẹn cụ thể. Chỉ bấm đóng đầu việc khi đạt mục tiêu chiến dịch, khách từ chối rõ ràng, sai số hoặc khách yêu cầu ngừng liên hệ (`DNC`). Theo dõi kết quả của bản thân tại `/cskh/scorecard`.

### 5.2. Dành cho Trưởng nhóm (`Team Leader`)
1. **Giám sát trong ngày:** Kiểm tra `/cskh/sla-operations` và `/cskh/progress` đầu giờ sáng và đầu giờ chiều để hỗ trợ ngay các đầu việc sắp quá hạn liên hệ đầu hoặc quá hạn lịch hẹn trong đội.
2. **Đánh giá chất lượng đội:** Mở `/cskh/team-quality` và `/cskh/employee-quality` theo kỳ; bấm vào từng chỉ số để xem danh sách gốc và rà soát các yêu cầu hiệu chỉnh lần liên hệ của nhân viên trong phạm vi chính sách cho phép.

### 5.3. Dành cho Quản trị viên nghiệp vụ (`Admin CRM`)
1. **Nhập kho & Làm sạch:** Vào `/cskh/import` tải tệp CSV chuẩn → kiểm tra kỹ bảng báo cáo số hợp lệ / số trùng / dòng lỗi trước khi xác nhận.
2. **Chia số & Điều phối:** Cấu hình chiến dịch và quy tắc trọng số tại `/cskh/campaigns` → **luôn chạy Xem trước (`Preview`)** để kiểm tra tỷ lệ và nhân sự nghỉ/đạt trần trước khi bấm **Thực thi (`Execute`)**. Mọi thao tác thu hồi hoặc chuyển người phụ trách bắt buộc thực hiện qua nút chức năng chuẩn của màn CSKH (không sửa trực tiếp trường chủ sở hữu thô) để hệ thống đồng bộ lịch hẹn và thu hồi quyền cũ.

### 5.4. Dành cho Ban Giám đốc (`BOD`)
1. **Theo dõi kho & Phân phối:** Xem `/cskh/dashboard` để nắm tổng nguồn khách vào, tiến độ chia số và tồn đọng theo chiến dịch.
2. **Đọc Dashboard Chất lượng (`/cskh/quality-overview`):** Hệ thống tách 3 trụ cột độc lập: *Tuân thủ quy trình & SLA*, *Hiệu quả kết nối/chuyển đổi* và *Độ tin cậy dữ liệu*. Lưu ý phân biệt số liệu **Nhân viên tự báo cáo** với **Đã xác minh**, nhãn **Tạm tính** (khi chưa bật chấm mẫu QA) và nhãn **N/A** (khi chưa phát sinh mẫu đến hạn). Bấm trực tiếp vào con số để đối chiếu danh sách truy vết đã che thông tin nhạy cảm.

### 5.5. Dành cho Người vận hành kỹ thuật (`Operator`)
1. **Kiểm tra hằng ngày:** Xem báo cáo của `deploy/ops/check_health_and_invariants.py` (10 container `healthy`, dung lượng đĩa `< 80%`, hàng đợi RQ không tồn job lỗi, 6 bất biến nghiệp vụ `PASS`) và xác nhận bản backup mã hóa đêm trước đã lên kho ngoài VPS.
2. **Phát hành, Khóa tài khoản & Ứng phó sự cố:** Thực hiện đúng từng bước có điểm dừng trong [`release-runbook.md`](./release-runbook.md), [`rollback-runbook.md`](./rollback-runbook.md) và [`operations-sop.md`](./operations-sop.md); tuyệt đối không sửa mã nguồn hay chạy SQL xóa/sửa tay trên VPS.

---

## 6. Biểu Mẫu Ký Nghiệm Thu UAT & Cổng `G13`–`G16`, `G20`, `G22`

> **Trạng thái hiện tại:** **`BLOCKED`** — Chờ hoàn tất `P4-03` (Staging digest), `P4-04`..`P4-06` và chỉ định người nghiệm thu (`BLK-GOV-06`). Không điền chữ ký giả.

| Vai trò nghiệm thu | Họ tên / Tài khoản (`BLK-GOV-06`) | Phạm vi ca đã chạy | Digest Image kiểm thử | Kết quả | Ngày ký & Xác nhận |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Ban Giám đốc (`BOD`)** | `PENDING (BLK-GOV-06)` | `F02`, `F25..F27`, `F29`, `UAT-P4-BOD-01..05` | `PENDING (BLK-INFRA-03)` | **`BLOCKED`** | Chờ Staging & UAT |
| **Admin nghiệp vụ (`CRM Admin`)** | `PENDING (BLK-GOV-06)` | `F03..F11`, `F20..F21`, `F24`, `UAT-P4-ADM-01..04` | `PENDING (BLK-INFRA-03)` | **`BLOCKED`** | Chờ Staging & UAT |
| **Trưởng nhóm Đội A & Đội B** | `PENDING (BLK-GOV-06)` | `F27..F29`, `UAT-P4-LDR-01..03` | `PENDING (BLK-INFRA-03)` | **`BLOCKED`** | Chờ Staging & UAT |
| **Nhân viên CSKH đại diện** | `PENDING (BLK-GOV-06)` | `F01`, `F12..F20`, `F22`, `F30`, `UAT-P4-STF-01..04` | `PENDING (BLK-INFRA-03)` | **`BLOCKED`** | Chờ Staging & UAT |
| **Vận hành kỹ thuật (`Operator`)** | `PENDING (BLK-GOV-06)` | `UAT-P4-OPS-01..03`, Đào tạo & Bàn giao | `PENDING (BLK-INFRA-03)` | **`BLOCKED`** | Chờ Staging & UAT |
