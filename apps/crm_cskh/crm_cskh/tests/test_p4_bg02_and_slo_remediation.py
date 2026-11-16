"""P4 `BG-02` streaming/batched CSV import, `record_attempt` SQL budget, and `runtime_cache`
RBAC scope isolation & post-write freshness verification (`P4.md` §§9.2, 13.2, `G18`).
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from unittest import mock

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime, set_request

from crm_cskh.api import campaigns as campaigns_api
from crm_cskh.api import settings as settings_api
from crm_cskh.jobs import import_worker
from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import authorized_sla_write, ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services import (
	contact_attempt,
	correction,
	import_service,
	kpi_runs,
	ownership,
	read_model,
	report_query,
	runtime_cache,
)
from crm_cskh.services.errors import Forbidden, ForbiddenScope
from crm_cskh.services.kpi_periods import resolve_period_spec, today_hcm
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.tests.p1_base import P1TestCase
from crm_cskh.www import cskh_context
from crm_cskh.www.cskh_renderer import CskhPageRenderer


class TestP4Bg02AndSloRemediation(P1TestCase):
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
		frappe.local.request = None
		frappe.set_user("Administrator")
		runtime_cache.clear_all_local_caches()
		frappe.db.rollback()
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", self._prev_hier or 1)
		settings_api.save_policy(
			policy_code="GLOBAL-DEFAULT",
			title="Chính sách Vận hành Liên hệ & Tuyển dụng Mặc định",
			scope_type="Global",
			enabled=1,
			p3_sla_enabled=1,
			p3_reporting_enabled=1,
			qa_enabled=0,
			idempotency_key=self.unique_key("restore-pol"),
		)
		frappe.db.commit()
		super().tearDown()

	def _upload_private_csv(self, filename: str, lines: list[str]) -> str:
		raw = ("\n".join(lines) + "\n").encode("utf-8")
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": filename,
				"is_private": 1,
				"content": raw,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name

	def _assign_new_work_item(self, campaign: str, target_user: str, lead: str | None = None) -> str:
		lead_name = lead or self.make_canonical_lead()
		if not frappe.db.exists("CSKH Assignment Rule", {"campaign": campaign, "user": target_user}):
			self.make_rule(campaign, target_user, 50)
		wi_name = self.make_work_item(campaign, lead_name, status="queued")
		if not frappe.db.exists("CSKH Active Claim", {"lead": lead_name}):
			frappe.get_doc(
				{
					"doctype": "CSKH Active Claim",
					"lead": lead_name,
					"campaign": campaign,
					"work_item": wi_name,
				}
			).insert(ignore_permissions=True)
		ownership.assign(
			wi_name,
			target_user,
			actor=self.admin_user,
			reason="Giao khách kiểm thử P4 G18",
			request_id=self.unique_key("assign"),
		)
		return wi_name

	def test_bg02_streaming_preview_batched_worker_and_phase_telemetry(self):
		"""Verify streaming CSV preview + batched worker writes across >1 chunk of 500, preserving
		existing CRM Lead fields, formula sanitization, per-row errors > row 200, and telemetry."""
		frappe.set_user("Administrator")
		existing_lead = self.make_canonical_lead()
		existing_phone = frappe.db.get_value("CRM Lead", existing_lead, "mobile_no")
		frappe.db.set_value(
			"CRM Lead",
			existing_lead,
			{
				"first_name": "Khách Cũ Giữ Nguyên",
				"lead_owner": self.staff_a,
				"status": "Qualified",
			},
			update_modified=False,
		)
		frappe.db.commit()

		new_phones = [self.unique_phone() for _ in range(520)]
		lines = ["full_name,phone,email,source"]
		# 520 brand-new valid rows (spans >1 batch of 500; row 0 tests formula sanitization)
		for idx, ph in enumerate(new_phones):
			nm = "=CMD|' /C calc'!A0" if idx == 0 else f"Ứng viên Lô {idx:04d}"
			lines.append(f'"{nm}",{ph},u{idx:04d}@example.test, Nguồn BG02 ')
		# 1 existing CRM phone (`duplicate_in_crm`)
		lines.append(f"Tên Mới Không Được Ghi Đè,+84 {existing_phone[1:]},dup@example.test,Nguồn BG02")
		# 10 in-file duplicates (`duplicate_in_file`)
		for idx in range(10):
			lines.append(f"Trùng Trong File {idx},84{new_phones[idx][1:]},fdup{idx}@example.test,Nguồn BG02")
		# 15 invalid rows placed AFTER row 530 (> PREVIEW_ERROR_SAMPLE=200) to prove streaming worker catches all
		for idx in range(15):
			lines.append(f"=Lỗi Công Thức {idx},12345,bad{idx}@example.test,Nguồn BG02")

		file_id = self._upload_private_csv("bg02_streaming_verify.csv", lines)
		prev = import_service.create_preview(
			file_name=file_id,
			mapping={"name": "full_name", "phone": "phone", "source": "source"},
			actor=self.admin_user,
			idempotency_key=self.unique_key("bg02-prev"),
		)
		self.assertEqual(prev["totals"]["total_rows"], 546)
		self.assertEqual(prev["totals"]["valid_rows"], 520)
		self.assertEqual(prev["totals"]["new_rows"], 520)
		self.assertEqual(prev["totals"]["duplicate_rows"], 11)
		self.assertEqual(prev["totals"]["error_rows"], 15)
		ptel = prev.get("preview_telemetry") or {}
		self.assertIn("preview_csv_parse_ms", ptel)
		self.assertIn("preview_db_lookup_ms", ptel)
		self.assertIn("preview_persist_ms", ptel)

		batch_id = prev["batch"]
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch_id, actor=self.admin_user)
		frappe.db.commit()

		import_worker.run_batch(batch_id)
		status_out = import_service._status_response(batch_id)
		self.assertEqual(status_out["status"], "COMPLETED_WITH_ERRORS")
		self.assertEqual(status_out["created_rows"], 520)
		self.assertEqual(status_out["reused_rows"], 1)
		self.assertEqual(status_out["skipped_rows"], 15)
		self.assertTrue(status_out["error_artifact"])

		wtel = status_out.get("worker_telemetry") or {}
		self.assertTrue(wtel.get("worker_batched_mode"))
		self.assertIn("worker_csv_parse_ms", wtel)
		self.assertIn("worker_db_write_ms", wtel)
		self.assertLess(float(wtel.get("worker_sql_queries_per_target") or 99.0), 0.5)

		# Existing CRM lead must NOT have its owner, status, or name overwritten
		ex_after = frappe.db.get_value(
			"CRM Lead", existing_lead, ["first_name", "lead_owner", "status"], as_dict=True
		)
		self.assertEqual(ex_after.first_name, "Khách Cũ Giữ Nguyên")
		self.assertEqual(ex_after.lead_owner, self.staff_a)
		self.assertEqual(ex_after.status, "Qualified")

		# First new lead must have formula injection neutralized
		first_lead = frappe.db.get_value(
			"CSKH Identity Index",
			{"normalized_phone": normalize_vn_phone(new_phones[0])},
			"canonical_lead",
		)
		self.assertTrue(first_lead)
		first_name_val = frappe.db.get_value("CRM Lead", first_lead, "first_name")
		self.assertFalse(str(first_name_val).startswith("="))

		# Error artifact must be private and contain all 15 errors beyond row 530
		err_file = frappe.get_doc("File", status_out["error_artifact"])
		self.assertEqual(int(err_file.is_private or 0), 1)
		raw_err = err_file.get_content()
		err_txt = raw_err.decode("utf-8") if isinstance(raw_err, bytes) else str(raw_err)
		err_rows = list(csv.DictReader(io.StringIO(err_txt)))
		self.assertEqual(len(err_rows), 15)
		self.assertGreater(int(err_rows[0]["row_number"]), 530)

	def test_bg02_mid_batch_retry_idempotency_and_zero_duplicates(self):
		"""Simulate mid-worker interruption after chunk 1 (`500` rows committed), then retry to completion."""
		frappe.set_user("Administrator")
		phones = [self.unique_phone() for _ in range(620)]
		lines = ["full_name,phone"] + [f"Retry Lead {i:04d},{ph}" for i, ph in enumerate(phones)]
		file_id = self._upload_private_csv("bg02_retry_verify.csv", lines)
		prev = import_service.create_preview(
			file_name=file_id,
			mapping={"name": "full_name", "phone": "phone"},
			actor=self.admin_user,
			idempotency_key=self.unique_key("bg02-retry-prev"),
		)
		batch_id = prev["batch"]
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch_id, actor=self.admin_user)
		frappe.db.commit()

		orig_set_val = frappe.db.set_value
		progress_updates = {"n": 0}

		def _interrupt_on_second_progress(dt, dn, field, val=None, *args, **kwargs):
			res = orig_set_val(dt, dn, field, val, *args, **kwargs)
			if dt == "CSKH Import Batch" and field == "progress":
				progress_updates["n"] += 1
				if progress_updates["n"] == 2:
					raise RuntimeError("Simulated worker interruption before chunk 2 commit")
			return res

		with mock.patch.object(frappe.db, "set_value", side_effect=_interrupt_on_second_progress):
			with self.assertRaises(RuntimeError):
				import_worker.run_batch(batch_id)

		mid_batch = frappe.get_doc("CSKH Import Batch", batch_id)
		self.assertEqual(mid_batch.status, "FAILED")
		# Header is row 1 -> 500 data rows in chunk 1 cover row_numbers 2..501
		self.assertEqual(int(mid_batch.progress or 0), 501)
		self.assertEqual(frappe.db.count("CSKH Import Batch Lead", {"import_batch": batch_id}), 500)

		# Re-confirm and resume from checkpoint -> zero duplicates, exact 620 created
		with mock.patch("frappe.enqueue"):
			import_service.confirm(batch_id, actor=self.admin_user)
		frappe.db.commit()
		import_worker.run_batch(batch_id)

		done_status = import_service._status_response(batch_id)
		self.assertEqual(done_status["status"], "COMPLETED")
		self.assertEqual(done_status["created_rows"], 620)
		self.assertEqual(done_status["reused_rows"], 0)
		self.assertEqual(frappe.db.count("CSKH Import Batch Lead", {"import_batch": batch_id}), 620)

	def test_record_attempt_and_callback_sql_budget_and_notification_indexes(self):
		"""Verify `tabCRM Notification` composite indexes and `record_attempt` + `followup` parity & SQL budget."""
		frappe.set_user("Administrator")
		notif_idx = {
			str(r.Key_name) for r in frappe.db.sql("SHOW INDEX FROM `tabCRM Notification`", as_dict=True)
		}
		self.assertIn("cskh_notif_type_doc_idx", notif_idx)
		self.assertIn("cskh_notif_ref_doc_idx", notif_idx)

		lead_warm = self.make_canonical_lead()
		lead = self.make_canonical_lead()
		camp = self.make_campaign(team_scope=self.team_a)
		wi_warm = self._assign_new_work_item(camp, self.staff_a, lead=lead_warm)
		wi_name = self._assign_new_work_item(camp, self.staff_a, lead=lead)
		frappe.db.commit()
		due_dt = (now_datetime() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

		with runtime_cache.enable_runtime_cache_for_test():
			# Warm Frappe DocType Meta + runtime_cache on one prior write (matching steady-state worker)
			warm_det = read_model.get_work_item_detail(wi_warm, actor=self.staff_a)
			contact_attempt.record_attempt(
				work_item=wi_warm,
				channel="phone_call",
				outcome_code="callback_requested",
				note="Warm-up cache",
				expected_revision=warm_det["work_item"]["revision"],
				followup={"due_at": due_dt, "title": "Warm-up callback"},
				idempotency_key=self.unique_key("att-warm"),
				actor=self.staff_a,
			)
			frappe.db.commit()

			detail0 = read_model.get_work_item_detail(wi_name, actor=self.staff_a)
			self.assertEqual(detail0["work_item"]["name"], wi_name)

			sql_counter = {"n": 0}
			orig_sql = frappe.db.sql

			def _count_sql(query, *args, **kwargs):
				sql_counter["n"] += 1
				return orig_sql(query, *args, **kwargs)

			idem_key = self.unique_key("att-opt")
			with mock.patch.object(frappe.db, "sql", side_effect=_count_sql):
				res = contact_attempt.record_attempt(
					work_item=wi_name,
					channel="phone_call",
					outcome_code="callback_requested",
					note="Khách hẹn gọi lại chiều nay",
					expected_revision=detail0["work_item"]["revision"],
					followup={"due_at": due_dt, "title": "Gọi lại xác nhận hồ sơ"},
					idempotency_key=idem_key,
					actor=self.staff_a,
				)

			self.assertEqual(res["status"], "recorded")
			self.assertTrue(res["callback_task"])
			# Steady-state warm write executes <= 30 SQL statements (~27 vs ~63 cold without runtime_cache)
			self.assertLessEqual(sql_counter["n"], 30, f"Unexpected SQL count: {sql_counter['n']}")

			# Idempotency replay returns identical attempt + followup task with 0 duplicates
			replay = contact_attempt.record_attempt(
				work_item=wi_name,
				channel="phone_call",
				outcome_code="callback_requested",
				note="Khách hẹn gọi lại chiều nay",
				expected_revision=detail0["work_item"]["revision"],
				followup={"due_at": due_dt, "title": "Gọi lại xác nhận hồ sơ"},
				idempotency_key=idem_key,
				actor=self.staff_a,
			)
			self.assertTrue(replay["idempotent_replay"])
			self.assertEqual(replay["attempt_id"], res["attempt_id"])
			self.assertEqual(replay["callback_task"], res["callback_task"])
			self.assertEqual(
				frappe.db.count(
					"CRM Notification",
					{"to_user": self.staff_a, "notification_type_doc": res["callback_task"]},
				),
				1,
			)

			detail1 = read_model.get_work_item_detail(wi_name, actor=self.staff_a)
			self.assertEqual(detail1["work_item"]["attempt_count"], 1)
			self.assertEqual(detail1["open_followup"]["task_id"], res["callback_task"])
			tl = read_model.list_attempts_timeline(wi_name, actor=self.staff_a)
			self.assertEqual(tl["total"], 1)
			self.assertEqual(tl["items"][0]["attempt_id"], res["attempt_id"])

	def test_runtime_cache_rbac_scope_isolation_and_ssr_csrf_isolation(self):
		"""Verify zero cross-user / cross-team / BOD PII / CSRF leakage when `runtime_cache` is active."""
		frappe.set_user("Administrator")
		lead_a = self.make_canonical_lead()
		lead_b = self.make_canonical_lead()
		camp_a = self.make_campaign(team_scope=self.team_a)
		camp_b = self.make_campaign(team_scope=self.team_b)
		self._assign_new_work_item(camp_a, self.staff_a, lead=lead_a)
		self._assign_new_work_item(camp_b, self.staff_c, lead=lead_b)
		cur_period = resolve_period_spec("monthly", today_hcm().strftime("%Y-%m"))
		kpi_runs.execute_kpi_recompute(cur_period, run_mode="full", actor="Administrator")

		with runtime_cache.enable_runtime_cache_for_test():
			# 1. Guest blocked with 403 on both SSR context and report API
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				cskh_context.build_context(
					frappe._dict(),
					route="my-customers",
					title="Khách của tôi",
					allowed_roles=cskh_context.WORKFLOW_PII_ROLES,
				)
			with self.assertRaises(ForbiddenScope):
				report_query.get_executive_overview(period_type="monthly", period_key=cur_period.period_key)

			# 2. BOD blocked on PII page (`my-customers`), allowed on `quality-overview` with `pii=False`,
			#    and blocked on `get_metric_drilldown(..., include_pii=1)`
			frappe.set_user(self.bod_user)
			with self.assertRaises(frappe.PermissionError):
				cskh_context.build_context(
					frappe._dict(),
					route="my-customers",
					title="Khách của tôi",
					allowed_roles=cskh_context.WORKFLOW_PII_ROLES,
				)
			bod_ctx = cskh_context.build_context(
				frappe._dict(),
				route="quality-overview",
				title="Tổng quan chất lượng",
				allowed_roles=cskh_context.P3_EXECUTIVE_ROLES,
				require_p3=True,
			)
			self.assertEqual(bod_ctx.cskh_user, self.bod_user)
			self.assertFalse(bod_ctx.cskh_capabilities["pii"])
			with self.assertRaises(ForbiddenScope):
				report_query.get_metric_drilldown(
					metric_code="ELIGIBLE_COVERAGE",
					period_type="monthly",
					period_key=cur_period.period_key,
					include_pii=1,
					actor=self.bod_user,
				)

			# 3. Leader A vs Leader B strict subtree isolation (`leader_root_team` & campaign filter)
			frappe.set_user(self.leader_a)
			ldr_a_ctx = cskh_context.build_context(
				frappe._dict(),
				route="team-quality",
				title="Chất lượng đội nhóm",
				allowed_roles=cskh_context.P3_TEAM_QUALITY_ROLES,
				require_p3=True,
			)
			frappe.set_user(self.leader_b)
			ldr_b_ctx = cskh_context.build_context(
				frappe._dict(),
				route="team-quality",
				title="Chất lượng đội nhóm",
				allowed_roles=cskh_context.P3_TEAM_QUALITY_ROLES,
				require_p3=True,
			)
			self.assertEqual(ldr_a_ctx.cskh_capabilities["leader_root_team"], self.team_a)
			self.assertEqual(ldr_b_ctx.cskh_capabilities["leader_root_team"], self.team_b)
			with self.assertRaises(ForbiddenScope):
				report_query.get_team_comparison(
					period_type="monthly",
					period_key=cur_period.period_key,
					campaign=camp_b,
					actor=self.leader_a,
				)

			# 4. SSR HTML renderer CSRF token isolation across distinct sessions of the SAME user
			set_request(method="GET", path="/cskh/my-customers")
			try:
				frappe.set_user(self.staff_a)
				sess_data = getattr(frappe.local.session, "data", None)
				if isinstance(sess_data, dict):
					sess_data["csrf_token"] = "csrf-tok-session-alpha-111"
				r1 = CskhPageRenderer(path="cskh/my-customers")
				self.assertTrue(r1.can_render())
				html_1 = r1.get_html()
				self.assertIn("csrf-tok-session-alpha-111", html_1)
				self.assertIn(self.staff_a, html_1)

				if isinstance(sess_data, dict):
					sess_data["csrf_token"] = "csrf-tok-session-beta-222"
				r2 = CskhPageRenderer(path="cskh/my-customers")
				self.assertTrue(r2.can_render())
				html_2 = r2.get_html()
				self.assertIn("csrf-tok-session-beta-222", html_2)
				self.assertNotIn("csrf-tok-session-alpha-111", html_2)

				# Staff B must never see Staff A's identity in SSR HTML
				frappe.set_user(self.staff_b)
				sess_data_b = getattr(frappe.local.session, "data", None)
				if isinstance(sess_data_b, dict):
					sess_data_b["csrf_token"] = "csrf-tok-staff-b-333"
				r3 = CskhPageRenderer(path="cskh/my-customers")
				self.assertTrue(r3.can_render())
				html_3 = r3.get_html()
				self.assertIn(self.staff_b, html_3)
				self.assertNotIn(self.staff_a, html_3)
				self.assertNotIn("csrf-tok-session-beta-222", html_3)
			finally:
				frappe.local.request = None
				frappe.set_user("Administrator")

	def test_runtime_cache_post_write_freshness_across_all_domains(self):
		"""Verify immediate post-write cache invalidation across Policy, Outcome, Campaign, Correction, and KPI Dirty Scope."""
		frappe.set_user("Administrator")
		lead = self.make_canonical_lead()
		camp = self.make_campaign(title="Chiến dịch Trước Cập Nhật", team_scope=self.team_a)
		wi_name = self._assign_new_work_item(camp, self.staff_a, lead=lead)
		frappe.db.commit()
		cur_period = resolve_period_spec("monthly", today_hcm().strftime("%Y-%m"))

		with runtime_cache.enable_runtime_cache_for_test():
			# Clear any pending dirty scopes left by prior suites, then publish a clean baseline run
			kpi_runs.process_dirty_scopes()
			run1 = kpi_runs.execute_kpi_recompute(cur_period, run_mode="full", actor="Administrator")
			ov1 = report_query.get_executive_overview(
				period_type="monthly", period_key=cur_period.period_key, actor=self.bod_user
			)
			self.assertEqual(ov1["freshness"]["run_id"], run1["run_id"])
			self.assertFalse(ov1["freshness"]["dirty_pending"])

			# 1. Policy write immediately invalidates `p3_reporting_enabled`
			frappe.set_user(self.admin_user)
			settings_api.save_policy(
				policy_code="GLOBAL-DEFAULT",
				title="Chính sách Tạm Tắt Báo Cáo",
				scope_type="Global",
				enabled=1,
				p3_sla_enabled=1,
				p3_reporting_enabled=0,
				qa_enabled=0,
				idempotency_key=self.unique_key("pol-off"),
			)
			frappe.db.commit()
			ov_off = report_query.get_executive_overview(
				period_type="monthly", period_key=cur_period.period_key, actor=self.bod_user
			)
			self.assertFalse(ov_off["reporting_enabled"])
			self.assertEqual(ov_off["status"], "disabled_by_policy")

			settings_api.save_policy(
				policy_code="GLOBAL-DEFAULT",
				title="Chính sách Bật Lại Báo Cáo",
				scope_type="Global",
				enabled=1,
				p3_sla_enabled=1,
				p3_reporting_enabled=1,
				qa_enabled=0,
				idempotency_key=self.unique_key("pol-on"),
			)
			frappe.db.commit()
			ov_on = report_query.get_executive_overview(
				period_type="monthly", period_key=cur_period.period_key, actor=self.bod_user
			)
			self.assertTrue(ov_on["ok"])
			self.assertTrue(ov_on["actor_scope"]["p3_reporting_enabled"])
			self.assertNotEqual(ov_on.get("status"), "disabled_by_policy")

			# 2. Campaign update immediately updates cached campaign title map
			campaigns_api.save(
				campaign=camp,
				title="Chiến dịch Sau Cập Nhật Tức Thời",
				status="Active",
				team_scope=self.team_a,
			)
			frappe.db.commit()
			self.assertEqual(
				report_query._resolve_campaign_title_map({camp}).get(camp),
				"Chiến dịch Sau Cập Nhật Tức Thời",
			)

			# 3. `record_attempt` marks `dirty_pending=True` immediately on commit;
			#    `process_dirty_scopes` swaps `run_id` and flips `dirty_pending=False` immediately!
			det = read_model.get_work_item_detail(wi_name, actor=self.staff_a)
			att_res = contact_attempt.record_attempt(
				work_item=wi_name,
				channel="phone_call",
				outcome_code="no_answer",
				expected_revision=det["work_item"]["revision"],
				idempotency_key=self.unique_key("fresh-att"),
				actor=self.staff_a,
			)
			frappe.db.commit()
			ov_dirty = report_query.get_executive_overview(
				period_type="monthly", period_key=cur_period.period_key, actor=self.bod_user
			)
			self.assertTrue(ov_dirty["freshness"]["dirty_pending"])

			frappe.set_user("Administrator")
			drain_res = kpi_runs.process_dirty_scopes()
			self.assertGreaterEqual(drain_res["processed_scopes"], 1)
			ov_fresh = report_query.get_executive_overview(
				period_type="monthly", period_key=cur_period.period_key, actor=self.bod_user
			)
			self.assertFalse(ov_fresh["freshness"]["dirty_pending"])
			self.assertNotEqual(ov_fresh["freshness"]["run_id"], run1["run_id"])

			# Verify fast SQL drill-downs (`CONNECTED_RATE` & `VALID_OUTCOME_LOGGING_RATE`) with `runtime_cache`
			dd_conn = report_query.get_metric_drilldown(
				metric_code="CONNECTED_RATE",
				period_type="monthly",
				period_key=cur_period.period_key,
				scope_type="campaign",
				scope_id=camp,
				include_pii=0,
				actor=self.bod_user,
			)
			self.assertTrue(dd_conn["ok"])
			dd_log = report_query.get_metric_drilldown(
				metric_code="VALID_OUTCOME_LOGGING_RATE",
				period_type="monthly",
				period_key=cur_period.period_key,
				scope_type="campaign",
				scope_id=camp,
				include_pii=0,
				actor=self.bod_user,
			)
			self.assertTrue(dd_log["ok"])

			# Direct `frappe.db.set_value(..., update_modified=False)` immediately invalidates `DOMAIN_POLICY`
			from crm_cskh.services import p3_flags

			self.assertFalse(p3_flags.is_qa_enabled())
			frappe.db.set_value("CSKH Policy", "GLOBAL-DEFAULT", "qa_enabled", 1, update_modified=False)
			self.assertTrue(p3_flags.is_qa_enabled())
			frappe.db.set_value("CSKH Policy", "GLOBAL-DEFAULT", "qa_enabled", 0, update_modified=False)
			self.assertFalse(p3_flags.is_qa_enabled())

			# 4. Auto-approved correction immediately bumps `DOMAIN_CORRECTION` across workers via Redis
			#    (`void -> restore` consecutive flow in `G14` with simulated separate worker RAM)
			det2 = read_model.get_work_item_detail(wi_name, actor=self.staff_a)
			corr_res = correction.request_correction(
				attempt=att_res["attempt_id"],
				action="void",
				reason="Ghi nhầm cuộc gọi thử nghiệm",
				auto_approve=True,
				expected_revision=det2["work_item"]["revision"],
				idempotency_key=self.unique_key("fresh-void"),
				actor=self.admin_user,
			)
			frappe.db.commit()
			self.assertEqual(corr_res["status"], "approved")
			# Simulate Gunicorn Worker B (fresh `_SITE_EPOCHS` + `on_before_request()`, reading Redis `cskh:runtime_epochs`)
			with runtime_cache._EPOCH_LOCK:
				runtime_cache._SITE_EPOCHS.clear()
			runtime_cache.on_before_request()
			self.assertGreater(runtime_cache.get_domain_epoch(runtime_cache.DOMAIN_CORRECTION), 0)
			eff_after = read_model.compute_effective_attempts(wi_name)
			self.assertTrue(eff_after[0]["is_voided"])

			det3 = read_model.get_work_item_detail(wi_name, actor=self.staff_a)
			rest_res = correction.request_correction(
				attempt=att_res["attempt_id"],
				action="restore",
				reason="Khôi phục bản ghi hợp lệ sau kiểm tra",
				auto_approve=True,
				expected_revision=det3["work_item"]["revision"],
				idempotency_key=self.unique_key("fresh-restore"),
				actor=self.admin_user,
			)
			frappe.db.commit()
			self.assertEqual(rest_res["status"], "approved")
			with runtime_cache._EPOCH_LOCK:
				runtime_cache._SITE_EPOCHS.clear()
			runtime_cache.on_before_request()
			eff_restored = read_model.compute_effective_attempts(wi_name)
			self.assertFalse(eff_restored[0]["is_voided"])

			# 5. `User` role downgrade (`Sales Manager` -> `Sales User` only, WITHOUT manual `frappe.clear_cache()`)
			#    propagates via Redis `cskh:runtime_epochs` EVEN AFTER an earlier CLI `frappe.clear_cache()` (`FLUSHALL`)
			#    reset Redis counters below Gunicorn Worker B's pre-flush local epoch (`G11` / `S09` exact topology)
			for _ in range(10):
				runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_USER_HIERARCHY)
			with runtime_cache._EPOCH_LOCK:
				pre_flush_worker_epochs = {
					k: (v[0], dict(v[1])) for k, v in runtime_cache._SITE_EPOCHS.items()
				}
				pre_flush_worker_gens = dict(runtime_cache._SITE_GENS)
			# External CLI runs `frappe.clear_cache()` (`FLUSHALL` resets Redis counters to 1 under a new `__gen__`)
			frappe.clear_cache()
			# Restore Worker B's pre-flush RAM state (`counter >= 10` under old `__gen__`), then serve pre-downgrade HTTP read
			with runtime_cache._EPOCH_LOCK:
				runtime_cache._SITE_EPOCHS.clear()
				runtime_cache._SITE_EPOCHS.update(pre_flush_worker_epochs)
				runtime_cache._SITE_GENS.clear()
				runtime_cache._SITE_GENS.update(pre_flush_worker_gens)
			runtime_cache.on_before_request()
			ldr_pre = read_model.get_work_item_detail(wi_name, actor=self.leader_a)
			self.assertEqual(ldr_pre["work_item"]["name"], wi_name)
			frappe.set_user("Administrator")
			u_ldr = frappe.get_doc("User", self.leader_a)
			orig_roles = [r.role for r in u_ldr.roles]
			try:
				# Snapshot Worker B's warm `_USER_PROFILE_LRU`, `_SITE_EPOCHS`, and `_SITE_GENS` before CLI mutation
				saved_profile_lru = dict(runtime_cache._USER_PROFILE_LRU._data)
				with runtime_cache._EPOCH_LOCK:
					saved_worker_epochs = {
						k: (v[0], dict(v[1])) for k, v in runtime_cache._SITE_EPOCHS.items()
					}
					saved_worker_gens = dict(runtime_cache._SITE_GENS)
				u_ldr.roles = []
				u_ldr.append("roles", {"role": "Sales User"})
				u_ldr.save(ignore_permissions=True)
				frappe.db.commit()
				# Simulate Worker B still holding pre-downgrade RAM state, syncing epochs from Redis on `on_before_request()`
				runtime_cache._USER_PROFILE_LRU._data.update(saved_profile_lru)
				with runtime_cache._EPOCH_LOCK:
					runtime_cache._SITE_EPOCHS.clear()
					runtime_cache._SITE_EPOCHS.update(saved_worker_epochs)
					runtime_cache._SITE_GENS.clear()
					runtime_cache._SITE_GENS.update(saved_worker_gens)
				runtime_cache.on_before_request()
				self.assertNotIn("Sales Manager", runtime_cache.get_roles_set(self.leader_a))
				with self.assertRaises((Forbidden, ForbiddenScope, frappe.PermissionError)):
					read_model.get_work_item_detail(wi_name, actor=self.leader_a)
			finally:
				frappe.set_user("Administrator")
				u_restore = frappe.get_doc("User", self.leader_a)
				u_restore.roles = []
				for r_name in orig_roles:
					u_restore.append("roles", {"role": r_name})
				u_restore.save(ignore_permissions=True)
				frappe.db.commit()

	def test_g14_closed_work_item_void_breached_handoff_and_restore_sla_relink(self):
		"""Verify `G14` `void -> restore` on an already-`closed` `CSKH Work Item`:
		1. `void` transitions `FIRST_ATTEMPT` and `FOLLOWUP` to `breached_handoff` (`fulfilled_by_attempt=None`)
		   and `FIRST_CONNECTED` to `canceled_validly` while keeping the Work Item `closed`.
		2. `restore` transitions SLA obligations back to their true status (`fulfilled_on_time` / `fulfilled_late`)
		   and re-links `fulfilled_by_attempt` to the restored `CSKH Contact Attempt`.
		"""
		frappe.set_user("Administrator")
		camp = self.make_campaign(title="Chiến dịch Kiểm định G14 Closed WI", team_scope=self.team_a)
		wi_on_time = self._assign_new_work_item(camp, self.staff_a)
		due_dt = (now_datetime() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
		frappe.db.commit()

		with runtime_cache.enable_runtime_cache_for_test():
			# 1. Record attempt 1 (`no_answer`, `is_connected=0`, with follow-up)
			#    -> fulfills `FIRST_ATTEMPT` (`fulfilled_on_time`) & spawns `FOLLOWUP`, while `FIRST_CONNECTED` stays `open`
			att1 = contact_attempt.record_attempt(
				work_item=wi_on_time,
				channel="phone_call",
				outcome_code="no_answer",
				note="Chưa bắt máy, đặt lịch gọi lại xác nhận phỏng vấn",
				followup={"due_at": due_dt, "title": "Gọi lại chốt lịch G14"},
				idempotency_key=self.unique_key("g14-closed-att1"),
				actor=self.staff_a,
			)
			task_id = att1["callback_task"]
			self.assertTrue(task_id)

			# 2. Record attempt 2 (`connected_interested`, `complete_open_followup=True`)
			#    -> fulfills `FOLLOWUP` (`fulfilled_on_time`) & `FIRST_CONNECTED` (`fulfilled_on_time`)
			att2 = contact_attempt.record_attempt(
				work_item=wi_on_time,
				channel="phone_call",
				outcome_code="connected_interested",
				note="Khách đồng ý tham gia phỏng vấn",
				complete_open_followup=True,
				idempotency_key=self.unique_key("g14-closed-att2"),
				actor=self.staff_a,
			)

			# 3. Close `wi_on_time` -> Work Item is now `closed` (`assigned_to = None`)
			cl_res = contact_attempt.close_work_item(
				work_item=wi_on_time,
				completion_reason="Hoàn tất quy trình tư vấn chiến dịch",
				close_category="normal",
				idempotency_key=self.unique_key("g14-closed-close"),
				actor=self.staff_a,
			)
			self.assertEqual(cl_res["work_item_status"], "closed")

			ob_first = frappe.db.get_value(
				"CSKH SLA Obligation",
				{"work_item": wi_on_time, "obligation_type": "FIRST_ATTEMPT"},
				["name", "status", "fulfilled_by_attempt", "effective_due_at"],
				as_dict=True,
			)
			ob_fu = frappe.db.get_value(
				"CSKH SLA Obligation",
				{"work_item": wi_on_time, "obligation_type": "FOLLOWUP", "followup_task": task_id},
				["name", "status", "fulfilled_by_attempt", "effective_due_at"],
				as_dict=True,
			)
			ob_conn = frappe.db.get_value(
				"CSKH SLA Obligation",
				{"work_item": wi_on_time, "obligation_type": "FIRST_CONNECTED"},
				["name", "status", "fulfilled_by_attempt"],
				as_dict=True,
			)
			self.assertEqual(ob_first.status, "fulfilled_on_time")
			self.assertEqual(ob_first.fulfilled_by_attempt, att1["attempt_id"])
			self.assertEqual(ob_fu.status, "fulfilled_on_time")
			self.assertEqual(ob_fu.fulfilled_by_attempt, att2["attempt_id"])
			self.assertEqual(ob_conn.status, "fulfilled_on_time")
			self.assertEqual(ob_conn.fulfilled_by_attempt, att2["attempt_id"])

			# 4. Void `att2` then `att1` on the `closed` Work Item -> `FIRST_ATTEMPT` & `FOLLOWUP` become `breached_handoff`
			void2 = correction.request_correction(
				attempt=att2["attempt_id"],
				action="void",
				reason="Hủy tạm thời lần gọi thứ 2 để kiểm tra SLA trên Work Item đã đóng",
				auto_approve=True,
				idempotency_key=self.unique_key("g14-closed-void2"),
				actor=self.admin_user,
			)
			self.assertEqual(void2["status"], "approved")
			self.assertEqual(void2["work_item_status"], "closed")

			fu_voided = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_fu.name,
				["status", "fulfilled_by_attempt", "fulfilled_at", "breached_at"],
				as_dict=True,
			)
			conn_voided = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_conn.name,
				["status", "fulfilled_by_attempt", "fulfilled_at"],
				as_dict=True,
			)
			self.assertEqual(fu_voided.status, "breached_handoff")
			self.assertIsNone(fu_voided.fulfilled_by_attempt)
			self.assertIsNone(fu_voided.fulfilled_at)
			self.assertEqual(get_datetime(fu_voided.breached_at), get_datetime(ob_fu.effective_due_at))
			self.assertEqual(conn_voided.status, "canceled_validly")
			self.assertIsNone(conn_voided.fulfilled_by_attempt)

			void1 = correction.request_correction(
				attempt=att1["attempt_id"],
				action="void",
				reason="Hủy tạm thời lần gọi thứ 1 để kiểm tra trạng thái breached_handoff",
				auto_approve=True,
				idempotency_key=self.unique_key("g14-closed-void1"),
				actor=self.leader_a,
			)
			self.assertEqual(void1["status"], "approved")
			self.assertEqual(void1["work_item_status"], "closed")

			first_voided = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_first.name,
				["status", "fulfilled_by_attempt", "fulfilled_at", "breached_at"],
				as_dict=True,
			)
			self.assertEqual(first_voided.status, "breached_handoff")
			self.assertIsNone(first_voided.fulfilled_by_attempt)
			self.assertIsNone(first_voided.fulfilled_at)
			self.assertEqual(get_datetime(first_voided.breached_at), get_datetime(ob_first.effective_due_at))

			# Simulate cross-worker Redis epoch sync before `restore`
			with runtime_cache._EPOCH_LOCK:
				runtime_cache._SITE_EPOCHS.clear()
			runtime_cache.on_before_request()
			det_void = read_model.get_work_item_detail(wi_on_time, actor=self.admin_user)
			self.assertEqual(det_void["work_item"]["status"], "closed")
			self.assertEqual(det_void["work_item"]["attempt_count"], 0)

			# 5. Restore `att1` via two-step `request_correction(auto_approve=False)` + `review_correction('approve')`
			rest1_req = correction.request_correction(
				attempt=att1["attempt_id"],
				action="restore",
				reason="Yêu cầu khôi phục lần gọi thứ 1 hợp lệ",
				auto_approve=False,
				idempotency_key=self.unique_key("g14-closed-rest1-req"),
				actor=self.admin_user,
			)
			self.assertEqual(rest1_req["status"], "pending")
			rest1_rev = correction.review_correction(
				correction_id=rest1_req["correction_id"],
				decision="approve",
				review_note="Xác nhận khôi phục lần gọi thứ 1 trên Work Item đã đóng",
				idempotency_key=self.unique_key("g14-closed-rest1-rev"),
				actor=self.admin_user,
			)
			self.assertEqual(rest1_rev["status"], "approved")
			self.assertEqual(rest1_rev["work_item_status"], "closed")

			first_restored = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_first.name,
				["status", "fulfilled_by_attempt", "fulfilled_at", "breached_at", "closed_at"],
				as_dict=True,
			)
			self.assertEqual(first_restored.status, "fulfilled_on_time")
			self.assertEqual(first_restored.fulfilled_by_attempt, att1["attempt_id"])
			self.assertIsNotNone(first_restored.fulfilled_at)
			self.assertIsNone(first_restored.breached_at)
			self.assertIsNone(first_restored.closed_at)

			# 6. Restore `att2` via `auto_approve=True` -> re-links both `FOLLOWUP` and `FIRST_CONNECTED` to `att2`
			rest2 = correction.request_correction(
				attempt=att2["attempt_id"],
				action="restore",
				reason="Khôi phục lần gọi thứ 2 hợp lệ trên Work Item đã đóng",
				auto_approve=True,
				idempotency_key=self.unique_key("g14-closed-rest2"),
				actor=self.admin_user,
			)
			self.assertEqual(rest2["status"], "approved")
			self.assertEqual(rest2["work_item_status"], "closed")

			fu_restored = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_fu.name,
				["status", "fulfilled_by_attempt", "fulfilled_at", "breached_at", "closed_at"],
				as_dict=True,
			)
			conn_restored = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_conn.name,
				["status", "fulfilled_by_attempt", "fulfilled_at", "breached_at", "closed_at"],
				as_dict=True,
			)
			self.assertEqual(fu_restored.status, "fulfilled_on_time")
			self.assertEqual(fu_restored.fulfilled_by_attempt, att2["attempt_id"])
			self.assertIsNotNone(fu_restored.fulfilled_at)
			self.assertIsNone(fu_restored.breached_at)
			self.assertIsNone(fu_restored.closed_at)
			self.assertEqual(conn_restored.status, "fulfilled_on_time")
			self.assertEqual(conn_restored.fulfilled_by_attempt, att2["attempt_id"])
			self.assertIsNotNone(conn_restored.fulfilled_at)
			self.assertIsNone(conn_restored.closed_at)

			det_rest = read_model.get_work_item_detail(wi_on_time, actor=self.admin_user)
			self.assertEqual(det_rest["work_item"]["attempt_count"], 2)
			self.assertEqual(det_rest["work_item"]["connected_attempt_count"], 1)

			# 7. Late attempt case (`fulfilled_late` -> `void` -> `breached_handoff` -> `restore` -> `fulfilled_late` + attempt re-link)
			wi_late = self._assign_new_work_item(camp, self.staff_a)
			ob_late_name = frappe.db.get_value(
				"CSKH SLA Obligation",
				{"work_item": wi_late, "obligation_type": "FIRST_ATTEMPT"},
				"name",
			)
			past_eff_due = add_to_date(now_datetime(), minutes=-15)
			with authorized_sla_write():
				d_late = frappe.get_doc("CSKH SLA Obligation", ob_late_name)
				d_late.effective_due_at = past_eff_due
				d_late.save(ignore_permissions=True)

			att_late = contact_attempt.record_attempt(
				work_item=wi_late,
				channel="phone_call",
				outcome_code="no_answer",
				note="Liên hệ sau hạn SLA",
				idempotency_key=self.unique_key("g14-late-att"),
				actor=self.staff_a,
			)
			contact_attempt.close_work_item(
				work_item=wi_late,
				completion_reason="Đóng sau liên hệ trễ hạn",
				close_category="normal",
				idempotency_key=self.unique_key("g14-late-close"),
				actor=self.staff_a,
			)
			self.assertEqual(
				frappe.db.get_value("CSKH SLA Obligation", ob_late_name, "status"),
				"fulfilled_late",
			)

			correction.request_correction(
				attempt=att_late["attempt_id"],
				action="void",
				reason="Hủy tạm lần liên hệ trễ hạn trên Work Item đã đóng",
				auto_approve=True,
				idempotency_key=self.unique_key("g14-late-void"),
				actor=self.admin_user,
			)
			late_voided = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_late_name,
				["status", "fulfilled_by_attempt", "fulfilled_at"],
				as_dict=True,
			)
			self.assertEqual(late_voided.status, "breached_handoff")
			self.assertIsNone(late_voided.fulfilled_by_attempt)
			self.assertIsNone(late_voided.fulfilled_at)

			correction.request_correction(
				attempt=att_late["attempt_id"],
				action="restore",
				reason="Khôi phục lần liên hệ trễ hạn về đúng fulfilled_late",
				auto_approve=True,
				idempotency_key=self.unique_key("g14-late-restore"),
				actor=self.admin_user,
			)
			late_restored = frappe.db.get_value(
				"CSKH SLA Obligation",
				ob_late_name,
				["status", "fulfilled_by_attempt", "fulfilled_at", "breached_at"],
				as_dict=True,
			)
			self.assertEqual(late_restored.status, "fulfilled_late")
			self.assertEqual(late_restored.fulfilled_by_attempt, att_late["attempt_id"])
			self.assertIsNotNone(late_restored.fulfilled_at)
			self.assertEqual(get_datetime(late_restored.breached_at), get_datetime(past_eff_due))
