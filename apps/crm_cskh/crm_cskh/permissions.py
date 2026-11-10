import sys

import frappe

LEAD_LINKS = {
	"CRM Call Log": ("reference_doctype", "reference_docname"),
	"CRM Task": ("reference_doctype", "reference_docname"),
	"FCRM Note": ("reference_doctype", "reference_docname"),
	"Comment": ("reference_doctype", "reference_name"),
	"File": ("attached_to_doctype", "attached_to_name"),
	"ToDo": ("reference_type", "reference_name"),
}
_ORG_HIERARCHY_GUARD_INSTALLED = False


def _is_system(user):
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _manager_without_scope(user):
	return "Sales Manager" in frappe.get_roles(user) and not (
		frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy")
		and frappe.db.exists("CRM Sales Hierarchy", {"user": user})
	)


def _owner_scope_condition(user, table, owner_field):
	roles = set(frappe.get_roles(user))
	if not {"Sales User", "Sales Manager"}.intersection(roles):
		return "1=0"
	user_sql = frappe.db.escape(user, percent=False)
	if "Sales Manager" in roles:
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
	roles = set(frappe.get_roles(user))
	if not {"Sales User", "Sales Manager"}.intersection(roles):
		return False
	if "Sales Manager" in roles:
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
	roles = set(frappe.get_roles(user))
	if "Sales Manager" not in roles or _manager_without_scope(user):
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


def on_user_security_change(doc, method=None):
	"""Invalidate cached user roles/permissions on role change and purge sessions on user disable (`G11`)."""
	if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
		return
	user_name = getattr(doc, "name", None)
	if not user_name:
		return
	frappe.clear_cache(user=user_name)
	if user_name not in ("Administrator", "Guest") and not int(getattr(doc, "enabled", 1) or 0):
		from frappe.sessions import clear_sessions

		clear_sessions(user=user_name, force=True)


def _install_org_hierarchy_guard():
	"""Wrap upstream `crm.permissions.org_hierarchy.get_lead_permission_query_conditions` and
	`get_deal_permission_query_conditions` so unparenthesized `owner OR todo` SQL never precedence-hijacks
	`DatabaseQuery.get_permission_query_conditions()` (`A OR B and C`) and never grants `CRM Sales Hierarchy`
	subtree visibility after `Sales Manager` role removal (`G11`).
	"""
	global _ORG_HIERARCHY_GUARD_INSTALLED
	if _ORG_HIERARCHY_GUARD_INSTALLED:
		return
	try:
		from crm.permissions import org_hierarchy
	except Exception:
		return

	def _guarded_lead_permission_query_conditions(user=None, **kwargs):
		resolved_user = user or frappe.session.user
		if _is_system(resolved_user):
			return ""
		return f"({_owner_scope_condition(resolved_user, 'tabCRM Lead', 'lead_owner')})"

	def _guarded_deal_permission_query_conditions(user=None, **kwargs):
		resolved_user = user or frappe.session.user
		if _is_system(resolved_user):
			return ""
		return f"({_owner_scope_condition(resolved_user, 'tabCRM Deal', 'deal_owner')})"

	_guarded_lead_permission_query_conditions._cskh_guarded = True
	_guarded_deal_permission_query_conditions._cskh_guarded = True
	org_hierarchy.get_lead_permission_query_conditions = _guarded_lead_permission_query_conditions
	org_hierarchy.get_deal_permission_query_conditions = _guarded_deal_permission_query_conditions
	_ORG_HIERARCHY_GUARD_INSTALLED = True


def _normalize_caller_permission_conditions(cskh_cond: str) -> str:
	"""Ensure any prior hook condition in `DatabaseQuery.get_permission_query_conditions()` is parenthesized
	and restricted to `cskh_cond` even on the first call before `_install_org_hierarchy_guard()` ran.
	"""
	_install_org_hierarchy_guard()
	wrapped = f"({cskh_cond})" if cskh_cond else ""
	try:
		frame = sys._getframe(1)
		for _ in range(4):
			if frame is None:
				break
			if frame.f_code.co_name == "get_permission_query_conditions":
				conds = frame.f_locals.get("conditions")
				if isinstance(conds, list):
					for idx, item in enumerate(conds):
						if isinstance(item, str) and item.strip():
							conds[idx] = wrapped or f"({item})"
				break
			frame = frame.f_back
	except Exception:
		pass
	return wrapped


def lead_query_conditions(user=None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		_install_org_hierarchy_guard()
		return ""
	return _normalize_caller_permission_conditions(_owner_scope_condition(user, "tabCRM Lead", "lead_owner"))


def deal_query_conditions(user=None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		_install_org_hierarchy_guard()
		return ""
	return _normalize_caller_permission_conditions(_owner_scope_condition(user, "tabCRM Deal", "deal_owner"))


def linked_lead_query_conditions(user=None, doctype=None, **kwargs):
	user = user or frappe.session.user
	if _is_system(user):
		return ""
	if doctype == "CRM Task":
		from crm_cskh.p1_permissions import _check_active_db_query_oracles

		_check_active_db_query_oracles("CRM Task", user)

	link_doctype, link_name = LEAD_LINKS[doctype]
	parent_condition = _owner_scope_condition(user, "cskh_lead", "lead_owner")
	if parent_condition:
		parent_condition = f" AND ({parent_condition})"

	lead_exists_sql = (
		"EXISTS (SELECT 1 FROM `tabCRM Lead` `cskh_lead` "
		f"WHERE `cskh_lead`.`name` = `tab{doctype}`.`{link_name}` "
		f"AND `tab{doctype}`.`{link_doctype}` = 'CRM Lead'{parent_condition})"
	)
	if doctype == "File":
		from frappe.utils import now_datetime

		from crm_cskh.p1_permissions import ROLE_ADMIN
		from crm_cskh.p3_permissions import authorized_export_job_names_for_user
		from crm_cskh.services.p3_flags import is_p3_reporting_enabled

		if not is_p3_reporting_enabled():
			return lead_exists_sql
		if ROLE_ADMIN in frappe.get_roles(user):
			now_sql = frappe.db.escape(str(now_datetime()), percent=False)
			export_exists_sql = (
				"EXISTS (SELECT 1 FROM `tabCSKH Export Job` `cskh_exp` "
				"WHERE `cskh_exp`.`name` = `tabFile`.`attached_to_name` "
				"AND `tabFile`.`attached_to_doctype` = 'CSKH Export Job' "
				"AND `cskh_exp`.`status` = 'completed' "
				f"AND `cskh_exp`.`expires_at` > {now_sql})"
			)
			return f"({lead_exists_sql} OR {export_exists_sql})"

		valid_export_ids = authorized_export_job_names_for_user(user, completed_only=True)
		if not valid_export_ids:
			return lead_exists_sql
		in_sql = "(" + ", ".join(frappe.db.escape(jid, percent=False) for jid in valid_export_ids) + ")"
		export_exists_sql = (
			"(`tabFile`.`attached_to_doctype` = 'CSKH Export Job' "
			f"AND `tabFile`.`attached_to_name` IN {in_sql})"
		)
		return f"({lead_exists_sql} OR {export_exists_sql})"
	return lead_exists_sql


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
	if doc.doctype == "File" and doc.get("attached_to_doctype") == "CSKH Export Job":
		if ptype not in ("read", "select", "print"):
			return False
		from crm_cskh.p3_permissions import is_export_file_downloadable

		if not is_export_file_downloadable(doc.get("attached_to_name"), user=user):
			return False
		return None

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
				doc.has_value_changed(field) for field in ("allocated_to", "reference_type", "reference_name")
			)
		)
	):
		lead_owner = frappe.db.get_value("CRM Lead", doc.reference_name, "lead_owner")
		if doc.allocated_to != lead_owner:
			frappe.throw(
				"Lead assignment changes are disabled until the controlled ownership service is available.",
				frappe.PermissionError,
			)


@frappe.whitelist()
def remove_assignments(doctype, name, assignees, ignore_permissions=False):
	if not _is_system(frappe.session.user):
		frappe.throw("Lead assignment changes are not available for this role.", frappe.PermissionError)
	assignees = frappe.parse_json(assignees)
	result = frappe.get_attr("crm.api.doc.remove_assignments")(
		doctype, name, assignees, ignore_permissions=False
	)
	# CRM clears lead_owner through a direct DB write on unassign, which skips
	# the CRM Lead on_update hook. Revoke only shares belonging to the revoked
	# assignees in the same transaction so a stale grant cannot outlive access.
	if doctype == "CRM Lead" and isinstance(assignees, (list, tuple)) and assignees:
		frappe.db.delete(
			"DocShare",
			{
				"share_doctype": "CRM Lead",
				"share_name": name,
				"user": ["in", list(assignees)],
			},
		)
	return result
