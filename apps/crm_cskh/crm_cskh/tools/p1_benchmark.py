"""P1 §12.4 benchmark: 10.000 synthetic rows import + 10.000 work item distribution.

The benchmark measures the **runtime** cost of the P1 pipeline on a DEV site:

* the wall time of the import (preview + confirm + worker) and of the
  distribution (API enqueue latency + chunked RQ worker execution);
* the peak RSS of the worker process;
* the number of database queries;
* the lock waiting time (InnoDB `Innodb_row_lock_time` / `Innodb_row_lock_waits`);
* the index inventory of the P1 tables and `tabCRM Lead`;
* the success / failure / duplicate counters of the import and of the run;
* the reconciliation of the campaign (owners, claims, history) and capacity caps.

The tool never invents an SLA: it only reports the **measured** numbers. The
caller decides whether they satisfy the requirement.

Usage (on the isolated benchmark site):

    bench --site p1bench.localhost execute crm_cskh.tools.p1_benchmark.run_benchmark
    bench --site p1bench.localhost execute crm_cskh.tools.p1_benchmark.run_supplementary_checks
"""

from __future__ import annotations

import json
import os
import resource
import time
from unittest import mock

import frappe
from frappe.utils import now_datetime

from crm_cskh.jobs import distribution_worker, import_worker
from crm_cskh.services import distribution
from crm_cskh.services.import_service import confirm, create_preview

ROW_COUNT = int(os.environ.get("P1_BENCH_ROWS", "10000"))
HEADER = "Số điện thoại"
# The offset makes the synthetic phone range unique, so a second benchmark on
# the same site imports **new** customers instead of reusing the first run.
PHONE_OFFSET = int(os.environ.get("P1_BENCH_PHONE_OFFSET", "0"))
# The report is written to this file: a long benchmark must not lose its
# measured numbers when the console output is truncated.
OUTPUT_PATH = os.environ.get("P1_BENCH_OUTPUT", "/tmp/p1-benchmark-report.json")
SUPP_OUTPUT_PATH = os.environ.get("P1_BENCH_SUPP_OUTPUT", "/tmp/p1-benchmark-mixed-capacity.json")


def _log(message):
	"""Log the progress of the benchmark on stdout."""
	print(message, flush=True)


def _rss_mb():
	"""The peak RSS of this process in MiB."""
	return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _db_counters():
	"""The cumulative InnoDB counters of the session (queries, lock waits)."""
	rows = frappe.db.sql(
		"show global status where Variable_name in %s",
		(("Innodb_row_lock_waits", "Innodb_row_lock_time", "Questions", "Queries"),),
		as_dict=True,
	)
	return {row["Variable_name"]: int(row["Value"] or 0) for row in rows}


def _synthetic_phones(count, offset=None):
	"""`count` unique, syntactically valid Vietnamese mobile numbers."""
	phones = []
	seen = set()
	index = PHONE_OFFSET if offset is None else int(offset)
	while len(phones) < count:
		index += 1
		subscriber = f"{index:08d}"[-8:]
		phone = f"09{subscriber}"
		if phone in seen:
			continue
		seen.add(phone)
		phones.append(phone)
	return phones


def _write_csv(phones, file_name="p1-benchmark.csv"):
	content = ("\n".join([HEADER, *phones]) + "\n").encode("utf-8")
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"is_private": 1,
			"attached_to_doctype": "CRM Lead",
			"attached_to_name": "Administrator",
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc.name, len(content)


def _index_inventory():
	"""The indexes of the P1 tables and upstream `tabCRM Lead`."""
	inventory = {}
	for table in (
		"tabCRM Lead",
		"tabCSKH Work Item",
		"tabCSKH Active Claim",
		"tabCSKH Assignment History",
		"tabCSKH Identity Index",
		"tabCSKH Import Batch",
		"tabCSKH Import Batch Lead",
		"tabCSKH Distribution Run",
	):
		rows = frappe.db.sql(f"show index from `{table}`")
		inventory[table] = [
			{
				"name": row[2],
				"column": row[4],
				"unique": not bool(row[1]),
			}
			for row in rows
		]
	return inventory


def _ensure_users(campaign, capacities=None):
	"""Create (or reuse) the three benchmark assignees with 50/30/20 weights."""
	capacities = capacities or (0, 0, 0)
	users = []
	for index, (weight, cap) in enumerate(zip((50, 30, 20), capacities, strict=True)):
		email = f"p1-bench-staff-{index}@example.test"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": f"bench{index}",
					"enabled": 1,
					"send_welcome_email": 0,
					"user_type": "System User",
				}
			).insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		if "Sales User" not in {row.role for row in user.roles}:
			user.add_roles("Sales User")
		frappe.get_doc(
			{
				"doctype": "CSKH Assignment Rule",
				"campaign": campaign,
				"user": email,
				"weight": weight,
				"capacity_limit": int(cap),
				"version": 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		users.append({"user": email, "weight": weight, "capacity_limit": int(cap)})
	frappe.db.commit()
	return users


def run_benchmark():
	"""Run the whole 10k benchmark and write a JSON report."""
	frappe.set_user("Administrator")
	report = {
		"site": frappe.local.site,
		"started_at": now_datetime(),
		"row_count": ROW_COUNT,
	}

	counters_before = _db_counters()
	rss_before = _rss_mb()

	# -- 1. the synthetic file ------------------------------------------
	phones = _synthetic_phones(ROW_COUNT)
	file_name, size = _write_csv(phones)
	frappe.db.commit()
	report["file_bytes"] = size

	# -- 2. the import ----------------------------------------------------
	t0 = time.perf_counter()
	batch = create_preview(
		file_name,
		actor="Administrator",
		idempotency_key=f"p1-bench-import-{frappe.generate_hash(length=8)}",
	)
	preview_seconds = time.perf_counter() - t0
	_log(f"[bench] preview done in {preview_seconds:.3f}s (batch={batch['batch']})")

	with mock.patch("frappe.enqueue"):
		confirm(batch["batch"], actor="Administrator")
	frappe.db.commit()
	t0 = time.perf_counter()
	import_worker.run_batch(batch["batch"])
	worker_seconds = time.perf_counter() - t0
	_log(f"[bench] import worker done in {worker_seconds:.3f}s")

	batch_doc = frappe.get_doc("CSKH Import Batch", batch["batch"])
	report["import"] = {
		"preview_seconds": round(preview_seconds, 3),
		"worker_seconds": round(worker_seconds, 3),
		"total_seconds": round(preview_seconds + worker_seconds, 3),
		"status": batch_doc.status,
		"attempts": batch_doc.attempts,
		"total_rows": batch_doc.total_rows,
		"valid_rows": batch_doc.valid_rows,
		"new_rows": batch_doc.new_rows,
		"duplicate_rows": batch_doc.duplicate_rows,
		"created_rows": batch_doc.created_rows,
		"reused_rows": batch_doc.reused_rows,
		"skipped_rows": batch_doc.skipped_rows,
		"error_rows": batch_doc.error_rows,
		"rows_per_second": round(batch_doc.created_rows / worker_seconds, 1) if worker_seconds else None,
	}

	# -- 3. the distribution (async enqueue + chunked RQ worker) ----------
	leads = frappe.db.sql(
		"select canonical_lead as lead from `tabCSKH Identity Index` order by canonical_lead asc"
	)
	leads = [row[0] for row in leads]
	report["distribution_pool"] = len(leads)

	code = f"P1-BENCH-{frappe.generate_hash(length=8)}"
	campaign = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": code,
			"title": "P1 benchmark",
			"status": "Active",
			"selection_json": json.dumps({"leads": leads}),
		}
	)
	campaign.insert(ignore_permissions=True)
	rules = _ensure_users(campaign.name)

	preview = distribution.preview(campaign.name, actor="Administrator")
	_log(f"[bench] preview of the distribution done (id={preview['preview_id']})")
	t_enq = time.perf_counter()
	with mock.patch("frappe.enqueue"):
		queued = distribution.enqueue_run(preview["preview_id"], actor="Administrator", enqueue_job=True)
	frappe.db.commit()
	enqueue_seconds = time.perf_counter() - t_enq
	_log(
		f"[bench] distribution enqueue done in {enqueue_seconds:.3f}s "
		f"status={queued['status']} total={queued['total']}"
	)

	t0 = time.perf_counter()
	result = distribution_worker.run_distribution(queued["run_id"], actor="Administrator")
	execute_seconds = time.perf_counter() - t0
	_log(f"[bench] distribution worker done in {execute_seconds:.3f}s status={result['status']}")

	report["distribution"] = {
		"campaign": campaign.name,
		"rules": rules,
		"api_enqueue_seconds": round(enqueue_seconds, 3),
		"api_initial_status": queued["status"],
		"seconds": round(execute_seconds, 3),
		"status": result["status"],
		"progress": result["progress"],
		"total": result["total"],
		"created": result["created"],
		"errors": result["errors"],
		"assigned": result["assigned"],
		"items_per_second": round(result["created"] / execute_seconds, 1) if execute_seconds else None,
	}

	# -- 4. the reconciliation (the database truth) ------------------------
	reconciliation = distribution.reconcile(campaign.name, actor="Administrator")
	report["reconciliation"] = {
		"distinct_leads": reconciliation["distinct_leads"],
		"active_work_items": reconciliation["active_work_items"],
		"active_claims": reconciliation["active_claims"],
		"current_owners": reconciliation["current_owners"],
		"assignment_events": reconciliation["assignment_events"],
		"owner_total": sum(reconciliation["current_owners"].values()),
		"event_total": sum(reconciliation["assignment_events"].values()),
	}

	# The fairness ratio of the measured result.
	total = sum(result["assigned"].values()) or 1
	report["fairness"] = {
		rule["user"]: {
			"weight": rule["weight"],
			"assigned": result["assigned"].get(rule["user"], 0),
			"ratio": round(result["assigned"].get(rule["user"], 0) / total, 4),
		}
		for rule in rules
	}

	# -- 5. the database cost ---------------------------------------------
	counters_after = _db_counters()
	report["database"] = {
		"questions_delta": counters_after.get("Questions", 0) - counters_before.get("Questions", 0),
		"queries_delta": counters_after.get("Queries", 0) - counters_before.get("Queries", 0),
		"row_lock_waits_delta": counters_after.get("Innodb_row_lock_waits", 0)
		- counters_before.get("Innodb_row_lock_waits", 0),
		"row_lock_time_ms_delta": counters_after.get("Innodb_row_lock_time", 0)
		- counters_before.get("Innodb_row_lock_time", 0),
	}

	report["memory"] = {
		"rss_before_mb": round(rss_before, 1),
		"peak_rss_mb": round(_rss_mb(), 1),
	}

	report["indexes"] = _index_inventory()
	report["finished_at"] = now_datetime()

	payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
	try:
		with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
			handle.write(payload + "\n")
	except OSError as exc:  # pragma: no cover - the file is best effort
		_log(f"[bench] the report could not be written to {OUTPUT_PATH}: {exc}")
	_log(payload)
	return report


def run_supplementary_checks():
	"""Verify mixed import classes, capacity-capped RQ distribution, and global DB integrity."""
	frappe.set_user("Administrator")
	counters_before = _db_counters()
	rss_before = _rss_mb()

	# 1. Mixed import batch on the 20k+ populated benchmark site:
	# 50 new phones + 20 existing CRM phones + 15 in-file duplicates + 15 invalid rows = 100 rows.
	base_offset = 30_000_000 + (int(frappe.generate_hash(length=8), 16) % 50_000_000)
	new_phones = _synthetic_phones(50, offset=base_offset)
	existing_crm = _synthetic_phones(20, offset=0)
	in_file_dups = new_phones[:15]
	invalid_rows = [f"bad-phone-{i:02d}" for i in range(15)]
	mixed_rows = [*new_phones, *existing_crm, *in_file_dups, *invalid_rows]

	file_name, file_bytes = _write_csv(mixed_rows, file_name="p1-bench-mixed.csv")
	frappe.db.commit()

	t0 = time.perf_counter()
	mixed_preview = create_preview(
		file_name,
		actor="Administrator",
		idempotency_key=f"p1-bench-mixed-{frappe.generate_hash(length=8)}",
	)
	mixed_preview_s = time.perf_counter() - t0

	with mock.patch("frappe.enqueue"):
		confirm(mixed_preview["batch"], actor="Administrator")
	frappe.db.commit()
	t0 = time.perf_counter()
	import_worker.run_batch(mixed_preview["batch"])
	mixed_worker_s = time.perf_counter() - t0
	mixed_doc = frappe.get_doc("CSKH Import Batch", mixed_preview["batch"])

	# Create 50 additional unclaimed canonical leads so we have a 100-lead unclaimed pool for capacity test.
	cap_extra_phones = _synthetic_phones(50, offset=base_offset + 1000)
	cap_file, _ = _write_csv(cap_extra_phones, file_name="p1-bench-cap.csv")
	frappe.db.commit()
	cap_imp = create_preview(
		cap_file,
		actor="Administrator",
		idempotency_key=f"p1-bench-cap-{frappe.generate_hash(length=8)}",
	)
	with mock.patch("frappe.enqueue"):
		confirm(cap_imp["batch"], actor="Administrator")
	frappe.db.commit()
	import_worker.run_batch(cap_imp["batch"])

	# 2. Capacity-capped 50/30/20 distribution over 100 unclaimed leads (cap=30 on 50-weight user).
	from crm_cskh.services.phone_identity import normalize_vn_phone

	pool_canonicals = [normalize_vn_phone(p) for p in (*new_phones, *cap_extra_phones)]
	pool_leads = frappe.get_all(
		"CSKH Identity Index",
		filters={"normalized_phone": ["in", pool_canonicals]},
		pluck="canonical_lead",
	)
	pool_leads.sort()

	cap_campaign = frappe.get_doc(
		{
			"doctype": "CSKH Campaign",
			"code": f"P1-BENCH-CAP-{frappe.generate_hash(length=8)}",
			"title": "P1 capacity benchmark",
			"status": "Active",
			"selection_json": json.dumps({"leads": pool_leads}),
		}
	)
	cap_campaign.insert(ignore_permissions=True)
	cap_rules = _ensure_users(cap_campaign.name, capacities=(30, 0, 0))

	cap_prev = distribution.preview(cap_campaign.name, actor="Administrator")
	t_enq = time.perf_counter()
	with mock.patch("frappe.enqueue"):
		cap_queued = distribution.enqueue_run(cap_prev["preview_id"], actor="Administrator", enqueue_job=True)
	frappe.db.commit()
	cap_enq_s = time.perf_counter() - t_enq

	t0 = time.perf_counter()
	cap_res = distribution_worker.run_distribution(cap_queued["run_id"], actor="Administrator")
	cap_exec_s = time.perf_counter() - t0
	cap_reconcile = distribution.reconcile(cap_campaign.name, actor="Administrator")

	# 3. Global SQL post-check across the entire benchmark database (20,100+ leads).
	dup_identities = frappe.db.sql(
		"select normalized_phone, count(*) as c from `tabCSKH Identity Index` group by normalized_phone having count(*) > 1"
	)
	dup_claims = frappe.db.sql(
		"select lead, count(*) as c from `tabCSKH Active Claim` group by lead having count(*) > 1"
	)
	dup_work_items = frappe.db.sql(
		"select campaign, lead, count(*) as c from `tabCSKH Work Item` group by campaign, lead having count(*) > 1"
	)

	counters_after = _db_counters()
	report = {
		"site": frappe.local.site,
		"recorded_at": str(now_datetime()),
		"mixed_import": {
			"file_bytes": file_bytes,
			"preview_seconds": round(mixed_preview_s, 3),
			"worker_seconds": round(mixed_worker_s, 3),
			"status": mixed_doc.status,
			"total_rows": int(mixed_doc.total_rows),
			"valid_rows": int(mixed_doc.valid_rows),
			"new_rows": int(mixed_doc.new_rows),
			"duplicate_rows": int(mixed_doc.duplicate_rows),
			"error_rows": int(mixed_doc.error_rows),
			"created_rows": int(mixed_doc.created_rows),
			"reused_rows": int(mixed_doc.reused_rows),
			"skipped_rows": int(mixed_doc.skipped_rows),
			"has_error_artifact": bool(mixed_doc.error_artifact),
		},
		"capacity_distribution": {
			"campaign": cap_campaign.name,
			"pool_size": len(pool_leads),
			"rules": cap_rules,
			"api_enqueue_seconds": round(cap_enq_s, 3),
			"api_initial_status": cap_queued["status"],
			"execute_seconds": round(cap_exec_s, 3),
			"status": cap_res["status"],
			"progress": cap_res["progress"],
			"total": cap_res["total"],
			"created": cap_res["created"],
			"errors": cap_res["errors"],
			"assigned": cap_res["assigned"],
			"reconciliation": {
				"distinct_leads": cap_reconcile["distinct_leads"],
				"active_work_items": cap_reconcile["active_work_items"],
				"active_claims": cap_reconcile["active_claims"],
				"current_owners": cap_reconcile["current_owners"],
				"assignment_events": cap_reconcile["assignment_events"],
			},
		},
		"global_site_postcheck": {
			"total_crm_leads": frappe.db.count("CRM Lead"),
			"total_identities": frappe.db.count("CSKH Identity Index"),
			"total_work_items": frappe.db.count("CSKH Work Item"),
			"total_active_claims": frappe.db.count("CSKH Active Claim"),
			"total_assignment_history": frappe.db.count("CSKH Assignment History"),
			"duplicate_identities": len(dup_identities),
			"duplicate_active_claims": len(dup_claims),
			"duplicate_campaign_work_items": len(dup_work_items),
		},
		"database": {
			"questions_delta": counters_after.get("Questions", 0) - counters_before.get("Questions", 0),
			"queries_delta": counters_after.get("Queries", 0) - counters_before.get("Queries", 0),
			"row_lock_waits_delta": counters_after.get("Innodb_row_lock_waits", 0)
			- counters_before.get("Innodb_row_lock_waits", 0),
			"row_lock_time_ms_delta": counters_after.get("Innodb_row_lock_time", 0)
			- counters_before.get("Innodb_row_lock_time", 0),
		},
		"memory": {
			"rss_before_mb": round(rss_before, 1),
			"peak_rss_mb": round(_rss_mb(), 1),
		},
		"indexes": _index_inventory(),
	}
	payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
	with open(SUPP_OUTPUT_PATH, "w", encoding="utf-8") as handle:
		handle.write(payload + "\n")
	_log(payload)
	return report
