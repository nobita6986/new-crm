"""P2 migration patch: Custom Fields, DB unique/lookup indexes, and canonical seeds.

Idempotent across repeated `bench migrate` runs (`P2.md` §15.1, `docs/data-model-p2.md`).
"""

import frappe

from crm_cskh.p1_models import ensure_indexes
from crm_cskh.p2_models import (
	ensure_p2_custom_fields,
	ensure_p2_indexes,
	ensure_p2_seeds,
	set_lead_dnc_fields,
)
from crm_cskh.services import ownership


def backfill_closed_work_item_owners() -> int:
	"""Backfill `CSKH Work Item.owner_at_close` on `closed` items (`F-P2-04B`, idempotent)."""
	if not (
		frappe.db.table_exists("CSKH Work Item") and frappe.db.has_column("CSKH Work Item", "owner_at_close")
	):
		return 0
	rows = frappe.db.sql(
		"""
		select name, closed_by, last_activity_by
		from `tabCSKH Work Item`
		where status = 'closed' and (owner_at_close is null or owner_at_close = '')
		""",
		as_dict=True,
	)
	if not rows:
		return 0

	wi_names = [r.name for r in rows if r.name]
	close_owner_by_wi: dict[str, str] = {}
	if wi_names and frappe.db.table_exists("CSKH Assignment History"):
		ph = ",".join(["%s"] * len(wi_names))
		hist_rows = frappe.db.sql(
			f"""
			select work_item, from_user
			from `tabCSKH Assignment History`
			where work_item in ({ph}) and action = 'close'
			  and from_user is not null and from_user != ''
			order by event_at desc, creation desc, name desc
			""",
			tuple(wi_names),
			as_dict=True,
		)
		for h in hist_rows:
			if h.work_item and h.from_user:
				close_owner_by_wi.setdefault(h.work_item, h.from_user)

	updated = 0
	for r in rows:
		owner_val = close_owner_by_wi.get(r.name) or r.closed_by or r.last_activity_by
		if owner_val:
			frappe.db.set_value("CSKH Work Item", r.name, "owner_at_close", owner_val, update_modified=False)
			updated += 1
	return updated


def migrate_p2_assignment_history_privacy() -> int:
	"""Scrub pre-fix sensitive reasons & `[H:<16hex>]` tokens on `CSKH Assignment History` (`F-P2-01`).

	Idempotent across repeated runs:
	  1. Extracts legacy `[H:<16hex>]` idempotency prefixes into `payload_hash` (`permlevel = 1`, hidden)
	     before stripping them from display text so duplicate/conflict detection continues to work.
	  2. For `close-att:<wi>:<actor>:<idem>` rows where customer `CSKH Contact Attempt.note` had been
	     auto-copied into `reason` / `completion_reason` / `cskh_dnc_reason`, replaces copied customer
	     notes with the neutral outcome label while preserving the original note in `CSKH Contact Attempt`.
	  3. Moves any non-neutral operator reason into `private_reason` (`permlevel = 1`) and writes a
	     neutral `reason` (`permlevel = 0`) so `CSKH BOD Viewer` sees 0 PII across all 5 planes.
	"""
	if not (
		frappe.db.table_exists("CSKH Assignment History")
		and frappe.db.has_column("CSKH Assignment History", "private_reason")
		and frappe.db.has_column("CSKH Assignment History", "payload_hash")
	):
		return 0

	rows = frappe.db.sql(
		"""
		select name, work_item, action, event_id, reason, private_reason, payload_hash
		from `tabCSKH Assignment History`
		where (reason is not null and reason != '')
		   or (private_reason is not null and private_reason like '[H:%')
		""",
		as_dict=True,
	)
	updated = 0
	has_att_table = frappe.db.table_exists("CSKH Contact Attempt")
	has_wi_comp = frappe.db.has_column("CSKH Work Item", "completion_reason")
	has_lead_dnc_reason = frappe.db.has_column("CRM Lead", "cskh_dnc_reason")

	for h in rows:
		raw_reason = str(h.reason or "").strip()
		raw_priv = str(h.private_reason or "").strip()
		legacy_hash = ownership.extract_legacy_hash_prefix(raw_reason)
		priv_legacy_hash = ownership.extract_legacy_hash_prefix(raw_priv)
		if (
			legacy_hash is None
			and priv_legacy_hash is None
			and ownership.is_neutral_public_history_reason(raw_reason)
		):
			continue

		stripped = ownership.strip_legacy_hash_prefix(raw_reason)
		clean_priv_existing = ownership.strip_legacy_hash_prefix(raw_priv)
		new_hash = str(h.payload_hash or "").strip() or legacy_hash or priv_legacy_hash or None
		event_id = str(h.event_id or "")

		new_pub: str
		new_priv: str | None
		if has_att_table and event_id.startswith("close-att:"):
			parts = event_id.split(":", 3)
			att = None
			if len(parts) == 4:
				_, wi_id, att_actor, idem_key = parts
				att = frappe.db.get_value(
					"CSKH Contact Attempt",
					{"work_item": wi_id, "performed_by": att_actor, "idempotency_key": idem_key},
					["note", "outcome_code", "outcome_label_snapshot", "dnc_snapshot", "payload_hash"],
					as_dict=True,
				)
			if att:
				is_dnc = bool(int(att.dnc_snapshot or 0))
				if not new_hash and att.payload_hash:
					new_hash = att.payload_hash
				att_note = str(att.note or "").strip()
				safe_label = str(att.outcome_label_snapshot or att.outcome_code or "").strip() or (
					"Yêu cầu ngừng liên hệ (DNC)" if is_dnc else "Hoàn tất mục tiêu chăm sóc"
				)
				note_was_copied = bool(att_note and (stripped == att_note or clean_priv_existing == att_note))
				if note_was_copied:
					new_priv = safe_label
					if has_wi_comp and h.work_item:
						wi_row = frappe.db.get_value(
							"CSKH Work Item",
							h.work_item,
							["lead", "completion_reason"],
							as_dict=True,
						)
						if wi_row:
							if str(wi_row.completion_reason or "").strip() == att_note:
								frappe.db.set_value(
									"CSKH Work Item",
									h.work_item,
									"completion_reason",
									None,
									update_modified=False,
								)
							if is_dnc and has_lead_dnc_reason and wi_row.lead:
								cur_dnc_reason = str(
									frappe.db.get_value("CRM Lead", wi_row.lead, "cskh_dnc_reason") or ""
								).strip()
								if cur_dnc_reason == att_note:
									set_lead_dnc_fields(
										wi_row.lead,
										{"cskh_dnc_reason": safe_label},
										update_modified=False,
									)
				else:
					new_priv = clean_priv_existing or stripped or safe_label
				new_pub = ownership.public_history_reason("close", is_dnc=is_dnc)
			else:
				is_dnc = "DNC" in stripped or "ngừng liên hệ" in stripped.lower()
				new_priv = clean_priv_existing or stripped or None
				new_pub = ownership.public_history_reason("close", is_dnc=is_dnc)
		else:
			action_str = str(h.action or "").strip().lower()
			is_dnc = (action_str == "close") and ("DNC" in stripped or "ngừng liên hệ" in stripped.lower())
			override_dnc = (action_str == "reopen") and ("DNC" in stripped or "dnc" in stripped.lower())
			new_pub = ownership.public_history_reason(action_str, is_dnc=is_dnc, override_dnc=override_dnc)
			new_priv = clean_priv_existing or (
				None if ownership.is_neutral_public_history_reason(stripped) else (stripped or None)
			)

		frappe.db.set_value(
			"CSKH Assignment History",
			h.name,
			{
				"reason": new_pub,
				"private_reason": new_priv,
				"payload_hash": new_hash,
			},
			update_modified=False,
		)
		updated += 1
	return updated


def execute():
	ensure_p2_custom_fields()
	if frappe.db.table_exists("CSKH Work Item"):
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
	backfill_closed_work_item_owners()
	migrate_p2_assignment_history_privacy()
	ensure_p2_seeds()
	frappe.db.commit()
