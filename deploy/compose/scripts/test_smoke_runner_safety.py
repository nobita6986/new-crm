#!/usr/bin/env python3
"""Regression unit tests for `run_isolated_smoke.py` isolation & safety guards."""

from __future__ import annotations

import http.server
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_isolated_smoke as smoke  # noqa: E402

RUNNER_PATH = SCRIPTS_DIR / "run_isolated_smoke.py"


class SmokeRunnerSafetyTests(unittest.TestCase):
    def test_unique_per_run_project_and_site(self) -> None:
        r1 = subprocess.run(
            [sys.executable, "-X", "utf8", str(RUNNER_PATH), "--preflight-only"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        r2 = subprocess.run(
            [sys.executable, "-X", "utf8", str(RUNNER_PATH), "--preflight-only"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        j1 = json.loads(r1.stdout)
        j2 = json.loads(r2.stdout)

        self.assertNotEqual(j1["run_id"], j2["run_id"])
        self.assertNotEqual(j1["project_name"], j2["project_name"])
        self.assertNotEqual(j1["site_name"], j2["site_name"])
        self.assertRegex(j1["project_name"], r"^crm-cskh-p4-smoke-[a-z0-9-]+$")
        self.assertRegex(j1["site_name"], r"^p4-smoke-[a-z0-9-]+\.localhost$")

    def test_preflight_exit_zero_requires_json_status_inspection(self) -> None:
        res = subprocess.run(
            [sys.executable, "-X", "utf8", str(RUNNER_PATH), "--preflight-only"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(res.returncode, 0)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertTrue(payload["preflight_only"])
        self.assertGreaterEqual(len(payload["blockers"]), 1)

    def test_refuses_preexisting_volume_without_invoking_down_volumes(self) -> None:
        daemon_ok, _ = smoke.check_docker_daemon()
        if not daemon_ok:
            self.skipTest("Docker daemon unavailable")

        run_id = "utvolguard99"
        vol_name = f"crm-cskh-p4-smoke-{run_id}_sites"
        subprocess.run(["docker", "volume", "create", vol_name], check=True, capture_output=True)
        try:
            res = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(RUNNER_PATH),
                    "--run-id",
                    run_id,
                    "--preflight-only",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(res.returncode, 3)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["status"], "REFUSED_PREEXISTING_RESOURCES")
            self.assertFalse(payload["cleanup_invoked"])
            self.assertIn(vol_name, payload["preexisting_resources"]["volumes"])

            # Verify pre-existing volume was NOT removed by any cleanup handler
            inspect_res = subprocess.run(
                ["docker", "volume", "inspect", vol_name],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(inspect_res.returncode, 0)
        finally:
            subprocess.run(["docker", "volume", "rm", vol_name], check=False, capture_output=True)

    def test_refuses_occupied_loopback_port(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            occupied_port = srv.getsockname()[1]

            res = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(RUNNER_PATH),
                    "--http-port",
                    str(occupied_port),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(res.returncode, 3)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["status"], "REFUSED_PREEXISTING_RESOURCES")
            self.assertFalse(payload["cleanup_invoked"])
            self.assertFalse(payload["port_check"]["free"])

    def test_secret_scrubber_redacts_registered_and_pattern_secrets(self) -> None:
        logger = smoke.SmokeLogger()
        logger.register_secret("SuperSecretDbPw123!", "<REDACTED_DB_ROOT_PASSWORD>")
        logger.register_secret("SmokeP4#abcdef0123456789", "<REDACTED_ADMIN_PASSWORD>")
        logger.info(
            'boot db=SuperSecretDbPw123! adm=SmokeP4#abcdef0123456789 '
            'Cookie: sid=0123456789abcdef0123456789abcdef '
            '{"X-Frappe-CSRF-Token": "deadbeefcafe1234567890", "encryption_key": "k_secret_val"}'
        )

        with tempfile.TemporaryDirectory() as td:
            rep_path = pathlib.Path(td) / "report.json"
            log_path = pathlib.Path(td) / "smoke.log"
            smoke.write_scrubbed_artifacts(
                {"note": "adm=SmokeP4#abcdef0123456789"},
                logger,
                rep_path,
                log_path,
            )
            rep_txt = rep_path.read_text(encoding="utf-8")
            log_txt = log_path.read_text(encoding="utf-8")

        for forbidden in (
            "SuperSecretDbPw123!",
            "SmokeP4#abcdef0123456789",
            "0123456789abcdef0123456789abcdef",
            "deadbeefcafe1234567890",
            "k_secret_val",
        ):
            self.assertNotIn(forbidden, rep_txt)
            self.assertNotIn(forbidden, log_txt)
        self.assertIn("<REDACTED_ADMIN_PASSWORD>", rep_txt)
        self.assertIn("<REDACTED_DB_ROOT_PASSWORD>", log_txt)
        self.assertIn("sid=<REDACTED_SID>", log_txt)

    def test_db_root_password_file_lf_only_bytes_and_rejects_crlf(self) -> None:
        sample_pw = "DbSecretWinLfCheck_987654321"
        with tempfile.TemporaryDirectory() as td:
            sec_file = pathlib.Path(td) / "db_root_password.txt"
            info = smoke.write_lf_secret_file(sec_file, sample_pw)
            raw = sec_file.read_bytes()

            self.assertEqual(raw, sample_pw.encode("utf-8") + b"\n")
            self.assertTrue(raw.endswith(b"\n"))
            self.assertFalse(raw.endswith(b"\r\n"))
            self.assertNotIn(b"\r", raw)
            self.assertTrue(info["ends_with_lf_only"])
            self.assertFalse(info["contains_cr"])

            # Verify CRLF bytes are explicitly rejected
            sec_file.write_bytes(sample_pw.encode("utf-8") + b"\r\n")
            with self.assertRaises(AssertionError):
                smoke.verify_lf_secret_file_bytes(sec_file, sample_pw)

    def test_runner_preflight_verifies_lf_secret_and_log_contains_no_password(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rep_path = pathlib.Path(td) / "preflight.json"
            log_path = pathlib.Path(td) / "preflight.log"
            res = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(RUNNER_PATH),
                    "--preflight-only",
                    "--report-out",
                    str(rep_path),
                    "--log-out",
                    str(log_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(res.returncode, 0)
            rep_bytes = rep_path.read_bytes()
            log_bytes = log_path.read_bytes()
            self.assertNotIn(b"\r", rep_bytes)
            self.assertNotIn(b"\r", log_bytes)
            self.assertTrue(rep_bytes.endswith(b"\n"))
            self.assertTrue(log_bytes.endswith(b"\n"))

            payload = json.loads(rep_bytes.decode("utf-8"))
            self.assertTrue(payload["secret_file_byte_check"]["ends_with_lf_only"])
            self.assertFalse(payload["secret_file_byte_check"]["contains_cr"])

            log_text = log_bytes.decode("utf-8")
            self.assertNotIn("SmokeP4#", log_text)
            self.assertNotIn("SmokeP4#", rep_bytes.decode("utf-8"))
            self.assertIn("ends_with_lf_only=True, contains_cr=False", log_text)

    def test_parse_sid_cookie_attributes_multi_cookie_frappe_order_and_masking(self) -> None:
        raw_sid = "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbcc"
        headers = [
            f"sid={raw_sid}; Expires=Tue, 10 Nov 2026 08:00:00 GMT; HttpOnly; Path=/; SameSite=Lax",
            "system_user=yes; Path=/; SameSite=Lax",
            "full_name=Administrator; Path=/; SameSite=Lax",
            "user_id=Administrator; Path=/; SameSite=Lax",
            "user_image=; Path=/; SameSite=Lax",
        ]
        logger = smoke.SmokeLogger()
        attrs = smoke.parse_sid_cookie_attributes(headers, logger=logger)

        self.assertTrue(attrs["httponly"])
        self.assertEqual(attrs["samesite"], "Lax")
        self.assertEqual(attrs["path"], "/")
        self.assertTrue(attrs["has_expires"])
        self.assertFalse(attrs["secure"])
        self.assertEqual(attrs["secure_assertion_scope"], "DEFERRED_TO_STAGING_HTTPS")
        self.assertEqual(attrs["set_cookie_header_count"], 5)
        self.assertEqual(
            attrs["response_cookie_names"],
            ["sid", "system_user", "full_name", "user_id", "user_image"],
        )

        serialized = json.dumps(attrs)
        self.assertNotIn(raw_sid, serialized)
        self.assertIn("<REDACTED_FRAPPE_SID>", serialized)

        logger.info(f"debug leak check {raw_sid}")
        self.assertNotIn(raw_sid, logger.render())

    def test_parse_sid_cookie_attributes_does_not_leak_flags_from_other_cookies(self) -> None:
        raw_sid = "sid_without_httponly_or_secure_token_123456"
        headers = [
            "csrf_aux=1; HttpOnly; Secure; Path=/; SameSite=Strict",
            f"sid={raw_sid}; Path=/; SameSite=Lax",
            "trailing_cookie=yes; HttpOnly; Secure; Path=/; SameSite=Strict",
        ]
        attrs = smoke.parse_sid_cookie_attributes(headers)
        self.assertFalse(attrs["httponly"])
        self.assertFalse(attrs["secure"])
        self.assertEqual(attrs["samesite"], "Lax")
        self.assertNotIn(raw_sid, json.dumps(attrs))

    def test_http_call_with_cookies_preserves_multiple_set_cookie_headers_live(self) -> None:
        raw_sid = "f0e1d2c3b4a5968778695a4b3c2d1e0f9988776655443322"

        class MultiCookieHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                req_len = int(self.headers.get("Content-Length", "0"))
                if req_len:
                    self.rfile.read(req_len)
                payload = b'{"message":"Logged In"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header(
                    "Set-Cookie",
                    f"sid={raw_sid}; Expires=Tue, 10 Nov 2026 08:00:00 GMT; HttpOnly; Path=/; SameSite=Lax",
                )
                self.send_header("Set-Cookie", "system_user=yes; Path=/; SameSite=Lax")
                self.send_header("Set-Cookie", "full_name=Administrator; Path=/; SameSite=Lax")
                self.send_header("Set-Cookie", "user_id=Administrator; Path=/; SameSite=Lax")
                self.send_header("Set-Cookie", "user_image=; Path=/; SameSite=Lax")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        server = http.server.HTTPServer(("127.0.0.1", 0), MultiCookieHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _, opener = smoke.make_http_client()
            code, _, set_cookies, _ = smoke.http_call_with_cookies(
                opener,
                f"http://127.0.0.1:{port}/api/method/login",
                "p4-smoke-ut.localhost",
                method="POST",
                data=b"usr=Administrator&pwd=x",
            )
            self.assertEqual(code, 200)
            self.assertEqual(len(set_cookies), 5)
            attrs = smoke.parse_sid_cookie_attributes(set_cookies)
            self.assertTrue(attrs["httponly"])
            self.assertEqual(attrs["samesite"], "Lax")
            self.assertFalse(attrs["secure"])
        finally:
            server.shutdown()
            server.server_close()

    def test_validate_compose_config_enforces_apps_order_and_named_logs_volume(self) -> None:
        validator = SCRIPTS_DIR / "validate_compose_config.py"
        res = subprocess.run(
            [sys.executable, "-X", "utf8", str(validator)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        payload = json.loads(res.stdout)
        self.assertEqual(payload["status"], "PASS")
        for stack_name, expected_proj in (
            ("production", "crm-cskh-prod"),
            ("staging", "crm-cskh-staging"),
            ("isolated-smoke", "crm-cskh-p4-smoke"),
        ):
            st = payload["stacks"][stack_name]
            self.assertTrue(st["apps_order_guard_verified"])
            self.assertEqual(st["volumes"]["logs"], f"{expected_proj}_logs")
            self.assertEqual(
                st["app_services_mounting_named_logs_volume"],
                [
                    "backend",
                    "configurator",
                    "frontend",
                    "migrator",
                    "queue-long",
                    "queue-short",
                    "scheduler",
                    "websocket",
                ],
            )


if __name__ == "__main__":
    unittest.main()
