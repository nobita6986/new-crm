#!/usr/bin/env python3
"""P4-07 / P4-10 Observability, Business Invariants, Data-Loss Counter & Alert Dispatcher.

Lightweight zero-agent operational probe grounded in P4-01 (`492423c`).
Designed to run via systemd timer / cron on Staging & Production without adding
persistent container memory overhead on a single VPS (`P4-ADR-08`, `P4.md §10, §15.3`).

Capabilities:
1. Technical health: Compose container health/restarts/`OOMKilled`, disk usage %,
   RQ queue depths & failed jobs, Frappe `Error Log` 15m spike, and scheduler
   heartbeat across all 4 `crm_cskh` scheduled jobs (`hooks.py`).
2. Business integrity invariants (`INV-01`..`INV-06`): zero-PII scalar SQL checks
   for single active owner, `lead_owner` sync, attempt idempotency & verification
   integrity, follow-up `CRM Task` owner sync, SLA overdue evaluation lag, KPI
   snapshot / Dirty Scope health, and Export TTL enforcement.
3. Pre-restore data-loss impact calculator (`--since-utc`): counts business records
   created/updated after a backup restore point (`P4.md §15.3`).
4. Alert hygiene & test alert dispatch (`--preflight-alert-check` / `--send-test-alert`):
   enforces owner, severity, first_action, runbook link, deduplication cooldown,
   PII/secret redaction, and reports `BLOCKED` cleanly when `BLK-GOV-06` /
   `BLK-INFRA-07` are not yet provisioned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO_ROOT / "deploy" / "compose"
UPSTREAM_DIR = COMPOSE_DIR / "upstream" / "frappe_docker"
OVERRIDES_DIR = COMPOSE_DIR / "overrides"

COMPOSE_MODES: dict[str, tuple[Path, ...]] = {
    "tls": (
        UPSTREAM_DIR / "compose.yaml",
        UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
        UPSTREAM_DIR / "overrides" / "compose.mariadb-secrets.yaml",
        UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
        UPSTREAM_DIR / "overrides" / "compose.https.yaml",
        UPSTREAM_DIR / "overrides" / "compose.migrator.yaml",
        OVERRIDES_DIR / "compose.cskh-common.yaml",
        OVERRIDES_DIR / "compose.cskh-tls.yaml",
    ),
    "isolated-smoke": (
        UPSTREAM_DIR / "compose.yaml",
        UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
        UPSTREAM_DIR / "overrides" / "compose.mariadb-secrets.yaml",
        UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
        UPSTREAM_DIR / "overrides" / "compose.proxy.yaml",
        UPSTREAM_DIR / "overrides" / "compose.migrator.yaml",
        OVERRIDES_DIR / "compose.cskh-common.yaml",
        OVERRIDES_DIR / "compose.cskh-isolated-smoke.yaml",
    ),
}

SCHEDULER_CADENCE_MAX_LAG_SECONDS: dict[str, int] = {
    "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations": 600,  # 2 x 5m
    "crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes": 600,  # 2 x 5m
    "crm_cskh.jobs.run_export.expire_stale_exports": 7200,  # 2 x 60m
    "crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods": 172800,  # 2 x 24h
}

ALERT_CATALOG: dict[str, dict[str, str]] = {
    "ALT-TECH-CONTAINER": {
        "severity": "SEV-1",
        "first_action": "Inspect unhealthy/restarting/OOM container via `docker compose ps` & `docker logs --tail 100`.",
        "runbook": "docs/p4/operations-sop.md#2-phan-loai-va-ung-pho-su-co",
    },
    "ALT-TECH-DISK": {
        "severity": "SEV-2",
        "first_action": "Check disk usage (`df -h`, `docker system df`), prune old rotated logs/temp exports, verify offsite backup space.",
        "runbook": "docs/p4/monitoring-alerts.md#1-tin-hieu-ky-thuat-va-nguong-canh-bao",
    },
    "ALT-TECH-QUEUE-SCHEDULER": {
        "severity": "SEV-2",
        "first_action": "Verify single running `scheduler` & `queue-short`/`queue-long` workers; inspect failed RQ jobs and Redis queue memory.",
        "runbook": "docs/p4/monitoring-alerts.md#2-theo-doi-queue-va-scheduler-g17",
    },
    "ALT-BIZ-INVARIANT": {
        "severity": "SEV-1",
        "first_action": "Contain write operations if ownership/PII integrity is affected; follow 6-step Data Incident SOP (never edit history via raw SQL).",
        "runbook": "docs/p4/operations-sop.md#3-quy-trinh-xu-ly-su-co-du-lieu",
    },
    "ALT-TEST-SYNTHETIC": {
        "severity": "INFO",
        "first_action": "Acknowledge test alert receipt and archive timestamp + receipt hash for Gate G19.",
        "runbook": "docs/p4/monitoring-alerts.md#5-kiem-thu-canh-bao-end-to-end-g19",
    },
}

PLACEHOLDER_TOKENS = (
    "example.invalid",
    "example.com",
    "changeme",
    "replace_me",
    "<",
    ">",
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact_payload_text(
    text: str,
    extra_secrets: list[str] | None = None,
    csrf_tokens: list[str] | None = None,
) -> str:
    out = text or ""
    for tok in csrf_tokens or []:
        if tok and tok != "None" and len(tok) >= 4:
            out = out.replace(tok, "<REDACTED_CSRF>")
    for sec in extra_secrets or []:
        if sec and len(sec) >= 4:
            out = out.replace(sec, "<REDACTED_SECRET>")
    out = re.sub(r"(\bsid=)[^;\s\"']+", r"\1<REDACTED_FRAPPE_SID>", out)
    out = re.sub(
        r'(?i)(["\']x-frappe-csrf-token["\']\s*:\s*["\'])[^"\']+(["\'])',
        r"\1<REDACTED_CSRF>\2",
        out,
    )
    out = re.sub(
        r"(?i)(\bx-frappe-csrf-token[ \t]*[:=][ \t]*)(?![\"']|<REDACTED_CSRF>)[^\s,;\"'\}|]+",
        r"\1<REDACTED_CSRF>",
        out,
    )
    out = re.sub(
        r'(?i)(["\']?csrf_token["\']?\s*[:=]\s*["\'])[^"\']+(["\'])',
        r"\1<REDACTED_CSRF>\2",
        out,
    )
    out = re.sub(r"(https?://[^\s\"'/]+/)[^\s\"']+", r"\1<REDACTED_WEBHOOK_PATH>", out)
    out = PHONE_RE.sub("<REDACTED_PHONE>", out)
    return out


def resolve_effective_invariant_flags(flags: dict[str, Any]) -> dict[str, bool]:
    """Use exact keys returned by `crm_cskh.services.p3_flags.resolve_p3_flags()` (F-P4-OPS-03)."""
    return {
        "p3_sla_enabled": bool(flags.get("p3_sla_enabled")),
        "p3_reporting_enabled": bool(flags.get("p3_reporting_enabled")),
    }


def parse_docker_started_at_uptime_seconds(
    started_at: str,
    now_utc: datetime | None = None,
) -> int | None:
    raw = (started_at or "").strip()
    if not raw or raw.startswith("0001-01-01"):
        return None
    normalized = re.sub(r"\.(\d{6})\d+", r".\1", raw)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        started_dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if started_dt.tzinfo is None:
        started_dt = started_dt.replace(tzinfo=timezone.utc)
    ref_now = now_utc or datetime.now(timezone.utc)
    return max(0, int((ref_now - started_dt).total_seconds()))


def evaluate_scheduler_and_queues(
    scheduler_disabled: bool,
    queues: dict[str, dict[str, int]],
    cron_job_details: dict[str, dict[str, Any]],
    error_log_last_15m: int,
    runtime_age_seconds: int | None,
    startup_grace_seconds: int | None = None,
) -> dict[str, Any]:
    """Enforce cron `Complete` after startup grace and fail if latest run `Failed` (F-P4-OPS-05)."""
    queue_failed_total = sum(int(v.get("failed", 0)) for v in queues.values())
    unregistered_crons: list[str] = []
    failed_latest_crons: list[str] = []
    never_completed_crons: list[str] = []
    stale_crons: list[str] = []
    in_startup_grace_crons: list[str] = []
    cron_lags: dict[str, int | None] = {}

    for job, max_lag in SCHEDULER_CADENCE_MAX_LAG_SECONDS.items():
        info = cron_job_details.get(job, {})
        registered = bool(info.get("registered", False))
        lag = info.get("last_complete_lag_seconds")
        latest_terminal = info.get("latest_terminal_status")
        cron_lags[job] = lag
        grace_sec = startup_grace_seconds if startup_grace_seconds is not None else max_lag
        past_grace = True if runtime_age_seconds is None else (runtime_age_seconds >= grace_sec)

        if not registered:
            unregistered_crons.append(job)
        if latest_terminal == "Failed":
            failed_latest_crons.append(job)
        if lag is None:
            if past_grace:
                never_completed_crons.append(job)
            else:
                in_startup_grace_crons.append(job)
        elif lag > max_lag:
            stale_crons.append(job)

    ok = (
        not scheduler_disabled
        and queue_failed_total == 0
        and not unregistered_crons
        and not failed_latest_crons
        and not never_completed_crons
        and not stale_crons
    )
    return {
        "status": "PASS" if ok else "FAIL",
        "scheduler_disabled": scheduler_disabled,
        "scheduler_uptime_seconds": runtime_age_seconds,
        "startup_grace_override_seconds": startup_grace_seconds,
        "queues": queues,
        "unregistered_cron_jobs": unregistered_crons,
        "failed_latest_cron_jobs": failed_latest_crons,
        "never_completed_cron_jobs": never_completed_crons,
        "stale_cron_jobs": stale_crons,
        "in_startup_grace_cron_jobs": in_startup_grace_crons,
        "cron_last_complete_lag_seconds": cron_lags,
        "cron_job_details": cron_job_details,
        "error_log_last_15m": error_log_last_15m,
    }


def evaluate_disk_thresholds(path: Path, warn_pct: float = 80.0, crit_pct: float = 90.0) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    used_pct = round((usage.used / usage.total) * 100.0, 2) if usage.total else 0.0
    level = "OK"
    if used_pct >= crit_pct:
        level = "CRITICAL"
    elif used_pct >= warn_pct:
        level = "WARNING"
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": used_pct,
        "warn_threshold_pct": warn_pct,
        "critical_threshold_pct": crit_pct,
        "level": level,
    }


def evaluate_business_invariants(raw_metrics: dict[str, int]) -> dict[str, Any]:
    checks = {
        "INV-01_single_active_owner_and_sync": {
            "duplicate_active_leads": raw_metrics.get("duplicate_active_leads", 0),
            "owner_mismatch_active_work_items": raw_metrics.get("owner_mismatch_active_work_items", 0),
            "active_claim_count_mismatch": raw_metrics.get("active_claim_count_mismatch", 0),
        },
        "INV-02_attempt_idempotency_and_verification": {
            "duplicate_attempt_idempotency_keys": raw_metrics.get("duplicate_attempt_idempotency_keys", 0),
            "unverified_marked_verified": raw_metrics.get("unverified_marked_verified", 0),
        },
        "INV-03_followup_callback_task_integrity": {
            "orphan_or_misassigned_open_followup_tasks": raw_metrics.get(
                "orphan_or_misassigned_open_followup_tasks", 0
            ),
        },
        "INV-04_sla_engine_integrity": {
            "duplicate_sla_idempotency_keys": raw_metrics.get("duplicate_sla_idempotency_keys", 0),
            "stale_unevaluated_overdue_sla": raw_metrics.get("stale_unevaluated_overdue_sla", 0),
        },
        "INV-05_kpi_snapshot_and_dirty_scope_integrity": {
            "stuck_dirty_scopes_over_30m": raw_metrics.get("stuck_dirty_scopes_over_30m", 0),
            "false_zero_qa_snapshots": raw_metrics.get("false_zero_qa_snapshots", 0),
        },
        "INV-06_export_ttl_enforcement": {
            "unexpired_completed_exports_past_2h_grace": raw_metrics.get(
                "unexpired_completed_exports_past_2h_grace", 0
            ),
        },
    }
    failed_invariants: list[str] = []
    for inv_name, metrics in checks.items():
        if any(int(v) != 0 for v in metrics.values()):
            failed_invariants.append(inv_name)
    return {
        "status": "PASS" if not failed_invariants else "FAIL",
        "failed_invariants": failed_invariants,
        "details": checks,
    }


def evaluate_alert_preflight(operator_contact: str, webhook_url: str) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    op_clean = (operator_contact or "").strip()
    wh_clean = (webhook_url or "").strip()

    if not op_clean or any(tok in op_clean.lower() for tok in PLACEHOLDER_TOKENS):
        blockers.append(
            {
                "id": "BLK-GOV-06",
                "field": "CSKH_OPERATOR_CONTACT",
                "reason": "Named Day-2 Operator / on-call recipient has not been assigned and signed off by Owner.",
            }
        )
    if (
        not wh_clean
        or not wh_clean.startswith("https://")
        or any(tok in wh_clean.lower() for tok in PLACEHOLDER_TOKENS)
    ):
        blockers.append(
            {
                "id": "BLK-INFRA-07",
                "field": "CSKH_ALERT_WEBHOOK_URL",
                "reason": "Approved HTTPS alert delivery endpoint (Webhook / ChatOps / Pager) has not been provisioned.",
            }
        )
    return {
        "timestamp_utc": utc_now_iso(),
        "status": "READY" if not blockers else "BLOCKED",
        "operator_contact_configured": len([b for b in blockers if b["id"] == "BLK-GOV-06"]) == 0,
        "webhook_https_configured": len([b for b in blockers if b["id"] == "BLK-INFRA-07"]) == 0,
        "blockers": blockers,
    }


def build_alert_envelope(
    alert_code: str,
    environment: str,
    operator_contact: str,
    summary: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    meta = ALERT_CATALOG.get(alert_code, ALERT_CATALOG["ALT-BIZ-INVARIANT"])
    clean_summary = redact_payload_text(summary)
    envelope = {
        "alert_code": alert_code,
        "severity": meta["severity"],
        "environment": environment,
        "owner": operator_contact,
        "timestamp_utc": utc_now_iso(),
        "summary": clean_summary,
        "first_action": meta["first_action"],
        "runbook": meta["runbook"],
        "metrics": metrics,
    }
    envelope["dedup_key"] = f"{environment}:{alert_code}"
    return envelope


def dispatch_alert_with_cooldown(
    envelope: dict[str, Any],
    webhook_url: str,
    dedup_state_file: Path | None = None,
    cooldown_seconds: int = 900,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now_ts = time.time() if now_epoch is None else now_epoch
    state: dict[str, float] = {}
    if dedup_state_file and dedup_state_file.is_file():
        try:
            state = json.loads(dedup_state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    dedup_key = envelope["dedup_key"]
    last_sent = float(state.get(dedup_key, 0.0))
    if last_sent and (now_ts - last_sent) < cooldown_seconds:
        return {
            "status": "SUPPRESSED_COOLDOWN",
            "dedup_key": dedup_key,
            "seconds_until_next_allowed": int(cooldown_seconds - (now_ts - last_sent)),
        }

    payload_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    req = urllib.request.Request(
        webhook_url,
        data=payload_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "crm-cskh-p4-monitor/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            http_status = resp.status
    except urllib.error.HTTPError as exc:
        http_status = exc.code

    delivered = 200 <= http_status < 300
    if delivered and dedup_state_file:
        dedup_state_file.parent.mkdir(parents=True, exist_ok=True)
        state[dedup_key] = now_ts
        dedup_state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8", newline="\n")

    return {
        "status": "DELIVERED" if delivered else "FAILED",
        "http_status": http_status,
        "dedup_key": dedup_key,
        "payload_sha256": payload_sha256,
        "webhook_redacted": redact_payload_text(webhook_url),
        "timestamp_utc": utc_now_iso(),
    }


def _compose_cmd(env_file: Path, mode: str, extra_compose_files: tuple[Path, ...] = ()) -> list[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in (*COMPOSE_MODES[mode], *extra_compose_files):
        if not cf.is_file():
            raise ValueError(f"Compose file does not exist: {cf}")
        cmd.extend(["-f", str(cf)])
    return cmd


def _collect_container_health(env_file: Path, mode: str, extra_compose_files: tuple[Path, ...] = ()) -> dict[str, Any]:
    ps_proc = subprocess.run(
        _compose_cmd(env_file, mode, extra_compose_files) + ["ps", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    raw_ps = (ps_proc.stdout or "").strip()
    rows = (
        json.loads(raw_ps)
        if raw_ps.startswith("[")
        else [json.loads(line) for line in raw_ps.splitlines() if line.strip()]
    )
    expected_daemons = {
        "backend",
        "db",
        "frontend",
        "proxy",
        "queue-long",
        "queue-short",
        "redis-cache",
        "redis-queue",
        "scheduler",
        "websocket",
    }
    seen_services: set[str] = set()
    scheduler_running_count = 0
    scheduler_uptime_seconds: int | None = None
    unhealthy: list[str] = []
    oom_or_restarts: list[dict[str, Any]] = []

    for row in rows:
        svc = row.get("Service", "")
        state = row.get("State", "")
        health = row.get("Health", "")
        name = row.get("Name", "")
        seen_services.add(svc)
        if svc == "scheduler" and state == "running":
            scheduler_running_count += 1
        if svc in expected_daemons and (state != "running" or health != "healthy"):
            unhealthy.append(f"{svc}:{state}/{health}")
        if name:
            insp = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.RestartCount}}|{{.State.OOMKilled}}|{{.State.StartedAt}}",
                    name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            parts = insp.stdout.strip().split("|")
            restarts = int(parts[0]) if parts and parts[0].isdigit() else 0
            oom = len(parts) > 1 and parts[1].lower() == "true"
            started_at = parts[2] if len(parts) > 2 else ""
            if svc == "scheduler" and state == "running":
                scheduler_uptime_seconds = parse_docker_started_at_uptime_seconds(started_at)
            if restarts > 0 or oom:
                oom_or_restarts.append({"service": svc, "restart_count": restarts, "oom_killed": oom})

    missing = sorted(expected_daemons - seen_services)
    ok = not missing and not unhealthy and not oom_or_restarts and scheduler_running_count == 1
    return {
        "status": "PASS" if ok else "FAIL",
        "missing_daemon_services": missing,
        "unhealthy_services": unhealthy,
        "scheduler_running_instances": scheduler_running_count,
        "scheduler_uptime_seconds": scheduler_uptime_seconds,
        "restarts_or_oom": oom_or_restarts,
    }


def _collect_backend_runtime_and_invariants(
    env_file: Path,
    mode: str,
    site_name: str,
    since_utc: str | None = None,
    extra_compose_files: tuple[Path, ...] = (),
) -> dict[str, Any]:
    probe_script = r"""
import json, os, frappe
from datetime import datetime, timezone
from rq.registry import FailedJobRegistry
from frappe.utils.background_jobs import get_queue
from frappe.utils.scheduler import is_scheduler_disabled

site = os.environ["CSKH_PROBE_SITE"]
since_utc = os.environ.get("CSKH_SINCE_UTC", "").strip()
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    def scalar(sql, params=None):
        rows = frappe.db.sql(sql) if params is None else frappe.db.sql(sql, params)
        return int(rows[0][0] or 0) if rows else 0

    # 1. RQ queues & failed jobs
    queues = {}
    for qtype in ("short", "default", "long"):
        q = get_queue(qtype, is_async=True)
        failed_reg = FailedJobRegistry(queue=q)
        queues[qtype] = {"queued": int(q.count), "failed": int(len(failed_reg))}

    # 2. Scheduler status & Scheduled Job Log lag + latest terminal status (F-P4-OPS-05)
    sched_disabled = bool(is_scheduler_disabled())
    job_methods = [
        "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations",
        "crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes",
        "crm_cskh.jobs.run_export.expire_stale_exports",
        "crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods",
    ]
    cron_job_details = {}
    for m in job_methods:
        sjt_name = frappe.db.get_value("Scheduled Job Type", {"method": m}, "name")
        if sjt_name:
            lag_rows = frappe.db.sql(
                "SELECT TIMESTAMPDIFF(SECOND, MAX(creation), NOW()) FROM `tabScheduled Job Log` "
                "WHERE scheduled_job_type = %s AND status = 'Complete'",
                (sjt_name,),
            )
            lag_sec = int(lag_rows[0][0]) if lag_rows and lag_rows[0][0] is not None else None
            term_rows = frappe.db.sql(
                "SELECT status, TIMESTAMPDIFF(SECOND, creation, NOW()) FROM `tabScheduled Job Log` "
                "WHERE scheduled_job_type = %s AND status IN ('Complete', 'Failed') "
                "ORDER BY creation DESC, modified DESC, name DESC LIMIT 1",
                (sjt_name,),
            )
            term_status = str(term_rows[0][0]) if term_rows else None
            term_lag = int(term_rows[0][1]) if term_rows and term_rows[0][1] is not None else None
            any_rows = frappe.db.sql(
                "SELECT status FROM `tabScheduled Job Log` "
                "WHERE scheduled_job_type = %s "
                "ORDER BY creation DESC, modified DESC, name DESC LIMIT 1",
                (sjt_name,),
            )
            any_status = str(any_rows[0][0]) if any_rows else None
        else:
            lag_sec = None
            term_status = None
            term_lag = None
            any_status = None
        cron_job_details[m] = {
            "registered": bool(sjt_name),
            "scheduled_job_type": sjt_name,
            "last_complete_lag_seconds": lag_sec,
            "latest_terminal_status": term_status,
            "latest_terminal_lag_seconds": term_lag,
            "latest_any_status": any_status,
        }

    error_log_15m = scalar(
        "SELECT COUNT(*) FROM `tabError Log` WHERE creation >= DATE_SUB(NOW(), INTERVAL 15 MINUTE)"
    )

    # 3. Policy flags check — exact keys from `resolve_p3_flags()` (F-P4-OPS-03)
    from crm_cskh.services.p3_flags import resolve_p3_flags
    flags = resolve_p3_flags()
    sla_on = 1 if flags.get("p3_sla_enabled") else 0
    rep_on = 1 if flags.get("p3_reporting_enabled") else 0

    # 4. Structured JSON audit of `QA_PASS_RATE` on `tabCSKH KPI Snapshot` (F-P4-OPS-04)
    snap_rows = frappe.db.sql("SELECT metrics_json FROM `tabCSKH KPI Snapshot`", as_list=True)
    qa_zero_denom_null = 0
    false_zero_qa = 0
    for r in snap_rows:
        raw = r[0] if r else None
        if not raw or not isinstance(raw, str):
            false_zero_qa += 1
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            false_zero_qa += 1
            continue
        if not isinstance(parsed, dict):
            false_zero_qa += 1
            continue
        m_map = parsed.get("metrics") if isinstance(parsed.get("metrics"), dict) else parsed
        qa = m_map.get("QA_PASS_RATE") if isinstance(m_map, dict) else None
        if not isinstance(qa, dict) or "denominator" not in qa or "value" not in qa:
            false_zero_qa += 1
            continue
        denom = qa.get("denominator")
        val = qa.get("value")
        if denom is None or isinstance(denom, bool) or not isinstance(denom, (int, float)) or denom < 0:
            false_zero_qa += 1
            continue
        if denom == 0:
            if val is None:
                qa_zero_denom_null += 1
            else:
                false_zero_qa += 1
        elif val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
            false_zero_qa += 1

    # 5. Business invariants (counts only, 0 PII)
    active_wi = scalar("SELECT COUNT(*) FROM `tabCSKH Work Item` WHERE status IN ('assigned','in_progress','on_hold')")
    active_claims = scalar("SELECT COUNT(*) FROM `tabCSKH Active Claim`")
    inv_raw = {
        "duplicate_active_leads": scalar(
            "SELECT COUNT(*) FROM (SELECT lead FROM `tabCSKH Work Item` WHERE status IN ('assigned','in_progress','on_hold') GROUP BY lead HAVING COUNT(*) > 1) t"
        ),
        "owner_mismatch_active_work_items": scalar(
            "SELECT COUNT(*) FROM `tabCSKH Work Item` wi JOIN `tabCRM Lead` l ON l.name = wi.lead WHERE wi.status IN ('assigned','in_progress','on_hold') AND IFNULL(wi.assigned_to,'') != IFNULL(l.lead_owner,'')"
        ),
        "active_claim_count_mismatch": abs(active_wi - active_claims),
        "duplicate_attempt_idempotency_keys": scalar(
            "SELECT COUNT(*) FROM (SELECT work_item, performed_by, idempotency_key FROM `tabCSKH Contact Attempt` WHERE IFNULL(idempotency_key,'') != '' GROUP BY work_item, performed_by, idempotency_key HAVING COUNT(*) > 1) t"
        ),
        "unverified_marked_verified": scalar(
            "SELECT COUNT(*) FROM `tabCSKH Contact Attempt` ca "
            "WHERE ca.verification_status='verified' AND IFNULL(ca.call_log,'')='' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM `tabCSKH QA Review` qr "
            "WHERE qr.contact_attempt = ca.name AND qr.status = 'published'"
            ")"
        ),
        "orphan_or_misassigned_open_followup_tasks": scalar(
            "SELECT COUNT(*) FROM `tabCRM Task` t LEFT JOIN `tabCSKH Work Item` wi ON wi.name = t.cskh_work_item WHERE IFNULL(t.cskh_is_followup,0)=1 AND t.status IN ('Backlog','Todo','In Progress') AND (wi.name IS NULL OR wi.status NOT IN ('assigned','in_progress','on_hold') OR (IFNULL(t.assigned_to,'') != '' AND IFNULL(t.assigned_to,'') != IFNULL(wi.assigned_to,'')))"
        ),
        "duplicate_sla_idempotency_keys": scalar(
            "SELECT COUNT(*) FROM (SELECT idempotency_key FROM `tabCSKH SLA Obligation` WHERE IFNULL(idempotency_key,'') != '' GROUP BY idempotency_key HAVING COUNT(*) > 1) t"
        ),
        "stale_unevaluated_overdue_sla": (
            scalar(
                "SELECT COUNT(*) FROM `tabCSKH SLA Obligation` WHERE status='open' AND IFNULL(is_paused,0)=0 AND effective_due_at < DATE_SUB(NOW(), INTERVAL 15 MINUTE)"
            )
            if sla_on
            else 0
        ),
        "stuck_dirty_scopes_over_30m": (
            scalar(
                "SELECT COUNT(*) FROM `tabCSKH Dirty Scope` WHERE status IN ('pending','processing') AND first_dirtied_at < DATE_SUB(NOW(), INTERVAL 30 MINUTE)"
            )
            if rep_on
            else 0
        ),
        "false_zero_qa_snapshots": false_zero_qa,
        "unexpired_completed_exports_past_2h_grace": scalar(
            "SELECT COUNT(*) FROM `tabCSKH Export Job` WHERE status='completed' AND expires_at < DATE_SUB(NOW(), INTERVAL 2 HOUR)"
        ),
    }

    # 6. Optional pre-restore data-loss window delta counts (P4.md §15.3)
    delta = None
    if since_utc:
        from zoneinfo import ZoneInfo
        from frappe.utils import get_system_timezone
        parsed_utc = datetime.fromisoformat(since_utc.replace("Z", "+00:00"))
        if parsed_utc.tzinfo is None:
            parsed_utc = parsed_utc.replace(tzinfo=timezone.utc)
        local_dt = parsed_utc.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
        ts = local_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        delta = {
            "since_utc": since_utc,
            "new_leads": scalar("SELECT COUNT(*) FROM `tabCRM Lead` WHERE creation > %s", (ts,)),
            "new_or_updated_work_items": scalar("SELECT COUNT(*) FROM `tabCSKH Work Item` WHERE modified > %s", (ts,)),
            "new_assignment_events": scalar("SELECT COUNT(*) FROM `tabCSKH Assignment History` WHERE creation > %s", (ts,)),
            "new_contact_attempts": scalar("SELECT COUNT(*) FROM `tabCSKH Contact Attempt` WHERE creation > %s", (ts,)),
            "new_or_updated_followup_tasks": scalar("SELECT COUNT(*) FROM `tabCRM Task` WHERE IFNULL(cskh_is_followup,0)=1 AND modified > %s", (ts,)),
            "new_import_batches": scalar("SELECT COUNT(*) FROM `tabCSKH Import Batch` WHERE creation > %s", (ts,)),
            "new_sla_breaches": scalar("SELECT COUNT(*) FROM `tabCSKH SLA Obligation` WHERE breached_at > %s", (ts,)),
            "new_published_kpi_runs": scalar("SELECT COUNT(*) FROM `tabCSKH KPI Run` WHERE completed_at > %s AND status='completed'", (ts,)),
        }

    print("PROBE_JSON=" + json.dumps({
        "queues": queues,
        "scheduler_disabled": sched_disabled,
        "cron_job_details": cron_job_details,
        "error_log_last_15m": error_log_15m,
        "p3_flags": flags,
        "qa_snapshot_audit": {
            "total_snapshots": len(snap_rows),
            "qa_zero_denom_null_snapshots": qa_zero_denom_null,
            "false_zero_qa_snapshots": false_zero_qa,
        },
        "invariants_raw": inv_raw,
        "data_loss_window_delta": delta,
    }, sort_keys=True))
finally:
    frappe.destroy()
"""
    cmd = _compose_cmd(env_file, mode, extra_compose_files) + [
        "exec",
        "-T",
        "-e",
        f"CSKH_PROBE_SITE={site_name}",
        "-e",
        f"CSKH_SINCE_UTC={since_utc or ''}",
        "backend",
        "/home/frappe/frappe-bench/env/bin/python",
        "-c",
        probe_script,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Backend health/invariant probe failed (rc={proc.returncode}): {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE_JSON="):
            return json.loads(line.split("=", 1)[1])
    raise RuntimeError("Backend health/invariant probe did not emit PROBE_JSON")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Compose .env file for target stack")
    parser.add_argument("--mode", choices=("tls", "isolated-smoke"), default="tls")
    parser.add_argument(
        "--extra-compose-file",
        type=Path,
        action="append",
        default=[],
        help="Append an existing deployment overlay, in order (repeatable; read-only inspection)",
    )
    parser.add_argument("--site", help="Override site name (defaults to SITE_NAME in --env-file)")
    parser.add_argument("--environment-label", default="staging")
    parser.add_argument(
        "--startup-grace-seconds",
        type=int,
        default=None,
        help="Override startup grace window (seconds) before requiring at least one Complete cron run",
    )
    parser.add_argument(
        "--since-utc",
        help="ISO-8601 UTC timestamp to compute P4 §15.3 restore data-loss counts",
    )
    parser.add_argument(
        "--preflight-alert-check",
        action="store_true",
        help="Only evaluate alert channel readiness",
    )
    parser.add_argument(
        "--send-test-alert",
        action="store_true",
        help="Dispatch synthetic test alert for G19",
    )
    parser.add_argument("--operator-contact", default=os.environ.get("CSKH_OPERATOR_CONTACT", ""))
    parser.add_argument("--webhook-url", default=os.environ.get("CSKH_ALERT_WEBHOOK_URL", ""))
    parser.add_argument("--dedup-state-file", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    alert_preflight = evaluate_alert_preflight(args.operator_contact, args.webhook_url)

    if args.preflight_alert_check:
        report = {
            "timestamp_utc": utc_now_iso(),
            "environment": args.environment_label,
            "mode": "preflight_alert_check",
            "status": alert_preflight["status"],
            "alert_preflight": alert_preflight,
            "disk": evaluate_disk_thresholds(REPO_ROOT),
        }
        out_txt = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(out_txt, encoding="utf-8", newline="\n")
        sys.stdout.write(out_txt)
        return 0

    if args.send_test_alert and not args.env_file:
        if alert_preflight["status"] != "READY":
            report = {
                "timestamp_utc": utc_now_iso(),
                "environment": args.environment_label,
                "status": "BLOCKED",
                "alert_preflight": alert_preflight,
            }
            out_txt = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(out_txt, encoding="utf-8", newline="\n")
            sys.stdout.write(out_txt)
            return 2
        env_msg = build_alert_envelope(
            "ALT-TEST-SYNTHETIC",
            args.environment_label,
            args.operator_contact,
            "Synthetic P4-07 G19 test alert verification (no PII, no secrets).",
            {"test_alert": True},
        )
        receipt = dispatch_alert_with_cooldown(env_msg, args.webhook_url, args.dedup_state_file)
        ok_status = receipt["status"] in ("DELIVERED", "SUPPRESSED_COOLDOWN")
        report = {
            "timestamp_utc": utc_now_iso(),
            "environment": args.environment_label,
            "status": "PASS" if ok_status else "FAIL",
            "alert_envelope": env_msg,
            "receipt": receipt,
        }
        out_txt = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(out_txt, encoding="utf-8", newline="\n")
        sys.stdout.write(out_txt)
        return 0 if ok_status else 2

    if not args.env_file or not args.env_file.is_file():
        raise SystemExit("--env-file is required for live stack health and invariant inspection")

    # Parse SITE_NAME if not explicitly given
    site_name = args.site
    if not site_name:
        for raw_line in args.env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("SITE_NAME="):
                site_name = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not site_name:
        raise SystemExit("SITE_NAME could not be resolved from --site or --env-file")

    extra_compose_files = tuple(p.resolve(strict=True) for p in args.extra_compose_file)
    containers = _collect_container_health(args.env_file, args.mode, extra_compose_files)
    disk = evaluate_disk_thresholds(REPO_ROOT)
    backend_probe = _collect_backend_runtime_and_invariants(
        args.env_file, args.mode, site_name, args.since_utc, extra_compose_files
    )
    invariants = evaluate_business_invariants(backend_probe["invariants_raw"])
    invariants["effective_p3_flags"] = resolve_effective_invariant_flags(backend_probe["p3_flags"])
    invariants["qa_snapshot_audit"] = backend_probe["qa_snapshot_audit"]

    queues_and_scheduler = evaluate_scheduler_and_queues(
        scheduler_disabled=backend_probe["scheduler_disabled"],
        queues=backend_probe["queues"],
        cron_job_details=backend_probe["cron_job_details"],
        error_log_last_15m=backend_probe["error_log_last_15m"],
        runtime_age_seconds=containers.get("scheduler_uptime_seconds"),
        startup_grace_seconds=args.startup_grace_seconds,
    )

    overall_ok = (
        containers["status"] == "PASS"
        and disk["level"] != "CRITICAL"
        and queues_and_scheduler["status"] == "PASS"
        and invariants["status"] == "PASS"
    )
    report = {
        "timestamp_utc": utc_now_iso(),
        "environment": args.environment_label,
        "site": site_name,
        "extra_compose_files": [str(p) for p in extra_compose_files],
        "status": "PASS" if overall_ok else "FAIL",
        "containers": containers,
        "disk": disk,
        "queues_and_scheduler": queues_and_scheduler,
        "business_invariants": invariants,
        "data_loss_window_delta": backend_probe["data_loss_window_delta"],
        "alert_preflight": alert_preflight,
    }
    out_txt = redact_payload_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_txt, encoding="utf-8", newline="\n")
    sys.stdout.write(out_txt)
    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
