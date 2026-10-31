#!/usr/bin/env python3
"""Test cho `ci_junit_gate.py` (T1B) — chạy hoàn toàn cục bộ, không cần bench/site.

Vì sao cần test cho chính cái gate
-----------------------------------
T1C lần 17 phát hiện gate per-method có thể PASS khi:
  - 99/99 testcase đều `<skipped/>` (SKIP bị bỏ qua),
  - XML hỏng / parse error bị bỏ qua,
  - case ngoài inventory không làm fail,
  - định danh chỉ theo `(class, method)` nên XML của module A thoả được module B,
  - chạy lặp / XML trùng làm PASS count tăng so với số method duy nhất.

Test ở đây **sinh XML thật** bằng `unittest` + `xmlrunner` cho các fixture
PASS / FAIL / ERROR / SKIP / subTest-có-lỗi, rồi chứng minh gate trả exit ≠ 0
cho từng failure mode, và chứng minh XML của module A **không** làm tăng PASS
của module B.

Lưu ý về fixture: `xmlrunner` ghi `classname` là tên class **runtime**, nên mọi
biến thể kết quả (pass/fail/error/skip) được điều khiển bằng biến module-level
`_OUTCOME` thay vì subclass — subclass sẽ đổi `classname` trong XML và không
còn khớp inventory (đây chính là hành vi đúng của gate).

Chạy:
    python3 deploy/dev/seed/test_ci_junit_gate.py
"""

from __future__ import annotations

import importlib.util
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GATE_PATH = Path(__file__).resolve().parent / "ci_junit_gate.py"

_spec = importlib.util.spec_from_file_location("ci_junit_gate", GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

try:
	import xmlrunner
except ImportError:  # pragma: no cover - môi trường thiếu dependency
	xmlrunner = None

MODULE_A = "crm_cskh.tests.test_permissions"
MODULE_B = "crm_cskh.tests.test_security_remediation"
MODULE_C = "crm_cskh.tests.test_security_remediation_v2"

SLUG_A = "test_permissions"
SLUG_B = "test_security_remediation"
SLUG_C = "test_security_remediation_v2"

# Điều khiển kết quả runtime mà không đổi tên class trong XML.
_OUTCOME = {"mode": "pass"}


# ---------------------------------------------------------------------------
# Fixture module A — 4 method, tất cả PASS.
# ---------------------------------------------------------------------------
class FixtureModuleA(unittest.TestCase):
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False

	# `__test__ = False`: đây là **fixture** sinh XML, không phải test của harness.
	# `unittest.main()` mặc định sẽ discovery các class ở module scope và coi các
	# FAIL/ERROR/skip cố ý trong fixture là hỏng test thật.
	__test__ = False

	def test_alpha_pass(self):
		self.assertTrue(True)

	def test_beta_pass(self):
		self.assertEqual(1, 1)

	def test_gamma_pass(self):
		self.assertIn("a", "abc")

	def test_delta_pass(self):
		self.assertIsInstance(self, FixtureModuleA)


# ---------------------------------------------------------------------------
# Fixture module B — 3 method, kết quả theo `_OUTCOME["mode"]`.
# ---------------------------------------------------------------------------
class FixtureModuleBOutcomes(unittest.TestCase):
	"""Module B — 3 method. `test_one` nhận outcome theo `_OUTCOME`."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_one(self):
		mode = _OUTCOME["mode"]
		if mode == "fail":
			self.fail("intentional failure")
		if mode == "error":
			raise RuntimeError("intentional error")
		if mode == "skip":
			self.skipTest("intentional skip")
		self.assertTrue(True)

	def test_two(self):
		self.assertTrue(True)

	def test_three_subtests(self):
		for value in (1, 2, 3):
			with self.subTest(value=value):
				self.assertLess(value, 5)


class FixtureSubTestFailure(unittest.TestCase):
	"""subTest fail — gate phải quy về method gốc và giữ FAIL (không phải PASS)."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_subtest_grouping(self):
		for index in range(3):
			with self.subTest(index=index):
				self.assertLess(index, 2)  # index==2 fail


class FixtureSubTestError(unittest.TestCase):
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False

	def test_subtest_error_grouping(self):
		for index in range(2):
			with self.subTest(index=index):
				if index == 1:
					raise ValueError("subtest boom")


class FixtureSubTestSkip(unittest.TestCase):
	"""subTest bị skip — method gốc phải là SKIP, không phải PASS."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_subtest_skipping(self):
		for value in (1,):
			with self.subTest(value=value):
				self.skipTest("intentional subtest skip")


class FixtureSubTestPassOnly(unittest.TestCase):
	"""subTest pass thuần — không có method khác trong class để tránh
	unexpected case khi gate chỉ khai báo một method."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_only_subtests(self):
		for value in (1, 2, 3):
			with self.subTest(value=value):
				self.assertLess(value, 5)


class FixtureUnexpectedMethod(unittest.TestCase):
	"""Method ngoài inventory — phải thành unexpected_case và làm gate fail."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_one(self):
		self.assertTrue(True)

	def test_two(self):
		self.assertTrue(True)


class FixtureSingleMethodA(unittest.TestCase):
	"""Chỉ 1 method — dùng khi inventory chỉ khai báo `test_alpha_pass`."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_alpha_pass(self):
		self.assertTrue(True)


class FixtureModuleC(unittest.TestCase):
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False

	def test_v2_only(self):
		self.assertTrue(True)


class FixtureSharedNamespace(unittest.TestCase):
	"""Class dùng chung tên ở nhiều module (không trùng method) — kiểm định
	việc định danh phải khoá theo module, không chỉ theo class+method."""
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False


	def test_other_method(self):
		"""Method chỉ tồn tại ở inventory module A."""
		self.assertTrue(True)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def run_gate(
	xml_dir: Path, inventory: dict, allow_unexpected: list[str] | None = None
) -> tuple[int, dict]:
	"""Chạy gate như subprocess ⇒ kiểm đúng exit code + artifact thật."""
	with tempfile.TemporaryDirectory() as tmp:
		inv_path = Path(tmp) / "inventory.json"
		inv_path.write_text(json.dumps(inventory), encoding="utf-8")
		out_path = Path(tmp) / "gate.json"
		cmd = [
			sys.executable,
			str(GATE_PATH),
			"--inventory",
			str(inv_path),
			"--xml-dir",
			str(xml_dir),
			"--out",
			str(out_path),
		]
		for entry in allow_unexpected or []:
			cmd += ["--allow-unexpected", entry]
		proc = subprocess.run(
			cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
		)
		report = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
		return proc.returncode, report


def generate_real_xml(target: Path, test_classes: list[type], label: str) -> Path:
	"""Sinh JUnit XML **thật** bằng `unittest` + `xmlrunner`.

	Xuất vào `io.BytesIO` rồi tự ghi file: nếu cho `XMLTestRunner` mở file trực
	tiếp, handle vẫn còn khoá sau `run()` trên Windows và gate đọc sẽ nhận
	PermissionDenied thay vì nội dung XML. `xmlrunner` ghi **bytes** nên buffer
	phải là `BytesIO` (`StringIO` sẽ ném TypeError).
	"""
	assert xmlrunner is not None, "cần cài xmlrunner để sinh XML thật"
	target.mkdir(parents=True, exist_ok=True)
	xml_path = target / f"{label}.junit.xml"

	buffer = io.BytesIO()
	loader = unittest.TestLoader()
	suite = unittest.TestSuite([loader.loadTestsFromTestCase(cls) for cls in test_classes])
	runner = xmlrunner.XMLTestRunner(output=buffer, verbosity=0, stream=io.StringIO())
	result = runner.run(suite)
	assert result is not None
	xml_path.write_bytes(buffer.getvalue())
	return xml_path


def write_raw_xml(target: Path, label: str, content: str) -> Path:
	target.mkdir(parents=True, exist_ok=True)
	xml_path = target / f"{label}.junit.xml"
	xml_path.write_text(content, encoding="utf-8")
	return xml_path


def case_xml(module: str, class_name: str, name: str, inner: str = "") -> str:
	return (
		f'<testcase classname="{module}.{class_name}" name="{name}" time="0.01">'
		f"{inner}</testcase>"
	)


class GateTestBase(unittest.TestCase):
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False

	def setUp(self):
		self.tmp = Path(tempfile.mkdtemp(prefix="gate-test-"))
		self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
		_OUTCOME["mode"] = "pass"

	def inventory(self, modules: dict[str, list[str]]) -> dict:
		return {
			"schema_version": 1,
			"expected_total": sum(len(v) for v in modules.values()),
			"modules": modules,
		}

	def inventory_module_a(self) -> dict:
		return self.inventory(
			{
				MODULE_A: [
					"FixtureModuleA.test_alpha_pass",
					"FixtureModuleA.test_beta_pass",
					"FixtureModuleA.test_gamma_pass",
					"FixtureModuleA.test_delta_pass",
				]
			}
		)

	def inventory_both(self) -> dict:
		inv = self.inventory_module_a()
		inv["modules"][MODULE_B] = [
			"FixtureModuleBOutcomes.test_one",
			"FixtureModuleBOutcomes.test_two",
			"FixtureModuleBOutcomes.test_three_subtests",
		]
		return inv


# ===========================================================================
# 1. Baseline: XML đầy đủ, mọi method PASS ⇒ gate PASS
# ===========================================================================
class TestGateHappyPath(GateTestBase):
	def test_all_pass_gate_passes(self):
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		generate_real_xml(self.tmp, [FixtureModuleBOutcomes], SLUG_B)
		code, report = run_gate(self.tmp, self.inventory_both())
		self.assertEqual(code, 0, f"gate phải PASS: {report}")
		self.assertTrue(report["gate_passed"])
		self.assertEqual(report["passing_total"], 7)
		self.assertEqual(report["expected_total"], 7)
		self.assertEqual(report["counts"], {"PASS": 7})
		self.assertEqual(report["unexpected_cases"], [])
		self.assertEqual(report["parse_errors"], [])
		self.assertEqual(report["missing_methods"], [])

	def test_single_module_all_pass(self):
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		code, report = run_gate(self.tmp, self.inventory_module_a())
		self.assertEqual(code, 0, f"gate phải PASS: {report}")
		self.assertEqual(report["passing_total"], 4)


# ===========================================================================
# 2. A1 — SKIP phải làm gate fail (100% SKIP không được coi là PASS)
# ===========================================================================
class TestGateSkipFails(GateTestBase):
	def test_single_skip_fails_gate(self):
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		_OUTCOME["mode"] = "skip"
		generate_real_xml(self.tmp, [FixtureModuleBOutcomes], SLUG_B)
		_OUTCOME["mode"] = "pass"
		code, report = run_gate(self.tmp, self.inventory_both())
		self.assertEqual(code, 1, "1 testcase SKIP phải làm gate fail")
		self.assertFalse(report["gate_passed"])
		self.assertEqual(len(report["skipped_methods"]), 1)
		self.assertEqual(report["counts"].get("PASS"), 6)
		self.assertEqual(report["counts"].get("SKIP"), 1)

	def test_all_skip_fails_gate(self):
		"""Toàn bộ method SKIP (tương đương 99/99 SKIP) ⇒ chắc chắn fail."""
		content = (
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<testsuites><testsuite name="all-skip" tests="2">'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass", "<skipped/>")
			+ case_xml(MODULE_A, "FixtureModuleA", "test_beta_pass", "<skipped/>")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory(
			{
				MODULE_A: [
					"FixtureModuleA.test_alpha_pass",
					"FixtureModuleA.test_beta_pass",
				]
			}
		)
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "100% SKIP không được coi là PASS")
		self.assertEqual(report["counts"].get("SKIP"), 2)
		self.assertEqual(report["passing_total"], 0)
		self.assertEqual(report["expected_total"], 2)


# ===========================================================================
# 3. A2/A5 — FAIL / ERROR / XML hỏng / thiếu XML / cấu trúc sai phải fail
# ===========================================================================
class TestGateFailureModes(GateTestBase):
	def test_fail_case_fails_gate(self):
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		_OUTCOME["mode"] = "fail"
		generate_real_xml(self.tmp, [FixtureModuleBOutcomes], SLUG_B)
		_OUTCOME["mode"] = "pass"
		code, report = run_gate(self.tmp, self.inventory_both())
		self.assertEqual(code, 1, "testcase FAIL phải làm gate fail")
		self.assertEqual(len(report["failed_methods"]), 1)
		self.assertEqual(report["counts"].get("FAIL"), 1)

	def test_error_case_fails_gate(self):
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		_OUTCOME["mode"] = "error"
		generate_real_xml(self.tmp, [FixtureModuleBOutcomes], SLUG_B)
		_OUTCOME["mode"] = "pass"
		code, report = run_gate(self.tmp, self.inventory_both())
		self.assertEqual(code, 1, "testcase ERROR phải làm gate fail")
		self.assertEqual(len(report["errored_methods"]), 1)
		self.assertEqual(report["counts"].get("ERROR"), 1)

	def test_corrupt_xml_fails_gate(self):
		"""XML hỏng ⇒ parse error ⇒ fail, KHÔNG được bỏ qua rồi tính PASS phần còn lại."""
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		write_raw_xml(self.tmp, SLUG_B, "<testsuite><testcase name='x'  <<<BROKEN>>>")
		code, report = run_gate(self.tmp, self.inventory_both())
		self.assertEqual(code, 1, "XML hỏng phải làm gate fail")
		self.assertFalse(report["gate_passed"])
		self.assertTrue(report["parse_errors"], "parse_errors phải được ghi nhận")
		self.assertTrue(any("không parse được" in item for item in report["parse_errors"]))
		# Module B vẫn phải NOT_RUN, không được coi module A đã đủ.
		self.assertEqual(report["passing_total"], 4)

	def test_missing_xml_for_module_fails_gate(self):
		"""Thiếu file XML cho 1 module ⇒ method của module đó NOT_RUN ⇒ fail."""
		generate_real_xml(self.tmp, [FixtureModuleA], SLUG_A)
		code, report = run_gate(self.tmp, self.inventory_both())
		self.assertEqual(code, 1, "thiếu XML cho module B phải làm gate fail")
		self.assertIn(MODULE_B, report["missing_modules"])
		self.assertEqual(len(report["missing_methods"]), 3)
		self.assertTrue(any("Không có file XML nào cho module" in i for i in report["parse_errors"]))

	def test_missing_xml_directory_fails_gate(self):
		empty = self.tmp / "empty-dir"
		empty.mkdir()
		code, report = run_gate(empty, self.inventory_module_a())
		self.assertEqual(code, 1, "thư mục không có XML nào phải fail")
		self.assertTrue(report["parse_errors"])

	def test_wrong_root_tag_fails_gate(self):
		write_raw_xml(self.tmp, SLUG_A, "<html><body>not junit</body></html>")
		code, report = run_gate(self.tmp, self.inventory_module_a())
		self.assertEqual(code, 1)
		self.assertTrue(any("root tag" in item for item in report["parse_errors"]))

	def test_testcase_missing_name_fails_gate(self):
		content = (
			'<testsuites><testsuite>'
			f'<testcase classname="{MODULE_A}.FixtureModuleA" time="0.01"/>'
			"</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1)
		self.assertTrue(any("thiếu thuộc tính name" in item for item in report["parse_errors"]))

	def test_testcase_missing_classname_fails_gate(self):
		content = '<testsuites><testsuite><testcase name="test_alpha_pass"/></testsuite></testsuites>'
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1)
		self.assertTrue(any("thiếu thuộc tính classname" in item for item in report["parse_errors"]))

	def test_empty_testsuite_fails_gate(self):
		"""XML có suite nhưng không có testcase nào ⇒ không được coi là đạt."""
		write_raw_xml(self.tmp, SLUG_A, '<testsuites><testsuite tests="0"/></testsuites>')
		code, report = run_gate(self.tmp, self.inventory_module_a())
		self.assertEqual(code, 1)
		self.assertIn(SLUG_A + ".junit.xml", report["empty_xml_files"])


# ===========================================================================
# 4. A3 — testcase ngoài inventory (unexpected) phải fail; allowlist thì được
# ===========================================================================
class TestGateUnexpectedCases(GateTestBase):
	def test_unexpected_case_fails_gate(self):
		generate_real_xml(self.tmp, [FixtureUnexpectedMethod], SLUG_B)
		inv = self.inventory({MODULE_B: ["FixtureUnexpectedMethod.test_one"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "testcase ngoài inventory phải làm gate fail")
		self.assertEqual(len(report["unexpected_cases"]), 1)
		self.assertEqual(report["unexpected_cases"][0]["allowed"], "no")
		self.assertEqual(report["unexpected_cases"][0]["raw_name"], "test_two")
		self.assertTrue(any("ngoài inventory" in i for i in report["parse_errors"]))

	def test_unmatched_method_name_is_unexpected(self):
		"""Method không khớp tên nào trong inventory ⇒ unexpected (không bị bỏ qua)."""
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ case_xml(MODULE_A, "FixtureModuleA", "test_completely_different_name")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1)
		self.assertEqual(len(report["unexpected_cases"]), 1)
		self.assertEqual(report["passing_total"], 1)

	def test_allowlist_makes_unexpected_accepted(self):
		"""Allowlist cụ thể được giải thích ⇒ case đó không còn làm gate fail."""
		generate_real_xml(self.tmp, [FixtureUnexpectedMethod], SLUG_B)
		inv = self.inventory({MODULE_B: ["FixtureUnexpectedMethod.test_one"]})
		allow = [f"{MODULE_B}::FixtureUnexpectedMethod::test_two"]
		code, report = run_gate(self.tmp, inv, allow_unexpected=allow)
		self.assertEqual(code, 0, f"allowlist hợp lệ phải cho gate PASS: {report}")
		self.assertEqual(len(report["unexpected_cases"]), 1)
		self.assertEqual(report["unexpected_cases"][0]["allowed"], "yes")
		self.assertEqual(report["parse_errors"], [])

	def test_allowlist_must_be_specific(self):
		"""Allowlist không khớp case thật vẫn phải fail."""
		generate_real_xml(self.tmp, [FixtureUnexpectedMethod], SLUG_B)
		inv = self.inventory({MODULE_B: ["FixtureUnexpectedMethod.test_one"]})
		code, report = run_gate(
			self.tmp, inv, allow_unexpected=[f"{MODULE_B}::FixtureUnexpectedMethod::test_other"]
		)
		self.assertEqual(code, 1, "allowlist không khớp case thật vẫn phải fail")
		self.assertEqual(report["unexpected_cases"][0]["allowed"], "no")

	def test_malformed_allowlist_entry_is_rejected(self):
		generate_real_xml(self.tmp, [FixtureUnexpectedMethod], SLUG_B)
		inv = self.inventory({MODULE_B: ["FixtureUnexpectedMethod.test_one"]})
		code, _ = run_gate(self.tmp, inv, allow_unexpected=["only-one-part"])
		self.assertNotEqual(code, 0, "allowlist sai định dạng phải bị từ chối")


# ===========================================================================
# 5. A4 — định danh theo module + class + method
#    Chứng minh XML của module A KHÔNG làm tăng PASS của module B.
# ===========================================================================
class TestGateModuleIdentity(GateTestBase):
	def test_xml_from_module_a_cannot_satisfy_module_b_inventory(self):
		"""XML gắn tên module A nhưng chứa method chỉ có trong inventory module B.

		Đây là biến thể của lỗi T1C: nếu gate chỉ khoá theo (class, method) thì
		case này sẽ được tính PASS cho module B.
		"""
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "SharedClass", "test_module_b_only_method")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory(
			{
				MODULE_A: ["SharedClass.test_module_a_only_method"],
				MODULE_B: ["SharedClass.test_module_b_only_method"],
			}
		)
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "XML module A không được thoả inventory module B")
		b_rows = [row for row in report["results"] if row["module"] == MODULE_B]
		self.assertEqual(len(b_rows), 1)
		self.assertEqual(b_rows[0]["status"], "NOT_RUN")
		self.assertEqual(report["passing_total"], 0, "không module nào được tính PASS")

	def test_xml_from_module_b_cannot_satisfy_module_a_inventory(self):
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_B, "SharedClass", "test_module_a_only_method")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_B, content)
		inv = self.inventory(
			{
				MODULE_A: ["SharedClass.test_module_a_only_method"],
				MODULE_B: ["SharedClass.test_module_b_only_method"],
			}
		)
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "XML module B không được thoả inventory module A")
		self.assertEqual(report["passing_total"], 0)

	def test_same_class_different_methods_are_module_scoped(self):
		"""Cùng tên class ở hai module, mỗi module một method riêng ⇒ mỗi XML
		chỉ được credit cho đúng module của nó."""
		generate_real_xml(self.tmp, [FixtureSharedNamespace], SLUG_A)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_B, "FixtureSharedNamespace", "test_module_b_only_method")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_B, content)
		inv = self.inventory(
			{
				MODULE_A: ["FixtureSharedNamespace.test_other_method"],
				MODULE_B: ["FixtureSharedNamespace.test_module_b_only_method"],
			}
		)
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 0, f"mỗi XML credit đúng module: {report}")
		self.assertEqual(report["passing_total"], 2)

	def test_xml_from_other_module_bound_by_filename_is_structural_error(self):
		"""XML chứa class của module C nhưng file lại gắn tên module A ⇒ fail."""
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_C, "FixtureModuleC", "test_v2_only")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory(
			{
				# Method khác tên ở module A để inventory không bị "mơ hồ"
				# (cùng class+method ở 2 module sẽ bị gate từ chối ngay).
				MODULE_A: ["FixtureModuleC.test_module_a_only"],
				MODULE_C: ["FixtureModuleC.test_v2_only"],
			}
		)
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "sai module giữa file XML và testcase phải fail")
		self.assertTrue(
			any("thuộc module" in item for item in report["parse_errors"]),
			f"phải báo mismatch module: {report['parse_errors']}",
		)
		self.assertEqual(report["passing_total"], 0)

	def test_xml_filename_not_matching_any_module_fails(self):
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, "unknown_module_name", content)
		code, report = run_gate(self.tmp, self.inventory_module_a())
		self.assertEqual(code, 1)
		self.assertTrue(any("không khớp module nào" in item for item in report["parse_errors"]))


# ===========================================================================
# 6. A5/A6 — duplicate, chạy lặp, subTest
# ===========================================================================
class TestGateDuplicates(GateTestBase):
	def test_duplicate_xml_for_same_module_is_structural_error(self):
		"""Hai XML cho cùng một module (re-run) phải là lỗi cấu trúc, không gộp PASS."""
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		(self.tmp / f"{SLUG_A} (rerun).junit.xml").write_text(content, encoding="utf-8")
		code, report = run_gate(self.tmp, self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]}))
		self.assertEqual(code, 1, "duplicate XML cho cùng module phải fail")
		self.assertTrue(any("duplicate XML" in item for item in report["parse_errors"]))
		self.assertEqual(report["passing_total"], 1, "duplicate không làm tăng PASS count")

	def test_repeated_cases_within_one_xml_do_not_inflate_pass_count(self):
		"""Cùng method xuất hiện nhiều lần trong 1 XML ⇒ PASS count vẫn = số method."""
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(report["passing_total"], 1, "3 lần xuất hiện vẫn chỉ 1 method PASS")
		self.assertEqual(report["observed_total"], 1)
		self.assertEqual(report["duplicate_methods"], [f"{MODULE_A}::FixtureModuleA::test_alpha_pass"])

	def test_repeated_pass_cannot_mask_a_failure(self):
		"""Lần PASS không được che lần FAIL của cùng method."""
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass", "<failure message='boom'/>")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "PASS lặp không được che FAIL")
		self.assertEqual(len(report["failed_methods"]), 1)

	def test_repeated_pass_cannot_mask_an_error(self):
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass")
			+ case_xml(MODULE_A, "FixtureModuleA", "test_alpha_pass", "<error message='boom'/>")
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureModuleA.test_alpha_pass"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1)
		self.assertEqual(len(report["errored_methods"]), 1)


class TestGateSubTest(GateTestBase):
	def test_subtest_failure_maps_to_original_method(self):
		generate_real_xml(self.tmp, [FixtureSubTestFailure], SLUG_A)
		inv = self.inventory({MODULE_A: ["FixtureSubTestFailure.test_subtest_grouping"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "subTest fail phải làm gate fail")
		rows = [row for row in report["results"] if row["module"] == MODULE_A]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["status"], "FAIL", "phải quy về method gốc, không phải NOT_RUN")
		self.assertIn("test_subtest_grouping", rows[0]["raw_name"])
		self.assertEqual(rows[0]["match_reason"], "prefix")

	def test_subtest_error_maps_to_original_method(self):
		generate_real_xml(self.tmp, [FixtureSubTestError], SLUG_A)
		inv = self.inventory({MODULE_A: ["FixtureSubTestError.test_subtest_error_grouping"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1)
		rows = [row for row in report["results"] if row["module"] == MODULE_A]
		self.assertEqual(rows[0]["status"], "ERROR")

	def test_subtest_skip_maps_to_original_method(self):
		"""subTest bị skip ⇒ method gốc là SKIP ⇒ gate fail."""
		generate_real_xml(self.tmp, [FixtureSubTestSkip], SLUG_A)
		inv = self.inventory({MODULE_A: ["FixtureSubTestSkip.test_subtest_skipping"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "subTest skip phải làm gate fail")
		self.assertEqual(report["counts"].get("SKIP"), 1)

	def test_subtest_pass_still_passes(self):
		generate_real_xml(self.tmp, [FixtureSubTestPassOnly], SLUG_B)
		inv = self.inventory({MODULE_B: ["FixtureSubTestPassOnly.test_only_subtests"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 0, f"subTest pass phải vẫn PASS: {report}")
		self.assertEqual(report["passing_total"], 1)

	def test_subtest_failure_not_masked_by_subtest_pass(self):
		"""Cùng method: một subTest pass + một subTest fail ⇒ giữ FAIL."""
		self.tmp.mkdir(parents=True, exist_ok=True)
		content = (
			'<testsuites><testsuite>'
			+ case_xml(MODULE_A, "FixtureSubTestFailure", "test_subtest_grouping")
			+ case_xml(
				MODULE_A,
				"FixtureSubTestFailure",
				"test_subtest_grouping [index=2]",
				"<failure message='boom'/>",
			)
			+ "</testsuite></testsuites>"
		)
		write_raw_xml(self.tmp, SLUG_A, content)
		inv = self.inventory({MODULE_A: ["FixtureSubTestFailure.test_subtest_grouping"]})
		code, report = run_gate(self.tmp, inv)
		self.assertEqual(code, 1, "subTest pass không được che subTest fail")
		self.assertEqual(len(report["failed_methods"]), 1)


# ===========================================================================
# 7. Inventory hygiene — fail-closed khi inventory tự mâu thuẫn
# ===========================================================================
class TestGateInventoryValidation(GateTestBase):
	def _run_raw(self, inventory_payload: dict) -> subprocess.CompletedProcess:
		with tempfile.TemporaryDirectory() as tmp:
			inv_path = Path(tmp) / "inventory.json"
			inv_path.write_text(json.dumps(inventory_payload), encoding="utf-8")
			return subprocess.run(
				[
					sys.executable,
					str(GATE_PATH),
					"--inventory",
					str(inv_path),
					"--xml-dir",
					str(self.tmp),
					"--out",
					str(Path(tmp) / "out.json"),
				],
				capture_output=True,
				text=True,
				encoding="utf-8",
				errors="replace",
			)

	def test_ambiguous_inventory_is_rejected(self):
		"""Cùng class+method ở 2 module ⇒ định danh mơ hồ ⇒ từ chối chạy."""
		proc = self._run_raw(
			self.inventory(
				{
					MODULE_A: ["SharedClass.test_shared_method"],
					MODULE_B: ["SharedClass.test_shared_method"],
				}
			)
		)
		self.assertNotEqual(proc.returncode, 0, "inventory mơ hồ phải fail")
		self.assertIn("mơ hồ", proc.stderr + proc.stdout)

	def test_inventory_without_modules_is_rejected(self):
		proc = self._run_raw({"schema_version": 1})
		self.assertNotEqual(proc.returncode, 0)

	def test_inventory_file_missing_is_rejected(self):
		proc = subprocess.run(
			[
				sys.executable,
				str(GATE_PATH),
				"--inventory",
				str(self.tmp / "nope.json"),
				"--xml-dir",
				str(self.tmp),
				"--out",
				str(self.tmp / "out.json"),
			],
			capture_output=True,
			text=True,
			encoding="utf-8",
			errors="replace",
		)
		self.assertNotEqual(proc.returncode, 0)


# ===========================================================================
# 8. Real repo inventory — structural sanity (không cần XML)
# ===========================================================================
class TestGateRepoInventory(unittest.TestCase):
	# `__test__ = False`: fixture sinh XML cho test, không phải test của harness.
	# `unittest.main()` sẽ discovery mọi class ở module scope và coi FAIL/ERROR/skip
	# cố ý trong fixture là hỏng test thật.
	__test__ = False

	def test_repo_inventory_is_unambiguous_and_non_empty(self):
		inv_path = GATE_PATH.parent / "g007_test_inventory.json"
		if not inv_path.exists():
			self.skipTest("không có g007_test_inventory.json trong repo")
		modules = gate.load_inventory(inv_path)
		self.assertGreater(len(modules), 0)
		# load_inventory đã raise SystemExit nếu mơ hồ ⇒ chạy tới đây là đủ.
		total = sum(len(v) for v in modules.values())
		self.assertGreater(total, 0)
		self.assertEqual(total, 99, f"inventory phải có 99 method, thực tế {total}")


if __name__ == "__main__":
	if xmlrunner is None:
		print("CẦN cài xmlrunner: pip install unittest-xml-reporting", file=sys.stderr)
		sys.exit(2)
	os.environ.setdefault("PYTHONIOENCODING", "utf-8")
	if hasattr(sys.stdout, "reconfigure"):
		sys.stdout.reconfigure(encoding="utf-8", errors="replace")
	# Chỉ load các class *harness* (TestGate*/TestGateRepoInventory). Các lớp
	# `Fixture*` là input để sinh XML, có chứa FAIL/ERROR/skip cố ý — nếu để
	# unittest discovery chạy luôn thì chúng sẽ bị báo là test hỏng.
	loader = unittest.TestLoader()
	suite = unittest.TestSuite()
	for _name, _obj in sorted(globals().items()):
		if (
			inspect.isclass(_obj)
			and issubclass(_obj, unittest.TestCase)
			and _obj.__module__ == __name__
			and _obj.__name__.startswith("Test")
		):
			suite.addTests(loader.loadTestsFromTestCase(_obj))
	result = unittest.TextTestRunner(verbosity=2).run(suite)
	sys.exit(0 if result.wasSuccessful() else 1)