"""Pure, conservative normalization for the mobile-number format used by P1.

This validates syntax, not carrier allocation or whether a subscriber exists.
Numeric spreadsheet cells must be reviewed; their lost leading zero is never
guessed (P1 §5.4 #4 and §9.2).

The module is intentionally free of any Frappe import so it can be unit-tested
without a site.
"""

import re
import unicodedata

# A Vietnamese mobile subscriber number is 9 digits starting with 3, 5, 7, 8 or 9.
MOBILE_PREFIXES = ("3", "5", "7", "8", "9")
NATIONAL_PREFIX = "0"
COUNTRY_CODE = "84"
CANONICAL_PREFIX = "+84"

MAX_INPUT_LENGTH = 64
MAX_CANONICAL_LENGTH = 16

_SEPARATORS = re.compile(r"[\s().\u00a0\u2007\u202f_-]")
_ALLOWED = re.compile(r"^\+?[0-9]+$")
_SCIENTIFIC = re.compile(r"^[+-]?[0-9]*\.?[0-9]+[eE][+-]?[0-9]+$")


class InvalidPhone(ValueError):
	"""Raised when a phone cell cannot be turned into a canonical mobile number."""

	code = "INVALID_PHONE"


def looks_like_scientific_notation(raw) -> bool:
	"""Detect Excel scientific notation, which must never be trusted as a phone."""
	if not isinstance(raw, str):
		return False
	value = unicodedata.normalize("NFKC", raw).strip()
	if not value or "e" not in value.lower():
		return False
	return bool(_SCIENTIFIC.match(value.replace(" ", "")))


def _strip_separators(value: str) -> str:
	return _SEPARATORS.sub("", value)


def normalize_vn_phone(raw) -> str:
	"""Return the canonical E.164 form of a Vietnamese mobile number.

	Accepted inputs (P1 §5.4): `0xxxxxxxxx`, `84xxxxxxxxx`, `+84xxxxxxxxx`, with
	spaces, dots, dashes, parentheses as separators.

	Rejected: landlines, international numbers outside +84, short strings,
	broken spreadsheet scientific notation, unsupported characters, and cells
	that carry more than one number.
	"""
	if not isinstance(raw, str):
		raise InvalidPhone("A phone number must be supplied as a text value.")
	if len(raw) > MAX_INPUT_LENGTH:
		raise InvalidPhone("A phone number must be a short text value.")
	if looks_like_scientific_notation(raw):
		raise InvalidPhone("The phone cell looks like scientific notation and cannot be trusted.")

	value = unicodedata.normalize("NFKC", raw).strip()
	if not value:
		raise InvalidPhone("A phone number is required.")

	# Two numbers in one cell must be split by the operator, never guessed.
	if re.search(r"[0-9]{9,}.*[0-9]{4,}", value):
		raise InvalidPhone("The phone cell contains more than one number.")

	stripped = _strip_separators(value)
	if not stripped:
		raise InvalidPhone("A phone number is required.")
	if not _ALLOWED.match(stripped):
		raise InvalidPhone("The phone cell contains unsupported characters.")

	if stripped.startswith("+"):
		if not stripped.startswith(CANONICAL_PREFIX):
			raise InvalidPhone("Only Vietnamese numbers are supported.")
		digits = stripped[len(CANONICAL_PREFIX) :]
	elif stripped.startswith(COUNTRY_CODE):
		digits = stripped[len(COUNTRY_CODE) :]
	elif stripped.startswith(NATIONAL_PREFIX):
		digits = stripped[1:]
	else:
		raise InvalidPhone("The phone number must start with 0, 84 or +84.")

	if len(digits) != 9 or digits[0] not in MOBILE_PREFIXES:
		raise InvalidPhone("The phone number is outside the supported Vietnamese mobile format.")

	canonical = CANONICAL_PREFIX + digits
	if len(canonical) > MAX_CANONICAL_LENGTH:
		raise InvalidPhone("The phone number is outside the supported Vietnamese mobile format.")
	return canonical
