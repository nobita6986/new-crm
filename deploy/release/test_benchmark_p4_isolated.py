#!/usr/bin/env python3
"""Regression tests for `benchmark_p4_isolated.py` JSON persistence, raw data, diagnostics & exit codes."""

from __future__ import annotations

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
import evaluate_s1_slo  # noqa: E402

CANDIDATE_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "ba68cd229ea3f3dc2566017e13251387b3fe398196acd4880a2a184201a00be8"
)
SLO_SPEC_PATH = RELEASE_DIR / "p4-s1-slo-spec.json"


def _make_http_node(
    *,
    p95_ms: float = 420.0,
    p99_ms: float = 580.0,
    requests_total: int = 120,
    error_count: int = 0,
) -> dict[str, object]:
    raw = (
        [round(p95_ms * 0.6, 3) for _ in range(max(requests_total - 6, 1))]
        + [round(p95_ms, 3) for _ in range(5)]
        + [round(p99_ms, 3)]
    )
    raw = raw[:requests_total]
    statuses = [500 if i < error_count else 200 for i in range(len(raw))]
    return {
        **bench._classify_http_statuses(statuses),
        **bench.summarize_latencies_ms(raw, error_count=error_count, elapsed_sec=5.0),
        "raw_latencies_ms": raw,
    }


def _make_synthetic_s1_and_http(
    *,
    callback_p95_ms: float,
    dist_created: int,
    dist_status: str,
    spec: dict[str, object],
    latency_scale: float = 1.0,
) -> tuple[dict[str, object], dict[str, object]]:
    def _nh(p95_ms: float, p99_ms: float, requests_total: int) -> dict[str, object]:
        return _make_http_node(
            p95_ms=round(p95_ms * latency_scale, 3),
            p99_ms=round(p99_ms * latency_scale, 3),
            requests_total=requests_total,
        )

    timed_out = dist_status.lower() not in (
        "completed",
        "completed_with_errors",
        "failed",
        "aborted",
    )
    s1_in_container: dict[str, object] = {
        "runner_hardware": {
            "platform": "Linux-test",
            "cpu_count": 4,
            "python_version": "3.11",
        },
        "s1_seeded_counts": {
            "crm_leads": 50_000,
            "cskh_identity_index": 50_000,
            "cskh_work_items": 75_000,
            "cskh_contact_attempts": 250_000,
            "cskh_sla_obligations": 150_000,
            "cskh_assignment_history": 75_000,
            "active_staff_users": 40,
            "teams": 4,
            "campaigns": 6,
            "seed_elapsed_sec": 42.1,
        },
        "primary_campaign_id": "CAMP-S1-01",
        "dist_10k_campaign_id": "CAMP-DIST-10K",
        "staff_users": [f"staff.s1.{i:02d}@dev.local" for i in range(1, 41)],
        "leader_users": [f"leader.bench.{i}@dev.local" for i in range(1, 5)],
        "bod_user": "bod.bench@dev.local",
        "admin_user": "admin.bench@dev.local",
        "http_open_work_items": [
            {
                "work_item": f"S1-WI-{i:06d}",
                "owner": "staff.s1.01@dev.local",
                "lead": f"S1-LEAD-{i:06d}",
            }
            for i in range(350)
        ],
        "kpi_recompute_s1": {
            "monthly_full_75k_wi_250k_att_ms": 11250.0,
            "monthly_run_status": "completed",
            "monthly_rss_after_mib": 210.5,
            "monthly_hwm_mib": 240.0,
            "daily_incremental_ms": 980.0,
            "daily_rss_after_mib": 212.0,
            "background_queue_long_recompute": {
                "wall_ms": 12400.0,
                "revision_before": "RUN-001",
                "revisions_seen_while_running": ["RUN-001"],
                "revision_after_complete": "RUN-002",
                "atomic_publish_verified": True,
                "concurrent_ui_overview_reads": {
                    "count": 20,
                    "error_count": 0,
                    "error_rate_pct": 0.0,
                    "throughput_ops_sec": 12.5,
                    "min_ms": 15.0,
                    "p50_ms": 28.0,
                    "p95_ms": 55.0,
                    "p99_ms": 62.0,
                    "max_ms": 65.0,
                    "raw_latencies_ms": [28.0] * 20,
                },
            },
        },
        "domain_workloads_s1": {
            "my_customers_list_work_items": {
                "p95_ms": 18.0,
                "raw_latencies_ms": [15.0, 18.0],
            },
            "work_item_detail_and_attempt_timeline": {
                "p95_ms": 22.0,
                "raw_latencies_ms": [20.0, 22.0],
            },
            "contact_attempt_and_followup_write": {
                "p95_ms": 95.0,
                "idempotent_replay_verified": True,
                "raw_latencies_ms": [80.0, 95.0],
            },
            "bod_executive_overview": {
                "p95_ms": 25.0,
                "contact_attempt_table_scans_during_read": 0,
                "raw_latencies_ms": [20.0, 25.0],
            },
            "kpi_metric_drilldown_250k_attempts": {
                "p95_ms": 48.0,
                "raw_latencies_ms": [40.0, 48.0],
            },
            "kpi_metric_drilldown_full_month_company_250k": {
                "p95_ms": 110.0,
                "raw_latencies_ms": [95.0, 110.0],
            },
        },
        "async_import_distribution_and_queue_lag": {
            "csv_import_50000_rows": {
                "total_rows": 50_000,
                "valid_rows": 49_500,
                "new_rows": 10_000,
                "duplicate_rows": 39_500,
                "error_rows": 500,
                "created_rows": 10_000,
                "reused_rows": 5_000,
                "skipped_rows": 34_500,
                "error_artifact_present": True,
                "preview_ms": 4200.0,
                "worker_commit_and_drain_ms": 58000.0,
                "total_preview_confirm_and_worker_drain_ms": 62200.0,
                "final_batch_status": "COMPLETED_WITH_ERRORS",
                "preview_rss_before_mib": 212.0,
                "preview_rss_after_mib": 265.0,
                "preview_hwm_mib": 290.0,
                "progress_checkpoints_count": 10,
                "all_progress_checkpoints": list(range(5000, 55000, 5000)),
                "concurrent_worklist_read_p95_ms": 24.0,
                "concurrent_worklist_read_raw_latencies_ms": [20.0, 24.0],
                "rows_throughput_per_sec": 803.86,
            },
            "campaign_distribution_10000_work_items_40_staff": {
                "eligible_count": 10_000,
                "preview_ms": 2644.37,
                "preview_within_60s_reference": True,
                "execute_and_worker_drain_ms": 1201130.43 if timed_out else 185000.0,
                "final_run_status": dist_status,
                "created_work_items": dist_created,
                "duplicate_work_items": 0,
                "double_submit_idempotent": True,
                "abort_cancel_guard_verified": True,
                "progress_checkpoints_recorded": [0, 100, dist_created],
                "all_progress_checkpoints": list(range(0, dist_created + 1, 100)),
                "concurrent_worklist_read_p95_ms": 31.0,
                "concurrent_worklist_read_raw_latencies_ms": [25.0, 31.0],
                "reconciliation": {
                    "active_work_items": dist_created,
                    "stale_docshares": 0,
                    "stale_open_todos": 0,
                    "ownership_reconciled": True,
                },
                "worker_and_job_diagnostics": {
                    "run_id": "dist-run-10k",
                    "campaign_id": "CAMP-DIST-10K",
                    "poll_deadline_sec": 1200.0,
                    "poll_timed_out_while_running": timed_out,
                    "distribution_run_row": {
                        "name": "dist-run-10k",
                        "status": dist_status,
                        "progress": dist_created,
                    },
                    "work_item_counts_by_status": {"in_progress": dist_created},
                    "work_item_total_in_db": dist_created,
                    "active_claim_count_in_db": dist_created,
                    "dirty_scope_counts_by_status": {"pending": 12 if timed_out else 0},
                    "rq_job_redis_key": "rq:job:p4-s1-bench.localhost::cskh-dist-dist-run-10k",
                    "rq_job_state": {"status": "started" if timed_out else "finished"},
                    "rq_queue_depths_at_step_end": {
                        "short": 2 if timed_out else 0,
                        "default": 0,
                        "long": 0,
                    },
                    "rq_active_workers_at_step_end": [
                        {
                            "name": "w-long",
                            "state": "busy" if timed_out else "idle",
                            "queues": ["long"],
                        }
                    ],
                    "all_progress_checkpoints": list(range(0, dist_created + 1, 100)),
                },
            },
            "rq_short_50_job_burst": {
                "enqueue_50_jobs_ms": 48.0,
                "peak_queue_short_depth": 50,
                "drain_to_zero_lag_ms": 620.0,
                "effective_worker_drain_jobs_per_sec": 80.65,
            },
        },
        "mariadb_explain_plans": {
            "kpi_snapshot_lookup": [{"key": "idx_cskh_kpisnap_lookup"}],
        },
    }

    scenario_ids = [
        "health_ping",
        "telesale_worklist_page",
        "quality_overview_drilldown",
        "campaigns_distribution_view",
        "my_customers_worklist_api",
        "bod_executive_overview_api",
        "kpi_metric_drilldown_250k_api",
        "kpi_metric_drilldown_monthly_company_api",
    ]
    scenario_summaries = {
        sid: {
            "workload_class": sid,
            "path": f"/{sid}",
            "concurrency": 12,
            **_nh(
                25.0 if sid == "health_ping" else 340.0,
                40.0 if sid == "health_ping" else 490.0,
                240,
            ),
        }
        for sid in scenario_ids
    }
    wave40_ids = [
        "telesale_worklist_page",
        "my_customers_worklist_api",
        "bod_executive_overview_api",
        "kpi_metric_drilldown_250k_api",
        "kpi_metric_drilldown_monthly_company_api",
    ]
    wave40_scenarios = {
        sid: {
            "workload_class": sid,
            "path": f"/{sid}",
            "concurrency": 40,
            **_nh(980.0, 1350.0, 160),
        }
        for sid in wave40_ids
    }
    ssr_c12 = {
        "path": "/cskh/my-customers",
        "concurrency": 12,
        "distinct_sessions_used": 40,
        "client_timeout_threshold_sec": 30.0,
        **_nh(410.0, 590.0, 240),
    }
    ssr_c40 = {
        "path": "/cskh/my-customers",
        "concurrency": 40,
        "distinct_sessions_used": 40,
        "client_timeout_threshold_sec": 30.0,
        **_nh(1420.0, 1880.0, 160),
    }
    cb_node = {
        **_make_http_node(
            p95_ms=callback_p95_ms,
            p99_ms=round(callback_p95_ms * 1.15, 3),
            requests_total=80,
        ),
        "idempotent_replay_raw_latencies_ms": [42.1] * 10,
        "idempotent_replay_status_counts": {"200": 10},
        "http_idempotent_replays_verified": 10,
        "db_reconciliation": {
            "attempts_created": 80,
            "duplicate_idempotency_keys": 0,
            "work_items_with_open_callback_task": 80,
        },
    }
    s1_http_acc: dict[str, object] = {
        "distinct_authenticated_sessions_created": 80,
        "staff_my_customers_http": _nh(310.0, 460.0, 240),
        "telesale_worklist_ssr_40_staff_sessions_c12": ssr_c12,
        "telesale_worklist_ssr_40_staff_sessions_c40": ssr_c40,
        "lead_detail_and_history_http": {
            **_nh(520.0, 740.0, 160),
            "get_work_item_p95_ms": 260.0,
            "list_attempts_p95_ms": 260.0,
            "raw_samples_first_15_ms": [312.0] * 15,
            "get_work_item_raw_latencies_ms": [156.0] * 160,
            "list_attempts_raw_latencies_ms": [156.0] * 160,
        },
        "record_attempt_and_callback_http": cb_node,
        "bod_overview_and_drilldown_http": {
            "bod_executive_overview_role_api": _nh(290.0, 410.0, 120),
            "kpi_drilldown_daily_campaign_role_api": _nh(360.0, 510.0, 120),
            "kpi_drilldown_monthly_company_role_api": _nh(640.0, 890.0, 120),
            "staff_unauthorized_company_drilldown_http_status": 403,
        },
        "concurrent_mixed_session_waves_20_40_80": {
            "sessions_20": {
                "concurrent_user_sessions": 20,
                "overall_summary": _nh(450.0, 650.0, 200),
                "by_workload": {
                    "my_customers_api": _nh(300.0, 420.0, 90),
                    "lead_detail_history_api": _nh(420.0, 590.0, 50),
                    "bod_overview_api": _nh(280.0, 390.0, 30),
                    "kpi_drilldown_api": _nh(390.0, 540.0, 20),
                    "record_attempt_write_api": _nh(520.0, 710.0, 10),
                },
                "container_resources_during_wave": [],
                "queue_and_db_after_wave": {"queue_backlog_total": 0},
            },
            "sessions_40": {
                "concurrent_user_sessions": 40,
                "overall_summary": _nh(920.0, 1380.0, 320),
                "by_workload": {
                    "my_customers_api": _nh(610.0, 890.0, 144),
                    "lead_detail_history_api": _nh(790.0, 1120.0, 80),
                    "bod_overview_api": _nh(520.0, 760.0, 48),
                    "kpi_drilldown_api": _nh(740.0, 1050.0, 32),
                    "record_attempt_write_api": _nh(910.0, 1280.0, 16),
                },
                "container_resources_during_wave": [],
                "queue_and_db_after_wave": {"queue_backlog_total": 0},
            },
            "sessions_80": {
                "concurrent_user_sessions": 80,
                "overall_summary": _nh(2150.0, 3100.0, 480),
                "by_workload": {
                    "my_customers_api": _nh(1500.0, 2200.0, 216),
                    "lead_detail_history_api": _nh(1650.0, 2350.0, 120),
                    "bod_overview_api": _nh(1350.0, 1950.0, 72),
                    "kpi_drilldown_api": _nh(1550.0, 2180.0, 48),
                    "record_attempt_write_api": _nh(1620.0, 1980.0, 24),
                },
                "container_resources_during_wave": [],
                "queue_and_db_after_wave": {"queue_backlog_total": 0},
            },
        },
    }
    overall_c12 = _nh(380.0, 540.0, 1920)
    overall_c40 = _nh(1050.0, 1490.0, 800)
    http_report: dict[str, object] = {
        **spec,
        "status": "MEASURED",
        "completed_at_utc": "2026-11-12T14:05:00+00:00",
        "overall_summary": overall_c12,
        "scenario_summaries": scenario_summaries,
        "queue_and_db_before": {"queue_backlog_total": 0},
        "queue_and_db_after": {"queue_backlog_total": 0},
        "container_resources_before": [],
        "container_resources_after": [],
        "peak_40_staff_concurrent_wave": {
            "concurrency": 40,
            "overall_summary": overall_c40,
            "scenario_summaries": wave40_scenarios,
        },
        "s1_http_acceptance_and_mixed_waves": s1_http_acc,
        "worklist_ssr_acceptance_and_separation": {
            "execution_mode": "BUILT_INTO_OFFICIAL_COMMIT_AND_IMAGE_DIGEST_NO_IN_CONTAINER_PATCHING",
            "priority_acceptance_case_1_shared_new_session_c12": scenario_summaries[
                "telesale_worklist_page"
            ],
            "priority_acceptance_case_2_40_staff_sessions_c40": ssr_c40,
            "case_2a_shared_new_session_c12": scenario_summaries[
                "telesale_worklist_page"
            ],
            "case_2b_single_initialized_session_c12": ssr_c12,
            "case_2b_single_initialized_session_c40": wave40_scenarios[
                "telesale_worklist_page"
            ],
            "case_2c_40_staff_sessions_c12": ssr_c12,
            "case_2c_40_staff_sessions_c40": ssr_c40,
            "ssr_vs_aggregate_separation": {
                "c12_ssr_telesale_worklist_page_only": scenario_summaries[
                    "telesale_worklist_page"
                ],
                "c12_overall_summary_all_8_scenarios": overall_c12,
                "c40_ssr_telesale_worklist_page_40_staff_sessions_only": ssr_c40,
                "c40_ssr_telesale_worklist_page_single_session_only": wave40_scenarios[
                    "telesale_worklist_page"
                ],
                "c40_overall_summary_peak_wave_5_scenarios": overall_c40,
                "c40_overall_summary_mixed_api_sessions_40": (
                    (
                        s1_http_acc["concurrent_mixed_session_waves_20_40_80"][
                            "sessions_40"
                        ]
                    )["overall_summary"]  # type: ignore[index]
                ),
            },
        },
    }
    return s1_in_container, http_report


class TestBenchmarkP4IsolatedRunnerFix(unittest.TestCase):
    def test_s1_in_container_script_compiles_cleanly(self) -> None:
        compile(bench.S1_IN_CONTAINER_BENCH_SCRIPT, "<s1_in_container>", "exec")

    def test_scrub_diagnostic_text_redacts_secrets_cookies_and_csrf(self) -> None:
        raw = (
            "Cookie: sid=9f8e7d6c5b4a3210; csrf_token='tok_secret_1234567890' "
            '{"X-Frappe-CSRF-Token": "csrf_json_val_999"} '
            "BENCH_DB_ROOT_PW=my_root_secret --admin-password admin_secret_pw "
            "leaked_actor=custom_actor_pw_hex_998877"
        )
        scrubbed = bench._scrub_diagnostic_text(
            raw, ("custom_actor_pw_hex_998877", "my_root_secret", "admin_secret_pw")
        )
        self.assertNotIn("9f8e7d6c5b4a3210", scrubbed)
        self.assertNotIn("tok_secret_1234567890", scrubbed)
        self.assertNotIn("csrf_json_val_999", scrubbed)
        self.assertNotIn("my_root_secret", scrubbed)
        self.assertNotIn("admin_secret_pw", scrubbed)
        self.assertNotIn("custom_actor_pw_hex_998877", scrubbed)
        self.assertIn("sid=[REDACTED_SID]", scrubbed)

    def test_acceptance_fail_writes_valid_json_preserves_raw_data_and_exits_nonzero(
        self,
    ) -> None:
        spec_preview = bench.build_benchmark_specification(
            profile_name="s1_suggested_mvp",
            base_url="http://127.0.0.1:18880",
            site_name="p4-s1-bench.localhost",
            compose_project="cskh-p4-bench-deadbeef",
            confirm_isolated_target=True,
        )
        s1_payload, http_payload = _make_synthetic_s1_and_http(
            callback_p95_ms=2323.612,
            dist_created=4_100,
            dist_status="running",
            spec=spec_preview,
        )

        def _fake_subprocess_run(
            cmd: list[str], *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if "-" in cmd and kwargs.get("input") == bench.S1_IN_CONTAINER_BENCH_SCRIPT:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="S1_BENCH_JSON:" + json.dumps(s1_payload) + "\n",
                    stderr="",
                )
            if "logs" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="worker-long running job cskh-dist-dist-run-10k sid=secretcookie123\n",
                    stderr="",
                )
            if "-c" in cmd:
                probe_src = str(cmd[cmd.index("-c") + 1])
                if "mariadb_global_status" in probe_src:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps(
                            {
                                "queue_depths": {"short": 3, "default": 0, "long": 0},
                                "queue_backlog_total": 3,
                                "redis_queue_rtt_ms": 0.42,
                                "active_rq_workers": [
                                    {
                                        "name": "w-long",
                                        "state": "busy",
                                        "queues": ["long"],
                                        "current_job_id": "cskh-dist-dist-run-10k",
                                    }
                                ],
                                "busy_worker_count": 1,
                                "mariadb_global_status": {"Threads_connected": "14"},
                            }
                        )
                        + "\n",
                        stderr="",
                    )
                if "S1-CAMP-DIST-10K" in probe_src:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps(
                            {
                                "dist_10k_campaign_id": "CAMP-DIST-10K",
                                "distribution_run_row": {
                                    "name": "dist-run-10k",
                                    "status": "running",
                                    "progress": 6400,
                                },
                                "work_item_counts_by_status": {"in_progress": 6400},
                                "work_item_total_in_db": 6400,
                                "dirty_scope_counts_by_status": {"pending": 8},
                                "rq_job_state": {"status": "started"},
                            }
                        )
                        + "\n",
                        stderr="",
                    )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        class _FakeHTTPResp:
            status = 200

            def __enter__(self) -> _FakeHTTPResp:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"message":"pong"}'

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "g18-benchmark-report.json"
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with (
                mock.patch.object(
                    bench,
                    "enforce_live_resource_headroom",
                    return_value={
                        "cpu_count": 6,
                        "mem_available_mib": 9500.0,
                        "headroom_ok": True,
                    },
                ),
                mock.patch.object(
                    bench.subprocess, "run", side_effect=_fake_subprocess_run
                ),
                mock.patch.object(
                    bench.subprocess,
                    "check_output",
                    return_value=json.dumps([{"Id": "sha256:testimageid"}]),
                ),
                mock.patch.object(
                    bench.urllib.request, "urlopen", return_value=_FakeHTTPResp()
                ),
                mock.patch.object(
                    bench, "execute_isolated_benchmark", return_value=http_payload
                ),
                redirect_stdout(stdout_buf),
                redirect_stderr(stderr_buf),
            ):
                rc = bench.main(
                    [
                        "--provision-and-run-s1",
                        "--confirm-isolated-target",
                        "--app-image",
                        CANDIDATE_DIGEST,
                        "--resource-profile",
                        "staging_vps_capped",
                        "--out",
                        str(out_path),
                    ]
                )

            self.assertEqual(
                rc,
                1,
                "Runner must return non-zero exit code when acceptance criteria FAIL",
            )
            self.assertTrue(
                out_path.is_file(), "Runner must still write --out JSON file on FAIL"
            )
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "MEASURED")
            self.assertEqual(saved["acceptance_verdict"], "FAIL")
            self.assertFalse(saved["all_acceptance_passed"])

            matrix = saved["p4_9_2_acceptance_matrix"]
            self.assertFalse(matrix["all_passed"])
            self.assertEqual(
                set(matrix["failed_checks"]),
                {
                    "record_attempt_and_callback_p95_le_2000ms_zero_dup",
                    "execute_weighted_distribution_10000_items",
                },
            )
            # Raw latency arrays and worker/job diagnostics preserved losslessly
            cb_saved = saved["s1_http_acceptance_and_mixed_waves"][
                "record_attempt_and_callback_http"
            ]
            self.assertEqual(len(cb_saved["raw_latencies_ms"]), 80)
            self.assertEqual(cb_saved["p95_ms"], 2323.612)
            dist_diag = saved["s1_domain_and_database_benchmark"][
                "async_import_distribution_and_queue_lag"
            ]["campaign_distribution_10000_work_items_40_staff"][
                "worker_and_job_diagnostics"
            ]
            self.assertTrue(dist_diag["poll_timed_out_while_running"])
            self.assertEqual(dist_diag["work_item_total_in_db"], 4100)
            self.assertEqual(dist_diag["rq_job_state"]["status"], "started")

            pre_td = saved["worker_and_job_diagnostics"]
            self.assertEqual(
                pre_td["distribution_and_work_item_final_state"][
                    "work_item_total_in_db"
                ],
                6400,
            )
            self.assertNotIn(
                "secretcookie123", pre_td["service_logs_tail"]["queue-long"]
            )
            self.assertIn(
                "sid=[REDACTED_SID]", pre_td["service_logs_tail"]["queue-long"]
            )

            # Verify evaluate_s1_slo consumes the FAIL report cleanly without JSON/schema crash
            slo_sha = evaluate_s1_slo.compute_normalized_sha256(SLO_SPEC_PATH)
            eval_res = evaluate_s1_slo.evaluate_slo_bundle(
                slo_spec_path=SLO_SPEC_PATH,
                expected_slo_sha256=slo_sha,
                benchmark_report_path=out_path,
                profile="staging_vps_capped",
                expected_image_digest=CANDIDATE_DIGEST,
            )
            self.assertEqual(eval_res["gate_errors"], [])
            self.assertEqual(eval_res["status"], "FAIL")
            failed_slo_ids = {
                r["slo_id"] for r in eval_res["s1_rule_results"] if not r["passed"]
            }
            self.assertEqual(failed_slo_ids, {"API-03A", "BG-01"})

    def test_acceptance_pass_writes_valid_json_and_exits_zero(self) -> None:
        spec_preview = bench.build_benchmark_specification(
            profile_name="s1_suggested_mvp",
            base_url="http://127.0.0.1:18880",
            site_name="p4-s1-bench.localhost",
            compose_project="cskh-p4-bench-cafebabe",
            confirm_isolated_target=True,
        )
        s1_payload, http_payload = _make_synthetic_s1_and_http(
            callback_p95_ms=240.0,
            dist_created=10_000,
            dist_status="completed",
            spec=spec_preview,
            latency_scale=0.18,
        )

        def _fake_subprocess_run(
            cmd: list[str], *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if "-" in cmd and kwargs.get("input") == bench.S1_IN_CONTAINER_BENCH_SCRIPT:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="S1_BENCH_JSON:" + json.dumps(s1_payload) + "\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        class _FakeHTTPResp:
            status = 200

            def __enter__(self) -> _FakeHTTPResp:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"message":"pong"}'

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "g18-pass-report.json"
            with (
                mock.patch.object(
                    bench.subprocess, "run", side_effect=_fake_subprocess_run
                ),
                mock.patch.object(
                    bench.subprocess,
                    "check_output",
                    return_value=json.dumps([{"Id": "sha256:testimageid"}]),
                ),
                mock.patch.object(
                    bench.urllib.request, "urlopen", return_value=_FakeHTTPResp()
                ),
                mock.patch.object(
                    bench, "execute_isolated_benchmark", return_value=http_payload
                ),
                redirect_stdout(io.StringIO()),
            ):
                rc = bench.main(
                    [
                        "--provision-and-run-s1",
                        "--confirm-isolated-target",
                        "--app-image",
                        CANDIDATE_DIGEST,
                        "--resource-profile",
                        "s1_canonical_4x4",
                        "--out",
                        str(out_path),
                    ]
                )

            self.assertEqual(rc, 0)
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "MEASURED")
            self.assertEqual(saved["acceptance_verdict"], "PASS")
            self.assertTrue(saved["all_acceptance_passed"])
            self.assertEqual(saved["p4_9_2_acceptance_matrix"]["failed_checks"], [])

            slo_sha = evaluate_s1_slo.compute_normalized_sha256(SLO_SPEC_PATH)
            eval_pass = evaluate_s1_slo.evaluate_slo_bundle(
                slo_spec_path=SLO_SPEC_PATH,
                expected_slo_sha256=slo_sha,
                benchmark_report_path=out_path,
                profile="s1_canonical_4x4",
                expected_image_digest=CANDIDATE_DIGEST,
            )
            self.assertEqual(eval_pass["gate_errors"], [])
            self.assertEqual(eval_pass["status"], "PASS")

    def test_mid_run_execution_error_writes_diagnostic_json_and_exits_nonzero(
        self,
    ) -> None:
        spec_preview = bench.build_benchmark_specification(
            profile_name="s1_suggested_mvp",
            base_url="http://127.0.0.1:18880",
            site_name="p4-s1-bench.localhost",
            compose_project="cskh-p4-bench-feedface",
            confirm_isolated_target=True,
        )
        s1_payload, _ = _make_synthetic_s1_and_http(
            callback_p95_ms=923.278,
            dist_created=10_000,
            dist_status="completed",
            spec=spec_preview,
        )

        def _fake_subprocess_run(
            cmd: list[str], *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if "-" in cmd and kwargs.get("input") == bench.S1_IN_CONTAINER_BENCH_SCRIPT:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout="S1_BENCH_JSON:" + json.dumps(s1_payload) + "\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        class _FakeHTTPResp:
            status = 200

            def __enter__(self) -> _FakeHTTPResp:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"message":"pong"}'

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "g18-error-report.json"
            with (
                mock.patch.object(
                    bench.subprocess, "run", side_effect=_fake_subprocess_run
                ),
                mock.patch.object(
                    bench.urllib.request, "urlopen", return_value=_FakeHTTPResp()
                ),
                mock.patch.object(
                    bench,
                    "execute_isolated_benchmark",
                    side_effect=RuntimeError(
                        "HTTP login crashed with sid=leaked_cookie_abc"
                    ),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                rc = bench.main(
                    [
                        "--provision-and-run-s1",
                        "--confirm-isolated-target",
                        "--app-image",
                        CANDIDATE_DIGEST,
                        "--resource-profile",
                        "s1_canonical_4x4",
                        "--out",
                        str(out_path),
                    ]
                )

            self.assertEqual(rc, 1)
            self.assertTrue(out_path.is_file())
            saved = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "EXECUTION_ERROR")
            self.assertEqual(saved["failed_stage"], "http_benchmark_execution")
            self.assertNotIn("leaked_cookie_abc", saved["error_message"])
            self.assertIn("sid=[REDACTED_SID]", saved["error_message"])
            # In-container S1 domain measurements completed before the HTTP error are preserved
            self.assertIsInstance(saved["s1_domain_and_database_benchmark"], dict)

    def test_staging_vps_capped_compose_override_pins_mariadb_innodb_and_worker_queues(
        self,
    ) -> None:
        self.assertIn(
            "--innodb-buffer-pool-size=2560M",
            bench.STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
        )
        self.assertIn(
            "--innodb-log-file-size=512M",
            bench.STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
        )
        self.assertIn(
            'command: ["bench", "worker", "--queue", "long"]',
            bench.STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
        )
        self.assertIn(
            'command: ["bench", "worker", "--queue", "short,default"]',
            bench.STAGING_VPS_CAPPED_BENCH_OVERRIDE_YAML,
        )
        common_override = (
            bench.REPO_ROOT
            / "deploy"
            / "compose"
            / "overrides"
            / "compose.cskh-common.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'command: !override ["bench", "worker", "--queue", "long"]',
            common_override,
        )
        self.assertIn(
            'command: !override ["bench", "worker", "--queue", "short,default"]',
            common_override,
        )

    def test_probe_s1_short_cli_persists_valid_json_on_pass_and_fail(self) -> None:
        spec = bench.build_benchmark_specification(
            profile_name="s1_suggested_mvp",
            base_url="http://127.0.0.1:18880",
            site_name="p4-s1-probe.localhost",
            compose_project="cskh-p4-bench-probe-unit",
            confirm_isolated_target=True,
        )
        passing_probe = {
            **spec,
            "mode": "s1_short_throughput_probe",
            "status": "COMPLETED",
            "acceptance_verdict": "PASS",
            "all_acceptance_passed": True,
            "sla_commitment_status": "UNCOMMITTED_PROPOSAL_PENDING_OWNER_APPROVAL",
            "app_image_tested": CANDIDATE_DIGEST,
            "resource_profile": "staging_vps_capped",
            "probe_items": 500,
            "queue_long_scaling_evaluation": {
                "bg01_job_architecture": "single_sequential_rq_job",
                "single_job_parallelizable_by_extra_workers": False,
                "configured_queue_long_workers": 1,
                "peak_long_queue_waiting_jobs_during_bg01": 0,
                "measured_ms_per_item": 42.5,
                "extrapolated_10000_items_sec": 425.0,
                "recommend_scaling_queue_long_above_1": False,
                "decision": "KEEP_QUEUE_LONG_REPLICAS_1",
            },
            "probe_gate_verdict": {
                "all_passed": True,
                "total_checks": 6,
                "passed_count": 6,
                "failed_count": 0,
                "failed_checks": [],
                "ready_for_official_g18_full_run": True,
                "items": {},
            },
        }
        failing_probe = {
            **passing_probe,
            "acceptance_verdict": "FAIL",
            "all_acceptance_passed": False,
            "probe_gate_verdict": {
                "all_passed": False,
                "total_checks": 6,
                "passed_count": 5,
                "failed_count": 1,
                "failed_checks": ["bg01_per_item_le_95ms_extrapolated_10k_le_950s"],
                "ready_for_official_g18_full_run": False,
                "items": {},
            },
        }

        with tempfile.TemporaryDirectory() as td:
            pass_out = Path(td) / "probe-pass.json"
            with (
                mock.patch.object(
                    bench,
                    "provision_and_execute_s1_short_probe",
                    return_value=passing_probe,
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                rc_pass = bench.main(
                    [
                        "--probe-s1-short",
                        "--confirm-isolated-target",
                        "--resource-profile",
                        "staging_vps_capped",
                        "--probe-items",
                        "500",
                        "--app-image",
                        CANDIDATE_DIGEST,
                        "--out",
                        str(pass_out),
                    ]
                )
            self.assertEqual(rc_pass, 0)
            saved_pass = json.loads(pass_out.read_text(encoding="utf-8"))
            self.assertTrue(saved_pass["probe_gate_verdict"]["all_passed"])
            self.assertFalse(
                saved_pass["queue_long_scaling_evaluation"][
                    "recommend_scaling_queue_long_above_1"
                ]
            )

            fail_out = Path(td) / "probe-fail.json"
            with (
                mock.patch.object(
                    bench,
                    "provision_and_execute_s1_short_probe",
                    return_value=failing_probe,
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                rc_fail = bench.main(
                    [
                        "--probe-s1-short",
                        "--confirm-isolated-target",
                        "--resource-profile",
                        "staging_vps_capped",
                        "--probe-items",
                        "500",
                        "--app-image",
                        CANDIDATE_DIGEST,
                        "--out",
                        str(fail_out),
                    ]
                )
            self.assertEqual(rc_fail, 1)
            saved_fail = json.loads(fail_out.read_text(encoding="utf-8"))
            self.assertEqual(saved_fail["acceptance_verdict"], "FAIL")
            self.assertEqual(
                saved_fail["probe_gate_verdict"]["failed_checks"],
                ["bg01_per_item_le_95ms_extrapolated_10k_le_950s"],
            )

    def test_provision_and_execute_s1_short_probe_compose_project_prefix_passes_isolation_guard(
        self,
    ) -> None:
        seen_projects: list[str] = []

        def _fake_subproc_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "-p" in cmd:
                seen_projects.append(str(cmd[cmd.index("-p") + 1]))
            if isinstance(cmd, list) and "up" in cmd:
                raise RuntimeError("stop_after_isolation_guard")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(
                bench,
                "enforce_live_resource_headroom",
                return_value={"headroom_ok": True},
            ),
            mock.patch.object(bench.subprocess, "run", side_effect=_fake_subproc_run),
        ):
            with self.assertRaises(bench.BenchmarkExecutionError) as ctx:
                bench.provision_and_execute_s1_short_probe(
                    app_image=CANDIDATE_DIGEST,
                    pull_policy="never",
                    port=18882,
                    resource_profile="staging_vps_capped",
                    require_live_headroom=True,
                    probe_items=500,
                )
            self.assertEqual(ctx.exception.report["failed_stage"], "compose_up")
            self.assertTrue(
                str(
                    ctx.exception.report["isolation_guard"]["compose_project"]
                ).startswith("cskh-p4-bench-probe-"),
                ctx.exception.report["isolation_guard"],
            )
            self.assertTrue(
                any(p.startswith("cskh-p4-bench-probe-") for p in seen_projects)
            )
            compile(bench.S1_IN_CONTAINER_BENCH_SCRIPT, "<s1_in_container>", "exec")
            self.assertNotIn('_status_after_bg01["', bench.S1_IN_CONTAINER_BENCH_SCRIPT)
            self.assertNotIn(
                '_status_before_bg01["', bench.S1_IN_CONTAINER_BENCH_SCRIPT
            )
            self.assertNotIn(
                'dist_status_after_exec["', bench.S1_IN_CONTAINER_BENCH_SCRIPT
            )
            self.assertNotIn(
                'dist_status_before_preview["', bench.S1_IN_CONTAINER_BENCH_SCRIPT
            )
            self.assertNotIn(
                'dist_status_before_exec["', bench.S1_IN_CONTAINER_BENCH_SCRIPT
            )
            for expected_key in (
                '"sql_queries_delta":',
                '"sql_queries_per_item":',
                '"execute_sql_queries_delta":',
                '"execute_sql_queries_per_item":',
                '"peak_long_queue_waiting_jobs_during_bg01":',
                '"peak_short_queue_waiting_jobs_during_bg01":',
            ):
                self.assertGreaterEqual(
                    bench.S1_IN_CONTAINER_BENCH_SCRIPT.count(expected_key),
                    2,
                    f"Expected {expected_key} in both short probe and full 10k BG-01 payloads",
                )


if __name__ == "__main__":
    unittest.main()
