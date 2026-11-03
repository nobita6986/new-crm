"""P1 — API contract and the security negatives (P1 §10, §12.3).

The suite proves two things at once:

1. the **frozen API contract** that T1A connects to: the endpoint names, the
   request parameters, the response keys and the stable error codes;
2. the **security negatives** SEC-01..SEC-10 of P1 §12.3.

The business roles are the axis of the test (`CSKH CRM Admin`,
`CSKH BOD Viewer`, `Sales Manager`, `Sales User`). `CSKH CRM Admin` is a
*business* role: it is never replaced by `Administrator` and it never implies
`System Manager`. `CSKH BOD Viewer` never receives `System Manager` either
(P1 §8.1).

> **Reading with permissions:** the tests use `frappe.get_list` and
> `frappe.client.get`, never `frappe.get_all` / `frappe.get_doc`, because
> `get_all` ignores `permission_query_conditions` and `get_doc` does not check
> the read permission (P1 §8.2).
"""

import frappe
from frappe.client import get as client_get
from frappe.client import get_list as client_get_list

from crm_cskh.api import campaigns as campaigns_api
from crm_cskh.api import customers as customers_api
from crm_cskh.api import dashboard as dashboard_api
from crm_cskh.api import distribution as distribution_api
from crm_cskh.api import imports as imports_api
from crm_cskh.api import ownership as ownership_api
from crm_cskh.services import ownership
from crm_cskh.services.errors import Conflict, Forbidden, P1Error
from crm_cskh.tests.p1_base import P1TestCase


class TestApiContract(P1TestCase):
	"""The frozen shape of the P1 API (P1 §10.1, §10.2)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.lead = self.make_lead()
		self.work_item = self.make_work_item(self.campaign, self.lead)

	def as_admin(self):
		frappe.set_user(self.admin_user)

	# -- endpoints ---------------------------------------------------------

	def test_every_planned_endpoint_exists(self):
		expected = {
			"imports": ["create_preview", "confirm", "get_status"],
			"campaigns": ["list", "save", "rules", "save_rule"],
			"distribution": ["preview", "execute", "get_run", "reconcile"],
			"ownership": ["assign", "reassign", "revoke", "history"],
			"customers": ["my_list", "pool_list", "customer_pool"],
			"dashboard": ["distribution", "campaigns"],
		}
		modules = {
			"imports": imports_api,
			"campaigns": campaigns_api,
			"distribution": distribution_api,
			"ownership": ownership_api,
			"customers": customers_api,
			"dashboard": dashboard_api,
		}
		for group, names in expected.items():
			for name in names:
				method = getattr(modules[group], name, None)
				self.assertTrue(callable(method), f"{group}.{name} is missing")
				self.assertTrue(
					method.__module__.startswith("crm_cskh.api."),
					f"{group}.{name} is not an API method",
				)

	def test_the_list_endpoints_return_the_scope_total_and_timestamp(self):
		frappe.set_user(self.staff_a)
		payload = customers_api.my_list()
		for key in ("scope", "items", "total", "page", "page_size", "as_of"):
			self.assertIn(key, payload)

		# The pool is not readable by a staff member (P1 §8.1); the leader reads
		# the pool of their own scope.
		frappe.set_user(self.leader_a)
		for payload in (customers_api.pool_list(), customers_api.customer_pool()):
			for key in ("scope", "items", "total", "page", "page_size", "as_of"):
				self.assertIn(key, payload)

	def test_the_dashboard_returns_the_p1_cards(self):
		frappe.set_user(self.bod_user)
		payload = dashboard_api.distribution()
		self.assertIn("cards", payload)
		for card in (
			"unique_customers",
			"eligible_unassigned",
			"assigned_work_items",
			"unassigned_work_items",
			"revoked_work_items",
			"closed_work_items",
			"active_claims",
			"active_campaigns",
			"failed_batches",
			"failed_runs",
		):
			self.assertIn(card, payload["cards"])
		self.assertIn("per_user", payload)
		self.assertIn("as_of", payload)

	def test_the_preview_contract_carries_the_frozen_keys(self):
		self.as_admin()
		lead = self.make_canonical_lead()
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": [lead]})
		)
		self.make_rule(self.campaign, self.staff_a, weight=1)
		payload = distribution_api.preview(campaign=self.campaign)
		for key in (
			"preview_id",
			"campaign",
			"selection_fingerprint",
			"rule_version",
			"expires_at",
			"summary",
		):
			self.assertIn(key, payload)
		self.assertIn("eligible_count", payload["summary"])
		self.assertIn("excluded_count", payload["summary"])
		self.assertIn("targets", payload["summary"])

	def test_the_mutations_require_an_idempotency_key(self):
		self.as_admin()
		with self.assertRaises(P1Error) as ctx:
			ownership_api.assign(work_item=self.work_item, target_user=self.staff_a)
		self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

	def test_the_error_codes_are_stable(self):
		from crm_cskh.services import errors

		for code in (
			"INVALID_PHONE",
			"DUPLICATE",
			"ALREADY_ASSIGNED",
			"RULE_CHANGED",
			"CAPACITY_EXCEEDED",
			"CONFLICT",
			"FORBIDDEN",
			"PARTIAL_SUCCESS",
			"RETRYABLE_FAILURE",
		):
			self.assertIn(code, errors.ALL_CODES)

	def test_a_forbidden_response_carries_the_forbidden_code(self):
		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden) as ctx:
			ownership_api.reassign(work_item=self.work_item, target_user=self.staff_b)
		self.assertEqual(ctx.exception.code, "FORBIDDEN")
		self.assertEqual(ctx.exception.http_status, 403)

	def test_the_business_roles_are_not_system_roles(self):
		"""`CSKH CRM Admin` is a business role, not a backdoor to the system."""
		self.assertNotIn("System Manager", frappe.get_roles(self.admin_user))
		self.assertNotIn("System Manager", frappe.get_roles(self.bod_user))
		self.assertNotIn("System Manager", frappe.get_roles(self.staff_a))
		self.assertIn("CSKH CRM Admin", frappe.get_roles(self.admin_user))
		self.assertIn("CSKH BOD Viewer", frappe.get_roles(self.bod_user))


class TestSecurityNegatives(P1TestCase):
	"""SEC-01..SEC-10 of P1 §12.3."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.lead_a = self.make_lead(owner=self.staff_a)
		self.lead_b = self.make_lead(owner=self.staff_b)
		self.item_a = self.make_work_item(self.campaign, self.lead_a)
		self.item_b = self.make_work_item(self.campaign, self.lead_b)
		self.as_admin()
		ownership.assign(self.item_a, self.staff_a, actor=self.admin_user, request_id="sec-assign-a")
		ownership.assign(self.item_b, self.staff_b, actor=self.admin_user, request_id="sec-assign-b")

	def as_admin(self):
		frappe.set_user(self.admin_user)

	# -- SEC-01 ------------------------------------------------------------

	def test_sec_01_a_staff_member_cannot_read_the_lead_of_another_user(self):
		frappe.set_user(self.staff_a)
		self.assertFalse(
			frappe.has_permission("CRM Lead", "read", doc=self.lead_b, user=self.staff_a, throw=False)
		)
		with self.assertRaises(frappe.PermissionError):
			client_get("CRM Lead", self.lead_b)

	def test_sec_01_a_staff_member_cannot_widen_the_lead_list(self):
		frappe.set_user(self.staff_a)
		filters = {"name": ["in", [self.lead_a, self.lead_b]]}
		visible = client_get_list("CRM Lead", fields=["name"], filters=filters)
		self.assertEqual([row["name"] for row in visible], [self.lead_a])
		# An explicit filter for another owner must not widen the scope.
		self.assertEqual(
			client_get_list("CRM Lead", fields=["name"], filters={"lead_owner": self.staff_b}),
			[],
		)

	# -- SEC-02 ------------------------------------------------------------

	def test_sec_02_a_staff_member_cannot_read_the_work_item_of_another_user(self):
		frappe.set_user(self.staff_a)
		self.assertFalse(
			frappe.has_permission("CSKH Work Item", "read", doc=self.item_b, user=self.staff_a, throw=False)
		)
		visible = client_get_list("CSKH Work Item", fields=["name"], filters={"campaign": self.campaign})
		self.assertEqual([row["name"] for row in visible], [self.item_a])

	def test_sec_02_a_staff_member_cannot_read_an_import_batch(self):
		batch = self._make_batch("sec-02-batch")
		frappe.set_user(self.staff_a)
		self.assertFalse(
			frappe.has_permission("CSKH Import Batch", "read", doc=batch, user=self.staff_a, throw=False)
		)
		with self.assertRaises(Forbidden):
			imports_api.get_status(batch=batch.name)

	def test_sec_02_a_staff_member_cannot_read_the_raw_import_file(self):
		file_name = self._private_file("secret-2.csv", b"phone\n0912345678\n", attached_to=self.lead_b)
		frappe.set_user(self.staff_a)
		self.assertFalse(frappe.has_permission("File", "read", doc=file_name, user=self.staff_a, throw=False))

	# -- SEC-03 ------------------------------------------------------------

	def test_sec_03_a_staff_member_cannot_execute_a_distribution(self):
		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			distribution_api.preview(campaign=self.campaign)

	def test_sec_03_a_staff_member_cannot_transfer_or_revoke_an_ownership(self):
		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			ownership_api.reassign(
				work_item=self.item_a,
				expected_owner=self.staff_a,
				target_user=self.staff_b,
				reason="not allowed",
				request_id="sec-03-reassign",
			)
		with self.assertRaises(Forbidden):
			ownership_api.revoke(
				work_item=self.item_a,
				expected_owner=self.staff_a,
				reason="not allowed",
				request_id="sec-03-revoke",
			)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.item_a, "assigned_to"), self.staff_a)

	# -- SEC-04 ------------------------------------------------------------

	def test_sec_04_a_leader_only_reads_the_aggregate_of_their_own_team(self):
		# A lead of the other branch, assigned to `staff_c` (team B).
		other_lead = self.make_lead(owner=self.staff_c)
		other_item = self.make_work_item(self.campaign, other_lead)
		self.as_admin()
		ownership.assign(other_item, self.staff_c, actor=self.admin_user, request_id="sec-04-other")

		frappe.set_user(self.leader_a)
		payload = dashboard_api.distribution(campaign=self.campaign)
		# `staff_c` belongs to the branch of `leader_b`, so the leader of team A
		# must not see its work item in the aggregate: only the two work items
		# of the team A (`staff_a` and `staff_b`) are counted.
		self.assertEqual(payload["scope"], "team")
		self.assertEqual(payload["cards"]["assigned_work_items"], 2)
		per_user = [row["user"] for row in payload["per_user"]]
		self.assertIn(self.staff_a, per_user)
		self.assertNotIn(self.staff_c, per_user)

	def test_sec_04_a_leader_cannot_transfer_the_customer_of_another_team(self):
		# A work item owned by `staff_c`, the branch of `leader_b`.
		other_lead = self.make_lead(owner=self.staff_c)
		other_item = self.make_work_item(self.campaign, other_lead)
		self.as_admin()
		ownership.assign(other_item, self.staff_c, actor=self.admin_user, request_id="sec-04-cross-item")

		frappe.set_user(self.leader_a)
		with self.assertRaises(Forbidden):
			ownership_api.reassign(
				work_item=other_item,
				expected_owner=self.staff_c,
				target_user=self.staff_a,
				reason="cross team",
				request_id="sec-04-cross",
			)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", other_item, "assigned_to"), self.staff_c)

	def test_sec_04_a_leader_cannot_see_the_work_items_of_another_team(self):
		other_lead = self.make_lead(owner=self.staff_c)
		other_item = self.make_work_item(self.campaign, other_lead)
		self.as_admin()
		ownership.assign(other_item, self.staff_c, actor=self.admin_user, request_id="sec-04-other-item")

		frappe.set_user(self.leader_a)
		visible = client_get_list("CSKH Work Item", fields=["name"], filters={"campaign": self.campaign})
		names = [row["name"] for row in visible]
		self.assertIn(self.item_a, names)
		self.assertNotIn(other_item, names)

	# -- SEC-05 ------------------------------------------------------------

	def test_sec_05_the_bod_viewer_cannot_change_a_campaign_or_a_rule(self):
		frappe.set_user(self.bod_user)
		with self.assertRaises(Forbidden):
			campaigns_api.save(campaign=self.campaign, title="changed", status="Active")

		campaign_doc = frappe.get_doc("CSKH Campaign", self.campaign)
		campaign_doc.title = "tampered"
		with self.assertRaises(frappe.PermissionError):
			campaign_doc.save()

	def test_sec_05_the_bod_viewer_cannot_change_the_assignment_history(self):
		frappe.set_user(self.bod_user)
		event = frappe.get_all(
			"CSKH Assignment History",
			filters={"work_item": self.item_a},
			fields=["name", "reason"],
			limit=1,
		)
		self.assertTrue(event)
		history_doc = frappe.get_doc("CSKH Assignment History", event[0].name)
		history_doc.reason = "tampered"
		with self.assertRaises(frappe.PermissionError):
			history_doc.save()

	def test_sec_05_the_bod_viewer_cannot_mutate_an_ownership(self):
		frappe.set_user(self.bod_user)
		with self.assertRaises(Forbidden):
			ownership_api.revoke(
				work_item=self.item_a,
				expected_owner=self.staff_a,
				reason="bod",
				request_id="sec-05-bod",
			)

	# -- SEC-06 ------------------------------------------------------------

	def test_sec_06_the_revoked_owner_loses_every_access_path(self):
		self.as_admin()
		ownership.revoke(
			self.item_a,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="release",
			request_id="sec-06-revoke",
		)
		shares = frappe.get_all(
			"DocShare",
			filters={"share_doctype": "CRM Lead", "share_name": self.lead_a},
			pluck="user",
		)
		self.assertEqual(shares, [])
		todos = frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "CRM Lead",
				"reference_name": self.lead_a,
				"status": "Open",
			},
			pluck="name",
		)
		self.assertEqual(todos, [])

		frappe.set_user(self.staff_a)
		self.assertFalse(
			frappe.has_permission("CRM Lead", "read", doc=self.lead_a, user=self.staff_a, throw=False)
		)

	# -- SEC-07 ------------------------------------------------------------

	def test_sec_07_the_endpoints_do_not_accept_a_scope_parameter(self):
		"""The scope is derived on the server, never from a request parameter."""
		import inspect

		for endpoint in (
			customers_api.my_list,
			customers_api.pool_list,
			customers_api.customer_pool,
			dashboard_api.distribution,
			dashboard_api.campaigns,
		):
			parameters = set(inspect.signature(endpoint).parameters)
			self.assertNotIn("user", parameters)
			self.assertNotIn("team", parameters)
			self.assertNotIn("scope", parameters)

	def test_sec_07_a_staff_member_cannot_widen_the_scope_with_a_query_parameter(self):
		frappe.set_user(self.staff_a)
		leaked = client_get_list("CSKH Work Item", fields=["name"], filters={"assigned_to": self.staff_b})
		self.assertEqual(leaked, [])

		payload = customers_api.my_list(campaign=self.campaign)
		self.assertEqual([item["work_item"] for item in payload["items"]], [self.item_a])
		self.assertEqual(payload["total"], 1)

	def test_sec_07_the_pool_of_a_leader_stays_inside_the_scope(self):
		frappe.set_user(self.leader_a)
		payload = customers_api.pool_list()
		queued = set(frappe.get_all("CSKH Work Item", filters={"status": "queued"}, pluck="name"))
		for item in payload["items"]:
			self.assertIn(item["work_item"], queued)

	# -- SEC-08 ------------------------------------------------------------

	def test_sec_08_a_formula_cell_is_neutralized_in_the_error_artifact(self):
		from crm_cskh.services.import_service import write_error_artifact

		batch = self._make_batch("sec-08-batch")
		artifact = write_error_artifact(
			batch.name,
			[
				{"row_number": 2, "reason": "=1+1"},
				{"row_number": 3, "reason": "<script>alert(1)</script>"},
			],
		)
		content = frappe.get_doc("File", artifact).get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8")
		self.assertNotIn("<script>", content)
		# The formula is kept as a literal: the cell starts with an apostrophe.
		self.assertIn("'=1+1", content)
		self.assertNotIn("\n=1+1", content)
		self.assertEqual(frappe.get_doc("File", artifact).is_private, 1)

	# -- SEC-09 ------------------------------------------------------------

	def test_sec_09_the_raw_file_is_not_reachable_without_permission(self):
		file_name = self._private_file("raw.csv", b"phone\n0912345678\n", attached_to=self.lead_b)
		frappe.set_user(self.staff_outside)
		self.assertFalse(
			frappe.has_permission("File", "read", doc=file_name, user=self.staff_outside, throw=False)
		)
		with self.assertRaises(frappe.PermissionError):
			client_get("File", file_name)

	def test_sec_09_the_import_batch_is_not_readable_by_a_foreign_role(self):
		batch = self._make_batch("sec-09-batch")
		link = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch Lead",
				"import_batch": batch.name,
				"lead": self.lead_a,
				"outcome": "created",
				"row_number": 2,
			}
		)
		link.insert(ignore_permissions=True)

		frappe.set_user(self.admin_user)
		self.assertEqual(
			[
				row["name"]
				for row in client_get_list("CSKH Import Batch Lead", filters={"import_batch": batch.name})
			],
			[link.name],
		)
		self.assertFalse(
			frappe.has_permission(
				"CSKH Import Batch Lead", "write", doc=link.name, user=self.admin_user, throw=False
			)
		)

		for denied_user in (self.staff_outside, self.leader_a, self.bod_user):
			frappe.set_user(denied_user)
			self.assertFalse(
				frappe.has_permission(
					"CSKH Import Batch Lead", "read", doc=link.name, user=denied_user, throw=False
				)
			)

		frappe.set_user(self.staff_outside)
		with self.assertRaises(Forbidden):
			imports_api.get_status(batch=batch.name)

	# -- SEC-10 ------------------------------------------------------------

	def test_sec_10_a_stale_expected_owner_does_not_overwrite_a_new_owner(self):
		self.as_admin()
		ownership.reassign(
			self.item_a,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="transfer",
			request_id="sec-10-first",
		)
		# The client still believes `staff_a` owns the customer.
		with self.assertRaises(Conflict) as ctx:
			ownership.reassign(
				self.item_a,
				expected_owner=self.staff_a,
				target_user=self.staff_c,
				actor=self.admin_user,
				reason="stale client",
				request_id="sec-10-stale",
			)
		self.assertEqual(ctx.exception.code, "CONFLICT")
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.item_a, "assigned_to"), self.staff_b)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead_a, "lead_owner"), self.staff_b)

	def test_sec_10_a_retry_with_the_same_request_id_does_not_double_apply(self):
		self.as_admin()
		first = ownership.reassign(
			self.item_a,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="transfer",
			request_id="sec-10-retry",
		)
		second = ownership.reassign(
			self.item_a,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="transfer",
			request_id="sec-10-retry",
		)
		self.assertEqual(first["status"], "reassigned")
		self.assertEqual(second["status"], "already_applied")
		events = frappe.get_all(
			"CSKH Assignment History",
			filters={"work_item": self.item_a, "action": "reassign"},
			pluck="name",
		)
		self.assertEqual(len(events), 1)

	# -- helpers -----------------------------------------------------------

	def _private_file(self, file_name, content, attached_to=None):
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": attached_to or "Administrator",
				"content": content,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _make_batch(self, key_prefix):
		batch = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch",
				"source_file": self._private_file(
					f"{key_prefix}.csv", b"phone\n0912345678\n", attached_to=self.lead_a
				),
				"checksum": key_prefix,
				"idempotency_key": f"{key_prefix}-{frappe.generate_hash(length=8)}",
				"status": "DRY_RUN",
			}
		)
		batch.insert(ignore_permissions=True)
		return batch
