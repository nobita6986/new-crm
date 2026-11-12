#!/usr/bin/env python3
"""Unit tests for P4-03 / P4-07 / P4-10 operational toolkit (`deploy/ops/`)."""

from __future__ import annotations

import json
import mimetypes
import os
import sys

mimetypes.init(files=[])

import http.server  # noqa: E402
import socketserver  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

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
    def test_health_uses_explicit_domain_overlay_last_and_refuses_missing_overlay(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "compose.domain.yaml"
            overlay.write_text("services: {}\n", encoding="utf-8")
            baseline = health_mod._compose_cmd(Path("staging.env"), "isolated-smoke")
            actual = health_mod._compose_cmd(Path("staging.env"), "isolated-smoke", (overlay,))
            self.assertEqual(actual, baseline + ["-f", str(overlay)])
            stg_base = staging_mod._compose_cmd(Path("staging.env"), "tls")
            stg_actual = staging_mod._compose_cmd(Path("staging.env"), "tls", (overlay,))
            self.assertEqual(stg_actual, stg_base + ["-f", str(overlay)])
            overlay.unlink()
            with self.assertRaisesRegex(ValueError, "Compose file does not exist"):
                health_mod._compose_cmd(Path("staging.env"), "isolated-smoke", (overlay,))
            with self.assertRaisesRegex(ValueError, "Compose file does not exist"):
                staging_mod._compose_cmd(Path("staging.env"), "tls", (overlay,))

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

    def test_https_sid_cookie_policy_requires_secure_and_httponly_strictly_on_sid(
        self,
    ) -> None:
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

    def test_migration_snapshot_reconciliation_catches_drift_and_blg_p4_mig_01(
        self,
    ) -> None:
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
        self.assertIn(
            "INV-05_kpi_snapshot_and_dirty_scope_integrity",
            broken_res["failed_invariants"],
        )

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

        def _assert_cooldown_delivery(webhook_url: str) -> None:
            with tempfile.TemporaryDirectory() as tmp:
                dedup_file = Path(tmp) / "alert_dedup.json"
                r1 = health_mod.dispatch_alert_with_cooldown(
                    env,
                    webhook_url,
                    dedup_state_file=dedup_file,
                    cooldown_seconds=900,
                    now_epoch=1_700_000_000.0,
                )
                self.assertEqual(r1["status"], "DELIVERED")
                self.assertNotIn("secret-webhook-token-xyz", r1["webhook_redacted"])

                # Second dispatch inside cooldown window must be suppressed
                r2 = health_mod.dispatch_alert_with_cooldown(
                    env,
                    webhook_url,
                    dedup_state_file=dedup_file,
                    cooldown_seconds=900,
                    now_epoch=1_700_000_120.0,
                )
                self.assertEqual(r2["status"], "SUPPRESSED_COOLDOWN")
                self.assertEqual(len(_WebhookRecorderHandler.received_bodies), 1)

        force_loopback = bool(os.environ.get("CSKH_FORCE_LOOPBACK_SERVER"))
        if (os.name == "posix" or force_loopback) and not os.environ.get("CSKH_NO_LOOPBACK_BIND"):
            with socketserver.TCPServer(("127.0.0.1", 0), _WebhookRecorderHandler) as httpd:
                port = httpd.server_address[1]
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                try:
                    _assert_cooldown_delivery(f"http://127.0.0.1:{port}/secret-webhook-token-xyz")
                finally:
                    httpd.shutdown()
        else:
            class _FakeResp:
                status = 200

                def __enter__(self) -> _FakeResp:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

                def read(self) -> bytes:
                    return b'{"ok":true}'

            def _fake_urlopen(req: object, **_kwargs: object) -> _FakeResp:
                data = getattr(req, "data", b"") or b""
                _WebhookRecorderHandler.received_bodies.append(bytes(data))
                return _FakeResp()

            with mock.patch.object(health_mod.urllib.request, "urlopen", side_effect=_fake_urlopen):
                _assert_cooldown_delivery("https://127.0.0.1:8443/secret-webhook-token-xyz")

    def test_compose_modes_match_p4_01_validator_profiles(self) -> None:
        import re
        import shutil
        import subprocess

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compose" / "scripts"))
        import validate_compose_config as vcc

        expected_tls = tuple(Path(p).resolve() for p in vcc.STACK_PROFILES["staging"]["compose_files"])
        expected_smoke = tuple(Path(p).resolve() for p in vcc.STACK_PROFILES["isolated-smoke"]["compose_files"])
        expected_dr_https = tuple(
            Path(p).resolve() for p in vcc.STACK_PROFILES["staging-dr-https"]["compose_files"]
        )
        self.assertEqual(len(expected_tls), 8)
        self.assertEqual(len(expected_smoke), 8)
        self.assertEqual(len(expected_dr_https), 8)
        self.assertEqual(tuple(p.resolve() for p in health_mod.COMPOSE_MODES["tls"]), expected_tls)
        self.assertEqual(
            tuple(p.resolve() for p in health_mod.COMPOSE_MODES["isolated-smoke"]),
            expected_smoke,
        )
        self.assertEqual(
            tuple(p.resolve() for p in health_mod.COMPOSE_MODES["dr-https"]),
            expected_dr_https,
        )
        self.assertEqual(tuple(p.resolve() for p in staging_mod.COMPOSE_MODES["tls"]), expected_tls)
        self.assertEqual(
            tuple(p.resolve() for p in staging_mod.COMPOSE_MODES["isolated-smoke"]),
            expected_smoke,
        )
        self.assertEqual(
            tuple(p.resolve() for p in staging_mod.COMPOSE_MODES["dr-https"]),
            expected_dr_https,
        )

        # Verify both runbooks list all 8 Compose files in exact order (F-P4-OPS-01)
        repo_root = staging_mod.REPO_ROOT
        for doc_rel in (
            "docs/p4/p4-03-staging-checklist-and-rehearsal.md",
            "docs/p4/release-runbook.md",
        ):
            doc_text = (repo_root / doc_rel).read_text(encoding="utf-8")
            doc_files = tuple(
                (repo_root / m.group(1)).resolve() for m in re.finditer(r"-f\s+(deploy/compose/[^\s\"\\]+)", doc_text)
            )
            self.assertEqual(doc_files, expected_tls, f"Compose list mismatch in {doc_rel}")

        # Verify `docker compose config` renders cleanly across both modes & migrate profile
        if shutil.which("docker") and (
            os.name == "posix" or bool(os.environ.get("CSKH_ENABLE_WINDOWS_DOCKER_CLI_TEST"))
        ):
            with tempfile.TemporaryDirectory() as dcfg_tmp:
                docker_env = dict(os.environ, DOCKER_CONFIG=dcfg_tmp)
                probe = subprocess.run(
                    ["docker", "compose", "version"],
                    capture_output=True,
                    text=True,
                    env=docker_env,
                )
                if probe.returncode == 0:
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
                            env=docker_env,
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
                            env=docker_env,
                            check=True,
                        )
                        svcs_mig = [s.strip() for s in p_mig.stdout.splitlines() if s.strip()]
                        self.assertEqual(len(svcs_mig), expected_svcs + 1)
                        self.assertIn("migrator", svcs_mig)

    def test_f_p4_ops_02_container_image_id_and_repodigests_parity(self) -> None:
        good_digest = "ghcr.io/company/crm-cskh@sha256:" + ("b" * 64)
        wrong_digest = "ghcr.io/company/crm-cskh@sha256:" + ("c" * 64)
        img_id = "sha256:" + ("d" * 64)
        containers = [{"Service": svc, "State": "running", "Name": f"proj-{svc}-1"} for svc in staging_mod.APP_SERVICES]
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
            "staging_https_smoke_and_reconcile.subprocess.run",
            side_effect=fake_empty_repodigests,
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

    def test_f_p4_ops_04_structured_qa_pass_rate_json_and_published_contact_attempt_sql(
        self,
    ) -> None:
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

    def test_f_p4_ops_05_csrf_redaction_vi_heading_and_cron_complete_or_failed(
        self,
    ) -> None:
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
            "Header X-Frappe-CSRF-Token: headerTok111222333 and "
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

        rec_ws_html = '<html><body><h1>Không gian tuyển dụng</h1></body></html>'
        self.assertTrue(staging_mod.has_vi_dashboard_heading(rec_ws_html))

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
            {
                "COMPOSE_PROJECT_NAME": "crm-cskh-staging",
                "SITE_NAME": "staging-crm.company.vn",
            },
            "wrong-site.company.vn",
        )
        self.assertFalse(ok_mismatch)

        ok_stg, errs_stg = seeder.validate_staging_seed_target(
            {
                "COMPOSE_PROJECT_NAME": "crm-cskh-staging",
                "SITE_NAME": "staging-crm.company.vn",
            },
            "staging-crm.company.vn",
        )
        self.assertTrue(ok_stg, errs_stg)

        # Must also refuse Owner's live VPS project/site even though they contain 'staging'
        ok_vps, errs_vps = seeder.validate_staging_seed_target(
            {
                "COMPOSE_PROJECT_NAME": "crm-cskh-vps-staging",
                "SITE_NAME": "crm-staging.localhost",
            },
            "crm-staging.localhost",
        )
        self.assertFalse(ok_vps)
        self.assertGreaterEqual(len(errs_vps), 2)

    def test_backup_freshness_monitor_and_isolated_staging_dr_owner_guards(self) -> None:
        from datetime import datetime, timezone
        import manage_isolated_staging_dr as dr_mod

        ref_now = datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            lc_path = Path(tmp) / "latest-backup-lifecycle.json"
            # Missing file -> FAIL
            missing_eval = health_mod.evaluate_backup_freshness_and_offsite(lc_path, now_utc=ref_now)
            self.assertEqual(missing_eval["status"], "FAIL")

            # Fresh local backup without automated offsite -> PASS local freshness, PARTIAL G07, FAIL when --require-automated-offsite
            lc_path.write_text(
                json.dumps(
                    {
                        "local_backup_status": "PASS",
                        "periodic_offsite_upload_proven": False,
                        "latest_bundle": {
                            "completed_at_utc": "2026-11-09T20:00:00Z",
                            "archive_sha256": "a" * 64,
                            "offsite_verified": True,
                            "offsite_copy_status": "MANUAL_OFFSITE_HASH_VERIFIED",
                        },
                    }
                ),
                encoding="utf-8",
            )
            local_ok = health_mod.evaluate_backup_freshness_and_offsite(
                lc_path, max_age_hours=26.0, require_automated_offsite=False, now_utc=ref_now
            )
            self.assertEqual(local_ok["status"], "PASS")
            self.assertEqual(local_ok["end_to_end_backup_gate_g07_status"], "PARTIAL_PERIODIC_OFFSITE_UNPROVEN")
            self.assertFalse(local_ok["one_off_manual_copy_proves_periodic_upload"])

            req_auto = health_mod.evaluate_backup_freshness_and_offsite(
                lc_path, max_age_hours=26.0, require_automated_offsite=True, now_utc=ref_now
            )
            self.assertEqual(req_auto["status"], "FAIL")

            # Stale (>26h) -> FAIL even without --require-automated-offsite
            lc_path.write_text(
                json.dumps(
                    {
                        "local_backup_status": "PASS",
                        "periodic_offsite_upload_proven": True,
                        "latest_bundle": {
                            "completed_at_utc": "2026-11-08T10:00:00Z",
                            "archive_sha256": "a" * 64,
                            "offsite_verified": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            stale_eval = health_mod.evaluate_backup_freshness_and_offsite(
                lc_path, max_age_hours=26.0, require_automated_offsite=False, now_utc=ref_now
            )
            self.assertEqual(stale_eval["status"], "FAIL")

        # Isolated Staging/DR guard must accept isolated dr-https and reject Owner live / wildcard / port 443
        valid_dr_env = {
            "COMPOSE_PROJECT_NAME": "crm-cskh-staging-dr-https",
            "SITE_NAME": "staging-dr-uat.crm-internal.localhost",
            "INGRESS_BIND_IP": "127.0.0.1",
            "HTTP_PUBLISH_PORT": "18081",
            "HTTPS_PUBLISH_PORT": "18443",
            "DB_PASSWORD_SECRETS_FILE": "/tmp/dr-secret/db_root_password.txt",
        }
        dr_mod.assert_isolated_from_owner_stack(valid_dr_env)

        for bad_override in (
            {"COMPOSE_PROJECT_NAME": "crm-cskh-vps-staging"},
            {"SITE_NAME": "crm-staging.localhost"},
            {"SITE_NAME": "crm.hrpartner.vn"},
            {"INGRESS_BIND_IP": "0.0.0.0"},
            {"HTTPS_PUBLISH_PORT": "443"},
            {"DB_PASSWORD_SECRETS_FILE": "/opt/crm-cskh-staging/secrets/db_root_password.txt"},
        ):
            with self.assertRaises(ValueError):
                dr_mod.assert_isolated_from_owner_stack({**valid_dr_env, **bad_override})

    def test_rehearsal_runner_persists_scrubbed_json_and_log_after_cleanup_on_mid_run_error(
        self,
    ) -> None:
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
                    return subprocess.CompletedProcess(cmd, 0, stdout=f"SEED_RESULT={seed_out}\n", stderr="")
                if "exec" in cmd and "DELETE sjl FROM" in cmd[-1]:
                    leak_stdout = (
                        f"db={captured_secrets['db_pw']} adm={captured_secrets['adm_pw']} "
                        f"seed={captured_secrets['seed_pw']} sid=liveSecretSid998877 "
                        f"phone=0912345678"
                    )
                    leak_stderr = (
                        "X-Frappe-CSRF-Token: liveSecretCsrfToken665544 | "
                        '{"X-Frappe-CSRF-Token": "jsonCsrfToken332211"} | +84987654321'
                    )
                    raise subprocess.CalledProcessError(2, cmd, output=leak_stdout, stderr=leak_stderr)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            script_str = " ".join(str(x) for x in cmd)
            if "staging_https_smoke_and_reconcile.py" in script_str:
                if "snapshot-counts" in cmd:
                    out_p = Path(cmd[cmd.index("--out") + 1])
                    out_p.write_text("{}", encoding="utf-8", newline="\n")
                    return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
                if "compare-counts" in cmd:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"status": "PASS", "violation_count": 0}),
                        stderr="",
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
                                "container_digest_parity": {"inspected_services": [1, 2, 3, 4, 5, 6]},
                            }
                        ),
                        stderr="",
                    )

            raise AssertionError(f"Unexpected subprocess command in test: {cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            out_json = Path(tmp) / "evidence" / "rehearsal-fail.json"
            out_log = Path(tmp) / "evidence" / "rehearsal-fail.log"
            with (
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_project_resources",
                    side_effect=fake_inspect_proj,
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "snapshot_host_docker_resources",
                    side_effect=fake_snapshot_host,
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_image_metadata",
                    side_effect=fake_inspect_img,
                ),
                mock.patch.object(reh_mod, "pick_free_loopback_port", return_value=18180),
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

    def test_rehearsal_runner_persists_json_and_log_when_interrupted_at_bootstrap_like_c61a1b(
        self,
    ) -> None:
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
                    return_value={
                        "containers": set(),
                        "volumes": set(),
                        "networks": set(),
                    },
                ),
                mock.patch.object(
                    reh_mod.smoke_lib,
                    "inspect_image_metadata",
                    return_value=(True, {"id": "sha256:" + ("a" * 64)}, ""),
                ),
                mock.patch.object(reh_mod, "pick_free_loopback_port", return_value=18180),
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

    def test_rehearsal_runner_persists_json_and_log_on_preflight_error_without_stack_teardown(
        self,
    ) -> None:
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
                    return_value={
                        "containers": set(),
                        "volumes": set(),
                        "networks": set(),
                    },
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

    def test_record_key_escrow_and_audit_g07_gate_blocked_vs_pass(self) -> None:
        import hashlib
        import manage_backup_lifecycle as bkl_mod

        repo_root = staging_mod.REPO_ROOT
        vps_dir = repo_root / "deploy" / "ops" / "evidence" / "p4-vps-live-20261109"
        vps_audit = bkl_mod.audit_g07_gate(
            lifecycle_file=vps_dir / "backup-lifecycle.json",
            timers_runtime_file=vps_dir / "timers-runtime.json",
            restore_report_file=vps_dir / "restore-drill-report.json",
        )
        self.assertEqual(vps_audit["gate_g07_status"], "BLOCKED")
        self.assertTrue(vps_audit["proofs"]["local_backup_service_pass"])
        self.assertTrue(vps_audit["proofs"]["backup_timer_enabled"])
        self.assertFalse(vps_audit["proofs"]["timer_triggered_backup_proven"])
        self.assertFalse(vps_audit["proofs"]["automated_offsite_upload_proven"])
        self.assertFalse(vps_audit["proofs"]["remote_checksum_readback_verified"])
        self.assertTrue(vps_audit["proofs"]["manual_offsite_checksum_verified"])
        self.assertFalse(vps_audit["proofs"]["separate_key_escrow_proven"])
        self.assertTrue(vps_audit["proofs"]["vps_isolated_restore_reused_pass"])
        self.assertEqual(vps_audit["reused_vps_restore_drill"]["phase_a_rto_seconds"], 142.598)
        self.assertGreaterEqual(len(vps_audit["required_owner_inputs"]), 2)

        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "scheduled-20261110T191500Z"
            bundle.mkdir(parents=True)
            enc_bytes = b"encrypted-archive-payload-for-unit-test"
            enc_sha = hashlib.sha256(enc_bytes).hexdigest()
            raw_key_secret = "deadbeef" * 8
            (bundle / "backup.tar.gz.enc").write_bytes(enc_bytes)
            (bundle / "backup-key").write_text(raw_key_secret + "\n", encoding="utf-8", newline="\n")
            (bundle / "backup-report.json").write_text(
                json.dumps(
                    {
                        "schema": "p4-encrypted-backup-v2",
                        "archive_sha256": enc_sha,
                        "archive_bytes": len(enc_bytes),
                        "source_revision": "467204176326f046442b5e20c381f9a895b6e176",
                        "completed_at_utc": "2026-11-10T19:16:10Z",
                        "end_to_end_backup_status": "PASS",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (bundle / "offsite-verification.json").write_text(
                json.dumps(
                    {
                        "verified_at_utc": "2026-11-10T19:16:15Z",
                        "archive_sha256": enc_sha,
                        "archive_bytes": len(enc_bytes),
                        "offsite_location_label": "owner-s3-archive-bucket-hn",
                        "transfer_mode": "automated_offsite_sync",
                        "automated_upload": True,
                        "periodic_upload_proven": True,
                        "key_excluded_from_offsite_archive_target": True,
                        "status": "OFFSITE_COPY_HASH_VERIFIED",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            # Reject placeholder or identical vault label as archive offsite target
            with self.assertRaisesRegex(ValueError, "placeholder"):
                bkl_mod.record_key_escrow_confirmation(bundle, "PENDING_OWNER_VAULT")
            with self.assertRaisesRegex(ValueError, "separate from offsite_location_label"):
                bkl_mod.record_key_escrow_confirmation(bundle, "owner-s3-archive-bucket-hn")

            escrow = bkl_mod.record_key_escrow_confirmation(
                bundle,
                "owner-1password-escrow-vault-separate",
                site_encryption_key_escrowed=True,
            )
            escrow_raw = (bundle / "key-escrow-receipt.json").read_text(encoding="utf-8")
            self.assertNotIn(raw_key_secret, escrow_raw)
            self.assertEqual(escrow["status"], "SEPARATE_KEY_ESCROW_VERIFIED")
            self.assertEqual(
                escrow["key_fingerprint_sha256"],
                hashlib.sha256((raw_key_secret + "\n").encode("utf-8")).hexdigest(),
            )

            lifecycle_p = Path(tmp) / "latest-backup-lifecycle.json"
            lifecycle_p.write_text(
                json.dumps(
                    {
                        "local_backup_status": "PASS",
                        "automated_offsite_upload_configured": True,
                        "periodic_offsite_upload_proven": True,
                        "key_separation_enforced": True,
                        "latest_bundle": {
                            "bundle_dir": str(bundle),
                            "local_archive_status": "PASS",
                            "archive_sha256": enc_sha,
                            "offsite_verified": True,
                            "offsite_copy_status": "AUTOMATED_OFFSITE_HASH_VERIFIED",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            timers_p = Path(tmp) / "timers-runtime.json"
            timers_p.write_text(
                json.dumps(
                    {
                        "timers": {
                            "crm-cskh-backup.timer": {
                                "UnitFileState": "enabled",
                                "ActiveState": "active",
                                "LastTriggerUSec": "Tue 2026-11-10 02:16:41 +07",
                                "NextElapseUSecRealtime": "Wed 2026-11-11 02:15:12 +07",
                            }
                        },
                        "services": {
                            "crm-cskh-backup.service": {
                                "Result": "success",
                                "ExecMainStatus": "0",
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            pass_audit = bkl_mod.audit_g07_gate(
                lifecycle_file=lifecycle_p,
                timers_runtime_file=timers_p,
                restore_report_file=vps_dir / "restore-drill-report.json",
                key_escrow_receipt_file=bundle / "key-escrow-receipt.json",
            )
            self.assertEqual(pass_audit["gate_g07_status"], "PASS", pass_audit)
            self.assertEqual(pass_audit["blockers"], [])

            # CLI parser verification matching runbook commands
            parsed_esc = bkl_mod.build_parser().parse_args(
                ["record-key-escrow", "--bundle-dir", str(bundle), "--vault-label", "vault-a"]
            )
            self.assertEqual(parsed_esc.command, "record-key-escrow")
            parsed_g07 = bkl_mod.build_parser().parse_args(
                [
                    "audit-g07-gate",
                    "--lifecycle-file",
                    str(lifecycle_p),
                    "--timers-runtime-file",
                    str(timers_p),
                    "--key-escrow-receipt-file",
                    str(bundle / "key-escrow-receipt.json"),
                    "--restore-report-file",
                    str(vps_dir / "restore-drill-report.json"),
                ]
            )
            self.assertEqual(parsed_g07.command, "audit-g07-gate")

    def test_verify_alert_receipt_file_g19_blocked_pass_and_fail_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_p = Path(tmp) / "missing-receipt.json"
            self.assertEqual(health_mod.verify_alert_receipt_file(missing_p)["status"], "BLOCKED")

            blocked_p = Path(tmp) / "blocked-receipt.json"
            blocked_p.write_text(json.dumps({"status": "BLOCKED"}) + "\n", encoding="utf-8")
            self.assertEqual(health_mod.verify_alert_receipt_file(blocked_p)["status"], "BLOCKED")

            good_p = Path(tmp) / "good-receipt.json"
            good_p.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "alert_envelope": {
                            "alert_code": "ALT-TEST-SYNTHETIC",
                            "owner": "oncall-primary@hrpartner.vn",
                            "summary": "Synthetic P4-07 G19 test alert verification",
                        },
                        "receipt": {
                            "status": "DELIVERED",
                            "http_status": 200,
                            "payload_sha256": "b" * 64,
                            "webhook_redacted": "https://hooks.hrpartner.vn/<REDACTED_WEBHOOK_PATH>",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            good_res = health_mod.verify_alert_receipt_file(good_p)
            self.assertEqual(good_res["status"], "PASS", good_res)

            leaked_p = Path(tmp) / "leaked-receipt.json"
            leaked_p.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "alert_envelope": {
                            "alert_code": "ALT-TEST-SYNTHETIC",
                            "owner": "PENDING_OWNER",
                            "summary": "Leaked phone 0912345678",
                        },
                        "receipt": {
                            "status": "DELIVERED",
                            "http_status": 200,
                            "payload_sha256": "b" * 64,
                            "webhook_redacted": "https://hooks.hrpartner.vn/raw-secret-token",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            bad_res = health_mod.verify_alert_receipt_file(leaked_p)
            self.assertEqual(bad_res["status"], "FAIL")
            self.assertGreaterEqual(len(bad_res["errors"]), 3)

    def test_ast_113_whitelisted_endpoints_and_p4_gate_traceability_docs(self) -> None:
        import ast
        import manage_isolated_staging_dr as dr_mod

        repo_root = staging_mod.REPO_ROOT
        app_root = repo_root / "apps" / "crm_cskh" / "crm_cskh"
        endpoints: list[str] = []
        for py_path in sorted(app_root.rglob("*.py")):
            if "tests" in py_path.parts:
                continue
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
            mod = ".".join(py_path.relative_to(repo_root / "apps" / "crm_cskh").with_suffix("").parts)
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    for dec in node.decorator_list:
                        dec_txt = ast.unparse(dec)
                        if "whitelist" in dec_txt:
                            self.assertNotIn(
                                "allow_guest=True",
                                dec_txt,
                                f"Unexpected allow_guest=True on {mod}.{node.name}",
                            )
                            endpoints.append(f"{mod}.{node.name}")

        self.assertEqual(len(endpoints), 113)
        api_doc = (repo_root / "docs" / "p4" / "api-contract.md").read_text(encoding="utf-8")
        sec_doc = (repo_root / "docs" / "p4" / "security-review.md").read_text(encoding="utf-8")
        uat_doc = (repo_root / "docs" / "p4" / "uat-report.md").read_text(encoding="utf-8")
        perf_doc = (repo_root / "docs" / "p4" / "performance-report.md").read_text(encoding="utf-8")
        exit_doc = (repo_root / "docs" / "p4" / "P4-exit-report.md").read_text(encoding="utf-8")

        missing_api = [ep for ep in endpoints if ep not in api_doc]
        missing_sec = [ep for ep in endpoints if ep not in sec_doc]
        self.assertEqual(missing_api, [], f"Endpoints missing in api-contract.md: {missing_api}")
        self.assertEqual(missing_sec, [], f"Endpoints missing in security-review.md: {missing_sec}")

        for i in range(1, 31):
            code = f"F{i:02d}"
            self.assertIn(code, uat_doc, f"Missing {code} in uat-report.md")
        for i in range(1, 16):
            code = f"D{i:02d}"
            self.assertIn(code, uat_doc, f"Missing {code} in uat-report.md")
        for i in range(1, 21):
            code = f"S{i:02d}"
            self.assertIn(code, sec_doc, f"Missing {code} in security-review.md")
        for i in range(1, 25):
            code = f"G{i:02d}"
            self.assertIn(code, exit_doc, f"Missing {code} in P4-exit-report.md")

        self.assertIn("UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL", perf_doc)
        self.assertIn("cskh-metrics-v2", api_doc)
        self.assertIn("cskh-metrics-v2", exit_doc)

        p4_20_2_mandatory_files = (
            "entry-audit.md",
            "infra-inventory.md",
            "release-manifest.json",
            "security-review.md",
            "backup-restore.md",
            "release-runbook.md",
            "rollback-runbook.md",
            "monitoring-alerts.md",
            "performance-report.md",
            "uat-report.md",
            "api-contract.md",
            "glossary-vi.md",
            "operations-sop.md",
            "P4-exit-report.md",
        )
        for fname in p4_20_2_mandatory_files:
            fpath = repo_root / "docs" / "p4" / fname
            self.assertTrue(fpath.is_file(), f"Missing P4.md 20.2 mandatory file: {fname}")
        rel_manifest = json.loads((repo_root / "docs" / "p4" / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(rel_manifest["kpi_formula_version"], "cskh-metrics-v2")
        self.assertEqual(rel_manifest["promotion_governance"]["production_promotion_status"], "BLOCKED")
        self.assertEqual(rel_manifest["promotion_governance"]["approved_by"], [])

        for i in range(1, 17):
            code = f"P4-8.4-{i:02d}"
            self.assertIn(code, sec_doc, f"Missing {code} in security-review.md")

        infra_doc = (repo_root / "docs" / "p4" / "infra-inventory.md").read_text(encoding="utf-8")
        for req_tok in (
            "COMPOSE_PROJECT_NAME",
            "SITE_NAME",
            "networks",
            "ports",
            "volumes",
            "secrets",
            "crm-cskh-p4-smoke",
            "crm-cskh-staging-dr-https",
            "crm-cskh-vps-staging",
            "crm-cskh-prod",
        ):
            self.assertIn(req_tok, infra_doc, f"Missing G03 token {req_tok} in infra-inventory.md")

        parsed_mig = dr_mod.build_parser().parse_args(
            ["rehearse-migration-x2", "--state-dir", "/tmp/dr-state"]
        )
        self.assertEqual(parsed_mig.command, "rehearse-migration-x2")
        parsed_full = dr_mod.build_parser().parse_args(
            [
                "full-rehearsal",
                "--app-image",
                "sha256:" + ("a" * 64),
                "--n-minus-1-image",
                "sha256:" + ("b" * 64),
                "--browser-report-out",
                "/tmp/browser.json",
                "--out",
                "/tmp/full.json",
            ]
        )
        self.assertEqual(parsed_full.n_minus_1_image, "sha256:" + ("b" * 64))

        import run_https_browser_uat_and_security as brw_mod

        for _route, actor_key in brw_mod.AUTHORIZED_ROUTE_ACTORS:
            self.assertNotIn("administrator", actor_key.lower())
        parsed_brw = brw_mod.build_parser().parse_args(
            [
                "--env-file",
                "/tmp/dr.env",
                "--ca-crt",
                "/tmp/ca.crt",
                "--seed-info-json",
                "/tmp/seed.json",
                "--out",
                "/tmp/brw-out.json",
            ]
        )
        self.assertEqual(parsed_brw.connect_host, "127.0.0.1")

        # Separate G07 and G19 gate audit artifacts must both exist and report BLOCKED honestly
        g07_doc = json.loads(
            (repo_root / "deploy" / "ops" / "evidence" / "p4-https-delta-20261109" / "g07-gate-vps-audit.json").read_text(
                encoding="utf-8"
            )
        )
        g19_doc = json.loads(
            (repo_root / "deploy" / "ops" / "evidence" / "p4-https-delta-20261109" / "g19-gate-vps-audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(g07_doc["gate"], "G07")
        self.assertEqual(g07_doc["gate_g07_status"], "BLOCKED")
        self.assertTrue(g07_doc["proofs"]["st_dev_only_proves_different_filesystem_not_off_host"])
        self.assertEqual(g19_doc["gate"], "G19")
        self.assertEqual(g19_doc["gate_g19_status"], "BLOCKED")
        self.assertFalse(g19_doc["external_alerts_sent"])

        # G21 API contract version lock & migration policy checks
        for g21_tok in (
            "v15.126.0",
            "v1.87.0",
            "a8737dbcce74f9095a6e76c5e93491e92843f3d0",
            "693a3cdcc758dab64b3ac61d8e6f066581d94b362c9649d709684b72d7d0a2e6",
            "Class A",
            "Class B",
            "Class C",
            "Class D",
            "N -> N-1",
        ):
            self.assertIn(g21_tok, api_doc, f"Missing G21 token {g21_tok} in api-contract.md")

        # Offsite st_dev vs destination inventory & G22 4-role vs G24 Operator separation checks
        backup_doc = (repo_root / "docs" / "p4" / "backup-restore.md").read_text(encoding="utf-8")
        sop_doc = (repo_root / "docs" / "p4" / "operations-sop.md").read_text(encoding="utf-8")
        self.assertIn("Destination Inventory", backup_doc)
        self.assertIn("không chứng minh", backup_doc.lower())
        self.assertIn("4 vai trò nghiệp vụ", uat_doc)
        self.assertIn("không biến thành yêu cầu 5 người UAT khác nhau", uat_doc)
        self.assertIn("Khuyến nghị", sop_doc)

        cand_manifest = json.loads(
            (repo_root / "deploy" / "ops" / "evidence" / "p4-integrated-candidate-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        owner_ticket = cand_manifest["owner_pending_inputs_consolidated"]
        self.assertEqual(owner_ticket["canonical_ticket_reference"], "docs/p4/P4-exit-report.md#3")
        self.assertEqual(owner_ticket["ticket_version"], "v1.4")
        for key in (
            "0_ssh_access_for_t1a_isolated_staging_and_g18",
            "1_offsite_destination_inventory_and_credentials",
            "2_separate_key_escrow_location",
            "3_oncall_operator",
            "4_approved_alert_channel",
            "5_uat_4_roles_and_separate_operator_handover",
            "6_release_decision",
        ):
            self.assertIn(key, owner_ticket)
        v14_slo = owner_ticket["6_release_decision"]["attached_slo_set_v1_4"]
        self.assertEqual(set(v14_slo["thresholds"]), set(dr_mod.OWNER_TICKET_V1_4_PROPOSED_SLOS))
        self.assertEqual(v14_slo["thresholds_sha256"], dr_mod.LOCKED_G18_THRESHOLDS_V1_4_SHA256)

    def _build_mock_staging_vps_bundle(
        self,
        repo_root: Path,
        out_dir: Path,
        *,
        expected_digest: str,
        vps_platform: str = "Linux-6.8.0-51-generic-x86_64-with-glibc2.39",
        vps_hostname: str = "vps-hrpartner-103-77-208-186",
        isolated_project: str = "crm-cskh-dr-https-vps",
    ) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        ci_delta_dir = repo_root / "deploy" / "ops" / "evidence" / "p4-https-delta-20261109"
        pull_rec = json.loads((ci_delta_dir / "candidate-registry-pull-inspect-receipt.json").read_text(encoding="utf-8"))
        pull_rec["requested_registry_digest"] = expected_digest
        pull_rec["inspected_repo_digests"] = [expected_digest]

        g03_rec = json.loads((ci_delta_dir / "staging-dr-runtime-isolation-audit.json").read_text(encoding="utf-8"))
        g03_rec["project_name"] = isolated_project
        g03_rec["app_image"] = expected_digest
        g03_rec["container_digest_parity"]["expected_app_image"] = expected_digest
        for svc_name, svc_row in g03_rec["container_digest_parity"]["inspected_services"].items():
            svc_row["container"] = f"{isolated_project}-{svc_name}-1"
            svc_row["repo_digests"] = [expected_digest]
            svc_row["matched"] = True

        dr_rep = json.loads((ci_delta_dir / "isolated-staging-dr-https-report.json").read_text(encoding="utf-8"))
        g05_smoke = json.loads(json.dumps(dr_rep["phases"]["bootstrap_and_https_smoke"]["https_smoke"]))
        g05_smoke["app_image"] = expected_digest
        g05_smoke["checks"]["container_digest_parity"] = json.loads(
            json.dumps(g03_rec["container_digest_parity"])
        )

        snap_live = {
            "timestamp_utc": "2026-11-11T13:00:00Z",
            "live_project_name": "crm-cskh-vps-staging",
            "container_count": 1,
            "all_running": True,
            "containers": [
                {
                    "id": "cid-live-1",
                    "name": "crm-cskh-vps-staging-backend-1",
                    "service": "backend",
                    "image": "sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9",
                    "config_image": "crm-cskh:0.0.1-4672041",
                    "status": "running",
                    "health": "healthy",
                    "started_at": "2026-11-09T12:00:00Z",
                    "restart_count": 0,
                }
            ],
        }
        preflight = {
            "schema_version": "cskh-p4-readonly-vps-preflight-v1",
            "timestamp_utc": "2026-11-11T13:00:00Z",
            "mode": "READ_ONLY_ZERO_MUTATION",
            "status": "PASS",
            "host_identity": {
                "hostname": vps_hostname,
                "platform": vps_platform,
                "kernel_release": "6.8.0-51-generic",
            },
            "host_resources": {
                "cpu_cores": 4,
                "mem_total_mib": 15984.0,
                "mem_available_mib": 9200.0,
                "required_avail_ram_mib": 1536.0,
                "disk_check_path": "/",
                "disk_total_gib": 120.0,
                "disk_free_gib": 68.5,
                "required_free_disk_gib": 12.0,
            },
            "planned_isolated_loopback_ports": {
                "bind_ip": "127.0.0.1",
                "http_port": 18081,
                "http_port_free": True,
                "https_port": 18443,
                "https_port_free": True,
            },
            "checks": {
                "cpu_cores_ge_2": True,
                "available_ram_sufficient": True,
                "free_disk_sufficient": True,
                "planned_loopback_ports_free": True,
                "live_stack_running_untouched": True,
            },
            "live_before_snapshot": snap_live,
        }
        import manage_isolated_staging_dr as dr_mod

        live_pres = dr_mod.compare_live_stack_preservation(snap_live, json.loads(json.dumps(snap_live)))
        summary = {
            "schema_version": "cskh-p4-vps-g03-g05-bundle-v1",
            "timestamp_utc": "2026-11-11T13:10:00Z",
            "status": "PASS",
            "app_image": expected_digest,
            "host_identity": preflight["host_identity"],
            "isolated_project_name": isolated_project,
            "isolated_site_name": "staging-dr-uat.crm-internal.localhost",
            "kept_up_for_g18": True,
            "live_project_untouched": "crm-cskh-vps-staging",
            "zero_live_mutation": True,
            "inspected_app_services": [
                "backend",
                "frontend",
                "websocket",
                "queue-short",
                "queue-long",
                "scheduler",
            ],
            "artifacts": {
                "readonly_vps_preflight": str(out_dir / "readonly-vps-preflight.json"),
                "candidate_pull_inspect_receipt": str(out_dir / "candidate-registry-pull-inspect-receipt.json"),
                "runtime_isolation_audit_g03": str(out_dir / "staging-dr-runtime-isolation-audit.json"),
                "https_wss_smoke_g05": str(out_dir / "https-wss-smoke-receipt.json"),
                "live_preservation_before_after": str(out_dir / "live-preservation-before-after.json"),
            },
        }
        files_to_write = {
            "readonly-vps-preflight.json": preflight,
            "candidate-registry-pull-inspect-receipt.json": pull_rec,
            "staging-dr-runtime-isolation-audit.json": g03_rec,
            "https-wss-smoke-receipt.json": g05_smoke,
            "live-preservation-before-after.json": live_pres,
            "vps-g03-g05-summary.json": summary,
        }
        for fname, payload in files_to_write.items():
            (out_dir / fname).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
            )
        return out_dir

    def test_vps_live_preservation_and_g18_v1_4_slo_reconciliation(self) -> None:
        import manage_isolated_staging_dr as dr_mod

        repo_root = staging_mod.REPO_ROOT
        ci_bench_doc = json.loads(
            (
                repo_root
                / "deploy"
                / "ops"
                / "evidence"
                / "p4-candidate-and-s1-20261109"
                / "benchmark-s1-report.json"
            ).read_text(encoding="utf-8")
        )

        # Verify canonical threshold file & SHA-256 cryptographic lock
        canon_spec, canon_sha, canon_errs = dr_mod.load_and_validate_g18_thresholds_v1_4()
        self.assertEqual(canon_errs, [])
        self.assertEqual(canon_sha, dr_mod.LOCKED_G18_THRESHOLDS_V1_4_SHA256)
        self.assertEqual(len(canon_spec["precheck_7_metrics"]), 7)
        self.assertEqual(len(canon_spec["section_3_2A_cases_and_session_models"]), 22)

        valid_approval = {
            "approved": True,
            "ticket_version": "v1.4",
            "signed_by": "owner-representative@hrpartner.vn",
            "signed_at": "2026-11-11T20:00:00+07:00",
            "thresholds_sha256": dr_mod.LOCKED_G18_THRESHOLDS_V1_4_SHA256,
            "approved_thresholds": {
                k: spec["threshold"] for k, spec in dr_mod.OWNER_TICKET_V1_4_PROPOSED_SLOS.items()
            },
        }

        # 1. CI benchmark report (unsigned): 7-metric precheck + §3.2A-C pass, but status stays CI-blocked
        unsigned_ci = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(ci_bench_doc)
        self.assertEqual(
            unsigned_ci["status"],
            "BLOCKED_CI_PRECHECK_ONLY_PENDING_VPS_RECEIPTS_AND_OWNER_APPROVAL",
        )
        self.assertTrue(unsigned_ci["is_ci_benchmark_report"])
        self.assertTrue(unsigned_ci["measurements_complete"])
        self.assertEqual(unsigned_ci["missing_measurements"], [])
        self.assertEqual(unsigned_ci["precheck_7_metrics"]["status"], "PRECHECK_PASS")
        self.assertTrue(unsigned_ci["all_7_metrics_within_v1_4_thresholds"])
        self.assertTrue(unsigned_ci["section_3_2_reconciliation"]["all_passed"])
        self.assertEqual(unsigned_ci["section_3_2_reconciliation"]["section_3_2A"]["cases_passed"], 22)
        self.assertEqual(
            set(unsigned_ci["section_3_2_reconciliation"]["section_3_2A"]["by_session_model_summary"]),
            {
                "shared_session",
                "shared_new_session",
                "single_initialized_session",
                "40_distinct_staff_sessions",
                "40_distinct_staff_sessions_idempotent_post",
                "single_initialized_session_peak_c40",
                "bod_role_session",
            },
        )

        # 2. CI benchmark report EVEN WITH valid Owner signature + thresholds_sha256 MUST NOT PASS G18
        signed_ci = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
            ci_bench_doc, owner_slo_approval=valid_approval
        )
        self.assertEqual(
            signed_ci["status"],
            "BLOCKED_CI_REPORT_REQUIRES_STAGING_VPS_BENCHMARK_AND_RECEIPTS",
        )
        self.assertNotEqual(signed_ci["status"], "PASS")
        self.assertTrue(signed_ci["owner_slo_v1_4_signed"])
        self.assertFalse(signed_ci["g18_pass_requirements"]["staging_vps_benchmark_and_receipts_verified"])

        with tempfile.TemporaryDirectory(prefix="g18-v14-test-") as td:
            td_path = Path(td)
            vps_receipt_dir = self._build_mock_staging_vps_bundle(
                repo_root,
                td_path / "vps-receipts",
                expected_digest=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
            )

            # Pairing CI Azure benchmark report with Staging VPS receipts must fail closed
            ci_plus_vps = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                ci_bench_doc,
                owner_slo_approval=valid_approval,
                vps_receipts=vps_receipt_dir,
            )
            self.assertEqual(ci_plus_vps["status"], "FAIL_INVALID_STAGING_VPS_RECEIPTS")
            self.assertIn(
                "benchmark_report_is_ci_runner_cannot_pair_with_staging_vps_receipts",
                ci_plus_vps["staging_vps_receipt_validation_errors"],
            )

            # Build non-CI Staging VPS benchmark report on same host platform & locked @sha256 digest
            vps_bench_doc = json.loads(json.dumps(ci_bench_doc))
            vps_bench_doc["s1_domain_and_database_benchmark"]["runner_hardware"]["platform"] = (
                "Linux-6.8.0-51-generic-x86_64-with-glibc2.39"
            )
            vps_bench_doc["isolation_guard"]["compose_project"] = "crm-cskh-s1-bench-vps"

            # 3. Non-CI VPS benchmark without Staging VPS receipts -> BLOCKED_MISSING_STAGING_VPS_RECEIPTS
            no_receipts_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_bench_doc,
                owner_slo_approval=valid_approval,
            )
            self.assertEqual(no_receipts_res["status"], "BLOCKED_MISSING_STAGING_VPS_RECEIPTS")

            # 4. Positive control: complete measurements + locked digest + Staging VPS receipts + SHA-256 approval -> PASS
            pass_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_bench_doc,
                owner_slo_approval=valid_approval,
                vps_receipts=vps_receipt_dir,
            )
            self.assertEqual(pass_res["status"], "PASS", pass_res)
            self.assertTrue(all(pass_res["g18_pass_requirements"].values()))

            # CLI reconcile-g18-slo returns 0 on full Staging VPS PASS and 2 on CI report
            vps_bench_file = td_path / "vps-bench.json"
            ci_bench_file = td_path / "ci-bench.json"
            approval_file = td_path / "owner-approval.json"
            out_pass_file = td_path / "reconcile-pass.json"
            vps_bench_file.write_text(json.dumps(vps_bench_doc), encoding="utf-8")
            ci_bench_file.write_text(json.dumps(ci_bench_doc), encoding="utf-8")
            approval_file.write_text(json.dumps(valid_approval), encoding="utf-8")
            self.assertEqual(
                dr_mod.main(
                    [
                        "reconcile-g18-slo",
                        "--benchmark-json",
                        str(vps_bench_file),
                        "--vps-receipt-dir",
                        str(vps_receipt_dir),
                        "--owner-approval-json",
                        str(approval_file),
                        "--out",
                        str(out_pass_file),
                    ]
                ),
                0,
            )
            self.assertEqual(
                dr_mod.main(
                    [
                        "reconcile-g18-slo",
                        "--benchmark-json",
                        str(ci_bench_file),
                        "--owner-approval-json",
                        str(approval_file),
                    ]
                ),
                2,
            )

            # 5. Missing measurements (số đo thiếu) MUST report FAIL_MISSING_MEASUREMENTS (never default to 0.0)
            # 5a. Missing one leg of paginated drill-down in 7-metric precheck / §3.2A
            miss_drill = json.loads(json.dumps(vps_bench_doc))
            del miss_drill["s1_http_acceptance_and_mixed_waves"]["bod_overview_and_drilldown_http"][
                "kpi_drilldown_monthly_company_role_api"
            ]["p95_ms"]
            res_miss_drill = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                miss_drill, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(res_miss_drill["status"], "FAIL_MISSING_MEASUREMENTS")
            self.assertIn(
                "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http.kpi_drilldown_monthly_company_role_api.p95_ms",
                res_miss_drill["missing_measurements"],
            )
            self.assertEqual(
                res_miss_drill["metric_comparisons"]["kpi_drilldown_paginated_api_p95_ms"]["evaluation_status"],
                "MISSING_MEASUREMENT",
            )

            # 5b. Missing §3.2A 40-session SSR case metric even on unsigned precheck
            miss_ssr = json.loads(json.dumps(ci_bench_doc))
            miss_ssr["worklist_ssr_acceptance_and_separation"]["case_2c_40_staff_sessions_c40"]["p95_ms"] = None
            res_miss_ssr = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(miss_ssr)
            self.assertEqual(res_miss_ssr["status"], "FAIL_MISSING_MEASUREMENTS")
            self.assertIn(
                "worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c40.p95_ms",
                res_miss_ssr["missing_measurements"],
            )

            # 5c. Missing §3.2B sub-workload p95 or boolean spoofing numeric metric
            miss_32b = json.loads(json.dumps(vps_bench_doc))
            miss_32b["s1_http_acceptance_and_mixed_waves"]["concurrent_mixed_session_waves_20_40_80"][
                "sessions_40"
            ]["by_workload"]["record_attempt_write_api"]["p95_ms"] = True
            res_miss_32b = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                miss_32b, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(res_miss_32b["status"], "FAIL_MISSING_MEASUREMENTS")
            self.assertIn(
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.record_attempt_write_api.p95_ms",
                res_miss_32b["missing_measurements"],
            )

            # 5d. Missing §3.2C CSV batch / domain workload measurement
            miss_32c = json.loads(json.dumps(vps_bench_doc))
            del miss_32c["s1_domain_and_database_benchmark"]["async_import_distribution_and_queue_lag"][
                "csv_import_50000_rows"
            ]["preview_ms"]
            del miss_32c["s1_domain_and_database_benchmark"]["domain_workloads_s1"][
                "my_customers_list_work_items"
            ]["p95_ms"]
            res_miss_32c = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                miss_32c, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(res_miss_32c["status"], "FAIL_MISSING_MEASUREMENTS")
            self.assertIn(
                "s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.csv_import_50000_rows.preview_ms",
                res_miss_32c["missing_measurements"],
            )
            self.assertIn(
                "s1_domain_and_database_benchmark.domain_workloads_s1.my_customers_list_work_items.p95_ms",
                res_miss_32c["missing_measurements"],
            )

            # 6. Wrong thresholds / SHA-256 mismatch (ngưỡng sai)
            # 6a. Tampered threshold specification dict or file
            bad_spec = json.loads(json.dumps(canon_spec))
            bad_spec["section_3_2A_cases_and_session_models"]["worklist_ssr_case_2c_40_staff_sessions_c40"][
                "max_p95_ms"
            ] = 9500.0
            bad_spec_file = td_path / "tampered-thresholds.json"
            bad_spec_file.write_bytes(dr_mod.serialize_g18_thresholds_spec(bad_spec))
            res_bad_spec = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_bench_doc,
                owner_slo_approval=valid_approval,
                vps_receipts=vps_receipt_dir,
                thresholds_path=bad_spec_file,
            )
            self.assertEqual(res_bad_spec["status"], "FAIL_INVALID_THRESHOLDS")
            self.assertTrue(
                any("worklist_ssr_case_2c_40_staff_sessions_c40.max_p95_ms" in e for e in res_bad_spec["threshold_validation_errors"])
            )

            # 6b. Missing or wrong thresholds_sha256 in Owner approval
            no_sha_approval = {k: v for k, v in valid_approval.items() if k != "thresholds_sha256"}
            res_no_sha = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_bench_doc, owner_slo_approval=no_sha_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(res_no_sha["status"], "FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256")
            self.assertIn(
                "missing_thresholds_sha256_in_owner_approval",
                res_no_sha["owner_approval_validation_errors"],
            )

            wrong_sha_approval = dict(valid_approval, thresholds_sha256="0" * 64)
            res_wrong_sha = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_bench_doc, owner_slo_approval=wrong_sha_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(res_wrong_sha["status"], "FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256")

            # 6c. Wrong ticket version or tampered approved_thresholds value in Owner approval
            wrong_ver = dict(valid_approval, ticket_version="v1.3")
            self.assertEqual(
                dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                    vps_bench_doc, owner_slo_approval=wrong_ver, vps_receipts=vps_receipt_dir
                )["status"],
                "FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256",
            )
            tampered_thresh = dict(
                valid_approval,
                approved_thresholds=dict(
                    valid_approval["approved_thresholds"], my_customers_worklist_api_p95_ms=550.0
                ),
            )
            self.assertEqual(
                dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                    vps_bench_doc, owner_slo_approval=tampered_thresh, vps_receipts=vps_receipt_dir
                )["status"],
                "FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256",
            )

            # 7. Digest mismatch & threshold breach checks
            wrong_dig_bench = json.loads(json.dumps(vps_bench_doc))
            wrong_dig_bench["app_image_tested"] = "ghcr.io/nobita6986/crm-cskh@sha256:" + ("a" * 64)
            self.assertEqual(
                dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                    wrong_dig_bench, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
                )["status"],
                "FAIL_DIGEST_MISMATCH",
            )

            breached_bench = json.loads(json.dumps(vps_bench_doc))
            breached_bench["s1_http_acceptance_and_mixed_waves"]["record_attempt_and_callback_http"][
                "p95_ms"
            ] = 1250.5
            breached_bench["p4_9_2_acceptance_matrix"]["items"][
                "record_attempt_and_callback_p95_le_2000ms_zero_dup"
            ]["measured_http_p95_ms"] = 1250.5
            breached_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                breached_bench, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(breached_res["status"], "FAIL_THRESHOLD_BREACH")
            self.assertFalse(
                breached_res["metric_comparisons"]["contact_attempt_write_p95_ms"]["within_v1_4_threshold"]
            )

        # 8. Live preservation zero-delta check & CLI options remain independent for SSH preflight + G03/G05
        snap_before = {
            "live_project_name": "crm-cskh-vps-staging",
            "containers": [
                {
                    "id": "cid-1",
                    "name": "crm-cskh-vps-staging-backend-1",
                    "service": "backend",
                    "image": "sha256:acb95057",
                    "config_image": "crm-cskh:0.0.1-4672041",
                    "status": "running",
                    "started_at": "2026-11-09T12:00:00Z",
                    "restart_count": 0,
                }
            ],
        }
        same_check = dr_mod.compare_live_stack_preservation(snap_before, json.loads(json.dumps(snap_before)))
        self.assertEqual(same_check["status"], "PASS")
        self.assertTrue(same_check["zero_live_mutation"])

        mutated_after = json.loads(json.dumps(snap_before))
        mutated_after["containers"][0]["restart_count"] = 1
        mutated_check = dr_mod.compare_live_stack_preservation(snap_before, mutated_after)
        self.assertEqual(mutated_check["status"], "FAIL")
        self.assertFalse(mutated_check["zero_live_mutation"])

        p_pre = dr_mod.build_parser().parse_args(["readonly-vps-preflight", "--out", "/tmp/pre.json"])
        self.assertEqual(p_pre.live_project, "crm-cskh-vps-staging")
        p_col = dr_mod.build_parser().parse_args(["collect-vps-g03-g05", "--out-dir", "/tmp/receipts"])
        self.assertEqual(p_col.app_image, dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4)
        p_slo = dr_mod.build_parser().parse_args(
            [
                "reconcile-g18-slo",
                "--benchmark-json",
                "/tmp/bench.json",
                "--vps-receipt-dir",
                "/tmp/receipts",
                "--thresholds-json",
                str(dr_mod.DEFAULT_G18_THRESHOLDS_V1_4_PATH),
            ]
        )
        self.assertEqual(p_slo.expected_digest, dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4)

    def test_staging_scope_key_guard_resource_caps_inventory_and_digest_refresh(self) -> None:
        import base64
        import hashlib
        import subprocess
        from types import SimpleNamespace
        import manage_isolated_staging_dr as dr_mod

        repo_root = staging_mod.REPO_ROOT

        # 1. Exact 15 T1C-signed PASS gates preserved + 9 BLOCKED gates = 24 gates
        gate_map = json.loads(
            (repo_root / "deploy" / "ops" / "evidence" / "p4-https-delta-20261109" / "gate-acceptance-evidence-map.json").read_text(
                encoding="utf-8"
            )
        )
        expected_15_signed_pass = {
            "G01",
            "G02",
            "G06",
            "G08",
            "G09",
            "G10",
            "G11",
            "G12",
            "G13",
            "G14",
            "G15",
            "G16",
            "G17",
            "G20",
            "G21",
        }
        expected_1_fail = {"G04"}
        expected_8_blocked = {"G03", "G05", "G07", "G18", "G19", "G22", "G23", "G24"}
        actual_pass = {g["gate"] for g in gate_map["gates"] if g["status"] == "PASS"}
        actual_fail = {g["gate"] for g in gate_map["gates"] if g["status"] == "FAIL"}
        actual_blocked = {g["gate"] for g in gate_map["gates"] if g["status"] == "BLOCKED"}
        self.assertEqual(actual_pass, expected_15_signed_pass)
        self.assertEqual(actual_fail, expected_1_fail)
        self.assertEqual(actual_blocked, expected_8_blocked)
        self.assertEqual(len(gate_map["gates"]), 24)
        self.assertEqual(gate_map.get("failed_gates_count"), 1)
        self.assertEqual(gate_map.get("blocked_gates_count"), 8)

        # 2. Staging candidate scope vs production_candidate BLOCKED on G04 + rehearsal key ban
        stg_scope = dr_mod.verify_staging_candidate_scope_and_key_guard(
            app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
            requested_scope=dr_mod.STAGING_CANDIDATE_SCOPE,
        )
        self.assertEqual(stg_scope["verification_scope"], "staging_candidate_isolated_verification")
        self.assertTrue(stg_scope["candidate_verification_only"])
        self.assertFalse(stg_scope["promoted_to_production"])
        self.assertEqual(stg_scope["g04_status"], "FAIL")
        self.assertEqual(stg_scope["production_candidate_status"], "BLOCKED")
        self.assertFalse(stg_scope["rehearsal_key_allowed_for_production"])

        with self.assertRaisesRegex(ValueError, "production_candidate remains BLOCKED"):
            dr_mod.verify_staging_candidate_scope_and_key_guard(
                app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                requested_scope=dr_mod.PRODUCTION_CANDIDATE_SCOPE,
            )

        with self.assertRaisesRegex(ValueError, "ci_ephemeral_rehearsal"):
            dr_mod.verify_staging_candidate_scope_and_key_guard(
                app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                requested_scope=dr_mod.CI_REHEARSAL_SCOPE,
            )

        ci_attestation = json.loads(
            (repo_root / "deploy" / "ops" / "evidence" / "p4-candidate-and-s1-20261109" / "ghcr-digest-promotion-attestation.json").read_text(
                encoding="utf-8"
            )
        )
        with self.assertRaisesRegex(ValueError, "ci_ephemeral_rehearsal"):
            dr_mod.verify_staging_candidate_scope_and_key_guard(
                app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                requested_scope=dr_mod.PRODUCTION_CANDIDATE_SCOPE,
                promotion_manifest=ci_attestation,
                g04_override_passed=True,
            )

        spoofed_attestation = dict(
            ci_attestation,
            environment_scope="production_candidate",
            release_id="prod-release-001",
        )
        with self.assertRaisesRegex(ValueError, "ephemeral rehearsal key"):
            dr_mod.verify_staging_candidate_scope_and_key_guard(
                app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                requested_scope=dr_mod.PRODUCTION_CANDIDATE_SCOPE,
                promotion_manifest=spoofed_attestation,
                g04_override_passed=True,
            )

        with self.assertRaisesRegex(ValueError, "rehearsal/ephemeral signing key"):
            dr_mod.verify_staging_candidate_scope_and_key_guard(
                app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                requested_scope=dr_mod.PRODUCTION_CANDIDATE_SCOPE,
                promotion_public_key_path=Path("/tmp/ci-ephemeral-rehearsal.pub"),
                g04_override_passed=True,
            )

        fake_der = b"\x30\x2a\x30\x05\x06\x03\x2b\x65\x70\x03\x21\x00" + (b"\x42" * 32)
        fake_fp = hashlib.sha256(fake_der).hexdigest()
        fake_pem = (
            b"-----BEGIN PUBLIC KEY-----\n"
            + base64.b64encode(fake_der)
            + b"\n-----END PUBLIC KEY-----\n"
        )
        with mock.patch.object(
            dr_mod,
            "KNOWN_REHEARSAL_PUBLIC_KEY_FINGERPRINTS",
            frozenset({*dr_mod.KNOWN_REHEARSAL_PUBLIC_KEY_FINGERPRINTS, fake_fp}),
        ):
            with self.assertRaisesRegex(ValueError, "ephemeral CI rehearsal public key"):
                dr_mod.verify_staging_candidate_scope_and_key_guard(
                    app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                    requested_scope=dr_mod.PRODUCTION_CANDIDATE_SCOPE,
                    promotion_public_key_pem=fake_pem,
                    g04_override_passed=True,
                )

        # 3. 10-daemon resource limits, headroom timeline, inventory/config audit, and digest refresh
        svc_caps = {
            "proxy": (256, 0.5),
            "frontend": (256, 0.5),
            "backend": (1024, 1.0),
            "websocket": (384, 0.5),
            "queue-short": (768, 0.75),
            "queue-long": (1024, 1.0),
            "scheduler": (384, 0.25),
            "db": (1024, 1.0),
            "redis-cache": (256, 0.25),
            "redis-queue": (320, 0.25),
        }
        unbounded_service_override: list[str] = []
        teardown_calls: list[list[str]] = []
        active_digest_box = [dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4]

        live_snap = {
            "timestamp_utc": "2026-11-11T14:00:00Z",
            "live_project_name": "crm-cskh-vps-staging",
            "container_count": 1,
            "all_running": True,
            "containers": [
                {
                    "id": "cid-live-1",
                    "name": "crm-cskh-vps-staging-backend-1",
                    "service": "backend",
                    "image": "sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9",
                    "config_image": "crm-cskh:0.0.1-4672041",
                    "status": "running",
                    "health": "healthy",
                    "started_at": "2026-11-09T12:00:00Z",
                    "restart_count": 0,
                }
            ],
        }

        def fake_run_sub(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            proj = "crm-cskh-dr-https-vps"
            if "--env-file" in cmd:
                ef = Path(cmd[cmd.index("--env-file") + 1])
                if ef.is_file():
                    env_d = staging_mod.parse_env_file(ef)
                    proj = env_d.get("COMPOSE_PROJECT_NAME", proj)
                    active_digest_box[0] = env_d.get("APP_IMAGE", active_digest_box[0])
            cur_img = active_digest_box[0]

            if cmd[:2] == ["docker", "pull"]:
                active_digest_box[0] = cmd[2]
                cur_img = cmd[2]
                dig = cur_img.split("@", 1)[1]
                return subprocess.CompletedProcess(cmd, 0, stdout=f"Digest: {dig}\nStatus: Downloaded\n", stderr="")
            if cmd[:3] == ["docker", "image", "inspect"]:
                target_img = cmd[3]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Id": "sha256:4d43b76850b8caa34f96a4139b31e8bd17815706bf93edcb8a0dd18cec25e8df",
                                "RepoDigests": [target_img],
                                "Created": "2026-11-11T08:00:00Z",
                                "Architecture": "amd64",
                                "Os": "linux",
                                "Size": 1190528512,
                                "Config": {
                                    "User": "frappe",
                                    "Labels": {
                                        "io.cskh.apps_tree_sha": "4d361e68912f52243751ae5986cabbd13033f050",
                                        "io.cskh.crm_cskh_tree_sha": "a8737dbcce74f9095a6e76c5e93491e92843f3d0",
                                    },
                                },
                            }
                        ]
                    ),
                    stderr="",
                )
            if cmd[:2] == ["docker", "compose"]:
                if "down" in cmd and "--volumes" in cmd:
                    teardown_calls.append(list(cmd))
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if "config" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=f"name: {proj}\n", stderr="")
                if "ps" in cmd and "--format" in cmd:
                    rows = []
                    for svc in dr_mod.ALL_DAEMON_SERVICES:
                        pubs = (
                            [
                                {"URL": "127.0.0.1", "PublishedPort": 18081, "TargetPort": 80, "Protocol": "tcp"},
                                {"URL": "127.0.0.1", "PublishedPort": 18443, "TargetPort": 443, "Protocol": "tcp"},
                            ]
                            if svc == "proxy"
                            else []
                        )
                        rows.append(
                            {
                                "ID": f"cid-{svc}",
                                "Name": f"{proj}-{svc}-1",
                                "Service": svc,
                                "State": "running",
                                "Publishers": pubs,
                            }
                        )
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout="\n".join(json.dumps(r) for r in rows) + "\n", stderr=""
                    )
                if "ps" in cmd and "-q" in cmd:
                    q_idx = cmd.index("-q")
                    svcs = cmd[q_idx + 1 :]
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout="\n".join(f"cid-{s}" for s in svcs) + "\n", stderr=""
                    )
                if "exec" in cmd and "MOUNTED_OK" in cmd[-1]:
                    return subprocess.CompletedProcess(cmd, 0, stdout="MOUNTED_OK\n", stderr="")
            if cmd[:3] == ["docker", "network", "ls"]:
                nets = f"{proj}_app_net\n{proj}_data_net\n{proj}_edge_net\n"
                return subprocess.CompletedProcess(cmd, 0, stdout=nets, stderr="")
            if cmd[:3] == ["docker", "network", "inspect"]:
                nname = cmd[3]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Driver": "bridge",
                                "Internal": nname.endswith(("_app_net", "_data_net")),
                                "Containers": {"c1": {"Name": f"{proj}-backend-1"}},
                            }
                        ]
                    ),
                    stderr="",
                )
            if cmd[:3] == ["docker", "volume", "ls"]:
                vols = "\n".join(
                    f"{proj}{sfx}" for sfx in ("_cert_data", "_db_data", "_logs", "_redis_queue_data", "_sites")
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=vols + "\n", stderr="")
            if cmd[:2] == ["docker", "inspect"] and "--format" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(["MARIADB_ROOT_PASSWORD_FILE=/run/secrets/db_password"]), stderr="")
            if cmd[:2] == ["docker", "inspect"]:
                cids = cmd[2:]
                items = []
                for cid in cids:
                    svc = cid.removeprefix("cid-")
                    mib, cpus = svc_caps[svc]
                    mem_b = 0 if svc in unbounded_service_override else int(mib * 1024 * 1024)
                    nano_c = int(cpus * 1_000_000_000)
                    items.append(
                        {
                            "Name": f"/{proj}-{svc}-1",
                            "Config": {"Labels": {"com.docker.compose.service": svc}},
                            "HostConfig": {"Memory": mem_b, "NanoCpus": nano_c},
                        }
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(items), stderr="")
            raise AssertionError(f"Unexpected mocked command: {cmd}")

        def fake_verify_digest(_containers: object, expected_image: str) -> dict[str, object]:
            return {
                "pass": True,
                "expected_app_image": expected_image,
                "missing_services": [],
                "mismatches": [],
                "inspected_services": {
                    svc: {
                        "container": f"crm-cskh-dr-https-vps-{svc}-1",
                        "image_id": "sha256:4d43b76850b8caa34f96a4139b31e8bd17815706bf93edcb8a0dd18cec25e8df",
                        "repo_digests": [expected_image],
                        "matched": True,
                    }
                    for svc in staging_mod.APP_SERVICES
                },
            }

        def fake_bootstrap(*, env_file: Path, **_kwargs: object) -> dict[str, object]:
            ed = staging_mod.parse_env_file(env_file)
            img = ed["APP_IMAGE"]
            return {
                "https_smoke": {
                    "status": "PASS",
                    "mode": "dr-https",
                    "app_image": img,
                    "checks": {
                        "container_digest_parity": fake_verify_digest([], img),
                        "http_to_https_redirect": {"pass": True},
                        "tls_certificate": {"expiry_ok": True},
                        "login_and_sid_cookie": {"pass": True},
                        "vi_ui_and_csrf": {"pass": True},
                        "wss_websocket": {"pass": True, "switched_protocols_101": True},
                    },
                }
            }

        with tempfile.TemporaryDirectory(prefix="t1a-vps-receipt-test-") as td:
            out_dir = Path(td) / "vps-receipts"
            state_dir = Path(td) / "vps-state"
            with (
                mock.patch.object(dr_mod, "run_sub", side_effect=fake_run_sub),
                mock.patch.object(dr_mod.p403_tool, "verify_running_containers_digest", side_effect=fake_verify_digest),
                mock.patch.object(dr_mod, "bootstrap_isolated_dr_stack", side_effect=fake_bootstrap),
                mock.patch.object(dr_mod, "capture_readonly_live_stack_snapshot", return_value=json.loads(json.dumps(live_snap))),
                mock.patch.object(dr_mod, "_check_loopback_port_free", return_value=True),
                mock.patch.object(dr_mod.os, "cpu_count", return_value=4),
                mock.patch.object(
                    dr_mod,
                    "_read_linux_meminfo_mib",
                    return_value={
                        "mem_total_mib": 15984.0,
                        "mem_available_mib": 9400.0,
                        "swap_total_mib": 2048.0,
                        "swap_free_mib": 2048.0,
                    },
                ),
                mock.patch.object(
                    dr_mod,
                    "_read_linux_loadavg",
                    return_value={"loadavg_1m": 0.42, "loadavg_5m": 0.38, "loadavg_15m": 0.31},
                ),
                mock.patch.object(
                    dr_mod.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(
                        total=int(120 * (1024**3)),
                        used=int(50 * (1024**3)),
                        free=int(70 * (1024**3)),
                    ),
                ),
                mock.patch.object(dr_mod.socket, "gethostname", return_value="vps-hrpartner-103-77-208-186"),
                mock.patch.object(dr_mod.platform, "platform", return_value="Linux-6.8.0-51-generic-x86_64-with-glibc2.39"),
                mock.patch.object(dr_mod.platform, "release", return_value="6.8.0-51-generic"),
            ):
                bundle_sum = dr_mod.collect_vps_g03_g05_receipts(
                    app_image=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
                    out_dir=out_dir,
                    keep_up=True,
                    state_dir=state_dir,
                )
                self.assertEqual(bundle_sum["status"], "PASS")
                self.assertTrue((out_dir / "staging-inventory-and-config-audit.json").is_file())
                inv_audit = json.loads((out_dir / "staging-inventory-and-config-audit.json").read_text(encoding="utf-8"))
                self.assertEqual(inv_audit["status"], "PASS")
                self.assertEqual(inv_audit["governance_scope"]["production_candidate_status"], "BLOCKED")
                self.assertTrue(inv_audit["resource_limits"]["pass"])
                self.assertEqual(inv_audit["resource_limits"]["total_capped_ram_mib"], 5696.0)
                self.assertEqual(inv_audit["resource_limits"]["total_capped_cpus"], 6.0)
                self.assertTrue(inv_audit["zero_live_mutation_during_and_after"])
                self.assertIn("before_isolated_staging", inv_audit["headroom_timeline"])
                self.assertIn("during_isolated_staging", inv_audit["headroom_timeline"])
                self.assertIn("after_isolated_staging", inv_audit["headroom_timeline"])

                # Unbounded daemon container must fail runtime resource audit
                unbounded_service_override.append("backend")
                fail_audit = dr_mod.audit_isolated_stack_runtime_resources(
                    env_file=state_dir / "staging-dr-https.env",
                    secret_file=state_dir / "db_root_password.txt",
                )
                self.assertEqual(fail_audit["status"], "FAIL")
                self.assertEqual(fail_audit["resource_limits"]["unbounded_services"], ["backend"])
                unbounded_service_override.clear()

                # When T1B changes candidate image digest to fix G18, refresh-candidate-digest updates Staging & all receipts
                new_t1b_digest = "ghcr.io/nobita6986/crm-cskh@sha256:" + ("c" * 64)
                rc = dr_mod.main(
                    [
                        "refresh-candidate-digest",
                        "--app-image",
                        new_t1b_digest,
                        "--out-dir",
                        str(out_dir),
                        "--state-dir",
                        str(state_dir),
                        "--keep-up",
                    ]
                )
                self.assertEqual(rc, 0)
                self.assertGreaterEqual(len(teardown_calls), 1)
                refreshed_sum = json.loads((out_dir / "vps-g03-g05-summary.json").read_text(encoding="utf-8"))
                self.assertEqual(refreshed_sum["app_image"], new_t1b_digest)
                self.assertEqual(refreshed_sum["refreshed_from_previous_digest"], dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4)
                self.assertEqual(refreshed_sum["stale_digest_references"], [])
                for rfile in (
                    "candidate-registry-pull-inspect-receipt.json",
                    "staging-dr-runtime-isolation-audit.json",
                    "staging-inventory-and-config-audit.json",
                    "https-wss-smoke-receipt.json",
                ):
                    raw_r = (out_dir / rfile).read_text(encoding="utf-8")
                    self.assertIn(new_t1b_digest, raw_r)
                    self.assertNotIn(dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4, raw_r)

    def test_g18_v1_4_ticket_3_2_abc_divergent_thresholds_and_p99_checks(self) -> None:
        import manage_isolated_staging_dr as dr_mod

        repo_root = staging_mod.REPO_ROOT
        ci_bench_doc = json.loads(
            (
                repo_root
                / "deploy"
                / "ops"
                / "evidence"
                / "p4-candidate-and-s1-20261109"
                / "benchmark-s1-report.json"
            ).read_text(encoding="utf-8")
        )

        # 1. Verify new SHA-256 lock and reject Owner approvals bound to old 141f52e SHA-256
        expected_new_sha = "e9fde1a0e796a4f30651689c02384fb07051ab6adcaf368e5c336122f4ec54e7"
        old_141f52e_sha = "2a3ff3642af97b98d4102ec3bcb138655a58ce21860132d056c7ff0f33c2dc19"
        self.assertEqual(dr_mod.LOCKED_G18_THRESHOLDS_V1_4_SHA256, expected_new_sha)

        valid_approval = {
            "approved": True,
            "ticket_version": "v1.4",
            "signed_by": "owner-representative@hrpartner.vn",
            "signed_at": "2026-11-11T21:40:00+07:00",
            "thresholds_sha256": expected_new_sha,
            "approved_thresholds": {
                k: spec["threshold"] for k, spec in dr_mod.OWNER_TICKET_V1_4_PROPOSED_SLOS.items()
            },
        }
        stale_approval = dict(valid_approval, thresholds_sha256=old_141f52e_sha)

        def set_path(doc: dict, dotted: str, val: object) -> None:
            parts = dotted.split(".")
            cur: dict = doc
            for part in parts[:-1]:
                cur = cur[part]
            cur[parts[-1]] = val

        def del_path(doc: dict, dotted: str) -> None:
            parts = dotted.split(".")
            cur: dict = doc
            for part in parts[:-1]:
                cur = cur[part]
            del cur[parts[-1]]

        with tempfile.TemporaryDirectory(prefix="g18-gap-p99-test-") as td:
            td_path = Path(td)
            vps_receipt_dir = self._build_mock_staging_vps_bundle(
                repo_root,
                td_path / "vps-receipts",
                expected_digest=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
            )
            vps_base = json.loads(json.dumps(ci_bench_doc))
            vps_base["s1_domain_and_database_benchmark"]["runner_hardware"]["platform"] = (
                "Linux-6.8.0-51-generic-x86_64-with-glibc2.39"
            )
            vps_base["isolation_guard"]["compose_project"] = "crm-cskh-s1-bench-vps"

            # Stale SHA-256 from 141f52e must be rejected
            stale_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_base, owner_slo_approval=stale_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(stale_res["status"], "FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256")
            self.assertTrue(
                any("owner_approval_thresholds_sha256_mismatch" in e for e in stale_res["owner_approval_validation_errors"])
            )

            # Baseline passes cleanly
            base_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                vps_base, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(base_res["status"], "PASS")

            # 2. Measurements that EXCEED Ticket §3.2A-C SLOs while STILL FALLING INSIDE old 141f52e JSON thresholds
            gap_breach_cases: list[tuple[str, list[tuple[str, object]], str]] = [
                # §3.2A SSR-01A: Ticket <= 6500ms vs old JSON <= 8000ms (p95=7000, p99=7200 <= 7500)
                (
                    "SSR-01A_c40_40_sessions",
                    [
                        ("worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c40.p95_ms", 7000.0),
                        ("worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c40.p99_ms", 7200.0),
                        ("s1_http_acceptance_and_mixed_waves.telesale_worklist_ssr_40_staff_sessions_c40.p95_ms", 7000.0),
                        ("s1_http_acceptance_and_mixed_waves.telesale_worklist_ssr_40_staff_sessions_c40.p99_ms", 7200.0),
                        ("worklist_ssr_acceptance_and_separation.priority_acceptance_case_2_40_staff_sessions_c40.p95_ms", 7000.0),
                        ("worklist_ssr_acceptance_and_separation.priority_acceptance_case_2_40_staff_sessions_c40.p99_ms", 7200.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_ssr_telesale_worklist_page_40_staff_sessions_only.p95_ms", 7000.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_ssr_telesale_worklist_page_40_staff_sessions_only.p99_ms", 7200.0),
                    ],
                    "section_3_2A.worklist_ssr_case_2c_40_staff_sessions_c40.p95_ms:7000.0>6500.0",
                ),
                # §3.2A SSR-01B: Ticket <= 1800ms vs old JSON <= 3000ms (p95=2000, p99=2100 <= 2200)
                (
                    "SSR-01B_c12_40_sessions",
                    [
                        ("worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c12.p95_ms", 2000.0),
                        ("worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c12.p99_ms", 2100.0),
                        ("s1_http_acceptance_and_mixed_waves.telesale_worklist_ssr_40_staff_sessions_c12.p95_ms", 2000.0),
                        ("s1_http_acceptance_and_mixed_waves.telesale_worklist_ssr_40_staff_sessions_c12.p99_ms", 2100.0),
                    ],
                    "section_3_2A.worklist_ssr_case_2c_40_staff_sessions_c12.p95_ms:2000.0>1800.0",
                ),
                # §3.2A SSR-01C 2a c=12: Ticket <= 1500ms vs old JSON <= 3000ms (p95=1750, p99=1900 <= 2000)
                (
                    "SSR-01C_2a_c12_shared_new",
                    [
                        ("worklist_ssr_acceptance_and_separation.case_2a_shared_new_session_c12.p95_ms", 1750.0),
                        ("worklist_ssr_acceptance_and_separation.case_2a_shared_new_session_c12.p99_ms", 1900.0),
                        ("scenario_summaries.telesale_worklist_page.p95_ms", 1750.0),
                        ("scenario_summaries.telesale_worklist_page.p99_ms", 1900.0),
                        ("worklist_ssr_acceptance_and_separation.priority_acceptance_case_1_shared_new_session_c12.p95_ms", 1750.0),
                        ("worklist_ssr_acceptance_and_separation.priority_acceptance_case_1_shared_new_session_c12.p99_ms", 1900.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c12_ssr_telesale_worklist_page_only.p95_ms", 1750.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c12_ssr_telesale_worklist_page_only.p99_ms", 1900.0),
                    ],
                    "section_3_2A.worklist_ssr_case_2a_shared_new_session_c12.p95_ms:1750.0>1500.0",
                ),
                # §3.2A SSR-01C 2b c=12: Ticket <= 1500ms vs old JSON <= 3000ms
                (
                    "SSR-01C_2b_c12_single_init",
                    [
                        ("worklist_ssr_acceptance_and_separation.case_2b_single_initialized_session_c12.p95_ms", 1750.0),
                        ("worklist_ssr_acceptance_and_separation.case_2b_single_initialized_session_c12.p99_ms", 1900.0),
                    ],
                    "section_3_2A.worklist_ssr_case_2b_single_initialized_session_c12.p95_ms:1750.0>1500.0",
                ),
                # §3.2A SSR-01C 2b c=40: Ticket <= 3000ms vs old JSON <= 4500ms (p95=3250, p99=3400 <= 3500)
                (
                    "SSR-01C_2b_c40_single_init",
                    [
                        ("worklist_ssr_acceptance_and_separation.case_2b_single_initialized_session_c40.p95_ms", 3250.0),
                        ("worklist_ssr_acceptance_and_separation.case_2b_single_initialized_session_c40.p99_ms", 3400.0),
                        ("peak_40_staff_concurrent_wave.scenario_summaries.telesale_worklist_page.p95_ms", 3250.0),
                        ("peak_40_staff_concurrent_wave.scenario_summaries.telesale_worklist_page.p99_ms", 3400.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_ssr_telesale_worklist_page_single_session_only.p95_ms", 3250.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_ssr_telesale_worklist_page_single_session_only.p99_ms", 3400.0),
                    ],
                    "section_3_2A.worklist_ssr_case_2b_single_initialized_session_c40.p95_ms:3250.0>3000.0",
                ),
                # §3.2A SSR-02 campaigns & quality SSR: Ticket <= 1500ms vs old JSON <= 3000ms
                (
                    "SSR-02_campaigns_ssr_c12",
                    [
                        ("scenario_summaries.campaigns_distribution_view.p95_ms", 1750.0),
                        ("scenario_summaries.campaigns_distribution_view.p99_ms", 1900.0),
                    ],
                    "section_3_2A.campaigns_distribution_ssr_c12_shared.p95_ms:1750.0>1500.0",
                ),
                (
                    "SSR-02_quality_ssr_c12",
                    [
                        ("scenario_summaries.quality_overview_drilldown.p95_ms", 1750.0),
                        ("scenario_summaries.quality_overview_drilldown.p99_ms", 1900.0),
                    ],
                    "section_3_2A.quality_overview_ssr_c12_shared.p95_ms:1750.0>1500.0",
                ),
                # §3.2A API-01 c=40: Ticket <= 1000ms vs old JSON <= 1500ms
                (
                    "API-01_c40_peak",
                    [
                        ("peak_40_staff_concurrent_wave.scenario_summaries.my_customers_worklist_api.p95_ms", 1150.0),
                        ("peak_40_staff_concurrent_wave.scenario_summaries.my_customers_worklist_api.p99_ms", 1250.0),
                    ],
                    "section_3_2A.my_customers_worklist_api_c40_peak.p95_ms:1150.0>1000.0",
                ),
                # §3.2A API-02 c=12 per-leg: Ticket <= 400ms each leg while combined p95=550 <= 600ms
                (
                    "API-02_c12_each_leg_le_400ms",
                    [
                        ("s1_http_acceptance_and_mixed_waves.lead_detail_and_history_http.get_work_item_p95_ms", 500.0),
                        ("s1_http_acceptance_and_mixed_waves.lead_detail_and_history_http.list_attempts_p95_ms", 480.0),
                        ("s1_http_acceptance_and_mixed_waves.lead_detail_and_history_http.p95_ms", 550.0),
                        ("s1_http_acceptance_and_mixed_waves.lead_detail_and_history_http.p99_ms", 700.0),
                        ("p4_9_2_acceptance_matrix.items.open_lead_and_history_p95_le_2000ms.measured_http_p95_ms", 550.0),
                    ],
                    "section_3_2A.open_lead_and_history_role_40_sessions.get_work_item_p95_ms:500.0>400.0",
                ),
                # §3.2A API-03 c=12: Ticket <= 950ms vs old JSON / 7-metric precheck <= 1200ms
                (
                    "API-03_c12_write_le_950ms",
                    [
                        ("s1_http_acceptance_and_mixed_waves.record_attempt_and_callback_http.p95_ms", 1050.0),
                        ("s1_http_acceptance_and_mixed_waves.record_attempt_and_callback_http.p99_ms", 1150.0),
                        ("p4_9_2_acceptance_matrix.items.record_attempt_and_callback_p95_le_2000ms_zero_dup.measured_http_p95_ms", 1050.0),
                    ],
                    "section_3_2A.record_attempt_and_callback_role_40_sessions.p95_ms:1050.0>950.0",
                ),
                # §3.2A API-04 c=40: Ticket <= 1500ms vs old JSON <= 3000ms
                (
                    "API-04_c40_peak",
                    [
                        ("peak_40_staff_concurrent_wave.scenario_summaries.bod_executive_overview_api.p95_ms", 1750.0),
                        ("peak_40_staff_concurrent_wave.scenario_summaries.bod_executive_overview_api.p99_ms", 1900.0),
                    ],
                    "section_3_2A.bod_executive_overview_api_c40_peak.p95_ms:1750.0>1500.0",
                ),
                # §3.2A API-05 c=40 daily & monthly: Ticket <= 2500ms vs old JSON <= 3000ms
                (
                    "API-05_c40_daily_drilldown",
                    [
                        ("peak_40_staff_concurrent_wave.scenario_summaries.kpi_metric_drilldown_250k_api.p95_ms", 2750.0),
                        ("peak_40_staff_concurrent_wave.scenario_summaries.kpi_metric_drilldown_250k_api.p99_ms", 2900.0),
                    ],
                    "section_3_2A.kpi_drilldown_daily_campaign_api_c40_peak.p95_ms:2750.0>2500.0",
                ),
                (
                    "API-05_c40_monthly_drilldown",
                    [
                        ("peak_40_staff_concurrent_wave.scenario_summaries.kpi_metric_drilldown_monthly_company_api.p95_ms", 2750.0),
                        ("peak_40_staff_concurrent_wave.scenario_summaries.kpi_metric_drilldown_monthly_company_api.p99_ms", 2900.0),
                    ],
                    "section_3_2A.kpi_drilldown_monthly_company_250k_api_c40_peak.p95_ms:2750.0>2500.0",
                ),
                # §3.2A API-06 health_ping: Ticket <= 150ms vs old JSON <= 500ms
                (
                    "API-06_health_ping_c12",
                    [
                        ("scenario_summaries.health_ping.p95_ms", 200.0),
                        ("scenario_summaries.health_ping.p99_ms", 230.0),
                    ],
                    "section_3_2A.health_ping_c12_shared.p95_ms:200.0>150.0",
                ),
                # §3.2B AGG-03 sessions_20 overall & sub-workloads: Ticket <= 800ms (950ms write) vs old JSON <= 1000ms (1200ms write)
                (
                    "AGG-03_sessions_20_overall",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_20.overall_summary.p95_ms", 900.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_20.overall_summary.p99_ms", 1100.0),
                    ],
                    "section_3_2B.sessions_20.overall_summary.p95_ms:900.0>800.0",
                ),
                (
                    "AGG-03_sessions_20_record_attempt_write",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_20.by_workload.record_attempt_write_api.p95_ms", 1050.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_20.by_workload.record_attempt_write_api.p99_ms", 1150.0),
                    ],
                    "section_3_2B.sessions_20.by_workload.record_attempt_write_api.p95_ms:1050.0>950.0",
                ),
                # §3.2B AGG-03 sessions_40 sub-workloads: my_customers<=1000, lead_detail<=1300, write<=1500, bod<=1500 vs old 1500/2000/2000/2000
                (
                    "AGG-03_sessions_40_my_customers",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.my_customers_api.p95_ms", 1150.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.my_customers_api.p99_ms", 1250.0),
                    ],
                    "section_3_2B.sessions_40.by_workload.my_customers_api.p95_ms:1150.0>1000.0",
                ),
                (
                    "AGG-03_sessions_40_lead_detail",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.lead_detail_history_api.p95_ms", 1450.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.lead_detail_history_api.p99_ms", 1550.0),
                    ],
                    "section_3_2B.sessions_40.by_workload.lead_detail_history_api.p95_ms:1450.0>1300.0",
                ),
                (
                    "AGG-03_sessions_40_record_attempt_write",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.record_attempt_write_api.p95_ms", 1750.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.record_attempt_write_api.p99_ms", 1900.0),
                    ],
                    "section_3_2B.sessions_40.by_workload.record_attempt_write_api.p95_ms:1750.0>1500.0",
                ),
                (
                    "AGG-03_sessions_40_bod_overview",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.bod_overview_api.p95_ms", 1750.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.bod_overview_api.p99_ms", 1900.0),
                    ],
                    "section_3_2B.sessions_40.by_workload.bod_overview_api.p95_ms:1750.0>1500.0",
                ),
                # §3.2B AGG-03 sessions_80 overall & sub-workload: Ticket <= 2500ms vs old JSON <= 3000ms
                (
                    "AGG-03_sessions_80_overall",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_80.overall_summary.p95_ms", 2750.0),
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_80.overall_summary.p99_ms", 3000.0),
                    ],
                    "section_3_2B.sessions_80.overall_summary.p95_ms:2750.0>2500.0",
                ),
                # §3.2C AGG-01 & AGG-02 aggregate load summaries (previously unchecked in old JSON)
                (
                    "AGG-01_c12_overall_summary",
                    [
                        ("overall_summary.p95_ms", 1500.0),
                        ("overall_summary.p99_ms", 1700.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c12_overall_summary_all_8_scenarios.p95_ms", 1500.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c12_overall_summary_all_8_scenarios.p99_ms", 1700.0),
                    ],
                    "section_3_2C.aggregate_load_summaries.agg_01_c12_overall_summary.p95_ms:1500.0>1200.0",
                ),
                (
                    "AGG-02_c40_peak_overall_summary",
                    [
                        ("peak_40_staff_concurrent_wave.overall_summary.p95_ms", 2800.0),
                        ("peak_40_staff_concurrent_wave.overall_summary.p99_ms", 3000.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_overall_summary_peak_wave_5_scenarios.p95_ms", 2800.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_overall_summary_peak_wave_5_scenarios.p99_ms", 3000.0),
                    ],
                    "section_3_2C.aggregate_load_summaries.agg_02_c40_peak_overall_summary.p95_ms:2800.0>2500.0",
                ),
                # §3.2C domain_workloads_s1 contact_attempt_and_followup_write: Ticket <= 950ms vs old JSON <= 1200ms
                (
                    "domain_contact_attempt_write_le_950ms",
                    [
                        ("s1_domain_and_database_benchmark.domain_workloads_s1.contact_attempt_and_followup_write.p95_ms", 1050.0),
                        ("s1_domain_and_database_benchmark.domain_workloads_s1.contact_attempt_and_followup_write.p99_ms", 1150.0),
                    ],
                    "section_3_2C.domain_workloads_s1.contact_attempt_and_followup_write.p95_ms:1050.0>950.0",
                ),
                # §3.2C BG-01 distribution: preview <= 10000ms (old 60000ms), drain <= 900000ms (old 1200000ms), enqueue <= 500ms
                (
                    "BG-01_distribution_preview_le_10s",
                    [
                        ("s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.campaign_distribution_10000_work_items_40_staff.preview_ms", 25000.0),
                    ],
                    "section_3_2C.distribution.preview_ms:25000.0>10000.0",
                ),
                (
                    "BG-01_distribution_drain_le_15m",
                    [
                        ("s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.campaign_distribution_10000_work_items_40_staff.execute_and_worker_drain_ms", 1000000.0),
                    ],
                    "section_3_2C.distribution.execute_and_worker_drain_ms:1000000.0>900000.0",
                ),
                (
                    "BG-01_distribution_enqueue_le_500ms",
                    [
                        ("s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.campaign_distribution_10000_work_items_40_staff.enqueue_api_ms", 650.0),
                    ],
                    "section_3_2C.distribution.enqueue_api_ms:650.0>500.0",
                ),
                # §3.2C BG-02 CSV import: preview <= 5000ms (old 15000ms), worker_commit_and_drain <= 300000ms (old 600000ms)
                (
                    "BG-02_csv_preview_le_5s",
                    [
                        ("s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.csv_import_50000_rows.preview_ms", 9000.0),
                    ],
                    "section_3_2C.csv_import_50000_rows.preview_ms:9000.0>5000.0",
                ),
                (
                    "BG-02_csv_worker_drain_le_5m",
                    [
                        ("s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.csv_import_50000_rows.worker_commit_and_drain_ms", 400000.0),
                        ("s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.csv_import_50000_rows.total_preview_confirm_and_worker_drain_ms", 400650.63),
                    ],
                    "section_3_2C.csv_import_50000_rows.worker_commit_and_drain_ms:400000.0>300000.0",
                ),
                # §3.2C BG-03 KPI recompute: daily <= 30000ms (old 300000ms), monthly <= 120000ms (old 180000ms), wall <= 120000ms, UI p95 <= 500ms (old 800ms)
                (
                    "BG-03_kpi_daily_le_30s",
                    [
                        ("s1_domain_and_database_benchmark.kpi_recompute_s1.daily_incremental_ms", 60000.0),
                    ],
                    "section_3_2C.kpi_recompute_s1.daily_incremental_ms:60000.0>30000.0",
                ),
                (
                    "BG-03_kpi_monthly_le_120s",
                    [
                        ("s1_domain_and_database_benchmark.kpi_recompute_s1.monthly_full_75k_wi_250k_att_ms", 150000.0),
                    ],
                    "section_3_2C.kpi_recompute_s1.monthly_full_ms:150000.0>120000.0",
                ),
                (
                    "BG-03_kpi_queue_long_wall_le_120s",
                    [
                        ("s1_domain_and_database_benchmark.kpi_recompute_s1.background_queue_long_recompute.wall_ms", 150000.0),
                    ],
                    "section_3_2C.kpi_recompute_s1.background_queue_long_wall_ms:150000.0>120000.0",
                ),
                (
                    "BG-03_kpi_concurrent_ui_p95_le_500ms",
                    [
                        ("s1_domain_and_database_benchmark.kpi_recompute_s1.background_queue_long_recompute.concurrent_ui_overview_reads.p95_ms", 650.0),
                        ("s1_domain_and_database_benchmark.kpi_recompute_s1.background_queue_long_recompute.concurrent_ui_overview_reads.p99_ms", 800.0),
                        ("p4_9_2_acceptance_matrix.items.recompute_kpi_non_blocking_atomic_publish.concurrent_ui_p95_ms", 650.0),
                    ],
                    "section_3_2C.kpi_recompute_s1.concurrent_ui_overview_p95_ms:650.0>500.0",
                ),
            ]

            for label, mutations, expected_breach in gap_breach_cases:
                with self.subTest(gap_case=label):
                    cand = json.loads(json.dumps(vps_base))
                    for path_str, val in mutations:
                        set_path(cand, path_str, val)
                    res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                        cand, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
                    )
                    self.assertEqual(res["status"], "FAIL_THRESHOLD_BREACH", f"{label}: {res['status']}")
                    self.assertIn(expected_breach, res["breached_checks"], f"{label}: {res['breached_checks']}")
                    if label in ("API-02_c12_each_leg_le_400ms", "API-03_c12_write_le_950ms", "BG-03_kpi_daily_le_30s"):
                        # 7-metric headline precheck still passes, proving full §3.2A-C catch is required
                        self.assertTrue(res["all_7_metrics_within_v1_4_thresholds"], label)

            # Verify sessions_40.by_workload.kpi_drilldown_api corrected from overly strict old 2000ms to Ticket 2500ms/3000ms
            s40_drill_ok = json.loads(json.dumps(vps_base))
            set_path(
                s40_drill_ok,
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.kpi_drilldown_api.p95_ms",
                2250.0,
            )
            set_path(
                s40_drill_ok,
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.kpi_drilldown_api.p99_ms",
                2400.0,
            )
            self.assertEqual(
                dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                    s40_drill_ok, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
                )["status"],
                "PASS",
            )
            s40_drill_fail = json.loads(json.dumps(vps_base))
            set_path(
                s40_drill_fail,
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.kpi_drilldown_api.p95_ms",
                2750.0,
            )
            set_path(
                s40_drill_fail,
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.kpi_drilldown_api.p99_ms",
                2900.0,
            )
            s40_drill_fail_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                s40_drill_fail, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
            )
            self.assertEqual(s40_drill_fail_res["status"], "FAIL_THRESHOLD_BREACH")
            self.assertIn(
                "section_3_2B.sessions_40.by_workload.kpi_drilldown_api.p95_ms:2750.0>2500.0",
                s40_drill_fail_res["breached_checks"],
            )

            # 3. Dedicated p99 breach checks across §3.2A, §3.2B, and §3.2C (when p95 is strictly within max_p95_ms)
            p99_breach_cases: list[tuple[str, list[tuple[str, object]], str]] = [
                (
                    "p99_3_2A_SSR_01A",
                    [
                        ("worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c40.p99_ms", 7800.0),
                        ("s1_http_acceptance_and_mixed_waves.telesale_worklist_ssr_40_staff_sessions_c40.p99_ms", 7800.0),
                        ("worklist_ssr_acceptance_and_separation.priority_acceptance_case_2_40_staff_sessions_c40.p99_ms", 7800.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_ssr_telesale_worklist_page_40_staff_sessions_only.p99_ms", 7800.0),
                    ],
                    "section_3_2A.worklist_ssr_case_2c_40_staff_sessions_c40.p99_ms:7800.0>7500.0",
                ),
                (
                    "p99_3_2A_API_06",
                    [("scenario_summaries.health_ping.p99_ms", 300.0)],
                    "section_3_2A.health_ping_c12_shared.p99_ms:300.0>250.0",
                ),
                (
                    "p99_3_2A_non_monotonic_p99_lt_p95",
                    [
                        ("scenario_summaries.my_customers_worklist_api.p95_ms", 400.0),
                        ("scenario_summaries.my_customers_worklist_api.p99_ms", 350.0),
                    ],
                    "section_3_2A.my_customers_worklist_api_c12_shared.p99_lt_p95:350.0<400.0",
                ),
                (
                    "p99_3_2B_sessions_20_overall",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_20.overall_summary.p99_ms", 1350.0),
                    ],
                    "section_3_2B.sessions_20.overall_summary.p99_ms:1350.0>1200.0",
                ),
                (
                    "p99_3_2B_sessions_40_lead_detail",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.lead_detail_history_api.p99_ms", 1750.0),
                    ],
                    "section_3_2B.sessions_40.by_workload.lead_detail_history_api.p99_ms:1750.0>1600.0",
                ),
                (
                    "p99_3_2B_sessions_80_overall",
                    [
                        ("s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_80.overall_summary.p99_ms", 3500.0),
                    ],
                    "section_3_2B.sessions_80.overall_summary.p99_ms:3500.0>3200.0",
                ),
                (
                    "p99_3_2C_AGG_01",
                    [
                        ("overall_summary.p99_ms", 1950.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c12_overall_summary_all_8_scenarios.p99_ms", 1950.0),
                    ],
                    "section_3_2C.aggregate_load_summaries.agg_01_c12_overall_summary.p99_ms:1950.0>1800.0",
                ),
                (
                    "p99_3_2C_AGG_02",
                    [
                        ("peak_40_staff_concurrent_wave.overall_summary.p99_ms", 3400.0),
                        ("worklist_ssr_acceptance_and_separation.ssr_vs_aggregate_separation.c40_overall_summary_peak_wave_5_scenarios.p99_ms", 3400.0),
                    ],
                    "section_3_2C.aggregate_load_summaries.agg_02_c40_peak_overall_summary.p99_ms:3400.0>3200.0",
                ),
                (
                    "p99_3_2C_domain_contact_attempt_write",
                    [
                        ("s1_domain_and_database_benchmark.domain_workloads_s1.contact_attempt_and_followup_write.p99_ms", 1350.0),
                    ],
                    "section_3_2C.domain_workloads_s1.contact_attempt_and_followup_write.p99_ms:1350.0>1200.0",
                ),
                (
                    "p99_3_2C_BG_03_concurrent_ui_p99",
                    [
                        ("s1_domain_and_database_benchmark.kpi_recompute_s1.background_queue_long_recompute.concurrent_ui_overview_reads.p99_ms", 1150.0),
                    ],
                    "section_3_2C.kpi_recompute_s1.concurrent_ui_overview_p99_ms:1150.0>1000.0",
                ),
            ]
            for label, mutations, expected_breach in p99_breach_cases:
                with self.subTest(p99_breach=label):
                    cand = json.loads(json.dumps(vps_base))
                    for path_str, val in mutations:
                        set_path(cand, path_str, val)
                    res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                        cand, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
                    )
                    self.assertEqual(res["status"], "FAIL_THRESHOLD_BREACH", label)
                    self.assertIn(expected_breach, res["breached_checks"], f"{label}: {res['breached_checks']}")

            # 4. Missing p99 measurements across §3.2A, §3.2B, and §3.2C MUST report FAIL_MISSING_MEASUREMENTS
            missing_p99_paths = [
                "worklist_ssr_acceptance_and_separation.case_2c_40_staff_sessions_c12.p99_ms",
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_20.overall_summary.p99_ms",
                "s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.sessions_40.by_workload.my_customers_api.p99_ms",
                "overall_summary.p99_ms",
                "peak_40_staff_concurrent_wave.overall_summary.p99_ms",
                "s1_domain_and_database_benchmark.domain_workloads_s1.bod_executive_overview.p99_ms",
                "s1_domain_and_database_benchmark.kpi_recompute_s1.background_queue_long_recompute.concurrent_ui_overview_reads.p99_ms",
            ]
            for miss_p99 in missing_p99_paths:
                with self.subTest(missing_p99=miss_p99):
                    cand = json.loads(json.dumps(vps_base))
                    del_path(cand, miss_p99)
                    res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                        cand, owner_slo_approval=valid_approval, vps_receipts=vps_receipt_dir
                    )
                    self.assertEqual(res["status"], "FAIL_MISSING_MEASUREMENTS", miss_p99)
                    self.assertIn(miss_p99, res["missing_measurements"])

    def test_staging_vps_safe_sizing_t1c_confirmation_and_config_bound_g18_gate(self) -> None:
        import shutil
        import subprocess
        from types import SimpleNamespace
        import manage_isolated_staging_dr as dr_mod

        sys.path.insert(0, str(staging_mod.REPO_ROOT / "deploy" / "release"))
        import benchmark_p4_isolated as bench_mod

        repo_root = staging_mod.REPO_ROOT
        new_digest = (
            "ghcr.io/nobita6986/crm-cskh@sha256:ba68cd229ea3f3dc2566017e13251387b3fe398196acd4880a2a184201a00be8"
        )
        new_commit = "3c040c85a44c4ad37e9d9152b5630a4596e593ee"
        new_image_id = "sha256:29b6285a0808875b91de8115f0b61a44ec3fb47bc45da87a07d50f2995f44cf8"

        # 1. Read-only VPS preflight -> safe Staging configuration sizing across 4 capacity tiers
        high_cap = dr_mod.determine_safe_staging_vps_configuration(
            cpu_cores=8,
            mem_available_mib=12288.0,
            loadavg_1m=0.8,
            free_disk_gib=28.0,
            loopback_ports_free=True,
            live_stack_running=True,
        )
        self.assertEqual(high_cap["status"], "SAFE_FOR_G03_G05_AND_G18")
        self.assertTrue(high_cap["g03_g05_smoke_safe"])
        self.assertTrue(high_cap["g18_acceptance_safe"])
        self.assertEqual(high_cap["recommended_g03_g05_profile"], "s1_canonical_4x4")
        self.assertEqual(high_cap["recommended_g18_profile"], "s1_canonical_4x4")

        shared_4c = dr_mod.determine_safe_staging_vps_configuration(
            cpu_cores=4,
            mem_available_mib=4200.0,
            loadavg_1m=0.6,
            free_disk_gib=18.0,
            loopback_ports_free=True,
            live_stack_running=True,
        )
        self.assertEqual(shared_4c["status"], "SAFE_FOR_G03_G05_AND_G18")
        self.assertTrue(shared_4c["g03_g05_smoke_safe"])
        self.assertTrue(shared_4c["g18_acceptance_safe"])
        self.assertEqual(shared_4c["recommended_g03_g05_profile"], "staging_vps_capped")
        self.assertEqual(shared_4c["recommended_g18_profile"], "staging_vps_capped")
        self.assertEqual(shared_4c["safe_profiles_allowed"], ["staging_vps_capped", "staging_dr_smoke_only"])
        self.assertFalse(shared_4c["profile_evaluations"]["s1_canonical_4x4"]["safe_to_allocate"])

        smoke_only_2c = dr_mod.determine_safe_staging_vps_configuration(
            cpu_cores=2,
            mem_available_mib=2048.0,
            loadavg_1m=0.3,
            free_disk_gib=15.0,
            loopback_ports_free=True,
            live_stack_running=True,
        )
        self.assertEqual(smoke_only_2c["status"], "SAFE_FOR_G03_G05_SMOKE_ONLY_G18_BLOCKED")
        self.assertTrue(smoke_only_2c["g03_g05_smoke_safe"])
        self.assertFalse(smoke_only_2c["g18_acceptance_safe"])
        self.assertEqual(smoke_only_2c["recommended_g03_g05_profile"], "staging_dr_smoke_only")
        self.assertIsNone(smoke_only_2c["recommended_g18_profile"])

        unsafe_ram = dr_mod.determine_safe_staging_vps_configuration(
            cpu_cores=4,
            mem_available_mib=1024.0,
            loadavg_1m=0.4,
            free_disk_gib=20.0,
            loopback_ports_free=True,
            live_stack_running=True,
        )
        self.assertEqual(unsafe_ram["status"], "UNSAFE_INSUFFICIENT_LIVE_HEADROOM")
        self.assertFalse(unsafe_ram["g03_g05_smoke_safe"])
        self.assertFalse(unsafe_ram["g18_acceptance_safe"])
        self.assertEqual(unsafe_ram["safe_profiles_allowed"], [])

        # 2. T1C candidate confirmation validation & post-T1C G03/G05 receipt collection on new digest
        valid_t1c_confirm = {
            "confirmed": True,
            "confirmed_by": "T1C-Independent-Acceptance-Lead",
            "confirmed_at": "2026-11-12T09:10:00+07:00",
            "candidate_commit_sha": new_commit,
            "app_image_digest": new_digest,
            "verification_scope": dr_mod.STAGING_CANDIDATE_SCOPE,
            "g04_status": "FAIL",
            "production_candidate_status": "BLOCKED",
        }
        stale_t1c_confirm = dict(valid_t1c_confirm, app_image_digest=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4)
        self_t1c_confirm = dict(valid_t1c_confirm, confirmed_by="T1B-P4-02")
        bad_prod_t1c_confirm = dict(valid_t1c_confirm, production_candidate_status="READY")

        self.assertFalse(dr_mod.verify_t1c_candidate_confirmation(None, expected_app_image=new_digest)[0])
        self.assertFalse(
            dr_mod.verify_t1c_candidate_confirmation(stale_t1c_confirm, expected_app_image=new_digest)[0]
        )
        self.assertFalse(
            dr_mod.verify_t1c_candidate_confirmation(self_t1c_confirm, expected_app_image=new_digest)[0]
        )
        self.assertFalse(
            dr_mod.verify_t1c_candidate_confirmation(bad_prod_t1c_confirm, expected_app_image=new_digest)[0]
        )
        ok_t1c, norm_t1c, errs_t1c = dr_mod.verify_t1c_candidate_confirmation(
            valid_t1c_confirm, expected_app_image=new_digest
        )
        self.assertTrue(ok_t1c, errs_t1c)
        assert norm_t1c is not None
        self.assertEqual(norm_t1c["app_image_digest"], new_digest)

        docker_calls: list[list[str]] = []
        active_env_caps: dict[str, tuple[float, float]] = {
            "proxy": (256.0, 0.5),
            "frontend": (256.0, 0.5),
            "backend": (1536.0, 1.5),
            "websocket": (384.0, 0.5),
            "queue-short": (768.0, 0.75),
            "queue-long": (1536.0, 1.0),
            "scheduler": (384.0, 0.25),
            "db": (2048.0, 1.5),
            "redis-cache": (384.0, 0.25),
            "redis-queue": (384.0, 0.25),
        }
        live_snap_base = {
            "timestamp_utc": "2026-11-12T02:00:00Z",
            "live_project_name": "crm-cskh-vps-staging",
            "container_count": 1,
            "all_running": True,
            "containers": [
                {
                    "id": "cid-live-1",
                    "name": "crm-cskh-vps-staging-backend-1",
                    "service": "backend",
                    "image": "sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9",
                    "config_image": "crm-cskh:0.0.1-4672041",
                    "status": "running",
                    "health": "healthy",
                    "started_at": "2026-11-09T12:00:00Z",
                    "restart_count": 0,
                }
            ],
        }

        def fake_docker_sub(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            docker_calls.append(list(cmd))
            proj = "crm-cskh-dr-https-vps"
            if "--env-file" in cmd:
                ef = Path(cmd[cmd.index("--env-file") + 1])
                if ef.is_file():
                    ed = staging_mod.parse_env_file(ef)
                    proj = ed.get("COMPOSE_PROJECT_NAME", proj)
            if cmd[:2] == ["docker", "pull"]:
                dig = cmd[2].split("@", 1)[1]
                return subprocess.CompletedProcess(cmd, 0, stdout=f"Digest: {dig}\n", stderr="")
            if cmd[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Id": new_image_id,
                                "RepoDigests": [cmd[3]],
                                "Created": "2026-11-12T01:00:00Z",
                                "Architecture": "amd64",
                                "Os": "linux",
                                "Size": 1190528512,
                                "Config": {"User": "frappe", "Labels": {}},
                            }
                        ]
                    ),
                    stderr="",
                )
            if cmd[:2] == ["docker", "compose"]:
                if "down" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                if "config" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout=f"name: {proj}\n", stderr="")
                if "ps" in cmd and "--format" in cmd:
                    rows = []
                    for svc in dr_mod.ALL_DAEMON_SERVICES:
                        pubs = (
                            [
                                {"URL": "127.0.0.1", "PublishedPort": 18081, "TargetPort": 80, "Protocol": "tcp"},
                                {"URL": "127.0.0.1", "PublishedPort": 18443, "TargetPort": 443, "Protocol": "tcp"},
                            ]
                            if svc == "proxy"
                            else []
                        )
                        rows.append(
                            {
                                "ID": f"cid-{svc}",
                                "Name": f"{proj}-{svc}-1",
                                "Service": svc,
                                "State": "running",
                                "Publishers": pubs,
                            }
                        )
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout="\n".join(json.dumps(r) for r in rows) + "\n", stderr=""
                    )
                if "ps" in cmd and "-q" in cmd:
                    svcs = cmd[cmd.index("-q") + 1 :]
                    return subprocess.CompletedProcess(
                        cmd, 0, stdout="\n".join(f"cid-{s}" for s in svcs) + "\n", stderr=""
                    )
                if "exec" in cmd and "MOUNTED_OK" in cmd[-1]:
                    return subprocess.CompletedProcess(cmd, 0, stdout="MOUNTED_OK\n", stderr="")
            if cmd[:3] == ["docker", "network", "ls"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=f"{proj}_app_net\n{proj}_data_net\n{proj}_edge_net\n", stderr=""
                )
            if cmd[:3] == ["docker", "network", "inspect"]:
                nname = cmd[3]
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Driver": "bridge",
                                "Internal": nname.endswith(("_app_net", "_data_net")),
                                "Containers": {"c1": {"Name": f"{proj}-backend-1"}},
                            }
                        ]
                    ),
                    stderr="",
                )
            if cmd[:3] == ["docker", "volume", "ls"]:
                vols = "\n".join(
                    f"{proj}{sfx}" for sfx in ("_cert_data", "_db_data", "_logs", "_redis_queue_data", "_sites")
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=vols + "\n", stderr="")
            if cmd[:2] == ["docker", "inspect"] and "--format" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(["MARIADB_ROOT_PASSWORD_FILE=/run/secrets/db_password"]), stderr=""
                )
            if cmd[:2] == ["docker", "inspect"]:
                items = []
                for cid in cmd[2:]:
                    svc = cid.removeprefix("cid-")
                    mib, cpus = active_env_caps[svc]
                    items.append(
                        {
                            "Name": f"/{proj}-{svc}-1",
                            "Config": {"Labels": {"com.docker.compose.service": svc}},
                            "HostConfig": {
                                "Memory": int(mib * 1024 * 1024),
                                "NanoCpus": int(cpus * 1_000_000_000),
                            },
                        }
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(items), stderr="")
            raise AssertionError(f"Unexpected mocked command: {cmd}")

        def fake_verify_digest(_containers: object, expected_image: str) -> dict[str, object]:
            return {
                "pass": True,
                "expected_app_image": expected_image,
                "missing_services": [],
                "mismatches": [],
                "inspected_services": {
                    svc: {
                        "container": f"crm-cskh-dr-https-vps-{svc}-1",
                        "image_id": new_image_id,
                        "repo_digests": [expected_image],
                        "matched": True,
                    }
                    for svc in staging_mod.APP_SERVICES
                },
            }

        def fake_bootstrap(*, env_file: Path, **_kwargs: object) -> dict[str, object]:
            ed = staging_mod.parse_env_file(env_file)
            img = ed["APP_IMAGE"]
            # Confirm staging_vps_capped overrides were written to staging-dr-https.env
            self.assertEqual(ed.get("GUNICORN_WORKERS"), "2")
            self.assertEqual(ed.get("BACKEND_CPUS"), "1.50")
            self.assertEqual(ed.get("DB_MEM_LIMIT"), "2048m")
            return {
                "https_smoke": {
                    "status": "PASS",
                    "mode": "dr-https",
                    "app_image": img,
                    "checks": {
                        "container_digest_parity": fake_verify_digest([], img),
                        "http_to_https_redirect": {"pass": True},
                        "tls_certificate": {"expiry_ok": True},
                        "login_and_sid_cookie": {"pass": True},
                        "vi_ui_and_csrf": {"pass": True},
                        "wss_websocket": {"pass": True, "switched_protocols_101": True},
                    },
                }
            }

        with tempfile.TemporaryDirectory(prefix="t1a-cfg-slo-g18-") as td:
            td_path = Path(td)
            receipt_dir = td_path / "vps-receipts-new"
            state_dir = td_path / "vps-state-new"
            t1c_file = td_path / "t1c-confirm.json"
            t1c_file.write_text(json.dumps(valid_t1c_confirm), encoding="utf-8")

            with (
                mock.patch.object(dr_mod, "run_sub", side_effect=fake_docker_sub),
                mock.patch.object(dr_mod.p403_tool, "verify_running_containers_digest", side_effect=fake_verify_digest),
                mock.patch.object(dr_mod, "bootstrap_isolated_dr_stack", side_effect=fake_bootstrap),
                mock.patch.object(
                    dr_mod,
                    "capture_readonly_live_stack_snapshot",
                    return_value=json.loads(json.dumps(live_snap_base)),
                ),
                mock.patch.object(dr_mod, "_check_loopback_port_free", return_value=True),
                mock.patch.object(dr_mod.os, "cpu_count", return_value=4),
                mock.patch.object(
                    dr_mod,
                    "_read_linux_meminfo_mib",
                    return_value={
                        "mem_total_mib": 7984.0,
                        "mem_available_mib": 4200.0,
                        "swap_total_mib": 2048.0,
                        "swap_free_mib": 2048.0,
                    },
                ),
                mock.patch.object(
                    dr_mod,
                    "_read_linux_loadavg",
                    return_value={"loadavg_1m": 0.55, "loadavg_5m": 0.48, "loadavg_15m": 0.40},
                ),
                mock.patch.object(
                    dr_mod.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(
                        total=int(80 * (1024**3)),
                        used=int(62 * (1024**3)),
                        free=int(18 * (1024**3)),
                    ),
                ),
                mock.patch.object(dr_mod.socket, "gethostname", return_value="vps-hrpartner-103-77-208-186"),
                mock.patch.object(
                    dr_mod.platform, "platform", return_value="Linux-6.8.0-51-generic-x86_64-with-glibc2.39"
                ),
                mock.patch.object(dr_mod.platform, "release", return_value="6.8.0-51-generic"),
            ):
                # 2a. Unconfirmed candidate MUST fail closed BEFORE touching Docker
                with self.assertRaisesRegex(ValueError, "before T1C confirms candidate digest"):
                    dr_mod.collect_vps_g03_g05_receipts(
                        app_image=new_digest,
                        out_dir=receipt_dir,
                        require_t1c_confirmation=True,
                        t1c_confirmation=None,
                    )
                self.assertEqual(docker_calls, [])

                with self.assertRaisesRegex(ValueError, "digest_mismatch"):
                    dr_mod.collect_vps_g03_g05_receipts(
                        app_image=new_digest,
                        out_dir=receipt_dir,
                        require_t1c_confirmation=True,
                        t1c_confirmation=stale_t1c_confirm,
                    )
                self.assertEqual(docker_calls, [])

                # 2b. Requesting s1_canonical_4x4 on 4-vCPU / 4200 MiB shared VPS MUST abort BEFORE Docker pull/up
                with self.assertRaisesRegex(RuntimeError, "NOT safe on this VPS"):
                    dr_mod.collect_vps_g03_g05_receipts(
                        app_image=new_digest,
                        out_dir=receipt_dir,
                        require_t1c_confirmation=True,
                        t1c_confirmation=t1c_file,
                        resource_profile="s1_canonical_4x4",
                    )
                self.assertEqual(docker_calls, [])

                # 2c. With T1C confirmation + resource_profile="auto", resolves to staging_vps_capped & collects G03/G05
                with self.assertRaisesRegex(ValueError, "before T1C confirms candidate digest"):
                    dr_mod.refresh_staging_for_new_candidate_digest(
                        new_app_image=new_digest,
                        out_dir=receipt_dir,
                        require_t1c_confirmation=True,
                        t1c_confirmation=None,
                    )
                self.assertEqual(docker_calls, [])

                g03_g05_sum = dr_mod.collect_vps_g03_g05_receipts(
                    app_image=new_digest,
                    out_dir=receipt_dir,
                    state_dir=state_dir,
                    keep_up=True,
                    require_t1c_confirmation=True,
                    t1c_confirmation=t1c_file,
                    resource_profile="auto",
                )
                self.assertEqual(g03_g05_sum["status"], "PASS")
                self.assertTrue(g03_g05_sum["zero_live_mutation"])
                self.assertEqual(
                    g03_g05_sum["staging_resource_configuration"]["profile_id"], "staging_vps_capped"
                )
                self.assertEqual(g03_g05_sum["t1c_candidate_confirmation"]["app_image_digest"], new_digest)
                self.assertTrue((receipt_dir / "t1c-candidate-confirmation-receipt.json").is_file())

            # 3. Config-bound G18 threshold builder & validator
            with self.assertRaisesRegex(ValueError, "smoke-only"):
                dr_mod.build_config_bound_g18_thresholds_spec(
                    target_configuration_profile="staging_dr_smoke_only",
                    candidate_digest=new_digest,
                )

            capped_spec = dr_mod.build_config_bound_g18_thresholds_spec(
                target_configuration_profile="staging_vps_capped",
                candidate_digest=new_digest,
                candidate_image_id=new_image_id,
                ticket_version="v1.7-staging-vps-capped",
                threshold_overrides={
                    "precheck_7_metrics.bod_executive_overview_api_p95_ms.threshold": 1500.0,
                    "section_3_2A_cases_and_session_models.bod_executive_overview_role_session.max_p95_ms": 1500.0,
                    "section_3_2A_cases_and_session_models.bod_executive_overview_role_session.max_p99_ms": 1800.0,
                    "section_3_2C_dataset_batch_and_background.rq_short_50_job_burst_and_post_run_db_health.max_post_run_slow_queries": 2,
                },
            )
            capped_sha = dr_mod.compute_g18_thresholds_sha256(capped_spec)
            _, val_sha, val_errs = dr_mod.validate_g18_thresholds_for_configuration(
                thresholds_spec=capped_spec,
                target_configuration_profile="staging_vps_capped",
                expected_digest=new_digest,
            )
            self.assertEqual(val_errs, [])
            self.assertEqual(val_sha, capped_sha)

            # Zero-error budget loosening, p99 < p95, or S1 dataset tampering MUST be rejected
            bad_err_spec = json.loads(json.dumps(capped_spec))
            bad_err_spec["section_3_2A_cases_and_session_models"]["my_customers_worklist_api_c12_shared"][
                "max_error_count"
            ] = 1
            bad_err_spec["section_3_2A_cases_and_session_models"]["my_customers_worklist_api_c12_shared"][
                "max_p99_ms"
            ] = 200.0
            bad_err_spec["section_3_2C_dataset_batch_and_background"]["s1_dataset_and_explain_plans"][
                "expected_counts"
            ]["crm_leads"] = 1000
            _, _, bad_cfg_errs = dr_mod.validate_g18_thresholds_for_configuration(
                thresholds_spec=bad_err_spec,
                target_configuration_profile="staging_vps_capped",
                expected_digest=new_digest,
            )
            self.assertTrue(any("zero_error_budget_loosened" in e for e in bad_cfg_errs), bad_cfg_errs)
            self.assertTrue(any("p99_threshold_lt_p95_threshold" in e for e in bad_cfg_errs), bad_cfg_errs)
            self.assertTrue(any("invariant_threshold_modified" in e for e in bad_cfg_errs), bad_cfg_errs)

            capped_cfg_sha = dr_mod.get_staging_resource_profile_spec("staging_vps_capped")["config_spec_sha256"]
            canon_4x4_spec = dr_mod.build_config_bound_g18_thresholds_spec(
                target_configuration_profile="s1_canonical_4x4",
                candidate_digest=new_digest,
                candidate_image_id=new_image_id,
                ticket_version="v1.7-s1-4x4",
            )
            canon_4x4_sha = dr_mod.compute_g18_thresholds_sha256(canon_4x4_spec)
            canon_4x4_cfg_sha = dr_mod.get_staging_resource_profile_spec("s1_canonical_4x4")["config_spec_sha256"]

            capped_spec_file = td_path / "g18-thresholds-staging-vps-capped.json"
            capped_spec_file.write_bytes(dr_mod.serialize_g18_thresholds_spec(capped_spec))

            owner_capped_approval = {
                "approved": True,
                "ticket_version": "v1.7-staging-vps-capped",
                "signed_by": "owner-representative@hrpartner.vn",
                "signed_at": "2026-11-12T09:30:00+07:00",
                "thresholds_sha256": capped_sha,
                "approved_configuration_profile": "staging_vps_capped",
                "approved_config_spec_sha256": capped_cfg_sha,
                "approved_candidate_digest": new_digest,
                "approved_thresholds": {
                    k: spec["threshold"] for k, spec in capped_spec["precheck_7_metrics"].items()
                },
            }
            owner_capped_file = td_path / "owner-capped-approval.json"
            owner_capped_file.write_text(json.dumps(owner_capped_approval), encoding="utf-8")

            owner_4x4_approval = {
                "approved": True,
                "ticket_version": "v1.7-s1-4x4",
                "signed_by": "owner-representative@hrpartner.vn",
                "signed_at": "2026-11-12T09:30:00+07:00",
                "thresholds_sha256": canon_4x4_sha,
                "approved_configuration_profile": "s1_canonical_4x4",
                "approved_config_spec_sha256": canon_4x4_cfg_sha,
                "approved_candidate_digest": new_digest,
                "approved_thresholds": {
                    k: spec["threshold"] for k, spec in canon_4x4_spec["precheck_7_metrics"].items()
                },
            }

            # 4. Pre-measurement gate (prepare-staging-g18) & protected runner (run-staging-vps-g18)
            bench_sub_calls: list[list[str]] = []

            def never_run_bench(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
                bench_sub_calls.append(list(cmd))
                raise AssertionError(f"G18 benchmark MUST NOT execute when pre-measurement gate is blocked: {cmd}")

            with mock.patch.object(dr_mod, "run_sub", side_effect=never_run_bench):
                # 4a. Missing/stale T1C confirmation -> blocked, zero benchmark calls
                no_t1c_dir = td_path / "vps-receipts-no-t1c"
                shutil.copytree(receipt_dir, no_t1c_dir)
                (no_t1c_dir / "t1c-candidate-confirmation-receipt.json").unlink()
                sum_no_t1c = json.loads((no_t1c_dir / "vps-g03-g05-summary.json").read_text(encoding="utf-8"))
                sum_no_t1c.pop("t1c_candidate_confirmation", None)
                (no_t1c_dir / "vps-g03-g05-summary.json").write_text(
                    json.dumps(sum_no_t1c), encoding="utf-8"
                )
                r_no_t1c = dr_mod.run_staging_vps_g18_acceptance(
                    vps_receipts=no_t1c_dir,
                    expected_digest=new_digest,
                    benchmark_out_json=td_path / "never1.json",
                    thresholds_path=capped_spec_file,
                    owner_slo_approval=owner_capped_file,
                )
                self.assertEqual(r_no_t1c["status"], "BLOCKED_PENDING_T1C_CANDIDATE_CONFIRMATION")
                self.assertFalse(r_no_t1c["benchmark_executed"])
                self.assertEqual(bench_sub_calls, [])

                # 4b. Tight VPS (staging_dr_smoke_only, g18_acceptance_safe=False) -> blocked
                smoke_only_dir = td_path / "vps-receipts-smoke-only"
                shutil.copytree(receipt_dir, smoke_only_dir)
                pre_smk = json.loads((smoke_only_dir / "readonly-vps-preflight.json").read_text(encoding="utf-8"))
                pre_smk["safe_staging_allocation"] = smoke_only_2c
                (smoke_only_dir / "readonly-vps-preflight.json").write_text(
                    json.dumps(pre_smk), encoding="utf-8"
                )
                r_smk = dr_mod.run_staging_vps_g18_acceptance(
                    vps_receipts=smoke_only_dir,
                    expected_digest=new_digest,
                    benchmark_out_json=td_path / "never2.json",
                    thresholds_path=capped_spec_file,
                    owner_slo_approval=owner_capped_file,
                )
                self.assertEqual(r_smk["status"], "FAIL_UNSAFE_OR_INVALID_STAGING_VPS_CONFIGURATION")
                self.assertFalse(r_smk["benchmark_executed"])

                # 4c. Scorer still pointing to default g18_slo_thresholds_v1_4.json (un-bound) -> blocked
                r_unbound = dr_mod.run_staging_vps_g18_acceptance(
                    vps_receipts=receipt_dir,
                    expected_digest=new_digest,
                    benchmark_out_json=td_path / "never3.json",
                    thresholds_path=dr_mod.DEFAULT_G18_THRESHOLDS_V1_4_PATH,
                    owner_slo_approval=owner_capped_file,
                )
                self.assertEqual(r_unbound["status"], "BLOCKED_SCORER_THRESHOLDS_NOT_BOUND_TO_CONFIGURATION")
                self.assertFalse(r_unbound["benchmark_executed"])

                # 4d. Owner signed SLO for s1_canonical_4x4 while Staging VPS is staging_vps_capped -> blocked
                r_wrong_prof = dr_mod.run_staging_vps_g18_acceptance(
                    vps_receipts=receipt_dir,
                    expected_digest=new_digest,
                    benchmark_out_json=td_path / "never4.json",
                    thresholds_path=capped_spec_file,
                    owner_slo_approval=owner_4x4_approval,
                )
                self.assertEqual(r_wrong_prof["status"], "FAIL_SCORER_DOES_NOT_MATCH_OWNER_SIGNED_CONFIG_SLO")
                self.assertFalse(r_wrong_prof["benchmark_executed"])

                # 4e. Scorer threshold JSON differs by 1.0 ms from Owner's signed thresholds_sha256 -> blocked
                drift_spec = json.loads(json.dumps(capped_spec))
                drift_spec["precheck_7_metrics"]["bod_executive_overview_api_p95_ms"]["threshold"] = 1501.0
                r_drift = dr_mod.run_staging_vps_g18_acceptance(
                    vps_receipts=receipt_dir,
                    expected_digest=new_digest,
                    benchmark_out_json=td_path / "never5.json",
                    thresholds_spec=drift_spec,
                    owner_slo_approval=owner_capped_approval,
                )
                self.assertEqual(r_drift["status"], "FAIL_SCORER_DOES_NOT_MATCH_OWNER_SIGNED_CONFIG_SLO")
                self.assertFalse(r_drift["benchmark_executed"])
                self.assertEqual(bench_sub_calls, [])

            # 4f. Positive prepare-staging-g18 check & CLI compatibility with benchmark_p4_isolated.py
            prep_out = td_path / "prep-gate.json"
            bench_out_file = td_path / "staging-vps-s1-benchmark.json"
            reconcile_out_file = td_path / "staging-vps-g18-reconcile.json"
            self.assertEqual(
                dr_mod.main(
                    [
                        "prepare-staging-g18",
                        "--vps-receipt-dir",
                        str(receipt_dir),
                        "--expected-digest",
                        new_digest,
                        "--target-config-profile",
                        "auto",
                        "--thresholds-json",
                        str(capped_spec_file),
                        "--owner-approval-json",
                        str(owner_capped_file),
                        "--bench-port",
                        "18882",
                        "--benchmark-out",
                        str(bench_out_file),
                        "--out",
                        str(prep_out),
                    ]
                ),
                0,
            )
            prep_doc = json.loads(prep_out.read_text(encoding="utf-8"))
            self.assertEqual(prep_doc["status"], "READY_FOR_G18_ACCEPTANCE_MEASUREMENT")
            self.assertTrue(prep_doc["measurement_allowed"])
            self.assertEqual(prep_doc["resolved_configuration_profile"], "staging_vps_capped")
            # Verify benchmark_p4_isolated.py main() accepts the exact planned command flags & forwards profile/headroom
            forwarded_bench_kwargs: dict[str, object] = {}

            def fake_provision_s1(**kwargs: object) -> dict[str, object]:
                forwarded_bench_kwargs.update(kwargs)
                return {
                    "status": "PASS",
                    "profile_name": "s1_production_load_50k_leads_40_staff",
                    "sla_commitment_status": "PENDING_OWNER_REVIEW",
                }

            with mock.patch.object(bench_mod, "provision_and_execute_s1_benchmark", side_effect=fake_provision_s1):
                self.assertEqual(bench_mod.main(prep_doc["planned_benchmark_command"][2:]), 0)
            self.assertEqual(forwarded_bench_kwargs["resource_profile"], "staging_vps_capped")
            self.assertTrue(forwarded_bench_kwargs["require_live_headroom"])
            self.assertEqual(forwarded_bench_kwargs["pull_policy"], "never")
            self.assertEqual(forwarded_bench_kwargs["port"], 18882)
            self.assertEqual(forwarded_bench_kwargs["app_image"], new_digest)

            # Verify benchmark_p4_isolated.enforce_live_resource_headroom blocks low RAM headroom
            fake_meminfo = td_path / "fake-proc-meminfo"
            fake_meminfo.write_text("MemTotal: 8192000 kB\nMemAvailable: 1500000 kB\n", encoding="utf-8")
            with mock.patch.object(
                bench_mod,
                "Path",
                side_effect=lambda p: fake_meminfo if str(p) == "/proc/meminfo" else Path(p),
            ):
                with self.assertRaisesRegex(RuntimeError, "Live resource guard refused execution"):
                    bench_mod.enforce_live_resource_headroom(min_mem_available_mib=2048.0)

            # 4g. Positive run-staging-vps-g18 execution + live service preservation guard
            ci_template_bench = json.loads(
                (
                    repo_root
                    / "deploy"
                    / "ops"
                    / "evidence"
                    / "p4-candidate-and-s1-20261109"
                    / "benchmark-s1-report.json"
                ).read_text(encoding="utf-8")
            )
            vps_capped_bench = json.loads(json.dumps(ci_template_bench))
            vps_capped_bench["app_image_tested"] = new_digest
            vps_capped_bench["resource_profile"] = "staging_vps_capped"
            vps_capped_bench["isolation_guard"]["compose_project"] = "crm-cskh-s1-bench-vps"
            vps_capped_bench["s1_domain_and_database_benchmark"]["runner_hardware"]["platform"] = (
                "Linux-6.8.0-51-generic-x86_64-with-glibc2.39"
            )
            vps_capped_bench["s1_http_acceptance_and_mixed_waves"]["bod_overview_and_drilldown_http"][
                "bod_executive_overview_role_api"
            ]["p95_ms"] = 1280.0
            vps_capped_bench["s1_http_acceptance_and_mixed_waves"]["bod_overview_and_drilldown_http"][
                "bod_executive_overview_role_api"
            ]["p99_ms"] = 1420.0
            vps_capped_bench["p4_9_2_acceptance_matrix"]["items"]["bod_overview_snapshot_p95_le_3000ms"][
                "measured_http_p95_ms"
            ] = 1280.0
            vps_capped_bench["queue_and_db_after"]["mariadb_global_status"]["Slow_queries"] = "1"

            def fake_exec_bench(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
                bench_sub_calls.append(list(cmd))
                out_idx = cmd.index("--out") + 1
                Path(cmd[out_idx]).write_text(json.dumps(vps_capped_bench), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

            with (
                mock.patch.object(dr_mod, "run_sub", side_effect=fake_exec_bench),
                mock.patch.object(
                    dr_mod,
                    "capture_readonly_live_stack_snapshot",
                    side_effect=[
                        json.loads(json.dumps(live_snap_base)),
                        json.loads(json.dumps(live_snap_base)),
                    ],
                ),
            ):
                rc_run = dr_mod.main(
                    [
                        "run-staging-vps-g18",
                        "--vps-receipt-dir",
                        str(receipt_dir),
                        "--expected-digest",
                        new_digest,
                        "--target-config-profile",
                        "auto",
                        "--thresholds-json",
                        str(capped_spec_file),
                        "--owner-approval-json",
                        str(owner_capped_file),
                        "--bench-port",
                        "18882",
                        "--benchmark-out",
                        str(bench_out_file),
                        "--reconcile-out",
                        str(reconcile_out_file),
                    ]
                )
                self.assertEqual(rc_run, 0)
            self.assertEqual(len(bench_sub_calls), 1)
            rec_doc = json.loads(reconcile_out_file.read_text(encoding="utf-8"))
            self.assertEqual(rec_doc["status"], "PASS", rec_doc)
            self.assertTrue(rec_doc["benchmark_executed"])
            self.assertTrue(rec_doc["config_bound_mode"])
            self.assertEqual(rec_doc["target_configuration_profile"], "staging_vps_capped")
            self.assertTrue(rec_doc["g18_pass_requirements"]["benchmark_and_slo_bound_to_safe_staging_configuration"])
            self.assertTrue(rec_doc["g18_pass_requirements"]["t1c_candidate_confirmation_verified"])
            self.assertTrue((td_path / "g18-live-preservation-before-after.json").is_file())

            # Live container restart during G18 MUST fail closed with FAIL_LIVE_SERVICE_MUTATED_DURING_G18
            mutated_after = json.loads(json.dumps(live_snap_base))
            mutated_after["containers"][0]["restart_count"] = 1
            with (
                mock.patch.object(dr_mod, "run_sub", side_effect=fake_exec_bench),
                mock.patch.object(
                    dr_mod,
                    "capture_readonly_live_stack_snapshot",
                    side_effect=[json.loads(json.dumps(live_snap_base)), mutated_after],
                ),
            ):
                live_mut_res = dr_mod.run_staging_vps_g18_acceptance(
                    vps_receipts=receipt_dir,
                    expected_digest=new_digest,
                    benchmark_out_json=bench_out_file,
                    thresholds_path=capped_spec_file,
                    owner_slo_approval=owner_capped_file,
                )
                self.assertEqual(live_mut_res["status"], "FAIL_LIVE_SERVICE_MUTATED_DURING_G18")
                self.assertFalse(live_mut_res["live_zero_mutation"])

            # 5. Post-measurement cross-profile scoring guard: s1_canonical_4x4 report vs staging_vps_capped SLO -> FAIL_CONFIGURATION_MISMATCH
            wrong_prof_bench = json.loads(json.dumps(vps_capped_bench))
            wrong_prof_bench["resource_profile"] = "s1_canonical_4x4"
            cross_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                wrong_prof_bench,
                owner_slo_approval=owner_capped_approval,
                vps_receipts=receipt_dir,
                thresholds_spec=capped_spec,
                expected_digest=new_digest,
                t1c_confirmation=valid_t1c_confirm,
                require_t1c_confirmation=True,
            )
            self.assertEqual(cross_res["status"], "FAIL_CONFIGURATION_MISMATCH")
            self.assertIn(
                "benchmark_resource_profile_mismatch:expected='staging_vps_capped':got='s1_canonical_4x4'",
                cross_res["configuration_validation_errors"],
            )

    def test_synced_p4_s1_slo_bundle_ba68cd22_precheck_and_g18_gate(self) -> None:
        import subprocess
        import manage_isolated_staging_dr as dr_mod

        repo_root = staging_mod.REPO_ROOT
        cand_dir = repo_root / "deploy" / "ops" / "evidence" / "p4-candidate-ba68cd22-s1-20261112"
        bench_4x4_path = cand_dir / "benchmark-s1-report.json"
        bench_capped_path = cand_dir / "benchmark-s1-staging-vps-capped.json"
        diag_4x4_path = cand_dir / "g18-diagnostic-post_opt_s1_canonical_4x4.json"
        diag_capped_path = cand_dir / "g18-diagnostic-post_opt_staging_vps_capped.json"

        # 1. Immutable SLO spec SHA-256 & governance checks across profiles
        spec_sha_before = dr_mod.compute_p4_s1_slo_spec_sha256(dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH)
        self.assertEqual(spec_sha_before, dr_mod.LOCKED_P4_S1_SLO_SPEC_SHA256)
        self.assertEqual(
            dr_mod.CURRENT_CANDIDATE_SOURCE_SHA,
            "3c040c85a44c4ad37e9d9152b5630a4596e593ee",
        )

        for prof in ("s1_canonical_4x4", "staging_vps_capped"):
            val_spec, val_sha, val_errs = dr_mod.validate_g18_thresholds_for_configuration(
                thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
                target_configuration_profile=prof,
                expected_digest=dr_mod.CURRENT_CANDIDATE_DIGEST,
            )
            self.assertEqual(val_errs, [], f"{prof}: {val_errs}")
            self.assertEqual(val_sha, dr_mod.LOCKED_P4_S1_SLO_SPEC_SHA256)
            self.assertEqual(val_spec["slo_bundle_id"], "cskh-p4-g18-s1-endpoint-slo")

        _, _, smoke_prof_errs = dr_mod.validate_g18_thresholds_for_configuration(
            thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
            target_configuration_profile="staging_dr_smoke_only",
            expected_digest=dr_mod.CURRENT_CANDIDATE_DIGEST,
        )
        self.assertTrue(
            any("target_configuration_profile_not_g18_s1_capable" in e for e in smoke_prof_errs),
            smoke_prof_errs,
        )

        _, _, stale_dig_errs = dr_mod.validate_g18_thresholds_for_configuration(
            thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
            target_configuration_profile="staging_vps_capped",
            expected_digest=dr_mod.LOCKED_CANDIDATE_DIGEST_V1_4,
        )
        self.assertTrue(
            any("thresholds_locked_candidate_digest_mismatch" in e for e in stale_dig_errs),
            stale_dig_errs,
        )

        canon_s1_spec = json.loads(dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH.read_text(encoding="utf-8"))
        inflated_spec = json.loads(json.dumps(canon_s1_spec))
        for r in inflated_spec["rules"]:
            if r["slo_id"] == "API-05B-BOD":
                r["proposed_thresholds"]["staging_vps_capped"]["max_p95_ms"] = 3500.0
                r["proposed_thresholds"]["staging_vps_capped"]["max_p99_ms"] = 4000.0
                r["proposed_thresholds"]["staging_vps_capped"]["max_timeout_count"] = 1
        _, _, inf_errs = dr_mod.validate_g18_thresholds_for_configuration(
            thresholds_spec=inflated_spec,
            target_configuration_profile="staging_vps_capped",
            expected_digest=dr_mod.CURRENT_CANDIDATE_DIGEST,
        )
        self.assertTrue(any("exceeds P4.md §9.2 ceiling" in e for e in inf_errs), inf_errs)
        self.assertTrue(any("zero_error_budget_loosened" in e for e in inf_errs), inf_errs)

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)

            # 2. CLI reconcile-g18-slo against real ba68cd22 CI benchmark & G18 deep diagnostic reports
            for prof, bpath, dpath in (
                ("s1_canonical_4x4", bench_4x4_path, diag_4x4_path),
                ("staging_vps_capped", bench_capped_path, diag_capped_path),
            ):
                out_rec = td_path / f"rec-{prof}.json"
                rc = dr_mod.main(
                    [
                        "reconcile-g18-slo",
                        "--benchmark-json",
                        str(bpath),
                        "--thresholds-json",
                        str(dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH),
                        "--owner-approval-json",
                        str(dr_mod.DEFAULT_P4_S1_SLO_APPROVAL_PATH),
                        "--g18-diag-json",
                        str(dpath),
                        "--out",
                        str(out_rec),
                    ]
                )
                self.assertEqual(rc, 2)
                rec = json.loads(out_rec.read_text(encoding="utf-8"))
                self.assertEqual(
                    rec["status"],
                    "BLOCKED_CI_PRECHECK_ONLY_PENDING_VPS_RECEIPTS_AND_OWNER_APPROVAL",
                    rec,
                )
                self.assertEqual(rec["expected_candidate_digest"], dr_mod.CURRENT_CANDIDATE_DIGEST)
                self.assertTrue(rec["digest_matches_candidate"])
                self.assertEqual(rec["evaluated_thresholds_sha256"], dr_mod.LOCKED_P4_S1_SLO_SPEC_SHA256)
                self.assertEqual(rec["target_configuration_profile"], prof)
                self.assertTrue(rec["measurements_complete"], rec["missing_measurements"])
                self.assertEqual(rec["breached_checks"], [])
                self.assertTrue(rec["all_7_metrics_within_v1_4_thresholds"])
                bundle_eval = rec["s1_slo_bundle_evaluation"]
                self.assertTrue(bundle_eval["all_passed"])
                self.assertEqual(bundle_eval["category_verdicts"]["SSR"]["passed_count"], 7)
                self.assertEqual(bundle_eval["category_verdicts"]["JSON_API"]["passed_count"], 20)
                self.assertEqual(bundle_eval["category_verdicts"]["AGGREGATE_AND_BACKGROUND"]["passed_count"], 8)
                self.assertEqual(len(bundle_eval["g18_deep_diagnostic_results"]), 12)
                self.assertTrue(all(r["passed"] for r in bundle_eval["g18_deep_diagnostic_results"]))

            # 3. Missing measurements & threshold breaches on cskh-p4-s1-slo-spec-v1 fail closed
            capped_doc = json.loads(bench_capped_path.read_text(encoding="utf-8"))
            pending_approval_doc = json.loads(dr_mod.DEFAULT_P4_S1_SLO_APPROVAL_PATH.read_text(encoding="utf-8"))

            missing_p99_doc = json.loads(json.dumps(capped_doc))
            del missing_p99_doc["s1_http_acceptance_and_mixed_waves"]["bod_overview_and_drilldown_http"][
                "kpi_drilldown_monthly_company_role_api"
            ]["p99_ms"]
            miss_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                missing_p99_doc,
                owner_slo_approval=pending_approval_doc,
                thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
            )
            self.assertEqual(miss_res["status"], "FAIL_MISSING_MEASUREMENTS")
            self.assertIn(
                "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http.kpi_drilldown_monthly_company_role_api.p99_ms",
                miss_res["missing_measurements"],
            )

            missing_headroom_doc = json.loads(json.dumps(capped_doc))
            del missing_headroom_doc["live_resource_headroom_precheck"]
            miss_hr_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                missing_headroom_doc,
                owner_slo_approval=pending_approval_doc,
                thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
            )
            self.assertEqual(miss_hr_res["status"], "FAIL_MISSING_MEASUREMENTS")
            self.assertIn("live_resource_headroom_precheck", miss_hr_res["missing_measurements"])

            bad_p99_doc = json.loads(json.dumps(capped_doc))
            bad_p99_doc["s1_http_acceptance_and_mixed_waves"]["bod_overview_and_drilldown_http"][
                "kpi_drilldown_monthly_company_role_api"
            ]["p99_ms"] = 1850.0
            breach_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                bad_p99_doc,
                owner_slo_approval=pending_approval_doc,
                thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
            )
            self.assertEqual(breach_res["status"], "FAIL_THRESHOLD_BREACH")
            self.assertTrue(any("s1_slo.API-05B-BOD" in b for b in breach_res["breached_checks"]), breach_res["breached_checks"])

            bad_explain_doc = json.loads(json.dumps(capped_doc))
            bad_explain_doc["s1_domain_and_database_benchmark"]["mariadb_explain_plans"][
                "kpi_drilldown_interval_page"
            ][0]["key"] = "PRIMARY"
            exp_res = dr_mod.reconcile_g18_benchmark_with_v1_4_slos(
                bad_explain_doc,
                owner_slo_approval=pending_approval_doc,
                thresholds_path=dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH,
            )
            self.assertEqual(exp_res["status"], "FAIL_THRESHOLD_BREACH")
            self.assertTrue(
                any("mariadb_explain_plans.kpi_drilldown_interval_page" in b for b in exp_res["breached_checks"]),
                exp_res["breached_checks"],
            )

            # 4. Pre-measurement gate & acceptance runner with decoupled p4-s1-slo-approval.json
            receipt_dir = self._build_mock_staging_vps_bundle(
                repo_root,
                td_path / "vps-receipts-ba68cd22",
                expected_digest=dr_mod.CURRENT_CANDIDATE_DIGEST,
            )
            valid_t1c = {
                "confirmed": True,
                "confirmed_by": "T1C-Independent-Acceptance-Lead",
                "confirmed_at": "2026-11-12T10:00:00+07:00",
                "candidate_commit_sha": dr_mod.CURRENT_CANDIDATE_SOURCE_SHA,
                "app_image_digest": dr_mod.CURRENT_CANDIDATE_DIGEST,
                "verification_scope": dr_mod.STAGING_CANDIDATE_SCOPE,
                "g04_status": "FAIL",
                "production_candidate_status": "BLOCKED",
            }
            verified_t1c_doc = dr_mod.verify_t1c_candidate_confirmation(
                valid_t1c, expected_app_image=dr_mod.CURRENT_CANDIDATE_DIGEST
            )[1]
            (receipt_dir / "t1c-candidate-confirmation-receipt.json").write_text(
                json.dumps(verified_t1c_doc, indent=2), encoding="utf-8"
            )
            preflight_path = receipt_dir / "readonly-vps-preflight.json"
            preflight_doc = json.loads(preflight_path.read_text(encoding="utf-8"))
            preflight_doc["safe_staging_allocation"] = dr_mod.determine_safe_staging_vps_configuration(
                cpu_cores=4,
                mem_available_mib=4200.0,
                loadavg_1m=0.6,
                free_disk_gib=18.0,
                loopback_ports_free=True,
                live_stack_running=True,
            )
            preflight_path.write_text(json.dumps(preflight_doc, indent=2), encoding="utf-8")
            sum_path = receipt_dir / "vps-g03-g05-summary.json"
            sum_doc = json.loads(sum_path.read_text(encoding="utf-8"))
            sum_doc["staging_resource_configuration"] = dr_mod.get_staging_resource_profile_spec("staging_vps_capped")
            sum_doc["t1c_candidate_confirmation"] = verified_t1c_doc
            sum_path.write_text(json.dumps(sum_doc, indent=2), encoding="utf-8")

            # 4a. Default CLI prepare-staging-g18 with pending p4-s1-slo-approval.json -> BLOCKED_PENDING_OWNER_SIGNED_CONFIG_SLO
            prep_pending_out = td_path / "prep-pending.json"
            self.assertEqual(
                dr_mod.main(
                    [
                        "prepare-staging-g18",
                        "--vps-receipt-dir",
                        str(receipt_dir),
                        "--out",
                        str(prep_pending_out),
                    ]
                ),
                2,
            )
            prep_pending = json.loads(prep_pending_out.read_text(encoding="utf-8"))
            self.assertEqual(prep_pending["status"], "BLOCKED_PENDING_OWNER_SIGNED_CONFIG_SLO")
            self.assertFalse(prep_pending["measurement_allowed"])
            self.assertEqual(prep_pending["resolved_configuration_profile"], "staging_vps_capped")

            # 4b. Profile mismatch in decoupled approval (signed only for s1_canonical_4x4) -> FAIL
            wrong_prof_app_file = td_path / "approval-4x4-only.json"
            wrong_prof_app = {
                "schema_version": "cskh-p4-s1-slo-approval-v1",
                "slo_bundle_id": "cskh-p4-g18-s1-endpoint-slo",
                "slo_version": "1.0.0",
                "slo_spec_sha256": dr_mod.LOCKED_P4_S1_SLO_SPEC_SHA256,
                "approved_image_digest": dr_mod.CURRENT_CANDIDATE_DIGEST,
                "approved_profiles": ["s1_canonical_4x4"],
                "approval_status": "OWNER_APPROVED",
                "owner_signoff": {
                    "approved_by": "CRM Product & Ops Owner",
                    "approved_at_utc": "2026-11-12T03:00:00Z",
                    "decision_reference": "OWNER-SLO-4X4-ONLY",
                },
            }
            wrong_prof_app_file.write_text(json.dumps(wrong_prof_app, indent=2), encoding="utf-8")
            gate_wrong_prof = dr_mod.verify_g18_acceptance_measurement_gate(
                vps_receipts=receipt_dir,
                owner_slo_approval=wrong_prof_app_file,
            )
            self.assertEqual(gate_wrong_prof["status"], "FAIL_SCORER_DOES_NOT_MATCH_OWNER_SIGNED_CONFIG_SLO")
            self.assertFalse(gate_wrong_prof["measurement_allowed"])

            # 4c. Self-signed approval by T1B -> FAIL
            self_signed_app_file = td_path / "approval-self-signed.json"
            self_signed_app = dict(
                wrong_prof_app,
                approved_profiles=["staging_vps_capped"],
                owner_signoff={
                    "approved_by": "T1B-Release-Engineer",
                    "approved_at_utc": "2026-11-12T03:00:00Z",
                    "decision_reference": "SELF-SIGN-ATTEMPT",
                },
            )
            self_signed_app_file.write_text(json.dumps(self_signed_app, indent=2), encoding="utf-8")
            gate_self_sign = dr_mod.verify_g18_acceptance_measurement_gate(
                vps_receipts=receipt_dir,
                owner_slo_approval=self_signed_app_file,
            )
            self.assertEqual(gate_self_sign["status"], "FAIL_SCORER_DOES_NOT_MATCH_OWNER_SIGNED_CONFIG_SLO")
            self.assertFalse(gate_self_sign["measurement_allowed"])

            # 4d. Valid Owner-approved decoupled p4-s1-slo-approval-v1 -> READY -> run-staging-vps-g18 PASS
            valid_app_file = td_path / "approval-capped-owner.json"
            valid_app = dict(
                wrong_prof_app,
                approved_profiles=["staging_vps_capped"],
                owner_signoff={
                    "approved_by": "CRM Product & Ops Owner",
                    "approved_at_utc": "2026-11-12T03:15:00Z",
                    "decision_reference": "OWNER-SLO-2026-11-12-BA68CD22",
                },
            )
            valid_app_file.write_text(json.dumps(valid_app, indent=2), encoding="utf-8")

            vps_bench_doc = json.loads(json.dumps(capped_doc))
            vps_bench_doc["isolation_guard"]["compose_project"] = "crm-cskh-s1-bench-vps"
            vps_bench_doc["s1_domain_and_database_benchmark"]["runner_hardware"]["platform"] = (
                "Linux-6.8.0-51-generic-x86_64-with-glibc2.39"
            )
            live_snap = {
                "timestamp_utc": "2026-11-12T03:20:00Z",
                "live_project_name": "crm-cskh-vps-staging",
                "container_count": 1,
                "all_running": True,
                "containers": [
                    {
                        "id": "cid-live-1",
                        "name": "crm-cskh-vps-staging-backend-1",
                        "service": "backend",
                        "image": "sha256:acb950578479189ab2f11d8843d1182b83e78789bda99b4743ed24d8b0ede0d9",
                        "config_image": "crm-cskh:0.0.1-4672041",
                        "status": "running",
                        "health": "healthy",
                        "started_at": "2026-11-09T12:00:00Z",
                        "restart_count": 0,
                    }
                ],
            }

            def fake_run_s1_vps(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
                out_idx = cmd.index("--out") + 1
                Path(cmd[out_idx]).write_text(json.dumps(vps_bench_doc), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, stdout="OK\n", stderr="")

            bench_vps_out = td_path / "vps-bench-ba68cd22.json"
            rec_vps_out = td_path / "vps-rec-ba68cd22.json"
            with (
                mock.patch.object(dr_mod, "run_sub", side_effect=fake_run_s1_vps),
                mock.patch.object(
                    dr_mod,
                    "capture_readonly_live_stack_snapshot",
                    side_effect=[json.loads(json.dumps(live_snap)), json.loads(json.dumps(live_snap))],
                ),
            ):
                rc_vps = dr_mod.main(
                    [
                        "run-staging-vps-g18",
                        "--vps-receipt-dir",
                        str(receipt_dir),
                        "--owner-approval-json",
                        str(valid_app_file),
                        "--g18-diag-json",
                        str(diag_capped_path),
                        "--benchmark-out",
                        str(bench_vps_out),
                        "--reconcile-out",
                        str(rec_vps_out),
                    ]
                )
                self.assertEqual(rc_vps, 0)

            rec_vps = json.loads(rec_vps_out.read_text(encoding="utf-8"))
            self.assertEqual(rec_vps["status"], "PASS", rec_vps)
            self.assertTrue(rec_vps["owner_slo_v1_4_signed"])
            self.assertEqual(rec_vps["evaluated_thresholds_sha256"], dr_mod.LOCKED_P4_S1_SLO_SPEC_SHA256)
            self.assertEqual(rec_vps["owner_approval_thresholds_sha256"], dr_mod.LOCKED_P4_S1_SLO_SPEC_SHA256)
            self.assertTrue(rec_vps["s1_slo_bundle_evaluation"]["all_passed"])
            self.assertEqual(len(rec_vps["s1_slo_bundle_evaluation"]["g18_deep_diagnostic_results"]), 12)
            self.assertTrue(all(rec_vps["g18_pass_requirements"].values()), rec_vps["g18_pass_requirements"])
            # Submitted SLO spec bytes remained 100% untouched
            self.assertEqual(
                dr_mod.compute_p4_s1_slo_spec_sha256(dr_mod.DEFAULT_P4_S1_SLO_SPEC_PATH),
                spec_sha_before,
            )


if __name__ == "__main__":
    unittest.main()
