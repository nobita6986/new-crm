"""Scope-safe, epoch-invalidated runtime cache for CSKH hot read/write paths (`P4 G18`).

Guarantees:
  1. **Strict RBAC & Tenant/User Scope Isolation:**
     Cached entries never pool customer PII across users/roles. Actor profiles are keyed by
     `(site, user, epoch)`; immutable KPI snapshots are keyed by `(site, kpi_run_id, scope_type,
     campaign_filter, scope_ids, team_filters, epoch)` and returned as fresh copies AFTER caller
     RBAC checks (`resolve_report_actor`, `_validate_campaign_filter`, `_validate_team_filter`).
  2. **Immediate Cross-Worker Post-Write Invalidation:**
     Every write path (`save_policy`, `save_outcome`, `User` / `CRM Sales Hierarchy` / `CSKH Campaign`
     / `CSKH Working Calendar` / `CRM Holiday List` / `Translation` / `CSKH Attempt Correction`
     `doc_events`, `execute_kpi_recompute`, `_flush_pending_dirty_events`, `process_dirty_scopes`,
     and ownership history writes) bumps both the in-process epoch (`0 ms` lag) and the shared Redis
     hash `cskh:runtime_epochs` (plus `after_commit` & `after_rollback` hooks so rolled-back
     uncommitted reads never linger).
  3. **Zero Stale Reads in Legacy Unit Tests:**
     Unit tests that mutate DB tables directly via `frappe.db.set_value(..., update_modified=False)`
     run with `frappe.flags.in_test = True`, where cross-request DB caching defaults to off unless
     explicitly enabled via `enable_runtime_cache_for_test()` (`cskh_force_runtime_cache = True`).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime

import frappe
from frappe.utils import cint

try:
	import redis as _redis_mod
except Exception:  # pragma: no cover
	_redis_mod = None  # type: ignore[assignment]

DOMAIN_POLICY = "policy"
DOMAIN_OUTCOME = "outcome"
DOMAIN_CALENDAR = "calendar"
DOMAIN_CAMPAIGN = "campaign"
DOMAIN_USER_HIERARCHY = "user_hierarchy"
DOMAIN_TRANSLATIONS = "translations"
DOMAIN_KPI_SNAPSHOT = "kpi_snapshot"
DOMAIN_DIRTY_SCOPE = "dirty_scope"
DOMAIN_CORRECTION = "correction"
DOMAIN_ASSIGNMENT_HISTORY = "assignment_history"
DOMAIN_WORK_ITEM = "work_item"

ALL_DOMAINS = (
	DOMAIN_POLICY,
	DOMAIN_OUTCOME,
	DOMAIN_CALENDAR,
	DOMAIN_CAMPAIGN,
	DOMAIN_USER_HIERARCHY,
	DOMAIN_TRANSLATIONS,
	DOMAIN_KPI_SNAPSHOT,
	DOMAIN_DIRTY_SCOPE,
	DOMAIN_CORRECTION,
	DOMAIN_ASSIGNMENT_HISTORY,
	DOMAIN_WORK_ITEM,
)

_EPOCH_REDIS_HASH = "cskh:runtime_epochs"
_EPOCH_GEN_FIELD = "__gen__"
_DIAG_PROBE_FIELD = "__diag_probe__"
_DIAG_DISABLE_COALESCE_FIELD = "__diag_disable_coalesce__"
_DIAG_REDIS_STATS_HASH = "cskh:diag:coalesce_stats"
_EPOCH_SYNC_TTL_SEC = 0.50

_LRU_MISS = object()

_SF_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class _BoundedLRU:
	"""Thread-safe bounded LRU dictionary."""

	def __init__(self, maxsize: int = 1024):
		self._maxsize = max(int(maxsize), 16)
		self._lock = threading.RLock()
		self._data: OrderedDict = OrderedDict()

	def get(self, key, default=None):
		with self._lock:
			if key not in self._data:
				return default
			self._data.move_to_end(key)
			return self._data[key]

	def set(self, key, value) -> None:
		with self._lock:
			if key in self._data:
				self._data.move_to_end(key)
			self._data[key] = value
			while len(self._data) > self._maxsize:
				self._data.popitem(last=False)

	def clear(self) -> None:
		with self._lock:
			self._data.clear()


_EPOCH_LOCK = threading.RLock()
_SITE_EPOCHS: dict[str, tuple[float, dict[str, int]]] = {}
_SITE_GENS: dict[str, int] = {}
_SITE_DIAG_FLAGS: dict[str, tuple[bool, bool]] = {}

_SCHEMA_LRU = _BoundedLRU(maxsize=2048)
_SCHEMA_LOCK = threading.RLock()
_TRANSLATIONS_LRU = _BoundedLRU(maxsize=16)
_STATIC_UI_LRU = _BoundedLRU(maxsize=16)
_USER_PROFILE_LRU = _BoundedLRU(maxsize=2048)
_USER_BULK_EPOCH_LRU = _BoundedLRU(maxsize=8)
_USER_BULK_LOCK = threading.RLock()
_HIERARCHY_LRU = _BoundedLRU(maxsize=16)
_CAMPAIGN_LRU = _BoundedLRU(maxsize=32)
_POLICY_LRU = _BoundedLRU(maxsize=512)
_OUTCOME_LRU = _BoundedLRU(maxsize=32)
_CALENDAR_LRU = _BoundedLRU(maxsize=32)
_KPI_RUN_LRU = _BoundedLRU(maxsize=256)
_KPI_SNAP_LRU = _BoundedLRU(maxsize=512)
_DIRTY_CHECK_LRU = _BoundedLRU(maxsize=256)
_INVARIANT_LRU = _BoundedLRU(maxsize=256)
_DRILLDOWN_COUNT_LRU = _BoundedLRU(maxsize=1024)
_SSR_HTML_LRU = _BoundedLRU(maxsize=1024)

_INPROC_SF_LOCK = threading.Lock()
_INPROC_SF_EVENTS: dict[tuple, threading.Event] = {}

_COALESCE_STATS_LOCK = threading.Lock()
_COALESCE_METRIC_KEYS = (
	"l1_hits",
	"redis_hits",
	"leader_computes",
	"coalesced_follower_hits",
	"epoch_changed_mid_compute",
	"lock_expired_takeovers",
	"lock_release_skipped_token_mismatch",
	"wait_timeouts",
	"fallback_computes",
	"bypass_uncached_computes",
	"sql_count_executions",
	"sql_count_elapsed_us",
)
_COALESCE_STATS: dict[str, int] = {k: 0 for k in _COALESCE_METRIC_KEYS}

_ASSET_VERSION_CACHE: str | None = None
_MUTATION_HOOKS_INSTALLED: bool = False
_DB_INSTANCE_HOOKED_TYPES: set[type] = set()
_TRANSLATION_CACHE_INSTALLED: bool = False

_SET_VALUE_DOCTYPE_DOMAINS: dict[str, tuple[str, ...]] = {
	"CSKH Policy": (DOMAIN_POLICY,),
	"CSKH Outcome Code": (DOMAIN_OUTCOME,),
	"CSKH Working Calendar": (DOMAIN_CALENDAR,),
	"CRM Holiday List": (DOMAIN_CALENDAR,),
	"CSKH Campaign": (DOMAIN_CAMPAIGN, DOMAIN_POLICY),
	"User": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"Role": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"Has Role": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"CRM Sales Hierarchy": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"CSKH Team Membership History": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"CSKH Attempt Correction": (DOMAIN_CORRECTION,),
	"CSKH Assignment History": (DOMAIN_ASSIGNMENT_HISTORY,),
	"CSKH Contact Attempt": (DOMAIN_DIRTY_SCOPE,),
	"CSKH SLA Obligation": (DOMAIN_DIRTY_SCOPE,),
	"CSKH Dirty Scope": (DOMAIN_DIRTY_SCOPE,),
	"CSKH KPI Run": (DOMAIN_KPI_SNAPSHOT, DOMAIN_DIRTY_SCOPE),
	"CSKH KPI Snapshot": (DOMAIN_KPI_SNAPSHOT, DOMAIN_DIRTY_SCOPE),
	"Translation": (DOMAIN_TRANSLATIONS,),
	"FCRM Settings": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"System Settings": (DOMAIN_USER_HIERARCHY, DOMAIN_CALENDAR),
}

_DELETE_ONLY_DOCTYPE_DOMAINS: dict[str, tuple[str, ...]] = {
	"CSKH Work Item": (DOMAIN_WORK_ITEM,),
}

_SQL_DML_TABLE_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = tuple(
	(
		f"tab{dt}".lower(),
		_SET_VALUE_DOCTYPE_DOMAINS.get(dt, ()),
		tuple(
			dict.fromkeys(_SET_VALUE_DOCTYPE_DOMAINS.get(dt, ()) + _DELETE_ONLY_DOCTYPE_DOMAINS.get(dt, ()))
		),
	)
	for dt in sorted(set(_SET_VALUE_DOCTYPE_DOMAINS) | set(_DELETE_ONLY_DOCTYPE_DOMAINS))
)
_SQL_MUTATION_PREFIXES = ("insert", "update", "delete", "replac", "trunca", "alter ")


def _orm_hook_depth_delta(delta: int) -> int:
	local = getattr(frappe, "local", None)
	if local is None:
		return 0
	prev = int(getattr(local, "_cskh_orm_hook_depth", 0) or 0)
	local._cskh_orm_hook_depth = max(0, prev + delta)
	return prev


_SCHEMA_INVALIDATION_DOCTYPES = frozenset(("DocType", "Custom Field", "Property Setter", "DocField"))
_USER_PERMISSION_DOCTYPES = frozenset(
	("User", "Role", "Has Role", "User Permission", "CRM Sales Hierarchy", "CSKH Team Membership History")
)


def _resolve_redis_wrapper():
	"""Unwrap `frappe.cache` (`LocalProxy` or callable returning `RedisWrapper`) safely."""
	cache_attr = getattr(frappe, "cache", None)
	if cache_attr is None:
		return None
	try:
		if hasattr(cache_attr, "_get_current_object"):
			cache_obj = cache_attr._get_current_object()
		elif callable(cache_attr) and not hasattr(cache_attr, "make_key"):
			cache_obj = cache_attr()
		else:
			cache_obj = cache_attr
		return cache_obj if hasattr(cache_obj, "make_key") else None
	except Exception:
		return None


def _wrap_db_class_set_value(db_cls: type) -> None:
	if db_cls in _DB_INSTANCE_HOOKED_TYPES:
		return
	orig_set_value = db_cls.__dict__.get("set_value")
	if callable(orig_set_value) and not getattr(orig_set_value, "_cskh_cache_wrapped", False):

		def _wrapped_set_value(self, dt, dn, field, val=None, *args, **kwargs):
			_orm_hook_depth_delta(1)
			try:
				res = orig_set_value(self, dt, dn, field, val, *args, **kwargs)
			finally:
				_orm_hook_depth_delta(-1)
			doms = _SET_VALUE_DOCTYPE_DOMAINS.get(str(dt) if dt else "")
			if doms:
				bump_cache_domain(*doms)
			return res

		_wrapped_set_value._cskh_cache_wrapped = True  # type: ignore[attr-defined]
		db_cls.set_value = _wrapped_set_value  # type: ignore[attr-defined]

	orig_set_single = db_cls.__dict__.get("set_single_value")
	if callable(orig_set_single) and not getattr(orig_set_single, "_cskh_cache_wrapped", False):

		def _wrapped_set_single_value(self, doctype, fieldname, value=None, *args, **kwargs):
			_orm_hook_depth_delta(1)
			try:
				res = orig_set_single(self, doctype, fieldname, value, *args, **kwargs)
			finally:
				_orm_hook_depth_delta(-1)
			doms = _SET_VALUE_DOCTYPE_DOMAINS.get(str(doctype) if doctype else "")
			if doms:
				bump_cache_domain(*doms)
			return res

		_wrapped_set_single_value._cskh_cache_wrapped = True  # type: ignore[attr-defined]
		db_cls.set_single_value = _wrapped_set_single_value  # type: ignore[attr-defined]

	orig_db_delete = db_cls.__dict__.get("delete")
	if callable(orig_db_delete) and not getattr(orig_db_delete, "_cskh_cache_wrapped", False):

		def _wrapped_db_delete(self, doctype: str, *args, **kwargs):
			_orm_hook_depth_delta(1)
			try:
				res = orig_db_delete(self, doctype, *args, **kwargs)
			finally:
				_orm_hook_depth_delta(-1)
			dt_key = str(doctype) if doctype else ""
			doms = _SET_VALUE_DOCTYPE_DOMAINS.get(dt_key, ()) + _DELETE_ONLY_DOCTYPE_DOMAINS.get(dt_key, ())
			if doms:
				bump_cache_domain(*doms)
			return res

		_wrapped_db_delete._cskh_cache_wrapped = True  # type: ignore[attr-defined]
		db_cls.delete = _wrapped_db_delete  # type: ignore[attr-defined]

	orig_bulk_insert = db_cls.__dict__.get("bulk_insert")
	if callable(orig_bulk_insert) and not getattr(orig_bulk_insert, "_cskh_cache_wrapped", False):

		def _wrapped_bulk_insert(self, doctype: str, *args, **kwargs):
			_orm_hook_depth_delta(1)
			try:
				res = orig_bulk_insert(self, doctype, *args, **kwargs)
			finally:
				_orm_hook_depth_delta(-1)
			doms = _SET_VALUE_DOCTYPE_DOMAINS.get(str(doctype) if doctype else "")
			if doms:
				bump_cache_domain(*doms)
			return res

		_wrapped_bulk_insert._cskh_cache_wrapped = True  # type: ignore[attr-defined]
		db_cls.bulk_insert = _wrapped_bulk_insert  # type: ignore[attr-defined]

	orig_sql = db_cls.__dict__.get("sql")
	if callable(orig_sql) and not getattr(orig_sql, "_cskh_cache_wrapped", False):

		def _wrapped_sql(self, query, *args, **kwargs):
			if not isinstance(query, str):
				return orig_sql(self, query, *args, **kwargs)
			q_s = query.lstrip()
			kw = q_s[:7].lower()
			if not kw.startswith(_SQL_MUTATION_PREFIXES):
				return orig_sql(self, query, *args, **kwargs)
			prev_depth = _orm_hook_depth_delta(1)
			try:
				res = orig_sql(self, query, *args, **kwargs)
			finally:
				_orm_hook_depth_delta(-1)
			if kw.startswith("alter "):
				_SCHEMA_LRU.clear()
				return res
			if prev_depth == 0:
				is_del = kw.startswith(("delete", "trunca"))
				q_low = q_s.lower()
				b_doms: list[str] = []
				for tab_low, w_doms, d_doms in _SQL_DML_TABLE_RULES:
					if tab_low in q_low:
						doms = d_doms if is_del else w_doms
						if doms:
							b_doms.extend(doms)
				if b_doms:
					bump_cache_domain(*b_doms)
			return res

		_wrapped_sql._cskh_cache_wrapped = True  # type: ignore[attr-defined]
		db_cls.sql = _wrapped_sql  # type: ignore[attr-defined]

	orig_sql_ddl = db_cls.__dict__.get("sql_ddl")
	if callable(orig_sql_ddl) and not getattr(orig_sql_ddl, "_cskh_cache_wrapped", False):

		def _wrapped_sql_ddl(self, *args, **kwargs):
			res = orig_sql_ddl(self, *args, **kwargs)
			_SCHEMA_LRU.clear()
			return res

		_wrapped_sql_ddl._cskh_cache_wrapped = True  # type: ignore[attr-defined]
		db_cls.sql_ddl = _wrapped_sql_ddl  # type: ignore[attr-defined]

	_DB_INSTANCE_HOOKED_TYPES.add(db_cls)


def _invalidate_scoped_clear_cache(*args, **kwargs) -> None:
	"""Scope `frappe.clear_cache(user=..., doctype=...)` so per-user `ToDo` / callback writes
	do not wipe static schema/KPI/invariant caches or bump unrelated `DOMAIN_WORK_ITEM` epochs,
	while full `frappe.clear_cache()` (`FLUSHALL` / `bench clear-cache`) still purges everything."""
	user_arg = args[0] if len(args) >= 1 else kwargs.get("user")
	dt_arg = args[1] if len(args) >= 2 else kwargs.get("doctype")
	has_extra = len(args) > 2 or bool(set(kwargs) - {"user", "doctype"})
	if (not user_arg and not dt_arg) or has_extra:
		clear_all_local_caches()
		bump_cache_domain(*ALL_DOMAINS)
		return

	b_doms: list[str] = []
	if user_arg:
		_USER_PROFILE_LRU.clear()
		_USER_BULK_EPOCH_LRU.clear()
		_HIERARCHY_LRU.clear()
		_POLICY_LRU.clear()
		_SSR_HTML_LRU.clear()
		b_doms.extend((DOMAIN_USER_HIERARCHY, DOMAIN_POLICY))

	if dt_arg:
		dts = dt_arg if isinstance(dt_arg, list | tuple | set) else (dt_arg,)
		for dt in dts:
			dt_key = str(dt) if dt else ""
			if not dt_key:
				continue
			if dt_key in _SCHEMA_INVALIDATION_DOCTYPES:
				_SCHEMA_LRU.clear()
			if dt_key in _USER_PERMISSION_DOCTYPES:
				_USER_PROFILE_LRU.clear()
				_USER_BULK_EPOCH_LRU.clear()
				_HIERARCHY_LRU.clear()
				_POLICY_LRU.clear()
				_SSR_HTML_LRU.clear()
				b_doms.extend((DOMAIN_USER_HIERARCHY, DOMAIN_POLICY))
			found = _SET_VALUE_DOCTYPE_DOMAINS.get(dt_key, ()) + _DELETE_ONLY_DOCTYPE_DOMAINS.get(dt_key, ())
			if found:
				b_doms.extend(found)

	if b_doms:
		bump_cache_domain(*b_doms)


def _ensure_mutation_hooks_installed() -> None:
	"""Idempotently hook `BaseDocument.db_insert`/`db_update`, `Database.set_value`/`set_single_value`
	(plus active `type(frappe.db)`), `frappe.delete_doc`, and `frappe.clear_cache` in-process so all
	ORM / low-level writes in HTTP, RQ, scheduler, and standalone CLI processes invalidate `runtime_cache`
	domains immediately WITHOUT adding new module paths to `hooks.py` (preserving `G09` Tier 3 `N -> N-1`
	image rollback without flushing Redis)."""
	global _MUTATION_HOOKS_INSTALLED
	db_obj = getattr(frappe, "db", None)
	if db_obj is not None and type(db_obj) not in _DB_INSTANCE_HOOKED_TYPES:
		try:
			_wrap_db_class_set_value(type(db_obj))
		except Exception:
			pass
	if _MUTATION_HOOKS_INSTALLED:
		return
	try:
		from frappe.database.database import Database
		from frappe.model.base_document import BaseDocument

		_wrap_db_class_set_value(Database)

		orig_db_insert = getattr(BaseDocument, "db_insert", None)
		if callable(orig_db_insert) and not getattr(orig_db_insert, "_cskh_cache_wrapped", False):

			def _wrapped_db_insert(self, *args, **kwargs):
				cur_db = getattr(frappe, "db", None)
				if cur_db is not None and type(cur_db) not in _DB_INSTANCE_HOOKED_TYPES:
					_wrap_db_class_set_value(type(cur_db))
				_orm_hook_depth_delta(1)
				try:
					res = orig_db_insert(self, *args, **kwargs)
				finally:
					_orm_hook_depth_delta(-1)
				doms = _SET_VALUE_DOCTYPE_DOMAINS.get(str(getattr(self, "doctype", "") or ""))
				if doms:
					bump_cache_domain(*doms)
				return res

			_wrapped_db_insert._cskh_cache_wrapped = True  # type: ignore[attr-defined]
			BaseDocument.db_insert = _wrapped_db_insert  # type: ignore[method-assign]

		orig_db_update = getattr(BaseDocument, "db_update", None)
		if callable(orig_db_update) and not getattr(orig_db_update, "_cskh_cache_wrapped", False):

			def _wrapped_db_update(self, *args, **kwargs):
				cur_db = getattr(frappe, "db", None)
				if cur_db is not None and type(cur_db) not in _DB_INSTANCE_HOOKED_TYPES:
					_wrap_db_class_set_value(type(cur_db))
				_orm_hook_depth_delta(1)
				try:
					res = orig_db_update(self, *args, **kwargs)
				finally:
					_orm_hook_depth_delta(-1)
				doms = _SET_VALUE_DOCTYPE_DOMAINS.get(str(getattr(self, "doctype", "") or ""))
				if doms:
					bump_cache_domain(*doms)
				return res

			_wrapped_db_update._cskh_cache_wrapped = True  # type: ignore[attr-defined]
			BaseDocument.db_update = _wrapped_db_update  # type: ignore[method-assign]

		orig_delete_doc = getattr(frappe, "delete_doc", None)
		if callable(orig_delete_doc) and not getattr(orig_delete_doc, "_cskh_cache_wrapped", False):

			def _wrapped_delete_doc(doctype=None, *args, **kwargs):
				_orm_hook_depth_delta(1)
				try:
					res = orig_delete_doc(doctype, *args, **kwargs)
				finally:
					_orm_hook_depth_delta(-1)
				dts = doctype if isinstance(doctype, list | tuple | set) else (doctype,)
				b_doms: list[str] = []
				for dt in dts:
					dt_key = str(dt) if dt else ""
					found = _SET_VALUE_DOCTYPE_DOMAINS.get(dt_key, ()) + _DELETE_ONLY_DOCTYPE_DOMAINS.get(
						dt_key, ()
					)
					if found:
						b_doms.extend(found)
				if b_doms:
					bump_cache_domain(*b_doms)
				return res

			_wrapped_delete_doc._cskh_cache_wrapped = True  # type: ignore[attr-defined]
			frappe.delete_doc = _wrapped_delete_doc

		orig_clear_cache = getattr(frappe, "clear_cache", None)
		if callable(orig_clear_cache) and not getattr(orig_clear_cache, "_cskh_cache_wrapped", False):

			def _wrapped_clear_cache(*args, **kwargs):
				res = orig_clear_cache(*args, **kwargs)
				_invalidate_scoped_clear_cache(*args, **kwargs)
				return res

			_wrapped_clear_cache._cskh_cache_wrapped = True  # type: ignore[attr-defined]
			frappe.clear_cache = _wrapped_clear_cache

		_MUTATION_HOOKS_INSTALLED = True
	except Exception:
		pass


def _current_site() -> str:
	return str(getattr(frappe.local, "site", None) or "default")


def is_runtime_db_cache_active() -> bool:
	"""True in live/Staging/benchmark runtime, or inside dedicated cache unit tests."""
	flags = getattr(frappe, "flags", None)
	if flags is not None:
		if getattr(flags, "in_migrate", False) or getattr(flags, "in_install", False):
			return False
		if getattr(flags, "in_test", False) and not getattr(flags, "cskh_force_runtime_cache", False):
			return False
	_ensure_mutation_hooks_installed()
	return True


@contextmanager
def enable_runtime_cache_for_test():
	"""Context manager enabling `runtime_cache` inside `in_test=True` verification suites."""
	prev = getattr(frappe.flags, "cskh_force_runtime_cache", False)
	frappe.flags.cskh_force_runtime_cache = True
	clear_all_local_caches()
	try:
		yield
	finally:
		frappe.flags.cskh_force_runtime_cache = prev
		clear_all_local_caches()


def _clear_all_lru_stores() -> None:
	for lru in (
		_SCHEMA_LRU,
		_TRANSLATIONS_LRU,
		_STATIC_UI_LRU,
		_USER_PROFILE_LRU,
		_USER_BULK_EPOCH_LRU,
		_HIERARCHY_LRU,
		_CAMPAIGN_LRU,
		_POLICY_LRU,
		_OUTCOME_LRU,
		_CALENDAR_LRU,
		_KPI_RUN_LRU,
		_KPI_SNAP_LRU,
		_DIRTY_CHECK_LRU,
		_INVARIANT_LRU,
		_DRILLDOWN_COUNT_LRU,
		_SSR_HTML_LRU,
	):
		lru.clear()


def clear_all_local_caches() -> None:
	_clear_all_lru_stores()
	with _EPOCH_LOCK:
		_SITE_EPOCHS.clear()
		_SITE_GENS.clear()
		_SITE_DIAG_FLAGS.clear()
	local = getattr(frappe, "local", None)
	if local is not None:
		local._cskh_req_epochs = None


def _new_gen_nonce() -> int:
	return (int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)) or 1


def _ensure_redis_gen(cache_obj, rkey) -> int:
	"""Return the Redis epoch generation nonce, atomically initializing one after `FLUSHALL`."""
	g_raw = _redis_mod.Redis.hget(cache_obj, rkey, _EPOCH_GEN_FIELD)
	if g_raw is not None:
		return int(g_raw.decode("utf-8") if isinstance(g_raw, bytes) else g_raw)
	seed_str = str(_new_gen_nonce())
	_redis_mod.Redis.hsetnx(cache_obj, rkey, _EPOCH_GEN_FIELD, seed_str)
	g_raw2 = _redis_mod.Redis.hget(cache_obj, rkey, _EPOCH_GEN_FIELD)
	if g_raw2 is not None:
		return int(g_raw2.decode("utf-8") if isinstance(g_raw2, bytes) else g_raw2)
	return int(seed_str)


def _sync_site_epochs(site: str, *, force: bool = False) -> dict[str, int]:
	now_mono = time.monotonic()
	with _EPOCH_LOCK:
		cached = _SITE_EPOCHS.get(site)
		if not force and cached is not None and (now_mono - cached[0]) < _EPOCH_SYNC_TTL_SEC:
			return cached[1]
		if cached is not None and not force:
			# Stamp refresh-in-progress so concurrent Gunicorn threads don't stampede Redis
			_SITE_EPOCHS[site] = (now_mono, cached[1])

	redis_ok = False
	redis_gen = 0
	diag_probe = False
	diag_disable_coalesce = False
	redis_epochs: dict[str, int] = {}
	try:
		cache_obj = _resolve_redis_wrapper()
		if cache_obj is not None:
			rkey = cache_obj.make_key(_EPOCH_REDIS_HASH)
			if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
				raw = _redis_mod.Redis.hgetall(cache_obj, rkey) or {}
				for k, v in raw.items():
					k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
					v_int = int(v.decode("utf-8") if isinstance(v, bytes) else v)
					if k_str == _EPOCH_GEN_FIELD:
						redis_gen = v_int
					elif k_str == _DIAG_PROBE_FIELD:
						diag_probe = v_int != 0
					elif k_str == _DIAG_DISABLE_COALESCE_FIELD:
						diag_disable_coalesce = v_int != 0
					elif not k_str.startswith("__"):
						redis_epochs[k_str] = v_int
				if not redis_gen:
					redis_gen = _ensure_redis_gen(cache_obj, rkey)
				redis_ok = True
			else:
				raw = cache_obj.get_value(_EPOCH_REDIS_HASH) or {}
				for k, v in raw.items():
					k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
					v_int = int(v.decode("utf-8") if isinstance(v, bytes) else v)
					if k_str == _EPOCH_GEN_FIELD:
						redis_gen = v_int
					elif k_str == _DIAG_PROBE_FIELD:
						diag_probe = v_int != 0
					elif k_str == _DIAG_DISABLE_COALESCE_FIELD:
						diag_disable_coalesce = v_int != 0
					elif not k_str.startswith("__"):
						redis_epochs[k_str] = v_int
				if not redis_gen:
					redis_gen = _new_gen_nonce()
					raw_copy = dict(redis_epochs)
					raw_copy[_EPOCH_GEN_FIELD] = redis_gen
					cache_obj.set_value(_EPOCH_REDIS_HASH, raw_copy)
				redis_ok = True
	except Exception:
		redis_ok = False

	if redis_ok:
		gen_changed = False
		with _EPOCH_LOCK:
			_SITE_DIAG_FLAGS[site] = (diag_probe, diag_disable_coalesce)
			prev_gen = _SITE_GENS.get(site, 0)
			if redis_gen != prev_gen:
				# Redis `FLUSHALL` (`frappe.clear_cache()`) or first worker sync: adopt Redis epochs
				# authoritatively and purge any pre-flush in-process LRU entries!
				_SITE_GENS[site] = redis_gen
				epochs = dict(redis_epochs)
				_SITE_EPOCHS[site] = (now_mono, epochs)
				gen_changed = prev_gen != 0
			else:
				epochs = dict(redis_epochs)
				cur = _SITE_EPOCHS.get(site)
				if cur is not None:
					for k_str, v_int in cur[1].items():
						if v_int > epochs.get(k_str, 0):
							epochs[k_str] = v_int
				_SITE_EPOCHS[site] = (now_mono, epochs)
		if gen_changed:
			_clear_all_lru_stores()
		return epochs

	with _EPOCH_LOCK:
		_SITE_GENS.setdefault(site, 1)
		fallback_epochs = dict(cached[1]) if cached is not None else {}
		cur = _SITE_EPOCHS.get(site)
		if cur is not None:
			for k_str, v_int in cur[1].items():
				if v_int > fallback_epochs.get(k_str, 0):
					fallback_epochs[k_str] = v_int
		_SITE_EPOCHS[site] = (now_mono, fallback_epochs)
		return fallback_epochs


def _get_current_epochs(site: str) -> tuple[int, dict[str, int]]:
	"""Sync once per HTTP request (`0 ms` cross-worker staleness) and reuse across the request."""
	local = getattr(frappe, "local", None)
	if local is not None:
		req_ep = getattr(local, "_cskh_req_epochs", None)
		if isinstance(req_ep, tuple) and len(req_ep) == 3 and req_ep[0] == site:
			return req_ep[1], req_ep[2]
		epochs = _sync_site_epochs(site, force=True)
		with _EPOCH_LOCK:
			gen_val = _SITE_GENS.get(site, 1)
		local._cskh_req_epochs = (site, gen_val, epochs)
		return gen_val, epochs
	epochs = _sync_site_epochs(site, force=False)
	with _EPOCH_LOCK:
		gen_val = _SITE_GENS.get(site, 1)
	return gen_val, epochs


def _force_fresh_domains_epoch_tuple(site: str, domains: tuple[str, ...]) -> tuple[int, ...]:
	"""Force an immediate Redis epoch re-sync (used post-`COUNT` & during follower wait)."""
	epochs = _sync_site_epochs(site, force=True)
	with _EPOCH_LOCK:
		gen_val = _SITE_GENS.get(site, 1)
	local = getattr(frappe, "local", None)
	if local is not None:
		local._cskh_req_epochs = (site, gen_val, epochs)
	prefix = int(gen_val) << 32
	return tuple(prefix + int(epochs.get(d, 0)) for d in domains)


def get_domain_epoch(domain: str) -> int:
	site = _current_site()
	gen_val, epochs = _get_current_epochs(site)
	return (int(gen_val) << 32) + int(epochs.get(domain, 0))


def get_domains_epoch_tuple(*domains: str) -> tuple[int, ...]:
	site = _current_site()
	gen_val, epochs = _get_current_epochs(site)
	prefix = int(gen_val) << 32
	return tuple(prefix + int(epochs.get(d, 0)) for d in domains)


def _bump_domains_once(domains: tuple[str, ...]) -> None:
	if not domains:
		return
	site = _current_site()
	now_mono = time.monotonic()

	redis_bumped = False
	try:
		cache_obj = _resolve_redis_wrapper()
		if cache_obj is not None:
			rkey = cache_obj.make_key(_EPOCH_REDIS_HASH)
			if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
				redis_gen = _ensure_redis_gen(cache_obj, rkey)
				gen_changed = False
				with _EPOCH_LOCK:
					prev_gen = _SITE_GENS.get(site, 0)
					if redis_gen != prev_gen:
						_SITE_GENS[site] = redis_gen
						_SITE_EPOCHS[site] = (now_mono, {})
						gen_changed = prev_gen != 0
				if gen_changed:
					_clear_all_lru_stores()
				for dom in domains:
					new_val = int(_redis_mod.Redis.hincrby(cache_obj, rkey, dom, 1) or 0)
					with _EPOCH_LOCK:
						cur_s = _SITE_EPOCHS.get(site)
						local_map = dict(cur_s[1]) if cur_s is not None else {}
						if new_val > local_map.get(dom, 0):
							local_map[dom] = new_val
						else:
							local_map[dom] = local_map.get(dom, 0) + 1
						_SITE_EPOCHS[site] = (now_mono, local_map)
				redis_bumped = True
			else:
				cur_dict = dict(cache_obj.get_value(_EPOCH_REDIS_HASH) or {})
				redis_gen = int(cur_dict.get(_EPOCH_GEN_FIELD) or _new_gen_nonce())
				cur_dict[_EPOCH_GEN_FIELD] = redis_gen
				for dom in domains:
					cur_dict[dom] = int(cur_dict.get(dom, 0)) + 1
				cache_obj.set_value(_EPOCH_REDIS_HASH, cur_dict)
				with _EPOCH_LOCK:
					_SITE_GENS[site] = redis_gen
					_SITE_EPOCHS[site] = (
						now_mono,
						{k: int(v) for k, v in cur_dict.items() if k != _EPOCH_GEN_FIELD},
					)
				redis_bumped = True
	except Exception:
		redis_bumped = False

	if not redis_bumped:
		with _EPOCH_LOCK:
			_SITE_GENS.setdefault(site, 1)
			cur = _SITE_EPOCHS.get(site)
			local_map = dict(cur[1]) if cur is not None else {}
			for dom in domains:
				local_map[dom] = int(local_map.get(dom, 0)) + 1
			_SITE_EPOCHS[site] = (now_mono, local_map)

	local = getattr(frappe, "local", None)
	if local is not None:
		with _EPOCH_LOCK:
			cur_final = _SITE_EPOCHS.get(site)
			gen_final = _SITE_GENS.get(site, 1)
			local._cskh_req_epochs = (site, gen_final, dict(cur_final[1])) if cur_final is not None else None


def bump_cache_domain(*domains: str) -> None:
	"""Invalidate `domains` immediately in-process + Redis, and again on tx commit/rollback."""
	clean = tuple(dict.fromkeys(d for d in domains if d))
	if not clean:
		return
	_bump_domains_once(clean)

	db = getattr(frappe, "db", None)
	local = getattr(frappe, "local", None)
	if db is not None and local is not None:
		pending: set[str] | None = getattr(local, "_cskh_pending_epoch_bumps", None)
		if pending is None:
			pending = set()
			local._cskh_pending_epoch_bumps = pending

			def _flush_tx_bumps():
				items = tuple(sorted(getattr(local, "_cskh_pending_epoch_bumps", None) or ()))
				local._cskh_pending_epoch_bumps = None
				if items:
					_bump_domains_once(items)

			try:
				db.after_commit.add(_flush_tx_bumps)
				db.after_rollback.add(_flush_tx_bumps)
			except Exception:
				pass
		pending.update(clean)


# ---------------------------------------------------------------------------
# 1. In-process Frappe translation dictionary cache (eliminates 1.15 MB Redis
#    `merged_translations` TCP fetch + `pickle.loads()` per SSR/API request)
# ---------------------------------------------------------------------------


def install_inprocess_translation_cache() -> None:
	"""Idempotently wrap `frappe.translate.get_all_translations` with worker-RAM cache."""
	global _TRANSLATION_CACHE_INSTALLED
	if _TRANSLATION_CACHE_INSTALLED:
		return
	try:
		import frappe.translate as tr_mod
	except Exception:
		return

	orig = getattr(tr_mod, "get_all_translations", None)
	if orig is None:
		return
	if getattr(orig, "_cskh_inproc_wrapped", False):
		_TRANSLATION_CACHE_INSTALLED = True
		return

	def _cached_get_all_translations(lang: str) -> dict[str, str]:
		if not lang:
			return {}
		if not is_runtime_db_cache_active():
			return orig(lang)
		site = _current_site()
		ep = get_domain_epoch(DOMAIN_TRANSLATIONS)
		key = (site, str(lang), ep)
		hit = _TRANSLATIONS_LRU.get(key)
		if hit is not None:
			return hit
		res = orig(lang)
		if isinstance(res, dict) and res:
			_TRANSLATIONS_LRU.set(key, res)
		return res

	_cached_get_all_translations._cskh_inproc_wrapped = True  # type: ignore[attr-defined]
	_cached_get_all_translations._cskh_orig = orig  # type: ignore[attr-defined]
	tr_mod.get_all_translations = _cached_get_all_translations
	tr_mod.get_full_dict = _cached_get_all_translations
	_TRANSLATION_CACHE_INSTALLED = True


def on_before_request(*_args, **_kwargs) -> None:
	"""Invoked via `crm_cskh.i18n.set_guest_language` (`before_request`) so worker-memory
	translation cache, ORM invalidation hooks, and `/cskh/*` SSR fastpath are active,
	and per-request epoch cache is refreshed from Redis (`0 ms` cross-worker staleness)."""
	local = getattr(frappe, "local", None)
	if local is not None:
		local._cskh_req_epochs = None
	install_inprocess_translation_cache()
	_ensure_mutation_hooks_installed()
	try:
		from crm_cskh.www.cskh_renderer import install_template_page_fastpath

		install_template_page_fastpath()
	except Exception:
		pass


# ---------------------------------------------------------------------------
# 2. Schema introspection (`table_exists`, `has_column`)
# ---------------------------------------------------------------------------


def table_exists(doctype: str) -> bool:
	if not getattr(frappe, "db", None):
		return False
	if not is_runtime_db_cache_active():
		return bool(frappe.db.table_exists(doctype))
	key = (_current_site(), "tbl", doctype)
	hit = _SCHEMA_LRU.get(key)
	if hit is not None:
		return bool(hit)
	with _SCHEMA_LOCK:
		hit = _SCHEMA_LRU.get(key)
		if hit is not None:
			return bool(hit)
		val = bool(frappe.db.table_exists(doctype))
		_SCHEMA_LRU.set(key, val)
		return val


def has_column(doctype: str, column: str) -> bool:
	if not getattr(frappe, "db", None):
		return False
	if not is_runtime_db_cache_active():
		return bool(frappe.db.has_column(doctype, column))
	key = (_current_site(), "col", doctype, column)
	hit = _SCHEMA_LRU.get(key)
	if hit is not None:
		return bool(hit)
	with _SCHEMA_LOCK:
		hit = _SCHEMA_LRU.get(key)
		if hit is not None:
			return bool(hit)
		val = bool(frappe.db.has_column(doctype, column))
		_SCHEMA_LRU.set(key, val)
		return val


def has_index(doctype: str, index_name: str) -> bool:
	if not getattr(frappe, "db", None):
		return False
	_ensure_mutation_hooks_installed()
	key = (_current_site(), "idx", doctype, index_name)
	hit = _SCHEMA_LRU.get(key)
	if hit is not None:
		return bool(hit)
	with _SCHEMA_LOCK:
		hit = _SCHEMA_LRU.get(key)
		if hit is not None:
			return bool(hit)
		clean_tbl = f"tab{doctype}".replace("`", "")
		val = bool(frappe.db.sql(f"show index from `{clean_tbl}` where Key_name = %s", (index_name,)))
		_SCHEMA_LRU.set(key, val)
		return val


def clear_schema_cache() -> None:
	_SCHEMA_LRU.clear()


# ---------------------------------------------------------------------------
# 3. User auth profile & `CRM Sales Hierarchy` nested-set cache
# ---------------------------------------------------------------------------


def get_user_auth_profile(user: str | None = None) -> dict:
	"""Return `{"enabled", "roles", "time_zone", "full_name", "user_image"}` scoped to `user`."""
	u = str(user or getattr(getattr(frappe, "session", None), "user", None) or "Guest")
	if u == "Guest":
		return {
			"user": "Guest",
			"enabled": 0,
			"roles": frozenset({"Guest"}),
			"time_zone": "",
			"full_name": "Guest",
			"user_image": "",
		}
	if not is_runtime_db_cache_active():
		roles = frozenset(frappe.get_roles(u))
		if u == "Administrator":
			return {
				"user": "Administrator",
				"enabled": 1,
				"roles": roles,
				"time_zone": str(frappe.db.get_value("User", "Administrator", "time_zone") or ""),
				"full_name": "Administrator",
				"user_image": "",
			}
		row = (
			frappe.db.get_value("User", u, ["enabled", "time_zone", "full_name", "user_image"], as_dict=True)
			or {}
		)
		return {
			"user": u,
			"enabled": 1 if row.get("enabled") is None else cint(row.get("enabled")),
			"roles": roles,
			"time_zone": str(row.get("time_zone") or ""),
			"full_name": str(row.get("full_name") or u),
			"user_image": str(row.get("user_image") or ""),
		}

	site = _current_site()
	ep = get_domain_epoch(DOMAIN_USER_HIERARCHY)
	key = (site, u, ep)
	hit = _USER_PROFILE_LRU.get(key)
	if hit is not None:
		return hit

	if u != "Administrator":
		_preload_enabled_users_into_lru(site, ep)
		hit = _USER_PROFILE_LRU.get(key)
		if hit is not None:
			return hit

	roles = frozenset(frappe.get_roles(u))
	row = (
		frappe.db.get_value("User", u, ["enabled", "time_zone", "full_name", "user_image"], as_dict=True)
		or {}
	)
	profile = {
		"user": u,
		"enabled": 1 if (u == "Administrator" or row.get("enabled") is None) else cint(row.get("enabled")),
		"roles": roles,
		"time_zone": str(row.get("time_zone") or ""),
		"full_name": str(row.get("full_name") or u),
		"user_image": str(row.get("user_image") or ""),
	}
	_USER_PROFILE_LRU.set(key, profile)
	return profile


def _preload_enabled_users_into_lru(site: str, ep: int) -> None:
	"""Bulk-load enabled non-Administrator `User` profiles + active `Has Role` rows once per epoch."""
	bulk_key = (site, ep)
	if _USER_BULK_EPOCH_LRU.get(bulk_key):
		return
	with _USER_BULK_LOCK:
		if _USER_BULK_EPOCH_LRU.get(bulk_key):
			return
		try:
			cnt_rows = frappe.db.sql("select count(*) from `tabUser` where enabled = 1")
			enabled_cnt = int((cnt_rows[0][0] if cnt_rows and cnt_rows[0] else 0) or 0)
			if 0 < enabled_cnt <= 2000:
				u_rows = frappe.db.sql(
					"""
					select name, time_zone, full_name, user_image
					from `tabUser`
					where enabled = 1 and name not in ('Administrator', 'Guest')
					""",
					as_dict=False,
				)
				r_rows = frappe.db.sql(
					"""
					select hr.parent, hr.role
					from `tabHas Role` hr
					inner join `tabRole` r on r.name = hr.role
					where hr.parenttype = 'User' and coalesce(r.disabled, 0) = 0
					""",
					as_dict=False,
				)
				roles_by_user: dict[str, set[str]] = {}
				for parent_u, role_name in r_rows or ():
					if parent_u and role_name:
						roles_by_user.setdefault(str(parent_u), set()).add(str(role_name))
				for u_name, u_tz, u_fn, u_img in u_rows or ():
					if not u_name:
						continue
					u_str = str(u_name)
					r_set = frozenset((roles_by_user.get(u_str) or set()) | {"All", "Guest"})
					_USER_PROFILE_LRU.set(
						(site, u_str, ep),
						{
							"user": u_str,
							"enabled": 1,
							"roles": r_set,
							"time_zone": str(u_tz or ""),
							"full_name": str(u_fn or u_str),
							"user_image": str(u_img or ""),
						},
					)
		except Exception:
			return
		_USER_BULK_EPOCH_LRU.set(bulk_key, True)


def get_cached_system_timezone() -> str:
	"""Return cached `System Settings.time_zone` without per-request Redis `get_value` round-trips."""
	if not is_runtime_db_cache_active():
		return str(frappe.utils.get_system_timezone() or "Asia/Ho_Chi_Minh")
	key = (_current_site(), "__system_tz__", get_domain_epoch(DOMAIN_USER_HIERARCHY))
	hit = _STATIC_UI_LRU.get(key)
	if isinstance(hit, str) and hit:
		return hit
	tz_val = str(frappe.utils.get_system_timezone() or "Asia/Ho_Chi_Minh")
	_STATIC_UI_LRU.set(key, tz_val)
	return tz_val


def get_roles_set(user: str | None = None) -> frozenset[str]:
	return get_user_auth_profile(user)["roles"]


def get_hierarchy_snapshot() -> dict | None:
	"""Return pre-indexed `CRM Sales Hierarchy` nested-set snapshot (`~50` nodes)."""
	if not table_exists("CRM Sales Hierarchy"):
		return None
	use_cache = is_runtime_db_cache_active()
	key = (_current_site(), get_domain_epoch(DOMAIN_USER_HIERARCHY))
	if use_cache:
		hit = _HIERARCHY_LRU.get(key)
		if hit is not None:
			return hit

	cols = ["h.`name`", "h.`user`", "h.`lft`", "h.`rgt`", "h.`reports_to`", "h.`is_group`"]
	for opt_col in ("full_name", "title", "node_name", "hierarchy_name", "team_name"):
		if has_column("CRM Sales Hierarchy", opt_col):
			cols.append(f"h.`{opt_col}`")
	col_sql = ", ".join(cols)
	rows = frappe.db.sql(
		f"""
		select {col_sql}, u.full_name as user_full_name
		from `tabCRM Sales Hierarchy` h
		left join `tabUser` u on u.name = h.user
		order by h.lft asc, h.name asc
		""",
		as_dict=True,
	)
	hist_labels: dict[str, str] = {}
	if table_exists("CSKH Team Membership History"):
		for mr in frappe.db.sql(
			"""
			select team_node, max(team_name_snapshot) as team_name_snapshot
			from `tabCSKH Team Membership History`
			where team_node is not null and team_node != ''
			group by team_node
			""",
			as_dict=True,
		):
			if mr.get("team_name_snapshot"):
				hist_labels[str(mr.team_node)] = str(mr.team_name_snapshot)

	by_user: dict[str, dict] = {}
	by_name: dict[str, dict] = {}
	ordered: list[dict] = []
	team_labels: dict[str, str] = {}
	for r in rows or ():
		n_id = str(r.name)
		raw_lbl = str(r.get("full_name") or r.get("user_full_name") or r.get("user") or r.name)
		if (not raw_lbl or raw_lbl == n_id or "@" in raw_lbl) and hist_labels.get(n_id):
			raw_lbl = hist_labels[n_id]
		team_labels[n_id] = raw_lbl
		label = (
			r.get("title") or r.get("node_name") or r.get("hierarchy_name") or r.get("team_name") or raw_lbl
		)
		item = {
			"name": n_id,
			"user": str(r.user) if r.user else None,
			"lft": cint(r.lft),
			"rgt": cint(r.rgt),
			"reports_to": str(r.reports_to) if r.reports_to else None,
			"is_group": cint(r.is_group),
			"label": str(label or n_id),
			"team_label": raw_lbl,
		}
		ordered.append(item)
		by_name[item["name"]] = item
		if item["user"] and item["user"] not in by_user:
			by_user[item["user"]] = item

	for h_id, h_lbl in hist_labels.items():
		if h_id not in team_labels:
			team_labels[h_id] = h_lbl

	snap = {
		"ordered": tuple(ordered),
		"by_user": by_user,
		"by_name": by_name,
		"team_labels": team_labels,
	}
	if use_cache:
		_HIERARCHY_LRU.set(key, snap)
	return snap


def get_user_hierarchy_node(user: str | None) -> dict | None:
	if not user:
		return None
	snap = get_hierarchy_snapshot()
	if not snap:
		return None
	return snap["by_user"].get(str(user))


def get_subtree_nodes_from_cache(node_name: str | None) -> tuple[str, ...]:
	if not node_name:
		return ()
	snap = get_hierarchy_snapshot()
	if not snap:
		return ()
	root = snap["by_name"].get(str(node_name))
	if not root:
		return ()
	lft, rgt = root["lft"], root["rgt"]
	return tuple(n["name"] for n in snap["ordered"] if lft <= n["lft"] and n["rgt"] <= rgt)


def get_subtree_users_from_cache(user: str | None) -> tuple[str, ...]:
	if not user:
		return ()
	snap = get_hierarchy_snapshot()
	if not snap:
		return (str(user),)
	root = snap["by_user"].get(str(user))
	if not root:
		return (str(user),)
	lft, rgt = root["lft"], root["rgt"]
	users = [n["user"] for n in snap["ordered"] if lft <= n["lft"] and n["rgt"] <= rgt and n["user"]]
	return tuple(users) if users else (str(user),)


def is_user_in_leader_subtree(leader_user: str, target_user: str) -> bool:
	if not leader_user or not target_user:
		return False
	snap = get_hierarchy_snapshot()
	if not snap:
		return False
	mgr = snap["by_user"].get(str(leader_user))
	mem = snap["by_user"].get(str(target_user))
	if not mgr or not mem:
		return False
	return bool(mgr["lft"] <= mem["lft"] and mem["rgt"] <= mgr["rgt"])


# ---------------------------------------------------------------------------
# 4. Campaign, Policy, Outcome, Calendar & Static UI i18n bundles
# ---------------------------------------------------------------------------


def get_campaign_map() -> dict[str, dict]:
	if not table_exists("CSKH Campaign"):
		return {}
	use_cache = is_runtime_db_cache_active()
	key = (_current_site(), get_domain_epoch(DOMAIN_CAMPAIGN))
	if use_cache:
		hit = _CAMPAIGN_LRU.get(key)
		if hit is not None:
			return hit

	cols = ["name", "code", "title", "status", "team_scope"]
	if has_column("CSKH Campaign", "conversion_outcome_codes"):
		cols.append("conversion_outcome_codes")
	col_sql = ", ".join(f"`{c}`" for c in cols)
	rows = frappe.db.sql(
		f"select {col_sql} from `tabCSKH Campaign` order by creation desc",
		as_dict=True,
	)
	cmap: dict[str, dict] = {}
	for r in rows or ():
		conv_raw = str(r.get("conversion_outcome_codes") or "")
		conv_set = frozenset(c.strip().lower() for c in conv_raw.split(",") if c.strip())
		cmap[str(r.name)] = {
			"name": str(r.name),
			"code": r.get("code"),
			"title": r.get("title"),
			"status": r.get("status"),
			"team_scope": r.get("team_scope"),
			"conversion_outcome_codes": conv_raw,
			"conversion_outcome_set": conv_set,
		}
	if use_cache:
		_CAMPAIGN_LRU.set(key, cmap)
	return cmap


def get_campaign_row(campaign: str | None) -> dict | None:
	if not campaign:
		return None
	return get_campaign_map().get(str(campaign))


def _clone_policy_dict(pol: dict) -> dict:
	out = dict(pol)
	ch = out.get("enabled_channels")
	if isinstance(ch, list):
		out["enabled_channels"] = list(ch)
	return out


def get_cached_effective_policy(
	*,
	campaign: str | None,
	owner_user: str | None,
	compute_fn,
) -> dict:
	if not is_runtime_db_cache_active():
		return compute_fn()
	key = (
		_current_site(),
		str(campaign or ""),
		str(owner_user or ""),
		get_domains_epoch_tuple(DOMAIN_POLICY, DOMAIN_CAMPAIGN, DOMAIN_USER_HIERARCHY),
	)
	hit = _POLICY_LRU.get(key)
	if hit is not None:
		return _clone_policy_dict(hit)
	res = compute_fn()
	_POLICY_LRU.set(key, _clone_policy_dict(res))
	return res


def get_cached_global_policy_flag(field: str, default: bool, compute_fn) -> bool:
	if not is_runtime_db_cache_active():
		return bool(compute_fn())
	key = (_current_site(), "flag", field, bool(default), get_domain_epoch(DOMAIN_POLICY))
	hit = _POLICY_LRU.get(key)
	if hit is not None:
		return bool(hit)
	val = bool(compute_fn())
	_POLICY_LRU.set(key, val)
	return val


def get_outcome_catalog(serialize_fn) -> dict[str, dict]:
	"""Return `{code_lower: serialized_outcome_dict}` for all `CSKH Outcome Code` rows."""
	if not table_exists("CSKH Outcome Code"):
		return {}
	use_cache = is_runtime_db_cache_active()
	key = (_current_site(), get_domain_epoch(DOMAIN_OUTCOME))
	if use_cache:
		hit = _OUTCOME_LRU.get(key)
		if hit is not None:
			return hit

	cols = [
		"name",
		"code",
		"label_vi",
		"description",
		"result_group",
		"is_contact_attempt",
		"is_connected",
		"is_interested",
		"is_terminal",
		"is_do_not_contact",
		"requires_note",
		"requires_followup",
		"allows_followup",
		"requires_completion_reason",
		"allowed_channels",
		"suggested_next_status",
		"version",
		"enabled",
		"sort_order",
	]
	for opt_col in ("is_conversion", "is_invalid_phone"):
		if has_column("CSKH Outcome Code", opt_col):
			cols.append(opt_col)
	col_sql = ", ".join(f"`{c}`" for c in cols)
	rows = frappe.db.sql(
		f"select {col_sql} from `tabCSKH Outcome Code` order by sort_order asc, code asc",
		as_dict=True,
	)
	out_map: dict[str, dict] = {}
	for r in rows or ():
		ser = serialize_fn(r)
		out_map[str(ser["code"]).lower()] = ser
	if use_cache:
		_OUTCOME_LRU.set(key, out_map)
	return out_map


def get_cached_working_calendar(calendar_code: str | None, compute_fn):
	if not is_runtime_db_cache_active():
		return compute_fn(calendar_code)
	key = (_current_site(), str(calendar_code or "__DEFAULT__"), get_domain_epoch(DOMAIN_CALENDAR))
	hit = _CALENDAR_LRU.get(key)
	if hit is not None:
		return hit
	cal = compute_fn(calendar_code)
	_CALENDAR_LRU.set(key, cal)
	return cal


def html_safe_json_dumps(obj) -> str:
	"""Serialize `obj` to JSON safe for inline `<script>` embedding (matches Jinja `| tojson`)."""
	raw = json.dumps(obj, ensure_ascii=False)
	return (
		raw.replace("<", "\\u003c")
		.replace(">", "\\u003e")
		.replace("&", "\\u0026")
		.replace("'", "\\u0027")
		.replace("\u2028", "\\u2028")
		.replace("\u2029", "\\u2029")
	)


def get_asset_version() -> str:
	global _ASSET_VERSION_CACHE
	if _ASSET_VERSION_CACHE is not None and is_runtime_db_cache_active():
		return _ASSET_VERSION_CACHE
	folder = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "public", "cskh"))
	newest = 0
	if os.path.isdir(folder):
		for name in os.listdir(folder):
			path = os.path.join(folder, name)
			if os.path.isfile(path):
				newest = max(newest, int(os.path.getmtime(path)))
	val = str(newest)
	_ASSET_VERSION_CACHE = val
	return val


def get_static_cskh_ui_bundle(lang: str = "vi") -> dict:
	"""Return cached `(i18n, error_codes, error_texts, *_json, asset_version)` for `/cskh/*`."""
	install_inprocess_translation_cache()
	use_cache = is_runtime_db_cache_active()
	key = (_current_site(), lang, get_domain_epoch(DOMAIN_TRANSLATIONS))
	if use_cache:
		hit = _STATIC_UI_LRU.get(key)
		if hit is not None:
			return hit

	from crm_cskh import p1_strings, p2_strings, p3_strings
	from crm_cskh.services import errors as p1_errors

	error_codes = (
		list(p1_errors.ALL_CODES) + list(p2_strings.P2_ERROR_CODES) + list(p3_strings.P3_ERROR_CODES)
	)
	i18n_map = p1_strings.translated_map()
	error_texts = p1_strings.translated_error_texts()
	bundle = {
		"error_codes": error_codes,
		"i18n": i18n_map,
		"error_texts": error_texts,
		"error_codes_json": html_safe_json_dumps(error_codes),
		"i18n_json": html_safe_json_dumps(i18n_map),
		"error_texts_json": html_safe_json_dumps(error_texts),
		"asset_version": get_asset_version(),
	}
	if use_cache:
		_STATIC_UI_LRU.set(key, bundle)
	return bundle


# ---------------------------------------------------------------------------
# 5. Cross-Process Singleflight Coalescing, KPI Snapshots & Drill-down Guards
# ---------------------------------------------------------------------------

DRILLDOWN_COUNT_DOMAINS = (
	DOMAIN_DIRTY_SCOPE,
	DOMAIN_KPI_SNAPSHOT,
	DOMAIN_ASSIGNMENT_HISTORY,
	DOMAIN_CORRECTION,
	DOMAIN_OUTCOME,
	DOMAIN_CAMPAIGN,
	DOMAIN_POLICY,
	DOMAIN_WORK_ITEM,
	DOMAIN_CALENDAR,
)


def _is_diag_probe_enabled(site: str | None = None) -> bool:
	flags = getattr(frappe, "flags", None)
	if flags is not None and getattr(flags, "cskh_diag_probe", False):
		return True
	s = site or _current_site()
	with _EPOCH_LOCK:
		pair = _SITE_DIAG_FLAGS.get(s)
	return bool(pair and pair[0])


def is_diag_coalesce_disabled(site: str | None = None) -> bool:
	flags = getattr(frappe, "flags", None)
	if flags is not None and getattr(flags, "cskh_disable_coalesce", False):
		return True
	s = site or _current_site()
	with _EPOCH_LOCK:
		pair = _SITE_DIAG_FLAGS.get(s)
	return bool(pair and pair[1])


def _record_coalesce_stat(metric: str, delta: int = 1, *, site: str | None = None) -> None:
	if delta == 0:
		return
	with _COALESCE_STATS_LOCK:
		_COALESCE_STATS[metric] = int(_COALESCE_STATS.get(metric, 0)) + int(delta)
	if _is_diag_probe_enabled(site):
		try:
			cache_obj = _resolve_redis_wrapper()
			if cache_obj is not None and _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
				skey = cache_obj.make_key(_DIAG_REDIS_STATS_HASH)
				_redis_mod.Redis.hincrby(cache_obj, skey, metric, int(delta))
				_redis_mod.Redis.hincrby(cache_obj, skey, f"pid:{os.getpid()}:{metric}", int(delta))
		except Exception:
			pass


def get_coalesce_telemetry() -> dict[str, int]:
	with _COALESCE_STATS_LOCK:
		return dict(_COALESCE_STATS)


def reset_coalesce_telemetry(*, clear_redis_diag: bool = False) -> None:
	with _COALESCE_STATS_LOCK:
		for k in _COALESCE_METRIC_KEYS:
			_COALESCE_STATS[k] = 0
	if clear_redis_diag:
		try:
			cache_obj = _resolve_redis_wrapper()
			if cache_obj is not None and _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
				_redis_mod.Redis.delete(cache_obj, cache_obj.make_key(_DIAG_REDIS_STATS_HASH))
		except Exception:
			pass


def set_cross_process_diag_mode(*, probe_enabled: bool, disable_coalesce: bool = False) -> None:
	"""Toggle zero-production-cost Redis diagnostic probe across all Gunicorn worker processes."""
	site = _current_site()
	with _EPOCH_LOCK:
		_SITE_DIAG_FLAGS[site] = (bool(probe_enabled), bool(disable_coalesce))
	try:
		cache_obj = _resolve_redis_wrapper()
		if cache_obj is not None and _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			rkey = cache_obj.make_key(_EPOCH_REDIS_HASH)
			_ensure_redis_gen(cache_obj, rkey)
			if probe_enabled:
				_redis_mod.Redis.hset(cache_obj, rkey, _DIAG_PROBE_FIELD, "1")
			else:
				_redis_mod.Redis.hdel(cache_obj, rkey, _DIAG_PROBE_FIELD)
			if disable_coalesce:
				_redis_mod.Redis.hset(cache_obj, rkey, _DIAG_DISABLE_COALESCE_FIELD, "1")
			else:
				_redis_mod.Redis.hdel(cache_obj, rkey, _DIAG_DISABLE_COALESCE_FIELD)
	except Exception:
		pass
	_sync_site_epochs(site, force=True)


def read_cross_process_diag_telemetry() -> dict[str, object]:
	"""Read aggregated cross-Gunicorn-process singleflight & SQL COUNT telemetry from Redis."""
	totals: dict[str, int] = {k: 0 for k in _COALESCE_METRIC_KEYS}
	by_pid: dict[str, dict[str, int]] = {}
	try:
		cache_obj = _resolve_redis_wrapper()
		if cache_obj is not None and _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			raw = _redis_mod.Redis.hgetall(cache_obj, cache_obj.make_key(_DIAG_REDIS_STATS_HASH)) or {}
			for k, v in raw.items():
				k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
				v_int = int(v.decode("utf-8") if isinstance(v, bytes) else v)
				if k_str.startswith("pid:"):
					parts = k_str.split(":", 2)
					if len(parts) == 3:
						by_pid.setdefault(parts[1], {})[parts[2]] = v_int
				else:
					totals[k_str] = v_int
	except Exception:
		pass
	return {
		"totals": totals,
		"sql_count_elapsed_ms": round(totals.get("sql_count_elapsed_us", 0) / 1000.0, 3),
		"distinct_worker_pids_observed": sorted(by_pid.keys()),
		"by_pid": by_pid,
	}


def _sf_json_default(obj):
	if isinstance(obj, datetime):
		return {"__cskh_dt__": obj.isoformat()}
	if isinstance(obj, date):
		return {"__cskh_d__": obj.isoformat()}
	if isinstance(obj, tuple | set | frozenset):
		return list(obj)
	return str(obj)


def _sf_json_object_hook(d: dict):
	if len(d) == 1:
		if "__cskh_dt__" in d:
			return datetime.fromisoformat(d["__cskh_dt__"])
		if "__cskh_d__" in d:
			return date.fromisoformat(d["__cskh_d__"])
	return d


def _sf_json_dumps(payload) -> str:
	return json.dumps(payload, default=_sf_json_default, separators=(",", ":"), ensure_ascii=False)


def _sf_json_loads(raw: bytes | str):
	txt = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
	return json.loads(txt, object_hook=_sf_json_object_hook)


def _build_sf_digest(site: str, namespace: str, scope_key: tuple, epochs: tuple[int, ...]) -> str:
	raw = json.dumps(
		[site, namespace, scope_key, list(epochs)],
		default=_sf_json_default,
		separators=(",", ":"),
		ensure_ascii=True,
	).encode("utf-8")
	return hashlib.sha256(raw).hexdigest()[:40]


def _sf_redis_get_json(cache_obj, val_rkey: str, expected_epochs: tuple[int, ...]) -> tuple[object, bool]:
	if cache_obj is None:
		return None, False
	try:
		if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			raw = _redis_mod.Redis.get(cache_obj, val_rkey)
		elif hasattr(cache_obj, "get"):
			raw = cache_obj.get(val_rkey)
		else:
			return None, False
		if raw is None:
			return None, False
		parsed = _sf_json_loads(raw)
		if isinstance(parsed, dict) and parsed.get("ep") == list(expected_epochs):
			return parsed.get("v"), True
	except Exception:
		return None, False
	return None, False


def _sf_redis_set_json(
	cache_obj,
	val_rkey: str,
	payload: dict,
	val_ttl_ms: int,
	*,
	nx: bool = False,
) -> bool:
	if cache_obj is None:
		return False
	ttl = max(1, int(val_ttl_ms))
	try:
		encoded = _sf_json_dumps(payload)
		if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			return bool(_redis_mod.Redis.set(cache_obj, val_rkey, encoded, px=ttl, nx=nx))
		if hasattr(cache_obj, "set"):
			return bool(cache_obj.set(val_rkey, encoded, px=ttl, nx=nx))
	except Exception:
		return False
	return False


def _sf_redis_acquire_lock(cache_obj, lock_rkey: str, owner_token: str, lock_ttl_ms: int) -> bool:
	if cache_obj is None:
		return False
	ttl = max(1, int(lock_ttl_ms))
	try:
		if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			return bool(_redis_mod.Redis.set(cache_obj, lock_rkey, owner_token, nx=True, px=ttl))
		if hasattr(cache_obj, "set"):
			return bool(cache_obj.set(lock_rkey, owner_token, nx=True, px=ttl))
	except Exception:
		return False
	return False


def _sf_redis_lock_exists(cache_obj, lock_rkey: str) -> bool:
	if cache_obj is None:
		return False
	try:
		if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			return bool(_redis_mod.Redis.exists(cache_obj, lock_rkey))
		if hasattr(cache_obj, "get"):
			return cache_obj.get(lock_rkey) is not None
	except Exception:
		return False
	return False


def _sf_release_lock_if_owner(
	cache_obj, lock_rkey: str, owner_token: str, *, site: str | None = None
) -> bool:
	"""Atomically release `lock_rkey` ONLY if it is still owned by `owner_token`."""
	if cache_obj is None:
		return False
	try:
		if _redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis):
			deleted = int(
				_redis_mod.Redis.eval(cache_obj, _SF_RELEASE_LOCK_LUA, 1, lock_rkey, owner_token) or 0
			)
			if deleted == 0:
				_record_coalesce_stat("lock_release_skipped_token_mismatch", 1, site=site)
				return False
			return True
		if hasattr(cache_obj, "get") and hasattr(cache_obj, "delete"):
			cur = cache_obj.get(lock_rkey)
			cur_str = cur.decode("utf-8") if isinstance(cur, bytes) else (str(cur) if cur is not None else "")
			if cur_str == owner_token:
				cache_obj.delete(lock_rkey)
				return True
			_record_coalesce_stat("lock_release_skipped_token_mismatch", 1, site=site)
			return False
	except Exception:
		return False
	return False


def _acquire_inproc_sf_slot(inproc_key: tuple) -> tuple[bool, threading.Event]:
	with _INPROC_SF_LOCK:
		existing = _INPROC_SF_EVENTS.get(inproc_key)
		if existing is not None:
			return False, existing
		ev = threading.Event()
		_INPROC_SF_EVENTS[inproc_key] = ev
		return True, ev


def _register_inproc_sf_event(inproc_key: tuple) -> threading.Event:
	with _INPROC_SF_LOCK:
		ev = _INPROC_SF_EVENTS.get(inproc_key)
		if ev is None:
			ev = threading.Event()
			_INPROC_SF_EVENTS[inproc_key] = ev
		return ev


def _notify_inproc_sf_waiters(inproc_key: tuple) -> None:
	with _INPROC_SF_LOCK:
		ev = _INPROC_SF_EVENTS.pop(inproc_key, None)
	if ev is not None:
		ev.set()


def _wait_inproc_or_sleep(inproc_key: tuple, timeout_sec: float) -> None:
	if timeout_sec <= 0:
		return
	with _INPROC_SF_LOCK:
		ev = _INPROC_SF_EVENTS.get(inproc_key)
	if ev is not None:
		ev.wait(timeout=timeout_sec)
	else:
		time.sleep(timeout_sec)


def coalesced_epoch_compute(
	namespace: str,
	scope_key: tuple,
	domains: tuple[str, ...],
	compute_fn,
	*,
	l1_lru: _BoundedLRU | None = None,
	val_ttl_ms: int = 300_000,
	lock_ttl_ms: int = 5_000,
	wait_timeout_ms: int = 2_500,
	poll_interval_ms: int = 12,
	is_sql_count: bool = False,
):
	"""Cross-Gunicorn-process + intra-process singleflight coalescing bound to `domains` epochs.

	Guarantees:
	  1. Concurrent requests across separate Gunicorn worker processes sharing `(site, namespace,
	     scope_key, pre_epochs)` elect 1 Redis lock leader (`SET NX PX`); followers wait up to
	     `wait_timeout_ms` and reuse the leader's result (`0` duplicate SQL `COUNT(*)` executions).
	  2. **Mid-compute epoch change guard:** After `compute_fn()` finishes, epochs are force-resynced
	     from Redis (`_force_fresh_domains_epoch_tuple`). If any governing domain epoch bumped while
	     computing, the pre-bump result is NEVER written to Redis or L1.
	  3. **Safe lock expiration & dead-holder recovery:** Locks always carry mandatory `PX lock_ttl_ms`
	     (auto-expiring on `SIGKILL` / worker crash) and release atomically via token-checked Lua
	     `EVAL` so an expired leader never deletes a newer leader's lock.
	  4. **Bounded follower wait:** Followers never block beyond `wait_timeout_ms`, cleanly falling
	     back to direct computation if the leader is still busy.
	"""
	site = _current_site()

	def _timed_compute():
		if not is_sql_count:
			return compute_fn()
		t_c0 = time.perf_counter()
		try:
			return compute_fn()
		finally:
			el_us = max(0, round((time.perf_counter() - t_c0) * 1_000_000))
			_record_coalesce_stat("sql_count_executions", 1, site=site)
			_record_coalesce_stat("sql_count_elapsed_us", el_us, site=site)

	if not is_runtime_db_cache_active():
		return _timed_compute()

	if is_diag_coalesce_disabled(site):
		_record_coalesce_stat("bypass_uncached_computes", 1, site=site)
		return _timed_compute()

	flags = getattr(frappe, "flags", None)
	if flags is not None:
		if getattr(flags, "cskh_sf_lock_ttl_ms", None) is not None:
			lock_ttl_ms = int(flags.cskh_sf_lock_ttl_ms)
		if getattr(flags, "cskh_sf_wait_timeout_ms", None) is not None:
			wait_timeout_ms = int(flags.cskh_sf_wait_timeout_ms)
		if getattr(flags, "cskh_sf_poll_interval_ms", None) is not None:
			poll_interval_ms = int(flags.cskh_sf_poll_interval_ms)

	pre_epochs = get_domains_epoch_tuple(*domains)
	l1_key = (site, namespace, scope_key, pre_epochs)
	if l1_lru is not None:
		hit_l1 = l1_lru.get(l1_key, _LRU_MISS)
		if hit_l1 is not _LRU_MISS:
			_record_coalesce_stat("l1_hits", 1, site=site)
			return hit_l1

	digest = _build_sf_digest(site, namespace, scope_key, pre_epochs)
	inproc_key = (site, namespace, digest)
	cache_obj = _resolve_redis_wrapper()
	has_redis = cache_obj is not None and (
		(_redis_mod is not None and isinstance(cache_obj, _redis_mod.Redis))
		or (hasattr(cache_obj, "set") and hasattr(cache_obj, "get"))
	)
	val_rkey = cache_obj.make_key(f"cskh:sf:v1:val:{namespace}:{digest}") if has_redis else ""
	lock_rkey = cache_obj.make_key(f"cskh:sf:v1:lock:{namespace}:{digest}") if has_redis else ""

	if has_redis:
		hit_r, found_r = _sf_redis_get_json(cache_obj, val_rkey, pre_epochs)
		if found_r:
			if l1_lru is not None:
				l1_lru.set(l1_key, hit_r)
			_record_coalesce_stat("redis_hits", 1, site=site)
			return hit_r

	owner_token = f"{os.getpid()}:{threading.get_ident()}:{secrets.token_hex(8)}"
	if has_redis:
		acquired = _sf_redis_acquire_lock(cache_obj, lock_rkey, owner_token, lock_ttl_ms)
		if acquired:
			_register_inproc_sf_event(inproc_key)
	else:
		acquired, _ = _acquire_inproc_sf_slot(inproc_key)

	if acquired:
		try:
			_record_coalesce_stat("leader_computes", 1, site=site)
			val = _timed_compute()
			post_epochs = _force_fresh_domains_epoch_tuple(site, domains)
			if post_epochs == pre_epochs:
				if has_redis:
					_sf_redis_set_json(cache_obj, val_rkey, {"ep": list(pre_epochs), "v": val}, val_ttl_ms)
				if l1_lru is not None:
					l1_lru.set(l1_key, val)
			else:
				_record_coalesce_stat("epoch_changed_mid_compute", 1, site=site)
			return val
		finally:
			if has_redis:
				_sf_release_lock_if_owner(cache_obj, lock_rkey, owner_token, site=site)
			_notify_inproc_sf_waiters(inproc_key)

	# Follower wait loop (bounded strictly by `wait_timeout_ms`)
	deadline = time.monotonic() + (max(0, int(wait_timeout_ms)) / 1000.0)
	poll_sec = max(0.002, int(poll_interval_ms) / 1000.0)
	active_epochs = pre_epochs
	active_digest = digest
	active_inproc_key = inproc_key
	active_l1_key = l1_key
	active_val_rkey = val_rkey
	active_lock_rkey = lock_rkey

	while True:
		if l1_lru is not None:
			hit_w1 = l1_lru.get(active_l1_key, _LRU_MISS)
			if hit_w1 is not _LRU_MISS:
				_record_coalesce_stat("coalesced_follower_hits", 1, site=site)
				return hit_w1

		if has_redis:
			hit_wr, found_wr = _sf_redis_get_json(cache_obj, active_val_rkey, active_epochs)
			if found_wr:
				if l1_lru is not None:
					l1_lru.set(active_l1_key, hit_wr)
				_record_coalesce_stat("coalesced_follower_hits", 1, site=site)
				return hit_wr

		fresh_epochs = _force_fresh_domains_epoch_tuple(site, domains)
		if fresh_epochs != active_epochs:
			active_epochs = fresh_epochs
			active_digest = _build_sf_digest(site, namespace, scope_key, active_epochs)
			active_inproc_key = (site, namespace, active_digest)
			active_l1_key = (site, namespace, scope_key, active_epochs)
			if has_redis:
				active_val_rkey = cache_obj.make_key(f"cskh:sf:v1:val:{namespace}:{active_digest}")
				active_lock_rkey = cache_obj.make_key(f"cskh:sf:v1:lock:{namespace}:{active_digest}")
				hit_ne, found_ne = _sf_redis_get_json(cache_obj, active_val_rkey, active_epochs)
				if found_ne:
					if l1_lru is not None:
						l1_lru.set(active_l1_key, hit_ne)
					_record_coalesce_stat("coalesced_follower_hits", 1, site=site)
					return hit_ne

		if has_redis and not _sf_redis_lock_exists(cache_obj, active_lock_rkey):
			tk_token = f"{os.getpid()}:{threading.get_ident()}:{secrets.token_hex(8)}"
			if _sf_redis_acquire_lock(cache_obj, active_lock_rkey, tk_token, lock_ttl_ms):
				_register_inproc_sf_event(active_inproc_key)
				try:
					hit_dc, found_dc = _sf_redis_get_json(cache_obj, active_val_rkey, active_epochs)
					if found_dc:
						if l1_lru is not None:
							l1_lru.set(active_l1_key, hit_dc)
						_record_coalesce_stat("coalesced_follower_hits", 1, site=site)
						return hit_dc
					_record_coalesce_stat("lock_expired_takeovers", 1, site=site)
					_record_coalesce_stat("leader_computes", 1, site=site)
					val_tk = _timed_compute()
					post_tk = _force_fresh_domains_epoch_tuple(site, domains)
					if post_tk == active_epochs:
						_sf_redis_set_json(
							cache_obj, active_val_rkey, {"ep": list(active_epochs), "v": val_tk}, val_ttl_ms
						)
						if l1_lru is not None:
							l1_lru.set(active_l1_key, val_tk)
					else:
						_record_coalesce_stat("epoch_changed_mid_compute", 1, site=site)
					return val_tk
				finally:
					_sf_release_lock_if_owner(cache_obj, active_lock_rkey, tk_token, site=site)
					_notify_inproc_sf_waiters(active_inproc_key)
		elif not has_redis:
			tk_inproc_ok, _ = _acquire_inproc_sf_slot(active_inproc_key)
			if tk_inproc_ok:
				try:
					if l1_lru is not None:
						hit_ip = l1_lru.get(active_l1_key, _LRU_MISS)
						if hit_ip is not _LRU_MISS:
							_record_coalesce_stat("coalesced_follower_hits", 1, site=site)
							return hit_ip
					_record_coalesce_stat("lock_expired_takeovers", 1, site=site)
					_record_coalesce_stat("leader_computes", 1, site=site)
					val_ip = _timed_compute()
					post_ip = _force_fresh_domains_epoch_tuple(site, domains)
					if post_ip == active_epochs and l1_lru is not None:
						l1_lru.set(active_l1_key, val_ip)
					elif post_ip != active_epochs:
						_record_coalesce_stat("epoch_changed_mid_compute", 1, site=site)
					return val_ip
				finally:
					_notify_inproc_sf_waiters(active_inproc_key)

		rem = deadline - time.monotonic()
		if rem <= 0:
			_record_coalesce_stat("wait_timeouts", 1, site=site)
			_record_coalesce_stat("fallback_computes", 1, site=site)
			val_fb = _timed_compute()
			post_fb = _force_fresh_domains_epoch_tuple(site, domains)
			if post_fb == active_epochs:
				if has_redis:
					_sf_redis_set_json(
						cache_obj,
						active_val_rkey,
						{"ep": list(active_epochs), "v": val_fb},
						val_ttl_ms,
						nx=True,
					)
				if l1_lru is not None:
					l1_lru.set(active_l1_key, val_fb)
			else:
				_record_coalesce_stat("epoch_changed_mid_compute", 1, site=site)
			return val_fb

		_wait_inproc_or_sleep(active_inproc_key, min(poll_sec, rem))


def get_coalesced_drilldown_count(
	scope_key: tuple,
	count_fn,
	*,
	lock_ttl_ms: int = 5_000,
	wait_timeout_ms: int = 2_500,
) -> int:
	"""Coalesce identical drill-down `SELECT COUNT(*)` calls across Gunicorn processes & threads."""
	val = coalesced_epoch_compute(
		"dd_count",
		scope_key,
		DRILLDOWN_COUNT_DOMAINS,
		lambda: int(count_fn()),
		l1_lru=_DRILLDOWN_COUNT_LRU,
		lock_ttl_ms=lock_ttl_ms,
		wait_timeout_ms=wait_timeout_ms,
		is_sql_count=True,
	)
	return int(val or 0)


def get_cached_published_kpi_run(period_type: str, period_key: str, campaign_filter: str, load_fn):
	if not is_runtime_db_cache_active():
		return load_fn()

	def _load_dict():
		row = load_fn()
		return "__NONE__" if row is None else dict(row)

	hit = coalesced_epoch_compute(
		"kpi_run",
		(period_type, period_key, campaign_filter or ""),
		(DOMAIN_KPI_SNAPSHOT,),
		_load_dict,
		l1_lru=_KPI_RUN_LRU,
	)
	return None if (hit is None or hit == "__NONE__") else frappe._dict(hit)


def get_cached_kpi_snapshots(
	*,
	run_id: str,
	period_type: str,
	period_key: str,
	scope_type: str,
	campaign_filter: str,
	scope_ids: tuple[str, ...] | None,
	team_filters: tuple[str, ...] | None,
	load_fn,
) -> list[dict]:
	if not is_runtime_db_cache_active():
		return load_fn()

	def _load_rows():
		rows = load_fn()
		return [dict(r) for r in rows]

	hit = coalesced_epoch_compute(
		"kpi_snap",
		(run_id, period_type, period_key, scope_type, campaign_filter or "", scope_ids, team_filters),
		(DOMAIN_KPI_SNAPSHOT,),
		_load_rows,
		l1_lru=_KPI_SNAP_LRU,
	)
	return [frappe._dict(copy.deepcopy(r)) for r in (hit or ())]


def get_cached_dirty_pending(from_iso: str, to_iso: str, campaign_filter: str, check_fn) -> bool:
	if not is_runtime_db_cache_active():
		return bool(check_fn())
	val = coalesced_epoch_compute(
		"dirty_chk",
		(from_iso, to_iso, campaign_filter or ""),
		(DOMAIN_DIRTY_SCOPE, DOMAIN_KPI_SNAPSHOT),
		lambda: bool(check_fn()),
		l1_lru=_DIRTY_CHECK_LRU,
	)
	return bool(val)


def get_cached_invariant_bool(name: str, domains: tuple[str, ...], args_key: tuple, check_fn) -> bool:
	if not is_runtime_db_cache_active():
		return bool(check_fn())
	val = coalesced_epoch_compute(
		"inv_bool",
		(name, args_key),
		domains,
		lambda: bool(check_fn()),
		l1_lru=_INVARIANT_LRU,
	)
	return bool(val)


def get_ssr_html_cache() -> _BoundedLRU:
	return _SSR_HTML_LRU


# ---------------------------------------------------------------------------
# 6. `doc_events` invalidation hooks (wired in `hooks.py`)
# ---------------------------------------------------------------------------


def on_policy_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_POLICY)


def on_outcome_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_OUTCOME)


def on_calendar_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_CALENDAR)


def on_campaign_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_CAMPAIGN, DOMAIN_POLICY)


def on_user_or_hierarchy_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_USER_HIERARCHY, DOMAIN_POLICY)


def on_translation_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_TRANSLATIONS)


def on_correction_doc_change(doc=None, method=None) -> None:
	bump_cache_domain(DOMAIN_CORRECTION)


_ensure_mutation_hooks_installed()
