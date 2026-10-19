import frappe

from crm_cskh.permissions import LEAD_LINKS, _is_system, user_in_scope


def _check_read(doctype, name):
	if not frappe.has_permission(doctype, "read", name, throw=False):
		frappe.throw("Not permitted", frappe.PermissionError)


def _require_system_phone_lookup():
	if not _is_system(frappe.session.user):
		frappe.throw(
			"Phone lookup is disabled until it can be restricted to the user's scope.", frappe.PermissionError
		)


@frappe.whitelist()
def get_contact_by_phone_number(phone_number):
	_require_system_phone_lookup()
	return frappe.get_attr("crm.integrations.api.get_contact_by_phone_number")(phone_number)


@frappe.whitelist()
def get_contact_lead_or_deal_from_number(number):
	_require_system_phone_lookup()
	return frappe.get_attr("crm.integrations.api.get_contact_lead_or_deal_from_number")(number)


@frappe.whitelist()
def get_assigned_users(doctype, name, default_assigned_to=None):
	_check_read(doctype, name)
	users = frappe.get_attr("crm.api.doc.get_assigned_users")(doctype, name, default_assigned_to)
	return [user for user in users if user_in_scope(frappe.session.user, user)]


@frappe.whitelist()
def get_linked_docs_of_document(doctype, docname):
	_check_read(doctype, docname)
	docs = frappe.get_attr("crm.api.doc.get_linked_docs_of_document")(doctype, docname)
	return [
		linked
		for linked in docs
		if frappe.has_permission(linked["doc"], "read", linked["reference_docname"], throw=False)
	]


@frappe.whitelist()
def get_linked_calls(name):
	_check_read("CRM Lead", name)
	return frappe.get_attr("crm.api.activities.get_linked_calls")(name)


@frappe.whitelist()
def get_linked_notes(name):
	_check_read("CRM Lead", name)
	return frappe.get_attr("crm.api.activities.get_linked_notes")(name)


@frappe.whitelist()
def get_linked_tasks(name):
	_check_read("CRM Lead", name)
	return frappe.get_attr("crm.api.activities.get_linked_tasks")(name)


@frappe.whitelist()
def get_attachments(doctype, name):
	_check_read(doctype, name)
	return frappe.get_attr("crm.api.activities.get_attachments")(doctype, name)


@frappe.whitelist()
def delete_bulk_docs(doctype: str, items: str | list, delete_linked: bool = False):
	items = frappe.parse_json(items)
	if doctype in ("CRM Lead", "CRM Deal", *LEAD_LINKS):
		for name in items:
			frappe.has_permission(doctype, "delete", name, throw=True)
	return frappe.get_attr("crm.api.doc.delete_bulk_docs")(doctype, items, delete_linked)
