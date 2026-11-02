# T1A — CSKH P1 UI (5 màn hình P1 §9)

## Bối cảnh

PR #4 (backend P1 của T1B) **chưa merge**. PR này mang **toàn bộ** P1: backend
đã sửa (nhánh `codex/p1-foundation`, merge vào đây) **và** giao diện P1 (nhánh
`codex/p1-ui`).

## Khoảng cách với PR #4

* `codex/p1-foundation` đã có: sửa L-01 … L-15, suite `test_p1_regressions`
  (71 method), race harness đa tiến trình, workflow CI P1, `api-contract-p1.md`,
  `P1-Exit-Report.md` (chương 0–6).
* PR này thêm: **5 màn hình P1 §9**, lớp lỗi ổn định trong transport, bản dịch
  P1 đầy đủ, bằng chứng HTTP + screenshot, và chương T1A của báo cáo đóng.

## Giao diện (P1 §9)

| Màn | Đường dẫn |
|---|---|
| Trang 1 — Kho khách hàng | `/cskh/pool` |
| Trang 2 — Nhập danh sách | `/cskh/import` |
| Trang 3 — Chiến dịch và quy tắc | `/cskh/campaigns` |
| Trang 4 — Khách được giao | `/cskh/my-customers` |
| Trang 5 — Bảng điều khiển phân phối | `/cskh/dashboard` |

## Bằng chứng

* Screenshot: `deploy/p1/evidence/screenshots/01-pool.png` … `05-dashboard.png`
* Probe HTTP end-to-end: `deploy/p1/probe_flows.py`
* Runner suite: `deploy/p1/run_suites.py`
* Báo cáo: `docs/architecture/P1-Exit-Report.md` §7

## Kết quả đo trên nhánh này

* 6/6 trang HTTP 200, không banner lỗi.
* Import: upload private → `DRY_RUN` → `QUEUED` → `COMPLETED_WITH_ERRORS`,
  tổng khớp preview.
* Phân phối: campaign + 3 rule 50/30/20 → preview `315 / 188 / 125` → execute
  `300` lead, phân bổ `100 / 100 / 100`.
* Suite P1 **174/174 OK**, P0 core inventory **99/99 OK**.
* `ruff check` + `ruff format --check` (0.14.0) pass.

## CI (mã nguồn bàn giao `ec5b4f7`)

| Workflow | Run | Kết quả |
|---|---|---|
| `P0 DEV verification` | [41461874235](https://github.com/nobita6986/new-crm/actions/runs/41461874235) | **SUCCESS** — probe HTTP trực tiếp 18/18 PASS |
| `P1 backend verification` | [41461874336](https://github.com/nobita6986/new-crm/actions/runs/41461874336) | **SUCCESS** — 174 method P1 + lint/format + schema |

`ec5b4f7` sửa lỗi khiến `P0 DEV verification` fail: trang `/cskh` chỉ xuất
`frappe.csrf_token = "…"` nên probe trực tiếp không đọc được token ⇒ `csrf-page`,
`sec14-post-ok` và 2 case `sec16-*` fail. Nay trang xuất **header CSRF của session**
dưới dạng object JSON (`window.cskhCsrfHeader`), `window.frappe.csrf_token` đọc
lại từ đó — một nguồn sự thật duy nhất cho cả trình duyệt lẫn probe.

## Điều chỉnh so với backend của T1B

Hai chỗ, đều giữ đúng ý P1 §8.1 và §9.1 và **không** phá test hồi quy:

1. `_scope` cho `CSKH CRM Admin` = toàn hệ thống (P1 §8.1). Không có nhánh này,
   Admin rơi vào `self` và dashboard trả 0 ở mọi card.
2. `pool_list` thêm `unique_customers` / `work_items` và trạng thái phân phối
   **suy ra** cho từng lead (không ghi vào `CRM Lead.status`), giữ nguyên `total`
   của hợp đồng và bộ lọc scope của T1B.

## Còn nợ (không coi là đã đạt)

1. Chưa có bước CI kiểm tra screenshot / smoke test 5 màn.
2. Chưa có test tự động trên DOM của UI.
3. Chưa có màn lịch sử batch + nút tải file lỗi (P1 §9.2).
4. Chưa có nút retry job và tra cứu run ID (P1 §9.3).
5. Dashboard chưa có bộ lọc `team` / `user` (P1 §9.5).

Quyết định GO P2 thuộc T0/Owner.
