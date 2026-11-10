#!/usr/bin/env python3
"""P4 Đợt 2 — `G06` & `G09` Verifier: N-1 Clone Migration, Idempotent Migrate, Reconciliation & 4-Tier Rollback.

Rehearses on isolated 10-service Compose topology (zero impact on Owner/live stacks):
  1. `G06` / `D01`: Populated `N-1` (`origin/main` Frappe v15.123.1) -> `N` (locked Đợt 1 GHCR digest Frappe v15.126.0)
     migration with P1/P2/P3 business records, legacy `[H:<16hex>]` history, custom `version=2` Outcome/Policy,
     private Export CSV, `BLG-P4-MIG-01` DocType/collation audit, and pre-upgrade backup (`D10`).
  2. `G06` / `D02`: Consecutive `migrator` Run #2 idempotency + schema compatibility classification (`Class A/B`)
     + immutable-table SHA-256 parity + P1/P2/P3 reconciliation + live HTTP post-upgrade write.
  3. `G09`: Tier 1 (`cskh_p3_enabled=false`) & Tier 2 (`cskh_p3_export_enabled=false`) live HTTP rollback + dirty-scope recovery.
  4. `D03`: Pre-cutover `migrator` fail-closed guard (`rc != 0` aborts before switching runtime services).
  5. `G09` / `D13`: Tier 3 Application Image rollback (`N -> N-1` without DB downgrade), preserving post-upgrade writes.
  6. `G09` / `D10–D12`: Tier 4 DR full restore onto an isolated target site + post-backup delta accounting
     documenting the forward-fix vs restore boundary for Class C/D migrations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(THIS_DIR.parent))

from batch2_delta import (  # noqa: E402
    BASELINE_MAIN_COMMIT_SHA,
    LOCKED_BATCH1_COMMIT_SHA,
    LOCKED_BATCH1_IMAGE_DIGEST,
)
from batch2_delta.common_stack import (  # noqa: E402
    APP_RUNTIME_SERVICES,
    IsolatedComposeStack,
    SecretScrubber,
    unwrap_message,
)


def classify_schema_diff(
    before_cols: dict[str, dict[str, str]],
    after_cols: dict[str, dict[str, str]],
) -> dict[str, object]:
    """Classifies schema delta between `N-1` and `N` per `docs/root-plan/P4.md` §15.2."""
    dropped_tables = sorted(set(before_cols) - set(after_cols))
    added_tables = sorted(set(after_cols) - set(before_cols))
    dropped_columns: list[str] = []
    added_non_nullable_no_default: list[str] = []
    added_compatible_columns: list[str] = []
    type_changes: list[str] = []

    for tbl in sorted(set(before_cols) & set(after_cols)):
        b_map = before_cols[tbl]
        a_map = after_cols[tbl]
        for col in sorted(set(b_map) - set(a_map)):
            dropped_columns.append(f"{tbl}.{col}")
        for col in sorted(set(a_map) - set(b_map)):
            meta = a_map[col]
            if meta.get("is_nullable") == "NO" and meta.get("column_default") is None:
                added_non_nullable_no_default.append(f"{tbl}.{col}")
            else:
                added_compatible_columns.append(f"{tbl}.{col}")
        for col in sorted(set(b_map) & set(a_map)):
            if b_map[col].get("column_type") != a_map[col].get("column_type"):
                type_changes.append(
                    f"{tbl}.{col}:{b_map[col].get('column_type')}->{a_map[col].get('column_type')}"
                )

    breaking = bool(
        dropped_tables
        or dropped_columns
        or added_non_nullable_no_default
        or type_changes
    )
    if breaking:
        compat_class = "Class_D_Breaking_Requires_Forward_Fix_Or_Tier4_Restore"
        tier3_image_rollback_safe = False
    elif added_tables or added_compatible_columns:
        compat_class = "Class_B_Additive_Backward_Compatible"
        tier3_image_rollback_safe = True
    else:
        compat_class = "Class_A_Code_And_Runtime_Only"
        tier3_image_rollback_safe = True

    return {
        "compatibility_class": compat_class,
        "tier3_image_rollback_safe": tier3_image_rollback_safe,
        "added_tables": added_tables,
        "dropped_tables": dropped_tables,
        "added_compatible_columns": added_compatible_columns,
        "added_non_nullable_no_default": added_non_nullable_no_default,
        "dropped_columns": dropped_columns,
        "type_changes": type_changes,
    }


def forward_fix_vs_restore_contract() -> dict[str, object]:
    """Explicit operational contract for `G09` forward-fix vs Tier 4 DB restore."""
    return {
        "tier1_and_tier2_feature_flags": {
            "when_to_use": "Regression isolated to P3 reporting, SLA engine, export, or QA toggle.",
            "action": "Set `cskh_p3_enabled=false` or scoped `cskh_p3_export_enabled=false` + `bench clear-cache`.",
            "rto_target_sec": 60,
            "data_loss": "ZERO (P1/P2 writes continue and enqueue `CSKH Dirty Scope` for replay on re-enable).",
        },
        "tier3_image_rollback_n_to_n_minus_1": {
            "when_to_use": "Class A (code/dependency/OS only) or Class B (additive nullable/default columns or new indexes/tables).",
            "action": (
                "Pin `APP_IMAGE` back to verified `N-1` `@sha256` digest, recreate `backend`, `frontend`, "
                "`websocket`, `queue-short`, `queue-long`, `scheduler`, and run `bench --site <site> clear-cache`. "
                "NEVER run `git reset`, `bench update --reset`, or manual `DROP TABLE`/`DROP COLUMN`."
            ),
            "tier3_safe_for_current_batch1_candidate": True,
            "data_loss": "ZERO (all post-upgrade `CRM Lead`, `CSKH Work Item`, and `CSKH Contact Attempt` rows remain intact).",
        },
        "class_c_and_d_forward_fix_vs_tier4_restore": {
            "when_tier3_is_insufficient": (
                "Class C (destructive backfill/data rewrite) or Class D (dropped/renamed/narrowed column or incompatible state enum)."
            ),
            "primary_path_forward_fix": (
                "1. Enable maintenance / pause ingress mutations if data integrity is threatened; "
                "2. Ship a tested forward-fix candidate image (`N+1`) with an idempotent corrective patch; "
                "3. Promote `N+1` by `@sha256` digest so zero post-cutover business writes are lost."
            ),
            "last_resort_tier4_db_restore": (
                "Execute ONLY with explicit Owner (`T0`) sign-off when forward-fix cannot restore integrity within RTO: "
                "1. Query post-backup delta rows (`CSKH Contact Attempt`, `CRM Call Log`, `CRM Task`, `CSKH Assignment History` "
                "with `creation >= pre_migrate_backup_utc`) and export delta audit manifest before overwrite; "
                "2. Restore pre-migrate `bench backup --with-files` (`SQL + private + public + site_config.json`) onto isolated/target site; "
                "3. Boot `N-1` image, run reconciliation, and coordinate manual business replay of the audited delta window."
            ),
        },
    }


def build_plan_only_report(
    n_minus_1_image: str, candidate_image: str
) -> dict[str, object]:
    return {
        "schema_version": "cskh-p4-batch2-g06-g09-v1",
        "mode": "plan_only",
        "status": "PLANNED",
        "gates": ["G06", "G09"],
        "matrix_items": ["D01", "D02", "D03", "D10", "D11", "D12", "D13"],
        "baseline_main_sha": BASELINE_MAIN_COMMIT_SHA,
        "locked_batch1_commit_sha": LOCKED_BATCH1_COMMIT_SHA,
        "n_minus_1_image": n_minus_1_image,
        "candidate_image": candidate_image,
        "forward_fix_vs_restore_contract": forward_fix_vs_restore_contract(),
    }


SNAPSHOT_PY = r"""
import hashlib
from crm_cskh.services import distribution as dist_svc
from crm_cskh.jobs.reconcile_metrics import reconcile_published_period

db_name = frappe.conf.db_name
cols_rows = frappe.db.sql(
    '''
    SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = %s AND (TABLE_NAME LIKE 'tabCSKH%%' OR TABLE_NAME = 'tabCRM Lead')
    ORDER BY TABLE_NAME, COLUMN_NAME
    ''',
    (db_name,),
    as_dict=True,
)
schema_cols = {}
for r in cols_rows:
    schema_cols.setdefault(r["TABLE_NAME"], {})[r["COLUMN_NAME"]] = {
        "column_type": str(r["COLUMN_TYPE"]),
        "is_nullable": str(r["IS_NULLABLE"]),
        "column_default": None if r["COLUMN_DEFAULT"] is None else str(r["COLUMN_DEFAULT"]),
    }

mod_rows = frappe.db.sql(
    '''
    SELECT module, COUNT(*) AS cnt
    FROM `tabDocType`
    WHERE module IN ('CRM CSKH', 'Crm Cskh')
    GROUP BY module
    ORDER BY module
    ''',
    as_dict=True,
)
coll_rows = frappe.db.sql(
    '''
    SELECT TABLE_COLLATION, COUNT(*) AS cnt
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = %s AND TABLE_NAME LIKE 'tabCSKH%%'
    GROUP BY TABLE_COLLATION
    ''',
    (db_name,),
    as_dict=True,
)

def _table_digest(sql_query):
    rows = frappe.db.sql(sql_query, as_dict=True)
    raw = json.dumps(rows, default=str, sort_keys=True)
    return {"count": len(rows), "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}

att_fp = _table_digest(
    "SELECT name, work_item, lead, channel, outcome_code, connected_snapshot, verification_status, owner_at_attempt FROM `tabCSKH Contact Attempt` ORDER BY name"
)
hist_fp = _table_digest(
    "SELECT name, work_item, action, from_user, to_user, actor, event_id FROM `tabCSKH Assignment History` ORDER BY name"
)
corr_fp = _table_digest(
    "SELECT name, attempt, work_item, status, action, requested_by FROM `tabCSKH Attempt Correction` ORDER BY name"
)

counts = {
    "crm_lead": frappe.db.count("CRM Lead"),
    "work_item": frappe.db.count("CSKH Work Item"),
    "active_claim": frappe.db.count("CSKH Active Claim"),
    "contact_attempt": frappe.db.count("CSKH Contact Attempt"),
    "assignment_history": frappe.db.count("CSKH Assignment History"),
    "attempt_correction": frappe.db.count("CSKH Attempt Correction"),
    "sla_obligation": frappe.db.count("CSKH SLA Obligation"),
    "kpi_snapshot_monthly_published": frappe.db.count(
        "CSKH KPI Snapshot",
        {"period_type": "monthly", "period_key": _IN.get("month_key"), "is_latest_published": 1},
    ),
    "export_job": frappe.db.count("CSKH Export Job"),
    "custom_translation": frappe.db.count("Translation", {"source_text": " Batch2 Migration Guard Label "}),
}

outcome_ver = frappe.db.get_value("CSKH Outcome Code", "no_answer", ["version", "label_vi"], as_dict=True)
pol_ver = frappe.db.get_value("CSKH Policy", {"enabled": 1}, ["name", "version"], as_dict=True)

camp_id = _IN.get("campaign_id")
dist_rec = dist_svc.reconcile(camp_id) if camp_id else {}
if dist_rec:
    dist_rec["reconciled"] = (
        int(dist_rec.get("active_work_items", -1)) == int(dist_rec.get("active_claims", -2))
        and int(dist_rec.get("active_claims", -2)) == sum(int(v) for v in (dist_rec.get("current_owners") or {}).values())
    )
month_key = _IN.get("month_key")
kpi_rec = reconcile_published_period(period_type="monthly", period_key=month_key) if month_key else {}

print(json.dumps({
    "frappe_version": getattr(frappe, "__version__", "unknown"),
    "schema_cols": schema_cols,
    "blg_p4_mig_01": {
        "doctype_modules": {r["module"]: int(r["cnt"]) for r in mod_rows},
        "table_collations": {str(r["TABLE_COLLATION"]): int(r["cnt"]) for r in coll_rows},
        "export_job_table_exists": bool(frappe.db.table_exists("CSKH Export Job")),
    },
    "immutable_fingerprints": {
        "contact_attempt": att_fp,
        "assignment_history": hist_fp,
        "attempt_correction": corr_fp,
    },
    "counts": counts,
    "outcome_no_answer": outcome_ver,
    "active_policy": pol_ver,
    "distribution_reconcile": dist_rec,
    "kpi_monthly_reconcile": kpi_rec,
}, default=str))
"""


SEED_N_MINUS_1_PY = r"""
from frappe.utils import add_days, now_datetime
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.api import (
    campaigns as camp_api,
    ownership as own_api,
    p3_reporting as p3_api,
    settings as set_api,
    workflow as wf_api,
)

# 1. Custom Translation + custom version=2 Outcome & Policy (`D02` non-overwrite check)
if not frappe.db.exists("Translation", {"source_text": " Batch2 Migration Guard Label "}):
    frappe.get_doc({
        "doctype": "Translation",
        "language": "vi",
        "source_text": " Batch2 Migration Guard Label ",
        "translated_text": "Nhãn kiểm chứng nâng cấp P4",
    }).insert(ignore_permissions=True)

frappe.set_user("crm.admin@dev.local")
set_api.save_outcome(
    code="no_answer",
    label_vi="Không bắt máy (giữ qua migrate)",
    result_group="unreached",
    is_contact_attempt=1,
    is_connected=0,
    is_interested=0,
    is_terminal=0,
    is_do_not_contact=0,
    requires_note=0,
    requires_followup=0,
    allows_followup=1,
    requires_completion_reason=0,
    allowed_channels="phone_call",
    enabled=1,
    idempotency_key="b2-mig-outcome-v2",
)
set_api.save_policy(
    policy_code="GLOBAL-DEFAULT",
    title="Chính sách mặc định P4 (v2)",
    scope_type="Global",
    enabled=1,
    allowed_backdate_minutes=120,
    require_attempt_before_close=1,
    allow_close_without_contact_for_invalid=1,
    do_not_contact_behavior="block_and_auto_close",
    enabled_channels="phone_call,sms,zalo,facebook,other",
    allow_leader_reopen=1,
    allow_leader_review_correction=1,
    idempotency_key="b2-mig-policy-v2",
)

# 2. Create Campaign + 4 Leads + Work Items + Active Claims
camp = camp_api.save(
    code="CAMP-G06-MIG",
    title="Chiến dịch kiểm định nâng cấp N-1 -> N",
    description="Seeded on N-1 before candidate upgrade",
    status="Active",
)
camp_id = camp["campaign"]

frappe.set_user("Administrator")
work_items = []
for idx, owner_email in enumerate(
    ["staff.a@dev.local", "staff.a@dev.local", "staff.b@dev.local", "staff.c@dev.local"], start=1
):
    phone = f"090810010{idx}"
    lead = frappe.get_doc({
        "doctype": "CRM Lead",
        "first_name": f"Ứng viên N1 {idx}",
        "lead_name": f"Ứng viên N1 {idx}",
        "mobile_no": phone,
        "status": "New",
    }).insert(ignore_permissions=True)
    norm_ph = normalize_vn_phone(phone)
    if not frappe.db.exists("CSKH Identity Index", {"normalized_phone": norm_ph}):
        frappe.get_doc({
            "doctype": "CSKH Identity Index",
            "normalized_phone": norm_ph,
            "canonical_lead": lead.name,
        }).insert(ignore_permissions=True)
    wi = frappe.get_doc({
        "doctype": "CSKH Work Item",
        "lead": lead.name,
        "campaign": camp_id,
        "status": "queued",
    }).insert(ignore_permissions=True)
    frappe.get_doc({
        "doctype": "CSKH Active Claim",
        "lead": lead.name,
        "campaign": camp_id,
        "work_item": wi.name,
    }).insert(ignore_permissions=True)
    frappe.set_user("crm.admin@dev.local")
    own_api.assign(
        work_item=wi.name,
        target_user=owner_email,
        reason=f"Seed N-1 item {idx}",
        request_id=f"b2-n1-assign-{idx}",
    )
    frappe.set_user("Administrator")
    work_items.append(wi.name)

# Legacy `[H:<16hex>]` marker on one historical row (`D01`/`T2-21` regression check)
first_hist = frappe.db.get_value("CSKH Assignment History", {"work_item": work_items[0]}, "name")
if first_hist:
    frappe.db.set_value(
        "CSKH Assignment History",
        first_hist,
        "reason",
        "[H:0123456789abcdef] Giao hồ sơ lịch sử P1",
        update_modified=False,
    )

# 3. Record P2 Contact Attempts, Correction, Close one item, and run P3 KPI + Export on N-1
due_str = add_days(now_datetime(), 1).strftime("%Y-%m-%d 10:00:00")
frappe.set_user("staff.a@dev.local")
att1 = wf_api.record_attempt(
    work_item=work_items[0],
    channel="phone_call",
    outcome_code="no_answer",
    note="Cuộc gọi thử trên bản N-1",
    followup_due_at=due_str,
    followup_title="Gọi lại ứng viên N-1",
    idempotency_key="b2-n1-att-1",
)
att1_id = att1["attempt_id"]
corr_req = wf_api.request_correction(
    attempt=att1_id,
    action="correct",
    corrected_fields={"note": "Cuộc gọi thử trên bản N-1 (đã đính chính)"},
    reason="Bổ sung ghi chú rõ hơn trước nâng cấp",
    idempotency_key="b2-n1-corr-1",
)
frappe.set_user("team1.leader@dev.local")
wf_api.review_correction(
    correction_id=corr_req["correction_id"],
    decision="approve",
    review_note="Leader duyệt đính chính N-1",
    idempotency_key="b2-n1-corr-rev-1",
)

frappe.set_user("staff.b@dev.local")
wf_api.record_attempt(
    work_item=work_items[2],
    channel="phone_call",
    outcome_code="connected_interested",
    note="Ứng viên quan tâm vị trí nhân viên kho",
    idempotency_key="b2-n1-att-2",
)
wf_api.close_work_item(
    work_item=work_items[2],
    completion_reason="Hoàn tất mục tiêu liên hệ chiến dịch trên N-1",
    close_category="normal",
    idempotency_key="b2-n1-close-2",
)

frappe.set_user("Administrator")
now_dt = now_datetime()
month_key = f"{now_dt.year:04d}-{now_dt.month:02d}"
import time as _t
from crm_cskh.jobs.recompute_kpi import run_single_period_recompute
from crm_cskh.jobs.run_export import execute_export_job
from crm_cskh.services.errors import RecomputeInProgress
for _ in range(20):
    try:
        run_single_period_recompute(period_type="monthly", period_key=month_key, actor="Administrator")
        break
    except RecomputeInProgress:
        _t.sleep(1.5)

frappe.set_user("crm.admin@dev.local")
exp_req = p3_api.request_export(
    report_type="executive_overview",
    period_type="monthly",
    period_key=month_key,
)
exp_id = exp_req["export_id"]
frappe.set_user("Administrator")
execute_export_job(export_id=exp_id)

print(json.dumps({
    "campaign_id": camp_id,
    "work_items": work_items,
    "open_work_item_staff_a": work_items[1],
    "month_key": month_key,
    "export_id": exp_id,
}))
"""


def take_pre_upgrade_backup(
    stack: IsolatedComposeStack, host_backup_dir: pathlib.Path
) -> dict[str, object]:
    """Executes `bench --site <site> backup --with-files` inside `backend` and copies artifacts to host."""
    backup_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    body = r"""
import glob, subprocess
from frappe.utils import now_datetime
site = _SITE
pre_attempt_ids = list(frappe.get_all("CSKH Contact Attempt", pluck="name"))
subprocess.check_call(["bench", "--site", site, "backup", "--with-files"], cwd="/home/frappe/frappe-bench")
site_ts = str(now_datetime())
bdir = f"/home/frappe/frappe-bench/sites/{site}/private/backups"
db_gz = sorted(glob.glob(f"{bdir}/*-database.sql.gz"))[-1]
priv_tar = sorted(glob.glob(f"{bdir}/*-private-files.tar"))[-1]
pub_tar = sorted(glob.glob(f"{bdir}/*-files.tar"))
pub_tar = [p for p in pub_tar if not p.endswith("-private-files.tar")][-1]
cfg_json = f"/home/frappe/frappe-bench/sites/{site}/site_config.json"
print(json.dumps({
    "db_gz": db_gz,
    "priv_tar": priv_tar,
    "pub_tar": pub_tar,
    "site_config": cfg_json,
    "backup_site_ts": site_ts,
    "pre_attempt_ids": pre_attempt_ids,
}))
"""
    paths = stack.exec_site_python(body)
    cid = stack.get_container_id("backend")
    host_backup_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    for key in ("db_gz", "priv_tar", "pub_tar", "site_config"):
        cpath = str(paths[key])
        dest = host_backup_dir / pathlib.Path(cpath).name
        subprocess.run(
            ["docker", "cp", f"{cid}:{cpath}", str(dest)],
            check=True,
            capture_output=True,
        )
        copied[key] = str(dest)
    return {
        "backup_recorded_at_utc": backup_utc,
        "backup_site_ts": str(paths["backup_site_ts"]),
        "pre_attempt_ids": list(paths["pre_attempt_ids"]),
        "files_present": {k: pathlib.Path(v).stat().st_size for k, v in copied.items()},
        "host_paths": copied,
    }


def run_live_rehearsal(
    *,
    n_minus_1_image: str,
    candidate_image: str,
    http_port: int,
    dr_http_port: int,
    scrubber: SecretScrubber,
) -> dict[str, object]:
    stack = IsolatedComposeStack(
        prefix="g06g09",
        http_port=http_port,
        app_image=n_minus_1_image,
        scrubber=scrubber,
    )
    dr_stack = IsolatedComposeStack(
        prefix="g09dr",
        http_port=dr_http_port,
        app_image=n_minus_1_image,
        scrubber=scrubber,
    )
    backup_tmp = pathlib.Path(tempfile.mkdtemp(prefix="cskh-b2-backup-"))
    try:
        # Step 1: Boot N-1 (`v15.123.1`) and seed full P1/P2/P3 dataset
        stack.boot_and_bootstrap_site(install_site=True)
        stack.seed_standard_actors_and_teams()
        seeded = stack.exec_site_python(SEED_N_MINUS_1_PY)
        camp_id = str(seeded["campaign_id"])
        month_key = str(seeded["month_key"])
        open_wi = str(seeded["open_work_item_staff_a"])

        snap_before = stack.exec_site_python(
            SNAPSHOT_PY, {"campaign_id": camp_id, "month_key": month_key}
        )
        assert snap_before["outcome_no_answer"]["version"] >= 2, snap_before[
            "outcome_no_answer"
        ]
        assert snap_before["active_policy"]["version"] >= 2, snap_before[
            "active_policy"
        ]
        assert snap_before["distribution_reconcile"]["reconciled"] is True
        assert snap_before["kpi_monthly_reconcile"]["ok"] is True

        # Step 2: Pre-upgrade backup (`D10`)
        backup_meta = take_pre_upgrade_backup(stack, backup_tmp)

        # Step 3: Controlled upgrade `N-1 -> N` (`D01` + `D02`)
        scrubber.log("Stopping N-1 runtime workers/backend before candidate migration")
        stack.run_compose(["stop", *APP_RUNTIME_SERVICES])
        stack.set_app_image(candidate_image)

        # `D03` fail-closed pre-switch check: migrator against non-existent site MUST fail (rc != 0)
        bad_mig = stack.run_compose(
            ["run", "--rm", "-e", "SITE_NAME=nonexistent-abort.localhost", "migrator"],
            migrate_sites=True,
            check=False,
        )
        assert bad_mig.returncode != 0, (
            "Expected migrator to fail closed on missing site"
        )

        # Run configurator + migrator #1 (`D01`) + migrator #2 (`D02`)
        stack.run_compose(["run", "--rm", "configurator"])
        t0 = time.perf_counter()
        mig1 = stack.run_compose(["run", "--rm", "migrator"], migrate_sites=True)
        mig1_sec = round(time.perf_counter() - t0, 3)

        t1 = time.perf_counter()
        mig2 = stack.run_compose(["run", "--rm", "migrator"], migrate_sites=True)
        mig2_sec = round(time.perf_counter() - t1, 3)

        stack.run_compose(
            ["up", "-d", "--no-build", "--force-recreate", *APP_RUNTIME_SERVICES]
        )
        stack.wait_services_healthy(APP_RUNTIME_SERVICES, timeout_sec=180)
        stack.wait_http_ping(timeout_sec=90)

        snap_after_mig2 = stack.exec_site_python(
            SNAPSHOT_PY, {"campaign_id": camp_id, "month_key": month_key}
        )

        # Verify immutable fingerprints, row counts, and non-overwrite of custom Outcome/Policy (`D01`, `D02`)
        assert (
            snap_after_mig2["immutable_fingerprints"]
            == snap_before["immutable_fingerprints"]
        ), "Immutable history/attempt/correction fingerprints changed across migrate!"
        assert snap_after_mig2["counts"] == snap_before["counts"], (
            f"Row count drift after migrate x2: {snap_before['counts']} vs {snap_after_mig2['counts']}"
        )
        assert (
            snap_after_mig2["outcome_no_answer"] == snap_before["outcome_no_answer"]
        ), "Custom Outcome Code version/label was overwritten by migrate!"
        assert (
            snap_after_mig2["active_policy"]["version"]
            == snap_before["active_policy"]["version"]
        ), "Custom Policy version was overwritten by migrate!"
        assert snap_after_mig2["distribution_reconcile"]["reconciled"] is True
        assert snap_after_mig2["kpi_monthly_reconcile"]["ok"] is True

        schema_diff = classify_schema_diff(
            snap_before["schema_cols"], snap_after_mig2["schema_cols"]
        )
        assert schema_diff["tier3_image_rollback_safe"] is True, schema_diff

        # Live HTTP write on `N` (`staff_a` records a Contact Attempt after upgrade)
        staff_a = stack.make_actor("staff_a", "staff.a@dev.local")
        bod = stack.make_actor("bod", "bod.viewer@dev.local")
        admin = stack.make_actor("admin", "crm.admin@dev.local")

        ict = dt.timezone(dt.timedelta(hours=7))
        due_iso = (dt.datetime.now(ict) + dt.timedelta(days=2)).strftime(
            "%Y-%m-%d 14:00:00"
        )
        st_att_n, _, body_att_n = staff_a.post_json(
            "/api/method/crm_cskh.api.workflow.record_attempt",
            {
                "work_item": open_wi,
                "channel": "zalo",
                "outcome_code": "message_sent_no_reply",
                "note": "Liên hệ trên bản N sau migrate",
                "followup_due_at": due_iso,
                "followup_title": "Theo dõi phản hồi Zalo",
                "idempotency_key": f"b2-post-upgrade-{uuid.uuid4().hex[:8]}",
            },
        )
        assert st_att_n == 200, (
            f"Post-upgrade record_attempt failed ({st_att_n}): {body_att_n}"
        )
        post_upgrade_attempt_id = unwrap_message(body_att_n)["attempt_id"]

        # Step 4: `G09` Tier 1 & Tier 2 Feature-Flag Rollback over Live HTTP
        # Tier 2: disable only P3 export (`cskh_p3_export_enabled = false`) while overview stays 200
        stack.exec_site_python(
            "from frappe.installer import update_site_config\n"
            "update_site_config('cskh_p3_export_enabled', False)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'tier2_export_disabled': True}))"
        )
        st_ov_t2, _, _ = bod.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
            {"period_type": "monthly", "period_key": month_key},
        )
        st_exp_t2, _, _ = admin.post_json(
            "/api/method/crm_cskh.api.p3_reporting.request_export",
            {
                "report_type": "executive_overview",
                "period_type": "monthly",
                "period_key": month_key,
            },
        )
        assert st_ov_t2 == 200, (
            f"Expected overview 200 during Tier 2 export rollback, got {st_ov_t2}"
        )
        assert st_exp_t2 in (403, 409, 503), (
            f"Expected export blocked during Tier 2 rollback, got {st_exp_t2}"
        )

        # Tier 1: disable full P3 (`cskh_p3_enabled = false`) -> P3 page 403, P3 API 503, P1/P2 200!
        stack.exec_site_python(
            "from frappe.installer import update_site_config\n"
            "update_site_config('cskh_p3_enabled', False)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'tier1_p3_disabled': True}))"
        )
        st_p3_page, _, _ = bod.raw_request("GET", "/cskh/quality-overview")
        st_ov_t1, _, _ = bod.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
            {"period_type": "monthly", "period_key": month_key},
        )
        st_my_t1, _, _ = staff_a.get_json(
            "/api/method/crm_cskh.api.customers.my_list", {"page": 1, "page_size": 10}
        )
        st_att_t1, _, body_att_t1 = staff_a.post_json(
            "/api/method/crm_cskh.api.workflow.record_attempt",
            {
                "work_item": open_wi,
                "channel": "sms",
                "outcome_code": "message_sent_no_reply",
                "note": "Ghi nhận liên hệ P2 khi cờ P3 đang tắt",
                "idempotency_key": f"b2-tier1-att-{uuid.uuid4().hex[:8]}",
            },
        )
        assert st_p3_page == 403, (
            f"Expected /cskh/quality-overview 403 when P3 disabled, got {st_p3_page}"
        )
        assert st_ov_t1 == 503, (
            f"Expected get_executive_overview 503 when P3 disabled, got {st_ov_t1}"
        )
        assert st_my_t1 == 200 and st_att_t1 == 200, (st_my_t1, st_att_t1, body_att_t1)

        # Re-enable P3 flags first so subsequent `frappe.init()` loads `cskh_p3_enabled=true` in `frappe.conf`
        stack.exec_site_python(
            "from frappe.installer import update_site_config\n"
            "update_site_config('cskh_p3_enabled', True)\n"
            "update_site_config('cskh_p3_export_enabled', True)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'reenabled_flags': True}))"
        )
        reenabled = stack.exec_site_python(
            "import time\n"
            "from crm_cskh.jobs.recompute_kpi import drain_dirty_kpi_scopes, run_single_period_recompute\n"
            "from crm_cskh.jobs.reconcile_metrics import reconcile_published_period\n"
            "from crm_cskh.services.errors import RecomputeInProgress\n"
            "frappe.conf['cskh_p3_enabled'] = True\n"
            "frappe.conf['cskh_p3_export_enabled'] = True\n"
            "drained = {}\n"
            "for _ in range(12):\n"
            "    try:\n"
            "        drained = drain_dirty_kpi_scopes(max_scopes=20)\n"
            "        if not drained.get('runs'):\n"
            "            run_single_period_recompute(period_type='monthly', period_key=_IN['month_key'], actor='Administrator')\n"
            "        break\n"
            "    except RecomputeInProgress:\n"
            "        time.sleep(1.5)\n"
            "rec = reconcile_published_period(period_type='monthly', period_key=_IN['month_key'])\n"
            "print(json.dumps({'drained': drained, 'reconcile': rec}, default=str))",
            {"month_key": month_key},
        )
        assert reenabled["reconcile"]["ok"] is True, reenabled

        # Step 5: `G09` / `D13` Tier 3 Application Image Rollback (`N -> N-1` WITHOUT DB downgrade)
        scrubber.log(
            "Executing Tier 3 Application Image rollback N -> N-1 without touching MariaDB"
        )
        stack.set_app_image(n_minus_1_image)
        stack.run_compose(
            ["up", "-d", "--no-build", "--force-recreate", *APP_RUNTIME_SERVICES]
        )
        stack.wait_services_healthy(APP_RUNTIME_SERVICES, timeout_sec=180)
        stack.wait_http_ping(timeout_sec=90)

        tier3_check = stack.exec_site_python(
            "import frappe\n"
            "from crm_cskh.services import distribution as dist_svc\n"
            "from crm_cskh.jobs.reconcile_metrics import reconcile_published_period\n"
            "frappe.clear_cache()\n"
            "att_exists = bool(frappe.db.exists('CSKH Contact Attempt', _IN['post_att']))\n"
            "d_rec = dist_svc.reconcile(_IN['campaign_id'])\n"
            "d_ok = (\n"
            "  int(d_rec.get('active_work_items', -1)) == int(d_rec.get('active_claims', -2))\n"
            "  and int(d_rec.get('active_claims', -2)) == sum(int(v) for v in (d_rec.get('current_owners') or {}).values())\n"
            ")\n"
            "k_rec = reconcile_published_period(period_type='monthly', period_key=_IN['month_key'])\n"
            "print(json.dumps({\n"
            "  'frappe_version': getattr(frappe, '__version__', 'unknown'),\n"
            "  'post_upgrade_attempt_preserved_on_n_minus_1': att_exists,\n"
            "  'total_attempts_on_n_minus_1': frappe.db.count('CSKH Contact Attempt'),\n"
            "  'distribution_reconciled': d_ok,\n"
            "  'kpi_reconciled': bool(k_rec.get('ok')),\n"
            "}, default=str))",
            {
                "post_att": post_upgrade_attempt_id,
                "campaign_id": camp_id,
                "month_key": month_key,
            },
        )
        assert tier3_check["post_upgrade_attempt_preserved_on_n_minus_1"] is True, (
            tier3_check
        )
        assert tier3_check["distribution_reconciled"] is True, tier3_check
        assert tier3_check["kpi_reconciled"] is True, tier3_check

        st_t3_my, _, _ = staff_a.get_json(
            "/api/method/crm_cskh.api.customers.my_list", {"page": 1, "page_size": 10}
        )
        st_t3_ov, _, _ = bod.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
            {"period_type": "monthly", "period_key": month_key},
        )
        assert st_t3_my == 200 and st_t3_ov == 200, (st_t3_my, st_t3_ov)

        # Compute post-backup delta before Tier 4 restore accounting check
        delta_audit = stack.exec_site_python(
            "pre = set(_IN['pre_ids'])\n"
            "rows = [r for r in frappe.db.sql(\n"
            "  'SELECT name, channel, outcome_code FROM `tabCSKH Contact Attempt` WHERE creation >= %s ORDER BY creation',\n"
            "  (_IN['since_site_ts'],), as_dict=True\n"
            ") if r['name'] not in pre]\n"
            "print(json.dumps({'post_backup_attempts_count': len(rows), 'attempt_ids': [r['name'] for r in rows]}))",
            {
                "since_site_ts": backup_meta["backup_site_ts"],
                "pre_ids": backup_meta["pre_attempt_ids"],
            },
        )
        assert delta_audit["post_backup_attempts_count"] == 2, delta_audit

        # Step 6: `D10–D12` Tier 4 Full DR Restore on isolated target site (`dr_stack`)
        # Tear down `stack` first so Traefik Docker provider never sees duplicate `frontend-http` routers across two concurrent stacks
        stack.teardown()
        scrubber.log(
            "Executing Tier 4 full pre-upgrade backup restore rehearsal on isolated DR stack"
        )
        dr_stack.boot_and_bootstrap_site(install_site=True)
        dr_cid = dr_stack.get_container_id("backend")
        for key in ("db_gz", "priv_tar", "pub_tar", "site_config"):
            hpath = backup_meta["host_paths"][key]
            subprocess.run(
                ["docker", "cp", hpath, f"{dr_cid}:/tmp/{pathlib.Path(hpath).name}"],
                check=True,
                capture_output=True,
            )

        restore_code = r"""
import json, subprocess, sys
p = json.loads(sys.stdin.read())
site = p["site"]
src_cfg = json.load(open(f"/tmp/{p['cfg_name']}", encoding="utf-8"))
dst_cfg_path = f"/home/frappe/frappe-bench/sites/{site}/site_config.json"
dst_cfg = json.load(open(dst_cfg_path, encoding="utf-8"))
if "encryption_key" in src_cfg:
    dst_cfg["encryption_key"] = src_cfg["encryption_key"]
with open(dst_cfg_path, "w", encoding="utf-8") as fh:
    json.dump(dst_cfg, fh, indent=2)

subprocess.check_call(
    [
        "bench", "--site", site, "--force", "restore", f"/tmp/{p['db_name']}",
        "--with-private-files", f"/tmp/{p['priv_name']}",
        "--with-public-files", f"/tmp/{p['pub_name']}",
        "--db-root-password", p["db_root_pw"],
        "--admin-password", p["admin_pw"],
    ],
    cwd="/home/frappe/frappe-bench",
)
print(json.dumps({"restored_site": site}))
"""
        dr_stack.run_compose(
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                restore_code,
            ],
            input_text=json.dumps(
                {
                    "site": dr_stack.site_name,
                    "db_name": pathlib.Path(backup_meta["host_paths"]["db_gz"]).name,
                    "priv_name": pathlib.Path(
                        backup_meta["host_paths"]["priv_tar"]
                    ).name,
                    "pub_name": pathlib.Path(backup_meta["host_paths"]["pub_tar"]).name,
                    "cfg_name": pathlib.Path(
                        backup_meta["host_paths"]["site_config"]
                    ).name,
                    "db_root_pw": dr_stack.db_root_pw,
                    "admin_pw": dr_stack.admin_pw,
                }
            ),
        )
        dr_stack.run_compose(["run", "--rm", "migrator"], migrate_sites=True)
        dr_snap = dr_stack.exec_site_python(
            SNAPSHOT_PY, {"campaign_id": camp_id, "month_key": month_key}
        )
        assert dr_snap["counts"] == snap_before["counts"], (
            f"DR restored counts {dr_snap['counts']} != pre-upgrade {snap_before['counts']}"
        )
        assert (
            dr_snap["immutable_fingerprints"] == snap_before["immutable_fingerprints"]
        )

        return {
            "schema_version": "cskh-p4-batch2-g06-g09-v1",
            "status": "PASS",
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "gates_verified": ["G06", "G09"],
            "matrix_items_verified": ["D01", "D02", "D03", "D10", "D11", "D12", "D13"],
            "n_minus_1_provenance": {
                "git_commit_sha": BASELINE_MAIN_COMMIT_SHA,
                "image_ref": n_minus_1_image,
                "frappe_version": snap_before["frappe_version"],
            },
            "candidate_n_provenance": {
                "git_commit_sha": LOCKED_BATCH1_COMMIT_SHA,
                "image_ref": candidate_image,
                "frappe_version": snap_after_mig2["frappe_version"],
            },
            "g06_clone_migration_and_idempotency": {
                "blg_p4_mig_01_audit": snap_after_mig2["blg_p4_mig_01"],
                "migrator_run_1_duration_sec": mig1_sec,
                "migrator_run_2_duration_sec": mig2_sec,
                "migrator_run_1_exit_code": mig1.returncode,
                "migrator_run_2_exit_code": mig2.returncode,
                "failed_migration_guard_d03_exit_code": bad_mig.returncode,
                "schema_compatibility": schema_diff,
                "counts_before": snap_before["counts"],
                "counts_after_migrate_x2": snap_after_mig2["counts"],
                "immutable_fingerprints_identical": True,
                "custom_outcome_preserved": snap_after_mig2["outcome_no_answer"],
                "custom_policy_preserved": snap_after_mig2["active_policy"],
                "distribution_reconcile": snap_after_mig2["distribution_reconcile"],
                "kpi_monthly_reconcile": snap_after_mig2["kpi_monthly_reconcile"],
            },
            "g09_rollback_rehearsals": {
                "tier2_export_flag_rollback": {
                    "overview_http_status": st_ov_t2,
                    "export_blocked_http_status": st_exp_t2,
                },
                "tier1_master_p3_flag_rollback": {
                    "p3_page_http_status": st_p3_page,
                    "p3_overview_http_status": st_ov_t1,
                    "p1_my_customers_http_status": st_my_t1,
                    "p2_record_attempt_http_status": st_att_t1,
                    "dirty_scope_reenable_reconcile_ok": bool(
                        reenabled["reconcile"]["ok"]
                    ),
                },
                "tier3_image_rollback_n_to_n_minus_1": {
                    **tier3_check,
                    "http_my_customers_status": st_t3_my,
                    "http_executive_overview_status": st_t3_ov,
                },
                "tier4_dr_full_restore_and_delta_accounting": {
                    "backup_files_bytes": backup_meta["files_present"],
                    "restored_counts_match_pre_upgrade": True,
                    "restored_fingerprints_match_pre_upgrade": True,
                    "audited_post_backup_delta_window": delta_audit,
                },
            },
            "forward_fix_vs_restore_contract": forward_fix_vs_restore_contract(),
        }
    finally:
        dr_stack.teardown()
        stack.teardown()
        shutil.rmtree(backup_tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-minus-1-image", default="cskh-n-minus-1:local")
    parser.add_argument("--candidate-image", default=LOCKED_BATCH1_IMAGE_DIGEST)
    parser.add_argument("--http-port", type=int, default=18910)
    parser.add_argument("--dr-http-port", type=int, default=18911)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--report-out", type=pathlib.Path, required=True)
    parser.add_argument("--log-out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    scrubber = SecretScrubber()
    if args.plan_only:
        scrubber.write_json_and_log(
            build_plan_only_report(args.n_minus_1_image, args.candidate_image),
            args.report_out,
            args.log_out,
        )
        return 0

    try:
        report = run_live_rehearsal(
            n_minus_1_image=args.n_minus_1_image,
            candidate_image=args.candidate_image,
            http_port=args.http_port,
            dr_http_port=args.dr_http_port,
            scrubber=scrubber,
        )
    except Exception as exc:
        scrubber.log(f"FATAL: {exc}\n{traceback.format_exc()}")
        scrubber.write_json_and_log(
            {
                "schema_version": "cskh-p4-batch2-g06-g09-v1",
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
