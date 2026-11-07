# Hồ sơ kiểm chứng độc lập trên site sạch — `test_p3_reporting_and_security` (`a973e3a`)

## 1. Mốc mã nguồn & môi trường thực kiểm
- **Base tích hợp:** `6912c78c1c74483c1a0063379d94d8c9b8bdb30b`
- **Commit test riêng (`testedCodeSha`):** `a973e3a69530edb9441480c3b2862b381656b36b` (nhánh `codex/fix-p3-reporting-test-isolation`)
- **Tree `apps/crm_cskh` thực kiểm:** `0248d8954b9a9314e907151f911373d48546e8b6`
- **App image:** `crm-cskh:0.0.1-92e14af` (`sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9`)
- **DB image:** `mariadb@sha256:f16d7a344413e519dff518f27bdb221388f84b0d274c97c4fb5f2cb0dc1b34fa`
- **Redis image:** `redis@sha256:ac2da09bc822f325a9f68f533120b86fa39c1b8db46b3aa7c1bbea7fdfd4fcba`
- **Phạm vi thay đổi:** Chỉ 2 tệp kiểm thử (`apps/crm_cskh/crm_cskh/tests/p2_base.py`, `apps/crm_cskh/crm_cskh/tests/test_p3_reporting_and_security.py`). Giữ nguyên 100% logic ứng dụng, điều kiện auto-verify `SEC2-02` (`call_log_adapter.py`), công thức KPI (`kpi_calculator.py`) và toàn bộ assertion bảo mật `S01..S18`.

## 2. Nguyên nhân & nội dung sửa trong test
1. **Fixture tổng đài giả lập đáp ứng `SEC2-02`:** Mở rộng `P2TestCase.make_native_call_log(..., telephony_medium=None)` (tương thích ngược hoàn toàn khi không truyền tham số). Trong `_seed_two_team_activity()`, cuộc gọi `cl_a` của `staff_a` tạo `CRM Call Log` tổng đài giả lập (`telephony_medium="Twilio"`, `status="Completed"`, `duration=60 > 0`), được `SEC2-02` tự động phân loại `verification_status == "verified"`.
2. **Kiểm tra `Manual` giữ phân loại `self_reported`:** Trong `_seed_two_team_activity()` và `test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10`, xác minh cả cuộc gọi truyền tường minh `telephony_medium="Manual"` (`status="Completed"`, `duration=45`) lẫn mặc định `CRM Call Log` (`Manual`, `status="Completed"`, `duration=30`) đều có `verification_status == "self_reported"`, đồng thời `overview["attempt_breakdown"]` đạt đúng `verified == 1`, `self_reported == 2`.
3. **Cô lập hoàn toàn dữ liệu runtime giữa các module/method:** `TestP3ReportingAndSecurity.setUp()` dọn sạch các bảng runtime CSKH (`CSKH Contact Attempt`, `CSKH Attempt Correction`, `CSKH SLA Obligation`, `CSKH Active Claim`, `CSKH Assignment History`, `CSKH Work Item` và `CRM Task` follow-up CSKH) bên cạnh các bảng KPI/Export/QA, loại bỏ hoàn toàn sự phụ thuộc vào dữ liệu rơi rớt từ `test_p3_kpi_and_snapshots`.

## 3. Lệnh tái hiện độc lập trên site sạch
Chạy từ thư mục gốc repository ở commit `a973e3a69530edb9441480c3b2862b381656b36b`:

```bash
COMPOSE_FILE=deploy/dev/evidence/p3-reporting-test-isolation-20261107/scripts/docker-compose.p3-reptest.yml
docker compose -f "$COMPOSE_FILE" -p cskh-p3-reptest-clean up -d --wait

# 1. Khởi tạo site sạch (không có bất kỳ khóa p3_*_enabled nào trong site_config.json)
docker compose -f "$COMPOSE_FILE" -p cskh-p3-reptest-clean exec -T web bash -lc \
  "tr -d '\r' < /verify-harness/bootstrap_clean_site.sh | SITE_NAME=p3-reptest-clean.localhost bash"

# 2. Chạy riêng toàn bộ module với JUnit XML trên site sạch (7/7 method PASS)
docker compose -f "$COMPOSE_FILE" -p cskh-p3-reptest-clean exec -T web bash -lc \
  "tr -d '\r' < /verify-harness/run_frappe_junit.py > /tmp/run_frappe_junit.py && \
   /home/frappe/frappe-bench/env/bin/python /tmp/run_frappe_junit.py \
   p3-reptest-clean.localhost crm_cskh.tests.test_p3_reporting_and_security \
   /tmp/test_p3_reporting_and_security.junit.xml"

# 3. Hoặc chạy bằng CLI chuẩn của Frappe trên site sạch
docker compose -f "$COMPOSE_FILE" -p cskh-p3-reptest-clean exec -T web bash -lc \
  "cd /home/frappe/frappe-bench && bench --site p3-reptest-clean.localhost run-tests \
   --app crm_cskh --module crm_cskh.tests.test_p3_reporting_and_security"

# 4. Dọn stack kiểm chứng
docker compose -f "$COMPOSE_FILE" -p cskh-p3-reptest-clean down -v --remove-orphans
```

## 4. Tóm tắt kết quả thực kiểm
| Lần chạy | Site | Lệnh / Đối tượng | Kết quả | Bằng chứng |
|---|---|---|---|---|
| Trạng thái trước test | `p3-reptest-clean.localhost` | Kiểm tra `site_config.json` & đếm bảng runtime | `0` hàng sẵn có, `has_any_p3_flag_override=false` | `logs/01-pre-test-clean-site-state.json` |
| Chạy riêng module (JUnit) | `p3-reptest-clean.localhost` (site sạch mới) | `crm_cskh.tests.test_p3_reporting_and_security` | **7/7 PASS** (`0` fail, `0` error, `0` skip) | `junit-standalone/test_p3_reporting_and_security.junit.xml`, `logs/02-standalone-clean-site-test_p3_reporting_and_security.log` |
| Đối chiếu `ci_junit_gate.py` | — | Kiểm kê 7 method trong `p3_test_inventory.json` | **PASS (`7/7`)** | `junit-standalone/standalone_per_method_gate.json`, `logs/02b-standalone-ci-junit-gate.log` |
| Chạy riêng bằng `bench run-tests` | `p3-reptest-cli.localhost` (site sạch mới #2) | `--module crm_cskh.tests.test_p3_reporting_and_security` | **Ran 7 tests -> OK** | `logs/03b-virgin-site-bench-run-tests-module.log` |
| Chạy đơn lẻ ca BOD `S08/S09/S10` | `p3-reptest-cli.localhost` | `--test test_bod_viewer_aggregate_access_and_zero_pii_s08_s09_s10` | **Ran 1 test -> OK** | `logs/04-bench-run-tests-single-method-bod.log` |
| Hồi quy liên module P2 + P3 | `p3-reptest-clean.localhost` | `test_p2_calllog_followup` (4) + `test_p3_kpi_and_snapshots` (8) + `test_p3_reporting_and_security` (7) | **19/19 PASS** (`ci_junit_gate.py` PASS) | `junit-regression/*.junit.xml`, `logs/05-regression-p2-and-p3-kpi-then-reporting.log` |
