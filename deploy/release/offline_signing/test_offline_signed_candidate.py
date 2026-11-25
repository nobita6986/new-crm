"""Exercise the real approved signature and fail-closed negative cases."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_offline_signed_candidate import IMAGE, verify


BUNDLE = Path(__file__).parent / "bundle"
SOURCE = Path(os.environ["P4_CANDIDATE_SOURCE_ROOT"])


class OfflineSignatureTests(unittest.TestCase):
    def canonical_verify(self, sig: Path, pub: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(SOURCE / "deploy/release/release_manifest.py"),
                "verify",
                "--manifest",
                str(BUNDLE / "release-manifest.json"),
                "--sig",
                str(sig),
                "--public-key-file",
                str(pub),
                "--expected-image",
                IMAGE,
                "--expected-scope",
                "production_candidate",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_real_approved_signature_and_artifact_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            result = verify(SOURCE, BUNDLE, Path(directory))
        self.assertEqual(result["signature"]["status"], "VERIFIED")
        self.assertEqual(result["artifact_bindings_verified"], 6)
        self.assertEqual(result["g23_verdict"], "NO-GO")

    def test_altered_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "bundle"
            shutil.copytree(BUNDLE, copy)
            manifest = copy / "release-manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify(SOURCE, copy, Path(directory) / "results")

    def test_substituted_signature_is_rejected_cryptographically(self):
        with tempfile.TemporaryDirectory() as directory:
            sig = Path(directory) / "substituted.sig"
            sig.write_text(
                base64.b64encode(bytes(64)).decode() + "\n", encoding="ascii"
            )
            result = self.canonical_verify(sig, BUNDLE / "official-release-signing.pub")
        self.assertEqual(result.returncode, 2)

    def test_wrong_public_key_is_rejected_cryptographically(self):
        with tempfile.TemporaryDirectory() as directory:
            pub = Path(directory) / "wrong.pub"
            der = bytes.fromhex("302a300506032b6570032100") + bytes([1]) * 32
            pub.write_text(
                "-----BEGIN PUBLIC KEY-----\n"
                + base64.b64encode(der).decode()
                + "\n-----END PUBLIC KEY-----\n",
                encoding="ascii",
            )
            result = self.canonical_verify(BUNDLE / "release-manifest.json.sig", pub)
        self.assertEqual(result.returncode, 2)

    def test_scan_for_different_digest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.loads((BUNDLE / "trivy-image.json").read_text(encoding="utf-8"))
            raw["ArtifactName"] = "ghcr.io/nobita6986/crm-cskh@sha256:" + "1" * 64
            scan = root / "cross-digest.json"
            scan.write_text(json.dumps(raw), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SOURCE / "deploy/release/evaluate_security_scan.py"),
                    "--trivy-json",
                    str(scan),
                    "--expected-image",
                    IMAGE,
                    "--exceptions",
                    str(BUNDLE / "cve-exceptions.json"),
                    "--pyjwt-metadata-file",
                    str(BUNDLE / "pyjwt-METADATA.txt"),
                    "--strict",
                    "--out",
                    str(root / "rejected.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
