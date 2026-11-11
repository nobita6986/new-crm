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
from datetime import date
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = RELEASE_DIR.parents[1]
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from audit_registry_and_signing_config import (  # noqa: E402
    audit_registry_and_signing,
)
from benchmark_p4_isolated import (  # noqa: E402
    build_benchmark_specification,
    enforce_isolated_benchmark_target,
    summarize_latencies_ms,
)
from evaluate_security_scan import (  # noqa: E402
    DEFAULT_EXCEPTIONS_FILE,
    evaluate_trivy_report,
    main as scan_main,
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
from run_diff_impact_verification import (  # noqa: E402
    load_canonical_inventories,
    resolve_diff_impact_plan,
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
PYJWT_METADATA_TARGET = "/home/frappe/frappe-bench/env/lib/python3.11/site-packages/pyjwt-2.15.1.dist-info/METADATA"


def _find_posix_sh() -> str:
    candidates = [
        "C:/Program Files/Git/bin/sh.exe",
        "C:/Program Files/Git/usr/bin/sh.exe",
        shutil.which("sh"),
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return cand
    raise RuntimeError("POSIX `sh` executable not found on test host")


def _build_pyjwt_metadata_text(
    *, line77_token: str = VERIFIED_PYJWT_DOCTEST_TOKEN, extra_line90_token: str = ""
) -> str:
    lines = [f"Metadata-Line-{idx}: placeholder" for idx in range(1, 95)]
    lines[76] = f"    '{line77_token}'"
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
            "p4-candidate-verify.yml",
            "p4-release.yml",
            "p4-production.yml",
        ):
            self.assertIn(expected_wf, res["checked_workflows"])

    def test_negative_workflow_security_and_privilege_separation_rejected(self) -> None:
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
            "on:\n  workflow_dispatch:\n"
            "permissions:\n  contents: read\n"
            "jobs:\n"
            "  build-once-sign-and-push:\n"
            "    environment: registry-build\n"
            "    env:\n      SSH_LEAK: ${{ secrets.STAGING_SSH_PRIVATE_KEY }}\n"
            "    steps:\n      - run: release_manifest.py sign --manifest m.json\n"
            "  verify-by-digest:\n"
            "    environment: registry-verify\n"
            "    permissions:\n      packages: write\n"
            "    env:\n      SIGN_LEAK: ${{ secrets.RELEASE_SIGNING_PRIVATE_KEY }}\n"
            "    steps:\n      - run: docker build .\n"
            "  deploy-staging:\n"
            "    environment: staging\n"
            "    env:\n      PUSH_LEAK: ${{ secrets.REGISTRY_PUSH_TOKEN }}\n"
        )
        rel_errs = audit_workflow_text(Path("p4-release.yml"), bad_release)
        self.assertTrue(any("STAGING_SSH_PRIVATE_KEY" in e for e in rel_errs), rel_errs)
        self.assertTrue(any("--expected-image" in e for e in rel_errs), rel_errs)
        self.assertTrue(
            any("generate" in e and "--exceptions" in e for e in rel_errs), rel_errs
        )
        self.assertTrue(any("--security-scan" in e for e in rel_errs), rel_errs)
        self.assertTrue(
            any("sign" in e and "--exceptions" in e for e in rel_errs), rel_errs
        )
        self.assertTrue(
            any("verify-by-digest (pull-only)" in e for e in rel_errs), rel_errs
        )
        self.assertTrue(
            any("RELEASE_SIGNING_PRIVATE_KEY" in e for e in rel_errs), rel_errs
        )
        self.assertTrue(any("never build images" in e for e in rel_errs), rel_errs)
        self.assertTrue(any("REGISTRY_PUSH_TOKEN" in e for e in rel_errs), rel_errs)

        bad_prod = (
            "name: bad-prod\non:\n  push:\n    branches: [main]\n"
            "jobs:\n  d:\n    environment: production\n"
            "    env:\n      K: ${{ secrets.RELEASE_SIGNING_PRIVATE_KEY }}\n"
        )
        prod_errs = audit_workflow_text(Path("p4-production.yml"), bad_prod)
        self.assertTrue(any("workflow_dispatch" in e for e in prod_errs), prod_errs)
        self.assertTrue(
            any("RELEASE_SIGNING_PRIVATE_KEY" in e for e in prod_errs), prod_errs
        )

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

        lock = json.loads(DEFAULT_LOCK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(lock["security_patches"]["pyjwt_version"], "2.15.1")
        self.assertEqual(lock["security_patches"]["pillow_version"], "13.0.0")
        self.assertEqual(lock["base_image"]["tag"], "3.11.18-slim-trixie")
        self.assertEqual(lock["apps"]["frappe"]["tag"], "v15.126.0")
        self.assertEqual(lock["toolchain"]["frappe_bench_version"], "5.31.1")

        cfile = (REPO_ROOT / "deploy" / "images" / "Containerfile").read_text(
            encoding="utf-8"
        )
        for marker in (
            "PIP_CONSTRAINT=/tmp/system-python-constraints.txt",
            "PIP_CONSTRAINT=/tmp/cskh-build/python-constraints.txt",
            "UV_CONSTRAINT=/tmp/cskh-build/python-constraints.txt",
            "UV_BUILD_CONSTRAINT=/tmp/cskh-build/python-constraints.txt",
            "--no-build-isolation -c /tmp/cskh-build/python-constraints.txt -e ./apps/crm_cskh",
            "PyJWT==${PYJWT_PATCH_VERSION}",
            "pillow==${PILLOW_PATCH_VERSION}",
            'test -z "$(git -C apps/frappe diff pyproject.toml)"',
            "pip3 check",
            "./env/bin/pip check",
        ):
            self.assertIn(marker, cfile)

        verify_compose = (
            REPO_ROOT / "deploy" / "images" / "compose.verify.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("/home/frappe/frappe-bench/apps-order.txt", verify_compose)
        self.assertNotIn("ls -1 apps > sites/apps.txt", verify_compose)

        rel_wf = (REPO_ROOT / ".github" / "workflows" / "p4-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("environment: registry-verify", rel_wf)
        self.assertIn("--strict", rel_wf)
        self.assertIn("run_diff_impact_verification.py", rel_wf)


class TestRegistryConfigAuditor(unittest.TestCase):
    def test_reuses_ghcr_github_token_before_requesting_robot_secrets(self) -> None:
        priv_pem, pub_pem = generate_ed25519_keypair()
        pub_str = pub_pem.decode("utf-8")
        priv_str = priv_pem.decode("utf-8")

        # GHCR same-owner push role: GITHUB_TOKEN satisfies push auth; only signing key needed!
        push_audit = audit_registry_and_signing(
            env={
                "GITHUB_REPOSITORY": "nobita6986/new-crm",
                "CSKH_REGISTRY_IMAGE": "ghcr.io/nobita6986/crm-cskh",
                "RELEASE_SIGNING_PUBLIC_KEY": pub_str,
                "RELEASE_SIGNING_PRIVATE_KEY": priv_str,
                "GITHUB_TOKEN": "ghs_ephemeral_job_token",
            },
            role="push",
        )
        self.assertEqual(push_audit["status"], "READY", push_audit)
        self.assertEqual(push_audit["minimal_secrets_to_request"], [])
        self.assertEqual(
            push_audit["registry"]["effective_push_auth"],
            "github_token_packages_write",
        )

        # Pull role fails closed if signing private key or SSH key leaks into pull environment
        leaked_pull = audit_registry_and_signing(
            env={
                "GITHUB_REPOSITORY": "nobita6986/new-crm",
                "CSKH_REGISTRY_IMAGE": "ghcr.io/nobita6986/crm-cskh",
                "RELEASE_SIGNING_PUBLIC_KEY": pub_str,
                "GITHUB_TOKEN_READONLY": "ghs_read_token",
                "RELEASE_SIGNING_PRIVATE_KEY": priv_str,
            },
            role="pull",
        )
        self.assertEqual(leaked_pull["status"], "BLOCKED")
        self.assertTrue(leaked_pull["privilege_separation_violations"])

        # External private registry requests robot push/pull tokens when unset
        ext_push = audit_registry_and_signing(
            env={
                "GITHUB_REPOSITORY": "nobita6986/new-crm",
                "CSKH_REGISTRY_IMAGE": "registry.internal.example/cskh/crm-cskh",
                "RELEASE_SIGNING_PUBLIC_KEY": pub_str,
                "RELEASE_SIGNING_PRIVATE_KEY": priv_str,
                "GITHUB_TOKEN": "ghs_ephemeral",
            },
            role="push",
        )
        self.assertEqual(ext_push["status"], "BLOCKED")
        self.assertEqual(
            ext_push["minimal_secrets_to_request"],
            ["REGISTRY_PUSH_TOKEN", "REGISTRY_PUSH_USERNAME"],
        )


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
            "",
            "BLOCKED",
            f"<registry>/crm-cskh@sha256:{hex64}",
            f" {SAMPLE_DIGEST}",
            f"{SAMPLE_DIGEST} ",
            f"ghcr.io/nobita6986/crm cskh@sha256:{hex64}",
            f"ghcr.io/nobita6986/crm-cskh\t@sha256:{hex64}",
            f"{SAMPLE_DIGEST}\ninjected-line",
            f"injected-line\n{SAMPLE_DIGEST}",
            "ghcr.io/nobita6986/crm-cskh:latest",
            "ghcr.io/nobita6986/crm-cskh:0.0.1-92e14af",
            "ghcr.io/nobita6986/crm-cskh",
            f"crm-cskh@sha256:{hex64}",
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
        self.assertEqual(fp["exact_line"], 77)
        self.assertIn("2.15.1", fp["target_suffix"])
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
                                "StartLine": 77,
                                "EndLine": 77,
                                "Match": "    '***'",
                            }
                        ],
                    }
                ],
            }
            trivy_file.write_text(
                json.dumps(trivy_single_redacted_hit), encoding="utf-8"
            )

            # 1. Exact verified doctest JWT on line 77 -> PASS
            meta_file.write_text(_build_pyjwt_metadata_text(), encoding="utf-8")
            ok = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="strict",
            )
            self.assertEqual(ok["status"], "PASS", ok)
            self.assertEqual(ok["secret_findings_count"], 0)
            self.assertEqual(ok["excepted_secret_false_positives_count"], 1)

            # 2. A DIFFERENT JWT replacing line 77 in the EXACT SAME METADATA file MUST FAIL!
            meta_file.write_text(
                _build_pyjwt_metadata_text(line77_token=DIFFERENT_JWT_TOKEN),
                encoding="utf-8",
            )
            diff_line77 = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(diff_line77["status"], "FAIL", diff_line77)
            self.assertGreaterEqual(diff_line77["secret_findings_count"], 1)
            self.assertEqual(diff_line77["excepted_secret_false_positives_count"], 0)

            # 3. Adding a second JWT on line 90 in the SAME METADATA file MUST FAIL!
            meta_file.write_text(
                _build_pyjwt_metadata_text(
                    line77_token=VERIFIED_PYJWT_DOCTEST_TOKEN,
                    extra_line90_token=DIFFERENT_JWT_TOKEN,
                ),
                encoding="utf-8",
            )
            extra_same_file = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=meta_file,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(extra_same_file["status"], "FAIL")
            self.assertGreaterEqual(extra_same_file["secret_findings_count"], 1)

            # 4. Redacted Trivy match WITHOUT --pyjwt-metadata-file MUST FAIL closed
            no_meta = evaluate_trivy_report(
                trivy_file,
                pyjwt_metadata_path=None,
                mode="advisory_upstream_critical",
            )
            self.assertEqual(no_meta["status"], "FAIL")

    def test_strict_cve_gate_and_case_insensitive_regressed_package_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trivy_file = tmp / "trivy.json"
            exc_file = tmp / "exceptions.json"

            trivy_file.write_text(
                json.dumps(
                    {
                        "ArtifactName": SAMPLE_DIGEST,
                        "Results": [
                            {
                                "Target": "debian",
                                "Vulnerabilities": [
                                    {
                                        "VulnerabilityID": "CVE-2026-102268",
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

            # Case-insensitive forbid_critical_packages (`pyjwt` catches `PyJWT`)
            adv_regressed = evaluate_trivy_report(
                trivy_file,
                exceptions_path=exc_file,
                mode="advisory_upstream_critical",
                forbid_critical_packages={"pyjwt", "pillow"},
            )
            self.assertEqual(adv_regressed["status"], "FAIL")

    def test_g04_scoped_cve_exceptions_reject_wrong_digest_cve_package_version_and_expiry(
        self,
    ) -> None:
        ref_day = date(2026, 11, 11)
        base_exc_doc = json.loads(DEFAULT_EXCEPTIONS_FILE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trivy_file = tmp / "trivy-g04.json"
            clean_trivy_file = tmp / "trivy-clean.json"
            exc_file = tmp / "cve-exceptions-sim.json"

            trivy_file.write_text(
                json.dumps(
                    {
                        "ArtifactName": SAMPLE_DIGEST,
                        "ArtifactType": "container_image",
                        "Results": [
                            {
                                "Target": "debian 13.7",
                                "Vulnerabilities": [
                                    {
                                        "VulnerabilityID": "CVE-2026-44172",
                                        "PkgName": "libmariadb3",
                                        "InstalledVersion": "1:11.8.6-0+deb13u1",
                                        "FixedVersion": "",
                                        "Severity": "CRITICAL",
                                    },
                                    {
                                        "VulnerabilityID": "CVE-2026-44172",
                                        "PkgName": "mariadb-client",
                                        "InstalledVersion": "1:11.8.6-0+deb13u1",
                                        "FixedVersion": "",
                                        "Severity": "CRITICAL",
                                    },
                                    {
                                        "VulnerabilityID": "CVE-2026-6653",
                                        "PkgName": "libxml2",
                                        "InstalledVersion": "2.12.7+dfsg+really2.9.14-2.1+deb13u3",
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
            clean_trivy_file.write_text(
                json.dumps({"ArtifactName": SAMPLE_DIGEST, "Results": []}),
                encoding="utf-8",
            )

            def _valid_exceptions() -> list[dict[str, object]]:
                return [
                    {
                        "id": "EXC-SIM-001",
                        "cve_id": "CVE-2026-44172",
                        "image_digest": SAMPLE_DIGEST,
                        "packages": ["libmariadb3", "mariadb-client"],
                        "installed_version": "1:11.8.6-0+deb13u1",
                        "expires_on": "2026-12-11",
                        "approved_by": "Owner-T0-Signed-Ref-001",
                        "reason": "Simulated Owner-signed scoped exception",
                    },
                    {
                        "id": "EXC-SIM-002",
                        "cve_id": "CVE-2026-6653",
                        "image_digest": SAMPLE_DIGEST,
                        "package": "libxml2",
                        "installed_version": "2.12.7+dfsg+really2.9.14-2.1+deb13u3",
                        "expires_on": "2026-11-11",
                        "approved_by": "Owner-T0-Signed-Ref-001",
                        "reason": "Valid inclusive on exact expiry date",
                    },
                ]

            def _eval(
                exceptions: list[dict[str, object]],
                *,
                scan_path: Path = trivy_file,
                mode: str = "strict",
                expected_img: str | None = SAMPLE_DIGEST,
            ) -> dict[str, object]:
                doc = {**base_exc_doc, "exceptions": exceptions}
                exc_file.write_text(json.dumps(doc), encoding="utf-8")
                return evaluate_trivy_report(
                    scan_path,
                    exceptions_path=exc_file,
                    mode=mode,
                    expected_image_digest=expected_img,
                    reference_date=ref_day,
                )

            # 1. Exact 5-tuple match -> PASS
            ok = _eval(_valid_exceptions())
            self.assertEqual(ok["status"], "PASS", ok)
            self.assertEqual(ok["rejected_exceptions_count"], 0)
            self.assertEqual(
                ok["unique_critical_counts"],
                {
                    "fixable_unapproved": 0,
                    "unfixed_upstream_os_unapproved": 0,
                    "excepted": 3,
                },
            )

            # 2. Wrong / missing / tag-only image_digest -> FAIL
            for bad_digest in (
                SAMPLE_DRIFT_DIGEST,
                "",
                "ghcr.io/nobita6986/crm-cskh:latest",
                "*",
            ):
                mut = _valid_exceptions()
                mut[0]["image_digest"] = bad_digest
                res = _eval(mut)
                self.assertEqual(res["status"], "FAIL", (bad_digest, res))
                self.assertGreaterEqual(int(res["rejected_exceptions_count"]), 1)

            # Also fails if --expected-image differs from scanned ArtifactName
            mismatch_target = _eval(
                _valid_exceptions(), expected_img=SAMPLE_DRIFT_DIGEST
            )
            self.assertEqual(mismatch_target["status"], "FAIL", mismatch_target)

            # 3. Wrong CVE ID or wildcard CVE -> FAIL (even on a 0-CVE scan!)
            for bad_cve in ("CVE-2026-99999", "CVE-*", "cve-2026-44172", ""):
                mut = _valid_exceptions()
                mut[1]["cve_id"] = bad_cve
                res = _eval(mut)
                self.assertEqual(res["status"], "FAIL", (bad_cve, res))

            unmatched_on_clean = _eval(_valid_exceptions(), scan_path=clean_trivy_file)
            self.assertEqual(unmatched_on_clean["status"], "FAIL", unmatched_on_clean)

            # 4. Wrong, partial, over-broad, case-mismatched, or wildcard package(s) -> FAIL
            partial_pkg = _valid_exceptions()
            partial_pkg[0]["packages"] = ["libmariadb3"]  # omits mariadb-client
            self.assertEqual(_eval(partial_pkg)["status"], "FAIL")

            overbroad_pkg = _valid_exceptions()
            overbroad_pkg[0]["packages"] = [
                "libmariadb3",
                "mariadb-client",
                "openssl",
            ]
            self.assertEqual(_eval(overbroad_pkg)["status"], "FAIL")

            for bad_pkg in ("openssl", "LibXML2", "*", ""):
                mut = _valid_exceptions()
                mut[1]["package"] = bad_pkg
                self.assertEqual(_eval(mut)["status"], "FAIL", bad_pkg)

            # 5. Wrong, range, or wildcard installed_version -> FAIL
            for bad_ver in (
                "1:11.8.5-0+deb13u1",
                ">=1:11.8.6-0+deb13u1",
                "*",
                "",
            ):
                mut = _valid_exceptions()
                mut[0]["installed_version"] = bad_ver
                self.assertEqual(_eval(mut)["status"], "FAIL", bad_ver)

            # 6. Expired expires_on, invalid date, placeholder approver, or T1B self-approval -> FAIL in ALL modes
            for field_name, bad_val in (
                ("expires_on", "2026-11-10"),
                ("expires_on", "2026/12/11"),
                ("expires_on", ""),
                ("approved_by", ""),
                ("approved_by", "<T0_APPROVER_HANDLE>"),
                ("approved_by", "TBD"),
                ("approved_by", "T1B"),
                ("approved_by", "T1B-P4-02"),
            ):
                mut = _valid_exceptions()
                mut[0][field_name] = bad_val
                for eval_mode in ("strict", "advisory_upstream_critical"):
                    res = _eval(mut, mode=eval_mode)
                    self.assertEqual(
                        res["status"], "FAIL", (field_name, bad_val, eval_mode, res)
                    )


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
        lock_obj = json.loads(DEFAULT_LOCK_PATH.read_text(encoding="utf-8"))
        build_manifest_obj = json.loads(
            (evidence_dir / "build-manifest.json").read_text(encoding="utf-8")
        )
        build_manifest_obj["apps"]["frappe"]["tag"] = lock_obj["apps"]["frappe"]["tag"]
        build_manifest_obj["apps"]["frappe"]["commit_sha"] = lock_obj["apps"]["frappe"][
            "commit_sha"
        ]
        build_manifest_obj["apps"]["crm"]["tag"] = lock_obj["apps"]["crm"]["tag"]
        build_manifest_obj["apps"]["crm"]["commit_sha"] = lock_obj["apps"]["crm"][
            "commit_sha"
        ]
        build_manifest_obj["apps"]["crm_cskh"]["baseline_main_sha"] = lock_obj[
            "baseline_main_sha"
        ]
        build_manifest_obj["apps"]["crm_cskh"]["apps_tree_sha"] = lock_obj[
            "apps_tree_sha"
        ]
        build_manifest_obj["apps"]["crm_cskh"]["crm_cskh_tree_sha"] = lock_obj[
            "crm_cskh_tree_sha"
        ]
        self.build_manifest_path = self.tmp / "build-manifest.json"
        self.build_manifest_path.write_text(
            json.dumps(build_manifest_obj, indent=2) + "\n", encoding="utf-8"
        )

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
        self.scan_summary = self.tmp / "security-scan-strict.json"
        self.scan_summary.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "gate_mode": "strict",
                    "artifact_name": SAMPLE_DIGEST,
                    "secret_findings_count": 0,
                    "unique_critical_counts": {
                        "fixable_unapproved": 0,
                        "unfixed_upstream_os_unapproved": 0,
                        "excepted": 0,
                    },
                    "gate_failures": [],
                }
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

    def test_production_candidate_rejects_non_strict_or_cross_digest_scan_on_generate_and_sign(
        self,
    ) -> None:
        advisory_scan = self.tmp / "advisory-scan.json"
        advisory_scan.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "gate_mode": "advisory_upstream_critical",
                    "artifact_name": SAMPLE_DIGEST,
                    "secret_findings_count": 0,
                    "gate_failures": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "gate_mode='strict'"):
            build_manifest_dict(
                release_id="crm-cskh-20261109-001",
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

        # Cross-digest scan (e.g. CI loopback image scan reused for live/release digest) MUST fail!
        cross_digest_scan = self.tmp / "cross-digest-scan.json"
        cross_digest_scan.write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "gate_mode": "strict",
                    "artifact_name": SAMPLE_DRIFT_DIGEST,
                    "secret_findings_count": 0,
                    "gate_failures": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            build_manifest_dict(
                release_id="crm-cskh-20261109-001",
                environment_scope="production_candidate",
                lock_path=DEFAULT_LOCK_PATH,
                build_manifest_path=self.build_manifest_path,
                image_inspect_path=self.inspect_path,
                sbom_cyclonedx_path=self.sbom_cdx,
                sbom_spdx_path=self.sbom_spdx,
                security_scan_path=cross_digest_scan,
                build_url="https://example.invalid/1",
                test_report_url="https://example.invalid/1",
                previous_release_id=None,
                approved_by=[],
            )

        # Signing a production_candidate manifest WITHOUT --security-scan MUST fail closed!
        prod_manifest = self.tmp / "prod-candidate.json"
        sig_out = self.tmp / "prod-candidate.sig"
        self.assertEqual(
            manifest_main(
                [
                    "generate",
                    "--release-id",
                    "crm-cskh-20261109-001",
                    "--environment-scope",
                    "production_candidate",
                    "--build-manifest",
                    str(self.build_manifest_path),
                    "--image-inspect",
                    str(self.inspect_path),
                    "--sbom-cyclonedx",
                    str(self.sbom_cdx),
                    "--security-scan",
                    str(self.scan_summary),
                    "--build-url",
                    "https://example.invalid/1",
                    "--test-report-url",
                    "https://example.invalid/1",
                    "--out",
                    str(prod_manifest),
                ]
            ),
            0,
        )
        self.assertNotEqual(
            manifest_main(
                [
                    "sign",
                    "--manifest",
                    str(prod_manifest),
                    "--private-key-file",
                    str(self.priv_file),
                    "--sig-out",
                    str(sig_out),
                ]
            ),
            0,
        )
        self.assertFalse(sig_out.exists())

        # Signing WITH matching strict PASS --security-scan succeeds!
        self.assertEqual(
            manifest_main(
                [
                    "sign",
                    "--manifest",
                    str(prod_manifest),
                    "--security-scan",
                    str(self.scan_summary),
                    "--private-key-file",
                    str(self.priv_file),
                    "--sig-out",
                    str(sig_out),
                ]
            ),
            0,
        )
        self.assertTrue(sig_out.is_file())

    def test_g04_manifest_generate_and_sign_enforce_5tuple_exception_scope_and_expiry(
        self,
    ) -> None:
        base_exc_doc = json.loads(DEFAULT_EXCEPTIONS_FILE.read_text(encoding="utf-8"))
        trivy_path = self.tmp / "trivy-candidate.json"
        strict_scan_path = self.tmp / "security-scan-strict-excepted.json"
        valid_exc_path = self.tmp / "cve-exceptions-owner.json"

        trivy_path.write_text(
            json.dumps(
                {
                    "ArtifactName": SAMPLE_DIGEST,
                    "ArtifactType": "container_image",
                    "Results": [
                        {
                            "Target": "debian 13.7",
                            "Vulnerabilities": [
                                {
                                    "VulnerabilityID": "CVE-2026-44172",
                                    "PkgName": "libmariadb3",
                                    "InstalledVersion": "1:11.8.6-0+deb13u1",
                                    "FixedVersion": "",
                                    "Severity": "CRITICAL",
                                },
                                {
                                    "VulnerabilityID": "CVE-2026-44172",
                                    "PkgName": "mariadb-client",
                                    "InstalledVersion": "1:11.8.6-0+deb13u1",
                                    "FixedVersion": "",
                                    "Severity": "CRITICAL",
                                },
                                {
                                    "VulnerabilityID": "CVE-2026-6653",
                                    "PkgName": "libxml2",
                                    "InstalledVersion": "2.12.7+dfsg+really2.9.14-2.1+deb13u3",
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

        def _make_valid_exc() -> dict[str, object]:
            return {
                **base_exc_doc,
                "exceptions": [
                    {
                        "id": "EXC-REL-001",
                        "cve_id": "CVE-2026-44172",
                        "image_digest": SAMPLE_DIGEST,
                        "packages": ["libmariadb3", "mariadb-client"],
                        "installed_version": "1:11.8.6-0+deb13u1",
                        "expires_on": "2026-11-20",
                        "approved_by": "Owner-T0-Decision-20261111",
                        "reason": "Scoped exception on SAMPLE_DIGEST",
                    },
                    {
                        "id": "EXC-REL-002",
                        "cve_id": "CVE-2026-6653",
                        "image_digest": SAMPLE_DIGEST,
                        "package": "libxml2",
                        "installed_version": "2.12.7+dfsg+really2.9.14-2.1+deb13u3",
                        "expires_on": "2026-11-20",
                        "approved_by": "Owner-T0-Decision-20261111",
                        "reason": "Scoped exception on SAMPLE_DIGEST",
                    },
                ],
            }

        valid_exc_path.write_text(
            json.dumps(_make_valid_exc(), indent=2) + "\n", encoding="utf-8"
        )

        # 1. Strict scan CLI succeeds with exact 5-tuple scope -> generate -> sign -> verify succeed
        self.assertEqual(
            scan_main(
                [
                    "--strict",
                    "--expected-image",
                    SAMPLE_DIGEST,
                    "--exceptions",
                    str(valid_exc_path),
                    "--reference-date",
                    "2026-11-11",
                    "--trivy-json",
                    str(trivy_path),
                    "--out",
                    str(strict_scan_path),
                ]
            ),
            0,
        )

        valid_manifest = self.tmp / "prod-scoped-manifest.json"
        valid_sig = self.tmp / "prod-scoped-manifest.json.sig"

        def _run_generate(
            exc_path: Path, out_path: Path, *, ref_date: str = "2026-11-11"
        ) -> int:
            return manifest_main(
                [
                    "generate",
                    "--release-id",
                    "crm-cskh-20261111-g04",
                    "--environment-scope",
                    "production_candidate",
                    "--build-manifest",
                    str(self.build_manifest_path),
                    "--image-inspect",
                    str(self.inspect_path),
                    "--sbom-cyclonedx",
                    str(self.sbom_cdx),
                    "--sbom-spdx",
                    str(self.sbom_spdx),
                    "--security-scan",
                    str(strict_scan_path),
                    "--exceptions",
                    str(exc_path),
                    "--reference-date",
                    ref_date,
                    "--build-url",
                    "https://example.invalid/run/1",
                    "--test-report-url",
                    "https://example.invalid/run/1",
                    "--out",
                    str(out_path),
                ]
            )

        def _run_sign(
            manifest_file: Path,
            exc_path: Path,
            sig_file: Path,
            *,
            ref_date: str = "2026-11-11",
        ) -> int:
            return manifest_main(
                [
                    "sign",
                    "--manifest",
                    str(manifest_file),
                    "--security-scan",
                    str(strict_scan_path),
                    "--exceptions",
                    str(exc_path),
                    "--reference-date",
                    ref_date,
                    "--private-key-file",
                    str(self.priv_file),
                    "--sig-out",
                    str(sig_file),
                ]
            )

        self.assertEqual(_run_generate(valid_exc_path, valid_manifest), 0)
        manifest_json = json.loads(valid_manifest.read_text(encoding="utf-8"))
        self.assertIn("cve_exceptions_sha256", manifest_json["artifacts"])

        self.assertEqual(_run_sign(valid_manifest, valid_exc_path, valid_sig), 0)
        self.assertEqual(
            manifest_main(
                [
                    "verify",
                    "--manifest",
                    str(valid_manifest),
                    "--sig",
                    str(valid_sig),
                    "--public-key-file",
                    str(self.pub_file),
                    "--expected-image",
                    SAMPLE_DIGEST,
                    "--expected-scope",
                    "production_candidate",
                ]
            ),
            0,
        )

        # 2. Empty repo `cve-exceptions.json` (`exceptions: []`) MUST reject BOTH generate & sign
        blocked_empty = self.tmp / "blocked-empty.json"
        blocked_empty_sig = self.tmp / "blocked-empty.sig"
        self.assertNotEqual(_run_generate(DEFAULT_EXCEPTIONS_FILE, blocked_empty), 0)
        self.assertFalse(blocked_empty.exists())
        self.assertNotEqual(
            _run_sign(valid_manifest, DEFAULT_EXCEPTIONS_FILE, blocked_empty_sig), 0
        )
        self.assertFalse(blocked_empty_sig.exists())

        # 3. Expired after scan time (2026-11-21 > expires_on 2026-11-20) MUST reject BOTH generate & sign!
        blocked_exp = self.tmp / "blocked-expired.json"
        blocked_exp_sig = self.tmp / "blocked-expired.sig"
        self.assertNotEqual(
            _run_generate(valid_exc_path, blocked_exp, ref_date="2026-11-21"), 0
        )
        self.assertFalse(blocked_exp.exists())
        self.assertNotEqual(
            _run_sign(
                valid_manifest, valid_exc_path, blocked_exp_sig, ref_date="2026-11-21"
            ),
            0,
        )
        self.assertFalse(blocked_exp_sig.exists())

        # 4. Out-of-scope mutations (wrong digest, wrong CVE, partial/extra package, wrong version, T1B self-approval)
        mutations: list[tuple[str, object]] = []

        m_digest = _make_valid_exc()
        m_digest["exceptions"][0]["image_digest"] = SAMPLE_DRIFT_DIGEST  # type: ignore[index]
        mutations.append(("wrong_digest", m_digest))

        m_cve = _make_valid_exc()
        m_cve["exceptions"][1]["cve_id"] = "CVE-2026-99999"  # type: ignore[index]
        mutations.append(("wrong_cve", m_cve))

        m_partial_pkg = _make_valid_exc()
        m_partial_pkg["exceptions"][0]["packages"] = ["libmariadb3"]  # type: ignore[index]
        mutations.append(("partial_package", m_partial_pkg))

        m_extra_pkg = _make_valid_exc()
        m_extra_pkg["exceptions"][1]["packages"] = ["libxml2", "openssl"]  # type: ignore[index]
        del m_extra_pkg["exceptions"][1]["package"]  # type: ignore[index]
        mutations.append(("extra_unmatched_package", m_extra_pkg))

        m_ver = _make_valid_exc()
        m_ver["exceptions"][1]["installed_version"] = (
            "2.12.7+dfsg+really2.9.14-2.1+deb13u2"  # type: ignore[index]
        )
        mutations.append(("wrong_version", m_ver))

        m_self = _make_valid_exc()
        m_self["exceptions"][0]["approved_by"] = "T1B"  # type: ignore[index]
        mutations.append(("t1b_self_approved", m_self))

        for label, bad_doc in mutations:
            bad_exc_file = self.tmp / f"bad-{label}.json"
            bad_manifest_out = self.tmp / f"blocked-{label}.manifest.json"
            bad_sig_out = self.tmp / f"blocked-{label}.sig"
            bad_exc_file.write_text(json.dumps(bad_doc), encoding="utf-8")

            self.assertNotEqual(
                _run_generate(bad_exc_file, bad_manifest_out),
                0,
                f"generate failed to reject {label}",
            )
            self.assertFalse(bad_manifest_out.exists(), label)

            # Even if manifest without cve_exceptions_sha256 is forged, sign MUST still reject on scope check!
            forged_manifest = self.tmp / f"forged-{label}.manifest.json"
            forged_obj = json.loads(valid_manifest.read_text(encoding="utf-8"))
            del forged_obj["artifacts"]["cve_exceptions_sha256"]
            forged_manifest.write_text(json.dumps(forged_obj), encoding="utf-8")
            self.assertNotEqual(
                _run_sign(forged_manifest, bad_exc_file, bad_sig_out),
                0,
                f"sign failed to reject {label}",
            )
            self.assertFalse(bad_sig_out.exists(), label)

        # 5. Tampering with cve-exceptions.json between generate and sign (even benign text) fails SHA-256 lock
        tampered_exc = self.tmp / "tampered-after-generate.json"
        tampered_doc = _make_valid_exc()
        tampered_doc["exceptions"][0]["reason"] = "Modified after generate"  # type: ignore[index]
        tampered_exc.write_text(json.dumps(tampered_doc), encoding="utf-8")
        tampered_sig = self.tmp / "tampered.sig"
        self.assertNotEqual(_run_sign(valid_manifest, tampered_exc, tampered_sig), 0)
        self.assertFalse(tampered_sig.exists())

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
                "--security-scan",
                str(self.scan_summary),
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
                "total_assertions": 55,
                "passed": 55,
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
        self.assertEqual(proof["canonical_registry_digest"], SAMPLE_DIGEST)
        self.assertEqual(proof["canonical_image_id"], SAMPLE_IMAGE_ID)
        self.assertNotEqual(
            proof["canonical_image_id"], proof["canonical_registry_digest"]
        )
        tax = proof["identifier_taxonomy"]
        assert isinstance(tax, dict)
        self.assertEqual(
            tax["canonical_registry_digest_class"], "oci_distribution_manifest_sha256"
        )
        self.assertEqual(tax["canonical_image_id_class"], "oci_image_config_sha256")


class TestDiffImpactAndIsolatedBenchmark(unittest.TestCase):
    def test_canonical_inventories_exist_and_diff_impact_maps_correctly(self) -> None:
        invs = load_canonical_inventories(REPO_ROOT)
        self.assertEqual(invs["g007"]["expected_total"], 99)
        self.assertEqual(invs["p1"]["expected_total"], 209)
        self.assertEqual(invs["p2"]["expected_total"], 38)
        self.assertGreaterEqual(int(invs["p3"]["expected_total"]), 31)  # type: ignore[arg-type]
        self.assertEqual(invs["p3_ui"]["expected_total"], 7)

        # Containerfile change escalates to full P0-P3 suite (29 modules)
        full_plan = resolve_diff_impact_plan(
            ["deploy/images/Containerfile"], repo_root=REPO_ROOT
        )
        self.assertTrue(full_plan["full_suite_escalated"])
        self.assertEqual(
            full_plan["selected_suites"], ["g007", "p1", "p2", "p3", "p3_ui"]
        )
        self.assertEqual(full_plan["total_modules_to_run"], 31)

        # Scoped P3 KPI edit runs g007 + p3 + p3_ui without unneeded P1/P2 modules
        p3_plan = resolve_diff_impact_plan(
            ["apps/crm_cskh/crm_cskh/services/kpi_calculator.py"],
            repo_root=REPO_ROOT,
        )
        self.assertFalse(p3_plan["full_suite_escalated"])
        self.assertEqual(p3_plan["selected_suites"], ["g007", "p3", "p3_ui"])

    def test_isolated_benchmark_blocks_owner_vps_and_commits_zero_unmeasured_sla(
        self,
    ) -> None:
        # Missing --confirm-isolated-target -> rejected
        with self.assertRaisesRegex(ValueError, "--confirm-isolated-target"):
            enforce_isolated_benchmark_target(
                base_url="http://127.0.0.1:18080",
                site_name="p4-bench.localhost",
                compose_project="cskh-p4-bench-01",
                confirm_isolated_target=False,
            )

        # Public / Owner / Production URLs -> rejected even with --confirm-isolated-target
        for forbidden_url in (
            "https://crm.hrpartner.vn",
            "https://prod-crm.example.com",
            "http://203.0.113.10:8080",
        ):
            with self.assertRaises(ValueError):
                enforce_isolated_benchmark_target(
                    base_url=forbidden_url,
                    site_name="p4-bench.localhost",
                    compose_project="cskh-p4-bench-01",
                    confirm_isolated_target=True,
                )

        # Loopback isolated target -> accepted, S1 suggested dataset framed as non-committed
        s1_spec = build_benchmark_specification(
            profile_name="s1_suggested_mvp",
            base_url="http://127.0.0.1:18080",
            site_name="p4-bench.localhost",
            compose_project="cskh-p4-bench-local",
            confirm_isolated_target=True,
        )
        self.assertEqual(
            s1_spec["sla_commitment_status"],
            "UNCOMMITTED_PENDING_MEASURED_RUN_AND_OWNER_APPROVAL",
        )
        self.assertIn("NOT a commitment of 50,000", str(s1_spec["dataset_disclaimer"]))
        s1_ds = s1_spec["published_dataset_and_load"]
        assert isinstance(s1_ds, dict)
        self.assertEqual(s1_ds["leads"], 50000)
        self.assertEqual(s1_ds["work_items"], 75000)
        self.assertEqual(s1_ds["contact_attempts"], 250000)
        self.assertEqual(s1_ds["active_staff"], 40)
        self.assertEqual(
            s1_ds["dataset_framing"],
            "SUGGESTED_S1_DATASET_NOT_MONTHLY_VOLUME_COMMITMENT",
        )
        self.assertFalse(s1_spec["isolation_guard"]["owner_vps_touched"])  # type: ignore[index]

        # Legacy profile alias also supported cleanly
        spec = build_benchmark_specification(
            profile_name="isolated_p4_target_50k",
            base_url="http://127.0.0.1:18080",
            site_name="p4-bench.localhost",
            compose_project="cskh-p4-bench-local",
            confirm_isolated_target=True,
        )
        self.assertEqual(
            spec["sla_commitment_status"],
            "UNCOMMITTED_PENDING_MEASURED_RUN_AND_OWNER_APPROVAL",
        )
        self.assertFalse(spec["isolation_guard"]["owner_vps_touched"])  # type: ignore[index]

        stats = summarize_latencies_ms(
            [10.0, 20.0, 30.0, 40.0, 100.0], error_count=1, elapsed_sec=2.0
        )
        self.assertEqual(stats["requests_total"], 5)
        self.assertEqual(stats["error_rate_pct"], 20.0)
        self.assertEqual(stats["throughput_rps"], 2.5)
        self.assertEqual(stats["p50_ms"], 30.0)
        self.assertGreaterEqual(float(stats["p95_ms"]), 80.0)


if __name__ == "__main__":
    unittest.main()
