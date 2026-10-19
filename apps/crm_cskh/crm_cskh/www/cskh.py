import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.response["status_code"] = 403
		frappe.throw(_("Log in to access this page."), frappe.PermissionError)
	if not frappe.has_permission("CRM Lead", "read", user=frappe.session.user, throw=False):
		frappe.throw(_("You do not have permission to access this page."), frappe.PermissionError)

	context.no_cache = 1
	context.title = _("CSKH")
	context.current_user = frappe.session.user
	context.csrf_token = frappe.sessions.get_csrf_token()
