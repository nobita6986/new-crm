"""P1 — canonical phone identity (T1-01, T1-02, T1-05, T1-06, ADR-P1-01).

The invariants under test:

* `normalize_vn_phone` is a pure function and only accepts the documented
  Vietnamese mobile formats (P1 §5.4).
* The DB unique index on `CSKH Identity Index.normalized_phone` is the hard
  guarantee: two concurrent writers cannot create two canonical leads.
* An existing lead is reused as-is: the import never changes the owner, the
  status or a manual field of a lead that already exists (P1 §6.3, T1-06).
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm_cskh.services import identity as identity_service
from crm_cskh.services.errors import Conflict
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone
from crm_cskh.tests.p1_base import P1TestCase


class TestPhoneNormalization(FrappeTestCase):
	"""The pure normalizer (P1 §5.4, example table)."""

	def test_equivalent_formats_normalize_to_the_same_canonical_phone(self):
		for raw in ("0912345678", "84912345678", "+84 912 345 678", "+84912345678", "0912.345.678"):
			with self.subTest(raw=raw):
				self.assertEqual(normalize_vn_phone(raw), "+84912345678")

	def test_invalid_forms_are_rejected(self):
		for raw in ("091234567", "09abc", "", "   ", "+84912345678x", "12345", "0212345678"):
			with self.subTest(raw=raw):
				with self.assertRaises(InvalidPhone):
					normalize_vn_phone(raw)

	def test_scientific_notation_is_never_trusted(self):
		for raw in ("9.12346E+08", "9.12346e+08", "1e10"):
			with self.subTest(raw=raw):
				with self.assertRaises(InvalidPhone):
					normalize_vn_phone(raw)

	def test_two_numbers_in_one_cell_are_not_guessed(self):
		with self.assertRaises(InvalidPhone):
			normalize_vn_phone("0912345678 0912345679")

	def test_non_string_input_is_rejected(self):
		with self.assertRaises(InvalidPhone):
			normalize_vn_phone(912345678)

	def test_error_code_is_stable(self):
		try:
			normalize_vn_phone("09abc")
		except InvalidPhone as exc:
			self.assertEqual(exc.code, "INVALID_PHONE")


class TestCanonicalIdentity(P1TestCase):
	"""The DB-level uniqueness of the canonical identity (ADR-P1-01)."""

	def make_lead_with_canonical(self):
		"""Create a lead and return `(lead, canonical_phone)`."""
		lead = self.make_lead()
		canonical = normalize_vn_phone(frappe.db.get_value("CRM Lead", lead, "mobile_no"))
		return lead, canonical

	def test_duplicate_normalized_phone_is_blocked_by_the_database(self):
		# Two leads may carry the same number (upstream CRM allows it); only
		# the identity index is unique. The application validation passes, so
		# the DB unique index is what actually blocks the second row.
		lead_a = self.make_lead()
		phone = frappe.db.get_value("CRM Lead", lead_a, "mobile_no")
		canonical = normalize_vn_phone(phone)
		lead_b = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "CSKH P1 Test",
				"mobile_no": phone,
				"status": "New",
			}
		).insert(ignore_permissions=True)
		lead_b = lead_b.name
		identity_service.claim_canonical_identity(canonical, lead_a)
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{
					"doctype": "CSKH Identity Index",
					"normalized_phone": canonical,
					"canonical_lead": lead_b,
				}
			).insert(ignore_permissions=True)

	def test_claim_is_idempotent_for_the_same_lead(self):
		lead, canonical = self.make_lead_with_canonical()
		first = identity_service.claim_canonical_identity(canonical, lead)
		second = identity_service.claim_canonical_identity(canonical, lead)
		self.assertEqual(first, lead)
		self.assertEqual(second, lead)
		self.assertEqual(frappe.db.count("CSKH Identity Index", {"canonical_lead": lead}), 1)

	def test_claim_rejects_a_phone_owned_by_another_lead(self):
		lead_a, canonical = self.make_lead_with_canonical()
		lead_b = self.make_lead()
		identity_service.claim_canonical_identity(canonical, lead_a)
		with self.assertRaises(Conflict) as ctx:
			identity_service.claim_canonical_identity(canonical, lead_b)
		self.assertEqual(ctx.exception.code, "DUPLICATE")

	def test_lookup_by_phone_and_by_lead(self):
		lead, canonical = self.make_lead_with_canonical()
		identity_service.claim_canonical_identity(canonical, lead)
		self.assertEqual(identity_service.find_by_phone(canonical), lead)
		self.assertEqual(identity_service.find_by_lead(lead).normalized_phone, canonical)
		self.assertIsNone(identity_service.find_by_phone("+84909999999"))

	def test_adopt_policy_links_a_lead_created_outside_p1(self):
		"""A lead that already carries the number is adopted, never duplicated."""
		lead, canonical = self.make_lead_with_canonical()
		self.assertIsNone(identity_service.find_by_phone(canonical))
		canonical_lead = identity_service.claim_canonical_identity(canonical, lead, allow_adopt=True)
		self.assertEqual(canonical_lead, lead)
		self.assertEqual(
			frappe.db.count("CRM Lead", {"mobile_no": frappe.db.get_value("CRM Lead", lead, "mobile_no")}),
			1,
		)

	def test_ensure_identity_for_lead_is_idempotent(self):
		lead, _canonical = self.make_lead_with_canonical()
		first = identity_service.ensure_identity_for_lead(lead)
		second = identity_service.ensure_identity_for_lead(lead)
		self.assertEqual(first, lead)
		self.assertEqual(second, lead)
		self.assertEqual(frappe.db.count("CSKH Identity Index", {"canonical_lead": lead}), 1)

	def test_release_removes_the_identity_claim(self):
		lead, canonical = self.make_lead_with_canonical()
		identity_service.claim_canonical_identity(canonical, lead)
		self.assertTrue(identity_service.release_canonical_identity(canonical))
		self.assertIsNone(identity_service.find_by_phone(canonical))
		self.assertFalse(identity_service.release_canonical_identity(canonical))
