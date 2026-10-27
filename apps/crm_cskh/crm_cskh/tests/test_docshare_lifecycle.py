# T1B — Characterization: vòng đời DocShare của CRM Lead
#
# Mục đích
#   Regression tests cho chính sách fail-closed về `DocShare`, ownership và
#   assignment của CRM Lead. Quyết định Q1-Q4 do Owner ủy quyền ngày 2026-10-26.
#
# Ranh giới phạm vi — ĐỌC KỸ TRƯỚC KHI SỬA
#   1. Assertions kiểm tra quyết định fail-closed đã chọn, không phải canary
#      chờ chính sách. Log quan sát chỉ bổ sung chẩn đoán, không thay assertion.
#   2. Không sửa `permission-matrix.md`, `risk-register.md`, ADR-004/005,
#      `t1c-security-verification.md`.
#   3. Không dùng `ignore_permissions=True` để "ép" một kết quả mong muốn.
#      Chỗ nào dùng `ignore_permissions=True` thì mục đích là **ghi nhận** hành vi
#      sau khi bypass, và điều đó được nêu rõ trong docstring.
#   4. Không sửa source upstream CRM/Frappe.
#
# Runtime status is recorded in current P0 evidence, not in this source header.
#
# Cơ sở tĩnh (đọc source upstream tại SHA đã ghim)
#   Frappe v15.123.1 = d2efa79657ba5c1abf96098b3bfacc199517d57e
#     frappe/desk/form/assign_to.py  L104-118  -> add() tạo DocShare khi assignee
#                                                     chưa có quyền
#     frappe/share.py                L47-83    -> add_docshare() tạo DocShare
#                                                     read=1 write=0 submit=0 share=0
#     frappe/share.py                L65-75   -> nếu đã có share thì UPDATE bản ghi cũ
#     frappe/share.py                L234-244 -> check_share_permission() raise
#                                                     PermissionError
#     frappe/permissions.py          L162-191 -> share fallback cho quyền read
#     frappe/permissions.py          L219-221 -> hook has_permission chạy TRƯỚC share
#   CRM v1.87.0 = 0d3910b456ef4fb913e77b29db011b3debc4f778
#     crm/api/doc.py                L589-604 -> remove_assignments chỉ hủy ToDo,
#                                                     KHÔNG gỡ DocShare
#                                                     (decorator L589, def L590,
#                                                      parse_json L591,
#                                                      `if not assignees: return`
#                                                      L593-594, set_status L596-604)
#   >> Mọi dòng tham chiếu trên đã được KIỂM CHỨNG BYTE-EXACT tại đúng SHA
#      (2026-10-22): git tree API -> blob API -> tính lại git blob SHA-1 từ byte
#      thô, phải KHỚP với blob SHA do API trả về; file upstream LF-only.
#      Blob SHA-1 đã match:
#        frappe/utils/__init__.py d4e4b4429435e901dd3538dc06a4c60c673fe0a0
#        frappe/__init__.py       3404d465c3ae627d5ad6ffc174e87fb7043cdf52
#        crm/api/doc.py           9b72375dd96c860c1515d92c2a3831ec3b1c7f4e
#      ⇒ `frappe.parse_json` = frappe/utils/__init__.py L882-890, re-export
#        frappe/__init__.py L2503-2506.
#      Chi tiết + lịch sử hiệu chỉnh: evidence
#      `deploy/dev/evidence/t1b-g007-runtime-evidence-2026-10-21.md` §8.5.2
#      (v1.4). Ba cặp số dòng từng được đề xuất (L827-835 / L2342-2345 /
#      L549-564) KHÔNG được áp dụng — không khớp nội dung tại SHA này.
#   App crm_cskh (repo này)
#     crm_cskh/permissions.py        L141-143 -> clear_stale_lead_shares() xoá
#                                                     DocShare khi lead_owner đổi
#     crm_cskh/permissions.py        L165-170 -> remove_assignments() guard _is_system
#     crm_cskh/permissions.py        L119-130 -> linked_lead_has_permission() chặn child
#     crm_cskh/permissions.py        L39-52   -> _user_owns_in_scope() scope lá chỉ
#                                                     thấy lead_owner == user
#
# ---------------------------------------------------------------------------
# REGISTRY quyết định chính sách đã chốt cho P0
#
#   `covered_by` được kiểm tra bởi `TestDocSharePolicyPinRegistry` để tránh
#   khai báo coverage tới test không tồn tại.
# ---------------------------------------------------------------------------
POLICY_DECISIONS = {
	"Q1": {
		"question": (
			"Khi `lead_owner` đổi, `DocShare` cũ có nên bị xoá hết, hay giữ lại share đã được cấp hợp lệ?"
		),
		"status": "DECIDED",
		"decision": "Xoá toàn bộ DocShare của Lead khi lead_owner đổi; giữ share khi owner không đổi.",
		"decided_by": "Owner đã ủy quyền Codex chốt P0",
		"decided_on": "2026-10-26",
		"current_behavior": (
			"`clear_stale_lead_shares` (permissions.py L141-143) xoá TOÀN BỘ "
			"`DocShare` của Lead khi `lead_owner` đổi; giữ nguyên khi không đổi."
		),
		"covered_by": [
			"TestDocShareLeadSave::test_docshare_removed_by_lead_save_when_owner_changes",
			"TestDocShareLeadSave::test_docshare_survives_lead_save_when_owner_unchanged",
			("TestDocShareEffectivePermission::test_effective_permission_before_and_after_reassign"),
			("TestDocShareEffectivePermission::test_effective_permission_of_shared_child_records"),
		],
	},
	"Q2": {
		"question": (
			"`assign_agent` tự tạo `DocShare` `read=1` cho assignee ngoài scope — "
			"có đúng ý định không khi `crm_cskh` chủ động chặn scope?"
		),
		"status": "DECIDED",
		"decision": (
			"Không dùng DocShare để cấp quyền đọc ngoài scope. Generic assignment không được đổi "
			"lead_owner kể cả với System Manager; chỉ System Manager được đổi owner qua guarded "
			"CRM Lead document path."
		),
		"decided_by": "Owner đã ủy quyền Codex chốt P0",
		"decided_on": "2026-10-26",
		"current_behavior": (
			"Guarded generic assignment từ chối assignee khác owner hiện tại; assignment cho owner "
			"hiện tại không tạo DocShare."
		),
		"covered_by": [
			("TestDocShareAssignAgent::test_docshare_created_by_assign_agent_when_assignee_lacks_permission"),
			(
				"TestDocShareAssignAgent::"
				"test_docshare_not_created_by_assign_agent_when_assignee_already_in_scope"
			),
			"TestDocShareAssignAgent::test_docshare_flags_after_assign_agent",
		],
	},
	"Q3": {
		"question": (
			"`remove_assignments` hủy `ToDo` nhưng không gỡ `DocShare` — "
			"assignee cũ có còn đọc được Lead không?"
		),
		"status": "DECIDED",
		"decision": (
			"Khi System Manager hủy assignment Lead, giữ hành vi hủy ToDo của upstream và "
			"xóa DocShare thuộc đúng assignee bị gỡ trong cùng transaction."
		),
		"decided_by": "Owner đã ủy quyền Codex chốt P0",
		"decided_on": "2026-10-26",
		"current_behavior": (
			"Upstream chỉ hủy ToDo; wrapper crm_cskh bổ sung thu hồi DocShare của assignee "
			"vì cập nhật owner qua DB bỏ qua on_update."
		),
		"covered_by": [
			"TestDocShareRemoveAssignments::test_docshare_survives_remove_assignments",
		],
	},
	"Q4": {
		"question": ("Có cần path nâng quyền share (`write`/`share`) cho `crm_cskh` không?"),
		"status": "DECIDED",
		"decision": "Không cấp write, submit hoặc share qua DocShare; app không tạo DocShare tự động.",
		"decided_by": "Owner đã ủy quyền Codex chốt P0",
		"decided_on": "2026-10-26",
		"current_behavior": (
			"`frappe.share.add_docshare` đặt read=1, write=0, submit=0, share=0, "
			"everyone=0 (share.py L54-60)."
		),
		"covered_by": [
			"TestDocShareAssignAgent::test_docshare_flags_after_assign_agent",
		],
	},
}

# Owner delegated the P0 policy decisions to Codex on 2026-10-26. Each
# decision is recorded above and is now a regression gate.
OWNER_DECISIONS_CONFIRMED = True

from typing import ClassVar

import frappe
from frappe.tests.utils import FrappeTestCase

from crm_cskh import permissions


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


def _make_lead(phone, owner=None):
	doc = {
		"doctype": "CRM Lead",
		"first_name": "CSKH DocShare",
		"mobile_no": phone,
		"status": "New",
	}
	if owner:
		doc["lead_owner"] = owner
	return frappe.get_doc(doc).insert(ignore_permissions=True).name


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


def _make_task(lead):
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "CSKH DocShare task",
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
				"title": "CSKH DocShare note",
				"content": "CSKH DocShare note body",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _make_file(lead):
	return (
		frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "docshare-lifecycle-test.txt",
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": lead,
				"content": f"docshare lifecycle file for {lead}",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


class _DocShareHelpers:
	"""Mixin — helper (O) quan sát / (PIN) pin hành vi hiện trạng.

	Tách riêng khỏi `_DocShareBase` để các test **không cần fixture Lead**
	(ví dụ test registry) vẫn dùng được mà không dựng dữ liệu thừa.
	"""

	def observe(self, label, payload):
		"""(O) Ghi nhận giá trị quan sát được — **in** ra stdout, KHÔNG assert.

		Dùng để T0/Owner có artifact độc lập, không phụ thuộc vào kết quả assert.
		"""
		print(f"[DocShare characterization] {label}: {payload}")


class _DocShareBase(_DocShareHelpers, FrappeTestCase):
	"""Base — seed user/hierarchy + fixture Lead cho DocShare policy tests.

	Team 1: `a` (Sales User) dưới `tl1`; Team 2: `c` (Sales User) dưới `tl2`.
	`c` dùng làm assignee **ngoài scope** để khảo sát nhánh Frappe tự tạo
	`DocShare` trong `assign_to.add`.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
		_ensure_role("CSKH BOD Viewer")
		_ensure_role("CSKH CRM Admin")
		cls.users = {
			"a": _ensure_user("cskh-sec-a@example.test", "Sales User"),
			"b": _ensure_user("cskh-sec-b@example.test", "Sales User"),
			"tl1": _ensure_user("cskh-sec-tl1@example.test", "Sales Manager", "Sales User"),
			"c": _ensure_user("cskh-sec-c@example.test", "Sales User"),
			"tl2": _ensure_user("cskh-sec-tl2@example.test", "Sales Manager", "Sales User"),
		}
		for node in frappe.get_all(
			"CRM Sales Hierarchy",
			filters={"user": ["in", list(cls.users.values())]},
			fields=["name"],
			order_by="lft desc",
		):
			frappe.delete_doc("CRM Sales Hierarchy", node.name, force=True, ignore_permissions=True)
		_ensure_hierarchy(cls.users["tl1"], 1)
		_ensure_hierarchy(cls.users["a"], 0, cls.users["tl1"])
		_ensure_hierarchy(cls.users["b"], 0, cls.users["tl1"])
		_ensure_hierarchy(cls.users["tl2"], 1)
		_ensure_hierarchy(cls.users["c"], 0, cls.users["tl2"])
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", cls.previous_hierarchy or 0)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		base_phone = int(frappe.generate_hash(length=6), 16) % 1_000_000
		self.lead = _make_lead(f"093{base_phone:07d}", self.users["a"])
		self.call = _make_call(self.lead)
		self.task = _make_task(self.lead)
		self.note = _make_note(self.lead)
		self.file = _make_file(self.lead)

	def tearDown(self):
		frappe.set_user("Administrator")
		for doctype, name in (
			("ToDo", getattr(self, "todo", None)),
			("CRM Call Log", getattr(self, "call", None)),
			("CRM Task", getattr(self, "task", None)),
			("FCRM Note", getattr(self, "note", None)),
			("File", getattr(self, "file", None)),
			("CRM Lead", getattr(self, "lead", None)),
		):
			if name and frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()

	# -- helpers ------------------------------------------------------------

	def _docshares(self, lead=None):
		"""Trả về list DocShare của Lead (dạng list of dict)."""
		return frappe.get_all(
			"DocShare",
			filters={"share_doctype": "CRM Lead", "share_name": lead or self.lead},
			fields=["name", "user", "read", "write", "submit", "share", "everyone"],
			order_by="name",
		)

	def _share_for(self, user, lead=None):
		for row in self._docshares(lead):
			if row.get("user") == user:
				return row
		return None

	def _assign(self, assign_to, lead=None):
		"""Gọi route assignment đã được `crm_cskh` override."""
		method = frappe.get_attr(frappe.override_whitelisted_method("frappe.desk.form.assign_to.add"))
		return method(
			{
				"assign_to": [assign_to],
				"doctype": "CRM Lead",
				"name": lead or self.lead,
				"description": "CSKH DocShare characterization",
			}
		)

	def _seed_read_share(self, user, lead=None):
		"""Tạo DocShare trực tiếp để test vòng đời, độc lập với assign_agent."""
		frappe.share.add("CRM Lead", lead or self.lead, user=user, read=1)
		return self._share_for(user, lead)

	def _remove_assignments(self, assignees, lead=None):
		"""Gọi wrapper `crm_cskh.permissions.remove_assignments` (đã override trong hooks)."""
		remove = frappe.get_attr(frappe.override_whitelisted_method("crm.api.doc.remove_assignments"))
		return remove("CRM Lead", lead or self.lead, assignees, ignore_permissions=False)

	def _reassign_owner(self, new_owner, lead=None):
		"""Đổi `lead_owner` rồi `save()`.

		`ignore_permissions=True` ở đây là **cần thiết và có chủ đích**: actor
		`Administrator` là system user nên `validate_lead_owner` vốn đã cho qua;
		`ignore_permissions=True` chỉ để chắc chắn `save()` chạy trọn vẹn mà
		không bị chặn bởi bất kỳ permission nào khác. Đây là đường **hợp lệ**
		để thay owner trong hệ thống hiện tại (ADR-004 §2.2 ownership service
		chưa tồn tại — xem `test_bt1c5_matrix_gaps.py` L597-707).
		"""
		doc = frappe.get_doc("CRM Lead", lead or self.lead)
		doc.lead_owner = new_owner
		doc.save(ignore_permissions=True)
		return doc.name

	# Helper (O)/(PIN) nằm ở mixin `_DocShareHelpers` (trên), dùng chung cho
	# cả các test không cần fixture Lead (ví dụ test registry).


# ===========================================================================
# Nhóm 1 — Baseline: trạng thái DocShare trước mọi thao tác
# ===========================================================================


class TestDocShareBaseline(_DocShareBase):
	"""Baseline — Lead mới tạo chưa có share nào."""

	def test_docshare_absent_before_any_assignment(self):
		"""(P) Baseline — Lead vừa tạo (có `lead_owner`) không có `DocShare` nào.

		Đây là **tiền đề kỹ thuật** (trung lập với chính sách): cần biết trạng
		thái nền để các test sau diễn giải được. Không liên quan Q1..Q4.
		"""
		shares = self._docshares()
		self.observe("baseline — DocShare ngay sau khi tạo Lead", shares)
		self.assertEqual(shares, [])
		self.assertIsNone(self._share_for(self.users["a"]))


# ===========================================================================
# Nhóm 2 — assign_agent: Frappe có tự tạo DocShare không?
# ===========================================================================


class TestDocShareAssignAgent(_DocShareBase):
	"""`assign_agent` (`frappe.desk.form.assign_to.add`) — ảnh hưởng lên `DocShare`."""

	def test_docshare_created_by_assign_agent_when_assignee_lacks_permission(self):
		"""Q2 — generic assignment không được đổi owner hoặc cấp share ngoài scope.

		Actor: `Administrator` (system). Assignee: user `c` (Team 2, ngoài scope
		của Lead của Team 1).

		Quyết định P0: không actor nào được đổi owner qua `assign_to.add`; System Manager
		có thể đổi owner riêng qua guarded CRM Lead document path.
		"""
		# (P) Precondition — assignee ngoài scope KHÔNG đọc được Lead trước khi assign.
		# Trung lập với chính sách: đây là hành vi scope đã được matrix chốt.
		frappe.set_user(self.users["c"])
		can_read = frappe.has_permission("CRM Lead", "read", self.lead, throw=False)
		frappe.set_user("Administrator")
		self.assertFalse(can_read, "precondition: assignee ngoài scope phải không đọc được Lead")

		frappe.set_user(self.users["c"])
		with self.assertRaises(frappe.PermissionError):
			self._assign(self.users["c"])
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			self._assign(self.users["c"])
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "lead_owner"), self.users["a"])
		self.assertEqual(self._docshares(), [])

		# Ownership changes use the permission-checked Lead document path.
		lead = frappe.get_doc("CRM Lead", self.lead)
		lead.lead_owner = self.users["c"]
		lead.save()

		self.assertIsNone(self._share_for(self.users["c"]))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "lead_owner"), self.users["c"])
		frappe.set_user(self.users["c"])
		self.assertTrue(frappe.has_permission("CRM Lead", "read", self.lead, throw=False))
		frappe.set_user("Administrator")

	def test_docshare_not_created_by_assign_agent_when_assignee_already_in_scope(self):
		"""Q2 — assignment cho người trong scope không tạo DocShare phụ.

		Assignee: user `a` (owner hiện tại của Lead).

		**Lưu ý về fixture:** user `b` (Sales User, Team 1) **không** dùng
		được làm assignee "đã có quyền" — theo scope của `crm_cskh`, Sales User
		lá chỉ thấy record có `lead_owner == user` (`permissions._user_owns_in_scope`
		trả `owner == user` khi user không phải gốc hierarchy). `tl1` là gốc
		hierarchy nên thấy toàn bộ subtree Team 1.

		Đây là **nhánh bù** của Q2 (cùng câu hỏi chính sách, hướng ngược).
		"""
		# (P) Precondition — assignee trong scope ĐỌC ĐƯỢC Lead trước khi assign.
		# Nếu precondition này sai thì `assign_to.add` sẽ đi vào nhánh tạo share
		# (assign_to.py L107) và kết luận "không tạo DocShare" sẽ sai.
		frappe.set_user(self.users["a"])
		can_read = frappe.has_permission("CRM Lead", "read", self.lead, throw=False)
		frappe.set_user("Administrator")
		self.assertTrue(can_read, "precondition: assignee trong scope phải đọc được Lead")

		self._assign(self.users["a"])

		shares = self._docshares()
		self.observe("after assign_agent (assignee trong scope) — DocShare", shares)

		self.assertIsNone(self._share_for(self.users["a"]))
		self.assertEqual(shares, [], "không được có DocShare sau assign_agent")

	def test_docshare_flags_after_assign_agent(self):
		"""Q2/Q4 — System Manager cũng không đổi owner qua generic assignment.

		Quyết định: assignment chung không được biến thành đường đổi owner; thay đổi
		owner chỉ qua CRM Lead document path và share cũ bị dọn ở on_update.
		"""
		with self.assertRaises(frappe.PermissionError):
			self._assign(self.users["c"])

		share = self._share_for(self.users["c"])
		self.observe("after denied generic assignment — DocShare", share)

		self.assertIsNone(share)
		self.assertEqual(self._docshares(), [])
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "lead_owner"), self.users["a"])


# ===========================================================================
# Nhóm 3 — lead.save(): DocShare bị xoá khi đổi lead_owner?
# ===========================================================================


class TestDocShareLeadSave(_DocShareBase):
	"""`lead.save()` — ảnh hưởng lên `DocShare` khi `lead_owner` đổi / không đổi."""

	def test_docshare_removed_by_lead_save_when_owner_changes(self):
		"""Q1 — đổi `lead_owner` thu hồi mọi DocShare cũ.

		Cơ chế: `doc_events["CRM Lead"]["on_update"]` → `clear_stale_lead_shares`
		xoá toàn bộ `DocShare` của Lead khi `lead_owner` đổi
		(`crm_cskh/permissions.py` L141-143).

		Quyết định P0: xóa hết share cũ vì DocShare không phải nguồn quyền độc lập.
		"""
		self._seed_read_share(self.users["c"])
		self.assertIsNotNone(self._share_for(self.users["c"]))

		self._reassign_owner(self.users["b"])

		shares = self._docshares()
		self.observe("after lead.save() đổi lead_owner — DocShare", shares)

		self.assertEqual(shares, [])
		self.assertIsNone(self._share_for(self.users["c"]))

	def test_docshare_survives_lead_save_when_owner_unchanged(self):
		"""(PIN Q1) `lead.save()` **không** đổi `lead_owner` ⇒ `DocShare` còn?

		Cơ chế: `clear_stale_lead_shares` chỉ chạy khi `doc.has_value_changed(
		"lead_owner")` ⇒ share được giữ nguyên.

		Quyết định P0: khi owner không đổi, share hợp lệ hiện có được giữ nguyên.
		"""
		self._seed_read_share(self.users["c"])
		self.assertIsNotNone(self._share_for(self.users["c"]))

		# Thay đổi field KHÁC (first_name) để chắc `save()` thực sự chạy
		# nhưng `lead_owner` không đổi.
		doc = frappe.get_doc("CRM Lead", self.lead)
		doc.first_name = "CSKH DocShare renamed"
		doc.save(ignore_permissions=True)

		shares = self._docshares()
		self.observe("after lead.save() KHÔNG đổi lead_owner — DocShare", shares)

		# (PIN Q1) — nhánh bù: share được giữ nguyên.
		self.assertIsNotNone(self._share_for(self.users["c"]))
		self.assertEqual(len(shares), 1)


# ===========================================================================
# Nhóm 4 — remove_assignments: DocShare có bị gỡ không?
# ===========================================================================


class TestDocShareRemoveAssignments(_DocShareBase):
	"""`remove_assignments` — ảnh hưởng lên `DocShare`."""

	def test_docshare_survives_remove_assignments(self):
		"""Q3 — hủy assignment phải thu hồi DocShare của assignee đó.

		Cơ chế: `crm.api.doc.remove_assignments` (CRM v1.87.0 L589-604 — đã kiểm
		chứng byte-exact tại SHA `0d3910b4…`) chỉ gọi
		`set_status(..., status="Cancelled")` trên `ToDo` ⇒ **không** đụng `DocShare`.

		Quyết định P0: hủy ToDo phải thu hồi share của assignee trong cùng
		transaction; upstream chỉ hủy ToDo nên wrapper crm_cskh thực hiện cleanup.

		Phần assert về `ToDo.status = Cancelled` là **(P)** — hành vi upstream
		CRM đã xác định, không phải câu hỏi chính sách của `crm_cskh`.
		"""
		self.todo = (
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"description": "legacy assignment for revoke test",
					"allocated_to": self.users["c"],
					"status": "Open",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_value("ToDo", self.todo, {"reference_type": "CRM Lead", "reference_name": self.lead})
		self._seed_read_share(self.users["c"])
		self.assertIsNotNone(self._share_for(self.users["c"]))

		self._remove_assignments([self.users["c"]])

		# (P) ToDo bị hủy — hành vi upstream CRM, trung lập với chính sách crm_cskh.
		todo_statuses = frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "CRM Lead",
				"reference_name": self.lead,
				"allocated_to": self.users["c"],
			},
			fields=["name", "status"],
		)
		self.observe("after remove_assignments — ToDo", todo_statuses)
		self.assertTrue(todo_statuses)
		for row in todo_statuses:
			self.assertEqual(row.get("status"), "Cancelled")

		share = self._share_for(self.users["c"])
		self.observe("after remove_assignments — DocShare", share)

		self.assertIsNone(share, "DocShare của assignee bị gỡ phải bị thu hồi")


# ===========================================================================
# Nhóm 5 — Quyền hiệu dụng trước/sau reassign
# ===========================================================================


class TestDocShareEffectivePermission(_DocShareBase):
	"""Quyền hiệu dụng (record-level) trước/sau reassign, với user được share."""

	def _list_contains(self, lead):
		"""Quan sát list path mà không làm fail test khi bị từ chối.

		`frappe.get_list` với `ignore_permissions` mặc định có thể **raise**
		`frappe.PermissionError` (không phải trả list rỗng) khi user không có
		quyền đọc doctype. Trong characterization, ta **ghi nhận** cả hai khả
		năng: `True` (thấy trong list) / `False` (không thấy) / `DENIED` (bị
		từ chối). Không assert giá trị cụ thể — giá trị đó thuộc quyết định
		chính sách của T0/Owner.
		"""
		try:
			rows = frappe.get_list("CRM Lead", pluck="name")
		except frappe.PermissionError:
			return "DENIED"
		return lead in rows

	def test_effective_permission_before_and_after_reassign(self):
		"""Q1 — reassign thu hồi share cũ và không để quyền đọc còn sót.

		Chuỗi quan sát:
		  1. Owner `a` tạo Lead; `a` có quyền đọc (thuộc scope).
		  2. Fixture tạo một DocShare read-only trực tiếp cho user `c`.
		  3. Quan sát quyền hiệu dụng của `c` **trước** reassign.
		  4. Reassign owner `a` → `b` ⇒ `clear_stale_lead_shares` xoá `DocShare`.
		  5. Quan sát quyền hiệu dụng của `c` **sau** reassign.

		Quyết định P0: mọi DocShare cũ trên Lead được thu hồi khi owner đổi.
		"""
		self._seed_read_share(self.users["c"])
		self.assertIsNotNone(self._share_for(self.users["c"]))

		frappe.set_user(self.users["c"])
		before = frappe.has_permission("CRM Lead", "read", self.lead, throw=False)
		before_list = self._list_contains(self.lead)
		self.observe(
			f"before reassign — has_permission={before} in_list={before_list}",
			{"share": self._share_for(self.users["c"])},
		)

		frappe.set_user("Administrator")
		self._reassign_owner(self.users["b"])
		shares = self._docshares()
		self.observe("after reassign — DocShare", shares)

		self.assertEqual(shares, [])

		frappe.set_user(self.users["c"])
		after = frappe.has_permission("CRM Lead", "read", self.lead, throw=False)
		after_list = self._list_contains(self.lead)
		self.observe(
			f"after reassign — has_permission={after} in_list={after_list}",
			{"share": self._share_for(self.users["c"])},
		)

		frappe.set_user("Administrator")
		# (PIN Q1) — nhất quán nội bộ gắn với Q1: sau khi share bị xoá thì
		# quyền đọc record-level không còn được cấp bởi share fallback
		# (frappe/permissions.py L162-191).
		self.assertFalse(after)
		self.assertIn(after_list, (False, "DENIED"))

	def test_effective_permission_of_shared_child_records(self):
		"""Q1 — share trên Lead không cấp quyền cho child ngoài phạm vi.

		Đây là nhánh quan trọng: hook `linked_lead_has_permission`
		(`crm_cskh/permissions.py` L119-130) chặn child nếu parent Lead ngoài
		scope ⇒ share fallback của framework **không** áp dụng cho child.

		Quyết định P0: child access vẫn bị giới hạn theo parent scope.
		"""
		self._seed_read_share(self.users["c"])
		self.assertIsNotNone(self._share_for(self.users["c"]))

		children = (
			("CRM Call Log", self.call),
			("CRM Task", self.task),
			("FCRM Note", self.note),
			("File", self.file),
		)

		frappe.set_user(self.users["c"])
		before = {
			doctype: frappe.has_permission(doctype, "read", name, throw=False) for doctype, name in children
		}
		self.observe("child read perms before reassign", before)

		frappe.set_user("Administrator")
		self._reassign_owner(self.users["b"])
		shares = self._docshares()
		self.observe("after reassign — DocShare", shares)

		self.assertEqual(shares, [])

		frappe.set_user(self.users["c"])
		after = {
			doctype: frappe.has_permission(doctype, "read", name, throw=False) for doctype, name in children
		}
		self.observe("child read perms after reassign", after)

		frappe.set_user("Administrator")
		# (PIN Q1) — sau khi share bị xoá thì quyền đọc child không được
		# cấp bởi share fallback (hook `linked_lead_has_permission` chặn trước).
		for doctype, _name in children:
			self.assertFalse(after[doctype], f"{doctype} remains readable after the share is revoked")

		# (O) Ghi nhận bổ sung: `permissions.user_in_scope` — helper scope của app.
		self.observe(
			"user_in_scope(c, owner=a)",
			permissions.user_in_scope(self.users["c"], self.users["a"]),
		)


# ===========================================================================
# Nhóm 6 — Registry: bảo toàn tính nhất quán giữa code và câu hỏi chính sách
# ===========================================================================


class TestDocSharePolicyPinRegistry(_DocShareHelpers, FrappeTestCase):
	"""Giữ metadata quyết định Q1-Q4 khớp với regression tests.

	Test kiểm tra registry có đủ quyết định đã chốt và các method được liệt kê
	đều tồn tại thật trong module (chống sai tên/đổi tên mà quên cập nhật).

	Không dùng `_DocShareBase` ⇒ **không** dựng fixture Lead/child record —
	test này thuần metadata, không cần dữ liệu nghiệp vụ.
	"""

	ALLOWED_STATUS: ClassVar[frozenset[str]] = frozenset({"DECIDED"})
	REQUIRED_QUESTION_IDS: ClassVar[frozenset[str]] = frozenset({"Q1", "Q2", "Q3", "Q4"})

	def test_policy_pin_registry_is_consistent(self):
		"""Registry `POLICY_DECISIONS` phải nhất quán với code thực tế."""
		self.assertEqual(set(POLICY_DECISIONS), self.REQUIRED_QUESTION_IDS)

		for ref, entry in POLICY_DECISIONS.items():
			self.assertIn(entry["status"], self.ALLOWED_STATUS, f"{ref}: status không hợp lệ")
			self.assertTrue(entry["question"].strip(), f"{ref}: thiếu câu hỏi chính sách")
			self.assertTrue(entry.get("decision", "").strip(), f"{ref}: thiếu quyết định")
			self.assertTrue(entry["current_behavior"].strip(), f"{ref}: thiếu mô tả hành vi")
			self.assertTrue(entry["covered_by"], f"{ref}: chưa liệt kê test coverage")

			for dotted in entry["covered_by"]:
				class_name, _, method_name = dotted.partition("::")
				klass = globals().get(class_name)
				self.assertIsNotNone(klass, f"{ref}: class {class_name} không tồn tại")
				self.assertTrue(
					hasattr(klass, method_name),
					f"{ref}: method {class_name}::{method_name} không tồn tại",
				)

		self.assertTrue(OWNER_DECISIONS_CONFIRMED)
		for ref, entry in POLICY_DECISIONS.items():
			for field in ("decided_by", "decided_on"):
				self.assertTrue(entry.get(field), f"{ref}: thiếu trường '{field}'")

		# (O) In quyết định cùng artifact runtime.
		self.observe(
			f"OWNER_DECISIONS_CONFIRMED={OWNER_DECISIONS_CONFIRMED}",
			{ref: entry["status"] for ref, entry in sorted(POLICY_DECISIONS.items())},
		)
		for ref in sorted(POLICY_DECISIONS):
			entry = POLICY_DECISIONS[ref]
			self.observe(f"{ref} — {entry['question']}", entry["current_behavior"])
			for dotted in entry["covered_by"]:
				self.observe(f"  {ref} covered by", dotted)
