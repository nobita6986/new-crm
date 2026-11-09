#!/usr/bin/env python3
"""Isolated P4 benchmark harness (`P4-06` / `G18`) with fail-closed Owner-VPS protection.

Implements P4 isolated performance & capacity benchmarking:
1. Fail-closed target boundary (`enforce_isolated_benchmark_target`):
   - Refuses to send load unless `--confirm-isolated-target` is explicitly set.
   - Blocks non-loopback URLs by default (and blocks any URL, site name, or Compose project
     referencing `prod`, `production`, `owner`, `live`, `hrpartner`, or public domains) so the
     Owner's live VPS can NEVER be stress-tested.
   - Requires `--compose-project` to carry an isolated benchmark prefix (`cskh-p4-bench-*` or
     `crm-cskh-p4-bench-*`).
2. Published synthetic dataset & workload specification (`0` real PII):
   - `s1_suggested_mvp` (`docs/root-plan/P4.md` §9.1 suggested `S1` dataset: `50,000` hồ sơ
     [`CRM Lead`], `75,000` `CSKH Work Item`, `250,000` `CSKH Contact Attempt`, `40` staff —
     explicitly framed as a **suggested benchmark dataset, NOT a commitment of 50,000 hồ sơ/tháng**),
   - `isolated_smoke_1k` (1,000 Work Items — CI/local harness smoke),
   - `isolated_staging_10k` (10,000 Work Items — isolated Staging pre-scale),
   - `isolated_p4_target_50k` (50,000 Work Items — legacy profile alias).
3. Self-contained `--provision-and-run-s1` mode:
   - Boots an ephemeral isolated stack (`deploy/images/compose.verify.yaml`) from an immutable
     `APP_IMAGE` digest with zero host bind-mounts, seeds the exact `S1` dataset (`50k` Leads,
     `75k` Work Items, `250k` Attempts, `40` staff), measures domain reads/writes, CSV import +
     distribution + Redis RQ queue lag, `12`-worker and `40`-staff concurrent HTTP waves,
     container `CPU%`/`RAM`, MariaDB telemetry + `EXPLAIN` plans, observed bottlenecks/limits,
     and proposed SLOs, then destroys all containers and volumes in `finally`.
4. Never self-commits unmeasured or unapproved SLAs (`sla_commitment_status` stays
   `UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import ipaddress
import json
import math
import os
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGES_DIR = REPO_ROOT / "deploy" / "images"
LOCK_FILE = IMAGES_DIR / "release-lock.json"
VERIFY_COMPOSE_FILE = IMAGES_DIR / "compose.verify.yaml"

FORBIDDEN_LIVE_TOKENS = (
    "prod",
    "production",
    "owner",
    "live",
    "hrpartner",
    ".vn",
    ".com",
    ".net",
    ".org",
)
ALLOWED_BENCH_PROJECT_PREFIXES = ("cskh-p4-bench-", "crm-cskh-p4-bench-")

PUBLISHED_PROFILES: dict[str, dict[str, object]] = {
    "s1_suggested_mvp": {
        "description": (
            "Suggested S1 benchmark dataset (P4.md §9.1): 50,000 Leads, 75,000 Work Items, "
            "250,000 Contact Attempts, 40 active staff on isolated environment (NOT a 50,000 Leads/month commitment)"
        ),
        "dataset_framing": "SUGGESTED_S1_DATASET_NOT_MONTHLY_VOLUME_COMMITMENT",
        "leads": 50_000,
        "work_items": 75_000,
        "monthly_work_items": 75_000,
        "contact_attempts": 250_000,
        "sla_obligations": 150_000,
        "teams": 4,
        "campaigns": 5,
        "telesale_agents": 40,
        "active_staff": 40,
        "import_batch_rows": 500,
        "distribution_batch_items": 400,
        "concurrency": 12,
        "peak_staff_concurrency": 40,
        "requests_per_scenario": 240,
        "pii_policy": "ZERO_REAL_PII_SYNTHETIC_ONLY",
    },
    "isolated_smoke_1k": {
        "description": "Local/CI isolated smoke benchmark (0 PII synthetic dataset)",
        "dataset_framing": "SUGGESTED_SYNTHETIC_SMOKE_DATASET",
        "monthly_work_items": 1_000,
        "campaigns": 3,
        "telesale_agents": 10,
        "import_batch_rows": 250,
        "concurrency": 4,
        "requests_per_scenario": 40,
        "pii_policy": "ZERO_REAL_PII_SYNTHETIC_ONLY",
    },
    "isolated_staging_10k": {
        "description": "Isolated Staging pre-scale benchmark (0 PII synthetic dataset)",
        "dataset_framing": "SUGGESTED_SYNTHETIC_PRESCALE_DATASET",
        "monthly_work_items": 10_000,
        "campaigns": 5,
        "telesale_agents": 20,
        "import_batch_rows": 1_000,
        "concurrency": 8,
        "requests_per_scenario": 160,
        "pii_policy": "ZERO_REAL_PII_SYNTHETIC_ONLY",
    },
    "isolated_p4_target_50k": {
        "description": "Isolated 50k Work Items synthetic benchmark profile (not a monthly volume commitment)",
        "dataset_framing": "SUGGESTED_DATASET_NOT_MONTHLY_VOLUME_COMMITMENT",
        "monthly_work_items": 50_000,
        "campaigns": 10,
        "telesale_agents": 30,
        "import_batch_rows": 2_500,
        "concurrency": 12,
        "requests_per_scenario": 400,
        "pii_policy": "ZERO_REAL_PII_SYNTHETIC_ONLY",
    },
}

PUBLISHED_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "id": "health_ping",
        "method": "GET",
        "path": "/api/method/ping",
        "workload_class": "baseline_edge_to_gunicorn",
    },
    {
        "id": "telesale_worklist_page",
        "method": "GET",
        "path": "/cskh/my-customers",
        "workload_class": "authenticated_worklist_ssr",
    },
    {
        "id": "quality_overview_drilldown",
        "method": "GET",
        "path": "/cskh/quality-overview",
        "workload_class": "kpi_reporting_drilldown",
    },
    {
        "id": "campaigns_distribution_view",
        "method": "GET",
        "path": "/cskh/campaigns",
        "workload_class": "campaign_distribution_summary",
    },
)

S1_API_SCENARIOS: tuple[dict[str, str], ...] = (
    *PUBLISHED_SCENARIOS,
    {
        "id": "my_customers_worklist_api",
        "method": "GET",
        "path": "/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
        "workload_class": "authenticated_worklist_json_api",
    },
    {
        "id": "bod_executive_overview_api",
        "method": "GET",
        "path": "/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key=2026-10",
        "workload_class": "bod_kpi_snapshot_read_json_api",
    },
    {
        "id": "kpi_metric_drilldown_250k_api",
        "method": "GET",
        "path": (
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
            "?period_type=daily&period_key=2026-10-27&scope_type=company"
            "&metric_code=CONNECTED_RATE&page=1&page_size=20"
        ),
        "workload_class": "kpi_drilldown_paginated_250k_attempts",
    },
)


def enforce_isolated_benchmark_target(
    *,
    base_url: str,
    site_name: str,
    compose_project: str,
    confirm_isolated_target: bool,
    allow_isolated_private_ip: bool = False,
) -> dict[str, object]:
    """Fail-closed guard preventing accidental load generation against Owner/live VPS."""
    if not confirm_isolated_target:
        raise ValueError(
            "Refusing to run benchmark without explicit --confirm-isolated-target flag"
        )

    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Invalid benchmark --base-url: {base_url!r}")

    host = parsed.hostname.lower()
    site_low = site_name.strip().lower()
    proj_low = compose_project.strip().lower()

    for label, val in (
        ("base_url host", host),
        ("site_name", site_low),
        ("compose_project", proj_low),
    ):
        for bad in FORBIDDEN_LIVE_TOKENS:
            if bad in val:
                raise ValueError(
                    f"Forbidden live/Owner marker {bad!r} detected in {label} ({val!r}); "
                    "never stress-test Owner or Production VPS"
                )

    if not proj_low.startswith(ALLOWED_BENCH_PROJECT_PREFIXES):
        raise ValueError(
            f"--compose-project {compose_project!r} must start with one of {ALLOWED_BENCH_PROJECT_PREFIXES}"
        )
    if not site_low.endswith(".localhost") and not site_low.endswith(".isolated"):
        raise ValueError(
            f"--site-name {site_name!r} must end with '.localhost' or '.isolated'"
        )

    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    is_private_ip = False
    if not is_loopback:
        try:
            ip_obj = ipaddress.ip_address(host)
            is_private_ip = ip_obj.is_private and not ip_obj.is_loopback
        except ValueError:
            is_private_ip = False
        if not (allow_isolated_private_ip and is_private_ip):
            raise ValueError(
                f"Non-loopback benchmark host {host!r} is blocked by default "
                "(must be 127.0.0.1/localhost/::1 or private RFC1918 IP with --allow-isolated-private-ip)"
            )

    return {
        "verified_isolated": True,
        "owner_vps_touched": False,
        "target_host": host,
        "is_loopback": is_loopback,
        "is_isolated_private_ip": is_private_ip,
        "site_name": site_name,
        "compose_project": compose_project,
    }


def percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return round(sorted_vals[low], 3)
    weight = rank - low
    return round(sorted_vals[low] * (1.0 - weight) + sorted_vals[high] * weight, 3)


def summarize_latencies_ms(
    latencies_ms: list[float], *, error_count: int, elapsed_sec: float
) -> dict[str, float | int]:
    total = len(latencies_ms)
    ordered = sorted(latencies_ms)
    err_rate = (error_count / total * 100.0) if total > 0 else 0.0
    rps = (total / elapsed_sec) if elapsed_sec > 0 else 0.0
    return {
        "requests_total": total,
        "ok_count": total - error_count,
        "error_count": error_count,
        "error_rate_pct": round(err_rate, 4),
        "throughput_rps": round(rps, 3),
        "min_ms": round(ordered[0], 3) if ordered else 0.0,
        "p50_ms": percentile(ordered, 50.0),
        "p95_ms": percentile(ordered, 95.0),
        "p99_ms": percentile(ordered, 99.0),
        "max_ms": round(ordered[-1], 3) if ordered else 0.0,
    }


def _collect_docker_stats(
    compose_project: str,
    *,
    compose_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    cmd = ["docker", "compose"]
    if compose_file is not None:
        cmd.extend(["-f", str(compose_file)])
    cmd.extend(["-p", compose_project, "ps", "-q"])
    ps_proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    cids = [ln.strip() for ln in ps_proc.stdout.splitlines() if ln.strip()]
    if not cids:
        return []
    st_proc = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{json .}}",
            *cids,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    rows: list[dict[str, str]] = []
    for raw in st_proc.stdout.splitlines():
        if raw.strip():
            obj = json.loads(raw)
            rows.append(
                {
                    "container": str(obj.get("Name") or ""),
                    "cpu_pct": str(obj.get("CPUPerc") or ""),
                    "mem_usage": str(obj.get("MemUsage") or ""),
                    "mem_pct": str(obj.get("MemPerc") or ""),
                    "net_io": str(obj.get("NetIO") or ""),
                    "block_io": str(obj.get("BlockIO") or ""),
                }
            )
    return rows


def _collect_queue_and_db_telemetry(
    compose_project: str,
    site_name: str,
    *,
    compose_file: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    probe = (
        "import json, time, frappe, redis; "
        "from frappe.utils.background_jobs import generate_qname; "
        f"site = {site_name!r}; "
        "frappe.init(site=site, sites_path='/home/frappe/frappe-bench/sites'); "
        "frappe.connect(); "
        "t0 = time.perf_counter(); "
        "r = redis.from_url(frappe.conf.redis_queue); "
        "q_depths = {q: int(r.llen(f'rq:queue:{generate_qname(q)}')) for q in ('short', 'default', 'long')}; "
        "redis_ping_ms = round((time.perf_counter() - t0) * 1000.0, 3); "
        "status_rows = dict(frappe.db.sql(\"SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Max_used_connections','Innodb_row_lock_time_avg','Innodb_row_lock_waits','Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads','Slow_queries','Questions')\")); "
        "frappe.destroy(); "
        "print(json.dumps({'queue_depths': q_depths, 'queue_backlog_total': sum(q_depths.values()), 'redis_queue_rtt_ms': redis_ping_ms, 'mariadb_global_status': status_rows}))"
    )
    cmd = ["docker", "compose"]
    if compose_file is not None:
        cmd.extend(["-f", str(compose_file)])
    cmd.extend(
        [
            "-p",
            compose_project,
            "exec",
            "-T",
            "-e",
            "FRAPPE_STREAM_LOGGING=1",
            "backend",
            "/home/frappe/frappe-bench/env/bin/python",
            "-c",
            probe,
        ]
    )
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=60,
    )
    return json.loads(res.stdout.strip().splitlines()[-1])


def build_benchmark_specification(
    *,
    profile_name: str,
    base_url: str,
    site_name: str,
    compose_project: str,
    confirm_isolated_target: bool,
    allow_isolated_private_ip: bool = False,
) -> dict[str, object]:
    if profile_name not in PUBLISHED_PROFILES:
        raise ValueError(
            f"Unknown profile {profile_name!r}; expected one of {sorted(PUBLISHED_PROFILES)}"
        )
    isolation = enforce_isolated_benchmark_target(
        base_url=base_url,
        site_name=site_name,
        compose_project=compose_project,
        confirm_isolated_target=confirm_isolated_target,
        allow_isolated_private_ip=allow_isolated_private_ip,
    )
    scenarios = (
        list(S1_API_SCENARIOS)
        if profile_name == "s1_suggested_mvp"
        else list(PUBLISHED_SCENARIOS)
    )
    return {
        "schema_version": "cskh-p4-isolated-benchmark-v1",
        "work_package": "P4-06",
        "gate_alignment": "G18",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sla_commitment_status": "UNCOMMITTED_PENDING_MEASURED_RUN_AND_OWNER_APPROVAL",
        "dataset_disclaimer": (
            "S1 (50,000 hồ sơ / 75,000 Work Items / 250,000 Attempts / 40 staff) is a suggested "
            "synthetic dataset for isolated performance/capacity measurement; it is NOT a commitment "
            "of 50,000 hồ sơ/tháng and NOT a committed production SLA."
        ),
        "isolation_guard": isolation,
        "profile_name": profile_name,
        "published_dataset_and_load": PUBLISHED_PROFILES[profile_name],
        "scenarios": scenarios,
        "measured_metrics_contract": [
            "p50_ms",
            "p95_ms",
            "p99_ms",
            "throughput_rps",
            "error_rate_pct",
            "queue_depths_and_lag",
            "container_cpu_ram",
            "mariadb_connections_and_row_locks",
        ],
    }


def _run_http_wave(
    scenarios: list[dict[str, str]],
    *,
    base_url: str,
    site_name: str,
    cookie_header: str,
    concurrency: int,
    requests_per_scenario: int,
) -> tuple[dict[str, dict[str, object]], dict[str, float | int]]:
    scenario_results: dict[str, dict[str, object]] = {}
    all_latencies: list[float] = []
    all_errors = 0
    overall_t0 = time.perf_counter()

    def _one_call(path: str, method: str) -> tuple[float, bool]:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}{path}",
            headers={"Host": site_name, "Cookie": cookie_header},
            method=method,
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _ = r.read()
                ok = 200 <= r.status < 400
        except urllib.error.HTTPError as exc:
            if exc.fp:
                _ = exc.read()
            ok = False
        except Exception:
            ok = False
        return (time.perf_counter() - t0) * 1000.0, ok

    for sc in scenarios:
        sc_id = sc["id"]
        sc_path = sc["path"]
        sc_method = sc["method"]
        t_sc = time.perf_counter()
        lats: list[float] = []
        errs = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [
                pool.submit(_one_call, sc_path, sc_method)
                for _ in range(requests_per_scenario)
            ]
            for fut in concurrent.futures.as_completed(futs):
                lat_ms, ok = fut.result()
                lats.append(lat_ms)
                if not ok:
                    errs += 1
        elapsed = time.perf_counter() - t_sc
        scenario_results[sc_id] = {
            "workload_class": sc["workload_class"],
            "path": sc_path,
            "concurrency": concurrency,
            **summarize_latencies_ms(lats, error_count=errs, elapsed_sec=elapsed),
        }
        all_latencies.extend(lats)
        all_errors += errs

    overall_elapsed = time.perf_counter() - overall_t0
    return scenario_results, summarize_latencies_ms(
        all_latencies, error_count=all_errors, elapsed_sec=overall_elapsed
    )


def execute_isolated_benchmark(
    spec: dict[str, object],
    *,
    base_url: str,
    site_name: str,
    compose_project: str,
    admin_user: str,
    admin_password: str,
    compose_file: Path | None = None,
    compose_env: dict[str, str] | None = None,
) -> dict[str, object]:
    profile = spec["published_dataset_and_load"]
    assert isinstance(profile, dict)
    concurrency = int(profile["concurrency"])
    requests_per_scenario = int(profile["requests_per_scenario"])
    scenarios = list(spec.get("scenarios") or PUBLISHED_SCENARIOS)  # type: ignore[arg-type]

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_body = urllib.parse.urlencode(
        {"usr": admin_user, "pwd": admin_password}
    ).encode("utf-8")
    login_req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/method/login",
        data=login_body,
        headers={
            "Host": site_name,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with opener.open(login_req, timeout=20) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Benchmark login failed with HTTP {resp.status}")
    cookie_header = "; ".join(f"{c.name}={c.value}" for c in jar)

    pre_db_queue = _collect_queue_and_db_telemetry(
        compose_project, site_name, compose_file=compose_file, env=compose_env
    )
    pre_stats = _collect_docker_stats(
        compose_project, compose_file=compose_file, env=compose_env
    )

    scenario_results, overall_summary = _run_http_wave(
        scenarios,
        base_url=base_url,
        site_name=site_name,
        cookie_header=cookie_header,
        concurrency=concurrency,
        requests_per_scenario=requests_per_scenario,
    )

    peak_staff_wave: dict[str, object] | None = None
    peak_staff_conc = int(profile.get("peak_staff_concurrency") or 0)
    if peak_staff_conc > concurrency:
        wave40_scenarios, wave40_overall = _run_http_wave(
            [
                s
                for s in scenarios
                if s["id"]
                in (
                    "telesale_worklist_page",
                    "my_customers_worklist_api",
                    "bod_executive_overview_api",
                    "kpi_metric_drilldown_250k_api",
                )
            ],
            base_url=base_url,
            site_name=site_name,
            cookie_header=cookie_header,
            concurrency=peak_staff_conc,
            requests_per_scenario=160,
        )
        peak_staff_wave = {
            "concurrency": peak_staff_conc,
            "overall_summary": wave40_overall,
            "scenario_summaries": wave40_scenarios,
        }

    post_stats = _collect_docker_stats(
        compose_project, compose_file=compose_file, env=compose_env
    )
    post_db_queue = _collect_queue_and_db_telemetry(
        compose_project, site_name, compose_file=compose_file, env=compose_env
    )

    out: dict[str, object] = {
        **spec,
        "status": "MEASURED",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_summary": overall_summary,
        "scenario_summaries": scenario_results,
        "queue_and_db_before": pre_db_queue,
        "queue_and_db_after": post_db_queue,
        "container_resources_before": pre_stats,
        "container_resources_after": post_stats,
    }
    if peak_staff_wave is not None:
        out["peak_40_staff_concurrent_wave"] = peak_staff_wave
    return out


S1_IN_CONTAINER_BENCH_SCRIPT = r"""
import gc
import io
import json
import math
import os
import platform
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import frappe
import redis

from crm_cskh.api import p3_reporting
from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services import contact_attempt, distribution, import_service, read_model
from crm_cskh.services.attribution import AUTHORITATIVE
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import execute_kpi_recompute


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return round(s[0], 3)
    k = (len(s) - 1) * (p / 100.0)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return round(s[f], 3)
    return round(s[f] * (c - k) + s[c] * (k - f), 3)


def _summarize(lats, errs, elapsed_s):
    total = len(lats)
    s = sorted(lats)
    return {
        "count": total,
        "error_count": errs,
        "error_rate_pct": round((errs / total * 100.0) if total else 0.0, 4),
        "throughput_ops_sec": round((total / elapsed_s) if elapsed_s > 0 else 0.0, 3),
        "min_ms": round(s[0], 3) if s else 0.0,
        "p50_ms": _pct(s, 50.0),
        "p95_ms": _pct(s, 95.0),
        "p99_ms": _pct(s, 99.0),
        "max_ms": round(s[-1], 3) if s else 0.0,
    }


def _rss_hwm_mib():
    rss, hwm = 0.0, 0.0
    p = Path("/proc/self/status")
    if p.is_file():
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ln.startswith("VmRSS:"):
                rss = round(int(ln.split()[1]) / 1024.0, 2)
            elif ln.startswith("VmHWM:"):
                hwm = round(int(ln.split()[1]) / 1024.0, 2)
    return rss, hwm


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


site = os.environ["BENCH_SITE"]
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


def _ensure_user(email, full_name, roles):
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
    return email


bod_user = _ensure_user("bod.bench@dev.local", "BOD Bench", ["CSKH BOD Viewer"])
admin_user = _ensure_user("admin.bench@dev.local", "Admin Bench", ["CSKH CRM Admin"])

team_nodes = []
staff_users = []
staff_team_map = {}

# Exact S1 staff count: 40 active staff across 4 teams of 10
for t_idx in range(1, 5):
    leader_email = _ensure_user(
        f"leader.bench.{t_idx}@dev.local",
        f"Leader Bench {t_idx}",
        ["Sales Manager", "Sales User"],
    )
    t_title = f"Team S1 {t_idx}"
    t_doc = frappe.get_doc(
        {
            "doctype": "CRM Sales Hierarchy",
            "full_name": t_title,
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

# Seed distribution rules on shared campaign campaigns[4] across all 40 staff
for s_email in staff_users:
    frappe.get_doc(
        {
            "doctype": "CSKH Assignment Rule",
            "campaign": campaigns[4],
            "user": s_email,
            "team": staff_team_map[s_email],
            "weight": 1,
            "capacity_limit": 0,
            "enabled": 1,
            "version": 1,
        }
    ).insert(ignore_permissions=True)
frappe.db.commit()

# Bulk-insert exact S1 dataset:
# - 50,000 CRM Lead (0..49,999)
# - 75,000 CSKH Work Item (0..74,999; 0..49,999 on primary campaign, 50,000..74,999 on distinct 2nd campaign)
# - 250,000 CSKH Contact Attempt (WI 0..24,999 -> 4 attempts = 100k; WI 25,000..74,999 -> 3 attempts = 150k)
# - 150,000 CSKH SLA Obligation (2 per Work Item)
lead_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "naming_series", "first_name", "lead_name", "mobile_no", "status", "lead_owner", "cskh_do_not_contact",
]
wi_cols = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
    "lead", "campaign", "status", "assigned_to", "assigned_at", "owner_at_close",
    "closed_by", "closed_at", "attempt_count", "connected_attempt_count",
    "first_attempt_at", "last_attempt_at", "last_outcome_code", "last_outcome_label", "revision",
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

# 1. Insert 50,000 Leads in 5 chunks of 10,000
# Active open working backlog = 1,600 Work Items (40 active per staff across disjoint leads
# 24,200..24,999 and 49,200..49,999 on 2026-10-27); remaining 73,400 Work Items across Oct 1..26 are closed.
for l_start in range(0, 50_000, 10_000):
    l_rows = []
    for li in range(l_start, l_start + 10_000):
        has_open_wi = (24_200 <= li < 25_000) or (49_200 <= li < 50_000)
        day_num = 27 if has_open_wi else ((li % 26) + 1)
        base_ts = datetime(2026, 10, day_num, 8 + (li % 4), (li * 7) % 50, 0)
        owner_u = staff_users[li % 40]
        l_rows.append(
            (
                f"S1-LEAD-{li:06d}", base_ts, base_ts, "Administrator", "Administrator", 0, 0,
                "CRM-LEAD-", f"Ứng viên S1 {li:06d}", f"Ứng viên S1 {li:06d}", f"091{li:07d}",
                "Contacted" if has_open_wi else "Converted", owner_u if has_open_wi else None, 0,
            )
        )
    _bulk_insert("tabCRM Lead", lead_cols, l_rows)

# 2. Insert 75,000 Work Items + 250,000 Contact Attempts + 150,000 SLA Obligations in blocks of 7,500
active_sample_wis = []
for w_start in range(0, 75_000, 7_500):
    w_end = min(w_start + 7_500, 75_000)
    wi_rows, ah_rows, att_rows, sla_rows = [], [], [], []
    for i in range(w_start, w_end):
        lead_idx = i if i < 50_000 else (i - 50_000)
        lead_id = f"S1-LEAD-{lead_idx:06d}"
        s_idx = lead_idx % 40
        owner_u = staff_users[s_idx]
        team_u = staff_team_map[owner_u]
        primary_camp_idx = s_idx // 10  # 0..3 matching team_scope
        camp = campaigns[primary_camp_idx] if i < 50_000 else campaigns[4]

        is_open = (49_200 <= i < 50_000) or (74_200 <= i < 75_000)
        is_closed = not is_open
        day_num = 27 if is_open else ((i % 26) + 1)
        hour_num = 8 + (i % 8)
        if hour_num >= 12:
            hour_num += 1
        base_ts = datetime(2026, 10, day_num, hour_num, (i * 5) % 40, 0)
        wi_id = f"S1-WI-{i:06d}"
        if is_open and i < 50_000 and len(active_sample_wis) < 120:
            active_sample_wis.append((wi_id, owner_u))

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

for tbl in (
    "tabCRM Lead",
    "tabCSKH Work Item",
    "tabCSKH Assignment History",
    "tabCSKH Contact Attempt",
    "tabCSKH SLA Obligation",
):
    frappe.db.sql(f"ANALYZE TABLE `{tbl}`")
frappe.db.commit()
seed_elapsed_s = round(time.perf_counter() - t_seed0, 2)

dataset_counts = {
    "crm_leads": int(frappe.db.count("CRM Lead")),
    "cskh_work_items": int(frappe.db.count("CSKH Work Item")),
    "cskh_contact_attempts": int(frappe.db.count("CSKH Contact Attempt")),
    "cskh_sla_obligations": int(frappe.db.count("CSKH SLA Obligation")),
    "cskh_assignment_history": int(frappe.db.count("CSKH Assignment History")),
    "active_staff_users": len(staff_users),
    "teams": len(team_nodes),
    "campaigns": len(campaigns),
    "seed_elapsed_sec": seed_elapsed_s,
}

# 3. Measure Full Monthly KPI Recompute (2026-10) & Single-Day Incremental Recompute (2026-10-27)
gc.collect()
rss_b0, _ = _rss_hwm_mib()
t_m0 = time.perf_counter()
month_spec = resolve_period_spec("monthly", "2026-10")
month_res = execute_kpi_recompute(month_spec, run_mode="full", actor="Administrator")
frappe.db.commit()
monthly_recompute_ms = round((time.perf_counter() - t_m0) * 1000.0, 2)
rss_m1, hwm_m1 = _rss_hwm_mib()

t_d0 = time.perf_counter()
day_spec = resolve_period_spec("daily", "2026-10-27")
day_res = execute_kpi_recompute(day_spec, run_mode="incremental", actor="Administrator")
frappe.db.commit()
daily_recompute_ms = round((time.perf_counter() - t_d0) * 1000.0, 2)
rss_d1, hwm_d1 = _rss_hwm_mib()

# 4. Measure Domain Read & Write Workloads at S1 Scale
# 4a. Staff My Customers list_work_items (N=60 across all 40 staff)
wl_lats, wl_errs = [], 0
t0 = time.perf_counter()
for idx in range(60):
    actor = staff_users[idx % 40]
    c0 = time.perf_counter()
    try:
        res = read_model.list_work_items(page=1, page_size=20, actor=actor)
        ok = isinstance(res, dict) and "items" in res
    except Exception:
        ok = False
    wl_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        wl_errs += 1
worklist_summary = _summarize(wl_lats, wl_errs, time.perf_counter() - t0)

# 4b. Work Item detail + Attempt timeline (N=60)
det_lats, det_errs = [], 0
t0 = time.perf_counter()
for idx in range(60):
    wi_id, actor = active_sample_wis[idx % len(active_sample_wis)]
    c0 = time.perf_counter()
    try:
        d1 = read_model.get_work_item_detail(wi_id, actor=actor)
        d2 = read_model.list_attempts_timeline(wi_id, page=1, page_size=20, actor=actor)
        ok = bool(d1) and bool(d2)
    except Exception:
        ok = False
    det_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        det_errs += 1
detail_history_summary = _summarize(det_lats, det_errs, time.perf_counter() - t0)

# 4c. Transactional Contact Attempt + Followup Write + Idempotent Replay (N=50)
wr_lats, wr_errs = [], 0
future_due_str = (frappe.utils.now_datetime() + timedelta(days=3)).strftime("%Y-%m-%d 09:30:00")
idempotent_replay_verified = False
t0 = time.perf_counter()
for idx in range(50):
    wi_id, actor = active_sample_wis[idx]
    idem_key = f"s1-live-write-{idx:04d}"
    c0 = time.perf_counter()
    try:
        r_write = contact_attempt.record_attempt(
            work_item=wi_id,
            channel="phone_call",
            outcome_code="connected_interested",
            note=f"Ghi nhận liên hệ S1 {idx:04d}",
            followup={
                "due_at": future_due_str,
                "title": f"Gọi lại ứng viên {idx:04d}",
                "description": "Kiểm tra lịch phỏng vấn",
            },
            idempotency_key=idem_key,
            actor=actor,
        )
        frappe.db.commit()
        ok = bool(r_write and r_write.get("attempt_id"))
        if ok and idx == 0:
            r_rep = contact_attempt.record_attempt(
                work_item=wi_id,
                channel="phone_call",
                outcome_code="connected_interested",
                note=f"Ghi nhận liên hệ S1 {idx:04d}",
                followup={
                    "due_at": future_due_str,
                    "title": f"Gọi lại ứng viên {idx:04d}",
                    "description": "Kiểm tra lịch phỏng vấn",
                },
                idempotency_key=idem_key,
                actor=actor,
            )
            idempotent_replay_verified = bool(
                r_rep and r_rep.get("idempotent_replay") is True and r_rep.get("attempt_id") == r_write.get("attempt_id")
            )
    except Exception:
        frappe.db.rollback()
        ok = False
    wr_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        wr_errs += 1
attempt_write_summary = {
    **_summarize(wr_lats, wr_errs, time.perf_counter() - t0),
    "idempotent_replay_verified": idempotent_replay_verified,
}

# 4d. BOD Executive Overview (verify 0 Contact Attempt table scans) & Drill-down on 250k Attempts
captured_sql = []
orig_sql = frappe.db.sql
def _spy_sql(q, *a, **kw):
    captured_sql.append(str(q))
    return orig_sql(q, *a, **kw)

frappe.set_user(bod_user)
frappe.db.sql = _spy_sql
try:
    _ = p3_reporting.get_executive_overview(period_type="monthly", period_key="2026-10")
finally:
    frappe.db.sql = orig_sql
attempt_scans_on_dashboard = sum(1 for q in captured_sql if "tabCSKH Contact Attempt" in q)

bod_lats, bod_errs = [], 0
t0 = time.perf_counter()
for _ in range(40):
    c0 = time.perf_counter()
    try:
        r = p3_reporting.get_executive_overview(period_type="monthly", period_key="2026-10")
        ok = bool(r)
    except Exception:
        ok = False
    bod_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        bod_errs += 1
bod_overview_summary = {
    **_summarize(bod_lats, bod_errs, time.perf_counter() - t0),
    "contact_attempt_table_scans_during_read": attempt_scans_on_dashboard,
}

# Campaign-scoped daily slice drill-down on the 250k-attempt / 75k-work-item DB (N=40)
dd_lats, dd_errs = [], 0
t0 = time.perf_counter()
for pg in range(40):
    c0 = time.perf_counter()
    try:
        r = p3_reporting.get_metric_drilldown(
            period_type="daily",
            period_key="2026-10-27",
            scope_type="campaign",
            scope_id=campaigns[0],
            metric_code="CONNECTED_RATE",
            page=(pg % 5) + 1,
            page_size=20,
        )
        ok = bool(r)
    except Exception:
        ok = False
    dd_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        dd_errs += 1
drilldown_summary = _summarize(dd_lats, dd_errs, time.perf_counter() - t0)

# Unscoped full-month company drill-down across all 250k attempts (N=2) to quantify full-period Python scan ceiling
dd_month_lats, dd_month_errs = [], 0
t0 = time.perf_counter()
for pg in range(2):
    c0 = time.perf_counter()
    try:
        r = p3_reporting.get_metric_drilldown(
            period_type="monthly",
            period_key="2026-10",
            scope_type="company",
            metric_code="CONNECTED_RATE",
            page=pg + 1,
            page_size=20,
        )
        ok = bool(r)
    except Exception:
        ok = False
    dd_month_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        dd_month_errs += 1
drilldown_full_month_summary = _summarize(dd_month_lats, dd_month_errs, time.perf_counter() - t0)

# 5. CSV Import (500 rows) + Campaign Distribution across 40 staff + Redis RQ Queue Lag Drain
frappe.set_user("Administrator")
csv_lines = ["full_name,phone,email,source"]
for r_i in range(500):
    csv_lines.append(f"Ứng viên Import S1 {r_i:04d},09887{r_i:05d},s1imp{r_i:04d}@dev.local,Website")
csv_bytes = ("\n".join(csv_lines) + "\n").encode("utf-8")

t_imp0 = time.perf_counter()
file_doc = frappe.get_doc(
    {
        "doctype": "File",
        "file_name": "s1_benchmark_500.csv",
        "is_private": 1,
        "content": csv_bytes,
    }
).insert(ignore_permissions=True)
frappe.db.commit()

prev = import_service.create_preview(
    file_name=file_doc.name,
    mapping={"name": "full_name", "phone": "phone", "source": "source"},
    actor="Administrator",
    idempotency_key="s1-import-500",
)
frappe.db.commit()
batch_id = prev["batch"]
import_service.confirm(batch_id, actor="Administrator")
frappe.db.commit()

# Poll RQ worker completion for import batch (`CSKH Import Batch.status` uses uppercase states)
imp_deadline = time.perf_counter() + 90.0
imp_status = {}
while time.perf_counter() < imp_deadline:
    frappe.db.commit()
    imp_status = import_service._status_response(batch_id)
    if str(imp_status.get("status") or "").upper() in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
        break
    time.sleep(0.25)
imp_total_ms = round((time.perf_counter() - t_imp0) * 1000.0, 2)

# Distribution preview + execute on shared campaign campaigns[4] across 40 staff
t_dist0 = time.perf_counter()
dist_prev = distribution.preview(campaigns[4], actor="Administrator", idempotency_key="s1-dist-prev-1")
frappe.db.commit()
dist_preview_ms = round((time.perf_counter() - t_dist0) * 1000.0, 2)
dist_exec_ms = 0.0
dist_run_status = "no_unassigned_pool"
dist_eligible_count = int((dist_prev.get("summary") or {}).get("eligible_count") or 0)
if dist_prev.get("preview_id") and dist_eligible_count > 0:
    t_de0 = time.perf_counter()
    dist_enq = distribution.enqueue_run(
        dist_prev["preview_id"],
        expected_rule_version=dist_prev.get("rule_version"),
        idempotency_key="s1-dist-exec-1",
        actor="Administrator",
        enqueue_job=True,
    )
    frappe.db.commit()
    run_id = dist_enq["run_id"]
    d_deadline = time.perf_counter() + 120.0
    while time.perf_counter() < d_deadline:
        frappe.db.commit()
        r_info = distribution.get_run(run_id, actor="Administrator")
        dist_run_status = str(r_info.get("status") or "")
        if dist_run_status.lower() in ("completed", "completed_with_errors", "failed", "aborted"):
            break
        time.sleep(0.25)
    dist_exec_ms = round((time.perf_counter() - t_de0) * 1000.0, 2)

# Direct Redis RQ 50-job burst queue lag measurement (using Frappe bench-prefixed queue key)
from frappe.utils.background_jobs import generate_qname
rq_conn = redis.from_url(frappe.conf.redis_queue)
short_q_key = f"rq:queue:{generate_qname('short')}"
all_q_keys = [f"rq:queue:{generate_qname(q)}" for q in ("short", "default", "long")]
t_rq0 = time.perf_counter()
last_rq_job = None
for j_idx in range(50):
    last_rq_job = frappe.enqueue("frappe.ping", queue="short", job_name=f"s1-lag-probe-{j_idx:03d}")
enqueue_50_ms = round((time.perf_counter() - t_rq0) * 1000.0, 2)
peak_q_depth = int(rq_conn.llen(short_q_key))
last_job_key = f"rq:job:{last_rq_job.id}" if getattr(last_rq_job, "id", None) else None
t_drain0 = time.perf_counter()
drain_deadline = t_drain0 + 60.0
while time.perf_counter() < drain_deadline:
    rem = sum(int(rq_conn.llen(qk)) for qk in all_q_keys)
    j_done = True
    if last_job_key:
        j_st = rq_conn.hget(last_job_key, "status")
        j_done = j_st in (b"finished", b"failed", b"stopped", b"canceled", "finished", "failed", None)
    if rem == 0 and j_done:
        break
    time.sleep(0.05)
drain_50_ms = round((time.perf_counter() - t_drain0) * 1000.0, 2)

# 6. MariaDB EXPLAIN plans on S1 tables
explain_plans = {
    "worklist_by_owner": frappe.db.sql(
        "EXPLAIN SELECT name, lead, campaign, status, last_attempt_at "
        "FROM `tabCSKH Work Item` WHERE assigned_to = %s AND status = 'in_progress' "
        "ORDER BY modified DESC LIMIT 20",
        (staff_users[0],),
        as_dict=True,
    ),
    "attempt_timeline_by_work_item": frappe.db.sql(
        "EXPLAIN SELECT name, outcome_code, occurred_at "
        "FROM `tabCSKH Contact Attempt` WHERE work_item = 'S1-WI-000010' "
        "ORDER BY occurred_at DESC LIMIT 20",
        as_dict=True,
    ),
    "kpi_snapshot_lookup": frappe.db.sql(
        "EXPLAIN SELECT name, metrics_json FROM `tabCSKH KPI Snapshot` "
        "WHERE period_type = 'monthly' AND period_key = '2026-10' AND scope_type = 'company' LIMIT 5",
        as_dict=True,
    ),
}

out = {
    "runner_hardware": {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
    },
    "s1_seeded_counts": dataset_counts,
    "primary_campaign_id": campaigns[0],
    "kpi_recompute_s1": {
        "monthly_full_75k_wi_250k_att_ms": monthly_recompute_ms,
        "monthly_run_status": month_res.get("status") if isinstance(month_res, dict) else "ok",
        "monthly_rss_after_mib": rss_m1,
        "monthly_hwm_mib": hwm_m1,
        "daily_incremental_ms": daily_recompute_ms,
        "daily_rss_after_mib": rss_d1,
    },
    "domain_workloads_s1": {
        "my_customers_list_work_items": worklist_summary,
        "work_item_detail_and_attempt_timeline": detail_history_summary,
        "contact_attempt_and_followup_write": attempt_write_summary,
        "bod_executive_overview": bod_overview_summary,
        "kpi_metric_drilldown_250k_attempts": drilldown_summary,
        "kpi_metric_drilldown_full_month_company_250k": drilldown_full_month_summary,
    },
    "async_import_distribution_and_queue_lag": {
        "csv_import_500_rows": {
            "total_preview_confirm_and_worker_drain_ms": imp_total_ms,
            "final_batch_status": imp_status.get("status"),
            "created_rows": imp_status.get("created_rows"),
            "rows_throughput_per_sec": round(500.0 / (imp_total_ms / 1000.0), 2) if imp_total_ms > 0 else 0.0,
        },
        "campaign_distribution_40_staff": {
            "eligible_count": dist_eligible_count,
            "preview_ms": dist_preview_ms,
            "execute_and_worker_drain_ms": dist_exec_ms,
            "final_run_status": dist_run_status,
        },
        "rq_short_50_job_burst": {
            "enqueue_50_jobs_ms": enqueue_50_ms,
            "peak_queue_short_depth": peak_q_depth,
            "drain_to_zero_lag_ms": drain_50_ms,
            "effective_worker_drain_jobs_per_sec": round(50.0 / (drain_50_ms / 1000.0), 2) if drain_50_ms > 0 else 0.0,
        },
    },
    "mariadb_explain_plans": explain_plans,
}
frappe.destroy()
print("S1_BENCH_JSON:" + json.dumps(out, default=str))
"""


def _derive_limits_and_proposed_slos(
    s1_in_container: dict[str, object],
    http_report: dict[str, object],
) -> dict[str, object]:
    sc = http_report.get("scenario_summaries") or {}
    assert isinstance(sc, dict)
    wave40 = http_report.get("peak_40_staff_concurrent_wave") or {}
    assert isinstance(wave40, dict)
    dom = s1_in_container.get("domain_workloads_s1") or {}
    assert isinstance(dom, dict)
    kpi_rec = s1_in_container.get("kpi_recompute_s1") or {}
    assert isinstance(kpi_rec, dict)
    async_q = s1_in_container.get("async_import_distribution_and_queue_lag") or {}
    assert isinstance(async_q, dict)

    wl_api_p95 = float((sc.get("my_customers_worklist_api") or {}).get("p95_ms") or 0.0)
    bod_api_p95 = float(
        (sc.get("bod_executive_overview_api") or {}).get("p95_ms") or 0.0
    )
    dd_api_p95 = float(
        (sc.get("kpi_metric_drilldown_250k_api") or {}).get("p95_ms") or 0.0
    )
    dd_month_p95 = float(
        (dom.get("kpi_metric_drilldown_full_month_company_250k") or {}).get("p95_ms")
        or 0.0
    )
    wr_p95 = float(
        (dom.get("contact_attempt_and_followup_write") or {}).get("p95_ms") or 0.0
    )
    month_ms = float(kpi_rec.get("monthly_full_75k_wi_250k_att_ms") or 0.0)
    daily_ms = float(kpi_rec.get("daily_incremental_ms") or 0.0)

    return {
        "observed_environment_limits": [
            (
                "Gunicorn worker concurrency ceiling: isolated compose topology runs "
                "`GUNICORN_WORKERS=2, GUNICORN_THREADS=4` (8 concurrent Python request threads). "
                "At `concurrency=12` latency stays tight; at `concurrency=40` simultaneous burst "
                "(all 40 staff firing in the exact same millisecond without think-time), queueing "
                "behind 8 Gunicorn worker threads increases tail `p95`/`p99` while keeping 0% HTTP 5xx."
            ),
            (
                f"Full monthly KPI recompute (`75,000` Work Items / `250,000` Attempts / `150,000` "
                f"SLA Obligations) completes in `{month_ms} ms` in-process (single-day incremental: "
                f"`{daily_ms} ms`), confirming full-month recompute belongs on background `queue-long` / "
                "scheduled off-peak jobs while daytime updates use dirty-scope incremental recompute."
            ),
            (
                f"Drill-down period scope ceiling: `get_metric_drilldown` loads raw period rows via "
                f"`load_period_raw_dataset()` before Python slicing (`daily` slice HTTP `p95={dd_api_p95} ms` vs "
                f"unscoped full-month `company` `250k`-attempt `p95={dd_month_p95} ms`). Interactive drill-downs "
                "at `S1` scale should default to `daily`/`weekly` or `campaign`/`team`-scoped filters."
            ),
            (
                "Single `queue-short` worker drains short bursts serially; bulk CSV imports (>1,000 rows), "
                "campaign distribution runs, and full-month KPI recomputes must stay routed to dedicated "
                "`queue-long` in 10-service Compose topology so interactive `short` jobs are never blocked."
            ),
        ],
        "proposed_slos_for_review": {
            "status": "PROPOSED_ONLY_NOT_COMMITTED_SLA",
            "target_sizing_assumption": "4 vCPU / 8-16 GB RAM isolated host, Gunicorn 3-4 workers x 4 threads, dedicated queue-short + queue-long",
            "targets": {
                "my_customers_worklist_api_p95_ms": {
                    "measured_c12_p95_ms": wl_api_p95,
                    "proposed_slo_p95_ms": max(
                        500, int(math.ceil(wl_api_p95 * 2.5 / 50.0) * 50)
                    ),
                },
                "contact_attempt_write_p95_ms": {
                    "measured_in_tx_p95_ms": wr_p95,
                    "proposed_slo_p95_ms": max(
                        600, int(math.ceil(wr_p95 * 2.5 / 50.0) * 50)
                    ),
                },
                "bod_executive_overview_api_p95_ms": {
                    "measured_c12_p95_ms": bod_api_p95,
                    "proposed_slo_p95_ms": max(
                        800, int(math.ceil(bod_api_p95 * 2.5 / 50.0) * 50)
                    ),
                },
                "kpi_drilldown_paginated_api_p95_ms": {
                    "measured_c12_p95_ms": dd_api_p95,
                    "proposed_slo_p95_ms": max(
                        1200, int(math.ceil(dd_api_p95 * 2.5 / 50.0) * 50)
                    ),
                },
                "http_5xx_error_rate_pct": {
                    "proposed_slo_max_pct": 0.5,
                },
                "daily_incremental_kpi_refresh_lag_sec": {
                    "measured_daily_recompute_ms": daily_ms,
                    "proposed_slo_max_sec": 300,
                },
            },
        },
    }


def provision_and_execute_s1_benchmark(
    *,
    app_image: str,
    pull_policy: str = "missing",
    port: int = 18880,
    site_name: str = "p4-s1-bench.localhost",
) -> dict[str, object]:
    """Self-contained isolated S1 provisioning, seeding, measurement, and teardown."""
    compose_project = f"cskh-p4-bench-{secrets.token_hex(4)}"
    base_url = f"http://127.0.0.1:{port}"
    spec = build_benchmark_specification(
        profile_name="s1_suggested_mvp",
        base_url=base_url,
        site_name=site_name,
        compose_project=compose_project,
        confirm_isolated_target=True,
    )
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    db_root_pw = secrets.token_hex(24)
    admin_pw = secrets.token_hex(24)

    with tempfile.TemporaryDirectory(prefix="cskh-p4-s1-bench-") as tmp_dir_str:
        db_secret = Path(tmp_dir_str) / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
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
            "-f",
            str(VERIFY_COMPOSE_FILE),
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

            # Create isolated S1 benchmark site + install crm + install crm_cskh + migrate
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
                    f"S1 site bootstrap failed (rc={init_proc.returncode}):\n"
                    f"STDOUT:\n{init_proc.stdout[-1500:]}\nSTDERR:\n{init_proc.stderr[-1500:]}"
                )

            # Restart workers + nginx so Gunicorn, RQ workers, and nginx upstream DNS pick up the new site
            subprocess.run(
                [
                    *compose_base,
                    "restart",
                    "backend",
                    "websocket",
                    "queue-short",
                    "scheduler",
                    "frontend",
                ],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Execute S1 seeding + in-container domain, recompute, import, distribution & RQ lag probes
            s1_proc = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    "FRAPPE_STREAM_LOGGING=1",
                    "-e",
                    f"BENCH_SITE={site_name}",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-",
                ],
                input=S1_IN_CONTAINER_BENCH_SCRIPT,
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if s1_proc.returncode != 0:
                raise RuntimeError(
                    f"S1 in-container benchmark failed (rc={s1_proc.returncode}):\n"
                    f"STDOUT:\n{s1_proc.stdout[-2000:]}\nSTDERR:\n{s1_proc.stderr[-2000:]}"
                )
            marker_lines = [
                ln[len("S1_BENCH_JSON:") :]
                for ln in s1_proc.stdout.splitlines()
                if ln.startswith("S1_BENCH_JSON:")
            ]
            if not marker_lines:
                raise RuntimeError(
                    f"S1 in-container benchmark did not emit S1_BENCH_JSON marker: {s1_proc.stderr[-500:]}"
                )
            s1_in_container = json.loads(marker_lines[-1])
            primary_camp = str(s1_in_container.get("primary_campaign_id") or "").strip()
            if primary_camp:
                spec["scenarios"] = [
                    (
                        {
                            **s,
                            "path": (
                                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                                f"?period_type=daily&period_key=2026-10-27&scope_type=campaign"
                                f"&scope_id={urllib.parse.quote(primary_camp)}"
                                "&metric_code=CONNECTED_RATE&page=1&page_size=20"
                            ),
                        }
                        if s.get("id") == "kpi_metric_drilldown_250k_api"
                        else s
                    )
                    for s in (spec.get("scenarios") or [])  # type: ignore[union-attr]
                ]

            # Wait for HTTP ping readiness through nginx -> gunicorn
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                try:
                    req = urllib.request.Request(
                        f"{base_url}/api/method/ping", headers={"Host": site_name}
                    )
                    with urllib.request.urlopen(req, timeout=5) as r:
                        if r.status == 200:
                            break
                except Exception:
                    pass
                time.sleep(2.0)

            http_report = execute_isolated_benchmark(
                spec,
                base_url=base_url,
                site_name=site_name,
                compose_project=compose_project,
                admin_user="Administrator",
                admin_password=admin_pw,
                compose_file=VERIFY_COMPOSE_FILE,
                compose_env=compose_env,
            )

            img_insp = json.loads(
                subprocess.check_output(
                    ["docker", "image", "inspect", app_image], text=True
                )
            )[0]
            limits_and_slos = _derive_limits_and_proposed_slos(
                s1_in_container, http_report
            )

            return {
                **http_report,
                "sla_commitment_status": "UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL",
                "app_image_tested": app_image,
                "image_id": img_insp.get("Id"),
                "s1_domain_and_database_benchmark": s1_in_container,
                **limits_and_slos,
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
    p.add_argument(
        "--profile",
        default="s1_suggested_mvp",
        choices=sorted(PUBLISHED_PROFILES),
    )
    p.add_argument("--base-url", default="http://127.0.0.1:18080")
    p.add_argument("--site-name", default="p4-bench.localhost")
    p.add_argument("--compose-project", default="cskh-p4-bench-isolated")
    p.add_argument(
        "--confirm-isolated-target",
        action="store_true",
        help="Mandatory safety confirmation that target is NOT Owner/Production VPS",
    )
    p.add_argument(
        "--allow-isolated-private-ip",
        action="store_true",
        help="Allow private RFC1918 IP for dedicated isolated Staging VM (never public/Owner VPS)",
    )
    p.add_argument(
        "--plan-only",
        action="store_true",
        help="Validate target isolation & emit published workload spec without sending load",
    )
    p.add_argument(
        "--provision-and-run-s1",
        action="store_true",
        help="Self-provision ephemeral isolated compose stack, seed exact S1 dataset, measure, and tear down",
    )
    p.add_argument(
        "--app-image",
        default="",
        help="Immutable APP_IMAGE (@sha256 digest or local image ref) for --provision-and-run-s1",
    )
    p.add_argument(
        "--pull-policy",
        default="missing",
        choices=("never", "missing", "always"),
    )
    p.add_argument("--port", type=int, default=18880)
    p.add_argument("--admin-user", default="Administrator")
    p.add_argument("--admin-password-env", default="BENCHMARK_ADMIN_PASSWORD")
    p.add_argument("--out", required=True, help="Output JSON benchmark spec/report")
    args = p.parse_args(argv)

    if args.provision_and_run_s1:
        if not args.confirm_isolated_target:
            raise SystemExit(
                "ERROR: --provision-and-run-s1 requires explicit --confirm-isolated-target"
            )
        if not args.app_image.strip():
            raise SystemExit("ERROR: --provision-and-run-s1 requires --app-image")
        report = provision_and_execute_s1_benchmark(
            app_image=args.app_image.strip(),
            pull_policy=args.pull_policy,
            port=args.port,
            site_name=(
                args.site_name
                if args.site_name != "p4-bench.localhost"
                else "p4-s1-bench.localhost"
            ),
        )
    else:
        spec = build_benchmark_specification(
            profile_name=args.profile,
            base_url=args.base_url,
            site_name=args.site_name,
            compose_project=args.compose_project,
            confirm_isolated_target=args.confirm_isolated_target,
            allow_isolated_private_ip=args.allow_isolated_private_ip,
        )
        if args.plan_only:
            report = {**spec, "status": "READY_PLAN_ONLY"}
        else:
            adm_pw = os.environ.get(args.admin_password_env, "").strip()
            if not adm_pw:
                raise SystemExit(
                    f"ERROR: Environment variable {args.admin_password_env} must be set for live benchmark execution"
                )
            report = execute_isolated_benchmark(
                spec,
                base_url=args.base_url,
                site_name=args.site_name,
                compose_project=args.compose_project,
                admin_user=args.admin_user,
                admin_password=adm_pw,
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes((json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(
        json.dumps(
            {
                "status": report["status"],
                "profile": report.get("profile_name", args.profile),
                "sla_commitment_status": report["sla_commitment_status"],
                "out": str(out_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
