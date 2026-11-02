"""P1 — atomic ownership service (ADR-P1-05, ADR-P1-07, P1 §7.6).

The invariants under test:

* `assign` / `reassign` / `revoke` are the only mutation path and they are
  atomic: the work item, the audit history, `CRM Lead.lead_owner`, the ToDo
  and the DocShare always end up consistent.
* The `expected_owner` check makes a stale client fail instead of stealing a
  customer that was transferred in the meantime.
* A `request_id` makes a retry idempotent.
* The history is append-only: no event may be edited or deleted.
* The revoked owner loses every access path (DocShare and ToDo).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm_cskh.services import ownership
from crm_cskh.services.errors import Conflict, Forbidden, InvalidRequest
from crm_cskh.tests.p1_base import P1TestCase


class TestOwnershipService(P1TestCase):
	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.lead = self.make_lead()
		self.work_item = self.make_work_item(self.campaign, self.lead)

	# -- helpers -----------------------------------------------------------

	def as_admin(self):
		frappe.set_user(self.admin_user)

	def docshare_users(self, lead=None):
		return sorted(
			row.user
			for row in frappe.get_all(
				"DocShare",
				filters={"share_doctype": "CRM Lead", "share_name": lead or self.lead},
				fields=["user"],
			)
		)

	def open_todos(self, lead=None, user=None):
		filters = {"reference_type": "CRM Lead", "reference_name": lead or self.lead, "status": "Open"}
		if user:
			filters["allocated_to"] = user
		return frappe.get_all("ToDo", filters=filters, pluck="name")

	def history(self, work_item=None):
		return frappe.get_all(
			"CSKH Assignment History",
			filters={"work_item": work_item or self.work_item},
			fields=["name", "action", "from_user", "to_user", "event_id", "ownership_start", "ownership_end"],
			order_by="event_at asc",
		)

	# -- assign ------------------------------------------------------------

	def test_assign_sets_owner_audit_todo_and_share(self):
		self.as_admin()
		result = ownership.assign(
			self.work_item, self.staff_a, actor=self.admin_user, request_id="req-assign-1"
		)
		self.assertEqual(result["owner"], self.staff_a)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "lead_owner"), self.staff_a)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "assigned_to"), self.staff_a)
		self.assertEqual(self.docshare_users(), [self.staff_a])
		self.assertEqual(len(self.open_todos()), 1)

		events = self.history()
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0].action, "assign")
		self.assertEqual(events[0].to_user, self.staff_a)
		self.assertEqual(events[0].event_id, f"assign:{self.work_item}:req-assign-1")
		self.assertIsNone(events[0].ownership_end)

	def test_assign_is_idempotent_for_the_same_request_id(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-idem")
		second = ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-idem")
		self.assertEqual(second["status"], "already_applied")
		self.assertEqual(len(self.history()), 1)

	def test_assign_requires_a_request_id(self):
		self.as_admin()
		with self.assertRaises(InvalidRequest):
			ownership.assign(self.work_item, self.staff_a, actor=self.admin_user)

	def test_assign_rejects_a_non_admin_actor(self):
		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			ownership.assign(self.work_item, self.staff_a, actor=self.staff_a, request_id="req-forbidden")

	def test_assign_rejects_a_disabled_target(self):
		self.as_admin()
		frappe.db.set_value("User", self.staff_b, "enabled", 0)
		self.addCleanup(frappe.db.set_value, "User", self.staff_b, "enabled", 1)
		with self.assertRaises(InvalidRequest):
			ownership.assign(self.work_item, self.staff_b, actor=self.admin_user, request_id="req-disabled")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "status"), "queued")

	def test_assign_rejects_a_target_that_is_not_a_sales_user(self):
		self.as_admin()
		with self.assertRaises(InvalidRequest):
			ownership.assign(self.work_item, self.bod_user, actor=self.admin_user, request_id="req-bod")

	def test_assign_of_an_already_assigned_item_conflicts(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-a")
		with self.assertRaises(Conflict) as ctx:
			ownership.assign(self.work_item, self.staff_b, actor=self.admin_user, request_id="req-b")
		self.assertEqual(ctx.exception.code, "ALREADY_ASSIGNED")

	# -- reassign ----------------------------------------------------------

	def test_reassign_moves_owner_and_revokes_the_previous_access(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		ownership.reassign(
			self.work_item,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="Customer transferred",
			request_id="req-2",
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "lead_owner"), self.staff_b)
		self.assertEqual(self.docshare_users(), [self.staff_b])
		self.assertEqual(self.open_todos(user=self.staff_a), [])
		self.assertEqual(len(self.open_todos(user=self.staff_b)), 1)

		events = self.history()
		self.assertEqual([event.action for event in events], ["assign", "reassign"])
		self.assertEqual(events[1].from_user, self.staff_a)
		self.assertEqual(events[1].to_user, self.staff_b)
		self.assertIsNotNone(events[0].ownership_end)
		self.assertIsNone(events[1].ownership_end)

	def test_reassign_with_a_stale_expected_owner_is_rejected(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		ownership.reassign(
			self.work_item,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="transfer",
			request_id="req-2",
		)
		with self.assertRaises(Conflict) as ctx:
			ownership.reassign(
				self.work_item,
				expected_owner=self.staff_a,
				target_user=self.staff_c,
				actor=self.admin_user,
				reason="stale",
				request_id="req-3",
			)
		self.assertEqual(ctx.exception.code, "CONFLICT")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "assigned_to"), self.staff_b)

	def test_reassign_requires_a_reason(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		with self.assertRaises(InvalidRequest):
			ownership.reassign(
				self.work_item,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				actor=self.admin_user,
				request_id="req-2",
			)

	def test_reassign_respects_the_capacity_limit(self):
		self.as_admin()
		self.make_rule(self.campaign, self.staff_b, weight=1, capacity=1)
		self.make_work_item(self.campaign, self.make_lead(), status="assigned", assigned_to=self.staff_b)
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		with self.assertRaises(Conflict) as ctx:
			ownership.reassign(
				self.work_item,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				actor=self.admin_user,
				reason="over capacity",
				request_id="req-2",
			)
		self.assertEqual(ctx.exception.code, "CAPACITY_EXCEEDED")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "assigned_to"), self.staff_a)

	# -- revoke ------------------------------------------------------------

	def test_revoke_returns_the_lead_to_the_pool_and_clears_every_access(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		ownership.revoke(
			self.work_item,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="Customer asked to be released",
			request_id="req-2",
		)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "status"), "queued")
		self.assertIsNone(frappe.db.get_value("CSKH Work Item", self.work_item, "assigned_to"))
		self.assertIsNone(frappe.db.get_value("CRM Lead", self.lead, "lead_owner"))
		self.assertEqual(self.docshare_users(), [])
		self.assertEqual(self.open_todos(), [])

		events = self.history()
		self.assertEqual(events[-1].action, "revoke")
		self.assertEqual(events[-1].from_user, self.staff_a)
		self.assertIsNone(events[-1].to_user)

	def test_revoke_releases_the_active_claim(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"work_item": self.work_item,
				"lead": self.lead,
				"campaign": self.campaign,
			}
		).insert(ignore_permissions=True)
		ownership.revoke(
			self.work_item,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="release",
			request_id="req-2",
		)
		self.assertFalse(frappe.db.exists("CSKH Active Claim", {"lead": self.lead}))

	def test_revoke_with_a_stale_expected_owner_is_rejected(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		with self.assertRaises(Conflict):
			ownership.revoke(
				self.work_item,
				expected_owner=self.staff_b,
				actor=self.admin_user,
				reason="stale",
				request_id="req-2",
			)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "assigned_to"), self.staff_a)

	# -- append-only audit -------------------------------------------------

	def test_history_cannot_be_updated(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		event = self.history()[0]
		with self.assertRaises(frappe.ValidationError):
			frappe.db.set_value("CSKH Assignment History", event.name, "reason", "tampered")
			frappe.get_doc("CSKH Assignment History", event.name).save(ignore_permissions=True)

	def test_history_cannot_be_deleted(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		event = self.history()[0]
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("CSKH Assignment History", event.name, force=True, ignore_permissions=True)

	def test_history_records_the_actor(self):
		self.as_admin()
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="req-1")
		event = frappe.db.get_value(
			"CSKH Assignment History", {"event_id": f"assign:{self.work_item}:req-1"}, "actor"
		)
		self.assertEqual(event, self.admin_user)
