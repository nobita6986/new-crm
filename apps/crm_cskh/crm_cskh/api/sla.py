"""Whitelisted SLA operational endpoints (`docs/p3/report-api-contract.md` §3.9)."""

from __future__ import annotations

import frappe

from crm_cskh.jobs.evaluate_sla import evaluate_sla_obligations
from crm_cskh.p1_permissions import ROLE_ADMIN, _is_system, _roles
from crm_cskh.services import sla_engine
from crm_cskh.services.errors import ForbiddenScope, InvalidRequest


def _require_post() -> None:
	req = getattr(frappe.local, "request", None)
	if req is not None and getattr(req, "method", "POST").upper() != "POST":
		raise InvalidRequest("Thao tác cập nhật SLA bắt buộc dùng phương thức HTTP POST.")


@frappe.whitelist()
def list_obligations(
	work_item: str | None = None,
	status: str | None = None,
	obligation_type: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	employee: str | None = None,
	owner_user: str | None = None,
	lead_source: str | None = None,
	period_type: str | None = None,
	period_key: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	search: str | None = None,
	sort_by: str | None = None,
	sort_dir: str | None = None,
	page=1,
	page_size=20,
):
	return sla_engine.list_obligations(
		work_item=work_item,
		status=status,
		obligation_type=obligation_type,
		campaign=campaign,
		team=team,
		employee=employee if employee is not None else owner_user,
		lead_source=lead_source,
		period_type=period_type,
		period_key=period_key,
		from_date=from_date,
		to_date=to_date,
		search=search,
		sort_by=sort_by,
		sort_dir=sort_dir,
		page=page,
		page_size=page_size,
	)


@frappe.whitelist()
def get_obligation_detail(obligation_id: str):
	return sla_engine.get_obligation_detail(obligation_id=obligation_id)


@frappe.whitelist()
def waive_obligation(obligation_id: str, reason: str | None = None):
	_require_post()
	return sla_engine.waive_obligation(obligation_id=obligation_id, reason=reason)


@frappe.whitelist()
def pause_obligation(obligation_id: str, reason: str | None = None):
	_require_post()
	return sla_engine.pause_obligation(obligation_id=obligation_id, reason=reason)


@frappe.whitelist()
def resume_obligation(obligation_id: str, reason: str | None = None):
	_require_post()
	return sla_engine.resume_obligation(obligation_id=obligation_id, reason=reason)


@frappe.whitelist()
def evaluate_due_obligations(batch_size: int = 500):
	_require_post()
	actor = frappe.session.user
	if not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
		raise ForbiddenScope(
			"Chỉ CSKH CRM Admin hoặc System Manager được phép kích hoạt đánh giá SLA thủ công."
		)
	return evaluate_sla_obligations(batch_size=int(batch_size or 500))
