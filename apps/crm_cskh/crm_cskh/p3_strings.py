"""Vietnamese string catalogue for P3 SLA, KPI, QA & BOD Quality Reporting (`P3-00..P3-09`).

Imported by `crm_cskh.p1_strings` (with standalone file-import fallback so CI
offline translation audits work without a Frappe runtime).
"""

P3_ERROR_CODES = (
	"INVALID_DATE_RANGE",
	"INVALID_DIMENSION",
	"INVALID_METRIC_KEY",
	"FORBIDDEN_SCOPE",
	"SNAPSHOT_NOT_READY",
	"RECOMPUTE_IN_PROGRESS",
	"EXPORT_EXPIRED",
	"EXPORT_TOO_LARGE",
	"QA_DISABLED",
	"QA_CONFLICT",
	"CALENDAR_INVALID",
	"POLICY_CONFLICT",
)

P3_ERROR_EXPLANATIONS = {
	"INVALID_DATE_RANGE": "The requested reporting date range or period grain is invalid.",
	"INVALID_DIMENSION": "The requested grouping dimension or filter parameter is invalid.",
	"INVALID_METRIC_KEY": "The requested KPI metric key is not supported by the current metric version.",
	"FORBIDDEN_SCOPE": "You do not have permission to access this reporting scope, SLA obligation or export job.",
	"SNAPSHOT_NOT_READY": "The KPI snapshot for this period is still being computed. Please retry shortly.",
	"RECOMPUTE_IN_PROGRESS": "A KPI recomputation run is already in progress for the requested period.",
	"EXPORT_EXPIRED": "This export file has expired or was revoked.",
	"EXPORT_TOO_LARGE": "The requested export exceeds the permitted row limit; please narrow your filters.",
	"QA_DISABLED": "QA review is currently disabled in the active policy configuration.",
	"QA_CONFLICT": "This QA review conflicts with self-review or scoring rules.",
	"CALENDAR_INVALID": "The working calendar or shift configuration is invalid.",
	"POLICY_CONFLICT": "The action conflicts with the active SLA or operational policy.",
}

P3_SLA_AND_KPI_LABELS = (
	"Working calendar",
	"Working shift",
	"Team membership history",
	"SLA obligation",
	"First attempt SLA",
	"Follow-up SLA",
	"First connected tracker (informational)",
	"Minimum attempts policy",
	"Original due at",
	"Effective due at",
	"Fulfilled on time",
	"Fulfilled late",
	"Breached (open)",
	"Breached before handoff",
	"Closed on handoff (within SLA)",
	"Superseded by valid reschedule",
	"Waived with audit",
	"Canceled validly",
	"Pause SLA clock",
	"Resume SLA clock",
	"Waive SLA obligation",
	"Historical attribution unknown",
	"Authoritative team attribution",
	"Process compliance score",
	"Provisional score (QA disabled)",
	"Insufficient data",
	"DEV / configurable policy (pending BOD sign-off)",
)

ALL_P3_STRINGS = P3_ERROR_CODES + tuple(P3_ERROR_EXPLANATIONS.values()) + P3_SLA_AND_KPI_LABELS
