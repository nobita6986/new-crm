#!/usr/bin/env python3
"""Trivy security scan evaluator & fail-closed vulnerability/secret gate (P4-02 / G04).

Reads Trivy JSON scan output (`trivy image --format json` or `trivy fs --format json`),
verifies zero secret findings, classifies OS/language package vulnerabilities by severity and
fix availability, applies only non-expired approved exceptions from `cve-exceptions.json`,
and writes a sanitized, deterministic JSON summary (`security-scan-summary.json`).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_EXCEPTIONS_FILE = Path(__file__).resolve().parent / "cve-exceptions.json"


def load_active_exceptions(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    active: dict[str, dict[str, str]] = {}
    for item in raw.get("exceptions") or []:
        cve_id = str(item.get("cve_id") or "").strip()
        exp_str = str(item.get("expires_on") or "").strip()
        approved_by = str(item.get("approved_by") or "").strip()
        if not cve_id or not exp_str or not approved_by:
            continue
        if date.fromisoformat(exp_str) >= today:
            active[cve_id] = item
    return active


def evaluate_trivy_report(
    trivy_json_path: Path,
    *,
    exceptions_path: Path = DEFAULT_EXCEPTIONS_FILE,
    fail_on_secrets: bool = True,
    fail_on_unapproved_fixable_critical: bool = True,
) -> dict[str, object]:
    data = json.loads(trivy_json_path.read_text(encoding="utf-8"))
    active_exceptions = load_active_exceptions(exceptions_path)

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    fixable_critical_unapproved: list[dict[str, str]] = []
    unfixed_critical: list[dict[str, str]] = []
    excepted_findings: list[dict[str, str]] = []
    secret_findings: list[dict[str, str]] = []

    for res in data.get("Results") or []:
        target = str(res.get("Target") or "")
        for sec in res.get("Secrets") or []:
            secret_findings.append(
                {
                    "target": target,
                    "rule_id": str(sec.get("RuleID") or ""),
                    "category": str(sec.get("Category") or ""),
                    "severity": str(sec.get("Severity") or ""),
                    "title": str(sec.get("Title") or ""),
                }
            )
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

    gate_failures: list[str] = []
    if fail_on_secrets and secret_findings:
        gate_failures.append(
            f"Detected {len(secret_findings)} embedded secret finding(s) in scanned artifact"
        )
    if fail_on_unapproved_fixable_critical and fixable_critical_unapproved:
        ids = sorted({f["cve_id"] for f in fixable_critical_unapproved})
        gate_failures.append(
            f"Detected {len(fixable_critical_unapproved)} unapproved fixable CRITICAL CVE(s): {ids}"
        )

    return {
        "schema_version": "cskh-p4-security-scan-summary-v1",
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact_name": data.get("ArtifactName"),
        "artifact_type": data.get("ArtifactType"),
        "severity_counts": counts,
        "secret_findings_count": len(secret_findings),
        "secret_findings": secret_findings,
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
        "--allow-fixable-critical",
        action="store_true",
        help="Record fixable CRITICAL CVEs without failing exit code (advisory mode only)",
    )
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    summary = evaluate_trivy_report(
        Path(args.trivy_json),
        exceptions_path=Path(args.exceptions),
        fail_on_secrets=True,
        fail_on_unapproved_fixable_critical=not args.allow_fixable_critical,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes((json.dumps(summary, indent=2) + "\n").encode("utf-8"))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "severity_counts": summary["severity_counts"],
                "secret_findings_count": summary["secret_findings_count"],
                "fixable_critical_unapproved_count": len(
                    summary["fixable_critical_unapproved"]
                ),
            }
        )
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
