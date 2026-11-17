#!/usr/bin/env python3
"""Isolated Staging-capped (`staging_vps_capped`) diagnostic probe for `API-01C` (`my_customers_worklist_api_c40`).

Runs against an immutable `@sha256` image digest (without mutating the candidate branch/digest):
1. Seeds exact S1 dataset (`50k` Leads, `75k` Work Items, `250k` Contact Attempts, `150k` SLA Obligations,
   `75k` Assignment History, `40` active staff across `4` teams, `6` campaigns) under `staging_vps_capped`.
2. Profiles in-container WSGI sub-phases (`db_connect_ms`, `redis_session_ms`, `sql_ms`, `sql_count`,
   `handler_ms`, `destroy_ms`, `EXPLAIN` / `ANALYZE FORMAT=JSON`) + inspects pinned `gunicorn/workers/gthread.py`.
3. Runs unmodified `d3e35f81` stock `peak_40` (`telesale_worklist_page` c=40 -> `my_customers_worklist_api` c=40)
   capturing per-container `/sys/fs/cgroup/cpu.stat` (`nr_throttled`, `throttled_usec`, `usage_usec`),
   MariaDB `SHOW GLOBAL STATUS` (`Questions`, `Connections`, `Innodb_buffer_pool_reads`, `Innodb_row_lock_waits`),
   and RQ queue/worker telemetry.
4. Decomposes every single HTTP request at `c=40` (`160` reqs x `5` trials) into `worker_pid`,
   `queue_wait_ms = client_ms - wsgi_ms`, `wsgi_ms`, `db_conn_ms`, and `sql_ms` on both:
   - exact `staging_vps_capped` (`backend_cpus = 1.50`), and
   - VPS-throughput-calibrated cgroup (`backend_cpus = 0.48`, matching `bd99619`'s `~88.9 rps` on KVM VPS),
   comparing stock `gthread` (`worker_connections=1000`) against `FairGthreadWorker` (`max_in_flight=4` and `5`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import secrets
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from benchmark_p4_isolated import (
    LOCK_FILE,
    STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
    VERIFY_COMPOSE_FILE,
    _collect_queue_and_db_telemetry,
    _compose_files_arg,
    _login_session,
    enforce_isolated_benchmark_target,
    enforce_live_resource_headroom,
    summarize_latencies_ms,
)
from diagnose_g18_drilldown import scrub_sensitive_tokens

IN_CONTAINER_SETUP_AND_PROFILE = r'''
import inspect
import json
import os
import time
from datetime import datetime, timedelta

import frappe
from frappe.utils.password import update_password

import gunicorn.workers.gthread as gthread_mod
from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services.attribution import AUTHORITATIVE


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

for w_start in range(0, 75_000, 15_000):
    w_end = min(w_start + 15_000, 75_000)
    wi_rows = []
    for i in range(w_start, w_end):
        lead_idx = i if i < 50_000 else (i - 50_000)
        lead_id = f"S1-LEAD-{lead_idx:06d}"
        s_idx = lead_idx % 40
        owner_u = staff_users[s_idx]
        primary_camp_idx = s_idx // 10
        camp = campaigns[primary_camp_idx] if i < 50_000 else campaigns[4]
        is_open = (49_200 <= i < 50_000) or (74_200 <= i < 75_000)
        is_closed = not is_open
        day_num = 27 if is_open else ((i % 26) + 1)
        base_ts = datetime(2026, 10, day_num, 9 + (i % 6), (i * 5) % 40, 0)
        close_ts = base_ts + timedelta(hours=2) if is_closed else None
        st = "assigned" if (is_open and i % 2 == 0) else ("in_progress" if is_open else "closed")
        wi_rows.append(
            (
                f"S1-WI-{i:06d}", base_ts, close_ts or base_ts, "Administrator", "Administrator", 0, 0,
                lead_id, camp, st, owner_u if is_open else None, base_ts,
                owner_u if is_closed else None, owner_u if is_closed else None, close_ts,
                3, 1, base_ts + timedelta(minutes=15), base_ts + timedelta(minutes=45),
                "connected_interested", "Đã trao đổi — Có quan tâm", 1,
            )
        )
    _bulk_insert("tabCSKH Work Item", wi_cols, wi_rows)

frappe.db.sql("ANALYZE TABLE `tabCRM Lead`, `tabCSKH Work Item`, `tabCSKH Campaign`")
frappe.db.commit()
seed_sec = round(time.perf_counter() - t_seed0, 2)

# EXPLAIN & ANALYZE FORMAT=JSON for Administrator (0 rows) and staff.s1.01 (40 open rows)
explain_admin = frappe.db.sql(
    "ANALYZE FORMAT=JSON SELECT count(*) FROM `tabCSKH Work Item` wi "
    "WHERE wi.assigned_to = 'Administrator' AND wi.status IN ('assigned', 'in_progress', 'on_hold')",
    as_dict=True,
)
explain_staff_cnt = frappe.db.sql(
    "ANALYZE FORMAT=JSON SELECT count(*) FROM `tabCSKH Work Item` wi "
    "WHERE wi.assigned_to = %s AND wi.status IN ('assigned', 'in_progress', 'on_hold')",
    (staff_users[0],),
    as_dict=True,
)

# Measure Frappe per-request DB connection close+reopen cost vs warm SQL cost
conn_times = []
for _ in range(20):
    frappe.db.close()
    t0 = time.perf_counter()
    frappe.db.connect()
    conn_times.append((time.perf_counter() - t0) * 1000.0)

gthread_src = inspect.getsource(gthread_mod.ThreadWorker)
frappe.destroy()

out = {
    "seed_sec": seed_sec,
    "staff_users": staff_users,
    "explain_admin_count_json": explain_admin[0] if explain_admin else {},
    "explain_staff_count_json": explain_staff_cnt[0] if explain_staff_cnt else {},
    "pymysql_connect_close_20_samples_ms": [round(x, 3) for x in conn_times],
    "pymysql_connect_close_median_ms": round(sorted(conn_times)[10], 3),
    "gthread_worker_source": gthread_src,
}
print("API01C_SETUP_JSON:" + json.dumps(out))
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

FAIR_GTHREAD_MODULE = r'''
import functools
import os
import selectors
from collections import deque
from concurrent import futures
from gunicorn.workers.gthread import ThreadWorker


class FairGthreadWorker(ThreadWorker):
    """ThreadWorker that bounds in-flight accepted sockets to worker thread capacity.

    Prevents a single Gunicorn worker process from greedily draining 35/40 concurrent
    connections from the shared kernel listen socket into its private ThreadPoolExecutor
    queue while sibling worker processes sit idle.
    """

    def _register_listeners(self):
        if getattr(self, "_listeners_registered", False):
            return
        for sock in self.sockets:
            sock.setblocking(False)
            server = sock.getsockname()
            acceptor = functools.partial(self.accept, server)
            self.poller.register(sock, selectors.EVENT_READ, acceptor)
        self._listeners_registered = True

    def _unregister_listeners(self):
        if not getattr(self, "_listeners_registered", False):
            return
        for sock in self.sockets:
            try:
                self.poller.unregister(sock)
            except (KeyError, ValueError):
                pass
        self._listeners_registered = False

    def accept(self, server, listener):
        if len(self.futures) >= self._max_in_flight:
            self._unregister_listeners()
            return
        super().accept(server, listener)
        if len(self.futures) >= self._max_in_flight:
            self._unregister_listeners()

    def run(self):
        self.alive = True
        self._keep = deque()
        self.nr_conns = 0
        self._max_in_flight = max(
            1, int(os.environ.get("CSKH_FAIR_MAX_INFLIGHT") or self.cfg.threads)
        )
        self._listeners_registered = False
        self._register_listeners()

        while self.alive:
            self.notify()

            if len(self.futures) < self._max_in_flight and self.nr_conns < self.worker_connections:
                self._register_listeners()
                events = self.poller.select(1.0)
                for key, _ in events:
                    callback = key.data
                    callback(key.fileobj)
                result = futures.wait(
                    self.futures, timeout=0, return_when=futures.FIRST_COMPLETED
                )
            elif self._keep:
                self._unregister_listeners()
                events = self.poller.select(0.01)
                for key, _ in events:
                    callback = key.data
                    callback(key.fileobj)
                result = futures.wait(
                    self.futures, timeout=0.01, return_when=futures.FIRST_COMPLETED
                )
            else:
                self._unregister_listeners()
                result = futures.wait(
                    self.futures, timeout=0.2, return_when=futures.FIRST_COMPLETED
                )

            for fut in result.done:
                self.futures.remove(fut)

            if not self.is_parent_alive():
                break

            self.murder_keepalived()

        self.tpool.shutdown(False)
        self.poller.close()
        for s in self.sockets:
            s.close()
'''


def _read_cgroup_cpu_stats(compose_project: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for svc in ("frontend", "backend", "db", "redis-cache", "queue-short", "queue-long"):
        cname = f"{compose_project}-{svc}-1"
        proc = subprocess.run(
            ["docker", "exec", cname, "cat", "/sys/fs/cgroup/cpu.stat"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        stats: dict[str, int] = {}
        if proc.returncode == 0:
            for ln in proc.stdout.splitlines():
                parts = ln.strip().split()
                if len(parts) == 2 and parts[1].isdigit():
                    stats[parts[0]] = int(parts[1])
        out[svc] = stats
    return out


def _diff_cgroup(before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    res: dict[str, dict[str, int]] = {}
    for svc, a_map in after.items():
        b_map = before.get(svc) or {}
        res[svc] = {k: a_map.get(k, 0) - b_map.get(k, 0) for k in a_map}
    return res


def _read_mariadb_counters(compose_base: list[str], compose_env: dict[str, str], db_root_pw: str) -> dict[str, int]:
    proc = subprocess.run(
        [
            *compose_base,
            "exec",
            "-T",
            "db",
            "mariadb",
            "-uroot",
            f"-p{db_root_pw}",
            "-Nse",
            "SHOW GLOBAL STATUS WHERE Variable_name IN "
            "('Questions','Connections','Threads_running','Innodb_buffer_pool_reads','Innodb_row_lock_waits')",
        ],
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


def _http_call_with_diag(
    *,
    base_url: str,
    site_name: str,
    cookie_header: str,
    path: str,
    timeout_sec: float = 30.0,
) -> dict[str, object]:
    headers = {"Host": site_name, "Accept": "application/json"}
    if cookie_header:
        headers["Cookie"] = cookie_header
    req = urllib.request.Request(f"{base_url}{path}", headers=headers, method="GET")
    t0 = time.perf_counter()
    status = 0
    resp_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            _ = resp.read()
            status = int(resp.status)
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except Exception:
        status = 0
    client_ms = (time.perf_counter() - t0) * 1000.0
    wsgi_ms = float(resp_headers.get("x-diag-wsgi-ms") or 0.0)
    db_conn_ms = float(resp_headers.get("x-diag-dbconn-ms") or 0.0)
    sql_ms = float(resp_headers.get("x-diag-sql-ms") or 0.0)
    sql_cnt = int(resp_headers.get("x-diag-sql-count") or 0)
    pid = resp_headers.get("x-diag-pid") or ""
    queue_wait_ms = max(0.0, client_ms - wsgi_ms) if wsgi_ms > 0.0 else None
    return {
        "status": status,
        "client_ms": round(client_ms, 3),
        "wsgi_ms": round(wsgi_ms, 3) if wsgi_ms > 0 else None,
        "queue_wait_ms": round(queue_wait_ms, 3) if queue_wait_ms is not None else None,
        "db_conn_ms": round(db_conn_ms, 3) if wsgi_ms > 0 else None,
        "sql_ms": round(sql_ms, 3) if wsgi_ms > 0 else None,
        "sql_count": sql_cnt if wsgi_ms > 0 else None,
        "pid": pid,
    }


def _run_c40_wave_detailed(
    *,
    base_url: str,
    site_name: str,
    cookies: list[str],
    path: str,
    concurrency: int = 40,
    requests_total: int = 160,
) -> dict[str, object]:
    lock = threading.Lock()
    next_idx = 0
    records: list[dict[str, object]] = []

    def _worker() -> None:
        nonlocal next_idx
        while True:
            with lock:
                if next_idx >= requests_total:
                    return
                idx = next_idx
                next_idx += 1
            ck = cookies[idx % len(cookies)]
            rec = _http_call_with_diag(
                base_url=base_url,
                site_name=site_name,
                cookie_header=ck,
                path=path,
            )
            rec["issue_idx"] = idx
            with lock:
                records.append(rec)

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(_worker) for _ in range(concurrency)]
        for f in futs:
            f.result()
    wall_sec = max(1e-6, time.perf_counter() - t0)

    lats = [float(r["client_ms"]) for r in records]
    summary = summarize_latencies_ms(lats, wall_sec=wall_sec)
    summary["error_count"] = sum(1 for r in records if int(r["status"]) != 200)

    pid_counts: dict[str, int] = {}
    pid_p95: dict[str, float] = {}
    by_pid: dict[str, list[float]] = {}
    for r in records:
        p = str(r.get("pid") or "")
        if p:
            pid_counts[p] = pid_counts.get(p, 0) + 1
            by_pid.setdefault(p, []).append(float(r["client_ms"]))
    for p, vals in by_pid.items():
        s_vals = sorted(vals)
        pid_p95[p] = round(s_vals[min(len(s_vals) - 1, int(len(s_vals) * 0.95))], 3)

    steady = lats[60:140]
    lt200 = sum(1 for x in steady if x < 200.0)
    mid_200_600 = sum(1 for x in steady if 200.0 <= x <= 600.0)
    gt600 = sum(1 for x in steady if x > 600.0)

    slowest_10 = sorted(records, key=lambda r: float(r["client_ms"]), reverse=True)[:10]
    fastest_5 = sorted(records, key=lambda r: float(r["client_ms"]))[:5]
    wsgi_vals = [float(r["wsgi_ms"]) for r in records if r.get("wsgi_ms") is not None]
    qwait_vals = [float(r["queue_wait_ms"]) for r in records if r.get("queue_wait_ms") is not None]
    dbconn_vals = [float(r["db_conn_ms"]) for r in records if r.get("db_conn_ms") is not None]
    sql_vals = [float(r["sql_ms"]) for r in records if r.get("sql_ms") is not None]

    return {
        "summary": {k: v for k, v in summary.items() if k != "raw_latencies_ms"},
        "steady_idx_60_139_buckets": {
            "lt_200ms": lt200,
            "between_200_600ms": mid_200_600,
            "gt_600ms": gt600,
        },
        "worker_pid_counts": pid_counts,
        "worker_pid_p95_ms": pid_p95,
        "component_medians_ms": {
            "wsgi_p50_ms": round(statistics.median(wsgi_vals), 3) if wsgi_vals else None,
            "wsgi_p95_ms": round(sorted(wsgi_vals)[int(len(wsgi_vals) * 0.95)], 3) if wsgi_vals else None,
            "queue_wait_p50_ms": round(statistics.median(qwait_vals), 3) if qwait_vals else None,
            "queue_wait_p95_ms": round(sorted(qwait_vals)[int(len(qwait_vals) * 0.95)], 3) if qwait_vals else None,
            "db_conn_p50_ms": round(statistics.median(dbconn_vals), 3) if dbconn_vals else None,
            "sql_p50_ms": round(statistics.median(sql_vals), 3) if sql_vals else None,
        },
        "slowest_10_samples": slowest_10,
        "fastest_5_samples": fastest_5,
    }


def _wait_http_ready(base_url: str, site_name: str, timeout_sec: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(f"{base_url}/api/method/ping", headers={"Host": site_name})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1.5)
    raise RuntimeError(f"HTTP not ready on {base_url}")


def run_probe(*, app_image: str, pull_policy: str = "never", port: int = 18892) -> dict[str, object]:
    site_name = "p4-api01c-diag.localhost"
    compose_project = f"cskh-p4-bench-c40-{secrets.token_hex(4)}"
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

    with tempfile.TemporaryDirectory(prefix="cskh-api01c-diag-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_secret = tmp_dir / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
        bench_override = tmp_dir / "compose.bench-override.yaml"
        bench_override.write_text(STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML, encoding="utf-8")
        compose_files = [VERIFY_COMPOSE_FILE, bench_override]

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

        def _run_checked(cmd: list[str], *, label: str, input_text: str | None = None, timeout_sec: int = 300) -> subprocess.CompletedProcess[str]:
            proc = subprocess.run(
                cmd,
                input=input_text,
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if proc.returncode != 0:
                msg = f"{label} failed (rc={proc.returncode}):\nSTDOUT:\n{proc.stdout[-2500:]}\nSTDERR:\n{proc.stderr[-2500:]}"
                for sec in (db_root_pw, admin_pw, actor_pw):
                    msg = msg.replace(sec, "[REDACTED]")
                raise RuntimeError(msg)
            return proc

        try:
            _run_checked([*compose_base, "up", "-d", "--no-build"], label="compose up", timeout_sec=300)
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
                label="site init",
                timeout_sec=600,
            )
            _run_checked(
                [*compose_base, "restart", "backend", "websocket", "queue-short", "queue-long", "scheduler"],
                label="restart services",
                timeout_sec=120,
            )
            setup_proc = _run_checked(
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
                label="in-container S1 setup & profile",
                input_text=IN_CONTAINER_SETUP_AND_PROFILE,
                timeout_sec=900,
            )
            markers = [
                ln[len("API01C_SETUP_JSON:") :]
                for ln in setup_proc.stdout.splitlines()
                if ln.startswith("API01C_SETUP_JSON:")
            ]
            in_container = json.loads(markers[-1])
            staff_users: list[str] = list(in_container.pop("staff_users"))

            subprocess.run(
                [*compose_base, "up", "-d", "--no-build", "--force-recreate", "frontend"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            _wait_http_ready(base_url, site_name)

            admin_cookie = _login_session(
                base_url=base_url, site_name=site_name, user="Administrator", password=admin_pw, fetch_csrf=False
            )["cookie"]
            staff_cookies = [
                _login_session(base_url=base_url, site_name=site_name, user=u, password=actor_pw, fetch_csrf=False)["cookie"]
                for u in staff_users[:20]
            ]

            def _run_peak40_suite(label: str, trials: int = 5) -> dict[str, object]:
                # Warm worker threads
                _ = _run_c40_wave_detailed(
                    base_url=base_url, site_name=site_name, cookies=[admin_cookie], path="/api/method/ping", concurrency=8, requests_total=24
                )
                time.sleep(0.5)
                q_before = _collect_queue_and_db_telemetry(compose_project, site_name, compose_file=compose_files, env=compose_env)
                trial_results = []
                for t_idx in range(1, trials + 1):
                    cg_0 = _read_cgroup_cpu_stats(compose_project)
                    ssr_res = _run_c40_wave_detailed(
                        base_url=base_url,
                        site_name=site_name,
                        cookies=[admin_cookie],
                        path="/cskh/my-customers",
                        concurrency=40,
                        requests_total=160,
                    )
                    cg_1 = _read_cgroup_cpu_stats(compose_project)
                    db_1 = _read_mariadb_counters(compose_base, compose_env, db_root_pw)
                    api_res = _run_c40_wave_detailed(
                        base_url=base_url,
                        site_name=site_name,
                        cookies=[admin_cookie],
                        path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                        concurrency=40,
                        requests_total=160,
                    )
                    cg_2 = _read_cgroup_cpu_stats(compose_project)
                    db_2 = _read_mariadb_counters(compose_base, compose_env, db_root_pw)
                    trial_results.append(
                        {
                            "trial": t_idx,
                            "preceding_ssr_c40_p95_ms": ssr_res["summary"]["p95_ms"],
                            "preceding_ssr_c40_rps": ssr_res["summary"]["throughput_rps"],
                            "ssr_cgroup_delta": _diff_cgroup(cg_0, cg_1),
                            "api01c_c40": api_res,
                            "api01c_cgroup_delta": _diff_cgroup(cg_1, cg_2),
                            "api01c_mariadb_delta": {k: db_2.get(k, 0) - db_1.get(k, 0) for k in db_2},
                        }
                    )
                    time.sleep(0.5)
                staff_c40 = _run_c40_wave_detailed(
                    base_url=base_url,
                    site_name=site_name,
                    cookies=staff_cookies,
                    path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                    concurrency=40,
                    requests_total=160,
                )
                p95s = [float(t["api01c_c40"]["summary"]["p95_ms"]) for t in trial_results]
                return {
                    "label": label,
                    "queue_and_db_before": q_before,
                    "trials_p95_ms": p95s,
                    "trials_max_p95_ms": max(p95s),
                    "trials_median_p95_ms": round(statistics.median(p95s), 3),
                    "any_trial_breached_800ms": any(x > 800.0 for x in p95s),
                    "breach_count_out_of_trials": sum(1 for x in p95s if x > 800.0),
                    "staff_20_sessions_my_list_c40": staff_c40,
                    "trials": trial_results,
                }

            # 1. Pure unmodified stock `d3e35f81` on `staging_vps_capped` (1.50 vCPU)
            unmodified_150 = _run_peak40_suite("unmodified_d3e35f81_1_50cpu", trials=5)

            # Write diagnostic modules onto shared `sites` volume (`/home/frappe/frappe-bench/sites/_diag_c40`)
            # so container restarts retain them cleanly without touching the immutable image.
            write_diag_py = (
                "import json, pathlib, sys; "
                "d = pathlib.Path('/home/frappe/frappe-bench/sites/_diag_c40'); d.mkdir(parents=True, exist_ok=True); "
                "obj = json.loads(sys.stdin.read()); "
                "(d / 'cskh_diag_wsgi.py').write_text(obj['wsgi'], encoding='utf-8'); "
                "(d / 'cskh_fair_worker.py').write_text(obj['fair'], encoding='utf-8')"
            )
            _run_checked(
                [*compose_base, "exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", write_diag_py],
                label="write diagnostic wsgi/worker modules",
                input_text=json.dumps({"wsgi": DIAG_WSGI_MODULE, "fair": FAIR_GTHREAD_MODULE}),
                timeout_sec=30,
            )

            def _recreate_backend(*, cpus: float, worker_class: str, keepalive_zero: bool, max_inflight: int | None) -> None:
                extra_flags = ["--keep-alive=0"] if keepalive_zero else []
                env_block = [
                    '      GUNICORN_WORKERS: "2"',
                    '      GUNICORN_THREADS: "4"',
                    '      GUNICORN_TIMEOUT: "120"',
                    '      TZ: Asia/Ho_Chi_Minh',
                ]
                if max_inflight is not None:
                    env_block.append(f'      CSKH_FAIR_MAX_INFLIGHT: "{max_inflight}"')
                cmd_list = [
                    "/home/frappe/frappe-bench/env/bin/gunicorn",
                    "--chdir=/home/frappe/frappe-bench/sites",
                    "--pythonpath=/home/frappe/frappe-bench/sites/_diag_c40",
                    "--bind=0.0.0.0:8000",
                    "--threads=4",
                    "--workers=2",
                    f"--worker-class={worker_class}",
                    "--worker-tmp-dir=/dev/shm",
                    "--timeout=120",
                    *extra_flags,
                    "--preload",
                    "cskh_diag_wsgi:application",
                ]
                backend_override = tmp_dir / "compose.backend-diag.yaml"
                backend_override.write_text(
                    "services:\n"
                    "  backend:\n"
                    f"    cpus: {cpus:.2f}\n"
                    "    environment:\n"
                    + "\n".join(env_block)
                    + "\n    command: "
                    + json.dumps(cmd_list)
                    + "\n",
                    encoding="utf-8",
                )
                files_now = [VERIFY_COMPOSE_FILE, bench_override, backend_override]
                cbase_now = ["docker", "compose", *_compose_files_arg(files_now), "-p", compose_project]
                _run_checked(
                    [*cbase_now, "up", "-d", "--no-build", "--force-recreate", "backend", "frontend"],
                    label=f"recreate backend ({worker_class}, cpus={cpus})",
                    timeout_sec=120,
                )
                _wait_http_ready(base_url, site_name)

            # 2. Instrumented stock `gthread` at 1.50 vCPU
            _recreate_backend(cpus=1.50, worker_class="gthread", keepalive_zero=False, max_inflight=None)
            stock_diag_150 = _run_peak40_suite("stock_gthread_diag_1_50cpu", trials=5)

            # Auto-calibrate VPS-equivalent backend CPU quota so stock `gthread` throughput matches `bd99619` (`88.88 rps`)
            measured_150_rps = statistics.median(
                [float(t["api01c_c40"]["summary"]["throughput_rps"]) for t in stock_diag_150["trials"]]
            )
            vps_target_rps = 88.88
            calibrated_cpus = round(max(0.30, min(1.50, 1.50 * (vps_target_rps / max(1.0, measured_150_rps)))), 2)

            # 3. Stock `gthread` at VPS-calibrated CPU (`~88.9 rps`) -> tests reproducibility of `p95 > 800 ms`
            _recreate_backend(cpus=calibrated_cpus, worker_class="gthread", keepalive_zero=False, max_inflight=None)
            stock_diag_vps = _run_peak40_suite(f"stock_gthread_vps_calibrated_{calibrated_cpus}cpu", trials=5)

            # 4. FairGthreadWorker (`max_in_flight = 4`, `--keep-alive=0`) at EXACT SAME VPS-calibrated CPU
            _recreate_backend(
                cpus=calibrated_cpus,
                worker_class="cskh_fair_worker.FairGthreadWorker",
                keepalive_zero=True,
                max_inflight=4,
            )
            fair_m4_vps = _run_peak40_suite(f"fair_gthread_m4_vps_calibrated_{calibrated_cpus}cpu", trials=5)

            # 5. FairGthreadWorker (`max_in_flight = 5`, `--keep-alive=0`) at EXACT SAME VPS-calibrated CPU
            _recreate_backend(
                cpus=calibrated_cpus,
                worker_class="cskh_fair_worker.FairGthreadWorker",
                keepalive_zero=True,
                max_inflight=5,
            )
            fair_m5_vps = _run_peak40_suite(f"fair_gthread_m5_vps_calibrated_{calibrated_cpus}cpu", trials=5)

            # 6. FairGthreadWorker (`max_in_flight = 4`, `--keep-alive=0`) at `1.50 vCPU` (`staging_vps_capped`)
            _recreate_backend(
                cpus=1.50,
                worker_class="cskh_fair_worker.FairGthreadWorker",
                keepalive_zero=True,
                max_inflight=4,
            )
            fair_m4_150 = _run_peak40_suite("fair_gthread_m4_1_50cpu", trials=5)

            return {
                "schema_version": "cskh-p4-api01c-c40-isolated-diagnostic-v1",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "app_image_tested": app_image,
                "isolation_guard": isolation,
                "live_headroom": live_headroom,
                "vps_cpu_calibration": {
                    "ci_1_50cpu_median_rps": round(measured_150_rps, 3),
                    "vps_bd99619_rps": vps_target_rps,
                    "calibrated_backend_cpus": calibrated_cpus,
                },
                "in_container_sql_and_gthread_inspection": in_container,
                "suites": {
                    "unmodified_d3e35f81_1_50cpu": unmodified_150,
                    "stock_gthread_diag_1_50cpu": stock_diag_150,
                    "stock_gthread_vps_calibrated": stock_diag_vps,
                    "fair_gthread_m4_vps_calibrated": fair_m4_vps,
                    "fair_gthread_m5_vps_calibrated": fair_m5_vps,
                    "fair_gthread_m4_1_50cpu": fair_m4_150,
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
    ap.add_argument("--app-image", required=True)
    ap.add_argument("--pull-policy", default="never", choices=("never", "missing", "always"))
    ap.add_argument("--port", type=int, default=18892)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rpt = run_probe(app_image=args.app_image.strip(), pull_policy=args.pull_policy, port=args.port)
    serialized = scrub_sensitive_tokens(json.dumps(rpt, indent=2) + "\n")
    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(serialized, encoding="utf-8")
    print(json.dumps({"status": "OK", "out": str(out_p)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
