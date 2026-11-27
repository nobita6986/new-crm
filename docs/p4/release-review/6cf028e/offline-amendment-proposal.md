# Đề xuất offline riêng cho Worker Care

**PROPOSED_NOT_APPROVED.** Chỉ dùng nếu Owner chọn phương án B; protected workflow vẫn là ưu tiên.

## Phạm vi

Source `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`, image `ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e`, OCI config/apps/tree đúng README; official key fingerprint `b1ed6b9e26baa2ef6e415e96a99456d59020111c434c70a7bb73b96ca0627931`.

Amendment/signature/SLO cũ của 3d/f76 không tự có hiệu lực cho candidate mới. Chưa có chữ ký chính thức mới: cần phê duyệt trước lần sử dụng khóa cho candidate này, không ghi POST_SIGNING_APPROVAL cho thao tác chưa xảy ra.

## Kiểm soát đề xuất

1. Owner quyết định phạm vi, phương pháp, người vận hành và lưu ký; T1C kiểm nguồn/thẩm quyền.
2. Manifest mới từ metadata/SBOM/strict scan của đúng image; giữ build-once/promote-by-digest.
3. Ký trong ranh giới được chấp thuận; khóa không vào GitHub verification runner; signer không giữ SSH/registry pull credentials ngoài phạm vi.
4. Ghi đúng isolation. ACL + allowlist môi trường + DPAPI trên máy phát triển không tương đương HSM/container/enclave; DPAPI cùng máy/profile chưa phải backup ngoại tuyến độc lập.
5. Verify bằng public key chính thức, artifact bindings và provenance; bundle mới gắn candidate mới.
6. Verifier/binding mới được review; giữ bundle baseline 12 tệp của PR #18 làm lịch sử.
7. Lưu ký và quyền xử lý PEM theo quyết định thật. Có thể dùng văn bản được T1C đối chiếu độc lập; không ép Owner tạo khóa Ed25519 thứ hai.
8. Scan đủ mới theo chính sách được chấp thuận. SLO, Staging authorization và Production GO là quyết định riêng.

## Quyết định

Quyết định áp dụng, danh tính/chức danh, nguồn xác minh, thời gian thực tế, ranh giới ký và chế độ lưu ký: **đều chưa có**.

Phụ lục chưa có hiệu lực ký hoặc triển khai.
