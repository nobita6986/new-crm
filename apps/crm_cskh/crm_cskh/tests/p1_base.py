"""Shared fixtures for the P1 test suite.

The P1 tests are a **separate suite** from the P0 core inventory (99 methods):
they live in `crm_cskh.tests.test_p1_*` so the P0 gate is not renumbered.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from crm_cskh.services.phone_identity import normalize_vn_phone


class P1TestCase(FrappeTestCase):
	"""Base class with the users, the roles and the sales hierarchy of P1."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

		cls.ensure_role("CSKH BOD Viewer")
		cls.ensure_role("CSKH CRM Admin")

		cls.admin_user = "cskh-p1-admin@example.test"
		cls.bod_user = "cskh-p1-bod@example.test"
		cls.leader_a = "cskh-p1-leader-a@example.test"
		cls.staff_a = "cskh-p1-staff-a@example.test"
		cls.staff_b = "cskh-p1-staff-b@example.test"
		cls.staff_c = "cskh-p1-staff-c@example.test"
		cls.leader_b = "cskh-p1-leader-b@example.test"
		cls.staff_outside = "cskh-p1-outside@example.test"

		cls.ensure_user(cls.admin_user, "CSKH CRM Admin")
		cls.ensure_user(cls.bod_user, "CSKH BOD Viewer")
		cls.ensure_user(cls.leader_a, "Sales Manager", "Sales User")
		cls.ensure_user(cls.staff_a, "Sales User")
		cls.ensure_user(cls.staff_b, "Sales User")
		cls.ensure_user(cls.staff_c, "Sales User")
		cls.ensure_user(cls.leader_b, "Sales Manager", "Sales User")
		cls.ensure_user(cls.staff_outside, "Sales User")

		for node in frappe.get_all(
			"CRM Sales Hierarchy",
			filters={"user": ["in", cls.p1_users()]},
			fields=["name"],
			order_by="lft desc",
		):
			frappe.delete_doc("CRM Sales Hierarchy", node.name, force=True, ignore_permissions=True)

		cls.team_a = cls.ensure_hierarchy(cls.leader_a, 1)
		cls.ensure_hierarchy(cls.staff_a, 0, cls.leader_a)
		cls.ensure_hierarchy(cls.staff_b, 0, cls.leader_a)
		cls.team_b = cls.ensure_hierarchy(cls.leader_b, 1)
		cls.ensure_hierarchy(cls.staff_c, 0, cls.leader_b)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", cls.previous_hierarchy or 0)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def p1_users(cls):
		return [
			cls.admin_user,
			cls.bod_user,
			cls.leader_a,
			cls.staff_a,
			cls.staff_b,
			cls.staff_c,
			cls.leader_b,
			cls.staff_outside,
		]

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

	# -- helpers -----------------------------------------------------------

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._phone_counter = int(frappe.generate_hash(length=8), 16) % 100_000_000

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def next_phone(self):
		"""A unique, valid Vietnamese mobile number for the test run.

		The site may already carry seeded leads, so the number is derived from
		a random base to avoid colliding with the existing data.
		"""
		self._phone_counter += 1
		base = int(frappe.generate_hash(length=8), 16) % 90_000_000
		digits = f"{(base + self._phone_counter) % 100_000_000:08d}"
		return f"09{digits}"[:10]

	def unique_key(self, prefix):
		"""An idempotency key that is unique for this test run.

		The worker commits its transaction, so the data of a previous run
		survives on the site. The keys and the phone numbers of the tests must
		therefore be unique per run.
		"""
		return f"{prefix}-{frappe.generate_hash(length=10)}"

	def unique_phone(self, prefix="09"):
		"""A valid Vietnamese mobile number that is unique for this run."""
		digits = f"{int(frappe.generate_hash(length=10), 16) % 100000000:08d}"
		phone = f"{prefix}{digits}"
		while frappe.db.exists("CRM Lead", {"mobile_no": phone}) or frappe.db.exists(
			"CSKH Identity Index", {"normalized_phone": normalize_vn_phone(phone)}
		):
			digits = f"{int(frappe.generate_hash(length=10), 16) % 100000000:08d}"
			phone = f"{prefix}{digits}"
		return phone

	def make_lead(self, phone=None, owner=None, source=None, name=None):
		phone = phone or self.next_phone()
		while frappe.db.exists("CRM Lead", {"mobile_no": phone}):
			phone = self.next_phone()
		doc = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "CSKH P1 Test",
				"mobile_no": phone,
				"status": "New",
				"lead_owner": owner,
			}
		)
		if source:
			doc.source = source
		if name:
			doc.name = name
			doc.flags.name = name
		doc.insert(ignore_permissions=True)
		return doc.name

	def make_canonical_lead(self):
		"""Create a lead and register it in the canonical identity index."""
		from crm_cskh.services import identity as identity_service
		from crm_cskh.services.phone_identity import normalize_vn_phone

		lead = self.make_lead()
		phone = frappe.db.get_value("CRM Lead", lead, "mobile_no")
		identity_service.claim_canonical_identity(normalize_vn_phone(phone), lead)
		return lead

	def make_campaign(self, code=None, title="P1 campaign", status="Active", team_scope=None, selection=None):
		code = code or f"P1-{frappe.generate_hash(length=8)}"
		doc = frappe.get_doc(
			{
				"doctype": "CSKH Campaign",
				"code": code,
				"title": title,
				"status": status,
				"team_scope": team_scope,
			}
		)
		if selection is not None:
			import json

			doc.selection_json = json.dumps(selection)
		doc.insert(ignore_permissions=True)
		return doc.name

	def make_rule(self, campaign, user, weight, capacity=0, version=1, enabled=1, team=None):
		doc = frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": campaign,
				"user": user,
				"weight": weight,
				"capacity_limit": capacity,
				"version": version,
				"enabled": enabled,
				"team": team,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def make_work_item(self, campaign, lead, status="queued", assigned_to=None):
		doc = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"campaign": campaign,
				"lead": lead,
				"status": status,
				"assigned_to": assigned_to,
			}
		)
		if status == "assigned":
			doc.assigned_at = now_datetime()
		doc.insert(ignore_permissions=True)
		return doc.name
