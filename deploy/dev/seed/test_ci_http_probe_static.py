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
HOOKS = REPO / "apps/crm_cskh/crm_cskh/hooks.py"


def load_probe() -> tuple[str, ast.Module]:
	source = PROBE_PATH.read_text(encoding="utf-8")
	return source, ast.parse(source, filename=str(PROBE_PATH))


def all_check_ids(tree: ast.Module) -> list[str]:
	"""Mọi case id được truyền làm tham số đầu của `check(...)`."""
	ids: list[str] = []
	for node in ast.walk(tree):
		if (
			isinstance(node, ast.Call)
			and isinstance(node.func, ast.Name)
			and node.func.id == "check"
		):
			if node.args and isinstance(node.args[0], ast.Constant):
				ids.append(str(node.args[0].value))
	return ids


def assigned_string_constants(tree: ast.Module) -> dict[str, str]:
	"""Bảng `{tên biến: giá trị chuỗi}` cho biến được gán bằng string literal.

	Dùng để truy vết giá trị thực của biến (ví dụ `get_data_path`) khi kiểm tra
	tĩnh, thay vì so chuỗi rời rạc có thể vỡ khi cấu trúc lời gọi thay đổi.
	"""
	assigned: dict[str, str] = {}
	for node in ast.walk(tree):
		if not isinstance(node, ast.Assign) or len(node.targets) != 1:
			continue
		target = node.targets[0]
		value = node.value
		if (
			isinstance(target, ast.Name)
			and isinstance(value, ast.Constant)
			and isinstance(value.value, str)
		):
			assigned[target.id] = value.value
		elif isinstance(target, ast.Name) and isinstance(value, ast.BinOp):
			# `"/api/..." + biến` — chỉ giữ phần literal đầu làm dấu hiệu.
			if isinstance(value.left, ast.Constant) and isinstance(
				value.left.value, str
			):
				assigned.setdefault(target.id, value.left.value)
	return assigned


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


def find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
	"""Tìm định nghĩa hàm theo tên (dùng cho kiểm tra AST trong một hàm)."""
	for node in ast.walk(tree):
		if isinstance(node, ast.FunctionDef) and node.name == name:
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
			if case_id.startswith("sec16-")
			and "get_attachments" in self.source_for_case(case_id)
		]
		self.assertEqual(
			offenders,
			[],
			f"case SEC-16 gọi get_attachments (thuộc SEC-26): {offenders}",
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
		cls.assignments = assigned_string_constants(cls.tree)

	@classmethod
	def _resolve_name(cls, name: str) -> str:
		"""Giá trị của một biến module cấp chuỗi (ví dụ `get_data_path`)."""
		return cls.assignments.get(name, name)

	def test_sec16_exists(self):
		self.assertTrue(self.sec16_ids, "phải có case SEC-16 (field exposure)")

	def test_sec16_uses_crm_api_doc_get_data(self):
		"""SEC-16 phải đi qua `crm.api.doc.get_data` — endpoint có trả permlevel field.

		Endpoint được truyền qua biến `get_data_path` (không nằm trực tiếp trong
		lời gọi `request`) nên ta gom chuỗi endpoint của **mọi** lời gọi `request`
		có case id bắt đầu bằng `sec16-`, rồi resolve biến để không bỏ sót khi
		cấu trúc call thay đổi (ví dụ GET → POST + JSON body).
		"""
		sec16_paths: list[str] = []
		for node in ast.walk(self.tree):
			if not (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "request"
				and node.args
				and isinstance(node.args[0], ast.Constant)
				and str(node.args[0].value).startswith("sec16-")
			):
				continue
			# `request(case_id, opener, method, path, body=None, headers=None)`
			if len(node.args) < 4:
				continue
			path = node.args[3]
			if isinstance(path, ast.Constant) and isinstance(path.value, str):
				sec16_paths.append(path.value)
			elif isinstance(path, ast.Name):
				sec16_paths.append(self._resolve_name(path.id))
		# `sec16-setup` là bước chuẩn bị (fail-closed khi không tạo được field), không
		# phải case HTTP — nó cố ý **không** gọi `request()`.
		http_sec16_ids = [i for i in self.sec16_ids if i != "sec16-setup"]
		self.assertEqual(
			len(sec16_paths),
			len(http_sec16_ids),
			"mỗi case SEC-16 HTTP phải gọi request() với path tường minh",
		)
		self.assertTrue(
			sec16_paths and all("crm.api.doc.get_data" in path for path in sec16_paths),
			"mọi case SEC-16 phải trỏ cùng endpoint crm.api.doc.get_data, thấy: %r"
			% (sec16_paths,),
		)

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
		scan_ids = [i for i in self.sec16_ids if "sentinel" in i or "get-data" in i]
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
		self.assertTrue(
			any("staff" in i for i in sec26), "thiếu case SEC-26 non-system"
		)
		self.assertTrue(
			any("admin" in i for i in sec26), "thiếu case SEC-26 system caller"
		)

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
		self.assertIn('assertFalse(result["data"][0].get(self.fieldname))', self.source)

	def test_source_test_is_named_for_get_data_permlevel(self):
		self.assertIn("test_crm_api_get_data_filters_permlevel_field", self.source)


class TestProbeGetActivitiesCoverage(unittest.TestCase):
	"""C6 — SEC-26: probe phải xác minh redaction qua `get_activities`.

	Probe phải gọi route `get_activities` đã override và kiểm tra payload cùng
	`docinfo` sau khi wrapper áp dụng redaction.

	Nguyên tắc bất di bất dịch:
	- Probe phải gọi **đúng** endpoint `crm.api.activities.get_activities`.
	- Phải đo **riêng** `message[4].attachments` **và** `docinfo.attachments`
	  (2 đường độc lập, không được gộp).
	- Với file private và caller non-system, actual `/private/files/` URL phải
	  vắng mặt trên mọi nhánh response; placeholder redaction không phải URL.
	- Nếu URL bị loại, không được gọi download endpoint và báo đã tải thành công.
	- Caller phải là **non-system** (để không đo nhầm hành vi Administrator).
	"""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()
		cls.check_ids = all_check_ids(cls.tree)

	def test_case_exists_and_is_labelled_sec26(self):
		ids = [i for i in self.check_ids if "activities" in i]
		self.assertTrue(ids, "phải có case đo `get_activities` (RFC SEC-26)")
		for case_id in ids:
			self.assertTrue(
				case_id.startswith("sec26-"),
				f"case đo get_activities phải mang nhãn sec26-*: {case_id}",
			)

	def test_case_calls_get_activities_endpoint(self):
		"""Case phải trỏ đúng `crm.api.activities.get_activities` (KHÔNG phải
		endpoint đã override `guarded_crm.get_attachments`)."""
		found = False
		for node in ast.walk(self.tree):
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "request"
				and len(node.args) >= 4
				and isinstance(node.args[0], ast.Constant)
				and "activities" in str(node.args[0].value)
			):
				path = ast.unparse(node.args[3])
				# Resolve tên hằng module-scope (vd `ACTIVITIES_ENDPOINT`) để kiểm
				# được endpoint thật, không chỉ tên biến.
				resolved = self.resolve_path(path)
				if "crm.api.activities.get_activities" in resolved:
					found = True
				else:
					self.fail(
						f"case activities phải gọi đúng endpoint, thấy: {resolved}"
					)
		self.assertTrue(found, "không tìm thấy request() nào trỏ get_activities")

	def resolve_path(self, rendered: str) -> str:
		"""Resolve tên biến/hằng trong path đã render (module scope + local scope).

		`request(..., activities_path)` trong đó `activities_path` được gán
		`ACTIVITIES_ENDPOINT + "?" + urlencode(...)` ⇒ phải truy vết tới hằng
		module-scope `ACTIVITIES_ENDPOINT` để kiểm được endpoint thật.
		"""
		# 1) Bảng gán: tên -> biểu thức RHS (module scope + function scope).
		assignments: dict[str, ast.AST] = {}
		for node in ast.walk(self.tree):
			if isinstance(node, ast.Assign) and len(node.targets) == 1:
				target = node.targets[0]
				if isinstance(target, ast.Name):
					assignments.setdefault(target.id, node.value)

		# 2) Tìm chuỗi literal đầu tiên trong biểu thức, nếu có.
		def first_literal(node: ast.AST) -> str | None:
			if isinstance(node, ast.Constant) and isinstance(node.value, str):
				return node.value
			if isinstance(node, ast.Name):
				target = assignments.get(node.id)
				if target is not None:
					return first_literal(target)
				return None
			if isinstance(node, ast.BinOp):
				return first_literal(node.left)
			if isinstance(node, ast.Call):
				return None
			return None

		# 3) Resolve lặp cho tới khi không còn tên biến nào.
		for _ in range(5):
			before = rendered
			for name in list(assignments):
				if name not in rendered:
					continue
				literal = first_literal(assignments[name])
				if literal:
					rendered = rendered.replace(name, literal)
			if rendered == before:
				break
		return rendered

	def test_get_activities_route_is_overridden_by_wrapper(self):
		"""Route HTTP phải đi qua wrapper bảo vệ `get_activities`."""
		hooks = HOOKS.read_text(encoding="utf-8")
		wrapper = WRAPPER.read_text(encoding="utf-8")
		self.assertIn(
			'"crm.api.activities.get_activities": "crm_cskh.api.guarded_crm.get_activities"',
			hooks,
		)
		self.assertIn("def get_activities(name: str):", wrapper)

	def test_measures_both_paths_separately(self):
		"""Phải đo riêng `message[4].attachments` và `docinfo.attachments`."""
		src = self.source_for_activities()
		# Phải **thực sự lấy** `message` và `docinfo` từ response, không chỉ
		# nhắc tới tên biến.
		self.assertRegex(src, r'get\("message"\)', "phải lấy `message` từ response")
		self.assertRegex(src, r'get\("docinfo"\)', "phải lấy `docinfo` từ response")
		self.assertIn("attachments", src)
		# `message[4]` là phần tử thứ 5 của tuple 5 phần tử.
		self.assertRegex(src, r"message\[4\]|len\(message\) == 5")
		# Hai đường phải được rút ra bằng **hai biến khác nhau**.
		self.assertIn("message_private", src)
		self.assertIn("docinfo_private", src)

	def test_private_file_urls_must_be_redacted_from_all_paths(self):
		"""Response in-scope không được chứa private URL tại bất kỳ leak site nào."""
		src = self.source_for_activities()
		self.assertIn("any_private_file_url = any(", src)
		self.assertIn("and not any_private_file_url", src)
		for name in (
			"message_has_url",
			"attachment_log_has_url",
			"docinfo_has_url",
			"attachment_log_html_has_url",
		):
			self.assertIn(name, src)

	def test_records_observations_for_each_path(self):
		"""Mỗi đường phải có **một** quan sát riêng trong evidence."""
		self.assertIn(
			"observations", self.source, "phải ghi `observations` vào evidence"
		)
		for observation_id in (
			"sec26-activities-message-attachments",
			"sec26-activities-message-attachment-log",
			"sec26-activities-docinfo-attachments",
			"sec26-activities-docinfo-attachment-logs",
		):
			self.assertIn(observation_id, self.source)
		# Quan sát phải được **ghi vào** list `observations` (không chỉ nhắc tên).
		self.assertRegex(
			self.source,
			r"observations\s*\.\s*append\s*\(",
			"phải thực sự append vào list `observations`",
		)

	def test_every_observation_group_is_actually_appended(self):
		"""**Mọi** nhóm quan sát phải thực sự `observations.append(...)`.

		Regex `observations\\.append(` của test cũ chỉ bắt được **một** occurrence
		⇒ mutation đổi occurrence **đầu tiên** (nhóm 4 đường lộ) thành
		`_ignored.append(` vẫn lọt, dù 4 quan sát đó không còn được ghi.
		"""
		append_calls = [
			node
			for node in ast.walk(self.tree)
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "append"
				and isinstance(node.func.value, ast.Name)
				and node.func.value.id == "observations"
			)
		]
		self.assertGreaterEqual(
			len(append_calls),
			3,
			"phải có ≥3 lời gọi observations.append (4 đường lộ + ngoài scope + tải file)",
		)

		# (a) Nhóm 4 đường lộ: vòng `for` duyệt tuple chứa id của đường đầu
		#     ⇒ thân vòng phải append vào `observations`.
		loop_appends = False
		for node in ast.walk(self.tree):
			if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)):
				continue
			# `for ... in (("id-1", ...), ("id-2", ...)):` — id nằm ở tuple con.
			iter_ids: set[str] = set()
			for element in node.iter.elts:
				if isinstance(element, ast.Constant) and isinstance(element.value, str):
					iter_ids.add(element.value)
				elif isinstance(element, ast.Tuple):
					for sub in element.elts:
						if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
							iter_ids.add(sub.value)
			if "sec26-activities-message-attachments" not in iter_ids:
				continue
			for sub in ast.walk(node):
				if (
					isinstance(sub, ast.Call)
					and isinstance(sub.func, ast.Attribute)
					and sub.func.attr == "append"
					and isinstance(sub.func.value, ast.Name)
					and sub.func.value.id == "observations"
				):
					loop_appends = True
		self.assertTrue(
			loop_appends,
			"vòng lặp ghi 4 quan sát đường lộ phải append vào `observations`",
		)

		# (b) Quan sát ngoài scope + tải file: dict literal có `id` hằng.
		appended_ids = set()
		for call in append_calls:
			for sub in ast.walk(call):
				if not isinstance(sub, ast.Dict):
					continue
				for key, value in zip(sub.keys, sub.values):
					if (
						isinstance(key, ast.Constant)
						and key.value == "id"
						and isinstance(value, ast.Constant)
						and isinstance(value.value, str)
					):
						appended_ids.add(value.value)
		for observation_id in (
			"sec26-activities-out-of-scope",
			"sec26-activities-download-in-scope",
		):
			self.assertIn(
				observation_id,
				appended_ids,
				"quan sát %s phải được append vào `observations`" % observation_id,
			)

	def test_measures_attachment_log_paths(self):
		"""Phải đo **2 đường attachment_log** (URL trong HTML/activity, không phải key `file_url`).

		Đường 3: `message[0][attachment_log].data.file_url` (CRM `parse_attachment_log` L524).
		        Đường 4: `docinfo.attachment_logs[].content` — HTML `<a href="/private/files/…">`
		        (Frappe `file.py` L898-906). Vì URL nằm trong chuỗi HTML, probe phải đo
		        riêng đường này và xác nhận wrapper đã thay URL trước khi trả response.
		"""
		src = self.source_for_activities()
		# Đường 3: phải **thực sự lấy** activity type `attachment_log` từ `message[0]`
		# và đọc `data.file_url` — không được chỉ nhắc tên.
		self.assertRegex(
			src,
			r'get\(\s*"activity_type"\s*\)\s*==\s*"attachment_log"',
			"phải lọc activity có activity_type == 'attachment_log'",
		)
		# `attachment_log_data` phải được **rút từ** `attachment_log_activity` — nếu
		# đường 3 bị cắt (activity = None) thì `data` không bao giờ được đọc.
		data_assignment = self.assignment_node("attachment_log_data")
		self.assertIsNotNone(data_assignment)
		self.assertIn(
			"attachment_log_activity.get",
			ast.unparse(data_assignment),
			"đường 3 phải rút `data` từ activity attachment_log",
		)
		self.assertRegex(
			src,
			r"attachment_log_data\.get\(\s*\"file_url\"\s*\)",
			"đường 3 phải đọc `data.file_url`",
		)
		# Kiểm tra **AST**: `attachment_log_activity` phải được gán bằng lời gọi
		# `next(...)` — nếu đường 3 bị cắt (gán `None`) thì các regex ở trên vẫn
		# khớp vì phần đọc `data` còn nằm trong source ⇒ phải kiểm ở tầng AST.
		self.assertIsNotNone(
			self.assignment_is_next_call("attachment_log_activity"),
			"`attachment_log_activity` phải được rút bằng next(...) từ message[0]",
		)
		self.assertIsNotNone(
			self.assignment_reads_docinfo_key(
				"docinfo_attachment_logs", "attachment_logs"
			),
			"`docinfo_attachment_logs` phải được đọc từ `docinfo['attachment_logs']`",
		)
		# Đường 4: phải đọc `content` của dòng attachment_log và nhận diện URL private.
		self.assertRegex(
			src,
			r'attachment_log_row\.get\(\s*"content"\s*\)',
			"đường 4 phải đọc `content` của dòng attachment_log",
		)
		# Nhận diện URL private trong HTML phải nằm trong **biểu thức tính** của
		# `attachment_log_html_has_url` — kiểm ở tầng **AST**, không phải regex
		# trên text: regex `"/private/files/" in src` khớp được cả **comment**
		# ⇒ mutation đặt biểu thức thành `False` vẫn lọt.
		assign = self.assignment_node("attachment_log_html_has_url")
		self.assertIsNotNone(
			assign,
			"phải có biến `attachment_log_html_has_url` để đo đường 4",
		)
		self.assertIn(
			"contains_private_file_url(attachment_log_content)",
			ast.unparse(assign),
			"đường 4 phải nhận diện private URL trong HTML attachment_log",
		)

	def assignment_node(self, name: str) -> ast.Assign | None:
		"""Trả về lệnh gán `name = ...` (nếu có)."""
		for node in ast.walk(self.tree):
			if (
				isinstance(node, ast.Assign)
				and len(node.targets) == 1
				and isinstance(node.targets[0], ast.Name)
				and node.targets[0].id == name
			):
				return node
		return None

	def assignment_is_next_call(self, name: str) -> ast.Call | None:
		"""Trả về lời gọi `next(...)` được gán cho biến `name` (nếu có)."""
		for node in ast.walk(self.tree):
			if (
				isinstance(node, ast.Assign)
				and len(node.targets) == 1
				and isinstance(node.targets[0], ast.Name)
				and node.targets[0].id == name
				and isinstance(node.value, ast.Call)
				and isinstance(node.value.func, ast.Name)
				and node.value.func.id == "next"
			):
				return node.value
		return None

	def assignment_reads_docinfo_key(self, name: str, key: str) -> ast.Call | None:
		"""Trả về lời gọi `.get("key")` được gán cho biến `name` (nếu có).

		Chấp nhận cả dạng `docinfo.get("key")` lẫn dạng có điều kiện
		(`x.get("key") if … else None`) — cả hai đều là truy vấn thật.
		"""
		for node in ast.walk(self.tree):
			if not (
				isinstance(node, ast.Assign)
				and len(node.targets) == 1
				and isinstance(node.targets[0], ast.Name)
				and node.targets[0].id == name
			):
				continue
			value = node.value
			# Bóc điều kiện `X if cond else Y`.
			if isinstance(value, ast.IfExp):
				candidates = [value.body, value.orelse]
			else:
				candidates = [value]
			for cand in candidates:
				if (
					isinstance(cand, ast.Call)
					and isinstance(cand.func, ast.Attribute)
					and cand.func.attr == "get"
					and cand.args
					and isinstance(cand.args[0], ast.Constant)
					and cand.args[0].value == key
				):
					return cand
		return None

	def test_observations_do_not_affect_gate(self):
		"""`observations` KHÔNG được ảnh hưởng PASS/FAIL của gate.

		Gate chỉ dựa vào `failed_cases`; quan sát là **bằng chứng quan sát**.
		"""
		self.assertIn('"failed_cases": failures', self.source)
		self.assertIn('"probe_result": "FAIL" if failures else "PASS"', self.source)

	def test_uses_non_system_caller(self):
		"""Caller phải là non-system (`staff.a@dev.local`), KHÔNG phải Administrator."""
		src = self.source_for_activities()
		self.assertIn(
			"staff", src, "case get_activities phải dùng opener của staff (non-system)"
		)
		self.assertNotIn("admin,", src)

	def test_uses_seeded_synthetic_document(self):
		"""Phải dùng tài liệu seed **giả** (CRM-LEAD-00001), không dữ liệu thật."""
		src = self.source_for_activities()
		self.assertIn("LEAD_NAME", src)
		self.assertIn("PRIVATE_FILE_NAME", src)

	def test_out_of_scope_case_is_asserted_as_permission_denial(self):
		"""Caller ngoài scope phải nằm trong gate, không chỉ là observation."""
		src = self.source_for_activities()
		self.assertIn('"sec26-activities-out-of-scope"', src)
		self.assertIn('out_of_scope_activities["status"] == 403', src)
		self.assertIn('out_of_scope_exc == "PermissionError"', src)
		self.assertIn("and not out_of_scope_leaks", src)

	def source_for_activities(self) -> str:
		"""Render source của phần code xử lý case `get_activities`."""
		lines = self.source.splitlines()
		start = None
		for i, line in enumerate(lines):
			if "SEC-26: redaction `get_activities`" in line:
				start = i
				break
		self.assertIsNotNone(start, "không tìm thấy request của case get_activities")
		# Lấy từ case đầu tiên tới hết file (case này nằm cuối `run()`).
		return "\n".join(lines[start:])


class TestProbeDownloadMeasurement(unittest.TestCase):
	"""H-RFC-2 — không gọi tải nếu wrapper đã loại private URL.

	Yêu cầu:
	- Có case ghi nhận handoff tải; URL chỉ được dùng nếu thực sự còn lộ.
	- Không URL là kết quả redaction mong đợi, không phải download PASS.
	- **KHÔNG** ghi bytes/nội dung file vào artifact (chỉ đặc trưng).
	- **KHÔNG** assert cứng "phải tải được" / "phải bị chặn" (kết luận thuộc Owner).
	"""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()
		cls.check_ids = all_check_ids(cls.tree)

	def test_case_exists(self):
		self.assertIn(
			"sec26-activities-download-in-scope",
			self.check_ids,
			"phải có case đo khả năng tải file (H-RFC-2)",
		)

	def test_uses_private_file_url_prefix(self):
		"""Case tải phải dùng đúng tiền tố đường private của Frappe."""
		self.assertIn("DOWNLOAD_PATH_PREFIX", self.source)
		self.assertIn('DOWNLOAD_PATH_PREFIX = "/private/files/"', self.source)

	def test_does_not_assert_download_outcome(self):
		"""KHÔNG được assert cứng 200/403 cho case tải — chỉ quan sát."""
		call = check_call(self.tree, "sec26-activities-download-in-scope")
		self.assertIsNotNone(call)
		rendered = ast.unparse(call)
		for forbidden in ("== 200", "== 403", "in (401, 403)"):
			self.assertNotIn(
				forbidden,
				rendered,
				f"case tải file không được assert cứng kết quả: {forbidden}",
			)

	def test_does_not_write_raw_bytes_to_artifact(self):
		"""Nội dung file tải về **KHÔNG** được ghi vào evidence.

		Kiểm ở tầng **AST**: trong dict `entry` của `download_bytes`, giá trị
		`body` phải là **hằng chuỗi rỗng** — không được là biến chứa bytes
		(`payload`, `response_text`, …) và không được là `.decode(...)`.
		"""
		func = None
		for node in ast.walk(self.tree):
			if isinstance(node, ast.FunctionDef) and node.name == "download_bytes":
				func = node
				break
		self.assertIsNotNone(func, "phải có hàm `download_bytes` tải file an toàn")
		rendered = ast.unparse(func)
		self.assertIn("content_sha256", rendered, "phải ghi sha256 thay vì nội dung")
		self.assertIn("content_length", rendered, "phải ghi độ dài thay vì nội dung")

		# `entry` là dict được gán trong hàm — tìm dict có key "body".
		body_values: list[ast.AST] = []
		for node in ast.walk(func):
			if not isinstance(node, ast.Dict):
				continue
			for key, value in zip(node.keys, node.values):
				if (
					isinstance(key, ast.Constant)
					and key.value == "body"
					and isinstance(value, ast.Constant)
					and value.value == ""
				):
					body_values.append(value)
		self.assertTrue(
			body_values,
			"entry của download_bytes phải có `body` là hằng chuỗi rỗng",
		)
		# Ngược lại: KHÔNG được gán `body` = biến chứa bytes.
		for node in ast.walk(func):
			if not isinstance(node, ast.Dict):
				continue
			for key, value in zip(node.keys, node.values):
				if not (isinstance(key, ast.Constant) and key.value == "body"):
					continue
				self.assertIsInstance(
					value,
					ast.Constant,
					"`body` của download_bytes phải là hằng rỗng, không gán bytes thô",
				)

	def test_download_bytes_helper_never_assigns_payload_to_body(self):
		"""Không được gán `payload` (bytes file) vào bất kỳ key `body` nào."""
		func = None
		for node in ast.walk(self.tree):
			if isinstance(node, ast.FunctionDef) and node.name == "download_bytes":
				func = node
				break
		self.assertIsNotNone(func)
		for node in ast.walk(func):
			if not isinstance(node, ast.Assign):
				continue
			for target in node.targets:
				if not (
					isinstance(target, ast.Subscript)
					and isinstance(target.slice, ast.Constant)
				):
					continue
				if target.slice.value != "body":
					continue
				self.fail(
					"download_bytes không được gán vào key `body` (bytes file thô): %s"
					% ast.unparse(node)
				)

	def test_download_records_sha256_and_length(self):
		"""Phải ghi `content_sha256` + `content_length` (đặc trưng không nhận diện)."""
		func = None
		for node in ast.walk(self.tree):
			if isinstance(node, ast.FunctionDef) and node.name == "download_bytes":
				func = node
				break
		self.assertIsNotNone(func)
		rendered = ast.unparse(func)
		self.assertRegex(
			rendered,
			r"content_sha256.*hashlib\.sha256",
			"phải tính sha256 của bytes tải về",
		)
		self.assertRegex(
			rendered,
			r"content_length.*len\(payload\)",
			"phải ghi độ dài bytes tải về",
		)

	def test_download_uses_url_from_payload(self):
		"""URL tải, nếu còn lộ, phải lấy từ payload thật trên một trong bốn nhánh."""
		self.assertIn("observed_private_url", self.source)
		self.assertIn("message_file_url", self.source)
		self.assertIn("attachment_log_file_url", self.source)
		self.assertIn("docinfo_file_url", self.source)
		self.assertIn("attachment_log_html_url", self.source)

	def test_redacted_url_means_no_download_request(self):
		self.assertIn("if not observed_private_url", self.source)
		self.assertIn('"redacted_before_download"', self.source)
		self.assertIn("not any_private_file_url", self.source)

	def test_redacted_download_case_is_recorded_without_request(self):
		"""Gate case phải có trong JSON dù redaction ngăn yêu cầu tải file."""
		self.assertIn('"method": "NOT SENT"', self.source)
		self.assertIn('"url": redact_text("[NOT SENT]")', self.source)
		self.assertIn(
			'"not_requested_reason": "private URL absent after response redaction"',
			self.source,
		)
		self.assertRegex(
			self.source,
			r"records\.append\(download_entry\)\s+write_evidence\(\)",
			"case không gửi request vẫn phải được ghi vào `cases` trước assertion",
		)

	def test_file_docname_identifier_is_redacted(self):
		"""Identifier của File (docname) **không** được ghi vào artifact.

		`name` của hồ sơ File là **file identifier**. Dict giống hồ sơ File
		(có `file_name`) ⇒ phải che `name`; dict khác (vd CRM Lead) ⇒ giữ nguyên
		để reviewer còn đối chiếu được tài liệu seed giả.
		"""
		self.assertRegex(
			self.source,
			r'is_file_record\s*=\s*"file_name" in value',
			"phải nhận diện dict giống hồ sơ File để che identifier",
		)
		self.assertRegex(
			self.source,
			r'str\(k\)\.lower\(\)\s*==\s*"name"',
			"phải che `name` của hồ sơ File",
		)

	def test_content_length_is_measured_from_payload(self):
		"""`content_length` phải đo **thật** từ bytes tải về, không hardcode.

		Mutation ``"content_length": 0`` làm case vẫn PASS nhưng **không** đo
		được khả năng tải ⇒ phải bị bắt.

		Kiểm **mọi** chỗ ghi `content_length` trong `download_bytes` (cả entry
		ghi vào artifact lẫn giá trị trả về) đều phải suy ra từ `len(payload)`
		— không được có bất kỳ giá trị hằng nào.
		"""
		func = find_function(self.tree, "download_bytes")
		self.assertIsNotNone(func)
		length_values = [
			value
			for node in ast.walk(func)
			if isinstance(node, ast.Dict)
			for key, value in zip(node.keys, node.values)
			if isinstance(key, ast.Constant) and key.value == "content_length"
		]
		self.assertTrue(length_values, "phải có `content_length` trong download_bytes")
		for value in length_values:
			self.assertNotIsInstance(
				value,
				ast.Constant,
				"content_length phải được tính từ `len(payload)`, không phải hằng số",
			)
		self.assertRegex(
			ast.unparse(func),
			r"content_length.*len\(payload\)",
			"content_length phải đo từ `len(payload)`",
		)

	def test_content_sha256_is_computed_from_payload(self):
		"""`content_sha256` phải **tính** từ bytes tải về, không hardcode/rỗng.

		Mutation ``"content_sha256": ""`` làm mất đặc trưng so sánh ⇒ phải bị bắt.
		"""
		func = find_function(self.tree, "download_bytes")
		self.assertIsNotNone(func)
		sha_values = [
			value
			for node in ast.walk(func)
			if isinstance(node, ast.Dict)
			for key, value in zip(node.keys, node.values)
			if isinstance(key, ast.Constant) and key.value == "content_sha256"
		]
		self.assertTrue(sha_values, "phải có `content_sha256` trong entry")
		for value in sha_values:
			self.assertNotIsInstance(
				value,
				ast.Constant,
				"content_sha256 phải được tính (hashlib.sha256), không hardcode/rỗng",
			)
		self.assertRegex(
			ast.unparse(func),
			r"content_sha256.*hashlib\.sha256\(payload\)",
			"content_sha256 phải tính từ `hashlib.sha256(payload)`",
		)


class TestProbeOutOfScopeMeasurement(unittest.TestCase):
	"""H-RFC-4 — kịch bản ngoài scope phải được đo ở **tầng HTTP**."""

	@classmethod
	def setUpClass(cls):
		cls.source, cls.tree = load_probe()
		cls.check_ids = all_check_ids(cls.tree)

	def test_case_exists(self):
		"""Case ngoài scope phải vừa được request, vừa được assert trong gate."""
		self.assertIn("sec26-activities-out-of-scope", self.source)
		self.assertRegex(
			self.source,
			r'request\(\s*"sec26-activities-out-of-scope"',
			"phải có request() cho case ngoài scope",
		)
		self.assertIn(
			"sec26-activities-out-of-scope",
			self.check_ids,
			"case ngoài scope phải được tính vào failed_cases/gate",
		)

	def test_uses_distinct_out_of_scope_actor(self):
		"""Actor ngoài scope phải **khác** actor in-scope."""
		consts = load_env_consts()
		out_of_scope = consts.get("OUT_OF_SCOPE_USER", "")
		in_scope = consts.get("SEC16_USER", "")
		self.assertTrue(out_of_scope)
		self.assertTrue(in_scope)
		self.assertNotEqual(
			out_of_scope, in_scope, "actor ngoài scope phải khác actor in-scope"
		)
		self.assertNotEqual(
			out_of_scope, "Administrator", "actor ngoài scope phải non-system"
		)

	def test_out_of_scope_case_actually_logs_in_with_out_of_scope_actor(self):
		"""Case ngoài scope phải **login bằng** actor ngoài scope.

		Mutation ``login("staffc", SEC16_USER, ...)`` khiến case ngoài scope thực
		chất chạy bằng actor **đúng scope** ⇒ đo sai kịch bản, phải bị bắt.
		"""
		consts = load_env_consts()
		out_of_scope = consts.get("OUT_OF_SCOPE_USER", "")
		in_scope = consts.get("SEC16_USER", "")
		self.assertTrue(out_of_scope)
		self.assertTrue(in_scope)

		login_calls = [
			ast.unparse(node)
			for node in ast.walk(self.tree)
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "login"
				and len(node.args) >= 2
				and isinstance(node.args[1], (ast.Constant, ast.Name))
			)
		]
		self.assertTrue(login_calls, "phải có lời gọi login() trong probe")
		# `login("staffc", OUT_OF_SCOPE_USER, ...)` — tham số là **tên hằng**,
		# không phải literal ⇒ phải resolve qua bảng hằng module scope.
		out_of_scope_logins = [
			text
			for text in login_calls
			if out_of_scope in text or "OUT_OF_SCOPE_USER" in text
		]
		self.assertTrue(
			out_of_scope_logins,
			"phải login bằng actor ngoài scope %s (thấy: %s)"
			% (out_of_scope, login_calls),
		)
		# Không được login bằng actor **in-scope** rồi dùng làm actor ngoài scope.
		self.assertFalse(
			any(in_scope in text for text in out_of_scope_logins),
			"login của case ngoài scope không được dùng actor in-scope %s" % in_scope,
		)
		self.assertFalse(
			any("SEC16_USER" in text for text in out_of_scope_logins),
			"login của case ngoài scope không được dùng hằng SEC16_USER (actor in-scope)",
		)
		# `request()` cho case ngoài scope phải dùng **opener** của actor ngoài scope.
		for node in ast.walk(self.tree):
			if not (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "request"
				and node.args
				and isinstance(node.args[0], ast.Constant)
				and node.args[0].value == "sec26-activities-out-of-scope"
			):
				continue
			rendered = ast.unparse(node)
			self.assertIn(
				"out_of_scope",
				rendered,
				"case ngoài scope phải dùng opener của actor ngoài scope",
			)
			self.assertNotIn(
				in_scope,
				rendered,
				"case ngoài scope không được dùng opener của actor in-scope",
			)

	def test_out_of_scope_case_calls_same_endpoint(self):
		"""Case ngoài scope phải gọi **cùng** endpoint `get_activities`."""
		found = False
		for node in ast.walk(self.tree):
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "request"
				and node.args
				and isinstance(node.args[0], ast.Constant)
				and node.args[0].value == "sec26-activities-out-of-scope"
			):
				self.assertIn("activities_path", ast.unparse(node))
				found = True
		self.assertTrue(found, "không tìm thấy request() cho case ngoài scope")

	def test_asserts_out_of_scope_permission_denial(self):
		"""Case ngoài scope chỉ PASS khi HTTP từ chối quyền và không lộ file."""
		call = check_call(self.tree, "sec26-activities-out-of-scope")
		self.assertIsNotNone(call, "case ngoài scope phải có assertion trong gate")
		rendered = ast.unparse(call)
		self.assertRegex(
			rendered, r"out_of_scope_activities\[['\"]status['\"]\] == 403"
		)
		self.assertRegex(rendered, r"out_of_scope_exc == ['\"]PermissionError['\"]")
		self.assertIn("not out_of_scope_leaks", rendered)
		self.assertIn("observations", self.source)
		self.assertRegex(
			self.source,
			r'"id"\s*:\s*"sec26-activities-out-of-scope"',
			"phải ghi observation cho kịch bản ngoài scope",
		)

	def test_observation_label_is_derived_from_measured_exc_type(self):
		"""Nhãn quan sát phải **suy ra từ** `exc_type` đo được, không hardcode.

		Mutation `if True` (thay vì `if out_of_scope_exc == "PermissionError"`)
		khiến nhãn `blocked_with_permission_error` được gán **vô điều kiện** ⇒
		bằng chứng có thể nói dối ⇒ phải bị bắt.
		"""
		obs_calls = [
			node
			for node in ast.walk(self.tree)
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "append"
				and node.args
				and isinstance(node.args[0], ast.Dict)
				and any(
					isinstance(key, ast.Constant)
					and key.value == "id"
					and isinstance(value, ast.Constant)
					and value.value == "sec26-activities-out-of-scope"
					for key, value in zip(node.args[0].keys, node.args[0].values)
				)
			)
		]
		self.assertTrue(obs_calls, "phải có observation cho case ngoài scope")
		rendered = ast.unparse(obs_calls[0])
		self.assertRegex(
			rendered,
			r'out_of_scope_exc\s*==\s*[\'"]PermissionError[\'"]',
			"nhãn `blocked_with_permission_error` phải được suy ra từ exc_type đo được",
		)
		self.assertNotRegex(
			rendered,
			r"if\s+True\b",
			"nhãn quan sát không được gán vô điều kiện (phải suy ra từ exc_type)",
		)

	def test_out_of_scope_observation_records_status(self):
		"""Observation phải ghi HTTP status + exc_type để reviewer thấy hành vi thật."""
		self.assertRegex(
			self.source,
			r'"http_status"\s*:\s*out_of_scope_activities\["status"\]',
			"observation phải ghi HTTP status thật",
		)
		self.assertRegex(
			self.source,
			r'"exc_type"\s*:\s*out_of_scope_exc',
			"observation phải ghi exc_type thật",
		)


class TestProbeRedactionCoversRequestUrl(unittest.TestCase):
	"""`request.url` cũng phải qua redact — URL private có identifier file."""

	def test_request_url_is_redacted(self):
		source, _ = load_probe()
		self.assertRegex(
			source,
			r'"url"\s*:\s*redact_text\(url\)',
			"request.url phải qua redact_text để không lộ identifier file",
		)

	def test_download_helper_redacts_url(self):
		source, _ = load_probe()
		self.assertIn(
			'"url": redact_text(url)',
			source,
			"download_bytes cũng phải redact URL trước khi ghi vào artifact",
		)

	def test_every_url_written_to_artifact_is_redacted(self):
		"""**Mọi** chỗ ghi `url` vào artifact phải qua `redact_text`.

		Regex `assertIn('"url": redact_text(url)')` của test cũ chỉ bắt được
		**một** occurrence ⇒ mutation đổi occurrence trong `download_bytes`
		(thành `url` thô) vẫn lọt. Test này kiểm **toàn bộ** dict entry trong
		probe: mọi key `url` phải có value gọi `redact_text(...)`.
		"""
		tree = load_probe()[1]
		offenders: list[str] = []
		total = 0
		for node in ast.walk(tree):
			if not isinstance(node, ast.Dict):
				continue
			for key, value in zip(node.keys, node.values):
				if not (isinstance(key, ast.Constant) and key.value == "url"):
					continue
				total += 1
				if not (
					isinstance(value, ast.Call)
					and isinstance(value.func, ast.Name)
					and value.func.id == "redact_text"
				):
					offenders.append(ast.unparse(value))
		self.assertTrue(total, "phải có ít nhất một key `url` trong artifact")
		self.assertEqual(
			offenders,
			[],
			"mọi `url` ghi vào artifact phải qua redact_text(); vi phạm: %s"
			% offenders,
		)


class TestProbeSyntax(unittest.TestCase):
	def test_probe_parses(self):
		source, tree = load_probe()
		self.assertIsInstance(tree, ast.Module)
		self.assertTrue(source.strip())

	def test_probe_has_fail_closed_exit(self):
		"""Probe phải trả exit khác 0 khi có case fail."""
		source, tree = load_probe()
		self.assertIn("return 1 if failures else 0", source)


class TestRemainingP0GapsProbe(unittest.TestCase):
	"""Static safety and coverage guard for the P0 closure probe."""

	def load_source(self):
		path = HERE / "ci_g007_remaining_gaps_probe.py"
		source = path.read_text(encoding="utf-8")
		return source, ast.parse(source, filename=str(path))

	def test_probe_parses_and_records_only_sanitized_summaries(self):
		source, tree = self.load_source()
		self.assertIsInstance(tree, ast.Module)
		self.assertIn('"body_length"', source)
		self.assertIn('"body_sha256"', source)
		self.assertNotIn('"body": body', source)
		self.assertNotIn('"response_body":', source)

	def test_probe_covers_the_remaining_matrix_and_file_routes(self):
		source, _ = self.load_source()
		for marker in (
			"cross-doctype-get-data-vs-get-list",
			"/api/method/crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log",
			"at06-private-download-",
			"sec26-private-download-out-of-scope-existing-vs-unknown",
			'for linked_doctype in ("FCRM Note", "Comment")',
			"nt04_create_doc_case(",
		):
			with self.subTest(marker=marker):
				self.assertIn(marker, source)

	def test_nt04_create_probe_is_fail_closed_and_cleans_unexpected_rows(self):
		source, _ = self.load_source()
		self.assertIn("def nt04_create_doc_case(", source)
		self.assertIn('"/api/method/frappe.client.insert"', source)
		self.assertIn('"/api/method/frappe.client.delete"', source)
		self.assertIn('and insert_status == 403', source)
		self.assertIn('insert_exc_type.endswith("PermissionError")', source)
		self.assertIn('"matching_rows_before_cleanup"', source)
		self.assertIn('"matching_rows_after_cleanup"', source)
		self.assertIn('"cleanup_verified": cleanup_ok', source)
		self.assertIn('supplied_markers=(LEAD_NAME, marker, inserted_name or "")', source)
		self.assertNotIn('"marker": marker', source)
		self.assertNotIn('"response_body":', source)

	def test_probe_has_twelve_permlevel_surfaces_and_positive_controls(self):
		source, tree = self.load_source()
		endpoint_specs = [
			node.value
			for node in ast.walk(tree)
			if isinstance(node, ast.Assign)
			and any(
				isinstance(target, ast.Name) and target.id == "endpoint_specs"
				for target in node.targets
			)
			and isinstance(node.value, ast.Dict)
		]
		self.assertEqual(len(endpoint_specs), 1)
		self.assertEqual(len(endpoint_specs[0].keys), 12)
		self.assertIn("FIELD_SENTINEL.encode() in body", source)
		self.assertIn("sec16-system-positive-control-", source)

	def test_probe_does_not_serialize_credentials_or_private_url_values(self):
		source, _ = self.load_source()
		self.assertIn('"private_file_url_present"', source)
		self.assertIn('"sentinel_present"', source)
		self.assertNotIn('"cookie":', source)
		self.assertNotIn('"password":', source)
		self.assertNotIn('"csrf_token":', source)

	def test_probe_restores_the_synthetic_field_after_measurement(self):
		source, _ = self.load_source()
		self.assertIn("def cleanup_permlevel_field()", source)
		self.assertIn('"sec16-field-cleanup"', source)
		self.assertIn("atexit.register(cleanup_permlevel_field)", source)
		self.assertIn("atexit.unregister(cleanup_permlevel_field)", source)
		self.assertIn("cleanup_ok = cleanup_permlevel_field()", source)


if __name__ == "__main__":
	if hasattr(sys.stdout, "reconfigure"):
		sys.stdout.reconfigure(encoding="utf-8", errors="replace")
	unittest.main(verbosity=2)
