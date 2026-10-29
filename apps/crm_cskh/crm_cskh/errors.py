# Copyright (c) 2026, CRM-CSKH Project
# License: agpl-3.0
#
# Normalize identifier echo in raw HTTP 403/404 JSON bodies (fail-closed).
# Root cause: frappe.model.document raise_no_permission_to sets
# frappe.flags.error_message with doctype+name; make_logs copies it to
# _error_message and message_log to _server_messages. Status codes and
# exc_type are NEVER changed; only human-readable message strings.

from __future__ import annotations

import json
import re

_GENERIC_DENIAL = "Không đủ quyền truy cập tài liệu này."

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
	"""after_request hook: strip identifier echo from JSON 403/404 bodies."""
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
