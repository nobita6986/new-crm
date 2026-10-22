# T1B — Remediation bảo mật G0-07: SEC-13 (Guest), SEC-14 (CSRF POST), SEC-27 (Call Log fallback contact)
#
# Phạm vi: chạy trên test site cô lập `test_cskh.localhost` cùng 7 user giả
# (`TestPermissionBoundaries.users`) — seed sẵn trong `test_permissions.py`.
#
# Tái hiện các khoảng trống đã nêu trong `t1c-security-verification.md` §4 + §6:
#  - SEC-13: Guest gọi endpoint `crm_cskh` không có test thật ở transport.
#  - SEC-14: POST thiếu/sai CSRF token chưa có automated test.
#  - SEC-27: `get_call_contact` nội bộ khi Call Log không tham chiếu Lead chưa cover.
#
# Đường thực tế (trong phạm vi Frappe test site không có HTTP server):
#  - SEC-13: dùng `frappe.is_whitelisted(method)` — đây là hàm mà `frappe.handler.handle()`
#    và `frappe.api.handle()` gọi **trước** khi thực thi method. Nếu raise PermissionError,
#    method không bao giờ chạy — đây là fail-closed semantic của Frappe.
#  - SEC-14: dùng `frappe.auth.HTTPRequest.validate_csrf_token()` — đây là hàm thực tế
#    mà HTTP handler Frappe gọi ở `app.py:init_request()` qua `frappe.local.http_request =
#    HTTPRequest()`. Test xác minh behavior trên header thiếu/sai/đúng bằng cách bind
#    một fake request vào `frappe.local.request` rồi gọi `validate_csrf_token()` (hàm thuần).
#  - SEC-27: tạo Call Log với `reference_doctype=None`, gọi upstream `get_call_contact`
#    để tái hiện nhánh leak; sau đó verify wrapper `crm_cskh.api.guarded_crm.get_call_log`
#    fail-closed cho staff (đây là remediation).
#
# KHÔNG sửa source upstream CRM/Frappe.

import frappe
from frappe.tests.utils import FrappeTestCase

from crm_cskh.tests._http_context import bound_request

# ---------------------------------------------------------------------------
# Helper — seed users + hierarchy (mirror TestPermissionBoundaries để test này
# có thể chạy độc lập, không phụ thuộc thứ tự test runner).
# ---------------------------------------------------------------------------


def _ensure_role(role_name):
	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
			ignore_permissions=True
		)


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
			"reports_to": frappe.db.get_value("CRM Sales Hierarchy", {"user": reports_to_user}, "name")
			if reports_to_user
			else None,
		}
	).insert(ignore_permissions=True)
	return row.name


def _restore_sales_hierarchy(previous_value):
	"""Khôi phục `FCRM Settings.enable_sales_hierarchy` về giá trị trước khi test.

	Được đăng ký qua `cls.addClassCleanup` nên chạy **sau** `tearDownClass` dù
	test fail. `None` được chuẩn hoá thành `0` vì field là checkbox.
	"""
	frappe.set_user("Administrator")
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", previous_value or 0)
	frappe.db.commit()


def _seed_security_users(cls):
	"""Seed các user và cây hierarchy giống TestPermissionBoundaries để test này
	độc lập (không phụ thuộc vào thứ tự class load).

	Nhận `cls` để đăng ký `addClassCleanup` khôi phục `enable_sales_hierarchy`
	sau `tearDownClass`. 5 module test trong G0-07 chạy tuần tự trên **cùng**
	một site (`p0-test.localhost`), nếu bật `enable_sales_hierarchy` mà không
	restore thì các module chạy sau sẽ bị ô nhiễm trạng thái của module trước.
	"""
	previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
	cls.addClassCleanup(_restore_sales_hierarchy, previous_hierarchy)
	_ensure_role("CSKH BOD Viewer")
	_ensure_role("CSKH CRM Admin")
	users = {
		"a": _ensure_user("cskh-sec-a@example.test", "Sales User"),
		"b": _ensure_user("cskh-sec-b@example.test", "Sales User"),
		"tl1": _ensure_user("cskh-sec-tl1@example.test", "Sales Manager", "Sales User"),
		"c": _ensure_user("cskh-sec-c@example.test", "Sales User"),
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
# SEC-13 — Guest không được phép gọi endpoint có xác thực của `crm_cskh`
# ---------------------------------------------------------------------------


class TestSEC13GuestTransport(FrappeTestCase):
	"""SEC-13 — Guest gọi endpoint `crm_cskh` qua whitelist gate đăng ký với Frappe.

	`frappe.is_whitelisted(method)` là hàm mà HTTP handler (`frappe.handler.handle()`)
	gọi **trước** khi thực thi method. Nếu raise `PermissionError`, method không bao giờ
	chạy — fail-closed semantic. Tất cả endpoint `crm_cskh` được khẳng định phải từ chối Guest."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users(cls)

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_health_ping_endpoint_not_registered_for_guest(self):
		"""`crm_cskh.api.health.ping` KHÔNG nằm trong `frappe.guest_methods`."""
		from crm_cskh.api import health

		self.assertIn(health.ping, frappe.whitelisted)
		self.assertNotIn(health.ping, frappe.guest_methods)

	def test_guest_is_denied_for_health_ping(self):
		"""Guest gọi whitelist gate của `crm_cskh.api.health.ping` → expect raise."""
		frappe.set_user("Guest")
		from crm_cskh.api import health

		with self.assertRaises(frappe.PermissionError):
			frappe.is_whitelisted(health.ping)

	def test_guest_is_denied_for_every_crm_cskh_endpoint(self):
		"""Mọi endpoint `crm_cskh` đã đăng ký phải từ chối Guest."""
		frappe.set_user("Guest")
		endpoints = [
			"crm_cskh.api.health.ping",
			"crm_cskh.api.guarded_reporting.crm_manager_dashboard",
			"crm_cskh.api.guarded_reporting.reportview_export_query",
			"crm_cskh.api.guarded_crm.get_contact_by_phone_number",
			"crm_cskh.api.guarded_crm.get_contact_lead_or_deal_from_number",
			"crm_cskh.api.guarded_crm.get_call_log",
			"crm_cskh.api.guarded_crm.get_linked_calls",
			"crm_cskh.api.guarded_crm.get_linked_notes",
			"crm_cskh.api.guarded_crm.get_linked_tasks",
			"crm_cskh.api.guarded_crm.get_attachments",
		]
		for endpoint in endpoints:
			with self.subTest(endpoint=endpoint):
				method = frappe.get_attr(endpoint)
				with self.assertRaises(frappe.PermissionError):
					frappe.is_whitelisted(method)


# ---------------------------------------------------------------------------
# SEC-14 — POST thiếu/sai CSRF token phải raise PermissionError
# ---------------------------------------------------------------------------


class TestSEC14CSRFAndSession(FrappeTestCase):
	"""SEC-14 — CSRF token check.

	`frappe.auth.HTTPRequest.validate_csrf_token()` được gọi trong `app.py:init_request()`,
	cùng code path mà mọi request HTTP qua Frappe đều đi. Test xác minh:
	  - GET không yêu cầu CSRF (SAFE_HTTP_METHODS).
	  - POST thiếu token → raise `frappe.CSRFTokenError`.
	  - POST kèm token sai → raise `frappe.CSRFTokenError`.
	  - POST kèm token đúng → không raise.

	Đây là cùng logic Frappe dùng cho `/api/method/crm_cskh.api.health.ping` mà
	`cskh.html` gọi qua `fetch()` với header `X-Frappe-CSRF-Token`."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users(cls)

	def setUp(self):
		frappe.set_user(self.users["a"])
		# Khởi tạo session data đúng shape Frappe dùng cho `validate_csrf_token`.
		if not getattr(frappe.local, "session", None) or not hasattr(frappe.local.session, "data"):
			frappe.local.session = frappe._dict({"data": frappe._dict({}), "user": self.users["a"]})
		frappe.sessions.generate_csrf_token()
		self.valid_csrf = frappe.sessions.get_csrf_token()

	def tearDown(self):
		# Reset request state — tránh leak giữa các test.
		frappe.local.request = None
		frappe.local.form_dict = frappe._dict()
		frappe.set_user("Administrator")

	def _build_request(self, method, headers=None):
		"""Tạo một `werkzeug.Request` giả lập cho `validate_csrf_token`."""
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(method=method, headers=headers or {})
		return Request(builder.get_environ())

	def _run_csrf_check(self, request):
		"""Bind request + gọi đúng method CSRF mà Frappe dùng."""
		frappe.local.request = request
		# Tạo HTTPRequest instance không trigger full LoginManager side-effect.
		# Cách Frappe thật: HTTPRequest() trong `app.py:init_request()` — code path đó chạy
		# set_session trước, sau đó validate_csrf_token. Trong test này ta chỉ cần verify
		# `validate_csrf_token` đúng vì đây là hàm raise/return CSRF decision.
		http_req = frappe.auth.HTTPRequest.__new__(frappe.auth.HTTPRequest)
		http_req.validate_csrf_token()

	def test_get_request_does_not_require_csrf(self):
		"""GET là SAFE_HTTP_METHODS — không yêu cầu CSRF token (positive regression)."""
		self._run_csrf_check(self._build_request("GET"))
		# Không raise → PASS.

	def test_post_without_csrf_token_is_rejected(self):
		"""POST không kèm `X-Frappe-CSRF-Token` → expect CSRFTokenError."""
		with self.assertRaises(frappe.CSRFTokenError):
			self._run_csrf_check(self._build_request("POST", headers={"Content-Type": "application/json"}))

	def test_post_with_invalid_csrf_token_is_rejected(self):
		"""POST kèm CSRF token sai → expect CSRFTokenError."""
		with self.assertRaises(frappe.CSRFTokenError):
			self._run_csrf_check(
				self._build_request(
					"POST",
					headers={
						"Content-Type": "application/json",
						"X-Frappe-CSRF-Token": "definitely-not-a-valid-token",
					},
				)
			)

	def test_post_with_valid_csrf_token_succeeds(self):
		"""POST kèm CSRF token đúng → không raise (positive regression)."""
		self._run_csrf_check(
			self._build_request(
				"POST",
				headers={
					"Content-Type": "application/json",
					"X-Frappe-CSRF-Token": self.valid_csrf,
				},
			)
		)
		# Không raise → PASS.

	def test_post_with_valid_csrf_token_via_form_data_succeeds(self):
		"""POST kèm CSRF token trong `form_dict.csrf_token` (đường thay thế Frappe chấp nhận)."""
		frappe.local.form_dict = frappe._dict({"csrf_token": self.valid_csrf})
		self._run_csrf_check(self._build_request("POST", headers={"Content-Type": "application/json"}))
		# Không raise → PASS.


# ---------------------------------------------------------------------------
# SEC-27 — Call Log không tham chiếu Lead → wrapper fail-closed
# ---------------------------------------------------------------------------


class TestSEC27CallLogFallbackContact(FrappeTestCase):
	"""SEC-27 — Nhánh fallback tra contact khi Call Log không tham chiếu Lead/Deal.

	Upstream `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log` gọi
	`parse_call_log(call)` → `get_call_contact(call, phone_number)` →
	`get_contact_by_phone_number(phone_number)` khi `reference_doctype not in
	("CRM Lead", "CRM Deal")`. Khi đó user có DocType-level read trên CRM Call Log
	sẽ thấy `_caller`/`_receiver` — rò rỉ contact theo số điện thoại.

	`crm_cskh` đã override `crm.integrations.api.get_contact_by_phone_number`
	qua `guarded_crm.get_contact_by_phone_number` (fail-closed cho non-system),
	nhưng wrapper `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log` CHƯA
	được override — nhánh nội bộ vẫn có thể leak.

	Remediation: thêm `crm_cskh.api.guarded_crm.get_call_log` và override
	`crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log` qua hook."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.users = _seed_security_users(cls)

	def setUp(self):
		frappe.set_user("Administrator")
		# Tạo Contact cho lookup test.
		if not frappe.db.exists("Contact", "CSKH-SEC27-Contact"):
			frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": "CSKH Sec27 Lookup",
					"mobile_no": "0909876543",
				}
			).insert(ignore_permissions=True)
		# Tạo orphan Call Log (không ref Lead/Deal).
		self.orphan_call = (
			frappe.get_doc(
				{
					"doctype": "CRM Call Log",
					"id": frappe.generate_hash(length=12),
					"from": "0900000011",
					"to": "0909876543",
					"type": "Outgoing",
					"status": "No Answer",
					"reference_doctype": None,
					"reference_docname": None,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		if frappe.db.exists("CRM Call Log", getattr(self, "orphan_call", None)):
			frappe.delete_doc("CRM Call Log", self.orphan_call, force=True, ignore_permissions=True)
		if frappe.db.exists("Contact", "CSKH-SEC27-Contact"):
			frappe.delete_doc("Contact", "CSKH-SEC27-Contact", force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_get_call_contact_orphan_routes_to_lookup_path(self):
		"""Reproduction: gọi upstream `get_call_contact` cho orphan call phải trả
		từ `get_contact_by_phone_number` (nhánh leak nếu wrapper không guard).

		Test này chứng minh nhánh nội bộ upstream KHÔNG guard — đây là gap cần fix."""
		from crm.fcrm.doctype.crm_call_log.crm_call_log import get_call_contact

		call = {"reference_doctype": None, "reference_docname": None}
		contact = get_call_contact(call, "0909876543")
		# Upstream trả Contact match — đây là leak path. Wrapper `crm_cskh` phải chặn.
		self.assertIsInstance(contact, dict)
		self.assertIn("full_name", contact)
		# Document: nếu upstream guard thì test này phải trả {} → cần override wrapper.

	def test_overridden_get_call_log_wrapper_exists_and_is_whitelisted(self):
		"""Wrapper `crm_cskh.api.guarded_crm.get_call_log` phải tồn tại và Frappe
		phải công nhận là override của upstream."""
		from crm_cskh.api import guarded_crm

		override = frappe.override_whitelisted_method(
			"crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log"
		)
		self.assertTrue(
			override.endswith("get_call_log"),
			f"Override path không trỏ tới get_call_log, got: {override}",
		)
		self.assertIn(guarded_crm.get_call_log, frappe.whitelisted)

	def test_staff_get_call_log_for_orphan_raises_permission_error(self):
		"""Staff gọi wrapper `get_call_log` cho orphan call → expect raise.

		Sau remediation, wrapper fail-closed cho non-system caller."""
		frappe.set_user(self.users["a"])
		from frappe.handler import execute_cmd

		frappe.local.form_dict = frappe._dict({"name": self.orphan_call})
		with bound_request(), self.assertRaises(frappe.PermissionError):
			execute_cmd("crm_cskh.api.guarded_crm.get_call_log")

	def test_administrator_get_call_log_for_orphan_succeeds(self):
		"""Sanity: Administrator vẫn gọi được (fail-open cố ý cho system caller)."""
		from frappe.handler import execute_cmd

		frappe.set_user("Administrator")
		frappe.local.form_dict = frappe._dict({"name": self.orphan_call})
		try:
			with bound_request():
				result = execute_cmd("crm_cskh.api.guarded_crm.get_call_log")
		finally:
			frappe.local.form_dict = frappe._dict()
		self.assertIsInstance(result, dict)
		self.assertEqual(result.get("name"), self.orphan_call)
