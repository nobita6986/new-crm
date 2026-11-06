#!/usr/bin/env python3
"""Unit & security-boundary test suite for the P4-02 release pipeline (`deploy/release/*`)."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = RELEASE_DIR.parents[1]
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from evaluate_security_scan import (  # noqa: E402
    DEFAULT_EXCEPTIONS_FILE,
    evaluate_trivy_report,
)
from generate_sbom import generate_sboms  # noqa: E402
from release_manifest import (  # noqa: E402
    DEFAULT_LOCK_PATH,
    build_manifest_dict,
    ed25519_sign,
    ed25519_verify,
    generate_ed25519_keypair,
    main as manifest_main,
    read_kpi_formula_version,
    read_latest_migration_revision,
    validate_manifest_dict,
    validate_registry_digest,
)
from verify_digest_promotion import (  # noqa: E402
    REQUIRED_STAGING_APP_SERVICES,
    verify_promotion_chain,
)
from verify_pr_isolation import (  # noqa: E402
    audit_all_workflows,
    audit_workflow_text,
    check_locks_and_repo_hygiene,
    check_runtime_pr_zero_secrets,
)

SAMPLE_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "8f4e2b1c9d0a3e5f7b6c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a"
)
SAMPLE_DRIFT_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:"
    "8f4e2b1c9d0a3e5f7b6c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9b"
)
SAMPLE_IMAGE_ID = (
    "sha256:c56c19f9d87546ba5d52161cad92d937c1369d9fb0cd53f77c7193b166380df9"
)
VERIFIED_PYJWT_DOCTEST_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzb21lIjoicGF5bG9hZCJ9."
    "4twFt5NiznN84AWoo1d7KO1T_yoc0Z6XOpOVswacPZg"
)
DIFFERENT_JWT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiI5OTk5OTk5OTk5Iiwicm9sZSI6ImFkbWluIiwiaWF0IjoxNzAwMDAwMDAwfQ."
    "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)
PYJWT_METADATA_TARGET = "/home/frappe/frappe-bench/env/lib/python3.11/site-packages/pyjwt-2.13.0.dist-info/METADATA"


def _find_posix_sh() -> str:
    candidates = [
        shutil.which("sh"),
        "C:/Program Files/Git/bin/sh.exe",
        "C:/Program Files/Git/usr/bin/sh.exe",
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return cand
    raise RuntimeError("POSIX `sh` executable not found on test host")


def _build_pyjwt_metadata_text(
    *, line76_token: str = VERIFIED_PYJWT_DOCTEST_TOKEN, extra_line90_token: str = ""
) -> str:
    lines = [f"Metadata-Line-{idx}: placeholder" for idx in range(1, 95)]
    lines[75] = f"    '{line76_token}'"
    if extra_line90_token:
        lines[89] = f"    '{extra_line90_token}'"
    return "\n".join(lines) + "\n"


class TestWorkflowAndLockIsolation(unittest.TestCase):
    def test_repository_workflows_pass_isolation_audit(self) -> None:
        res = audit_all_workflows()
        self.assertTrue(res["passed"], f"Workflow isolation errors: {res['errors']}")
        for expected_wf in (
            "p0.yml",
            "p4-ci.yml",
            "p4-release.yml",
            "p4-production.yml",
        ):
            self.assertIn(expected_wf, res["checked_workflows"])

    def test_negative_workflow_security_violations_rejected(self) -> None:
        bad_prt = (
            "name: bad\non:\n  pull_request_target:\npermissions:\n  contents: read\n"
        )
        self.assertTrue(
            any(
                "pull_request_target" in e
                for e in audit_workflow_text(Path("p4-ci.yml"), bad_prt)
            )
        )

        bad_pr_write = (
            "name: bad\n"
            "on:\n  pull_request:\n"
            "permissions:\n  contents: read\n  packages: write\n"
            "jobs:\n  j:\n    environment: staging\n"
            "    env:\n      TOK: ${{ secrets.REGISTRY_PUSH_TOKEN }}\n"
            "      PULL_TOK: ${{ secrets.REGISTRY_PULL_TOKEN }}\n"
            "    steps:\n      - uses: actions/checkout@v4\n"
        )
        errs = audit_workflow_text(Path("p4-ci.yml"), bad_pr_write)
        self.assertTrue(any("write" in e for e in errs), errs)
        self.assertTrue(any("environment" in e for e in errs), errs)
        self.assertTrue(any("REGISTRY_PUSH_TOKEN" in e for e in errs), errs)
        self.assertTrue(any("REGISTRY_PULL_TOKEN" in e for e in errs), errs)
        self.assertTrue(any("unpinned" in e for e in errs), errs)

        bad_release = (
            "name: bad-rel\n"
            "on:\n  pull_request:\n"
            "permissions:\n  contents: read\n"
            "jobs:\n  b:\n    environment: production\n"
        )
        rel_errs = audit_workflow_text(Path("p4-release.yml"), bad_release)
        self.assertTrue(any("pull_request" in e for e in rel_errs), rel_errs)
        self.assertTrue(any("production" in e for e in rel_errs), rel_errs)
        self.assertTrue(any("registry-verify" in e for e in rel_errs), rel_errs)

        bad_prod = "name: bad-prod\non:\n  push:\n    branches: [main]\njobs: {}\n"
        prod_errs = audit_workflow_text(Path("p4-production.yml"), bad_prod)
        self.assertTrue(any("workflow_dispatch" in e for e in prod_errs), prod_errs)

    def test_runtime_pr_zero_secrets_boundary(self) -> None:
        clean = check_runtime_pr_zero_secrets(
            env={"GITHUB_EVENT_NAME": "pull_request", "PATH": "/usr/bin"}
        )
        self.assertTrue(clean["passed"])
        self.assertTrue(clean["enforced"])

        leaked = check_runtime_pr_zero_secrets(
            env={
                "GITHUB_EVENT_NAME": "pull_request",
                "RELEASE_SIGNING_PRIVATE_KEY": "secret-key",
                "REGISTRY_PULL_TOKEN": "pull-secret",
                "STAGING_SSH_PRIVATE_KEY": "ssh-key",
            }
        )
        self.assertFalse(leaked["passed"])
        self.assertEqual(
            leaked["leaked_secret_env_names"],
            [
                "REGISTRY_PULL_TOKEN",
                "RELEASE_SIGNING_PRIVATE_KEY",
                "STAGING_SSH_PRIVATE_KEY",
            ],
        )

    def test_locks_python_constraints_and_verify_harness_contracts(self) -> None:
        res = check_locks_and_repo_hygiene(REPO_ROOT)
        self.assertTrue(res["passed"], f"Lock hygiene errors: {res['errors']}")
        self.assertEqual(res["verified_evidence_checksums"], 10)
        self.assertEqual(res["bench_python_constraints_count"], 154)
        self.assertEqual(res["system_python_constraints_count"], 22)

        # REL-03: Containerfile enforces constraints across pip & uv steps
        cfile = (REPO_ROOT / "deploy" / "images" / "Containerfile").read_text(
            encoding="utf-8"
        )
        for marker in (
            "PIP_CONSTRAINT=/tmp/system-python-constraints.txt",
            "PIP_CONSTRAINT=/tmp/cskh-build/python-constraints.txt",
            "UV_CONSTRAINT=/tmp/cskh-build/python-constraints.txt",
            "UV_BUILD_CONSTRAINT=/tmp/cskh-build/python-constraints.txt",
            "--no-build-isolation -c /tmp/cskh-build/python-constraints.txt -e ./apps/crm_cskh",
        ):
            self.assertIn(marker, cfile)

        # REL-04: compose.verify.yaml uses apps-order.txt (not alphabetical `ls -1 apps`)
        verify_compose = (
            REPO_ROOT / "deploy" / "images" / "compose.verify.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("/home/frappe/frappe-bench/apps-order.txt", verify_compose)
        self.assertNotIn("ls -1 apps > sites/apps.txt", verify_compose)

        # REL-04: p4-release.yml uses --no-mariadb-socket and strict security gate
        rel_wf = (REPO_ROOT / ".github" / "workflows" / "p4-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("--no-mariadb-socket", rel_wf)
        self.assertIn("environment: registry-verify", rel_wf)
        self.assertIn("--strict", rel_wf)


class TestPosixDigestValidator(unittest.TestCase):
    """REL-02: Tests POSIX `validate_digest_uri.sh` and Python `validate_registry_digest`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sh_bin = _find_posix_sh()
        cls.validator_script = (RELEASE_DIR / "validate_digest_uri.sh").as_posix()

    def _run_posix_validator(self, candidate: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.sh_bin, self.validator_script, candidate],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_valid_crm_cskh_digest_uris_accepted(self) -> None:
        valid_uris = [
            SAMPLE_DIGEST,
            (
                "127.0.0.1:15000/cskh/crm-cskh@sha256:"
                "6cc10044b55be433d120660417f75d244eb984b269923d69d12465b537759a7a"
            ),
            (
                "registry.internal.example:5000/team/sub/crm-cskh:0.0.1-92e14af@sha256:"
                "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
            ),
        ]
        for uri in valid_uris:
            proc = self._run_posix_validator(uri)
            self.assertEqual(
                proc.returncode,
                0,
                f"POSIX validator unexpectedly rejected valid URI {uri!r}: {proc.stderr}",
            )
            validate_registry_digest(uri)

    def test_negative_whitespace_tags_and_bad_digests_rejected(self) -> None:
        hex64 = "8f4e2b1c9d0a3e5f7b6c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a"
        invalid_cases = [
            # Empty / blocked / placeholder
            "",
            "BLOCKED",
            f"<registry>/crm-cskh@sha256:{hex64}",
            # Whitespace: leading, trailing, middle, tab, embedded newline
            f" {SAMPLE_DIGEST}",
            f"{SAMPLE_DIGEST} ",
            f"ghcr.io/nobita6986/crm cskh@sha256:{hex64}",
            f"ghcr.io/nobita6986/crm-cskh\t@sha256:{hex64}",
            f"{SAMPLE_DIGEST}\ninjected-line",
            f"injected-line\n{SAMPLE_DIGEST}",
            # Floating tags / missing @sha256 digest / missing registry host
            "ghcr.io/nobita6986/crm-cskh:latest",
            "ghcr.io/nobita6986/crm-cskh:0.0.1-92e14af",
            "ghcr.io/nobita6986/crm-cskh",
            f"crm-cskh@sha256:{hex64}",
            # Malformed digests: short, long, uppercase, non-hex, wrong alg, all-zero
            f"ghcr.io/nobita6986/crm-cskh@sha256:{hex64[:63]}",
            f"ghcr.io/nobita6986/crm-cskh@sha256:{hex64}a",
            f"ghcr.io/nobita6986/crm-cskh@sha256:{hex64.upper()}",
            f"ghcr.io/nobita6986/crm-cskh@sha256:{'g' * 64}",
            f"ghcr.io/nobita6986/crm-cskh@sha512:{hex64}",
            f"ghcr.io/nobita6986/crm-cskh@sha256:{'0' * 64}",
        ]
        for bad in invalid_cases:
            proc = self._run_posix_validator(bad)
            self.assertNotEqual(
                proc.returncode,
                0,
                f"POSIX validator failed to reject invalid URI {bad!r}",
            )
            with self.assertRaises(
                ValueError, msg=f"Python validator failed to reject {bad!r}"
            ):
                validate_registry_digest(bad)


class TestSbomAndSecurityScanGate(unittest.TestCase):
    def test_deterministic_sbom_from_p4_01_locks(self) -> None:
        evidence_dir = REPO_ROOT / "deploy" / "images" / "evidence"
        with tempfile.TemporaryDirectory() as td:
            cdx = Path(td) / "sbom.cyclonedx.json"
            spdx = Path(td) / "sbom.spdx.json"
            summary = generate_sboms(
                metadata_dir=evidence_dir,
                image_ref=SAMPLE_DIGEST,
                cyclonedx_out=cdx,
                spdx_out=spdx,
            )
            self.assertEqual(summary["status"], "OK")
            counts = summary["component_counts"]
            self.assertEqual(counts["dpkg"], 442)
            self.assertEqual(counts["pip"], 154)
            self.assertEqual(counts["yarn_lockfiles"], 3)
            self.assertEqual(counts["apps"], 3)

            cdx_doc = json.loads(cdx.read_text(encoding="utf-8"))
            spdx_doc = json.loads(spdx.read_text(encoding="utf-8"))
            self.assertEqual(cdx_doc["bomFormat"], "CycloneDX")
            self.assertEqual(cdx_doc["specVersion"], "1.5")
            self.assertEqual(spdx_doc["spdxVersion"], "SPDX-2.3")

            with self.assertRaises(ValueError):
                generate_sboms(
                    metadata_dir=evidence_dir,
                    image_ref="crm-cskh:latest",
                    cyclonedx_out=cdx,
                    spdx_out=spdx,
                )

    def test_repo_cve_exceptions_has_zero_preapproved_cves(self) -> None:
        raw = json.loads(DEFAULT_EXCEPTIONS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(raw["exceptions"], [])
        self.assertEqual(len(raw["secret_false_positives"]), 1)
        fp = raw["secret_false_positives"][0]
        self.assertEqual(fp["exact_line"], 76)
        self.assertEqual(fp["exact_sample"], VERIFIED_PYJWT_DOCTEST_TOKEN)

    def test_rel01_exact_pyjwt_sample_and_different_jwt_in_same_file_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trivy_file = tmp / "trivy.json"
            meta_file = tmp / "METADATA"

            trivy_single_redacted_hit = {
                "ArtifactName": SAMPLE_DIGEST,
                "Results": [
                    {
                        "Target": PYJWT_METADATA_TARGET,
                        "Secrets": [
                            {
                                "RuleID": "jwt-token",
                                "Category": "JWT",
                                "Severity": "MEDIUM",
                                "Title": "JWT token",
                                "StartLine": 76,
                                "EndLine": 76,
                                "Match": "    '***'",
                            }
                        ],
                    }
                ],
            }
            trivy_file.write_text(
                json.dumps(trivy_single_redacted_hit), encoding="utf-8"
            )

            # 1. Exact verified doctest JWT on line 76 -> PASS
            meta_file.write_text(_build_pyjwt_metadata_text(), encoding="utf-8")
            ok = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="strict",
            )
            self.assertEqual(ok["status"], "PASS", ok)
            self.assertEqual(ok["secret_findings_count"], 0)
            self.assertEqual(ok["excepted_secret_false_positives_count"], 1)

            # 2. REL-01A REQUIRED TEST: A DIFFERENT JWT replacing line 76 in the EXACT SAME METADATA file MUST FAIL!
            meta_file.write_text(
                _build_pyjwt_metadata_text(line76_token=DIFFERENT_JWT_TOKEN),
                encoding="utf-8",
            )
            diff_line76 = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(diff_line76["status"], "FAIL", diff_line76)
            self.assertGreaterEqual(diff_line76["secret_findings_count"], 1)
            self.assertEqual(diff_line76["excepted_secret_false_positives_count"], 0)

            # 3. REL-01A REQUIRED TEST: Keeping verified JWT on line 76 AND adding a DIFFERENT JWT on line 90
            #    in the EXACT SAME METADATA file MUST FAIL (both when Trivy reports 2 hits and 1 hit)!
            meta_file.write_text(
                _build_pyjwt_metadata_text(
                    line76_token=VERIFIED_PYJWT_DOCTEST_TOKEN,
                    extra_line90_token=DIFFERENT_JWT_TOKEN,
                ),
                encoding="utf-8",
            )
            extra_same_file_single_trivy = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(extra_same_file_single_trivy["status"], "FAIL")
            self.assertGreaterEqual(
                extra_same_file_single_trivy["secret_findings_count"], 1
            )
            self.assertEqual(
                extra_same_file_single_trivy["excepted_secret_false_positives_count"], 0
            )

            trivy_two_hits_same_file = {
                "ArtifactName": SAMPLE_DIGEST,
                "Results": [
                    {
                        "Target": PYJWT_METADATA_TARGET,
                        "Secrets": [
                            {
                                "RuleID": "jwt-token",
                                "Category": "JWT",
                                "Severity": "MEDIUM",
                                "Title": "JWT token",
                                "StartLine": 76,
                                "EndLine": 76,
                                "Match": "    '***'",
                            },
                            {
                                "RuleID": "jwt-token",
                                "Category": "JWT",
                                "Severity": "MEDIUM",
                                "Title": "JWT token",
                                "StartLine": 90,
                                "EndLine": 90,
                                "Match": "    '***'",
                            },
                        ],
                    }
                ],
            }
            trivy_file.write_text(
                json.dumps(trivy_two_hits_same_file), encoding="utf-8"
            )
            extra_same_file_two_trivy = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(extra_same_file_two_trivy["status"], "FAIL")
            self.assertGreaterEqual(
                extra_same_file_two_trivy["secret_findings_count"], 2
            )
            self.assertEqual(
                extra_same_file_two_trivy["excepted_secret_false_positives_count"], 0
            )

            # 4. Redacted Trivy match WITHOUT --pyjwt-metadata-file MUST FAIL closed
            trivy_file.write_text(
                json.dumps(trivy_single_redacted_hit), encoding="utf-8"
            )
            no_meta = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=None,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(no_meta["status"], "FAIL")
            self.assertEqual(no_meta["secret_findings_count"], 1)

            # 5. Same verified token inside application code MUST FAIL closed
            trivy_app_secret = {
                "ArtifactName": SAMPLE_DIGEST,
                "Results": [
                    {
                        "Target": "/home/frappe/frappe-bench/apps/crm_cskh/crm_cskh/api.py",
                        "Secrets": [
                            {
                                "RuleID": "jwt-token",
                                "Category": "JWT",
                                "Severity": "MEDIUM",
                                "Title": "JWT token",
                                "StartLine": 76,
                                "EndLine": 76,
                                "Match": VERIFIED_PYJWT_DOCTEST_TOKEN,
                            }
                        ],
                    }
                ],
            }
            trivy_file.write_text(json.dumps(trivy_app_secret), encoding="utf-8")
            meta_file.write_text(_build_pyjwt_metadata_text(), encoding="utf-8")
            self.assertEqual(
                evaluate_trivy_report(
                    trivy_file,
                    pyjwt_metadata_path=meta_file,
                    mode="advisory_upstream_critical",
                )["status"],
                "FAIL",
            )

    def test_strict_cve_gate_and_regressed_package_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trivy_file = tmp / "trivy.json"
            exc_file = tmp / "exceptions.json"

            # Strict mode fails on BOTH fixable CRITICAL and unfixed OS CRITICAL without active exceptions
            trivy_file.write_text(
                json.dumps(
                    {
                        "ArtifactName": SAMPLE_DIGEST,
                        "Results": [
                            {
                                "Target": "debian",
                                "Vulnerabilities": [
                                    {
                                        "VulnerabilityID": "CVE-2026-99991",
                                        "PkgName": "PyJWT",
                                        "InstalledVersion": "2.13.0",
                                        "FixedVersion": "2.14.0",
                                        "Severity": "CRITICAL",
                                    },
                                    {
                                        "VulnerabilityID": "CVE-2025-7458",
                                        "PkgName": "libsqlite3-0",
                                        "InstalledVersion": "3.40.1-2+deb12u2",
                                        "FixedVersion": "",
                                        "Severity": "CRITICAL",
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            strict_fail = evaluate_trivy_report(
                trivy_file, exceptions_path=exc_file, mode="strict"
            )
            self.assertEqual(strict_fail["status"], "FAIL")
            self.assertEqual(len(strict_fail["gate_failures"]), 2)

            # Advisory mode allows pending upstream CRITICALs ONLY if not in forbid_critical_packages
            adv_ok = evaluate_trivy_report(
                trivy_file,
                exceptions_path=exc_file,
                mode="advisory_upstream_critical",
                forbid_critical_packages={"uv", "form-data"},
            )
            self.assertEqual(adv_ok["status"], "PASS")

            # Regressed patched package fails even in advisory mode
            adv_regressed = evaluate_trivy_report(
                trivy_file,
                exceptions_path=exc_file,
                mode="advisory_upstream_critical",
                forbid_critical_packages={"PyJWT"},
            )
            self.assertEqual(adv_regressed["status"], "FAIL")

            # Expired exception still fails strict mode
            exc_file.write_text(
                json.dumps(
                    {
                        "exceptions": [
                            {
                                "cve_id": "CVE-2026-99991",
                                "expires_on": "2020-01-01",
                                "approved_by": "T0",
                            },
                            {
                                "cve_id": "CVE-2025-7458",
                                "expires_on": "2099-12-31",
                                "approved_by": "T0",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                evaluate_trivy_report(
                    trivy_file, exceptions_path=exc_file, mode="strict"
                )["status"],
                "FAIL",
            )

            # Non-expired T0-approved exceptions for BOTH CVEs pass strict mode
            exc_file.write_text(
                json.dumps(
                    {
                        "exceptions": [
                            {
                                "cve_id": "CVE-2026-99991",
                                "expires_on": "2099-12-31",
                                "approved_by": "T0",
                            },
                            {
                                "cve_id": "CVE-2025-7458",
                                "expires_on": "2099-12-31",
                                "approved_by": "T0",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            strict_pass = evaluate_trivy_report(
                trivy_file, exceptions_path=exc_file, mode="strict"
            )
            self.assertEqual(strict_pass["status"], "PASS")
            self.assertEqual(len(strict_pass["excepted_critical"]), 2)


class TestReleaseManifestAndDigestPromotion(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.priv_pem, self.pub_pem = generate_ed25519_keypair()
        self.priv_file = self.tmp / "signing.key"
        self.pub_file = self.tmp / "signing.pub"
        self.priv_file.write_bytes(self.priv_pem)
        self.pub_file.write_bytes(self.pub_pem)

        evidence_dir = REPO_ROOT / "deploy" / "images" / "evidence"
        self.build_manifest_path = evidence_dir / "build-manifest.json"

        inspect_obj = json.loads(
            (evidence_dir / "image-inspect-summary.json").read_text(encoding="utf-8")
        )
        inspect_obj["registry_verification"] = {
            "status": "VERIFIED",
            "approved_registry_repo_digest": SAMPLE_DIGEST,
            "blocker": None,
        }
        self.inspect_path = self.tmp / "image-inspect-summary.json"
        self.inspect_path.write_text(
            json.dumps(inspect_obj, indent=2) + "\n", encoding="utf-8"
        )

        self.sbom_cdx = self.tmp / "sbom.cyclonedx.json"
        self.sbom_spdx = self.tmp / "sbom.spdx.json"
        generate_sboms(
            metadata_dir=evidence_dir,
            image_ref=SAMPLE_DIGEST,
            cyclonedx_out=self.sbom_cdx,
            spdx_out=self.sbom_spdx,
        )
        self.scan_summary = self.tmp / "security-scan-summary.json"
        self.scan_summary.write_text(
            json.dumps(
                {"status": "PASS", "gate_mode": "strict", "secret_findings_count": 0}
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_source_derived_constants(self) -> None:
        self.assertEqual(
            read_latest_migration_revision(),
            "crm_cskh.patches.v3_0.p3_schema_and_seeds",
        )
        self.assertEqual(read_kpi_formula_version(), "cskh-metrics-v2")

    def test_production_candidate_manifest_rejects_non_strict_scan(self) -> None:
        advisory_scan = self.tmp / "advisory-scan.json"
        advisory_scan.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "gate_mode": "advisory_upstream_critical",
                    "secret_findings_count": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "gate_mode='strict'"):
            build_manifest_dict(
                release_id="crm-cskh-20261106-001",
                environment_scope="production_candidate",
                lock_path=DEFAULT_LOCK_PATH,
                build_manifest_path=self.build_manifest_path,
                image_inspect_path=self.inspect_path,
                sbom_cyclonedx_path=self.sbom_cdx,
                sbom_spdx_path=self.sbom_spdx,
                security_scan_path=advisory_scan,
                build_url="https://example.invalid/1",
                test_report_url="https://example.invalid/1",
                previous_release_id=None,
                approved_by=[],
            )

    def test_manifest_sign_verify_tamper_and_scope_isolation(self) -> None:
        manifest_path = self.tmp / "release-manifest.json"
        sig_path = self.tmp / "release-manifest.json.sig"

        rc_gen = manifest_main(
            [
                "generate",
                "--release-id",
                "crm-cskh-20261106-001",
                "--environment-scope",
                "ci_ephemeral_rehearsal",
                "--build-manifest",
                str(self.build_manifest_path),
                "--image-inspect",
                str(self.inspect_path),
                "--sbom-cyclonedx",
                str(self.sbom_cdx),
                "--sbom-spdx",
                str(self.sbom_spdx),
                "--security-scan",
                str(self.scan_summary),
                "--build-url",
                "https://github.com/nobita6986/new-crm/actions/runs/1",
                "--test-report-url",
                "https://github.com/nobita6986/new-crm/actions/runs/1",
                "--out",
                str(manifest_path),
            ]
        )
        self.assertEqual(rc_gen, 0)

        rc_sign = manifest_main(
            [
                "sign",
                "--manifest",
                str(manifest_path),
                "--private-key-file",
                str(self.priv_file),
                "--sig-out",
                str(sig_path),
            ]
        )
        self.assertEqual(rc_sign, 0)

        rc_ok = manifest_main(
            [
                "verify",
                "--manifest",
                str(manifest_path),
                "--sig",
                str(sig_path),
                "--public-key-file",
                str(self.pub_file),
                "--expected-image",
                SAMPLE_DIGEST,
                "--expected-scope",
                "ci_ephemeral_rehearsal",
            ]
        )
        self.assertEqual(rc_ok, 0)

        rc_wrong_scope = manifest_main(
            [
                "verify",
                "--manifest",
                str(manifest_path),
                "--sig",
                str(sig_path),
                "--public-key-file",
                str(self.pub_file),
                "--expected-scope",
                "production_candidate",
            ]
        )
        self.assertNotEqual(rc_wrong_scope, 0)

        tampered = self.tmp / "tampered.json"
        tampered.write_bytes(manifest_path.read_bytes().replace(b"runs/1", b"runs/2"))
        rc_tamper = manifest_main(
            [
                "verify",
                "--manifest",
                str(tampered),
                "--sig",
                str(sig_path),
                "--public-key-file",
                str(self.pub_file),
            ]
        )
        self.assertNotEqual(rc_tamper, 0)

    def test_blocked_inspect_refused_for_manifest_generation(self) -> None:
        blocked_inspect = (
            REPO_ROOT / "deploy" / "images" / "evidence" / "image-inspect-summary.json"
        )
        with self.assertRaises(ValueError):
            build_manifest_dict(
                release_id="crm-cskh-blocked",
                environment_scope="production_candidate",
                lock_path=DEFAULT_LOCK_PATH,
                build_manifest_path=self.build_manifest_path,
                image_inspect_path=blocked_inspect,
                sbom_cyclonedx_path=self.sbom_cdx,
                sbom_spdx_path=self.sbom_spdx,
                security_scan_path=self.scan_summary,
                build_url="https://example.invalid/1",
                test_report_url="https://example.invalid/1",
                previous_release_id=None,
                approved_by=[],
            )

    def test_end_to_end_digest_promotion_and_drift_rejection(self) -> None:
        manifest_dict = build_manifest_dict(
            release_id="crm-cskh-20261106-001",
            environment_scope="production_candidate",
            lock_path=DEFAULT_LOCK_PATH,
            build_manifest_path=self.build_manifest_path,
            image_inspect_path=self.inspect_path,
            sbom_cyclonedx_path=self.sbom_cdx,
            sbom_spdx_path=self.sbom_spdx,
            security_scan_path=self.scan_summary,
            build_url="https://github.com/nobita6986/new-crm/actions/runs/99",
            test_report_url="https://github.com/nobita6986/new-crm/actions/runs/99",
            previous_release_id=None,
            approved_by=["T0"],
        )
        validate_manifest_dict(manifest_dict, expected_scope="production_candidate")

        manifest_path = self.tmp / "prod-manifest.json"
        sig_path = self.tmp / "prod-manifest.json.sig"
        raw_bytes = (json.dumps(manifest_dict, indent=2) + "\n").encode("utf-8")
        manifest_path.write_bytes(raw_bytes)
        raw_sig, _ = ed25519_sign(raw_bytes, self.priv_pem)
        ed25519_verify(raw_bytes, raw_sig, self.pub_pem)
        sig_path.write_bytes(
            (base64.b64encode(raw_sig).decode("ascii") + "\n").encode("ascii")
        )

        pulled_manifest_id = SAMPLE_DIGEST.split("@", 1)[1]

        clean_site_path = self.tmp / "clean-site-verify.json"
        clean_site_obj = {
            "app_image_tested": SAMPLE_DIGEST,
            "image_id": pulled_manifest_id,
            "summary": {
                "total_assertions": 45,
                "passed": 45,
                "failed": 0,
                "verdict": "PASS",
            },
        }
        clean_site_path.write_text(json.dumps(clean_site_obj), encoding="utf-8")

        smoke_path = self.tmp / "isolated-smoke-report.json"
        smoke_obj = {
            "status": "PASS",
            "project_name": "crm-cskh-p4-smoke-ci01",
            "image_provenance": {
                "app_image": SAMPLE_DIGEST,
                "image_id": SAMPLE_IMAGE_ID,
                "repo_digests": [SAMPLE_DIGEST],
            },
        }
        smoke_path.write_text(json.dumps(smoke_obj), encoding="utf-8")

        staging_inspect_path = self.tmp / "staging-containers.json"
        staging_containers = [
            {
                "Name": f"/cskh-staging-{svc}-1",
                "Image": SAMPLE_IMAGE_ID,
                "State": {"Status": "running"},
                "Config": {
                    "Image": SAMPLE_DIGEST,
                    "Labels": {"com.docker.compose.service": svc},
                },
                "Mounts": [
                    {
                        "Type": "volume",
                        "Destination": "/home/frappe/frappe-bench/sites",
                    }
                ],
            }
            for svc in REQUIRED_STAGING_APP_SERVICES
        ]
        staging_inspect_path.write_text(
            json.dumps(staging_containers), encoding="utf-8"
        )

        proof = verify_promotion_chain(
            manifest_path=manifest_path,
            sig_path=sig_path,
            public_key_pem=self.pub_pem,
            lock_path=DEFAULT_LOCK_PATH,
            build_inspect_path=self.inspect_path,
            clean_site_report_path=clean_site_path,
            compose_smoke_report_path=smoke_path,
            staging_containers_inspect_path=staging_inspect_path,
            expected_scope="production_candidate",
        )
        self.assertEqual(proof["status"], "PASS")
        self.assertTrue(proof["zero_digest_drift"])

        drifted = json.loads(staging_inspect_path.read_text(encoding="utf-8"))
        drifted[0]["Config"]["Image"] = SAMPLE_DRIFT_DIGEST
        staging_inspect_path.write_text(json.dumps(drifted), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "drift"):
            verify_promotion_chain(
                manifest_path=manifest_path,
                sig_path=sig_path,
                public_key_pem=self.pub_pem,
                lock_path=DEFAULT_LOCK_PATH,
                build_inspect_path=self.inspect_path,
                clean_site_report_path=clean_site_path,
                compose_smoke_report_path=smoke_path,
                staging_containers_inspect_path=staging_inspect_path,
                expected_scope="production_candidate",
            )

        bind_mounted = json.loads(json.dumps(staging_containers))
        bind_mounted[1]["Mounts"].append({"Type": "bind", "Destination": "/src"})
        staging_inspect_path.write_text(json.dumps(bind_mounted), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bind-mount"):
            verify_promotion_chain(
                manifest_path=manifest_path,
                sig_path=sig_path,
                public_key_pem=self.pub_pem,
                lock_path=DEFAULT_LOCK_PATH,
                build_inspect_path=self.inspect_path,
                clean_site_report_path=clean_site_path,
                compose_smoke_report_path=None,
                staging_containers_inspect_path=staging_inspect_path,
                expected_scope="production_candidate",
            )

        incomplete = [
            c
            for c in staging_containers
            if c["Config"]["Labels"]["com.docker.compose.service"] != "scheduler"
        ]
        staging_inspect_path.write_text(json.dumps(incomplete), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "scheduler"):
            verify_promotion_chain(
                manifest_path=manifest_path,
                sig_path=sig_path,
                public_key_pem=self.pub_pem,
                lock_path=DEFAULT_LOCK_PATH,
                build_inspect_path=self.inspect_path,
                clean_site_report_path=clean_site_path,
                compose_smoke_report_path=None,
                staging_containers_inspect_path=staging_inspect_path,
                expected_scope="production_candidate",
            )


if __name__ == "__main__":
    unittest.main()
