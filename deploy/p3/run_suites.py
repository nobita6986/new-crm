#!/usr/bin/env python3
"""Run every P3 backend/UI suite, P2 backend/UI suite, P1 backend/UI suite, and P0 core suite on `p3ui.localhost`.

Strictly guarded so it NEVER touches Owner's `p1.localhost`, P2's `p2.localhost`, or T1B's `p3-t1b-dev` stack.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS_DIR = os.path.join(ROOT, "apps", "crm_cskh", "crm_cskh", "tests")

SITE = os.environ.get("P3_SITE", "p3ui.localhost")
CONTAINER = os.environ.get("P3_CONTAINER", "crm-cskh-p3-ui-bench-1")
BENCH = "/workspace/frappe-bench"

if (
    SITE in ("p1.localhost", "p2.localhost")
    or "p1-bench" in CONTAINER
    or "p2-ui" in CONTAINER
    or "p3-t1b" in CONTAINER
):
    raise RuntimeError(
        f"Refusing to run test suites against non-isolated P3 UI container/site: {CONTAINER} / {SITE}"
    )

P2_MODULES = [
    "test_p2_lifecycle",
    "test_p2_outcomes_policy",
    "test_p2_calllog_followup",
    "test_p2_corrections",
    "test_p2_permissions_sec",
    "test_p2_concurrency_chaos",
    "test_p2_reconciliation_perf",
    "test_p2_t1c_regressions",
]
P2_UI_MODULES = [
    "test_ui_p2_pages_and_context",
]
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
P1_UI_MODULES = [
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
P3_UI_MODULES = [
    "test_ui_p3_pages_and_context",
]


def discover_p3_backend_modules() -> list[str]:
    paths = sorted(glob.glob(os.path.join(TESTS_DIR, "test_p3_*.py")))
    return [os.path.splitext(os.path.basename(p))[0] for p in paths]


def run(module: str):
    command = (
        f"cd {BENCH} && bench --site {SITE} run-tests --module crm_cskh.tests.{module} "
        "2>&1 | grep -E '^(OK|FAILED|ERROR:|FAIL:|Ran [0-9]+ tests)' "
    )
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "bash", "-lc", command],
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


def wipe_site() -> None:
    cmd = f"cd {BENCH} && bench --site {SITE} execute crm_cskh.tools.p3_e2e_seed.wipe_p3_site_clean >/dev/null"
    subprocess.run(["docker", "exec", CONTAINER, "bash", "-lc", cmd], check=True)


def seed_p3_site() -> None:
    cmd = f"cd {BENCH} && bench --site {SITE} execute crm_cskh.tools.p3_e2e_seed.reset_and_seed_p3 >/dev/null"
    subprocess.run(["docker", "exec", CONTAINER, "bash", "-lc", cmd], check=True)


def main() -> None:
    p3_backend_modules = discover_p3_backend_modules()
    wipe_site()
    modules = (
        [("P0", m) for m in P0_MODULES]
        + [("P1", m) for m in P1_MODULES]
        + [("P1-UI", m) for m in P1_UI_MODULES]
        + [("P2", m) for m in P2_MODULES]
        + [("P2-UI", m) for m in P2_UI_MODULES]
        + [("P3", m) for m in p3_backend_modules]
        + [("P3-UI", m) for m in P3_UI_MODULES]
    )
    report = []
    prev_group = None
    for group, mod in modules:
        if prev_group == "P0" and group == "P1":
            wipe_site()
        elif prev_group == "P1-UI" and group == "P2":
            wipe_site()
        elif prev_group == "P2-UI" and group == "P3":
            wipe_site()
        elif prev_group == "P3" and group == "P3-UI":
            seed_p3_site()
        prev_group = group
        name, ok, count, output = run(mod)
        report.append({"group": group, "module": name, "methods": count, "ok": ok})
        print(
            f"{'PASS' if ok else 'FAIL'}  [{group}] {name} ({count} methods)",
            flush=True,
        )
        if not ok:
            print(output.encode("ascii", "replace").decode("ascii"), flush=True)
            print("-" * 60, flush=True)

    summary = {
        "site": SITE,
        "container": CONTAINER,
        "p3_methods": sum(
            r["methods"] for r in report if r["group"] == "P3" and r["ok"]
        ),
        "p3_ui_methods": sum(
            r["methods"] for r in report if r["group"] == "P3-UI" and r["ok"]
        ),
        "p2_methods": sum(
            r["methods"] for r in report if r["group"] == "P2" and r["ok"]
        ),
        "p2_ui_methods": sum(
            r["methods"] for r in report if r["group"] == "P2-UI" and r["ok"]
        ),
        "p1_methods": sum(
            r["methods"] for r in report if r["group"] == "P1" and r["ok"]
        ),
        "p1_ui_methods": sum(
            r["methods"] for r in report if r["group"] == "P1-UI" and r["ok"]
        ),
        "p0_core_methods": sum(
            r["methods"]
            for r in report
            if r["group"] == "P0"
            and r["module"] != "test_error_response_normalization"
            and r["ok"]
        ),
        "p0_extra_methods": sum(
            r["methods"]
            for r in report
            if r["module"] == "test_error_response_normalization" and r["ok"]
        ),
        "total_methods": sum(r["methods"] for r in report if r["ok"]),
        "failed_modules": [r["module"] for r in report if not r["ok"]],
        "modules": report,
    }
    print(json.dumps(summary, indent=2))
    if summary["failed_modules"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
