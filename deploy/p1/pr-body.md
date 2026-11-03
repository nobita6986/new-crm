# T1A — CSKH P1 UI hoàn chỉnh (5 màn hình P1 §9, Việt hóa 100% & E2E thực tế)

## Bối cảnh & nguồn gốc tích hợp

* **Nguồn yêu cầu:** `docs/root-plan/P1.md` §9 (UI/UX), §10 (Contract), §12 (Nghiệm thu).
* **Đồng bộ backend có kiểm soát:** Nhánh `codex/p1-ui` tích hợp trực tiếp từ Git cả baseline `3025495f58bc0391c3f8d092af500b0566a5f038` lẫn commit mới nhất đã qua CI của PR #4 (`codex/p1-foundation`): `64b7486f29ad712106d167dfaea50e3b060a6845` (sau khi kiểm tra `api-contract-p1.md` §6.1: **0 breaking change**).
* **Không nới quyền backend:** `0` dòng thay đổi trên `permissions.py` và `p1_permissions.py`; giữ nguyên toàn bộ chương 0–6 của `docs/architecture/P1-Exit-Report.md` và hợp nhất có chủ đích `translations/vi.csv` (**577 dòng**, `0` dòng lỗi).

## Năm màn hình hoàn thiện (P1 §9)

| Màn | Đường dẫn | Nội dung hoàn thiện chính |
|---|---|---|
| Trang 1 — Kho khách hàng | `/cskh/pool` | Trạng thái suy ra (`Chưa phân phối`, `Trong hàng đợi`, `Đã giao`, `Xung đột`, `Đã thu hồi`, `Đã đóng`, `Lỗi dữ liệu`) không chạm `CRM Lead.status`; tách `Số khách hàng duy nhất` vs `Số công việc`; bộ lọc chiến dịch/trạng thái/nguồn/lô/tìm kiếm (`0...` & `+84...`); ẩn hoàn toàn PII và chặn `search` (`403`) với BOD |
| Trang 2 — Nhập danh sách | `/cskh/import` | Upload riêng tư CSV/XLSX → ánh xạ 3 cột (`số điện thoại`, `họ tên`, `nguồn`) → preview tiếng Việt → confirm → tiến độ Bước 3 trực tiếp (`Số dòng tạo mới / dùng lại / bỏ qua / lỗi`), bảng `Lịch sử nhập`, `Chạy lại công việc thất bại`, và `download_error_file` chỉ cho phép `CSKH CRM Admin` (`200`), từ chối mọi role khác (`403`) |
| Trang 3 — Chiến dịch & quy tắc | `/cskh/campaigns` | Tạo/Sửa/Đóng chiến dịch, tính trực tiếp `%` chuẩn hóa trọng số, `capacity = 0` hiển thị **`Không giới hạn`**, preview đủ 5 cột, chống bấm đúp, kiểm tra stale preview (`RULE_CHANGED`, `PREVIEW_EXPIRED`), tra cứu `Mã đợt chạy` và chạy lại / resume đợt chạy `failed` & `completed_with_errors` |
| Trang 4 — Khách được giao | `/cskh/my-customers` | Lọc chiến dịch, tìm kiếm tên/SĐT, sắp xếp thời gian giao, ngăn `Chi tiết quyền phụ trách` + `Lịch sử phụ trách` chỉ ghi thêm; `CSKH CRM Admin` chuyển giao / thu hồi kèm lý do bắt buộc & `expected_owner`; `Sales Manager` chỉ xem phạm vi đội |
| Trang 5 — Bảng điều khiển phân phối | `/cskh/dashboard` | Bộ lọc `Chiến dịch`, `Từ ngày` / `Đến ngày`, `Đội`, `Người được giao`, `Mã đợt chạy`; phân biệt rõ **`Quyền phụ trách hiện tại (công việc đang hoạt động)`** và **`Sự kiện phân công (nhật ký kiểm toán)`** + bảng lịch sử `Đợt phân phối` |

## Kết quả kiểm chứng thực tế trên nhánh `codex/p1-ui`

* **Kiểm toán tĩnh:**
  * `ruff==0.14.0 check` + `format --check`: **PASS** (`101` tệp Python chuẩn format).
  * `p1_test_inventory.py --check`: **PASS** (`184` method P1 trong `8` module; P0 core giữ nguyên `99` method).
  * `deploy/p1/check_translations_audit.py`: **PASS** (`used=204`, `declared=372`, `vi.csv=577 rows`, `missing=0`, `untranslated=0`, `orphan=0`).
* **Suite đơn vị & tích hợp (`deploy/p1/run_suites.py`):** **15/15 module PASS (`297/297` method)** — P1 `184/184` + UI `test_ui_p1_endpoints` `6/6` + P0 core `99/99` + transport `8/8`.
* **HTTP E2E đa vai trò thật (`deploy/p1/verify_p1_http_e2e.py` → `deploy/p1/evidence/http-e2e-report.json`):** **6/6 nhóm E2E PASS** trên 8 phiên thật (`admin`, `bod`, `leader_a`, `leader_b`, `staff_a`, `staff_b`, `outside`, `guest`).
* **Trình duyệt Chromium thật (`deploy/p1/verify_p1_browser_e2e.mjs` → `deploy/p1/evidence/browser-manifest.json`):** **11/11 lượt kiểm tra & chụp màn hình PASS** (`console_errors=0`, `page_errors=0`, `asset_failures=0`, `xss_triggered=false`, `unmasked_phones=0`, `<html lang="vi">`).

## Cách dựng & tái hiện cho T1C

```bash
git fetch origin codex/p1-foundation codex/p1-ui
git checkout codex/p1-ui
git merge-base --is-ancestor 3025495f58bc0391c3f8d092af500b0566a5f038 HEAD
git merge-base --is-ancestor 64b7486f29ad712106d167dfaea50e3b060a6845 HEAD

cp -n deploy/p1/.env.example deploy/p1/.env
docker compose -f deploy/p1/docker-compose.yml --env-file deploy/p1/.env -p crm-cskh-p1 up -d
docker exec crm-cskh-p1-bench-1 bash -lc "cd /workspace/frappe-bench && bench --site p1.localhost migrate && bench --site p1.localhost clear-cache && bench --site p1.localhost clear-website-cache"

python -X utf8 deploy/p1/check_translations_audit.py
python -X utf8 deploy/p1/run_suites.py
python -X utf8 deploy/p1/verify_p1_http_e2e.py
node deploy/p1/verify_p1_browser_e2e.mjs
```

## Giới hạn còn mở ngoài phạm vi UI hiện tại

1. Script kiểm thử trình duyệt Chromium (`deploy/p1/verify_p1_browser_e2e.mjs`) chạy tự động trên stack cô lập `crm-cskh-p1`, chưa gắn thêm thành job riêng trong `.github/workflows/p1.yml` để giữ nguyên workflow CI backend của PR #4.

Quyết định merge và ký GO P2 thuộc T0/Owner.
