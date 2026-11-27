# Hồ sơ Owner review — nền P4 và Worker Care `6cf028e`

**ĐỀ XUẤT / CHỜ PHÊ DUYỆT.** Hồ sơ chuẩn bị quyết định, chưa cho phép merge, sử dụng khóa chính thức hoặc triển khai. Nhánh tài liệu độc lập với nhánh tích hợp đã kiểm chứng.

## 1. Ứng viên được trình

- Nhánh: `codex/p4-worker-care-integration`.
- Source SHA: `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`.
- Image: `ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e`.
- OCI config: `sha256:a4ff0da5e2ef71f7690de4c35cc02c54081f621519953d28c05a60b5aa99c86a`.
- Root tree: `d100401f1412a5a3b9ac5ec3c02e8da00e46fdba`.
- Apps tree: `3a4f3d0ea036c814ae352c45b6b2f07fe33c1185`.
- CRM CSKH tree: `471b7925d35d4aa7de154df9cb57aab24bf2ca19`.
- Migration revision: `crm_cskh.patches.v4_0.worker_care_workspace`.
- KPI formula: `cskh-metrics-v2`.

## 2. Đề xuất quyết định cụ thể

1. **Hợp nhất:** [Draft PR nền P4 #20](https://github.com/nobita6986/new-crm/pull/20) trước; khi nền thực sự vào main mới retarget và hợp nhất [Worker Care #19](https://github.com/nobita6986/new-crm/pull/19). [PR #18](https://github.com/nobita6986/new-crm/pull/18) là công cụ độc lập, chỉ tích hợp nếu cơ chế offline/amendment được chấp thuận. Khuyến nghị merge commit để giữ ancestry. [Điều kiện/cây nguồn sau từng bước](merge-and-source-integrity.md).
2. **Artifact:** ưu tiên giữ digest `6599356c` đã kiểm chứng, không build lại chỉ để đổi tên merge SHA. Owner quyết định chấp thuận source binding/cây nội dung; nếu yêu cầu release revision đúng SHA main mới thì cần candidate mới và kiểm chứng delta.
3. **Ký:** ưu tiên protected `p4-release.yml`, ký/xác minh digest có sẵn từ ref giữ nguyên ở `6cf028e`, với `deploy_to_staging=false`. [Hiện trạng, điều kiện và giới hạn](signing-decision.md). Phương án offline chỉ dùng khi [phụ lục riêng cho candidate mới](offline-amendment-proposal.md) được duyệt.
4. **SLO:** giữ spec v1.0.0 SHA `ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d`. Đề xuất Staging Config B+ / `staging_vps_capped_b_plus_2_5vcpu`, config SHA `530765c275067dc08bef704cc316c22683376c850b32cde5546e52b9c0d302a7`, runner `5283a0fdfec6d4bf2608aaf3d053fd060c9f811962b04582d934ae6f785044d2`. Phê duyệt đúng digest trước lượt đo VPS chính thức. [Số đo thực và đề xuất](slo-review.md).
5. **Staging:** ủy quyền riêng sau khi có chữ ký chính thức và approval SLO/profile. Kế hoạch có backup, migration, UAT theo vai trò, điểm dừng và khôi phục tại [staging-and-recovery-plan.md](staging-and-recovery-plan.md).
6. **Production:** quyết định GO riêng sau nghiệm thu Staging/UAT, offsite backup, bàn giao Operator và đủ cổng tiên quyết. Hồ sơ này chưa đề nghị ký GO sớm.

## 3. Bằng chứng đã kiểm chứng

GitHub API xác nhận **5 workflow, 14/14 job success** trên SHA tích hợp. Đã tải và đối chiếu **14/14 ZIP artifact khớp SHA-256 API**.

- [P4 CI — 4/4](https://github.com/nobita6986/new-crm/actions/runs/45953929696).
- [Worker Care integration — 2/2](https://github.com/nobita6986/new-crm/actions/runs/45953933914).
- [GHCR candidate / regression / S1 — 3/3](https://github.com/nobita6986/new-crm/actions/runs/45953937721).
- [Offline verifier — 1/1](https://github.com/nobita6986/new-crm/actions/runs/45953941451).
- [Batch 2 — 4/4](https://github.com/nobita6986/new-crm/actions/runs/45955303485).

**Phạm vi:** code verifier offline chạy trên `6cf028e`, nhưng bundle/chữ ký được kiểm tra vẫn thuộc `3d0c474/f76c101b`. Candidate `6599356c` có chữ ký khóa tạm CI, chưa có chữ ký chính thức. Không suy ra chữ ký mới từ tổng job xanh.

S1 có 11/11 tiêu chí P4 đạt; đối chiếu threshold từ JSON đo CI có sẵn cho 35/35 rule đạt. Đây là CI precheck, chưa phải Owner approval hoặc nghiệm thu G18 VPS của digest mới.

Chi tiết: [verification-summary.json](verification-summary.json), [candidate-and-signature-scope.json](candidate-and-signature-scope.json).

## 4. Phiếu quyết định

Owner có thể xác nhận bằng văn bản/phản hồi được xác minh; T1B chuẩn hóa JSON sau đó. Mọi ô hiện để trống:

- Người có thẩm quyền/chức danh/nguồn xác minh và thời điểm quyết định: **chưa điền**.
- Thứ tự hợp nhất và chiến lược merge commit: **chưa chọn**.
- Giữ candidate `6cf028e/6599356c` theo source-tree mapping, hoặc yêu cầu candidate từ main: **chưa chọn**.
- Protected signing (khuyến nghị) hoặc offline theo phụ lục mới: **chưa chọn**.
- Spec/profile/config cho digest mới: **chưa chọn**.
- Người cấu hình Environment, reviewer và cơ chế nạp khóa: **chưa chỉ định**.
- Cho phép Staging, lịch thực hiện và người vận hành: **chưa chọn**.
- Bốn người UAT, Primary Operator, offsite target và người giữ khóa backup/site: **chưa chỉ định**.
- Production GO: **chưa đề nghị duyệt ở bước này**.

[owner-decision-proposal.json](owner-decision-proposal.json) lưu các lựa chọn null. [SLO approval proposal](p4-s1-slo-approval-proposal-6599356c.json) gắn đúng digest mới, vẫn `PENDING_OWNER_APPROVAL`, `approved_profiles=[]`, signoff null.

## 5. Phạm vi thực thi của tác vụ chuẩn bị

Đã mở Draft PR #20, giữ base #19, kiểm chứng artifact và chuẩn bị hồ sơ. Chưa merge, ký chính thức cho digest mới, sửa approval trên nhánh ứng dụng hoặc deploy. Không dispatch/rerun workflow đã đạt. Đối chiếu SLO chỉ đọc số đo có sẵn, không chạy lại tải.
