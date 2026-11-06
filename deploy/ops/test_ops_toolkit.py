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

    def test_rehearsal_runner_persists_scrubbed_json_and_log_after_cleanup_on_mid_run_error(self) -> None:
        import subprocess
        import run_local_ops_rehearsal as reh_mod

        call_events: list[str] = []
        captured_secrets: dict[str, str] = {}
        captured_secret_file: list[Path] = []

        def fake_inspect_proj(_proj: str) -> dict[str, list[str]]:
            call_events.append("inspect_project")
            return {"containers": [], "volumes": [], "networks": []}

        def fake_snapshot_host() -> dict[str, set[str]]:
            call_events.append("snapshot_host")
            return {
                "containers": {"owner-keep-c1"},
                "volumes": {"owner-keep-v1"},
                "networks": {"owner-keep-n1"},
            }

        def fake_inspect_img(_img: str) -> tuple[bool, dict[str, object], str]:
            return (
                True,
                {
                    "id": "sha256:" + ("a" * 64),
                    "repo_digests": ["crm-cskh@sha256:" + ("b" * 64)],
                },
                "",
            )

        healthy_10 = "\n".join(
            json.dumps({"Service": s, "State": "running", "Health": "healthy"})
            for s in (
                "backend",
                "db",
                "frontend",
                "proxy",
                "queue-long",
                "queue-short",
                "redis-cache",
                "redis-queue",
                "scheduler",
                "websocket",
            )
        )

        def fake_sub_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "git":
                out = ("deadbeef" * 5 + "\n") if "rev-parse" in cmd else ""
                return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

            if cmd[:2] == ["docker", "compose"]:
                if "--env-file" in cmd:
                    ef = Path(cmd[cmd.index("--env-file") + 1])
                    if ef.exists() and not captured_secret_file:
                        parsed = staging_mod.parse_env_file(ef)
                        sf = Path(parsed["DB_PASSWORD_SECRETS_FILE"])
                        captured_secret_file.append(sf)
                        raw_bytes = sf.read_bytes()
                        self.assertTrue(raw_bytes.endswith(b"\n"))
                        self.assertNotIn(b"\r", raw_bytes)
                        captured_secrets["file_db_pw"] = raw_bytes.decode("ascii").strip()

                if "ps" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=healthy_10 + "\n", stderr="")
                if "down" in cmd and "--volumes" in cmd and "--remove-orphans" in cmd:
                    call_events.append("teardown_down_volumes")
                    return subprocess.CompletedProcess(cmd, 0, stdout="Removed\n", stderr="")
                if "config" in cmd:
                    svcs = [
                        "backend",
                        "configurator",
                        "db",
                        "frontend",
                        "proxy",
                        "queue-long",
                        "queue-short",
                        "redis-cache",
                        "redis-queue",
                        "scheduler",
                        "websocket",
                    ]
                    if "--profile" in cmd and "migrate" in cmd:
                        svcs.append("migrator")
                    out = "\n".join(svcs) + "\n" if "--services" in cmd else "name: test\n"
                    return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
                if "exec" in cmd and "SEED_RESULT=" in cmd[-1]:
                    inp = json.loads(str(kwargs.get("input", "{}")))
                    captured_secrets.update(inp)
                    seed_out = json.dumps(
                        {
                            "aba_work_item": "WI-0001",
                            "users": {},
                            "period_key": "2026-11",
                            "team_a": "TA",
                            "team_b": "TB",
                            "golden_employee": "EMP-1",
                        }
                    )
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=f"SEED_RESULT={seed_out}\n", stderr=""
                    )
                if "exec" in cmd and "DELETE sjl FROM" in cmd[-1]:
                    leak_stdout = (
                        f"db={captured_secrets['db_pw']} adm={captured_secrets['adm_pw']} "
                        f"seed={captured_secrets['seed_pw']} sid=liveSecretSid998877 "
                        f"phone=0912345678"
                    )
                    leak_stderr = (
                        'X-Frappe-CSRF-Token: liveSecretCsrfToken665544 | '
                        '{"X-Frappe-CSRF-Token": "jsonCsrfToken332211"} | +84987654321'
                    )
                    raise subprocess.CalledProcessError(
                        2, cmd, output=leak_stdout, stderr=leak_stderr
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            script_str = " ".join(str(x) for x in cmd)
            if "staging_https_smoke_and_reconcile.py" in script_str:
                if "snapshot-counts" in cmd:
                    out_p = Path(cmd[cmd.index("--out") + 1])
                    out_p.write_text("{}", encoding="utf-8", newline="\n")
                    return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
                if "compare-counts" in cmd:
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout=json.dumps({"status": "PASS", "violation_count": 0}), stderr=""
                    )
                if "verify-digest" in cmd:
                    exp = cmd[cmd.index("--expected-image") + 1]
                    if exp.endswith("f" * 64):
                        return subprocess.CompletedProcess(
                            cmd,
                            2,
                            stdout=json.dumps(
                                {
                                    "status": "FAIL",
                                    "container_digest_parity": {"mismatches": [1, 2, 3, 4, 5, 6]},
                                }
                            ),
                            stderr="",
                        )
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps(
                            {
                                "status": "PASS",
                                "container_digest_parity": {
                                    "inspected_services": [1, 2, 3, 4, 5, 6]
                                },
                            }
                        ),
                        stderr="",
                    )

            raise AssertionError(f"Unexpected subprocess command in test: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "evidence" / "rehearsal-fail.json"
            out_log = Path(tmp) / "evidence" / "rehearsal-fail.log"
            with (
                mock.patch.object(reh_mod.smoke_lib, "inspect_project_resources", side_effect=fake_inspect_proj),
                mock.patch.object(reh_mod.smoke_lib, "snapshot_host_docker_resources", side_effect=fake_snapshot_host),
                mock.patch.object(reh_mod.smoke_lib, "inspect_image_metadata", side_effect=fake_inspect_img),
                mock.patch.object(reh_mod.subprocess, "run", side_effect=fake_sub_run),
            ):
                rc = reh_mod.run_rehearsal(
                    app_image="sha256:" + ("a" * 64),
                    image_git_sha="4de3dc8656b0077cabd4220afc6636da543e34f6",
                    out_json=out_json,
                    out_log=out_log,
                    run_id="20261106999999-errtst",
                )

            self.assertEqual(rc, 2)
            self.assertTrue(out_json.exists())
            self.assertTrue(out_log.exists())

            # Teardown (`down --volumes --remove-orphans`) MUST happen before post-cleanup checks
            self.assertIn("teardown_down_volumes", call_events)
            td_idx = call_events.index("teardown_down_volumes")
            self.assertIn("inspect_project", call_events[td_idx + 1 :])
            self.assertIn("snapshot_host", call_events[td_idx + 1 :])
            self.assertFalse(captured_secret_file[0].exists())

            raw_json_bytes = out_json.read_bytes()
            raw_log_bytes = out_log.read_bytes()
            for raw_b in (raw_json_bytes, raw_log_bytes):
                self.assertTrue(raw_b.endswith(b"\n"))
                self.assertNotIn(b"\r", raw_b)

            json_txt = raw_json_bytes.decode("utf-8")
            log_txt = raw_log_bytes.decode("utf-8")
            for forbidden in (
                captured_secrets["file_db_pw"],
                captured_secrets["db_pw"],
                captured_secrets["adm_pw"],
                captured_secrets["seed_pw"],
                "liveSecretSid998877",
                "liveSecretCsrfToken665544",
                "jsonCsrfToken332211",
                "0912345678",
                "+84987654321",
            ):
                self.assertNotIn(forbidden, json_txt)
                self.assertNotIn(forbidden, log_txt)

            self.assertIn("<REDACTED_SECRET>", json_txt)
            self.assertIn("<REDACTED_FRAPPE_SID>", json_txt)
            self.assertIn("<REDACTED_CSRF>", json_txt)
            self.assertIn("<REDACTED_PHONE>", json_txt)

            parsed = json.loads(json_txt)
            self.assertEqual(parsed["run_id"], "20261106999999-errtst")
            self.assertEqual(parsed["status"], "FAIL")
            self.assertEqual(parsed["failed_step"], "phase_b_and_f_p4_ops_05_cron")
            self.assertEqual(parsed["exit_code"], 2)
            self.assertEqual(parsed["measured_phases"], {"p4_03_migration_x2_rehearsal": "PASS"})
            self.assertTrue(parsed["cleanup_verification"]["stack_started"])
            self.assertTrue(parsed["cleanup_verification"]["teardown_invoked"])
            self.assertEqual(parsed["cleanup_verification"]["teardown_exit_code"], 0)
            self.assertTrue(parsed["cleanup_verification"]["tmpdir_removed"])
            self.assertTrue(parsed["cleanup_verification"]["pass"])

            self.assertIn("run_id=20261106999999-errtst", log_txt)
            self.assertIn("failed_step=phase_b_and_f_p4_ops_05_cron", log_txt)
            self.assertIn("exit_code=2", log_txt)
            self.assertIn('measured_phases={"p4_03_migration_x2_rehearsal": "PASS"}', log_txt)
            self.assertIn("cleanup_pass=True", log_txt)

    def test_rehearsal_runner_persists_json_and_log_when_interrupted_at_bootstrap_like_c61a1b(self) -> None:
        import signal
        import subprocess
        import run_local_ops_rehearsal as reh_mod

        teardown_called = False
        infra_healthy = "\n".join(
            json.dumps({"Service": s, "State": "running", "Health": "healthy"})
            for s in ("db", "redis-cache", "redis-queue")
        )

        def fake_sub_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal teardown_called
            if cmd[0] == "git":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:2] == ["docker", "compose"]:
                if "ps" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=infra_healthy + "\n", stderr="")
                if "down" in cmd and "--volumes" in cmd:
                    teardown_called = True
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if "exec" in cmd and "SEED_RESULT=" in cmd[-1]:
                    raise reh_mod._TerminationSignalError(int(signal.SIGTERM))
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "c61a1b-sim.json"
            out_log = Path(tmp) / "c61a1b-sim.log"
            with (
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_project_resources",
                    return_value={"containers": [], "volumes": [], "networks": []},
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "snapshot_host_docker_resources",
                    return_value={"containers": set(), "volumes": set(), "networks": set()},
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_image_metadata",
                    return_value=(True, {"id": "sha256:" + ("a" * 64)}, ""),
                ),
                mock.patch.object(reh_mod.subprocess, "run", side_effect=fake_sub_run),
            ):
                rc = reh_mod.run_rehearsal(
                    app_image="sha256:" + ("a" * 64),
                    image_git_sha="4de3dc8656b0077cabd4220afc6636da543e34f6",
                    out_json=out_json,
                    out_log=out_log,
                    run_id="20261106054158-c61a1b",
                )

            self.assertEqual(rc, 128 + int(signal.SIGTERM))
            self.assertTrue(teardown_called)
            parsed = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(parsed["run_id"], "20261106054158-c61a1b")
            self.assertEqual(parsed["status"], "FAIL")
            self.assertEqual(parsed["failed_step"], "site_bootstrap_and_seed")
            self.assertEqual(parsed["exit_code"], 128 + int(signal.SIGTERM))
            self.assertEqual(parsed["measured_phases"], {})
            self.assertTrue(parsed["cleanup_verification"]["pass"])

    def test_rehearsal_runner_persists_json_and_log_on_preflight_error_without_stack_teardown(self) -> None:
        import subprocess
        import run_local_ops_rehearsal as reh_mod

        compose_called = False

        def fake_sub_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal compose_called
            if cmd[:2] == ["docker", "compose"]:
                compose_called = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "preflight-fail.json"
            out_log = Path(tmp) / "preflight-fail.log"
            with (
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_project_resources",
                    return_value={"containers": [], "volumes": [], "networks": []},
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "snapshot_host_docker_resources",
                    return_value={"containers": set(), "volumes": set(), "networks": set()},
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_image_metadata",
                    return_value=(False, {}, "No such image locally"),
                ),
                mock.patch.object(reh_mod.subprocess, "run", side_effect=fake_sub_run),
            ):
                rc = reh_mod.run_rehearsal(
                    app_image="sha256:" + ("e" * 64),
                    image_git_sha="4de3dc8656b0077cabd4220afc6636da543e34f6",
                    out_json=out_json,
                    out_log=out_log,
                    run_id="20261106999999-preflt",
                )

            self.assertEqual(rc, 1)
            self.assertFalse(compose_called)
            parsed = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(parsed["status"], "FAIL")
            self.assertEqual(parsed["failed_step"], "preflight_inspect_app_image")
            self.assertEqual(parsed["exit_code"], 1)
            self.assertEqual(parsed["measured_phases"], {})
            self.assertFalse(parsed["cleanup_verification"]["stack_started"])
            self.assertFalse(parsed["cleanup_verification"]["teardown_invoked"])
            self.assertTrue(parsed["cleanup_verification"]["pass"])


if __name__ == "__main__":
    unittest.main()
