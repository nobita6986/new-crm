# Hợp nhất bàn giao T1C v1.84, T1A và T1B

Các báo cáo không thay thế quyết định T0 đã ghi nhận tại [hồ sơ phê duyệt có phạm vi](../decisions/20261127-owner-authorization/README.md). Snapshot `b549ff3` giữ nguyên như lịch sử; không xin lại thứ tự merge, phương thức protected signing hoặc tiêu chí SLO đã được chấp thuận có điều kiện.

## Trạng thái hiện hành

- PR #20 head `4562ecb`, chưa merge; main `4678301`. P0/P1/P2 và cả bốn job P4 CI đã PASS; P3 còn 54/56. Không gán tổng 5 workflow/14 job tích hợp cũ thành trạng thái xanh của PR #20 mới.
- #19 giữ nguyên base, #18 chưa được phép hợp nhất.
- Candidate source `6cf028e` / image `6599356c…` giữ nguyên. Chưa có chữ ký chính thức hoặc số đo G18 VPS cho digest mới.
- SLO criteria đã được duyệt có điều kiện; chưa có quyền deploy, stop UAT hay dùng key khi thiếu người được chỉ định.

## Phân công, mỗi T1 một việc

- [T1B: sửa finding P3 PDF/DEV](t1b-next-task.md).
- [T1A: hoàn tất runbook rollback đúng Live N-1](t1a-next-task.md).
- [T1C: kiểm định delta và giữ hai finding mở đúng phạm vi](t1c-next-task.md).

[Phụ lục vận hành](tier3-live-nminus1-annex.md) sửa nhầm tên bảng CRM Form Script, quy định restore dữ liệu cấu hình bằng snapshot có kiểm soát, giữ admin overrides và xử lý upgrade lại sau rollback. Đây là tài liệu chuẩn bị, không phải bằng chứng đã diễn tập.

[Đối chiếu nguồn](source-reconciliation.json) giữ hash ba bản bàn giao và hồ sơ T1C v1.84; các gói đã niêm phong không bị sửa.

Không nạp khóa, tạo stack/clone, stop UAT, chạy migration/G18 hoặc triển khai trong tác vụ hợp nhất tài liệu này.
