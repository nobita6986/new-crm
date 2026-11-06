# Quy Trình Vận Hành Chuẩn Sau Go-Live & Hồ Sơ Bàn Giao Vận Hành (`P4-11` — Cổng `G24`)

- **Mã tài liệu:** `P4-OPERATIONS-SOP` (`docs/p4/operations-sop.md` — `P4.md §3.3`, `§17`, `§20.2`)
- **Đầu vào khóa cứng:** Hồ sơ `P4-01` tại `492423c` (`deploy/compose/*`), trình thăm dò [`deploy/ops/check_health_and_invariants.py`](../../deploy/ops/check_health_and_invariants.py) và sổ tay [`release-runbook.md`](./release-runbook.md) / [`rollback-runbook.md`](./rollback-runbook.md)
- **Trạng thái ký bàn giao vận hành (`G24`) hiện tại:** **`BLOCKED`** *(Tài liệu SOP đã hoàn chỉnh; bước bàn giao tài khoản, quyền hạn thực tế và ký nhận với Người vận hành đang chờ `BLK-GOV-06` và hoàn tất Go-Live `G23`)*.

---

## 1. Lịch Kiểm Tra Vận Hành Định Kỳ (`P4.md §17.1`)

| Chu kỳ | Danh mục kiểm tra cụ thể | Lệnh / Màn hình chuẩn | Vai trò chịu trách nhiệm |
| :--- | :--- | :--- | :--- |
| **Hằng ngày (Đầu giờ sáng)** | 1. 10 container thường trực `healthy`, `0` `OOMKilled`, ổ đĩa `< 80%`.<br>2. Hàng đợi RQ `failed == 0`, 4 cron `crm_cskh` chạy đúng nhịp.<br>3. Cả 6 bất biến nghiệp vụ (`INV-01`..`INV-06`) đạt `PASS`.<br>4. Bản backup mã hóa đêm trước đã đẩy ra ngoài VPS & khớp SHA-256. | `python3 deploy/ops/check_health_and_invariants.py --env-file /etc/crm-cskh/production.env --mode tls --environment-label production` + kiểm tra manifest backup `P4-05` | **Operator kỹ thuật** (`BLK-GOV-06`) |
| **Hằng ngày (Nghiệp vụ)** | 1. Dòng lỗi khi import CSV và số lượng khách chờ phân phối.<br>2. Danh sách đầu việc sắp vi phạm / đã vi phạm SLA liên hệ đầu & lịch hẹn.<br>3. Độ tươi (`as_of`) của bảng chỉ số KPI ngày hiện tại. | `/cskh/import`, `/cskh/pool`, `/cskh/sla-operations`, `/cskh/quality-overview` | **Admin nghiệp vụ & Trưởng nhóm** |
| **Hằng tuần** | 1. Rà soát nhật ký lỗi ứng dụng (`Error Log`), các truy vấn MariaDB chậm.<br>2. Rà soát nhật ký thay đổi phân quyền, xuất báo cáo (`CSKH Export Job`) và yêu cầu tính lại KPI.<br>3. Lấy mẫu đối soát ngẫu nhiên 5 đầu việc từ Dashboard xuống lịch sử gốc. | Báo cáo tuần từ `check_health_and_invariants.py` + kiểm toán DocType quản trị | **Tech Owner + Admin nghiệp vụ** |
| **Hằng tháng** | 1. **Diễn tập khôi phục (`Restore Drill`)** bản backup mã hóa mới nhất lên site DR cô lập (`P4.md §7.2`).<br>2. Rà soát danh sách tài khoản đang hoạt động so với danh sách nhân sự thực tế.<br>3. Đánh giá xu hướng tăng trưởng dung lượng DB/files và bản vá bảo mật OS/image. | Quy trình DR của `P4-05` + kiểm tra user active | **Operator + Tech Owner** |
| **Trước mỗi bản phát hành** | Chạy trọn bộ hồi quy trên Staging cùng digest, sao lưu pre-release, đánh giá phân loại migration (`Class A/B/C/D`) và ký duyệt `GO`. | [`release-runbook.md`](./release-runbook.md) | **Release Owner** |

---

## 2. Phân Loại Và Ứng Phó Sự Cố (`P4.md §17.2`)

> **Lưu ý minh bạch nguồn lực:** Công ty phê duyệt khung giờ trực và thời gian phản hồi thực tế khi chốt `BLK-GOV-06`; tài liệu này không mặc định cam kết trực `24/7` khi chưa bố trí ca trực.

| Mức độ | Dấu hiệu nhận diện | Hành động cô lập đầu tiên (trong 15 phút đầu sau khi tiếp nhận) | Lộ trình leo thang |
| :--- | :--- | :--- | :--- |
| **`SEV-1` (Khẩn cấp)** | - Phát hiện lộ SĐT/ghi chú/tệp đính kèm chéo quyền.<br>- Vi phạm `INV-01` (nhiều owner active / lệch chủ sở hữu hàng loạt) hoặc `INV-02`.<br>- Mất kết nối MariaDB, hỏng dữ liệu hoặc nghi ngờ lộ tài khoản quản trị. | 1. Giữ nguyên bằng chứng log.<br>2. Khóa ngay tính năng gây lộ (tắt `cskh_p3_export_enabled` / `cskh_p3_enabled` hoặc bật `maintenance_mode` nếu lộ ở lõi P1/P2).<br>3. Thu hồi phiên người dùng liên quan nếu nghi lộ tài khoản. | Báo ngay **Tech Owner + BOD/Owner**; kích hoạt [`rollback-runbook.md`](./rollback-runbook.md) hoặc Quy trình Sự cố Dữ liệu Mục 3. |
| **`SEV-2` (Cao)** | - `scheduler` hoặc worker `queue-short`/`queue-long` ngừng chạy; vi phạm `INV-03`..`INV-06`.<br>- Dashboard KPI sai lệch diện rộng hoặc chức năng import/phân phối lỗi.<br>- Ổ đĩa vượt `80%` hoặc backup đêm thất bại. | 1. Ưu tiên bảo toàn dữ liệu gốc.<br>2. Nếu lỗi ở báo cáo/SLA P3, tạm tắt cờ P3 tương ứng để nhân viên vẫn làm việc bình thường ở P1/P2.<br>3. Khởi động lại worker bị treo / giải phóng dung lượng tạm an toàn. | Báo **Tech Owner** xử lý trong ngày làm việc; lập PR sửa lỗi có kiểm thử. |
| **`SEV-3` (Thông thường)** | - Lỗi hiển thị nhỏ trên giao diện phụ, nhãn dịch chưa tự nhiên không làm sai số liệu hay quyền hạn. | Ghi nhận phiếu lỗi kèm ảnh chụp màn hình đã che PII. | Đưa vào nhánh sửa lỗi và phát hành theo chu kỳ chuẩn. |

---

## 3. Quy Trình Xử Lý Sự Cố Dữ Liệu 6 Bước — Cấm Sửa Tay Lịch Sử (`P4.md §17.3`)

Khi phát hiện sai lệch dữ liệu nghiệp vụ (phân phối, lần liên hệ, hạn SLA hoặc điểm KPI):

1. **Cô lập & Khoanh vùng thời gian:** Tạm dừng tác vụ nền gây sai lệch (nếu còn đang diễn tiến) và ghi nhận chính xác khoảng thời gian `[t_start, t_end]` cùng phiên bản image digest.
2. **Xác định tập bản ghi ảnh hưởng (Chỉ đọc):** Truy vấn danh sách ID của `CRM Lead`, `CSKH Work Item`, `CSKH Assignment History`, `CSKH Contact Attempt`, `CRM Task`, `CSKH KPI Run` liên quan.
3. **Xuất bảng đối chiếu từ nguồn sự thật:** Lưu kết quả đối chiếu ra tệp kiểm toán nội bộ; **tuyệt đối không chạy câu lệnh `UPDATE`/`DELETE` trực tiếp sửa lịch sử trong MariaDB**.
4. **Thực thi hiệu chỉnh có kiểm toán (`Audited Correction / Recompute`):**
   - Với lần liên hệ ghi nhầm: dùng luồng chuẩn `CSKH Attempt Correction` (`correct` / `void` / `restore`) có người yêu cầu, người duyệt và lý do.
   - Với chủ sở hữu: dùng service điều chuyển/thu hồi chuẩn để sinh bản ghi `CSKH Assignment History` mới.
   - Với điểm KPI đã công bố: trình `BOD` / `QA` phê duyệt trước khi chạy `recompute` sinh bản chụp `is_restated = 1`.
5. **Chạy đối soát hậu kiểm:** Thực thi `check_health_and_invariants.py` xác nhận toàn bộ `INV-01`..`INV-06` trở về `0` vi phạm và lập biên bản số liệu trước/sau.
6. **Ứng phó vi phạm dữ liệu cá nhân (nếu có):** Nếu xác nhận có truy cập trái phép vào dữ liệu cá nhân thật, báo cáo ngay cho Ban Giám đốc và bộ phận pháp chế công ty để thực hiện nghĩa vụ thông báo/xử lý theo quy định hiện hành.

---

## 4. Quy Trình Quản Lý Tài Khoản, Chuyển Đội & Thu Hồi Quyền Nhân Sự Nghỉ Việc (`P4.md §17.4`)

### 4.1. Nguyên tắc cấp phát tài khoản (`Least Privilege`)
- Mỗi cá nhân sử dụng **một tài khoản định danh riêng**; cấm dùng chung tài khoản `Administrator` cho công việc hằng ngày của BOD, Trưởng nhóm hay Admin nghiệp vụ.
- `BOD` chỉ cấp role `CSKH BOD Viewer` (không cấp `System Manager`).

### 4.2. Nhân viên nghỉ việc hoặc ngừng phụ trách khách (`Offboarding Checklist` — Thực hiện ngay trong giờ bàn giao)
1. **Khóa đăng nhập & Hủy toàn bộ phiên đang mở:** Đặt `User.enabled = 0` cho tài khoản nhân viên và xóa phiên trong Redis (`clear-cache` / session revoke) để mọi tab trình duyệt đang mở mất hiệu lực tức thì.
2. **Ngừng chia số mới:** Tắt cờ `enabled = 0` trên mọi `CSKH Assignment Rule` của nhân viên đó.
3. **Thu hồi / Điều chuyển đầu việc đang mở bằng Service chuẩn:** Trên `/cskh/pool` hoặc `/cskh/campaigns`, Admin nghiệp vụ thực hiện **Thu hồi (`Revoke`)** hoặc **Điều chuyển (`Transfer`)** toàn bộ `CSKH Work Item` trạng thái `assigned`/`in_progress`/`on_hold` sang nhân sự tiếp nhận (hệ thống tự động chuyển giao `CRM Task` hẹn lại và xóa sạch `DocShare`/`ToDo` cũ).
4. **Hậu kiểm `0` quyền tồn đọng:** Chạy `check_health_and_invariants.py` xác nhận `INV-01 == 0`, `INV-03 == 0` và mọi `CSKH Export Job` cũ của tài khoản đã bị chặn tải (`403`).

### 4.3. Nhân viên hoặc Trưởng nhóm chuyển đội (`Team Transfer`)
- Cập nhật nhánh quản lý trong `CRM Sales Hierarchy` thông qua giao diện quản trị chuẩn để hook `crm_cskh.services.attribution.on_sales_hierarchy_change` tự động đóng khoảng cũ và mở khoảng mới trong `CSKH Team Membership History` (`team-as-of`).
- **Tuyệt đối không** sửa lùi ngày hiệu lực quá khứ làm biến dạng điểm KPI đã chốt của đội cũ và đội mới.

---

## 5. Giới Hạn Chịu Lỗi Của Kiến Trúc Một VPS & Quy Tắc Xoay Vòng Bí Mật (`P4.md §3.3–§3.4`)

1. **Giới hạn Single-VPS (SPOF):** Hệ thống hiện chạy trên một máy chủ vật lý/ảo duy nhất — **không phải kiến trúc High Availability (HA) hay Zero-Downtime**. Nếu bản thân VPS gặp sự cố phần cứng/mạng trung tâm dữ liệu, thời gian gián đoạn bằng thời gian khôi phục nhà cung cấp hoặc dựng VPS thay thế và chạy khôi phục Cấp độ 4 (theo chỉ tiêu RTO thực tế đo ở `P4-05`).
2. **Xoay vòng mật khẩu & Lưu ký khóa giải mã:**
   - Xoay vòng định kỳ (tối thiểu 90 ngày/lần hoặc ngay khi thay đổi nhân sự vận hành) đối với mật khẩu `Administrator`, token pull registry, khóa kết nối SSH và URL webhook cảnh báo.
   - Tệp `/etc/crm-cskh/secrets/prod/db_root_password.txt` luôn giữ quyền `0600` và chỉ kết thúc bằng ký tự `\n` (`LF`).
   - Khóa `encryption_key` của `site_config.json` phải luôn có bản sao lưu mã hóa nằm ở kho lưu ký độc lập ngoài VPS; **nếu đổi hoặc làm mất khóa này mà không cập nhật kho lưu ký, các trường mã hóa trong bản backup sẽ không thể khôi phục**.

---

## 6. Biên Bản Bàn Giao Vận Hành Cho Cổng `G24` (`Trạng Thái Hiện Tại: BLOCKED`)

| Hạng mục bàn giao bắt buộc (`P4.md §19` — `G24`, `§20.3` §9) | Trạng thái | Ghi chú / Đầu vào chờ mở khóa |
| :--- | :---: | :--- |
| Bàn giao tài khoản vận hành cá nhân & phân quyền tối thiểu trên VPS / GitHub Environment | **`BLOCKED`** | Chờ chỉ định `Operator` (`BLK-GOV-06`) và dựng VPS (`BLK-INFRA-01`). |
| Bàn giao bộ sổ tay vận hành (`release-runbook.md`, `rollback-runbook.md`, `monitoring-alerts.md`, `operations-sop.md`) | **`READY (In-Repo)`** | Đã hoàn thiện đầy đủ trong `docs/p4/`. |
| Người vận hành trực tiếp thực hành `check_health_and_invariants.py`, nhận cảnh báo thử & diễn tập restore DR | **`BLOCKED`** | Chờ hoàn tất `P4-05`, `P4-07` live và danh tính `BLK-GOV-06`. |
| Chữ ký nghiệm thu bàn giao kỹ thuật (`Technical Sign-off`) & nghiệp vụ (`Business Sign-off`) | **`BLOCKED`** | Chỉ ký sau khi hoàn tất Go-Live `G23` và cửa sổ theo dõi sau phát hành. |
