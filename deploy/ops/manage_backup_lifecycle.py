#!/usr/bin/env python3
"""Backup schedule wrapper, SHA-256 checksum/offsite verifier, GFS retention & failure alerter.

Implements P4-05 sustainable backup operations (`P4.md §7.1–§7.4`, Gate `G07`):
1. `run-scheduled`: Invokes `create_encrypted_backup.py` into a timestamped bundle
   directory, verifies archive bytes + SHA-256 + POSIX `0600` permissions, runs
   optional automated offsite upload + SHA-256 readback (strictly excluding `backup-key`),
   applies Grandfather-Father-Son (`14 daily / 8 weekly / 3 monthly`) retention pruning,
   persists `latest-backup-lifecycle.json`, and emits `ALT-BACKUP-FAILURE` on any error.
2. `verify-bundle`: Verifies `backup-report.json`, `backup.tar.gz.enc` SHA-256 &
   byte length, `0600` key/archive permissions, and `offsite-verification.json`.
3. `record-offsite`: Records `offsite-verification.json` after an operator manually
   copies the encrypted archive offsite and confirms the offsite SHA-256 digest.
   Explicitly marks `automated_upload = False` and `periodic_offsite_upload_proven = False`
   because a single manual copy to an Owner/operator machine does NOT prove scheduled
   automated offsite upload works.
4. `prune-retention`: Evaluates or applies the `14 daily / 8 weekly / 3 monthly`
   retention policy while strictly protecting the newest verified backup and any
   pinned/milestone backups (`pre-release`, `pre-scheduler`, `pre-cutover`, `golden`, `.pinned`).
5. `emit-failure-alert`: Triggered automatically by `run-scheduled` on error or by
   systemd `OnFailure=crm-cskh-backup-failure@%n.service`; writes a zero-secret/zero-PII
   `ALT-BACKUP-FAILURE` record to `<backup_root>/backup-alerts.jsonl` (`0600`) and
   keeps external webhook/chat delivery gated until Owner assigns `BLK-GOV-06` & `BLK-INFRA-07`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from typing import Any

OPS_DIR = Path(__file__).resolve().parent
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

import check_health_and_invariants as health_mod  # noqa: E402

CREATE_BACKUP_SCRIPT = OPS_DIR / "create_encrypted_backup.py"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MILESTONE_TOKENS = ("pre-release", "pre-scheduler", "pre-cutover", "golden")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_iso_utc(raw: str) -> datetime:
    clean = raw.strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    clean = re.sub(r"\.(\d{6})\d+", r".\1", clean)
    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verify_backup_bundle(
    bundle_dir: Path,
    *,
    expect_key_file: bool = True,
    enforce_posix_permissions: bool = True,
) -> dict[str, Any]:
    """Verify archive checksum, size, permissions, and offsite confirmation status."""
    errors: list[str] = []
    report_path = bundle_dir / "backup-report.json"
    archive_path = bundle_dir / "backup.tar.gz.enc"
    key_path = bundle_dir / "backup-key"
    offsite_path = bundle_dir / "offsite-verification.json"

    if not report_path.is_file():
        return {
            "bundle_dir": str(bundle_dir),
            "local_archive_status": "FAIL",
            "offsite_copy_status": "UNVERIFIED",
            "offsite_verified": False,
            "automated_offsite_upload_configured": False,
            "periodic_offsite_upload_proven": False,
            "one_off_manual_copy_proves_periodic_upload": False,
            "automated_offsite_pipeline_status": "BLOCKED_BLK_INFRA_05",
            "end_to_end_backup_status": "FAIL",
            "errors": ["Missing backup-report.json"],
        }

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "bundle_dir": str(bundle_dir),
            "local_archive_status": "FAIL",
            "offsite_copy_status": "UNVERIFIED",
            "offsite_verified": False,
            "automated_offsite_upload_configured": False,
            "periodic_offsite_upload_proven": False,
            "one_off_manual_copy_proves_periodic_upload": False,
            "automated_offsite_pipeline_status": "BLOCKED_BLK_INFRA_05",
            "end_to_end_backup_status": "FAIL",
            "errors": ["Malformed backup-report.json"],
        }

    expected_sha = str(report.get("archive_sha256", ""))
    expected_bytes = int(report.get("archive_bytes", -1))
    if not SHA256_RE.match(expected_sha):
        errors.append("Invalid archive_sha256 in backup-report.json")
    if expected_bytes <= 0:
        errors.append("Invalid archive_bytes in backup-report.json")

    actual_sha = ""
    actual_bytes = 0
    if not archive_path.is_file():
        errors.append("Missing backup.tar.gz.enc")
    else:
        actual_bytes = archive_path.stat().st_size
        actual_sha = sha256_file(archive_path)
        if actual_bytes != expected_bytes:
            errors.append(
                f"Archive byte size mismatch: expected {expected_bytes}, got {actual_bytes}"
            )
        if actual_sha != expected_sha:
            errors.append("Archive SHA-256 digest mismatch against backup-report.json")
        if enforce_posix_permissions and os.name == "posix":
            mode = stat.S_IMODE(archive_path.stat().st_mode)
            if (mode & 0o077) != 0:
                errors.append(f"Encrypted archive permissions too broad: {oct(mode)}")

    if expect_key_file:
        if not key_path.is_file():
            errors.append("Missing backup-key file")
        else:
            if key_path.stat().st_size < 32:
                errors.append("backup-key file unexpectedly short")
            if enforce_posix_permissions and os.name == "posix":
                kmode = stat.S_IMODE(key_path.stat().st_mode)
                if kmode != 0o600:
                    errors.append(f"backup-key must have 0600 permissions, got {oct(kmode)}")

    offsite_verified = False
    automated_offsite = False
    key_separated = False
    offsite_meta: dict[str, Any] | None = None
    if offsite_path.is_file():
        try:
            offsite_meta = json.loads(offsite_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append("Malformed offsite-verification.json")
        else:
            if offsite_meta.get("status") != "OFFSITE_COPY_HASH_VERIFIED":
                errors.append("offsite-verification.json status is not OFFSITE_COPY_HASH_VERIFIED")
            elif (
                offsite_meta.get("archive_sha256") != expected_sha
                or int(offsite_meta.get("archive_bytes", -1)) != expected_bytes
            ):
                errors.append(
                    "offsite-verification.json digest/bytes do not match backup-report.json"
                )
            else:
                offsite_verified = True
                automated_offsite = bool(offsite_meta.get("automated_upload", False))
                key_separated = bool(
                    offsite_meta.get("key_excluded_from_offsite_archive_target", False)
                )

    local_ok = len(errors) == 0
    periodic_proven = local_ok and offsite_verified and automated_offsite and key_separated
    if not local_ok:
        offsite_status = "FAIL"
        pipeline_status = "FAIL"
        e2e_status = "FAIL"
    elif periodic_proven:
        offsite_status = "AUTOMATED_OFFSITE_HASH_VERIFIED"
        pipeline_status = "AUTOMATED_OFFSITE_VERIFIED"
        e2e_status = "PASS"
    elif offsite_verified:
        offsite_status = "MANUAL_OFFSITE_HASH_VERIFIED"
        # Single manual copy does NOT prove periodic automated offsite upload works.
        pipeline_status = "BLOCKED_BLK_INFRA_05"
        e2e_status = "PARTIAL_MANUAL_OFFSITE_VERIFIED_AUTOMATION_BLOCKED"
    else:
        offsite_status = "PENDING_OFFSITE_COPY"
        pipeline_status = "BLOCKED_BLK_INFRA_05"
        e2e_status = "PARTIAL_LOCAL_ONLY_OFFSITE_PENDING"

    return {
        "bundle_dir": str(bundle_dir),
        "bundle_name": bundle_dir.name,
        "completed_at_utc": report.get("completed_at_utc"),
        "source_project": report.get("source_project"),
        "source_revision": report.get("source_revision"),
        "archive_sha256": expected_sha,
        "archive_bytes": expected_bytes,
        "local_archive_status": "PASS" if local_ok else "FAIL",
        "offsite_copy_status": offsite_status,
        "offsite_verified": offsite_verified,
        "offsite_verified_at_utc": offsite_meta.get("verified_at_utc") if offsite_meta else None,
        "automated_offsite_upload_configured": automated_offsite,
        "periodic_offsite_upload_proven": periodic_proven,
        "one_off_manual_copy_proves_periodic_upload": False,
        "automated_offsite_pipeline_status": pipeline_status,
        "end_to_end_backup_status": e2e_status,
        "errors": errors,
    }


def record_offsite_confirmation(
    bundle_dir: Path,
    verified_sha256: str,
    offsite_location_label: str = "operator-offsite-vault",
) -> dict[str, Any]:
    """Record offsite SHA-256 verification receipt after manual SCP/pull transfer."""
    clean_sha = verified_sha256.strip().lower()
    if not SHA256_RE.match(clean_sha):
        raise ValueError("Expected lowercase 64-hex SHA-256 digest")

    pre = verify_backup_bundle(bundle_dir, expect_key_file=False, enforce_posix_permissions=False)
    if pre["local_archive_status"] != "PASS":
        raise ValueError(f"Cannot record offsite receipt on invalid bundle: {pre['errors']}")
    if clean_sha != pre["archive_sha256"]:
        raise ValueError("Offsite SHA-256 does not match bundle archive_sha256")

    receipt = {
        "verified_at_utc": utc_now_iso(),
        "archive_sha256": clean_sha,
        "archive_bytes": pre["archive_bytes"],
        "source_revision": pre["source_revision"],
        "offsite_location_label": offsite_location_label,
        "transfer_mode": "manual_verified_copy",
        "automated_upload": False,
        "periodic_upload_proven": False,
        "key_excluded_from_offsite_archive_target": True,
        "status": "OFFSITE_COPY_HASH_VERIFIED",
    }
    out_file = bundle_dir / "offsite-verification.json"
    out_file.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    if os.name == "posix":
        out_file.chmod(0o600)
    return verify_backup_bundle(bundle_dir, expect_key_file=False, enforce_posix_permissions=False)


def sync_bundle_offsite_automated(
    bundle_dir: Path,
    *,
    backup_root: Path,
    offsite_target_dir: Path | None = None,
    offsite_upload_cmd: str = "",
    offsite_verify_cmd: str = "",
    offsite_location_label: str = "automated-offsite-vault",
) -> dict[str, Any]:
    """Execute automated offsite transfer + remote SHA-256 readback with strict key separation.

    Security invariants:
    - `backup-key` MUST NEVER be copied or exposed to the offsite archive destination.
    - Offsite target directory MUST NOT reside inside `backup_root`.
    - Remote SHA-256 and byte length MUST match `backup-report.json` before recording receipt.
    """
    pre = verify_backup_bundle(bundle_dir, expect_key_file=False, enforce_posix_permissions=False)
    if pre["local_archive_status"] != "PASS":
        raise ValueError(f"Refusing offsite upload of invalid local bundle: {pre['errors']}")

    expected_sha = str(pre["archive_sha256"])
    expected_bytes = int(pre["archive_bytes"])
    archive_src = bundle_dir / "backup.tar.gz.enc"
    report_src = bundle_dir / "backup-report.json"

    upload_cmd_clean = offsite_upload_cmd.strip()
    verify_cmd_clean = offsite_verify_cmd.strip()
    if offsite_target_dir is None and not upload_cmd_clean:
        raise ValueError("Neither offsite_target_dir nor offsite_upload_cmd was provided")
    if upload_cmd_clean and not verify_cmd_clean:
        raise ValueError("offsite_upload_cmd requires offsite_verify_cmd for independent SHA-256 readback")

    verified_remote_sha = ""
    verified_remote_bytes = 0

    if offsite_target_dir is not None:
        root_resolved = backup_root.resolve()
        target_resolved = offsite_target_dir.resolve()
        if target_resolved == root_resolved or root_resolved in target_resolved.parents or target_resolved in root_resolved.parents:
            raise ValueError(
                "Offsite target directory must be physically/logically disjoint from local backup_root"
            )
        remote_bundle_dir = target_resolved / bundle_dir.name
        remote_bundle_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            remote_bundle_dir.chmod(0o700)

        remote_archive = remote_bundle_dir / "backup.tar.gz.enc"
        remote_report = remote_bundle_dir / "backup-report.json"
        shutil.copy2(archive_src, remote_archive)
        shutil.copy2(report_src, remote_report)
        if os.name == "posix":
            remote_archive.chmod(0o600)
            remote_report.chmod(0o600)

        # Fail closed if decryption key ever exists at offsite archive target!
        if (remote_bundle_dir / "backup-key").exists() or any(
            p.name == "backup-key" for p in remote_bundle_dir.iterdir()
        ):
            raise ValueError("CRITICAL: backup-key detected in offsite archive destination")

        verified_remote_bytes = remote_archive.stat().st_size
        verified_remote_sha = sha256_file(remote_archive)

    if upload_cmd_clean:
        cmd_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CSKH_BUNDLE_NAME": bundle_dir.name,
            "CSKH_ARCHIVE_PATH": str(archive_src),
            "CSKH_REPORT_PATH": str(report_src),
            "CSKH_EXPECTED_SHA256": expected_sha,
            "CSKH_EXPECTED_BYTES": str(expected_bytes),
        }
        up_proc = subprocess.run(
            ["/bin/sh", "-c", upload_cmd_clean],
            env=cmd_env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if up_proc.returncode != 0:
            raise RuntimeError(f"Automated offsite upload command exited {up_proc.returncode}")

        ver_proc = subprocess.run(
            ["/bin/sh", "-c", verify_cmd_clean],
            env=cmd_env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if ver_proc.returncode != 0:
            raise RuntimeError(f"Automated offsite verify command exited {ver_proc.returncode}")
        tokens = ver_proc.stdout.strip().split()
        verified_remote_sha = tokens[0].lower() if tokens else ""
        verified_remote_bytes = int(tokens[1]) if len(tokens) >= 2 and tokens[1].isdigit() else expected_bytes

    if verified_remote_bytes != expected_bytes:
        raise ValueError(
            f"Offsite readback byte size mismatch: expected {expected_bytes}, got {verified_remote_bytes}"
        )
    if verified_remote_sha != expected_sha:
        raise ValueError(
            f"Offsite readback SHA-256 mismatch: expected {expected_sha}, got {verified_remote_sha}"
        )

    receipt = {
        "verified_at_utc": utc_now_iso(),
        "archive_sha256": verified_remote_sha,
        "archive_bytes": verified_remote_bytes,
        "source_revision": pre["source_revision"],
        "offsite_location_label": offsite_location_label,
        "transfer_mode": "automated_scheduled_upload",
        "automated_upload": True,
        "periodic_upload_proven": True,
        "key_excluded_from_offsite_archive_target": True,
        "status": "OFFSITE_COPY_HASH_VERIFIED",
    }
    receipt_path = bundle_dir / "offsite-verification.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    if os.name == "posix":
        receipt_path.chmod(0o600)
    if offsite_target_dir is not None:
        remote_receipt = offsite_target_dir.resolve() / bundle_dir.name / "offsite-verification.json"
        shutil.copy2(receipt_path, remote_receipt)
        if os.name == "posix":
            remote_receipt.chmod(0o600)

    return verify_backup_bundle(bundle_dir, expect_key_file=False, enforce_posix_permissions=False)


def emit_backup_failure_alert(
    *,
    backup_root: Path,
    environment_label: str,
    reason: str,
    metrics: dict[str, Any] | None = None,
    operator_contact: str = "",
    webhook_url: str = "",
    dedup_state_file: Path | None = None,
) -> dict[str, Any]:
    """Persist redacted ALT-BACKUP-FAILURE alert locally and dispatch only if Owner channel is READY."""
    backup_root.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        backup_root.chmod(0o700)

    preflight = health_mod.evaluate_alert_preflight(operator_contact, webhook_url)
    owner_val = operator_contact.strip() or "UNASSIGNED_PENDING_BLK_GOV_06"
    envelope = health_mod.build_alert_envelope(
        "ALT-BACKUP-FAILURE",
        environment_label,
        owner_val,
        reason,
        metrics or {},
    )
    if preflight["status"] == "READY":
        state_file = dedup_state_file or (backup_root / "backup-alert-dedup.json")
        dispatch = health_mod.dispatch_alert_with_cooldown(
            envelope,
            webhook_url,
            dedup_state_file=state_file,
        )
    else:
        dispatch = {
            "status": "GATED_PENDING_OWNER_CONTACT_AND_CHANNEL",
            "external_delivery_attempted": False,
            "blockers": [b["id"] for b in preflight["blockers"]],
        }

    record = {
        "timestamp_utc": utc_now_iso(),
        "alert_envelope": envelope,
        "alert_preflight_status": preflight["status"],
        "dispatch": dispatch,
    }
    alert_log = backup_root / "backup-alerts.jsonl"
    scrubbed_line = health_mod.redact_payload_text(json.dumps(record, ensure_ascii=False))
    with alert_log.open("a", encoding="utf-8", newline="\n") as f:
        f.write(scrubbed_line + "\n")
    if os.name == "posix":
        alert_log.chmod(0o600)
    return json.loads(scrubbed_line)


def _is_pinned_bundle(bundle_dir: Path, protect_milestones: bool = True) -> tuple[bool, str | None]:
    if (bundle_dir / ".pinned").exists():
        return True, "pinned_marker"
    if protect_milestones:
        low = bundle_dir.name.lower()
        for tok in MILESTONE_TOKENS:
            if tok in low:
                return True, f"milestone_name:{tok}"
    return False, None


def plan_gfs_retention(
    backup_root: Path,
    *,
    keep_daily: int = 14,
    keep_weekly: int = 8,
    keep_monthly: int = 3,
    protect_milestones: bool = True,
) -> dict[str, Any]:
    """Compute deterministic GFS (14 daily / 8 weekly / 3 monthly) retention plan."""
    if keep_daily < 1 or keep_weekly < 0 or keep_monthly < 0:
        raise ValueError("Invalid retention counts")

    verified_entries: list[dict[str, Any]] = []
    skipped_unverified: list[dict[str, Any]] = []

    if backup_root.is_dir():
        for child in sorted(backup_root.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            check = verify_backup_bundle(
                child,
                expect_key_file=False,
                enforce_posix_permissions=False,
            )
            ts_raw = check.get("completed_at_utc")
            if check["local_archive_status"] != "PASS" or not isinstance(ts_raw, str):
                skipped_unverified.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "errors": check.get("errors", ["Missing completed_at_utc"]),
                    }
                )
                continue
            completed_dt = parse_iso_utc(ts_raw)
            pinned, pin_reason = _is_pinned_bundle(child, protect_milestones=protect_milestones)
            verified_entries.append(
                {
                    "name": child.name,
                    "path": child,
                    "completed_dt": completed_dt,
                    "completed_at_utc": ts_raw,
                    "archive_sha256": check["archive_sha256"],
                    "pinned": pinned,
                    "pin_reason": pin_reason,
                }
            )

    # Newest first; tie-break deterministically by directory name descending.
    verified_entries.sort(key=lambda e: (e["completed_dt"], e["name"]), reverse=True)

    kept_reasons: dict[str, list[str]] = {}
    daily_buckets: set[str] = set()
    weekly_buckets: set[str] = set()
    monthly_buckets: set[str] = set()

    for idx, entry in enumerate(verified_entries):
        name = entry["name"]
        dt: datetime = entry["completed_dt"]
        reasons: list[str] = []
        if idx == 0:
            reasons.append("newest_verified")
        if entry["pinned"]:
            reasons.append(str(entry["pin_reason"]))

        day_key = dt.strftime("%Y-%m-%d")
        if day_key not in daily_buckets and len(daily_buckets) < keep_daily:
            daily_buckets.add(day_key)
            reasons.append(f"daily:{day_key}")

        iso_year, iso_week, _ = dt.isocalendar()
        week_key = f"{iso_year:04d}-W{iso_week:02d}"
        if week_key not in weekly_buckets and len(weekly_buckets) < keep_weekly:
            weekly_buckets.add(week_key)
            reasons.append(f"weekly:{week_key}")

        month_key = dt.strftime("%Y-%m")
        if month_key not in monthly_buckets and len(monthly_buckets) < keep_monthly:
            monthly_buckets.add(month_key)
            reasons.append(f"monthly:{month_key}")

        if reasons:
            kept_reasons[name] = reasons

    kept = [
        {
            "name": e["name"],
            "completed_at_utc": e["completed_at_utc"],
            "archive_sha256": e["archive_sha256"],
            "reasons": kept_reasons[e["name"]],
        }
        for e in verified_entries
        if e["name"] in kept_reasons
    ]
    prune_candidates = [
        {
            "name": e["name"],
            "path": str(e["path"]),
            "completed_at_utc": e["completed_at_utc"],
            "archive_sha256": e["archive_sha256"],
        }
        for e in verified_entries
        if e["name"] not in kept_reasons
    ]
    return {
        "policy": {
            "keep_daily": keep_daily,
            "keep_weekly": keep_weekly,
            "keep_monthly": keep_monthly,
            "protect_milestones": protect_milestones,
        },
        "verified_total": len(verified_entries),
        "kept_count": len(kept),
        "prune_count": len(prune_candidates),
        "kept": kept,
        "prune_candidates": prune_candidates,
        "skipped_unverified": skipped_unverified,
    }


def apply_retention_plan(backup_root: Path, plan: dict[str, Any], apply: bool = False) -> dict[str, Any]:
    deleted: list[str] = []
    root_abs = Path(os.path.abspath(backup_root))
    if apply:
        for item in plan["prune_candidates"]:
            target = Path(os.path.abspath(item["path"]))
            if target.parent != root_abs or not (target / "backup-report.json").is_file():
                raise ValueError(f"Refusing unsafe retention delete target: {target}")
            shutil.rmtree(target)
            deleted.append(item["name"])
    out = dict(plan)
    out["applied"] = bool(apply)
    out["deleted_bundles"] = deleted
    return out


def _persist_latest_lifecycle(backup_root: Path, summary: dict[str, Any]) -> None:
    latest_path = backup_root / "latest-backup-lifecycle.json"
    latest_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    if os.name == "posix":
        latest_path.chmod(0o600)


def run_scheduled_backup(
    *,
    compose_wrapper: Path,
    site: str,
    backup_root: Path,
    environment_label: str = "staging",
    keep_daily: int = 14,
    keep_weekly: int = 8,
    keep_monthly: int = 3,
    prune_old: bool = True,
    offsite_target_dir: Path | None = None,
    offsite_upload_cmd: str = "",
    offsite_verify_cmd: str = "",
    offsite_location_label: str = "automated-offsite-vault",
    operator_contact: str = "",
    webhook_url: str = "",
) -> tuple[int, dict[str, Any]]:
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Run scheduled backup as root on the Linux Docker host")
    os.umask(0o077)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_root.chmod(0o700)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = backup_root / f"scheduled-{stamp}-{os.getpid()}"
    proc = subprocess.run(
        [
            sys.executable,
            str(CREATE_BACKUP_SCRIPT),
            "--compose-wrapper",
            str(compose_wrapper),
            "--site",
            site,
            "--out-dir",
            str(bundle_dir),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        alert_rec = emit_backup_failure_alert(
            backup_root=backup_root,
            environment_label=environment_label,
            reason="Scheduled backup failed: create_encrypted_backup.py exited non-zero.",
            metrics={"site": site, "bundle_name": bundle_dir.name, "exit_code": proc.returncode},
            operator_contact=operator_contact,
            webhook_url=webhook_url,
        )
        summary = {
            "schema_version": "p4-backup-lifecycle-v2",
            "timestamp_utc": utc_now_iso(),
            "local_backup_status": "FAIL",
            "status": "FAIL",
            "periodic_offsite_upload_proven": False,
            "one_off_manual_copy_proves_periodic_upload": False,
            "end_to_end_backup_status": "FAIL",
            "bundle_dir": str(bundle_dir),
            "error": "create_encrypted_backup.py exited non-zero",
            "failure_alert": alert_rec,
        }
        _persist_latest_lifecycle(backup_root, summary)
        return 1, summary

    verification = verify_backup_bundle(bundle_dir, expect_key_file=True, enforce_posix_permissions=True)
    if verification["local_archive_status"] != "PASS":
        alert_rec = emit_backup_failure_alert(
            backup_root=backup_root,
            environment_label=environment_label,
            reason="Scheduled backup bundle failed post-creation SHA-256 or POSIX permission verification.",
            metrics={"site": site, "bundle_name": bundle_dir.name, "errors": verification["errors"]},
            operator_contact=operator_contact,
            webhook_url=webhook_url,
        )
        summary = {
            "schema_version": "p4-backup-lifecycle-v2",
            "timestamp_utc": utc_now_iso(),
            "local_backup_status": "FAIL",
            "status": "FAIL",
            "periodic_offsite_upload_proven": False,
            "one_off_manual_copy_proves_periodic_upload": False,
            "end_to_end_backup_status": "FAIL",
            "latest_bundle": verification,
            "failure_alert": alert_rec,
        }
        _persist_latest_lifecycle(backup_root, summary)
        return 1, summary

    offsite_configured = offsite_target_dir is not None or bool(offsite_upload_cmd.strip())
    if offsite_configured:
        try:
            sync_bundle_offsite_automated(
                bundle_dir,
                backup_root=backup_root,
                offsite_target_dir=offsite_target_dir,
                offsite_upload_cmd=offsite_upload_cmd,
                offsite_verify_cmd=offsite_verify_cmd,
                offsite_location_label=offsite_location_label,
            )
            verification = verify_backup_bundle(
                bundle_dir, expect_key_file=True, enforce_posix_permissions=True
            )
        except Exception as exc:
            alert_rec = emit_backup_failure_alert(
                backup_root=backup_root,
                environment_label=environment_label,
                reason=f"Automated offsite upload or SHA-256 readback failed: {type(exc).__name__}",
                metrics={"site": site, "bundle_name": bundle_dir.name, "error_type": type(exc).__name__},
                operator_contact=operator_contact,
                webhook_url=webhook_url,
            )
            summary = {
                "schema_version": "p4-backup-lifecycle-v2",
                "timestamp_utc": utc_now_iso(),
                "local_backup_status": "PASS",
                "status": "FAIL",
                "automated_offsite_upload_configured": True,
                "periodic_offsite_upload_proven": False,
                "one_off_manual_copy_proves_periodic_upload": False,
                "end_to_end_backup_status": "FAIL",
                "error": str(exc),
                "latest_bundle": verification,
                "failure_alert": alert_rec,
            }
            _persist_latest_lifecycle(backup_root, summary)
            return 1, summary

    plan = plan_gfs_retention(
        backup_root,
        keep_daily=keep_daily,
        keep_weekly=keep_weekly,
        keep_monthly=keep_monthly,
        protect_milestones=True,
    )
    retention_res = apply_retention_plan(backup_root, plan, apply=prune_old)

    periodic_proven = bool(verification.get("periodic_offsite_upload_proven", False))
    summary = {
        "schema_version": "p4-backup-lifecycle-v2",
        "timestamp_utc": utc_now_iso(),
        "local_backup_status": verification["local_archive_status"],
        # Explicit honesty: local scheduled backup succeeds locally, NOT end-to-end PASS without automated offsite upload.
        "end_to_end_backup_status": verification["end_to_end_backup_status"],
        "automated_offsite_upload_configured": offsite_configured,
        "periodic_offsite_upload_proven": periodic_proven,
        "one_off_manual_copy_proves_periodic_upload": False,
        "key_separation_enforced": True,
        "automated_offsite_blocker": None if periodic_proven else "BLK-INFRA-05",
        "action_required": (
            None
            if periodic_proven
            else (
                "Provision automated offsite vault (BLK-INFRA-05) for end-to-end Gate G07 PASS; "
                "until then, copy backup.tar.gz.enc offsite, store backup-key in separate secret escrow, "
                "verify SHA-256 offsite, and run manage_backup_lifecycle.py record-offsite."
            )
        ),
        "latest_bundle": verification,
        "retention": {
            "applied": retention_res["applied"],
            "kept_count": retention_res["kept_count"],
            "deleted_bundles": retention_res["deleted_bundles"],
        },
    }
    _persist_latest_lifecycle(backup_root, summary)
    return 0, summary


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ver = sub.add_parser("verify-bundle", help="Verify local encrypted bundle & offsite status")
    p_ver.add_argument("--bundle-dir", type=Path, required=True)
    p_ver.add_argument(
        "--no-key-in-dir",
        action="store_true",
        help="Allow backup-key to reside in separate vault outside bundle-dir",
    )
    p_ver.add_argument(
        "--require-offsite",
        action="store_true",
        help="Return exit code 2 if offsite SHA-256 confirmation receipt is absent",
    )
    p_ver.add_argument(
        "--require-automated-offsite",
        action="store_true",
        help="Return exit code 2 unless automated periodic offsite upload + SHA-256 readback is proven",
    )
    p_ver.add_argument("--out", type=Path)

    p_rec = sub.add_parser("record-offsite", help="Record verified manual offsite copy SHA-256")
    p_rec.add_argument("--bundle-dir", type=Path, required=True)
    p_rec.add_argument("--verified-sha256", required=True)
    p_rec.add_argument("--offsite-location-label", default="operator-offsite-vault")

    p_ret = sub.add_parser("prune-retention", help="Preview or apply GFS 14d/8w/3m retention")
    p_ret.add_argument("--backup-root", type=Path, required=True)
    p_ret.add_argument("--keep-daily", type=int, default=14)
    p_ret.add_argument("--keep-weekly", type=int, default=8)
    p_ret.add_argument("--keep-monthly", type=int, default=3)
    p_ret.add_argument("--include-milestones", action="store_true")
    p_ret.add_argument("--apply", action="store_true", help="Delete prune candidates (default dry-run)")
    p_ret.add_argument("--out", type=Path)

    p_run = sub.add_parser("run-scheduled", help="Run encrypted backup + verify + offsite + GFS retention")
    p_run.add_argument("--compose-wrapper", type=Path, required=True)
    p_run.add_argument("--site", required=True)
    p_run.add_argument("--backup-root", type=Path, required=True)
    p_run.add_argument("--environment-label", default="staging")
    p_run.add_argument("--keep-daily", type=int, default=14)
    p_run.add_argument("--keep-weekly", type=int, default=8)
    p_run.add_argument("--keep-monthly", type=int, default=3)
    p_run.add_argument("--no-prune", action="store_true")
    p_run.add_argument(
        "--offsite-target-dir",
        type=Path,
        default=Path(os.environ["CSKH_OFFSITE_TARGET_DIR"])
        if os.environ.get("CSKH_OFFSITE_TARGET_DIR")
        else None,
        help="Optional mounted offsite archive target (backup-key is strictly excluded)",
    )
    p_run.add_argument(
        "--offsite-upload-cmd",
        default=os.environ.get("CSKH_OFFSITE_UPLOAD_CMD", ""),
        help="Optional command to upload backup.tar.gz.enc + backup-report.json offsite",
    )
    p_run.add_argument(
        "--offsite-verify-cmd",
        default=os.environ.get("CSKH_OFFSITE_VERIFY_CMD", ""),
        help="Command outputting '<sha256> [bytes]' read back from offsite destination",
    )
    p_run.add_argument("--offsite-location-label", default="automated-offsite-vault")
    p_run.add_argument("--operator-contact", default=os.environ.get("CSKH_OPERATOR_CONTACT", ""))
    p_run.add_argument("--webhook-url", default=os.environ.get("CSKH_ALERT_WEBHOOK_URL", ""))

    p_fail = sub.add_parser(
        "emit-failure-alert",
        help="Record ALT-BACKUP-FAILURE alert (invoked by systemd OnFailure= unit)",
    )
    p_fail.add_argument("--backup-root", type=Path, default=Path("/var/backups/crm-cskh"))
    p_fail.add_argument("--environment-label", default="staging")
    p_fail.add_argument("--unit-name", default="crm-cskh-backup.service")
    p_fail.add_argument("--reason", default="systemd unit crm-cskh-backup.service failed")
    p_fail.add_argument("--operator-contact", default=os.environ.get("CSKH_OPERATOR_CONTACT", ""))
    p_fail.add_argument("--webhook-url", default=os.environ.get("CSKH_ALERT_WEBHOOK_URL", ""))

    args = parser.parse_args(argv)

    if args.command == "verify-bundle":
        res = verify_backup_bundle(
            args.bundle_dir,
            expect_key_file=not args.no_key_in_dir,
            enforce_posix_permissions=True,
        )
        txt = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(txt, encoding="utf-8", newline="\n")
        sys.stdout.write(txt)
        if res["local_archive_status"] != "PASS":
            return 1
        if args.require_automated_offsite and not res["periodic_offsite_upload_proven"]:
            return 2
        if args.require_offsite and not res["offsite_verified"]:
            return 2
        return 0

    if args.command == "record-offsite":
        res = record_offsite_confirmation(
            args.bundle_dir,
            args.verified_sha256,
            args.offsite_location_label,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0

    if args.command == "prune-retention":
        plan = plan_gfs_retention(
            args.backup_root,
            keep_daily=args.keep_daily,
            keep_weekly=args.keep_weekly,
            keep_monthly=args.keep_monthly,
            protect_milestones=not args.include_milestones,
        )
        res = apply_retention_plan(args.backup_root, plan, apply=args.apply)
        txt = json.dumps(res, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(txt, encoding="utf-8", newline="\n")
        sys.stdout.write(txt)
        return 0

    if args.command == "run-scheduled":
        rc, res = run_scheduled_backup(
            compose_wrapper=args.compose_wrapper,
            site=args.site,
            backup_root=args.backup_root,
            environment_label=args.environment_label,
            keep_daily=args.keep_daily,
            keep_weekly=args.keep_weekly,
            keep_monthly=args.keep_monthly,
            prune_old=not args.no_prune,
            offsite_target_dir=args.offsite_target_dir,
            offsite_upload_cmd=args.offsite_upload_cmd,
            offsite_verify_cmd=args.offsite_verify_cmd,
            offsite_location_label=args.offsite_location_label,
            operator_contact=args.operator_contact,
            webhook_url=args.webhook_url,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return rc

    if args.command == "emit-failure-alert":
        rec = emit_backup_failure_alert(
            backup_root=args.backup_root,
            environment_label=args.environment_label,
            reason=args.reason,
            metrics={"failed_unit": args.unit_name},
            operator_contact=args.operator_contact,
            webhook_url=args.webhook_url,
        )
        sys.stdout.write(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
