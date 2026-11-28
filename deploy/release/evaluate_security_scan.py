#!/usr/bin/env python3
"""Trivy security scan evaluator & fail-closed vulnerability/secret gate (P4-02 / G04).

Enforces F-P4-REL-01A, F-P4-REL-01B & G04 Scoped Exception Governance:
1. Secret gate (`fail-closed` in ALL modes):
   - Never suppresses a whole file path (`target_suffix` alone is forbidden).
   - An upstream documentation false positive (such as the PyJWT 2.15.1 `METADATA` doctest JWT)
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
2. Vulnerability & Scoped Exception gate:
   - Every CVE exception entry in `cve-exceptions.json` MUST pin all five scope attributes:
       (1) canonical `@sha256:<64-hex>` `image_digest` (or `digest`),
       (2) exact `cve_id` (`CVE-YYYY-NNNN+` or `GHSA-xxxx-xxxx-xxxx`, zero wildcards),
       (3) exact `package` or `packages` list (zero wildcards),
       (4) exact `installed_version` (zero wildcards or comparator ranges),
       (5) non-expired ISO `expires_on` (`expires_on >= reference_date`) with written `Owner`/`T0`
           `approved_by` (never self-approved by `T1B`).
   - `--strict` (default; mandatory in `p4-release.yml` and `production_candidate` manifests):
     fails closed on ANY malformed/expired/wrong-digest/unmatched exception entry AND on ANY
     unapproved `CRITICAL` CVE (both fixable language/OS CVEs and unfixed upstream OS CVEs).
   - `--advisory-upstream-critical` (PR CI rehearsal alongside `--strict` report output): records
     pending upstream `CRITICAL` CVEs for `T0`/Owner review while still failing closed on ANY
     secret finding, ANY malformed/expired exception entry, and ANY package listed in
     `--forbid-critical-packages`.
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
REGISTRY_DIGEST_RE = re.compile(
    r"\A[^/@\s:<>]+(?::[0-9]+)?(?:/[^/@\s:<>]+)+(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}\Z"
)
ZERO_DIGEST_SUFFIX = "@sha256:" + ("0" * 64)
CVE_ID_RE = re.compile(
    r"\A(?:CVE-\d{4}-\d{4,}|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})\Z"
)
ISO_DATE_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
PLACEHOLDER_APPROVERS = {
    "TBD",
    "TODO",
    "PENDING",
    "NONE",
    "N/A",
    "NA",
    "UNAPPROVED",
    "UNKNOWN",
}


def _sha256_ascii(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


def is_valid_registry_digest(image_ref: str) -> bool:
    if not image_ref or not isinstance(image_ref, str):
        return False
    if (
        "BLOCKED" in image_ref.upper()
        or "ACTUAL_DIGEST" in image_ref
        or "<" in image_ref
    ):
        return False
    if image_ref.endswith(ZERO_DIGEST_SUFFIX):
        return False
    return bool(REGISTRY_DIGEST_RE.match(image_ref))


def _extract_exception_packages(
    item: dict[str, object], exc_id: str, cve_label: str
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    has_single = "package" in item and item.get("package") is not None
    has_multi = "packages" in item and item.get("packages") is not None

    candidates: list[object] = []
    if has_single and has_multi:
        single_val = item.get("package")
        multi_val = item.get("packages")
        if (
            not isinstance(single_val, str)
            or not isinstance(multi_val, list)
            or [single_val] != multi_val
        ):
            errors.append(
                f"Exception {exc_id} ({cve_label}): conflicting 'package' and 'packages' fields"
            )
            return [], errors
        candidates = list(multi_val)
    elif has_multi:
        multi_val = item.get("packages")
        if not isinstance(multi_val, list) or not multi_val:
            errors.append(
                f"Exception {exc_id} ({cve_label}): 'packages' must be a non-empty list of package names"
            )
            return [], errors
        candidates = list(multi_val)
    elif has_single:
        candidates = [item.get("package")]
    else:
        errors.append(
            f"Exception {exc_id} ({cve_label}): missing required 'package' or 'packages' scope"
        )
        return [], errors

    normalized: list[str] = []
    for raw_pkg in candidates:
        if not isinstance(raw_pkg, str) or not raw_pkg or raw_pkg != raw_pkg.strip():
            errors.append(
                f"Exception {exc_id} ({cve_label}): invalid empty or padded package name {raw_pkg!r}"
            )
            continue
        if re.search(r"\s", raw_pkg) or "*" in raw_pkg or "?" in raw_pkg:
            errors.append(
                f"Exception {exc_id} ({cve_label}): wildcard or whitespace forbidden in package {raw_pkg!r}"
            )
            continue
        if raw_pkg not in normalized:
            normalized.append(raw_pkg)

    if not normalized and not errors:
        errors.append(
            f"Exception {exc_id} ({cve_label}): 'packages' resolved to zero valid package names"
        )
    return normalized, errors


def validate_cve_exception_entries(
    raw_exceptions: object,
    *,
    reference_date: date | None = None,
    expected_image_digest: str | None = None,
    enforce_digest_match: bool = True,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Validates `exceptions` entries against the 5-tuple G04 scope contract.

    Returns `(valid_rules, rejected_entries, error_messages)`.
    """
    today = reference_date or datetime.now(timezone.utc).date()
    valid_rules: list[dict[str, object]] = []
    rejected_entries: list[dict[str, object]] = []
    errors: list[str] = []

    if raw_exceptions is None:
        return [], [], []
    if not isinstance(raw_exceptions, list):
        msg = "cve-exceptions.json 'exceptions' field must be a JSON array"
        return [], [{"rejection_reasons": [msg]}], [msg]

    target_digest = (expected_image_digest or "").strip()
    if (
        raw_exceptions
        and enforce_digest_match
        and not is_valid_registry_digest(target_digest)
    ):
        msg = (
            "Strict exception evaluation requires a valid canonical @sha256 target image "
            f"digest, got {target_digest!r}"
        )
        errors.append(msg)

    seen_tuples: set[tuple[str, str, str, str]] = set()

    for idx, item in enumerate(raw_exceptions):
        if not isinstance(item, dict):
            msg = f"Exception entry #{idx} must be a JSON object"
            errors.append(msg)
            rejected_entries.append({"index": idx, "rejection_reasons": [msg]})
            continue

        exc_id = str(item.get("id") or f"EXC-{idx + 1:03d}").strip()
        raw_cve = item.get("cve_id")
        cve_label = str(raw_cve or "<missing-cve>")
        entry_errors: list[str] = []

        # 1. Exact CVE / GHSA identifier
        if (
            not isinstance(raw_cve, str)
            or raw_cve != raw_cve.strip()
            or not CVE_ID_RE.match(raw_cve)
        ):
            entry_errors.append(
                f"Exception {exc_id}: invalid or wildcard cve_id {raw_cve!r}"
            )

        # 2. Canonical @sha256 image digest
        has_img_digest = "image_digest" in item and item.get("image_digest") is not None
        has_digest = "digest" in item and item.get("digest") is not None
        raw_digest: object = None
        if has_img_digest and has_digest:
            if item.get("image_digest") != item.get("digest"):
                entry_errors.append(
                    f"Exception {exc_id} ({cve_label}): conflicting 'image_digest' and 'digest' fields"
                )
            raw_digest = item.get("image_digest")
        elif has_img_digest:
            raw_digest = item.get("image_digest")
        elif has_digest:
            raw_digest = item.get("digest")

        if not isinstance(raw_digest, str) or not is_valid_registry_digest(raw_digest):
            entry_errors.append(
                f"Exception {exc_id} ({cve_label}): missing or invalid canonical @sha256 image_digest {raw_digest!r}"
            )
        elif enforce_digest_match and target_digest and raw_digest != target_digest:
            entry_errors.append(
                f"Exception {exc_id} ({cve_label}): image_digest scope mismatch "
                f"(exception={raw_digest!r} != target={target_digest!r})"
            )

        # 3. Exact package(s)
        packages, pkg_errors = _extract_exception_packages(item, exc_id, cve_label)
        entry_errors.extend(pkg_errors)

        # 4. Exact installed_version
        raw_ver = item.get("installed_version")
        if (
            not isinstance(raw_ver, str)
            or not raw_ver
            or raw_ver != raw_ver.strip()
            or bool(re.search(r"\s", raw_ver))
            or "*" in raw_ver
            or "?" in raw_ver
            or raw_ver.startswith((">", "<", "=", "~", "^"))
        ):
            entry_errors.append(
                f"Exception {exc_id} ({cve_label}): missing, wildcard, or range installed_version {raw_ver!r}"
            )

        # 5. Expiration date (`expires_on >= today`)
        raw_exp = item.get("expires_on")
        if not isinstance(raw_exp, str) or not ISO_DATE_RE.match(raw_exp):
            entry_errors.append(
                f"Exception {exc_id} ({cve_label}): missing or invalid ISO expires_on date {raw_exp!r}"
            )
        else:
            try:
                exp_date = date.fromisoformat(raw_exp)
            except ValueError:
                entry_errors.append(
                    f"Exception {exc_id} ({cve_label}): invalid calendar expires_on date {raw_exp!r}"
                )
            else:
                if exp_date < today:
                    entry_errors.append(
                        f"Exception {exc_id} ({cve_label}): expired on {raw_exp} "
                        f"(reference_date={today.isoformat()})"
                    )

        # 6. Written Owner / T0 approval (never placeholder, never self-approved by T1B)
        raw_approver = item.get("approved_by")
        if (
            not isinstance(raw_approver, str)
            or not raw_approver.strip()
            or raw_approver != raw_approver.strip()
        ):
            entry_errors.append(
                f"Exception {exc_id} ({cve_label}): missing required approved_by authority"
            )
        else:
            approver_uc = raw_approver.upper()
            if (
                "<" in raw_approver
                or ">" in raw_approver
                or approver_uc in PLACEHOLDER_APPROVERS
            ):
                entry_errors.append(
                    f"Exception {exc_id} ({cve_label}): placeholder approved_by {raw_approver!r} forbidden"
                )
            elif approver_uc == "T1B" or approver_uc.startswith(
                ("T1B-", "T1B_", "T1B/")
            ):
                entry_errors.append(
                    f"Exception {exc_id} ({cve_label}): self-approval by T1B ({raw_approver!r}) is forbidden"
                )

        if not entry_errors:
            assert isinstance(raw_digest, str)
            assert isinstance(raw_cve, str)
            assert isinstance(raw_ver, str)
            assert isinstance(raw_exp, str)
            assert isinstance(raw_approver, str)
            for pkg in packages:
                tup = (raw_digest, raw_cve, pkg, raw_ver)
                if tup in seen_tuples:
                    entry_errors.append(
                        f"Exception {exc_id} ({raw_cve}): duplicate exception scope for {pkg}@{raw_ver} on {raw_digest}"
                    )
                else:
                    seen_tuples.add(tup)

        if entry_errors:
            errors.extend(entry_errors)
            rejected_entries.append(
                {
                    "id": exc_id,
                    "cve_id": str(raw_cve or ""),
                    "image_digest": str(raw_digest or ""),
                    "packages": packages,
                    "installed_version": str(raw_ver or ""),
                    "expires_on": str(raw_exp or ""),
                    "approved_by": str(raw_approver or ""),
                    "rejection_reasons": entry_errors,
                }
            )
            continue

        valid_rules.append(
            {
                "id": exc_id,
                "cve_id": raw_cve,
                "image_digest": raw_digest,
                "packages": packages,
                "installed_version": raw_ver,
                "expires_on": raw_exp,
                "approved_by": raw_approver,
                "reason": str(item.get("reason") or ""),
            }
        )

    return valid_rules, rejected_entries, errors


def load_active_exceptions(
    path: Path,
    *,
    reference_date: date | None = None,
    expected_image_digest: str | None = None,
    enforce_digest_match: bool = True,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[str],
]:
    if not path.is_file():
        return [], [], [], []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Exceptions file {path} must contain a top-level JSON object"
        return [], [], [{"rejection_reasons": [msg]}], [msg]

    today = reference_date or datetime.now(timezone.utc).date()
    valid_cves, rejected_cves, cve_errors = validate_cve_exception_entries(
        raw.get("exceptions") if "exceptions" in raw else [],
        reference_date=today,
        expected_image_digest=expected_image_digest,
        enforce_digest_match=enforce_digest_match,
    )

    active_secret_fps: list[dict[str, object]] = []
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

    return valid_cves, active_secret_fps, rejected_cves, cve_errors


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
    items: list[dict[str, object]],
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


def _match_scoped_cve_rule(
    *,
    artifact_name: str,
    cve_id: str,
    package: str,
    installed_version: str,
    reference_date: date,
    valid_rules: list[dict[str, object]],
) -> dict[str, object] | None:
    for rule in valid_rules:
        pkgs = rule["packages"]
        assert isinstance(pkgs, list)
        if (
            rule["image_digest"] == artifact_name
            and rule["cve_id"] == cve_id
            and package in pkgs
            and rule["installed_version"] == installed_version
            and date.fromisoformat(str(rule["expires_on"])) >= reference_date
        ):
            return rule
    return None


def evaluate_trivy_report(
    trivy_json_path: Path,
    *,
    exceptions_path: Path = DEFAULT_EXCEPTIONS_FILE,
    pyjwt_metadata_path: Path | None = None,
    mode: str = "strict",
    forbid_critical_packages: set[str] | None = None,
    expected_image_digest: str | None = None,
    reference_date: date | None = None,
) -> dict[str, object]:
    if mode not in ("strict", "advisory_upstream_critical"):
        raise ValueError(f"Unsupported evaluation mode: {mode!r}")

    today = reference_date or datetime.now(timezone.utc).date()
    data = json.loads(trivy_json_path.read_text(encoding="utf-8"))
    scanned_artifact = str(data.get("ArtifactName") or "").strip()
    expected_img = (expected_image_digest or "").strip()

    target_digest_for_exceptions = expected_img or scanned_artifact
    valid_cve_rules, active_secret_fps, rejected_cve_entries, exception_errors = (
        load_active_exceptions(
            exceptions_path,
            reference_date=today,
            expected_image_digest=target_digest_for_exceptions,
            enforce_digest_match=(mode == "strict"),
        )
    )
    pyjwt_metadata_content = (
        pyjwt_metadata_path.read_text(encoding="utf-8", errors="replace")
        if pyjwt_metadata_path is not None
        else None
    )

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    fixable_critical_unapproved: list[dict[str, object]] = []
    unfixed_critical: list[dict[str, object]] = []
    excepted_findings: list[dict[str, object]] = []
    secret_findings: list[dict[str, object]] = []
    excepted_secret_fps: list[dict[str, object]] = []
    matched_scope_tuples: set[tuple[str, str, str, str]] = set()

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
                entry: dict[str, object] = {
                    "cve_id": cve_id,
                    "package": pkg,
                    "installed_version": installed,
                    "fixed_version": fixed or None,
                    "target": target,
                }
                matched_rule = _match_scoped_cve_rule(
                    artifact_name=scanned_artifact,
                    cve_id=cve_id,
                    package=pkg,
                    installed_version=installed,
                    reference_date=today,
                    valid_rules=valid_cve_rules,
                )
                if matched_rule is not None:
                    matched_scope_tuples.add((scanned_artifact, cve_id, pkg, installed))
                    excepted_findings.append(
                        {
                            **entry,
                            "exception_id": matched_rule["id"],
                            "approved_image_digest": matched_rule["image_digest"],
                            "approved_by": matched_rule["approved_by"],
                            "expires_on": matched_rule["expires_on"],
                            "reason": matched_rule["reason"],
                        }
                    )
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

    # In strict mode, reject any exception scope item that did not match an actual CRITICAL finding
    if mode == "strict":
        for rule in valid_cve_rules:
            pkgs = rule["packages"]
            assert isinstance(pkgs, list)
            unmatched_pkgs = [
                pkg
                for pkg in pkgs
                if (
                    str(rule["image_digest"]),
                    str(rule["cve_id"]),
                    pkg,
                    str(rule["installed_version"]),
                )
                not in matched_scope_tuples
            ]
            if unmatched_pkgs:
                msg = (
                    f"Exception {rule['id']} ({rule['cve_id']}): out-of-scope or unmatched "
                    f"package/version {unmatched_pkgs}@{rule['installed_version']} on {scanned_artifact}"
                )
                exception_errors.append(msg)
                rejected_cve_entries.append(
                    {
                        "id": rule["id"],
                        "cve_id": rule["cve_id"],
                        "image_digest": rule["image_digest"],
                        "packages": unmatched_pkgs,
                        "installed_version": rule["installed_version"],
                        "expires_on": rule["expires_on"],
                        "approved_by": rule["approved_by"],
                        "rejection_reasons": [msg],
                    }
                )

    unique_fixable = _dedupe_critical_entries(fixable_critical_unapproved)
    unique_unfixed = _dedupe_critical_entries(unfixed_critical)
    forbidden_pkgs = {
        p.strip().lower() for p in (forbid_critical_packages or set()) if p.strip()
    }

    gate_failures: list[str] = []
    if expected_img:
        if not is_valid_registry_digest(expected_img):
            gate_failures.append(
                f"Invalid --expected-image digest reference: {expected_img!r}"
            )
        elif scanned_artifact != expected_img:
            gate_failures.append(
                "Scanned artifact digest mismatch: "
                f"trivy ArtifactName={scanned_artifact!r} != expected_image={expected_img!r}"
            )

    if secret_findings:
        gate_failures.append(
            f"Detected {len(secret_findings)} unapproved embedded secret finding(s) in scanned artifact"
        )

    for exc_err in exception_errors:
        gate_failures.append(f"Exception gate: {exc_err}")

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
        "reference_date_utc": today.isoformat(),
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
        "rejected_exceptions_count": len(rejected_cve_entries),
        "rejected_exceptions": rejected_cve_entries,
        "exception_validation_errors": exception_errors,
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
        "--expected-image",
        default="",
        help="Canonical @sha256 image digest that Trivy ArtifactName and CVE exceptions must match",
    )
    p.add_argument(
        "--reference-date",
        default="",
        help="Optional ISO YYYY-MM-DD reference date for deterministic expiry verification",
    )
    p.add_argument(
        "--pyjwt-metadata-file",
        default="",
        help="Path to extracted pyjwt-*.dist-info/METADATA from scanned image for exact-token verification",
    )
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--strict",
        action="store_true",
        help="Fail closed on ANY secret finding, invalid/out-of-scope exception, or unapproved CRITICAL CVE",
    )
    mode_group.add_argument(
        "--advisory-upstream-critical",
        action="store_true",
        help="PR rehearsal mode: fail closed on ANY secret, invalid/expired exception & forbidden packages",
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
    ref_date = (
        date.fromisoformat(args.reference_date.strip())
        if args.reference_date.strip()
        else None
    )

    summary = evaluate_trivy_report(
        Path(args.trivy_json),
        exceptions_path=Path(args.exceptions),
        pyjwt_metadata_path=meta_path,
        mode=mode,
        forbid_critical_packages=forbid_pkgs,
        expected_image_digest=args.expected_image or None,
        reference_date=ref_date,
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
                "rejected_exceptions_count": summary["rejected_exceptions_count"],
            }
        )
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
