"""P2 migration patch: Custom Fields, DB unique/lookup indexes, and canonical seeds.

Idempotent across repeated `bench migrate` runs (`P2.md` §15.1, `docs/data-model-p2.md`).
"""

import frappe

from crm_cskh.p1_models import ensure_indexes
from crm_cskh.p2_models import ensure_p2_custom_fields, ensure_p2_indexes, ensure_p2_seeds


def execute():
	ensure_p2_custom_fields()
	ensure_indexes("CSKH Work Item")
	ensure_p2_indexes()
	if frappe.db.table_exists("CSKH Work Item") and frappe.db.has_column("CSKH Work Item", "revision"):
		frappe.db.sql(
			"""
			update `tabCSKH Work Item`
			set revision = 1,
			    policy_version = coalesce(nullif(policy_version, 0), 1),
			    attempt_count = coalesce(attempt_count, 0),
			    connected_attempt_count = coalesce(connected_attempt_count, 0)
			where coalesce(revision, 0) < 1
			"""
		)
	ensure_p2_seeds()
	frappe.db.commit()
