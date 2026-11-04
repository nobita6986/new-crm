"""Authoritative `team-as-of` historical attribution service (`P3-01`, `ADR-P3-05`).

Invariants (`docs/p3/ownership-attribution.md`):
  1. NEVER back-calculate historical team or owner from current `CRM Sales Hierarchy` or `CRM Lead.lead_owner`.
  2. Every hierarchy insert/update/delete records an append-only interval in `CSKH Team Membership History`.
  3. On upgrade to P3, current hierarchy nodes receive `source_type = 'migration_cutover'` starting at
     `migration_executed_at` (NEVER backdated to `1970-01-01`).
  4. Historical events predating any authoritative membership interval for `(user, at_dt)` return
     `(None, 'historical_attribution_unknown')` so team-level KPIs exclude them honestly with explicit counts.
"""

from __future__ import annotations

import contextvars
from datetime import datetime

import frappe
from frappe.utils import get_datetime, now_datetime

AUTHORITATIVE = "authoritative"
UNKNOWN_HISTORICAL = "historical_attribution_unknown"

_AUTHORIZED_MEMBERSHIP_WRITE: contextvars.ContextVar[bool] = contextvars.ContextVar(
	"_AUTHORIZED_MEMBERSHIP_WRITE",
	default=False,
)


class _AuthorizedMembershipContext:
	def __enter__(self):
		self._token = _AUTHORIZED_MEMBERSHIP_WRITE.set(True)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		_AUTHORIZED_MEMBERSHIP_WRITE.reset(self._token)
		return False


def authorized_membership_write() -> _AuthorizedMembershipContext:
	return _AuthorizedMembershipContext()


def is_authorized_membership_write() -> bool:
	return bool(_AUTHORIZED_MEMBERSHIP_WRITE.get())


def resolve_hierarchy_team_node(user_node_row: dict | None) -> tuple[str | None, str | None]:
	"""Determine the governing Team node `(team_node, team_name)` for a `CRM Sales Hierarchy` row.

	- If the user's node itself is a group/leader node (`is_group = 1` or has children or no `reports_to`),
	  its own node `name` represents that Leader's team unit unless it reports to an intermediate team node
	  while being a leaf staff member.
	- Specifically: a leaf member (`is_group = 0` with `reports_to` set) belongs to `reports_to` (their
	  Leader's team node); a group node (`is_group = 1`, e.g., `Team1 Leader`) represents `name` (`Team1 Leader`).
	"""
	if not user_node_row:
		return None, None
	node_name = user_node_row.get("name")
	reports_to = user_node_row.get("reports_to")
	is_group = int(user_node_row.get("is_group") or 0)
	if is_group or not reports_to:
		return node_name, user_node_row.get("full_name") or node_name
	parent_name = frappe.db.get_value("CRM Sales Hierarchy", reports_to, "full_name") or reports_to
	return reports_to, parent_name


def record_membership_interval(
	*,
	user: str,
	hierarchy_node: str | None,
	team_node: str | None,
	team_name_snapshot: str | None = None,
	effective_from: datetime | str | None = None,
	source_type: str = "hierarchy_hook",
	recorded_by: str | None = None,
) -> str | None:
	"""Close any open membership interval of `user` (if team changed) and open a new authoritative interval."""
	if not user or not frappe.db.table_exists("CSKH Team Membership History"):
		return None
	eff_dt = get_datetime(effective_from or now_datetime())
	actor = recorded_by or getattr(frappe.session, "user", None) or "Administrator"

	open_rows = frappe.db.sql(
		"""
		select name, hierarchy_node, team_node, effective_from, source_type
		from `tabCSKH Team Membership History`
		where user = %s and effective_to is null
		order by effective_from desc, creation desc
		for update
		""",
		(user,),
		as_dict=True,
	)
	if open_rows:
		latest = open_rows[0]
		if latest.team_node == team_node and latest.hierarchy_node == hierarchy_node:
			return latest.name
		for r in open_rows:
			close_at = eff_dt if eff_dt >= get_datetime(r.effective_from) else get_datetime(r.effective_from)
			frappe.db.set_value(
				"CSKH Team Membership History",
				r.name,
				"effective_to",
				close_at,
				update_modified=False,
			)

	if not team_node:
		return None

	if not team_name_snapshot:
		team_name_snapshot = frappe.db.get_value("CRM Sales Hierarchy", team_node, "full_name") or team_node

	event_key = f"{user}:{team_node}:{eff_dt.isoformat()}:{frappe.generate_hash(length=8)}"
	with authorized_membership_write():
		doc = frappe.get_doc(
			{
				"doctype": "CSKH Team Membership History",
				"user": user,
				"hierarchy_node": hierarchy_node,
				"team_node": team_node,
				"team_name_snapshot": team_name_snapshot,
				"effective_from": eff_dt,
				"effective_to": None,
				"source_type": source_type,
				"recorded_by": actor,
				"event_key": event_key,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name


def close_open_membership_for_user(user: str, ended_at: datetime | str | None = None) -> int:
	if not user or not frappe.db.table_exists("CSKH Team Membership History"):
		return 0
	eff_end = get_datetime(ended_at or now_datetime())
	open_rows = frappe.db.get_all(
		"CSKH Team Membership History",
		filters={"user": user, "effective_to": ["is", "not set"]},
		fields=["name", "effective_from"],
	)
	closed = 0
	for r in open_rows:
		close_at = eff_end if eff_end >= get_datetime(r.effective_from) else get_datetime(r.effective_from)
		frappe.db.set_value(
			"CSKH Team Membership History",
			r.name,
			"effective_to",
			close_at,
			update_modified=False,
		)
		closed += 1
	return closed


def on_sales_hierarchy_change(doc, method=None) -> None:
	"""`doc_events` hook on `CRM Sales Hierarchy` (`after_insert`, `on_update`)."""
	if not frappe.db.table_exists("CSKH Team Membership History"):
		return
	now_dt = now_datetime()
	# If `user` changed on an existing node, close the previous user's interval first.
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	if before and getattr(before, "user", None) and before.user != getattr(doc, "user", None):
		close_open_membership_for_user(before.user, ended_at=now_dt)

	if getattr(doc, "user", None):
		row_dict = {
			"name": doc.name,
			"user": doc.user,
			"reports_to": getattr(doc, "reports_to", None),
			"is_group": getattr(doc, "is_group", 0),
			"full_name": getattr(doc, "full_name", None),
		}
		team_node, team_name = resolve_hierarchy_team_node(row_dict)
		record_membership_interval(
			user=doc.user,
			hierarchy_node=doc.name,
			team_node=team_node,
			team_name_snapshot=team_name,
			effective_from=now_dt,
			source_type="hierarchy_hook",
		)

	# If this node's `is_group` or `full_name` changed and direct leaf children report to it, ensure their
	# intervals exist if they had none yet.
	children = frappe.db.get_all(
		"CRM Sales Hierarchy",
		filters={"reports_to": doc.name},
		fields=["name", "user", "reports_to", "is_group", "full_name"],
	)
	for child in children:
		if child.user and not int(child.is_group or 0):
			has_open = frappe.db.exists(
				"CSKH Team Membership History",
				{"user": child.user, "effective_to": ["is", "not set"]},
			)
			if not has_open:
				team_node, team_name = resolve_hierarchy_team_node(child)
				record_membership_interval(
					user=child.user,
					hierarchy_node=child.name,
					team_node=team_node,
					team_name_snapshot=team_name,
					effective_from=now_dt,
					source_type="hierarchy_hook",
				)


def on_sales_hierarchy_trash(doc, method=None) -> None:
	"""`doc_events` hook on `CRM Sales Hierarchy` (`on_trash`)."""
	if getattr(doc, "user", None):
		close_open_membership_for_user(doc.user, ended_at=now_datetime())


def resolve_team_for_user_at(
	user: str | None,
	at_dt: datetime | str | None,
	*,
	allow_live_capture_for_current_tx: bool = False,
) -> tuple[str | None, str]:
	"""Return `(team_node, attribution_status)` for `user` at `at_dt`.

	- Searches `CSKH Team Membership History` intervals where `effective_from <= at_dt` and
	  `(effective_to IS NULL OR at_dt < effective_to)`.
	- Only when `allow_live_capture_for_current_tx=True` (i.e. a brand-new live `assign`/`reassign`/`reopen`
	  happening right now at `now_datetime()`, where the hierarchy node exists in DB right now even if
	  seeded via `frappe.db` before a hook fired) may it initialize the live membership interval from the
	  current `CRM Sales Hierarchy` row at `at_dt`.
	- For any historical query (`allow_live_capture_for_current_tx=False`), if no recorded membership
	  interval covers `at_dt`, returns `(None, 'historical_attribution_unknown')` — NEVER guesses from the
	  current hierarchy tree!
	"""
	if not user or not at_dt:
		return None, UNKNOWN_HISTORICAL
	target_dt = get_datetime(at_dt)

	if frappe.db.table_exists("CSKH Team Membership History"):
		rows = frappe.db.sql(
			"""
			select team_node
			from `tabCSKH Team Membership History`
			where user = %s
			  and effective_from <= %s
			  and (effective_to is null or %s < effective_to)
			order by effective_from desc, creation desc
			limit 1
			""",
			(user, target_dt, target_dt),
			as_dict=True,
		)
		if rows and rows[0].team_node:
			return rows[0].team_node, AUTHORITATIVE

	if allow_live_capture_for_current_tx and frappe.db.table_exists("CRM Sales Hierarchy"):
		node_row = frappe.db.get_value(
			"CRM Sales Hierarchy",
			{"user": user},
			["name", "user", "reports_to", "is_group", "full_name"],
			as_dict=True,
		)
		if node_row:
			team_node, team_name = resolve_hierarchy_team_node(node_row)
			if team_node:
				# Only bootstrap live interval when `target_dt` is not backdated prior to an existing later interval
				later_exists = (
					frappe.db.sql(
						"""
						select 1 from `tabCSKH Team Membership History`
						where user = %s and effective_from > %s
						limit 1
						""",
						(user, target_dt),
					)
					if frappe.db.table_exists("CSKH Team Membership History")
					else ()
				)
				if not later_exists:
					record_membership_interval(
						user=user,
						hierarchy_node=node_row.name,
						team_node=team_node,
						team_name_snapshot=team_name,
						effective_from=target_dt,
						source_type="hierarchy_hook",
					)
					return team_node, AUTHORITATIVE

	return None, UNKNOWN_HISTORICAL


def ensure_cutover_membership_baseline(cutover_at: datetime | None = None) -> int:
	"""Create `migration_cutover` intervals at `cutover_at` (`now`) for current hierarchy users lacking an open row.

	Crucially, `effective_from = cutover_at` (`now`), NEVER backdated to the past, so pre-cutover rows
	without authoritative team evidence remain `historical_attribution_unknown`.
	"""
	if not (
		frappe.db.table_exists("CRM Sales Hierarchy")
		and frappe.db.table_exists("CSKH Team Membership History")
	):
		return 0
	cut_dt = get_datetime(cutover_at or now_datetime())
	nodes = frappe.db.get_all(
		"CRM Sales Hierarchy",
		fields=["name", "user", "reports_to", "is_group", "full_name"],
	)
	created = 0
	for n in nodes:
		if not n.user:
			continue
		has_any = frappe.db.exists(
			"CSKH Team Membership History",
			{"user": n.user, "effective_to": ["is", "not set"]},
		)
		if has_any:
			continue
		team_node, team_name = resolve_hierarchy_team_node(n)
		if team_node:
			record_membership_interval(
				user=n.user,
				hierarchy_node=n.name,
				team_node=team_node,
				team_name_snapshot=team_name,
				effective_from=cut_dt,
				source_type="migration_cutover",
				recorded_by="Administrator",
			)
			created += 1
	return created


def backfill_pre_p3_assignment_attribution() -> int:
	"""Mark pre-P3 `CSKH Assignment History` intervals lacking authoritative evidence as `historical_attribution_unknown`."""
	if not (
		frappe.db.table_exists("CSKH Assignment History")
		and frappe.db.has_column("CSKH Assignment History", "attribution_status")
	):
		return 0
	rows = frappe.db.sql(
		"""
		select name, action, to_user, ownership_start, event_at, team_at_assignment, attribution_status
		from `tabCSKH Assignment History`
		where attribution_status is null or attribution_status = ''
		""",
		as_dict=True,
	)
	updated = 0
	for r in rows:
		if r.action in ("assign", "reassign", "reopen") and r.to_user:
			if r.team_at_assignment:
				status = AUTHORITATIVE
				team_val = r.team_at_assignment
			else:
				team_val, status = resolve_team_for_user_at(
					r.to_user,
					r.ownership_start or r.event_at,
					allow_live_capture_for_current_tx=False,
				)
			frappe.db.set_value(
				"CSKH Assignment History",
				r.name,
				{"team_at_assignment": team_val, "attribution_status": status},
				update_modified=False,
			)
			updated += 1
		else:
			frappe.db.set_value(
				"CSKH Assignment History",
				r.name,
				{"attribution_status": AUTHORITATIVE},
				update_modified=False,
			)
			updated += 1
	return updated
