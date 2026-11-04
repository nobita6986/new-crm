"""Synthetic `10k` & `100k` Work Item benchmark harness for Phase P3 (`P3-09`, `OBS-P2-02`).

Measures on an isolated synthetic benchmark site (never on Owner/production sites):
  1. Full monthly KPI recompute (`2026-10`) vs single-day incremental recompute (`2026-10-15`):
     wall-clock duration (`ms`), Python peak heap (`tracemalloc` MiB), and Linux `VmRSS` (`MiB`).
  2. Read latencies (`N=20` warm iterations -> `min`, `p50`, `p95`, `max` in `ms`) across:
     - `get_executive_overview` (`BOD Viewer`)
     - `get_team_comparison` (`BOD Viewer`)
     - `get_employee_quality` (`BOD Viewer`, sorted by `PROCESS_SCORE`)
     - `get_campaign_quality` (`BOD Viewer`)
     - `get_my_scorecard` (`Staff`)
     - `get_metric_drilldown` (daily company & monthly campaign, `page_size=20`)
  3. SQL query inspection proving `0` queries touch `tabCSKH Contact Attempt` on dashboard reads (`OBS-P2-02`).
  4. MariaDB `EXPLAIN` execution plans on `tabCSKH KPI Snapshot`, `tabCSKH Contact Attempt`, and
     `tabCSKH SLA Obligation` at both `10,000` and `100,000` Work Items.

Disclaimer:
  Results reflect a single-host DEV Docker environment (`MariaDB 11.8` + `Frappe v15.123.1`) on synthetic data
  and do NOT constitute a production SLA guarantee.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import statistics
import sys
import time
import tracemalloc
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import frappe

from crm_cskh.api import p3_reporting
from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services.attribution import AUTHORITATIVE
from crm_cskh.services.kpi_periods import resolve_period_spec
from crm_cskh.services.kpi_runs import (
	execute_kpi_recompute,
	process_dirty_scopes,
	schedule_post_commit_dirty_invalidation,
)

BENCH_MONTH_KEY = "2026-10"
BENCH_DAY_KEY = "2026-10-15"


def _read_proc_mem_mib() -> tuple[float, float]:
	"""Read current process `(VmRSS, VmHWM)` in MiB on Linux."""
	rss_mib = 0.0
	hwm_mib = 0.0
	status_path = Path("/proc/self/status")
	if status_path.is_file():
		for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
			if line.startswith("VmRSS:"):
				parts = line.split()
				if len(parts) >= 2 and parts[1].isdigit():
					rss_mib = round(int(parts[1]) / 1024.0, 2)
			elif line.startswith("VmHWM:"):
				parts = line.split()
				if len(parts) >= 2 and parts[1].isdigit():
					hwm_mib = round(int(parts[1]) / 1024.0, 2)
	return rss_mib, hwm_mib


def _percentile(sorted_vals: list[float], pct: float) -> float:
	if not sorted_vals:
		return 0.0
	if len(sorted_vals) == 1:
		return round(sorted_vals[0], 3)
	k = (len(sorted_vals) - 1) * (pct / 100.0)
	f = math.floor(k)
	c = math.ceil(k)
	if f == c:
		return round(sorted_vals[int(k)], 3)
	d0 = sorted_vals[f] * (c - k)
	d1 = sorted_vals[c] * (k - f)
	return round(d0 + d1, 3)


@contextmanager
def _capture_sql_statements():
	"""Intercept `frappe.db.sql` calls to count statements and verify `0` `tabCSKH Contact Attempt` scans."""
	captured: list[str] = []
	orig_sql = frappe.db.sql

	def _wrapped(query, *args, **kwargs):
		captured.append(str(query))
		return orig_sql(query, *args, **kwargs)

	frappe.db.sql = _wrapped
	try:
		yield captured
	finally:
		frappe.db.sql = orig_sql


def _setup_org_and_campaigns() -> dict:
	"""Seed 4 Teams, 20 Staff, 1 BOD Viewer, 1 CRM Admin, and 5 Campaigns idempotently."""
	frappe.set_user("Administrator")
	ensure_p2_indexes()
	ensure_p2_seeds()
	ensure_p3_indexes()
	ensure_p3_seeds()
	frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
	if frappe.db.exists("DocType", "FCRM Settings"):
		frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

	for role_name in ("CSKH BOD Viewer", "CSKH CRM Admin", "Sales Manager", "Sales User"):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)

	def _ensure_user(email: str, full_name: str, roles: list[str]) -> str:
		if not frappe.db.exists("User", email):
			u = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": full_name,
					"enabled": 1,
					"language": "vi",
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			)
			u.insert(ignore_permissions=True)
			u.add_roles(*roles)
		return email

	bod_user = _ensure_user("bod.bench@dev.local", "BOD Bench", ["CSKH BOD Viewer"])
	admin_user = _ensure_user("admin.bench@dev.local", "Admin Bench", ["CSKH CRM Admin"])

	team_nodes: list[str] = []
	leaders: list[str] = []
	staff_users: list[str] = []
	staff_team_map: dict[str, str] = {}

	for t_idx in range(1, 5):
		leader_email = _ensure_user(
			f"leader.bench.{t_idx}@dev.local",
			f"Leader Bench {t_idx}",
			["Sales Manager", "Sales User"],
		)
		leaders.append(leader_email)
		t_title = f"Team Bench {t_idx}"
		t_node = frappe.db.get_value("CRM Sales Hierarchy", {"full_name": t_title}, "name")
		if not t_node:
			t_doc = frappe.get_doc(
				{
					"doctype": "CRM Sales Hierarchy",
					"full_name": t_title,
					"is_group": 1,
					"user": leader_email,
				}
			)
			t_doc.insert(ignore_permissions=True)
			t_node = t_doc.name
		team_nodes.append(t_node)

		for s_offset in range(1, 6):
			s_num = (t_idx - 1) * 5 + s_offset
			s_email = _ensure_user(
				f"staff.bench.{s_num:02d}@dev.local",
				f"Staff Bench {s_num:02d}",
				["Sales User"],
			)
			staff_users.append(s_email)
			staff_team_map[s_email] = t_node
			if not frappe.db.exists("CRM Sales Hierarchy", {"user": s_email}):
				frappe.get_doc(
					{
						"doctype": "CRM Sales Hierarchy",
						"full_name": f"Staff Bench {s_num:02d}",
						"is_group": 0,
						"user": s_email,
						"reports_to": t_node,
					}
				).insert(ignore_permissions=True)

	# Backdate synthetic benchmark membership intervals before 2026-01-01 so all 2026-10 rows resolve `authoritative`
	frappe.db.sql("update `tabCSKH Team Membership History` set effective_from = '2026-01-01 00:00:00'")

	campaigns: list[str] = []
	for c_idx in range(1, 6):
		code = f"BENCH-CAMP-{c_idx:02d}"
		c_name = frappe.db.get_value("CSKH Campaign", {"code": code}, "name")
		if not c_name:
			t_scope = team_nodes[c_idx - 1] if c_idx <= 4 else None
			c_doc = frappe.get_doc(
				{
					"doctype": "CSKH Campaign",
					"code": code,
					"title": f"Chiến dịch Benchmark {c_idx:02d}",
					"channel": "Telesales",
					"status": "Active",
					"assignment_mode": "weighted_round_robin",
					"start_date": "2026-01-01",
					"end_date": "2026-12-31",
					"team_scope": t_scope,
				}
			)
			c_doc.insert(ignore_permissions=True)
			c_name = c_doc.name
		campaigns.append(c_name)

	frappe.db.commit()
	return {
		"bod_user": bod_user,
		"admin_user": admin_user,
		"leaders": leaders,
		"team_nodes": team_nodes,
		"staff_users": staff_users,
		"staff_team_map": staff_team_map,
		"campaigns": campaigns,
	}


def _bulk_insert_chunk(table: str, columns: list[str], rows: list[tuple], chunk_size: int = 2500) -> None:
	if not rows:
		return
	col_sql = ", ".join(f"`{c}`" for c in columns)
	row_ph = "(" + ", ".join(["%s"] * len(columns)) + ")"
	for offset in range(0, len(rows), chunk_size):
		batch = rows[offset : offset + chunk_size]
		values_sql = ", ".join([row_ph] * len(batch))
		flat_params = [val for tup in batch for val in tup]
		frappe.db.sql(f"INSERT INTO `{table}` ({col_sql}) VALUES {values_sql}", tuple(flat_params))
	frappe.db.commit()


def _seed_synthetic_range(start_idx: int, end_idx: int, ctx: dict) -> dict[str, int]:
	"""Bulk-insert deterministic synthetic Work Items `[start_idx, end_idx)` into `2026-10`."""
	staff_users: list[str] = ctx["staff_users"]
	staff_team_map: dict[str, str] = ctx["staff_team_map"]
	campaigns: list[str] = ctx["campaigns"]

	# Process in macro-batches of 10,000 Work Items so peak insertion RAM stays bounded
	inserted = {
		"leads": 0,
		"work_items": 0,
		"intervals": 0,
		"attempts": 0,
		"obligations": 0,
	}

	lead_cols = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"naming_series",
		"first_name",
		"lead_name",
		"mobile_no",
		"status",
		"lead_owner",
		"cskh_do_not_contact",
	]
	wi_cols = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"lead",
		"campaign",
		"status",
		"assigned_to",
		"assigned_at",
		"owner_at_close",
		"closed_by",
		"closed_at",
		"attempt_count",
		"connected_attempt_count",
		"first_attempt_at",
		"last_attempt_at",
		"last_outcome_code",
		"last_outcome_label",
		"revision",
	]
	ah_cols = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"work_item",
		"action",
		"from_user",
		"to_user",
		"actor",
		"event_at",
		"ownership_start",
		"ownership_end",
		"event_id",
		"team_at_assignment",
		"attribution_status",
		"reason",
	]
	att_cols = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"work_item",
		"lead",
		"campaign",
		"ownership_event",
		"owner_at_attempt",
		"performed_by",
		"occurred_at",
		"recorded_at",
		"channel",
		"outcome_code",
		"outcome_label_snapshot",
		"outcome_group_snapshot",
		"outcome_version_snapshot",
		"contact_attempt_snapshot",
		"connected_snapshot",
		"interested_snapshot",
		"terminal_snapshot",
		"dnc_snapshot",
		"note",
		"verification_status",
		"policy_name_snapshot",
		"policy_version_snapshot",
		"idempotency_key",
		"payload_hash",
		"work_item_revision",
	]
	sla_cols = [
		"name",
		"creation",
		"modified",
		"modified_by",
		"owner",
		"docstatus",
		"idx",
		"obligation_type",
		"work_item",
		"lead",
		"campaign",
		"interval_event",
		"owner_user",
		"team_at_obligation",
		"attribution_status",
		"status",
		"informational_only",
		"is_recovery_obligation",
		"reschedule_seq",
		"started_at",
		"original_due_at",
		"effective_due_at",
		"is_paused",
		"fulfilled_at",
		"fulfilled_by_attempt",
		"breached_at",
		"closed_at",
		"evaluation_anchor_at",
		"policy_name_snapshot",
		"policy_version_snapshot",
		"idempotency_key",
	]

	for block_start in range(start_idx, end_idx, 10000):
		block_end = min(block_start + 10000, end_idx)
		lead_rows: list[tuple] = []
		wi_rows: list[tuple] = []
		ah_rows: list[tuple] = []
		att_rows: list[tuple] = []
		sla_rows: list[tuple] = []

		for i in range(block_start, block_end):
			day_num = (i % 27) + 1  # 2026-10-01 .. 2026-10-27
			hour_num = 8 + (i % 8)
			if hour_num >= 12:
				hour_num += 1  # skip lunch 12:00-13:00
			min_num = (i * 7) % 40
			base_ts = datetime(2026, 10, day_num, hour_num, min_num, 0)

			s_idx = i % len(staff_users)
			owner_u = staff_users[s_idx]
			team_u = staff_team_map[owner_u]
			# Match team campaign 80% of the time, shared campaign 20% of the time
			camp = campaigns[4] if (i % 5 == 4) else campaigns[s_idx // 5]

			lead_id = f"BENCH-LEAD-{i:06d}"
			wi_id = f"BENCH-WI-{i:06d}"
			phone = f"0909{i:06d}"
			is_closed = (i % 3) != 0

			lead_rows.append(
				(
					lead_id,
					base_ts,
					base_ts,
					"Administrator",
					"Administrator",
					0,
					0,
					"CRM-LEAD-",
					f"Khách Benchmark {i:06d}",
					f"Khách Benchmark {i:06d}",
					phone,
					"Converted" if is_closed else "Contacted",
					None if is_closed else owner_u,
					0,
				)
			)

			# Ownership interval 1 (+ optional transfer interval on every 10th Work Item)
			ah_1 = f"BENCH-AH-{i:06d}-1"
			has_xfer = (i % 10) == 0
			if has_xfer:
				next_u = staff_users[(s_idx + 5) % len(staff_users)]
				next_team = staff_team_map[next_u]
				xfer_ts = base_ts + timedelta(minutes=25)
				close_ts = base_ts + timedelta(hours=2) if is_closed else None
				ah_rows.append(
					(
						ah_1,
						base_ts,
						xfer_ts,
						"Administrator",
						"Administrator",
						0,
						0,
						wi_id,
						"assign",
						None,
						owner_u,
						"Administrator",
						base_ts,
						base_ts,
						xfer_ts,
						f"evt-{i:06d}-1",
						team_u,
						AUTHORITATIVE,
						"Phân bổ benchmark",
					)
				)
				ah_2 = f"BENCH-AH-{i:06d}-2"
				ah_rows.append(
					(
						ah_2,
						xfer_ts,
						close_ts or xfer_ts,
						"Administrator",
						"Administrator",
						0,
						0,
						wi_id,
						"reassign",
						owner_u,
						next_u,
						"Administrator",
						xfer_ts,
						xfer_ts,
						close_ts,
						f"evt-{i:06d}-2",
						next_team,
						AUTHORITATIVE,
						"Điều chuyển benchmark",
					)
				)
				final_owner = next_u
				final_team = next_team
				final_ah = ah_2
				att_base_ts = xfer_ts + timedelta(minutes=5)
			else:
				close_ts = base_ts + timedelta(hours=2) if is_closed else None
				ah_rows.append(
					(
						ah_1,
						base_ts,
						close_ts or base_ts,
						"Administrator",
						"Administrator",
						0,
						0,
						wi_id,
						"assign",
						None,
						owner_u,
						"Administrator",
						base_ts,
						base_ts,
						close_ts,
						f"evt-{i:06d}-1",
						team_u,
						AUTHORITATIVE,
						"Phân bổ benchmark",
					)
				)
				final_owner = owner_u
				final_team = team_u
				final_ah = ah_1
				att_base_ts = base_ts + timedelta(minutes=12 if (i % 5 != 0) else 45)

			# Deterministic 2 or 3 attempts per Work Item -> exact average 2.5 attempts/WI (`25k` at `10k`, `250k` at `100k`)
			num_atts = 3 if (i % 2 == 0) else 2
			first_att_id = f"BENCH-ATT-{i:06d}-1"
			last_att_ts = att_base_ts
			conn_cnt = 0
			last_code = "no_answer"
			last_label = "Không nghe máy"

			for a_seq in range(1, num_atts + 1):
				att_id = f"BENCH-ATT-{i:06d}-{a_seq}"
				occ_ts = att_base_ts + timedelta(minutes=(a_seq - 1) * 20)
				last_att_ts = occ_ts
				if a_seq == num_atts and (i % 4 != 0):
					ocode = "connected_interested"
					olabel = "Kết nối - Quan tâm"
					ogroup = "connected"
					is_conn = 1
					is_int = 1
					conn_cnt += 1
				elif a_seq == 1:
					ocode = "no_answer"
					olabel = "Không nghe máy"
					ogroup = "unreached"
					is_conn = 0
					is_int = 0
				else:
					ocode = "busy"
					olabel = "Máy bận"
					ogroup = "unreached"
					is_conn = 0
					is_int = 0
				last_code = ocode
				last_label = olabel
				att_rows.append(
					(
						att_id,
						occ_ts,
						occ_ts,
						final_owner,
						final_owner,
						0,
						0,
						wi_id,
						lead_id,
						camp,
						final_ah,
						final_owner,
						final_owner,
						occ_ts,
						occ_ts,
						"phone_call",
						ocode,
						olabel,
						ogroup,
						1,
						1,
						is_conn,
						is_int,
						0,
						0,
						f"Ghi chú benchmark {i:06d}-{a_seq}",
						"self_reported",
						"DEFAULT-POLICY",
						1,
						f"bench-idem-att-{i:06d}-{a_seq}",
						f"hash-{i:06d}-{a_seq}",
						2,
					)
				)

			wi_rows.append(
				(
					wi_id,
					base_ts,
					last_att_ts,
					"Administrator",
					"Administrator",
					0,
					0,
					lead_id,
					camp,
					"closed" if is_closed else "in_progress",
					None if is_closed else final_owner,
					base_ts,
					final_owner if is_closed else None,
					final_owner if is_closed else None,
					(last_att_ts + timedelta(minutes=5)) if is_closed else None,
					num_atts,
					conn_cnt,
					att_base_ts,
					last_att_ts,
					last_code,
					last_label,
					2,
				)
			)

			# 2 SLA obligations per Work Item -> `20,000` at `10k`, `200,000` at `100k`
			due_1 = base_ts + timedelta(minutes=30)
			on_time_1 = att_base_ts <= due_1
			st_1 = "fulfilled_on_time" if on_time_1 else "fulfilled_late"
			sla_rows.append(
				(
					f"BENCH-SLA-{i:06d}-FA",
					base_ts,
					att_base_ts,
					"Administrator",
					"Administrator",
					0,
					0,
					"FIRST_ATTEMPT",
					wi_id,
					lead_id,
					camp,
					final_ah,
					final_owner,
					final_team,
					AUTHORITATIVE,
					st_1,
					0,
					0,
					0,
					base_ts,
					due_1,
					due_1,
					0,
					att_base_ts,
					first_att_id,
					None if on_time_1 else due_1,
					None,
					att_base_ts,
					"DEFAULT-POLICY",
					1,
					f"bench-sla-fa-{i:06d}",
				)
			)

			if i % 2 == 0:
				# `FOLLOWUP` obligation
				fu_start = att_base_ts
				fu_due = fu_start + timedelta(minutes=35)
				fu_done = att_base_ts + timedelta(minutes=20)
				sla_rows.append(
					(
						f"BENCH-SLA-{i:06d}-FU",
						fu_start,
						fu_done,
						"Administrator",
						"Administrator",
						0,
						0,
						"FOLLOWUP",
						wi_id,
						lead_id,
						camp,
						final_ah,
						final_owner,
						final_team,
						AUTHORITATIVE,
						"fulfilled_on_time",
						0,
						0,
						0,
						fu_start,
						fu_due,
						fu_due,
						0,
						fu_done,
						f"BENCH-ATT-{i:06d}-2",
						None,
						None,
						fu_done,
						"DEFAULT-POLICY",
						1,
						f"bench-sla-fu-{i:06d}",
					)
				)
			else:
				# `FIRST_CONNECTED` informational obligation (`informational_only = 1`)
				fc_due = base_ts + timedelta(minutes=120)
				fc_status = "fulfilled_on_time" if conn_cnt > 0 else "handoff_closed"
				fc_anchor = last_att_ts if conn_cnt > 0 else fc_due
				sla_rows.append(
					(
						f"BENCH-SLA-{i:06d}-FC",
						base_ts,
						fc_anchor,
						"Administrator",
						"Administrator",
						0,
						0,
						"FIRST_CONNECTED",
						wi_id,
						lead_id,
						camp,
						final_ah,
						final_owner,
						final_team,
						AUTHORITATIVE,
						fc_status,
						1,
						0,
						0,
						base_ts,
						fc_due,
						fc_due,
						0,
						last_att_ts if conn_cnt > 0 else None,
						f"BENCH-ATT-{i:06d}-{num_atts}" if conn_cnt > 0 else None,
						None,
						fc_due if conn_cnt == 0 else None,
						fc_anchor,
						"DEFAULT-POLICY",
						1,
						f"bench-sla-fc-{i:06d}",
					)
				)

		_bulk_insert_chunk("tabCRM Lead", lead_cols, lead_rows)
		_bulk_insert_chunk("tabCSKH Work Item", wi_cols, wi_rows)
		_bulk_insert_chunk("tabCSKH Assignment History", ah_cols, ah_rows)
		_bulk_insert_chunk("tabCSKH Contact Attempt", att_cols, att_rows)
		_bulk_insert_chunk("tabCSKH SLA Obligation", sla_cols, sla_rows)

		inserted["leads"] += len(lead_rows)
		inserted["work_items"] += len(wi_rows)
		inserted["intervals"] += len(ah_rows)
		inserted["attempts"] += len(att_rows)
		inserted["obligations"] += len(sla_rows)
		print(
			f"  [seed] inserted up to {block_end:,} Work Items "
			f"(attempts={inserted['attempts']:,}, obligations={inserted['obligations']:,})",
			flush=True,
		)

	for tbl in (
		"tabCRM Lead",
		"tabCSKH Work Item",
		"tabCSKH Assignment History",
		"tabCSKH Contact Attempt",
		"tabCSKH SLA Obligation",
	):
		frappe.db.sql(f"ANALYZE TABLE `{tbl}`")
	frappe.db.commit()
	return inserted


def _measure_callable_with_memory(fn, *, measure_tracemalloc_separately: bool = False) -> tuple[object, dict]:
	"""Measure true un-instrumented wall-clock time + Linux `VmRSS`/`VmHWM`, plus optional separate `tracemalloc` peak."""
	gc.collect()
	rss_before, _ = _read_proc_mem_mib()
	t0 = time.perf_counter()
	res = fn()
	elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
	rss_after, hwm_after = _read_proc_mem_mib()

	peak_mib: float | None = None
	if measure_tracemalloc_separately:
		gc.collect()
		tracemalloc.start()
		try:
			fn()
			_, peak_bytes = tracemalloc.get_traced_memory()
			peak_mib = round(peak_bytes / (1024.0 * 1024.0), 2)
		finally:
			tracemalloc.stop()

	return res, {
		"wall_ms": elapsed_ms,
		"tracemalloc_peak_mib": peak_mib,
		"rss_before_mib": rss_before,
		"rss_after_mib": rss_after,
		"rss_hwm_mib": hwm_after,
	}


def _benchmark_endpoint(fn, iterations: int = 20, warmups: int = 2) -> dict:
	for _ in range(warmups):
		fn()

	latencies_ms: list[float] = []
	sql_queries_sample: list[str] = []
	for idx in range(iterations):
		if idx == 0:
			with _capture_sql_statements() as captured:
				t0 = time.perf_counter()
				fn()
				latencies_ms.append((time.perf_counter() - t0) * 1000.0)
			sql_queries_sample = list(captured)
		else:
			t0 = time.perf_counter()
			fn()
			latencies_ms.append((time.perf_counter() - t0) * 1000.0)

	latencies_ms.sort()
	touches_attempt_table = any("tabCSKH Contact Attempt" in q for q in sql_queries_sample)
	touches_snapshot_table = any("tabCSKH KPI Snapshot" in q for q in sql_queries_sample)
	return {
		"iterations": iterations,
		"min_ms": round(latencies_ms[0], 3),
		"p50_ms": _percentile(latencies_ms, 50),
		"p95_ms": _percentile(latencies_ms, 95),
		"mean_ms": round(statistics.mean(latencies_ms), 3),
		"max_ms": round(latencies_ms[-1], 3),
		"sql_query_count": len(sql_queries_sample),
		"touches_contact_attempt_table": touches_attempt_table,
		"touches_kpi_snapshot_table": touches_snapshot_table,
	}


def _collect_explain_plans(camp_name: str) -> dict[str, list[dict]]:
	def _explain(sql: str, params: tuple) -> list[dict]:
		rows = frappe.db.sql("EXPLAIN " + sql, params, as_dict=True)
		return [
			{
				"table": r.get("table"),
				"type": r.get("type"),
				"possible_keys": r.get("possible_keys"),
				"key": r.get("key"),
				"key_len": r.get("key_len"),
				"rows": r.get("rows"),
				"Extra": r.get("Extra"),
			}
			for r in rows
		]

	return {
		"dashboard_snapshot_lookup": _explain(
			"""
			SELECT name, scope_id, metrics_json
			FROM `tabCSKH KPI Snapshot`
			WHERE is_latest_published = %s
			  AND period_type = %s
			  AND period_key = %s
			  AND campaign_filter = %s
			  AND scope_type = %s
			""",
			(1, "monthly", BENCH_MONTH_KEY, "", "employee"),
		),
		"dashboard_snapshot_rank": _explain(
			"""
			SELECT name, scope_id, process_score_sort
			FROM `tabCSKH KPI Snapshot`
			WHERE is_latest_published = %s
			  AND period_type = %s
			  AND period_key = %s
			  AND scope_type = %s
			ORDER BY process_score_sort DESC
			LIMIT 20
			""",
			(1, "monthly", BENCH_MONTH_KEY, "employee"),
		),
		"recompute_attempt_daily_range": _explain(
			"""
			SELECT name, work_item, occurred_at, outcome_code
			FROM `tabCSKH Contact Attempt`
			WHERE occurred_at >= %s
			  AND occurred_at <= %s
			  AND recorded_at <= %s
			""",
			("2026-10-15 00:00:00", "2026-10-15 23:59:59", "2026-11-01 00:00:00"),
		),
		"recompute_sla_due_evaluator": _explain(
			"""
			SELECT name, work_item, effective_due_at
			FROM `tabCSKH SLA Obligation`
			WHERE status = %s
			  AND is_paused = 0
			  AND effective_due_at <= %s
			""",
			("open", "2026-10-15 17:00:00"),
		),
		"recompute_sla_campaign_anchor": _explain(
			"""
			SELECT name, status, evaluation_anchor_at
			FROM `tabCSKH SLA Obligation`
			WHERE campaign = %s
			  AND evaluation_anchor_at >= %s
			  AND evaluation_anchor_at <= %s
			""",
			(camp_name, "2026-10-01 00:00:00", "2026-10-31 23:59:59"),
		),
	}


def _run_scale_tier(label: str, ctx: dict) -> dict:
	frappe.set_user("Administrator")
	counts = {
		"work_items": int(frappe.db.count("CSKH Work Item")),
		"leads": int(frappe.db.count("CRM Lead")),
		"ownership_intervals": int(frappe.db.count("CSKH Assignment History")),
		"contact_attempts": int(frappe.db.count("CSKH Contact Attempt")),
		"sla_obligations": int(frappe.db.count("CSKH SLA Obligation")),
	}
	print(f"\n=== Measuring tier {label}: {counts} ===", flush=True)

	month_spec = resolve_period_spec("monthly", BENCH_MONTH_KEY)
	day_spec = resolve_period_spec("daily", BENCH_DAY_KEY)
	sample_campaign = ctx["campaigns"][0]
	sample_staff = ctx["staff_users"][0]
	bod_user = ctx["bod_user"]
	admin_user = ctx["admin_user"]

	# 1. Full monthly recompute (`2026-10`) + campaign-filtered snapshot (`sample_campaign`)
	full_res, full_mem = _measure_callable_with_memory(
		lambda: execute_kpi_recompute(
			month_spec,
			run_mode="full",
			trigger_reason=f"benchmark_full_{label}",
			as_of="2026-11-01 00:00:00",
			actor="Administrator",
		),
		measure_tracemalloc_separately=True,
	)
	camp_res, camp_mem = _measure_callable_with_memory(
		lambda: execute_kpi_recompute(
			month_spec,
			run_mode="full",
			campaign_filter=sample_campaign,
			trigger_reason=f"benchmark_camp_{label}",
			as_of="2026-11-01 00:00:00",
			actor="Administrator",
		)
	)

	# 2. Single-day incremental recompute (`2026-10-15`) + full dirty-scope drain (`daily`+`weekly`+`monthly`+`quarterly`+`yearly`)
	day_res, day_mem = _measure_callable_with_memory(
		lambda: execute_kpi_recompute(
			day_spec,
			run_mode="incremental",
			trigger_reason=f"benchmark_daily_inc_{label}",
			as_of="2026-11-01 00:00:00",
			actor="Administrator",
		),
		measure_tracemalloc_separately=True,
	)
	schedule_post_commit_dirty_invalidation(
		work_item="BENCH-WI-000014",
		campaign=sample_campaign,
		owner_user=sample_staff,
		event_dt=datetime(2026, 10, 15, 10, 0, 0),
		reason="benchmark_incremental_dirty_day",
	)
	frappe.db.commit()
	drain_res, drain_mem = _measure_callable_with_memory(
		lambda: process_dirty_scopes(max_scopes=20, as_of="2026-11-01 00:00:00")
	)

	frappe.db.sql("ANALYZE TABLE `tabCSKH KPI Run`, `tabCSKH KPI Snapshot`")
	frappe.db.commit()

	# 3. Dashboard & Drill-down read latencies (`N=20` warm calls)
	frappe.set_user(bod_user)
	ep_overview = _benchmark_endpoint(
		lambda: p3_reporting.get_executive_overview(period_type="monthly", period_key=BENCH_MONTH_KEY)
	)
	ep_teams = _benchmark_endpoint(
		lambda: p3_reporting.get_team_comparison(period_type="monthly", period_key=BENCH_MONTH_KEY)
	)
	ep_employees = _benchmark_endpoint(
		lambda: p3_reporting.get_employee_quality(
			period_type="monthly",
			period_key=BENCH_MONTH_KEY,
			sort_by="PROCESS_SCORE",
			sort_dir="desc",
			page=1,
			page_size=20,
		)
	)
	ep_campaigns = _benchmark_endpoint(
		lambda: p3_reporting.get_campaign_quality(period_type="monthly", period_key=BENCH_MONTH_KEY)
	)
	ep_drill_daily = _benchmark_endpoint(
		lambda: p3_reporting.get_metric_drilldown(
			metric_code="FIRST_ATTEMPT_SLA_RATE",
			period_type="daily",
			period_key=BENCH_DAY_KEY,
			scope_type="company",
			page=1,
			page_size=20,
		)
	)

	frappe.set_user(sample_staff)
	ep_my_scorecard = _benchmark_endpoint(
		lambda: p3_reporting.get_my_scorecard(period_type="monthly", period_key=BENCH_MONTH_KEY)
	)

	frappe.set_user(admin_user)
	ep_drill_monthly_campaign = _benchmark_endpoint(
		lambda: p3_reporting.get_metric_drilldown(
			metric_code="FIRST_ATTEMPT_SLA_RATE",
			period_type="monthly",
			period_key=BENCH_MONTH_KEY,
			scope_type="campaign",
			scope_id=sample_campaign,
			page=1,
			page_size=20,
		),
		iterations=10,
	)

	frappe.set_user("Administrator")
	explains = _collect_explain_plans(sample_campaign)

	# Assert OBS-P2-02 guarantee: ZERO dashboard snapshot endpoints touch `tabCSKH Contact Attempt`!
	for dash_name, dash_stats in (
		("get_executive_overview", ep_overview),
		("get_team_comparison", ep_teams),
		("get_employee_quality", ep_employees),
		("get_campaign_quality", ep_campaigns),
		("get_my_scorecard", ep_my_scorecard),
	):
		if dash_stats["touches_contact_attempt_table"]:
			raise RuntimeError(
				f"OBS-P2-02 VIOLATION: {dash_name} queried tabCSKH Contact Attempt during dashboard read!"
			)

	return {
		"tier": label,
		"row_counts": counts,
		"recompute": {
			"monthly_full": {
				**full_mem,
				"snapshots_written": full_res["snapshots_written"],
				"reconciliation_ok": bool(full_res["reconciliation"]["ok"]),
			},
			"monthly_single_campaign": {
				**camp_mem,
				"snapshots_written": camp_res["snapshots_written"],
				"reconciliation_ok": bool(camp_res["reconciliation"]["ok"]),
			},
			"daily_single_day_incremental": {
				**day_mem,
				"snapshots_written": day_res["snapshots_written"],
				"reconciliation_ok": bool(day_res["reconciliation"]["ok"]),
			},
			"dirty_scope_multi_grain_drain": {
				**drain_mem,
				"periods_recomputed": len(drain_res["runs"]),
			},
		},
		"endpoints": {
			"get_executive_overview": ep_overview,
			"get_team_comparison": ep_teams,
			"get_employee_quality": ep_employees,
			"get_campaign_quality": ep_campaigns,
			"get_my_scorecard": ep_my_scorecard,
			"get_metric_drilldown_daily_company": ep_drill_daily,
			"get_metric_drilldown_monthly_campaign": ep_drill_monthly_campaign,
		},
		"explain_plans": explains,
	}


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Run P3 synthetic 10k & 100k Work Item benchmarks")
	parser.add_argument("--site", required=True, help="Isolated benchmark site name (e.g. p3bench.localhost)")
	parser.add_argument("--sites-path", default="/workspace/frappe-bench/sites", help="Bench sites directory")
	parser.add_argument("--out", required=True, help="Output JSON path for benchmark evidence")
	args = parser.parse_args(argv)

	if args.site in ("dev.localhost", "site1.local"):
		raise SystemExit(f"Refusing to run destructive synthetic benchmark on non-isolated site: {args.site}")

	os.environ.setdefault("FRAPPE_STREAM_LOGGING", "1")
	out_path = Path(args.out).resolve()
	os.chdir(args.sites_path)
	frappe.init(site=args.site, sites_path=".")
	frappe.connect()
	try:
		db_version = frappe.db.sql("SELECT VERSION()")[0][0]
		ctx = _setup_org_and_campaigns()

		t_start = time.perf_counter()
		print(">>> Seeding Tier 1: 0 -> 10,000 Work Items ...", flush=True)
		_seed_synthetic_range(0, 10000, ctx)
		tier_10k = _run_scale_tier("10k_work_items", ctx)

		print("\n>>> Extending to Tier 2: 10,000 -> 100,000 Work Items ...", flush=True)
		_seed_synthetic_range(10000, 100000, ctx)
		tier_100k = _run_scale_tier("100k_work_items", ctx)

		total_elapsed_s = round(time.perf_counter() - t_start, 2)
		report = {
			"schema_version": 1,
			"generated_at_utc": datetime.now(timezone.utc).isoformat(),
			"disclaimer": (
				"DEV single-host Docker synthetic benchmark on isolated site; numbers measure architectural "
				"scaling and index usage (OBS-P2-02) and are NOT production SLA commitments."
			),
			"environment": {
				"site": args.site,
				"python_version": sys.version.split()[0],
				"platform": platform.platform(),
				"cpu_count": os.cpu_count(),
				"mariadb_version": str(db_version),
				"frappe_version": getattr(frappe, "__version__", "unknown"),
				"total_benchmark_wall_seconds": total_elapsed_s,
			},
			"obs_p2_02_verified_zero_attempt_scans_on_dashboard": True,
			"tiers": {
				"10k": tier_10k,
				"100k": tier_100k,
			},
		}

		out_path.parent.mkdir(parents=True, exist_ok=True)
		out_path.write_text(
			json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
			encoding="utf-8",
			newline="\n",
		)
		print(f"\n[OK] Wrote P3 benchmark report to {out_path} in {total_elapsed_s}s.", flush=True)
		return 0
	finally:
		frappe.destroy()


if __name__ == "__main__":
	sys.exit(main())
