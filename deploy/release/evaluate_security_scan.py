#!/usr/bin/env python3
"""Trivy security scan evaluator & fail-closed vulnerability/secret gate (P4-02 / G04).

Enforces F-P4-REL-01A & F-P4-REL-01B:
1. Secret gate (`fail-closed` in ALL modes):
   - Never suppresses a whole file path (`target_suffix` alone is forbidden).
   - An upstream documentation false positive (such as the PyJWT 2.14.0 `METADATA` doctest JWT)
     is permitted ONLY when ALL four invariants hold simultaneously:
       a) `rule_id` and normalized `target_suffix` (case-insensitive dist-info folder) match a
          non-expired rule in `cve-exceptions.json`
       b) `StartLine == EndLine`, `allowed_line_min <= StartLine <= allowed_line_max`, AND total
          Trivy secret findings in that file == 1
       c) Actual unmasked token bytes are verified (via `--pyjwt-metadata-file` extracted from the
          scanned `@sha256` image, or unredacted `Match`) to equal `exact_sample` and
          `exact_sample_sha256` on the exact same line reported by Trivy
       d) Full-file JWT token scan of `METADATA` confirms ZERO other JWT tokens exist anywhere in
          that same file (a second or modified JWT in `METADATA` fails closed immediately).
2. Vulnerability gate:
   - `--strict` (default; mandatory in `p4-release.yml`): fails closed on ANY unapproved `CRITICAL`
     CVE (both fixable language/OS CVEs and unfixed upstream OS CVEs) unless covered by a
     non-expired `T0`/Owner-approved exception in `cve-exceptions.json`.
   - `--advisory-upstream-critical` (PR CI rehearsal alongside `--strict` report output): records
     pending upstream `CRITICAL` CVEs for `T0`/Owner review while still failing closed on ANY
     secret finding and on any package listed in `--forbid-critical-packages`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_EXCEPTIONS_FILE = Path(__file__).resolve().parent / "cve-exceptions.json"
JWT_CANDIDATE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
)


def _sha256_ascii(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def load_active_exceptions(
    path: Path,
) -> tuple[dict[str, dict[str, str]], list[dict[str, object]]]:
    if not path.is_file():
        return {}, []
    raw = json.loads(path.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    active_cves: dict[str, dict[str, str]] = {}
    active_secret_fps: list[dict[str, object]] = []

    for item in raw.get("exceptions") or []:
        cve_id = str(item.get("cve_id") or "").strip()
        exp_str = str(item.get("expires_on") or "").strip()
        approved_by = str(item.get("approved_by") or "").strip()
        if not cve_id or not exp_str or not approved_by:
            continue
        if date.fromisoformat(exp_str) >= today:
            active_cves[cve_id] = item

    for fp in raw.get("secret_false_positives") or []:
        rule_id = str(fp.get("rule_id") or "").strip()
        target_suffix = str(fp.get("target_suffix") or "").strip().replace("\\", "/")
        exact_sample = str(fp.get("exact_sample") or "").strip()
        exact_sha = str(fp.get("exact_sample_sha256") or "").strip().lower()
        exact_line = int(fp.get("exact_line") or 0)
        line_min = int(fp.get("allowed_line_min") or exact_line)
        line_max = int(fp.get("allowed_line_max") or exact_line)
        exp_str = str(fp.get("expires_on") or "").strip()
        approved_by = str(fp.get("approved_by") or "").strip()
        if (
            not rule_id
            or not target_suffix
            or not exact_sample
            or not exact_sha
            or exact_line <= 0
            or line_min <= 0
            or line_max < line_min
            or not exp_str
            or not approved_by
        ):
            continue
        if _sha256_ascii(exact_sample) != exact_sha:
            raise ValueError(
                f"Corrupted secret_false_positives entry {fp.get('id')!r}: "
                "exact_sample does not match exact_sample_sha256"
            )
        if date.fromisoformat(exp_str) >= today:
            active_secret_fps.append(
                {
                    "id": str(fp.get("id") or "FP-LOCKED"),
                    "rule_id": rule_id,
                    "target_suffix": target_suffix,
                    "exact_line": exact_line,
                    "allowed_line_min": line_min,
                    "allowed_line_max": line_max,
                    "exact_sample": exact_sample,
                    "exact_sample_sha256": exact_sha,
                    "expires_on": exp_str,
                    "approved_by": approved_by,
                    "reason": str(fp.get("reason") or ""),
                }
            )

    return active_cves, active_secret_fps


def verify_pyjwt_metadata_content(
    content: str,
    fp_rule: dict[str, object],
    *,
    expected_trivy_line: int | None = None,
) -> tuple[bool, list[str]]:
    """Verifies `METADATA` contains ONLY the exact verified doctest JWT in the locked line window."""
    line_min = int(fp_rule.get("allowed_line_min") or fp_rule["exact_line"])
    line_max = int(fp_rule.get("allowed_line_max") or fp_rule["exact_line"])
    expected_sample = str(fp_rule["exact_sample"])
    expected_sha = str(fp_rule["exact_sample_sha256"])

    found_tokens: list[tuple[int, str]] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        for tok in JWT_CANDIDATE_RE.findall(raw_line):
            found_tokens.append((line_no, tok))

    errors: list[str] = []
    if not found_tokens:
        errors.append(
            f"Expected verified doctest JWT in lines {line_min}..{line_max}, but found 0 JWT tokens in METADATA"
        )
        return False, errors

    if len(found_tokens) != 1:
        lines = [ln for ln, _ in found_tokens]
        errors.append(
            f"Expected exactly 1 verified doctest JWT in METADATA, found {len(found_tokens)} JWT tokens at lines {lines}"
        )

    for line_no, tok in found_tokens:
        tok_sha = _sha256_ascii(tok)
        line_ok = line_min <= line_no <= line_max and (
            expected_trivy_line is None or line_no == expected_trivy_line
        )
        if not line_ok or tok != expected_sample or tok_sha != expected_sha:
            errors.append(
                f"Unapproved JWT token in METADATA at line {line_no} (sha256={tok_sha[:16]}... != {expected_sha[:16]}...)"
            )

    return len(errors) == 0, errors


def _match_locked_secret_fp(
    *,
    target: str,
    sec: dict[str, object],
    findings_in_same_target: int,
    active_secret_fps: list[dict[str, object]],
    pyjwt_metadata_content: str | None,
) -> tuple[dict[str, object] | None, str | None]:
    norm_target = target.replace("\\", "/")
    norm_target_lower = norm_target.lower()
    rule_id = str(sec.get("RuleID") or "")
    start_line = int(sec.get("StartLine") or 0)
    end_line = int(sec.get("EndLine") or 0)
    raw_match = str(sec.get("Match") or "")

    for fp in active_secret_fps:
        suffix_lower = str(fp["target_suffix"]).lower()
        if fp["rule_id"] != rule_id or not norm_target_lower.endswith(suffix_lower):
            continue

        if findings_in_same_target != 1:
            return (
                None,
                f"Multiple secret findings ({findings_in_same_target}) in {norm_target}; refusing false-positive exclusion",
            )
        line_min = int(fp["allowed_line_min"])
        line_max = int(fp["allowed_line_max"])
        if start_line != end_line or not (line_min <= start_line <= line_max):
            return (
                None,
                f"Secret finding line {start_line}..{end_line} outside locked range {line_min}..{line_max} in {norm_target}",
            )

        if pyjwt_metadata_content is not None:
            ok, errs = verify_pyjwt_metadata_content(
                pyjwt_metadata_content, fp, expected_trivy_line=start_line
            )
            if not ok:
                return None, "; ".join(errs)
            return fp, None

        # Without the extracted METADATA file, refuse any Trivy-masked `***` match!
        if not raw_match or "*" in raw_match:
            return (
                None,
                f"Trivy redacted Match on {norm_target}:{start_line} without --pyjwt-metadata-file content proof",
            )
        tokens = JWT_CANDIDATE_RE.findall(raw_match)
        if (
            len(tokens) != 1
            or tokens[0] != fp["exact_sample"]
            or _sha256_ascii(tokens[0]) != fp["exact_sample_sha256"]
        ):
            return (
                None,
                f"Unmasked Match on {norm_target}:{start_line} does not match locked sample SHA-256",
            )
        return fp, None

    return None, None


def _dedupe_critical_entries(
    items: list[dict[str, str | None]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    for item in items:
        key = (
            str(item.get("cve_id") or ""),
            str(item.get("package") or ""),
            str(item.get("installed_version") or ""),
        )
        if key not in grouped:
            grouped[key] = {
                "cve_id": item.get("cve_id"),
                "package": item.get("package"),
                "installed_version": item.get("installed_version"),
                "fixed_version": item.get("fixed_version"),
                "targets": [item.get("target")],
                "occurrences": 1,
            }
        else:
            grouped[key]["occurrences"] = int(grouped[key]["occurrences"]) + 1
            tgt = item.get("target")
            targets_list = grouped[key]["targets"]
            assert isinstance(targets_list, list)
            if tgt not in targets_list:
                targets_list.append(tgt)
    return [grouped[k] for k in sorted(grouped)]


def evaluate_trivy_report(
    trivy_json_path: Path,
    *,
    exceptions_path: Path = DEFAULT_EXCEPTIONS_FILE,
    pyjwt_metadata_path: Path | None = None,
    mode: str = "strict",
    forbid_critical_packages: set[str] | None = None,
) -> dict[str, object]:
    if mode not in ("strict", "advisory_upstream_critical"):
        raise ValueError(f"Unsupported evaluation mode: {mode!r}")

    data = json.loads(trivy_json_path.read_text(encoding="utf-8"))
    active_exceptions, active_secret_fps = load_active_exceptions(exceptions_path)
    pyjwt_metadata_content = (
        pyjwt_metadata_path.read_text(encoding="utf-8", errors="replace")
        if pyjwt_metadata_path is not None
        else None
    )

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    fixable_critical_unapproved: list[dict[str, str | None]] = []
    unfixed_critical: list[dict[str, str | None]] = []
    excepted_findings: list[dict[str, str | None]] = []
    secret_findings: list[dict[str, object]] = []
    excepted_secret_fps: list[dict[str, object]] = []

    results = data.get("Results") or []
    secret_counts_by_target: dict[str, int] = {}
    for res in results:
        norm_t = str(res.get("Target") or "").replace("\\", "/").lower()
        secs = res.get("Secrets") or []
        if secs:
            secret_counts_by_target[norm_t] = secret_counts_by_target.get(
                norm_t, 0
            ) + len(secs)

    matched_pyjwt_fp = False
    for res in results:
        target = str(res.get("Target") or "")
        norm_target_lower = target.replace("\\", "/").lower()
        for sec in res.get("Secrets") or []:
            rule_id = str(sec.get("RuleID") or "")
            entry_sec: dict[str, object] = {
                "target": target,
                "rule_id": rule_id,
                "category": str(sec.get("Category") or ""),
                "severity": str(sec.get("Severity") or ""),
                "title": str(sec.get("Title") or ""),
                "start_line": int(sec.get("StartLine") or 0),
                "end_line": int(sec.get("EndLine") or 0),
            }
            matched_fp, reject_reason = _match_locked_secret_fp(
                target=target,
                sec=sec,
                findings_in_same_target=secret_counts_by_target.get(
                    norm_target_lower, 0
                ),
                active_secret_fps=active_secret_fps,
                pyjwt_metadata_content=pyjwt_metadata_content,
            )
            if matched_fp is not None:
                matched_pyjwt_fp = True
                excepted_secret_fps.append(
                    {
                        **entry_sec,
                        "fp_id": matched_fp["id"],
                        "exact_line": matched_fp["exact_line"],
                        "exact_sample_sha256": matched_fp["exact_sample_sha256"],
                        "verified_via_metadata_file": pyjwt_metadata_content
                        is not None,
                        "approved_by": matched_fp["approved_by"],
                        "expires_on": matched_fp["expires_on"],
                        "reason": matched_fp["reason"],
                    }
                )
            else:
                if reject_reason:
                    entry_sec["rejection_reason"] = reject_reason
                secret_findings.append(entry_sec)

        for vuln in res.get("Vulnerabilities") or []:
            sev = str(vuln.get("Severity") or "UNKNOWN").upper()
            counts[sev if sev in counts else "UNKNOWN"] += 1
            cve_id = str(vuln.get("VulnerabilityID") or "")
            pkg = str(vuln.get("PkgName") or "")
            installed = str(vuln.get("InstalledVersion") or "")
            fixed = str(vuln.get("FixedVersion") or "").strip()

            if sev == "CRITICAL":
                entry = {
                    "cve_id": cve_id,
                    "package": pkg,
                    "installed_version": installed,
                    "fixed_version": fixed or None,
                    "target": target,
                }
                if cve_id in active_exceptions:
                    excepted_findings.append(entry)
                elif fixed:
                    fixable_critical_unapproved.append(entry)
                else:
                    unfixed_critical.append(entry)

    # Independent direct scan of extracted METADATA even if Trivy emitted 0 findings
    if (
        pyjwt_metadata_content is not None
        and not matched_pyjwt_fp
        and active_secret_fps
    ):
        pyjwt_rules = [
            r
            for r in active_secret_fps
            if str(r.get("target_suffix") or "").lower().endswith("metadata")
        ]
        for r in pyjwt_rules:
            ok, errs = verify_pyjwt_metadata_content(pyjwt_metadata_content, r)
            if not ok:
                secret_findings.append(
                    {
                        "target": str(pyjwt_metadata_path),
                        "rule_id": "jwt-token-direct-metadata-audit",
                        "category": "JWT",
                        "severity": "HIGH",
                        "title": "Unapproved JWT token detected during direct METADATA file audit",
                        "rejection_reason": "; ".join(errs),
                    }
                )

    unique_fixable = _dedupe_critical_entries(fixable_critical_unapproved)
    unique_unfixed = _dedupe_critical_entries(unfixed_critical)
    forbidden_pkgs = {
        p.strip().lower() for p in (forbid_critical_packages or set()) if p.strip()
    }

    gate_failures: list[str] = []
    if secret_findings:
        gate_failures.append(
            f"Detected {len(secret_findings)} unapproved embedded secret finding(s) in scanned artifact"
        )

    if forbidden_pkgs:
        regressed = sorted(
            {
                f"{u['package']} ({u['cve_id']})"
                for u in (*unique_fixable, *unique_unfixed)
                if str(u["package"] or "").lower() in forbidden_pkgs
            }
        )
        if regressed:
            gate_failures.append(
                f"Detected regressed CRITICAL CVE(s) in patched package set: {regressed}"
            )

    if mode == "strict":
        if unique_fixable:
            ids = [
                f"{u['cve_id']} ({u['package']}@{u['installed_version']})"
                for u in unique_fixable
            ]
            gate_failures.append(
                f"Strict gate: {len(unique_fixable)} unapproved fixable CRITICAL CVE(s): {ids}"
            )
        if unique_unfixed:
            ids = [
                f"{u['cve_id']} ({u['package']}@{u['installed_version']})"
                for u in unique_unfixed
            ]
            gate_failures.append(
                f"Strict gate: {len(unique_unfixed)} unapproved unfixed OS CRITICAL CVE(s): {ids}"
            )

    return {
        "schema_version": "cskh-p4-security-scan-summary-v2",
        "gate_mode": mode,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_name": data.get("ArtifactName"),
        "artifact_type": data.get("ArtifactType"),
        "severity_counts": counts,
        "unique_critical_counts": {
            "fixable_unapproved": len(unique_fixable),
            "unfixed_upstream_os_unapproved": len(unique_unfixed),
            "excepted": len(_dedupe_critical_entries(excepted_findings)),
        },
        "secret_findings_count": len(secret_findings),
        "secret_findings": secret_findings,
        "excepted_secret_false_positives_count": len(excepted_secret_fps),
        "excepted_secret_false_positives": excepted_secret_fps,
        "unique_fixable_critical_unapproved": unique_fixable,
        "unique_unfixed_critical_upstream_os": unique_unfixed,
        "fixable_critical_unapproved": fixable_critical_unapproved,
        "unfixed_critical_upstream_os": unfixed_critical,
        "excepted_critical": excepted_findings,
        "gate_failures": gate_failures,
        "status": "PASS" if not gate_failures else "FAIL",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trivy-json", required=True)
    p.add_argument("--exceptions", default=str(DEFAULT_EXCEPTIONS_FILE))
    p.add_argument(
        "--pyjwt-metadata-file",
        default="",
        help="Path to extracted pyjwt-*.dist-info/METADATA from scanned image for exact-token verification",
    )
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--strict",
        action="store_true",
        help="Fail closed on ANY secret finding AND ANY unapproved CRITICAL CVE (default; mandatory for Release)",
    )
    mode_group.add_argument(
        "--advisory-upstream-critical",
        action="store_true",
        help="PR rehearsal mode: fail closed on ANY secret & forbidden packages; record upstream CRITICALs",
    )
    p.add_argument(
        "--forbid-critical-packages",
        default="",
        help="Comma-separated package names whose CRITICAL CVEs fail closed even in advisory mode",
    )
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    mode = "advisory_upstream_critical" if args.advisory_upstream_critical else "strict"
    forbid_pkgs = {
        x.strip() for x in args.forbid_critical_packages.split(",") if x.strip()
    }
    meta_path = Path(args.pyjwt_metadata_file) if args.pyjwt_metadata_file else None

    summary = evaluate_trivy_report(
        Path(args.trivy_json),
        exceptions_path=Path(args.exceptions),
        pyjwt_metadata_path=meta_path,
        mode=mode,
        forbid_critical_packages=forbid_pkgs,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "gate_mode": summary["gate_mode"],
                "severity_counts": summary["severity_counts"],
                "unique_critical_counts": summary["unique_critical_counts"],
                "secret_findings_count": summary["secret_findings_count"],
                "excepted_secret_false_positives_count": summary[
                    "excepted_secret_false_positives_count"
                ],
            }
        )
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
