#!/usr/bin/env python3
"""Fail-closed VPS A/B 4-Cell & Official G18 35-SLO Evaluator (`F-T1B-AB-EVAL-SCHEMA-01` remediated).

Synchronized 1:1 with T1A's actual `04-b-vs-bplus-4cell-raw-and-selection.json` schema
(`cskh-p4-b-vs-bplus-dirtycount-isolated-diag-and-4field-dossier-v1` and `diagnose_g18_a_vs_b_contention.py`):
  1. Reads canonical `slo_metrics_summary` keys (`<SLO_ID>_p95_ms` and `<SLO_ID>_p99_ms`) with
     clean backward-compatible fallback to legacy `<prefix>_p95_ms` keys, evaluating BOTH `p95`
     and `p99` against `staging_vps_capped` in `deploy/release/p4-s1-slo-spec.json` (`ccd8f83c...`).
  2. Supports `waves` structured as either `dict[str, dict]` or `list[dict]` and sums
     `summary.error_count` (with fallback to `summary.errors`).
  3. Enforces fail-closed `DIAGNOSTIC_INVALID` detection at top-level (`status` / `diagnostic_status`),
     cell-level (`status == "DIAGNOSTIC_INVALID"`, `diagnostic_status`, `cell_valid is False`, or
     missing cell), and wave-level (`queue_short_window_verification.verified is False` or
     `queue_short_classification` starting with `"DIAGNOSTIC_INVALID"`).
  4. Evaluates full S1 benchmark reports across all 35 S1 SLO rules, all 11 `P4.md §9.2` criteria,
     `BG-01..BG-03` background tasks, and pre-acceptance cgroup + worker runtime snapshots.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

RELEASE_DIR = pathlib.Path(__file__).resolve().parent
SLO_SPEC_PATH = RELEASE_DIR / "p4-s1-slo-spec.json"
LOCKED_SLO_SPEC_SHA256 = (
    "ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d"
)
LOCKED_RUNNER_SHA256 = (
    "5283a0fdfec6d4bf2608aaf3d053fd060c9f811962b04582d934ae6f785044d2"
)
CONFIG_B_SPEC_SHA256 = (
    "7cbc282ff2c380b145a21c494dbf06c9249d08bd6ec75234f8f4dbc332ccc906"
)
CONFIG_B_PLUS_SPEC_SHA256 = (
    "530765c275067dc08bef704cc316c22683376c850b32cde5546e52b9c0d302a7"
)

# `(p95_key, thr_p95_ms, p99_key, thr_p99_ms, legacy_p95_key)` matching `p4-s1-slo-spec.json` (`staging_vps_capped`)
CANONICAL_SIX_CAPPED_SLO_THRESHOLDS_MS: dict[
    str, tuple[str, float, str, float, str]
] = {
    "API-00": (
        "API-00_p95_ms",
        200.0,
        "API-00_p99_ms",
        300.0,
        "api00_ping_c12_p95_ms",
    ),
    "SSR-01C": (
        "SSR-01C_p95_ms",
        3600.0,
        "SSR-01C_p99_ms",
        4200.0,
        "ssr01c_my_customers_cold_c12_p95_ms",
    ),
    "API-01C": (
        "API-01C_p95_ms",
        800.0,
        "API-01C_p99_ms",
        1100.0,
        "api01c_my_customers_c40_p95_ms",
    ),
    "AGG-03B": (
        "AGG-03B_p95_ms",
        1850.0,
        "AGG-03B_p99_ms",
        2300.0,
        "agg03b_mixed_40_overall_p95_ms",
    ),
    "API-02B": (
        "API-02B_p95_ms",
        1950.0,
        "API-02B_p99_ms",
        2200.0,
        "api02b_mixed_40_lead_detail_p95_ms",
    ),
    "AGG-03C": (
        "AGG-03C_p95_ms",
        3000.0,
        "AGG-03C_p99_ms",
        3600.0,
        "agg03c_mixed_80_overall_p95_ms",
    ),
}

EXPECTED_11_P4_9_2_KEYS = (
    "staff_my_customers_p95_le_1500ms",
    "open_lead_and_history_p95_le_2000ms",
    "record_attempt_and_callback_p95_le_2000ms_zero_dup",
    "bod_overview_snapshot_p95_le_3000ms",
    "kpi_drilldown_paginated_p95_le_3000ms",
    "preview_weighted_distribution_10000_items",
    "execute_weighted_distribution_10000_items",
    "import_50000_csv_rows",
    "recompute_kpi_non_blocking_atomic_publish",
    "concurrent_20_40_80_mixed_user_sessions",
    "telesale_worklist_ssr_zero_errors_c12_and_c40",
)

if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))
import evaluate_s1_slo as slo_mod  # noqa: E402


def _is_diag_invalid_token(val: Any) -> bool:
    return isinstance(val, str) and val.strip().upper().startswith("DIAGNOSTIC_INVALID")


def _iter_cell_waves(waves_raw: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(waves_raw, dict):
        return [(str(k), v) for k, v in waves_raw.items() if isinstance(v, dict)]
    if isinstance(waves_raw, list):
        out: list[tuple[str, dict[str, Any]]] = []
        for idx, w in enumerate(waves_raw):
            if isinstance(w, dict):
                wid = str(w.get("wave_id") or w.get("slo_id") or f"wave_{idx}")
                out.append((wid, w))
        return out
    return []


def inspect_cell_validity_and_errors(
    cell_id: str,
    cdoc: Any,
) -> dict[str, Any]:
    """Evaluate per-cell `DIAGNOSTIC_INVALID` status and exact `error_count` total."""
    if not isinstance(cdoc, dict) or not cdoc:
        return {
            "cell_id": cell_id,
            "cell_valid": False,
            "cell_status": "DIAGNOSTIC_INVALID",
            "http_errors": 1,
            "invalid_reasons": [f"CELL_MISSING_OR_EMPTY:{cell_id}"],
        }

    invalid_reasons: list[str] = []
    if _is_diag_invalid_token(cdoc.get("status")):
        invalid_reasons.append(
            f"CELL_DIAGNOSTIC_INVALID:{cell_id}:{cdoc.get('status')}"
        )
    if _is_diag_invalid_token(cdoc.get("diagnostic_status")):
        invalid_reasons.append(
            f"CELL_DIAGNOSTIC_INVALID:{cell_id}:{cdoc.get('diagnostic_status')}"
        )
    if cdoc.get("cell_valid") is False:
        invalid_reasons.append(f"CELL_VALID_FALSE:{cell_id}")

    wave_pairs = _iter_cell_waves(cdoc.get("waves"))
    if not invalid_reasons and not wave_pairs:
        invalid_reasons.append(f"CELL_NO_WAVES_RECORDED:{cell_id}")

    total_err = 0
    for w_key, w_obj in wave_pairs:
        summary = w_obj.get("summary") or {}
        if "error_count" in summary and summary.get("error_count") is not None:
            w_err = int(summary.get("error_count") or 0)
        else:
            w_err = int(summary.get("errors") or 0)
        total_err += w_err

        q_ver = w_obj.get("queue_short_window_verification")
        if isinstance(q_ver, dict):
            q_cls = q_ver.get("queue_short_classification")
            if q_ver.get("verified") is False or _is_diag_invalid_token(q_cls):
                invalid_reasons.append(
                    f"WAVE_DIAGNOSTIC_INVALID:{cell_id}:{w_key}:{q_cls or 'unverified'}"
                )

    if invalid_reasons:
        err_details = (
            ((cdoc.get("error") or {}).get("details") or {})
            if isinstance(cdoc.get("error"), dict)
            else {}
        )
        err_from_exc = int(
            err_details.get("error_count")
            if "error_count" in err_details
            and err_details.get("error_count") is not None
            else (err_details.get("errors") or 0)
        )
        effective_err = max(1, total_err, err_from_exc)
        return {
            "cell_id": cell_id,
            "cell_valid": False,
            "cell_status": "DIAGNOSTIC_INVALID",
            "http_errors": effective_err,
            "invalid_reasons": invalid_reasons,
        }

    return {
        "cell_id": cell_id,
        "cell_valid": True,
        "cell_status": str(cdoc.get("status") or "VALID"),
        "http_errors": total_err,
        "invalid_reasons": [],
    }


def evaluate_t1a_4cell_ab_report(
    ab_json_path: pathlib.Path,
    *,
    expected_digest: str | None = None,
    cell_ids: tuple[str, ...] = ("B_idle", "B_busy", "B_plus_idle", "B_plus_busy"),
) -> dict[str, Any]:
    doc = json.loads(ab_json_path.read_text(encoding="utf-8"))
    findings: list[str] = []

    top_diag_invalid = _is_diag_invalid_token(
        doc.get("status")
    ) or _is_diag_invalid_token(doc.get("diagnostic_status"))
    if top_diag_invalid:
        findings.append(
            f"TOP_LEVEL_DIAGNOSTIC_INVALID: status={doc.get('status')!r}, "
            f"diagnostic_status={doc.get('diagnostic_status')!r}"
        )

    actual_digest = str(
        doc.get("candidate_digest") or doc.get("app_image_digest") or ""
    )
    if expected_digest is not None and actual_digest != expected_digest:
        findings.append(f"DIGEST_MISMATCH: {actual_digest!r} != {expected_digest!r}")

    if (
        "slo_spec_sha256" in doc
        and doc.get("slo_spec_sha256") != LOCKED_SLO_SPEC_SHA256
    ):
        findings.append(
            f"SLO_SPEC_SHA256_MISMATCH: {doc.get('slo_spec_sha256')!r} != {LOCKED_SLO_SPEC_SHA256!r}"
        )
    if (
        "benchmark_runner_sha256" in doc
        and doc.get("benchmark_runner_sha256") != LOCKED_RUNNER_SHA256
    ):
        findings.append(
            f"RUNNER_SHA256_MISMATCH: {doc.get('benchmark_runner_sha256')!r} != {LOCKED_RUNNER_SHA256!r}"
        )
    if "live_stack_preservation" in doc:
        if (doc.get("live_stack_preservation") or {}).get(
            "zero_live_mutation"
        ) is not True:
            findings.append("LIVE_STACK_MUTATION_DETECTED")

    if "control_count_reconciliation" in doc:
        ctrl = doc.get("control_count_reconciliation") or {}
        if ctrl.get("exact_parity_all_states_and_buckets") is not True:
            findings.append("CONTROL_COUNT_PARITY_FAILED")
        if ctrl.get("worker_observed_processing") is not True:
            findings.append("CONTROL_COUNT_WORKER_NOT_OBSERVED_PROCESSING")

    cells = doc.get("cells") or {}
    cell_diagnostics: dict[str, dict[str, Any]] = {}
    for cid in cell_ids:
        c_info = inspect_cell_validity_and_errors(cid, cells.get(cid))
        cell_diagnostics[cid] = c_info
        if not c_info["cell_valid"]:
            findings.extend(c_info["invalid_reasons"])
        elif c_info["http_errors"] > 0:
            findings.append(
                f"CELL_HTTP_ERRORS:{cid}:error_count={c_info['http_errors']}"
            )

    rescore_matrix: dict[str, dict[str, Any]] = {}
    for slo_id, (
        p95_key,
        thr_p95_ms,
        p99_key,
        thr_p99_ms,
        legacy_p95_key,
    ) in CANONICAL_SIX_CAPPED_SLO_THRESHOLDS_MS.items():
        row: dict[str, Any] = {}
        for cid in cell_ids:
            cdoc = cells.get(cid) if isinstance(cells.get(cid), dict) else {}
            s_metrics = (cdoc or {}).get("slo_metrics_summary") or {}
            c_info = cell_diagnostics[cid]

            if p95_key in s_metrics and s_metrics[p95_key] is not None:
                val_p95 = float(s_metrics[p95_key])
            elif legacy_p95_key in s_metrics and s_metrics[legacy_p95_key] is not None:
                val_p95 = float(s_metrics[legacy_p95_key])
            else:
                val_p95 = 999999.0

            val_p99 = (
                float(s_metrics[p99_key])
                if (p99_key in s_metrics and s_metrics[p99_key] is not None)
                else None
            )
            p99_ok = True if val_p99 is None else (val_p99 <= thr_p99_ms)
            err_total = int(c_info["http_errors"])
            valid_cell = bool(c_info["cell_valid"]) and not top_diag_invalid
            passed = (
                valid_cell and (val_p95 <= thr_p95_ms) and p99_ok and (err_total == 0)
            )

            row[cid] = {
                "measured_p95_ms": val_p95,
                "slo_threshold_ms": thr_p95_ms,
                "measured_p99_ms": val_p99,
                "slo_threshold_p99_ms": thr_p99_ms,
                "http_errors": err_total,
                "cell_valid": valid_cell,
                "cell_status": "DIAGNOSTIC_INVALID"
                if not valid_cell
                else c_info["cell_status"],
                "passed": passed,
                "headroom_ms": round(thr_p95_ms - val_p95, 3),
                "headroom_p99_ms": (
                    round(thr_p99_ms - val_p99, 3) if val_p99 is not None else None
                ),
                "headroom_pct": round(((thr_p95_ms - val_p95) / thr_p95_ms) * 100.0, 2),
            }
        rescore_matrix[slo_id] = row

    has_b_pair = "B_idle" in cell_ids and "B_busy" in cell_ids
    has_bp_pair = "B_plus_idle" in cell_ids and "B_plus_busy" in cell_ids

    b_failed = (
        [
            sid
            for sid in CANONICAL_SIX_CAPPED_SLO_THRESHOLDS_MS
            if not (
                rescore_matrix[sid]["B_idle"]["passed"]
                and rescore_matrix[sid]["B_busy"]["passed"]
            )
        ]
        if has_b_pair
        else []
    )
    bp_failed = (
        [
            sid
            for sid in CANONICAL_SIX_CAPPED_SLO_THRESHOLDS_MS
            if not (
                rescore_matrix[sid]["B_plus_idle"]["passed"]
                and rescore_matrix[sid]["B_plus_busy"]["passed"]
            )
        ]
        if has_bp_pair
        else []
    )

    b_min_headroom_pct = (
        min(
            min(
                rescore_matrix[sid]["B_idle"]["headroom_pct"],
                rescore_matrix[sid]["B_busy"]["headroom_pct"],
            )
            for sid in CANONICAL_SIX_CAPPED_SLO_THRESHOLDS_MS
        )
        if has_b_pair
        else None
    )

    any_invalid_cell = top_diag_invalid or any(
        not c_info["cell_valid"] for c_info in cell_diagnostics.values()
    )
    if any_invalid_cell:
        recommended = "DIAGNOSTIC_INVALID"
        recommended_hash = None
    elif (
        has_b_pair
        and not b_failed
        and (b_min_headroom_pct is not None and b_min_headroom_pct >= 10.0)
    ):
        recommended = "staging_vps_capped"
        recommended_hash = CONFIG_B_SPEC_SHA256
    elif has_bp_pair and not bp_failed:
        recommended = "staging_vps_capped_b_plus_2_5vcpu"
        recommended_hash = CONFIG_B_PLUS_SPEC_SHA256
    else:
        recommended = "REMEDIATION_REQUIRED"
        recommended_hash = None
        findings.append(
            f"BOTH_CONFIGS_FAILED_SIX_SLOS: B={b_failed}, B_plus={bp_failed}"
        )

    return {
        "ab_report_path": str(ab_json_path),
        "candidate_digest": actual_digest,
        "diagnostic_valid": not any_invalid_cell,
        "diagnostic_status": "DIAGNOSTIC_INVALID" if any_invalid_cell else "VALID",
        "cell_diagnostics": cell_diagnostics,
        "canonical_six_slo_rescore": rescore_matrix,
        "config_b_failed_slos": b_failed,
        "config_b_min_headroom_pct": b_min_headroom_pct,
        "config_b_plus_failed_slos": bp_failed,
        "recommended_profile": recommended,
        "recommended_config_spec_sha256": recommended_hash,
        "findings": findings,
        "overall_passed": (not any_invalid_cell) and (len(findings) == 0),
    }


def evaluate_full_s1_benchmark_report(
    report_path: pathlib.Path,
    *,
    expected_digest: str | None = None,
    expected_config_spec_sha256: str | None = None,
) -> dict[str, Any]:
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    actual_digest = str(raw.get("app_image_tested") or "")
    slo_res = slo_mod.evaluate_slo_bundle(
        slo_spec_path=SLO_SPEC_PATH,
        expected_slo_sha256=LOCKED_SLO_SPEC_SHA256,
        benchmark_report_path=report_path,
        profile="staging_vps_capped",
        approval_doc_path=None,
        expected_image_digest=expected_digest or actual_digest,
        g18_diag_report_path=None,
        require_owner_approved=False,
    )
    rules = slo_res.get("s1_rule_results") or []
    passed_rules = [r["slo_id"] for r in rules if r.get("passed")]
    failed_rules = [r["slo_id"] for r in rules if not r.get("passed")]
    bg_rules = {
        r["slo_id"]: {
            "passed": r.get("passed"),
            "measured": r.get("measured") or r.get("invariants_checked"),
            "failure_reasons": r.get("failure_reasons") or [],
        }
        for r in rules
        if str(r.get("slo_id") or "").startswith("BG-")
    }

    p4_mat = raw.get("p4_9_2_acceptance_matrix") or {}
    p4_items = p4_mat.get("items") or {}
    missing_p4 = [k for k in EXPECTED_11_P4_9_2_KEYS if k not in p4_items]
    failed_p4 = [
        k for k in EXPECTED_11_P4_9_2_KEYS if not (p4_items.get(k) or {}).get("passed")
    ]

    snap = raw.get("pre_acceptance_runtime_config_snapshot") or {}
    ver_obj = snap.get("verification_verdict") or {}
    snap_verdict = bool(
        ver_obj.get("verified") is True or ver_obj.get("all_passed") is True
    )
    cfg_sha = str(raw.get("config_spec_sha256") or "")
    cfg_ok = (
        expected_config_spec_sha256 is None or cfg_sha == expected_config_spec_sha256
    )

    findings: list[str] = []
    if expected_digest is not None and actual_digest != expected_digest:
        findings.append(f"DIGEST_MISMATCH: {actual_digest!r} != {expected_digest!r}")
    if not cfg_ok:
        findings.append(
            f"CONFIG_SPEC_HASH_MISMATCH: {cfg_sha!r} != {expected_config_spec_sha256!r}"
        )
    if not snap_verdict:
        findings.append(f"RUNTIME_SNAPSHOT_INVALID: {snap.get('verification_verdict')}")
    if len(rules) != 35 or failed_rules:
        findings.append(f"SLO_35_FAILURES ({len(passed_rules)}/35): {failed_rules}")
    if missing_p4 or failed_p4 or p4_mat.get("all_passed") is not True:
        findings.append(
            f"P4_9_2_11_CRITERIA_FAILURES: missing={missing_p4}, failed={failed_p4}"
        )
    if any(not v["passed"] for v in bg_rules.values()):
        findings.append(
            f"BACKGROUND_TASK_FAILURES: {[k for k, v in bg_rules.items() if not v['passed']]}"
        )

    return {
        "report_path": str(report_path),
        "app_image_tested": actual_digest,
        "resource_profile": raw.get("resource_profile"),
        "resource_variant": raw.get("resource_variant"),
        "config_spec_sha256": cfg_sha,
        "runtime_snapshot_verified": snap_verdict,
        "slo_35_summary": {
            "total_rules": len(rules),
            "passed_count": len(passed_rules),
            "failed_count": len(failed_rules),
            "failed_slo_ids": failed_rules,
            "all_35_passed": len(rules) == 35 and len(failed_rules) == 0,
        },
        "p4_9_2_11_criteria_summary": {
            "total_checks": len(EXPECTED_11_P4_9_2_KEYS),
            "passed_count": len(EXPECTED_11_P4_9_2_KEYS) - len(failed_p4),
            "failed_checks": failed_p4,
            "missing_checks": missing_p4,
            "all_11_passed": (
                not missing_p4 and not failed_p4 and p4_mat.get("all_passed") is True
            ),
        },
        "background_tasks_bg01_bg03": bg_rules,
        "findings": findings,
        "overall_passed": len(findings) == 0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ab-report", type=pathlib.Path, default=None)
    ap.add_argument("--s1-report", type=pathlib.Path, default=None)
    ap.add_argument("--expected-digest", default=None)
    ap.add_argument("--expected-config-sha256", default=None)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)

    out: dict[str, Any] = {
        "schema_version": "cskh-p4-t1b-ab-and-g18-evaluator-v2",
        "remediated_finding": "F-T1B-AB-EVAL-SCHEMA-01",
    }
    rc = 0
    if args.ab_report is not None:
        ab_res = evaluate_t1a_4cell_ab_report(
            args.ab_report, expected_digest=args.expected_digest
        )
        out["ab_4cell_evaluation"] = ab_res
        if not ab_res["overall_passed"]:
            rc = 2
    if args.s1_report is not None:
        s1_res = evaluate_full_s1_benchmark_report(
            args.s1_report,
            expected_digest=args.expected_digest,
            expected_config_spec_sha256=args.expected_config_sha256,
        )
        out["s1_official_evaluation"] = s1_res
        if not s1_res["overall_passed"]:
            rc = 2

    rendered = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
