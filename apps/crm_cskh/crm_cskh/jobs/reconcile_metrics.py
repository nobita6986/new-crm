"""Independent KPI reconciliation audit job (`P3-05`, `docs/p3/metric-reconciliation.md`).

Compares `is_latest_published = 1` `CSKH KPI Snapshot` records against live recomputed raw figures
at the run's `as_of` timestamp so any silent drift is immediately detected.
"""

from __future__ import annotations

import frappe

from crm_cskh.services.errors import DataReconciliationFailed
from crm_cskh.services.kpi_calculator import compute_period_snapshots
from crm_cskh.services.kpi_periods import PeriodSpec, resolve_period_spec
from crm_cskh.services.kpi_runs import unpack_snapshot_metrics


def reconcile_published_period(
	period: PeriodSpec | None = None,
	*,
	period_type: str = "monthly",
	period_key: str | None = None,
	campaign_filter: str = "",
) -> dict:
	"""Verify that the currently published snapshots for `period` match raw ground truth at `run.as_of`."""
	p_spec = period or resolve_period_spec(period_type, period_key)
	camp_val = str(campaign_filter or "").strip()

	pub_run = frappe.db.get_value(
		"CSKH KPI Run",
		{
			"period_type": p_spec.period_type,
			"period_key": p_spec.period_key,
			"campaign_filter": camp_val,
			"is_latest_published": 1,
		},
		["name", "as_of", "source_watermark", "snapshots_written"],
		as_dict=True,
	)
	if not pub_run:
		return {
			"ok": True,
			"status": "no_published_run",
			"period": p_spec.to_dict(),
			"mismatches": [],
		}

	fresh = compute_period_snapshots(p_spec, as_of=pub_run.as_of, campaign_filter=camp_val)
	stored_rows = frappe.db.get_all(
		"CSKH KPI Snapshot",
		filters={"kpi_run": pub_run.name, "is_latest_published": 1},
		fields=[
			"scope_type",
			"scope_id",
			"contact_attempt_count",
			"connected_attempt_count",
			"distinct_work_items",
			"ownership_intervals_count",
			"metrics_json",
		],
	)
	stored_by_scope = {(r.scope_type, r.scope_id): r for r in stored_rows}
	fresh_by_scope = {(s["scope_type"], s["scope_id"]): s for s in fresh["snapshots"]}

	mismatches: list[str] = []
	for skey, fsnap in fresh_by_scope.items():
		srow = stored_by_scope.get(skey)
		if not srow:
			mismatches.append(f"Thiếu bản chụp đã lưu cho scope {skey}.")
			continue
		if int(srow.contact_attempt_count) != int(fsnap["contact_attempt_count"]):
			mismatches.append(
				f"Lệch contact_attempt_count tại {skey}: lưu={srow.contact_attempt_count}, thực={fsnap['contact_attempt_count']}"
			)
		s_metrics, _, _ = unpack_snapshot_metrics(srow.metrics_json)
		for mcode, fcell in fsnap["metrics"].items():
			scell = s_metrics.get(mcode) or {}
			if scell.get("value") != fcell.get("value") or scell.get("status") != fcell.get("status"):
				mismatches.append(
					f"Lệch {mcode} tại {skey}: lưu=({scell.get('value')},{scell.get('status')}), "
					f"thực=({fcell.get('value')},{fcell.get('status')})"
				)

	if mismatches:
		raise DataReconciliationFailed(
			f"Đối soát KPI thất bại trên run {pub_run.name}: {'; '.join(mismatches[:5])}",
			details={"run_id": pub_run.name, "mismatches": mismatches},
		)

	return {
		"ok": True,
		"status": "reconciled",
		"run_id": pub_run.name,
		"period": p_spec.to_dict(),
		"snapshots_checked": len(stored_rows),
		"mismatches": [],
	}
