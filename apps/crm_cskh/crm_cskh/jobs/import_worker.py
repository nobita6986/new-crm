"""Background worker that commits a confirmed import batch (P1 6.2, 6.4, L-25, P4 BG-02).

The worker streams the file, re-normalizes every phone number and checks/claims
the canonical identity under chunked transactions. Counters are reconciled from
the database (`CSKH Import Batch Lead` + `CSKH Identity Index`), never summed
from non-idempotent events, so a retry after a crash or a concurrent race cannot
inflate the totals (P1 6.4).
"""

import json
import re
import time

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import identity as identity_service
from crm_cskh.services import runtime_cache
from crm_cskh.services.errors import Conflict, FileTooLarge
from crm_cskh.services.import_service import (
	_cell_value,
	_classify,
	_column_index_map,
	_detect_extension,
	_raw_cell_at,
	_read_rows,
	_session_questions_count,
	_settings,
	resolve_import_columns,
	sanitize_cell,
	stream_import_rows,
	write_error_artifact,
)
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone

CHUNK_SIZE = 500
PLACEHOLDER_FIRST_NAME = "Chưa rõ tên"
MAX_DATA_FIELD_LENGTH = 140

II_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"normalized_phone",
	"canonical_lead",
]

IBL_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"import_batch",
	"lead",
	"outcome",
	"row_number",
]


def run_batch(batch_name):
	"""Process a confirmed batch. Safe to call twice for the same batch.

	**Crash recovery (P1 §6.4, P1 §12.4):** the worker commits its progress
	after every chunk and stores the number of processed rows in
	`CSKH Import Batch.progress`. A worker that dies in the middle of a batch
	therefore leaves the batch in `RUNNING` with a known progress, and the
	next attempt resumes **after** that progress instead of starting over.
	"""
	if not batch_name:
		return
	locked = frappe.db.sql(
		"select name, status from `tabCSKH Import Batch` where name = %s for update",
		(batch_name,),
		as_dict=True,
	)
	if not locked:
		return
	current_status = locked[0].status
	if current_status in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
		return
	if current_status not in ("QUEUED", "CONFIRMED", "RUNNING"):
		return

	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	if batch.status == "CONFIRMED":
		batch.status = "QUEUED"
		batch.save(ignore_permissions=True)
	batch.status = "RUNNING"
	batch.attempts = int(batch.attempts or 0) + 1
	batch.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		_result = _process(batch)
	except Exception as exc:
		frappe.db.rollback()
		batch.reload()
		batch.status = "FAILED"
		batch.error = _redact(str(exc))[:500]
		batch.save(ignore_permissions=True)
		frappe.db.commit()
		raise


# SEC-08 / P1 §12.3: an exception message may carry a phone number. The
# stored error is redacted so the batch never becomes a PII leak.
_PHONE_PATTERN = re.compile(r"(?:\+?84|0)\d{8,10}")


def _redact(message):
	"""Replace every phone number of a message with a placeholder."""
	return _PHONE_PATTERN.sub("***", message or "")


def _stream_worker_targets_and_errors(content: bytes, extension: str, max_rows: int, mapping: dict):
	"""Stream file once into `(resolved, total_rows, targets, preview_invalid_rows, error_rows)`."""
	seen_in_file: set[str] = set()
	targets: list[dict] = []
	preview_invalid_rows: set[int] = set()
	error_rows: list[dict] = []
	total_rows = 0

	with stream_import_rows(content, extension, max_rows) as (header, first_cells, is_xlsx, row_iter):
		resolved = resolve_import_columns(header, first_cells, mapping)
		col_map = _column_index_map(header)
		phone_col = resolved["phone"]
		phone_idx = col_map.get(phone_col) if phone_col else None
		name_idx = col_map.get(resolved["name"]) if resolved["name"] else None
		source_idx = col_map.get(resolved["source"]) if resolved["source"] else None

		for row_number, raw_row in row_iter:
			total_rows += 1
			raw_phone = _raw_cell_at(raw_row, phone_idx, is_xlsx=is_xlsx) if phone_col else None
			if raw_phone is None:
				preview_invalid_rows.add(row_number)
				error_rows.append({"row_number": row_number, "reason": "no_phone_column"})
				continue
			try:
				canonical = normalize_vn_phone(str(raw_phone))
			except InvalidPhone as exc:
				preview_invalid_rows.add(row_number)
				error_rows.append({"row_number": row_number, "reason": str(exc)[:160]})
				continue

			if canonical in seen_in_file:
				continue
			seen_in_file.add(canonical)
			raw_name = _raw_cell_at(raw_row, name_idx, is_xlsx=is_xlsx) if name_idx is not None else None
			raw_source = (
				_raw_cell_at(raw_row, source_idx, is_xlsx=is_xlsx) if source_idx is not None else None
			)
			targets.append(
				{
					"row_number": int(row_number),
					"canonical": canonical,
					"_raw_phone": str(raw_phone),
					"_mapped_name": raw_name,
					"_mapped_source": raw_source,
				}
			)

	return resolved, total_rows, targets, preview_invalid_rows, error_rows


def _resolve_default_lead_fields() -> tuple[str, str | None, list[str]]:
	"""Resolve default `CRM Lead.status`, `communication_status`, and optional CSKH columns once."""
	if frappe.db.exists("CRM Lead Status", "New"):
		lead_status = "New"
	else:
		open_statuses = frappe.get_all("CRM Lead Status", {"type": "Open"}, pluck="name")
		lead_status = open_statuses[0] if open_statuses else "New"

	comm_status = None
	if runtime_cache.table_exists("CRM Communication Status") and frappe.db.exists(
		"CRM Communication Status", "Open"
	):
		comm_status = "Open"

	opt_cols: list[str] = []
	for col in ("cskh_is_placeholder_name", "cskh_do_not_contact", "title"):
		if runtime_cache.has_column("CRM Lead", col):
			opt_cols.append(col)
	return lead_status, comm_status, opt_cols


def _reserve_crm_lead_names(count: int, now_ts) -> list[str]:
	"""Atomically reserve `count` consecutive `CRM-LEAD-YYYY-NNNNN` primary keys."""
	if count <= 0:
		return []
	series_prefix = f"CRM-LEAD-{now_ts.year}-"
	cur_rows = frappe.db.sql(
		"select `current` from `tabSeries` where `name` = %s for update",
		(series_prefix,),
	)
	max_rows = frappe.db.sql(
		"select max(cast(substring(`name`, %s) as unsigned)) from `tabCRM Lead` where `name` like %s",
		(len(series_prefix) + 1, f"{series_prefix}%"),
	)
	max_existing = int((max_rows[0][0] if max_rows and max_rows[0] else 0) or 0)
	if cur_rows:
		base_seq = max(int(cur_rows[0][0] or 0), max_existing)
		frappe.db.sql(
			"update `tabSeries` set `current` = %s where `name` = %s",
			(base_seq + count, series_prefix),
		)
	else:
		base_seq = max_existing
		frappe.db.sql(
			"insert into `tabSeries` (`name`, `current`) values (%s, %s)",
			(series_prefix, base_seq + count),
		)
	return [f"{series_prefix}{seq:05d}" for seq in range(base_seq + 1, base_seq + count + 1)]


def _process_chunk_batched(
	*,
	batch_name: str,
	active_entries: list[dict],
	source_map: dict[str, str],
	lead_defaults: tuple[str, str | None, list[str]],
) -> None:
	"""Bulk-resolve existing leads, bulk-insert new `CRM Lead` + `CSKH Identity Index`, and link batch."""
	if not active_entries:
		return

	now_ts = now_datetime()
	session_user = getattr(getattr(frappe, "session", None), "user", None) or "Administrator"
	default_status, default_comm_status, opt_lead_cols = lead_defaults

	chunk_phones = [e["canonical"] for e in active_entries]
	ph = ", ".join(["%s"] * len(chunk_phones))
	lead_by_phone: dict[str, str] = {}
	for r in frappe.db.sql(
		f"select normalized_phone, canonical_lead from `tabCSKH Identity Index` "
		f"where normalized_phone in ({ph}) lock in share mode",
		tuple(chunk_phones),
		as_dict=True,
	):
		if r.normalized_phone and r.canonical_lead:
			lead_by_phone[str(r.normalized_phone)] = str(r.canonical_lead)

	unindexed_phones = [p for p in chunk_phones if p not in lead_by_phone]
	if unindexed_phones:
		raw_variants: list[str] = []
		for norm_ph in unindexed_phones:
			digits = norm_ph[3:] if norm_ph.startswith("+84") else norm_ph
			raw_variants.extend((f"0{digits}", f"84{digits}", f"+84{digits}"))
		ph_v = ", ".join(["%s"] * len(raw_variants))
		adopted_ii_values: list[tuple] = []
		seen_ii_names: set[str] = set()
		for r in frappe.db.sql(
			f"select name, mobile_no from `tabCRM Lead` where mobile_no in ({ph_v}) "
			f"order by creation asc, name asc",
			tuple(raw_variants),
			as_dict=True,
		):
			try:
				norm = normalize_vn_phone(r.mobile_no)
			except InvalidPhone:
				continue
			if norm in unindexed_phones and norm not in lead_by_phone:
				lead_by_phone[norm] = str(r.name)
				iname = frappe.generate_hash(length=10)
				while iname in seen_ii_names:
					iname = frappe.generate_hash(length=10)
				seen_ii_names.add(iname)
				adopted_ii_values.append(
					(iname, now_ts, now_ts, session_user, session_user, 0, 0, norm, str(r.name))
				)
		if adopted_ii_values:
			frappe.db.bulk_insert("CSKH Identity Index", fields=II_BULK_FIELDS, values=adopted_ii_values)

	new_entries = [e for e in active_entries if e["canonical"] not in lead_by_phone]
	created_lead_names: set[str] = set()
	if new_entries:
		allocated_names = _reserve_crm_lead_names(len(new_entries), now_ts)
		lead_fields = [
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
			"communication_status",
			"source",
			"lead_owner",
			"converted",
			"lead_score",
			*opt_lead_cols,
		]
		lead_values: list[tuple] = []
		new_ii_values: list[tuple] = []
		seen_ii_new: set[str] = set()

		for lead_id, entry in zip(allocated_names, new_entries, strict=True):
			first_name, is_placeholder = _normalize_new_lead_name(entry.get("_mapped_name"))
			clean_src_key = sanitize_cell(entry.get("_mapped_source"))[:MAX_DATA_FIELD_LENGTH].strip()
			resolved_src = source_map.get(clean_src_key) if clean_src_key else None

			opt_vals: list = []
			for col in opt_lead_cols:
				if col == "cskh_is_placeholder_name":
					opt_vals.append(int(is_placeholder))
				elif col == "cskh_do_not_contact":
					opt_vals.append(0)
				elif col == "title":
					opt_vals.append(first_name)

			lead_values.append(
				(
					lead_id,
					now_ts,
					now_ts,
					session_user,
					session_user,
					0,
					0,
					"CRM-LEAD-.YYYY.-",
					first_name,
					first_name,
					entry["canonical"],
					default_status,
					default_comm_status,
					resolved_src,
					None,
					0,
					0,
					*opt_vals,
				)
			)
			iname = frappe.generate_hash(length=10)
			while iname in seen_ii_new:
				iname = frappe.generate_hash(length=10)
			seen_ii_new.add(iname)
			new_ii_values.append(
				(
					iname,
					now_ts,
					now_ts,
					session_user,
					session_user,
					0,
					0,
					entry["canonical"],
					lead_id,
				)
			)
			lead_by_phone[entry["canonical"]] = lead_id
			created_lead_names.add(lead_id)

		frappe.db.bulk_insert("CRM Lead", fields=lead_fields, values=lead_values)
		frappe.db.bulk_insert("CSKH Identity Index", fields=II_BULK_FIELDS, values=new_ii_values)

	target_leads = [lead_by_phone[e["canonical"]] for e in active_entries]
	ph_l = ", ".join(["%s"] * len(target_leads))
	existing_ibl = {
		str(r.lead)
		for r in frappe.db.sql(
			f"select lead from `tabCSKH Import Batch Lead` where import_batch = %s and lead in ({ph_l})",
			(batch_name, *target_leads),
			as_dict=True,
		)
	}

	ibl_values: list[tuple] = []
	seen_ibl_names: set[str] = set()
	for entry in active_entries:
		lead_id = lead_by_phone[entry["canonical"]]
		if lead_id in existing_ibl:
			continue
		existing_ibl.add(lead_id)
		outcome = "created" if lead_id in created_lead_names else "reused"
		bname = frappe.generate_hash(length=10)
		while bname in seen_ibl_names:
			bname = frappe.generate_hash(length=10)
		seen_ibl_names.add(bname)
		ibl_values.append(
			(
				bname,
				now_ts,
				now_ts,
				session_user,
				session_user,
				0,
				0,
				batch_name,
				lead_id,
				outcome,
				max(0, int(entry["row_number"] or 0)),
			)
		)

	if ibl_values:
		frappe.db.bulk_insert("CSKH Import Batch Lead", fields=IBL_BULK_FIELDS, values=ibl_values)


def _process(batch):
	t_worker_start = time.perf_counter()
	q_worker_before = _session_questions_count()

	file_doc = frappe.get_doc("File", batch.source_file)
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	extension = _detect_extension(file_doc)

	limits = _settings()
	if file_doc.file_size and file_doc.file_size > limits["max_import_file_bytes"]:
		raise FileTooLarge("The file is larger than the configured limit.")

	mapping = {}
	if batch.mapping_json:
		try:
			mapping = json.loads(batch.mapping_json)
		except (ValueError, TypeError):
			mapping = {}

	# Stream CSV/XLSX once into compact distinct targets + invalid row records
	t_parse0 = time.perf_counter()
	resolved, streamed_total_rows, targets, preview_invalid_rows, error_rows = (
		_stream_worker_targets_and_errors(content, extension, limits["max_import_rows"], mapping)
	)
	worker_csv_parse_ms = (time.perf_counter() - t_parse0) * 1000.0
	phone_column = resolved["phone"]

	# Only run full `_classify` fallback if the batch was created manually without `create_preview`
	fallback_classification = None
	if not int(batch.total_rows or 0) and streamed_total_rows > 0:
		_, raw_rows = _read_rows(content, extension, limits["max_import_rows"])
		fallback_classification = _classify(
			raw_rows,
			mapping,
			phone_column,
			name_column=resolved["name"],
			source_column=resolved["source"],
		)

	resume_from = int(batch.progress or 0)
	safe_progress = resume_from
	blocked_at = None

	use_batched_chunk = (
		_ensure_lead is _DEFAULT_ENSURE_LEAD and _record_batch_lead is _DEFAULT_RECORD_BATCH_LEAD
	)

	t_write0 = time.perf_counter()
	source_map: dict[str, str] = {}
	lead_defaults: tuple[str, str | None, list[str]] | None = None
	if use_batched_chunk and targets:
		distinct_sources = sorted(
			{
				sanitize_cell(t.get("_mapped_source"))[:MAX_DATA_FIELD_LENGTH].strip()
				for t in targets
				if int(t["row_number"]) > resume_from and t.get("_mapped_source")
			}
			- {""}
		)
		for src_text in distinct_sources:
			resolved_src = _resolve_lead_source(src_text)
			if resolved_src:
				source_map[src_text] = resolved_src
		if distinct_sources:
			frappe.db.commit()
		lead_defaults = _resolve_default_lead_fields()

	eff_chunk_size = max(1, int(CHUNK_SIZE or 500))
	for start in range(0, len(targets), eff_chunk_size):
		chunk = targets[start : start + eff_chunk_size]
		active_entries = [entry for entry in chunk if int(entry["row_number"]) > resume_from]
		chunk_error_rows = []
		failed_rows = []

		if active_entries:
			for chunk_attempt in range(5):
				chunk_error_rows = []
				failed_rows = []
				try:
					if use_batched_chunk and lead_defaults is not None:
						sp_chunk = f"cskh_imp_chunk_{start}"
						frappe.db.savepoint(sp_chunk)
						try:
							_process_chunk_batched(
								batch_name=batch.name,
								active_entries=active_entries,
								source_map=source_map,
								lead_defaults=lead_defaults,
							)
							break
						except Exception as batch_exc:
							if _is_transient_db_conflict(batch_exc) and chunk_attempt < 4:
								raise
							_rollback_savepoint_quietly(sp_chunk)

					for entry in active_entries:
						savepoint = f"cskh_import_{entry['row_number']}"
						frappe.db.savepoint(savepoint)
						try:
							raw_ph = entry.get("_raw_phone")
							if raw_ph is None:
								continue
							canonical = normalize_vn_phone(str(raw_ph))
							lead_name, is_new = _ensure_lead(canonical, entry)
							_record_batch_lead(
								batch.name,
								lead_name,
								is_new=is_new,
								row_number=int(entry["row_number"]),
							)
						except InvalidPhone as exc:
							_rollback_savepoint_quietly(savepoint)
							row_num = int(entry["row_number"])
							failed_rows.append(row_num)
							chunk_error_rows.append(
								{"row_number": entry["row_number"], "reason": str(exc)[:160]}
							)
						except Exception as exc:
							if _is_transient_db_conflict(exc) and chunk_attempt < 4:
								raise
							_rollback_savepoint_quietly(savepoint)
							row_num = int(entry["row_number"])
							failed_rows.append(row_num)
							chunk_error_rows.append(
								{"row_number": entry["row_number"], "reason": str(exc)[:200]}
							)
					break
				except Exception as exc:
					if not (_is_transient_db_conflict(exc) and chunk_attempt < 4):
						raise
					frappe.db.rollback()
					time.sleep(0.03 * (chunk_attempt + 1))

		error_rows.extend(chunk_error_rows)
		if failed_rows:
			first_failed = min(failed_rows)
			blocked_at = min(blocked_at, first_failed) if blocked_at is not None else first_failed
		if blocked_at is not None:
			safe_progress = max(resume_from, blocked_at - 1)
		else:
			safe_progress = max(
				safe_progress,
				max((int(entry["row_number"]) for entry in chunk), default=0),
			)
		frappe.db.set_value("CSKH Import Batch", batch.name, "progress", safe_progress, update_modified=True)
		frappe.db.commit()

	worker_db_write_ms = (time.perf_counter() - t_write0) * 1000.0

	# Reconcile counters strictly from the database (P1 §6.4, L-25)
	t_rec0 = time.perf_counter()
	batch.reload()
	created, reused, failed, missing = _reconcile_counts(targets, batch)
	worker_reconcile_ms = (time.perf_counter() - t_rec0) * 1000.0

	skipped = failed
	for entry in missing:
		error_rows.append({"row_number": entry["row_number"], "reason": "lead_not_created"})
	error_rows = _dedupe_error_rows(error_rows)
	preview_error_count = len(preview_invalid_rows)
	worker_error_count = sum(1 for row in error_rows if row.get("row_number") not in preview_invalid_rows)

	batch.created_rows = created
	batch.reused_rows = reused
	batch.skipped_rows = skipped + preview_error_count
	if not int(batch.total_rows or 0) and fallback_classification is not None:
		batch.total_rows = fallback_classification["total_rows"]
		batch.valid_rows = fallback_classification["valid_rows"]
		batch.new_rows = fallback_classification["new_rows"]
		batch.duplicate_rows = fallback_classification["duplicate_rows"]
	batch.error_rows = preview_error_count + worker_error_count
	batch.status = "COMPLETED_WITH_ERRORS" if batch.error_rows else "COMPLETED"
	batch.committed_at = now_datetime()
	if blocked_at is not None:
		batch.progress = safe_progress
	else:
		batch.progress = max(
			int(batch.progress or 0),
			max((int(e["row_number"]) for e in targets), default=0),
		)

	t_art0 = time.perf_counter()
	if error_rows:
		batch.error_artifact = write_error_artifact(batch.name, error_rows)
	else:
		batch.error_artifact = None
		batch.error = None
	worker_artifact_ms = (time.perf_counter() - t_art0) * 1000.0

	worker_total_ms = (time.perf_counter() - t_worker_start) * 1000.0
	sql_delta = max(0, _session_questions_count() - q_worker_before)
	try:
		prev_obj = json.loads(batch.preview_json or "{}")
		if isinstance(prev_obj, dict):
			prev_obj["worker_telemetry"] = {
				"worker_csv_parse_ms": round(worker_csv_parse_ms, 2),
				"worker_db_write_ms": round(worker_db_write_ms, 2),
				"worker_reconcile_ms": round(worker_reconcile_ms, 2),
				"worker_error_artifact_ms": round(worker_artifact_ms, 2),
				"worker_total_ms": round(worker_total_ms, 2),
				"worker_targets_count": len(targets),
				"worker_chunk_size": eff_chunk_size,
				"worker_batched_mode": bool(use_batched_chunk),
				"worker_sql_queries_delta": sql_delta,
				"worker_sql_queries_per_target": round(sql_delta / len(targets), 4) if targets else 0.0,
			}
			batch.preview_json = json.dumps(prev_obj, ensure_ascii=False)
	except Exception:
		pass

	batch.save(ignore_permissions=True)
	frappe.db.commit()
	return {"created": created, "reused": reused, "skipped": skipped}


def _dedupe_error_rows(error_rows):
	"""Keep **one** line per failed row in the error report (SEC-08)."""
	seen = {}
	order = []
	for entry in error_rows:
		row_number = entry.get("row_number")
		if row_number in seen:
			continue
		seen[row_number] = entry
		order.append(entry)
	return order


def _distinct_targets(classification):
	"""Return the **distinct** canonical phones of the file, in row order."""
	seen = set()
	targets = []
	for entry in classification.get("resolvable") or []:
		canonical = entry.get("canonical")
		if not canonical or canonical in seen:
			continue
		seen.add(canonical)
		targets.append({"row_number": int(entry["row_number"]), "canonical": canonical})
	targets.sort(key=lambda entry: entry["row_number"])
	return targets


def _is_transient_db_conflict(exc):
	text = str(exc)
	return (
		isinstance(exc, frappe.QueryDeadlockError)
		or "Deadlock" in type(exc).__name__
		or "1213" in text
		or "1020" in text
	)


def _rollback_savepoint_quietly(sp_name):
	try:
		frappe.db.rollback(save_point=sp_name)
	except Exception:
		pass


def _record_batch_lead(batch_name, lead_name, is_new, row_number=0):
	"""Idempotently persist the `(import_batch, lead)` relation (L-25)."""
	if not batch_name or not lead_name:
		return None
	existing_outcome = frappe.db.get_value(
		"CSKH Import Batch Lead", {"import_batch": batch_name, "lead": lead_name}, "outcome"
	)
	if existing_outcome:
		return existing_outcome

	snapshot_has_lead = bool(frappe.db.exists("CRM Lead", lead_name))
	if not snapshot_has_lead:
		lead_committed = frappe.db.sql(
			"select name from `tabCRM Lead` where name = %s limit 1 lock in share mode",
			(lead_name,),
		)
		if not lead_committed:
			raise frappe.DoesNotExistError(f"CRM Lead {lead_name} does not exist.")

	outcome = "created" if is_new else "reused"
	sp_name = f"cskh_ibl_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(sp_name)
	try:
		doc = frappe.get_doc(
			{
				"doctype": "CSKH Import Batch Lead",
				"import_batch": batch_name,
				"lead": lead_name,
				"outcome": outcome,
				"row_number": max(0, int(row_number or 0)),
			}
		)
		if not (snapshot_has_lead and frappe.db.exists("CSKH Import Batch", batch_name)):
			doc.flags.ignore_links = True
		doc.insert(ignore_permissions=True)
		return outcome
	except Exception as exc:
		if _is_transient_db_conflict(exc):
			raise
		_rollback_savepoint_quietly(sp_name)
		text = str(exc)
		if (
			"Duplicate" in text
			or "1062" in text
			or "already exists" in text
			or isinstance(exc, frappe.ValidationError)
		):
			rows = frappe.db.sql(
				"select outcome from `tabCSKH Import Batch Lead` "
				"where import_batch = %s and lead = %s limit 1 lock in share mode",
				(batch_name, lead_name),
			)
			if rows:
				return rows[0][0]
		raise


def _reconcile_counts(targets, batch):
	"""Reconcile the created/reused/failed counters from the **database** (P1 §6.4, L-25)."""
	batch_created = frappe.db.get_value("CSKH Import Batch", batch.name, "creation")
	linked_rows = frappe.db.sql(
		"select lead, outcome from `tabCSKH Import Batch Lead` where import_batch = %s lock in share mode",
		(batch.name,),
		as_dict=True,
	)
	linked = {row.lead: row.outcome for row in linked_rows}

	bulk_index: dict[str, str] = {}
	all_phones = [entry["canonical"] for entry in targets if entry.get("canonical")]
	for idx in range(0, len(all_phones), 2000):
		chunk = all_phones[idx : idx + 2000]
		ph = ", ".join(["%s"] * len(chunk))
		for r in frappe.db.sql(
			f"select normalized_phone, canonical_lead from `tabCSKH Identity Index` where normalized_phone in ({ph}) lock in share mode",
			tuple(chunk),
			as_dict=True,
		):
			if r.normalized_phone and r.canonical_lead:
				bulk_index[r.normalized_phone] = r.canonical_lead

	created = 0
	reused = 0
	failed = 0
	missing = []
	for entry in targets:
		lead = bulk_index.get(entry["canonical"]) or identity_service.find_by_phone_current(
			entry["canonical"]
		)
		if not lead:
			lead = identity_service.find_lead_by_phone(entry["canonical"])
		if not lead:
			failed += 1
			missing.append({"row_number": entry["row_number"]})
			continue

		outcome = linked.get(lead)
		if not outcome:
			lead_creation = frappe.db.get_value("CRM Lead", lead, "creation")
			is_new_fallback = bool(batch_created and lead_creation and lead_creation >= batch_created)
			outcome = _record_batch_lead(
				batch.name,
				lead,
				is_new=is_new_fallback,
				row_number=int(entry["row_number"]),
			)
			linked[lead] = outcome

		if outcome == "created":
			created += 1
		else:
			reused += 1
	return created, reused, failed, missing


def _normalize_new_lead_name(raw_name):
	"""Sanitize mapped `name` and decide `(first_name, is_placeholder)` (P1 §5.5, L-25)."""
	sanitized = sanitize_cell(raw_name)[:MAX_DATA_FIELD_LENGTH].strip()
	if not sanitized or sanitized == PLACEHOLDER_FIRST_NAME:
		return PLACEHOLDER_FIRST_NAME, 1
	probe = sanitized[1:] if sanitized.startswith("'") else sanitized
	try:
		normalize_vn_phone(probe)
		return PLACEHOLDER_FIRST_NAME, 1
	except InvalidPhone:
		pass
	return sanitized, 0


def _resolve_lead_source(raw_source):
	"""Sanitize mapped `source` and ensure its `CRM Lead Source` target exists (L-25)."""
	sanitized = sanitize_cell(raw_source)[:MAX_DATA_FIELD_LENGTH].strip()
	if not sanitized:
		return None
	existing = frappe.db.get_value("CRM Lead Source", {"name": sanitized}, "name")
	if existing:
		return existing

	sp_name = f"cskh_src_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(sp_name)
	try:
		src_doc = frappe.get_doc({"doctype": "CRM Lead Source", "source_name": sanitized})
		src_doc.insert(ignore_permissions=True)
		return src_doc.name
	except Exception as exc:
		if _is_transient_db_conflict(exc):
			raise
		_rollback_savepoint_quietly(sp_name)
		if "Duplicate" in str(exc) or "1062" in str(exc):
			locked = frappe.db.sql(
				"select name from `tabCRM Lead Source` where name = %s limit 1 lock in share mode",
				(sanitized,),
			)
			if locked:
				return locked[0][0]
		raise


def _extract_row_mapped_values(row):
	"""Read raw mapped `(name, source)` from an enriched worker row or test dict."""
	if not isinstance(row, dict):
		return None, None
	raw_name = row.get("_mapped_name")
	if raw_name is None and "name" in row:
		raw_name = row.get("name")
	raw_source = row.get("_mapped_source")
	if raw_source is None and "source" in row:
		raw_source = row.get("source")
	return raw_name, raw_source


def _ensure_lead(canonical_phone, row):
	"""Return the canonical lead for a phone number, creating it when needed (L-25)."""
	existing_lead = identity_service.find_by_phone(canonical_phone)
	if existing_lead:
		return existing_lead, False
	existing_lead = identity_service.find_lead_by_phone(canonical_phone)
	if existing_lead:
		identity_service.ensure_identity_for_lead(existing_lead)
		return existing_lead, False

	raw_name, raw_source = _extract_row_mapped_values(row)
	first_name, is_placeholder = _normalize_new_lead_name(raw_name)
	resolved_source = _resolve_lead_source(raw_source)

	sp_lead = f"cskh_lead_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(sp_lead)

	lead_payload = {
		"doctype": "CRM Lead",
		"first_name": first_name,
		"mobile_no": canonical_phone,
		"lead_owner": None,
		"cskh_is_placeholder_name": is_placeholder,
	}
	if resolved_source:
		lead_payload["source"] = resolved_source

	lead = frappe.get_doc(lead_payload)
	if resolved_source and not frappe.db.exists("CRM Lead Source", resolved_source):
		lead.flags.ignore_links = True
	try:
		lead.insert(ignore_permissions=True)
	except Exception as exc:
		if _is_transient_db_conflict(exc):
			raise
		_rollback_savepoint_quietly(sp_lead)
		if "Duplicate" not in str(exc) and "1062" not in str(exc):
			raise
		winner = identity_service.find_by_phone_current(canonical_phone)
		if not winner:
			raise
		return winner, False

	try:
		winner = identity_service.claim_canonical_identity(canonical_phone, lead.name, allow_adopt=False)
	except Conflict:
		_rollback_savepoint_quietly(sp_lead)
		winner = identity_service.find_by_phone_current(canonical_phone)
		if not winner:
			raise
		return winner, False
	except Exception as exc:
		if _is_transient_db_conflict(exc):
			raise
		_rollback_savepoint_quietly(sp_lead)
		raise

	if winner != lead.name:
		_rollback_savepoint_quietly(sp_lead)
		return winner, False

	return lead.name, True


_DEFAULT_ENSURE_LEAD = _ensure_lead
_DEFAULT_RECORD_BATCH_LEAD = _record_batch_lead
