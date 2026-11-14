"""Pure-stdlib tests for opt-in G18 distribution SQL telemetry."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[2] / "apps" / "crm_cskh"
sys.path.insert(0, str(APP_ROOT))

from crm_cskh.p4_diagnostics import (  # noqa: E402
    normalize_sql_shape,
    profile_database_sql,
)


class FakeDatabase:
    def __init__(self, *, fail_query: bool = False):
        self.fail_query = fail_query
        self.calls = []
        self.sql = self._sql
        self.commit = self._commit

    def _sql(self, query, values=(), **kwargs):
        self.calls.append((query, values))
        if self.fail_query:
            raise RuntimeError("synthetic sensitive payload: +84912345678")
        return [(1,)]

    def _commit(self, *args, **kwargs):
        return "committed"


class TestDistributionSQLDiagnostics(unittest.TestCase):
    def test_sql_normalization_masks_literals_comments_and_numbers(self):
        left = (
            "SELECT * FROM `tabX` WHERE phone = '+84912345678' AND id = 42 -- private\n"
        )
        right = ' select * from `tabX` where phone="+84900000000" and id=7 # other\n'
        self.assertEqual(normalize_sql_shape(left), normalize_sql_shape(right))
        self.assertNotIn("84912345678", normalize_sql_shape(left))
        self.assertNotIn("private", normalize_sql_shape(left))
        comment_marker_in_literal = "SELECT '--not a comment 84912345678' AS value"
        self.assertNotIn("84912345678", normalize_sql_shape(comment_marker_in_literal))
        quote_in_comment = "select 1 /* ' */ where email='secret@example.invalid'"
        self.assertNotIn(
            "secret@example.invalid", normalize_sql_shape(quote_in_comment)
        )
        db = FakeDatabase()
        with profile_database_sql(db, "run") as recorder:
            db.sql(quote_in_comment)
            report = recorder.snapshot(0)
        self.assertNotIn("secret@example.invalid", json.dumps(report, sort_keys=True))

    def test_fingerprint_equivalence_for_different_bound_values_and_literals(self):
        one = "SELECT name FROM `tabLead` WHERE name = 'CRM-LEAD-00001' AND idx=1"
        two = "select name from `tabLead` where name = 'CRM-LEAD-00099' and idx=99"
        self.assertEqual(normalize_sql_shape(one), normalize_sql_shape(two))

        db = FakeDatabase()
        with profile_database_sql(db, "synthetic-run") as recorder:
            db.sql(one)
            db.sql(two)
            report = recorder.snapshot(2)
        self.assertEqual(report["sql_query_count"], 2)
        self.assertEqual(len(report["fingerprints"]), 1)
        self.assertEqual(report["fingerprints"][0]["count"], 2)

    def test_success_and_exception_queries_are_counted_without_payloads(self):
        db = FakeDatabase()
        with profile_database_sql(db, "synthetic-run-0001") as recorder:
            self.assertEqual(
                db.sql("SELECT * FROM `tabX` WHERE phone=%s", ("+84912345678",)), [(1,)]
            )
            db.fail_query = True
            with self.assertRaisesRegex(RuntimeError, "sensitive payload"):
                db.sql("UPDATE `tabX` SET phone='+84912345678'")
            report = recorder.snapshot(100)

        self.assertEqual(report["progress"], 100)
        self.assertEqual(report["sql_query_count"], 2)
        self.assertEqual(len(report["fingerprints"]), 2)
        serialized = json.dumps(report, sort_keys=True)
        for sensitive in (
            "+84912345678",
            "CRM-LEAD-00001",
            "synthetic-run-0001",
            "SELECT",
            "UPDATE",
        ):
            self.assertNotIn(sensitive, serialized)
        for item in report["fingerprints"]:
            self.assertRegex(item["hmac_sha256"], r"^[0-9a-f]{64}$")

    def test_artifact_fingerprints_use_unexported_per_run_key(self):
        artifacts = []
        for run_id in ("run-a", "run-b"):
            db = FakeDatabase()
            with profile_database_sql(db, run_id) as recorder:
                db.sql("SELECT * FROM `tabX` WHERE email='secret@example.invalid'")
                artifacts.append(recorder.snapshot(1))

        left = artifacts[0]["fingerprints"][0]["hmac_sha256"]
        right = artifacts[1]["fingerprints"][0]["hmac_sha256"]
        self.assertNotEqual(left, right)
        for artifact in artifacts:
            serialized = json.dumps(artifact, sort_keys=True)
            self.assertNotIn("secret@example.invalid", serialized)
            self.assertNotIn("run-a", serialized)
            self.assertNotIn("run-b", serialized)

    def test_database_methods_are_restored_after_success_and_exception(self):
        for fail in (False, True):
            db = FakeDatabase(fail_query=fail)
            original_sql = db.sql
            original_commit = db.commit
            if fail:
                with self.assertRaises(RuntimeError):
                    with profile_database_sql(db, "run"):
                        db.sql("SELECT 1")
            else:
                with profile_database_sql(db, "run"):
                    db.sql("SELECT 1")
                    db.commit()
            self.assertIs(db.sql, original_sql)
            self.assertIs(db.commit, original_commit)

    def test_recorder_failure_never_changes_wrapped_database_result_or_error(self):
        for fail_query in (False, True):
            db = FakeDatabase(fail_query=fail_query)
            with patch(
                "crm_cskh.p4_diagnostics.SQLCallRecorder.record",
                side_effect=ValueError("diagnostic failure"),
            ):
                if fail_query:
                    with self.assertRaisesRegex(RuntimeError, "sensitive payload"):
                        with profile_database_sql(db, "synthetic-run"):
                            db.sql("SELECT 1")
                else:
                    with profile_database_sql(db, "synthetic-run"):
                        self.assertEqual(db.sql("SELECT 1"), [(1,)])

    def test_fingerprint_cardinality_is_bounded(self):
        db = FakeDatabase()
        with profile_database_sql(db, "run", max_fingerprints=1) as recorder:
            db.sql("SELECT * FROM `tabA`")
            db.sql("SELECT * FROM `tabB`")
            report = recorder.snapshot(0)
        self.assertEqual(report["sql_query_count"], 2)
        self.assertEqual(report["fingerprint_overflow"]["count"], 1)
        self.assertEqual(len(report["fingerprints"]), 1)


if __name__ == "__main__":
    unittest.main()
