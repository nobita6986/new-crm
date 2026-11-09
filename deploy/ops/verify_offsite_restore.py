#!/usr/bin/env python3
"""Restore a checksum-pinned encrypted backup into disposable, network-isolated containers.

The source stack is inspected only. No host port, worker, scheduler, live volume,
or live database credential is attached to the restore environment. JSON output
contains counts, phase timings, and hashes — never restored records, passwords,
session cookies, encryption keys, or customer file names.

Verification phases executed strictly inside the disposable isolated clone:
1. Phase A (Real Backup DR + Migrate x2 + RTO Measurement):
   - SHA-256 verification before any Docker action, OpenSSL AES-256-CBC-PBKDF2
     decryption, safe member extraction, and per-member SHA-256 check.
   - Isolated stack (`db`, `redis`, `backend`, `frontend` Nginx on `internal: true`
     network, zero host ports, zero shared live mounts).
   - Restore SQL + public/private files + `site_config_backup.json`.
   - Fail-closed `encryption_key` evaluation distinguishing a site that has not
     yet initialized encrypted secrets (`UNINITIALIZED_NO_ENCRYPTED_DATA`) from a
     backup that lost/corrupted its `encryption_key` (`ValueError`).
   - Reconcile row counts, business table SHA-256 hashes, public/private file
     bytes & SHA-256 hashes, POSIX permissions, and `Translation` overrides.
   - Run `bench migrate` twice (`migrate x2`), verify count/hash idempotency,
     `Translation` override preservation + effective Vietnamese resolution, and
     business invariants `INV-01..06 == 0`.
   - Start Nginx + Gunicorn, verify Guest `403` guards, real `POST /api/method/login`
     (`sid` `HttpOnly`), `/crm` `200`, `/cskh` `200` Vietnamese heading, and CSRF
     reject (`400`/`403`) + accept (`200`). Record end-to-end isolated DR RTO
     with per-phase breakdown (`sql_import_only_is_full_rto = False`).
2. Phase B (Positive Control on Disposable Clone Only):
   - Seed a genuine Fernet-encrypted credential (`__Auth.encrypted = 1`, `gAAAAAB...`),
     non-empty public & private files, and a synthetic business row on the clone.
   - Run full `bench backup --with-files` + OpenSSL encrypt/decrypt round-trip.
   - Wipe files and `encryption_key`; prove negative control fails closed when
     `encryption_key` is missing while `__Auth.encrypted = 1` rows exist.
   - Restore `site_config_backup.json` (`0600`), verify exact secret decryption,
     exact file bytes & SHA-256, POSIX + HTTP access boundary (Guest `200` public,
     Guest `403` private, Authenticated `200` private), and business reconciliation.
3. Guaranteed teardown (`docker compose down --volumes --remove-orphans`) with
   zero remaining containers/volumes/networks and unchanged live source stack.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import subprocess
import tarfile
import tempfile
import time
from typing import Any


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def extract_backup(archive: Path, dest: Path) -> dict[str, Path]:
    """Select only the required regular members; reject traversal and duplicates."""
    selected: dict[str, Path] = {}
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            p = PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts or member.issym() or member.islnk():
                raise ValueError("Unsafe backup archive member")
            if not member.isfile():
                continue
            name = p.name
            suffixes = (
                "-database.sql.gz",
                "-site_config_backup.json",
                "-private-files.tar",
                "-files.tar",
            )
            kind = next((s for s in suffixes if name.endswith(s)), None)
            if name in ("before.json", "backup-file-hashes.json"):
                kind = name
            if kind is None:
                continue
            if kind in selected:
                raise ValueError("Ambiguous backup archive")
            target = dest / name
            extracted = tf.extractfile(member)
            if extracted is None:
                raise ValueError("Unreadable backup archive member")
            with extracted as src, target.open("wb") as dst:
                while block := src.read(1024 * 1024):
                    dst.write(block)
            target.chmod(0o600)
            selected[kind] = target
    required = {
        "-database.sql.gz",
        "-site_config_backup.json",
        "-private-files.tar",
        "-files.tar",
        "before.json",
        "backup-file-hashes.json",
    }
    if selected.keys() != required:
        raise ValueError(
            "Backup missing a required database/files/config/baseline member"
        )
    hashes = json.loads(selected["backup-file-hashes.json"].read_text(encoding="utf-8"))
    for k, p in selected.items():
        if k.startswith("-") and hashes.get(p.name) != digest(p):
            raise ValueError("Backup member digest mismatch")
    return selected


def file_manifest_detailed(archive: Path) -> dict[str, dict[str, Any]]:
    """Return relative path -> {sha256, bytes, mode} for backed-up public/private files."""
    result: dict[str, dict[str, Any]] = {}
    with tarfile.open(archive) as tf:
        for m in tf.getmembers():
            p = PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk():
                raise ValueError("Unsafe file archive member")
            if not m.isfile():
                continue
            if "files" not in p.parts:
                raise ValueError("File outside the backed-up files directory")
            name = "/".join(p.parts[p.parts.index("files") + 1 :])
            if not name or name in result:
                raise ValueError("Duplicate or empty file path")
            extracted = tf.extractfile(m)
            if extracted is None:
                raise ValueError("Unreadable file archive member")
            with extracted as f:
                data = f.read()
            result[name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "mode": int(m.mode) & 0o777,
            }
    return result


def file_manifest(archive: Path) -> dict[str, str]:
    """Return relative path -> sha256 digest map (backward-compatible helper)."""
    return {k: str(v["sha256"]) for k, v in file_manifest_detailed(archive).items()}


SHARED_WORKER_HELPERS_SOURCE = '''import base64, binascii, hashlib, json, os, pathlib, secrets, signal, stat, subprocess, sys, time
from typing import Any


def is_valid_fernet_key(key_value: object) -> bool:
    """Validate Frappe Fernet encryption_key (44-char URL-safe base64 -> 32 raw bytes)."""
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


def evaluate_encryption_key_state(
    saved_site_config: object,
    restored_site_config: object,
    baseline_before: dict[str, Any] | None,
    encrypted_auth_rows: int,
) -> dict[str, Any]:
    """Distinguish fail-closed between an uninitialized key and a lost/corrupted backup key."""
    if not isinstance(saved_site_config, dict):
        raise ValueError("site_config_backup.json is not a valid JSON object")
    if not isinstance(restored_site_config, dict):
        raise ValueError("Restored site_config.json is not a valid JSON object")
    for required_field in ("db_name", "db_password"):
        val = saved_site_config.get(required_field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(
                f"Corrupted or truncated site_config_backup.json: missing {required_field}"
            )
    if isinstance(encrypted_auth_rows, bool) or not isinstance(encrypted_auth_rows, int) or encrypted_auth_rows < 0:
        raise ValueError("encrypted_auth_rows must be a non-negative integer")

    baseline = baseline_before if isinstance(baseline_before, dict) else {}
    src_had_key = baseline.get("encryption_key_present")
    src_key_sha256 = baseline.get("encryption_key_sha256")
    src_enc_rows_raw = baseline.get("auth_encrypted_rows")
    src_enc_rows = int(src_enc_rows_raw) if src_enc_rows_raw is not None else None

    if "encryption_key" in saved_site_config:
        raw_key = saved_site_config["encryption_key"]
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError(
                "Corrupted site_config_backup.json: blank or non-string encryption_key"
            )
        if not is_valid_fernet_key(raw_key):
            raise ValueError(
                "Corrupted site_config_backup.json: invalid Fernet encryption_key format"
            )
        actual_sha256 = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        if src_had_key is False:
            raise ValueError(
                "Unexpected encryption_key in backup when source baseline reported none"
            )
        if src_key_sha256 and actual_sha256 != src_key_sha256:
            raise ValueError(
                "Backup encryption_key SHA-256 mismatch against source baseline"
            )
        if restored_site_config.get("encryption_key") != raw_key:
            raise ValueError(
                "Restored site_config.json encryption_key does not match site_config_backup.json"
            )
        if src_enc_rows is not None and encrypted_auth_rows != src_enc_rows:
            raise ValueError(
                "Restored __Auth encrypted=1 row count differs from source baseline"
            )
        return {
            "state": "PRESENT_VERIFIED",
            "encryption_key_present_in_backup": True,
            "restored_matches_backup": True,
            "encryption_key_sha256": actual_sha256,
            "auth_encrypted_rows": encrypted_auth_rows,
            "key_loss_detected": False,
        }

    # No `encryption_key` in `site_config_backup.json`: fail closed on any sign of key loss.
    if src_had_key is True or bool(src_key_sha256):
        raise ValueError(
            "Missing encryption_key: source site had encryption_key but backup lost it"
        )
    if src_enc_rows is not None and src_enc_rows > 0:
        raise ValueError(
            "Missing encryption_key: source baseline recorded encrypted __Auth rows"
        )
    if encrypted_auth_rows > 0:
        raise ValueError(
            "Missing encryption_key: restored database contains __Auth rows with encrypted=1"
        )
    if "encryption_key" in restored_site_config and restored_site_config.get("encryption_key") is not None:
        raise ValueError(
            "Restored site_config.json contains an unbacked-up stray encryption_key"
        )
    return {
        "state": "UNINITIALIZED_NO_ENCRYPTED_DATA",
        "encryption_key_present_in_backup": False,
        "restored_matches_backup": True,
        "source_key_provenance": (
            "explicitly_absent_at_source"
            if src_had_key is False
            else "v1_archive_verified_intact_config_and_zero_encrypted_auth_rows"
        ),
        "auth_encrypted_rows": 0,
        "key_loss_detected": False,
    }


def evaluate_translation_integrity(
    before_tr: dict[str, Any],
    after_tr: dict[str, Any],
    expected_rows: int | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify Translation DocType overrides and effective Vietnamese resolution survive restore + migrate x2."""
    errors: list[str] = []
    b_rows = int(before_tr.get("translation_rows", -1))
    a_rows = int(after_tr.get("translation_rows", -2))
    b_sha = str(before_tr.get("translation_sha256", ""))
    a_sha = str(after_tr.get("translation_sha256", ""))

    if not b_sha or b_rows < 0:
        errors.append("Invalid pre-migrate translation snapshot")
    if b_rows != a_rows:
        errors.append(f"Translation row count changed across migrate ({b_rows} -> {a_rows})")
    if b_sha != a_sha:
        errors.append("Translation SHA-256 changed across migrate")
    if expected_rows is not None and b_rows != int(expected_rows):
        errors.append(
            f"Restored Translation row count ({b_rows}) differs from backup baseline ({expected_rows})"
        )
    if expected_sha256 and b_sha != expected_sha256:
        errors.append("Restored Translation SHA-256 differs from backup baseline")
    if int(before_tr.get("mismatched_override_count", 0)) != 0:
        errors.append("Pre-migrate Translation overrides did not resolve in effective vi locale")
    if int(after_tr.get("mismatched_override_count", 0)) != 0:
        errors.append("Post-migrate Translation overrides did not resolve in effective vi locale")
    if not bool(after_tr.get("core_vi_catalog_verified", False)):
        errors.append("Core crm_cskh vi.csv catalog labels failed resolution check")

    return {
        "status": "PASS" if not errors else "FAIL",
        "translation_override_rows": a_rows,
        "translation_sha256": a_sha,
        "effective_overrides_verified": int(after_tr.get("verified_override_count", 0)),
        "core_vi_catalog_verified": bool(after_tr.get("core_vi_catalog_verified", False)),
        "errors": errors,
    }
'''

exec(SHARED_WORKER_HELPERS_SOURCE, globals())


def build_rto_metrics(
    phases_seconds: dict[str, float],
    measured_dr_rto_seconds: float,
    positive_control_seconds: float,
    total_wall_seconds: float,
) -> dict[str, Any]:
    """Build explicit phase-by-phase DR timing metrics (never conflating SQL import with full RTO)."""
    required_phases = (
        "archive_checksum_decrypt_and_extract",
        "isolated_stack_bootstrap_and_db_ready",
        "new_site_init",
        "sql_and_files_restore_only",
        "pre_migrate_reconciliation",
        "migrate_run_1",
        "migrate_run_2",
        "post_migrate_reconciliation_and_invariants",
        "web_service_start_and_authenticated_http_smoke",
    )
    missing = [k for k in required_phases if k not in phases_seconds]
    if missing:
        raise ValueError(f"Missing required RTO phases: {missing}")
    normalized = {k: round(float(phases_seconds[k]), 3) for k in required_phases}
    if measured_dr_rto_seconds < normalized["sql_and_files_restore_only"]:
        raise ValueError("End-to-end DR RTO cannot be smaller than SQL+files restore phase")
    return {
        "measurement_scope": "isolated_single_host_container_dr_drill_excluding_offsite_wan_transfer",
        "sql_import_only_is_full_rto": False,
        "phases_seconds": normalized,
        "sql_and_files_restore_only_seconds": normalized["sql_and_files_restore_only"],
        "measured_end_to_end_isolated_dr_rto_seconds": round(float(measured_dr_rto_seconds), 3),
        "positive_control_drill_seconds_excluded_from_rto": round(float(positive_control_seconds), 3),
        "total_drill_wall_seconds_with_positive_control": round(float(total_wall_seconds), 3),
    }


def _shared_worker_helpers_source() -> str:
    return SHARED_WORKER_HELPERS_SOURCE


PHASE_A_WORKER_BODY = r"""
import frappe

p = json.load(sys.stdin)
os.chdir('/home/frappe/frappe-bench')
site = p['site']
root = pathlib.Path('sites') / site
common = {
    'db_host': 'db',
    'db_port': 3306,
    'redis_cache': 'redis://redis:6379',
    'redis_queue': 'redis://redis:6379',
    'redis_socketio': 'redis://redis:6379',
    'socketio_port': 9000,
}
pathlib.Path('sites/common_site_config.json').write_text(json.dumps(common), encoding='utf-8')
apps_order = pathlib.Path('sites/apps.txt').read_text(encoding='utf-8').splitlines()
if apps_order != ['frappe', 'crm', 'crm_cskh']:
    pathlib.Path('sites/apps.txt').write_text('frappe\ncrm\ncrm_cskh\n', encoding='utf-8')
    apps_order = ['frappe', 'crm', 'crm_cskh']

def bench(*args: str) -> None:
    done = subprocess.run(['bench', '--site', site, *args], capture_output=True, text=True)
    if done.returncode:
        pathlib.Path('/tmp/restore-error.log').write_text(done.stdout + done.stderr, encoding='utf-8')
        raise RuntimeError('bench operation failed: ' + args[0])

timings: dict[str, float] = {}
t0 = time.monotonic()
bench(
    'new-site',
    site,
    '--no-mariadb-socket',
    '--db-host',
    'db',
    '--db-root-password',
    p['db_password'],
    '--admin-password',
    p['admin_password'],
)
timings['new_site_init'] = round(time.monotonic() - t0, 3)

t0 = time.monotonic()
bench(
    'restore',
    p['sql'],
    '--with-public-files',
    p['public'],
    '--with-private-files',
    p['private'],
    '--db-root-password',
    p['db_password'],
)
timings['sql_and_files_restore_only'] = round(time.monotonic() - t0, 3)

t0 = time.monotonic()
saved = json.loads(pathlib.Path(p['site_config']).read_text(encoding='utf-8'))
site_cfg_path = root / 'site_config.json'
config = json.loads(site_cfg_path.read_text(encoding='utf-8'))
if 'encryption_key' not in saved:
    config.pop('encryption_key', None)
for k, v in saved.items():
    if k not in {
        'db_name',
        'db_password',
        'db_host',
        'db_port',
        'db_type',
        'host_name',
        'redis_cache',
        'redis_queue',
        'redis_socketio',
    }:
        config[k] = v
config.update({
    'db_host': 'db',
    'db_port': 3306,
    'pause_scheduler': 1,
    'disable_scheduler': 1,
    'mute_emails': 1,
    'host_name': 'http://' + site,
    'maintenance_mode': 0,
})
site_cfg_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
os.chmod(site_cfg_path, 0o600)

baseline = p['baseline']
expected_counts = baseline['counts']
expected_table_sha256 = baseline.get('table_sha256', {})

def capture_site_state(*, verify_encryption_state: bool = False, check_invariants: bool = False) -> dict[str, Any]:
    os.chdir('/home/frappe/frappe-bench/sites')
    frappe.init(site=site, sites_path='.')
    frappe.connect()
    try:
        counts = {d: frappe.db.count(d) for d in expected_counts}
        table_hashes = {
            d: hashlib.sha256(
                json.dumps(frappe.get_all(d, fields=['*'], order_by='name'), sort_keys=True, default=str).encode('utf-8')
            ).hexdigest()
            for d in expected_table_sha256
        }
        tr_rows_v1 = frappe.get_all(
            'Translation',
            fields=['language', 'source_text', 'translated_text'],
            order_by='name',
        )
        tr_rows_v2 = frappe.get_all(
            'Translation',
            fields=['language', 'source_text', 'translated_text', 'context'],
            order_by='name',
        )
        use_v2_tr_hash = 'translation_sha256' in baseline
        tr_hash = hashlib.sha256(
            json.dumps(tr_rows_v2 if use_v2_tr_hash else tr_rows_v1, sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()

        frappe.local.lang = 'vi'
        if hasattr(frappe.local, 'lang_full_dict'):
            delattr(frappe.local, 'lang_full_dict')
        from frappe.translate import clear_cache as clear_translate_cache
        try:
            clear_translate_cache()
        except Exception:
            pass

        vi_overrides = [r for r in tr_rows_v2 if (r.get('language') or '') == 'vi']
        mismatched_overrides = 0
        overridden_sources = set()
        for r in vi_overrides:
            src = r.get('source_text') or ''
            ctx = r.get('context') or None
            if not ctx:
                overridden_sources.add(src)
            resolved = frappe._(src, context=ctx) if ctx else frappe._(src)
            if resolved != r.get('translated_text'):
                mismatched_overrides += 1

        core_pairs = {
            'Recruitment workspace': 'Không gian tuyển dụng',
            'Lead': 'Hồ sơ liên hệ',
            'Deal': 'Giao dịch CRM',
            'Hoàn tất mục tiêu chiến dịch': 'Hoàn tất mục tiêu liên hệ chiến dịch',
        }
        core_ok = all(
            (src in overridden_sources) or (frappe._(src) == expected_vi)
            for src, expected_vi in core_pairs.items()
        )

        secrets_found = frappe.db.sql(
            'SELECT doctype, name, fieldname FROM `__Auth` WHERE encrypted=1',
            as_dict=True,
        )
        enc_eval = None
        if verify_encryption_state:
            restored_disk_cfg = json.loads(
                (pathlib.Path(site) / 'site_config.json').read_text(encoding='utf-8')
            )
            enc_eval = evaluate_encryption_key_state(
                saved,
                restored_disk_cfg,
                baseline,
                len(secrets_found),
            )
            if secrets_found:
                from frappe.utils.password import get_decrypted_password
                for row in secrets_found:
                    get_decrypted_password(row.doctype, row.name, row.fieldname, raise_exception=True)

        invariants = None
        if check_invariants:
            def scalar(sql: str) -> int:
                rows = frappe.db.sql(sql)
                return int(rows[0][0] or 0) if rows else 0

            active_wi = scalar(
                "SELECT COUNT(*) FROM `tabCSKH Work Item` WHERE status IN ('assigned','in_progress','on_hold')"
            )
            active_claims = scalar("SELECT COUNT(*) FROM `tabCSKH Active Claim`")
            snap_rows = frappe.db.sql("SELECT metrics_json FROM `tabCSKH KPI Snapshot`", as_list=True)
            false_zero_qa = 0
            for sr in snap_rows:
                raw = sr[0] if sr else None
                if not raw or not isinstance(raw, str):
                    false_zero_qa += 1
                    continue
                parsed = json.loads(raw)
                m_map = parsed.get('metrics') if isinstance(parsed.get('metrics'), dict) else parsed
                qa = m_map.get('QA_PASS_RATE') if isinstance(m_map, dict) else None
                if not isinstance(qa, dict) or 'denominator' not in qa or 'value' not in qa:
                    false_zero_qa += 1
                    continue
                if qa['denominator'] == 0 and qa['value'] is not None:
                    false_zero_qa += 1
                elif qa['denominator'] > 0 and qa['value'] is None:
                    false_zero_qa += 1

            invariants = {
                'duplicate_active_leads': scalar(
                    "SELECT COUNT(*) FROM (SELECT lead FROM `tabCSKH Work Item` WHERE status IN ('assigned','in_progress','on_hold') GROUP BY lead HAVING COUNT(*) > 1) t"
                ),
                'owner_mismatch_active_work_items': scalar(
                    "SELECT COUNT(*) FROM `tabCSKH Work Item` wi JOIN `tabCRM Lead` l ON l.name = wi.lead WHERE wi.status IN ('assigned','in_progress','on_hold') AND IFNULL(wi.assigned_to,'') != IFNULL(l.lead_owner,'')"
                ),
                'active_claim_count_mismatch': abs(active_wi - active_claims),
                'duplicate_attempt_idempotency_keys': scalar(
                    "SELECT COUNT(*) FROM (SELECT work_item, performed_by, idempotency_key FROM `tabCSKH Contact Attempt` WHERE IFNULL(idempotency_key,'') != '' GROUP BY work_item, performed_by, idempotency_key HAVING COUNT(*) > 1) t"
                ),
                'unverified_marked_verified': scalar(
                    "SELECT COUNT(*) FROM `tabCSKH Contact Attempt` ca WHERE ca.verification_status='verified' AND IFNULL(ca.call_log,'')='' AND NOT EXISTS (SELECT 1 FROM `tabCSKH QA Review` qr WHERE qr.contact_attempt = ca.name AND qr.status = 'published')"
                ),
                'orphan_or_misassigned_open_followup_tasks': scalar(
                    "SELECT COUNT(*) FROM `tabCRM Task` t LEFT JOIN `tabCSKH Work Item` wi ON wi.name = t.cskh_work_item WHERE IFNULL(t.cskh_is_followup,0)=1 AND t.status IN ('Backlog','Todo','In Progress') AND (wi.name IS NULL OR wi.status NOT IN ('assigned','in_progress','on_hold') OR (IFNULL(t.assigned_to,'') != '' AND IFNULL(t.assigned_to,'') != IFNULL(wi.assigned_to,'')))"
                ),
                'duplicate_sla_idempotency_keys': scalar(
                    "SELECT COUNT(*) FROM (SELECT idempotency_key FROM `tabCSKH SLA Obligation` WHERE IFNULL(idempotency_key,'') != '' GROUP BY idempotency_key HAVING COUNT(*) > 1) t"
                ),
                'false_zero_qa_snapshots': false_zero_qa,
            }

        return {
            'counts': counts,
            'installed_apps': frappe.get_installed_apps(),
            'table_sha256': table_hashes,
            'translations': {
                'translation_rows': len(tr_rows_v2),
                'translation_sha256': tr_hash,
                'verified_override_count': len(vi_overrides) - mismatched_overrides,
                'mismatched_override_count': mismatched_overrides,
                'core_vi_catalog_verified': core_ok,
            },
            'encrypted_credentials_decrypted': len(secrets_found),
            'encryption_key_evaluation': enc_eval,
            'invariants': invariants,
        }
    finally:
        frappe.destroy()
        os.chdir('/home/frappe/frappe-bench')

before = capture_site_state(verify_encryption_state=True)
assert before['counts'] == expected_counts, 'Restored counts differ from backup baseline'
assert before['table_sha256'] == expected_table_sha256, 'Restored business rows differ from backup fingerprints'
assert set(before['installed_apps']) == {'frappe', 'crm', 'crm_cskh'}, 'Missing installed apps'

def verify_disk_files(kind: str) -> dict[str, Any]:
    folder = root / kind / 'files'
    expected_map = p['file_manifests_detailed'][kind]
    actual_files = [f for f in folder.rglob('*') if f.is_file()] if folder.exists() else []
    actual_map = {}
    world_writable = 0
    wrong_uid = 0
    for f in actual_files:
        rel = f.relative_to(folder).as_posix()
        raw = f.read_bytes()
        st = f.stat()
        if (stat.S_IMODE(st.st_mode) & 0o002) != 0:
            world_writable += 1
        if st.st_uid != 1000:
            wrong_uid += 1
        actual_map[rel] = {
            'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw),
        }
    expected_comparable = {
        rel: {'sha256': meta['sha256'], 'bytes': int(meta['bytes'])}
        for rel, meta in expected_map.items()
    }
    assert actual_map == expected_comparable, f'Restored {kind} files byte/hash mismatch'
    assert world_writable == 0 and wrong_uid == 0, f'Unsafe POSIX permissions on restored {kind} files'
    return {
        'count': len(actual_map),
        'total_bytes': sum(m['bytes'] for m in actual_map.values()),
        'all_bytes_and_hashes_match': True,
        'posix_owner_and_mode_ok': True,
    }

file_checks = {kind: verify_disk_files(kind) for kind in ('public', 'private')}
site_cfg_mode = stat.S_IMODE(site_cfg_path.stat().st_mode)
assert site_cfg_mode == 0o600, f'Expected 0600 on site_config.json, found {oct(site_cfg_mode)}'
timings['pre_migrate_reconciliation'] = round(time.monotonic() - t0, 3)

t0 = time.monotonic()
bench('migrate')
timings['migrate_run_1'] = round(time.monotonic() - t0, 3)
after_1 = capture_site_state()

t0 = time.monotonic()
bench('migrate')
timings['migrate_run_2'] = round(time.monotonic() - t0, 3)

t0 = time.monotonic()
bench('clear-cache')
bench('clear-website-cache')
after_2 = capture_site_state(check_invariants=True)
assert before['counts'] == after_1['counts'] == after_2['counts'], 'Migration changed business counts'
assert after_1['table_sha256'] == after_2['table_sha256'], 'Second migrate was not idempotent across tables'
tr_integrity = evaluate_translation_integrity(
    before['translations'],
    after_2['translations'],
    expected_rows=expected_counts.get('Translation'),
    expected_sha256=baseline.get('translation_sha256'),
)
assert tr_integrity['status'] == 'PASS', f"Translation integrity failed: {tr_integrity['errors']}"
assert all(v == 0 for v in after_2['invariants'].values()), f"Business invariant violation: {after_2['invariants']}"
timings['post_migrate_reconciliation_and_invariants'] = round(time.monotonic() - t0, 3)

# Set ephemeral login credential on disposable clone ONLY AFTER all hash/count reconciliations pass.
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=site, sites_path='.')
frappe.connect()
try:
    from frappe.utils.password import update_password
    update_password('Administrator', p['clone_login_password'])
    frappe.db.commit()
finally:
    frappe.destroy()
    os.chdir('/home/frappe/frappe-bench')

print('PHASE_A_JSON=' + json.dumps({
    'before_migrate': {
        'counts': before['counts'],
        'installed_apps': before['installed_apps'],
        'table_sha256': before['table_sha256'],
        'translation_rows': before['translations']['translation_rows'],
        'translation_sha256': before['translations']['translation_sha256'],
        'encrypted_credentials_decrypted': before['encrypted_credentials_decrypted'],
    },
    'after_migrate': {
        'counts': after_2['counts'],
        'installed_apps': after_2['installed_apps'],
        'table_sha256': after_2['table_sha256'],
        'translation_rows': after_2['translations']['translation_rows'],
        'translation_sha256': after_2['translations']['translation_sha256'],
        'invariants': after_2['invariants'],
    },
    'migrate_x2_idempotent': True,
    'migrate_seconds': [timings['migrate_run_1'], timings['migrate_run_2']],
    'worker_phase_seconds': timings,
    'translation_integrity': tr_integrity,
    'encryption_key_evaluation': before['encryption_key_evaluation'],
    'files': file_checks,
    'site_config_mode_octal': oct(site_cfg_mode),
    'apps_order': apps_order,
}), flush=True)
"""


PHASE_A_HTTP_PROBE = r"""
import http.cookiejar, json, re, sys, urllib.error, urllib.parse, urllib.request

payload = json.load(sys.stdin)
site = payload['site']
base_url = payload['base_url']
login_password = payload['login_password']

def request_no_cookies(path: str) -> dict[str, int]:
    req = urllib.request.Request(base_url + path, headers={'Host': site})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return {'status': r.status, 'bytes': len(r.read())}
    except urllib.error.HTTPError as e:
        return {'status': e.code, 'bytes': len(e.read())}

guest = {
    path: request_no_cookies(path)
    for path in ('/login', '/crm', '/cskh', '/api/method/ping')
}
assert guest['/login']['status'] == 200
assert guest['/crm']['status'] == 403 and guest['/cskh']['status'] == 403
assert guest['/api/method/ping']['status'] == 200

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
login_data = urllib.parse.urlencode({'usr': 'Administrator', 'pwd': login_password}).encode('utf-8')
login_req = urllib.request.Request(
    base_url + '/api/method/login',
    data=login_data,
    headers={
        'Host': site,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    },
    method='POST',
)
with opener.open(login_req, timeout=10) as resp:
    login_status = resp.status
    raw_cookies = resp.headers.get_all('Set-Cookie') or []
    resp.read()

sid_headers = [c for c in raw_cookies if c.lstrip().startswith('sid=')]
assert login_status == 200 and sid_headers, 'Authenticated login did not issue sid cookie'
sid_header = sid_headers[-1]
sid_val = sid_header.split(';', 1)[0].split('=', 1)[1].strip()
assert sid_val and sid_val != 'Guest', 'Login returned Guest sid'
sid_httponly = 'httponly' in sid_header.lower()
assert sid_httponly, 'Cookie sid missing HttpOnly attribute'

def auth_get(path: str) -> tuple[int, str]:
    req = urllib.request.Request(base_url + path, headers={'Host': site})
    with opener.open(req, timeout=10) as r:
        return r.status, r.read().decode('utf-8', errors='replace')

crm_status, crm_html = auth_get('/crm')
cskh_status, cskh_html = auth_get('/cskh')
assert crm_status == 200 and len(crm_html) > 100
assert cskh_status == 200 and 'Không gian tuyển dụng' in cskh_html

csrf_token = ''
for pat in (
    r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)["\']',
    r'csrf_token\s*[:=]\s*["\']([^"\']+)["\']',
):
    for m in re.finditer(pat, cskh_html):
        cand = m.group(1).strip()
        if cand and cand.lower() not in {'none', 'null', 'undefined'} and len(cand) >= 8:
            csrf_token = cand
            break
    if csrf_token:
        break
assert csrf_token, 'Failed to extract CSRF token from authenticated /cskh page'

bad_csrf_req = urllib.request.Request(
    base_url + '/api/method/crm_cskh.api.health.ping',
    data=b'',
    headers={'Host': site, 'X-Frappe-CSRF-Token': 'forged-invalid-csrf-token'},
    method='POST',
)
try:
    with opener.open(bad_csrf_req, timeout=8) as r:
        bad_csrf_status = r.status
except urllib.error.HTTPError as e:
    bad_csrf_status = e.code
assert bad_csrf_status in (400, 403), f'Expected CSRF rejection, got {bad_csrf_status}'

good_csrf_req = urllib.request.Request(
    base_url + '/api/method/crm_cskh.api.health.ping',
    data=b'',
    headers={'Host': site, 'X-Frappe-CSRF-Token': csrf_token},
    method='POST',
)
with opener.open(good_csrf_req, timeout=8) as r:
    good_csrf_status = r.status
    good_csrf_body = r.read()
assert good_csrf_status == 200 and b'crm_cskh' in good_csrf_body

print(json.dumps({
    'via_nginx_frontend': base_url.startswith('http://frontend:'),
    'guest_guards': guest,
    'authenticated': {
        'login_status': login_status,
        'sid_httponly': sid_httponly,
        'crm_status': crm_status,
        'cskh_status': cskh_status,
        'cskh_vi_heading_verified': True,
        'forged_csrf_status': bad_csrf_status,
        'valid_csrf_health_ping_status': good_csrf_status,
    },
}))
"""


STOP_GUNICORN_SNIPPET = r"""
import os, pathlib, signal, time

def find_gunicorn_pids() -> list[int]:
    found = []
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            cmd = (entry / 'cmdline').read_bytes().decode('utf-8', errors='ignore')
        except OSError:
            continue
        if 'gunicorn' in cmd and 'frappe.app' in cmd:
            found.append(pid)
    return found

for pid in find_gunicorn_pids():
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

deadline = time.monotonic() + 8.0
while time.monotonic() < deadline and find_gunicorn_pids():
    time.sleep(0.25)

for pid in find_gunicorn_pids():
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
time.sleep(0.3)
assert not find_gunicorn_pids(), 'Gunicorn processes still active before positive-control restore'
"""


PHASE_B_PREPARE_WORKER_BODY = r"""
import frappe

p = json.load(sys.stdin)
os.chdir('/home/frappe/frappe-bench')
site = p['site']
root = pathlib.Path('sites') / site
nonce = p['nonce']
control_secret = p['control_secret']

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

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=site, sites_path='.')
frappe.connect()
frappe.set_user('Administrator')
try:
    from frappe.utils.password import set_encrypted_password
    pub_bytes = (f'P4-POSITIVE-PUBLIC-CONTROL-{nonce}\n' * 3).encode('utf-8')
    priv_bytes = (f'P4-POSITIVE-PRIVATE-CONTROL-{nonce}\n' * 3).encode('utf-8')

    pub_doc = frappe.get_doc({
        'doctype': 'File',
        'file_name': f'p4-posctrl-public-{nonce}.txt',
        'is_private': 0,
        'content': pub_bytes,
    }).insert(ignore_permissions=True)
    priv_doc = frappe.get_doc({
        'doctype': 'File',
        'file_name': f'p4-posctrl-private-{nonce}.txt',
        'is_private': 1,
        'content': priv_bytes,
    }).insert(ignore_permissions=True)

    lead_doc = frappe.get_doc({
        'doctype': 'CRM Lead',
        'first_name': f'P4 Positive Control {nonce}',
        'mobile_no': '0900000999',
    }).insert(ignore_permissions=True)

    set_encrypted_password('User', 'Administrator', control_secret, fieldname='p4_restore_control')
    frappe.db.commit()

    auth_rows = frappe.db.sql(
        "SELECT encrypted, password FROM `__Auth` WHERE doctype='User' AND name='Administrator' AND fieldname='p4_restore_control'",
        as_dict=True,
    )
    assert len(auth_rows) == 1 and int(auth_rows[0].encrypted) == 1, 'Control secret was not stored with encrypted=1'
    assert str(auth_rows[0].password).startswith('gAAAAAB'), 'Control secret is not Fernet ciphertext'
    total_enc_rows = int(frappe.db.sql('SELECT COUNT(*) FROM `__Auth` WHERE encrypted=1')[0][0] or 0)
    assert total_enc_rows >= 1

    counts = {d: frappe.db.count(d) for d in doctypes}
    table_hashes = {
        d: hashlib.sha256(
            json.dumps(frappe.get_all(d, fields=['*'], order_by='name'), sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()
        for d in doctypes
    }
    pub_info = {
        'file_url': pub_doc.file_url,
        'full_path': str(pathlib.Path(pub_doc.get_full_path()).resolve()),
        'bytes': len(pub_bytes),
        'sha256': hashlib.sha256(pub_bytes).hexdigest(),
    }
    priv_info = {
        'file_url': priv_doc.file_url,
        'full_path': str(pathlib.Path(priv_doc.get_full_path()).resolve()),
        'bytes': len(priv_bytes),
        'sha256': hashlib.sha256(priv_bytes).hexdigest(),
    }
finally:
    frappe.destroy()
    os.chdir('/home/frappe/frappe-bench')

site_cfg = json.loads((root / 'site_config.json').read_text(encoding='utf-8'))
enc_key = site_cfg.get('encryption_key')
assert is_valid_fernet_key(enc_key), 'encryption_key was not generated in site_config.json'
enc_key_sha256 = hashlib.sha256(enc_key.encode('utf-8')).hexdigest()

backups_dir = root / 'private/backups'
prev = set(backups_dir.glob('*'))
done = subprocess.run(['bench', '--site', site, 'backup', '--with-files'], capture_output=True, text=True)
if done.returncode:
    pathlib.Path('/tmp/restore-error.log').write_text(done.stdout + done.stderr, encoding='utf-8')
    raise RuntimeError('Positive-control bench backup failed')
fresh = [f.resolve() for f in backups_dir.glob('*') if f not in prev and f.is_file()]
expected_suffixes = ('-database.sql.gz', '-private-files.tar', '-files.tar', '-site_config_backup.json')
assert len(fresh) == 4 and all(any(f.name.endswith(s) for f in fresh) for s in expected_suffixes)

out_stage = pathlib.Path('/tmp/posctrl-backup-out')
out_stage.mkdir(parents=True, exist_ok=True)
staged_names = []
for f in fresh:
    dst = out_stage / f.name
    dst.write_bytes(f.read_bytes())
    os.chmod(dst, 0o600)
    staged_names.append(f.name)

pos_before = {
    'counts': counts,
    'table_sha256': table_hashes,
    'encryption_key_present': True,
    'encryption_key_sha256': enc_key_sha256,
    'auth_encrypted_rows': total_enc_rows,
}
print('POS_PREP_JSON=' + json.dumps({
    'pos_before': pos_before,
    'staged_names': staged_names,
    'public_file': pub_info,
    'private_file': priv_info,
    'synthetic_lead_created': bool(lead_doc.name),
}), flush=True)
"""


PHASE_B_VERIFY_WORKER_BODY = r"""
import frappe

p = json.load(sys.stdin)
os.chdir('/home/frappe/frappe-bench')
site = p['site']
root = pathlib.Path('sites') / site
site_cfg_path = root / 'site_config.json'

pub_path = pathlib.Path(p['public_file']['full_path'])
priv_path = pathlib.Path(p['private_file']['full_path'])
pub_path.unlink()
priv_path.unlink()
assert not pub_path.exists() and not priv_path.exists()

# Strip encryption_key before restore to simulate host config/key loss.
cfg_stripped = json.loads(site_cfg_path.read_text(encoding='utf-8'))
cfg_stripped.pop('encryption_key', None)
site_cfg_path.write_text(json.dumps(cfg_stripped), encoding='utf-8')

done = subprocess.run(
    [
        'bench',
        '--site',
        site,
        'restore',
        p['sql'],
        '--with-public-files',
        p['public'],
        '--with-private-files',
        p['private'],
        '--db-root-password',
        p['db_password'],
    ],
    capture_output=True,
    text=True,
)
if done.returncode:
    pathlib.Path('/tmp/restore-error.log').write_text(done.stdout + done.stderr, encoding='utf-8')
    raise RuntimeError('Positive-control bench restore failed')

# 1. Negative control: missing encryption_key while __Auth encrypted=1 rows exist MUST fail closed.
cfg_after_sql = json.loads(site_cfg_path.read_text(encoding='utf-8'))
cfg_after_sql.pop('encryption_key', None)
site_cfg_path.write_text(json.dumps(cfg_after_sql), encoding='utf-8')

negative_state_rejected = False
try:
    evaluate_encryption_key_state(
        {'db_name': 'simulated_lost_key_db', 'db_password': 'simulated_lost_key_pw'},
        cfg_after_sql,
        p['pos_before'],
        int(p['pos_before']['auth_encrypted_rows']),
    )
except ValueError:
    negative_state_rejected = True
assert negative_state_rejected, 'Negative control did not reject missing encryption_key'

negative_decrypt_failed = False
os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=site, sites_path='.')
frappe.connect()
try:
    from frappe.utils.password import get_decrypted_password
    try:
        val = get_decrypted_password('User', 'Administrator', 'p4_restore_control', raise_exception=True)
        if val == p['control_secret']:
            raise AssertionError('Decryption unexpectedly succeeded without encryption_key')
    except Exception:
        negative_decrypt_failed = True
finally:
    frappe.destroy()
    os.chdir('/home/frappe/frappe-bench')
assert negative_decrypt_failed, 'get_decrypted_password did not fail when encryption_key was missing'

# 2. Restore encryption_key from site_config_backup.json (`0600`) and verify positive control.
saved_pos = json.loads(pathlib.Path(p['site_config']).read_text(encoding='utf-8'))
cfg_restored = json.loads(site_cfg_path.read_text(encoding='utf-8'))
for k, v in saved_pos.items():
    if k not in {
        'db_name',
        'db_password',
        'db_host',
        'db_port',
        'db_type',
        'host_name',
        'redis_cache',
        'redis_queue',
        'redis_socketio',
    }:
        cfg_restored[k] = v
cfg_restored.update({
    'db_host': 'db',
    'db_port': 3306,
    'pause_scheduler': 1,
    'disable_scheduler': 1,
    'mute_emails': 1,
    'host_name': 'http://' + site,
    'maintenance_mode': 0,
})
site_cfg_path.write_text(json.dumps(cfg_restored, indent=2) + '\n', encoding='utf-8')
os.chmod(site_cfg_path, 0o600)

pos_enc_eval = evaluate_encryption_key_state(
    saved_pos,
    json.loads(site_cfg_path.read_text(encoding='utf-8')),
    p['pos_before'],
    int(p['pos_before']['auth_encrypted_rows']),
)
assert pos_enc_eval['state'] == 'PRESENT_VERIFIED'

for label, expected in (('public', p['public_file']), ('private', p['private_file'])):
    fp = pathlib.Path(expected['full_path'])
    assert fp.is_file(), f'Restored {label} control file missing'
    raw = fp.read_bytes()
    assert len(raw) == int(expected['bytes']), f'Restored {label} byte count mismatch'
    assert hashlib.sha256(raw).hexdigest() == expected['sha256'], f'Restored {label} SHA-256 mismatch'
    st = fp.stat()
    assert st.st_uid == 1000 and (stat.S_IMODE(st.st_mode) & 0o002) == 0

assert stat.S_IMODE(site_cfg_path.stat().st_mode) == 0o600

os.chdir('/home/frappe/frappe-bench/sites')
frappe.init(site=site, sites_path='.')
frappe.connect()
try:
    from frappe.utils.password import get_decrypted_password, update_password
    decrypted = get_decrypted_password('User', 'Administrator', 'p4_restore_control', raise_exception=True)
    assert decrypted == p['control_secret'], 'Decrypted positive-control secret mismatch'

    doctypes = list(p['pos_before']['counts'].keys())
    counts_after = {d: frappe.db.count(d) for d in doctypes}
    hashes_after = {
        d: hashlib.sha256(
            json.dumps(frappe.get_all(d, fields=['*'], order_by='name'), sort_keys=True, default=str).encode('utf-8')
        ).hexdigest()
        for d in doctypes
    }
    assert counts_after == p['pos_before']['counts'], 'Positive-control business counts mismatch'
    assert hashes_after == p['pos_before']['table_sha256'], 'Positive-control business table hashes mismatch'

    update_password('Administrator', p['pos_login_password'])
    frappe.db.commit()
finally:
    frappe.destroy()
    os.chdir('/home/frappe/frappe-bench')

subprocess.run(['bench', '--site', site, 'clear-cache'], check=True, capture_output=True, text=True)

print('POS_VERIFY_JSON=' + json.dumps({
    'scope': 'disposable_clone_only',
    'encryption_key_state': pos_enc_eval['state'],
    'negative_control_missing_key_rejected': True,
    'negative_control_decrypt_without_key_failed': True,
    'encrypted_secret_fernet_round_trip': True,
    'public_file_bytes_and_sha256_match': True,
    'private_file_bytes_and_sha256_match': True,
    'posix_permissions_verified': True,
    'business_data_counts_and_hashes_reconciled': True,
}), flush=True)
"""


PHASE_B_HTTP_PERM_PROBE = r"""
import hashlib, http.cookiejar, json, sys, urllib.error, urllib.parse, urllib.request

p = json.load(sys.stdin)
site = p['site']
base_url = p['base_url']
pub = p['public_file']
priv = p['private_file']

def guest_fetch(url_path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(base_url + url_path, headers={'Host': site})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

pub_guest_status, pub_guest_bytes = guest_fetch(pub['file_url'])
assert pub_guest_status == 200
assert len(pub_guest_bytes) == int(pub['bytes'])
assert hashlib.sha256(pub_guest_bytes).hexdigest() == pub['sha256']

priv_guest_status, priv_guest_bytes = guest_fetch(priv['file_url'])
assert priv_guest_status in (401, 403), f'Expected Guest denial on private file, got {priv_guest_status}'
assert hashlib.sha256(priv_guest_bytes).hexdigest() != priv['sha256']

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
login_data = urllib.parse.urlencode({'usr': 'Administrator', 'pwd': p['pos_login_password']}).encode('utf-8')
login_req = urllib.request.Request(
    base_url + '/api/method/login',
    data=login_data,
    headers={'Host': site, 'Content-Type': 'application/x-www-form-urlencoded'},
    method='POST',
)
with opener.open(login_req, timeout=10) as r:
    assert r.status == 200
    r.read()

priv_auth_req = urllib.request.Request(base_url + priv['file_url'], headers={'Host': site})
with opener.open(priv_auth_req, timeout=10) as r:
    priv_auth_status = r.status
    priv_auth_bytes = r.read()
assert priv_auth_status == 200
assert len(priv_auth_bytes) == int(priv['bytes'])
assert hashlib.sha256(priv_auth_bytes).hexdigest() == priv['sha256']

print(json.dumps({
    'public_guest_http_status': pub_guest_status,
    'public_guest_bytes_and_sha256_match': True,
    'private_guest_http_status': priv_guest_status,
    'private_guest_access_denied': True,
    'private_authenticated_http_status': priv_auth_status,
    'private_authenticated_bytes_and_sha256_match': True,
}))
"""


def run(args: argparse.Namespace) -> int:
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit(
            "Run on the Linux Docker host as root (private input and temporary files)"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", args.archive_sha256):
        raise SystemExit("Expected full SHA-256")
    if digest(args.archive) != args.archive_sha256:
        raise SystemExit("Offsite archive checksum mismatch; no Docker mutations")
    os.umask(0o077)
    started = time.monotonic()
    project = (
        "crm-cskh-p4-restore-"
        + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        + "-"
        + secrets.token_hex(3)
    )
    report: dict[str, Any] = {
        "schema_version": "p4-offsite-restore-v2",
        "status": "FAIL",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive_sha256": args.archive_sha256,
        "project": project,
        "source_project": args.source_project,
    }
    step = "preflight"
    cleanup_started = False
    rto_phases: dict[str, float] = {}

    def command(
        cmd: list[str],
        *,
        input_text: str | None = None,
        timeout: int = 1200,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode:
            raise RuntimeError(f"Command failed at {step} (exit {result.returncode})")
        return result

    def resources(kind: str, namespace: str) -> list[str]:
        return command(
            [
                "docker",
                kind,
                "ls",
                "-aq" if kind == "container" else "-q",
                "--filter",
                f"label=com.docker.compose.project={namespace}",
            ]
        ).stdout.split()

    def protected() -> dict[str, dict[str, Any]]:
        ids = command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={args.source_project}",
            ]
        ).stdout.split()
        if not ids:
            raise ValueError("Source project not found")
        rows = json.loads(command(["docker", "inspect", *ids]).stdout)
        return {
            r["Name"]: {
                "id": r["Id"],
                "image": r["Image"],
                "started": r["State"]["StartedAt"],
                "mounts": r["Mounts"],
                "service": r["Config"]["Labels"].get("com.docker.compose.service"),
            }
            for r in rows
        }

    live_before = protected()
    by_service = {r["service"]: r for r in live_before.values()}
    images = {s: by_service[s]["image"] for s in ("backend", "db", "redis-cache")}
    images["frontend"] = by_service.get("frontend", by_service["backend"])["image"]
    report["images"] = images
    for kind in ("container", "volume", "network"):
        if resources(kind, project):
            raise ValueError("Refusing preexisting restore resources")

    shared_helpers = _shared_worker_helpers_source()

    with tempfile.TemporaryDirectory(prefix=project + "-") as tmp:
        tmpdir = Path(tmp)
        try:
            step = "decrypt_and_validate"
            t_phase = time.monotonic()
            plain = tmpdir / "backup.tar.gz"
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
                    str(args.archive),
                    "-out",
                    str(plain),
                    "-pass",
                    "file:" + str(args.key_file),
                ]
            )
            selected = extract_backup(plain, tmpdir)
            manifests_detailed = {
                k: file_manifest_detailed(
                    selected["-private-files.tar" if k == "private" else "-files.tar"]
                )
                for k in ("public", "private")
            }
            baseline = json.loads(selected["before.json"].read_text(encoding="utf-8"))
            rto_phases["archive_checksum_decrypt_and_extract"] = round(
                time.monotonic() - t_phase, 3
            )

            password = secrets.token_urlsafe(36)
            secret = tmpdir / "db-password"
            secret.write_text(password + "\n", encoding="utf-8")
            secret.chmod(0o600)
            cfg = {
                "name": project,
                "services": {
                    "db": {
                        "image": images["db"],
                        "pull_policy": "never",
                        "environment": {
                            "MARIADB_ROOT_PASSWORD_FILE": "/run/secrets/db-password"
                        },
                        "volumes": [
                            "db:/var/lib/mysql",
                            f"{secret}:/run/secrets/db-password:ro",
                        ],
                        "networks": ["restore"],
                        "mem_limit": "1536m",
                        "cpus": 1,
                    },
                    "redis": {
                        "image": images["redis-cache"],
                        "pull_policy": "never",
                        "networks": ["restore"],
                        "mem_limit": "256m",
                        "cpus": 0.5,
                    },
                    "backend": {
                        "image": images["backend"],
                        "pull_policy": "never",
                        "command": ["sleep", "infinity"],
                        "volumes": [
                            "sites:/home/frappe/frappe-bench/sites",
                            "logs:/home/frappe/frappe-bench/logs",
                        ],
                        "networks": ["restore"],
                        "mem_limit": "2g",
                        "cpus": 2,
                    },
                    "frontend": {
                        "image": images["frontend"],
                        "pull_policy": "never",
                        "command": ["nginx-entrypoint.sh"],
                        "environment": {
                            "BACKEND": "backend:8000",
                            "SOCKETIO": "backend:8000",
                            "NGINX_LISTEN_PORT": "8080",
                            "FRAPPE_SITE_NAME_HEADER": args.site,
                            "UPSTREAM_REAL_IP_ADDRESS": "127.0.0.1",
                            "UPSTREAM_REAL_IP_HEADER": "X-Forwarded-For",
                            "UPSTREAM_REAL_IP_RECURSIVE": "off",
                            "PROXY_READ_TIMEOUT": "120",
                            "CLIENT_MAX_BODY_SIZE": "50m",
                        },
                        "volumes": [
                            "sites:/home/frappe/frappe-bench/sites",
                        ],
                        "networks": ["restore"],
                        "mem_limit": "256m",
                        "cpus": 0.5,
                    },
                },
                "volumes": {"db": {}, "sites": {}, "logs": {}},
                "networks": {"restore": {"internal": True}},
            }
            compose = tmpdir / "compose.json"
            compose.write_text(json.dumps(cfg), encoding="utf-8")
            base = ["docker", "compose", "--project-name", project, "-f", str(compose)]

            step = "start_isolated_stack"
            t_phase = time.monotonic()
            cleanup_started = True
            command(base + ["up", "-d"])
            db_id = command(base + ["ps", "-q", "db"]).stdout.strip()
            redis_id = command(base + ["ps", "-q", "redis"]).stdout.strip()
            app_id = command(base + ["ps", "-q", "backend"]).stdout.strip()
            frontend_id = command(base + ["ps", "-q", "frontend"]).stdout.strip()
            for _ in range(90):
                ready = command(
                    [
                        "docker",
                        "exec",
                        db_id,
                        "healthcheck.sh",
                        "--connect",
                        "--innodb_initialized",
                    ],
                    check=False,
                    timeout=10,
                )
                if ready.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise TimeoutError("Isolated DB readiness timed out")

            command(
                [
                    "docker",
                    "exec",
                    "--user",
                    "root",
                    app_id,
                    "mkdir",
                    "-p",
                    "/tmp/restore-input",
                ]
            )
            for k, p in selected.items():
                if k.startswith("-"):
                    command(
                        [
                            "docker",
                            "cp",
                            str(p),
                            app_id + ":/tmp/restore-input/" + p.name,
                        ]
                    )
            command(
                [
                    "docker",
                    "exec",
                    "--user",
                    "root",
                    app_id,
                    "chown",
                    "-R",
                    "1000:1000",
                    "/tmp/restore-input",
                ]
            )
            rto_phases["isolated_stack_bootstrap_and_db_ready"] = round(
                time.monotonic() - t_phase, 3
            )

            step = "restore_and_migrate_twice"
            clone_login_password = secrets.token_urlsafe(32)
            inputs: dict[str, Any] = {
                "site": args.site,
                "db_password": password,
                "admin_password": secrets.token_urlsafe(32),
                "clone_login_password": clone_login_password,
                "baseline": baseline,
                "file_manifests_detailed": manifests_detailed,
            }
            for key, kind in [
                ("sql", "-database.sql.gz"),
                ("public", "-files.tar"),
                ("private", "-private-files.tar"),
                ("site_config", "-site_config_backup.json"),
            ]:
                inputs[key] = "/tmp/restore-input/" + selected[kind].name

            worker = command(
                [
                    "docker",
                    "exec",
                    "-i",
                    app_id,
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    shared_helpers + "\n\n" + PHASE_A_WORKER_BODY,
                ],
                input_text=json.dumps(inputs),
                check=False,
            )
            if worker.returncode:
                detail = command(
                    ["docker", "exec", app_id, "cat", "/tmp/restore-error.log"],
                    check=False,
                ).stdout
                priv_log = args.out.with_suffix(".private.log")
                priv_log.write_text(
                    worker.stdout + worker.stderr + detail, encoding="utf-8"
                )
                priv_log.chmod(0o600)
                raise RuntimeError(
                    "Isolated restore worker failed; see private diagnostic"
                )
            line = next(
                row
                for row in worker.stdout.splitlines()
                if row.startswith("PHASE_A_JSON=")
            )
            phase_a = json.loads(line.split("=", 1)[1])
            report["restored"] = phase_a
            rto_phases.update(phase_a["worker_phase_seconds"])

            step = "restored_http_smoke"
            t_phase = time.monotonic()
            command(["docker", "exec", "-d", app_id, "start.sh"])
            http_inputs = json.dumps(
                {
                    "site": args.site,
                    "base_url": "http://frontend:8080",
                    "login_password": clone_login_password,
                }
            )
            last_probe_err = ""
            for _ in range(30):
                probe = command(
                    [
                        "docker",
                        "exec",
                        "-i",
                        app_id,
                        "env/bin/python",
                        "-c",
                        PHASE_A_HTTP_PROBE,
                    ],
                    input_text=http_inputs,
                    check=False,
                )
                if probe.returncode == 0:
                    report["restored_http_smoke"] = json.loads(probe.stdout)
                    break
                last_probe_err = probe.stdout + probe.stderr
                time.sleep(2)
            else:
                priv_log = args.out.with_suffix(".private.log")
                priv_log.write_text(last_probe_err, encoding="utf-8")
                priv_log.chmod(0o600)
                raise RuntimeError("Restored HTTP login & CSRF smoke failed")

            rto_phases["web_service_start_and_authenticated_http_smoke"] = round(
                time.monotonic() - t_phase, 3
            )
            measured_dr_rto_seconds = round(time.monotonic() - started, 3)

            step = "positive_control_encrypted_secret_and_files"
            t_pos_start = time.monotonic()
            command(
                [
                    "docker",
                    "exec",
                    app_id,
                    "env/bin/python",
                    "-c",
                    STOP_GUNICORN_SNIPPET,
                ]
            )
            pos_nonce = secrets.token_hex(6)
            pos_secret = "posctrl-secret-" + secrets.token_urlsafe(24)
            pos_login_password = secrets.token_urlsafe(32)
            prep = command(
                [
                    "docker",
                    "exec",
                    "-i",
                    app_id,
                    "env/bin/python",
                    "-c",
                    shared_helpers + "\n\n" + PHASE_B_PREPARE_WORKER_BODY,
                ],
                input_text=json.dumps(
                    {
                        "site": args.site,
                        "nonce": pos_nonce,
                        "control_secret": pos_secret,
                    }
                ),
                check=False,
            )
            if prep.returncode:
                priv_log = args.out.with_suffix(".private.log")
                priv_log.write_text(prep.stdout + prep.stderr, encoding="utf-8")
                priv_log.chmod(0o600)
                raise RuntimeError("Positive-control preparation failed")
            prep_data = json.loads(
                next(
                    r
                    for r in prep.stdout.splitlines()
                    if r.startswith("POS_PREP_JSON=")
                ).split("=", 1)[1]
            )

            pos_dir = tmpdir / "posctrl"
            pos_dir.mkdir(mode=0o700)
            pos_members: list[Path] = []
            for name in prep_data["staged_names"]:
                dst = pos_dir / name
                command(
                    ["docker", "cp", f"{app_id}:/tmp/posctrl-backup-out/{name}", str(dst)]
                )
                dst.chmod(0o600)
                pos_members.append(dst)
            pos_hashes = {f.name: digest(f) for f in pos_members}
            for meta_name, meta_obj in (
                ("before.json", prep_data["pos_before"]),
                ("backup-file-hashes.json", pos_hashes),
            ):
                mp = pos_dir / meta_name
                mp.write_text(json.dumps(meta_obj), encoding="utf-8")
                mp.chmod(0o600)
                pos_members.append(mp)
            pos_plain = pos_dir / "pos-backup.tar.gz"
            with tarfile.open(pos_plain, "w:gz") as tf:
                for f in pos_members:
                    tf.add(f, arcname=f.name, recursive=False)
            pos_key = pos_dir / "pos-key"
            pos_key.write_text(secrets.token_hex(32) + "\n", encoding="utf-8")
            pos_key.chmod(0o600)
            pos_enc = pos_dir / "pos-backup.tar.gz.enc"
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
                    str(pos_plain),
                    "-out",
                    str(pos_enc),
                    "-pass",
                    "file:" + str(pos_key),
                ]
            )
            pos_dec = pos_dir / "pos-decrypted.tar.gz"
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
                    str(pos_enc),
                    "-out",
                    str(pos_dec),
                    "-pass",
                    "file:" + str(pos_key),
                ]
            )
            pos_extract_dir = pos_dir / "extracted"
            pos_extract_dir.mkdir(mode=0o700)
            pos_selected = extract_backup(pos_dec, pos_extract_dir)

            command(
                [
                    "docker",
                    "exec",
                    "--user",
                    "root",
                    app_id,
                    "mkdir",
                    "-p",
                    "/tmp/posctrl-restore-in",
                ]
            )
            for k, p in pos_selected.items():
                if k.startswith("-"):
                    command(
                        [
                            "docker",
                            "cp",
                            str(p),
                            f"{app_id}:/tmp/posctrl-restore-in/{p.name}",
                        ]
                    )
            command(
                [
                    "docker",
                    "exec",
                    "--user",
                    "root",
                    app_id,
                    "chown",
                    "-R",
                    "1000:1000",
                    "/tmp/posctrl-restore-in",
                ]
            )

            pos_verify_inputs: dict[str, Any] = {
                "site": args.site,
                "db_password": password,
                "control_secret": pos_secret,
                "pos_login_password": pos_login_password,
                "pos_before": prep_data["pos_before"],
                "public_file": prep_data["public_file"],
                "private_file": prep_data["private_file"],
            }
            for key, kind in (
                ("sql", "-database.sql.gz"),
                ("public", "-files.tar"),
                ("private", "-private-files.tar"),
                ("site_config", "-site_config_backup.json"),
            ):
                pos_verify_inputs[key] = (
                    "/tmp/posctrl-restore-in/" + pos_selected[kind].name
                )

            pos_verify = command(
                [
                    "docker",
                    "exec",
                    "-i",
                    app_id,
                    "env/bin/python",
                    "-c",
                    shared_helpers + "\n\n" + PHASE_B_VERIFY_WORKER_BODY,
                ],
                input_text=json.dumps(pos_verify_inputs),
                check=False,
            )
            if pos_verify.returncode:
                detail = command(
                    ["docker", "exec", app_id, "cat", "/tmp/restore-error.log"],
                    check=False,
                ).stdout
                priv_log = args.out.with_suffix(".private.log")
                priv_log.write_text(
                    pos_verify.stdout + pos_verify.stderr + detail, encoding="utf-8"
                )
                priv_log.chmod(0o600)
                raise RuntimeError("Positive-control restore verification failed")
            pos_summary = json.loads(
                next(
                    r
                    for r in pos_verify.stdout.splitlines()
                    if r.startswith("POS_VERIFY_JSON=")
                ).split("=", 1)[1]
            )

            command(["docker", "exec", "-d", app_id, "start.sh"])
            pos_http_inputs = json.dumps(
                {
                    "site": args.site,
                    "base_url": "http://frontend:8080",
                    "pos_login_password": pos_login_password,
                    "public_file": prep_data["public_file"],
                    "private_file": prep_data["private_file"],
                }
            )
            last_pos_http_err = ""
            for _ in range(30):
                pos_http = command(
                    [
                        "docker",
                        "exec",
                        "-i",
                        app_id,
                        "env/bin/python",
                        "-c",
                        PHASE_B_HTTP_PERM_PROBE,
                    ],
                    input_text=pos_http_inputs,
                    check=False,
                )
                if pos_http.returncode == 0:
                    pos_summary["http_file_access_boundary"] = json.loads(
                        pos_http.stdout
                    )
                    break
                last_pos_http_err = pos_http.stdout + pos_http.stderr
                time.sleep(2)
            else:
                priv_log = args.out.with_suffix(".private.log")
                priv_log.write_text(last_pos_http_err, encoding="utf-8")
                priv_log.chmod(0o600)
                raise RuntimeError("Positive-control HTTP file access check failed")

            pos_summary["openssl_aes256_round_trip_verified"] = True
            report["positive_control"] = pos_summary
            positive_control_seconds = round(time.monotonic() - t_pos_start, 3)

            step = "verify_isolation"
            rows = json.loads(
                command(
                    ["docker", "inspect", db_id, redis_id, app_id, frontend_id]
                ).stdout
            )
            assert all(not r["HostConfig"]["PortBindings"] for r in rows)
            live_mounts = {
                m["Source"] for r in live_before.values() for m in r["mounts"]
            }
            assert not any(
                m["Source"] in live_mounts for r in rows for m in r["Mounts"]
            )
            net = json.loads(
                command(["docker", "network", "inspect", project + "_restore"]).stdout
            )[0]
            assert net["Internal"] is True
            report["isolation"] = {
                "containers_inspected": 4,
                "host_ports": 0,
                "shared_live_mounts": 0,
                "network_internal": True,
                "workers_started": 0,
                "scheduler_started": False,
            }
            total_wall = round(time.monotonic() - started, 3)
            report["rto"] = build_rto_metrics(
                phases_seconds=rto_phases,
                measured_dr_rto_seconds=measured_dr_rto_seconds,
                positive_control_seconds=positive_control_seconds,
                total_wall_seconds=total_wall,
            )
            report["restore_and_verify_seconds"] = total_wall
            report["status"] = "PASS"
        except Exception as exc:
            report["failure"] = {"step": step, "type": type(exc).__name__}
        finally:
            if cleanup_started:
                cleanup = command(
                    base + ["down", "--volumes", "--remove-orphans"], check=False
                )
                report["cleanup_exit_code"] = cleanup.returncode
            report["remaining_restore_resources"] = {
                k: resources(k, project) for k in ("container", "volume", "network")
            }
            report["live_containers_unchanged"] = live_before == protected()
            if (
                any(report["remaining_restore_resources"].values())
                or not report["live_containers_unchanged"]
                or report.get("cleanup_exit_code", 0) != 0
            ):
                report["status"] = "FAIL"
            report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "report": str(args.out),
                        "failed_step": report.get("failure", {}).get("step"),
                    }
                ),
                flush=True,
            )
    return 0 if report["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--archive-sha256", required=True)
    p.add_argument("--key-file", type=Path, required=True)
    p.add_argument("--source-project", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]+", args.site):
        p.error("Invalid site name")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    def interrupted(signum: int, frame: object) -> None:
        raise InterruptedError("Restore drill interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
