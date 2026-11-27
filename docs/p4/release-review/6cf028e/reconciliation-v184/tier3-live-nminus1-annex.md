# Phụ lục Live N-1, migration và rollback Tier 3

**Kế hoạch để review; chưa cho phép thực thi.** Phụ lục sửa phần mô tả vận hành trong snapshot `b549ff3`, không sửa code runtime, image, các gói bằng chứng đã niêm phong hoặc kết quả G06/G09.

Nguồn số liệu: báo cáo chỉ đọc T1A lúc `2026-11-27T08:33:43Z`, đối chiếu labels/ports với preflight chỉ đọc T0. Số liệu DB dưới đây là quan sát do T1A bàn giao, chưa được T0 truy vấn lại DB trong lần hợp nhất tài liệu này. [Nguồn và trạng thái](source-reconciliation.json).

## 1. Định danh N-1 và khả năng reuse

Live N-1 là revision `152d057735db9b920763dc09882118fab2e6fada`, image config `sha256:158105c0da9d420a9193e939f64c4b257a507a075072d31a5606261561bbad27`, apps tree `652fb0ed3f0b278ac34c3ff1e6ee9b17cac332f8`, CSKH tree `14b480200552f642dd27aab39c36ae4b8fd4cf90`. Target rollback phải được inventory/chứng thực theo immutable image thực có; không mặc định tên local `crm-cskh@sha256:...` là một GHCR reference có thể pull.

G06/G09 CI đã diễn tập từ `4678301`, không phải Live `152d057`. Live đã có sidebar bridge, setup-wizard và theme riêng. Sau rollback không được dùng tiêu chí “xóa mọi sidebar/theme” của baseline CI cũ để loại bỏ các hook hợp lệ của Live N-1.

Capture manifest hooks của đúng target N-1. Sau rollback kiểm PDF hooks/Worker Care endpoints không còn thuộc runtime N nếu target N-1 không có chúng, nhưng giữ sidebar/theme đúng N-1. Clear cache là cần thiết, không thay việc kiểm source/module/hook thực tải và fresh login.

## 2. Sửa tên bảng và chụp cấu hình trước nâng cấp

Patch `crm_cskh.patches.v4_0.worker_care_workspace` tác động **CRM Form Script** (`tabCRM Form Script`), không phải `tabClient Script`. Script đích là `CSKH - Open CSKH Page from Deal`.

Trước nâng cấp được phép, lưu snapshot trong kho bảo vệ ngoài Git, có checksum và thời điểm:
- Sự tồn tại, enabled, modified và fingerprint nội dung của đúng CRM Form Script đích.
- Trạng thái tồn tại/value của `System Settings.default_app`; phân biệt không có giá trị với chuỗi rỗng.
- `tabUser.default_app` cho đúng tập user có thể bị patch tác động, kèm điều kiện role và SQL NULL/chuỗi rỗng/giá trị cụ thể. Bảo vệ snapshot chứa định danh người dùng.
- Patch Log, schema, hooks, danh mục masters và số đếm nghiệp vụ trước nâng cấp.

T1A báo Live có System Settings default_app=`crm`, hai user default_app=NULL và script đích enabled=1. Đây là snapshot lịch sử, không phải hằng số dùng để UPDATE toàn bộ ở thời điểm rollback.

## 3. Hoàn nguyên cấu hình có kiểm soát ở Tier 3

Sau khi dừng runtime N và trước khi mở lại N-1:
1. Giữ DB/Redis và các bản ghi nghiệp vụ sau nâng cấp; pin target image N-1 đã inventory.
2. Dùng snapshot pre-upgrade và change-set do migration thực tạo để hoàn nguyên đúng script, System Settings và user đã thay đổi.
3. Chỉ tự hoàn nguyên một giá trị nếu giá trị hiện tại còn bằng post-migration value đã ghi nhận. Nếu có admin override hoặc thay đổi mới sau nâng cấp, dừng để đối chiếu; không ghi đè mặc định.
4. Bảo toàn trạng thái không tồn tại/NULL/chuỗi rỗng. Không blanket UPDATE mọi User về `crm`, không tự tạo record script vốn không có, không mặc định mọi script đều enabled=1.
5. Hoàn nguyên bằng công cụ/runtime tương thích đã được thử trên clone. Chưa cung cấp lệnh SQL ghi Live trong phụ lục này.
6. Configurator → hoàn nguyên cấu hình → clear-cache và clear-website-cache bằng N-1 trước khi mở backend/frontend/websocket/queues/scheduler.
7. Kiểm dữ liệu, roles, hooks, fresh login care/non-care, default app và các luồng ghi P1/P2. Lưu bằng chứng không chứa PII/secrets.

Patch Log không tự lùi theo image. Nếu dữ liệu cấu hình đã hoàn nguyên nhưng patch vẫn marked applied, lần upgrade lại có thể không tự áp lại patch. Ma trận clone phải kiểm **upgrade → rollback → upgrade lại** và dùng quy trình reapply/reconcile được kiểm định. Không xóa Patch Log trên Live để ép chạy lại.

## 4. after_migrate và bảo toàn dữ liệu

`p2_schema_and_seeds.execute` và `p3_schema_and_seeds.execute` có thể chạy lại qua after_migrate dù Patch Log đã có. Trên clone đúng N-1, migrate hai lần phải idempotent, không làm mất/nhân đôi masters, thay admin override ngoài phạm vi, hoặc làm đổi counts ngoài change-set dự kiến.

Số liệu T1A bàn giao: 12 Outcome Codes, 1 Policy, 432 Leads. Khi được phép diễn tập dùng snapshot thật tại thời điểm clone; không coi các số lịch sử này là counts bắt buộc của Live tương lai.

## 5. Backup và encryption key

Snapshot T1A báo encryption_key chưa khởi tạo và không có encrypted auth rows. Ghi `UNINITIALIZED_NO_ENCRYPTED_DATA` thay vì tạo khóa hay xác nhận đã escrow một khóa không tồn tại.

Backup trước nâng cấp phải có DB/files/config, manifest, UTC, byte counts, checksums và restore được kiểm chứng trên clone. `backup-key` mã hóa bundle được giữ tách biệt. Nếu migration/runtime sinh site encryption_key mới, lưu config tương ứng và đưa key vào lưu ký được duyệt; không ghi key vào Git/log hoặc coi local backup là offsite G07 PASS.

## 6. Năng lực host và UAT hiện hữu

Host có 6 vCPU, khoảng 11.960 MiB RAM; Live và UAT đang chạy. Config B+ khai báo tổng cap 7,5 vCPU / 10.688 MiB; cap không phải reservation và snapshot RAM trống không chứng minh đủ sức cho ba stack khi đo tải nặng.

T1A đề xuất ngưỡng 3072 MiB cho smoke nhẹ và reserve 1536 MiB khi đo, cùng phương án tạm stop UAT cũ, giữ volumes. Đây là **đề xuất**, chưa phải preflight đã PASS hoặc quyền dừng UAT. Không đổi profile/spec/config đã duyệt để né guard.

Trước thao tác được phép phải có lịch chạy, headroom/pressure guards và hành động dừng tải trên stack thử nghiệm khi chạm giới hạn; kiểm lại ports 18084/18446. Nếu cần stop UAT, xin quyền thực thi riêng và kế hoạch khởi động lại; không stop/khởi động lại Live. Không công bố số đo G18 chính thức trong điều kiện contention chưa kiểm soát.

## 7. Điều kiện đóng finding

Finding về Live N-1/Tier 3 chỉ có thể đóng sau khi:
- N-1 target và capacity plan được xác nhận.
- Có quyền thực hiện clone/migration/rollback.
- Backup/restore checkpoint đúng N-1 đạt.
- Migrate ×2, rollback với data-state restore, và upgrade lại trên clone đạt; không mất post-upgrade writes/admin overrides.
- T1C kiểm định bằng chứng thực thi.

Phụ lục này hoàn tất phần chuẩn bị tài liệu, chưa thay thế các biên nhận thực nghiệm.
