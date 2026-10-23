# T1B — B-T1C-5: test mapping cho 9 ô matrix `CHƯA XÁC MINH` thuộc phạm vi T1B
#
# Ô được cover (theo `permission-matrix.md` — tiêu chí chuẩn cho tới khi T1C xác nhận
# §3.5/§3.6 trong `t1c-security-verification.md`):
#   - LEAD-07 : Staff (A) đọc Lead chưa giao (không `lead_owner`, không DocShare) → D
#   - CL-06   : TL1 đọc Call Log thuộc Lead Team 1 → A (list + detail)
#   - CL-07   : TL1 đọc Call Log thuộc Lead Team 2 → D (list + detail)
#   - TK-05   : TL1 đọc Task thuộc Lead Team 1 → A (list + detail)
#   - TK-06   : TL1 đọc Task thuộc Lead Team 2 → D (list + detail)
#   - NT-05   : TL1 đọc Note thuộc Lead Team 1 → A (list + detail)
#   - NT-06   : TL1 đọc Note thuộc Lead Team 2 → D (list + detail)
#   - RA-04   : CRM-ADMIN reassign Lead nội bộ Team 1 A→B → **BLOCKED, chưa đóng**
#   - LS-02   : global search không trả Lead ngoài scope (A)
#
# NGUYÊN TẮC:
#   - KHÔNG sửa `permission-matrix.md`, `t1c-security-verification.md`, `risk-register.md`,
#     `p0-exit-report.md`, ADR-004/005.
#   - KHÔNG tạo bypass. Với ô bị block (RA-04) chỉ ghi blocker + câu hỏi cần Owner/T1C chốt.
#   - KHÔNG suy rộng từ test Lead-only sang child DocType: mỗi ô CL/TK/NT có assertion
#     riêng cho cả list (query scope) và detail (record-level `check_permission`).
#   - Runtime: **NOT RUN** — host không có Docker/bench (xem `t1b-runtime-evidence-not-run.md` §1).
#     Test đã THÊM và assertion đã VIẾT, nhưng chưa có log thực thi ⇒ **không** tuyên bố PASS
#     và **không** nâng G0-07 / P0.
#
# Backend global search (LS-02) — đã xác minh tĩnh trước khi viết test:
#   Frappe v15.123.1 `frappe/desk/search.py`:
#     - `search_link` (Link Field) → gọi `search_widget`
#     - `search_widget` (search box) → `frappe.get_list(..., ignore_permissions=ignore_permissions)`
#       ⇒ **permission-aware**, áp `permission_query_conditions` của app
#     - `awesomebar_search` chỉ gọi hook `awesomebar_search`; `crm_cskh` **không** đăng ký hook
#       này (`hooks.py:141` bị comment) ⇒ awesomebar KHÔNG phải backend của global search
#     - `get_names_for_mentions` chỉ trả User/User Group ⇒ không liên quan CRM Lead
#   CRM v1.87.0 `crm/hooks.py` **không** override `frappe.desk.search.*`.
#   ⇒ Backend đúng cho LS-02 là `frappe.desk.search.search_widget` / `search_link`.
#     KHÔNG dùng `get_data` (đó là list API của CRM, đã cover ở LS-01/LS-05) làm đại diện
#     cho global search.
#
# KHÔNG sửa source upstream CRM/Frappe.

import frappe
from frappe.tests.utils import FrappeTestCase

from crm_cskh.tests._http_context import bound_request

# ---------------------------------------------------------------------------
# Helper — seed users + hierarchy
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
	missing = [role for role in roles if role not in {row.role for row in user.roles}]
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


def _seed_bt1c5_users(cls):
	"""Seed user + cây hierarchy 2 team cho B-T1C-5.

	Bản mở rộng so với `_seed_security_users()` ở `test_security_remediation_v2.py`:
	thêm `tl2`/`c` (Team 2) và `admin` (CRM-ADMIN) vì CL/TK/NT-06 cần dữ liệu Team 2
	và RA-04 cần actor CRM-ADMIN.

	Nhận `cls` để đăng ký `addClassCleanup` khôi phục `enable_sales_hierarchy`.
	5 module test trong G0-07 chạy tuần tự trên **cùng** một site
	(`p0-test.localhost`), nếu bật `enable_sales_hierarchy` mà không restore
	thì các module chạy sau sẽ bị ô nhiễm trạng thái của module trước.
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
		"tl2": _ensure_user("cskh-sec-tl2@example.test", "Sales Manager", "Sales User"),
		"outside": _ensure_user("cskh-sec-outside@example.test", "Sales Manager", "Sales User"),
		"bod": _ensure_user("cskh-sec-bod@example.test", "CSKH BOD Viewer"),
		"admin": _ensure_user("cskh-sec-admin@example.test", "CSKH CRM Admin"),
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
	_ensure_hierarchy(users["tl2"], 1)
	_ensure_hierarchy(users["c"], 0, users["tl2"])
	frappe.db.commit()
	return users


# ---------------------------------------------------------------------------
# Helper — fixtures
# ---------------------------------------------------------------------------


def _make_lead(phone, owner=None, first_name="CSKH BT1C5"):
	"""Tạo CRM Lead. `owner=None` → Lead CHƯA GIAO (`lead_owner` rỗng).

	Tham số `first_name` cho phép fixture có giá trị **searchable** (xem
	`_make_searchable_lead`).
	"""
	doc = {
		"doctype": "CRM Lead",
		"first_name": first_name,
		"mobile_no": phone,
		"status": "New",
	}
	if owner:
		doc["lead_owner"] = owner
	return frappe.get_doc(doc).insert(ignore_permissions=True).name


def _make_call(lead, phone_from="0900000001"):
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": frappe.generate_hash(length=12),
				"from": phone_from,
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


def _make_task(lead):
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "CSKH BT1C5 task",
				"status": "Todo",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _make_note(lead):
	return (
		frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "CSKH BT1C5 note",
				"content": "CSKH BT1C5 note body",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


class _BT1C5Base(FrappeTestCase):
	"""Base — seed user + fixture Team 1 / Team 2 dùng chung cho các ô B-T1C-5."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
		cls.users = _seed_bt1c5_users(cls)
		base_phone = int(frappe.generate_hash(length=6), 16) % 1_000_000
		# Team 1 (dưới TL1)
		cls.lead_t1 = _make_lead(f"091{base_phone:07d}", cls.users["a"])
		cls.call_t1 = _make_call(cls.lead_t1)
		cls.task_t1 = _make_task(cls.lead_t1)
		cls.note_t1 = _make_note(cls.lead_t1)
		# Team 2 (dưới TL2)
		cls.lead_t2 = _make_lead(f"092{(base_phone + 1) % 1_000_000:07d}", cls.users["c"])
		cls.call_t2 = _make_call(cls.lead_t2)
		cls.task_t2 = _make_task(cls.lead_t2)
		cls.note_t2 = _make_note(cls.lead_t2)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for doctype, name in (
			("CRM Call Log", getattr(cls, "call_t1", None)),
			("CRM Call Log", getattr(cls, "call_t2", None)),
			("CRM Task", getattr(cls, "task_t1", None)),
			("CRM Task", getattr(cls, "task_t2", None)),
			("FCRM Note", getattr(cls, "note_t1", None)),
			("FCRM Note", getattr(cls, "note_t2", None)),
			("CRM Lead", getattr(cls, "lead_t1", None)),
			("CRM Lead", getattr(cls, "lead_t2", None)),
		):
			if name and frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", cls.previous_hierarchy or 0)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")


# ---------------------------------------------------------------------------
# LEAD-07 — Staff đọc Lead chưa giao
# ---------------------------------------------------------------------------


class TestLEAD07UnassignedLead(_BT1C5Base):
	"""LEAD-07 — Staff `A` đọc Lead **chưa giao** (không `lead_owner`, không DocShare) → D.

	Tiêu chí matrix: Actor `A`, dữ liệu `U1 (chưa giao)`, thao tác `detail`, kỳ vọng `D`.

	Lead fixture được tạo **không có `lead_owner`** và **không có DocShare** — đúng
	trạng thái "chưa giao" ở `permission-matrix.md` §1. Cơ chế kỳ vọng:
	`permissions._user_owns_in_scope()` trả `False` khi `owner` falsy
	(`permissions.py` L39-40), nên `lead_has_permission` trả `False` cho `read`.

	Test kiểm tra **cả hai đường truy cập thực tế**:
	  1. list path — `frappe.get_list` + `frappe.client.get_list` không trả Lead này.
	  2. detail path — `frappe.client.get` và `get_doc(...).check_permission("read")` bị từ chối.
	"""

	def setUp(self):
		super().setUp()
		# Lead chưa giao — tạo riêng trong setUp để cô lập với fixture dùng chung.
		self.lead_t1_unassigned = _make_lead(
			f"093{int(frappe.generate_hash(length=6), 16) % 1_000_000:07d}", None
		)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		if getattr(self, "lead_t1_unassigned", None) and frappe.db.exists(
			"CRM Lead", self.lead_t1_unassigned
		):
			frappe.delete_doc("CRM Lead", self.lead_t1_unassigned, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def test_unassigned_lead_has_no_owner_and_no_share(self):
		"""Precondition — fixture đúng trạng thái "chưa giao": owner rỗng, không DocShare."""
		lead = frappe.get_doc("CRM Lead", self.lead_t1_unassigned)
		self.assertFalse(lead.lead_owner)
		self.assertFalse(frappe.db.exists("DocShare", {"share_doctype": "CRM Lead", "share_name": lead.name}))

	def test_staff_list_does_not_return_unassigned_lead(self):
		"""LEAD-07 (list) — `A` không thấy Lead chưa giao trong list path."""
		frappe.set_user(self.users["a"])
		from frappe.client import get_list as client_get_list

		filters = {"name": ["in", [self.lead_t1, self.lead_t1_unassigned]]}
		# `pluck="name"` trả list[str] (không phải list[dict]) nên không có
		# `.name` — dùng trực tiếp giá trị trả về (CI #39815209140).
		self.assertEqual(
			set(frappe.get_list("CRM Lead", filters=filters, pluck="name")),
			{self.lead_t1},
			"Lead chưa giao KHÔNG được xuất hiện trong list của Staff A.",
		)
		self.assertEqual(
			{row["name"] for row in client_get_list("CRM Lead", fields=["name"], filters=filters)},
			{self.lead_t1},
			"Lead chưa giao KHÔNG được xuất hiện qua frappe.client.get_list của A.",
		)
		self.assertNotIn(
			self.lead_t1_unassigned,
			frappe.get_list("CRM Lead", pluck="name"),
			"Lead chưa giao phải nằm ngoài toàn bộ scope list của A.",
		)

	def test_staff_detail_is_denied_for_unassigned_lead(self):
		"""LEAD-07 (detail) — `A` bị từ chối ở cả `frappe.client.get` và `check_permission`."""
		from frappe.client import get as client_get

		frappe.set_user(self.users["a"])
		with self.assertRaises(frappe.PermissionError):
			client_get("CRM Lead", self.lead_t1_unassigned)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Lead", self.lead_t1_unassigned).check_permission("read")
		self.assertFalse(frappe.has_permission("CRM Lead", "read", self.lead_t1_unassigned, throw=False))


# ---------------------------------------------------------------------------
# CL-06 / CL-07 — TL1 đọc Call Log theo team
# ---------------------------------------------------------------------------


class TestCL0607TeamLeaderCallLog(_BT1C5Base):
	"""CL-06 / CL-07 — TL1 đọc `CRM Call Log` theo team (list + detail).

	Tiêu chí matrix:
	  - CL-06: Actor `TL1`, dữ liệu `call Team 1`, `list/detail`, kỳ vọng `A`.
	  - CL-07: Actor `TL1`, dữ liệu `call Team 2`, `list/detail`, kỳ vọng `D`.

	Cơ chế: `hooks.py` đăng ký `permission_query_conditions["CRM Call Log"]` →
	`crm_cskh.permissions.linked_lead_query_conditions` (EXISTS subquery trên parent
	CRM Lead theo `lead_owner` scope) và `has_permission["CRM Call Log"]` →
	`linked_lead_has_permission` (check quyền read của parent Lead).

	Mỗi ô có assertion RIÊNG cho **cả list và detail** — không suy rộng kết quả
	từ test Lead-only (`test_team_leader_sees_team_but_not_other_team`) sang child DocType.
	"""

	def test_cl06_tl1_can_list_call_log_of_team_1(self):
		"""CL-06 (list) — TL1 thấy Call Log gắn Lead Team 1."""
		frappe.set_user(self.users["tl1"])
		rows = frappe.get_list(
			"CRM Call Log",
			filters={"reference_docname": ["in", [self.lead_t1, self.lead_t2]]},
			pluck="name",
		)
		self.assertIn(
			self.call_t1,
			rows,
			f"TL1 phải thấy Call Log Team 1 {self.call_t1!r}. Actual: {rows}",
		)
		self.assertNotIn(
			self.call_t2,
			rows,
			f"TL1 KHÔNG được thấy Call Log Team 2 {self.call_t2!r}. Actual: {rows}",
		)

	def test_cl06_tl1_can_read_call_log_detail_of_team_1(self):
		"""CL-06 (detail) — TL1 đọc được record Call Log Team 1."""
		frappe.set_user(self.users["tl1"])
		self.assertTrue(frappe.has_permission("CRM Call Log", "read", self.call_t1, throw=False))
		doc = frappe.get_doc("CRM Call Log", self.call_t1)
		doc.check_permission("read")  # không raise ⇒ được phép
		self.assertEqual(doc.reference_docname, self.lead_t1)

	def test_cl06_tl1_can_call_get_call_log_endpoint_for_team_1(self):
		"""CL-06 (endpoint) — wrapper `get_call_log` trả dữ liệu cho TL1 trong Team 1."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["tl1"])
		frappe.local.form_dict = frappe._dict({"name": self.call_t1})
		try:
			with bound_request():
				result = execute_cmd("crm_cskh.api.guarded_crm.get_call_log")
		finally:
			frappe.local.form_dict = frappe._dict()
		self.assertIsInstance(result, dict)
		self.assertEqual(result.get("name"), self.call_t1)

	def test_cl07_tl1_list_does_not_include_call_log_of_team_2(self):
		"""CL-07 (list) — TL1 KHÔNG thấy Call Log gắn Lead Team 2."""
		frappe.set_user(self.users["tl1"])
		rows = frappe.get_list(
			"CRM Call Log",
			filters={"reference_docname": ["in", [self.lead_t1, self.lead_t2]]},
			pluck="name",
		)
		self.assertNotIn(self.call_t2, rows)
		# Filter trực tiếp theo tên record — vẫn phải rỗng.
		self.assertEqual(
			frappe.get_list("CRM Call Log", filters={"name": self.call_t2}, pluck="name"),
			[],
			"TL1 không được thấy Call Log Team 2 qua filter trực tiếp theo name.",
		)

	def test_cl07_tl1_detail_is_denied_for_call_log_of_team_2(self):
		"""CL-07 (detail) — TL1 bị từ chối record-level cho Call Log Team 2."""
		frappe.set_user(self.users["tl1"])
		self.assertFalse(frappe.has_permission("CRM Call Log", "read", self.call_t2, throw=False))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Call Log", self.call_t2).check_permission("read")

	def test_cl07_tl1_get_call_log_endpoint_is_denied_for_team_2(self):
		"""CL-07 (endpoint) — wrapper `get_call_log` fail-closed cho TL1 ở Call Log Team 2."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["tl1"])
		frappe.local.form_dict = frappe._dict({"name": self.call_t2})
		try:
			with bound_request(), self.assertRaises(frappe.PermissionError):
				execute_cmd("crm_cskh.api.guarded_crm.get_call_log")
		finally:
			frappe.local.form_dict = frappe._dict()


# ---------------------------------------------------------------------------
# TK-05 / TK-06 — TL1 đọc Task theo team
# ---------------------------------------------------------------------------


class TestTK0506TeamLeaderTask(_BT1C5Base):
	"""TK-05 / TK-06 — TL1 đọc `CRM Task` theo team (list + detail).

	Tiêu chí matrix:
	  - TK-05: Actor `TL1`, dữ liệu `Task Team 1`, `list/detail`, kỳ vọng `A`.
	  - TK-06: Actor `TL1`, dữ liệu `Task Team 2`, `list/detail`, kỳ vọng `D`.

	Cơ chế: giống CL-06/07 — `linked_lead_query_conditions` + `linked_lead_has_permission`
	qua `hooks.py` (CRM Task nằm trong `LEAD_LINKS` với
	`("reference_doctype", "reference_docname")`).
	"""

	def test_tk05_tl1_can_list_task_of_team_1(self):
		"""TK-05 (list) — TL1 thấy Task gắn Lead Team 1."""
		frappe.set_user(self.users["tl1"])
		rows = frappe.get_list(
			"CRM Task",
			filters={"reference_docname": ["in", [self.lead_t1, self.lead_t2]]},
			pluck="name",
		)
		self.assertIn(self.task_t1, rows)
		self.assertNotIn(self.task_t2, rows)

	def test_tk05_tl1_can_read_task_detail_of_team_1(self):
		"""TK-05 (detail) — TL1 đọc được record Task Team 1."""
		frappe.set_user(self.users["tl1"])
		self.assertTrue(frappe.has_permission("CRM Task", "read", self.task_t1, throw=False))
		doc = frappe.get_doc("CRM Task", self.task_t1)
		doc.check_permission("read")
		self.assertEqual(doc.reference_docname, self.lead_t1)

	def test_tk05_tl1_can_call_get_linked_tasks_endpoint_for_team_1(self):
		"""TK-05 (endpoint) — `get_linked_tasks` trả Task Team 1 cho TL1."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["tl1"])
		frappe.local.form_dict = frappe._dict({"name": self.lead_t1})
		try:
			with bound_request():
				result = execute_cmd("crm_cskh.api.guarded_crm.get_linked_tasks")
		finally:
			frappe.local.form_dict = frappe._dict()
		# Upstream `crm.api.activities.get_linked_tasks` trả `list[dict]`, mỗi
		# entry có `name` — không phải list[string] (CI #39815209140).
		self.assertIn(
			self.task_t1,
			{row["name"] for row in result},
			f"Task Team 1 phải có trong kết quả, nhận: {result!r}",
		)

	def test_tk06_tl1_list_does_not_include_task_of_team_2(self):
		"""TK-06 (list) — TL1 KHÔNG thấy Task gắn Lead Team 2."""
		frappe.set_user(self.users["tl1"])
		rows = frappe.get_list(
			"CRM Task",
			filters={"reference_docname": ["in", [self.lead_t1, self.lead_t2]]},
			pluck="name",
		)
		self.assertNotIn(self.task_t2, rows)
		self.assertEqual(frappe.get_list("CRM Task", filters={"name": self.task_t2}, pluck="name"), [])

	def test_tk06_tl1_detail_is_denied_for_task_of_team_2(self):
		"""TK-06 (detail) — TL1 bị từ chối record-level cho Task Team 2."""
		frappe.set_user(self.users["tl1"])
		self.assertFalse(frappe.has_permission("CRM Task", "read", self.task_t2, throw=False))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Task", self.task_t2).check_permission("read")

	def test_tk06_tl1_get_linked_tasks_endpoint_is_denied_for_team_2(self):
		"""TK-06 (endpoint) — `get_linked_tasks` raise cho TL1 khi gọi trên Lead Team 2."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["tl1"])
		frappe.local.form_dict = frappe._dict({"name": self.lead_t2})
		try:
			with bound_request(), self.assertRaises(frappe.PermissionError):
				execute_cmd("crm_cskh.api.guarded_crm.get_linked_tasks")
		finally:
			frappe.local.form_dict = frappe._dict()


# ---------------------------------------------------------------------------
# NT-05 / NT-06 — TL1 đọc Note theo team
# ---------------------------------------------------------------------------


class TestNT0506TeamLeaderNote(_BT1C5Base):
	"""NT-05 / NT-06 — TL1 đọc `FCRM Note` theo team (list + detail).

	Tiêu chí matrix:
	  - NT-05: Actor `TL1`, dữ liệu `Note Team 1`, thao tác `read`, kỳ vọng `A`.
	  - NT-06: Actor `TL1`, dữ liệu `Note Team 2`, thao tác `read`, kỳ vọng `D`.

	Cơ chế: `linked_lead_query_conditions` + `linked_lead_has_permission`
	(`FCRM Note` → `("reference_doctype", "reference_docname")`).
	"""

	def test_nt05_tl1_can_list_note_of_team_1(self):
		"""NT-05 (list) — TL1 thấy Note gắn Lead Team 1."""
		frappe.set_user(self.users["tl1"])
		rows = frappe.get_list(
			"FCRM Note",
			filters={"reference_docname": ["in", [self.lead_t1, self.lead_t2]]},
			pluck="name",
		)
		self.assertIn(self.note_t1, rows)
		self.assertNotIn(self.note_t2, rows)

	def test_nt05_tl1_can_read_note_detail_of_team_1(self):
		"""NT-05 (detail) — TL1 đọc được record Note Team 1."""
		frappe.set_user(self.users["tl1"])
		self.assertTrue(frappe.has_permission("FCRM Note", "read", self.note_t1, throw=False))
		doc = frappe.get_doc("FCRM Note", self.note_t1)
		doc.check_permission("read")
		self.assertEqual(doc.reference_docname, self.lead_t1)

	def test_nt05_tl1_can_call_get_linked_notes_endpoint_for_team_1(self):
		"""NT-05 (endpoint) — `get_linked_notes` trả Note Team 1 cho TL1."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["tl1"])
		frappe.local.form_dict = frappe._dict({"name": self.lead_t1})
		try:
			with bound_request():
				result = execute_cmd("crm_cskh.api.guarded_crm.get_linked_notes")
		finally:
			frappe.local.form_dict = frappe._dict()
		# Upstream `crm.api.activities.get_linked_notes` trả `list[dict]`, mỗi
		# entry có `name` — không phải list[string] (CI #39815209140).
		self.assertIn(
			self.note_t1,
			{row["name"] for row in result},
			f"Note Team 1 phải có trong kết quả, nhận: {result!r}",
		)

	def test_nt06_tl1_list_does_not_include_note_of_team_2(self):
		"""NT-06 (list) — TL1 KHÔNG thấy Note gắn Lead Team 2."""
		frappe.set_user(self.users["tl1"])
		rows = frappe.get_list(
			"FCRM Note",
			filters={"reference_docname": ["in", [self.lead_t1, self.lead_t2]]},
			pluck="name",
		)
		self.assertNotIn(self.note_t2, rows)
		self.assertEqual(frappe.get_list("FCRM Note", filters={"name": self.note_t2}, pluck="name"), [])

	def test_nt06_tl1_detail_is_denied_for_note_of_team_2(self):
		"""NT-06 (detail) — TL1 bị từ chối record-level cho Note Team 2."""
		frappe.set_user(self.users["tl1"])
		self.assertFalse(frappe.has_permission("FCRM Note", "read", self.note_t2, throw=False))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("FCRM Note", self.note_t2).check_permission("read")

	def test_nt06_tl1_get_linked_notes_endpoint_is_denied_for_team_2(self):
		"""NT-06 (endpoint) — `get_linked_notes` raise cho TL1 khi gọi trên Lead Team 2."""
		from frappe.handler import execute_cmd

		frappe.set_user(self.users["tl1"])
		frappe.local.form_dict = frappe._dict({"name": self.lead_t2})
		try:
			with bound_request(), self.assertRaises(frappe.PermissionError):
				execute_cmd("crm_cskh.api.guarded_crm.get_linked_notes")
		finally:
			frappe.local.form_dict = frappe._dict()


# ---------------------------------------------------------------------------
# RA-04 — CRM-ADMIN reassign Lead nội bộ Team 1 (BLOCKED)
# ---------------------------------------------------------------------------


class TestRA04CrmAdminReassignBlocked(_BT1C5Base):
	"""RA-04 — CRM-ADMIN reassign Lead nội bộ Team 1 `A → B`. **BLOCKED — chưa đóng.**

	Matrix (`permission-matrix.md` §7) ghi kỳ vọng **`A`** (cho phép) cho CRM-ADMIN.
	ADR-004 §2.2 quy định **ownership service trong `crm_cskh` là đường DUY NHẤT**
	để đổi owner/assignee, chạy trong một transaction có kiểm soát.

	**Blocker (bằng chứng tĩnh, đọc từ source trong checkout):**

	1. **Ownership service KHÔNG tồn tại.** `crm_cskh/permissions.py` chỉ có:
	   - `validate_lead_owner(doc)` — chặn non-system khi đổi `lead_owner`;
	   - `clear_stale_lead_shares(doc)` — dọn DocShare;
	   - `validate_lead_assignment(doc)` — chặn ToDo cho non-system;
	   - `remove_assignments(...)` — guard `_is_system` rồi delegate upstream.
	   Không có hàm nào đồng bộ `lead_owner` + `ToDo` + `DocShare` trong một transaction
	   như ADR-004 §2.2 yêu cầu. `hooks.py` không đăng ký endpoint reassign nào.

	2. **CRM-ADMIN không thể đổi owner hiện tại — fail-closed.**
	   `validate_lead_owner` (`permissions.py`) chặn khi
	   `not _is_system(frappe.session.user)`. `_is_system` trả `True` chỉ khi
	   `user == "Administrator"` hoặc `"System Manager" in frappe.get_roles(user)`.
	   Test `test_crm_admin_role_fails_closed_until_controlled_service_exists`
	   (`test_permissions.py`) đã chứng minh actor CRM-ADMIN **không** có
	   `System Manager`. ⇒ CRM-ADMIN sẽ bị `validate_lead_owner` chặn khi đổi
	   `lead_owner` ⇒ hành vi runtime là **D**, **mâu thuẫn** với kỳ vọng matrix `A`.

	3. **Vì sao KHÔNG viết test "cho phép":** dùng `Administrator` để reassign sẽ
	   *đại diện sai actor* (Administrator ≠ CRM-ADMIN) và tạo bằng chứng giả cho
	   RA-04. Dùng `lead.save(ignore_permissions=True)` từ CRM-ADMIN sẽ vô hiệu hóa
	   chính guard mà test cần kiểm chứng. Cả hai đều là **bypass**, bị T0 cấm.

	Các test dưới đây KHÔNG assert hành vi "cho phép" hay "từ chối" — chúng **pin
	sự thật hiện trạng** để T1C/Owner có bằng chứng để chốt, và ghi rõ RA-04
	**chưa được đóng**.
	"""

	def test_ra04_ownership_service_does_not_exist_yet(self):
		"""Blocker #1 — chứng minh `crm_cskh` chưa có ownership service (ADR-004 §2.2).

		Assert: không có attribute nào trong `crm_cskh.permissions` trông giống
		ownership/reassign service, và không có endpoint reassign nào được đăng ký
		trong `override_whitelisted_methods`.
		"""
		from crm_cskh import permissions

		self.assertFalse(
			hasattr(permissions, "reassign_lead"),
			"ADR-004 §2.2 yêu cầu ownership service; `reassign_lead` chưa tồn tại.",
		)
		self.assertFalse(
			hasattr(permissions, "transfer_lead_ownership"),
			"ADR-004 §2.2 yêu cầu ownership service; `transfer_lead_ownership` chưa tồn tại.",
		)
		override = frappe.get_hooks("override_whitelisted_methods") or {}
		reassign_routes = [
			route
			for route in override
			if any(token in route.lower() for token in ("reassign", "transfer_ownership", "assign"))
		]
		self.assertEqual(
			reassign_routes,
			[],
			"Không được đăng ký endpoint reassign/transfer khi ownership service chưa có "
			"(nếu có, phải xem lại guard theo ADR-004 §2.2).",
		)

	def test_ra04_crm_admin_is_not_system_and_is_blocked_by_validate_lead_owner(self):
		"""Blocker #2 — CRM-ADMIN bị `validate_lead_owner` chặn ⇒ runtime là D, không phải A.

		Đây là **hành vi quan sát được**, không phải kỳ vọng matrix. Test KHÔNG khẳng
		định RA-04 PASS/FAIL — nó pin sự thật để Owner/T1C chốt chính sách.
		"""
		from crm_cskh import permissions

		# CRM-ADMIN không phải system user theo định nghĩa `_is_system`.
		frappe.set_user(self.users["admin"])
		self.assertNotIn("System Manager", frappe.get_roles(self.users["admin"]))
		self.assertFalse(permissions._is_system(self.users["admin"]))

		# Thử đổi owner như CRM-ADMIN (không dùng ignore_permissions để không bypass guard).
		with self.assertRaises(frappe.PermissionError):
			lead = frappe.get_doc("CRM Lead", self.lead_t1)
			lead.lead_owner = self.users["b"]
			lead.save()

		# Owner không đổi — confirm bằng DB.
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead_t1, "lead_owner"), self.users["a"])

	def test_ra04_stays_unclosed_pending_owner_decision(self):
		"""Ghi nhận trạng thái — RA-04 **chưa đóng**, cần Owner/T1C chốt.

		Ba câu hỏi cần quyết định (không tự ý chọn phương án nào ở T1B):
		  1. Có build ownership service (ADR-004 §2.2) trước P0 exit không?
		  2. Nếu chưa build: matrix RA-04 có chấp nhận kỳ vọng `D` (fail-closed) tạm thời
		     cho tới khi service sẵn sàng, thay vì `A`?
		  3. Nếu build: actor nào được gọi service — CRM-ADMIN role, hay System Manager?
		"""
		self.assertTrue(
			True,
			"RA-04 CHƯA ĐÓNG — runtime hiện là D (fail-closed) do `validate_lead_owner` chặn "
			"CRM-ADMIN, MÂU THUẪN với kỳ vọng A của permission-matrix.md §7. Cần Owner/T1C "
			"chốt: build ownership service (ADR-004 §2.2) hay tạm đổi kỳ vọng sang D. "
			"T1B không tạo bypass và không dùng Administrator thay CRM-ADMIN.",
		)


# ---------------------------------------------------------------------------
# LS-02 — global search không trả Lead ngoài scope
# ---------------------------------------------------------------------------


class TestLS02GlobalSearchScope(_BT1C5Base):
	"""LS-02 — global search của phiên bản đang dùng KHÔNG trả Lead ngoài scope (actor `A`).

	**Backend đã xác minh tĩnh** (không đoán):
	  - Frappe v15.123.1 `frappe/desk/search.py`:
	      `search_link` (Link Field) → `search_widget`;
	      `search_widget` (search box) → `frappe.get_list(..., ignore_permissions=...)`
	      ⇒ **permission-aware**, áp `permission_query_conditions` của app;
	      `awesomebar_search` chỉ gọi hook `awesomebar_search` — `crm_cskh` **không**
	      đăng ký hook này (`hooks.py:141` bị comment) ⇒ không phải global search;
	      `get_names_for_mentions` chỉ trả User/User Group ⇒ không liên quan CRM Lead.
	  - CRM v1.87.0 `crm/hooks.py` **không** override `frappe.desk.search.*`.
	  ⇒ Backend đúng cho ô này là `frappe.desk.search.search_widget` / `search_link`.

	Test KHÔNG dùng `crm.api.doc.get_data` (đó là list API của CRM, đã cover ở
	LS-01/LS-05) làm đại diện cho global search, và KHÔNG sửa upstream.

	Đối chiếu: actor `A` (Sales User, Team 1) phải **không** thấy
	- Lead của chính mình không? — không, phải THẤY (sanity positive);
	- Lead của đồng đội `B` (Team 1, ngoài owner scope);
	- Lead của `C` (Team 2);
	- Lead chưa giao (owner rỗng) — owner `""` không thuộc scope của bất kỳ ai.

	**Sửa lỗi fixture (T1B, 2026-10-23) — search term không khớp field searchable.**
	`search_widget` (Frappe v15.123.1 `frappe/desk/search.py`) **KHÔNG dùng
	`searchfield` để lọc**. Filter thật được dựng trong `or_filters` từ:
	    - `name` (docname — CRM Lead dùng `naming_series` ⇒ `CRM-LEAD-2026-XXXXX`)
	    - `meta.title_field` (`lead_name`)
	    - `meta.get_search_fields()` (`status`, `email`, `lead_name`)
	`searchfield` chỉ quyết định danh sách field được SELECT, không quyết định
	điều kiện lọc.

	Fixture cũ tìm bằng `mobile_no` = `"091..."` ⇒ không field nào ở trên chứa
	"091" ⇒ `search_widget` trả list RỖNG ⇒ assert "A phải thấy Lead của chính
	mình" FAIL **nham** (CI #39885152649: `'CRM-LEAD-2026-00060' not found in set()`).
	Đây là lỗi **fixture**, KHÔNG phải lỗi scope — `search_widget` vẫn
	permission-aware; fix bằng cách dùng `lead_name` (field `search_index=1`)
	làm giá trị tìm kiếm.
	"""

	# Giá trị tìm kiếm dùng cho LS-02 — nằm trong `lead_name` (searchable).
	SEARCH_T1 = "cskh-ls02-team1"
	SEARCH_T2 = "cskh-ls02-team2"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Fixture riêng cho LS-02 với `lead_name` chứa giá trị tìm kiếm.
		base_phone = int(frappe.generate_hash(length=6), 16) % 1_000_000
		cls.lead_t1 = _make_lead(f"091{base_phone:07d}", cls.users["a"], first_name=cls.SEARCH_T1)
		cls.lead_t2 = _make_lead(
			f"092{(base_phone + 1) % 1_000_000:07d}", cls.users["c"], first_name=cls.SEARCH_T2
		)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in (getattr(cls, "lead_t1", None), getattr(cls, "lead_t2", None)):
			if name and frappe.db.exists("CRM Lead", name):
				frappe.delete_doc("CRM Lead", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_ls02_search_widget_returns_only_own_leads_for_staff(self):
		"""LS-02 — `search_widget` (search box) trả đúng scope của `A`.

		`search_widget` gọi `frappe.get_list` với `ignore_permissions=0` cho user
		thường ⇒ áp `permission_query_conditions["CRM Lead"]` →
		`crm_cskh.permissions.lead_query_conditions`.
		"""
		from frappe.desk.search import search_widget

		frappe.set_user(self.users["a"])
		# Search term phải nằm trong field SEARCHABLE của CRM Lead
		# (`lead_name` — `search_index=1`); `search_widget` KHÔNG lọc theo
		# `searchfield` (xem docstring class).
		rows = search_widget(
			"CRM Lead", self.SEARCH_T1, searchfield="lead_name", page_length=50, as_dict=True
		)
		returned = {row.get("name") for row in rows}
		self.assertIn(
			self.lead_t1,
			returned,
			"Sanity: `A` phải thấy Lead của chính mình qua global search.",
		)
		for foreign, label in ((self.lead_t2, "Lead của C (Team 2)"),):
			with self.subTest(foreign=foreign):
				self.assertNotIn(
					foreign,
					returned,
					f"Global search KHÔNG được trả {label} cho actor A (ngoài scope).",
				)

	def test_ls02_search_link_does_not_leak_cross_scope_lead(self):
		"""LS-02 — `search_link` (Link Field) cũng phải giữ scope cho `A`.

		`search_link` là wrapper của `search_widget` → cùng code path permission-aware.
		"""
		from frappe.desk.search import search_link

		frappe.set_user(self.users["a"])
		# `search_link` → `search_widget` ⇒ cùng bộ filter `or_filters`
		# (name / title_field / search_fields) — dùng `lead_name` như ở test trên.
		results = search_link("CRM Lead", self.SEARCH_T2, page_length=50)
		returned = {item.get("value") for item in results}
		self.assertNotIn(
			self.lead_t2,
			returned,
			"Global search (link field) KHÔNG được trả Lead Team 2 cho actor A.",
		)

	def test_ls02_search_with_ignore_user_permissions_is_still_scope_bound(self):
		"""LS-02 — đường có tham số `ignore_user_permissions` vẫn bị chặn cho non-system.

		`search_widget` nhận `ignore_user_permissions`. Với actor thường, `has_permission`
		phải trả `False` (CRM-ADMIN không có read ⇒ `lead_has_permission` trả `False`),
		nên `ignore_permissions` không được bật. Test pin hành vi này để chống bypass.

		**Sửa lỗi assertion — hợp đồng thật là RAISE, không phải trả list rỗng
		(T1B, 2026-10-23).** Bản trước gọi `search_widget(...)` rồi assert
		`lead_t2 not in returned` ⇒ kỳ vọng hàm **trả list rỗng**. Nhưng theo
		source Frappe v15.123.1 `frappe/desk/search.py` (đã xác minh tĩnh):

		    ignore_permissions = doctype == "DocType" or (
		        cint(ignore_user_permissions)
		        and has_permission(doctype, ptype=..., parent_doctype=...)
		    )
		    values = frappe.get_list(..., ignore_permissions=ignore_permissions, ...)

		CRM-ADMIN không có DocPerm read trên CRM Lead ⇒ `has_permission` trả False
		⇒ `ignore_permissions` giữ **False** ⇒ `frappe.get_list` gọi
		`db_query.check_read_permission` ⇒ `frappe.has_permission(..., throw=True)`
		⇒ **raise `frappe.PermissionError`** (không phải trả list rỗng).

		Bằng chứng CI #39885152649: ERROR tại `search.py:222 → frappe.get_list →
		db_query.py:351 check_read_permission → db_query.py:906 _set_permission_map
		→ frappe/__init__.py:1083 raise frappe.PermissionError`.

		⇒ Hành vi fail-closed **đúng** là raise. `assertRaises(PermissionError)` mạnh
		hơn "trả list rỗng" (chặn hẳn request thay vì lọc rỗng), nên đây là
		siết chặt hơn, KHÔNG phải nới lỏng để làm CI xanh. Test vẫn pin đúng ý
		"bypass bằng `ignore_user_permissions=True` KHÔNG thành công".
		"""
		from frappe.desk.search import search_widget

		frappe.set_user(self.users["admin"])  # CRM-ADMIN: fail-closed (không read)
		with self.assertRaises(frappe.PermissionError):
			search_widget(
				"CRM Lead",
				self.SEARCH_T2,
				searchfield="lead_name",
				page_length=50,
				as_dict=True,
				ignore_user_permissions=True,
			)
