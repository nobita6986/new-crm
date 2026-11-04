"""P2 API: outcome catalog, operational policy configuration & audited Lead DNC controls.

Implements `docs/api-contract-p2.md` §4.14.
All mutations (`save_policy`, `save_outcome`, `set_lead_dnc`) require `CSKH CRM Admin`
or `System Manager` and HTTP `POST` (`SEC2-09`, `SEC2-11`).
"""

from __future__ import annotations

import frappe

from crm_cskh.api.workflow import _reject_spoofed_identity_fields, _require_post
from crm_cskh.services import policies


@frappe.whitelist(methods=["GET"])
def list_policies(scope_type=None, campaign=None, team=None):
	"""Scoped list of `CSKH Policy` documents (`docs/api-contract-p2.md` §4.14)."""
	return policies.list_policies(
		scope_type=scope_type,
		campaign=campaign,
		team=team,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
def save_policy(
	policy_code=None,
	title=None,
	scope_type="Global",
	team=None,
	campaign=None,
	enabled=True,
	allowed_backdate_minutes=120,
	require_attempt_before_close=True,
	allow_close_without_contact_for_invalid=True,
	do_not_contact_behavior="block_and_auto_close",
	enabled_channels="phone_call,sms,zalo,facebook,other",
	allow_leader_reopen=True,
	allow_leader_review_correction=True,
	idempotency_key=None,
	**kwargs,
):
	"""Create or update a `CSKH Policy` (`version += 1` on change) (`docs/api-contract-p2.md` §4.14)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	allowed_p3_keys = {
		"working_calendar",
		*policies._P3_BOOL_POLICY_KEYS,
		*policies._P3_INT_POLICY_KEYS,
	}
	p3_opts = {k: v for k, v in kwargs.items() if k in allowed_p3_keys}
	return policies.save_policy(
		policy_code=policy_code,
		title=title,
		scope_type=scope_type,
		team=team,
		campaign=campaign,
		enabled=enabled,
		allowed_backdate_minutes=allowed_backdate_minutes,
		require_attempt_before_close=require_attempt_before_close,
		allow_close_without_contact_for_invalid=allow_close_without_contact_for_invalid,
		do_not_contact_behavior=do_not_contact_behavior,
		enabled_channels=enabled_channels,
		allow_leader_reopen=allow_leader_reopen,
		allow_leader_review_correction=allow_leader_review_correction,
		idempotency_key=idempotency_key,
		actor=frappe.session.user,
		**p3_opts,
	)


@frappe.whitelist(methods=["GET"])
def list_outcomes(include_disabled=True):
	"""List `CSKH Outcome Code` definitions (`docs/api-contract-p2.md` §4.14)."""
	return policies.list_outcomes(include_disabled=include_disabled, actor=frappe.session.user)


@frappe.whitelist(methods=["POST"])
def save_outcome(
	code=None,
	label_vi=None,
	result_group=None,
	description=None,
	is_contact_attempt=True,
	is_connected=False,
	is_interested=False,
	is_terminal=False,
	is_do_not_contact=False,
	requires_note=False,
	requires_followup=False,
	allows_followup=True,
	requires_completion_reason=False,
	allowed_channels="phone_call,sms,zalo,facebook,other",
	suggested_next_status="in_progress",
	enabled=True,
	sort_order=100,
	idempotency_key=None,
	**kwargs,
):
	"""Create or update a `CSKH Outcome Code` (`version += 1` on change) (`docs/api-contract-p2.md` §4.14)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return policies.save_outcome(
		code=code,
		label_vi=label_vi,
		result_group=result_group,
		description=description,
		is_contact_attempt=is_contact_attempt,
		is_connected=is_connected,
		is_interested=is_interested,
		is_terminal=is_terminal,
		is_do_not_contact=is_do_not_contact,
		requires_note=requires_note,
		requires_followup=requires_followup,
		allows_followup=allows_followup,
		requires_completion_reason=requires_completion_reason,
		allowed_channels=allowed_channels,
		suggested_next_status=suggested_next_status,
		enabled=enabled,
		sort_order=sort_order,
		idempotency_key=idempotency_key,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
def set_lead_dnc(lead=None, do_not_contact=None, reason=None, idempotency_key=None, **kwargs):
	"""Admin-only audited setter/clearer for `CRM Lead.cskh_do_not_contact` (`docs/api-contract-p2.md` §4.14)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return policies.set_lead_dnc(
		lead=lead,
		do_not_contact=do_not_contact,
		reason=reason,
		idempotency_key=idempotency_key,
		actor=frappe.session.user,
	)
