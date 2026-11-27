# Quyết định cơ chế ký chính thức

**Ưu tiên protected `p4-release.yml`. Chưa có ủy quyền dùng khóa chính thức cho candidate mới trong hồ sơ này.**

## Hiện trạng đã có

- Image `ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e`, source `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`, OCI config theo README.
- Strict scan image mới PASS: Critical=0, secret findings không được chấp thuận=0, CVE exceptions=[]; còn HIGH=135, MEDIUM=215, LOW=234, UNKNOWN=19. PASS theo chính sách hiện hành không có nghĩa mọi severity bằng 0.
- Repo có variables `CSKH_REGISTRY_IMAGE`, `CSKH_REGISTRY_IS_PUBLIC`, `RELEASE_SIGNING_PUBLIC_KEY`.
- DER SHA-256 public key khớp khóa chính thức `b1ed6b9e26baa2ef6e415e96a99456d59020111c434c70a7bb73b96ca0627931`.
- Candidate đã được push/pull và kiểm digest/config qua CI; public pull khai báo trong repo.
- Workflow hỗ trợ `existing_app_image_digest`, ký/xác minh đúng artifact có sẵn.

## Điều kiện còn thiếu

API trả `environments=[]`; chưa có `registry-build`/`registry-verify` được cấu hình và chưa nạp private key vào Environment bảo vệ. Cộng tác viên hiện có quyền push, không có Administration.

Repo hiện public. [GitHub Docs](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) cho biết Environments/protection có trên public repo ở các gói hiện hành; cấu hình Environment của repo cá nhân thuộc repository owner. Hồ sơ không quy nguyên nhân thiếu cấu hình cho GitHub Free.

System Owner duyệt kiến trúc và người có quyền cấu hình repository là hai vai trò cần được xác định riêng. Không yêu cầu người dùng lab đăng nhập tài khoản họ không sở hữu. [Snapshot hiện trạng](protected-signing-current-state.json).

## Phương án A — khuyến nghị

1. Owner chấp thuận exact candidate, phương thức ký, người cấu hình và reviewer.
2. Người có quyền cấu hình tạo `registry-build`, `registry-verify`, đặt protection/reviewer và branch policy cho ref tích hợp được duyệt.
3. Nạp `RELEASE_SIGNING_PRIVATE_KEY` chính thức chỉ vào `registry-build` qua kênh bảo vệ. `registry-verify` pull-only, không giữ khóa ký. Job ký không giữ SSH Staging/Production hoặc credential pull khác phạm vi.
4. Ưu tiên token tích hợp job với `packages:write` khi package permission hợp lệ; chỉ cấp credential bổ sung nếu thực tế cần. Public pull không mặc định đòi token riêng.
5. Xác nhận ref vẫn `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`. Dispatch proposal tại [protected-signing-dispatch-proposal.json](protected-signing-dispatch-proposal.json), `existing_app_image_digest` đúng `6599356c`, `deploy_to_staging=false`.
6. Workflow pull chính digest, tạo SBOM/strict scan và manifest chính thức mới, ký bằng khóa đã ghim. Không build lại image.
7. T1C tự verify scope `production_candidate`, official fingerprint, source/tree, config và artifact bindings. Ghi release ID và previous-release identity thực tế trước Production.

Workflow kiểm `org.opencontainers.image.revision == HEAD`; không chạy input này trên merge SHA main mới chỉ vì apps tree tương đương.

## Phương án B — chỉ sau amendment mới được duyệt

[Đề xuất offline riêng](offline-amendment-proposal.md) chỉ áp dụng `6cf028e/6599356c`; amendment cũ cho `3d0c474/f76c101b` không tự mở rộng.

Sau phê duyệt: ký manifest mới trong ranh giới Owner chấp thuận; ghi đúng provenance/giới hạn; T1C đối chiếu nguồn và thẩm quyền độc lập. Không ép Owner tạo khóa Ed25519 thứ hai. Lập bundle/verifier inputs mới dưới review phù hợp; giữ bundle baseline làm lịch sử. Lưu ký và xử lý PEM chỉ theo quyết định thực tế.

## Ba phạm vi chữ ký

- Manifest CI của image mới có fingerprint `fbc2cd1629d3831b63b88bc37f138e22d8e13caababdd03899359201daef3037`: khóa tạm CI. Scope production_candidate không biến signer tạm thành signer chính thức.
- Chữ ký chính thức hiện có, fingerprint `b1ed6b9e26baa2ef6e415e96a99456d59020111c434c70a7bb73b96ca0627931`, thuộc `3d0c474/f76c101b`.
- Chữ ký chính thức cho digest mới: **chưa có trong hồ sơ này**.

[candidate-and-signature-scope.json](candidate-and-signature-scope.json) lưu đối chiếu. Tác vụ hiện tại chưa dispatch, tạo Environment, dùng khóa hoặc deploy.
