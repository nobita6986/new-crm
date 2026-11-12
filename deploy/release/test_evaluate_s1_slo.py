"""Unit tests for `evaluate_s1_slo.py`, `p4-s1-slo-spec.json`, and `p4-s1-slo-approval.json`."""

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
    main,
    validate_slo_approval_document,
    validate_slo_spec_governance,
)

RELEASE_DIR = Path(__file__).resolve().parent
CANONICAL_SLO_PATH = RELEASE_DIR / "p4-s1-slo-spec.json"
CANONICAL_APPROVAL_PATH = RELEASE_DIR / "p4-s1-slo-approval.json"
LOCKED_SUBMITTED_SLO_SHA256 = (
    "ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d"
)
LOCKED_CANDIDATE_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "ba68cd229ea3f3dc2566017e13251387b3fe398196acd4880a2a184201a00be8"
)


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
        self.approval_doc = json.loads(
            CANONICAL_APPROVAL_PATH.read_text(encoding="utf-8")
        )

    def test_submitted_slo_spec_sha256_is_immutable(self) -> None:
        self.assertEqual(self.spec_sha, LOCKED_SUBMITTED_SLO_SHA256)
        self.assertEqual(
            self.approval_doc["slo_spec_sha256"], LOCKED_SUBMITTED_SLO_SHA256
        )
        self.assertEqual(
            self.approval_doc["approved_image_digest"], LOCKED_CANDIDATE_DIGEST
        )
        self.assertEqual(self.approval_doc["approval_status"], "PENDING_OWNER_APPROVAL")
        self.assertEqual(self.approval_doc["approved_profiles"], [])

    def test_canonical_slo_spec_and_pending_approval_doc_pass_technical_check(
        self,
    ) -> None:
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
                    approval_doc_path=CANONICAL_APPROVAL_PATH,
                    benchmark_report_path=bench_path,
                    profile=profile,
                )
                self.assertEqual(res["status"], "PASS", res)
                self.assertEqual(res["gate_errors"], [])
                self.assertEqual(res["slo_approval_status"], "PENDING_OWNER_APPROVAL")
                self.assertFalse(
                    res["approval_verification"]["owner_approved_for_profile"]
                )

    def test_separate_owner_approval_passes_without_mutating_slo_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            bench_capped = tmp_dir / "bench-capped.json"
            bench_4x4 = tmp_dir / "bench-4x4.json"
            out_eval = tmp_dir / "eval-out.json"
            bench_capped.write_text(
                json.dumps(
                    _synthesize_report_from_spec(self.spec, "staging_vps_capped")
                ),
                encoding="utf-8",
            )
            bench_4x4.write_text(
                json.dumps(_synthesize_report_from_spec(self.spec, "s1_canonical_4x4")),
                encoding="utf-8",
            )

            signed_approval = copy.deepcopy(self.approval_doc)
            signed_approval["approval_status"] = "OWNER_APPROVED"
            signed_approval["approved_profiles"] = ["staging_vps_capped"]
            signed_approval["owner_signoff"] = {
                "approved_by": "Owner (T0)",
                "approved_at_utc": "2026-11-12T03:00:00Z",
                "decision_reference": "T0-SLO-APPROVAL-2026-11-12",
            }
            approval_path = tmp_dir / "p4-s1-slo-approval-signed.json"
            approval_path.write_text(json.dumps(signed_approval), encoding="utf-8")

            # Approved profile (`staging_vps_capped`) passes `--require-owner-approved`
            # while `p4-s1-slo-spec.json` keeps its exact submitted SHA-256!
            rc_ok = main(
                [
                    "--slo-spec",
                    str(CANONICAL_SLO_PATH),
                    "--expected-slo-sha256",
                    LOCKED_SUBMITTED_SLO_SHA256,
                    "--approval-doc",
                    str(approval_path),
                    "--benchmark-report",
                    str(bench_capped),
                    "--profile",
                    "staging_vps_capped",
                    "--expected-image-digest",
                    LOCKED_CANDIDATE_DIGEST,
                    "--require-owner-approved",
                    "--out",
                    str(out_eval),
                ]
            )
            self.assertEqual(rc_ok, 0)
            eval_doc = json.loads(out_eval.read_text(encoding="utf-8"))
            self.assertEqual(eval_doc["status"], "PASS")
            self.assertEqual(eval_doc["slo_spec_sha256"], LOCKED_SUBMITTED_SLO_SHA256)
            self.assertEqual(eval_doc["slo_approval_status"], "OWNER_APPROVED")
            self.assertTrue(
                eval_doc["approval_verification"]["owner_approved_for_profile"]
            )

            # Unapproved profile (`s1_canonical_4x4`) fails closed under `--require-owner-approved`
            rc_unapproved_prof = main(
                [
                    "--slo-spec",
                    str(CANONICAL_SLO_PATH),
                    "--expected-slo-sha256",
                    LOCKED_SUBMITTED_SLO_SHA256,
                    "--approval-doc",
                    str(approval_path),
                    "--benchmark-report",
                    str(bench_4x4),
                    "--profile",
                    "s1_canonical_4x4",
                    "--expected-image-digest",
                    LOCKED_CANDIDATE_DIGEST,
                    "--require-owner-approved",
                ]
            )
            self.assertEqual(rc_unapproved_prof, 1)

    def test_approval_doc_scope_and_signoff_tampering_fail_closed(self) -> None:
        base_signed = {
            "schema_version": "cskh-p4-s1-slo-approval-v1",
            "slo_bundle_id": self.spec["slo_bundle_id"],
            "slo_version": self.spec["slo_version"],
            "slo_spec_sha256": self.spec_sha,
            "approved_image_digest": LOCKED_CANDIDATE_DIGEST,
            "approved_profiles": ["s1_canonical_4x4", "staging_vps_capped"],
            "approval_status": "OWNER_APPROVED",
            "owner_signoff": {
                "approved_by": "Owner (T0)",
                "approved_at_utc": "2026-11-12T03:00:00Z",
                "decision_reference": "T0-DEC-01",
            },
        }

        # 1. Mismatched SLO JSON SHA-256
        bad_sha = copy.deepcopy(base_signed)
        bad_sha["slo_spec_sha256"] = "f" * 64
        errs_sha, _ = validate_slo_approval_document(
            bad_sha,
            spec=self.spec,
            actual_slo_sha256=self.spec_sha,
            target_image_digest=LOCKED_CANDIDATE_DIGEST,
            profile="staging_vps_capped",
            require_owner_approved=True,
        )
        self.assertTrue(any("slo_spec_sha256" in e for e in errs_sha), errs_sha)

        # 2. Mismatched image digest
        bad_dig = copy.deepcopy(base_signed)
        bad_dig["approved_image_digest"] = (
            "ghcr.io/nobita6986/crm-cskh@sha256:" + "1" * 64
        )
        errs_dig, _ = validate_slo_approval_document(
            bad_dig,
            spec=self.spec,
            actual_slo_sha256=self.spec_sha,
            target_image_digest=LOCKED_CANDIDATE_DIGEST,
            profile="staging_vps_capped",
            require_owner_approved=True,
        )
        self.assertTrue(any("approved_image_digest" in e for e in errs_dig), errs_dig)

        # 3. Missing signoff metadata or non-UTC timestamp when OWNER_APPROVED
        bad_sig = copy.deepcopy(base_signed)
        bad_sig["owner_signoff"]["decision_reference"] = ""
        bad_sig["owner_signoff"]["approved_at_utc"] = "2026-11-12 10:00:00"
        errs_sig, _ = validate_slo_approval_document(
            bad_sig,
            spec=self.spec,
            actual_slo_sha256=self.spec_sha,
            target_image_digest=LOCKED_CANDIDATE_DIGEST,
            profile="staging_vps_capped",
            require_owner_approved=True,
        )
        self.assertTrue(any("decision_reference" in e for e in errs_sig), errs_sig)
        self.assertTrue(any("approved_at_utc" in e for e in errs_sig), errs_sig)

        # 4. Populated approved_profiles while status is PENDING_OWNER_APPROVAL
        bad_pending = copy.deepcopy(self.approval_doc)
        bad_pending["approved_profiles"] = ["staging_vps_capped"]
        errs_pend, _ = validate_slo_approval_document(
            bad_pending,
            spec=self.spec,
            actual_slo_sha256=self.spec_sha,
            target_image_digest=LOCKED_CANDIDATE_DIGEST,
            profile="staging_vps_capped",
            require_owner_approved=False,
        )
        self.assertTrue(
            any("PENDING_OWNER_APPROVAL" in e for e in errs_pend), errs_pend
        )

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
                approval_doc_path=CANONICAL_APPROVAL_PATH,
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
                approval_doc_path=CANONICAL_APPROVAL_PATH,
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

            # Pending Owner approval in separate doc must fail when `--require-owner-approved` is active
            res_unapproved = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256=self.spec_sha,
                approval_doc_path=CANONICAL_APPROVAL_PATH,
                benchmark_report_path=bench_path,
                profile="staging_vps_capped",
                require_owner_approved=True,
            )
            self.assertEqual(res_unapproved["status"], "FAIL")
            self.assertTrue(
                any("OWNER_APPROVED" in e for e in res_unapproved["gate_errors"]),
                res_unapproved["gate_errors"],
            )

            # Missing approval doc must fail when `--require-owner-approved` is active
            res_missing_doc = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256=self.spec_sha,
                approval_doc_path=None,
                benchmark_report_path=bench_path,
                profile="staging_vps_capped",
                require_owner_approved=True,
            )
            self.assertEqual(res_missing_doc["status"], "FAIL")

            # Wrong image digest fails closed
            res_wrong_digest = evaluate_slo_bundle(
                slo_spec_path=CANONICAL_SLO_PATH,
                expected_slo_sha256=self.spec_sha,
                approval_doc_path=CANONICAL_APPROVAL_PATH,
                benchmark_report_path=bench_path,
                profile="staging_vps_capped",
                expected_image_digest="ghcr.io/nobita6986/crm-cskh@sha256:" + "a" * 64,
            )
            self.assertEqual(res_wrong_digest["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
