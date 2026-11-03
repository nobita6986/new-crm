"""Background worker that commits a confirmed import batch (P1 6.2, 6.4).

The worker re-reads the file, re-normalizes every phone number and re-checks
the canonical identity under a transaction. Counters are reconciled from the
database, never summed from non-idempotent events, so a retry after a crash
cannot inflate the totals (P1 6.4).
"""

import json
import re

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import identity as identity_service
from crm_cskh.services.errors import Conflict, FileTooLarge
from crm_cskh.services.import_service import (
	_cell_value,
	_classify,
	_detect_extension,
	_phone_candidates,
	_read_rows,
	_resolve_column_name,
	_settings,
	write_error_artifact,
)
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone

CHUNK_SIZE = 200
PLACEHOLDER_FIRST_NAME = "Chưa rõ tên"


def run_batch(batch_name):
	"""Process a confirmed batch. Safe to call twice for the same batch.

	**Crash recovery (P1 §6.4, P1 §12.4):** the worker commits its progress
	after every chunk and stores the number of processed rows in
	`CSKH Import Batch.progress`. A worker that dies in the middle of a batch
	therefore leaves the batch in `RUNNING` with a known progress, and the
	next attempt resumes **after** that progress instead of starting over.
	"""
	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	if not batch:
		return
	if batch.status in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
		return
	if batch.status not in ("QUEUED", "CONFIRMED", "RUNNING"):
		return

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

	# The worker applies the **same** mapping, the same phone column and the
	# same validation as the preview: the preview is a dry run of this exact
	# pipeline (P1 §6.2).
	phone_column = _resolve_column_name(rows[0]["cells"] if rows else {}, mapping, _phone_candidates(header))
	classification = _classify(rows, mapping, phone_column)

	created = 0
	reused = 0
	skipped = 0
	error_rows = []

	# The rows the preview already rejected are part of the error report
	# (SEC-08): the operator downloads one file with every failed row, even
	# beyond the 200-row UI preview slice.
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

	# The **distinct** canonical phones of the file. The reconciliation works
	# on this list, not on the preview classes: after the first attempt of a
	# resumed batch the rows the worker itself created are classified
	# `duplicate_in_crm`, so a reconciliation based on the classes would lose
	# them (P1 §6.4).
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
	row_lookup = {row["row_number"]: row for row in rows}

	for start in range(0, len(targets), CHUNK_SIZE):
		chunk = targets[start : start + CHUNK_SIZE]
		failed_rows = []
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
				_lead, _is_new = _ensure_lead(canonical, row)
			except InvalidPhone as exc:
				frappe.db.rollback(save_point=savepoint)
				row_num = int(entry["row_number"])
				blocked_at = min(blocked_at, row_num) if blocked_at is not None else row_num
				failed_rows.append(row_num)
				error_rows.append({"row_number": entry["row_number"], "reason": str(exc)[:160]})
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				row_num = int(entry["row_number"])
				blocked_at = min(blocked_at, row_num) if blocked_at is not None else row_num
				failed_rows.append(row_num)
				error_rows.append({"row_number": entry["row_number"], "reason": str(exc)[:200]})

		# **Chunk commit (P1 §6.4, P1 §7.5):** the progress of the batch is
		# committed after every chunk, so a worker that dies later resumes
		# from the last committed chunk instead of reprocessing the whole
		# file. The commit also bounds the size of the transaction (P1 §7.5:
		# "với batches lớn chia transaction thành chunks có kiểm soát").
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
	# the same totals as an uninterrupted one (P1 §6.4).
	batch.reload()
	created, reused, failed, missing = _reconcile_counts(targets, batch)
	skipped = failed
	for entry in missing:
		error_rows.append({"row_number": entry["row_number"], "reason": "lead_not_created"})
	# One **row** produces at most one line in the error report: the failed
	# row is reported once, with the reason of the failure that actually
	# happened (SEC-08: the operator gets one row per failed line).
	error_rows = _dedupe_error_rows(error_rows)
	batch.created_rows = created
	batch.reused_rows = reused
	batch.skipped_rows = skipped + classification["error_rows"]
	batch.valid_rows = classification["valid_rows"]
	batch.new_rows = classification["new_rows"]
	batch.duplicate_rows = classification["duplicate_rows"]
	worker_error_count = sum(1 for row in error_rows if row.get("row_number") not in preview_invalid_rows)
	batch.error_rows = classification["error_rows"] + worker_error_count
	batch.total_rows = classification["total_rows"]
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
	batch.save(ignore_permissions=True)
	frappe.db.commit()
	return {"created": created, "reused": reused, "skipped": skipped}


def _dedupe_error_rows(error_rows):
	"""Keep **one** line per failed row in the error report (SEC-08).

	A row can fail twice in the same attempt: the row itself failed, and the
	reconciliation then reports that the lead was not created. The operator
	must see one line per failed row, with the reason of the failure that
	actually happened — never two lines for the same row.
	"""
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


def _reconcile_counts(targets, batch):
	"""Reconcile the created/reused/failed counters from the **database**.

	P1 §6.4 forbids counting the success by summing non-idempotent events:
	the counters have to be read from the database, so a crash and a retry
	cannot inflate them.

	Whether a lead was **created by this batch** is read from the creation
	timestamp of the lead: a lead that was created **after** the batch existed
	was created by this batch (in this attempt or in an earlier one), a lead
	that existed before belongs to another batch and is therefore **reused**.

	The placeholder marker (P1 §6.2) alone is not enough: a lead created by an
	*earlier* batch is also a placeholder, so counting it as "created" would
	inflate the counter of the second batch.
	"""
	from crm_cskh.services import identity as identity_service

	batch_created = frappe.db.get_value("CSKH Import Batch", batch.name, "creation")
	created = 0
	reused = 0
	failed = 0
	missing = []
	for entry in targets:
		lead = identity_service.find_by_phone(entry["canonical"])
		if not lead:
			lead = identity_service.find_lead_by_phone(entry["canonical"])
		if not lead:
			failed += 1
			missing.append({"row_number": entry["row_number"]})
			continue
		lead_creation = frappe.db.get_value("CRM Lead", lead, "creation")
		is_placeholder = frappe.db.get_value("CRM Lead", lead, "cskh_is_placeholder_name")
		if is_placeholder and batch_created and lead_creation and lead_creation >= batch_created:
			created += 1
		else:
			reused += 1
	return created, reused, failed, missing


def _ensure_lead(canonical_phone, row):
	"""Return the canonical lead for a phone number, creating it when needed.

	An existing lead is reused as-is: the import never overwrites the owner,
	the status or any manual field of a lead that already exists (P1 6.3, T1-06).

	The lookup covers the leads that are **not** registered in the identity
	index yet: a customer that entered the CRM before P1 must be adopted, not
	duplicated.

	**Race safety (P1 12.4):** two workers may create a lead for the same phone
	at the same time. The `CSKH Identity Index` unique index on
	`normalized_phone` is the arbiter: the loser of the race deletes the lead
	it created and adopts the winner's lead, so exactly one canonical lead
	survives.
	"""
	existing_lead = identity_service.find_by_phone(canonical_phone)
	if not existing_lead:
		existing_lead = identity_service.find_lead_by_phone(canonical_phone)
	if existing_lead:
		identity_service.ensure_identity_for_lead(existing_lead)
		return existing_lead, False

	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": PLACEHOLDER_FIRST_NAME,
			"mobile_no": canonical_phone,
			"lead_owner": None,
			"cskh_is_placeholder_name": 1,
		}
	)
	try:
		lead.insert(ignore_permissions=True)
	except Exception as exc:
		if "Duplicate" not in str(exc) and "1062" not in str(exc):
			raise
		# Another worker won the race; reuse its canonical lead.
		winner = identity_service.find_by_phone(canonical_phone)
		if not winner:
			raise
		return winner, False

	try:
		winner = identity_service.claim_canonical_identity(canonical_phone, lead.name, allow_adopt=False)
	except Conflict:
		# Another writer claimed the phone while this lead was being created.
		winner = identity_service.find_by_phone(canonical_phone)
		if not winner:
			raise
	if winner != lead.name:
		# The duplicate lead is removed; the canonical one is kept.
		frappe.delete_doc("CRM Lead", lead.name, force=True, ignore_permissions=True)
		return winner, False

	return lead.name, True
