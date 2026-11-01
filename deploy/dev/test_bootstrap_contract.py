"""Run the bootstrap's fail-closed input checks without touching a real bench."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

BOOTSTRAP = Path(__file__).with_name("bootstrap.sh")


class BootstrapContractTests(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		self.addCleanup(self.tmp.cleanup)
		self.root = Path(self.tmp.name)
		bin_dir = self.root / "bin"
		bin_dir.mkdir()
		bench = bin_dir / "bench"
		bench.write_text(
			'#!/bin/sh\nprintf "%s\\n" "$*" >> "$TASK_BENCH_CALLS"\n'
			'if [ -n "$TASK_APP_LIST_JSON" ]; then printf "%s\\n" "$TASK_APP_LIST_JSON"; else exit 197; fi\n',
			encoding="utf-8",
		)
		bench.chmod(0o755)
		self.env = {
			**os.environ,
			"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
			"DB_ROOT_PASSWORD": "unused-test-value",
			"ADMIN_PASSWORD": "unused-test-value",
			"SITE_NAME": "test.localhost",
			"FRAPPE_TAG": "v15.123.1",
			"FRAPPE_SHA": "d2efa79657ba5c1abf96098b3bfacc199517d57e",
			"CRM_TAG": "v1.87.0",
			"CRM_SHA": "0d3910b456ef4fb913e77b29db011b3debc4f778",
			"BENCH_DIR": str(self.root / "bench"),
			"SRC_DIR": str(self.root / "source"),
			"TASK_BENCH_CALLS": str(self.root / "calls.txt"),
			"TASK_APP_LIST_JSON": "",
		}
		self.env.pop("INSTALL_CSKH", None)

	def assert_rejected_before_bench(self, install_value, expected):
		if install_value is not None:
			self.env["INSTALL_CSKH"] = install_value
		result = subprocess.run(
			["bash", str(BOOTSTRAP)], env=self.env, capture_output=True, text=True, timeout=10
		)
		self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
		self.assertIn(expected, result.stderr)
		self.assertFalse((self.root / "calls.txt").exists(), "Unsafe input reached bench/DB commands")

	def test_disabled_security_app_is_rejected_before_side_effects(self):
		self.assert_rejected_before_bench("0", "INSTALL_CSKH=1")

	def test_invalid_security_app_flag_is_rejected_before_side_effects(self):
		self.assert_rejected_before_bench("2", "INSTALL_CSKH=1")

	def test_default_requires_security_app_source_before_side_effects(self):
		self.assert_rejected_before_bench(None, "crm_cskh")

	def test_installed_app_detection_uses_json_not_display_text(self):
		source = BOOTSTRAP.read_text(encoding="utf-8")
		start = source.index("installed() {")
		end = source.index("\n\nif installed crm;", start)
		function = source[start:end]
		self.env["SITE"] = "test.localhost"
		self.env["TASK_APP_LIST_JSON"] = json.dumps({"test.localhost": ["frappe", "crm", "crm_cskh"]})
		for name, expected in (("crm", 0), ("crm_cskh", 0), ("missing_app", 1)):
			with self.subTest(app=name):
				result = subprocess.run(
					["bash", "-c", function + '\ninstalled "$1"', "test", name],
					env=self.env,
					capture_output=True,
					text=True,
					timeout=10,
				)
				self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == "__main__":
	unittest.main()
