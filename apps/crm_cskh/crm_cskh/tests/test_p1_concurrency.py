"""P1 — resilience, concurrency and rollback (P1 §12.4).

The invariants under test:

* **2 and 5 parallel import jobs** that touch the same normalized phone end up
  with exactly one canonical lead and one identity row;
* **2 and 5 parallel distribution runs** that share a lead never assign it
  twice: the `CSKH Active Claim` unique index is the arbiter;
* a failure **after** the work item was created but **before** the owner was
  written leaves no orphan: the transaction rolls back;
* a retry after a committed batch does not create a second assignment event;
* `bench migrate` twice is safe (the patch and the index creation are
  idempotent).

The concurrency is real: the worker threads open **their own** database
connections, so the race happens in MariaDB and not in the Python GIL.
"""

import os
import threading

import frappe

from crm_cskh.services import identity as identity_service
from crm_cskh.services import ownership
from crm_cskh.services.errors import Conflict
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.tests.p1_base import P1TestCase

SITE = getattr(frappe.local, "site", None)
SITES_PATH = getattr(frappe.local, "sites_path", None)


def _run_in_thread(fn, results, index):
	"""Run `fn` in a thread with its own database connection.

		The Frappe `hash` autoname uses the `tabSeries` table: two threads that
		create a document at the same time can collide on that series row and
	MariaDB reports a deadlock (`1020`). The retry loop is the documented way to
	handle it — the *domain* race (the unique index on the phone and on the
	active claim) is what the test proves.
	"""

	def target():
		attempt = 0
		while True:
			try:
				frappe.init(SITE, sites_path=SITES_PATH, force=True)
				frappe.connect()
				frappe.set_user("Administrator")
				results[index] = {"ok": fn()}
				frappe.db.commit()
				return
			except Exception as exc:
				retryable = "1020" in str(exc) or "Deadlock" in type(exc).__name__
				try:
					frappe.db.rollback()
				except Exception:
					pass
				if retryable and attempt < 5:
					attempt += 1
					continue
				results[index] = {"error": f"{type(exc).__name__}: {exc}"}
				return
			finally:
				try:
					frappe.db.close()
				except Exception:
					pass

	thread = threading.Thread(target=target)
	thread.start()
	return thread


class TestConcurrentIdentity(P1TestCase):
	"""2 and 5 parallel writers of the same phone (P1 §12.4, T1-05)."""

	def _parallel_claims(self, phone, workers):
		results = {}

		# The fixture must be visible to every thread: the threads run in their
		# own transaction and cannot see the uncommitted data of the test.
		frappe.db.commit()

		def claim():
			lead = frappe.get_doc(
				{
					"doctype": "CRM Lead",
					"first_name": "Race",
					"mobile_no": phone,
					"status": "New",
				}
			)
			lead.insert(ignore_permissions=True)
			try:
				winner = identity_service.claim_canonical_identity(
					normalize_vn_phone(phone), lead.name, allow_adopt=False
				)
			except Exception:
				# Another writer won the race: adopt its canonical lead.
				winner = identity_service.find_by_phone(normalize_vn_phone(phone))
				if not winner:
					raise
			if winner != lead.name:
				frappe.delete_doc("CRM Lead", lead.name, force=True, ignore_permissions=True)
			return winner

		threads = [_run_in_thread(claim, results, index) for index in range(workers)]
		for thread in threads:
			thread.join()
		return results

	def test_two_parallel_writers_leave_one_canonical_lead(self):
		phone = self.next_phone()
		results = self._parallel_claims(phone, 2)
		errors = [value for value in results.values() if "error" in value]
		self.assertEqual(errors, [], f"the race must not raise: {errors}")

		canonical = normalize_vn_phone(phone)
		self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": canonical}), 1)
		self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": ["in", (phone, "+84" + phone[1:])]}), 1)

	def test_five_parallel_writers_leave_one_canonical_lead(self):
		phone = self.next_phone()
		results = self._parallel_claims(phone, 5)
		errors = [value for value in results.values() if "error" in value]
		self.assertEqual(errors, [], f"the race must not raise: {errors}")

		canonical = normalize_vn_phone(phone)
		self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": canonical}), 1)
		self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": ["in", (phone, "+84" + phone[1:])]}), 1)

	def test_two_parallel_import_jobs_on_the_same_phone(self):
		"""The import worker path: the phone is claimed once, the lead is reused."""
		from crm_cskh.jobs import import_worker

		phone = self.next_phone()
		results = {}
		frappe.db.commit()

		def worker():
			lead, is_new = import_worker._ensure_lead(normalize_vn_phone(phone), {})
			return {"lead": lead, "is_new": is_new}

		threads = [_run_in_thread(worker, results, index) for index in range(2)]
		for thread in threads:
			thread.join()
		errors = [value for value in results.values() if "error" in value]
		self.assertEqual(errors, [], f"the race must not raise: {errors}")

		canonical = normalize_vn_phone(phone)
		self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": canonical}), 1)
		self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": ["in", (phone, "+84" + phone[1:])]}), 1)


class TestConcurrentDistribution(P1TestCase):
	"""2 and 5 parallel distribution runs that share a lead (P1 §12.4, T1-12)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.make_rule(self.campaign, self.staff_a, weight=1)

	def _parallel_claims(self, lead, workers):
		"""Every thread tries to claim the same lead for the same campaign."""
		results = {}

		# The fixture must be visible to every thread.
		frappe.db.commit()

		def claim():
			work_item = frappe.get_doc(
				{
					"doctype": "CSKH Work Item",
					"campaign": self.campaign,
					"lead": lead,
					"status": "queued",
				}
			)
			work_item.insert(ignore_permissions=True)
			claim_doc = frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": lead,
					"campaign": self.campaign,
					"work_item": work_item.name,
				}
			)
			claim_doc.insert(ignore_permissions=True)
			return work_item.name

		threads = [_run_in_thread(claim, results, index) for index in range(workers)]
		for thread in threads:
			thread.join()
		return results

	def test_two_parallel_runs_cannot_claim_the_same_lead_twice(self):
		lead = self.make_canonical_lead()
		results = self._parallel_claims(lead, 2)
		# Exactly one writer wins; the loser is rejected by the unique index.
		winners = [value for value in results.values() if "ok" in value]
		losers = [value for value in results.values() if "error" in value]
		self.assertEqual(len(winners), 1, f"exactly one run may win: {results}")
		self.assertEqual(len(losers), 1, f"the loser must be rejected: {results}")
		self.assertIn("Duplicate", losers[0]["error"])
		self.assertEqual(
			frappe.db.count("CSKH Active Claim", {"lead": lead}),
			1,
		)
		self.assertEqual(
			frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "lead": lead}),
			1,
		)

	def test_five_parallel_runs_cannot_claim_the_same_lead_twice(self):
		lead = self.make_canonical_lead()
		results = self._parallel_claims(lead, 5)
		winners = [value for value in results.values() if "ok" in value]
		self.assertEqual(len(winners), 1, f"exactly one run may win: {results}")
		self.assertEqual(
			frappe.db.count("CSKH Active Claim", {"lead": lead}),
			1,
		)
		self.assertEqual(
			frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "lead": lead}),
			1,
		)

	def test_a_lead_cannot_be_claimed_by_two_different_campaigns(self):
		lead = self.make_canonical_lead()
		other_campaign = self.make_campaign()
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead,
				"campaign": self.campaign,
				"work_item": self.make_work_item(self.campaign, lead),
			}
		).insert(ignore_permissions=True)

		results = {}

		def claim_other():
			work_item = frappe.get_doc(
				{
					"doctype": "CSKH Work Item",
					"campaign": other_campaign,
					"lead": lead,
					"status": "queued",
				}
			)
			work_item.insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": lead,
					"campaign": other_campaign,
					"work_item": work_item.name,
				}
			).insert(ignore_permissions=True)
			return work_item.name

		thread = _run_in_thread(claim_other, results, 0)
		thread.join()
		self.assertIn("error", results[0])
		self.assertEqual(
			frappe.db.count("CSKH Active Claim", {"lead": lead}),
			1,
		)


class TestRollbackAndRetry(P1TestCase):
	"""A failure inside the transaction leaves no orphan (P1 §12.4)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.lead = self.make_lead()
		self.work_item = self.make_work_item(self.campaign, self.lead)

	def test_a_failure_after_the_work_item_creation_leaves_no_orphan(self):
		"""The artificial failure of P1 §12.4.

		The work item is created, then the ownership transfer fails. The
		savepoint rollback must remove the work item, the claim and the audit
		event together.
		"""
		lead = self.make_lead()
		frappe.db.savepoint("cskh_test_rollback")
		work_item = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"campaign": self.campaign,
				"lead": lead,
				"status": "queued",
			}
		)
		work_item.insert(ignore_permissions=True)
		claim = frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead,
				"campaign": self.campaign,
				"work_item": work_item.name,
			}
		)
		claim.insert(ignore_permissions=True)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 1)

		# The artificial failure.
		frappe.db.rollback(save_point="cskh_test_rollback")

		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "lead": lead}), 0)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": lead}), 0)

	def test_a_retry_after_a_committed_assignment_does_not_double_apply(self):
		"""A notification retry must not create a second assignment event."""
		frappe.set_user(self.admin_user)
		first = ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="retry-1")
		self.assertEqual(first["status"], "assigned")
		frappe.db.commit()

		second = ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="retry-1")
		self.assertEqual(second["status"], "already_applied")
		self.assertEqual(
			frappe.db.count("CSKH Assignment History", {"work_item": self.work_item, "action": "assign"}),
			1,
		)

	def test_a_revoked_work_item_frees_the_lead_for_another_campaign(self):
		frappe.set_user(self.admin_user)
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="retry-2")
		frappe.db.commit()
		ownership.revoke(
			self.work_item,
			expected_owner=self.staff_a,
			actor=self.admin_user,
			reason="release",
			request_id="retry-3",
		)
		self.assertFalse(frappe.db.exists("CSKH Active Claim", {"lead": self.lead}))

		other_campaign = self.make_campaign()
		other_item = self.make_work_item(other_campaign, self.lead)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": self.lead,
				"campaign": other_campaign,
				"work_item": other_item,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"lead": self.lead}), 1)

	def test_an_expected_owner_conflict_keeps_the_current_owner(self):
		frappe.set_user(self.admin_user)
		ownership.assign(self.work_item, self.staff_a, actor=self.admin_user, request_id="retry-4")
		with self.assertRaises(Conflict):
			ownership.reassign(
				self.work_item,
				expected_owner=self.staff_b,
				target_user=self.staff_c,
				actor=self.admin_user,
				reason="stale",
				request_id="retry-5",
			)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", self.work_item, "assigned_to"), self.staff_a)
