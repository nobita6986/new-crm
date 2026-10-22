#!/usr/bin/env python3
"""Static verification cho `ci_http_transport_probe.py` (T1B) — không cần site.

Vì sao cần test tĩnh
--------------------
Probe HTTP chỉ chạy được trong container bench với site đã seed. Nhưng các lỗi
T1C lần 17 phát hiện là lỗi **nhãn + assertion**, hoàn toàn kiểm được tĩnh:

  1. Các case gọi `get_attachments` và kiểm `file_url` bị gắn nhãn `sec16-*`
     ⇒ phải là SEC-26.
  2. SEC-16 phải thật sự được probe (permlevel field qua `crm.api.doc.get_data`),
     không chỉ là nhãn trên giấy.
  3. Assertion phải chứng minh sentinel không lộ và khớp semantics test nguồn;
     không được tuyên bố key bị loại khi mới chỉ xác nhận value absent/falsy.
  4. Không được dùng assertion vacuous (ví dụ kiểm `"owner"` không thuộc tập key
     lọc chỉ lấy key bắt đầu bằng `_` — luôn đúng vì `"owner"` không bắt đầu
     bằng `_`).
  5. SEC-26 phải giữ riêng: private attachment + non-system ⇒ redact `file_url`;
     system caller giữ `file_url` theo đúng wrapper hiện hành.

Test ở đây **AST/parse tĩnh** source probe (không import, không network, không
site) và kiểm cả source test gốc trong repo.

Chạy:
    python3 deploy/dev/seed/test_ci_http_probe_static.py
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE_PATH = HERE / "ci_http_transport_probe.py"
REPO = HERE.parent.parent.parent
SOURCE_TEST = REPO / "apps/crm_cskh/crm_cskh/tests/test_security_remediation_v2.py"
WRAPPER = REPO / "apps/crm_cskh/crm_cskh/api/guarded_crm.py"


def load_probe() -> tuple[str, ast.Module]:
	source = PROBE_PATH.read_text(encoding="utf-8")
	return source, ast.parse(source, filename=str(PROBE_PATH))


def all_check_ids(tree: ast.Module) -> list[str]:
	"""Mọi case id được truyền làm tham số đầu của `check(...)`."""
	ids: list[str] = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check":
			if node.args and isinstance(node.args[0], ast.Constant):
				ids.append(str(node.args[0].value))
	return ids


def check_call(tree: ast.Module, case_id: str) -> ast.Call | None:
	for node in ast.walk(tree):
		if (
			isinstance(node, ast.Call)
			and isinstance(node.func, ast.Name)
			and node.func.id == "check"
			and node.args
			and isinstance(node.args[0], ast.Constant)
			and node.args[0].value == case_id
		):
			return node
	return None


def request_path(tree: ast.Module, case_id: str) -> str | None:
	"""Tìm path literal truyền cho `request(case_id, ...)`, gộp string nối chuỗi."""
	for node in ast.walk(tree):
		if (
			isinstance(node, ast.Call)
			and isinstance(node.func, ast.Name)
			and node.func.id == "request"
			and node.args
			and isinstance(node.args[0], ast.Constant)
			and node.args[0].value == case_id
		):
			for arg in node.args[1:]:
				text = literal_text(arg)
				if text and text.startswith("/"):
					return text
	return None


def literal_text(node: ast.AST) -> str | None:
	"""Chuỗi literal hoặc tên hằng đã thu thập ở module scope."""
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	if isinstance(node, ast.Name):
		return _ENV_CONSTS.get(node.id)
	return None


#: Hằng chuỗi ở module scope của probe, điền bởi `load_env_consts()`.
_ENV_CONSTS: dict[str, str] = {}


def load_env_consts() -> dict[str, str]:
	"""Thu thập hằng `NAME = "literal"` ở module scope của probe."""
	source, tree = load_probe()
	consts: dict[str, str] = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and len(node.targets) == 1:
			target = node.targets[0]
			value = node.value
			if (
				isinstance(target, ast.Name)
				and isinstance(value, ast.Constant)
				and isinstance(value.value, str)
			):
				consts[target.id] = value.value
			elif isinstance(target, ast.Name) and isinstance(value, ast.Call):
				# f"{DEMO}-{LEAD_NAME}.txt" → nối chuỗi với tên đã biết
				parts: list[str] = []
				ok = True
				for piece in value.args[:-1]:
					if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
						parts.append(piece.value)
					elif isinstance(piece, ast.Name) and piece.id in consts:
						parts.append(consts[piece.id])
					else:
						ok = False
				if ok:
					consts[target.id] = "".join(parts)
	_ENV_CONSTS.update(consts)
	return consts


class TestProbeLabeling(unittest.TestCase):
	"""C1 — nhãn case: get_attachments/file_url phải là SEC-26, không phải SEC-16."""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()
		cls.check_ids = all_check_ids(cls.tree)
		cls.env = load_env_consts()

	def test_no_sec16_case_calls_get_attachments(self):
		"""Không case `sec16-*` nào được gọi `get_attachments`."""
		offenders = [
			case_id
			for case_id in self.check_ids
			if case_id.startswith("sec16-") and "get_attachments" in self.source_for_case(case_id)
		]
		self.assertEqual(
			offenders, [], f"case SEC-16 gọi get_attachments (thuộc SEC-26): {offenders}"
		)

	def test_no_sec16_case_asserts_file_url(self):
		"""Không assertion `sec16-*` nào được kiểm `file_url`."""
		offenders = []
		for case_id in self.check_ids:
			if not case_id.startswith("sec16-"):
				continue
			call = check_call(self.tree, case_id)
			if call is None:
				continue
			rendered = ast.unparse(call)
			if "file_url" in rendered:
				offenders.append(case_id)
		self.assertEqual(
			offenders, [], f"case SEC-16 kiểm file_url (thuộc SEC-26): {offenders}"
		)

	def test_get_attachments_cases_are_labelled_sec26(self):
		"""Case nào gọi `get_attachments` thì phải mang nhãn `sec26-*`."""
		attachment_cases = [
			case_id
			for case_id in self.check_ids
			if "get_attachments" in self.source_for_case(case_id)
		]
		self.assertTrue(attachment_cases, "phải còn case SEC-26 qua get_attachments")
		for case_id in attachment_cases:
			self.assertTrue(
				case_id.startswith("sec26-"),
				f"{case_id} gọi get_attachments ⇒ phải nhãn sec26-*",
			)

	def test_source_has_no_legacy_sec16_attachment_labels(self):
		"""Không còn nhãn `sec16-staff` / `sec16-admin` (nhãn sai của T1C lần 17)."""
		self.assertNotIn('"sec16-staff"', self.source)
		self.assertNotIn('"sec16-admin"', self.source)

	def source_for_case(self, case_id: str) -> str:
		"""Text lệnh `request(...)` ngay trước check tương ứng (xấp xỉ có kiểm soát)."""
		paths = []
		for node in ast.walk(self.tree):
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "request"
				and node.args
				and isinstance(node.args[0], ast.Constant)
				and str(node.args[0].value) == case_id
			):
				paths.append(ast.unparse(node))
		joined = " ".join(paths)
		# `attachments_path` là biến chứa path get_attachments.
		if any("attachments_path" in item for item in paths):
			return "get_attachments " + joined
		return joined


class TestProbeSec16RealCoverage(unittest.TestCase):
	"""C2/C3 — SEC-16 phải thật sự probe permlevel field, assertion chứng minh sentinel không lộ."""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()
		cls.check_ids = all_check_ids(cls.tree)
		cls.sec16_ids = [i for i in cls.check_ids if i.startswith("sec16-")]

	def test_sec16_exists(self):
		self.assertTrue(self.sec16_ids, "phải có case SEC-16 (field exposure)")

	def test_sec16_uses_crm_api_doc_get_data(self):
		"""SEC-16 phải đi qua `crm.api.doc.get_data` — endpoint có trả permlevel field."""
		uses = any(
			"crm.api.doc.get_data" in ast.unparse(node)
			for node in ast.walk(self.tree)
			if isinstance(node, ast.Call)
			and isinstance(node.func, ast.Name)
			and node.func.id == "request"
			and node.args
			and isinstance(node.args[0], ast.Constant)
			and str(node.args[0].value).startswith("sec16-")
		)
		self.assertTrue(uses, "SEC-16 phải gọi crm.api.doc.get_data")

	def test_sec16_uses_synthetic_permlevel_field_with_sentinel(self):
		"""Field synthetic phải có permlevel=1 và record phải chứa sentinel secret."""
		self.assertIn("SEC16_FIELDNAME", self.source)
		self.assertIn("SEC16_SENTINEL", self.source)
		self.assertRegex(self.source, r'"permlevel"\s*:\s*1')
		self.assertIn(
			'frappe.db.set_value("CRM Lead", LEAD_NAME, SEC16_FIELDNAME, SEC16_SENTINEL)',
			self.source,
		)

	def test_sec16_uses_non_system_user(self):
		"""SEC-16 phải dùng user KHÔNG phải Administrator/system."""
		self.assertIn("SEC16_USER", self.source)
		user_const = load_env_consts().get("SEC16_USER", "")
		self.assertTrue(user_const)
		self.assertNotEqual(user_const, "Administrator")
		self.assertIn("staff.", user_const)

	def test_sec16_asserts_sentinel_absent(self):
		"""Assertion phải chứng minh sentinel không lộ (không chỉ field vắng mặt)."""
		scan_ids = [
			i for i in self.sec16_ids if "sentinel" in i or "get-data" in i
		]
		self.assertTrue(scan_ids, "phải có case kiểm sentinel của SEC-16")
		found_value_assert = False
		found_key_absent_assert = False
		for case_id in scan_ids:
			call = check_call(self.tree, case_id)
			if call is None:
				continue
			rendered = ast.unparse(call)
			if "SEC16_SENTINEL not in" in rendered:
				found_value_assert = True
			if re.search(r"not row\.get\(SEC16_FIELDNAME\)", rendered):
				found_value_assert = True
		self.assertTrue(
			found_value_assert,
			"phải có assertion chứng minh sentinel/giá trị permlevel không lộ",
		)
		self.assertFalse(found_key_absent_assert)

	def test_sec16_does_not_claim_key_removal(self):
		"""Không được tuyên bố key bị loại khi mới chỉ xác nhận giá trị absent."""
		for case_id in self.sec16_ids:
			call = check_call(self.tree, case_id)
			if call is None:
				continue
			rendered = ast.unparse(call)
			self.assertNotIn(
				'SEC16_FIELDNAME" not in',
				rendered,
				f"{case_id}: không được khẳng định key bị loại khi chỉ xác nhận value falsy",
			)


class TestProbeNoVacuousAssertion(unittest.TestCase):
	"""C4 — không assertion vacuous kiểu `assertNotIn("owner", {k for k in ... if k.startswith("_")})`."""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()

	def test_probe_has_no_startswith_key_filter_assertion(self):
		self.assertNotIn(
			'if k.startswith("_")',
			self.source,
			"probe không được dùng assertion lọc key bắt đầu bằng `_` (vacuous)",
		)
		self.assertNotIn(
			'if key.startswith("_")',
			self.source,
			"probe không được dùng assertion lọc key bắt đầu bằng `_` (vacuous)",
		)

	def test_vacuous_pattern_is_recognisable_for_future_review(self):
		"""Guard: nếu sau này ai đó viết lại assertion theo mẫu vacuous thì test này
		vẫn bắt được (regex khớp chính xác mẫu đã bị T1C chỉ ra)."""
		vacuous = re.compile(
			r'assert(?:Not)?In\(\s*["\'][^"\']+["\']\s*,\s*\{[^}]*startswith\([^)]*\)',
			re.S,
		)
		self.assertIsNone(
			vacuous.search(self.source),
			"phát hiện assertion dạng vacuous (key cố định vs tập key lọc)",
		)


class TestProbeSec26Boundary(unittest.TestCase):
	"""C5 — SEC-26 giữ riêng: private+non-system redact, system caller giữ `file_url`."""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()
		cls.check_ids = all_check_ids(cls.tree)

	def test_sec26_has_staff_and_admin_cases(self):
		sec26 = [i for i in self.check_ids if i.startswith("sec26-")]
		self.assertTrue(any("staff" in i for i in sec26), "thiếu case SEC-26 non-system")
		self.assertTrue(any("admin" in i for i in sec26), "thiếu case SEC-26 system caller")

	def test_non_system_private_must_have_file_url_absent(self):
		"""Private + non-system: wrapper loại **key** `file_url` ⇒ assert key vắng mặt."""
		case_id = next(
			(i for i in self.check_ids if i.startswith("sec26-") and "staff" in i),
			None,
		)
		self.assertIsNotNone(case_id)
		call = check_call(self.tree, case_id)
		# `ast.unparse` chuẩn hoá quote thành `'` ⇒ so khớp không phụ thuộc dấu nháy.
		rendered = ast.unparse(call).replace('"', "'")
		self.assertIn("'file_url' not in", rendered)
		self.assertIn("is_private", rendered)

	def test_system_caller_must_retain_file_url(self):
		"""System caller: wrapper trả nguyên bản ⇒ `file_url` còn, khớp wrapper."""
		case_id = next(
			(i for i in self.check_ids if i.startswith("sec26-") and "admin" in i),
			None,
		)
		self.assertIsNotNone(case_id)
		call = check_call(self.tree, case_id)
		rendered = ast.unparse(call)
		self.assertIn("file_url", rendered)
		self.assertIn("is_private", rendered)

	def test_assertions_match_current_wrapper_behaviour(self):
		"""Wrapper `_redact_private_file_urls` chỉ redact khi `is_private` truthy và
		caller không phải system ⇒ probe phải kiểm đúng hai điều kiện đó."""
		read = WRAPPER.read_text(encoding="utf-8")
		self.assertIn("_redact_private_file_urls", read)
		self.assertIn('entry.get("is_private")', read)


class TestProbeSourceSemanticsMatch(unittest.TestCase):
	"""C3 — probe phải khớp semantics test nguồn trong repo."""

	@classmethod
	def setUpClass(cls):
		if not SOURCE_TEST.exists():
			raise unittest.SkipTest(f"không có {SOURCE_TEST}")
		cls.source = SOURCE_TEST.read_text(encoding="utf-8")

	def test_source_test_creates_permlevel_1_custom_field(self):
		self.assertIn("custom_cskh_sec16_restricted", self.source)
		self.assertRegex(self.source, r'"permlevel"\s*:\s*1')

	def test_source_test_asserts_value_falsy_not_key_absent(self):
		"""Test nguồn assert `assertFalse(result["data"][0].get(fieldname))` —
		nghĩa là **giá trị** falsy, không phải key bị loại."""
		self.assertIn("assertFalse(result[\"data\"][0].get(self.fieldname))", self.source)

	def test_source_test_is_named_for_get_data_permlevel(self):
		self.assertIn("test_crm_api_get_data_filters_permlevel_field", self.source)


class TestProbeSyntax(unittest.TestCase):
	def test_probe_parses(self):
		source, tree = load_probe()
		self.assertIsInstance(tree, ast.Module)
		self.assertTrue(source.strip())

	def test_probe_has_fail_closed_exit(self):
		"""Probe phải trả exit khác 0 khi có case fail."""
		source, tree = load_probe()
		self.assertIn("return 1 if failures else 0", source)


if __name__ == "__main__":
	if hasattr(sys.stdout, "reconfigure"):
		sys.stdout.reconfigure(encoding="utf-8", errors="replace")
	unittest.main(verbosity=2)