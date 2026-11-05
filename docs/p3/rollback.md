# P3 Rollback & Runtime Feature-Flag Runbook (`P3-09`, `F-P3-08`)

> **Phụ trách:** T1B (Backend & CI)
> **Tham chiếu:** `docs/root-plan/P3.md` §13, `apps/crm_cskh/crm_cskh/services/p3_flags.py`

---

## 1. Kiến trúc Nguồn Sự Thật Cờ Cấu Hình Hợp Nhất (`services/p3_flags.py`)

Để loại bỏ hoàn toàn hiện tượng "nửa bật nửa tắt" giữa UI (`www/cskh_context.py`) và Backend/Worker (`F-P3-08`), mọi tầng của hệ thống (context giao diện `/cskh/*`, API báo cáo, SLA engine, QA service, Export service, Cron scheduler và các RQ job đã nằm sẵn trong hàng đợi) đều đọc qua **một bộ giải quyết duy nhất** `crm_cskh.services.p3_flags.resolve_p3_flags()`:

### Thứ tự ưu tiên (`Precedence`)
1. **Site-level Emergency Kill Switch (`site_config.json` / `frappe.conf`):**
   - Các khóa được nhận diện:
     - Báo cáo/KPI/Export: `cskh_p3_reporting_enabled` *(alias tương thích: `cskh_p3_quality_enabled`)*
     - Nghĩa vụ SLA: `cskh_p3_sla_enabled`
     - Kiểm định QA: `cskh_p3_qa_enabled` *(alias tương thích: `cskh_qa_enabled`)*
   - Khi khóa cấp site được đặt tường minh bằng `false` / `0`, tính năng tương ứng **TẮT NGAY LẬP TỨC trên toàn bộ UI + API + RQ Worker** bất kể cấu hình trong DB `CSKH Policy` đang là `1`.
2. **Database Policy (`CSKH Policy` hiệu lực hiện hành):**
   - Nếu `site_config.json` không ghi đè khóa tương ứng (`None`), hệ thống đọc từ `CSKH Policy` đang hiệu lực (`p3_reporting_enabled`, `p3_sla_enabled`, `qa_enabled`).

| Nhóm tính năng | Khóa Kill Switch cấp Site | Trường trên `CSKH Policy` | Hành vi nhất quán khi TẮT (`false` / `0`) |
|---|---|---|---|
| **Báo cáo KPI & Export** | `cskh_p3_reporting_enabled` | `p3_reporting_enabled` (mặc định `1`) | UI ẩn điều hướng báo cáo P3; API đọc trả envelope an toàn `reporting_enabled: false, status: "disabled_by_policy"` (`0` lỗi 500); `request_export` & `request_recompute` trả `409 POLICY_CONFLICT`; job RQ `run_single_recompute_job` / `run_export_job` đang xếp hàng tự dừng an toàn (`skipped=true` / `aborted_kill_switch`). |
| **Đồng hồ & Nghĩa vụ SLA** | `cskh_p3_sla_enabled` | `p3_sla_enabled` (mặc định `1`) | UI ẩn mục SLA P3; các hook P1/P2 ngừng tạo/chuyển/đóng `CSKH SLA Obligation`; cron `evaluate_sla_obligations` trả `skipped=true, reason="p3_sla_disabled"`; luồng tác nghiệp P1/P2 hoạt động bình thường `100%`. |
| **Kiểm định mẫu QA** | `cskh_p3_qa_enabled` | `qa_enabled` (mặc định **`0`**) | UI ẩn thao tác chấm QA; mọi API ghi `crm_cskh.api.qa.*` trả `409 QA_DISABLED`; `PROCESS_SCORE` tự động chuẩn hóa trên tổng trọng số hợp lệ còn lại (`provisional`). |

> **Bảo toàn dữ liệu kiểm toán & bù dữ liệu (`Catch-up Preservation`):** Trong suốt thời gian bật kill switch tạm thời, hệ thống vẫn bảo toàn nguyên vẹn `CSKH Team Membership History`, `CSKH Assignment History`, `CSKH Contact Attempt` và `CSKH Dirty Scope` để khi mở lại cờ, tiến trình recompute/backfill bù chính xác `100%` số liệu mà không mất dấu vết chuyển đội hay sửa lỗi.

---

## 2. Cấp độ 1A — Tắt Khẩn Cấp Cấp Site (`site_config.json`, tức thời cho cả UI + API + RQ Worker)

```bash
bench --site <site-name> set-config cskh_p3_reporting_enabled false
bench --site <site-name> set-config cskh_p3_sla_enabled false
bench --site <site-name> set-config cskh_p3_qa_enabled false
bench --site <site-name> clear-cache
```

### Mở lại sau khi khắc phục
```bash
bench --site <site-name> set-config cskh_p3_reporting_enabled true
bench --site <site-name> set-config cskh_p3_sla_enabled true
bench --site <site-name> clear-cache
bench --site <site-name> execute crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes
```

---

## 3. Cấp độ 1B — Tắt Mềm Qua `CSKH Policy` (Khi không đặt override trong `site_config.json`)

```bash
bench --site <site-name> execute frappe.client.set_value --kwargs '{"doctype":"CSKH Policy","name":"GLOBAL-DEFAULT","fieldname":{"p3_reporting_enabled":0,"p3_sla_enabled":0,"qa_enabled":0}}'
bench --site <site-name> clear-cache
```

---

## 4. Cấp độ 2 — Khôi Phục Toàn Bộ Từ Bản Sao Lưu Trước Nâng Cấp (Full Site Restore)

```bash
# 1. Trước khi nâng cấp lên P3 (bắt buộc):
bench --site <site-name> backup --with-files

# 2. Khi cần quay lui hoàn toàn về P2 baseline:
git checkout 449fbc26374c3540b07ad86219fa0f33fc84feba
bench --site <site-name> --force restore <path-to-pre-p3-database.sql.gz> \
  --with-public-files <path-to-files.tar> \
  --with-private-files <path-to-private-files.tar>
bench --site <site-name> migrate
bench --site <site-name> clear-cache
```
