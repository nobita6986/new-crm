"""Adapter for native `CRM Call Log` reuse (`P2.md` §7.1, `ADR-P2-04`, `docs/calllog-task-reuse.md`).

Rules:
  * `call_log` or `call_log_data` is ONLY compatible with `channel == "phone_call"`; passing either on `sms`/`zalo`/`facebook`/`other` raises `422 OUTCOME_NOT_COMPATIBLE` (never create fake phone logs for non-voice outreach).
  * Linking an existing `CRM Call Log`:
    - Must exist and belong to the same `CRM Lead` (`reference_doctype == "CRM Lead"` and `reference_docname == lead`).
    - Must NOT already be linked to another `CSKH Contact Attempt` (`409 ALREADY_LINKED`, backed by MariaDB `UNIQUE(call_log)`).
    - If `CRM Call Log.status == "Completed"` and `duration > 0` (meaning PBX/telephony confirmed a connected call), returns `verification_status = "verified"` when not overridden; otherwise `"self_reported"`.
  * Creating a new `CRM Call Log` from `call_log_data`:
    - Only created when truthful mandatory fields (`from`/`caller_number`, `to`/`receiver_number`, `type` in `Incoming|Outgoing`, `status` in valid native `CRM Call Log` statuses) are present; missing/invalid fields raise `400 INVALID_REQUEST` instead of inventing fake phone numbers.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from crm_cskh.api._params import parse_optional_str
from crm_cskh.p2_permissions import assert_work_item_workflow_access
from crm_cskh.services.errors import AlreadyLinked, InvalidRequest, NotFound, OutcomeNotCompatible
from crm_cskh.services.sanitizer import sanitize_note_text

VALID_CALL_TYPES = ("Incoming", "Outgoing")
VALID_CALL_STATUSES = (
	"Initiated",
	"Ringing",
	"In Progress",
	"Completed",
	"Failed",
	"Busy",
	"No Answer",
	"Queued",
	"Canceled",
)


def parse_call_log_data(raw) -> dict | None:
	if raw is None or raw == "":
		return None
	if isinstance(raw, str):
		try:
			parsed = json.loads(raw)
		except (TypeError, ValueError) as exc:
			raise InvalidRequest("Tham số call_log_data phải là JSON object hợp lệ.") from exc
	elif isinstance(raw, dict):
		parsed = raw
	else:
		raise InvalidRequest("Tham số call_log_data phải là JSON object.")
	if not isinstance(parsed, dict):
		raise InvalidRequest("Tham số call_log_data phải là JSON object.")
	return parsed or None


def resolve_or_create_call_log_in_tx(
	*,
	channel: str,
	lead: str,
	actor: str,
	call_log: str | None = None,
	call_log_data: dict | str | None = None,
	outcome_code: str | None = None,
) -> tuple[str | None, str]:
	"""Validate/link an existing `CRM Call Log` or create a truthful one inside caller's transaction.

	Returns `(call_log_name_or_none, verification_status)`.
	"""
	call_log_id = parse_optional_str(call_log, "Mã CRM Call Log không hợp lệ.")
	parsed_data = parse_call_log_data(call_log_data)

	if not call_log_id and not parsed_data:
		return None, "self_reported"

	if channel != "phone_call":
		raise OutcomeNotCompatible("Chỉ kênh gọi thoại (phone_call) mới được gắn hoặc tạo CRM Call Log.")
	if call_log_id and parsed_data:
		raise InvalidRequest("Chỉ truyền một trong hai tham số call_log hoặc call_log_data.")

	if call_log_id:
		row = frappe.db.sql(
			"""
			select name, `from` as caller_no, `to` as receiver_no, `type`, status, duration,
			       reference_doctype, reference_docname, telephony_medium as medium
			from `tabCRM Call Log`
			where name = %s
			for update
			"""
			if frappe.db.has_column("CRM Call Log", "telephony_medium")
			else """
			select name, `from` as caller_no, `to` as receiver_no, `type`, status, duration,
			       reference_doctype, reference_docname
			from `tabCRM Call Log`
			where name = %s
			for update
			""",
			(call_log_id,),
			as_dict=True,
		)
		if not row:
			raise NotFound("Không tìm thấy bản ghi CRM Call Log.")
		call_row = row[0]
		if call_row.reference_doctype != "CRM Lead" or call_row.reference_docname != lead:
			raise OutcomeNotCompatible(
				"Bản ghi CRM Call Log không thuộc khách hàng (CRM Lead) của Work Item này."
			)

		already = frappe.db.get_value("CSKH Contact Attempt", {"call_log": call_log_id}, "name")
		if already:
			raise AlreadyLinked(
				"Bản ghi CRM Call Log này đã được gắn với một nhật ký liên hệ khác.",
				details={"call_log": call_log_id},
			)

		# Only a genuinely PBX/Twilio/Exotel-backed Call Log (not Manual) can auto-verify;
		# manual logs stay `self_reported` so Staff cannot spoof `verified` (`SEC2-02`).
		medium = (call_row.get("medium") or "").strip().lower()
		is_telephony_verified = (
			bool(medium and medium != "manual")
			and call_row.status == "Completed"
			and cint(call_row.duration or 0) > 0
		)
		return call_log_id, ("verified" if is_telephony_verified else "self_reported")

	# `parsed_data` branch: create a truthful `CRM Call Log` only when required fields are supplied.
	assert parsed_data is not None
	from_no = parse_optional_str(
		parsed_data.get("from") or parsed_data.get("caller_number"),
		"Số gọi đi (from) không hợp lệ.",
	)
	to_no = parse_optional_str(
		parsed_data.get("to") or parsed_data.get("receiver_number"),
		"Số nhận (to) không hợp lệ.",
	)
	call_type = (
		parse_optional_str(parsed_data.get("type"), "Loại cuộc gọi (type) không hợp lệ.") or "Outgoing"
	)
	call_status = parse_optional_str(parsed_data.get("status"), "Trạng thái cuộc gọi (status) không hợp lệ.")

	if not call_status:
		# Default truthful call status from outcome if caller omitted explicit status
		if outcome_code == "no_answer":
			call_status = "No Answer"
		elif outcome_code == "busy":
			call_status = "Busy"
		elif outcome_code in ("unreachable", "wrong_number"):
			call_status = "Failed"
		else:
			call_status = "Completed"

	if not from_no or not to_no:
		raise InvalidRequest(
			"Tạo CRM Call Log yêu cầu đầy đủ số gọi (from) và số nhận (to) thực tế; không tự sinh số giả."
		)
	if not frappe.utils.validate_phone_number(from_no, throw=False) or not frappe.utils.validate_phone_number(
		to_no, throw=False
	):
		raise InvalidRequest(
			"Số gọi (from) và số nhận (to) của CRM Call Log phải đúng định dạng số điện thoại."
		)
	if call_type not in VALID_CALL_TYPES:
		raise InvalidRequest("Loại cuộc gọi (type) chỉ chấp nhận Incoming hoặc Outgoing.")
	if call_status not in VALID_CALL_STATUSES:
		raise InvalidRequest("Trạng thái cuộc gọi (status) của CRM Call Log không hợp lệ.")

	raw_duration = parsed_data.get("duration")
	duration_sec = 0
	if raw_duration is not None and str(raw_duration).strip() != "":
		try:
			duration_sec = int(raw_duration)
		except (TypeError, ValueError) as exc:
			raise InvalidRequest("Thời lượng cuộc gọi (duration) phải là số nguyên giây >= 0.") from exc
		if duration_sec < 0:
			raise InvalidRequest("Thời lượng cuộc gọi (duration) không được âm.")

	start_dt = None
	if parsed_data.get("start_time"):
		try:
			start_dt = get_datetime(parsed_data.get("start_time"))
		except Exception as exc:
			raise InvalidRequest("Thời điểm bắt đầu cuộc gọi (start_time) không hợp lệ.") from exc
	end_dt = None
	if parsed_data.get("end_time"):
		try:
			end_dt = get_datetime(parsed_data.get("end_time"))
		except Exception as exc:
			raise InvalidRequest("Thời điểm kết thúc cuộc gọi (end_time) không hợp lệ.") from exc
	if start_dt and end_dt and end_dt < start_dt:
		raise InvalidRequest("end_time của cuộc gọi không được nhỏ hơn start_time.")

	call_id = parse_optional_str(parsed_data.get("id")) or f"cskh-call-{frappe.generate_hash(length=12)}"
	if frappe.db.exists("CRM Call Log", call_id) or frappe.db.exists("CRM Call Log", {"id": call_id}):
		raise AlreadyLinked("Mã định danh cuộc gọi (id) đã tồn tại trên hệ thống.")

	doc_dict = {
		"doctype": "CRM Call Log",
		"id": call_id,
		"type": call_type,
		"status": call_status,
		"from": from_no.strip()[:32],
		"to": to_no.strip()[:32],
		"duration": duration_sec,
		"start_time": start_dt,
		"end_time": end_dt,
		"reference_doctype": "CRM Lead",
		"reference_docname": lead,
		"caller": actor if call_type == "Outgoing" else None,
		"receiver": actor if call_type == "Incoming" else None,
	}
	if frappe.db.has_column("CRM Call Log", "telephony_medium"):
		doc_dict["telephony_medium"] = "Manual"
	if parsed_data.get("note") and frappe.db.has_column("CRM Call Log", "note"):
		# Upstream `CRM Call Log.note` is `Link -> FCRM Note`; only link when it references a valid note of this Lead.
		raw_cl_note = sanitize_note_text(str(parsed_data.get("note")), max_length=140)
		if raw_cl_note and frappe.db.exists(
			"FCRM Note",
			{"name": raw_cl_note, "reference_doctype": "CRM Lead", "reference_docname": lead},
		):
			doc_dict["note"] = raw_cl_note

	call_doc = frappe.get_doc(doc_dict)
	call_doc.insert(ignore_permissions=True)
	return str(call_doc.name), "self_reported"


def list_unlinked_call_logs(work_item: str, limit: int = 20, actor: str | None = None) -> dict:
	"""List `CRM Call Log`s on `work_item.lead` not yet linked to any `CSKH Contact Attempt`."""
	from crm_cskh.api._params import parse_int

	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not wi_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	limit_i = parse_int(limit, minimum=1, maximum=100, field="limit")
	wi_doc = assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)
	lead = wi_doc["lead"]

	rows = frappe.db.sql(
		"""
		select cl.name, cl.id, cl.`type`, cl.status, cl.`from` as caller_number,
		       cl.`to` as receiver_number, cl.duration, cl.start_time, cl.end_time, cl.creation
		from `tabCRM Call Log` cl
		left join `tabCSKH Contact Attempt` att on att.call_log = cl.name
		where cl.reference_doctype = 'CRM Lead'
		  and cl.reference_docname = %s
		  and att.name is null
		order by coalesce(cl.start_time, cl.creation) desc, cl.name desc
		limit %s
		""",
		(lead, limit_i),
		as_dict=True,
	)
	return {
		"work_item": wi_val,
		"lead": lead,
		"items": rows,
		"total": len(rows),
		"as_of": now_datetime(),
	}
