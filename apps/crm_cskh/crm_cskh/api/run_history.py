"""P1 API: the distribution runs of a campaign (P1 §9.3).

P1 §9.3 asks for a **batch history** of the campaigns, a way to **look up a
run by its id** and to **retry** a failed job. The frozen contract of T1B
already offers `crm_cskh.api.distribution.get_run(run_id)`; this module adds
the **list** the page needs to show the history of a campaign.

**The scope is the scope of the dashboard** (ADR-P1-07): the whole
installation for the CRM Admin and the BOD viewer, the campaigns of the team
for a leader. A staff member is not a reader of a distribution run and gets
`FORBIDDEN`, exactly like `get_run` does.

For a team leader (`scope == "team"`), runs on a shared campaign
(`team_scope IS NULL`) as well as team campaigns strictly scope `assigned`,
`skipped`, `preview`, `progress`, `total`, `created`, and `errors` (including
`run_id` polling responses) to the leader's own team (`L-24`).

The counters come from the **run row** (`actual_json`), never from a sum of
the events, so a run is never counted twice (P1 §12.5).
"""

import json

import frappe
from frappe.utils import now_datetime

from crm_cskh.api._ui_params import parse_optional_str, parse_pagination
from crm_cskh.api.pool_ui import filter_team_authorized_leads
from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden

READ_ROLES = {"CSKH CRM Admin", "CSKH BOD Viewer", "Sales Manager"}

# The role grant of the **frozen** lookup `crm_cskh.api.distribution.get_run`:
# the CRM Admin, the BOD viewer and the system. A leader may read the
# *history* of the runs of their team, but the lookup of a single run is
# not granted to them by the contract, so the page must not offer it.
LOOKUP_ROLES = {"CSKH CRM Admin", "CSKH BOD Viewer"}

# The role grant of the frozen **preview** / **execute**: only the CRM Admin
# may distribute. The page uses this flag to decide whether it offers the
# **retry** of a failed run (P1 §9.3): the retry runs a new preview and a new
# execute, so it needs the same grant as the distribution itself.
DISTRIBUTE_ROLES = {"CSKH CRM Admin"}


def _roles(user=None):
	return set(frappe.get_roles(user or frappe.session.user))


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_read(user=None):
	"""The same role grant as `crm_cskh.api.distribution.get_run`."""
	user = user or frappe.session.user
	if _is_system(user):
		return
	if _roles(user) & READ_ROLES:
		return
	raise Forbidden("You may not read a distribution run.")


def _parse_actual(run):
	"""Return the counters of a run, or an empty dict when it never ran."""
	if not run.actual_json:
		return {}
	try:
		actual = json.loads(run.actual_json)
	except (ValueError, TypeError):
		return {}
	return actual if isinstance(actual, dict) else {}


def _parse_preview(run):
	"""Return the preview summary of a run, or an empty dict."""
	if not getattr(run, "preview_json", None):
		return {}
	try:
		preview = json.loads(run.preview_json)
	except (ValueError, TypeError):
		return {}
	return preview if isinstance(preview, dict) else {}


def _db_team_assigned_by_run(run_ids, team_users):
	"""Fallback DB counts per `(run_id, assigned_to)` within `team_users`."""
	if not run_ids or not team_users:
		return {}
	run_ph = ",".join(["%s"] * len(run_ids))
	user_ph = ",".join(["%s"] * len(team_users))
	rows = frappe.db.sql(
		f"select allocation_run as run_id, assigned_to as user, count(*) as total "
		f"from `tabCSKH Work Item` "
		f"where allocation_run in ({run_ph}) and status = 'assigned' "
		f"and assigned_to in ({user_ph}) "
		f"group by allocation_run, assigned_to",
		(*run_ids, *team_users),
		as_dict=True,
	)
	out = {}
	for row in rows:
		if row.run_id and row.user:
			out.setdefault(row.run_id, {})[row.user] = int(row.total or 0)
	return out


def _serialize_full_run_item(row, actual, preview):
	raw_assigned = actual.get("assigned") if isinstance(actual.get("assigned"), dict) else {}
	raw_skipped = actual.get("skipped") if isinstance(actual.get("skipped"), list) else []
	raw_targets = preview.get("targets") if isinstance(preview.get("targets"), list) else []
	raw_simulated = preview.get("simulated") if isinstance(preview.get("simulated"), dict) else {}
	raw_excluded = (
		preview.get("excluded_assignees") if isinstance(preview.get("excluded_assignees"), list) else []
	)
	eligible_count = int(preview.get("eligible_count", 0) or 0)
	excluded_count = int(preview.get("excluded_count", 0) or 0)

	return {
		"run_id": row.run_id,
		"campaign": row.campaign,
		"campaign_title": row.campaign_title,
		"campaign_code": row.campaign_code,
		"status": row.status,
		"rule_version": row.rule_version,
		"idempotency_key": row.idempotency_key,
		"selection_fingerprint": row.selection_fingerprint,
		"progress": int(row.progress or 0),
		"total": eligible_count,
		"created": int(actual.get("created", 0) or 0),
		"errors": int(actual.get("errors", 0) or 0),
		"assigned": {u: int(cnt or 0) for u, cnt in raw_assigned.items()},
		"skipped": [
			{"lead": entry.get("lead"), "reason": entry.get("reason")}
			for entry in raw_skipped
			if isinstance(entry, dict) and entry.get("lead")
		],
		"preview": {
			"eligible_count": eligible_count,
			"excluded_count": excluded_count,
			"targets": [dict(t) for t in raw_targets if isinstance(t, dict)],
			"simulated": {u: int(v or 0) for u, v in raw_simulated.items()},
			"excluded_assignees": [dict(e) for e in raw_excluded if isinstance(e, dict)],
		},
		"created_at": row.created_at,
		"started_at": row.started_at,
		"finished_at": row.finished_at,
		"error": row.error,
	}


def _serialize_team_run_item(
	row,
	actual,
	preview,
	team_users_set,
	team_nodes_set,
	authorized_skipped_leads,
	db_assigned_by_run,
):
	raw_assigned = actual.get("assigned") if isinstance(actual.get("assigned"), dict) else {}
	scoped_assigned = {u: int(cnt or 0) for u, cnt in raw_assigned.items() if u in team_users_set}
	if not scoped_assigned and not raw_assigned and row.run_id in db_assigned_by_run:
		scoped_assigned = dict(db_assigned_by_run[row.run_id])

	raw_skipped = actual.get("skipped") if isinstance(actual.get("skipped"), list) else []
	scoped_skipped = [
		{"lead": entry.get("lead"), "reason": entry.get("reason")}
		for entry in raw_skipped
		if isinstance(entry, dict) and entry.get("lead") in authorized_skipped_leads
	]

	raw_targets = preview.get("targets") if isinstance(preview.get("targets"), list) else []
	raw_simulated = preview.get("simulated") if isinstance(preview.get("simulated"), dict) else {}
	raw_excluded = (
		preview.get("excluded_assignees") if isinstance(preview.get("excluded_assignees"), list) else []
	)

	scoped_targets = [
		{k: v for k, v in t.items() if k in ("user", "weight", "planned")}
		for t in raw_targets
		if isinstance(t, dict) and t.get("user") in team_users_set
	]
	scoped_simulated = {u: int(v or 0) for u, v in raw_simulated.items() if u in team_users_set}
	scoped_excluded = [
		dict(e) for e in raw_excluded if isinstance(e, dict) and e.get("user") in team_users_set
	]

	is_private_team_campaign = bool(row.campaign_team_scope and row.campaign_team_scope in team_nodes_set)
	if raw_simulated:
		scoped_planned = sum(scoped_simulated.values())
	elif raw_targets:
		scoped_planned = sum(int(t.get("planned") or 0) for t in scoped_targets)
	elif is_private_team_campaign:
		scoped_planned = int(preview.get("eligible_count", 0) or 0)
	else:
		scoped_planned = 0

	scoped_preview = {
		"eligible_count": scoped_planned,
		"excluded_count": (int(preview.get("excluded_count", 0) or 0) if is_private_team_campaign else 0),
		"targets": scoped_targets,
		"simulated": scoped_simulated,
		"excluded_assignees": scoped_excluded,
	}

	created = sum(scoped_assigned.values())
	errors = len(scoped_skipped)
	processed = created + errors
	norm_status = str(row.status or "").strip().lower()

	if is_private_team_campaign and not raw_simulated and not raw_targets and not actual:
		total_val = scoped_planned
		raw_prog = int(row.progress or 0)
		progress_val = min(raw_prog, total_val) if total_val > 0 else raw_prog
	elif norm_status == "completed":
		total_val = processed
		progress_val = processed
	elif norm_status == "completed_with_errors":
		total_val = max(scoped_planned, processed)
		progress_val = processed
	else:
		total_val = max(scoped_planned, processed)
		progress_val = min(processed, total_val) if total_val > 0 else processed

	return {
		"run_id": row.run_id,
		"campaign": row.campaign,
		"campaign_title": row.campaign_title,
		"campaign_code": row.campaign_code,
		"status": row.status,
		"rule_version": row.rule_version,
		"idempotency_key": row.idempotency_key,
		"selection_fingerprint": row.selection_fingerprint,
		"progress": progress_val,
		"total": total_val,
		"created": created,
		"errors": errors,
		"assigned": scoped_assigned,
		"skipped": scoped_skipped,
		"preview": scoped_preview,
		"created_at": row.created_at,
		"started_at": row.started_at,
		"finished_at": row.finished_at,
		"error": row.error if (is_private_team_campaign or norm_status == "failed") else None,
	}


@frappe.whitelist(methods=["GET"])
def runs(page=1, page_size=20, campaign=None, status=None, run_id=None):
	"""The distribution runs of the caller's scope, newest first."""
	actor = frappe.session.user
	_require_read(actor)

	page_num, limit, offset = parse_pagination(page, page_size)
	campaign_val = parse_optional_str(campaign, "The campaign must be a string.")
	run_id_val = parse_optional_str(run_id, "The run id must be a string.")
	status_val = parse_optional_str(status)

	can_lookup = _is_system(actor) or bool(_roles(actor) & LOOKUP_ROLES)
	can_distribute = _is_system(actor) or bool(_roles(actor) & DISTRIBUTE_ROLES)

	scope = reporting._scope(actor)
	nodes = reporting._scope_team_nodes(scope)

	clauses = []
	params = []
	if run_id_val:
		clauses.append("r.name = %s")
		params.append(run_id_val)
	if campaign_val:
		clauses.append("r.campaign = %s")
		params.append(campaign_val)
	if status_val:
		clauses.append("r.status = %s")
		params.append(status_val)
	if nodes is not None:
		if not nodes:
			return {
				"items": [],
				"total": 0,
				"page": page_num,
				"page_size": limit,
				"can_lookup": can_lookup,
				"can_distribute": can_distribute,
				"as_of": now_datetime(),
			}
		placeholders = ",".join(["%s"] * len(nodes))
		clauses.append(
			"(r.campaign in (select cskh_scope_campaign.name from `tabCSKH Campaign` cskh_scope_campaign "
			"where cskh_scope_campaign.team_scope is null "
			f"or cskh_scope_campaign.team_scope in ({placeholders})))"
		)
		params.extend(nodes)

	where = (" where " + " and ".join(clauses)) if clauses else ""
	total = frappe.db.sql(
		f"select count(*) from `tabCSKH Distribution Run` r{where}",
		tuple(params),
	)[0][0]
	rows = frappe.db.sql(
		"select r.name as run_id, r.campaign as campaign, r.status as status, "
		"r.rule_version as rule_version, r.idempotency_key as idempotency_key, "
		"r.selection_fingerprint as selection_fingerprint, r.progress as progress, "
		"r.creation as created_at, r.started_at as started_at, r.finished_at as finished_at, "
		"r.preview_json as preview_json, r.actual_json as actual_json, r.error as error, "
		"c.title as campaign_title, c.code as campaign_code, c.team_scope as campaign_team_scope "
		"from `tabCSKH Distribution Run` r "
		"left join `tabCSKH Campaign` c on c.name = r.campaign"
		f"{where} order by r.creation desc limit %s offset %s",
		(*tuple(params), limit, offset),
		as_dict=True,
	)

	parsed_rows = [(row, _parse_actual(row), _parse_preview(row)) for row in rows]

	items = []
	if scope["scope"] == "team":
		team_users = reporting._team_users(scope["user"])
		team_users_set = set(team_users)
		team_nodes_set = set(nodes or [])

		candidate_skipped_leads = set()
		fallback_run_ids = []
		for row, actual, _preview in parsed_rows:
			raw_skipped = actual.get("skipped") if isinstance(actual.get("skipped"), list) else []
			for entry in raw_skipped:
				if isinstance(entry, dict) and entry.get("lead"):
					candidate_skipped_leads.add(entry["lead"])
			raw_assigned = actual.get("assigned") if isinstance(actual.get("assigned"), dict) else {}
			if not raw_assigned and str(row.status or "").strip().lower() != "planned":
				fallback_run_ids.append(row.run_id)

		authorized_skipped_leads = filter_team_authorized_leads(scope, candidate_skipped_leads)
		db_assigned_by_run = _db_team_assigned_by_run(fallback_run_ids, team_users)

		for row, actual, preview in parsed_rows:
			items.append(
				_serialize_team_run_item(
					row,
					actual,
					preview,
					team_users_set,
					team_nodes_set,
					authorized_skipped_leads,
					db_assigned_by_run,
				)
			)
	else:
		for row, actual, preview in parsed_rows:
			items.append(_serialize_full_run_item(row, actual, preview))

	return {
		"items": items,
		"total": total,
		"page": page_num,
		"page_size": limit,
		# Whether the caller may open the **frozen** lookup of a single run
		# (`crm_cskh.api.distribution.get_run`): the page only offers the
		# lookup to a caller the contract grants it to.
		"can_lookup": can_lookup,
		# Whether the caller may **distribute** (the grant of the frozen
		# preview / execute): the page only offers the retry of a failed run
		# to a caller that may run a new distribution.
		"can_distribute": can_distribute,
		"as_of": now_datetime(),
	}
