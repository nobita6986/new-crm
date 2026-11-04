"""P3 migration patch: P1/P2/P3 indexes, DEV/configurable calendar & policy seeds, and attribution cutover.

Idempotent across repeated `bench migrate` runs (`bench migrate` x 2 verification gate).
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.patches.v2_0 import p2_schema_and_seeds
from crm_cskh.services.attribution import (
	backfill_pre_p3_assignment_attribution,
	ensure_cutover_membership_baseline,
)


def execute() -> None:
	p2_schema_and_seeds.execute()
	ensure_p3_indexes()
	ensure_p3_seeds()
	# 1. Mark pre-P3 historical intervals lacking authoritative evidence as `historical_attribution_unknown`
	backfill_pre_p3_assignment_attribution()
	# 2. Record cutover membership intervals starting at `now_datetime()` (never backdated)
	ensure_cutover_membership_baseline(cutover_at=now_datetime())
	frappe.clear_cache()
