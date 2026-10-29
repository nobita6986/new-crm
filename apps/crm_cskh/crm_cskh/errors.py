# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0
#
# Close the File existence oracle in raw HTTP JSON bodies (fail-closed).
#
# Root cause: frappe.model.document raise_no_permission_to sets
# frappe.flags.error_message with doctype+name; make_logs copies it to
# _error_message and message_log to _server_messages. An out-of-scope caller
# probing a real File id therefore got 403 echoing the id, while a random id
# got 404 DoesNotExistError — a status/body oracle for File existence.
#
# Fix (app layer only, no upstream change):
#  1. Echo scrub (all routes, status preserved): human-readable message
#     strings that echo File/Lead identifiers are replaced with a generic
#     denial. Status codes and exc_type are NEVER changed on this path.
#  2. Existence-oracle closure (File direct routes ONLY): responses to
#     `/api/resource/File/<name>` and `frappe.client.get` with
#     `doctype=File` are canonicalized — 403 PermissionError is mapped to
#     404 with a fixed generic body, and 404 DoesNotExistError bodies are
#     rewritten to the SAME fixed generic body (caller-supplied names are
#     never reflected). Both cases become byte-identical: same status, same
#     keys, same length, same sha. Access stays denied; in-scope 200s are
#     untouched.
#
# Residual limitation: response timing is not normalized at app layer.

from __future__ import annotations

import json
import re

_GENERIC_DENIAL = "Không đủ quyền truy cập tài liệu này."
_GENERIC_NOT_FOUND = "Không tìm thấy tài liệu."

_DENIAL_MARKERS = (
	"Tập tin -",
	"Tệp tin -",
	"File -",
	"CRM Lead -",
	"CRM-LEAD-",
)

_ID_PATTERNS = (
	re.compile(r"CRM-LEAD-\d+"),
	re.compile(r"[0-9a-f]{8,}"),
)

# Direct File routes covered by the status-level oracle closure.
_FILE_REST_PREFIX = "/api/resource/File/"
_FILE_RPC_PATH = "/api/method/frappe.client.get"


def _looks_like_permission_echo(payload: dict) -> bool:
	if not isinstance(payload, dict):
		return False
	if payload.get("exc_type") not in ("PermissionError", "DoesNotExistError"):
		return False
	haystack = ""
	for key in ("_error_message", "_server_messages", "exception", "message"):
		value = payload.get(key)
		if isinstance(value, str) and value:
			haystack += value + "\n"
	return any(marker in haystack for marker in _DENIAL_MARKERS)


def _request_targets_file(request) -> bool:
	"""True when the request addresses a single File document directly."""
	try:
		if request is None:
			return False
		path = getattr(request, "path", "") or ""
		if path.startswith(_FILE_REST_PREFIX):
			return True
		if path == _FILE_RPC_PATH:
			doctype = None
			args = getattr(request, "args", None)
			if args is not None:
				try:
					doctype = args.get("doctype")
				except Exception:
					doctype = None
			if doctype is None:
				try:
					import frappe

					form = getattr(frappe.local, "form_dict", None)
					if form:
						doctype = form.get("doctype")
				except Exception:
					doctype = None
			return doctype == "File"
	except Exception:
		return False
	return False


def _canonical_not_found_body() -> str:
	"""Fixed generic 404 body shared by both oracle cases (no identifiers)."""
	payload = {
		"exc_type": "DoesNotExistError",
		"_server_messages": json.dumps(
			[json.dumps({"message": _GENERIC_NOT_FOUND}, ensure_ascii=False)],
			ensure_ascii=False,
		),
	}
	return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_server_messages(raw: str) -> str:
	try:
		items = json.loads(raw)
	except (TypeError, ValueError):
		return raw
	if not isinstance(items, list):
		return raw
	normalized: list[str] = []
	for item in items:
		try:
			message = json.loads(item) if isinstance(item, str) else item
		except (TypeError, ValueError):
			normalized.append(item if isinstance(item, str) else json.dumps(item))
			continue
		if isinstance(message, dict):
			message = dict(message)
			if isinstance(message.get("message"), str):
				message["message"] = _GENERIC_DENIAL
			normalized.append(json.dumps(message, ensure_ascii=False))
		else:
			normalized.append(item if isinstance(item, str) else json.dumps(item))
	return json.dumps(normalized, ensure_ascii=False)


def normalize_error_response(response=None, request=None):
	"""after_request hook: close the File existence oracle, fail-closed."""
	try:
		status = getattr(response, "status_code", None)
		if status not in (403, 404):
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
		try:
			payload = json.loads(text) if text else None
		except ValueError:
			return response
		if not isinstance(payload, dict):
			return response
		# Status-level closure for direct File routes: both the real-id
		# denial (403) and the random-id miss (404) leave as the same
		# fixed generic 404. Nothing is reflected back.
		if _request_targets_file(request):
			if status == 403 and payload.get("exc_type") == "PermissionError":
				response.status_code = 404
				response.data = _canonical_not_found_body()
				return response
			if status == 404 and payload.get("exc_type") == "DoesNotExistError":
				response.data = _canonical_not_found_body()
				return response
			return response
		# Generic echo scrub for every other route (status preserved).
		if not _looks_like_permission_echo(payload):
			return response
		if isinstance(payload.get("_error_message"), str):
			payload["_error_message"] = _GENERIC_DENIAL
		if isinstance(payload.get("_server_messages"), str):
			payload["_server_messages"] = _normalize_server_messages(payload["_server_messages"])
		if isinstance(payload.get("message"), str):
			cleaned = payload["message"]
			for pattern in _ID_PATTERNS:
				cleaned = pattern.sub("[REDACTED]", cleaned)
			payload["message"] = cleaned
		response.data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
		return response
	except Exception:
		return response
