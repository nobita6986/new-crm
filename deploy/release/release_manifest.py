#!/usr/bin/env python3
"""Canonical release-manifest generator, Ed25519 signer, and fail-closed verifier (P4-02 / G04).

Implements P4.md §4.1 and docs/p4/p4-02-pipeline-input-contract.md §4:
- Never accepts tag-only, placeholder, or BLOCKED image references.
- Derives `migration_revision` from `apps/crm_cskh/crm_cskh/patches.txt` and
  `kpi_formula_version` from `apps/crm_cskh/crm_cskh/services/kpi_calculator.py`.
- Enforces that `production_candidate` manifests can ONLY be generated AND signed when
  a `strict` security scan (`gate_mode == "strict"`, `status == "PASS"`, zero unapproved
  Critical CVEs, zero secrets) matches the exact `manifest.image` `@sha256` digest
  (blocking cross-digest scan extrapolation between CI/live/release images).
- Signs exact UTF-8 manifest bytes with Ed25519 (via `cryptography` or `openssl` CLI fallback)
  without writing secrets to logs or stdout.
- Enforces `environment_scope` separation so a `ci_ephemeral_rehearsal` manifest is
  cryptographically valid for CI testing yet strictly rejected by Staging/Production deployers.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCK_PATH = REPO_ROOT / "deploy" / "images" / "release-lock.json"
PATCHES_TXT_PATH = REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "patches.txt"
KPI_CALC_PATH = (
    REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "services" / "kpi_calculator.py"
)

REGISTRY_DIGEST_RE = re.compile(
    r"\A[^/@\s:<>]+(?::[0-9]+)?(?:/[^/@\s:<>]+)+(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}\Z"
)
IMAGE_ID_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
SHA40_RE = re.compile(r"\A[0-9a-f]{40}\Z")
SHA64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
ZERO_DIGEST_SUFFIX = "@sha256:" + ("0" * 64)
ALLOWED_SCOPES = ("production_candidate", "ci_ephemeral_rehearsal")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_latest_migration_revision(patches_path: Path = PATCHES_TXT_PATH) -> str:
    lines = [
        ln.strip()
        for ln in patches_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
        and not ln.strip().startswith("#")
        and not ln.strip().startswith("[")
    ]
    if not lines:
        raise ValueError(f"No migration patches found in {patches_path}")
    return lines[-1]


def read_kpi_formula_version(kpi_calc_path: Path = KPI_CALC_PATH) -> str:
    tree = ast.parse(
        kpi_calc_path.read_text(encoding="utf-8"), filename=str(kpi_calc_path)
    )
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "METRIC_VERSION":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    raise ValueError(f"METRIC_VERSION constant not found in {kpi_calc_path}")


def validate_registry_digest(image_ref: str) -> None:
    if not image_ref or not isinstance(image_ref, str):
        raise ValueError("Image digest reference is empty or missing")
    if (
        "BLOCKED" in image_ref.upper()
        or "ACTUAL_DIGEST" in image_ref
        or "<" in image_ref
    ):
        raise ValueError(
            f"Refusing placeholder or BLOCKED image reference: {image_ref!r}"
        )
    if image_ref.endswith(ZERO_DIGEST_SUFFIX):
        raise ValueError(f"Refusing all-zero placeholder digest: {image_ref!r}")
    if not REGISTRY_DIGEST_RE.match(image_ref):
        raise ValueError(
            f"Image reference {image_ref!r} must match '<registry>/<repo>@sha256:<64-hex>'"
        )


def verify_security_scan_for_manifest(
    scan_doc: dict[str, object],
    *,
    environment_scope: str,
    expected_image: str,
) -> None:
    """Enforces fail-closed scan & same-digest invariants before manifest generate or sign."""
    if (
        scan_doc.get("status") != "PASS"
        or int(scan_doc.get("secret_findings_count", -1)) != 0
    ):
        raise ValueError(
            f"Refusing security scan with non-PASS status or secret findings "
            f"(status={scan_doc.get('status')!r}, secrets={scan_doc.get('secret_findings_count')!r})"
        )
    if scan_doc.get("gate_failures"):
        raise ValueError(
            f"Refusing security scan with non-empty gate_failures: {scan_doc.get('gate_failures')!r}"
        )

    scanned_artifact = str(scan_doc.get("artifact_name") or "").strip()
    if scanned_artifact != expected_image:
        raise ValueError(
            "Security scan digest mismatch (cross-image scan extrapolation forbidden): "
            f"scan artifact_name={scanned_artifact!r} != manifest image={expected_image!r}"
        )

    if environment_scope == "production_candidate":
        gate_mode = scan_doc.get("gate_mode")
        if gate_mode != "strict":
            raise ValueError(
                "production_candidate release-manifest.json requires explicit security scan "
                f"gate_mode='strict', got {gate_mode!r}"
            )
        crit_counts = scan_doc.get("unique_critical_counts")
        if isinstance(crit_counts, dict):
            fixable_unapproved = int(crit_counts.get("fixable_unapproved", 0))
            unfixed_unapproved = int(
                crit_counts.get("unfixed_upstream_os_unapproved", 0)
            )
            if fixable_unapproved != 0 or unfixed_unapproved != 0:
                raise ValueError(
                    "production_candidate manifest cannot be generated or signed while "
                    f"unapproved Critical CVEs remain (fixable={fixable_unapproved}, "
                    f"unfixed_os={unfixed_unapproved})"
                )
        if scan_doc.get("fixable_critical_unapproved") or scan_doc.get(
            "unfixed_critical_upstream_os"
        ):
            raise ValueError(
                "production_candidate manifest cannot be generated or signed while "
                "unapproved Critical CVE lists are non-empty"
            )


def _find_openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
    if git_openssl.exists():
        return str(git_openssl)
    return None


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        priv = ed25519.Ed25519PrivateKey.generate()
        pub = priv.public_key()
        priv_pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return priv_pem, pub_pem
    except ImportError:
        openssl_bin = _find_openssl()
        if not openssl_bin:
            raise RuntimeError(
                "Neither python `cryptography` nor `openssl` CLI is available"
            ) from None
        with tempfile.TemporaryDirectory() as td:
            priv_file = Path(td) / "priv.pem"
            pub_file = Path(td) / "pub.pem"
            subprocess.run(
                [
                    openssl_bin,
                    "genpkey",
                    "-algorithm",
                    "ED25519",
                    "-out",
                    str(priv_file),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    openssl_bin,
                    "pkey",
                    "-in",
                    str(priv_file),
                    "-pubout",
                    "-out",
                    str(pub_file),
                ],
                check=True,
                capture_output=True,
            )
            return priv_file.read_bytes(), pub_file.read_bytes()


def ed25519_sign(payload: bytes, private_key_pem: bytes) -> tuple[bytes, bytes]:
    """Returns `(raw_signature_64_bytes, public_key_pem_bytes)`."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        priv = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(priv, ed25519.Ed25519PrivateKey):
            raise ValueError("Signing key must be an Ed25519 private key")
        sig = priv.sign(payload)
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return sig, pub_pem
    except ImportError:
        openssl_bin = _find_openssl()
        if not openssl_bin:
            raise RuntimeError(
                "Neither python `cryptography` nor `openssl` CLI is available"
            ) from None
        with tempfile.TemporaryDirectory() as td:
            priv_file = Path(td) / "priv.pem"
            pub_file = Path(td) / "pub.pem"
            msg_file = Path(td) / "msg.bin"
            sig_file = Path(td) / "sig.bin"
            priv_file.write_bytes(private_key_pem)
            os.chmod(priv_file, 0o600)
            msg_file.write_bytes(payload)
            subprocess.run(
                [
                    openssl_bin,
                    "pkeyutl",
                    "-sign",
                    "-inkey",
                    str(priv_file),
                    "-rawin",
                    "-in",
                    str(msg_file),
                    "-out",
                    str(sig_file),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    openssl_bin,
                    "pkey",
                    "-in",
                    str(priv_file),
                    "-pubout",
                    "-out",
                    str(pub_file),
                ],
                check=True,
                capture_output=True,
            )
            return sig_file.read_bytes(), pub_file.read_bytes()


def ed25519_verify(payload: bytes, signature: bytes, public_key_pem: bytes) -> None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        pub = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(pub, ed25519.Ed25519PublicKey):
            raise ValueError("Verification key must be an Ed25519 public key")
        pub.verify(signature, payload)
    except ImportError:
        openssl_bin = _find_openssl()
        if not openssl_bin:
            raise RuntimeError(
                "Neither python `cryptography` nor `openssl` CLI is available"
            ) from None
        with tempfile.TemporaryDirectory() as td:
            pub_file = Path(td) / "pub.pem"
            msg_file = Path(td) / "msg.bin"
            sig_file = Path(td) / "sig.bin"
            pub_file.write_bytes(public_key_pem)
            msg_file.write_bytes(payload)
            sig_file.write_bytes(signature)
            res = subprocess.run(
                [
                    openssl_bin,
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(pub_file),
                    "-rawin",
                    "-in",
                    str(msg_file),
                    "-sigfile",
                    str(sig_file),
                ],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                raise ValueError(
                    f"OpenSSL Ed25519 signature verification failed: {res.stderr.strip()}"
                )


def normalize_public_key_fingerprint(public_key_pem: bytes) -> str:
    clean_lines = [
        ln.strip()
        for ln in public_key_pem.decode("utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("-----")
    ]
    der_bytes = base64.b64decode("".join(clean_lines))
    return sha256_bytes(der_bytes)


def build_manifest_dict(
    *,
    release_id: str,
    environment_scope: str,
    lock_path: Path,
    build_manifest_path: Path,
    image_inspect_path: Path,
    sbom_cyclonedx_path: Path,
    sbom_spdx_path: Path | None,
    security_scan_path: Path,
    build_url: str,
    test_report_url: str,
    previous_release_id: str | None,
    approved_by: list[str],
) -> dict[str, object]:
    if environment_scope not in ALLOWED_SCOPES:
        raise ValueError(
            f"environment_scope must be one of {ALLOWED_SCOPES}, got {environment_scope!r}"
        )
    if not release_id or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,80}$", release_id):
        raise ValueError(f"Invalid release_id: {release_id!r}")

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    build_meta = json.loads(build_manifest_path.read_text(encoding="utf-8"))
    inspect_meta = json.loads(image_inspect_path.read_text(encoding="utf-8"))

    reg_ver = inspect_meta.get("registry_verification") or {}
    reg_status = reg_ver.get("status")
    reg_digest = reg_ver.get("approved_registry_repo_digest")
    if reg_status != "VERIFIED" or not reg_digest:
        raise ValueError(
            f"Cannot generate release-manifest.json while registry_verification.status={reg_status!r} "
            f"(approved_registry_repo_digest={reg_digest!r})"
        )
    validate_registry_digest(reg_digest)

    image_id = str(inspect_meta.get("local_image_id") or "")
    if not IMAGE_ID_RE.match(image_id):
        raise ValueError(
            f"Invalid local_image_id in image-inspect-summary.json: {image_id!r}"
        )

    frappe_meta = build_meta["apps"]["frappe"]
    crm_meta = build_meta["apps"]["crm"]
    cskh_meta = build_meta["apps"]["crm_cskh"]

    if frappe_meta["commit_sha"] != lock["apps"]["frappe"]["commit_sha"]:
        raise ValueError(
            "Frappe commit_sha mismatch between build-manifest.json and release-lock.json"
        )
    if crm_meta["commit_sha"] != lock["apps"]["crm"]["commit_sha"]:
        raise ValueError(
            "CRM commit_sha mismatch between build-manifest.json and release-lock.json"
        )
    if cskh_meta["apps_tree_sha"] != lock["apps_tree_sha"]:
        raise ValueError(
            "apps_tree_sha mismatch between build-manifest.json and release-lock.json"
        )
    if cskh_meta["crm_cskh_tree_sha"] != lock["crm_cskh_tree_sha"]:
        raise ValueError(
            "crm_cskh_tree_sha mismatch between build-manifest.json and release-lock.json"
        )

    git_sha = str(cskh_meta["source_commit_sha"])
    if not SHA40_RE.match(git_sha):
        raise ValueError(
            f"source_commit_sha must be 40-char lowercase hex, got {git_sha!r}"
        )

    if not sbom_cyclonedx_path.is_file() or sbom_cyclonedx_path.stat().st_size == 0:
        raise ValueError(f"Missing or empty CycloneDX SBOM: {sbom_cyclonedx_path}")
    if not security_scan_path.is_file() or security_scan_path.stat().st_size == 0:
        raise ValueError(f"Missing or empty security scan report: {security_scan_path}")
    scan_doc = json.loads(security_scan_path.read_text(encoding="utf-8"))
    verify_security_scan_for_manifest(
        scan_doc,
        environment_scope=environment_scope,
        expected_image=reg_digest,
    )

    migration_rev = read_latest_migration_revision()
    kpi_version = read_kpi_formula_version()

    artifacts: dict[str, str] = {
        "build_manifest_sha256": sha256_file(build_manifest_path),
        "image_inspect_summary_sha256": sha256_file(image_inspect_path),
        "sbom_cyclonedx_sha256": sha256_file(sbom_cyclonedx_path),
        "security_scan_sha256": sha256_file(security_scan_path),
    }
    if sbom_spdx_path and sbom_spdx_path.is_file():
        artifacts["sbom_spdx_sha256"] = sha256_file(sbom_spdx_path)

    return {
        "schema_version": "cskh-p4-release-manifest-v1",
        "release_id": release_id,
        "environment_scope": environment_scope,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "apps_tree_sha": cskh_meta["apps_tree_sha"],
        "crm_cskh_tree_sha": cskh_meta["crm_cskh_tree_sha"],
        "frappe_framework_ref": f"{frappe_meta['tag']}@{frappe_meta['commit_sha']}",
        "frappe_crm_ref": f"{crm_meta['tag']}@{crm_meta['commit_sha']}",
        "crm_cskh_ref": f"{git_sha} (tree:{cskh_meta['crm_cskh_tree_sha']})",
        "image": reg_digest,
        "image_id": image_id,
        "migration_revision": migration_rev,
        "kpi_formula_version": kpi_version,
        "previous_release_id": previous_release_id,
        "build_url": build_url,
        "test_report_url": test_report_url,
        "approved_by": approved_by,
        "artifacts": artifacts,
    }


def validate_manifest_dict(
    manifest: dict[str, object],
    *,
    lock_path: Path = DEFAULT_LOCK_PATH,
    expected_image: str | None = None,
    expected_scope: str | None = None,
) -> None:
    if manifest.get("schema_version") != "cskh-p4-release-manifest-v1":
        raise ValueError(
            f"Unsupported schema_version: {manifest.get('schema_version')!r}"
        )

    scope = str(manifest.get("environment_scope") or "")
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"Invalid environment_scope: {scope!r}")
    if expected_scope and scope != expected_scope:
        raise ValueError(
            f"Manifest environment_scope mismatch: actual={scope!r}, expected={expected_scope!r}"
        )

    image_ref = str(manifest.get("image") or "")
    validate_registry_digest(image_ref)
    if expected_image and image_ref != expected_image:
        raise ValueError(
            f"Manifest image digest drift: manifest={image_ref!r} != expected={expected_image!r}"
        )

    image_id = str(manifest.get("image_id") or "")
    if not IMAGE_ID_RE.match(image_id):
        raise ValueError(f"Invalid image_id in manifest: {image_id!r}")

    git_sha = str(manifest.get("git_sha") or "")
    if not SHA40_RE.match(git_sha):
        raise ValueError(f"Invalid 40-char git_sha in manifest: {git_sha!r}")

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    expected_frappe_ref = (
        f"{lock['apps']['frappe']['tag']}@{lock['apps']['frappe']['commit_sha']}"
    )
    expected_crm_ref = (
        f"{lock['apps']['crm']['tag']}@{lock['apps']['crm']['commit_sha']}"
    )

    if manifest.get("frappe_framework_ref") != expected_frappe_ref:
        raise ValueError(
            f"frappe_framework_ref {manifest.get('frappe_framework_ref')!r} != lock {expected_frappe_ref!r}"
        )
    if manifest.get("frappe_crm_ref") != expected_crm_ref:
        raise ValueError(
            f"frappe_crm_ref {manifest.get('frappe_crm_ref')!r} != lock {expected_crm_ref!r}"
        )
    if manifest.get("apps_tree_sha") != lock["apps_tree_sha"]:
        raise ValueError("apps_tree_sha in manifest does not match release-lock.json")
    if manifest.get("crm_cskh_tree_sha") != lock["crm_cskh_tree_sha"]:
        raise ValueError(
            "crm_cskh_tree_sha in manifest does not match release-lock.json"
        )

    expected_mig = read_latest_migration_revision()
    expected_kpi = read_kpi_formula_version()
    if manifest.get("migration_revision") != expected_mig:
        raise ValueError(
            f"migration_revision {manifest.get('migration_revision')!r} != patches.txt {expected_mig!r}"
        )
    if manifest.get("kpi_formula_version") != expected_kpi:
        raise ValueError(
            f"kpi_formula_version {manifest.get('kpi_formula_version')!r} != source {expected_kpi!r}"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("Manifest missing `artifacts` checksum dictionary")
    for req_key in (
        "build_manifest_sha256",
        "sbom_cyclonedx_sha256",
        "security_scan_sha256",
    ):
        val = str(artifacts.get(req_key) or "")
        if not SHA64_RE.match(val):
            raise ValueError(
                f"Invalid SHA-256 checksum for artifacts.{req_key}: {val!r}"
            )


def _load_key_bytes(*, file_arg: str | None, env_arg: str | None, label: str) -> bytes:
    if file_arg:
        raw = Path(file_arg).read_bytes()
    elif env_arg:
        val = os.environ.get(env_arg, "").strip()
        if not val:
            raise ValueError(
                f"Environment variable {env_arg} for {label} is empty or unset"
            )
        raw = val.encode("utf-8")
    else:
        raise ValueError(f"Must specify either file or env variable for {label}")
    return raw.replace(b"\r\n", b"\n")


def cmd_keygen(args: argparse.Namespace) -> int:
    priv_pem, pub_pem = generate_ed25519_keypair()
    priv_path = Path(args.private_key_out)
    pub_path = Path(args.public_key_out)
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_bytes(priv_pem.replace(b"\r\n", b"\n"))
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass
    pub_path.write_bytes(pub_pem.replace(b"\r\n", b"\n"))
    fp = normalize_public_key_fingerprint(pub_pem)
    print(json.dumps({"status": "OK", "public_key_fingerprint_sha256": fp}))
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    approved_by = [x.strip() for x in (args.approved_by or "").split(",") if x.strip()]
    manifest = build_manifest_dict(
        release_id=args.release_id,
        environment_scope=args.environment_scope,
        lock_path=Path(args.lock),
        build_manifest_path=Path(args.build_manifest),
        image_inspect_path=Path(args.image_inspect),
        sbom_cyclonedx_path=Path(args.sbom_cyclonedx),
        sbom_spdx_path=Path(args.sbom_spdx) if args.sbom_spdx else None,
        security_scan_path=Path(args.security_scan),
        build_url=args.build_url,
        test_report_url=args.test_report_url,
        previous_release_id=args.previous_release_id or None,
        approved_by=approved_by,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    out_path.write_bytes(rendered)
    print(
        json.dumps(
            {
                "status": "GENERATED",
                "manifest_path": str(out_path),
                "manifest_sha256": sha256_bytes(rendered),
                "image": manifest["image"],
                "environment_scope": manifest["environment_scope"],
            }
        )
    )
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    payload = manifest_path.read_bytes()
    manifest_obj = json.loads(payload.decode("utf-8"))
    validate_manifest_dict(manifest_obj, lock_path=Path(args.lock))

    scope = str(manifest_obj.get("environment_scope") or "")
    scan_arg = str(getattr(args, "security_scan", "") or "").strip()
    if scope == "production_candidate" and not scan_arg:
        raise ValueError(
            "Signing a production_candidate manifest requires --security-scan "
            "pointing to the strict PASS security scan report for that exact @sha256 digest"
        )
    if scan_arg:
        scan_path = Path(scan_arg)
        if not scan_path.is_file():
            raise ValueError(f"Missing security scan file for signing: {scan_path}")
        artifacts = manifest_obj.get("artifacts")
        assert isinstance(artifacts, dict)
        expected_scan_sha = str(artifacts.get("security_scan_sha256") or "")
        actual_scan_sha = sha256_file(scan_path)
        if actual_scan_sha != expected_scan_sha:
            raise ValueError(
                "Security scan SHA-256 mismatch during manifest signing: "
                f"scan file={actual_scan_sha} != manifest.artifacts.security_scan_sha256={expected_scan_sha}"
            )
        scan_doc = json.loads(scan_path.read_text(encoding="utf-8"))
        verify_security_scan_for_manifest(
            scan_doc,
            environment_scope=scope,
            expected_image=str(manifest_obj["image"]),
        )

    priv_pem = _load_key_bytes(
        file_arg=args.private_key_file,
        env_arg=args.private_key_env,
        label="Ed25519 private signing key",
    )
    raw_sig, pub_pem = ed25519_sign(payload, priv_pem)
    sig_b64 = base64.b64encode(raw_sig).decode("ascii")
    pub_fp = normalize_public_key_fingerprint(pub_pem)

    sig_path = Path(args.sig_out)
    sig_path.parent.mkdir(parents=True, exist_ok=True)
    sig_path.write_bytes((sig_b64 + "\n").encode("ascii"))

    envelope = {
        "schema_version": "cskh-p4-signed-manifest-envelope-v1",
        "signature_algorithm": "ed25519",
        "payload_sha256": sha256_bytes(payload),
        "public_key_fingerprint_sha256": pub_fp,
        "public_key_pem": pub_pem.decode("utf-8").strip(),
        "signature_base64": sig_b64,
        "signed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if args.envelope_out:
        env_path = Path(args.envelope_out)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_bytes((json.dumps(envelope, indent=2) + "\n").encode("utf-8"))

    print(
        json.dumps(
            {
                "status": "SIGNED",
                "signature_algorithm": "ed25519",
                "payload_sha256": envelope["payload_sha256"],
                "public_key_fingerprint_sha256": pub_fp,
                "sig_path": str(sig_path),
            }
        )
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    sig_path = Path(args.sig)
    payload = manifest_path.read_bytes()
    sig_b64 = sig_path.read_text(encoding="ascii").strip()
    raw_sig = base64.b64decode(sig_b64, validate=True)

    pub_pem = _load_key_bytes(
        file_arg=args.public_key_file,
        env_arg=args.public_key_env,
        label="Ed25519 public verification key",
    )
    ed25519_verify(payload, raw_sig, pub_pem)

    manifest_obj = json.loads(payload.decode("utf-8"))
    validate_manifest_dict(
        manifest_obj,
        lock_path=Path(args.lock),
        expected_image=args.expected_image,
        expected_scope=args.expected_scope,
    )
    pub_fp = normalize_public_key_fingerprint(pub_pem)
    print(
        json.dumps(
            {
                "status": "VERIFIED",
                "release_id": manifest_obj["release_id"],
                "environment_scope": manifest_obj["environment_scope"],
                "image": manifest_obj["image"],
                "image_id": manifest_obj["image_id"],
                "git_sha": manifest_obj["git_sha"],
                "payload_sha256": sha256_bytes(payload),
                "public_key_fingerprint_sha256": pub_fp,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    kg = sub.add_parser("keygen", help="Generate an Ed25519 PEM keypair")
    kg.add_argument("--private-key-out", required=True)
    kg.add_argument("--public-key-out", required=True)
    kg.set_defaults(func=cmd_keygen)

    gen = sub.add_parser("generate", help="Generate canonical release-manifest.json")
    gen.add_argument("--release-id", required=True)
    gen.add_argument(
        "--environment-scope",
        default="production_candidate",
        choices=list(ALLOWED_SCOPES),
    )
    gen.add_argument("--lock", default=str(DEFAULT_LOCK_PATH))
    gen.add_argument("--build-manifest", required=True)
    gen.add_argument("--image-inspect", required=True)
    gen.add_argument("--sbom-cyclonedx", required=True)
    gen.add_argument("--sbom-spdx", default="")
    gen.add_argument("--security-scan", required=True)
    gen.add_argument("--build-url", required=True)
    gen.add_argument("--test-report-url", required=True)
    gen.add_argument("--previous-release-id", default="")
    gen.add_argument("--approved-by", default="")
    gen.add_argument("--out", required=True)
    gen.set_defaults(func=cmd_generate)

    sg = sub.add_parser("sign", help="Sign release-manifest.json with Ed25519")
    sg.add_argument("--manifest", required=True)
    sg.add_argument("--lock", default=str(DEFAULT_LOCK_PATH))
    sg.add_argument(
        "--security-scan",
        default="",
        help="Path to security-scan JSON (mandatory when signing production_candidate manifests)",
    )
    sg.add_argument("--private-key-file", default="")
    sg.add_argument("--private-key-env", default="RELEASE_SIGNING_PRIVATE_KEY")
    sg.add_argument("--sig-out", required=True)
    sg.add_argument("--envelope-out", default="")
    sg.set_defaults(func=cmd_sign)

    vf = sub.add_parser("verify", help="Verify Ed25519 signature and lock invariants")
    vf.add_argument("--manifest", required=True)
    vf.add_argument("--sig", required=True)
    vf.add_argument("--lock", default=str(DEFAULT_LOCK_PATH))
    vf.add_argument("--public-key-file", default="")
    vf.add_argument("--public-key-env", default="RELEASE_SIGNING_PUBLIC_KEY")
    vf.add_argument("--expected-image", default="")
    vf.add_argument("--expected-scope", default="")
    vf.set_defaults(func=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
