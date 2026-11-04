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

# --- P2 workflow & policy error codes (P2 §10.2) -----------------------------
OWNERSHIP_CHANGED = "OWNERSHIP_CHANGED"
WORK_ITEM_NOT_ACTIVE = "WORK_ITEM_NOT_ACTIVE"
STALE_WORK_ITEM = "STALE_WORK_ITEM"
POLICY_CHANGED = "POLICY_CHANGED"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
ALREADY_LINKED = "ALREADY_LINKED"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
INVALID_CHANNEL = "INVALID_CHANNEL"
INVALID_OUTCOME = "INVALID_OUTCOME"
NOTE_REQUIRED = "NOTE_REQUIRED"
FOLLOWUP_REQUIRED = "FOLLOWUP_REQUIRED"
INVALID_FOLLOWUP_TIME = "INVALID_FOLLOWUP_TIME"
OUTCOME_NOT_COMPATIBLE = "OUTCOME_NOT_COMPATIBLE"

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
	OWNERSHIP_CHANGED,
	WORK_ITEM_NOT_ACTIVE,
	STALE_WORK_ITEM,
	POLICY_CHANGED,
	IDEMPOTENCY_CONFLICT,
	ALREADY_LINKED,
	DO_NOT_CONTACT,
	INVALID_CHANNEL,
	INVALID_OUTCOME,
	NOTE_REQUIRED,
	FOLLOWUP_REQUIRED,
	INVALID_FOLLOWUP_TIME,
	OUTCOME_NOT_COMPATIBLE,
)


def _record_p1_error(code, details=None):
	try:
		import frappe

		local = getattr(frappe, "local", None)
		if local is not None:
			local.p1_error_code = code
			local.p1_error_details = dict(details or {})
	except Exception:
		pass


def is_transient_tx_abort(exc: BaseException) -> bool:
	"""Return True for MariaDB InnoDB deadlock (`1213`) or snapshot-isolation conflict (`1020`)."""
	text = str(exc)
	return (
		"Deadlock" in type(exc).__name__
		or "1213" in text
		or "1020" in text
		or "Record has changed since last read" in text
	)


def rollback_savepoint_safely(savepoint: str) -> None:
	"""Roll back `savepoint` without clobbering `1213`/`1020` with `1305 SAVEPOINT does not exist`."""
	import frappe

	try:
		frappe.db.rollback(save_point=savepoint)
	except Exception:
		pass


class P1Error(Exception):
	"""Base error carrying a stable machine-readable code.

	Frappe turns any raised exception into a JSON error response; the code is
	attached so the client can branch on it instead of on the message text.

	> **HTTP status (contract §2):** Frappe reads `http_status_code` off the
	> exception (`frappe.app.handle_exception`), never `http_status`. Without
	> the alias below every P1 error would leave as HTTP 500 instead of the
	> contracted 403 / 404 / 409 / 413, and the UI could not branch on the
	> status. `http_status` is kept as the readable name of the same value.
	"""

	code = INVALID_REQUEST
	http_status = 400
	http_status_code = 400

	def __init__(self, message=None, code=None, http_status=None, details=None):
		self.code = code or self.code
		if http_status is not None:
			self.http_status = http_status
			self.http_status_code = http_status
		self.details = details or {}
		_record_p1_error(self.code, self.details)
		super().__init__(message or self.code)

	def as_dict(self):
		return {"code": self.code, "message": str(self), "details": self.details}


class InvalidRequest(P1Error):
	code = INVALID_REQUEST
	http_status = 400
	http_status_code = 400


class Forbidden(P1Error):
	code = FORBIDDEN
	http_status = 403
	http_status_code = 403


class NotFound(P1Error):
	code = NOT_FOUND
	http_status = 404
	http_status_code = 404


class Conflict(P1Error):
	code = CONFLICT
	http_status = 409
	http_status_code = 409


class RuleChanged(P1Error):
	code = RULE_CHANGED
	http_status = 409
	http_status_code = 409


class PreviewExpired(P1Error):
	code = PREVIEW_EXPIRED
	http_status = 409
	http_status_code = 409


class CapacityExceeded(P1Error):
	code = CAPACITY_EXCEEDED
	http_status = 409
	http_status_code = 409


class InvalidPhoneError(P1Error):
	code = INVALID_PHONE
	http_status = 400
	http_status_code = 400


class UnsupportedFormat(P1Error):
	code = UNSUPPORTED_FORMAT
	http_status = 400
	http_status_code = 400


class FileTooLarge(P1Error):
	code = FILE_TOO_LARGE
	http_status = 413
	http_status_code = 413


class TooManyRows(P1Error):
	code = TOO_MANY_ROWS
	http_status = 413
	http_status_code = 413


class OwnershipChanged(P1Error):
	code = OWNERSHIP_CHANGED
	http_status = 409
	http_status_code = 409


class WorkItemNotActive(P1Error):
	code = WORK_ITEM_NOT_ACTIVE
	http_status = 409
	http_status_code = 409


class StaleWorkItem(P1Error):
	code = STALE_WORK_ITEM
	http_status = 409
	http_status_code = 409


class PolicyChanged(P1Error):
	code = POLICY_CHANGED
	http_status = 409
	http_status_code = 409


class IdempotencyConflict(P1Error):
	code = IDEMPOTENCY_CONFLICT
	http_status = 409
	http_status_code = 409


class AlreadyLinked(P1Error):
	code = ALREADY_LINKED
	http_status = 409
	http_status_code = 409


class DoNotContactError(P1Error):
	code = DO_NOT_CONTACT
	http_status = 409
	http_status_code = 409


DoNotContact = DoNotContactError


class InvalidChannel(P1Error):
	code = INVALID_CHANNEL
	http_status = 400
	http_status_code = 400


class InvalidOutcome(P1Error):
	code = INVALID_OUTCOME
	http_status = 422
	http_status_code = 422


class NoteRequired(P1Error):
	code = NOTE_REQUIRED
	http_status = 422
	http_status_code = 422


class FollowupRequired(P1Error):
	code = FOLLOWUP_REQUIRED
	http_status = 422
	http_status_code = 422


class InvalidFollowupTime(P1Error):
	code = INVALID_FOLLOWUP_TIME
	http_status = 422
	http_status_code = 422


class OutcomeNotCompatible(P1Error):
	code = OUTCOME_NOT_COMPATIBLE
	http_status = 422
	http_status_code = 422
