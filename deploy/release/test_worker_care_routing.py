"""Host regressions for worker-care entry routes; no Frappe services are required."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "apps" / "crm_cskh" / "crm_cskh"
spec = importlib.util.spec_from_file_location(
    "worker_care_sidebar_test_helpers",
    REPO_ROOT / "deploy/dev/test_cskh_sidebar_hook.py",
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


class WorkerCareRoutingTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = {
            key: sys.modules.get(key)
            for key in ("frappe", "crm_cskh.cskh_sidebar", "crm_cskh.hooks")
        }
        helpers._install_frappe_stub("staff@example.test", ["Sales User"])
        self.sidebar = helpers._load_module(
            "crm_cskh.cskh_sidebar", APP_DIR / "cskh_sidebar.py"
        )

    def tearDown(self):
        for key, value in self.saved_modules.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    def adapt(
        self,
        path="/crm",
        *,
        user="staff@example.test",
        roles=None,
        method="GET",
        headers=None,
        args=None,
        status=200,
        mimetype="text/html",
        body=None,
    ):
        self.sidebar.frappe = helpers._install_frappe_stub(
            user, ["Sales User"] if roles is None else roles
        )
        request = SimpleNamespace(
            path=path, method=method, headers=headers or {}, args=args or {}
        )
        response = helpers._DummyResponse(
            helpers.SAMPLE_CRM_HTML if body is None else body,
            status_code=status,
            mimetype=mimetype,
        )
        self.sidebar.inject_crm_sidebar_bridge(response, request)
        return response

    def test_sales_routes_redirect_for_each_authorized_role_and_administrator(self):
        for role in (
            "Sales User",
            "Sales Manager",
            "CSKH CRM Admin",
            "CSKH BOD Viewer",
            "System Manager",
        ):
            for path in (
                "/crm",
                "/crm/",
                "/crm/dashboard",
                "/crm/dashboard/",
                "/crm/deals",
                "/crm/deals/DEAL-001",
                "/crm/deals/view/list/",
                "/crm/forecast",
                "/crm/forecasts/",
                "/crm/revenue/by-month",
                "/crm/data-import/doctype/CRM Deal",
                "/crm/data-import/doctype/CRM%20Deal",
            ):
                with self.subTest(role=role, path=path):
                    response = self.adapt(path, roles=[role])
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], "/cskh")
                    self.assertEqual(
                        response.headers["Cache-Control"], "private, no-store"
                    )
                    self.assertEqual(response.data, "")
        self.assertEqual(self.adapt(user="Administrator", roles=[]).status_code, 302)

    def test_guest_outsider_and_role_lookup_failure_keep_native_behavior(self):
        for user, roles in (
            ("Guest", ["Sales User"]),
            ("outsider@example.test", ["Customer"]),
            ("outsider@example.test", []),
        ):
            with self.subTest(user=user, roles=roles):
                response = self.adapt(user=user, roles=roles)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, helpers.SAMPLE_CRM_HTML)
                self.assertNotIn("Location", response.headers)
        self.sidebar.frappe.get_roles = lambda _user: (_ for _ in ()).throw(
            RuntimeError("offline")
        )
        self.assertFalse(self.sidebar.has_app_permission("staff@example.test"))

    def test_errors_login_redirects_api_mutations_and_non_documents_are_preserved(self):
        cases = (
            [
                {"status": status}
                for status in (301, 302, 303, 307, 308, 400, 401, 403, 404, 500)
            ]
            + [
                {"method": method}
                for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
            ]
            + [
                {"mimetype": "application/json"},
                {"mimetype": "text/plain"},
                {"path": "/api/method/crm.api.dashboard.get_won_deals"},
                {"path": "/api/resource/CRM Deal"},
                {"path": "/login"},
                {"headers": {"X-Requested-With": "XMLHttpRequest"}},
                {"headers": {"Accept": "application/json"}},
                {"headers": {"Sec-Fetch-Dest": "empty"}},
                {"args": {"cmd": "crm.api.dashboard.get_won_deals"}},
                {"body": "<html><body>login required</body></html>"},
            ]
        )
        for case in cases:
            with self.subTest(case=case):
                response = self.adapt(**case)
                self.assertEqual(response.status_code, case.get("status", 200))
                self.assertNotIn("Location", response.headers)
                self.assertNotIn("crm-sidebar-bridge.js", response.data)

    def test_helpers_remain_available_and_guard_runs_before_native_module(self):
        for path in (
            "/crm/leads",
            "/crm/leads/LEAD-001",
            "/crm/tasks",
            "/crm/call-logs",
            "/crm/contacts",
            "/crm/organizations",
            "/crm/data-import",
            "/crm/data-import/doctype/CRM Lead",
        ):
            with self.subTest(path=path):
                response = self.adapt(
                    path, headers={"Accept": "text/html", "Sec-Fetch-Dest": "document"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Location", response.headers)
                self.assertLess(
                    response.data.index("crm-sidebar-bridge.js"),
                    response.data.index('type="module"'),
                )
                self.assertNotIn("<script defer", response.data)

    def test_query_targets_external_destinations_and_lookalike_paths_do_not_change_policy(
        self,
    ):
        response = self.adapt(
            args={"redirect": "https://attacker.test", "next": "/crm/leads"}
        )
        self.assertEqual(response.headers["Location"], "/cskh")
        for path in (
            "/crm/leads",
            "/crm/deals-archive",
            "/crm/dashboard-tools",
            "/crm-form/deal",
            "//crm/deals",
            "https://attacker.test/crm/deals",
            "/crm/%64eals",
            "/crm/leads?next=/crm/deals",
            "/crm\\deals",
        ):
            with self.subTest(path=path):
                response = self.adapt(
                    path,
                    args={"next": "/crm/deals", "redirect": "https://attacker.test"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Location", response.headers)

    def test_app_switcher_and_role_home_pages_point_to_worker_care(self):
        hooks = helpers._load_module("crm_cskh.hooks", APP_DIR / "hooks.py")
        self.assertEqual(hooks.app_title, "Worker care")
        self.assertEqual(hooks.add_to_apps_screen[0]["title"], "Worker care")
        self.assertEqual(hooks.add_to_apps_screen[0]["route"], "/cskh")
        self.assertNotIn("Guest", hooks.role_home_page)
        self.assertEqual(set(hooks.role_home_page.values()), {"cskh"})

    def test_dev_capability_contains_only_routing_data_and_obeys_scope(self):
        for user, roles, expected in (
            ("Guest", ["Sales User"], False),
            ("outsider@example.test", ["Customer"], False),
            ("staff@example.test", ["Sales User"], True),
            ("Administrator", [], True),
        ):
            with self.subTest(user=user):
                self.sidebar.frappe = helpers._install_frappe_stub(user, roles)
                capability = self.sidebar.worker_care_entry()
                self.assertEqual(
                    capability,
                    {
                        "enabled": expected,
                        "href": "/cskh",
                        "label": "Chăm sóc người lao động",
                    },
                )

    def test_browserless_native_navigation_regressions(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the navigation contract suite")
        result = subprocess.run(
            [
                node,
                "--test",
                str(REPO_ROOT / "deploy/dev/test_worker_care_navigation.mjs"),
            ],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
