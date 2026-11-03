"""Strict request parameter parsers for P1 HTTP endpoints (JSON & form-urlencoded)."""

from __future__ import annotations

from crm_cskh.services.errors import InvalidRequest


def parse_bool(value, *, default: bool = True, field: str = "value") -> bool:
	"""Parse a boolean from JSON (`bool`/`int`) or form-urlencoded (`str`).

	Accepted truthy values: ``True``, ``1``, ``"true"``, ``"1"`` (case-insensitive).
	Accepted falsy values: ``False``, ``0``, ``"false"``, ``"0"`` (case-insensitive).
	``None`` or empty string returns ``default``. Any other input fails closed
	with ``InvalidRequest`` (HTTP 400 ``INVALID_REQUEST``).
	"""
	if value is None:
		return bool(default)
	if isinstance(value, bool):
		return value
	if isinstance(value, int):
		if value in (0, 1):
			return bool(value)
		raise InvalidRequest(f"Invalid boolean value for {field}.")
	if isinstance(value, str):
		normalized = value.strip().lower()
		if normalized == "":
			return bool(default)
		if normalized in ("true", "1"):
			return True
		if normalized in ("false", "0"):
			return False
		raise InvalidRequest(f"Invalid boolean value for {field}.")
	raise InvalidRequest(f"Invalid boolean value for {field}.")
