"""Shared base class and fixtures for the isolated P3 backend test suite (`docs/root-plan/P3.md` §15).

All P3 backend tests live in `crm_cskh.tests.test_p3_*` so that:
  * P0 inventory (`99` methods in `g007_test_inventory.json`) remains untouched,
  * P1 inventory (`209` methods in `p1_test_inventory.json`) remains untouched, and
  * P2 inventory (`39` methods in `p2_test_inventory.json`) remains untouched.
"""

from __future__ import annotations

import frappe

from crm_cskh.p3_models import DEFAULT_P3_POLICY_FIELDS, ensure_p3_indexes, ensure_p3_seeds
from crm_cskh.services.attribution import ensure_cutover_membership_baseline
from crm_cskh.tests.p2_base import P2TestCase


class P3TestCase(P2TestCase):
	"""Isolated P3 test harness building on `P2TestCase` with P3 calendars, seeds, and indexes."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		ensure_p3_indexes()
		ensure_p3_seeds()
		if frappe.db.exists("CSKH Policy", "GLOBAL-DEFAULT"):
			frappe.db.set_value(
				"CSKH Policy",
				"GLOBAL-DEFAULT",
				dict(DEFAULT_P3_POLICY_FIELDS),
				update_modified=False,
			)
		ensure_cutover_membership_baseline()
		frappe.db.commit()
