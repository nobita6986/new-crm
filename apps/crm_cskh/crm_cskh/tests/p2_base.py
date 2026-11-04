"""Shared fixtures and helpers for the separate P2 backend test suite (`P2.md` §15.2).

The P2 backend tests live in `crm_cskh.tests.test_p2_*` so that:
  * the P0 core inventory (`99` methods in `g007_test_inventory.json`) is untouched, and
  * the P1 backend inventory (`209` methods in `p1_test_inventory.json`) is untouched.
"""

from __future__ import annotations

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from crm_cskh.patches.v2_0.p2_schema_and_seeds import (
	ensure_p2_custom_fields,
	ensure_p2_indexes,
	ensure_p2_seeds,
)
from crm_cskh.services import identity as identity_service
from crm_cskh.services import ownership
from crm_cskh.services.phone_identity import normalize_vn_phone


class P2TestCase(FrappeTestCase):
	"""Base class providing isolated P2 users, sales hierarchy, campaigns, and helpers."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.previous_hierarchy = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

		ensure_p2_custom_fields()
		ensure_p2_indexes()
		ensure_p2_seeds()

		cls.ensure_role("CSKH BOD Viewer")
		cls.ensure_role("CSKH CRM Admin")

		cls.admin_user = "cskh-p2-admin@example.test"
		cls.bod_user = "cskh-p2-bod@example.test"
		cls.leader_a = "cskh-p2-leader-a@example.test"
		cls.leader_b = "cskh-p2-leader-b@example.test"
		cls.staff_a = "cskh-p2-staff-a@example.test"
		cls.staff_b = "cskh-p2-staff-b@example.test"
		cls.staff_a3 = "cskh-p2-staff-a3@example.test"
		cls.staff_c = "cskh-p2-staff-c@example.test"
		cls.staff_c2 = "cskh-p2-staff-c2@example.test"
		cls.staff_c3 = "cskh-p2-staff-c3@example.test"
		cls.staff_disabled = "cskh-p2-disabled@example.test"
		cls.staff_outside = "cskh-p2-outside@example.test"

		cls.ensure_user(cls.admin_user, "CSKH CRM Admin")
		cls.ensure_user(cls.bod_user, "CSKH BOD Viewer")
		cls.ensure_user(cls.leader_a, "Sales Manager", "Sales User")
		cls.ensure_user(cls.leader_b, "Sales Manager", "Sales User")
		cls.ensure_user(cls.staff_a, "Sales User")
		cls.ensure_user(cls.staff_b, "Sales User")
		cls.ensure_user(cls.staff_a3, "Sales User")
		cls.ensure_user(cls.staff_c, "Sales User")
		cls.ensure_user(cls.staff_c2, "Sales User")
		cls.ensure_user(cls.staff_c3, "Sales User")
		cls.ensure_user(cls.staff_disabled, "Sales User", enabled=0)
		cls.ensure_user(cls.staff_outside, "Sales User")

		for node in frappe.get_all(
			"CRM Sales Hierarchy",
			filters={"user": ["in", cls.p2_users()]},
			fields=["name"],
			order_by="lft desc",
		):
			frappe.delete_doc("CRM Sales Hierarchy", node.name, force=True, ignore_permissions=True)

		cls.team_a = cls.ensure_hierarchy(cls.leader_a, 1)
		cls.ensure_hierarchy(cls.staff_a, 0, cls.leader_a)
		cls.ensure_hierarchy(cls.staff_b, 0, cls.leader_a)
		cls.ensure_hierarchy(cls.staff_a3, 0, cls.leader_a)
		cls.ensure_hierarchy(cls.staff_disabled, 0, cls.leader_a)

		cls.team_b = cls.ensure_hierarchy(cls.leader_b, 1)
		cls.ensure_hierarchy(cls.staff_c, 0, cls.leader_b)
		cls.ensure_hierarchy(cls.staff_c2, 0, cls.leader_b)
		cls.ensure_hierarchy(cls.staff_c3, 0, cls.leader_b)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", cls.previous_hierarchy or 0)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def p2_users(cls) -> list[str]:
		return [
			cls.admin_user,
			cls.bod_user,
			cls.leader_a,
			cls.leader_b,
			cls.staff_a,
			cls.staff_b,
			cls.staff_a3,
			cls.staff_c,
			cls.staff_c2,
			cls.staff_c3,
			cls.staff_disabled,
			cls.staff_outside,
		]

	@classmethod
	def ensure_role(cls, role_name: str) -> None:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)

	@classmethod
	def ensure_user(cls, email: str, *roles: str, enabled: int = 1) -> str:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"enabled": enabled,
					"language": "vi",
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			).insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		missing = [role for role in roles if role not in {row.role for row in user.roles}]
		if missing:
			user.add_roles(*missing)
		if int(user.enabled or 0) != int(enabled):
			frappe.db.set_value("User", email, "enabled", int(enabled), update_modified=False)
		return email

	@classmethod
	def ensure_hierarchy(cls, user: str, is_group: int, reports_to_user: str | None = None) -> str:
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
		super().setUp()
		frappe.set_user("Administrator")
		self._phone_counter = int(frappe.generate_hash(length=8), 16) % 90_000_000

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def unique_key(self, prefix: str = "idem") -> str:
		return f"{prefix}-{frappe.generate_hash(length=10)}"

	def next_phone(self) -> str:
		while True:
			self._phone_counter += 1
			base = int(frappe.generate_hash(length=8), 16) % 80_000_000
			digits = f"{(base + self._phone_counter) % 100_000_000:08d}"
			candidate = f"09{digits}"[:10]
			canonical = normalize_vn_phone(candidate)
			if not frappe.db.exists(
				"CRM Lead", {"mobile_no": ["in", (candidate, canonical)]}
			) and not frappe.db.exists("CSKH Identity Index", {"normalized_phone": canonical}):
				return candidate

	def make_canonical_lead(
		self,
		first_name: str = "Khách P2 Giả Lập",
		source: str | None = None,
		status: str = "New",
	) -> str:
		phone = self.next_phone()
		doc = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first_name,
				"lead_name": first_name,
				"mobile_no": phone,
				"status": status,
			}
		)
		if source and frappe.db.exists("CRM Lead Source", source):
			doc.source = source
		doc.insert(ignore_permissions=True)
		identity_service.claim_canonical_identity(normalize_vn_phone(phone), doc.name)
		return doc.name

	def make_campaign(
		self,
		code: str | None = None,
		title: str = "Chiến dịch CSKH P2",
		status: str = "Active",
		team_scope: str | None = None,
		selection: dict | None = None,
	) -> str:
		code = code or f"P2-{frappe.generate_hash(length=8)}"
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
			doc.selection_json = json.dumps(selection)
		doc.insert(ignore_permissions=True)
		return doc.name

	def make_rule(
		self,
		campaign: str,
		user: str,
		weight: int = 50,
		capacity: int = 0,
		version: int = 1,
		enabled: int = 1,
		team: str | None = None,
	) -> str:
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

	def make_queued_work_item(self, campaign: str, lead: str) -> str:
		wi = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"campaign": campaign,
				"lead": lead,
				"status": "queued",
			}
		).insert(ignore_permissions=True)
		if not frappe.db.exists("CSKH Active Claim", {"lead": lead}):
			frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": lead,
					"campaign": campaign,
					"work_item": wi.name,
				}
			).insert(ignore_permissions=True)
		return wi.name

	def make_assigned_work_item(
		self,
		campaign: str,
		target_user: str,
		lead: str | None = None,
		actor: str | None = None,
	) -> tuple[str, str]:
		"""Create a canonical Lead + queued Work Item + Active Claim and assign via `ownership.assign`."""
		lead_name = lead or self.make_canonical_lead()
		if not frappe.db.exists("CSKH Assignment Rule", {"campaign": campaign, "user": target_user}):
			self.make_rule(campaign, target_user, weight=50)
		wi_name = self.make_queued_work_item(campaign, lead_name)
		ownership.assign(
			wi_name,
			target_user,
			reason="Giao khách trong test P2",
			request_id=self.unique_key("assign"),
			actor=actor or self.admin_user,
		)
		return wi_name, lead_name

	def future_due_str(self, hours: int = 2, minutes: int = 0) -> str:
		return str(add_to_date(now_datetime(), hours=hours, minutes=minutes))

	def make_native_call_log(
		self,
		lead: str,
		call_type: str = "Outgoing",
		status: str = "Completed",
		duration: int = 45,
		receiver: str | None = None,
	) -> str:
		phone = frappe.db.get_value("CRM Lead", lead, "mobile_no") or "0900000999"
		doc = frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": f"P2CL-{frappe.generate_hash(length=10)}",
				"type": call_type,
				"status": status,
				"from": "+842873001101" if call_type == "Outgoing" else phone,
				"to": phone if call_type == "Outgoing" else "+842873001101",
				"duration": duration,
				"start_time": add_to_date(now_datetime(), minutes=-2),
				"end_time": add_to_date(now_datetime(), minutes=-1),
				"caller": frappe.session.user if call_type == "Outgoing" else None,
				"receiver": receiver,
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name
