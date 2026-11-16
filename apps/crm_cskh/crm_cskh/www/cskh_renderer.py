"""RBAC-enforced, per-user SSR renderer for `/cskh/*` web pages (`P4 G18`).

Guarantees:
  1. **Zero Bypass of Page Authorization or Feature Flags:**
     On EVERY request, `CskhPageRenderer` executes the page controller's `get_context()`
     (`build_context`) BEFORE touching any cached HTML, so `Guest` (`403`), unauthorized
     roles (`403`), and disabled `p3_enabled` (`403`) behave 100% identically to `TemplatePage`.
  2. **Zero Cross-User / Cross-Role / CSRF Leakage:**
     Pre-CSRF HTML templates are strictly keyed per `(site, path, user, full_name, user_image,
     nav_keys, capabilities_tuple, effective_tz, site_tz, asset_version, runtime_epochs)` —
     never shared across distinct users or role capabilities — and every response injects the
     live session's `csrf_token` + live `cskh_timezone` (`server_now_iso`).
"""

from __future__ import annotations

import html as html_mod

import frappe
from frappe.website.page_renderers.template_page import TemplatePage

from crm_cskh.services import runtime_cache

_TZ_SENTINEL = "__CSKH_TIMEZONE_JSON_SENTINEL__"
_CSRF_HDR_SENTINEL = "__CSKH_CSRF_HEADER_JSON_SENTINEL__"
_CSRF_TOKEN_SENTINEL = "__CSKH_LIVE_CSRF_TOKEN_SENTINEL__"


class CskhPageRenderer(TemplatePage):
	"""Fast RBAC-gated renderer solely for `crm_cskh` `/cskh` routes."""

	def can_render(self):
		if not (self.path == "cskh" or self.path.startswith("cskh/")):
			return False
		return bool(super().can_render()) and getattr(self, "app", None) == "crm_cskh"

	def _inject_live_session_tokens(self, html: str, *, csrf_token: str, tz_payload: dict) -> str:
		csrf_hdr_json = runtime_cache.html_safe_json_dumps({"X-Frappe-CSRF-Token": csrf_token})
		tz_json = runtime_cache.html_safe_json_dumps(tz_payload)
		escaped_tok = html_mod.escape(csrf_token, quote=True)
		return (
			html.replace(_CSRF_HDR_SENTINEL, csrf_hdr_json)
			.replace(_TZ_SENTINEL, tz_json)
			.replace(_CSRF_TOKEN_SENTINEL, escaped_tok)
		)

	def get_html(self):
		runtime_cache.install_inprocess_translation_cache()
		if not runtime_cache.is_runtime_db_cache_active():
			return super().get_html()

		# Step 1: ALWAYS run the page controller's `get_context` first so RBAC,
		# Guest 403, P3 feature-flag rollback, and CSRF initialization run live.
		self.set_pymodule()
		if not getattr(self, "pymodule_name", None):
			return super().get_html()

		self.pymodule = frappe.get_module(self.pymodule_name)
		get_ctx_fn = getattr(self.pymodule, "get_context", None)
		if not callable(get_ctx_fn):
			return super().get_html()

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

		cache_key = (
			runtime_cache._current_site(),
			self.path,
			live_user,
			u_prof["full_name"],
			u_prof["user_image"],
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

		ssr_cache = runtime_cache.get_ssr_html_cache()
		skeleton_html = ssr_cache.get(cache_key)
		if skeleton_html is None:
			sess_data = getattr(getattr(frappe.local, "session", None), "data", None)
			had_sess_csrf = isinstance(sess_data, dict) and "csrf_token" in sess_data
			prev_sess_csrf = sess_data.get("csrf_token") if isinstance(sess_data, dict) else None
			frappe.local.cskh_ssr_skeleton_sentinels = {
				"csrf_token": _CSRF_TOKEN_SENTINEL,
				"csrf_header_json": _CSRF_HDR_SENTINEL,
				"timezone_json": _TZ_SENTINEL,
			}
			if isinstance(sess_data, dict):
				sess_data["csrf_token"] = _CSRF_TOKEN_SENTINEL
			try:
				skeleton_html = super().get_html()
			finally:
				frappe.local.cskh_ssr_skeleton_sentinels = None
				if isinstance(sess_data, dict):
					if had_sess_csrf:
						sess_data["csrf_token"] = prev_sess_csrf
					else:
						sess_data.pop("csrf_token", None)

			if _TZ_SENTINEL in skeleton_html and _CSRF_HDR_SENTINEL in skeleton_html:
				ssr_cache.set(cache_key, skeleton_html)

		return self._inject_live_session_tokens(
			skeleton_html,
			csrf_token=live_csrf,
			tz_payload=live_tz,
		)
