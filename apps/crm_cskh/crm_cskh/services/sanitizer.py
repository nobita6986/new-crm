"""Server-side text sanitization, CSV formula neutralization, and PII redaction for P2 (`SEC2-12..14`)."""

from __future__ import annotations

import html
import re

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

# Strip executable HTML blocks together with their body, then strip any remaining markup tags.
_EXECUTABLE_TAG_BLOCK_RE = re.compile(
	r"<(script|style|iframe|object|embed|svg|math)\b[^>]*>.*?</\1\s*>",
	re.IGNORECASE | re.DOTALL,
)
_ANY_HTML_TAG_RE = re.compile(r"<[^>]+>")
_JS_PROTOCOL_RE = re.compile(r"javascript\s*:", re.IGNORECASE)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Vietnamese phone & email patterns for error/log scrubbing (`SEC2-13`, `SEC2-14`).
_VN_PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:3|5|7|8|9)\d{8}(?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def sanitize_note_text(value: str | None, *, max_length: int = 4000) -> str:
	"""Sanitize user-supplied free text (`note`, `reason`, `completion_reason`, `review_note`).

	* Removes `<script>`, `<iframe>`, `<object>`, `<embed>`, `<svg>`, `<style>` blocks and all HTML tags.
	* Neutralizes inline `javascript:` protocol tokens.
	* Strips non-printable ASCII control characters while preserving `\n` and Vietnamese Unicode.
	* Neutralizes leading spreadsheet formula prefixes (`=`, `+`, `-`, `@`, `\t`, `\r`) with `'`.
	"""
	if value is None:
		return ""
	text = str(value)
	if not text:
		return ""
	text = _CONTROL_CHAR_RE.sub("", text)
	text = _EXECUTABLE_TAG_BLOCK_RE.sub("", text)
	text = _ANY_HTML_TAG_RE.sub("", text)
	# Also decode HTML entities once if someone double-encoded `<script>` and re-strip tags!
	unescaped = html.unescape(text)
	if "<" in unescaped and ">" in unescaped:
		text = _EXECUTABLE_TAG_BLOCK_RE.sub("", unescaped)
		text = _ANY_HTML_TAG_RE.sub("", text)
	text = _JS_PROTOCOL_RE.sub("", text)
	text = text.strip()
	if not text:
		return ""
	if text[:1] in FORMULA_PREFIXES:
		text = "'" + text
	if max_length and len(text) > max_length:
		text = text[:max_length].rstrip()
	return text


def neutralize_csv_cell(value) -> str:
	"""Ensure a value exported to CSV/Excel cannot execute as a spreadsheet formula."""
	if value is None:
		return ""
	text = str(value)
	if not text:
		return ""
	text = sanitize_note_text(text, max_length=0)
	if text[:1] in FORMULA_PREFIXES:
		return "'" + text
	return text


def redact_pii_text(text: str | None) -> str:
	"""Redact phone numbers and emails before writing diagnostic error messages or logs."""
	if not text:
		return ""
	cleaned = _VN_PHONE_RE.sub("[REDACTED_PHONE]", str(text))
	cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
	return cleaned
