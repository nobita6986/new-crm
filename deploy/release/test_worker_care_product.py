"""Upgrade and navigation regressions for the worker-care product boundary."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "crm_cskh" / "crm_cskh"


class WorkerCareProductTests(unittest.TestCase):
    def _migration(self, present: bool):
        rows = {"CSKH - Open CSKH Page from Deal": 1} if present else {}
        writes = []

        def set_value(doctype, name, field, value):
            self.assertEqual(doctype, "CRM Form Script")
            self.assertEqual(field, "enabled")
            rows[name] = value
            writes.append((name, value))

        stub = types.ModuleType("frappe")
        stub.db = types.SimpleNamespace(
            exists=lambda doctype, name: name in rows,
            set_value=set_value,
        )
        clears = []
        stub.clear_cache = lambda: clears.append(True)
        spec = importlib.util.spec_from_file_location(
            "worker_care_workspace_patch",
            APP / "patches" / "v4_0" / "worker_care_workspace.py",
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": stub}):
            spec.loader.exec_module(module)
            module.execute()
            module.execute()
        return rows, writes, clears

    def test_upgrade_disables_app_owned_deal_action_and_is_repeatable(self):
        rows, writes, clears = self._migration(True)
        self.assertEqual(rows, {"CSKH - Open CSKH Page from Deal": 0})
        self.assertEqual(len(writes), 2)
        self.assertEqual(len(clears), 2)

    def test_upgrade_handles_site_without_old_action(self):
        rows, writes, clears = self._migration(False)
        self.assertEqual(rows, {})
        self.assertEqual(writes, [])
        self.assertEqual(len(clears), 2)

    def test_fresh_install_also_disables_legacy_deal_shortcut(self):
        scripts = json.loads(
            (APP / "fixtures" / "crm_form_script.json").read_text(encoding="utf-8")
        )
        owned_action = next(
            row for row in scripts if row["name"] == "CSKH - Open CSKH Page from Deal"
        )
        self.assertEqual(owned_action["enabled"], 0)
        self.assertEqual(owned_action["dt"], "CRM Deal")

    def test_worker_portal_never_links_back_to_sales_workflows(self):
        files = list((APP / "www" / "cskh").glob("*.html"))
        files.extend((APP / "templates" / "cskh").glob("*.html"))
        pattern = re.compile(
            r"href\s*=\s*['\"]/crm/(?:deals?|dashboard|forecasts?|revenue)(?:[/'\"?])",
            re.I,
        )
        for path in files:
            with self.subTest(template=path.name):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))

    def test_vietnamese_translation_audit_and_worker_care_vocabulary_sync(self):
        audit_spec = importlib.util.spec_from_file_location(
            "check_translations_audit_gate",
            ROOT / "deploy" / "p1" / "check_translations_audit.py",
        )
        audit_mod = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(audit_mod)
        self.assertEqual(audit_mod.main(), 0)

        rows = audit_mod.translation_rows()
        self.assertIn("Keep today's contact promises", rows)
        self.assertEqual(rows["Customer"], "Người lao động")
        self.assertEqual(rows["Care workspace"], "Hồ sơ chăm sóc")
        self.assertEqual(rows["Sales User"], "Nhân viên marketing")
        self.assertEqual(rows["Sales Manager"], "Trưởng nhóm marketing")
        self.assertEqual(rows["Campaign"], "Chiến dịch chăm sóc")
        self.assertEqual(rows["Callbacks schedule"], "Lịch hẹn chăm sóc")
        self.assertEqual(rows["Contact outcome"], "Kết quả liên hệ")
        self.assertNotIn(
            "/crm/deals",
            rows.get(
                "Important: A connected contact record only confirms two-way communication — it does not mean a candidate has been hired or placed. Native CRM Deals (/crm/deals) remain separate commercial/CRM transactions.",
                "",
            ),
        )


if __name__ == "__main__":
    unittest.main()
