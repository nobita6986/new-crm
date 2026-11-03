"""P1 schema patch: the composite unique constraints and lookup indexes.

P1 §5.6 requires the unique constraints and the lookup indexes of the P1
DocTypes to be **version-controlled**. The DocType JSON only expresses the
single-column uniques; the composite rules need `frappe.db.add_unique` /
`frappe.db.add_index`, so they live in a patch.

`crm_cskh.p1_models.ensure_indexes` keeps the same definitions for the
`on_doctype_update` path (a fresh install). The patch is what rebuilds the
constraints on an **existing** database whose indexes were lost (a restore
from a dump without indexes, or a site whose `on_doctype_update` never fired
again). Every call is idempotent, so `bench migrate` stays safe to run twice.
"""

import frappe

from crm_cskh.p1_models import ensure_indexes

P1_DOCTYPES = (
	"CSKH Campaign",
	"CSKH Assignment Rule",
	"CSKH Work Item",
	"CSKH Assignment History",
	"CSKH Import Batch",
	"CSKH Import Batch Lead",
	"CSKH Distribution Run",
	"CSKH Identity Index",
	"CSKH Active Claim",
)


def execute():
	"""Rebuild the P1 unique constraints and lookup indexes."""
	for doctype in P1_DOCTYPES:
		ensure_indexes(doctype)
	frappe.db.commit()
