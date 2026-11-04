"""Whitelisted Frappe RPC endpoints for P3 QA module (`docs/p3/report-api-contract.md` §2, `qa_enabled=false` default)."""

from __future__ import annotations

import frappe

from crm_cskh.services import qa as qa_svc


@frappe.whitelist(methods=["GET", "POST"])
def list_reviews(
	reviewed_staff: str | None = None,
	campaign: str | None = None,
	status: str | None = None,
	page=1,
	page_size=20,
) -> dict:
	return qa_svc.list_reviews(
		reviewed_staff=reviewed_staff,
		campaign=campaign,
		status=status,
		page=page,
		page_size=page_size,
	)


@frappe.whitelist(methods=["POST"])
def create_sample(contact_attempt: str, rubric_version: str = "qa-rubric-v1") -> dict:
	return qa_svc.create_sample(contact_attempt=contact_attempt, rubric_version=rubric_version)


@frappe.whitelist(methods=["POST"])
def submit_review(
	contact_attempt: str,
	points_achieved: float | int,
	max_points: float | int = 100.0,
	passed=None,
	status: str = "published",
	feedback_summary: str | None = None,
	internal_note: str | None = None,
	scores=None,
	rubric_version: str = "qa-rubric-v1",
	reviewed_at: str | None = None,
) -> dict:
	return qa_svc.submit_review(
		contact_attempt=contact_attempt,
		points_achieved=points_achieved,
		max_points=max_points,
		passed=passed,
		status=status,
		feedback_summary=feedback_summary,
		internal_note=internal_note,
		scores=scores,
		rubric_version=rubric_version,
		reviewed_at=reviewed_at,
	)


# Contract aliases (`docs/p3/qa-checklist-and-sampling.md` §2.1)
list_qa_reviews = frappe.whitelist(methods=["GET", "POST"])(list_reviews)
create_qa_sample = frappe.whitelist(methods=["POST"])(create_sample)
submit_qa_review = frappe.whitelist(methods=["POST"])(submit_review)
