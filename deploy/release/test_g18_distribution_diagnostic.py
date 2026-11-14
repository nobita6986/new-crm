#!/usr/bin/env python3
"""Unit tests for safe, opt-in G18 distribution SQL diagnostics."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_p4_isolated import S1_IN_CONTAINER_BENCH_SCRIPT  # noqa: E402
from g18_distribution_diagnostic import instrument_s1_benchmark_script  # noqa: E402


class TestG18DistributionDiagnostic(unittest.TestCase):
    def test_instrumented_s1_script_compiles_and_marks_diagnostic_only(self) -> None:
        source = instrument_s1_benchmark_script(
            S1_IN_CONTAINER_BENCH_SCRIPT,
            poll_seconds=7200,
        )
        compile(source, "S1_G18_DIAGNOSTIC", "exec")
        self.assertNotEqual(source, S1_IN_CONTAINER_BENCH_SCRIPT)
        self.assertIn('"mode": "DIAGNOSTIC_ONLY_NOT_ACCEPTANCE"', source)
        self.assertIn('"raw_sql_or_bind_values_recorded": False', source)
        self.assertIn(
            'float(os.environ.get("P4_G18_DIAGNOSTIC_POLL_SECONDS", "1200"))', source
        )
        self.assertIn('"query_shape_sha256": fingerprint', source)
        self.assertNotIn('"sample_query":', source)
        self.assertNotIn('"sample_values":', source)

    def test_instrumentation_rejects_missing_or_duplicate_anchors(self) -> None:
        with self.assertRaisesRegex(ValueError, "anchor"):
            instrument_s1_benchmark_script("incomplete", poll_seconds=1200)
        anchor = 'dist_eligible_count = int((dist_prev.get("summary") or {}).get("eligible_count") or 0)\n'
        duplicated = S1_IN_CONTAINER_BENCH_SCRIPT.replace(anchor, anchor + anchor)
        with self.assertRaisesRegex(ValueError, "anchor"):
            instrument_s1_benchmark_script(duplicated, poll_seconds=1200)
        with self.assertRaisesRegex(ValueError, "between 1 and 7200"):
            instrument_s1_benchmark_script(
                S1_IN_CONTAINER_BENCH_SCRIPT, poll_seconds=7201
            )


if __name__ == "__main__":
    unittest.main()
