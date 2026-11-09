#!/usr/bin/env python3
"""Unit tests for P4-05 backup, offsite restore verification, retention, and timers."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

OPS_DIR = Path(__file__).resolve().parent
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

from create_encrypted_backup import SNAPSHOT, validate_site_config_backup_member  # noqa: E402
from install_systemd_timers import render_backup_units, render_healthcheck_units  # noqa: E402
from manage_backup_lifecycle import (  # noqa: E402
    apply_retention_plan,
    plan_gfs_retention,
    record_offsite_confirmation,
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
            repo_root=Path("/opt/crm-cskh-staging/repo"),
            env_file=Path("/opt/crm-cskh-staging/.env.staging"),
            site="crm-staging.localhost",
            extra_compose_files=(Path("/opt/crm-cskh-staging/compose.domain.yaml"),),
        )
        svc = hc["crm-cskh-healthcheck.service"]
        self.assertIn("--extra-compose-file /opt/crm-cskh-staging/compose.domain.yaml", svc)
        self.assertNotIn("--send-test-alert", svc)
        self.assertNotIn("CSKH_ALERT_WEBHOOK_URL", svc)

        bk = render_backup_units(
            repo_root=Path("/opt/crm-cskh-staging/repo"),
            compose_wrapper=Path("/opt/crm-cskh-staging/staging-compose"),
            site="crm-staging.localhost",
        )
        self.assertIn("manage_backup_lifecycle.py run-scheduled", bk["crm-cskh-backup.service"])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
