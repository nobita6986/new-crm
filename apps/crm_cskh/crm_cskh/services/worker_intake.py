"""Self-sourced worker intake & instant phone ownership protection for Marketing staff.

Enforces the KCN Marketing business rules:
  1. Marketing staff (`Sales User`) and `CSKH CRM Admin` may add worker phone numbers
     they source themselves (Facebook, TikTok, Zalo, KCN groups, worker referrals).
  2. Every phone number is normalized to canonical `+84...` (`normalize_vn_phone`) and
     checked against `CSKH Identity Index` + `CSKH Active Claim` inside a DB savepoint.
  3. If the phone is currently protected by another staff member's active Work Item,
     intake fails closed with `ALREADY_ASSIGNED` and leaks zero PII/notes of the colleague.
  4. If available (brand-new or previous care cycle already closed), the canonical
     `CRM Lead`, `CSKH Identity Index`, `CSKH Work Item`, `CSKH Active Claim`,
     `CSKH Assignment History`, `ToDo`, and `DocShare` are bound atomically to the caller.
"""

from __future__ import annotations

import re

import frappe
from frappe.utils import cint, now_datetime

from crm_cskh.api._params import parse_int, parse_optional_str
from crm_cskh.p1_models import ACTIVE_WORK_ITEM_STATES, OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.patches.v5_0.kcn_worker_care_7days import (
	SELF_SOURCED_CAMPAIGN_CODE,
	ensure_self_sourced_default_campaign,
)
from crm_cskh.services import identity as identity_service
from crm_cskh.services import ownership, runtime_cache
from crm_cskh.services.errors import (
	Conflict,
	DoNotContact,
	Forbidden,
	InvalidPhoneError,
	InvalidRequest,
	NotFound,
	rollback_savepoint_safely,
)
from crm_cskh.services.import_service import sanitize_cell
from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone
from crm_cskh.services.sanitizer import sanitize_note_text

PLACEHOLDER_FIRST_NAME = "Chưa rõ tên"
MAX_BULK_PASTE_LINES = 100
VALID_GENDERS = ("", "Nam", "Nữ", "Khác")
VALID_ID_CARD_STATUSES = ("", "Có CCCD gốc", "VNeID", "Thiếu giấy tờ")


def to_national_phone(phone: str | None) -> str:
	"""Format `+84912345678` into familiar Vietnamese `0912345678` for Gọi / Zalo / Copy."""
	if not phone:
		return ""
	raw = str(phone).strip()
	if raw.startswith("+84") and len(raw) == 12 and raw[3:].isdigit():
		return "0" + raw[3:]
	if raw.startswith("84") and len(raw) == 11 and raw[2:].isdigit():
		return "0" + raw[2:]
	return raw


def _assert_intake_actor(actor: str | None = None) -> str:
	actor = actor or frappe.session.user
	if not actor or actor == "Guest":
		raise Forbidden("Bạn cần đăng nhập để thực hiện thao tác này.")
	if actor == "Administrator":
		return actor
	roles = set(runtime_cache.get_roles_set(actor))
	if "System Manager" in roles or "CSKH CRM Admin" in roles or "Sales User" in roles:
		if not frappe.db.get_value("User", actor, "enabled"):
			raise Forbidden("Tài khoản của bạn đang bị vô hiệu hóa.")
		return actor
	raise Forbidden("Chỉ Nhân viên Marketing hoặc Quản trị viên mới được thêm người lao động.")


def list_intake_campaigns(actor: str | None = None) -> dict:
	"""Active KCN campaigns / factory job orders selectable when adding or consulting workers."""
	_assert_intake_actor(actor)
	default_camp = ensure_self_sourced_default_campaign()

	fields = ["name", "code", "title", "description", "status"]
	for opt_col in (
		"factory_name",
		"industrial_zone",
		"recruitment_criteria",
		"target_workdays",
		"allow_self_sourced",
	):
		if frappe.db.has_column("CSKH Campaign", opt_col):
			fields.append(opt_col)

	rows = frappe.db.get_all(
		"CSKH Campaign",
		filters={"status": "Active"},
		fields=fields,
		order_by="creation desc",
	)
	items = []
	for row in rows:
		allow_self = cint(row.get("allow_self_sourced", 1)) if "allow_self_sourced" in row else 1
		if not allow_self and row.code != SELF_SOURCED_CAMPAIGN_CODE:
			continue
		items.append(
			{
				"campaign": row.name,
				"code": row.code,
				"title": row.title,
				"factory_name": row.get("factory_name") or "",
				"industrial_zone": row.get("industrial_zone") or "",
				"recruitment_criteria": row.get("recruitment_criteria") or row.get("description") or "",
				"target_workdays": max(cint(row.get("target_workdays") or 7), 1),
				"is_default_self_sourced": bool(
					row.name == default_camp or row.code == SELF_SOURCED_CAMPAIGN_CODE
				),
			}
		)
	items.sort(key=lambda item: (0 if item["is_default_self_sourced"] else 1, item["code"] or ""))
	return {
		"default_campaign": default_camp,
		"items": items,
		"total": len(items),
		"as_of": now_datetime(),
	}


def _resolve_active_protection_for_lead(lead_name: str) -> dict | None:
	"""Return active Work Item / Claim holding protection on `lead_name`, or None."""
	claim = frappe.db.get_value(
		"CSKH Active Claim",
		{"lead": lead_name},
		["name", "campaign", "work_item"],
		as_dict=True,
	)
	if claim and claim.work_item:
		wi = frappe.db.get_value(
			"CSKH Work Item",
			claim.work_item,
			["name", "campaign", "status", "assigned_to", "worker_stage"],
			as_dict=True,
		)
		if wi and wi.status in ACTIVE_WORK_ITEM_STATES:
			return wi

	active_rows = frappe.db.get_all(
		"CSKH Work Item",
		filters={"lead": lead_name, "status": ["in", list(ACTIVE_WORK_ITEM_STATES)]},
		fields=["name", "campaign", "status", "assigned_to", "worker_stage"],
		order_by="modified desc",
		limit_page_length=1,
	)
	return active_rows[0] if active_rows else None


def check_phone_protection(phone: str | None, actor: str | None = None) -> dict:
	"""Check whether a Vietnamese mobile number is free to claim, owned by caller, or protected."""
	actor = _assert_intake_actor(actor)
	raw_phone = parse_optional_str(phone, "Số điện thoại không hợp lệ.")
	if not raw_phone:
		raise InvalidPhoneError("Vui lòng nhập số điện thoại người lao động.")
	try:
		normalized = normalize_vn_phone(raw_phone)
	except InvalidPhone as exc:
		raise InvalidPhoneError(
			"Số điện thoại không đúng chuẩn di động Việt Nam (10 số bắt đầu bằng 03, 05, 07, 08, 09)."
		) from exc

	national = to_national_phone(normalized)
	lead_name = identity_service.find_by_phone(normalized) or identity_service.find_lead_by_phone(normalized)
	if not lead_name:
		return {
			"valid": True,
			"status": "available",
			"can_claim": True,
			"normalized_phone": normalized,
			"national_phone": national,
			"message": "Số điện thoại hợp lệ, chưa có người bảo hộ — có thể thêm ngay.",
		}

	if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and cint(
		frappe.db.get_value("CRM Lead", lead_name, "cskh_do_not_contact")
	):
		return {
			"valid": True,
			"status": "do_not_contact",
			"can_claim": False,
			"normalized_phone": normalized,
			"national_phone": national,
			"message": "Số điện thoại này đã yêu cầu ngừng liên hệ (DNC), không thể thêm mới.",
		}

	active_wi = _resolve_active_protection_for_lead(lead_name)
	if active_wi:
		if active_wi.assigned_to == actor:
			camp_title = (
				frappe.db.get_value("CSKH Campaign", active_wi.campaign, "title")
				if active_wi.campaign
				else ""
			)
			return {
				"valid": True,
				"status": "owned_by_me",
				"can_claim": False,
				"work_item": active_wi.name,
				"campaign": active_wi.campaign,
				"campaign_title": camp_title or "",
				"worker_stage": active_wi.get("worker_stage") or "new_contact",
				"normalized_phone": normalized,
				"national_phone": national,
				"message": "Số điện thoại này đang do chính bạn phụ trách bảo hộ.",
			}
		# Strictly 0 PII / 0 colleague notes leaked when protected by another user.
		return {
			"valid": True,
			"status": "protected_other",
			"can_claim": False,
			"normalized_phone": normalized,
			"national_phone": national,
			"message": "Trùng bảo hộ: Số điện thoại này đang được nhân viên khác chăm sóc.",
		}

	return {
		"valid": True,
		"status": "reusable",
		"can_claim": True,
		"normalized_phone": normalized,
		"national_phone": national,
		"message": "Số điện thoại từng có trong hệ thống nhưng hiện không có bảo hộ mở — có thể nhận chăm sóc.",
	}


def _clean_profile_inputs(
	*,
	full_name: str | None = None,
	birth_year=None,
	gender: str | None = None,
	current_area: str | None = None,
	id_card_status: str | None = None,
	worker_tags: str | None = None,
	zalo_phone: str | None = None,
	acquisition_channel: str | None = None,
	target_factory: str | None = None,
	target_industrial_zone: str | None = None,
) -> dict:
	clean_name = sanitize_cell(parse_optional_str(full_name, "Họ tên không hợp lệ.") or "")
	if len(clean_name) > 140:
		clean_name = clean_name[:140].strip()

	byear = None
	if birth_year is not None and str(birth_year).strip() != "":
		byear = parse_int(birth_year, minimum=1950, field="birth_year")
		if byear > 2012:
			raise InvalidRequest("Năm sinh người lao động không hợp lệ (1950-2012).")

	gen_raw = (parse_optional_str(gender, "Giới tính không hợp lệ.") or "").strip()
	gen_map = {"Nu": "Nữ", "Khac": "Khác"}
	gen_val = gen_map.get(gen_raw, gen_raw)
	if gen_val not in VALID_GENDERS:
		raise InvalidRequest("Giới tính chỉ chấp nhận Nam, Nữ hoặc Khác.")

	id_raw = (parse_optional_str(id_card_status, "Tình trạng CCCD không hợp lệ.") or "").strip()
	id_map = {
		"Co CCCD goc": "Có CCCD gốc",
		"VNeID muc 2": "VNeID",
		"Mat / dang lam lai": "Thiếu giấy tờ",
	}
	id_status = id_map.get(id_raw, id_raw)
	if id_status not in VALID_ID_CARD_STATUSES:
		raise InvalidRequest("Tình trạng CCCD không hợp lệ.")

	area_val = sanitize_cell(parse_optional_str(current_area, "Khu vực không hợp lệ.") or "")[:140]
	tags_val = sanitize_note_text(
		parse_optional_str(worker_tags, "Ghi chú sàng lọc không hợp lệ."), max_length=1000
	)
	zalo_val = sanitize_cell(
		parse_optional_str(zalo_phone, "Số Zalo không hợp lệ.") or "", allow_leading_sign=True
	)[:64]
	acq_val = sanitize_cell(parse_optional_str(acquisition_channel, "Nguồn khai thác không hợp lệ.") or "")[
		:100
	]
	fac_val = sanitize_cell(parse_optional_str(target_factory, "Nhà máy không hợp lệ.") or "")[:140]
	iz_val = sanitize_cell(parse_optional_str(target_industrial_zone, "Khu công nghiệp không hợp lệ.") or "")[
		:140
	]
	return {
		"full_name": clean_name,
		"birth_year": byear,
		"gender": gen_val,
		"current_area": area_val,
		"id_card_status": id_status,
		"worker_tags": tags_val,
		"zalo_phone": zalo_val,
		"acquisition_channel": acq_val or "Tự khai thác",
		"target_factory": fac_val,
		"target_industrial_zone": iz_val,
	}


def _apply_lead_profile_fields(lead_name: str, cleaned: dict, *, overwrite_existing: bool = True) -> None:
	lead_updates: dict = {}
	cur_lead = frappe.db.get_value(
		"CRM Lead",
		lead_name,
		["first_name", "lead_name", "cskh_is_placeholder_name"]
		+ [
			col
			for col in (
				"cskh_birth_year",
				"cskh_gender",
				"cskh_current_area",
				"cskh_id_card_status",
				"cskh_worker_tags",
				"cskh_zalo_phone",
			)
			if frappe.db.has_column("CRM Lead", col)
		],
		as_dict=True,
	)
	if not cur_lead:
		return

	is_placeholder = bool(cint(cur_lead.get("cskh_is_placeholder_name") or 0))
	if cleaned["full_name"] and (overwrite_existing or is_placeholder or not cur_lead.get("lead_name")):
		lead_updates["first_name"] = cleaned["full_name"]
		lead_updates["last_name"] = ""
		lead_updates["lead_name"] = cleaned["full_name"]
		if frappe.db.has_column("CRM Lead", "cskh_is_placeholder_name"):
			lead_updates["cskh_is_placeholder_name"] = 0

	field_map = (
		("cskh_birth_year", "birth_year"),
		("cskh_gender", "gender"),
		("cskh_current_area", "current_area"),
		("cskh_id_card_status", "id_card_status"),
		("cskh_worker_tags", "worker_tags"),
		("cskh_zalo_phone", "zalo_phone"),
	)
	for db_col, key in field_map:
		if not frappe.db.has_column("CRM Lead", db_col):
			continue
		val = cleaned.get(key)
		if val is None or val == "":
			continue
		if overwrite_existing or not cur_lead.get(db_col):
			lead_updates[db_col] = val

	if lead_updates:
		frappe.db.set_value("CRM Lead", lead_name, lead_updates, update_modified=True)


def _resolve_target_campaign(campaign: str | None) -> dict:
	camp_val = parse_optional_str(campaign, "Mã chiến dịch/đơn hàng không hợp lệ.")
	if not camp_val:
		camp_val = ensure_self_sourced_default_campaign()
	if not camp_val:
		raise NotFound("Chưa tìm thấy chiến dịch mặc định cho nguồn tự khai thác.")

	cols = ["name", "code", "title", "status"]
	for opt in ("factory_name", "industrial_zone", "target_workdays", "allow_self_sourced"):
		if frappe.db.has_column("CSKH Campaign", opt):
			cols.append(opt)
	camp_doc = frappe.db.get_value("CSKH Campaign", camp_val, cols, as_dict=True)
	if not camp_doc:
		camp_doc = frappe.db.get_value("CSKH Campaign", {"code": camp_val}, cols, as_dict=True)
	if not camp_doc:
		raise NotFound("Không tìm thấy đơn hàng / chiến dịch KCN đã chọn.")
	if camp_doc.status != "Active":
		raise Conflict("Đơn hàng / chiến dịch này hiện không ở trạng thái Đang hoạt động.", code="CONFLICT")
	if (
		"allow_self_sourced" in camp_doc
		and not cint(camp_doc.get("allow_self_sourced", 1))
		and camp_doc.code != SELF_SOURCED_CAMPAIGN_CODE
	):
		raise Forbidden("Chiến dịch này không cho phép tự thêm SĐT trực tiếp.")
	return camp_doc


def add_self_sourced_worker(
	*,
	phone: str | None,
	full_name: str | None = None,
	campaign: str | None = None,
	acquisition_channel: str | None = None,
	birth_year=None,
	gender: str | None = None,
	current_area: str | None = None,
	id_card_status: str | None = None,
	worker_tags: str | None = None,
	zalo_phone: str | None = None,
	target_factory: str | None = None,
	target_industrial_zone: str | None = None,
	request_id: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Atomically normalize phone, check company-wide protection, and assign protection to `actor`."""
	actor = _assert_intake_actor(actor)
	req_id = (
		parse_optional_str(request_id, "Mã request_id không hợp lệ.")
		or f"self-{frappe.generate_hash(length=12)}"
	)
	raw_phone = parse_optional_str(phone, "Số điện thoại không hợp lệ.")
	if not raw_phone:
		raise InvalidPhoneError("Vui lòng nhập số điện thoại người lao động.")
	try:
		normalized = normalize_vn_phone(raw_phone)
	except InvalidPhone as exc:
		raise InvalidPhoneError(
			"Số điện thoại không đúng chuẩn di động Việt Nam (10 số bắt đầu bằng 03, 05, 07, 08, 09)."
		) from exc

	cleaned = _clean_profile_inputs(
		full_name=full_name,
		birth_year=birth_year,
		gender=gender,
		current_area=current_area,
		id_card_status=id_card_status,
		worker_tags=worker_tags,
		zalo_phone=zalo_phone,
		acquisition_channel=acquisition_channel,
		target_factory=target_factory,
		target_industrial_zone=target_industrial_zone,
	)
	camp_doc = _resolve_target_campaign(campaign)
	camp_name = str(camp_doc.name)
	target_days = max(cint(camp_doc.get("target_workdays") or 7), 1)
	eff_factory = cleaned["target_factory"] or (camp_doc.get("factory_name") or "")
	eff_iz = cleaned["target_industrial_zone"] or (camp_doc.get("industrial_zone") or "")

	savepoint = f"cskh_intake_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(savepoint)
	try:
		lead_name = identity_service.find_by_phone_current(normalized) or identity_service.find_lead_by_phone(
			normalized
		)
		created_new_lead = False
		if not lead_name:
			first_name_val = cleaned["full_name"] or PLACEHOLDER_FIRST_NAME
			lead_payload = {
				"doctype": "CRM Lead",
				"first_name": first_name_val,
				"lead_name": first_name_val,
				"mobile_no": normalized,
				"status": "New",
			}
			if frappe.db.has_column("CRM Lead", "cskh_is_placeholder_name"):
				lead_payload["cskh_is_placeholder_name"] = 0 if cleaned["full_name"] else 1
			lead_doc = frappe.get_doc(lead_payload)
			lead_doc.insert(ignore_permissions=True)
			lead_name = str(lead_doc.name)
			claimed_lead = identity_service.claim_canonical_identity(normalized, lead_name, allow_adopt=True)
			if claimed_lead != lead_name:
				# Lost concurrent race on `CSKH Identity Index`: delete our orphan lead and use winner.
				frappe.db.delete("CRM Lead", {"name": lead_name})
				lead_name = str(claimed_lead)
			else:
				created_new_lead = True
		else:
			identity_service.ensure_identity_for_lead(lead_name)

		# Lock canonical Lead row to serialize concurrent claim checks on the same phone number.
		frappe.db.sql("select name from `tabCRM Lead` where name = %s for update", (lead_name,))

		if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and cint(
			frappe.db.get_value("CRM Lead", lead_name, "cskh_do_not_contact")
		):
			raise DoNotContact("Người lao động này đã yêu cầu ngừng liên hệ (DNC); không thể thêm mới.")

		active_wi = _resolve_active_protection_for_lead(lead_name)
		if active_wi:
			if active_wi.assigned_to == actor:
				_apply_lead_profile_fields(lead_name, cleaned, overwrite_existing=False)
				return {
					"status": "already_owned_by_me",
					"created_lead": False,
					"work_item": active_wi.name,
					"lead": lead_name,
					"campaign": active_wi.campaign,
					"normalized_phone": normalized,
					"national_phone": to_national_phone(normalized),
					"assigned_to": actor,
					"worker_stage": active_wi.get("worker_stage") or "new_contact",
				}
			raise Conflict(
				"Trùng bảo hộ: Số điện thoại này đang được nhân viên khác chăm sóc.",
				code="ALREADY_ASSIGNED",
			)

		# Phone is completely free to claim! Apply profile details on `CRM Lead`.
		_apply_lead_profile_fields(lead_name, cleaned, overwrite_existing=created_new_lead)

		now = now_datetime()
		# Check if `(camp_name, lead_name)` already had an older closed/revoked Work Item
		existing_pair_wi = frappe.db.get_value(
			"CSKH Work Item",
			{"campaign": camp_name, "lead": lead_name},
			["name", "status", "revision"],
			as_dict=True,
		)
		if existing_pair_wi:
			wi_name = str(existing_pair_wi.name)
			next_rev = cint(existing_pair_wi.revision or 1) + 1
			wi_updates = {
				"status": "assigned",
				"assigned_to": actor,
				"assigned_at": now,
				"open_callback_task": None,
				"next_action_due": None,
				"reopened_at": now,
				"reopened_by": actor,
				"reopen_reason": "Tự khai thác lại khi không còn bảo hộ active",
				"last_activity_by": actor,
				"revision": next_rev,
			}
			for col, val in (
				("source_type", "self_sourced"),
				("acquisition_channel", cleaned["acquisition_channel"]),
				("target_factory", eff_factory or None),
				("target_industrial_zone", eff_iz or None),
				("worker_stage", "new_contact"),
				("interview_date", None),
				("onboard_date", None),
				("workdays_completed", 0),
				("target_workdays", target_days),
				("last_workday_logged_on", None),
				("completed_7d_at", None),
				("dropout_reason", None),
			):
				if frappe.db.has_column("CSKH Work Item", col):
					wi_updates[col] = val
			frappe.db.set_value("CSKH Work Item", wi_name, wi_updates, update_modified=True)
			hist_action = "reopen"
			pub_reason = ownership.public_history_reason("reopen")
		else:
			wi_doc_payload = {
				"doctype": "CSKH Work Item",
				"campaign": camp_name,
				"lead": lead_name,
				"status": "assigned",
				"revision": 1,
				"assigned_to": actor,
				"assigned_at": now,
				"attempt_count": 0,
				"connected_attempt_count": 0,
				"last_activity_by": actor,
				"policy_version": 1,
			}
			for col, val in (
				("source_type", "self_sourced"),
				("acquisition_channel", cleaned["acquisition_channel"]),
				("target_factory", eff_factory or None),
				("target_industrial_zone", eff_iz or None),
				("worker_stage", "new_contact"),
				("workdays_completed", 0),
				("target_workdays", target_days),
			):
				if frappe.db.has_column("CSKH Work Item", col):
					wi_doc_payload[col] = val
			wi_doc = frappe.get_doc(wi_doc_payload)
			wi_doc.insert(ignore_permissions=True)
			wi_name = str(wi_doc.name)
			hist_action = "assign"
			pub_reason = ownership.public_history_reason("assign")

		# Remove any stale orphan claim row and insert authoritative `CSKH Active Claim`
		frappe.db.delete("CSKH Active Claim", {"lead": lead_name})
		claim_doc = frappe.get_doc(
			{
				"doctype": "CSKH Active Claim",
				"lead": lead_name,
				"campaign": camp_name,
				"work_item": wi_name,
			}
		)
		claim_doc.insert(ignore_permissions=True)

		ownership._close_open_interval(wi_name, now)
		ev_id = f"self-intake:{wi_name}:{actor}:{req_id}"
		if not ownership._find_history_event(ev_id):
			ownership._insert_history(
				{
					"doctype": "CSKH Assignment History",
					"work_item": wi_name,
					"action": hist_action,
					"from_user": None,
					"to_user": actor,
					"actor": actor,
					"event_at": now,
					"reason": pub_reason,
					"private_reason": f"Nhân viên tự khai thác nguồn ({cleaned['acquisition_channel']})",
					"ownership_start": now,
					"ownership_end": None,
					"event_id": ev_id,
				}
			)

		ownership._sync_lead_owner(lead_name, actor)
		ownership._cancel_stale_lead_todos(lead_name, keep_user=actor)
		if not ownership._todo_for(lead_name, actor):
			ownership._create_todo(lead_name, actor, f"CSKH assignment ({wi_name})")
		ownership._sync_docshares(lead_name, [actor])
		ownership._sync_lead_owner(lead_name, actor)
		ownership._assert_final_owner(lead_name, actor)
		runtime_cache.bump_cache_domain(runtime_cache.DOMAIN_WORK_ITEM)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	display_name = (
		frappe.db.get_value("CRM Lead", lead_name, "lead_name")
		or cleaned["full_name"]
		or PLACEHOLDER_FIRST_NAME
	)
	return {
		"status": "claimed",
		"created_lead": created_new_lead,
		"work_item": wi_name,
		"lead": lead_name,
		"display_name": display_name,
		"campaign": camp_name,
		"campaign_title": camp_doc.get("title") or camp_name,
		"target_factory": eff_factory,
		"target_industrial_zone": eff_iz,
		"normalized_phone": normalized,
		"national_phone": to_national_phone(normalized),
		"assigned_to": actor,
		"worker_stage": "new_contact",
		"target_workdays": target_days,
	}


_BULK_SPLIT_RE = re.compile(r"[|\t,;]+")


def bulk_add_self_sourced_workers(
	*,
	lines_text: str | None,
	campaign: str | None = None,
	acquisition_channel: str | None = None,
	request_id: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Paste multiple worker lines (`SĐT | Họ tên | Năm sinh | Ghi chú`) and claim protection for each."""
	actor = _assert_intake_actor(actor)
	raw_block = parse_optional_str(lines_text, "Danh sách dòng nhập không hợp lệ.") or ""
	lines = [ln.strip() for ln in raw_block.splitlines() if ln.strip()]
	if not lines:
		raise InvalidRequest("Vui lòng dán ít nhất 1 dòng số điện thoại người lao động.")
	if len(lines) > MAX_BULK_PASTE_LINES:
		raise InvalidRequest(f"Mỗi lần dán nhanh hỗ trợ tối đa {MAX_BULK_PASTE_LINES} dòng.")

	base_req = (
		parse_optional_str(request_id, "Mã request_id không hợp lệ.")
		or f"bulk-{frappe.generate_hash(length=10)}"
	)
	camp_doc = _resolve_target_campaign(campaign)

	counts = {
		"total": len(lines),
		"claimed": 0,
		"already_owned_by_me": 0,
		"protected_other": 0,
		"do_not_contact": 0,
		"invalid": 0,
	}
	results = []

	for idx, line in enumerate(lines, start=1):
		parts = [p.strip() for p in _BULK_SPLIT_RE.split(line)]
		phone_part = parts[0] if parts else ""
		name_part = parts[1] if len(parts) > 1 else ""
		year_part = None
		notes_part = ""
		if len(parts) > 2:
			if parts[2].isdigit() and len(parts[2]) == 4:
				year_part = int(parts[2])
				notes_part = " · ".join(p for p in parts[3:] if p)
			else:
				notes_part = " · ".join(p for p in parts[2:] if p)

		try:
			res = add_self_sourced_worker(
				phone=phone_part,
				full_name=name_part or None,
				campaign=camp_doc.name,
				acquisition_channel=acquisition_channel,
				birth_year=year_part,
				worker_tags=notes_part or None,
				request_id=f"{base_req}:L{idx}",
				actor=actor,
			)
			st = res["status"]
			counts[st] = counts.get(st, 0) + 1
			results.append(
				{
					"line": idx,
					"raw_phone": phone_part,
					"national_phone": res.get("national_phone"),
					"display_name": res.get("display_name") or name_part,
					"status": st,
					"work_item": res.get("work_item"),
					"message": (
						"Đã thêm & gắn bảo hộ thành công"
						if st == "claimed"
						else "Đang thuộc quyền bảo hộ của bạn"
					),
				}
			)
		except Conflict:
			counts["protected_other"] += 1
			results.append(
				{
					"line": idx,
					"raw_phone": phone_part,
					"status": "protected_other",
					"message": "Trùng bảo hộ: đang do nhân viên khác chăm sóc",
				}
			)
		except DoNotContact:
			counts["do_not_contact"] += 1
			results.append(
				{
					"line": idx,
					"raw_phone": phone_part,
					"status": "do_not_contact",
					"message": "Ngừng liên hệ (DNC)",
				}
			)
		except (InvalidPhoneError, InvalidRequest) as exc:
			counts["invalid"] += 1
			results.append(
				{
					"line": idx,
					"raw_phone": phone_part,
					"status": "invalid",
					"message": str(exc),
				}
			)

	return {
		"campaign": camp_doc.name,
		"campaign_title": camp_doc.get("title") or camp_doc.name,
		"counts": counts,
		"protected_count": counts["claimed"],
		"already_mine_count": counts["already_owned_by_me"],
		"conflict_count": counts["protected_other"] + counts["do_not_contact"],
		"invalid_count": counts["invalid"],
		"results": results,
		"as_of": now_datetime(),
	}
