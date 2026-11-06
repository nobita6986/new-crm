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
        UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
        OVERRIDES_DIR / "compose.cskh-common.yaml",
        OVERRIDES_DIR / "compose.cskh-tls.yaml",
    ),
    "isolated-smoke": (
        UPSTREAM_DIR / "compose.yaml",
        UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
        UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
        OVERRIDES_DIR / "compose.cskh-common.yaml",
        OVERRIDES_DIR / "compose.cskh-isolated-smoke.yaml",
    ),
}

SCHEDULER_CADENCE_MAX_LAG_SECONDS: dict[str, int] = {
    "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations": 600,          # 2 x 5m
    "crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes": 600,           # 2 x 5m
    "crm_cskh.jobs.run_export.expire_stale_exports": 7200,               # 2 x 60m
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

PLACEHOLDER_TOKENS = ("example.invalid", "example.com", "changeme", "replace_me", "<", ">")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact_payload_text(text: str, extra_secrets: list[str] | None = None) -> str:
    out = text or ""
    for sec in extra_secrets or []:
        if sec and len(sec) >= 4:
            out = out.replace(sec, "<REDACTED_SECRET>")
    out = re.sub(r"(\bsid=)[^;\s\"']+", r"\1<REDACTED_FRAPPE_SID>", out)
    out = re.sub(r"(https?://[^\s\"'/]+/)[^\s\"']+", r"\1<REDACTED_WEBHOOK_PATH>", out)
    out = PHONE_RE.sub("<REDACTED_PHONE>", out)
    return out


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
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "crm-cskh-p4-monitor/1.0"},
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


def _compose_cmd(env_file: Path, mode: str) -> list[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in COMPOSE_MODES[mode]:
        cmd.extend(["-f", str(cf)])
    return cmd


def _collect_container_health(env_file: Path, mode: str) -> dict[str, Any]:
    ps_proc = subprocess.run(
        _compose_cmd(env_file, mode) + ["ps", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    rows = [json.loads(line) for line in ps_proc.stdout.splitlines() if line.strip()]
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
                ["docker", "inspect", "--format", "{{.RestartCount}}|{{.State.OOMKilled}}", name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            parts = insp.stdout.strip().split("|")
            restarts = int(parts[0]) if parts and parts[0].isdigit() else 0
            oom = len(parts) > 1 and parts[1].lower() == "true"
            if restarts > 0 or oom:
                oom_or_restarts.append({"service": svc, "restart_count": restarts, "oom_killed": oom})

    missing = sorted(expected_daemons - seen_services)
    ok = not missing and not unhealthy and not oom_or_restarts and scheduler_running_count == 1
    return {
        "status": "PASS" if ok else "FAIL",
        "missing_daemon_services": missing,
        "unhealthy_services": unhealthy,
        "scheduler_running_instances": scheduler_running_count,
        "restarts_or_oom": oom_or_restarts,
    }


def _collect_backend_runtime_and_invariants(
    env_file: Path,
    mode: str,
    site_name: str,
    since_utc: str | None = None,
) -> dict[str, Any]:
    probe_script = r"""
import json, os, frappe
from datetime import datetime, timezone
from rq import Queue
from rq.registry import FailedJobRegistry
from frappe.utils.background_jobs import get_redis_conn, get_queue_name
from frappe.utils.scheduler import is_scheduler_disabled

site = os.environ["CSKH_PROBE_SITE"]
since_utc = os.environ.get("CSKH_SINCE_UTC", "").strip()
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
try:
    def scalar(sql, params=()):
        rows = frappe.db.sql(sql, params)
        return int(rows[0][0] or 0) if rows else 0

    # 1. RQ queues & failed jobs
    conn = get_redis_conn()
    queues = {}
    for qtype in ("short", "default", "long"):
        qname = get_queue_name(qtype)
        q = Queue(qname, connection=conn)
        failed_reg = FailedJobRegistry(queue=q)
        queues[qtype] = {"queued": int(q.count), "failed": int(len(failed_reg))}

    # 2. Scheduler status & Scheduled Job Log lag
    sched_disabled = bool(is_scheduler_disabled())
    job_methods = [
        "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations",
        "crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes",
        "crm_cskh.jobs.run_export.expire_stale_exports",
        "crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods",
    ]
    cron_freshness = {}
    for m in job_methods:
        rows = frappe.db.sql(
            "SELECT TIMESTAMPDIFF(SECOND, MAX(creation), NOW()) FROM `tabScheduled Job Log` WHERE scheduled_job_type LIKE %s AND status='Complete'",
            (f"%{m.split('.')[-1]}%",),
        )
        lag_sec = int(rows[0][0]) if rows and rows[0][0] is not None else None
        cron_freshness[m] = lag_sec

    error_log_15m = scalar(
        "SELECT COUNT(*) FROM `tabError Log` WHERE creation >= DATE_SUB(NOW(), INTERVAL 15 MINUTE)"
    )

    # 3. Policy flags check
    from crm_cskh.services.p3_flags import resolve_p3_flags
    flags = resolve_p3_flags()
    sla_on = 1 if flags.get("sla_enabled") else 0
    rep_on = 1 if flags.get("reporting_enabled") else 0

    # 4. Business invariants (counts only, 0 PII)
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
            "SELECT COUNT(*) FROM `tabCSKH Contact Attempt` ca WHERE ca.verification_status='verified' AND IFNULL(ca.call_log,'')='' AND NOT EXISTS (SELECT 1 FROM `tabCSKH QA Review` qr WHERE qr.work_item = ca.work_item)"
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
        "false_zero_qa_snapshots": scalar(
            "SELECT COUNT(*) FROM `tabCSKH KPI Snapshot` WHERE metrics_json LIKE '%\"QA_PASS_RATE\": {\"value\": 0%' AND metrics_json LIKE '%\"denominator\": 0%'"
        ),
        "unexpired_completed_exports_past_2h_grace": scalar(
            "SELECT COUNT(*) FROM `tabCSKH Export Job` WHERE status='completed' AND expires_at < DATE_SUB(NOW(), INTERVAL 2 HOUR)"
        ),
    }

    # 5. Optional pre-restore data-loss window delta counts (P4.md §15.3)
    delta = None
    if since_utc:
        ts = since_utc.replace("T", " ").rstrip("Z")
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
        "cron_last_complete_lag_seconds": cron_freshness,
        "error_log_last_15m": error_log_15m,
        "p3_flags": flags,
        "invariants_raw": inv_raw,
        "data_loss_window_delta": delta,
    }, sort_keys=True))
finally:
    frappe.destroy()
"""
    cmd = _compose_cmd(env_file, mode) + [
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
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE_JSON="):
            return json.loads(line.split("=", 1)[1])
    raise RuntimeError("Backend health/invariant probe did not emit PROBE_JSON")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Compose .env file for target stack")
    parser.add_argument("--mode", choices=("tls", "isolated-smoke"), default="tls")
    parser.add_argument("--site", help="Override site name (defaults to SITE_NAME in --env-file)")
    parser.add_argument("--environment-label", default="staging")
    parser.add_argument("--since-utc", help="ISO-8601 UTC timestamp to compute P4 §15.3 restore data-loss counts")
    parser.add_argument("--preflight-alert-check", action="store_true", help="Only evaluate alert channel readiness")
    parser.add_argument("--send-test-alert", action="store_true", help="Dispatch synthetic test alert for G19")
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
        report = {
            "timestamp_utc": utc_now_iso(),
            "environment": args.environment_label,
            "status": "PASS" if receipt["status"] == "DELIVERED" else "FAIL",
            "alert_envelope": env_msg,
            "receipt": receipt,
        }
        out_txt = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(out_txt, encoding="utf-8", newline="\n")
        sys.stdout.write(out_txt)
        return 0 if report["status"] == "PASS" else 2

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

    containers = _collect_container_health(args.env_file, args.mode)
    disk = evaluate_disk_thresholds(REPO_ROOT)
    backend_probe = _collect_backend_runtime_and_invariants(args.env_file, args.mode, site_name, args.since_utc)
    invariants = evaluate_business_invariants(backend_probe["invariants_raw"])

    queue_failed_total = sum(v["failed"] for v in backend_probe["queues"].values())
    cron_lags = backend_probe["cron_last_complete_lag_seconds"]
    stale_crons = [
        job
        for job, lag in cron_lags.items()
        if lag is not None and lag > SCHEDULER_CADENCE_MAX_LAG_SECONDS[job]
    ]
    queue_sched_ok = (
        not backend_probe["scheduler_disabled"]
        and queue_failed_total == 0
        and len(stale_crons) == 0
    )

    overall_ok = (
        containers["status"] == "PASS"
        and disk["level"] != "CRITICAL"
        and queue_sched_ok
        and invariants["status"] == "PASS"
    )
    report = {
        "timestamp_utc": utc_now_iso(),
        "environment": args.environment_label,
        "site": site_name,
        "status": "PASS" if overall_ok else "FAIL",
        "containers": containers,
        "disk": disk,
        "queues_and_scheduler": {
            "status": "PASS" if queue_sched_ok else "FAIL",
            "scheduler_disabled": backend_probe["scheduler_disabled"],
            "queues": backend_probe["queues"],
            "stale_cron_jobs": stale_crons,
            "cron_last_complete_lag_seconds": cron_lags,
            "error_log_last_15m": backend_probe["error_log_last_15m"],
        },
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
