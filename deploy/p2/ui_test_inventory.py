#!/usr/bin/env python3
"""Generate and check the canonical P2 UI test inventory (`test_ui_p2_*.py`).

Kept separate from backend `p1_test_inventory.json` / `p2_test_inventory.json`
and `deploy/p1/ui_test_inventory.json` so PRs never conflict on inventory files
while CI enforces fail-closed per-method JUnit coverage via `ci_junit_gate.py`.

Usage:
    python3 deploy/p2/ui_test_inventory.py --out deploy/p2/ui_test_inventory.json
    python3 deploy/p2/ui_test_inventory.py --check deploy/p2/ui_test_inventory.json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def collect_module(path: Path) -> dict[str, list[str]]:
    """Return `{class_name: [method, ...]}` for a test file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test")
        ]
        if methods:
            found[node.name] = methods
    return found


def build_inventory(tests_dir: Path) -> dict:
    modules: dict[str, list[str]] = {}
    for path in sorted(tests_dir.glob("test_ui_p2_*.py")):
        module = f"crm_cskh.tests.{path.stem}"
        entries: list[str] = []
        for class_name, methods in collect_module(path).items():
            for method in methods:
                entries.append(f"{class_name}.{method}")
        if entries:
            modules[module] = entries
    return {
        "schema_version": 1,
        "description": (
            "Canonical inventory of the P2 UI test methods (`test_ui_p2_*.py`). "
            "Enforced in CI via `deploy/dev/seed/ci_junit_gate.py`."
        ),
        "expected_total": sum(len(v) for v in modules.values()),
        "modules": modules,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P2 UI test inventory generator")
    parser.add_argument(
        "--tests-dir", default=None, help="Directory containing test_ui_p2_*.py"
    )
    parser.add_argument("--out", default=None, help="Write inventory JSON to this file")
    parser.add_argument(
        "--check", default=None, help="Compare on-disk inventory against source"
    )
    args = parser.parse_args(argv)

    root = repo_root()
    tests_dir = (
        Path(args.tests_dir)
        if args.tests_dir
        else root / "apps/crm_cskh/crm_cskh/tests"
    )
    inventory = build_inventory(tests_dir)

    if args.check:
        expected = json.loads(Path(args.check).read_text(encoding="utf-8"))
        if (
            expected.get("modules") != inventory["modules"]
            or expected.get("expected_total") != inventory["expected_total"]
        ):
            print("P2 UI inventory does NOT match the source:", file=sys.stderr)
            print(json.dumps(inventory, indent=2, ensure_ascii=False), file=sys.stderr)
            return 1
        print(
            f"P2 UI inventory matches the source: {inventory['expected_total']} methods "
            f"in {len(inventory['modules'])} module."
        )
        return 0

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {out_path} ({inventory['expected_total']} methods)")
        return 0

    print(json.dumps(inventory, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
