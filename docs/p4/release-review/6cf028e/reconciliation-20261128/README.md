# Đối chiếu tích hợp T1C / T0 — 28/11/2026

Hồ sơ bổ sung theo nguyên tắc append-only; giữ nguyên toàn bộ các gói đã niêm phong (`b549ff3`, `decisions/20261127-owner-authorization/`, `reconciliation-v184/`, `p4-t0-t1e-pdf-review-b9f870d-20261127/`).

## 1. Kết luận kiểm định PR #21 (`b67565d`) & tích hợp nhánh branding cache

- **PR #21 (`codex/fix-dev-pdf-css-ssrf` @ `b67565d5f55d403c981f5fcc7a628c06bc887db2`)**: Đã đóng đủ 4 điểm review độc lập `IR-B9-MARGIN-01`, `IR-B9-NATIVE-CONCURRENT-02` (`WeasyPrint 69.0` `FcConfig` double-free `SIGSEGV`, kiểm chứng `360/360` tiến trình con lạnh), `IR-B9-RETAINED-LINK-03`, `IR-B9-CUSTOM-FETCHER-04`; CI P3 (`46100241039` — 57/57 backend, 7/7 UI, 84/84 HTTP, 0 skip) và P4-02 (`46100244621` — 4/4 job) PASS. PR #21 đã chuyển trạng thái **MERGED** lúc `2026-11-28T03:53:43Z` sau khi fast-forward vào nhánh nền PR #20.
- **Nhánh branding cache (`codex/p4-branding-cache-review-fixes`)**: Đã rebase sạch lên sau `b67565d`, xử lý xung đột `Containerfile` / `release-lock.json`, khắc phục lỗi cô lập harness trên `frappe.*`, đồng bộ `favicon` + `splash_image` cho `/crm/*` qua `crm_cskh.i18n.update_website_context` theo epoch `DOMAIN_BRANDING`, và làm test `HTMLParser` tương thích đa phiên bản Python.

## 2. Trạng thái PR #20 (`c52d2cac4ee20474be2679bfd5441cc39541c772`)

- **Head mới**: `c52d2cac4ee20474be2679bfd5441cc39541c772` (`apps_tree_sha=07f25a203cb14e8b39b1f820b6c091083eb8d910`, `crm_cskh_tree_sha=2e61dd455c102d0055c12aae40285550d192bef4`), `mergeStateStatus=CLEAN`, `mergeable=MERGEABLE`.
- **Kiểm tra local**: `122/122` unit test `deploy/release` PASS, `verify_pr_isolation.py` PASS, `ruff check` & `ruff format --check` PASS, `git diff --check` PASS.
- **CI GitHub Actions trên exact head `c52d2ca` — 7/7 workflow PASS**:
  - `P0 DEV verification`: run `46107036950` (job `140862000476`) — **success**.
  - `P1 backend verification`: run `46107036983` (job `140862000291`) — **success**.
  - `P2 workflow verification`: run `46107036953` (job `140862000117`) — **success**.
  - `P3 quality & reporting verification`: run `46107036957` (job `140862000159`) — **success**.
  - `P4-02 isolated CI, SBOM, scan & digest promotion` (`pull_request`): run `46107037009` (4/4 job) — **success**.
  - `P4-02 isolated CI, SBOM, scan & digest promotion` (`push`): run `46107033118` (4/4 job) — **success**.
  - `P4 GHCR candidate digest, full P0-P3 regression & S1 benchmark` (`push`): run `46107033037` (3/3 job) — **success**; build/smoke/S1 PASS trên image nền P4 `ghcr.io/nobita6986/crm-cskh@sha256:28ecfca34f240f4adcd9e79b9edbcc9c0f7a808b23ca8bffd3b0d83d72bc0497` (`image_id=sha256:db094520bd9cc24af1e2aaf62f1a6d6c637d8835368e5f26c718e7af5707eedd`).

## 3. Trạng thái các Finding & Lưu ý PR #19 / Candidate

- **`F-WC-6CF028E-PR20-LEGACY-P2P3-CHECKS-01` / `F-P4-LEGACY-DEV-PDF-CSS-SSRF-01`**: `READY_TO_CLOSE_ON_PR20_GREEN` — toàn bộ kiểm tra P0–P4 trên PR #20 đã xanh.
- **`F-WC-6CF028E-LIVE-NMINUS1-AND-TIER3-DB-STATE-01`**: **Vẫn MỞ** (`OPEN_DOCUMENTATION_PREPARED_EXECUTION_EVIDENCE_PENDING`, `clone_migration_rollback_executed: false`, `capacity_cleared: false`). Còn chờ cấp quyền diễn tập clone trên Live N-1 (`152d057`) và xử lý headroom UAT.
- **Lưu ý PR #19 (`codex/worker-care-marketing` @ `c0f4deb`)**: Phần purge cache `Website Settings` trong `runtime_cache.py` từ `c0f4deb` đã được tích hợp vào PR #20 (`c52d2ca`). Khi PR #20 vào `main` và retarget PR #19 sang `main`, cần rebase/reconcile `Containerfile` & `release-lock.json` để không ghi đè thay đổi trên `main`.
- **Phạm vi Candidate**: Image lịch sử `6599356c...` (nguồn `6cf028e`) đã hết hiệu lực đại diện do cây nguồn thay đổi; sau khi hợp nhất `#20 → #19` cần tạo và kiểm định candidate tích hợp mới trước khi ký chính thức qua `p4-release.yml`.

## 4. Cổng chờ Owner / T0 tiếp theo

1. Đánh dấu sẵn sàng & review merge **PR #20** vào `main` (hiện vẫn giữ nguyên Draft, chưa tự merge).
2. Sau khi PR #20 vào `main`, retarget/rebase **PR #19** và kiểm chứng candidate tích hợp cuối.
3. Cấp quyền phạm vi diễn tập clone + phương án headroom UAT cho **T1A** để đóng finding N-1/Tier 3.
4. Chỉ định nhân sự cho protected signing Environment (`p4-release.yml`). Chưa deploy Staging/Production, chưa stop UAT, chưa merge PR #18.
