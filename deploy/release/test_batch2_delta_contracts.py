#!/usr/bin/env python3
"""Offline unit tests for P4 Đợt 2 (`batch2_delta`) contracts, scrubber, schema classifier, and `--plan-only` CLI."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile

RELEASE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = RELEASE_DIR.parents[1]
if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

from batch2_delta import (  # noqa: E402
    BASELINE_MAIN_COMMIT_SHA,
    GOLDEN_DATASET_EXPECTED_SHA256,
    LOCKED_BATCH1_COMMIT_SHA,
    LOCKED_BATCH1_IMAGE_DIGEST,
)
from batch2_delta.common_stack import (  # noqa: E402
    EDGE_RATELIMIT_OVERRIDE,
    SecretScrubber,
    build_minimal_xlsx,
)
from batch2_delta.verify_g06_g09_migration_and_rollback import (  # noqa: E402
    SEED_N_MINUS_1_PY,
    SNAPSHOT_PY,
    classify_schema_diff,
    forward_fix_vs_restore_contract,
)
from batch2_delta.verify_g11_g12_security_topology import SEED_G11_G12_PY  # noqa: E402
from batch2_delta.verify_g13_g14_g15_g17_delta import G15_SETUP_AND_ORACLE_PY  # noqa: E402


class TestBatch2DeltaContracts(unittest.TestCase):
    def test_locked_provenance_and_golden_fixture_sha256(self) -> None:
        self.assertEqual(
            LOCKED_BATCH1_COMMIT_SHA, "427af4834eda48a15ca86c15e88342c3c40d5f2f"
        )
        self.assertEqual(
            BASELINE_MAIN_COMMIT_SHA, "467830195358b2431ac772486530ab935887a518"
        )
        self.assertTrue(
            LOCKED_BATCH1_IMAGE_DIGEST.startswith("ghcr.io/nobita6986/crm-cskh@sha256:")
        )
        golden_path = (
            REPO_ROOT
            / "apps"
            / "crm_cskh"
            / "crm_cskh"
            / "tests"
            / "fixtures"
            / "t1c_p3_golden_dataset_expected.json"
        )
        actual_sha = hashlib.sha256(golden_path.read_bytes()).hexdigest()
        self.assertEqual(actual_sha, GOLDEN_DATASET_EXPECTED_SHA256)

    def test_secret_scrubber_redacts_cookies_csrf_private_urls_and_phones(self) -> None:
        scrubber = SecretScrubber()
        scrubber.register("SuperSecretAdminPw!123", "<REDACTED_ADMIN_PW>")

        raw = (
            'Cookie: sid=abcdef0123456789; "X-Frappe-CSRF-Token": "tok-999888" '
            "url=/private/files/cskh_export_oct.csv phone=0908123456 "
            'intl=+84912345678 pw=SuperSecretAdminPw!123 "encryption_key": "fernet-xyz"'
        )
        scrubbed = scrubber.scrub_text(raw)
        self.assertNotIn("abcdef0123456789", scrubbed)
        self.assertNotIn("tok-999888", scrubbed)
        self.assertNotIn("cskh_export_oct.csv", scrubbed)
        self.assertNotIn("0908123456", scrubbed)
        self.assertNotIn("+84912345678", scrubbed)
        self.assertNotIn("SuperSecretAdminPw!123", scrubbed)
        self.assertNotIn("fernet-xyz", scrubbed)
        self.assertIn("******3456", scrubbed)
        self.assertIn("******5678", scrubbed)

        with self.assertRaises(RuntimeError):
            scrubber.assert_no_leaks("leak SuperSecretAdminPw!123 here")

    def test_schema_diff_classification_and_forward_fix_contract(self) -> None:
        base = {
            "tabCSKH Work Item": {
                "name": {
                    "column_type": "varchar(140)",
                    "is_nullable": "NO",
                    "column_default": None,
                },
                "status": {
                    "column_type": "varchar(140)",
                    "is_nullable": "YES",
                    "column_default": "'queued'",
                },
            }
        }
        # Class A: identical schema
        c_a = classify_schema_diff(base, base)
        self.assertEqual(c_a["compatibility_class"], "Class_A_Code_And_Runtime_Only")
        self.assertTrue(c_a["tier3_image_rollback_safe"])

        # Class B: additive nullable column
        after_b = {
            "tabCSKH Work Item": {
                **base["tabCSKH Work Item"],
                "extra_note": {
                    "column_type": "text",
                    "is_nullable": "YES",
                    "column_default": None,
                },
            }
        }
        c_b = classify_schema_diff(base, after_b)
        self.assertEqual(
            c_b["compatibility_class"], "Class_B_Additive_Backward_Compatible"
        )
        self.assertTrue(c_b["tier3_image_rollback_safe"])

        # Class D: dropped column -> Tier 3 unsafe, requires forward-fix or Tier 4 restore
        after_d = {"tabCSKH Work Item": {"name": base["tabCSKH Work Item"]["name"]}}
        c_d = classify_schema_diff(base, after_d)
        self.assertEqual(
            c_d["compatibility_class"],
            "Class_D_Breaking_Requires_Forward_Fix_Or_Tier4_Restore",
        )
        self.assertFalse(c_d["tier3_image_rollback_safe"])

        contract = forward_fix_vs_restore_contract()
        self.assertIn("tier3_image_rollback_n_to_n_minus_1", contract)
        self.assertIn("class_c_and_d_forward_fix_vs_tier4_restore", contract)
        self.assertIn(
            "primary_path_forward_fix",
            contract["class_c_and_d_forward_fix_vs_tier4_restore"],
        )
        self.assertIn(
            "last_resort_tier4_db_restore",
            contract["class_c_and_d_forward_fix_vs_tier4_restore"],
        )

    def test_build_minimal_xlsx_is_valid_ooxml_zip(self) -> None:
        raw = build_minimal_xlsx(
            [["phone", "name"], ["0908000111", "Ứng viên & Kiểm thử <XLSX>"]]
        )
        with zipfile.ZipFile(
            pathlib.Path(tempfile.mktemp(suffix=".xlsx"))
            if False
            else __import__("io").BytesIO(raw)
        ) as zf:
            names = set(zf.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("&amp;", sheet_xml)
            self.assertIn("&lt;XLSX&gt;", sheet_xml)

    def test_edge_ratelimit_override_is_self_contained_in_release_dir(self) -> None:
        self.assertTrue(EDGE_RATELIMIT_OVERRIDE.is_file())
        txt = EDGE_RATELIMIT_OVERRIDE.read_text(encoding="utf-8")
        self.assertIn("cskh-login-rl.ratelimit.average", txt)
        self.assertIn(
            "cskh-heavy-rl.ratelimit.sourcecriterion.requestheadername=Cookie", txt
        )

    def test_all_three_verifiers_support_plan_only_cli(self) -> None:
        scripts = [
            "verify_g06_g09_migration_and_rollback.py",
            "verify_g11_g12_security_topology.py",
            "verify_g13_g14_g15_g17_delta.py",
        ]
        with tempfile.TemporaryDirectory(prefix="b2-plan-") as tmp:
            tdir = pathlib.Path(tmp)
            for idx, sname in enumerate(scripts):
                out_json = tdir / f"out-{idx}.json"
                out_log = tdir / f"out-{idx}.log"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(RELEASE_DIR / "batch2_delta" / sname),
                        "--plan-only",
                        "--report-out",
                        str(out_json),
                        "--log-out",
                        str(out_log),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(proc.returncode, 0)
                parsed = json.loads(out_json.read_text(encoding="utf-8"))
                self.assertEqual(parsed["status"], "PLANNED")
                self.assertEqual(
                    parsed["locked_batch1_commit_sha"], LOCKED_BATCH1_COMMIT_SHA
                )

    def test_embedded_site_python_snippets_compile_cleanly(self) -> None:
        for name, src in (
            ("SEED_N_MINUS_1_PY", SEED_N_MINUS_1_PY),
            ("SNAPSHOT_PY", SNAPSHOT_PY),
            ("SEED_G11_G12_PY", SEED_G11_G12_PY),
            ("G15_SETUP_AND_ORACLE_PY", G15_SETUP_AND_ORACLE_PY),
        ):
            compile(src, f"<{name}>", "exec")

    def test_verifier_cskh_doctype_columns_and_api_keys(self) -> None:
        import re

        dt_root = REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh" / "crm_cskh" / "doctype"
        std_cols = {
            "name",
            "creation",
            "modified",
            "modified_by",
            "owner",
            "docstatus",
            "idx",
        }
        schema: dict[str, set[str]] = {}
        for jf in dt_root.glob("cskh_*/*.json"):
            data = json.loads(jf.read_text(encoding="utf-8"))
            if data.get("doctype") == "DocType" and data.get("name"):
                cols = set(std_cols) | {
                    f["fieldname"]
                    for f in data.get("fields", [])
                    if f.get("fieldname")
                    and f.get("fieldtype")
                    not in ("Section Break", "Column Break", "Tab Break", "HTML")
                }
                schema[data["name"]] = cols

        for sname in (
            "verify_g06_g09_migration_and_rollback.py",
            "verify_g11_g12_security_topology.py",
            "verify_g13_g14_g15_g17_delta.py",
        ):
            src = (RELEASE_DIR / "batch2_delta" / sname).read_text(encoding="utf-8")
            self.assertNotIn('["attempt"]', src, f"Stale record_attempt key in {sname}")
            self.assertNotIn(
                '["correction"]', src, f"Stale request_correction key in {sname}"
            )
            self.assertNotIn("dedup_key", src, f"Non-existent dedup_key in {sname}")
            self.assertNotIn(
                "reschedule_count", src, f"Non-existent reschedule_count in {sname}"
            )
            for m in re.finditer(
                r"SELECT\s+([a-zA-Z0-9_,\s]+)\s+FROM\s+`tab(CSKH [^`]+)`", src
            ):
                sel_part, dt_name = m.group(1), m.group(2)
                if "COUNT(" in sel_part.upper() or dt_name not in schema:
                    continue
                for raw_col in sel_part.split(","):
                    col = raw_col.strip().split()[0]
                    if "." in col:
                        col = col.split(".")[-1]
                    self.assertIn(
                        col,
                        schema[dt_name],
                        f"{sname}: column {col!r} missing from {dt_name}",
                    )

    def test_lock_diff_parsers_and_reachability_probe_compile(self) -> None:
        from collect_cve_and_lock_diff_evidence import (
            IN_CONTAINER_REACHABILITY_PROBE,
            _diff_kv_map,
            _parse_dpkg_list,
            _parse_pip_freeze,
        )

        compile(IN_CONTAINER_REACHABILITY_PROBE, "<reachability_probe>", "exec")
        p_before = _parse_pip_freeze("PyJWT==2.13.0\npillow==12.3.0\nPyMySQL==1.1.1\n")
        p_after = _parse_pip_freeze("PyJWT==2.15.1\npillow==13.0.0\nPyMySQL==1.1.1\n")
        diff = _diff_kv_map(p_before, p_after)
        self.assertEqual(diff["changed_count"], 2)
        self.assertEqual(diff["added_count"], 0)
        dpkg = _parse_dpkg_list(
            "ii  libmariadb3:amd64  1:11.8.6-0+deb13u1  amd64\nlibxml2\t2.12.7+dfsg\n"
        )
        self.assertIn("libmariadb3:amd64", dpkg)
        self.assertIn("libxml2", dpkg)


if __name__ == "__main__":
    unittest.main()
