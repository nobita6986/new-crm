"""P1 API: the private file upload of the import page.

Why a dedicated endpoint instead of the standard `upload_file`:

* The P0 guard (`crm_cskh.permissions.linked_lead_has_permission`) makes a
  `File` readable only through the `CRM Lead` it is attached to. An **import
  source file is not attached to a lead** — it is the raw customer list, and it
  must stay private (P1 §6.1, ADR-P1-03).
* The import page is the only place of the product that needs to put a private
  file into the site without a lead owner. The endpoint therefore accepts the
  file **only** from the `CSKH CRM Admin` business role (P1 §8.1) and always
  creates the file with `is_private = 1` and **no** attachment.

The file is never exposed: the caller receives only the `file_name`, and the
import API refuses a file that is not private or that the actor may not read.
"""

import frappe

from crm_cskh.services.errors import Forbidden, InvalidRequest, P1Error

# The import limits (P1 §6.1, ADR-P1-08). The same limits are enforced by the
# import service itself; they are repeated here so an oversized file is
# rejected **before** it is written to disk.
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = ("csv", "xlsx")


def _require_import_permission(user=None):
	"""Only the CRM Admin business role may upload an import file (P1 §8.1)."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return
	if "CSKH CRM Admin" in frappe.get_roles(user):
		return
	raise Forbidden("Importing the customer pool requires the CRM Admin business role.")


@frappe.whitelist(methods=["POST"])
def upload_import_file(file_name=None, content=None):
	"""Store the uploaded import file as a **private** Frappe file.

	The file is read from the request body as base64 (`content`) or from the
	multipart upload (`frappe.request.files`). It is stored without an
	attachment, so it is only reachable through the import API.
	"""
	actor = frappe.session.user
	_require_import_permission(actor)

	raw = None
	uploads = getattr(frappe.request, "files", None) or {}
	upload = uploads.get("file") if hasattr(uploads, "get") else None
	if upload is not None:
		raw = upload.stream.read()
		file_name = file_name or getattr(upload, "filename", None)
	elif content:
		import base64

		try:
			raw = base64.b64decode(content, validate=False)
		except Exception as exc:
			raise InvalidRequest("The uploaded file could not be decoded.") from exc

	if not raw:
		raise InvalidRequest("The uploaded file is required.")
	if len(raw) > MAX_FILE_BYTES:
		raise P1Error(
			"The file is larger than the configured limit.",
			code="FILE_TOO_LARGE",
			http_status=413,
		)

	name = (file_name or "import.csv").lower()
	if not name.endswith(ALLOWED_EXTENSIONS):
		raise P1Error(
			"Only CSV and XLSX files are supported.",
			code="UNSUPPORTED_FORMAT",
			http_status=400,
		)

	try:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name or "import.csv",
				"is_private": 1,
				"folder": "Home/Attachments",
				"content": raw,
			}
		)
		file_doc.save(ignore_permissions=True)
	except Exception as exc:
		raise InvalidRequest(str(exc)[:200]) from exc

	return {
		"name": file_doc.name,
		"file_name": file_doc.file_name,
		"is_private": int(file_doc.is_private or 0),
		"size": file_doc.file_size,
	}
