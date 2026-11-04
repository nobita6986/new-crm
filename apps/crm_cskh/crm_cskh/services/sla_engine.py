"""SLA Obligation lifecycle engine (`P3-03`, `ADR-P3-01`, `ADR-P3-04`, `ADR-P3-12`).

Guarantees (`docs/p3/sla-policy-spec.md`):
  * `FIRST_ATTEMPT` & `FOLLOWUP` active by default (`p3_sla_enabled = 1`).
  * `FIRST_CONNECTED` tracked separately with `informational_only = 1` (never penalizes staff).
  * `MIN_ATTEMPTS` generated ONLY when explicitly enabled in policy (`min_attempts_enabled = 1`).
  * `original_due_at` is strictly immutable once written.
  * Ownership transfers (`A -> B`, `A -> B -> A`):
      - Transfer BEFORE `effective_due_at`: `A` closes as `handoff_closed` (no fault); `B` receives
        `max(remaining_to_work_item_due, handoff_min_grace_minutes)` bounded by `max_handoff_grace_resets`.
      - Transfer AFTER `effective_due_at`: `A` closes as `breached_handoff` (retains breach); `B` receives
        a recovery obligation (`is_recovery_obligation = 1`) with `handoff_min_grace_minutes`.
  * Callback `CRM Task` reschedule:
      - Reschedule BEFORE breach: transitions old obligation to `superseded` and opens new obligation
        (enforcing `max_reschedules_without_approval` for Staff).
      - Reschedule AFTER breach: preserves old breach (`breached_handoff`) so breach cannot be erased,
        and opens a recovery tracking obligation for the new callback time.
  * Explicit audited `waive_obligation`, `pause_obligation`, `resume_obligation` with strict authority checks.
  * Delayed background evaluator compares `occurred_at` / `as_of` against `effective_due_at` and NEVER
    shifts `original_due_at` or `effective_due_at`.
"""

from __future__ import annotations

import json
from datetime import datetime

import frappe
from frappe.utils import cint, flt, get_datetime, now_datetime

from crm_cskh.api._params import parse_int, parse_optional_str, parse_pagination
from crm_cskh.p1_permissions import ROLE_ADMIN, _is_system, _roles
from crm_cskh.p2_permissions import _in_team, _is_leader, _is_staff
from crm_cskh.p3_models import DEFAULT_P3_POLICY_FIELDS, authorized_sla_write
from crm_cskh.p3_permissions import sla_obligation_has_permission
from crm_cskh.services.attribution import AUTHORITATIVE, UNKNOWN_HISTORICAL, resolve_team_for_user_at
from crm_cskh.services.calendar import (
	WorkingCalendarSpec,
	add_business_minutes,
	business_minutes_between,
	format_calendar_iso,
	resolve_working_calendar,
)
from crm_cskh.services.errors import (
	ForbiddenScope,
	InvalidRequest,
	NoteRequired,
	NotFound,
	PolicyConflict,
)
from crm_cskh.services.sanitizer import sanitize_note_text


def _p3_sla_ready() -> bool:
	return bool(frappe.db.table_exists("CSKH SLA Obligation"))


def _notify_dirty_post_commit(
	*,
	work_item: str | None = None,
	campaign: str | None = None,
	owner_user: str | None = None,
	team_node: str | None = None,
	event_dt: datetime | str | None = None,
	reason: str = "sla_mutation",
) -> None:
	"""Hook point for PR #3 post-commit `CSKH Dirty Scope` invalidation (no-op until `kpi_runs` exists)."""
	try:
		from crm_cskh.services import kpi_runs  # type: ignore[attr-defined]

		kpi_runs.schedule_post_commit_dirty_invalidation(
			work_item=work_item,
			campaign=campaign,
			owner_user=owner_user,
			team_node=team_node,
			event_dt=event_dt,
			reason=reason,
		)
	except ImportError:
		return


def _policy_with_p3_defaults(policy: dict) -> dict:
	merged = dict(DEFAULT_P3_POLICY_FIELDS)
	merged.update({k: v for k, v in policy.items() if v is not None})
	return merged


def _resolve_policy_and_calendar(
	campaign: str | None, owner_user: str | None
) -> tuple[dict, WorkingCalendarSpec]:
	from crm_cskh.services.policies import resolve_effective_policy

	raw_pol = resolve_effective_policy(campaign=campaign, owner_user=owner_user)
	pol = _policy_with_p3_defaults(raw_pol)
	cal = resolve_working_calendar(pol.get("working_calendar"))
	return pol, cal


def _load_audit_events(raw: str | None) -> list[dict]:
	if not raw:
		return []
	try:
		parsed = json.loads(raw)
		return parsed if isinstance(parsed, list) else []
	except (TypeError, ValueError):
		return []


def _append_audit(raw: str | None, event: dict) -> str:
	events = _load_audit_events(raw)
	events.append(event)
	return json.dumps(events, ensure_ascii=False)


def _calendar_from_obligation(ob_row: dict) -> WorkingCalendarSpec:
	snap = ob_row.get("calendar_snapshot_json")
	if snap:
		try:
			return WorkingCalendarSpec.from_snapshot(snap)
		except Exception:
			pass
	return resolve_working_calendar(None)


def _insert_obligation(fields: dict) -> str:
	idem = fields["idempotency_key"]
	existing = frappe.db.get_value("CSKH SLA Obligation", {"idempotency_key": idem}, "name")
	if existing:
		return str(existing)
	with authorized_sla_write():
		doc = frappe.get_doc({"doctype": "CSKH SLA Obligation", **fields})
		doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
		return str(doc.name)


def _update_obligation(name: str, updates: dict, audit_entry: dict | None = None) -> None:
	with authorized_sla_write():
		doc = frappe.get_doc("CSKH SLA Obligation", name)
		for k, v in updates.items():
			setattr(doc, k, v)
		if audit_entry is not None:
			doc.audit_events_json = _append_audit(doc.audit_events_json, audit_entry)
		doc.flags.ignore_links = True
		doc.save(ignore_permissions=True)


def _effective_due_with_active_pause(ob_row: dict, as_of_dt: datetime) -> datetime:
	eff = get_datetime(ob_row["effective_due_at"])
	if cint(ob_row.get("is_paused")) and ob_row.get("paused_at"):
		cal = _calendar_from_obligation(ob_row)
		extra = business_minutes_between(ob_row["paused_at"], as_of_dt, cal)
		if extra > 0:
			eff = add_business_minutes(eff, extra, cal)
	return eff


def _resolve_interval_attribution(
	interval_event: str | None, owner_user: str | None, at_dt: datetime
) -> tuple[str | None, str]:
	if interval_event and frappe.db.has_column("CSKH Assignment History", "attribution_status"):
		hist = frappe.db.get_value(
			"CSKH Assignment History",
			interval_event,
			["team_at_assignment", "attribution_status"],
			as_dict=True,
		)
		if hist and hist.get("attribution_status"):
			return hist.get("team_at_assignment"), str(hist.get("attribution_status"))
	return resolve_team_for_user_at(
		owner_user,
		at_dt,
		allow_live_capture_for_current_tx=True,
	)


# ---------------------------------------------------------------------------
# 1. Lifecycle hooks called from `ownership`, `followup`, `contact_attempt`, `correction`
# ---------------------------------------------------------------------------


def on_interval_opened_in_tx(
	*,
	work_item: str,
	lead: str,
	campaign: str | None,
	interval_event: str,
	owner_user: str,
	started_at: datetime | str,
	actor: str,
	is_reopen: bool = False,
) -> list[str]:
	"""Spawn initial obligations when `assign` or `reopen` opens a new ownership interval."""
	if not _p3_sla_ready():
		return []
	start_dt = get_datetime(started_at)
	policy, calendar = _resolve_policy_and_calendar(campaign=campaign, owner_user=owner_user)
	if not cint(policy.get("p3_sla_enabled", 1)):
		return []

	# Check DNC: never spawn outreach obligations if Lead is flagged DNC
	if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and cint(
		frappe.db.get_value("CRM Lead", lead, "cskh_do_not_contact")
	):
		return []

	from crm_cskh.services.read_model import compute_effective_attempts, summarize_effective_attempts

	eff_summary = summarize_effective_attempts(compute_effective_attempts(work_item))
	created_ids: list[str] = []
	team_node, attr_status = _resolve_interval_attribution(interval_event, owner_user, start_dt)
	pol_json = json.dumps(policy, ensure_ascii=False, sort_keys=True, default=str)
	cal_json = calendar.to_snapshot_json()

	# 1. `FIRST_ATTEMPT`: created if the Work Item has not yet had an effective contact attempt
	if eff_summary["attempt_count"] == 0:
		target_min = max(cint(policy.get("first_attempt_sla_minutes", 30)), 0)
		due_dt = add_business_minutes(start_dt, target_min, calendar)
		ob_id = _insert_obligation(
			{
				"obligation_type": "FIRST_ATTEMPT",
				"work_item": work_item,
				"lead": lead,
				"campaign": campaign,
				"interval_event": interval_event,
				"owner_user": owner_user,
				"team_at_obligation": team_node,
				"attribution_status": attr_status,
				"status": "open",
				"informational_only": 0,
				"is_recovery_obligation": 1 if is_reopen else 0,
				"handoff_reset_seq": 0,
				"handoff_cap_reached": 0,
				"started_at": start_dt,
				"original_due_at": due_dt,
				"effective_due_at": due_dt,
				"work_item_original_due_at": due_dt,
				"policy_name_snapshot": policy.get("name"),
				"policy_version_snapshot": cint(policy.get("version", 1)),
				"policy_snapshot_json": pol_json,
				"calendar_snapshot_json": cal_json,
				"audit_events_json": json.dumps(
					[
						{
							"event": "created_reopen" if is_reopen else "created_assign",
							"actor": actor,
							"at": str(start_dt),
							"target_minutes": target_min,
							"due_at": str(due_dt),
						}
					],
					ensure_ascii=False,
				),
				"idempotency_key": f"FIRST_ATTEMPT:{work_item}:{interval_event}",
			}
		)
		created_ids.append(ob_id)

	# 2. `FIRST_CONNECTED` (informational only — never penalizes staff)
	if cint(policy.get("track_first_connected", 1)) and eff_summary["connected_attempt_count"] == 0:
		existing_fc = frappe.db.get_value(
			"CSKH SLA Obligation",
			{
				"work_item": work_item,
				"obligation_type": "FIRST_CONNECTED",
				"status": ["in", ["open", "fulfilled_on_time", "fulfilled_late", "breached_open"]],
			},
			"name",
		)
		if not existing_fc:
			fc_min = max(cint(policy.get("first_connected_target_minutes", 120)), 0)
			fc_due = add_business_minutes(start_dt, fc_min, calendar)
			fc_id = _insert_obligation(
				{
					"obligation_type": "FIRST_CONNECTED",
					"work_item": work_item,
					"lead": lead,
					"campaign": campaign,
					"interval_event": interval_event,
					"owner_user": owner_user,
					"team_at_obligation": team_node,
					"attribution_status": attr_status,
					"status": "open",
					"informational_only": 1,
					"started_at": start_dt,
					"original_due_at": fc_due,
					"effective_due_at": fc_due,
					"work_item_original_due_at": fc_due,
					"policy_name_snapshot": policy.get("name"),
					"policy_version_snapshot": cint(policy.get("version", 1)),
					"policy_snapshot_json": pol_json,
					"calendar_snapshot_json": cal_json,
					"audit_events_json": json.dumps(
						[
							{
								"event": "created_informational",
								"actor": actor,
								"at": str(start_dt),
								"target_minutes": fc_min,
								"due_at": str(fc_due),
							}
						],
						ensure_ascii=False,
					),
					"idempotency_key": f"FIRST_CONNECTED:{work_item}:{interval_event}",
				}
			)
			created_ids.append(fc_id)

	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=campaign,
		owner_user=owner_user,
		team_node=team_node,
		event_dt=start_dt,
		reason="interval_opened",
	)
	return created_ids


def on_interval_transferred_in_tx(
	*,
	work_item: str,
	lead: str,
	campaign: str | None,
	old_interval_event: str | None,
	new_interval_event: str,
	from_user: str,
	to_user: str,
	transferred_at: datetime | str,
	actor: str,
	transferred_task: str | None = None,
) -> dict:
	"""Handle `A -> B` and `A -> B -> A` SLA handoff fairness & anti-gaming reset cap."""
	if not _p3_sla_ready():
		return {"closed": [], "opened": []}
	tx_dt = get_datetime(transferred_at)
	policy, calendar = _resolve_policy_and_calendar(campaign=campaign, owner_user=to_user)
	if not cint(policy.get("p3_sla_enabled", 1)):
		return {"closed": [], "opened": []}

	team_node_b, attr_status_b = _resolve_interval_attribution(new_interval_event, to_user, tx_dt)
	pol_json = json.dumps(policy, ensure_ascii=False, sort_keys=True, default=str)
	cal_json = calendar.to_snapshot_json()

	active_obs = frappe.db.sql(
		"""
		select name, obligation_type, owner_user, team_at_obligation, followup_task, status,
		       original_due_at, effective_due_at, work_item_original_due_at, breached_at,
		       handoff_reset_seq, is_paused, paused_at, calendar_snapshot_json
		from `tabCSKH SLA Obligation`
		where work_item = %s and status in ('open', 'breached_open')
		for update
		""",
		(work_item,),
		as_dict=True,
	)

	max_prior_resets = int(
		frappe.db.sql(
			"select coalesce(max(handoff_reset_seq), 0) from `tabCSKH SLA Obligation` where work_item = %s",
			(work_item,),
		)[0][0]
		or 0
	)
	grace_min = float(max(cint(policy.get("handoff_min_grace_minutes", 15)), 0))
	max_resets = max(cint(policy.get("max_handoff_grace_resets", 2)), 0)

	closed_ids: list[str] = []
	opened_ids: list[str] = []

	for ob in active_obs:
		ob_type = ob.obligation_type
		# `FIRST_CONNECTED` is work-item-level informational; keep open with updated owner pointer
		if ob_type == "FIRST_CONNECTED":
			_update_obligation(
				ob.name,
				{"owner_user": to_user},
				{"event": "owner_transferred", "from_user": from_user, "to_user": to_user, "at": str(tx_dt)},
			)
			continue

		if ob_type not in ("FIRST_ATTEMPT", "FOLLOWUP"):
			continue

		eff_due_a = _effective_due_with_active_pause(ob, tx_dt)
		wi_orig_due = get_datetime(ob.work_item_original_due_at or ob.original_due_at)
		was_already_breached = ob.status == "breached_open" or tx_dt > eff_due_a

		if not was_already_breached:
			# Transfer BEFORE breach -> A is NOT penalized (`handoff_closed`)
			_update_obligation(
				ob.name,
				{
					"status": "handoff_closed",
					"is_paused": 0,
					"paused_at": None,
					"closed_at": tx_dt,
				},
				{
					"event": "handoff_before_breach",
					"actor": actor,
					"from_user": from_user,
					"to_user": to_user,
					"at": str(tx_dt),
				},
			)
			closed_ids.append(ob.name)

			anchor_due = wi_orig_due if ob_type == "FIRST_ATTEMPT" else eff_due_a
			rem_min = business_minutes_between(tx_dt, anchor_due, calendar)
			if max_prior_resets < max_resets:
				target_min = max(rem_min, grace_min)
				used_reset = 1 if grace_min > rem_min + 1e-6 else 0
				cap_reached = 0
			else:
				target_min = rem_min
				used_reset = 0
				cap_reached = 1

			max_prior_resets += used_reset
			due_b = add_business_minutes(tx_dt, target_min, calendar)
			is_recovery = 0
		else:
			# Transfer AFTER breach -> A permanently retains `breached_handoff`
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due_a
			_update_obligation(
				ob.name,
				{
					"status": "breached_handoff",
					"is_paused": 0,
					"paused_at": None,
					"breached_at": breach_ts,
					"closed_at": tx_dt,
				},
				{
					"event": "handoff_after_breach",
					"actor": actor,
					"from_user": from_user,
					"to_user": to_user,
					"breached_at": str(breach_ts),
					"at": str(tx_dt),
				},
			)
			closed_ids.append(ob.name)
			target_min = grace_min
			due_b = add_business_minutes(tx_dt, target_min, calendar)
			cap_reached = 1 if max_prior_resets >= max_resets else 0
			is_recovery = 1

		task_ref = transferred_task if ob_type == "FOLLOWUP" else None
		if ob_type == "FOLLOWUP" and not task_ref:
			continue

		idem_b = (
			f"FIRST_ATTEMPT:{work_item}:{new_interval_event}"
			if ob_type == "FIRST_ATTEMPT"
			else f"FOLLOWUP:{task_ref}:int:{new_interval_event}"
		)
		new_ob_id = _insert_obligation(
			{
				"obligation_type": ob_type,
				"work_item": work_item,
				"lead": lead,
				"campaign": campaign,
				"interval_event": new_interval_event,
				"owner_user": to_user,
				"team_at_obligation": team_node_b,
				"attribution_status": attr_status_b,
				"followup_task": task_ref,
				"status": "open",
				"informational_only": 0,
				"is_recovery_obligation": is_recovery,
				"handoff_reset_seq": max_prior_resets,
				"handoff_cap_reached": cap_reached,
				"started_at": tx_dt,
				"original_due_at": due_b,
				"effective_due_at": due_b,
				"work_item_original_due_at": wi_orig_due,
				"policy_name_snapshot": policy.get("name"),
				"policy_version_snapshot": cint(policy.get("version", 1)),
				"policy_snapshot_json": pol_json,
				"calendar_snapshot_json": cal_json,
				"audit_events_json": json.dumps(
					[
						{
							"event": "created_handoff",
							"actor": actor,
							"from_user": from_user,
							"to_user": to_user,
							"at": str(tx_dt),
							"target_minutes": target_min,
							"due_at": str(due_b),
							"is_recovery_obligation": bool(is_recovery),
							"handoff_cap_reached": bool(cap_reached),
						}
					],
					ensure_ascii=False,
				),
				"idempotency_key": idem_b,
			}
		)
		opened_ids.append(new_ob_id)

	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=campaign,
		owner_user=from_user,
		event_dt=tx_dt,
		reason="interval_transferred_from",
	)
	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=campaign,
		owner_user=to_user,
		team_node=team_node_b,
		event_dt=tx_dt,
		reason="interval_transferred_to",
	)
	return {"closed": closed_ids, "opened": opened_ids}


def on_interval_revoked_in_tx(
	*,
	work_item: str,
	from_user: str | None,
	revoked_at: datetime | str,
	actor: str,
	reason: str,
) -> list[str]:
	"""Close active obligations when ownership is revoked: preserve breach if already overdue, else `canceled_validly`."""
	if not _p3_sla_ready():
		return []
	rev_dt = get_datetime(revoked_at)
	active_obs = frappe.db.sql(
		"""
		select name, obligation_type, status, effective_due_at, breached_at,
		       is_paused, paused_at, calendar_snapshot_json, campaign, team_at_obligation
		from `tabCSKH SLA Obligation`
		where work_item = %s and status in ('open', 'breached_open')
		for update
		""",
		(work_item,),
		as_dict=True,
	)
	updated: list[str] = []
	for ob in active_obs:
		eff_due = _effective_due_with_active_pause(ob, rev_dt)
		if ob.obligation_type != "FIRST_CONNECTED" and (ob.status == "breached_open" or rev_dt > eff_due):
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			_update_obligation(
				ob.name,
				{
					"status": "breached_handoff",
					"is_paused": 0,
					"paused_at": None,
					"breached_at": breach_ts,
					"closed_at": rev_dt,
				},
				{"event": "revoked_after_breach", "actor": actor, "at": str(rev_dt), "reason": reason},
			)
		else:
			_update_obligation(
				ob.name,
				{
					"status": "canceled_validly",
					"is_paused": 0,
					"paused_at": None,
					"closed_at": rev_dt,
				},
				{"event": "revoked_before_breach", "actor": actor, "at": str(rev_dt), "reason": reason},
			)
		updated.append(ob.name)

	if updated:
		_notify_dirty_post_commit(
			work_item=work_item,
			owner_user=from_user,
			event_dt=rev_dt,
			reason="interval_revoked",
		)
	return updated


def on_followup_created_in_tx(
	*,
	work_item: str,
	lead: str,
	campaign: str | None,
	owner_user: str,
	task_id: str,
	due_dt: datetime | str,
	actor: str,
	now_dt: datetime | str,
) -> str | None:
	"""Spawn a `FOLLOWUP` obligation tied to native `CRM Task` (`task_id`)."""
	if not _p3_sla_ready():
		return None
	created_at = get_datetime(now_dt)
	task_due = get_datetime(due_dt)
	policy, calendar = _resolve_policy_and_calendar(campaign=campaign, owner_user=owner_user)
	if not cint(policy.get("p3_sla_enabled", 1)):
		return None

	open_hist = frappe.db.get_value(
		"CSKH Assignment History",
		{"work_item": work_item, "ownership_end": ["is", "not set"]},
		["name", "team_at_assignment", "attribution_status"],
		order_by="event_at desc, creation desc",
		as_dict=True,
	)
	interval_id = open_hist.name if open_hist else None
	team_node, attr_status = _resolve_interval_attribution(interval_id, owner_user, created_at)

	grace_min = max(cint(policy.get("followup_grace_minutes", 15)), 0)
	sla_due = add_business_minutes(task_due, grace_min, calendar)
	pol_json = json.dumps(policy, ensure_ascii=False, sort_keys=True, default=str)
	cal_json = calendar.to_snapshot_json()

	ob_id = _insert_obligation(
		{
			"obligation_type": "FOLLOWUP",
			"work_item": work_item,
			"lead": lead,
			"campaign": campaign,
			"interval_event": interval_id,
			"owner_user": owner_user,
			"team_at_obligation": team_node,
			"attribution_status": attr_status,
			"followup_task": str(task_id),
			"status": "open",
			"informational_only": 0,
			"reschedule_seq": 0,
			"started_at": created_at,
			"original_due_at": sla_due,
			"effective_due_at": sla_due,
			"work_item_original_due_at": sla_due,
			"policy_name_snapshot": policy.get("name"),
			"policy_version_snapshot": cint(policy.get("version", 1)),
			"policy_snapshot_json": pol_json,
			"calendar_snapshot_json": cal_json,
			"audit_events_json": json.dumps(
				[
					{
						"event": "created_followup",
						"actor": actor,
						"at": str(created_at),
						"task_due_date": str(task_due),
						"grace_minutes": grace_min,
						"sla_due_at": str(sla_due),
					}
				],
				ensure_ascii=False,
			),
			"idempotency_key": f"FOLLOWUP:{task_id}:seq:0",
		}
	)
	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=campaign,
		owner_user=owner_user,
		team_node=team_node,
		event_dt=created_at,
		reason="followup_created",
	)
	return ob_id


def on_followup_rescheduled_in_tx(
	*,
	work_item: str,
	task_id: str,
	new_due_dt: datetime | str,
	new_reschedule_count: int,
	actor: str,
	reason: str,
	now_dt: datetime | str,
) -> str | None:
	"""Handle callback reschedule: enforce cap for Staff, preserve prior breach if overdue, spawn next tracker."""
	if not _p3_sla_ready():
		return None
	resched_at = get_datetime(now_dt)
	task_due = get_datetime(new_due_dt)
	wi_row = frappe.db.get_value(
		"CSKH Work Item",
		work_item,
		["name", "lead", "campaign", "assigned_to"],
		as_dict=True,
	)
	if not wi_row or not wi_row.assigned_to:
		return None

	policy, calendar = _resolve_policy_and_calendar(campaign=wi_row.campaign, owner_user=wi_row.assigned_to)
	if not cint(policy.get("p3_sla_enabled", 1)):
		return None

	max_self_resched = max(cint(policy.get("max_reschedules_without_approval", 2)), 0)
	is_privileged = _is_system(actor) or ROLE_ADMIN in _roles(actor) or _is_leader(actor)
	if not is_privileged and cint(new_reschedule_count) > max_self_resched:
		raise PolicyConflict(
			f"Số lần tự dời lịch hẹn ({new_reschedule_count}) vượt giới hạn cho phép ({max_self_resched}); "
			"cần Team Leader hoặc CRM Admin phê duyệt."
		)

	active_fu = frappe.db.sql(
		"""
		select name, status, original_due_at, effective_due_at, work_item_original_due_at,
		       breached_at, is_paused, paused_at, calendar_snapshot_json
		from `tabCSKH SLA Obligation`
		where work_item = %s and followup_task = %s and obligation_type = 'FOLLOWUP'
		  and status in ('open', 'breached_open')
		order by creation desc
		for update
		""",
		(work_item, str(task_id)),
		as_dict=True,
	)

	is_recovery = 0
	wi_orig_due = None
	for ob in active_fu:
		eff_due = _effective_due_with_active_pause(ob, resched_at)
		wi_orig_due = wi_orig_due or get_datetime(ob.work_item_original_due_at or ob.original_due_at)
		if ob.status == "breached_open" or resched_at > eff_due:
			# Reschedule AFTER breach -> NEVER erase prior breach!
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			_update_obligation(
				ob.name,
				{
					"status": "breached_handoff",
					"is_paused": 0,
					"paused_at": None,
					"breached_at": breach_ts,
					"closed_at": resched_at,
				},
				{
					"event": "rescheduled_after_breach_preserved",
					"actor": actor,
					"at": str(resched_at),
					"breached_at": str(breach_ts),
					"new_task_due": str(task_due),
					"reason": reason,
				},
			)
			is_recovery = 1
		else:
			# Reschedule BEFORE breach -> valid `superseded`
			_update_obligation(
				ob.name,
				{
					"status": "superseded",
					"is_paused": 0,
					"paused_at": None,
					"closed_at": resched_at,
				},
				{
					"event": "rescheduled_before_breach",
					"actor": actor,
					"at": str(resched_at),
					"new_task_due": str(task_due),
					"reason": reason,
				},
			)

	open_hist = frappe.db.get_value(
		"CSKH Assignment History",
		{"work_item": work_item, "ownership_end": ["is", "not set"]},
		["name", "team_at_assignment", "attribution_status"],
		order_by="event_at desc, creation desc",
		as_dict=True,
	)
	interval_id = open_hist.name if open_hist else None
	team_node, attr_status = _resolve_interval_attribution(interval_id, wi_row.assigned_to, resched_at)

	grace_min = max(cint(policy.get("followup_grace_minutes", 15)), 0)
	sla_due = add_business_minutes(task_due, grace_min, calendar)
	pol_json = json.dumps(policy, ensure_ascii=False, sort_keys=True, default=str)
	cal_json = calendar.to_snapshot_json()

	new_ob_id = _insert_obligation(
		{
			"obligation_type": "FOLLOWUP",
			"work_item": work_item,
			"lead": wi_row.lead,
			"campaign": wi_row.campaign,
			"interval_event": interval_id,
			"owner_user": wi_row.assigned_to,
			"team_at_obligation": team_node,
			"attribution_status": attr_status,
			"followup_task": str(task_id),
			"status": "open",
			"informational_only": 0,
			"is_recovery_obligation": is_recovery,
			"reschedule_seq": cint(new_reschedule_count),
			"started_at": resched_at,
			"original_due_at": sla_due,
			"effective_due_at": sla_due,
			"work_item_original_due_at": sla_due if not is_recovery else (wi_orig_due or sla_due),
			"policy_name_snapshot": policy.get("name"),
			"policy_version_snapshot": cint(policy.get("version", 1)),
			"policy_snapshot_json": pol_json,
			"calendar_snapshot_json": cal_json,
			"audit_events_json": json.dumps(
				[
					{
						"event": "created_reschedule",
						"actor": actor,
						"at": str(resched_at),
						"reschedule_seq": cint(new_reschedule_count),
						"is_recovery_obligation": bool(is_recovery),
						"sla_due_at": str(sla_due),
					}
				],
				ensure_ascii=False,
			),
			"idempotency_key": f"FOLLOWUP:{task_id}:seq:{cint(new_reschedule_count)}",
		}
	)
	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=wi_row.campaign,
		owner_user=wi_row.assigned_to,
		team_node=team_node,
		event_dt=resched_at,
		reason="followup_rescheduled",
	)
	return new_ob_id


def on_followup_resolved_in_tx(
	*,
	work_item: str,
	task_id: str,
	attempt_id: str,
	occurred_at: datetime | str,
	actor: str,
) -> list[str]:
	"""Fulfill active `FOLLOWUP` obligation using `occurred_at` of the resolving attempt."""
	if not _p3_sla_ready():
		return []
	att_dt = get_datetime(occurred_at)
	active_fu = frappe.db.sql(
		"""
		select name, status, effective_due_at, breached_at, is_paused, paused_at, calendar_snapshot_json,
		       owner_user, campaign, team_at_obligation
		from `tabCSKH SLA Obligation`
		where work_item = %s and followup_task = %s and obligation_type = 'FOLLOWUP'
		  and status in ('open', 'breached_open')
		for update
		""",
		(work_item, str(task_id)),
		as_dict=True,
	)
	resolved_ids: list[str] = []
	for ob in active_fu:
		eff_due = _effective_due_with_active_pause(ob, att_dt)
		if att_dt <= eff_due:
			_update_obligation(
				ob.name,
				{
					"status": "fulfilled_on_time",
					"is_paused": 0,
					"paused_at": None,
					"fulfilled_at": att_dt,
					"fulfilled_by_attempt": attempt_id,
					"breached_at": None,
				},
				{
					"event": "fulfilled_on_time",
					"actor": actor,
					"attempt_id": attempt_id,
					"occurred_at": str(att_dt),
				},
			)
		else:
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			_update_obligation(
				ob.name,
				{
					"status": "fulfilled_late",
					"is_paused": 0,
					"paused_at": None,
					"fulfilled_at": att_dt,
					"fulfilled_by_attempt": attempt_id,
					"breached_at": breach_ts,
				},
				{
					"event": "fulfilled_late",
					"actor": actor,
					"attempt_id": attempt_id,
					"occurred_at": str(att_dt),
					"breached_at": str(breach_ts),
				},
			)
		resolved_ids.append(ob.name)
		_notify_dirty_post_commit(
			work_item=work_item,
			campaign=ob.campaign,
			owner_user=ob.owner_user,
			team_node=ob.team_at_obligation,
			event_dt=att_dt,
			reason="followup_resolved",
		)
	return resolved_ids


def on_followup_canceled_in_tx(
	*,
	work_item: str,
	task_id: str,
	canceled_at: datetime | str,
	actor: str,
	reason: str,
	is_valid_system_cancel: bool = False,
) -> list[str]:
	"""Handle callback cancel: preserve breach if already overdue; valid system cancel (DNC/revoke/close) closes cleanly."""
	if not _p3_sla_ready():
		return []
	canc_dt = get_datetime(canceled_at)
	active_fu = frappe.db.sql(
		"""
		select name, status, effective_due_at, breached_at, is_paused, paused_at, calendar_snapshot_json,
		       owner_user, campaign, team_at_obligation
		from `tabCSKH SLA Obligation`
		where work_item = %s and followup_task = %s and obligation_type = 'FOLLOWUP'
		  and status in ('open', 'breached_open')
		for update
		""",
		(work_item, str(task_id)),
		as_dict=True,
	)
	updated: list[str] = []
	for ob in active_fu:
		eff_due = _effective_due_with_active_pause(ob, canc_dt)
		if ob.status == "breached_open" or canc_dt > eff_due:
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			_update_obligation(
				ob.name,
				{
					"status": "breached_handoff",
					"is_paused": 0,
					"paused_at": None,
					"breached_at": breach_ts,
					"closed_at": canc_dt,
				},
				{"event": "canceled_after_breach", "actor": actor, "at": str(canc_dt), "reason": reason},
			)
		else:
			_update_obligation(
				ob.name,
				{
					"status": "canceled_validly",
					"is_paused": 0,
					"paused_at": None,
					"closed_at": canc_dt,
				},
				{
					"event": "canceled_validly" if is_valid_system_cancel else "canceled_before_due",
					"actor": actor,
					"at": str(canc_dt),
					"reason": reason,
				},
			)
		updated.append(ob.name)
		_notify_dirty_post_commit(
			work_item=work_item,
			campaign=ob.campaign,
			owner_user=ob.owner_user,
			team_node=ob.team_at_obligation,
			event_dt=canc_dt,
			reason="followup_canceled",
		)
	return updated


def on_attempt_recorded_in_tx(
	*,
	work_item: str,
	attempt_id: str,
	occurred_at: datetime | str,
	outcome: dict,
	actor: str,
) -> list[str]:
	"""Fulfill `FIRST_ATTEMPT` / `FIRST_CONNECTED` and handle DNC termination upon `record_attempt`."""
	if not _p3_sla_ready():
		return []
	att_dt = get_datetime(occurred_at)
	active_obs = frappe.db.sql(
		"""
		select name, obligation_type, status, effective_due_at, breached_at,
		       is_paused, paused_at, calendar_snapshot_json, owner_user, campaign, team_at_obligation
		from `tabCSKH SLA Obligation`
		where work_item = %s and status in ('open', 'breached_open')
		for update
		""",
		(work_item,),
		as_dict=True,
	)
	updated: list[str] = []
	is_contact = bool(outcome.get("is_contact_attempt", True))
	is_connected = bool(outcome.get("is_connected", False))
	is_dnc = bool(outcome.get("is_do_not_contact", False))

	for ob in active_obs:
		eff_due = _effective_due_with_active_pause(ob, att_dt)
		if ob.obligation_type == "FIRST_ATTEMPT" and is_contact:
			if att_dt <= eff_due:
				_update_obligation(
					ob.name,
					{
						"status": "fulfilled_on_time",
						"is_paused": 0,
						"paused_at": None,
						"fulfilled_at": att_dt,
						"fulfilled_by_attempt": attempt_id,
						"breached_at": None,
					},
					{
						"event": "fulfilled_on_time",
						"actor": actor,
						"attempt_id": attempt_id,
						"occurred_at": str(att_dt),
					},
				)
			else:
				breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
				_update_obligation(
					ob.name,
					{
						"status": "fulfilled_late",
						"is_paused": 0,
						"paused_at": None,
						"fulfilled_at": att_dt,
						"fulfilled_by_attempt": attempt_id,
						"breached_at": breach_ts,
					},
					{
						"event": "fulfilled_late",
						"actor": actor,
						"attempt_id": attempt_id,
						"occurred_at": str(att_dt),
						"breached_at": str(breach_ts),
					},
				)
			updated.append(ob.name)
		elif ob.obligation_type == "FIRST_CONNECTED" and is_connected:
			if att_dt <= eff_due:
				_update_obligation(
					ob.name,
					{
						"status": "fulfilled_on_time",
						"is_paused": 0,
						"paused_at": None,
						"fulfilled_at": att_dt,
						"fulfilled_by_attempt": attempt_id,
						"breached_at": None,
					},
					{
						"event": "connected_within_target",
						"actor": actor,
						"attempt_id": attempt_id,
						"occurred_at": str(att_dt),
					},
				)
			else:
				breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
				_update_obligation(
					ob.name,
					{
						"status": "fulfilled_late",
						"is_paused": 0,
						"paused_at": None,
						"fulfilled_at": att_dt,
						"fulfilled_by_attempt": attempt_id,
						"breached_at": breach_ts,
					},
					{
						"event": "connected_after_target",
						"actor": actor,
						"attempt_id": attempt_id,
						"occurred_at": str(att_dt),
					},
				)
			updated.append(ob.name)
		elif is_dnc:
			if ob.obligation_type != "FIRST_CONNECTED" and (ob.status == "breached_open" or att_dt > eff_due):
				breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
				_update_obligation(
					ob.name,
					{
						"status": "breached_handoff",
						"is_paused": 0,
						"paused_at": None,
						"breached_at": breach_ts,
						"closed_at": att_dt,
					},
					{
						"event": "dnc_after_breach",
						"actor": actor,
						"attempt_id": attempt_id,
						"at": str(att_dt),
					},
				)
			else:
				_update_obligation(
					ob.name,
					{
						"status": "canceled_validly",
						"is_paused": 0,
						"paused_at": None,
						"closed_at": att_dt,
					},
					{
						"event": "dnc_canceled_validly",
						"actor": actor,
						"attempt_id": attempt_id,
						"at": str(att_dt),
					},
				)
			updated.append(ob.name)

	wi_info = (
		frappe.db.get_value("CSKH Work Item", work_item, ["campaign", "assigned_to"], as_dict=True) or {}
	)
	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=wi_info.get("campaign"),
		owner_user=wi_info.get("assigned_to") or actor,
		event_dt=att_dt,
		reason="attempt_recorded",
	)
	return updated


def on_work_item_closed_in_tx(
	*,
	work_item: str,
	lead: str,
	campaign: str | None,
	owner_user: str | None,
	closed_at: datetime | str,
	actor: str,
	close_category: str = "normal",
	is_dnc: bool = False,
) -> list[str]:
	"""Finalize remaining open obligations and evaluate policy-gated `MIN_ATTEMPTS` upon Work Item close."""
	if not _p3_sla_ready():
		return []
	cl_dt = get_datetime(closed_at)
	policy, calendar = _resolve_policy_and_calendar(campaign=campaign, owner_user=owner_user)
	if not cint(policy.get("p3_sla_enabled", 1)):
		return []

	active_obs = frappe.db.sql(
		"""
		select name, obligation_type, status, effective_due_at, breached_at, is_paused, paused_at, calendar_snapshot_json
		from `tabCSKH SLA Obligation`
		where work_item = %s and status in ('open', 'breached_open')
		for update
		""",
		(work_item,),
		as_dict=True,
	)
	touched: list[str] = []
	for ob in active_obs:
		eff_due = _effective_due_with_active_pause(ob, cl_dt)
		if ob.obligation_type != "FIRST_CONNECTED" and (ob.status == "breached_open" or cl_dt > eff_due):
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			_update_obligation(
				ob.name,
				{
					"status": "breached_handoff",
					"is_paused": 0,
					"paused_at": None,
					"breached_at": breach_ts,
					"closed_at": cl_dt,
				},
				{"event": "closed_after_breach", "actor": actor, "at": str(cl_dt)},
			)
		else:
			_update_obligation(
				ob.name,
				{
					"status": "canceled_validly",
					"is_paused": 0,
					"paused_at": None,
					"closed_at": cl_dt,
				},
				{"event": "closed_validly", "actor": actor, "at": str(cl_dt)},
			)
		touched.append(ob.name)

	# Evaluate `MIN_ATTEMPTS` ONLY when explicitly enabled in policy and closing an unreached/non-DNC/non-invalid item
	if (
		cint(policy.get("min_attempts_enabled", 0))
		and cint(policy.get("min_attempts_before_close", 0)) > 0
		and not is_dnc
		and close_category != "invalid_or_duplicate"
	):
		from crm_cskh.services.read_model import compute_effective_attempts

		eff_attempts = [
			a
			for a in compute_effective_attempts(work_item)
			if not a.get("is_voided") and bool(a["effective"].get("is_contact_attempt", True))
		]
		connected_any = any(bool(a["effective"].get("is_connected")) for a in eff_attempts)
		if not connected_any:
			req_count = cint(policy.get("min_attempts_before_close", 3))
			min_gap = max(cint(policy.get("min_attempt_gap_minutes", 0)), 0)
			spaced_count = 0
			last_counted_dt: datetime | None = None
			for a in sorted(eff_attempts, key=lambda x: get_datetime(x["effective"]["occurred_at"])):
				cur_t = get_datetime(a["effective"]["occurred_at"])
				if last_counted_dt is None or (cur_t - last_counted_dt).total_seconds() >= min_gap * 60:
					spaced_count += 1
					last_counted_dt = cur_t

			met = spaced_count >= req_count
			last_hist = frappe.db.get_value(
				"CSKH Assignment History",
				{"work_item": work_item, "action": ["in", ["assign", "reassign", "reopen"]]},
				["name", "team_at_assignment", "attribution_status"],
				order_by="event_at desc, creation desc",
				as_dict=True,
			)
			interval_id = last_hist.name if last_hist else None
			team_node, attr_status = _resolve_interval_attribution(interval_id, owner_user, cl_dt)
			ob_id = _insert_obligation(
				{
					"obligation_type": "MIN_ATTEMPTS",
					"work_item": work_item,
					"lead": lead,
					"campaign": campaign,
					"interval_event": interval_id,
					"owner_user": owner_user,
					"team_at_obligation": team_node,
					"attribution_status": attr_status,
					"status": "fulfilled_on_time" if met else "breached_open",
					"informational_only": 0,
					"started_at": cl_dt,
					"original_due_at": cl_dt,
					"effective_due_at": cl_dt,
					"work_item_original_due_at": cl_dt,
					"fulfilled_at": cl_dt if met else None,
					"breached_at": None if met else cl_dt,
					"closed_at": cl_dt,
					"policy_name_snapshot": policy.get("name"),
					"policy_version_snapshot": cint(policy.get("version", 1)),
					"policy_snapshot_json": json.dumps(
						policy, ensure_ascii=False, sort_keys=True, default=str
					),
					"calendar_snapshot_json": calendar.to_snapshot_json(),
					"audit_events_json": json.dumps(
						[
							{
								"event": "evaluated_min_attempts_on_close",
								"actor": actor,
								"at": str(cl_dt),
								"required_attempts": req_count,
								"min_gap_minutes": min_gap,
								"spaced_effective_attempts": spaced_count,
								"met": met,
							}
						],
						ensure_ascii=False,
					),
					"idempotency_key": f"MIN_ATTEMPTS:{work_item}:close:{cl_dt.isoformat()}",
				}
			)
			touched.append(ob_id)

	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=campaign,
		owner_user=owner_user,
		event_dt=cl_dt,
		reason="work_item_closed",
	)
	return touched


def on_lead_dnc_set_in_tx(
	*,
	lead: str,
	dnc_at: datetime | str,
	actor: str,
	reason: str,
) -> list[str]:
	"""Terminate open outreach obligations on all Work Items of `lead` when Admin sets global DNC."""
	if not _p3_sla_ready():
		return []
	ts = get_datetime(dnc_at)
	active_obs = frappe.db.sql(
		"""
		select name, work_item, obligation_type, status, effective_due_at, breached_at,
		       is_paused, paused_at, calendar_snapshot_json, owner_user, campaign, team_at_obligation
		from `tabCSKH SLA Obligation`
		where lead = %s and status in ('open', 'breached_open')
		for update
		""",
		(lead,),
		as_dict=True,
	)
	updated: list[str] = []
	for ob in active_obs:
		eff_due = _effective_due_with_active_pause(ob, ts)
		if ob.obligation_type != "FIRST_CONNECTED" and (ob.status == "breached_open" or ts > eff_due):
			breach_ts = get_datetime(ob.breached_at) if ob.breached_at else eff_due
			_update_obligation(
				ob.name,
				{
					"status": "breached_handoff",
					"is_paused": 0,
					"paused_at": None,
					"breached_at": breach_ts,
					"closed_at": ts,
				},
				{"event": "admin_dnc_after_breach", "actor": actor, "at": str(ts), "reason": reason},
			)
		else:
			_update_obligation(
				ob.name,
				{
					"status": "canceled_validly",
					"is_paused": 0,
					"paused_at": None,
					"closed_at": ts,
				},
				{"event": "admin_dnc_canceled_validly", "actor": actor, "at": str(ts), "reason": reason},
			)
		updated.append(ob.name)
		_notify_dirty_post_commit(
			work_item=ob.work_item,
			campaign=ob.campaign,
			owner_user=ob.owner_user,
			team_node=ob.team_at_obligation,
			event_dt=ts,
			reason="admin_lead_dnc",
		)
	return updated


def on_correction_reviewed_in_tx(
	*,
	work_item: str,
	attempt_id: str,
	action: str,
	reviewed_at: datetime | str,
	actor: str,
) -> list[str]:
	"""Reconcile obligations when an approved correction voids, restores, or updates an attempt."""
	if not _p3_sla_ready():
		return []
	rev_dt = get_datetime(reviewed_at)
	from crm_cskh.services.read_model import compute_effective_attempts

	projected = compute_effective_attempts(work_item)
	by_id = {a["attempt_id"]: a for a in projected}
	valid_attempts = [
		a
		for a in projected
		if not a.get("is_voided") and bool(a["effective"].get("is_contact_attempt", True))
	]
	valid_attempts.sort(key=lambda x: get_datetime(x["effective"]["occurred_at"]))
	connected_attempts = [a for a in valid_attempts if bool(a["effective"].get("is_connected"))]

	obs = frappe.db.sql(
		"""
		select name, obligation_type, status, owner_user, interval_event, followup_task,
		       effective_due_at, fulfilled_at, fulfilled_by_attempt, breached_at,
		       campaign, team_at_obligation
		from `tabCSKH SLA Obligation`
		where work_item = %s
		  and (fulfilled_by_attempt = %s or status in ('open', 'breached_open', 'fulfilled_on_time', 'fulfilled_late'))
		for update
		""",
		(work_item, attempt_id),
		as_dict=True,
	)
	wi_status = frappe.db.get_value("CSKH Work Item", work_item, "status")
	touched: list[str] = []

	for ob in obs:
		eff_due = get_datetime(ob.effective_due_at)
		if ob.obligation_type == "FIRST_ATTEMPT":
			candidates = [
				a
				for a in valid_attempts
				if not ob.interval_event or a.get("ownership_event") == ob.interval_event
			]
			if ob.fulfilled_by_attempt == attempt_id or (
				candidates and ob.status in ("open", "breached_open")
			):
				if candidates:
					first_a = candidates[0]
					occ = get_datetime(first_a["effective"]["occurred_at"])
					new_status = "fulfilled_on_time" if occ <= eff_due else "fulfilled_late"
					_update_obligation(
						ob.name,
						{
							"status": new_status,
							"fulfilled_at": occ,
							"fulfilled_by_attempt": first_a["attempt_id"],
							"breached_at": None if new_status == "fulfilled_on_time" else eff_due,
						},
						{
							"event": f"reconciled_after_{action}",
							"actor": actor,
							"at": str(rev_dt),
							"new_attempt": first_a["attempt_id"],
						},
					)
				else:
					if rev_dt <= eff_due and wi_status in ("assigned", "in_progress", "on_hold"):
						new_status = "open"
						breach_ts = None
					else:
						new_status = (
							"breached_open"
							if wi_status in ("assigned", "in_progress", "on_hold")
							else "breached_handoff"
						)
						breach_ts = eff_due
					_update_obligation(
						ob.name,
						{
							"status": new_status,
							"fulfilled_at": None,
							"fulfilled_by_attempt": None,
							"breached_at": breach_ts,
						},
						{
							"event": f"unfulfilled_after_{action}",
							"actor": actor,
							"at": str(rev_dt),
							"voided_attempt": attempt_id,
						},
					)
				touched.append(ob.name)

		elif ob.obligation_type == "FOLLOWUP" and (
			ob.fulfilled_by_attempt == attempt_id
			or (
				attempt_id in by_id
				and by_id[attempt_id].get("resolved_callback_task") == ob.followup_task
				and ob.status in ("open", "breached_open")
			)
		):
			target_att = by_id.get(ob.fulfilled_by_attempt or attempt_id)
			if (
				target_att
				and not target_att.get("is_voided")
				and bool(target_att["effective"].get("is_contact_attempt", True))
			):
				occ = get_datetime(target_att["effective"]["occurred_at"])
				new_status = "fulfilled_on_time" if occ <= eff_due else "fulfilled_late"
				_update_obligation(
					ob.name,
					{
						"status": new_status,
						"fulfilled_at": occ,
						"fulfilled_by_attempt": target_att["attempt_id"],
						"breached_at": None if new_status == "fulfilled_on_time" else eff_due,
					},
					{"event": f"followup_reconciled_{action}", "actor": actor, "at": str(rev_dt)},
				)
			else:
				if rev_dt <= eff_due and wi_status in ("assigned", "in_progress", "on_hold"):
					new_status = "open"
					breach_ts = None
				else:
					new_status = (
						"breached_open"
						if wi_status in ("assigned", "in_progress", "on_hold")
						else "breached_handoff"
					)
					breach_ts = eff_due
				_update_obligation(
					ob.name,
					{
						"status": new_status,
						"fulfilled_at": None,
						"fulfilled_by_attempt": None,
						"breached_at": breach_ts,
					},
					{"event": f"followup_unfulfilled_{action}", "actor": actor, "at": str(rev_dt)},
				)
			touched.append(ob.name)

		elif ob.obligation_type == "FIRST_CONNECTED" and (
			ob.fulfilled_by_attempt == attempt_id
			or (connected_attempts and ob.status in ("open", "breached_open"))
		):
			if connected_attempts:
				first_c = connected_attempts[0]
				occ = get_datetime(first_c["effective"]["occurred_at"])
				new_status = "fulfilled_on_time" if occ <= eff_due else "fulfilled_late"
				_update_obligation(
					ob.name,
					{
						"status": new_status,
						"fulfilled_at": occ,
						"fulfilled_by_attempt": first_c["attempt_id"],
						"breached_at": None if new_status == "fulfilled_on_time" else eff_due,
					},
					{"event": f"connected_reconciled_{action}", "actor": actor, "at": str(rev_dt)},
				)
			else:
				new_status = "open" if rev_dt <= eff_due else "breached_open"
				_update_obligation(
					ob.name,
					{
						"status": new_status,
						"fulfilled_at": None,
						"fulfilled_by_attempt": None,
						"breached_at": None if new_status == "open" else eff_due,
					},
					{"event": f"connected_cleared_{action}", "actor": actor, "at": str(rev_dt)},
				)
			touched.append(ob.name)

	att_occ = frappe.db.get_value("CSKH Contact Attempt", attempt_id, "occurred_at")
	wi_info = (
		frappe.db.get_value("CSKH Work Item", work_item, ["campaign", "assigned_to"], as_dict=True) or {}
	)
	if att_occ:
		_notify_dirty_post_commit(
			work_item=work_item,
			campaign=wi_info.get("campaign"),
			owner_user=wi_info.get("assigned_to"),
			event_dt=att_occ,
			reason="prior_period_correction",
		)
	_notify_dirty_post_commit(
		work_item=work_item,
		campaign=wi_info.get("campaign"),
		owner_user=wi_info.get("assigned_to"),
		event_dt=rev_dt,
		reason="correction_reviewed",
	)
	return touched


# ---------------------------------------------------------------------------
# 2. Explicit audited actions (`waive`, `pause`, `resume`) & query APIs
# ---------------------------------------------------------------------------


def _assert_sla_admin_or_authorized_leader(ob_doc, actor: str, *, action: str) -> dict:
	roles = _roles(actor)
	if _is_system(actor) or ROLE_ADMIN in roles:
		return {}
	if not _is_leader(actor):
		raise ForbiddenScope("Nhân viên hoặc BOD không có quyền miễn trừ hoặc tạm dừng nghĩa vụ SLA.")
	# Leader must NOT waive/pause their own personal obligation AND must manage the obligation's owner/team
	if ob_doc.owner_user == actor:
		raise ForbiddenScope("Team Leader không được tự miễn trừ hoặc tạm dừng nghĩa vụ SLA của chính mình.")
	if not (ob_doc.owner_user and _in_team(actor, ob_doc.owner_user)):
		raise ForbiddenScope("Nghĩa vụ SLA không thuộc phạm vi nhân sự trong team quản lý của bạn.")
	pol, _ = _resolve_policy_and_calendar(campaign=ob_doc.campaign, owner_user=ob_doc.owner_user)
	if action == "waive" and not cint(pol.get("allow_leader_waive_sla", 1)):
		raise ForbiddenScope("Chính sách hiện hành không cho phép Team Leader miễn trừ SLA.")
	if action in ("pause", "resume") and not cint(pol.get("allow_leader_pause_sla", 1)):
		raise ForbiddenScope("Chính sách hiện hành không cho phép Team Leader tạm dừng SLA.")
	return pol


def serialize_obligation(row: dict, *, include_audit: bool = False) -> dict:
	tz = "Asia/Ho_Chi_Minh"
	payload = {
		"obligation_id": row.get("name"),
		"obligation_type": row.get("obligation_type"),
		"work_item": row.get("work_item"),
		"campaign": row.get("campaign"),
		"interval_event": row.get("interval_event"),
		"owner_user": row.get("owner_user"),
		"team_at_obligation": row.get("team_at_obligation"),
		"attribution_status": row.get("attribution_status") or AUTHORITATIVE,
		"followup_task": row.get("followup_task"),
		"status": row.get("status"),
		"informational_only": bool(cint(row.get("informational_only"))),
		"is_recovery_obligation": bool(cint(row.get("is_recovery_obligation"))),
		"handoff_reset_seq": cint(row.get("handoff_reset_seq")),
		"handoff_cap_reached": bool(cint(row.get("handoff_cap_reached"))),
		"reschedule_seq": cint(row.get("reschedule_seq")),
		"started_at": format_calendar_iso(row.get("started_at"), tz),
		"original_due_at": format_calendar_iso(row.get("original_due_at"), tz),
		"effective_due_at": format_calendar_iso(row.get("effective_due_at"), tz),
		"work_item_original_due_at": format_calendar_iso(row.get("work_item_original_due_at"), tz),
		"is_paused": bool(cint(row.get("is_paused"))),
		"paused_at": format_calendar_iso(row.get("paused_at"), tz),
		"total_paused_business_minutes": round(flt(row.get("total_paused_business_minutes")), 2),
		"fulfilled_at": format_calendar_iso(row.get("fulfilled_at"), tz),
		"fulfilled_by_attempt": row.get("fulfilled_by_attempt"),
		"breached_at": format_calendar_iso(row.get("breached_at"), tz),
		"closed_at": format_calendar_iso(row.get("closed_at"), tz),
		"waived_by": row.get("waived_by"),
		"waived_at": format_calendar_iso(row.get("waived_at"), tz),
		"policy_name_snapshot": row.get("policy_name_snapshot"),
		"policy_version_snapshot": cint(row.get("policy_version_snapshot") or 1),
		"timezone": tz,
	}
	if include_audit:
		payload["waive_reason"] = row.get("waive_reason")
		payload["audit_events"] = _load_audit_events(row.get("audit_events_json"))
	return payload


def waive_obligation(obligation_id: str, reason: str | None, actor: str | None = None) -> dict:
	actor = actor or frappe.session.user
	if actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập.")
	ob_id = parse_optional_str(obligation_id, "Mã nghĩa vụ SLA không hợp lệ.")
	if not ob_id:
		raise InvalidRequest("Mã nghĩa vụ SLA (obligation_id) là bắt buộc.")
	clean_reason = sanitize_note_text(
		parse_optional_str(reason, "Lý do miễn trừ không hợp lệ."), max_length=1000
	)
	if not clean_reason:
		raise NoteRequired("Miễn trừ SLA bắt buộc nhập lý do kiểm toán (reason).")

	if not frappe.db.exists("CSKH SLA Obligation", ob_id):
		if _is_system(actor) or ROLE_ADMIN in _roles(actor):
			raise NotFound("Không tìm thấy nghĩa vụ SLA.")
		raise ForbiddenScope("Bạn không có quyền thao tác trên nghĩa vụ SLA này.")

	ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_id)
	_assert_sla_admin_or_authorized_leader(ob_doc, actor, action="waive")

	if ob_doc.status not in ("open", "breached_open", "fulfilled_late", "breached_handoff"):
		raise PolicyConflict(f"Không thể miễn trừ nghĩa vụ SLA đang ở trạng thái '{ob_doc.status}'.")

	now_dt = now_datetime()
	_update_obligation(
		ob_id,
		{
			"status": "waived",
			"is_paused": 0,
			"paused_at": None,
			"waived_by": actor,
			"waived_at": now_dt,
			"waive_reason": clean_reason,
			"closed_at": ob_doc.closed_at or now_dt,
		},
		{"event": "waived", "actor": actor, "at": str(now_dt), "reason": clean_reason},
	)
	_notify_dirty_post_commit(
		work_item=ob_doc.work_item,
		campaign=ob_doc.campaign,
		owner_user=ob_doc.owner_user,
		team_node=ob_doc.team_at_obligation,
		event_dt=ob_doc.evaluation_anchor_at or now_dt,
		reason="obligation_waived",
	)
	frappe.db.commit()
	refreshed = frappe.get_doc("CSKH SLA Obligation", ob_id).as_dict()
	return serialize_obligation(refreshed, include_audit=True)


def pause_obligation(obligation_id: str, reason: str | None, actor: str | None = None, now=None) -> dict:
	actor = actor or frappe.session.user
	if actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập.")
	ob_id = parse_optional_str(obligation_id, "Mã nghĩa vụ SLA không hợp lệ.")
	if not ob_id:
		raise InvalidRequest("Mã nghĩa vụ SLA (obligation_id) là bắt buộc.")
	clean_reason = sanitize_note_text(
		parse_optional_str(reason, "Lý do tạm dừng không hợp lệ."), max_length=1000
	)
	if not clean_reason:
		raise NoteRequired("Tạm dừng SLA bắt buộc nhập lý do kiểm toán (reason).")

	if not frappe.db.exists("CSKH SLA Obligation", ob_id):
		if _is_system(actor) or ROLE_ADMIN in _roles(actor):
			raise NotFound("Không tìm thấy nghĩa vụ SLA.")
		raise ForbiddenScope("Bạn không có quyền thao tác trên nghĩa vụ SLA này.")

	ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_id)
	_assert_sla_admin_or_authorized_leader(ob_doc, actor, action="pause")

	now_dt = get_datetime(now or now_datetime())
	if ob_doc.status != "open":
		raise PolicyConflict(
			f"Chỉ nghĩa vụ SLA đang mở ('open') mới có thể tạm dừng (hiện tại: '{ob_doc.status}')."
		)
	if cint(ob_doc.is_paused):
		raise PolicyConflict("Nghĩa vụ SLA này đang ở trạng thái tạm dừng.")
	if now_dt > get_datetime(ob_doc.effective_due_at):
		raise PolicyConflict("Nghĩa vụ SLA đã quá hạn; không được tạm dừng lùi sau thời điểm quá hạn.")

	_update_obligation(
		ob_id,
		{"is_paused": 1, "paused_at": now_dt},
		{"event": "paused", "actor": actor, "at": str(now_dt), "reason": clean_reason},
	)
	frappe.db.commit()
	return serialize_obligation(frappe.get_doc("CSKH SLA Obligation", ob_id).as_dict(), include_audit=True)


def resume_obligation(
	obligation_id: str, reason: str | None = None, actor: str | None = None, now=None
) -> dict:
	actor = actor or frappe.session.user
	if actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập.")
	ob_id = parse_optional_str(obligation_id, "Mã nghĩa vụ SLA không hợp lệ.")
	if not ob_id:
		raise InvalidRequest("Mã nghĩa vụ SLA (obligation_id) là bắt buộc.")
	clean_reason = (
		sanitize_note_text(parse_optional_str(reason, "Ghi chú tiếp tục không hợp lệ."), max_length=1000)
		or "Tiếp tục tính SLA"
	)

	if not frappe.db.exists("CSKH SLA Obligation", ob_id):
		if _is_system(actor) or ROLE_ADMIN in _roles(actor):
			raise NotFound("Không tìm thấy nghĩa vụ SLA.")
		raise ForbiddenScope("Bạn không có quyền thao tác trên nghĩa vụ SLA này.")

	ob_doc = frappe.get_doc("CSKH SLA Obligation", ob_id)
	_assert_sla_admin_or_authorized_leader(ob_doc, actor, action="resume")

	if ob_doc.status != "open" or not cint(ob_doc.is_paused) or not ob_doc.paused_at:
		raise PolicyConflict("Nghĩa vụ SLA hiện không ở trạng thái tạm dừng.")

	now_dt = get_datetime(now or now_datetime())
	cal = _calendar_from_obligation(ob_doc.as_dict())
	paused_biz_min = business_minutes_between(ob_doc.paused_at, now_dt, cal)
	new_eff_due = add_business_minutes(ob_doc.effective_due_at, paused_biz_min, cal)
	new_total_paused = round(flt(ob_doc.total_paused_business_minutes) + paused_biz_min, 6)

	_update_obligation(
		ob_id,
		{
			"is_paused": 0,
			"paused_at": None,
			"effective_due_at": new_eff_due,
			"total_paused_business_minutes": new_total_paused,
		},
		{
			"event": "resumed",
			"actor": actor,
			"at": str(now_dt),
			"paused_business_minutes": round(paused_biz_min, 4),
			"new_effective_due_at": str(new_eff_due),
			"reason": clean_reason,
		},
	)
	frappe.db.commit()
	return serialize_obligation(frappe.get_doc("CSKH SLA Obligation", ob_id).as_dict(), include_audit=True)


def list_obligations(
	work_item: str | None = None,
	status: str | None = None,
	obligation_type: str | None = None,
	page=1,
	page_size=20,
	actor: str | None = None,
) -> dict:
	actor = actor or frappe.session.user
	if actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập.")
	from crm_cskh.p1_permissions import ROLE_BOD

	if ROLE_BOD in _roles(actor) and not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
		raise ForbiddenScope(
			"BOD Viewer chỉ truy cập chỉ số SLA tổng hợp, không đọc danh sách nghĩa vụ chi tiết."
		)

	page_num, size_num, offset = parse_pagination(
		page=page, page_size=page_size, default_page_size=20, max_page_size=100
	)
	wi_filter = parse_optional_str(work_item, "Tham số work_item không hợp lệ.")
	st_filter = parse_optional_str(status, "Tham số status không hợp lệ.")
	tp_filter = parse_optional_str(obligation_type, "Tham số obligation_type không hợp lệ.")

	from crm_cskh.p3_permissions import sla_obligation_query_conditions

	conds: list[str] = ["1=1"]
	params: list = []
	scope_sql = sla_obligation_query_conditions(user=actor)
	if scope_sql:
		conds.append(f"({scope_sql})")
	if wi_filter:
		conds.append("`tabCSKH SLA Obligation`.`work_item` = %s")
		params.append(wi_filter)
	if st_filter:
		conds.append("`tabCSKH SLA Obligation`.`status` = %s")
		params.append(st_filter)
	if tp_filter:
		conds.append("`tabCSKH SLA Obligation`.`obligation_type` = %s")
		params.append(tp_filter)

	where_clause = " and ".join(conds)
	total = int(
		frappe.db.sql(
			f"select count(*) from `tabCSKH SLA Obligation` where {where_clause}",
			tuple(params),
		)[0][0]
		or 0
	)
	rows = frappe.db.sql(
		f"""
		select *
		from `tabCSKH SLA Obligation`
		where {where_clause}
		order by effective_due_at asc, creation desc
		limit %s offset %s
		""",
		tuple([*params, size_num, offset]),
		as_dict=True,
	)
	include_audit = _is_system(actor) or ROLE_ADMIN in _roles(actor) or _is_leader(actor)
	return {
		"ok": True,
		"rows": [serialize_obligation(r, include_audit=include_audit) for r in rows],
		"pagination": {
			"page": page_num,
			"page_size": size_num,
			"total": total,
			"total_pages": (total + size_num - 1) // size_num if total else 0,
		},
	}


def get_obligation_detail(obligation_id: str, actor: str | None = None) -> dict:
	actor = actor or frappe.session.user
	if actor == "Guest":
		raise ForbiddenScope("Yêu cầu đăng nhập.")
	ob_id = parse_optional_str(obligation_id, "Mã nghĩa vụ SLA không hợp lệ.")
	if not ob_id:
		raise InvalidRequest("Mã nghĩa vụ SLA (obligation_id) là bắt buộc.")
	if not frappe.db.exists("CSKH SLA Obligation", ob_id):
		if _is_system(actor) or ROLE_ADMIN in _roles(actor):
			raise NotFound("Không tìm thấy nghĩa vụ SLA.")
		raise ForbiddenScope("Bạn không có quyền truy cập nghĩa vụ SLA này.")
	doc = frappe.get_doc("CSKH SLA Obligation", ob_id)
	if not sla_obligation_has_permission(doc, ptype="read", user=actor):
		raise ForbiddenScope("Bạn không có quyền truy cập nghĩa vụ SLA ngoài phạm vi được phép.")
	include_audit = _is_system(actor) or ROLE_ADMIN in _roles(actor) or _is_leader(actor)
	return {"ok": True, "obligation": serialize_obligation(doc.as_dict(), include_audit=include_audit)}
