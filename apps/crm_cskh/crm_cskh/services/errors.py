"""Stable business error codes for the P1 API (P1 10.1).

Every P1 endpoint returns one of these codes so the UI never has to parse
human-readable messages. The codes are part of the frozen API contract handed
over to T1A.
"""

INVALID_PHONE = "INVALID_PHONE"
DUPLICATE = "DUPLICATE"
ALREADY_ASSIGNED = "ALREADY_ASSIGNED"
RULE_CHANGED = "RULE_CHANGED"
CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
CONFLICT = "CONFLICT"
FORBIDDEN = "FORBIDDEN"
PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
RETRYABLE_FAILURE = "RETRYABLE_FAILURE"

UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
TOO_MANY_ROWS = "TOO_MANY_ROWS"
INVALID_REQUEST = "INVALID_REQUEST"
NOT_FOUND = "NOT_FOUND"
PREVIEW_EXPIRED = "PREVIEW_EXPIRED"
NO_ELIGIBLE_LEADS = "NO_ELIGIBLE_LEADS"
CAPACITY_FULL = "CAPACITY_FULL"

ALL_CODES = (
	INVALID_PHONE,
	DUPLICATE,
	ALREADY_ASSIGNED,
	RULE_CHANGED,
	CAPACITY_EXCEEDED,
	CONFLICT,
	FORBIDDEN,
	PARTIAL_SUCCESS,
	RETRYABLE_FAILURE,
	UNSUPPORTED_FORMAT,
	FILE_TOO_LARGE,
	TOO_MANY_ROWS,
	INVALID_REQUEST,
	NOT_FOUND,
	PREVIEW_EXPIRED,
	NO_ELIGIBLE_LEADS,
	CAPACITY_FULL,
)


class P1Error(Exception):
	"""Base error carrying a stable machine-readable code.

	Frappe turns any raised exception into a JSON error response; the code is
	attached so the client can branch on it instead of on the message text.
	"""

	code = INVALID_REQUEST
	http_status = 400

	def __init__(self, message=None, code=None, http_status=None, details=None):
		self.code = code or self.code
		if http_status is not None:
			self.http_status = http_status
		self.details = details or {}
		super().__init__(message or self.code)

	def as_dict(self):
		return {"code": self.code, "message": str(self), "details": self.details}


class InvalidRequest(P1Error):
	code = INVALID_REQUEST
	http_status = 400


class Forbidden(P1Error):
	code = FORBIDDEN
	http_status = 403


class NotFound(P1Error):
	code = NOT_FOUND
	http_status = 404


class Conflict(P1Error):
	code = CONFLICT
	http_status = 409


class RuleChanged(P1Error):
	code = RULE_CHANGED
	http_status = 409


class PreviewExpired(P1Error):
	code = PREVIEW_EXPIRED
	http_status = 409


class CapacityExceeded(P1Error):
	code = CAPACITY_EXCEEDED
	http_status = 409


class InvalidPhoneError(P1Error):
	code = INVALID_PHONE
	http_status = 400


class UnsupportedFormat(P1Error):
	code = UNSUPPORTED_FORMAT
	http_status = 400


class FileTooLarge(P1Error):
	code = FILE_TOO_LARGE
	http_status = 413


class TooManyRows(P1Error):
	code = TOO_MANY_ROWS
	http_status = 413
