# T1B — Một việc: khép finding CI P3 trước merge #20

Tiếp tục từ head nền P4 `4562ecb6b7e7eec3aae7ddf0e48632d899cf09a4` sau fetch/recheck thực tế. Giữ ref ứng viên `6cf028e` và image `6599356c…` bất biến.

Coverage đã sửa: P2 inventory 11 test; P3 7 module/56 test. Không sửa lại phần đã đạt hoặc chạy lại 5 workflow tích hợp khi chưa có delta.

Việc còn lại là tái hiện và sửa hai test PDF `/evil.css` trên DEV runtime trong run `45967068984`, không bỏ test/nới assertion/đổi upstream pin mù để xanh. Lấy HTML tối thiểu, call path phát request, phiên bản Frappe/PDF engine và so với cùng ca đã PASS trong image release.

Phân loại delta trước khi sửa:
- Chỉ harness/CI dependency ngoài đầu vào image: chứng minh runtime contract, giữ đủ coverage và chạy kiểm tra chịu tác động.
- Chạm app/runtime/Containerfile/locks: trình delta ảnh hưởng artifact; không coi image cũ đại diện code mới. Candidate mới cần provenance/security/regression/migration/performance tương ứng.

Không ký, không dùng key, không rebuild chỉ để đổi merge SHA, không sửa SLO spec. Protected signing và SLO criteria đã được T0 chấp thuận có điều kiện; không hỏi lại lựa chọn phương án.

Đầu ra: fix có bằng chứng hoặc finding được khoanh đúng nguyên nhân, danh sách file ảnh hưởng và kết quả CI thật. Chỉ khi #20 đạt mới thực hiện chuỗi merge được phép #20 → đổi base/kiểm phạm vi #19. #18 chưa được phép merge.
