import frappe
from frappe.client import get as client_get
from frappe.client import get_list as client_get_list
from frappe.tests.utils import FrappeTestCase

from crm_cskh import permissions
from crm_cskh.api import guarded_crm


class TestPermissionBoundaries(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
		cls.ensure_role("CSKH BOD Viewer")
		cls.ensure_role("CSKH CRM Admin")
		cls.users = {
			"a": cls.ensure_user("cskh-sec-a@example.test", "Sales User"),
			"b": cls.ensure_user("cskh-sec-b@example.test", "Sales User"),
			"tl1": cls.ensure_user("cskh-sec-tl1@example.test", "Sales Manager", "Sales User"),
			"c": cls.ensure_user("cskh-sec-c@example.test", "Sales User"),
			"tl2": cls.ensure_user("cskh-sec-tl2@example.test", "Sales Manager", "Sales User"),
			"outside": cls.ensure_user("cskh-sec-outside@example.test", "Sales Manager", "Sales User"),
			"bod": cls.ensure_user("cskh-sec-bod@example.test", "CSKH BOD Viewer"),
			"admin": cls.ensure_user("cskh-sec-admin@example.test", "CSKH CRM Admin"),
		}
		for node in frappe.get_all(
			"CRM Sales Hierarchy",
			filters={"user": ["in", list(cls.users.values())]},
			fields=["name"],
			order_by="lft desc",
		):
			frappe.delete_doc("CRM Sales Hierarchy", node.name, force=True, ignore_permissions=True)
		cls.ensure_hierarchy(cls.users["tl1"], 1)
		cls.ensure_hierarchy(cls.users["a"], 0, cls.users["tl1"])
		cls.ensure_hierarchy(cls.users["b"], 0, cls.users["tl1"])
		cls.ensure_hierarchy(cls.users["tl2"], 1)
		cls.ensure_hierarchy(cls.users["c"], 0, cls.users["tl2"])
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", cls.previous_hierarchy or 0)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def ensure_role(cls, role_name):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)

	@classmethod
	def ensure_user(cls, email, *roles):
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

	@classmethod
	def ensure_hierarchy(cls, user, is_group, reports_to_user=None):
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

	def setUp(self):
		frappe.set_user("Administrator")
		base_phone = int(frappe.generate_hash(length=6), 16) % 1_000_000
		self.leads = {
			"a": self.make_lead(f"090{base_phone:07d}", self.users["a"]),
			"b": self.make_lead(f"090{(base_phone + 1) % 1_000_000:07d}", self.users["b"]),
			"c": self.make_lead(f"090{(base_phone + 2) % 1_000_000:07d}", self.users["c"]),
		}
		self.call_b = self.make_call(self.leads["b"])
		self.call_a = self.make_call(self.leads["a"])
		self.note_b = self.make_note(self.leads["b"])
		self.note_a = self.make_note(self.leads["a"])
		self.task_b = self.make_task(self.leads["b"])
		self.task_a = self.make_task(self.leads["a"])
		self.comment_b = self.make_comment(self.leads["b"])
		self.file_b = self.make_file(self.leads["b"])
		self.file_a = self.make_file(self.leads["a"])

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_lead(self, phone, owner):
		return (
			frappe.get_doc(
				{
					"doctype": "CRM Lead",
					"first_name": "CSKH Security Test",
					"mobile_no": phone,
					"status": "New",
					"lead_owner": owner,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_call(self, lead):
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

	def make_note(self, lead):
		return (
			frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": "Private security note",
					"content": "Private note body",
					"reference_doctype": "CRM Lead",
					"reference_docname": lead,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_task(self, lead):
		return (
			frappe.get_doc(
				{
					"doctype": "CRM Task",
					"title": "Private security task",
					"status": "Todo",
					"reference_doctype": "CRM Lead",
					"reference_docname": lead,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_comment(self, lead):
		return (
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"content": "Private comment body",
					"reference_doctype": "CRM Lead",
					"reference_name": lead,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def make_file(self, lead):
		return (
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "private-security-test.txt",
					"is_private": 1,
					"attached_to_doctype": "CRM Lead",
					"attached_to_name": lead,
					"content": f"private file body for {lead}",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def test_lead_list_and_get_data_only_return_owner_scope(self):
		frappe.set_user(self.users["a"])
		filters = {"name": ["in", list(self.leads.values())]}
		self.assertEqual(frappe.get_list("CRM Lead", filters=filters, pluck="name"), [self.leads["a"]])
		self.assertEqual(
			{row.name for row in client_get_list("CRM Lead", fields=["name"], filters=filters)},
			{self.leads["a"]},
		)
		client_get("CRM Lead", self.leads["a"])
		with self.assertRaises(frappe.PermissionError):
			client_get("CRM Lead", self.leads["b"])
		from crm.api.doc import get_data

		data = get_data("CRM Lead", filters, "creation desc", rows=["name", "mobile_no", "email"])
		self.assertEqual({row["name"] for row in data["data"]}, {self.leads["a"]})
		self.assertEqual(
			data["data"][0]["mobile_no"], frappe.db.get_value("CRM Lead", self.leads["a"], "mobile_no")
		)
		spoofed = get_data("CRM Lead", {"lead_owner": self.users["b"]}, "creation desc", rows=["name"])
		self.assertEqual(spoofed["data"], [])

	def test_get_data_does_not_return_permlevel_fields(self):
		from crm.api.doc import get_data

		fieldname = "custom_cskh_sec_restricted"
		custom_field = frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "CRM Lead",
				"fieldname": fieldname,
				"label": "CSKH Security Test Field",
				"fieldtype": "Data",
				"insert_after": "first_name",
				"permlevel": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(doctype="CRM Lead")
		try:
			frappe.db.set_value("CRM Lead", self.leads["a"], fieldname, "synthetic restricted value")
			frappe.db.commit()
			frappe.set_user(self.users["a"])
			result = get_data(
				"CRM Lead",
				{"name": self.leads["a"]},
				"creation desc",
				rows=["name", fieldname],
			)
			self.assertEqual(len(result["data"]), 1)
			self.assertFalse(result["data"][0].get(fieldname))
		finally:
			frappe.set_user("Administrator")
			if frappe.db.exists("Custom Field", custom_field.name):
				frappe.delete_doc("Custom Field", custom_field.name, force=True, ignore_permissions=True)
			frappe.clear_cache(doctype="CRM Lead")

	def test_child_lists_and_activity_reads_stay_with_parent_scope(self):
		frappe.set_user(self.users["a"])
		for doctype, link_field, allowed in (
			("CRM Call Log", "reference_docname", {self.call_a}),
			("CRM Task", "reference_docname", {self.task_a}),
			("FCRM Note", "reference_docname", {self.note_a}),
			("Comment", "reference_name", set()),
			("File", "attached_to_name", {self.file_a}),
		):
			try:
				rows = frappe.get_list(
					doctype,
					filters={link_field: ["in", [self.leads["a"], self.leads["b"]]]},
					pluck="name",
				)
			except frappe.PermissionError:
				rows = []
			with self.subTest(doctype=doctype):
				self.assertLessEqual(set(rows), allowed)
		from crm.api.activities import get_activities

		with self.assertRaises(frappe.PermissionError):
			get_activities(self.leads["b"])

	def test_private_file_download_and_stale_docshare_are_revoked(self):
		from frappe.core.doctype.file.utils import find_file_by_url

		frappe.set_user("Administrator")
		frappe.share.add("CRM Lead", self.leads["a"], user=self.users["a"], read=1)
		lead = frappe.get_doc("CRM Lead", self.leads["a"])
		lead.lead_owner = self.users["b"]
		lead.save(ignore_permissions=True)

		frappe.set_user(self.users["a"])
		self.assertIsNone(find_file_by_url(frappe.db.get_value("File", self.file_a, "file_url")))
		self.assertFalse(frappe.has_permission("CRM Lead", "read", self.leads["a"], throw=False))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Lead", self.leads["a"]).check_permission("read")
		for doctype, name in (
			("CRM Call Log", self.call_a),
			("CRM Task", self.task_a),
			("FCRM Note", self.note_a),
			("File", self.file_a),
		):
			with self.subTest(doctype=doctype), self.assertRaises(frappe.PermissionError):
				frappe.get_doc(doctype, name).check_permission("read")
		for endpoint in (
			guarded_crm.get_linked_calls,
			guarded_crm.get_linked_notes,
			guarded_crm.get_linked_tasks,
		):
			with self.subTest(endpoint=endpoint.__name__), self.assertRaises(frappe.PermissionError):
				endpoint(self.leads["a"])
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.get_attachments("CRM Lead", self.leads["a"])
		frappe.set_user(self.users["b"])
		self.assertIsNotNone(find_file_by_url(frappe.db.get_value("File", self.file_a, "file_url")))

	def test_export_and_unscoped_reporting_are_blocked_for_staff_and_managers(self):
		routes = (
			("frappe.core.doctype.data_export.exporter.export_data", ()),
			("frappe.desk.reportview.export_query", ()),
			("frappe.desk.query_report.run", ("Sales Analytics",)),
			("frappe.desk.query_report.export_query", ()),
		)
		for user in (self.users["a"], self.users["tl1"], self.users["bod"], self.users["admin"]):
			frappe.set_user(user)
			for route, args in routes:
				method = frappe.get_attr(frappe.override_whitelisted_method(route))
				with self.subTest(user=user, method=route), self.assertRaises(frappe.PermissionError):
					method(*args)
			for route in (
				"crm.integrations.api.get_contact_by_phone_number",
				"crm.integrations.api.get_contact_lead_or_deal_from_number",
			):
				method = frappe.get_attr(frappe.override_whitelisted_method(route))
				with self.subTest(user=user, method=route), self.assertRaises(frappe.PermissionError):
					method("P0-LOOKUP-NO-MATCH")
			if user == self.users["tl1"]:
				for route, args in (
					("crm.api.dashboard.get_dashboard", ()),
					("crm.api.dashboard.get_chart", ("total_leads", "number")),
				):
					method = frappe.get_attr(frappe.override_whitelisted_method(route))
					with self.subTest(user=user, method=route), self.assertRaises(frappe.PermissionError):
						method(*args)
		frappe.set_user("Administrator")
		self.assertTrue(frappe.has_permission("CRM Lead", "export", throw=False))
		self.assertEqual(
			frappe.get_attr(
				frappe.override_whitelisted_method("crm.integrations.api.get_contact_by_phone_number")
			)("P0-LOOKUP-NO-MATCH"),
			{"mobile_no": "P0-LOOKUP-NO-MATCH"},
		)

	def test_child_records_and_direct_private_file_are_record_scoped(self):
		frappe.set_user(self.users["a"])
		for doctype, name in (
			("CRM Call Log", self.call_a),
			("FCRM Note", self.note_a),
			("CRM Task", self.task_a),
			("File", self.file_a),
		):
			frappe.get_doc(doctype, name).check_permission("read")
		for doctype, name in (
			("CRM Call Log", self.call_b),
			("FCRM Note", self.note_b),
			("CRM Task", self.task_b),
			("Comment", self.comment_b),
			("File", self.file_b),
		):
			with self.subTest(doctype=doctype), self.assertRaises(frappe.PermissionError):
				frappe.get_doc(doctype, name).check_permission("read")
		from crm.fcrm.doctype.crm_call_log.crm_call_log import get_call_log

		with self.assertRaises(frappe.PermissionError):
			get_call_log(self.call_b)
		from frappe.client import set_value

		for doctype, name, field in (
			("CRM Call Log", self.call_b, "status"),
			("FCRM Note", self.note_b, "title"),
			("CRM Task", self.task_b, "title"),
		):
			with self.subTest(doctype=doctype), self.assertRaises(frappe.PermissionError):
				set_value(doctype, name, field, "Cross-scope change")

	def test_team_leader_sees_team_but_not_other_team(self):
		frappe.set_user(self.users["tl1"])
		visible = set(frappe.get_list("CRM Lead", pluck="name"))
		self.assertIn(self.leads["a"], visible)
		self.assertIn(self.leads["b"], visible)
		self.assertNotIn(self.leads["c"], visible)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Lead", self.leads["c"]).check_permission("read")

	def test_sales_manager_without_hierarchy_is_denied(self):
		frappe.set_user(self.users["outside"])
		self.assertEqual(frappe.get_list("CRM Lead", pluck="name"), [])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Lead", self.leads["a"]).check_permission("read")

	def test_linked_activity_endpoints_check_parent_before_returning_data(self):
		frappe.set_user(self.users["a"])
		for endpoint in (
			guarded_crm.get_linked_calls,
			guarded_crm.get_linked_notes,
			guarded_crm.get_linked_tasks,
		):
			with self.subTest(endpoint=endpoint.__name__), self.assertRaises(frappe.PermissionError):
				endpoint(self.leads["b"])
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.get_attachments("CRM Lead", self.leads["b"])
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.get_linked_docs_of_document("CRM Lead", self.leads["b"])
		with self.assertRaises(frappe.PermissionError):
			guarded_crm.get_assigned_users("CRM Lead", self.leads["b"])
		calls = guarded_crm.get_linked_calls(self.leads["a"])
		self.assertEqual([call["name"] for call in calls["calls"]], [self.call_a])
		self.assertEqual(guarded_crm.get_attachments("CRM Lead", self.leads["a"])[0]["name"], self.file_a)
		self.assertEqual(guarded_crm.get_assigned_users("CRM Lead", self.leads["a"]), [self.users["a"]])

	def test_owner_change_and_assignment_bypass_are_denied(self):
		frappe.set_user(self.users["a"])
		lead = frappe.get_doc("CRM Lead", self.leads["a"])
		lead.lead_owner = self.users["c"]
		with self.assertRaises(frappe.PermissionError):
			lead.save(ignore_permissions=True)

		with self.assertRaises(frappe.PermissionError):
			frappe.get_attr(frappe.override_whitelisted_method("crm.api.doc.remove_assignments"))(
				"CRM Lead", self.leads["a"], [self.users["a"]], ignore_permissions=True
			)
		with self.assertRaises(frappe.PermissionError):
			frappe.share.add("CRM Lead", self.leads["a"], user=self.users["c"], write=1)

	def test_bulk_delete_and_unlink_cannot_touch_cross_scope_records(self):
		frappe.set_user(self.users["a"])
		from crm.api.doc import remove_linked_doc_reference

		delete = frappe.get_attr(frappe.override_whitelisted_method("crm.api.doc.delete_bulk_docs"))
		with self.assertRaises(frappe.PermissionError):
			delete("CRM Lead", [self.leads["b"]])
		remove_linked_doc_reference([{"doctype": "CRM Call Log", "docname": self.call_b}])
		self.assertTrue(frappe.db.exists("CRM Lead", self.leads["b"]))
		self.assertEqual(
			frappe.db.get_value("CRM Call Log", self.call_b, "reference_docname"), self.leads["b"]
		)

	def test_stale_todo_cannot_restore_access_after_owner_changes(self):
		frappe.set_user("Administrator")
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": self.users["a"],
				"reference_type": "CRM Lead",
				"reference_name": self.leads["a"],
				"description": "Existing assignment",
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		lead = frappe.get_doc("CRM Lead", self.leads["a"])
		lead.lead_owner = self.users["b"]
		lead.save(ignore_permissions=True)
		frappe.set_user(self.users["a"])
		self.assertNotIn(self.leads["a"], frappe.get_list("CRM Lead", pluck="name"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Lead", self.leads["a"]).check_permission("read")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("ToDo", todo.name).check_permission("read")
		frappe.set_user(self.users["b"])
		self.assertIn(self.leads["a"], frappe.get_list("CRM Lead", pluck="name"))

	def test_cross_team_transfer_blocks_old_owner_and_leader(self):
		frappe.set_user("Administrator")
		lead = frappe.get_doc("CRM Lead", self.leads["c"])
		lead.lead_owner = self.users["a"]
		lead.save(ignore_permissions=True)
		frappe.set_user(self.users["c"])
		self.assertNotIn(self.leads["c"], frappe.get_list("CRM Lead", pluck="name"))
		frappe.set_user(self.users["a"])
		self.assertIn(self.leads["c"], frappe.get_list("CRM Lead", pluck="name"))
		frappe.set_user(self.users["tl2"])
		self.assertNotIn(self.leads["c"], frappe.get_list("CRM Lead", pluck="name"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("CRM Lead", self.leads["c"]).check_permission("read")
		frappe.set_user(self.users["tl1"])
		self.assertIn(self.leads["c"], frappe.get_list("CRM Lead", pluck="name"))

	def test_direct_todo_assignment_is_denied_even_with_ignore_permissions(self):
		frappe.set_user(self.users["a"])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"allocated_to": self.users["c"],
					"reference_type": "CRM Lead",
					"reference_name": self.leads["a"],
					"description": "Attempted cross-team assignment",
					"status": "Open",
				}
			).insert(ignore_permissions=True)

	def test_staff_cannot_duplicate_assignment_rules(self):
		frappe.set_user("Administrator")
		rule = frappe.get_doc(
			{
				"doctype": "Assignment Rule",
				"name": "P0-SEC24-Rule",
				"document_type": "CRM Lead",
				"description": "P0 security probe",
				"assign_condition": "1",
				"rule": "Round Robin",
				"users": [{"user": "Administrator"}],
				"assignment_days": [{"day": "Monday"}],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.delete_doc("Assignment Rule", rule.name, force=True, ignore_permissions=True)
		)
		frappe.set_user(self.users["a"])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_attr("crm.api.assignment_rule.duplicate_assignment_rule")(
				rule.name, "P0-SEC24-Duplicate"
			)
		self.assertFalse(frappe.db.exists("Assignment Rule", "P0-SEC24-Duplicate"))

	def test_bod_viewer_cannot_read_leads_or_manage_users(self):
		frappe.set_user(self.users["bod"])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("CRM Lead", pluck="name")
		self.assertFalse(frappe.has_permission("User", "create", throw=False))
		self.assertFalse(frappe.has_permission("Role", "create", throw=False))
		self.assertFalse(frappe.has_permission("Data Import", "create", throw=False))
		self.assertNotIn("System Manager", frappe.get_roles(self.users["bod"]))

	def test_crm_admin_role_fails_closed_until_controlled_service_exists(self):
		frappe.set_user(self.users["admin"])
		self.assertNotIn("System Manager", frappe.get_roles(self.users["admin"]))
		self.assertFalse(frappe.has_permission("CRM Lead", "read", throw=False))
		self.assertFalse(frappe.has_permission("CRM Lead", "write", throw=False))

	def test_seed_owner_scope_and_hierarchy_are_applied(self):
		self.assertTrue(permissions._user_owns_in_scope(self.users["a"], self.users["a"]))
		self.assertTrue(permissions.user_in_scope(self.users["tl1"], self.users["a"]))
		self.assertFalse(permissions.user_in_scope(self.users["tl1"], self.users["c"]))
