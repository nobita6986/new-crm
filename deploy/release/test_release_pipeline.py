#!/usr/bin/env python3
"""Unit & security-boundary test suite for the P4-02 release pipeline (`deploy/release/*`)."""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = RELEASE_DIR.parents[1]
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from evaluate_security_scan import evaluate_trivy_report  # noqa: E402
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
            "    steps:\n      - uses: actions/checkout@v4\n"
        )
        errs = audit_workflow_text(Path("p4-ci.yml"), bad_pr_write)
        self.assertTrue(any("write" in e for e in errs), errs)
        self.assertTrue(any("environment" in e for e in errs), errs)
        self.assertTrue(any("REGISTRY_PUSH_TOKEN" in e for e in errs), errs)
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
                "STAGING_SSH_PRIVATE_KEY": "ssh-key",
            }
        )
        self.assertFalse(leaked["passed"])
        self.assertEqual(
            leaked["leaked_secret_env_names"],
            ["RELEASE_SIGNING_PRIVATE_KEY", "STAGING_SSH_PRIVATE_KEY"],
        )

    def test_locks_and_evidence_checksums_pass(self) -> None:
        res = check_locks_and_repo_hygiene(REPO_ROOT)
        self.assertTrue(res["passed"], f"Lock hygiene errors: {res['errors']}")
        self.assertEqual(res["verified_evidence_checksums"], 10)


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

    def test_security_scan_secret_and_cve_gates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            trivy_file = tmp / "trivy.json"
            exc_file = tmp / "exceptions.json"

            # 1. Clean report passes
            trivy_file.write_text(
                json.dumps({"ArtifactName": SAMPLE_DIGEST, "Results": []}),
                encoding="utf-8",
            )
            clean = evaluate_trivy_report(trivy_file)
            self.assertEqual(clean["status"], "PASS")

            # 2. Embedded secret ALWAYS fails even if allow_fixable_critical is True
            trivy_file.write_text(
                json.dumps(
                    {
                        "ArtifactName": SAMPLE_DIGEST,
                        "Results": [
                            {
                                "Target": "app/config.py",
                                "Secrets": [
                                    {
                                        "RuleID": "private-key",
                                        "Category": "AsymmetricPrivateKey",
                                        "Severity": "CRITICAL",
                                        "Title": "Private Key",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            sec_fail = evaluate_trivy_report(
                trivy_file,
                fail_on_secrets=True,
                fail_on_unapproved_fixable_critical=False,
            )
            self.assertEqual(sec_fail["status"], "FAIL")
            self.assertEqual(sec_fail["secret_findings_count"], 1)

            # 2b. Upstream PyJWT METADATA example JWT is excepted ONLY on exact target suffix + rule_id
            trivy_file.write_text(
                json.dumps(
                    {
                        "ArtifactName": SAMPLE_DIGEST,
                        "Results": [
                            {
                                "Target": "/home/frappe/frappe-bench/env/lib/python3.11/site-packages/pyjwt-2.13.0.dist-info/METADATA",
                                "Secrets": [
                                    {
                                        "RuleID": "jwt-token",
                                        "Category": "JWT",
                                        "Severity": "MEDIUM",
                                        "Title": "JWT token",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            pyjwt_fp_ok = evaluate_trivy_report(
                trivy_file,
                fail_on_secrets=True,
                fail_on_unapproved_fixable_critical=False,
            )
            self.assertEqual(pyjwt_fp_ok["status"], "PASS")
            self.assertEqual(pyjwt_fp_ok["secret_findings_count"], 0)
            self.assertEqual(pyjwt_fp_ok["excepted_secret_false_positives_count"], 1)

            # Same rule_id in app code MUST still fail closed!
            trivy_file.write_text(
                json.dumps(
                    {
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
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                evaluate_trivy_report(
                    trivy_file,
                    fail_on_secrets=True,
                    fail_on_unapproved_fixable_critical=False,
                )["status"],
                "FAIL",
            )

            # 3. Fixable CRITICAL fails unless active non-expired exception exists
            trivy_file.write_text(
                json.dumps(
                    {
                        "ArtifactName": SAMPLE_DIGEST,
                        "Results": [
                            {
                                "Target": "debian",
                                "Vulnerabilities": [
                                    {
                                        "VulnerabilityID": "CVE-2026-99999",
                                        "PkgName": "libfoo",
                                        "InstalledVersion": "1.0.0",
                                        "FixedVersion": "1.0.1",
                                        "Severity": "CRITICAL",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            cve_fail = evaluate_trivy_report(trivy_file, exceptions_path=exc_file)
            self.assertEqual(cve_fail["status"], "FAIL")

            # Expired exception still fails
            exc_file.write_text(
                json.dumps(
                    {
                        "exceptions": [
                            {
                                "cve_id": "CVE-2026-99999",
                                "expires_on": "2020-01-01",
                                "approved_by": "T0",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                evaluate_trivy_report(trivy_file, exceptions_path=exc_file)["status"],
                "FAIL",
            )

            # Active future exception passes and records excepted_critical
            exc_file.write_text(
                json.dumps(
                    {
                        "exceptions": [
                            {
                                "cve_id": "CVE-2026-99999",
                                "expires_on": "2099-12-31",
                                "approved_by": "T0",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            excepted = evaluate_trivy_report(trivy_file, exceptions_path=exc_file)
            self.assertEqual(excepted["status"], "PASS")
            self.assertEqual(len(excepted["excepted_critical"]), 1)


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

        # Synthesize registry-verified inspect summary from real P4-01 inspect file
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
            json.dumps({"status": "PASS", "secret_findings_count": 0}) + "\n",
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

    def test_digest_validator_rejects_tags_and_placeholders(self) -> None:
        validate_registry_digest(SAMPLE_DIGEST)
        for invalid in (
            "",
            "crm-cskh:0.0.1-92e14af",
            "BLOCKED",
            "ghcr.io/org/crm-cskh@sha256:" + ("0" * 64),
            "<registry>/crm-cskh@sha256:" + ("a" * 64),
        ):
            with self.assertRaises(ValueError, msg=f"Should reject {invalid!r}"):
                validate_registry_digest(invalid)

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

        # Valid verification for matching scope
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

        # Production/Staging scope MUST reject `ci_ephemeral_rehearsal` manifest!
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

        # 1-byte tamper on manifest MUST fail signature verification
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

        # Support containerd manifest-sha256 ID on downstream pulled stages as well as config ID
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

        # Negative 1: 1-char digest drift on Staging service MUST fail closed
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

        # Negative 2: Forbidden `/src` host bind-mount on Staging container MUST fail closed
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

        # Negative 3: Missing required Staging service (`scheduler`) MUST fail closed
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
