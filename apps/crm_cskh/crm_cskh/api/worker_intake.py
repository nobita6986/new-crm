"""Whitelisted RPC endpoints for Marketing self-sourced worker intake, KCN profile, and 7-day acceptance."""

from __future__ import annotations

import frappe

from crm_cskh.api.workflow import _reject_spoofed_identity_fields, _require_post, _run_with_tx_retry
from crm_cskh.services import worker_intake, worker_lifecycle


@frappe.whitelist(methods=["GET"])
def campaigns():
	"""Active KCN factory campaigns selectable by Marketing staff."""
	return worker_intake.list_intake_campaigns(actor=frappe.session.user)


@frappe.whitelist(methods=["GET"])
def check_phone(phone=None):
	"""Real-time duplicate & protection check for a Vietnamese mobile phone."""
	return worker_intake.check_phone_protection(phone=phone, actor=frappe.session.user)


@frappe.whitelist(methods=["POST"])
def add_worker(
	phone=None,
	full_name=None,
	campaign=None,
	acquisition_channel=None,
	birth_year=None,
	gender=None,
	current_area=None,
	id_card_status=None,
	worker_tags=None,
	zalo_phone=None,
	target_factory=None,
	target_industrial_zone=None,
	request_id=None,
	**kwargs,
):
	"""Add a self-sourced worker phone and immediately bind ownership protection to the caller."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: worker_intake.add_self_sourced_worker(
			phone=phone,
			full_name=full_name,
			campaign=campaign,
			acquisition_channel=acquisition_channel,
			birth_year=birth_year,
			gender=gender,
			current_area=current_area,
			id_card_status=id_card_status,
			worker_tags=worker_tags,
			zalo_phone=zalo_phone,
			target_factory=target_factory,
			target_industrial_zone=target_industrial_zone,
			request_id=request_id,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["POST"])
def bulk_add_workers(
	lines_text=None,
	campaign=None,
	acquisition_channel=None,
	request_id=None,
	**kwargs,
):
	"""Bulk paste self-sourced worker phones (`SĐT | Họ tên | Năm sinh | Ghi chú`)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return worker_intake.bulk_add_self_sourced_workers(
		lines_text=lines_text,
		campaign=campaign,
		acquisition_channel=acquisition_channel,
		request_id=request_id,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
def update_profile(
	work_item=None,
	full_name=None,
	birth_year=None,
	gender=None,
	current_area=None,
	id_card_status=None,
	worker_tags=None,
	zalo_phone=None,
	target_factory=None,
	target_industrial_zone=None,
	campaign=None,
	**kwargs,
):
	"""Update worker screening profile & target KCN factory (owner or CRM Admin only)."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: worker_lifecycle.update_worker_profile(
			work_item=work_item,
			full_name=full_name,
			birth_year=birth_year,
			gender=gender,
			current_area=current_area,
			id_card_status=id_card_status,
			worker_tags=worker_tags,
			zalo_phone=zalo_phone,
			target_factory=target_factory,
			target_industrial_zone=target_industrial_zone,
			campaign=campaign,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["POST"])
def record_progress(
	work_item=None,
	action_type=None,
	channel="zalo",
	note=None,
	interview_date=None,
	onboard_date=None,
	workdays_completed=None,
	increment_workday=False,
	target_factory=None,
	target_industrial_zone=None,
	followup_due_at=None,
	dropout_reason=None,
	close_after_dropout=False,
	idempotency_key=None,
	**kwargs,
):
	"""Record KCN interview / onboarding / daily workday check-in up to the 7-workday completion milestone."""
	_require_post()
	_reject_spoofed_identity_fields(kwargs)
	return _run_with_tx_retry(
		lambda: worker_lifecycle.record_worker_progress(
			work_item=work_item,
			action_type=action_type,
			channel=channel,
			note=note,
			interview_date=interview_date,
			onboard_date=onboard_date,
			workdays_completed=workdays_completed,
			increment_workday=increment_workday,
			target_factory=target_factory,
			target_industrial_zone=target_industrial_zone,
			followup_due_at=followup_due_at,
			dropout_reason=dropout_reason,
			close_after_dropout=close_after_dropout,
			idempotency_key=idempotency_key,
			actor=frappe.session.user,
		)
	)


@frappe.whitelist(methods=["GET"])
def acceptance_board(target_date=None, date_from=None, date_to=None, campaign=None):
	"""End-of-Day Acceptance Board & 7-Workday Funnel."""
	return worker_lifecycle.get_daily_acceptance_board(
		target_date=target_date,
		date_from=date_from,
		date_to=date_to,
		campaign=campaign,
		actor=frappe.session.user,
	)


@frappe.whitelist(methods=["GET"])
def export_acceptance_csv(date_from=None, date_to=None, campaign=None):
	"""Export End-of-Day & 7-Workday acceptance report as UTF-8 BOM CSV."""
	return worker_lifecycle.export_acceptance_csv(
		date_from=date_from,
		date_to=date_to,
		campaign=campaign,
		actor=frappe.session.user,
	)
