"""Project language defaults for requests served by crm_cskh."""

import frappe


def set_guest_language():
	"""Use the site's configured default for guests unless they chose a language.

	Frappe prioritizes ``Accept-Language`` over the site's default for guests.
	For this Vietnamese DEV site, use the configured default so the login page
	and other guest-rendered pages follow the selected product language. An
	explicit ``_lang`` parameter or ``preferred_language`` cookie still wins.
	Authenticated users continue to use their own language preference.
	"""
	if frappe.session.user != "Guest":
		return

	if frappe.form_dict.get("_lang") or frappe.request.cookies.get("preferred_language"):
		return

	default_language = frappe.local.conf.get("default_language")
	if default_language:
		frappe.local.lang = default_language


def update_website_context(context):
	"""Translate the login document title, which Frappe sets as a raw string."""
	if frappe.local.lang == "vi" and context.get("title") == "Login":
		return {"title": frappe._("Login")}
	return {}
