# Thứ tự hợp nhất và kiểm tra cây nguồn

**Đề xuất, chưa merge.** Repository duy nhất: `nobita6986/new-crm`.

## 1. PR nền P4 #20 → main

Base: `main@467830195358b2431ac772486530ab935887a518`.
Head: `codex/p4-t1b-trial-g04-mariadb1189-nochromium@3d0c47486c78c09a966a3ea9100c477f53fe6a79`.
Diff: **135 tệp, +55.580 / -3.183** — runtime/cache, bảo mật/PDF, container/dependency/provenance, hiệu năng và UI nền.

Trước merge: kiểm head/base, review và checks hiện hành; job bị hủy/skipped không được ghi PASS. Giữ phạm vi bằng chứng baseline tách khỏi Worker Care.

Khuyến nghị merge commit để bảo toàn ancestry. Sau merge ghi SHA main `M1`, kiểm baseline là ancestor và:
- Root tree: `0d96001d368d8dc44f2a5cf9b299912344ded469`.
- Apps: `f4cb374f0eed89304e7b55e4978434a83d406051`.
- CSKH: `f1b9258c53b231390da8f9421e2c7c06cfc9281a`.

Nếu main đã có thay đổi khác snapshot, đánh giá diff-impact trước bước tiếp theo.

## 2. PR #19 Worker Care

Giữ base là nhánh P4 cho đến khi Bước 1 thực sự hoàn tất; tác vụ chuẩn bị không đổi base.

Sau khi baseline đã vào main:
1. Retarget #19 sang main.
2. Xác nhận head còn `c0f4debece35f907c15ea12d07368effa3905244`; diff Worker Care hiện tại là 50 tệp (+8.127 / -528).
3. Review delta do retarget/conflict resolution, đối chiếu checks hiện hành.
4. Sau merge commit ghi `M2`. Root mong đợi `8644c084aea5de50018bd33e8b1ef549e3c1d6a4`, bằng Worker Care head và merge mô phỏng `d1d12b778bf6cad689c6176c4e8283947b5e5060`.
5. Apps/CSKH phải bằng `3a4f3d0ea036c814ae352c45b6b2f07fe33c1185` / `471b7925d35d4aa7de154df9cb57aab24bf2ca19`.
6. Release-lock SHA-256: `a6b7112009347063d88c43a9759c85d337c46c7ac6af2e5830e1305c91acc9b0`; Containerfile: `c540abed3e85eee2ecef7c1a406948e19b37713f6a0e3100008dfb9fd3f94f07`.

## 3. PR #18 — công cụ độc lập

Head `e9bb412df02e8f6826e5ab45a7a84e6b47c92ad0`; 18 tệp thêm mới ở workflow offline và `deploy/release/offline_signing/`.

Chỉ tích hợp khi cơ chế offline/amendment được duyệt và điều kiện review/checks được xử lý. Nếu merge sau Bước 2, không có delta khác, root `M3` phải bằng `d100401f1412a5a3b9ac5ec3c02e8da00e46fdba`, đúng tree của `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`. Apps, lock và Containerfile không đổi.

Nếu chọn protected signing mà #18 chưa được duyệt, giữ PR mở. Main sau Bước 2 khác integration đúng phần công cụ này; ghi rõ khác biệt, kiểm riêng apps và đầu vào build, trình Owner chấp thuận source binding. Không khẳng định full-tree equality khi #18 chưa vào main.

## 4. Ref ký và merge SHA

SHA merge thực tế khác SHA mô phỏng; kiểm tree/hash thay vì gán kết quả CI cho SHA chưa chạy.

`p4-release.yml` so revision label của image với chính HEAD checkout. Để ký `ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e` không rebuild, dispatch từ `codex/p4-worker-care-integration` khi ref vẫn đúng `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`. Dùng main merge SHA mới sẽ không qua kiểm tra revision dù apps tree giống.

Nếu Owner yêu cầu release revision đúng main mới, tạo candidate mới và kiểm chứng delta theo artifact mới; không sửa label hay manifest của image cũ.

## 5. Kiểm chứng delta

- Chỉ đổi merge metadata, tree/recipe/lock/runner bằng snapshot: giữ bằng chứng nội dung, bổ sung provenance/ancestry; không đo lại chỉ vì SHA merge khác.
- Đổi apps, migration, runtime/container/dependency/cache: kiểm regression/security/migration/performance chịu tác động.
- Đổi digest hoặc manifest: xác minh signature, config, source binding, artifact hashes và approval binding mới.
- Giữ SHA công cụ và SHA ứng dụng tách biệt; không force-push nhánh dùng chung hoặc build trên VPS.

[source-tree-map.json](source-tree-map.json) khóa các giá trị. Mẫu đọc sau merge, chưa thực thi ở tác vụ này:

```bash
git merge-base --is-ancestor 3d0c47486c78c09a966a3ea9100c477f53fe6a79 origin/main
git rev-parse origin/main^{tree} origin/main:apps origin/main:apps/crm_cskh
git diff --name-status 6cf028eff4cfd35b5c82ade40358a3bbe22f4273 origin/main
```
