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

FROZEN_SLO_SPEC_SHA256 = (
    "ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d"
)
FORBIDDEN_PRODUCTION_TOKENS = (
    "chaos",
    "maybe_fail",
    "maybe_crash",
    "injectedfailure",
    "_exit",
)


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
        py_files = [APP_ROOT / "gunicorn_worker.py"]
        for sub in ("api", "services", "jobs", "www"):
            folder = APP_ROOT / sub
            py_files.extend(sorted(folder.rglob("*.py")))
        for py_file in py_files:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                name = None
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
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

    def test_hooks_py_preserves_tier3_n_minus_1_rollback_compatibility(self) -> None:
        hooks_src = (APP_ROOT / "hooks.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "crm_cskh.services.runtime_cache",
            hooks_src,
            "hooks.py must not serialize new module paths into Redis app_hooks (breaks G09 Tier 3 N->N-1 rollback)",
        )
        self.assertNotIn(
            "crm_cskh.www.cskh_renderer",
            hooks_src,
            "hooks.py must not serialize page_renderer into Redis app_hooks (breaks G09 Tier 3 N->N-1 rollback)",
        )

    def test_cli_and_cross_worker_cache_invalidation_contracts(self) -> None:
        init_src = (APP_ROOT / "__init__.py").read_text(encoding="utf-8")
        perm_src = (APP_ROOT / "permissions.py").read_text(encoding="utf-8")
        rc_src = (APP_ROOT / "services" / "runtime_cache.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_ensure_mutation_hooks_installed()", init_src)
        self.assertIn("on_user_or_hierarchy_doc_change", perm_src)
        self.assertIn("_redis_mod.Redis.hgetall", rc_src)
        self.assertIn("_redis_mod.Redis.hincrby", rc_src)
        self.assertIn("_redis_mod.Redis.hsetnx", rc_src)
        self.assertIn("_EPOCH_GEN_FIELD", rc_src)
        self.assertNotIn("cache.hgetall(rkey)", rc_src)

    def test_sql_budget_30_and_g14_closed_wi_breached_handoff_contracts(self) -> None:
        suite_src = (
            APP_ROOT / "tests" / "test_p4_bg02_and_slo_remediation.py"
        ).read_text(encoding="utf-8")
        sla_src = (APP_ROOT / "services" / "sla_engine.py").read_text(encoding="utf-8")
        g14_src = (
            REPO_ROOT
            / "deploy"
            / "release"
            / "batch2_delta"
            / "verify_g13_g14_g15_g17_delta.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'self.assertLessEqual(sql_counter["n"], 30,',
            suite_src,
            "Warm record_attempt SQL budget assertion must strictly enforce <= 30 SQL statements",
        )
        self.assertIn(
            "def test_g14_closed_work_item_void_breached_handoff_and_restore_sla_relink(",
            suite_src,
        )
        self.assertIn(
            "'breached_handoff'",
            sla_src.split("def on_correction_reviewed_in_tx", 1)[1].split(
                "def on_campaign_status_changed_in_tx", 1
            )[0],
        )
        self.assertIn(
            "f19_closed_wi_void_breached_handoff_and_restore_relinked",
            g14_src,
        )

    def test_diff_impact_includes_bg02_slo_remediation_suite(self) -> None:
        from deploy.release import run_diff_impact_verification as rdiv

        plan = rdiv.resolve_diff_impact_plan(
            ["apps/crm_cskh/crm_cskh/hooks.py"], repo_root=REPO_ROOT
        )
        self.assertIn(
            "crm_cskh.tests.test_p4_bg02_and_slo_remediation", plan["junit_modules"]
        )

    def test_bounded_lru_concurrency_and_eviction_via_ast_isolated_exec(self) -> None:
        src = (APP_ROOT / "services" / "runtime_cache.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls_nodes = [
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "_BoundedLRU"
        ]
        self.assertEqual(len(cls_nodes), 1)
        mod = ast.Module(body=cls_nodes, type_ignores=[])
        ns: dict = {
            "threading": threading,
            "OrderedDict": __import__("collections").OrderedDict,
        }
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

    def test_phone_identity_fastpath_parity_and_error_contracts(self) -> None:
        import sys

        app_pkg_dir = str(REPO_ROOT / "apps" / "crm_cskh")
        if app_pkg_dir not in sys.path:
            sys.path.insert(0, app_pkg_dir)
        from crm_cskh.services.phone_identity import (
            InvalidPhone,
            looks_like_scientific_notation,
            normalize_vn_phone,
        )

        valid_cases = [
            ("0912345678", "+84912345678"),
            (" 0387654321\t", "+84387654321"),
            ("84567890123", "+84567890123"),
            ("+84789012345", "+84789012345"),
            ("0812-345-678", "+84812345678"),
            ("+84 (912) 345.678", "+84912345678"),
            (
                "\uff10\uff19\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18",
                "+84912345678",
            ),
        ]
        for raw_in, expected in valid_cases:
            self.assertEqual(normalize_vn_phone(raw_in), expected)

        invalid_cases = [
            (None, "supplied as a text value"),
            (912345678, "supplied as a text value"),
            ("0" * 65, "short text value"),
            ("8.4912345678E+10", "scientific notation"),
            ("9.12345e8", "scientific notation"),
            ("   ", "is required"),
            ("---", "is required"),
            ("0912345678 / 0987654321", "more than one number"),
            ("09123abc78", "unsupported characters"),
            ("+14155552671", "Only Vietnamese numbers"),
            ("912345678", "start with 0, 84 or +84"),
            ("0241234567", "outside the supported Vietnamese mobile format"),
            ("091234567", "outside the supported Vietnamese mobile format"),
            ("09123456789", "outside the supported Vietnamese mobile format"),
        ]
        for raw_bad, msg_substr in invalid_cases:
            with self.assertRaises(InvalidPhone) as ctx:
                normalize_vn_phone(raw_bad)
            self.assertIn(msg_substr, str(ctx.exception))

        self.assertFalse(looks_like_scientific_notation("0912345678"))
        self.assertTrue(looks_like_scientific_notation("8.49123E+10"))

    def test_ssr_role_skeleton_zero_leakage_contracts(self) -> None:
        shell_src = (APP_ROOT / "templates" / "cskh" / "_shell.html").read_text(
            encoding="utf-8"
        )
        ctx_src = (APP_ROOT / "www" / "cskh_context.py").read_text(encoding="utf-8")
        rend_src = (APP_ROOT / "www" / "cskh_renderer.py").read_text(encoding="utf-8")
        self.assertIn("cskh_user_html", shell_src)
        self.assertIn("cskh_user_json", shell_src)
        self.assertIn("cskh_user_html", ctx_src)
        self.assertIn("cskh_user_json", ctx_src)
        self.assertIn("_is_role_shareable_skeleton", rend_src)
        self.assertIn("_RENDER_LOCK_STRIPES", rend_src)

    def test_api01c_fair_gthread_worker_and_start_sh_contracts(self) -> None:
        gw_src = (APP_ROOT / "gunicorn_worker.py").read_text(encoding="utf-8")
        start_sh = (
            REPO_ROOT / "deploy" / "images" / "resources" / "start.sh"
        ).read_bytes()
        self.assertNotIn(b"\r\n", start_sh, "start.sh must retain LF line endings")
        start_txt = start_sh.decode("utf-8")
        self.assertIn("crm_cskh.gunicorn_worker.FairGthreadWorker", start_txt)
        self.assertIn("--keep-alive=", start_txt)
        self.assertIn("class FairGthreadWorker(ThreadWorker):", gw_src)
        self.assertIn("self.shutdown_event", gw_src)
        self.assertIn("self.cfg.graceful_timeout", gw_src)


if __name__ == "__main__":
    unittest.main()
