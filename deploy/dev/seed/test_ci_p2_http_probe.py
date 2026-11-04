#!/usr/bin/env python3
"""Unit & real-HTTP negative verification suite for `ci_p2_http_probe.py`.

Proves that:
  1. `p2-http-staff-b-close-wi-final` checks HTTP 200, full API contract (§4.10),
     and MariaDB post-close invariants (Work Item `closed`, 0 open callbacks,
     0 active claims, 0 open ownership intervals, `lead_owner` & `ToDo` cleared).
  2. Any transport error, non-200 HTTP status, deceptive HTTP 200 payload, or
     DB invariant violation on `close_work_item` turns the probe gate RED (`FAIL`).
  3. Any `record_role == "assertion"` record lacking a valid `check()` verdict
     (such as the 60/61 historical gap) is flagged in `unasserted_cases` and
     turns the probe gate RED (`FAIL`).
  4. `record_role == "setup_observation"` records are classified and counted
     separately and never inflate `asserted_cases` or `passed_assertions`.
"""

from __future__ import annotations

import ast
import copy
import http.server
import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

PROBE_PATH = Path(__file__).resolve().with_name("ci_p2_http_probe.py")


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("ci_p2_http_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load probe module from {PROBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_probe_module()


def _valid_close_http_envelope(
    *,
    work_item: str = "WI-TEST-0001",
    actor: str = "staff.b@dev.local",
    revision: int = 6,
) -> dict[str, object]:
    return {
        "message": {
            "status": "closed",
            "idempotent_replay": False,
            "work_item": work_item,
            "work_item_status": "closed",
            "closed_at": "2026-11-04 09:00:00",
            "closed_by": actor,
            "canceled_followup_task": None,
            "revision": revision,
        }
    }


def _valid_closed_db_state(
    *,
    work_item: str = "WI-TEST-0001",
    lead: str = "CRM-LEAD-0001",
    actor: str = "staff.b@dev.local",
    reason: str = "Kết thúc tại Team 1",
    revision: int = 6,
) -> dict[str, object]:
    return {
        "work_item": {
            "name": work_item,
            "lead": lead,
            "status": "closed",
            "assigned_to": None,
            "assigned_at": None,
            "open_callback_task": None,
            "next_action_due": None,
            "closed_at": "2026-11-04 09:00:00",
            "closed_by": actor,
            "completion_reason": reason,
            "revision": revision,
        },
        "open_callback_count": 0,
        "active_claim_for_work_item": 0,
        "active_claim_for_lead": 0,
        "open_interval_count": 0,
        "close_events": [
            {
                "actor": actor,
                "ownership_start": None,
                "ownership_end": "2026-11-04 09:00:00",
            }
        ],
        "lead_owner": None,
        "open_lead_todo_count": 0,
    }


class _ConfigurableHandler(http.server.BaseHTTPRequestHandler):
    status_code: int = 200
    response_body: bytes = b"{}"
    content_type: str = "application/json"

    def do_GET(self) -> None:  # noqa: N802
        self._respond()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 0:
            _ = self.rfile.read(length)
        self._respond()

    def _respond(self) -> None:
        self.send_response(self.status_code)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class TestCiP2HttpProbeGate(unittest.TestCase):
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
        self._set_http_json(200, _valid_close_http_envelope())

    def tearDown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        probe.records.clear()
        probe.failures.clear()
        probe.BASE_URL = self._orig_base_url
        probe.OUTPUT = self._orig_output
        self._tmp.cleanup()

    def _set_http_json(self, status_code: int, body: object) -> None:
        _ConfigurableHandler.status_code = status_code
        _ConfigurableHandler.content_type = "application/json"
        _ConfigurableHandler.response_body = json.dumps(
            body, ensure_ascii=False
        ).encode("utf-8")

    def _reset_probe_buffers(self) -> None:
        probe.records.clear()
        probe.failures.clear()

    def _read_artifact_json(self) -> dict[str, object]:
        return json.loads(
            (probe.OUTPUT / "p2-http-transport.json").read_text(encoding="utf-8")
        )

    def test_positive_final_close_contract_and_db_invariants_pass_gate(self) -> None:
        opener, _ = probe.make_opener()
        db_state = _valid_closed_db_state(revision=6)
        resp = probe.run_close_work_item_case(
            "p2-http-staff-b-close-wi-final",
            opener,
            "csrf-tok",
            work_item="WI-TEST-0001",
            lead="CRM-LEAD-0001",
            actor="staff.b@dev.local",
            completion_reason="Kết thúc tại Team 1",
            idempotency_key="close-pos-1",
            db_state_fetcher=lambda _wi, _lead: db_state,
            min_revision=6,
        )
        self.assertEqual(resp["status"], 200)
        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "PASS")
        self.assertEqual(
            summary["counts"],
            {
                "total_records": 1,
                "verification_records": 1,
                "setup_observation_records": 0,
                "asserted_cases": 1,
                "unasserted_cases": 0,
                "passed_assertions": 1,
                "failed_assertions": 0,
            },
        )
        artifact = self._read_artifact_json()
        self.assertEqual(artifact["probe_result"], "PASS")
        self.assertEqual(artifact["unasserted_cases"], [])
        self.assertEqual(artifact["failed_cases"], [])
        self.assertEqual(artifact["gate_failures"], [])

    def test_negative_http_error_status_on_close_turns_gate_red(self) -> None:
        opener, _ = probe.make_opener()
        for bad_status, err_body in (
            (
                409,
                {
                    "ok": False,
                    "code": "WORK_ITEM_NOT_ACTIVE",
                    "message": "Chỉ Work Item đang hoạt động mới có thể đóng.",
                },
            ),
            (
                403,
                {
                    "ok": False,
                    "code": "FORBIDDEN",
                    "message": "Không có quyền.",
                },
            ),
            (
                500,
                {
                    "exc_type": "InternalServerError",
                    "message": "Database error",
                },
            ),
        ):
            with self.subTest(http_status=bad_status):
                self._reset_probe_buffers()
                self._set_http_json(bad_status, err_body)
                probe.run_close_work_item_case(
                    "p2-http-staff-b-close-wi-final",
                    opener,
                    "csrf-tok",
                    work_item="WI-TEST-0001",
                    lead="CRM-LEAD-0001",
                    actor="staff.b@dev.local",
                    completion_reason="Kết thúc tại Team 1",
                    idempotency_key=f"close-neg-{bad_status}",
                    db_state_fetcher=lambda _wi, _lead: _valid_closed_db_state(),
                    min_revision=6,
                )
                summary = probe.write_evidence(final=True)
                self.assertEqual(summary["probe_result"], "FAIL")
                self.assertEqual(summary["counts"]["failed_assertions"], 1)
                self.assertIn(
                    "p2-http-staff-b-close-wi-final",
                    summary["failed_assertion_cases"],
                )
                artifact = self._read_artifact_json()
                self.assertEqual(artifact["probe_result"], "FAIL")
                self.assertEqual(
                    artifact["cases"][0]["assertion"]["result"],
                    "FAIL",
                )

    def test_negative_deceptive_http_200_non_closed_payload_turns_gate_red(
        self,
    ) -> None:
        opener, _ = probe.make_opener()
        mutations: list[tuple[str, object]] = [
            ("missing_message_envelope", {"status": "closed"}),
            (
                "status_not_closed",
                {
                    "message": {
                        **_valid_close_http_envelope()["message"],
                        "status": "ok",
                    }
                },  # type: ignore[index]
            ),
            (
                "work_item_status_in_progress",
                {
                    "message": {
                        **_valid_close_http_envelope()["message"],  # type: ignore[index]
                        "work_item_status": "in_progress",
                    }
                },
            ),
            (
                "unexpected_idempotent_replay",
                {
                    "message": {
                        **_valid_close_http_envelope()["message"],  # type: ignore[index]
                        "idempotent_replay": True,
                    }
                },
            ),
            (
                "wrong_work_item_id",
                {
                    "message": {
                        **_valid_close_http_envelope()["message"],  # type: ignore[index]
                        "work_item": "WI-OTHER-9999",
                    }
                },
            ),
            (
                "wrong_closed_by",
                {
                    "message": {
                        **_valid_close_http_envelope()["message"],  # type: ignore[index]
                        "closed_by": "staff.a@dev.local",
                    }
                },
            ),
            (
                "empty_closed_at",
                {
                    "message": {
                        **_valid_close_http_envelope()["message"],  # type: ignore[index]
                        "closed_at": "",
                    }
                },
            ),
            (
                "stale_revision_not_incremented",
                {
                    "message": {
                        **_valid_close_http_envelope(revision=4)["message"],  # type: ignore[index]
                    }
                },
            ),
        ]
        for label, bad_payload in mutations:
            with self.subTest(mutation=label):
                self._reset_probe_buffers()
                self._set_http_json(200, bad_payload)
                probe.run_close_work_item_case(
                    "p2-http-staff-b-close-wi-final",
                    opener,
                    "csrf-tok",
                    work_item="WI-TEST-0001",
                    lead="CRM-LEAD-0001",
                    actor="staff.b@dev.local",
                    completion_reason="Kết thúc tại Team 1",
                    idempotency_key=f"close-payload-{label}",
                    db_state_fetcher=lambda _wi, _lead: _valid_closed_db_state(
                        revision=6
                    ),
                    min_revision=6,
                )
                summary = probe.write_evidence(final=True)
                self.assertEqual(summary["probe_result"], "FAIL")
                self.assertEqual(summary["counts"]["failed_assertions"], 1)

    def test_negative_transport_error_on_close_turns_gate_red(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            closed_port = sock.getsockname()[1]
        probe.BASE_URL = f"http://127.0.0.1:{closed_port}"

        opener, _ = probe.make_opener()
        resp = probe.run_close_work_item_case(
            "p2-http-staff-b-close-wi-final",
            opener,
            "csrf-tok",
            work_item="WI-TEST-0001",
            lead="CRM-LEAD-0001",
            actor="staff.b@dev.local",
            completion_reason="Kết thúc tại Team 1",
            idempotency_key="close-transport-err",
            db_state_fetcher=lambda _wi, _lead: _valid_closed_db_state(),
            min_revision=6,
        )
        self.assertIsNotNone(resp["transport_error"])
        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "FAIL")
        self.assertEqual(summary["counts"]["failed_assertions"], 1)
        self.assertIn(
            "p2-http-staff-b-close-wi-final", summary["failed_assertion_cases"]
        )

    def test_negative_db_invariant_violations_turn_gate_red_despite_http_200(
        self,
    ) -> None:
        opener, _ = probe.make_opener()
        self._set_http_json(200, _valid_close_http_envelope(revision=6))

        def mutate_wi(field: str, value: object) -> dict[str, object]:
            state = copy.deepcopy(_valid_closed_db_state(revision=6))
            assert isinstance(state["work_item"], dict)
            state["work_item"][field] = value
            return state

        def mutate_top(field: str, value: object) -> dict[str, object]:
            state = copy.deepcopy(_valid_closed_db_state(revision=6))
            state[field] = value
            return state

        violations: list[tuple[str, dict[str, object]]] = [
            ("wi_still_in_progress", mutate_wi("status", "in_progress")),
            (
                "wi_assigned_to_not_cleared",
                mutate_wi("assigned_to", "staff.b@dev.local"),
            ),
            (
                "wi_assigned_at_not_cleared",
                mutate_wi("assigned_at", "2026-11-04 08:00:00"),
            ),
            (
                "wi_open_callback_pointer_left",
                mutate_wi("open_callback_task", "TASK-01"),
            ),
            (
                "wi_next_action_due_left",
                mutate_wi("next_action_due", "2026-11-05 09:00:00"),
            ),
            ("wi_closed_by_mismatch", mutate_wi("closed_by", "staff.a@dev.local")),
            ("wi_closed_at_null", mutate_wi("closed_at", None)),
            (
                "wi_completion_reason_mismatch",
                mutate_wi("completion_reason", "Lý do khác"),
            ),
            ("wi_revision_mismatch", mutate_wi("revision", 99)),
            ("leftover_open_callback_task", mutate_top("open_callback_count", 1)),
            (
                "leftover_active_claim_on_wi",
                mutate_top("active_claim_for_work_item", 1),
            ),
            ("leftover_active_claim_on_lead", mutate_top("active_claim_for_lead", 1)),
            ("leftover_open_ownership_interval", mutate_top("open_interval_count", 1)),
            ("missing_close_history_event", mutate_top("close_events", [])),
            ("lead_owner_not_cleared", mutate_top("lead_owner", "staff.b@dev.local")),
            ("open_todo_not_cleared", mutate_top("open_lead_todo_count", 1)),
        ]
        for label, bad_db_state in violations:
            with self.subTest(db_violation=label):
                self._reset_probe_buffers()
                probe.run_close_work_item_case(
                    "p2-http-staff-b-close-wi-final",
                    opener,
                    "csrf-tok",
                    work_item="WI-TEST-0001",
                    lead="CRM-LEAD-0001",
                    actor="staff.b@dev.local",
                    completion_reason="Kết thúc tại Team 1",
                    idempotency_key=f"close-db-{label}",
                    db_state_fetcher=lambda _wi, _lead, st=bad_db_state: st,
                    min_revision=6,
                )
                summary = probe.write_evidence(final=True)
                self.assertEqual(summary["probe_result"], "FAIL")
                self.assertEqual(summary["counts"]["failed_assertions"], 1)

        with self.subTest(db_violation="db_fetcher_exception"):
            self._reset_probe_buffers()

            def _raise_db_err(_wi: str, _lead: str) -> dict[str, object]:
                raise RuntimeError("MariaDB connection lost")

            probe.run_close_work_item_case(
                "p2-http-staff-b-close-wi-final",
                opener,
                "csrf-tok",
                work_item="WI-TEST-0001",
                lead="CRM-LEAD-0001",
                actor="staff.b@dev.local",
                completion_reason="Kết thúc tại Team 1",
                idempotency_key="close-db-exc",
                db_state_fetcher=_raise_db_err,
                min_revision=6,
            )
            summary = probe.write_evidence(final=True)
            self.assertEqual(summary["probe_result"], "FAIL")
            self.assertEqual(summary["counts"]["failed_assertions"], 1)

    def test_negative_unasserted_record_reproducing_60_of_61_gap_turns_gate_red(
        self,
    ) -> None:
        opener, _ = probe.make_opener()
        self._set_http_json(200, _valid_close_http_envelope())

        for idx in range(60):
            cid = f"p2-http-asserted-{idx:02d}"
            r = probe.get(cid, opener, "/api/method/ping")
            probe.check(cid, r, r["status"] == 200, "HTTP 200", "staff.a@dev.local")

        # Exact reproduction of the historical gap: 61st HTTP 200 record WITHOUT check()
        probe.post_json(
            "p2-http-staff-b-close-wi-final",
            opener,
            "csrf-tok",
            "/api/method/crm_cskh.api.workflow.close_work_item",
            {"work_item": "WI-TEST-0001", "completion_reason": "Kết thúc tại Team 1"},
        )

        summary = probe.write_evidence(final=True)
        self.assertEqual(summary["probe_result"], "FAIL")
        self.assertEqual(
            summary["counts"],
            {
                "total_records": 61,
                "verification_records": 61,
                "setup_observation_records": 0,
                "asserted_cases": 60,
                "unasserted_cases": 1,
                "passed_assertions": 60,
                "failed_assertions": 0,
            },
        )
        self.assertEqual(
            summary["unasserted_cases"], ["p2-http-staff-b-close-wi-final"]
        )
        self.assertIn(
            "unasserted:p2-http-staff-b-close-wi-final", summary["gate_failures"]
        )
        artifact = self._read_artifact_json()
        self.assertEqual(artifact["probe_result"], "FAIL")

    def test_setup_observation_records_counted_separately_and_never_inflate_assertions(
        self,
    ) -> None:
        opener, _ = probe.make_opener()
        setup_resp = probe.get(
            "p2-http-setup-warmup",
            opener,
            "/api/method/ping",
            record_role="setup_observation",
        )
        with self.assertRaises(ValueError):
            probe.check(
                "p2-http-setup-warmup",
                setup_resp,
                True,
                "Must not allow check() on setup_observation",
            )

        # Only setup records and 0 verification assertions -> MUST FAIL (no_asserted_verification_cases)
        only_setup_summary = probe.write_evidence(final=True)
        self.assertEqual(only_setup_summary["probe_result"], "FAIL")
        self.assertIn(
            "no_asserted_verification_cases", only_setup_summary["gate_failures"]
        )

        # Add 2 real asserted verification cases -> total_records=3, asserted_cases=2, passed_assertions=2
        for idx in (1, 2):
            cid = f"p2-http-verify-{idx}"
            r = probe.get(cid, opener, "/api/method/ping", record_role="assertion")
            probe.check(cid, r, r["status"] == 200, "HTTP 200", "staff.a@dev.local")

        mixed_summary = probe.write_evidence(final=True)
        self.assertEqual(mixed_summary["probe_result"], "PASS")
        self.assertEqual(
            mixed_summary["counts"],
            {
                "total_records": 3,
                "verification_records": 2,
                "setup_observation_records": 1,
                "asserted_cases": 2,
                "unasserted_cases": 0,
                "passed_assertions": 2,
                "failed_assertions": 0,
            },
        )

    def test_intermediate_write_evidence_marks_in_progress_until_finalized(
        self,
    ) -> None:
        opener, _ = probe.make_opener()
        r = probe.get("p2-http-mid-run", opener, "/api/method/ping")
        intermediate = self._read_artifact_json()
        self.assertEqual(intermediate["probe_result"], "IN_PROGRESS")

        probe.check("p2-http-mid-run", r, r["status"] == 200, "HTTP 200")
        final_summary = probe.write_evidence(final=True)
        self.assertEqual(final_summary["probe_result"], "PASS")
        self.assertEqual(self._read_artifact_json()["probe_result"], "PASS")

    def test_source_static_audit_no_discarded_http_calls_in_main(self) -> None:
        source = PROBE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(PROBE_PATH))
        main_fn = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main"
            ),
            None,
        )
        self.assertIsNotNone(
            main_fn, "main() function must exist in ci_p2_http_probe.py"
        )
        assert isinstance(main_fn, ast.FunctionDef)

        raw_http_helpers = {"request", "get", "post", "post_json"}
        discarded_calls: list[int] = []
        close_helper_case_ids: list[str] = []

        for node in ast.walk(main_fn):
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in raw_http_helpers
            ):
                discarded_calls.append(node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "run_close_work_item_case"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                close_helper_case_ids.append(node.args[0].value)

        self.assertEqual(
            discarded_calls,
            [],
            f"Bare uncaptured HTTP helper calls found in main() at lines: {discarded_calls}",
        )
        self.assertIn("p2-http-staff-b-close-wi", close_helper_case_ids)
        self.assertIn("p2-http-staff-b-close-wi-final", close_helper_case_ids)


if __name__ == "__main__":
    unittest.main()
