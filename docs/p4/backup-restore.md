# Sao Lưu Mã Hóa, Vòng Đời Lưu Giữ & Diễn Tập Khôi Phục Thảm Họa Cô Lập (`P4-05` — Cổng `G07`, `G08`)

- **Mã tài liệu:** `P4-BACKUP-RESTORE` (`docs/p4/backup-restore.md` — `P4.md §7`, `§15.3`, `§20.2`)
- **Bộ công cụ thực thi:**
  - Tạo gói sao lưu mã hóa: [`deploy/ops/create_encrypted_backup.py`](../../deploy/ops/create_encrypted_backup.py)
  - Kiểm tra checksum, biên nhận offsite & GFS retention: [`deploy/ops/manage_backup_lifecycle.py`](../../deploy/ops/manage_backup_lifecycle.py)
  - Khôi phục diễn tập DR cô lập + Positive Control: [`deploy/ops/verify_offsite_restore.py`](../../deploy/ops/verify_offsite_restore.py)
  - Cài đặt lịch systemd tự động trên VPS: [`deploy/ops/install_systemd_timers.py`](../../deploy/ops/install_systemd_timers.py) (`crm-cskh-backup.service` / `crm-cskh-backup.timer`)
  - Kiểm thử đơn vị: [`deploy/ops/test_offsite_restore.py`](../../deploy/ops/test_offsite_restore.py)

> [!WARNING]
> **Báo cáo trung thực trạng thái Sao lưu End-to-End (`G07`):**
> - **Đã hoàn tất trong mã nguồn & kiểm chứng thực tế trên container Linux:** Tạo gói sao lưu mã hóa AES-256-CBC-PBKDF2 kèm kiểm tra toàn vẹn trước đóng gói, đồng bộ offsite tự động kèm đọc ngược SHA-256 hai đầu và tách biệt khóa giải mã (`sync_bundle_offsite_automated` — tuyệt đối không bao giờ chép `backup-key` sang kho offsite), chính sách lưu giữ GFS (`14 ngày / 8 tuần / 3 tháng`), và phát cảnh báo tức thì khi sao lưu lỗi/quá hạn (`OnFailure=crm-cskh-backup-failure@%n.service` → `ALT-BACKUP-FAILURE`).
> - **Một bản sao chép thủ công về máy Owner (`p4-20261109-pre-scheduler`, SHA-256 `d6f1a0a1600f92d9b0b486665da486f74f209b676e43c15835e6c4a74d877e11`) KHÔNG chứng minh upload định kỳ tự động đang chạy** (`one_off_manual_copy_proves_periodic_upload = false`).
> - **Trên VPS live `103.77.208.186` hiện tại (`PARTIAL_MANUAL_OFFSITE_VERIFIED_AUTOMATION_BLOCKED` bởi `BLK-INFRA-05`):** Cho tới khi cấu hình kho đích ngoài VPS (`--offsite-target-dir` gắn kho ngoài hoặc `--offsite-upload-cmd` + `--offsite-verify-cmd` sang S3/B2/SSH kho độc lập), tiến trình hằng đêm chỉ báo `PASS` phần cục bộ và giữ nguyên trạng thái `periodic_offsite_upload_proven = false`.

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
> **Quy định Lưu ký Khóa (`Escrow`):** Ngay khi cấu hình tích hợp ngoài đầu tiên (SMTP, tổng đài, OAuth, webhook có mật khẩu mã hóa) làm phát sinh `encryption_key` trong `/opt/crm-cskh-staging` / `site_config.json`, Người vận hành **bắt buộc** sao lưu giá trị `encryption_key` vào kho lưu ký bí mật độc lập ngoài VPS (Password Vault của Owner) và chạy `create_encrypted_backup.py` ngay lập tức.

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

### 3.2. Upload Offsite Tự Động Kèm Đọc Ngược SHA-256 & Cảnh Báo Lỗi (`G07`)
1. **Khi đã cấp phát kho đích ngoài VPS (`BLK-INFRA-05`):** Truyền `--offsite-target-dir <MOUNT_OFFSITE>` hoặc cặp `--offsite-upload-cmd` + `--offsite-verify-cmd` cho `manage_backup_lifecycle.py run-scheduled` (hoặc biến môi trường `CSKH_OFFSITE_TARGET_DIR` / `CSKH_OFFSITE_UPLOAD_CMD` + `CSKH_OFFSITE_VERIFY_CMD`). Công cụ chỉ đẩy `backup.tar.gz.enc` + `backup-report.json`, **chặn cứng nếu `backup-key` xuất hiện ở đích hoặc đích nằm lồng trong `backup_root`**, đọc ngược SHA-256 + kích thước byte từ đích và chỉ khi khớp tuyệt đối mới đặt `periodic_offsite_upload_proven = true` (`end_to_end_backup_status = "PASS"`). Khi chưa có đích upload tự động, trạng thái bắt buộc giữ `PARTIAL` (`PARTIAL_LOCAL_ONLY_OFFSITE_PENDING` hoặc `PARTIAL_MANUAL_OFFSITE_VERIFIED_AUTOMATION_BLOCKED`).
2. **Cảnh báo tự động khi sao lưu lỗi hoặc quá hạn (`> 26 giờ`):**
   - `crm-cskh-backup.service` gắn `OnFailure=crm-cskh-backup-failure@%n.service` gọi `manage_backup_lifecycle.py emit-failure-alert`, ghi biên bản `ALT-BACKUP-FAILURE` đã khử PII/secret vào `/var/backups/crm-cskh/backup-alerts.jsonl` (`0600`) và giữ khóa kênh ngoài cho tới khi Owner mở `BLK-GOV-06` + `BLK-INFRA-07`.
   - `crm-cskh-healthcheck.service` kiểm tra mỗi 5 phút tham số `--backup-status-file /var/backups/crm-cskh/latest-backup-lifecycle.json` (`--max-backup-age-hours 26.0`) để phát hiện sớm lịch backup bị kẹt hoặc lỗi.
3. **Xác nhận bản sao Offsite thủ công (khi chưa gỡ `BLK-INFRA-05`):**
Sau khi kéo `backup.tar.gz.enc` và `backup-report.json` về kho lưu trữ ngoài VPS qua `scp`, lưu ký riêng `backup-key` vào Password Vault độc lập và đối chiếu SHA-256 khớp hai đầu (lưu ý: biên nhận thủ công giữ `periodic_offsite_upload_proven = false`):

```bash
# 1. Kiểm tra lại toàn vẹn gói backup tại chỗ:
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py verify-bundle \
  --bundle-dir /var/backups/crm-cskh/scheduled-20261109T191500Z

# 2. Ghi biên nhận đã chép ra ngoài VPS và khớp SHA-256:
sudo python3 /opt/crm-cskh-ops/deploy/ops/manage_backup_lifecycle.py record-offsite \
  --bundle-dir /var/backups/crm-cskh/scheduled-20261109T191500Z \
  --verified-sha256 "<SHA256_DO_TREN_KHO_OFFSITE>" \
  --offsite-location-label "workstation-offsite-vault:crm-hrpartner"
```

### 3.3. Chính sách Lưu giữ GFS (`Grandfather-Father-Son`)
`manage_backup_lifecycle.py prune-retention` thực thi chính sách giữ lại:
- **`14` bản ngày gần nhất (`daily`)**
- **`8` bản tuần gần nhất (`weekly` — theo tuần ISO)**
- **`3` bản tháng gần nhất (`monthly` — theo tháng dương lịch)**
- **Quy tắc an toàn:** Không bao giờ xóa bản hợp lệ mới nhất (`latest_verified`), không xóa các bản gắn nhãn mốc phát hành (`pre-scheduler`, `pre-release`, `milestone`, hoặc có tệp `.pinned`), và mặc định chỉ chạy chế độ xem trước trừ khi truyền `--apply`.

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

### 4.3. Quy tắc đo thời gian khôi phục (`RTO`)
Báo cáo JSON tách bạch rõ `sql_import_only_is_full_rto: false` và ghi nhận đầy đủ 9 chặng:
1. `archive_checksum_decrypt_and_extract`
2. `isolated_stack_bootstrap_and_db_ready`
3. `new_site_init`
4. `sql_and_files_restore_only` *(chỉ riêng thời gian nạp SQL + bung tar tệp — **không gọi là RTO đầy đủ**)*
5. `pre_migrate_reconciliation`
6. `migrate_run_1`
7. `migrate_run_2`
8. `post_migrate_reconciliation_and_invariants`
9. `web_service_start_and_authenticated_http_smoke`

Tổng thời gian từ chặng 1 đến hết chặng 9 là `measured_end_to_end_isolated_dr_rto_seconds` (chưa bao gồm thời gian tải mạng WAN từ kho offsite hoặc cấp phát VPS mới nếu mất toàn bộ máy chủ). Thời gian chạy Pha B (`positive_control_drill_seconds_excluded_from_rto`) được tách riêng để không làm sai lệch số đo RTO.

### 4.4. Lệnh thực thi chuẩn trên VPS `103.77.208.186` (dành cho `T0` / `Operator`)

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

## 5. Hồ Sơ Bản Sao Lưu Đã Đối Chiếu Hiện Hành

| Định danh bản sao lưu | Nguồn & Revision ứng dụng | Kích thước (`bytes`) | SHA-256 (`backup.tar.gz.enc`) | Trạng thái Offsite & Khóa |
| :--- | :--- | :---: | :--- | :--- |
| `p4-20261109-pre-scheduler` | `crm-staging.localhost` (`467204176326f046442b5e20c381f9a895b6e176`) | `312.672` | `d6f1a0a1600f92d9b0b486665da486f74f209b676e43c15835e6c4a74d877e11` | Đã chép về `C:\Users\Admin\.codex\backups\crm-hrpartner\p4-20261109-pre-scheduler` (`OFFSITE_COPY_HASH_VERIFIED`); site chưa sinh `encryption_key` (`__Auth encrypted=1` = `0` dòng). |
