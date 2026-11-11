"""P4 backend regression suite closing `S13`/`S14`, `role-change`, `disabled-user` allocation exclusion,
and `G18` bulk SQL / KPI drill-down optimization parity (`P4.md` §§9.2, 13.2).
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import add_days, now_datetime

from crm_cskh import permissions
from crm_cskh.api import campaigns as campaigns_api
from crm_cskh.api import p3_reporting as p3_api
from crm_cskh.api import workflow as workflow_api
from crm_cskh.jobs import distribution_worker
from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services import contact_attempt, distribution, eligibility, import_service, ownership
from crm_cskh.services.errors import Conflict, Forbidden, InvalidRequest
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import execute_kpi_recompute
from crm_cskh.tests.p1_base import P1TestCase


class TestP4SecurityAndScaleGaps(P1TestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		ensure_p2_indexes()
		ensure_p2_seeds()
		ensure_p3_indexes()
		ensure_p3_seeds()
		self._prev_hier = frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", self._prev_hier or 1)
		frappe.db.set_value("User", self.staff_a, "enabled", 1)
		frappe.db.set_value("User", self.staff_b, "enabled", 1)
		frappe.db.set_value("User", self.staff_c, "enabled", 1)
		ldr_doc = frappe.get_doc("User", self.leader_a)
		cur_roles = {r.role for r in ldr_doc.roles}
		for req_role in ("Sales Manager", "Sales User"):
			if req_role not in cur_roles:
				ldr_doc.add_roles(req_role)
		frappe.clear_cache(user=self.leader_a)
		frappe.db.commit()
		super().tearDown()

	def _make_child_records(self, lead_name: str) -> dict[str, str]:
		call_name = (
			frappe.get_doc(
				{
					"doctype": "CRM Call Log",
					"id": f"P4-CL-{frappe.generate_hash(length=10)}",
					"from": "+842873009901",
					"to": frappe.db.get_value("CRM Lead", lead_name, "mobile_no") or "0909111222",
					"type": "Outgoing",
					"status": "Completed",
					"duration": 35,
					"start_time": now_datetime() - timedelta(minutes=3),
					"end_time": now_datetime() - timedelta(minutes=2),
					"caller": self.staff_a,
					"telephony_medium": "Manual",
					"reference_doctype": "CRM Lead",
					"reference_docname": lead_name,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		note_name = (
			frappe.get_doc(
				{
					"doctype": "FCRM Note",
					"title": "Ghi chú P4 S13",
					"content": "Nội dung nội bộ P4 S13",
					"reference_doctype": "CRM Lead",
					"reference_docname": lead_name,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		file_name = (
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"p4-s13-{lead_name}.txt",
					"is_private": 1,
					"attached_to_doctype": "CRM Lead",
					"attached_to_name": lead_name,
					"content": f"private payload {lead_name}",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		return {"call_log": call_name, "note": note_name, "file": file_name}

	def test_role_downgrade_revokes_hierarchy_subtree_without_manual_clear_cache(self):
		"""`role-change`: Removing `Sales Manager` from a group hierarchy node owner immediately revokes subordinate access."""
		frappe.set_user("Administrator")
		campaign = self.make_campaign(team_scope=self.team_a)
		lead_a = self.make_canonical_lead()
		children = self._make_child_records(lead_a)
		wi_a = self.make_work_item(campaign, lead_a)
		frappe.set_user(self.admin_user)
		ownership.assign(wi_a, self.staff_a, actor=self.admin_user, request_id=self.unique_key("rc-asgn"))

		# Before role downgrade: `leader_a` (`Sales Manager` + `Sales User`) can read subordinate `lead_a` & children.
		frappe.set_user(self.leader_a)
		self.assertTrue(permissions.user_in_scope(self.leader_a, self.staff_a))
		self.assertTrue(frappe.has_permission("CRM Lead", "read", lead_a, throw=False))
		self.assertIn(lead_a, frappe.get_list("CRM Lead", filters={"name": lead_a}, pluck="name"))

		# Downgrade `leader_a` via `User.save()` (keeping `CRM Sales Hierarchy` group node `self.team_a` intact!).
		frappe.set_user("Administrator")
		ldr_doc = frappe.get_doc("User", self.leader_a)
		ldr_doc.roles = [r for r in ldr_doc.roles if r.role != "Sales Manager"]
		ldr_doc.save(ignore_permissions=True)

		# Without manual `frappe.clear_cache()`, `on_user_security_change` + `permissions.py` revoke subtree access:
		frappe.set_user(self.leader_a)
		self.assertFalse(permissions.user_in_scope(self.leader_a, self.staff_a))
		self.assertFalse(frappe.has_permission("CRM Lead", "read", lead_a, throw=False))
		self.assertEqual(frappe.get_list("CRM Lead", filters={"name": lead_a}, pluck="name"), [])
		for dt, docname in (
			("CRM Lead", lead_a),
			("CSKH Work Item", wi_a),
			("CRM Call Log", children["call_log"]),
			("FCRM Note", children["note"]),
			("File", children["file"]),
		):
			with self.subTest(doctype=dt), self.assertRaises(frappe.PermissionError):
				frappe.get_doc(dt, docname).check_permission("read")
		with self.assertRaises(Forbidden):
			workflow_api.get_work_item(work_item=wi_a)

	def test_s13_and_s14_handoff_a_to_b_to_a_denies_old_ids_and_passes_reconciliation(self):
		"""`P4-S13` & `P4-S14`: `A -> B` denies old record IDs; `A -> B -> A` purges duplicate ToDos & child DocShares."""
		frappe.set_user("Administrator")
		campaign = self.make_campaign(team_scope=self.team_a)
		lead = self.make_canonical_lead()
		children = self._make_child_records(lead)
		wi = self.make_work_item(campaign, lead)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead,
				"campaign": campaign,
				"work_item": wi,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.admin_user)
		ownership.assign(wi, self.staff_a, actor=self.admin_user, request_id=self.unique_key("s13-a1"))

		due_str = add_days(now_datetime(), 1).strftime("%Y-%m-%d 10:00:00")
		frappe.set_user(self.staff_a)
		att = workflow_api.record_attempt(
			work_item=wi,
			channel="phone_call",
			outcome_code="connected_interested",
			call_log=children["call_log"],
			note="Trao đổi P4 S13",
			followup_due_at=due_str,
			followup_title="Hẹn gọi lại S13",
			idempotency_key=self.unique_key("s13-att1"),
		)
		attempt_id = att["attempt_id"]
		task_id = att["callback_task"]
		todo_a_old = frappe.db.get_value(
			"ToDo",
			{
				"reference_type": "CRM Lead",
				"reference_name": lead,
				"allocated_to": self.staff_a,
				"status": "Open",
			},
			"name",
		)
		self.assertTrue(todo_a_old)

		# Inject an extra duplicate `Open` ToDo for `staff_a` AND stray child `DocShare`s to prove `S14` deep cleanup:
		frappe.set_user("Administrator")
		extra_todo = (
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"allocated_to": self.staff_a,
					"reference_type": "CRM Lead",
					"reference_name": lead,
					"description": "Duplicate stale ToDo",
					"status": "Open",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		for child_dt, child_name in (
			("CRM Call Log", children["call_log"]),
			("CRM Task", task_id),
			("FCRM Note", children["note"]),
			("File", children["file"]),
		):
			frappe.share.add_docshare(
				child_dt,
				child_name,
				self.staff_a,
				read=1,
				flags={"ignore_share_permission": True},
			)

		# Step 1 (`S13`): Handoff `A -> B` -> `staff_a` MUST lose access to EVERY record ID!
		frappe.set_user(self.admin_user)
		ownership.reassign(
			wi,
			expected_owner=self.staff_a,
			target_user=self.staff_b,
			actor=self.admin_user,
			reason="Chuyển A sang B kiểm định S13",
			request_id=self.unique_key("s13-a2b"),
		)

		frappe.set_user(self.staff_a)
		with self.assertRaises(Forbidden):
			workflow_api.get_work_item(work_item=wi)
		with self.assertRaises(Forbidden):
			workflow_api.list_attempts(work_item=wi)
		for dt, old_id in (
			("CRM Lead", lead),
			("CSKH Work Item", wi),
			("CSKH Contact Attempt", attempt_id),
			("CRM Call Log", children["call_log"]),
			("CRM Task", task_id),
			("FCRM Note", children["note"]),
			("File", children["file"]),
			("ToDo", todo_a_old),
			("ToDo", extra_todo),
		):
			with self.subTest(step="after_A_to_B", doctype=dt), self.assertRaises(frappe.PermissionError):
				frappe.get_doc(dt, old_id).check_permission("read")

		# Step 2 (`S14`): Handoff `B -> A` (`A -> B -> A`) -> zero residual shares/ToDos for `B` & PASS reconciliation!
		frappe.set_user("Administrator")
		frappe.share.add_docshare(
			"CRM Task", task_id, self.staff_b, read=1, flags={"ignore_share_permission": True}
		)
		frappe.set_user(self.admin_user)
		ownership.reassign(
			wi,
			expected_owner=self.staff_b,
			target_user=self.staff_a,
			actor=self.admin_user,
			reason="Chuyển trả B về A kiểm định S14",
			request_id=self.unique_key("s14-b2a"),
		)

		frappe.set_user("Administrator")
		lead_shares = frappe.get_all(
			"DocShare",
			filters={"share_doctype": "CRM Lead", "share_name": lead},
			pluck="user",
		)
		self.assertEqual(lead_shares, [self.staff_a])
		child_shares = frappe.get_all(
			"DocShare",
			filters={
				"share_doctype": ["in", ["CRM Call Log", "CRM Task", "FCRM Note", "File"]],
				"share_name": ["in", [children["call_log"], task_id, children["note"], children["file"]]],
				"user": ["!=", self.staff_a],
			},
			pluck="name",
		)
		self.assertEqual(child_shares, [])
		open_todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "CRM Lead", "reference_name": lead, "status": "Open"},
			pluck="allocated_to",
		)
		self.assertEqual(open_todos, [self.staff_a])

		rec = distribution.reconcile(campaign, actor=self.admin_user)
		self.assertEqual(rec["stale_docshares"], 0)
		self.assertEqual(rec["stale_open_todos"], 0)
		self.assertTrue(rec["ownership_reconciled"])

	def test_disabled_user_session_purge_and_allocation_exclusion_all_paths(self):
		"""`disabled-user`: Disabling a user purges sessions and blocks/excludes allocation across all paths."""
		frappe.set_user("Administrator")
		campaign = self.make_campaign(team_scope=self.team_a)
		self.make_rule(campaign, self.staff_a, weight=60)
		self.make_rule(campaign, self.staff_b, weight=40)
		leads = [self.make_canonical_lead() for _ in range(4)]
		frappe.db.set_value("CSKH Campaign", campaign, "selection_json", frappe.as_json({"leads": leads}))

		# Insert synthetic session row for `staff_a` and verify `User.save(enabled=0)` purges it automatically
		fake_sid = f"p4testsid{frappe.generate_hash(length=24)}"
		frappe.db.sql(
			"""insert into `tabSessions` (`user`, `sid`, `sessiondata`, `ipaddress`, `lastupdate`, `status`)
			   values (%s, %s, '{}', '127.0.0.1', %s, 'Active')""",
			(self.staff_a, fake_sid, now_datetime()),
		)
		self.assertEqual(
			int(frappe.db.sql("select count(*) from `tabSessions` where sid = %s", (fake_sid,))[0][0]),
			1,
		)

		prev = distribution.preview(
			campaign, actor=self.admin_user, idempotency_key=self.unique_key("dis-prev1")
		)

		# Disable `staff_a` via `User.save()` AFTER preview!
		u_a = frappe.get_doc("User", self.staff_a)
		u_a.enabled = 0
		u_a.save(ignore_permissions=True)

		self.assertEqual(
			int(frappe.db.sql("select count(*) from `tabSessions` where sid = %s", (fake_sid,))[0][0]),
			0,
		)

		# 1. If `staff_b` is ALSO disabled after preview, `enqueue_run` MUST reject with `409 CAPACITY_FULL`!
		frappe.db.set_value("User", self.staff_b, "enabled", 0)
		with self.assertRaises(Conflict) as cap_ctx:
			distribution.enqueue_run(prev["preview_id"], actor=self.admin_user, enqueue_job=False)
		self.assertEqual(cap_ctx.exception.code, "CAPACITY_FULL")

		# 2. Re-enable `staff_b` while `staff_a` stays disabled: `staff_a` gets `0` items and is listed in `excluded_assignees`!
		frappe.db.set_value("User", self.staff_b, "enabled", 1)
		with mock.patch("frappe.enqueue"):
			enq = distribution.enqueue_run(prev["preview_id"], actor=self.admin_user, enqueue_job=True)
		frappe.db.commit()
		try:
			done = distribution_worker.run_distribution(enq["run_id"], actor=self.admin_user)
			self.assertEqual(done["status"], "completed")
			self.assertEqual(done["assigned"].get(self.staff_a, 0), 0)
			self.assertEqual(done["assigned"].get(self.staff_b, 0), 4)
			excl_reasons = {e["user"]: e["reason"] for e in done["excluded_assignees"]}
			self.assertEqual(excl_reasons.get(self.staff_a), "user_disabled")

			# 3. Manual `assign`, `reassign`, and `reopen_work_item` all reject disabled `staff_a`!
			extra_lead = self.make_canonical_lead()
			extra_wi = self.make_work_item(campaign, extra_lead)
			with self.assertRaises(InvalidRequest):
				ownership.assign(
					extra_wi, self.staff_a, actor=self.admin_user, request_id=self.unique_key("dis-asgn")
				)

			assigned_wi = frappe.get_all(
				"CSKH Work Item", filters={"campaign": campaign, "assigned_to": self.staff_b}, pluck="name"
			)[0]
			with self.assertRaises(InvalidRequest):
				ownership.reassign(
					assigned_wi,
					expected_owner=self.staff_b,
					target_user=self.staff_a,
					actor=self.admin_user,
					reason="test disabled target",
					request_id=self.unique_key("dis-reasgn"),
				)
			frappe.set_user(self.staff_b)
			contact_attempt.close_work_item(
				work_item=assigned_wi,
				completion_reason="Đóng hồ sơ dữ liệu trùng kiểm thử",
				close_category="invalid_or_duplicate",
				idempotency_key=self.unique_key("dis-close"),
				actor=self.staff_b,
			)
			frappe.set_user(self.admin_user)
			with self.assertRaises(InvalidRequest):
				contact_attempt.reopen_work_item(
					work_item=assigned_wi,
					target_user=self.staff_a,
					reason="Thử mở lại cho user bị khóa",
					idempotency_key=self.unique_key("dis-reopen"),
					actor=self.admin_user,
				)
		finally:
			frappe.set_user("Administrator")
			w_ids = frappe.get_all("CSKH Work Item", filters={"campaign": campaign}, pluck="name")
			if w_ids:
				frappe.db.delete("CSKH SLA Obligation", {"work_item": ["in", w_ids]})
				frappe.db.delete("CSKH Assignment History", {"work_item": ["in", w_ids]})
			frappe.db.delete("CSKH Active Claim", {"campaign": campaign})
			frappe.db.delete("CSKH Work Item", {"campaign": campaign})
			frappe.db.delete("CSKH Distribution Run", {"campaign": campaign})
			frappe.db.delete("CSKH Assignment Rule", {"campaign": campaign})
			frappe.db.delete("CSKH Campaign", {"name": campaign})
			all_l = [*leads]
			frappe.db.delete("ToDo", {"reference_type": "CRM Lead", "reference_name": ["in", all_l]})
			frappe.db.delete("DocShare", {"share_doctype": "CRM Lead", "share_name": ["in", all_l]})
			frappe.db.delete("CSKH Identity Index", {"canonical_lead": ["in", all_l]})
			frappe.db.delete("CRM Lead", {"name": ["in", all_l]})
			frappe.db.commit()

	def test_bulk_eligibility_import_and_kpi_drilldown_index_parity(self):
		"""`G18`: Bulk eligibility (>50), bulk CSV classify (>50), and SQL-aggregated drill-down match single-row oracles."""
		frappe.set_user("Administrator")
		campaign = self.make_campaign(team_scope=self.team_a)
		leads_55 = [self.make_canonical_lead() for _ in range(55)]
		# Inject exclusions: 1 DNC, 1 existing WI in campaign, 1 active claim in other campaign
		frappe.db.set_value("CRM Lead", leads_55[0], "cskh_do_not_contact", 1)
		self.make_work_item(campaign, leads_55[1])
		other_camp = self.make_campaign(team_scope=self.team_b)
		other_wi = self.make_work_item(other_camp, leads_55[2], status="assigned", assigned_to=self.staff_c)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": leads_55[2],
				"campaign": other_camp,
				"work_item": other_wi,
			}
		).insert(ignore_permissions=True)

		frappe.db.set_value("CSKH Campaign", campaign, "selection_json", frappe.as_json({"leads": leads_55}))
		bulk_res = eligibility.eligible_leads(campaign)
		expected_excl = [
			{"lead": ld, "reason": r}
			for ld in leads_55
			if (r := eligibility._lead_exclusion_reason(campaign, ld))
		]
		self.assertEqual(bulk_res["excluded"], expected_excl)
		self.assertEqual(len(bulk_res["eligible"]), 52)

		# Bulk CSV `_classify` (>50 rows) parity check
		existing_ph = frappe.db.get_value(
			"CSKH Identity Index", {"canonical_lead": leads_55[3]}, "normalized_phone"
		)
		rows_60 = [{"row_number": 2, "cells": {"phone": existing_ph, "name": "Dup CRM"}}]
		for idx in range(55):
			rows_60.append(
				{"row_number": idx + 3, "cells": {"phone": f"09678{idx:05d}", "name": f"New {idx}"}}
			)
		rows_60.append({"row_number": 58, "cells": {"phone": "0967800001", "name": "Dup File"}})
		rows_60.append({"row_number": 59, "cells": {"phone": "INVALID", "name": "Bad"}})
		cls_res = import_service._classify(
			rows_60, {"phone": "phone", "name": "name"}, "phone", name_column="name"
		)
		self.assertEqual(cls_res["total_rows"], 58)
		self.assertEqual(cls_res["new_rows"], 55)
		self.assertEqual(cls_res["duplicate_rows"], 2)
		self.assertEqual(cls_res["error_rows"], 1)

		# KPI Snapshot index usage & drill-down reconciliation check
		wi_kpi = self.make_work_item(campaign, leads_55[4])
		frappe.set_user(self.admin_user)
		ownership.assign(wi_kpi, self.staff_a, actor=self.admin_user, request_id=self.unique_key("kpi-asgn"))
		frappe.set_user(self.staff_a)
		workflow_api.record_attempt(
			work_item=wi_kpi,
			channel="phone_call",
			outcome_code="connected_interested",
			note="KPI drilldown check",
			idempotency_key=self.unique_key("kpi-att"),
		)
		frappe.set_user("Administrator")
		now_dt = now_datetime()
		m_key = f"{now_dt.year:04d}-{now_dt.month:02d}"
		execute_kpi_recompute(resolve_period_spec("monthly", m_key), run_mode="full", actor="Administrator")

		plan = frappe.db.sql(
			"""EXPLAIN SELECT name, metrics_json FROM `tabCSKH KPI Snapshot` USE INDEX (idx_cskh_kpisnap_lookup)
			   WHERE is_latest_published = 1 AND period_type = 'monthly'
			     AND period_key = %s AND campaign_filter = '' AND scope_type = 'company' AND scope_id = '__company__'""",
			(m_key,),
			as_dict=True,
		)
		self.assertTrue(plan and plan[0].get("key") == "idx_cskh_kpisnap_lookup", plan)

		frappe.set_user(self.bod_user)
		dd = p3_api.get_metric_drilldown(
			metric_code="CONNECTED_RATE",
			period_type="monthly",
			period_key=m_key,
			scope_type="company",
			page=1,
			page_size=50,
		)
		snap_m = dd["drilldown_context"]["snapshot_metric"]
		self.assertIsNotNone(snap_m)
		self.assertEqual(dd["pagination"]["total"], snap_m["denominator"])
		frappe.db.rollback()

	def test_cskh_context_csrf_token_concurrent_session_init_recovery(self):
		"""Verify cskh_context._get_or_init_csrf_token recovers from MariaDB Error 1020 on tabSessions while keeping RBAC/CSRF."""
		from unittest.mock import patch

		from crm_cskh.www import cskh_context

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			cskh_context.build_context(
				frappe._dict(), route="/cskh/my-customers", allowed_roles=cskh_context.READ_ROLES
			)

		frappe.set_user(self.staff_a)
		with self.assertRaises(frappe.PermissionError):
			cskh_context.build_context(
				frappe._dict(),
				route="/cskh/import",
				allowed_roles=cskh_context.IMPORT_ROLES,
				require_admin=True,
			)

		sess_data = getattr(getattr(frappe.local, "session", None), "data", None)
		orig_tok = getattr(sess_data, "csrf_token", None) if sess_data is not None else None
		orig_hget = frappe.cache.hget
		try:
			if sess_data is not None:
				sess_data.csrf_token = None
			expected_tok = "csrf-recovered-from-concurrent-session-update-1020"

			def _fake_hget(name, key, *args, **kwargs):
				if name == "session":
					return {"data": {"csrf_token": expected_tok}}
				return orig_hget(name, key, *args, **kwargs)

			with (
				patch.object(
					frappe.sessions,
					"get_csrf_token",
					side_effect=frappe.QueryDeadlockError(
						"(1020, \"Record has changed since last read in table 'tabSessions'; try restarting transaction\")"
					),
				),
				patch.object(
					frappe.cache,
					"hget",
					side_effect=_fake_hget,
				),
			):
				ctx = cskh_context.build_context(
					frappe._dict(),
					route="/cskh/my-customers",
					allowed_roles=cskh_context.READ_ROLES,
				)
				self.assertEqual(ctx.csrf_token, expected_tok)
				self.assertEqual(ctx.cskh_csrf_header, {"X-Frappe-CSRF-Token": expected_tok})
				if sess_data is not None:
					self.assertEqual(sess_data.csrf_token, expected_tok)
		finally:
			if sess_data is not None:
				sess_data.csrf_token = orig_tok
			frappe.set_user("Administrator")
