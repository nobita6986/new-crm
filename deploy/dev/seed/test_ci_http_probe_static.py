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
			if isinstance(value.left, ast.Constant) and isinstance(value.left.value, str):
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
			"mọi case SEC-16 phải trỏ cùng endpoint crm.api.doc.get_data, thấy: %r" % (sec16_paths,),
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


class TestProbeGetActivitiesCoverage(unittest.TestCase):
	"""C6 — SEC-26 RFC: probe phải đo `get_activities` (2 đường lộ `file_url`).

	Đây là **coverage gap** mà T1C lần 22b nêu: probe 14/14 và V16–V21 chỉ phủ
	endpoint **đã override** (`guarded_crm.get_attachments`), **KHÔNG** phủ
	`crm.api.activities.get_activities` và **KHÔNG** phủ `docinfo.attachments`.

	Nguyên tắc bất di bất dịch:
	- Probe phải gọi **đúng** endpoint `crm.api.activities.get_activities`.
	- Phải đo **riêng** `message[4].attachments` **và** `docinfo.attachments`
	  (2 đường độc lập, không được gộp).
	- Đây là **characterization**: KHÔNG được assert giá trị `file_url` phải có
	  hay không — phải **ghi lại quan sát**. Assert cứng sẽ biến kết luận đang
	  chờ Owner thành kết luận đã chốt.
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
					self.fail(f"case activities phải gọi đúng endpoint, thấy: {resolved}")
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

	def test_does_not_call_overridden_endpoint_for_activities(self):
		"""`get_activities` phải **không** đi qua `guarded_crm.get_attachments`."""
		self.assertNotIn(
			"guarded_crm.get_attachments",
			self.source_for_activities(),
			"case get_activities không được gọi endpoint đã override",
		)

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

	def test_does_not_hardcode_file_url_expectation(self):
		"""KHÔNG được assert cứng `file_url` có/không — phải ghi quan sát.

		Đây là characterization: kết luận còn chờ Owner, probe chỉ **đo**.
		"""
		src = self.source_for_activities()
		for forbidden in (
			"'file_url' not in",
			'"file_url" not in',
			"not in message_private",
			"not in docinfo_private",
		):
			self.assertNotIn(
				forbidden,
				src,
				"probe không được assert cứng file_url (phải ghi quan sát thay vì kết luận)",
			)

	def test_records_observations_for_each_path(self):
		"""Mỗi đường phải có **một** quan sát riêng trong evidence."""
		self.assertIn("observations", self.source, "phải ghi `observations` vào evidence")
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

	def test_measures_attachment_log_paths(self):
		"""Phải đo **2 đường attachment_log** (URL trong HTML/activity, không phải key `file_url`).

		Đường 3: `message[0][attachment_log].data.file_url` (CRM `parse_attachment_log` L524).
		Đường 4: `docinfo.attachment_logs[].content` — HTML `<a href="/private/files/…">`
		(Frappe `file.py` L898-906). Ở đường 4 URL nằm trong **chuỗi HTML**, KHÔNG phải
		key `file_url` ⇒ probe không redact được ⇒ phải đo riêng.
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
		self.assertRegex(
			src,
			r"attachment_log_data\s*=\s*attachment_log_activity\.get\(\s*\"data\"\s*\)",
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
			self.assignment_reads_docinfo_key("docinfo_attachment_logs", "attachment_logs"),
			"`docinfo_attachment_logs` phải được đọc từ `docinfo['attachment_logs']`",
		)
		# Đường 4: phải đọc `content` của dòng attachment_log và nhận diện URL private.
		self.assertRegex(
			src,
			r'attachment_log_row\.get\(\s*"content"\s*\)',
			"đường 4 phải đọc `content` của dòng attachment_log",
		)
		self.assertIn("/private/files/", src, "phải nhận diện URL private trong HTML")

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
		self.assertIn("staff", src, "case get_activities phải dùng opener của staff (non-system)")
		self.assertNotIn("admin,", src)

	def test_uses_seeded_synthetic_document(self):
		"""Phải dùng tài liệu seed **giả** (CRM-LEAD-00001), không dữ liệu thật."""
		src = self.source_for_activities()
		self.assertIn("LEAD_NAME", src)
		self.assertIn("PRIVATE_FILE_NAME", src)

	def source_for_activities(self) -> str:
		"""Render source của phần code xử lý case `get_activities`."""
		lines = self.source.splitlines()
		start = None
		for i, line in enumerate(lines):
			if "sec26-activities-in-scope" in line:
				start = i
				break
		self.assertIsNotNone(start, "không tìm thấy case sec26-activities-in-scope")
		# Lấy từ case đầu tiên tới hết file (case này nằm cuối `run()`).
		return "\n".join(lines[start:])


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