"""Unit tests for `/crm` sidebar bridge injection and App Switcher registration."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh"


def _install_frappe_stub(user: str = "Guest", roles: list[str] | None = None):
    stub = types.ModuleType("frappe")
    stub.session = types.SimpleNamespace(user=user)
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
        _install_frappe_stub("Guest", [])
        self.sidebar_mod = _load_module(
            "crm_cskh.cskh_sidebar", APP_DIR / "cskh_sidebar.py"
        )
        self.hooks_mod = _load_module("crm_cskh.hooks", APP_DIR / "hooks.py")

    def tearDown(self):
        if self._prev_frappe is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = self._prev_frappe

    def test_hooks_register_apps_screen_and_after_request_bridge(self):
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

        after_req = getattr(self.hooks_mod, "after_request", [])
        self.assertIn("crm_cskh.cskh_sidebar.inject_crm_sidebar_bridge", after_req)

        self.assertTrue((APP_DIR / "public" / "cskh" / "logo.svg").is_file())
        self.assertTrue(
            (APP_DIR / "public" / "cskh" / "crm-sidebar-bridge.js").is_file()
        )

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

    def test_injects_bridge_script_for_authorized_crm_spa_html_and_is_idempotent(self):
        _install_frappe_stub("staff@example.com", ["Sales User"])
        self.sidebar_mod.frappe = sys.modules["frappe"]

        for path in ("/crm", "/crm/leads", "/crm/deals/view/list"):
            with self.subTest(path=path):
                resp = _DummyResponse(SAMPLE_CRM_HTML.encode("utf-8"))
                req = _DummyRequest(path=path, method="GET")
                self.sidebar_mod.inject_crm_sidebar_bridge(resp, req)
                html_text = (
                    resp.data
                    if isinstance(resp.data, str)
                    else resp.data.decode("utf-8")
                )
                self.assertIn(
                    "/assets/crm_cskh/cskh/crm-sidebar-bridge.js?v=", html_text
                )
                self.assertIn('data-cskh-href="/cskh"', html_text)
                self.assertIn('data-cskh-label="CSKH"', html_text)
                self.assertEqual(html_text.count("crm-sidebar-bridge.js"), 1)

                # Second pass must be a strict no-op (no duplicate script tags)
                self.sidebar_mod.inject_crm_sidebar_bridge(resp, req)
                html_after = (
                    resp.data
                    if isinstance(resp.data, str)
                    else resp.data.decode("utf-8")
                )
                self.assertEqual(html_after.count("crm-sidebar-bridge.js"), 1)

    def test_skips_guest_non_crm_routes_post_requests_and_json_responses(self):
        # 1. Guest on /crm
        _install_frappe_stub("Guest", [])
        self.sidebar_mod.frappe = sys.modules["frappe"]
        guest_resp = _DummyResponse(SAMPLE_CRM_HTML)
        self.sidebar_mod.inject_crm_sidebar_bridge(
            guest_resp, _DummyRequest("/crm", "GET")
        )
        self.assertNotIn("crm-sidebar-bridge.js", guest_resp.data)

        # 2. Authorized user on /cskh, /login, /api/method/ping, POST /crm, JSON, 403
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
            self.sidebar_mod.inject_crm_sidebar_bridge(resp, req)
            text = (
                resp.data if isinstance(resp.data, str) else resp.data.decode("utf-8")
            )
            self.assertNotIn("crm-sidebar-bridge.js", text)


if __name__ == "__main__":
    unittest.main()
