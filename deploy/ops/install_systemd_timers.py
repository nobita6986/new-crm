#!/usr/bin/env python3
"""Render and optionally install systemd timers for CRM-CSKH periodic healthcheck & backup.

Matches the live VPS topology (`/opt/crm-cskh-staging/.env.staging`, wrapper
`/opt/crm-cskh-staging/staging-compose`, site `crm-staging.localhost`, and domain
overlay `/opt/crm-cskh-staging/compose.domain.yaml`).

Safety guarantees:
- Read-only `--dry-run` by default unless `--install` is explicitly passed as root.
- Refuses missing `--env-file`, `--compose-wrapper`, or `--extra-compose-file`.
- External alerts (`email`/`chat`/webhook) remain strictly DISABLED in periodic
  healthcheck units until Owner assigns `BLK-GOV-06` and `BLK-INFRA-07`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

OPS_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = OPS_DIR.parents[1]


def render_healthcheck_units(
    *,
    repo_root: Path,
    env_file: Path,
    site: str,
    environment_label: str = "staging",
    mode: str = "tls",
    extra_compose_files: tuple[Path, ...] = (),
    out_report: Path = Path("/var/lib/crm-cskh/health/latest-health-report.json"),
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
            "--out",
            out_report.as_posix(),
        ]
    )
    exec_line = " ".join(shlex.quote(part) for part in cmd_parts)
    service = (
        "[Unit]\n"
        "Description=CRM-CSKH Periodic Technical Health & Business Invariants Probe (External Alerts Gated)\n"
        "After=docker.service\n"
        "Requires=docker.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=root\n"
        "Group=root\n"
        "UMask=0077\n"
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
    backup_root: Path = Path("/var/backups/crm-cskh"),
    keep_daily: int = 14,
    keep_weekly: int = 8,
    keep_monthly: int = 3,
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
        "--keep-daily",
        str(keep_daily),
        "--keep-weekly",
        str(keep_weekly),
        "--keep-monthly",
        str(keep_monthly),
    ]
    exec_line = " ".join(shlex.quote(part) for part in cmd_parts)
    service = (
        "[Unit]\n"
        "Description=CRM-CSKH Daily Encrypted Backup + Checksum + GFS Retention\n"
        "After=docker.service\n"
        "Requires=docker.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "User=root\n"
        "Group=root\n"
        "UMask=0077\n"
        f"ExecStart={exec_line}\n"
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
        "crm-cskh-backup.timer": timer,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
    p.add_argument("--systemd-dir", type=Path, default=Path("/etc/systemd/system"))
    p.add_argument("--install", action="store_true", help="Write units and enable timers as root")
    args = p.parse_args(argv)

    overlays = tuple(args.extra_compose_file)
    units: dict[str, str] = {}
    units.update(
        render_healthcheck_units(
            repo_root=args.repo_root,
            env_file=args.env_file,
            site=args.site,
            environment_label=args.environment_label,
            extra_compose_files=overlays,
            out_report=args.health_out,
        )
    )
    units.update(
        render_backup_units(
            repo_root=args.repo_root,
            compose_wrapper=args.compose_wrapper,
            site=args.site,
            backup_root=args.backup_root,
        )
    )

    summary: dict[str, Any] = {
        "mode": "install" if args.install else "dry_run",
        "site": args.site,
        "extra_compose_files": [str(x) for x in overlays],
        "external_alerts_enabled": False,
        "external_alert_gate": "WAITING_OWNER_CONTACT_AND_CHANNEL_BLK_GOV_06_BLK_INFRA_07",
        "automated_offsite_upload_enabled": False,
        "automated_offsite_blocker": "BLK-INFRA-05",
        "units": units,
    }

    if args.install:
        if os.name != "posix" or os.geteuid() != 0:
            raise SystemExit("--install requires root on Linux")
        for required_path in (args.env_file, args.compose_wrapper, *overlays):
            if not required_path.is_file():
                raise SystemExit(f"Required path missing on host: {required_path}")
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
