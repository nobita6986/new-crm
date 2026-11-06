#!/usr/bin/env python3
"""Unit tests for P4-03 / P4-07 / P4-10 operational toolkit (`deploy/ops/`)."""

from __future__ import annotations

import http.server
import json
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import sys

OPS_DIR = Path(__file__).resolve().parent
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

import check_health_and_invariants as health_mod  # noqa: E402
import staging_https_smoke_and_reconcile as staging_mod  # noqa: E402


class _WebhookRecorderHandler(http.server.BaseHTTPRequestHandler):
    received_bodies: list[bytes] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        self.__class__.received_bodies.append(body)
        resp = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class OpsToolkitTests(unittest.TestCase):
    def test_staging_example_env_reports_expected_blockers(self) -> None:
        staging_example = staging_mod.COMPOSE_DIR / "env" / "staging.env.example"
        res = staging_mod.evaluate_preflight(staging_example, require_escrow_flag=True, escrow_confirmed=False)
        self.assertEqual(res["status"], "BLOCKED")
        blocker_ids = {b["id"] for b in res["blockers"]}
        self.assertIn("BLK-INFRA-02", blocker_ids)
        self.assertIn("BLK-INFRA-03", blocker_ids)
        self.assertIn("BLK-INFRA-04", blocker_ids)

    def test_valid_staging_env_with_lf_secret_and_escrow_passes_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sec_file = tmp_path / "db_root_password.txt"
            sec_file.write_bytes(b"SuperStrongSyntheticDbSecret12345\n")
            env_file = tmp_path / "staging.env"
            digest = "a" * 64
            env_file.write_text(
                "\n".join(
                    [
                        "COMPOSE_PROJECT_NAME=crm-cskh-staging",
                        "SITE_NAME=staging-crm.company-internal.vn",
                        "LETSENCRYPT_EMAIL=ops@company-internal.vn",
                        f"APP_IMAGE=ghcr.io/company/crm-cskh@sha256:{digest}",
                        "PULL_POLICY=always",
                        f"DB_PASSWORD_SECRETS_FILE={sec_file}",
                    ]
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            with mock.patch("os.name", "nt"):
                res = staging_mod.evaluate_preflight(env_file, require_escrow_flag=True, escrow_confirmed=True)
            self.assertEqual(res["status"], "PREFLIGHT_READY", res)
            self.assertEqual(res["blocker_count"], 0)

            # CRLF secret file must be rejected fail-closed
            sec_file.write_bytes(b"SuperStrongSyntheticDbSecret12345\r\n")
            with mock.patch("os.name", "nt"):
                bad_res = staging_mod.evaluate_preflight(env_file, require_escrow_flag=True, escrow_confirmed=True)
            self.assertEqual(bad_res["status"], "BLOCKED")
            self.assertIn("BLK-INFRA-04", {b["id"] for b in bad_res["blockers"]})

    def test_https_sid_cookie_policy_requires_secure_and_httponly_strictly_on_sid(self) -> None:
        # Other cookie has Secure+HttpOnly, but sid lacks Secure -> must FAIL on HTTPS
        headers_missing_secure = [
            "system_user=yes; Path=/; Secure; HttpOnly; SameSite=Lax",
            "sid=secret-session-value-999; Path=/; HttpOnly; SameSite=Lax",
            "full_name=Administrator; Path=/",
        ]
        sid_info = staging_mod.parse_sid_cookie_from_headers(headers_missing_secure)
        self.assertTrue(sid_info["found"])
        self.assertTrue(sid_info["httponly"])
        self.assertFalse(sid_info["secure"])
        self.assertEqual(sid_info["redacted_value"], "<REDACTED_FRAPPE_SID>")
        ok, errs = staging_mod.validate_https_sid_cookie_policy(sid_info)
        self.assertFalse(ok)
        self.assertTrue(any("Secure" in e for e in errs))

        # Both Secure and HttpOnly on sid -> PASS
        headers_https_ok = [
            "sid=secret-session-value-999; Path=/; Secure; HttpOnly; SameSite=Lax",
            "user_id=Administrator; Path=/",
        ]
        sid_ok_info = staging_mod.parse_sid_cookie_from_headers(headers_https_ok)
        ok2, errs2 = staging_mod.validate_https_sid_cookie_policy(sid_ok_info)
        self.assertTrue(ok2, errs2)
        self.assertEqual(errs2, [])

    def test_migration_snapshot_reconciliation_catches_drift_and_blg_p4_mig_01(self) -> None:
        base_snap = {
            "counts": {
                "crm_lead": 120,
                "cskh_work_item": 95,
                "cskh_active_claim": 60,
                "cskh_assignment_history": 140,
                "cskh_contact_attempt": 210,
                "contact_attempt_self_reported": 200,
                "contact_attempt_verified": 10,
                "cskh_qa_review_published": 10,
                "cskh_kpi_snapshot": 30,
                "cskh_outcome_code": 12,
                "cskh_policy": 1,
            },
            "invariants": {
                "duplicate_active_leads": 0,
                "owner_mismatch_active_work_items": 0,
                "unverified_marked_verified": 0,
                "false_zero_qa_snapshots": 0,
            },
            "blg_p4_mig_01": {
                "module_def_collation": "utf8mb4_unicode_ci",
                "export_job_table_exists": True,
                "cskh_doctype_module_distribution": {"CRM CSKH": 22, "Crm Cskh": 1},
            },
        }
        good = staging_mod.compare_migration_snapshots(base_snap, base_snap, base_snap)
        self.assertEqual(good["status"], "PASS", good)

        # Simulate silent self_reported -> verified tampering + non-idempotent run 2
        tampered_r1 = json.loads(json.dumps(base_snap))
        tampered_r1["counts"]["contact_attempt_verified"] = 15
        tampered_r2 = json.loads(json.dumps(tampered_r1))
        tampered_r2["counts"]["cskh_outcome_code"] = 13
        bad = staging_mod.compare_migration_snapshots(base_snap, tampered_r1, tampered_r2)
        self.assertEqual(bad["status"], "FAIL")
        self.assertGreaterEqual(bad["violation_count"], 2)

    def test_business_invariants_and_alert_redaction_and_cooldown(self) -> None:
        clean_raw = {
            "duplicate_active_leads": 0,
            "owner_mismatch_active_work_items": 0,
            "active_claim_count_mismatch": 0,
            "duplicate_attempt_idempotency_keys": 0,
            "unverified_marked_verified": 0,
            "orphan_or_misassigned_open_followup_tasks": 0,
            "duplicate_sla_idempotency_keys": 0,
            "stale_unevaluated_overdue_sla": 0,
            "stuck_dirty_scopes_over_30m": 0,
            "false_zero_qa_snapshots": 0,
            "unexpired_completed_exports_past_2h_grace": 0,
        }
        self.assertEqual(health_mod.evaluate_business_invariants(clean_raw)["status"], "PASS")

        broken_raw = dict(clean_raw, duplicate_active_leads=1, false_zero_qa_snapshots=2)
        broken_res = health_mod.evaluate_business_invariants(broken_raw)
        self.assertEqual(broken_res["status"], "FAIL")
        self.assertIn("INV-01_single_active_owner_and_sync", broken_res["failed_invariants"])
        self.assertIn("INV-05_kpi_snapshot_and_dirty_scope_integrity", broken_res["failed_invariants"])

        # Alert preflight blocked when operator / webhook missing
        pre_blk = health_mod.evaluate_alert_preflight("", "https://hooks.example.invalid/test")
        self.assertEqual(pre_blk["status"], "BLOCKED")
        self.assertEqual({b["id"] for b in pre_blk["blockers"]}, {"BLK-GOV-06", "BLK-INFRA-07"})

        # Alert envelope redacts phone numbers & sid cookies, and enforces cooldown
        env = health_mod.build_alert_envelope(
            "ALT-TEST-SYNTHETIC",
            "staging",
            "ops-oncall@company-internal.vn",
            "Test alert mentioning phone 0912345678 and sid=abc123secret",
            {"sample": 1},
        )
        self.assertNotIn("0912345678", env["summary"])
        self.assertNotIn("abc123secret", env["summary"])
        self.assertIn("<REDACTED_PHONE>", env["summary"])
        self.assertIn("<REDACTED_FRAPPE_SID>", env["summary"])

        _WebhookRecorderHandler.received_bodies = []
        with socketserver.TCPServer(("127.0.0.1", 0), _WebhookRecorderHandler) as httpd:
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    dedup_file = Path(tmp) / "alert_dedup.json"
                    r1 = health_mod.dispatch_alert_with_cooldown(
                        env,
                        f"http://127.0.0.1:{port}/secret-webhook-token-xyz",
                        dedup_state_file=dedup_file,
                        cooldown_seconds=900,
                        now_epoch=1_700_000_000.0,
                    )
                    self.assertEqual(r1["status"], "DELIVERED")
                    self.assertNotIn("secret-webhook-token-xyz", r1["webhook_redacted"])

                    # Second dispatch inside cooldown window must be suppressed
                    r2 = health_mod.dispatch_alert_with_cooldown(
                        env,
                        f"http://127.0.0.1:{port}/secret-webhook-token-xyz",
                        dedup_state_file=dedup_file,
                        cooldown_seconds=900,
                        now_epoch=1_700_000_120.0,
                    )
                    self.assertEqual(r2["status"], "SUPPRESSED_COOLDOWN")
                    self.assertEqual(len(_WebhookRecorderHandler.received_bodies), 1)
            finally:
                httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
