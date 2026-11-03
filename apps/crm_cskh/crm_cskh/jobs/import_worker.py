"""Background worker that commits a confirmed import batch (P1 6.2, 6.4, L-25).

The worker re-reads the file, re-normalizes every phone number and re-checks
the canonical identity under a transaction. Counters are reconciled from the
database (`CSKH Import Batch Lead` + `CSKH Identity Index`), never summed from
non-idempotent events, so a retry after a crash or a concurrent race cannot
inflate the totals (P1 6.4).
"""

import json
import re
import time

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import identity as identity_service
from crm_cskh.services.errors import Conflict, FileTooLarge
from crm_cskh.services.import_service import (
	_cell_value,
	_classify,
	_detect_extension,
	_read_rows,
	_settings,
	resolve_import_columns,
	sanitize_cell,
	write_error_artifact,
)
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone

CHUNK_SIZE = 200
PLACEHOLDER_FIRST_NAME = "Chưa rõ tên"
MAX_DATA_FIELD_LENGTH = 140


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


def _process(batch):
	file_doc = frappe.get_doc("File", batch.source_file)
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	extension = _detect_extension(file_doc)

	limits = _settings()
	if file_doc.file_size and file_doc.file_size > limits["max_import_file_bytes"]:
		raise FileTooLarge("The file is larger than the configured limit.")

	# The worker applies the **same** limits as the preview, so a file that
	# the preview accepted can never be rejected by the worker (ADR-P1-08).
	header, rows = _read_rows(content, extension, limits["max_import_rows"])

	mapping = {}
	if batch.mapping_json:
		try:
			mapping = json.loads(batch.mapping_json)
		except (ValueError, TypeError):
			mapping = {}

	# The worker applies the **same** mapping, the same columns and the
	# same validation as the preview: the preview is a dry run of this exact
	# pipeline (P1 §6.2, L-25).
	resolved = resolve_import_columns(header, rows[0]["cells"] if rows else {}, mapping)
	phone_column = resolved["phone"]
	name_column = resolved["name"]
	source_column = resolved["source"]
	classification = _classify(
		rows,
		mapping,
		phone_column,
		name_column=name_column,
		source_column=source_column,
	)

	created = 0
	reused = 0
	skipped = 0
	error_rows = []

	# The rows the preview already rejected are part of the error report
	# (SEC-08, L-20): the operator downloads one file with every failed row,
	# including rows beyond the 200-row UI preview slice.
	preview_invalid_rows = set()
	for entry in classification["rows"]:
		if entry.get("class") == "invalid":
			preview_invalid_rows.add(entry.get("row_number"))
			error_rows.append(
				{
					"row_number": entry.get("row_number"),
					"reason": entry.get("reason") or "invalid_row",
				}
			)

	# The **distinct** canonical phones of the file (L-19: built from the full
	# `resolvable` list, never from the 200-row UI preview slice). After the
	# first attempt of a resumed batch the rows the worker itself created are
	# classified `duplicate_in_crm`, so a reconciliation based on the classes
	# would lose them (P1 §6.4).
	targets = _distinct_targets(classification)

	# **Resume point (P1 §6.4):** the rows that a previous, crashed attempt
	# already committed are skipped. The resume marker is the **row number** of
	# the last row that was committed **without** a failure before it, so a
	# row that failed (and was rolled back) is retried by the next attempt
	# instead of being silently lost.
	resume_from = int(batch.progress or 0)
	# The marker of the committed progress. It only advances while **no** row
	# failed: after a failure the marker must not skip the failed row.
	safe_progress = resume_from
	# The earliest row that blocked the progress (`None` while everything is fine).
	blocked_at = None
	row_lookup = {}
	for row in rows:
		enriched = dict(row)
		if name_column:
			enriched["_mapped_name"] = _cell_value(row["cells"], name_column)
		if source_column:
			enriched["_mapped_source"] = _cell_value(row["cells"], source_column)
		row_lookup[row["row_number"]] = enriched

	for start in range(0, len(targets), CHUNK_SIZE):
		chunk = targets[start : start + CHUNK_SIZE]
		chunk_error_rows = []
		failed_rows = []
		for chunk_attempt in range(5):
			chunk_error_rows = []
			failed_rows = []
			try:
				for entry in chunk:
					row = row_lookup.get(entry["row_number"])
					if row is None:
						continue
					if int(entry["row_number"]) <= resume_from:
						# Already committed by a previous attempt: skip it.
						continue
					savepoint = f"cskh_import_{entry['row_number']}"
					frappe.db.savepoint(savepoint)
					try:
						raw = _cell_value(row["cells"], phone_column)
						if raw is None:
							continue
						canonical = normalize_vn_phone(str(raw))
						lead_name, is_new = _ensure_lead(canonical, row)
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
						chunk_error_rows.append({"row_number": entry["row_number"], "reason": str(exc)[:160]})
					except Exception as exc:
						if _is_transient_db_conflict(exc) and chunk_attempt < 4:
							raise
						_rollback_savepoint_quietly(savepoint)
						row_num = int(entry["row_number"])
						failed_rows.append(row_num)
						chunk_error_rows.append({"row_number": entry["row_number"], "reason": str(exc)[:200]})
				break
			except Exception as exc:
				if not (_is_transient_db_conflict(exc) and chunk_attempt < 4):
					raise
				frappe.db.rollback()
				batch.reload()
				time.sleep(0.03 * (chunk_attempt + 1))

		error_rows.extend(chunk_error_rows)
		# **Chunk commit (P1 §6.4, P1 §7.5):** the progress of the batch is
		# committed after every chunk, so a worker that dies later resumes
		# from the last committed chunk instead of reprocessing the whole
		# file.
		batch.reload()
		if failed_rows:
			first_failed = min(failed_rows)
			blocked_at = min(blocked_at, first_failed) if blocked_at is not None else first_failed
		if blocked_at is not None:
			# Never advance the resume cursor past an earlier failed row, even
			# when subsequent chunks have zero failures; rows already committed
			# in later chunks are safely adopted via CSKH Identity Index on retry.
			safe_progress = max(resume_from, blocked_at - 1)
		else:
			safe_progress = max(
				safe_progress,
				max((int(entry["row_number"]) for entry in chunk), default=0),
			)
		batch.progress = safe_progress
		batch.save(ignore_permissions=True)
		frappe.db.commit()

	# Reconcile the counters from the database instead of trusting the events.
	# The counts are read from the **database**, so a resumed batch reports
	# the same totals as an uninterrupted one (P1 §6.4, L-25).
	batch.reload()
	created, reused, failed, missing = _reconcile_counts(targets, batch)
	skipped = failed
	for entry in missing:
		error_rows.append({"row_number": entry["row_number"], "reason": "lead_not_created"})
	# One **row** produces at most one line in the error report: the failed
	# row is reported once, with the reason of the failure that actually
	# happened (SEC-08, L-20: the operator gets one row per failed line).
	error_rows = _dedupe_error_rows(error_rows)
	preview_error_count = len(preview_invalid_rows)
	worker_error_count = sum(1 for row in error_rows if row.get("row_number") not in preview_invalid_rows)

	batch.created_rows = created
	batch.reused_rows = reused
	batch.skipped_rows = skipped + preview_error_count
	# Preserve the original preview classification counters (`valid_rows`,
	# `new_rows`, `duplicate_rows`) across crash-resume attempts: on a resumed
	# attempt `_classify` sees leads created by attempt #1 as `duplicate_in_crm`.
	if not int(batch.total_rows or 0):
		batch.total_rows = classification["total_rows"]
		batch.valid_rows = classification["valid_rows"]
		batch.new_rows = classification["new_rows"]
		batch.duplicate_rows = classification["duplicate_rows"]
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
	if error_rows:
		batch.error_artifact = write_error_artifact(batch.name, error_rows)
	else:
		batch.error_artifact = None
		batch.error = None
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
	"""Return the **distinct** canonical phones of the file, in row order.

	Two rows with the same phone are **one** customer: the second one is a
	`duplicate_in_file` in the preview and must not create a second lead
	(P1 §6.3). The reconciliation therefore works on the distinct phones.
	"""
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
	"""Idempotently persist the `(import_batch, lead)` relation (L-25).

	* Preserves the initial `outcome` (`created` vs `reused`) if `(batch_name, lead_name)`
	  was already recorded by an earlier attempt before a crash or retry.
	* Avoids `LOCK IN SHARE MODE` before `INSERT` so non-existent keys never acquire
	  InnoDB `S-gap` locks; uses `LOCK IN SHARE MODE` only when confirming a row
	  already committed in a newer MVCC snapshot.
	"""
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
	"""Reconcile the created/reused/failed counters from the **database** (P1 §6.4, L-25).

	Reads canonical identity resolution (`CSKH Identity Index`) together with
	the persisted `CSKH Import Batch Lead` relation (`outcome = created | reused`):
	* Newly created leads with mapped real names (`cskh_is_placeholder_name = 0`)
	  AND newly created leads with placeholder names (`cskh_is_placeholder_name = 1`)
	  are both counted accurately as `created`.
	* Existing canonical leads reused by subsequent batches (or adopted after a
	  concurrent multi-batch race) are counted as `reused`, never inflating `created`.
	* Crash-resume retries read the persisted `(import_batch, lead)` outcome and
	  never double-count.
	"""
	batch_created = frappe.db.get_value("CSKH Import Batch", batch.name, "creation")
	linked_rows = frappe.db.sql(
		"select lead, outcome from `tabCSKH Import Batch Lead` where import_batch = %s lock in share mode",
		(batch.name,),
		as_dict=True,
	)
	linked = {row.lead: row.outcome for row in linked_rows}

	created = 0
	reused = 0
	failed = 0
	missing = []
	for entry in targets:
		lead = identity_service.find_by_phone_current(entry["canonical"])
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
	"""Sanitize mapped `name` and decide `(first_name, is_placeholder)` (P1 §5.5, L-25).

	* Strips HTML tags and neutralises spreadsheet formula prefixes (`sanitize_cell`).
	* Falls back to `PLACEHOLDER_FIRST_NAME` (`cskh_is_placeholder_name = 1`) ONLY
	  when a valid name is missing: empty/whitespace/empty-HTML, explicit
	  placeholder text, or a phone number placed into the name column (P1 §5.5).
	"""
	sanitized = sanitize_cell(raw_name)[:MAX_DATA_FIELD_LENGTH].strip()
	if not sanitized or sanitized == PLACEHOLDER_FIRST_NAME:
		return PLACEHOLDER_FIRST_NAME, 1
	# P1 §5.5: never store a phone number in `first_name` as if it were a real name.
	probe = sanitized[1:] if sanitized.startswith("'") else sanitized
	try:
		normalize_vn_phone(probe)
		return PLACEHOLDER_FIRST_NAME, 1
	except InvalidPhone:
		pass
	return sanitized, 0


def _resolve_lead_source(raw_source):
	"""Sanitize mapped `source` and ensure its `CRM Lead Source` target exists (L-25).

	`CRM Lead.source` is a `Link` to `CRM Lead Source` (`autoname = field:source_name`).
	Sanitizing before lookup/insert guarantees no HTML or raw formula prefix enters
	either `tabCRM Lead Source` or `tabCRM Lead`, while creating missing sources
	under an inner savepoint avoids both `LinkValidationError` and race failures.
	"""
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
	"""Return the canonical lead for a phone number, creating it when needed (L-25).

	* **Existing canonical lead:** reused strictly as-is. Import never overwrites
	  `first_name`, `lead_name`, `cskh_is_placeholder_name`, `source`,
	  `lead_owner`, `status`, or notes of a canonical lead that already exists
	  (P1 §5.5, §6.3, T1-06, L-25).
	* **New canonical lead:** applies sanitized `name` / `source` mapping, using
	  `PLACEHOLDER_FIRST_NAME` (`cskh_is_placeholder_name = 1`) only when a valid
	  name is missing.
	* **Race safety (P1 §12.4):** two workers may attempt to create a lead for
	  the same phone simultaneously. `CSKH Identity Index` (`normalized_phone`
	  unique) arbitrates; the loser rolls back its provisional lead via savepoint
	  and adopts the winner's canonical lead untouched (`is_new = False`).
	"""
	existing_lead = identity_service.find_by_phone(canonical_phone)
	if not existing_lead:
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
