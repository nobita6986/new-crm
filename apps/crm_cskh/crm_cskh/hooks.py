app_name = "crm_cskh"
app_title = "CRM CSKH"
app_publisher = "CRM-CSKH Project"
app_description = "CRM CSKH custom app (P0 scaffold)"
app_email = "noreply@example.com"
# Cần rà soát: crm_cskh là code tự phát triển chạy cùng Frappe CRM (AGPL-3.0).
# Chưa kết luận pháp lý về nghĩa vụ nguồn mở của bản triển khai; xem P0 §4.3 (B1).
app_license = "agpl-3.0"

# Fixtures
# ------------------
# Metadata do crm_cskh quản lý qua file (P0-B §B2).
fixtures = [
	{"doctype": "Custom Field", "filters": [["name", "=", "CRM Lead-cskh_poc_marker"]]},
	{"doctype": "CRM Form Script", "filters": [["name", "=", "CSKH - Open CSKH Page"]]},
]

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "crm_cskh",
# 		"logo": "/assets/crm_cskh/logo.png",
# 		"title": "CRM CSKH",
# 		"route": "/crm_cskh",
# 		"has_permission": "crm_cskh.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/crm_cskh/css/crm_cskh.css"
# app_include_js = "/assets/crm_cskh/js/crm_cskh.js"

# include js, css files in header of web template
# web_include_css = "/assets/crm_cskh/css/crm_cskh.css"
# web_include_js = "/assets/crm_cskh/js/crm_cskh.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "crm_cskh/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "crm_cskh/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "crm_cskh.utils.jinja_methods",
# 	"filters": "crm_cskh.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "crm_cskh.install.before_install"
# after_install = "crm_cskh.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "crm_cskh.uninstall.before_uninstall"
# after_uninstall = "crm_cskh.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "crm_cskh.utils.before_app_install"
# after_app_install = "crm_cskh.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "crm_cskh.utils.before_app_uninstall"
# after_app_uninstall = "crm_cskh.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "crm_cskh.build.after_build"

# To hook into the build process of other apps
# The list of apps being built is passed as an argument

# after_app_build = "crm_cskh.build.after_app_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "crm_cskh.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["crm_cskh.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"CRM Lead": "crm_cskh.permissions.lead_query_conditions",
	"CRM Deal": "crm_cskh.permissions.deal_query_conditions",
	"CRM Call Log": "crm_cskh.permissions.linked_lead_query_conditions",
	"CRM Task": "crm_cskh.permissions.linked_lead_query_conditions",
	"FCRM Note": "crm_cskh.permissions.linked_lead_query_conditions",
	"Comment": "crm_cskh.permissions.linked_lead_query_conditions",
	"File": "crm_cskh.permissions.linked_lead_query_conditions",
	"ToDo": "crm_cskh.permissions.linked_lead_query_conditions",
}

has_permission = {
	"CRM Lead": "crm_cskh.permissions.lead_has_permission",
	"CRM Deal": "crm_cskh.permissions.deal_has_permission",
	"CRM Call Log": "crm_cskh.permissions.linked_lead_has_permission",
	"CRM Task": "crm_cskh.permissions.linked_lead_has_permission",
	"FCRM Note": "crm_cskh.permissions.linked_lead_has_permission",
	"Comment": "crm_cskh.permissions.linked_lead_has_permission",
	"File": "crm_cskh.permissions.linked_lead_has_permission",
	"ToDo": "crm_cskh.permissions.linked_lead_has_permission",
}

doc_events = {
	"CRM Lead": {
		"validate": "crm_cskh.permissions.validate_lead_owner",
		"on_update": "crm_cskh.permissions.clear_stale_lead_shares",
	},
	"ToDo": {"validate": "crm_cskh.permissions.validate_lead_assignment"},
}

override_whitelisted_methods = {
	"frappe.core.doctype.data_export.exporter.export_data": "crm_cskh.api.guarded_reporting.data_export",
	"frappe.desk.reportview.export_query": "crm_cskh.api.guarded_reporting.reportview_export_query",
	"frappe.desk.query_report.run": "crm_cskh.api.guarded_reporting.query_report_run",
	"frappe.desk.query_report.export_query": "crm_cskh.api.guarded_reporting.query_report_export_query",
	"crm.api.dashboard.get_dashboard": "crm_cskh.api.guarded_reporting.crm_manager_dashboard",
	"crm.api.dashboard.get_chart": "crm_cskh.api.guarded_reporting.crm_manager_chart",
	"crm.integrations.api.get_contact_by_phone_number": "crm_cskh.api.guarded_crm.get_contact_by_phone_number",
	"crm.integrations.api.get_contact_lead_or_deal_from_number": "crm_cskh.api.guarded_crm.get_contact_lead_or_deal_from_number",
	"crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log": "crm_cskh.api.guarded_crm.get_call_log",
	"crm.api.doc.remove_assignments": "crm_cskh.permissions.remove_assignments",
	"crm.api.doc.delete_bulk_docs": "crm_cskh.api.guarded_crm.delete_bulk_docs",
	"crm.api.doc.get_assigned_users": "crm_cskh.api.guarded_crm.get_assigned_users",
	"frappe.desk.form.assign_to.add": "crm_cskh.api.guarded_crm.add_assignment",
	"crm.api.doc.get_linked_docs_of_document": "crm_cskh.api.guarded_crm.get_linked_docs_of_document",
	"crm.api.activities.get_linked_calls": "crm_cskh.api.guarded_crm.get_linked_calls",
	"crm.api.activities.get_linked_notes": "crm_cskh.api.guarded_crm.get_linked_notes",
	"crm.api.activities.get_linked_tasks": "crm_cskh.api.guarded_crm.get_linked_tasks",
	"crm.api.activities.get_attachments": "crm_cskh.api.guarded_crm.get_attachments",
	"crm.api.activities.get_activities": "crm_cskh.api.guarded_crm.get_activities",
}

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"crm_cskh.tasks.all"
# 	],
# 	"daily": [
# 		"crm_cskh.tasks.daily"
# 	],
# 	"hourly": [
# 		"crm_cskh.tasks.hourly"
# 	],
# 	"weekly": [
# 		"crm_cskh.tasks.weekly"
# 	],
# 	"monthly": [
# 		"crm_cskh.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "crm_cskh.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "crm_cskh.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "crm_cskh.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
before_request = ["crm_cskh.i18n.set_guest_language"]
update_website_context = ["crm_cskh.i18n.update_website_context"]
# after_request = ["crm_cskh.utils.after_request"]

# Job Events
# ----------
# before_job = ["crm_cskh.utils.before_job"]
# after_job = ["crm_cskh.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"crm_cskh.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
