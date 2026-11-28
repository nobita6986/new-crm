"""CI-gated regression suite for T1A UI polish integration + Live VPS preservation.

Automatically discovered and executed with zero-skip enforcement in `p4-ci.yml`
(`source-and-static-gate` -> `deploy/release` unittest discovery).
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh"
DEV_HOOK_TEST = REPO_ROOT / "deploy" / "dev" / "test_cskh_sidebar_hook.py"
DEV_BRIDGE_MJS = REPO_ROOT / "deploy" / "dev" / "test_crm_sidebar_bridge.mjs"


def _load_dev_hook_suite() -> unittest.TestSuite:
    spec = importlib.util.spec_from_file_location(
        "test_cskh_sidebar_hook_loaded", str(DEV_HOOK_TEST)
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return unittest.TestLoader().loadTestsFromTestCase(mod.CskhSidebarHookTests)


class TestUiIntegrationAndLiveParity(unittest.TestCase):
    def test_cskh_sidebar_hook_and_theme_injector_contracts(self) -> None:
        suite = _load_dev_hook_suite()
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])
        self.assertEqual(result.skipped, [])
        self.assertGreaterEqual(result.testsRun, 4)

    def test_crm_sidebar_bridge_node_dom_suite(self) -> None:
        node_bin = shutil.which("node")
        self.assertIsNotNone(
            node_bin, "node binary must be available for bridge DOM tests"
        )
        proc = subprocess.run(
            [str(node_bin), str(DEV_BRIDGE_MJS)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"test_crm_sidebar_bridge.mjs failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )

    def test_live_setup_wizard_vietnam_default_and_defensive_guards(self) -> None:
        wiz_js = (APP_DIR / "public" / "cskh" / "setup-wizard.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('values.country ||= "Vietnam"', wiz_js)
        self.assertIn("Array.isArray(frappe.setup.slides_settings)", wiz_js)
        self.assertIn('slide.name === "welcome"', wiz_js)

    def test_cskh_shell_fouc_fix_sidebar_stacking_and_ssr_sentinel_parity(self) -> None:
        shell_html = (APP_DIR / "templates" / "cskh" / "_shell.html").read_text(
            encoding="utf-8"
        )
        cskh_css = (APP_DIR / "public" / "cskh" / "cskh.css").read_text(
            encoding="utf-8"
        )

        # 836db33: cskh.css loaded in head_include before page_content to eliminate SVG FOUC
        self.assertIn("{% block head_include %}", shell_html)
        self.assertLess(
            shell_html.index("/assets/crm_cskh/cskh/cskh.css"),
            shell_html.index("{% block page_content %}"),
        )
        # 2fd88d3: modern left sidebar & mobile nav toggle
        self.assertIn('class="cskh-sidebar"', shell_html)
        self.assertIn('id="cskh-mobile-nav-toggle"', shell_html)
        # Preserve P4 G18 role-shareable SSR skeleton sentinels (`cskh_renderer.py`)
        for sentinel_expr in (
            "cskh_user_html",
            "cskh_user_json",
            "cskh_timezone_json",
            "cskh_csrf_header_json",
        ):
            self.assertIn(sentinel_expr, shell_html)

        # 386bb14: explicit stacking order so Frappe navbar dropdown renders above topbar/sidebar/subnav
        z_vals = {
            m.group(1): int(m.group(2))
            for m in re.finditer(r"--cskh-z-([a-z]+)\s*:\s*(\d+)\s*;", cskh_css)
        }
        self.assertGreater(z_vals["dropdown"], z_vals["navbar"])
        self.assertGreater(z_vals["navbar"], z_vals["sidebar"])
        self.assertGreater(z_vals["sidebar"], z_vals["topbar"])
        self.assertGreater(z_vals["topbar"], z_vals["subnav"])

        # 2f835b7: zero upward translateY hover lift across CSKH, CRM, Desk, and Login CSS
        for css_name in (
            "cskh.css",
            "crm-theme.css",
            "desk-theme.css",
            "web-theme.css",
        ):
            css_text = (APP_DIR / "public" / "cskh" / css_name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn(
                "translateY(-", css_text, f"Unexpected upward hover lift in {css_name}"
            )

    def test_csp_and_self_hosted_asset_hygiene_across_ui_templates_and_stylesheets(
        self,
    ) -> None:
        css_files = [
            APP_DIR / "public" / "cskh" / name
            for name in ("cskh.css", "crm-theme.css", "desk-theme.css", "web-theme.css")
        ]
        html_files = [APP_DIR / "templates" / "cskh" / "_shell.html"] + sorted(
            (APP_DIR / "www" / "cskh").glob("*.html")
        )
        for fpath in css_files:
            txt = fpath.read_text(encoding="utf-8")
            self.assertNotIn("@import", txt, f"Disallowed @import in {fpath.name}")
            self.assertIsNone(
                re.search(r"url\(\s*['\"]?(?:https?:)?//", txt, flags=re.IGNORECASE),
                f"External URL found in {fpath.name}",
            )
        for fpath in html_files:
            txt = fpath.read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(
                    r"(?:src|href)\s*=\s*['\"](?:https?:)?//", txt, flags=re.IGNORECASE
                ),
                f"External asset src/href found in {fpath.name}",
            )

    def test_hrpartner_branding_prepaint_patch_dockerignore_and_cskh_brand_logo(
        self,
    ) -> None:
        patcher_path = REPO_ROOT / "deploy" / "dev" / "apply-crm-branding-patch.py"
        self.assertTrue(patcher_path.is_file())
        spec = importlib.util.spec_from_file_location(
            "apply_crm_branding_patch", str(patcher_path)
        )
        assert spec is not None and spec.loader is not None
        patcher_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patcher_mod)

        sample_crm_built_html = (
            '<!doctype html>\n<html class="h-full" lang="en">\n  <head>\n'
            '    <meta charset="UTF-8" />\n'
            '    <link rel="icon" type="image/png" sizes="196x196" href="/assets/crm/manifest/apple-icon-180.png" />\n'
            '    <link rel="apple-touch-icon" href="/assets/crm/manifest/apple-icon-180.png" />\n'
            '  </head>\n  <body><div id="app"></div></body>\n</html>\n'
        )
        patched_once = patcher_mod.patch_template(sample_crm_built_html)
        patched_twice = patcher_mod.patch_template(patched_once)
        self.assertEqual(patched_once, patched_twice)
        self.assertIn(
            '<link rel="icon" href="{{ favicon | e }}" type="image/x-icon">',
            patched_once,
        )
        self.assertNotIn('rel="icon" type="image/png" sizes="196x196"', patched_once)
        self.assertIn('id="hrpartner-branding-prepaint"', patched_once)
        self.assertIn(
            "{% set hrpartner_logo = splash_image | default('') %}", patched_once
        )
        self.assertIn("object-fit: contain;", patched_once)

        dockerignore = (
            REPO_ROOT / "deploy" / "images" / "Containerfile.dockerignore"
        ).read_text(encoding="utf-8")
        self.assertIn("!deploy/dev/apply-crm-branding-patch.py", dockerignore)

        containerfile = (REPO_ROOT / "deploy" / "images" / "Containerfile").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "COPY --chown=frappe:0 deploy/dev/apply-crm-branding-patch.py /tmp/cskh-build/apply-crm-branding-patch.py",
            containerfile,
        )
        self.assertLess(
            containerfile.index("CI=true bench build --production --force;"),
            containerfile.index(
                "./env/bin/python /tmp/cskh-build/apply-crm-branding-patch.py apps/crm/crm/www/crm.html;"
            ),
        )

        shell_html = (APP_DIR / "templates" / "cskh" / "_shell.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("{% if splash_image %}", shell_html)
        self.assertIn(
            '<img src="{{ splash_image | e }}" alt="HRPARTNER" class="cskh-sidebar-logo is-brand-logo" width="48" height="38">',
            shell_html,
        )

        cskh_css = (APP_DIR / "public" / "cskh" / "cskh.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".cskh-sidebar-logo.is-brand-logo", cskh_css)
        self.assertIn("object-fit: contain;", cskh_css)


if __name__ == "__main__":
    unittest.main()
