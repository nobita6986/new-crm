# P3 Rollback & Runtime Feature-Flag Runbook (`P3-09`)

> **Phụ trách:** T1B (Backend & CI)
> **Tham chiếu:** `docs/root-plan/P3.md` §13, `docs/architecture/ADR-P3-07-read-model-and-incremental-recompute.md`

---

## 1. Nguyên tắc thiết kế Rollback không gián đoạn luồng P0/P1/P2

Toàn bộ phần mở rộng backend P3 được cô lập trong các bảng chuyên biệt (`CSKH Team Membership History`, `CSKH Working Calendar`, `CSKH SLA Obligation`, `CSKH Dirty Scope`, `CSKH KPI Run`, `CSKH KPI Snapshot`, `CSKH QA Review`, `CSKH Export Job`) và 3 cờ cấu hình trên `CSKH Policy`:

| Cờ trên `CSKH Policy` | Giá trị mặc định P3 (DEV/configurable) | Hành vi khi gạt về `0` (`false`) |
|---|---:|---|
| `p3_sla_enabled` | `1` | Ngừng tạo mới/chuyển giao/đóng `CSKH SLA Obligation` trong `ownership`, `contact_attempt`, `followup`, `correction`. Cron `evaluate_sla_obligations` trả ngay `skipped=true, reason="p3_sla_disabled"`. Luồng P1/P2 (`assign` → `record_attempt` → `schedule_followup` → `close_work_item`) tiếp tục hoạt động bình thường `100%`. |
| `p3_reporting_enabled` | `1` | Các endpoint đọc báo cáo (`get_executive_overview`, `get_team_comparison`, `get_employee_quality`, `get_campaign_quality`, `get_my_scorecard`, `get_metric_drilldown`) trả envelope an toàn `ok: true, reporting_enabled: false, status: "disabled_by_policy"` (`0` lỗi 500 trên UI). Các endpoint ghi/tác vụ nặng (`request_export`, `request_recompute`) trả `409 POLICY_CONFLICT`. Cron `drain_dirty_kpi_scopes` và `refresh_current_standard_periods` trả `skipped=true, reason="p3_reporting_disabled"`. |
| `qa_enabled` | **`0`** | Mặc định **TẮT** ngay từ đầu trong P3 (`0` bản ghi review giả, `0` cờ `verified` giả). Mọi lời gọi ghi `crm_cskh.api.qa.*` trả `409 QA_DISABLED`; `PROCESS_SCORE` tự động chuẩn hóa theo tổng trọng số hợp lệ còn lại (`provisional`, ví dụ `7350 / 85 = 86,47`). |

---

## 2. Cấp độ 1 — Tắt nóng bằng Feature Flag (Khuyến nghị đầu tiên, `< 5 giây`, không cần restart)

Sử dụng khi phát hiện bất thường trên tính toán SLA hoặc tải truy vấn báo cáo mà cần giữ nguyên hoạt động CSKH P1/P2:

```bash
bench --site <site-name> execute frappe.client.set_value --kwargs '{"doctype":"CSKH Policy","name":"DEFAULT-POLICY","fieldname":{"p3_reporting_enabled":0,"p3_sla_enabled":0,"qa_enabled":0}}'
bench --site <site-name> clear-cache
```

Hoặc chạy cho toàn bộ các `CSKH Policy` đang bật (`enabled = 1`) qua `bench mariadb`:

```sql
UPDATE `tabCSKH Policy`
SET `p3_reporting_enabled` = 0,
    `p3_sla_enabled` = 0,
    `qa_enabled` = 0
WHERE `enabled` = 1;
```

### Bật lại sau khi khắc phục

1. Chuyển `p3_sla_enabled = 1` và `p3_reporting_enabled = 1` trên `CSKH Policy`.
2. Nếu có khoảng thời gian tạm tắt báo cáo cần làm tươi lại snapshot, chạy backfill an toàn theo batch (thử `dry_run=True` trước):
   ```bash
   bench --site <site-name> execute crm_cskh.services.kpi_runs.run_kpi_backfill --kwargs '{"from_date":"2026-11-01","to_date":"2026-11-04","dry_run":true}'
   bench --site <site-name> execute crm_cskh.services.kpi_runs.run_kpi_backfill --kwargs '{"from_date":"2026-11-01","to_date":"2026-11-04","dry_run":false}'
   ```

---

## 3. Cấp độ 2 — Khôi phục từ bản sao lưu trước nâng cấp (Full Site Restore)

Áp dụng khi cần quay lui toàn bộ code về baseline `main` trước P3 (`449fbc26374c3540b07ad86219fa0f33fc84feba`).

> [!IMPORTANT]
> Luôn chụp backup đầy đủ (`--with-files`) trước khi nâng cấp site lên P3.

```bash
# 1. Trước khi nâng cấp lên P3 (bước phòng vệ bắt buộc):
bench --site <site-name> backup --with-files

# 2. Khi cần quay lui hoàn toàn về P2 baseline:
git checkout 449fbc26374c3540b07ad86219fa0f33fc84feba
bench --site <site-name> --force restore <path-to-pre-p3-database.sql.gz> \
  --with-public-files <path-to-files.tar> \
  --with-private-files <path-to-private-files.tar>
bench --site <site-name> migrate
bench --site <site-name> clear-cache
```

---

## 4. Bằng chứng kiểm thử tự động

- **Unit/Integration test:** `TestP3ReportingAndSecurity.test_feature_flag_rollback_p3_sla_and_p3_reporting_disabled` trong `apps/crm_cskh/crm_cskh/tests/test_p3_reporting_and_security.py` xác minh:
  1. Khi `p3_reporting_enabled = 0`: `get_executive_overview` (`BOD`) và `get_my_scorecard` (`Staff`) trả `reporting_enabled = False`, `status = "disabled_by_policy"`, trong khi `request_export` và `request_recompute` trả `409 POLICY_CONFLICT`.
  2. Khi `p3_sla_enabled = 0`: luồng `assign` → `record_attempt` (có callback `CRM Task`) → `record_attempt(complete_open_followup=True)` → `close_work_item` hoàn tất `closed` sạch sẽ với `0` dòng `CSKH SLA Obligation` phát sinh.
- **HTTP Transport Probe & CI Backup/Restore:** `.github/workflows/p3.yml` kiểm chứng cả `bench migrate` lặp 2 lần (`idempotent`) và quy trình `bench backup --with-files` → `restore` sang site mới → đối soát toàn vẹn `reconcile_p2_invariants()` + P3 counts.
