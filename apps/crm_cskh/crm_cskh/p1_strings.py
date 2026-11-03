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
	"Timeout",
	"An error occurred while processing the request.",
	"Invalid or expired session/CSRF token. Reload the page and try again.",
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

# --- Error messages of the frozen backend contract & API helpers -------------
# The API answers with these English messages; the browser shows them, so they
# have to be part of the Vietnamese catalogue as well.
BACKEND_MESSAGES = (
	"A phone number is required.",
	"A phone number must be a short text value.",
	"A phone number must be supplied as a text value.",
	"A queued, revoked or closed work item must not have an assignee.",
	"A reason is required to revoke a customer.",
	"A reason is required to transfer a customer.",
	"A request id is required so the action stays idempotent.",
	"A revocation event must not have a target user.",
	"A target user is required.",
	"An assigned work item requires an assignee.",
	"An assigned work item requires an assignment timestamp.",
	"An assignment event requires a target user.",
	"Assignment history is append-only; use a compensating event.",
	"Column mapping must be a JSON object.",
	"Distributing the customers requires the CRM Admin business role.",
	"Import counters must not be negative.",
	"Import source files must be private.",
	"Importing the customer pool requires the CRM Admin business role.",
	"Managing the campaigns requires the CRM Admin business role.",
	"No assignee is eligible for this campaign.",
	"No lead is eligible for this campaign.",
	"Only CSV and XLSX files are supported.",
	"Only Vietnamese numbers are supported.",
	"Only a queued work item can be assigned.",
	"Only an active work item may hold an active claim.",
	"Only the CRM Admin may list all sales assignees.",
	"Only the CRM Admin may read an import batch.",
	"Resolve the active work items before closing the campaign; nothing is released implicitly.",
	"The active claim must reference the same lead and campaign as its work item.",
	"The actual payload must be valid JSON.",
	"The allocator state must be a JSON object.",
	"The allocator state must be valid JSON.",
	"The assignee must be an enabled Sales User.",
	"The assignment rules changed after the preview.",
	"The batch status filter must be a string.",
	"The campaign and the assignee are required.",
	"The campaign code is required.",
	"The campaign does not exist.",
	"The campaign is not open for distribution.",
	"The campaign is required.",
	"The campaign must be a string.",
	"The campaign status is not supported.",
	"The campaign title is required.",
	"The canonical lead does not exist.",
	"The canonical lead must have a mobile number.",
	"The capacity must be an integer.",
	"The capacity must not be negative.",
	"The current owner is not the expected owner.",
	"The distribution run",
	"The distribution run does not exist.",
	"The distribution run is already executing.",
	"The distribution run is already owned by another executor.",
	"The distribution state changed after the preview.",
	"The eligible lead set changed after the preview.",
	"The end date must not precede the start date.",
	"The error file does not exist for this import batch.",
	"The event id is required so the event stays idempotent.",
	"The file has more rows than the configured limit.",
	"The file is larger than the configured limit.",
	"The import batch",
	"The import batch does not exist.",
	"The import batch is required.",
	"The import file must be private.",
	"The lead is already claimed by another campaign.",
	"The lead owner does not match the intended owner.",
	"The phone cell contains more than one number.",
	"The phone cell contains unsupported characters.",
	"The phone cell looks like scientific notation and cannot be trusted.",
	"The phone identity must be a canonical Vietnamese mobile number.",
	"The phone identity must match the canonical lead mobile number.",
	"The phone number is already bound to another canonical lead.",
	"The phone number is already used by another lead.",
	"The phone number is outside the supported Vietnamese mobile format.",
	"The phone number must start with 0, 84 or +84.",
	"The preview does not exist.",
	"The preview has expired; run the preview again.",
	"The preview id is required.",
	"The preview payload must be valid JSON.",
	"The rule version must be positive.",
	"The run id is required.",
	"The selection fingerprint is required.",
	"The selection must be a JSON object.",
	"The target user has no remaining capacity.",
	"The target user is disabled.",
	"The target user must be a Sales User.",
	"The uploaded file could not be decoded.",
	"The uploaded file does not exist.",
	"The uploaded file is required.",
	"The weight must be an integer.",
	"The weight must be positive.",
	"The work item already belongs to the target user.",
	"The work item and the target user are required.",
	"The work item does not exist.",
	"The work item is not currently assigned.",
	"The work item is required.",
	"This action requires the CRM Admin business role.",
	"This campaign, assignee and version already has a rule.",
	"This idempotency key was already used with a different file.",
	"Transferring a customer requires the CRM Admin business role.",
	"Unexpected share holders remain after the ownership change.",
	"Weight and version must be positive; capacity must not be negative.",
	"You may not read a distribution run.",
	"You may not read the assigned customers.",
	"You may not read the campaign list.",
	"You may not read the campaigns.",
	"You may not read the customer list.",
	"You may not read the customer pool.",
	"You may not read the distribution dashboard.",
	"You may not read this assignment history.",
	"You may not reconcile a campaign.",
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
	"Data error",
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
	"Name column",
	"Source column",
	"Total rows",
	"Valid rows",
	"New rows",
	"Duplicate rows",
	"Error rows",
	"Created rows",
	"Reused rows",
	"Skipped rows",
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
	"Edit campaign",
	"Close campaign",
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
	"Newest first",
	"Oldest first",
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
	"Active claims",
	"Active campaigns",
	"Failed batches",
	"Failed runs",
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
	"CSRF_ERROR",
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
	"CSRF_ERROR": "Invalid or expired session/CSRF token. Reload the page and try again.",
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
	"no_phone_column",
	"invalid_row",
)

# --- Import row classifications ---------------------------------------------
IMPORT_CLASSES = (
	"valid",
	"duplicate_in_file",
	"duplicate_in_crm",
	"conflict_active_campaign",
	"invalid",
)

# --- Additional labels of the P1 screens -----------------------------------
ADDITIONAL = (
	# Campaigns: run history and run lookup.
	"Look up run",
	"Running",
	"Completed",
	"Completed with errors",
	# Import: batch history and the error artifact.
	"Confirmed",
	"Error file",
	"Download error file",
	# Import and campaigns: the retry of a failed job (P1 §9.2, §9.3).
	"Retry the failed job",
	# My customers: the manual ownership actions.
	"Action",
	"Actions",
	"Actor",
	"Assign",
	"Revoke",
	"Transfer owner",
	"Transfer the ownership",
	"Revoke the ownership",
	"Transferred",
	"Current owner",
	"New owner",
	"From owner",
	"To owner",
	"Event at",
	"Ownership detail",
	"Ownership history",
	"Customers assigned in the scope",
	"The scope is resolved by the server. Only the CRM Admin may transfer or revoke an ownership.",
	# Dashboard: the date filters and the separation of the two counters.
	"From date",
	"To date",
	"Clear date filter",
	"Current ownership (live work items)",
	"Assignment events (audit trail)",
	"A transfer adds one event but the number of owned work items stays the same.",
	"The start date must not be after the end date.",
	# The validation of the manual ownership form.
	"A reason is required to transfer or revoke an ownership.",
	"Select the new owner of the work item.",
	# The states of a campaign and the scopes of the data.
	"Campaign draft",
	"Campaign active",
	"Campaign paused",
	"Campaign closed",
	"Whole installation",
	"Team scope",
	"Only my own data",
)

ALL_STRINGS = (
	SHELL
	+ TITLES
	+ POOL
	+ IMPORT
	+ CAMPAIGNS
	+ MY_CUSTOMERS
	+ DASHBOARD
	+ ADDITIONAL
	+ ERROR_CODES
	+ tuple(ERROR_EXPLANATIONS.values())
	+ ELIGIBILITY_REASONS
	+ IMPORT_CLASSES
	+ BACKEND_MESSAGES
)


def translated_map():
	"""Return `{english message id: translated text}` for the current language."""
	language = frappe.local.lang or "en"
	result = {}
	for message in ALL_STRINGS:
		result[message] = frappe._(message, lang=language)
	return result


def translated_error_texts():
	"""Return `{stable error code: operator-facing text}`."""
	language = frappe.local.lang or "en"
	return {code: frappe._(message, lang=language) for code, message in ERROR_EXPLANATIONS.items()}
