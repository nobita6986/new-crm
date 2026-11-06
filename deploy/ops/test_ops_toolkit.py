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

    def test_compose_modes_match_p4_01_validator_profiles(self) -> None:
        import re
        import shutil
        import subprocess

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compose" / "scripts"))
        import validate_compose_config as vcc

        expected_tls = tuple(Path(p).resolve() for p in vcc.STACK_PROFILES["staging"]["compose_files"])
        expected_smoke = tuple(
            Path(p).resolve() for p in vcc.STACK_PROFILES["isolated-smoke"]["compose_files"]
        )
        self.assertEqual(len(expected_tls), 8)
        self.assertEqual(len(expected_smoke), 8)
        self.assertEqual(tuple(p.resolve() for p in health_mod.COMPOSE_MODES["tls"]), expected_tls)
        self.assertEqual(
            tuple(p.resolve() for p in health_mod.COMPOSE_MODES["isolated-smoke"]), expected_smoke
        )
        self.assertEqual(tuple(p.resolve() for p in staging_mod.COMPOSE_MODES["tls"]), expected_tls)
        self.assertEqual(
            tuple(p.resolve() for p in staging_mod.COMPOSE_MODES["isolated-smoke"]), expected_smoke
        )

        # Verify both runbooks list all 8 Compose files in exact order (F-P4-OPS-01)
        repo_root = staging_mod.REPO_ROOT
        for doc_rel in (
            "docs/p4/p4-03-staging-checklist-and-rehearsal.md",
            "docs/p4/release-runbook.md",
        ):
            doc_text = (repo_root / doc_rel).read_text(encoding="utf-8")
            doc_files = tuple(
                (repo_root / m.group(1)).resolve()
                for m in re.finditer(r"-f\s+(deploy/compose/[^\s\"\\]+)", doc_text)
            )
            self.assertEqual(doc_files, expected_tls, f"Compose list mismatch in {doc_rel}")

        # Verify `docker compose config` renders cleanly across both modes & migrate profile
        if shutil.which("docker"):
            stg_env = staging_mod.COMPOSE_DIR / "env" / "staging.env.example"
            smk_env = staging_mod.COMPOSE_DIR / "env" / "isolated-smoke.env.example"
            for cmd_base, expected_svcs in (
                (staging_mod._compose_cmd(stg_env, "tls"), 11),
                (health_mod._compose_cmd(smk_env, "isolated-smoke"), 11),
            ):
                p_default = subprocess.run(
                    cmd_base + ["config", "--services"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                )
                svcs_default = [s.strip() for s in p_default.stdout.splitlines() if s.strip()]
                self.assertEqual(len(svcs_default), expected_svcs)
                self.assertNotIn("migrator", svcs_default)

                p_mig = subprocess.run(
                    cmd_base + ["--profile", "migrate", "config", "--services"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                )
                svcs_mig = [s.strip() for s in p_mig.stdout.splitlines() if s.strip()]
                self.assertEqual(len(svcs_mig), expected_svcs + 1)
                self.assertIn("migrator", svcs_mig)

    def test_f_p4_ops_02_container_image_id_and_repodigests_parity(self) -> None:
        good_digest = "ghcr.io/company/crm-cskh@sha256:" + ("b" * 64)
        wrong_digest = "ghcr.io/company/crm-cskh@sha256:" + ("c" * 64)
        img_id = "sha256:" + ("d" * 64)
        containers = [
            {"Service": svc, "State": "running", "Name": f"proj-{svc}-1"}
            for svc in staging_mod.APP_SERVICES
        ]
        seen_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> mock.MagicMock:
            seen_cmds.append(cmd)
            m = mock.MagicMock()
            if cmd[:3] == ["docker", "inspect", "--format"]:
                m.stdout = img_id + "\n"
            elif cmd[:4] == ["docker", "image", "inspect", "--format"]:
                self.assertEqual(cmd[5], img_id)
                m.stdout = json.dumps([good_digest]) + "\n"
            else:
                raise AssertionError(f"Unexpected docker command: {cmd}")
            return m

        with mock.patch("staging_https_smoke_and_reconcile.subprocess.run", side_effect=fake_run):
            ok_res = staging_mod.verify_running_containers_digest(containers, good_digest)
            self.assertTrue(ok_res["pass"], ok_res)
            self.assertEqual(ok_res["mismatches"], [])

            bad_res = staging_mod.verify_running_containers_digest(containers, wrong_digest)
            self.assertFalse(bad_res["pass"])
            self.assertEqual(len(bad_res["mismatches"]), len(staging_mod.APP_SERVICES))

        # Empty RepoDigests must fail closed
        def fake_empty_repodigests(cmd: list[str], **_kwargs: object) -> mock.MagicMock:
            m = mock.MagicMock()
            m.stdout = (img_id + "\n") if cmd[1] == "inspect" else "[]\n"
            return m

        with mock.patch(
            "staging_https_smoke_and_reconcile.subprocess.run", side_effect=fake_empty_repodigests
        ):
            empty_res = staging_mod.verify_running_containers_digest(containers, good_digest)
            self.assertFalse(empty_res["pass"])
            self.assertEqual(len(empty_res["mismatches"]), len(staging_mod.APP_SERVICES))

        # Missing app service must fail closed
        with mock.patch("staging_https_smoke_and_reconcile.subprocess.run", side_effect=fake_run):
            partial_res = staging_mod.verify_running_containers_digest(containers[:-1], good_digest)
            self.assertFalse(partial_res["pass"])
            self.assertEqual(partial_res["missing_services"], [staging_mod.APP_SERVICES[-1]])

    def test_f_p4_ops_03_exact_p3_flag_keys(self) -> None:
        eff = health_mod.resolve_effective_invariant_flags(
            {
                "p3_sla_enabled": True,
                "p3_reporting_enabled": False,
                "sla_enabled": False,
                "reporting_enabled": True,
            }
        )
        self.assertEqual(eff, {"p3_sla_enabled": True, "p3_reporting_enabled": False})

        src = Path(health_mod.__file__).read_text(encoding="utf-8")
        self.assertIn('flags.get("p3_sla_enabled")', src)
        self.assertIn('flags.get("p3_reporting_enabled")', src)
        self.assertNotIn('flags.get("sla_enabled")', src)
        self.assertNotIn('flags.get("reporting_enabled")', src)

    def test_f_p4_ops_04_structured_qa_pass_rate_json_and_published_contact_attempt_sql(self) -> None:
        good_null = json.dumps(
            {
                "metrics": {
                    "QA_PASS_RATE": {
                        "metric_code": "QA_PASS_RATE",
                        "denominator": 0,
                        "numerator": 0,
                        "value": None,
                        "status": "disabled",
                    }
                }
            }
        )
        good_positive = json.dumps(
            {
                "metrics": {
                    "QA_PASS_RATE": {
                        "metric_code": "QA_PASS_RATE",
                        "denominator": 8,
                        "numerator": 7,
                        "value": 87.5,
                        "status": "ok",
                    }
                }
            }
        )
        audit_ok = staging_mod.audit_kpi_snapshot_qa_metrics([good_null, good_positive])
        self.assertEqual(audit_ok["qa_zero_denom_null_snapshots"], 1)
        self.assertEqual(audit_ok["qa_positive_denom_numeric_snapshots"], 1)
        self.assertEqual(audit_ok["false_zero_qa_snapshots"], 0)

        # Reversed keys / float 0.0 / malformed JSON must all be caught structurally
        bad_false_zero = '{"metrics":{"QA_PASS_RATE":{"denominator":0,"status":"ok","value":0.0}}}'
        bad_missing_qa = '{"metrics":{}}'
        audit_bad = staging_mod.audit_kpi_snapshot_qa_metrics([good_null, bad_false_zero, bad_missing_qa, "{bad"])
        self.assertEqual(audit_bad["qa_zero_denom_null_snapshots"], 1)
        self.assertEqual(audit_bad["false_zero_qa_snapshots"], 3)

        for mod in (staging_mod, health_mod):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertIn("qr.contact_attempt = ca.name AND qr.status = 'published'", src)
            self.assertNotIn("qr.work_item = ca.work_item", src)
            self.assertNotIn("docstatus < 2", src)

    def test_f_p4_ops_05_csrf_redaction_vi_heading_and_cron_complete_or_failed(self) -> None:
        # 1. Extract real token from `window.cskhCsrfHeader` even when `frappe.csrf_token = "None"` precedes it
        realistic_shell_html = """
        <html lang="vi">
        <body>
          <script>window.frappe = {csrf_token: "None"};</script>
          <h1 class="cskh-title">Bảng điều khiển phân phối</h1>
          <script>
            window.cskhCsrfHeader = {"X-Frappe-CSRF-Token": "csrfSecretToken987654321abcdef"};
            window.frappe.csrf_token = window.cskhCsrfHeader["X-Frappe-CSRF-Token"];
          </script>
        </body>
        </html>
        """
        tok = staging_mod.extract_csrf_token(realistic_shell_html)
        self.assertEqual(tok, "csrfSecretToken987654321abcdef")

        # 2. Redact both X-Frappe-CSRF-Token header/JSON and raw token
        sample_leak = (
            'Header X-Frappe-CSRF-Token: headerTok111222333 and '
            '{"X-Frappe-CSRF-Token": "jsonTok444555666"} and raw csrfSecretToken987654321abcdef'
        )
        scrub1 = staging_mod.redact_sensitive(sample_leak, csrf_tokens=[tok])
        scrub2 = health_mod.redact_payload_text(sample_leak, csrf_tokens=[tok])
        for s in (scrub1, scrub2):
            self.assertNotIn("headerTok111222333", s)
            self.assertNotIn("jsonTok444555666", s)
            self.assertNotIn("csrfSecretToken987654321abcdef", s)
            self.assertIn("<REDACTED_CSRF>", s)

        # 3. Mandatory Vietnamese `<h1>` heading check
        en_only_html = '<html><body><h1 class="cskh-title">Distribution dashboard</h1></body></html>'
        en_check = staging_mod.evaluate_vi_ui_and_csrf_check(
            dash_code=200,
            dash_html=en_only_html,
            csrf_token=tok,
            bad_csrf_code=400,
            good_csrf_code=200,
            good_csrf_bytes=b'{"message":{"app":"crm_cskh"}}',
        )
        self.assertFalse(en_check["contains_vi_heading"])
        self.assertFalse(en_check["pass"])

        vi_check = staging_mod.evaluate_vi_ui_and_csrf_check(
            dash_code=200,
            dash_html=realistic_shell_html,
            csrf_token=tok,
            bad_csrf_code=400,
            good_csrf_code=200,
            good_csrf_bytes=b'{"message":{"app":"crm_cskh"}}',
        )
        self.assertTrue(vi_check["contains_vi_heading"])
        self.assertTrue(vi_check["csrf_token_extracted"])
        self.assertTrue(vi_check["pass"])

        # 4. Cron never Complete (grace vs past-grace) and latest Failed
        clean_queues = {q: {"queued": 0, "failed": 0} for q in ("short", "default", "long")}
        never_ran_details = {
            job: {
                "registered": True,
                "last_complete_lag_seconds": None,
                "latest_terminal_status": None,
            }
            for job in health_mod.SCHEDULER_CADENCE_MAX_LAG_SECONDS
        }
        in_grace = health_mod.evaluate_scheduler_and_queues(
            scheduler_disabled=False,
            queues=clean_queues,
            cron_job_details=never_ran_details,
            error_log_last_15m=0,
            runtime_age_seconds=120,
            startup_grace_seconds=600,
        )
        self.assertEqual(in_grace["status"], "PASS")
        self.assertEqual(len(in_grace["in_startup_grace_cron_jobs"]), 4)

        past_grace = health_mod.evaluate_scheduler_and_queues(
            scheduler_disabled=False,
            queues=clean_queues,
            cron_job_details=never_ran_details,
            error_log_last_15m=0,
            runtime_age_seconds=601,
            startup_grace_seconds=600,
        )
        self.assertEqual(past_grace["status"], "FAIL")
        self.assertEqual(len(past_grace["never_completed_cron_jobs"]), 4)

        # Latest run Failed must fail immediately even if an earlier Complete run was recent
        latest_failed_details = {
            job: {
                "registered": True,
                "last_complete_lag_seconds": 60,
                "latest_terminal_status": "Complete",
            }
            for job in health_mod.SCHEDULER_CADENCE_MAX_LAG_SECONDS
        }
        sla_job = "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations"
        latest_failed_details[sla_job]["latest_terminal_status"] = "Failed"
        failed_latest = health_mod.evaluate_scheduler_and_queues(
            scheduler_disabled=False,
            queues=clean_queues,
            cron_job_details=latest_failed_details,
            error_log_last_15m=0,
            runtime_age_seconds=45,
            startup_grace_seconds=600,
        )
        self.assertEqual(failed_latest["status"], "FAIL")
        self.assertEqual(failed_latest["failed_latest_cron_jobs"], [sla_job])

    def test_staging_synthetic_seeder_refuses_production_and_mismatch(self) -> None:
        import seed_staging_synthetic as seeder

        ok_prod, errs_prod = seeder.validate_staging_seed_target(
            {"COMPOSE_PROJECT_NAME": "crm-cskh-prod", "SITE_NAME": "crm.company.vn"},
            "crm.company.vn",
        )
        self.assertFalse(ok_prod)
        self.assertGreaterEqual(len(errs_prod), 2)

        ok_mismatch, _ = seeder.validate_staging_seed_target(
            {"COMPOSE_PROJECT_NAME": "crm-cskh-staging", "SITE_NAME": "staging-crm.company.vn"},
            "wrong-site.company.vn",
        )
        self.assertFalse(ok_mismatch)

        ok_stg, errs_stg = seeder.validate_staging_seed_target(
            {"COMPOSE_PROJECT_NAME": "crm-cskh-staging", "SITE_NAME": "staging-crm.company.vn"},
            "staging-crm.company.vn",
        )
        self.assertTrue(ok_stg, errs_stg)


if __name__ == "__main__":
    unittest.main()
