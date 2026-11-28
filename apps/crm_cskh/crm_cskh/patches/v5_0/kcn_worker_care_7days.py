"""v5_0 migration patch: KCN worker profile fields, self-sourced campaign seed & 7-workday stage backfill.

Idempotent across repeated `bench migrate` runs.
"""

from __future__ import annotations

import frappe

from crm_cskh.p2_models import ensure_p2_custom_fields, ensure_p2_indexes, ensure_p2_seeds

SELF_SOURCED_CAMPAIGN_CODE = "NGUON-TU-KHAI-THAC"
SELF_SOURCED_CAMPAIGN_TITLE = "Nguồn Marketing tự khai thác (KCN)"


def ensure_self_sourced_default_campaign() -> str | None:
	"""Ensure the default active campaign for Marketing self-sourced worker phone protection exists."""
	if not frappe.db.table_exists("CSKH Campaign"):
		return None
	existing = frappe.db.get_value("CSKH Campaign", {"code": SELF_SOURCED_CAMPAIGN_CODE}, "name")
	if existing:
		return str(existing)

	payload = {
		"doctype": "CSKH Campaign",
		"code": SELF_SOURCED_CAMPAIGN_CODE,
		"title": SELF_SOURCED_CAMPAIGN_TITLE,
		"description": "Đơn mặc định dùng để gắn bảo hộ tức thì khi nhân viên Marketing tự khai thác SĐT người lao động.",
		"status": "Active",
		"rule_version": 1,
		"conversion_outcome_codes": "completed_7_days,converted",
	}
	if frappe.db.has_column("CSKH Campaign", "target_workdays"):
		payload["target_workdays"] = 7
	if frappe.db.has_column("CSKH Campaign", "allow_self_sourced"):
		payload["allow_self_sourced"] = 1

	doc = frappe.get_doc(payload)
	doc.insert(ignore_permissions=True)
	return str(doc.name)


def backfill_worker_stages() -> None:
	"""Backfill `worker_stage` and `target_workdays` on pre-v5 `CSKH Work Item` rows."""
	if not (
		frappe.db.table_exists("CSKH Work Item") and frappe.db.has_column("CSKH Work Item", "worker_stage")
	):
		return

	if frappe.db.has_column("CSKH Work Item", "target_workdays"):
		frappe.db.sql(
			"""
			update `tabCSKH Work Item`
			set `target_workdays` = 7
			where coalesce(`target_workdays`, 0) <= 0
			"""
		)
	if frappe.db.has_column("CSKH Work Item", "source_type"):
		frappe.db.sql(
			"""
			update `tabCSKH Work Item`
			set `source_type` = 'company_assigned'
			where `source_type` is null or `source_type` = ''
			"""
		)

	frappe.db.sql(
		"""
		update `tabCSKH Work Item`
		set `worker_stage` = case
			when `status` = 'closed' and coalesce(`workdays_completed`, 0) >= 7 then 'completed_7_days'
			when `status` = 'closed' then 'stopped'
			when `status` in ('assigned', 'in_progress', 'on_hold') and coalesce(`workdays_completed`, 0) > 0 then 'working_care'
			when `status` in ('assigned', 'in_progress', 'on_hold') and `interview_date` is not null then 'interview_scheduled'
			when `status` in ('assigned', 'in_progress', 'on_hold') and coalesce(`attempt_count`, 0) > 0 then 'consulting'
			else 'new_contact'
		end
		where `worker_stage` is null or `worker_stage` = ''
		"""
	)


def execute():
	ensure_p2_custom_fields()
	ensure_p2_indexes()
	ensure_p2_seeds()
	ensure_self_sourced_default_campaign()
	backfill_worker_stages()
	frappe.clear_cache()
