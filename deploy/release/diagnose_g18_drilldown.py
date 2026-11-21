#!/usr/bin/env python3
"""G18 `get_metric_drilldown` root-cause diagnostic & before/after verification harness.

Collects mandatory P4 G18 diagnostic & verification telemetry on an immutable `@sha256` image
digest against the exact S1 dataset (`50k` Leads, `75k` Work Items, `250k` Contact Attempts,
`150k` SLA Obligations, `75k` Assignment History, `40` active staff across `4` teams):
1. Table cardinalities and `SHOW INDEX FROM` across all reporting & workflow tables.
2. Per-query timings, `EXPLAIN`, `ANALYZE FORMAT=JSON` (`r_rows`, `r_total_time_ms`, `key`,
   `Extra`), MariaDB `Handler_read_*` / `Created_tmp_*` / `Sort_rows`, Python CPU vs SQL time,
   and `tracemalloc` heap peak across:
   - `daily` campaign `CONNECTED_RATE` (`page=1`),
   - `monthly` `250k` company `CONNECTED_RATE` across shallow & deep pagination
     (`page=1`, `page=10`, `page=25`, `page=26`, `page=500`),
   - `dirty_pending=True` active-period state,
   - `scope_type=team` (`Team Leader` role),
   - `FIRST_ATTEMPT_SLA_RATE` (`150k` SLA Obligations),
   - `CONTACT_ATTEMPT_COUNT` (`250k` Contact Attempts),
   - `ELIGIBLE_COVERAGE` (`75k` Assignment History intervals).
3. Live HTTP concurrency & Gunicorn worker/thread saturation at `c=12` (`240` requests) and
   `c=40` (`160` requests, `30.0s` client timeout), reporting exact `timeout_count`,
   `client_error_count`, `http_4xx_count`, `http_5xx_count`, `http_error_count`,
   `throughput_rps`, `p50_ms`, `p95_ms`, `p99_ms`, Gunicorn `R`/`S` thread saturation,
   CPU%, RSS MiB, and MariaDB `Threads_running`.
4. Post-optimization correctness reconciliation (`cskh-metrics-v2` snapshot numerator/denominator
   parity, shallow/deep pagination disjointness & total consistency, cross-access `403` RBAC,
   and `0` raw PII/cookie leakage).
5. Supports both `s1_canonical_4x4` (exact S1 baseline topology for apples-to-apples comparison)
   and `staging_vps_capped` (`deploy/compose/env/staging.env.example` cgroup CPU/RAM caps +
   fail-closed live-resource protection guard).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from benchmark_p4_isolated import (
    LOCK_FILE,
    VERIFY_COMPOSE_FILE,
    _collect_docker_stats,
    _collect_queue_and_db_telemetry,
    _compose_files_arg,
    _http_call,
    _login_session,
    enforce_isolated_benchmark_target,
    summarize_latencies_ms,
)
from g18_resource_profiles import (
    get_g18_resource_profile_spec,
    render_verify_compose_override_yaml,
)

S1_CANONICAL_OVERRIDE_YAML = render_verify_compose_override_yaml("s1_canonical_4x4")
STAGING_VPS_CAPPED_OVERRIDE_YAML = render_verify_compose_override_yaml(
    "staging_vps_capped"
)
STAGING_VPS_CAPPED_B_PLUS_OVERRIDE_YAML = render_verify_compose_override_yaml(
    "staging_vps_capped_b_plus_2_5vcpu"
)

RESOURCE_PROFILES: dict[str, dict[str, object]] = {
    "s1_canonical_4x4": {
        "description": str(
            get_g18_resource_profile_spec("s1_canonical_4x4")["description"]
        ),
        "override_yaml": S1_CANONICAL_OVERRIDE_YAML,
        "gunicorn_workers": int(
            get_g18_resource_profile_spec("s1_canonical_4x4")["gunicorn_workers"]
        ),
        "gunicorn_threads": int(
            get_g18_resource_profile_spec("s1_canonical_4x4")["gunicorn_threads"]
        ),
        "cgroup_capped": True,
        "config_spec_sha256": str(
            get_g18_resource_profile_spec("s1_canonical_4x4")["config_spec_sha256"]
        ),
    },
    "staging_vps_capped": {
        "description": str(
            get_g18_resource_profile_spec("staging_vps_capped")["description"]
        ),
        "override_yaml": STAGING_VPS_CAPPED_OVERRIDE_YAML,
        "gunicorn_workers": int(
            get_g18_resource_profile_spec("staging_vps_capped")["gunicorn_workers"]
        ),
        "gunicorn_threads": int(
            get_g18_resource_profile_spec("staging_vps_capped")["gunicorn_threads"]
        ),
        "cgroup_capped": True,
        "config_spec_sha256": str(
            get_g18_resource_profile_spec("staging_vps_capped")["config_spec_sha256"]
        ),
    },
    "staging_vps_capped_b_plus_2_5vcpu": {
        "description": str(
            get_g18_resource_profile_spec("staging_vps_capped_b_plus_2_5vcpu")[
                "description"
            ]
        ),
        "override_yaml": STAGING_VPS_CAPPED_B_PLUS_OVERRIDE_YAML,
        "gunicorn_workers": int(
            get_g18_resource_profile_spec("staging_vps_capped_b_plus_2_5vcpu")[
                "gunicorn_workers"
            ]
        ),
        "gunicorn_threads": int(
            get_g18_resource_profile_spec("staging_vps_capped_b_plus_2_5vcpu")[
                "gunicorn_threads"
            ]
        ),
        "cgroup_capped": True,
        "config_spec_sha256": str(
            get_g18_resource_profile_spec("staging_vps_capped_b_plus_2_5vcpu")[
                "config_spec_sha256"
            ]
        ),
    },
}

FORBIDDEN_CONTAINER_PREFIXES = (
    "crm-cskh-prod",
    "cskh-prod",
    "hrpartner",
    "frappe_docker-backend",
)


def read_host_resource_snapshot() -> dict[str, object]:
    """Read Linux/host memory, load average, CPU count, and free disk space."""
    cpu_count = os.cpu_count() or 1
    mem_total_mib = 0.0
    mem_avail_mib = 0.0
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for ln in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("MemTotal:"):
                mem_total_mib = round(int(ln.split()[1]) / 1024.0, 1)
            elif ln.startswith("MemAvailable:"):
                mem_avail_mib = round(int(ln.split()[1]) / 1024.0, 1)
    load_1m = 0.0
    if hasattr(os, "getloadavg"):
        try:
            load_1m = round(os.getloadavg()[0], 3)
        except OSError:
            load_1m = 0.0
    disk = shutil.disk_usage(Path.cwd())
    disk_free_gib = round(disk.free / (1024.0**3), 2)
    return {
        "cpu_count": cpu_count,
        "mem_total_mib": mem_total_mib,
        "mem_available_mib": mem_avail_mib,
        "loadavg_1m": load_1m,
        "disk_free_gib": disk_free_gib,
    }


def enforce_live_resource_headroom(
    *,
    snapshot: dict[str, object],
    running_container_names: list[str],
    min_mem_available_mib: float = 3072.0,
    max_load_per_cpu: float = 0.85,
    min_disk_free_gib: float = 8.0,
    forbid_cohosted_live_containers: bool = False,
) -> dict[str, object]:
    """Fail-closed preflight protecting live services from resource starvation."""
    live_hits = [
        name
        for name in running_container_names
        if any(tok in name.lower() for tok in FORBIDDEN_CONTAINER_PREFIXES)
    ]
    if forbid_cohosted_live_containers and live_hits:
        raise ValueError(
            f"Refusing benchmark while live/production containers are present on host: {live_hits}"
        )

    mem_avail = float(snapshot.get("mem_available_mib") or 0.0)
    mem_total = float(snapshot.get("mem_total_mib") or 0.0)
    if mem_total > 0.0 and mem_avail < min_mem_available_mib:
        raise ValueError(
            f"Insufficient host MemAvailable ({mem_avail} MiB < {min_mem_available_mib} MiB floor); "
            "aborting to protect host/live stability"
        )

    cpu_count = max(1, int(snapshot.get("cpu_count") or 1))
    load_1m = float(snapshot.get("loadavg_1m") or 0.0)
    if load_1m > 0.0 and (load_1m / cpu_count) > max_load_per_cpu:
        raise ValueError(
            f"Host 1m loadavg too high ({load_1m} across {cpu_count} CPUs > {max_load_per_cpu:.2f}/CPU); "
            "aborting to protect host/live resources"
        )

    disk_free = float(snapshot.get("disk_free_gib") or 0.0)
    if disk_free < min_disk_free_gib:
        raise ValueError(
            f"Insufficient free disk ({disk_free} GiB < {min_disk_free_gib} GiB floor)"
        )

    return {
        "status": "PASS",
        "cohosted_live_containers_detected": live_hits,
        "min_mem_available_mib_floor": min_mem_available_mib,
        "max_load_per_cpu_ceiling": max_load_per_cpu,
        "min_disk_free_gib_floor": min_disk_free_gib,
        "host_snapshot_at_preflight": snapshot,
    }


def classify_http_outcomes(
    outcomes: list[tuple[float, int]],
    *,
    elapsed_sec: float,
    timeout_sec: float,
) -> dict[str, object]:
    """Compute complete HTTP error/timeout/client-error/latency summary."""
    lats = [lat for lat, _ in outcomes]
    timeout_count = 0
    client_conn_error_count = 0
    http_4xx_count = 0
    http_5xx_count = 0
    error_count = 0
    status_counts: dict[str, int] = {}

    for _, st in outcomes:
        if st == 0:
            key = "timeout"
            timeout_count += 1
            error_count += 1
        elif st < 0:
            key = "client_conn_error"
            client_conn_error_count += 1
            error_count += 1
        else:
            key = str(st)
            if 400 <= st < 500:
                http_4xx_count += 1
                error_count += 1
            elif st >= 500:
                http_5xx_count += 1
                error_count += 1
            elif not (200 <= st < 400):
                error_count += 1
        status_counts[key] = status_counts.get(key, 0) + 1

    base = summarize_latencies_ms(
        lats, error_count=error_count, elapsed_sec=elapsed_sec
    )
    total = int(base["requests_total"])
    return {
        **base,
        "client_timeout_threshold_sec": timeout_sec,
        "timeout_count": timeout_count,
        "timeout_rate_pct": round((timeout_count / total * 100.0) if total else 0.0, 4),
        "client_error_count": timeout_count + client_conn_error_count,
        "client_conn_error_count": client_conn_error_count,
        "http_4xx_count": http_4xx_count,
        "http_5xx_count": http_5xx_count,
        "http_error_count": http_4xx_count + http_5xx_count,
        "status_counts": status_counts,
    }


def scrub_sensitive_tokens(text: str) -> str:
    """Guarantee zero raw `sid` or CSRF tokens in serialized diagnostic reports."""
    patterns = (
        (re.compile(r"(?im)(set-cookie:\s*)[^\r\n]+"), r"\1[REDACTED]"),
        (re.compile(r"(?im)(cookie:\s*)[^\r\n]+"), r"\1[REDACTED]"),
        (
            re.compile(r"(?i)(x-frappe-csrf-token[\"'=:\s]+)[^\"'&\s<,}]+"),
            r"\1[REDACTED]",
        ),
        (re.compile(r"(?i)(\bsid=)[^;\s\"'&]+"), r"\1[REDACTED]"),
    )
    out = text
    for pat, rep in patterns:
        out = pat.sub(rep, out)
    return out


IN_CONTAINER_DIAG_SCRIPT = r"""
import gc
import json
import os
import platform
import re
import time
import tracemalloc
from datetime import datetime, timedelta

import frappe
from frappe.utils.password import update_password

from crm_cskh.api import p3_reporting
from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services import runtime_cache
from crm_cskh.services.attribution import AUTHORITATIVE
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import (
    _flush_pending_dirty_events,
    execute_kpi_recompute,
    schedule_post_commit_dirty_invalidation,
)


def _bulk_insert(table, columns, rows, chunk_size=2500):
    if not rows:
        return
    col_sql = ", ".join(f"`{c}`" for c in columns)
    row_ph = "(" + ", ".join(["%s"] * len(columns)) + ")"
    for offset in range(0, len(rows), chunk_size):
        batch = rows[offset : offset + chunk_size]
        values_sql = ", ".join([row_ph] * len(batch))
        flat = [v for tup in batch for v in tup]
        frappe.db.sql(f"INSERT INTO `{table}` ({col_sql}) VALUES {values_sql}", tuple(flat))
    frappe.db.commit()


def _ensure_user(email, full_name, roles, pw):
    if not frappe.db.exists("User", email):
        u = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": full_name,
                "enabled": 1,
                "language": "vi",
                "send_welcome_email": 0,
                "user_type": "System User",
            }
        )
        u.insert(ignore_permissions=True)
        u.add_roles(*roles)
    update_password(email, pw)
    return email


site = os.environ["BENCH_SITE"]
actor_pw = os.environ["BENCH_ACTOR_PW"]
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
frappe.set_user("Administrator")

t_seed0 = time.perf_counter()
ensure_p2_indexes()
ensure_p2_seeds()
ensure_p3_indexes()
ensure_p3_seeds()
frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
if frappe.db.exists("DocType", "FCRM Settings"):
    frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

for role_name in ("CSKH BOD Viewer", "CSKH CRM Admin", "Sales Manager", "Sales User"):
    if not frappe.db.exists("Role", role_name):
        frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(ignore_permissions=True)

bod_user = _ensure_user("bod.bench@dev.local", "BOD Bench", ["CSKH BOD Viewer"], actor_pw)
admin_user = _ensure_user("admin.bench@dev.local", "Admin Bench", ["CSKH CRM Admin"], actor_pw)

team_nodes = []
leader_users = []
staff_users = []
staff_team_map = {}

for t_idx in range(1, 5):
    leader_email = _ensure_user(
        f"leader.bench.{t_idx}@dev.local",
        f"Leader Bench {t_idx}",
        ["Sales Manager", "Sales User"],
        actor_pw,
    )
    leader_users.append(leader_email)
    t_doc = frappe.get_doc(
        {
            "doctype": "CRM Sales Hierarchy",
            "full_name": f"Team S1 {t_idx}",
            "is_group": 1,
            "user": leader_email,
        }
    )
    t_doc.insert(ignore_permissions=True)
    t_node = t_doc.name
    team_nodes.append(t_node)
    for s_offset in range(1, 11):
        s_num = (t_idx - 1) * 10 + s_offset
        s_email = _ensure_user(
            f"staff.s1.{s_num:02d}@dev.local",
            f"Staff S1 {s_num:02d}",
            ["Sales User"],
            actor_pw,
        )
        staff_users.append(s_email)
        staff_team_map[s_email] = t_node
        frappe.get_doc(
            {
                "doctype": "CRM Sales Hierarchy",
                "full_name": f"Staff S1 {s_num:02d}",
                "is_group": 0,
                "user": s_email,
                "reports_to": t_node,
            }
        ).insert(ignore_permissions=True)

frappe.db.sql("UPDATE `tabCSKH Team Membership History` SET effective_from = '2026-01-01 00:00:00'")

campaigns = []
for c_idx in range(1, 6):
    t_scope = team_nodes[c_idx - 1] if c_idx <= 4 else None
    c_doc = frappe.get_doc(
        {
            "doctype": "CSKH Campaign",
            "code": f"S1-CAMP-{c_idx:02d}",
            "title": f"Chiến dịch S1 {c_idx:02d}",
            "status": "Active",
            "started_at": "2026-01-01 00:00:00",
            "ended_at": "2026-12-31 23:59:59",
            "team_scope": t_scope,
            "rule_version": 1,
        }
    )
    c_doc.insert(ignore_permissions=True)
    campaigns.append(c_doc.name)
frappe.db.commit()

lead_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "naming_series", "first_name", "lead_name", "mobile_no", "status", "lead_owner", "cskh_do_not_contact",
]
ii_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "normalized_phone", "canonical_lead",
]
wi_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "lead", "campaign", "status", "assigned_to", "assigned_at", "owner_at_close",
    "closed_by", "closed_at", "attempt_count", "connected_attempt_count",
    "first_attempt_at", "last_attempt_at", "last_outcome_code", "last_outcome_label", "revision",
]
claim_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "lead", "campaign", "work_item",
]
share_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "user", "share_doctype", "share_name", "read", "write", "share", "submit", "everyone", "notify_by_email",
]
todo_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "allocated_to", "reference_type", "reference_name", "description", "priority", "status", "assigned_by",
]
ah_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "work_item", "action", "from_user", "to_user", "actor", "event_at",
    "ownership_start", "ownership_end", "event_id", "team_at_assignment", "attribution_status", "reason",
]
att_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "work_item", "lead", "campaign", "ownership_event", "owner_at_attempt", "performed_by",
    "occurred_at", "recorded_at", "channel", "outcome_code", "outcome_label_snapshot",
    "outcome_group_snapshot", "outcome_version_snapshot", "contact_attempt_snapshot",
    "connected_snapshot", "interested_snapshot", "terminal_snapshot", "dnc_snapshot",
    "note", "verification_status", "policy_name_snapshot", "policy_version_snapshot",
    "idempotency_key", "payload_hash", "work_item_revision",
]
sla_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "obligation_type", "work_item", "lead", "campaign", "interval_event", "owner_user",
    "team_at_obligation", "attribution_status", "status", "informational_only",
    "is_recovery_obligation", "reschedule_seq", "started_at", "original_due_at",
    "effective_due_at", "is_paused", "fulfilled_at", "fulfilled_by_attempt",
    "breached_at", "closed_at", "evaluation_anchor_at", "policy_name_snapshot",
    "policy_version_snapshot", "idempotency_key",
]

for l_start in range(0, 50_000, 10_000):
    l_rows, ii_rows = [], []
    for li in range(l_start, l_start + 10_000):
        has_open_wi = (24_200 <= li < 25_000) or (49_200 <= li < 50_000)
        day_num = 27 if has_open_wi else ((li % 26) + 1)
        base_ts = datetime(2026, 10, day_num, 8 + (li % 4), (li * 7) % 50, 0)
        owner_u = staff_users[li % 40]
        lead_id = f"S1-LEAD-{li:06d}"
        l_rows.append(
            (
                lead_id, base_ts, base_ts, "Administrator", "Administrator", 0, 0,
                "CRM-LEAD-", f"Ứng viên S1 {li:06d}", f"Ứng viên S1 {li:06d}", f"091{li:07d}",
                "Contacted" if has_open_wi else "Converted", owner_u if has_open_wi else None, 0,
            )
        )
        ii_rows.append(
            (
                f"S1-II-{li:06d}", base_ts, base_ts, "Administrator", "Administrator", 0, 0,
                f"+8491{li:07d}", lead_id,
            )
        )
    _bulk_insert("tabCRM Lead", lead_cols, l_rows)
    _bulk_insert("tabCSKH Identity Index", ii_cols, ii_rows)

claim_rows_all, share_rows_all, todo_rows_all = [], [], []
for w_start in range(0, 75_000, 7_500):
    w_end = min(w_start + 7_500, 75_000)
    wi_rows, ah_rows, att_rows, sla_rows = [], [], [], []
    for i in range(w_start, w_end):
        lead_idx = i if i < 50_000 else (i - 50_000)
        lead_id = f"S1-LEAD-{lead_idx:06d}"
        s_idx = lead_idx % 40
        owner_u = staff_users[s_idx]
        team_u = staff_team_map[owner_u]
        primary_camp_idx = s_idx // 10
        camp = campaigns[primary_camp_idx] if i < 50_000 else campaigns[4]

        is_open = (49_200 <= i < 50_000) or (74_200 <= i < 75_000)
        is_closed = not is_open
        day_num = 27 if is_open else ((i % 26) + 1)
        hour_num = 8 + (i % 8)
        if hour_num >= 12:
            hour_num += 1
        base_ts = datetime(2026, 10, day_num, hour_num, (i * 5) % 40, 0)
        wi_id = f"S1-WI-{i:06d}"
        if is_open:
            claim_rows_all.append(
                (
                    f"S1-CLM-{i:06d}", base_ts, base_ts, "Administrator", "Administrator", 0, 0,
                    lead_id, camp, wi_id,
                )
            )
            share_rows_all.append(
                (
                    f"S1-SHR-{i:06d}", base_ts, base_ts, "Administrator", "Administrator", 0, 0,
                    owner_u, "CRM Lead", lead_id, 1, 1, 0, 0, 0, 0,
                )
            )
            todo_rows_all.append(
                (
                    f"S1-TODO-{i:06d}", base_ts, base_ts, "Administrator", "Administrator", 0, 0,
                    owner_u, "CRM Lead", lead_id, f"CSKH assignment ({wi_id})", "Medium", "Open", "Administrator",
                )
            )

        ah_id = f"S1-AH-{i:06d}-1"
        close_ts = base_ts + timedelta(hours=2) if is_closed else None
        ah_rows.append(
            (
                ah_id, base_ts, close_ts or base_ts, "Administrator", "Administrator", 0, 0,
                wi_id, "assign", None, owner_u, "Administrator", base_ts, base_ts, close_ts,
                f"s1-evt-{i:06d}-1", team_u, AUTHORITATIVE, "Phân bổ S1",
            )
        )

        num_atts = 4 if i < 25_000 else 3
        att_base_ts = base_ts + timedelta(minutes=12 if (i % 5 != 0) else 42)
        first_att_id = f"S1-ATT-{i:06d}-1"
        last_att_ts = att_base_ts
        conn_cnt = 0
        last_code = "no_answer"
        last_label = "Không nghe máy"

        for a_seq in range(1, num_atts + 1):
            att_id = f"S1-ATT-{i:06d}-{a_seq}"
            occ_ts = att_base_ts + timedelta(minutes=(a_seq - 1) * 15)
            last_att_ts = occ_ts
            if a_seq == num_atts and (i % 4 != 0):
                ocode, olabel, ogroup, is_conn, is_int = (
                    "connected_interested", "Đã trao đổi — Có quan tâm", "positive", 1, 1
                )
                conn_cnt += 1
            elif a_seq == 1:
                ocode, olabel, ogroup, is_conn, is_int = (
                    "no_answer", "Không nghe máy", "unreached", 0, 0
                )
            else:
                ocode, olabel, ogroup, is_conn, is_int = (
                    "busy", "Máy bận", "unreached", 0, 0
                )
            last_code, last_label = ocode, olabel
            att_rows.append(
                (
                    att_id, occ_ts, occ_ts, owner_u, owner_u, 0, 0,
                    wi_id, lead_id, camp, ah_id, owner_u, owner_u, occ_ts, occ_ts,
                    "phone_call", ocode, olabel, ogroup, 1, 1, is_conn, is_int, 0, 0,
                    f"Ghi chú S1 {i:06d}-{a_seq}", "self_reported", "GLOBAL-DEFAULT", 1,
                    f"s1-idem-att-{i:06d}-{a_seq}", f"s1-hash-{i:06d}-{a_seq}", 2,
                )
            )

        wi_rows.append(
            (
                wi_id, base_ts, last_att_ts, "Administrator", "Administrator", 0, 0,
                lead_id, camp, "closed" if is_closed else "in_progress",
                None if is_closed else owner_u, base_ts,
                owner_u if is_closed else None, owner_u if is_closed else None,
                (last_att_ts + timedelta(minutes=5)) if is_closed else None,
                num_atts, conn_cnt, att_base_ts, last_att_ts, last_code, last_label, 2,
            )
        )

        due_1 = base_ts + timedelta(minutes=30)
        on_time_1 = att_base_ts <= due_1
        sla_rows.append(
            (
                f"S1-SLA-{i:06d}-FA", base_ts, att_base_ts, "Administrator", "Administrator", 0, 0,
                "FIRST_ATTEMPT", wi_id, lead_id, camp, ah_id, owner_u, team_u, AUTHORITATIVE,
                "fulfilled_on_time" if on_time_1 else "fulfilled_late", 0, 0, 0,
                base_ts, due_1, due_1, 0, att_base_ts, first_att_id,
                None if on_time_1 else due_1, None, att_base_ts, "GLOBAL-DEFAULT", 1, f"s1-sla-fa-{i:06d}",
            )
        )
        fu_due = att_base_ts + timedelta(minutes=35)
        fu_done = att_base_ts + timedelta(minutes=15)
        sla_rows.append(
            (
                f"S1-SLA-{i:06d}-FU", att_base_ts, fu_done, "Administrator", "Administrator", 0, 0,
                "FOLLOWUP", wi_id, lead_id, camp, ah_id, owner_u, team_u, AUTHORITATIVE,
                "fulfilled_on_time", 0, 0, 0, att_base_ts, fu_due, fu_due, 0, fu_done,
                f"S1-ATT-{i:06d}-2", None, None, fu_done, "GLOBAL-DEFAULT", 1, f"s1-sla-fu-{i:06d}",
            )
        )

    _bulk_insert("tabCSKH Work Item", wi_cols, wi_rows)
    _bulk_insert("tabCSKH Assignment History", ah_cols, ah_rows)
    _bulk_insert("tabCSKH Contact Attempt", att_cols, att_rows)
    _bulk_insert("tabCSKH SLA Obligation", sla_cols, sla_rows)

_bulk_insert("tabCSKH Active Claim", claim_cols, claim_rows_all)
_bulk_insert("tabDocShare", share_cols, share_rows_all)
_bulk_insert("tabToDo", todo_cols, todo_rows_all)

for tbl in (
    "tabCRM Lead",
    "tabCSKH Identity Index",
    "tabCSKH Work Item",
    "tabCSKH Assignment History",
    "tabCSKH Contact Attempt",
    "tabCSKH SLA Obligation",
):
    frappe.db.sql(f"ANALYZE TABLE `{tbl}`")
frappe.db.commit()

month_spec = resolve_period_spec("monthly", "2026-10")
execute_kpi_recompute(month_spec, run_mode="full", actor="Administrator")
day_spec = resolve_period_spec("daily", "2026-10-27")
execute_kpi_recompute(day_spec, run_mode="incremental", actor="Administrator")
frappe.db.sql("ANALYZE TABLE `tabCSKH KPI Snapshot`, `tabCSKH KPI Run`")
frappe.db.commit()
seed_elapsed_sec = round(time.perf_counter() - t_seed0, 2)

# 1. Collect table cardinalities & index metadata
monitored_tables = (
    "tabCSKH Assignment History",
    "tabCSKH Contact Attempt",
    "tabCSKH SLA Obligation",
    "tabCSKH Work Item",
    "tabCSKH KPI Snapshot",
    "tabCSKH Team Membership History",
    "tabCRM Lead",
)
table_and_index_inventory = {}
for tbl in monitored_tables:
    cnt = int(frappe.db.sql(f"SELECT COUNT(*) FROM `{tbl}`")[0][0])
    idxs = frappe.db.sql(f"SHOW INDEX FROM `{tbl}`", as_dict=True)
    grouped_idx = {}
    for row in idxs:
        kname = str(row.get("Key_name") or "")
        grouped_idx.setdefault(
            kname,
            {
                "non_unique": int(row.get("Non_unique") or 0),
                "columns": [],
                "cardinality_last_col": row.get("Cardinality"),
            },
        )
        grouped_idx[kname]["columns"].append(str(row.get("Column_name") or ""))
        grouped_idx[kname]["cardinality_last_col"] = row.get("Cardinality")
    table_and_index_inventory[tbl] = {
        "exact_row_count": cnt,
        "indexes": grouped_idx,
    }


def _session_status():
    rows = frappe.db.sql(
        "SHOW SESSION STATUS WHERE Variable_name IN ("
        "'Handler_read_first','Handler_read_key','Handler_read_next',"
        "'Handler_read_prev','Handler_read_rnd','Handler_read_rnd_next',"
        "'Created_tmp_tables','Created_tmp_disk_tables','Sort_rows','Sort_scan'"
        ")"
    )
    return {str(k): int(v or 0) for k, v in rows}


def _innodb_bp_status():
    rows = frappe.db.sql(
        "SHOW GLOBAL STATUS WHERE Variable_name IN ("
        "'Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads','Innodb_rows_read'"
        ")"
    )
    return {str(k): int(v or 0) for k, v in rows}


def _normalize_sql_shape(sql_str):
    s = " ".join(str(sql_str).split())
    s = re.sub(r"\(\s*%s(?:\s*,\s*%s){3,}\s*\)", "(%s, ...CHUNK...)", s)
    return s[:280]


def _profile_drilldown_call(label, actor_email, kwargs):
    frappe.set_user(actor_email)
    frappe.db.commit()
    gc.collect()

    orig_sql = frappe.db.sql
    shape_stats = {}

    def _spy(query, values=(), *a, **kw):
        q_str = str(query).strip()
        t_q0 = time.perf_counter()
        res = orig_sql(query, values, *a, **kw)
        dt_ms = (time.perf_counter() - t_q0) * 1000.0
        row_cnt = len(res) if isinstance(res, (list, tuple)) else 0
        shape = _normalize_sql_shape(q_str)
        entry = shape_stats.get(shape)
        if entry is None:
            entry = {
                "shape": shape,
                "executions": 0,
                "total_ms": 0.0,
                "max_ms": 0.0,
                "total_rows_returned": 0,
                "sample_query": q_str,
                "sample_values": values,
            }
            shape_stats[shape] = entry
        entry["executions"] += 1
        entry["total_ms"] += dt_ms
        entry["total_rows_returned"] += row_cnt
        if dt_ms >= entry["max_ms"]:
            entry["max_ms"] = dt_ms
            entry["sample_query"] = q_str
            entry["sample_values"] = values
        return res

    st_before = _session_status()
    bp_before = _innodb_bp_status()
    tracemalloc.start()
    cpu0 = time.process_time()
    t0 = time.perf_counter()
    frappe.db.sql = _spy
    try:
        resp = p3_reporting.get_metric_drilldown(**kwargs)
    finally:
        frappe.db.sql = orig_sql
    wall_ms = (time.perf_counter() - t0) * 1000.0
    cpu_ms = (time.process_time() - cpu0) * 1000.0
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    st_after = _session_status()
    bp_after = _innodb_bp_status()

    status_delta = {k: st_after.get(k, 0) - st_before.get(k, 0) for k in st_after}
    innodb_bp_delta = {k: bp_after.get(k, 0) - bp_before.get(k, 0) for k in bp_after}
    total_sql_ms = sum(v["total_ms"] for v in shape_stats.values())
    total_sql_calls = sum(v["executions"] for v in shape_stats.values())
    total_rows_fetched = sum(v["total_rows_returned"] for v in shape_stats.values())

    ranked_shapes = sorted(shape_stats.values(), key=lambda x: x["total_ms"], reverse=True)
    top_queries = []
    for item in ranked_shapes[:8]:
        q_raw = item["sample_query"]
        q_vals = item["sample_values"]
        explain_rows = []
        analyze_summary = None
        if q_raw.lstrip().upper().startswith("SELECT"):
            try:
                explain_rows = orig_sql("EXPLAIN " + q_raw, q_vals, as_dict=True)
            except Exception as exc:
                explain_rows = [{"explain_error": str(exc)}]
            try:
                an_raw = orig_sql("ANALYZE FORMAT=JSON " + q_raw, q_vals)
                if an_raw and an_raw[0] and an_raw[0][0]:
                    an_obj = json.loads(an_raw[0][0])
                    qblock = an_obj.get("query_block") or {}
                    analyze_summary = {
                        "r_total_time_ms": qblock.get("r_total_time_ms"),
                        "query_block": qblock,
                    }
            except Exception as exc:
                analyze_summary = {"analyze_error": str(exc)}
        top_queries.append(
            {
                "shape": item["shape"],
                "executions": item["executions"],
                "total_ms": round(item["total_ms"], 3),
                "max_ms": round(item["max_ms"], 3),
                "total_rows_returned": item["total_rows_returned"],
                "explain": explain_rows,
                "analyze_json": analyze_summary,
            }
        )

    pag = (resp or {}).get("pagination") or {}
    ctx = (resp or {}).get("drilldown_context") or {}
    snap_m = ctx.get("snapshot_metric") or {}
    fresh = (resp or {}).get("freshness") or {}
    rows_out = (resp or {}).get("rows") or []
    return {
        "scenario": label,
        "actor": actor_email,
        "params": kwargs,
        "wall_ms": round(wall_ms, 3),
        "python_process_cpu_ms": round(cpu_ms, 3),
        "sql_total_ms": round(total_sql_ms, 3),
        "python_non_sql_ms": round(max(0.0, wall_ms - total_sql_ms), 3),
        "sql_query_count": total_sql_calls,
        "sql_rows_fetched_into_python": total_rows_fetched,
        "tracemalloc_peak_mib": round(peak_bytes / (1024.0 * 1024.0), 3),
        "mariadb_handler_and_sort_delta": status_delta,
        "innodb_buffer_pool_delta": innodb_bp_delta,
        "pagination": pag,
        "snapshot_reconciliation": {
            "published_numerator": snap_m.get("numerator"),
            "published_denominator": snap_m.get("denominator"),
            "drilldown_pagination_total": pag.get("total"),
        },
        "freshness_dirty_pending": fresh.get("dirty_pending"),
        "returned_page_row_count": len(rows_out),
        "first_row_record_id": rows_out[0].get("record_id") if rows_out else None,
        "top_sql_shapes": top_queries,
    }


# Warmup metadata once
frappe.set_user(bod_user)
_ = p3_reporting.get_executive_overview(period_type="monthly", period_key="2026-10")

diagnostic_profiles = []
scenario_defs = [
    (
        "D1_daily_campaign_connected_rate_p1",
        bod_user,
        {
            "period_type": "daily",
            "period_key": "2026-10-27",
            "scope_type": "campaign",
            "scope_id": campaigns[0],
            "metric_code": "CONNECTED_RATE",
            "page": 1,
            "page_size": 20,
        },
    ),
    (
        "D2_monthly_company_connected_rate_p1",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "CONNECTED_RATE",
            "page": 1,
            "page_size": 20,
        },
    ),
    (
        "D3a_monthly_company_connected_rate_p10",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "CONNECTED_RATE",
            "page": 10,
            "page_size": 20,
        },
    ),
    (
        "D3b_monthly_company_connected_rate_p25_pre_cliff",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "CONNECTED_RATE",
            "page": 25,
            "page_size": 20,
        },
    ),
    (
        "D3c_monthly_company_connected_rate_p26_post_cliff",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "CONNECTED_RATE",
            "page": 26,
            "page_size": 20,
        },
    ),
    (
        "D3d_monthly_company_connected_rate_p500_deep",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "CONNECTED_RATE",
            "page": 500,
            "page_size": 20,
        },
    ),
    (
        "D5_monthly_team_leader_connected_rate_p1",
        leader_users[0],
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "team",
            "scope_id": team_nodes[0],
            "metric_code": "CONNECTED_RATE",
            "page": 1,
            "page_size": 20,
        },
    ),
    (
        "D6_monthly_company_first_attempt_sla_rate_p1",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "page": 1,
            "page_size": 20,
        },
    ),
    (
        "D7_monthly_company_contact_attempt_count_p1",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "CONTACT_ATTEMPT_COUNT",
            "page": 1,
            "page_size": 20,
        },
    ),
    (
        "D8_monthly_company_eligible_coverage_p1",
        bod_user,
        {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": "company",
            "metric_code": "ELIGIBLE_COVERAGE",
            "page": 1,
            "page_size": 20,
        },
    ),
]

for sc_label, sc_actor, sc_kw in scenario_defs:
    diagnostic_profiles.append(_profile_drilldown_call(sc_label, sc_actor, sc_kw))

# D4: Test dirty_pending=True on monthly=2026-10 (simulating active period while cron recompute is pending)
frappe.set_user("Administrator")
prev_sup = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
frappe.flags.cskh_suppress_dirty_enqueue = True
try:
    schedule_post_commit_dirty_invalidation(
        campaign=campaigns[0],
        event_dt=datetime(2026, 10, 27, 15, 0, 0),
        reason="diag_dirty_pending_probe",
        trigger_reference="S1-WI-049200",
    )
    _flush_pending_dirty_events()
    frappe.db.commit()
finally:
    frappe.flags.cskh_suppress_dirty_enqueue = prev_sup

dirty_profile = _profile_drilldown_call(
    "D4_monthly_company_connected_rate_dirty_pending_p1",
    bod_user,
    {
        "period_type": "monthly",
        "period_key": "2026-10",
        "scope_type": "company",
        "metric_code": "CONNECTED_RATE",
        "page": 1,
        "page_size": 20,
    },
)
diagnostic_profiles.append(dirty_profile)

dirty_warm_profile = _profile_drilldown_call(
    "D4_warm_monthly_company_connected_rate_dirty_pending_p1",
    bod_user,
    {
        "period_type": "monthly",
        "period_key": "2026-10",
        "scope_type": "company",
        "metric_code": "CONNECTED_RATE",
        "page": 1,
        "page_size": 20,
    },
)
diagnostic_profiles.append(dirty_warm_profile)

dirty_cov_profile = _profile_drilldown_call(
    "D4_elig_cov_monthly_company_dirty_pending_p1",
    bod_user,
    {
        "period_type": "monthly",
        "period_key": "2026-10",
        "scope_type": "company",
        "metric_code": "ELIGIBLE_COVERAGE",
        "page": 1,
        "page_size": 20,
    },
)
diagnostic_profiles.append(dirty_cov_profile)

# Clear the synthetic diagnostic dirty row before running full S1 fresh/dirty/concurrent-worker cross-check
frappe.set_user("Administrator")
frappe.db.sql("DELETE FROM `tabCSKH Dirty Scope` WHERE trigger_reference = 'S1-WI-049200'")
runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE, runtime_cache.DOMAIN_KPI_SNAPSHOT)
frappe.db.commit()

# F-G18-AB-CONFIGB-AND-DIRTY-COUNT-01: Full S1 count cross-check across (a) fresh, (b) dirty mutated,
# (c) live concurrent `process_dirty_scopes` worker in `queue-short`, and (d) post-worker snapshot convergence.
def _bucket_totals(actor_u, mcode_name, scope_t="company", scope_i=None):
    frappe.set_user(actor_u)
    res_map = {}
    for bkt in ("all_denominator", "numerator_only", "breached_only", "excluded"):
        kw_b = {
            "period_type": "monthly",
            "period_key": "2026-10",
            "scope_type": scope_t,
            "metric_code": mcode_name,
            "filter_bucket": bkt,
            "page": 1,
            "page_size": 20,
        }
        if scope_i:
            kw_b["scope_id"] = scope_i
        rb = p3_reporting.get_metric_drilldown(**kw_b)
        res_map[bkt] = {
            "total": rb["pagination"]["total"],
            "dirty_pending": rb["freshness"]["dirty_pending"],
        }
    return res_map

s1_fresh_counts = {
    "company_connected_rate": _bucket_totals(bod_user, "CONNECTED_RATE", "company"),
    "company_eligible_coverage": _bucket_totals(bod_user, "ELIGIBLE_COVERAGE", "company"),
    "team1_connected_rate": _bucket_totals(leader_users[0], "CONNECTED_RATE", "team", team_nodes[0]),
    "campaign1_connected_rate": _bucket_totals(bod_user, "CONNECTED_RATE", "campaign", campaigns[0]),
    "staff1_connected_rate": _bucket_totals(staff_users[0], "CONNECTED_RATE", "employee", staff_users[0]),
}

# Mutate 2 attempts (`S1-ATT-049200-1` unconnected + `S1-ATT-049201-1` connected) to `wrong_number`
# and seed multi-day dirty scopes (`2026-10-23..2026-10-27`) so `process_dirty_scopes` runs ~8-15s in `queue-short`
frappe.set_user("Administrator")
frappe.db.sql(
    "UPDATE `tabCSKH Contact Attempt` "
    "SET outcome_code = 'wrong_number', invalid_phone_snapshot = 1 "
    "WHERE name IN ('S1-ATT-049200-1', 'S1-ATT-049201-1')"
)
prev_sup_cw = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
frappe.flags.cskh_suppress_dirty_enqueue = True
try:
    for d_day in (23, 24, 25, 26, 27):
        schedule_post_commit_dirty_invalidation(
            campaign=campaigns[0],
            event_dt=datetime(2026, 10, d_day, 14, 0, 0),
            reason="fg18_s1_concurrent_worker_probe",
            trigger_reference="S1-FG18-CONC-WORKER",
        )
    _flush_pending_dirty_events()
    frappe.db.commit()
finally:
    frappe.flags.cskh_suppress_dirty_enqueue = prev_sup_cw

s1_dirty_pre_worker_counts = {
    "company_connected_rate": _bucket_totals(bod_user, "CONNECTED_RATE", "company"),
    "company_eligible_coverage": _bucket_totals(bod_user, "ELIGIBLE_COVERAGE", "company"),
    "team1_connected_rate": _bucket_totals(leader_users[0], "CONNECTED_RATE", "team", team_nodes[0]),
    "campaign1_connected_rate": _bucket_totals(bod_user, "CONNECTED_RATE", "campaign", campaigns[0]),
    "staff1_connected_rate": _bucket_totals(staff_users[0], "CONNECTED_RATE", "employee", staff_users[0]),
}
dirty_inv_profile = _profile_drilldown_call(
    "D4_inv_monthly_company_connected_rate_dirty_with_invalid_phone_p1",
    bod_user,
    {
        "period_type": "monthly",
        "period_key": "2026-10",
        "scope_type": "company",
        "metric_code": "CONNECTED_RATE",
        "page": 1,
        "page_size": 20,
    },
)
diagnostic_profiles.append(dirty_inv_profile)

# Enqueue real `process_dirty_scopes` job onto `queue-short` and wait for `status = 'processing'`
frappe.set_user("Administrator")
try:
    frappe.cache.delete(frappe.cache.make_key("cskh:kpi:dirty_worker_enqueued"))
except Exception:
    pass
frappe.enqueue(
    "crm_cskh.jobs.recompute_kpi.process_dirty_scopes",
    queue="short",
    timeout=900,
    now=False,
    defer_unmaterialized_large_rollups=True,
)
frappe.db.commit()

worker_started_ts = None
poll_deadline = time.monotonic() + 30.0
while time.monotonic() < poll_deadline:
    frappe.db.commit()
    proc_cnt = int(
        frappe.db.sql(
            "SELECT COUNT(*) FROM `tabCSKH Dirty Scope` WHERE trigger_reference = 'S1-FG18-CONC-WORKER' AND status = 'processing'"
        )[0][0]
        or 0
    )
    if proc_cnt > 0:
        worker_started_ts = time.monotonic()
        break
    done_cnt = int(
        frappe.db.sql(
            "SELECT COUNT(*) FROM `tabCSKH Dirty Scope` WHERE trigger_reference = 'S1-FG18-CONC-WORKER' AND status = 'processed'"
        )[0][0]
        or 0
    )
    if done_cnt > 0:
        break
    time.sleep(0.05)

# Execute monthly/company drilldowns concurrently WHILE `process_dirty_scopes` is running in `queue-short`
conc_worker_profile_p1 = _profile_drilldown_call(
    "D4b_monthly_company_connected_rate_dirty_concurrent_worker_p1",
    bod_user,
    {
        "period_type": "monthly",
        "period_key": "2026-10",
        "scope_type": "company",
        "metric_code": "CONNECTED_RATE",
        "page": 1,
        "page_size": 20,
    },
)
diagnostic_profiles.append(conc_worker_profile_p1)

conc_worker_profile_p30 = _profile_drilldown_call(
    "D4c_monthly_company_connected_rate_dirty_concurrent_worker_p30",
    bod_user,
    {
        "period_type": "monthly",
        "period_key": "2026-10",
        "scope_type": "company",
        "metric_code": "CONNECTED_RATE",
        "page": 30,
        "page_size": 20,
    },
)
diagnostic_profiles.append(conc_worker_profile_p30)

s1_concurrent_worker_counts = _bucket_totals(bod_user, "CONNECTED_RATE", "company")
frappe.db.commit()
processing_still_active_after_calls = int(
    frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCSKH Dirty Scope` WHERE trigger_reference = 'S1-FG18-CONC-WORKER' AND status = 'processing'"
    )[0][0]
    or 0
)

# Wait for `queue-short` `process_dirty_scopes` to finish publishing restated/incremental snapshots
finish_deadline = time.monotonic() + 240.0
while time.monotonic() < finish_deadline:
    frappe.db.commit()
    rem_cnt = int(
        frappe.db.sql(
            "SELECT COUNT(*) FROM `tabCSKH Dirty Scope` WHERE trigger_reference = 'S1-FG18-CONC-WORKER' AND status IN ('pending', 'processing')"
        )[0][0]
        or 0
    )
    if rem_cnt == 0:
        break
    time.sleep(0.25)

frappe.db.commit()
runtime_cache.clear_all_local_caches()
frappe.set_user(bod_user)
post_worker_p1 = p3_reporting.get_metric_drilldown(
    period_type="monthly",
    period_key="2026-10",
    scope_type="company",
    metric_code="CONNECTED_RATE",
    page=1,
    page_size=20,
)
post_worker_snap = (post_worker_p1.get("drilldown_context") or {}).get("snapshot_metric") or {}

s1_dirty_and_concurrent_worker_reconciliation = {
    "worker_processing_observed": worker_started_ts is not None,
    "worker_still_processing_after_concurrent_calls": processing_still_active_after_calls > 0,
    "fresh_baseline_counts": s1_fresh_counts,
    "dirty_pre_worker_counts": s1_dirty_pre_worker_counts,
    "dirty_concurrent_worker_counts": s1_concurrent_worker_counts,
    "post_worker_converged": {
        "dirty_pending": post_worker_p1["freshness"]["dirty_pending"],
        "pagination_total": post_worker_p1["pagination"]["total"],
        "published_numerator": post_worker_snap.get("numerator"),
        "published_denominator": post_worker_snap.get("denominator"),
    },
    "all_invariants_verified": (
        s1_fresh_counts["company_connected_rate"]["all_denominator"]["total"] == 75000
        and s1_fresh_counts["company_connected_rate"]["numerator_only"]["total"] == 56250
        and s1_fresh_counts["company_connected_rate"]["breached_only"]["total"] == 18750
        and s1_fresh_counts["company_connected_rate"]["excluded"]["total"] == 0
        and s1_dirty_pre_worker_counts["company_connected_rate"]["all_denominator"]["total"] == 74998
        and s1_dirty_pre_worker_counts["company_connected_rate"]["numerator_only"]["total"] == 56249
        and s1_dirty_pre_worker_counts["company_connected_rate"]["breached_only"]["total"] == 18749
        and s1_dirty_pre_worker_counts["company_connected_rate"]["excluded"]["total"] == 2
        and s1_concurrent_worker_counts["all_denominator"]["total"] == 74998
        and s1_concurrent_worker_counts["numerator_only"]["total"] == 56249
        and s1_concurrent_worker_counts["breached_only"]["total"] == 18749
        and s1_concurrent_worker_counts["excluded"]["total"] == 2
        and post_worker_p1["freshness"]["dirty_pending"] is False
        and post_worker_p1["pagination"]["total"] == 74998
        and post_worker_snap.get("denominator") == 74998
        and post_worker_snap.get("numerator") == 56249
    ),
}

# Restore canonical 75,000 S1 baseline cleanly before HTTP concurrency waves (`W1..W6`)
frappe.set_user("Administrator")
frappe.db.sql(
    "UPDATE `tabCSKH Contact Attempt` "
    "SET outcome_code = 'no_answer', invalid_phone_snapshot = 0 "
    "WHERE name IN ('S1-ATT-049200-1', 'S1-ATT-049201-1')"
)
frappe.db.sql("DELETE FROM `tabCSKH Dirty Scope` WHERE trigger_reference = 'S1-FG18-CONC-WORKER'")
execute_kpi_recompute(month_spec, run_mode="full", actor="Administrator")
execute_kpi_recompute(day_spec, run_mode="incremental", actor="Administrator")
runtime_cache.clear_all_local_caches()
frappe.db.commit()

# Correctness, pagination disjointness & RBAC cross-access checks
frappe.set_user(bod_user)
p1 = p3_reporting.get_metric_drilldown(
    period_type="monthly", period_key="2026-10", scope_type="company",
    metric_code="CONNECTED_RATE", page=1, page_size=20,
)
p2 = p3_reporting.get_metric_drilldown(
    period_type="monthly", period_key="2026-10", scope_type="company",
    metric_code="CONNECTED_RATE", page=2, page_size=20,
)
p30 = p3_reporting.get_metric_drilldown(
    period_type="monthly", period_key="2026-10", scope_type="company",
    metric_code="CONNECTED_RATE", page=30, page_size=20,
)
ids_p1 = [r["record_id"] for r in p1["rows"]]
ids_p2 = [r["record_id"] for r in p2["rows"]]
ids_p30 = [r["record_id"] for r in p30["rows"]]
bod_raw_phones = [
    r.get("phone") for r in p1["rows"]
    if r.get("phone") and "*" not in str(r.get("phone"))
]

cross_access_checks = {}
for role_label, actor_u, kw in (
    (
        "staff_to_company",
        staff_users[0],
        {"period_type": "monthly", "period_key": "2026-10", "scope_type": "company", "metric_code": "CONNECTED_RATE"},
    ),
    (
        "staff_to_other_staff",
        staff_users[0],
        {"period_type": "monthly", "period_key": "2026-10", "scope_type": "employee", "scope_id": staff_users[1], "metric_code": "CONNECTED_RATE"},
    ),
    (
        "leader1_to_team2",
        leader_users[0],
        {"period_type": "monthly", "period_key": "2026-10", "scope_type": "team", "scope_id": team_nodes[1], "metric_code": "CONNECTED_RATE"},
    ),
    (
        "leader1_to_company",
        leader_users[0],
        {"period_type": "monthly", "period_key": "2026-10", "scope_type": "company", "metric_code": "CONNECTED_RATE"},
    ),
    (
        "bod_include_pii_masked",
        bod_user,
        {"period_type": "monthly", "period_key": "2026-10", "scope_type": "company", "metric_code": "CONNECTED_RATE", "include_pii": 1},
    ),
):
    frappe.set_user(actor_u)
    try:
        res_x = p3_reporting.get_metric_drilldown(**kw)
        raw_leak = [
            r.get("phone") for r in (res_x.get("rows") or [])
            if r.get("phone") and "*" not in str(r.get("phone"))
        ]
        cross_access_checks[role_label] = {
            "allowed": True,
            "all_rows_pii_redacted": all(bool(r.get("pii_redacted")) for r in (res_x.get("rows") or [])),
            "unmasked_phone_leaks": len(raw_leak),
        }
    except Exception as exc:
        cross_access_checks[role_label] = {
            "allowed": False,
            "error_type": type(exc).__name__,
            "http_status_code": getattr(exc, "http_status_code", None),
        }

snap_p1 = (p1.get("drilldown_context") or {}).get("snapshot_metric") or {}
correctness_summary = {
    "monthly_connected_rate_reconciled": (
        snap_p1.get("denominator") == p1["pagination"]["total"]
        and snap_p1.get("denominator") == 75000
    ),
    "published_numerator": snap_p1.get("numerator"),
    "published_denominator": snap_p1.get("denominator"),
    "p1_total": p1["pagination"]["total"],
    "p2_total": p2["pagination"]["total"],
    "p30_total": p30["pagination"]["total"],
    "pages_1_2_30_disjoint": (
        len(set(ids_p1) & set(ids_p2)) == 0
        and len(set(ids_p1) & set(ids_p30)) == 0
        and len(set(ids_p2) & set(ids_p30)) == 0
        and len(ids_p1) == 20
        and len(ids_p2) == 20
        and len(ids_p30) == 20
    ),
    "bod_unmasked_phone_leaks": len(bod_raw_phones),
    "cross_access_checks": cross_access_checks,
}

out = {
    "runner_hardware": {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
    },
    "seed_elapsed_sec": seed_elapsed_sec,
    "primary_campaign_id": campaigns[0],
    "primary_team_id": team_nodes[0],
    "bod_user": bod_user,
    "leader_user": leader_users[0],
    "staff_user": staff_users[0],
    "staff_users": staff_users,
    "table_and_index_inventory": table_and_index_inventory,
    "single_call_sql_and_explain_profiles": diagnostic_profiles,
    "s1_dirty_and_concurrent_worker_reconciliation": s1_dirty_and_concurrent_worker_reconciliation,
    "correctness_pagination_and_rbac": correctness_summary,
}
frappe.destroy()
print("G18_DIAG_JSON:" + json.dumps(out, default=str))
"""


def _sample_worker_and_db_saturation(
    *,
    compose_base: list[str],
    compose_env: dict[str, str],
    compose_project: str,
    compose_files: list[Path],
    db_root_pw: str,
) -> dict[str, object]:
    """Capture Gunicorn worker/thread CPU+state, container cgroup stats, and MariaDB Threads_running."""
    proc_script = (
        "import json, os\n"
        "from pathlib import Path\n"
        "pg = os.sysconf('SC_PAGE_SIZE')\n"
        "tot = run = slp = 0\n"
        "rss = {}\n"
        "for p in Path('/proc').iterdir():\n"
        "    if not p.name.isdigit(): continue\n"
        "    try:\n"
        "        cmd = (p / 'cmdline').read_bytes().replace(b'\\x00', b' ').decode('utf-8', 'ignore').lower()\n"
        "        if 'gunicorn' not in cmd and 'frappe.app:application' not in cmd: continue\n"
        "        sm = (p / 'statm').read_text().split()\n"
        "        rss[p.name] = int(sm[1]) * pg // 1024\n"
        "        for t in (p / 'task').iterdir():\n"
        "            if not t.name.isdigit(): continue\n"
        "            st = (t / 'stat').read_text().rsplit(')', 1)[1].split()[0]\n"
        "            tot += 1\n"
        "            if st == 'R': run += 1\n"
        "            else: slp += 1\n"
        "    except Exception: pass\n"
        "print(json.dumps({'tot': tot, 'run': run, 'slp': slp, 'rss': rss}))\n"
    )
    ps_res = subprocess.run(
        [
            *compose_base,
            "exec",
            "-T",
            "backend",
            "/home/frappe/frappe-bench/env/bin/python",
            "-c",
            proc_script,
        ],
        env=compose_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    running_threads = 0
    sleeping_threads = 0
    total_threads = 0
    worker_rss_kib_by_pid: dict[str, int] = {}
    cpu_pct_sum = 0.0
    try:
        proc_obj = json.loads((ps_res.stdout or "").strip().splitlines()[-1])
        total_threads = int(proc_obj.get("tot") or 0)
        running_threads = int(proc_obj.get("run") or 0)
        sleeping_threads = int(proc_obj.get("slp") or 0)
        worker_rss_kib_by_pid = {
            str(k): int(v) for k, v in (proc_obj.get("rss") or {}).items()
        }
    except Exception:
        pass

    db_res = subprocess.run(
        [
            *compose_base,
            "exec",
            "-T",
            "db",
            "mariadb",
            "-uroot",
            f"-p{db_root_pw}",
            "-Nse",
            "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_running','Threads_connected','Slow_queries');",
        ],
        env=compose_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    db_status: dict[str, int] = {}
    for ln in db_res.stdout.splitlines():
        kv = ln.split()
        if len(kv) == 2:
            try:
                db_status[kv[0]] = int(kv[1])
            except ValueError:
                pass

    cstats = _collect_docker_stats(
        compose_project, compose_file=compose_files, env=compose_env
    )
    host_snap = read_host_resource_snapshot()
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "gunicorn_total_threads": total_threads,
        "gunicorn_running_R_threads": running_threads,
        "gunicorn_sleeping_S_threads": sleeping_threads,
        "gunicorn_ps_cpu_pct_sum": round(cpu_pct_sum, 2),
        "gunicorn_workers_rss_mib_total": round(
            sum(worker_rss_kib_by_pid.values()) / 1024.0, 2
        ),
        "gunicorn_distinct_pids": len(worker_rss_kib_by_pid),
        "mariadb_threads_running": db_status.get("Threads_running", 0),
        "mariadb_threads_connected": db_status.get("Threads_connected", 0),
        "container_stats": cstats,
        "host_mem_available_mib": host_snap["mem_available_mib"],
    }


def _run_monitored_http_wave(
    *,
    wave_id: str,
    path: str,
    cookies: list[str],
    base_url: str,
    site_name: str,
    concurrency: int,
    requests_total: int,
    timeout_sec: float,
    compose_base: list[str],
    compose_env: dict[str, str],
    compose_project: str,
    compose_files: list[Path],
    db_root_pw: str,
) -> dict[str, object]:
    """Run an HTTP wave while sampling Gunicorn thread & MariaDB saturation every ~1.2s."""
    samples: list[dict[str, object]] = []
    stop_evt = threading.Event()

    def _sampler() -> None:
        while not stop_evt.is_set():
            try:
                samples.append(
                    _sample_worker_and_db_saturation(
                        compose_base=compose_base,
                        compose_env=compose_env,
                        compose_project=compose_project,
                        compose_files=compose_files,
                        db_root_pw=db_root_pw,
                    )
                )
            except Exception:
                pass
            stop_evt.wait(1.2)

    sampler_thread = threading.Thread(target=_sampler, daemon=True)
    sampler_thread.start()

    outcomes: list[tuple[float, int]] = []

    def _call_one(idx: int) -> tuple[float, int]:
        ck = cookies[idx % len(cookies)]
        lat_ms, st, _ = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=ck,
            path=path,
            method="GET",
            timeout=timeout_sec,
        )
        return lat_ms, st

    t0 = time.perf_counter()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(_call_one, i) for i in range(requests_total)]
            for fut in concurrent.futures.as_completed(futs):
                outcomes.append(fut.result())
    finally:
        stop_evt.set()
        sampler_thread.join(timeout=5.0)

    elapsed = time.perf_counter() - t0
    metrics = classify_http_outcomes(
        outcomes, elapsed_sec=elapsed, timeout_sec=timeout_sec
    )

    peak_r_threads = max(
        (int(s.get("gunicorn_running_R_threads") or 0) for s in samples), default=0
    )
    avg_r_threads = (
        round(
            sum(int(s.get("gunicorn_running_R_threads") or 0) for s in samples)
            / len(samples),
            2,
        )
        if samples
        else 0.0
    )
    peak_db_running = max(
        (int(s.get("mariadb_threads_running") or 0) for s in samples), default=0
    )
    peak_rss_mib = max(
        (float(s.get("gunicorn_workers_rss_mib_total") or 0.0) for s in samples),
        default=0.0,
    )

    return {
        "wave_id": wave_id,
        "path": path,
        "concurrency": concurrency,
        "distinct_sessions": len(cookies),
        **metrics,
        "worker_and_db_saturation": {
            "samples_collected": len(samples),
            "peak_gunicorn_running_R_threads": peak_r_threads,
            "avg_gunicorn_running_R_threads": avg_r_threads,
            "peak_gunicorn_workers_rss_mib": peak_rss_mib,
            "peak_mariadb_threads_running": peak_db_running,
            "timeline_samples": samples[:12],
        },
    }


_STAMPEDE_DB_STATUS_KEYS = (
    "Questions",
    "Com_select",
    "Innodb_buffer_pool_read_requests",
    "Innodb_buffer_pool_reads",
    "Handler_read_next",
    "Handler_read_key",
    "Handler_read_rnd_next",
    "Innodb_row_lock_waits",
    "Slow_queries",
)

_BACKEND_STAMPEDE_CONTROL_SCRIPT = r"""
import json, os, sys
from datetime import datetime, timedelta
import frappe
from crm_cskh.services import contact_attempt, runtime_cache
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import (
    _flush_pending_dirty_events,
    execute_kpi_recompute,
    process_dirty_scopes,
    schedule_post_commit_dirty_invalidation,
)

cmd = json.loads(os.environ["STAMPEDE_CMD_JSON"])
site = os.environ["BENCH_SITE"]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
frappe.set_user("Administrator")
try:
    action = cmd["action"]
    out = {"ok": True, "action": action}
    if action == "prepare_wave":
        mode = cmd["reset_mode"]
        disable_c = bool(cmd.get("disable_coalesce", False))
        if mode == "cold_clean_all":
            frappe.clear_cache()
        elif mode == "post_record_attempt_2026_11":
            # Reproduce exact benchmark sequence: callback ToDo user cache clear + dirty/snapshot epoch bump
            u = str(cmd["staff_user"])
            if disable_c:
                # Reproduce 2796d1f2 pre-fix _wrapped_clear_cache(user=...) wiping all domains
                runtime_cache.clear_all_local_caches()
                runtime_cache.bump_cache_domain(*runtime_cache.ALL_DOMAINS)
            else:
                frappe.clear_cache(user=u)
                runtime_cache.bump_cache_domain(
                    runtime_cache.DOMAIN_DIRTY_SCOPE,
                    runtime_cache.DOMAIN_KPI_SNAPSHOT,
                )
        elif mode == "dirty_2026_10_mutation":
            frappe.db.sql(
                "update `tabCSKH Contact Attempt` set invalid_phone_snapshot = 1, outcome_code = 'wrong_number' "
                "where name in ('S1-ATT-000000-1', 'S1-ATT-000000-2', 'S1-ATT-000000-3', 'S1-ATT-000000-4')"
            )
            prev_sup = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
            frappe.flags.cskh_suppress_dirty_enqueue = True
            try:
                schedule_post_commit_dirty_invalidation(
                    work_item="S1-WI-000000",
                    campaign=str(cmd["primary_camp"]),
                    owner_user=str(cmd["staff_user"]),
                    event_dt=datetime(2026, 10, 27, 9, 0, 0),
                    reason="diag_api05b_stampede_dirty",
                    trigger_reference="S1-ATT-000000-1",
                )
                _flush_pending_dirty_events()
                frappe.db.commit()
            finally:
                frappe.flags.cskh_suppress_dirty_enqueue = prev_sup
            if disable_c:
                runtime_cache.bump_cache_domain(*runtime_cache.ALL_DOMAINS)
            else:
                runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
        elif mode == "dirty_2026_10_enqueue_worker":
            frappe.db.sql(
                "update `tabCSKH Contact Attempt` set invalid_phone_snapshot = 1, outcome_code = 'wrong_number' "
                "where name in ('S1-ATT-000000-1', 'S1-ATT-000000-2', 'S1-ATT-000000-3', 'S1-ATT-000000-4')"
            )
            prev_sup = getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
            frappe.flags.cskh_suppress_dirty_enqueue = True
            try:
                schedule_post_commit_dirty_invalidation(
                    work_item="S1-WI-000000",
                    campaign=str(cmd["primary_camp"]),
                    owner_user=str(cmd["staff_user"]),
                    event_dt=datetime(2026, 10, 27, 9, 0, 0),
                    reason="diag_api05b_worker_overlap",
                    trigger_reference="S1-ATT-000000-1",
                )
                _flush_pending_dirty_events()
                frappe.db.commit()
            finally:
                frappe.flags.cskh_suppress_dirty_enqueue = prev_sup
            job = frappe.enqueue(
                "crm_cskh.jobs.recompute_kpi.process_dirty_scopes",
                queue="short",
                timeout=900,
                now=False,
                defer_unmaterialized_large_rollups=True,
            )
            frappe.db.commit()
            out["enqueued_job_id"] = getattr(job, "id", None)
            if disable_c:
                runtime_cache.bump_cache_domain(*runtime_cache.ALL_DOMAINS)
            else:
                runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_DIRTY_SCOPE)
        runtime_cache.set_cross_process_diag_mode(probe_enabled=True, disable_coalesce=disable_c)
        runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
    elif action == "read_telemetry":
        out["telemetry"] = runtime_cache.read_cross_process_diag_telemetry()
        out["dirty_scope_counts"] = {
            str(s): int(c)
            for s, c in frappe.db.sql("select status, count(*) from `tabCSKH Dirty Scope` group by status")
        }
    elif action == "restore_clean_s1":
        runtime_cache.set_cross_process_diag_mode(probe_enabled=False, disable_coalesce=False)
        runtime_cache.reset_coalesce_telemetry(clear_redis_diag=True)
        import time as _t
        dl = _t.monotonic() + 90.0
        while _t.monotonic() < dl:
            rem = frappe.db.sql(
                "select count(*) from `tabCSKH Dirty Scope` where status in ('pending', 'processing')"
            )[0][0]
            running = frappe.db.sql(
                "select count(*) from `tabCSKH KPI Run` where status = 'running'"
            )[0][0]
            if int(rem or 0) == 0 and int(running or 0) == 0:
                break
            _t.sleep(0.5)
            frappe.db.commit()
        frappe.db.sql(
            "update `tabCSKH Contact Attempt` set invalid_phone_snapshot = 0, "
            "outcome_code = case when name = 'S1-ATT-000000-1' then 'no_answer' "
            "when name = 'S1-ATT-000000-4' then 'connected_interested' else 'busy' end "
            "where name in ('S1-ATT-000000-1', 'S1-ATT-000000-2', 'S1-ATT-000000-3', 'S1-ATT-000000-4')"
        )
        frappe.db.sql("delete from `tabCSKH Dirty Scope`")
        frappe.db.commit()
        execute_kpi_recompute(resolve_period_spec("monthly", "2026-10"), run_mode="full", actor="Administrator")
        frappe.db.commit()
        frappe.clear_cache()
    print("STAMPEDE_CTRL_JSON:" + json.dumps(out, default=str))
finally:
    frappe.destroy()
"""


def _read_stampede_db_counters(
    *,
    compose_base: list[str],
    compose_env: dict[str, str],
    db_root_pw: str,
) -> dict[str, int]:
    in_list = ",".join(f"'{k}'" for k in _STAMPEDE_DB_STATUS_KEYS)
    proc = subprocess.run(
        [
            *compose_base,
            "exec",
            "-T",
            "-e",
            f"MYSQL_PWD={db_root_pw}",
            "db",
            "mariadb",
            "-uroot",
            "-N",
            "-B",
            "-e",
            f"SHOW GLOBAL STATUS WHERE Variable_name IN ({in_list});",
        ],
        env=compose_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out: dict[str, int] = {k: 0 for k in _STAMPEDE_DB_STATUS_KEYS}
    if proc.returncode == 0:
        for ln in proc.stdout.splitlines():
            parts = ln.strip().split("\t")
            if len(parts) == 2 and parts[0] in out:
                try:
                    out[parts[0]] = int(float(parts[1]))
                except ValueError:
                    pass
    return out


def _exec_stampede_ctrl(
    *,
    compose_base: list[str],
    compose_env: dict[str, str],
    site_name: str,
    cmd_payload: dict[str, object],
) -> dict[str, object]:
    proc = subprocess.run(
        [
            *compose_base,
            "exec",
            "-T",
            "-e",
            f"BENCH_SITE={site_name}",
            "-e",
            f"STAMPEDE_CMD_JSON={json.dumps(cmd_payload)}",
            "backend",
            "/home/frappe/frappe-bench/env/bin/python",
            "-",
        ],
        input=_BACKEND_STAMPEDE_CONTROL_SCRIPT,
        env=compose_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Stampede control action {cmd_payload.get('action')!r} failed:\n"
            f"{proc.stdout[-1200:]}\n{proc.stderr[-1200:]}"
        )
    lines = [
        ln[len("STAMPEDE_CTRL_JSON:") :]
        for ln in proc.stdout.splitlines()
        if ln.startswith("STAMPEDE_CTRL_JSON:")
    ]
    if not lines:
        raise RuntimeError(f"Missing STAMPEDE_CTRL_JSON marker:\n{proc.stdout[-800:]}")
    return json.loads(lines[-1])


def _run_api05b_stampede_before_after_suite(
    *,
    base_url: str,
    site_name: str,
    admin_cookie: str,
    bod_cookie: str,
    leader_cookie: str,
    staff_cookie: str,
    primary_camp_raw: str,
    primary_team_raw: str,
    staff_user_raw: str,
    compose_base: list[str],
    compose_env: dict[str, str],
    db_root_pw: str,
) -> dict[str, object]:
    """Reproduce `API-05B-C12`, `API-05B-BOD`, and `c=40` BEFORE vs AFTER cross-process coalescing."""
    path_comp = (
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
        "?period_type=monthly&period_key=2026-10&scope_type=company"
        "&metric_code=CONNECTED_RATE&page=1&page_size=20"
    )
    path_team = (
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
        f"?period_type=monthly&period_key=2026-10&scope_type=team"
        f"&scope_id={urllib.parse.quote(primary_team_raw)}&metric_code=CONNECTED_RATE&page=1&page_size=20"
    )
    path_emp = (
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
        f"?period_type=monthly&period_key=2026-10&scope_type=employee"
        f"&scope_id={urllib.parse.quote(staff_user_raw)}&metric_code=CONNECTED_RATE&page=1&page_size=20"
    )

    def _run_single_probed_wave(
        *,
        wave_id: str,
        path: str,
        cookie: str,
        concurrency: int,
        requests_total: int,
        reset_mode: str,
        disable_coalesce: bool,
    ) -> dict[str, object]:
        prep = _exec_stampede_ctrl(
            compose_base=compose_base,
            compose_env=compose_env,
            site_name=site_name,
            cmd_payload={
                "action": "prepare_wave",
                "reset_mode": reset_mode,
                "disable_coalesce": disable_coalesce,
                "primary_camp": primary_camp_raw,
                "staff_user": staff_user_raw,
            },
        )
        db_before = _read_stampede_db_counters(
            compose_base=compose_base, compose_env=compose_env, db_root_pw=db_root_pw
        )

        ordered_lats: list[float] = [0.0] * requests_total
        outcomes: list[tuple[float, int]] = []
        observed_totals: set[int] = set()
        observed_dirty: set[bool] = set()

        def _one_req(idx: int) -> tuple[int, float, int, int | None, bool | None]:
            lat_ms, st, raw = _http_call(
                base_url=base_url,
                site_name=site_name,
                cookie_header=cookie,
                path=path,
                method="GET",
                timeout=30.0,
            )
            tot_v = None
            dirty_v = None
            if st == 200 and raw:
                try:
                    msg = (json.loads(raw.decode("utf-8", errors="ignore")) or {}).get(
                        "message"
                    ) or {}
                    tot_v = int((msg.get("pagination") or {}).get("total") or 0)
                    dirty_v = bool((msg.get("freshness") or {}).get("dirty_pending"))
                except Exception:
                    pass
            return idx, lat_ms, st, tot_v, dirty_v

        t_w0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [pool.submit(_one_req, i) for i in range(requests_total)]
            for fut in concurrent.futures.as_completed(futs):
                idx, lat_ms, st, tot_v, dirty_v = fut.result()
                ordered_lats[idx] = round(lat_ms, 3)
                outcomes.append((lat_ms, st))
                if tot_v is not None:
                    observed_totals.add(tot_v)
                if dirty_v is not None:
                    observed_dirty.add(dirty_v)
        elapsed_s = time.perf_counter() - t_w0

        db_after = _read_stampede_db_counters(
            compose_base=compose_base, compose_env=compose_env, db_root_pw=db_root_pw
        )
        tel_out = _exec_stampede_ctrl(
            compose_base=compose_base,
            compose_env=compose_env,
            site_name=site_name,
            cmd_payload={"action": "read_telemetry"},
        )
        db_delta = {
            k: max(0, int(db_after.get(k, 0)) - int(db_before.get(k, 0)))
            for k in _STAMPEDE_DB_STATUS_KEYS
        }
        summary = classify_http_outcomes(
            outcomes, elapsed_sec=elapsed_s, timeout_sec=30.0
        )
        tel_obj = tel_out.get("telemetry") or {}
        totals_map = (tel_obj.get("totals") if isinstance(tel_obj, dict) else {}) or {}

        return {
            "wave_id": wave_id,
            "mode": "before_uncoalesced" if disable_coalesce else "after_coalesced",
            "reset_mode": reset_mode,
            "concurrency": concurrency,
            "requests_total": requests_total,
            "enqueued_worker_job_id": prep.get("enqueued_job_id"),
            **summary,
            "first_12_latencies_ms": ordered_lats[: min(12, len(ordered_lats))],
            "after_first_12_latencies_ms": ordered_lats[12:36],
            "per_request_latencies_ms": ordered_lats,
            "observed_pagination_totals": sorted(observed_totals),
            "observed_dirty_pending_states": sorted(observed_dirty),
            "sql_count_executions": int(totals_map.get("sql_count_executions", 0)),
            "sql_count_elapsed_ms": tel_obj.get("sql_count_elapsed_ms", 0.0)
            if isinstance(tel_obj, dict)
            else 0.0,
            "cache_and_coalesce_telemetry": tel_obj,
            "dirty_scope_counts_after_wave": tel_out.get("dirty_scope_counts"),
            "mariadb_delta": db_delta,
        }

    scenarios_spec = [
        (
            "API05B_C12_admin_cold_clean",
            path_comp,
            admin_cookie,
            12,
            48,
            "cold_clean_all",
        ),
        (
            "API05B_C40_admin_warm_control",
            path_comp,
            admin_cookie,
            40,
            80,
            "warm_no_reset",
        ),
        (
            "API05B_BOD_c12_post_record_attempt_epoch_bump",
            path_comp,
            bod_cookie,
            12,
            48,
            "post_record_attempt_2026_11",
        ),
        (
            "API05B_C12_admin_dirty_2026_10_cold_epoch",
            path_comp,
            admin_cookie,
            12,
            48,
            "dirty_2026_10_mutation",
        ),
        (
            "API05B_C40_admin_dirty_2026_10_cold_epoch",
            path_comp,
            admin_cookie,
            40,
            80,
            "dirty_2026_10_mutation",
        ),
        (
            "API05B_BOD_c12_dirty_2026_10_concurrent_worker",
            path_comp,
            bod_cookie,
            12,
            48,
            "dirty_2026_10_enqueue_worker",
        ),
        (
            "RBAC_team_leader_c12_dirty_2026_10",
            path_team,
            leader_cookie,
            12,
            36,
            "dirty_2026_10_mutation",
        ),
        (
            "RBAC_staff_self_c12_dirty_2026_10",
            path_emp,
            staff_cookie,
            12,
            36,
            "dirty_2026_10_mutation",
        ),
    ]

    before_waves: dict[str, object] = {}
    after_waves: dict[str, object] = {}
    comparisons: dict[str, object] = {}

    try:
        for sc_name, sc_path, sc_cookie, sc_conc, sc_reqs, sc_reset in scenarios_spec:
            if sc_reset == "dirty_2026_10_enqueue_worker":
                # Ensure clean S1 before worker-overlap wave
                _exec_stampede_ctrl(
                    compose_base=compose_base,
                    compose_env=compose_env,
                    site_name=site_name,
                    cmd_payload={"action": "restore_clean_s1"},
                )
            bw = _run_single_probed_wave(
                wave_id=f"BEFORE_{sc_name}",
                path=sc_path,
                cookie=sc_cookie,
                concurrency=sc_conc,
                requests_total=sc_reqs,
                reset_mode=sc_reset,
                disable_coalesce=True,
            )
            before_waves[sc_name] = bw

        _exec_stampede_ctrl(
            compose_base=compose_base,
            compose_env=compose_env,
            site_name=site_name,
            cmd_payload={"action": "restore_clean_s1"},
        )

        for sc_name, sc_path, sc_cookie, sc_conc, sc_reqs, sc_reset in scenarios_spec:
            if sc_reset == "dirty_2026_10_enqueue_worker":
                _exec_stampede_ctrl(
                    compose_base=compose_base,
                    compose_env=compose_env,
                    site_name=site_name,
                    cmd_payload={"action": "restore_clean_s1"},
                )
            aw = _run_single_probed_wave(
                wave_id=f"AFTER_{sc_name}",
                path=sc_path,
                cookie=sc_cookie,
                concurrency=sc_conc,
                requests_total=sc_reqs,
                reset_mode=sc_reset,
                disable_coalesce=False,
            )
            after_waves[sc_name] = aw

            bw = before_waves[sc_name]
            assert isinstance(bw, dict)
            comparisons[sc_name] = {
                "before_p95_ms": bw["p95_ms"],
                "after_p95_ms": aw["p95_ms"],
                "before_p99_ms": bw["p99_ms"],
                "after_p99_ms": aw["p99_ms"],
                "before_sql_count_executions": bw["sql_count_executions"],
                "after_sql_count_executions": aw["sql_count_executions"],
                "before_bp_read_requests": bw["mariadb_delta"][
                    "Innodb_buffer_pool_read_requests"
                ],
                "after_bp_read_requests": aw["mariadb_delta"][
                    "Innodb_buffer_pool_read_requests"
                ],
                "before_questions": bw["mariadb_delta"]["Questions"],
                "after_questions": aw["mariadb_delta"]["Questions"],
                "exact_totals_before": bw["observed_pagination_totals"],
                "exact_totals_after": aw["observed_pagination_totals"],
            }
    finally:
        _exec_stampede_ctrl(
            compose_base=compose_base,
            compose_env=compose_env,
            site_name=site_name,
            cmd_payload={"action": "restore_clean_s1"},
        )

    return {
        "schema_version": "cskh-p4-api05b-stampede-before-after-v1",
        "before_uncoalesced_waves": before_waves,
        "after_coalesced_waves": after_waves,
        "before_vs_after_summary": comparisons,
    }


def run_diagnostic(
    *,
    app_image: str,
    resource_profile: str,
    pull_policy: str = "missing",
    port: int = 18890,
    site_name: str = "p4-g18-diag.localhost",
    protect_live_resources: bool = True,
    forbid_cohosted_live_containers: bool = False,
) -> dict[str, object]:
    if resource_profile not in RESOURCE_PROFILES:
        raise ValueError(f"Unknown --resource-profile: {resource_profile}")

    compose_project = f"cskh-p4-bench-diag-{secrets.token_hex(4)}"
    base_url = f"http://127.0.0.1:{port}"
    isolation = enforce_isolated_benchmark_target(
        base_url=base_url,
        site_name=site_name,
        compose_project=compose_project,
        confirm_isolated_target=True,
    )

    ps_names_proc = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    running_names = [
        ln.strip() for ln in ps_names_proc.stdout.splitlines() if ln.strip()
    ]
    live_guard: dict[str, object] | None = None
    if protect_live_resources:
        live_guard = enforce_live_resource_headroom(
            snapshot=read_host_resource_snapshot(),
            running_container_names=running_names,
            forbid_cohosted_live_containers=forbid_cohosted_live_containers,
        )

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    prof = RESOURCE_PROFILES[resource_profile]
    db_root_pw = secrets.token_hex(24)
    admin_pw = secrets.token_hex(24)
    actor_pw = secrets.token_hex(20)

    with tempfile.TemporaryDirectory(prefix="cskh-g18-diag-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_secret = tmp_dir / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
        override_file = tmp_dir / "compose.diag-override.yaml"
        override_file.write_text(str(prof["override_yaml"]), encoding="utf-8")
        compose_files = [VERIFY_COMPOSE_FILE, override_file]

        compose_env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": compose_project,
            "APP_IMAGE": app_image,
            "PULL_POLICY": pull_policy,
            "SITE_NAME": site_name,
            "VERIFY_HTTP_PORT": str(port),
            "DB_PASSWORD_SECRETS_FILE": str(db_secret),
            "MARIADB_IMAGE": lock["infra_images"]["mariadb"],
            "REDIS_IMAGE": lock["infra_images"]["redis"],
        }
        compose_base = [
            "docker",
            "compose",
            *_compose_files_arg(compose_files),
            "-p",
            compose_project,
        ]

        try:
            subprocess.run(
                [*compose_base, "up", "-d", "--no-build"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )

            init_cmd = (
                "set -euo pipefail; cd /home/frappe/frappe-bench; "
                "bench new-site --no-mariadb-socket --mariadb-user-host-login-scope=% "
                '--db-root-password "$BENCH_DB_ROOT_PW" --admin-password "$BENCH_ADMIN_PW" "$BENCH_SITE"; '
                'bench use "$BENCH_SITE"; '
                'bench --site "$BENCH_SITE" set-config time_zone Asia/Ho_Chi_Minh; '
                'bench --site "$BENCH_SITE" set-config default_language vi; '
                'bench --site "$BENCH_SITE" install-app crm; '
                'bench --site "$BENCH_SITE" install-app crm_cskh; '
                'bench --site "$BENCH_SITE" migrate; '
                'bench --site "$BENCH_SITE" clear-cache'
            )
            init_proc = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"BENCH_DB_ROOT_PW={db_root_pw}",
                    "-e",
                    f"BENCH_ADMIN_PW={admin_pw}",
                    "-e",
                    f"BENCH_SITE={site_name}",
                    "backend",
                    "bash",
                    "-lc",
                    init_cmd,
                ],
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if init_proc.returncode != 0:
                raise RuntimeError(
                    f"Site bootstrap failed (rc={init_proc.returncode}):\n"
                    f"STDOUT:\n{init_proc.stdout[-1500:]}\nSTDERR:\n{init_proc.stderr[-1500:]}"
                )

            subprocess.run(
                [
                    *compose_base,
                    "restart",
                    "backend",
                    "websocket",
                    "queue-short",
                    "queue-long",
                    "scheduler",
                ],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

            diag_proc = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    "FRAPPE_STREAM_LOGGING=1",
                    "-e",
                    f"BENCH_SITE={site_name}",
                    "-e",
                    f"BENCH_ACTOR_PW={actor_pw}",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-",
                ],
                input=IN_CONTAINER_DIAG_SCRIPT,
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=2400,
            )
            if diag_proc.returncode != 0:
                raise RuntimeError(
                    f"In-container G18 diagnostic failed (rc={diag_proc.returncode}):\n"
                    f"STDOUT:\n{diag_proc.stdout[-2500:]}\nSTDERR:\n{diag_proc.stderr[-2500:]}"
                )
            markers = [
                ln[len("G18_DIAG_JSON:") :]
                for ln in diag_proc.stdout.splitlines()
                if ln.startswith("G18_DIAG_JSON:")
            ]
            if not markers:
                raise RuntimeError(
                    f"Missing G18_DIAG_JSON marker:\n{diag_proc.stderr[-1000:]}"
                )
            in_container = json.loads(markers[-1])

            subprocess.run(
                [
                    *compose_base,
                    "up",
                    "-d",
                    "--no-build",
                    "--force-recreate",
                    "frontend",
                ],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

            deadline = time.monotonic() + 90.0
            http_ready = False
            while time.monotonic() < deadline:
                try:
                    req = urllib.request.Request(
                        f"{base_url}/api/method/ping", headers={"Host": site_name}
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status == 200:
                            http_ready = True
                            break
                except Exception:
                    pass
                time.sleep(2.0)
            if not http_ready:
                raise RuntimeError(f"Frontend HTTP not ready on {base_url}")

            admin_sess = _login_session(
                base_url=base_url,
                site_name=site_name,
                user="Administrator",
                password=admin_pw,
            )
            bod_sess = _login_session(
                base_url=base_url,
                site_name=site_name,
                user=str(in_container["bod_user"]),
                password=actor_pw,
            )
            leader_sess = _login_session(
                base_url=base_url,
                site_name=site_name,
                user=str(in_container["leader_user"]),
                password=actor_pw,
            )
            staff_sess = _login_session(
                base_url=base_url,
                site_name=site_name,
                user=str(in_container["staff_user"]),
                password=actor_pw,
            )

            api05b_stampede_suite = _run_api05b_stampede_before_after_suite(
                base_url=base_url,
                site_name=site_name,
                admin_cookie=admin_sess["cookie"],
                bod_cookie=bod_sess["cookie"],
                leader_cookie=leader_sess["cookie"],
                staff_cookie=staff_sess["cookie"],
                primary_camp_raw=str(in_container["primary_campaign_id"]),
                primary_team_raw=str(in_container["primary_team_id"]),
                staff_user_raw=str(in_container["staff_user"]),
                compose_base=compose_base,
                compose_env=compose_env,
                db_root_pw=db_root_pw,
            )

            primary_camp = urllib.parse.quote(str(in_container["primary_campaign_id"]))
            primary_team = urllib.parse.quote(str(in_container["primary_team_id"]))

            profiles_by_name = {
                p["scenario"]: p
                for p in in_container["single_call_sql_and_explain_profiles"]
            }

            path_daily_camp = (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                f"?period_type=daily&period_key=2026-10-27&scope_type=campaign"
                f"&scope_id={primary_camp}&metric_code=CONNECTED_RATE&page=1&page_size=20"
            )
            path_month_comp_p1 = (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                "?period_type=monthly&period_key=2026-10&scope_type=company"
                "&metric_code=CONNECTED_RATE&page=1&page_size=20"
            )
            path_month_comp_p30 = (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                "?period_type=monthly&period_key=2026-10&scope_type=company"
                "&metric_code=CONNECTED_RATE&page=30&page_size=20"
            )
            path_month_team_p1 = (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                f"?period_type=monthly&period_key=2026-10&scope_type=team"
                f"&scope_id={primary_team}&metric_code=CONNECTED_RATE&page=1&page_size=20"
            )
            path_month_sla_p1 = (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                "?period_type=monthly&period_key=2026-10&scope_type=company"
                "&metric_code=FIRST_ATTEMPT_SLA_RATE&page=1&page_size=20"
            )
            path_month_att_p1 = (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                "?period_type=monthly&period_key=2026-10&scope_type=company"
                "&metric_code=CONTACT_ATTEMPT_COUNT&page=1&page_size=20"
            )

            # Live HTTP cross-access negative checks (must return HTTP 403 on clean socket pool)
            _, st_staff_comp, _ = _http_call(
                base_url=base_url,
                site_name=site_name,
                cookie_header=staff_sess["cookie"],
                path=path_month_comp_p1,
                method="GET",
            )
            _, st_leader_comp, _ = _http_call(
                base_url=base_url,
                site_name=site_name,
                cookie_header=leader_sess["cookie"],
                path=path_month_comp_p1,
                method="GET",
            )

            http_waves: dict[str, object] = {}

            # Mandatory exact S1 baseline comparison waves: c=12 (240 reqs) & c=40 (160 reqs, 30s timeout)
            http_waves["W1_daily_campaign_connected_rate_p1_c12"] = (
                _run_monitored_http_wave(
                    wave_id="W1_daily_campaign_connected_rate_p1_c12",
                    path=path_daily_camp,
                    cookies=[bod_sess["cookie"]],
                    base_url=base_url,
                    site_name=site_name,
                    concurrency=12,
                    requests_total=240,
                    timeout_sec=30.0,
                    compose_base=compose_base,
                    compose_env=compose_env,
                    compose_project=compose_project,
                    compose_files=compose_files,
                    db_root_pw=db_root_pw,
                )
            )
            http_waves["W1_daily_campaign_connected_rate_p1_c40"] = (
                _run_monitored_http_wave(
                    wave_id="W1_daily_campaign_connected_rate_p1_c40",
                    path=path_daily_camp,
                    cookies=[bod_sess["cookie"]],
                    base_url=base_url,
                    site_name=site_name,
                    concurrency=40,
                    requests_total=160,
                    timeout_sec=30.0,
                    compose_base=compose_base,
                    compose_env=compose_env,
                    compose_project=compose_project,
                    compose_files=compose_files,
                    db_root_pw=db_root_pw,
                )
            )

            # Extended waves across monthly 250k, deep page=30, team scope, SLA 150k, and Attempt 250k
            extended_specs = [
                (
                    "W2_monthly_company_connected_rate_p1",
                    path_month_comp_p1,
                    [bod_sess["cookie"]],
                    profiles_by_name.get("D2_monthly_company_connected_rate_p1") or {},
                ),
                (
                    "W3_monthly_company_connected_rate_deep_p30",
                    path_month_comp_p30,
                    [bod_sess["cookie"]],
                    profiles_by_name.get(
                        "D3c_monthly_company_connected_rate_p26_post_cliff"
                    )
                    or {},
                ),
                (
                    "W4_monthly_team_leader_connected_rate_p1",
                    path_month_team_p1,
                    [leader_sess["cookie"]],
                    profiles_by_name.get("D5_monthly_team_leader_connected_rate_p1")
                    or {},
                ),
                (
                    "W5_monthly_company_first_attempt_sla_p1",
                    path_month_sla_p1,
                    [bod_sess["cookie"]],
                    profiles_by_name.get("D6_monthly_company_first_attempt_sla_rate_p1")
                    or {},
                ),
                (
                    "W6_monthly_company_contact_attempt_count_p1",
                    path_month_att_p1,
                    [bod_sess["cookie"]],
                    profiles_by_name.get("D7_monthly_company_contact_attempt_count_p1")
                    or {},
                ),
            ]

            for w_prefix, w_path, w_cookies, prof_obj in extended_specs:
                single_ms = float(prof_obj.get("wall_ms") or 0.0)
                rows_fetched = int(prof_obj.get("sql_rows_fetched_into_python") or 0)
                if rows_fetched > 5000 or single_ms > 600.0:
                    # Unoptimized full-dataset hydration (>5k rows per request): run bounded c=12 (24 reqs)
                    # saturation probe so CI collects real saturation metrics without 16x250k host OOM.
                    http_waves[f"{w_prefix}_c12"] = _run_monitored_http_wave(
                        wave_id=f"{w_prefix}_c12_compact_unoptimized",
                        path=w_path,
                        cookies=w_cookies,
                        base_url=base_url,
                        site_name=site_name,
                        concurrency=12,
                        requests_total=24,
                        timeout_sec=30.0,
                        compose_base=compose_base,
                        compose_env=compose_env,
                        compose_project=compose_project,
                        compose_files=compose_files,
                        db_root_pw=db_root_pw,
                    )
                else:
                    http_waves[f"{w_prefix}_c12"] = _run_monitored_http_wave(
                        wave_id=f"{w_prefix}_c12",
                        path=w_path,
                        cookies=w_cookies,
                        base_url=base_url,
                        site_name=site_name,
                        concurrency=12,
                        requests_total=240,
                        timeout_sec=30.0,
                        compose_base=compose_base,
                        compose_env=compose_env,
                        compose_project=compose_project,
                        compose_files=compose_files,
                        db_root_pw=db_root_pw,
                    )
                    http_waves[f"{w_prefix}_c40"] = _run_monitored_http_wave(
                        wave_id=f"{w_prefix}_c40",
                        path=w_path,
                        cookies=w_cookies,
                        base_url=base_url,
                        site_name=site_name,
                        concurrency=40,
                        requests_total=160,
                        timeout_sec=30.0,
                        compose_base=compose_base,
                        compose_env=compose_env,
                        compose_project=compose_project,
                        compose_files=compose_files,
                        db_root_pw=db_root_pw,
                    )

            db_after = _collect_queue_and_db_telemetry(
                compose_project, site_name, compose_file=compose_files, env=compose_env
            )
            img_insp = json.loads(
                subprocess.check_output(
                    ["docker", "image", "inspect", app_image], text=True
                )
            )[0]

            in_clean = {
                k: v for k, v in in_container.items() if k not in ("staff_users",)
            }
            return {
                "schema_version": "cskh-p4-g18-drilldown-diagnostic-v1",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "app_image_tested": app_image,
                "local_image_id": img_insp.get("Id"),
                "repo_digests": img_insp.get("RepoDigests"),
                "resource_profile": {
                    "name": resource_profile,
                    "description": prof["description"],
                    "gunicorn_workers": prof["gunicorn_workers"],
                    "gunicorn_threads": prof["gunicorn_threads"],
                    "cgroup_capped": prof["cgroup_capped"],
                },
                "isolation_guard": isolation,
                "live_resource_protection_guard": live_guard,
                "in_container_diagnostics": in_clean,
                "api05b_stampede_reproduction_and_coalescing_verification": api05b_stampede_suite,
                "http_concurrency_and_saturation_waves": http_waves,
                "http_cross_access_status_codes": {
                    "staff_to_company_http_status": st_staff_comp,
                    "leader_to_company_http_status": st_leader_comp,
                },
                "queue_and_db_after": db_after,
            }
        finally:
            subprocess.run(
                [*compose_base, "down", "-v", "--remove-orphans"],
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app-image", required=True, help="Immutable @sha256 image digest")
    p.add_argument(
        "--resource-profile",
        default="s1_canonical_4x4",
        choices=sorted(RESOURCE_PROFILES),
    )
    p.add_argument(
        "--pull-policy",
        default="missing",
        choices=("never", "missing", "always"),
    )
    p.add_argument("--port", type=int, default=18890)
    p.add_argument("--site-name", default="p4-g18-diag.localhost")
    p.add_argument(
        "--no-live-resource-guard",
        action="store_true",
        help="Disable host memory/loadavg floor check",
    )
    p.add_argument(
        "--forbid-cohosted-live-containers",
        action="store_true",
        help="Fail closed if any live/production container name is running on the same Docker daemon",
    )
    p.add_argument("--out", required=True, help="Output JSON path")
    args = p.parse_args(argv)

    report = run_diagnostic(
        app_image=args.app_image.strip(),
        resource_profile=args.resource_profile,
        pull_policy=args.pull_policy,
        port=args.port,
        site_name=args.site_name,
        protect_live_resources=not args.no_live_resource_guard,
        forbid_cohosted_live_containers=args.forbid_cohosted_live_containers,
    )
    serialized = scrub_sensitive_tokens(json.dumps(report, indent=2) + "\n")
    if "sid=" in serialized.lower() and "sid=[redacted]" not in serialized.lower():
        raise RuntimeError(
            "Refusing to write diagnostic output containing unredacted sid="
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(serialized, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "OK",
                "app_image": report["app_image_tested"],
                "resource_profile": args.resource_profile,
                "out": str(out_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
