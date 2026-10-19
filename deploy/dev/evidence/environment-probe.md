# EVIDENCE — Dò môi trường DEV (WP-00/WP-01)

> Thời điểm: **2026-10-15**. Máy: Windows 11 Home (build 26200), 14 vCPU, 15.6 GiB RAM.
> Đây là **bằng chứng thô** cho phần "kiểm tra khả năng chạy Docker/WSL/Bench" (P0-A §A1).
> §2–4 ghi nhận lần dò **trước khi Owner cài WSL2**; trạng thái mới nhất và runtime evidence ở §8.

---

## 1. Hệ điều hành / phần cứng

```
WindowsProductName : Windows 10 Home Single Language   (CIM; thực tế là Windows 11 Home, build 26200)
OsArchitecture     : 64-bit
CPU logical cores  : 14
RAM                : 16723189760 bytes (~15.6 GiB)
C: FreeGB 136.4 / SizeGB 252.8
D: FreeGB 156.2 / SizeGB 223
HyperVisorPresent  : False
HyperVRequirementVirtualizationFirmwareEnabled : True
```

## 2. Toolchain

| Công cụ | Kết quả | Đánh giá |
|---|---|---|
| git | `git version 2.55.0.windows.5` | ✅ |
| node | `v22.22.1` | ✅ thoả `engines.node ^20.19.0 \|\| >=22.12.0` của CRM |
| python | `Python 3.12.12` | ✅ nằm trong dải v15 (`>=3.10,<3.15`); ❌ không đủ cho v16 (`3.14`) |
| yarn (host) | NOT FOUND | Host-only result from initial probe; pinned bench image has Yarn 1.22.22 (§8) |
| bench | NOT FOUND | ❌ (chỉ chạy trong Linux container/WSL) |
| python3 | trỏ tới Microsoft Store alias | ⚠️ không dùng được |

```
git     => C:\Program Files\Git\cmd\git.exe
docker  => C:\Users\Admin\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe
wsl     => C:\WINDOWS\system32\wsl.exe
node    => C:\Users\Admin\aicoworker\app\nodejs\node.exe
npm     => C:\Users\Admin\aicoworker\app\nodejs\npm.ps1   (bị chặn bởi ExecutionPolicy)
yarn    => NOT FOUND
python3 => C:\Users\Admin\AppData\Local\Microsoft\WindowsApps\python3.exe
```

## 3. Docker (lần dò trước khi cài WSL2)

```
docker version  (client) : 29.7.2      context: desktop-linux
docker compose version   : v5.4.0
docker context ls        : default -> npipe:////./pipe/docker_engine
                           desktop-linux * -> npipe:////./pipe/dockerDesktopLinuxEngine
```

`docker info` / `docker version` (hỏi server) — **THẤT BẠI** (cập nhật 2026-10-15):

```
request returned 500 Internal Server Error for API route and version
  http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.55/info,
  check if the server supports the requested API version
```

- Docker Desktop có cài: `C:\Users\Admin\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe` ✅
- Tiến trình Docker **ĐANG CHẠY**: `Docker Desktop`, `com.docker.backend`, `com.docker.build`
  (khác lần dò đầu — khi đó danh sách tiến trình rỗng).
- ➡️ **Engine Linux chưa lên**: pipe đã tồn tại nhưng API trả **500**. Chẩn đoán hiện tại: **WSL2 chưa cài** là prerequisite còn thiếu cho backend WSL2 đang chọn; chưa có Docker log xác nhận đây là nguyên nhân trực tiếp của HTTP 500 (§4).

> Lần dò đầu (cùng ngày, trước khi Docker Desktop khởi động) trả về:
> `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.`

## 4. WSL (lần dò trước khi cài WSL2)

```
wsl --status
  The Windows Subsystem for Linux is not installed.
  You can install by running 'wsl.exe --install'
```

- Truy vấn Windows optional features (`Microsoft-Windows-Subsystem-Linux`, `VirtualMachinePlatform`,
  `Microsoft-Hyper-V-All`, `Containers`) → **`The requested operation requires elevation`**
  (phiên làm việc không có quyền admin, chưa xác nhận được trạng thái chính xác).
- Windows 11 **Home** **hỗ trợ WSL2**; thiếu **Hyper-V role đầy đủ** **không** đồng nghĩa WSL2 không khả dụng.
  Docker Desktop Linux container **phụ thuộc WSL2** → cần cài WSL2 trước (WSL2 dùng `VirtualMachinePlatform`).
- `HyperVisorPresent = False` **không** đồng nghĩa WSL2 không khả dụng; trạng thái optional feature **chưa xác minh**.

---

## Blockers tại lần dò đầu (đã đối chiếu lại ở §8)

| # | Blocker | Bằng chứng | Hệ quả |
|---|---|---|---|
| B-1 | Docker Desktop chạy nhưng Linux engine trả **HTTP 500** (engine chưa lên) | §3 | Không `docker compose up`, không chạy container |
| B-2 | **WSL2 chưa cài** → Linux engine chưa lên (Home **hỗ trợ** WSL2; không cần Hyper-V role đầy đủ) | §4, `HyperVisorPresent=False` (không đồng nghĩa WSL2 không khả dụng) | Chưa chạy được Linux container **cho tới khi cài WSL2**, dù Docker Desktop đang chạy |
| B-3 | Chưa có `bench` | §2 | Không dựng được Frappe bench ngoài container |
| B-4 | Chưa có `yarn` | §2 | Không build/hot-reload frontend CRM |
| B-5 | Không có quyền elevation | §4 | Không tự cài WSL2/optional features |

**Hướng xử lý đã được thực hiện:**

- **Chính:** giữ **Docker Desktop** trên máy DEV và bật **WSL2 backend**. **Owner** cài **WSL2 + các thành
  phần Windows cần thiết** bằng quyền **admin**, rồi **khởi động lại máy**.
- **Dự phòng:** nếu **Docker + WSL2 không hoạt động được** trên máy này → **Linux DEV VM**.
- Owner đã cài WSL2 và bật WSL2 backend trên Docker Desktop; kiểm tra sau cài đặt ghi ở §8.
- Nếu Docker + WSL2 không hoạt động được trên máy này, phương án dự phòng vẫn là Linux DEV VM.

---

## 5. Bằng chứng khóa phiên bản (nguồn đúng phiên bản)

```
git ls-remote https://github.com/frappe/crm refs/tags/v1.87.0
  0d3910b456ef4fb913e77b29db011b3debc4f778   refs/tags/v1.87.0

git ls-remote https://github.com/frappe/frappe refs/tags/v15.123.1 refs/tags/v16.52.0
  d2efa79657ba5c1abf96098b3bfacc199517d57e   refs/tags/v15.123.1
  4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a   refs/tags/v16.52.0

git ls-remote https://github.com/frappe/frappe_docker refs/tags/v4.0.1
  2b26e9ab8d751e5ea33bb7b930aea3550bb81ded   refs/tags/v4.0.1
```

Checkout cục bộ `frappe/crm@v1.87.0` (đối chiếu độc lập với `ls-remote`, **tại thời điểm xác minh**):

```
git -C .survey-cache/crm describe --tags --always   ->  v1.87.0
git -C .survey-cache/crm rev-parse HEAD             ->  0d3910b456ef4fb913e77b29db011b3debc4f778
git -C .survey-cache/crm remote -v                  ->  origin https://github.com/frappe/crm.git
```

> ⚠️ Thư mục `.survey-cache/crm` **NAY KHÔNG CÒN** trong working tree (kiểm lại: `Test-Path .survey-cache` → `False`).
> Khối trên là **bằng chứng lịch sử**; muốn tái lập, clone tạm **ra ngoài** repo dự án.

Tag mới nhất theo version-sort (2026-10-15):
`frappe`: v15.123.1, v16.52.0 · `crm`: v1.87.0 · `frappe_docker`: v4.0.1

## 6. Bằng chứng image digest (Docker Hub Registry API, 2026-10-15)

```
frappe/bench:v5.31.0    index sha256:1b27bb30ab18b0a50b5264f44988335bfe8e280a68b45d0ff2daf72ca6ddb85f
                        amd64 sha256:73c0aa3fff8face360021450f9cb6dc862fa5f2e02d7e0d012fb170928a91de2
mariadb:11.8            index sha256:6422478cb8e159f080fb1d8ccf65101e26fe51385787fde7d16c3b165a331f15
                        amd64 sha256:93fc3fe333b6cdfb061425869c2c3a5bb2851c0c74dbebc2c940f024e7482d76
redis:8.6-alpine        index sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba
                        amd64 sha256:019e4fb3c6de024c8f70ab5219d4abded5f335de770876981c7b0a051c43df87
```

## 7. Trạng thái repo dự án (cập nhật 2026-10-15)

```
origin       https://github.com/nobita6986/new-crm.git
branch       main  (tracking origin/main)
HEAD         bc114d21cd099593c2dcb4396b040838f36a3cce  "Add CRM CSKH project plans"
origin/main  bc114d21cd099593c2dcb4396b040838f36a3cce   (HEAD == origin/main)
git log      1 commit — lịch sử đã amend từ fc30134f2833fa2aff43b3c1aecd707bddc95688
git status --short (untracked):
  ?? .commandcode/
  ?? deploy/
  ?? docs/architecture/
  ?? docs/operations/
```

- `.survey-cache/` **KHÔNG còn tồn tại** trong working tree (`Test-Path .survey-cache` → `False`).
  Ghi chú O-05 cũ đã được đóng trong `version-lock.md`.
- Khi commit: chỉ đưa `docs/` và `deploy/` (nội dung WP-00/WP-01). **Loại trừ** `.commandcode/`
  (trạng thái công cụ) cùng mọi cache/checkout upstream.
- Lượt này **không commit gì**; `deploy/` và `docs/{architecture,operations}/` vẫn untracked.

## 8. WP-01 runtime sau khi Owner cài WSL2 (2026-10-15)

- `wsl --status`: default version 2; `wsl -l -v`: `docker-desktop` Running, version 2.
- `docker version`: client/server 29.7.2, context `desktop-linux`; server reports Linux on WSL2. `docker compose config --quiet` passed.
- `docker compose up -d db redis-cache redis-queue bench`: all four services started; MariaDB healthcheck passed.
- `docker compose down` (without `-v`) + `up -d` recreated the containers and preserved the site; post-recreate login/API succeeded and Lead count remained 15.
- Bench image: Bench 5.31.0, Python 3.14.8, Node 24.22.0, Yarn 1.22.22. Image platform is linux/amd64; local RepoDigests match the pinned references in `version-lock.md` §5.
- Bootstrap installed Frappe 15.123.1 / CRM 1.87.0 at the locked SHAs and migrated the new site. Bootstrap ran again successfully; migration also passed after seeding.
- Seed created fake users, two sales groups, 15 Leads, 3 Call Logs, 2 Tasks, one private attachment, and the demo reassignment. Re-running seed created no duplicates.
- Authenticated `/crm` returned HTTP 200. Login API returned `Logged In`; `frappe.auth.get_logged_user` returned `Administrator`.
- Windows host DNS does not resolve `dev.localhost`; the runtime HTTP probe used `127.0.0.1` with `Host: dev.localhost`. This shell is not elevated, so it did not edit the Windows hosts file.
- MariaDB emitted a compatibility warning: version 11.8 is newer than 10.8 and is not yet tested with this Frappe Framework version. Site install/migrate succeeded, but this remains open for ADR-001.

See [`wp01-runtime.md`](./wp01-runtime.md) for the concise run record and remaining P0 scope. This proves WP-01 runtime on this host; it does not prove all G0 gates or close P0.
