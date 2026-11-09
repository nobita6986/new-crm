#!/usr/bin/env python3
"""Render and optionally install systemd timers for CRM-CSKH periodic healthcheck & backup.

Matches the live VPS topology (`/opt/crm-cskh-staging/.env.staging`, wrapper
`/opt/crm-cskh-staging/staging-compose`, site `crm-staging.localhost`, and domain
overlay `/opt/crm-cskh-staging/compose.domain.yaml`), including:
- Periodic healthcheck + backup freshness/staleness monitoring (`--backup-status-file`).
- Daily encrypted backup + checksum + GFS `14d/8w/3m` retention (`crm-cskh-backup.timer`).
- Dedicated failure alert recorder (`OnFailure=crm-cskh-backup-failure@%n.service`).

Safety guarantees:
- Read-only `--dry-run` by default unless `--install` is explicitly passed as root.
- Refuses missing `--env-file`, `--compose-wrapper`, or `--extra-compose-file`.
- External alerts (`email`/`chat`/webhook) remain strictly DISABLED in periodic
  units until Owner assigns `BLK-GOV-06` and `BLK-INFRA-07`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from typing import Any

OPS_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = OPS_DIR.parents[1]
DEFAULT_OFFSITE_ENV_FILE = Path("/etc/crm-cskh/offsite-backup.env")
DEFAULT_ALERT_ENV_FILE = Path("/etc/crm-cskh/alerting.env")


def render_healthcheck_units(
    *,
    repo_root: Path,
    env_file: Path,
    site: str,
    environment_label: str = "staging",
    mode: str = "tls",
    extra_compose_files: tuple[Path, ...] = (),
    backup_status_file: Path | None = Path("/var/backups/crm-cskh/latest-backup-lifecycle.json"),
    max_backup_age_hours: float = 26.0,
    alert_log_file: Path = Path("/var/lib/crm-cskh/health/health-alerts.jsonl"),
    out_report: Path = Path("/var/lib/crm-cskh/health/latest-health-report.json"),
    alert_env_file: Path = DEFAULT_ALERT_ENV_FILE,
    calendar_spec: str = "*:0/5",
) -> dict[str, str]:
    script = (repo_root / "deploy" / "ops" / "check_health_and_invariants.py").as_posix()
    cmd_parts = [
        "/usr/bin/python3",
        script,
        "--env-file",
        env_file.as_posix(),
        "--mode",
        mode,
    ]
    for overlay in extra_compose_files:
        cmd_parts.extend(["--extra-compose-file", overlay.as_posix()])
    cmd_parts.extend(
        [
            "--site",
            site,
            "--environment-label",
            environment_label,
        ]
    )
    if backup_status_file is not None:
        cmd_parts.extend(
            [
                "--backup-status-file",
                backup_status_file.as_posix(),
                "--max-backup-age-hours",
                str(max_backup_age_hours),
            ]
        )
    cmd_parts.extend(
        [
            "--alert-log-file",
            alert_log_file.as_posix(),
            "--out",
            out_report.as_posix(),
        ]
    )
    exec_line = " ".join(shlex.quote(part) for part in cmd_parts)
    service = (
        "[Unit]\n"
        "Description=CRM-CSKH Periodic Technical Health, Backup Freshness & Business Invariants Probe (External Alerts Gated)\n"
        "After=docker.service\n"
        "Requires=docker.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=root\n"
        "Group=root\n"
        "UMask=0077\n"
        f"EnvironmentFile=-{alert_env_file.as_posix()}\n"
        f"ExecStart={exec_line}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Run CRM-CSKH Health & Invariants Probe Periodically\n\n"
        "[Timer]\n"
        f"OnCalendar={calendar_spec}\n"
        "RandomizedDelaySec=20\n"
        "Persistent=true\n"
        "Unit=crm-cskh-healthcheck.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {
        "crm-cskh-healthcheck.service": service,
        "crm-cskh-healthcheck.timer": timer,
    }


def render_backup_units(
    *,
    repo_root: Path,
    compose_wrapper: Path,
    site: str,
    environment_label: str = "staging",
    backup_root: Path = Path("/var/backups/crm-cskh"),
    keep_daily: int = 14,
    keep_weekly: int = 8,
    keep_monthly: int = 3,
    offsite_env_file: Path = DEFAULT_OFFSITE_ENV_FILE,
    alert_env_file: Path = DEFAULT_ALERT_ENV_FILE,
    calendar_spec: str = "*-*-* 19:15:00 UTC",
) -> dict[str, str]:
    script = (repo_root / "deploy" / "ops" / "manage_backup_lifecycle.py").as_posix()
    cmd_parts = [
        "/usr/bin/python3",
        script,
        "run-scheduled",
        "--compose-wrapper",
        compose_wrapper.as_posix(),
        "--site",
        site,
        "--backup-root",
        backup_root.as_posix(),
        "--environment-label",
        environment_label,
        "--keep-daily",
        str(keep_daily),
        "--keep-weekly",
        str(keep_weekly),
        "--keep-monthly",
        str(keep_monthly),
    ]
    exec_line = " ".join(shlex.quote(part) for part in cmd_parts)

    fail_parts = [
        "/usr/bin/python3",
        script,
        "emit-failure-alert",
        "--backup-root",
        backup_root.as_posix(),
        "--environment-label",
        environment_label,
        "--unit-name",
        "%i",
        "--reason",
        "systemd unit %i exited non-zero",
    ]
    fail_exec = " ".join(shlex.quote(part) for part in fail_parts)

    service = (
        "[Unit]\n"
        "Description=CRM-CSKH Daily Encrypted Backup + Checksum + GFS Retention\n"
        "After=docker.service\n"
        "Requires=docker.service\n"
        "OnFailure=crm-cskh-backup-failure@%n.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=root\n"
        "Group=root\n"
        "UMask=0077\n"
        f"EnvironmentFile=-{offsite_env_file.as_posix()}\n"
        f"EnvironmentFile=-{alert_env_file.as_posix()}\n"
        f"ExecStart={exec_line}\n"
    )
    failure_service = (
        "[Unit]\n"
        "Description=CRM-CSKH Backup Failure Alert Recorder (External Delivery Gated)\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=root\n"
        "Group=root\n"
        "UMask=0077\n"
        f"EnvironmentFile=-{alert_env_file.as_posix()}\n"
        f"ExecStart={fail_exec}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Run CRM-CSKH Daily Encrypted Backup\n\n"
        "[Timer]\n"
        f"OnCalendar={calendar_spec}\n"
        "RandomizedDelaySec=120\n"
        "Persistent=true\n"
        "Unit=crm-cskh-backup.service\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {
        "crm-cskh-backup.service": service,
        "crm-cskh-backup-failure@.service": failure_service,
        "crm-cskh-backup.timer": timer,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    p.add_argument("--env-file", type=Path, default=Path("/opt/crm-cskh-staging/.env.staging"))
    p.add_argument(
        "--compose-wrapper",
        type=Path,
        default=Path("/opt/crm-cskh-staging/staging-compose"),
    )
    p.add_argument(
        "--extra-compose-file",
        type=Path,
        action="append",
        default=[],
        help="Additional deployment overlay(s) such as /opt/crm-cskh-staging/compose.domain.yaml",
    )
    p.add_argument("--site", default="crm-staging.localhost")
    p.add_argument("--environment-label", default="staging")
    p.add_argument("--backup-root", type=Path, default=Path("/var/backups/crm-cskh"))
    p.add_argument(
        "--health-out",
        type=Path,
        default=Path("/var/lib/crm-cskh/health/latest-health-report.json"),
    )
    p.add_argument(
        "--offsite-env-file",
        type=Path,
        default=DEFAULT_OFFSITE_ENV_FILE,
        help="Optional 0600 drop-in env file for automated offsite upload (loaded with systemd '-')",
    )
    p.add_argument(
        "--alert-env-file",
        type=Path,
        default=DEFAULT_ALERT_ENV_FILE,
        help="Optional 0600 drop-in env file for external alert contact/webhook (loaded with systemd '-')",
    )
    p.add_argument("--systemd-dir", type=Path, default=Path("/etc/systemd/system"))
    p.add_argument("--install", action="store_true", help="Write units and enable timers as root")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = build_parser()
    args = p.parse_args(argv)

    overlays = tuple(args.extra_compose_file)
    required_ops_scripts = (
        args.repo_root / "deploy" / "ops" / "check_health_and_invariants.py",
        args.repo_root / "deploy" / "ops" / "manage_backup_lifecycle.py",
        args.repo_root / "deploy" / "ops" / "create_encrypted_backup.py",
    )
    units: dict[str, str] = {}
    units.update(
        render_healthcheck_units(
            repo_root=args.repo_root,
            env_file=args.env_file,
            site=args.site,
            environment_label=args.environment_label,
            extra_compose_files=overlays,
            backup_status_file=args.backup_root / "latest-backup-lifecycle.json",
            out_report=args.health_out,
            alert_env_file=args.alert_env_file,
        )
    )
    units.update(
        render_backup_units(
            repo_root=args.repo_root,
            compose_wrapper=args.compose_wrapper,
            site=args.site,
            environment_label=args.environment_label,
            backup_root=args.backup_root,
            offsite_env_file=args.offsite_env_file,
            alert_env_file=args.alert_env_file,
        )
    )

    offsite_env_present = args.offsite_env_file.is_file()
    alert_env_present = args.alert_env_file.is_file()
    summary: dict[str, Any] = {
        "mode": "install" if args.install else "dry_run",
        "repo_root": args.repo_root.as_posix(),
        "referenced_ops_scripts": [s.as_posix() for s in required_ops_scripts],
        "site": args.site,
        "extra_compose_files": [x.as_posix() for x in overlays],
        "optional_offsite_env_file": args.offsite_env_file.as_posix(),
        "optional_alert_env_file": args.alert_env_file.as_posix(),
        "external_alerts_enabled": alert_env_present,
        "external_alert_gate": (
            "ALERT_ENV_FILE_PRESENT_PREFLIGHT_ENFORCED_AT_RUNTIME"
            if alert_env_present
            else "WAITING_OWNER_CONTACT_AND_CHANNEL_BLK_GOV_06_BLK_INFRA_07"
        ),
        "automated_offsite_upload_enabled": offsite_env_present,
        "automated_offsite_status": (
            "OFFSITE_ENV_FILE_PRESENT_VERIFIED_AT_RUNTIME"
            if offsite_env_present
            else "PARTIAL_UNTIL_AUTOMATED_TARGET_PROVISIONED"
        ),
        "one_off_manual_copy_proves_periodic_upload": False,
        "automated_offsite_blocker": None if offsite_env_present else "BLK-INFRA-05",
        "units": units,
    }

    if args.install:
        if os.name != "posix" or os.geteuid() != 0:
            raise SystemExit("--install requires root on Linux")
        for script_path in required_ops_scripts:
            if not script_path.is_file():
                raise SystemExit(
                    f"Required ops script missing under --repo-root: {script_path}"
                )
        for required_path in (args.env_file, args.compose_wrapper, *overlays):
            if not required_path.is_file():
                raise SystemExit(f"Required path missing on host: {required_path}")
        for opt_env in (args.offsite_env_file, args.alert_env_file):
            if opt_env.is_file():
                mode = stat.S_IMODE(opt_env.stat().st_mode)
                if (mode & 0o077) != 0:
                    raise SystemExit(
                        f"Optional env file must have 0600 permissions (got {oct(mode)}): {opt_env}"
                    )
        args.offsite_env_file.parent.mkdir(parents=True, exist_ok=True)
        args.offsite_env_file.parent.chmod(0o700)
        args.health_out.parent.mkdir(parents=True, exist_ok=True)
        args.backup_root.mkdir(parents=True, exist_ok=True)
        args.backup_root.chmod(0o700)
        args.systemd_dir.mkdir(parents=True, exist_ok=True)
        for name, content in units.items():
            dst = args.systemd_dir / name
            dst.write_text(content, encoding="utf-8", newline="\n")
            dst.chmod(0o644)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(
            [
                "systemctl",
                "enable",
                "--now",
                "crm-cskh-healthcheck.timer",
                "crm-cskh-backup.timer",
            ],
            check=True,
        )
        summary["timers_enabled"] = [
            "crm-cskh-healthcheck.timer",
            "crm-cskh-backup.timer",
        ]

    sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
