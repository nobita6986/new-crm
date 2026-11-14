"""Opt-in, privacy-preserving SQL timing for isolated P4 diagnostics only."""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import contextmanager

_STRING_LITERAL = re.compile(r"'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"", re.DOTALL)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--(?:[ \t]|$)[^\r\n]*|#[^\r\n]*")
_HEX_LITERAL = re.compile(r"(?<![A-Za-z0-9_])0x[0-9A-Fa-f]+(?![A-Za-z0-9_])", re.IGNORECASE)
_NUMBER_LITERAL = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![A-Za-z0-9_])")


def normalize_sql_shape(query: object) -> str:
	"""Return a value-free SQL shape; never return bind values or source literals."""
	text = str(query)
	# Mask quoted literals first so comment markers inside a string are not
	# mistaken for SQL comments.
	text = _STRING_LITERAL.sub("?", text)
	text = _BLOCK_COMMENT.sub(" ", text)
	text = _LINE_COMMENT.sub(" ", text)
	text = _HEX_LITERAL.sub("?", text)
	text = _NUMBER_LITERAL.sub("?", text)
	text = " ".join(text.split()).casefold()
	return re.sub(r"\s*([=<>!]+)\s*", r"\1", text)


def sql_fingerprint(query: str) -> str:
	"""Hash a normalized shape without caching raw query text in process memory."""
	return hashlib.sha256(normalize_sql_shape(query).encode("utf-8")).hexdigest()


class SQLCallRecorder:
	"""Bounded aggregate SQL counters; stores fingerprints and durations only."""

	def __init__(self, run_id: str, *, max_fingerprints: int = 256):
		self._run_id_sha256 = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()
		self._max_fingerprints = max(1, int(max_fingerprints))
		self._started_ns = time.perf_counter_ns()
		self._query_count = 0
		self._sql_ns = 0
		self._fingerprints: dict[str, dict[str, int]] = {}
		self._overflow = {"count": 0, "total_ns": 0, "max_ns": 0}

	def record(self, query: object, elapsed_ns: int) -> None:
		elapsed_ns = max(0, int(elapsed_ns))
		fingerprint = sql_fingerprint(str(query))
		self._query_count += 1
		self._sql_ns += elapsed_ns
		entry = self._fingerprints.get(fingerprint)
		if entry is None and len(self._fingerprints) >= self._max_fingerprints:
			entry = self._overflow
		else:
			if entry is None:
				entry = {"count": 0, "total_ns": 0, "max_ns": 0}
				self._fingerprints[fingerprint] = entry
		entry["count"] += 1
		entry["total_ns"] += elapsed_ns
		entry["max_ns"] = max(entry["max_ns"], elapsed_ns)

	def snapshot(self, progress: int | None) -> dict[str, object]:
		"""Build a JSON-safe record containing aggregate counters and hashes only."""
		fingerprints = [
			{
				"sha256": fingerprint,
				"count": stats["count"],
				"total_ms": round(stats["total_ns"] / 1_000_000, 3),
				"max_ms": round(stats["max_ns"] / 1_000_000, 3),
			}
			for fingerprint, stats in self._fingerprints.items()
		]
		fingerprints.sort(
			key=lambda item: (
				-float(item["total_ms"]),
				-int(item["count"]),
				str(item["sha256"]),
			)
		)
		return {
			"schema_version": "cskh-p4-g18-distribution-sql-diagnostic-v1",
			"run_id_sha256": self._run_id_sha256,
			"progress": None if progress is None else max(0, int(progress)),
			"elapsed_ms": round((time.perf_counter_ns() - self._started_ns) / 1_000_000, 3),
			"sql_query_count": self._query_count,
			"sql_total_ms": round(self._sql_ns / 1_000_000, 3),
			"fingerprints": fingerprints,
			"fingerprint_overflow": {
				"count": self._overflow["count"],
				"total_ms": round(self._overflow["total_ns"] / 1_000_000, 3),
				"max_ms": round(self._overflow["max_ns"] / 1_000_000, 3),
			},
		}


@contextmanager
def profile_database_sql(db: object, run_id: str, *, max_fingerprints: int = 256):
	"""Temporarily time DB SQL calls, restoring the exact instance methods in `finally`."""
	recorder = SQLCallRecorder(run_id, max_fingerprints=max_fingerprints)
	original_sql = db.sql
	instance_dict = getattr(db, "__dict__", {})
	had_instance_sql = "sql" in instance_dict
	old_instance_sql = instance_dict.get("sql")

	def measured_sql(query, *args, **kwargs):
		started_ns = time.perf_counter_ns()
		try:
			return original_sql(query, *args, **kwargs)
		finally:
			try:
				recorder.record(query, time.perf_counter_ns() - started_ns)
			except Exception:
				pass

	db.sql = measured_sql
	try:
		yield recorder
	finally:
		if had_instance_sql:
			db.sql = old_instance_sql
		else:
			try:
				delattr(db, "sql")
			except AttributeError:
				pass
