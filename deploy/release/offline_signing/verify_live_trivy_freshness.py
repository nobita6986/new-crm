#!/usr/bin/env python3
"""Run or evaluate a read-only live Trivy DB freshness scan on the accepted @sha256 image (DK-03).

Outputs a standalone freshness evidence bundle completely separate from the sealed
12-file offline-signing bundle. Never modifies release-manifest.json, signatures,
or previously sealed artifacts, and fails closed if DB age > 24 hours or if
evaluate_security_scan.py --strict reports any CRITICAL CVE or unapproved secret.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from verify_offline_signed_candidate import (
    BUNDLE_SUMS_SHA,
    CANDIDATE_SHA,
    EXCEPTIONS_SHA,
    FORBIDDEN_ENV,
    IMAGE,
    IMAGE_ID,
    file_sha,
    require,
)


TRIVY_IMAGE = (
    "aquasec/trivy:0.58.2"
    "@sha256:665030f4d33a82c1e8d9d5e0453365842236723c1ee5cc3becca698268e66a56"
)
EXPECTED_TRIVY_VERSION = "0.58.2"
TRIVY_DB_REPOSITORIES = (
    "public.ecr.aws/aquasecurity/trivy-db:2,ghcr.io/aquasecurity/trivy-db:2"
)
PYJWT_METADATA_SHA = "1ea0c9f139b8c928276b3628af165955f6f764491448a6ad8f103cdcbb382da3"
CLOCK_SKEW_TOLERANCE_SECONDS = 300.0
ISO_FRAC_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$"
)


def parse_iso_utc(raw: str) -> datetime:
    text = (raw or "").strip()
    match = ISO_FRAC_RE.match(text)
    require(match is not None, f"Invalid ISO-8601 UTC timestamp: {raw!r}")
    assert match is not None
    base, frac, tz_part = match.groups()
    micro = (frac[:6].ljust(6, "0")) if frac else "000000"
    tz_norm = "+00:00" if tz_part == "Z" else tz_part
    return datetime.fromisoformat(f"{base}.{micro}{tz_norm}").astimezone(timezone.utc)


def run_checked(
    command: list[str], *, cwd: Path | None = None, timeout: int = 900
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    require(
        result.returncode == 0,
        f"Command failed ({command[0]} rc={result.returncode}): {result.stderr[-2000:]}",
    )
    return result.stdout


def assert_zero_forbidden_env() -> None:
    present = [name for name in FORBIDDEN_ENV if os.environ.get(name)]
    require(
        not present,
        f"Read-only live Trivy scanner received forbidden credentials: {present}",
    )


def assert_sealed_offline_bundle_untouched(bundle_dir: Path) -> None:
    bundle_dir = bundle_dir.resolve()
    sums_path = bundle_dir / "SHA256SUMS.txt"
    require(
        sums_path.is_file() and file_sha(sums_path) == BUNDLE_SUMS_SHA,
        "Sealed offline-signing bundle SHA256SUMS.txt was modified or missing",
    )
    entries: dict[str, str] = {}
    for line in sums_path.read_text(encoding="ascii").splitlines():
        expected, relative = line.split(None, 1)
        rel = relative.strip()
        target = bundle_dir / rel
        require(
            target.is_file() and file_sha(target) == expected,
            f"Sealed offline-signing bundle file modified: {rel}",
        )
        entries[rel] = expected
    actual = {
        p.relative_to(bundle_dir).as_posix()
        for p in bundle_dir.rglob("*")
        if p.is_file() and p != sums_path
    }
    require(
        set(entries) == actual and len(entries) == 12,
        "Sealed offline-signing bundle file inventory changed",
    )


def write_sha256sums(out_dir: Path) -> str:
    sums: list[str] = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            sums.append(f"{file_sha(path)}  {path.relative_to(out_dir).as_posix()}")
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(sums) + "\n", encoding="utf-8")
    return file_sha(sums_path)


def execute_docker_trivy_scan(out_dir: Path) -> None:
    """Pull immutable public @sha256 candidate + pinned Trivy 0.58.2 and scan with fresh DB."""
    assert_zero_forbidden_env()
    out_dir.mkdir(parents=True, exist_ok=True)

    run_checked(["docker", "pull", IMAGE])
    inspect_raw = run_checked(["docker", "image", "inspect", IMAGE])
    inspect_obj = json.loads(inspect_raw)[0]
    pulled_id = inspect_obj.get("Id")
    pulled_digests = inspect_obj.get("RepoDigests") or []
    pulled_labels = (inspect_obj.get("Config") or {}).get("Labels") or {}
    require(pulled_id == IMAGE_ID, f"Pulled image OCI config mismatch: {pulled_id}")
    require(IMAGE in pulled_digests, f"Pulled image RepoDigests missing {IMAGE}")
    require(
        pulled_labels.get("org.opencontainers.image.revision") == CANDIDATE_SHA,
        "Pulled image revision label mismatch",
    )

    run_checked(["docker", "pull", TRIVY_IMAGE])
    trivy_image_id = run_checked(
        ["docker", "image", "inspect", "-f", "{{ .Id }}", TRIVY_IMAGE]
    ).strip()

    cache_dir = Path(tempfile.mkdtemp(prefix="p4-trivy-fresh-cache-"))
    try:
        run_checked(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                "-v",
                f"{out_dir}:/scan-out",
                "-v",
                f"{cache_dir}:/root/.cache",
                TRIVY_IMAGE,
                "image",
                "--image-src",
                "docker",
                "--db-repository",
                TRIVY_DB_REPOSITORIES,
                "--scanners",
                "vuln,secret",
                "--format",
                "json",
                "--timeout",
                "15m",
                "--output",
                "/scan-out/trivy-image-fresh.json",
                IMAGE,
            ],
            timeout=1200,
        )

        ver_out = run_checked(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{cache_dir}:/root/.cache",
                TRIVY_IMAGE,
                "version",
                "--format",
                "json",
            ],
            timeout=120,
        )
        ver_doc = json.loads(ver_out)
        ver_doc["PinnedTrivyImage"] = TRIVY_IMAGE
        ver_doc["PulledTrivyImageID"] = trivy_image_id
        (out_dir / "trivy-version.json").write_text(
            json.dumps(ver_doc, indent=2) + "\n", encoding="utf-8"
        )

        db_meta_candidates = sorted(
            p for p in cache_dir.rglob("metadata.json") if p.parent.name == "db"
        )
        require(
            len(db_meta_candidates) == 1,
            f"Expected exactly 1 Trivy db/metadata.json, found {db_meta_candidates}",
        )
        shutil.copyfile(db_meta_candidates[0], out_dir / "trivy-db-metadata.json")
    finally:
        shutil.rmtree(cache_dir, ignore_errors=True)

    pyjwt_meta = run_checked(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/home/frappe/frappe-bench/env/bin/python",
            IMAGE,
            "-c",
            (
                "from pathlib import Path; "
                "matches = sorted(Path('/home/frappe/frappe-bench/env/lib/python3.11/site-packages')"
                ".glob('[Pp]y[Jj][Ww][Tt]-2.15.1.dist-info/METADATA')); "
                "assert len(matches) == 1, matches; "
                "print(matches[0].read_text(encoding='utf-8'), end='')"
            ),
        ],
        timeout=120,
    )
    (out_dir / "pyjwt-METADATA.txt").write_text(pyjwt_meta, encoding="utf-8")


def evaluate_live_trivy_freshness(
    source_root: Path,
    out_dir: Path,
    *,
    bundle_dir: Path | None = None,
    max_db_age_hours: float = 24.0,
    eval_now: datetime | None = None,
) -> dict[str, object]:
    assert_zero_forbidden_env()
    source_root = source_root.resolve()
    out_dir = out_dir.resolve()
    sealed_bundle = (
        bundle_dir.resolve()
        if bundle_dir is not None
        else (Path(__file__).resolve().parent / "bundle").resolve()
    )

    require(
        out_dir != sealed_bundle
        and not out_dir.is_relative_to(sealed_bundle)
        and not sealed_bundle.is_relative_to(out_dir),
        "Freshness scan output directory must be strictly separate from sealed bundle directory",
    )
    require(
        out_dir != source_root and not out_dir.is_relative_to(source_root),
        "Freshness scan output directory must not write inside accepted candidate checkout",
    )

    assert_sealed_offline_bundle_untouched(sealed_bundle)
    require(
        run_checked(["git", "rev-parse", "HEAD"], cwd=source_root).strip()
        == CANDIDATE_SHA,
        "Accepted candidate checkout HEAD mismatch",
    )
    require(
        not run_checked(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=source_root
        ).strip(),
        "Accepted candidate checkout has tracked modifications",
    )

    exc_path = source_root / "deploy/release/cve-exceptions.json"
    require(
        file_sha(exc_path) == EXCEPTIONS_SHA,
        "Accepted candidate cve-exceptions.json SHA-256 mismatch",
    )
    require(
        json.loads(exc_path.read_text(encoding="utf-8")).get("exceptions") == [],
        "Accepted candidate cve-exceptions.json must keep exceptions: []",
    )

    trivy_json_path = out_dir / "trivy-image-fresh.json"
    db_meta_path = out_dir / "trivy-db-metadata.json"
    ver_json_path = out_dir / "trivy-version.json"
    pyjwt_path = out_dir / "pyjwt-METADATA.txt"
    for req_file in (trivy_json_path, db_meta_path, ver_json_path, pyjwt_path):
        require(
            req_file.is_file(), f"Missing required fresh scan input: {req_file.name}"
        )

    ver_doc = json.loads(ver_json_path.read_text(encoding="utf-8"))
    db_meta = json.loads(db_meta_path.read_text(encoding="utf-8"))
    trivy_raw = json.loads(trivy_json_path.read_text(encoding="utf-8"))

    require(
        str(ver_doc.get("Version") or "") == EXPECTED_TRIVY_VERSION,
        f"Unexpected Trivy version: {ver_doc.get('Version')!r} != {EXPECTED_TRIVY_VERSION!r}",
    )
    require(
        trivy_raw.get("ArtifactName") == IMAGE,
        f"Trivy ArtifactName mismatch: {trivy_raw.get('ArtifactName')!r}",
    )
    meta_block = trivy_raw.get("Metadata") or {}
    require(
        meta_block.get("ImageID") == IMAGE_ID,
        f"Trivy ImageID mismatch: {meta_block.get('ImageID')!r}",
    )
    require(
        IMAGE in (meta_block.get("RepoDigests") or []),
        "Trivy Metadata.RepoDigests missing canonical candidate @sha256 digest",
    )
    require(
        file_sha(pyjwt_path) == PYJWT_METADATA_SHA,
        "Extracted pyjwt-METADATA.txt SHA-256 mismatch",
    )

    now_utc = (eval_now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scan_dt = parse_iso_utc(str(trivy_raw.get("CreatedAt") or ""))
    db_updated_dt = parse_iso_utc(str(db_meta.get("UpdatedAt") or ""))
    db_next_update_dt = parse_iso_utc(str(db_meta.get("NextUpdate") or ""))
    db_downloaded_dt = parse_iso_utc(str(db_meta.get("DownloadedAt") or ""))

    max_age_sec = max_db_age_hours * 3600.0
    db_age_at_scan_sec = (scan_dt - db_updated_dt).total_seconds()
    db_age_at_eval_sec = (now_utc - db_updated_dt).total_seconds()
    scan_age_sec = (now_utc - scan_dt).total_seconds()

    failure_reasons: list[str] = []
    if int(db_meta.get("Version") or 0) != 2:
        failure_reasons.append(
            f"Unexpected Trivy DB schema version: {db_meta.get('Version')!r}"
        )
    if db_age_at_scan_sec < -CLOCK_SKEW_TOLERANCE_SECONDS:
        failure_reasons.append(
            f"Trivy DB UpdatedAt ({db_updated_dt.isoformat()}) is in the future relative to scan CreatedAt ({scan_dt.isoformat()})"
        )
    if db_age_at_scan_sec > max_age_sec:
        failure_reasons.append(
            f"Trivy DB age at scan time ({db_age_at_scan_sec / 3600.0:.4f}h) exceeds {max_db_age_hours:.1f}h limit"
        )
    if (
        db_age_at_eval_sec < -CLOCK_SKEW_TOLERANCE_SECONDS
        or db_age_at_eval_sec > max_age_sec
    ):
        failure_reasons.append(
            f"Trivy DB age at evaluation time ({db_age_at_eval_sec / 3600.0:.4f}h) outside [-0.08h, {max_db_age_hours:.1f}h] window"
        )
    if scan_age_sec < -CLOCK_SKEW_TOLERANCE_SECONDS or scan_age_sec > max_age_sec:
        failure_reasons.append(
            f"Scan CreatedAt ({scan_dt.isoformat()}) is stale relative to evaluation time ({now_utc.isoformat()})"
        )

    strict_out_path = out_dir / "security-scan-strict-fresh.json"
    eval_proc = subprocess.run(
        [
            sys.executable,
            "-B",
            str(source_root / "deploy/release/evaluate_security_scan.py"),
            "--strict",
            "--expected-image",
            IMAGE,
            "--exceptions",
            str(exc_path),
            "--trivy-json",
            str(trivy_json_path),
            "--pyjwt-metadata-file",
            str(pyjwt_path),
            "--out",
            str(strict_out_path),
        ],
        cwd=source_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    require(
        strict_out_path.is_file(),
        f"evaluate_security_scan.py failed to produce output: {eval_proc.stderr[-2000:]}",
    )
    strict_doc = json.loads(strict_out_path.read_text(encoding="utf-8"))
    if (
        eval_proc.returncode != 0
        or strict_doc.get("status") != "PASS"
        or strict_doc.get("gate_mode") != "strict"
        or int((strict_doc.get("severity_counts") or {}).get("CRITICAL", -1)) != 0
        or int(strict_doc.get("secret_findings_count", -1)) != 0
        or int(strict_doc.get("rejected_exceptions_count", -1)) != 0
    ):
        failure_reasons.extend(
            strict_doc.get("gate_failures")
            or [
                f"evaluate_security_scan.py --strict returned rc={eval_proc.returncode}"
            ]
        )

    assert_sealed_offline_bundle_untouched(sealed_bundle)
    db_age_within_limit = (
        -CLOCK_SKEW_TOLERANCE_SECONDS <= db_age_at_scan_sec <= max_age_sec
        and -CLOCK_SKEW_TOLERANCE_SECONDS <= db_age_at_eval_sec <= max_age_sec
    )
    status = "PASS" if not failure_reasons else "FAIL"

    report: dict[str, object] = {
        "schema_version": "cskh-p4-dk03-live-trivy-freshness-scan-v1",
        "control_id": "DK-03",
        "status": status,
        "evaluated_at_utc": now_utc.isoformat(),
        "scanned_at_utc": scan_dt.isoformat(),
        "candidate_git_sha": CANDIDATE_SHA,
        "workflow_tool_git_sha": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "image": IMAGE,
        "image_id": IMAGE_ID,
        "trivy_tool": {
            "pinned_image": TRIVY_IMAGE,
            "pulled_image_id": ver_doc.get("PulledTrivyImageID"),
            "version": ver_doc.get("Version"),
            "db_repositories": TRIVY_DB_REPOSITORIES,
        },
        "vulnerability_db": {
            "schema_version": db_meta.get("Version"),
            "updated_at_utc": db_updated_dt.isoformat(),
            "downloaded_at_utc": db_downloaded_dt.isoformat(),
            "next_update_utc": db_next_update_dt.isoformat(),
            "age_at_scan_seconds": round(db_age_at_scan_sec, 3),
            "age_at_scan_hours": round(db_age_at_scan_sec / 3600.0, 6),
            "age_at_eval_seconds": round(db_age_at_eval_sec, 3),
            "age_at_eval_hours": round(db_age_at_eval_sec / 3600.0, 6),
            "max_allowed_age_hours": max_db_age_hours,
            "db_age_within_24h": db_age_within_limit,
        },
        "strict_evaluation": {
            "status": strict_doc.get("status"),
            "gate_mode": strict_doc.get("gate_mode"),
            "cve_exceptions_sha256": EXCEPTIONS_SHA,
            "active_cve_exceptions_count": 0,
            "severity_counts": strict_doc.get("severity_counts"),
            "unique_critical_counts": strict_doc.get("unique_critical_counts"),
            "secret_findings_count": strict_doc.get("secret_findings_count"),
            "excepted_secret_false_positives_count": strict_doc.get(
                "excepted_secret_false_positives_count"
            ),
            "unapproved_fixable_critical": strict_doc.get(
                "unique_fixable_critical_unapproved"
            ),
            "unapproved_unfixed_os_critical": strict_doc.get(
                "unique_unfixed_critical_upstream_os"
            ),
            "unapproved_secrets": strict_doc.get("secret_findings"),
            "gate_failures": strict_doc.get("gate_failures"),
        },
        "artifacts_sha256": {
            "trivy_image_fresh_json": file_sha(trivy_json_path),
            "security_scan_strict_fresh_json": file_sha(strict_out_path),
            "trivy_db_metadata_json": file_sha(db_meta_path),
            "trivy_version_json": file_sha(ver_json_path),
            "pyjwt_metadata_txt": file_sha(pyjwt_path),
        },
        "failure_reasons": failure_reasons,
        "release_secrets_present": False,
        "sealed_offline_bundle_untouched": True,
        "sealed_offline_bundle_sha256sums": BUNDLE_SUMS_SHA,
        "rebuild_performed": False,
        "g18_rerun_performed": False,
        "live_vps_used": False,
        "g04_status_changed": False,
        "g23_verdict": "NO-GO",
    }
    (out_dir / "live-trivy-freshness-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    write_sha256sums(out_dir)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--bundle-dir", type=Path, default=Path(__file__).resolve().parent / "bundle"
    )
    parser.add_argument("--run-docker-scan", action="store_true")
    parser.add_argument("--max-db-age-hours", type=float, default=24.0)
    args = parser.parse_args()

    try:
        if args.run_docker_scan:
            execute_docker_trivy_scan(args.out_dir.resolve())
        report = evaluate_live_trivy_freshness(
            args.source_root,
            args.out_dir,
            bundle_dir=args.bundle_dir,
            max_db_age_hours=args.max_db_age_hours,
        )
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}), file=sys.stderr)
        return 1

    print(json.dumps(report))
    if report["status"] != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
