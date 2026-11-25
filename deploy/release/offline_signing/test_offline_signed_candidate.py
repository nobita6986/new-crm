"""Exercise the real approved signature and fail-closed negative cases."""

from datetime import datetime, timedelta, timezone
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_live_trivy_freshness import (
    EXPECTED_TRIVY_VERSION,
    TRIVY_IMAGE,
    evaluate_live_trivy_freshness,
)
from verify_offline_signed_candidate import IMAGE, IMAGE_ID, verify


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


class LiveTrivyFreshnessTests(unittest.TestCase):
    def _seed_fresh_scan_dir(
        self,
        out_dir: Path,
        *,
        eval_now: datetime,
        db_age_hours: float = 2.5,
        inject_critical: bool = False,
    ) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BUNDLE / "pyjwt-METADATA.txt", out_dir / "pyjwt-METADATA.txt")

        scan_dt = eval_now - timedelta(minutes=2)
        db_updated_dt = scan_dt - timedelta(hours=db_age_hours)
        db_next_dt = db_updated_dt + timedelta(hours=12)

        (out_dir / "trivy-version.json").write_text(
            json.dumps(
                {
                    "Version": EXPECTED_TRIVY_VERSION,
                    "PinnedTrivyImage": TRIVY_IMAGE,
                    "PulledTrivyImageID": "sha256:" + "a" * 64,
                }
            ),
            encoding="utf-8",
        )
        (out_dir / "trivy-db-metadata.json").write_text(
            json.dumps(
                {
                    "Version": 2,
                    "UpdatedAt": db_updated_dt.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"),
                    "NextUpdate": db_next_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "DownloadedAt": scan_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ),
            encoding="utf-8",
        )

        trivy_doc = json.loads(
            (BUNDLE / "trivy-image.json").read_text(encoding="utf-8")
        )
        trivy_doc["CreatedAt"] = scan_dt.strftime("%Y-%m-%dT%H:%M:%S.987654321Z")
        trivy_doc["ArtifactName"] = IMAGE
        trivy_doc["Metadata"]["ImageID"] = IMAGE_ID
        if inject_critical:
            trivy_doc["Results"][0].setdefault("Vulnerabilities", []).append(
                {
                    "VulnerabilityID": "CVE-2026-99999",
                    "PkgName": "openssl",
                    "InstalledVersion": "3.5.4-1",
                    "FixedVersion": "3.5.4-2",
                    "Severity": "CRITICAL",
                }
            )
        (out_dir / "trivy-image-fresh.json").write_text(
            json.dumps(trivy_doc), encoding="utf-8"
        )

    def test_fresh_db_within_24h_and_zero_critical_passes(self):
        eval_now = datetime(2026, 11, 25, 14, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "fresh-scan"
            self._seed_fresh_scan_dir(out_dir, eval_now=eval_now, db_age_hours=3.0)
            report = evaluate_live_trivy_freshness(
                SOURCE, out_dir, bundle_dir=BUNDLE, eval_now=eval_now
            )
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(report["vulnerability_db"]["db_age_within_24h"])
            self.assertTrue(report["sealed_offline_bundle_untouched"])
            self.assertTrue((out_dir / "SHA256SUMS.txt").is_file())

    def test_stale_db_over_24h_fails_closed(self):
        eval_now = datetime(2026, 11, 25, 14, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "stale-scan"
            self._seed_fresh_scan_dir(out_dir, eval_now=eval_now, db_age_hours=25.5)
            report = evaluate_live_trivy_freshness(
                SOURCE, out_dir, bundle_dir=BUNDLE, eval_now=eval_now
            )
            self.assertEqual(report["status"], "FAIL")
            self.assertFalse(report["vulnerability_db"]["db_age_within_24h"])
            self.assertTrue((out_dir / "SHA256SUMS.txt").is_file())

    def test_new_critical_cve_in_fresh_scan_fails_closed_and_reports_finding(self):
        eval_now = datetime(2026, 11, 25, 14, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "crit-scan"
            self._seed_fresh_scan_dir(
                out_dir, eval_now=eval_now, db_age_hours=1.5, inject_critical=True
            )
            report = evaluate_live_trivy_freshness(
                SOURCE, out_dir, bundle_dir=BUNDLE, eval_now=eval_now
            )
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(
                report["strict_evaluation"]["unapproved_fixable_critical"][0]["cve_id"],
                "CVE-2026-99999",
            )
            self.assertTrue((out_dir / "SHA256SUMS.txt").is_file())

    def test_output_dir_overlapping_sealed_bundle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly separate"):
            evaluate_live_trivy_freshness(SOURCE, BUNDLE, bundle_dir=BUNDLE)


if __name__ == "__main__":
    unittest.main()
