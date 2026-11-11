#!/usr/bin/env python3
"""Diff-impact verification planner & isolated JUnit regression runner for P4 releases (G04).

Implements Work Item 5:
1. Loads the canonical P0–P3 test inventories directly from tracked JSON manifests:
   - `g007`: `deploy/dev/seed/g007_test_inventory.json` (P0 RBAC, permissions, DocShare)
   - `p1`:   `deploy/dev/seed/p1_test_inventory.json` (P1 imports, identity, distribution, ownership)
   - `p2`:   `deploy/dev/seed/p2_test_inventory.json` (P2 lifecycle, outcomes, call log, reconciliation)
   - `p3`:   `deploy/dev/seed/p3_test_inventory.json` (P3 calendar, SLA, KPI, reporting & security)
   - `p3_ui`: `deploy/p3/ui_test_inventory.json` (P3 UI pages & context)
   plus auxiliary modules (`test_error_response_normalization`, `test_ui_p1_endpoints`,
   `test_ui_p2_pages_and_context`), verifying every module exists on disk under `apps/crm_cskh`.
2. Maps `git diff --name-only <base>..<head>` to impacted verification suites:
   - Escalates to FULL P0–P3 suite whenever container image / dependency locks / release tooling /
     Compose topology / core Frappe hooks, patches, permissions, or DocTypes change.
   - Always enforces clean-site install + `migrate` x2 + assets + auth + Socket.IO (`verify_clean_site.py`).
3. Executes inside isolated `deploy/images/compose.verify.yaml` (`--no-build`, zero host code mounts)
   and gates per-method JUnit results through `deploy/dev/seed/ci_junit_gate.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

INVENTORY_SPECS: tuple[tuple[str, str, str], ...] = (
    ("g007", "deploy/dev/seed/g007_test_inventory.json", "junit"),
    ("p1", "deploy/dev/seed/p1_test_inventory.json", "junit"),
    ("p2", "deploy/dev/seed/p2_test_inventory.json", "junit"),
    ("p3", "deploy/dev/seed/p3_test_inventory.json", "junit"),
    ("p3_ui", "deploy/p3/ui_test_inventory.json", "junit-ui"),
)

AUXILIARY_MODULES: dict[str, tuple[str, ...]] = {
    "core": (
        "crm_cskh.tests.test_error_response_normalization",
        "crm_cskh.tests.test_p4_security_and_scale_gaps",
    ),
    "p1_ui": ("crm_cskh.tests.test_ui_p1_endpoints",),
    "p2_ui": ("crm_cskh.tests.test_ui_p2_pages_and_context",),
}

FULL_SUITE_PREFIXES: tuple[str, ...] = (
    "deploy/images/",
    "deploy/release/",
    "deploy/compose/",
    ".github/workflows/p4-",
    "apps/crm_cskh/pyproject.toml",
    "apps/crm_cskh/crm_cskh/hooks.py",
    "apps/crm_cskh/crm_cskh/patches.txt",
    "apps/crm_cskh/crm_cskh/patches/",
    "apps/crm_cskh/crm_cskh/permissions",
    "apps/crm_cskh/crm_cskh/crm_cskh/doctype/",
)


def _module_to_rel_path(dotted_module: str) -> str:
    return f"apps/crm_cskh/{dotted_module.replace('.', '/')}.py"


def load_canonical_inventories(
    repo_root: Path = REPO_ROOT,
) -> dict[str, dict[str, object]]:
    loaded: dict[str, dict[str, object]] = {}
    for suite_id, rel_inv, xml_bucket in INVENTORY_SPECS:
        inv_path = repo_root / rel_inv
        if not inv_path.is_file():
            raise FileNotFoundError(f"Missing canonical test inventory: {inv_path}")
        raw = json.loads(inv_path.read_text(encoding="utf-8"))
        modules: list[str] = []
        raw_modules = raw.get("modules")
        if isinstance(raw_modules, dict):
            for mod_key in raw_modules:
                mod = str(mod_key).strip()
                if mod and mod not in modules:
                    modules.append(mod)
        for entry in raw.get("tests") or []:
            if isinstance(entry, dict):
                mod = str(entry.get("module") or "").strip()
                if mod and mod not in modules:
                    modules.append(mod)
        if not modules:
            raise ValueError(
                f"Inventory {rel_inv} declared zero test modules (expected non-empty 'modules' map)"
            )
        for mod in modules:
            mod_file = repo_root / _module_to_rel_path(mod)
            if not mod_file.is_file():
                raise FileNotFoundError(
                    f"Inventory {rel_inv} references missing test module {mod!r} ({mod_file})"
                )
        loaded[suite_id] = {
            "inventory_rel_path": rel_inv,
            "xml_bucket": xml_bucket,
            "expected_total": int(raw["expected_total"]),
            "modules": modules,
        }

    for group, mods in AUXILIARY_MODULES.items():
        for mod in mods:
            mod_file = repo_root / _module_to_rel_path(mod)
            if not mod_file.is_file():
                raise FileNotFoundError(
                    f"Auxiliary group {group!r} references missing module {mod!r} ({mod_file})"
                )
    return loaded


def resolve_diff_impact_plan(
    changed_files: list[str],
    *,
    repo_root: Path = REPO_ROOT,
    force_full_suite: bool = False,
) -> dict[str, object]:
    inventories = load_canonical_inventories(repo_root)
    norm_files = sorted(
        {f.strip().replace("\\", "/") for f in changed_files if f.strip()}
    )

    reasons: list[str] = []
    full_escalation = force_full_suite or not norm_files
    if force_full_suite:
        reasons.append("--full-suite explicitly requested")
    elif not norm_files:
        reasons.append("empty diff list -> defaulting fail-safe to full P0-P3 suite")

    for path in norm_files:
        if any(path.startswith(pfx) for pfx in FULL_SUITE_PREFIXES):
            full_escalation = True
            reasons.append(f"global runtime/image/schema impact path: {path}")
            break

    selected_suites: list[str] = ["g007"]
    extra_modules: list[str] = list(AUXILIARY_MODULES["core"])

    if full_escalation:
        selected_suites = [s for s, _, _ in INVENTORY_SPECS]
        for mods in AUXILIARY_MODULES.values():
            for m in mods:
                if m not in extra_modules:
                    extra_modules.append(m)
    else:
        for path in norm_files:
            low = path.lower()
            if any(
                tok in low
                for tok in (
                    "p1",
                    "import",
                    "distribution",
                    "ownership",
                    "identity",
                    "pool",
                )
            ):
                if "p1" not in selected_suites:
                    selected_suites.append("p1")
                    reasons.append(f"P1 impact from {path}")
                for m in AUXILIARY_MODULES["p1_ui"]:
                    if m not in extra_modules:
                        extra_modules.append(m)
            if any(
                tok in low
                for tok in (
                    "p2",
                    "attempt",
                    "call",
                    "outcome",
                    "callback",
                    "campaign",
                    "reconcil",
                )
            ):
                if "p2" not in selected_suites:
                    selected_suites.append("p2")
                    reasons.append(f"P2 impact from {path}")
                for m in AUXILIARY_MODULES["p2_ui"]:
                    if m not in extra_modules:
                        extra_modules.append(m)
            if any(
                tok in low
                for tok in (
                    "p3",
                    "kpi",
                    "report",
                    "calendar",
                    "sla",
                    "scorecard",
                    "quality",
                    "public/cskh/",
                    "www/cskh",
                    "templates/cskh/",
                    "translations/",
                )
            ):
                for s_id in ("p3", "p3_ui"):
                    if s_id not in selected_suites:
                        selected_suites.append(s_id)
                reasons.append(f"P3/UI impact from {path}")
            if path.startswith("apps/crm_cskh/crm_cskh/tests/test_") and path.endswith(
                ".py"
            ):
                dotted = (
                    path.removeprefix("apps/crm_cskh/")
                    .removesuffix(".py")
                    .replace("/", ".")
                )
                matched_inv = False
                for s_id, meta in inventories.items():
                    if dotted in meta["modules"]:  # type: ignore[operator]
                        matched_inv = True
                        if s_id not in selected_suites:
                            selected_suites.append(s_id)
                if not matched_inv and dotted not in extra_modules:
                    extra_modules.append(dotted)

        # Fallback: if an app file under apps/crm_cskh changed without matching a specific phase keyword, run all suites
        if any(
            p.startswith("apps/crm_cskh/") for p in norm_files
        ) and selected_suites == ["g007"]:
            full_escalation = True
            selected_suites = [s for s, _, _ in INVENTORY_SPECS]
            for mods in AUXILIARY_MODULES.values():
                for m in mods:
                    if m not in extra_modules:
                        extra_modules.append(m)
            reasons.append(
                "unclassified apps/crm_cskh change -> escalated to full P0-P3"
            )

    ordered_suites = [s for s, _, _ in INVENTORY_SPECS if s in selected_suites]
    junit_modules: list[str] = []
    junit_ui_modules: list[str] = []
    expected_inventory_methods = 0
    selected_inventory_gates: list[dict[str, object]] = []

    for s_id in ordered_suites:
        meta = inventories[s_id]
        bucket = str(meta["xml_bucket"])
        mods = list(meta["modules"])  # type: ignore[arg-type]
        expected_inventory_methods += int(meta["expected_total"])  # type: ignore[arg-type]
        selected_inventory_gates.append(
            {
                "suite_id": s_id,
                "inventory": meta["inventory_rel_path"],
                "xml_bucket": bucket,
                "expected_total": meta["expected_total"],
                "modules": mods,
            }
        )
        target_list = junit_ui_modules if bucket == "junit-ui" else junit_modules
        for mod in mods:
            if mod not in target_list:
                target_list.append(mod)

    for aux_mod in extra_modules:
        if aux_mod not in junit_modules and aux_mod not in junit_ui_modules:
            junit_modules.append(aux_mod)

    return {
        "schema_version": "cskh-p4-diff-impact-plan-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "full_suite_escalated": full_escalation,
        "changed_files_count": len(norm_files),
        "changed_files": norm_files,
        "escalation_reasons": reasons,
        "clean_site_and_socketio_required": True,
        "selected_suites": ordered_suites,
        "selected_inventory_gates": selected_inventory_gates,
        "expected_gated_methods_total": expected_inventory_methods,
        "junit_modules": junit_modules,
        "junit_ui_modules": junit_ui_modules,
        "total_modules_to_run": len(junit_modules) + len(junit_ui_modules),
    }


def _collect_git_diff_files(repo_root: Path, base_ref: str, head_ref: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "diff", "--name-only", f"{base_ref}..{head_ref}"],
        text=True,
    )
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _run_checked(
    args: list[str], *, env: dict[str, str] | None = None, timeout: int = 900
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        args,
        env=merged,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def execute_verification_plan(
    plan: dict[str, object],
    *,
    app_image: str,
    out_dir: Path,
    repo_root: Path = REPO_ROOT,
    skip_clean_site: bool = False,
    clean_site_port: int = 18780,
    suite_http_port: int = 18880,
) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    junit_dir = out_dir / "junit"
    junit_ui_dir = out_dir / "junit-ui"
    junit_dir.mkdir(parents=True, exist_ok=True)
    junit_ui_dir.mkdir(parents=True, exist_ok=True)

    lock = json.loads(
        (repo_root / "deploy" / "images" / "release-lock.json").read_text(
            encoding="utf-8"
        )
    )

    if not skip_clean_site:
        _run_checked(
            [
                sys.executable,
                str(repo_root / "deploy" / "images" / "verify_clean_site.py"),
                "--app-image",
                app_image,
                "--pull-policy",
                "never",
                "--port",
                str(clean_site_port),
                "--out",
                str(out_dir / "clean-site-verify.json"),
            ],
            timeout=1200,
        )

    project_name = f"cskh-p4-suites-{secrets.token_hex(4)}"
    db_pw = secrets.token_hex(24)
    adm_pw = f"P4Suite#{secrets.token_hex(12)}"
    compose_file = repo_root / "deploy" / "images" / "compose.verify.yaml"

    with tempfile.TemporaryDirectory(prefix="cskh-p4-suites-") as sec_dir_s:
        db_pw_file = Path(sec_dir_s) / "db_pw.txt"
        db_pw_file.write_text(db_pw + "\n", encoding="utf-8")
        try:
            os.chmod(db_pw_file, 0o600)
        except OSError:
            pass

        compose_env = {
            "COMPOSE_PROJECT_NAME": project_name,
            "APP_IMAGE": app_image,
            "PULL_POLICY": "never",
            "SITE_NAME": "p4-suites.localhost",
            "VERIFY_HTTP_PORT": str(suite_http_port),
            "DB_PASSWORD_SECRETS_FILE": str(db_pw_file),
            "MARIADB_IMAGE": lock["infra_images"]["mariadb"],
            "REDIS_IMAGE": lock["infra_images"]["redis"],
        }
        compose_base = [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "-p",
            project_name,
        ]
        try:
            # Start only DB, Redis, configurator, and backend for deterministic unit-test execution
            # (matches p0-p3 CI topology so background RQ workers/scheduler never race tearDownClass deletes).
            _run_checked(
                [
                    *compose_base,
                    "up",
                    "-d",
                    "--no-build",
                    "db",
                    "redis-cache",
                    "redis-queue",
                    "configurator",
                    "backend",
                ],
                env=compose_env,
            )
            bootstrap_cmd = (
                "set -euo pipefail; "
                "cd /home/frappe/frappe-bench; "
                "cmp -s /home/frappe/frappe-bench/apps-order.txt /home/frappe/frappe-bench/sites/apps.txt; "
                "./env/bin/python -c 'import xmlrunner' || ./env/bin/pip install --no-cache-dir "
                "-c /home/frappe/frappe-bench/release-metadata/python-constraints.txt "
                "unittest-xml-reporting==3.2.0; "
                "./env/bin/pip check; "
                "bench new-site --no-mariadb-socket "
                '--db-root-password "$DB_PW" --admin-password "$ADM_PW" '
                "--mariadb-user-host-login-scope=% "
                "--install-app crm --install-app crm_cskh p4-suites.localhost; "
                "cmp -s /home/frappe/frappe-bench/apps-order.txt /home/frappe/frappe-bench/sites/apps.txt; "
                "bench --site p4-suites.localhost set-config allow_tests true; "
                "bench --site p4-suites.localhost migrate; "
                "bench --site p4-suites.localhost migrate; "
                "mkdir -p /tmp/p4-junit /tmp/p4-junit-ui"
            )
            _run_checked(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"DB_PW={db_pw}",
                    "-e",
                    f"ADM_PW={adm_pw}",
                    "backend",
                    "bash",
                    "-lc",
                    bootstrap_cmd,
                ],
                env=compose_env,
                timeout=900,
            )

            for mod in plan["junit_modules"]:  # type: ignore[union-attr]
                slug = str(mod).rsplit(".", 1)[-1]
                _run_checked(
                    [
                        *compose_base,
                        "exec",
                        "-T",
                        "-e",
                        "CI=true",
                        "backend",
                        "bash",
                        "-lc",
                        f"cd /home/frappe/frappe-bench && bench --site p4-suites.localhost --verbose run-tests --module '{mod}' --junit-xml-output '/tmp/p4-junit/{slug}.junit.xml'",
                    ],
                    env=compose_env,
                    timeout=900,
                )

            for mod in plan["junit_ui_modules"]:  # type: ignore[union-attr]
                slug = str(mod).rsplit(".", 1)[-1]
                _run_checked(
                    [
                        *compose_base,
                        "exec",
                        "-T",
                        "-e",
                        "CI=true",
                        "backend",
                        "bash",
                        "-lc",
                        f"cd /home/frappe/frappe-bench && bench --site p4-suites.localhost --verbose run-tests --module '{mod}' --junit-xml-output '/tmp/p4-junit-ui/{slug}.junit.xml'",
                    ],
                    env=compose_env,
                    timeout=600,
                )

            cid_res = subprocess.run(
                [*compose_base, "ps", "-q", "backend"],
                env={**os.environ, **compose_env},
                capture_output=True,
                text=True,
                check=True,
            )
            backend_cid = cid_res.stdout.strip()
            _run_checked(
                ["docker", "cp", f"{backend_cid}:/tmp/p4-junit/.", str(junit_dir)],
                env=compose_env,
            )
            _run_checked(
                [
                    "docker",
                    "cp",
                    f"{backend_cid}:/tmp/p4-junit-ui/.",
                    str(junit_ui_dir),
                ],
                env=compose_env,
            )
        finally:
            subprocess.run(
                [*compose_base, "down", "-v", "--remove-orphans"],
                env={**os.environ, **compose_env},
                capture_output=True,
                check=False,
                timeout=180,
            )

    gate_outputs: list[str] = []
    gated_xml_names: set[str] = set()
    for gate in plan["selected_inventory_gates"]:  # type: ignore[union-attr]
        inv_rel = str(gate["inventory"])
        bucket = str(gate["xml_bucket"])
        suite_id = str(gate["suite_id"])
        src_xml_dir = junit_ui_dir if bucket == "junit-ui" else junit_dir
        inv_path = repo_root / inv_rel
        inv_mods = list(gate.get("modules") or [])  # type: ignore[arg-type]
        suite_xml_dir = out_dir / f"junit-{suite_id}"
        suite_xml_dir.mkdir(parents=True, exist_ok=True)
        for mod in inv_mods:
            xml_name = f"{str(mod).rsplit('.', 1)[-1]}.junit.xml"
            src_xml = src_xml_dir / xml_name
            if src_xml.is_file():
                shutil.copy2(src_xml, suite_xml_dir / xml_name)
                gated_xml_names.add(xml_name)
        gate_name = (
            "p3-ui-gate.json"
            if suite_id == "p3_ui"
            else f"{Path(inv_rel).stem}-gate.json"
        )
        gate_out = out_dir / gate_name
        _run_checked(
            [
                sys.executable,
                str(repo_root / "deploy" / "dev" / "seed" / "ci_junit_gate.py"),
                "--inventory",
                str(inv_path),
                "--xml-dir",
                str(suite_xml_dir),
                "--out",
                str(gate_out),
            ]
        )
        gate_outputs.append(str(gate_out))

    # Also verify auxiliary JUnit XML reports outside phase inventories have >0 tests and 0 failures/errors
    auxiliary_checks: dict[str, dict[str, int]] = {}
    for mod in plan["junit_modules"]:  # type: ignore[union-attr]
        xml_name = f"{str(mod).rsplit('.', 1)[-1]}.junit.xml"
        if xml_name in gated_xml_names:
            continue
        aux_xml = junit_dir / xml_name
        if not aux_xml.is_file():
            raise FileNotFoundError(f"Missing auxiliary JUnit report: {aux_xml}")
        root_el = ET.parse(aux_xml).getroot()
        suites_els = (
            [root_el]
            if root_el.tag == "testsuite"
            else list(root_el.findall(".//testsuite"))
        )
        tests_cnt = sum(int(el.attrib.get("tests", 0)) for el in suites_els)
        fail_cnt = sum(int(el.attrib.get("failures", 0)) for el in suites_els)
        err_cnt = sum(int(el.attrib.get("errors", 0)) for el in suites_els)
        if tests_cnt <= 0 or fail_cnt > 0 or err_cnt > 0:
            raise RuntimeError(
                f"Auxiliary JUnit suite {xml_name} failed: tests={tests_cnt}, failures={fail_cnt}, errors={err_cnt}"
            )
        auxiliary_checks[xml_name] = {
            "tests": tests_cnt,
            "failures": fail_cnt,
            "errors": err_cnt,
        }

    summary = {
        "schema_version": "cskh-p4-diff-impact-execution-v1",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "app_image": app_image,
        "clean_site_executed": not skip_clean_site,
        "plan": plan,
        "junit_gate_reports": gate_outputs,
        "auxiliary_junit_checks": auxiliary_checks,
    }
    (out_dir / "diff-impact-verification-summary.json").write_bytes(
        (json.dumps(summary, indent=2) + "\n").encode("utf-8")
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-ref", default="", help="Base git ref for diff-impact")
    p.add_argument("--head-ref", default="HEAD", help="Head git ref for diff-impact")
    p.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Explicit changed file path (repeatable)",
    )
    p.add_argument(
        "--full-suite",
        action="store_true",
        help="Force full P0-P3 suite execution regardless of diff",
    )
    p.add_argument(
        "--plan-only",
        action="store_true",
        help="Resolve and validate test plan without starting Docker",
    )
    p.add_argument(
        "--skip-clean-site",
        action="store_true",
        help="Skip verify_clean_site.py when caller already ran it on the same digest",
    )
    p.add_argument("--app-image", default="", help="Pulled @sha256 image digest")
    p.add_argument("--out-dir", default="", help="Output directory for reports & JUnit")
    args = p.parse_args(argv)

    changed = list(args.changed_file)
    if args.base_ref and not args.full_suite:
        changed.extend(_collect_git_diff_files(REPO_ROOT, args.base_ref, args.head_ref))

    plan = resolve_diff_impact_plan(
        changed, repo_root=REPO_ROOT, force_full_suite=args.full_suite
    )
    if args.plan_only:
        if args.out_dir:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "diff-impact-plan.json").write_bytes(
                (json.dumps(plan, indent=2) + "\n").encode("utf-8")
            )
        print(json.dumps(plan, indent=2))
        return 0

    if not args.app_image or not args.out_dir:
        p.error("--app-image and --out-dir are required unless --plan-only is set")

    summary = execute_verification_plan(
        plan,
        app_image=args.app_image,
        out_dir=Path(args.out_dir),
        repo_root=REPO_ROOT,
        skip_clean_site=args.skip_clean_site,
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "total_modules": plan["total_modules_to_run"],
                "expected_gated_methods_total": plan["expected_gated_methods_total"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
