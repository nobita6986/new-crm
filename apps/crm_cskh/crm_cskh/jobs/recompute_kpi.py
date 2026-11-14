"""Background worker & scheduled tasks for incremental KPI dirty-scope draining & daily refresh (`P3-05`)."""

from __future__ import annotations

from datetime import timedelta

from crm_cskh.services.kpi_periods import (
	affected_standard_periods_for_date,
	resolve_period_spec,
	today_hcm,
)
from crm_cskh.services.kpi_runs import execute_kpi_recompute, process_dirty_scopes


def _is_reporting_enabled() -> bool:
	from crm_cskh.services.p3_flags import is_p3_reporting_enabled

	return bool(is_p3_reporting_enabled())


def drain_dirty_kpi_scopes(
	max_scopes: int = 100,
	*,
	defer_unmaterialized_large_rollups: bool = True,
) -> dict:
	"""Cron entry point (`*/5 * * * *`) draining pending `CSKH Dirty Scope` records."""
	if not _is_reporting_enabled():
		return {"processed_scopes": 0, "runs": [], "skipped": True, "reason": "p3_reporting_disabled"}
	return process_dirty_scopes(
		max_scopes=max_scopes,
		defer_unmaterialized_large_rollups=defer_unmaterialized_large_rollups,
	)


def run_single_period_recompute(
	period_type: str = "monthly",
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	campaign_filter: str | None = None,
	actor: str = "Administrator",
) -> dict:
	"""Async RQ job entry point enqueued when a large uncomputed period is requested by a reader."""
	if not _is_reporting_enabled():
		return {"ok": True, "skipped": True, "reason": "p3_reporting_disabled"}
	spec = resolve_period_spec(period_type, period_key, from_date, to_date)
	return execute_kpi_recompute(
		spec,
		run_mode="full",
		campaign_filter=str(campaign_filter or ""),
		trigger_reason="async_large_period_materialization",
		actor=actor,
	)


def refresh_current_standard_periods() -> dict:
	"""Scheduled refresh ensuring today's & yesterday's standard KPI snapshots stay warm."""
	if not _is_reporting_enabled():
		return {"periods_refreshed": 0, "runs": [], "skipped": True, "reason": "p3_reporting_disabled"}
	cur = today_hcm()
	prev = cur - timedelta(days=1)
	seen: dict[tuple[str, str], object] = {}
	for d in (prev, cur):
		for p in affected_standard_periods_for_date(d):
			seen[(p.period_type, p.period_key)] = p

	runs = []
	for (_, _), p_spec in sorted(seen.items()):
		runs.append(
			execute_kpi_recompute(
				p_spec,
				run_mode="incremental",
				trigger_reason="scheduled_warm_refresh",
				actor="Administrator",
			)
		)
	return {"periods_refreshed": len(runs), "runs": runs}
