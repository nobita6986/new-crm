"""Canonical identity service: the ONLY writer of `CSKH Identity Index`.

ADR-P1-01: the DB unique index on `normalized_phone` is the hard invariant; this
service is the controlled application layer on top of it. It turns the raw
`IntegrityError` of two concurrent writers into a stable business error code
and keeps the "canonical lead" pointer consistent.

Nothing outside this module may insert or delete a `CSKH Identity Index` row.
"""

import frappe
from frappe.utils import now_datetime

from crm_cskh.services.errors import Conflict, NotFound
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone


def find_by_phone(normalized_phone):
	"""Return the canonical lead name for a normalized phone, or None."""
	if not normalized_phone:
		return None
	return frappe.db.get_value(
		"CSKH Identity Index", {"normalized_phone": normalized_phone}, "canonical_lead"
	)


def find_by_lead(lead):
	"""Return the identity row of a lead, or None."""
	if not lead:
		return None
	return frappe.db.get_value(
		"CSKH Identity Index", {"canonical_lead": lead}, ["name", "normalized_phone"], as_dict=True
	)


def find_lead_by_phone(normalized_phone):
	"""Return a lead whose mobile number normalizes to `normalized_phone`.

	Unlike `find_by_phone`, this also finds a lead that is **not** registered
	in the identity index yet — the "a lead exists outside P1" case of
	ADR-P1-01 §3.2. The import pipeline uses it so an existing customer is
	adopted instead of duplicated (P1 §6.3, T1-06).
	"""
	if not normalized_phone:
		return None
	return _find_lead_by_raw_phone(normalized_phone)


def claim_canonical_identity(normalized_phone, canonical_lead, allow_adopt=True):
	"""Claim `normalized_phone` for `canonical_lead`.

	Returns the canonical lead name that owns the phone. When the phone is
	already claimed by another lead, the existing claim wins and the caller must
	not create a duplicate lead.

	`allow_adopt` covers the "a lead exists outside P1" case (ADR-P1-01 3.2):
	the phone is not in the identity index yet but a lead already carries that
	mobile number, so the index row is created pointing at that lead.
	"""
	normalized_phone = normalize_vn_phone(normalized_phone)

	existing = find_by_phone(normalized_phone)
	if existing:
		if existing != canonical_lead:
			raise Conflict(
				"The phone number is already bound to another canonical lead.",
				code="DUPLICATE",
				details={"canonical_lead": existing},
			)
		return existing

	if canonical_lead and not frappe.db.exists("CRM Lead", canonical_lead):
		raise NotFound("The canonical lead does not exist.")

	if not canonical_lead:
		canonical_lead = _find_lead_by_raw_phone(normalized_phone)
		if not canonical_lead:
			return None
	else:
		# The caller proposes a new lead, but a lead outside P1 may already
		# carry this number. Never create a second canonical lead for it.
		other = _find_lead_by_raw_phone(normalized_phone)
		if other and other != canonical_lead:
			raise Conflict(
				"The phone number is already used by another lead.",
				code="DUPLICATE",
				details={"canonical_lead": other},
			)

	identity = frappe.get_doc(
		{
			"doctype": "CSKH Identity Index",
			"normalized_phone": normalized_phone,
			"canonical_lead": canonical_lead,
		}
	)
	try:
		identity.insert(ignore_permissions=True)
	except Exception as exc:
		if not _is_duplicate_error(exc):
			raise
		# Two concurrent writers reached the unique index. The database wins.
		winner = find_by_phone(normalized_phone)
		if winner:
			return winner
		raise

	return canonical_lead


def _is_duplicate_error(exc):
	text = str(exc)
	return "Duplicate" in text or "1062" in text or "unique" in text.lower()


def _find_lead_by_raw_phone(normalized_phone):
	"""Find an existing lead whose mobile number normalizes to `normalized_phone`."""
	digits = normalized_phone[3:] if normalized_phone.startswith("+84") else normalized_phone
	candidates = (f"0{digits}", f"84{digits}", f"+84{digits}")
	rows = frappe.db.sql(
		"""select name, mobile_no from `tabCRM Lead`
		   where mobile_no in (%s, %s, %s) limit 5""",
		candidates,
		as_dict=True,
	)
	for row in rows:
		try:
			if normalize_vn_phone(row.mobile_no) == normalized_phone:
				return row.name
		except InvalidPhone:
			continue
	return None


def release_canonical_identity(normalized_phone):
	"""Release an identity claim. Used when a lead is deleted or merged."""
	if not normalized_phone:
		return False
	existing = frappe.db.get_value("CSKH Identity Index", {"normalized_phone": normalized_phone}, "name")
	if not existing:
		return False
	frappe.db.delete("CSKH Identity Index", {"name": existing})
	return True


def ensure_identity_for_lead(lead):
	"""Make sure a lead has an identity row for its current mobile number."""
	lead_phone = frappe.db.get_value("CRM Lead", lead, "mobile_no")
	if not lead_phone:
		return None
	try:
		normalized = normalize_vn_phone(lead_phone)
	except InvalidPhone:
		return None
	existing = find_by_phone(normalized)
	if existing:
		return existing
	return claim_canonical_identity(normalized, lead, allow_adopt=True)


def phone_of_lead(lead):
	"""Return the canonical phone of a lead, or None."""
	identity = find_by_lead(lead)
	if identity:
		return identity.normalized_phone
	lead_phone = frappe.db.get_value("CRM Lead", lead, "mobile_no")
	if not lead_phone:
		return None
	try:
		return normalize_vn_phone(lead_phone)
	except InvalidPhone:
		return None


def identity_snapshot():
	"""Counts used by the dashboard; no phone numbers are returned."""
	return {
		"identities": frappe.db.count("CSKH Identity Index"),
		"as_of": now_datetime(),
	}
