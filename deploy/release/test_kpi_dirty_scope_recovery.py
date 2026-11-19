#!/usr/bin/env python3
"""Offline pure-Python regression tests for `F-KPI-1305` (`kpi_runs.py` Dirty Scope transaction & worker recovery).

Covers:
  1. MariaDB 1020 (`ER_CHECKREAD`) implicitly aborting tx & dropping savepoints -> ZERO `1305 SAVEPOINT does not exist`,
     clean full `frappe.db.rollback()`, bounded retry succeeds, Dirty Scope persisted `pending`, committed API response intact.
  2. Explicit `1305 SAVEPOINT ... does not exist` error classified retryable and recovered cleanly.
  3. Exhausted Dirty Scope write failures (all `DIRTY_FLUSH_MAX_ATTEMPTS` fail) -> bounded retry cap respected,
     aborted tx reset, structured diagnostics emitted via `frappe.logger` + `frappe.log_error`, committed `record_attempt`
     response preserved (ZERO HTTP 500), AND subsequent call after error recovers cleanly.
  4. Non-retryable Dirty Scope write error fails fast without breaking committed API response, and follow-up call succeeds.
  5. Multi-key batch failure falls back to per-key atomic commits so healthy keys persist independently.
  6. Concurrent `process_dirty_scopes` worker hitting MariaDB 1020 on `claim_pending` and `mark_processed` while a
     concurrent `record_attempt` resets the same scope back to `pending` -> worker retries cleanly, does NOT clobber
     `pending` back to `processed`, and re-enqueues a follow-up drain.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PKG_ROOT = REPO_ROOT / "apps" / "crm_cskh"
APP_DIR = APP_PKG_ROOT / "crm_cskh"


class _SimQueryDeadlockError(Exception):
    pass


class _SimOperationalError(Exception):
    pass


class _AttrDict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _TxHookQueue:
    def __init__(self) -> None:
        self._callbacks: list = []

    def add(self, fn) -> None:
        self._callbacks.append(fn)

    def reset(self) -> None:
        self._callbacks.clear()

    def run(self) -> None:
        pending = list(self._callbacks)
        self._callbacks.clear()
        for fn in pending:
            fn()


class _CaptureLogger:
    def __init__(self, sink: list[dict]) -> None:
        self._sink = sink

    def error(self, msg, *args, **kwargs) -> None:
        self._sink.append(
            {"level": "error", "message": msg % args if args else str(msg)}
        )

    def warning(self, msg, *args, **kwargs) -> None:
        self._sink.append(
            {"level": "warning", "message": msg % args if args else str(msg)}
        )

    def info(self, msg, *args, **kwargs) -> None:
        self._sink.append(
            {"level": "info", "message": msg % args if args else str(msg)}
        )


class _MariaDBDirtyScopeHarness:
    """In-memory MariaDB 11.8 + Frappe transaction/savepoint harness for `kpi_runs.py`."""

    def __init__(self) -> None:
        self.active_savepoints: list[str] = []
        self.in_tx = False
        self.aborted_tx = False
        self.committed_attempts: list[dict] = []
        self.pending_attempts: list[dict] = []
        self.dirty_scopes: dict[str, _AttrDict] = {}
        self.uncommitted_ops: list[tuple] = []
        self.after_commit = _TxHookQueue()
        self.after_rollback = _TxHookQueue()
        self.rollback_calls: list[dict] = []
        self.commit_count = 0
        self.upsert_attempts = 0
        self.upsert_exceptions: list[Exception | None] = []
        self.per_key_fail_keys: set[str] = set()
        self.claim_fail_once = False
        self.mark_processed_fail_once = False
        self.enqueue_calls: list[dict] = []
        self.error_logs: list[dict] = []
        self.logger_entries: list[dict] = []
        self.cache_bumps: list[str] = []
        self.cache_store: dict[str, str] = {}
        self._hash_seq = 0

    def next_hash(self, length: int = 10) -> str:
        self._hash_seq += 1
        return f"ds{self._hash_seq:08d}"[:length]

    def seed_scope(
        self,
        scope_key: str,
        dirty_date: str,
        *,
        status: str = "processed",
        trigger_reference: str = "ATT-OLD",
        name: str = "ds-seed-001",
    ) -> None:
        parts = scope_key.split("|")
        self.dirty_scopes[scope_key] = _AttrDict(
            name=name,
            scope_key=scope_key,
            dirty_date=dirty_date,
            period_month=dirty_date[:7],
            campaign=None if parts[1] == "*" else parts[1],
            team_node=None if parts[2] == "*" else parts[2],
            owner_user=None if parts[3] == "*" else parts[3],
            reason="attempt_recorded",
            is_prior_period=0,
            trigger_reference=trigger_reference,
            status=status,
            first_dirtied_at=dt.datetime(2026, 11, 19, 8, 0, 0),
            last_dirtied_at=dt.datetime(2026, 11, 19, 8, 0, 0),
        )

    def savepoint(self, sp_name: str) -> None:
        self.in_tx = True
        self.aborted_tx = False
        self.active_savepoints.append(sp_name)

    def rollback(self, save_point: str | None = None) -> None:
        self.rollback_calls.append(
            {
                "save_point": save_point,
                "active_savepoints": list(self.active_savepoints),
                "aborted_tx": self.aborted_tx,
            }
        )
        if save_point is not None:
            if save_point not in self.active_savepoints:
                raise _SimOperationalError(
                    f"(1305, 'SAVEPOINT {save_point} does not exist')"
                )
            idx = self.active_savepoints.index(save_point)
            self.active_savepoints = self.active_savepoints[: idx + 1]
            return
        self.pending_attempts.clear()
        self.uncommitted_ops.clear()
        self.active_savepoints.clear()
        self.in_tx = False
        self.aborted_tx = False
        self.after_commit.reset()
        self.after_rollback.run()

    def commit(self) -> None:
        if self.pending_attempts:
            self.committed_attempts.extend(self.pending_attempts)
            self.pending_attempts.clear()
        for op in self.uncommitted_ops:
            self._apply_op(op)
        self.uncommitted_ops.clear()
        self.active_savepoints.clear()
        self.in_tx = False
        self.aborted_tx = False
        self.commit_count += 1
        self.after_rollback.reset()
        self.after_commit.run()

    def _apply_op(self, op: tuple) -> None:
        kind = op[0]
        if kind == "upsert":
            row = op[1]
            skey = row["scope_key"]
            if skey in self.dirty_scopes:
                ex = self.dirty_scopes[skey]
                ex["status"] = "pending"
                ex["last_dirtied_at"] = row["last_dirtied_at"]
                ex["modified"] = row["modified"]
                ex["modified_by"] = row["modified_by"]
                ex["reason"] = row["reason"]
                ex["is_prior_period"] = max(
                    int(ex.get("is_prior_period") or 0),
                    int(row.get("is_prior_period") or 0),
                )
                if row.get("trigger_reference"):
                    ex["trigger_reference"] = row["trigger_reference"]
            else:
                self.dirty_scopes[skey] = _AttrDict(row)
        elif kind == "claim_processing":
            ids = set(op[1])
            for row in self.dirty_scopes.values():
                if row["name"] in ids and row["status"] == "pending":
                    row["status"] = "processing"
        elif kind == "mark_processed":
            run_id, done_dt, ids = op[1], op[2], set(op[3])
            for row in self.dirty_scopes.values():
                if row["name"] in ids and row["status"] == "processing":
                    row["status"] = "processed"
                    row["processed_by_run"] = run_id
                    row["processed_at"] = done_dt
        elif kind == "requeue_pending":
            ids = set(op[1])
            for row in self.dirty_scopes.values():
                if row["name"] in ids:
                    row["status"] = "pending"

    def _engine_abort(self, exc: Exception) -> None:
        self.in_tx = False
        self.aborted_tx = True
        self.active_savepoints.clear()
        self.uncommitted_ops.clear()
        raise exc

    def sql(self, query: str, values=None, as_dict: bool = False):
        q = " ".join(query.strip().split())
        q_low = q.lower()
        self.in_tx = True

        if q_low.startswith("insert into `tabcskh dirty scope`"):
            self.upsert_attempts += 1
            vals = list(values or ())
            ncols = 19
            n_rows = len(vals) // ncols
            rows = []
            for i in range(n_rows):
                chunk = vals[i * ncols : (i + 1) * ncols]
                rows.append(
                    {
                        "name": chunk[0],
                        "creation": chunk[1],
                        "modified": chunk[2],
                        "modified_by": chunk[3],
                        "owner": chunk[4],
                        "docstatus": chunk[5],
                        "idx": chunk[6],
                        "scope_key": chunk[7],
                        "dirty_date": chunk[8],
                        "period_month": chunk[9],
                        "campaign": chunk[10],
                        "team_node": chunk[11],
                        "owner_user": chunk[12],
                        "reason": chunk[13],
                        "is_prior_period": chunk[14],
                        "trigger_reference": chunk[15],
                        "status": chunk[16],
                        "first_dirtied_at": chunk[17],
                        "last_dirtied_at": chunk[18],
                    }
                )
            if self.upsert_exceptions:
                planned = self.upsert_exceptions.pop(0)
                if planned is not None:
                    self._engine_abort(planned)
            for row in rows:
                if row["scope_key"] in self.per_key_fail_keys:
                    self._engine_abort(
                        _SimQueryDeadlockError(
                            "(1020, \"Record has changed since last read in table 'tabCSKH Dirty Scope'\")"
                        )
                    )
            for row in rows:
                self.uncommitted_ops.append(("upsert", row))
            return []

        if q_low.startswith(
            "select name, dirty_date, campaign, reason, is_prior_period, trigger_reference from `tabcskh dirty scope` where status = 'pending'"
        ):
            limit = int((values or (100,))[0])
            pending = [
                _AttrDict(r)
                for r in self.dirty_scopes.values()
                if r["status"] == "pending"
            ]
            pending.sort(key=lambda r: str(r.get("first_dirtied_at") or ""))
            return pending[:limit]

        if q_low.startswith(
            "update `tabcskh dirty scope` set status = 'processing' where name in"
        ):
            if self.claim_fail_once:
                self.claim_fail_once = False
                self._engine_abort(
                    _SimQueryDeadlockError(
                        "(1020, \"Record has changed since last read in table 'tabCSKH Dirty Scope'\")"
                    )
                )
            self.uncommitted_ops.append(("claim_processing", list(values or ())))
            return []

        if q_low.startswith("update `tabcskh dirty scope` set status = 'processed'"):
            if self.mark_processed_fail_once:
                self.mark_processed_fail_once = False
                self._engine_abort(
                    _SimQueryDeadlockError(
                        "(1020, \"Record has changed since last read in table 'tabCSKH Dirty Scope'\")"
                    )
                )
            vals = list(values or ())
            self.uncommitted_ops.append(("mark_processed", vals[0], vals[1], vals[2:]))
            return []

        if q_low.startswith(
            "update `tabcskh dirty scope` set status = 'pending' where name in"
        ):
            self.uncommitted_ops.append(("requeue_pending", list(values or ())))
            return []

        if q_low.startswith(
            "select 1 from `tabcskh dirty scope` where status = 'pending'"
        ):
            return (
                [(1,)]
                if any(r["status"] == "pending" for r in self.dirty_scopes.values())
                else []
            )

        raise AssertionError(f"Unexpected SQL in harness: {q}")

    def table_exists(self, _doctype: str) -> bool:
        return True


def _load_kpi_runs_with_harness(harness: _MariaDBDirtyScopeHarness):
    if str(APP_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(APP_PKG_ROOT))

    frappe_mod = types.ModuleType("frappe")
    frappe_utils = types.ModuleType("frappe.utils")
    frappe_mod.db = harness
    frappe_mod.local = types.SimpleNamespace()
    frappe_mod.flags = types.SimpleNamespace(
        in_test=False, cskh_suppress_dirty_enqueue=False
    )
    frappe_mod.session = types.SimpleNamespace(user="staff.a@dev.local")
    frappe_mod.cache = types.SimpleNamespace(
        make_key=lambda k: f"site:{k}",
        set=lambda k, v, ex=None, nx=False: (
            False
            if (nx and k in harness.cache_store)
            else (harness.cache_store.__setitem__(k, v) or True)
        ),
        delete=lambda k: harness.cache_store.pop(k, None),
    )
    frappe_mod.generate_hash = harness.next_hash
    frappe_mod.enqueue = lambda method, **kw: harness.enqueue_calls.append(
        {"method": method, **kw}
    )
    frappe_mod.log_error = lambda *args, **kwargs: harness.error_logs.append(
        {
            "args": [str(a) for a in args],
            "kwargs": {k: str(v) for k, v in kwargs.items()},
        }
    )
    frappe_mod.logger = lambda _name="frappe": _CaptureLogger(harness.logger_entries)

    frappe_utils.cint = lambda x: int(x or 0)
    frappe_utils.get_datetime = lambda x: (
        x if isinstance(x, dt.datetime) else dt.datetime.fromisoformat(str(x))
    )
    frappe_utils.now_datetime = lambda: dt.datetime(2026, 11, 19, 10, 40, 0)

    p1_perm = types.ModuleType("crm_cskh.p1_permissions")
    p1_perm.ROLE_ADMIN = "CSKH CRM Admin"
    p1_perm.ROLE_BOD = "CSKH BOD Viewer"
    p1_perm._is_system = lambda _u: False
    p1_perm._node_in_subtree = lambda *a, **k: True
    p1_perm._roles = lambda _u: {"Sales User"}

    p2_perm = types.ModuleType("crm_cskh.p2_permissions")
    p2_perm._in_team = lambda *a, **k: True
    p2_perm._is_leader = lambda *a, **k: False
    p2_perm._is_staff = lambda *a, **k: True

    p3_models = types.ModuleType("crm_cskh.p3_models")

    @contextmanager
    def _auth_kpi_write():
        yield

    p3_models.authorized_kpi_write = _auth_kpi_write

    rt_cache = types.ModuleType("crm_cskh.services.runtime_cache")
    rt_cache.DOMAIN_DIRTY_SCOPE = "dirty_scope"
    rt_cache.DOMAIN_KPI_SNAPSHOT = "kpi_snapshot"
    rt_cache.table_exists = lambda _dt: True
    rt_cache.bump_cache_domain = lambda *domains: harness.cache_bumps.extend(domains)

    cal_mod = types.ModuleType("crm_cskh.services.calendar")
    cal_mod.format_calendar_iso = lambda x: str(x)

    calc_mod = types.ModuleType("crm_cskh.services.kpi_calculator")
    calc_mod.METRIC_LABELS_VI = {}
    calc_mod.METRIC_VERSION = "cskh-metrics-v2"
    calc_mod.SCORE_COMPONENT_LABELS_VI = {}
    calc_mod.compute_period_snapshots = lambda *a, **k: []

    periods_mod = types.ModuleType("crm_cskh.services.kpi_periods")

    class _PeriodSpec:
        def __init__(self, period_type: str, period_key: str) -> None:
            self.period_type = period_type
            self.period_key = period_key

    periods_mod.PeriodSpec = _PeriodSpec
    periods_mod._parse_iso_date = lambda s, _f="": dt.date.fromisoformat(str(s)[:10])
    periods_mod.affected_standard_periods_for_date = lambda d: [
        _PeriodSpec("daily", d.isoformat()),
        _PeriodSpec("monthly", d.strftime("%Y-%m")),
    ]
    periods_mod.iter_days = lambda _a, _b: []
    periods_mod.resolve_period_spec = lambda pt, pk: _PeriodSpec(pt, pk)
    periods_mod.today_hcm = lambda _x=None: dt.date(2026, 11, 19)

    p3_flags = types.ModuleType("crm_cskh.services.p3_flags")
    p3_flags.is_p3_reporting_enabled = lambda: True

    crm_pkg = types.ModuleType("crm_cskh")
    crm_pkg.__path__ = [str(APP_DIR)]
    svc_pkg = types.ModuleType("crm_cskh.services")
    svc_pkg.__path__ = [str(APP_DIR / "services")]

    injected = {
        "frappe": frappe_mod,
        "frappe.utils": frappe_utils,
        "crm_cskh": crm_pkg,
        "crm_cskh.services": svc_pkg,
        "crm_cskh.p1_permissions": p1_perm,
        "crm_cskh.p2_permissions": p2_perm,
        "crm_cskh.p3_models": p3_models,
        "crm_cskh.services.runtime_cache": rt_cache,
        "crm_cskh.services.calendar": cal_mod,
        "crm_cskh.services.kpi_calculator": calc_mod,
        "crm_cskh.services.kpi_periods": periods_mod,
        "crm_cskh.services.p3_flags": p3_flags,
    }
    saved: dict[str, object] = {}
    for mod_name in (
        *injected.keys(),
        "crm_cskh.services.errors",
        "crm_cskh.services.kpi_runs",
    ):
        saved[mod_name] = sys.modules.get(mod_name)
    sys.modules.pop("crm_cskh.services.errors", None)
    sys.modules.pop("crm_cskh.services.kpi_runs", None)
    sys.modules.update(injected)

    errors_mod = importlib.import_module("crm_cskh.services.errors")
    kpi_runs_mod = importlib.import_module("crm_cskh.services.kpi_runs")
    return frappe_mod, errors_mod, kpi_runs_mod, saved


def _restore_modules(saved: dict[str, object]) -> None:
    for mod_name, prev in saved.items():
        if prev is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = prev  # type: ignore[assignment]


class TestFKpi1305DirtyScopeRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _MariaDBDirtyScopeHarness()
        (
            self.frappe,
            self.errors,
            self.kpi_runs,
            self._saved_mods,
        ) = _load_kpi_runs_with_harness(self.harness)

    def tearDown(self) -> None:
        _restore_modules(self._saved_mods)

    def _simulate_committed_record_attempt(
        self,
        attempt_id: str,
        *,
        campaign: str = "CAMP-001",
        team_node: str = "TEAM-1",
        owner_user: str = "staff.a@dev.local",
        dirty_date: dt.datetime | None = None,
    ) -> dict:
        self.harness.pending_attempts.append({"attempt_id": attempt_id})
        self.kpi_runs.schedule_post_commit_dirty_invalidation(
            work_item="WI-001",
            campaign=campaign,
            owner_user=owner_user,
            team_node=team_node,
            event_dt=dirty_date or dt.datetime(2026, 11, 19, 10, 35, 0),
            reason="attempt_recorded",
            trigger_reference=attempt_id,
        )
        self.frappe.db.commit()
        return {"status": "recorded", "attempt_id": attempt_id, "work_item": "WI-001"}

    def test_f_kpi_1305_lost_savepoint_after_1020_recovers_without_1305_and_returns_committed_result(
        self,
    ) -> None:
        skey = "2026-11-19|CAMP-001|TEAM-1|staff.a@dev.local"
        self.harness.seed_scope(
            skey, "2026-11-19", status="processing", trigger_reference="ATT-OLD"
        )
        self.harness.upsert_exceptions = [
            _SimQueryDeadlockError(
                "(1020, \"Record has changed since last read in table 'tabCSKH Dirty Scope'\")"
            )
        ]

        with mock.patch.object(self.kpi_runs.time, "sleep"):
            res = self._simulate_committed_record_attempt("ATT-20261119-001")

        self.assertEqual(res["status"], "recorded")
        self.assertEqual(res["attempt_id"], "ATT-20261119-001")
        self.assertEqual(len(self.harness.committed_attempts), 1)
        self.assertEqual(self.harness.upsert_attempts, 2)
        # Never calls rollback(save_point=...) which would raise MariaDB 1305!
        self.assertTrue(
            all(call["save_point"] is None for call in self.harness.rollback_calls)
        )
        self.assertEqual(len(self.harness.rollback_calls), 1)
        self.assertEqual(self.harness.dirty_scopes[skey]["status"], "pending")
        self.assertEqual(
            self.harness.dirty_scopes[skey]["trigger_reference"], "ATT-20261119-001"
        )
        self.assertIn("dirty_scope", self.harness.cache_bumps)
        self.assertEqual(len(self.harness.enqueue_calls), 1)
        self.assertEqual(self.harness.error_logs, [])

    def test_f_kpi_1305_explicit_1305_savepoint_missing_is_retryable_and_recovers(
        self,
    ) -> None:
        err1305 = _SimOperationalError(
            "(1305, 'SAVEPOINT cskh_dirty_flush_batch does not exist')"
        )
        self.assertTrue(self.errors.is_savepoint_missing_error(err1305))
        self.assertTrue(self.errors.is_dirty_scope_retryable_error(err1305))

        skey = "2026-11-19|CAMP-001|TEAM-1|staff.a@dev.local"
        self.harness.upsert_exceptions = [err1305]

        with mock.patch.object(self.kpi_runs.time, "sleep"):
            res = self._simulate_committed_record_attempt("ATT-1305-RECOVER")

        self.assertEqual(res["status"], "recorded")
        self.assertEqual(self.harness.upsert_attempts, 2)
        self.assertEqual(self.harness.dirty_scopes[skey]["status"], "pending")
        self.assertEqual(
            self.harness.dirty_scopes[skey]["trigger_reference"], "ATT-1305-RECOVER"
        )

    def test_f_kpi_1305_exhausted_dirty_write_preserves_api_logs_diagnostics_and_recovers_next_call(
        self,
    ) -> None:
        skey = "2026-11-19|CAMP-001|TEAM-1|staff.a@dev.local"
        self.harness.seed_scope(
            skey, "2026-11-19", status="processed", trigger_reference="ATT-OLD"
        )
        self.harness.upsert_exceptions = [
            _SimQueryDeadlockError(
                "(1020, \"Record has changed since last read in table 'tabCSKH Dirty Scope'\")"
            )
            for _ in range(self.kpi_runs.DIRTY_FLUSH_MAX_ATTEMPTS)
        ]

        with mock.patch.object(self.kpi_runs.time, "sleep"):
            res1 = self._simulate_committed_record_attempt("ATT-EXHAUST-001")

        # 1. Committed API write MUST still return success (zero HTTP 500!)
        self.assertEqual(res1["status"], "recorded")
        self.assertEqual(res1["attempt_id"], "ATT-EXHAUST-001")
        self.assertEqual(len(self.harness.committed_attempts), 1)
        # 2. Retries strictly bounded at DIRTY_FLUSH_MAX_ATTEMPTS
        self.assertEqual(
            self.harness.upsert_attempts, self.kpi_runs.DIRTY_FLUSH_MAX_ATTEMPTS
        )
        self.assertFalse(self.harness.aborted_tx)
        # 3. Diagnostic logs emitted to BOTH logger and frappe.log_error
        self.assertEqual(len(self.harness.error_logs), 1)
        self.assertEqual(
            self.harness.error_logs[0]["kwargs"].get("title"),
            "CSKH Dirty Scope Post-Commit Flush Failed",
        )
        diag = json.loads(self.harness.error_logs[0]["kwargs"]["message"])
        self.assertEqual(diag["failed_scope_keys"], [skey])
        self.assertEqual(diag["attempts"], self.kpi_runs.DIRTY_FLUSH_MAX_ATTEMPTS)
        self.assertEqual(diag["trigger_references"], ["ATT-EXHAUST-001"])
        self.assertTrue(any(e["level"] == "error" for e in self.harness.logger_entries))

        # 4. Subsequent call after transient error clears MUST succeed and persist Dirty Scope cleanly!
        res2 = self._simulate_committed_record_attempt("ATT-FOLLOWUP-002")
        self.assertEqual(res2["status"], "recorded")
        self.assertEqual(len(self.harness.committed_attempts), 2)
        self.assertEqual(self.harness.dirty_scopes[skey]["status"], "pending")
        self.assertEqual(
            self.harness.dirty_scopes[skey]["trigger_reference"], "ATT-FOLLOWUP-002"
        )

    def test_f_kpi_1305_non_retryable_dirty_error_fails_fast_and_recovers_on_next_call(
        self,
    ) -> None:
        skey = "2026-11-19|CAMP-001|TEAM-1|staff.a@dev.local"
        self.harness.upsert_exceptions = [
            RuntimeError("simulated non-retryable I/O error")
        ]

        res1 = self._simulate_committed_record_attempt("ATT-NONRETRY-001")
        self.assertEqual(res1["status"], "recorded")
        self.assertEqual(self.harness.upsert_attempts, 1)
        self.assertEqual(len(self.harness.error_logs), 1)

        res2 = self._simulate_committed_record_attempt("ATT-NONRETRY-002")
        self.assertEqual(res2["status"], "recorded")
        self.assertEqual(self.harness.dirty_scopes[skey]["status"], "pending")

    def test_f_kpi_1305_multi_key_partial_failure_isolates_healthy_keys(self) -> None:
        k1 = "2026-11-19|CAMP-A|TEAM-1|staff.a@dev.local"
        k2 = "2026-11-19|CAMP-B|TEAM-1|staff.a@dev.local"
        k3 = "2026-11-19|CAMP-C|TEAM-1|staff.a@dev.local"
        self.harness.per_key_fail_keys.add(k2)

        for camp, ref in (
            ("CAMP-A", "REF-A"),
            ("CAMP-B", "REF-B"),
            ("CAMP-C", "REF-C"),
        ):
            self.kpi_runs.schedule_post_commit_dirty_invalidation(
                work_item="WI-MULTI",
                campaign=camp,
                owner_user="staff.a@dev.local",
                team_node="TEAM-1",
                event_dt=dt.datetime(2026, 11, 19, 10, 35, 0),
                reason="attempt_recorded",
                trigger_reference=ref,
            )
        with mock.patch.object(self.kpi_runs.time, "sleep"):
            self.frappe.db.commit()

        # Healthy keys k1 and k3 persisted cleanly; only k2 logged as failed!
        self.assertEqual(self.harness.dirty_scopes[k1]["status"], "pending")
        self.assertEqual(self.harness.dirty_scopes[k3]["status"], "pending")
        self.assertNotIn(k2, self.harness.dirty_scopes)
        self.assertEqual(len(self.harness.error_logs), 1)
        diag = json.loads(self.harness.error_logs[0]["kwargs"]["message"])
        self.assertEqual(diag["succeeded_count"], 2)
        self.assertEqual(diag["failed_scope_keys"], [k2])

    def test_f_kpi_1305_concurrent_worker_1020_retry_and_pending_preservation_re_enqueues(
        self,
    ) -> None:
        skey = "2026-11-19|CAMP-001|TEAM-1|staff.a@dev.local"
        self.harness.seed_scope(
            skey, "2026-11-19", status="pending", trigger_reference="ATT-1"
        )
        self.harness.claim_fail_once = True
        self.harness.mark_processed_fail_once = True

        concurrent_fired = {"done": False}

        def _fake_recompute(p_spec, **_kw):
            # Simulate concurrent `record_attempt` post-commit flush while worker is recomputing!
            if not concurrent_fired["done"]:
                concurrent_fired["done"] = True
                self.assertEqual(
                    self.harness.dirty_scopes[skey]["status"], "processing"
                )
                self._simulate_committed_record_attempt("ATT-CONCURRENT-2")
                self.assertEqual(self.harness.dirty_scopes[skey]["status"], "pending")
            return {"run_id": f"RUN-{p_spec.period_type}"}

        with (
            mock.patch.object(self.kpi_runs.time, "sleep"),
            mock.patch.object(
                self.kpi_runs, "execute_kpi_recompute", side_effect=_fake_recompute
            ),
        ):
            out = self.kpi_runs.process_dirty_scopes(max_scopes=10)

        self.assertEqual(out["processed_scopes"], 1)
        self.assertEqual(out["periods_recomputed"], 2)
        # Because concurrent `record_attempt` reset `status='pending'`, `mark_processed`
        # (`WHERE status='processing'`) MUST NOT overwrite it back to `'processed'`!
        self.assertEqual(self.harness.dirty_scopes[skey]["status"], "pending")
        self.assertEqual(
            self.harness.dirty_scopes[skey]["trigger_reference"], "ATT-CONCURRENT-2"
        )
        # Worker clears dedup flag and re-enqueues follow-up drain immediately!
        self.assertGreaterEqual(len(self.harness.enqueue_calls), 2)

        # Second drain processes the concurrent mutation cleanly to `'processed'`
        with mock.patch.object(
            self.kpi_runs,
            "execute_kpi_recompute",
            side_effect=lambda p, **_kw: {"run_id": f"RUN2-{p.period_type}"},
        ):
            out2 = self.kpi_runs.process_dirty_scopes(max_scopes=10)
        self.assertEqual(out2["processed_scopes"], 1)
        self.assertEqual(self.harness.dirty_scopes[skey]["status"], "processed")

    def test_f_kpi_1305_static_contract_removes_buggy_savepoint_rollback(self) -> None:
        src = (APP_DIR / "services" / "kpi_runs.py").read_text(encoding="utf-8")
        self.assertNotIn('rollback(save_point="cskh_dirty_flush_batch")', src)
        self.assertNotIn('savepoint("cskh_dirty_flush_batch")', src)
        self.assertIn("DIRTY_FLUSH_MAX_ATTEMPTS", src)
        self.assertIn("on duplicate key update", src.lower())
        self.assertIn("CSKH Dirty Scope Post-Commit Flush Failed", src)


if __name__ == "__main__":
    unittest.main()
