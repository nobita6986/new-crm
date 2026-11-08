# Hồ sơ Tích hợp `P4-00` + `P4-01` (`codex/p4-01-integration`) — Bàn giao cho `T0` Review

- **Nhánh tích hợp:** `codex/p4-01-integration`
- **Baseline ứng dụng (`main`):** `92e14af3532876ad5bc2468bf0e502474cc95a53`
  - `git rev-parse HEAD:apps`: `b37e2cbdd99c88c7bd5620912de4519b53b2c159` (`0` thay đổi trong `apps/**`)
  - `git rev-parse HEAD:apps/crm_cskh`: `da6373a01ad963301877f69c413fdda9a361f718`
  - `.github/workflows/**`: `0` thay đổi
- **Quyết định merge `main`:** Do `T0` review và quyết định.

---

## 1. Ba Mốc Bàn giao Đã Tích hợp & Provenance Thực Đo Được Bảo Toàn

| Hạng mục | Mốc bàn giao tích hợp | Provenance thực chạy / thực đo trong evidence | Đường dẫn kiểm định |
| :--- | :--- | :--- | :--- |
| **`P4-00` — Kiểm tra đầu vào & Inventory** | `be33e6c931337c42b94184b2fe292e094bd51c97` (`codex/p4-entry-audit`) | Baseline `92e14af3532876ad5bc2468bf0e502474cc95a53` (CI P0–P3 `4/4 PASS`) | [`docs/p4/entry-audit.md`](./entry-audit.md), [`docs/p4/infra-inventory.md`](./infra-inventory.md) |
| **`P4-01` (`T1B`) — Custom Image, Lock & Docs** | `7569e9b33eaa8fdd14acd7284256f71db1001cc3` (`codex/p4-01-image`) | Image build & đo site sạch tại **`4de3dc8656b0077cabd4220afc6636da543e34f6`** (`APP_IMAGE=sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`, `45/45` assertions `PASS`) | [`deploy/images/`](../../deploy/images/), [`deploy/images/evidence/SHA256SUMS.txt`](../../deploy/images/evidence/SHA256SUMS.txt), [`docs/p4/p4-01-image-build-and-lock.md`](./p4-01-image-build-and-lock.md) |
| **`P4-01` (`T1A`) — Compose, Isolation & Smoke** | `12a44c99a12239a341c2f555d2e239bb6711f465` (`codex/p4-01-compose`) | Compose thực chạy lượt smoke `20261106090505-fa2d24` tại **`2c1e7594471b119f2c41a85fa12d30a85b356785`** cùng image **`4de3dc8656b0077cabd4220afc6636da543e34f6`** (đã đóng `F-P4-01`, `F-P4-02`, `F-P4-03`) | [`deploy/compose/`](../../deploy/compose/), [`deploy/compose/evidence/p4-01-isolated-smoke-report.json`](../../deploy/compose/evidence/p4-01-isolated-smoke-report.json), [`docs/p4/p4-01-compose-and-blockers.md`](./p4-01-compose-and-blockers.md) |

### Bảo toàn nguyên trạng các tệp ứng dụng, workflow và runtime đã kiểm định

- `git diff 92e14af3532876ad5bc2468bf0e502474cc95a53..HEAD -- apps/ .github/ deploy/dev/ deploy/p1/ deploy/p2/ deploy/p3/` → **rỗng tuyệt đối (`0` tệp thay đổi)**.
- `git diff 7569e9b33eaa8fdd14acd7284256f71db1001cc3..HEAD -- deploy/images/ ":!deploy/images/.gitattributes"` → **rỗng tuyệt đối (`0` byte thay đổi trên toàn bộ tệp runtime và `10/10` mã băm trong `SHA256SUMS.txt`)**; chỉ bổ sung 2 dòng `-whitespace` trong [`deploy/images/.gitattributes`](../../deploy/images/.gitattributes) cho log thô `evidence/build.log` và unified diff `evidence/upstream-patches.diff` để bảo vệ nguyên vẹn byte băm `4de3dc8` khi chạy `git diff --check`.
- `git diff 12a44c99a12239a341c2f555d2e239bb6711f465..HEAD -- deploy/compose/` → **rỗng tuyệt đối (`0` byte thay đổi trên toàn bộ 26 tệp `deploy/compose/**`, giữ nguyên provenance `2c1e759` và `4de3dc8` trong JSON/log smoke)**.
- `git diff be33e6c931337c42b94184b2fe292e094bd51c97..HEAD -- docs/p4/entry-audit.md docs/p4/infra-inventory.md` → **rỗng tuyệt đối**.

---

## 2. Bảng Trạng thái Cổng Nghiệm thu (`P4.md §19`) Theo Phạm vi Bằng chứng Hiện có

| Gate | Nội dung nghiệm thu (`P4.md §19`) | Trạng thái ghi nhận | Phạm vi bằng chứng đã xác thực & Hạng mục giữ `BLOCKED` / `PENDING` |
| :--- | :--- | :---: | :--- |
| **`G01`** | P0–P3 Exit Reports có thật, baseline `main` `92e14af` xanh 4/4 workflow | **`PASS`** | Đã đối chiếu đầy đủ trong [`docs/p4/entry-audit.md`](./entry-audit.md) (`be33e6c`); Owner vẫn cần chốt đầu mối vận hành/UAT/rollback (`BLK-GOV-06`) trước Production. |
| **`G02`** | Build custom image chứa đủ Frappe / CRM / `crm_cskh`, đúng version locked | **`PASS (Local)`**<br>*(Registry: `BLOCKED`)* | **Local PASS:** Image `sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9` (build từ `4de3dc8`, khóa `release-lock.json` trên baseline `92e14af`) đạt `45/45` assertions trên site sạch ([`clean-site-verify.json`](../../deploy/images/evidence/clean-site-verify.json)) và `10/10` SHA-256 artifacts.<br>**Registry giữ `BLOCKED`:** `approved_registry_repo_digest = null` do chưa cấp Container Registry & quyền `push`/`pull` (`BLK-INFRA-03`). |
| **`G03`** | DEV/CI/STAGING/PROD tách rõ dữ liệu/secrets/volumes, không sửa code trên VPS | **`PASS (Local)`**<br>*(Host Staging/Prod: `BLOCKED`)* | **Local PASS:** Tách mạng 3 lớp (`edge_net`, `app_net`, `data_net`), named volumes (`sites`, `logs`, `db-data`, `redis-queue-data`), secrets riêng theo `COMPOSE_PROJECT_NAME`, khóa `sites/apps.txt` (`F-P4-01`), tách `Set-Cookie` `sid` (`F-P4-02`), `0` anonymous volume rò rỉ (`F-P4-03`), `validate_compose_config.py` `PASS`, `11/11` unit tests `PASS`, isolated smoke `PASS` (`2c1e759` + `4de3dc8`).<br>**Hạ tầng thật giữ `BLOCKED`:** VPS/OS/firewall (`BLK-INFRA-01`), DNS/TLS (`BLK-INFRA-02`), kho secret & lưu ký `encryption_key` (`BLK-INFRA-04`), đích backup offsite (`BLK-INFRA-05`). |
| **`G04`** | Pipeline CI build-once / test / lint / security và artifact digest tái lập | **`PENDING` / `BLOCKED`** | Đã có hợp đồng đầu vào [`docs/p4/p4-02-pipeline-input-contract.md`](./p4-02-pipeline-input-contract.md); triển khai workflow ở gói `P4-02` sau khi mở khóa Container Registry (`BLK-INFRA-03`). |
| **`G05`** | Staging chạy cùng digest sẽ lên Production (kèm kiểm tra cookie `Secure` trên HTTPS) | **`BLOCKED`** | Chờ Registry digest (`BLK-INFRA-03`), VPS/DNS/TLS Staging (`BLK-INFRA-01`, `BLK-INFRA-02`) và kho secret Staging (`BLK-INFRA-04`). |
| **`G06`** | Diễn tập schema migration / re-run trên bản sao `N-1` của Staging | **`PENDING` / `BLOCKED`** | Chờ hạ tầng Staging; đã ghi nhận mục kiểm chứng `BLG-P4-MIG-01` (`CSKH Export Job` `"module": "Crm Cskh"` vs `"CRM CSKH"`) trong [`docs/p4/staging-migration-backlog.md`](./staging-migration-backlog.md). |

---

## 3. Kết quả Chạy Lại Kiểm định Trên SHA Tích hợp

1. **Compose Static & Negative-Gate Validator:**
   - Lệnh: `python -X utf8 deploy/compose/scripts/validate_compose_config.py`
   - Kết quả: **`PASS`** (`upstream_provenance` `9/9` tệp khớp `frappe_docker@v4.0.1`, render `production`/`staging`/`isolated_smoke` hợp lệ với `0` stderr warnings, khóa `apps-order.txt` + named volume `logs` trên cả 8 app services, chỉ `proxy` mở cổng host, cả `4/4` ca negative fail-closed đạt `PASS`).
2. **Unit Tests An toàn & Hồi quy của Smoke Runner (`11/11`):**
   - Lệnh: `python -X utf8 -m unittest discover -s deploy/compose/scripts -p "test_*.py" -v`
   - Kết quả: **`Ran 11 tests ... OK`** (kiểm tra `LF` byte của `db_root_password.txt`, tách đa dòng `Set-Cookie` + che `sid`, cấu hình `F-P4-01`/`F-P4-03`, cô lập project riêng từng lượt, từ chối tài nguyên sẵn có, giới hạn `down --volumes`, khử secret).
3. **Đối chiếu Toàn vẹn Evidence Image (`SHA256SUMS.txt`) & Smoke Provenance:**
   - `deploy/images/evidence/SHA256SUMS.txt`: **`10/10` tệp khớp SHA-256** (`image_git_sha = 4de3dc8656b0077cabd4220afc6636da543e34f6`).
   - `deploy/compose/evidence/p4-01-isolated-smoke-report.json`: `status = "PASS"`, `compose_provenance.head_sha = "2c1e7594471b119f2c41a85fa12d30a85b356785"`, `image_provenance.image_git_sha = "4de3dc8656b0077cabd4220afc6636da543e34f6"`.
4. **Kiểm tra Vệ sinh Diff (`git diff --check`):**
   - Lệnh: `git diff --check 92e14af3532876ad5bc2468bf0e502474cc95a53..HEAD`
   - Kết quả: **`PASS` (`exit 0`, `0` lỗi khoảng trắng / conflict marker / EOL)**.
