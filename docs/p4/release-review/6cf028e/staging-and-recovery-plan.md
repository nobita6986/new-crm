# Kế hoạch Staging, UAT và khôi phục

**Đề xuất; chưa triển khai hoặc sử dụng khóa chính thức trong tác vụ này.**

## 1. Artifact và điều kiện mở bước

Promote `ghcr.io/nobita6986/crm-cskh@sha256:6599356cb09a9fa76f284cdc5d23ac2accc91dfdefe3b4161393488d7782e37e`, revision `6cf028eff4cfd35b5c82ade40358a3bbe22f4273`; kiểm OCI config theo README. Dùng distribution digest, không dùng tag mutable, local image ID hoặc digest loopback.

Cần phê duyệt phương thức ký, chữ ký chính thức mới, SLO/profile binding và người/thời điểm được phép vận hành Staging. Production GO giữ riêng.

[staging-authorization-proposal.json](staging-authorization-proposal.json) còn signoff null; dispatch proposal ký đặt deploy=false.

## 2. Preflight và backup

- Ghi inventory Live/N-1 thực tế: image/config digest, apps/version/tree, Patch Log/schema, cấu hình, migration revision và backup state.
- Đối chiếu với N-1 CI `467830195358b2431ac772486530ab935887a518` / Frappe 15.123.1. Không giả định Live đang đúng mốc đó.
- Kiểm CPU/RAM/disk/port/headroom; tách DB, Redis, volumes, network và secrets.
- Đề xuất project `crm-cskh-worker-care-staging-6599356c`, loopback 18084/18446; đây chưa phải cổng đã kiểm trống. Kiểm xung đột trước sử dụng, ghi lại nếu chọn cổng khác.
- Ref ký vẫn phải đúng 6cf; source/recipe/lock khác snapshot là điểm dừng.

Trước nâng cấp tạo backup nhất quán DB + public/private files + site config/encryption key. Lưu UTC, manifest, byte counts, SHA-256; mã hóa và giữ quyền giải mã riêng. Kiểm đọc/restore cách ly. Production cần offsite và lưu ký thực tế theo G07; backup cùng VPS không thay offsite. Retention đã chốt 14d/8w/3m.

Clone Staging dùng dữ liệu giả lập/đã khử nhạy cảm trong phạm vi được phép. Không ghi PII/credentials lên log, CI hoặc ảnh.

## 3. Xác minh chữ ký và migration

- Pull immutable manifest/config, kiểm digest blob, OCI revision/tree labels và build metadata.
- T1C chạy release_manifest.py verify với official public key, expected image đúng digest và expected scope production_candidate.
- Đối chiếu build metadata, SBOM, strict scan/exceptions và signature/envelope; phân biệt signer chính thức với CI tạm.
- Chốt release ID/previous release identity; không sửa payload đã ký.
- Dựng stack cô lập riêng sau ủy quyền; giữ Live và UAT baseline cũ.
- Chạy one-shot migrator; exit khác 0 thì chặn traffic. Kiểm Patch Log, idempotency và đối soát INV-01..06, ownership/SLA/KPI/counts.
- Patch worker_care_workspace cập nhật workspace/default_app có giới hạn và tắt Client Script Deal. Dù schema không có DDL delta, vẫn kiểm các thay đổi cấu hình/dữ liệu này, bảo toàn admin overrides và user ngoài nhóm chăm sóc.

Smoke đúng digest: care/non-care fresh login, `/cskh`, redirects `/crm`, Lead/contact/tasks/call logs, Worker Care SQL totals, callback/timezone, DNC/SLA, role scope, logo/favicon/menu/mobile, PDF và queues/scheduler.

## 4. SLO và UAT

Chỉ đo chính thức sau approval mới cho digest/spec/profile/config, với thời điểm duyệt trước đo. Giữ tải/ngưỡng/runner đã duyệt; lưu mọi attempt, raw JSON và telemetry. Chứng thực cgroup/Gunicorn/RQ/MariaDB/headroom; T1C chấm số đo mới.

Owner chỉ định bốn người thật, tài khoản riêng:
- BOD: overview/snapshot/drilldown theo scope, không lộ PII ngoài quyền.
- CRM Admin: campaign/import/distribution và nghiệp vụ được phép.
- Team Lead: team/assignment/SLA trong phạm vi đội.
- Staff: hồ sơ phụ trách, attempt/callback/DNC, Save/call/log, không truy cập chéo.

T1A điều phối F01–F30, ghi expected/actual, lỗi và bằng chứng; thu đủ bốn chữ ký thật đúng digest. Automation không thay chữ ký UAT.

Điểm dừng: sai signer/digest/source; migration hoặc reconciliation lỗi; leak PII/quyền chéo; duplicate ownership/attempt; login/redirect lặp, mất admin overrides; queues/scheduler lỗi; lỗi nghiêm trọng chưa xử lý; thiếu đường phục hồi. Dừng mở lưu lượng, giữ log/telemetry, đánh giá forward-fix/rollback.

## 5. G06/G09 có thể tái sử dụng

[Batch 2](https://github.com/nobita6986/new-crm/actions/runs/45955303485), [job G06/G09](https://github.com/nobita6986/new-crm/actions/runs/45955303485/job/140360398992), artifact 15179146880; ZIP SHA `0dce6f1f5adfa8de3676099a3896f59275183ee9b2798f08ee6dffceea92417f` gắn đúng 6cf/659.

Đã kiểm chứng trên clone CI:
- N-1 4678301 / Frappe 15.123.1 → N / Frappe 15.126.0.
- Migrate lần 1/lần 2 exit 0, 10,473s / 9,990s; guard lỗi trả exit 1.
- Schema report: Class_A_Code_And_Runtime_Only, không added/dropped table/column/type change.
- Tier 1/2 flags giữ P1/P2 writes và dirty-scope recovery.
- Tier 3 giữ post-upgrade attempts, assignment/KPI reconciled; clear-cache và clear-website-cache trên N-1 trước khi mở runtime.
- Tier 4 restore DB/files/config và kiểm đếm delta sau backup.

[g06-g09-reuse-map.json](g06-g09-reuse-map.json) lưu phạm vi. Reuse khi digest, code/recipe/runner và giả định N-1 khớp. Nếu N-1 thực tế hoặc migration semantics khác, chỉ diễn tập delta trên clone trước cutover. Duration CI không phải RTO/RPO Production.

## 6. Rollback

- Lỗi giới hạn P3/export: feature flag phù hợp, đối soát và recovery theo runbook.
- Lỗi code/runtime trong trường hợp đã chứng minh tương thích N-1: dừng N runtime, pin N-1 digest đã kiểm chứng, configurator, clear-cache + clear-website-cache trên N-1 trước khi bật backend/frontend/websocket/queues/scheduler; kiểm hooks, quyền, fresh login và dữ liệu.
- Không suy ra rollback an toàn chỉ từ việc không có DDL. Kiểm user defaults/workspace/hooks và dữ liệu ghi sau nâng cấp.
- Dữ liệu không tương thích/mất toàn vẹn: ưu tiên forward-fix đã kiểm định. DB restore cần phê duyệt riêng, xuất delta sau backup và ghi cửa sổ mất dữ liệu/replay.
- Không git pull/bench update trên VPS để phát hành; không xóa volume/hạ DB tùy tiện.

## 7. Production

Sau Staging/UAT, T1C xác nhận các cổng trên bản phát hành tương ứng; hoàn tất offsite/key escrow và bàn giao Primary Operator. Người có thẩm quyền ký GO riêng, chốt maintenance window, backup checkpoint và người có quyền dừng.

T1A mới triển khai, smoke/post-watch và bàn giao G23. Hồ sơ hiện chưa chỉ định người thật, chưa ký UAT/SOP hoặc cho phép cutover.
