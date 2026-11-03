"""Strict request parameter parsers for P1 HTTP endpoints (JSON & form-urlencoded)."""

from __future__ import annotations

from crm_cskh.services.errors import InvalidRequest

MAX_PAGE = 1_000_000


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


def parse_int(
	value,
	*,
	default: int | None = None,
	minimum: int | None = None,
	maximum: int | None = None,
	field: str = "value",
) -> int:
	"""Parse a strict base-10 integer from JSON (`int`) or form/query (`str`) (`L-26`).

	Rejects ``bool`` (subclass of ``int`` in Python), floats/decimal strings,
	non-numeric strings, and out-of-range integers with ``InvalidRequest``
	(HTTP 400 ``INVALID_REQUEST``), never letting ``int(...)`` leak HTTP 500.
	``None`` or an empty/whitespace string returns ``default`` when provided.
	"""
	if value is None:
		if default is not None:
			parsed = default
		else:
			raise InvalidRequest(f"Invalid integer value for {field}.")
	elif isinstance(value, bool):
		raise InvalidRequest(f"Invalid integer value for {field}.")
	elif isinstance(value, int):
		parsed = value
	elif isinstance(value, str):
		stripped = value.strip()
		if stripped == "":
			if default is not None:
				parsed = default
			else:
				raise InvalidRequest(f"Invalid integer value for {field}.")
		else:
			try:
				parsed = int(stripped, 10)
			except ValueError as exc:
				raise InvalidRequest(f"Invalid integer value for {field}.") from exc
	else:
		raise InvalidRequest(f"Invalid integer value for {field}.")

	if minimum is not None and parsed < minimum:
		raise InvalidRequest(f"Invalid integer value for {field}.")
	if maximum is not None and parsed > maximum:
		raise InvalidRequest(f"Invalid integer value for {field}.")
	return parsed


def parse_pagination(
	page=1,
	page_size=20,
	*,
	default_page: int = 1,
	default_page_size: int = 20,
	max_page_size: int = 100,
) -> tuple[int, int, int]:
	"""Return `(page_num, limit, offset)` or raise `InvalidRequest` (`400`, `L-26`)."""
	page_num = parse_int(page, default=default_page, minimum=1, maximum=MAX_PAGE, field="page")
	limit = parse_int(
		page_size, default=default_page_size, minimum=1, maximum=max_page_size, field="page_size"
	)
	offset = (page_num - 1) * limit
	return page_num, limit, offset


def parse_optional_str(value, error_message: str = "The request is invalid.") -> str:
	"""Return stripped string or `""`, raising `InvalidRequest` (`400`) on non-strings."""
	if value is None:
		return ""
	if not isinstance(value, str):
		raise InvalidRequest(error_message)
	return value.strip()
