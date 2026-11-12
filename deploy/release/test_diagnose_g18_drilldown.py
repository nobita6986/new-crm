#!/usr/bin/env python3
"""Unit tests for G18 diagnostic & live-resource protection helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnose_g18_drilldown import (  # noqa: E402
    classify_http_outcomes,
    enforce_live_resource_headroom,
    scrub_sensitive_tokens,
)


class TestDiagnoseG18Drilldown(unittest.TestCase):
    def test_enforce_live_resource_headroom_passes_healthy_host(self) -> None:
        res = enforce_live_resource_headroom(
            snapshot={
                "cpu_count": 4,
                "mem_total_mib": 15980.0,
                "mem_available_mib": 12400.0,
                "loadavg_1m": 0.45,
                "disk_free_gib": 38.5,
            },
            running_container_names=["cskh-p4-bench-diag-a1b2-db-1"],
            forbid_cohosted_live_containers=True,
        )
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["cohosted_live_containers_detected"], [])

    def test_enforce_live_resource_headroom_blocks_live_container_when_forbidden(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "live/production containers"):
            enforce_live_resource_headroom(
                snapshot={
                    "cpu_count": 4,
                    "mem_total_mib": 15980.0,
                    "mem_available_mib": 12400.0,
                    "loadavg_1m": 0.45,
                    "disk_free_gib": 38.5,
                },
                running_container_names=["crm-cskh-prod-backend-1"],
                forbid_cohosted_live_containers=True,
            )

    def test_enforce_live_resource_headroom_blocks_low_memory_or_high_load(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "MemAvailable"):
            enforce_live_resource_headroom(
                snapshot={
                    "cpu_count": 4,
                    "mem_total_mib": 8192.0,
                    "mem_available_mib": 1500.0,
                    "loadavg_1m": 0.5,
                    "disk_free_gib": 25.0,
                },
                running_container_names=[],
            )
        with self.assertRaisesRegex(ValueError, "loadavg"):
            enforce_live_resource_headroom(
                snapshot={
                    "cpu_count": 4,
                    "mem_total_mib": 8192.0,
                    "mem_available_mib": 6000.0,
                    "loadavg_1m": 3.9,
                    "disk_free_gib": 25.0,
                },
                running_container_names=[],
            )

    def test_classify_http_outcomes_separates_timeouts_client_and_http_errors(
        self,
    ) -> None:
        outcomes = [
            (120.0, 200),
            (150.0, 200),
            (30010.0, 0),
            (30015.0, 0),
            (45.0, -1),
            (25.0, 403),
            (90.0, 502),
        ]
        summary = classify_http_outcomes(outcomes, elapsed_sec=2.0, timeout_sec=30.0)
        self.assertEqual(summary["requests_total"], 7)
        self.assertEqual(summary["ok_count"], 2)
        self.assertEqual(summary["error_count"], 5)
        self.assertEqual(summary["timeout_count"], 2)
        self.assertEqual(summary["client_conn_error_count"], 1)
        self.assertEqual(summary["client_error_count"], 3)
        self.assertEqual(summary["http_4xx_count"], 1)
        self.assertEqual(summary["http_5xx_count"], 1)
        self.assertEqual(summary["http_error_count"], 2)
        self.assertEqual(summary["throughput_rps"], 3.5)

    def test_scrub_sensitive_tokens_redacts_sid_and_cookies(self) -> None:
        raw = '{"hdr": "Cookie: sid=abc123def456; system_user=yes", "q": "sid=deadbeef99"}'
        cleaned = scrub_sensitive_tokens(raw)
        self.assertNotIn("abc123def456", cleaned)
        self.assertNotIn("deadbeef99", cleaned)
        self.assertIn("[REDACTED]", cleaned)


if __name__ == "__main__":
    unittest.main()
