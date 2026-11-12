"""Unit tests for `deploy/release/evaluate_s1_slo.py` and `deploy/release/p4-s1-slo-spec.json`."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluate_s1_slo import (
    compute_normalized_sha256,
    evaluate_slo_bundle,
    validate_slo_spec_governance,
)

RELEASE_DIR = Path(__file__).resolve().parent
CANONICAL_SLO_PATH = RELEASE_DIR / "p4-s1-slo-spec.json"


def _set_dotted(doc: dict[str, Any], dotted_path: str, val: Any) -> None:
    parts = dotted_path.split(".")
    cur = doc
    for seg in parts[:-1]:
        cur = cur.setdefault(seg, {})
    cur[parts[-1]] = val


def _synthesize_report_from_spec(spec: dict[str, Any], profile: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "app_image_tested": spec["candidate_binding"]["app_image_digest"],
        "resource_profile": profile,
        "live_resource_headroom_precheck": {"headroom_ok": True},
    }
    for rule in spec["rules"]:
        if rule.get("rule_type", "http_summary") == "http_summary":
            _set_dotted(
                report,
                rule["report_json_path"],
                copy.deepcopy(rule["measured"][profile]),
            )
        for inv in rule["proposed_thresholds"][profile].get("invariants") or []:
            _set_dotted(report, inv["report_json_path"], inv["expected"])
    return report


class EvaluateS1SloTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = json.loads(CANONICAL_SLO_PATH.read_text(encoding="utf-8"))
        self.spec_sha = compute_normalized_sha256(CANONICAL_SLO_PATH)

    def test_canonical_slo_spec_governance_and_both_profiles_pass(self) -> None:
        for profile in ("s1_canonical_4x4", "staging_vps_capped"):
            errs = validate_slo_spec_governance(self.spec, profile=profile)
            self.assertEqual(
                errs, [], f"Unexpected governance errors on {profile}: {errs}"
            )

            with tempfile.TemporaryDirectory() as tmp:
                bench_path = Path(tmp) / f"bench-{profile}.json"
                bench_doc = _synthesize_report_from_spec(self.spec, profile)
                bench_path.write_text(json.dumps(bench_doc), encoding="utf-8")

                res = evaluate_slo_bundle(
                    slo_spec_path=CANONICAL_SLO_PATH,
                    expected_slo_sha256=self.spec_sha,
                    benchmark_report_path=bench_path,
                    profile=profile,
                )
                self.assertEqual(res["status"], "PASS", res)
                self.assertEqual(res["gate_errors"], [])

    def test_wrong_slo_sha256_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench_path = Path(tmp) / "bench.json"
            bench_path.write_text(
                json.dumps(_synthesize_report_from_spec(self.spec, "s1_canonical_4x4")),
                encoding="utf-8",
            )
            res = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256="0" * 64,
                benchmark_report_path=bench_path,
                profile="s1_canonical_4x4",
            )
            self.assertEqual(res["status"], "FAIL")
            self.assertTrue(
                any("SHA-256 mismatch" in e for e in res["gate_errors"]),
                res["gate_errors"],
            )

    def test_excluding_either_drilldown_variant_fails_closed(self) -> None:
        for variant in (
            "kpi_metric_drilldown_250k_api",
            "kpi_metric_drilldown_monthly_company_api",
        ):
            tampered = copy.deepcopy(self.spec)
            tampered["rules"] = [
                r for r in tampered["rules"] if r.get("drilldown_variant") != variant
            ]
            errs = validate_slo_spec_governance(tampered, profile="s1_canonical_4x4")
            self.assertTrue(
                any(variant in e for e in errs),
                f"Expected missing variant error for {variant}, got {errs}",
            )

    def test_inflating_threshold_above_p4_ceiling_fails_closed(self) -> None:
        tampered = copy.deepcopy(self.spec)
        for r in tampered["rules"]:
            if r["slo_id"] == "API-05B-C40":
                r["proposed_thresholds"]["staging_vps_capped"]["max_p95_ms"] = 3500.0
        errs = validate_slo_spec_governance(tampered, profile="staging_vps_capped")
        self.assertTrue(
            any("threshold inflation forbidden" in e for e in errs),
            errs,
        )

    def test_endpoint_failure_is_never_masked_by_passing_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench_doc = _synthesize_report_from_spec(self.spec, "staging_vps_capped")
            # Aggregate passes, but monthly_company drilldown times out once
            dd_node = bench_doc["peak_40_staff_concurrent_wave"]["scenario_summaries"][
                "kpi_metric_drilldown_monthly_company_api"
            ]
            dd_node["timeout_count"] = 1
            dd_node["error_count"] = 1
            dd_node["error_rate_pct"] = 0.625

            bench_path = Path(tmp) / "bench.json"
            bench_path.write_text(json.dumps(bench_doc), encoding="utf-8")
            res = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256=self.spec_sha,
                benchmark_report_path=bench_path,
                profile="staging_vps_capped",
            )
            self.assertEqual(res["status"], "FAIL")
            self.assertTrue(
                res["category_verdicts"]["AGGREGATE_AND_BACKGROUND"]["passed"]
            )
            self.assertFalse(res["category_verdicts"]["JSON_API"]["passed"])

    def test_digest_mismatch_and_owner_approval_gate_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench_doc = _synthesize_report_from_spec(self.spec, "staging_vps_capped")
            bench_path = Path(tmp) / "bench.json"
            bench_path.write_text(json.dumps(bench_doc), encoding="utf-8")

            # Pending Owner approval must fail when --require-owner-approved is active
            res_unapproved = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256=self.spec_sha,
                benchmark_report_path=bench_path,
                profile="staging_vps_capped",
                require_owner_approved=True,
            )
            self.assertEqual(res_unapproved["status"], "FAIL")
            self.assertTrue(
                any("OWNER_APPROVED" in e for e in res_unapproved["gate_errors"])
            )

            # Wrong image digest fails closed
            res_wrong_digest = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256=self.spec_sha,
                benchmark_report_path=bench_path,
                profile="staging_vps_capped",
                expected_image_digest="ghcr.io/nobita6986/crm-cskh@sha256:" + "a" * 64,
            )
            self.assertEqual(res_wrong_digest["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
