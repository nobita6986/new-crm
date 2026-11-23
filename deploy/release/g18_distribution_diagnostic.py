"""Privacy-preserving SQL digest instrumentation for isolated G18 diagnosis."""

from __future__ import annotations

_ELIGIBLE_ANCHOR = 'dist_eligible_count = int((dist_prev.get("summary") or {}).get("eligible_count") or 0)\n'
_POLL_ANCHOR = '    dist_run_status = str(r_info.get("status") or "")\n'
_FINAL_ANCHOR = "dist_dup_leads = int(\n"
_OUTPUT_ANCHOR = '    "mariadb_explain_plans": explain_plans,\n'
_DEADLINE_ANCHOR = "d_deadline = time.perf_counter() + 1200.0\n"

_HELPERS = r'''
import re

dist_diag_enabled = os.environ.get("P4_G18_DISTRIBUTION_DIAGNOSTIC_ONLY") == "1"
dist_diag_started = time.perf_counter()
dist_diag_before = None
dist_diag_status_before = None
dist_diag_samples = []
dist_diag_next_sample = dist_diag_started + 30.0

def _p4_g18_sql_digest_snapshot():
    try:
        rows = frappe.db.sql(
            """SELECT DIGEST AS digest_key, DIGEST_TEXT AS normalized_sql,
                      COUNT_STAR AS execution_count, SUM_TIMER_WAIT AS total_time_ps,
                      MAX_TIMER_WAIT AS max_time_ps, SUM_ROWS_EXAMINED AS rows_examined,
                      SUM_ROWS_SENT AS rows_sent
               FROM performance_schema.events_statements_summary_by_digest
               WHERE SCHEMA_NAME = DATABASE() AND DIGEST IS NOT NULL
                 AND DIGEST_TEXT NOT LIKE '%events_statements_summary_by_digest%'
               ORDER BY SUM_TIMER_WAIT DESC LIMIT 500""",
            as_dict=True,
        )
        out = {}
        for row in rows or []:
            digest_key = str(row.get("digest_key") or "")
            normalized_sql = str(row.get("normalized_sql") or "")
            if not digest_key or not normalized_sql:
                continue
            fingerprint = hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()
            tables = sorted(set(re.findall(r"\x60(tab[^\x60]+)\x60", normalized_sql, flags=re.IGNORECASE)))
            out[fingerprint] = {
                "operation": (normalized_sql.strip().split(None, 1) or [""])[0].upper(),
                "tables": tables[:12],
                "execution_count": int(row.get("execution_count") or 0),
                "total_time_ps": int(row.get("total_time_ps") or 0),
                "max_time_ps": int(row.get("max_time_ps") or 0),
                "rows_examined": int(row.get("rows_examined") or 0),
                "rows_sent": int(row.get("rows_sent") or 0),
            }
        return {"available": True, "rows": out}
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__, "rows": {}}

def _p4_g18_global_status_snapshot():
    wanted = {"Questions", "Slow_queries", "Innodb_buffer_pool_reads",
              "Innodb_row_lock_waits", "Innodb_row_lock_time"}
    try:
        rows = frappe.db.sql("SHOW GLOBAL STATUS", as_dict=True)
        values = {}
        for row in rows or []:
            name = row.get("Variable_name") or row.get("variable_name")
            value = row.get("Value") or row.get("value")
            if name in wanted:
                try:
                    values[str(name)] = int(value or 0)
                except (TypeError, ValueError):
                    continue
        return {"available": True, "values": values}
    except Exception as exc:
        return {"available": False, "error_type": type(exc).__name__, "values": {}}

def _p4_g18_capture_sample(progress, status):
    global dist_diag_next_sample
    if not dist_diag_enabled or time.perf_counter() < dist_diag_next_sample:
        return
    now = time.perf_counter()
    digest_snapshot = _p4_g18_sql_digest_snapshot()
    status_snapshot = _p4_g18_global_status_snapshot()
    dist_diag_samples.append({
        "elapsed_sec": round(now - dist_diag_started, 1),
        "progress": int(progress or 0),
        "status": str(status or ""),
        "db_status": status_snapshot.get("values", {}),
        "digest_snapshot_available": bool(digest_snapshot.get("available")),
    })
    dist_diag_next_sample = now + 30.0

def _p4_g18_build_report(final_progress, final_status):
    if not dist_diag_enabled:
        return None
    after = _p4_g18_sql_digest_snapshot()
    status_after = _p4_g18_global_status_snapshot()
    before_rows = (dist_diag_before or {}).get("rows", {})
    ranked = []
    for fingerprint, row in (after.get("rows", {}) or {}).items():
        old = before_rows.get(fingerprint, {})
        calls = max(0, row["execution_count"] - int(old.get("execution_count") or 0))
        elapsed_ps = max(0, row["total_time_ps"] - int(old.get("total_time_ps") or 0))
        examined = max(0, row["rows_examined"] - int(old.get("rows_examined") or 0))
        sent = max(0, row["rows_sent"] - int(old.get("rows_sent") or 0))
        if calls or elapsed_ps:
            ranked.append({
                "query_shape_sha256": fingerprint,
                "operation": row["operation"],
                "tables": row["tables"],
                "execution_count_delta": calls,
                "total_time_ms_delta": round(elapsed_ps / 1000000000.0, 3),
                "max_time_ms_cumulative": round(row["max_time_ps"] / 1000000000.0, 3),
                "rows_examined_delta": examined,
                "rows_sent_delta": sent,
            })
    ranked.sort(key=lambda item: item["total_time_ms_delta"], reverse=True)
    status_before = (dist_diag_status_before or {}).get("values", {})
    status_deltas = {
        key: max(0, int(value) - int(status_before.get(key) or 0))
        for key, value in (status_after.get("values", {}) or {}).items()
    }
    return {
        "schema_version": "cskh-p4-g18-distribution-sql-diagnostic-v1",
        "mode": "DIAGNOSTIC_ONLY_NOT_ACCEPTANCE",
        "raw_sql_or_bind_values_recorded": False,
        "sql_fingerprints_are_sha256_of_database_normalized_digest_text": True,
        "performance_schema_available": bool(after.get("available") and (dist_diag_before or {}).get("available")),
        "sample_interval_sec": 30,
        "poll_limit_sec": float(os.environ.get("P4_G18_DIAGNOSTIC_POLL_SECONDS", "1200")),
        "worker_elapsed_sec": round(time.perf_counter() - dist_diag_started, 3),
        "final_progress": int(final_progress or 0),
        "target_count": 10000,
        "final_status": str(final_status or ""),
        "global_status_delta": status_deltas,
        "progress_samples": dist_diag_samples,
        "top_query_shapes_by_cumulative_db_time": ranked[:25],
    }
'''


def instrument_s1_benchmark_script(source: str, *, poll_seconds: int) -> str:
    """Add opt-in diagnostic hooks; regular benchmark source remains untouched."""
    if not 1 <= poll_seconds <= 7200:
        raise ValueError("poll_seconds must be between 1 and 7200")
    anchors = (
        _ELIGIBLE_ANCHOR,
        _POLL_ANCHOR,
        _FINAL_ANCHOR,
        _OUTPUT_ANCHOR,
        _DEADLINE_ANCHOR,
    )
    if any(source.count(anchor) != 1 for anchor in anchors):
        raise ValueError("G18 diagnostic injection anchor missing or ambiguous")

    before = (
        _ELIGIBLE_ANCHOR
        + _HELPERS
        + r"""
if dist_diag_enabled:
    dist_diag_before = _p4_g18_sql_digest_snapshot()
    dist_diag_status_before = _p4_g18_global_status_snapshot()
"""
    )
    poll = (
        _POLL_ANCHOR
        + r"""
    _p4_g18_capture_sample(p_cur, dist_run_status)
"""
    )
    final = (
        r"""
dist_diag_result = _p4_g18_build_report(
    dist_progress_samples[-1] if dist_progress_samples else 0, dist_run_status
)
"""
        + _FINAL_ANCHOR
    )
    output = '    "_g18_distribution_diagnostic": dist_diag_result,\n' + _OUTPUT_ANCHOR
    result = (
        source.replace(_ELIGIBLE_ANCHOR, before)
        .replace(_POLL_ANCHOR, poll)
        .replace(_FINAL_ANCHOR, final)
        .replace(_OUTPUT_ANCHOR, output)
        .replace(
            _DEADLINE_ANCHOR,
            'd_deadline = time.perf_counter() + float(os.environ.get("P4_G18_DIAGNOSTIC_POLL_SECONDS", "1200"))\n',
        )
    )
    if result == source:
        raise ValueError("G18 diagnostic injection did not change the benchmark script")
    return result
