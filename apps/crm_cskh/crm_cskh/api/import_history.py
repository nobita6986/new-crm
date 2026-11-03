"""P1 API: the import history and error file download of the CRM Admin (P1 §9.2).

The import page of P1 §9.2 must show the **history of the batches** with the
totals of every batch, the **error file** of the failed batches and a way to
**retry** a failed job.

The read side of the pipeline is frozen by T1B: `create_preview`, `confirm`
and `get_status` are the contract of `docs/architecture/api-contract-p1.md`.
This module adds only the **read-only list** and the **authorised error-file
download** the page needs; it never changes a counter, a status or a DocType
permission.

**The scope is unchanged.** Only the `CSKH CRM Admin` business role (or a
system manager) may read an import batch or download its error artifact:
the DocType permission of `CSKH Import Batch` already grants `read` to
`System Manager` and `CSKH CRM Admin` only, and the row-level rule
(`crm_cskh.p1_permissions.import_batch_query_conditions`) returns `1=0` for
every other role. Both endpoints repeat that check explicitly, so a team
leader, a staff member, a BOD viewer or a guest gets `FORBIDDEN` (HTTP 403).
"""

from urllib.parse import quote

import frappe
from frappe.utils import now_datetime

from crm_cskh.services.errors import Forbidden, InvalidRequest, NotFound

ROLE_ADMIN = "CSKH CRM Admin"
DOWNLOAD_METHOD = "crm_cskh.api.import_history.download_error_file"

# The columns the page shows. `error_artifact` is the **name** of the private
# `File`; the authorised download URL is resolved below.
BATCH_FIELDS = (
	"name",
	"status",
	"total_rows",
	"valid_rows",
	"new_rows",
	"duplicate_rows",
	"error_rows",
	"created_rows",
	"reused_rows",
	"skipped_rows",
	"error_artifact",
	"creation",
	"committed_at",
)


def _batch_fields():
	fields = list(BATCH_FIELDS)
	if frappe.db.has_column("CSKH Import Batch", "error"):
		fields.append("error")
	return fields


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_admin(user=None):
	"""Only the CRM Admin business role may read an import batch or error file."""
	user = user or frappe.session.user
	if _is_system(user):
		return
	if ROLE_ADMIN in frappe.get_roles(user):
		return
	raise Forbidden("Only the CRM Admin may read an import batch.")


def _artifact_payload(batch_name, artifact):
	"""Return the metadata and authorised download URL of the batch error file.

	The error artifact is a **private** Frappe `File` attached to
	`CSKH Import Batch`. Because the P0 `File` guard
	(`crm_cskh.permissions.linked_lead_has_permission`) restricts direct
	`/private/files/...` access to files attached to `CRM Lead`, downloading
	through this dedicated Admin-only endpoint keeps the P0 `File` guard
	strictly intact while letting `CSKH CRM Admin` (which intentionally does
	not hold `System Manager`) download the sanitised error CSV.
	"""
	if not artifact:
		return None
	row = frappe.db.get_value(
		"File",
		artifact,
		["name", "file_name", "file_url", "is_private"],
		as_dict=True,
	)
	if not row:
		return None
	return {
		"name": row.name,
		"file_name": row.file_name,
		"file_url": row.file_url,
		"download_url": f"/api/method/{DOWNLOAD_METHOD}?batch={quote(str(batch_name), safe='')}",
		"is_private": int(row.is_private or 0),
	}


@frappe.whitelist(methods=["GET"])
def download_error_file(batch=None):
	"""Stream the private error CSV of an import batch to an authorised Admin."""
	actor = frappe.session.user
	_require_admin(actor)

	if not batch or not isinstance(batch, str):
		raise InvalidRequest("The import batch is required.")

	batch_row = frappe.db.get_value(
		"CSKH Import Batch",
		batch.strip(),
		["name", "error_artifact"],
		as_dict=True,
	)
	if not batch_row or not batch_row.error_artifact:
		raise NotFound("The error file does not exist for this import batch.")

	file_meta = frappe.db.get_value(
		"File",
		batch_row.error_artifact,
		["name", "file_name", "is_private", "attached_to_doctype", "attached_to_name"],
		as_dict=True,
	)
	if (
		not file_meta
		or not int(file_meta.is_private or 0)
		or file_meta.attached_to_doctype != "CSKH Import Batch"
		or file_meta.attached_to_name != batch_row.name
	):
		raise NotFound("The error file does not exist for this import batch.")

	file_doc = frappe.get_doc("File", file_meta.name)
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")

	frappe.local.response.filename = file_meta.file_name or f"import-errors-{batch_row.name}.csv"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"


def _serialize_batch(row):
	return {
		"batch": row.name,
		"status": row.status,
		"totals": {
			"total_rows": row.total_rows,
			"valid_rows": row.valid_rows,
			"new_rows": row.new_rows,
			"duplicate_rows": row.duplicate_rows,
			"error_rows": row.error_rows,
			"created_rows": row.created_rows,
			"reused_rows": row.reused_rows,
			"skipped_rows": row.skipped_rows,
		},
		"error": row.get("error"),
		"error_artifact": _artifact_payload(row.name, row.error_artifact),
		"created_at": row.creation,
		"committed_at": row.committed_at,
	}


@frappe.whitelist(methods=["GET"])
def batch_detail(batch=None):
	"""Return the full reconciled counters and artifact link of one batch for Step 3."""
	actor = frappe.session.user
	_require_admin(actor)

	if not batch or not isinstance(batch, str):
		raise InvalidRequest("The import batch is required.")

	row = frappe.db.get_value(
		"CSKH Import Batch",
		batch.strip(),
		_batch_fields(),
		as_dict=True,
	)
	if not row:
		raise NotFound("The import batch does not exist.")
	return _serialize_batch(row)


@frappe.whitelist(methods=["GET"])
def history(page=1, page_size=20, status=None):
	"""The import batches of the CRM Admin, newest first.

	Every counter comes from the **batch row**: the page never sums the
	events, so a retried batch is not counted twice (P1 §6.4).
	"""
	actor = frappe.session.user
	_require_admin(actor)

	limit = max(1, min(int(page_size or 20), 100))
	offset = max(0, (int(page or 1) - 1) * limit)
	if status:
		if not isinstance(status, str):
			raise InvalidRequest("The batch status filter must be a string.")

	rows = frappe.get_list(
		"CSKH Import Batch",
		filters={"status": status} if status else None,
		fields=_batch_fields(),
		order_by="creation desc",
		limit_start=offset,
		limit_page_length=limit,
	)
	total = frappe.db.count("CSKH Import Batch", {"status": status} if status else None)

	items = [_serialize_batch(row) for row in rows]

	return {
		"items": items,
		"total": total,
		"page": int(page or 1),
		"page_size": limit,
		"as_of": now_datetime(),
	}
