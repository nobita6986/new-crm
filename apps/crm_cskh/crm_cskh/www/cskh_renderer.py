"""RBAC-enforced, role-profile SSR renderer for `/cskh/*` web pages (`P4 G18`).

Guarantees:
  1. **Zero Bypass of Page Authorization or Feature Flags:**
     On EVERY request, `_render_cskh_page_html` executes the page controller's `get_context()`
     (`build_context`) BEFORE touching any cached HTML, so `Guest` (`403`), unauthorized
     roles (`403`), and disabled `p3_enabled` (`403`) behave 100% identically to `TemplatePage`.
  2. **Zero Cross-User / Cross-Role / CSRF Leakage:**
     Skeleton HTML is keyed by `(site, path, is_authenticated, roles_tuple, nav_keys,
     capabilities_tuple, effective_tz, site_tz, asset_version, dev_mode, runtime_epochs)`,
     verified at runtime to contain zero user-identifying tokens (`live_user`, `full_name`,
     `user_image`) before sharing across same-capability sessions (automatically falling back
     to a per-user cache key if any user token were ever present), and EVERY response injects
     the live session's `cskh_user`, `csrf_token`, and `cskh_timezone` (`server_now_iso`).
  3. **Zero `hooks.py` Module Drift on `N -> N-1` Tier 3 Image Rollback (`G09`):**
     Installed in-process on `TemplatePage.get_html` rather than serialized into Redis
     `app_hooks["page_renderer"]`, so rolling container images back to `N-1` without flushing
     `redis-cache` never encounters missing module paths.
"""

from __future__ import annotations

import html as html_mod
import threading

import frappe
from frappe.website.page_renderers.template_page import TemplatePage

from crm_cskh.services import runtime_cache

_TZ_SENTINEL = "__CSKH_TIMEZONE_JSON_SENTINEL__"
_CSRF_HDR_SENTINEL = "__CSKH_CSRF_HEADER_JSON_SENTINEL__"
_CSRF_TOKEN_SENTINEL = "__CSKH_LIVE_CSRF_TOKEN_SENTINEL__"
_USER_HTML_SENTINEL = "__CSKH_LIVE_USER_HTML_SENTINEL__"
_USER_JSON_SENTINEL = "__CSKH_LIVE_USER_JSON_SENTINEL__"
_TEMPLATE_PAGE_FASTPATH_INSTALLED: bool = False
_RENDER_LOCK_STRIPES = tuple(threading.Lock() for _ in range(64))
_CSKH_PYMODULE_CACHE: dict[tuple[str, str], tuple[str, object]] = {}


def _inject_live_session_tokens(
	html: str,
	*,
	live_user: str,
	csrf_token: str,
	tz_payload: dict,
) -> str:
	csrf_hdr_json = runtime_cache.html_safe_json_dumps({"X-Frappe-CSRF-Token": csrf_token})
	tz_json = runtime_cache.html_safe_json_dumps(tz_payload)
	escaped_tok = html_mod.escape(csrf_token, quote=True)
	escaped_user = html_mod.escape(live_user, quote=True)
	user_json = runtime_cache.html_safe_json_dumps(live_user)
	return (
		html.replace(_CSRF_HDR_SENTINEL, csrf_hdr_json)
		.replace(_TZ_SENTINEL, tz_json)
		.replace(_CSRF_TOKEN_SENTINEL, escaped_tok)
		.replace(_USER_HTML_SENTINEL, escaped_user)
		.replace(_USER_JSON_SENTINEL, user_json)
	)


def _is_role_shareable_skeleton(
	skeleton_html: str,
	*,
	live_user: str,
	u_full_name: str,
	u_image: str,
) -> bool:
	if (
		_TZ_SENTINEL not in skeleton_html
		or _CSRF_HDR_SENTINEL not in skeleton_html
		or _USER_HTML_SENTINEL not in skeleton_html
		or _USER_JSON_SENTINEL not in skeleton_html
	):
		return False
	if live_user:
		if live_user in skeleton_html:
			return False
		esc_u = html_mod.escape(live_user, quote=True)
		if esc_u != live_user and esc_u in skeleton_html:
			return False
		json_u = runtime_cache.html_safe_json_dumps(live_user)
		if json_u in skeleton_html:
			return False
	if u_full_name and u_full_name != live_user:
		if u_full_name in skeleton_html:
			return False
		esc_fn = html_mod.escape(u_full_name, quote=True)
		if esc_fn != u_full_name and esc_fn in skeleton_html:
			return False
	if u_image and u_image in skeleton_html:
		return False
	return True


def _resolve_cskh_pymodule(page: TemplatePage, site: str) -> object | None:
	dev_mode = bool(frappe.conf.developer_mode)
	path_key = (site, str(page.path or ""))
	if not dev_mode:
		cached = _CSKH_PYMODULE_CACHE.get(path_key)
		if cached is not None:
			page.pymodule_name = cached[0]
			page.pymodule = cached[1]
			return cached[1]
	page.set_pymodule()
	pymod_name = getattr(page, "pymodule_name", None)
	if not pymod_name:
		return None
	pymod = frappe.get_module(pymod_name)
	page.pymodule = pymod
	if not dev_mode and len(_CSKH_PYMODULE_CACHE) < 256:
		_CSKH_PYMODULE_CACHE[path_key] = (str(pymod_name), pymod)
	return pymod


def _compile_skeleton_html(page: TemplatePage, orig_get_html) -> str:
	page.set_pymodule()
	sess_data = getattr(getattr(frappe.local, "session", None), "data", None)
	had_sess_csrf = isinstance(sess_data, dict) and "csrf_token" in sess_data
	prev_sess_csrf = sess_data.get("csrf_token") if isinstance(sess_data, dict) else None
	frappe.local.cskh_ssr_skeleton_sentinels = {
		"csrf_token": _CSRF_TOKEN_SENTINEL,
		"csrf_header_json": _CSRF_HDR_SENTINEL,
		"timezone_json": _TZ_SENTINEL,
		"user_html": _USER_HTML_SENTINEL,
		"user_json": _USER_JSON_SENTINEL,
	}
	if isinstance(sess_data, dict):
		sess_data["csrf_token"] = _CSRF_TOKEN_SENTINEL
	try:
		return orig_get_html(page)
	finally:
		frappe.local.cskh_ssr_skeleton_sentinels = None
		if isinstance(sess_data, dict):
			if had_sess_csrf:
				sess_data["csrf_token"] = prev_sess_csrf
			else:
				sess_data.pop("csrf_token", None)


def _render_cskh_page_html(page: TemplatePage, orig_get_html) -> str:
	runtime_cache.install_inprocess_translation_cache()
	if not runtime_cache.is_runtime_db_cache_active():
		return orig_get_html(page)

	# `PathResolver` already runs `can_render()` which populates `page.app`; only run if unset.
	if not getattr(page, "app", None):
		if not page.can_render():
			return orig_get_html(page)
	if getattr(page, "app", None) != "crm_cskh":
		return orig_get_html(page)

	site = runtime_cache._current_site()
	pymod = _resolve_cskh_pymodule(page, site)
	if pymod is None:
		return orig_get_html(page)

	get_ctx_fn = getattr(pymod, "get_context", None)
	if not callable(get_ctx_fn):
		return orig_get_html(page)

	# Step 1: ALWAYS run `get_context` live first so RBAC, Guest 403, P3 flag rollback, & CSRF run live.
	auth_ctx = frappe._dict({"boot": {}})
	data = get_ctx_fn(auth_ctx)
	if isinstance(data, dict):
		auth_ctx.update(data)

	live_user = str(auth_ctx.get("cskh_user") or frappe.session.user or "")
	live_csrf = str(auth_ctx.get("csrf_token") or "")
	live_tz = dict(auth_ctx.get("cskh_timezone") or {})
	caps = dict(auth_ctx.get("cskh_capabilities") or {})
	nav_sig = tuple(str(item.get("key") or "") for item in (auth_ctx.get("cskh_navigation") or ()))
	caps_sig = tuple(sorted((str(k), v) for k, v in caps.items()))
	u_prof = runtime_cache.get_user_auth_profile(live_user)
	roles_sig = tuple(sorted(u_prof["roles"]))

	role_cache_key = (
		site,
		str(page.path or ""),
		live_user != "Guest",
		roles_sig,
		nav_sig,
		caps_sig,
		str(live_tz.get("effective") or ""),
		str(live_tz.get("site") or ""),
		str(auth_ctx.get("cskh_asset_version") or ""),
		bool(frappe.conf.developer_mode),
		runtime_cache.get_domains_epoch_tuple(
			runtime_cache.DOMAIN_TRANSLATIONS,
			runtime_cache.DOMAIN_POLICY,
			runtime_cache.DOMAIN_USER_HIERARCHY,
		),
	)
	user_cache_key = (
		*role_cache_key,
		live_user,
		u_prof["full_name"],
		u_prof["user_image"],
	)

	ssr_cache = runtime_cache.get_ssr_html_cache()
	skeleton_html = ssr_cache.get(role_cache_key)
	if skeleton_html is None:
		skeleton_html = ssr_cache.get(user_cache_key)
	if skeleton_html is None:
		stripe_lock = _RENDER_LOCK_STRIPES[hash(role_cache_key) & 63]
		with stripe_lock:
			skeleton_html = ssr_cache.get(role_cache_key)
			if skeleton_html is None:
				skeleton_html = ssr_cache.get(user_cache_key)
			if skeleton_html is None:
				skeleton_html = _compile_skeleton_html(page, orig_get_html)
				if _is_role_shareable_skeleton(
					skeleton_html,
					live_user=live_user,
					u_full_name=str(u_prof["full_name"] or ""),
					u_image=str(u_prof["user_image"] or ""),
				):
					ssr_cache.set(role_cache_key, skeleton_html)
				elif _TZ_SENTINEL in skeleton_html and _CSRF_HDR_SENTINEL in skeleton_html:
					ssr_cache.set(user_cache_key, skeleton_html)

	return _inject_live_session_tokens(
		skeleton_html,
		live_user=live_user,
		csrf_token=live_csrf,
		tz_payload=live_tz,
	)


def install_template_page_fastpath() -> None:
	"""Idempotently hook `TemplatePage.get_html` in-process for `/cskh/*` web routes."""
	global _TEMPLATE_PAGE_FASTPATH_INSTALLED
	if _TEMPLATE_PAGE_FASTPATH_INSTALLED:
		return
	orig_get_html = getattr(TemplatePage, "get_html", None)
	if not callable(orig_get_html):
		return
	if getattr(orig_get_html, "_cskh_fastpath_wrapped", False):
		_TEMPLATE_PAGE_FASTPATH_INSTALLED = True
		return

	def _wrapped_get_html(self):
		path_val = str(getattr(self, "path", "") or "")
		if path_val == "cskh" or path_val.startswith("cskh/"):
			return _render_cskh_page_html(self, orig_get_html)
		return orig_get_html(self)

	_wrapped_get_html._cskh_fastpath_wrapped = True  # type: ignore[attr-defined]
	_wrapped_get_html._cskh_orig_get_html = orig_get_html  # type: ignore[attr-defined]
	TemplatePage.get_html = _wrapped_get_html  # type: ignore[method-assign]
	_TEMPLATE_PAGE_FASTPATH_INSTALLED = True


class CskhPageRenderer(TemplatePage):
	"""Explicit renderer subclass preserved for direct unit-test invocation."""

	def can_render(self):
		if not (self.path == "cskh" or str(self.path or "").startswith("cskh/")):
			return False
		return bool(super().can_render()) and getattr(self, "app", None) == "crm_cskh"

	def get_html(self):
		cur_fn = TemplatePage.get_html
		raw_orig = getattr(cur_fn, "_cskh_orig_get_html", cur_fn)
		return _render_cskh_page_html(self, raw_orig)
