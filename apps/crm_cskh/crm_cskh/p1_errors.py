"""Transport adapter for the stable P1 error codes (P1 §10.1).

The frozen API contract of P1 promises stable machine-readable codes
(`INVALID_PHONE`, `DUPLICATE`, `RULE_CHANGED`, `CONFLICT`, `FORBIDDEN`,
`PARTIAL_SUCCESS`, …). The service layer raises them through
`crm_cskh.services.errors.P1Error`; this module makes sure the code reaches
the browser instead of being flattened into a generic HTTP 500.

Why an `after_request` hook and not a custom exception class:

* Frappe builds the JSON error body itself (`frappe.utils.response.report_error`)
  and puts the **Python class name** of the exception into `exc_type`.
* Rewriting the exception hierarchy of the backend contract would change the
  contract of the service layer, which T1B owns. The transport mapping belongs
  to the UI delivery package and therefore lives here, in the app that owns the
  UI.

Two problems had to be solved, and both are transport-level (the service layer
of T1B stays untouched):

1. **The stable code never reached the browser.** The hook was written but not
   registered in `hooks.after_request`, so every P1 error was a bare HTTP 500
   with a Python class name. The hook is now registered.

2. **Every P1 error was an HTTP 500.** Frappe derives the status of a raised
   exception from the `http_status_code` attribute of the exception class. The
   P1 exceptions carry `http_status` instead (that is the name the frozen
   contract of the service layer uses), so Frappe fell back to 500 and the
   browser could not tell a conflict (409) from a permission denial (403).

   The mapping is done here, in the transport layer: the `after_request` hook
   receives the already-built response and rewrites the **status code** of the
   response, so the stable code and the HTTP status always agree.

3. **The specific code of an instance was flattened into the code of its
   class.** The service layer raises the same exception class with different
   codes (`Conflict` with `CONFLICT`, `NO_ELIGIBLE_LEADS` or `CAPACITY_FULL`;
   `Conflict` with `PREVIEW_EXPIRED`). Frappe only puts the **class name** into
   the body, so the transport could only recover the class default and the
   browser received a generic `CONFLICT` for every conflict. The operator then
   saw "the operation conflicts" instead of "no customer is eligible", and the
   page could not branch on the situation.

   The instance code is captured **at raise time**, before Frappe builds the
   body: the exception records its own code and its own HTTP status in the
   request context, so the transport reads the exact code of the situation
   instead of guessing it from the class.

The hook is idempotent and never touches a response that does not carry a P1
error, so the P0 error-normalisation behaviour is unchanged.
"""

import json

import frappe

from crm_cskh.services import errors as p1_errors

# Python class name of the P1 exceptions -> stable contract code.
# The mapping is derived from the exception classes themselves, so a new
# exception type cannot silently lose its code.
CODE_BY_EXCEPTION = {
	exception_class.__name__: exception_class.code
	for exception_class in (
		p1_errors.P1Error,
		p1_errors.InvalidRequest,
		p1_errors.Forbidden,
		p1_errors.NotFound,
		p1_errors.Conflict,
		p1_errors.RuleChanged,
		p1_errors.PreviewExpired,
		p1_errors.CapacityExceeded,
		p1_errors.InvalidPhoneError,
		p1_errors.UnsupportedFormat,
		p1_errors.FileTooLarge,
		p1_errors.TooManyRows,
	)
}

# The HTTP status of every stable code of the frozen contract (P1 §10.1).
#
# The status is derived from the **code**, not from the Python class: the
# service layer may raise the same class with a different code (a `Conflict`
# with `NO_ELIGIBLE_LEADS` is still a 409, but `FORBIDDEN` is a 403), and the
# browser branches on the code.
STATUS_BY_CODE = {
	p1_errors.INVALID_REQUEST: 400,
	p1_errors.INVALID_PHONE: 400,
	p1_errors.UNSUPPORTED_FORMAT: 400,
	p1_errors.FORBIDDEN: 403,
	p1_errors.NOT_FOUND: 404,
	p1_errors.CONFLICT: 409,
	p1_errors.RULE_CHANGED: 409,
	p1_errors.PREVIEW_EXPIRED: 409,
	p1_errors.CAPACITY_EXCEEDED: 409,
	p1_errors.CAPACITY_FULL: 409,
	p1_errors.ALREADY_ASSIGNED: 409,
	p1_errors.DUPLICATE: 409,
	p1_errors.NO_ELIGIBLE_LEADS: 409,
	p1_errors.FILE_TOO_LARGE: 413,
	p1_errors.TOO_MANY_ROWS: 413,
	# `PARTIAL_SUCCESS` and `RETRYABLE_FAILURE` are *outcomes*, not refusals:
	# the request itself was accepted, so the HTTP status stays 200.
	p1_errors.PARTIAL_SUCCESS: 200,
	p1_errors.RETRYABLE_FAILURE: 200,
}


def _payload(response):
	raw = getattr(response, "data", None)
	if isinstance(raw, (bytes, bytearray)):
		raw = bytes(raw).decode("utf-8", errors="replace")
	if not isinstance(raw, str) or not raw:
		return None
	mimetype = getattr(response, "mimetype", "") or ""
	if "json" not in mimetype:
		return None
	try:
		payload = json.loads(raw)
	except ValueError:
		return None
	return payload if isinstance(payload, dict) else None


def _recorded_code():
	"""The exact code of the P1 error raised in this request, if any.

	The record is written by `crm_cskh.services.errors.P1Error` when the error
	is raised, so it holds the code of the **situation** and not only the code
	of the exception class.
	"""
	code = getattr(frappe.local, "p1_error_code", None)
	if not isinstance(code, str) or not code:
		return None
	return code if code in p1_errors.ALL_CODES else None


def normalize_p1_error_response(response=None, request=None):
	"""Attach the stable P1 error code to the JSON error body.

	The HTTP status is rewritten to the status of the stable code, so the
	status and the code always agree: the client distinguishes the situation by
	the code and never has to guess from the status (P1 §10.1).
	"""
	if response is None:
		return response
	payload = _payload(response)
	if not payload:
		return response

	exc_type = payload.get("exc_type")
	if not isinstance(exc_type, str):
		return response

	class_code = CODE_BY_EXCEPTION.get(exc_type)
	if not class_code:
		return response

	# The **instance** code wins over the class default: the service layer
	# raises the same class with different codes (`Conflict` with `CONFLICT`,
	# `NO_ELIGIBLE_LEADS`, `CAPACITY_FULL` or `PREVIEW_EXPIRED`), and Frappe
	# only puts the class name into the body. The instance code was recorded
	# in `frappe.local` when the error was raised (see `errors.P1Error`).
	code = _recorded_code() or class_code

	payload["p1_code"] = code
	# The stable code replaces the Python class name so the browser never has
	# to know the backend exception hierarchy.
	payload["exc_type"] = code
	if isinstance(payload.get("exception"), str):
		payload["exception"] = f"crm_cskh.services.errors.{code}"
	if isinstance(payload.get("exc"), str):
		payload["exc"] = "[]"

	status = STATUS_BY_CODE.get(code)
	if status is None:
		status = STATUS_BY_CODE.get(class_code)
	if status is not None and getattr(response, "status_code", None) != status:
		response.status_code = status

	response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
	return response
