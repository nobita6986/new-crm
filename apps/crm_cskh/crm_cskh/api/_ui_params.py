"""Input validation helpers for P1 UI read endpoints (`L-26`).

Normalises `page` and `page_size` across all UI list endpoints so malformed or
out-of-range query parameters always raise `InvalidRequest` (HTTP `400` with
stable `code = "INVALID_REQUEST"`) instead of an unhandled `ValueError` (`500`).
"""

from crm_cskh.services.errors import InvalidRequest

MAX_PAGE = 1_000_000


def _parse_positive_int(raw, default_value):
	if raw is None:
		return default_value
	if isinstance(raw, bool):
		raise InvalidRequest("The request is invalid.")
	if isinstance(raw, int):
		value = raw
	elif isinstance(raw, str):
		stripped = raw.strip()
		if stripped == "":
			return default_value
		try:
			value = int(stripped, 10)
		except ValueError as exc:
			raise InvalidRequest("The request is invalid.") from exc
	else:
		raise InvalidRequest("The request is invalid.")

	if value < 1:
		raise InvalidRequest("The request is invalid.")
	return value


def parse_pagination(
	page=1,
	page_size=20,
	*,
	default_page=1,
	default_page_size=20,
	max_page_size=100,
):
	"""Return `(page_num, limit, offset)` or raise `InvalidRequest` (`400`)."""
	page_num = _parse_positive_int(page, default_page)
	limit = _parse_positive_int(page_size, default_page_size)
	if page_num > MAX_PAGE or limit > max_page_size:
		raise InvalidRequest("The request is invalid.")
	offset = (page_num - 1) * limit
	return page_num, limit, offset


def parse_optional_str(raw, error_message="The request is invalid."):
	"""Return stripped string or `""`, raising `InvalidRequest` on non-strings."""
	if raw is None:
		return ""
	if not isinstance(raw, str):
		raise InvalidRequest(error_message)
	return raw.strip()
