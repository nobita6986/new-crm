"""P1 - weighted fair distribution (ADR-P1-04, P1 §7.2-§7.5).

The invariants under test:

* 1.000 leads with the weights 50/30/20 are split 500/300/200.
* 100 batches of a single lead keep the cumulative ratio 50/30/20, which
  proves that the fairness counters are persisted and not recomputed per
  batch.
* The preview is a dry run: it never moves the counters and never creates a
  work item.
* A stale preview (changed rules or changed fairness state) is refused.
* The capacity limit cuts the distribution off instead of over-assigning.
* The `CSKH Active Claim` unique index keeps a lead out of two campaigns.
* The HTTP API enqueues distribution runs onto RQ (`status='queued'`),
  deduplicates double-submits, checkpoints progress per chunk for live
  `get_run` polling, and re-verifies actor permission, `rule_version`, and
  assignee/lead eligibility & capacity at execution time.
"""

import unittest
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.api import distribution as distribution_api
from crm_cskh.jobs import distribution_worker
from crm_cskh.services import distribution, eligibility
from crm_cskh.services import identity as identity_service
from crm_cskh.services.errors import Conflict, RuleChanged
from crm_cskh.services.weighted_allocator import allocate
from crm_cskh.tests.p1_base import P1TestCase

WEIGHTS = {"a": 50, "b": 30, "c": 20}


class TestAllocatorFairness(unittest.TestCase):
	"""The pure allocator: the fairness proof of P1 §7.3."""

	def test_1000_leads_are_split_500_300_200(self):
		rules = [{"user": user, "weight": weight, "capacity": 0} for user, weight in WEIGHTS.items()]
		result = allocate(1000, rules)
		counts = result["state"]["counts"]
		self.assertEqual(counts["a"], 500)
		self.assertEqual(counts["b"], 300)
		self.assertEqual(counts["c"], 200)
		self.assertEqual(sum(counts.values()), 1000)

	def test_100_batches_of_one_keep_the_cumulative_ratio(self):
		rules = [{"user": user, "weight": weight, "capacity": 0} for user, weight in WEIGHTS.items()]
		state = {}
		counts = {"a": 0, "b": 0, "c": 0}
		for _ in range(100):
			result = allocate(1, rules, state=state)
			state = result["state"]
			for user in counts:
				counts[user] += state["counts"].get(user, 0) - counts[user]
		self.assertEqual(counts, {"a": 50, "b": 30, "c": 20})
		self.assertEqual(state["total"], 100)

	def test_the_allocation_is_deterministic(self):
		rules = [{"user": user, "weight": weight, "capacity": 0} for user, weight in WEIGHTS.items()]
		first = allocate(50, rules)["assignments"]
		second = allocate(50, rules)["assignments"]
		self.assertEqual(first, second)

	def test_capacity_exhaustion_stops_the_allocation(self):
		rules = [
			{"user": "a", "weight": 50, "capacity": 2},
			{"user": "b", "weight": 30, "capacity": 0},
		]
		result = allocate(10, rules)
		# `a` hit its capacity after 2 items, so it left the eligible set and
		# the epoch restarted for the remaining user. The **epoch** counters
		# therefore describe the current epoch only, while the **cumulative
		# ledger** keeps what every user actually received (ADR-P1-04 §2.5).
		self.assertEqual(result["state"]["allocated_counts"]["a"], 2)
		self.assertEqual(result["state"]["allocated_counts"]["b"], 8)
		self.assertEqual(result["state"]["allocated_total"], 10)
		# The capacity is a hard limit: `a` never received more than 2.
		self.assertEqual(result["state"]["loads"]["a"], 2)
		self.assertEqual(len(result["assignments"]), 10)
		self.assertEqual(result["assignments"].count("a"), 2)

	def test_a_changed_user_set_starts_a_new_epoch(self):
		rules = [{"user": "a", "weight": 50, "capacity": 0}]
		state = allocate(10, rules)["state"]
		self.assertEqual(state["epoch"], 1)
		rules = [
			{"user": "a", "weight": 50, "capacity": 0},
			{"user": "b", "weight": 50, "capacity": 0},
		]
		state = allocate(1, rules, state=state)["state"]
		self.assertEqual(state["epoch"], 2)
		self.assertEqual(state["total"], 1)


class TestDistributionFlow(P1TestCase):
	"""The preview / execute flow against the real database."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.users = [self.staff_a, self.staff_b, self.staff_c]
		self.weights = {self.staff_a: 50, self.staff_b: 30, self.staff_c: 20}
		for user, weight in self.weights.items():
			self.make_rule(self.campaign, user, weight=weight)

	def campaign_work_items(self):
		return frappe.get_all("CSKH Work Item", filters={"campaign": self.campaign}, pluck="name")

	def make_pool(self, count):
		"""Create `count` canonical leads and select them in the campaign."""
		leads = [self.make_canonical_lead() for _ in range(count)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": leads})
		)
		return leads

	def test_preview_is_a_dry_run(self):
		self.make_pool(10)
		before = frappe.db.count("CSKH Work Item", {"campaign": self.campaign})
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		self.assertEqual(preview["summary"]["eligible_count"], 10)
		self.assertEqual(preview["campaign"], self.campaign)
		self.assertEqual(preview["rule_version"], 1)
		self.assertTrue(preview["selection_fingerprint"])
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), before)
		self.assertEqual(
			frappe.db.count("CSKH Assignment History", {"work_item": ["in", self.campaign_work_items()]}),
			0,
		)

	def test_preview_is_idempotent_for_the_same_selection(self):
		self.make_pool(5)
		first = distribution.preview(self.campaign, actor=self.admin_user)
		second = distribution.preview(self.campaign, actor=self.admin_user)
		self.assertEqual(first["preview_id"], second["preview_id"])

	def test_execute_assigns_every_lead_and_creates_the_claims(self):
		leads = self.make_pool(10)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		result = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(result["status"], "completed")
		self.assertEqual(result["created"], 10)
		self.assertEqual(result["errors"], 0)
		self.assertEqual(sum(result["assigned"].values()), 10)

		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), 10)
		self.assertEqual(
			frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "status": "assigned"}), 10
		)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"campaign": self.campaign}), 10)
		self.assertEqual(
			frappe.db.count(
				"CSKH Assignment History",
				{"work_item": ["in", self.campaign_work_items()], "action": "assign"},
			),
			10,
		)
		for lead in leads:
			self.assertTrue(frappe.db.exists("CSKH Active Claim", {"lead": lead, "campaign": self.campaign}))

	def test_execute_is_idempotent(self):
		self.make_pool(4)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		first = distribution.execute(preview["preview_id"], actor=self.admin_user)
		second = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(first["run_id"], second["run_id"])
		self.assertEqual(second["created"], first["created"])
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), 4)

	def test_a_changed_rule_version_makes_the_preview_stale(self):
		self.make_pool(5)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		self.make_rule(self.campaign, self.staff_a, weight=50, version=2)
		with self.assertRaises(RuleChanged):
			distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), 0)

	def test_a_changed_fairness_state_makes_the_preview_stale(self):
		self.make_pool(5)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		# Another run commits assignments in the meantime (a different lead
		# set, so it is a different preview of the same campaign).
		other_leads = self.make_pool(2)
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": other_leads})
		)
		other = distribution.preview(self.campaign, actor=self.admin_user)
		self.assertNotEqual(other["preview_id"], preview["preview_id"])
		distribution.execute(other["preview_id"], actor=self.admin_user)
		with self.assertRaises(RuleChanged):
			distribution.execute(preview["preview_id"], actor=self.admin_user)

	def test_the_capacity_limit_cuts_the_distribution_off(self):
		self.make_pool(6)
		frappe.db.set_value(
			"CSKH Assignment Rule",
			{"campaign": self.campaign, "user": self.staff_c},
			"capacity_limit",
			2,
		)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		result = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertLessEqual(result["assigned"].get(self.staff_c, 0), 2)
		self.assertEqual(result["created"] + result["errors"], 6)

	def test_a_lead_claimed_in_another_campaign_is_excluded(self):
		leads = self.make_pool(3)
		other_campaign = self.make_campaign()
		claimed = leads[0]
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": claimed,
				"campaign": other_campaign,
				"work_item": self.make_work_item(other_campaign, claimed),
			}
		).insert(ignore_permissions=True)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		self.assertEqual(preview["summary"]["eligible_count"], 2)
		result = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(result["created"], 2)

	def test_a_lead_cannot_be_claimed_twice_in_the_same_campaign(self):
		lead = self.make_canonical_lead()
		work_item = self.make_work_item(self.campaign, lead)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead,
				"campaign": self.campaign,
				"work_item": work_item,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": lead,
					"campaign": self.campaign,
					"work_item": work_item,
				}
			).insert(ignore_permissions=True)

	def test_reconcile_reports_the_database_truth(self):
		self.make_pool(4)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		distribution.execute(preview["preview_id"], actor=self.admin_user)
		report = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(report["active_work_items"], 4)
		self.assertEqual(report["active_claims"], 4)
		self.assertEqual(sum(report["current_owners"].values()), 4)

	def test_an_exhausted_campaign_is_rejected(self):
		self.make_pool(6)
		for user in self.users:
			frappe.db.set_value(
				"CSKH Assignment Rule",
				{"campaign": self.campaign, "user": user},
				"capacity_limit",
				1,
			)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		result = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(result["created"], 3)
		self.assertEqual(result["errors"], 3)


class TestPersistedFairness(P1TestCase):
	"""The cumulative fairness proof against the real database.

	100 single-lead batches must keep the cumulative 50/30/20 ratio: the
	fairness counters live in the database, not in memory (ADR-P1-04 §2.4).
	"""

	def test_100_single_lead_batches_keep_the_cumulative_ratio(self):
		campaign = self.make_campaign()
		weights = {self.staff_a: 50, self.staff_b: 30, self.staff_c: 20}
		for user, weight in weights.items():
			self.make_rule(campaign, user, weight=weight)

		leads = []
		for _ in range(100):
			leads.append(self.make_canonical_lead())

		for index, lead in enumerate(leads):
			frappe.db.set_value(
				"CSKH Campaign", campaign, "selection_json", frappe.as_json({"leads": [lead]})
			)
			preview = distribution.preview(
				campaign, actor=self.admin_user, idempotency_key=f"{campaign}-batch-{index}"
			)
			result = distribution.execute(preview["preview_id"], actor=self.admin_user)
			self.assertEqual(result["created"], 1, f"batch {index} did not assign exactly one lead")

		counts = frappe.db.sql(
			"""select assigned_to as user, count(*) as total
			   from `tabCSKH Work Item`
			   where campaign = %s and status = 'assigned'
			   group by assigned_to""",
			(campaign,),
			as_dict=True,
		)
		actual = {row.user: int(row.total) for row in counts}
		self.assertEqual(actual.get(self.staff_a, 0), 50)
		self.assertEqual(actual.get(self.staff_b, 0), 30)
		self.assertEqual(actual.get(self.staff_c, 0), 20)


class TestBackgroundDistributionRQ(P1TestCase):
	"""Background RQ distribution: early response, chunk polling, rechecks, deduplication."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=30)
		self.make_rule(self.campaign, self.staff_c, weight=20)
		frappe.set_user(self.admin_user)

	def make_pool(self, count, campaign=None):
		campaign = campaign or self.campaign
		leads = [self.make_canonical_lead() for _ in range(count)]
		frappe.db.set_value("CSKH Campaign", campaign, "selection_json", frappe.as_json({"leads": leads}))
		return leads

	def test_api_execute_enqueues_rq_and_returns_early_queued_state(self):
		self.make_pool(10)
		preview = distribution_api.preview(campaign=self.campaign)

		with mock.patch("frappe.enqueue") as mocked_enqueue:
			first = distribution_api.execute(
				preview_id=preview["preview_id"],
				expected_rule_version=preview["rule_version"],
			)
			# Double-submit while `queued` with an active lease MUST NOT enqueue again.
			second = distribution_api.execute(
				preview_id=preview["preview_id"],
				expected_rule_version=preview["rule_version"],
			)
			self.assertEqual(mocked_enqueue.call_count, 1)
			kwargs = mocked_enqueue.call_args.kwargs
			self.assertEqual(kwargs.get("queue"), "long")
			self.assertTrue(kwargs.get("enqueue_after_commit"))
			self.assertTrue(kwargs.get("deduplicate"))
			self.assertEqual(kwargs.get("job_id"), f"cskh-dist-{first['run_id']}")
			self.assertEqual(kwargs.get("run_id"), first["run_id"])
			self.assertEqual(kwargs.get("actor"), self.admin_user)

		self.assertEqual(first["run_id"], preview["preview_id"])
		self.assertEqual(first["status"], "queued")
		self.assertEqual(first["total"], 10)
		self.assertEqual(first["progress"], 0)
		self.assertEqual(first["created"], 0)
		self.assertEqual(second["run_id"], first["run_id"])
		self.assertEqual(second["status"], "queued")
		# Zero work items created before the RQ worker executes.
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), 0)

		# Execute the RQ worker job and verify completion + post-completion idempotency.
		done = distribution_worker.run_distribution(first["run_id"], actor=self.admin_user)
		self.assertEqual(done["status"], "completed")
		self.assertEqual(done["progress"], 10)
		self.assertEqual(done["created"], 10)
		self.assertEqual(done["assigned"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2})

		with mock.patch("frappe.enqueue") as mocked_after:
			third = distribution_api.execute(preview_id=preview["preview_id"])
			mocked_after.assert_not_called()
		self.assertEqual(third["status"], "completed")
		self.assertEqual(third["created"], 10)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), 10)

	def test_chunked_worker_persists_progress_and_polling_reads_cumulative_counts(self):
		self.make_pool(10)
		preview = distribution_api.preview(campaign=self.campaign)
		with mock.patch("frappe.enqueue"):
			queued = distribution_api.execute(preview_id=preview["preview_id"])
		frappe.db.commit()

		observed_polls = []
		real_commit = frappe.db.commit

		def observe_chunk_commits(*args, **kwargs):
			real_commit(*args, **kwargs)
			snapshot = distribution_api.get_run(run_id=queued["run_id"])
			if snapshot["status"] == "running" and snapshot["progress"] > 0:
				observed_polls.append(
					(
						snapshot["status"],
						snapshot["progress"],
						snapshot["created"],
						sum(snapshot["assigned"].values()),
					)
				)

		with mock.patch.object(distribution, "CHUNK_SIZE", 3):
			with mock.patch.object(frappe.db, "commit", side_effect=observe_chunk_commits):
				final = distribution_worker.run_distribution(queued["run_id"], actor=self.admin_user)

		progress_steps = [entry[1] for entry in observed_polls]
		self.assertIn(3, progress_steps)
		self.assertIn(6, progress_steps)
		self.assertIn(9, progress_steps)
		for status_val, prog_val, created_val, sum_assigned in observed_polls:
			self.assertEqual(status_val, "running")
			self.assertEqual(created_val, prog_val)
			self.assertEqual(sum_assigned, prog_val)

		self.assertEqual(final["status"], "completed")
		self.assertEqual(final["progress"], 10)
		self.assertEqual(final["total"], 10)
		self.assertEqual(final["created"], 10)
		self.assertEqual(final["assigned"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2})

	def test_worker_rechecks_actor_permission_and_rule_version_at_execution_time(self):
		# Case A: actor loses `CSKH CRM Admin` after enqueue -> worker aborts with FORBIDDEN.
		frappe.set_user("Administrator")
		temp_admin = "p1-temp-dist-admin@example.test"
		self.ensure_user(temp_admin, "CSKH CRM Admin")
		camp_a = self.make_campaign()
		self.make_rule(camp_a, self.staff_a, weight=100)
		self.make_pool(3, campaign=camp_a)
		frappe.set_user(temp_admin)
		prev_a = distribution_api.preview(campaign=camp_a)
		with mock.patch("frappe.enqueue"):
			queued_a = distribution_api.execute(preview_id=prev_a["preview_id"])
		frappe.set_user("Administrator")
		frappe.get_doc("User", temp_admin).remove_roles("CSKH CRM Admin")
		frappe.db.commit()

		aborted_perm = distribution_worker.run_distribution(queued_a["run_id"])
		self.assertEqual(aborted_perm["status"], "failed")
		self.assertTrue(str(aborted_perm["error"]).startswith("FORBIDDEN:"))
		self.assertEqual(aborted_perm["created"], 0)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": camp_a}), 0)

		# Case B: `rule_version` changes while queued -> worker aborts with RULE_CHANGED.
		camp_b = self.make_campaign()
		self.make_rule(camp_b, self.staff_a, weight=100)
		self.make_pool(3, campaign=camp_b)
		frappe.set_user(self.admin_user)
		prev_b = distribution_api.preview(campaign=camp_b)
		with mock.patch("frappe.enqueue"):
			queued_b = distribution_api.execute(preview_id=prev_b["preview_id"])
		self.make_rule(camp_b, self.staff_b, weight=50, version=2)
		frappe.db.commit()

		aborted_rule = distribution_worker.run_distribution(queued_b["run_id"], actor=self.admin_user)
		self.assertEqual(aborted_rule["status"], "failed")
		self.assertTrue(str(aborted_rule["error"]).startswith("RULE_CHANGED:"))
		self.assertEqual(aborted_rule["created"], 0)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": camp_b}), 0)

	def test_worker_rechecks_assignee_and_lead_eligibility_and_capacity_mid_run(self):
		leads = self.make_pool(6)
		# Cap `staff_b` at 1 item so capacity is enforced dynamically across chunks.
		frappe.db.set_value(
			"CSKH Assignment Rule",
			{"campaign": self.campaign, "user": self.staff_b},
			"capacity_limit",
			1,
		)
		other_campaign = self.make_campaign()
		preview = distribution_api.preview(campaign=self.campaign)
		with mock.patch("frappe.enqueue"):
			queued = distribution_api.execute(preview_id=preview["preview_id"])
		frappe.db.commit()

		self.addCleanup(frappe.db.set_value, "User", self.staff_c, "enabled", 1)
		mutated_mid_run = {"done": False}
		real_commit = frappe.db.commit

		def mutate_after_first_chunk(*args, **kwargs):
			real_commit(*args, **kwargs)
			if not mutated_mid_run["done"]:
				prog = int(frappe.db.get_value("CSKH Distribution Run", queued["run_id"], "progress") or 0)
				if prog >= 2:
					mutated_mid_run["done"] = True
					# Disable `staff_c` and claim `leads[4]` in another campaign before chunk 2/3!
					frappe.db.set_value("User", self.staff_c, "enabled", 0)
					other_wi = self.make_work_item(other_campaign, leads[4], status="queued")
					frappe.get_doc(
						{
							"doctype": "CSKH Active Claim",
							"lead": leads[4],
							"campaign": other_campaign,
							"work_item": other_wi,
						}
					).insert(ignore_permissions=True)
					real_commit()

		with mock.patch.object(distribution, "CHUNK_SIZE", 2):
			with mock.patch.object(frappe.db, "commit", side_effect=mutate_after_first_chunk):
				result = distribution_worker.run_distribution(queued["run_id"], actor=self.admin_user)

		self.assertTrue(mutated_mid_run["done"])
		self.assertEqual(result["status"], "completed_with_errors")
		self.assertEqual(result["created"], 5)
		self.assertEqual(result["errors"], 1)
		self.assertLessEqual(result["assigned"].get(self.staff_b, 0), 1)
		# `staff_c` was disabled after chunk 1 (progress=2) so it received 0 items in chunks 2 & 3.
		self.assertEqual(result["assigned"].get(self.staff_c, 0), 0)
		self.assertEqual(result["skipped"][0]["lead"], leads[4])
		self.assertEqual(result["skipped"][0]["reason"], "active_claim_in_other_campaign")

		# Double-submit while active lease is held is a no-op as well.
		frappe.db.set_value(
			"CSKH Distribution Run",
			queued["run_id"],
			"lease_expires_at",
			add_to_date(now_datetime(), seconds=300),
		)
		reconciled = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(reconciled["active_work_items"], 5)
		self.assertEqual(reconciled["active_claims"], 5)
