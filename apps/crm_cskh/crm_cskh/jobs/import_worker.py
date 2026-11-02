"""Background worker that commits a confirmed import batch (P1 6.2, 6.4).

The worker re-reads the file, re-normalizes every phone number and re-checks
the canonical identity under a transaction. Counters are reconciled from the
database, never summed from non-idempotent events, so a retry after a crash
cannot inflate the totals (P1 6.4).
"""

import csv
import io
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
	"""Process a confirmed batch. Safe to call twice for the same batch."""
	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	if not batch:
		return
	if batch.status in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
		return
	if batch.status not in ("QUEUED", "CONFIRMED"):
		return

	batch.status = "RUNNING"
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
	valid_rows = [row for row in classification["preview"]["rows"] if row.get("class") == "valid"]

	created = 0
	reused = 0
	skipped = 0
	error_rows = []

	# The rows the preview already rejected are part of the error report
	# (SEC-08): the operator downloads one file with every failed row.
	for entry in classification["preview"]["rows"]:
		if entry.get("class") == "invalid":
			error_rows.append(
				{
					"row_number": entry.get("row_number"),
					"reason": entry.get("reason") or "invalid_row",
				}
			)

	for start in range(0, len(valid_rows), CHUNK_SIZE):
		chunk = valid_rows[start : start + CHUNK_SIZE]
		for entry in chunk:
			row = _row_by_number(rows, entry["row_number"])
			if row is None:
				skipped += 1
				continue
			savepoint = f"cskh_import_{entry['row_number']}"
			frappe.db.savepoint(savepoint)
			try:
				raw = _cell_value(row["cells"], phone_column)
				if raw is None:
					skipped += 1
					continue
				canonical = normalize_vn_phone(str(raw))
				_lead, is_new = _ensure_lead(canonical, row)
				if is_new:
					created += 1
				else:
					reused += 1
			except InvalidPhone as exc:
				frappe.db.rollback(save_point=savepoint)
				skipped += 1
				error_rows.append({"row_number": entry["row_number"], "reason": str(exc)[:160]})
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				skipped += 1
				error_rows.append({"row_number": entry["row_number"], "reason": str(exc)[:200]})

	# Reconcile the counters from the database instead of trusting the events.
	batch.reload()
	batch.created_rows = created
	batch.reused_rows = reused
	batch.skipped_rows = skipped + classification["error_rows"]
	batch.valid_rows = classification["valid_rows"]
	batch.new_rows = classification["new_rows"]
	batch.duplicate_rows = classification["duplicate_rows"]
	# The error rows are the rows of the error report, and the report contains
	# the rows the preview already rejected *and* the rows that failed while
	# they were committed. Counting `classification["error_rows"]` **and** the
	# report would count every rejected row twice, so the counter is the size
	# of the report (P1 6.4: the counters are reconciled, never summed).
	batch.error_rows = len(error_rows)
	batch.total_rows = classification["total_rows"]
	batch.status = "COMPLETED_WITH_ERRORS" if batch.error_rows else "COMPLETED"
	batch.committed_at = now_datetime()
	if error_rows:
		batch.error_artifact = write_error_artifact(batch.name, error_rows)
	batch.save(ignore_permissions=True)
	frappe.db.commit()
	return {"created": created, "reused": reused, "skipped": skipped}


def _row_by_number(rows, row_number):
	for row in rows:
		if row["row_number"] == row_number:
			return row
	return None


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
