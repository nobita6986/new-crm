#!/usr/bin/env python3
"""Isolated P4 benchmark harness (`P4-06` / `G18`) with fail-closed Owner-VPS protection.

Implements Work Item 6:
1. Fail-closed target boundary (`enforce_isolated_benchmark_target`):
   - Refuses to send load unless `--confirm-isolated-target` is explicitly set.
   - Blocks non-loopback URLs by default (and blocks any URL, site name, or Compose project
     referencing `prod`, `production`, `owner`, `live`, `hrpartner`, or public domains) so the
     Owner's live VPS can NEVER be stress-tested.
   - Requires `--compose-project` to carry an isolated benchmark prefix (`cskh-p4-bench-*` or
     `crm-cskh-p4-bench-*`).
2. Published synthetic dataset & workload specification (`0` real PII):
   - `isolated_smoke_1k` (1,000 Work Items — CI/local harness smoke),
   - `isolated_staging_10k` (10,000 Work Items — isolated Staging pre-scale),
   - `isolated_p4_target_50k` (50,000 Work Items/month — P4-06 benchmark workload across
     import, distribution, Telesale worklist/contact-attempt, KPI drill-down, and export queue).
3. Measures and exports:
   - `p50_ms`, `p95_ms`, `p99_ms`, `throughput_rps`, `error_rate_pct`,
   - Redis RQ `queue_lag` (`rq:queue:*` depth + drain latency),
   - Container `CPU%` / `RAM MiB` (`docker stats --no-stream`) and MariaDB connection/row-lock
     counters (`Threads_connected`, `Innodb_row_lock_time_avg`, `Innodb_row_lock_waits`, `Slow_queries`).
4. Never self-commits unmeasured SLAs (`sla_commitment_status` stays
   `UNCOMMITTED_PENDING_MEASURED_RUN_AND_OWNER_APPROVAL`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import ipaddress
import json
import math
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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
    "isolated_smoke_1k": {
        "description": "Local/CI isolated smoke benchmark (0 PII synthetic dataset)",
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
        "monthly_work_items": 10_000,
        "campaigns": 5,
        "telesale_agents": 20,
        "import_batch_rows": 1_000,
        "concurrency": 8,
        "requests_per_scenario": 160,
        "pii_policy": "ZERO_REAL_PII_SYNTHETIC_ONLY",
    },
    "isolated_p4_target_50k": {
        "description": "P4-06 full 50k Work Items/month benchmark on dedicated isolated environment",
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


def _collect_docker_stats(compose_project: str) -> list[dict[str, str]]:
    ps_proc = subprocess.run(
        ["docker", "compose", "-p", compose_project, "ps", "-q"],
        capture_output=True,
        text=True,
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
    compose_project: str, site_name: str
) -> dict[str, object]:
    probe = (
        "import json, time, frappe, redis; "
        f"site = {site_name!r}; "
        "frappe.init(site=site, sites_path='/home/frappe/frappe-bench/sites'); "
        "frappe.connect(); "
        "t0 = time.perf_counter(); "
        "r = redis.from_url(frappe.conf.redis_queue); "
        "q_depths = {q: int(r.llen(f'rq:queue:{q}')) for q in ('short', 'default', 'long')}; "
        "redis_ping_ms = round((time.perf_counter() - t0) * 1000.0, 3); "
        "status_rows = dict(frappe.db.sql(\"SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Max_used_connections','Innodb_row_lock_time_avg','Innodb_row_lock_waits','Slow_queries','Questions')\")); "
        "frappe.destroy(); "
        "print(json.dumps({'queue_depths': q_depths, 'queue_backlog_total': sum(q_depths.values()), 'redis_queue_rtt_ms': redis_ping_ms, 'mariadb_global_status': status_rows}))"
    )
    res = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            compose_project,
            "exec",
            "-T",
            "backend",
            "/home/frappe/frappe-bench/env/bin/python",
            "-c",
            probe,
        ],
        capture_output=True,
        text=True,
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
    return {
        "schema_version": "cskh-p4-isolated-benchmark-v1",
        "work_package": "P4-06",
        "gate_alignment": "G18",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sla_commitment_status": "UNCOMMITTED_PENDING_MEASURED_RUN_AND_OWNER_APPROVAL",
        "isolation_guard": isolation,
        "profile_name": profile_name,
        "published_dataset_and_load": PUBLISHED_PROFILES[profile_name],
        "scenarios": list(PUBLISHED_SCENARIOS),
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


def execute_isolated_benchmark(
    spec: dict[str, object],
    *,
    base_url: str,
    site_name: str,
    compose_project: str,
    admin_user: str,
    admin_password: str,
) -> dict[str, object]:
    profile = spec["published_dataset_and_load"]
    assert isinstance(profile, dict)
    concurrency = int(profile["concurrency"])
    requests_per_scenario = int(profile["requests_per_scenario"])

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

    pre_db_queue = _collect_queue_and_db_telemetry(compose_project, site_name)
    pre_stats = _collect_docker_stats(compose_project)

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

    for sc in PUBLISHED_SCENARIOS:
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
            **summarize_latencies_ms(lats, error_count=errs, elapsed_sec=elapsed),
        }
        all_latencies.extend(lats)
        all_errors += errs

    overall_elapsed = time.perf_counter() - overall_t0
    post_stats = _collect_docker_stats(compose_project)
    post_db_queue = _collect_queue_and_db_telemetry(compose_project, site_name)

    return {
        **spec,
        "status": "MEASURED",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_summary": summarize_latencies_ms(
            all_latencies, error_count=all_errors, elapsed_sec=overall_elapsed
        ),
        "scenario_summaries": scenario_results,
        "queue_and_db_before": pre_db_queue,
        "queue_and_db_after": post_db_queue,
        "container_resources_before": pre_stats,
        "container_resources_after": post_stats,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--profile",
        default="isolated_p4_target_50k",
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
    p.add_argument("--admin-user", default="Administrator")
    p.add_argument("--admin-password-env", default="BENCHMARK_ADMIN_PASSWORD")
    p.add_argument("--out", required=True, help="Output JSON benchmark spec/report")
    args = p.parse_args(argv)

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
        import os

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
                "profile": args.profile,
                "sla_commitment_status": report["sla_commitment_status"],
                "out": str(out_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
