"""Background worker & scheduled tasks for incremental KPI dirty-scope draining & daily refresh (`P3-05`)."""

from __future__ import annotations

from datetime import timedelta

from crm_cskh.services.kpi_periods import affected_standard_periods_for_date, today_hcm
from crm_cskh.services.kpi_runs import execute_kpi_recompute, process_dirty_scopes


def drain_dirty_kpi_scopes(max_scopes: int = 100) -> dict:
	"""Cron entry point (`*/5 * * * *`) draining pending `CSKH Dirty Scope` records."""
	return process_dirty_scopes(max_scopes=max_scopes)


def refresh_current_standard_periods() -> dict:
	"""Scheduled refresh ensuring today's & yesterday's standard KPI snapshots stay warm."""
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
