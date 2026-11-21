"""Server-side `/crm` sidebar bridge & SaaS theme injector and Frappe App Switcher hook.

Exposes `CSKH` directly on the Frappe CRM left navigation panel (`/crm/*`), injects
the cohesive soft Indigo/Slate SaaS stylesheet (`crm-theme.css`) into `<head>`, and
registers `CSKH` in the Frappe App Switcher (`add_to_apps_screen`) without modifying
upstream `apps/crm` Git-tracked files (`ADR-UI-001`, `Containerfile` clean-tree invariant).
"""

from __future__ import annotations

import html
import os

import frappe

BRIDGE_SCRIPT_PATH = "/assets/crm_cskh/cskh/crm-sidebar-bridge.js"
BRIDGE_MARKER = "crm-sidebar-bridge.js"
CRM_THEME_CSS_PATH = "/assets/crm_cskh/cskh/crm-theme.css"
CRM_THEME_MARKER = 'data-cskh-crm-theme="1"'
DEFAULT_SIDEBAR_HREF = "/cskh"
DEFAULT_SIDEBAR_LABEL = "CSKH"

# Mirror `cskh_context.READ_ROLES` so only actors authorized to enter `/cskh`
# receive the sidebar menu entry and App Switcher item.
CSKH_MENU_ROLES = frozenset(
	(
		"Sales User",
		"Sales Manager",
		"CSKH CRM Admin",
		"CSKH BOD Viewer",
	)
)


def has_app_permission(user: str | None = None) -> bool:
	"""Return True when `user` is authenticated and holds a valid CSKH or System role."""
	try:
		resolved_user = user or getattr(getattr(frappe, "session", None), "user", None)
		if not resolved_user or resolved_user == "Guest":
			return False
		if resolved_user == "Administrator":
			return True
		user_roles = set(frappe.get_roles(resolved_user) or ())
		if "System Manager" in user_roles:
			return True
		return bool(user_roles & CSKH_MENU_ROLES)
	except Exception:
		return False


def _asset_mtime_version(filename: str) -> str:
	try:
		asset_file = os.path.join(os.path.dirname(__file__), "public", "cskh", filename)
		if os.path.isfile(asset_file):
			return str(int(os.path.getmtime(asset_file)))
	except Exception:
		pass
	return "1"


def _bridge_asset_version() -> str:
	return _asset_mtime_version("crm-sidebar-bridge.js")


def _theme_asset_version() -> str:
	return _asset_mtime_version("crm-theme.css")


def _build_theme_link_tag(*, version: str | None = None) -> str:
	ver = html.escape(version or _theme_asset_version(), quote=True)
	safe_href = html.escape(f"{CRM_THEME_CSS_PATH}?v={ver}", quote=True)
	return f'<link rel="stylesheet" href="{safe_href}" {CRM_THEME_MARKER}>'


def _build_script_tag(
	*,
	href: str = DEFAULT_SIDEBAR_HREF,
	label: str = DEFAULT_SIDEBAR_LABEL,
	version: str | None = None,
) -> str:
	ver = html.escape(version or _bridge_asset_version(), quote=True)
	safe_src = html.escape(f"{BRIDGE_SCRIPT_PATH}?v={ver}", quote=True)
	safe_href = html.escape(href, quote=True)
	safe_label = html.escape(label, quote=True)
	return (
		f'<script defer src="{safe_src}" data-cskh-sidebar-bridge="1" '
		f'data-cskh-href="{safe_href}" data-cskh-label="{safe_label}"></script>'
	)


def _is_crm_spa_request(request) -> bool:
	if request is None:
		return False
	method = (getattr(request, "method", None) or "GET").upper()
	if method != "GET":
		return False
	path = getattr(request, "path", None) or ""
	return path == "/crm" or path.startswith("/crm/")


def inject_crm_sidebar_bridge(response=None, request=None):
	"""`after_request` hook: inject `crm-theme.css` + CSKH sidebar bridge into `/crm` HTML pages."""
	try:
		if response is None or not _is_crm_spa_request(request):
			return response
		if getattr(response, "status_code", None) != 200:
			return response
		mimetype = (getattr(response, "mimetype", None) or "").lower()
		if "html" not in mimetype:
			return response
		if not has_app_permission():
			return response

		raw = getattr(response, "data", None)
		if isinstance(raw, (bytes, bytearray)):
			body = bytes(raw).decode("utf-8", errors="replace")
		elif isinstance(raw, str):
			body = raw
		else:
			return response

		if 'id="app"' not in body:
			return response

		modified = False
		if CRM_THEME_MARKER not in body:
			head_idx = body.rfind("</head>")
			if head_idx >= 0:
				theme_tag = _build_theme_link_tag()
				body = f"{body[:head_idx]}{theme_tag}\n{body[head_idx:]}"
				modified = True

		if BRIDGE_MARKER not in body:
			closing_idx = body.rfind("</body>")
			if closing_idx >= 0:
				script_tag = _build_script_tag()
				body = f"{body[:closing_idx]}{script_tag}\n{body[closing_idx:]}"
				modified = True

		if modified:
			response.data = body
		return response
	except Exception:
		return response
