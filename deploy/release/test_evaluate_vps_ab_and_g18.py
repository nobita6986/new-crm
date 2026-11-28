#!/usr/bin/env python3
"""Unit & real-schema regression tests for `evaluate_vps_ab_and_g18.py` (`F-T1B-AB-EVAL-SCHEMA-01`)."""

from __future__ import annotations

import copy
import json
import pathlib
import tempfile
import unittest

import evaluate_vps_ab_and_g18 as eval_mod

SAMPLE_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "2796d1f262ad5b09951271c45079629b4d5a440005e7494531661fe16d96ceb1"
)


def _make_real_schema_4cell_doc() -> dict:
    """Exact replica of T1A's `04-b-vs-bplus-4cell-raw-and-selection.json` structure & numbers."""

    def _wave(wid: str, p95: float, p99: float, err_cnt: int = 0) -> dict:
        return {
            "wave_id": wid,
            "concurrency": 12,
            "summary": {
                "requests_total": 240,
                "ok_count": 240 - err_cnt,
                "error_count": err_cnt,
                "error_rate_pct": round((err_cnt / 240.0) * 100.0, 4),
                "p95_ms": p95,
                "p99_ms": p99,
            },
            "queue_short_window_verification": {
                "queue_short_classification": "IDLE_VERIFIED",
                "verified": True,
            },
        }

    def _cell(
        cid: str,
        cfg: str,
        mode: str,
        metrics: dict[str, float],
        *,
        err_cnt: int = 0,
    ) -> dict:
        return {
            "cell_id": cid,
            "config_id": cfg,
            "queue_short_mode": mode,
            "waves": {
                "API-00": _wave(
                    f"{cid}_api00",
                    metrics["API-00_p95_ms"],
                    metrics["API-00_p99_ms"],
                    err_cnt,
                ),
                "SSR-01C": _wave(
                    f"{cid}_ssr01c",
                    metrics["SSR-01C_p95_ms"],
                    metrics["SSR-01C_p99_ms"],
                ),
                "API-01C": _wave(
                    f"{cid}_api01c",
                    metrics["API-01C_p95_ms"],
                    metrics["API-01C_p99_ms"],
                ),
                "MIXED-40": _wave(
                    f"{cid}_mix40", metrics["AGG-03B_p95_ms"], metrics["AGG-03B_p99_ms"]
                ),
                "MIXED-80": _wave(
                    f"{cid}_mix80", metrics["AGG-03C_p95_ms"], metrics["AGG-03C_p99_ms"]
                ),
            },
            "slo_metrics_summary": metrics,
        }

    return {
        "schema_version": "cskh-p4-b-vs-bplus-dirtycount-isolated-diag-and-4field-dossier-v1",
        "candidate_commit_sha": "8dbf098da2d71490d64a92bb560f6032aceff988",
        "candidate_digest": SAMPLE_DIGEST,
        "slo_spec_sha256": eval_mod.LOCKED_SLO_SPEC_SHA256,
        "benchmark_runner_sha256": eval_mod.LOCKED_RUNNER_SHA256,
        "live_stack_preservation": {"zero_live_mutation": True},
        "control_count_reconciliation": {
            "exact_parity_all_states_and_buckets": True,
            "worker_observed_processing": True,
        },
        "cells": {
            "B_idle": _cell(
                "B_idle",
                "B",
                "IDLE_VERIFIED",
                {
                    "API-00_p95_ms": 103.007,
                    "API-00_p99_ms": 122.928,
                    "SSR-01C_p95_ms": 1235.617,
                    "SSR-01C_p99_ms": 2199.204,
                    "API-01C_p95_ms": 695.669,
                    "API-01C_p99_ms": 771.108,
                    "AGG-03B_p95_ms": 1403.752,
                    "AGG-03B_p99_ms": 1608.361,
                    "API-02B_p95_ms": 1607.084,
                    "API-02B_p99_ms": 1746.118,
                    "AGG-03C_p95_ms": 1941.346,
                    "AGG-03C_p99_ms": 2096.295,
                },
            ),
            "B_busy": _cell(
                "B_busy",
                "B",
                "BUSY_VERIFIED",
                {
                    "API-00_p95_ms": 211.364,
                    "API-00_p99_ms": 281.12,
                    "SSR-01C_p95_ms": 1336.169,
                    "SSR-01C_p99_ms": 2119.583,
                    "API-01C_p95_ms": 376.254,
                    "API-01C_p99_ms": 407.982,
                    "AGG-03B_p95_ms": 2402.162,
                    "AGG-03B_p99_ms": 4241.084,
                    "API-02B_p95_ms": 1802.542,  # p95 <= 1950 OK, but p99 2353.092 > 2200 FAIL!
                    "API-02B_p99_ms": 2353.092,
                    "AGG-03C_p95_ms": 4469.764,
                    "AGG-03C_p99_ms": 5462.526,
                },
            ),
            "B_plus_idle": _cell(
                "B_plus_idle",
                "B_plus",
                "IDLE_VERIFIED",
                {
                    "API-00_p95_ms": 97.304,
                    "API-00_p99_ms": 114.591,
                    "SSR-01C_p95_ms": 693.884,
                    "SSR-01C_p99_ms": 1120.273,
                    "API-01C_p95_ms": 386.056,
                    "API-01C_p99_ms": 419.299,
                    "AGG-03B_p95_ms": 1327.551,
                    "AGG-03B_p99_ms": 1763.899,
                    "API-02B_p95_ms": 1638.817,
                    "API-02B_p99_ms": 1972.159,
                    "AGG-03C_p95_ms": 1985.574,
                    "AGG-03C_p99_ms": 2588.2,
                },
            ),
            "B_plus_busy": _cell(
                "B_plus_busy",
                "B_plus",
                "BUSY_VERIFIED",
                {
                    "API-00_p95_ms": 109.249,
                    "API-00_p99_ms": 156.339,
                    "SSR-01C_p95_ms": 946.066,
                    "SSR-01C_p99_ms": 1524.502,
                    "API-01C_p95_ms": 812.777,
                    "API-01C_p99_ms": 880.147,
                    "AGG-03B_p95_ms": 2697.597,
                    "AGG-03B_p99_ms": 4355.701,
                    "API-02B_p95_ms": 2323.781,
                    "API-02B_p99_ms": 2716.908,
                    "AGG-03C_p95_ms": 4166.733,
                    "AGG-03C_p99_ms": 6440.635,
                },
            ),
        },
    }


class TestEvaluateVpsAbAndG18(unittest.TestCase):
    def test_real_t1a_4cell_schema_p95_and_p99_exact_parity(self):
        doc = _make_real_schema_4cell_doc()
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "04-real.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            res = eval_mod.evaluate_t1a_4cell_ab_report(
                p, expected_digest=SAMPLE_DIGEST
            )

        self.assertTrue(res["diagnostic_valid"])
        self.assertEqual(res["diagnostic_status"], "VALID")
        # Notice B_busy API-02B has p95=1802.542 <= 1950, p99=2353.092 > 2200 -> MUST FAIL!
        self.assertEqual(
            res["config_b_failed_slos"], ["API-00", "AGG-03B", "API-02B", "AGG-03C"]
        )
        self.assertEqual(
            res["config_b_plus_failed_slos"],
            ["API-01C", "AGG-03B", "API-02B", "AGG-03C"],
        )
        self.assertEqual(res["recommended_profile"], "REMEDIATION_REQUIRED")
        self.assertFalse(res["overall_passed"])

    def test_all_green_config_b_plus_selected_when_b_slips_and_bp_passes(self):
        doc = _make_real_schema_4cell_doc()
        bp_busy = doc["cells"]["B_plus_busy"]["slo_metrics_summary"]
        bp_busy["API-01C_p95_ms"] = 610.0
        bp_busy["AGG-03B_p95_ms"] = 1420.0
        bp_busy["AGG-03B_p99_ms"] = 1810.0
        bp_busy["API-02B_p95_ms"] = 1520.0
        bp_busy["API-02B_p99_ms"] = 1790.0
        bp_busy["AGG-03C_p95_ms"] = 2120.0
        bp_busy["AGG-03C_p99_ms"] = 2650.0
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "04-bp-win.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            res = eval_mod.evaluate_t1a_4cell_ab_report(
                p, expected_digest=SAMPLE_DIGEST
            )

        self.assertTrue(res["diagnostic_valid"])
        self.assertEqual(res["config_b_plus_failed_slos"], [])
        self.assertEqual(
            res["recommended_profile"], "staging_vps_capped_b_plus_2_5vcpu"
        )
        self.assertEqual(
            res["recommended_config_spec_sha256"], eval_mod.CONFIG_B_PLUS_SPEC_SHA256
        )
        self.assertTrue(res["overall_passed"])

    def test_error_count_in_wave_summary_and_list_waves_fail_closed(self):
        doc = _make_real_schema_4cell_doc()
        for cid in ("B_idle", "B_busy", "B_plus_idle", "B_plus_busy"):
            sm = doc["cells"][cid]["slo_metrics_summary"]
            for k in list(sm.keys()):
                sm[k] = 80.0
        # Convert `B_plus_busy.waves` to list form AND inject `error_count = 3`
        waves_list = list(doc["cells"]["B_plus_busy"]["waves"].values())
        waves_list[0] = copy.deepcopy(waves_list[0])
        waves_list[0]["summary"]["error_count"] = 3
        doc["cells"]["B_plus_busy"]["waves"] = waves_list
        # Also make B_busy slip p95 so B_plus would otherwise be chosen
        doc["cells"]["B_busy"]["slo_metrics_summary"]["API-00_p95_ms"] = 250.0

        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "04-err-cnt.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            res = eval_mod.evaluate_t1a_4cell_ab_report(
                p, expected_digest=SAMPLE_DIGEST
            )

        self.assertEqual(res["cell_diagnostics"]["B_plus_busy"]["http_errors"], 3)
        self.assertFalse(res["overall_passed"])
        self.assertTrue(
            any(
                "CELL_HTTP_ERRORS:B_plus_busy:error_count=3" in f
                for f in res["findings"]
            )
        )

    def test_cell_and_top_level_diagnostic_invalid_fail_closed(self):
        doc = _make_real_schema_4cell_doc()
        # Simulate `DiagnosticInvalidError` cell exactly as emitted by `run_b_vs_bplus_dirtycount_diag.py`
        doc["cells"]["B_busy"] = {
            "cell_id": "B_busy",
            "config_id": "B",
            "status": "DIAGNOSTIC_INVALID",
            "error": {
                "code": "QUEUE_SHORT_NOT_OBSERVED_BUSY",
                "details": {"error_count": 5},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "04-cell-invalid.json"
            p.write_text(json.dumps(doc), encoding="utf-8")
            res = eval_mod.evaluate_t1a_4cell_ab_report(
                p, expected_digest=SAMPLE_DIGEST
            )

        self.assertFalse(res["diagnostic_valid"])
        self.assertEqual(res["diagnostic_status"], "DIAGNOSTIC_INVALID")
        self.assertEqual(res["recommended_profile"], "DIAGNOSTIC_INVALID")
        self.assertFalse(res["cell_diagnostics"]["B_busy"]["cell_valid"])
        self.assertEqual(res["cell_diagnostics"]["B_busy"]["http_errors"], 5)
        self.assertFalse(res["overall_passed"])
        self.assertTrue(
            any("CELL_DIAGNOSTIC_INVALID:B_busy" in f for f in res["findings"])
        )

        # Top-level `diagnostic_status == "DIAGNOSTIC_INVALID"` (as in `04a-t1b-v2-full-2x2-...`)
        doc2 = _make_real_schema_4cell_doc()
        doc2["diagnostic_status"] = "DIAGNOSTIC_INVALID"
        with tempfile.TemporaryDirectory() as td:
            p2 = pathlib.Path(td) / "04-top-invalid.json"
            p2.write_text(json.dumps(doc2), encoding="utf-8")
            res2 = eval_mod.evaluate_t1a_4cell_ab_report(
                p2, expected_digest=SAMPLE_DIGEST
            )
        self.assertFalse(res2["diagnostic_valid"])
        self.assertEqual(res2["diagnostic_status"], "DIAGNOSTIC_INVALID")
        self.assertFalse(res2["overall_passed"])
        self.assertTrue(
            any("TOP_LEVEL_DIAGNOSTIC_INVALID" in f for f in res2["findings"])
        )


if __name__ == "__main__":
    unittest.main()
