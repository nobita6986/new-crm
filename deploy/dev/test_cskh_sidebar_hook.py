"""Unit tests for `/crm` sidebar bridge injection, Setup Wizard Vietnam default, and N-1 rollback safety."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PKG_ROOT = REPO_ROOT / "apps" / "crm_cskh"
APP_DIR = APP_PKG_ROOT / "crm_cskh"


def _install_frappe_stub(user: str = "Guest", roles: list[str] | None = None):
    stub = types.ModuleType("frappe")
    stub.session = types.SimpleNamespace(user=user)
    stub.local = types.SimpleNamespace()
    role_list = list(roles or [])
    stub.get_roles = lambda _u=None: list(role_list)
    sys.modules["frappe"] = stub
    return stub


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _DummyRequest:
    def __init__(self, path: str = "/crm/leads", method: str = "GET"):
        self.path = path
        self.method = method


class _DummyResponse:
    def __init__(
        self,
        data: str | bytes,
        status_code: int = 200,
        mimetype: str = "text/html",
    ):
        self.data = data
        self.status_code = status_code
        self.mimetype = mimetype


SAMPLE_CRM_HTML = (
    "<!doctype html><html><head><title>Frappe CRM</title></head>"
    '<body><div id="app" class="h-full"></div>'
    '<script type="module" src="/assets/crm/frontend/assets/index-abc123.js"></script>'
    "</body></html>"
)


class CskhSidebarHookTests(unittest.TestCase):
    def setUp(self):
        self._prev_frappe = sys.modules.get("frappe")
        if str(APP_PKG_ROOT) not in sys.path:
            sys.path.insert(0, str(APP_PKG_ROOT))
        _install_frappe_stub("Guest", [])
        self.sidebar_mod = _load_module(
            "crm_cskh.cskh_sidebar", APP_DIR / "cskh_sidebar.py"
        )
        self.hooks_mod = _load_module("crm_cskh.hooks", APP_DIR / "hooks.py")
        self.p1_errors_mod = _load_module(
            "crm_cskh.p1_errors", APP_DIR / "p1_errors.py"
        )

    def tearDown(self):
        if self._prev_frappe is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = self._prev_frappe

    def test_hooks_register_apps_screen_setup_wizard_and_preserve_n_minus_1_after_request(
        self,
    ):
        apps_entries = getattr(self.hooks_mod, "add_to_apps_screen", [])
        self.assertEqual(len(apps_entries), 1)
        entry = apps_entries[0]
        self.assertEqual(entry["name"], "crm_cskh")
        self.assertEqual(entry["title"], "CSKH")
        self.assertEqual(entry["route"], "/cskh")
        self.assertEqual(entry["logo"], "/assets/crm_cskh/cskh/logo.svg")
        self.assertEqual(
            entry["has_permission"], "crm_cskh.cskh_sidebar.has_app_permission"
        )

        self.assertEqual(
            getattr(self.hooks_mod, "setup_wizard_requires", None),
            "/assets/crm_cskh/cskh/setup-wizard.js",
        )
        self.assertEqual(
            getattr(self.hooks_mod, "app_include_css", None),
            "/assets/crm_cskh/cskh/desk-theme.css",
        )
        self.assertEqual(
            getattr(self.hooks_mod, "web_include_css", None),
            "/assets/crm_cskh/cskh/web-theme.css",
        )

        # Preserve G09 Tier 3 N->N-1 rollback compatibility: after_request keeps N-1 hooks
        after_req = getattr(self.hooks_mod, "after_request", [])
        self.assertEqual(
            after_req,
            [
                "crm_cskh.errors.normalize_error_response",
                "crm_cskh.p1_errors.normalize_p1_error_response",
            ],
        )
        self.assertEqual(
            getattr(self.hooks_mod, "before_job", []),
            ["crm_cskh.services.pdf_hooks.ensure_pdf_runtime_hooks"],
        )

        self.assertTrue((APP_DIR / "public" / "cskh" / "logo.svg").is_file())
        self.assertTrue(
            (APP_DIR / "public" / "cskh" / "crm-sidebar-bridge.js").is_file()
        )
        self.assertTrue((APP_DIR / "public" / "cskh" / "crm-theme.css").is_file())
        self.assertTrue((APP_DIR / "public" / "cskh" / "desk-theme.css").is_file())
        self.assertTrue((APP_DIR / "public" / "cskh" / "web-theme.css").is_file())
        self.assertTrue((APP_DIR / "public" / "cskh" / "setup-wizard.js").is_file())

    def test_has_app_permission_enforces_cskh_and_system_roles(self):
        _install_frappe_stub("Guest", ["Sales User"])
        self.sidebar_mod.frappe = sys.modules["frappe"]
        self.assertFalse(self.sidebar_mod.has_app_permission())

        _install_frappe_stub("outsider@example.com", ["Customer"])
        self.sidebar_mod.frappe = sys.modules["frappe"]
        self.assertFalse(self.sidebar_mod.has_app_permission())

        _install_frappe_stub("Administrator", [])
        self.sidebar_mod.frappe = sys.modules["frappe"]
        self.assertTrue(self.sidebar_mod.has_app_permission())

        for allowed_role in (
            "System Manager",
            "Sales User",
            "Sales Manager",
            "CSKH CRM Admin",
            "CSKH BOD Viewer",
        ):
            with self.subTest(role=allowed_role):
                _install_frappe_stub("staff@example.com", [allowed_role])
                self.sidebar_mod.frappe = sys.modules["frappe"]
                self.assertTrue(self.sidebar_mod.has_app_permission())

    def test_normalize_p1_error_response_delegates_crm_html_bridge_and_is_idempotent(
        self,
    ):
        _install_frappe_stub("staff@example.com", ["Sales User"])
        self.sidebar_mod.frappe = sys.modules["frappe"]

        for path in ("/crm", "/crm/leads", "/crm/deals/view/list"):
            with self.subTest(path=path):
                resp = _DummyResponse(SAMPLE_CRM_HTML.encode("utf-8"))
                req = _DummyRequest(path=path, method="GET")
                self.p1_errors_mod.normalize_p1_error_response(resp, req)
                html_text = (
                    resp.data
                    if isinstance(resp.data, str)
                    else resp.data.decode("utf-8")
                )
                self.assertIn("/assets/crm_cskh/cskh/crm-theme.css?v=", html_text)
                self.assertIn('data-cskh-crm-theme="1"', html_text)
                self.assertLess(
                    html_text.index("crm-theme.css"), html_text.index("</head>")
                )
                self.assertIn(
                    "/assets/crm_cskh/cskh/crm-sidebar-bridge.js?v=", html_text
                )
                self.assertIn('data-cskh-href="/cskh"', html_text)
                self.assertIn('data-cskh-label="CSKH"', html_text)
                self.assertEqual(html_text.count("crm-theme.css"), 1)
                self.assertEqual(html_text.count("crm-sidebar-bridge.js"), 1)

                # Second pass must be a strict no-op (no duplicate link/script tags)
                self.p1_errors_mod.normalize_p1_error_response(resp, req)
                html_after = (
                    resp.data
                    if isinstance(resp.data, str)
                    else resp.data.decode("utf-8")
                )
                self.assertEqual(html_after.count("crm-theme.css"), 1)
                self.assertEqual(html_after.count("crm-sidebar-bridge.js"), 1)

    def test_skips_guest_non_crm_routes_post_requests_and_json_responses(self):
        # 1. Guest on /crm
        _install_frappe_stub("Guest", [])
        self.sidebar_mod.frappe = sys.modules["frappe"]
        guest_resp = _DummyResponse(SAMPLE_CRM_HTML)
        self.p1_errors_mod.normalize_p1_error_response(
            guest_resp, _DummyRequest("/crm", "GET")
        )
        self.assertNotIn("crm-theme.css", guest_resp.data)
        self.assertNotIn("crm-sidebar-bridge.js", guest_resp.data)

        # 2. Authorized user on /cskh, /login, /crm-form/lead, POST /crm, JSON, 403
        _install_frappe_stub("admin@example.com", ["CSKH CRM Admin"])
        self.sidebar_mod.frappe = sys.modules["frappe"]

        for req, resp in (
            (_DummyRequest("/cskh", "GET"), _DummyResponse(SAMPLE_CRM_HTML)),
            (_DummyRequest("/login", "GET"), _DummyResponse(SAMPLE_CRM_HTML)),
            (_DummyRequest("/crm-form/lead", "GET"), _DummyResponse(SAMPLE_CRM_HTML)),
            (_DummyRequest("/crm", "POST"), _DummyResponse(SAMPLE_CRM_HTML)),
            (
                _DummyRequest("/crm", "GET"),
                _DummyResponse('{"ok":true}', mimetype="application/json"),
            ),
            (
                _DummyRequest("/crm", "GET"),
                _DummyResponse(SAMPLE_CRM_HTML, status_code=403),
            ),
        ):
            self.p1_errors_mod.normalize_p1_error_response(resp, req)
            text = (
                resp.data if isinstance(resp.data, str) else resp.data.decode("utf-8")
            )
            self.assertNotIn("crm-theme.css", text)
            self.assertNotIn("crm-sidebar-bridge.js", text)


if __name__ == "__main__":
    unittest.main()
