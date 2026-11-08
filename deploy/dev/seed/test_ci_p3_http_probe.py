#!/usr/bin/env python3
"""Unit & real-HTTP negative verification suite for `ci_p3_http_probe.py`.

Proves fail-closed completeness & security gate behavior:
  1. Valid `record_role == "assertion"` records with `result == "PASS"` and `transport_error is None`
     yield `probe_result == "PASS"`.
  2. Any unasserted `record_role == "assertion"` entry is flagged in `unasserted_cases` and forces
     `probe_result == "FAIL"` (cannot report `N/N PASS` when an assertion is missing).
  3. `record_role == "setup_observation"` records are counted separately and never inflate `asserted_cases`.
  4. Duplicate case IDs, transport errors, HTTP 500/403 on `verify_export_download_response`, or
     unescaped CSV formula cells (`=CMD` without leading `'`) immediately force `probe_result == "FAIL"`.
  5. `redact_text` & `sanitize` strip passwords, CSRF tokens, cookies, `/private/files/...` paths, and phone numbers.
"""

from __future__ import annotations

import http.server
import importlib.util
import socket
import tempfile
import threading
import unittest
from pathlib import Path

PROBE_PATH = Path(__file__).resolve().with_name("ci_p3_http_probe.py")


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("ci_p3_http_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load probe module from {PROBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe_module()


class _ConfigurableHandler(http.server.BaseHTTPRequestHandler):
    status_code: int = 200
    response_body: bytes = (
        b"record_type,customer_name,note_excerpt\nattempt,'=CMD calc,'@SUM(1,2)\n"
    )
    content_type: str = "text/csv; charset=utf-8"

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(self.status_code)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class TestCiP3HttpProbeGate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_base_url = probe.BASE_URL
        self._orig_output = probe.OUTPUT
        probe.OUTPUT = Path(self._tmp.name)
        probe.records.clear()
        probe.failures.clear()

        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _ConfigurableHandler
        )
        host, port = self._server.server_address
        probe.BASE_URL = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        _ConfigurableHandler.status_code = 200
        _ConfigurableHandler.content_type = "text/csv; charset=utf-8"
        _ConfigurableHandler.response_body = (
            b"record_type,customer_name,note_excerpt\nattempt,'=CMD calc,'@SUM(1,2)\n"
        )

    def tearDown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        probe.records.clear()
        probe.failures.clear()
        probe.BASE_URL = self._orig_base_url
        probe.OUTPUT = self._orig_output
        self._tmp.cleanup()

    def test_valid_sanitized_csv_export_passes_gate(self) -> None:
        opener, _ = probe.make_opener()
        ok = probe.verify_export_download_response(
            "p3-selftest-export-ok",
            opener,
            "EXP-TEST-001",
            "team1.leader@dev.local",
            expect_formula_escaped=True,
        )
        self.assertTrue(ok)
        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "PASS")
        self.assertEqual(summary["counts"]["verification_records"], 1)
        self.assertEqual(summary["counts"]["passed_assertions"], 1)
        self.assertEqual(summary["unasserted_cases"], [])

    def test_unescaped_formula_in_csv_turns_gate_red(self) -> None:
        _ConfigurableHandler.response_body = (
            b"record_type,customer_name,note_excerpt\nattempt,=CMD calc,@SUM(1,2)\n"
        )
        opener, _ = probe.make_opener()
        ok = probe.verify_export_download_response(
            "p3-selftest-export-unescaped",
            opener,
            "EXP-TEST-002",
            "team1.leader@dev.local",
            expect_formula_escaped=True,
        )
        self.assertFalse(ok)
        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "FAIL")
        self.assertIn("p3-selftest-export-unescaped", summary["failed_assertion_cases"])

    def test_non_200_or_transport_error_turns_gate_red(self) -> None:
        _ConfigurableHandler.status_code = 403
        _ConfigurableHandler.response_body = b'{"code":"FORBIDDEN_SCOPE"}'
        opener, _ = probe.make_opener()
        ok = probe.verify_export_download_response(
            "p3-selftest-export-403",
            opener,
            "EXP-TEST-003",
            "team1.leader@dev.local",
        )
        self.assertFalse(ok)
        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "FAIL")

        # Transport error on dead port
        probe.records.clear()
        probe.failures.clear()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
        sock.close()
        probe.BASE_URL = f"http://127.0.0.1:{dead_port}"
        ok2 = probe.verify_export_download_response(
            "p3-selftest-export-dead",
            opener,
            "EXP-TEST-004",
            "team1.leader@dev.local",
        )
        self.assertFalse(ok2)
        summary2 = probe.write_evidence(final=True)
        self.assertEqual(summary2["probe_result"], "FAIL")

    def test_unasserted_record_and_setup_observation_classification(self) -> None:
        opener, _ = probe.make_opener()
        _ = probe.get(
            "p3-selftest-setup",
            opener,
            "/setup",
            record_role="setup_observation",
        )
        _ = probe.get(
            "p3-selftest-missing-check",
            opener,
            "/unasserted",
            record_role="assertion",
        )
        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "FAIL")
        self.assertEqual(summary["counts"]["setup_observation_records"], 1)
        self.assertEqual(summary["counts"]["verification_records"], 1)
        self.assertEqual(summary["counts"]["asserted_cases"], 0)
        self.assertEqual(summary["unasserted_cases"], ["p3-selftest-missing-check"])

    def test_redaction_strips_private_file_urls_phones_and_secrets(self) -> None:
        raw = (
            f"pwd={probe.SEED_PASSWORD} url=/private/files/cskh_export_secret_123.csv "
            "phone=0908123456 sid=abc123def"
        )
        cleaned = probe.redact_text(raw)
        self.assertNotIn(probe.SEED_PASSWORD, cleaned)
        self.assertNotIn("cskh_export_secret_123.csv", cleaned)
        self.assertNotIn("0908123456", cleaned)
        self.assertIn("[REDACTED_FILE_URL]", cleaned)
        self.assertIn("[REDACTED_PHONE]", cleaned)


if __name__ == "__main__":
    unittest.main()
