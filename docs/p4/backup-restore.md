# Sao Lưu Mã Hóa, Vòng Đời Lưu Giữ & Diễn Tập Khôi Phục Thảm Họa Cô Lập (`P4-05` — Cổng `G07`, `G08`)

- **Mã tài liệu:** `P4-BACKUP-RESTORE` (`docs/p4/backup-restore.md` — `P4.md §7`, `§15.3`, `§20.2`)
- **Bộ công cụ thực thi:**
  - Tạo gói sao lưu mã hóa: [`deploy/ops/create_encrypted_backup.py`](../../deploy/ops/create_encrypted_backup.py)
  - Kiểm tra checksum, biên nhận offsite & GFS retention: [`deploy/ops/manage_backup_lifecycle.py`](../../deploy/ops/manage_backup_lifecycle.py)
  - Khôi phục diễn tập DR cô lập + Positive Control: [`deploy/ops/verify_offsite_restore.py`](../../deploy/ops/verify_offsite_restore.py)
  - Cài đặt lịch systemd tự động trên VPS: [`deploy/ops/install_systemd_timers.py`](../../deploy/ops/install_systemd_timers.py) (`crm-cskh-backup.service` / `crm-cskh-backup.timer`)
  - Kiểm thử đơn vị: [`deploy/ops/test_offsite_restore.py`](../../deploy/ops/test_offsite_restore.py)

> [!WARNING]
> **Báo cáo trung thực trạng thái Sao lưu (`G07`) & Diễn tập Khôi phục (`G08`) sau đối chiếu thực thi trên VPS `103.77.208.186` (`801be22`, ngày `09/11/2026`):**
> - **Diễn tập khôi phục trên bản clone cô lập tại VPS (`G08`):** **`PASS`** ([`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json), SHA-256 `af2d0a899e84d68243f80d64ad4d8ed43b4c2f21e5c28355c9022001c56f689c`) — Pha A khôi phục `p4-20261109-pre-scheduler` đạt `PASS` trong **`142.598 giây`** (**phạm vi chỉ tính clone cô lập trên cùng VPS, chưa gồm truyền mạng WAN, cấp máy chủ mới hay chuyển DNS**); Pha B đối chứng Dương/Âm mã hóa Fernet (`ValidationError`), tệp public/private, `migrate ×2` và dọn sạch `0` dư lượng đạt `PASS`; container & image live `acb9505...` giữ nguyên không đổi.
> - **Lịch & dịch vụ sao lưu hằng ngày trên VPS (`G07`):** `crm-cskh-backup.service` đã chạy thật trên VPS đạt **`PASS`** lúc `20:00:28 +07` ngày `09/11/2026` (`scheduled-20261109T130029Z-1400885`, SHA-256 `33fdf18bf4e9168e98baee65446c215e005b44271a9507cd94b3e1e86f34e14c`), đã chép thủ công ra ngoài VPS khớp checksum và lưu riêng `backup-key`. Bộ hẹn giờ `crm-cskh-backup.timer` đã `enabled`/`active` (`02:15 ICT`), **nhưng lượt chạy đã đo là khởi chạy service thủ công (`systemctl start`), chưa phải bằng chứng timer tự kích hoạt theo lịch** (`LastTriggerUSec = ""`, lịch kích hoạt tự động đầu tiên lúc `2026-11-10 02:16:41 +07`).
> - **Đồng bộ Offsite tự động (`BLK-INFRA-05`):** Hai bản chép thủ công ra máy trạm Owner **KHÔNG chứng minh upload định kỳ tự động đang hoạt động** (`one_off_manual_copy_proves_periodic_upload = false`). Cho tới khi Owner cấp đích lưu trữ ngoài VPS và credentials vào `/etc/crm-cskh/offsite-backup.env` (`0600`), cổng **`G07` giữ trạng thái `BLOCKED`** (`manage_backup_lifecycle.py audit-g07-gate` trả về `"gate_g07_status": "BLOCKED"`, trong khi tái sử dụng kết quả diễn tập restore cô lập đã đạt `PASS` trên VPS `142.598s` mà không chạy lại do không có thay đổi ảnh hưởng tới `create_encrypted_backup.py` hay `verify_offsite_restore.py`). Tuyệt đối không coi thư mục trên cùng VPS là offsite.

---

## 1. Kiến Trúc Gói Sao Lưu Mã Hóa (`p4-encrypted-backup-v2`)

Mỗi lần chạy `create_encrypted_backup.py` sinh một thư mục mốc sao lưu độc lập quyền `0700` (ví dụ `/var/backups/crm-cskh/scheduled-20261109T191500Z/`) gồm các tệp ngoài vỏ và 6 thành phần bên trong gói mã hóa:

### 1.1. Tệp trong thư mục bundle cục bộ & quy tắc tách khóa
1. `backup.tar.gz.enc` (`0600`): Gói nén `.tar.gz` đã mã hóa bằng OpenSSL `AES-256-CBC` với dẫn xuất khóa `PBKDF2-HMAC-SHA256` (`200.000` vòng lặp, salt ngẫu nhiên) từ chính tệp `backup-key` của bundle đó (`-pass file:<out_dir>/backup-key`) và kiểm chứng giải mã vòng khép kín khớp SHA-256 ngay khi tạo.
2. `backup-key` (`0600`): **Khóa ngẫu nhiên 256-bit riêng biệt cho từng bundle (`secrets.token_hex(32)` + `\n`)** được `create_encrypted_backup.py` sinh mới độc lập ngay trong `<out_dir>/backup-key` (công cụ **không** đọc khóa cố định dùng chung từ tệp hay biến môi trường bên ngoài). Khi lưu trữ ngoài VPS, `backup-key` **bắt buộc** lưu ký tại kho bí mật độc lập (Password Vault của Owner / kho khóa tách rời) và **tuyệt đối không được đẩy sang cùng đích lưu trữ offsite chứa `backup.tar.gz.enc`**.
3. `backup-report.json` (`0600`): Biên bản siêu dữ liệu `0` PII / `0` secret (`archive_sha256`, `archive_bytes`, `source_revision`, `encryption_key_state` chỉ báo trạng thái có/chưa khởi tạo — không lộ khóa, và trạng thái trung thực `end_to_end_backup_status`).
4. `offsite-verification.json` *(xuất hiện sau khi đã chép `backup.tar.gz.enc` ra kho ngoài VPS và đối chiếu khớp mã băm hai đầu qua `sync_bundle_offsite_automated` hoặc `manage_backup_lifecycle.py record-offsite`)*.

### 1.2. Sáu thành phần bắt buộc bên trong `backup.tar.gz.enc`
Khi giải mã, `verify_offsite_restore.py` kiểm tra nghiêm ngặt chống path traversal / symlink và yêu cầu **đúng đủ 6 thành phần**:
1. `*-database.sql.gz`: Bản dump MariaDB đầy đủ của site (`crm-staging.localhost`).
2. `*-files.tar`: Toàn bộ tệp đính kèm công khai (`sites/<site>/public/files`).
3. `*-private-files.tar`: Toàn bộ tệp đính kèm riêng tư (`sites/<site>/private/files`).
4. `*-site_config_backup.json`: Cấu hình cấp site (bao gồm `encryption_key` nếu site đã khởi tạo trường mã hóa).
5. `before.json`: Bản chụp đối soát `0` PII tại thời điểm sao lưu (số dòng nghiệp vụ, SHA-256 bảng cốt lõi, số dòng & SHA-256 bảng `Translation`, `encryption_key_present`, `encryption_key_sha256`, và số dòng `__Auth WHERE encrypted=1`).
6. `backup-file-hashes.json`: Bảng SHA-256 của từng thành phần `*-database.sql.gz`, `*-files.tar`, `*-private-files.tar`, `*-site_config_backup.json`.

---

## 2. Quy Tắc Fail-Closed Cho `encryption_key` (`P4-05` Mục 3)

Trong Frappe, khóa Fernet `encryption_key` trong `site_config.json` chỉ tự động được sinh lần đầu khi ứng dụng ghi một trường kiểu `Password` được mã hóa (`__Auth.encrypted = 1`) hoặc gọi `get_encryption_key()`.

Để **không bỏ điều kiện kiểm tra một cách mù quáng** nhưng vẫn phản ánh đúng thực tế hạ tầng, hàm `evaluate_encryption_key_state()` thực thi kiểm định **fail-closed 2 chiều**:

| Trạng thái kết luận | Điều kiện bắt buộc để đạt hợp lệ | Hành vi khi vi phạm |
| :--- | :--- | :--- |
| **`PRESENT_VERIFIED`** | 1. `site_config_backup.json` là JSON nguyên vẹn (`db_name`, `db_password` đầy đủ) và có `encryption_key` chuẩn Fernet (`44` ký tự base64 URL-safe = `32` bytes).<br>2. SHA-256 của khóa khớp tuyệt đối với `before.json` (nếu có).<br>3. `site_config.json` sau khôi phục giữ đúng khóa và quyền `0600`.<br>4. Toàn bộ dòng `__Auth WHERE encrypted = 1` giải mã thành công (`decrypt()` không lỗi). | Ném `ValueError` (dừng khôi phục ngay) nếu khóa rỗng, sai định dạng Fernet, lệch SHA-256 với nguồn, hoặc bất kỳ dòng `__Auth` mã hóa nào không giải mã được. |
| **`UNINITIALIZED_NO_ENCRYPTED_DATA`** | 1. `site_config_backup.json` nguyên vẹn (`db_name`, `db_password` hợp lệ, không bị cắt cụt).<br>2. Nguồn gốc xác nhận `encryption_key_present == False` (hoặc archive `v1` nguyên vẹn cấu hình).<br>3. Cả nguồn lẫn DB sau khôi phục có **`__Auth WHERE encrypted = 1` bằng đúng `0` dòng**.<br>4. Không để sót `encryption_key` ngẫu nhiên lạ trong `site_config.json` của bản khôi phục. | **Ném `ValueError("Missing encryption key: ...")` ngay lập tức** nếu nguồn từng có khóa (`encryption_key_present == True`), hoặc có `encryption_key_sha256`, hoặc DB có bất kỳ dòng `__Auth.encrypted = 1` nào (`> 0`), hoặc tệp cấu hình bị hỏng. |

> [!IMPORTANT]
> **Hướng Dẫn Lưu Ký Khóa Tách Biệt (`Key Escrow` — 3 Lớp Bí Mật Bắt Buộc):**
> 1. **Khóa giải mã từng gói backup (`<bundle>/backup-key`, quyền `0600`, 256-bit hex):** Mỗi lần chạy `create_encrypted_backup.py` sinh một `backup-key` ngẫu nhiên hoàn toàn mới chỉ giải mã được đúng gói `backup.tar.gz.enc` của bundle đó. Khi lưu trữ ngoài VPS:
>    - Lưu ký nội dung `backup-key` vào **Kho quản lý bí mật độc lập của Owner** (ví dụ Password Vault nội bộ của Owner, gắn nhãn tra cứu theo `bundle_name` + `archive_sha256` + `completed_at_utc`).
>    - **CẤM TUYỆT ĐỐI** lưu `backup-key` trên cùng kho/bucket/thư mục đích chứa `backup.tar.gz.enc` (nếu kẻ tấn công đọc được kho backup offsite thì không có khóa giải mã; nếu mất toàn bộ VPS `103.77.208.186` thì Owner lấy `backup.tar.gz.enc` từ kho offsite và lấy `backup-key` từ Password Vault để khôi phục).
>    - Sau khi đã lưu ký `backup-key` vào Password Vault, có thể xóa bản `backup-key` cục bộ trên VPS nếu muốn cô lập tối đa và dùng cờ `--no-key-in-dir` khi gọi `manage_backup_lifecycle.py verify-bundle`.
> 2. **Khóa mã hóa cấp ứng dụng Frappe (`site_config.json` → `encryption_key`, Fernet 32-byte):** Hiện tại trên `crm-staging.localhost` chưa có trường mật khẩu mã hóa nào (`UNINITIALIZED_NO_ENCRYPTED_DATA`, `__Auth.encrypted = 1` có `0` dòng). Ngay khi bật tích hợp ngoài đầu tiên (SMTP, tổng đài, OAuth, webhook có mật khẩu mã hóa) làm phát sinh `encryption_key` trong `site_config.json`, Người vận hành **bắt buộc** lưu ký ngay giá trị `encryption_key` vào Password Vault độc lập của Owner và chạy ngay một lượt `create_encrypted_backup.py` mới.
> 3. **Đồng bộ vòng đời hủy khóa theo GFS Retention:** Chỉ hủy mục lưu ký `backup-key` cũ trong Password Vault sau khi gói `backup.tar.gz.enc` tương ứng đã hết hạn lưu giữ cả ở VPS lẫn kho offsite theo chính sách `14 ngày / 8 tuần / 3 tháng`. Giữ cố định không xóa khóa của các bản mốc (`pre-scheduler`, `pre-release`, `pre-cutover`, `golden`, `.pinned`).

---

## 3. Lịch Chạy Tự Động, Kiểm Tra Checksum, Offsite & Lưu Giữ (`Retention`)

### 3.0. Chuẩn bị & xác minh thư mục mã nguồn vận hành đúng SHA (`/opt/crm-cskh-ops`)
> [!IMPORTANT]
> **Không giả định `/opt/crm-cskh-app` hay `/opt/crm-cskh-staging/repo` đã tồn tại trên VPS `103.77.208.186`.** Trước khi chạy diễn tập khôi phục hoặc cài đặt `systemd` timer, chuẩn bị và xác minh thư mục mã nguồn vận hành `/opt/crm-cskh-ops` đúng commit SHA đã bàn giao (`<OPS_COMMIT_SHA>`):

```bash
# Bước A (Trên máy trạm có repo Git): Đóng gói mã nguồn ops từ đúng commit SHA bất biến và chuyển lên VPS:
OPS_SHA="<OPS_COMMIT_SHA>"
git archive --format=tar.gz -o crm-cskh-ops.tar.gz "${OPS_SHA}" deploy docs/p4
sha256sum crm-cskh-ops.tar.gz
scp crm-cskh-ops.tar.gz root@103.77.208.186:/tmp/crm-cskh-ops.tar.gz

# Bước B (Trên VPS 103.77.208.186): Đối chiếu SHA-256 gói mã nguồn, giải nén nguyên tử vào /opt/crm-cskh-ops và kiểm tra tệp thực thi:
echo "<TAR_SHA256>  /tmp/crm-cskh-ops.tar.gz" | sha256sum -c -
sudo rm -rf /opt/crm-cskh-ops.new && sudo install -d -m 0755 /opt/crm-cskh-ops.new
sudo tar -xzf /tmp/crm-cskh-ops.tar.gz -C /opt/crm-cskh-ops.new
printf '%s\n' "<OPS_COMMIT_SHA>" | sudo tee /opt/crm-cskh-ops.new/.ops-commit-sha >/dev/null
sudo rm -rf /opt/crm-cskh-ops && sudo mv /opt/crm-cskh-ops.new /opt/crm-cskh-ops
rm -f /tmp/crm-cskh-ops.tar.gz
test -f /opt/crm-cskh-ops/deploy/ops/verify_offsite_restore.py
test -f /opt/crm-cskh-ops/deploy/ops/install_systemd_timers.py
```

*(Nếu trên VPS dùng trực tiếp bản checkout Git tại `/opt/crm-cskh-ops`, bắt buộc kiểm tra `test "$(git -C /opt/crm-cskh-ops rev-parse HEAD)" = "<OPS_COMMIT_SHA>"` trước khi chạy).*

### 3.1. Lịch chạy hằng ngày trên VPS (`systemd`) — Dry-run → Kiểm tra Unit → Cài đặt (`--install`) → Đo lần chạy thật
Tệp mẫu [`deploy/ops/systemd/crm-cskh-backup.timer`](../../deploy/ops/systemd/crm-cskh-backup.timer) kích hoạt lúc **`02:15` sáng mỗi ngày (`Asia/Ho_Chi_Minh`, tức `19:15:00 UTC`)**. Trình cài đặt `install_systemd_timers.py` mặc định chạy ở chế độ xem trước (`dry_run`), kiểm tra sự tồn tại của các script dưới `--repo-root` cùng `.env.staging`, `staging-compose`, `compose.domain.yaml`, và chỉ ghi unit + bật timer khi truyền `--install` với quyền `root`:

```bash
# 1. Xem trước (dry-run mặc định — không truyền --install) và kiểm tra đường dẫn/unit:
python3 /opt/crm-cskh-ops/deploy/ops/install_systemd_timers.py \
  --repo-root /opt/crm-cskh-ops \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --compose-wrapper /opt/crm-cskh-staging/staging-compose \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --backup-root /var/backups/crm-cskh

# 2. Cài đặt và kích hoạt timer sau khi dry-run khớp 100% đường dẫn:
sudo python3 /opt/crm-cskh-ops/deploy/ops/install_systemd_timers.py \
  --repo-root /opt/crm-cskh-ops \
  --env-file /opt/crm-cskh-staging/.env.staging \
  --compose-wrapper /opt/crm-cskh-staging/staging-compose \
  --extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml \
  --site crm-staging.localhost \
  --environment-label staging \
  --backup-root /var/backups/crm-cskh \
  --install

# 3. Kiểm tra cú pháp unit đã cài và đo lần chạy thật đầu tiên (giữ offsite PARTIAL & tắt gửi cảnh báo ngoài):
sudo systemd-analyze verify \
  /etc/systemd/system/crm-cskh-healthcheck.service \
  /etc/systemd/system/crm-cskh-backup.service
sudo systemctl list-timers --all 'crm-cskh-*'
sudo systemctl start crm-cskh-backup.service
sudo systemctl start crm-cskh-healthcheck.service
sudo jq '{local_backup_status,end_to_end_backup_status,periodic_offsite_upload_proven,automated_offsite_blocker}' \
  /var/backups/crm-cskh/latest-backup-lifecycle.json
sudo jq '{status,containers:.containers.status,invariants:.invariants.status,backup:.backup_freshness.end_to_end_backup_gate_g07_status}' \
  /var/lib/crm-cskh/health/latest-health-report.json
```

### 3.2. Cấu Hình Upload Offsite Tự Động (`/etc/crm-cskh/offsite-backup.env`), Cấm Offsite Cùng VPS & Đầu Vào Cần Owner Cấp (`BLK-INFRA-05`)

1. **Cơ chế cấu hình không cần sửa code/unit (`EnvironmentFile=-/etc/crm-cskh/offsite-backup.env`):**
   - `crm-cskh-backup.service` tự động nạp tệp cấu hình tùy chọn `/etc/crm-cskh/offsite-backup.env` (quyền `0600`, `root:root`, xem mẫu trung lập nhà cung cấp tại [`deploy/compose/env/offsite-backup.env.example`](../../deploy/compose/env/offsite-backup.env.example)).
   - Khi tệp này chưa tồn tại trên VPS, `systemd` bỏ qua an toàn (dấu `-`), giữ nguyên trạng thái `PARTIAL` (`periodic_offsite_upload_proven = false`).
   - Ngay khi Owner cấp đích lưu trữ ngoài VPS và thông tin xác thực, chỉ cần khởi tạo `/etc/crm-cskh/offsite-backup.env` (`0600`) với cặp `CSKH_OFFSITE_UPLOAD_CMD` + `CSKH_OFFSITE_VERIFY_CMD` (hoặc mount kho mạng ngoài VPS vào `CSKH_OFFSITE_TARGET_DIR`) và `CSKH_OFFSITE_LOCATION_LABEL`, sau đó chạy thử `sudo systemctl start crm-cskh-backup.service` để chứng minh đọc ngược SHA-256 tự động (`end_to_end_backup_status = "PASS"`).
2. **Rào chắn kỹ thuật bắt buộc (Không chọn dịch vụ trả phí hộ Owner & Không coi thư mục cùng VPS là Offsite):**
   - **Không tự chọn dịch vụ lưu trữ trả phí** khi chưa có phê duyệt của Owner.
   - **Cấm tuyệt đối coi một thư mục khác trên cùng máy chủ VPS `103.77.208.186` là đích offsite:** Hàm `sync_bundle_offsite_automated()` trong `manage_backup_lifecycle.py` kiểm tra mã thiết bị tệp (`st_dev`) giữa `backup_root` và `offsite_target_dir`; nếu hai đường dẫn nằm trên cùng ổ đĩa/cùng filesystem của VPS, công cụ từ chối ngay với `ValueError` và phát cảnh báo `ALT-BACKUP-FAILURE`.
   - **Cấm đẩy khóa giải mã:** Công cụ chỉ chuyển `backup.tar.gz.enc` + `backup-report.json`, đồng thời chặn cứng nếu `CSKH_OFFSITE_UPLOAD_CMD` tham chiếu tới `backup-key` hoặc nếu `backup-key` xuất hiện tại đích.
3. **Danh sách chính xác đầu vào cần Owner cấp để mở khóa Offsite tự động (`BLK-INFRA-05`):**

| STT | Hạng mục đầu vào cần Owner cấp | Yêu cầu kỹ thuật & an toàn bắt buộc | Trạng thái hiện tại |
| :---: | :--- | :--- | :---: |
| 1 | **Loại đích lưu trữ ngoài VPS** | Máy chủ Linux/NAS SFTP độc lập khác vật lý với `103.77.208.186` **hoặc** kho Object Storage tương thích S3 / Rclone sẵn có của Owner (không tự đăng ký dịch vụ trả phí). | `CHỜ OWNER CẤP (BLK-INFRA-05)` |
| 2 | **Địa chỉ đích & Đường dẫn / Bucket** | `host:port` + thư mục đích (`0700`) hoặc `endpoint URL` + tên `bucket/prefix` dành riêng cho `crm-cskh`. | `CHỜ OWNER CẤP (BLK-INFRA-05)` |
| 3 | **Thông tin xác thực tách quyền (`Least Privilege`)** | Khóa SSH `ed25519` riêng hoặc cặp Access Key chỉ có quyền ghi gói mới + đọc kiểm tra SHA-256 (**không dùng chung khóa quản trị có quyền xóa toàn bộ kho**). | `CHỜ OWNER CẤP (BLK-INFRA-05)` |
| 4 | **Kho lưu ký khóa tách biệt (`Key Escrow`)** | Định danh kho bí mật độc lập của Owner để lưu `backup-key` của từng gói và `encryption_key` của `site_config.json` (tách rời hoàn toàn khỏi đích lưu `backup.tar.gz.enc`). | `Đang lưu thủ công riêng bởi Owner/T0` |
| 5 | **Chính sách dung lượng & RPO/RTO phê duyệt** | Xác nhận hạn mức dung lượng cho chu kỳ GFS `14 ngày / 8 tuần / 3 tháng` tại kho đích và ký xác nhận ngưỡng RPO (`<= 24h`) / RTO mục tiêu. | `CHỜ OWNER DUYỆT` |

4. **Cảnh báo tự động khi sao lưu lỗi hoặc quá hạn (`> 26 giờ`):**
   - `crm-cskh-backup.service` gắn `OnFailure=crm-cskh-backup-failure@%n.service` gọi `manage_backup_lifecycle.py emit-failure-alert`, ghi biên bản `ALT-BACKUP-FAILURE` đã khử PII/secret vào `/var/backups/crm-cskh/backup-alerts.jsonl` (`0600`) và giữ khóa kênh ngoài cho tới khi Owner mở `BLK-GOV-06` + `BLK-INFRA-07` trong `/etc/crm-cskh/alerting.env`.
   - `crm-cskh-healthcheck.service` (chạy tự động mỗi 5 phút qua `crm-cskh-healthcheck.timer`) kiểm tra tham số `--backup-status-file /var/backups/crm-cskh/latest-backup-lifecycle.json` (`--max-backup-age-hours 26.0`) để phát hiện sớm lịch backup bị kẹt hoặc quá hạn.
5. **Quy trình tạm thời xác nhận bản sao Offsite thủ công (trong thời gian chờ `BLK-INFRA-05`):**
Sau khi kéo `backup.tar.gz.enc` và `backup-report.json` về kho lưu trữ ngoài VPS qua `scp`, lưu ký riêng `backup-key` vào Password Vault độc lập và đối chiếu SHA-256 khớp hai đầu (lưu ý: biên nhận thủ công luôn giữ `periodic_offsite_upload_proven = false`):

```bash
# 1. Kiểm tra lại toàn vẹn gói backup tại chỗ:
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py verify-bundle \
  --bundle-dir /var/backups/crm-cskh/scheduled-20261109T130029Z-1400885

# 2. Ghi biên nhận đã chép ra ngoài VPS và khớp SHA-256:
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py record-offsite \
  --bundle-dir /var/backups/crm-cskh/scheduled-20261109T130029Z-1400885 \
  --verified-sha256 "<SHA256_DO_TREN_KHO_OFFSITE>" \
  --offsite-location-label "owner-windows-protected-archive-separate-key-escrow"

# 3. Ghi biên nhận lưu ký khóa tách biệt (0 secret, chỉ ghi dấu vân tay SHA-256 của backup-key):
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py record-key-escrow \
  --bundle-dir /var/backups/crm-cskh/scheduled-20261109T130029Z-1400885 \
  --vault-label "<TEN_KHO_LUU_KY_KHOA_DOC_LAP_CUA_OWNER>"

# 4. Kiểm toán 4 bằng chứng runtime để đóng cổng G07 (trả BLOCKED khi chưa đủ timer + offsite tự động + remote SHA-256 + kho khóa riêng):
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py audit-g07-gate \
  --lifecycle-file /var/backups/crm-cskh/latest-backup-lifecycle.json \
  --timers-runtime-file /var/lib/crm-cskh/health/timers-runtime.json \
  --key-escrow-receipt-file /var/backups/crm-cskh/scheduled-20261109T130029Z-1400885/key-escrow-receipt.json \
  --restore-report-file /opt/crm-p4-restore-input-20261109/restore-drill-report.json \
  --out /var/backups/crm-cskh/g07-gate-audit.json
```

### 3.3. Chính Sách Lưu Giữ GFS (`Grandfather-Father-Son` — `14d / 8w / 3m`)
`manage_backup_lifecycle.py prune-retention` thực thi chính sách giữ lại:
- **`14` bản ngày gần nhất (`daily`)**
- **`8` bản tuần gần nhất (`weekly` — theo tuần ISO)**
- **`3` bản tháng gần nhất (`monthly` — theo tháng dương lịch)**
- **Quy tắc an toàn:** Không bao giờ xóa bản hợp lệ mới nhất (`newest_verified`), không xóa các bản chưa kiểm chứng để phục vụ điều tra lỗi (`skipped_unverified`), không xóa các bản gắn nhãn mốc phát hành (`pre-scheduler`, `pre-release`, `pre-cutover`, `golden`, hoặc có tệp `.pinned`), và khi gọi độc lập mặc định chỉ chạy chế độ xem trước trừ khi truyền `--apply`:

```bash
# Xem trước kế hoạch giữ/xóa (dry-run an toàn):
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py prune-retention \
  --backup-root /var/backups/crm-cskh \
  --keep-daily 14 \
  --keep-weekly 8 \
  --keep-monthly 3

# Thực thi dọn các bản hết hạn lưu giữ (chỉ xóa bundle hợp lệ ngoài cửa sổ 14d/8w/3m và không gắn nhãn mốc):
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py prune-retention \
  --backup-root /var/backups/crm-cskh \
  --keep-daily 14 \
  --keep-weekly 8 \
  --keep-monthly 3 \
  --apply
```

### 3.4. Quy Trình Ứng Phó Khi Sao Lưu Lỗi Hoặc Quá Hạn (`ALT-BACKUP-FAILURE`)
Khi `/var/lib/crm-cskh/health/latest-health-report.json` báo `backup_freshness_and_offsite.status == "FAIL"` (tuổi gói `> 26.0 giờ` hoặc `local_backup_status == "FAIL"`) hoặc xuất hiện bản ghi mới trong `/var/backups/crm-cskh/backup-alerts.jsonl`:

1. **Giữ nguyên các bản sao lưu hợp lệ trước đó:** Tuyệt đối không xóa tay các thư mục bundle cũ trong `/var/backups/crm-cskh` khi chưa tạo được bản sao lưu mới đạt `PASS`.
2. **Chẩn đoán nguyên nhân gốc (chỉ đọc, không gây gián đoạn site live):**
   - Xem nhật ký chi tiết của lần chạy lỗi: `sudo journalctl -u crm-cskh-backup.service -u 'crm-cskh-backup-failure@*' -n 100 --no-pager` và dòng cuối `/var/backups/crm-cskh/backup-alerts.jsonl`.
   - Kiểm tra dung lượng & inode ổ đĩa: `df -h /var/backups/crm-cskh /var/lib/docker && df -i /var/backups/crm-cskh`.
   - Kiểm tra trạng thái MariaDB và container `backend`: `/opt/crm-cskh-staging/staging-compose ps db backend`.
   - Nếu `local_backup_status == "PASS"` nhưng `status == "FAIL"` do lỗi mạng/đích offsite: kiểm tra kết nối SSH/HTTPS tới kho ngoài VPS và quyền tệp `/etc/crm-cskh/offsite-backup.env` (`0600`).
3. **Khắc phục & Chạy bù ngay lập tức:**
   - Nếu đầy đĩa (`>= 80%`): chạy `manage_backup_lifecycle.py prune-retention --backup-root /var/backups/crm-cskh --apply` và dọn thư mục bundle lỗi rỗng/dở dang (sau khi đã ghi nhận log).
   - Kích hoạt chạy bù: `sudo systemctl start crm-cskh-backup.service`.
4. **Xác nhận phục hồi & Lưu ký khóa:**
   - Kiểm tra `/var/backups/crm-cskh/latest-backup-lifecycle.json` đạt `local_backup_status == "PASS"`.
   - Chép `backup-key` của gói mới vào Password Vault độc lập của Owner; nếu chưa mở `BLK-INFRA-05`, kéo `backup.tar.gz.enc` về kho ngoài VPS, kiểm tra `sha256sum` hai đầu và chạy `record-offsite`.
   - Kích hoạt `sudo systemctl start crm-cskh-healthcheck.service` và xác nhận `backup_freshness_and_offsite.status == "PASS"` trong `/var/lib/crm-cskh/health/latest-health-report.json`.

---

## 4. Quy Trình Diễn Tập Khôi Phục Thảm Họa Cô Lập (`verify_offsite_restore.py` — Cổng `G08`)

### 4.1. Rào chắn an toàn tuyệt đối với Site Live của Owner
1. Chỉ đọc cấu hình của project nguồn (`crm-cskh-vps-staging`) để xác định tên image và danh sách volume/container cần bảo vệ; **không ghi, không seed, không dừng service, không chạm volume của `crm-staging.localhost`**.
2. Dựng stack khôi phục dùng một lần (`crm-p4-restore-<hex>`) trên mạng Docker `internal: true`, **`0` host port xuất bản ra ngoài**, **`0` bind mount / volume dùng chung với site live**, tắt cứng `scheduler` và `mute_emails = 1`.
3. Mọi bước tạo dữ liệu đối chứng (`Positive Control`) chỉ diễn ra trên container tạm của bản clone sau khi đã đối soát xong bản sao lưu gốc, và toàn bộ container/volume/network tạm bị hủy sạch (`docker compose down --volumes --remove-orphans`) trong khối `finally`.

### 4.2. Hai pha kiểm định bắt buộc trong một lần chạy
- **Pha A — Khôi phục bản sao lưu thật + `migrate ×2` + HTTP Smoke + Đo RTO từng chặng:**
  1. Kiểm tra SHA-256 của `backup.tar.gz.enc` trước khi gọi bất kỳ lệnh Docker nào; giải mã OpenSSL và kiểm tra SHA-256 từng tệp thành phần.
  2. Khởi tạo MariaDB + Redis + Backend + Frontend Nginx cô lập, chạy `bench new-site` và `bench restore` kèm `--with-public-files` & `--with-private-files`, phục hồi `site_config_backup.json` (`0600`).
  3. Kiểm định `evaluate_encryption_key_state()`, đối soát 100% số dòng và SHA-256 bảng nghiệp vụ, kiểm tra byte/SHA-256 và quyền POSIX (`uid=1000`, không world-writable) của toàn bộ tệp public/private.
  4. Chạy **`bench migrate` lần 1 và lần 2 (`migrate ×2`)**, xóa cache, xác nhận số dòng không đổi, SHA-256 sau lần 1 và lần 2 trùng khít (`idempotent`), **bảo toàn toàn bộ bản ghi ghi đè `Translation` của Owner** + kiểm chứng `frappe._()` tiếng Việt thực tế, và cả 6 bất biến `INV-01..06 == 0`.
  5. Khởi động Gunicorn + Nginx nội bộ: xác nhận Guest truy cập `/crm` & `/cskh` bị chặn `403`, đăng nhập thật qua `POST /api/method/login` (`sid` có `HttpOnly`), tải `/crm` (`200`), tải `/cskh` (`200`, có tiêu đề `Không gian tuyển dụng`), kiểm tra CSRF giả bị từ chối (`400`/`403`) và CSRF thật đạt `200`.
- **Pha B — Đối chứng Dương & Âm (`Positive & Negative Control`) trên Clone cô lập:**
  1. Tạo một bí mật mã hóa Fernet thật qua `set_encrypted_password` (`__Auth.encrypted = 1`, ciphertext `gAAAAAB...`), tạo tệp `public/files` và `private/files` khác rỗng, và tạo 1 `CRM Lead` synthetic trên bản clone.
  2. Chạy trọn vòng `bench backup --with-files` → đóng gói → mã hóa OpenSSL → giải mã → trích xuất an toàn.
  3. Xóa tệp & xóa `encryption_key` trên clone → chứng minh **Negative Control fail-closed**: `evaluate_encryption_key_state()` chặn đứng lỗi mất khóa (`Missing encryption_key`), đồng thời `run_negative_control_decrypt_probe()` gọi `get_decrypted_password(..., raise_exception=True)` theo hợp đồng nghiêm ngặt: không bao giờ bắt `AssertionError`, mọi trường hợp hàm giải mã trả về mà không ném lỗi đều làm ca kiểm chứng `FAIL`, mọi lỗi DB/SQL/import/`AuthenticationError("Password not found")` đều làm ca kiểm chứng `FAIL`, và **chỉ** chấp nhận lỗi mất khóa/giải mã Fernet dự kiến (`cryptography.fernet.InvalidToken` / `ValidationError` khóa mã hóa không hợp lệ).
  4. Khôi phục lại `site_config_backup.json` (`0600`) và tệp từ gói sao lưu mới → chứng minh **Positive Control**: `get_decrypted_password()` trả về đúng từng ký tự gốc cả trước và sau `migrate ×2` idempotent, byte & SHA-256 tệp public/private khớp 100%, và qua Nginx: **Guest đọc tệp public đạt `200`, Guest đọc tệp private bị chặn `403`, User đã đăng nhập đọc tệp private đạt `200`**.

### 4.3. Quy tắc đo & Phạm vi chính xác của thời gian khôi phục (`RTO`)
Báo cáo JSON tách bạch rõ `sql_import_only_is_full_rto: false` và ghi nhận đầy đủ 9 chặng (kèm số thực đo trên VPS `103.77.208.186` ngày `09/11/2026` tại [`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json)):
1. `archive_checksum_decrypt_and_extract`: **`0.364 giây`**
2. `isolated_stack_bootstrap_and_db_ready`: **`13.619 giây`**
3. `new_site_init`: **`66.149 giây`**
4. `sql_and_files_restore_only`: **`22.585 giây`** *(chỉ riêng thời gian nạp SQL + bung tar tệp — **không gọi là RTO đầy đủ**)*
5. `pre_migrate_reconciliation`: **`6.048 giây`**
6. `migrate_run_1`: **`10.247 giây`**
7. `migrate_run_2`: **`9.767 giây`**
8. `post_migrate_reconciliation_and_invariants`: **`4.445 giây`**
9. `web_service_start_and_authenticated_http_smoke`: **`7.841 giây`**

- **Tổng Pha A (`measured_end_to_end_isolated_dr_rto_seconds` trên VPS `103.77.208.186`):** **`142.598 giây`** (`~2 phút 23 giây`). Thời gian chạy Pha B đối chứng Dương/Âm (`positive_control_drill_seconds_excluded_from_rto = 66.366 giây`, tổng wall-clock = `209.052 giây`) được tách riêng để không làm lệch số đo RTO.
- **Giới hạn phạm vi RTO bắt buộc ghi rõ (`measurement_scope = isolated_single_host_container_dr_drill_excluding_offsite_wan_transfer`):**
  - Con số **`142.598 giây`** là **thời gian khôi phục bản clone cô lập ngay trên cùng máy chủ VPS `103.77.208.186`** khi gói sao lưu mã hóa và các Docker image (`acb9505...`, MariaDB, Redis) đã nằm sẵn trên ổ đĩa cục bộ của VPS.
  - Con số này **CHƯA BAO GỒM**:
    1. Thời gian truyền tải gói `backup.tar.gz.enc` và Docker image qua mạng **WAN** từ kho lưu trữ offsite / Container Registry về máy chủ đích;
    2. Thời gian **cấp phát máy chủ VPS mới**, cài đặt hệ điều hành, khóa tường lửa (`ufw` + `DOCKER-USER`) và cài đặt Docker Engine khi mất hoàn toàn VPS cũ;
    3. Thời gian **chuyển đổi bản ghi DNS** (`TTL` lan truyền) và cấp phát lại chứng chỉ TLS Let's Encrypt cho tên miền công khai.

### 4.4. Lệnh thực thi chuẩn trên VPS `103.77.208.186` (dành cho `T0` / `Operator` khi diễn tập định kỳ hằng tháng)

Sau khi đã chuẩn bị thư mục mã nguồn vận hành `/opt/crm-cskh-ops` đúng commit SHA theo **Mục 3.0**:

```bash
test -f /opt/crm-cskh-ops/deploy/ops/verify_offsite_restore.py
sudo python3 /opt/crm-cskh-ops/deploy/ops/verify_offsite_restore.py \
  --archive /opt/crm-p4-restore-input-20261109/backup.tar.gz.enc \
  --archive-sha256 d6f1a0a1600f92d9b0b486665da486f74f209b676e43c15835e6c4a74d877e11 \
  --key-file /opt/crm-p4-restore-input-20261109/backup-key \
  --source-project crm-cskh-vps-staging \
  --site crm-staging.localhost \
  --out /opt/crm-p4-restore-input-20261109/restore-drill-report.json
```

---

## 5. Hồ Sơ Đối Chiếu Thực Thi (Tách Biệt Rõ Evidence VPS Live Và Evidence Container CI)

### 5.1. Danh mục các bản sao lưu từ VPS `103.77.208.186` đã xác thực ngoài VPS

| Định danh bản sao lưu | Nguồn & Revision ứng dụng | Kích thước (`bytes`) | SHA-256 (`backup.tar.gz.enc`) | Nguồn tạo & Trạng thái Offsite / Khóa |
| :--- | :--- | :---: | :--- | :--- |
| `p4-20261109-pre-scheduler` | `crm-staging.localhost` (`467204176326f046442b5e20c381f9a895b6e176`) | `312.672` | `d6f1a0a1600f92d9b0b486665da486f74f209b676e43c15835e6c4a74d877e11` | Chép thủ công về `C:\Users\Admin\.codex\backups\crm-hrpartner\p4-20261109-pre-scheduler` (`OFFSITE_COPY_HASH_VERIFIED`); đã dùng làm đầu vào diễn tập khôi phục cô lập trên VPS đạt **`PASS`** (`142.598s`). |
| `scheduled-20261109T130029Z-1400885` | `crm-staging.localhost` (`467204176326f046442b5e20c381f9a895b6e176`) | `349.920` | `33fdf18bf4e9168e98baee65446c215e005b44271a9507cd94b3e1e86f34e14c` | Sinh bởi lần khởi chạy thủ công `systemctl start crm-cskh-backup.service` lúc `20:00:28 +07` ngày `09/11/2026` ([`scheduled-backup-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/scheduled-backup-report.json)); đã chép thủ công ra ngoài VPS khớp SHA-256 lúc `20:03:25 +07`, `backup-key` lưu riêng ([`offsite-verification.json`](../../deploy/ops/evidence/p4-vps-live-20261109/offsite-verification.json), [`manual-offsite-receipt.json`](../../deploy/ops/evidence/p4-vps-live-20261109/manual-offsite-receipt.json)). |

### 5.2. Bảng tách biệt Bằng chứng Thực thi trên VPS (`p4-vps-live-20261109`) và Bằng chứng Diễn tập CI (`p4-g07-g08-dr-20261109`)

> [!NOTE]
> Toàn bộ lịch sử kiểm thử container trên GitHub Actions (`ubuntu-24.04`) được giữ nguyên bất biến tại [`deploy/ops/evidence/p4-g07-g08-dr-20261109/`](../../deploy/ops/evidence/p4-g07-g08-dr-20261109/), hoàn toàn tách biệt với bộ bằng chứng thực thi trực tiếp trên VPS `103.77.208.186` do `T0` bàn giao tại [`deploy/ops/evidence/p4-vps-live-20261109/`](../../deploy/ops/evidence/p4-vps-live-20261109/) ([`vps-evidence-manifest.json`](../../deploy/ops/evidence/p4-vps-live-20261109/vps-evidence-manifest.json), SHA-256 `daa60a1366452c90fcaa1ea3a2f549d82a2d8aa06c6bb697312ea173b2f79493`).

| Hạng mục đối chiếu | Thực thi trực tiếp trên VPS `103.77.208.186` (`p4-vps-live-20261109`) | Diễn tập Container CI `ubuntu-24.04` (`p4-g07-g08-dr-20261109` — lịch sử bảo toàn) |
| :--- | :--- | :--- |
| **Commit mã nguồn vận hành** | `801be22b3be2c8199704c4da7442b0c2e0edf352` (`/opt/crm-cskh-ops`) | `49678c49154ad21fddb1ddc38d5b905959a504e2` (GitHub Actions Run `#42615723010`) |
| **Diễn tập Restore cô lập (`G08`)** | **`PASS`** — Pha A RTO clone cùng VPS = **`142.598s`** (`sql_and_files_restore_only = 22.585s`), `migrate ×2` `[10.247s, 9.767s]`, `5/5 Translation` overrides `PASS` ([`restore-drill-report.json`](../../deploy/ops/evidence/p4-vps-live-20261109/restore-drill-report.json)) | **`PASS`** — Pha A RTO container tổng hợp = `50.352s` (`sql_and_files_restore_only = 2.965s`), `5/5 Translation` overrides `PASS` ([`g08-restore-drill-report.json`](../../deploy/ops/evidence/p4-g07-g08-dr-20261109/g08-restore-drill-report.json)) |
| **Positive / Negative Control mã hóa & Files (Pha B)** | **`PASS`** — Fernet round-trip + `migrate ×2` `[10.635s, 11.234s]`, Negative Control bắt đúng `ValidationError`, HTTP ACL public `200` / Guest private `403` / Auth private `200` | **`PASS`** — Fernet round-trip + `migrate ×2`, Negative Control bắt đúng `ValidationError`, HTTP ACL public `200` / Guest private `403` / Auth private `200` |
| **Dọn dẹp & Bảo toàn Site Live** | `cleanup_exit_code = 0`, `0` container/volume/network dư; `11` container live (`10` daemon `healthy`) & image `acb9505...` không đổi ([`live-preservation-check.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-preservation-check.json)) | `cleanup_exit_code = 0`, `0` container/volume/network dư; không chạm môi trường live |
| **Timer sức khỏe 5 phút (`crm-cskh-healthcheck.timer`)** | **`PASS` (Tự kích hoạt theo lịch)** — `enabled`/`active`, tự chạy lúc `20:05:45 +07` → `20:05:47 +07` (`ExecMainStatus=0`), báo cáo sức khỏe `PASS` ([`timers-runtime.json`](../../deploy/ops/evidence/p4-vps-live-20261109/timers-runtime.json), [`live-health-after.json`](../../deploy/ops/evidence/p4-vps-live-20261109/live-health-after.json)) | Dry-run & parser check `PASS` ([`systemd-timers-dry-run.json`](../../deploy/ops/evidence/p4-g07-g08-dr-20261109/systemd-timers-dry-run.json)); lúc bàn giao CI chưa chạy trên VPS (`NOT_RUN`) |
| **Dịch vụ & Timer sao lưu (`crm-cskh-backup.*`)** | Service chạy thật thủ công **`PASS`** (`20:00:28 +07` → `20:00:36 +07`); `crm-cskh-backup.timer` đã `enabled`/`active` (`NextElapse = 2026-11-10 02:16:41 +07`), **chưa có lượt tự kích hoạt theo lịch** (`LastTriggerUSec = ""`) | Diễn tập vòng đời cục bộ, biên nhận thủ công (`PARTIAL`), đồng bộ tự động container và cảnh báo `ALT-BACKUP-FAILURE` đạt `PASS` ([`g07-backup-lifecycle-drill.json`](../../deploy/ops/evidence/p4-g07-g08-dr-20261109/g07-backup-lifecycle-drill.json)) |
| **Đồng bộ Offsite tự động & Cảnh báo ngoài** | **`PARTIAL` / `BLOCKED`** — Chờ Owner cấp đích lưu trữ ngoài VPS (`BLK-INFRA-05`) và đầu mối/webhook cảnh báo (`BLK-GOV-06`, `BLK-INFRA-07`) | Đã kiểm chứng logic mã nguồn trên container Linux; giữ khóa kênh ngoài mặc định |
