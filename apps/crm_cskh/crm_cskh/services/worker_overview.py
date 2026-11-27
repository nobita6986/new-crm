"""Aggregate-only current marketing care workload, with server-derived scope.

Read-model counters include approved corrections. These are contact workload
counts, never hiring or retention counts. No page-sized lists calculate totals.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, now_datetime

from crm_cskh.p2_permissions import (
	assert_workflow_actor,
	leader_authorized_work_item_sql,
	staff_authorized_work_item_sql,
)
from crm_cskh.services import reporting, runtime_cache
from crm_cskh.services.p3_flags import is_p3_sla_enabled


def day_window(now, site_timezone, effective_timezone):
	"""User calendar day as half-open site-local DB bounds, including DST."""
	site = ZoneInfo(site_timezone)
	effective = ZoneInfo(effective_timezone)
	aware = get_datetime(now)
	if aware.tzinfo is None:
		aware = aware.replace(tzinfo=site)
	local = aware.astimezone(effective)
	start = datetime.combine(local.date(), time.min, effective)
	end = datetime.combine(local.date() + timedelta(days=1), time.min, effective)
	return start.astimezone(site).replace(tzinfo=None), end.astimezone(site).replace(tzinfo=None), local


def _timezones(actor):
	site = runtime_cache.get_cached_system_timezone() or "Asia/Ho_Chi_Minh"
	user = runtime_cache.get_user_auth_profile(actor).get("time_zone") or site
	try:
		ZoneInfo(site)
	except (ValueError, KeyError):
		site = "Asia/Ho_Chi_Minh"
	try:
		ZoneInfo(user)
	except (ValueError, KeyError):
		user = site
	return site, user


def _readable_clause(scope, actor):
	if scope["scope"] in ("system", "all"):
		return "1=1"
	if scope["scope"] == "team":
		return leader_authorized_work_item_sql(actor, "wi")
	return staff_authorized_work_item_sql(actor, "wi")


def get_overview(actor=None):
	actor = assert_workflow_actor(actor, allow_bod=True)
	scope = reporting._scope(actor)
	scope_name = "all" if scope["scope"] in ("system", "all") else scope["scope"]
	now = now_datetime()
	site_tz, user_tz = _timezones(actor)
	day_start, day_end, local_now = day_window(now, site_tz, user_tz)
	owner_sql, owner_params = reporting._user_filter(scope, "wi.assigned_to")
	active_where = "wi.status in ('assigned', 'in_progress', 'on_hold')"
	if owner_sql:
		active_where += " and " + owner_sql
	params = tuple(owner_params)
	row = frappe.db.sql(
		f"""select count(*) as active, count(distinct wi.lead) as contact_records,
		sum(case when coalesce(wi.attempt_count, 0) = 0 then 1 else 0 end) as unattempted,
		sum(case when coalesce(wi.attempt_count, 0) > 0 then 1 else 0 end) as attempted,
		sum(case when coalesce(wi.connected_attempt_count, 0) > 0 then 1 else 0 end) as connected,
		sum(case when wi.status = 'on_hold' then 1 else 0 end) as on_hold,
		count(distinct case when coalesce(wi.assigned_to, '') != '' then wi.assigned_to end) as active_assignees,
		count(distinct case when coalesce(wi.attempt_count, 0) = 0 and coalesce(wi.assigned_to, '') != '' then wi.assigned_to end) as assignees_with_unattempted
		from `tabCSKH Work Item` wi where {active_where}""",
		params,
		as_dict=True,
	)[0]
	workload = {
		key: int(row.get(key) or 0)
		for key in ("active", "contact_records", "unattempted", "attempted", "connected")
	}
	workload["attempted_unconnected"] = max(workload["attempted"] - workload["connected"], 0)
	followups = frappe.db.sql(
		f"""select count(*) as open,
		sum(case when t.due_date < %s then 1 else 0 end) as overdue,
		sum(case when t.due_date >= %s and t.due_date < %s then 1 else 0 end) as today,
		count(distinct case when t.due_date < %s and coalesce(wi.assigned_to, '') != '' then wi.assigned_to end) as assignees_with_overdue
		from `tabCRM Task` t inner join `tabCSKH Work Item` wi
		on wi.name = t.cskh_work_item and wi.open_callback_task = t.name
		where coalesce(t.cskh_is_followup, 0) = 1
		and t.status in ('Backlog', 'Todo', 'In Progress') and {active_where}""",
		(now, day_start, day_end, now, *params),
		as_dict=True,
	)[0]
	callbacks = {key: int(followups.get(key) or 0) for key in ("open", "overdue", "today")}
	bottlenecks = {
		"on_hold": int(row.get("on_hold") or 0),
		"active_assignees": int(row.get("active_assignees") or 0),
		"assignees_with_unattempted": int(row.get("assignees_with_unattempted") or 0),
		"assignees_with_overdue": int(followups.get("assignees_with_overdue") or 0),
	}
	dnc = None
	if runtime_cache.has_column("CRM Lead", "cskh_do_not_contact"):
		if scope_name == "all":
			dnc = int(frappe.db.count("CRM Lead", {"cskh_do_not_contact": 1}))
		else:
			dnc = int(
				frappe.db.sql(
					f"""select count(distinct wi.lead) from `tabCSKH Work Item` wi
				inner join `tabCRM Lead` l on l.name = wi.lead
				where coalesce(l.cskh_do_not_contact, 0) = 1
				and {_readable_clause(scope, actor)}"""
				)[0][0]
				or 0
			)
	# Operational obligations, not a historical compliance score.
	sla = None
	if is_p3_sla_enabled() and runtime_cache.table_exists("CSKH SLA Obligation"):
		sla_row = frappe.db.sql(
			f"""select count(*) as pending,
			sum(case when o.effective_due_at < %s then 1 else 0 end) as overdue
			from `tabCSKH SLA Obligation` o inner join `tabCSKH Work Item` wi
			on wi.name = o.work_item and o.owner_user = wi.assigned_to
			where o.status in ('open', 'breached_open') and coalesce(o.is_paused, 0) = 0
			and coalesce(o.informational_only, 0) = 0 and {active_where}""",
			(now, *params),
			as_dict=True,
		)[0]
		sla = {key: int(sla_row.get(key) or 0) for key in ("pending", "overdue")}
	return {
		"scope": scope_name,
		"as_of": local_now.isoformat(timespec="seconds"),
		"timezone": {"site": site_tz, "effective": user_tz},
		"day": {
			"date": local_now.date().isoformat(),
			"from": day_start.isoformat(),
			"to_exclusive": day_end.isoformat(),
		},
		"workload": workload,
		"callbacks": callbacks,
		"bottlenecks": bottlenecks,
		"do_not_contact": dnc,
		"sla": sla,
		"definitions": {
			"workload_unit": "current_active_work_items",
			"contact_records_unit": "distinct_active_worker_leads",
			"coverage_denominator": workload["active"],
			"coverage_history": "all_effective_attempts_on_current_work_items",
			"callbacks_today_includes_overdue_today": True,
			"dnc_unit": "distinct_readable_contact_records",
			"sla_unit": "unpaused_noninformational_current_owner_obligations",
		},
	}
