"""Worker-care entry point and native CRM helper navigation.

Only successful authenticated CRM HTML documents are adapted. Commercial data,
API permissions, login redirects and error responses keep their native behavior.
The existing after-request delegate preserves the N-1 hook compatibility contract.
"""

from __future__ import annotations

import html
import os
import urllib.parse

import frappe

BRIDGE_SCRIPT_PATH = "/assets/crm_cskh/cskh/crm-sidebar-bridge.js"
BRIDGE_MARKER = "crm-sidebar-bridge.js"
CRM_THEME_CSS_PATH = "/assets/crm_cskh/cskh/crm-theme.css"
CRM_THEME_MARKER = 'data-cskh-crm-theme="1"'
DEFAULT_SIDEBAR_HREF = "/cskh"
DEFAULT_SIDEBAR_LABEL = "Chăm sóc người lao động"
SALES_CRM_SECTIONS = frozenset(("dashboard", "deals", "forecast", "forecasts", "revenue"))

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


@frappe.whitelist(methods=["GET"])
def worker_care_entry():
	"""Authenticated DEV bootstrap capability; contains no user or business data."""
	return {
		"enabled": has_app_permission(),
		"href": DEFAULT_SIDEBAR_HREF,
		"label": frappe._("Worker care"),
	}


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
		f'<script src="{safe_src}" data-cskh-sidebar-bridge="1" '
		f'data-cskh-worker-care="1" data-cskh-href="{safe_href}" data-cskh-label="{safe_label}"></script>'
	)


def is_sales_crm_path(path: str) -> bool:
	"""Match normalized route segments, including percent-encoded or repeated-slash variants."""
	if (
		not isinstance(path, str)
		or not path.startswith("/")
		or path.startswith("//")
		or any(ch in path for ch in ("?", "#", "\\"))
	):
		return False
	parts: list[str] = []
	for raw_seg in path.split("/"):
		if not raw_seg:
			continue
		decoded = urllib.parse.unquote(raw_seg)
		if any(ord(ch) < 32 or ord(ch) == 127 for ch in decoded):
			return False
		for sub in decoded.split("/"):
			if sub:
				parts.append(sub)
	if not parts or parts[0] != "crm":
		return False
	if len(parts) == 1:
		return True
	if parts[1] in SALES_CRM_SECTIONS:
		return True
	return len(parts) >= 4 and parts[1] == "data-import" and parts[2] == "doctype" and parts[3] == "CRM Deal"


def _is_crm_spa_request(request) -> bool:
	if request is None:
		return False
	method = (getattr(request, "method", None) or "GET").upper()
	if method != "GET":
		return False
	headers = getattr(request, "headers", None) or {}
	if headers.get("X-Requested-With", "").lower() == "xmlhttprequest":
		return False
	destination = headers.get("Sec-Fetch-Dest", "")
	if destination and destination not in ("document", "iframe"):
		return False
	accept = headers.get("Accept", "")
	if accept and "text/html" not in accept.lower() and "*/*" not in accept:
		return False
	# Frappe supports RPC dispatch through ?cmd= even outside /api/method/.
	if "cmd" in (getattr(request, "args", None) or {}):
		return False
	path = getattr(request, "path", None) or ""
	return path == "/crm" or path.startswith("/crm/")


def inject_crm_sidebar_bridge(response=None, request=None):
	"""Redirect sales screens; install the early navigation guard on CRM helper pages."""
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
		if is_sales_crm_path(getattr(request, "path", "")):
			# A fixed local destination deliberately drops sales filters and redirect=.
			# No bypass is granted to administrators: helpers remain reachable directly.
			response.status_code = 302
			response.headers["Location"] = DEFAULT_SIDEBAR_HREF
			response.headers["Cache-Control"] = "private, no-store"
			response.headers.pop("ETag", None)
			response.data = ""
			return response

		modified = False
		if CRM_THEME_MARKER not in body:
			head_idx = body.rfind("</head>")
			if head_idx >= 0:
				theme_tag = _build_theme_link_tag()
				body = f"{body[:head_idx]}{theme_tag}\n{body[head_idx:]}"
				modified = True

		if BRIDGE_MARKER not in body:
			# Classic script in <head> runs before the native deferred Vue entry point,
			# so SPA history/click guards are installed before its initial navigation.
			closing_idx = body.find(">", body.find("<head")) + 1 if "<head" in body else -1
			if closing_idx >= 0:
				translate = getattr(frappe, "_", None)
				label = translate("Worker care") if callable(translate) else DEFAULT_SIDEBAR_LABEL
				script_tag = _build_script_tag(label=label)
				body = f"{body[:closing_idx]}{script_tag}\n{body[closing_idx:]}"
				modified = True

		if modified:
			response.data = body
			response.headers["Cache-Control"] = "private, no-store"
			response.headers.pop("ETag", None)
		return response
	except Exception:
		return response
