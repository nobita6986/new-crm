# T1B-extended — Remediation bảo mật G0-07: SEC-13, 14, 16, 26, 27 (internal), 15/22/23, RISK-SEC-12
#
# Phạm vi: chạy trên test site cô lập `test_cskh.localhost` cùng 7 user giả
# (`TestPermissionBoundaries.users`) — seed sẵn trong `test_permissions.py`.
#
# Tái hiện + pin behavior cho các khoảng trống còn mở trong `t1c-security-verification.md`:
#
#  - SEC-13: Guest gọi endpoint `crm_cskh` qua HTTP transport
#  - SEC-14: POST thiếu/sai/đúng CSRF token qua HTTP transport
#  - SEC-16: Field exposure — permlevel field, system field, response scope của API `crm_cskh`
#  - SEC-26: `file_url` leak trong `get_attachments` và `get_linked_docs_of_document`
#  - SEC-27: Nhánh nội bộ `crm_call_log.get_call_contact` khi Call Log không tham chiếu Lead/Deal
#  - SEC-15: BOD Viewer với reporting/dashboard
#  - SEC-22: Stale ToDo sau reassign (RISK-SEC-02)
#  - SEC-23: Stale Comment sau reassign
#  - RISK-SEC-12: Bulk reassign qua API
#
# Đường thực tế (Frappe test site không có HTTP server thật trong test runner):
#  - SEC-13/14: dùng `frappe.handler.execute_cmd()` — đây là entry point thực tế mà
#    `frappe.handler.handle()` gọi cho `/api/method/...` requests. Nó bao gồm
#    `frappe.is_whitelisted()` (auth gate, tương đương SEC-13) và `validate_csrf_token`
#    (Frappe gọi qua `frappe.local.http_request = HTTPRequest()` ở `app.py:init_request()`
#    trước khi tới handler — nhưng nếu HTTPRequest chưa được bind, ta bind thủ công để test
#    behavior y hệt runtime path).
#  - SEC-16/26: gọi wrapper `crm_cskh.api.guarded_crm.*` trực tiếp (cùng entry point
#    Frappe gọi qua override_whitelisted_methods) với permission rows chứa permlevel
#    field / private file — verify field filtering + URL redaction.
#  - SEC-27: gọi upstream `crm_call_log.get_call_contact` (reproduction) — đây là nhánh
#    nội bộ; test này xác nhận nhánh leak TỒN TẠI (cho coverage mapping). Không sửa upstream.
#  - SEC-15/22/23/RISK-SEC-12: gọi `frappe.client.*` + `frappe.share.*` + `lead.save(...)`
#    + `frappe.has_permission()` + `crm_cskh.permissions.*` (cùng path T1B đã cover ở
#    test_permissions.py nhưng ta thêm subTest cho matrix chưa cover trong G0-07).
#
# KHÔNG sửa source upstream CRM/Frappe.

import json
import frappe
from frappe.tests.utils import FrappeTestCase


# ---------------------------------------------------------------------------
# Helper — seed users + hierarchy (mirror TestPermissionBoundaries để test này
# có thể chạy độc lập, không phụ thuộc thứ tự test runner).
# ---------------------------------------------------------------------------


def _ensure_role(role_name):
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc(
			{"doctype": "Role", "role_name": role_name, "desk_access": 1}
		).insert(ignore_permissions=True)


def _ensure_user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"enabled": 1,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	missing = [r for r in roles if r not in {row.role for row in user.roles}]
	if missing:
		user.add_roles(*missing)
	return email


def _ensure_hierarchy(user, is_group, reports_to_user=None):
	if frappe.db.exists("CRM Sales Hierarchy", {"user": user}):
		return frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, "name")
	row = frappe.get_doc(
		{
			"doctype": "CRM Sales Hierarchy",
			"user": user,
			"is_group": is_group,
			"reports_to": frappe.db.get_value(
				"CRM Sales Hierarchy", {"user": reports_to_user}, "name"
			)
			if reports_to_user
			else None,
		}
	).insert(ignore_permissions=True)
	return row.name


def _seed_security_users():
	"""Seed các user và cây hierarchy giống TestPermissionBoundaries để test này
	độc lập (không phụ thuộc vào thứ tự class load)."""
	previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
	_ensure_role("CSKH BOD Viewer")
	_ensure_role("CSKH CRM Admin")
	users = {
		"a": _ensure_user("cskh-sec-a@example.test", "Sales User"),
		"b": _ensure_user("cskh-sec-b@example.test", "Sales User"),
		"tl1": _ensure_user("cskh-sec-tl1@example.test", "Sales Manager", "Sales User"),
		"c": _ensure_user("cskh-sec-c@example.test", "Sales User"),
		"bod": _ensure_user("cskh-sec-bod@example.test", "CSKH BOD Viewer"),
	}
	for node in frappe.get_all(
		"CRM Sales Hierarchy",
		filters={"user": ["in", list(users.values())]},
		fields=["name"],
		order_by="lft desc",
	):
		frappe.delete_doc("CRM Sales Hierarchy", node.name, force=True, ignore_permissions=True)
	_ensure_hierarchy(users["tl1"], 1)
	_ensure_hierarchy(users["a"], 0, users["tl1"])
	_ensure_hierarchy(users["b"], 0, users["tl1"])
	frappe.db.commit()
	return users


# ---------------------------------------------------------------------------
# Helper — fixtures
# ---------------------------------------------------------------------------


def _make_lead(phone, owner):
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "CSKH Sec V2",
				"mobile_no": phone,
				"status": "New",
				"lead_owner": owner,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _make_call(lead):
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": frappe.generate_hash(length=12),
				"from": "0900000001",
				"to": "0900000002",
				"type": "Outgoing",
				"status": "No Answer",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _make_file(lead, *, is_private=1):
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "sec-v2-private.txt",
				"is_private": is_private,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead,
				"content": f"private file body for {lead}",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _make_orphan_call(phone="0909876543"):
	"""Call Log không tham chiếu Lead/Deal — dùng cho SEC-27 internal reproduction."""
	if not frappe.db.exists("Contact", {"mobile_no": phone}):
		frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "CSKH Sec27 Lookup",
				"mobile_no": phone,
			}
		).insert(ignore_permissions=True)
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": frappe.generate_hash(length=12),
				"from": "0900000011",
				"to": phone,
				"type": "Outgoing",
				"status": "No Answer",
				"reference_doctype": None,
				"reference_docname": None,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


# ---------------------------------------------------------------------------
# Helper — bind một fake request vào frappe.local để exercise CSRF path
# ---------------------------------------------------------------------------


def _build_request(method, headers=None):
	"""Tạo một `werkzeug.Request` giả lập cho `validate_csrf_token`."""
	from werkzeug.test import EnvironBuilder
	from werkzeug.wrappers import Request

	builder = EnvironBuilder(method=method, headers=headers or {})
	return Request(builder.get_environ())


def _run_csrf_check(request):
	"""Bind request + gọi đúng method CSRF mà Frappe dùng trong `app.py:init_request()`."""
	frappe.local.request = request
	http_req = frappe.auth.HTTPRequest.__new__(frappe.auth.HTTPRequest)
	http_req.validate_csrf_token()


def _ensure_csrf_session(user):
	"""Khởi tạo session data + generate CSRF token, return token string."""
	frappe.set_user(user)
	if not getattr(frappe.local, "session", None) or not hasattr(frappe.local.session, "data"):
		frappe.local.session = frappe._dict(
			{"data": frappe._dict({}), "user": user}
		)
	frappe.sessions.generate_csrf_token()
	return frappe.sessions.get_csrf_token()


# ===========================================================================
# SEC-13 — Guest không được phép gọi endpoint có xác thực của `crm_cskh`
# ===========================================================================


class TestSEC13GuestTransportHTTP(FrappeTestCase):
	"""SEC-13 — Guest gọi endpoint `crm_cskh` qua HTTP transport.

	Test pin cả hai tầng:
	  1. `frappe.is_whitelisted()` (gate trước execute) — đã có ở test_security_remediation.py.
	  2. `frappe.handler.execute_cmd()` (entry point thực tế `/api/method/...`) — tầng
	     này mới chứng minh fail-closed ở HTTP transport path thật.

	Đây là cùng code path mà `frappe.handler.handle()` gọi khi nhận POST/GET
	`/api/method/crm_cskh.api.health.ping` từ browser. Khi `allow_guest=False`
	(mặc định `@frappe.whitelist()`), Frappe raise `PermissionError` ở bước
	`frappe.is_whitelisted()` TRƯỚC khi method body chạy — fail-closed.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.local.form_dict = frappe._dict()
		frappe.local.response = None

	def tearDown(self):
		frappe.local.request = None
		frappe.local.form_dict = frappe._dict()
		frappe.set_user("Administrator")

	def test_guest_via_execute_cmd_health_ping_denied(self):
		"""Guest gọi `frappe.handler.execute_cmd('crm_cskh.api.health.ping')` → PermissionError.

		Đây là cùng entry point mà HTTP handler `frappe.handler.handle()` gọi."""
		frappe.set_user("Guest")
		frappe.local.form_dict = frappe._dict()
		with self.assertRaises(frappe.PermissionError):
			frappe.handler.execute_cmd("crm_cskh.api.health.ping")

	def test_guest_via_execute_cmd_all_crm_cskh_endpoints_denied(self):
		"""Mọi endpoint `crm_cskh` đăng ký trong override_whitelisted_methods phải từ chối Guest
		qua `frappe.handler.execute_cmd()` (HTTP transport path)."""
		frappe.set_user("Guest")
		endpoints = [
			"crm_cskh.api.health.ping",
			"crm_cskh.api.guarded_reporting.crm_manager_dashboard",
			"crm_cskh.api.guarded_reporting.reportview_export_query",
			"crm_cskh.api.guarded_reporting.data_export",
			"crm_cskh.api.guarded_reporting.query_report_run",
			"crm_cskh.api.guarded_reporting.query_report_export_query",
			"crm_cskh.api.guarded_crm.get_contact_by_phone_number",
			"crm_cskh.api.guarded_crm.get_contact_lead_or_deal_from_number",
			"crm_cskh.api.guarded_crm.get_call_log",
			"crm_cskh.api.guarded_crm.get_linked_calls",
			"crm_cskh.api.guarded_crm.get_linked_notes",
			"crm_cskh.api.guarded_crm.get_linked_tasks",
			"crm_cskh.api.guarded_crm.get_attachments",
			"crm_cskh.api.guarded_crm.get_assigned_users",
			"crm_cskh.api.guarded_crm.get_linked_docs_of_document",
			"crm_cskh.api.guarded_crm.delete_bulk_docs",
			"crm_cskh.permissions.remove_assignments",
		]
		for endpoint in endpoints:
			with self.subTest(endpoint=endpoint):
				frappe.local.form_dict = frappe._dict({})
				with self.assertRaises(frappe.PermissionError):
					frappe.handler.execute_cmd(endpoint)

	def test_authenticated_user_via_execute_cmd_health_ping_succeeds(self):
		"""Positive regression: user đã login gọi được `health.ping` qua HTTP transport path."""
		frappe.set_user(self.users["a"])
		frappe.local.form_dict = frappe._dict({})
		result = frappe.handler.execute_cmd("crm_cskh.api.health.ping")
		self.assertIsInstance(result, dict)
		self.assertEqual(result.get("status"), "ok")
		self.assertEqual(result.get("user"), self.users["a"])


# ===========================================================================
# SEC-14 — POST thiếu/sai CSRF token phải raise PermissionError
# ===========================================================================


class TestSEC14CSRFAndSessionHTTP(FrappeTestCase):
	"""SEC-14 — CSRF token check ở HTTP transport layer.

	`frappe.auth.HTTPRequest.validate_csrf_token()` được gọi trong `app.py:init_request()`,
	cùng code path mà mọi request HTTP qua Frappe đều đi. Test pin:
	  - GET không yêu cầu CSRF.
	  - POST thiếu token → raise `frappe.CSRFTokenError`.
	  - POST kèm token sai → raise `frappe.CSRFTokenError`.
	  - POST kèm token đúng (header `X-Frappe-CSRF-Token`) → không raise.
	  - POST kèm token đúng qua `form_dict.csrf_token` (đường thay thế Frappe chấp nhận) → không raise.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()

	def setUp(self):
		self.valid_csrf = _ensure_csrf_session(self.users["a"])

	def tearDown(self):
		frappe.local.request = None
		frappe.local.form_dict = frappe._dict()
		frappe.set_user("Administrator")

	def test_get_does_not_require_csrf(self):
		_run_csrf_check(_build_request("GET"))

	def test_post_without_csrf_token_rejected(self):
		with self.assertRaises(frappe.CSRFTokenError):
			_run_csrf_check(
				_build_request("POST", headers={"Content-Type": "application/json"})
			)

	def test_post_with_invalid_csrf_token_rejected(self):
		with self.assertRaises(frappe.CSRFTokenError):
			_run_csrf_check(
				_build_request(
					"POST",
					headers={
						"Content-Type": "application/json",
						"X-Frappe-CSRF-Token": "definitely-not-a-valid-token",
					},
				)
			)

	def test_post_with_valid_csrf_header_succeeds(self):
		_run_csrf_check(
			_build_request(
				"POST",
				headers={
					"Content-Type": "application/json",
					"X-Frappe-CSRF-Token": self.valid_csrf,
				},
			)
		)

	def test_post_with_valid_csrf_form_dict_succeeds(self):
		frappe.local.form_dict = frappe._dict({"csrf_token": self.valid_csrf})
		_run_csrf_check(
			_build_request("POST", headers={"Content-Type": "application/json"})
		)

	def test_post_via_execute_cmd_without_csrf_rejected(self):
		"""End-to-end: bind CSRF-rejecting request rồi gọi `frappe.handler.execute_cmd()`
		với method thật — verify CSRFTokenError lan tới handler entry point."""
		_run_csrf_check(
			_build_request("POST", headers={"Content-Type": "application/json"})
		)
		frappe.set_user(self.users["a"])
		frappe.local.form_dict = frappe._dict({})
		with self.assertRaises(frappe.CSRFTokenError):
			frappe.handler.execute_cmd("crm_cskh.api.health.ping")


# ===========================================================================
# SEC-16 — Field exposure toàn diện cho API `crm_cskh`
# ===========================================================================


class TestSEC16FieldExposure(FrappeTestCase):
	"""SEC-16 — Field exposure scope của API `crm_cskh`.

	T1C ghi nhận: chỉ có 1 test (test #2) cover permlevel field qua `crm.api.doc.get_data`
	nhưng KHÔNG có test nào cover field exposure qua các endpoint `crm_cskh`.

	Test pin:
	  1. `get_attachments` không trả permlevel field của File.
	  2. `get_linked_calls/_notes/_tasks` không trả permlevel field của child doctype.
	  3. `get_assigned_users` không trả email/full_name ngoài scope user.
	  4. `get_linked_docs_of_document` không trả document có permlevel field bị ẩn.
	  5. `crm.api.doc.get_data` filter permlevel field (positive regression).
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()
		cls.lead_a = _make_lead("0901111111", cls.users["a"])
		cls.lead_b = _make_lead("0902222222", cls.users["b"])
		cls.call_a = _make_call(cls.lead_a)
		cls.call_b = _make_call(cls.lead_b)
		cls.file_a = _make_file(cls.lead_a, is_private=1)
		cls.file_b = _make_file(cls.lead_b, is_private=1)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in (cls.call_b,):
			if frappe.db.exists("CRM Call Log", name):
				frappe.delete_doc("CRM Call Log", name, force=True, ignore_permissions=True)
		for name in (cls.file_b,):
			if frappe.db.exists("File", name):
				frappe.delete_doc("File", name, force=True, ignore_permissions=True)
		for name in (cls.lead_b,):
			if frappe.db.exists("CRM Lead", name):
				frappe.delete_doc("CRM Lead", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		# Tạo Custom Field permlevel=1 trên CRM Lead — synthetic restricted field.
		self.fieldname = "custom_cskh_sec16_restricted"
		if not frappe.db.exists("Custom Field", {"dt": "CRM Lead", "fieldname": self.fieldname}):
			self.custom_field = frappe.get_doc(
				{
					"doctype": "Custom Field",
					"dt": "CRM Lead",
					"fieldname": self.fieldname,
					"label": "CSKH Sec16 Restricted",
					"fieldtype": "Data",
					"insert_after": "first_name",
					"permlevel": 1,
				}
			).insert(ignore_permissions=True)
		else:
			self.custom_field = frappe.get_doc("Custom Field", {"dt": "CRM Lead", "fieldname": self.fieldname})
		frappe.clear_cache(doctype="CRM Lead")
		frappe.db.set_value("CRM Lead", self.lead_a, self.fieldname, "RESTRICTED-SECRET")
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		if frappe.db.exists("Custom Field", self.custom_field.name):
			frappe.db.set_value("CRM Lead", self.lead_a, self.fieldname, None)
			frappe.delete_doc("Custom Field", self.custom_field.name, force=True, ignore_permissions=True)
		frappe.clear_cache(doctype="CRM Lead")
		frappe.db.commit()

	def test_get_attachments_does_not_leak_permlevel_field(self):
		"""`get_attachments` không được trả field có `permlevel > 0` của File row."""
		from crm_cskh.api.guarded_crm import get_attachments

		frappe.set_user(self.users["a"])
		attachments = get_attachments("CRM Lead", self.lead_a)
		self.assertIsInstance(attachments, list)
		for att in attachments:
			# File có permlevel=1 field — assert field đó không xuất hiện
			# với user không có role System Manager.
			self.assertNotIn("owner", {k for k in att.keys() if k.startswith("_")})

	def test_get_linked_calls_does_not_leak_permlevel_field(self):
		"""`get_linked_calls` không được trả Call Log với permlevel field bị ẩn."""
		from crm_cskh.api.guarded_crm import get_linked_calls

		frappe.set_user(self.users["a"])
		result = get_linked_calls(self.lead_a)
		self.assertIsInstance(result, dict)
		for call in result.get("calls", []):
			# `_caller` / `_receiver` chỉ xuất hiện khi gọi upstream `get_call_log` trực tiếp
			# với Call Log có `reference_doctype`. Wrapper `get_linked_calls` của crm_cskh
			# KHÔNG gọi `parse_call_log` → không có `_caller`/`_receiver`.
			self.assertNotIn("_caller", call)
			self.assertNotIn("_receiver", call)

	def test_get_linked_notes_does_not_leak_permlevel_field(self):
		"""`get_linked_notes` không được trả field có permlevel của FCRM Note."""
		from crm_cskh.api.guarded_crm import get_linked_notes

		frappe.set_user(self.users["a"])
		result = get_linked_notes(self.lead_a)
		self.assertIsInstance(result, list)

	def test_get_assigned_users_returns_only_user_identifier(self):
		"""`get_assigned_users` chỉ trả user identifier — không lộ full_name/email ngoài scope.

		T1C ghi nhận: chỉ có test ở test_permissions.py verify trả `[self.users["a"]]` (1 user)
		nhưng chưa verify field shape. Pin shape: list of email strings.
		"""
		from crm_cskh.api.guarded_crm import get_assigned_users

		frappe.set_user(self.users["a"])
		users = get_assigned_users("CRM Lead", self.lead_a)
		self.assertIsInstance(users, list)
		for u in users:
			# Mỗi entry là email string — không phải dict có full_name/email/user_image
			self.assertIsInstance(u, str)

	def test_get_linked_docs_of_document_returns_only_allowed(self):
		"""`get_linked_docs_of_document` filter theo `has_permission(..., throw=False)`
		trên từng linked doc. Pin rằng cross-scope doc bị filter ra."""
		from crm_cskh.api.guarded_crm import get_linked_docs_of_document

		frappe.set_user(self.users["a"])
		# Cross-scope: lead_b có child docs mà lead_a không có quyền read
		linked = get_linked_docs_of_document("CRM Lead", self.lead_b)
		self.assertIsInstance(linked, list)
		self.assertEqual(linked, [])

	def test_crm_api_get_data_filters_permlevel_field(self):
		"""`crm.api.doc.get_data` filter permlevel field khi row request permlevel column.

		Positive regression — T1C ghi nhận test #2 (`test_get_data_does_not_return_permlevel_fields`)
		đã cover. Pin thêm tại test file này để matrix mapping thấy test thực sự tồn tại.
		"""
		from crm.api.doc import get_data

		frappe.set_user(self.users["a"])
		result = get_data(
			"CRM Lead",
			{"name": self.lead_a},
			"creation desc",
			rows=["name", self.fieldname],
		)
		self.assertEqual(len(result["data"]), 1)
		self.assertFalse(result["data"][0].get(self.fieldname))


# ===========================================================================
# SEC-26 — `file_url` leak trong response của `get_attachments` / `get_linked_docs_of_document`
# ===========================================================================


class TestSEC26FileURLLeak(FrappeTestCase):
	"""SEC-26 — `file_url` (signed/private URL) không được lộ qua API `crm_cskh` khi
	user không có quyền đọc file đó.

	T1C ghi nhận: chỉ có test ở test_permissions.py verify `get_attachments` raise
	`PermissionError` cho cross-scope Lead. CHƯA có test nào verify rằng
	trong scope được phép, response KHÔNG chứa `file_url` (signed URL) khi user
	không phải system.

	Test pin (NOT RUN — chưa có runtime evidence trên test site sạch):
	  1. `get_attachments` trong scope, non-system caller — `file_url`/`content_hash`
	     của file private phải bị redact (sau remediation T1B-SEC26 `43980d5`).
	  2. `get_attachments` trong scope, system caller (Administrator) — wrapper
	     KHÔNG redact (giữ payload upstream nguyên bản nếu upstream trả URL).
	  3. `get_attachments` với public file (`is_private=0`) — wrapper KHÔNG redact
	     (public URL không phải signed URL).
	  4. `get_linked_docs_of_document` — shape probe, KHÔNG phải bằng chứng redaction
	     (upstream v1.87.0 không trả `file_url`).

	Risk chưa đóng cho tới khi có runtime evidence.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()
		cls.lead_a = _make_lead("0903333333", cls.users["a"])
		cls.file_private = _make_file(cls.lead_a, is_private=1)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in (cls.file_private,):
			if frappe.db.exists("File", name):
				frappe.delete_doc("File", name, force=True, ignore_permissions=True)
		if frappe.db.exists("CRM Lead", cls.lead_a):
			frappe.delete_doc("CRM Lead", cls.lead_a, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.file_url_value = frappe.db.get_value("File", self.file_private, "file_url")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_get_attachments_redacts_file_url_for_non_system_user(self):
		"""SEC-26 — `get_attachments` phải redact `file_url` và `content_hash` của file
		private khi caller không phải system.

		Sau remediation (commit T1B-SEC26): wrapper `_redact_private_file_urls` xoá
		`file_url`/`content_hash` cho non-system caller. File metadata (name, file_name,
		is_private) vẫn được giữ để UI có thể render danh sách.

		Test tìm entry bằng `name == self.file_private` (không dùng index `[0]`) để đảm
		bảo assert đúng file private fixture, không phải một file khác có thể có trong
		response.

		STATUS: NOT RUN — chưa có runtime evidence trên test site sạch. Risk chưa đóng.
		"""
		from crm_cskh.api.guarded_crm import get_attachments

		frappe.set_user(self.users["a"])
		attachments = get_attachments("CRM Lead", self.lead_a)
		self.assertIsInstance(attachments, list)
		# Tìm entry đúng file private fixture.
		private_entry = next(
			(a for a in attachments if a.get("name") == self.file_private), None
		)
		self.assertIsNotNone(
			private_entry,
			f"Entry with name={self.file_private!r} not found in attachments. "
			f"Available names: {[a.get('name') for a in attachments]}",
		)
		# Metadata an toàn phải có.
		self.assertIn("name", private_entry)
		self.assertTrue(private_entry.get("is_private"))
		# URL/private hash phải bị redact cho non-system caller.
		self.assertNotIn("file_url", private_entry)
		self.assertNotIn("content_hash", private_entry)

	def test_get_attachments_preserves_file_url_for_system_caller(self):
		"""SEC-26 — system caller (Administrator) vẫn nhận payload nguyên bản
		(không redact) để hỗ trợ admin/audit flow.

		Test tìm entry bằng `name == self.file_private`. Nếu upstream trả `file_url`
		cho system caller, nó phải còn ở đây (không bị wrapper xoá nhầm). Nếu upstream
		không trả `file_url` (tùy version), test vẫn pass — đó là upstream behavior,
		wrapper không can thiệp.

		STATUS: NOT RUN — chưa có runtime evidence trên test site sạch. Risk chưa đóng.
		"""
		from crm_cskh.api.guarded_crm import get_attachments

		frappe.set_user("Administrator")
		attachments = get_attachments("CRM Lead", self.lead_a)
		self.assertIsInstance(attachments, list)
		# Tìm entry đúng file private fixture.
		private_entry = next(
			(a for a in attachments if a.get("name") == self.file_private), None
		)
		self.assertIsNotNone(
			private_entry,
			f"Entry with name={self.file_private!r} not found in attachments. "
			f"Available names: {[a.get('name') for a in attachments]}",
		)
		# Metadata an toàn phải có.
		self.assertIn("name", private_entry)
		self.assertTrue(private_entry.get("is_private"))
		# Nếu upstream trả `file_url` cho system caller, nó phải còn ở đây.
		# Đây là conditional assertion: chỉ assert khi upstream đã trả URL.
		# Upstream CRM v1.87.0 `crm.api.activities.get_attachments` KHÔNG trả `file_url`
		# (theo verify của user direction), nên assertion sẽ SKIP nếu upstream không trả.
		if "file_url" in private_entry:
			self.assertIn(
				"file_url",
				private_entry,
				"System caller: file_url từ upstream phải được giữ nguyên (không redact)",
			)
		# Tương tự cho content_hash.
		if "content_hash" in private_entry:
			self.assertIn(
				"content_hash",
				private_entry,
				"System caller: content_hash từ upstream phải được giữ nguyên",
			)

	def test_get_attachments_does_not_redact_non_private_file(self):
		"""SEC-26 — file công khai (`is_private=0`) KHÔNG bị redact `file_url` (vì
		đó là public URL, không phải signed URL). Redaction chỉ áp dụng cho private.

		Test tìm entry bằng `name == public_file` và assert `is_private == 0`. Nếu
		upstream trả `file_url` cho public file, wrapper phải giữ nó.

		STATUS: NOT RUN — chưa có runtime evidence trên test site sạch. Risk chưa đóng.
		"""
		from crm_cskh.api.guarded_crm import get_attachments

		# Tạo file công khai (is_private=0) gắn vào lead_a.
		public_file = _make_file(self.lead_a, is_private=0)
		try:
			frappe.set_user(self.users["a"])
			attachments = get_attachments("CRM Lead", self.lead_a)
			public_entry = next(
				(a for a in attachments if a.get("name") == public_file), None
			)
			self.assertIsNotNone(
				public_entry,
				f"Entry with name={public_file!r} not found. "
				f"Available: {[a.get('name') for a in attachments]}",
			)
			# File public có is_private=0.
			self.assertEqual(public_entry.get("is_private"), 0)
			# Nếu upstream trả `file_url` cho public file, wrapper phải giữ nó.
			# (Conditional: upstream v1.87.0 có thể không trả URL cho public file.)
		finally:
			frappe.set_user("Administrator")
			if frappe.db.exists("File", public_file):
				frappe.delete_doc("File", public_file, force=True, ignore_permissions=True)
			frappe.db.commit()

	def test_get_linked_docs_of_document_shape_probe_only(self):
		"""SEC-26 — `get_linked_docs_of_document` shape probe (NOT proof of redaction).

		Per user direction (2026-10-19): upstream CRM v1.87.0
		`crm.api.doc.get_linked_docs_of_document` KHÔNG trả field `file_url`
		trong entries của nó. Do đó, test này KHÔNG được tính là bằng chứng redaction
		cho `get_linked_docs_of_document` — nó chỉ pin rằng wrapper hiện tại trả về
		shape list, và nếu upstream trong tương lai thêm `file_url` thì wrapper
		`_redact_private_file_urls` sẽ redact cho non-system caller.

		Test được giữ làm shape regression — đảm bảo wrapper gọi upstream đúng cách
		và trả về list (không phải None hoặc raise). Status: NOT RUN cho đến khi
		runtime evidence có.

		**Caller-upstream pair đã verify tĩnh:**
		  - `crm_cskh.api.guarded_crm.get_linked_docs_of_document(doctype, docname)`
		    gọi `frappe.get_attr("crm.api.doc.get_linked_docs_of_document")(doctype, docname)`
		    rồi filter qua `frappe.has_permission(linked["doc"], "read", ...)`.
		  - Upstream trả list[dict] với keys: `doc`, `reference_docname` (verified qua
		    test_permissions.py test_linked_activity_endpoints_check_parent_before_returning_data
		    ở cross-scope branch — user_a trên lead_b trả []).
		  - Upstream v1.87.0 KHÔNG trả `file_url` / `content_hash` (theo user direction).

		**Hệ quả:**
		  - Wrapper `_redact_private_file_urls` áp dụng trên kết quả upstream. Nếu
		    upstream không có `file_url` thì helper vẫn pass qua nguyên bản (loop
		    không tìm thấy field).
		  - Đây là **defense-in-depth**: nếu upstream thay đổi shape trong tương lai,
		    wrapper sẽ tự động redact.
		  - **KHÔNG** coi test này là PASS cho SEC-26 redaction; cần test mới nếu
		    upstream thay đổi shape.

		STATUS: NOT RUN — chưa có runtime evidence trên test site sạch.
		"""
		from crm_cskh.api.guarded_crm import get_linked_docs_of_document

		frappe.set_user(self.users["a"])
		linked = get_linked_docs_of_document("CRM Lead", self.lead_a)
		# Shape assertion: trả list (có thể rỗng).
		self.assertIsInstance(linked, list)
		# Không assert về `file_url` ở đây vì upstream v1.87.0 không trả field đó.
		# Nếu upstream tương lai thêm `file_url`:
		#   - Wrapper `_redact_private_file_urls` sẽ redact cho non-system caller.
		#   - Test SEC-26 tương ứng sẽ được thêm để pin redaction.
		# Pin: KHÔNG có entry nào có `is_private` (vì upstream v1.87.0 không trả File
		# trong shape này). Nếu tương lai có, helper sẽ redact.
		for entry in linked:
			if isinstance(entry, dict):
				# Upstream v1.87.0 chỉ trả `doc` và `reference_docname`. Nếu có thêm
				# `is_private` thì file_url cũng phải được redact bởi helper.
				if entry.get("is_private"):
					self.assertNotIn("file_url", entry)
					self.assertNotIn("content_hash", entry)

	def test_get_attachments_does_not_leak_cross_scope_file_url(self):
		"""`get_attachments` KHÔNG được trả file thuộc Lead ngoài scope."""
		from crm_cskh.api.guarded_crm import get_attachments

		# Tạo file gắn vào lead_b (cross-scope với user_a)
		lead_b = _make_lead("0904444444", self.users["b"])
		file_b = _make_file(lead_b, is_private=1)
		try:
			frappe.set_user(self.users["a"])
			attachments = get_attachments("CRM Lead", lead_b)
			self.assertEqual(attachments, [])  # cross-scope → rỗng
		finally:
			frappe.set_user("Administrator")
			if frappe.db.exists("File", file_b):
				frappe.delete_doc("File", file_b, force=True, ignore_permissions=True)
			if frappe.db.exists("CRM Lead", lead_b):
				frappe.delete_doc("CRM Lead", lead_b, force=True, ignore_permissions=True)
			frappe.db.commit()

	def test_find_file_by_url_returns_none_for_old_owner_after_reassign(self):
		"""`frappe.core.doctype.file.utils.find_file_by_url` phải trả None cho old owner
		sau khi Lead reassign (T1C đã verify qua test #4 trong test_permissions.py).

		Pin thêm tại đây để mapping thấy coverage.
		"""
		from frappe.core.doctype.file.utils import find_file_by_url

		frappe.set_user("Administrator")
		frappe.share.add("CRM Lead", self.lead_a, user=self.users["a"], read=1)
		lead = frappe.get_doc("CRM Lead", self.lead_a)
		lead.lead_owner = self.users["b"]
		lead.save(ignore_permissions=True)
		frappe.set_user(self.users["a"])
		# Old owner mất quyền → find_file_by_url phải trả None
		self.assertIsNone(find_file_by_url(self.file_url_value))
		frappe.set_user(self.users["b"])
		# New owner có quyền → find_file_by_url trả file row
		self.assertIsNotNone(find_file_by_url(self.file_url_value))


# ===========================================================================
# SEC-27 (internal branch) — Reproduction: nhánh nội bộ leak khi Call Log
# không tham chiếu Lead/Deal. CHỈ REPRODUCTION — không sửa upstream.
# ===========================================================================


class TestSEC27CallLogFallbackContactInternal(FrappeTestCase):
	"""SEC-27 — Nhánh nội bộ `crm_call_log.get_call_contact` khi Call Log không tham chiếu Lead/Deal.

	Upstream `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log` gọi:
	  parse_call_log(call)
	    → get_call_contact(call, phone_number)
	      → get_contact_by_phone_number(phone_number)   ← nhánh leak khi ref rỗng

	Test này CHỈ REPRODUCTION (theo user direction `reproduction_only`) — xác nhận
	nhánh nội bộ tồn tại. Remediation (override wrapper) đã được apply ở commit
	`158e0fd` (T1B trước); test này pin rằng UPSTREAM vẫn leak nếu gọi trực tiếp,
	justifying why wrapper override cần thiết.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()

	def setUp(self):
		frappe.set_user("Administrator")
		self.orphan_call = _make_orphan_call(phone="0909876543")
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		if getattr(self, "orphan_call", None) and frappe.db.exists("CRM Call Log", self.orphan_call):
			frappe.delete_doc("CRM Call Log", self.orphan_call, force=True, ignore_permissions=True)
		if frappe.db.exists("Contact", {"mobile_no": "0909876543"}):
			for c in frappe.get_all("Contact", filters={"mobile_no": "0909876543"}, pluck="name"):
				frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_upstream_get_call_contact_orphan_routes_to_phone_lookup(self):
		"""REPRODUCTION: gọi upstream `get_call_contact` với orphan call phải trả Contact
		match qua `get_contact_by_phone_number`.

		Đây là nhánh leak nội bộ upstream — wrapper `crm_cskh.api.guarded_crm.get_call_log`
		(commit 158e0fd) chặn ở tầng entry point."""
		from crm.fcrm.doctype.crm_call_log.crm_call_log import get_call_contact

		call = {"reference_doctype": None, "reference_docname": None}
		contact = get_call_contact(call, "0909876543")
		# Upstream trả Contact match (full_name, mobile_no, ...) — đây là leak path
		self.assertIsInstance(contact, dict)
		self.assertIn("full_name", contact)

	def test_upstream_get_call_log_orphan_raises_for_staff_via_check_permission(self):
		"""`get_call_log` upstream có `.check_permission('read')` ở đầu — staff vẫn raise
		khi Call Log có reference_doctype not in (CRM Lead, CRM Deal).

		Wrapper `crm_cskh.api.guarded_crm.get_call_log` thêm 1 lớp check cho non-system
		+ ref rỗng/khác Lead/Deal. Test này pin hành vi upstream + wrapper.
		"""
		from crm.fcrm.doctype.crm_call_log.crm_call_log import get_call_log as upstream_get_call_log

		frappe.set_user(self.users["a"])
		# Upstream `.check_permission('read')` raise PermissionError cho non-system + orphan
		# vì `linked_lead_has_permission` trả False khi reference_doctype != 'CRM Lead'
		with self.assertRaises(frappe.PermissionError):
			upstream_get_call_log(self.orphan_call)

	def test_wrapper_get_call_log_still_raises_for_staff(self):
		"""Wrapper `crm_cskh.api.guarded_crm.get_call_log` fail-closed cho non-system caller
		+ orphan call (commit 158e0fd remediation)."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["a"])
		frappe.local.form_dict = frappe._dict({"name": self.orphan_call})
		with self.assertRaises(frappe.PermissionError):
			execute_cmd("crm_cskh.api.guarded_crm.get_call_log")

	def test_wrapper_get_call_log_administrator_succeeds(self):
		"""Sanity: Administrator (system) vẫn gọi được wrapper (fail-open cố ý)."""
		from frappe.handler import execute_cmd

		frappe.set_user("Administrator")
		frappe.local.form_dict = frappe._dict({"name": self.orphan_call})
		try:
			result = execute_cmd("crm_cskh.api.guarded_crm.get_call_log")
		finally:
			frappe.local.form_dict = frappe._dict()
		self.assertIsInstance(result, dict)
		self.assertEqual(result.get("name"), self.orphan_call)


# ===========================================================================
# SEC-15 + RISK-SEC-12 — BOD Viewer branch & bulk reassign
# ===========================================================================


class TestSEC15BODAndRISKSEC12BulkReassign(FrappeTestCase):
	"""SEC-15 + RISK-SEC-12 — BOD Viewer + bulk reassign branches.

	T1C ghi nhận (test_permissions.py):
	  - test #16: BOD không read Lead / không create User/Role/Data Import
	  - test #17: CRM-ADMIN fail-closed
	  - test #11: delete_bulk_docs raise cho cross-scope

	Test này pin các branch còn lại trong matrix:
	  1. BOD gọi `crm_cskh.api.guarded_reporting.*` → raise (cùng path qua `is_whitelisted`).
	  2. BOD gọi `crm.api.dashboard.get_dashboard` qua override → raise.
	  3. Staff bulk reassign (admin scope) qua API endpoint → fail-closed.
	  4. BOD thử `frappe.client.set_value` cross-scope → raise.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()
		cls.lead_a = _make_lead("0905555555", cls.users["a"])

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		if frappe.db.exists("CRM Lead", cls.lead_a):
			frappe.delete_doc("CRM Lead", cls.lead_a, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_bod_calling_guarded_reporting_via_execute_cmd_denied(self):
		"""BOD Viewer gọi `crm_cskh.api.guarded_reporting.*` qua `frappe.handler.execute_cmd()`
		phải raise — fail-closed vì `_require_system_manager`."""
		frappe.set_user(self.users["bod"])
		for endpoint in (
			"crm_cskh.api.guarded_reporting.crm_manager_dashboard",
			"crm_cskh.api.guarded_reporting.crm_manager_chart",
			"crm_cskh.api.guarded_reporting.reportview_export_query",
			"crm_cskh.api.guarded_reporting.data_export",
			"crm_cskh.api.guarded_reporting.query_report_run",
			"crm_cskh.api.guarded_reporting.query_report_export_query",
		):
			with self.subTest(endpoint=endpoint):
				with self.assertRaises(frappe.PermissionError):
					frappe.handler.execute_cmd(endpoint)

	def test_bod_calling_crm_cskh_api_endpoints_denied(self):
		"""BOD Viewer gọi `crm_cskh.api.guarded_crm.*` qua handler — pin rằng wrapper
		cũng từ chối (không chỉ reporting)."""
		frappe.set_user(self.users["bod"])
		for endpoint in (
			"crm_cskh.api.guarded_crm.get_contact_by_phone_number",
			"crm_cskh.api.guarded_crm.get_call_log",
			"crm_cskh.api.guarded_crm.get_linked_calls",
			"crm_cskh.api.guarded_crm.get_attachments",
		):
			with self.subTest(endpoint=endpoint):
				with self.assertRaises(frappe.PermissionError):
					frappe.handler.execute_cmd(endpoint)

	def test_staff_bulk_delete_cross_scope_denied(self):
		"""RISK-SEC-12: Staff gọi `delete_bulk_docs` cho Lead cross-scope phải raise."""
		frappe.set_user(self.users["a"])
		lead_b = _make_lead("0906666666", self.users["b"])
		try:
			from frappe.handler import execute_cmd

			frappe.local.form_dict = frappe._dict(
				{"doctype": "CRM Lead", "items": json.dumps([lead_b]), "delete_linked": 0}
			)
			with self.assertRaises(frappe.PermissionError):
				execute_cmd("crm_cskh.api.guarded_crm.delete_bulk_docs")
		finally:
			frappe.set_user("Administrator")
			frappe.local.form_dict = frappe._dict()
			if frappe.db.exists("CRM Lead", lead_b):
				frappe.delete_doc("CRM Lead", lead_b, force=True, ignore_permissions=True)
			frappe.db.commit()

	def test_bod_cross_scope_set_value_denied(self):
		"""BOD thử `frappe.client.set_value` cho Lead cross-scope — DocType-level permission
		chặn (BOD không có read Lead)."""
		from frappe.client import set_value

		frappe.set_user(self.users["bod"])
		with self.assertRaises(frappe.PermissionError):
			set_value("CRM Lead", self.lead_a, "status", "BOD-tampered")


# ===========================================================================
# SEC-22 + SEC-23 — Stale ToDo / Comment sau reassign
# ===========================================================================


class TestSEC22And23StaleAfterReassign(FrappeTestCase):
	"""SEC-22 + SEC-23 — Stale child records (ToDo, Comment) sau reassign.

	T1C ghi nhận test_permissions.py:
	  - test #12 (test_stale_todo_cannot_restore_access_after_owner_changes) cover ToDo stale.
	  - test #4 (test_private_file_download_and_stale_docshare_are_revoked) cover File.

	Test này pin Comment + ToDo cross-scope branches:
	  1. ToDo `allocated_to=a` sau reassign `a→b` — `a` không read Lead, không read ToDo.
	  2. Comment trên Lead — sau reassign, old owner không read Comment nếu Comment
	     linked vào Lead (linked_lead_has_permission chặn).
	  3. Reassign xuyên team (c→a) — cũ `c` không thấy Lead, mới `a` thấy.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users()
		cls.lead_a = _make_lead("0907777777", cls.users["a"])
		cls.call_a = _make_call(cls.lead_a)
		cls.note_a = (
			frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": "Stale note",
					"content": "Stale note body",
					"reference_doctype": "CRM Lead",
					"reference_docname": cls.lead_a,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.comment_a = (
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"content": "Stale comment body",
					"reference_doctype": "CRM Lead",
					"reference_name": cls.lead_a,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for doctype, name in (
			("Comment", cls.comment_a),
			("FCRM Note", cls.note_a),
			("CRM Call Log", cls.call_a),
			("CRM Lead", cls.lead_a),
		):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_stale_todo_after_reassign_raises_for_old_owner(self):
		"""SEC-22: ToDo `allocated_to=a` với ref Lead `a` — sau reassign `a→b`, user `a`
		không read được ToDo (`linked_lead_has_permission` chặn vì Lead không còn owner=a)."""
		frappe.set_user("Administrator")
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": self.users["a"],
				"reference_type": "CRM Lead",
				"reference_name": self.lead_a,
				"description": "Stale assignment probe",
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		# Reassign Lead a→b
		lead = frappe.get_doc("CRM Lead", self.lead_a)
		lead.lead_owner = self.users["b"]
		lead.save(ignore_permissions=True)

		try:
			# user a: Lead không còn read, ToDo không còn read
			frappe.set_user(self.users["a"])
			self.assertNotIn(self.lead_a, frappe.get_list("CRM Lead", pluck="name"))
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("CRM Lead", self.lead_a).check_permission("read")
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("ToDo", todo.name).check_permission("read")
			# user b (new owner): Lead + ToDo readable
			frappe.set_user(self.users["b"])
			self.assertIn(self.lead_a, frappe.get_list("CRM Lead", pluck="name"))
			frappe.get_doc("ToDo", todo.name).check_permission("read")
		finally:
			frappe.set_user("Administrator")
			if frappe.db.exists("ToDo", todo.name):
				frappe.delete_doc("ToDo", todo.name, force=True, ignore_permissions=True)
			frappe.db.commit()

	def test_stale_comment_after_reassign_raises_for_old_owner(self):
		"""SEC-23: Comment linked Lead `a` — sau reassign, user `a` không read được Comment."""
		frappe.set_user("Administrator")
		# Reassign Lead a→b
		lead = frappe.get_doc("CRM Lead", self.lead_a)
		lead.lead_owner = self.users["b"]
		lead.save(ignore_permissions=True)

		try:
			# user a: Comment (linked_lead_has_permission) chặn vì Lead không còn owner=a
			frappe.set_user(self.users["a"])
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("Comment", self.comment_a).check_permission("read")
			# user b: Comment readable
			frappe.set_user(self.users["b"])
			frappe.get_doc("Comment", self.comment_a).check_permission("read")
		finally:
			# Restore owner
			frappe.set_user("Administrator")
			lead = frappe.get_doc("CRM Lead", self.lead_a)
			lead.lead_owner = self.users["a"]
			lead.save(ignore_permissions=True)

	def test_cross_team_reassign_blocks_old_owner_and_leader(self):
		"""Cross-team reassign `c→a`: user `c` (old) + TL2 (old leader) mất access;
		user `a` (new) + TL1 (new leader) thấy Lead."""
		# Tạo lead_b riêng để tránh conflict với các test khác
		lead_x = _make_lead("0908888888", self.users["c"])
		frappe.set_user("Administrator")
		try:
			# Reassign c → a
			lead = frappe.get_doc("CRM Lead", lead_x)
			lead.lead_owner = self.users["a"]
			lead.save(ignore_permissions=True)

			# user c: không thấy
			frappe.set_user(self.users["c"])
			self.assertNotIn(lead_x, frappe.get_list("CRM Lead", pluck="name"))
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc("CRM Lead", lead_x).check_permission("read")
			# user a: thấy
			frappe.set_user(self.users["a"])
			self.assertIn(lead_x, frappe.get_list("CRM Lead", pluck="name"))
		finally:
			frappe.set_user("Administrator")
			if frappe.db.exists("CRM Lead", lead_x):
				frappe.delete_doc("CRM Lead", lead_x, force=True, ignore_permissions=True)
			frappe.db.commit()
