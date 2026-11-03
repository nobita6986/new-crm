"""Context for the CSKH import page.

The page mirrors the role grant of `crm_cskh.api.imports.*` (P1 §8.1): only
the CRM Admin business role may import a customer list. The page gate and the
API agree, so the operator never sees a screen that could not work.
"""

from frappe import _

from crm_cskh.www.cskh_context import IMPORT_ROLES, build_context


def get_context(context):
	return build_context(
		context,
		route="/cskh/import",
		title=_("Import list"),
		subtitle=_("Upload a private file, map the phone column, preview, then confirm."),
		allowed_roles=IMPORT_ROLES,
	)
