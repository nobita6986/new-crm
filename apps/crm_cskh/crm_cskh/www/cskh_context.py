"""Shared helpers for the CSKH pages (same Frappe site/session as CRM).

The pages are rendered by the Frappe web engine of the SAME site and session
as CRM (ADR-UI-001 P0, ADR-P1-06 P1). There is no second SPA and no second
backend: every list comes from the whitelisted P1 APIs and every mutation is
a POST with the Frappe CSRF token.

The navigation is a **convenience only**. Hiding a link never replaces the
server-side permission check: each API resolves the actor's scope itself.

The page gate mirrors the role grant of the matching API (P1 §8.1). It is
never *stricter* than the API, otherwise the UI would contradict the frozen
contract, and never *looser*: a deep link to a page the actor may not use
answers with the permission-denied page instead of a crash.
"""

import frappe
from frappe import _

from crm_cskh import p1_strings
from crm_cskh.services import errors as p1_errors

ROLE_ADMIN = "CSKH CRM Admin"
ROLE_BOD = "CSKH BOD Viewer"

# The role grants of the frozen P1 APIs (P1 §8.1).
#
# P1 §8.1, row "Xem tổng quan phân phối": BOD = toàn hệ thống, Admin = toàn
# hệ thống, Leader = đội mình. The frozen API contract
# (`docs/architecture/api-contract-p1.md` §4) grants the dashboard to
# `CSKH BOD Viewer` / `CSKH CRM Admin` / `Sales Manager` only: a staff member
# is **not** a dashboard reader, and the page gate mirrors the API exactly.
#
# A page gate must never be *looser* than the API, otherwise the operator
# opens a screen that answers `FORBIDDEN` on every read.
READ_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN, ROLE_BOD}
POOL_ROLES = {"Sales Manager", ROLE_ADMIN, ROLE_BOD}
DASHBOARD_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
IMPORT_ROLES = {ROLE_ADMIN}
CAMPAIGN_READ_ROLES = {ROLE_ADMIN, ROLE_BOD, "Sales Manager"}

# Navigation entries per role. The keys are English message ids; the template
# translates them with the Frappe i18n pipeline.
NAV_ITEMS = (
	{"key": "pool", "route": "/cskh/pool", "label": "Customer pool"},
	{"key": "import", "route": "/cskh/import", "label": "Import list"},
	{"key": "campaigns", "route": "/cskh/campaigns", "label": "Campaigns and rules"},
	{"key": "my-customers", "route": "/cskh/my-customers", "label": "My customers"},
	{"key": "dashboard", "route": "/cskh/dashboard", "label": "Distribution dashboard"},
)

# Which navigation entries a role may see. The keys mirror the backend
# permission of the matching API (P1 §8.1).
NAV_BY_ROLE = {
	ROLE_ADMIN: {"pool", "import", "campaigns", "my-customers", "dashboard"},
	ROLE_BOD: {"pool", "my-customers", "dashboard"},
	"Sales Manager": {"pool", "campaigns", "my-customers", "dashboard"},
	"Sales User": {"my-customers"},
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
	"""Navigation keys visible to the actor.

	Union of the role grants: a user may hold several roles (a team leader is
	usually also a sales user). The server still enforces the real scope.
	"""
	if is_system():
		return {item["key"] for item in NAV_ITEMS}
	keys = set()
	for role, allowed in NAV_BY_ROLE.items():
		if role in roles():
			keys |= allowed
	return keys


def _asset_version():
	"""A cache-busting version of the P1 assets.

	Frappe serves `/assets/<app>/...` with `Cache-Control: max-age=43200`,
	so a browser that already loaded a page keeps the **old** JavaScript for
	twelve hours. The version is the newest modification time of the asset
	files, so a new deployment invalidates the cache of the browser.
	"""
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


def build_context(
	context,
	route="",
	title=None,
	subtitle=None,
	allowed_roles=None,
	require_admin=False,
):
	"""Common context for every CSKH page.

	`allowed_roles` mirrors the role grant of the matching API. A page the
	actor may not use answers with the Frappe permission page (HTTP 403)
	instead of rendering a broken screen.
	"""
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
	context.csrf_token = frappe.sessions.get_csrf_token()
	# The CSRF header of the current session, published as a JSON object so
	# the browser sends exactly the same token on every mutation and the
	# direct-HTTP CI probe can read the token out of the rendered page.
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
		# The BOD viewer is an aggregated read-only role: it must not see the
		# phone number of a lead by default (P1 §8.1).
		"pii": not is_bod() or is_admin(),
	}
	# The stable business error codes of the frozen contract (P1 §10.1). The
	# browser uses them to show the right conflict / permission state.
	context.cskh_error_codes = list(p1_errors.ALL_CODES)
	# The translated label catalogue: the JavaScript never carries a hard-coded
	# user-facing string (P1 §9.6).
	context.cskh_i18n = p1_strings.translated_map()
	# The operator-facing text of every stable error code (P1 §10.1).
	context.cskh_error_texts = p1_strings.translated_error_texts()
	# The cache-busting version of the P1 assets (see `_asset_version`).
	context.cskh_asset_version = _asset_version()
	return context
