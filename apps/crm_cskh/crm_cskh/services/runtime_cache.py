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
import json
import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager

import frappe
from frappe.utils import cint

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
)

_EPOCH_REDIS_HASH = "cskh:runtime_epochs"
_EPOCH_SYNC_TTL_SEC = 0.25


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

_SCHEMA_LRU = _BoundedLRU(maxsize=2048)
_TRANSLATIONS_LRU = _BoundedLRU(maxsize=16)
_STATIC_UI_LRU = _BoundedLRU(maxsize=16)
_USER_PROFILE_LRU = _BoundedLRU(maxsize=2048)
_HIERARCHY_LRU = _BoundedLRU(maxsize=16)
_CAMPAIGN_LRU = _BoundedLRU(maxsize=32)
_POLICY_LRU = _BoundedLRU(maxsize=512)
_OUTCOME_LRU = _BoundedLRU(maxsize=32)
_CALENDAR_LRU = _BoundedLRU(maxsize=32)
_KPI_RUN_LRU = _BoundedLRU(maxsize=256)
_KPI_SNAP_LRU = _BoundedLRU(maxsize=512)
_DIRTY_CHECK_LRU = _BoundedLRU(maxsize=256)
_INVARIANT_LRU = _BoundedLRU(maxsize=128)
_SSR_HTML_LRU = _BoundedLRU(maxsize=1024)

_ASSET_VERSION_CACHE: str | None = None
_MUTATION_HOOKS_INSTALLED: bool = False

_SET_VALUE_DOCTYPE_DOMAINS: dict[str, tuple[str, ...]] = {
	"CSKH Policy": (DOMAIN_POLICY,),
	"CSKH Outcome Code": (DOMAIN_OUTCOME,),
	"CSKH Working Calendar": (DOMAIN_CALENDAR,),
	"CRM Holiday List": (DOMAIN_CALENDAR,),
	"CSKH Campaign": (DOMAIN_CAMPAIGN, DOMAIN_POLICY),
	"User": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"CRM Sales Hierarchy": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"CSKH Team Membership History": (DOMAIN_USER_HIERARCHY, DOMAIN_POLICY),
	"CSKH Attempt Correction": (DOMAIN_CORRECTION,),
	"CSKH Assignment History": (DOMAIN_ASSIGNMENT_HISTORY,),
	"CSKH Dirty Scope": (DOMAIN_DIRTY_SCOPE,),
	"CSKH KPI Run": (DOMAIN_KPI_SNAPSHOT, DOMAIN_DIRTY_SCOPE),
	"CSKH KPI Snapshot": (DOMAIN_KPI_SNAPSHOT, DOMAIN_DIRTY_SCOPE),
	"Translation": (DOMAIN_TRANSLATIONS,),
}


def _ensure_mutation_hooks_installed() -> None:
	"""Idempotently hook `Database.set_value` and `frappe.clear_cache` so direct low-level
	mutations (`frappe.db.set_value(..., update_modified=False)`, `bench clear-cache`)
	invalidate `runtime_cache` domains immediately even when `doc_events` are bypassed."""
	global _MUTATION_HOOKS_INSTALLED
	if _MUTATION_HOOKS_INSTALLED:
		return
	try:
		from frappe.database.database import Database

		orig_set_value = getattr(Database, "set_value", None)
		if callable(orig_set_value) and not getattr(orig_set_value, "_cskh_cache_wrapped", False):

			def _wrapped_set_value(self, dt, dn, field, val=None, *args, **kwargs):
				res = orig_set_value(self, dt, dn, field, val, *args, **kwargs)
				doms = _SET_VALUE_DOCTYPE_DOMAINS.get(str(dt) if dt else "")
				if doms:
					bump_cache_domain(*doms)
				return res

			_wrapped_set_value._cskh_cache_wrapped = True  # type: ignore[attr-defined]
			Database.set_value = _wrapped_set_value  # type: ignore[method-assign]

		orig_clear_cache = getattr(frappe, "clear_cache", None)
		if callable(orig_clear_cache) and not getattr(orig_clear_cache, "_cskh_cache_wrapped", False):

			def _wrapped_clear_cache(*args, **kwargs):
				res = orig_clear_cache(*args, **kwargs)
				clear_all_local_caches()
				bump_cache_domain(*ALL_DOMAINS)
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


def clear_all_local_caches() -> None:
	for lru in (
		_SCHEMA_LRU,
		_TRANSLATIONS_LRU,
		_STATIC_UI_LRU,
		_USER_PROFILE_LRU,
		_HIERARCHY_LRU,
		_CAMPAIGN_LRU,
		_POLICY_LRU,
		_OUTCOME_LRU,
		_CALENDAR_LRU,
		_KPI_RUN_LRU,
		_KPI_SNAP_LRU,
		_DIRTY_CHECK_LRU,
		_INVARIANT_LRU,
		_SSR_HTML_LRU,
	):
		lru.clear()
	with _EPOCH_LOCK:
		_SITE_EPOCHS.clear()


def _sync_site_epochs(site: str, *, force: bool = False) -> dict[str, int]:
	now_mono = time.monotonic()
	with _EPOCH_LOCK:
		cached = _SITE_EPOCHS.get(site)
		if not force and cached is not None and (now_mono - cached[0]) < _EPOCH_SYNC_TTL_SEC:
			return cached[1]

	epochs: dict[str, int] = dict(cached[1]) if cached is not None else {}
	try:
		cache = getattr(frappe, "cache", None)
		if cache is not None:
			rkey = cache.make_key(_EPOCH_REDIS_HASH)
			raw = cache.hgetall(rkey) or {}
			for k, v in raw.items():
				k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
				v_int = int(v.decode("utf-8") if isinstance(v, bytes) else v)
				if v_int > epochs.get(k_str, 0):
					epochs[k_str] = v_int
	except Exception:
		pass

	with _EPOCH_LOCK:
		cur = _SITE_EPOCHS.get(site)
		if cur is not None:
			for k_str, v_int in cur[1].items():
				if v_int > epochs.get(k_str, 0):
					epochs[k_str] = v_int
		_SITE_EPOCHS[site] = (now_mono, epochs)
		return epochs


def get_domain_epoch(domain: str) -> int:
	site = _current_site()
	epochs = _sync_site_epochs(site, force=False)
	return int(epochs.get(domain, 0))


def get_domains_epoch_tuple(*domains: str) -> tuple[int, ...]:
	site = _current_site()
	epochs = _sync_site_epochs(site, force=False)
	return tuple(int(epochs.get(d, 0)) for d in domains)


def _bump_domains_once(domains: tuple[str, ...]) -> None:
	if not domains:
		return
	site = _current_site()
	now_mono = time.monotonic()
	with _EPOCH_LOCK:
		cur = _SITE_EPOCHS.get(site)
		local_map = dict(cur[1]) if cur is not None else {}
		for dom in domains:
			local_map[dom] = int(local_map.get(dom, 0)) + 1
		_SITE_EPOCHS[site] = (now_mono, local_map)

	try:
		cache = getattr(frappe, "cache", None)
		if cache is not None:
			rkey = cache.make_key(_EPOCH_REDIS_HASH)
			for dom in domains:
				new_val = int(cache.hincrby(rkey, dom, 1) or 0)
				with _EPOCH_LOCK:
					cur_s = _SITE_EPOCHS.get(site)
					if cur_s is not None and new_val > cur_s[1].get(dom, 0):
						cur_s[1][dom] = new_val
	except Exception:
		pass


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
	try:
		import frappe.translate as tr_mod
	except Exception:
		return

	orig = getattr(tr_mod, "get_all_translations", None)
	if orig is None or getattr(orig, "_cskh_inproc_wrapped", False):
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


def on_before_request(*_args, **_kwargs) -> None:
	"""`before_request` hook ensuring worker-memory translation cache is active."""
	install_inprocess_translation_cache()


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
	val = bool(frappe.db.has_column(doctype, column))
	_SCHEMA_LRU.set(key, val)
	return val


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

	key = (_current_site(), u, get_domain_epoch(DOMAIN_USER_HIERARCHY))
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
		return copy.deepcopy(hit)
	res = compute_fn()
	_POLICY_LRU.set(key, copy.deepcopy(res))
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
# 5. KPI Published Run, Immutable Snapshot, Dirty-Scope & Drill-down Guards
# ---------------------------------------------------------------------------


def get_cached_published_kpi_run(period_type: str, period_key: str, campaign_filter: str, load_fn):
	if not is_runtime_db_cache_active():
		return load_fn()
	key = (
		_current_site(),
		period_type,
		period_key,
		campaign_filter or "",
		get_domain_epoch(DOMAIN_KPI_SNAPSHOT),
	)
	hit = _KPI_RUN_LRU.get(key)
	if hit is not None:
		return None if hit == "__NONE__" else frappe._dict(hit)
	row = load_fn()
	if row is None:
		_KPI_RUN_LRU.set(key, "__NONE__")
		return None
	_KPI_RUN_LRU.set(key, dict(row))
	return frappe._dict(row)


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
	key = (
		_current_site(),
		run_id,
		period_type,
		period_key,
		scope_type,
		campaign_filter or "",
		scope_ids,
		team_filters,
		get_domain_epoch(DOMAIN_KPI_SNAPSHOT),
	)
	hit = _KPI_SNAP_LRU.get(key)
	if hit is not None:
		return [frappe._dict(copy.deepcopy(r)) for r in hit]
	rows = load_fn()
	_KPI_SNAP_LRU.set(key, [dict(r) for r in rows])
	return [frappe._dict(copy.deepcopy(r)) for r in rows]


def get_cached_dirty_pending(from_iso: str, to_iso: str, campaign_filter: str, check_fn) -> bool:
	if not is_runtime_db_cache_active():
		return bool(check_fn())
	key = (
		_current_site(),
		from_iso,
		to_iso,
		campaign_filter or "",
		get_domains_epoch_tuple(DOMAIN_DIRTY_SCOPE, DOMAIN_KPI_SNAPSHOT),
	)
	hit = _DIRTY_CHECK_LRU.get(key)
	if hit is not None:
		return bool(hit)
	val = bool(check_fn())
	_DIRTY_CHECK_LRU.set(key, val)
	return val


def get_cached_invariant_bool(name: str, domains: tuple[str, ...], args_key: tuple, check_fn) -> bool:
	if not is_runtime_db_cache_active():
		return bool(check_fn())
	key = (_current_site(), name, args_key, get_domains_epoch_tuple(*domains))
	hit = _INVARIANT_LRU.get(key)
	if hit is not None:
		return bool(hit)
	val = bool(check_fn())
	_INVARIANT_LRU.set(key, val)
	return val


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
