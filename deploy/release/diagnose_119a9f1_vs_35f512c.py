#!/usr/bin/env python3
"""Focused Isolated Staging A/B Forensic Probe (`119a9f1` / `caf6a8ef` vs `35f512c` / `d3e35f81`).

Separates code impact from environment / background-worker contention across the 4 slipped
SLO rules on `119a9f1` plus `API-01C` (`my_customers_worklist_api_c40`):
- `AGG-03C` (`mixed_user_sessions_80_overall`, target `p95 <= 3000 ms`, `p99 <= 3600 ms`)
- `API-05C-MIX80` (`kpi_drilldown_api` in `sessions_80`, target `p95 <= 2600 ms`, `p99 <= 3000 ms`)
- `API-00` (`health_ping` `/api/method/ping` at `c=12`, target `p95 <= 200 ms`, `p99 <= 300 ms`)
- `AGG-03B` (`mixed_user_sessions_40_overall`, target `p95 <= 1850 ms`, `p99 <= 2300 ms`)
- `API-01C` (`my_customers_worklist_api` at `c=40`, target `p95 <= 800 ms`, `p99 <= 1100 ms`)

All comparisons execute on the EXACT SAME isolated VM and seeded S1 dataset (`50,000` Leads,
`75,000` Work Items + `10,000` `today` distributed Work Items, `250,000` Contact Attempts,
`150,000`+ SLA Obligations, `40` active staff across `4` teams, published `monthly:2026-10`
and `daily:2026-10-27` KPI snapshots) with zero mutation to candidate branches or Owner VPS.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import itertools
import json
import multiprocessing
import os
import secrets
import socket
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from benchmark_p4_isolated import (
    LOCK_FILE,
    STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
    VERIFY_COMPOSE_FILE,
    _classify_http_statuses,
    _collect_docker_stats,
    _collect_queue_and_db_telemetry,
    _compose_files_arg,
    _http_call,
    _login_session,
    enforce_isolated_benchmark_target,
    enforce_live_resource_headroom,
    percentile,
    summarize_latencies_ms,
)
from diagnose_g18_drilldown import scrub_sensitive_tokens

DEFAULT_IMAGE_35F512C = (
    "ghcr.io/nobita6986/crm-cskh@sha256:d3e35f813b7de9b8bcd0ef5b03d2827efe8d8c78d2cdeef9c4aa8ee393096b2e"
)
DEFAULT_IMAGE_119A9F1 = (
    "ghcr.io/nobita6986/crm-cskh@sha256:caf6a8effb9ca59e788a3afc2512ba40865bbee536542fd6e5a28d4ad01c7761"
)

IN_CONTAINER_S1_SEED_SCRIPT = r'''
import gc
import json
import os
import time
from datetime import datetime, timedelta

import frappe
from frappe.utils.password import update_password

from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services.attribution import AUTHORITATIVE
from crm_cskh.services.kpi_runs import execute_kpi_recompute, resolve_period_spec


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


os.chdir("/home/frappe/frappe-bench/sites")
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
for c_idx in range(1, 7):
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

dist_camp_doc = frappe.get_doc(
    {
        "doctype": "CSKH Campaign",
        "code": "S1-CAMP-DIST-10K",
        "title": "Chiến dịch Phân bổ 10k S1",
        "status": "Active",
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-12-31 23:59:59",
        "rule_version": 1,
    }
)
dist_camp_doc.insert(ignore_permissions=True)
dist_camp = dist_camp_doc.name
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

http_open_work_items = []
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
            http_open_work_items.append({"work_item": wi_id, "owner": owner_u, "lead": lead_id})
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

# Also seed the 10,000 today-distributed Work Items + AH + SLA Obligations (matching post-BG-01 DB state in Official G18!)
now_today = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
dist_wi, dist_ah, dist_sla, dist_clm = [], [], [], []
for di in range(10_000):
    lead_id = f"S1-LEAD-{di:06d}"
    owner_u = staff_users[di % 40]
    team_u = staff_team_map[owner_u]
    wi_id = f"S1-DIST-WI-{di:05d}"
    ah_id = f"S1-DIST-AH-{di:05d}"
    due_ts = now_today + timedelta(hours=2)
    dist_wi.append(
        (
            wi_id, now_today, now_today, "Administrator", "Administrator", 0, 0,
            lead_id, dist_camp, "assigned", owner_u, now_today,
            None, None, None, 0, 0, None, None, None, None, 1,
        )
    )
    dist_ah.append(
        (
            ah_id, now_today, now_today, "Administrator", "Administrator", 0, 0,
            wi_id, "assign", None, owner_u, "Administrator", now_today, now_today, None,
            f"s1-dist-evt-{di:05d}", team_u, AUTHORITATIVE, "BG-01 S1",
        )
    )
    dist_sla.append(
        (
            f"S1-DIST-SLA-{di:05d}", now_today, now_today, "Administrator", "Administrator", 0, 0,
            "FIRST_ATTEMPT", wi_id, lead_id, dist_camp, ah_id, owner_u, team_u, AUTHORITATIVE,
            "open", 0, 0, 0, now_today, due_ts, due_ts, 0, None, None,
            None, None, None, "GLOBAL-DEFAULT", 1, f"s1-dist-sla-{di:05d}",
        )
    )
    dist_clm.append(
        (
            f"S1-DIST-CLM-{di:05d}", now_today, now_today, "Administrator", "Administrator", 0, 0,
            lead_id, dist_camp, wi_id,
        )
    )
_bulk_insert("tabCSKH Work Item", wi_cols, dist_wi)
_bulk_insert("tabCSKH Assignment History", ah_cols, dist_ah)
_bulk_insert("tabCSKH SLA Obligation", sla_cols, dist_sla)
_bulk_insert("tabCSKH Active Claim", claim_cols, claim_rows_all + dist_clm)
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
seed_elapsed_s = round(time.perf_counter() - t_seed0, 2)

gc.collect()
t_m0 = time.perf_counter()
month_spec = resolve_period_spec("monthly", "2026-10")
execute_kpi_recompute(month_spec, run_mode="full", actor="Administrator")
frappe.db.commit()
monthly_recompute_ms = round((time.perf_counter() - t_m0) * 1000.0, 2)

t_d0 = time.perf_counter()
day_spec = resolve_period_spec("daily", "2026-10-27")
execute_kpi_recompute(day_spec, run_mode="incremental", actor="Administrator")
frappe.db.commit()
daily_recompute_ms = round((time.perf_counter() - t_d0) * 1000.0, 2)

frappe.db.sql("ANALYZE TABLE `tabCSKH KPI Snapshot`")
frappe.db.commit()
frappe.destroy()

print(
    "AB_S1_SEED_JSON:"
    + json.dumps(
        {
            "seed_elapsed_sec": seed_elapsed_s,
            "monthly_recompute_ms": monthly_recompute_ms,
            "daily_recompute_ms": daily_recompute_ms,
            "staff_users": staff_users,
            "leader_users": leader_users,
            "bod_user": bod_user,
            "primary_campaign_id": campaigns[0],
            "dist_campaign_id": dist_camp,
            "http_open_work_items": http_open_work_items,
        }
    )
)
'''

DIAG_WSGI_MODULE = r'''
import os
import time
import frappe
import frappe.app
from frappe.database.mariadb.database import MariaDBDatabase

_orig_sql = MariaDBDatabase.sql
_orig_get_conn = MariaDBDatabase.get_connection


def _timed_get_conn(self, *args, **kwargs):
    t0 = time.perf_counter()
    res = _orig_get_conn(self, *args, **kwargs)
    dt = (time.perf_counter() - t0) * 1000.0
    tl = getattr(frappe.local, "_cskh_diag", None)
    if tl is not None:
        tl["db_conn_ms"] += dt
    return res


def _timed_sql(self, query, values=(), *args, **kwargs):
    t0 = time.perf_counter()
    res = _orig_sql(self, query, values, *args, **kwargs)
    dt = (time.perf_counter() - t0) * 1000.0
    tl = getattr(frappe.local, "_cskh_diag", None)
    if tl is not None:
        tl["sql_ms"] += dt
        tl["sql_count"] += 1
    return res


MariaDBDatabase.get_connection = _timed_get_conn
MariaDBDatabase.sql = _timed_sql


def application(environ, start_response):
    t0 = time.perf_counter()
    diag = {"db_conn_ms": 0.0, "sql_ms": 0.0, "sql_count": 0}
    frappe.local._cskh_diag = diag
    pid = str(os.getpid())

    def _wrapped_start_response(status, headers, exc_info=None):
        wsgi_ms = (time.perf_counter() - t0) * 1000.0
        extra = [
            ("X-Diag-Pid", pid),
            ("X-Diag-Wsgi-Ms", f"{wsgi_ms:.3f}"),
            ("X-Diag-DbConn-Ms", f"{diag['db_conn_ms']:.3f}"),
            ("X-Diag-Sql-Ms", f"{diag['sql_ms']:.3f}"),
            ("X-Diag-Sql-Count", str(diag["sql_count"])),
        ]
        return start_response(status, list(headers) + extra, exc_info)

    return frappe.app.application(environ, _wrapped_start_response)
'''


def _cpu_burn_worker(stop_evt: multiprocessing.Event) -> None:  # type: ignore[name-defined]
    x = 1.0001
    while not stop_evt.is_set():
        for _ in range(50_000):
            x = (x * 1.000001) % 9.99999


def _read_cgroup_cpu_stat(compose_base: list[str], compose_env: dict[str, str], svc: str) -> dict[str, int]:
    proc = subprocess.run(
        [*compose_base, "exec", "-T", svc, "cat", "/sys/fs/cgroup/cpu.stat"],
        env=compose_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    out: dict[str, int] = {}
    if proc.returncode == 0:
        for ln in proc.stdout.splitlines():
            parts = ln.strip().split()
            if len(parts) == 2 and parts[1].isdigit():
                out[parts[0]] = int(parts[1])
    return out


def _diff_cpu_stat(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        k: int(after.get(k, 0)) - int(before.get(k, 0))
        for k in ("usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec")
        if k in after or k in before
    }


def _http_call_diag(
    *,
    base_url: str,
    site_name: str,
    cookie_header: str,
    path: str,
    method: str = "GET",
    json_body: dict[str, object] | None = None,
    csrf_token: str = "",
    timeout: float = 30.0,
) -> dict[str, object]:
    hdrs: dict[str, str] = {"Host": site_name, "Cookie": cookie_header}
    payload: bytes | None = None
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if csrf_token:
        hdrs["X-Frappe-CSRF-Token"] = csrf_token
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        headers=hdrs,
        method=method,
    )
    t0 = time.perf_counter()
    resp_hdrs: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = int(r.status)
            resp_hdrs = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        status = int(exc.code)
        resp_hdrs = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
    except (TimeoutError, socket.timeout):
        body = b""
        status = 0
    except urllib.error.URLError as exc:
        body = b""
        status = 0 if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)) else -1
    except Exception:
        body = b""
        status = -1
    client_ms = (time.perf_counter() - t0) * 1000.0
    wsgi_ms = float(resp_hdrs.get("x-diag-wsgi-ms") or 0.0)
    sql_ms = float(resp_hdrs.get("x-diag-sql-ms") or 0.0)
    sql_cnt = int(resp_hdrs.get("x-diag-sql-count") or 0)
    pid = resp_hdrs.get("x-diag-pid") or ""
    return {
        "client_ms": round(client_ms, 3),
        "wsgi_ms": round(wsgi_ms, 3),
        "queue_wait_ms": round(max(0.0, client_ms - wsgi_ms), 3) if wsgi_ms > 0 else None,
        "sql_ms": round(sql_ms, 3),
        "sql_count": sql_cnt,
        "pid": pid,
        "status": status,
        "has_attempt_id": b"attempt_id" in body,
    }


def _wait_http_ready(base_url: str, site_name: str, timeout_sec: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/api/method/ping", headers={"Host": site_name})
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"Backend HTTP readiness timed out on {base_url}")


def run_ab_probe(
    *,
    image_35f512c: str,
    image_119a9f1: str,
    pull_policy: str = "never",
    port: int = 18894,
) -> dict[str, object]:
    site_name = "p4-s1-ab.localhost"
    compose_project = f"cskh-p4-bench-ab{secrets.token_hex(3)}"
    base_url = f"http://127.0.0.1:{port}"
    isolation = enforce_isolated_benchmark_target(
        base_url=base_url,
        site_name=site_name,
        compose_project=compose_project,
        confirm_isolated_target=True,
    )
    live_headroom = enforce_live_resource_headroom()
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    db_root_pw = secrets.token_hex(24)
    admin_pw = secrets.token_hex(24)
    actor_pw = secrets.token_hex(20)

    with tempfile.TemporaryDirectory(prefix="cskh-p4-ab-") as tmp_str:
        tmp_dir = Path(tmp_str)
        db_secret = tmp_dir / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
        bench_override = tmp_dir / "compose.bench-override.yaml"
        bench_override.write_text(STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML, encoding="utf-8")
        base_files = [VERIFY_COMPOSE_FILE, bench_override]

        def _make_env(app_image: str) -> dict[str, str]:
            return {
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

        compose_env = _make_env(image_119a9f1)
        compose_base = ["docker", "compose", *_compose_files_arg(base_files), "-p", compose_project]

        def _run_checked(cmd: list[str], *, env: dict[str, str], label: str, input_text: str | None = None, timeout_sec: int = 300) -> subprocess.CompletedProcess[str]:
            proc = subprocess.run(
                cmd,
                env=env,
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"{label} failed (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout[-1800:]}\nSTDERR:\n{proc.stderr[-1800:]}"
                )
            return proc

        try:
            _run_checked([*compose_base, "up", "-d", "--no-build"], env=compose_env, label="compose up", timeout_sec=300)

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
            _run_checked(
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
                label="site bootstrap",
                timeout_sec=600,
            )

            # Stop queue-short initially so seeding & clean regimes have deterministic 0% background RQ noise
            subprocess.run([*compose_base, "stop", "queue-short", "scheduler"], env=compose_env, check=False, capture_output=True)

            seed_proc = _run_checked(
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
                env=compose_env,
                label="S1 seed + KPI snapshots",
                input_text=IN_CONTAINER_S1_SEED_SCRIPT,
                timeout_sec=900,
            )
            marker = [
                ln[len("AB_S1_SEED_JSON:") :]
                for ln in seed_proc.stdout.splitlines()
                if ln.startswith("AB_S1_SEED_JSON:")
            ]
            if not marker:
                raise RuntimeError("Missing AB_S1_SEED_JSON marker")
            seeded = json.loads(marker[-1])

            # Write diagnostic WSGI wrapper onto shared sites volume
            write_diag = (
                "import pathlib, sys; "
                "d = pathlib.Path('/home/frappe/frappe-bench/sites/_diag_ab'); d.mkdir(parents=True, exist_ok=True); "
                "(d / 'cskh_diag_wsgi.py').write_text(sys.stdin.read(), encoding='utf-8')"
            )
            _run_checked(
                [*compose_base, "exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", write_diag],
                env=compose_env,
                label="write diag wsgi",
                input_text=DIAG_WSGI_MODULE,
                timeout_sec=30,
            )

            _run_checked(
                [*compose_base, "up", "-d", "--no-build", "--force-recreate", "backend", "frontend"],
                env=compose_env,
                label="recreate initial backend/frontend",
                timeout_sec=120,
            )
            _wait_http_ready(base_url, site_name)

            # Authenticate Administrator + 80 user sessions (persisted in redis-cache & MariaDB across backend swaps)
            admin_sess = _login_session(
                base_url=base_url,
                site_name=site_name,
                user="Administrator",
                password=admin_pw,
                fetch_csrf=False,
            )
            staff_users = list(seeded["staff_users"])
            leader_users = list(seeded["leader_users"])
            bod_user = str(seeded["bod_user"])
            primary_camp = str(seeded["primary_campaign_id"])
            open_wis = list(seeded["http_open_work_items"])
            detail_wis = open_wis[:160]
            mixed_write_pool = open_wis[160:]
            write_cursor = itertools.count(0)
            future_due_str = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 10:00:00")

            login_targets: list[tuple[str, bool]] = []
            for u in staff_users:
                login_targets.append((str(u), True))
            for u in staff_users[:34]:
                login_targets.append((str(u), False))
            for u in leader_users[:4]:
                login_targets.append((str(u), False))
            login_targets.append((bod_user, False))
            login_targets.append((bod_user, False))

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                sessions_80 = list(
                    pool.map(
                        lambda pair: _login_session(
                            base_url=base_url,
                            site_name=site_name,
                            user=pair[0],
                            password=actor_pw,
                            fetch_csrf=pair[1],
                        ),
                        login_targets,
                    )
                )
            staff_session_by_user = {s["user"]: s for s in sessions_80[:40]}
            bod_session = sessions_80[-1]

            def _reset_dirty_scopes_and_rq(cbase_now: list[str], env: dict[str, str]) -> None:
                cleanup_py = (
                    "import os, frappe, redis; "
                    "from frappe.utils.background_jobs import generate_qname; "
                    "os.chdir('/home/frappe/frappe-bench/sites'); "
                    f"frappe.init(site={site_name!r}, sites_path='/home/frappe/frappe-bench/sites'); "
                    "frappe.connect(); "
                    "frappe.db.sql(\"DELETE FROM `tabCSKH Dirty Scope` WHERE status IN ('pending', 'processing')\"); "
                    "frappe.db.commit(); "
                    "r = redis.from_url(frappe.conf.redis_queue); "
                    "r.delete(f'rq:queue:{generate_qname(\"short\")}'); "
                    "frappe.cache.delete(frappe.cache.make_key('cskh:kpi:dirty_worker_enqueued')); "
                    "frappe.destroy()"
                )
                subprocess.run(
                    [*cbase_now, "exec", "-T", "-e", "FRAPPE_STREAM_LOGGING=1", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", cleanup_py],
                    env=env,
                    check=False,
                    capture_output=True,
                    timeout=30,
                )

            def _trigger_busy_qshort_recompute(cbase_now: list[str], env: dict[str, str]) -> None:
                trig_py = (
                    "import os, frappe; "
                    "from crm_cskh.services import kpi_runs; "
                    "os.chdir('/home/frappe/frappe-bench/sites'); "
                    f"frappe.init(site={site_name!r}, sites_path='/home/frappe/frappe-bench/sites'); "
                    "frappe.connect(); "
                    "frappe.cache.delete(frappe.cache.make_key('cskh:kpi:dirty_worker_enqueued')); "
                    f"kpi_runs.schedule_post_commit_dirty_invalidation(campaign={primary_camp!r}, event_dt='2026-10-27 10:00:00', reason='operational_mutation'); "
                    f"kpi_runs.schedule_post_commit_dirty_invalidation(campaign={seeded['dist_campaign_id']!r}, reason='operational_mutation'); "
                    "kpi_runs._flush_pending_dirty_events(); "
                    "frappe.db.commit(); "
                    "frappe.destroy()"
                )
                _run_checked(
                    [*cbase_now, "exec", "-T", "-e", "FRAPPE_STREAM_LOGGING=1", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", trig_py],
                    env=env,
                    label="trigger busy queue-short dirty scope recompute",
                    timeout_sec=30,
                )

            def _switch_backend(
                *,
                app_image: str,
                backend_cpus: float,
                env_overrides: dict[str, str] | None = None,
                diag_wsgi: bool = False,
                custom_worker_class: str | None = None,
                custom_keepalive: int | None = None,
            ) -> tuple[list[str], dict[str, str]]:
                env_now = _make_env(app_image)
                ov_lines = [
                    "services:",
                    "  backend:",
                    f"    cpus: {backend_cpus:.2f}",
                    "    environment:",
                    '      GUNICORN_WORKERS: "2"',
                    '      GUNICORN_THREADS: "4"',
                    '      GUNICORN_TIMEOUT: "120"',
                    '      TZ: Asia/Ho_Chi_Minh',
                ]
                for ek, ev in (env_overrides or {}).items():
                    ov_lines.append(f'      {ek}: "{ev}"')
                if diag_wsgi:
                    wc = custom_worker_class or "gthread"
                    ka = custom_keepalive if custom_keepalive is not None else 2
                    cmd_list = [
                        "/home/frappe/frappe-bench/env/bin/gunicorn",
                        "--chdir=/home/frappe/frappe-bench/sites",
                        "--pythonpath=/home/frappe/frappe-bench/sites/_diag_ab",
                        "--bind=0.0.0.0:8000",
                        "--threads=4",
                        "--workers=2",
                        f"--worker-class={wc}",
                        f"--keep-alive={ka}",
                        "--worker-tmp-dir=/dev/shm",
                        "--timeout=120",
                        "--preload",
                        "cskh_diag_wsgi:application",
                    ]
                    ov_lines.append("    command: " + json.dumps(cmd_list))
                ov_path = tmp_dir / "compose.ab-backend.yaml"
                ov_path.write_text("\n".join(ov_lines) + "\n", encoding="utf-8")
                files_now = [*base_files, ov_path]
                cbase_now = ["docker", "compose", *_compose_files_arg(files_now), "-p", compose_project]
                _run_checked(
                    [*cbase_now, "up", "-d", "--no-build", "--force-recreate", "backend", "frontend"],
                    env=env_now,
                    label=f"switch backend ({app_image[-16:]}, cpus={backend_cpus})",
                    timeout_sec=120,
                )
                _wait_http_ready(base_url, site_name)
                # Warm all worker threads + DB/Redis connections
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as wp:
                    list(
                        wp.map(
                            lambda _: _http_call(
                                base_url=base_url,
                                site_name=site_name,
                                cookie_header=admin_sess["cookie"],
                                path="/api/method/ping",
                            ),
                            range(24),
                        )
                    )
                    list(
                        wp.map(
                            lambda idx: _http_call(
                                base_url=base_url,
                                site_name=site_name,
                                cookie_header=sessions_80[idx % 40]["cookie"],
                                path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                            ),
                            range(16),
                        )
                    )
                    list(
                        wp.map(
                            lambda _: _http_call(
                                base_url=base_url,
                                site_name=site_name,
                                cookie_header=bod_session["cookie"],
                                path=(
                                    "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                                    f"?period_type=daily&period_key=2026-10-27&scope_type=campaign"
                                    f"&scope_id={urllib.parse.quote(primary_camp)}&metric_code=CONNECTED_RATE&page=1&page_size=20"
                                ),
                            ),
                            range(8),
                        )
                    )
                time.sleep(0.5)
                return cbase_now, env_now

            def _run_single_scenario_diag(
                *,
                cookie_fn,
                path: str,
                concurrency: int,
                requests_total: int,
            ) -> dict[str, object]:
                t0 = time.perf_counter()
                recs: list[dict[str, object]] = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futs = [
                        pool.submit(
                            _http_call_diag,
                            base_url=base_url,
                            site_name=site_name,
                            cookie_header=cookie_fn(i),
                            path=path,
                        )
                        for i in range(requests_total)
                    ]
                    for fut in concurrent.futures.as_completed(futs):
                        recs.append(fut.result())
                elapsed = time.perf_counter() - t0
                lats = [float(r["client_ms"]) for r in recs]
                errs = sum(1 for r in recs if int(r["status"]) != 200)
                pids: dict[str, int] = {}
                wsgi_vals = [float(r["wsgi_ms"]) for r in recs if float(r["wsgi_ms"]) > 0]
                qw_vals = [float(r["queue_wait_ms"]) for r in recs if r["queue_wait_ms"] is not None]
                sql_vals = [float(r["sql_ms"]) for r in recs if float(r["wsgi_ms"]) > 0]
                for r in recs:
                    p = str(r["pid"])
                    if p:
                        pids[p] = pids.get(p, 0) + 1
                out: dict[str, object] = {
                    **summarize_latencies_ms(lats, error_count=errs, elapsed_sec=elapsed),
                }
                if wsgi_vals:
                    out["diag_decomposition"] = {
                        "pid_counts": pids,
                        "wsgi_p50_ms": percentile(sorted(wsgi_vals), 50.0),
                        "wsgi_p95_ms": percentile(sorted(wsgi_vals), 95.0),
                        "queue_wait_p50_ms": percentile(sorted(qw_vals), 50.0),
                        "queue_wait_p95_ms": percentile(sorted(qw_vals), 95.0),
                        "sql_p50_ms": percentile(sorted(sql_vals), 50.0),
                        "sql_p95_ms": percentile(sorted(sql_vals), 95.0),
                    }
                return out

            def _run_mixed_wave_once(
                *,
                wave_sessions_count: int,
                total_ops: int,
                cbase_now: list[str],
                env_now: dict[str, str],
            ) -> dict[str, object]:
                active_pool = sessions_80[:wave_sessions_count]

                def _op(op_idx: int) -> dict[str, object]:
                    bucket = op_idx % 20
                    if bucket < 9:
                        sess = active_pool[op_idx % min(wave_sessions_count, 40)]
                        r = _http_call_diag(
                            base_url=base_url,
                            site_name=site_name,
                            cookie_header=sess["cookie"],
                            path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                        )
                        return {"kind": "my_customers_api", "ok": r["status"] == 200, **r}
                    if bucket < 14:
                        item = detail_wis[op_idx % len(detail_wis)]
                        wi_id = urllib.parse.quote(str(item["work_item"]))
                        sess = staff_session_by_user[str(item["owner"])]
                        r1 = _http_call_diag(
                            base_url=base_url,
                            site_name=site_name,
                            cookie_header=sess["cookie"],
                            path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={wi_id}",
                        )
                        r2 = _http_call_diag(
                            base_url=base_url,
                            site_name=site_name,
                            cookie_header=sess["cookie"],
                            path=f"/api/method/crm_cskh.api.workflow.list_attempts?work_item={wi_id}&page=1&page_size=20",
                        )
                        c_ms = float(r1["client_ms"]) + float(r2["client_ms"])
                        w_ms = float(r1["wsgi_ms"]) + float(r2["wsgi_ms"])
                        s_ms = float(r1["sql_ms"]) + float(r2["sql_ms"])
                        ok = int(r1["status"]) == 200 and int(r2["status"]) == 200
                        return {
                            "kind": "lead_detail_history_api",
                            "ok": ok,
                            "client_ms": round(c_ms, 3),
                            "wsgi_ms": round(w_ms, 3),
                            "queue_wait_ms": round(max(0.0, c_ms - w_ms), 3) if w_ms > 0 else None,
                            "sql_ms": round(s_ms, 3),
                            "sql_count": int(r1["sql_count"]) + int(r2["sql_count"]),
                            "pid": str(r1["pid"]),
                            "status": 200 if ok else max(int(r1["status"]), int(r2["status"])),
                        }
                    if bucket < 17:
                        r = _http_call_diag(
                            base_url=base_url,
                            site_name=site_name,
                            cookie_header=bod_session["cookie"],
                            path="/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key=2026-10",
                        )
                        return {"kind": "bod_overview_api", "ok": r["status"] == 200, **r}
                    if bucket < 19:
                        r = _http_call_diag(
                            base_url=base_url,
                            site_name=site_name,
                            cookie_header=bod_session["cookie"],
                            path=(
                                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                                f"?period_type=daily&period_key=2026-10-27&scope_type=campaign"
                                f"&scope_id={urllib.parse.quote(primary_camp)}&metric_code=CONNECTED_RATE&page=1&page_size=20"
                            ),
                        )
                        return {"kind": "kpi_drilldown_api", "ok": r["status"] == 200, **r}
                    w_slot = next(write_cursor)
                    item = mixed_write_pool[w_slot % len(mixed_write_pool)]
                    sess = staff_session_by_user[str(item["owner"])]
                    r = _http_call_diag(
                        base_url=base_url,
                        site_name=site_name,
                        cookie_header=sess["cookie"],
                        csrf_token=sess["csrf_token"],
                        path="/api/method/crm_cskh.api.workflow.record_attempt",
                        method="POST",
                        json_body={
                            "work_item": str(item["work_item"]),
                            "channel": "phone_call",
                            "outcome_code": "no_answer",
                            "note": f"AB mix c{wave_sessions_count} slot {w_slot}",
                            "followup_due_at": future_due_str,
                            "followup_title": f"AB cb c{wave_sessions_count}-{w_slot}",
                            "idempotency_key": f"ab-mix-c{wave_sessions_count}-{w_slot:06d}",
                        },
                    )
                    return {
                        "kind": "record_attempt_write_api",
                        "ok": int(r["status"]) == 200 and bool(r["has_attempt_id"]),
                        **r,
                    }

                cpu_b = _read_cgroup_cpu_stat(cbase_now, env_now, "backend")
                cpu_db_b = _read_cgroup_cpu_stat(cbase_now, env_now, "db")
                t0 = time.perf_counter()
                recs: list[dict[str, object]] = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=wave_sessions_count) as pool:
                    futs = [pool.submit(_op, i) for i in range(total_ops)]
                    mid_stats = _collect_docker_stats(compose_project, compose_file=base_files, env=env_now)
                    for fut in concurrent.futures.as_completed(futs):
                        recs.append(fut.result())
                elapsed = time.perf_counter() - t0
                cpu_a = _read_cgroup_cpu_stat(cbase_now, env_now, "backend")
                cpu_db_a = _read_cgroup_cpu_stat(cbase_now, env_now, "db")

                all_lats = [float(r["client_ms"]) for r in recs]
                all_errs = sum(1 for r in recs if not bool(r["ok"]))
                by_kind: dict[str, list[dict[str, object]]] = {}
                for r in recs:
                    by_kind.setdefault(str(r["kind"]), []).append(r)

                by_workload: dict[str, object] = {}
                for k, rows in sorted(by_kind.items()):
                    klats = [float(x["client_ms"]) for x in rows]
                    kerrs = sum(1 for x in rows if not bool(x["ok"]))
                    kw = [float(x["wsgi_ms"]) for x in rows if float(x["wsgi_ms"]) > 0]
                    kqw = [float(x["queue_wait_ms"]) for x in rows if x["queue_wait_ms"] is not None]
                    ksql = [float(x["sql_ms"]) for x in rows if float(x["wsgi_ms"]) > 0]
                    kentry: dict[str, object] = {
                        **summarize_latencies_ms(klats, error_count=kerrs, elapsed_sec=elapsed),
                    }
                    if kw:
                        kentry["diag_decomposition"] = {
                            "wsgi_p50_ms": percentile(sorted(kw), 50.0),
                            "wsgi_p95_ms": percentile(sorted(kw), 95.0),
                            "queue_wait_p50_ms": percentile(sorted(kqw), 50.0),
                            "queue_wait_p95_ms": percentile(sorted(kqw), 95.0),
                            "sql_p50_ms": percentile(sorted(ksql), 50.0),
                            "sql_p95_ms": percentile(sorted(ksql), 95.0),
                        }
                    by_workload[k] = kentry

                return {
                    "overall_summary": summarize_latencies_ms(all_lats, error_count=all_errs, elapsed_sec=elapsed),
                    "by_workload": by_workload,
                    "backend_cpu_stat_delta": _diff_cpu_stat(cpu_b, cpu_a),
                    "db_cpu_stat_delta": _diff_cpu_stat(cpu_db_b, cpu_db_a),
                    "docker_stats_mid_wave": mid_stats,
                }

            def _run_focus_bundle(
                *,
                cbase_now: list[str],
                env_now: dict[str, str],
                clean_dirty_after: bool = True,
            ) -> dict[str, object]:
                cpu_b0 = _read_cgroup_cpu_stat(cbase_now, env_now, "backend")
                api00 = _run_single_scenario_diag(
                    cookie_fn=lambda _: admin_sess["cookie"],
                    path="/api/method/ping",
                    concurrency=12,
                    requests_total=240,
                )
                cpu_b1 = _read_cgroup_cpu_stat(cbase_now, env_now, "backend")
                api00["backend_cpu_stat_delta"] = _diff_cpu_stat(cpu_b0, cpu_b1)

                api01c = _run_single_scenario_diag(
                    cookie_fn=lambda _: admin_sess["cookie"],
                    path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                    concurrency=40,
                    requests_total=160,
                )
                cpu_b2 = _read_cgroup_cpu_stat(cbase_now, env_now, "backend")
                api01c["backend_cpu_stat_delta"] = _diff_cpu_stat(cpu_b1, cpu_b2)

                mix40 = _run_mixed_wave_once(
                    wave_sessions_count=40,
                    total_ops=320,
                    cbase_now=cbase_now,
                    env_now=env_now,
                )
                mix80 = _run_mixed_wave_once(
                    wave_sessions_count=80,
                    total_ops=480,
                    cbase_now=cbase_now,
                    env_now=env_now,
                )
                qdb = _collect_queue_and_db_telemetry(compose_project, site_name, compose_file=base_files, env=env_now)
                if clean_dirty_after:
                    _reset_dirty_scopes_and_rq(cbase_now, env_now)
                return {
                    "API_00_health_ping_c12": api00,
                    "API_01C_my_customers_c40": api01c,
                    "AGG_03B_sessions_40": mix40,
                    "AGG_03C_sessions_80": mix80,
                    "API_05C_MIX80": mix80["by_workload"]["kpi_drilldown_api"],
                    "API_02B_MIX80_lead_detail": mix80["by_workload"]["lead_detail_history_api"],
                    "queue_and_db_after": qdb,
                }

            def _summarize_trials(trials: list[dict[str, object]]) -> dict[str, object]:
                def _med(vals: list[float]) -> float:
                    return round(statistics.median(vals), 3) if vals else 0.0

                return {
                    "trial_count": len(trials),
                    "median_API_00_p95_ms": _med([float(t["API_00_health_ping_c12"]["p95_ms"]) for t in trials]),
                    "median_API_00_rps": _med([float(t["API_00_health_ping_c12"]["throughput_rps"]) for t in trials]),
                    "median_API_01C_p95_ms": _med([float(t["API_01C_my_customers_c40"]["p95_ms"]) for t in trials]),
                    "median_API_01C_rps": _med([float(t["API_01C_my_customers_c40"]["throughput_rps"]) for t in trials]),
                    "median_AGG_03B_p95_ms": _med([float(t["AGG_03B_sessions_40"]["overall_summary"]["p95_ms"]) for t in trials]),
                    "median_AGG_03B_rps": _med([float(t["AGG_03B_sessions_40"]["overall_summary"]["throughput_rps"]) for t in trials]),
                    "median_AGG_03C_p95_ms": _med([float(t["AGG_03C_sessions_80"]["overall_summary"]["p95_ms"]) for t in trials]),
                    "median_AGG_03C_rps": _med([float(t["AGG_03C_sessions_80"]["overall_summary"]["throughput_rps"]) for t in trials]),
                    "median_API_05C_MIX80_p95_ms": _med([float(t["API_05C_MIX80"]["p95_ms"]) for t in trials]),
                    "median_API_02B_MIX80_p95_ms": _med([float(t["API_02B_MIX80_lead_detail"]["p95_ms"]) for t in trials]),
                    "trials": trials,
                }

            # =========================================================================
            # PHASE 1: Official `1.50 vCPU` (`staging_vps_capped`) interleaved A/B/B/A
            # =========================================================================
            trials_35f_150: list[dict[str, object]] = []
            trials_119_150: list[dict[str, object]] = []

            cb, ev = _switch_backend(app_image=image_35f512c, backend_cpus=1.50)
            trials_35f_150.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            cb, ev = _switch_backend(app_image=image_119a9f1, backend_cpus=1.50)
            trials_119_150.append(_run_focus_bundle(cbase_now=cb, env_now=ev))
            trials_119_150.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            cb, ev = _switch_backend(app_image=image_35f512c, backend_cpus=1.50)
            trials_35f_150.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            p1_35f = _summarize_trials(trials_35f_150)
            p1_119 = _summarize_trials(trials_119_150)

            # Calibrate `backend_cpus` so `35f512c` (`d3e35f81`) matches Real VPS `35f512c` MIX80 throughput (`57.78 rps`)
            ci_mix80_rps = float(p1_35f["median_AGG_03C_rps"])
            vps_target_mix80_rps = 56.0
            vps_cpus = round(max(0.32, min(1.50, 1.50 * (vps_target_mix80_rps / max(1.0, ci_mix80_rps)))), 2)

            # =========================================================================
            # PHASE 2: VPS-Calibrated CPU (`~56 rps` MIX80), `queue-short` idle (`35f512c` parity),
            #          interleaved A -> B -> A -> B -> B -> A (`3` trials each!)
            # =========================================================================
            trials_35f_vps: list[dict[str, object]] = []
            trials_119_vps: list[dict[str, object]] = []

            cb, ev = _switch_backend(app_image=image_35f512c, backend_cpus=vps_cpus)
            trials_35f_vps.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            cb, ev = _switch_backend(app_image=image_119a9f1, backend_cpus=vps_cpus)
            trials_119_vps.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            cb, ev = _switch_backend(app_image=image_35f512c, backend_cpus=vps_cpus)
            trials_35f_vps.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            cb, ev = _switch_backend(app_image=image_119a9f1, backend_cpus=vps_cpus)
            trials_119_vps.append(_run_focus_bundle(cbase_now=cb, env_now=ev))
            trials_119_vps.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            cb, ev = _switch_backend(app_image=image_35f512c, backend_cpus=vps_cpus)
            trials_35f_vps.append(_run_focus_bundle(cbase_now=cb, env_now=ev))

            p2_35f_vps = _summarize_trials(trials_35f_vps)
            p2_119_vps = _summarize_trials(trials_119_vps)

            # Factor isolation on `119a9f1` (`caf6a8ef`) at `vps_cpus`:
            # - `keepalive=2, m=4`
            # - `keepalive=2, m=6`
            cb, ev = _switch_backend(
                app_image=image_119a9f1,
                backend_cpus=vps_cpus,
                env_overrides={"GUNICORN_KEEPALIVE": "2", "CSKH_GUNICORN_MAX_INFLIGHT": "4"},
            )
            p2_119_ka2_m4 = _summarize_trials([_run_focus_bundle(cbase_now=cb, env_now=ev) for _ in range(2)])

            cb, ev = _switch_backend(
                app_image=image_119a9f1,
                backend_cpus=vps_cpus,
                env_overrides={"GUNICORN_KEEPALIVE": "2", "CSKH_GUNICORN_MAX_INFLIGHT": "6"},
            )
            p2_119_ka2_m6 = _summarize_trials([_run_focus_bundle(cbase_now=cb, env_now=ev) for _ in range(2)])

            # =========================================================================
            # PHASE 3: Instrumented Queue-Wait vs Server-WSGI/SQL Decomposition at `vps_cpus`
            # =========================================================================
            cb, ev = _switch_backend(
                app_image=image_119a9f1,
                backend_cpus=vps_cpus,
                diag_wsgi=True,
                custom_worker_class="gthread",
                custom_keepalive=2,
            )
            p3_diag_stock_gthread = _run_focus_bundle(cbase_now=cb, env_now=ev)

            cb, ev = _switch_backend(
                app_image=image_119a9f1,
                backend_cpus=vps_cpus,
                env_overrides={"CSKH_GUNICORN_MAX_INFLIGHT": "4"},
                diag_wsgi=True,
                custom_worker_class="crm_cskh.gunicorn_worker.FairGthreadWorker",
                custom_keepalive=0,
            )
            p3_diag_fair_m4 = _run_focus_bundle(cbase_now=cb, env_now=ev)

            # =========================================================================
            # PHASE 4: Environment Contention Isolation on Unmodified `119a9f1` (`caf6a8ef`) at `vps_cpus`
            #   4A: Busy `queue-short` running `process_dirty_scopes` (50% CPU + `DOMAIN_KPI_SNAPSHOT` bump)
            #   4B: Busy `queue-short` + Host vCPU contention (simulating shared 6-vCPU VPS `loadavg_1m = 3.1-4.2`)
            # =========================================================================
            cb, ev = _switch_backend(app_image=image_119a9f1, backend_cpus=vps_cpus)
            _run_checked([*cb, "up", "-d", "--no-build", "queue-short"], env=ev, label="start queue-short", timeout_sec=60)
            _trigger_busy_qshort_recompute(cb, ev)
            time.sleep(1.5)
            p4a_busy_qshort = _run_focus_bundle(cbase_now=cb, env_now=ev, clean_dirty_after=False)

            # Start host CPU contention workers (2 * cpu_count workers saturating host scheduler runqueue)
            ncpu = os.cpu_count() or 4
            stop_burn = multiprocessing.Event()
            burners = [
                multiprocessing.Process(target=_cpu_burn_worker, args=(stop_burn,), daemon=True)
                for _ in range(ncpu * 2)
            ]
            for bp in burners:
                bp.start()
            try:
                _trigger_busy_qshort_recompute(cb, ev)
                time.sleep(2.5)
                load_during_burn = round(os.getloadavg()[0], 3) if hasattr(os, "getloadavg") else None
                p4b_busy_qshort_plus_host_contention = _run_focus_bundle(
                    cbase_now=cb,
                    env_now=ev,
                    clean_dirty_after=False,
                )
                p4b_busy_qshort_plus_host_contention["host_load_1m_during_contention"] = load_during_burn
            finally:
                stop_burn.set()
                for bp in burners:
                    bp.join(timeout=5.0)

            return {
                "schema_version": "cskh-p4-119a9f1-vs-35f512c-isolated-ab-forensic-v1",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "image_35f512c": image_35f512c,
                "image_119a9f1": image_119a9f1,
                "isolation_guard": isolation,
                "live_headroom": live_headroom,
                "runner_cpu_count": os.cpu_count() or 4,
                "s1_seed_summary": {
                    k: v
                    for k, v in seeded.items()
                    if k not in ("staff_users", "leader_users", "http_open_work_items")
                },
                "vps_calibration": {
                    "ci_1_50cpu_35f512c_mix80_rps": ci_mix80_rps,
                    "vps_35f512c_reference_mix80_rps": 57.778,
                    "calibrated_backend_cpus": vps_cpus,
                },
                "phase1_official_1_50cpu_clean_interleaved": {
                    "image_35f512c_d3e35f81": p1_35f,
                    "image_119a9f1_caf6a8ef": p1_119,
                },
                "phase2_vps_calibrated_clean_interleaved": {
                    "image_35f512c_d3e35f81": p2_35f_vps,
                    "image_119a9f1_caf6a8ef": p2_119_vps,
                    "variant_caf6a8ef_keepalive2_m4": p2_119_ka2_m4,
                    "variant_caf6a8ef_keepalive2_m6": p2_119_ka2_m6,
                },
                "phase3_queue_wait_vs_wsgi_decomposition_vps_calibrated": {
                    "stock_gthread_keepalive2": p3_diag_stock_gthread,
                    "fair_gthread_m4_keepalive0": p3_diag_fair_m4,
                },
                "phase4_environment_contention_isolation_vps_calibrated": {
                    "busy_queue_short_dirty_recompute_only": p4a_busy_qshort,
                    "busy_queue_short_plus_host_vcpu_contention": p4b_busy_qshort_plus_host_contention,
                },
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image-35f512c", default=DEFAULT_IMAGE_35F512C)
    ap.add_argument("--image-119a9f1", default=DEFAULT_IMAGE_119A9F1)
    ap.add_argument("--pull-policy", default="never", choices=("never", "missing", "always"))
    ap.add_argument("--port", type=int, default=18894)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rpt = run_ab_probe(
        image_35f512c=args.image_35f512c.strip(),
        image_119a9f1=args.image_119a9f1.strip(),
        pull_policy=args.pull_policy,
        port=args.port,
    )
    serialized = scrub_sensitive_tokens(json.dumps(rpt, indent=2) + "\n")
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(serialized, encoding="utf-8")
    print(json.dumps({"status": "OK", "out": str(out_p)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
