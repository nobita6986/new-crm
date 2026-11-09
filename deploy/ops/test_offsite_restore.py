#!/usr/bin/env python3
"""Unit tests for P4-05 backup, offsite restore verification, retention, and timers."""

from __future__ import annotations

import argparse
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tarfile
import tempfile
import types
import unittest

OPS_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPS_DIR.parents[1]
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

import check_health_and_invariants as health_mod  # noqa: E402
import create_encrypted_backup as create_backup_mod  # noqa: E402
from create_encrypted_backup import SNAPSHOT, validate_site_config_backup_member  # noqa: E402
import install_systemd_timers as timers_mod  # noqa: E402
from install_systemd_timers import render_backup_units, render_healthcheck_units  # noqa: E402
import manage_backup_lifecycle as lifecycle_mod  # noqa: E402
from manage_backup_lifecycle import (  # noqa: E402
    apply_retention_plan,
    emit_backup_failure_alert,
    plan_gfs_retention,
    record_offsite_confirmation,
    sync_bundle_offsite_automated,
    verify_backup_bundle,
)
import verify_offsite_restore as restore_mod  # noqa: E402
from verify_offsite_restore import (  # noqa: E402
    build_rto_metrics,
    evaluate_encryption_key_state,
    evaluate_translation_integrity,
    extract_backup,
    file_manifest,
    file_manifest_detailed,
    is_expected_missing_key_decrypt_error,
    run_negative_control_decrypt_probe,
)


def _sample_fernet_key(seed_byte: bytes = b"\x11") -> str:
    return base64.urlsafe_b64encode(seed_byte * 32).decode("ascii")


class BackupValidationTests(unittest.TestCase):
    def write_tar(self, path: Path, members: list[tuple[str, bytes]]) -> None:
        with tarfile.open(path, "w") as tf:
            for name, body in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                info.mode = 0o640
                tf.addfile(info, io.BytesIO(body))

    def members(self) -> list[tuple[str, bytes]]:
        members = [
            ("snapshot" + s, b"test")
            for s in (
                "-database.sql.gz",
                "-site_config_backup.json",
                "-private-files.tar",
                "-files.tar",
            )
        ]
        hashes = {name: hashlib.sha256(body).hexdigest() for name, body in members}
        return members + [
            ("before.json", b'{"counts":{}}'),
            ("backup-file-hashes.json", json.dumps(hashes).encode("utf-8")),
        ]

    def test_complete_backup_and_member_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "backup.tar"
            self.write_tar(archive, self.members())
            self.assertEqual(len(extract_backup(archive, root)), 6)

    def test_rejects_corrupt_missing_and_duplicate_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "backup.tar"
            good = self.members()
            cases = [
                good[1:],
                [("snapshot-database.sql.gz", b"changed")] + good[1:],
                good + [good[0]],
            ]
            for members in cases:
                with self.subTest(members=members):
                    self.write_tar(archive, members)
                    with self.assertRaises(ValueError):
                        extract_backup(archive, root)

    def test_rejects_path_traversal_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "backup.tar"
            for name in ("../escape", "/absolute"):
                self.write_tar(archive, [(name, b"bad")] + self.members())
                with self.assertRaises(ValueError):
                    extract_backup(archive, root)

    def test_public_private_paths_and_symlink_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "backup.tar"
            for prefix in ("./public/files/", "./private/files/", "files/"):
                self.write_tar(archive, [(prefix + "nested/file.txt", b"payload")])
                self.assertEqual(
                    file_manifest(archive),
                    {"nested/file.txt": hashlib.sha256(b"payload").hexdigest()},
                )
                detailed = file_manifest_detailed(archive)
                self.assertEqual(
                    detailed["nested/file.txt"],
                    {
                        "sha256": hashlib.sha256(b"payload").hexdigest(),
                        "bytes": 7,
                        "mode": 0o640,
                    },
                )
            with tarfile.open(archive, "w") as tf:
                link = tarfile.TarInfo("files/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                tf.addfile(link)
            with self.assertRaises(ValueError):
                file_manifest(archive)

    def test_encryption_key_state_distinguishes_uninitialized_site_from_lost_key(
        self,
    ) -> None:
        intact_uninit_cfg = {"db_name": "_live_db", "db_password": "live_pw_value"}
        restored_uninit_cfg = {"db_name": "_clone_db", "db_password": "clone_pw_value"}

        # 1. Live v1 archive case (`p4-20261109-pre-scheduler`): no encryption_key yet, 0 encrypted __Auth rows
        res_v1 = evaluate_encryption_key_state(
            intact_uninit_cfg,
            restored_uninit_cfg,
            {"counts": {"CRM Lead": 1, "Translation": 5}},
            encrypted_auth_rows=0,
        )
        self.assertEqual(res_v1["state"], "UNINITIALIZED_NO_ENCRYPTED_DATA")
        self.assertFalse(res_v1["key_loss_detected"])

        # 2. v2 archive explicitly recording `encryption_key_present = False`
        res_v2 = evaluate_encryption_key_state(
            intact_uninit_cfg,
            restored_uninit_cfg,
            {"encryption_key_present": False, "auth_encrypted_rows": 0},
            encrypted_auth_rows=0,
        )
        self.assertEqual(res_v2["state"], "UNINITIALIZED_NO_ENCRYPTED_DATA")
        self.assertEqual(res_v2["source_key_provenance"], "explicitly_absent_at_source")

        # 3. Positive encrypted site (`PRESENT_VERIFIED`)
        key_a = _sample_fernet_key(b"\x22")
        key_a_sha = hashlib.sha256(key_a.encode("utf-8")).hexdigest()
        saved_with_key = dict(intact_uninit_cfg, encryption_key=key_a)
        restored_with_key = dict(restored_uninit_cfg, encryption_key=key_a)
        res_pos = evaluate_encryption_key_state(
            saved_with_key,
            restored_with_key,
            {
                "encryption_key_present": True,
                "encryption_key_sha256": key_a_sha,
                "auth_encrypted_rows": 2,
            },
            encrypted_auth_rows=2,
        )
        self.assertEqual(res_pos["state"], "PRESENT_VERIFIED")
        self.assertEqual(res_pos["encryption_key_sha256"], key_a_sha)

        # 4. Fail-closed cases: MUST raise ValueError (never silently pass!)
        key_b = _sample_fernet_key(b"\x33")
        fail_cases = [
            # Truncated / empty site_config_backup.json
            ({}, restored_uninit_cfg, {}, 0),
            ({"db_name": "_db"}, restored_uninit_cfg, {}, 0),
            # Blank or malformed encryption_key field
            (dict(intact_uninit_cfg, encryption_key=""), restored_uninit_cfg, {}, 0),
            (
                dict(intact_uninit_cfg, encryption_key="not-a-fernet-key"),
                dict(restored_uninit_cfg, encryption_key="not-a-fernet-key"),
                {},
                0,
            ),
            # Backup lost encryption_key while restored DB has __Auth encrypted=1 rows!
            (intact_uninit_cfg, restored_uninit_cfg, {}, 1),
            # Backup lost encryption_key that existed on source site (`before.json`)!
            (
                intact_uninit_cfg,
                restored_uninit_cfg,
                {"encryption_key_present": True, "encryption_key_sha256": key_a_sha},
                0,
            ),
            (
                intact_uninit_cfg,
                restored_uninit_cfg,
                {"encryption_key_present": False, "auth_encrypted_rows": 1},
                0,
            ),
            # Stray unbacked-up key left in restored site_config.json
            (
                intact_uninit_cfg,
                dict(restored_uninit_cfg, encryption_key=key_a),
                {},
                0,
            ),
            # Key hash mismatch vs source baseline
            (
                saved_with_key,
                restored_with_key,
                {
                    "encryption_key_present": True,
                    "encryption_key_sha256": hashlib.sha256(key_b.encode()).hexdigest(),
                },
                0,
            ),
            # Restored site_config.json did not receive saved encryption_key
            (
                saved_with_key,
                restored_uninit_cfg,
                {"encryption_key_present": True, "encryption_key_sha256": key_a_sha},
                1,
            ),
        ]
        for saved_cfg, rest_cfg, base_meta, enc_rows in fail_cases:
            with self.subTest(saved=saved_cfg, base=base_meta, rows=enc_rows):
                with self.assertRaises(ValueError):
                    evaluate_encryption_key_state(saved_cfg, rest_cfg, base_meta, enc_rows)

    def test_create_encrypted_backup_site_config_validator(self) -> None:
        key_a = _sample_fernet_key(b"\x44")
        key_sha = hashlib.sha256(key_a.encode("utf-8")).hexdigest()

        uninit = validate_site_config_backup_member(
            {"db_name": "_db", "db_password": "secret"},
            {"encryption_key_present": False, "auth_encrypted_rows": 0},
        )
        self.assertEqual(uninit["encryption_key_state"], "UNINITIALIZED_NO_ENCRYPTED_DATA")

        verified = validate_site_config_backup_member(
            {"db_name": "_db", "db_password": "secret", "encryption_key": key_a},
            {
                "encryption_key_present": True,
                "encryption_key_sha256": key_sha,
                "auth_encrypted_rows": 1,
            },
        )
        self.assertEqual(verified["encryption_key_state"], "PRESENT_VERIFIED")

        with self.assertRaises(ValueError):
            validate_site_config_backup_member(
                {"db_name": "_db", "db_password": "secret"},
                {"encryption_key_present": True, "encryption_key_sha256": key_sha},
            )

    def test_translation_integrity_and_rto_breakdown_contract(self) -> None:
        tr_snap = {
            "translation_rows": 5,
            "translation_sha256": "a" * 64,
            "verified_override_count": 5,
            "mismatched_override_count": 0,
            "core_vi_catalog_verified": True,
        }
        ok = evaluate_translation_integrity(
            tr_snap,
            dict(tr_snap),
            expected_rows=5,
            expected_sha256="a" * 64,
        )
        self.assertEqual(ok["status"], "PASS")

        drifted = dict(tr_snap, mismatched_override_count=1)
        bad = evaluate_translation_integrity(tr_snap, drifted, expected_rows=5)
        self.assertEqual(bad["status"], "FAIL")

        phases = {
            "archive_checksum_decrypt_and_extract": 0.85,
            "isolated_stack_bootstrap_and_db_ready": 14.2,
            "new_site_init": 19.4,
            "sql_and_files_restore_only": 4.1,
            "pre_migrate_reconciliation": 2.3,
            "migrate_run_1": 16.5,
            "migrate_run_2": 14.8,
            "post_migrate_reconciliation_and_invariants": 2.1,
            "web_service_start_and_authenticated_http_smoke": 6.4,
        }
        rto = build_rto_metrics(
            phases_seconds=phases,
            measured_dr_rto_seconds=80.65,
            positive_control_seconds=22.4,
            total_wall_seconds=103.05,
        )
        self.assertFalse(rto["sql_import_only_is_full_rto"])
        self.assertGreater(
            rto["measured_end_to_end_isolated_dr_rto_seconds"],
            rto["sql_and_files_restore_only_seconds"],
        )
        with self.assertRaises(ValueError):
            build_rto_metrics(phases, 1.0, 5.0, 6.0)

    def test_embedded_worker_scripts_compile_cleanly(self) -> None:
        shared = restore_mod._shared_worker_helpers_source()
        snippets = {
            "create_backup_snapshot": SNAPSHOT,
            "phase_a_worker": shared + "\n\n" + restore_mod.PHASE_A_WORKER_BODY,
            "phase_a_http": restore_mod.PHASE_A_HTTP_PROBE,
            "stop_gunicorn": restore_mod.STOP_GUNICORN_SNIPPET,
            "phase_b_prepare": shared + "\n\n" + restore_mod.PHASE_B_PREPARE_WORKER_BODY,
            "phase_b_verify": shared + "\n\n" + restore_mod.PHASE_B_VERIFY_WORKER_BODY,
            "phase_b_http": restore_mod.PHASE_B_HTTP_PERM_PROBE,
        }
        for name, code in snippets.items():
            with self.subTest(snippet=name):
                compile(code, f"<{name}>", "exec")

    def test_backup_lifecycle_honest_offsite_status_and_gfs_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backup_root = Path(tmp)

            def make_bundle(name: str, completed_dt: datetime, payload: bytes = b"encrypted-archive-bytes") -> Path:
                bdir = backup_root / name
                bdir.mkdir()
                enc = bdir / "backup.tar.gz.enc"
                enc.write_bytes(payload)
                (bdir / "backup-key").write_text("0123456789abcdef" * 4 + "\n", encoding="utf-8")
                rep = {
                    "schema_version": "p4-encrypted-backup-v2",
                    "completed_at_utc": completed_dt.isoformat(),
                    "source_project": "crm-cskh-vps-staging",
                    "source_revision": "467204176326f046442b5e20c381f9a895b6e176",
                    "archive_sha256": hashlib.sha256(payload).hexdigest(),
                    "archive_bytes": len(payload),
                }
                (bdir / "backup-report.json").write_text(
                    json.dumps(rep), encoding="utf-8"
                )
                return bdir

            base_dt = datetime(2026, 11, 9, 19, 15, tzinfo=timezone.utc)
            latest_dir = make_bundle("scheduled-20261109T191500Z", base_dt)

            # Without offsite-verification.json -> NEVER claims end-to-end PASS
            v_local = verify_backup_bundle(latest_dir, enforce_posix_permissions=False)
            self.assertEqual(v_local["local_archive_status"], "PASS")
            self.assertEqual(
                v_local["end_to_end_backup_status"],
                "PARTIAL_LOCAL_ONLY_OFFSITE_PENDING",
            )
            self.assertEqual(
                v_local["automated_offsite_pipeline_status"],
                "BLOCKED_BLK_INFRA_05",
            )

            # Wrong SHA-256 rejected when recording offsite confirmation
            with self.assertRaises(ValueError):
                record_offsite_confirmation(latest_dir, "0" * 64)

            # Valid offsite confirmation -> manual offsite verified, automation still honestly BLOCKED
            v_off = record_offsite_confirmation(latest_dir, v_local["archive_sha256"])
            self.assertTrue(v_off["offsite_verified"])
            self.assertEqual(
                v_off["end_to_end_backup_status"],
                "PARTIAL_MANUAL_OFFSITE_VERIFIED_AUTOMATION_BLOCKED",
            )

            # Seed 20 daily backups + 1 older same-day duplicate + 1 milestone `pre-scheduler`
            make_bundle(
                "scheduled-20261109T010000Z",
                base_dt - timedelta(hours=10),
                b"older-same-day",
            )
            for day_offset in range(1, 20):
                dt = base_dt - timedelta(days=day_offset)
                make_bundle(
                    f"scheduled-{dt.strftime('%Y%m%dT%H%M%SZ')}",
                    dt,
                    f"day-{day_offset}".encode("utf-8"),
                )
            milestone_dt = base_dt - timedelta(days=120)
            make_bundle("p4-20260712-pre-scheduler", milestone_dt, b"milestone")

            plan = plan_gfs_retention(
                backup_root,
                keep_daily=14,
                keep_weekly=8,
                keep_monthly=3,
                protect_milestones=True,
            )
            kept_names = {item["name"] for item in plan["kept"]}
            prune_names = {item["name"] for item in plan["prune_candidates"]}
            self.assertIn("scheduled-20261109T191500Z", kept_names)
            self.assertIn("p4-20260712-pre-scheduler", kept_names)
            self.assertIn("scheduled-20261109T010000Z", prune_names)

            applied = apply_retention_plan(backup_root, plan, apply=True)
            self.assertIn("scheduled-20261109T010000Z", applied["deleted_bundles"])
            self.assertTrue((backup_root / "p4-20260712-pre-scheduler").exists())
            self.assertTrue(latest_dir.exists())

    def test_systemd_units_include_domain_overlay_and_keep_external_alerts_gated(
        self,
    ) -> None:
        hc = render_healthcheck_units(
            repo_root=Path("/opt/crm-cskh-ops"),
            env_file=Path("/opt/crm-cskh-staging/.env.staging"),
            site="crm-staging.localhost",
            extra_compose_files=(Path("/opt/crm-cskh-staging/compose.domain.yaml"),),
        )
        svc = hc["crm-cskh-healthcheck.service"]
        self.assertIn("--extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml", svc)
        self.assertIn("EnvironmentFile=-/etc/crm-cskh/alerting.env", svc)
        self.assertNotIn("--send-test-alert", svc)
        self.assertNotIn("CSKH_ALERT_WEBHOOK_URL", svc)

        bk = render_backup_units(
            repo_root=Path("/opt/crm-cskh-ops"),
            compose_wrapper=Path("/opt/crm-cskh-staging/staging-compose"),
            site="crm-staging.localhost",
        )
        self.assertIn("manage_backup_lifecycle.py run-scheduled", bk["crm-cskh-backup.service"])
        self.assertIn(
            "EnvironmentFile=-/etc/crm-cskh/offsite-backup.env",
            bk["crm-cskh-backup.service"],
        )
        self.assertIn(
            "EnvironmentFile=-/etc/crm-cskh/alerting.env",
            bk["crm-cskh-backup.service"],
        )
        self.assertIn("OnFailure=crm-cskh-backup-failure@%n.service", bk["crm-cskh-backup.service"])
        self.assertIn("crm-cskh-backup-failure@.service", bk)
        self.assertIn(
            "EnvironmentFile=-/etc/crm-cskh/alerting.env",
            bk["crm-cskh-backup-failure@.service"],
        )
        self.assertIn(
            "--backup-status-file /var/backups/crm-cskh/latest-backup-lifecycle.json",
            svc,
        )

    def test_automated_offsite_sync_enforces_key_separation_and_failure_alert_redaction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_root = root / "local-backups"
            offsite_vault = root / "offsite-vault"
            backup_root.mkdir()
            offsite_vault.mkdir()

            payload = b"encrypted-archive-for-automated-offsite"
            bdir = backup_root / "scheduled-20261109T200000Z"
            bdir.mkdir()
            (bdir / "backup.tar.gz.enc").write_bytes(payload)
            (bdir / "backup-key").write_text("fedcba9876543210" * 4 + "\n", encoding="utf-8")
            (bdir / "backup-report.json").write_text(
                json.dumps(
                    {
                        "schema_version": "p4-encrypted-backup-v2",
                        "completed_at_utc": "2026-11-09T20:00:00+00:00",
                        "source_project": "crm-cskh-vps-staging",
                        "source_revision": "467204176326f046442b5e20c381f9a895b6e176",
                        "archive_sha256": hashlib.sha256(payload).hexdigest(),
                        "archive_bytes": len(payload),
                    }
                ),
                encoding="utf-8",
            )

            # 1. Target inside backup_root must fail closed
            with self.assertRaisesRegex(ValueError, "disjoint"):
                sync_bundle_offsite_automated(
                    bdir,
                    backup_root=backup_root,
                    offsite_target_dir=backup_root / "nested-offsite",
                )

            # 1b. Sibling directory on the SAME host filesystem/VPS disk MUST fail closed by default!
            with self.assertRaisesRegex(ValueError, "NOT valid offsite storage"):
                sync_bundle_offsite_automated(
                    bdir,
                    backup_root=backup_root,
                    offsite_target_dir=offsite_vault,
                )

            # 1c. Upload command referencing backup-key MUST fail closed!
            with self.assertRaisesRegex(ValueError, "never reference or upload backup-key"):
                sync_bundle_offsite_automated(
                    bdir,
                    backup_root=backup_root,
                    offsite_upload_cmd="scp backup-key user@remote:/vault/",
                    offsite_verify_cmd="sha256sum /vault/backup.tar.gz.enc",
                )

            # 2. Disjoint automated offsite target (with explicit CI drill flag) copies archive + report ONLY
            verified = sync_bundle_offsite_automated(
                bdir,
                backup_root=backup_root,
                offsite_target_dir=offsite_vault,
                allow_same_filesystem_for_drill=True,
            )
            self.assertEqual(verified["end_to_end_backup_status"], "PASS")
            self.assertTrue(verified["periodic_offsite_upload_proven"])
            self.assertFalse(verified["one_off_manual_copy_proves_periodic_upload"])
            self.assertFalse((offsite_vault / bdir.name / "backup-key").exists())
            self.assertTrue((offsite_vault / bdir.name / "backup.tar.gz.enc").is_file())
            self.assertTrue((offsite_vault / bdir.name / "offsite-verification.json").is_file())

            # 3. Pre-existing backup-key at offsite destination must trigger CRITICAL refusal
            leaked_key = offsite_vault / bdir.name / "backup-key"
            leaked_key.write_text("accidental-key\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "backup-key detected"):
                sync_bundle_offsite_automated(
                    bdir,
                    backup_root=backup_root,
                    offsite_target_dir=offsite_vault,
                    allow_same_filesystem_for_drill=True,
                )

            # 4. Failure alert writes redacted ALT-BACKUP-FAILURE JSONL and keeps external channel gated
            alert_res = emit_backup_failure_alert(
                backup_root=backup_root,
                environment_label="vps-staging",
                reason="Offsite upload failed for phone 0912345678 and sid=topSecretSid123",
                metrics={"unit": "crm-cskh-backup.service"},
            )
            self.assertEqual(alert_res["alert_envelope"]["alert_code"], "ALT-BACKUP-FAILURE")
            self.assertEqual(
                alert_res["dispatch"]["status"],
                "GATED_PENDING_OWNER_CONTACT_AND_CHANNEL",
            )
            log_text = (backup_root / "backup-alerts.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("0912345678", log_text)
            self.assertNotIn("topSecretSid123", log_text)
            self.assertIn("<REDACTED_PHONE>", log_text)
            self.assertIn("<REDACTED_FRAPPE_SID>", log_text)

    def test_negative_control_decrypt_branch_execution(self) -> None:
        """Execute both helper and actual PHASE_B_VERIFY_WORKER_BODY negative-control block."""
        secret = "P4-PosCtrl-Secret-Value-99!"

        try:
            from cryptography.fernet import Fernet, InvalidToken as CryptoInvalidToken

            def _make_invalid_token() -> Exception:
                token = Fernet(_sample_fernet_key(b"\x51").encode("ascii")).encrypt(
                    secret.encode("utf-8")
                )
                try:
                    Fernet(_sample_fernet_key(b"\x52").encode("ascii")).decrypt(token)
                except CryptoInvalidToken as exc:
                    return exc
                raise AssertionError("Expected InvalidToken from mismatched Fernet keys")

        except ImportError:
            CryptoInvalidToken = type(  # type: ignore[misc,assignment]
                "InvalidToken", (Exception,), {"__module__": "cryptography.fernet"}
            )

            def _make_invalid_token() -> Exception:
                return CryptoInvalidToken("Fernet token signature mismatch")

        OperationalError = type(
            "OperationalError", (Exception,), {"__module__": "pymysql.err"}
        )
        FrappeAuthError = type(
            "AuthenticationError", (Exception,), {"__module__": "frappe.exceptions"}
        )
        FrappeValidationError = type(
            "ValidationError", (Exception,), {"__module__": "frappe.exceptions"}
        )

        # 1. Returning the exact secret without raising MUST FAIL with AssertionError.
        with self.assertRaisesRegex(AssertionError, "matched_control_secret=True"):
            run_negative_control_decrypt_probe(lambda *_a, **_kw: secret, secret)

        # 2. Returning empty/None/other value without raising MUST ALSO FAIL with AssertionError.
        for unexpected_return in ("", None, "different-plaintext"):
            with self.subTest(unexpected_return=unexpected_return):
                with self.assertRaisesRegex(
                    AssertionError, "matched_control_secret=False"
                ):
                    run_negative_control_decrypt_probe(
                        lambda *_a, _v=unexpected_return, **_kw: _v, secret
                    )

        # 3. AssertionError raised inside decrypt_fn MUST NEVER be caught or turned into PASS.
        def _raise_assertion(*_a: object, **_kw: object) -> str:
            raise AssertionError("inner verification assertion")

        with self.assertRaisesRegex(AssertionError, "inner verification assertion"):
            run_negative_control_decrypt_probe(_raise_assertion, secret)

        # 4. DB/SQL, Import, Auth ('Password not found'), Runtime, and unrelated ValidationError MUST FAIL.
        rejected_errors: list[tuple[type[BaseException], BaseException]] = [
            (
                ImportError,
                ImportError("No module named 'frappe.utils.password'"),
            ),
            (
                OperationalError,
                OperationalError(
                    2013, "Lost connection to MySQL during encryption_key query"
                ),
            ),
            (FrappeAuthError, FrappeAuthError("Password not found")),
            (
                FrappeValidationError,
                FrappeValidationError("Unrelated DocType validation error"),
            ),
            (RuntimeError, RuntimeError("Unexpected runtime crash")),
        ]
        for expected_cls, err_obj in rejected_errors:
            with self.subTest(rejected_error=type(err_obj).__name__):
                self.assertFalse(is_expected_missing_key_decrypt_error(err_obj))

                def _raise_unexpected(*_a: object, _e: BaseException = err_obj, **_kw: object) -> str:
                    raise _e

                with self.assertRaises(expected_cls):
                    run_negative_control_decrypt_probe(_raise_unexpected, secret)

        # DB error chained beneath a ValidationError MUST STILL be rejected.
        try:
            try:
                raise OperationalError(2006, "MySQL server has gone away")
            except OperationalError as db_exc:
                raise FrappeValidationError(
                    "Encryption key is invalid! Please check site_config.json"
                ) from db_exc
        except FrappeValidationError as wrapped_db_exc:
            self.assertFalse(is_expected_missing_key_decrypt_error(wrapped_db_exc))

        # 5. Expected decryption errors (direct InvalidToken and Frappe ValidationError from InvalidToken) PASS.
        inv_tok = _make_invalid_token()
        self.assertTrue(is_expected_missing_key_decrypt_error(inv_tok))
        direct_ok = run_negative_control_decrypt_probe(
            lambda *_a, **_kw: (_ for _ in ()).throw(inv_tok),
            secret,
        )
        self.assertEqual(direct_ok["rejected"], True)
        self.assertEqual(direct_ok["exception_class"], "InvalidToken")

        def _frappe_missing_key_decrypt(*_a: object, **_kw: object) -> str:
            try:
                raise _make_invalid_token()
            except Exception as tok_err:
                raise FrappeValidationError(
                    "Encryption key is invalid! Please check site_config.json"
                ) from tok_err

        chained_ok = run_negative_control_decrypt_probe(_frappe_missing_key_decrypt, secret)
        self.assertEqual(chained_ok["rejected"], True)
        self.assertEqual(chained_ok["exception_class"], "ValidationError")

        # 6. Execute the actual negative-control block extracted from PHASE_B_VERIFY_WORKER_BODY.
        body = restore_mod.PHASE_B_VERIFY_WORKER_BODY
        start_marker = "# BEGIN_NEGATIVE_CONTROL_DECRYPT_BLOCK"
        end_marker = "# END_NEGATIVE_CONTROL_DECRYPT_BLOCK"
        self.assertIn(start_marker, body)
        self.assertIn(end_marker, body)
        neg_block = body.split(start_marker, 1)[1].split(end_marker, 1)[0]
        executable_worker_slice = (
            restore_mod._shared_worker_helpers_source() + "\n\n" + neg_block
        )

        with tempfile.TemporaryDirectory() as tmp:
            bench_dir = Path(tmp) / "frappe-bench"
            (bench_dir / "sites").mkdir(parents=True)
            orig_cwd = Path.cwd()
            saved_modules = {
                k: sys.modules.get(k)
                for k in ("frappe", "frappe.utils", "frappe.utils.password")
            }
            old_bench_env = os.environ.get("CSKH_BENCH_ROOT")
            os.environ["CSKH_BENCH_ROOT"] = str(bench_dir)
            try:
                def _run_worker_slice(password_impl: object | None) -> dict[str, object]:
                    destroyed: list[bool] = []
                    fake_frappe = types.ModuleType("frappe")
                    fake_frappe.init = lambda **_kw: None  # type: ignore[attr-defined]
                    fake_frappe.connect = lambda: None  # type: ignore[attr-defined]
                    fake_frappe.destroy = lambda: destroyed.append(True)  # type: ignore[attr-defined]
                    fake_utils = types.ModuleType("frappe.utils")
                    sys.modules["frappe"] = fake_frappe
                    sys.modules["frappe.utils"] = fake_utils
                    if password_impl is None:
                        # Force ImportError on `from frappe.utils.password import get_decrypted_password`
                        sys.modules["frappe.utils.password"] = None  # type: ignore[assignment]
                    else:
                        fake_pw = types.ModuleType("frappe.utils.password")
                        fake_pw.get_decrypted_password = password_impl  # type: ignore[attr-defined]
                        fake_utils.password = fake_pw  # type: ignore[attr-defined]
                        sys.modules["frappe.utils.password"] = fake_pw

                    ns: dict[str, object] = {
                        "frappe": fake_frappe,
                        "site": "crm-staging.localhost",
                        "p": {"control_secret": secret},
                    }
                    try:
                        exec(compile(executable_worker_slice, "<phase_b_neg_slice>", "exec"), ns)
                    finally:
                        self.assertEqual(destroyed, [True])
                        self.assertEqual(Path.cwd().resolve(), bench_dir.resolve())
                    return ns

                # Returning exact secret in real worker block -> MUST FAIL
                with self.assertRaises(AssertionError):
                    _run_worker_slice(lambda *_a, **_kw: secret)

                # Returning blank string without raising in real worker block -> MUST FAIL
                with self.assertRaises(AssertionError):
                    _run_worker_slice(lambda *_a, **_kw: "")

                # Import failure in real worker block -> MUST FAIL
                with self.assertRaises(ImportError):
                    _run_worker_slice(None)

                # Database OperationalError in real worker block -> MUST FAIL
                with self.assertRaises(OperationalError):
                    _run_worker_slice(
                        lambda *_a, **_kw: (_ for _ in ()).throw(
                            OperationalError(2006, "DB connection dropped")
                        )
                    )

                # Expected missing/invalid encryption_key decryption error -> MUST PASS
                ns_ok = _run_worker_slice(_frappe_missing_key_decrypt)
                neg_eval = ns_ok["neg_decrypt_eval"]
                self.assertIsInstance(neg_eval, dict)
                self.assertEqual(neg_eval["rejected"], True)  # type: ignore[index]
            finally:
                os.chdir(orig_cwd)
                if old_bench_env is None:
                    os.environ.pop("CSKH_BENCH_ROOT", None)
                else:
                    os.environ["CSKH_BENCH_ROOT"] = old_bench_env
                for mod_name, prev_mod in saved_modules.items():
                    if prev_mod is None:
                        sys.modules.pop(mod_name, None)
                    else:
                        sys.modules[mod_name] = prev_mod

    def test_runbook_cli_commands_and_systemd_units_parse_and_dry_run(self) -> None:
        """Verify runbook CLI flags, restore key path, ops directory prep, and systemd dry-run."""
        docs_dir = REPO_ROOT / "docs" / "p4"
        systemd_dir = OPS_DIR / "systemd"

        whole_file_forbidden = re.compile(
            "|".join(
                (
                    r"backup\.key\b",
                    r"backup-passphrase\.key\b",
                    r"CSKH_BACKUP_ENCRYPTION_KEY_FILE",
                    r"--backup-key-file\b",
                    r"install_systemd_timers\.py[^\n`]*--apply",
                )
            )
        )
        code_block_forbidden = re.compile(
            r"/opt/crm-cskh-app\b|/opt/crm-cskh-staging/repo\b"
        )

        for path in sorted([*docs_dir.glob("*.md"), *systemd_dir.glob("*")]):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            m = whole_file_forbidden.search(text)
            self.assertIsNone(
                m,
                f"Stale CLI flag or key path '{m.group(0) if m else ''}' found in {path.relative_to(REPO_ROOT)}",
            )
            if path.suffix == ".service":
                mc = code_block_forbidden.search(text)
                self.assertIsNone(
                    mc,
                    f"Unverified repo path '{mc.group(0) if mc else ''}' found in {path.relative_to(REPO_ROOT)}",
                )
            else:
                for block in re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL):
                    mc = code_block_forbidden.search(block)
                    self.assertIsNone(
                        mc,
                        f"Unverified repo path '{mc.group(0) if mc else ''}' in code block of {path.relative_to(REPO_ROOT)}",
                    )

        backup_doc = (docs_dir / "backup-restore.md").read_text(encoding="utf-8")
        self.assertIn("/opt/crm-p4-restore-input-20261109/backup-key", backup_doc)
        self.assertIn("/opt/crm-cskh-ops", backup_doc)
        self.assertIn("secrets.token_hex(32)", backup_doc)

        # Bogus legacy flags on install_systemd_timers.py MUST fail parser validation.
        for bad_flag in (["--apply"], ["--backup-key-file", "/tmp/k"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                timers_mod.build_parser().parse_args(bad_flag)

        # Dry-run install_systemd_timers.py with live VPS parameters.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = timers_mod.main(
                [
                    "--repo-root",
                    "/opt/crm-cskh-ops",
                    "--env-file",
                    "/opt/crm-cskh-staging/.env.staging",
                    "--compose-wrapper",
                    "/opt/crm-cskh-staging/staging-compose",
                    "--extra-compose-file",
                    "/opt/crm-cskh-staging/compose.domain.yaml",
                    "--site",
                    "crm-staging.localhost",
                    "--environment-label",
                    "staging",
                    "--backup-root",
                    "/var/backups/crm-cskh",
                ]
            )
        self.assertEqual(rc, 0)
        dry_summary = json.loads(buf.getvalue())
        self.assertEqual(dry_summary["mode"], "dry_run")
        self.assertFalse(dry_summary["external_alerts_enabled"])
        self.assertFalse(dry_summary["automated_offsite_upload_enabled"])
        self.assertEqual(
            dry_summary["automated_offsite_status"],
            "PARTIAL_UNTIL_AUTOMATED_TARGET_PROVISIONED",
        )
        for rel_script in dry_summary["referenced_ops_scripts"]:
            rel_tail = rel_script.removeprefix("/opt/crm-cskh-ops/")
            self.assertTrue(
                (REPO_ROOT / rel_tail).is_file(),
                f"Referenced ops script missing in repo: {rel_tail}",
            )

        parsers = {
            "install_systemd_timers.py": timers_mod.build_parser(),
            "verify_offsite_restore.py": restore_mod.build_parser(),
            "create_encrypted_backup.py": create_backup_mod.build_parser(),
            "manage_backup_lifecycle.py": lifecycle_mod.build_parser(),
            "check_health_and_invariants.py": health_mod.build_parser(),
        }

        def _parse_exec_line(raw_cmd: str) -> tuple[str, argparse.Namespace]:
            tokens = shlex.split(raw_cmd)
            while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
                tokens.pop(0)
            if tokens and tokens[0] == "sudo":
                tokens.pop(0)
            while tokens and ("=" in tokens[0] and not tokens[0].startswith("-")):
                tokens.pop(0)
            self.assertTrue(tokens and "python" in Path(tokens[0]).name)
            script_name = Path(tokens[1]).name
            self.assertIn(script_name, parsers)
            return script_name, parsers[script_name].parse_args(tokens[2:])

        # Validate rendered ExecStart= in dry-run units AND static systemd/*.service templates.
        for unit_name, unit_body in dry_summary["units"].items():
            if not unit_name.endswith(".service"):
                continue
            exec_lines = [
                ln.split("ExecStart=", 1)[1]
                for ln in unit_body.splitlines()
                if ln.startswith("ExecStart=")
            ]
            self.assertEqual(len(exec_lines), 1)
            _parse_exec_line(exec_lines[0])

        for svc_file in sorted(systemd_dir.glob("*.service")):
            raw_text = svc_file.read_text(encoding="utf-8")
            joined = re.sub(r"\\\n\s*", " ", raw_text)
            exec_lines = [
                ln.split("ExecStart=", 1)[1]
                for ln in joined.splitlines()
                if ln.startswith("ExecStart=")
            ]
            self.assertEqual(len(exec_lines), 1, svc_file.name)
            _parse_exec_line(exec_lines[0])

        # Extract and parse EVERY sample CLI invocation across all four ops runbooks!
        parsed_count = 0
        for doc_name in (
            "backup-restore.md",
            "monitoring-alerts.md",
            "operations-sop.md",
            "rollback-runbook.md",
        ):
            doc_text = (docs_dir / doc_name).read_text(encoding="utf-8")
            for block in re.findall(r"```bash\n(.*?)```", doc_text, flags=re.DOTALL):
                unwrapped = re.sub(r"\\\n\s*", " ", block)
                for line in unwrapped.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if stripped.startswith("test "):
                        continue
                    if any(s in stripped for s in parsers):
                        # Substitute shell command substitution `$(date ...)` for shlex/argparse validation.
                        normalized = re.sub(r"\$\([^)]+\)", "20261109T191500Z", stripped)
                        normalized = normalized.replace("${ENV_FILE}", "/etc/crm-cskh/production.env")
                        normalized = normalized.replace("${SITE_NAME}", "crm-staging.localhost")
                        _parse_exec_line(normalized)
                        parsed_count += 1
        self.assertGreaterEqual(parsed_count, 12)

    def test_vps_live_evidence_bundle_and_env_templates_reconciled(self) -> None:
        """Verify T0 VPS evidence SHA-256 reconciliation, CI/VPS separation, and drop-in templates."""
        vps_dir = OPS_DIR / "evidence" / "p4-vps-live-20261109"
        ci_dir = OPS_DIR / "evidence" / "p4-g07-g08-dr-20261109"
        self.assertTrue(vps_dir.is_dir())
        self.assertTrue(ci_dir.is_dir())

        vps_manifest = json.loads(
            (vps_dir / "vps-evidence-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            vps_manifest["ops_sha"], "801be22b3be2c8199704c4da7442b0c2e0edf352"
        )
        self.assertEqual(len(vps_manifest["files"]), 11)
        for item in vps_manifest["files"]:
            fp = vps_dir / item["name"]
            self.assertTrue(fp.is_file(), item["name"])
            raw = fp.read_bytes()
            self.assertEqual(len(raw), item["bytes"], item["name"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"], item["name"])

        restore_doc = json.loads(
            (vps_dir / "restore-drill-report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(restore_doc["status"], "PASS")
        self.assertEqual(
            restore_doc["rto"]["measured_end_to_end_isolated_dr_rto_seconds"], 142.598
        )
        self.assertEqual(restore_doc["cleanup_exit_code"], 0)
        self.assertTrue(restore_doc["live_containers_unchanged"])

        timers_doc = json.loads(
            (vps_dir / "timers-runtime.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            timers_doc["timers"]["crm-cskh-healthcheck.timer"]["LastTriggerUSec"],
            "Mon 2026-11-09 20:05:45 +07",
        )
        # Honest distinction: backup timer is enabled, but first measured run was manual start
        self.assertEqual(
            timers_doc["timers"]["crm-cskh-backup.timer"]["UnitFileState"], "enabled"
        )
        self.assertEqual(
            timers_doc["timers"]["crm-cskh-backup.timer"]["LastTriggerUSec"], ""
        )

        # Verify vendor-neutral drop-in env templates exist and contain zero fake signers/secrets
        offsite_tpl = (
            REPO_ROOT / "deploy" / "compose" / "env" / "offsite-backup.env.example"
        ).read_text(encoding="utf-8")
        alert_tpl = (
            REPO_ROOT / "deploy" / "compose" / "env" / "alerting.env.example"
        ).read_text(encoding="utf-8")
        self.assertIn("st_dev", offsite_tpl)
        self.assertIn("NEVER upload or reference `backup-key`", offsite_tpl)
        self.assertIn("BLK-GOV-06", alert_tpl)
        self.assertIn("BLK-INFRA-07", alert_tpl)

        manifest_path = OPS_DIR / "evidence" / "p4-resume-ops-manifest.json"
        manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_doc["schema_version"], "p4-resume-ops-manifest-v4")
        self.assertFalse(manifest_doc["p4_promoted_to_pass"])
        self.assertEqual(
            manifest_doc["live_vps"]["vps_isolated_restore_drill_runtime_status"], "PASS"
        )
        self.assertEqual(
            manifest_doc["live_vps"]["vps_backup_timer_status"],
            "ENABLED_AWAITING_FIRST_SCHEDULED_TRIGGER",
        )
        for entry in manifest_doc["deliverables"]:
            target = REPO_ROOT / entry["path"]
            self.assertTrue(target.is_file(), entry["path"])
            raw_bytes = target.read_bytes()
            self.assertEqual(len(raw_bytes), entry["bytes"], entry["path"])
            self.assertEqual(
                hashlib.sha256(raw_bytes).hexdigest(), entry["sha256"], entry["path"]
            )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
