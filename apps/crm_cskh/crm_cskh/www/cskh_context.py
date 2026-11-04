"""Shared helpers for the CSKH pages (same Frappe site/session as CRM).

The pages are rendered by the Frappe web engine of the SAME site and session
as CRM (ADR-UI-001 P0, ADR-P1-06 P1, ADR-P2-01 P2, ADR-P3-09 P3). There is no
second SPA and no second backend: every list/report comes from the whitelisted
P1/P2/P3 APIs and every mutation is a POST with the Frappe CSRF token.

The navigation is a **convenience only**. Hiding a link never replaces the
server-side permission check: each API resolves the actor's scope itself.

The page gate mirrors the role grant of the matching API (P1 §8.1, P2 §11.1,
P3 §10 & §12.1). It is never *stricter* than the API, otherwise the UI would
contradict the contract, and never *looser*: a deep link to a page the actor
may not use answers with the permission-denied page instead of a crash.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from crm_cskh import p1_strings, p2_strings, p3_strings
from crm_cskh.services import errors as p1_errors

ROLE_ADMIN = "CSKH CRM Admin"
ROLE_BOD = "CSKH BOD Viewer"

# The language of the CSKH screens (P1 §9.6, P2 §12.5, P3 §0.15).
LANGUAGE = "vi"

# Role grants mirroring backend APIs (P1 §8.1, P2 §10 & §11.1, P3 §10 & §12.1).
READ_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN, ROLE_BOD}
WORKFLOW_PII_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN}
POOL_ROLES = {"Sales Manager", ROLE_ADMIN, ROLE_BOD}
DASHBOARD_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
IMPORT_ROLES = {ROLE_ADMIN}
CAMPAIGN_READ_ROLES = {ROLE_ADMIN, ROLE_BOD, "Sales Manager"}
SETTINGS_READ_ROLES = {ROLE_ADMIN, ROLE_BOD, "Sales Manager"}

# P3 quality, SLA & scorecard page role grants (docs/p3/report-api-contract.md §2).
P3_EXECUTIVE_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
P3_TEAM_QUALITY_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
P3_EMPLOYEE_QUALITY_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
P3_CAMPAIGN_QUALITY_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
P3_SLA_OPS_ROLES = {ROLE_BOD, ROLE_ADMIN, "Sales Manager"}
P3_SCORECARD_ROLES = {"Sales User", "Sales Manager", ROLE_ADMIN}

P3_NAV_KEYS = {
	"quality-overview",
	"team-quality",
	"employee-quality",
	"campaign-quality",
	"sla-operations",
	"scorecard",
}

# Navigation entries per role. Keys are English message ids translated via Frappe i18n.
NAV_ITEMS = (
	{"key": "pool", "route": "/cskh/pool", "label": "Customer pool"},
	{"key": "import", "route": "/cskh/import", "label": "Import list"},
	{"key": "campaigns", "route": "/cskh/campaigns", "label": "Campaigns and rules"},
	{"key": "my-customers", "route": "/cskh/my-customers", "label": "My customers"},
	{"key": "callbacks", "route": "/cskh/callbacks", "label": "Callbacks schedule"},
	{"key": "progress", "route": "/cskh/progress", "label": "Care progress"},
	{"key": "dashboard", "route": "/cskh/dashboard", "label": "Distribution dashboard"},
	{
		"key": "quality-overview",
		"route": "/cskh/quality-overview",
		"label": "Quality overview",
		"phase": "p3",
	},
	{"key": "team-quality", "route": "/cskh/team-quality", "label": "Team comparison", "phase": "p3"},
	{
		"key": "employee-quality",
		"route": "/cskh/employee-quality",
		"label": "Employee quality",
		"phase": "p3",
	},
	{
		"key": "campaign-quality",
		"route": "/cskh/campaign-quality",
		"label": "Campaign & source quality",
		"phase": "p3",
	},
	{"key": "sla-operations", "route": "/cskh/sla-operations", "label": "SLA & QA operations", "phase": "p3"},
	{"key": "scorecard", "route": "/cskh/scorecard", "label": "Personal scorecard", "phase": "p3"},
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
		"quality-overview",
		"team-quality",
		"employee-quality",
		"campaign-quality",
		"sla-operations",
		"scorecard",
		"settings",
	},
	ROLE_BOD: {
		"pool",
		"campaigns",
		"my-customers",
		"progress",
		"dashboard",
		"quality-overview",
		"team-quality",
		"employee-quality",
		"campaign-quality",
		"sla-operations",
		"settings",
	},
	"Sales Manager": {
		"pool",
		"campaigns",
		"my-customers",
		"callbacks",
		"progress",
		"dashboard",
		"quality-overview",
		"team-quality",
		"employee-quality",
		"campaign-quality",
		"sla-operations",
		"scorecard",
		"settings",
	},
	"Sales User": {"my-customers", "callbacks", "progress", "scorecard"},
}


def _flag_bool(value, default):
	if value is None:
		return default
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return value != 0
	text = str(value).strip().lower()
	if text in ("1", "true", "yes", "on"):
		return True
	if text in ("0", "false", "no", "off"):
		return False
	return default


def is_p3_enabled():
	"""Feature flag controlling P3 quality/SLA screens for safe rollback (P3.md §15.3)."""
	conf = getattr(frappe, "conf", None) or {}
	for key in ("cskh_p3_enabled", "p3_sla_enabled", "p3_enabled"):
		if key in conf and conf.get(key) is not None:
			return _flag_bool(conf.get(key), True)
	return True


def is_qa_enabled():
	"""Feature flag controlling optional P3 QA review module (default False per P3 scope)."""
	conf = getattr(frappe, "conf", None) or {}
	for key in ("cskh_qa_enabled", "qa_enabled"):
		if key in conf and conf.get(key) is not None:
			return _flag_bool(conf.get(key), False)
	return False


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
		keys = {item["key"] for item in NAV_ITEMS}
	else:
		keys = set()
		for role, allowed in NAV_BY_ROLE.items():
			if role in roles():
				keys |= allowed
	if not is_p3_enabled():
		keys -= P3_NAV_KEYS
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
	"""Resolve site/user timezone and authoritative server timestamp (P2 §12.5, P3 §4.1).

	Never relies on browser host OS timezone: publishes both `site` timezone
	(used to interpret Frappe naive datetimes and `[from, to_exclusive)` periods)
	and `effective` user/site timezone (used to render timestamps and compute
	23:59 / 00:00 boundaries).
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
	require_p3=False,
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

	if require_p3 and not is_p3_enabled():
		frappe.response["status_code"] = 403
		frappe.throw(
			_("P3 quality and SLA screens are currently disabled by site configuration."),
			frappe.PermissionError,
		)

	if require_admin and not is_admin():
		frappe.response["status_code"] = 403
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	if allowed_roles is not None and not has_role_grant(allowed_roles):
		frappe.response["status_code"] = 403
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	visible = _visible_keys()
	nav_items = [item for item in NAV_ITEMS if item["key"] in visible]
	p3_subnav = [item for item in nav_items if item.get("phase") == "p3"]

	leader_root_team = ""
	if is_leader() and not is_admin() and frappe.session.user and frappe.session.user != "Guest":
		try:
			leader_root_team = (
				frappe.db.get_value("CRM Sales Hierarchy", {"user": frappe.session.user}, "name") or ""
			)
		except Exception:
			leader_root_team = ""

	context.no_cache = 1
	context.title = _(title) if title else _("CSKH")
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.cskh_csrf_header = {"X-Frappe-CSRF-Token": context.csrf_token}
	context.cskh_user = frappe.session.user
	context.cskh_route = route
	context.cskh_page_title = title
	context.cskh_page_subtitle = subtitle
	context.cskh_navigation = nav_items
	context.cskh_p3_subnav = p3_subnav
	context.cskh_capabilities = {
		"admin": is_admin(),
		"bod": is_bod(),
		"leader": is_leader(),
		"staff": is_staff(),
		"leader_root_team": leader_root_team,
		"pii": not is_bod() or is_admin(),
		"can_manage_settings": is_admin(),
		"can_review_correction": is_admin() or is_leader(),
		"p3_enabled": is_p3_enabled(),
		"qa_enabled": is_qa_enabled(),
		"can_recompute_kpi": is_admin() or is_leader(),
		"can_waive_sla": is_admin() or is_leader(),
		"can_export_report": has_role_grant(READ_ROLES),
	}
	context.cskh_timezone = timezone_context(frappe.session.user)
	context.cskh_error_codes = (
		list(p1_errors.ALL_CODES) + list(p2_strings.P2_ERROR_CODES) + list(p3_strings.P3_ERROR_CODES)
	)
	context.cskh_i18n = p1_strings.translated_map()
	context.cskh_error_texts = p1_strings.translated_error_texts()
	context.cskh_asset_version = _asset_version()
	return context
