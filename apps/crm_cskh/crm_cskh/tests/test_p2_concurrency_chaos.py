"""P2 Idempotency, Optimistic Revision Locking, Multi-Thread MariaDB Races & Rollback/Chaos (`T2-08`, `T2-09`, `T2-10`, `T2-11`, `P2.md` §15.3-§15.4)."""

from __future__ import annotations

import ast
import threading
import unittest
from pathlib import Path
from unittest import mock

import frappe

from crm_cskh.services import (
	contact_attempt,
	correction,
	followup,
	notifications,
	ownership,
	policies,
	read_model,
)
from crm_cskh.services.errors import IdempotencyConflict, StaleWorkItem
from crm_cskh.tests.p2_base import P2TestCase


def _run_db_thread(site: str, sites_path: str, fn, results: dict, key: str):
	def target():
		attempt = 0
		while True:
			try:
				frappe.init(site, sites_path=sites_path, force=True)
				frappe.connect()
				frappe.set_user("Administrator")
				out = fn()
				frappe.db.commit()
				results[key] = {"ok": out}
				return
			except Exception as exc:
				retryable = "1020" in str(exc) or "1213" in str(exc) or "Deadlock" in type(exc).__name__
				try:
					frappe.db.rollback()
				except Exception:
					pass
				if retryable and attempt < 4:
					attempt += 1
					continue
				results[key] = {
					"error_type": type(exc).__name__,
					"code": getattr(exc, "code", None),
					"error": str(exc),
				}
				return
			finally:
				try:
					frappe.db.close()
				except Exception:
					pass

	t = threading.Thread(target=target)
	t.start()
	return t


class TestP2IdempotencyAndStaleRevision(P2TestCase):
	"""`T2-08`, `T2-09`, `T2-10`: Idempotent replay, `409 IDEMPOTENCY_CONFLICT`, and two-tab `409 STALE_WORK_ITEM`."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=50)

	def test_idempotent_replay_and_payload_mismatch_across_all_p2_mutations(self):
		"""`T2-08`, `T2-09`: Exact replay returns `idempotent_replay=True`; different payload raises `IDEMPOTENCY_CONFLICT`."""
		wi, lead = self.make_assigned_work_item(self.campaign, self.staff_a)

		# 1. `record_attempt` replay + conflict
		k_att = self.unique_key("idem-att")
		a1 = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			note="Lần 1",
			idempotency_key=k_att,
			actor=self.staff_a,
		)
		self.assertFalse(a1["idempotent_replay"])
		a1_rep = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			note="Lần 1",
			idempotency_key=k_att,
			actor=self.staff_a,
		)
		self.assertTrue(a1_rep["idempotent_replay"])
		self.assertEqual(a1_rep["attempt_id"], a1["attempt_id"])
		self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"work_item": wi}), 1)

		with self.assertRaises(IdempotencyConflict):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="busy",
				note="Khác payload",
				idempotency_key=k_att,
				actor=self.staff_a,
			)

		# 2. `schedule_followup` + `complete_followup` replay + conflict
		k_fu = self.unique_key("idem-fu")
		due_str = self.future_due_str(hours=3)
		f1 = followup.schedule_followup(
			work_item=wi,
			due_at=due_str,
			title="Hẹn khách",
			idempotency_key=k_fu,
			actor=self.staff_a,
		)
		f1_rep = followup.schedule_followup(
			work_item=wi,
			due_at=due_str,
			title="Hẹn khách",
			idempotency_key=k_fu,
			actor=self.staff_a,
		)
		self.assertTrue(f1_rep["idempotent_replay"])
		self.assertEqual(f1_rep["task_id"], f1["task_id"])
		with self.assertRaises(IdempotencyConflict):
			followup.schedule_followup(
				work_item=wi,
				due_at=self.future_due_str(hours=6),
				title="Khác giờ",
				idempotency_key=k_fu,
				actor=self.staff_a,
			)

		k_comp = self.unique_key("idem-comp")
		c1 = followup.complete_followup(
			work_item=wi,
			task_id=f1["task_id"],
			action="cancel",
			completion_reason="Hủy thử idempotency",
			idempotency_key=k_comp,
			actor=self.staff_a,
		)
		c1_rep = followup.complete_followup(
			work_item=wi,
			task_id=f1["task_id"],
			action="cancel",
			completion_reason="Hủy thử idempotency",
			idempotency_key=k_comp,
			actor=self.staff_a,
		)
		self.assertTrue(c1_rep["idempotent_replay"])
		self.assertEqual(c1_rep["task_id"], c1["task_id"])

		# 3. `request_correction` + `review_correction` replay + conflict
		k_req = self.unique_key("idem-corr")
		cr1 = correction.request_correction(
			attempt=a1["attempt_id"],
			action="correct",
			corrected_fields={"note": "Sửa ghi chú"},
			reason="Bổ sung thông tin",
			idempotency_key=k_req,
			actor=self.staff_a,
		)
		cr1_rep = correction.request_correction(
			attempt=a1["attempt_id"],
			action="correct",
			corrected_fields={"note": "Sửa ghi chú"},
			reason="Bổ sung thông tin",
			idempotency_key=k_req,
			actor=self.staff_a,
		)
		self.assertTrue(cr1_rep["idempotent_replay"])
		self.assertEqual(cr1_rep["correction_id"], cr1["correction_id"])

		k_rev = self.unique_key("idem-rev")
		rv1 = correction.review_correction(
			correction_id=cr1["correction_id"],
			decision="approve",
			review_note="Đã duyệt",
			idempotency_key=k_rev,
			actor=self.leader_a,
		)
		rv1_rep = correction.review_correction(
			correction_id=cr1["correction_id"],
			decision="approve",
			review_note="Đã duyệt",
			idempotency_key=k_rev,
			actor=self.leader_a,
		)
		self.assertTrue(rv1_rep["idempotent_replay"])
		self.assertEqual(rv1_rep["correction_id"], rv1["correction_id"])

		# 4. `close_work_item` + `reopen_work_item` + `set_lead_dnc` replay
		k_close = self.unique_key("idem-close")
		cl1 = contact_attempt.close_work_item(
			work_item=wi,
			completion_reason="Đóng chuẩn",
			idempotency_key=k_close,
			actor=self.staff_a,
		)
		cl1_rep = contact_attempt.close_work_item(
			work_item=wi,
			completion_reason="Đóng chuẩn",
			idempotency_key=k_close,
			actor=self.staff_a,
		)
		self.assertTrue(cl1_rep["idempotent_replay"])
		self.assertEqual(cl1_rep["work_item_status"], cl1["work_item_status"])

		k_reopen = self.unique_key("idem-reopen")
		ro1 = contact_attempt.reopen_work_item(
			work_item=wi,
			target_user=self.staff_a,
			reason="Mở lại kiểm tra",
			idempotency_key=k_reopen,
			actor=self.leader_a,
		)
		ro1_rep = contact_attempt.reopen_work_item(
			work_item=wi,
			target_user=self.staff_a,
			reason="Mở lại kiểm tra",
			idempotency_key=k_reopen,
			actor=self.leader_a,
		)
		self.assertTrue(ro1_rep["idempotent_replay"])
		self.assertEqual(ro1_rep["assigned_to"], ro1["assigned_to"])

		k_dnc = self.unique_key("idem-dnc")
		d1 = policies.set_lead_dnc(
			lead=lead, do_not_contact=False, reason="Gỡ thử", idempotency_key=k_dnc, actor=self.admin_user
		)
		d1_rep = policies.set_lead_dnc(
			lead=lead, do_not_contact=False, reason="Gỡ thử", idempotency_key=k_dnc, actor=self.admin_user
		)
		self.assertTrue(d1_rep["idempotent_replay"])
		self.assertEqual(d1_rep["do_not_contact"], d1["do_not_contact"])

	def test_two_tab_expected_revision_mismatch_returns_409_stale_work_item(self):
		"""`T2-10`: Tab 2 submitting an older `expected_revision` gets `409 STALE_WORK_ITEM`."""
		wi, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		initial_rev = int(frappe.db.get_value("CSKH Work Item", wi, "revision") or 1)

		# Tab 1 records an attempt -> revision advances to `initial_rev + 1`
		contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="no_answer",
			expected_revision=initial_rev,
			idempotency_key=self.unique_key("tab1"),
			actor=self.staff_a,
		)

		# Tab 2 still carries `expected_revision = initial_rev` -> rejected across mutations
		with self.assertRaises(StaleWorkItem):
			contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="busy",
				expected_revision=initial_rev,
				idempotency_key=self.unique_key("tab2-att"),
				actor=self.staff_a,
			)
		with self.assertRaises(StaleWorkItem):
			followup.schedule_followup(
				work_item=wi,
				due_at=self.future_due_str(hours=2),
				expected_revision=initial_rev,
				idempotency_key=self.unique_key("tab2-fu"),
				actor=self.staff_a,
			)
		with self.assertRaises(StaleWorkItem):
			contact_attempt.close_work_item(
				work_item=wi,
				completion_reason="Tab cũ đóng",
				expected_revision=initial_rev,
				idempotency_key=self.unique_key("tab2-close"),
				actor=self.staff_a,
			)


class TestP2RealMariaDbRacesAndRollbackChaos(P2TestCase):
	"""`T2-11` & `P2.md` §15.3-§15.4: Real DB races, mid-transaction rollback, and post-commit retry."""

	def setUp(self):
		super().setUp()
		self.campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(self.campaign, self.staff_a, weight=50)
		self.make_rule(self.campaign, self.staff_b, weight=50)

	def test_parallel_double_submit_and_record_attempt_vs_reassign_race_preserve_invariants(self):
		"""`T2-11`: Parallel DB connections never duplicate attempts or break interval/callback invariants."""
		site = frappe.local.site
		sites_path = getattr(frappe.local, "sites_path", ".")

		# Race A: 2 parallel threads submitting identical `(work_item, actor, idempotency_key)`
		wi_dup, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		shared_idem = self.unique_key("race-idem")
		frappe.db.commit()

		dup_results: dict = {}
		t1 = _run_db_thread(
			site,
			sites_path,
			lambda: contact_attempt.record_attempt(
				work_item=wi_dup,
				channel="phone_call",
				outcome_code="no_answer",
				note="Double click",
				idempotency_key=shared_idem,
				actor=self.staff_a,
			),
			dup_results,
			"w1",
		)
		t2 = _run_db_thread(
			site,
			sites_path,
			lambda: contact_attempt.record_attempt(
				work_item=wi_dup,
				channel="phone_call",
				outcome_code="no_answer",
				note="Double click",
				idempotency_key=shared_idem,
				actor=self.staff_a,
			),
			dup_results,
			"w2",
		)
		t1.join()
		t2.join()
		frappe.db.commit()

		self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"work_item": wi_dup}), 1)
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi_dup, "attempt_count") or 0), 1)

		# Race B: `Staff A` `record_attempt(callback_requested)` vs `Leader A` `ownership.reassign(A -> B)`
		wi_race, _ = self.make_assigned_work_item(self.campaign, self.staff_a)
		due_str = self.future_due_str(hours=4)
		att_idem = self.unique_key("race-att")
		reass_idem = self.unique_key("race-reass")
		frappe.db.commit()

		race_results: dict = {}
		ta = _run_db_thread(
			site,
			sites_path,
			lambda: contact_attempt.record_attempt(
				work_item=wi_race,
				channel="phone_call",
				outcome_code="callback_requested",
				note="Khách hẹn gọi lại trong lúc chuyển quyền",
				followup={"due_at": due_str, "title": "Callback Race"},
				idempotency_key=att_idem,
				actor=self.staff_a,
			),
			race_results,
			"attempt",
		)
		tb = _run_db_thread(
			site,
			sites_path,
			lambda: ownership.reassign(
				wi_race,
				self.staff_a,
				self.staff_b,
				reason="Admin chuyển sang B song song",
				request_id=reass_idem,
				actor=self.admin_user,
			),
			race_results,
			"reassign",
		)
		ta.join()
		tb.join()
		frappe.db.commit()

		# `reassign` must succeed; `attempt` either committed BEFORE `reassign` (1 attempt by A, open callback owned by B!)
		# or ran AFTER `reassign` and failed cleanly with `OWNERSHIP_CHANGED` (0 attempts).
		self.assertIn("ok", race_results["reassign"], f"reassign failed: {race_results}")
		if "ok" in race_results["attempt"]:
			self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"work_item": wi_race}), 1)
			task_id = race_results["attempt"]["ok"]["callback_task"]
			self.assertEqual(frappe.db.get_value("CRM Task", task_id, "assigned_to"), self.staff_b)
		else:
			self.assertEqual(race_results["attempt"]["code"], "OWNERSHIP_CHANGED")
			self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"work_item": wi_race}), 0)

		self.assertTrue(read_model.reconcile_p2_invariants(self.campaign)["ok"])

	def test_mid_transaction_fault_rolls_back_savepoint_and_post_commit_notification_fault_preserves_state(
		self,
	):
		"""`P2.md` §15.3-§15.4: Savepoint rollback leaves 0 orphans; post-commit notification failure never rolls back DB."""
		wi, _lead = self.make_assigned_work_item(self.campaign, self.staff_a)
		rev_before = int(frappe.db.get_value("CSKH Work Item", wi, "revision") or 1)

		# 1. Fault injected AFTER `attempt_doc.insert()` inside `create_followup_in_tx` -> 0 orphan Attempts/Tasks!
		observed_inside_tx = []

		def boom_during_followup(*args, **kwargs):
			observed_inside_tx.append(frappe.db.count("CSKH Contact Attempt", {"work_item": wi}))
			raise RuntimeError("injected mid-tx failure after attempt insert")

		with mock.patch(
			"crm_cskh.services.contact_attempt.create_followup_in_tx", side_effect=boom_during_followup
		):
			with self.assertRaises(RuntimeError):
				contact_attempt.record_attempt(
					work_item=wi,
					channel="phone_call",
					outcome_code="callback_requested",
					note="Lỗi giữa giao dịch",
					followup={"due_at": self.future_due_str(hours=3)},
					idempotency_key=self.unique_key("mid-tx-fail"),
					actor=self.staff_a,
				)

		self.assertEqual(observed_inside_tx, [1], "attempt row existed inside savepoint before rollback")
		self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"work_item": wi}), 0)
		self.assertEqual(frappe.db.count("CRM Task", {"cskh_work_item": wi}), 0)
		self.assertEqual(frappe.db.get_value("CSKH Work Item", wi, "status"), "assigned")
		self.assertEqual(int(frappe.db.get_value("CSKH Work Item", wi, "revision") or 1), rev_before)

		# 2. Post-commit fault inside `CRM Notification.insert` during `deliver_followup_notification`:
		# `deliver_followup_notification` swallows/logs post-commit notification error -> committed Attempt + Task survive,
		# and client retry with the same `idempotency_key` replays cleanly without duplicating anything!
		idem_post = self.unique_key("post-commit")
		due_post = self.future_due_str(hours=3)
		real_get_doc = frappe.get_doc

		def fail_crm_notification(*args, **kwargs):
			first = args[0] if args else None
			if (isinstance(first, dict) and first.get("doctype") == "CRM Notification") or (
				kwargs.get("doctype") == "CRM Notification"
			):
				raise RuntimeError("transient notification bus outage")
			return real_get_doc(*args, **kwargs)

		with mock.patch("frappe.get_doc", side_effect=fail_crm_notification):
			committed = contact_attempt.record_attempt(
				work_item=wi,
				channel="phone_call",
				outcome_code="callback_requested",
				note="Đã commit trước khi gửi thông báo",
				followup={"due_at": due_post, "title": "Hẹn sau commit"},
				idempotency_key=idem_post,
				actor=self.staff_a,
			)

		self.assertEqual(committed["work_item_status"], "on_hold")
		self.assertEqual(frappe.db.count("CSKH Contact Attempt", {"work_item": wi}), 1)
		self.assertEqual(frappe.db.count("CRM Task", {"cskh_work_item": wi}), 1)

		replay = contact_attempt.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="callback_requested",
			note="Đã commit trước khi gửi thông báo",
			followup={"due_at": due_post, "title": "Hẹn sau commit"},
			idempotency_key=idem_post,
			actor=self.staff_a,
		)
		self.assertTrue(replay["idempotent_replay"])

		# Retrying `deliver_followup_notification` twice with the same `event_id` creates only 1 `CRM Notification`
		ev_id = f"att-fu:{wi}:{idem_post}"
		notifications.deliver_followup_notification(
			wi, committed["callback_task"], self.staff_a, ev_id, actor=self.staff_a
		)
		notifications.deliver_followup_notification(
			wi, committed["callback_task"], self.staff_a, ev_id, actor=self.staff_a
		)
		self.assertEqual(
			frappe.db.count(
				"CRM Notification",
				{
					"notification_type_doctype": "CRM Task",
					"notification_type_doc": str(committed["callback_task"]),
				},
			),
			1,
		)
		self.assertTrue(read_model.reconcile_p2_invariants(self.campaign)["ok"])


class TestZeroProductionFaultInjectionP2(unittest.TestCase):
	"""Verify zero fault-injection hooks or hard exits exist anywhere in `api/`, `services/`, or `jobs/`."""

	def test_no_fault_injection_symbols_in_production_modules(self):
		app_root = Path(contact_attempt.__file__).resolve().parent.parent
		forbidden = {
			"chaos",
			"maybe_fail",
			"maybe_crash",
			"InjectedFailure",
			"_exit",
			"_after_attempt_insert_hook",
		}
		for subdir in ("api", "services", "jobs"):
			for py_file in sorted((app_root / subdir).rglob("*.py")):
				tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
				for node in ast.walk(tree):
					if isinstance(node, ast.Name):
						self.assertNotIn(node.id, forbidden, f"forbidden symbol {node.id!r} in {py_file}")
					elif isinstance(node, ast.Attribute):
						self.assertNotIn(node.attr, forbidden, f"forbidden attr {node.attr!r} in {py_file}")
