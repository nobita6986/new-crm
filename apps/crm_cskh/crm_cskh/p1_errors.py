"""HTTP error envelope normalizer for P1 business exceptions (L-16, contract §2).

Attaches the stable machine-readable P1 error code (`code`, `p1_code`, and
`exc_type`) onto HTTP JSON responses produced when a `P1Error` subclass is
raised, while leaving P0 File-oracle (`crm_cskh.errors`) and upstream Frappe
exceptions (`PermissionError`, `DoesNotExistError`, etc.) completely untouched.
"""

from __future__ import annotations

import json

from crm_cskh.errors import _request_targets_file
from crm_cskh.services import errors as p1_error_codes
from crm_cskh.services.sanitizer import redact_pii_text

CODE_BY_EXCEPTION = {
	"P1Error": p1_error_codes.INVALID_REQUEST,
	"InvalidRequest": p1_error_codes.INVALID_REQUEST,
	"Forbidden": p1_error_codes.FORBIDDEN,
	"NotFound": p1_error_codes.NOT_FOUND,
	"Conflict": p1_error_codes.CONFLICT,
	"RuleChanged": p1_error_codes.RULE_CHANGED,
	"PreviewExpired": p1_error_codes.PREVIEW_EXPIRED,
	"CapacityExceeded": p1_error_codes.CAPACITY_EXCEEDED,
	"InvalidPhoneError": p1_error_codes.INVALID_PHONE,
	"UnsupportedFormat": p1_error_codes.UNSUPPORTED_FORMAT,
	"FileTooLarge": p1_error_codes.FILE_TOO_LARGE,
	"TooManyRows": p1_error_codes.TOO_MANY_ROWS,
	"OwnershipChanged": p1_error_codes.OWNERSHIP_CHANGED,
	"WorkItemNotActive": p1_error_codes.WORK_ITEM_NOT_ACTIVE,
	"StaleWorkItem": p1_error_codes.STALE_WORK_ITEM,
	"PolicyChanged": p1_error_codes.POLICY_CHANGED,
	"IdempotencyConflict": p1_error_codes.IDEMPOTENCY_CONFLICT,
	"AlreadyLinked": p1_error_codes.ALREADY_LINKED,
	"DoNotContactError": p1_error_codes.DO_NOT_CONTACT,
	"DoNotContact": p1_error_codes.DO_NOT_CONTACT,
	"InvalidChannel": p1_error_codes.INVALID_CHANNEL,
	"InvalidOutcome": p1_error_codes.INVALID_OUTCOME,
	"NoteRequired": p1_error_codes.NOTE_REQUIRED,
	"FollowupRequired": p1_error_codes.FOLLOWUP_REQUIRED,
	"InvalidFollowupTime": p1_error_codes.INVALID_FOLLOWUP_TIME,
	"OutcomeNotCompatible": p1_error_codes.OUTCOME_NOT_COMPATIBLE,
	"ForbiddenScope": p1_error_codes.FORBIDDEN_SCOPE,
	"InvalidPeriod": p1_error_codes.INVALID_PERIOD,
	"InvalidMetric": p1_error_codes.INVALID_METRIC,
	"SnapshotStale": p1_error_codes.SNAPSHOT_STALE,
	"PolicyConflict": p1_error_codes.POLICY_CONFLICT,
	"DataReconciliationFailed": p1_error_codes.DATA_RECONCILIATION_FAILED,
	"InsufficientData": p1_error_codes.INSUFFICIENT_DATA,
	"QaConflict": p1_error_codes.QA_CONFLICT,
	"QaDisabled": p1_error_codes.QA_DISABLED,
	"RecomputeInProgress": p1_error_codes.RECOMPUTE_IN_PROGRESS,
	"ExportExpired": p1_error_codes.EXPORT_EXPIRED,
}


def clear_p1_error_context(*_args, **_kwargs) -> None:
	"""Clear request-local P1 error metadata before/after each HTTP request."""
	try:
		import frappe

		local = getattr(frappe, "local", None)
		if local is not None:
			local.p1_error_code = None
			local.p1_error_details = None
	except Exception:
		pass


def _read_recorded_p1_error() -> tuple[str | None, dict | None]:
	try:
		import frappe

		local = getattr(frappe, "local", None)
		if local is None:
			return None, None
		code = getattr(local, "p1_error_code", None)
		details = getattr(local, "p1_error_details", None)
		return (
			code if isinstance(code, str) and code else None,
			details if isinstance(details, dict) and details else None,
		)
	except Exception:
		return None, None


def normalize_p1_error_response(response=None, request=None):
	"""after_request hook: expose stable `code` / `p1_code` on P1Error responses."""
	try:
		if response is None:
			return response
		# Never touch direct File routes protected by P0 File existence-oracle closure.
		if _request_targets_file(request):
			return response
		status = getattr(response, "status_code", None)
		if not isinstance(status, int) or status < 400:
			return response
		mimetype = getattr(response, "mimetype", "") or ""
		if "json" not in mimetype:
			return response
		raw = getattr(response, "data", None)
		if isinstance(raw, (bytes, bytearray)):
			text = bytes(raw).decode("utf-8", errors="replace")
		elif isinstance(raw, str):
			text = raw
		else:
			return response
		if not text:
			return response
		try:
			payload = json.loads(text)
		except ValueError:
			return response
		if not isinstance(payload, dict):
			return response

		exc_type = payload.get("exc_type")
		if not isinstance(exc_type, str) or exc_type not in CODE_BY_EXCEPTION:
			return response

		recorded_code, recorded_details = _read_recorded_p1_error()
		code = recorded_code if recorded_code in p1_error_codes.ALL_CODES else CODE_BY_EXCEPTION[exc_type]
		payload["code"] = code
		payload["p1_code"] = code
		payload["exc_type"] = code
		if isinstance(payload.get("exc"), str):
			payload["exc"] = "[]"
		for str_key in ("message", "exception", "_server_messages"):
			if isinstance(payload.get(str_key), str):
				payload[str_key] = redact_pii_text(payload[str_key])
		if recorded_details and "details" not in payload:
			payload["details"] = recorded_details
		response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
		return response
	except Exception:
		return response
	finally:
		clear_p1_error_context()
