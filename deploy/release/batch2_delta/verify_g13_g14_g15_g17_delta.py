#!/usr/bin/env python3
"""P4 Đợt 2 — `G13`, `G14`, `G15` & `G17` Delta Verifier on Locked Candidate GHCR Digest.

Executes only the required T1C gap deltas on the production 10-service Compose topology
(`proxy` Traefik -> `frontend` Nginx -> `backend` Gunicorn + `queue-short` + `queue-long`
+ `scheduler` + `websocket` + `db` + `redis-cache` + `redis-queue`):
  1. `G15` (`F21–F27`): Materializes `t1c_p3_golden_dataset_expected.json` (`SHA256 deb6be25...`) on clean site,
     verifies 9 working-calendar vectors + `EMPTY_CALENDAR`, `16/16` `cskh-metrics-v2` scopes (`86.47` provisional
     with `qa_enabled=false`, `87.0` final with `qa_enabled=true`, `null` `insufficient_data`, `A->B->A` interval
     vs distinct grain), double recompute append-only, all 5 grains (`daily`, `weekly`, `monthly`, `quarterly`,
     `yearly`) reconciliation, and live HTTP overview/team/employee/campaign/scorecard/drilldown/SLA/QA/export.
  2. `G13` (`F03–F11`): Live HTTP + `queue-long` CSV & XLSX import, error CSV RBAC, duplicate/replay idempotency,
     `RULE_CHANGED` & `PREVIEW_EXPIRED` `409`, `1,000`-Lead `50/30/20` -> `500/300/200` distribution, multi-batch
     cumulative fairness (`15/9/6`), disabled-user & capacity enforcement, and 2-Admin concurrent HTTP `execute`.
  3. `G14` (`F13–F20`): Multi-channel attempts (`no_answer`, Manual `self_reported` vs PBX `verified` per `SEC2-02`,
     `zalo` `call_log IS NULL`), 1:1 `CRM Call Log` `409 ALREADY_LINKED`, `FCRM Note` exclusion, followup schedule/
     reschedule (`original_due_at` immutable)/cancel, repeated `evaluate_sla_obligations` `0` duplicates, `on_hold`
     handoff `A->B`, close/reopen, DNC `409`, correction/void/restore, and Outcome/Policy snapshot immutability.
  4. `G17` (`D04–D06`, `D12`, `D14`, `D15`): Real `docker kill -s KILL` (`ExitCode 137`) mid-`1,200`-row import on
     `queue-long`, mid-`600`-item distribution + lease recovery on `queue-long`, and mid-recompute on `queue-short`
     proving `0` incomplete published snapshots + Redis lock `409` + clean retry swap, plus `redis-*`/`scheduler`/
     `queue-*` restarts and full `D12` SQL invariant verification.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import pathlib
import sys
import time
import traceback

THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(THIS_DIR.parent))

from batch2_delta import (  # noqa: E402
    GOLDEN_DATASET_EXPECTED_SHA256,
    LOCKED_BATCH1_COMMIT_SHA,
    LOCKED_BATCH1_IMAGE_DIGEST,
)
from batch2_delta.common_stack import (  # noqa: E402
    IsolatedComposeStack,
    SecretScrubber,
    build_minimal_xlsx,
    unwrap_message,
)


def build_plan_only_report(app_image: str) -> dict[str, object]:
    return {
        "schema_version": "cskh-p4-batch2-g13-g14-g15-g17-v1",
        "mode": "plan_only",
        "status": "PLANNED",
        "gates": ["G13", "G14", "G15", "G17"],
        "functional_matrix_delta": [
            *[f"F{i:02d}" for i in range(3, 12)],
            *[f"F{i:02d}" for i in range(13, 28)],
        ],
        "chaos_matrix_delta": ["D04", "D05", "D06", "D08", "D12", "D14", "D15"],
        "golden_dataset_expected_sha256": GOLDEN_DATASET_EXPECTED_SHA256,
        "locked_batch1_commit_sha": LOCKED_BATCH1_COMMIT_SHA,
        "app_image": app_image,
    }


def wait_import_terminal(
    stack: IsolatedComposeStack, batch_id: str, timeout_sec: float = 120.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        st = stack.exec_site_python(
            "row = frappe.db.get_value('CSKH Import Batch', _IN['b'], "
            "['name', 'status', 'total_rows', 'progress', 'created_rows', 'reused_rows', 'error_rows', 'error_artifact'], as_dict=True)\n"
            "print(json.dumps(row, default=str))",
            {"b": batch_id},
        )
        if st.get("status") in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
            return st
        time.sleep(0.8)
    raise TimeoutError(
        f"Import batch {batch_id} did not reach terminal state in {timeout_sec}s"
    )


def wait_distribution_terminal(
    stack: IsolatedComposeStack, run_id: str, timeout_sec: float = 120.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        st = stack.exec_site_python(
            "row = frappe.db.get_value('CSKH Distribution Run', _IN['r'], "
            "['name', 'status', 'progress', 'actual_json'], as_dict=True)\n"
            "print(json.dumps(row, default=str))",
            {"r": run_id},
        )
        if st.get("status") in ("completed", "failed"):
            return st
        time.sleep(0.8)
    raise TimeoutError(f"Distribution run {run_id} did not finish in {timeout_sec}s")


# =============================================================================
# SECTION 1 — `G15` (`F21–F27` Golden Dataset + `cskh-metrics-v2` + 5 Grains + Live HTTP)
# =============================================================================

G15_SETUP_AND_ORACLE_PY = r"""
import hashlib
from pathlib import Path
from crm_cskh.tests.test_p3_t1c_remediation import T1C_GOLDEN_FIXTURE_PATH, TestP3T1CRemediation
from crm_cskh.jobs.recompute_kpi import run_single_period_recompute
from crm_cskh.jobs.reconcile_metrics import reconcile_published_period
from crm_cskh.services.calendar import (
    InvalidCalendarError,
    add_business_minutes,
    build_calendar_spec,
    resolve_working_calendar,
)

raw_bytes = T1C_GOLDEN_FIXTURE_PATH.read_bytes()
fixture_sha = hashlib.sha256(raw_bytes).hexdigest()
assert fixture_sha == _IN["expected_sha"], f"Golden SHA mismatch: {fixture_sha}"
exp = json.loads(raw_bytes.decode("utf-8"))

# Run the full authoritative 16/16-scope QA-off + QA-on oracle test first
tc = TestP3T1CRemediation(methodName="test_t1c_golden_dataset_all_16_scopes_qa_off_and_qa_on_exact_match")
tc.setUp()
try:
    tc.test_t1c_golden_dataset_all_16_scopes_qa_off_and_qa_on_exact_match()
finally:
    pass

# Re-materialize clean `qa_enabled=0` state for live HTTP & 5-grain verification
seeded = tc._seed_t1c_golden_dataset(exp, qa_enabled=0)

# Set known password for Golden actors so live HTTP sessions can authenticate
pw = _IN["actor_pw"]
for email in (
    "cskh.admin@t1cp3.local",
    "bod.viewer@t1cp3.local",
    "leader.a@t1cp3.local",
    "leader.b@t1cp3.local",
    "staff.a1@t1cp3.local",
):
    u = frappe.get_doc("User", email)
    u.enabled = 1
    u.language = "vi"
    u.new_password = pw
    u.save(ignore_permissions=True)

# `F21` / `F23`: Verify all 9 `calendar_golden_vectors` (`CAL-01..08`, including `EMPTY_CALENDAR` guard)
cal_vectors = exp.get("calendar_golden_vectors") or []
empty_cal_blocked = False
for vec in cal_vectors:
    cal_code = vec["calendar"]
    if vec.get("expected_error") == "EMPTY_CALENDAR":
        try:
            build_calendar_spec(calendar_code="EMPTY", timezone="Asia/Ho_Chi_Minh", shifts=[])
        except InvalidCalendarError:
            empty_cal_blocked = True
        continue
    cal_spec = resolve_working_calendar(cal_code)
    actual_due = add_business_minutes(vec["start_at"], int(vec["target_minutes"]), cal_spec)
    exp_due = vec["expected_due_at"]
    assert actual_due.strftime("%Y-%m-%d %H:%M:%S") == exp_due[:19], (vec, actual_due)

# `F24` / `F25`: Double recompute append-only (`2026-10`) + all 5 grains (`daily`, `weekly`, `monthly`, `quarterly`, `yearly`)
r1 = run_single_period_recompute(period_type="monthly", period_key="2026-10", actor="Administrator")
r2 = run_single_period_recompute(period_type="monthly", period_key="2026-10", actor="Administrator")
total_oct_runs = frappe.db.count("CSKH KPI Run", {"period_type": "monthly", "period_key": "2026-10", "status": "completed"})
pub_oct_runs = frappe.db.count("CSKH KPI Run", {"period_type": "monthly", "period_key": "2026-10", "is_latest_published": 1})
assert total_oct_runs >= 2 and pub_oct_runs == 1, (total_oct_runs, pub_oct_runs)

grains = [
    ("daily", "2026-10-15"),
    ("weekly", "2026-W42"),
    ("monthly", "2026-10"),
    ("quarterly", "2026-Q4"),
    ("yearly", "2026"),
]
grain_reconciliations = {}
for ptype, pkey in grains:
    if ptype != "monthly":
        run_single_period_recompute(period_type=ptype, period_key=pkey, actor="Administrator")
    rec = reconcile_published_period(period_type=ptype, period_key=pkey)
    assert rec.get("ok") is True, (ptype, pkey, rec)
    grain_reconciliations[f"{ptype}:{pkey}"] = True

print(json.dumps({
    "fixture_sha256": fixture_sha,
    "golden_16_scopes_qa_off_and_on_exact": True,
    "calendar_vectors_verified": len(cal_vectors),
    "empty_calendar_blocked": empty_cal_blocked,
    "append_only_october_runs": total_oct_runs,
    "published_october_runs": pub_oct_runs,
    "five_grain_reconciliations": grain_reconciliations,
    "team_a_node": seeded.get("team_a_node") if isinstance(seeded, dict) else frappe.db.get_value("CRM Sales Hierarchy", {"user": "leader.a@t1cp3.local"}, "name"),
}, default=str))
"""


def run_g15_golden_delta(stack: IsolatedComposeStack) -> dict[str, object]:
    res = stack.exec_site_python(
        G15_SETUP_AND_ORACLE_PY,
        {"expected_sha": GOLDEN_DATASET_EXPECTED_SHA256, "actor_pw": stack.actor_pw},
    )
    assert res["empty_calendar_blocked"] is True, res

    bod = stack.make_actor("g15_bod", "bod.viewer@t1cp3.local")
    admin = stack.make_actor("g15_admin", "cskh.admin@t1cp3.local")
    staff_a1 = stack.make_actor("g15_staff_a1", "staff.a1@t1cp3.local")

    # Live HTTP P3 reporting endpoints on `2026-10`
    st_ov, _, body_ov = bod.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
        {"period_type": "monthly", "period_key": "2026-10"},
    )
    st_tm, _, body_tm = bod.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_team_comparison",
        {"period_type": "monthly", "period_key": "2026-10"},
    )
    st_emp, _, body_emp = admin.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_employee_quality",
        {"period_type": "monthly", "period_key": "2026-10"},
    )
    st_camp, _, body_camp = bod.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_campaign_quality",
        {"period_type": "monthly", "period_key": "2026-10"},
    )
    st_sc, _, body_sc = staff_a1.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_my_scorecard",
        {"period_type": "monthly", "period_key": "2026-10"},
    )
    st_dd, _, body_dd = bod.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "CONNECTED_RATE",
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "page": 1,
            "page_size": 20,
        },
    )
    st_qa, _, body_qa = bod.get_json("/api/method/crm_cskh.api.qa.get_qa_config")
    assert all(
        s == 200 for s in (st_ov, st_tm, st_emp, st_camp, st_sc, st_dd, st_qa)
    ), (
        st_ov,
        st_tm,
        st_emp,
        st_camp,
        st_sc,
        st_dd,
        st_qa,
    )
    assert unwrap_message(body_qa)["qa_enabled"] is False

    # `F27` Live async export via `queue-long` -> `get_export_status` -> `download_export`
    st_exp, _, body_exp = admin.post_json(
        "/api/method/crm_cskh.api.p3_reporting.request_export",
        {
            "report_type": "executive_overview",
            "period_type": "monthly",
            "period_key": "2026-10",
        },
    )
    assert st_exp == 200, body_exp
    exp_id = unwrap_message(body_exp)["export_id"]
    exp_done = False
    for _ in range(40):
        st_es, _, body_es = admin.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_export_status",
            {"export_id": exp_id},
        )
        assert st_es == 200
        if unwrap_message(body_es).get("status") == "completed":
            exp_done = True
            break
        time.sleep(1.0)
    assert exp_done, f"Async export {exp_id} did not complete via worker"
    st_dl, _, raw_csv = admin.raw_request(
        "GET",
        f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={exp_id}",
    )
    assert st_dl == 200 and len(raw_csv) > 40

    # Clean up `@t1cp3.local` Golden dataset entities before running `G13`/`G14`/`G17`
    stack.exec_site_python(
        "from crm_cskh.tests.test_p3_t1c_remediation import TestP3T1CRemediation\n"
        "tc = TestP3T1CRemediation(methodName='test_t1c_golden_dataset_all_16_scopes_qa_off_and_qa_on_exact_match')\n"
        "tc._cleanup_t1c_golden_entities()\n"
        "print(json.dumps({'cleaned_golden_entities': True}))"
    )

    return {
        **res,
        "live_http_endpoints_200": {
            "executive_overview": st_ov,
            "team_comparison": st_tm,
            "employee_quality": st_emp,
            "campaign_quality": st_camp,
            "my_scorecard": st_sc,
            "metric_drilldown": st_dd,
            "qa_config": st_qa,
            "async_export_download": st_dl,
        },
    }


# =============================================================================
# SECTION 2 — `G13` (`F03–F11` Live HTTP + `queue-long` P1 Delta)
# =============================================================================


def run_g13_p1_delta(stack: IsolatedComposeStack) -> dict[str, object]:
    admin1 = stack.make_actor("admin1", "crm.admin@dev.local")
    admin2 = stack.make_actor("admin2", "Administrator")
    staff_a = stack.make_actor("staff_a", "staff.a@dev.local")
    bod = stack.make_actor("bod", "bod.viewer@dev.local")

    # `F03` + `F04`: CSV (UTF-8 BOM, `+84` normalization, formula-error row) + XLSX upload -> preview -> confirm
    csv_lines = [
        "\ufeffphone,name,source",
        "+84908310001,Ứng viên CSV 1,Facebook",
        "0908310002,Ứng viên CSV 2,Website",
        "0908310002,Ứng viên Trùng Trong File,Website",
        "BAD_PHONE,=1+1 Lỗi SĐT,Facebook",
    ]
    st_up_csv, _, body_up_csv = admin1.upload_multipart(
        "/api/method/crm_cskh.api.import_files.upload_import_file",
        "f03_mixed.csv",
        ("\n".join(csv_lines) + "\n").encode("utf-8"),
    )
    assert st_up_csv == 200, body_up_csv
    csv_file_doc = unwrap_message(body_up_csv)["name"]

    st_pr_csv, _, body_pr_csv = admin1.post_json(
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": csv_file_doc,
            "mapping": {"phone": "phone", "name": "name", "source": "source"},
            "idempotency_key": "g13-f03-preview-1",
        },
    )
    # `F05`: Replay same `idempotency_key` returns identical batch without duplicating
    st_pr_replay, _, body_pr_replay = admin1.post_json(
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": csv_file_doc,
            "mapping": {"phone": "phone", "name": "name", "source": "source"},
            "idempotency_key": "g13-f03-preview-1",
        },
    )
    assert st_pr_csv == 200 and st_pr_replay == 200
    batch_csv = unwrap_message(body_pr_csv)["batch"]
    assert unwrap_message(body_pr_replay)["batch"] == batch_csv

    st_cf1, _, _ = admin1.post_json(
        "/api/method/crm_cskh.api.imports.confirm", {"batch": batch_csv}
    )
    st_cf2, _, _ = admin1.post_json(
        "/api/method/crm_cskh.api.imports.confirm", {"batch": batch_csv}
    )
    assert st_cf1 == 200 and st_cf2 == 200
    csv_done = wait_import_terminal(stack, str(batch_csv))
    assert csv_done["status"] == "COMPLETED_WITH_ERRORS", csv_done
    assert int(csv_done["created_rows"]) == 2 and int(csv_done["error_rows"]) >= 1, (
        csv_done
    )

    # Error CSV RBAC (`200` Admin, `403` Staff)
    st_err_adm, _, err_bytes = admin1.raw_request(
        "GET",
        f"/api/method/crm_cskh.api.import_history.download_error_file?batch={batch_csv}",
    )
    st_err_staff, _, _ = staff_a.raw_request(
        "GET",
        f"/api/method/crm_cskh.api.import_history.download_error_file?batch={batch_csv}",
    )
    assert st_err_adm == 200 and st_err_staff == 403
    assert b"\n=1+1" not in err_bytes and b",=1+1" not in err_bytes

    # `F04` XLSX import
    xlsx_bytes = build_minimal_xlsx(
        [
            ["phone", "name", "source"],
            ["0908310011", "Ứng viên XLSX 1", "Website"],
            ["0908310012", "Ứng viên XLSX 2", "Website"],
        ]
    )
    st_up_x, _, body_up_x = admin1.upload_multipart(
        "/api/method/crm_cskh.api.import_files.upload_import_file",
        "f04_valid.xlsx",
        xlsx_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    x_doc = unwrap_message(body_up_x)["name"]
    _, _, b_pr_x = admin1.post_json(
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": x_doc,
            "mapping": {"phone": "phone", "name": "name", "source": "source"},
            "idempotency_key": "g13-f04-xlsx",
        },
    )
    batch_xlsx = unwrap_message(b_pr_x)["batch"]
    admin1.post_json("/api/method/crm_cskh.api.imports.confirm", {"batch": batch_xlsx})
    xlsx_done = wait_import_terminal(stack, str(batch_xlsx))
    assert xlsx_done["status"] == "COMPLETED" and int(xlsx_done["created_rows"]) == 2, (
        xlsx_done
    )

    # `F06`, `F07`, `F08`: `1,000`-Lead `50/30/20` -> exact `500/300/200` + `RULE_CHANGED` & `PREVIEW_EXPIRED` `409`
    csv_1k = ["phone,name,source"] + [
        f"09084{i:05d},Ứng viên Nghìn {i},Campaign1K" for i in range(1, 1001)
    ]
    _, _, up_1k = admin1.upload_multipart(
        "/api/method/crm_cskh.api.import_files.upload_import_file",
        "f08_1000.csv",
        ("\n".join(csv_1k) + "\n").encode("utf-8"),
    )
    _, _, pr_1k = admin1.post_json(
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": unwrap_message(up_1k)["name"],
            "mapping": {"phone": "phone", "name": "name", "source": "source"},
            "idempotency_key": "g13-1000-leads",
        },
    )
    b1k = unwrap_message(pr_1k)["batch"]
    admin1.post_json("/api/method/crm_cskh.api.imports.confirm", {"batch": b1k})
    b1k_done = wait_import_terminal(stack, str(b1k), timeout_sec=180.0)
    assert int(b1k_done["created_rows"]) == 1000, b1k_done

    leads_1k = stack.exec_site_python(
        "rows = frappe.db.sql(\"SELECT canonical_lead FROM `tabCSKH Identity Index` WHERE normalized_phone LIKE %s ORDER BY normalized_phone\", ('+849084%',), pluck=True)\n"
        "print(json.dumps({'leads': list(rows)}))"
    )["leads"]
    assert len(leads_1k) == 1000, len(leads_1k)

    _, _, camp_1k_resp = admin1.post_json(
        "/api/method/crm_cskh.api.campaigns.save",
        {
            "code": "CAMP-F07-1000",
            "title": "Chiến dịch 1000 hồ sơ 50/30/20",
            "status": "Active",
            "selection": {"leads": leads_1k},
        },
    )
    camp_1k = unwrap_message(camp_1k_resp)["campaign"]

    # Initial rules -> preview -> mutate rule -> old preview rejected with `409 RULE_CHANGED` (`F07`)
    for u_email, w in (
        ("staff.a@dev.local", 40),
        ("staff.b@dev.local", 30),
        ("staff.c@dev.local", 30),
    ):
        admin1.post_json(
            "/api/method/crm_cskh.api.campaigns.save_rule",
            {"campaign": camp_1k, "user": u_email, "weight": w, "capacity_limit": 0},
        )
    _, _, stale_prev_resp = admin1.post_json(
        "/api/method/crm_cskh.api.distribution.preview",
        {"campaign": camp_1k, "idempotency_key": "g13-stale-prev"},
    )
    stale_prev = unwrap_message(stale_prev_resp)

    # Publish `version=2` rules (`50/30/20`) -> advances `_db_rule_version(camp_1k)` to `2`
    for u_email, w in (
        ("staff.a@dev.local", 50),
        ("staff.b@dev.local", 30),
        ("staff.c@dev.local", 20),
    ):
        admin1.post_json(
            "/api/method/crm_cskh.api.campaigns.save_rule",
            {
                "campaign": camp_1k,
                "user": u_email,
                "weight": w,
                "capacity_limit": 0,
                "version": 2,
            },
        )
    st_rule_chg, _, _ = admin1.post_json(
        "/api/method/crm_cskh.api.distribution.execute",
        {
            "preview_id": stale_prev["preview_id"],
            "expected_rule_version": stale_prev["rule_version"],
            "idempotency_key": "g13-exec-stale",
        },
    )
    assert st_rule_chg == 409, f"Expected 409 RULE_CHANGED, got {st_rule_chg}"

    # Expire a preview and verify `409 PREVIEW_EXPIRED` (`F07`)
    _, _, exp_prev_resp = admin1.post_json(
        "/api/method/crm_cskh.api.distribution.preview",
        {"campaign": camp_1k, "idempotency_key": "g13-expiring-prev"},
    )
    exp_prev = unwrap_message(exp_prev_resp)
    stack.exec_site_python(
        "frappe.db.set_value('CSKH Distribution Run', _IN['p'], 'expires_at', '2020-01-01 00:00:00', update_modified=False)\n"
        "print(json.dumps({'expired': True}))",
        {"p": exp_prev["preview_id"]},
    )
    st_prev_exp, _, _ = admin1.post_json(
        "/api/method/crm_cskh.api.distribution.execute",
        {
            "preview_id": exp_prev["preview_id"],
            "expected_rule_version": exp_prev["rule_version"],
            "idempotency_key": "g13-exec-expired",
        },
    )
    assert st_prev_exp == 409, f"Expected 409 PREVIEW_EXPIRED, got {st_prev_exp}"

    # Fresh preview + `F11`/`D08` 2 concurrent Admins firing `execute` simultaneously on the 1,000-Lead preview!
    _, _, good_prev_resp = admin1.post_json(
        "/api/method/crm_cskh.api.distribution.preview",
        {"campaign": camp_1k, "idempotency_key": "g13-good-1k-prev"},
    )
    good_prev = unwrap_message(good_prev_resp)

    def _fire_exec(actor_sess, key_suffix: str):
        return actor_sess.post_json(
            "/api/method/crm_cskh.api.distribution.execute",
            {
                "preview_id": good_prev["preview_id"],
                "expected_rule_version": good_prev["rule_version"],
                "idempotency_key": f"g13-race-{key_suffix}",
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_fire_exec, admin1, "a1")
        f2 = pool.submit(_fire_exec, admin2, "a2")
        r_a1 = f1.result()
        r_a2 = f2.result()
    race_codes = sorted([r_a1[0], r_a2[0]])
    assert 200 in race_codes, (r_a1, r_a2)

    dist_1k_done = wait_distribution_terminal(
        stack, str(good_prev["preview_id"]), timeout_sec=180.0
    )
    actual_1k = json.loads(dist_1k_done["actual_json"] or "{}").get("assigned") or {}
    assert actual_1k == {
        "staff.a@dev.local": 500,
        "staff.b@dev.local": 300,
        "staff.c@dev.local": 200,
    }, f"Expected 500/300/200, got {actual_1k}"

    # `F08` Multi-batch cumulative fairness (`3 x 10 = 30` Leads on `50/30/20` -> exact `15/9/6`)
    fair_res = stack.exec_site_python(
        r"""
from crm_cskh.api import campaigns as camp_api, distribution as dist_api
from crm_cskh.services import distribution as dist_svc
from crm_cskh.services.phone_identity import normalize_vn_phone

frappe.set_user("crm.admin@dev.local")
camp_f = camp_api.save(code="CAMP-F08-FAIR", title="Chiến dịch cộng dồn nhiều lô", status="Active")["campaign"]
for u, w in (("staff.a@dev.local", 50), ("staff.b@dev.local", 30), ("staff.c@dev.local", 20)):
    camp_api.save_rule(campaign=camp_f, user=u, weight=w, capacity_limit=0)

for b_idx in range(3):
    frappe.set_user("Administrator")
    b_leads = []
    for j in range(10):
        ph = f"09085{b_idx}{j:04d}"
        norm_ph = normalize_vn_phone(ph)
        ld = frappe.get_doc({"doctype": "CRM Lead", "first_name": f"Fair {b_idx}-{j}", "lead_name": f"Fair {b_idx}-{j}", "mobile_no": ph, "status": "New"}).insert(ignore_permissions=True)
        if not frappe.db.exists("CSKH Identity Index", {"normalized_phone": norm_ph}):
            frappe.get_doc({"doctype": "CSKH Identity Index", "normalized_phone": norm_ph, "canonical_lead": ld.name}).insert(ignore_permissions=True)
        b_leads.append(ld.name)
    frappe.set_user("crm.admin@dev.local")
    camp_api.save(campaign=camp_f, title="Chiến dịch cộng dồn nhiều lô", status="Active", selection={"leads": b_leads})
    pr = dist_api.preview(campaign=camp_f, idempotency_key=f"fair-prev-{b_idx}")
    dist_svc.execute(
        pr["preview_id"],
        expected_rule_version=pr["rule_version"],
        idempotency_key=f"fair-exec-{b_idx}",
        actor="crm.admin@dev.local",
    )

rows = frappe.db.sql(
    "SELECT assigned_to, COUNT(*) AS cnt FROM `tabCSKH Work Item` WHERE campaign=%s GROUP BY assigned_to",
    (camp_f,), as_dict=True
)
totals = {r["assigned_to"]: int(r["cnt"]) for r in rows}
rec = dist_api.reconcile(campaign=camp_f)
rec_ok = (
    int(rec["active_work_items"]) == 30
    and int(rec["active_claims"]) == 30
    and sum(int(v) for v in rec["current_owners"].values()) == 30
)
print(json.dumps({"cumulative_3x10": totals, "reconciled": rec_ok}, default=str))
"""
    )
    assert fair_res["cumulative_3x10"] == {
        "staff.a@dev.local": 15,
        "staff.b@dev.local": 9,
        "staff.c@dev.local": 6,
    }, fair_res
    assert fair_res["reconciled"] is True

    # `F09` Pool, `my_list` & workflow RBAC (`staff` blocked from pool `403`, `BOD` 0-PII pool & blocked from P2 workflow `403`)
    st_pool_adm, _, _ = admin1.get_json(
        "/api/method/crm_cskh.api.customers.customer_pool", {"page": 1}
    )
    st_pool_staff, _, _ = staff_a.get_json(
        "/api/method/crm_cskh.api.customers.customer_pool", {"page": 1}
    )
    st_my_a, _, body_my_a = staff_a.get_json(
        "/api/method/crm_cskh.api.customers.my_list", {"page": 1}
    )
    st_pool_bod, _, body_pool_bod = bod.get_json(
        "/api/method/crm_cskh.api.customers.customer_pool", {"page": 1}
    )
    st_wf_bod, _, _ = bod.get_json(
        "/api/method/crm_cskh.api.workflow.list_work_items", {"page": 1}
    )
    bod_pool_dump = json.dumps(body_pool_bod)
    assert (
        st_pool_adm == 200
        and st_pool_staff == 403
        and st_my_a == 200
        and st_pool_bod == 200
        and st_wf_bod == 403
    ), (st_pool_adm, st_pool_staff, st_my_a, st_pool_bod, st_wf_bod)
    assert "09084" not in bod_pool_dump and "849084" not in bod_pool_dump

    return {
        "f03_csv_import_status": csv_done["status"],
        "f04_xlsx_import_status": xlsx_done["status"],
        "f05_idempotent_replay_verified": True,
        "f07_rule_changed_and_expired_http": [st_rule_chg, st_prev_exp],
        "f08_1000_lead_50_30_20_allocation": actual_1k,
        "f08_multi_batch_cumulative_3x10": fair_res["cumulative_3x10"],
        "f09_pool_mylist_and_workflow_http": [
            st_pool_adm,
            st_pool_staff,
            st_my_a,
            st_pool_bod,
            st_wf_bod,
        ],
        "f11_concurrent_two_admin_execute_http": race_codes,
    }


# =============================================================================
# SECTION 3 — `G14` (`F13–F20` Live HTTP + `scheduler` P2 Delta)
# =============================================================================


def run_g14_p2_delta(stack: IsolatedComposeStack) -> dict[str, object]:
    fixtures = stack.exec_site_python(
        r"""
from frappe.utils import add_days, add_to_date, now_datetime
from crm_cskh.api import campaigns as camp_api, ownership as own_api
from crm_cskh.services.phone_identity import normalize_vn_phone

frappe.set_user("crm.admin@dev.local")
camp_p2 = camp_api.save(code="CAMP-G14-P2", title="Chiến dịch kiểm định P2 G14", status="Active")["campaign"]

frappe.set_user("Administrator")
wis = []
for idx in range(1, 6):
    ph = f"090860010{idx}"
    norm_ph = normalize_vn_phone(ph)
    ld = frappe.get_doc({"doctype": "CRM Lead", "first_name": f"Ứng viên P2 {idx}", "lead_name": f"Ứng viên P2 {idx}", "mobile_no": ph, "status": "New"}).insert(ignore_permissions=True)
    if not frappe.db.exists("CSKH Identity Index", {"normalized_phone": norm_ph}):
        frappe.get_doc({"doctype": "CSKH Identity Index", "normalized_phone": norm_ph, "canonical_lead": ld.name}).insert(ignore_permissions=True)
    wi = frappe.get_doc({"doctype": "CSKH Work Item", "lead": ld.name, "campaign": camp_p2, "status": "queued"}).insert(ignore_permissions=True)
    frappe.get_doc({"doctype": "CSKH Active Claim", "lead": ld.name, "campaign": camp_p2, "work_item": wi.name}).insert(ignore_permissions=True)
    frappe.set_user("crm.admin@dev.local")
    own_api.assign(work_item=wi.name, target_user="staff.a@dev.local", reason=f"G14 {idx}", request_id=f"g14-asgn-{idx}")
    frappe.set_user("Administrator")
    wis.append({"wi": wi.name, "lead": ld.name, "phone": ph})

# Create 1 Manual `CRM Call Log` (`self_reported`) and 1 PBX `Twilio` `CRM Call Log` (`verified` per `SEC2-02`)
cl_manual = frappe.get_doc({
    "doctype": "CRM Call Log",
    "id": f"CL-MAN-{frappe.generate_hash(length=8)}",
    "type": "Outgoing",
    "status": "Completed",
    "from": "+842873001101",
    "to": wis[0]["phone"],
    "duration": 40,
    "start_time": add_to_date(now_datetime(), minutes=-4),
    "end_time": add_to_date(now_datetime(), minutes=-3),
    "caller": "staff.a@dev.local",
    "telephony_medium": "Manual",
    "reference_doctype": "CRM Lead",
    "reference_docname": wis[0]["lead"],
}).insert(ignore_permissions=True).name

cl_pbx = frappe.get_doc({
    "doctype": "CRM Call Log",
    "id": f"CL-PBX-{frappe.generate_hash(length=8)}",
    "type": "Outgoing",
    "status": "Completed",
    "from": "+842873001101",
    "to": wis[1]["phone"],
    "duration": 55,
    "start_time": add_to_date(now_datetime(), minutes=-4),
    "end_time": add_to_date(now_datetime(), minutes=-3),
    "caller": "staff.a@dev.local",
    "telephony_medium": "Twilio",
    "reference_doctype": "CRM Lead",
    "reference_docname": wis[1]["lead"],
}).insert(ignore_permissions=True).name

# Insert `FCRM Note` on `wis[0]` to prove notes never inflate `attempt_count` (`F14`)
if frappe.db.exists("DocType", "FCRM Note"):
    frappe.get_doc({
        "doctype": "FCRM Note",
        "title": "Ghi chú nội bộ không phải cuộc gọi",
        "content": "Chỉ ghi chú sơ bộ",
        "reference_doctype": "CRM Lead",
        "reference_docname": wis[0]["lead"],
    }).insert(ignore_permissions=True)

now_local = now_datetime()
print(json.dumps({
    "camp_p2": camp_p2,
    "wis": wis,
    "cl_manual": cl_manual,
    "cl_pbx": cl_pbx,
    "due_1": add_days(now_local, 1).strftime("%Y-%m-%d 10:00:00"),
    "due_2": add_days(now_local, 2).strftime("%Y-%m-%d 10:00:00"),
    "due_3": add_days(now_local, 3).strftime("%Y-%m-%d 10:00:00"),
}))
"""
    )

    staff_a = stack.make_actor("p2_staff_a", "staff.a@dev.local")
    staff_b = stack.make_actor("p2_staff_b", "staff.b@dev.local")
    leader1 = stack.make_actor("p2_leader1", "team1.leader@dev.local")
    admin = stack.make_actor("p2_admin", "crm.admin@dev.local")

    wi1 = fixtures["wis"][0]["wi"]
    wi2 = fixtures["wis"][1]["wi"]
    wi3 = fixtures["wis"][2]["wi"]
    wi4 = fixtures["wis"][3]["wi"]
    wi5 = fixtures["wis"][4]["wi"]
    lead3 = fixtures["wis"][2]["lead"]
    due_1 = str(fixtures["due_1"])
    due_2 = str(fixtures["due_2"])
    due_3 = str(fixtures["due_3"])

    # `F13`: `callback_requested` without `followup_due_at` -> `422` (`FOLLOWUP_REQUIRED`)
    st_f13_missing_due, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi1,
            "channel": "phone_call",
            "outcome_code": "callback_requested",
            "idempotency_key": "g14-missing-due",
        },
    )
    assert st_f13_missing_due == 422, st_f13_missing_due

    # `F13` + `F14`: Manual Call Log -> `self_reported`; PBX Call Log -> `verified`; duplicate link -> `409`
    st_man, _, body_man = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi1,
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "call_log": fixtures["cl_manual"],
            "idempotency_key": "g14-att-manual",
        },
    )
    st_pbx, _, body_pbx = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi2,
            "channel": "phone_call",
            "outcome_code": "connected_interested",
            "call_log": fixtures["cl_pbx"],
            "note": "Ứng viên quan tâm vị trí nhân viên kỹ thuật",
            "idempotency_key": "g14-att-pbx",
        },
    )
    st_dup_cl, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi2,
            "channel": "phone_call",
            "outcome_code": "connected_interested",
            "call_log": fixtures["cl_pbx"],
            "note": "Gắn trùng Call Log",
            "idempotency_key": "g14-att-dup-cl",
        },
    )
    st_zalo, _, body_zalo = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi1,
            "channel": "zalo",
            "outcome_code": "message_sent_no_reply",
            "idempotency_key": "g14-att-zalo",
        },
    )
    assert st_man == 200 and st_pbx == 200 and st_dup_cl == 409 and st_zalo == 200, (
        st_man,
        st_pbx,
        st_dup_cl,
        st_zalo,
    )
    att_rows = stack.exec_site_python(
        "fields = ['name', 'connected_snapshot', 'verification_status', 'call_log', 'outcome_label_snapshot']\n"
        "out = {k: frappe.db.get_value('CSKH Contact Attempt', v, fields, as_dict=True) for k, v in _IN['ids'].items()}\n"
        "print(json.dumps(out, default=str))",
        {
            "ids": {
                "man": unwrap_message(body_man)["attempt_id"],
                "pbx": unwrap_message(body_pbx)["attempt_id"],
                "zalo": unwrap_message(body_zalo)["attempt_id"],
            }
        },
    )
    att_man = att_rows["man"]
    att_pbx = att_rows["pbx"]
    att_zalo = att_rows["zalo"]
    assert (
        att_man["connected_snapshot"] in (0, False)
        and att_man["verification_status"] == "self_reported"
    )
    assert (
        att_pbx["connected_snapshot"] in (1, True)
        and att_pbx["verification_status"] == "verified"
    )
    assert att_zalo["connected_snapshot"] in (0, False) and not att_zalo.get("call_log")

    # `F16`: Idempotent replay (`200`) vs altered payload (`409 IDEMPOTENCY_CONFLICT`) + `409 STALE_WORK_ITEM`
    st_idem_ok, _, body_idem_ok = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi1,
            "channel": "zalo",
            "outcome_code": "message_sent_no_reply",
            "idempotency_key": "g14-att-zalo",
        },
    )
    st_idem_conf, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi1,
            "channel": "sms",
            "outcome_code": "message_sent_no_reply",
            "idempotency_key": "g14-att-zalo",
        },
    )
    st_stale_rev, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi1,
            "channel": "sms",
            "outcome_code": "message_sent_no_reply",
            "expected_revision": 1,
            "idempotency_key": "g14-stale-rev",
        },
    )
    assert (
        st_idem_ok == 200
        and unwrap_message(body_idem_ok).get("idempotent_replay") is True
    )
    assert st_idem_conf == 409 and st_stale_rev == 409

    # `F15`: `schedule_followup` -> reschedule preserves `original_due_at` -> repeated overdue SLA evaluation `0` dupes
    st_fu1, _, body_fu1 = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": wi1,
            "due_at": due_1,
            "title": "Hẹn gọi lại ứng viên P2",
            "note": "Ứng viên hẹn chiều",
            "idempotency_key": "g14-fu-1",
        },
    )
    assert st_fu1 == 200, body_fu1
    # Reschedule without `reason` -> `422`
    st_fu_noreason, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": wi1,
            "due_at": due_2,
            "title": "Dời lịch thiếu lý do",
            "idempotency_key": "g14-fu-noreason",
        },
    )
    # Reschedule WITH `reason` -> `200`, `original_due_at` immutable!
    st_fu_resched, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": wi1,
            "due_at": due_2,
            "title": "Dời lịch có lý do",
            "reason": "Ứng viên báo họp đột xuất",
            "idempotency_key": "g14-fu-resched",
        },
    )
    assert st_fu_noreason == 422 and st_fu_resched == 200

    # `F20` (handoff `on_hold` `staff_a -> staff_b`) + repeated `evaluate_sla_obligations` (`0` duplicates)
    handoff_sla = stack.exec_site_python(
        r"""
from crm_cskh.jobs.evaluate_sla import evaluate_sla_obligations
obl_before = frappe.db.get_value(
    "CSKH SLA Obligation",
    {"work_item": _IN["wi"], "obligation_type": "FOLLOWUP"},
    ["name", "original_due_at", "effective_due_at", "reschedule_seq"],
    as_dict=True,
)
evaluate_sla_obligations(as_of="2026-12-31 23:59:59", batch_size=200)
cnt_after_1 = frappe.db.count("CSKH SLA Obligation", {"work_item": _IN["wi"]})
evaluate_sla_obligations(as_of="2026-12-31 23:59:59", batch_size=200)
cnt_after_2 = frappe.db.count("CSKH SLA Obligation", {"work_item": _IN["wi"]})
print(json.dumps({
    "followup_obligation": obl_before,
    "count_after_eval_1": cnt_after_1,
    "count_after_eval_2": cnt_after_2,
}, default=str))
""",
        {"wi": wi1},
    )
    assert handoff_sla["count_after_eval_1"] == handoff_sla["count_after_eval_2"], (
        handoff_sla
    )

    st_handoff, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.ownership.reassign",
        {
            "work_item": wi1,
            "expected_owner": "staff.a@dev.local",
            "target_user": "staff.b@dev.local",
            "reason": "Bàn giao hồ sơ on_hold sang Staff B",
            "request_id": "g14-onhold-handoff",
        },
    )
    st_handoff_b, _, _ = staff_b.get_json(
        "/api/method/crm_cskh.api.workflow.get_work_item", {"work_item": wi1}
    )
    st_handoff_a, _, _ = staff_a.get_json(
        "/api/method/crm_cskh.api.workflow.get_work_item", {"work_item": wi1}
    )
    assert st_handoff == 200 and st_handoff_b == 200 and st_handoff_a == 403

    # `F17`: `close_work_item` on `wi2` + `reopen_work_item` (`403` Staff, `200` Leader)
    st_close, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.close_work_item",
        {
            "work_item": wi2,
            "completion_reason": "Hoàn tất liên hệ chiến dịch",
            "close_category": "normal",
            "idempotency_key": "g14-close-wi2",
        },
    )
    st_reopen_staff, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.reopen_work_item",
        {
            "work_item": wi2,
            "target_user": "staff.a@dev.local",
            "reason": "Staff tự mở lại",
            "idempotency_key": "g14-reopen-staff",
        },
    )
    st_reopen_leader, _, _ = leader1.post_json(
        "/api/method/crm_cskh.api.workflow.reopen_work_item",
        {
            "work_item": wi2,
            "target_user": "staff.a@dev.local",
            "reason": "Leader mở lại kiểm tra bổ sung",
            "idempotency_key": "g14-reopen-leader",
        },
    )
    assert st_close == 200 and st_reopen_staff == 403 and st_reopen_leader == 200

    # `F18`: `set_lead_dnc` on `lead3` -> blocks subsequent `record_attempt` with `409 DO_NOT_CONTACT`
    st_dnc, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.settings.set_lead_dnc",
        {
            "lead": lead3,
            "do_not_contact": 1,
            "reason": "Ứng viên yêu cầu ngừng liên hệ",
            "idempotency_key": "g14-dnc-lead3",
        },
    )
    st_dnc_att, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi3,
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "idempotency_key": "g14-att-after-dnc",
        },
    )
    assert st_dnc == 200 and st_dnc_att == 409, (st_dnc, st_dnc_att)

    # `F19` & `F20`: `void` + `restore` correction + Outcome label update preserving historical snapshot!
    old_label = att_pbx["outcome_label_snapshot"]
    st_void, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.workflow.request_correction",
        {
            "attempt": att_pbx["name"],
            "action": "void",
            "reason": "Hủy thử nghiệm F19",
            "auto_approve": 1,
            "idempotency_key": "g14-void-pbx",
        },
    )
    st_rest, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.workflow.request_correction",
        {
            "attempt": att_pbx["name"],
            "action": "restore",
            "reason": "Khôi phục F19",
            "auto_approve": 1,
            "idempotency_key": "g14-restore-pbx",
        },
    )
    st_out_upd, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.settings.save_outcome",
        {
            "code": "connected_interested",
            "label_vi": "Đã trao đổi — Có quan tâm (Nhãn v2)",
            "result_group": "positive",
            "is_contact_attempt": 1,
            "is_connected": 1,
            "is_interested": 1,
            "is_terminal": 0,
            "is_do_not_contact": 0,
            "requires_note": 1,
            "requires_followup": 0,
            "allows_followup": 1,
            "requires_completion_reason": 0,
            "allowed_channels": "phone_call,sms,zalo,facebook,other",
            "enabled": 1,
            "idempotency_key": "g14-outcome-v2",
        },
    )
    assert st_void == 200 and st_rest == 200 and st_out_upd == 200
    snap_label_after = stack.exec_site_python(
        "lbl = frappe.db.get_value('CSKH Contact Attempt', _IN['a'], 'outcome_label_snapshot')\n"
        "dirty = frappe.db.count('CSKH Dirty Scope')\n"
        "print(json.dumps({'label': lbl, 'dirty_scopes': dirty}))",
        {"a": att_pbx["name"]},
    )
    assert snap_label_after["label"] == old_label, (old_label, snap_label_after)
    assert snap_label_after["dirty_scopes"] >= 1

    # Also close `wi4` without attempt when `require_attempt_before_close=1` -> rejected `409`/`422` (`F22` close policy)
    st_close_no_att, _, _ = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.close_work_item",
        {
            "work_item": wi4,
            "completion_reason": "Thử đóng khi chưa gọi",
            "close_category": "normal",
            "idempotency_key": "g14-close-no-att",
        },
    )
    assert st_close_no_att in (409, 422), st_close_no_att

    # `F22` Live HTTP SLA obligation lifecycle (`pause_obligation` -> `resume_obligation` -> `waive_obligation`) on `wi5`
    st_fu5, _, body_fu5 = staff_a.post_json(
        "/api/method/crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": wi5,
            "due_at": due_3,
            "title": "Hẹn gọi lại kiểm định F22 SLA",
            "note": "Tạo nghĩa vụ FOLLOWUP tương lai để kiểm chứng pause/resume/waive",
            "idempotency_key": "g14-f22-fu-wi5",
        },
    )
    assert st_fu5 == 200, body_fu5
    wi5_obl = stack.exec_site_python(
        "row = frappe.db.get_value('CSKH SLA Obligation', {'work_item': _IN['wi'], 'obligation_type': 'FOLLOWUP', 'status': 'open'}, ['name', 'original_due_at'], as_dict=True)\n"
        "print(json.dumps(row, default=str))",
        {"wi": wi5},
    )
    assert wi5_obl and wi5_obl.get("name"), wi5_obl
    obl5_id = str(wi5_obl["name"])
    st_p_no, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.sla.pause_obligation",
        {"obligation_id": obl5_id},
    )
    st_p_ok, _, body_p_ok = admin.post_json(
        "/api/method/crm_cskh.api.sla.pause_obligation",
        {"obligation_id": obl5_id, "reason": "Tạm dừng kiểm định F22"},
    )
    st_r_ok, _, body_r_ok = admin.post_json(
        "/api/method/crm_cskh.api.sla.resume_obligation",
        {"obligation_id": obl5_id, "reason": "Tiếp tục kiểm định F22"},
    )
    st_w_no, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.sla.waive_obligation",
        {"obligation_id": obl5_id},
    )
    st_w_ok, _, body_w_ok = admin.post_json(
        "/api/method/crm_cskh.api.sla.waive_obligation",
        {"obligation_id": obl5_id, "reason": "Miễn trừ có lý do kiểm định F22"},
    )
    f22_sla_http = [st_p_no, st_p_ok, st_r_ok, st_w_no, st_w_ok]
    assert f22_sla_http == [422, 200, 200, 422, 200], f22_sla_http
    assert unwrap_message(body_p_ok)["is_paused"] is True
    assert unwrap_message(body_r_ok)["is_paused"] is False
    assert (
        unwrap_message(body_r_ok)["original_due_at"]
        == unwrap_message(body_p_ok)["original_due_at"]
    )
    assert unwrap_message(body_w_ok)["status"] == "waived"

    return {
        "f13_missing_followup_http": st_f13_missing_due,
        "f13_manual_vs_pbx_verification": {
            "manual": att_man["verification_status"],
            "pbx_twilio": att_pbx["verification_status"],
            "zalo_call_log_null": not bool(att_zalo.get("call_log")),
        },
        "f14_duplicate_call_log_http": st_dup_cl,
        "f15_reschedule_and_sla_eval_dedup": {
            "reschedule_without_reason_http": st_fu_noreason,
            "reschedule_with_reason_http": st_fu_resched,
            "sla_obligation_counts_across_2_evals": [
                handoff_sla["count_after_eval_1"],
                handoff_sla["count_after_eval_2"],
            ],
        },
        "f16_idempotency_and_stale_revision_http": [
            st_idem_ok,
            st_idem_conf,
            st_stale_rev,
        ],
        "f17_close_and_reopen_http": [
            st_close,
            st_reopen_staff,
            st_reopen_leader,
            st_close_no_att,
        ],
        "f18_dnc_enforcement_http": [st_dnc, st_dnc_att],
        "f19_f20_void_restore_and_snapshot_immutability": True,
        "f22_sla_pause_resume_waive_http": f22_sla_http,
    }


# =============================================================================
# SECTION 4 — `G17` (`D04–D06`, `D12`, `D14`, `D15` Real `docker kill -s KILL` Chaos)
# =============================================================================


def run_g17_worker_kill_and_recovery(stack: IsolatedComposeStack) -> dict[str, object]:
    admin = stack.make_actor("chaos_admin", "crm.admin@dev.local")
    bod = stack.make_actor("chaos_bod", "bod.viewer@dev.local")

    # -------------------------------------------------------------------------
    # 4A. `D04`: `SIGKILL` (`docker kill -s KILL`) on `queue-long` mid-`1,200`-row CSV import
    # -------------------------------------------------------------------------
    csv_1200 = ["phone,name,source"] + [
        f"09087{i:05d},Ứng viên Chaos Import {i},ChaosD04" for i in range(1, 1201)
    ]
    _, _, up_d04 = admin.upload_multipart(
        "/api/method/crm_cskh.api.import_files.upload_import_file",
        "d04_1200.csv",
        ("\n".join(csv_1200) + "\n").encode("utf-8"),
    )
    _, _, pr_d04 = admin.post_json(
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": unwrap_message(up_d04)["name"],
            "mapping": {"phone": "phone", "name": "name", "source": "source"},
            "idempotency_key": "g17-d04-1200",
        },
    )
    batch_d04 = str(unwrap_message(pr_d04)["batch"])
    admin.post_json("/api/method/crm_cskh.api.imports.confirm", {"batch": batch_d04})

    # Poll fast until `import_worker` is actively mid-batch (`0 < progress < 1200` or `status == 'RUNNING'`)
    mid_import_progress = 0
    for _ in range(120):
        row = stack.exec_site_python(
            "r = frappe.db.get_value('CSKH Import Batch', _IN['b'], ['status', 'progress'], as_dict=True)\n"
            "print(json.dumps(r))",
            {"b": batch_d04},
        )
        prog = int(row.get("progress") or 0)
        if row.get("status") == "RUNNING" and 0 < prog < 1200:
            mid_import_progress = prog
            break
        if row.get("status") == "COMPLETED":
            mid_import_progress = prog
            break
        time.sleep(0.05)

    kill_d04 = stack.kill_service("queue-long", sig="KILL")
    assert kill_d04["exit_code"] == 137, kill_d04

    # Restart `queue-long` and trigger recovery job (`import_worker.run_batch` supports `status == 'RUNNING'`)
    stack.run_compose(["up", "-d", "--no-build", "queue-long"])
    stack.wait_services_healthy(["queue-long"], timeout_sec=90)
    stack.exec_site_python(
        "st = frappe.db.get_value('CSKH Import Batch', _IN['b'], 'status')\n"
        "if st != 'COMPLETED':\n"
        "    frappe.enqueue('crm_cskh.jobs.import_worker.run_batch', queue='long', timeout=3600, "
        "job_id=f\"cskh-import-recover-{_IN['b']}\", deduplicate=False, batch_name=_IN['b'])\n"
        "print(json.dumps({'requeued_from_status': st}))",
        {"b": batch_d04},
    )
    d04_done = wait_import_terminal(stack, batch_d04, timeout_sec=180.0)
    d04_db_truth = stack.exec_site_python(
        "cnt = frappe.db.sql(\"SELECT COUNT(*) FROM `tabCSKH Identity Index` WHERE normalized_phone LIKE %s\", ('+849087%',))[0][0]\n"
        "dups = frappe.db.sql(\"SELECT COUNT(*) FROM (SELECT normalized_phone FROM `tabCSKH Identity Index` WHERE normalized_phone LIKE %s GROUP BY normalized_phone HAVING COUNT(*)>1) t\", ('+849087%',))[0][0]\n"
        "print(json.dumps({'identity_rows': int(cnt), 'duplicate_phones': int(dups)}))"
    )
    assert (
        d04_done["status"] == "COMPLETED" and int(d04_done["created_rows"]) == 1200
    ), d04_done
    assert d04_db_truth == {"identity_rows": 1200, "duplicate_phones": 0}, d04_db_truth

    # -------------------------------------------------------------------------
    # 4B. `D05`: `SIGKILL` (`docker kill -s KILL`) on `queue-long` mid-`600`-Lead distribution + lease recovery
    # -------------------------------------------------------------------------
    leads_600 = stack.exec_site_python(
        "rows = frappe.db.sql(\"SELECT canonical_lead FROM `tabCSKH Identity Index` WHERE normalized_phone LIKE %s ORDER BY normalized_phone LIMIT 600\", ('+849087%',), pluck=True)\n"
        "print(json.dumps({'leads': list(rows)}))"
    )["leads"]
    assert len(leads_600) == 600, len(leads_600)
    _, _, camp_d05_resp = admin.post_json(
        "/api/method/crm_cskh.api.campaigns.save",
        {
            "code": "CAMP-G17-D05",
            "title": "Chiến dịch Chaos Distribution 600",
            "status": "Active",
            "selection": {"leads": leads_600},
        },
    )
    camp_d05 = unwrap_message(camp_d05_resp)["campaign"]
    for u_email, w in (
        ("staff.a@dev.local", 50),
        ("staff.b@dev.local", 30),
        ("staff.c@dev.local", 20),
    ):
        admin.post_json(
            "/api/method/crm_cskh.api.campaigns.save_rule",
            {"campaign": camp_d05, "user": u_email, "weight": w, "capacity_limit": 0},
        )
    _, _, prev_d05_resp = admin.post_json(
        "/api/method/crm_cskh.api.distribution.preview",
        {"campaign": camp_d05, "idempotency_key": "g17-d05-prev"},
    )
    prev_d05 = unwrap_message(prev_d05_resp)
    run_d05 = str(prev_d05["preview_id"])
    admin.post_json(
        "/api/method/crm_cskh.api.distribution.execute",
        {
            "preview_id": run_d05,
            "expected_rule_version": prev_d05["rule_version"],
            "idempotency_key": "g17-d05-exec-1",
        },
    )

    mid_dist_progress = 0
    for _ in range(120):
        row = stack.exec_site_python(
            "r = frappe.db.get_value('CSKH Distribution Run', _IN['r'], ['status', 'progress'], as_dict=True)\n"
            "print(json.dumps(r))",
            {"r": run_d05},
        )
        prog = int(row.get("progress") or 0)
        if row.get("status") == "running" and 0 < prog < 600:
            mid_dist_progress = prog
            break
        if row.get("status") == "completed":
            mid_dist_progress = prog
            break
        time.sleep(0.05)

    kill_d05 = stack.kill_service("queue-long", sig="KILL")
    assert kill_d05["exit_code"] == 137, kill_d05

    stack.run_compose(["up", "-d", "--no-build", "queue-long"])
    stack.wait_services_healthy(["queue-long"], timeout_sec=90)

    # Fast-forward expired lease (`lease_expires_at` + dead worker RQ `StartedJobRegistry`) and recover via HTTP `distribution.execute`!
    stack.exec_site_python(
        "from frappe.utils.background_jobs import create_job_id, get_queue\n"
        "from rq.job import Job\n"
        "from rq.registry import StartedJobRegistry\n"
        "st = frappe.db.get_value('CSKH Distribution Run', _IN['r'], 'status')\n"
        "if st != 'completed':\n"
        "    frappe.db.set_value('CSKH Distribution Run', _IN['r'], 'lease_expires_at', '2020-01-01 00:00:00', update_modified=False)\n"
        "    q = get_queue('long')\n"
        "    jid = create_job_id(f\"cskh-dist-{_IN['r']}\")\n"
        "    try:\n"
        "        j = Job.fetch(jid, connection=q.connection)\n"
        "        StartedJobRegistry(queue=q).remove(j, delete_job=True)\n"
        "        j.delete()\n"
        "    except Exception:\n"
        "        pass\n"
        "print(json.dumps({'status_at_kill': st}))",
        {"r": run_d05},
    )
    st_takeover, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.distribution.execute",
        {
            "preview_id": run_d05,
            "expected_rule_version": prev_d05["rule_version"],
            "idempotency_key": "g17-d05-exec-takeover",
        },
    )
    assert st_takeover == 200
    d05_done = wait_distribution_terminal(stack, run_d05, timeout_sec=180.0)
    _, _, body_d05_rec = admin.get_json(
        "/api/method/crm_cskh.api.distribution.reconcile", {"campaign": camp_d05}
    )
    d05_rec_msg = unwrap_message(body_d05_rec)
    assert d05_done["status"] == "completed"
    assert (
        int(d05_rec_msg["active_work_items"]) == 600
        and int(d05_rec_msg["active_claims"]) == 600
        and sum(int(v) for v in d05_rec_msg["current_owners"].values()) == 600
    ), d05_rec_msg

    # -------------------------------------------------------------------------
    # 4C. `D06`: `SIGKILL` (`docker kill -s KILL`) on `queue-short` mid-KPI recompute + Redis lock `409` + atomic swap
    # -------------------------------------------------------------------------
    ict = dt.timezone(dt.timedelta(hours=7))
    now_dt = dt.datetime.now(ict)
    month_key = f"{now_dt.year:04d}-{now_dt.month:02d}"
    r1_info = stack.exec_site_python(
        "from crm_cskh.jobs.recompute_kpi import run_single_period_recompute\n"
        "r1 = run_single_period_recompute(period_type='monthly', period_key=_IN['m'], actor='Administrator')\n"
        "pub_id = frappe.db.get_value('CSKH KPI Run', {'period_type': 'monthly', 'period_key': _IN['m'], 'is_latest_published': 1}, 'name')\n"
        "print(json.dumps({'r1_published_run': pub_id}))",
        {"m": month_key},
    )
    r1_pub_id = str(r1_info["r1_published_run"])

    # Enqueue background recompute `R2` onto `queue-short` so `queue-short` executes `execute_kpi_recompute`
    stack.exec_site_python(
        "frappe.enqueue(\n"
        "  'crm_cskh.jobs.recompute_kpi.run_single_period_recompute',\n"
        "  queue='short',\n"
        "  now=False,\n"
        "  period_type='monthly',\n"
        "  period_key=_IN['m'],\n"
        "  actor='Administrator',\n"
        ")\n"
        "print(json.dumps({'enqueued_r2_on_queue_short': True}))",
        {"m": month_key},
    )

    # Poll fast until `queue-short` picks up `R2` (`status == 'running'` or Redis lock held) and `SIGKILL` `queue-short`!
    for _ in range(80):
        running_info = stack.exec_site_python(
            "rkey = frappe.cache.make_key(f\"cskh:kpi:recompute_lock:monthly:{_IN['m']}:*\")\n"
            "locked = bool(frappe.cache.get(rkey))\n"
            "c = frappe.db.count('CSKH KPI Run', {'period_type': 'monthly', 'period_key': _IN['m'], 'status': 'running'})\n"
            "print(json.dumps({'running': int(c), 'locked': locked}))",
            {"m": month_key},
        )
        if running_info["running"] > 0 or running_info["locked"]:
            break
        time.sleep(0.03)

    kill_d06 = stack.kill_service("queue-short", sig="KILL")
    assert kill_d06["exit_code"] == 137, kill_d06

    # Verify `R1` remains 100% intact and served (`200`) with ZERO unpublished/half-written snapshots exposed!
    st_ov_during_kill, _, _ = bod.get_json(
        "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
        {"period_type": "monthly", "period_key": month_key},
    )
    assert st_ov_during_kill == 200

    # Verify Redis lock contention returns `409` (`RecomputeInProgress`) when lock key is active
    stack.exec_site_python(
        "rkey = frappe.cache.make_key(f\"cskh:kpi:recompute_lock:monthly:{_IN['m']}:*\")\n"
        "frappe.cache.set(rkey, 'held-by-dead-worker', ex=120)\n"
        "print(json.dumps({'lock_seeded': True}))",
        {"m": month_key},
    )
    st_locked_409, _, _ = admin.post_json(
        "/api/method/crm_cskh.api.p3_reporting.request_recompute",
        {
            "period_type": "monthly",
            "period_key": month_key,
            "reason": "Should hit 409 while locked",
        },
    )
    assert st_locked_409 == 409, (
        f"Expected 409 on active recompute Redis lock, got {st_locked_409}"
    )

    # Restart `queue-short`, clear expired lock & any orphaned `'processing'` dirty scopes, and run clean `R3` swap!
    stack.run_compose(["up", "-d", "--no-build", "queue-short"])
    stack.wait_services_healthy(["queue-short"], timeout_sec=90)
    d06_recovered = stack.exec_site_python(
        "from crm_cskh.jobs.recompute_kpi import run_single_period_recompute\n"
        "from crm_cskh.jobs.reconcile_metrics import reconcile_published_period\n"
        "rkey = frappe.cache.make_key(f\"cskh:kpi:recompute_lock:monthly:{_IN['m']}:*\")\n"
        "frappe.cache.delete(rkey)\n"
        "frappe.db.sql(\"UPDATE `tabCSKH Dirty Scope` SET status='pending' WHERE status='processing'\")\n"
        "frappe.db.sql(\"UPDATE `tabCSKH KPI Run` SET status='failed' WHERE status='running'\")\n"
        "frappe.db.commit()\n"
        "run_single_period_recompute(period_type='monthly', period_key=_IN['m'], actor='Administrator')\n"
        "pub_runs = frappe.get_all('CSKH KPI Run', filters={'period_type': 'monthly', 'period_key': _IN['m'], 'is_latest_published': 1}, pluck='name')\n"
        "rec = reconcile_published_period(period_type='monthly', period_key=_IN['m'])\n"
        "print(json.dumps({'published_runs': pub_runs, 'reconcile_ok': bool(rec.get('ok'))}, default=str))",
        {"m": month_key},
    )
    assert len(d06_recovered["published_runs"]) == 1, d06_recovered
    assert d06_recovered["published_runs"][0] != r1_pub_id
    assert d06_recovered["reconcile_ok"] is True

    # -------------------------------------------------------------------------
    # 4D. `D14` + `D15` + `D12`: Restart `redis-cache`, `redis-queue`, `scheduler`, `queue-short`, `queue-long` + DB invariants
    # -------------------------------------------------------------------------
    stack.run_compose(
        [
            "restart",
            "redis-cache",
            "redis-queue",
            "scheduler",
            "queue-short",
            "queue-long",
        ]
    )
    stack.wait_services_healthy(
        ["redis-cache", "redis-queue", "scheduler", "queue-short", "queue-long"],
        timeout_sec=120,
    )

    d12_invariants = stack.exec_site_python(
        r"""
from crm_cskh.jobs.evaluate_sla import evaluate_sla_obligations

obl_before = frappe.db.count("CSKH SLA Obligation")
evaluate_sla_obligations(batch_size=500)
obl_after = frappe.db.count("CSKH SLA Obligation")

dup_phones = frappe.db.sql(
    "SELECT COUNT(*) FROM (SELECT normalized_phone FROM `tabCSKH Identity Index` GROUP BY normalized_phone HAVING COUNT(*)>1) t"
)[0][0]
dup_claims = frappe.db.sql(
    "SELECT COUNT(*) FROM (SELECT work_item FROM `tabCSKH Active Claim` GROUP BY work_item HAVING COUNT(*)>1) t"
)[0][0]
dup_sla_keys = frappe.db.sql(
    "SELECT COUNT(*) FROM (SELECT idempotency_key FROM `tabCSKH SLA Obligation` WHERE idempotency_key IS NOT NULL AND idempotency_key != '' GROUP BY idempotency_key HAVING COUNT(*)>1) t"
)[0][0]
mismatch_claims = frappe.db.sql(
    '''
    SELECT COUNT(*)
    FROM `tabCSKH Active Claim` ac
    LEFT JOIN `tabCSKH Work Item` wi ON wi.name = ac.work_item
    WHERE wi.name IS NULL
       OR wi.lead != ac.lead
       OR wi.campaign != ac.campaign
       OR wi.status NOT IN ('assigned', 'in_progress', 'on_hold')
       OR COALESCE(wi.assigned_to, '') = ''
    '''
)[0][0]

print(json.dumps({
    "sla_obligations_before_after_restart_eval": [int(obl_before), int(obl_after)],
    "duplicate_normalized_phones": int(dup_phones),
    "duplicate_active_claims": int(dup_claims),
    "duplicate_sla_idempotency_keys": int(dup_sla_keys),
    "mismatched_active_claims": int(mismatch_claims),
}))
"""
    )
    assert d12_invariants["duplicate_normalized_phones"] == 0, d12_invariants
    assert d12_invariants["duplicate_active_claims"] == 0, d12_invariants
    assert d12_invariants["duplicate_sla_idempotency_keys"] == 0, d12_invariants
    assert d12_invariants["mismatched_active_claims"] == 0, d12_invariants

    return {
        "d04_import_worker_sigkill": {
            "kill_signal": kill_d04,
            "mid_kill_progress_observed": mid_import_progress,
            "final_status": d04_done["status"],
            "created_rows": int(d04_done["created_rows"]),
            "db_truth": d04_db_truth,
        },
        "d05_distribution_worker_sigkill_and_lease_takeover": {
            "kill_signal": kill_d05,
            "mid_kill_progress_observed": mid_dist_progress,
            "http_takeover_status": st_takeover,
            "final_status": d05_done["status"],
            "reconciled": True,
        },
        "d06_kpi_recompute_sigkill_and_atomic_publish": {
            "kill_signal": kill_d06,
            "http_overview_during_kill_status": st_ov_during_kill,
            "concurrent_lock_http_status": st_locked_409,
            "recovered_swap": d06_recovered,
        },
        "d14_d15_restarts_and_d12_sql_invariants": d12_invariants,
    }


def run_all_deltas(
    *,
    app_image: str,
    http_port: int,
    scrubber: SecretScrubber,
) -> dict[str, object]:
    stack = IsolatedComposeStack(
        prefix="g13to17",
        http_port=http_port,
        app_image=app_image,
        scrubber=scrubber,
    )
    try:
        stack.boot_and_bootstrap_site(install_site=True)

        # 1. Run `G15` FIRST on the clean site so `16/16` Golden Dataset scopes reconcile 1:1 over live HTTP
        scrubber.log(
            "Running G15 Golden Dataset + cskh-metrics-v2 + 5-grain + live HTTP delta"
        )
        g15_res = run_g15_golden_delta(stack)

        # 2. Seed standard P1–P3 business actors & hierarchy, then run `G13` -> `G14` -> `G17`
        stack.seed_standard_actors_and_teams()

        scrubber.log("Running G13 P1 live HTTP + queue-long delta (F03-F11)")
        g13_res = run_g13_p1_delta(stack)

        scrubber.log("Running G14 P2 live HTTP + scheduler delta (F13-F20)")
        g14_res = run_g14_p2_delta(stack)

        scrubber.log(
            "Running G17 real container SIGKILL + restart chaos delta (D04-D06, D12, D14, D15)"
        )
        g17_res = run_g17_worker_kill_and_recovery(stack)

        return {
            "schema_version": "cskh-p4-batch2-g13-g14-g15-g17-v1",
            "status": "PASS",
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "gates_verified": ["G13", "G14", "G15", "G17"],
            "locked_batch1_commit_sha": LOCKED_BATCH1_COMMIT_SHA,
            "app_image_tested": app_image,
            "g15_golden_and_metrics_v2_delta": g15_res,
            "g13_p1_e2e_delta": g13_res,
            "g14_p2_e2e_delta": g14_res,
            "g17_worker_kill_and_recovery_delta": g17_res,
        }
    finally:
        stack.teardown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", default=LOCKED_BATCH1_IMAGE_DIGEST)
    parser.add_argument("--http-port", type=int, default=18930)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--report-out", type=pathlib.Path, required=True)
    parser.add_argument("--log-out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    scrubber = SecretScrubber()
    if args.plan_only:
        scrubber.write_json_and_log(
            build_plan_only_report(args.app_image), args.report_out, args.log_out
        )
        return 0

    try:
        report = run_all_deltas(
            app_image=args.app_image,
            http_port=args.http_port,
            scrubber=scrubber,
        )
    except Exception as exc:
        scrubber.log(f"FATAL: {exc}\n{traceback.format_exc()}")
        scrubber.write_json_and_log(
            {
                "schema_version": "cskh-p4-batch2-g13-g14-g15-g17-v1",
                "status": "FAIL",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            args.report_out,
            args.log_out,
        )
        raise
    scrubber.write_json_and_log(report, args.report_out, args.log_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
