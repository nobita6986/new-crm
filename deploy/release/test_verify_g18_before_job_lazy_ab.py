#!/usr/bin/env python3
"""Unit tests for `deploy/release/verify_g18_before_job_lazy_ab.py`."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

import verify_g18_before_job_lazy_ab as ab_mod  # noqa: E402


def _sample_slot(
    *,
    label: str,
    import_ms: float,
    call_ms: float,
    weasyprint_loaded: bool,
    drained_in_60s: bool,
    finished_in_60s: int,
    short_depth_before_http: int,
    full_drain_ms: float,
    tput: float,
    wait_p95: float,
    e2e_p95: float,
    api00_p95: float,
) -> dict[str, object]:
    return {
        "label": label,
        "resource_profile_key": "staging_vps_capped_b_plus_2_5vcpu",
        "config_spec_sha256": "530765c290589588",
        "queue_short_cgroup": {"cpus_limit": 0.5, "memory_limit_mib": 2048.0},
        "before_job_hook_cost": {
            "registered_before_job_hook": (
                "crm_cskh.services.pdf_assets.ensure_pdf_runtime_hooks"
                if weasyprint_loaded
                else "crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks"
            ),
            "median_import_ms": import_ms,
            "median_call_ms": call_ms,
            "median_total_ms": round(import_ms + call_ms, 3),
            "sys_modules_after_non_pdf_job": {
                "weasyprint": weasyprint_loaded,
                "frappe.utils.weasyprint": weasyprint_loaded,
                "frappe.utils.pdf": weasyprint_loaded,
                "pdfkit": weasyprint_loaded,
                "cssutils": weasyprint_loaded,
                "pypdf": weasyprint_loaded,
                "bs4": weasyprint_loaded,
                "crm_cskh.services.pdf_assets": weasyprint_loaded,
            },
            "any_pdf_engine_loaded_after_non_pdf_job": weasyprint_loaded,
        },
        "rq_short_50_job_burst": {
            "burst_jobs": 50,
            "window_budget_sec": 60.0,
            "drained_within_60s_window": drained_in_60s,
            "jobs_finished_within_60s_window": finished_in_60s,
            "queue_depths_before_http_waves": {
                "short": short_depth_before_http,
                "default": 0,
                "long": 0,
            },
            "full_50_jobs_wall_drain_ms": full_drain_ms,
            "true_worker_throughput_jobs_per_sec": tput,
            "failed_jobs": 0,
            "per_job_queue_wait_lag_ms": {"p50_ms": wait_p95 / 2.0, "p95_ms": wait_p95},
            "per_job_end_to_end_lag_ms": {"p50_ms": e2e_p95 / 2.0, "p95_ms": e2e_p95},
        },
        "api_00_health_ping_after_60s_window": {
            "p50_ms": api00_p95 * 0.4,
            "p95_ms": api00_p95,
            "error_count": 0,
        },
    }


class TestVerifyG18BeforeJobLazyAb(unittest.TestCase):
    def test_embedded_in_container_probes_parse_cleanly(self) -> None:
        for attr in (
            "COLD_HOOK_PROBE_PY",
            "RQ_WARMUP_PY",
            "RQ_BURST_ENQUEUE_AND_60S_WINDOW_PY",
            "RQ_POST_HTTP_FULL_DRAIN_AND_LAG_PY",
        ):
            snippet = getattr(ab_mod, attr)
            self.assertIsInstance(snippet, str)
            ast.parse(snippet, filename=attr)

    def test_compare_before_after_passes_when_lazy_hook_eliminates_backlog(
        self,
    ) -> None:
        before = _sample_slot(
            label="before",
            import_ms=675.33,
            call_ms=3549.10,
            weasyprint_loaded=True,
            drained_in_60s=False,
            finished_in_60s=18,
            short_depth_before_http=32,
            full_drain_ms=165000.0,
            tput=0.303,
            wait_p95=154000.0,
            e2e_p95=158200.0,
            api00_p95=228.9,
        )
        after = _sample_slot(
            label="after",
            import_ms=2.1,
            call_ms=0.05,
            weasyprint_loaded=False,
            drained_in_60s=True,
            finished_in_60s=50,
            short_depth_before_http=0,
            full_drain_ms=3800.0,
            tput=13.158,
            wait_p95=3400.0,
            e2e_p95=3650.0,
            api00_p95=48.5,
        )
        cmp_res = ab_mod.compare_before_after_results(before, after)
        self.assertTrue(cmp_res["all_checks_passed"], cmp_res)
        self.assertGreater(cmp_res["deltas"]["hook_total_ms"]["reduction_pct"], 99.0)
        self.assertEqual(
            cmp_res["deltas"]["queue_depths_before_http_waves"]["after"]["short"], 0
        )

    def test_compare_before_after_fails_closed_if_residual_queue_or_eager_import_remains(
        self,
    ) -> None:
        before = _sample_slot(
            label="before",
            import_ms=675.33,
            call_ms=3549.10,
            weasyprint_loaded=True,
            drained_in_60s=False,
            finished_in_60s=18,
            short_depth_before_http=32,
            full_drain_ms=165000.0,
            tput=0.303,
            wait_p95=154000.0,
            e2e_p95=158200.0,
            api00_p95=228.9,
        )
        bad_after = _sample_slot(
            label="after",
            import_ms=2.1,
            call_ms=0.05,
            weasyprint_loaded=False,
            drained_in_60s=False,
            finished_in_60s=44,
            short_depth_before_http=6,
            full_drain_ms=68000.0,
            tput=0.735,
            wait_p95=62000.0,
            e2e_p95=64000.0,
            api00_p95=180.0,
        )
        cmp_res = ab_mod.compare_before_after_results(before, bad_after)
        self.assertFalse(cmp_res["all_checks_passed"])
        self.assertFalse(
            cmp_res["checks"]["after_zero_residual_queue_before_http_waves"]
        )
        self.assertFalse(
            cmp_res["checks"]["after_drains_all_50_jobs_within_60s_window"]
        )


if __name__ == "__main__":
    unittest.main()
