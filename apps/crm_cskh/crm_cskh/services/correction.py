"""Audited correction / void / restore overlay service (`P2.md` §6.4, `ADR-P2-03`).

Preserves 100% immutability of `CSKH Contact Attempt` rows:
  * Never `UPDATE`s or `DELETE`s business fields on `tabCSKH Contact Attempt`.
  * Validates corrected fields against an explicit allowlist (`outcome_code`, `channel`,
    `occurred_at`, `note`, `call_log`) and captures the outcome snapshot at correction time.
  * `Staff` requests always enter `status = "pending"` (`auto_approve=True` -> `403 FORBIDDEN`;
    `action == "restore"` -> `403 FORBIDDEN`; cannot request correction on another user's attempt).
  * `Sales Manager` (within team when `allow_leader_review_correction == 1`) and `CSKH CRM Admin`
    may approve/reject pending requests or submit `auto_approve=True`; only `CSKH CRM Admin`
    may execute `restore`.
  * Approved corrections recompute `CSKH Work Item` effective read-model atomically in the same
    DB transaction and bump `Work Item.revision`.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from crm_cskh.api._params import parse_bool, parse_int, parse_optional_str, parse_pagination
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p2_models import CORRECTION_ACTIONS, CORRECTION_STATUSES, VALID_CHANNELS
from crm_cskh.p2_permissions import (
	ROLE_ADMIN,
	_in_team,
	_is_leader,
	_is_staff,
	_is_system,
	_roles,
	assert_work_item_workflow_access,
	assert_workflow_actor,
	leader_authorized_work_item_sql,
)
from crm_cskh.services import reporting
from crm_cskh.services.errors import (
	AlreadyLinked,
	Conflict,
	Forbidden,
	IdempotencyConflict,
	InvalidChannel,
	InvalidFollowupTime,
	InvalidOutcome,
	InvalidRequest,
	NoteRequired,
	NotFound,
	OutcomeNotCompatible,
	OwnershipChanged,
	StaleWorkItem,
	rollback_savepoint_safely,
)
from crm_cskh.services.policies import resolve_effective_policy, serialize_outcome
from crm_cskh.services.read_model import compute_effective_attempts, recompute_work_item_read_model
from crm_cskh.services.sanitizer import sanitize_note_text

ALLOWED_CORRECTION_KEYS = frozenset(("outcome_code", "channel", "occurred_at", "note", "call_log"))


def _payload_hash(payload: dict) -> str:
	canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_corrected_fields(raw) -> dict:
	if raw is None or raw == "":
		return {}
	if isinstance(raw, str):
		try:
			parsed = json.loads(raw)
		except (TypeError, ValueError) as exc:
			raise InvalidRequest("Tham số corrected_fields phải là JSON object hợp lệ.") from exc
	elif isinstance(raw, dict):
		parsed = dict(raw)
	else:
		raise InvalidRequest("Tham số corrected_fields phải là JSON object.")
	if not isinstance(parsed, dict):
		raise InvalidRequest("Tham số corrected_fields phải là JSON object.")
	unknown = set(parsed.keys()) - ALLOWED_CORRECTION_KEYS
	if unknown:
		raise InvalidRequest(f"Trường không được phép hiệu chỉnh: {', '.join(sorted(unknown))}.")
	return parsed


def _validate_and_enrich_patch(
	*,
	patch: dict,
	current_effective: dict,
	attempt_row: dict,
	policy: dict,
	now_dt,
) -> dict:
	if not patch:
		raise InvalidRequest(
			"Hành động hiệu chỉnh (correct) yêu cầu ít nhất một trường trong corrected_fields."
		)

	enriched: dict = {}
	eff_channel = current_effective["channel"]
	if "channel" in patch and patch["channel"] is not None:
		ch = parse_optional_str(patch["channel"], "Kênh hiệu chỉnh không hợp lệ.") or ""
		ch = ch.lower()
		if ch not in VALID_CHANNELS:
			raise InvalidChannel("Kênh liên hệ hiệu chỉnh không hợp lệ.")
		if ch not in policy["enabled_channels"]:
			raise InvalidChannel("Kênh liên hệ hiệu chỉnh không được bật trong chính sách.")
		eff_channel = ch
		enriched["channel"] = ch

	eff_outcome_code = current_effective["outcome_code"]
	outcome_obj = None
	if "outcome_code" in patch and patch["outcome_code"] is not None:
		code = (
			parse_optional_str(patch["outcome_code"], "Mã kết quả hiệu chỉnh không hợp lệ.") or ""
		).lower()
		out_fields = [
			"name",
			"code",
			"label_vi",
			"result_group",
			"is_contact_attempt",
			"is_connected",
			"is_interested",
			"is_terminal",
			"is_do_not_contact",
			"requires_note",
			"requires_followup",
			"allows_followup",
			"requires_completion_reason",
			"allowed_channels",
			"suggested_next_status",
			"version",
			"enabled",
		]
		for opt_col in ("is_conversion", "is_invalid_phone"):
			if frappe.db.has_column("CSKH Outcome Code", opt_col):
				out_fields.append(opt_col)
		out_row = frappe.db.get_value(
			"CSKH Outcome Code",
			code,
			out_fields,
			as_dict=True,
		)
		if not out_row or not cint(out_row.enabled):
			raise InvalidOutcome("Mã kết quả hiệu chỉnh không tồn tại hoặc đã ngừng sử dụng.")
		outcome_obj = serialize_outcome(out_row)
		eff_outcome_code = outcome_obj["code"]
		is_conv_patch = bool(outcome_obj.get("is_conversion"))
		camp_id = (
			attempt_row.get("campaign")
			if isinstance(attempt_row, dict)
			else getattr(attempt_row, "campaign", None)
		)
		if (
			not is_conv_patch
			and camp_id
			and frappe.db.has_column("CSKH Campaign", "conversion_outcome_codes")
		):
			raw_cc = frappe.db.get_value("CSKH Campaign", camp_id, "conversion_outcome_codes") or ""
			if outcome_obj["code"].lower() in {
				c.strip().lower() for c in str(raw_cc).split(",") if c.strip()
			}:
				is_conv_patch = True
		enriched.update(
			{
				"outcome_code": outcome_obj["code"],
				"outcome_label": outcome_obj["label_vi"],
				"outcome_group": outcome_obj["result_group"],
				"outcome_version": outcome_obj["version"],
				"is_contact_attempt": 1 if outcome_obj["is_contact_attempt"] else 0,
				"is_connected": 1 if outcome_obj["is_connected"] else 0,
				"is_interested": 1 if outcome_obj["is_interested"] else 0,
				"is_terminal": 1 if outcome_obj["is_terminal"] else 0,
				"is_do_not_contact": 1 if outcome_obj["is_do_not_contact"] else 0,
				"is_conversion": 1 if is_conv_patch else 0,
				"is_invalid_phone": 1 if outcome_obj.get("is_invalid_phone") else 0,
			}
		)
	else:
		out_row = frappe.db.get_value(
			"CSKH Outcome Code",
			eff_outcome_code,
			[
				"name",
				"code",
				"allowed_channels",
				"requires_note",
				"enabled",
				"label_vi",
				"result_group",
				"version",
				"is_contact_attempt",
				"is_connected",
				"is_interested",
				"is_terminal",
				"is_do_not_contact",
				"requires_followup",
				"allows_followup",
				"requires_completion_reason",
				"suggested_next_status",
			],
			as_dict=True,
		)
		if out_row:
			outcome_obj = serialize_outcome(out_row)

	if outcome_obj and eff_channel not in outcome_obj["allowed_channels"]:
		raise OutcomeNotCompatible(
			f"Mã kết quả '{eff_outcome_code}' không tương thích với kênh '{eff_channel}'."
		)

	eff_note = current_effective.get("note") or ""
	if "note" in patch:
		raw_note = parse_optional_str(patch["note"], "Ghi chú hiệu chỉnh không hợp lệ.")
		eff_note = sanitize_note_text(raw_note, max_length=4000)
		enriched["note"] = eff_note
	if outcome_obj and outcome_obj["requires_note"] and not eff_note:
		raise NoteRequired("Mã kết quả hiệu chỉnh bắt buộc có ghi chú chi tiết.")

	if "occurred_at" in patch and patch["occurred_at"] is not None:
		occ_str = parse_optional_str(patch["occurred_at"], "Thời điểm liên hệ hiệu chỉnh không hợp lệ.")
		try:
			occ_dt = get_datetime(occ_str)
		except Exception as exc:
			raise InvalidFollowupTime("Định dạng thời điểm liên hệ hiệu chỉnh không hợp lệ.") from exc
		if occ_dt is None or occ_dt > add_to_date(now_dt, seconds=5):
			raise InvalidFollowupTime("Thời điểm liên hệ hiệu chỉnh không hợp lệ hoặc nằm ở tương lai.")
		# Must lie inside the ownership interval of the original attempt (`ownership_event`)
		hist = frappe.db.get_value(
			"CSKH Assignment History",
			attempt_row.ownership_event,
			["ownership_start", "ownership_end"],
			as_dict=True,
		)
		if hist:
			if hist.ownership_start and occ_dt < get_datetime(hist.ownership_start):
				raise InvalidFollowupTime(
					"Thời điểm hiệu chỉnh không được sớm hơn khoảng sở hữu của lần liên hệ gốc."
				)
			if hist.ownership_end and occ_dt > get_datetime(hist.ownership_end):
				raise InvalidFollowupTime(
					"Thời điểm hiệu chỉnh không được vượt quá thời điểm kết thúc khoảng sở hữu gốc."
				)
		enriched["occurred_at"] = str(occ_dt)

	if "call_log" in patch:
		cl_id = parse_optional_str(patch["call_log"], "Mã CRM Call Log không hợp lệ.")
		if cl_id:
			if eff_channel != "phone_call":
				raise OutcomeNotCompatible("Chỉ kênh gọi thoại (phone_call) mới được gắn CRM Call Log.")
			cl_row = frappe.db.get_value(
				"CRM Call Log", cl_id, ["name", "reference_doctype", "reference_docname"], as_dict=True
			)
			if not cl_row:
				raise NotFound("Không tìm thấy bản ghi CRM Call Log.")
			if cl_row.reference_doctype != "CRM Lead" or cl_row.reference_docname != attempt_row.lead:
				raise OutcomeNotCompatible("CRM Call Log không thuộc khách hàng của Work Item này.")
			other_att = frappe.db.get_value(
				"CSKH Contact Attempt",
				{"call_log": cl_id, "name": ["!=", attempt_row.name]},
				"name",
			)
			if other_att:
				raise AlreadyLinked("CRM Call Log này đã được gắn với một nhật ký liên hệ khác.")
			enriched["call_log"] = cl_id
		else:
			enriched["call_log"] = None
	elif eff_channel != "phone_call" and current_effective.get("call_log"):
		raise OutcomeNotCompatible(
			"Không thể đổi sang kênh ngoài gọi thoại khi bản ghi vẫn đang gắn CRM Call Log."
		)

	return enriched


def request_correction(
	attempt: str,
	action: str,
	corrected_fields: dict | str | None = None,
	reason: str | None = None,
	auto_approve=False,
	expected_revision=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Create (and optionally approve for Leader/Admin) an audited `CSKH Attempt Correction`."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	roles = _roles(actor)
	is_admin = _is_system(actor) or ROLE_ADMIN in roles
	is_leader = _is_leader(actor)
	is_staff = _is_staff(actor)

	att_val = parse_optional_str(attempt, "Mã nhật ký liên hệ (attempt) không hợp lệ.")
	if not att_val:
		raise InvalidRequest("Mã nhật ký liên hệ (attempt) là bắt buộc.")
	action_val = (parse_optional_str(action, "Hành động hiệu chỉnh không hợp lệ.") or "").lower()
	if action_val not in CORRECTION_ACTIONS:
		raise InvalidRequest("Hành động hiệu chỉnh chỉ chấp nhận correct | void | restore.")
	if action_val == "restore" and not is_admin:
		raise Forbidden("Chỉ CSKH CRM Admin được phép khôi phục (restore) nhật ký đã hủy hiệu lực.")

	auto_approve_b = parse_bool(auto_approve, default=False, field="auto_approve")
	if auto_approve_b and not (is_admin or is_leader):
		raise Forbidden("Nhân viên không được tự phê duyệt hiệu chỉnh (auto_approve).")

	clean_reason = sanitize_note_text(
		parse_optional_str(reason, "Lý do hiệu chỉnh không hợp lệ."), max_length=1000
	)
	if not clean_reason:
		raise NoteRequired("Yêu cầu hiệu chỉnh bắt buộc ghi rõ lý do kiểm toán.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)

	raw_patch = _parse_corrected_fields(corrected_fields) if action_val == "correct" else {}
	if action_val != "correct" and corrected_fields not in (None, "", {}):
		parsed_non_corr = _parse_corrected_fields(corrected_fields)
		if parsed_non_corr:
			raise InvalidRequest("Tham số corrected_fields chỉ dùng cho action = 'correct'.")

	att_row = frappe.db.get_value(
		"CSKH Contact Attempt",
		att_val,
		[
			"name",
			"work_item",
			"lead",
			"campaign",
			"ownership_event",
			"performed_by",
			"owner_at_attempt",
			"occurred_at",
			"channel",
			"outcome_code",
		],
		as_dict=True,
	)
	if not att_row:
		if is_admin:
			raise NotFound("Không tìm thấy bản ghi nhật ký liên hệ.")
		raise Forbidden("Bạn không có quyền truy cập nhật ký liên hệ này.")

	wi_val = att_row.work_item
	assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	req_hash = _payload_hash(
		{
			"op": "request_correction",
			"attempt": att_val,
			"action": action_val,
			"corrected_fields": raw_patch,
			"reason": clean_reason,
			"auto_approve": auto_approve_b,
		}
	)

	savepoint = f"cskh_req_corr_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked_rows = frappe.db.sql(
			"select name, campaign, lead, status, revision, assigned_to "
			"from `tabCSKH Work Item` where name = %s for update",
			(wi_val,),
			as_dict=True,
		)
		if not locked_rows:
			raise NotFound("Không tìm thấy Work Item.")
		locked = locked_rows[0]

		existing_corr = frappe.db.get_value(
			"CSKH Attempt Correction",
			{"requested_by": actor, "idempotency_key": idem_val},
			["name", "status", "payload_hash", "work_item"],
			as_dict=True,
		)
		if existing_corr:
			if existing_corr.payload_hash != req_hash:
				raise IdempotencyConflict("Khóa idempotency_key đã được dùng với yêu cầu hiệu chỉnh khác.")
			return {
				"status": existing_corr.status,
				"idempotent_replay": True,
				"correction_id": existing_corr.name,
				"attempt": att_val,
				"work_item": wi_val,
				"work_item_status": locked.status,
				"revision": cint(locked.revision or 1),
			}

		if not is_admin:
			if is_leader:
				pass  # Already verified via `assert_work_item_workflow_access`
			elif is_staff:
				if locked.status not in OWNED_ACTIVE_WORK_ITEM_STATES or locked.assigned_to != actor:
					raise OwnershipChanged("Bạn không còn phụ trách Work Item này.")
				if att_row.performed_by != actor:
					raise Forbidden(
						"Nhân viên chỉ được gửi yêu cầu hiệu chỉnh cho lần liên hệ do chính mình thực hiện."
					)

		current_rev = cint(locked.revision or 1)
		if exp_rev is not None and current_rev != exp_rev:
			raise StaleWorkItem(
				"Work Item đã thay đổi (lệch revision); vui lòng tải lại trước khi gửi hiệu chỉnh.",
				details={"expected_revision": exp_rev, "current_revision": current_rev},
			)

		policy = resolve_effective_policy(campaign=locked.campaign, owner_user=locked.assigned_to)
		if auto_approve_b and is_leader and not is_admin and not policy["allow_leader_review_correction"]:
			raise Forbidden("Chính sách hiện tại không cho phép Team Leader tự duyệt hiệu chỉnh.")

		projected_list = compute_effective_attempts(wi_val)
		proj_att = next((a for a in projected_list if a["attempt_id"] == att_val), None)
		if not proj_att:
			raise NotFound("Không tìm thấy bản ghi nhật ký liên hệ trên Work Item.")

		if action_val == "void" and proj_att["is_voided"]:
			raise Conflict("Nhật ký liên hệ này đã bị hủy hiệu lực (void).", code="CONFLICT")
		if action_val == "restore" and not proj_att["is_voided"]:
			raise Conflict("Nhật ký liên hệ này đang có hiệu lực, không cần restore.", code="CONFLICT")
		if action_val == "correct" and proj_att["is_voided"]:
			raise Conflict("Không thể sửa trường của nhật ký đã bị void; cần restore trước.", code="CONFLICT")

		now_dt = now_datetime()
		enriched_patch = (
			_validate_and_enrich_patch(
				patch=raw_patch,
				current_effective=proj_att["effective"],
				attempt_row=att_row,
				policy=policy,
				now_dt=now_dt,
			)
			if action_val == "correct"
			else {}
		)

		snapshot_before = {
			"is_voided": proj_att["is_voided"],
			"effective": proj_att["effective"],
		}
		corr_status = "approved" if auto_approve_b else "pending"

		prev_flag = getattr(frappe.flags, "cskh_in_correction_service", False)
		frappe.flags.cskh_in_correction_service = True
		try:
			corr_doc = frappe.get_doc(
				{
					"doctype": "CSKH Attempt Correction",
					"attempt": att_val,
					"work_item": wi_val,
					"lead": locked.lead,
					"campaign": locked.campaign,
					"action": action_val,
					"status": corr_status,
					"requested_by": actor,
					"requested_at": now_dt,
					"reason": clean_reason,
					"original_snapshot_json": json.dumps(snapshot_before, ensure_ascii=False, default=str),
					"corrected_fields_json": (
						json.dumps(enriched_patch, ensure_ascii=False, default=str)
						if enriched_patch
						else None
					),
					"reviewed_by": actor if auto_approve_b else None,
					"reviewed_at": now_dt if auto_approve_b else None,
					"review_note": clean_reason if auto_approve_b else None,
					"idempotency_key": idem_val,
					"payload_hash": req_hash,
				}
			)
			corr_doc.insert(ignore_permissions=True)
		finally:
			frappe.flags.cskh_in_correction_service = prev_flag

		if corr_status == "approved":
			recomputed = recompute_work_item_read_model(
				wi_val,
				bump_revision=True,
				update_status_when_active=True,
				extra_updates={"last_activity_by": actor},
			)
			from crm_cskh.services import sla_engine

			sla_engine.on_correction_reviewed_in_tx(
				work_item=wi_val,
				attempt_id=att_val,
				action=action_val,
				reviewed_at=now_dt,
				actor=actor,
			)
			final_status = recomputed["status"]
			final_rev = recomputed["revision"]
		else:
			final_status = locked.status
			final_rev = current_rev

		from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation

		dirty_reason = "prior_period_correction" if corr_status == "approved" else "correction_requested"
		schedule_post_commit_dirty_invalidation(
			work_item=wi_val,
			campaign=locked.campaign,
			owner_user=att_row.performed_by or att_row.owner_at_attempt,
			event_dt=att_row.occurred_at,
			reason=dirty_reason,
			trigger_reference=corr_doc.name,
		)
		if enriched_patch.get("occurred_at"):
			schedule_post_commit_dirty_invalidation(
				work_item=wi_val,
				campaign=locked.campaign,
				owner_user=att_row.performed_by or att_row.owner_at_attempt,
				event_dt=enriched_patch["occurred_at"],
				reason=dirty_reason,
				trigger_reference=corr_doc.name,
			)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()
	return {
		"status": corr_status,
		"idempotent_replay": False,
		"correction_id": corr_doc.name,
		"attempt": att_val,
		"work_item": wi_val,
		"work_item_status": final_status,
		"revision": final_rev,
	}


def review_correction(
	correction_id: str,
	decision: str,
	review_note: str | None = None,
	expected_revision=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Approve or reject a `pending` `CSKH Attempt Correction` (`docs/api-contract-p2.md` §4.12)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	roles = _roles(actor)
	is_admin = _is_system(actor) or ROLE_ADMIN in roles
	is_leader = _is_leader(actor)
	if not (is_admin or is_leader):
		raise Forbidden("Nhân viên không có quyền phê duyệt hoặc từ chối yêu cầu hiệu chỉnh.")

	corr_val = parse_optional_str(correction_id, "Mã yêu cầu hiệu chỉnh không hợp lệ.")
	if not corr_val:
		raise InvalidRequest("Mã yêu cầu hiệu chỉnh (correction_id) là bắt buộc.")
	dec_val = (parse_optional_str(decision, "Quyết định duyệt không hợp lệ.") or "").lower()
	if dec_val not in ("approve", "reject"):
		raise InvalidRequest("Tham số decision chỉ chấp nhận approve | reject.")
	clean_note = sanitize_note_text(
		parse_optional_str(review_note, "Ghi chú duyệt không hợp lệ."), max_length=1000
	)
	if dec_val == "reject" and not clean_note:
		raise NoteRequired("Từ chối yêu cầu hiệu chỉnh bắt buộc ghi rõ lý do (review_note).")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)

	corr_meta = frappe.db.get_value(
		"CSKH Attempt Correction",
		corr_val,
		["name", "work_item", "attempt", "action", "status"],
		as_dict=True,
	)
	if not corr_meta:
		if is_admin:
			raise NotFound("Không tìm thấy yêu cầu hiệu chỉnh.")
		raise Forbidden("Bạn không có quyền truy cập yêu cầu hiệu chỉnh này.")

	wi_val = corr_meta.work_item
	assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	if corr_meta.action == "restore" and not is_admin:
		raise Forbidden("Chỉ CSKH CRM Admin được phê duyệt thao tác restore.")

	req_hash = _payload_hash(
		{
			"op": "review_correction",
			"correction_id": corr_val,
			"decision": dec_val,
			"review_note": clean_note,
		}
	)
	review_marker = f"[REV:{idem_val}:{req_hash[:16]}]"

	savepoint = f"cskh_rev_corr_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked_rows = frappe.db.sql(
			"select name, campaign, lead, status, revision, assigned_to "
			"from `tabCSKH Work Item` where name = %s for update",
			(wi_val,),
			as_dict=True,
		)
		if not locked_rows:
			raise NotFound("Không tìm thấy Work Item.")
		locked = locked_rows[0]

		corr_doc = frappe.get_doc("CSKH Attempt Correction", corr_val)
		if corr_doc.status in ("approved", "rejected"):
			stored_note = corr_doc.review_note or ""
			if f"[REV:{idem_val}:" in stored_note:
				if review_marker not in stored_note:
					raise IdempotencyConflict("Khóa idempotency_key đã được dùng với quyết định duyệt khác.")
				return {
					"status": corr_doc.status,
					"idempotent_replay": True,
					"correction_id": corr_val,
					"attempt": corr_doc.attempt,
					"work_item": wi_val,
					"work_item_status": locked.status,
					"revision": cint(locked.revision or 1),
				}
			raise Conflict("Yêu cầu hiệu chỉnh này đã được xử lý trước đó.", code="CONFLICT")

		policy = resolve_effective_policy(campaign=locked.campaign, owner_user=locked.assigned_to)
		if not is_admin and not policy["allow_leader_review_correction"]:
			raise Forbidden("Chính sách hiện tại không cho phép Team Leader duyệt hiệu chỉnh.")

		current_rev = cint(locked.revision or 1)
		if exp_rev is not None and current_rev != exp_rev:
			raise StaleWorkItem(
				"Work Item đã thay đổi (lệch revision); vui lòng tải lại trước khi duyệt hiệu chỉnh.",
				details={"expected_revision": exp_rev, "current_revision": current_rev},
			)

		now_dt = now_datetime()
		if dec_val == "approve":
			projected_list = compute_effective_attempts(wi_val)
			proj_att = next((a for a in projected_list if a["attempt_id"] == corr_doc.attempt), None)
			if not proj_att:
				raise NotFound("Không tìm thấy nhật ký liên hệ gốc.")
			if corr_doc.action == "void" and proj_att["is_voided"]:
				raise Conflict("Nhật ký liên hệ đã bị void trước khi duyệt yêu cầu này.", code="CONFLICT")
			if corr_doc.action == "restore" and not proj_att["is_voided"]:
				raise Conflict("Nhật ký liên hệ đang có hiệu lực, không thể restore.", code="CONFLICT")
			if corr_doc.action == "correct":
				if proj_att["is_voided"]:
					raise Conflict("Không thể áp dụng sửa trường lên nhật ký đã bị void.", code="CONFLICT")
				att_row = frappe.db.get_value(
					"CSKH Contact Attempt",
					corr_doc.attempt,
					["name", "work_item", "lead", "campaign", "ownership_event"],
					as_dict=True,
				)
				patch = json.loads(corr_doc.corrected_fields_json or "{}")
				# Re-validate call_log uniqueness and channel/outcome rules at approval time!
				re_enriched = _validate_and_enrich_patch(
					patch={k: patch[k] for k in patch if k in ALLOWED_CORRECTION_KEYS},
					current_effective=proj_att["effective"],
					attempt_row=att_row,
					policy=policy,
					now_dt=now_dt,
				)
				corr_doc.corrected_fields_json = json.dumps(re_enriched, ensure_ascii=False, default=str)

		target_status = "approved" if dec_val == "approve" else "rejected"
		prev_flag = getattr(frappe.flags, "cskh_in_correction_service", False)
		frappe.flags.cskh_in_correction_service = True
		try:
			corr_doc.status = target_status
			corr_doc.reviewed_by = actor
			corr_doc.reviewed_at = now_dt
			corr_doc.review_note = f"{review_marker} {clean_note}".strip()[:1000]
			corr_doc.save(ignore_permissions=True)
		finally:
			frappe.flags.cskh_in_correction_service = prev_flag

		if target_status == "approved":
			recomputed = recompute_work_item_read_model(
				wi_val,
				bump_revision=True,
				update_status_when_active=True,
				extra_updates={"last_activity_by": actor},
			)
			from crm_cskh.services import sla_engine

			sla_engine.on_correction_reviewed_in_tx(
				work_item=wi_val,
				attempt_id=corr_doc.attempt,
				action=corr_doc.action,
				reviewed_at=now_dt,
				actor=actor,
			)
			final_status = recomputed["status"]
			final_rev = recomputed["revision"]
		else:
			final_status = locked.status
			final_rev = current_rev

		from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation

		orig_att_meta = frappe.db.get_value(
			"CSKH Contact Attempt",
			corr_doc.attempt,
			["occurred_at", "performed_by", "owner_at_attempt"],
			as_dict=True,
		)
		dirty_reason = "prior_period_correction" if target_status == "approved" else "correction_rejected"
		if orig_att_meta and orig_att_meta.occurred_at:
			schedule_post_commit_dirty_invalidation(
				work_item=wi_val,
				campaign=locked.campaign,
				owner_user=orig_att_meta.performed_by or orig_att_meta.owner_at_attempt,
				event_dt=orig_att_meta.occurred_at,
				reason=dirty_reason,
				trigger_reference=corr_doc.name,
			)
		if corr_doc.corrected_fields_json:
			try:
				patched_occ = json.loads(corr_doc.corrected_fields_json).get("occurred_at")
				if patched_occ:
					schedule_post_commit_dirty_invalidation(
						work_item=wi_val,
						campaign=locked.campaign,
						owner_user=(orig_att_meta.performed_by if orig_att_meta else None),
						event_dt=patched_occ,
						reason=dirty_reason,
						trigger_reference=corr_doc.name,
					)
			except Exception:
				pass
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()
	return {
		"status": target_status,
		"idempotent_replay": False,
		"correction_id": corr_val,
		"attempt": corr_doc.attempt,
		"work_item": wi_val,
		"work_item_status": final_status,
		"revision": final_rev,
	}


def list_corrections(
	page=1,
	page_size=20,
	work_item: str | None = None,
	status: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Scoped list of `CSKH Attempt Correction` requests (`docs/api-contract-p2.md` §3)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	page_num, limit, offset = parse_pagination(page, page_size)
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	status_val = parse_optional_str(status, "Tham số status không hợp lệ.")
	if status_val:
		status_val = status_val.lower()
		if status_val not in ("all", *CORRECTION_STATUSES):
			raise InvalidRequest("Tham số status chỉ chấp nhận pending | approved | rejected | all.")

	if wi_val:
		assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	scope = reporting._scope(actor)
	scope_label = "all" if scope["scope"] in ("system", "all") else scope["scope"]
	clauses = ["1=1"]
	params: list = []

	if scope["scope"] in ("system", "all"):
		pass
	elif scope["scope"] == "team":
		clauses.append(leader_authorized_work_item_sql(actor, "wi"))
	else:
		clauses.append("wi.assigned_to = %s")
		params.append(actor)
		clauses.append("wi.status in ('assigned', 'in_progress', 'on_hold')")
		clauses.append("corr.requested_by = %s")
		params.append(actor)

	if wi_val:
		clauses.append("corr.work_item = %s")
		params.append(wi_val)
	if status_val and status_val != "all":
		clauses.append("corr.status = %s")
		params.append(status_val)

	from_where = (
		"from `tabCSKH Attempt Correction` corr "
		"inner join `tabCSKH Work Item` wi on wi.name = corr.work_item "
		"where " + " and ".join(clauses)
	)
	total = int(frappe.db.sql(f"select count(*) {from_where}", tuple(params))[0][0] or 0)
	if total == 0:
		return {
			"scope": scope_label,
			"items": [],
			"total": 0,
			"page": page_num,
			"page_size": limit,
			"as_of": now_datetime(),
		}

	rows = frappe.db.sql(
		f"""
		select corr.name as correction_id, corr.attempt, corr.work_item, corr.lead, corr.campaign,
		       corr.action, corr.status, corr.requested_by, corr.requested_at, corr.reason,
		       corr.original_snapshot_json, corr.corrected_fields_json,
		       corr.reviewed_by, corr.reviewed_at, corr.review_note,
		       wi.revision as work_item_revision
		{from_where}
		order by case when corr.status = 'pending' then 0 else 1 end asc,
		         corr.creation desc, corr.name desc
		limit %s offset %s
		""",
		(*tuple(params), limit, offset),
		as_dict=True,
	)

	items = []
	for r in rows:
		clean_review = r.review_note or ""
		if clean_review.startswith("[REV:"):
			_, _, remainder = clean_review.partition("]")
			clean_review = remainder.strip()
		items.append(
			{
				"correction_id": r.correction_id,
				"attempt": r.attempt,
				"work_item": r.work_item,
				"lead": r.lead,
				"campaign": r.campaign,
				"action": r.action,
				"status": r.status,
				"requested_by": r.requested_by,
				"requested_at": r.requested_at,
				"reason": r.reason,
				"original_snapshot": json.loads(r.original_snapshot_json or "{}"),
				"corrected_fields": json.loads(r.corrected_fields_json or "{}"),
				"reviewed_by": r.reviewed_by,
				"reviewed_at": r.reviewed_at,
				"review_note": clean_review or None,
				"work_item_revision": cint(r.work_item_revision or 1),
			}
		)

	return {
		"scope": scope_label,
		"items": items,
		"total": total,
		"page": page_num,
		"page_size": limit,
		"as_of": now_datetime(),
	}
