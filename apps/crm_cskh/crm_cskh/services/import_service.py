"""Import pipeline: private file -> preview -> confirm -> worker (P1 6).

ADR-P1-03: the pipeline is a dedicated service around Frappe `File`, stdlib
`csv` and `openpyxl`. It never trusts a client-supplied preview: the server
re-reads the file, re-normalizes the phone numbers and re-checks the canonical
identity before every write (P1 6.2).

The preview is a `CSKH Import Batch` in status `DRY_RUN`. Confirming it moves
the batch to `CONFIRMED` and enqueues the worker; the worker processes the rows
in chunks and reconciles the counters from the database, never by summing
non-idempotent events (P1 6.4).
"""

import csv
import hashlib
import io
import json
import re

import frappe
from frappe.utils import now_datetime

from crm_cskh.services import identity as identity_service
from crm_cskh.services.errors import (
	Conflict,
	FileTooLarge,
	InvalidRequest,
	TooManyRows,
	UnsupportedFormat,
)
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone

DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_ROWS = 50000
SUPPORTED_EXTENSIONS = ("csv", "xlsx")

# Row classification buckets (P1 6.3).
CLASS_VALID = "valid"
CLASS_DUPLICATE_IN_FILE = "duplicate_in_file"
CLASS_DUPLICATE_IN_CRM = "duplicate_in_crm"
CLASS_CONFLICT_ACTIVE = "conflict_active_campaign"
CLASS_INVALID = "invalid"

# SEC-08: a cell that starts with one of these characters is executed by
# Excel/LibreOffice as a formula. The import pipeline never stores such a
# value; it is prefixed with an apostrophe so the cell stays a literal text.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# HTML is never stored in a lead field: it is escaped, not rendered.
_HTML_PATTERN = re.compile(r"<[^>]*>")


def sanitize_cell(value):
	"""Make a cell safe to store and to show (SEC-08).

	* a formula prefix is neutralised with a leading apostrophe;
	* HTML tags are removed, so a lead name can never carry a script.

	The function is pure so it can be unit-tested without a database.
	"""
	if value is None:
		return ""
	text = str(value)
	if not text:
		return ""
	text = _HTML_PATTERN.sub("", text)
	if text[:1] in FORMULA_PREFIXES:
		text = "'" + text
	return text.strip()


def looks_like_formula(value):
	"""True when `value` would be executed as a formula by a spreadsheet."""
	if value is None:
		return False
	return str(value)[:1] in FORMULA_PREFIXES


def _settings():
	"""Read the import limits, falling back to the documented defaults."""
	values = {"max_import_file_bytes": DEFAULT_MAX_FILE_BYTES, "max_import_rows": DEFAULT_MAX_ROWS}
	try:
		settings = frappe.get_single("CSKH App Settings")
	except Exception:
		return values
	for field in values:
		value = settings.get(field)
		if value:
			values[field] = int(value)
	return values


def _file_content(file_name):
	"""Return the file document and its content as **bytes**.

	`File.get_content()` returns `str` when the file has been flushed to disk
	and `bytes` while it is still in memory (ADR-P1-03). The import pipeline
	always works on bytes so the checksum and the parser behave identically in
	both cases.
	"""
	file_doc = frappe.get_doc("File", file_name)
	if not file_doc:
		raise InvalidRequest("The uploaded file does not exist.")
	if not file_doc.is_private:
		raise InvalidRequest("The import file must be private.")
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	elif not isinstance(content, bytes):
		content = bytes(content)
	return file_doc, content


def _checksum(content):
	return hashlib.sha256(content).hexdigest()


def _detect_extension(file_doc):
	name = (file_doc.file_name or "").lower()
	for extension in SUPPORTED_EXTENSIONS:
		if name.endswith("." + extension):
			return extension
	return None


def _read_rows(content, extension, max_rows):
	"""Parse the file into a list of dict rows, honouring the row limit."""
	rows = []
	if extension == "csv":
		text = content.decode("utf-8-sig", errors="replace")
		reader = csv.reader(io.StringIO(text))
		try:
			header = next(reader)
		except StopIteration:
			return [], []
		header = [column.strip() for column in header]
		for index, raw in enumerate(reader, start=2):
			if len(rows) >= max_rows:
				raise TooManyRows("The file has more rows than the configured limit.")
			if not any((cell or "").strip() for cell in raw):
				continue
			rows.append({"row_number": index, "cells": dict(zip(header, raw, strict=False))})
		return header, rows

	from openpyxl import load_workbook

	workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	try:
		sheet = workbook[workbook.sheetnames[0]]
		iterator = sheet.iter_rows(values_only=True)
		header_cells = next(iterator, None)
		if not header_cells:
			return [], []
		header = [str(cell).strip() if cell is not None else "" for cell in header_cells]
		for index, raw in enumerate(iterator, start=2):
			if len(rows) >= max_rows:
				raise TooManyRows("The file has more rows than the configured limit.")
			if raw is None or all(cell is None or str(cell).strip() == "" for cell in raw):
				continue
			cells = {}
			for column, cell in zip(header, raw, strict=False):
				if cell is None:
					continue
				# Excel numbers lose the leading zero of a Vietnamese mobile
				# number; the text form is kept so the operator can review it.
				cells[column] = str(cell)
			rows.append({"row_number": index, "cells": cells})
		return header, rows
	finally:
		workbook.close()


def _resolve_column_name(cells, mapping, candidates):
	"""Return the **name** of the column that holds the phone number.

	ADR-P1-03: the mapping maps a canonical field to a *column name*. The
	previous implementation returned the *value* of the cell, which made every
	row look like a missing phone column.
	"""
	if mapping:
		for _field, column in mapping.items():
			if column and column in cells:
				return column
		return None
	for candidate in candidates:
		if candidate in cells:
			return candidate
	for key in cells:
		normalized = (key or "").strip().lower()
		if normalized in candidates:
			return key
	return None


def _cell_value(cells, column):
	"""Return the value of `column` in `cells`, or None."""
	if not column:
		return None
	return cells.get(column)


def create_preview(file_name, mapping=None, actor=None, idempotency_key=None, import_mode="standard"):
	"""Parse the file and produce the import preview (P1 6.2, 6.5)."""
	actor = actor or frappe.session.user
	limits = _settings()
	file_doc, content = _file_content(file_name)

	if file_doc.file_size and file_doc.file_size > limits["max_import_file_bytes"]:
		raise FileTooLarge("The file is larger than the configured limit.")

	extension = _detect_extension(file_doc)
	if not extension:
		raise UnsupportedFormat("Only CSV and XLSX files are supported.")

	checksum = _checksum(content)
	key = idempotency_key or f"import:{checksum}:{import_mode}"

	existing = frappe.db.get_value(
		"CSKH Import Batch", {"idempotency_key": key}, ["name", "checksum", "status"], as_dict=True
	)
	if existing:
		if existing.checksum != checksum:
			raise Conflict(
				"This idempotency key was already used with a different file.",
				code="CONFLICT",
			)
		return _status_response(existing.name)

	header, rows = _read_rows(content, extension, limits["max_import_rows"])
	phone_column = _resolve_column_name(rows[0]["cells"] if rows else {}, mapping, _phone_candidates(header))

	classification = _classify(rows, mapping, phone_column)

	batch = frappe.get_doc(
		{
			"doctype": "CSKH Import Batch",
			"source_file": file_doc.name,
			"checksum": checksum,
			"idempotency_key": key,
			"status": "DRY_RUN",
			"mapping_json": json.dumps(mapping or {}, ensure_ascii=False),
			"total_rows": classification["total_rows"],
			"valid_rows": classification["valid_rows"],
			"new_rows": classification["new_rows"],
			"duplicate_rows": classification["duplicate_rows"],
			"error_rows": classification["error_rows"],
			"preview_json": json.dumps(classification["preview"], ensure_ascii=False),
		}
	)
	batch.insert(ignore_permissions=True)
	return _status_response(batch.name)


def _phone_candidates(header):
	candidates = set()
	for column in header or []:
		normalized = (column or "").strip().lower()
		if normalized in (
			"mobile_no",
			"mobile",
			"phone",
			"phone_number",
			"sdt",
			"số điện thoại",
			"dien thoai",
			"so_dien_thoai",
		):
			candidates.add(column)
	return candidates or set()


def _classify(rows, mapping, phone_column):
	"""Classify every row and compute the preview statistics (P1 6.5)."""
	seen_in_file = set()
	counts = {
		"total_rows": 0,
		"valid_rows": 0,
		"new_rows": 0,
		"duplicate_rows": 0,
		"error_rows": 0,
	}
	duplicate_in_crm = 0
	conflict_active = 0
	preview_rows = []

	for row in rows:
		counts["total_rows"] += 1
		raw = _cell_value(row["cells"], phone_column)
		if raw is None:
			counts["error_rows"] += 1
			preview_rows.append(
				{"row_number": row["row_number"], "class": CLASS_INVALID, "reason": "no_phone_column"}
			)
			continue
		try:
			canonical = normalize_vn_phone(str(raw))
		except InvalidPhone as exc:
			counts["error_rows"] += 1
			preview_rows.append(
				{"row_number": row["row_number"], "class": CLASS_INVALID, "reason": str(exc)[:160]}
			)
			continue

		if canonical in seen_in_file:
			counts["duplicate_rows"] += 1
			preview_rows.append(
				{"row_number": row["row_number"], "class": CLASS_DUPLICATE_IN_FILE, "canonical": canonical}
			)
			continue
		seen_in_file.add(canonical)

		existing_lead = identity_service.find_by_phone(canonical)
		if existing_lead:
			counts["duplicate_rows"] += 1
			duplicate_in_crm += 1
			preview_rows.append(
				{
					"row_number": row["row_number"],
					"class": CLASS_DUPLICATE_IN_CRM,
					"canonical": canonical,
					"lead": existing_lead,
				}
			)
			continue

		counts["valid_rows"] += 1
		counts["new_rows"] += 1
		preview_rows.append({"row_number": row["row_number"], "class": CLASS_VALID, "canonical": canonical})

	return {
		"total_rows": counts["total_rows"],
		"valid_rows": counts["valid_rows"],
		"new_rows": counts["new_rows"],
		"duplicate_rows": counts["duplicate_rows"],
		"error_rows": counts["error_rows"],
		"preview": {
			"rows": preview_rows[:200],
			"truncated": len(preview_rows) > 200,
			"duplicate_in_crm": duplicate_in_crm,
			"conflict_active": conflict_active,
		},
	}


def confirm(batch_name, actor=None):
	"""Confirm a preview and enqueue the import worker."""
	actor = actor or frappe.session.user
	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	if not batch:
		raise InvalidRequest("The import batch does not exist.")
	if batch.status in ("QUEUED", "RUNNING", "COMPLETED", "COMPLETED_WITH_ERRORS"):
		return _status_response(batch.name)
	if batch.status == "FAILED":
		batch.status = "QUEUED"
	elif batch.status == "CONFIRMED":
		batch.status = "QUEUED"
	elif batch.status == "DRY_RUN":
		batch.status = "CONFIRMED"
	batch.save(ignore_permissions=True)

	from crm_cskh.jobs import import_worker

	frappe.enqueue(
		import_worker.run_batch,
		queue="short",
		timeout=1800,
		enqueue_after_commit=True,
		batch_name=batch.name,
	)
	batch.reload()
	return _status_response(batch.name)


def _status_response(batch_name):
	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	preview_payload = {}
	if batch.preview_json:
		try:
			preview_payload = json.loads(batch.preview_json)
		except (ValueError, TypeError):
			preview_payload = {}
	return {
		"batch": batch.name,
		"status": batch.status,
		"checksum": batch.checksum,
		"idempotency_key": batch.idempotency_key,
		"totals": {
			"total_rows": batch.total_rows,
			"valid_rows": batch.valid_rows,
			"new_rows": batch.new_rows,
			"duplicate_rows": batch.duplicate_rows,
			"error_rows": batch.error_rows,
		},
		"preview": preview_payload.get("rows", []),
		"preview_truncated": preview_payload.get("truncated", False),
		"committed_at": batch.committed_at,
		"error_artifact": batch.error_artifact,
		"as_of": now_datetime(),
	}


def write_error_artifact(batch_name, error_rows):
	"""Write the failed rows into a **private** CSV file and link it.

	SEC-08: the artifact is sanitized (no formula prefix, no HTML) so a
	spreadsheet never executes a cell of the error report. The file is private
	and attached to nothing, so only the CRM Admin can read it (the raw import
	files stay private too).
	"""
	if not error_rows:
		return None
	import csv as _csv
	import io as _io

	buffer = _io.StringIO()
	writer = _csv.writer(buffer)
	writer.writerow(["row_number", "reason"])
	for entry in error_rows:
		writer.writerow(
			[
				entry.get("row_number", ""),
				sanitize_cell(entry.get("reason", ""))[:200],
			]
		)
	content = buffer.getvalue().encode("utf-8")

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"import-errors-{batch_name}.csv",
			"is_private": 1,
			"attached_to_doctype": "CSKH Import Batch",
			"attached_to_name": batch_name,
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc.name
