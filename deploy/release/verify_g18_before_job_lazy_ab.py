#!/usr/bin/env python3
"""Controlled Before/After A/B verifier for `G18` `before_job` lazy PDF hook remediation.

Compares `177df99a…` (Before, `Ops 54cb7a1` report `08e` eager import baseline) against a
new candidate image digest (After) on the **exact same host** under the **exact same cgroup
resource profile** (`staging_vps_capped_b_plus_2_5vcpu`: `queue-short = 0.50 vCPU / 2048 MiB`,
`queue-long = 1.25 vCPU`, `backend = 2.50 vCPU`, `db = 2.00 vCPU`), WITHOUT modifying a
single line of `benchmark_p4_isolated.py` or its `50`-job / `60.0s` wait window:

  1. Hook cost breakdown inside `queue-short` (`0.50` vCPU cgroup):
     - Cold Python process `before_job` `import_ms`, `call_ms`, `total_ms` (3 runs + median),
     - `sys.modules` state after non-PDF `execute_job(..., method="frappe.ping")` (`weasyprint`,
       `frappe.utils.weasyprint`, `frappe.utils.pdf`, `pdfkit`, `cssutils`, `pypdf`, `bs4`,
       `crm_cskh.services.pdf_assets`).
  2. Exact `benchmark_p4_isolated.py` `rq_short_50_job_burst` (`50` `s1-lag-probe` jobs on
     `queue-short` + identical `60.0s` drain wait window):
     - Jobs completed within the `60.0s` wait window & effective throughput (`jobs/sec`),
     - Residual queue depth immediately before HTTP waves (`queue_depths_before_http_waves`:
       `short`, `default`, `long` at `t = min(drain, 60.0s)`),
     - Immediate `API-00` (`GET /api/method/crm_cskh.api.ops.health_ping`) HTTP wave (`40`
       requests at concurrency `8`) right at the end of the `60.0s` window,
     - Full 50-job completion wall time, true worker throughput (`jobs/sec`), and per-job RQ
       queue lag (`started_at - enqueued_at` and `ended_at - enqueued_at`: `min`, `p50`, `p95`,
       `max` ms).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import platform
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RELEASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = RELEASE_DIR.parent.parent
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from benchmark_p4_isolated import (  # noqa: E402
    LOCK_FILE,
    VERIFY_COMPOSE_FILE,
    _compose_files_arg,
    enforce_isolated_benchmark_target,
)
from g18_resource_profiles import (  # noqa: E402
    RAW_G18_RESOURCE_PROFILES,
    collect_pre_acceptance_runtime_snapshot,
    get_g18_resource_profile_spec,
    verify_declared_vs_runtime_config_snapshot,
)

CANONICAL_BENCHMARK_BURST_JOBS = 50
CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC = 60.0


def _pct(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    k = (len(ordered) - 1) * (pct / 100.0)
    f_idx = int(math.floor(k))
    c_idx = int(math.ceil(k))
    if f_idx == c_idx:
        return round(ordered[f_idx], 3)
    return round(ordered[f_idx] * (c_idx - k) + ordered[c_idx] * (k - f_idx), 3)


def summarize_latencies_ms(vals: list[float]) -> dict[str, Any]:
    ordered = sorted(vals)
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3) if ordered else 0.0,
        "p50_ms": _pct(ordered, 50.0),
        "p95_ms": _pct(ordered, 95.0),
        "p99_ms": _pct(ordered, 99.0),
        "max_ms": round(ordered[-1], 3) if ordered else 0.0,
    }


def wait_for_quiet_host_load(
    *, max_load_per_cpu: float = 0.45, timeout_sec: float = 90.0
) -> dict[str, Any]:
    """Wait briefly between heavy container runs so Before and After start from equal quiet CPU."""
    cpu_count = os.cpu_count() or 1
    t0 = time.monotonic()
    deadline = t0 + timeout_sec
    last_load = 0.0
    while True:
        if not hasattr(os, "getloadavg"):
            return {"cpu_count": cpu_count, "waited_sec": 0.0, "load_per_cpu": None}
        try:
            last_load = os.getloadavg()[0]
        except OSError:
            return {"cpu_count": cpu_count, "waited_sec": 0.0, "load_per_cpu": None}
        load_per_cpu = last_load / cpu_count
        if load_per_cpu <= max_load_per_cpu or time.monotonic() >= deadline:
            return {
                "cpu_count": cpu_count,
                "waited_sec": round(time.monotonic() - t0, 2),
                "load_1m": round(last_load, 3),
                "load_per_cpu": round(load_per_cpu, 3),
            }
        time.sleep(2.0)


COLD_HOOK_PROBE_PY = r"""
import importlib, json, os, sys, time

# 1. Measure raw cold Python import + call of `crm_cskh.services.pdf_hooks` / `pdf_assets` BEFORE Frappe loads apps
os.chdir("/home/frappe/frappe-bench/sites")
import frappe
frappe.init(site=os.environ["BENCH_SITE"], sites_path=".")
frappe.connect()
try:
    monitored = (
        "weasyprint",
        "frappe.utils.weasyprint",
        "frappe.utils.pdf",
        "pdfkit",
        "cssutils",
        "pypdf",
        "bs4",
        "crm_cskh.services.pdf_assets",
    )
    # Identical `08e` measurement across Before and After (`frappe.get_hooks("before_job")` -> `frappe.get_attr` -> `fn()`)
    hooks = [str(h) for h in (frappe.get_hooks("before_job") or [])]
    before_mods = {m: (m in sys.modules) for m in monitored}

    hook_rows = []
    for h in hooks:
        t0 = time.perf_counter()
        fn = frappe.get_attr(h)
        t1 = time.perf_counter()
        fn()
        t2 = time.perf_counter()
        hook_rows.append({
            "hook": h,
            "import_ms": round((t1 - t0) * 1000.0, 3),
            "call_ms": round((t2 - t1) * 1000.0, 3),
            "total_ms": round((t2 - t0) * 1000.0, 3),
        })

    after_hook_mods = {m: (m in sys.modules) for m in monitored}
    from frappe.utils.background_jobs import execute_job
    execute_job(
        site=os.environ["BENCH_SITE"],
        method="frappe.ping",
        event=None,
        job_name="ab-cold-ping-probe",
        kwargs={},
        user="Administrator",
        is_async=False,
    )
    after_job_mods = {m: (m in sys.modules) for m in monitored}

    # Also measure standalone unwarmed reload of the hook module itself for full transparency
    primary_hook = next((h for h in hooks if "pdf_hooks" in h or "pdf_assets" in h), "")
    unwarmed_reload = None
    if "." in primary_hook:
        mod_name, attr_name = primary_hook.rsplit(".", 1)
        sys.modules.pop(mod_name, None)
        u0 = time.perf_counter()
        m_obj = importlib.import_module(mod_name)
        u1 = time.perf_counter()
        getattr(m_obj, attr_name)()
        u2 = time.perf_counter()
        unwarmed_reload = {
            "module": mod_name,
            "import_ms": round((u1 - u0) * 1000.0, 3),
            "call_ms": round((u2 - u1) * 1000.0, 3),
            "total_ms": round((u2 - u0) * 1000.0, 3),
        }

    print("AB_HOOK_PROBE_JSON:" + json.dumps({
        "before_job_hooks": hooks,
        "hook_timings": hook_rows,
        "unwarmed_hook_module_reload": unwarmed_reload,
        "sys_modules_before_hook": before_mods,
        "sys_modules_after_hook": after_hook_mods,
        "sys_modules_after_non_pdf_job": after_job_mods,
    }))
finally:
    frappe.destroy()
"""

RQ_WARMUP_PY = r"""
import json, os, time
import frappe, redis
from rq.job import Job
from frappe.utils.background_jobs import generate_qname

site = os.environ["BENCH_SITE"]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    rq_conn = redis.from_url(frappe.conf.redis_queue)
    all_q_keys = [f"rq:queue:{generate_qname(q)}" for q in ("short", "default", "long")]
    warm_job = frappe.enqueue("frappe.ping", queue="short", job_name="ab-warmup-ping")
    dl = time.perf_counter() + 60.0
    while time.perf_counter() < dl:
        rem = sum(int(rq_conn.llen(qk)) for qk in all_q_keys)
        fetched = Job.fetch(warm_job.id, connection=rq_conn)
        if rem == 0 and fetched.get_status(refresh=True) in ("finished", "failed", "stopped", "canceled"):
            break
        time.sleep(0.05)
    print("AB_WARMUP_JSON:" + json.dumps({"warmup_job_status": Job.fetch(warm_job.id, connection=rq_conn).get_status(refresh=True)}))
finally:
    frappe.destroy()
"""

RQ_BURST_ENQUEUE_AND_60S_WINDOW_PY = r"""
import json, os, time
import frappe, redis
from rq.job import Job
from frappe.utils.background_jobs import generate_qname

site = os.environ["BENCH_SITE"]
burst_count = int(os.environ["AB_BURST_JOBS"])
window_sec = float(os.environ["AB_DRAIN_WINDOW_SEC"])

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    rq_conn = redis.from_url(frappe.conf.redis_queue)
    short_q_key = f"rq:queue:{generate_qname('short')}"
    q_map = {q: f"rq:queue:{generate_qname(q)}" for q in ("short", "default", "long")}
    all_q_keys = list(q_map.values())

    # Exact `benchmark_p4_isolated.py` 50-job enqueue + 60.0s drain window
    t_rq0 = time.perf_counter()
    job_ids = []
    last_rq_job = None
    for j_idx in range(burst_count):
        last_rq_job = frappe.enqueue("frappe.ping", queue="short", job_name=f"s1-lag-probe-{j_idx:03d}")
        job_ids.append(last_rq_job.id)
    enqueue_50_ms = round((time.perf_counter() - t_rq0) * 1000.0, 2)
    peak_q_depth = int(rq_conn.llen(short_q_key))
    last_job_key = f"rq:job:{last_rq_job.id}" if getattr(last_rq_job, "id", None) else None

    t_drain0 = time.perf_counter()
    drain_deadline = t_drain0 + window_sec
    while time.perf_counter() < drain_deadline:
        rem = sum(int(rq_conn.llen(qk)) for qk in all_q_keys)
        j_done = True
        if last_job_key:
            j_st = rq_conn.hget(last_job_key, "status")
            j_done = j_st in (b"finished", b"failed", b"stopped", b"canceled", "finished", "failed", None)
        if rem == 0 and j_done:
            break
        time.sleep(0.05)
    drain_window_ms = round((time.perf_counter() - t_drain0) * 1000.0, 2)

    queue_depths_before_http = {q: int(rq_conn.llen(qk)) for q, qk in q_map.items()}
    finished_at_boundary = 0
    for jid in job_ids:
        st = rq_conn.hget(f"rq:job:{jid}", "status")
        if st in (b"finished", "finished"):
            finished_at_boundary += 1

    print("AB_WINDOW_JSON:" + json.dumps({
        "job_ids": job_ids,
        "burst_jobs": burst_count,
        "window_budget_sec": window_sec,
        "enqueue_50_jobs_ms": enqueue_50_ms,
        "peak_queue_short_depth": peak_q_depth,
        "drain_to_zero_or_window_ms": drain_window_ms,
        "drained_within_window": (
            sum(queue_depths_before_http.values()) == 0 and finished_at_boundary == burst_count
        ),
        "jobs_finished_within_window": finished_at_boundary,
        "effective_window_jobs_per_sec": (
            round(finished_at_boundary / (drain_window_ms / 1000.0), 3)
            if drain_window_ms > 0
            else 0.0
        ),
        "queue_depths_before_http_waves": queue_depths_before_http,
        "t_drain0_epoch": time.time() - (drain_window_ms / 1000.0),
    }))
finally:
    frappe.destroy()
"""

RQ_POST_HTTP_FULL_DRAIN_AND_LAG_PY = r"""
import json, os, sys, time
import frappe, redis
from rq.job import Job
from frappe.utils.background_jobs import generate_qname

payload = json.loads(sys.stdin.read())
site = payload["site_name"]
job_ids = payload["job_ids"]

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    rq_conn = redis.from_url(frappe.conf.redis_queue)
    all_q_keys = [f"rq:queue:{generate_qname(q)}" for q in ("short", "default", "long")]

    dl = time.monotonic() + 360.0
    while time.monotonic() < dl:
        rem = sum(int(rq_conn.llen(qk)) for qk in all_q_keys)
        statuses = [rq_conn.hget(f"rq:job:{jid}", "status") for jid in job_ids]
        if rem == 0 and all(
            s in (b"finished", b"failed", b"stopped", b"canceled", "finished", "failed")
            for s in statuses
        ):
            break
        time.sleep(0.1)

    wait_lags_ms = []
    e2e_lags_ms = []
    exec_spans_ms = []
    enqueued_dts = []
    ended_dts = []
    failed_jobs = 0

    for jid in job_ids:
        job = Job.fetch(jid, connection=rq_conn)
        if job.get_status(refresh=True) != "finished":
            failed_jobs += 1
        enq = job.enqueued_at
        st = job.started_at
        end = job.ended_at
        if enq:
            enqueued_dts.append(enq)
        if end:
            ended_dts.append(end)
        if enq and st:
            wait_lags_ms.append(max(0.0, (st - enq).total_seconds() * 1000.0))
        if enq and end:
            e2e_lags_ms.append(max(0.0, (end - enq).total_seconds() * 1000.0))
        if st and end:
            exec_spans_ms.append(max(0.0, (end - st).total_seconds() * 1000.0))

    if enqueued_dts and ended_dts:
        full_wall_ms = round(
            max(1.0, (max(ended_dts) - min(enqueued_dts)).total_seconds() * 1000.0), 2
        )
    else:
        full_wall_ms = 0.0

    print("AB_LAG_JSON:" + json.dumps({
        "total_jobs": len(job_ids),
        "failed_jobs": failed_jobs,
        "full_50_jobs_wall_drain_ms": full_wall_ms,
        "true_worker_throughput_jobs_per_sec": (
            round(len(job_ids) / (full_wall_ms / 1000.0), 3) if full_wall_ms > 0 else 0.0
        ),
        "wait_lags_ms": wait_lags_ms,
        "e2e_lags_ms": e2e_lags_ms,
        "exec_spans_ms": exec_spans_ms,
    }))
finally:
    frappe.destroy()
"""


def _extract_marker_json(stdout: str, marker: str) -> dict[str, Any]:
    matches = [ln[len(marker) :] for ln in stdout.splitlines() if ln.startswith(marker)]
    if not matches:
        raise RuntimeError(
            f"Missing expected output marker {marker!r} in:\n{stdout[-2000:]}"
        )
    return json.loads(matches[-1])


def _run_api00_health_ping_wave(
    base_url: str, site_name: str, *, total_requests: int = 40, concurrency: int = 8
) -> dict[str, Any]:
    url = f"{base_url}/api/method/ping"

    def _one(_idx: int) -> tuple[float, int]:
        t0 = time.perf_counter()
        req = urllib.request.Request(url, headers={"Host": site_name})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read()
                code = int(resp.status)
        except Exception:
            code = 0
        return (time.perf_counter() - t0) * 1000.0, code

    t_wave0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pairs = list(pool.map(_one, range(total_requests)))
    elapsed_s = max(0.001, time.perf_counter() - t_wave0)
    lats = [p[0] for p in pairs]
    errs = sum(1 for _, code in pairs if code != 200)
    return {
        **summarize_latencies_ms(lats),
        "error_count": errs,
        "throughput_rps": round(total_requests / elapsed_s, 3),
    }


def measure_single_candidate_under_capped_profile(
    *,
    label: str,
    app_image: str,
    profile_key: str,
    pull_policy: str,
    port: int,
    site_name: str,
    burst_jobs: int = CANONICAL_BENCHMARK_BURST_JOBS,
    drain_window_sec: float = CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC,
) -> dict[str, Any]:
    """Provision an isolated cgroup-capped stack for `app_image`, measure hook + 50-job burst, tear down."""
    if burst_jobs != CANONICAL_BENCHMARK_BURST_JOBS:
        raise ValueError(
            f"Benchmark workload invariant violated: burst_jobs={burst_jobs} != {CANONICAL_BENCHMARK_BURST_JOBS}"
        )
    if abs(drain_window_sec - CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC) > 1e-6:
        raise ValueError(
            f"Benchmark wait-window invariant violated: drain_window_sec={drain_window_sec} != {CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC}"
        )

    compose_project = f"cskh-p4-bench-ab-{label}-{secrets.token_hex(3)}"
    base_url = f"http://127.0.0.1:{port}"
    enforce_isolated_benchmark_target(
        base_url=base_url,
        site_name=site_name,
        compose_project=compose_project,
        confirm_isolated_target=True,
    )

    quiet_pre = wait_for_quiet_host_load()
    profile_spec = get_g18_resource_profile_spec(profile_key)
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    db_root_pw = secrets.token_hex(24)
    admin_pw = secrets.token_hex(24)

    with tempfile.TemporaryDirectory(prefix=f"cskh-p4-ab-{label}-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_secret = tmp_dir / "db_root_password.txt"
        db_secret.write_text(db_root_pw, encoding="utf-8")
        bench_override = tmp_dir / "compose.bench-override.yaml"
        bench_override.write_text(
            str(profile_spec["verify_compose_override_yaml"]), encoding="utf-8"
        )
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
                    f"[{label}] site bootstrap failed:\n{init_proc.stdout[-1500:]}\n{init_proc.stderr[-1500:]}"
                )

            # Stop workers & flush stale pre-bootstrap RQ worker registrations before starting fresh workers
            subprocess.run(
                [*compose_base, "stop", "scheduler", "queue-short", "queue-long"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
            subprocess.run(
                [*compose_base, "exec", "-T", "redis-queue", "redis-cli", "FLUSHALL"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            subprocess.run(
                [*compose_base, "restart", "backend", "websocket"],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            subprocess.run(
                [
                    *compose_base,
                    "up",
                    "-d",
                    "--no-build",
                    "scheduler",
                    "queue-short",
                    "queue-long",
                ],
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
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
            dl = time.monotonic() + 90.0
            while time.monotonic() < dl:
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
                time.sleep(1.5)
            if not http_ready:
                raise RuntimeError(f"[{label}] HTTP readiness failed on {base_url}")

            runtime_snap = collect_pre_acceptance_runtime_snapshot(
                compose_base=compose_base,
                compose_env=compose_env,
                site_name=site_name,
                profile_key=profile_key,
            )
            runtime_verdict = verify_declared_vs_runtime_config_snapshot(
                runtime_snap, profile_key=profile_key
            )

            # 1. Cold Python process `before_job` hook measurement inside `queue-short` (`0.50` vCPU)
            cold_runs: list[dict[str, Any]] = []
            for _ in range(3):
                c_proc = subprocess.run(
                    [
                        *compose_base,
                        "exec",
                        "-T",
                        "-e",
                        f"BENCH_SITE={site_name}",
                        "queue-short",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-",
                    ],
                    input=COLD_HOOK_PROBE_PY,
                    env=compose_env,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                cold_runs.append(
                    _extract_marker_json(c_proc.stdout, "AB_HOOK_PROBE_JSON:")
                )

            primary_imports: list[float] = []
            primary_calls: list[float] = []
            primary_totals: list[float] = []
            primary_hook_name = ""
            for cr in cold_runs:
                for hrow in cr["hook_timings"]:
                    if "pdf_assets" in hrow["hook"] or "pdf_hooks" in hrow["hook"]:
                        primary_hook_name = hrow["hook"]
                        primary_imports.append(float(hrow["import_ms"]))
                        primary_calls.append(float(hrow["call_ms"]))
                        primary_totals.append(float(hrow["total_ms"]))

            # 2. Warmup 1 RQ job on `queue-short`
            w_proc = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"BENCH_SITE={site_name}",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-",
                ],
                input=RQ_WARMUP_PY,
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            warmup_info = _extract_marker_json(w_proc.stdout, "AB_WARMUP_JSON:")

            # 3. Exact 50-job `s1-lag-probe` burst + 60.0s wait window
            b_proc = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"BENCH_SITE={site_name}",
                    "-e",
                    f"AB_BURST_JOBS={burst_jobs}",
                    "-e",
                    f"AB_DRAIN_WINDOW_SEC={drain_window_sec}",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-",
                ],
                input=RQ_BURST_ENQUEUE_AND_60S_WINDOW_PY,
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            window_info = _extract_marker_json(b_proc.stdout, "AB_WINDOW_JSON:")

            # 4. Immediate API-00 (`health_ping`) HTTP wave right at the 60.0s boundary
            api00_wave = _run_api00_health_ping_wave(base_url, site_name)

            # 5. Full 50-job completion & per-job queue lag extraction
            l_proc2 = subprocess.run(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    RQ_POST_HTTP_FULL_DRAIN_AND_LAG_PY,
                ],
                input=json.dumps(
                    {
                        "site_name": site_name,
                        "job_ids": window_info["job_ids"],
                        "t_drain0_epoch": window_info["t_drain0_epoch"],
                    }
                ),
                env=compose_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=420,
            )
            lag_raw = _extract_marker_json(l_proc2.stdout, "AB_LAG_JSON:")

            img_insp = json.loads(
                subprocess.check_output(
                    ["docker", "image", "inspect", app_image], text=True
                )
            )[0]
            last_cold = cold_runs[-1]
            after_non_pdf_mods = last_cold["sys_modules_after_non_pdf_job"]

            return {
                "label": label,
                "app_image": app_image,
                "image_id": img_insp.get("Id"),
                "repo_digests": img_insp.get("RepoDigests") or [],
                "resource_profile_key": profile_key,
                "config_spec_sha256": profile_spec["config_spec_sha256"],
                "host_precheck_load": quiet_pre,
                "queue_short_cgroup": (
                    runtime_snap.get("cgroup_container_inspection") or {}
                ).get("queue-short"),
                "runtime_config_verdict": runtime_verdict,
                "warmup_probe": warmup_info,
                "before_job_hook_cost": {
                    "registered_before_job_hook": primary_hook_name,
                    "cold_runs_count": len(cold_runs),
                    "median_import_ms": round(statistics.median(primary_imports), 3)
                    if primary_imports
                    else 0.0,
                    "median_call_ms": round(statistics.median(primary_calls), 3)
                    if primary_calls
                    else 0.0,
                    "median_total_ms": round(statistics.median(primary_totals), 3)
                    if primary_totals
                    else 0.0,
                    "raw_cold_runs": cold_runs,
                    "sys_modules_after_non_pdf_job": after_non_pdf_mods,
                    "any_pdf_engine_loaded_after_non_pdf_job": any(
                        bool(v) for v in after_non_pdf_mods.values()
                    ),
                },
                "rq_short_50_job_burst": {
                    "burst_jobs": window_info["burst_jobs"],
                    "window_budget_sec": window_info["window_budget_sec"],
                    "enqueue_50_jobs_ms": window_info["enqueue_50_jobs_ms"],
                    "peak_queue_short_depth": window_info["peak_queue_short_depth"],
                    "drain_to_zero_or_window_ms": window_info[
                        "drain_to_zero_or_window_ms"
                    ],
                    "drained_within_60s_window": window_info["drained_within_window"],
                    "jobs_finished_within_60s_window": window_info[
                        "jobs_finished_within_window"
                    ],
                    "effective_window_jobs_per_sec": window_info[
                        "effective_window_jobs_per_sec"
                    ],
                    "queue_depths_before_http_waves": window_info[
                        "queue_depths_before_http_waves"
                    ],
                    "full_50_jobs_wall_drain_ms": lag_raw["full_50_jobs_wall_drain_ms"],
                    "true_worker_throughput_jobs_per_sec": lag_raw[
                        "true_worker_throughput_jobs_per_sec"
                    ],
                    "failed_jobs": lag_raw["failed_jobs"],
                    "per_job_queue_wait_lag_ms": summarize_latencies_ms(
                        [float(x) for x in lag_raw["wait_lags_ms"]]
                    ),
                    "per_job_end_to_end_lag_ms": summarize_latencies_ms(
                        [float(x) for x in lag_raw["e2e_lags_ms"]]
                    ),
                    "per_job_worker_exec_span_ms": summarize_latencies_ms(
                        [float(x) for x in lag_raw["exec_spans_ms"]]
                    ),
                },
                "api_00_health_ping_after_60s_window": api00_wave,
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


def compare_before_after_results(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Compute controlled Before/After deltas and fail-closed acceptance checks."""
    b_hook = before["before_job_hook_cost"]
    a_hook = after["before_job_hook_cost"]
    b_rq = before["rq_short_50_job_burst"]
    a_rq = after["rq_short_50_job_burst"]
    b_qs = before.get("queue_short_cgroup") or {}
    a_qs = after.get("queue_short_cgroup") or {}

    b_imp = float(b_hook["median_import_ms"])
    a_imp = float(a_hook["median_import_ms"])
    b_call = float(b_hook["median_call_ms"])
    a_call = float(a_hook["median_call_ms"])
    b_tot = float(b_hook["median_total_ms"])
    a_tot = float(a_hook["median_total_ms"])

    b_tput = float(b_rq["true_worker_throughput_jobs_per_sec"])
    a_tput = float(a_rq["true_worker_throughput_jobs_per_sec"])
    b_e2e_p95 = float(b_rq["per_job_end_to_end_lag_ms"]["p95_ms"])
    a_e2e_p95 = float(a_rq["per_job_end_to_end_lag_ms"]["p95_ms"])
    b_wait_p95 = float(b_rq["per_job_queue_wait_lag_ms"]["p95_ms"])
    a_wait_p95 = float(a_rq["per_job_queue_wait_lag_ms"]["p95_ms"])

    a_qdepth = a_rq["queue_depths_before_http_waves"]
    b_qdepth = b_rq["queue_depths_before_http_waves"]
    b_api00_p95 = float(before["api_00_health_ping_after_60s_window"]["p95_ms"])
    a_api00_p95 = float(after["api_00_health_ping_after_60s_window"]["p95_ms"])

    checks = {
        "identical_resource_profile_and_config_sha": (
            before["resource_profile_key"] == after["resource_profile_key"]
            and before["config_spec_sha256"] == after["config_spec_sha256"]
        ),
        "identical_queue_short_0_5_vcpu_cgroup": (
            float(b_qs.get("cpus_limit") or 0.0) == 0.5
            and float(a_qs.get("cpus_limit") or 0.0) == 0.5
        ),
        "identical_benchmark_workload_50_jobs_60s_window": (
            int(b_rq["burst_jobs"]) == CANONICAL_BENCHMARK_BURST_JOBS
            and int(a_rq["burst_jobs"]) == CANONICAL_BENCHMARK_BURST_JOBS
            and float(b_rq["window_budget_sec"]) == CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC
            and float(a_rq["window_budget_sec"]) == CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC
        ),
        "before_reproduces_eager_weasyprint_import": bool(
            b_hook["sys_modules_after_non_pdf_job"].get("weasyprint") is True
        ),
        "after_eliminates_all_eager_pdf_engine_imports": (
            a_hook["any_pdf_engine_loaded_after_non_pdf_job"] is False
        ),
        "after_reduces_both_import_ms_and_call_ms": (
            a_imp < b_imp and a_call < 5.0 and a_tot < b_tot * 0.15
        ),
        "after_drains_all_50_jobs_within_60s_window": (
            a_rq["drained_within_60s_window"] is True
            and int(a_rq["jobs_finished_within_60s_window"])
            == CANONICAL_BENCHMARK_BURST_JOBS
            and int(a_rq["failed_jobs"]) == 0
        ),
        "after_zero_residual_queue_before_http_waves": (
            a_qdepth == {"short": 0, "default": 0, "long": 0}
        ),
        "after_improves_job_throughput_and_queue_lag": (
            a_tput > b_tput * 2.0 and a_e2e_p95 < b_e2e_p95 * 0.5
        ),
        "after_api_00_p95_within_200ms_slo": (
            float(after["api_00_health_ping_after_60s_window"]["p95_ms"]) <= 200.0
            and int(after["api_00_health_ping_after_60s_window"]["error_count"]) == 0
        ),
    }

    return {
        "all_checks_passed": all(checks.values()),
        "checks": checks,
        "deltas": {
            "hook_import_ms": {
                "before": b_imp,
                "after": a_imp,
                "reduction_pct": round((1.0 - (a_imp / b_imp)) * 100.0, 2)
                if b_imp > 0
                else 0.0,
            },
            "hook_call_ms": {
                "before": b_call,
                "after": a_call,
                "reduction_pct": round((1.0 - (a_call / b_call)) * 100.0, 4)
                if b_call > 0
                else 0.0,
            },
            "hook_total_ms": {
                "before": b_tot,
                "after": a_tot,
                "reduction_pct": round((1.0 - (a_tot / b_tot)) * 100.0, 2)
                if b_tot > 0
                else 0.0,
                "speedup_factor": round(b_tot / max(a_tot, 0.001), 1),
            },
            "job_throughput_jobs_per_sec": {
                "before": b_tput,
                "after": a_tput,
                "speedup_factor": round(a_tput / max(b_tput, 0.001), 2),
            },
            "per_job_queue_wait_lag_p95_ms": {
                "before": b_wait_p95,
                "after": a_wait_p95,
                "reduction_pct": round((1.0 - (a_wait_p95 / b_wait_p95)) * 100.0, 2)
                if b_wait_p95 > 0
                else 0.0,
            },
            "per_job_end_to_end_lag_p95_ms": {
                "before": b_e2e_p95,
                "after": a_e2e_p95,
                "reduction_pct": round((1.0 - (a_e2e_p95 / b_e2e_p95)) * 100.0, 2)
                if b_e2e_p95 > 0
                else 0.0,
            },
            "queue_depths_before_http_waves": {
                "before": b_qdepth,
                "after": a_qdepth,
            },
            "api_00_health_ping_p95_ms": {
                "before": b_api00_p95,
                "after": a_api00_p95,
                "slo_limit_ms": 200.0,
                "delta_ms": round(a_api00_p95 - b_api00_p95, 3),
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Controlled Before/After A/B proof for G18 lazy PDF hook remediation"
    )
    parser.add_argument("--confirm-isolated-target", action="store_true", required=True)
    parser.add_argument(
        "--before-image",
        required=True,
        help="Before candidate digest (177df99a...)",
    )
    parser.add_argument(
        "--after-image",
        required=True,
        help="After candidate digest (<D_new>)",
    )
    parser.add_argument(
        "--resource-variant",
        default="staging_vps_capped_b_plus_2_5vcpu",
        choices=tuple(sorted(RAW_G18_RESOURCE_PROFILES)),
    )
    parser.add_argument(
        "--pull-policy",
        default="never",
        choices=("never", "missing", "always"),
    )
    parser.add_argument("--port", type=int, default=18882)
    parser.add_argument("--site-name", default="p4-ab-bench.localhost")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if not args.confirm_isolated_target:
        raise SystemExit("ERROR: --confirm-isolated-target is required")

    t_start = datetime.now(timezone.utc).isoformat()
    before_res = measure_single_candidate_under_capped_profile(
        label="before",
        app_image=args.before_image.strip(),
        profile_key=args.resource_variant,
        pull_policy=args.pull_policy,
        port=args.port,
        site_name=args.site_name,
    )
    after_res = measure_single_candidate_under_capped_profile(
        label="after",
        app_image=args.after_image.strip(),
        profile_key=args.resource_variant,
        pull_policy=args.pull_policy,
        port=args.port,
        site_name=args.site_name,
    )
    comparison = compare_before_after_results(before_res, after_res)

    report = {
        "schema_version": "cskh-p4-g18-before-job-lazy-ab-v1",
        "started_at_utc": t_start,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "runner_environment": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "python_version": platform.python_version(),
        },
        "isolation_guard": {
            "owner_vps_touched": False,
            "loopback_only": True,
            "benchmark_workload_modified": False,
            "burst_jobs": CANONICAL_BENCHMARK_BURST_JOBS,
            "drain_window_sec": CANONICAL_BENCHMARK_DRAIN_WINDOW_SEC,
        },
        "before": before_res,
        "after": after_res,
        "comparison": comparison,
        "verdict": "PASS" if comparison["all_checks_passed"] else "FAIL",
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not comparison["all_checks_passed"]:
        sys.stderr.write(
            f"ERROR: Before/After A/B comparison failed checks: {json.dumps(comparison['checks'], indent=2)}\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
