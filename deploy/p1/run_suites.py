#!/usr/bin/env python3
"""Run every P1 suite (and the P0 core inventory) on the P1 site.

The suites are run **one after the other**: two suites on the same site at
the same time fight over the database and produce false failures.
"""

import json
import subprocess
import sys

SITE = "p1.localhost"
BENCH = "/workspace/frappe-bench"
P1_MODULES = [
	"test_p1_api_permissions",
	"test_p1_concurrency",
	"test_p1_distribution",
	"test_p1_identity",
	"test_p1_imports",
	"test_p1_ownership",
	"test_p1_regressions",
]
P0_MODULES = [
	"test_permissions",
	"test_security_remediation",
	"test_security_remediation_v2",
	"test_docshare_lifecycle",
	"test_bt1c5_matrix_gaps",
]


def run(module):
	command = (
		f"cd {BENCH} && bench --site {SITE} run-tests --module crm_cskh.tests.{module} "
		"2>&1 | tail -6"
	)
	result = subprocess.run(
		["docker", "exec", "crm-cskh-p1-bench-1", "bash", "-lc", command],
		capture_output=True,
		text=True,
	)
	output = (result.stdout or "") + (result.stderr or "")
	ok = "OK" in output and "FAILED" not in output
	return module, ok, output.strip()


def main():
	modules = P1_MODULES + P0_MODULES
	report = []
	for module in modules:
		name, ok, output = run(module)
		report.append({"module": name, "ok": ok})
		print(f"{'PASS' if ok else 'FAIL'}  {name}")
		if not ok:
			print(output)
			print("-" * 60)
	print(json.dumps({"total": len(report), "passed": sum(1 for r in report if r["ok"])}))
	return 0 if all(r["ok"] for r in report) else 1


if __name__ == "__main__":
	sys.exit(main())
