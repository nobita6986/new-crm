"""KPI Run orchestration, append-only snapshot pointer swap, post-commit invalidation & backfill (`P3-05`, `ADR-P3-07`, `ADR-P3-08`).

Guarantees:
  * **Post-Commit Dirty Invalidation (`schedule_post_commit_dirty_invalidation`):**
    Buffers operational mutations in `frappe.local.cskh_pending_dirty_events` and flushes them via
    `frappe.db.after_commit` so ZERO report/dirty-scope SQL ever runs inside `record_attempt`'s transaction.
    Cleared automatically on transaction rollback or savepoint rollback.
  * **Append-Only Revisions (`execute_kpi_recompute`):**
    Never overwrites or deletes historical `CSKH KPI Run` or `CSKH KPI Snapshot` payloads. Writes a new
    `CSKH KPI Run` + bulk-inserted `CSKH KPI Snapshot` rows, verifies reconciliation invariants, and
    atomically swaps `is_latest_published` (`0 -> 1`) inside one DB transaction.
  * **Restated Provenance:**
    When recomputing a prior date/period (or after an approved correction/waiver) that already had a
    published run, marks the new run `run_mode = "restated"`, `is_restated = 1`,
    `restated_from_run = <previous_run_id>` while preserving the superseded run for audit comparison.
  * **Backfill (`run_kpi_backfill`):**
    Supports `dry_run=True` (`0` published pointer/snapshot changes), deterministic batching,
    `checkpoint_json` persistence after each batch, and checkpoint resume.
"""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from crm_cskh.p1_permissions import ROLE_ADMIN, ROLE_BOD, _is_system, _node_in_subtree, _roles
from crm_cskh.p2_permissions import _in_team, _is_leader, _is_staff
from crm_cskh.p3_models import authorized_kpi_write
from crm_cskh.services.calendar import format_calendar_iso
from crm_cskh.services.errors import (
	ForbiddenScope,
	InvalidRequest,
	NotFound,
	RecomputeInProgress,
)
from crm_cskh.services.kpi_calculator import METRIC_VERSION, compute_period_snapshots
from crm_cskh.services.kpi_periods import (
	PeriodSpec,
	_parse_iso_date,
	affected_standard_periods_for_date,
	iter_days,
	resolve_period_spec,
	today_hcm,
)

SNAPSHOT_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"kpi_run",
	"period_type",
	"period_key",
	"from_date",
	"to_date",
	"scope_type",
	"scope_id",
	"scope_label",
	"campaign_filter",
	"team_filter",
	"parent_team_id",
	"parent_team_label",
	"active_staff_count",
	"metric_version",
	"as_of",
	"source_watermark",
	"is_latest_published",
	"process_score_status",
	"process_score_sort",
	"distinct_work_items",
	"ownership_intervals_count",
	"work_items_touched",
	"contact_attempt_count",
	"connected_attempt_count",
	"metrics_json",
	"exclusion_counts_json",
	"trend_series_json",
]

RESTATED_TRIGGER_REASONS = frozenset(
	(
		"prior_period_correction",
		"correction_approved",
		"obligation_waived",
		"historical_backfill_restate",
	)
)


def unpack_snapshot_metrics(raw_metrics_json: str | dict | None) -> tuple[dict, list[dict], dict]:
	"""Unpack `(metrics, score_components, attempt_breakdown)` from `CSKH KPI Snapshot.metrics_json`."""
	if not raw_metrics_json:
		return {}, [], {}
	parsed = json.loads(raw_metrics_json) if isinstance(raw_metrics_json, str) else dict(raw_metrics_json)
	if "metrics" in parsed and isinstance(parsed["metrics"], dict):
		return (
			parsed["metrics"],
			list(parsed.get("score_components") or []),
			dict(parsed.get("attempt_breakdown") or {}),
		)
	return parsed, [], {}


def _clear_pending_dirty_events() -> None:
	local = getattr(frappe, "local", None)
	if local is not None:
		local.cskh_pending_dirty_events = []
		local.cskh_dirty_after_commit_registered = False


def schedule_post_commit_dirty_invalidation(
	*,
	work_item: str | None = None,
	campaign: str | None = None,
	owner_user: str | None = None,
	team_node: str | None = None,
	event_dt: datetime | date | str | None = None,
	reason: str = "operational_mutation",
	trigger_reference: str | None = None,
) -> None:
	"""Buffer a dirty-scope invalidation in memory for post-commit persistence (`0` in-tx SQL!)."""
	local = getattr(frappe, "local", None)
	if local is None:
		return

	if isinstance(event_dt, datetime):
		dirty_d = event_dt.date()
	elif isinstance(event_dt, date):
		dirty_d = event_dt
	elif isinstance(event_dt, str) and event_dt.strip():
		dirty_d = get_datetime(event_dt).date()
	else:
		dirty_d = today_hcm()

	buf = getattr(local, "cskh_pending_dirty_events", None)
	if buf is None:
		buf = []
		local.cskh_pending_dirty_events = buf

	buf.append(
		{
			"dirty_date": dirty_d,
			"campaign": str(campaign or ""),
			"team_node": str(team_node or ""),
			"owner_user": str(owner_user or ""),
			"work_item": str(work_item or ""),
			"reason": str(reason or "operational_mutation"),
			"trigger_reference": str(trigger_reference or ""),
		}
	)

	if not getattr(local, "cskh_dirty_after_commit_registered", False):
		local.cskh_dirty_after_commit_registered = True
		frappe.db.after_commit.add(_flush_pending_dirty_events)
		frappe.db.after_rollback.add(_clear_pending_dirty_events)


def _flush_pending_dirty_events() -> None:
	"""Execute AFTER transaction commit: upsert `CSKH Dirty Scope` rows and optionally enqueue worker."""
	local = getattr(frappe, "local", None)
	if local is None:
		return
	events = list(getattr(local, "cskh_pending_dirty_events", None) or [])
	local.cskh_pending_dirty_events = []
	local.cskh_dirty_after_commit_registered = False

	if not events or not frappe.db.table_exists("CSKH Dirty Scope"):
		return

	now_dt = now_datetime()
	cur_today = today_hcm(now_dt)

	# Coalesce multiple events touching the same `(dirty_date, campaign, team_node, owner_user)`
	by_key: dict[str, dict] = {}
	for ev in events:
		d: date = ev["dirty_date"]
		skey = f"{d.isoformat()}|{ev['campaign'] or '*'}|{ev['team_node'] or '*'}|{ev['owner_user'] or '*'}"
		is_prior = 1 if (d < cur_today or ev["reason"] in RESTATED_TRIGGER_REASONS) else 0
		prev = by_key.get(skey)
		if prev is None:
			by_key[skey] = {**ev, "scope_key": skey, "is_prior_period": is_prior}
		else:
			if is_prior:
				prev["is_prior_period"] = 1
			if ev["reason"] in RESTATED_TRIGGER_REASONS:
				prev["reason"] = ev["reason"]
			if ev["trigger_reference"]:
				prev["trigger_reference"] = ev["trigger_reference"]

	wrote_any = False
	with authorized_kpi_write():
		for skey, item in by_key.items():
			d = item["dirty_date"]
			month_str = f"{d.year:04d}-{d.month:02d}"
			existing_name = frappe.db.get_value("CSKH Dirty Scope", {"scope_key": skey}, "name")
			if existing_name:
				updates = {
					"status": "pending",
					"last_dirtied_at": now_dt,
					"reason": item["reason"],
				}
				if item["is_prior_period"]:
					updates["is_prior_period"] = 1
				if item["trigger_reference"]:
					updates["trigger_reference"] = item["trigger_reference"]
				frappe.db.set_value("CSKH Dirty Scope", existing_name, updates, update_modified=True)
			else:
				doc = frappe.get_doc(
					{
						"doctype": "CSKH Dirty Scope",
						"scope_key": skey,
						"dirty_date": d.isoformat(),
						"period_month": month_str,
						"campaign": item["campaign"] or None,
						"team_node": item["team_node"] or None,
						"owner_user": item["owner_user"] or None,
						"reason": item["reason"],
						"is_prior_period": item["is_prior_period"],
						"trigger_reference": item["trigger_reference"] or item["work_item"] or None,
						"status": "pending",
						"first_dirtied_at": now_dt,
						"last_dirtied_at": now_dt,
					}
				)
				doc.flags.ignore_links = True
				doc.insert(ignore_permissions=True)
			wrote_any = True

	if wrote_any:
		frappe.db.commit()

	# Enqueue deduplicated background worker outside unit tests unless explicitly suppressed
	if (
		wrote_any
		and not getattr(frappe.flags, "in_test", False)
		and not getattr(frappe.flags, "cskh_suppress_dirty_enqueue", False)
	):
		try:
			cache = frappe.cache
			enq_key = cache.make_key("cskh:kpi:dirty_worker_enqueued")
			if cache.set(enq_key, "1", ex=20, nx=True):
				frappe.enqueue(
					"crm_cskh.jobs.recompute_kpi.process_dirty_scopes",
					queue="short",
					now=False,
				)
		except Exception:
			pass


@contextmanager
def acquire_recompute_lock(period: PeriodSpec, campaign_filter: str = "", ttl_sec: int = 300):
	"""Acquire an atomic Redis scope lock or raise `409 RecomputeInProgress`."""
	raw_key = f"cskh:kpi:recompute_lock:{period.period_type}:{period.period_key}:{campaign_filter or '*'}"
	cache = frappe.cache
	rkey = cache.make_key(raw_key)
	token = secrets.token_hex(12)
	acquired = False
	try:
		acquired = bool(cache.set(rkey, token, ex=int(ttl_sec), nx=True))
	except Exception:
		# Fallback if Redis transiently unavailable: check DB running run within ttl_sec
		cutoff = now_datetime() - timedelta(seconds=int(ttl_sec))
		running = frappe.db.exists(
			"CSKH KPI Run",
			{
				"period_type": period.period_type,
				"period_key": period.period_key,
				"campaign_filter": campaign_filter or "",
				"status": "running",
				"started_at": [">=", cutoff],
			},
		)
		if running:
			raise RecomputeInProgress(
				f"Đang có tiến trình tính lại KPI cho kỳ {period.period_type}:{period.period_key}."
			)
		acquired = True

	if not acquired:
		raise RecomputeInProgress(
			f"Đang có tiến trình tính lại KPI giữ khóa trên kỳ {period.period_type}:{period.period_key}.",
			details={"period_type": period.period_type, "period_key": period.period_key},
		)

	try:
		yield token
	finally:
		try:
			cur = cache.get(rkey)
			if cur is not None:
				cur_str = cur.decode("utf-8") if isinstance(cur, bytes) else str(cur)
				if cur_str == token:
					cache.delete(rkey)
		except Exception:
			pass


def execute_kpi_recompute(
	period: PeriodSpec,
	*,
	run_mode: str = "incremental",
	campaign_filter: str = "",
	target_scope_type: str = "all",
	target_scope_id: str = "",
	as_of: datetime | str | None = None,
	trigger_reason: str = "manual_or_scheduled",
	trigger_reference: str = "",
	actor: str | None = None,
	dry_run: bool = False,
) -> dict:
	"""Compute KPI snapshots for `period`, verify reconciliation, and atomically publish new revision."""
	camp_val = str(campaign_filter or "").strip()
	as_of_dt = get_datetime(as_of or now_datetime())
	started_dt = now_datetime()
	req_user = actor or frappe.session.user or "Administrator"

	with acquire_recompute_lock(period, camp_val):
		prev_pub = frappe.db.get_value(
			"CSKH KPI Run",
			{
				"period_type": period.period_type,
				"period_key": period.period_key,
				"campaign_filter": camp_val,
				"is_latest_published": 1,
			},
			["name", "as_of", "source_watermark"],
			order_by="creation desc",
			as_dict=True,
		)

		is_prior_period = period.to_date < today_hcm(as_of_dt)
		if dry_run or run_mode == "dry_run":
			eff_mode = "dry_run"
			is_restated = 0
			restated_from = prev_pub.name if prev_pub else None
			dry_run = True
		elif prev_pub and (
			run_mode == "restated" or is_prior_period or trigger_reason in RESTATED_TRIGGER_REASONS
		):
			eff_mode = "restated"
			is_restated = 1
			restated_from = prev_pub.name
		else:
			eff_mode = (
				run_mode if run_mode in ("full", "incremental", "restated", "backfill") else "incremental"
			)
			is_restated = 1 if (eff_mode == "restated" and prev_pub) else 0
			restated_from = prev_pub.name if is_restated and prev_pub else None

		with authorized_kpi_write():
			run_doc = frappe.get_doc(
				{
					"doctype": "CSKH KPI Run",
					"run_mode": eff_mode,
					"status": "running",
					"period_type": period.period_type,
					"period_key": period.period_key,
					"from_date": period.from_date.isoformat(),
					"to_date": period.to_date.isoformat(),
					"target_scope_type": target_scope_type or "all",
					"target_scope_id": target_scope_id or None,
					"campaign_filter": camp_val,
					"as_of": as_of_dt,
					"metric_version": METRIC_VERSION,
					"is_latest_published": 0,
					"is_restated": is_restated,
					"restated_from_run": restated_from,
					"trigger_reason": trigger_reason,
					"trigger_reference": trigger_reference or None,
					"requested_by": req_user if frappe.db.exists("User", req_user) else "Administrator",
					"started_at": started_dt,
				}
			)
			run_doc.flags.ignore_links = True
			run_doc.insert(ignore_permissions=True)
		frappe.db.commit()
		run_id = str(run_doc.name)

		try:
			computed = compute_period_snapshots(period, as_of=as_of_dt, campaign_filter=camp_val)
			snapshots = computed["snapshots"]
			reconciliation = computed["reconciliation"]
			watermark = computed["source_watermark"]

			if dry_run:
				completed_dt = now_datetime()
				with authorized_kpi_write():
					frappe.db.set_value(
						"CSKH KPI Run",
						run_id,
						{
							"status": "completed",
							"source_watermark": watermark,
							"is_latest_published": 0,
							"snapshots_written": 0,
							"reconciliation_ok": 1,
							"reconciliation_json": json.dumps(reconciliation, ensure_ascii=False),
							"completed_at": completed_dt,
						},
						update_modified=True,
					)
				frappe.db.commit()
				return {
					"run_id": run_id,
					"run_mode": "dry_run",
					"dry_run": True,
					"status": "completed",
					"period": period.to_dict(),
					"as_of": format_calendar_iso(as_of_dt),
					"source_watermark": format_calendar_iso(watermark) if watermark else None,
					"is_restated": False,
					"restated_from_run": restated_from,
					"snapshots_preview_count": len(snapshots),
					"snapshots_written": 0,
					"reconciliation": reconciliation,
				}

			# Bulk insert immutable `CSKH KPI Snapshot` rows (`is_latest_published = 0` until pointer swap)
			now_ts = now_datetime()
			bulk_rows = []
			for snap in snapshots:
				snap_name = frappe.generate_hash(length=12)
				packed_metrics = json.dumps(
					{
						"metrics": snap["metrics"],
						"score_components": snap["score_components"],
						"attempt_breakdown": snap["attempt_breakdown"],
					},
					ensure_ascii=False,
				)
				excl_json = json.dumps(snap["exclusion_counts"], ensure_ascii=False)
				trend_json = json.dumps(snap.get("trend_series") or [], ensure_ascii=False)
				bulk_rows.append(
					(
						snap_name,
						now_ts,
						now_ts,
						"Administrator",
						"Administrator",
						0,
						0,
						run_id,
						period.period_type,
						period.period_key,
						period.from_date.isoformat(),
						period.to_date.isoformat(),
						snap["scope_type"],
						snap["scope_id"],
						snap["scope_label"],
						camp_val,
						"",
						snap.get("parent_team_id") or "",
						snap.get("parent_team_label") or "",
						int(snap.get("active_staff_count") or 0),
						METRIC_VERSION,
						as_of_dt,
						watermark,
						0,
						snap["process_score_status"],
						float(snap["process_score_sort"]),
						int(snap["distinct_work_items"]),
						int(snap["ownership_intervals_count"]),
						int(snap["work_items_touched"]),
						int(snap["contact_attempt_count"]),
						int(snap["connected_attempt_count"]),
						packed_metrics,
						excl_json,
						trend_json,
					)
				)

			if bulk_rows:
				frappe.db.bulk_insert("CSKH KPI Snapshot", fields=SNAPSHOT_BULK_FIELDS, values=bulk_rows)

			# Atomic pointer swap: demote old published pointer -> promote new run & snapshots in 1 commit!
			frappe.db.sql(
				"""
				update `tabCSKH KPI Snapshot`
				set is_latest_published = 0
				where period_type = %s
				  and period_key = %s
				  and coalesce(campaign_filter, '') = %s
				  and is_latest_published = 1
				  and kpi_run != %s
				""",
				(period.period_type, period.period_key, camp_val, run_id),
			)
			frappe.db.sql(
				"""
				update `tabCSKH KPI Snapshot`
				set is_latest_published = 1
				where kpi_run = %s
				""",
				(run_id,),
			)
			frappe.db.sql(
				"""
				update `tabCSKH KPI Run`
				set is_latest_published = 0
				where period_type = %s
				  and period_key = %s
				  and coalesce(campaign_filter, '') = %s
				  and is_latest_published = 1
				  and name != %s
				""",
				(period.period_type, period.period_key, camp_val, run_id),
			)
			completed_dt = now_datetime()
			frappe.db.sql(
				"""
				update `tabCSKH KPI Run`
				set status = 'completed',
				    source_watermark = %s,
				    is_latest_published = 1,
				    snapshots_written = %s,
				    reconciliation_ok = 1,
				    reconciliation_json = %s,
				    completed_at = %s,
				    modified = %s
				where name = %s
				""",
				(
					watermark,
					len(bulk_rows),
					json.dumps(reconciliation, ensure_ascii=False),
					completed_dt,
					completed_dt,
					run_id,
				),
			)
			frappe.db.commit()

			return {
				"run_id": run_id,
				"run_mode": eff_mode,
				"dry_run": False,
				"status": "completed",
				"period": period.to_dict(),
				"as_of": format_calendar_iso(as_of_dt),
				"source_watermark": format_calendar_iso(watermark) if watermark else None,
				"is_restated": bool(is_restated),
				"restated_from_run": restated_from,
				"snapshots_written": len(bulk_rows),
				"reconciliation": reconciliation,
			}
		except Exception as exc:
			frappe.db.rollback()
			err_dt = now_datetime()
			frappe.db.sql(
				"""
				update `tabCSKH KPI Run`
				set status = 'failed',
				    is_latest_published = 0,
				    reconciliation_ok = 0,
				    error_message = %s,
				    completed_at = %s,
				    modified = %s
				where name = %s
				""",
				(str(exc)[:1000], err_dt, err_dt, run_id),
			)
			frappe.db.commit()
			raise


def process_dirty_scopes(max_scopes: int = 100, as_of: datetime | str | None = None) -> dict:
	"""Drain `pending` `CSKH Dirty Scope` rows into deduplicated period recomputations."""
	if not frappe.db.table_exists("CSKH Dirty Scope"):
		return {"processed_scopes": 0, "runs": []}

	pending_rows = frappe.db.sql(
		"""
		select name, dirty_date, campaign, reason, is_prior_period, trigger_reference
		from `tabCSKH Dirty Scope`
		where status = 'pending'
		order by first_dirtied_at asc
		limit %s
		""",
		(int(max_scopes),),
		as_dict=True,
	)
	if not pending_rows:
		return {"processed_scopes": 0, "runs": []}

	scope_ids = [r.name for r in pending_rows]
	ph = ", ".join(["%s"] * len(scope_ids))
	frappe.db.sql(
		f"update `tabCSKH Dirty Scope` set status = 'processing' where name in ({ph})",
		tuple(scope_ids),
	)
	frappe.db.commit()

	# Expand unique affected standard periods `(daily, weekly, monthly, quarterly, yearly)`
	periods_map: dict[tuple[str, str], PeriodSpec] = {}
	has_prior = False
	first_trigger_ref = ""
	reasons_seen: set[str] = set()

	for r in pending_rows:
		d = _parse_iso_date(r.dirty_date, "dirty_date")
		if cint(r.is_prior_period):
			has_prior = True
		if r.reason:
			reasons_seen.add(str(r.reason))
		if r.trigger_reference and not first_trigger_ref:
			first_trigger_ref = str(r.trigger_reference)
		for p in affected_standard_periods_for_date(d):
			periods_map[(p.period_type, p.period_key)] = p

	trig_reason = (
		"prior_period_correction"
		if (has_prior or bool(reasons_seen & RESTATED_TRIGGER_REASONS))
		else "dirty_scope_incremental"
	)
	runs_out: list[dict] = []
	last_run_id = ""
	try:
		for (_, _), p_spec in sorted(periods_map.items(), key=lambda kv: (kv[0][0], kv[0][1])):
			res = execute_kpi_recompute(
				p_spec,
				run_mode="restated" if has_prior else "incremental",
				as_of=as_of,
				trigger_reason=trig_reason,
				trigger_reference=first_trigger_ref,
				actor="Administrator",
			)
			runs_out.append(res)
			last_run_id = res["run_id"]
	except Exception:
		frappe.db.sql(
			f"update `tabCSKH Dirty Scope` set status = 'pending' where name in ({ph})",
			tuple(scope_ids),
		)
		frappe.db.commit()
		raise

	done_dt = now_datetime()
	frappe.db.sql(
		f"""
		update `tabCSKH Dirty Scope`
		set status = 'processed',
		    processed_by_run = %s,
		    processed_at = %s
		where name in ({ph})
		""",
		(last_run_id or None, done_dt, *scope_ids),
	)
	frappe.db.commit()
	return {
		"processed_scopes": len(scope_ids),
		"periods_recomputed": len(runs_out),
		"runs": runs_out,
	}


def has_pending_dirty_scope_for_period(period: PeriodSpec, campaign_filter: str = "") -> bool:
	"""Return True if any `pending` or `processing` `CSKH Dirty Scope` overlaps `[from_date, to_date]`."""
	if not frappe.db.table_exists("CSKH Dirty Scope"):
		return False
	if campaign_filter:
		rows = frappe.db.sql(
			"""
			select name from `tabCSKH Dirty Scope`
			where status in ('pending', 'processing')
			  and dirty_date >= %s and dirty_date <= %s
			  and (campaign = %s or campaign is null or campaign = '')
			limit 1
			""",
			(period.from_date.isoformat(), period.to_date.isoformat(), campaign_filter),
		)
	else:
		rows = frappe.db.sql(
			"""
			select name from `tabCSKH Dirty Scope`
			where status in ('pending', 'processing')
			  and dirty_date >= %s and dirty_date <= %s
			limit 1
			""",
			(period.from_date.isoformat(), period.to_date.isoformat()),
		)
	return bool(rows)


def run_kpi_backfill(
	from_date: str | date,
	to_date: str | date,
	*,
	period_types: tuple[str, ...] | list[str] = ("daily", "monthly"),
	batch_days: int = 7,
	dry_run: bool = False,
	resume_checkpoint: dict | str | None = None,
	campaign_filter: str = "",
	as_of: datetime | str | None = None,
	actor: str | None = None,
	stop_after_batches: int | None = None,
) -> dict:
	"""Batch-checkpointed historical KPI backfill with `dry_run` & reconciliation support (`P3-05`)."""
	d_from = _parse_iso_date(from_date, "from_date")
	d_to = _parse_iso_date(to_date, "to_date")
	if d_from > d_to:
		raise InvalidRequest("Ngày bắt đầu backfill (from_date) không được lớn hơn to_date.")
	b_days = max(int(batch_days or 7), 1)

	ckpt: dict = {}
	if resume_checkpoint:
		ckpt = (
			json.loads(resume_checkpoint) if isinstance(resume_checkpoint, str) else dict(resume_checkpoint)
		)
	completed_keys: set[str] = set(ckpt.get("completed_period_keys") or [])

	# Partition `[d_from, d_to]` into deterministic day batches
	all_days = iter_days(d_from, d_to)
	batches: list[list[date]] = [all_days[i : i + b_days] for i in range(0, len(all_days), b_days)]

	ptypes = [p.strip().lower() for p in period_types if p and p != "custom"]
	if not ptypes:
		ptypes = ["daily", "monthly"]

	batches_executed = 0
	runs_executed: list[dict] = []

	for b_idx, day_batch in enumerate(batches):
		batch_periods: dict[tuple[str, str], PeriodSpec] = {}
		for d in day_batch:
			for p in affected_standard_periods_for_date(d):
				if p.period_type in ptypes:
					batch_periods[(p.period_type, p.period_key)] = p

		pending_in_batch = [
			p for (pt, pk), p in sorted(batch_periods.items()) if f"{pt}:{pk}" not in completed_keys
		]
		if not pending_in_batch:
			continue

		for p_spec in pending_in_batch:
			res = execute_kpi_recompute(
				p_spec,
				run_mode="dry_run" if dry_run else "backfill",
				campaign_filter=campaign_filter,
				as_of=as_of,
				trigger_reason="backfill_dry_run" if dry_run else "backfill",
				actor=actor or "Administrator",
				dry_run=dry_run,
			)
			runs_executed.append(res)
			completed_keys.add(f"{p_spec.period_type}:{p_spec.period_key}")
			ckpt_payload = {
				"from_date": d_from.isoformat(),
				"to_date": d_to.isoformat(),
				"last_batch_index": b_idx,
				"completed_period_keys": sorted(completed_keys),
			}
			frappe.db.sql(
				"update `tabCSKH KPI Run` set checkpoint_json = %s where name = %s",
				(json.dumps(ckpt_payload, ensure_ascii=False), res["run_id"]),
			)
			frappe.db.commit()

		batches_executed += 1
		if stop_after_batches is not None and batches_executed >= int(stop_after_batches):
			return {
				"status": "partial_checkpointed",
				"dry_run": bool(dry_run),
				"batches_executed": batches_executed,
				"total_batches": len(batches),
				"runs_count": len(runs_executed),
				"runs": runs_executed,
				"checkpoint": {
					"from_date": d_from.isoformat(),
					"to_date": d_to.isoformat(),
					"last_batch_index": b_idx,
					"completed_period_keys": sorted(completed_keys),
				},
			}

	return {
		"status": "completed",
		"dry_run": bool(dry_run),
		"batches_executed": batches_executed,
		"total_batches": len(batches),
		"runs_count": len(runs_executed),
		"runs": runs_executed,
		"checkpoint": {
			"from_date": d_from.isoformat(),
			"to_date": d_to.isoformat(),
			"last_batch_index": len(batches) - 1 if batches else 0,
			"completed_period_keys": sorted(completed_keys),
		},
	}


def request_scoped_recompute(
	*,
	period_type: str | None = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	scope_type: str | None = "all",
	scope_id: str | None = None,
	campaign: str | None = None,
	reason: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Authorized `request_recompute` entry point (`docs/p3/report-api-contract.md` §3.8)."""
	user = actor or frappe.session.user
	roles = _roles(user)
	is_admin = _is_system(user) or ROLE_ADMIN in roles
	is_leader = _is_leader(user)

	if not (is_admin or is_leader):
		raise ForbiddenScope("Bạn không có quyền kích hoạt tính lại KPI (request_recompute).")

	period = resolve_period_spec(period_type, period_key, from_date, to_date)
	stype = (scope_type or ("team" if is_leader and not is_admin else "all")).strip().lower()
	sid = str(scope_id or "").strip()

	if is_leader and not is_admin:
		if stype not in ("team", "employee"):
			raise ForbiddenScope(
				"Team Leader chỉ được yêu cầu tính lại KPI trong phạm vi team hoặc nhân sự thuộc cây của mình."
			)
		if stype == "team":
			if not sid or not _node_in_subtree(user, sid):
				raise ForbiddenScope("Nhánh team yêu cầu tính lại nằm ngoài phạm vi quản lý của bạn.")
		elif stype == "employee":
			if not sid or not (sid == user or _in_team(user, sid)):
				raise ForbiddenScope("Nhân sự yêu cầu tính lại nằm ngoài phạm vi quản lý của bạn.")

	return execute_kpi_recompute(
		period,
		run_mode="incremental",
		campaign_filter=str(campaign or "").strip(),
		target_scope_type=stype,
		target_scope_id=sid,
		trigger_reason=str(reason or "manual_api_request")[:140],
		actor=user,
	)


def get_kpi_run_status(run_id: str, *, actor: str | None = None) -> dict:
	"""Authorized `get_run_status` enforcing `S17` anti-ID-probing (`docs/p3/authorization-matrix.md`)."""
	user = actor or frappe.session.user
	if not run_id or not str(run_id).strip():
		raise InvalidRequest("Tham số run_id là bắt buộc.")
	rid = str(run_id).strip()

	roles = _roles(user)
	is_admin = _is_system(user) or ROLE_ADMIN in roles
	is_bod = ROLE_BOD in roles
	is_leader = _is_leader(user)
	is_staff_only = _is_staff(user) and not (is_admin or is_bod or is_leader)

	# Staff cannot probe run_ids of company/team/other scopes (`S17`)
	if is_staff_only or not (is_admin or is_bod or is_leader):
		raise ForbiddenScope("Nhân viên không có quyền truy vấn trạng thái tiến trình KPI Run.")

	row = frappe.db.get_value(
		"CSKH KPI Run",
		rid,
		[
			"name",
			"run_mode",
			"status",
			"period_type",
			"period_key",
			"from_date",
			"to_date",
			"target_scope_type",
			"target_scope_id",
			"campaign_filter",
			"as_of",
			"source_watermark",
			"metric_version",
			"is_latest_published",
			"is_restated",
			"restated_from_run",
			"trigger_reason",
			"requested_by",
			"snapshots_written",
			"reconciliation_ok",
			"started_at",
			"completed_at",
		],
		as_dict=True,
	)
	if not row:
		if is_admin:
			raise NotFound("Không tìm thấy tiến trình CSKH KPI Run.")
		raise ForbiddenScope("Bạn không có quyền truy cập tiến trình CSKH KPI Run này.")

	if is_leader and not (is_admin or is_bod):
		# Leader can inspect runs covering company/all aggregate (for dashboard freshness banner) ONLY if published,
		# OR runs explicitly scoped to their own subtree / requested by themselves; never another team's private run!
		t_type = row.target_scope_type or "all"
		t_id = row.target_scope_id or ""
		allowed = False
		if row.requested_by == user:
			allowed = True
		elif t_type == "all" and cint(row.is_latest_published):
			allowed = True
		elif t_type == "team" and t_id and _node_in_subtree(user, t_id):
			allowed = True
		elif t_type == "employee" and t_id and (t_id == user or _in_team(user, t_id)):
			allowed = True
		if not allowed:
			raise ForbiddenScope("Tiến trình CSKH KPI Run này nằm ngoài phạm vi team của bạn.")

	return {
		"ok": True,
		"run_id": row.name,
		"run_mode": row.run_mode,
		"status": row.status,
		"period": {
			"period_type": row.period_type,
			"period_key": row.period_key,
			"from_date": str(row.from_date),
			"to_date": str(row.to_date),
		},
		"target_scope_type": row.target_scope_type,
		"target_scope_id": row.target_scope_id,
		"campaign_filter": row.campaign_filter or None,
		"as_of": format_calendar_iso(row.as_of) if row.as_of else None,
		"source_watermark": format_calendar_iso(row.source_watermark) if row.source_watermark else None,
		"metric_version": row.metric_version,
		"is_latest_published": bool(cint(row.is_latest_published)),
		"is_restated": bool(cint(row.is_restated)),
		"restated_from_run": row.restated_from_run or None,
		"snapshots_written": cint(row.snapshots_written),
		"reconciliation_ok": bool(cint(row.reconciliation_ok)),
		"started_at": format_calendar_iso(row.started_at) if row.started_at else None,
		"completed_at": format_calendar_iso(row.completed_at) if row.completed_at else None,
	}
