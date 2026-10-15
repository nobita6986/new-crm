import frappe

LEAD_LINKS = {
	"CRM Call Log": ("reference_doctype", "reference_docname"),
	"CRM Task": ("reference_doctype", "reference_docname"),
	"FCRM Note": ("reference_doctype", "reference_docname"),
	"Comment": ("reference_doctype", "reference_name"),
	"File": ("attached_to_doctype", "attached_to_name"),
	"ToDo": ("reference_type", "reference_name"),
}


def _is_system(user):
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _manager_without_scope(user):
	return "Sales Manager" in frappe.get_roles(user) and not (
		frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		and frappe.db.exists("CRM Sales Hierarchy", {"user": user})
	)


def _owner_scope_condition(user, table, owner_field):
	roles = frappe.get_roles(user)
	if not {"Sales User", "Sales Manager"}.intersection(roles):
		return "1=0"
	user_sql = frappe.db.escape(user, percent=False)
	manager = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"])
	if manager and frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy"):
		return (
			f"`{table}`.`{owner_field}` IN (SELECT `member`.`user` FROM `tabCRM Sales Hierarchy` `root` "
			"JOIN `tabCRM Sales Hierarchy` `member` ON `member`.`lft` >= `root`.`lft` "
			f"AND `member`.`rgt` <= `root`.`rgt` WHERE `root`.`user` = {user_sql})"
		)
	return f"`{table}`.`{owner_field}` = {user_sql}"


def _user_owns_in_scope(user, owner):
	if not owner:
		return False
	roles = frappe.get_roles(user)
	if not {"Sales User", "Sales Manager"}.intersection(roles):
		return False
	manager = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"])
	if manager and frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy"):
		return bool(
			frappe.db.exists(
				"CRM Sales Hierarchy",
				{"user": owner, "lft": [">=", manager[0]], "rgt": ["<=", manager[1]]},
			)
		)
	return owner == user


def user_in_scope(user, target):
	if _is_system(user) or user == target:
		return True
	if _manager_without_scope(user):
		return False
	manager = frappe.db.get_value("CRM Sales Hierarchy", {"user": user}, ["lft", "rgt"])
	member = frappe.db.get_value("CRM Sales Hierarchy", {"user": target}, ["lft", "rgt"])
	return bool(
		manager
		and member
		and frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		and manager[0] <= member[0]
		and member[1] <= manager[1]
	)


def lead_query_conditions(user=None, **kwargs):
	user = user or frappe.session.user
	return _owner_scope_condition(user, "tabCRM Lead", "lead_owner")


def deal_query_conditions(user=None, **kwargs):
	user = user or frappe.session.user
	return _owner_scope_condition(user, "tabCRM Deal", "deal_owner")


def linked_lead_query_conditions(user=None, doctype=None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		return ""

	link_doctype, link_name = LEAD_LINKS[doctype]
	parent_condition = _owner_scope_condition(user, "cskh_lead", "lead_owner")
	if parent_condition:
		parent_condition = f" AND ({parent_condition})"

	return (
		"EXISTS (SELECT 1 FROM `tabCRM Lead` `cskh_lead` "
		f"WHERE `cskh_lead`.`name` = `tab{doctype}`.`{link_name}` "
		f"AND `tab{doctype}`.`{link_doctype}` = 'CRM Lead'{parent_condition})"
	)


def lead_has_permission(doc, ptype, user, **kwargs):
	if _is_system(user):
		return None
	if ptype in ("create", "write", "delete", "share", "import", "export"):
		return False
	if ptype in ("read", "select", "email", "print"):
		return _user_owns_in_scope(user, doc.get("lead_owner"))
	return False


def deal_has_permission(doc, ptype, user, **kwargs):
	if _is_system(user):
		return None
	if _manager_without_scope(user):
		return False
	if ptype in ("read", "select", "email", "print"):
		return _user_owns_in_scope(user, doc.get("deal_owner"))
	return False


def linked_lead_has_permission(doc, ptype, user, **kwargs):
	if _is_system(user):
		return None

	link_doctype, link_name = LEAD_LINKS[doc.doctype]
	if doc.get(link_doctype) != "CRM Lead" or not doc.get(link_name):
		return False
	if ptype not in ("read", "select", "email", "print"):
		return False
	if not frappe.has_permission("CRM Lead", "read", doc=doc.get(link_name), user=user, throw=False):
		return False
	return None


def validate_lead_owner(doc, method=None):
	if not doc.is_new() and doc.has_value_changed("lead_owner") and not _is_system(frappe.session.user):
		frappe.throw(
			"Only a System Manager can change Lead ownership until the ownership service is available.",
			frappe.PermissionError,
		)


def clear_stale_lead_shares(doc, method=None):
	if doc.has_value_changed("lead_owner"):
		frappe.db.delete("DocShare", {"share_doctype": doc.doctype, "share_name": doc.name})


def validate_lead_assignment(doc, method=None):
	if (
		doc.reference_type == "CRM Lead"
		and doc.reference_name
		and not _is_system(frappe.session.user)
		and (
			doc.is_new()
			or any(
				doc.has_value_changed(field)
				for field in ("allocated_to", "reference_type", "reference_name", "status")
			)
		)
	):
		frappe.throw(
			"Lead assignments are disabled until the ownership service is available.", frappe.PermissionError
		)


@frappe.whitelist()
def remove_assignments(doctype, name, assignees, ignore_permissions=False):
	if not _is_system(frappe.session.user):
		frappe.throw("Lead assignment changes are not available for this role.", frappe.PermissionError)
	return frappe.get_attr("crm.api.doc.remove_assignments")(
		doctype, name, assignees, ignore_permissions=False
	)
