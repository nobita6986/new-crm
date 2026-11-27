# Quyết định T0 và thực thi có điều kiện — 27/11/2026

Nguồn phê duyệt trực tiếp của người dùng T0 được lưu tại [owner-source.md](owner-source.md), chuẩn hóa tại [owner-decision-record.json](owner-decision-record.json). Không tạo chữ ký thay Owner hoặc nhận diện GitHub chưa được cung cấp.

## Phạm vi đã chấp thuận

- #20 nền P4 trước; sau khi vào main mới đổi base #19, kiểm lại phạm vi và merge khi các kiểm soát repository được đáp ứng.
- Giữ source `6cf028eff4cfd35b5c82ade40358a3bbe22f4273` và digest `6599356c…` nếu runtime, build recipe và locks tương ứng kết quả tích hợp.
- Chọn protected `p4-release.yml`, ref ghim theo source image, existing digest, deploy=false.
- Phê duyệt spec v1.0.0 và Staging Config B+ cho digest mới, có điều kiện preflight đạt.
- Cho phép preflight chỉ đọc.

Không phê duyệt amendment offline/merge #18, chưa cho phép Staging/Production, chưa chỉ định người nạp/sử dụng khóa hoặc người nghiệm thu.

## SLO được ghi nhận

[slo-staging-criteria-approval.json](slo-staging-criteria-approval.json) là bản ghi phê duyệt tiêu chí đo trực tiếp trong chat; trạng thái OWNER_APPROVED chỉ áp dụng tiêu chí Staging. Tên cá nhân chưa được cung cấp nên không tự điền.

Digest, spec, profile, variant B+, config hash và runner hash được ghim. Validator approval-doc đạt với require_owner_approved=true, nhưng chưa có số đo G18 VPS cho digest mới. Không dùng record này để tự cho phép deploy hoặc cam kết SLA Production.

Proposal pending ở hồ sơ trước được giữ nguyên như snapshot lịch sử; bản ghi mới này thay trạng thái quyết định của đúng phạm vi được Owner duyệt.

## Chuẩn bị ký

[protected-signing-environment-plan.json](protected-signing-environment-plan.json) ghi cấu hình/input cụ thể. Environments hiện chưa tồn tại. Cần chỉ định:
- Người có quyền cấu hình repository/Environment.
- Người lưu ký/nạp khóa chính thức qua kênh bảo vệ.
- Reviewer được Owner chọn, định danh GitHub/user ID và quyền phù hợp.

Không tự chọn người, không nạp key, không dispatch. Không dùng signer CI tạm hoặc chuyển offline.

## Preflight và điều kiện triển khai còn thiếu

[readonly-preflight-summary.json](readonly-preflight-summary.json) ghi tài nguyên/ports thực đo; Live và UAT giữ nguyên. Ports 18084/18446 hiện trống nhưng phải kiểm lại tại thời điểm triển khai.

Live revision thực tế khác N-1 của ca CI G06/G09. Cần kiểm migration state và diễn tập delta trên clone đúng N-1 trước cutover được phép. Không chạy clone/migration trong tác vụ chỉ đọc.

Tổng cap B+ không chứng minh đủ tài nguyên dành riêng khi chạy đồng thời Live/UAT. Cần lịch chạy và preflight guard/headroom đạt trước phép đo. Offsite config chưa có; alerting.env đã có.

Staging còn cần chữ ký chính thức mới + T1C verify, người vận hành được chỉ định, quyền triển khai riêng, backup/restore checkpoint, kế hoạch năng lực và kiểm tra N-1 thực tế.

## UAT và bàn giao người thật

Chưa gán người. Owner cần chỉ định họ tên và kênh an toàn của BOD, CRM Admin, Team Lead, Staff để dùng tài khoản riêng và ký F01–F30 trên digest cuối. Primary Operator/backup custodian cần được chỉ định riêng; không mặc định cùng người hoặc ký thay.

## Source/artifact và CI

[artifact-source-projection.json](artifact-source-projection.json) phân biệt dự kiến với merge thực tế. PR #18 chưa vào main: không tuyên bố root tree bằng integration. Ngoài công cụ offline còn có hai thay đổi CI để bổ sung coverage; runtime/build inputs không đổi.

Các check mới của PR #20 đã phát hiện P2 inventory thiếu hai test UI, P3 runner bỏ sót 17 test thuộc hai module. Commit `4562ecb6b7e7eec3aae7ddf0e48632d899cf09a4` chỉ sửa hai tệp kê khai/chạy test; không giảm tiêu chí, không đổi apps/recipe/locks, không rebuild candidate. P0/P1/P2 mới đã PASS. P3 chạy đủ 56 test nhưng chỉ 54 PASS: hai ca PDF ghi nhận request bị cấm tới `/evil.css`. Chưa merge.

Kết quả và SHA main thực tế sẽ được ghi trong biên nhận thực thi, không dùng cây dự kiến làm chứng cứ đã merge.


## Biên nhận thực thi và finding mới

[execution-and-merge-status.json](execution-and-merge-status.json) ghi main thực tế vẫn `467830195358b2431ac772486530ab935887a518`; #20 còn mở Draft, #19 chưa đổi base, #18 chưa merge.

Finding [F-P4-LEGACY-DEV-PDF-CSS-SSRF-01](p3-pdf-security-finding.json) chặn merge. Legacy DEV dùng Frappe `d2efa796…`; image release dùng Frappe 15.126.0 / `53bae3ef…`. Cùng hai test đã PASS trong artifact kiểm định image `6599356c…`; khác môi trường chưa phải bằng chứng candidate mới mắc lỗi. PDF service/test không có diff giữa baseline và Worker Care.

Delta cần xử lý: tái hiện và xác định hợp đồng runtime được hỗ trợ; sửa harness/dependency CI nếu có căn cứ hoặc sửa runtime và tạo candidate mới khi cần. Không bỏ test, không nới kiểm tra SSRF và không merge qua check đỏ. Sau khi P3 đạt mới tiếp tục #20 → đổi base/kiểm phạm vi #19.

Các check P4 còn chạy tại lúc ghi nhận được giữ là IN_PROGRESS, không gán PASS và không hủy job.
