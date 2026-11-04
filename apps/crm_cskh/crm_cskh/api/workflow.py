"""P2 API: contact attempts, follow-up scheduling, close/reopen, corrections & operational progress.

Thin `@frappe.whitelist()` RPC layer delegating to transactional domain services:
  * `crm_cskh.services.read_model`
  * `crm_cskh.services.contact_attempt`
  * `crm_cskh.services.call_log_adapter`
  * `crm_cskh.services.followup`
  * `crm_cskh.services.correction`
  * `crm_cskh.services.policies`

All authorization scopes are resolved strictly server-side (`SEC-07`, `SEC2-08`, `SEC2-14`);
client-supplied performer/verification fields are rejected fail-closed (`SEC2-03`, `SEC2-04`).
"""

from __future__ import annotations

import frappe

from crm_cskh.api._params import parse_optional_str
from crm_cskh.p2_permissions import ROLE_ADMIN, ROLE_BOD, _is_system, _roles
from crm_cskh.services import call_log_adapter, contact_attempt, correction, followup, policies, read_model
from crm_cskh.services.errors import Forbidden, InvalidRequest, is_transient_tx_abort

SPOOFED_IDENTITY_KEYS = frozenset(
	(
		"performed_by",
		"owner_at_attempt",
		"verification_status",
		"actor",
		"closed_by",
		"owner_at_close",
		"reopened_by",
		"requested_by",
		"reviewed_by",
	)
)


def _require_post() -> None:
	req = getattr(frappe.local, "request", None)
	if req is not None and getattr(req, "method", "POST").upper() != "POST":
		raise InvalidRequest("Thao tác cập nhật dữ liệu chỉ chấp nhận phương thức HTTP POST.")


def _run_with_tx_retry(fn, max_retries: int = 3):
	"""Retry top-level RPC mutations on transient MariaDB `1213` deadlock / `1020` snapshot conflict."""
	attempt = 0
	while True:
		try:
			return fn()
		except Exception as exc:
			if is_transient_tx_abort(exc) and attempt < max_retries:
				attempt += 1
				frappe.db.rollback()
				continue
			raise


def _reject_spoofed_identity_fields(extra_kwargs: dict | None = None) -> None:
	"""Fail closed (`403 FORBIDDEN`) if client attempts to spoof performer or verification status."""
	if extra_kwargs:
		bad = SPOOFED_IDENTITY_KEYS.intersection(extra_kwargs.keys())
		if bad:
			raise Forbidden(
				"Không được phép giả mạo danh tính thực hiện hoặc trạng thái xác minh từ phía client."
			)
	form_dict = getattr(frappe.local, "form_dict", None)
	if isinstance(form_dict, dict):
		bad_form = SPOOFED_IDENTITY_KEYS.intersection(form_dict.keys())
		if bad_form:
			raise Forbidden(
				"Không được phép giả mạo danh tính thực hiện hoặc trạng thái xác minh từ phía client."
			)


@frappe.whitelist(methods=["GET"])
def list_work_items(
	page=1,
	page_size=20,
	status=None,
	care_state=None,
	campaign=None,
	followup_filter=None,
	assignee=None,
	owner_at_close=None,
	closed_by=None,
	search=None,
):
	"""Scoped CSKH work queue with server-side care-state & closed-owner filtering (`docs/api-contract-p2.md` §4.2)."""
	return read_model.list_work_items(
		page=page,
		page_size=page_size,
		status=status,
		care_state=care_state,
		campaign=campaign,
		followup_filter=followup_filter,
		assignee=assignee,
		owner_at_close=owner_at_close,
		closed_by=closed_by,
		search=search,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["GET"])
def get_work_item(work_item=None):
	"""Scoped Work Item detail, effective policy & actor capabilities (`docs/api-contract-p2.md` §4.3)."""
	return read_model.get_work_item_detail(work_item=work_item, actor=frappe.session.user)


@frappe.whitelist(methods=["GET"])
def list_attempts(work_item=None, page=1, page_size=20, include_milestones=True):
	"""Paginated effective attempt timeline + ownership milestones (`docs/api-contract-p2.md` §4.4)."""
	return read_model.list_attempts_timeline(
		work_item=work_item,
		page=page,
		page_size=page_size,
		include_milestones=include_milestones,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["GET"])
def list_unlinked_call_logs(work_item=None, limit=20):
	"""Unlinked native `CRM Call Log` records on `work_item.lead` (`docs/api-contract-p2.md` §3)."""
	return call_log_adapter.list_unlinked_call_logs(
		work_item=work_item,
		limit=limit,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
def record_attempt(
	work_item=None,
	channel=None,
	outcome_code=None,
	occurred_at=None,
	note=None,
	call_log=None,
	call_log_data=None,
	followup=None,
	followup_due_at=None,
	followup_title=None,
	followup_note=None,
	complete_open_followup=True,
	close_work_item=False,
	completion_reason=None,
	expected_revision=None,
	expected_policy_version=None,
	idempotency_key=None,
	**kwargs,
):
	"""Record an immutable `CSKH Contact Attempt` atomically (`docs/api-contract-p2.md` §4.5)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: contact_attempt.record_attempt(
			work_item=work_item,
			channel=channel,
			outcome_code=outcome_code,
			occurred_at=occurred_at,
			note=note,
			call_log=call_log,
			call_log_data=call_log_data,
			followup=followup,
			followup_due_at=followup_due_at,
			followup_title=followup_title,
			followup_note=followup_note,
			complete_open_followup=complete_open_followup,
			close_work_item=close_work_item,
			completion_reason=completion_reason,
			expected_revision=expected_revision,
			expected_policy_version=expected_policy_version,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["GET"])
def get_outcome_codes(channel=None, work_item=None, campaign=None, include_disabled=False):
	"""Active `CSKH Outcome Code` catalog + resolved effective policy (`docs/api-contract-p2.md` §4.6)."""
	return policies.get_outcome_codes_for_workflow(
		channel=channel,
		work_item=work_item,
		campaign=campaign,
		include_disabled=include_disabled,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
def schedule_followup(
	work_item=None,
	due_at=None,
	title=None,
	note=None,
	reason=None,
	expected_revision=None,
	idempotency_key=None,
	**kwargs,
):
	"""Create or reschedule the open callback `CRM Task` on `work_item` (`docs/api-contract-p2.md` §4.7)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: followup.schedule_followup(
			work_item=work_item,
			due_at=due_at,
			title=title,
			note=note,
			reason=reason,
			expected_revision=expected_revision,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["GET"])
def list_followups(
	page=1,
	page_size=20,
	work_item=None,
	campaign=None,
	bucket=None,
	status="open",
	assignee=None,
):
	"""Scoped follow-up `CRM Task` list (`docs/api-contract-p2.md` §4.8)."""
	return followup.list_followups(
		page=page,
		page_size=page_size,
		work_item=work_item,
		campaign=campaign,
		bucket=bucket,
		status=status,
		assignee=assignee,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
def complete_followup(
	work_item=None,
	task_id=None,
	action="complete",
	attempt_id=None,
	completion_reason=None,
	expected_revision=None,
	idempotency_key=None,
	**kwargs,
):
	"""Complete (linked to `attempt_id`) or cancel (with `completion_reason`) a callback (`docs/api-contract-p2.md` §4.9)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: followup.complete_followup(
			work_item=work_item,
			task_id=task_id,
			action=action,
			attempt_id=attempt_id,
			completion_reason=completion_reason,
			expected_revision=expected_revision,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["POST"])
def close_work_item(
	work_item=None,
	completion_reason=None,
	close_category="normal",
	expected_revision=None,
	idempotency_key=None,
	**kwargs,
):
	"""Close an active `CSKH Work Item` atomically (`docs/api-contract-p2.md` §4.10)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: contact_attempt.close_work_item(
			work_item=work_item,
			completion_reason=completion_reason,
			close_category=close_category,
			expected_revision=expected_revision,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["POST"])
def reopen_work_item(
	work_item=None,
	target_user=None,
	reason=None,
	override_dnc=False,
	expected_revision=None,
	idempotency_key=None,
	**kwargs,
):
	"""Audited reopen of a `closed` `CSKH Work Item` (`docs/api-contract-p2.md` §4.11)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: contact_attempt.reopen_work_item(
			work_item=work_item,
			target_user=target_user,
			reason=reason,
			override_dnc=override_dnc,
			expected_revision=expected_revision,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["POST"])
def request_correction(
	attempt=None,
	action=None,
	corrected_fields=None,
	reason=None,
	auto_approve=False,
	expected_revision=None,
	idempotency_key=None,
	**kwargs,
):
	"""Submit an audited `CSKH Attempt Correction` (`docs/api-contract-p2.md` §4.12)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: correction.request_correction(
			attempt=attempt,
			action=action,
			corrected_fields=corrected_fields,
			reason=reason,
			auto_approve=auto_approve,
			expected_revision=expected_revision,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["POST"])
def review_correction(
	correction_id=None,
	decision=None,
	review_note=None,
	expected_revision=None,
	idempotency_key=None,
	**kwargs,
):
	"""Approve or reject a pending `CSKH Attempt Correction` (`docs/api-contract-p2.md` §4.12)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: correction.review_correction(
			correction_id=correction_id,
			decision=decision,
			review_note=review_note,
			expected_revision=expected_revision,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["GET"])
def list_corrections(page=1, page_size=20, work_item=None, status=None):
	"""Scoped `CSKH Attempt Correction` queue (`docs/api-contract-p2.md` §3)."""
	return correction.list_corrections(
		page=page,
		page_size=page_size,
		work_item=work_item,
		status=status,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["GET"])
def progress_summary(campaign=None, date_from=None, date_to=None):
	"""Scoped P2 operational summary with zero PII (`docs/api-contract-p2.md` §4.13)."""
	return read_model.progress_summary(
		campaign=campaign,
		date_from=date_from,
		date_to=date_to,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["GET"])
def reconcile(campaign=None):
	"""Read-only P2 database invariant check (`P2.md` §15.5) for Admin / BOD."""
	actor = frappe.session.user
	if not actor or actor == "Guest":
		raise Forbidden("Bạn cần đăng nhập để kiểm tra đối soát dữ liệu.")
	if not (_is_system(actor) or bool({ROLE_ADMIN, ROLE_BOD} & _roles(actor))):
		raise Forbidden("Chỉ CSKH CRM Admin hoặc BOD được chạy đối soát P2.")
	camp_val = parse_optional_str(campaign, "Mã chiến dịch không hợp lệ.")
	return read_model.reconcile_p2_invariants(campaign=camp_val or None)
