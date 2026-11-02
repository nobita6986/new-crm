#!/usr/bin/env python3
"""Test cho `p1_test_inventory.py` — chạy hoàn toàn cục bộ, không cần bench/site.

Vì sao cần test cho script sinh inventory
------------------------------------------
Inventory của P1 là **sinh từ source** (không viết tay). Nếu script hỏng, nó có
thể sinh ra inventory rỗng hoặc thiếu method mà **không ai nhận ra** ⇒ gate
per-method sẽ "PASS" với bề mặt kiểm thử đã thu nhỏ. Test ở đây chứng minh:

1. Mỗi class `test_*` trong một file test được liệt kê với đúng các method.
2. Một method không bắt đầu bằng `test` **không** bị tính.
3. File không phải `test_p1_*` **không** bị đưa vào inventory.
4. `--check` FAIL khi source lệch inventory (fail-closed).
5. `--check` PASS khi source khớp inventory.
6. Class không có method `test` nào bị bỏ qua.

Chạy:
    python3 deploy/dev/seed/test_p1_test_inventory.py
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "p1_test_inventory.py"


def load_module():
    spec = importlib.util.spec_from_file_location("p1_test_inventory", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inventory_module = load_module()


SAMPLE_TEST = '''
"""Sample."""


class TestAlpha:
	def test_one(self):
		pass

	def test_two(self):
		pass

	def helper(self):
		pass


class TestBeta:
	def test_three(self):
		pass


class TestNoTests:
	def helper(self):
		pass
'''

SAMPLE_OTHER = """
class TestGamma:
	def test_four(self):
		pass
"""


class TestInventoryGeneration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tests_dir = Path(self._tmp.name)
        (self.tests_dir / "test_p1_alpha.py").write_text(SAMPLE_TEST, encoding="utf-8")
        (self.tests_dir / "test_p1_beta.py").write_text(SAMPLE_OTHER, encoding="utf-8")
        # Không phải file P1 => không được vào inventory.
        (self.tests_dir / "test_p0_other.py").write_text(SAMPLE_OTHER, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_only_p1_modules_are_collected(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        self.assertEqual(
            sorted(inventory["modules"]),
            ["crm_cskh.tests.test_p1_alpha", "crm_cskh.tests.test_p1_beta"],
        )

    def test_every_test_method_is_listed(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        self.assertEqual(
            inventory["modules"]["crm_cskh.tests.test_p1_alpha"],
            ["TestAlpha.test_one", "TestAlpha.test_two", "TestBeta.test_three"],
        )

    def test_non_test_methods_are_ignored(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        alpha = inventory["modules"]["crm_cskh.tests.test_p1_alpha"]
        self.assertNotIn("TestAlpha.helper", alpha)
        self.assertNotIn("TestNoTests.test_anything", alpha)

    def test_a_class_without_test_methods_is_dropped(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        self.assertNotIn(
            "TestNoTests", inventory["modules"]["crm_cskh.tests.test_p1_alpha"]
        )

    def test_expected_total_matches_the_method_count(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        self.assertEqual(inventory["expected_total"], 4)

    def test_check_fails_when_the_source_drifts(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        # Xoá một method khỏi source nhưng giữ inventory cũ.
        (self.tests_dir / "test_p1_alpha.py").write_text(
            SAMPLE_TEST.replace("\tdef test_two(self):\n\t\tpass\n", ""),
            encoding="utf-8",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
            argv = ["--tests-dir", str(self.tests_dir), "--check", str(path)]
            self.assertNotEqual(inventory_module.main(argv), 0)

    def test_check_passes_when_the_source_matches(self):
        inventory = inventory_module.build_inventory(self.tests_dir)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
            argv = ["--tests-dir", str(self.tests_dir), "--check", str(path)]
            self.assertEqual(inventory_module.main(argv), 0)


class TestCommittedInventory(unittest.TestCase):
    """Inventory đã commit phải khớp source thật của app."""

    def test_the_committed_inventory_matches_the_source(self):
        root = Path(__file__).resolve().parents[3]
        committed = root / "deploy/dev/seed/p1_test_inventory.json"
        self.assertTrue(committed.exists(), f"Thiếu inventory: {committed}")
        argv = ["--check", str(committed)]
        self.assertEqual(inventory_module.main(argv), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
