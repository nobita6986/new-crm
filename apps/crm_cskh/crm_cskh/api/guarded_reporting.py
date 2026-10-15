import frappe

from crm_cskh.permissions import _is_system


def _require_system_manager():
	if not _is_system(frappe.session.user):
		frappe.throw(
			"Reporting and export are disabled until record-scoped reporting is available.",
			frappe.PermissionError,
		)


@frappe.whitelist()
def reportview_export_query():
	_require_system_manager()
	return frappe.get_attr("frappe.desk.reportview.export_query")()


@frappe.whitelist()
def data_export(
	doctype: str | list | None = None,
	parent_doctype: str | None = None,
	all_doctypes: bool | int | str = True,
	with_data: bool | int | str = False,
	select_columns: str | dict | None = None,
	file_type: str = "CSV",
	template: bool | str = False,
	filters: str | dict | list | None = None,
	export_without_column_meta: bool | str = False,
):
	_require_system_manager()
	return frappe.get_attr("frappe.core.doctype.data_export.exporter.export_data")(
		doctype,
		parent_doctype,
		all_doctypes,
		with_data,
		select_columns,
		file_type,
		template,
		filters,
		export_without_column_meta,
	)


@frappe.whitelist()
@frappe.read_only()
def query_report_run(
	report_name: str,
	filters: str | dict | None = None,
	user: str | None = None,
	ignore_prepared_report: bool = False,
	custom_columns: str | list | None = None,
	is_tree: bool = False,
	parent_field: str | None = None,
	are_default_filters: bool = True,
):
	_require_system_manager()
	return frappe.get_attr("frappe.desk.query_report.run")(
		report_name,
		filters,
		user,
		ignore_prepared_report,
		custom_columns,
		is_tree,
		parent_field,
		are_default_filters,
	)


@frappe.whitelist()
def query_report_export_query():
	_require_system_manager()
	return frappe.get_attr("frappe.desk.query_report.export_query")()


@frappe.whitelist()
@frappe.read_only()
def crm_manager_dashboard(from_date: str | None = None, to_date: str | None = None, user: str | None = None):
	if "Sales Manager" in frappe.get_roles() and not _is_system(frappe.session.user):
		frappe.throw(
			"Manager dashboards are disabled until team-scoped reporting is available.",
			frappe.PermissionError,
		)
	return frappe.get_attr("crm.api.dashboard.get_dashboard")(from_date, to_date, user)


@frappe.whitelist()
@frappe.read_only()
def crm_manager_chart(
	name: str,
	type: str,
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
):
	if "Sales Manager" in frappe.get_roles() and not _is_system(frappe.session.user):
		frappe.throw(
			"Manager dashboards are disabled until team-scoped reporting is available.",
			frappe.PermissionError,
		)
	return frappe.get_attr("crm.api.dashboard.get_chart")(name, type, from_date, to_date, user)
