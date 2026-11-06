#!/usr/bin/env python3
"""P4-03 / P4-10 Staging & Production HTTPS Smoke + Migration Reconciliation Tool.

Grounded in P4-01 baseline `492423c` (`deploy/compose/*` and `deploy/images/*`).
Provides four fail-closed subcommands without exposing secrets or PII:

1. `preflight`: Validates target `.env` file, pinned `@sha256` registry digest,
   non-placeholder FQDN/ACME email, host `db_root_password.txt` (`0600` on POSIX,
   LF-only bytes, no CR), and `encryption_key` offsite escrow attestation.
   Emits `"status": "PREFLIGHT_READY"` or `"status": "BLOCKED"` with explicit blocker IDs.
2. `https-smoke`: Verifies container digest parity (`docker inspect` vs `APP_IMAGE`),
   HTTP -> HTTPS redirect, TLS certificate chain & expiry days, authenticated login,
   `Set-Cookie: sid` (`Secure=true`, `HttpOnly=true`, `SameSite=Lax`, value redacted),
   CSRF reject/accept, `wss://` RFC 6455 WebSocket `101 Switching Protocols` + frame
   exchange, and RQ worker / scheduler liveness.
3. `snapshot-counts`: Captures zero-PII row counts, invariant counters, and
   `BLG-P4-MIG-01` `tabDocType.module` binary distribution before/after migration.
4. `compare-counts`: Reconciles pre-migration (`N-1`), post-migrate #1, and
   post-migrate #2 (`idempotency`) snapshots against P4 §6.2 invariants.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
TLS_COMPOSE_FILES = COMPOSE_MODES["tls"]

REGISTRY_DIGEST_RE = re.compile(r"^[a-z0-9._-]+(?:\.[a-z0-9._-]+|:[0-9]+)+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
PLACEHOLDER_TOKENS = ("example.invalid", "example.com", "changeme", "replace_me", "<", ">")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")
APP_SERVICES: tuple[str, ...] = (
    "backend",
    "frontend",
    "websocket",
    "queue-short",
    "queue-long",
    "scheduler",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact_sensitive(
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
    out = PHONE_RE.sub("<REDACTED_PHONE>", out)
    return out


def extract_csrf_token(html: str) -> str:
    """Extract live session CSRF token from CSKH `X-Frappe-CSRF-Token` header map or boot script."""
    src = html or ""
    invalid_literals = {"", "none", "null", "undefined", "x-frappe-csrf-token"}
    for pattern in (
        r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)["\']',
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
        r'csrf_token\s*[:=]\s*["\']([^"\']+)["\']',
    ):
        for match in re.finditer(pattern, src, flags=re.IGNORECASE):
            candidate = match.group(1).strip()
            if candidate.lower() not in invalid_literals and len(candidate) >= 8:
                return candidate
    return ""


def has_vi_dashboard_heading(html: str) -> bool:
    """Require an explicit Vietnamese `<h1>` heading on `/cskh/dashboard` (F-P4-OPS-05)."""
    for match in re.finditer(r"<h1\b[^>]*>(.*?)</h1>", html or "", flags=re.IGNORECASE | re.DOTALL):
        heading_text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if "Bảng điều khiển" in heading_text or "Tổng quan" in heading_text:
            return True
    return False


def evaluate_vi_ui_and_csrf_check(
    dash_code: int,
    dash_html: str,
    csrf_token: str,
    bad_csrf_code: int,
    good_csrf_code: int,
    good_csrf_bytes: bytes,
) -> dict[str, Any]:
    contains_vi_heading = has_vi_dashboard_heading(dash_html)
    csrf_extracted = bool(csrf_token and csrf_token.lower() != "none" and len(csrf_token) >= 8)
    health_ping_ok = b'"crm_cskh"' in (good_csrf_bytes or b"")
    passed = (
        dash_code == 200
        and contains_vi_heading
        and csrf_extracted
        and bad_csrf_code in (400, 403)
        and good_csrf_code == 200
        and health_ping_ok
    )
    return {
        "dashboard_http_status": dash_code,
        "contains_vi_heading": contains_vi_heading,
        "csrf_token_extracted": csrf_extracted,
        "x_frappe_csrf_token": "<REDACTED_CSRF>" if csrf_extracted else None,
        "forged_csrf_http_status": bad_csrf_code,
        "valid_csrf_http_status": good_csrf_code,
        "health_ping_ok": health_ping_ok,
        "pass": passed,
    }


def audit_kpi_snapshot_qa_metrics(metrics_json_rows: list[str | None]) -> dict[str, int]:
    """Parse `metrics_json` structurally and verify `QA_PASS_RATE` is `null` when `denominator == 0`."""
    total = len(metrics_json_rows)
    zero_denom_null = 0
    positive_denom_numeric = 0
    invalid_or_false_zero = 0

    for raw in metrics_json_rows:
        if not raw or not isinstance(raw, str):
            invalid_or_false_zero += 1
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            invalid_or_false_zero += 1
            continue
        if not isinstance(parsed, dict):
            invalid_or_false_zero += 1
            continue
        metrics_map = parsed.get("metrics") if isinstance(parsed.get("metrics"), dict) else parsed
        qa = metrics_map.get("QA_PASS_RATE") if isinstance(metrics_map, dict) else None
        if not isinstance(qa, dict) or "denominator" not in qa or "value" not in qa:
            invalid_or_false_zero += 1
            continue
        denom = qa.get("denominator")
        val = qa.get("value")
        if denom is None or isinstance(denom, bool) or not isinstance(denom, (int, float)) or denom < 0:
            invalid_or_false_zero += 1
            continue
        if denom == 0:
            if val is None:
                zero_denom_null += 1
            else:
                invalid_or_false_zero += 1
        else:
            if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                invalid_or_false_zero += 1
            else:
                positive_denom_numeric += 1

    return {
        "total_snapshots": total,
        "qa_zero_denom_null_snapshots": zero_denom_null,
        "qa_positive_denom_numeric_snapshots": positive_denom_numeric,
        "false_zero_qa_snapshots": invalid_or_false_zero,
    }


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        env[key.strip()] = val
    return env


def inspect_secret_file_bytes(secret_path: Path, enforce_posix_mode: bool = False) -> dict[str, Any]:
    if not secret_path.is_file():
        return {
            "exists": False,
            "ends_with_lf_only": False,
            "contains_cr": False,
            "non_empty": False,
            "mode_ok": False,
            "byte_length": 0,
        }
    raw = secret_path.read_bytes()
    contains_cr = b"\r" in raw
    ends_with_lf_only = raw.endswith(b"\n") and not raw.endswith(b"\r\n") and not contains_cr
    stripped = raw.rstrip(b"\r\n")
    mode_octal = oct(stat.S_IMODE(secret_path.stat().st_mode))
    mode_ok = True
    if enforce_posix_mode and os.name == "posix":
        mode_ok = (stat.S_IMODE(secret_path.stat().st_mode) & 0o077) == 0
    return {
        "exists": True,
        "ends_with_lf_only": ends_with_lf_only,
        "contains_cr": contains_cr,
        "non_empty": len(stripped) >= 16,
        "mode_octal": mode_octal,
        "mode_ok": mode_ok,
        "byte_length": len(raw),
    }


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def evaluate_preflight(
    env_path: Path,
    require_escrow_flag: bool = True,
    escrow_confirmed: bool = False,
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    if not env_path.is_file():
        return {
            "timestamp_utc": utc_now_iso(),
            "env_file": _display_path(env_path),
            "status": "BLOCKED",
            "blockers": [
                {
                    "id": "BLK-INFRA-01",
                    "field": "env_file",
                    "reason": f"Missing environment configuration file: {_display_path(env_path)}",
                }
            ],
            "checks": {},
        }

    env = parse_env_file(env_path)
    site_name = env.get("SITE_NAME", "").strip()
    acme_email = env.get("LETSENCRYPT_EMAIL", "").strip()
    app_image = env.get("APP_IMAGE", "").strip()
    pull_policy = env.get("PULL_POLICY", "").strip()
    secret_file_str = env.get("DB_PASSWORD_SECRETS_FILE", "").strip()
    project_name = env.get("COMPOSE_PROJECT_NAME", "").strip()

    # 1. Domain / DNS / ACME check (BLK-INFRA-02)
    domain_placeholder = (
        not site_name
        or site_name.endswith(".localhost")
        or any(tok in site_name.lower() for tok in PLACEHOLDER_TOKENS)
        or "." not in site_name
    )
    email_placeholder = (
        not acme_email
        or "@" not in acme_email
        or any(tok in acme_email.lower() for tok in PLACEHOLDER_TOKENS)
    )
    checks["dns_tls"] = {
        "site_name": site_name,
        "acme_email_configured": not email_placeholder,
        "domain_valid_fqdn": not domain_placeholder,
    }
    if domain_placeholder or email_placeholder:
        blockers.append(
            {
                "id": "BLK-INFRA-02",
                "field": "SITE_NAME / LETSENCRYPT_EMAIL",
                "reason": "Staging/Production requires a real approved FQDN and non-placeholder ACME email.",
            }
        )

    # 2. Registry digest check (BLK-INFRA-03)
    digest_valid = bool(REGISTRY_DIGEST_RE.match(app_image))
    checks["registry_digest"] = {
        "app_image": app_image,
        "matches_remote_repo_sha256": digest_valid,
        "pull_policy": pull_policy,
    }
    if not digest_valid or any(tok in app_image.lower() for tok in PLACEHOLDER_TOKENS):
        blockers.append(
            {
                "id": "BLK-INFRA-03",
                "field": "APP_IMAGE",
                "reason": "APP_IMAGE must be a real approved remote registry digest (<registry>/<repo>@sha256:<64hex>).",
            }
        )

    # 3. Host DB secret file & encryption_key escrow check (BLK-INFRA-04)
    secret_path = (env_path.parent / secret_file_str).resolve() if secret_file_str and not Path(secret_file_str).is_absolute() else Path(secret_file_str or "/nonexistent")
    sec_info = inspect_secret_file_bytes(secret_path, enforce_posix_mode=True)
    checks["secrets"] = {
        "project_name": project_name,
        "db_password_file_configured": bool(secret_file_str),
        "db_password_file_audit": sec_info,
        "encryption_key_escrow_confirmed": bool(escrow_confirmed) if require_escrow_flag else True,
    }
    if not sec_info["exists"] or not sec_info["ends_with_lf_only"] or not sec_info["non_empty"] or not sec_info["mode_ok"]:
        blockers.append(
            {
                "id": "BLK-INFRA-04",
                "field": "DB_PASSWORD_SECRETS_FILE",
                "reason": "Host secret file missing, <16 bytes, contains CR/CRLF, or lacks 0600 permissions.",
            }
        )
    if require_escrow_flag and not escrow_confirmed:
        blockers.append(
            {
                "id": "BLK-INFRA-04",
                "field": "encryption_key_escrow",
                "reason": "Off-VPS encrypted escrow of site_config.json encryption_key has not been attested (--confirm-encryption-key-escrow).",
            }
        )

    status = "PREFLIGHT_READY" if not blockers else "BLOCKED"
    return {
        "timestamp_utc": utc_now_iso(),
        "env_file": _display_path(env_path),
        "project_name": project_name,
        "site_name": site_name,
        "status": status,
        "blocker_count": len(blockers),
        "blockers": blockers,
        "checks": checks,
    }


def parse_sid_cookie_from_headers(set_cookie_headers: list[str]) -> dict[str, Any]:
    """Extract attributes strictly from the `sid=` Set-Cookie header line (F-P4-02)."""
    cookie_names: list[str] = []
    sid_attrs: dict[str, Any] | None = None
    for raw_header in set_cookie_headers:
        parts = [p.strip() for p in raw_header.split(";") if p.strip()]
        if not parts or "=" not in parts[0]:
            continue
        name, _val = parts[0].split("=", 1)
        name_clean = name.strip()
        cookie_names.append(name_clean)
        if name_clean != "sid":
            continue
        attrs: dict[str, Any] = {
            "found": True,
            "redacted_value": "<REDACTED_FRAPPE_SID>",
            "secure": False,
            "httponly": False,
            "samesite": None,
            "path": None,
        }
        for attr in parts[1:]:
            if "=" in attr:
                ak, av = attr.split("=", 1)
                ak_low = ak.strip().lower()
                if ak_low == "samesite":
                    attrs["samesite"] = av.strip()
                elif ak_low == "path":
                    attrs["path"] = av.strip()
            else:
                alow = attr.strip().lower()
                if alow == "secure":
                    attrs["secure"] = True
                elif alow == "httponly":
                    attrs["httponly"] = True
        sid_attrs = attrs

    if sid_attrs is None:
        return {
            "found": False,
            "cookie_names_seen": cookie_names,
            "secure": False,
            "httponly": False,
            "samesite": None,
            "path": None,
        }
    sid_attrs["cookie_names_seen"] = cookie_names
    return sid_attrs


def validate_https_sid_cookie_policy(sid_info: dict[str, Any]) -> tuple[bool, list[str]]:
    """Fail-closed HTTPS cookie policy for Staging & Production (G05 / P4 §8.3)."""
    errors: list[str] = []
    if not sid_info.get("found"):
        errors.append("Missing sid Set-Cookie header on login response")
    if not sid_info.get("httponly"):
        errors.append("Cookie sid is missing HttpOnly flag")
    if not sid_info.get("secure"):
        errors.append("Cookie sid is missing Secure flag over HTTPS")
    samesite = (sid_info.get("samesite") or "").lower()
    if samesite not in ("lax", "strict"):
        errors.append(f"Cookie sid has unexpected SameSite={sid_info.get('samesite')!r} (expected Lax or Strict)")
    return (len(errors) == 0, errors)


def compare_migration_snapshots(
    before: dict[str, Any],
    after_run1: dict[str, Any],
    after_run2: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate P4 §6.2 & BLG-P4-MIG-01 migration reconciliation invariants."""
    violations: list[str] = []
    b_counts = before.get("counts", {})
    r1_counts = after_run1.get("counts", {})
    r2_counts = after_run2.get("counts", {})

    # 1. Canonical row preservation (N-1 -> N must not drop Leads, Work Items, History, Attempts, Snapshots)
    for key in (
        "crm_lead",
        "cskh_work_item",
        "cskh_assignment_history",
        "cskh_contact_attempt",
        "cskh_kpi_snapshot",
    ):
        if r1_counts.get(key, 0) < b_counts.get(key, 0):
            violations.append(
                f"Row count decreased for {key}: before={b_counts.get(key)} -> run1={r1_counts.get(key)}"
            )

    # 2. Idempotency between migrate run #1 and migrate run #2 (exact match on structural/canonical tables)
    for key in (
        "crm_lead",
        "cskh_work_item",
        "cskh_active_claim",
        "cskh_assignment_history",
        "cskh_contact_attempt",
        "cskh_outcome_code",
        "cskh_policy",
    ):
        if r1_counts.get(key) != r2_counts.get(key):
            violations.append(
                f"Non-idempotent second migrate on {key}: run1={r1_counts.get(key)} != run2={r2_counts.get(key)}"
            )

    # 3. Data integrity invariants after both runs
    for label, snap in (("after_run1", after_run1), ("after_run2", after_run2)):
        inv = snap.get("invariants", {})
        if inv.get("duplicate_active_leads", 0) != 0:
            violations.append(f"{label}: duplicate_active_leads={inv.get('duplicate_active_leads')} (must be 0)")
        if inv.get("owner_mismatch_active_work_items", 0) != 0:
            violations.append(
                f"{label}: owner_mismatch_active_work_items={inv.get('owner_mismatch_active_work_items')} (must be 0)"
            )
        if inv.get("unverified_marked_verified", 0) != 0:
            violations.append(
                f"{label}: unverified_marked_verified={inv.get('unverified_marked_verified')} (must be 0)"
            )
        if inv.get("false_zero_qa_snapshots", 0) != 0:
            violations.append(
                f"{label}: false_zero_qa_snapshots={inv.get('false_zero_qa_snapshots')} (must be 0)"
            )

    # 4. Self-reported count must not silently convert to verified during migration
    if (
        b_counts.get("contact_attempt_verified", 0) < r1_counts.get("contact_attempt_verified", 0)
        and r1_counts.get("cskh_qa_review_published", 0) == b_counts.get("cskh_qa_review_published", 0)
    ):
        violations.append(
            "contact_attempt_verified increased during migration without new published QA reviews"
        )

    # 5. BLG-P4-MIG-01 module casing & collation check
    for label, snap in (("after_run1", after_run1), ("after_run2", after_run2)):
        mig01 = snap.get("blg_p4_mig_01", {})
        collation = mig01.get("module_def_collation", "")
        if collation and collation.endswith("_bin"):
            violations.append(f"{label}: unsupported binary collation on tabModule Def ({collation})")
        if not mig01.get("export_job_table_exists", False):
            violations.append(f"{label}: tabCSKH Export Job missing")
        dist = mig01.get("cskh_doctype_module_distribution", {})
        total_cskh_dt = sum(int(v) for v in dist.values())
        if total_cskh_dt != 23:
            violations.append(f"{label}: expected 23 CSKH DocTypes, found {total_cskh_dt} ({dist})")
        allowed_keys = {"CRM CSKH", "Crm Cskh"}
        unexpected = set(dist.keys()) - allowed_keys
        if unexpected:
            violations.append(f"{label}: unexpected module names on CSKH DocTypes: {sorted(unexpected)}")

    return {
        "timestamp_utc": utc_now_iso(),
        "status": "PASS" if not violations else "FAIL",
        "violation_count": len(violations),
        "violations": violations,
        "summary": {
            "before_counts": b_counts,
            "after_run1_counts": r1_counts,
            "after_run2_counts": r2_counts,
            "after_run2_blg_p4_mig_01": after_run2.get("blg_p4_mig_01", {}),
        },
    }


def _compose_cmd(env_file: Path, mode: str = "tls") -> list[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in COMPOSE_MODES[mode]:
        cmd.extend(["-f", str(cf)])
    return cmd


def _parse_compose_ps_json(raw_stdout: str) -> list[dict[str, Any]]:
    raw = (raw_stdout or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        return json.loads(raw)
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def verify_running_containers_digest(
    containers: list[dict[str, Any]],
    expected_app_image: str,
) -> dict[str, Any]:
    """Verify running container `.Image` ID -> `docker image inspect` `.RepoDigests` (F-P4-OPS-02)."""
    expected_clean = expected_app_image.strip()
    expected_sha256 = (
        expected_clean.split("@", 1)[1]
        if "@" in expected_clean
        else expected_clean
    )
    by_svc: dict[str, dict[str, Any]] = {}
    for row in containers:
        svc = str(row.get("Service", ""))
        if svc in APP_SERVICES:
            by_svc[svc] = row

    missing_services: list[str] = []
    digest_mismatches: list[str] = []
    inspected_services: dict[str, dict[str, Any]] = {}

    for svc in APP_SERVICES:
        row = by_svc.get(svc)
        if not row or row.get("State") != "running" or not row.get("Name"):
            missing_services.append(svc)
            continue
        cname = str(row["Name"])
        c_insp = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", cname],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        running_image_id = c_insp.stdout.strip()
        img_insp = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", running_image_id],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        repo_digests: list[str] = json.loads(img_insp.stdout.strip() or "[]") or []
        if "@" in expected_clean:
            matched = expected_clean in repo_digests
        else:
            matched = bool(repo_digests) and any(d.endswith("@" + expected_sha256) for d in repo_digests)

        inspected_services[svc] = {
            "container": cname,
            "running_image_id": running_image_id,
            "repo_digests": repo_digests,
            "matched": matched,
        }
        if not matched:
            digest_mismatches.append(f"{svc}:image_id={running_image_id},repo_digests={repo_digests}")

    return {
        "expected_app_image": expected_clean,
        "expected_sha256": expected_sha256,
        "missing_services": missing_services,
        "inspected_services": inspected_services,
        "mismatches": digest_mismatches,
        "pass": len(missing_services) == 0 and len(digest_mismatches) == 0,
    }


def capture_live_snapshot(env_file: Path, site_name: str, mode: str = "tls") -> dict[str, Any]:
    """Run read-only zero-PII count & invariant snapshot inside `backend`."""
    probe_code = r"""
import collections, json, os, frappe
site = os.environ["CSKH_PROBE_SITE"]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    def scalar(sql, params=None):
        rows = frappe.db.sql(sql) if params is None else frappe.db.sql(sql, params)
        return int(rows[0][0] or 0) if rows else 0

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

    counts = {
        "crm_lead": scalar("SELECT COUNT(*) FROM `tabCRM Lead`"),
        "cskh_work_item": scalar("SELECT COUNT(*) FROM `tabCSKH Work Item`"),
        "cskh_active_claim": scalar("SELECT COUNT(*) FROM `tabCSKH Active Claim`"),
        "cskh_assignment_history": scalar("SELECT COUNT(*) FROM `tabCSKH Assignment History`"),
        "cskh_contact_attempt": scalar("SELECT COUNT(*) FROM `tabCSKH Contact Attempt`"),
        "contact_attempt_self_reported": scalar("SELECT COUNT(*) FROM `tabCSKH Contact Attempt` WHERE verification_status='self_reported'"),
        "contact_attempt_verified": scalar("SELECT COUNT(*) FROM `tabCSKH Contact Attempt` WHERE verification_status='verified'"),
        "cskh_qa_review_published": scalar(
            "SELECT COUNT(DISTINCT qr.contact_attempt) FROM `tabCSKH QA Review` qr "
            "INNER JOIN `tabCSKH Contact Attempt` ca ON ca.name = qr.contact_attempt "
            "WHERE qr.status = 'published'"
        ),
        "cskh_sla_obligation": scalar("SELECT COUNT(*) FROM `tabCSKH SLA Obligation`"),
        "cskh_kpi_run": scalar("SELECT COUNT(*) FROM `tabCSKH KPI Run`"),
        "cskh_kpi_snapshot": scalar("SELECT COUNT(*) FROM `tabCSKH KPI Snapshot`"),
        "qa_zero_denom_null_snapshots": qa_zero_denom_null,
        "cskh_outcome_code": scalar("SELECT COUNT(*) FROM `tabCSKH Outcome Code`"),
        "cskh_policy": scalar("SELECT COUNT(*) FROM `tabCSKH Policy`"),
        "cskh_export_job": scalar("SELECT COUNT(*) FROM `tabCSKH Export Job`"),
    }
    invariants = {
        "duplicate_active_leads": scalar(
            "SELECT COUNT(*) FROM (SELECT lead FROM `tabCSKH Work Item` WHERE status IN ('assigned','in_progress','on_hold') GROUP BY lead HAVING COUNT(*) > 1) t"
        ),
        "owner_mismatch_active_work_items": scalar(
            "SELECT COUNT(*) FROM `tabCSKH Work Item` wi JOIN `tabCRM Lead` l ON l.name = wi.lead WHERE wi.status IN ('assigned','in_progress','on_hold') AND IFNULL(wi.assigned_to,'') != IFNULL(l.lead_owner,'')"
        ),
        "unverified_marked_verified": scalar(
            "SELECT COUNT(*) FROM `tabCSKH Contact Attempt` ca "
            "WHERE ca.verification_status='verified' AND IFNULL(ca.call_log,'')='' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM `tabCSKH QA Review` qr "
            "WHERE qr.contact_attempt = ca.name AND qr.status = 'published'"
            ")"
        ),
        "false_zero_qa_snapshots": false_zero_qa,
    }
    mod_rows = frappe.db.sql(
        "SELECT module FROM `tabDocType` WHERE name LIKE 'CSKH %'",
        as_list=True,
    )
    mod_dist = dict(collections.Counter(str(r[0]) for r in mod_rows))
    col_rows = frappe.db.sql(
        "SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tabModule Def' AND COLUMN_NAME = 'name'",
        as_list=True,
    )
    tbl_exists = bool(frappe.db.table_exists("CSKH Export Job"))
    print("SNAPSHOT_JSON=" + json.dumps({
        "site": site,
        "counts": counts,
        "invariants": invariants,
        "blg_p4_mig_01": {
            "module_def_collation": col_rows[0][0] if col_rows else "",
            "export_job_table_exists": tbl_exists,
            "cskh_doctype_module_distribution": mod_dist,
        },
    }, sort_keys=True))
finally:
    frappe.destroy()
"""
    cmd = _compose_cmd(env_file, mode=mode) + [
        "exec",
        "-T",
        "-e",
        f"CSKH_PROBE_SITE={site_name}",
        "backend",
        "/home/frappe/frappe-bench/env/bin/python",
        "-c",
        probe_code,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
    for line in proc.stdout.splitlines():
        if line.startswith("SNAPSHOT_JSON="):
            payload = json.loads(line.split("=", 1)[1])
            payload["timestamp_utc"] = utc_now_iso()
            payload["fingerprint_sha256"] = hashlib.sha256(
                json.dumps(payload["counts"], sort_keys=True).encode("utf-8")
            ).hexdigest()
            return payload
    raise RuntimeError("Backend snapshot probe did not return SNAPSHOT_JSON payload")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _https_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 15.0,
) -> tuple[int, list[tuple[str, str]], bytes]:
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _NoRedirectHandler)
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, list(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, list(exc.headers.items()), exc.read()


def _check_tls_certificate(hostname: str, port: int = 443) -> dict[str, Any]:
    ctx = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            cert = ssock.getpeercert()
            cipher = ssock.cipher()
            tls_version = ssock.version()
    not_after_str = cert.get("notAfter", "")
    not_after_dt = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days_remaining = (not_after_dt - datetime.now(timezone.utc)).days
    return {
        "hostname": hostname,
        "tls_version": tls_version,
        "cipher": cipher[0] if cipher else None,
        "not_after_utc": not_after_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_remaining": days_remaining,
        "expiry_ok": days_remaining >= 14,
    }


def _check_wss_upgrade(hostname: str, sid_cookie_pair: str, port: int = 443) -> dict[str, Any]:
    raw_key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1((raw_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    ).decode("ascii")
    ctx = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=10) as tcp_sock:
        with ctx.wrap_socket(tcp_sock, server_hostname=hostname) as tls_sock:
            tls_sock.settimeout(10)
            req = (
                f"GET /socket.io/?EIO=4&transport=websocket HTTP/1.1\r\n"
                f"Host: {hostname}\r\n"
                f"Origin: https://{hostname}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Key: {raw_key}\r\n"
                f"Cookie: {sid_cookie_pair}\r\n\r\n"
            )
            tls_sock.sendall(req.encode("utf-8"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = tls_sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            head_bytes, rem = buf.split(b"\r\n\r\n", 1)
            head_lines = head_bytes.decode("iso-8859-1").split("\r\n")
            status_line = head_lines[0]
            hdrs: dict[str, str] = {}
            for hl in head_lines[1:]:
                if ":" in hl:
                    k, v = hl.split(":", 1)
                    hdrs[k.strip().lower()] = v.strip()
            accept_ok = hdrs.get("sec-websocket-accept") == expected_accept
            # Read initial Engine.IO open frame ('0{"sid":...}')
            frame_bytes = rem if rem else tls_sock.recv(4096)
            opcode = frame_bytes[0] & 0x0F if frame_bytes else -1
            length = frame_bytes[1] & 0x7F if len(frame_bytes) >= 2 else 0
            payload_text = frame_bytes[2 : 2 + length].decode("utf-8", errors="replace") if length else ""
            return {
                "status_line": status_line,
                "switched_protocols_101": " 101 " in status_line,
                "sec_websocket_accept_valid": accept_ok,
                "initial_frame_opcode": opcode,
                "engineio_open_packet_received": payload_text.startswith("0{"),
                "initial_frame_redacted": re.sub(
                    r'"sid"\s*:\s*"[^"]+"', '"sid":"<REDACTED_SOCKET_SID>"', payload_text
                ),
            }


def run_https_smoke(env_file: Path, admin_user: str, admin_password_env: str) -> dict[str, Any]:
    preflight = evaluate_preflight(env_file, require_escrow_flag=False)
    if preflight["status"] != "PREFLIGHT_READY":
        return {
            "timestamp_utc": utc_now_iso(),
            "status": "BLOCKED",
            "preflight": preflight,
        }

    admin_password = os.environ.get(admin_password_env, "")
    if not admin_password:
        return {
            "timestamp_utc": utc_now_iso(),
            "status": "BLOCKED",
            "reason": f"Missing admin password in environment variable {admin_password_env}",
        }

    env = parse_env_file(env_file)
    site_name = env["SITE_NAME"]
    report: dict[str, Any] = {
        "timestamp_utc": utc_now_iso(),
        "site_name": site_name,
        "app_image": env["APP_IMAGE"],
        "checks": {},
    }

    # 1. Verify running containers use exact APP_IMAGE digest via container .Image -> image .RepoDigests
    ps_proc = subprocess.run(
        _compose_cmd(env_file, mode="tls") + ["ps", "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    containers = _parse_compose_ps_json(ps_proc.stdout)
    report["checks"]["container_digest_parity"] = verify_running_containers_digest(
        containers, env["APP_IMAGE"]
    )

    # 2. HTTP -> HTTPS redirect
    conn = http.client.HTTPConnection(site_name, 80, timeout=10)
    conn.request("GET", "/login")
    http_resp = conn.getresponse()
    loc = http_resp.getheader("Location", "")
    conn.close()
    report["checks"]["http_to_https_redirect"] = {
        "status_code": http_resp.status,
        "location_starts_with_https": loc.startswith(f"https://{site_name}"),
        "pass": http_resp.status in (301, 308) and loc.startswith(f"https://{site_name}"),
    }

    # 3. TLS certificate verification
    tls_info = _check_tls_certificate(site_name, 443)
    report["checks"]["tls_certificate"] = tls_info

    # 4. Login & Set-Cookie sid Secure + HttpOnly check
    login_body = urllib.parse.urlencode({"usr": admin_user, "pwd": admin_password}).encode("utf-8")
    st_code, hdrs, _body = _https_request(
        f"https://{site_name}/api/method/login",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        body=login_body,
    )
    set_cookies = [v for k, v in hdrs if k.lower() == "set-cookie"]
    sid_attrs = parse_sid_cookie_from_headers(set_cookies)
    cookie_ok, cookie_errors = validate_https_sid_cookie_policy(sid_attrs)
    sid_raw_pair = ""
    for sc in set_cookies:
        first = sc.split(";", 1)[0].strip()
        if first.startswith("sid="):
            sid_raw_pair = first
            break
    report["checks"]["login_and_sid_cookie"] = {
        "http_status": st_code,
        "sid_attributes": sid_attrs,
        "policy_errors": cookie_errors,
        "pass": st_code == 200 and cookie_ok and bool(sid_raw_pair),
    }

    # 5. Mandatory Vietnamese heading & X-Frappe-CSRF-Token extraction + CSRF reject/accept test
    dash_code, _, dash_bytes = _https_request(
        f"https://{site_name}/cskh/dashboard",
        headers={"Cookie": sid_raw_pair},
    )
    dash_html = dash_bytes.decode("utf-8", errors="replace")
    csrf_token = extract_csrf_token(dash_html)
    bad_csrf_code, _, _ = _https_request(
        f"https://{site_name}/api/method/crm_cskh.api.health.ping",
        method="POST",
        headers={"Cookie": sid_raw_pair, "X-Frappe-CSRF-Token": "invalid-csrf-token"},
        body=b"",
    )
    good_csrf_code, _, good_csrf_bytes = _https_request(
        f"https://{site_name}/api/method/crm_cskh.api.health.ping",
        method="POST",
        headers={"Cookie": sid_raw_pair, "X-Frappe-CSRF-Token": csrf_token},
        body=b"",
    )
    report["checks"]["vi_ui_and_csrf"] = evaluate_vi_ui_and_csrf_check(
        dash_code=dash_code,
        dash_html=dash_html,
        csrf_token=csrf_token,
        bad_csrf_code=bad_csrf_code,
        good_csrf_code=good_csrf_code,
        good_csrf_bytes=good_csrf_bytes,
    )

    # 6. Secure WebSocket (wss://) upgrade & frame check
    wss_info = _check_wss_upgrade(site_name, sid_raw_pair, 443)
    wss_info["pass"] = bool(
        wss_info["switched_protocols_101"]
        and wss_info["sec_websocket_accept_valid"]
        and wss_info["engineio_open_packet_received"]
    )
    report["checks"]["wss_websocket"] = wss_info

    all_passed = all(
        bool(section.get("pass", section.get("expiry_ok", False)))
        for section in report["checks"].values()
    )
    report["status"] = "PASS" if all_passed else "FAIL"
    return json.loads(
        redact_sensitive(
            json.dumps(report),
            extra_secrets=[admin_password],
            csrf_tokens=[csrf_token],
        )
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser("preflight", help="Check Staging/Prod readiness and report BLOCKED inputs")
    p_pre.add_argument("--env-file", type=Path, required=True)
    p_pre.add_argument("--confirm-encryption-key-escrow", action="store_true")
    p_pre.add_argument("--out", type=Path)

    p_dig = sub.add_parser(
        "verify-digest",
        help="Inspect running container image IDs and verify RepoDigests against expected APP_IMAGE",
    )
    p_dig.add_argument("--env-file", type=Path, required=True)
    p_dig.add_argument("--mode", choices=("tls", "isolated-smoke"), default="tls")
    p_dig.add_argument("--expected-image", help="Override expected <repo>@sha256:<64hex> for verification")
    p_dig.add_argument("--out", type=Path)

    p_smoke = sub.add_parser("https-smoke", help="Run live HTTPS smoke against approved Staging/Prod")
    p_smoke.add_argument("--env-file", type=Path, required=True)
    p_smoke.add_argument("--admin-user", default="Administrator")
    p_smoke.add_argument("--admin-password-env", default="CSKH_SMOKE_ADMIN_PASSWORD")
    p_smoke.add_argument("--out", type=Path)

    p_snap = sub.add_parser("snapshot-counts", help="Capture zero-PII DB counts & BLG-P4-MIG-01 state")
    p_snap.add_argument("--env-file", type=Path, required=True)
    p_snap.add_argument("--site", required=True)
    p_snap.add_argument("--mode", choices=("tls", "isolated-smoke"), default="tls")
    p_snap.add_argument("--out", type=Path, required=True)

    p_cmp = sub.add_parser("compare-counts", help="Compare before / migrate-1 / migrate-2 count snapshots")
    p_cmp.add_argument("--before", type=Path, required=True)
    p_cmp.add_argument("--after-run1", type=Path, required=True)
    p_cmp.add_argument("--after-run2", type=Path, required=True)
    p_cmp.add_argument("--out", type=Path)

    args = parser.parse_args(argv)

    if args.command == "preflight":
        res = evaluate_preflight(
            args.env_file,
            require_escrow_flag=True,
            escrow_confirmed=args.confirm_encryption_key_escrow,
        )
        text = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(text)
        return 0

    if args.command == "verify-digest":
        env = parse_env_file(args.env_file)
        expected_img = args.expected_image or env.get("APP_IMAGE", "")
        ps_proc = subprocess.run(
            _compose_cmd(args.env_file, mode=args.mode) + ["ps", "--format", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        containers = _parse_compose_ps_json(ps_proc.stdout)
        parity = verify_running_containers_digest(containers, expected_img)
        res = {
            "timestamp_utc": utc_now_iso(),
            "status": "PASS" if parity["pass"] else "FAIL",
            "container_digest_parity": parity,
        }
        text = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(text)
        return 0 if parity["pass"] else 2

    if args.command == "https-smoke":
        res = run_https_smoke(args.env_file, args.admin_user, args.admin_password_env)
        text = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(text)
        return 0 if res["status"] == "PASS" else 2

    if args.command == "snapshot-counts":
        res = capture_live_snapshot(args.env_file, args.site, mode=args.mode)
        text = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(text)
        return 0

    if args.command == "compare-counts":
        before = json.loads(args.before.read_text(encoding="utf-8"))
        r1 = json.loads(args.after_run1.read_text(encoding="utf-8"))
        r2 = json.loads(args.after_run2.read_text(encoding="utf-8"))
        res = compare_migration_snapshots(before, r1, r2)
        text = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8", newline="\n")
        sys.stdout.write(text)
        return 0 if res["status"] == "PASS" else 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
