"""P1 schema patch (L-25): composite unique and lookup indexes on `CSKH Import Batch Lead`.

Ensures sites that already executed `p1_schema_constraints` also receive the
composite unique constraint `(import_batch, lead)` and the covering lookup
indexes `(lead, import_batch)` and `(import_batch, outcome)`. Safe to run
multiple times (`ensure_indexes` is idempotent).
"""

import frappe

from crm_cskh.p1_models import ensure_indexes


def execute():
	if frappe.db.table_exists("CSKH Import Batch Lead"):
		ensure_indexes("CSKH Import Batch Lead")
		frappe.db.commit()
