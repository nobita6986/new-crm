"""Import pipeline: private file -> preview -> confirm -> worker (P1 6).

ADR-P1-03: the pipeline is a dedicated service around Frappe `File`, stdlib
`csv` and `openpyxl`. It never trusts a client-supplied preview: the server
re-reads the file, re-normalizes the phone numbers and re-checks the canonical
identity before every write (P1 6.2).

The preview is a `CSKH Import Batch` in status `DRY_RUN`. Confirming it walks
the valid state transitions (`DRY_RUN -> CONFIRMED -> QUEUED`) and enqueues the
worker; the worker processes the rows in chunks and reconciles the counters
from the database, never by summing non-idempotent events (P1 6.4).
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


def sanitize_cell(value, allow_leading_sign=False):
	"""Make a cell safe to store and to show (SEC-08).

	* a formula prefix is neutralised with a leading apostrophe;
	* HTML tags are removed, so a lead name can never carry a script.

	`allow_leading_sign` keeps a leading `+` or `-` that belongs to a
	**value** and not to a formula: a canonical phone number `+84912345678` is
	a valid number and must not be broken (L-13). The sign is still a formula
	prefix in a spreadsheet, so the caller that writes the cell to a CSV keeps
	escaping it there.

	The function is pure so it can be unit-tested without a database.
	"""
	if value is None:
		return ""
	text = str(value)
	if not text:
		return ""
	text = _HTML_PATTERN.sub("", text)
	text = text.strip()
	if not text:
		return ""
	if text[:1] in FORMULA_PREFIXES:
		if allow_leading_sign and text[:1] in ("+", "-"):
			# A leading sign followed by a digit is a number, not a formula.
			if re.match(r"^[+-]?\d", text):
				return text
		text = "'" + text
	return text


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
	if not file_name or not frappe.db.exists("File", file_name):
		raise InvalidRequest("The uploaded file does not exist.")
	file_doc = frappe.get_doc("File", file_name)
	if not file_doc.is_private:
		raise InvalidRequest("The import file must be private.")
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	elif not isinstance(content, bytes):
		content = bytes(content)
	return file_doc, content


def _checksum(content):
	"""Return the SHA-256 of the content.

	The content is normalized to **bytes** first: `File.get_content()` returns
	`str` when the file has been flushed to disk and `bytes` while it is still
	in memory, and `hashlib.sha256` refuses a `str` (L-02).
	"""
	if isinstance(content, str):
		content = content.encode("utf-8")
	elif not isinstance(content, bytes):
		content = bytes(content)
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


# The canonical fields of the import mapping (L-03, L-25).
PHONE_FIELD = "phone"
NAME_FIELD = "name"
SOURCE_FIELD = "source"
NAME_MAPPING_KEYS = ("name", "lead_name", "first_name", "full_name")
SOURCE_MAPPING_KEYS = ("source", "lead_source")


def _canonical_mapping(mapping):
	"""Normalize a mapping dict for deterministic storage and conflict comparison."""
	if not mapping or not isinstance(mapping, dict):
		return {}
	cleaned = {}
	for key, val in mapping.items():
		if val is None:
			continue
		text = str(val).strip()
		if text:
			cleaned[str(key).strip()] = text
	return cleaned


def _available_columns(header=None, cells=None):
	"""Return ordered list of column names from `header` and/or first row `cells`."""
	ordered = []
	seen = set()
	for col in list(header or []) + list((cells or {}).keys()):
		if col and col not in seen:
			seen.add(col)
			ordered.append(col)
	return ordered


def _resolve_column_name(cells, mapping, candidates, header=None):
	"""Return the **name** of the column that holds the phone number.

	ADR-P1-03: the mapping maps a canonical field to a *column name*. The
	previous implementation returned the *value* of the cell, which made every
	row look like a missing phone column.

	When the mapping carries several fields, the column of the canonical
	`phone` field wins: taking the first mapped column would read the name
	column as a phone number (L-03).
	"""
	available = set(_available_columns(header=header, cells=cells))
	lowered_candidates = {(c or "").strip().lower() for c in (candidates or ())}
	if mapping:
		mapped = mapping.get(PHONE_FIELD)
		if mapped and mapped in available:
			return mapped
		if mapped:
			mapped_norm = str(mapped).strip().lower()
			for col in _available_columns(header=header, cells=cells):
				if col.strip().lower() == mapped_norm:
					return col
		# A mapping without an explicit phone field: accept a mapped column
		# only when it is one of the recognised phone headers, otherwise the
		# mapping is incomplete and the **header** is used to find the phone
		# column. The candidates come from the header of the file, never from
		# the client, so the fallback cannot be abused (L-03).
		for _field, column in mapping.items():
			if column and column in available and (column or "").strip().lower() in lowered_candidates:
				return column
		# No mapped column is a phone column: fall back to the phone header
		# of the file, exactly like the unmapped path.
		for candidate in candidates:
			if candidate in available:
				return candidate
		for key in _available_columns(header=header, cells=cells):
			if (key or "").strip().lower() in lowered_candidates:
				return key
		return None
	for candidate in candidates:
		if candidate in available:
			return candidate
	for key in _available_columns(header=header, cells=cells):
		normalized = (key or "").strip().lower()
		if normalized in lowered_candidates:
			return key
	return None


def _resolve_optional_column_name(header, cells, mapping, field_keys, candidates, exclude=()):
	"""Resolve an optional mapped column (`name` or `source`) safely (L-25).

	* When the caller explicitly maps one of `field_keys` (e.g. `mapping["name"]`),
	  match that exact or case-folded column in the file header (never falling back
	  to a random column if the explicit column does not exist, and never reusing
	  an excluded column such as `phone_column`).
	* When `mapping` omits `field_keys`, auto-detect from `candidates` in `header`.
	"""
	excluded = {col for col in (exclude or ()) if col}
	columns = [col for col in _available_columns(header=header, cells=cells) if col not in excluded]
	available = set(columns)

	if isinstance(mapping, dict):
		explicit_specified = False
		for key in field_keys:
			raw_target = mapping.get(key)
			if raw_target is not None and str(raw_target).strip() != "":
				explicit_specified = True
				target = str(raw_target).strip()
				if target in available:
					return target
				target_lower = target.lower()
				for col in columns:
					if col.strip().lower() == target_lower:
						return col
		if explicit_specified:
			return None

	lowered_candidates = {(c or "").strip().lower() for c in (candidates or ())}
	for candidate in candidates or ():
		if candidate in available:
			return candidate
	for col in columns:
		if col.strip().lower() in lowered_candidates:
			return col
	return None


def resolve_import_columns(header, cells, mapping):
	"""Resolve canonical `phone`, `name` and `source` column names for an import (L-25)."""
	phone_column = _resolve_column_name(cells, mapping, _phone_candidates(header), header=header)
	name_column = _resolve_optional_column_name(
		header,
		cells,
		mapping,
		NAME_MAPPING_KEYS,
		_name_candidates(header),
		exclude=(phone_column,),
	)
	source_column = _resolve_optional_column_name(
		header,
		cells,
		mapping,
		SOURCE_MAPPING_KEYS,
		_source_candidates(header),
		exclude=(phone_column, name_column),
	)
	return {
		"phone": phone_column,
		"name": name_column,
		"source": source_column,
	}


def _cell_value(cells, column):
	"""Return the value of `column` in `cells`, or None."""
	if not column:
		return None
	return cells.get(column)


def create_preview(file_name, mapping=None, actor=None, idempotency_key=None, import_mode="standard"):
	"""Parse the file and produce the import preview (P1 6.2, 6.5)."""
	actor = actor or frappe.session.user
	if mapping is not None and not isinstance(mapping, dict):
		raise InvalidRequest("Column mapping must be a JSON object.")

	limits = _settings()
	file_doc, content = _file_content(file_name)

	if file_doc.file_size and file_doc.file_size > limits["max_import_file_bytes"]:
		raise FileTooLarge("The file is larger than the configured limit.")

	extension = _detect_extension(file_doc)
	if not extension:
		raise UnsupportedFormat("Only CSV and XLSX files are supported.")

	checksum = _checksum(content)
	normalized_mapping = _canonical_mapping(mapping)
	if idempotency_key:
		key = idempotency_key
	elif normalized_mapping:
		mapping_digest = hashlib.sha256(
			json.dumps(normalized_mapping, sort_keys=True, ensure_ascii=False).encode("utf-8")
		).hexdigest()[:12]
		key = f"import:{checksum}:{import_mode}:{mapping_digest}"
	else:
		key = f"import:{checksum}:{import_mode}"

	existing = frappe.db.get_value(
		"CSKH Import Batch",
		{"idempotency_key": key},
		["name", "checksum", "status", "mapping_json"],
		as_dict=True,
	)
	if existing:
		if existing.checksum != checksum:
			raise Conflict(
				"This idempotency key was already used with a different file.",
				code="CONFLICT",
			)
		try:
			existing_mapping = _canonical_mapping(json.loads(existing.mapping_json or "{}"))
		except (ValueError, TypeError):
			existing_mapping = {}
		if existing_mapping != normalized_mapping:
			raise Conflict(
				"This idempotency key was already used with a different column mapping.",
				code="CONFLICT",
			)
		return _status_response(existing.name)

	header, rows = _read_rows(content, extension, limits["max_import_rows"])
	resolved = resolve_import_columns(header, rows[0]["cells"] if rows else {}, normalized_mapping)
	phone_column = resolved["phone"]

	classification = _classify(
		rows,
		normalized_mapping,
		phone_column,
		name_column=resolved["name"],
		source_column=resolved["source"],
	)

	batch = frappe.get_doc(
		{
			"doctype": "CSKH Import Batch",
			"source_file": file_doc.name,
			"checksum": checksum,
			"idempotency_key": key,
			"status": "DRY_RUN",
			"mapping_json": json.dumps(normalized_mapping, ensure_ascii=False),
			"total_rows": classification["total_rows"],
			"valid_rows": classification["valid_rows"],
			"new_rows": classification["new_rows"],
			"duplicate_rows": classification["duplicate_rows"],
			"error_rows": classification["error_rows"],
			"preview_json": json.dumps(
				{
					**classification["preview"],
					"resolved_mapping": resolved,
				},
				ensure_ascii=False,
			),
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


def _name_candidates(header):
	candidates = []
	for column in header or []:
		normalized = (column or "").strip().lower()
		if normalized in (
			"name",
			"full_name",
			"fullname",
			"lead_name",
			"first_name",
			"customer_name",
			"tên",
			"ten",
			"họ tên",
			"ho ten",
			"ho_ten",
			"họ và tên",
			"ho va ten",
			"ho_va_ten",
			"tên khách hàng",
			"ten khach hang",
			"ten_khach_hang",
		):
			candidates.append(column)
	return candidates


def _source_candidates(header):
	candidates = []
	for column in header or []:
		normalized = (column or "").strip().lower()
		if normalized in (
			"source",
			"lead_source",
			"nguồn",
			"nguon",
			"nguồn khách",
			"nguon khach",
			"nguon_khach",
			"nguồn khách hàng",
			"kênh",
			"kenh",
		):
			candidates.append(column)
	return candidates


def _classify(rows, mapping, phone_column, name_column=None, source_column=None):
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

		extra = {}
		if name_column:
			safe_name = sanitize_cell(_cell_value(row["cells"], name_column))[:140].strip()
			if safe_name:
				extra["name"] = safe_name
		if source_column:
			safe_source = sanitize_cell(_cell_value(row["cells"], source_column))[:140].strip()
			if safe_source:
				extra["source"] = safe_source

		if canonical in seen_in_file:
			counts["duplicate_rows"] += 1
			preview_rows.append(
				{
					"row_number": row["row_number"],
					"class": CLASS_DUPLICATE_IN_FILE,
					"canonical": canonical,
					**extra,
				}
			)
			continue
		seen_in_file.add(canonical)

		existing_lead = identity_service.find_by_phone(canonical)
		if not existing_lead:
			existing_lead = identity_service.find_lead_by_phone(canonical)
		if existing_lead:
			counts["duplicate_rows"] += 1
			duplicate_in_crm += 1
			preview_rows.append(
				{
					"row_number": row["row_number"],
					"class": CLASS_DUPLICATE_IN_CRM,
					"canonical": canonical,
					"lead": existing_lead,
					**extra,
				}
			)
			continue

		counts["valid_rows"] += 1
		counts["new_rows"] += 1
		preview_rows.append(
			{
				"row_number": row["row_number"],
				"class": CLASS_VALID,
				"canonical": canonical,
				**extra,
			}
		)

	return {
		"total_rows": counts["total_rows"],
		"valid_rows": counts["valid_rows"],
		"new_rows": counts["new_rows"],
		"duplicate_rows": counts["duplicate_rows"],
		"error_rows": counts["error_rows"],
		# The **full** classification. The API response only shows the first
		# 200 rows (see `preview` below), but the worker needs **every** row:
		# a truncated list would silently import at most 200 leads out of a
		# 10.000-row file.
		"rows": preview_rows,
		# Every row whose phone number could be normalized, whatever its class.
		# The list is stable across retries (a lead that the worker itself
		# created becomes `duplicate_in_crm` on the next attempt), so the
		# reconciliation of a resumed batch still sees all rows.
		"resolvable": [
			{
				"row_number": row["row_number"],
				"canonical": row.get("canonical"),
				"class": row["class"],
			}
			for row in preview_rows
			if row.get("canonical")
		],
		"preview": {
			"rows": preview_rows[:200],
			"truncated": len(preview_rows) > 200,
			"duplicate_in_crm": duplicate_in_crm,
			"conflict_active": conflict_active,
		},
	}


def confirm(batch_name, actor=None):
	"""Confirm a preview, transition cleanly to `QUEUED`, and enqueue the worker (L-18)."""
	actor = actor or frappe.session.user
	if not batch_name:
		raise InvalidRequest("The import batch is required.")

	locked = frappe.db.sql(
		"select name, status from `tabCSKH Import Batch` where name = %s for update",
		(batch_name,),
		as_dict=True,
	)
	if not locked:
		raise InvalidRequest("The import batch does not exist.")

	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	if batch.status in ("QUEUED", "RUNNING", "COMPLETED"):
		return _status_response(batch.name)
	if batch.status == "COMPLETED_WITH_ERRORS":
		# Only re-queue when valid new rows remain uncreated (transient worker
		# error). When every valid row was already committed and only input
		# CSV rows were invalid, double-submit returns idempotently.
		if int(batch.created_rows or 0) >= int(batch.new_rows or 0):
			return _status_response(batch.name)
		batch.status = "QUEUED"
		batch.save(ignore_permissions=True)
	elif batch.status == "DRY_RUN":
		# Walk both valid state-machine steps (`DRY_RUN -> CONFIRMED -> QUEUED`)
		# before enqueueing so `import_worker.run_batch` transitions legally
		# from `QUEUED -> RUNNING` on the very first confirm (L-18).
		batch.status = "CONFIRMED"
		batch.save(ignore_permissions=True)
		batch.status = "QUEUED"
		batch.save(ignore_permissions=True)
	elif batch.status in ("CONFIRMED", "FAILED"):
		batch.status = "QUEUED"
		batch.save(ignore_permissions=True)

	from crm_cskh.jobs import import_worker

	frappe.enqueue(
		import_worker.run_batch,
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
		job_id=f"cskh-import-{batch.name}",
		deduplicate=True,
		batch_name=batch.name,
	)
	batch.reload()
	return _status_response(batch.name)


def _status_response(batch_name):
	if not batch_name or not frappe.db.exists("CSKH Import Batch", batch_name):
		raise InvalidRequest("The import batch does not exist.")
	batch = frappe.get_doc("CSKH Import Batch", batch_name)
	preview_payload = {}
	if batch.preview_json:
		try:
			preview_payload = json.loads(batch.preview_json)
		except (ValueError, TypeError):
			preview_payload = {}
	mapping_payload = {}
	if batch.mapping_json:
		try:
			mapping_payload = json.loads(batch.mapping_json)
		except (ValueError, TypeError):
			mapping_payload = {}
	return {
		"batch": batch.name,
		"status": batch.status,
		"checksum": batch.checksum,
		"idempotency_key": batch.idempotency_key,
		"mapping": mapping_payload,
		"resolved_mapping": preview_payload.get("resolved_mapping") or {},
		"totals": {
			"total_rows": batch.total_rows,
			"valid_rows": batch.valid_rows,
			"new_rows": batch.new_rows,
			"duplicate_rows": batch.duplicate_rows,
			"error_rows": batch.error_rows,
		},
		"created_rows": int(batch.created_rows or 0),
		"reused_rows": int(batch.reused_rows or 0),
		"skipped_rows": int(batch.skipped_rows or 0),
		"progress": int(batch.progress or 0),
		"attempts": int(batch.attempts or 0),
		"preview": preview_payload.get("rows", []),
		"preview_truncated": preview_payload.get("truncated", False),
		"committed_at": batch.committed_at,
		"error_artifact": batch.error_artifact,
		"error": getattr(batch, "error", None),
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
