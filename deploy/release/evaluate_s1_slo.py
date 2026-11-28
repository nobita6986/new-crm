"""Fail-closed SLO scorer for P4 S1 & G18 isolated benchmark reports (`T1A` / `T1C` / `Owner`).

Governance model:
- `deploy/release/p4-s1-slo-spec.json` is the immutable versioned threshold specification
  submitted for review; its bytes and SHA-256 digest are NEVER modified when Owner signs off.
- `deploy/release/p4-s1-slo-approval.json` is the separate Owner approval record bound
  cryptographically to `slo_spec_sha256`, `approved_image_digest`, and `approved_profiles`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_SCHEMA_VERSION = "cskh-p4-s1-slo-spec-v1"
EXPECTED_APPROVAL_SCHEMA_VERSION = "cskh-p4-s1-slo-approval-v1"
VALID_APPROVAL_STATUSES = ("PENDING_OWNER_APPROVAL", "OWNER_APPROVED")
REQUIRED_CATEGORIES = ("SSR", "JSON_API", "AGGREGATE_AND_BACKGROUND")
REQUIRED_DRILLDOWN_VARIANTS = (
    "kpi_metric_drilldown_250k_api",
    "kpi_metric_drilldown_monthly_company_api",
)
SUPPORTED_PROFILES = ("s1_canonical_4x4", "staging_vps_capped")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
DIGEST_URI_RE = re.compile(r"^ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")


def compute_normalized_sha256(path: Path) -> str:
    """Compute SHA-256 over LF-normalized UTF-8 bytes so Linux and Windows match bit-for-bit."""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _parse_utc_iso8601(raw: str) -> datetime | None:
    val = raw.strip()
    if not val:
        return None
    normalized = val[:-1] + "+00:00" if val.endswith("Z") else val
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() != timezone.utc.utcoffset(dt):
        return None
    return dt


def resolve_dotted_path(doc: dict[str, Any], dotted_path: str) -> Any:
    cur: Any = doc
    for segment in dotted_path.split("."):
        if not isinstance(cur, dict) or segment not in cur:
            raise KeyError(f"Missing JSON path {dotted_path!r} (at {segment!r})")
        cur = cur[segment]
    return cur


def _check_invariant(report: dict[str, Any], inv: dict[str, Any]) -> tuple[bool, str]:
    path = str(inv.get("report_json_path") or "")
    op = str(inv.get("op") or "")
    expected = inv.get("expected")
    try:
        actual = resolve_dotted_path(report, path)
    except KeyError as exc:
        return False, str(exc)

    if op == "eq":
        ok = actual == expected
    elif op == "le":
        ok = (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and float(actual) <= float(expected)
        )
    elif op == "ge":
        ok = (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and float(actual) >= float(expected)
        )
    else:
        return False, f"Unsupported invariant operator {op!r} on {path}"

    if ok:
        return True, ""
    return False, f"Invariant {path} ({actual!r}) failed {op} {expected!r}"


def validate_slo_spec_governance(
    spec: dict[str, Any],
    *,
    profile: str,
) -> list[str]:
    errors: list[str] = []
    if spec.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        errors.append(
            f"Invalid schema_version: {spec.get('schema_version')!r} != {EXPECTED_SCHEMA_VERSION!r}"
        )
    slo_ver = str(spec.get("slo_version") or "").strip()
    if not slo_ver:
        errors.append("Missing non-empty `slo_version` in SLO spec")

    if profile not in SUPPORTED_PROFILES:
        errors.append(f"Unsupported profile {profile!r}; expected {SUPPORTED_PROFILES}")

    gov = spec.get("governance_invariants") or {}
    if not isinstance(gov, dict):
        errors.append("Missing `governance_invariants` object in SLO spec")
    else:
        for flag in (
            "no_endpoint_exclusion",
            "no_aggregate_compensation_for_endpoints",
            "no_threshold_inflation_above_p4_9_2_ceiling",
        ):
            if gov.get(flag) is not True:
                errors.append(f"Governance invariant `{flag}` must be true")

    rules = spec.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("SLO spec `rules` must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    seen_categories: set[str] = set()
    seen_drilldown_variants: set[str] = set()

    for rule in rules:
        if not isinstance(rule, dict):
            errors.append("Every entry in `rules` must be an object")
            continue
        slo_id = str(rule.get("slo_id") or "").strip()
        if not slo_id:
            errors.append("Rule missing `slo_id`")
            continue
        if slo_id in seen_ids:
            errors.append(f"Duplicate slo_id {slo_id!r}")
        seen_ids.add(slo_id)

        cat = str(rule.get("category") or "")
        seen_categories.add(cat)
        dd_var = str(rule.get("drilldown_variant") or "")
        if dd_var:
            seen_drilldown_variants.add(dd_var)

        thr_by_prof = rule.get("proposed_thresholds") or {}
        if not isinstance(thr_by_prof, dict) or profile not in thr_by_prof:
            errors.append(f"Rule {slo_id}: missing thresholds for profile {profile!r}")
            continue
        prof_thr = thr_by_prof[profile]
        ceiling = rule.get("p4_9_2_ceiling_p95_ms")
        if ceiling is not None and "max_p95_ms" in prof_thr:
            if float(prof_thr["max_p95_ms"]) > float(ceiling):
                errors.append(
                    f"Rule {slo_id}: max_p95_ms ({prof_thr['max_p95_ms']}) exceeds "
                    f"P4.md §9.2 ceiling ({ceiling}) — threshold inflation forbidden"
                )

    for req_cat in REQUIRED_CATEGORIES:
        if req_cat not in seen_categories:
            errors.append(f"SLO spec missing required category {req_cat!r}")

    for req_var in REQUIRED_DRILLDOWN_VARIANTS:
        if req_var not in seen_drilldown_variants:
            errors.append(
                f"SLO spec missing mandatory G18 drill-down variant {req_var!r}"
            )

    return errors


def validate_slo_approval_document(
    approval_doc: dict[str, Any],
    *,
    spec: dict[str, Any],
    actual_slo_sha256: str,
    target_image_digest: str,
    profile: str,
    require_owner_approved: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """Validate separate Owner SLO approval document against SLO SHA-256, digest & profile."""
    errors: list[str] = []
    if not isinstance(approval_doc, dict):
        return ["Approval document root must be a JSON object"], {}

    if approval_doc.get("schema_version") != EXPECTED_APPROVAL_SCHEMA_VERSION:
        errors.append(
            f"Approval document invalid schema_version: {approval_doc.get('schema_version')!r} "
            f"!= {EXPECTED_APPROVAL_SCHEMA_VERSION!r}"
        )

    if approval_doc.get("slo_bundle_id") != spec.get("slo_bundle_id"):
        errors.append(
            f"Approval document slo_bundle_id ({approval_doc.get('slo_bundle_id')!r}) "
            f"!= SLO spec slo_bundle_id ({spec.get('slo_bundle_id')!r})"
        )

    if str(approval_doc.get("slo_version") or "") != str(spec.get("slo_version") or ""):
        errors.append(
            f"Approval document slo_version ({approval_doc.get('slo_version')!r}) "
            f"!= SLO spec slo_version ({spec.get('slo_version')!r})"
        )

    doc_slo_sha = str(approval_doc.get("slo_spec_sha256") or "").strip().lower()
    if not SHA256_HEX_RE.match(doc_slo_sha):
        errors.append(
            f"Approval document slo_spec_sha256 is not a valid 64-hex SHA-256: {doc_slo_sha!r}"
        )
    elif doc_slo_sha != actual_slo_sha256.lower():
        errors.append(
            f"Approval document slo_spec_sha256 ({doc_slo_sha}) != "
            f"actual SLO spec SHA-256 ({actual_slo_sha256.lower()})"
        )

    approved_digest = str(approval_doc.get("approved_image_digest") or "").strip()
    if not DIGEST_URI_RE.match(approved_digest):
        errors.append(
            "Approval document approved_image_digest must be an immutable "
            f"`ghcr.io/<owner>/<repo>@sha256:<64-hex>` URI, got {approved_digest!r}"
        )
    elif target_image_digest and approved_digest != target_image_digest:
        errors.append(
            f"Approval document approved_image_digest ({approved_digest!r}) != "
            f"expected image digest ({target_image_digest!r})"
        )

    approved_profiles = approval_doc.get("approved_profiles")
    valid_profile_list = True
    if not isinstance(approved_profiles, list):
        errors.append("Approval document `approved_profiles` must be a JSON array")
        valid_profile_list = False
        approved_profiles = []
    else:
        seen_profs: set[str] = set()
        for item in approved_profiles:
            if not isinstance(item, str) or item not in SUPPORTED_PROFILES:
                errors.append(
                    f"Approval document `approved_profiles` contains unsupported profile {item!r}"
                )
                valid_profile_list = False
            elif item in seen_profs:
                errors.append(
                    f"Approval document `approved_profiles` contains duplicate profile {item!r}"
                )
                valid_profile_list = False
            else:
                seen_profs.add(item)

    status = str(approval_doc.get("approval_status") or "").strip()
    if status not in VALID_APPROVAL_STATUSES:
        errors.append(
            f"Approval document invalid approval_status {status!r}; "
            f"expected one of {VALID_APPROVAL_STATUSES}"
        )

    signoff = approval_doc.get("owner_signoff")
    if not isinstance(signoff, dict):
        errors.append("Approval document `owner_signoff` must be a JSON object")
        signoff = {}

    approved_by = str(signoff.get("approved_by") or "").strip()
    approved_at_utc = str(signoff.get("approved_at_utc") or "").strip()
    decision_ref = str(signoff.get("decision_reference") or "").strip()

    if status == "PENDING_OWNER_APPROVAL":
        if valid_profile_list and len(approved_profiles) > 0:
            errors.append(
                "Approval document cannot populate `approved_profiles` while "
                "`approval_status` is 'PENDING_OWNER_APPROVAL'"
            )
        if approved_by or approved_at_utc or decision_ref:
            errors.append(
                "Approval document cannot populate `owner_signoff` fields while "
                "`approval_status` is 'PENDING_OWNER_APPROVAL'"
            )
        if require_owner_approved:
            errors.append(
                "Owner approval required: separate approval document has "
                "approval_status='PENDING_OWNER_APPROVAL' (expected 'OWNER_APPROVED')"
            )
    elif status == "OWNER_APPROVED":
        if valid_profile_list and len(approved_profiles) == 0:
            errors.append(
                "Approval document with approval_status='OWNER_APPROVED' must list "
                "at least one approved configuration in `approved_profiles`"
            )
        if not approved_by:
            errors.append(
                "Approval document with approval_status='OWNER_APPROVED' requires "
                "non-empty `owner_signoff.approved_by`"
            )
        if not decision_ref:
            errors.append(
                "Approval document with approval_status='OWNER_APPROVED' requires "
                "non-empty `owner_signoff.decision_reference`"
            )
        if not approved_at_utc or _parse_utc_iso8601(approved_at_utc) is None:
            errors.append(
                "Approval document with approval_status='OWNER_APPROVED' requires "
                "valid UTC ISO-8601 `owner_signoff.approved_at_utc`"
            )
        if require_owner_approved and profile not in approved_profiles:
            errors.append(
                f"Owner approval required: profile {profile!r} is not in "
                f"approved_profiles {approved_profiles!r}"
            )

    owner_approved_for_profile = (
        status == "OWNER_APPROVED"
        and valid_profile_list
        and profile in approved_profiles
        and len(errors) == 0
    )
    meta = {
        "approval_status": status or None,
        "slo_spec_sha256_bound": doc_slo_sha or None,
        "approved_image_digest": approved_digest or None,
        "approved_profiles": approved_profiles,
        "owner_approved_for_profile": owner_approved_for_profile,
        "owner_signoff": {
            "approved_by": approved_by or None,
            "approved_at_utc": approved_at_utc or None,
            "decision_reference": decision_ref or None,
        },
    }
    return errors, meta


def _evaluate_http_rule(
    report: dict[str, Any],
    rule: dict[str, Any],
    prof_thr: dict[str, Any],
    *,
    path_key: str = "report_json_path",
) -> dict[str, Any]:
    slo_id = str(rule["slo_id"])
    jpath = str(rule[path_key])
    failures: list[str] = []
    try:
        node = resolve_dotted_path(report, jpath)
    except KeyError as exc:
        return {
            "slo_id": slo_id,
            "category": rule.get("category", "G18_DEEP_DIAGNOSTIC"),
            "report_json_path": jpath,
            "passed": False,
            "failures": [str(exc)],
        }

    rule_type = str(rule.get("rule_type") or "http_summary")
    measured_summary: dict[str, Any] = {}

    if rule_type == "http_summary":
        if not isinstance(node, dict):
            return {
                "slo_id": slo_id,
                "category": rule.get("category", "G18_DEEP_DIAGNOSTIC"),
                "report_json_path": jpath,
                "passed": False,
                "failures": [f"Expected JSON object at {jpath!r}"],
            }
        req_total = int(node.get("requests_total") or 0)
        p95 = float(node.get("p95_ms") or 0.0)
        p99 = float(node.get("p99_ms") or 0.0)
        err_cnt = int(node.get("error_count") or 0)
        err_pct = float(node.get("error_rate_pct") or 0.0)
        to_cnt = int(node.get("timeout_count") or 0)
        cli_cnt = int(node.get("client_error_count") or 0)
        c4xx = int(node.get("http_4xx_count") or 0)
        c5xx = int(node.get("http_5xx_count") or 0)
        rps = float(node.get("throughput_rps") or 0.0)

        measured_summary = {
            "requests_total": req_total,
            "p95_ms": p95,
            "p99_ms": p99,
            "error_count": err_cnt,
            "error_rate_pct": err_pct,
            "timeout_count": to_cnt,
            "client_error_count": cli_cnt,
            "http_4xx_count": c4xx,
            "http_5xx_count": c5xx,
            "throughput_rps": rps,
        }

        if req_total <= 0:
            failures.append(f"requests_total ({req_total}) must be > 0")
        if p95 > float(prof_thr["max_p95_ms"]):
            failures.append(f"p95_ms ({p95}) > max_p95_ms ({prof_thr['max_p95_ms']})")
        if p99 > float(prof_thr["max_p99_ms"]):
            failures.append(f"p99_ms ({p99}) > max_p99_ms ({prof_thr['max_p99_ms']})")
        max_err_pct = float(prof_thr["max_error_rate_pct"])
        if err_pct > max_err_pct:
            failures.append(
                f"error_rate_pct ({err_pct}) > max_error_rate_pct ({max_err_pct})"
            )
        if max_err_pct == 0.0 and err_cnt != 0:
            failures.append(f"error_count ({err_cnt}) != 0")
        if to_cnt > int(prof_thr["max_timeout_count"]):
            failures.append(
                f"timeout_count ({to_cnt}) > max_timeout_count ({prof_thr['max_timeout_count']})"
            )
        if cli_cnt > int(prof_thr["max_client_error_count"]):
            failures.append(
                f"client_error_count ({cli_cnt}) > max_client_error_count "
                f"({prof_thr['max_client_error_count']})"
            )
        if c4xx > int(prof_thr["max_http_4xx_count"]):
            failures.append(
                f"http_4xx_count ({c4xx}) > max_http_4xx_count ({prof_thr['max_http_4xx_count']})"
            )
        if c5xx > int(prof_thr["max_http_5xx_count"]):
            failures.append(
                f"http_5xx_count ({c5xx}) > max_http_5xx_count ({prof_thr['max_http_5xx_count']})"
            )

    for inv in prof_thr.get("invariants") or []:
        ok, msg = _check_invariant(report, inv)
        if not ok:
            failures.append(msg)

    out: dict[str, Any] = {
        "slo_id": slo_id,
        "category": rule.get("category", "G18_DEEP_DIAGNOSTIC"),
        "scenario_key": rule.get("scenario_key") or rule.get("wave_key"),
        "report_json_path": jpath,
        "thresholds_applied": prof_thr,
        "passed": len(failures) == 0,
        "failures": failures,
    }
    if "drilldown_variant" in rule:
        out["drilldown_variant"] = rule["drilldown_variant"]
    if measured_summary:
        out["measured"] = measured_summary
    return out


def evaluate_slo_bundle(
    *,
    slo_spec_path: Path,
    expected_slo_sha256: str,
    benchmark_report_path: Path,
    profile: str,
    approval_doc_path: Path | None = None,
    expected_image_digest: str = "",
    g18_diag_report_path: Path | None = None,
    require_owner_approved: bool = False,
) -> dict[str, Any]:
    actual_slo_sha = compute_normalized_sha256(slo_spec_path)
    spec = json.loads(slo_spec_path.read_text(encoding="utf-8"))
    bench = json.loads(benchmark_report_path.read_text(encoding="utf-8"))
    bench_sha = compute_normalized_sha256(benchmark_report_path)

    gate_errors: list[str] = []
    if expected_slo_sha256.strip().lower() != actual_slo_sha.lower():
        gate_errors.append(
            f"SLO spec SHA-256 mismatch: expected {expected_slo_sha256.strip().lower()}, "
            f"got {actual_slo_sha}"
        )

    gate_errors.extend(validate_slo_spec_governance(spec, profile=profile))

    tested_image = str(bench.get("app_image_tested") or "")
    target_digest = expected_image_digest.strip() or str(
        (spec.get("candidate_binding") or {}).get("app_image_digest") or ""
    )
    if target_digest and tested_image != target_digest:
        gate_errors.append(
            f"Benchmark report app_image_tested ({tested_image!r}) != "
            f"expected_image_digest ({target_digest!r})"
        )

    approval_doc_sha: str | None = None
    approval_verification: dict[str, Any] = {
        "approval_doc_path": str(approval_doc_path) if approval_doc_path else None,
        "approval_doc_sha256": None,
        "approval_status": None,
        "approved_image_digest": None,
        "approved_profiles": [],
        "owner_approved_for_profile": False,
        "owner_signoff": None,
    }
    if approval_doc_path is not None:
        if not approval_doc_path.is_file():
            gate_errors.append(f"Approval document not found: {approval_doc_path}")
        else:
            approval_doc_sha = compute_normalized_sha256(approval_doc_path)
            approval_doc = json.loads(approval_doc_path.read_text(encoding="utf-8"))
            app_errs, app_meta = validate_slo_approval_document(
                approval_doc,
                spec=spec,
                actual_slo_sha256=actual_slo_sha,
                target_image_digest=target_digest,
                profile=profile,
                require_owner_approved=require_owner_approved,
            )
            gate_errors.extend(app_errs)
            approved_digest = str(app_meta.get("approved_image_digest") or "")
            if approved_digest and tested_image and tested_image != approved_digest:
                gate_errors.append(
                    f"Benchmark report app_image_tested ({tested_image!r}) != "
                    f"approval document approved_image_digest ({approved_digest!r})"
                )
            approval_verification = {
                "approval_doc_path": str(approval_doc_path),
                "approval_doc_sha256": approval_doc_sha,
                **app_meta,
            }
    elif require_owner_approved:
        gate_errors.append(
            "Owner approval required: separate approval document (`--approval-doc`) must be provided"
        )

    report_prof = str(bench.get("resource_profile") or "s1_canonical_4x4")
    if report_prof != profile:
        gate_errors.append(
            f"Benchmark report resource_profile ({report_prof!r}) != requested profile ({profile!r})"
        )

    prof_cfg = ((spec.get("resource_profiles") or {}).get(profile)) or {}
    if prof_cfg.get("require_live_headroom_precheck") is True:
        headroom = bench.get("live_resource_headroom_precheck") or {}
        if headroom.get("headroom_ok") is not True:
            gate_errors.append(
                "Profile requires `live_resource_headroom_precheck.headroom_ok == true`"
            )

    rule_results: list[dict[str, Any]] = []
    category_verdicts: dict[str, dict[str, Any]] = {
        cat: {"total": 0, "passed_count": 0, "failed_count": 0, "passed": True}
        for cat in REQUIRED_CATEGORIES
    }

    if not gate_errors or isinstance(spec.get("rules"), list):
        for rule in spec.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            prof_thr = ((rule.get("proposed_thresholds") or {}).get(profile)) or {}
            if not prof_thr and gate_errors:
                continue
            res = _evaluate_http_rule(bench, rule, prof_thr)
            rule_results.append(res)
            cat = str(res["category"])
            if cat in category_verdicts:
                category_verdicts[cat]["total"] += 1
                if res["passed"]:
                    category_verdicts[cat]["passed_count"] += 1
                else:
                    category_verdicts[cat]["failed_count"] += 1
                    category_verdicts[cat]["passed"] = False

    diag_results: list[dict[str, Any]] = []
    diag_sha: str | None = None
    if g18_diag_report_path is not None:
        diag_doc = json.loads(g18_diag_report_path.read_text(encoding="utf-8"))
        diag_sha = compute_normalized_sha256(g18_diag_report_path)
        if (
            target_digest
            and str(diag_doc.get("app_image_tested") or "") != target_digest
        ):
            gate_errors.append(
                f"G18 diagnostic app_image_tested ({diag_doc.get('app_image_tested')!r}) "
                f"!= expected_image_digest ({target_digest!r})"
            )
        approved_digest_for_diag = str(
            approval_verification.get("approved_image_digest") or ""
        )
        if (
            approved_digest_for_diag
            and str(diag_doc.get("app_image_tested") or "") != approved_digest_for_diag
        ):
            gate_errors.append(
                f"G18 diagnostic app_image_tested ({diag_doc.get('app_image_tested')!r}) "
                f"!= approval document approved_image_digest ({approved_digest_for_diag!r})"
            )
        diag_rp = diag_doc.get("resource_profile")
        diag_prof_name = (
            str(diag_rp.get("name") or "")
            if isinstance(diag_rp, dict)
            else str(diag_rp or "")
        )
        if diag_prof_name != profile:
            gate_errors.append(
                f"G18 diagnostic resource_profile ({diag_prof_name!r}) "
                f"!= requested profile ({profile!r})"
            )
        for drule in spec.get("g18_deep_diagnostic_rules") or []:
            prof_thr = ((drule.get("proposed_thresholds") or {}).get(profile)) or {}
            dres = _evaluate_http_rule(
                diag_doc,
                {**drule, "category": "G18_DEEP_DIAGNOSTIC"},
                prof_thr,
                path_key="diag_report_json_path",
            )
            diag_results.append(dres)

    all_rules_passed = (
        all(v["passed"] and v["total"] > 0 for v in category_verdicts.values())
        and all(r["passed"] for r in diag_results)
        and len(gate_errors) == 0
    )

    effective_approval_status = approval_verification.get(
        "approval_status"
    ) or spec.get("approval_status")

    return {
        "schema_version": "cskh-p4-s1-slo-evaluation-v1",
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "slo_bundle_id": spec.get("slo_bundle_id"),
        "slo_version": spec.get("slo_version"),
        "slo_approval_status": effective_approval_status,
        "slo_spec_sha256": actual_slo_sha,
        "approval_verification": approval_verification,
        "profile_evaluated": profile,
        "expected_image_digest": target_digest,
        "benchmark_report_sha256": bench_sha,
        "g18_diag_report_sha256": diag_sha,
        "require_owner_approved": require_owner_approved,
        "gate_errors": gate_errors,
        "category_verdicts": category_verdicts,
        "s1_rule_results": rule_results,
        "g18_deep_diagnostic_results": diag_results,
        "status": "PASS" if all_rules_passed else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slo-spec",
        default="deploy/release/p4-s1-slo-spec.json",
        help="Path to immutable versioned SLO JSON specification",
    )
    parser.add_argument(
        "--expected-slo-sha256",
        required=True,
        help="Mandatory expected SHA-256 hex digest of the immutable SLO specification file",
    )
    parser.add_argument(
        "--approval-doc",
        "--slo-approval",
        dest="approval_doc",
        default="deploy/release/p4-s1-slo-approval.json",
        help=(
            "Path to separate Owner SLO approval JSON document bound to slo_spec_sha256, "
            "approved_image_digest, and approved_profiles"
        ),
    )
    parser.add_argument(
        "--benchmark-report",
        required=True,
        help="Path to S1 benchmark JSON report produced by benchmark_p4_isolated.py",
    )
    parser.add_argument(
        "--profile",
        required=True,
        choices=SUPPORTED_PROFILES,
        help="Resource profile thresholds to evaluate against",
    )
    parser.add_argument(
        "--expected-image-digest",
        default="",
        help="Expected immutable GHCR @sha256 image digest",
    )
    parser.add_argument(
        "--g18-diag-report",
        default="",
        help="Optional path to g18-diagnostic-*.json report for deep wave scoring",
    )
    parser.add_argument(
        "--require-owner-approved",
        action="store_true",
        help=(
            "Fail closed unless separate `--approval-doc` has `approval_status == OWNER_APPROVED`, "
            "complete `owner_signoff`, and includes `--profile` in `approved_profiles`"
        ),
    )
    parser.add_argument("--out", default="", help="Optional JSON output summary path")
    args = parser.parse_args(argv)

    approval_arg = args.approval_doc.strip()
    summary = evaluate_slo_bundle(
        slo_spec_path=Path(args.slo_spec),
        expected_slo_sha256=args.expected_slo_sha256,
        benchmark_report_path=Path(args.benchmark_report),
        profile=args.profile,
        approval_doc_path=Path(approval_arg) if approval_arg else None,
        expected_image_digest=args.expected_image_digest,
        g18_diag_report_path=(
            Path(args.g18_diag_report) if args.g18_diag_report.strip() else None
        ),
        require_owner_approved=args.require_owner_approved,
    )
    rendered = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(rendered.encode("utf-8"))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "slo_version": summary["slo_version"],
                "slo_spec_sha256": summary["slo_spec_sha256"],
                "slo_approval_status": summary["slo_approval_status"],
                "owner_approved_for_profile": summary["approval_verification"][
                    "owner_approved_for_profile"
                ],
                "profile_evaluated": summary["profile_evaluated"],
                "category_verdicts": summary["category_verdicts"],
                "gate_errors": summary["gate_errors"],
            },
            indent=2,
        )
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
