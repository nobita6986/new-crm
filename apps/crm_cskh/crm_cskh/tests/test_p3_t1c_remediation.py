"""Comprehensive regression suite closing T1C Independent Verification findings `F-P3-01` .. `F-P3-10`.

Covers:
  1. `test_t1c_golden_dataset_all_16_scopes_qa_off_and_qa_on_exact_match` (`F-P3-01`, `F-P3-02`, `F-P3-03`, `F-P3-09`):
     - Verifies SHA-256 of immutable `t1c_p3_golden_dataset_expected.json` (`deb6be25a55a44deee4dcd21f9ee4fdada2ed38dfd2b14800c8cc1d375c49bdf`).
     - Verifies all 9 `calendar_golden_vectors` (`CAL-01..08`, including `EMPTY_CALENDAR` guard).
     - Materializes all 64 Work Items, 63 Intervals, 132 Attempts, 8 Corrections, 32 Callbacks,
       6 standalone noise records, 94 SLA Obligations, 9 QA Reviews, 3 Calendars, 3 Policies, and
       6 Team Membership History intervals into MariaDB and asserts `0` discrepancy across all 11 scopes
       (`qa_enabled=false`), all 5 employee scopes (`qa_enabled=true`), multi-day `DISTINCT` rollup,
       September restatement (`WI-024`, `CORR-003`), `historical_attribution_unknown = 4`, and reconciled drill-downs.
  2. `test_f_p3_04_leader_team_as_of_slice_drilldown_sla_qa_and_closed_wi_pii` (`F-P3-04`):
     - Mid-period staff transfer (`staff.a3`: `TEAM-A -> TEAM-B`): `Leader A` sees only `TEAM-A` historical slice
       (`6` intervals, `15` attempts) and subtree filter includes child sub-teams; `Leader B` sees only `TEAM-B`
       slice (`6` intervals, `15` attempts) with `0` exposure of `TEAM-A` private campaign, SLA, QA, or closed-WI PII
       across `get_employee_quality`, `get_metric_drilldown` (including `search` filter-oracle), `sla.list_obligations`,
       `qa.list_reviews`, `read_model.get_work_item_detail`, `reporting.customer_pool`, and generic `frappe.has_permission`.
  3. `test_f_p3_05_export_file_query_get_download_revocation_ttl_and_kpi_run_leader_403` (`F-P3-05`):
     - `Leader` denied `403` on company `CSKH KPI Run` across `get_run_status`, `frappe.get_list`, and `frappe.has_permission`,
       with positive control on authorized `team` `CSKH KPI Run`.
     - `File` records attached to `CSKH Export Job` filtered across `frappe.get_list("File")`, `frappe.has_permission("File")`,
       and `download_export` after role revocation (`403`) or TTL expiry (`410`/`403`).
  4. `test_f_p3_02_deadline_cohort_part_a_late_call_and_part_b_early_call_invariants` (`F-P3-02`):
     - Part A: Late call in `M+1` never erases or moves a breach whose `effective_due_at` was in `M`.
     - Part B: Early call in `M-1` for an obligation due in `M` evaluates `fulfilled_on_time` in `M`'s deadline cohort.
  5. `test_f_p3_06_metric_version_v1_to_v2_append_only_invalidation_and_restatement` (`F-P3-06`):
     - Legacy `cskh-metrics-v1` snapshots invalidated via `CSKH Dirty Scope` and restated to `cskh-metrics-v2`
       without mutating historical `v1` rows.
  6. `test_f_p3_07_campaign_pause_resume_close_sla_lifecycle_and_audit` (`F-P3-07`):
     - `CSKH Campaign` transitions (`Active -> Paused -> Active -> Closed`) freeze, shift `effective_due_at`
       (keeping `original_due_at` immutable), and cancel unbreached open SLA obligations while preserving prior breaches.
  7. `test_f_p3_08_unified_site_kill_switch_split_flags_and_queued_job_guards` (`F-P3-08`):
     - Site-level `frappe.conf` kill switch overrides DB `CSKH Policy` uniformly across UI context, reporting APIs,
       SLA hooks, export API, and already-queued RQ workers while preserving P1/P2 & catch-up logs.
  8. `test_f_p3_09_10_filter_options_server_side_filters_qa_409_400_and_bod_zero_pii` (`F-P3-09`, `F-P3-10`):
     - `get_filter_options`, `lead_source` filter, `teams_summary` + `alerts` + `first_touch_on_time_rate`,
       server-side filters/search/sort on `sla.list_obligations` & `get_metric_drilldown`, clean `409`/`400` on
       `qa.create_review`, `0-100` `score_components` scale, count `denominator=None`, and `BOD` `0` PII.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from crm_cskh.api import p3_reporting
from crm_cskh.api import qa as qa_api
from crm_cskh.api import sla as sla_api
from crm_cskh.jobs.recompute_kpi import run_single_period_recompute
from crm_cskh.p2_models import set_lead_dnc_fields
from crm_cskh.p3_models import (
	DEFAULT_P3_POLICY_FIELDS,
	authorized_export_write,
	authorized_kpi_write,
	authorized_qa_write,
	authorized_sla_write,
)
from crm_cskh.p3_permissions import is_export_job_currently_authorized
from crm_cskh.services import contact_attempt, ownership, read_model, reporting
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL
from crm_cskh.services.calendar import (
	InvalidCalendarError,
	WorkingCalendarSpec,
	add_business_minutes,
	resolve_working_calendar,
)
from crm_cskh.services.errors import (
	ExportExpired,
	Forbidden,
	ForbiddenScope,
	InvalidRequest,
	PolicyConflict,
	QaDisabled,
)
from crm_cskh.services.kpi_calculator import COMPANY_SCOPE_ID, METRIC_VERSION
from crm_cskh.services.kpi_periods import resolve_period_spec, today_hcm
from crm_cskh.services.kpi_runs import (
	execute_kpi_recompute,
	get_kpi_run_status,
	invalidate_stale_metric_version_snapshots,
	unpack_snapshot_extra,
	unpack_snapshot_metrics,
)
from crm_cskh.services.p3_export import (
	download_export,
	execute_export_job_now,
	request_export,
)
from crm_cskh.services.p3_flags import resolve_p3_flags
from crm_cskh.services.report_query import get_policy_flags
from crm_cskh.tests.p3_base import P3TestCase
from crm_cskh.www.cskh_context import build_context, is_p3_enabled

T1C_GOLDEN_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "t1c_p3_golden_dataset_expected.json"
T1C_GOLDEN_SHA256 = "deb6be25a55a44deee4dcd21f9ee4fdada2ed38dfd2b14800c8cc1d375c49bdf"


class TestP3T1CRemediation(P3TestCase):
	"""End-to-end positive and negative regressions for T1C findings `F-P3-01` .. `F-P3-10`."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._cleanup_t1c_golden_entities()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		for conf_key in (
			"cskh_p3_reporting_enabled",
			"cskh_p3_quality_enabled",
			"cskh_p3_sla_enabled",
			"cskh_p3_qa_enabled",
			"cskh_qa_enabled",
		):
			if hasattr(frappe.local, "conf") and conf_key in frappe.local.conf:
				del frappe.local.conf[conf_key]
		self._cleanup_t1c_golden_entities()
		frappe.db.commit()
		super().tearDown()

	def _wipe_cskh_runtime_tables(self) -> None:
		for dt in (
			"CSKH Dirty Scope",
			"CSKH KPI Snapshot",
			"CSKH KPI Run",
			"CSKH QA Review",
			"CSKH Export Job",
			"CSKH SLA Obligation",
			"CSKH Attempt Correction",
			"CSKH Contact Attempt",
			"CSKH Active Claim",
			"CSKH Assignment History",
			"CSKH Work Item",
		):
			if frappe.db.table_exists(dt):
				frappe.db.sql(f"delete from `tab{dt}`")
		if frappe.db.table_exists("CRM Task") and frappe.db.has_column("CRM Task", "cskh_is_followup"):
			frappe.db.sql("delete from `tabCRM Task` where coalesce(cskh_is_followup, 0) = 1")

	def _cleanup_t1c_golden_entities(self) -> None:
		self._wipe_cskh_runtime_tables()
		if frappe.db.table_exists("CRM Call Log"):
			frappe.db.sql("delete from `tabCRM Call Log` where id like 'CL-%' or name like 'CL-%'")
		if frappe.db.table_exists("CRM Task"):
			frappe.db.sql(
				"delete from `tabCRM Task` where title like 'Task ngoài CSKH %' or title like 'Callback CB-%'"
			)
		if frappe.db.table_exists("FCRM Note"):
			frappe.db.sql("delete from `tabFCRM Note` where title like 'EXT-%' or title like 'NOTE-%'")
		if frappe.db.table_exists("CRM Lead"):
			frappe.db.sql("delete from `tabCRM Lead` where last_name = 'Golden'")
		for camp_id in ("CAMP-P3-STD", "CAMP-P3-VIP", "CAMP-P3-PAUSE", "CAMP-PRIV-TEAMA"):
			if frappe.db.exists("CSKH Campaign", camp_id):
				frappe.db.sql("delete from `tabCSKH Campaign` where name = %s", (camp_id,))
		for pol_id in ("POL-STD", "POL-VIP-NIGHT"):
			if frappe.db.exists("CSKH Policy", pol_id):
				frappe.db.sql("delete from `tabCSKH Policy` where name = %s", (pol_id,))
		if frappe.db.exists("CSKH Policy", "GLOBAL-DEFAULT"):
			frappe.db.set_value(
				"CSKH Policy",
				"GLOBAL-DEFAULT",
				dict(DEFAULT_P3_POLICY_FIELDS),
				update_modified=False,
			)
		for cal_id in ("CAL-VN-SPLIT-BASE", "CAL-VN-SPLIT", "CAL-VN-OVERNIGHT", "CAL-EMPTY-TEST"):
			if frappe.db.exists("CSKH Working Calendar", cal_id):
				frappe.db.sql("delete from `tabCSKH Working Shift` where parent = %s", (cal_id,))
				frappe.db.sql("delete from `tabCSKH Working Calendar` where name = %s", (cal_id,))
		for hl_id in ("HL-CAL-VN-SPLIT", "HL-CAL-VN-OVERNIGHT"):
			if frappe.db.exists("CRM Holiday List", hl_id):
				frappe.db.sql("delete from `tabCRM Holiday` where parent = %s", (hl_id,))
				frappe.db.sql("delete from `tabCRM Holiday List` where name = %s", (hl_id,))
		if frappe.db.table_exists("CSKH Team Membership History"):
			frappe.db.sql(
				"delete from `tabCSKH Team Membership History` where user like %s",
				("%@t1cp3.local",),
			)
		for node in frappe.get_all(
			"CRM Sales Hierarchy",
			filters={"user": ["like", "%@t1cp3.local"]},
			fields=["name"],
			order_by="lft desc",
		):
			frappe.delete_doc("CRM Sales Hierarchy", node.name, force=True, ignore_permissions=True)

	def _seed_t1c_golden_dataset(self, exp: dict, *, qa_enabled: int = 0) -> dict:
		"""Materialize T1C's independent Golden Dataset (`t1c_p3_golden_dataset_expected.json`) 1:1 into MariaDB."""
		frappe.set_user("Administrator")
		self._cleanup_t1c_golden_entities()
		prev_mute = getattr(frappe.flags, "mute_emails", False)
		frappe.flags.mute_emails = True
		self.addCleanup(lambda: setattr(frappe.flags, "mute_emails", prev_mute))

		# 1. Seed users & `CRM Sales Hierarchy` (`TEAM-A` under `leader.a`, `TEAM-B` under `leader.b`)
		admin_u = self.ensure_user("cskh.admin@t1cp3.local", "CSKH CRM Admin")
		bod_u = self.ensure_user("bod.viewer@t1cp3.local", "CSKH BOD Viewer")
		leader_a = self.ensure_user("leader.a@t1cp3.local", "Sales Manager", "Sales User")
		leader_b = self.ensure_user("leader.b@t1cp3.local", "Sales Manager", "Sales User")
		staff_a1 = self.ensure_user("staff.a1@t1cp3.local", "Sales User")
		staff_a2 = self.ensure_user("staff.a2@t1cp3.local", "Sales User")
		staff_a3 = self.ensure_user("staff.a3@t1cp3.local", "Sales User")
		staff_b1 = self.ensure_user("staff.b1@t1cp3.local", "Sales User")
		staff_b2 = self.ensure_user("staff.b2@t1cp3.local", "Sales User")

		team_a_node = self.ensure_hierarchy(leader_a, 1)
		self.ensure_hierarchy(staff_a1, 0, leader_a)
		self.ensure_hierarchy(staff_a2, 0, leader_a)
		team_b_node = self.ensure_hierarchy(leader_b, 1)
		self.ensure_hierarchy(staff_b1, 0, leader_b)
		self.ensure_hierarchy(staff_b2, 0, leader_b)
		# `staff.a3` transferred to `TEAM-B` on `2026-10-16 00:00:00` -> current hierarchy under `leader_b`
		self.ensure_hierarchy(staff_a3, 0, leader_b)

		team_code_to_node = {"TEAM-A": team_a_node, "TEAM-B": team_b_node}

		# Replace auto-created membership rows for `@t1cp3.local` with exact `team_membership_history`
		frappe.db.sql(
			"delete from `tabCSKH Team Membership History` where user like %s",
			("%@t1cp3.local",),
		)
		for tm in exp["team_membership_history"]:
			t_node = team_code_to_node[tm["team"]]
			frappe.db.sql(
				"""
				insert into `tabCSKH Team Membership History`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 user, team_node, team_name_snapshot, effective_from, effective_to, source_type, recorded_by)
				values (%s, %s, %s, 'Administrator', 'Administrator', 0, 0,
				        %s, %s, %s, %s, %s, 'golden_seed', 'Administrator')
				""",
				(
					f"TMH-{frappe.generate_hash(length=10)}",
					tm["from"],
					tm["from"],
					tm["user"],
					t_node,
					tm["team"],
					tm["from"],
					tm["to"],
				),
			)

		# 2. Seed `CRM Holiday List` + `CSKH Working Calendar`
		prev_hl = getattr(frappe.flags, "cskh_seed_holiday_list", False)
		prev_cal = getattr(frappe.flags, "cskh_seed_calendar", False)
		frappe.flags.cskh_seed_holiday_list = True
		frappe.flags.cskh_seed_calendar = True
		try:
			for cal_id, cal_def in exp["calendars"].items():
				hl_name = None
				if cal_def.get("holidays"):
					hl_name = f"HL-{cal_id}"
					hl_doc = frappe.get_doc(
						{
							"doctype": "CRM Holiday List",
							"holiday_list_name": hl_name,
							"from_date": "2026-01-01",
							"to_date": "2026-12-31",
							"weekly_off": "Sunday",
							"holidays": [
								{"date": h_date, "description": f"Holiday {h_date}", "weekly_off": 0}
								for h_date in cal_def["holidays"]
							],
						}
					)
					hl_doc.insert(ignore_permissions=True)

				shifts = []
				for day_name, pairs in cal_def["shifts"].items():
					for s_hm, e_hm in pairs:
						is_ov = 1 if e_hm <= s_hm else 0
						shifts.append(
							{
								"workday": day_name,
								"start_time": f"{s_hm}:00",
								"end_time": f"{e_hm}:00",
								"is_overnight": is_ov,
							}
						)
				frappe.get_doc(
					{
						"doctype": "CSKH Working Calendar",
						"calendar_code": cal_id,
						"title": cal_id,
						"timezone": cal_def["timezone"],
						"holiday_list": hl_name,
						"enabled": 1,
						"is_default": 0,
						"shifts": shifts,
					}
				).insert(ignore_permissions=True)
		finally:
			frappe.flags.cskh_seed_holiday_list = prev_hl
			frappe.flags.cskh_seed_calendar = prev_cal

		# 3. Seed `CSKH Campaign` (`CAMP-P3-STD`, `CAMP-P3-VIP`, `CAMP-P3-PAUSE`) & `CSKH Policy`
		for camp_code, camp_status in (
			("CAMP-P3-STD", "Active"),
			("CAMP-P3-VIP", "Active"),
			("CAMP-P3-PAUSE", "Active"),
		):
			c_doc = frappe.get_doc(
				{
					"doctype": "CSKH Campaign",
					"title": camp_code,
					"code": camp_code,
					"status": camp_status,
					"assignment_mode": "weighted_round_robin",
					"start_date": "2026-09-01",
					"end_date": "2026-12-31",
				}
			)
			c_doc.insert(ignore_permissions=True, set_name=camp_code)

		pol_std = exp["policies"]["POL-STD@v2"]
		pol_vip = exp["policies"]["POL-VIP-NIGHT@v1"]
		frappe.db.set_value(
			"CSKH Policy",
			"GLOBAL-DEFAULT",
			{
				"working_calendar": pol_std["calendar_id"],
				"first_attempt_sla_minutes": pol_std["first_attempt_target_minutes"],
				"followup_grace_minutes": pol_std["followup_grace_minutes"],
				"handoff_min_grace_minutes": pol_std["handoff_min_grace_minutes"],
				"min_exposure_business_minutes": pol_std["min_exposure_business_minutes"],
				"min_qa_sample_size": pol_std["qa_min_sample"],
				"min_valid_score_weight": pol_std["min_valid_weight_for_score"],
				"min_eligible_events_for_score": 1,
				"qa_enabled": cint(qa_enabled),
				"p3_sla_enabled": 1,
				"p3_reporting_enabled": 1,
				"weight_first_attempt_sla": 25,
				"weight_eligible_coverage": 20,
				"weight_data_hygiene": 20,
				"weight_followup_sla": 20,
				"weight_callback_discipline": 20,
				"weight_qa_quality": 15,
			},
			update_modified=False,
		)
		frappe.get_doc(
			{
				"doctype": "CSKH Policy",
				"policy_code": "POL-VIP-NIGHT",
				"title": "Chính sách VIP Ca Đêm",
				"scope_type": "Campaign",
				"campaign": "CAMP-P3-VIP",
				"enabled": 1,
				"version": 1,
				"enabled_channels": "phone_call,sms,zalo,facebook,other",
				"working_calendar": pol_vip["calendar_id"],
				"first_attempt_sla_minutes": pol_vip["first_attempt_target_minutes"],
				"followup_grace_minutes": pol_vip["followup_grace_minutes"],
				"handoff_min_grace_minutes": pol_vip["handoff_min_grace_minutes"],
				"min_exposure_business_minutes": pol_vip["min_exposure_business_minutes"],
				"min_qa_sample_size": pol_vip["qa_min_sample"],
				"min_valid_score_weight": pol_vip["min_valid_weight_for_score"],
				"min_eligible_events_for_score": 1,
				"qa_enabled": cint(qa_enabled),
				"p3_sla_enabled": 1,
				"p3_reporting_enabled": 1,
				"weight_first_attempt_sla": 25,
				"weight_eligible_coverage": 20,
				"weight_data_hygiene": 20,
				"weight_followup_sla": 20,
				"weight_callback_discipline": 20,
				"weight_qa_quality": 15,
			}
		).insert(ignore_permissions=True)

		# 4. Pre-index intervals & DNC attempts per Work Item
		ivs_by_wi: dict[str, list[dict]] = {}
		for iv in exp["records"]["intervals"]:
			ivs_by_wi.setdefault(iv["work_item"], []).append(iv)
		dnc_by_wi: dict[str, str] = {}
		for att in exp["records"]["attempts"]:
			if att.get("is_dnc"):
				dnc_by_wi[att["work_item"]] = att["occurred_at"]

		# 5. Insert 64 `CRM Lead` + `CSKH Work Item`
		lead_by_wi: dict[str, str] = {}
		on_hold_wis: set[str] = set()
		for idx, wi_rec in enumerate(exp["records"]["work_items"], start=1):
			wi_id = wi_rec["work_item"]
			wi_ivs = ivs_by_wi.get(wi_id, [])
			last_assignee = wi_ivs[-1]["assignee"] if wi_ivs else wi_rec.get("current_crm_lead_owner_trap")
			active_lead_owner = (
				last_assignee if wi_rec["status"] in ("assigned", "in_progress", "on_hold") else ""
			)
			phone = f"0977{idx:06d}"
			lead_doc = frappe.get_doc(
				{
					"doctype": "CRM Lead",
					"naming_series": "CRM-LEAD-",
					"first_name": f"Khách {wi_id}",
					"last_name": "Golden",
					"lead_name": f"Khách {wi_id} Golden",
					"mobile_no": phone,
					"status": "New",
					"lead_owner": active_lead_owner,
				}
			)
			lead_doc.insert(ignore_permissions=True)
			lead_by_wi[wi_id] = lead_doc.name

			if wi_id in dnc_by_wi:
				set_lead_dnc_fields(
					lead_doc.name,
					{
						"cskh_do_not_contact": 1,
						"cskh_dnc_at": dnc_by_wi[wi_id],
						"cskh_dnc_work_item": wi_id,
						"cskh_dnc_reason": "Yêu cầu ngừng liên hệ Golden",
					},
					update_modified=False,
				)

			wi_created = wi_ivs[0]["start_at"] if wi_ivs else "2026-10-05 09:00:00"
			assigned_to = (
				last_assignee
				if wi_rec["status"] in ("assigned", "in_progress", "on_hold") and last_assignee
				else None
			)
			initial_status = "in_progress" if wi_rec["status"] == "on_hold" else wi_rec["status"]
			if wi_rec["status"] == "on_hold":
				on_hold_wis.add(wi_id)
			wi_doc = frappe.get_doc(
				{
					"doctype": "CSKH Work Item",
					"lead": lead_doc.name,
					"campaign": wi_rec["campaign"],
					"status": initial_status,
					"assigned_to": assigned_to,
					"assigned_by": admin_u if assigned_to else None,
					"assigned_at": (wi_ivs[-1]["start_at"] if wi_ivs else wi_created)
					if assigned_to
					else None,
				}
			)
			wi_doc.insert(ignore_permissions=True, set_name=wi_id)
			frappe.db.sql(
				"update `tabCSKH Work Item` set creation = %s, modified = %s where name = %s",
				(wi_created, wi_created, wi_id),
			)

		# 6. Insert 63 `CSKH Assignment History` intervals (`INT-001..063`)
		iv_map: dict[str, dict] = {}
		for iv in exp["records"]["intervals"]:
			iv_id = iv["interval_id"]
			iv_map[iv_id] = iv
			t_node = team_code_to_node[iv["team_at_assignment"]]
			frappe.db.sql(
				"""
				insert into `tabCSKH Assignment History`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 work_item, action, from_user, to_user, actor, event_at,
				 ownership_start, ownership_end, team_at_assignment, attribution_status, reason)
				values (%s, %s, %s, %s, %s, 0, 0,
				        %s, 'assign', null, %s, %s, %s,
				        %s, %s, %s, %s, %s)
				""",
				(
					iv_id,
					iv["start_at"],
					iv["end_at"] or iv["start_at"],
					admin_u,
					admin_u,
					iv["work_item"],
					iv["assignee"],
					admin_u,
					iv["start_at"],
					iv["start_at"],
					iv["end_at"],
					t_node,
					AUTHORITATIVE,
					iv["end_reason"] or "Giao hồ sơ Golden",
				),
			)

		# 7. Insert standalone noise records (`CRM Call Log`, non-CSKH `CRM Task`, `FCRM Note`)
		call_log_map: dict[str, str] = {}
		for cl_rec in exp["records"]["standalone_external_records"]["standalone_call_logs"]:
			cl_doc = frappe.get_doc(
				{
					"doctype": "CRM Call Log",
					"id": cl_rec["call_log_id"],
					"type": "Outgoing",
					"status": "Completed",
					"from": "19001000",
					"to": "0977000002",
					"duration": 42,
					"reference_doctype": "CRM Lead",
					"reference_docname": lead_by_wi[cl_rec["work_item"]],
				}
			)
			cl_doc.insert(ignore_permissions=True)
			call_log_map[cl_rec["call_log_id"]] = cl_doc.name
			frappe.db.sql(
				"update `tabCRM Call Log` set creation = '2026-10-03 10:00:00', modified = '2026-10-03 10:00:00' where name = %s",
				(cl_doc.name,),
			)

		for ext_t in exp["records"]["standalone_external_records"]["non_cskh_tasks"]:
			t_doc = frappe.get_doc(
				{
					"doctype": "CRM Task",
					"title": f"Task ngoài CSKH {ext_t['task_id']}",
					"status": "Todo",
					"reference_doctype": "CRM Lead",
					"reference_docname": lead_by_wi[ext_t["same_lead_as"]],
					"cskh_is_followup": 0,
				}
			)
			t_doc.insert(ignore_permissions=True)

		if frappe.db.table_exists("FCRM Note"):
			for ext_n in exp["records"]["standalone_external_records"]["standalone_fcrm_notes"]:
				frappe.get_doc(
					{
						"doctype": "FCRM Note",
						"title": ext_n["note_id"],
						"content": "Ghi chú ngoài luồng CSKH",
						"reference_doctype": "CRM Lead",
						"reference_docname": lead_by_wi[ext_n["work_item"]],
					}
				).insert(ignore_permissions=True)

		# 8. Insert 32 CSKH Followup `CRM Task` rows (`CB-001..032`)
		cb_docname_map: dict[str, str] = {}
		cb_by_id: dict[str, dict] = {}
		open_cb_by_wi: dict[str, str] = {}
		prev_fu_flag = getattr(frappe.flags, "cskh_in_followup_service", False)
		frappe.flags.cskh_in_followup_service = True
		try:
			for cb in exp["records"]["callbacks"]:
				cb_by_id[cb["task_id"]] = cb
				t_status = "Canceled" if cb["canceled_at"] else ("Done" if cb["completed_at"] else "Todo")
				t_doc = frappe.get_doc(
					{
						"doctype": "CRM Task",
						"title": f"Callback {cb['task_id']}",
						"status": t_status,
						"assigned_to": cb["assigned_to"],
						"due_date": cb["rescheduled_to"] or cb["due_date"],
						"reference_doctype": "CRM Lead",
						"reference_docname": lead_by_wi[cb["work_item"]],
						"cskh_is_followup": 1,
						"cskh_work_item": cb["work_item"],
						"cskh_campaign": cb["campaign"],
						"cskh_completed_attempt": cb.get("completed_attempt") if t_status == "Done" else None,
						"cskh_completion_reason": (cb.get("cancel_reason") or "Hủy lịch hẹn Golden")
						if t_status == "Canceled"
						else None,
					}
				)
				t_doc.flags.ignore_links = True
				t_doc.insert(ignore_permissions=True)
				cb_docname_map[cb["task_id"]] = t_doc.name
				if t_status == "Todo":
					open_cb_by_wi[cb["work_item"]] = t_doc.name
				frappe.db.sql(
					"update `tabCRM Task` set creation = %s, modified = %s where name = %s",
					(
						cb["created_at"],
						cb["completed_at"] or cb["rescheduled_at"] or cb["canceled_at"] or cb["created_at"],
						t_doc.name,
					),
				)
		finally:
			frappe.flags.cskh_in_followup_service = prev_fu_flag
		for wi_hold_id in on_hold_wis:
			frappe.db.sql(
				"update `tabCSKH Work Item` set status = 'on_hold', open_callback_task = %s where name = %s",
				(open_cb_by_wi.get(wi_hold_id), wi_hold_id),
			)

		# 9. Insert 132 `CSKH Contact Attempt` rows (`ATT-001..132`)
		for att in exp["records"]["attempts"]:
			att_id = att["attempt_id"]
			cl_link = call_log_map.get(att["call_log"]) if att.get("call_log") else None
			frappe.db.sql(
				"""
				insert into `tabCSKH Contact Attempt`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
				 occurred_at, recorded_at, channel, outcome_code,
				 outcome_label_snapshot, outcome_group_snapshot, outcome_version_snapshot,
				 contact_attempt_snapshot, connected_snapshot, interested_snapshot,
				 terminal_snapshot, dnc_snapshot, conversion_snapshot, invalid_phone_snapshot,
				 complete_fields, note, call_log, verification_status,
				 policy_name_snapshot, policy_version_snapshot)
				values (%s, %s, %s, %s, %s, 0, 0,
				        %s, %s, %s, %s, %s, %s,
				        %s, %s, %s, %s,
				        %s, 'engaged', 1,
				        %s, %s, 0,
				        0, %s, %s, %s,
				        %s, %s, %s, %s,
				        'POL-STD', 1)
				""",
				(
					att_id,
					att["recorded_at"],
					att["recorded_at"],
					att["performed_by"],
					att["performed_by"],
					att["work_item"],
					lead_by_wi[att["work_item"]],
					att["campaign"],
					att["interval_id"],
					att["performed_by"],
					att["performed_by"],
					att["occurred_at"],
					att["recorded_at"],
					att["channel"],
					att["outcome_code"],
					att["outcome_code"],
					1 if att["is_contact_attempt"] else 0,
					1 if att["is_connected"] else 0,
					1 if att["is_dnc"] else 0,
					1 if att["is_converted"] else 0,
					1 if att["is_invalid_phone"] else 0,
					1 if att["complete_fields"] else 0,
					att["note"] or "",
					cl_link,
					att["verification_status"],
				),
			)

		# 10. Insert 8 `CSKH Attempt Correction` rows (`CORR-001..008`)
		for corr in exp["records"]["corrections"]:
			corr_id = corr["correction_id"]
			patch: dict = {}
			if corr.get("patched_occurred_at"):
				patch["occurred_at"] = corr["patched_occurred_at"]
			if corr.get("patched_outcome_code"):
				patch["outcome_code"] = corr["patched_outcome_code"]
			if "patched_is_connected" in corr:
				patch["is_connected"] = 1 if corr["patched_is_connected"] else 0
			if "patched_is_converted" in corr:
				patch["is_conversion"] = 1 if corr["patched_is_converted"] else 0
			req_at = (
				(get_datetime(corr["reviewed_at"]) - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
				if corr.get("reviewed_at")
				else "2026-10-14 16:00:00"
			)
			frappe.db.sql(
				"""
				insert into `tabCSKH Attempt Correction`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 work_item, attempt, action, status, requested_by, requested_at,
				 reason, corrected_fields_json, reviewed_by, reviewed_at)
				values (%s, %s, %s, %s, %s, 0, 0,
				        %s, %s, %s, %s, %s, %s,
				        %s, %s, %s, %s)
				""",
				(
					corr_id,
					req_at,
					corr.get("reviewed_at") or req_at,
					admin_u,
					admin_u,
					corr["work_item"],
					corr["attempt_id"],
					corr["action"],
					corr["status"],
					admin_u,
					req_at,
					corr["reason"],
					json.dumps(patch, ensure_ascii=False) if patch else None,
					admin_u if corr.get("reviewed_at") else None,
					corr.get("reviewed_at"),
				),
			)

		# 11. Insert 94 `CSKH SLA Obligation` rows (`OBL-001..094`)
		for ob in exp["records"]["obligations"]:
			ob_id = ob["obligation_id"]
			t_node = team_code_to_node[ob["responsible_team"]]
			iv_obj = iv_map.get(ob["interval_id"] or "")
			iv_end = iv_obj["end_at"] if iv_obj else None
			cb_rec = cb_by_id.get(ob["task_id"]) if ob.get("task_id") else None
			fu_task_docname = cb_docname_map.get(ob["task_id"]) if ob.get("task_id") else None
			resched_seq = (
				1
				if (
					cb_rec
					and cb_rec.get("rescheduled_at")
					and ob["status"] != "superseded"
					and ob["original_due_at"] != cb_rec["due_date"]
				)
				else 0
			)
			fulfilled_att = cb_rec.get("completed_attempt") if (cb_rec and ob.get("fulfilled_at")) else None

			if ob["status"] == "superseded":
				db_status = "superseded"
				closed_at = cb_rec["rescheduled_at"] if cb_rec else ob["start_at"]
			elif ob["waived"]:
				db_status = "waived"
				closed_at = ob["effective_due_at"]
			elif ob["canceled_validly"]:
				db_status = "canceled_validly"
				closed_at = iv_end or (cb_rec["canceled_at"] if cb_rec else ob["start_at"])
			elif ob["fulfilled_at"]:
				db_status = (
					"fulfilled_on_time"
					if get_datetime(ob["fulfilled_at"]) <= get_datetime(ob["effective_due_at"])
					else "fulfilled_late"
				)
				closed_at = ob["fulfilled_at"]
			elif get_datetime(ob["effective_due_at"]) < datetime(2026, 11, 1, 0, 0, 0):
				if iv_end and get_datetime(iv_end) <= datetime(2026, 11, 1, 0, 0, 0):
					db_status = "breached_handoff"
					closed_at = iv_end
				else:
					db_status = "breached_open"
					closed_at = None
			else:
				db_status = "open"
				closed_at = None

			breached_at = (
				ob["effective_due_at"]
				if db_status in ("fulfilled_late", "breached_open", "breached_handoff")
				else None
			)
			waived_at = ob["effective_due_at"] if ob["waived"] else None
			mod_ts = ob["fulfilled_at"] or closed_at or breached_at or ob["start_at"]

			frappe.db.sql(
				"""
				insert into `tabCSKH SLA Obligation`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 obligation_type, work_item, lead, campaign, interval_event, owner_user,
				 team_at_obligation, attribution_status, followup_task, status,
				 informational_only, is_recovery_obligation, reschedule_seq,
				 started_at, original_due_at, effective_due_at, is_paused,
				 fulfilled_at, fulfilled_by_attempt, breached_at, closed_at,
				 waived_by, waived_at, waive_reason, evaluation_anchor_at)
				values (%s, %s, %s, %s, %s, 0, 0,
				        %s, %s, %s, %s, %s, %s,
				        %s, %s, %s, %s,
				        0, 0, %s,
				        %s, %s, %s, 0,
				        %s, %s, %s, %s,
				        %s, %s, %s, %s)
				""",
				(
					ob_id,
					ob["start_at"],
					mod_ts,
					admin_u,
					admin_u,
					ob["type"],
					ob["work_item"],
					lead_by_wi[ob["work_item"]],
					ob["campaign"],
					ob["interval_id"],
					ob["responsible_user"],
					t_node,
					AUTHORITATIVE,
					fu_task_docname,
					db_status,
					resched_seq,
					ob["start_at"],
					ob["original_due_at"],
					ob["effective_due_at"],
					ob["fulfilled_at"],
					fulfilled_att,
					breached_at,
					closed_at,
					ob["waived_by"],
					waived_at,
					ob["waiver_reason"],
					ob["fulfilled_at"] or breached_at or closed_at,
				),
			)

		# 12. Insert 9 `CSKH QA Review` rows (`QA-001..009`)
		att_camp_map = {a["attempt_id"]: a["campaign"] for a in exp["records"]["attempts"]}
		for qr in exp["records"]["qa_reviews"]:
			t_node = team_code_to_node[qr["team_at_review"]]
			frappe.db.sql(
				"""
				insert into `tabCSKH QA Review`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 contact_attempt, work_item, campaign, reviewed_staff, team_at_review,
				 reviewer, status, cohort_type, points_achieved, max_points, passed,
				 rubric_version, feedback_summary, reviewed_at)
				values (%s, %s, %s, %s, %s, 0, 0,
				        %s, %s, %s, %s, %s,
				        %s, 'published', %s, %s, 100, %s,
				        'v1', 'Đánh giá QA Golden', %s)
				""",
				(
					qr["qa_id"],
					qr["reviewed_at"],
					qr["reviewed_at"],
					qr["reviewer"],
					qr["reviewer"],
					qr["attempt_id"],
					qr["work_item"],
					att_camp_map[qr["attempt_id"]],
					qr["reviewed_user"],
					t_node,
					qr["reviewer"],
					qr["cohort_type"],
					100 if qr["passed"] else 40,
					1 if qr["passed"] else 0,
					qr["reviewed_at"],
				),
			)

		frappe.db.commit()
		return {
			"admin": admin_u,
			"bod": bod_u,
			"leader_a": leader_a,
			"leader_b": leader_b,
			"staff_a1": staff_a1,
			"staff_a2": staff_a2,
			"staff_a3": staff_a3,
			"staff_b1": staff_b1,
			"staff_b2": staff_b2,
			"team_a_node": team_a_node,
			"team_b_node": team_b_node,
			"lead_by_wi": lead_by_wi,
		}

	def _load_published_snap(
		self, period_type: str, period_key: str, scope_type: str, scope_id: str, campaign_filter: str = ""
	) -> dict:
		row = frappe.db.get_value(
			"CSKH KPI Snapshot",
			{
				"period_type": period_type,
				"period_key": period_key,
				"campaign_filter": campaign_filter or "",
				"scope_type": scope_type,
				"scope_id": scope_id,
				"is_latest_published": 1,
			},
			"*",
			as_dict=True,
		)
		self.assertIsNotNone(row, f"Missing snapshot for {period_type}:{period_key} {scope_type}:{scope_id}")
		metrics, comps, breakdown = unpack_snapshot_metrics(row.metrics_json)
		row["metrics"] = metrics
		row["score_components"] = comps
		row["attempt_breakdown"] = breakdown
		row["extra"] = unpack_snapshot_extra(row.metrics_json)
		row["exclusion_counts"] = json.loads(row.exclusion_counts_json or "{}")
		return row

	def _assert_scope_matches_t1c_oracle(
		self, snap: dict, oracle: dict, *, label: str, qa_enabled: bool
	) -> None:
		m = snap["metrics"]
		om = oracle["metrics"]

		# 1. Exact count metrics + `denominator is None` (`F-P3-09`)
		for our_code, oracle_key in (
			("ASSIGNED_INTERVALS", "assigned_intervals"),
			("UNIQUE_ASSIGNED_ITEMS", "unique_assigned_items"),
			("CONTACT_ATTEMPT_COUNT", "attempts_count"),
			("CONNECTED_ITEMS", "connected_items"),
			("OVERDUE_BACKLOG", "overdue_backlog"),
			("OPEN_FOLLOWUPS_OVERDUE", "open_followups_overdue"),
			("SLA_BREACH_COUNT", "breach_count"),
			("SLA_WAIVER_COUNT", "sla_waiver_count"),
		):
			cell = m[our_code]
			self.assertEqual(
				cell["value"],
				om[oracle_key],
				f"[{label}] {our_code} expected {om[oracle_key]}, got {cell['value']}",
			)
			self.assertIsNone(
				cell["denominator"], f"[{label}] count metric {our_code} must have denominator=None"
			)

		# 2. Exact rate metrics (`F-P3-01`, `F-P3-02`, `F-P3-03`)
		for our_code, oracle_key in (
			("ELIGIBLE_COVERAGE", "eligible_coverage"),
			("CONNECTED_RATE", "connected_rate"),
			("FIRST_ATTEMPT_SLA_RATE", "first_touch_on_time"),
			("FOLLOWUP_SLA_RATE", "followup_on_time"),
			("VALID_OUTCOME_LOGGING_RATE", "outcome_completeness"),
			("VERIFICATION_RATE", "verification_rate"),
			("CONVERSION_RATE", "conversion_rate"),
		):
			cell = m[our_code]
			exp_cell = om[oracle_key]
			self.assertEqual(
				(cell["numerator"], cell["denominator"], cell["value"], cell["status"]),
				(
					exp_cell["numerator"],
					exp_cell["denominator"],
					exp_cell["value_percent"],
					exp_cell["status"],
				),
				f"[{label}] {our_code} mismatch: got {cell}, expected {exp_cell}",
			)

		# 3. `QA_PASS_RATE` (`disabled` when `qa_enabled=False`, `random_sample` when `qa_enabled=True`)
		qa_cell = m["QA_PASS_RATE"]
		exp_qa = om["qa_pass_rate"]
		if not qa_enabled:
			self.assertIsNone(qa_cell["value"])
			self.assertEqual(qa_cell["status"], "disabled")
		else:
			self.assertEqual(
				(qa_cell["numerator"], qa_cell["denominator"], qa_cell["value"]),
				(exp_qa["numerator"], exp_qa["denominator"], exp_qa["value_percent"]),
				f"[{label}] QA_PASS_RATE mismatch: got {qa_cell}, expected {exp_qa}",
			)

		# 4. `verification_breakdown` (`verified=37, self_reported=86, disputed=6` preserved when `qa_enabled=False`!)
		vb = snap["attempt_breakdown"]
		exp_vb = om["verification_breakdown"]
		if not qa_enabled:
			self.assertEqual(
				(vb["verified"], vb["self_reported"], vb["disputed"]),
				(exp_vb["verified"], exp_vb["self_reported"], exp_vb["disputed"]),
				f"[{label}] verification_breakdown mismatch: got {vb}, expected {exp_vb}",
			)

		# 5. `PROCESS_SCORE` & `0-100` `score_components` scale (`F-P3-03`, `F-P3-09`)
		ps = m["PROCESS_SCORE"]
		exp_ps = oracle["process_score"]
		expected_status = "final" if exp_ps["status"] == "finalized" else exp_ps["status"]
		self.assertEqual(
			(ps["value"], ps["status"], ps["valid_weight"]),
			(exp_ps["value"], expected_status, exp_ps["valid_weight"]),
			f"[{label}] PROCESS_SCORE mismatch: got {ps}, expected {exp_ps}",
		)
		if exp_ps["value"] is not None:
			self.assertAlmostEqual(
				float(ps["raw_weight_rate_product"]),
				float(exp_ps["weighted_points"]),
				places=1,
				msg=f"[{label}] raw_weight_rate_product mismatch",
			)
		for comp in snap["score_components"]:
			if comp["weighted_points"] is not None:
				self.assertGreaterEqual(comp["weighted_points"], 0.0)
				self.assertLessEqual(comp["weighted_points"], float(comp["weight"]))

	def test_t1c_golden_dataset_all_16_scopes_qa_off_and_qa_on_exact_match(self):
		"""`F-P3-01..03, F-P3-09`: Full T1C Golden Dataset (`deb6be25...`) passes all 9 calendar vectors & all 16 scopes with `0` diff."""
		raw_bytes = T1C_GOLDEN_FIXTURE_PATH.read_bytes()
		self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), T1C_GOLDEN_SHA256)
		exp = json.loads(raw_bytes.decode("utf-8"))

		ctx = self._seed_t1c_golden_dataset(exp, qa_enabled=0)

		# Part 1: Verify all 9 `calendar_golden_vectors` (`CAL-01..08`)
		frappe.get_doc(
			{
				"doctype": "CSKH Working Calendar",
				"calendar_code": "CAL-EMPTY-TEST",
				"title": "Empty Calendar",
				"timezone": "Asia/Ho_Chi_Minh",
				"enabled": 1,
				"shifts": [],
			}
		)
		for vec in exp["calendar_golden_vectors"]:
			if vec.get("expected_error") == "EMPTY_CALENDAR":
				with self.assertRaises(InvalidCalendarError):
					WorkingCalendarSpec.from_snapshot(
						{"calendar_code": "EMPTY", "shifts": []}
					).validate_non_empty()
				continue
			cal_spec = resolve_working_calendar(vec["calendar"])
			actual_due = add_business_minutes(
				get_datetime(vec["start_at"]), int(vec["target_minutes"]), cal_spec
			)
			self.assertEqual(
				actual_due.strftime("%Y-%m-%d %H:%M:%S"),
				vec["expected_due_at"],
				f"Calendar vector {vec['case_id']} failed",
			)

		# Part 2: Recompute October 2026 (`qa_enabled = false`) at exact `as_of = 2026-11-01 00:00:00`
		p_oct = resolve_period_spec("monthly", "2026-10")
		oct_as_of = datetime(2026, 11, 1, 0, 0, 0)
		res_oct = execute_kpi_recompute(p_oct, run_mode="full", actor=ctx["admin"], as_of=oct_as_of)
		self.assertEqual(res_oct["status"], "completed")
		self.assertEqual(res_oct["metric_version"], METRIC_VERSION)

		eo = exp["expected_october_2026"]

		# 2a. Company (`all`) + `historical_attribution_unknown == 4` + exact `drilldown_ids`!
		comp_snap = self._load_published_snap("monthly", "2026-10", "company", COMPANY_SCOPE_ID)
		self._assert_scope_matches_t1c_oracle(
			comp_snap, eo["company_qa_disabled"], label="company:qa_off", qa_enabled=False
		)
		self.assertEqual(
			comp_snap["exclusion_counts"]["historical_attribution_unknown"],
			len(eo["historical_attribution_unknown_work_items"]),
		)

		exp_dd = eo["company_qa_disabled"]["metrics"]["drilldown_ids"]
		for mcode, bucket, exp_ids in (
			("FIRST_ATTEMPT_SLA_RATE", "all_denominator", exp_dd["first_touch_eligible_obligations"]),
			("FIRST_ATTEMPT_SLA_RATE", "numerator_only", exp_dd["first_touch_on_time_obligations"]),
			("FOLLOWUP_SLA_RATE", "all_denominator", exp_dd["followup_eligible_obligations"]),
			("OVERDUE_BACKLOG", "all_denominator", exp_dd["overdue_backlog_work_items"]),
			("SLA_BREACH_COUNT", "all_denominator", exp_dd["breached_obligations"]),
			("BREACH_COUNT", "all_denominator", exp_dd["breached_obligations"]),
			("SLA_WAIVER_COUNT", "all_denominator", exp_dd["waived_obligations"]),
		):
			frappe.set_user(ctx["admin"])
			dd_res = p3_reporting.get_metric_drilldown(
				metric_code=mcode,
				period_type="monthly",
				period_key="2026-10",
				scope_type="company",
				filter_bucket=bucket,
				page=1,
				page_size=100,
			)
			got_ids = sorted(r["record_id"] for r in dd_res["rows"])
			self.assertEqual(got_ids, sorted(exp_ids), f"Drilldown mismatch for {mcode}/{bucket}")

		# 2b. Teams (`TEAM-A`, `TEAM-B`)
		for t_code, t_node in (("TEAM-A", ctx["team_a_node"]), ("TEAM-B", ctx["team_b_node"])):
			t_snap = self._load_published_snap("monthly", "2026-10", "team", t_node)
			self._assert_scope_matches_t1c_oracle(
				t_snap, eo["teams_qa_disabled"][t_code], label=f"{t_code}:qa_off", qa_enabled=False
			)

		# 2c. Employees (`staff.a1..b2`) with `qa_enabled = false`
		for emp_id, emp_oracle in eo["employees_qa_disabled"].items():
			e_snap = self._load_published_snap("monthly", "2026-10", "employee", emp_id)
			self._assert_scope_matches_t1c_oracle(
				e_snap, emp_oracle, label=f"{emp_id}:qa_off", qa_enabled=False
			)

		# 2d. Campaigns (`CAMP-P3-STD`, `CAMP-P3-VIP`, `CAMP-P3-PAUSE`)
		for camp_id, camp_oracle in eo["campaigns_qa_disabled"].items():
			c_snap = self._load_published_snap("monthly", "2026-10", "campaign", camp_id)
			self._assert_scope_matches_t1c_oracle(
				c_snap, camp_oracle, label=f"{camp_id}:qa_off", qa_enabled=False
			)

		# Part 3: Enable QA (`qa_enabled = 1`) and verify all 5 employee scopes (`85.50` final on `staff.a1`, `78.31` provisional on `staff.a2`)
		frappe.set_user("Administrator")
		for pol_name in ("GLOBAL-DEFAULT", "POL-VIP-NIGHT"):
			frappe.db.set_value("CSKH Policy", pol_name, "qa_enabled", 1, update_modified=False)
		frappe.db.commit()

		res_oct_qa = execute_kpi_recompute(p_oct, run_mode="full", actor=ctx["admin"], as_of=oct_as_of)
		self.assertEqual(res_oct_qa["status"], "completed")
		for emp_id, emp_oracle in eo["employees_qa_enabled"].items():
			e_snap_qa = self._load_published_snap("monthly", "2026-10", "employee", emp_id)
			self._assert_scope_matches_t1c_oracle(
				e_snap_qa, emp_oracle, label=f"{emp_id}:qa_on", qa_enabled=True
			)
		# Verify `staff.a2` `qa_cohort_breakdown` separates `random_sample = 2` from `risk_targeted = 2`
		a2_snap = self._load_published_snap("monthly", "2026-10", "employee", "staff.a2@t1cp3.local")
		qa_bd = a2_snap["extra"]["qa_cohort_breakdown"]
		self.assertEqual(qa_bd["random_sample"]["count"], 2)
		self.assertEqual(qa_bd["risk_targeted"]["count"], 2)

		# Part 4: Multi-day `DISTINCT` rollup proof (`TEAM-B`, `2026-10-06..08`: `5 + 5 + 7 = 17`, multi-day = `7`)
		rollup_exp = eo["rollup_distinct_proof_team_b_oct06_to_oct08"]
		daily_sum = 0
		for d_str, exp_cnt in rollup_exp["daily_connected_items"].items():
			p_d = resolve_period_spec("daily", d_str)
			execute_kpi_recompute(p_d, run_mode="full", actor=ctx["admin"], as_of=oct_as_of)
			d_snap = self._load_published_snap("daily", d_str, "team", ctx["team_b_node"])
			self.assertEqual(d_snap["metrics"]["CONNECTED_ITEMS"]["value"], exp_cnt)
			daily_sum += d_snap["metrics"]["CONNECTED_ITEMS"]["value"]
		self.assertEqual(daily_sum, rollup_exp["sum_of_daily_distinct"])

		p_range = resolve_period_spec("custom", from_date="2026-10-06", to_date="2026-10-08")
		execute_kpi_recompute(p_range, run_mode="full", actor=ctx["admin"], as_of=oct_as_of)
		r_snap = self._load_published_snap("custom", p_range.period_key, "team", ctx["team_b_node"])
		self.assertEqual(
			r_snap["metrics"]["CONNECTED_ITEMS"]["value"], rollup_exp["multi_day_period_distinct"]
		)

		# Part 5: September 2026 restatement proof (`WI-024`, `CORR-003`)
		sep_exp = eo["september_restatement_proof_staff_a2"]
		p_sep = resolve_period_spec("monthly", "2026-09")
		sep_before = execute_kpi_recompute(
			p_sep,
			run_mode="full",
			actor=ctx["admin"],
			as_of=get_datetime(sep_exp["before_oct10_correction"]["as_of"]),
		)
		s_before = self._load_published_snap("monthly", "2026-09", "employee", "staff.a2@t1cp3.local")
		self.assertEqual(
			s_before["metrics"]["CONNECTED_ITEMS"]["value"],
			sep_exp["before_oct10_correction"]["connected_items"],
		)
		self.assertEqual(
			s_before["metrics"]["CONVERSION_RATE"]["numerator"],
			sep_exp["before_oct10_correction"]["conversion_numerator"],
		)

		sep_after = execute_kpi_recompute(
			p_sep,
			run_mode="restated",
			trigger_reason="prior_period_correction",
			trigger_reference="CORR-003",
			actor=ctx["admin"],
			as_of=get_datetime(sep_exp["after_oct10_correction"]["as_of"]),
		)
		self.assertEqual(sep_after["run_mode"], "restated")
		self.assertEqual(sep_after["restated_from_run"], sep_before["run_id"])
		s_after = self._load_published_snap("monthly", "2026-09", "employee", "staff.a2@t1cp3.local")
		self.assertEqual(
			s_after["metrics"]["CONNECTED_ITEMS"]["value"],
			sep_exp["after_oct10_correction"]["connected_items"],
		)
		self.assertEqual(
			s_after["metrics"]["CONVERSION_RATE"]["numerator"],
			sep_exp["after_oct10_correction"]["conversion_numerator"],
		)
		# Verify prior `s_before` snapshot row was NOT mutated in place!
		old_row = frappe.db.get_value(
			"CSKH KPI Snapshot", s_before["name"], ["is_latest_published", "metrics_json"], as_dict=True
		)
		self.assertEqual(cint(old_row.is_latest_published), 0)
		old_metrics, _, _ = unpack_snapshot_metrics(old_row.metrics_json)
		self.assertEqual(old_metrics["CONNECTED_ITEMS"]["value"], 0)

	def test_f_p3_04_leader_team_as_of_slice_drilldown_sla_qa_and_closed_wi_pii(self):
		"""`F-P3-04`: `Leader A` keeps `TEAM-A` historical slice of transferred `staff.a3`; `Leader B` gets `0` `TEAM-A` KPI/SLA/QA/closed-WI PII."""
		exp = json.loads(T1C_GOLDEN_FIXTURE_PATH.read_text(encoding="utf-8"))
		ctx = self._seed_t1c_golden_dataset(exp, qa_enabled=1)

		# Attach a private `TEAM-A` campaign & secret PII on `WI-027` (`staff.a3` closed while in `TEAM-A` on `02/10`)
		frappe.set_user("Administrator")
		priv_camp = frappe.get_doc(
			{
				"doctype": "CSKH Campaign",
				"title": "Chiến dịch Bí mật Đội A",
				"code": "CAMP-PRIV-TEAMA",
				"status": "Active",
				"assignment_mode": "weighted_round_robin",
				"start_date": "2026-09-01",
				"end_date": "2026-12-31",
				"team_scope": ctx["team_a_node"],
			}
		).insert(ignore_permissions=True, set_name="CAMP-PRIV-TEAMA")

		lead_wi27 = ctx["lead_by_wi"]["WI-027"]
		frappe.db.set_value(
			"CRM Lead",
			lead_wi27,
			{
				"first_name": "Khách VIP Bí Mật Đội A",
				"lead_name": "Khách VIP Bí Mật Đội A",
				"mobile_no": "0919888777",
				"lead_owner": None,
			},
			update_modified=False,
		)
		frappe.db.set_value(
			"CSKH Work Item",
			"WI-027",
			{"campaign": priv_camp.name, "status": "closed", "assigned_to": None},
			update_modified=False,
		)
		frappe.db.sql(
			"update `tabCSKH SLA Obligation` set campaign = %s where work_item = 'WI-027'",
			(priv_camp.name,),
		)
		frappe.db.sql(
			"update `tabCSKH Contact Attempt` set campaign = %s, note = %s where work_item = 'WI-027'",
			(priv_camp.name, "SENTINEL_NOTE_PRIVATE_TEAM_A_ONLY"),
		)
		with authorized_qa_write():
			frappe.db.sql(
				"""
				insert into `tabCSKH QA Review`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 contact_attempt, work_item, campaign, reviewed_staff, team_at_review,
				 reviewer, status, cohort_type, points_achieved, max_points, passed,
				 rubric_version, feedback_summary, reviewed_at)
				values ('QA-TEAMA-A3', '2026-10-10 10:00:00', '2026-10-10 10:00:00', %s, %s, 0, 0,
				        'ATT-046', 'WI-027', %s, %s, %s,
				        %s, 'published', 'random_sample', 100, 100, 1,
				        'v1', 'QA nội bộ Đội A', '2026-10-10 10:00:00')
				""",
				(
					ctx["admin"],
					ctx["admin"],
					priv_camp.name,
					ctx["staff_a3"],
					ctx["team_a_node"],
					ctx["leader_a"],
				),
			)
		frappe.db.commit()

		p_oct = resolve_period_spec("monthly", "2026-10")
		execute_kpi_recompute(
			p_oct, run_mode="full", actor=ctx["admin"], as_of=datetime(2026, 11, 1, 0, 0, 0)
		)

		# 1. `Leader A` MUST see `staff.a3`'s `TEAM-A` historical slice (`6` intervals, `15` attempts) and NOT `TEAM-B`!
		frappe.set_user(ctx["leader_a"])
		la_emp = p3_reporting.get_employee_quality(
			period_type="monthly",
			period_key="2026-10",
			employee=ctx["staff_a3"],
		)
		self.assertEqual(la_emp["pagination"]["total"], 1)
		la_a3 = la_emp["rows"][0]
		self.assertEqual(la_a3["intervals_count"], 6)
		self.assertEqual(la_a3["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 15)
		self.assertEqual(la_a3["team_id"], ctx["team_a_node"])

		la_dd = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key="2026-10",
			scope_type="employee",
			scope_id=ctx["staff_a3"],
			page_size=50,
		)
		self.assertEqual(la_dd["pagination"]["total"], 15)
		self.assertTrue(all(r["team_id"] == ctx["team_a_node"] for r in la_dd["rows"]))

		# 2. `Leader B` MUST see ONLY `staff.a3`'s `TEAM-B` slice (`6` intervals, `15` attempts) and `0` `TEAM-A` PII!
		frappe.set_user(ctx["leader_b"])
		lb_emp = p3_reporting.get_employee_quality(
			period_type="monthly",
			period_key="2026-10",
			employee=ctx["staff_a3"],
		)
		self.assertEqual(lb_emp["pagination"]["total"], 1)
		lb_a3 = lb_emp["rows"][0]
		self.assertEqual(lb_a3["intervals_count"], 6)
		self.assertEqual(lb_a3["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 15)
		self.assertEqual(lb_a3["team_id"], ctx["team_b_node"])

		lb_dd_att = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key="2026-10",
			scope_type="employee",
			scope_id=ctx["staff_a3"],
			page_size=50,
		)
		self.assertEqual(lb_dd_att["pagination"]["total"], 15)
		dump_lb_att = json.dumps(lb_dd_att, ensure_ascii=False)
		for forbidden_sentinel in (
			"CAMP-PRIV-TEAMA",
			"Khách VIP Bí Mật Đội A",
			"0919888777",
			"SENTINEL_NOTE_PRIVATE_TEAM_A_ONLY",
			"WI-027",
		):
			self.assertNotIn(forbidden_sentinel, dump_lb_att)

		# Search filter-oracle check: `Leader B` searching for `TEAM-A` secret customer name or note gets `0` hits!
		lb_oracle = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key="2026-10",
			scope_type="employee",
			scope_id=ctx["staff_a3"],
			search="SENTINEL_NOTE_PRIVATE",
			page_size=50,
		)
		self.assertEqual(lb_oracle["pagination"]["total"], 0)

		lb_dd_sla = p3_reporting.get_metric_drilldown(
			metric_code="FIRST_ATTEMPT_SLA_RATE",
			period_type="monthly",
			period_key="2026-10",
			scope_type="employee",
			scope_id=ctx["staff_a3"],
			page_size=50,
		)
		self.assertTrue(all(r["team_id"] == ctx["team_b_node"] for r in lb_dd_sla["rows"]))

		# `sla.list_obligations` & `qa.list_reviews` & `reporting.work_item_detail` & generic `has_permission`
		lb_sla_list = sla_api.list_obligations(owner_user=ctx["staff_a3"], page_size=100)
		self.assertTrue(all(r["team_at_obligation"] == ctx["team_b_node"] for r in lb_sla_list["rows"]))

		lb_qa_list = qa_api.list_reviews(reviewed_staff=ctx["staff_a3"], page_size=50)
		self.assertEqual(lb_qa_list["pagination"]["total"], 0)

		wi27_doc = frappe.get_doc("CSKH Work Item", "WI-027")
		self.assertFalse(
			frappe.has_permission("CSKH Work Item", ptype="read", doc=wi27_doc, user=ctx["leader_b"])
		)
		with self.assertRaises((Forbidden, ForbiddenScope, frappe.PermissionError)):
			read_model.get_work_item_detail(work_item="WI-027", actor=ctx["leader_b"])
		pool_lb = reporting.customer_pool(user=ctx["leader_b"], search="0919888777")
		self.assertEqual(pool_lb["total"], 0)

		# Positive control: `Leader A` CAN still read `WI-027` (`closed` in `TEAM-A`)
		self.assertTrue(
			frappe.has_permission("CSKH Work Item", ptype="read", doc=wi27_doc, user=ctx["leader_a"])
		)
		wi27_la = read_model.get_work_item_detail(work_item="WI-027", actor=ctx["leader_a"])
		self.assertEqual(wi27_la["work_item"]["name"], "WI-027")
		pool_la = reporting.customer_pool(user=ctx["leader_a"], search="0919888777")
		self.assertEqual(pool_la["total"], 1)

	def test_f_p3_05_export_file_query_get_download_revocation_ttl_and_kpi_run_leader_403(self):
		"""`F-P3-05`: `Leader` blocked `403` on company `CSKH KPI Run`; export `File` blocked across list/doc/download on revocation & expiry."""
		frappe.set_user("Administrator")
		camp = self.make_campaign(team_scope=None)
		self.make_rule(camp, self.staff_a, weight=100)
		wi_a, _ = self.make_assigned_work_item(camp, self.staff_a)
		contact_attempt.record_attempt(
			work_item=wi_a,
			channel="phone_call",
			outcome_code="no_answer",
			note="Kiểm thử export & KPI run",
			idempotency_key=f"f05-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)

		month_key = today_hcm().strftime("%Y-%m")
		period = resolve_period_spec("monthly", month_key)
		comp_run = execute_kpi_recompute(period, run_mode="full", actor=self.admin_user)
		comp_run_id = comp_run["run_id"]
		comp_run_doc = frappe.get_doc("CSKH KPI Run", comp_run_id)

		# Create a team-scoped `CSKH KPI Run` for `self.team_a` (positive control for `leader_a`)
		with authorized_kpi_write():
			team_run_doc = frappe.get_doc(
				{
					"doctype": "CSKH KPI Run",
					"period_type": "monthly",
					"period_key": month_key,
					"from_date": period.from_date,
					"to_date": period.to_date,
					"target_scope_type": "team",
					"target_scope_id": self.team_a,
					"run_mode": "incremental",
					"status": "completed",
					"is_latest_published": 0,
					"metric_version": METRIC_VERSION,
					"as_of": now_datetime(),
					"requested_by": self.leader_a,
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()

		# 1. `Leader A` denied `403` on company `CSKH KPI Run`, allowed `200` on `team_a` `CSKH KPI Run`
		frappe.set_user(self.leader_a)
		with self.assertRaises(ForbiddenScope):
			get_kpi_run_status(comp_run_id, actor=self.leader_a)
		self.assertFalse(
			frappe.has_permission("CSKH KPI Run", ptype="read", doc=comp_run_doc, user=self.leader_a)
		)
		visible_runs_la = frappe.get_list("CSKH KPI Run", pluck="name")
		self.assertNotIn(comp_run_id, visible_runs_la)
		self.assertIn(team_run_doc.name, visible_runs_la)
		ok_team_run = get_kpi_run_status(team_run_doc.name, actor=self.leader_a)
		self.assertEqual(ok_team_run["run_id"], team_run_doc.name)

		# Positive control: `BOD` & `Admin` CAN read company `CSKH KPI Run`
		self.assertEqual(get_kpi_run_status(comp_run_id, actor=self.bod_user)["run_id"], comp_run_id)

		# 2. Export `File` query, single-doc, & download lock across revocation and TTL expiry!
		req_exp = request_export(
			report_type="team_comparison",
			period_type="monthly",
			period_key=month_key,
			scope_type="team",
			scope_id=self.team_a,
			actor=self.leader_a,
		)
		exp_id = req_exp["export_id"]
		frappe.set_user("Administrator")
		w_res = execute_export_job_now(exp_id)
		self.assertEqual(w_res["status"], "completed")
		file_id = frappe.db.get_value("CSKH Export Job", exp_id, "file_doc")
		file_doc = frappe.get_doc("File", file_id)
		job_doc = frappe.get_doc("CSKH Export Job", exp_id)

		# Positive control while active
		frappe.set_user(self.leader_a)
		self.assertIn(
			file_id,
			frappe.get_list("File", filters={"attached_to_doctype": "CSKH Export Job"}, pluck="name"),
		)
		self.assertTrue(frappe.has_permission("File", ptype="read", doc=file_doc, user=self.leader_a))
		self.assertTrue(is_export_job_currently_authorized(job_doc.as_dict(), self.leader_a)[0])

		# Cross-team `Leader B` & `Staff A` blocked on list + single-doc
		for intr in (self.leader_b, self.staff_a):
			frappe.set_user(intr)
			self.assertEqual(
				frappe.get_list("File", filters={"name": file_id}, pluck="name"),
				[],
			)
			self.assertFalse(frappe.has_permission("File", ptype="read", doc=file_doc, user=intr))

		# Role revocation -> `get_list("File")` returns `[]` (`0` count), `has_permission("File")` is `False`, `download_export` raises `403`!
		frappe.set_user("Administrator")
		u_la = frappe.get_doc("User", self.leader_a)
		u_la.remove_roles("Sales Manager")
		frappe.db.commit()
		frappe.clear_cache(user=self.leader_a)
		try:
			frappe.set_user(self.leader_a)
			self.assertEqual(
				frappe.get_list("File", filters={"name": file_id}, pluck="name"),
				[],
			)
			self.assertFalse(frappe.has_permission("File", ptype="read", doc=file_doc, user=self.leader_a))
			with self.assertRaises(ForbiddenScope):
				download_export(exp_id, actor=self.leader_a)
		finally:
			frappe.set_user("Administrator")
			u_la = frappe.get_doc("User", self.leader_a)
			u_la.add_roles("Sales Manager")
			frappe.db.commit()
			frappe.clear_cache(user=self.leader_a)

		# TTL expiry on a fresh export job -> `get_list("File")` returns `[]`, `has_permission("File")` is `False`, `download_export` raises `410 ExportExpired`!
		frappe.set_user(self.leader_a)
		req_exp_ttl = request_export(
			report_type="team_comparison",
			period_type="monthly",
			period_key=month_key,
			scope_type="team",
			scope_id=self.team_a,
			actor=self.leader_a,
		)
		exp_ttl_id = req_exp_ttl["export_id"]
		frappe.set_user("Administrator")
		w_ttl_res = execute_export_job_now(exp_ttl_id)
		self.assertEqual(w_ttl_res["status"], "completed")
		file_ttl_id = frappe.db.get_value("CSKH Export Job", exp_ttl_id, "file_doc")
		file_ttl_doc = frappe.get_doc("File", file_ttl_id)
		frappe.db.set_value(
			"CSKH Export Job",
			exp_ttl_id,
			"expires_at",
			now_datetime() - timedelta(hours=2),
			update_modified=False,
		)
		frappe.db.commit()
		frappe.set_user(self.leader_a)
		self.assertEqual(
			frappe.get_list("File", filters={"name": file_ttl_id}, pluck="name"),
			[],
		)
		self.assertFalse(frappe.has_permission("File", ptype="read", doc=file_ttl_doc, user=self.leader_a))
		with self.assertRaises(ExportExpired):
			download_export(exp_ttl_id, actor=self.leader_a)

	def test_f_p3_02_deadline_cohort_part_a_late_call_and_part_b_early_call_invariants(self):
		"""`F-P3-02`: Late call in `M+1` never erases breach in `M`; early call in `M-1` counts on-time in `M`'s deadline cohort."""
		frappe.set_user("Administrator")
		camp = self.make_campaign(team_scope=None)

		# Part A: Assigned `2026-07-31 10:00`, due `2026-07-31 15:00`, fulfilled LATE on `2026-08-03 10:00`
		lead_a = self.make_canonical_lead(first_name="PartA Late")
		wi_a = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"lead": lead_a,
				"campaign": camp,
				"status": "in_progress",
				"assigned_to": self.staff_a,
				"assigned_by": "Administrator",
				"assigned_at": "2026-07-31 10:00:00",
			}
		).insert(ignore_permissions=True)
		frappe.db.sql(
			"""
			insert into `tabCSKH Assignment History`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 work_item, action, to_user, actor, event_at,
			 ownership_start, team_at_assignment, attribution_status, reason)
			values ('IV-F02-A', '2026-07-31 10:00:00', '2026-07-31 10:00:00', 'Administrator', 'Administrator', 0, 0,
			        %s, 'assign', %s, 'Administrator', '2026-07-31 10:00:00',
			        '2026-07-31 10:00:00', %s, 'authoritative', 'Part A')
			""",
			(wi_a.name, self.staff_a, self.team_a),
		)
		frappe.db.sql(
			"""
			insert into `tabCSKH Contact Attempt`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
			 occurred_at, recorded_at, channel, outcome_code, contact_attempt_snapshot,
			 connected_snapshot, complete_fields, verification_status)
			values ('ATT-F02-A', '2026-08-03 10:00:00', '2026-08-03 10:00:00', %s, %s, 0, 0,
			        %s, %s, %s, 'IV-F02-A', %s, %s,
			        '2026-08-03 10:00:00', '2026-08-03 10:00:00', 'phone_call', 'no_answer', 1,
			        0, 1, 'self_reported')
			""",
			(self.staff_a, self.staff_a, wi_a.name, lead_a, camp, self.staff_a, self.staff_a),
		)
		frappe.db.sql(
			"""
			insert into `tabCSKH SLA Obligation`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 obligation_type, work_item, lead, campaign, interval_event, owner_user,
			 team_at_obligation, attribution_status, status, started_at, original_due_at,
			 effective_due_at, fulfilled_at, fulfilled_by_attempt, breached_at, evaluation_anchor_at)
			values ('OB-F02-A', '2026-07-31 10:00:00', '2026-08-03 10:00:00', 'Administrator', 'Administrator', 0, 0,
			        'FIRST_ATTEMPT', %s, %s, %s, 'IV-F02-A', %s,
			        %s, 'authoritative', 'fulfilled_late', '2026-07-31 10:00:00', '2026-07-31 15:00:00',
			        '2026-07-31 15:00:00', '2026-08-03 10:00:00', 'ATT-F02-A', '2026-07-31 15:00:00', '2026-08-03 10:00:00')
			""",
			(wi_a.name, lead_a, camp, self.staff_a, self.team_a),
		)

		# Part B: Assigned `2026-07-31 16:30`, due `2026-08-01 09:30`, fulfilled EARLY on `2026-07-31 16:45`
		lead_b = self.make_canonical_lead(first_name="PartB Early")
		wi_b = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"lead": lead_b,
				"campaign": camp,
				"status": "in_progress",
				"assigned_to": self.staff_b,
				"assigned_by": "Administrator",
				"assigned_at": "2026-07-31 16:30:00",
			}
		).insert(ignore_permissions=True)
		frappe.db.sql(
			"""
			insert into `tabCSKH Assignment History`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 work_item, action, to_user, actor, event_at,
			 ownership_start, team_at_assignment, attribution_status, reason)
			values ('IV-F02-B', '2026-07-31 16:30:00', '2026-07-31 16:30:00', 'Administrator', 'Administrator', 0, 0,
			        %s, 'assign', %s, 'Administrator', '2026-07-31 16:30:00',
			        '2026-07-31 16:30:00', %s, 'authoritative', 'Part B')
			""",
			(wi_b.name, self.staff_b, self.team_a),
		)
		frappe.db.sql(
			"""
			insert into `tabCSKH Contact Attempt`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 work_item, lead, campaign, ownership_event, owner_at_attempt, performed_by,
			 occurred_at, recorded_at, channel, outcome_code, contact_attempt_snapshot,
			 connected_snapshot, complete_fields, verification_status)
			values ('ATT-F02-B', '2026-07-31 16:45:00', '2026-07-31 16:45:00', %s, %s, 0, 0,
			        %s, %s, %s, 'IV-F02-B', %s, %s,
			        '2026-07-31 16:45:00', '2026-07-31 16:45:00', 'phone_call', 'connected_interested', 1,
			        1, 1, 'verified')
			""",
			(self.staff_b, self.staff_b, wi_b.name, lead_b, camp, self.staff_b, self.staff_b),
		)
		frappe.db.sql(
			"""
			insert into `tabCSKH SLA Obligation`
			(name, creation, modified, modified_by, owner, docstatus, idx,
			 obligation_type, work_item, lead, campaign, interval_event, owner_user,
			 team_at_obligation, attribution_status, status, started_at, original_due_at,
			 effective_due_at, fulfilled_at, fulfilled_by_attempt, evaluation_anchor_at)
			values ('OB-F02-B', '2026-07-31 16:30:00', '2026-07-31 16:45:00', 'Administrator', 'Administrator', 0, 0,
			        'FIRST_ATTEMPT', %s, %s, %s, 'IV-F02-B', %s,
			        %s, 'authoritative', 'fulfilled_on_time', '2026-07-31 16:30:00', '2026-08-01 09:30:00',
			        '2026-08-01 09:30:00', '2026-07-31 16:45:00', 'ATT-F02-B', '2026-07-31 16:45:00')
			""",
			(wi_b.name, lead_b, camp, self.staff_b, self.team_a),
		)
		frappe.db.commit()

		# Recompute BOTH July and August at `as_of = 2026-08-05 12:00:00` (AFTER the August 3rd late call!)
		as_of_aug5 = datetime(2026, 8, 5, 12, 0, 0)
		p_jul = resolve_period_spec("monthly", "2026-07")
		p_aug = resolve_period_spec("monthly", "2026-08")
		execute_kpi_recompute(p_jul, run_mode="full", actor=self.admin_user, as_of=as_of_aug5)
		execute_kpi_recompute(p_aug, run_mode="full", actor=self.admin_user, as_of=as_of_aug5)

		jul_staff_a = self._load_published_snap("monthly", "2026-07", "employee", self.staff_a)
		self.assertEqual(jul_staff_a["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["numerator"], 0)
		self.assertEqual(jul_staff_a["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["denominator"], 1)
		self.assertEqual(jul_staff_a["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["value"], 0.0)
		self.assertEqual(jul_staff_a["metrics"]["SLA_BREACH_COUNT"]["value"], 1)

		aug_staff_a = self._load_published_snap("monthly", "2026-08", "employee", self.staff_a)
		self.assertEqual(aug_staff_a["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["denominator"], 0)
		self.assertIsNone(aug_staff_a["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["value"])

		aug_staff_b = self._load_published_snap("monthly", "2026-08", "employee", self.staff_b)
		self.assertEqual(aug_staff_b["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["numerator"], 1)
		self.assertEqual(aug_staff_b["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["denominator"], 1)
		self.assertEqual(aug_staff_b["metrics"]["FIRST_ATTEMPT_SLA_RATE"]["value"], 100.0)

	def test_f_p3_06_metric_version_v1_to_v2_append_only_invalidation_and_restatement(self):
		"""`F-P3-06`: Stale `cskh-metrics-v1` snapshots are invalidated via `CSKH Dirty Scope` and restated append-only to `v2`."""
		frappe.set_user("Administrator")
		p_jun = resolve_period_spec("monthly", "2026-06")
		with authorized_kpi_write():
			v1_run = frappe.get_doc(
				{
					"doctype": "CSKH KPI Run",
					"period_type": "monthly",
					"period_key": "2026-06",
					"from_date": p_jun.from_date,
					"to_date": p_jun.to_date,
					"target_scope_type": "all",
					"target_scope_id": "",
					"run_mode": "full",
					"status": "completed",
					"is_latest_published": 1,
					"metric_version": "cskh-metrics-v1",
					"as_of": datetime(2026, 7, 1, 0, 0, 0),
					"requested_by": "Administrator",
				}
			).insert(ignore_permissions=True)
			v1_snap = frappe.get_doc(
				{
					"doctype": "CSKH KPI Snapshot",
					"kpi_run": v1_run.name,
					"period_type": "monthly",
					"period_key": "2026-06",
					"from_date": p_jun.from_date,
					"to_date": p_jun.to_date,
					"campaign_filter": "",
					"scope_type": "company",
					"scope_id": COMPANY_SCOPE_ID,
					"as_of": datetime(2026, 7, 1, 0, 0, 0),
					"is_latest_published": 1,
					"metric_version": "cskh-metrics-v1",
					"metrics_json": json.dumps({"metrics": {"LEGACY_DUMMY": 1}}, ensure_ascii=False),
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()

		inv_res = invalidate_stale_metric_version_snapshots()
		self.assertGreaterEqual(inv_res, 1)
		dirty_cnt = frappe.db.count(
			"CSKH Dirty Scope",
			{"period_month": "2026-06", "reason": "metric_version_upgrade", "status": "pending"},
		)
		self.assertGreaterEqual(dirty_cnt, 1)

		# Querying `get_executive_overview` automatically restates `2026-06` to `cskh-metrics-v2` without mutating `v1_snap`!
		frappe.set_user(self.bod_user)
		ov = p3_reporting.get_executive_overview(period_type="monthly", period_key="2026-06")
		self.assertEqual(ov["freshness"]["metric_version"], METRIC_VERSION)
		self.assertTrue(ov["freshness"]["is_restated"])
		self.assertEqual(ov["freshness"]["restated_from_run"], v1_run.name)

		v1_snap_db = frappe.db.get_value(
			"CSKH KPI Snapshot",
			v1_snap.name,
			["metric_version", "is_latest_published", "metrics_json"],
			as_dict=True,
		)
		self.assertEqual(v1_snap_db.metric_version, "cskh-metrics-v1")
		self.assertEqual(cint(v1_snap_db.is_latest_published), 0)
		self.assertIn("LEGACY_DUMMY", v1_snap_db.metrics_json)

	def test_f_p3_07_campaign_pause_resume_close_sla_lifecycle_and_audit(self):
		"""`F-P3-07`: `CSKH Campaign` `Active -> Paused -> Active -> Closed` freezes/resumes/cancels open SLA obligations cleanly."""
		frappe.set_user("Administrator")
		camp = self.make_campaign(team_scope=None)
		self.make_rule(camp, self.staff_a, weight=100)
		wi_open, _ = self.make_assigned_work_item(camp, self.staff_a)
		wi_breached, _ = self.make_assigned_work_item(camp, self.staff_a)

		ob_open_id = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_open, "obligation_type": "FIRST_ATTEMPT"},
			"name",
		)
		ob_breach_id = frappe.db.get_value(
			"CSKH SLA Obligation",
			{"work_item": wi_breached, "obligation_type": "FIRST_ATTEMPT"},
			"name",
		)
		orig_due = frappe.db.get_value("CSKH SLA Obligation", ob_open_id, "original_due_at")

		# Mark `ob_breach_id` as already `breached_open` before campaign pause/close
		with authorized_sla_write():
			frappe.db.set_value(
				"CSKH SLA Obligation",
				ob_breach_id,
				{"status": "breached_open", "breached_at": now_datetime() - timedelta(minutes=20)},
				update_modified=False,
			)

		# 1. Pause campaign -> `ob_open_id` freezes (`is_paused = 1`)
		camp_doc = frappe.get_doc("CSKH Campaign", camp)
		camp_doc.status = "Paused"
		camp_doc.save(ignore_permissions=True)
		ob_paused = frappe.db.get_value(
			"CSKH SLA Obligation",
			ob_open_id,
			["is_paused", "paused_at", "audit_events_json"],
			as_dict=True,
		)
		self.assertEqual(cint(ob_paused.is_paused), 1)
		self.assertIn("campaign_paused", ob_paused.audit_events_json or "")

		# Simulate 15 business minutes elapsed during pause (rewind `paused_at` inside a Monday shift)
		with authorized_sla_write():
			frappe.db.set_value(
				"CSKH SLA Obligation",
				ob_open_id,
				{
					"started_at": "2026-11-02 09:00:00",
					"original_due_at": "2026-11-02 10:00:00",
					"effective_due_at": "2026-11-02 10:00:00",
					"paused_at": "2026-11-02 09:15:00",
				},
				update_modified=False,
			)
		orig_due_mon = get_datetime("2026-11-02 10:00:00")

		# 2. Resume campaign (`Active`) -> `is_paused = 0`, `effective_due_at > original_due_at`, `original_due_at` unchanged!
		camp_doc.reload()
		camp_doc.status = "Active"
		camp_doc.save(ignore_permissions=True)
		ob_resumed = frappe.db.get_value(
			"CSKH SLA Obligation",
			ob_open_id,
			["is_paused", "original_due_at", "effective_due_at", "audit_events_json"],
			as_dict=True,
		)
		self.assertEqual(cint(ob_resumed.is_paused), 0)
		self.assertEqual(get_datetime(ob_resumed.original_due_at), orig_due_mon)
		self.assertGreater(get_datetime(ob_resumed.effective_due_at), orig_due_mon)
		self.assertIn("campaign_resumed", ob_resumed.audit_events_json or "")
		_ = orig_due

		# 3. Close campaign (`Closed`): P1 blocks closing while Work Items remain active; once Work Items are resolved,
		#    any remaining unbreached `ob_open_id` becomes `canceled_validly` while `ob_breach_id` keeps `breached_open`!
		camp_doc.reload()
		camp_doc.status = "Closed"
		with self.assertRaises(frappe.ValidationError):
			camp_doc.save(ignore_permissions=True)

		frappe.db.set_value("CSKH Work Item", wi_open, "status", "canceled", update_modified=False)
		frappe.db.set_value("CSKH Work Item", wi_breached, "status", "canceled", update_modified=False)
		camp_doc.reload()
		camp_doc.status = "Closed"
		camp_doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("CSKH SLA Obligation", ob_open_id, "status"), "canceled_validly")
		self.assertEqual(
			frappe.db.get_value("CSKH SLA Obligation", ob_breach_id, "status"), "breached_handoff"
		)
		self.assertIsNotNone(frappe.db.get_value("CSKH SLA Obligation", ob_breach_id, "breached_at"))

	def test_f_p3_08_unified_site_kill_switch_split_flags_and_queued_job_guards(self):
		"""`F-P3-08`: Unified `p3_flags.py` governs UI, API, SLA hooks, export & queued RQ workers; catch-up logs preserved."""
		frappe.set_user("Administrator")
		camp = self.make_campaign(team_scope=None)
		self.make_rule(camp, self.staff_a, weight=100)
		month_key = today_hcm().strftime("%Y-%m")

		# Pre-queue an export job BEFORE flipping kill switch!
		frappe.set_user(self.bod_user)
		queued_exp = request_export(
			report_type="executive_overview",
			period_type="monthly",
			period_key=month_key,
			scope_type="company",
			actor=self.bod_user,
		)

		try:
			# Flip site-level emergency kill switch (`frappe.local.conf`) while DB `CSKH Policy` remains `1`!
			frappe.local.conf["cskh_p3_reporting_enabled"] = False
			frappe.local.conf["cskh_p3_sla_enabled"] = False

			flags = resolve_p3_flags()
			self.assertFalse(flags["p3_reporting_enabled"])
			self.assertFalse(flags["p3_sla_enabled"])
			self.assertEqual(get_policy_flags()["p3_reporting_enabled"], False)

			# UI context agrees 100% with backend APIs
			frappe.set_user(self.bod_user)
			self.assertFalse(is_p3_enabled("quality-overview"))
			self.assertFalse(is_p3_enabled("sla-operations"))
			with self.assertRaises(frappe.PermissionError):
				build_context(frappe._dict(), route="quality-overview", require_p3=True)
			p1_ctx = build_context(frappe._dict(), route="dashboard", require_p3=False)
			self.assertFalse(p1_ctx.cskh_capabilities["p3_enabled"])

			ov_disabled = p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
			self.assertFalse(ov_disabled.get("reporting_enabled", True))
			self.assertEqual(ov_disabled.get("status"), "disabled_by_policy")

			with self.assertRaises(PolicyConflict):
				request_export(
					report_type="executive_overview",
					period_type="monthly",
					period_key=month_key,
					scope_type="company",
					actor=self.bod_user,
				)

			# Already-queued RQ jobs abort cleanly when kill switch is active!
			frappe.set_user("Administrator")
			rq_kpi = run_single_period_recompute("monthly", month_key)
			self.assertTrue(rq_kpi.get("skipped"))
			rq_exp = execute_export_job_now(queued_exp["export_id"])
			self.assertTrue(rq_exp.get("skipped"))

			# P1/P2 workflow continues cleanly (`0` SLA obligations created) while preserving catch-up `CSKH Dirty Scope`!
			wi_p2, _ = self.make_assigned_work_item(camp, self.staff_a)
			self.assertEqual(frappe.db.count("CSKH SLA Obligation", {"work_item": wi_p2}), 0)
			contact_attempt.record_attempt(
				work_item=wi_p2,
				channel="phone_call",
				outcome_code="connected_interested",
				note="Tác nghiệp P2 bình thường khi tắt P3",
				close_work_item=True,
				completion_reason="Hoàn tất tư vấn",
				idempotency_key=f"f08-{frappe.generate_hash(length=6)}",
				actor=self.staff_a,
			)
			self.assertEqual(frappe.db.get_value("CSKH Work Item", wi_p2, "status"), "closed")

			# Re-enable site flags -> catch-up recompute captures the P2 attempt!
			frappe.local.conf["cskh_p3_reporting_enabled"] = True
			frappe.local.conf["cskh_p3_sla_enabled"] = True
			frappe.set_user(self.bod_user)
			ov_reenabled = p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
			self.assertEqual(ov_reenabled["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 1)
		finally:
			frappe.local.conf.pop("cskh_p3_reporting_enabled", None)
			frappe.local.conf.pop("cskh_p3_sla_enabled", None)

	def test_f_p3_09_10_filter_options_server_side_filters_qa_409_400_and_bod_zero_pii(self):
		"""`F-P3-09, F-P3-10`: `get_filter_options`, `lead_source`, `teams_summary` + `alerts`, server-side filters/sort, QA `409`/`400`, BOD `0` PII."""
		frappe.set_user("Administrator")
		camp_a = self.make_campaign(team_scope=self.team_a)
		camp_b = self.make_campaign(team_scope=self.team_b)
		self.make_rule(camp_a, self.staff_a, weight=100)
		self.make_rule(camp_b, self.staff_c, weight=100)

		wi_fb, lead_fb = self.make_assigned_work_item(camp_a, self.staff_a)
		wi_zl, lead_zl = self.make_assigned_work_item(camp_b, self.staff_c)
		if frappe.db.has_column("CRM Lead", "source"):
			if not frappe.db.exists("CRM Lead Source", "Facebook Ads"):
				frappe.get_doc({"doctype": "CRM Lead Source", "source_name": "Facebook Ads"}).insert(
					ignore_permissions=True
				)
			if not frappe.db.exists("CRM Lead Source", "Zalo OA"):
				frappe.get_doc({"doctype": "CRM Lead Source", "source_name": "Zalo OA"}).insert(
					ignore_permissions=True
				)
			frappe.db.set_value("CRM Lead", lead_fb, "source", "Facebook Ads", update_modified=False)
			frappe.db.set_value("CRM Lead", lead_zl, "source", "Zalo OA", update_modified=False)
		frappe.db.commit()

		contact_attempt.record_attempt(
			work_item=wi_fb,
			channel="phone_call",
			outcome_code="connected_interested",
			note="Khách từ nguồn Facebook",
			idempotency_key=f"f10-fb-{frappe.generate_hash(length=6)}",
			actor=self.staff_a,
		)
		contact_attempt.record_attempt(
			work_item=wi_zl,
			channel="phone_call",
			outcome_code="no_answer",
			note="Khách từ nguồn Zalo",
			idempotency_key=f"f10-zl-{frappe.generate_hash(length=6)}",
			actor=self.staff_c,
		)

		month_key = today_hcm().strftime("%Y-%m")
		execute_kpi_recompute(
			resolve_period_spec("monthly", month_key), run_mode="full", actor=self.admin_user
		)

		# 1. `get_filter_options` scoped per actor (`Leader A` sees `camp_a` & `team_a`, NOT `camp_b` or `team_b`)
		frappe.set_user(self.leader_a)
		opts_la = p3_reporting.get_filter_options()
		la_camp_ids = {c["campaign_id"] for c in opts_la["campaigns"]}
		self.assertIn(camp_a, la_camp_ids)
		self.assertNotIn(camp_b, la_camp_ids)
		la_team_ids = {t["team_id"] for t in opts_la["teams"]}
		self.assertIn(self.team_a, la_team_ids)
		self.assertNotIn(self.team_b, la_team_ids)

		# 2. `BOD` `get_executive_overview` includes `first_touch_on_time_rate`, `teams_summary`, `alerts`, and `0` PII
		frappe.set_user(self.bod_user)
		ov = p3_reporting.get_executive_overview(period_type="monthly", period_key=month_key)
		self.assertIn("first_touch_on_time_rate", ov)
		self.assertIsInstance(ov.get("teams_summary"), list)
		self.assertIsInstance(ov.get("alerts"), list)
		ov_dump = json.dumps(ov, ensure_ascii=False)
		self.assertNotIn("Khách từ nguồn", ov_dump)

		if frappe.db.has_column("CRM Lead", "source"):
			ov_fb = p3_reporting.get_executive_overview(
				period_type="monthly", period_key=month_key, lead_source="Facebook Ads"
			)
			self.assertEqual(ov_fb["metrics"]["CONTACT_ATTEMPT_COUNT"]["value"], 1)
			self.assertEqual(ov_fb["metrics"]["CONNECTED_ATTEMPT_COUNT"]["value"], 1)

		# 3. `sla.list_obligations` & `get_metric_drilldown` server-side filters (`team`, `obligation_type`, `search`, `sort_by`)
		frappe.set_user(self.admin_user)
		sla_all_for_wi = sla_api.list_obligations(
			team=self.team_a,
			search=wi_fb,
			sort_by="effective_due_at",
			sort_dir="asc",
		)
		self.assertEqual(sla_all_for_wi["pagination"]["total"], 2)
		sla_filtered = sla_api.list_obligations(
			team=self.team_a,
			obligation_type="FIRST_ATTEMPT",
			search=wi_fb,
			sort_by="effective_due_at",
			sort_dir="asc",
		)
		self.assertEqual(sla_filtered["pagination"]["total"], 1)
		self.assertEqual(sla_filtered["rows"][0]["work_item"], wi_fb)
		self.assertTrue(sla_filtered["rows"][0].get("team_name"))

		dd_filtered = p3_reporting.get_metric_drilldown(
			metric_code="CONTACT_ATTEMPT_COUNT",
			period_type="monthly",
			period_key=month_key,
			scope_type="company",
			team=self.team_a,
			employee=self.staff_a,
			sort_by="occurred_at",
			sort_dir="desc",
		)
		self.assertEqual(dd_filtered["pagination"]["total"], 1)
		self.assertEqual(dd_filtered["rows"][0]["work_item"], wi_fb)

		# 4. `qa.create_review` returns clean `409 QaDisabled` when `qa_enabled=false` and `400 InvalidRequest` when `qa_enabled=true` (NEVER `500 TypeError`!)
		frappe.db.set_value("CSKH Policy", "GLOBAL-DEFAULT", "qa_enabled", 0, update_modified=False)
		frappe.db.commit()
		with self.assertRaises(QaDisabled):
			qa_api.create_review()

		frappe.db.set_value("CSKH Policy", "GLOBAL-DEFAULT", "qa_enabled", 1, update_modified=False)
		frappe.db.commit()
		try:
			with self.assertRaises(InvalidRequest):
				qa_api.create_review()
		finally:
			frappe.db.set_value("CSKH Policy", "GLOBAL-DEFAULT", "qa_enabled", 0, update_modified=False)
			frappe.db.commit()
