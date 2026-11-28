#!/usr/bin/env python3
"""Comprehensive verification suite for `F-G18-RUNNER-4X4-CGROUP-01` & G18 config sync."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

RELEASE_DIR = Path(__file__).resolve().parent
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

import benchmark_p4_isolated as bench  # noqa: E402
import diagnose_g18_drilldown as diag_drill  # noqa: E402
import g18_resource_profiles as g18_prof  # noqa: E402
import reconcile_t1a_ab_2x2_diag as rec_ab  # noqa: E402
from test_benchmark_p4_isolated import (  # noqa: E402
    _handle_mock_runtime_topology_subproc,
    _make_synthetic_s1_and_http,
)

LOCKED_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "07c93a9f94b9fc2a7d98ee675a65e852ef49067645459ba99e4b74b01282f1ca"
)


def _make_valid_runtime_snapshot(profile_key: str = "staging_vps_capped") -> dict:
    spec = g18_prof.get_g18_resource_profile_spec(profile_key)
    limits_9 = spec["verify_9_service_limits"]
    gw = int(spec["gunicorn_workers"])
    gt = int(spec["gunicorn_threads"])

    cgroups = {
        svc: {
            "container_id": f"cid-{svc}",
            "container_name": f"cskh-p4-bench-{svc}-1",
            "running": True,
            "cpus_limit": float(limits_9[svc]["cpus"]),
            "memory_limit_mib": float(limits_9[svc]["memory_limit_mib"]),
            "cpu_shares": 512 if svc == "queue-short" else 1024,
            "oom_killed": False,
            "restart_count": 0,
            "cmd": [svc],
        }
        for svc in g18_prof.VERIFY_DAEMON_SERVICES
    }

    workers = {
        "backend": {
            "worker_model": "gunicorn_gthread",
            "env_gunicorn_workers": gw,
            "env_gunicorn_threads": gt,
            "env_gunicorn_timeout": "120",
            "cgroup_cpu_max_raw": f"{int(float(spec['backend_cpus']) * 100000)} 100000",
            "cgroup_cpus": float(spec["backend_cpus"]),
            "cgroup_memory_max_raw": str(
                int(float(spec["backend_mem_mib"]) * 1024 * 1024)
            ),
            "cgroup_mem_mib": float(spec["backend_mem_mib"]),
            "gunicorn_master_pid": 1,
            "gunicorn_worker_pids": list(range(11, 11 + gw)),
            "live_gunicorn_worker_count": gw,
            "os_threads_per_worker": [gt] * gw,
            "effective_total_web_threads": gw * gt,
        },
        "queue-short": {
            "worker_model": "rq_worker",
            "probe_ok": True,
            "live_process_count": 1,
            "registered_rq_worker_count": 1,
        },
        "queue-long": {
            "worker_model": "rq_worker",
            "probe_ok": True,
            "live_process_count": 1,
            "registered_rq_worker_count": 1,
        },
    }
    for svc in (
        "frontend",
        "websocket",
        "scheduler",
        "db",
        "redis-cache",
        "redis-queue",
    ):
        workers[svc] = {
            "worker_model": "daemon",
            "probe_ok": True,
            "live_process_count": 2 if svc == "frontend" else 1,
        }

    return {
        "schema_version": "cskh-p4-pre-acceptance-runtime-snapshot-v1",
        "captured_at_utc": "2026-11-20T02:00:00Z",
        "profile_key": profile_key,
        "profile_id": spec["profile_id"],
        "config_spec_sha256": spec["config_spec_sha256"],
        "verify_9_service_config_spec_sha256": spec[
            "verify_9_service_config_spec_sha256"
        ],
        "cgroup_container_inspection": cgroups,
        "service_worker_inspection": workers,
        "mariadb_variables": {
            "innodb_buffer_pool_size": str(g18_prof.EXPECTED_INNODB_BUFFER_POOL_BYTES),
            "innodb_log_file_size": str(g18_prof.EXPECTED_INNODB_LOG_FILE_BYTES),
            "innodb_flush_log_at_trx_commit": "1",
        },
        "rq_workers": [
            {"name": "w-short", "normalized_queues": ["short", "default"]},
            {"name": "w-long", "normalized_queues": ["long"]},
        ],
    }


class TestFG18Runner4x4CgroupSync(unittest.TestCase):
    def test_canonical_hashes_and_zero_drift_across_runner_diag_and_ops_export(
        self,
    ) -> None:
        capped = g18_prof.get_g18_resource_profile_spec("staging_vps_capped")
        can4x4 = g18_prof.get_g18_resource_profile_spec("s1_canonical_4x4")
        b_plus = g18_prof.get_g18_resource_profile_spec(
            "staging_vps_capped_b_plus_2_5vcpu"
        )
        legacy_a = g18_prof.get_g18_resource_profile_spec(
            "staging_vps_capped_legacy_2x4"
        )

        self.assertEqual(
            capped["config_spec_sha256"],
            "7cbc282ff2c380b145a21c494dbf06c9249d08bd6ec75234f8f4dbc332ccc906",
        )
        self.assertEqual(
            capped["compose_env_overrides_sha256"],
            "9ce1ccf1f24f844da48296e6eeb1507ce28d55c819f0ec4fe19669b8c383afd7",
        )
        self.assertEqual(
            capped["verify_9_service_config_spec_sha256"],
            "d751ae742818b05f31968f1d2522e6e7d4509abaeeb420a6730da0bdc556e69d",
        )
        self.assertEqual(
            can4x4["config_spec_sha256"],
            "683bc830dc7542c381fe2fb9016389a1d919c2b518f211cfe3ff5de5ff8324fe",
        )
        self.assertEqual(
            b_plus["config_spec_sha256"],
            "530765c275067dc08bef704cc316c22683376c850b32cde5546e52b9c0d302a7",
        )
        self.assertEqual(
            b_plus["verify_9_service_config_spec_sha256"],
            "3aae6715d5e11b87ee86e4dc3ee9172434bfa0a73f4d510a04b5bcb407895589",
        )
        self.assertEqual(
            legacy_a["config_spec_sha256"],
            "31cf8567ff3b58168670a4f265b2194baf13203d6af27065ffc4ff1cd9eace2e",
        )

        # Runner & diagnose_g18_drilldown must use the exact same generated overrides
        self.assertEqual(
            bench.STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
            capped["verify_compose_override_yaml"],
        )
        self.assertEqual(
            bench.EPHEMERAL_BENCH_OVERRIDE_YAML,
            can4x4["verify_compose_override_yaml"],
        )
        self.assertEqual(
            diag_drill.STAGING_VPS_CAPPED_OVERRIDE_YAML,
            capped["verify_compose_override_yaml"],
        )
        self.assertEqual(
            diag_drill.S1_CANONICAL_OVERRIDE_YAML,
            can4x4["verify_compose_override_yaml"],
        )
        self.assertTrue(
            diag_drill.RESOURCE_PROFILES["s1_canonical_4x4"]["cgroup_capped"]
        )
        self.assertEqual(
            diag_drill.RESOURCE_PROFILES["staging_vps_capped"]["gunicorn_workers"], 4
        )

        # Ops export must hash identically under STAGING_CONFIG_ENVELOPE_KEYS
        ops_export = g18_prof.export_ops_staging_resource_profiles()
        for pid, exp_sha in (
            ("staging_vps_capped", capped["config_spec_sha256"]),
            ("s1_canonical_4x4", can4x4["config_spec_sha256"]),
        ):
            env = {k: ops_export[pid][k] for k in g18_prof.STAGING_CONFIG_ENVELOPE_KEYS}
            canon = (
                json.dumps(env, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            self.assertEqual(hashlib.sha256(canon).hexdigest(), exp_sha)

    def test_verify_9_vs_staging_10_service_mapping_reconciles_proxy_delta_exactly(
        self,
    ) -> None:
        for key in (
            "staging_vps_capped",
            "s1_canonical_4x4",
            "staging_vps_capped_b_plus_2_5vcpu",
        ):
            mapping = g18_prof.build_verify_9_to_staging_10_mapping(key)
            self.assertEqual(mapping["verify_daemon_service_count"], 9)
            self.assertEqual(mapping["staging_full_daemon_service_count"], 10)
            self.assertEqual(mapping["staging_extra_edge_service"], "proxy")
            self.assertTrue(mapping["delta_equals_proxy_budget_exactly"])
            for svc in g18_prof.VERIFY_DAEMON_SERVICES:
                self.assertEqual(mapping["services"][svc]["parity"], "IDENTICAL_1_TO_1")
            self.assertEqual(
                mapping["services"]["proxy"]["parity"],
                "EXCLUDED_IN_LOOPBACK_VERIFY_STACK",
            )

    def test_valid_runtime_snapshot_passes_verification(self) -> None:
        snap = _make_valid_runtime_snapshot("staging_vps_capped")
        verdict = g18_prof.verify_declared_vs_runtime_config_snapshot(
            snap, profile_key="staging_vps_capped"
        )
        self.assertTrue(verdict["verified"])
        self.assertEqual(verdict["actual_total_capped_cpus_9_services"], 6.8)
        self.assertEqual(verdict["actual_total_capped_ram_mib_9_services"], 10496.0)
        self.assertEqual(verdict["live_gunicorn_workers"], 4)
        self.assertEqual(verdict["effective_total_web_threads"], 16)

    def test_rejects_cpus_limit_zero_uncapped_when_profile_declared_capped(
        self,
    ) -> None:
        # 1. HostConfig NanoCpus == 0 on backend (exact Ops 99310ce Attempt 2 D4 defect)
        snap = _make_valid_runtime_snapshot("staging_vps_capped")
        snap["cgroup_container_inspection"]["backend"]["cpus_limit"] = 0.0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx1:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx1.exception.code, "RUNTIME_CPU_UNCAPPED_ZERO")

        # 2. Uncapped on s1_canonical_4x4 as well
        snap_can = _make_valid_runtime_snapshot("s1_canonical_4x4")
        snap_can["cgroup_container_inspection"]["db"]["cpus_limit"] = 0.0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx2:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap_can, profile_key="s1_canonical_4x4"
            )
        self.assertEqual(ctx2.exception.code, "RUNTIME_CPU_UNCAPPED_ZERO")

        # 3. In-container /sys/fs/cgroup/cpu.max == 'max 100000' (0.0)
        snap_cg = _make_valid_runtime_snapshot("staging_vps_capped")
        snap_cg["service_worker_inspection"]["backend"]["cgroup_cpus"] = 0.0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx3:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap_cg, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx3.exception.code, "RUNTIME_CPU_UNCAPPED_ZERO")

    def test_rejects_wrong_ram_limits_and_wrong_innodb_buffer_pool(self) -> None:
        # 1. Wrong RAM on queue-short (1024 MiB instead of 2048 MiB)
        snap1 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap1["cgroup_container_inspection"]["queue-short"]["memory_limit_mib"] = 1024.0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx1:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap1, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx1.exception.code, "RUNTIME_RAM_LIMIT_MISMATCH")

        # 2. Uncapped RAM (0.0) on backend
        snap2 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap2["cgroup_container_inspection"]["backend"]["memory_limit_mib"] = 0.0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx2:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap2, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx2.exception.code, "RUNTIME_RAM_UNCAPPED_ZERO")

        # 3. Wrong MariaDB innodb_buffer_pool_size (128 MiB default instead of 2560 MiB)
        snap3 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap3["mariadb_variables"]["innodb_buffer_pool_size"] = str(128 * 1024 * 1024)
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx3:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap3, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx3.exception.code, "RUNTIME_DB_BUFFER_POOL_MISMATCH")

    def test_rejects_wrong_worker_counts_across_backend_rq_and_daemons(self) -> None:
        # 1. Gunicorn 2 live workers instead of 4
        snap1 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap1["service_worker_inspection"]["backend"]["live_gunicorn_worker_count"] = 2
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx1:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap1, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx1.exception.code, "RUNTIME_WORKER_COUNT_MISMATCH")

        # 2. queue-short has 0 registered RQ workers
        snap2 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap2["service_worker_inspection"]["queue-short"][
            "registered_rq_worker_count"
        ] = 0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx2:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap2, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx2.exception.code, "RUNTIME_WORKER_COUNT_MISMATCH")

        # 3. queue-long has 2 registered RQ workers instead of 1
        snap3 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap3["service_worker_inspection"]["queue-long"][
            "registered_rq_worker_count"
        ] = 2
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx3:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap3, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx3.exception.code, "RUNTIME_WORKER_COUNT_MISMATCH")

        # 4. scheduler daemon died (0 live processes)
        snap4 = _make_valid_runtime_snapshot("staging_vps_capped")
        snap4["service_worker_inspection"]["scheduler"]["live_process_count"] = 0
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx4:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap4, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx4.exception.code, "RUNTIME_WORKER_COUNT_MISMATCH")

    def test_rejects_missing_or_incomplete_runtime_config_snapshot(self) -> None:
        for bad_snap in (None, {}):
            with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx0:
                g18_prof.verify_declared_vs_runtime_config_snapshot(
                    bad_snap, profile_key="staging_vps_capped"
                )
            self.assertEqual(ctx0.exception.code, "MISSING_RUNTIME_CONFIG_SNAPSHOT")

        # Empty cgroup_container_inspection = {} (exact Ops 0127b18 G18 report defect!)
        snap_empty_cg = _make_valid_runtime_snapshot("staging_vps_capped")
        snap_empty_cg["cgroup_container_inspection"] = {}
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx1:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap_empty_cg, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx1.exception.code, "MISSING_RUNTIME_CONFIG_SNAPSHOT")

        # Missing 1 of the 9 required services (`redis-queue`)
        snap_missing_svc = _make_valid_runtime_snapshot("staging_vps_capped")
        del snap_missing_svc["cgroup_container_inspection"]["redis-queue"]
        with self.assertRaises(g18_prof.RuntimeConfigVerificationError) as ctx2:
            g18_prof.verify_declared_vs_runtime_config_snapshot(
                snap_missing_svc, profile_key="staging_vps_capped"
            )
        self.assertEqual(ctx2.exception.code, "MISSING_RUNTIME_CONFIG_SNAPSHOT")

    def test_runner_cli_fails_closed_before_http_acceptance_when_runtime_uncapped(
        self,
    ) -> None:
        spec_preview = bench.build_benchmark_specification(
            profile_name="s1_suggested_mvp",
            base_url="http://127.0.0.1:18880",
            site_name="p4-s1-bench.localhost",
            compose_project="cskh-p4-bench-deadbeef",
            confirm_isolated_target=True,
        )
        s1_payload, _ = _make_synthetic_s1_and_http(
            callback_p95_ms=240.0,
            dist_created=10_000,
            dist_status="completed",
            spec=spec_preview,
        )

        def _uncapped_subproc(cmd: list[str], *args: object, **kwargs: object):
            if "-" in cmd and kwargs.get("input") == bench.S1_IN_CONTAINER_BENCH_SCRIPT:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="S1_BENCH_JSON:" + json.dumps(s1_payload) + "\n",
                    stderr="",
                )
            if cmd[:2] == ["docker", "inspect"] and str(cmd[2]) == "cid-backend":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        [
                            {
                                "Id": "full-cid-backend",
                                "Name": "/cskh-p4-bench-backend-1",
                                "State": {"Running": True, "OOMKilled": False},
                                "HostConfig": {
                                    "NanoCpus": 0,
                                    "Memory": 2048 * 1024 * 1024,
                                },
                                "Config": {"Cmd": ["start.sh"]},
                                "RestartCount": 0,
                            }
                        ]
                    ),
                    stderr="",
                )
            handled = _handle_mock_runtime_topology_subproc(
                cmd, kwargs, profile_key="staging_vps_capped"
            )
            if handled is not None:
                return handled
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        class _FakeHTTPResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

            def read(self) -> bytes:
                return b'{"message":"pong"}'

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "uncapped-fail.json"
            http_mock = mock.Mock()
            with (
                mock.patch.object(
                    bench,
                    "enforce_live_resource_headroom",
                    return_value={"headroom_ok": True},
                ),
                mock.patch.object(
                    bench.subprocess, "run", side_effect=_uncapped_subproc
                ),
                mock.patch.object(
                    bench.urllib.request, "urlopen", return_value=_FakeHTTPResp()
                ),
                mock.patch.object(bench, "execute_isolated_benchmark", http_mock),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                rc = bench.main(
                    [
                        "--provision-and-run-s1",
                        "--confirm-isolated-target",
                        "--resource-profile",
                        "staging_vps_capped",
                        "--app-image",
                        LOCKED_DIGEST,
                        "--out",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 1)
            http_mock.assert_not_called()
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "EXECUTION_ERROR")
            self.assertEqual(
                saved["failed_stage"], "pre_acceptance_runtime_config_verification"
            )
            self.assertEqual(saved["error_code"], "RUNTIME_CPU_UNCAPPED_ZERO")

    def test_reconciler_validates_cells_and_triggers_b_plus_dossier_when_agg03c_slips(
        self,
    ) -> None:
        def _mk_v2_cell(
            cfg: str, busy: bool, agg03c_ms: float, cpus_limit: float | None = None
        ):
            exp_cpu = 1.5 if cfg == "A" else 2.0
            exp_w = 2 if cfg == "A" else 4
            cls = "BUSY_VERIFIED" if busy else "IDLE_VERIFIED"
            qver = {
                "queue_short_classification": cls,
                "active_job_ids_at_start": ["pds-1"] if busy else [],
                "qs_cpu_ms_during_wave": 620.0 if busy else 4.0,
            }
            return {
                "topology_verified": {
                    "containers_inspected": {
                        "backend": {
                            "cpus_limit": exp_cpu if cpus_limit is None else cpus_limit
                        }
                    },
                    "in_container_gunicorn_and_cgroup": {
                        "live_gunicorn_worker_count": exp_w
                    },
                },
                "waves": {
                    w: {"queue_short_window_verification": copy.deepcopy(qver)}
                    for w in ("API-00", "SSR-01C", "API-01C", "MIXED-40", "MIXED-80")
                },
                "slo_metrics_summary": {
                    "SSR-01C_p99_ms": 2200.0 if cfg == "B" else 4600.0,
                    "API-00_p95_ms": 145.0 if cfg == "B" else 280.0,
                    "API-01C_p95_ms": 680.0 if cfg == "B" else 1210.0,
                    "API-02B_p95_ms": 1620.0 if cfg == "B" else 2150.0,
                    "AGG-03B_p95_ms": 1510.0 if cfg == "B" else 1920.0,
                    "AGG-03C_p95_ms": agg03c_ms,
                },
            }

        # Case 1: B_busy passes all 6 SLOs -> locks Config B (2.00 vCPU / 4x4)
        v2_pass = {
            "schema_version": "cskh-p4-t1b-ab-2x2-vps-diag-v2",
            "valid": True,
            "diagnostic_status": "VALID",
            "cells": {
                "A_idle": _mk_v2_cell("A", False, 3550.0),
                "A_busy": _mk_v2_cell("A", True, 3820.0),
                "B_idle": _mk_v2_cell("B", False, 2640.0),
                "B_busy": _mk_v2_cell("B", True, 2860.0),
            },
        }
        r1 = rec_ab.reconcile_v2_2x2_report(v2_pass)
        self.assertEqual(r1["rejected_cells"], {})
        self.assertEqual(r1["decision"], "LOCK_CONFIG_B_2_0VCPU_4X4_STAGING_VPS_CAPPED")
        self.assertIsNone(r1["config_b_plus_2_5vcpu_contingency_dossier"])

        # Case 2: B_busy slips AGG-03C (3085 ms > 3000 ms) -> emits Config B+ 2.5 vCPU justification
        v2_slip = copy.deepcopy(v2_pass)
        v2_slip["cells"]["B_busy"] = _mk_v2_cell("B", True, 3085.0)
        r2 = rec_ab.reconcile_v2_2x2_report(v2_slip)
        self.assertEqual(
            r2["decision"],
            "ESCALATE_TO_CONFIG_B_PLUS_2_5VCPU_4X4_WITH_MEASURED_JUSTIFICATION",
        )
        self.assertIsNotNone(r2["config_b_plus_2_5vcpu_contingency_dossier"])
        self.assertEqual(
            r2["config_b_plus_2_5vcpu_contingency_dossier"][
                "host_resource_budget_on_vps_3928"
            ]["config_spec_sha256_10_service"],
            "530765c275067dc08bef704cc316c22683376c850b32cde5546e52b9c0d302a7",
        )

        # Case 3: Uncapped B_idle (cpus_limit=0.0) is rejected fail-closed
        v2_bad = copy.deepcopy(v2_pass)
        v2_bad["cells"]["B_idle"] = _mk_v2_cell("B", False, 2600.0, cpus_limit=0.0)
        r3 = rec_ab.reconcile_v2_2x2_report(v2_bad)
        self.assertIn("B_idle", r3["rejected_cells"])

        # Case 4: Split 04a (full-2x2 aborted on B_busy 30s timeout) + 04b (single-config B 120s)
        def _as_list_waves(cell_doc: dict) -> dict:
            c = copy.deepcopy(cell_doc)
            c["waves"] = [
                {
                    "wave_id": f"w_{idx}",
                    "summary": {"error_count": 0},
                    "queue_short_window_verification": copy.deepcopy(
                        next(iter(cell_doc["waves"].values()))[
                            "queue_short_window_verification"
                        ]
                    ),
                }
                for idx in range(6)
            ]
            return c

        doc_04a = {
            "schema_version": "cskh-p4-t1b-ab-2x2-vps-diag-v2",
            "mode": "full-2x2",
            "valid": False,
            "diagnostic_status": "DIAGNOSTIC_INVALID",
            "errors": [{"code": "HTTP_WAVE_NON_200_ERRORS"}],
            "cells": {
                "A_idle": _as_list_waves(_mk_v2_cell("A", False, 4378.8)),
                "A_busy": _as_list_waves(_mk_v2_cell("A", True, 17829.9)),
                "B_idle": _as_list_waves(_mk_v2_cell("B", False, 2501.3)),
            },
        }
        doc_04b = {
            "schema_version": "cskh-p4-t1b-ab-2x2-vps-diag-v2",
            "mode": "single-config",
            "valid": True,
            "diagnostic_status": "VALID",
            "errors": [],
            "cells": {
                "B_idle": _as_list_waves(_mk_v2_cell("B", False, 4007.0)),
                "B_busy": _as_list_waves(_mk_v2_cell("B", True, 26776.1)),
            },
        }
        r4 = rec_ab.reconcile_multi_v2_reports([("04a", doc_04a), ("04b", doc_04b)])
        self.assertEqual(r4["valid_cells"], ["A_busy", "A_idle", "B_busy", "B_idle"])
        self.assertIn("B_busy_04a", r4["rejected_cells"])
        self.assertIn("B_idle_prior_04a", r4["supplementary_valid_trials"])
        self.assertEqual(
            r4["decision"],
            "ESCALATE_TO_CONFIG_B_PLUS_2_5VCPU_4X4_WITH_MEASURED_JUSTIFICATION",
        )


if __name__ == "__main__":
    unittest.main()
