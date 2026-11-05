# Hướng Dẫn Di Chuyển Schema, Invalidation Hậu Commit & Backfill Dữ Liệu Lịch Sử (`docs/p3/migration-and-backfill.md`)

Tài liệu này quy định quy trình nâng cấp schema P3 không gián đoạn (`bench migrate × 2`), cơ chế đánh dấu `CSKH Dirty Scope` hậu commit, cơ chế tái phát hành kỳ quá khứ (`restated`), và quy trình chạy `run_kpi_backfill` an toàn có điểm dừng (`checkpoint`).

---

## 1. Nâng Cấp Schema Lặp Lại An Toàn (`Idempotent Migration`)

Patch `crm_cskh.patches.v3_0.p3_schema_and_seeds.execute` (chạy tự động qua `after_install` và `after_migrate`) đảm bảo an toàn tuyệt đối khi chạy `bench --site <site> migrate` hai lần liên tiếp (`bench migrate × 2`):
1. Tạo các chỉ mục tổ hợp (`idx_cskh_tmh_user_window`, `idx_cskh_sla_status_due`, `idx_cskh_attempt_occ_rec`, `idx_cskh_dirty_status_date`, `idx_cskh_kpirun_period_pub`, `idx_cskh_kpisnap_lookup`, `idx_cskh_kpisnap_rank`, `idx_cskh_qa_staff_date`...) bằng kiểm tra `SHOW INDEX` trước khi `ALTER TABLE`.
2. Khởi tạo danh sách ngày nghỉ `VN-HOLIDAYS-DEV` (trên `CRM Holiday List` upstream) và lịch hành chính `VN-OFFICE-DEFAULT` (trên `CSKH Working Calendar`) chỉ khi chưa tồn tại.
3. Gán các tham số mặc định P3 (**`DEV / configurable`**, `qa_enabled = 0`, `min_attempts_enabled = 0`) lên các bản ghi `CSKH Policy` hiện có mà không ghi đè cấu hình đã tinh chỉnh.
4. Điền `attribution_status = "historical_attribution_unknown"` cho các bản ghi `CSKH Assignment History` cũ trước P3 chưa có bằng chứng lịch sử nhánh team, và khởi tạo mốc `CSKH Team Membership History` hiện tại cho các nhân sự đang nằm trên cây `CRM Sales Hierarchy` mà không suy đoán ngược về quá khứ.

---

## 2. Cơ Chế Invalidation Hậu Commit (`Post-Commit Dirty Scope`)

Để bảo đảm các API tác nghiệp nóng (`record_attempt`, `close_work_item`, `reopen_work_item`, `request_correction`, `review_correction`, `ownership.assign/reassign/revoke`, `sla.waive/pause/resume`) không bao giờ bị chậm hoặc khóa chéo bởi truy vấn báo cáo:
1. Trong lúc giao dịch nghiệp vụ đang chạy: `schedule_post_commit_dirty_invalidation(...)` chỉ ghi nhận sự kiện vào bộ đệm bộ nhớ `frappe.local.cskh_pending_dirty_events` (`0` câu lệnh SQL báo cáo chạy trong transaction tác nghiệp).
2. Nếu giao dịch hoặc `SAVEPOINT` bị `ROLLBACK`: bộ đệm tự động bị xóa sạch (`rollback_savepoint_safely` & `frappe.db.after_rollback`), không sinh bản ghi bẩn giả.
3. Ngay sau khi `COMMIT` thành công: hook `frappe.db.after_commit` gộp các sự kiện trùng khóa `scope_key` và ghi vào bảng `CSKH Dirty Scope` (`status = "pending"`), đồng thời đẩy job nền `crm_cskh.jobs.recompute_kpi.process_dirty_scopes` (có chống trùng qua Redis key `cskh:kpi:dirty_worker_enqueued`).

---

## 3. Quy Tắc Phiên Bản Bất Biến & Tái Phát Hành Kỳ Cũ (`Append-Only & Restated Runs`)

Khi một hiệu chỉnh (`CSKH Attempt Correction` được duyệt) hoặc miễn trừ SLA (`waive_obligation`) làm thay đổi dữ liệu của một ngày/tháng quá khứ đã từng phát hành snapshot:
1. Bản ghi `CSKH Dirty Scope` được đánh dấu `is_prior_period = 1` kèm `trigger_reference` trỏ tới mã phiếu hiệu chỉnh/miễn trừ.
2. Tiến trình `execute_kpi_recompute` phát hiện kỳ đã có bản ghi `is_latest_published = 1` trước đó và tự động tạo `CSKH KPI Run` mới với:
   - `run_mode = "restated"`
   - `is_restated = 1`
   - `restated_from_run = <run_id_cũ>`
3. Sau khi vượt qua cổng đối soát `verify_snapshot_reconciliation`, con trỏ `is_latest_published` được hoán đổi nguyên tử từ run/snapshot cũ sang run/snapshot mới trong cùng một giao dịch DB. Toàn bộ dữ liệu của `run_id_cũ` vẫn được giữ nguyên (append-only) để phục vụ kiểm toán trước–sau.

---

## 4. Quy Trình Backfill Dữ Liệu Lịch Sử (`run_kpi_backfill`)

### 4.1 Chạy thử kiểm tra đối soát (`dry_run = True`)
Luôn chạy `dry_run=True` trước trên khoảng ngày cần backfill để xác minh toàn bộ bất biến đối soát mà **không ghi bất kỳ dòng `CSKH KPI Snapshot` nào và không thay đổi con trỏ `is_latest_published`**:

```bash
bench --site <site> execute crm_cskh.services.kpi_runs.run_kpi_backfill \
  --kwargs '{"from_date": "2026-10-01", "to_date": "2026-10-31", "period_types": ["daily", "monthly"], "batch_days": 7, "dry_run": true}'
```

### 4.2 Chạy thật theo lô kèm Checkpoint & Resume
```bash
bench --site <site> execute crm_cskh.services.kpi_runs.run_kpi_backfill \
  --kwargs '{"from_date": "2026-10-01", "to_date": "2026-10-31", "period_types": ["daily", "monthly"], "batch_days": 7, "dry_run": false}'
```
- Sau mỗi kỳ hoàn tất trong lô `batch_days`, hệ thống lưu `checkpoint_json` (`completed_period_keys`, `last_batch_index`) trên `CSKH KPI Run`.
- Nếu tiến trình bị ngắt giữa chừng, truyền lại `resume_checkpoint` từ `checkpoint_json` gần nhất để tiếp tục chính xác từ các kỳ chưa chạy mà không phải tính lại từ đầu.
