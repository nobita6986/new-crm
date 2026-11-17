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

import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from crm_cskh import p1_strings, p2_strings, p3_strings
from crm_cskh.services import errors as p1_errors
from crm_cskh.services import runtime_cache

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
	{"key": "import", "route": "/cskh/import", "label": "Import list"},
	{"key": "pool", "route": "/cskh/pool", "label": "Customer pool"},
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


def is_p3_enabled(route=None):
	"""Feature flag controlling P3 quality/SLA screens for safe rollback (P3.md §15.3)."""
	from crm_cskh.services.p3_flags import is_p3_ui_enabled

	return is_p3_ui_enabled(route=route)


def is_qa_enabled(campaign=None):
	"""Feature flag controlling optional P3 QA review module (default False per P3 scope)."""
	from crm_cskh.services.p3_flags import is_qa_enabled as _resolve_qa_enabled

	return _resolve_qa_enabled(campaign=campaign)


def roles(user=None):
	return set(runtime_cache.get_roles_set(user or frappe.session.user))


def is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in runtime_cache.get_roles_set(user)


def is_admin(user=None):
	user = user or frappe.session.user
	return is_system(user) or ROLE_ADMIN in runtime_cache.get_roles_set(user)


def is_bod(user=None):
	user = user or frappe.session.user
	return is_system(user) or ROLE_BOD in runtime_cache.get_roles_set(user)


def is_leader(user=None):
	user = user or frappe.session.user
	if is_admin(user):
		return True
	return "Sales Manager" in runtime_cache.get_roles_set(user)


def is_staff(user=None):
	return "Sales User" in runtime_cache.get_roles_set(user or frappe.session.user)


def has_role_grant(grants, user=None):
	"""True when the actor holds one of the business roles of the grant."""
	user = user or frappe.session.user
	if is_system(user):
		return True
	return bool(runtime_cache.get_roles_set(user) & set(grants))


def _visible_keys():
	"""Navigation keys visible to the actor."""
	actor_roles = runtime_cache.get_roles_set(frappe.session.user)
	if is_system():
		keys = {item["key"] for item in NAV_ITEMS}
	else:
		keys = set()
		for role, allowed in NAV_BY_ROLE.items():
			if role in actor_roles:
				keys |= allowed
	if not is_p3_enabled():
		keys -= P3_NAV_KEYS
	return keys


def _asset_version():
	"""A cache-busting version of the CSKH static assets."""
	return runtime_cache.get_asset_version()


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
		if runtime_cache.is_runtime_db_cache_active():
			site_tz = runtime_cache.get_cached_system_timezone() or site_tz
		else:
			site_tz = frappe.utils.get_system_timezone() or site_tz
	except Exception:
		site_tz = "Asia/Ho_Chi_Minh"

	user_tz = ""
	if user and user != "Guest":
		try:
			user_tz = runtime_cache.get_user_auth_profile(user).get("time_zone") or ""
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


def _get_or_init_csrf_token() -> str:
	"""Return the session CSRF token, defending against first-touch concurrent tab updates on tabSessions."""
	sess_data = getattr(getattr(frappe.local, "session", None), "data", None)
	csrf_tok = getattr(sess_data, "csrf_token", None) if sess_data is not None else None
	if csrf_tok:
		return str(csrf_tok)

	sid = getattr(getattr(frappe, "session", None), "sid", None)
	for attempt in range(4):
		try:
			csrf_tok = frappe.sessions.get_csrf_token()
			if csrf_tok:
				return str(csrf_tok)
		except Exception:
			frappe.db.rollback()
			if sid:
				try:
					cached = frappe.cache.hget("session", sid)
					if isinstance(cached, dict):
						cdata = cached.get("data")
						if isinstance(cdata, dict) and cdata.get("csrf_token"):
							csrf_tok = str(cdata["csrf_token"])
							if sess_data is not None:
								sess_data.csrf_token = csrf_tok
							return csrf_tok
				except Exception:
					pass
				try:
					row = frappe.db.sql(
						"SELECT sessiondata FROM `tabSessions` WHERE sid = %s LIMIT 1",
						(sid,),
					)
					if row and row[0] and row[0][0]:
						parsed = json.loads(row[0][0])
						if isinstance(parsed, dict) and parsed.get("csrf_token"):
							csrf_tok = str(parsed["csrf_token"])
							if sess_data is not None:
								sess_data.csrf_token = csrf_tok
							return csrf_tok
				except Exception:
					frappe.db.rollback()
			time.sleep(0.02 * (attempt + 1))

	return str(frappe.sessions.get_csrf_token())


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
	runtime_cache.install_inprocess_translation_cache()
	frappe.local.lang = LANGUAGE
	if frappe.local.form_dict is not None:
		frappe.local.form_dict.lang = LANGUAGE
	context.lang = LANGUAGE
	if isinstance(getattr(context, "boot", None), dict):
		context.boot["lang"] = LANGUAGE

	actor = frappe.session.user
	if actor == "Guest":
		frappe.response["status_code"] = 403
		frappe.throw(_("Log in to access this page."), frappe.PermissionError)

	if require_p3 and not is_p3_enabled(route=route):
		frappe.response["status_code"] = 403
		frappe.throw(
			_("P3 quality and SLA screens are currently disabled by site configuration."),
			frappe.PermissionError,
		)

	admin_flag = is_admin(actor)
	if require_admin and not admin_flag:
		frappe.response["status_code"] = 403
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	if allowed_roles is not None and not has_role_grant(allowed_roles, user=actor):
		frappe.response["status_code"] = 403
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	p3_flag = is_p3_enabled()
	actor_roles = runtime_cache.get_roles_set(actor)
	sys_flag = actor == "Administrator" or "System Manager" in actor_roles
	if sys_flag:
		visible = {item["key"] for item in NAV_ITEMS}
	else:
		visible = set()
		for role, allowed in NAV_BY_ROLE.items():
			if role in actor_roles:
				visible |= allowed
	if not p3_flag:
		visible -= P3_NAV_KEYS

	nav_items = [item for item in NAV_ITEMS if item["key"] in visible]
	p3_subnav = [item for item in nav_items if item.get("phase") == "p3"]

	bod_flag = sys_flag or ROLE_BOD in actor_roles
	leader_flag = admin_flag or ("Sales Manager" in actor_roles)
	staff_flag = "Sales User" in actor_roles

	leader_root_team = ""
	if leader_flag and not admin_flag and actor and actor != "Guest":
		try:
			h_node = runtime_cache.get_user_hierarchy_node(actor)
			leader_root_team = str(h_node["name"]) if h_node else ""
		except Exception:
			leader_root_team = ""

	ui_bundle = runtime_cache.get_static_cskh_ui_bundle(LANGUAGE)

	context.no_cache = 1
	context.title = _(title) if title else _("CSKH")
	context.csrf_token = _get_or_init_csrf_token()
	context.cskh_csrf_header = {"X-Frappe-CSRF-Token": context.csrf_token}
	context.cskh_user = actor
	context.cskh_route = route
	context.cskh_page_title = title
	context.cskh_page_subtitle = subtitle
	context.cskh_navigation = nav_items
	context.cskh_p3_subnav = p3_subnav
	context.cskh_capabilities = {
		"admin": admin_flag,
		"bod": bod_flag,
		"leader": leader_flag,
		"staff": staff_flag,
		"leader_root_team": leader_root_team,
		"pii": not bod_flag or admin_flag,
		"can_manage_settings": admin_flag,
		"can_review_correction": admin_flag or leader_flag,
		"p3_enabled": p3_flag,
		"qa_enabled": is_qa_enabled(),
		"can_recompute_kpi": admin_flag or leader_flag,
		"can_waive_sla": admin_flag or leader_flag,
		"can_export_report": sys_flag or bool(actor_roles & READ_ROLES),
		"can_open_crm_app": sys_flag or bool(actor_roles & {"Sales Manager", "Sales User"}),
	}
	context.cskh_timezone = timezone_context(actor)
	context.cskh_error_codes = ui_bundle["error_codes"]
	context.cskh_i18n = ui_bundle["i18n"]
	context.cskh_error_texts = ui_bundle["error_texts"]
	context.cskh_error_codes_json = ui_bundle["error_codes_json"]
	context.cskh_i18n_json = ui_bundle["i18n_json"]
	context.cskh_error_texts_json = ui_bundle["error_texts_json"]
	context.cskh_asset_version = ui_bundle["asset_version"]
	sentinels = getattr(frappe.local, "cskh_ssr_skeleton_sentinels", None)
	if isinstance(sentinels, dict):
		context.csrf_token = sentinels["csrf_token"]
		context.cskh_csrf_header = {"X-Frappe-CSRF-Token": sentinels["csrf_token"]}
		context.cskh_csrf_header_json = sentinels["csrf_header_json"]
		context.cskh_timezone_json = sentinels["timezone_json"]
		if "user_html" in sentinels:
			context.cskh_user_html = sentinels["user_html"]
		if "user_json" in sentinels:
			context.cskh_user_json = sentinels["user_json"]
	return context
