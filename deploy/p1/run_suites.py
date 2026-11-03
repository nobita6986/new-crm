#!/usr/bin/env python3
"""Run every P1 suite, T1A UI endpoint suite, and P0 core suite on the P1 site.

The suites are run **one after the other**: two suites on the same site at
the same time fight over the database and produce false failures.
"""

import json
import re
import subprocess
import sys

SITE = "p1.localhost"
BENCH = "/workspace/frappe-bench"
P1_MODULES = [
	"test_p1_api_permissions",
	"test_p1_chaos",
	"test_p1_concurrency",
	"test_p1_distribution",
	"test_p1_identity",
	"test_p1_imports",
	"test_p1_ownership",
	"test_p1_regressions",
]
UI_MODULES = [
	"test_ui_p1_endpoints",
]
P0_MODULES = [
	"test_permissions",
	"test_security_remediation",
	"test_security_remediation_v2",
	"test_docshare_lifecycle",
	"test_bt1c5_matrix_gaps",
	"test_error_response_normalization",
]


def run(module):
	command = (
		f"cd {BENCH} && bench --site {SITE} run-tests --module crm_cskh.tests.{module} "
		"2>&1 | grep -E '^(OK|FAILED|ERROR:|FAIL:|Ran [0-9]+ tests)' "
	)
	result = subprocess.run(
		["docker", "exec", "crm-cskh-p1-bench-1", "bash", "-lc", command],
		capture_output=True,
		text=True,
		encoding="utf-8",
		errors="replace",
	)
	output = (result.stdout or "") + (result.stderr or "")
	ok = bool(re.search(r"^OK\s*$", output, re.M)) and "FAILED" not in output
	ran_match = re.search(r"Ran\s+(\d+)\s+tests", output)
	count = int(ran_match.group(1)) if ran_match else 0
	return module, ok, count, output.strip()


def main():
	modules = [("P1", m) for m in P1_MODULES] + [("UI", m) for m in UI_MODULES] + [("P0", m) for m in P0_MODULES]
	report = []
	for group, mod in modules:
		name, ok, count, output = run(mod)
		report.append({"group": group, "module": name, "methods": count, "ok": ok})
		print(f"{'PASS' if ok else 'FAIL'}  [{group}] {name} ({count} methods)", flush=True)
		if not ok:
			print(output.encode("ascii", "replace").decode("ascii"), flush=True)
			print("-" * 60, flush=True)

	summary = {
		"p1_methods": sum(r["methods"] for r in report if r["group"] == "P1" and r["ok"]),
		"ui_methods": sum(r["methods"] for r in report if r["group"] == "UI" and r["ok"]),
		"p0_core_methods": sum(
			r["methods"]
			for r in report
			if r["group"] == "P0" and r["module"] != "test_error_response_normalization" and r["ok"]
		),
		"p0_extra_methods": sum(
			r["methods"]
			for r in report
			if r["module"] == "test_error_response_normalization" and r["ok"]
		),
		"total_modules": len(report),
		"passed_modules": sum(1 for r in report if r["ok"]),
	}
	print(json.dumps(summary), flush=True)
	return 0 if all(r["ok"] for r in report) else 1


if __name__ == "__main__":
	sys.exit(main())
