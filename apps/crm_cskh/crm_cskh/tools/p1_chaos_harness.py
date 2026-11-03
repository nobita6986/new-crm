"""Runtime crash / rollback / retry verification harness for P1 §12.4.

Executes the P1 runtime fault-injection scenarios on an isolated site using
test-harness `unittest.mock` wrappers and real child processes `os._exit(137)`
(both import worker and distribution RQ worker), and writes a sanitized,
PII-free JSON report with database post-checks.

Usage (inside the bench container on an isolated chaos site):

    bench --site p1chaos.localhost execute crm_cskh.tools.p1_chaos_harness.run_all
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest import mock

import frappe
from frappe.utils import add_to_date, now_datetime

from crm_cskh.jobs import distribution_worker, import_worker
from crm_cskh.services import distribution, notifications, ownership
from crm_cskh.services import identity as identity_service
from crm_cskh.services.import_service import confirm, create_preview
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.tests.test_p1_chaos import CHILD_CRASH_SCRIPT, CHILD_DISTRIBUTION_CRASH_SCRIPT

OUTPUT_PATH = os.environ.get("P1_CHAOS_OUTPUT", "/tmp/p1-chaos-report.json")


def _ensure_actors():
	for role_name in ("CSKH CRM Admin", "CSKH BOD Viewer"):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)
	actors = {
		"admin": ("p1-chaos-admin@example.test", ("CSKH CRM Admin",)),
		"staff_a": ("p1-chaos-staff-a@example.test", ("Sales User",)),
		"staff_b": ("p1-chaos-staff-b@example.test", ("Sales User",)),
		"staff_c": ("p1-chaos-staff-c@example.test", ("Sales User",)),
	}
	resolved = {}
	for key, (email, roles) in actors.items():
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": key,
					"enabled": 1,
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			).insert(ignore_permissions=True)
		user_doc = frappe.get_doc("User", email)
		existing_roles = {row.role for row in user_doc.roles}
		missing = [r for r in roles if r not in existing_roles]
		if missing:
			user_doc.add_roles(*missing)
		resolved[key] = email
	frappe.db.commit()
	return resolved


def _unique_phone():
	while True:
		digits = f"{int(frappe.generate_hash(length=10), 16) % 100000000:08d}"
		phone = f"09{digits}"
		canonical = normalize_vn_phone(phone)
		if not frappe.db.exists(
			"CRM Lead", {"mobile_no": ["in", (phone, canonical)]}
		) and not frappe.db.exists("CSKH Identity Index", {"normalized_phone": canonical}):
			return phone


def _make_canonical_lead():
	phone = _unique_phone()
	canonical = normalize_vn_phone(phone)
	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": "P1 Chaos Synthetic",
			"mobile_no": canonical,
			"status": "New",
		}
	)
	lead.insert(ignore_permissions=True)
	identity_service.claim_canonical_identity(canonical, lead.name)
	return lead.name


def _make_campaign(leads, actors):
	campaign = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": f"P1-CHAOS-{frappe.generate_hash(length=8)}",
			"title": "P1 chaos runtime verification",
			"status": "Active",
			"selection_json": json.dumps({"leads": leads}),
		}
	)
	campaign.insert(ignore_permissions=True)
	for key, weight in (("staff_a", 50), ("staff_b", 30), ("staff_c", 20)):
		frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": campaign.name,
				"user": actors[key],
				"weight": weight,
				"capacity_limit": 0,
				"version": 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()
	return campaign.name


def _campaign_orphan_postcheck(campaign, leads):
	orphan_work_items = frappe.db.sql(
		"""select wi.name
		   from `tabCSKH Work Item` wi
		   left join `tabCSKH Active Claim` ac on ac.work_item = wi.name
		   where wi.campaign = %s and wi.status in ('queued', 'assigned') and ac.name is null""",
		(campaign,),
	)
	orphan_claims = frappe.db.sql(
		"""select ac.name
		   from `tabCSKH Active Claim` ac
		   left join `tabCSKH Work Item` wi on wi.name = ac.work_item
		   where ac.campaign = %s and wi.name is null""",
		(campaign,),
	)
	owner_mismatches = 0
	todo_mismatches = 0
	share_mismatches = 0
	partial_history = 0
	for lead in leads:
		wi = frappe.db.get_value(
			"CSKH Work Item",
			{"campaign": campaign, "lead": lead},
			["name", "status", "assigned_to"],
			as_dict=True,
		)
		lead_owner = frappe.db.get_value("CRM Lead", lead, "lead_owner")
		open_todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "CRM Lead", "reference_name": lead, "status": "Open"},
			pluck="allocated_to",
		)
		shares = frappe.get_all(
			"DocShare",
			filters={"share_doctype": "CRM Lead", "share_name": lead},
			pluck="user",
		)
		if not wi:
			if lead_owner is not None:
				owner_mismatches += 1
			if open_todos:
				todo_mismatches += 1
			if shares:
				share_mismatches += 1
		else:
			if wi.status == "assigned":
				if lead_owner != wi.assigned_to:
					owner_mismatches += 1
				if open_todos != [wi.assigned_to]:
					todo_mismatches += 1
				if shares != [wi.assigned_to]:
					share_mismatches += 1
			hist = frappe.get_all(
				"CSKH Assignment History",
				filters={"work_item": wi.name},
				fields=["action", "to_user"],
			)
			if not hist or any(h.action == "assign" and not h.to_user for h in hist):
				partial_history += 1

	return {
		"orphan_work_items": len(orphan_work_items),
		"orphan_active_claims": len(orphan_claims),
		"owner_mismatches": owner_mismatches,
		"todo_mismatches": todo_mismatches,
		"docshare_mismatches": share_mismatches,
		"partial_history_rows": partial_history,
	}


def run_all():
	"""Execute all runtime chaos scenarios and write sanitized JSON evidence."""
	frappe.set_user("Administrator")
	actors = _ensure_actors()
	scenarios = []

	# Scenario 1: Fault after Work Item + Active Claim creation before ownership, then resume.
	s1_leads = [_make_canonical_lead() for _ in range(10)]
	frappe.db.commit()
	s1_campaign = _make_campaign(s1_leads, actors)
	s1_preview = distribution.preview(s1_campaign, actor=actors["admin"])

	real_assign = ownership.assign
	s1_faulted_items = []
	s1_call = {"n": 0}

	def s1_inject(work_item, target_user, **kwargs):
		s1_call["n"] += 1
		if s1_call["n"] in (1, 4):
			s1_faulted_items.append(
				{
					"work_item_existed_in_savepoint": bool(frappe.db.exists("CSKH Work Item", work_item)),
					"claim_existed_in_savepoint": bool(
						frappe.db.exists("CSKH Active Claim", {"work_item": work_item})
					),
					"work_item": work_item,
				}
			)
			raise RuntimeError("harness fault: after Work Item creation before ownership")
		elif s1_call["n"] == 7:
			real_assign(work_item, target_user, **kwargs)
			s1_faulted_items.append(
				{
					"mid_ownership_history_existed": frappe.db.count(
						"CSKH Assignment History", {"work_item": work_item}
					)
					== 1,
					"work_item": work_item,
				}
			)
			raise RuntimeError("harness fault: mid-ownership after History/ToDo/DocShare")
		return real_assign(work_item, target_user, **kwargs)

	with mock.patch("crm_cskh.services.distribution.ownership.assign", side_effect=s1_inject):
		s1_first = distribution.execute(s1_preview["preview_id"], actor=actors["admin"])

	s1_post_rollback = _campaign_orphan_postcheck(s1_campaign, s1_leads)
	rolled_back_surviving = sum(
		1 for entry in s1_faulted_items if frappe.db.exists("CSKH Work Item", entry["work_item"])
	)
	s1_resumed = distribution.execute(s1_preview["preview_id"], actor=actors["admin"])
	s1_reconcile = distribution.reconcile(s1_campaign, actor=actors["admin"])
	s1_post_resume = _campaign_orphan_postcheck(s1_campaign, s1_leads)

	scenarios.append(
		{
			"id": "S1_work_item_and_mid_ownership_rollback_then_resume",
			"campaign": s1_campaign,
			"pool_size": 10,
			"attempt_1": {
				"status": s1_first["status"],
				"created": s1_first["created"],
				"errors": s1_first["errors"],
				"faulted_savepoint_observations": len(s1_faulted_items),
				"rolled_back_work_items_surviving_in_db": rolled_back_surviving,
				"db_postcheck": s1_post_rollback,
			},
			"attempt_2_resume": {
				"status": s1_resumed["status"],
				"created": s1_resumed["created"],
				"errors": s1_resumed["errors"],
				"assigned_by_user": s1_resumed["assigned"],
				"reconcile": {
					"distinct_leads": s1_reconcile["distinct_leads"],
					"active_work_items": s1_reconcile["active_work_items"],
					"active_claims": s1_reconcile["active_claims"],
					"current_owners": s1_reconcile["current_owners"],
					"assignment_events": s1_reconcile["assignment_events"],
				},
				"db_postcheck": s1_post_resume,
			},
			"passed": (
				s1_first["status"] == "completed_with_errors"
				and s1_first["created"] == 7
				and s1_first["errors"] == 3
				and rolled_back_surviving == 0
				and all(v == 0 for v in s1_post_rollback.values())
				and s1_resumed["status"] == "completed"
				and s1_resumed["created"] == 10
				and s1_resumed["assigned"]
				== {actors["staff_a"]: 5, actors["staff_b"]: 3, actors["staff_c"]: 2}
				and all(v == 0 for v in s1_post_resume.values())
			),
		}
	)

	# Scenario 2: Fault after DB commit before notification, followed by idempotent retries.
	s2_leads = [_make_canonical_lead() for _ in range(3)]
	frappe.db.commit()
	s2_campaign = _make_campaign(s2_leads, actors)
	s2_preview = distribution.preview(s2_campaign, actor=actors["admin"])

	def s2_fail_notification(work_item, lead, to_user, actor):
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
		raise RuntimeError("harness fault: notification gateway down after DB commit")

	with mock.patch(
		"crm_cskh.services.notifications._ensure_notification",
		side_effect=s2_fail_notification,
	):
		s2_exec = distribution.execute(s2_preview["preview_id"], actor=actors["admin"])

	s2_items = frappe.get_all(
		"CSKH Work Item",
		filters={"campaign": s2_campaign},
		fields=["name", "lead", "assigned_to", "status"],
	)
	notif_before_retry = sum(
		frappe.db.count(
			"CRM Notification",
			{
				"reference_doctype": "CRM Lead",
				"reference_name": row.lead,
				"to_user": row.assigned_to,
				"type": "Assignment",
			},
		)
		for row in s2_items
	)

	# Retry execute + deliver_assignment x2 + ownership.assign(request_id)
	distribution.execute(s2_preview["preview_id"], actor=actors["admin"])
	for row in s2_items:
		notifications.deliver_assignment(
			row.name,
			row.assigned_to,
			event_id=f"assign:{row.name}:run:{s2_exec['run_id']}",
			actor=actors["admin"],
		)
		notifications.deliver_assignment(
			row.name,
			row.assigned_to,
			event_id=f"assign:{row.name}:run:{s2_exec['run_id']}",
			actor=actors["admin"],
		)
		ownership.assign(
			row.name,
			row.assigned_to,
			actor=actors["admin"],
			request_id=f"run:{s2_exec['run_id']}:{row.lead}",
		)
	frappe.db.commit()

	notif_after_retry = sum(
		frappe.db.count(
			"CRM Notification",
			{
				"reference_doctype": "CRM Lead",
				"reference_name": row.lead,
				"to_user": row.assigned_to,
				"type": "Assignment",
			},
		)
		for row in s2_items
	)
	todo_after_retry = sum(
		frappe.db.count(
			"ToDo",
			{
				"reference_type": "CRM Lead",
				"reference_name": row.lead,
				"allocated_to": row.assigned_to,
				"status": "Open",
			},
		)
		for row in s2_items
	)
	s2_reconcile = distribution.reconcile(s2_campaign, actor=actors["admin"])
	s2_postcheck = _campaign_orphan_postcheck(s2_campaign, s2_leads)

	scenarios.append(
		{
			"id": "S2_post_commit_notification_failure_and_idempotent_retry",
			"campaign": s2_campaign,
			"pool_size": 3,
			"after_failed_notification": {
				"run_status": s2_exec["status"],
				"committed_work_items": len(s2_items),
				"crm_notifications_in_db": notif_before_retry,
			},
			"after_double_retry": {
				"crm_notifications_in_db": notif_after_retry,
				"open_todos_in_db": todo_after_retry,
				"distinct_leads": s2_reconcile["distinct_leads"],
				"active_work_items": s2_reconcile["active_work_items"],
				"active_claims": s2_reconcile["active_claims"],
				"assignment_events_total": sum(s2_reconcile["assignment_events"].values()),
				"db_postcheck": s2_postcheck,
			},
			"passed": (
				s2_exec["status"] == "completed"
				and len(s2_items) == 3
				and notif_before_retry == 0
				and notif_after_retry == 3
				and todo_after_retry == 3
				and s2_reconcile["active_work_items"] == 3
				and s2_reconcile["active_claims"] == 3
				and sum(s2_reconcile["assignment_events"].values()) == 3
				and all(v == 0 for v in s2_postcheck.values())
			),
		}
	)

	# Scenario 3: Real OS child process hard kill (`os._exit(137)`) mid-import batch + recovery.
	s3_phones = [_unique_phone() for _ in range(9)]
	s3_canonicals = [normalize_vn_phone(p) for p in s3_phones]
	csv_bytes = ("\n".join(["Số điện thoại", *s3_phones]) + "\n").encode("utf-8")
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "p1-chaos-hardkill.csv",
			"is_private": 1,
			"attached_to_doctype": "CRM Lead",
			"attached_to_name": "Administrator",
			"content": csv_bytes,
		}
	)
	file_doc.insert(ignore_permissions=True)
	s3_preview = create_preview(
		file_doc.name,
		actor=actors["admin"],
		idempotency_key=f"p1-chaos-hardkill-{frappe.generate_hash(length=8)}",
	)
	s3_batch = s3_preview["batch"]
	with mock.patch("frappe.enqueue"):
		s3_confirmed = confirm(s3_batch, actor=actors["admin"])
	frappe.db.commit()

	child_payload = {
		"site": getattr(frappe.local, "site", None),
		"sites_path": getattr(frappe.local, "sites_path", None),
		"batch_name": s3_batch,
		"chunk_size": 3,
		"kill_after_progress": 4,
	}
	child_proc = subprocess.run(
		[sys.executable, "-c", CHILD_CRASH_SCRIPT],
		capture_output=True,
		text=True,
		timeout=120,
		env={**os.environ, "CSKH_CHAOS_CHILD_PAYLOAD": json.dumps(child_payload)},
	)
	frappe.db.commit()
	mid_batch = frappe.db.get_value(
		"CSKH Import Batch",
		s3_batch,
		["status", "attempts", "progress"],
		as_dict=True,
	)
	mid_identity_count = frappe.db.count("CSKH Identity Index", {"normalized_phone": ["in", s3_canonicals]})

	with mock.patch.object(import_worker, "CHUNK_SIZE", 3):
		import_worker.run_batch(s3_batch)
		import_worker.run_batch(s3_batch)

	final_batch = frappe.get_doc("CSKH Import Batch", s3_batch)
	final_identity_count = frappe.db.count("CSKH Identity Index", {"normalized_phone": ["in", s3_canonicals]})
	final_lead_count = frappe.db.count(
		"CRM Lead", {"mobile_no": ["in", s3_canonicals], "cskh_is_placeholder_name": 1}
	)
	dup_identities = frappe.db.sql(
		"""select normalized_phone, count(*) as c
		   from `tabCSKH Identity Index`
		   where normalized_phone in %s
		   group by normalized_phone having count(*) > 1""",
		(tuple(s3_canonicals),),
	)
	dup_leads = frappe.db.sql(
		"""select mobile_no, count(*) as c
		   from `tabCRM Lead`
		   where mobile_no in %s
		   group by mobile_no having count(*) > 1""",
		(tuple(s3_canonicals),),
	)

	scenarios.append(
		{
			"id": "S3_worker_hard_kill_exit_137_mid_batch_and_recovery",
			"batch": s3_batch,
			"first_confirm_status": s3_confirmed["status"],
			"total_rows": 9,
			"chunk_size": 3,
			"child_process_exit_code": child_proc.returncode,
			"mid_crash_db_state": {
				"status": mid_batch.status,
				"attempts": int(mid_batch.attempts),
				"progress_row_marker": int(mid_batch.progress),
				"committed_identities_before_crash": mid_identity_count,
			},
			"post_recovery_db_state": {
				"status": final_batch.status,
				"attempts": int(final_batch.attempts),
				"created_rows": int(final_batch.created_rows),
				"reused_rows": int(final_batch.reused_rows),
				"error_rows": int(final_batch.error_rows),
				"skipped_rows": int(final_batch.skipped_rows),
				"identity_index_count": final_identity_count,
				"crm_lead_count": final_lead_count,
				"duplicate_identities": len(dup_identities),
				"duplicate_leads": len(dup_leads),
			},
			"passed": (
				s3_confirmed["status"] == "QUEUED"
				and child_proc.returncode == 137
				and mid_batch.status == "RUNNING"
				and int(mid_batch.attempts) == 1
				and int(mid_batch.progress) == 4
				and mid_identity_count == 3
				and final_batch.status == "COMPLETED"
				and int(final_batch.attempts) == 2
				and int(final_batch.created_rows) == 9
				and int(final_batch.reused_rows) == 0
				and int(final_batch.error_rows) == 0
				and final_identity_count == 9
				and final_lead_count == 9
				and len(dup_identities) == 0
				and len(dup_leads) == 0
			),
		}
	)

	# Scenario 4: Real OS child process hard kill (`os._exit(137)`) mid-distribution RQ worker + lease recovery.
	s4_leads = [_make_canonical_lead() for _ in range(10)]
	frappe.db.commit()
	s4_campaign = _make_campaign(s4_leads, actors)
	s4_preview = distribution.preview(s4_campaign, actor=actors["admin"])
	s4_queued = distribution.enqueue_run(s4_preview["preview_id"], actor=actors["admin"], enqueue_job=False)
	s4_run_id = s4_queued["run_id"]
	frappe.db.commit()

	dist_child_payload = {
		"site": getattr(frappe.local, "site", None),
		"sites_path": getattr(frappe.local, "sites_path", None),
		"run_id": s4_run_id,
		"actor": actors["admin"],
		"chunk_size": 3,
		"kill_after_progress": 6,
	}
	dist_child_proc = subprocess.run(
		[sys.executable, "-c", CHILD_DISTRIBUTION_CRASH_SCRIPT],
		capture_output=True,
		text=True,
		timeout=120,
		env={**os.environ, "CSKH_CHAOS_DIST_PAYLOAD": json.dumps(dist_child_payload)},
	)
	frappe.db.commit()
	s4_mid = distribution.get_run(s4_run_id, actor=actors["admin"])

	# Verify active lease blocks duplicate enqueue/takeover before lease expiry.
	with mock.patch("frappe.enqueue") as s4_active_enq:
		distribution.enqueue_run(s4_run_id, actor=actors["admin"], enqueue_job=True)
		active_lease_blocked_enqueue = s4_active_enq.call_count == 0

	# Expire lease and recover remaining chunks cleanly.
	frappe.db.set_value(
		"CSKH Distribution Run",
		s4_run_id,
		"lease_expires_at",
		add_to_date(now_datetime(), seconds=-60),
	)
	frappe.db.commit()
	distribution.enqueue_run(s4_run_id, actor=actors["admin"], enqueue_job=False)
	frappe.db.commit()
	with mock.patch.object(distribution, "CHUNK_SIZE", 3):
		s4_final = distribution_worker.run_distribution(s4_run_id, actor=actors["admin"])
		distribution_worker.run_distribution(s4_run_id, actor=actors["admin"])

	s4_reconcile = distribution.reconcile(s4_campaign, actor=actors["admin"])
	s4_postcheck = _campaign_orphan_postcheck(s4_campaign, s4_leads)

	scenarios.append(
		{
			"id": "S4_distribution_worker_hard_kill_exit_137_mid_batch_and_lease_recovery",
			"campaign": s4_campaign,
			"run_id": s4_run_id,
			"pool_size": 10,
			"chunk_size": 3,
			"initial_enqueue_status": s4_queued["status"],
			"child_process_exit_code": dist_child_proc.returncode,
			"active_lease_blocked_duplicate_enqueue": active_lease_blocked_enqueue,
			"mid_crash_db_state": {
				"status": s4_mid["status"],
				"progress": s4_mid["progress"],
				"created": s4_mid["created"],
			},
			"post_recovery_db_state": {
				"status": s4_final["status"],
				"progress": s4_final["progress"],
				"created": s4_final["created"],
				"errors": s4_final["errors"],
				"assigned_by_user": s4_final["assigned"],
				"reconcile": {
					"distinct_leads": s4_reconcile["distinct_leads"],
					"active_work_items": s4_reconcile["active_work_items"],
					"active_claims": s4_reconcile["active_claims"],
					"current_owners": s4_reconcile["current_owners"],
					"assignment_events": s4_reconcile["assignment_events"],
				},
				"db_postcheck": s4_postcheck,
			},
			"passed": (
				s4_queued["status"] == "queued"
				and dist_child_proc.returncode == 137
				and active_lease_blocked_enqueue
				and s4_mid["status"] == "running"
				and s4_mid["progress"] == 6
				and s4_mid["created"] == 6
				and s4_final["status"] == "completed"
				and s4_final["progress"] == 10
				and s4_final["created"] == 10
				and s4_final["errors"] == 0
				and s4_final["assigned"] == {actors["staff_a"]: 5, actors["staff_b"]: 3, actors["staff_c"]: 2}
				and s4_reconcile["active_work_items"] == 10
				and s4_reconcile["active_claims"] == 10
				and sum(s4_reconcile["assignment_events"].values()) == 10
				and all(v == 0 for v in s4_postcheck.values())
			),
		}
	)

	overall_passed = all(s["passed"] for s in scenarios)
	report = {
		"site": frappe.local.site,
		"recorded_at": str(now_datetime()),
		"injection_mechanism": "pure_test_harness_mock_and_child_os_exit_137",
		"production_hooks_present": False,
		"overall_result": "PASS" if overall_passed else "FAIL",
		"scenarios": scenarios,
	}
	payload_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
	with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
		handle.write(payload_text)
	print(payload_text)
	if not overall_passed:
		raise RuntimeError("One or more P1 chaos scenarios failed")
	return report
