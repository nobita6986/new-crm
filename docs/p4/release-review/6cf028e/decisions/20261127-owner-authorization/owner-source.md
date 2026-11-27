T0 — Tôi chấp thuận phương án tích hợp và chuẩn bị ký theo phạm vi sau:

1. Thứ tự hợp nhất

Thực hiện #20 nền P4 trước, sau đó đổi base #19 sang main và kiểm tra lại phạm vi trước khi hợp nhất Worker Care.

Chỉ merge khi các required checks, review và branch protection được đáp ứng. Không bỏ qua kiểm soát của repository.

Chưa phê duyệt amendment offline hoặc hợp nhất PR #18 trong quyết định này.

2. Ràng buộc source và artifact

Ưu tiên giữ ứng viên đã kiểm chứng:
- Source: 6cf028eff4cfd35b5c82ade40358a3bbe22f4273
- Image: ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e

Trước khi dùng artifact này, xác nhận nội dung runtime, build recipe và dependency locks tương ứng với kết quả hợp nhất.

Nếu main chưa chứa PR #18, ghi rõ khác biệt công cụ offline; không tuyên bố toàn bộ root tree giống 6cf028e.

Không build lại chỉ để thay tên merge SHA. Nếu có khác biệt ảnh hưởng artifact, dừng sử dụng bằng chứng cũ và trình delta cần kiểm chứng.

3. Phương thức ký

Chọn protected p4-release.yml, dùng ref ghim đúng source của image và existing_app_image_digest nêu trên; deploy_to_staging=false.

Chuẩn bị cấu hình Environment, reviewer và input cụ thể. Việc nạp/sử dụng khóa chính thức chỉ thực hiện bởi người có thẩm quyền được chỉ định qua kênh bảo vệ.

Không chuyển sang offline hoặc dùng khóa tạm CI làm chữ ký chính thức.

4. SLO

Chấp thuận spec v1.0.0 và profile Staging Config B+ được trình trong hồ sơ để kiểm chứng trên VPS, có điều kiện preflight tài nguyên đạt.

Đây là phê duyệt tiêu chí đo cho Staging, không phải xác nhận kết quả G18 VPS của digest mới hoặc cam kết SLA Production.

5. Giới hạn thực thi

Chưa cấp quyền triển khai Staging hoặc Production trong quyết định này.

Hoàn tất phần tích hợp được phép, chuẩn bị ký và preflight chỉ đọc; sau đó bàn giao:
- SHA main và đối chiếu artifact.
- Trạng thái cấu hình ký, người/reviewer cần chỉ định.
- Điều kiện còn thiếu để triển khai Staging.
- Danh sách vai trò UAT cần người thật xác nhận.

Không tự điền tên người nghiệm thu, ký thay Owner hoặc phát hành Production.
