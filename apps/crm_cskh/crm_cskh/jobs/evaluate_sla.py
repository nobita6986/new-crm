"""Idempotent SLA evaluator & deduplicated due-soon / breach notification worker (`P3-03`).

Invariants (`docs/p3/sla-policy-spec.md` §2, §4):
  * Evaluates `open` (non-paused) obligations against `as_of` (defaults to `now_datetime()`).
  * Delayed worker execution NEVER mutates `original_due_at` or `effective_due_at`: when `as_of > effective_due_at`,
    `breached_at` is set to `effective_due_at` (the exact deadline crossed), NOT `worker_run_at`.
  * Deduplicates notifications via `last_alert_stage` (`due_soon` -> `breached`) under row lock AND
    `Notification Log` event token check so repeated retries emit `0` duplicate alerts.
"""

from __future__ import annotations

import json
from datetime import datetime

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from crm_cskh.p3_models import authorized_sla_write
from crm_cskh.services.calendar import business_minutes_between, resolve_working_calendar
from crm_cskh.services.sla_engine import _append_audit, _calendar_from_obligation, _notify_dirty_post_commit


def _emit_deduplicated_sla_notification(
	*,
	obligation_id: str,
	work_item: str,
	owner_user: str | None,
	stage: str,
	subject_vi: str,
	now_dt: datetime,
) -> bool:
	"""Insert at most one `Notification Log` per `(obligation_id, stage)`."""
	if not owner_user or not frappe.db.exists("User", owner_user):
		return False
	dedupe_token = f"[CSKH_SLA_ALERT:{obligation_id}:{stage}]"
	already = frappe.db.sql(
		"""
		select name from `tabNotification Log`
		where for_user = %s and email_content like %s
		limit 1
		""",
		(owner_user, f"%{dedupe_token}%"),
	)
	if already:
		return False

	doc = frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": owner_user,
			"from_user": "Administrator",
			"type": "Alert",
			"document_type": "CSKH Work Item",
			"document_name": work_item,
			"subject": subject_vi,
			"email_content": f"{subject_vi} {dedupe_token} ({now_dt})",
		}
	)
	doc.insert(ignore_permissions=True)
	return True


def evaluate_sla_obligations(as_of: datetime | str | None = None, batch_size: int = 500) -> dict:
	"""Scan `open` non-paused obligations, transition overdue rows to `breached_open`, and send deduped alerts."""
	if not frappe.db.table_exists("CSKH SLA Obligation"):
		return {"evaluated": 0, "breached": 0, "warned_due_soon": 0, "notifications_created": 0}
	from crm_cskh.services.sla_engine import is_p3_sla_enabled

	if not is_p3_sla_enabled():
		return {
			"evaluated": 0,
			"breached": 0,
			"warned_due_soon": 0,
			"notifications_created": 0,
			"skipped": True,
			"reason": "p3_sla_disabled",
		}

	eval_dt = get_datetime(as_of or now_datetime())
	candidates = frappe.db.sql(
		"""
		select name
		from `tabCSKH SLA Obligation`
		where status = 'open' and coalesce(is_paused, 0) = 0
		order by effective_due_at asc
		limit %s
		""",
		(int(batch_size),),
		pluck=True,
	)

	evaluated = 0
	breached_count = 0
	warned_count = 0
	notif_count = 0

	for ob_name in candidates or []:
		rows = frappe.db.sql(
			"""
			select name, obligation_type, work_item, campaign, owner_user, team_at_obligation,
			       status, is_paused, informational_only, original_due_at, effective_due_at,
			       breached_at, last_alert_stage, policy_snapshot_json, calendar_snapshot_json, audit_events_json
			from `tabCSKH SLA Obligation`
			where name = %s
			for update
			""",
			(ob_name,),
			as_dict=True,
		)
		if not rows:
			continue
		ob = rows[0]
		if ob.status != "open" or cint(ob.is_paused):
			continue

		evaluated += 1
		eff_due = get_datetime(ob.effective_due_at)

		if eval_dt > eff_due:
			# Overdue! `breached_at` is fixed to `eff_due` (never `eval_dt`!).
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			created_notif = False
			if not cint(ob.informational_only) and ob.last_alert_stage != "breached":
				created_notif = _emit_deduplicated_sla_notification(
					obligation_id=ob.name,
					work_item=ob.work_item,
					owner_user=ob.owner_user,
					stage="breached",
					subject_vi=f"Cảnh báo quá hạn SLA ({ob.obligation_type}) trên hồ sơ {ob.work_item}",
					now_dt=eval_dt,
				)
				if created_notif:
					notif_count += 1

			with authorized_sla_write():
				doc = frappe.get_doc("CSKH SLA Obligation", ob.name)
				doc.status = "breached_open"
				doc.breached_at = breach_ts
				doc.last_alert_stage = "breached"
				doc.alerted_at = eval_dt
				doc.audit_events_json = _append_audit(
					doc.audit_events_json,
					{
						"event": "evaluated_breached_open",
						"evaluated_at": str(eval_dt),
						"breached_at": str(breach_ts),
					},
				)
				doc.flags.ignore_links = True
				doc.save(ignore_permissions=True)

			breached_count += 1
			_notify_dirty_post_commit(
				work_item=ob.work_item,
				campaign=ob.campaign,
				owner_user=ob.owner_user,
				team_node=ob.team_at_obligation,
				event_dt=breach_ts,
				reason="obligation_breached",
			)
			continue

		# Check `due_soon` warning window if not yet alerted
		if not cint(ob.informational_only) and not ob.last_alert_stage:
			warn_min = 10
			if ob.policy_snapshot_json:
				try:
					warn_min = max(
						cint(json.loads(ob.policy_snapshot_json).get("due_soon_warning_minutes", 10)), 0
					)
				except Exception:
					warn_min = 10
			if warn_min > 0:
				cal = (
					_calendar_from_obligation(ob)
					if ob.calendar_snapshot_json
					else resolve_working_calendar(None)
				)
				rem_biz_min = business_minutes_between(eval_dt, eff_due, cal)
				if 0.0 <= rem_biz_min <= float(warn_min):
					created_notif = _emit_deduplicated_sla_notification(
						obligation_id=ob.name,
						work_item=ob.work_item,
						owner_user=ob.owner_user,
						stage="due_soon",
						subject_vi=f"Sắp đến hạn SLA ({ob.obligation_type}) trên hồ sơ {ob.work_item}",
						now_dt=eval_dt,
					)
					if created_notif:
						notif_count += 1
					with authorized_sla_write():
						doc = frappe.get_doc("CSKH SLA Obligation", ob.name)
						doc.last_alert_stage = "due_soon"
						doc.alerted_at = eval_dt
						doc.flags.ignore_links = True
						doc.save(ignore_permissions=True)
					warned_count += 1

	frappe.db.commit()
	return {
		"evaluated": evaluated,
		"breached": breached_count,
		"warned_due_soon": warned_count,
		"notifications_created": notif_count,
		"as_of": str(eval_dt),
	}
