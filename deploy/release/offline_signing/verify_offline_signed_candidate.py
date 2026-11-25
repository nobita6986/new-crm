#!/usr/bin/env python3
"""Verify the approved offline-signed candidate without release or deploy secrets.

This supplies additional CI evidence. It does not attest that registry-build ran,
approve the alternative signing policy, or authorize a Production deployment.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
import urllib.parse
import urllib.request


CANDIDATE_SHA = "3d0c47486c78c09a966a3ea9100c477f53fe6a79"
IMAGE = "ghcr.io/nobita6986/crm-cskh@sha256:f76c101b0d102977d3b185da531797c0c10b02bbdd648214fe6cef76525c1d7c"
IMAGE_ID = "sha256:04b97d587ec12491edee7197f3392df642e41c61b4f2a2e5ff92de779b7d6061"
OFFICIAL_FP = "b1ed6b9e26baa2ef6e415e96a99456d59020111c434c70a7bb73b96ca0627931"
BUNDLE_SUMS_SHA = "1e96b06efab6174bc43e57694bcea17b5c41e753bb6c0d4ac2bb3951439017d2"
EXCEPTIONS_SHA = "2e00aa50e874968800238413568a691c8f21f33d2a29c41123ab475acac6eacd"
FORBIDDEN_ENV = (
    "RELEASE_SIGNING_PRIVATE_KEY",
    "REGISTRY_PUSH_TOKEN",
    "REGISTRY_PULL_TOKEN",
    "STAGING_SSH_PRIVATE_KEY",
    "PROD_SSH_PRIVATE_KEY",
    "SSH_AUTH_SOCK",
)
ARTIFACT_FILES = {
    "build_manifest_sha256": "build-manifest.json",
    "image_inspect_summary_sha256": "image-inspect-summary.json",
    "sbom_cyclonedx_sha256": "sbom.cyclonedx.json",
    "sbom_spdx_sha256": "sbom.spdx.json",
    "security_scan_sha256": "security-scan-strict.json",
    "cve_exceptions_sha256": "cve-exceptions.json",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(command: list[str], cwd: Path) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    require(
        result.returncode == 0, f"Verification command failed: {result.stderr[-2000:]}"
    )
    return result.stdout


def verify_registry(out_dir: Path) -> dict[str, str]:
    """Read public OCI objects; do not push, rebuild, or pull image layers."""
    repo = "nobita6986/crm-cskh"
    query = urllib.parse.urlencode(
        {"service": "ghcr.io", "scope": f"repository:{repo}:pull"}
    )
    with urllib.request.urlopen(
        f"https://ghcr.io/token?{query}", timeout=30
    ) as response:
        token = json.load(response)["token"]

    def fetch(digest: str, filename: str) -> dict:
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{repo}/manifests/{digest}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": ", ".join(
                    (
                        "application/vnd.oci.image.manifest.v1+json",
                        "application/vnd.docker.distribution.manifest.v2+json",
                        "application/vnd.oci.image.index.v1+json",
                        "application/vnd.docker.distribution.manifest.list.v2+json",
                    )
                ),
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        require(
            "sha256:" + hashlib.sha256(raw).hexdigest() == digest,
            "Registry digest mismatch",
        )
        (out_dir / filename).write_bytes(raw)
        return json.loads(raw)

    obj = fetch(IMAGE.rsplit("@", 1)[1], "registry-manifest.json")
    if "manifests" in obj:
        candidates = [
            entry
            for entry in obj["manifests"]
            if entry.get("platform", {}).get("os") == "linux"
            and entry.get("platform", {}).get("architecture") == "amd64"
        ]
        require(len(candidates) == 1, "Registry platform is ambiguous")
        obj = fetch(candidates[0]["digest"], "registry-manifest-linux-amd64.json")
    require(obj["config"]["digest"] == IMAGE_ID, "Registry OCI config mismatch")
    return {"status": "VERIFIED", "image": IMAGE, "image_id": IMAGE_ID}


def verify(
    source_root: Path, bundle_dir: Path, out_dir: Path, *, registry: bool = False
) -> dict:
    source_root, bundle_dir = source_root.resolve(), bundle_dir.resolve()
    present = [name for name in FORBIDDEN_ENV if os.environ.get(name)]
    require(
        not present, f"Read-only verifier received forbidden credentials: {present}"
    )
    require(
        execute(["git", "rev-parse", "HEAD"], source_root).strip() == CANDIDATE_SHA,
        "Candidate checkout differs from the accepted application commit",
    )
    require(
        not execute(
            ["git", "status", "--porcelain", "--untracked-files=no"], source_root
        ).strip(),
        "Candidate checkout has tracked modifications",
    )

    sums_path = bundle_dir / "SHA256SUMS.txt"
    require(
        file_sha(sums_path) == BUNDLE_SUMS_SHA, "Approved bundle checksum list changed"
    )
    entries = {}
    for line in sums_path.read_text(encoding="ascii").splitlines():
        expected, relative = line.split(None, 1)
        require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, "Invalid checksum")
        relative = relative.strip()
        require(relative not in entries, "Duplicate checksum entry")
        path = bundle_dir / relative
        require(
            not path.is_symlink() and path.resolve().is_relative_to(bundle_dir),
            "Unsafe bundle path",
        )
        require(file_sha(path) == expected, f"Bundle checksum mismatch: {relative}")
        require(
            b"-----BEGIN PRIVATE KEY-----" not in path.read_bytes(),
            "Private key in public bundle",
        )
        entries[relative] = expected
    actual = {
        p.relative_to(bundle_dir).as_posix()
        for p in bundle_dir.rglob("*")
        if p.is_file() and p != sums_path
    }
    require(
        set(entries) == actual and len(entries) == 12, "Unexpected bundle inventory"
    )

    pub = (bundle_dir / "official-release-signing.pub").read_text(encoding="ascii")
    der = base64.b64decode(
        "".join(line for line in pub.splitlines() if not line.startswith("-----")),
        validate=True,
    )
    require(
        hashlib.sha256(der).hexdigest() == OFFICIAL_FP,
        "Official public key fingerprint mismatch",
    )
    manifest = json.loads(
        (bundle_dir / "release-manifest.json").read_text(encoding="utf-8")
    )
    require(
        manifest["git_sha"] == CANDIDATE_SHA and manifest["image"] == IMAGE,
        "Manifest candidate identity mismatch",
    )
    require(manifest["image_id"] == IMAGE_ID, "Manifest OCI config mismatch")
    for key, filename in ARTIFACT_FILES.items():
        require(
            manifest["artifacts"][key] == entries[filename],
            f"Artifact binding mismatch: {key}",
        )
    require(
        file_sha(source_root / "deploy/release/cve-exceptions.json") == EXCEPTIONS_SHA,
        "Accepted source exceptions changed",
    )
    require(
        entries["cve-exceptions.json"] == EXCEPTIONS_SHA, "Bundle exceptions changed"
    )
    require(
        json.loads((bundle_dir / "cve-exceptions.json").read_text())["exceptions"]
        == [],
        "Unexpected active CVE exceptions",
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    tool = source_root / "deploy/release/release_manifest.py"
    cmd = [
        sys.executable,
        "-B",
        str(tool),
        "verify",
        "--manifest",
        str(bundle_dir / "release-manifest.json"),
        "--sig",
        str(bundle_dir / "release-manifest.json.sig"),
        "--public-key-file",
        str(bundle_dir / "official-release-signing.pub"),
        "--lock",
        str(source_root / "deploy/images/release-lock.json"),
        "--expected-image",
        IMAGE,
        "--expected-scope",
        "production_candidate",
    ]
    signature = json.loads(execute(cmd, source_root).strip().splitlines()[-1])
    require(
        signature["status"] == "VERIFIED"
        and signature["public_key_fingerprint_sha256"] == OFFICIAL_FP,
        "Canonical signature verification failed",
    )
    scan_path = out_dir / "strict-scan-reevaluation.json"
    execute(
        [
            sys.executable,
            "-B",
            str(source_root / "deploy/release/evaluate_security_scan.py"),
            "--trivy-json",
            str(bundle_dir / "trivy-image.json"),
            "--pyjwt-metadata-file",
            str(bundle_dir / "pyjwt-METADATA.txt"),
            "--exceptions",
            str(bundle_dir / "cve-exceptions.json"),
            "--expected-image",
            IMAGE,
            "--strict",
            "--out",
            str(scan_path),
        ],
        source_root,
    )
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    require(
        scan["status"] == "PASS"
        and scan["severity_counts"]["CRITICAL"] == 0
        and scan["secret_findings_count"] == 0,
        "Strict scan reevaluation failed",
    )
    registry_result = (
        verify_registry(out_dir) if registry else {"status": "NOT_REQUESTED"}
    )
    report = {
        "schema_version": "cskh-p4-offline-signature-readonly-ci-verification-v1",
        "status": "PASS",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_git_sha": CANDIDATE_SHA,
        "workflow_tool_git_sha": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "image": IMAGE,
        "image_id": IMAGE_ID,
        "public_key_fingerprint_sha256": OFFICIAL_FP,
        "bundle_files_verified": len(entries),
        "artifact_bindings_verified": len(ARTIFACT_FILES),
        "signature": signature,
        "strict_scan_reevaluation": "PASS",
        "registry": registry_result,
        "scan_provenance": "Reevaluation of accepted Trivy JSON; not a new vulnerability database scan",
        "release_secrets_present": False,
        "github_registry_build_environment_used": False,
        "rebuild_performed": False,
        "image_layers_pulled": False,
        "g18_rerun_performed": False,
        "deployment_performed": False,
        "g04_alternative_policy_acceptance": "PENDING_T1C_AND_AUTHORIZED_POLICY_REVIEW",
        "g23_verdict": "NO-GO",
    }
    (out_dir / "offline-signature-verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--bundle-dir", type=Path, default=Path(__file__).parent / "bundle"
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--verify-registry", action="store_true")
    args = parser.parse_args()
    try:
        report = verify(
            args.source_root,
            args.bundle_dir,
            args.out_dir,
            registry=args.verify_registry,
        )
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
