"""Vietnamese labels of the P1 UI (P1 §9.6, `docs/architecture/glossary-vi.md`).

Every user-visible string of the P1 pages is declared here as an English
message id. The English id is translated through the normal Frappe pipeline
(`frappe._()` + `crm_cskh/translations/vi.csv`), so the browser receives the
already translated Vietnamese text and the pages never hard-code a label.

The ids are shared by the Jinja templates (via `_()`) and by the JavaScript
(the `window.cskhI18n` map), so a label has exactly one source of truth.
"""

import frappe

# --- Shared shell ------------------------------------------------------------
SHELL = (
	"CSKH",
	"Customer pool",
	"Import list",
	"Campaigns and rules",
	"My customers",
	"Distribution dashboard",
	"Signed in as",
	"This page uses the same Frappe site session as CRM.",
	"Check session and CSRF",
	"Request denied",
	"Session verified for",
	"Return to CRM Leads",
	"Loading...",
	"Refresh",
	"Apply filters",
	"Previous",
	"Next",
	"All",
	"Close",
	"Cancel",
	"Confirm",
	"Save",
	"Search",
	"No data",
	"Error",
)

# --- Page titles and subtitles ----------------------------------------------
TITLES = (
	"Customer pool",
	"Import list",
	"Campaigns and distribution rules",
	"Customers assigned to me",
	"Distribution dashboard",
	"Leads in your scope, with the derived distribution state.",
	"Upload a private file, map the phone column, preview, then confirm.",
	"Set up the target set and the weights, then preview before distributing.",
	"Only the customers that are currently assigned to you.",
	"Total pool, assigned, unassigned, per team, per user and per campaign.",
	"Customer pool, imports, campaigns and distribution.",
	"Customers assigned to me.",
	"Customers currently assigned to you.",
	"Operational distribution figures for your scope.",
	"Current owners are counted from work items; assignment events come from the audit trail.",
	"Log in to access this page.",
	"You do not have permission to access this page.",
)

# --- Error messages of the frozen backend contract ---------------------------
# The API answers with these English messages; the browser shows them, so they
# have to be part of the Vietnamese catalogue as well.
BACKEND_MESSAGES = (
	"A queued, revoked or closed work item must not have an assignee.",
	"A revocation event must not have a target user.",
	"An assigned work item requires an assignee.",
	"An assigned work item requires an assignment timestamp.",
	"An assignment event requires a target user.",
	"Assignment history is append-only; use a compensating event.",
	"Column mapping must be a JSON object.",
	"Import counters must not be negative.",
	"Import source files must be private.",
	"Only an active work item may hold an active claim.",
	"Resolve the active work items before closing the campaign; nothing is released implicitly.",
	"The active claim must reference the same lead and campaign as its work item.",
	"The actual payload must be valid JSON.",
	"The allocator state must be a JSON object.",
	"The allocator state must be valid JSON.",
	"The assignee must be an enabled Sales User.",
	"The canonical lead must have a mobile number.",
	"The distribution run",
	"The end date must not precede the start date.",
	"The event id is required so the event stays idempotent.",
	"The import batch",
	"The phone identity must be a canonical Vietnamese mobile number.",
	"The phone identity must match the canonical lead mobile number.",
	"The preview payload must be valid JSON.",
	"The rule version must be positive.",
	"The selection fingerprint is required.",
	"The selection must be a JSON object.",
	"This campaign, assignee and version already has a rule.",
	"Weight and version must be positive; capacity must not be negative.",
	"{0} cannot move from {1} to {2}.",
)

# --- Customer pool -----------------------------------------------------------
POOL = (
	"Distribution state",
	"Not distributed",
	"Assigned",
	"Queued",
	"Revoked",
	"Closed",
	"Conflict",
	"Campaign",
	"Customer",
	"Phone number",
	"Source",
	"Owner",
	"Created at",
	"Name or phone number",
	"No customers match the current filters.",
	"Work item",
	"Open in CRM",
	"Scope",
	"Unique customers",
	"Work items",
)

# --- Import ------------------------------------------------------------------
IMPORT = (
	"1. Upload a private file",
	"2. Preview and confirm",
	"3. Follow the progress",
	"CSV or XLSX, up to 10 MB. Keep the phone column as text so the leading zero is not lost.",
	"Preview",
	"Confirm import",
	"Start over",
	"Import history",
	"File",
	"Column mapping",
	"Phone column",
	"Total rows",
	"Valid rows",
	"New rows",
	"Duplicate rows",
	"Error rows",
	"Row",
	"Classification",
	"Reason",
	"Status",
	"Batch",
	"Committed at",
	"No import batch yet.",
	"The phone column was not found. Map the column that holds the phone number.",
	"The file must be CSV or XLSX.",
	"The upload failed.",
	"Preview is ready. Review the numbers before confirming.",
)

# --- Campaigns and rules -----------------------------------------------------
CAMPAIGNS = (
	"Campaigns",
	"Assignment rules",
	"Distribution",
	"Distribution runs",
	"Code",
	"Title",
	"Description",
	"Status",
	"Started at",
	"Ended at",
	"Rule version",
	"Team scope",
	"Create campaign",
	"New campaign",
	"Assignee",
	"Team",
	"Weight",
	"Capacity",
	# A capacity limit of zero means no limit -- see P1 7.2 and ADR-P1-04.
	"Unlimited",
	"Normalized share",
	"Remaining capacity",
	"Eligibility",
	"Eligible",
	"Excluded",
	"Preview distribution",
	"Confirm distribution",
	"Preview first. Executing is a separate, explicitly confirmed action.",
	"Preview ID",
	"Selection fingerprint",
	"Expires at",
	"Eligible customers",
	"Excluded customers",
	"Planned",
	"Assigned now",
	"Assigned in this run",
	"Failed",
	"No campaign yet.",
	"No assignment rule yet.",
	"The preview has expired. Run the preview again.",
	"The rules changed after the preview. Run the preview again.",
	"The distribution state changed after the preview. Run the preview again.",
	"The campaign is already distributed by another executor.",
	"Distribution completed.",
	"Distribution completed with errors.",
	"Run ID",
	"Created",
	"Errors",
)

# --- My customers ------------------------------------------------------------
MY_CUSTOMERS = (
	"Display name",
	"Assigned at",
	"Work item status",
	"You have not been assigned any customer yet.",
	"Search by name or phone number.",
)

# --- Dashboard ---------------------------------------------------------------
DASHBOARD = (
	"Unique customers in scope",
	"Eligible in the pool",
	"Assigned work items",
	"Unassigned or conflicted work items",
	"Active campaigns",
	"Failed batches",
	"Assignment events",
	"Current owners",
	"Per assignee",
	"Share",
	"Currently assigned",
	"Assigned in the run",
	"Remaining capacity",
	"Unlimited",
	"No data in the current scope.",
	"Per campaign",
	"Data as of",
)

# --- Error codes of the frozen API contract (P1 §10.1) ----------------------
ERROR_CODES = (
	"INVALID_PHONE",
	"DUPLICATE",
	"ALREADY_ASSIGNED",
	"RULE_CHANGED",
	"CAPACITY_EXCEEDED",
	"CONFLICT",
	"FORBIDDEN",
	"PARTIAL_SUCCESS",
	"RETRYABLE_FAILURE",
	"UNSUPPORTED_FORMAT",
	"FILE_TOO_LARGE",
	"TOO_MANY_ROWS",
	"INVALID_REQUEST",
	"NOT_FOUND",
	"PREVIEW_EXPIRED",
	"NO_ELIGIBLE_LEADS",
	"CAPACITY_FULL",
)

# --- Operator-facing explanation of each error code -------------------------
# The backend answers with a stable code and an English technical message.
# The page shows the **Vietnamese** explanation of the situation, because the
# operator has to act on it (P1 §9.6).
ERROR_EXPLANATIONS = {
	"INVALID_PHONE": "The phone number is not a valid Vietnamese mobile number.",
	"DUPLICATE": "The customer already exists in the CRM.",
	"ALREADY_ASSIGNED": "The customer is already assigned in another campaign.",
	"RULE_CHANGED": "The assignment rules changed after the preview. Run the preview again.",
	"CAPACITY_EXCEEDED": "The assignment exceeds the capacity of the assignee.",
	"CONFLICT": "The operation conflicts with the current state of the data.",
	"FORBIDDEN": "You do not have permission to perform this operation.",
	"PARTIAL_SUCCESS": "The operation completed with some failures. Review the failed rows.",
	"RETRYABLE_FAILURE": "The operation failed temporarily. Try again.",
	"UNSUPPORTED_FORMAT": "The file format is not supported.",
	"FILE_TOO_LARGE": "The file is larger than the allowed limit.",
	"TOO_MANY_ROWS": "The file has more rows than the allowed limit.",
	"INVALID_REQUEST": "The request is invalid.",
	"NOT_FOUND": "The requested data does not exist.",
	"PREVIEW_EXPIRED": "The preview has expired. Run the preview again.",
	"NO_ELIGIBLE_LEADS": "No customer is eligible for this campaign.",
	"CAPACITY_FULL": "The capacity of the assignee is exhausted.",
}

# --- Eligibility reasons of the frozen backend contract ----------------------
ELIGIBILITY_REASONS = (
	"no_canonical_identity",
	"work_item_exists_in_campaign",
	"active_claim_in_other_campaign",
	"owned_outside_cskh",
	"no_assignment_rule",
	"user_disabled",
	"user_out_of_team_scope",
	"user_is_not_sales_user",
	"no_positive_weight",
	"capacity_full",
	"no_eligible_assignee",
	"assignment_failed",
)

# --- Import row classifications ---------------------------------------------
IMPORT_CLASSES = (
	"valid",
	"duplicate_in_file",
	"duplicate_in_crm",
	"conflict_active_campaign",
	"invalid",
)

ALL_STRINGS = (
	SHELL
	+ TITLES
	+ POOL
	+ IMPORT
	+ CAMPAIGNS
	+ MY_CUSTOMERS
	+ DASHBOARD
	+ ERROR_CODES
	+ tuple(ERROR_EXPLANATIONS.values())
	+ ELIGIBILITY_REASONS
	+ IMPORT_CLASSES
	+ BACKEND_MESSAGES
)


def translated_map():
	"""Return `{english message id: translated text}` for the current language.

	The map is injected into the page as `window.cskhI18n`, so the JavaScript
	never carries a hard-coded label.

	Besides the labels, the map carries the **error explanation** of every
	stable code of the contract: `window.cskhErrorTexts[code]` is the
	operator-facing Vietnamese text of the situation.
	"""
	language = frappe.local.lang or "en"
	result = {}
	for message in ALL_STRINGS:
		result[message] = frappe._(message, lang=language)
	return result


def translated_error_texts():
	"""Return `{stable error code: operator-facing text}`.

	The backend answers with a stable code (P1 §10.1) and an English technical
	message. The page shows the **Vietnamese** explanation of the situation,
	because the operator has to act on it (P1 §9.6).
	"""
	language = frappe.local.lang or "en"
	return {
		code: frappe._(message, lang=language)
		for code, message in ERROR_EXPLANATIONS.items()
	}
