"""P2 §15.4 & §15.5 synthetic scale, index, pagination & 25-worker concurrency benchmark.

Executes on an isolated temporary benchmark site (`p2bench.localhost`) and records
only measured runtime numbers (no invented SLAs):

1. Sequential transactional service write micro-benchmark (`100` `record_attempt`,
   `40` `schedule_followup`, `20` `request_correction` + `review_correction` calls).
2. Synthetic scale dataset (`10,000` `CSKH Work Item`s + `50,000` `CSKH Contact Attempt`s,
   including a single Work Item with `120` attempts, `2,000` open callback `CRM Task`s,
   and `500` approved `CSKH Attempt Correction`s across `2` Teams / `25` Staff users).
3. Read-path & pagination latency + SQL query counts + payload sizes (`list_work_items`,
   `get_work_item_detail`, `list_attempts_timeline` on the `120`-attempt Work Item,
   `list_followups` for `overdue` / `today` / `upcoming`, `progress_summary` across
   `Staff` / `Leader` / `BOD` / `CRM Admin`).
4. MariaDB `EXPLAIN` query plans and `SHOW INDEX` inventory proving index selection on
   the `10k` / `50k` tables.
5. `25`-worker concurrent multi-connection workload (`ThreadPoolExecutor(max_workers=25)`)
   recording p50/p95/max latency, CPU user/sys time, peak RSS MiB, Python `tracemalloc`
   peak heap MiB, InnoDB row-lock waits/time, slow queries, and error count.
6. Full DB invariant post-check via `reconcile_p2_invariants()` (`ok == True`).

Usage:
    bench --site p2bench.localhost execute crm_cskh.tools.p2_benchmark.run_benchmark
"""

from __future__ import annotations

import json
import os
import re
import resource
import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from crm_cskh.patches.v2_0.p2_schema_and_seeds import (
	ensure_p2_custom_fields,
	ensure_p2_indexes,
	ensure_p2_seeds,
)
from crm_cskh.services import contact_attempt, correction, followup, ownership, read_model

WORK_ITEM_COUNT = int(os.environ.get("P2_BENCH_WORK_ITEMS", "10000"))
TARGET_ATTEMPT_COUNT = int(os.environ.get("P2_BENCH_ATTEMPTS", "50000"))
STAFF_COUNT = int(os.environ.get("P2_BENCH_STAFF_USERS", "25"))
OUTPUT_PATH = os.environ.get("P2_BENCH_OUTPUT", "/tmp/p2-benchmark-report.json")
PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")


def _log(msg: str) -> None:
	print(msg, flush=True)


def _rss_mb() -> float:
	return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)


def _cpu_times() -> tuple[float, float]:
	ru = resource.getrusage(resource.RUSAGE_SELF)
	return float(ru.ru_utime), float(ru.ru_stime)


def _db_status() -> dict[str, int]:
	rows = frappe.db.sql(
		"SHOW GLOBAL STATUS WHERE Variable_name IN %s",
		(
			(
				"Questions",
				"Queries",
				"Slow_queries",
				"Innodb_row_lock_waits",
				"Innodb_row_lock_time",
			),
		),
		as_dict=True,
	)
	return {r["Variable_name"]: int(r["Value"] or 0) for r in rows}


def _percentile(sorted_vals: list[float], pct: float) -> float:
	if not sorted_vals:
		return 0.0
	if len(sorted_vals) == 1:
		return round(sorted_vals[0], 3)
	idx = (len(sorted_vals) - 1) * (pct / 100.0)
	lo = int(idx)
	hi = min(lo + 1, len(sorted_vals) - 1)
	frac = idx - lo
	return round(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac, 3)


def _latency_summary_ms(samples_sec: list[float]) -> dict[str, float | int]:
	ms = sorted(s * 1000.0 for s in samples_sec)
	if not ms:
		return {"count": 0, "min_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
	return {
		"count": len(ms),
		"min_ms": round(ms[0], 3),
		"p50_ms": _percentile(ms, 50.0),
		"p95_ms": _percentile(ms, 95.0),
		"max_ms": round(ms[-1], 3),
		"mean_ms": round(statistics.fmean(ms), 3),
	}


def _measure_call(fn, repeats: int = 5) -> dict:
	"""Measure wall-clock latency across `repeats` runs and exact SQL query count on the last run."""
	latencies: list[float] = []
	last_result = None
	sql_count = 0
	orig_sql = frappe.db.sql

	for r_idx in range(repeats):
		if r_idx == repeats - 1:
			counter = {"n": 0}

			def counted_sql(*args, **kwargs):
				counter["n"] += 1
				return orig_sql(*args, **kwargs)

			frappe.db.sql = counted_sql
			try:
				t0 = time.perf_counter()
				last_result = fn()
				latencies.append(time.perf_counter() - t0)
			finally:
				frappe.db.sql = orig_sql
			sql_count = counter["n"]
		else:
			t0 = time.perf_counter()
			last_result = fn()
			latencies.append(time.perf_counter() - t0)

	serialized = json.dumps(last_result, ensure_ascii=False, default=str).encode("utf-8")
	return {
		"latency": _latency_summary_ms(latencies),
		"sql_queries_per_call": sql_count,
		"payload_bytes": len(serialized),
	}


def _sanitize_payload(obj):
	if isinstance(obj, dict):
		return {str(k): _sanitize_payload(v) for k, v in obj.items()}
	if isinstance(obj, list):
		return [_sanitize_payload(v) for v in obj]
	if isinstance(obj, str):
		return PHONE_RE.sub("[REDACTED_PHONE]", obj)
	return obj


def _ensure_roles_and_actors() -> dict:
	frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
	for role_name in ("Sales User", "Sales Manager", "CSKH CRM Admin", "CSKH BOD Viewer"):
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)

	def ensure_user(email: str, first_name: str, roles: tuple[str, ...]) -> str:
		if not frappe.db.exists("User", email):
			doc = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": first_name,
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
				}
			)
			doc.insert(ignore_permissions=True)
		else:
			doc = frappe.get_doc("User", email)
		existing = {r.role for r in doc.roles}
		missing = [r for r in roles if r not in existing]
		if missing:
			doc.add_roles(*missing)
		return email

	admin = ensure_user("p2bench.admin@example.test", "Admin Bench", ("CSKH CRM Admin", "Sales User"))
	bod = ensure_user("p2bench.bod@example.test", "BOD Bench", ("CSKH BOD Viewer",))
	leader_a = ensure_user("p2bench.leader1@example.test", "Leader 1 Bench", ("Sales Manager", "Sales User"))
	leader_b = ensure_user("p2bench.leader2@example.test", "Leader 2 Bench", ("Sales Manager", "Sales User"))

	staff_users = [
		ensure_user(f"p2bench.staff{i:02d}@example.test", f"Staff {i:02d}", ("Sales User",))
		for i in range(STAFF_COUNT)
	]

	def ensure_node(user: str, full_name: str, parent: str | None, is_group: int) -> str:
		existing = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, "name")
		if existing:
			return str(existing)
		payload = {
			"doctype": "CRM Sales Hierarchy",
			"full_name": full_name,
			"user": user,
			"is_group": is_group,
		}
		if parent:
			payload["reports_to"] = parent
		doc = frappe.get_doc(payload)
		doc.insert(ignore_permissions=True)
		return str(doc.name)

	root = ensure_node(admin, "Root Bench", None, 1)
	team_a = ensure_node(leader_a, "Team A Bench", root, 1)
	team_b = ensure_node(leader_b, "Team B Bench", root, 1)
	split = max(1, STAFF_COUNT // 2)
	for i, u in enumerate(staff_users[:split]):
		ensure_node(u, f"Staff A{i:02d} Bench", team_a, 0)
	for i, u in enumerate(staff_users[split:]):
		ensure_node(u, f"Staff B{i:02d} Bench", team_b, 0)

	frappe.db.commit()
	return {
		"admin": admin,
		"bod": bod,
		"leader_a": leader_a,
		"leader_b": leader_b,
		"team_a": team_a,
		"team_b": team_b,
		"staff_a_list": staff_users[:split],
		"staff_b_list": staff_users[split:],
		"all_staff": staff_users,
	}


def _run_service_write_microbench(actors: dict, camp_a: str) -> dict:
	"""Benchmark real transactional service writes (`record_attempt`, `schedule_followup`, `correction`)."""
	staff0 = actors["staff_a_list"][0]
	leader_a = actors["leader_a"]
	wis: list[str] = []

	for idx in range(25):
		phone = f"0971{idx:06d}"
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": f"Khách Micro {idx}",
				"lead_name": f"Khách Micro {idx}",
				"mobile_no": phone,
				"status": "New",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "CSKH Identity Index",
				"normalized_phone": phone,
				"canonical_lead": lead.name,
			}
		).insert(ignore_permissions=True)
		wi = frappe.get_doc(
			{
				"doctype": "CSKH Work Item",
				"campaign": camp_a,
				"lead": lead.name,
				"status": "queued",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead.name,
				"campaign": camp_a,
				"work_item": wi.name,
			}
		).insert(ignore_permissions=True)
		ownership.assign(
			wi.name,
			staff0,
			actor=actors["admin"],
			reason="Microbench assign",
			request_id=f"mb-asg-{idx}",
		)
		wis.append(wi.name)
	frappe.db.commit()

	att_lat: list[float] = []
	att_ids: list[str] = []
	for idx in range(100):
		wi_name = wis[idx % len(wis)]
		code = "connected_interested" if idx % 4 == 3 else "no_answer"
		t0 = time.perf_counter()
		res = contact_attempt.record_attempt(
			work_item=wi_name,
			channel="phone_call",
			outcome_code=code,
			note=f"Ghi nhận thực tế #{idx}",
			idempotency_key=f"mb-att-{idx}",
			actor=staff0,
		)
		frappe.db.commit()
		att_lat.append(time.perf_counter() - t0)
		att_ids.append(res["attempt_id"])

	fu_lat: list[float] = []
	base_now = now_datetime()
	for idx in range(40):
		wi_name = wis[idx % 20]
		due_str = str(add_to_date(base_now, hours=3 + (idx // 20) * 2, minutes=idx))
		t0 = time.perf_counter()
		followup.schedule_followup(
			work_item=wi_name,
			due_at=due_str,
			title=f"Callback micro #{idx}",
			reason="Dời giờ hẹn" if idx >= 20 else None,
			idempotency_key=f"mb-fu-{idx}",
			actor=staff0,
		)
		frappe.db.commit()
		fu_lat.append(time.perf_counter() - t0)

	corr_lat: list[float] = []
	for idx in range(20):
		t0 = time.perf_counter()
		req = correction.request_correction(
			attempt=att_ids[idx],
			action="correct",
			corrected_fields={"note": f"Đã đính chính #{idx}"},
			reason="Bổ sung chi tiết thực tế",
			idempotency_key=f"mb-creq-{idx}",
			actor=staff0,
		)
		correction.review_correction(
			correction_id=req["correction_id"],
			decision="approve",
			review_note="Trưởng nhóm duyệt",
			idempotency_key=f"mb-crev-{idx}",
			actor=leader_a,
		)
		frappe.db.commit()
		corr_lat.append(time.perf_counter() - t0)

	return {
		"work_items_seeded": len(wis),
		"record_attempt_100": _latency_summary_ms(att_lat),
		"schedule_or_reschedule_followup_40": _latency_summary_ms(fu_lat),
		"request_and_approve_correction_20": _latency_summary_ms(corr_lat),
	}


def _bulk_seed_10k_50k(actors: dict, camp_a: str, camp_b: str) -> dict:
	"""Populate `10,000` Work Items + `50,000` Contact Attempts + `2,000` Tasks + `500` Corrections."""
	t_start = time.perf_counter()
	now_dt = get_datetime(now_datetime()).replace(microsecond=0)
	assign_dt = add_to_date(now_dt, days=-5)
	pol_doc = frappe.db.get_value(
		"CSKH Policy", {"policy_code": "GLOBAL-DEFAULT"}, ["name", "version"], as_dict=True
	)
	pol_name = pol_doc.name if pol_doc else "GLOBAL-DEFAULT"
	pol_ver = int(pol_doc.version or 1) if pol_doc else 1

	existing_wis = frappe.db.count("CSKH Work Item")
	existing_atts = frappe.db.count("CSKH Contact Attempt")
	rem_wis = max(0, WORK_ITEM_COUNT - existing_wis)
	rem_atts = max(0, TARGET_ATTEMPT_COUNT - existing_atts)

	# Work Item #0 gets `120` attempts (`P2.md` §15.4 timeline 100+ pagination test).
	# Distribute the remaining attempts across Work Items `1 .. rem_wis-1`.
	deep_wi_attempts = 120

	staff_a = actors["staff_a_list"]
	staff_b = actors["staff_b_list"]

	# State distribution across `rem_wis`:
	# - `0`: `in_progress` with `120` attempts (`Staff 00`, `camp_a`)
	# - `1 .. 2000`: `on_hold` with open callback `CRM Task` (`650` overdue, `650` due today later, `700` upcoming tomorrow)
	# - `2001 .. 3000`: `closed` (`ownership_end = closed_at`, no `CSKH Active Claim`)
	# - `3001 .. 4000`: `assigned` (`0` attempts — compensate their attempts onto `4001 .. 6000` so total attempts == `50,000`!)
	# Let's compute exact per-WI attempt count cleanly so total == `rem_atts`!
	uncontacted_start, uncontacted_end = 3001, 4000
	contacted_other_wis = [i for i in range(1, rem_wis) if not (uncontacted_start <= i <= uncontacted_end)]
	per_wi_att_count = {0: deep_wi_attempts}
	for i in range(uncontacted_start, min(uncontacted_end + 1, rem_wis)):
		per_wi_att_count[i] = 0
	rem_after_deep = rem_atts - deep_wi_attempts
	q_att, r_att = divmod(rem_after_deep, len(contacted_other_wis))
	for pos, wi_idx in enumerate(contacted_other_wis):
		per_wi_att_count[wi_idx] = q_att + (1 if pos < r_att else 0)

	# Due timestamps for the `2,000` `on_hold` callback tasks:
	overdue_due = add_to_date(now_dt, hours=-3)
	today_Or_soon_due = min(add_to_date(now_dt, minutes=20), now_dt.replace(hour=23, minute=58, second=0))
	upcoming_due = add_to_date(now_dt, days=2)

	deep_wi_name = "P2B-WI-00000"
	callback_count = 0
	correction_count = 0
	inserted_atts = 0

	chunk_size = 500
	for chunk_base in range(0, rem_wis, chunk_size):
		chunk_end = min(rem_wis, chunk_base + chunk_size)
		lead_rows = []
		ident_rows = []
		wi_rows = []
		claim_rows = []
		hist_rows = []
		task_rows = []
		att_rows = []
		corr_rows = []

		for idx in range(chunk_base, chunk_end):
			lead_name = f"P2B-LEAD-{idx:05d}"
			phone = f"098{idx + 1000000:07d}"
			wi_name = f"P2B-WI-{idx:05d}"
			hist_name = f"P2B-HIST-{idx:05d}"

			in_team_a = idx % 2 == 0
			camp = camp_a if in_team_a else camp_b
			staff_pool = staff_a if in_team_a else staff_b
			owner = staff_pool[(idx // 2) % len(staff_pool)]

			n_atts = per_wi_att_count[idx]
			is_on_hold = 1 <= idx <= 2000
			is_closed = 2001 <= idx <= 3000

			if is_on_hold:
				wi_status = "on_hold"
			elif is_closed:
				wi_status = "closed"
			elif n_atts == 0:
				wi_status = "assigned"
			else:
				wi_status = "in_progress"

			closed_at = add_to_date(now_dt, hours=-1) if is_closed else None
			ownership_end = closed_at if is_closed else None

			task_id = None
			due_dt = None
			if is_on_hold:
				task_num = 100000 + idx
				task_id = str(task_num)
				if idx <= 650:
					due_dt = overdue_due
				elif idx <= 1300:
					due_dt = today_Or_soon_due
				else:
					due_dt = upcoming_due
				callback_count += 1
				task_rows.append(
					(
						task_num,
						now_dt,
						now_dt,
						owner,
						owner,
						f"Hẹn gọi lại khách #{idx}",
						"Todo",
						"Medium",
						due_dt,
						owner,
						"CRM Lead",
						lead_name,
						1,
						wi_name,
						0,
					)
				)

			# Build `n_atts` attempts for `wi_name`
			void_first_att = (4001 <= idx <= 4250) and n_atts >= 2
			edit_first_att = (4251 <= idx <= 4500) and n_atts >= 2

			first_eff_dt = None
			last_eff_dt = None
			last_conn_dt = None
			last_out_code = None
			last_out_label = None
			eff_cnt = 0
			conn_cnt = 0

			for a_pos in range(n_atts):
				att_name = f"P2B-ATT-{idx:05d}-{a_pos:03d}"
				occ_dt = add_to_date(assign_dt, minutes=10 + a_pos * 5)
				is_last = a_pos == n_atts - 1
				if is_last and (idx % 3 == 0):
					code = "connected_interested"
					label = "Đã trao đổi — Quan tâm"
					grp = "positive"
					is_conn = 1
					is_int = 1
					ch = "phone_call"
				elif a_pos % 4 == 2:
					code = "message_sent_no_reply"
					label = "Đã gửi tin nhắn, chưa phản hồi"
					grp = "unreached"
					is_conn = 0
					is_int = 0
					ch = "zalo"
				else:
					code = "no_answer"
					label = "Không nghe máy"
					grp = "unreached"
					is_conn = 0
					is_int = 0
					ch = "phone_call"

				cb_link = task_id if (is_on_hold and is_last) else None
				att_rows.append(
					(
						att_name,
						occ_dt,
						occ_dt,
						owner,
						owner,
						wi_name,
						lead_name,
						camp,
						hist_name,
						owner,
						owner,
						occ_dt,
						occ_dt,
						ch,
						code,
						label,
						grp,
						1,
						1,
						is_conn,
						is_int,
						0,
						0,
						f"Nhật ký tổng hợp #{a_pos + 1}",
						cb_link,
						"self_reported",
						pol_name,
						pol_ver,
						f"p2b-idem-{idx}-{a_pos}",
						f"fp-{idx}-{a_pos}",
						a_pos + 2,
					)
				)
				inserted_atts += 1

				is_voided_this = void_first_att and a_pos == 0
				if is_voided_this:
					correction_count += 1
					corr_rows.append(
						(
							f"P2B-CORR-{idx:05d}",
							occ_dt,
							occ_dt,
							owner,
							actors["admin"],
							att_name,
							wi_name,
							lead_name,
							camp,
							"void",
							"approved",
							owner,
							occ_dt,
							"Hủy dòng ghi nhầm trong đợt đối soát",
							"{}",
							"{}",
							actors["admin"],
							add_to_date(occ_dt, minutes=1),
							"Đã duyệt hủy",
							f"p2b-cidem-{idx}",
							f"cfp-{idx}",
							f"p2b-ridem-{idx}",
							n_atts + 2,
						)
					)
				elif edit_first_att and a_pos == 0:
					correction_count += 1
					corr_rows.append(
						(
							f"P2B-CORR-{idx:05d}",
							occ_dt,
							occ_dt,
							owner,
							actors["admin"],
							att_name,
							wi_name,
							lead_name,
							camp,
							"correct",
							"approved",
							owner,
							occ_dt,
							"Bổ sung nội dung trao đổi",
							"{}",
							json.dumps({"note": "Ghi chú đã hiệu chỉnh hợp lệ"}, ensure_ascii=False),
							actors["admin"],
							add_to_date(occ_dt, minutes=1),
							"Đã duyệt bổ sung",
							f"p2b-cidem-{idx}",
							f"cfp-{idx}",
							f"p2b-ridem-{idx}",
							n_atts + 2,
						)
					)

				if not is_voided_this:
					eff_cnt += 1
					if first_eff_dt is None:
						first_eff_dt = occ_dt
					last_eff_dt = occ_dt
					last_out_code = code
					last_out_label = label
					if is_conn:
						conn_cnt += 1
						last_conn_dt = occ_dt

			lead_rows.append(
				(
					lead_name,
					assign_dt,
					assign_dt,
					"Administrator",
					"Administrator",
					f"Khách Bench {idx:05d}",
					f"Khách Bench {idx:05d}",
					phone,
					"New",
					None if is_closed else owner,
					0,
				)
			)
			ident_rows.append(
				(
					f"P2B-ID-{idx:05d}",
					assign_dt,
					assign_dt,
					"Administrator",
					"Administrator",
					phone,
					lead_name,
				)
			)
			wi_rows.append(
				(
					wi_name,
					assign_dt,
					now_dt,
					"Administrator",
					"Administrator",
					camp,
					lead_name,
					wi_status,
					None if is_closed else owner,
					assign_dt,
					eff_cnt + 1,
					eff_cnt,
					conn_cnt,
					first_eff_dt,
					last_eff_dt,
					last_out_code,
					last_out_label,
					last_conn_dt,
					task_id,
					due_dt,
					closed_at,
					owner if is_closed else None,
					"Hoàn tất chiến dịch mẫu" if is_closed else None,
					pol_ver,
				)
			)
			if not is_closed:
				claim_rows.append(
					(
						f"P2B-CLM-{idx:05d}",
						assign_dt,
						assign_dt,
						"Administrator",
						"Administrator",
						lead_name,
						camp,
						wi_name,
					)
				)
			hist_rows.append(
				(
					hist_name,
					assign_dt,
					assign_dt,
					"Administrator",
					"Administrator",
					wi_name,
					"assign",
					owner,
					actors["admin"],
					assign_dt,
					"Seed benchmark P2",
					assign_dt,
					ownership_end,
					f"p2b-ev-{idx:05d}",
				)
			)

		def _bulk_insert(sql_prefix: str, rows: list[tuple], sub_batch: int = 1000) -> None:
			if not rows:
				return
			ncols = len(rows[0])
			row_ph = "(" + ",".join(["%s"] * ncols) + ")"
			for s in range(0, len(rows), sub_batch):
				part = rows[s : s + sub_batch]
				ph = ",".join([row_ph] * len(part))
				flat = [val for tup in part for val in tup]
				frappe.db.sql(f"{sql_prefix} VALUES {ph}", flat)

		_bulk_insert(
			"INSERT INTO `tabCRM Lead` (`name`, `creation`, `modified`, `owner`, `modified_by`, `first_name`, `lead_name`, `mobile_no`, `status`, `lead_owner`, `cskh_do_not_contact`)",
			lead_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCSKH Identity Index` (`name`, `creation`, `modified`, `owner`, `modified_by`, `normalized_phone`, `canonical_lead`)",
			ident_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCSKH Work Item` (`name`, `creation`, `modified`, `owner`, `modified_by`, `campaign`, `lead`, `status`, `assigned_to`, `assigned_at`, `revision`, `attempt_count`, `connected_attempt_count`, `first_attempt_at`, `last_attempt_at`, `last_outcome_code`, `last_outcome_label`, `last_connected_at`, `open_callback_task`, `next_action_due`, `closed_at`, `closed_by`, `completion_reason`, `policy_version`)",
			wi_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCSKH Active Claim` (`name`, `creation`, `modified`, `owner`, `modified_by`, `lead`, `campaign`, `work_item`)",
			claim_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCSKH Assignment History` (`name`, `creation`, `modified`, `owner`, `modified_by`, `work_item`, `action`, `to_user`, `actor`, `event_at`, `reason`, `ownership_start`, `ownership_end`, `event_id`)",
			hist_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCRM Task` (`name`, `creation`, `modified`, `owner`, `modified_by`, `title`, `status`, `priority`, `due_date`, `assigned_to`, `reference_doctype`, `reference_docname`, `cskh_is_followup`, `cskh_work_item`, `cskh_reschedule_count`)",
			task_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCSKH Contact Attempt` (`name`, `creation`, `modified`, `owner`, `modified_by`, `work_item`, `lead`, `campaign`, `ownership_event`, `owner_at_attempt`, `performed_by`, `occurred_at`, `recorded_at`, `channel`, `outcome_code`, `outcome_label_snapshot`, `outcome_group_snapshot`, `outcome_version_snapshot`, `contact_attempt_snapshot`, `connected_snapshot`, `interested_snapshot`, `terminal_snapshot`, `dnc_snapshot`, `note`, `callback_task`, `verification_status`, `policy_name_snapshot`, `policy_version_snapshot`, `idempotency_key`, `payload_hash`, `work_item_revision`)",
			att_rows,
		)
		_bulk_insert(
			"INSERT INTO `tabCSKH Attempt Correction` (`name`, `creation`, `modified`, `owner`, `modified_by`, `attempt`, `work_item`, `lead`, `campaign`, `action`, `status`, `requested_by`, `requested_at`, `reason`, `original_snapshot_json`, `corrected_fields_json`, `reviewed_by`, `reviewed_at`, `review_note`, `idempotency_key`, `payload_hash`, `review_idempotency_key`, `applied_revision`)",
			corr_rows,
		)
		frappe.db.commit()

	# Refresh InnoDB optimizer statistics so EXPLAIN and queries see accurate 10k/50k cardinality!
	for tbl in (
		"tabCRM Lead",
		"tabCSKH Work Item",
		"tabCSKH Contact Attempt",
		"tabCSKH Attempt Correction",
		"tabCRM Task",
	):
		frappe.db.sql(f"ANALYZE TABLE `{tbl}`")
	frappe.db.commit()

	return {
		"seed_wall_seconds": round(time.perf_counter() - t_start, 3),
		"total_work_items": frappe.db.count("CSKH Work Item"),
		"total_contact_attempts": frappe.db.count("CSKH Contact Attempt"),
		"total_open_callback_tasks": frappe.db.count(
			"CRM Task", {"cskh_is_followup": 1, "status": ["in", ["Backlog", "Todo", "In Progress"]]}
		),
		"total_approved_corrections": frappe.db.count("CSKH Attempt Correction", {"status": "approved"}),
		"deep_timeline_work_item": deep_wi_name,
		"deep_timeline_attempt_count": frappe.db.count("CSKH Contact Attempt", {"work_item": deep_wi_name}),
	}


def _explain_plans(actors: dict, camp_a: str, deep_wi: str) -> dict:
	staff0 = actors["staff_a_list"][0]
	now_dt = now_datetime()
	queries = {
		"work_item_queue_by_assignee_status_due": (
			"EXPLAIN SELECT name, status, next_action_due FROM `tabCSKH Work Item` "
			"WHERE assigned_to = %s AND status IN ('assigned', 'in_progress', 'on_hold') "
			"ORDER BY next_action_due ASC LIMIT 20",
			(staff0,),
		),
		"attempt_timeline_by_work_item_occurred": (
			"EXPLAIN SELECT name, occurred_at, outcome_code FROM `tabCSKH Contact Attempt` "
			"WHERE work_item = %s ORDER BY occurred_at ASC",
			(deep_wi,),
		),
		"attempt_summary_by_campaign_occurred": (
			"EXPLAIN SELECT name, outcome_group_snapshot, connected_snapshot FROM `tabCSKH Contact Attempt` "
			"WHERE campaign = %s AND occurred_at >= %s",
			(camp_a, add_to_date(now_dt, days=-7)),
		),
		"approved_corrections_by_attempt_status": (
			"EXPLAIN SELECT attempt, action FROM `tabCSKH Attempt Correction` "
			"WHERE attempt = %s AND status = 'approved'",
			("P2B-ATT-04001-000",),
		),
		"followup_tasks_by_flag_status_due": (
			"EXPLAIN SELECT name, cskh_work_item, due_date FROM `tabCRM Task` "
			"WHERE cskh_is_followup = 1 AND status = 'Todo' AND due_date < %s "
			"ORDER BY due_date ASC LIMIT 20",
			(now_dt,),
		),
	}
	plans = {}
	for label, (sql, params) in queries.items():
		rows = frappe.db.sql(sql, params, as_dict=True)
		plans[label] = [
			{
				"table": r.get("table"),
				"type": r.get("type"),
				"possible_keys": r.get("possible_keys"),
				"key": r.get("key"),
				"rows": r.get("rows"),
				"Extra": r.get("Extra"),
			}
			for r in rows
		]
	return plans


def _index_inventory() -> dict:
	inv = {}
	for table in (
		"tabCSKH Outcome Code",
		"tabCSKH Policy",
		"tabCSKH Contact Attempt",
		"tabCSKH Attempt Correction",
		"tabCSKH Work Item",
		"tabCRM Task",
	):
		rows = frappe.db.sql(f"SHOW INDEX FROM `{table}`", as_dict=True)
		inv[table] = [
			{
				"key_name": r.get("Key_name"),
				"column_name": r.get("Column_name"),
				"seq_in_index": r.get("Seq_in_index"),
				"unique": not bool(r.get("Non_unique")),
			}
			for r in rows
		]
	return inv


def _run_25_worker_concurrent_load(actors: dict, camp_a: str, camp_b: str) -> dict:
	"""Simulate `25` concurrent Staff users writing attempts/followups and reading queues/timelines."""
	site = frappe.local.site
	sites_path = getattr(frappe.local, "sites_path", ".")
	all_staff = actors["all_staff"]

	# Pick one active `in_progress` Work Item per Staff user so all 25 workers run concurrently on real data!
	target_by_staff: dict[str, str] = {}
	for u in all_staff:
		wi_name = frappe.db.get_value(
			"CSKH Work Item",
			{"assigned_to": u, "status": "in_progress"},
			"name",
		)
		if wi_name:
			target_by_staff[u] = str(wi_name)
	frappe.db.commit()

	db_before = _db_status()
	u_before, s_before = _cpu_times()
	rss_before = _rss_mb()
	t_wall_0 = time.perf_counter()

	def worker_job(worker_idx: int, staff_email: str, wi_name: str) -> dict:
		op_lat: dict[str, float] = {}
		errors: list[str] = []
		frappe.init(site=site, sites_path=sites_path, force=True)
		frappe.connect()
		try:
			frappe.set_user(staff_email)
			t0 = time.perf_counter()
			read_model.list_work_items(page=1, page_size=20, actor=staff_email)
			op_lat["list_work_items"] = time.perf_counter() - t0

			t0 = time.perf_counter()
			contact_attempt.record_attempt(
				work_item=wi_name,
				channel="phone_call",
				outcome_code="connected_needs_followup",
				note=f"Tải đồng thời 25 nhân viên #{worker_idx}",
				followup={
					"due_at": str(add_to_date(now_datetime(), hours=4, minutes=worker_idx)),
					"title": f"Callback tải đồng thời #{worker_idx}",
				},
				idempotency_key=f"conc25-att-{worker_idx}",
				actor=staff_email,
			)
			frappe.db.commit()
			op_lat["record_attempt_with_followup"] = time.perf_counter() - t0

			t0 = time.perf_counter()
			read_model.list_attempts_timeline(wi_name, page=1, page_size=20, actor=staff_email)
			op_lat["list_attempts_timeline"] = time.perf_counter() - t0

			t0 = time.perf_counter()
			followup.list_followups(bucket="all", page=1, page_size=20, actor=staff_email)
			op_lat["list_followups"] = time.perf_counter() - t0
		except Exception as exc:
			try:
				frappe.db.rollback()
			except Exception:
				pass
			errors.append(f"{type(exc).__name__}: {exc}")
		finally:
			try:
				frappe.destroy()
			except Exception:
				pass
		return {"latencies": op_lat, "errors": errors}

	per_op_samples: dict[str, list[float]] = {
		"list_work_items": [],
		"record_attempt_with_followup": [],
		"list_attempts_timeline": [],
		"list_followups": [],
	}
	all_errors: list[str] = []

	with ThreadPoolExecutor(max_workers=len(target_by_staff)) as pool:
		futs = [pool.submit(worker_job, i, u, wi) for i, (u, wi) in enumerate(target_by_staff.items())]
		for fut in as_completed(futs):
			res = fut.result()
			all_errors.extend(res["errors"])
			for k, val in res["latencies"].items():
				per_op_samples[k].append(val)

	wall_sec = time.perf_counter() - t_wall_0
	# Re-init main thread connection after worker threads finished
	frappe.init(site=site, sites_path=sites_path, force=True)
	frappe.connect()
	frappe.set_user("Administrator")

	db_after = _db_status()
	u_after, s_after = _cpu_times()

	return {
		"concurrent_workers": len(target_by_staff),
		"total_operations": sum(len(v) for v in per_op_samples.values()),
		"error_count": len(all_errors),
		"errors": all_errors[:10],
		"wall_seconds": round(wall_sec, 3),
		"cpu_user_seconds_delta": round(u_after - u_before, 3),
		"cpu_system_seconds_delta": round(s_after - s_before, 3),
		"rss_before_mb": rss_before,
		"peak_rss_mb": _rss_mb(),
		"per_operation_latency": {k: _latency_summary_ms(v) for k, v in per_op_samples.items()},
		"database_deltas": {
			"questions_delta": db_after.get("Questions", 0) - db_before.get("Questions", 0),
			"queries_delta": db_after.get("Queries", 0) - db_before.get("Queries", 0),
			"slow_queries_delta": db_after.get("Slow_queries", 0) - db_before.get("Slow_queries", 0),
			"innodb_row_lock_waits_delta": db_after.get("Innodb_row_lock_waits", 0)
			- db_before.get("Innodb_row_lock_waits", 0),
			"innodb_row_lock_time_ms_delta": db_after.get("Innodb_row_lock_time", 0)
			- db_before.get("Innodb_row_lock_time", 0),
		},
	}


def run_benchmark() -> dict:
	"""Execute full P2 §15.4 & §15.5 benchmark and write sanitized JSON report."""
	tracemalloc.start()
	t_total_0 = time.perf_counter()
	frappe.set_user("Administrator")

	ensure_p2_custom_fields()
	ensure_p2_indexes()
	ensure_p2_seeds()
	frappe.db.commit()

	rss_start = _rss_mb()
	db_start = _db_status()
	u_start, s_start = _cpu_times()

	_log("[p2-bench] 1/6 Setting up 25 Staff users + 2 Teams + 2 Campaigns...")
	actors = _ensure_roles_and_actors()
	camp_a = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": f"P2B-CA-{frappe.generate_hash(length=6)}",
			"title": "Chiến dịch Benchmark Team A",
			"status": "Active",
			"team_scope": actors["team_a"],
		}
	).insert(ignore_permissions=True)
	camp_b = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": f"P2B-CB-{frappe.generate_hash(length=6)}",
			"title": "Chiến dịch Benchmark Team B",
			"status": "Active",
			"team_scope": actors["team_b"],
		}
	).insert(ignore_permissions=True)
	for u in actors["staff_a_list"]:
		frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": camp_a.name,
				"user": u,
				"team": actors["team_a"],
				"weight": 100,
				"capacity_limit": 0,
				"version": 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	for u in actors["staff_b_list"]:
		frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": camp_b.name,
				"user": u,
				"team": actors["team_b"],
				"weight": 100,
				"capacity_limit": 0,
				"version": 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()

	_log("[p2-bench] 2/6 Measuring real transactional service write latencies...")
	write_micro = _run_service_write_microbench(actors, camp_a.name)

	_log(f"[p2-bench] 3/6 Seeding {WORK_ITEM_COUNT} Work Items + {TARGET_ATTEMPT_COUNT} Contact Attempts...")
	dataset = _bulk_seed_10k_50k(actors, camp_a.name, camp_b.name)
	deep_wi = dataset["deep_timeline_work_item"]
	staff0 = actors["staff_a_list"][0]

	_log("[p2-bench] 4/6 Measuring read, filter, pagination & progress_summary latencies on 10k/50k DB...")
	read_benchmarks = {
		"staff_list_work_items_default_p1": _measure_call(
			lambda: read_model.list_work_items(page=1, page_size=20, actor=staff0)
		),
		"staff_list_work_items_overdue": _measure_call(
			lambda: read_model.list_work_items(followup_filter="overdue", page=1, page_size=20, actor=staff0)
		),
		"leader_list_work_items_in_progress": _measure_call(
			lambda: read_model.list_work_items(
				status="in_progress", page=1, page_size=20, actor=actors["leader_a"]
			)
		),
		"admin_list_work_items_due_today": _measure_call(
			lambda: read_model.list_work_items(
				followup_filter="today", page=1, page_size=20, actor=actors["admin"]
			)
		),
		"staff_get_work_item_detail": _measure_call(
			lambda: read_model.get_work_item_detail(deep_wi, actor=staff0)
		),
		"timeline_120_attempts_page_1_size_20": _measure_call(
			lambda: read_model.list_attempts_timeline(deep_wi, page=1, page_size=20, actor=staff0)
		),
		"timeline_120_attempts_page_6_size_20": _measure_call(
			lambda: read_model.list_attempts_timeline(deep_wi, page=6, page_size=20, actor=staff0)
		),
		"leader_list_followups_overdue": _measure_call(
			lambda: followup.list_followups(bucket="overdue", page=1, page_size=20, actor=actors["leader_a"])
		),
		"leader_list_followups_today": _measure_call(
			lambda: followup.list_followups(bucket="today", page=1, page_size=20, actor=actors["leader_a"])
		),
		"staff_progress_summary": _measure_call(lambda: read_model.progress_summary(actor=staff0), repeats=3),
		"leader_team_progress_summary_25k_attempts": _measure_call(
			lambda: read_model.progress_summary(actor=actors["leader_a"]), repeats=3
		),
		"bod_system_progress_summary_50k_attempts": _measure_call(
			lambda: read_model.progress_summary(actor=actors["bod"]), repeats=3
		),
	}

	explain = _explain_plans(actors, camp_a.name, deep_wi)
	indexes = _index_inventory()

	_log("[p2-bench] 5/6 Running 25-worker concurrent load against 10k/50k MariaDB...")
	concurrency_25 = _run_25_worker_concurrent_load(actors, camp_a.name, camp_b.name)

	_log("[p2-bench] 6/6 Running full reconcile_p2_invariants across all 10k Work Items + 50k+ Attempts...")
	t_rec = time.perf_counter()
	reconcile_res = read_model.reconcile_p2_invariants()
	reconcile_sec = round(time.perf_counter() - t_rec, 3)

	_, py_peak_bytes = tracemalloc.get_traced_memory()
	tracemalloc.stop()
	db_end = _db_status()
	u_end, s_end = _cpu_times()

	report = _sanitize_payload(
		{
			"benchmark_scope": "P2.md §15.4 & §15.5 synthetic scale, pagination, index & 25-worker load",
			"sla_disclaimer": "Recorded numbers are measured DEV container baselines only; no artificial SLA threshold is invented.",
			"site": frappe.local.site,
			"recorded_at": str(now_datetime()),
			"total_wall_seconds": round(time.perf_counter() - t_total_0, 3),
			"dataset": dataset,
			"service_write_microbenchmark": write_micro,
			"read_and_pagination_benchmarks": read_benchmarks,
			"concurrency_25_workers": concurrency_25,
			"explain_plans": explain,
			"reconciliation_postcheck": {
				"ok": bool(reconcile_res["ok"]),
				"reconcile_seconds": reconcile_sec,
				"checked_work_items_with_attempts": reconcile_res["checked_work_items_with_attempts"],
				"orphan_claims_count": len(reconcile_res["orphan_claims"]),
				"duplicate_owned_leads_count": len(reconcile_res["duplicate_owned_leads"]),
				"multi_open_callbacks_count": len(reconcile_res["multi_open_callbacks"]),
				"callback_status_mismatches_count": len(reconcile_res["callback_status_mismatches"]),
				"ownership_mismatches_count": len(reconcile_res["ownership_mismatches"]),
				"due_projection_mismatches_count": len(reconcile_res["due_projection_mismatches"]),
				"duplicate_call_log_links_count": len(reconcile_res["duplicate_call_log_links"]),
				"interval_violations_count": len(reconcile_res["interval_violations"]),
				"counter_drifts_count": len(reconcile_res["counter_drifts"]),
			},
			"resources_overall": {
				"rss_start_mb": rss_start,
				"peak_rss_mb": _rss_mb(),
				"python_tracemalloc_peak_mb": round(py_peak_bytes / (1024.0 * 1024.0), 2),
				"cpu_user_seconds_total": round(u_end - u_start, 3),
				"cpu_system_seconds_total": round(s_end - s_start, 3),
				"questions_delta": db_end.get("Questions", 0) - db_start.get("Questions", 0),
				"queries_delta": db_end.get("Queries", 0) - db_start.get("Queries", 0),
				"slow_queries_delta": db_end.get("Slow_queries", 0) - db_start.get("Slow_queries", 0),
				"innodb_row_lock_waits_delta": db_end.get("Innodb_row_lock_waits", 0)
				- db_start.get("Innodb_row_lock_waits", 0),
				"innodb_row_lock_time_ms_delta": db_end.get("Innodb_row_lock_time", 0)
				- db_start.get("Innodb_row_lock_time", 0),
			},
			"indexes": indexes,
		}
	)

	payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
	with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
		handle.write(payload + "\n")
	_log(f"[p2-bench] Report written to {OUTPUT_PATH} (reconcile ok={reconcile_res['ok']})")
	if not reconcile_res["ok"] or concurrency_25["error_count"] > 0:
		raise RuntimeError(
			f"Benchmark invariant or concurrency check failed: reconcile_ok={reconcile_res['ok']}, errors={concurrency_25['errors']}"
		)
	return report
