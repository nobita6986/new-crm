"""Shared helpers for the CSKH pages (same Frappe site/session as CRM).

The pages are rendered by the Frappe web engine of the SAME site and session
as CRM (ADR-UI-001 P0, ADR-P1-06 P1, ADR-P2-01 P2). There is no second SPA and
no second backend: every list comes from the whitelisted P1/P2 APIs and every
mutation is a POST with the Frappe CSRF token.

The navigation is a **convenience only**. Hiding a link never replaces the
server-side permission check: each API resolves the actor's scope itself.

The page gate mirrors the role grant of the matching API (P1 §8.1, P2 §11.1).
It is never *stricter* than the API, otherwise the UI would contradict the
contract, and never *looser*: a deep link to a page the actor may not use
answers with the permission-denied page instead of a crash.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from crm_cskh import p1_strings, p2_strings
from crm_cskh.services import errors as p1_errors

ROLE_ADMIN = "CSKH CRM Admin"
ROLE_BOD = "CSKH BOD Viewer"

# The language of the CSKH screens (P1 §9.6, P2 §12.5).
LANGUAGE = "vi"

# Role grants mirroring backend APIs (P1 §8.1, P2 §10 & §11.1).
READ_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN, ROLE_BOD}
WORKFLOW_PII_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN}
POOL_ROLES = {"Sales Manager", ROLE_ADMIN, ROLE_BOD}
DASHBOARD_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
IMPORT_ROLES = {ROLE_ADMIN}
CAMPAIGN_READ_ROLES = {ROLE_ADMIN, ROLE_BOD, "Sales Manager"}
SETTINGS_READ_ROLES = {ROLE_ADMIN, ROLE_BOD, "Sales Manager"}

# Navigation entries per role. Keys are English message ids translated via Frappe i18n.
NAV_ITEMS = (
	{"key": "pool", "route": "/cskh/pool", "label": "Customer pool"},
	{"key": "import", "route": "/cskh/import", "label": "Import list"},
	{"key": "campaigns", "route": "/cskh/campaigns", "label": "Campaigns and rules"},
	{"key": "my-customers", "route": "/cskh/my-customers", "label": "My customers"},
	{"key": "callbacks", "route": "/cskh/callbacks", "label": "Callbacks schedule"},
	{"key": "progress", "route": "/cskh/progress", "label": "Care progress"},
	{"key": "dashboard", "route": "/cskh/dashboard", "label": "Distribution dashboard"},
	{"key": "settings", "route": "/cskh/settings", "label": "Outcome and policy settings"},
)

NAV_BY_ROLE = {
	ROLE_ADMIN: {
		"pool",
		"import",
		"campaigns",
		"my-customers",
		"callbacks",
		"progress",
		"dashboard",
		"settings",
	},
	ROLE_BOD: {
		"pool",
		"campaigns",
		"my-customers",
		"progress",
		"dashboard",
		"settings",
	},
	"Sales Manager": {
		"pool",
		"campaigns",
		"my-customers",
		"callbacks",
		"progress",
		"dashboard",
		"settings",
	},
	"Sales User": {"my-customers", "callbacks", "progress"},
}


def roles(user=None):
	return set(frappe.get_roles(user or frappe.session.user))


def is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in roles(user)


def is_admin(user=None):
	return is_system(user) or ROLE_ADMIN in roles(user)


def is_bod(user=None):
	return is_system(user) or ROLE_BOD in roles(user)


def is_leader(user=None):
	user = user or frappe.session.user
	if is_admin(user):
		return True
	return "Sales Manager" in roles(user)


def is_staff(user=None):
	return "Sales User" in roles(user)


def has_role_grant(grants, user=None):
	"""True when the actor holds one of the business roles of the grant."""
	if is_system(user):
		return True
	return bool(roles(user) & set(grants))


def _visible_keys():
	"""Navigation keys visible to the actor."""
	if is_system():
		return {item["key"] for item in NAV_ITEMS}
	keys = set()
	for role, allowed in NAV_BY_ROLE.items():
		if role in roles():
			keys |= allowed
	return keys


def _asset_version():
	"""A cache-busting version of the CSKH static assets."""
	import os

	folder = os.path.join(os.path.dirname(__file__), "..", "public", "cskh")
	folder = os.path.normpath(folder)
	newest = 0
	if os.path.isdir(folder):
		for name in os.listdir(folder):
			path = os.path.join(folder, name)
			if os.path.isfile(path):
				newest = max(newest, int(os.path.getmtime(path)))
	return str(newest)


def timezone_context(user=None):
	"""Resolve site/user timezone and authoritative server timestamp (P2 §12.5).

	Never relies on browser host OS timezone: publishes both `site` timezone
	(used to interpret Frappe naive datetimes) and `effective` user/site
	timezone (used to render timestamps and compute 23:59 / 00:00 boundaries).
	"""
	user = user or frappe.session.user
	site_tz = "Asia/Ho_Chi_Minh"
	try:
		site_tz = frappe.utils.get_system_timezone() or site_tz
	except Exception:
		site_tz = "Asia/Ho_Chi_Minh"

	user_tz = ""
	if user and user != "Guest":
		try:
			user_tz = frappe.db.get_value("User", user, "time_zone") or ""
		except Exception:
			user_tz = ""

	effective_tz = user_tz or site_tz or "Asia/Ho_Chi_Minh"
	try:
		tz_obj = ZoneInfo(effective_tz)
	except Exception:
		effective_tz = "Asia/Ho_Chi_Minh"
		tz_obj = ZoneInfo("Asia/Ho_Chi_Minh")

	try:
		site_obj = ZoneInfo(site_tz)
	except Exception:
		site_tz = "Asia/Ho_Chi_Minh"
		site_obj = ZoneInfo("Asia/Ho_Chi_Minh")

	now_utc = datetime.now(timezone.utc)
	server_now = now_utc.astimezone(tz_obj)
	site_now = now_utc.astimezone(site_obj)
	return {
		"site": site_tz,
		"user": user_tz,
		"effective": effective_tz,
		"server_now_iso": server_now.isoformat(timespec="seconds"),
		"site_offset": site_now.strftime("%z"),
		"effective_offset": server_now.strftime("%z"),
	}


def build_context(
	context,
	route="",
	title=None,
	subtitle=None,
	allowed_roles=None,
	require_admin=False,
):
	"""Common context for every CSKH page."""
	frappe.local.lang = LANGUAGE
	if frappe.local.form_dict is not None:
		frappe.local.form_dict.lang = LANGUAGE
	context.lang = LANGUAGE
	if isinstance(getattr(context, "boot", None), dict):
		context.boot["lang"] = LANGUAGE

	if frappe.session.user == "Guest":
		frappe.response["status_code"] = 403
		frappe.throw(_("Log in to access this page."), frappe.PermissionError)

	if require_admin and not is_admin():
		frappe.response["status_code"] = 403
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	if allowed_roles is not None and not has_role_grant(allowed_roles):
		frappe.response["status_code"] = 403
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	context.no_cache = 1
	context.title = _(title) if title else _("CSKH")
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.cskh_csrf_header = {"X-Frappe-CSRF-Token": context.csrf_token}
	context.cskh_user = frappe.session.user
	context.cskh_route = route
	context.cskh_page_title = title
	context.cskh_page_subtitle = subtitle
	context.cskh_navigation = [item for item in NAV_ITEMS if item["key"] in _visible_keys()]
	context.cskh_capabilities = {
		"admin": is_admin(),
		"bod": is_bod(),
		"leader": is_leader(),
		"staff": is_staff(),
		"pii": not is_bod() or is_admin(),
		"can_manage_settings": is_admin(),
		"can_review_correction": is_admin() or is_leader(),
	}
	context.cskh_timezone = timezone_context(frappe.session.user)
	context.cskh_error_codes = list(p1_errors.ALL_CODES) + list(p2_strings.P2_ERROR_CODES)
	context.cskh_i18n = p1_strings.translated_map()
	context.cskh_error_texts = p1_strings.translated_error_texts()
	context.cskh_asset_version = _asset_version()
	return context
