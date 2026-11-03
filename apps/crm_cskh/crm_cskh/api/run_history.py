"""P1 API: the distribution runs of a campaign (P1 §9.3).

P1 §9.3 asks for a **batch history** of the campaigns, a way to **look up a
run by its id** and to **retry** a failed job. The frozen contract of T1B
already offers `crm_cskh.api.distribution.get_run(run_id)`; this module adds
the **list** the page needs to show the history of a campaign.

**The scope is the scope of the dashboard** (ADR-P1-07): the whole
installation for the CRM Admin and the BOD viewer, the campaigns of the team
for a leader. A staff member is not a reader of a distribution run and gets
`FORBIDDEN`, exactly like `get_run` does.

The counters come from the **run row** (`actual_json`), never from a sum of
the events, so a run is never counted twice (P1 §12.5).
"""

import json

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import reporting
from crm_cskh.services.errors import Forbidden, InvalidRequest

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


@frappe.whitelist(methods=["GET"])
def runs(page=1, page_size=20, campaign=None, status=None):
	"""The distribution runs of the caller's scope, newest first."""
	actor = frappe.session.user
	_require_read(actor)
	limit = max(1, min(int(page_size or 20), 100))
	offset = max(0, (int(page or 1) - 1) * limit)
	if campaign and not isinstance(campaign, str):
		raise InvalidRequest("The campaign must be a string.")

	scope = reporting._scope(actor)
	nodes = reporting._scope_team_nodes(scope)

	clauses = []
	params = []
	if campaign:
		clauses.append("r.campaign = %s")
		params.append(campaign)
	if status:
		clauses.append("r.status = %s")
		params.append(status)
	if nodes is not None:
		if not nodes:
			return {
				"items": [],
				"total": 0,
				"page": int(page or 1),
				"page_size": limit,
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
		"r.selection_fingerprint as selection_fingerprint, "
		"r.creation as created_at, r.started_at as started_at, r.finished_at as finished_at, "
		"r.actual_json as actual_json, r.error as error, c.title as campaign_title, "
		"c.code as campaign_code "
		"from `tabCSKH Distribution Run` r "
		"left join `tabCSKH Campaign` c on c.name = r.campaign"
		f"{where} order by r.creation desc limit %s offset %s",
		(*tuple(params), limit, offset),
		as_dict=True,
	)

	items = []
	for row in rows:
		actual = _parse_actual(row)
		items.append(
			{
				"run_id": row.run_id,
				"campaign": row.campaign,
				"campaign_title": row.campaign_title,
				"campaign_code": row.campaign_code,
				"status": row.status,
				"rule_version": row.rule_version,
				"idempotency_key": row.idempotency_key,
				"selection_fingerprint": row.selection_fingerprint,
				"created": actual.get("created", 0),
				"errors": actual.get("errors", 0),
				"assigned": actual.get("assigned", {}),
				"skipped": actual.get("skipped", []),
				"created_at": row.created_at,
				"started_at": row.started_at,
				"finished_at": row.finished_at,
				"error": row.error,
			}
		)

	return {
		"items": items,
		"total": total,
		"page": int(page or 1),
		"page_size": limit,
		# Whether the caller may open the **frozen** lookup of a single run
		# (`crm_cskh.api.distribution.get_run`): the page only offers the
		# lookup to a caller the contract grants it to.
		"can_lookup": _is_system(actor) or bool(_roles(actor) & LOOKUP_ROLES),
		# Whether the caller may **distribute** (the grant of the frozen
		# preview / execute): the page only offers the retry of a failed run
		# to a caller that may run a new distribution.
		"can_distribute": _is_system(actor) or bool(_roles(actor) & DISTRIBUTE_ROLES),
		"as_of": now_datetime(),
	}
