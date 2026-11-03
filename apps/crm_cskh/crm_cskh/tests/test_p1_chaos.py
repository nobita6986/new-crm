"""P1 — runtime chaos: crash, rollback and retry (P1 §12.4, T1-13).

P1 §12.4 demands the **runtime** proof of failure modes across both import and
distribution pipelines:

* an artificial failure **after** the work item was created but **before** the
  ownership completed ⇒ the transaction must roll back and leave zero orphan
  work items, active claims, wrong owners, ToDos, DocShares or partial history;
* a failure **after** the DB commit but **before** the notification ⇒ the
  committed assignment survives and retrying does not create duplicate
  assignments, history events, ToDos or notifications;
* an import or distribution RQ worker stopped **in the middle** of a batch
  (including hard process exit `os._exit(137)`) ⇒ DB lease & checkpoint
  recovery completes the batch with zero duplicates verified by database
  post-checks.

Every fault injection lives strictly inside this test module (via `unittest.mock`
wrappers and an isolated child process); production `api/`, `services/` and
`jobs/` modules carry zero fault-injection hooks.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.jobs import distribution_worker, import_worker
from crm_cskh.services import distribution, notifications, ownership
from crm_cskh.services.import_service import confirm, create_preview
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.tests.p1_base import P1TestCase

CHILD_CRASH_SCRIPT = """
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.getcwd())

import frappe
from crm_cskh.jobs import import_worker

payload = json.loads(os.environ["CSKH_CHAOS_CHILD_PAYLOAD"])
frappe.init(payload["site"], sites_path=payload["sites_path"], force=True)
frappe.connect()
frappe.set_user("Administrator")

batch_name = payload["batch_name"]
chunk_size = int(payload["chunk_size"])
kill_after_progress = int(payload["kill_after_progress"])

real_commit = frappe.db.commit

def commit_then_hard_kill(*args, **kwargs):
	real_commit(*args, **kwargs)
	row = frappe.db.get_value(
		"CSKH Import Batch",
		batch_name,
		["status", "progress"],
		as_dict=True,
	)
	if row and row.status == "RUNNING" and int(row.progress or 0) >= kill_after_progress:
		os._exit(137)

with mock.patch.object(import_worker, "CHUNK_SIZE", chunk_size):
	with mock.patch.object(frappe.db, "commit", side_effect=commit_then_hard_kill):
		import_worker.run_batch(batch_name)
"""

CHILD_DISTRIBUTION_CRASH_SCRIPT = """
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.getcwd())

import frappe
from crm_cskh.jobs import distribution_worker
from crm_cskh.services import distribution

payload = json.loads(os.environ["CSKH_CHAOS_DIST_PAYLOAD"])
frappe.init(payload["site"], sites_path=payload["sites_path"], force=True)
frappe.connect()
frappe.set_user("Administrator")

run_id = payload["run_id"]
actor = payload["actor"]
chunk_size = int(payload["chunk_size"])
kill_after_progress = int(payload["kill_after_progress"])

real_commit = frappe.db.commit

def commit_then_hard_kill(*args, **kwargs):
	real_commit(*args, **kwargs)
	row = frappe.db.get_value(
		"CSKH Distribution Run",
		run_id,
		["status", "progress"],
		as_dict=True,
	)
	if row and row.status == "running" and int(row.progress or 0) >= kill_after_progress:
		os._exit(137)

with mock.patch.object(distribution, "CHUNK_SIZE", chunk_size):
	with mock.patch.object(frappe.db, "commit", side_effect=commit_then_hard_kill):
		distribution_worker.run_distribution(run_id, actor=actor)
"""


class ChaosTestCase(P1TestCase):
	"""Shared DB-level post-check assertions for P1 crash/rollback/retry tests."""

	def tearDown(self):
		frappe.db.rollback()
		super().tearDown()

	def assertNoOrphan(self, campaign, lead):
		"""Assert that `lead` in `campaign` has coherent DB state with zero orphans."""
		work_items = frappe.get_all(
			"CSKH Work Item",
			filters={"campaign": campaign, "lead": lead},
			fields=["name", "status", "assigned_to", "allocation_run"],
		)
		claims = frappe.get_all(
			"CSKH Active Claim",
			filters={"campaign": campaign, "lead": lead},
			fields=["name", "work_item"],
		)
		item_names = [row.name for row in work_items]
		history = (
			frappe.get_all(
				"CSKH Assignment History",
				filters={"work_item": ["in", item_names]},
				fields=["name", "action", "to_user", "from_user", "ownership_end"],
			)
			if item_names
			else []
		)
		claim_work_items = {row.work_item for row in claims}
		item_name_set = set(item_names)
		self.assertFalse(
			claim_work_items - item_name_set,
			f"orphan active claim(s) without work item: {claim_work_items - item_name_set}",
		)
		lead_owner = frappe.db.get_value("CRM Lead", lead, "lead_owner")
		open_todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "CRM Lead", "reference_name": lead, "status": "Open"},
			fields=["name", "allocated_to"],
		)
		shares = frappe.get_all(
			"DocShare",
			filters={"share_doctype": "CRM Lead", "share_name": lead},
			fields=["name", "user"],
		)
		if not work_items:
			self.assertEqual(len(claims), 0)
			self.assertEqual(len(history), 0)
			self.assertIsNone(lead_owner)
			self.assertEqual(len(open_todos), 0)
			self.assertEqual(len(shares), 0)
			return

		for item in work_items:
			self.assertIn(item.name, claim_work_items, f"orphan work item {item.name} without claim")
			self.assertIn(item.status, ("queued", "assigned"))
			if item.status == "assigned":
				self.assertTrue(item.assigned_to)
				self.assertEqual(lead_owner, item.assigned_to)
				self.assertEqual([t.allocated_to for t in open_todos], [item.assigned_to])
				self.assertEqual([s.user for s in shares], [item.assigned_to])
		for event in history:
			if event.action in ("assign", "reassign"):
				self.assertTrue(event.to_user)
			if event.action == "revoke":
				self.assertIsNone(event.to_user)


class TestDistributionRollbackAndResume(ChaosTestCase):
	"""Faults after Work Item creation / mid-ownership and resumable runs."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=30)
		self.make_rule(self.campaign, self.staff_c, weight=20)

	def make_pool(self, count):
		leads = [self.make_canonical_lead() for _ in range(count)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": leads})
		)
		return leads

	def test_fault_after_work_item_before_ownership_leaves_no_orphan(self):
		leads = self.make_pool(3)
		preview = distribution.preview(self.campaign, actor=self.admin_user)

		real_assign = ownership.assign
		observed_in_tx = []

		def fail_first_before_ownership(work_item, target_user, **kwargs):
			if not observed_in_tx:
				lead_name = frappe.db.get_value("CSKH Work Item", work_item, "lead")
				claim_exists = bool(
					frappe.db.exists("CSKH Active Claim", {"work_item": work_item, "lead": lead_name})
				)
				observed_in_tx.append(
					{
						"work_item": work_item,
						"lead": lead_name,
						"work_item_existed": True,
						"claim_existed": claim_exists,
					}
				)
				raise RuntimeError("injected fault after Work Item creation before ownership")
			return real_assign(work_item, target_user, **kwargs)

		with mock.patch(
			"crm_cskh.services.distribution.ownership.assign", side_effect=fail_first_before_ownership
		):
			result = distribution.execute(preview["preview_id"], actor=self.admin_user)

		self.assertEqual(len(observed_in_tx), 1)
		self.assertTrue(observed_in_tx[0]["work_item_existed"])
		self.assertTrue(observed_in_tx[0]["claim_existed"])

		self.assertEqual(result["status"], "completed_with_errors")
		self.assertEqual(result["errors"], 1)
		self.assertEqual(result["created"], 2)

		for lead in leads:
			self.assertNoOrphan(self.campaign, lead)

		failed_lead = result["skipped"][0]["lead"]
		self.assertEqual(failed_lead, observed_in_tx[0]["lead"])
		self.assertFalse(frappe.db.exists("CSKH Work Item", observed_in_tx[0]["work_item"]))
		self.assertEqual(
			frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "lead": failed_lead}), 0
		)
		self.assertEqual(
			frappe.db.count("CSKH Active Claim", {"campaign": self.campaign, "lead": failed_lead}), 0
		)
		self.assertEqual(
			frappe.db.count("CSKH Assignment History", {"work_item": observed_in_tx[0]["work_item"]}),
			0,
		)

	def test_fault_mid_ownership_after_history_and_todo_rolls_back_all_artifacts(self):
		leads = self.make_pool(2)
		preview = distribution.preview(self.campaign, actor=self.admin_user)

		real_assign = ownership.assign
		observed_partial = []

		def fail_after_ownership_writes(work_item, target_user, **kwargs):
			res = real_assign(work_item, target_user, **kwargs)
			if not observed_partial:
				lead_name = frappe.db.get_value("CSKH Work Item", work_item, "lead")
				observed_partial.append(
					{
						"work_item": work_item,
						"lead": lead_name,
						"history_count": frappe.db.count("CSKH Assignment History", {"work_item": work_item}),
						"todo_count": frappe.db.count(
							"ToDo",
							{"reference_type": "CRM Lead", "reference_name": lead_name, "status": "Open"},
						),
						"share_count": frappe.db.count(
							"DocShare", {"share_doctype": "CRM Lead", "share_name": lead_name}
						),
						"lead_owner": frappe.db.get_value("CRM Lead", lead_name, "lead_owner"),
					}
				)
				raise RuntimeError("injected fault after history/todo/docshare before row commit")
			return res

		with mock.patch(
			"crm_cskh.services.distribution.ownership.assign", side_effect=fail_after_ownership_writes
		):
			result = distribution.execute(preview["preview_id"], actor=self.admin_user)

		self.assertEqual(len(observed_partial), 1)
		self.assertEqual(observed_partial[0]["history_count"], 1)
		self.assertEqual(observed_partial[0]["todo_count"], 1)
		self.assertEqual(observed_partial[0]["share_count"], 1)
		self.assertIsNotNone(observed_partial[0]["lead_owner"])

		self.assertEqual(result["errors"], 1)
		self.assertEqual(result["created"], 1)
		for lead in leads:
			self.assertNoOrphan(self.campaign, lead)

		failed_lead = observed_partial[0]["lead"]
		self.assertEqual(
			frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "lead": failed_lead}), 0
		)
		self.assertEqual(
			frappe.db.count("CSKH Active Claim", {"campaign": self.campaign, "lead": failed_lead}), 0
		)
		self.assertEqual(
			frappe.db.count("CSKH Assignment History", {"work_item": observed_partial[0]["work_item"]}),
			0,
		)
		self.assertIsNone(frappe.db.get_value("CRM Lead", failed_lead, "lead_owner"))
		self.assertEqual(
			frappe.db.count("ToDo", {"reference_type": "CRM Lead", "reference_name": failed_lead}), 0
		)
		self.assertEqual(
			frappe.db.count("DocShare", {"share_doctype": "CRM Lead", "share_name": failed_lead}), 0
		)

	def test_fault_mid_reassign_rolls_back_to_original_owner_and_open_interval(self):
		leads = self.make_pool(1)
		lead = leads[0]
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		distribution.execute(preview["preview_id"], actor=self.admin_user)
		work_item = frappe.db.get_value("CSKH Work Item", {"campaign": self.campaign, "lead": lead}, "name")
		original_owner = frappe.db.get_value("CSKH Work Item", work_item, "assigned_to")
		new_owner = self.staff_b if original_owner != self.staff_b else self.staff_a

		savepoint = "cskh_chaos_reassign"
		frappe.db.savepoint(savepoint)
		with mock.patch(
			"crm_cskh.services.ownership._assert_final_owner",
			side_effect=RuntimeError("injected fault at end of reassign before commit"),
		):
			with self.assertRaises(RuntimeError):
				ownership.reassign(
					work_item,
					expected_owner=original_owner,
					target_user=new_owner,
					actor=self.admin_user,
					reason="Chaos mid-reassign test",
					request_id="req-chaos-reassign-rollback",
				)
		frappe.db.rollback(save_point=savepoint)

		self.assertNoOrphan(self.campaign, lead)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", work_item, "assigned_to"), original_owner)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_owner"), original_owner)
		self.assertEqual(frappe.db.count("CSKH Assignment History", {"work_item": work_item}), 1)
		open_history = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": work_item, "action": "assign"},
			["to_user", "ownership_end"],
			as_dict=True,
		)
		self.assertEqual(open_history.to_user, original_owner)
		self.assertIsNone(open_history.ownership_end)

	def test_resumed_run_retries_only_failed_leads_sums_counts_and_keeps_fairness(self):
		leads = self.make_pool(10)
		preview = distribution.preview(self.campaign, actor=self.admin_user)

		real_assign = ownership.assign
		call_index = {"n": 0}
		fail_indices = {2, 5, 8}

		def fail_selected_calls(work_item, target_user, **kwargs):
			call_index["n"] += 1
			if call_index["n"] in fail_indices:
				raise RuntimeError(f"transient failure on call {call_index['n']}")
			return real_assign(work_item, target_user, **kwargs)

		with mock.patch("crm_cskh.services.distribution.ownership.assign", side_effect=fail_selected_calls):
			first = distribution.execute(preview["preview_id"], actor=self.admin_user)

		self.assertEqual(first["status"], "completed_with_errors")
		self.assertEqual(first["created"], 7)
		self.assertEqual(first["errors"], 3)
		self.assertEqual(sum(first["assigned"].values()), 7)

		second = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(second["status"], "completed")
		self.assertEqual(second["errors"], 0)
		self.assertEqual(second["created"], 10)
		self.assertEqual(sum(second["assigned"].values()), 10)
		self.assertEqual(
			second["assigned"],
			{self.staff_a: 5, self.staff_b: 3, self.staff_c: 2},
		)

		# Third execution on completed run is a strict idempotent read.
		third = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(third["run_id"], second["run_id"])
		self.assertEqual(third["created"], 10)

		for lead in leads:
			self.assertNoOrphan(self.campaign, lead)

		reconciliation = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(reconciliation["distinct_leads"], 10)
		self.assertEqual(reconciliation["active_work_items"], 10)
		self.assertEqual(reconciliation["active_claims"], 10)
		self.assertEqual(
			reconciliation["current_owners"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2}
		)
		self.assertEqual(
			reconciliation["assignment_events"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2}
		)


class TestPostCommitNotificationRetry(ChaosTestCase):
	"""Failure **after** DB commit **before** notification delivery (P1 §7.5, §12.4)."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=50)
		self.leads = [self.make_canonical_lead(), self.make_canonical_lead()]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": self.leads})
		)

	def test_fault_after_commit_before_notification_and_idempotent_retry(self):
		preview = distribution.preview(self.campaign, actor=self.admin_user)

		def fail_post_commit_notification(work_item, lead, to_user, actor):
			# Simulate notification not yet written when post-commit delivery fails.
			frappe.db.delete(
				"CRM Notification",
				{
					"reference_doctype": "CRM Lead",
					"reference_name": lead,
					"to_user": to_user,
					"type": "Assignment",
				},
			)
			frappe.db.commit()
			raise RuntimeError("injected post-commit notification gateway error")

		with mock.patch(
			"crm_cskh.services.notifications._ensure_notification",
			side_effect=fail_post_commit_notification,
		):
			result = distribution.execute(preview["preview_id"], actor=self.admin_user)

		# Assignments ARE committed despite notification failure.
		self.assertEqual(result["status"], "completed")
		self.assertEqual(result["created"], 2)
		self.assertEqual(result["errors"], 0)

		items = frappe.get_all(
			"CSKH Work Item",
			filters={"campaign": self.campaign},
			fields=["name", "lead", "assigned_to", "status"],
		)
		self.assertEqual(len(items), 2)
		for item in items:
			self.assertEqual(item.status, "assigned")
			self.assertEqual(
				frappe.db.count(
					"CRM Notification",
					{
						"reference_doctype": "CRM Lead",
						"reference_name": item.lead,
						"to_user": item.assigned_to,
						"type": "Assignment",
					},
				),
				0,
			)

		# Retry via run execute, notification service (twice), and ownership request_id.
		repeat_run = distribution.execute(preview["preview_id"], actor=self.admin_user)
		self.assertEqual(repeat_run["created"], 2)

		for item in items:
			d1 = notifications.deliver_assignment(
				item.name,
				item.assigned_to,
				event_id=f"assign:{item.name}:run:{result['run_id']}",
				actor=self.admin_user,
			)
			d2 = notifications.deliver_assignment(
				item.name,
				item.assigned_to,
				event_id=f"assign:{item.name}:run:{result['run_id']}",
				actor=self.admin_user,
			)
			frappe.db.commit()
			self.assertTrue(d1["delivered"])
			self.assertTrue(d2["delivered"])
			self.assertEqual(d1["todo"], d2["todo"])
			self.assertEqual(d1["notification"], d2["notification"])

			dup_assign = ownership.assign(
				item.name,
				item.assigned_to,
				actor=self.admin_user,
				request_id=f"run:{result['run_id']}:{item.lead}",
			)
			self.assertEqual(dup_assign["status"], "already_applied")

			# DB post-checks: 1 notification, 1 Open ToDo, 1 assign history, 1 claim, 1 work item.
			self.assertEqual(
				frappe.db.count(
					"CRM Notification",
					{
						"reference_doctype": "CRM Lead",
						"reference_name": item.lead,
						"to_user": item.assigned_to,
						"type": "Assignment",
					},
				),
				1,
			)
			self.assertEqual(
				frappe.db.count(
					"ToDo",
					{
						"reference_type": "CRM Lead",
						"reference_name": item.lead,
						"allocated_to": item.assigned_to,
						"status": "Open",
					},
				),
				1,
			)
			self.assertEqual(
				frappe.db.count("CSKH Assignment History", {"work_item": item.name, "action": "assign"}),
				1,
			)
			self.assertEqual(
				frappe.db.count("CSKH Active Claim", {"campaign": self.campaign, "lead": item.lead}), 1
			)
			self.assertEqual(
				frappe.db.count("CSKH Work Item", {"campaign": self.campaign, "lead": item.lead}), 1
			)


class TestImportWorkerCrashAndRecovery(ChaosTestCase):
	"""Import worker mid-batch interruption (including OS process hard kill) and recovery."""

	def upload(self, content, file_name="chaos-import.csv"):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"is_private": 1,
				"attached_to_doctype": "CRM Lead",
				"attached_to_name": "Administrator",
				"content": content,
			}
		)
		file_doc.insert(ignore_permissions=True)
		return file_doc.name

	def build_csv(self, phones):
		lines = ["Số điện thoại", *phones]
		return ("\n".join(lines) + "\n").encode("utf-8")

	def queue_batch(self, batch_name):
		with mock.patch("frappe.enqueue"):
			resp = confirm(batch_name, actor=self.admin_user)
		self.assertEqual(resp["status"], "QUEUED")

	def test_worker_hard_kill_os_exit_mid_batch_recovers_with_zero_duplicates(self):
		phones = [self.unique_phone() for _ in range(7)]
		canonicals = [normalize_vn_phone(p) for p in phones]
		file_name = self.upload(self.build_csv(phones), file_name="chaos-hard-kill.csv")
		batch = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("chaos-hard-kill")
		)
		batch_name = batch["batch"]
		self.queue_batch(batch_name)
		frappe.db.commit()

		payload = {
			"site": getattr(frappe.local, "site", None),
			"sites_path": getattr(frappe.local, "sites_path", None),
			"batch_name": batch_name,
			"chunk_size": 2,
			"kill_after_progress": 3,
		}
		completed = subprocess.run(
			[sys.executable, "-c", CHILD_CRASH_SCRIPT],
			capture_output=True,
			text=True,
			timeout=120,
			env={**os.environ, "CSKH_CHAOS_CHILD_PAYLOAD": json.dumps(payload)},
		)
		self.assertEqual(completed.returncode, 137, f"expected hard exit 137, stderr={completed.stderr}")

		# Refresh MVCC snapshot to observe child process's committed chunk.
		frappe.db.commit()
		crashed_state = frappe.db.get_value(
			"CSKH Import Batch",
			batch_name,
			["status", "attempts", "progress"],
			as_dict=True,
		)
		self.assertEqual(crashed_state.status, "RUNNING")
		self.assertEqual(int(crashed_state.attempts), 1)
		self.assertEqual(int(crashed_state.progress), 3)

		# Chunk 1 (rows 2 & 3 -> first 2 phones) committed; remaining 5 phones not yet in DB.
		committed_mid_crash = frappe.db.count("CSKH Identity Index", {"normalized_phone": ["in", canonicals]})
		self.assertEqual(committed_mid_crash, 2)

		# Recovery run resumes from progress=3 and imports remaining chunks cleanly.
		with mock.patch.object(import_worker, "CHUNK_SIZE", 2):
			import_worker.run_batch(batch_name)
			# Calling run_batch again on COMPLETED batch is a strict no-op.
			import_worker.run_batch(batch_name)

		recovered = frappe.get_doc("CSKH Import Batch", batch_name)
		self.assertEqual(recovered.status, "COMPLETED")
		self.assertEqual(int(recovered.attempts), 2)
		self.assertEqual(int(recovered.created_rows), 7)
		self.assertEqual(int(recovered.reused_rows), 0)
		self.assertEqual(int(recovered.error_rows), 0)
		self.assertEqual(int(recovered.skipped_rows), 0)

		for canonical in canonicals:
			self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": canonical}), 1)
			self.assertEqual(
				frappe.db.count("CRM Lead", {"mobile_no": canonical, "cskh_is_placeholder_name": 1}),
				1,
			)
		dup_identities = frappe.db.sql(
			"""select normalized_phone, count(*) as c
			   from `tabCSKH Identity Index`
			   where normalized_phone in %s
			   group by normalized_phone having count(*) > 1""",
			(tuple(canonicals),),
		)
		self.assertEqual(len(dup_identities), 0)

	def test_failed_row_in_early_chunk_blocks_progress_advance_and_recovers_on_retry(self):
		phones = [self.unique_phone() for _ in range(6)]
		canonicals = [normalize_vn_phone(p) for p in phones]
		file_name = self.upload(self.build_csv(phones), file_name="chaos-blocked-at.csv")
		batch = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("chaos-blocked-at")
		)
		batch_name = batch["batch"]
		self.queue_batch(batch_name)

		real_ensure = import_worker._ensure_lead
		failed_once = {"done": False}
		target_fail_phone = canonicals[1]  # Row #3 in Chunk 1 (with CHUNK_SIZE=2)

		def fail_second_phone_once(canonical_phone, row):
			if canonical_phone == target_fail_phone and not failed_once["done"]:
				failed_once["done"] = True
				raise RuntimeError("transient DB error on row 3")
			return real_ensure(canonical_phone, row)

		with mock.patch.object(import_worker, "CHUNK_SIZE", 2):
			with mock.patch.object(import_worker, "_ensure_lead", side_effect=fail_second_phone_once):
				import_worker.run_batch(batch_name)

		after_first = frappe.get_doc("CSKH Import Batch", batch_name)
		self.assertEqual(after_first.status, "COMPLETED_WITH_ERRORS")
		self.assertEqual(int(after_first.created_rows), 5)
		self.assertEqual(int(after_first.error_rows), 1)
		# Progress MUST stay at row 2 (before failed row 3), even though chunks 2 & 3 succeeded!
		self.assertEqual(int(after_first.progress), 2)
		self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": target_fail_phone}), 0)

		# Re-queue via confirm() and retry: row 3 is retried and created, rows 4..7 are adopted without duplication.
		self.queue_batch(batch_name)
		with mock.patch.object(import_worker, "CHUNK_SIZE", 2):
			import_worker.run_batch(batch_name)

		after_retry = frappe.get_doc("CSKH Import Batch", batch_name)
		self.assertEqual(after_retry.status, "COMPLETED")
		self.assertEqual(int(after_retry.attempts), 2)
		self.assertEqual(int(after_retry.created_rows), 6)
		self.assertEqual(int(after_retry.reused_rows), 0)
		self.assertEqual(int(after_retry.error_rows), 0)
		for canonical in canonicals:
			self.assertEqual(frappe.db.count("CSKH Identity Index", {"normalized_phone": canonical}), 1)
			self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": canonical}), 1)

	def test_mixed_batch_does_not_double_count_preview_invalid_rows(self):
		existing_lead = self.make_canonical_lead()
		existing_phone = frappe.db.get_value("CRM Lead", existing_lead, "mobile_no")
		new_phones = [self.unique_phone(), self.unique_phone()]
		csv_rows = [
			new_phones[0],
			new_phones[1],
			existing_phone,
			new_phones[0],
			"invalid-phone-alpha",
			"123",
		]
		file_name = self.upload(self.build_csv(csv_rows), file_name="chaos-mixed-counts.csv")
		batch = create_preview(
			file_name, actor=self.admin_user, idempotency_key=self.unique_key("chaos-mixed-counts")
		)
		batch_name = batch["batch"]
		self.queue_batch(batch_name)
		import_worker.run_batch(batch_name)

		batch_doc = frappe.get_doc("CSKH Import Batch", batch_name)
		self.assertEqual(batch_doc.status, "COMPLETED_WITH_ERRORS")
		self.assertEqual(int(batch_doc.total_rows), 6)
		self.assertEqual(int(batch_doc.new_rows), 2)
		self.assertEqual(int(batch_doc.duplicate_rows), 2)
		self.assertEqual(int(batch_doc.error_rows), 2)
		self.assertEqual(int(batch_doc.skipped_rows), 2)
		self.assertEqual(int(batch_doc.created_rows), 2)
		self.assertEqual(int(batch_doc.reused_rows), 1)
		self.assertTrue(batch_doc.error_artifact)


class TestDistributionWorkerCrashAndRecovery(ChaosTestCase):
	"""Distribution RQ worker hard-kill (`os._exit(137)`) mid-batch + DB lease recovery."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign()
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=30)
		self.make_rule(self.campaign, self.staff_c, weight=20)

	def test_distribution_worker_hard_kill_os_exit_mid_batch_and_lease_recovery(self):
		leads = [self.make_canonical_lead() for _ in range(10)]
		frappe.db.set_value(
			"CSKH Campaign", self.campaign, "selection_json", frappe.as_json({"leads": leads})
		)
		preview = distribution.preview(self.campaign, actor=self.admin_user)
		queued = distribution.enqueue_run(preview["preview_id"], actor=self.admin_user, enqueue_job=False)
		run_id = queued["run_id"]
		self.assertEqual(queued["status"], "queued")
		frappe.db.commit()

		payload = {
			"site": getattr(frappe.local, "site", None),
			"sites_path": getattr(frappe.local, "sites_path", None),
			"run_id": run_id,
			"actor": self.admin_user,
			"chunk_size": 3,
			"kill_after_progress": 6,
		}
		completed = subprocess.run(
			[sys.executable, "-c", CHILD_DISTRIBUTION_CRASH_SCRIPT],
			capture_output=True,
			text=True,
			timeout=120,
			env={**os.environ, "CSKH_CHAOS_DIST_PAYLOAD": json.dumps(payload)},
		)
		self.assertEqual(completed.returncode, 137, f"expected hard exit 137, stderr={completed.stderr}")

		# Refresh MVCC snapshot to observe child worker's committed chunks.
		frappe.db.commit()
		mid_poll = distribution.get_run(run_id, actor=self.admin_user)
		self.assertEqual(mid_poll["status"], "running")
		self.assertEqual(mid_poll["progress"], 6)
		self.assertEqual(mid_poll["created"], 6)
		self.assertEqual(frappe.db.count("CSKH Work Item", {"campaign": self.campaign}), 6)
		self.assertEqual(frappe.db.count("CSKH Active Claim", {"campaign": self.campaign}), 6)

		# While lease is active, neither double-submit nor duplicate worker steals the run.
		with mock.patch("frappe.enqueue") as mocked_enqueue:
			active_double = distribution.enqueue_run(run_id, actor=self.admin_user, enqueue_job=True)
			mocked_enqueue.assert_not_called()
		self.assertEqual(active_double["status"], "running")
		noop_while_leased = distribution_worker.run_distribution(run_id, actor=self.admin_user)
		self.assertEqual(noop_while_leased["created"], 6)

		# Expire lease (simulating crash lease timeout) and recover cleanly from progress=6.
		frappe.db.set_value(
			"CSKH Distribution Run",
			run_id,
			"lease_expires_at",
			add_to_date(now_datetime(), seconds=-60),
		)
		frappe.db.commit()

		with mock.patch("frappe.enqueue") as mocked_recovery_enqueue:
			recovered_enqueue = distribution.enqueue_run(run_id, actor=self.admin_user, enqueue_job=True)
			mocked_recovery_enqueue.assert_called_once()
		self.assertEqual(recovered_enqueue["status"], "queued")
		frappe.db.commit()

		with mock.patch.object(distribution, "CHUNK_SIZE", 3):
			final = distribution_worker.run_distribution(run_id, actor=self.admin_user)
			# Calling worker again on completed run is a strict no-op.
			distribution_worker.run_distribution(run_id, actor=self.admin_user)

		self.assertEqual(final["status"], "completed")
		self.assertEqual(final["progress"], 10)
		self.assertEqual(final["created"], 10)
		self.assertEqual(final["errors"], 0)
		self.assertEqual(final["assigned"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2})

		for lead in leads:
			self.assertNoOrphan(self.campaign, lead)

		reconciled = distribution.reconcile(self.campaign, actor=self.admin_user)
		self.assertEqual(reconciled["distinct_leads"], 10)
		self.assertEqual(reconciled["active_work_items"], 10)
		self.assertEqual(reconciled["active_claims"], 10)
		self.assertEqual(reconciled["current_owners"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2})
		self.assertEqual(reconciled["assignment_events"], {self.staff_a: 5, self.staff_b: 3, self.staff_c: 2})


class TestZeroProductionFaultInjection(unittest.TestCase):
	"""Verify zero fault-injection pathways exist in production API/services/jobs."""

	def test_chaos_module_does_not_exist_in_production_services(self):
		self.assertIsNone(importlib.util.find_spec("crm_cskh.services.chaos"))
		app_root = Path(distribution.__file__).resolve().parent.parent
		self.assertFalse((app_root / "services" / "chaos.py").exists())

	def test_production_modules_contain_no_fault_injection_hooks_or_hard_exit(self):
		app_root = Path(distribution.__file__).resolve().parent.parent
		forbidden_names = {"chaos", "maybe_fail", "maybe_crash", "InjectedFailure", "_exit"}
		for subdir in ("api", "services", "jobs"):
			for py_file in sorted((app_root / subdir).rglob("*.py")):
				tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
				for node in ast.walk(tree):
					if isinstance(node, ast.Name):
						self.assertNotIn(
							node.id,
							forbidden_names,
							f"forbidden fault-injection symbol {node.id!r} in {py_file}",
						)
					elif isinstance(node, ast.Attribute):
						self.assertNotIn(
							node.attr,
							forbidden_names,
							f"forbidden fault-injection attribute {node.attr!r} in {py_file}",
						)
					elif isinstance(node, ast.ImportFrom) and node.module:
						self.assertNotIn(
							"chaos",
							node.module,
							f"forbidden chaos import {node.module!r} in {py_file}",
						)
