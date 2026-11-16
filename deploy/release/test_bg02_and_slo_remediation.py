#!/usr/bin/env python3
"""Pure-Python release & invariant tests for P4 `BG-02` streaming/batching and SLO remediation."""

from __future__ import annotations

import ast
import hashlib
import json
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh"
SLO_SPEC_PATH = REPO_ROOT / "deploy" / "release" / "p4-s1-slo-spec.json"
CVE_EXCEPTIONS_PATH = REPO_ROOT / "deploy" / "release" / "cve-exceptions.json"

FROZEN_SLO_SPEC_SHA256 = "ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d"
FORBIDDEN_PRODUCTION_TOKENS = ("chaos", "maybe_fail", "maybe_crash", "injectedfailure", "_exit")


class TestBg02AndSloGovernance(unittest.TestCase):
    def test_slo_spec_remains_byte_unchanged(self) -> None:
        raw = SLO_SPEC_PATH.read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(
            digest,
            FROZEN_SLO_SPEC_SHA256,
            "deploy/release/p4-s1-slo-spec.json must remain 100% unchanged!",
        )

    def test_cve_exceptions_remain_empty_without_signed_owner_approval(self) -> None:
        doc = json.loads(CVE_EXCEPTIONS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(doc.get("exceptions"), [])

    def test_zero_fault_injection_tokens_in_production_modules(self) -> None:
        for sub in ("api", "services", "jobs", "www"):
            folder = APP_ROOT / sub
            for py_file in sorted(folder.rglob("*.py")):
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
                for node in ast.walk(tree):
                    name = None
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        name = node.name
                    elif isinstance(node, ast.Name):
                        name = node.id
                    elif isinstance(node, ast.Attribute):
                        name = node.attr
                    if name and name != "__exit__":
                        low = name.lower()
                        for bad in FORBIDDEN_PRODUCTION_TOKENS:
                            self.assertNotIn(
                                bad,
                                low,
                                f"Forbidden fault-injection identifier {name!r} in {py_file}",
                            )

    def test_diff_impact_includes_bg02_slo_remediation_suite(self) -> None:
        from deploy.release import run_diff_impact_verification as rdiv

        plan = rdiv.resolve_diff_impact_plan(["apps/crm_cskh/crm_cskh/hooks.py"], repo_root=REPO_ROOT)
        self.assertIn("crm_cskh.tests.test_p4_bg02_and_slo_remediation", plan["junit_modules"])

    def test_bounded_lru_concurrency_and_eviction_via_ast_isolated_exec(self) -> None:
        src = (APP_ROOT / "services" / "runtime_cache.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls_nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_BoundedLRU"]
        self.assertEqual(len(cls_nodes), 1)
        mod = ast.Module(body=cls_nodes, type_ignores=[])
        ns: dict = {"threading": threading, "OrderedDict": __import__("collections").OrderedDict}
        exec(compile(mod, "runtime_cache.py", "exec"), ns)
        BoundedLRU = ns["_BoundedLRU"]

        lru = BoundedLRU(maxsize=32)
        for i in range(50):
            lru.set(f"k{i}", i)
        self.assertIsNone(lru.get("k0"))
        self.assertEqual(lru.get("k49"), 49)

        errors: list[Exception] = []

        def _worker(tid: int) -> None:
            try:
                for step in range(200):
                    key = f"t{tid % 4}-{step % 40}"
                    lru.set(key, (tid, step))
                    _ = lru.get(key)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
