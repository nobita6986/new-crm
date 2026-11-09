#!/usr/bin/env python3
"""Create a private encrypted DB+files+site-key bundle without changing site configuration.

Run on the Docker host. The output is LOCAL until separately copied and verified
offsite (`status = LOCAL_BACKUP_VERIFIED_OFFSITE_PENDING`, `end_to_end_backup_status =
PARTIAL_OFFSITE_UPLOAD_MANUAL`). Automated offsite upload is NOT claimed when only
local encrypted staging has completed. A consistency fingerprint detects business
writes during the backup; it does not turn an online backup into an atomic
filesystem/DB snapshot.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import tempfile
from typing import Any


SNAPSHOT = r"""
import hashlib, json, os, pathlib, sys, frappe
site = sys.argv[1]
os.chdir('sites')
site_cfg = json.loads((pathlib.Path(site) / 'site_config.json').read_text(encoding='utf-8'))
raw_enc_key = site_cfg.get('encryption_key')
enc_present = raw_enc_key is not None
enc_sha256 = hashlib.sha256(str(raw_enc_key).encode('utf-8')).hexdigest() if enc_present else None
frappe.init(site=site, sites_path='.')
frappe.connect()
try:
    doctypes = [
        'CRM Lead',
        'CRM Deal',
        'CSKH Work Item',
        'CSKH Active Claim',
        'CSKH Assignment History',
        'CSKH Contact Attempt',
        'CSKH Attempt Correction',
        'CSKH SLA Obligation',
        'CSKH KPI Snapshot',
        'CSKH KPI Run',
        'CRM Form Script',
        'User',
        'Translation',
        'File',
    ]
    hashes = {}
    for dt in doctypes:
        rows = frappe.get_all(dt, fields=['*'], order_by='name')
        hashes[dt] = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    auth_enc_rows = int(frappe.db.sql('SELECT COUNT(*) FROM `__Auth` WHERE encrypted=1')[0][0] or 0)
    translations = frappe.get_all(
        'Translation',
        fields=['language', 'source_text', 'translated_text', 'context'],
        order_by='name',
    )
    translation_sha256 = hashlib.sha256(
        json.dumps(translations, sort_keys=True, default=str).encode('utf-8')
    ).hexdigest()
    print('SNAPSHOT_JSON=' + json.dumps({
        'counts': {d: frappe.db.count(d) for d in doctypes},
        'table_sha256': hashes,
        'fingerprint_algorithm': 'json-sort-keys-default-str-fields-star-order-name-v1',
        'scheduler_enabled': frappe.db.get_single_value('System Settings', 'enable_scheduler'),
        'encryption_key_present': enc_present,
        'encryption_key_sha256': enc_sha256,
        'auth_encrypted_rows': auth_enc_rows,
        'translation_rows': len(translations),
        'translation_sha256': translation_sha256,
    }))
finally:
    frappe.destroy()
"""


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def is_valid_fernet_key(key_value: object) -> bool:
    if not isinstance(key_value, str):
        return False
    clean = key_value.strip()
    if len(clean) != 44:
        return False
    try:
        decoded = base64.urlsafe_b64decode(clean.encode("ascii"))
    except (ValueError, binascii.Error):
        return False
    return len(decoded) == 32


def validate_site_config_backup_member(
    site_cfg: object,
    before_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Fail-closed check ensuring site_config_backup.json matches live encryption_key state."""
    if not isinstance(site_cfg, dict):
        raise ValueError("site_config_backup.json is not a JSON object")
    for req_key in ("db_name", "db_password"):
        val = site_cfg.get(req_key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"site_config_backup.json missing non-empty {req_key}")

    raw_key = site_cfg.get("encryption_key")
    expected_present = bool(before_snapshot.get("encryption_key_present", False))
    expected_sha = before_snapshot.get("encryption_key_sha256")
    auth_enc_rows = int(before_snapshot.get("auth_encrypted_rows", 0) or 0)

    if raw_key is None:
        if expected_present or expected_sha:
            raise ValueError(
                "site_config_backup.json lost encryption_key present on source site"
            )
        if auth_enc_rows > 0:
            raise ValueError(
                "Source site has encrypted __Auth rows without encryption_key in site_config_backup.json"
            )
        return {
            "encryption_key_state": "UNINITIALIZED_NO_ENCRYPTED_DATA",
            "encryption_key_present": False,
            "auth_encrypted_rows": 0,
        }

    if not isinstance(raw_key, str) or not raw_key.strip():
        raise ValueError("site_config_backup.json contains blank/non-string encryption_key")
    if not is_valid_fernet_key(raw_key):
        raise ValueError("site_config_backup.json contains invalid Fernet encryption_key")

    actual_sha = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    if not expected_present:
        raise ValueError(
            "site_config_backup.json contains encryption_key absent from source snapshot"
        )
    if expected_sha and actual_sha != expected_sha:
        raise ValueError(
            "site_config_backup.json encryption_key SHA-256 differs from source snapshot"
        )
    return {
        "encryption_key_state": "PRESENT_VERIFIED",
        "encryption_key_present": True,
        "auth_encrypted_rows": auth_enc_rows,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--compose-wrapper", type=Path, required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args(argv)
    if os.name != "posix" or os.geteuid() != 0:
        p.error("Run as root on the Docker host")
    os.umask(0o077)
    args.out_dir.mkdir(parents=True, exist_ok=False)
    args.out_dir.chmod(0o700)
    wrapper = args.compose_wrapper.resolve(strict=True)

    def command(cmd_argv: list[str]) -> str:
        r = subprocess.run(
            cmd_argv,
            capture_output=True,
            text=True,
            timeout=900,
            cwd=wrapper.parent,
        )
        if r.returncode:
            fail_log = args.out_dir / "failure.private.log"
            fail_log.write_text(r.stdout + r.stderr, encoding="utf-8")
            fail_log.chmod(0o600)
            raise RuntimeError("Backup command failed; inspect private log")
        return r.stdout

    compose = [str(wrapper)]
    container = command(compose + ["ps", "-q", "backend"]).strip()
    if not container or "\n" in container:
        raise ValueError("Expected one backend container")
    meta = json.loads(command(["docker", "inspect", container]))[0]
    cli = compose + ["exec", "-T", "backend", "env/bin/python"]

    def snapshot() -> dict[str, Any]:
        output = command(cli + ["-c", SNAPSHOT, args.site])
        return json.loads(
            next(
                r for r in output.splitlines() if r.startswith("SNAPSHOT_JSON=")
            ).split("=", 1)[1]
        )

    start = datetime.now(timezone.utc)
    before = snapshot()
    backup_script = r"""
import json, pathlib, subprocess, sys
site = sys.argv[1]
folder = pathlib.Path('sites') / site / 'private/backups'
previous = set(folder.glob('*'))
r = subprocess.run(['bench', '--site', site, 'backup', '--with-files'], capture_output=True, text=True)
if r.returncode:
    raise RuntimeError('bench backup failed')
files = [str(f.resolve()) for f in folder.glob('*') if f not in previous and f.is_file()]
print('FILES_JSON=' + json.dumps(files))
"""
    output = command(cli + ["-c", backup_script, args.site])
    files = json.loads(
        next(r for r in output.splitlines() if r.startswith("FILES_JSON=")).split(
            "=", 1
        )[1]
    )
    expected = (
        "-database.sql.gz",
        "-private-files.tar",
        "-files.tar",
        "-site_config_backup.json",
    )
    if len(files) != 4 or not all(any(x.endswith(s) for x in files) for s in expected):
        raise RuntimeError("Incomplete fresh backup file set")
    after = snapshot()
    if (
        before["table_sha256"] != after["table_sha256"]
        or before["encryption_key_sha256"] != after["encryption_key_sha256"]
        or before["auth_encrypted_rows"] != after["auth_encrypted_rows"]
    ):
        raise RuntimeError(
            "Business records or encryption key changed during backup; retry in a controlled quiet window"
        )
    with tempfile.TemporaryDirectory(prefix="crm-p4-backup-") as tmp:
        folder = Path(tmp)
        members: list[Path] = []
        site_cfg_member: Path | None = None
        for source in files:
            dest = folder / Path(source).name
            command(["docker", "cp", container + ":" + source, str(dest)])
            dest.chmod(0o600)
            members.append(dest)
            if dest.name.endswith("-site_config_backup.json"):
                site_cfg_member = dest
        if site_cfg_member is None:
            raise RuntimeError("Missing site_config_backup.json after docker cp")
        key_summary = validate_site_config_backup_member(
            json.loads(site_cfg_member.read_text(encoding="utf-8")),
            before,
        )
        hashes = {f.name: sha(f) for f in members}
        for name, content in [
            ("before.json", before),
            ("backup-file-hashes.json", hashes),
        ]:
            dest = folder / name
            dest.write_text(json.dumps(content), encoding="utf-8")
            dest.chmod(0o600)
            members.append(dest)
        plain = folder / "backup.tar.gz"
        with tarfile.open(plain, "w:gz") as tf:
            for f in members:
                tf.add(f, arcname=f.name, recursive=False)
        key = args.out_dir / "backup-key"
        key.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
        key.chmod(0o600)
        encrypted = args.out_dir / "backup.tar.gz.enc"
        command(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-salt",
                "-pbkdf2",
                "-iter",
                "200000",
                "-in",
                str(plain),
                "-out",
                str(encrypted),
                "-pass",
                "file:" + str(key),
            ]
        )
        encrypted.chmod(0o600)
        check = folder / "decrypt-check.tar.gz"
        command(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-iter",
                "200000",
                "-in",
                str(encrypted),
                "-out",
                str(check),
                "-pass",
                "file:" + str(key),
            ]
        )
        if sha(check) != sha(plain):
            raise RuntimeError("Encryption round trip mismatch")
    report = {
        "schema_version": "p4-encrypted-backup-v2",
        "status": "LOCAL_BACKUP_VERIFIED_OFFSITE_PENDING",
        "end_to_end_backup_status": "PARTIAL_OFFSITE_UPLOAD_MANUAL",
        "offsite_upload_automated": False,
        "offsite_verified": False,
        "started_at_utc": start.isoformat(),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_project": meta["Config"]["Labels"]["com.docker.compose.project"],
        "source_image": meta["Image"],
        "source_revision": meta["Config"]["Labels"].get(
            "org.opencontainers.image.revision"
        ),
        "archive_sha256": sha(encrypted),
        "archive_bytes": encrypted.stat().st_size,
        "member_sha256": hashes,
        "counts": before["counts"],
        "encryption_key_state": key_summary["encryption_key_state"],
        "encryption_key_present": key_summary["encryption_key_present"],
        "auth_encrypted_rows": key_summary["auth_encrypted_rows"],
        "translation_override_rows": before["translation_rows"],
        "business_fingerprints_stable_during_backup": True,
        "encryption_round_trip_verified": True,
        "site_configuration_changed": False,
    }
    report_path = args.out_dir / "backup-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report_path.chmod(0o600)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
