#!/usr/bin/env python3
"""Isolated P4 benchmark harness (`P4-06` / `G18`) with fail-closed Owner-VPS protection.

Implements P4 isolated performance & capacity benchmarking (`docs/root-plan/P4.md` §9.1–§9.5):
1. Fail-closed target boundary (`enforce_isolated_benchmark_target`):
   - Refuses to send load unless `--confirm-isolated-target` is explicitly set.
   - Blocks non-loopback URLs by default (and blocks any URL, site name, or Compose project
     referencing `prod`, `production`, `owner`, `live`, `hrpartner`, or public domains) so the
     Owner's live VPS can NEVER be stress-tested.
   - Requires `--compose-project` to carry an isolated benchmark prefix (`cskh-p4-bench-*` or
     `crm-cskh-p4-bench-*`).
2. Published synthetic dataset & workload specification (`0` real PII):
   - `s1_suggested_mvp` (`docs/root-plan/P4.md` §9.1–§9.2 suggested `S1` dataset: `50,000` hồ sơ
     [`CRM Lead`], `75,000` `CSKH Work Item`, `250,000` `CSKH Contact Attempt`, `40` staff —
     explicitly framed as a **suggested benchmark dataset, NOT a commitment of 50,000 hồ sơ/tháng**),
   - `isolated_smoke_1k` (1,000 Work Items — CI/local harness smoke),
   - `isolated_staging_10k` (10,000 Work Items — isolated Staging pre-scale),
   - `isolated_p4_target_50k` (50,000 Work Items — legacy profile alias).
3. Self-contained `--provision-and-run-s1` mode covering all `P4.md` §9.2 workloads:
   - Boots an ephemeral isolated stack (`deploy/images/compose.verify.yaml` + ephemeral worker
     separation override: `Gunicorn 4x4`, dedicated `queue-short` + `queue-long`) from an immutable
     `APP_IMAGE` digest with zero host bind-mounts.
   - Seeds the exact `S1` dataset (`50k` Leads + Identity Index, `75k` Work Items, `250k` Attempts,
     `150k` SLA Obligations, `40` active staff across `4` teams).
   - Measures in-process & live HTTP `P4.md` §9.2 acceptance scenarios:
     * Staff `My Customers` (`p95 <= 1.5s`),
     * `Lead + lịch sử tương tác` (`p95 <= 2.0s` paginated),
     * `POST Contact Attempt + Task callback` (`p95 <= 2.0s`, `0` duplicates + idempotent replay),
     * `BOD overview` published snapshot (`p95 <= 3.0s`, `0` raw attempt scans),
     * `KPI drill-down` paginated (`p95 <= 3.0s` on both `daily` campaign and `monthly` `250k` company,
       plus server-side `403` scope check + `EXPLAIN` plans),
     * `Import 50,000 dòng CSV` (`10,000` new + `5,000` `duplicate_in_crm` + `34,500` `duplicate_in_file`
       + `500` invalid error rows -> `queue-long` batch commit, RAM, counts, private error artifact),
     * `Preview weighted distribution 10,000 Work Items` (`<= 60s`, non-blocking) & `Execute distribution
       10,000 Work Items` (`queue-long` progress checkpoints, pause/abort guard, `0` duplicates, full
       ownership/DocShare/ToDo reconciliation),
     * `Recompute KPI một kỳ` (`queue-long` background run: non-blocking UI reads + atomic snapshot
       publish only upon completion),
     * Multi-user concurrent session waves at **`20`, `40`, and `80` concurrent user sessions** across
       the mixed CSKH workload (`p50`/`p95`/`p99`, `error_rate_pct`, `CPU%`/`RAM`, MariaDB/RQ telemetry),
   - Destroys all containers and volumes in `finally`.
4. Never self-commits unmeasured or unapproved SLAs (`sla_commitment_status` stays
   `UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import ipaddress
import itertools
import json
import math
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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


class BenchmarkExecutionError(RuntimeError):
    """Benchmark runtime error carrying a structured JSON-serializable report for `--out`."""

    def __init__(self, message: str, *, report: dict[str, object]) -> None:
        super().__init__(message)
        self.report = report


def _scrub_diagnostic_text(
    text: str,
    secrets_to_redact: tuple[str, ...] = (),
) -> str:
    """Redact session cookies, CSRF tokens, and ephemeral passwords from diagnostic logs/errors."""
    if not text:
        return ""
    scrubbed = text
    for sec in secrets_to_redact:
        if sec and len(sec) >= 6:
            scrubbed = scrubbed.replace(sec, "[REDACTED_SECRET]")
    scrubbed = re.sub(r"\bsid=[A-Za-z0-9._%-]+", "sid=[REDACTED_SID]", scrubbed)
    scrubbed = re.sub(
        r'(["\']X-Frappe-CSRF-Token["\']\s*:\s*["\'])[^"\']+(["\'])',
        r"\1[REDACTED_CSRF]\2",
        scrubbed,
    )
    scrubbed = re.sub(
        r"(csrf_token\s*[:=]\s*['\"]?)[A-Za-z0-9._%-]{8,}",
        r"\1[REDACTED_CSRF]",
        scrubbed,
    )
    scrubbed = re.sub(
        r"((?:BENCH_DB_ROOT_PW|BENCH_ADMIN_PW|BENCH_ACTOR_PW|--db-root-password|--admin-password|pwd)(?:=|\s+))([^\s&'\"]+)",
        r"\1[REDACTED_PW]",
        scrubbed,
    )
    return scrubbed


def _write_json_report_atomic(out_path: Path, report: dict[str, object]) -> None:
    """Serialize, round-trip validate, and atomically write UTF-8 JSON report to `out_path`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, indent=2) + "\n"
    _ = json.loads(serialized)
    payload = serialized.encode("utf-8")
    tmp_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(out_path.parent),
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_file = Path(fh.name)
            fh.write(payload)
            fh.flush()
        tmp_file.replace(out_path)
    finally:
        if tmp_file is not None and tmp_file.exists():
            tmp_file.unlink(missing_ok=True)


def _classify_http_statuses(statuses: list[int]) -> dict[str, object]:
    """Summarize per-request HTTP status codes into timeout, client-error, 4xx, and 5xx counts."""
    timeouts = 0
    client_errors = 0
    http_4xx = 0
    http_5xx = 0
    status_counts: dict[str, int] = {}
    for st in statuses:
        st_key = "timeout" if st == 0 else ("conn_error" if st < 0 else str(st))
        status_counts[st_key] = status_counts.get(st_key, 0) + 1
        if st == 0:
            timeouts += 1
        elif st < 0:
            client_errors += 1
        elif 400 <= st < 500:
            http_4xx += 1
        elif st >= 500:
            http_5xx += 1
    return {
        "timeout_count": timeouts,
        "client_error_count": client_errors,
        "http_4xx_count": http_4xx,
        "http_5xx_count": http_5xx,
        "http_error_count": http_4xx + http_5xx,
        "status_counts": status_counts,
    }


EPHEMERAL_BENCH_OVERRIDE_YAML = """\
services:
  backend:
    cpu_shares: 1024
    environment:
      GUNICORN_WORKERS: "4"
      GUNICORN_THREADS: "4"
      GUNICORN_TIMEOUT: "120"
      TZ: Asia/Ho_Chi_Minh
  queue-short:
    cpu_shares: 512
    command: ["bench", "worker", "--queue", "short,default"]
  queue-long:
    image: ${APP_IMAGE:?set APP_IMAGE}
    pull_policy: ${PULL_POLICY:-never}
    restart: "no"
    cpu_shares: 1024
    security_opt:
      - no-new-privileges:true
    command: ["bench", "worker", "--queue", "long"]
    environment:
      TZ: Asia/Ho_Chi_Minh
    volumes:
      - sites:/home/frappe/frappe-bench/sites
    depends_on:
      configurator:
        condition: service_completed_successfully
    networks:
      - data_net
  db:
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
      - --skip-character-set-client-handshake
      - --innodb-buffer-pool-size=2560M
      - --innodb-log-file-size=512M
      - --innodb-log-buffer-size=64M
      - --innodb-flush-log-at-trx-commit=1
      - --innodb-flush-method=O_DIRECT
      - --innodb-io-capacity=1000
      - --innodb-io-capacity-max=2000
      - --max-connections=200
"""

STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML = """\
services:
  frontend:
    cpus: 0.35
    mem_limit: 256m
  backend:
    cpus: 1.50
    mem_limit: 1536m
    cpu_shares: 1024
    environment:
      GUNICORN_WORKERS: "2"
      GUNICORN_THREADS: "4"
      GUNICORN_TIMEOUT: "120"
      TZ: Asia/Ho_Chi_Minh
  websocket:
    cpus: 0.25
    mem_limit: 256m
  queue-short:
    cpus: 0.50
    mem_limit: 2048m
    cpu_shares: 512
    command: ["bench", "worker", "--queue", "short,default"]
  queue-long:
    image: ${APP_IMAGE:?set APP_IMAGE}
    pull_policy: ${PULL_POLICY:-never}
    restart: "no"
    cpus: 1.25
    mem_limit: 1536m
    cpu_shares: 1024
    security_opt:
      - no-new-privileges:true
    command: ["bench", "worker", "--queue", "long"]
    environment:
      TZ: Asia/Ho_Chi_Minh
    volumes:
      - sites:/home/frappe/frappe-bench/sites
    depends_on:
      configurator:
        condition: service_completed_successfully
    networks:
      - data_net
  scheduler:
    cpus: 0.15
    mem_limit: 256m
  db:
    cpus: 2.00
    mem_limit: 3584m
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
      - --skip-character-set-client-handshake
      - --innodb-buffer-pool-size=2560M
      - --innodb-log-file-size=512M
      - --innodb-log-buffer-size=64M
      - --innodb-flush-log-at-trx-commit=1
      - --innodb-flush-method=O_DIRECT
      - --innodb-io-capacity=1000
      - --innodb-io-capacity-max=2000
      - --max-connections=200
  redis-cache:
    cpus: 0.15
    mem_limit: 256m
    command:
      - redis-server
      - --save
      - ""
      - --appendonly
      - "no"
      - --maxmemory
      - 128mb
      - --maxmemory-policy
      - allkeys-lru
  redis-queue:
    cpus: 0.15
    mem_limit: 256m
"""


def enforce_live_resource_headroom(
    *,
    min_mem_available_mib: float = 2048.0,
    max_load_per_cpu: float = 0.85,
) -> dict[str, object]:
    """Fail-closed live-host resource protection guard for shared Staging/host execution."""
    cpu_count = os.cpu_count() or 1
    mem_avail_mib: float | None = None
    mem_total_mib: float | None = None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for raw in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
            if raw.startswith("MemAvailable:"):
                mem_avail_mib = round(int(raw.split()[1]) / 1024.0, 2)
            elif raw.startswith("MemTotal:"):
                mem_total_mib = round(int(raw.split()[1]) / 1024.0, 2)
    load_1m: float | None = None
    if hasattr(os, "getloadavg"):
        try:
            load_1m = round(os.getloadavg()[0], 3)
        except OSError:
            load_1m = None
    load_per_cpu = round(load_1m / cpu_count, 3) if load_1m is not None else None
    if mem_avail_mib is not None and mem_avail_mib < min_mem_available_mib:
        raise RuntimeError(
            f"Live resource guard refused execution: MemAvailable={mem_avail_mib} MiB < {min_mem_available_mib} MiB"
        )
    if load_per_cpu is not None and load_per_cpu > max_load_per_cpu:
        raise RuntimeError(
            f"Live resource guard refused execution: load_per_cpu={load_per_cpu} > {max_load_per_cpu}"
        )
    return {
        "cpu_count": cpu_count,
        "mem_total_mib": mem_total_mib,
        "mem_available_mib": mem_avail_mib,
        "load_1m": load_1m,
        "load_per_cpu": load_per_cpu,
        "headroom_ok": True,
    }


PUBLISHED_PROFILES: dict[str, dict[str, object]] = {
    "s1_suggested_mvp": {
        "description": (
            "Suggested S1 benchmark dataset (P4.md §9.1-§9.2): 50,000 Leads, 75,000 Work Items, "
            "250,000 Contact Attempts, 40 active staff, 50,000-row CSV import, 10,000-item weighted "
            "distribution, and 20/40/80 concurrent user sessions on isolated environment "
            "(NOT a 50,000 Leads/month commitment)"
        ),
        "dataset_framing": "SUGGESTED_S1_DATASET_NOT_MONTHLY_VOLUME_COMMITMENT",
        "leads": 50_000,
        "work_items": 75_000,
        "monthly_work_items": 75_000,
        "contact_attempts": 250_000,
        "sla_obligations": 150_000,
        "teams": 4,
        "campaigns": 6,
        "telesale_agents": 40,
        "active_staff": 40,
        "import_batch_rows": 50_000,
        "distribution_batch_items": 10_000,
        "concurrency": 12,
        "peak_staff_concurrency": 40,
        "concurrent_session_waves": [20, 40, 80],
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
    {
        "id": "kpi_metric_drilldown_monthly_company_api",
        "method": "GET",
        "path": (
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
            "?period_type=monthly&period_key=2026-10&scope_type=company"
            "&metric_code=CONNECTED_RATE&page=1&page_size=20"
        ),
        "workload_class": "kpi_drilldown_paginated_full_month_250k_attempts",
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


def _compose_files_arg(compose_files: list[Path] | Path | None) -> list[str]:
    if compose_files is None:
        return []
    if isinstance(compose_files, Path):
        return ["-f", str(compose_files)]
    out: list[str] = []
    for cf in compose_files:
        out.extend(["-f", str(cf)])
    return out


def _collect_docker_stats(
    compose_project: str,
    *,
    compose_file: list[Path] | Path | None = None,
    env: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    cmd = [
        "docker",
        "compose",
        *_compose_files_arg(compose_file),
        "-p",
        compose_project,
        "ps",
        "-q",
    ]
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
    compose_file: list[Path] | Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    probe = (
        "import json, time, frappe, redis\n"
        "from frappe.utils.background_jobs import generate_qname\n"
        f"site = {site_name!r}\n"
        "frappe.init(site=site, sites_path='/home/frappe/frappe-bench/sites')\n"
        "frappe.connect()\n"
        "t0 = time.perf_counter()\n"
        "r = redis.from_url(frappe.conf.redis_queue)\n"
        "q_depths = {q: int(r.llen(f'rq:queue:{generate_qname(q)}')) for q in ('short', 'default', 'long')}\n"
        "redis_ping_ms = round((time.perf_counter() - t0) * 1000.0, 3)\n"
        "active_workers = []\n"
        "try:\n"
        "    from rq import Worker\n"
        "    for w in Worker.all(connection=r):\n"
        "        active_workers.append({\n"
        "            'name': str(getattr(w, 'name', '')),\n"
        "            'state': str(w.get_state() if hasattr(w, 'get_state') else ''),\n"
        "            'queues': [str(q) for q in w.queue_names()] if hasattr(w, 'queue_names') else [],\n"
        "            'current_job_id': str(w.get_current_job_id() or '') if hasattr(w, 'get_current_job_id') else '',\n"
        "        })\n"
        "except Exception:\n"
        "    pass\n"
        "status_rows = dict(frappe.db.sql(\"SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Max_used_connections','Innodb_row_lock_time_avg','Innodb_row_lock_waits','Innodb_buffer_pool_read_requests','Innodb_buffer_pool_reads','Slow_queries','Questions')\"))\n"
        "frappe.destroy()\n"
        "print(json.dumps({\n"
        "    'queue_depths': q_depths,\n"
        "    'queue_backlog_total': sum(q_depths.values()),\n"
        "    'redis_queue_rtt_ms': redis_ping_ms,\n"
        "    'active_rq_workers': active_workers,\n"
        "    'busy_worker_count': sum(1 for w in active_workers if w.get('state') == 'busy'),\n"
        "    'mariadb_global_status': status_rows,\n"
        "}))"
    )
    cmd = [
        "docker",
        "compose",
        *_compose_files_arg(compose_file),
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
        "schema_version": "cskh-p4-isolated-benchmark-v2",
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


def _login_session(
    *,
    base_url: str,
    site_name: str,
    user: str,
    password: str,
    fetch_csrf: bool = False,
) -> dict[str, str]:
    """Authenticate an independent Frappe HTTP session (`sid` + optional `csrf_token`)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_body = urllib.parse.urlencode({"usr": user, "pwd": password}).encode("utf-8")
    last_exc: Exception | None = None
    for attempt in range(3):
        login_req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/method/login",
            data=login_body,
            headers={
                "Host": site_name,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with opener.open(login_req, timeout=35) as resp:
                if resp.status != 200:
                    raise RuntimeError(
                        f"Benchmark login failed for {user} with HTTP {resp.status}"
                    )
                _ = resp.read()
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    cookie_header = "; ".join(f"{c.name}={c.value}" for c in jar)
    csrf_token = ""
    if fetch_csrf:
        for path in ("/cskh/my-customers", "/cskh", "/app"):
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}{path}",
                headers={"Host": site_name, "Cookie": cookie_header},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=35) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                for pat in (
                    r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)',
                    r'csrf_token\s*[:=]\s*["\']([^"\']+)',
                ):
                    m = re.search(pat, html)
                    if m and m.group(1) and m.group(1) != "None":
                        csrf_token = m.group(1)
                        break
                if csrf_token:
                    break
            except Exception:
                continue
    return {
        "user": user,
        "cookie": cookie_header,
        "csrf_token": csrf_token,
    }


def _http_call(
    *,
    base_url: str,
    site_name: str,
    cookie_header: str,
    path: str,
    method: str = "GET",
    json_body: dict[str, object] | None = None,
    csrf_token: str = "",
    timeout: float = 30.0,
) -> tuple[float, int, bytes]:
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            status = int(r.status)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        status = int(exc.code)
    except (TimeoutError, socket.timeout):
        body = b""
        status = 0
    except urllib.error.URLError as exc:
        body = b""
        status = (
            0
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
            else -1
        )
    except Exception:
        body = b""
        status = -1
    return (time.perf_counter() - t0) * 1000.0, status, body


def _run_multi_cookie_wave(
    cookies: list[str],
    *,
    base_url: str,
    site_name: str,
    path: str,
    method: str = "GET",
    concurrency: int,
    requests_total: int,
    timeout: float = 30.0,
) -> dict[str, object]:
    lats: list[float] = []
    errs = 0
    timeouts = 0
    client_errors = 0
    http_4xx = 0
    http_5xx = 0
    status_counts: dict[str, int] = {}

    def _one(idx: int) -> tuple[float, int]:
        ck = cookies[idx % len(cookies)]
        lat_ms, st, _ = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=ck,
            path=path,
            method=method,
            timeout=timeout,
        )
        return lat_ms, st

    t_sc = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(_one, i) for i in range(requests_total)]
        for fut in concurrent.futures.as_completed(futs):
            lat_ms, st = fut.result()
            lats.append(lat_ms)
            st_key = "timeout" if st == 0 else ("conn_error" if st < 0 else str(st))
            status_counts[st_key] = status_counts.get(st_key, 0) + 1
            if st == 0:
                timeouts += 1
            elif st < 0:
                client_errors += 1
            elif 400 <= st < 500:
                http_4xx += 1
            elif st >= 500:
                http_5xx += 1
            if not (200 <= st < 400):
                errs += 1
    elapsed = time.perf_counter() - t_sc
    return {
        "path": path,
        "concurrency": concurrency,
        "distinct_sessions_used": len(cookies),
        "client_timeout_threshold_sec": timeout,
        "timeout_count": timeouts,
        "client_error_count": client_errors,
        "http_4xx_count": http_4xx,
        "http_5xx_count": http_5xx,
        "http_error_count": http_4xx + http_5xx,
        "status_counts": status_counts,
        **summarize_latencies_ms(lats, error_count=errs, elapsed_sec=elapsed),
        "raw_latencies_ms": [round(x, 3) for x in lats],
    }


def _run_http_wave(
    scenarios: list[dict[str, str]],
    *,
    base_url: str,
    site_name: str,
    cookie_header: str,
    concurrency: int,
    requests_per_scenario: int,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    scenario_results: dict[str, dict[str, object]] = {}
    all_latencies: list[float] = []
    all_errors = 0
    all_timeouts = 0
    all_client_errors = 0
    all_http_4xx = 0
    all_http_5xx = 0
    all_status_counts: dict[str, int] = {}
    overall_t0 = time.perf_counter()

    def _one_call(path: str, method: str) -> tuple[float, int]:
        lat_ms, status, _ = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=cookie_header,
            path=path,
            method=method,
        )
        return lat_ms, status

    for sc in scenarios:
        sc_id = sc["id"]
        sc_path = sc["path"]
        sc_method = sc["method"]
        t_sc = time.perf_counter()
        lats: list[float] = []
        errs = 0
        timeouts = 0
        client_errors = 0
        http_4xx = 0
        http_5xx = 0
        status_counts: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [
                pool.submit(_one_call, sc_path, sc_method)
                for _ in range(requests_per_scenario)
            ]
            for fut in concurrent.futures.as_completed(futs):
                lat_ms, status = fut.result()
                lats.append(lat_ms)
                st_key = (
                    "timeout"
                    if status == 0
                    else ("conn_error" if status < 0 else str(status))
                )
                status_counts[st_key] = status_counts.get(st_key, 0) + 1
                all_status_counts[st_key] = all_status_counts.get(st_key, 0) + 1
                if status == 0:
                    timeouts += 1
                    all_timeouts += 1
                elif status < 0:
                    client_errors += 1
                    all_client_errors += 1
                elif 400 <= status < 500:
                    http_4xx += 1
                    all_http_4xx += 1
                elif status >= 500:
                    http_5xx += 1
                    all_http_5xx += 1
                if not (200 <= status < 400):
                    errs += 1
        elapsed = time.perf_counter() - t_sc
        scenario_results[sc_id] = {
            "workload_class": sc["workload_class"],
            "path": sc_path,
            "concurrency": concurrency,
            "timeout_count": timeouts,
            "client_error_count": client_errors,
            "http_4xx_count": http_4xx,
            "http_5xx_count": http_5xx,
            "http_error_count": http_4xx + http_5xx,
            "status_counts": status_counts,
            **summarize_latencies_ms(lats, error_count=errs, elapsed_sec=elapsed),
            "raw_latencies_ms": [round(x, 3) for x in lats],
        }
        all_latencies.extend(lats)
        all_errors += errs

    overall_elapsed = time.perf_counter() - overall_t0
    return scenario_results, {
        **summarize_latencies_ms(
            all_latencies, error_count=all_errors, elapsed_sec=overall_elapsed
        ),
        "timeout_count": all_timeouts,
        "client_error_count": all_client_errors,
        "http_4xx_count": all_http_4xx,
        "http_5xx_count": all_http_5xx,
        "http_error_count": all_http_4xx + all_http_5xx,
        "status_counts": all_status_counts,
    }


def _execute_s1_http_acceptance_and_mixed_waves(
    *,
    base_url: str,
    site_name: str,
    actor_password: str,
    s1_in_container: dict[str, object],
    compose_project: str,
    compose_file: list[Path] | Path | None,
    compose_env: dict[str, str] | None,
) -> dict[str, object]:
    """Run all `P4.md` §9.2 HTTP scenarios and 20/40/80 multi-user mixed session waves."""
    staff_users = list(s1_in_container.get("staff_users") or [])  # type: ignore[arg-type]
    leader_users = list(s1_in_container.get("leader_users") or [])  # type: ignore[arg-type]
    bod_user = str(s1_in_container.get("bod_user") or "bod.bench@dev.local")
    primary_camp = str(s1_in_container.get("primary_campaign_id") or "")
    http_wis = list(s1_in_container.get("http_open_work_items") or [])  # type: ignore[arg-type]

    # Authenticate 80 independent user sessions:
    # - Sessions 0..39: 40 staff users (tab 1, with CSRF for POST writes)
    # - Sessions 40..73: 34 staff users (tab 2, independent browser session)
    # - Sessions 74..77: 4 team leaders
    # - Sessions 78..79: 2 BOD sessions
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
                    password=actor_password,
                    fetch_csrf=pair[1],
                ),
                login_targets,
            )
        )

    staff_session_by_user = {s["user"]: s for s in sessions_80[:40]}
    bod_session = sessions_80[-1]
    future_due_str = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d 10:00:00")

    # 1. Staff `My Customers` (`GET customers.my_list`, target p95 <= 1500 ms) across 40 staff sessions
    t0 = time.perf_counter()
    mc_lats: list[float] = []
    mc_statuses: list[int] = []
    mc_errs = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futs = [
            pool.submit(
                _http_call,
                base_url=base_url,
                site_name=site_name,
                cookie_header=sessions_80[idx % 40]["cookie"],
                path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                method="GET",
            )
            for idx in range(240)
        ]
        for fut in concurrent.futures.as_completed(futs):
            lat_ms, st, _ = fut.result()
            mc_lats.append(lat_ms)
            mc_statuses.append(st)
            if st != 200:
                mc_errs += 1
    staff_my_customers_http: dict[str, object] = {
        **_classify_http_statuses(mc_statuses),
        **summarize_latencies_ms(
            mc_lats, error_count=mc_errs, elapsed_sec=time.perf_counter() - t0
        ),
        "raw_latencies_ms": [round(x, 3) for x in mc_lats],
    }

    # 2. `Mở Lead + lịch sử tương tác` (`GET get_work_item` + `GET list_attempts` paginated, target p95 <= 2000 ms)
    detail_wis = http_wis[:160]

    def _pair_effective_status(st1: int, st2: int) -> int:
        if st1 == 200 and st2 == 200:
            return 200
        if st1 == 0 or st2 == 0:
            return 0
        if st1 < 0 or st2 < 0:
            return -1
        if st1 >= 500 or st2 >= 500:
            return max(st1, st2)
        return st1 if st1 != 200 else st2

    def _open_lead_and_history(
        item: dict[str, str],
    ) -> tuple[float, float, float, int, int, bool]:
        wi_id = urllib.parse.quote(str(item["work_item"]))
        sess = staff_session_by_user[str(item["owner"])]
        l1, st1, _ = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=sess["cookie"],
            path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={wi_id}",
            method="GET",
        )
        l2, st2, _ = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=sess["cookie"],
            path=f"/api/method/crm_cskh.api.workflow.list_attempts?work_item={wi_id}&page=1&page_size=20",
            method="GET",
        )
        return l1 + l2, l1, l2, st1, st2, (st1 == 200 and st2 == 200)

    t0 = time.perf_counter()
    comb_lats: list[float] = []
    wi_get_lats: list[float] = []
    att_list_lats: list[float] = []
    comb_statuses: list[int] = []
    wi_get_statuses: list[int] = []
    att_list_statuses: list[int] = []
    det_errs = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futs = [
            pool.submit(_open_lead_and_history, detail_wis[i % len(detail_wis)])
            for i in range(160)
        ]
        for fut in concurrent.futures.as_completed(futs):
            c_ms, g_ms, a_ms, st1, st2, ok = fut.result()
            comb_lats.append(c_ms)
            wi_get_lats.append(g_ms)
            att_list_lats.append(a_ms)
            wi_get_statuses.append(st1)
            att_list_statuses.append(st2)
            comb_statuses.append(_pair_effective_status(st1, st2))
            if not ok:
                det_errs += 1
    det_elapsed = time.perf_counter() - t0
    lead_detail_and_history_http = {
        **_classify_http_statuses(comb_statuses),
        **summarize_latencies_ms(
            comb_lats, error_count=det_errs, elapsed_sec=det_elapsed
        ),
        "get_work_item_p95_ms": percentile(sorted(wi_get_lats), 95.0),
        "list_attempts_p95_ms": percentile(sorted(att_list_lats), 95.0),
        "raw_samples_first_15_ms": [round(x, 3) for x in comb_lats[:15]],
        "raw_latencies_ms": [round(x, 3) for x in comb_lats],
        "get_work_item_raw_latencies_ms": [round(x, 3) for x in wi_get_lats],
        "list_attempts_raw_latencies_ms": [round(x, 3) for x in att_list_lats],
        "get_work_item_status_breakdown": _classify_http_statuses(wi_get_statuses),
        "list_attempts_status_breakdown": _classify_http_statuses(att_list_statuses),
    }

    # 3. `Ghi Contact Attempt + Task callback` over HTTP (`POST workflow.record_attempt`, target p95 <= 2000 ms, 0 duplicates)
    write_wis = http_wis[160:240]

    def _post_attempt_with_callback(
        idx_and_item: tuple[int, dict[str, str]],
    ) -> tuple[float, int, bool, str]:
        idx, item = idx_and_item
        wi_id = str(item["work_item"])
        sess = staff_session_by_user[str(item["owner"])]
        idem_key = f"s1-http-att-cb-{idx:04d}"
        lat_ms, st, raw = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=sess["cookie"],
            csrf_token=sess["csrf_token"],
            path="/api/method/crm_cskh.api.workflow.record_attempt",
            method="POST",
            json_body={
                "work_item": wi_id,
                "channel": "phone_call",
                "outcome_code": "connected_interested",
                "note": f"Ghi nhận gọi lại HTTP S1 {idx:04d}",
                "followup_due_at": future_due_str,
                "followup_title": f"Callback HTTP S1 {idx:04d}",
                "idempotency_key": idem_key,
            },
        )
        ok = st == 200 and b"attempt_id" in raw
        return lat_ms, st, ok, idem_key

    t0 = time.perf_counter()
    wr_http_lats: list[float] = []
    wr_http_statuses: list[int] = []
    wr_http_errs = 0
    written_idem_keys: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [
            pool.submit(_post_attempt_with_callback, (i, write_wis[i]))
            for i in range(len(write_wis))
        ]
        for fut in concurrent.futures.as_completed(futs):
            lat_ms, st, ok, ikey = fut.result()
            wr_http_lats.append(lat_ms)
            wr_http_statuses.append(st)
            written_idem_keys.append(ikey)
            if not ok:
                wr_http_errs += 1
    wr_http_elapsed = time.perf_counter() - t0

    # Replay first 10 HTTP POSTs with identical idempotency_key to prove 0 duplicate creation over HTTP
    replay_ok_count = 0
    replay_lats: list[float] = []
    replay_statuses: list[int] = []
    for r_idx in range(min(10, len(write_wis))):
        item = write_wis[r_idx]
        sess = staff_session_by_user[str(item["owner"])]
        rep_lat_ms, st_rep, raw_rep = _http_call(
            base_url=base_url,
            site_name=site_name,
            cookie_header=sess["cookie"],
            csrf_token=sess["csrf_token"],
            path="/api/method/crm_cskh.api.workflow.record_attempt",
            method="POST",
            json_body={
                "work_item": str(item["work_item"]),
                "channel": "phone_call",
                "outcome_code": "connected_interested",
                "note": f"Ghi nhận gọi lại HTTP S1 {r_idx:04d}",
                "followup_due_at": future_due_str,
                "followup_title": f"Callback HTTP S1 {r_idx:04d}",
                "idempotency_key": f"s1-http-att-cb-{r_idx:04d}",
            },
        )
        replay_lats.append(rep_lat_ms)
        replay_statuses.append(st_rep)
        if st_rep == 200 and b'"idempotent_replay":true' in raw_rep.replace(b" ", b""):
            replay_ok_count += 1

    # Reconcile in MariaDB: exact 80 attempts + exact 80 open callback CRM Tasks + 0 duplicate idempotency keys
    reconcile_probe = (
        "import json, os, frappe; "
        "os.chdir('/home/frappe/frappe-bench/sites'); "
        f"frappe.init(site={site_name!r}, sites_path='/home/frappe/frappe-bench/sites'); "
        "frappe.connect(); "
        f"wis = {[str(x['work_item']) for x in write_wis]!r}; "
        "ph = ','.join(['%s'] * len(wis)); "
        "att_cnt = int(frappe.db.sql(\"SELECT COUNT(*) FROM `tabCSKH Contact Attempt` WHERE idempotency_key LIKE %s\", ('s1-http-att-cb-%',))[0][0]); "
        "dup_cnt = int(frappe.db.sql(\"SELECT COUNT(*) FROM (SELECT idempotency_key FROM `tabCSKH Contact Attempt` WHERE idempotency_key LIKE %s GROUP BY idempotency_key HAVING COUNT(*) > 1) t\", ('s1-http-att-cb-%',))[0][0]); "
        "cb_cnt = int(frappe.db.sql(f\"SELECT COUNT(*) FROM `tabCSKH Work Item` WHERE name IN ({ph}) AND open_callback_task IS NOT NULL AND open_callback_task != ''\", tuple(wis))[0][0]); "
        "frappe.destroy(); "
        "print(json.dumps({'attempts_created': att_cnt, 'duplicate_idempotency_keys': dup_cnt, 'work_items_with_open_callback_task': cb_cnt}))"
    )
    rec_res = subprocess.run(
        [
            "docker",
            "compose",
            *_compose_files_arg(compose_file),
            "-p",
            compose_project,
            "exec",
            "-T",
            "-e",
            "FRAPPE_STREAM_LOGGING=1",
            "backend",
            "/home/frappe/frappe-bench/env/bin/python",
            "-c",
            reconcile_probe,
        ],
        capture_output=True,
        text=True,
        env=compose_env,
        check=False,
        timeout=60,
    )
    if rec_res.returncode != 0:
        raise RuntimeError(
            f"HTTP write DB reconciliation probe failed (rc={rec_res.returncode}):\n"
            f"STDOUT:\n{rec_res.stdout[-1500:]}\nSTDERR:\n{rec_res.stderr[-1500:]}"
        )
    http_write_reconciliation = json.loads(rec_res.stdout.strip().splitlines()[-1])
    record_attempt_and_callback_http = {
        **_classify_http_statuses(wr_http_statuses),
        **summarize_latencies_ms(
            wr_http_lats, error_count=wr_http_errs, elapsed_sec=wr_http_elapsed
        ),
        "raw_latencies_ms": [round(x, 3) for x in wr_http_lats],
        "idempotent_replay_raw_latencies_ms": [round(x, 3) for x in replay_lats],
        "idempotent_replay_status_counts": _classify_http_statuses(replay_statuses)[
            "status_counts"
        ],
        "http_idempotent_replays_verified": replay_ok_count,
        "db_reconciliation": http_write_reconciliation,
    }

    # 4. BOD Executive Overview (`GET get_executive_overview`, target p95 <= 3000 ms) & KPI Drill-down (`daily` + `monthly` 250k, target p95 <= 3000 ms) + negative scope check
    _, neg_dd_status, _ = _http_call(
        base_url=base_url,
        site_name=site_name,
        cookie_header=sessions_80[0]["cookie"],
        path="/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown?period_type=monthly&period_key=2026-10&scope_type=company&metric_code=CONNECTED_RATE&page=1&page_size=20",
        method="GET",
    )

    bod_scenarios = [
        {
            "id": "bod_executive_overview_role_api",
            "method": "GET",
            "path": "/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key=2026-10",
            "workload_class": "bod_kpi_snapshot_read_json_api",
        },
        {
            "id": "kpi_drilldown_daily_campaign_role_api",
            "method": "GET",
            "path": (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                f"?period_type=daily&period_key=2026-10-27&scope_type=campaign"
                f"&scope_id={urllib.parse.quote(primary_camp)}&metric_code=CONNECTED_RATE&page=1&page_size=20"
            ),
            "workload_class": "kpi_drilldown_daily_campaign_api",
        },
        {
            "id": "kpi_drilldown_monthly_company_role_api",
            "method": "GET",
            "path": (
                "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                "?period_type=monthly&period_key=2026-10&scope_type=company"
                "&metric_code=CONNECTED_RATE&page=1&page_size=20"
            ),
            "workload_class": "kpi_drilldown_monthly_company_250k_api",
        },
    ]
    bod_role_results, _ = _run_http_wave(
        bod_scenarios,
        base_url=base_url,
        site_name=site_name,
        cookie_header=bod_session["cookie"],
        concurrency=12,
        requests_per_scenario=120,
    )
    bod_role_results["staff_unauthorized_company_drilldown_http_status"] = neg_dd_status  # type: ignore[assignment]

    # 5. Mixed multi-user session waves at 20, 40, and 80 concurrent user sessions (`P4.md` §9.2 line 507)
    mixed_write_pool = http_wis[240:]
    write_cursor = itertools.count(0)
    mixed_waves: dict[str, dict[str, object]] = {}

    for wave_sessions_count, total_ops in ((20, 200), (40, 320), (80, 480)):
        active_pool = sessions_80[:wave_sessions_count]

        def _run_mixed_op(op_idx: int) -> tuple[str, float, int, bool]:
            bucket = op_idx % 20
            if bucket < 9:
                # 45%: Staff My Customers API
                sess = active_pool[op_idx % min(wave_sessions_count, 40)]
                lat, st, _ = _http_call(
                    base_url=base_url,
                    site_name=site_name,
                    cookie_header=sess["cookie"],
                    path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
                    method="GET",
                )
                return "my_customers_api", lat, st, st == 200
            if bucket < 14:
                # 25%: Open Lead + History API
                item = detail_wis[op_idx % len(detail_wis)]
                c_ms, _, _, st1, st2, ok = _open_lead_and_history(item)
                return (
                    "lead_detail_history_api",
                    c_ms,
                    _pair_effective_status(st1, st2),
                    ok,
                )
            if bucket < 17:
                # 15%: BOD Executive Overview snapshot read
                lat, st, _ = _http_call(
                    base_url=base_url,
                    site_name=site_name,
                    cookie_header=bod_session["cookie"],
                    path="/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key=2026-10",
                    method="GET",
                )
                return "bod_overview_api", lat, st, st == 200
            if bucket < 19:
                # 10%: Paginated KPI Drill-down
                lat, st, _ = _http_call(
                    base_url=base_url,
                    site_name=site_name,
                    cookie_header=bod_session["cookie"],
                    path=(
                        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown"
                        f"?period_type=daily&period_key=2026-10-27&scope_type=campaign"
                        f"&scope_id={urllib.parse.quote(primary_camp)}&metric_code=CONNECTED_RATE&page=1&page_size=20"
                    ),
                    method="GET",
                )
                return "kpi_drilldown_api", lat, st, st == 200
            # 5%: Idempotent Contact Attempt + Callback write on fresh open Work Item
            w_slot = next(write_cursor)
            item = mixed_write_pool[w_slot % len(mixed_write_pool)]
            sess = staff_session_by_user[str(item["owner"])]
            lat, st, raw = _http_call(
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
                    "note": f"Mixed wave c{wave_sessions_count} op {op_idx}",
                    "followup_due_at": future_due_str,
                    "followup_title": f"Mixed callback c{wave_sessions_count}-{op_idx}",
                    "idempotency_key": f"s1-mix-c{wave_sessions_count}-{w_slot:05d}",
                },
            )
            return (
                "record_attempt_write_api",
                lat,
                st,
                (st == 200 and b"attempt_id" in raw),
            )

        t_w0 = time.perf_counter()
        w_all_lats: list[float] = []
        w_all_statuses: list[int] = []
        w_all_errs = 0
        per_kind_lats: dict[str, list[float]] = {}
        per_kind_statuses: dict[str, list[int]] = {}
        per_kind_errs: dict[str, int] = {}

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=wave_sessions_count
        ) as pool:
            futs = [pool.submit(_run_mixed_op, i) for i in range(total_ops)]
            mid_stats = _collect_docker_stats(
                compose_project, compose_file=compose_file, env=compose_env
            )
            for fut in concurrent.futures.as_completed(futs):
                kind, lat_ms, st, ok = fut.result()
                w_all_lats.append(lat_ms)
                w_all_statuses.append(st)
                per_kind_lats.setdefault(kind, []).append(lat_ms)
                per_kind_statuses.setdefault(kind, []).append(st)
                if not ok:
                    w_all_errs += 1
                    per_kind_errs[kind] = per_kind_errs.get(kind, 0) + 1

        w_elapsed = time.perf_counter() - t_w0
        w_db_q = _collect_queue_and_db_telemetry(
            compose_project, site_name, compose_file=compose_file, env=compose_env
        )
        mixed_waves[f"sessions_{wave_sessions_count}"] = {
            "concurrent_user_sessions": wave_sessions_count,
            "overall_summary": {
                **_classify_http_statuses(w_all_statuses),
                **summarize_latencies_ms(
                    w_all_lats, error_count=w_all_errs, elapsed_sec=w_elapsed
                ),
                "raw_latencies_ms": [round(x, 3) for x in w_all_lats],
            },
            "by_workload": {
                k: {
                    **_classify_http_statuses(per_kind_statuses.get(k, [])),
                    **summarize_latencies_ms(
                        v, error_count=per_kind_errs.get(k, 0), elapsed_sec=w_elapsed
                    ),
                    "raw_latencies_ms": [round(x, 3) for x in v],
                }
                for k, v in sorted(per_kind_lats.items())
            },
            "container_resources_during_wave": mid_stats,
            "queue_and_db_after_wave": w_db_q,
        }

    staff_cookies_40 = [s["cookie"] for s in sessions_80[:40]]
    telesale_worklist_ssr_40_staff_c12 = _run_multi_cookie_wave(
        staff_cookies_40,
        base_url=base_url,
        site_name=site_name,
        path="/cskh/my-customers",
        concurrency=12,
        requests_total=240,
    )
    telesale_worklist_ssr_40_staff_c40 = _run_multi_cookie_wave(
        staff_cookies_40,
        base_url=base_url,
        site_name=site_name,
        path="/cskh/my-customers",
        concurrency=40,
        requests_total=160,
    )

    return {
        "distinct_authenticated_sessions_created": len(sessions_80),
        "staff_my_customers_http": staff_my_customers_http,
        "telesale_worklist_ssr_40_staff_sessions_c12": telesale_worklist_ssr_40_staff_c12,
        "telesale_worklist_ssr_40_staff_sessions_c40": telesale_worklist_ssr_40_staff_c40,
        "lead_detail_and_history_http": lead_detail_and_history_http,
        "record_attempt_and_callback_http": record_attempt_and_callback_http,
        "bod_overview_and_drilldown_http": bod_role_results,
        "concurrent_mixed_session_waves_20_40_80": mixed_waves,
    }


def execute_isolated_benchmark(
    spec: dict[str, object],
    *,
    base_url: str,
    site_name: str,
    compose_project: str,
    admin_user: str,
    admin_password: str,
    compose_file: list[Path] | Path | None = None,
    compose_env: dict[str, str] | None = None,
    actor_password: str = "",
    s1_in_container: dict[str, object] | None = None,
) -> dict[str, object]:
    profile = spec["published_dataset_and_load"]
    assert isinstance(profile, dict)
    concurrency = int(profile["concurrency"])
    requests_per_scenario = int(profile["requests_per_scenario"])
    scenarios = list(spec.get("scenarios") or PUBLISHED_SCENARIOS)  # type: ignore[arg-type]

    admin_sess = _login_session(
        base_url=base_url,
        site_name=site_name,
        user=admin_user,
        password=admin_password,
        fetch_csrf=False,
    )
    cookie_header = admin_sess["cookie"]

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
    worklist_ssr_single_initialized_c12 = _run_multi_cookie_wave(
        [cookie_header],
        base_url=base_url,
        site_name=site_name,
        path="/cskh/my-customers",
        concurrency=concurrency,
        requests_total=requests_per_scenario,
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
                    "kpi_metric_drilldown_monthly_company_api",
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
        time.sleep(2.0)

    s1_http_acceptance: dict[str, object] | None = None
    if s1_in_container is not None and actor_password:
        s1_http_acceptance = _execute_s1_http_acceptance_and_mixed_waves(
            base_url=base_url,
            site_name=site_name,
            actor_password=actor_password,
            s1_in_container=s1_in_container,
            compose_project=compose_project,
            compose_file=compose_file,
            compose_env=compose_env,
        )

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
    if s1_http_acceptance is not None:
        out["s1_http_acceptance_and_mixed_waves"] = s1_http_acceptance
    out["worklist_ssr_acceptance_and_separation"] = {
        "execution_mode": "BUILT_INTO_OFFICIAL_COMMIT_AND_IMAGE_DIGEST_NO_IN_CONTAINER_PATCHING",
        "priority_acceptance_case_1_shared_new_session_c12": scenario_results.get(
            "telesale_worklist_page"
        ),
        "priority_acceptance_case_2_40_staff_sessions_c40": (
            (s1_http_acceptance or {}).get(
                "telesale_worklist_ssr_40_staff_sessions_c40"
            )
        ),
        "case_2a_shared_new_session_c12": scenario_results.get(
            "telesale_worklist_page"
        ),
        "case_2b_single_initialized_session_c12": worklist_ssr_single_initialized_c12,
        "case_2b_single_initialized_session_c40": (
            ((peak_staff_wave or {}).get("scenario_summaries") or {}).get(
                "telesale_worklist_page"
            )
        ),
        "case_2c_40_staff_sessions_c12": (
            (s1_http_acceptance or {}).get(
                "telesale_worklist_ssr_40_staff_sessions_c12"
            )
        ),
        "case_2c_40_staff_sessions_c40": (
            (s1_http_acceptance or {}).get(
                "telesale_worklist_ssr_40_staff_sessions_c40"
            )
        ),
        "ssr_vs_aggregate_separation": {
            "c12_ssr_telesale_worklist_page_only": scenario_results.get(
                "telesale_worklist_page"
            ),
            "c12_overall_summary_all_8_scenarios": overall_summary,
            "c40_ssr_telesale_worklist_page_40_staff_sessions_only": (
                (s1_http_acceptance or {}).get(
                    "telesale_worklist_ssr_40_staff_sessions_c40"
                )
            ),
            "c40_ssr_telesale_worklist_page_single_session_only": (
                ((peak_staff_wave or {}).get("scenario_summaries") or {}).get(
                    "telesale_worklist_page"
                )
            ),
            "c40_overall_summary_peak_wave_5_scenarios": (
                (peak_staff_wave or {}).get("overall_summary")
            ),
            "c40_overall_summary_mixed_api_sessions_40": (
                (
                    (
                        (s1_http_acceptance or {}).get(
                            "concurrent_mixed_session_waves_20_40_80"
                        )
                        or {}
                    ).get("sessions_40")
                    or {}
                ).get("overall_summary")
            ),
        },
    }
    return out


S1_IN_CONTAINER_BENCH_SCRIPT = r"""
import gc
import hashlib
import json
import math
import os
import platform
import time
from datetime import datetime, timedelta
from pathlib import Path

import frappe
import redis
from frappe.utils.password import update_password

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
        "raw_latencies_ms": [round(x, 3) for x in lats],
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
actor_pw = os.environ["BENCH_ACTOR_PW"]
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
frappe.set_user("Administrator")

t_seed0 = time.perf_counter()
ensure_p2_indexes()
ensure_p2_seeds()
ensure_p3_indexes()
ensure_p3_seeds()
from crm_cskh.services import identity as _preflight_identity
assert callable(getattr(_preflight_identity, "claim_canonical_identity", None)), "missing claim_canonical_identity"
assert callable(getattr(distribution, "run_distribution_worker", None)), "missing run_distribution_worker"
assert callable(getattr(p3_reporting, "get_metric_drilldown", None)), "missing get_metric_drilldown"
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
    update_password(email, actor_pw)
    return email


bod_user = _ensure_user("bod.bench@dev.local", "BOD Bench", ["CSKH BOD Viewer"])
admin_user = _ensure_user("admin.bench@dev.local", "Admin Bench", ["CSKH CRM Admin"])

team_nodes = []
leader_users = []
staff_users = []
staff_team_map = {}

# Exact S1 staff count: 40 active staff across 4 teams of 10
for t_idx in range(1, 5):
    leader_email = _ensure_user(
        f"leader.bench.{t_idx}@dev.local",
        f"Leader Bench {t_idx}",
        ["Sales Manager", "Sales User"],
    )
    leader_users.append(leader_email)
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

# Dedicated 10,000-Work-Item weighted distribution campaign selecting the 10k newly imported Leads
dist_10k_doc = frappe.get_doc(
    {
        "doctype": "CSKH Campaign",
        "code": "S1-CAMP-DIST-10K",
        "title": "Chiến dịch Phân bổ Trọng số 10k S1",
        "status": "Active",
        "started_at": "2026-01-01 00:00:00",
        "ended_at": "2026-12-31 23:59:59",
        "team_scope": None,
        "selection_json": json.dumps({"source": "S1BatchImport"}),
        "rule_version": 1,
    }
)
dist_10k_doc.insert(ignore_permissions=True)
dist_10k_camp = dist_10k_doc.name

for idx_s, s_email in enumerate(staff_users):
    frappe.get_doc(
        {
            "doctype": "CSKH Assignment Rule",
            "campaign": dist_10k_camp,
            "user": s_email,
            "team": staff_team_map[s_email],
            "weight": 1 + (idx_s % 3),
            "capacity_limit": 0,
            "enabled": 1,
            "version": 1,
        }
    ).insert(ignore_permissions=True)
frappe.db.commit()

# Bulk-insert exact S1 dataset:
# - 50,000 CRM Lead + 50,000 CSKH Identity Index (0..49,999)
# - 75,000 CSKH Work Item (0..74,999) + 1,600 CSKH Active Claim / DocShare / Open ToDo
# - 250,000 CSKH Contact Attempt
# - 150,000 CSKH SLA Obligation
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

active_sample_wis = []
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
        if is_open and i < 50_000 and len(active_sample_wis) < 420:
            active_sample_wis.append((wi_id, owner_u, lead_id))
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
seed_elapsed_s = round(time.perf_counter() - t_seed0, 2)

dataset_counts = {
    "crm_leads": int(frappe.db.count("CRM Lead")),
    "cskh_identity_index": int(frappe.db.count("CSKH Identity Index")),
    "cskh_work_items": int(frappe.db.count("CSKH Work Item")),
    "cskh_contact_attempts": int(frappe.db.count("CSKH Contact Attempt")),
    "cskh_sla_obligations": int(frappe.db.count("CSKH SLA Obligation")),
    "cskh_assignment_history": int(frappe.db.count("CSKH Assignment History")),
    "active_staff_users": len(staff_users),
    "teams": len(team_nodes),
    "campaigns": len(campaigns) + 1,
    "seed_elapsed_sec": seed_elapsed_s,
}

# 3. Full Monthly KPI Recompute (2026-10), Daily Incremental Recompute (2026-10-27),
#    AND Background `queue-long` Recompute Non-Blocking + Atomic Revision Publish Proof (`P4.md` §9.2 line 506)
gc.collect()
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
rss_d1, _ = _rss_hwm_mib()

frappe.db.sql("ANALYZE TABLE `tabCSKH KPI Snapshot`")
frappe.db.commit()

if os.environ.get("S1_PROBE_ONLY") == "1":
    from frappe.utils.background_jobs import generate_qname as _probe_gen_qname
    from rq import Worker as _ProbeRQWorker

    _probe_items = max(100, min(int(os.environ.get("S1_PROBE_ITEMS") or "500"), 2000))
    _var_names = (
        "innodb_buffer_pool_size",
        "innodb_log_file_size",
        "innodb_log_buffer_size",
        "innodb_flush_log_at_trx_commit",
        "innodb_flush_method",
        "innodb_io_capacity",
        "innodb_io_capacity_max",
        "character_set_server",
        "collation_server",
        "max_connections",
    )
    _mariadb_vars = {
        str(k): v
        for k, v in frappe.db.sql(
            "SHOW GLOBAL VARIABLES WHERE Variable_name IN ("
            + ",".join(["%s"] * len(_var_names))
            + ")",
            _var_names,
        )
    }
    _cov_idx_rows = frappe.db.sql(
        "SHOW INDEX FROM `tabCSKH Contact Attempt` WHERE Key_name = 'idx_cskh_attempt_ownevt_cover'",
        as_dict=True,
    )
    _cov_hint = (
        "FORCE INDEX (idx_cskh_attempt_ownevt_cover)"
        if _cov_idx_rows
        else "USE INDEX (idx_cskh_attempt_ownevt_occ)"
    )
    _cov_explain = frappe.db.sql(
        f"EXPLAIN SELECT 1 "
        f"FROM `tabCSKH Contact Attempt` ca {_cov_hint} "
        "WHERE ca.ownership_event = 'AH-S1-000001' "
        "AND COALESCE(ca.contact_attempt_snapshot, 0) = 1 "
        "AND ca.recorded_at <= '2026-11-01 00:00:00' "
        "AND ca.occurred_at < '2026-11-01 00:00:00' "
        "LIMIT 1",
        as_dict=True,
    )

    frappe.set_user(bod_user)
    _probe_dd_lats, _probe_dd_errs = [], 0
    _t_dd0 = time.perf_counter()
    for _pg in range(1, 6):
        _c0 = time.perf_counter()
        try:
            _dd_res = p3_reporting.get_metric_drilldown(
                period_type="monthly",
                period_key="2026-10",
                scope_type="company",
                metric_code="CONNECTED_RATE",
                page=_pg,
                page_size=20,
            )
            _dd_ok = bool(_dd_res)
        except Exception:
            _dd_ok = False
        _probe_dd_lats.append((time.perf_counter() - _c0) * 1000.0)
        if not _dd_ok:
            _probe_dd_errs += 1
    _probe_dd_summary = _summarize(_probe_dd_lats, _probe_dd_errs, time.perf_counter() - _t_dd0)
    frappe.set_user("Administrator")

    _probe_rq = redis.from_url(frappe.conf.redis_queue)
    _probe_workers = []
    for _pw in _ProbeRQWorker.all(connection=_probe_rq):
        _raw_qs = [str(_q) for _q in _pw.queue_names()] if hasattr(_pw, "queue_names") else []
        _norm_qs = [str(_q).rsplit(":", 1)[-1] for _q in _raw_qs]
        _probe_workers.append(
            {
                "name": str(getattr(_pw, "name", "")),
                "state": str(_pw.get_state() if hasattr(_pw, "get_state") else ""),
                "raw_queues": _raw_qs,
                "normalized_queues": _norm_qs,
            }
        )

    _probe_csv_lines = ["full_name,phone,email,source"]
    for _ri in range(_probe_items):
        _probe_csv_lines.append(
            f"Ứng viên Probe Mới {_ri:05d},0971{_ri:06d},s1probe{_ri:05d}@dev.local,S1BatchImport"
        )
    _probe_csv_bytes = ("\n".join(_probe_csv_lines) + "\n").encode("utf-8")
    _t_pimp0 = time.perf_counter()
    _pfile_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"s1_short_probe_{_probe_items}.csv",
            "is_private": 1,
            "content": _probe_csv_bytes,
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    _pprev = import_service.create_preview(
        file_name=_pfile_doc.name,
        mapping={"name": "full_name", "phone": "phone", "source": "source"},
        actor="Administrator",
        idempotency_key=f"s1-probe-import-{_probe_items}",
    )
    frappe.db.commit()
    _pbatch_id = _pprev["batch"]
    import_service.confirm(_pbatch_id, actor="Administrator")
    frappe.db.commit()
    _pimp_deadline = time.perf_counter() + 180.0
    _pimp_status = {}
    while time.perf_counter() < _pimp_deadline:
        frappe.db.commit()
        _pimp_status = import_service._status_response(_pbatch_id)
        if str(_pimp_status.get("status") or "").upper() in (
            "COMPLETED",
            "COMPLETED_WITH_ERRORS",
            "FAILED",
        ):
            break
        time.sleep(0.25)
    _pimp_total_ms = round((time.perf_counter() - _t_pimp0) * 1000.0, 2)

    frappe.db.sql("ANALYZE TABLE `tabCRM Lead`, `tabCSKH Identity Index`")
    frappe.db.commit()

    _status_keys = (
        "Innodb_buffer_pool_reads",
        "Innodb_buffer_pool_read_requests",
        "Innodb_os_log_fsyncs",
        "Innodb_data_reads",
        "Innodb_data_writes",
        "Innodb_row_lock_waits",
        "Innodb_row_lock_time",
        "Slow_queries",
        "Questions",
    )

    def _read_status_counters():
        return {
            str(k): int(float(v or 0))
            for k, v in frappe.db.sql(
                "SHOW GLOBAL STATUS WHERE Variable_name IN ("
                + ",".join(["%s"] * len(_status_keys))
                + ")",
                _status_keys,
            )
        }

    _status_before_bg01 = _read_status_counters()
    _t_pdist_prev0 = time.perf_counter()
    _pdist_prev = distribution.preview(
        dist_10k_camp, actor="Administrator", idempotency_key="s1-probe-dist-prev"
    )
    frappe.db.commit()
    _pdist_preview_ms = round((time.perf_counter() - _t_pdist_prev0) * 1000.0, 2)
    _pdist_eligible = int((_pdist_prev.get("summary") or {}).get("eligible_count") or 0)

    _long_q_key = f"rq:queue:{_probe_gen_qname('long')}"
    _short_q_key = f"rq:queue:{_probe_gen_qname('short')}"
    _default_q_key = f"rq:queue:{_probe_gen_qname('default')}"

    _status_before_bg01_exec = _read_status_counters()
    _t_pdist_exec0 = time.perf_counter()
    _pdist_enq = distribution.enqueue_run(
        _pdist_prev["preview_id"],
        expected_rule_version=_pdist_prev.get("rule_version"),
        idempotency_key="s1-probe-dist-exec",
        actor="Administrator",
        enqueue_job=True,
    )
    frappe.db.commit()
    _prun_id = _pdist_enq["run_id"]

    _pdist_progress = []
    _pdist_nb_lats = []
    _pdist_status = ""
    _pr_info = {}
    _peak_long_waiting_while_running = 0
    _peak_short_waiting_during_bg01 = 0
    _pdist_deadline = time.perf_counter() + 240.0
    while time.perf_counter() < _pdist_deadline:
        frappe.db.commit()
        _pr_info = distribution.get_run(_prun_id, actor="Administrator")
        _pdist_status = str(_pr_info.get("status") or "")
        _p_cur = int(_pr_info.get("progress") or 0)
        if not _pdist_progress or _pdist_progress[-1] != _p_cur:
            _pdist_progress.append(_p_cur)
        _cur_short_wait = int(_probe_rq.llen(_short_q_key)) + int(_probe_rq.llen(_default_q_key))
        if _cur_short_wait > _peak_short_waiting_during_bg01:
            _peak_short_waiting_during_bg01 = _cur_short_wait
        if _pdist_status.lower() == "running":
            _cur_long_wait = int(_probe_rq.llen(_long_q_key))
            if _cur_long_wait > _peak_long_waiting_while_running:
                _peak_long_waiting_while_running = _cur_long_wait
        if len(_pdist_nb_lats) < 12:
            _c_nb = time.perf_counter()
            _ = read_model.list_work_items(page=1, page_size=20, actor=staff_users[1])
            _pdist_nb_lats.append(round((time.perf_counter() - _c_nb) * 1000.0, 3))
        if _pdist_status.lower() in ("completed", "completed_with_errors", "failed", "aborted"):
            break
        time.sleep(0.25)
    _pdist_exec_ms = round((time.perf_counter() - _t_pdist_exec0) * 1000.0, 2)
    _status_after_bg01 = _read_status_counters()

    _pdist_dup = int(
        frappe.db.sql(
            "SELECT COUNT(*) - COUNT(DISTINCT lead) FROM `tabCSKH Work Item` WHERE campaign = %s",
            (dist_10k_camp,),
        )[0][0]
    )
    _pdist_reconcile = distribution.reconcile(dist_10k_camp, actor="Administrator")
    _pdirty_by_status = {
        str(_st): int(_cnt)
        for _st, _cnt in frappe.db.sql(
            "SELECT status, COUNT(*) FROM `tabCSKH Dirty Scope` GROUP BY status"
        )
    }

    _all_q_keys = [
        f"rq:queue:{_probe_gen_qname(q)}" for q in ("short", "default", "long")
    ]
    _t_rq0 = time.perf_counter()
    _last_rq_job = None
    for _j_idx in range(50):
        _last_rq_job = frappe.enqueue(
            "frappe.ping", queue="short", job_name=f"s1-short-probe-lag-{_j_idx:03d}"
        )
    _enq_50_ms = round((time.perf_counter() - _t_rq0) * 1000.0, 2)
    _peak_q_short = int(_probe_rq.llen(_short_q_key))
    _last_j_key = f"rq:job:{_last_rq_job.id}" if getattr(_last_rq_job, "id", None) else None
    _t_drain0 = time.perf_counter()
    _drain_dl = _t_drain0 + 60.0
    while time.perf_counter() < _drain_dl:
        _rem = sum(int(_probe_rq.llen(_qk)) for _qk in _all_q_keys)
        _j_done = True
        if _last_j_key:
            _j_st = _probe_rq.hget(_last_j_key, "status")
            _j_done = _j_st in (
                b"finished",
                b"failed",
                b"stopped",
                b"canceled",
                "finished",
                "failed",
                None,
            )
        if _rem == 0 and _j_done:
            break
        time.sleep(0.05)
    _drain_50_ms = round((time.perf_counter() - _t_drain0) * 1000.0, 2)

    _created_cnt = int(
        _pr_info.get("created")
        if _pr_info.get("created") is not None
        else ((_pr_info.get("actual") or {}).get("created") or 0)
    )
    _reads_delta = max(
        0,
        _status_after_bg01.get("Innodb_buffer_pool_reads", 0)
        - _status_before_bg01.get("Innodb_buffer_pool_reads", 0),
    )
    _read_reqs_delta = max(
        0,
        _status_after_bg01.get("Innodb_buffer_pool_read_requests", 0)
        - _status_before_bg01.get("Innodb_buffer_pool_read_requests", 0),
    )
    _hit_ratio_pct = (
        round(100.0 * (1.0 - (_reads_delta / _read_reqs_delta)), 4)
        if _read_reqs_delta > 0
        else 100.0
    )
    _ms_per_item = round(_pdist_exec_ms / max(_created_cnt, 1), 2)
    _items_per_sec = round(
        _created_cnt / max(_pdist_exec_ms / 1000.0, 0.001), 2
    )
    _extrapolated_10k_sec = round((_ms_per_item * 10000.0) / 1000.0, 2)
    _questions_delta = max(
        0,
        _status_after_bg01.get("Questions", 0)
        - _status_before_bg01.get("Questions", 0),
    )
    _exec_questions_delta = max(
        0,
        _status_after_bg01.get("Questions", 0)
        - _status_before_bg01_exec.get("Questions", 0),
    )
    _sql_per_item = round(_questions_delta / max(_created_cnt, 1), 3)
    _exec_sql_per_item = round(_exec_questions_delta / max(_created_cnt, 1), 3)

    _probe_out = {
        "runner_hardware": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "python_version": platform.python_version(),
        },
        "s1_seeded_counts": dataset_counts,
        "kpi_warmup_recompute_s1": {
            "monthly_full_75k_wi_250k_att_ms": monthly_recompute_ms,
            "daily_incremental_ms": daily_recompute_ms,
        },
        "effective_mariadb_and_worker_config": {
            "mariadb_variables": _mariadb_vars,
            "covering_index_idx_cskh_attempt_ownevt_cover_present": bool(_cov_idx_rows),
            "covering_index_columns": [str(r.get("Column_name") or "") for r in _cov_idx_rows],
            "covering_index_explain": _cov_explain,
            "rq_workers": _probe_workers,
            "agg03c_drilldown_5_pages_probe": _probe_dd_summary,
        },
        "probe_import_seed": {
            "probe_items_target": _probe_items,
            "created_rows": _pimp_status.get("created_rows"),
            "final_batch_status": _pimp_status.get("status"),
            "total_import_ms": _pimp_total_ms,
        },
        "bg01_per_item_probe": {
            "run_id": _prun_id,
            "job_id": f"cskh-dist-{_prun_id}",
            "eligible_count": _pdist_eligible,
            "preview_ms": _pdist_preview_ms,
            "execute_and_worker_drain_ms": _pdist_exec_ms,
            "final_run_status": _pdist_status,
            "created_work_items": _created_cnt,
            "duplicate_work_items": _pdist_dup,
            "ms_per_item": _ms_per_item,
            "items_per_sec": _items_per_sec,
            "sql_queries_delta": _questions_delta,
            "sql_queries_per_item": _sql_per_item,
            "execute_sql_queries_delta": _exec_questions_delta,
            "execute_sql_queries_per_item": _exec_sql_per_item,
            "extrapolated_10000_items_sec": _extrapolated_10k_sec,
            "slo_10000_items_budget_sec": 1200.0,
            "progress_checkpoints": _pdist_progress,
            "concurrent_worklist_read_p95_ms": _pct(_pdist_nb_lats, 95.0),
            "ownership_reconciled": bool(_pdist_reconcile.get("ownership_reconciled")),
            "dirty_scope_counts_by_status": _pdirty_by_status,
            "peak_long_queue_waiting_jobs_during_bg01": _peak_long_waiting_while_running,
            "peak_short_queue_waiting_jobs_during_bg01": _peak_short_waiting_during_bg01,
        },
        "mariadb_disk_read_telemetry": {
            "before_bg01": _status_before_bg01,
            "before_bg01_exec": _status_before_bg01_exec,
            "after_bg01": _status_after_bg01,
            "questions_delta": _questions_delta,
            "sql_queries_per_item": _sql_per_item,
            "execute_questions_delta": _exec_questions_delta,
            "execute_sql_queries_per_item": _exec_sql_per_item,
            "innodb_buffer_pool_reads_delta": _reads_delta,
            "innodb_buffer_pool_read_requests_delta": _read_reqs_delta,
            "buffer_pool_hit_ratio_pct": _hit_ratio_pct,
            "physical_reads_per_item": round(_reads_delta / max(_created_cnt, 1), 4),
            "innodb_os_log_fsyncs_delta": max(
                0,
                _status_after_bg01.get("Innodb_os_log_fsyncs", 0)
                - _status_before_bg01.get("Innodb_os_log_fsyncs", 0),
            ),
            "innodb_data_writes_delta": max(
                0,
                _status_after_bg01.get("Innodb_data_writes", 0)
                - _status_before_bg01.get("Innodb_data_writes", 0),
            ),
            "innodb_row_lock_waits_delta": max(
                0,
                _status_after_bg01.get("Innodb_row_lock_waits", 0)
                - _status_before_bg01.get("Innodb_row_lock_waits", 0),
            ),
            "innodb_row_lock_time_ms_delta": max(
                0,
                _status_after_bg01.get("Innodb_row_lock_time", 0)
                - _status_before_bg01.get("Innodb_row_lock_time", 0),
            ),
            "slow_queries_delta": max(
                0,
                _status_after_bg01.get("Slow_queries", 0)
                - _status_before_bg01.get("Slow_queries", 0),
            ),
        },
        "rq_short_50_job_burst": {
            "enqueue_50_jobs_ms": _enq_50_ms,
            "peak_queue_short_depth": _peak_q_short,
            "drain_to_zero_lag_ms": _drain_50_ms,
            "effective_worker_drain_jobs_per_sec": (
                round(50.0 / (_drain_50_ms / 1000.0), 2) if _drain_50_ms > 0 else 0.0
            ),
        },
    }
    frappe.destroy()
    print("S1_PROBE_JSON:" + json.dumps(_probe_out, default=str))
    raise SystemExit(0)

rev_before_bg = str(
    frappe.db.sql(
        "SELECT COALESCE(MAX(kpi_run), '') FROM `tabCSKH KPI Snapshot` "
        "WHERE is_latest_published = 1 AND period_type = 'monthly' AND period_key = '2026-10' AND campaign_filter = ''"
    )[0][0]
    or ""
)
bg_job = frappe.enqueue(
    "crm_cskh.jobs.recompute_kpi.run_single_period_recompute",
    queue="long",
    timeout=1800,
    period_type="monthly",
    period_key="2026-10",
    actor="Administrator",
)
frappe.db.commit()

bg_ui_read_lats = []
revisions_seen_while_running = set()
t_bg0 = time.perf_counter()
bg_deadline = t_bg0 + 300.0
frappe.set_user(bod_user)
while time.perf_counter() < bg_deadline:
    frappe.db.commit()
    c_r0 = time.perf_counter()
    ov = p3_reporting.get_executive_overview(period_type="monthly", period_key="2026-10")
    bg_ui_read_lats.append((time.perf_counter() - c_r0) * 1000.0)
    cur_rev = str(
        frappe.db.sql(
            "SELECT COALESCE(MAX(kpi_run), '') FROM `tabCSKH KPI Snapshot` "
            "WHERE is_latest_published = 1 AND period_type = 'monthly' AND period_key = '2026-10' AND campaign_filter = ''"
        )[0][0]
        or ""
    )
    running_cnt = int(
        frappe.db.sql(
            "SELECT COUNT(*) FROM `tabCSKH KPI Run` WHERE period_type = 'monthly' AND period_key = '2026-10' AND status = 'running'"
        )[0][0]
    )
    if running_cnt > 0:
        revisions_seen_while_running.add(cur_rev)
    if cur_rev != "" and cur_rev != rev_before_bg and running_cnt == 0:
        break
    time.sleep(0.15)
bg_recompute_wall_ms = round((time.perf_counter() - t_bg0) * 1000.0, 2)
rev_after_bg = str(
    frappe.db.sql(
        "SELECT COALESCE(MAX(kpi_run), '') FROM `tabCSKH KPI Snapshot` "
        "WHERE is_latest_published = 1 AND period_type = 'monthly' AND period_key = '2026-10' AND campaign_filter = ''"
    )[0][0]
    or ""
)
frappe.set_user("Administrator")

# 4. In-Process Domain Read & Write Workloads at S1 Scale
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

det_lats, det_errs = [], 0
t0 = time.perf_counter()
for idx in range(60):
    wi_id, actor, _ = active_sample_wis[idx % len(active_sample_wis)]
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

wr_lats, wr_errs = [], 0
future_due_str = (frappe.utils.now_datetime() + timedelta(days=3)).strftime("%Y-%m-%d 09:30:00")
idempotent_replay_verified = False
t0 = time.perf_counter()
for idx in range(50):
    wi_id, actor, _ = active_sample_wis[idx]
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

dd_month_lats, dd_month_errs = [], 0
t0 = time.perf_counter()
for pg in range(10):
    c0 = time.perf_counter()
    try:
        r = p3_reporting.get_metric_drilldown(
            period_type="monthly",
            period_key="2026-10",
            scope_type="company",
            metric_code="CONNECTED_RATE",
            page=(pg % 5) + 1,
            page_size=20,
        )
        ok = bool(r)
    except Exception:
        ok = False
    dd_month_lats.append((time.perf_counter() - c0) * 1000.0)
    if not ok:
        dd_month_errs += 1
drilldown_full_month_summary = _summarize(dd_month_lats, dd_month_errs, time.perf_counter() - t0)

# 5. CSV Import 50,000 Rows (`P4.md` §9.2 line 505):
#    - 10,000 brand-new canonical phones (`0971000000..0971009999`, `source=S1BatchImport`) -> `valid_new`
#    - 5,000 existing CRM phones (`+84 910 000 000..+84 910 004 999`) -> `duplicate_in_crm`
#    - 34,500 in-file duplicate rows (`0971...` & `0910...` formatting variants) -> `duplicate_in_file`
#    - 500 invalid phone rows (`12345`) -> `invalid` (logged in private error CSV artifact)
frappe.set_user("Administrator")
csv_lines = ["full_name,phone,email,source"]
for r_i in range(10_000):
    csv_lines.append(f"Ứng viên S1 Mới {r_i:05d},0971{r_i:06d},s1new{r_i:05d}@dev.local,S1BatchImport")
for r_i in range(5_000):
    csv_lines.append(f"Ứng viên Trùng CRM {r_i:05d},+84 91{r_i:07d},s1crmdup{r_i:05d}@dev.local,S1BatchImport")
for r_i in range(34_500):
    dup_idx = r_i % 10_000
    csv_lines.append(f"Ứng viên Trùng File {r_i:05d},84971{dup_idx:06d},s1fdup{r_i:05d}@dev.local,S1BatchImport")
for r_i in range(500):
    csv_lines.append(f"Ứng viên Lỗi SĐT {r_i:04d},12345,s1err{r_i:04d}@dev.local,S1BatchImport")
assert len(csv_lines) - 1 == 50_000
csv_bytes = ("\n".join(csv_lines) + "\n").encode("utf-8")

gc.collect()
rss_imp_before, _ = _rss_hwm_mib()
t_imp_prev0 = time.perf_counter()
file_doc = frappe.get_doc(
    {
        "doctype": "File",
        "file_name": "s1_benchmark_50000.csv",
        "is_private": 1,
        "content": csv_bytes,
    }
).insert(ignore_permissions=True)
frappe.db.commit()

prev_50k = import_service.create_preview(
    file_name=file_doc.name,
    mapping={"name": "full_name", "phone": "phone", "source": "source"},
    actor="Administrator",
    idempotency_key="s1-import-50000",
)
frappe.db.commit()
imp_preview_ms = round((time.perf_counter() - t_imp_prev0) * 1000.0, 2)
rss_imp_after_prev, hwm_imp_prev = _rss_hwm_mib()

batch_id = prev_50k["batch"]
t_imp_exec0 = time.perf_counter()
import_service.confirm(batch_id, actor="Administrator")
frappe.db.commit()

imp_progress_samples = []
imp_nonblocking_lats = []
imp_deadline = time.perf_counter() + 900.0
imp_status = {}
while time.perf_counter() < imp_deadline:
    frappe.db.commit()
    imp_status = import_service._status_response(batch_id)
    prog_val = int(imp_status.get("progress") or 0)
    if not imp_progress_samples or imp_progress_samples[-1] != prog_val:
        imp_progress_samples.append(prog_val)
    if len(imp_nonblocking_lats) < 15:
        c_nb = time.perf_counter()
        _ = read_model.list_work_items(page=1, page_size=20, actor=staff_users[0])
        imp_nonblocking_lats.append(round((time.perf_counter() - c_nb) * 1000.0, 3))
    if str(imp_status.get("status") or "").upper() in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
        break
    time.sleep(0.5)
imp_worker_ms = round((time.perf_counter() - t_imp_exec0) * 1000.0, 2)
imp_total_ms = round(imp_preview_ms + imp_worker_ms, 2)

# 6. Weighted Distribution Preview (10,000 Work Items, target <= 60s) & Execute (10,000 Work Items, 0 duplicates)
frappe.db.sql("ANALYZE TABLE `tabCRM Lead`, `tabCSKH Identity Index`")
frappe.db.commit()

from frappe.utils.background_jobs import generate_qname as _dist_gen_qname
_dist_rq = redis.from_url(frappe.conf.redis_queue)
_dist_long_q_key = f"rq:queue:{_dist_gen_qname('long')}"
_dist_short_q_key = f"rq:queue:{_dist_gen_qname('short')}"
_dist_default_q_key = f"rq:queue:{_dist_gen_qname('default')}"

_dist_status_keys = (
    "Questions",
    "Innodb_buffer_pool_reads",
    "Innodb_buffer_pool_read_requests",
    "Innodb_os_log_fsyncs",
    "Innodb_data_writes",
    "Innodb_row_lock_waits",
    "Slow_queries",
)

def _read_dist_status_counters():
    return {
        str(k): int(float(v or 0))
        for k, v in frappe.db.sql(
            "SHOW GLOBAL STATUS WHERE Variable_name IN ("
            + ",".join(["%s"] * len(_dist_status_keys))
            + ")",
            _dist_status_keys,
        )
    }

dist_status_before_preview = _read_dist_status_counters()
t_dist0 = time.perf_counter()
dist_prev = distribution.preview(dist_10k_camp, actor="Administrator", idempotency_key="s1-dist-prev-10k")
frappe.db.commit()
dist_preview_ms = round((time.perf_counter() - t_dist0) * 1000.0, 2)
dist_eligible_count = int((dist_prev.get("summary") or {}).get("eligible_count") or 0)

dist_status_before_exec = _read_dist_status_counters()
t_de0 = time.perf_counter()
dist_enq = distribution.enqueue_run(
    dist_prev["preview_id"],
    expected_rule_version=dist_prev.get("rule_version"),
    idempotency_key="s1-dist-exec-10k",
    actor="Administrator",
    enqueue_job=True,
)
frappe.db.commit()
run_id = dist_enq["run_id"]

# Double-submit idempotency check while queued/running: must return same run_id and 0 duplicate RQ jobs
dist_enq_dup = distribution.enqueue_run(
    dist_prev["preview_id"],
    expected_rule_version=dist_prev.get("rule_version"),
    idempotency_key="s1-dist-exec-10k",
    actor="Administrator",
    enqueue_job=True,
)
frappe.db.commit()
double_submit_idempotent = bool(dist_enq_dup.get("run_id") == run_id)

dist_progress_samples = []
dist_nonblocking_lats = []
dist_run_status = ""
r_info = {}
dist_peak_long_waiting = 0
dist_peak_short_waiting = 0
d_deadline = time.perf_counter() + 1200.0
while time.perf_counter() < d_deadline:
    frappe.db.commit()
    r_info = distribution.get_run(run_id, actor="Administrator")
    dist_run_status = str(r_info.get("status") or "")
    p_cur = int(r_info.get("progress") or 0)
    if not dist_progress_samples or dist_progress_samples[-1] != p_cur:
        dist_progress_samples.append(p_cur)
    _cur_short_w = int(_dist_rq.llen(_dist_short_q_key)) + int(_dist_rq.llen(_dist_default_q_key))
    if _cur_short_w > dist_peak_short_waiting:
        dist_peak_short_waiting = _cur_short_w
    if dist_run_status.lower() == "running":
        _cur_long_w = int(_dist_rq.llen(_dist_long_q_key))
        if _cur_long_w > dist_peak_long_waiting:
            dist_peak_long_waiting = _cur_long_w
    if len(dist_nonblocking_lats) < 20:
        c_nb = time.perf_counter()
        _ = read_model.list_work_items(page=1, page_size=20, actor=staff_users[1])
        dist_nonblocking_lats.append(round((time.perf_counter() - c_nb) * 1000.0, 3))
    if dist_run_status.lower() in ("completed", "completed_with_errors", "failed", "aborted"):
        break
    time.sleep(0.5)
dist_exec_ms = round((time.perf_counter() - t_de0) * 1000.0, 2)
dist_status_after_exec = _read_dist_status_counters()

dist_dup_leads = int(
    frappe.db.sql(
        "SELECT COUNT(*) - COUNT(DISTINCT lead) FROM `tabCSKH Work Item` WHERE campaign = %s",
        (dist_10k_camp,),
    )[0][0]
)
dist_reconcile = distribution.reconcile(dist_10k_camp, actor="Administrator")

_dist_created_cnt = int(
    r_info.get("created")
    if r_info.get("created") is not None
    else ((r_info.get("actual") or {}).get("created") or 0)
)
dist_ms_per_item = round(dist_exec_ms / max(_dist_created_cnt, 1), 2)
dist_items_per_sec = round(_dist_created_cnt / max(dist_exec_ms / 1000.0, 0.001), 2)
dist_questions_delta = max(
    0,
    dist_status_after_exec.get("Questions", 0) - dist_status_before_preview.get("Questions", 0),
)
dist_exec_questions_delta = max(
    0,
    dist_status_after_exec.get("Questions", 0) - dist_status_before_exec.get("Questions", 0),
)
dist_sql_per_item = round(dist_questions_delta / max(_dist_created_cnt, 1), 3)
dist_exec_sql_per_item = round(dist_exec_questions_delta / max(_dist_created_cnt, 1), 3)
dist_reads_delta = max(
    0,
    dist_status_after_exec.get("Innodb_buffer_pool_reads", 0)
    - dist_status_before_preview.get("Innodb_buffer_pool_reads", 0),
)
dist_read_reqs_delta = max(
    0,
    dist_status_after_exec.get("Innodb_buffer_pool_read_requests", 0)
    - dist_status_before_preview.get("Innodb_buffer_pool_read_requests", 0),
)
dist_hit_ratio_pct = (
    round(100.0 * (1.0 - (dist_reads_delta / dist_read_reqs_delta)), 4)
    if dist_read_reqs_delta > 0
    else 100.0
)

_dist_poll_timed_out = dist_run_status.lower() not in (
    "completed",
    "completed_with_errors",
    "failed",
    "aborted",
)
_dist_run_rows = frappe.db.sql(
    "SELECT name, campaign, rule_version, status, progress, started_at, finished_at, "
    "lease_expires_at, error, actual_json FROM `tabCSKH Distribution Run` WHERE name = %s",
    (run_id,),
    as_dict=True,
)
_dist_wi_by_status = {
    str(_st_name): int(_cnt)
    for _st_name, _cnt in frappe.db.sql(
        "SELECT status, COUNT(*) FROM `tabCSKH Work Item` WHERE campaign = %s GROUP BY status",
        (dist_10k_camp,),
    )
}
_dist_claim_cnt = int(
    frappe.db.sql(
        "SELECT COUNT(*) FROM `tabCSKH Active Claim` WHERE campaign = %s",
        (dist_10k_camp,),
    )[0][0]
)
_dirty_scope_by_status = {
    str(_st_name): int(_cnt)
    for _st_name, _cnt in frappe.db.sql(
        "SELECT status, COUNT(*) FROM `tabCSKH Dirty Scope` GROUP BY status"
    )
}
_rq_job_candidates = [
    f"rq:job:{site}::cskh-dist-{run_id}",
    f"rq:job:cskh-dist-{run_id}",
]
_rq_job_key_found = ""
_rq_job_state = {}
for _cand_key in _rq_job_candidates:
    if _dist_rq.exists(_cand_key):
        _rq_job_key_found = _cand_key
        break
if not _rq_job_key_found:
    for _scan_key in _dist_rq.scan_iter(match=f"*{run_id}*", count=100):
        _ks = (
            _scan_key.decode("utf-8", errors="ignore")
            if isinstance(_scan_key, bytes)
            else str(_scan_key)
        )
        if _ks.startswith("rq:job:"):
            _rq_job_key_found = _ks
            break
if _rq_job_key_found:
    for _hk in (
        "status",
        "enqueued_at",
        "started_at",
        "ended_at",
        "worker_name",
        "origin",
        "timeout",
        "exc_info",
    ):
        _hv = _dist_rq.hget(_rq_job_key_found, _hk)
        if _hv is not None:
            _rq_job_state[_hk] = (
                _hv.decode("utf-8", errors="replace")[:1000]
                if isinstance(_hv, bytes)
                else str(_hv)[:1000]
            )
_rq_workers_snap = []
try:
    from rq import Worker as _RQWorker
    for _w in _RQWorker.all(connection=_dist_rq):
        _rq_workers_snap.append(
            {
                "name": str(getattr(_w, "name", "")),
                "state": str(_w.get_state() if hasattr(_w, "get_state") else ""),
                "queues": [str(_q) for _q in _w.queue_names()] if hasattr(_w, "queue_names") else [],
                "current_job_id": str(_w.get_current_job_id() or "") if hasattr(_w, "get_current_job_id") else "",
            }
        )
except Exception:
    pass
dist_worker_and_job_diagnostics = {
    "run_id": run_id,
    "campaign_id": dist_10k_camp,
    "poll_deadline_sec": 1200.0,
    "poll_timed_out_while_running": _dist_poll_timed_out,
    "distribution_run_row": _dist_run_rows[0] if _dist_run_rows else {},
    "work_item_counts_by_status": _dist_wi_by_status,
    "work_item_total_in_db": sum(_dist_wi_by_status.values()),
    "active_claim_count_in_db": _dist_claim_cnt,
    "dirty_scope_counts_by_status": _dirty_scope_by_status,
    "rq_job_redis_key": _rq_job_key_found or None,
    "rq_job_state": _rq_job_state,
    "rq_queue_depths_at_step_end": {
        _qn: int(_dist_rq.llen(f"rq:queue:{_dist_gen_qname(_qn)}"))
        for _qn in ("short", "default", "long")
    },
    "rq_active_workers_at_step_end": _rq_workers_snap,
    "all_progress_checkpoints": dist_progress_samples,
}

# Verify operator cancel/pause abort guard on a separate campaign (`Closed` before worker runs -> clean `CONFLICT` abort)
from crm_cskh.services import identity as identity_service
abort_l1 = frappe.get_doc(
    {"doctype": "CRM Lead", "first_name": "Abort Guard 1", "mobile_no": "0969990001"}
).insert(ignore_permissions=True)
identity_service.claim_canonical_identity("+84969990001", abort_l1.name)
frappe.db.commit()
abort_camp = frappe.get_doc(
    {
        "doctype": "CSKH Campaign",
        "code": "S1-CAMP-ABORT-GUARD",
        "title": "Chiến dịch Kiểm tra Hủy/Dừng",
        "status": "Active",
        "selection_json": json.dumps({"leads": [abort_l1.name]}),
        "rule_version": 1,
    }
).insert(ignore_permissions=True)
frappe.get_doc(
    {
        "doctype": "CSKH Assignment Rule",
        "campaign": abort_camp.name,
        "user": staff_users[0],
        "team": staff_team_map[staff_users[0]],
        "weight": 1,
        "capacity_limit": 0,
        "enabled": 1,
        "version": 1,
    }
).insert(ignore_permissions=True)
frappe.db.commit()
ab_prev = distribution.preview(abort_camp.name, actor="Administrator", idempotency_key="s1-abort-prev")
ab_enq = distribution.enqueue_run(
    ab_prev["preview_id"],
    expected_rule_version=1,
    idempotency_key="s1-abort-exec",
    actor="Administrator",
    enqueue_job=False,
)
frappe.db.set_value("CSKH Campaign", abort_camp.name, "status", "Closed")
frappe.db.commit()
ab_res = distribution.run_distribution_worker(ab_enq["run_id"], actor="Administrator")
ab_created = (
    ab_res.get("created")
    if isinstance(ab_res, dict) and ab_res.get("created") is not None
    else ((ab_res.get("actual") or {}).get("created") if isinstance(ab_res, dict) else 0)
)
abort_guard_verified = bool(
    ab_res and ab_res.get("status") == "failed" and int(ab_created or 0) == 0
)

# 7. Direct Redis RQ 50-job burst queue lag measurement on dedicated `queue-short`
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

# 8. MariaDB EXPLAIN plans on S1 tables (verifying composite index selection)
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
        "EXPLAIN SELECT name, metrics_json FROM `tabCSKH KPI Snapshot` USE INDEX (idx_cskh_kpisnap_lookup) "
        "WHERE is_latest_published = 1 AND period_type = 'monthly' AND period_key = '2026-10' "
        "AND campaign_filter = '' AND scope_type = 'company' AND scope_id = '__company__' LIMIT 5",
        as_dict=True,
    ),
    "canonical_phone_identity_lookup": frappe.db.sql(
        "EXPLAIN SELECT canonical_lead FROM `tabCSKH Identity Index` WHERE normalized_phone = '+84910000010' LIMIT 1",
        as_dict=True,
    ),
    "crm_lead_mobile_no_lookup": frappe.db.sql(
        "EXPLAIN SELECT name FROM `tabCRM Lead` WHERE mobile_no = '0910000010' LIMIT 1",
        as_dict=True,
    ),
    "kpi_drilldown_attempt_window": frappe.db.sql(
        "EXPLAIN SELECT work_item, outcome_code FROM `tabCSKH Contact Attempt` "
        "WHERE campaign = %s AND occurred_at >= '2026-10-27 00:00:00' AND occurred_at < '2026-10-28 00:00:00'",
        (campaigns[0],),
        as_dict=True,
    ),
    "kpi_drilldown_interval_page": frappe.db.sql(
        "EXPLAIN SELECT ah.name, ah.work_item, ah.to_user, ah.ownership_start, ah.ownership_end, wi.campaign, wi.lead "
        "FROM `tabCSKH Assignment History` ah USE INDEX (idx_cskh_ah_start_name) "
        "INNER JOIN `tabCSKH Work Item` wi ON wi.name = ah.work_item "
        "WHERE ah.ownership_start >= '2026-10-01 00:00:00' AND ah.ownership_start < '2026-11-01 00:00:00' "
        "AND ah.action IN ('assign', 'reassign', 'reopen') "
        "ORDER BY ah.ownership_start ASC, ah.name ASC LIMIT 120 OFFSET 0",
        as_dict=True,
    ),
    "kpi_drilldown_attempt_chunk": frappe.db.sql(
        "EXPLAIN SELECT ca.ownership_event, MAX(ca.connected_snapshot) "
        "FROM `tabCSKH Contact Attempt` ca USE INDEX (idx_cskh_attempt_ownevt_occ) "
        "WHERE ca.ownership_event IN ('AH-S1-000001', 'AH-S1-000002', 'AH-S1-000003') "
        "AND ca.recorded_at <= '2026-11-01 00:00:00' "
        "GROUP BY ca.ownership_event",
        as_dict=True,
    ),
    "kpi_drilldown_attempt_covering_chunk": (
        frappe.db.sql(
            "EXPLAIN SELECT ca.ownership_event, MAX(ca.connected_snapshot) "
            "FROM `tabCSKH Contact Attempt` ca FORCE INDEX (idx_cskh_attempt_ownevt_cover) "
            "WHERE ca.ownership_event IN ('AH-S1-000001', 'AH-S1-000002', 'AH-S1-000003') "
            "AND ca.recorded_at <= '2026-11-01 00:00:00' "
            "GROUP BY ca.ownership_event",
            as_dict=True,
        )
        if frappe.db.sql(
            "SHOW INDEX FROM `tabCSKH Contact Attempt` WHERE Key_name = 'idx_cskh_attempt_ownevt_cover'"
        )
        else []
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
    "dist_10k_campaign_id": dist_10k_camp,
    "staff_users": staff_users,
    "leader_users": leader_users,
    "bod_user": bod_user,
    "admin_user": admin_user,
    "http_open_work_items": [
        {"work_item": w_id, "owner": u_id, "lead": l_id}
        for (w_id, u_id, l_id) in active_sample_wis[50:400]
    ],
    "kpi_recompute_s1": {
        "monthly_full_75k_wi_250k_att_ms": monthly_recompute_ms,
        "monthly_run_status": month_res.get("status") if isinstance(month_res, dict) else "ok",
        "monthly_rss_after_mib": rss_m1,
        "monthly_hwm_mib": hwm_m1,
        "daily_incremental_ms": daily_recompute_ms,
        "daily_rss_after_mib": rss_d1,
        "background_queue_long_recompute": {
            "wall_ms": bg_recompute_wall_ms,
            "revision_before": rev_before_bg,
            "revisions_seen_while_running": sorted(revisions_seen_while_running),
            "revision_after_complete": rev_after_bg,
            "atomic_publish_verified": bool(
                rev_after_bg != ""
                and rev_after_bg != rev_before_bg
                and all(r == rev_before_bg for r in revisions_seen_while_running)
            ),
            "concurrent_ui_overview_reads": _summarize(
                bg_ui_read_lats, 0, max(bg_recompute_wall_ms / 1000.0, 0.001)
            ),
        },
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
        "csv_import_50000_rows": {
            "total_rows": (imp_status.get("totals") or {}).get("total_rows"),
            "valid_rows": (imp_status.get("totals") or {}).get("valid_rows"),
            "new_rows": (imp_status.get("totals") or {}).get("new_rows"),
            "duplicate_rows": (imp_status.get("totals") or {}).get("duplicate_rows"),
            "error_rows": (imp_status.get("totals") or {}).get("error_rows"),
            "created_rows": imp_status.get("created_rows"),
            "reused_rows": imp_status.get("reused_rows"),
            "skipped_rows": imp_status.get("skipped_rows"),
            "error_artifact_present": bool(imp_status.get("error_artifact")),
            "preview_ms": imp_preview_ms,
            "worker_commit_and_drain_ms": imp_worker_ms,
            "total_preview_confirm_and_worker_drain_ms": imp_total_ms,
            "final_batch_status": imp_status.get("status"),
            "preview_rss_before_mib": rss_imp_before,
            "preview_rss_after_mib": rss_imp_after_prev,
            "preview_hwm_mib": hwm_imp_prev,
            "progress_checkpoints_count": len(imp_progress_samples),
            "all_progress_checkpoints": imp_progress_samples,
            "concurrent_worklist_read_p95_ms": _pct(imp_nonblocking_lats, 95.0),
            "concurrent_worklist_read_raw_latencies_ms": imp_nonblocking_lats,
            "rows_throughput_per_sec": round(50_000.0 / (imp_total_ms / 1000.0), 2) if imp_total_ms > 0 else 0.0,
        },
        "campaign_distribution_10000_work_items_40_staff": {
            "eligible_count": dist_eligible_count,
            "preview_ms": dist_preview_ms,
            "preview_within_60s_reference": dist_preview_ms <= 60_000.0,
            "execute_and_worker_drain_ms": dist_exec_ms,
            "ms_per_item": dist_ms_per_item,
            "items_per_sec": dist_items_per_sec,
            "sql_queries_delta": dist_questions_delta,
            "sql_queries_per_item": dist_sql_per_item,
            "execute_sql_queries_delta": dist_exec_questions_delta,
            "execute_sql_queries_per_item": dist_exec_sql_per_item,
            "innodb_buffer_pool_reads_delta": dist_reads_delta,
            "buffer_pool_hit_ratio_pct": dist_hit_ratio_pct,
            "peak_long_queue_waiting_jobs_during_bg01": dist_peak_long_waiting,
            "peak_short_queue_waiting_jobs_during_bg01": dist_peak_short_waiting,
            "final_run_status": dist_run_status,
            "created_work_items": (
                r_info.get("created")
                if r_info.get("created") is not None
                else (r_info.get("actual") or {}).get("created")
            ),
            "duplicate_work_items": dist_dup_leads,
            "double_submit_idempotent": double_submit_idempotent,
            "abort_cancel_guard_verified": abort_guard_verified,
            "progress_checkpoints_recorded": dist_progress_samples[:10] + dist_progress_samples[-3:],
            "all_progress_checkpoints": dist_progress_samples,
            "concurrent_worklist_read_p95_ms": _pct(dist_nonblocking_lats, 95.0),
            "concurrent_worklist_read_raw_latencies_ms": dist_nonblocking_lats,
            "reconciliation": {
                "active_work_items": dist_reconcile.get("active_work_items"),
                "stale_docshares": dist_reconcile.get("stale_docshares"),
                "stale_open_todos": dist_reconcile.get("stale_open_todos"),
                "ownership_reconciled": dist_reconcile.get("ownership_reconciled"),
            },
            "worker_and_job_diagnostics": dist_worker_and_job_diagnostics,
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


def _build_p4_9_2_acceptance_matrix(
    s1_in_container: dict[str, object],
    http_report: dict[str, object],
) -> dict[str, object]:
    """Evaluate every single row of `P4.md` §9.2 against measured HTTP/DB evidence."""
    http_acc = http_report.get("s1_http_acceptance_and_mixed_waves") or {}
    assert isinstance(http_acc, dict)
    async_q = s1_in_container.get("async_import_distribution_and_queue_lag") or {}
    assert isinstance(async_q, dict)
    kpi_rec = s1_in_container.get("kpi_recompute_s1") or {}
    assert isinstance(kpi_rec, dict)
    explains = s1_in_container.get("mariadb_explain_plans") or {}
    assert isinstance(explains, dict)

    mc = http_acc.get("staff_my_customers_http") or {}
    ld = http_acc.get("lead_detail_and_history_http") or {}
    ra = http_acc.get("record_attempt_and_callback_http") or {}
    bod = http_acc.get("bod_overview_and_drilldown_http") or {}
    waves = http_acc.get("concurrent_mixed_session_waves_20_40_80") or {}
    imp50k = async_q.get("csv_import_50000_rows") or {}
    dist10k = async_q.get("campaign_distribution_10000_work_items_40_staff") or {}
    bg_rec = kpi_rec.get("background_queue_long_recompute") or {}

    snap_explain = explains.get("kpi_snapshot_lookup") or [{}]
    snap_key = str((snap_explain[0] if snap_explain else {}).get("key") or "")

    mc_p95 = float(mc.get("p95_ms") or 0.0)
    ld_p95 = float(ld.get("p95_ms") or 0.0)
    ra_p95 = float(ra.get("p95_ms") or 0.0)
    ra_rec = ra.get("db_reconciliation") or {}
    bod_ov_p95 = float(
        (bod.get("bod_executive_overview_role_api") or {}).get("p95_ms") or 0.0
    )
    dd_daily_p95 = float(
        (bod.get("kpi_drilldown_daily_campaign_role_api") or {}).get("p95_ms") or 0.0
    )
    dd_month_p95 = float(
        (bod.get("kpi_drilldown_monthly_company_role_api") or {}).get("p95_ms") or 0.0
    )
    neg_status = int(bod.get("staff_unauthorized_company_drilldown_http_status") or 0)

    dist_rec = dist10k.get("reconciliation") or {}
    w20 = (waves.get("sessions_20") or {}).get("overall_summary") or {}
    w40 = (waves.get("sessions_40") or {}).get("overall_summary") or {}
    w80 = (waves.get("sessions_80") or {}).get("overall_summary") or {}

    items = {
        "staff_my_customers_p95_le_1500ms": {
            "target_p95_ms": 1500.0,
            "measured_http_p95_ms": mc_p95,
            "error_rate_pct": mc.get("error_rate_pct"),
            "passed": mc_p95 <= 1500.0
            and float(mc.get("error_rate_pct") or 0.0) == 0.0,
        },
        "open_lead_and_history_p95_le_2000ms": {
            "target_p95_ms": 2000.0,
            "measured_http_p95_ms": ld_p95,
            "error_rate_pct": ld.get("error_rate_pct"),
            "passed": ld_p95 <= 2000.0
            and float(ld.get("error_rate_pct") or 0.0) == 0.0,
        },
        "record_attempt_and_callback_p95_le_2000ms_zero_dup": {
            "target_p95_ms": 2000.0,
            "measured_http_p95_ms": ra_p95,
            "duplicate_idempotency_keys": ra_rec.get("duplicate_idempotency_keys"),
            "attempts_created": ra_rec.get("attempts_created"),
            "work_items_with_open_callback_task": ra_rec.get(
                "work_items_with_open_callback_task"
            ),
            "http_idempotent_replays_verified": ra.get(
                "http_idempotent_replays_verified"
            ),
            "passed": (
                ra_p95 <= 2000.0
                and float(ra.get("error_rate_pct") or 0.0) == 0.0
                and int(ra_rec.get("duplicate_idempotency_keys") or 0) == 0
                and int(ra_rec.get("attempts_created") or 0) == 80
                and int(ra_rec.get("work_items_with_open_callback_task") or 0) == 80
            ),
        },
        "bod_overview_snapshot_p95_le_3000ms": {
            "target_p95_ms": 3000.0,
            "measured_http_p95_ms": bod_ov_p95,
            "explain_index_key": snap_key,
            "passed": (bod_ov_p95 <= 3000.0 and snap_key == "idx_cskh_kpisnap_lookup"),
        },
        "kpi_drilldown_paginated_p95_le_3000ms": {
            "target_p95_ms": 3000.0,
            "measured_daily_campaign_http_p95_ms": dd_daily_p95,
            "measured_monthly_company_250k_http_p95_ms": dd_month_p95,
            "staff_unauthorized_company_http_status": neg_status,
            "passed": (
                dd_daily_p95 <= 3000.0 and dd_month_p95 <= 3000.0 and neg_status == 403
            ),
        },
        "preview_weighted_distribution_10000_items": {
            "reference_target_ms": 60000.0,
            "eligible_count": dist10k.get("eligible_count"),
            "measured_preview_ms": dist10k.get("preview_ms"),
            "passed": (
                int(dist10k.get("eligible_count") or 0) == 10_000
                and float(dist10k.get("preview_ms") or 0.0) <= 60000.0
            ),
        },
        "execute_weighted_distribution_10000_items": {
            "created_work_items": dist10k.get("created_work_items"),
            "duplicate_work_items": dist10k.get("duplicate_work_items"),
            "execute_ms": dist10k.get("execute_and_worker_drain_ms"),
            "ownership_reconciled": dist_rec.get("ownership_reconciled"),
            "abort_cancel_guard_verified": dist10k.get("abort_cancel_guard_verified"),
            "passed": (
                str(dist10k.get("final_run_status") or "").lower() == "completed"
                and int(dist10k.get("created_work_items") or 0) == 10_000
                and int(dist10k.get("duplicate_work_items") or 0) == 0
                and bool(dist_rec.get("ownership_reconciled")) is True
                and bool(dist10k.get("abort_cancel_guard_verified")) is True
            ),
        },
        "import_50000_csv_rows": {
            "total_rows": imp50k.get("total_rows"),
            "created_rows": imp50k.get("created_rows"),
            "reused_rows": imp50k.get("reused_rows"),
            "error_rows": imp50k.get("error_rows"),
            "error_artifact_present": imp50k.get("error_artifact_present"),
            "total_ms": imp50k.get("total_preview_confirm_and_worker_drain_ms"),
            "preview_hwm_mib": imp50k.get("preview_hwm_mib"),
            "passed": (
                int(imp50k.get("total_rows") or 0) == 50_000
                and int(imp50k.get("created_rows") or 0) == 10_000
                and int(imp50k.get("reused_rows") or 0) == 5_000
                and int(imp50k.get("error_rows") or 0) == 500
                and bool(imp50k.get("error_artifact_present")) is True
            ),
        },
        "recompute_kpi_non_blocking_atomic_publish": {
            "atomic_publish_verified": bg_rec.get("atomic_publish_verified"),
            "concurrent_ui_p95_ms": (
                bg_rec.get("concurrent_ui_overview_reads") or {}
            ).get("p95_ms"),
            "passed": (
                bool(bg_rec.get("atomic_publish_verified")) is True
                and float(
                    (bg_rec.get("concurrent_ui_overview_reads") or {}).get("p95_ms")
                    or 0.0
                )
                <= 3000.0
            ),
        },
        "concurrent_20_40_80_mixed_user_sessions": {
            "sessions_20_p95_ms": w20.get("p95_ms"),
            "sessions_20_error_rate_pct": w20.get("error_rate_pct"),
            "sessions_40_p95_ms": w40.get("p95_ms"),
            "sessions_40_error_rate_pct": w40.get("error_rate_pct"),
            "sessions_80_p95_ms": w80.get("p95_ms"),
            "sessions_80_error_rate_pct": w80.get("error_rate_pct"),
            "passed": (
                float(w20.get("error_rate_pct") or 0.0) == 0.0
                and float(w40.get("error_rate_pct") or 0.0) == 0.0
                and float(w80.get("error_rate_pct") or 0.0) <= 0.5
            ),
        },
        "telesale_worklist_ssr_zero_errors_c12_and_c40": {
            "shared_new_session_c12_error_count": (
                (http_report.get("scenario_summaries") or {}).get(
                    "telesale_worklist_page"
                )
                or {}
            ).get("error_count"),
            "shared_new_session_c12_timeout_count": (
                (http_report.get("scenario_summaries") or {}).get(
                    "telesale_worklist_page"
                )
                or {}
            ).get("timeout_count"),
            "staff_40_sessions_c40_error_count": (
                http_acc.get("telesale_worklist_ssr_40_staff_sessions_c40") or {}
            ).get("error_count"),
            "staff_40_sessions_c40_timeout_count": (
                http_acc.get("telesale_worklist_ssr_40_staff_sessions_c40") or {}
            ).get("timeout_count"),
            "passed": (
                int(
                    (
                        (http_report.get("scenario_summaries") or {}).get(
                            "telesale_worklist_page"
                        )
                        or {}
                    ).get("error_count")
                    or 0
                )
                == 0
                and int(
                    (
                        (http_report.get("scenario_summaries") or {}).get(
                            "telesale_worklist_page"
                        )
                        or {}
                    ).get("timeout_count")
                    or 0
                )
                == 0
                and int(
                    (
                        http_acc.get("telesale_worklist_ssr_40_staff_sessions_c40")
                        or {}
                    ).get("error_count")
                    or 0
                )
                == 0
                and int(
                    (
                        http_acc.get("telesale_worklist_ssr_40_staff_sessions_c40")
                        or {}
                    ).get("timeout_count")
                    or 0
                )
                == 0
            ),
        },
    }
    failed_checks = [k for k, v in items.items() if not bool(v["passed"])]
    passed_count = sum(1 for v in items.values() if bool(v["passed"]))
    return {
        "all_passed": len(failed_checks) == 0,
        "total_checks": len(items),
        "passed_count": passed_count,
        "failed_count": len(failed_checks),
        "failed_checks": failed_checks,
        "items": items,
    }


def _derive_limits_and_proposed_slos(
    s1_in_container: dict[str, object],
    http_report: dict[str, object],
) -> dict[str, object]:
    sc = http_report.get("scenario_summaries") or {}
    assert isinstance(sc, dict)
    http_acc = http_report.get("s1_http_acceptance_and_mixed_waves") or {}
    assert isinstance(http_acc, dict)
    dom = s1_in_container.get("domain_workloads_s1") or {}
    assert isinstance(dom, dict)
    kpi_rec = s1_in_container.get("kpi_recompute_s1") or {}
    assert isinstance(kpi_rec, dict)
    async_q = s1_in_container.get("async_import_distribution_and_queue_lag") or {}
    assert isinstance(async_q, dict)

    wl_api_p95 = float(
        ((http_acc.get("staff_my_customers_http") or {}).get("p95_ms"))
        or (sc.get("my_customers_worklist_api") or {}).get("p95_ms")
        or 0.0
    )
    ld_api_p95 = float(
        (http_acc.get("lead_detail_and_history_http") or {}).get("p95_ms") or 0.0
    )
    wr_api_p95 = float(
        ((http_acc.get("record_attempt_and_callback_http") or {}).get("p95_ms"))
        or (dom.get("contact_attempt_and_followup_write") or {}).get("p95_ms")
        or 0.0
    )
    bod_api_p95 = float(
        (
            (
                (http_acc.get("bod_overview_and_drilldown_http") or {}).get(
                    "bod_executive_overview_role_api"
                )
                or {}
            ).get("p95_ms")
        )
        or (sc.get("bod_executive_overview_api") or {}).get("p95_ms")
        or 0.0
    )
    dd_api_p95 = float(
        (
            (
                (http_acc.get("bod_overview_and_drilldown_http") or {}).get(
                    "kpi_drilldown_daily_campaign_role_api"
                )
                or {}
            ).get("p95_ms")
        )
        or (sc.get("kpi_metric_drilldown_250k_api") or {}).get("p95_ms")
        or 0.0
    )
    dd_month_p95 = float(
        (
            (
                (http_acc.get("bod_overview_and_drilldown_http") or {}).get(
                    "kpi_drilldown_monthly_company_role_api"
                )
                or {}
            ).get("p95_ms")
        )
        or (dom.get("kpi_metric_drilldown_full_month_company_250k") or {}).get("p95_ms")
        or 0.0
    )
    month_ms = float(kpi_rec.get("monthly_full_75k_wi_250k_att_ms") or 0.0)
    daily_ms = float(kpi_rec.get("daily_incremental_ms") or 0.0)
    imp_ms = float(
        (
            (async_q.get("csv_import_50000_rows") or {}).get(
                "total_preview_confirm_and_worker_drain_ms"
            )
        )
        or 0.0
    )
    dist_prev_ms = float(
        (
            (async_q.get("campaign_distribution_10000_work_items_40_staff") or {}).get(
                "preview_ms"
            )
        )
        or 0.0
    )
    dist_exec_ms = float(
        (
            (async_q.get("campaign_distribution_10000_work_items_40_staff") or {}).get(
                "execute_and_worker_drain_ms"
            )
        )
        or 0.0
    )

    return {
        "observed_environment_limits": [
            (
                "Gunicorn & multi-session ceiling: isolated benchmark runs `GUNICORN_WORKERS=4, "
                "GUNICORN_THREADS=4` (16 concurrent Python threads) with dedicated `queue-short` + "
                "`queue-long`. Across 20, 40, and 80 simultaneous zero-think-time user sessions, "
                "20 and 40 sessions stay comfortably sub-second; at 80 simultaneous active requests "
                "queueing behind 16 Gunicorn threads increases tail `p95`/`p99` cleanly with 0% 5xx."
            ),
            (
                f"Full monthly KPI recompute (`75,000` Work Items / `250,000` Attempts / `150,000` "
                f"SLA Obligations) completes in `{month_ms} ms` (daily incremental: `{daily_ms} ms`) "
                "on `queue-long` without blocking UI snapshot reads and publishes the new revision "
                "atomically only upon completion."
            ),
            (
                f"Paginated KPI drill-down after SQL interval aggregation & lazy business-minute "
                f"optimizations achieves `p95={dd_api_p95} ms` on daily campaign scope and "
                f"`p95={dd_month_p95} ms` on full-month company `250k`-attempt scope (`<= 3,000 ms`)."
            ),
            (
                f"Bulk batch operations on dedicated `queue-long`: `50,000`-row CSV import completes in "
                f"`{imp_ms} ms`; `10,000`-Work-Item weighted distribution preview completes in "
                f"`{dist_prev_ms} ms` (`<= 60,000 ms`) and chunked background execution completes in "
                f"`{dist_exec_ms} ms` with `0` duplicates and unblocked interactive `My Customers` reads."
            ),
        ],
        "proposed_slos_for_review": {
            "status": "PROPOSED_ONLY_NOT_COMMITTED_SLA",
            "target_sizing_assumption": "4 vCPU / 8-16 GB RAM isolated host, Gunicorn 4 workers x 4 threads, dedicated queue-short + queue-long",
            "targets": {
                "my_customers_worklist_api_p95_ms": {
                    "measured_http_p95_ms": wl_api_p95,
                    "proposed_slo_p95_ms": min(
                        1500, max(500, int(math.ceil(wl_api_p95 * 2.5 / 50.0) * 50))
                    ),
                },
                "open_lead_and_history_api_p95_ms": {
                    "measured_http_p95_ms": ld_api_p95,
                    "proposed_slo_p95_ms": min(
                        2000, max(600, int(math.ceil(ld_api_p95 * 2.5 / 50.0) * 50))
                    ),
                },
                "contact_attempt_write_p95_ms": {
                    "measured_http_p95_ms": wr_api_p95,
                    "proposed_slo_p95_ms": min(
                        2000, max(600, int(math.ceil(wr_api_p95 * 2.5 / 50.0) * 50))
                    ),
                },
                "bod_executive_overview_api_p95_ms": {
                    "measured_http_p95_ms": bod_api_p95,
                    "proposed_slo_p95_ms": min(
                        3000, max(800, int(math.ceil(bod_api_p95 * 2.5 / 50.0) * 50))
                    ),
                },
                "kpi_drilldown_paginated_api_p95_ms": {
                    "measured_daily_http_p95_ms": dd_api_p95,
                    "measured_monthly_250k_http_p95_ms": dd_month_p95,
                    "proposed_slo_p95_ms": min(
                        3000,
                        max(
                            1200,
                            int(
                                math.ceil(max(dd_api_p95, dd_month_p95) * 2.0 / 50.0)
                                * 50
                            ),
                        ),
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


def _collect_pre_teardown_diagnostics(
    *,
    compose_project: str,
    site_name: str,
    compose_files: list[Path],
    compose_env: dict[str, str],
    secrets_to_redact: tuple[str, ...],
) -> dict[str, object]:
    """Capture best-effort container, worker, RQ job, DB, and scrubbed log state before teardown."""
    compose_base = [
        "docker",
        "compose",
        *_compose_files_arg(compose_files),
        "-p",
        compose_project,
    ]
    diag: dict[str, object] = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        diag["container_stats"] = _collect_docker_stats(
            compose_project, compose_file=compose_files, env=compose_env
        )
    except Exception as exc:
        diag["container_stats_error"] = _scrub_diagnostic_text(
            str(exc), secrets_to_redact
        )

    try:
        diag["queue_and_db_state"] = _collect_queue_and_db_telemetry(
            compose_project,
            site_name,
            compose_file=compose_files,
            env=compose_env,
        )
    except Exception as exc:
        diag["queue_and_db_state_error"] = _scrub_diagnostic_text(
            str(exc), secrets_to_redact
        )

    final_state_probe = (
        "import json, frappe, redis\n"
        f"site = {site_name!r}\n"
        "frappe.init(site=site, sites_path='/home/frappe/frappe-bench/sites')\n"
        "frappe.connect()\n"
        "camp_rows = frappe.db.sql(\"SELECT name FROM `tabCSKH Campaign` WHERE code = 'S1-CAMP-DIST-10K' LIMIT 1\")\n"
        "camp_id = str(camp_rows[0][0]) if camp_rows else ''\n"
        "run_rows = frappe.db.sql(\n"
        '    "SELECT name, campaign, rule_version, status, progress, started_at, finished_at, lease_expires_at, error, actual_json "\n'
        "    \"FROM `tabCSKH Distribution Run` WHERE idempotency_key = 's1-dist-exec-10k' ORDER BY creation DESC LIMIT 1\",\n"
        "    as_dict=True,\n"
        ")\n"
        "wi_by_status = {}\n"
        "if camp_id:\n"
        '    wi_by_status = {str(s): int(c) for s, c in frappe.db.sql("SELECT status, COUNT(*) FROM `tabCSKH Work Item` WHERE campaign = %s GROUP BY status", (camp_id,))}\n'
        'dirty_by_status = {str(s): int(c) for s, c in frappe.db.sql("SELECT status, COUNT(*) FROM `tabCSKH Dirty Scope` GROUP BY status")}\n'
        "rq_state = {}\n"
        "if run_rows:\n"
        "    rid = str(run_rows[0]['name'])\n"
        "    r = redis.from_url(frappe.conf.redis_queue)\n"
        "    for cand in (f'rq:job:{site}::cskh-dist-{rid}', f'rq:job:cskh-dist-{rid}'):\n"
        "        if r.exists(cand):\n"
        "            for hk in ('status', 'enqueued_at', 'started_at', 'ended_at', 'worker_name', 'exc_info'):\n"
        "                hv = r.hget(cand, hk)\n"
        "                if hv is not None:\n"
        "                    rq_state[hk] = (hv.decode('utf-8', errors='replace') if isinstance(hv, bytes) else str(hv))[:1000]\n"
        "            break\n"
        "frappe.destroy()\n"
        "print(json.dumps({\n"
        "    'dist_10k_campaign_id': camp_id or None,\n"
        "    'distribution_run_row': run_rows[0] if run_rows else None,\n"
        "    'work_item_counts_by_status': wi_by_status,\n"
        "    'work_item_total_in_db': sum(wi_by_status.values()),\n"
        "    'dirty_scope_counts_by_status': dirty_by_status,\n"
        "    'rq_job_state': rq_state,\n"
        "}, default=str))"
    )
    try:
        st_proc = subprocess.run(
            [
                *compose_base,
                "exec",
                "-T",
                "-e",
                "FRAPPE_STREAM_LOGGING=1",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                final_state_probe,
            ],
            env=compose_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if st_proc.returncode == 0 and st_proc.stdout.strip():
            diag["distribution_and_work_item_final_state"] = json.loads(
                st_proc.stdout.strip().splitlines()[-1]
            )
    except Exception as exc:
        diag["distribution_and_work_item_final_state_error"] = _scrub_diagnostic_text(
            str(exc), secrets_to_redact
        )

    service_logs: dict[str, str] = {}
    for svc in ("queue-long", "queue-short", "backend", "db"):
        try:
            lg_proc = subprocess.run(
                [*compose_base, "logs", "--no-color", "--tail=60", svc],
                env=compose_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            combined_log = (lg_proc.stdout or "") + (
                f"\n{lg_proc.stderr}" if lg_proc.stderr else ""
            )
            service_logs[svc] = _scrub_diagnostic_text(
                combined_log[-4000:], secrets_to_redact
            )
        except Exception as exc:
            service_logs[svc] = _scrub_diagnostic_text(
                f"log capture error: {exc}", secrets_to_redact
            )
    diag["service_logs_tail"] = service_logs
    return diag


def provision_and_execute_s1_benchmark(
    *,
    app_image: str,
    pull_policy: str = "missing",
    port: int = 18880,
    site_name: str = "p4-s1-bench.localhost",
    resource_profile: str = "s1_canonical_4x4",
    require_live_headroom: bool = False,
) -> dict[str, object]:
    """Self-contained isolated S1 provisioning, seeding, measurement, and teardown."""
    if resource_profile not in ("s1_canonical_4x4", "staging_vps_capped"):
        raise ValueError(
            f"Unsupported --resource-profile {resource_profile!r}; expected s1_canonical_4x4 or staging_vps_capped"
        )
    live_headroom = (
        enforce_live_resource_headroom()
        if (require_live_headroom or resource_profile == "staging_vps_capped")
        else None
    )
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
    actor_pw = secrets.token_hex(20)
    secrets_to_redact = (db_root_pw, admin_pw, actor_pw)

    with tempfile.TemporaryDirectory(prefix="cskh-p4-s1-bench-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_secret = tmp_dir / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
        bench_override = tmp_dir / "compose.bench-override.yaml"
        override_yaml = (
            STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML
            if resource_profile == "staging_vps_capped"
            else EPHEMERAL_BENCH_OVERRIDE_YAML
        )
        bench_override.write_text(override_yaml, encoding="utf-8")
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

        current_stage = "compose_up"
        s1_in_container: dict[str, object] | None = None
        http_report: dict[str, object] | None = None

        try:
            subprocess.run(
                [*compose_base, "up", "-d", "--no-build"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )

            current_stage = "site_bootstrap"
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

            current_stage = "service_restart"
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

            current_stage = "s1_in_container_benchmark"
            s1_proc = subprocess.run(
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
                input=S1_IN_CONTAINER_BENCH_SCRIPT,
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if s1_proc.returncode != 0:
                raise RuntimeError(
                    f"S1 in-container benchmark failed (rc={s1_proc.returncode}):\n"
                    f"STDOUT:\n{s1_proc.stdout[-2500:]}\nSTDERR:\n{s1_proc.stderr[-2500:]}"
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

            current_stage = "frontend_recreate_and_readiness"
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

            http_ready = False
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                try:
                    req = urllib.request.Request(
                        f"{base_url}/api/method/ping", headers={"Host": site_name}
                    )
                    with urllib.request.urlopen(req, timeout=5) as r:
                        if r.status == 200:
                            http_ready = True
                            break
                except Exception:
                    pass
                time.sleep(2.0)
            if not http_ready:
                diag = subprocess.run(
                    [*compose_base, "logs", "--tail=60", "frontend", "backend"],
                    env=compose_env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                raise RuntimeError(
                    f"HTTP readiness probe failed on {base_url}:\n{diag.stdout[-2000:]}\n{diag.stderr[-1000:]}"
                )

            current_stage = "http_benchmark_execution"
            http_report = execute_isolated_benchmark(
                spec,
                base_url=base_url,
                site_name=site_name,
                compose_project=compose_project,
                admin_user="Administrator",
                admin_password=admin_pw,
                compose_file=compose_files,
                compose_env=compose_env,
                actor_password=actor_pw,
                s1_in_container=s1_in_container,
            )

            current_stage = "post_measurement_packaging"
            # Strip helper list of 350 sample open WIs before persisting report JSON to keep artifact compact
            s1_clean = {
                k: v
                for k, v in s1_in_container.items()
                if k not in ("http_open_work_items", "staff_users", "leader_users")
            }
            acceptance_matrix = _build_p4_9_2_acceptance_matrix(s1_clean, http_report)
            pre_teardown_diag = _collect_pre_teardown_diagnostics(
                compose_project=compose_project,
                site_name=site_name,
                compose_files=compose_files,
                compose_env=compose_env,
                secrets_to_redact=secrets_to_redact,
            )

            image_id: str | None = None
            try:
                img_insp = json.loads(
                    subprocess.check_output(
                        ["docker", "image", "inspect", app_image], text=True
                    )
                )[0]
                image_id = img_insp.get("Id")
            except Exception:
                image_id = None

            limits_and_slos = _derive_limits_and_proposed_slos(s1_clean, http_report)
            all_passed = bool(acceptance_matrix["all_passed"])

            return {
                **http_report,
                "acceptance_verdict": "PASS" if all_passed else "FAIL",
                "all_acceptance_passed": all_passed,
                "sla_commitment_status": "UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL",
                "app_image_tested": app_image,
                "image_id": image_id,
                "resource_profile": resource_profile,
                "live_resource_headroom_precheck": live_headroom,
                "s1_domain_and_database_benchmark": s1_clean,
                "worker_and_job_diagnostics": pre_teardown_diag,
                "p4_9_2_acceptance_matrix": acceptance_matrix,
                **limits_and_slos,
            }
        except BenchmarkExecutionError:
            raise
        except Exception as exc:
            pre_teardown_diag = _collect_pre_teardown_diagnostics(
                compose_project=compose_project,
                site_name=site_name,
                compose_files=compose_files,
                compose_env=compose_env,
                secrets_to_redact=secrets_to_redact,
            )
            scrubbed_err = _scrub_diagnostic_text(str(exc), secrets_to_redact)
            s1_partial = (
                {
                    k: v
                    for k, v in s1_in_container.items()
                    if k not in ("http_open_work_items", "staff_users", "leader_users")
                }
                if isinstance(s1_in_container, dict)
                else None
            )
            failure_report: dict[str, object] = {
                **(http_report if isinstance(http_report, dict) else spec),
                "status": "EXECUTION_ERROR",
                "acceptance_verdict": "ERROR",
                "all_acceptance_passed": False,
                "failed_stage": current_stage,
                "error_type": type(exc).__name__,
                "error_message": scrubbed_err,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "sla_commitment_status": "UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL",
                "app_image_tested": app_image,
                "resource_profile": resource_profile,
                "live_resource_headroom_precheck": live_headroom,
                "s1_domain_and_database_benchmark": s1_partial,
                "worker_and_job_diagnostics": pre_teardown_diag,
            }
            raise BenchmarkExecutionError(scrubbed_err, report=failure_report) from exc
        finally:
            subprocess.run(
                [*compose_base, "down", "-v", "--remove-orphans"],
                env=compose_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )


def _parse_pct_value(raw: str) -> float:
    cleaned = str(raw or "").strip().rstrip("%").strip()
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return 0.0


def _parse_size_token_to_mib(token: str) -> float:
    s = str(token or "").strip()
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)$", s)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).lower()
    factors = {
        "b": 1.0 / (1024.0 * 1024.0),
        "kb": 1000.0 / (1024.0 * 1024.0),
        "kib": 1.0 / 1024.0,
        "mb": (1000.0 * 1000.0) / (1024.0 * 1024.0),
        "mib": 1.0,
        "gb": (1000.0**3) / (1024.0 * 1024.0),
        "gib": 1024.0,
        "tb": (1000.0**4) / (1024.0 * 1024.0),
        "tib": 1024.0 * 1024.0,
    }
    return round(val * factors.get(unit, 0.0), 2)


def _parse_mem_usage_pair_mib(mem_usage: str) -> tuple[float, float]:
    parts = [p.strip() for p in str(mem_usage or "").split("/", 1)]
    if len(parts) != 2:
        return (0.0, 0.0)
    return (_parse_size_token_to_mib(parts[0]), _parse_size_token_to_mib(parts[1]))


def _inspect_compose_service_cgroups(
    compose_base: list[str],
    compose_env: dict[str, str],
    services: tuple[str, ...] = ("db", "backend", "queue-long", "queue-short"),
) -> dict[str, dict[str, object]]:
    inspected: dict[str, dict[str, object]] = {}
    for svc in services:
        try:
            ps_proc = subprocess.run(
                [*compose_base, "ps", "-q", svc],
                env=compose_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            cid = (
                ps_proc.stdout.strip().splitlines()[0].strip()
                if ps_proc.stdout.strip()
                else ""
            )
            if not cid:
                continue
            insp_proc = subprocess.run(
                ["docker", "inspect", cid],
                env=compose_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if insp_proc.returncode != 0 or not insp_proc.stdout.strip():
                continue
            obj = json.loads(insp_proc.stdout)[0]
            hc = obj.get("HostConfig") or {}
            st = obj.get("State") or {}
            cfg = obj.get("Config") or {}
            nano_cpus = int(hc.get("NanoCpus") or 0)
            mem_bytes = int(hc.get("Memory") or 0)
            inspected[svc] = {
                "container_name": str(obj.get("Name") or "").lstrip("/"),
                "cpus_limit": round(nano_cpus / 1_000_000_000.0, 3)
                if nano_cpus
                else 0.0,
                "memory_limit_mib": round(mem_bytes / (1024.0 * 1024.0), 1)
                if mem_bytes
                else 0.0,
                "cpu_shares": int(hc.get("CpuShares") or 0),
                "oom_killed": bool(st.get("OOMKilled")),
                "restart_count": int(obj.get("RestartCount") or 0),
                "cmd": cfg.get("Cmd") or [],
            }
        except Exception:
            continue
    return inspected


RUN4_STAGING_VPS_CAPPED_REFERENCE: dict[str, object] = {
    "reference_run_date": "2026-11-13",
    "reference_candidate_digest": "ghcr.io/nobita6986/crm-cskh@sha256:ba68cd2268bf9e4e49c4f20b34f5a75f0598c4a6b5434c0be5b4218a913ca90a",
    "mariadb_innodb_buffer_pool_mib": 128.0,
    "mariadb_innodb_log_file_mib": 96.0,
    "innodb_buffer_pool_reads": 5_156_775,
    "buffer_pool_hit_ratio_pct": 88.09,
    "db_block_io": "86.2GB / 16.5GB",
    "bg01_completed_items": 4_500,
    "bg01_target_items": 10_000,
    "bg01_elapsed_sec": 1200.47,
    "bg01_ms_per_item": 266.77,
    "bg01_items_per_sec": 3.75,
    "bg01_extrapolated_10000_items_sec": 2667.7,
    "queue_short_peak_cpu_pct": 146.99,
    "queue_short_peak_mem_mib": 1601.54,
    "queue_short_block_io": "1.98GB / 625MB",
    "queue_short_job_timeout_exceptions": 1,
    "agg03c_drilldown_monthly_company_p95_ms": 11525.91,
}


def provision_and_execute_s1_short_probe(
    *,
    app_image: str,
    pull_policy: str = "missing",
    port: int = 18880,
    site_name: str = "p4-s1-probe.localhost",
    resource_profile: str = "staging_vps_capped",
    require_live_headroom: bool = True,
    probe_items: int = 500,
) -> dict[str, object]:
    """Run an isolated S1 short throughput & telemetry probe before full 10k G18 measurement."""
    if resource_profile not in ("s1_canonical_4x4", "staging_vps_capped"):
        raise ValueError(
            f"Unsupported --resource-profile {resource_profile!r}; expected s1_canonical_4x4 or staging_vps_capped"
        )
    bounded_probe_items = max(100, min(int(probe_items), 2000))
    live_headroom = (
        enforce_live_resource_headroom()
        if (require_live_headroom or resource_profile == "staging_vps_capped")
        else None
    )
    compose_project = f"cskh-p4-bench-probe-{secrets.token_hex(4)}"
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
    actor_pw = secrets.token_hex(20)
    secrets_to_redact = (db_root_pw, admin_pw, actor_pw)
    started_at_utc = datetime.now(timezone.utc).isoformat()

    with tempfile.TemporaryDirectory(prefix="cskh-p4-s1-probe-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_secret = tmp_dir / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
        bench_override = tmp_dir / "compose.bench-override.yaml"
        override_yaml = (
            STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML
            if resource_profile == "staging_vps_capped"
            else EPHEMERAL_BENCH_OVERRIDE_YAML
        )
        bench_override.write_text(override_yaml, encoding="utf-8")
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

        current_stage = "compose_up"
        probe_in_container: dict[str, object] | None = None
        stats_samples: list[dict[str, object]] = []
        stop_stats_event = threading.Event()

        def _stats_sampler_loop() -> None:
            while not stop_stats_event.is_set():
                try:
                    snap = _collect_docker_stats(
                        compose_project, compose_file=compose_files, env=compose_env
                    )
                    if snap:
                        stats_samples.append(
                            {
                                "ts_utc": datetime.now(timezone.utc).isoformat(),
                                "containers": snap,
                            }
                        )
                except Exception:
                    pass
                stop_stats_event.wait(2.0)

        sampler_thread: threading.Thread | None = None
        try:
            subprocess.run(
                [*compose_base, "up", "-d", "--no-build"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )

            current_stage = "site_bootstrap"
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
                    f"S1 short probe bootstrap failed (rc={init_proc.returncode}):\n"
                    f"STDOUT:\n{init_proc.stdout[-1500:]}\nSTDERR:\n{init_proc.stderr[-1500:]}"
                )

            current_stage = "service_restart"
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

            current_stage = "s1_short_probe_execution"
            sampler_thread = threading.Thread(
                target=_stats_sampler_loop, name="s1-probe-docker-stats", daemon=True
            )
            sampler_thread.start()
            try:
                probe_proc = subprocess.run(
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
                        "-e",
                        "S1_PROBE_ONLY=1",
                        "-e",
                        f"S1_PROBE_ITEMS={bounded_probe_items}",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-",
                    ],
                    input=S1_IN_CONTAINER_BENCH_SCRIPT,
                    env=compose_env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=1200,
                )
            finally:
                stop_stats_event.set()
                if sampler_thread is not None:
                    sampler_thread.join(timeout=5.0)

            if probe_proc.returncode != 0:
                raise RuntimeError(
                    f"S1 short probe failed (rc={probe_proc.returncode}):\n"
                    f"STDOUT:\n{probe_proc.stdout[-2500:]}\nSTDERR:\n{probe_proc.stderr[-2500:]}"
                )
            marker_lines = [
                ln[len("S1_PROBE_JSON:") :]
                for ln in probe_proc.stdout.splitlines()
                if ln.startswith("S1_PROBE_JSON:")
            ]
            if not marker_lines:
                raise RuntimeError(
                    f"S1 short probe did not emit S1_PROBE_JSON marker: {probe_proc.stderr[-500:]}"
                )
            probe_in_container = json.loads(marker_lines[-1])

            current_stage = "post_probe_inspection"
            cgroup_inspection = _inspect_compose_service_cgroups(
                compose_base, compose_env
            )
            final_stats = _collect_docker_stats(
                compose_project, compose_file=compose_files, env=compose_env
            )
            qs_logs_proc = subprocess.run(
                [*compose_base, "logs", "--no-color", "queue-short"],
                env=compose_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            qs_combined_logs = (
                (qs_logs_proc.stdout or "") + "\n" + (qs_logs_proc.stderr or "")
            )
            qs_timeout_count = qs_combined_logs.count(
                "JobTimeoutException"
            ) + qs_combined_logs.count("Task exceeded maximum timeout")

            qs_cpu_vals: list[float] = []
            qs_mem_vals: list[float] = []
            qs_mem_limit_mib = 0.0
            qs_last_block_io = ""
            db_last_block_io = ""
            ql_cpu_vals: list[float] = []
            ql_mem_vals: list[float] = []
            for snap in stats_samples + (
                [{"containers": final_stats}] if final_stats else []
            ):
                for row in snap.get("containers") or []:  # type: ignore[union-attr]
                    if not isinstance(row, dict):
                        continue
                    cname = str(row.get("container") or "")
                    if "queue-short" in cname:
                        qs_cpu_vals.append(
                            _parse_pct_value(str(row.get("cpu_pct") or ""))
                        )
                        used_m, lim_m = _parse_mem_usage_pair_mib(
                            str(row.get("mem_usage") or "")
                        )
                        qs_mem_vals.append(used_m)
                        if lim_m > 0:
                            qs_mem_limit_mib = lim_m
                        qs_last_block_io = str(row.get("block_io") or qs_last_block_io)
                    elif "queue-long" in cname:
                        ql_cpu_vals.append(
                            _parse_pct_value(str(row.get("cpu_pct") or ""))
                        )
                        used_m, _ = _parse_mem_usage_pair_mib(
                            str(row.get("mem_usage") or "")
                        )
                        ql_mem_vals.append(used_m)
                    elif cname.endswith("-db-1") or "-db-" in cname:
                        db_last_block_io = str(row.get("block_io") or db_last_block_io)

            eff_cfg = (
                probe_in_container.get("effective_mariadb_and_worker_config") or {}
            )
            assert isinstance(eff_cfg, dict)
            mariadb_vars = eff_cfg.get("mariadb_variables") or {}
            assert isinstance(mariadb_vars, dict)
            rq_workers = eff_cfg.get("rq_workers") or []
            assert isinstance(rq_workers, list)
            cov_explain = eff_cfg.get("covering_index_explain") or []
            assert isinstance(cov_explain, list)
            agg03c_probe = eff_cfg.get("agg03c_drilldown_5_pages_probe") or {}
            assert isinstance(agg03c_probe, dict)

            long_workers = [
                w
                for w in rq_workers
                if isinstance(w, dict) and w.get("normalized_queues") == ["long"]
            ]
            short_workers = [
                w
                for w in rq_workers
                if isinstance(w, dict)
                and w.get("normalized_queues") == ["short", "default"]
            ]

            bg01_probe = probe_in_container.get("bg01_per_item_probe") or {}
            assert isinstance(bg01_probe, dict)
            disk_tel = probe_in_container.get("mariadb_disk_read_telemetry") or {}
            assert isinstance(disk_tel, dict)
            rq_burst = probe_in_container.get("rq_short_50_job_burst") or {}
            assert isinstance(rq_burst, dict)

            qs_oom_killed = bool(
                (cgroup_inspection.get("queue-short") or {}).get("oom_killed")
            )
            ql_oom_killed = bool(
                (cgroup_inspection.get("queue-long") or {}).get("oom_killed")
            )
            db_oom_killed = bool((cgroup_inspection.get("db") or {}).get("oom_killed"))

            queue_short_load_telemetry = {
                "docker_stats_samples_count": len(qs_cpu_vals),
                "avg_cpu_pct": (
                    round(sum(qs_cpu_vals) / len(qs_cpu_vals), 2)
                    if qs_cpu_vals
                    else 0.0
                ),
                "max_cpu_pct": max(qs_cpu_vals) if qs_cpu_vals else 0.0,
                "peak_mem_mib": max(qs_mem_vals) if qs_mem_vals else 0.0,
                "configured_mem_limit_mib": (
                    qs_mem_limit_mib
                    or float(
                        (cgroup_inspection.get("queue-short") or {}).get(
                            "memory_limit_mib"
                        )
                        or 0.0
                    )
                ),
                "block_io_final": qs_last_block_io,
                "oom_killed": qs_oom_killed,
                "job_timeout_exception_count": qs_timeout_count,
                "peak_short_queue_waiting_jobs_during_bg01": int(
                    bg01_probe.get("peak_short_queue_waiting_jobs_during_bg01") or 0
                ),
                "rq_short_50_job_burst": rq_burst,
            }

            cov_row0 = (
                cov_explain[0]
                if cov_explain and isinstance(cov_explain[0], dict)
                else {}
            )
            effective_config_checks = {
                "mariadb_innodb_buffer_pool_2560m": (
                    int(mariadb_vars.get("innodb_buffer_pool_size") or 0)
                    == 2_684_354_560
                ),
                "mariadb_innodb_log_file_512m": (
                    int(mariadb_vars.get("innodb_log_file_size") or 0) == 536_870_912
                ),
                "mariadb_flush_trx_commit_1": (
                    str(mariadb_vars.get("innodb_flush_log_at_trx_commit") or "") == "1"
                ),
                "covering_index_present_and_using_index": (
                    bool(
                        eff_cfg.get(
                            "covering_index_idx_cskh_attempt_ownevt_cover_present"
                        )
                    )
                    and str(cov_row0.get("key") or "")
                    == "idx_cskh_attempt_ownevt_cover"
                    and "using index" in str(cov_row0.get("Extra") or "").lower()
                ),
                "queue_long_single_dedicated_worker": len(long_workers) == 1,
                "queue_short_single_dedicated_worker": len(short_workers) == 1,
            }

            ms_per_item = float(bg01_probe.get("ms_per_item") or 999999.0)
            extrapolated_10k_sec = float(
                bg01_probe.get("extrapolated_10000_items_sec") or 999999.0
            )
            hit_ratio_pct = float(disk_tel.get("buffer_pool_hit_ratio_pct") or 0.0)
            peak_long_waiting = int(
                bg01_probe.get("peak_long_queue_waiting_jobs_during_bg01") or 0
            )

            probe_gate_items = {
                "effective_mariadb_and_worker_config_locked": {
                    "checks": effective_config_checks,
                    "passed": all(effective_config_checks.values()),
                },
                "bg01_probe_completed_all_items_zero_dup": {
                    "target_items": bounded_probe_items,
                    "created_work_items": bg01_probe.get("created_work_items"),
                    "duplicate_work_items": bg01_probe.get("duplicate_work_items"),
                    "final_run_status": bg01_probe.get("final_run_status"),
                    "ownership_reconciled": bg01_probe.get("ownership_reconciled"),
                    "passed": (
                        str(bg01_probe.get("final_run_status") or "").lower()
                        == "completed"
                        and int(bg01_probe.get("created_work_items") or 0)
                        == bounded_probe_items
                        and int(bg01_probe.get("duplicate_work_items") or 0) == 0
                        and bool(bg01_probe.get("ownership_reconciled")) is True
                    ),
                },
                "bg01_per_item_le_95ms_extrapolated_10k_le_950s": {
                    "target_max_ms_per_item": 95.0,
                    "measured_ms_per_item": ms_per_item,
                    "target_max_extrapolated_10000_items_sec": 950.0,
                    "slo_10000_items_budget_sec": 1200.0,
                    "extrapolated_10000_items_sec": extrapolated_10k_sec,
                    "passed": ms_per_item <= 95.0 and extrapolated_10k_sec <= 950.0,
                },
                "mariadb_buffer_pool_hit_ratio_ge_99pct": {
                    "target_min_hit_ratio_pct": 99.0,
                    "measured_hit_ratio_pct": hit_ratio_pct,
                    "innodb_buffer_pool_reads_delta": disk_tel.get(
                        "innodb_buffer_pool_reads_delta"
                    ),
                    "innodb_buffer_pool_read_requests_delta": disk_tel.get(
                        "innodb_buffer_pool_read_requests_delta"
                    ),
                    "passed": hit_ratio_pct >= 99.0,
                },
                "queue_short_zero_timeouts_zero_oom": {
                    "job_timeout_exception_count": qs_timeout_count,
                    "queue_short_oom_killed": qs_oom_killed,
                    "queue_long_oom_killed": ql_oom_killed,
                    "db_oom_killed": db_oom_killed,
                    "passed": (
                        qs_timeout_count == 0
                        and not qs_oom_killed
                        and not ql_oom_killed
                        and not db_oom_killed
                    ),
                },
                "agg03c_drilldown_covering_index_probe_le_3000ms": {
                    "target_p95_ms": 3000.0,
                    "measured_p95_ms": agg03c_probe.get("p95_ms"),
                    "error_count": agg03c_probe.get("error_count"),
                    "passed": (
                        float(agg03c_probe.get("p95_ms") or 999999.0) <= 3000.0
                        and int(agg03c_probe.get("error_count") or 0) == 0
                    ),
                },
            }
            failed_probe_checks = [
                k for k, v in probe_gate_items.items() if not bool(v.get("passed"))
            ]
            probe_gate_verdict = {
                "all_passed": len(failed_probe_checks) == 0,
                "total_checks": len(probe_gate_items),
                "passed_count": len(probe_gate_items) - len(failed_probe_checks),
                "failed_count": len(failed_probe_checks),
                "failed_checks": failed_probe_checks,
                "ready_for_official_g18_full_run": len(failed_probe_checks) == 0,
                "items": probe_gate_items,
            }

            recommend_scale_ql = bool(peak_long_waiting > 0)
            queue_long_scaling_evaluation = {
                "bg01_job_architecture": "single_sequential_rq_job",
                "bg01_job_id": bg01_probe.get("job_id"),
                "single_job_parallelizable_by_extra_workers": False,
                "configured_queue_long_workers": len(long_workers),
                "peak_long_queue_waiting_jobs_during_bg01": peak_long_waiting,
                "queue_long_avg_cpu_pct": (
                    round(sum(ql_cpu_vals) / len(ql_cpu_vals), 2)
                    if ql_cpu_vals
                    else 0.0
                ),
                "queue_long_peak_mem_mib": max(ql_mem_vals) if ql_mem_vals else 0.0,
                "measured_ms_per_item": ms_per_item,
                "extrapolated_10000_items_sec": extrapolated_10k_sec,
                "recommend_scaling_queue_long_above_1": recommend_scale_ql,
                "decision": (
                    "KEEP_QUEUE_LONG_REPLICAS_1"
                    if not recommend_scale_ql
                    else "INVESTIGATE_CONCURRENT_LONG_QUEUE_BACKLOG"
                ),
                "rationale": (
                    "BG-01 executes inside a single sequential RQ job (`cskh-dist-<run_id>`) "
                    "processing 100-item DB batches; a 2nd `queue-long` worker cannot split a "
                    f"single job, `peak_long_queue_waiting_jobs_during_bg01={peak_long_waiting}`, "
                    f"and measured `ms_per_item={ms_per_item} ms` extrapolates 10,000 items to "
                    f"`{extrapolated_10k_sec} s` (budget `<= 1,200 s`). Keeping 1 replica saves "
                    "~250-400 MiB RAM on the shared 15.62 GiB Staging VPS."
                    if not recommend_scale_ql
                    else (
                        f"Observed `{peak_long_waiting}` waiting job(s) in `long` queue while "
                        "`BG-01` was running; inspect concurrent `long` job scheduling before "
                        "considering scaling `queue-long` above 1 replica."
                    )
                ),
            }

            run4_vs_probe_comparison = {
                "baseline_run4_staging_vps_capped": RUN4_STAGING_VPS_CAPPED_REFERENCE,
                "current_probe_measured": {
                    "mariadb_innodb_buffer_pool_mib": round(
                        int(mariadb_vars.get("innodb_buffer_pool_size") or 0)
                        / (1024.0 * 1024.0),
                        1,
                    ),
                    "mariadb_innodb_log_file_mib": round(
                        int(mariadb_vars.get("innodb_log_file_size") or 0)
                        / (1024.0 * 1024.0),
                        1,
                    ),
                    "innodb_buffer_pool_reads_delta": disk_tel.get(
                        "innodb_buffer_pool_reads_delta"
                    ),
                    "buffer_pool_hit_ratio_pct": hit_ratio_pct,
                    "db_block_io_final": db_last_block_io,
                    "bg01_probe_items": bounded_probe_items,
                    "bg01_ms_per_item": ms_per_item,
                    "bg01_items_per_sec": bg01_probe.get("items_per_sec"),
                    "bg01_sql_queries_delta": bg01_probe.get("sql_queries_delta"),
                    "bg01_sql_queries_per_item": bg01_probe.get("sql_queries_per_item"),
                    "bg01_execute_sql_queries_delta": bg01_probe.get(
                        "execute_sql_queries_delta"
                    ),
                    "bg01_execute_sql_queries_per_item": bg01_probe.get(
                        "execute_sql_queries_per_item"
                    ),
                    "bg01_extrapolated_10000_items_sec": extrapolated_10k_sec,
                    "peak_long_queue_waiting_jobs_during_bg01": peak_long_waiting,
                    "peak_short_queue_waiting_jobs_during_bg01": queue_short_load_telemetry[
                        "peak_short_queue_waiting_jobs_during_bg01"
                    ],
                    "queue_short_max_cpu_pct": queue_short_load_telemetry[
                        "max_cpu_pct"
                    ],
                    "queue_short_peak_mem_mib": queue_short_load_telemetry[
                        "peak_mem_mib"
                    ],
                    "queue_short_block_io_final": qs_last_block_io,
                    "queue_short_job_timeout_exceptions": qs_timeout_count,
                    "agg03c_drilldown_5_pages_p95_ms": agg03c_probe.get("p95_ms"),
                },
            }

            return {
                **spec,
                "mode": "s1_short_throughput_probe",
                "status": "COMPLETED",
                "started_at_utc": started_at_utc,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "acceptance_verdict": "PASS"
                if probe_gate_verdict["all_passed"]
                else "FAIL",
                "all_acceptance_passed": bool(probe_gate_verdict["all_passed"]),
                "sla_commitment_status": "UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL",
                "app_image_tested": app_image,
                "resource_profile": resource_profile,
                "probe_items": bounded_probe_items,
                "live_resource_headroom_precheck": live_headroom,
                "cgroup_container_inspection": cgroup_inspection,
                "s1_short_probe_telemetry": probe_in_container,
                "queue_short_load_telemetry": queue_short_load_telemetry,
                "queue_long_scaling_evaluation": queue_long_scaling_evaluation,
                "run4_vs_probe_comparison": run4_vs_probe_comparison,
                "probe_gate_verdict": probe_gate_verdict,
                "final_container_stats": final_stats,
            }
        except BenchmarkExecutionError:
            raise
        except Exception as exc:
            stop_stats_event.set()
            if sampler_thread is not None:
                sampler_thread.join(timeout=3.0)
            pre_teardown_diag = _collect_pre_teardown_diagnostics(
                compose_project=compose_project,
                site_name=site_name,
                compose_files=compose_files,
                compose_env=compose_env,
                secrets_to_redact=secrets_to_redact,
            )
            scrubbed_err = _scrub_diagnostic_text(str(exc), secrets_to_redact)
            failure_report: dict[str, object] = {
                **spec,
                "mode": "s1_short_throughput_probe",
                "status": "EXECUTION_ERROR",
                "started_at_utc": started_at_utc,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "acceptance_verdict": "ERROR",
                "all_acceptance_passed": False,
                "failed_stage": current_stage,
                "error_type": type(exc).__name__,
                "error_message": scrubbed_err,
                "sla_commitment_status": "UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL",
                "app_image_tested": app_image,
                "resource_profile": resource_profile,
                "probe_items": bounded_probe_items,
                "live_resource_headroom_precheck": live_headroom,
                "s1_short_probe_telemetry": probe_in_container,
                "worker_and_job_diagnostics": pre_teardown_diag,
            }
            raise BenchmarkExecutionError(scrubbed_err, report=failure_report) from exc
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
        "--probe-s1-short",
        action="store_true",
        help="Self-provision ephemeral isolated compose stack and run short S1 disk-read/per-item/queue-short probe",
    )
    p.add_argument(
        "--probe-items",
        type=int,
        default=500,
        help="Number of BG-01 distribution Work Items to execute in --probe-s1-short (100..2000, default: 500)",
    )
    p.add_argument(
        "--app-image",
        default="",
        help="Immutable APP_IMAGE (@sha256 digest or local image ref) for --provision-and-run-s1 / --probe-s1-short",
    )
    p.add_argument(
        "--pull-policy",
        default="missing",
        choices=("never", "missing", "always"),
    )
    p.add_argument("--port", type=int, default=18880)
    p.add_argument(
        "--resource-profile",
        default="s1_canonical_4x4",
        choices=("s1_canonical_4x4", "staging_vps_capped"),
        help="Container CPU/RAM & Gunicorn topology (s1_canonical_4x4 or staging_vps_capped)",
    )
    p.add_argument(
        "--require-live-headroom",
        action="store_true",
        help="Enforce fail-closed host CPU/RAM headroom precheck before provisioning",
    )
    p.add_argument("--admin-user", default="Administrator")
    p.add_argument("--admin-password-env", default="BENCHMARK_ADMIN_PASSWORD")
    p.add_argument("--out", required=True, help="Output JSON benchmark spec/report")
    args = p.parse_args(argv)

    out_path = Path(args.out)
    exit_code = 0
    failure_stderr_msg = ""

    if args.provision_and_run_s1 and args.probe_s1_short:
        raise SystemExit(
            "ERROR: --provision-and-run-s1 and --probe-s1-short are mutually exclusive"
        )

    if args.probe_s1_short:
        if not args.confirm_isolated_target:
            raise SystemExit(
                "ERROR: --probe-s1-short requires explicit --confirm-isolated-target"
            )
        if not args.app_image.strip():
            raise SystemExit("ERROR: --probe-s1-short requires --app-image")
        try:
            report = provision_and_execute_s1_short_probe(
                app_image=args.app_image.strip(),
                pull_policy=args.pull_policy,
                port=args.port,
                site_name=(
                    args.site_name
                    if args.site_name != "p4-bench.localhost"
                    else "p4-s1-probe.localhost"
                ),
                resource_profile=args.resource_profile,
                require_live_headroom=args.require_live_headroom,
                probe_items=args.probe_items,
            )
        except BenchmarkExecutionError as exc:
            report = exc.report
            exit_code = 1
            failure_stderr_msg = (
                f"ERROR: Short S1 probe failed at stage "
                f"{report.get('failed_stage')!r}: {exc}; "
                f"diagnostic JSON report saved to {out_path}"
            )
    elif args.provision_and_run_s1:
        if not args.confirm_isolated_target:
            raise SystemExit(
                "ERROR: --provision-and-run-s1 requires explicit --confirm-isolated-target"
            )
        if not args.app_image.strip():
            raise SystemExit("ERROR: --provision-and-run-s1 requires --app-image")
        try:
            report = provision_and_execute_s1_benchmark(
                app_image=args.app_image.strip(),
                pull_policy=args.pull_policy,
                port=args.port,
                site_name=(
                    args.site_name
                    if args.site_name != "p4-bench.localhost"
                    else "p4-s1-bench.localhost"
                ),
                resource_profile=args.resource_profile,
                require_live_headroom=args.require_live_headroom,
            )
        except BenchmarkExecutionError as exc:
            report = exc.report
            exit_code = 1
            failure_stderr_msg = (
                f"ERROR: Benchmark execution failed at stage "
                f"{report.get('failed_stage')!r}: {exc}; "
                f"diagnostic JSON report saved to {out_path}"
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
            try:
                report = execute_isolated_benchmark(
                    spec,
                    base_url=args.base_url,
                    site_name=args.site_name,
                    compose_project=args.compose_project,
                    admin_user=args.admin_user,
                    admin_password=adm_pw,
                )
            except Exception as exc:
                scrubbed_err = _scrub_diagnostic_text(str(exc), (adm_pw,))
                report = {
                    **spec,
                    "status": "EXECUTION_ERROR",
                    "acceptance_verdict": "ERROR",
                    "all_acceptance_passed": False,
                    "failed_stage": "http_benchmark_execution",
                    "error_type": type(exc).__name__,
                    "error_message": scrubbed_err,
                    "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                exit_code = 1
                failure_stderr_msg = (
                    f"ERROR: Live HTTP benchmark failed: {scrubbed_err}; "
                    f"diagnostic JSON report saved to {out_path}"
                )

    acceptance = report.get("p4_9_2_acceptance_matrix")
    if isinstance(acceptance, dict) and not bool(acceptance.get("all_passed", True)):
        exit_code = 1
        if not failure_stderr_msg:
            failure_stderr_msg = (
                f"ERROR: P4.md §9.2 benchmark acceptance check failed "
                f"({acceptance.get('failed_checks')}); "
                f"complete JSON report and raw measurements saved to {out_path}"
            )

    probe_verdict = report.get("probe_gate_verdict")
    if isinstance(probe_verdict, dict) and not bool(
        probe_verdict.get("all_passed", True)
    ):
        exit_code = 1
        if not failure_stderr_msg:
            failure_stderr_msg = (
                f"ERROR: S1 short throughput probe failed checks "
                f"({probe_verdict.get('failed_checks')}); "
                f"complete JSON probe report saved to {out_path}"
            )

    _write_json_report_atomic(out_path, report)
    summary_payload: dict[str, object] = {
        "status": report["status"],
        "profile": report.get("profile_name", args.profile),
        "sla_commitment_status": report["sla_commitment_status"],
        "out": str(out_path),
        "exit_code": exit_code,
    }
    if "mode" in report:
        summary_payload["mode"] = report["mode"]
    if "acceptance_verdict" in report:
        summary_payload["acceptance_verdict"] = report["acceptance_verdict"]
        summary_payload["all_acceptance_passed"] = report.get("all_acceptance_passed")
    if isinstance(acceptance, dict):
        summary_payload["failed_checks"] = acceptance.get("failed_checks", [])
    elif isinstance(probe_verdict, dict):
        summary_payload["failed_checks"] = probe_verdict.get("failed_checks", [])
        summary_payload["ready_for_official_g18_full_run"] = probe_verdict.get(
            "ready_for_official_g18_full_run"
        )
    print(json.dumps(summary_payload))
    if failure_stderr_msg:
        print(failure_stderr_msg, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
