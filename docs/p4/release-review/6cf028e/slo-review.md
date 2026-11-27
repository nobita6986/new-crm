# Đề xuất SLO cho Worker Care

**PENDING_OWNER_APPROVAL.** Giữ nguyên bytes spec v1.0.0; số đo CI không phải SLA Production đã cam kết.

## Binding mới

Source approval trong integration vẫn pending và trỏ `ba68cd22…`. [Proposal mới](p4-s1-slo-approval-proposal-6599356c.json) gắn đúng `ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e`, giữ `approved_profiles=[]` và mọi trường signoff null.

Source SHA `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`; spec SHA-256 `ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d`. Candidate binding lịch sử trong spec không bị viết lại: scorer truyền expected image mới và approval record riêng.

## Profile đề xuất

**CI đã đo:** `s1_canonical_4x4`, variant `s1_canonical_4x4_capped`, config SHA `683bc830dc7542c381fe2fb9016389a1d919c2b518f211cfe3ff5de5ff8324fe`. Backend 2 vCPU/2048 MiB, Gunicorn 4×4; DB 2 vCPU/4096 MiB. Envelope 10 service có tổng cap 10 vCPU/12032 MiB; verify CI 9 service có cap 9,5 vCPU/11776 MiB, khác đúng proxy budget. Tổng cap không phải bằng chứng host có tài nguyên dành riêng tương ứng.

**Khuyến nghị Staging:** `staging_vps_capped`, variant `staging_vps_capped_b_plus_2_5vcpu` (Config B+), config SHA `530765c275067dc08bef704cc316c22683376c850b32cde5546e52b9c0d302a7`. Backend 2,5 vCPU/2048 MiB, Gunicorn 4×4; DB 2 vCPU/3584 MiB; tổng cap 7,5 vCPU/10688 MiB cho 10 service.

Runner SHA `5283a0fdfec6d4bf2608aaf3d053fd060c9f811962b04582d934ae6f785044d2`. Runner/profile/spec giữ nguyên bytes từ baseline 3d. Config B+ có căn cứ lịch sử, chưa có số đo Worker Care trên VPS. Preflight CPU/RAM/disk/port và headroom Live/stack hiện hữu trước khi đo; Owner duyệt profile trước phép đo chính thức.

[resource-profiles-proposed.json](resource-profiles-proposed.json) chứa cấu hình đầy đủ.

## Số đo thực của image mới

[Run 45953937721, S1 artifact](https://github.com/nobita6986/new-crm/actions/runs/45953937721/artifacts/15179176106), ZIP SHA `ff04b7c5295662d4a0d32586d1d33c8f3c21c4064889ab7f953818563b46f49c`; benchmark JSON SHA `2ccb71c92d8dd88455e5afc1b4598905ae1bce92589870ac455ed3a627addcb8`.

Dataset giả lập: 50.000 Leads, 75.000 Work Items, 250.000 Attempts, 40 staff; không phải cam kết 50.000 hồ sơ/tháng.

- Wave chính 1920/1920 HTTP thành công, error/timeout=0; p95=149,663 ms, p99=364,932 ms.
- My Customers p95=61,217 ms; mốc P4 ≤1500 ms.
- Lead/history p95=149,265 ms; mốc ≤2000 ms.
- Attempt/callback p95=147,180 ms; mốc ≤2000 ms; duplicate idempotency=0; 10 replay được kiểm chứng.
- BOD snapshot p95=75,704 ms; mốc ≤3000 ms.
- Drilldown ngày/tháng 250k Attempts p95=50,196 / 52,944 ms; mốc ≤3000 ms.
- Preview distribution 10.000 mục 692,59 ms; thực thi 28.449,4 ms; duplicate Work Item=0.
- Import CSV 50.000 dòng 4531,62 ms; error artifact có mặt; preview HWM=579,75 MiB.
- Mixed 20/40/80 sessions p95=152,749 / 282,236 / 599,196 ms; error=0%.
- Recompute atomic; concurrent UI p95=2,971 ms.
- P4 §9.2: **11/11 đạt**.
- Đối chiếu read-only từ cùng số đo: **35/35 rule đạt** — SSR 7/7, JSON API 20/20, aggregate/background 8/8. Không chạy lại tải.

[s1-ci-precheck-derived.json](s1-ci-precheck-derived.json) ghi `CI_PRECHECK`, approval pending và `require_owner_approved=false`. Không dùng chế độ này cho nghiệm thu chính thức.

## Quyết định và phép đo tiếp theo

Đề nghị Owner duyệt spec bất biến và profile B+ cho đúng candidate mới trước khi đo. Nếu chọn profile khác, ghi đúng variant/config hash; không trộn số đo canonical với cấu hình B+ như cùng một lượt.

Sau ủy quyền Staging: đo digest mới, lưu config/cgroup, raw JSON, telemetry; T1C chấm với yêu cầu Owner approved. G18 VPS cũ của `f76c101b` giữ là lịch sử.

API Worker Care overview mới đã được kiểm chứng chức năng/phân quyền trong integration. 35 rule hiện có chỉ bao phủ endpoint đã khai báo; không suy diễn latency riêng chưa đo cho endpoint mới. Nếu cần ngân sách riêng, trình phần bổ sung được duyệt và giữ nguyên spec hiện tại.

[slo-decision-proposal.json](slo-decision-proposal.json) ghi số đo VPS mới là null.
