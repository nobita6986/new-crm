"""P1 API: distribution preview, background execution and run status.

P1 10.1: `preview` returns `preview_id`, `selection_fingerprint`, `rule_version`,
`expires_at` and a summary; `execute` takes `preview_id`, `expected_rule_version`
and `idempotency_key`, enqueues the background RQ worker and returns `run_id`
and early status (`queued`) immediately. A stale preview is refused with
`RULE_CHANGED` or `PREVIEW_EXPIRED` instead of distributing stale numbers.
"""

import frappe

from crm_cskh.services import distribution
from crm_cskh.services.errors import Forbidden, InvalidRequest, P1Error


def _is_system(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _require_distribute(user=None):
	"""Preview and execute are restricted to the CRM Admin business role."""
	user = user or frappe.session.user
	if _is_system(user):
		return
	if "CSKH CRM Admin" in frappe.get_roles(user):
		return
	raise Forbidden("Distributing the customers requires the CRM Admin business role.")


@frappe.whitelist(methods=["POST"])
def preview(campaign=None, idempotency_key=None):
	"""Compute a dry run of the distribution."""
	actor = frappe.session.user
	_require_distribute(actor)
	if not campaign:
		raise InvalidRequest("The campaign is required.")
	try:
		return distribution.preview(campaign, actor=actor, idempotency_key=idempotency_key)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["POST"])
def execute(preview_id=None, expected_rule_version=None, idempotency_key=None):
	"""Accept a distribution job onto RQ and return `run_id` + early status."""
	actor = frappe.session.user
	_require_distribute(actor)
	if not preview_id:
		raise InvalidRequest("The preview id is required.")
	try:
		return distribution.enqueue_run(
			preview_id,
			expected_rule_version=expected_rule_version,
			idempotency_key=idempotency_key,
			actor=actor,
			enqueue_job=True,
		)
	except P1Error:
		raise
	except Exception as exc:
		raise InvalidRequest(str(exc)[:300]) from exc


@frappe.whitelist(methods=["GET"])
def get_run(run_id=None):
	"""Return the live progress, cumulative totals and errors of a distribution run."""
	actor = frappe.session.user
	if not run_id:
		raise InvalidRequest("The run id is required.")
	if not _is_system(actor) and not {"CSKH CRM Admin", "CSKH BOD Viewer"} & set(frappe.get_roles(actor)):
		raise Forbidden("You may not read a distribution run.")
	return distribution.get_run(run_id, actor=actor)


@frappe.whitelist(methods=["GET"])
def reconcile(campaign=None):
	"""Reconcile the persisted distribution state with the database."""
	actor = frappe.session.user
	if not campaign:
		raise InvalidRequest("The campaign is required.")
	if not _is_system(actor) and "CSKH CRM Admin" not in frappe.get_roles(actor):
		raise Forbidden("You may not reconcile a campaign.")
	return distribution.reconcile(campaign, actor=actor)
