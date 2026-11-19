"""Server-side `/crm` sidebar bridge injector and Frappe App Switcher permission hook.

Exposes `CSKH` directly on the Frappe CRM left navigation panel (`/crm/*`) and in the
Frappe App Switcher (`add_to_apps_screen`) without modifying upstream `apps/crm`
Git-tracked files (`ADR-UI-001`, `Containerfile` clean-tree invariant).
"""

from __future__ import annotations

import html
import os

import frappe

BRIDGE_SCRIPT_PATH = "/assets/crm_cskh/cskh/crm-sidebar-bridge.js"
BRIDGE_MARKER = "crm-sidebar-bridge.js"
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


def _bridge_asset_version() -> str:
	try:
		bridge_file = os.path.join(os.path.dirname(__file__), "public", "cskh", "crm-sidebar-bridge.js")
		if os.path.isfile(bridge_file):
			return str(int(os.path.getmtime(bridge_file)))
	except Exception:
		pass
	return "1"


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
	"""`after_request` hook: append the CSKH sidebar bridge `<script>` to `/crm` HTML pages."""
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

		if 'id="app"' not in body or BRIDGE_MARKER in body:
			return response

		closing_idx = body.rfind("</body>")
		if closing_idx < 0:
			return response

		script_tag = _build_script_tag()
		response.data = f"{body[:closing_idx]}{script_tag}\n{body[closing_idx:]}"
		return response
	except Exception:
		return response
