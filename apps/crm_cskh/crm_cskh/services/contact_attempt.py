"""Transactional orchestration for `record_attempt`, `close_work_item` & `reopen_work_item`.

Implements `P2.md` (§5-§9, §15.3), `docs/state-machine.md`, `docs/api-contract-p2.md`
(§4.5, §4.10, §4.11), and `docs/race-retry-reconciliation.md`.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from crm_cskh.api._params import parse_bool, parse_int, parse_optional_str
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p2_models import VALID_CHANNELS, set_lead_dnc_fields
from crm_cskh.p2_permissions import (
	ROLE_ADMIN,
	_in_team,
	_is_leader,
	_is_staff,
	_is_system,
	_roles,
	assert_work_item_workflow_access,
	assert_workflow_actor,
	is_work_item_readable_for_workflow,
)
from crm_cskh.services import notifications, ownership
from crm_cskh.services.call_log_adapter import parse_call_log_data, resolve_or_create_call_log_in_tx
from crm_cskh.services.errors import (
	Conflict,
	DoNotContact,
	FollowupRequired,
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
	PolicyChanged,
	StaleWorkItem,
	WorkItemNotActive,
	rollback_savepoint_safely,
)
from crm_cskh.services.followup import (
	_parse_due_datetime,
	cancel_open_followup_in_tx,
	create_followup_in_tx,
	get_open_followup_task,
	resolve_open_followup_in_tx,
)
from crm_cskh.services.policies import resolve_effective_policy, serialize_outcome
from crm_cskh.services.read_model import (
	compute_effective_attempts,
	recompute_work_item_read_model,
	summarize_effective_attempts,
)
from crm_cskh.services.sanitizer import sanitize_note_text


def _payload_hash(payload: dict) -> str:
	canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _wi_preview_fields() -> list[str]:
	fields = ["name", "lead", "campaign", "status", "assigned_to", "closed_by", "last_activity_by"]
	if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
		fields.append("owner_at_close")
	return fields


def _lock_work_item(work_item: str) -> dict:
	owner_close_col = (
		"owner_at_close"
		if frappe.db.has_column("CSKH Work Item", "owner_at_close")
		else "null as owner_at_close"
	)
	rows = frappe.db.sql(
		f"""
		select name, campaign, lead, status, revision, assigned_to, assigned_at,
		       attempt_count, connected_attempt_count, open_callback_task, next_action_due,
		       closed_by, {owner_close_col}, last_activity_by
		from `tabCSKH Work Item`
		where name = %s
		for update
		""",
		(work_item,),
		as_dict=True,
	)
	if not rows:
		raise NotFound("Không tìm thấy Work Item.")
	return rows[0]


def _parse_followup_spec(
	followup=None,
	followup_due_at=None,
	followup_title: str | None = None,
	followup_note: str | None = None,
) -> dict | None:
	parsed: dict = {}
	if followup is not None and followup != "":
		if isinstance(followup, str):
			try:
				loaded = json.loads(followup)
			except (TypeError, ValueError) as exc:
				raise InvalidRequest("Tham số followup phải là JSON object hợp lệ.") from exc
			if not isinstance(loaded, dict):
				raise InvalidRequest("Tham số followup phải là JSON object.")
			parsed = dict(loaded)
		elif isinstance(followup, dict):
			parsed = dict(followup)
		else:
			raise InvalidRequest("Tham số followup phải là JSON object.")

	due_raw = parsed.get("due_at") if parsed.get("due_at") is not None else followup_due_at
	if due_raw is None or (isinstance(due_raw, str) and not due_raw.strip()):
		return None

	title_raw = parsed.get("title") if parsed.get("title") is not None else followup_title
	desc_raw = (
		parsed.get("description")
		if parsed.get("description") is not None
		else (parsed.get("note") if parsed.get("note") is not None else followup_note)
	)
	return {
		"due_at": str(due_raw).strip(),
		"title": parse_optional_str(title_raw, "Tiêu đề lịch hẹn không hợp lệ.") or "",
		"description": parse_optional_str(desc_raw, "Ghi chú lịch hẹn không hợp lệ.") or "",
	}


def _history_idempotency_fields() -> list[str]:
	fields = ["name", "reason", "from_user", "to_user", "event_at"]
	for col in ("private_reason", "payload_hash"):
		if frappe.db.has_column("CSKH Assignment History", col):
			fields.append(col)
	return fields


def _close_work_item_in_tx(
	*,
	locked: dict,
	actor: str,
	now_dt,
	clean_reason: str,
	event_id: str,
	next_revision: int,
	policy_version: int,
	public_reason: str | None = None,
	payload_hash: str | None = None,
	extra_wi_fields: dict | None = None,
	close_category: str = "normal",
	is_dnc: bool = False,
) -> str | None:
	"""Close an active `CSKH Work Item` atomically inside an existing transaction/savepoint."""
	work_item_val = locked["name"]
	old_owner = locked["assigned_to"]
	priv_reason = (clean_reason or "").strip() or "Hoàn tất mục tiêu chăm sóc"
	pub_reason = (public_reason or "").strip() or ownership.public_history_reason("close")

	prev_sys_cancel = getattr(frappe.flags, "cskh_system_cancel_followup", False)
	frappe.flags.cskh_system_cancel_followup = True
	try:
		canceled_task = cancel_open_followup_in_tx(
			work_item=work_item_val,
			actor=actor,
			reason=f"Đóng Work Item: {priv_reason}",
			now=now_dt,
		)
	finally:
		frappe.flags.cskh_system_cancel_followup = prev_sys_cancel

	from crm_cskh.services import sla_engine

	sla_engine.on_work_item_closed_in_tx(
		work_item=work_item_val,
		lead=locked["lead"],
		campaign=locked.get("campaign"),
		owner_user=old_owner,
		closed_at=now_dt,
		actor=actor,
		close_category=close_category,
		is_dnc=is_dnc,
	)

	ownership._close_open_interval(work_item_val, now_dt)
	ownership._insert_history(
		{
			"doctype": "CSKH Assignment History",
			"work_item": work_item_val,
			"action": "close",
			"from_user": old_owner,
			"to_user": None,
			"actor": actor,
			"event_at": now_dt,
			"reason": pub_reason,
			"private_reason": priv_reason,
			"payload_hash": payload_hash,
			"ownership_start": None,
			"ownership_end": now_dt,
			"event_id": event_id,
			"distribution_run": ownership._original_distribution_run(work_item_val),
		}
	)

	ownership._sync_lead_owner(locked["lead"], None)
	old_todo = ownership._todo_for(locked["lead"], old_owner)
	ownership._cancel_todo(old_todo)
	ownership._sync_docshares(locked["lead"], [])
	ownership._sync_lead_owner(locked["lead"], None)
	frappe.db.delete("CSKH Active Claim", {"lead": locked["lead"]})

	wi_updates = {
		"status": "closed",
		"assigned_to": None,
		"assigned_at": None,
		"open_callback_task": None,
		"next_action_due": None,
		"closed_at": now_dt,
		"closed_by": actor,
		"completion_reason": clean_reason or None,
		"last_activity_by": actor,
		"policy_version": policy_version,
		"revision": next_revision,
	}
	if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
		wi_updates["owner_at_close"] = old_owner
	if extra_wi_fields:
		wi_updates.update(extra_wi_fields)
	frappe.db.set_value("CSKH Work Item", work_item_val, wi_updates, update_modified=True)
	return canceled_task


def record_attempt(
	work_item: str,
	channel: str,
	outcome_code: str,
	occurred_at: str | None = None,
	note: str | None = None,
	call_log: str | None = None,
	call_log_data: dict | str | None = None,
	followup: dict | str | None = None,
	followup_due_at: str | None = None,
	followup_title: str | None = None,
	followup_note: str | None = None,
	complete_open_followup=True,
	close_work_item=False,
	completion_reason: str | None = None,
	expected_revision=None,
	expected_policy_version=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Atomic contact attempt recording (`docs/api-contract-p2.md` §4.5)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not wi_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	channel_val = (parse_optional_str(channel, "Kênh liên hệ không hợp lệ.") or "").lower()
	if not channel_val or channel_val not in VALID_CHANNELS:
		raise InvalidChannel("Kênh liên hệ không hợp lệ.")
	outcome_val = (parse_optional_str(outcome_code, "Mã kết quả không hợp lệ.") or "").lower()
	if not outcome_val:
		raise InvalidOutcome("Mã kết quả liên hệ (outcome_code) là bắt buộc.")

	occurred_raw = parse_optional_str(occurred_at, "Thời gian liên hệ (occurred_at) không hợp lệ.")
	note_raw = parse_optional_str(note, "Ghi chú liên hệ không hợp lệ.")
	clean_note = sanitize_note_text(note_raw, max_length=4000)
	comp_reason_raw = parse_optional_str(completion_reason, "Lý do hoàn tất không hợp lệ.")
	clean_comp_reason = sanitize_note_text(comp_reason_raw, max_length=1000)
	call_log_val = parse_optional_str(call_log, "Mã CRM Call Log không hợp lệ.")
	parsed_call_data = parse_call_log_data(call_log_data)
	followup_spec = _parse_followup_spec(followup, followup_due_at, followup_title, followup_note)
	complete_open_b = parse_bool(complete_open_followup, default=True, field="complete_open_followup")
	close_item_b = parse_bool(close_work_item, default=False, field="close_work_item")
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)
	exp_pol_ver = (
		parse_int(expected_policy_version, minimum=1, field="expected_policy_version")
		if expected_policy_version is not None and str(expected_policy_version).strip() != ""
		else None
	)

	# Pre-check scope before acquiring row lock (avoids cross-team oracle leaks).
	wi_preview = frappe.db.get_value(
		"CSKH Work Item",
		wi_val,
		_wi_preview_fields(),
		as_dict=True,
	)
	if not wi_preview:
		if _is_system(actor) or ROLE_ADMIN in _roles(actor):
			raise NotFound("Không tìm thấy Work Item.")
		raise Forbidden("Bạn không có quyền truy cập Work Item này.")
	if not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
		if wi_preview.status in ("queued", "closed", "revoked"):
			# Allow idempotent replay lookup if the caller still has read scope (e.g. closed item
			# closed by this actor/team whose Lead has not moved to another user).
			has_prior_idem = frappe.db.exists(
				"CSKH Contact Attempt",
				{"work_item": wi_val, "performed_by": actor, "idempotency_key": idem_val},
			)
			if not (has_prior_idem and is_work_item_readable_for_workflow(wi_preview, user=actor)):
				if is_work_item_readable_for_workflow(wi_preview, user=actor):
					raise WorkItemNotActive(
						"Work Item không ở trạng thái hoạt động để ghi nhận liên hệ.",
						details={"status": wi_preview.status},
					)
				if _is_staff(actor):
					last_hist = frappe.db.get_value(
						"CSKH Assignment History",
						{"work_item": wi_val},
						["from_user", "to_user", "action"],
						order_by="event_at desc, creation desc",
						as_dict=True,
					)
					if last_hist and last_hist.action == "revoke" and last_hist.from_user == actor:
						raise OwnershipChanged("Work Item đã bị thu hồi khỏi quyền phụ trách của bạn.")
					raise Forbidden("Bạn không có quyền thao tác trên Work Item này.")
				raise Forbidden("Bạn không có quyền thao tác trên Work Item ngoài phạm vi team.")

	req_hash = _payload_hash(
		{
			"op": "record_attempt",
			"work_item": wi_val,
			"channel": channel_val,
			"outcome_code": outcome_val,
			"occurred_at": occurred_raw or "",
			"note": clean_note,
			"call_log": call_log_val or "",
			"call_log_data": parsed_call_data or {},
			"followup": followup_spec or {},
			"complete_open_followup": complete_open_b,
			"close_work_item": close_item_b,
			"completion_reason": clean_comp_reason,
		}
	)

	new_task_id = None
	notify_owner = None
	savepoint = f"cskh_rec_att_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked = _lock_work_item(wi_val)

		# 1. Idempotency lookup under `SELECT ... FOR UPDATE` on Work Item
		existing_att = frappe.db.get_value(
			"CSKH Contact Attempt",
			{"work_item": wi_val, "performed_by": actor, "idempotency_key": idem_val},
			[
				"name",
				"payload_hash",
				"call_log",
				"callback_task",
				"resolved_callback_task",
				"dnc_snapshot",
			],
			as_dict=True,
		)
		if existing_att:
			# Block replay if ownership moved away after the attempt (`SEC2-07`).
			if not (
				_is_system(actor)
				or ROLE_ADMIN in _roles(actor)
				or is_work_item_readable_for_workflow(locked, user=actor)
			):
				raise Forbidden("Bạn không còn quyền truy cập Work Item này sau khi chuyển giao.")
			if existing_att.payload_hash != req_hash:
				raise IdempotencyConflict(
					"Khóa idempotency_key đã được sử dụng với dữ liệu nhật ký liên hệ khác."
				)
			dnc_flag = False
			if frappe.db.has_column("CRM Lead", "cskh_do_not_contact"):
				dnc_flag = bool(cint(frappe.db.get_value("CRM Lead", locked.lead, "cskh_do_not_contact")))
			return {
				"status": "recorded",
				"idempotent_replay": True,
				"attempt_id": existing_att.name,
				"work_item": wi_val,
				"work_item_status": locked.status,
				"revision": cint(locked.revision or 1),
				"attempt_count": cint(locked.attempt_count or 0),
				"connected_attempt_count": cint(locked.connected_attempt_count or 0),
				"call_log": existing_att.call_log,
				"callback_task": existing_att.callback_task,
				"resolved_callback_task": existing_att.resolved_callback_task,
				"do_not_contact": dnc_flag,
			}

		# 2. Lifecycle & ownership check under row lock
		if locked.status not in OWNED_ACTIVE_WORK_ITEM_STATES or not locked.assigned_to:
			raise WorkItemNotActive(
				"Work Item không ở trạng thái hoạt động để ghi nhận liên hệ.",
				details={"status": locked.status},
			)
		if not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
			if _is_leader(actor):
				if not (locked.assigned_to == actor or _in_team(actor, locked.assigned_to)):
					raise OwnershipChanged("Work Item đã chuyển ra ngoài phạm vi team phụ trách.")
			elif _is_staff(actor):
				if locked.assigned_to != actor:
					raise OwnershipChanged("Work Item đã được chuyển người phụ trách hoặc thu hồi.")
			else:
				raise Forbidden("Bạn không có quyền thao tác trên Work Item này.")

		current_rev = cint(locked.revision or 1)
		if exp_rev is not None and current_rev != exp_rev:
			raise StaleWorkItem(
				"Work Item đã thay đổi (lệch revision); vui lòng tải lại trước khi lưu.",
				details={"expected_revision": exp_rev, "current_revision": current_rev},
			)

		# 3. Active ownership interval check
		open_event = frappe.db.get_value(
			"CSKH Assignment History",
			{"work_item": wi_val, "ownership_end": ["is", "not set"]},
			["name", "to_user", "ownership_start"],
			order_by="event_at desc, creation desc",
			as_dict=True,
		)
		if not open_event or open_event.to_user != locked.assigned_to:
			raise OwnershipChanged("Khoảng sở hữu đang mở của Work Item không còn khớp với người phụ trách.")

		# 4. Effective policy & DNC check
		policy = resolve_effective_policy(campaign=locked.campaign, owner_user=locked.assigned_to)
		if exp_pol_ver is not None and cint(policy["version"]) != exp_pol_ver:
			raise PolicyChanged(
				"Chính sách CSKH hiệu lực đã thay đổi phiên bản; vui lòng tải lại cấu hình.",
				details={"expected_policy_version": exp_pol_ver, "current_policy_version": policy["version"]},
			)

		if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and cint(
			frappe.db.get_value("CRM Lead", locked.lead, "cskh_do_not_contact")
		):
			raise DoNotContact(
				"Khách hàng đang ở trạng thái ngừng liên hệ (DNC); không được ghi thêm tương tác."
			)

		if channel_val not in policy["enabled_channels"]:
			raise InvalidChannel("Kênh liên hệ này không được bật trong chính sách hiệu lực.")

		# 5. Outcome Code validation & snapshot
		outcome_fields = [
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
				outcome_fields.append(opt_col)
		outcome_doc = frappe.db.get_value(
			"CSKH Outcome Code",
			outcome_val,
			outcome_fields,
			as_dict=True,
		)
		if not outcome_doc or not cint(outcome_doc.enabled):
			raise InvalidOutcome("Mã kết quả liên hệ không tồn tại hoặc đã ngừng sử dụng.")
		outcome = serialize_outcome(outcome_doc)
		if channel_val not in outcome["allowed_channels"]:
			raise OutcomeNotCompatible(
				f"Mã kết quả '{outcome['code']}' không áp dụng cho kênh '{channel_val}'."
			)

		# 6. `occurred_at` window & active ownership interval boundary
		now_dt = now_datetime()
		if occurred_raw:
			try:
				occurred_dt = get_datetime(occurred_raw)
			except Exception as exc:
				raise InvalidFollowupTime("Định dạng thời gian liên hệ (occurred_at) không hợp lệ.") from exc
			if occurred_dt is None:
				raise InvalidFollowupTime("Định dạng thời gian liên hệ (occurred_at) không hợp lệ.")
		else:
			occurred_dt = now_dt

		if occurred_dt > add_to_date(now_dt, seconds=5):
			raise InvalidFollowupTime("Thời điểm liên hệ (occurred_at) không được nằm ở tương lai.")
		max_backdate_sec = cint(policy["allowed_backdate_minutes"]) * 60 + 5
		if (get_datetime(now_dt) - get_datetime(occurred_dt)).total_seconds() > max_backdate_sec:
			raise InvalidFollowupTime(
				"Thời điểm liên hệ vượt quá khoảng lùi thời gian cho phép của chính sách."
			)
		if open_event.ownership_start and get_datetime(occurred_dt) < get_datetime(
			open_event.ownership_start
		):
			raise InvalidFollowupTime(
				"Thời điểm liên hệ không được sớm hơn thời điểm bắt đầu sở hữu hiện tại."
			)

		# 7. Note, Follow-up & Close compatibility
		if outcome["requires_note"] and not clean_note:
			raise NoteRequired("Mã kết quả này bắt buộc nhập ghi chú chi tiết.")

		auto_close_dnc = bool(
			outcome["is_do_not_contact"] and policy["do_not_contact_behavior"] == "block_and_auto_close"
		)
		should_close = bool(close_item_b or auto_close_dnc)

		if followup_spec:
			if not outcome["allows_followup"] or outcome["is_do_not_contact"] or should_close:
				raise OutcomeNotCompatible(
					"Không được đặt lịch hẹn lại khi kết quả là kết thúc/DNC hoặc yêu cầu đóng Work Item."
				)
			_parse_due_datetime(followup_spec["due_at"], now=now_dt)
		else:
			if outcome["requires_followup"]:
				if should_close:
					raise OutcomeNotCompatible(
						"Kết quả này yêu cầu đặt lịch hẹn liên hệ lại, không được đóng Work Item ngay."
					)
				raise FollowupRequired("Kết quả này bắt buộc đặt lịch hẹn liên hệ lại (followup.due_at).")

		if should_close:
			if close_item_b and outcome["requires_completion_reason"] and not clean_comp_reason:
				raise NoteRequired("Kết quả này bắt buộc nhập lý do hoàn tất (completion_reason) khi đóng.")
			# F-P2-01: never copy customer `note` into `completion_reason` or `CSKH Assignment History`.
			effective_close_reason = clean_comp_reason or (
				f"Tự động đóng theo kết quả {outcome['label_vi']}"
				if outcome["is_do_not_contact"]
				else f"Hoàn tất theo kết quả {outcome['label_vi']}"
			)
			close_public_reason = ownership.public_history_reason(
				"close", is_dnc=bool(outcome["is_do_not_contact"])
			)
		else:
			effective_close_reason = ""
			close_public_reason = ""

		# 8. `CRM Call Log` link / creation
		linked_call_log, verification_status = resolve_or_create_call_log_in_tx(
			channel=channel_val,
			lead=locked.lead,
			actor=actor,
			call_log=call_log_val,
			call_log_data=parsed_call_data,
			outcome_code=outcome["code"],
		)

		# 9. Insert immutable `CSKH Contact Attempt`
		is_conv_flag = bool(outcome.get("is_conversion"))
		if (
			not is_conv_flag
			and locked.campaign
			and frappe.db.has_column("CSKH Campaign", "conversion_outcome_codes")
		):
			raw_camp_conv = (
				frappe.db.get_value("CSKH Campaign", locked.campaign, "conversion_outcome_codes") or ""
			)
			camp_conv_codes = {c.strip().lower() for c in str(raw_camp_conv).split(",") if c.strip()}
			if outcome["code"].lower() in camp_conv_codes:
				is_conv_flag = True

		prev_att_flag = getattr(frappe.flags, "cskh_in_attempt_service", False)
		frappe.flags.cskh_in_attempt_service = True
		try:
			att_payload = {
				"doctype": "CSKH Contact Attempt",
				"work_item": wi_val,
				"lead": locked.lead,
				"campaign": locked.campaign,
				"ownership_event": open_event.name,
				"owner_at_attempt": locked.assigned_to,
				"performed_by": actor,
				"occurred_at": occurred_dt,
				"recorded_at": now_dt,
				"channel": channel_val,
				"outcome_code": outcome["code"],
				"outcome_label_snapshot": outcome["label_vi"],
				"outcome_group_snapshot": outcome["result_group"],
				"outcome_version_snapshot": outcome["version"],
				"contact_attempt_snapshot": 1 if outcome["is_contact_attempt"] else 0,
				"connected_snapshot": 1 if outcome["is_connected"] else 0,
				"interested_snapshot": 1 if outcome["is_interested"] else 0,
				"terminal_snapshot": 1 if outcome["is_terminal"] else 0,
				"dnc_snapshot": 1 if outcome["is_do_not_contact"] else 0,
				"note": clean_note,
				"call_log": linked_call_log,
				"verification_status": verification_status,
				"policy_name_snapshot": policy["name"],
				"policy_version_snapshot": policy["version"],
				"idempotency_key": idem_val,
				"payload_hash": req_hash,
				"work_item_revision": current_rev + 1,
			}
			if frappe.db.has_column("CSKH Contact Attempt", "conversion_snapshot"):
				att_payload["conversion_snapshot"] = 1 if is_conv_flag else 0
			if frappe.db.has_column("CSKH Contact Attempt", "invalid_phone_snapshot"):
				att_payload["invalid_phone_snapshot"] = 1 if outcome.get("is_invalid_phone") else 0
			if frappe.db.has_column("CSKH Contact Attempt", "complete_fields"):
				att_payload["complete_fields"] = 1
			attempt_doc = frappe.get_doc(att_payload)
			attempt_doc.insert(ignore_permissions=True)
		finally:
			frappe.flags.cskh_in_attempt_service = prev_att_flag

		attempt_id = attempt_doc.name

		# 10. Resolve existing open callback `CRM Task` (if any), fulfill SLA obligations, & create new follow-up `CRM Task`
		resolved_task_id = None
		open_task_before = get_open_followup_task(wi_val)
		if open_task_before:
			if complete_open_b:
				resolved_task_id = resolve_open_followup_in_tx(
					work_item=wi_val,
					attempt_id=attempt_id,
					actor=actor,
					now=now_dt,
					occurred_at=occurred_dt,
					reason=f"Đã thực hiện liên hệ ({outcome['label_vi']})",
				)
				if resolved_task_id:
					frappe.db.set_value(
						"CSKH Contact Attempt",
						attempt_id,
						"resolved_callback_task",
						resolved_task_id,
						update_modified=False,
					)
			elif followup_spec:
				raise Conflict(
					"Work Item đang có lịch hẹn mở; cần bật complete_open_followup=true khi tạo lịch hẹn mới.",
					code="CONFLICT",
				)

		from crm_cskh.services import sla_engine

		sla_engine.on_attempt_recorded_in_tx(
			work_item=wi_val,
			attempt_id=attempt_id,
			occurred_at=occurred_dt,
			outcome=outcome,
			actor=actor,
		)

		if followup_spec:
			new_task_id = create_followup_in_tx(
				locked,
				due_at=followup_spec["due_at"],
				actor=actor,
				title=followup_spec["title"] or None,
				description=followup_spec["description"] or clean_note or None,
				source_attempt=attempt_id,
				idempotency_key=f"att-fu:{idem_val}",
				payload_hash=req_hash,
				now=now_dt,
			)
			notify_owner = locked.assigned_to
			frappe.db.set_value(
				"CSKH Contact Attempt",
				attempt_id,
				"callback_task",
				new_task_id,
				update_modified=False,
			)

		# 11. Persist global DNC flag on `CRM Lead` when outcome is `is_do_not_contact`
		dnc_active = False
		if outcome["is_do_not_contact"] and frappe.db.has_column("CRM Lead", "cskh_do_not_contact"):
			dnc_active = True
			set_lead_dnc_fields(
				locked.lead,
				{
					"cskh_do_not_contact": 1,
					"cskh_dnc_reason": clean_comp_reason or outcome["label_vi"],
					"cskh_dnc_at": now_dt,
					"cskh_dnc_work_item": wi_val,
				},
				update_modified=True,
			)

		# 12. Recompute read-model counters & transition `CSKH Work Item`
		next_rev = current_rev + 1
		if should_close:
			recomputed = recompute_work_item_read_model(
				wi_val,
				bump_revision=False,
				update_status_when_active=False,
			)
			_close_work_item_in_tx(
				locked=locked,
				actor=actor,
				now_dt=now_dt,
				clean_reason=effective_close_reason,
				event_id=f"close-att:{wi_val}:{actor}:{idem_val}",
				next_revision=next_rev,
				policy_version=policy["version"],
				public_reason=close_public_reason,
				payload_hash=req_hash,
				is_dnc=bool(outcome["is_do_not_contact"]),
			)
			final_status = "closed"
			final_callback = None
		else:
			recomputed = recompute_work_item_read_model(
				wi_val,
				bump_revision=False,
				update_status_when_active=True,
				extra_updates={
					"last_activity_by": actor,
					"policy_version": policy["version"],
					"revision": next_rev,
				},
			)
			final_status = recomputed["status"]
			final_callback = new_task_id or recomputed["open_callback_task"]

		from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation

		schedule_post_commit_dirty_invalidation(
			work_item=wi_val,
			campaign=locked.campaign,
			owner_user=actor,
			event_dt=occurred_dt,
			reason="attempt_recorded",
			trigger_reference=attempt_id,
		)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()

	# Post-commit best-effort notification delivery (never rolls back committed attempt)
	if new_task_id and notify_owner:
		notifications.deliver_followup_notification(
			work_item=wi_val,
			task_id=new_task_id,
			owner=notify_owner,
			event_id=f"att-fu:{wi_val}:{idem_val}",
			actor=actor,
		)

	return {
		"status": "recorded",
		"idempotent_replay": False,
		"attempt_id": attempt_id,
		"work_item": wi_val,
		"work_item_status": final_status,
		"revision": next_rev,
		"attempt_count": recomputed["attempt_count"],
		"connected_attempt_count": recomputed["connected_attempt_count"],
		"call_log": linked_call_log,
		"callback_task": final_callback if new_task_id else None,
		"resolved_callback_task": resolved_task_id,
		"do_not_contact": dnc_active,
	}


def close_work_item(
	work_item: str,
	completion_reason: str | None = None,
	close_category: str = "normal",
	expected_revision=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Explicitly close a `CSKH Work Item` (`docs/api-contract-p2.md` §4.10)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not wi_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	cat_val = (parse_optional_str(close_category, "Tham số close_category không hợp lệ.") or "normal").lower()
	if cat_val not in ("normal", "invalid_or_duplicate"):
		raise InvalidRequest("Tham số close_category chỉ chấp nhận normal | invalid_or_duplicate.")
	clean_reason = sanitize_note_text(
		parse_optional_str(completion_reason, "Lý do hoàn tất không hợp lệ."), max_length=1000
	)
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)

	req_hash = _payload_hash(
		{
			"op": "close_work_item",
			"work_item": wi_val,
			"completion_reason": clean_reason,
			"close_category": cat_val,
		}
	)
	event_id = f"close:{wi_val}:{actor}:{idem_val}"

	# Pre-check scope
	wi_preview = frappe.db.get_value(
		"CSKH Work Item",
		wi_val,
		_wi_preview_fields(),
		as_dict=True,
	)
	if not wi_preview:
		if _is_system(actor) or ROLE_ADMIN in _roles(actor):
			raise NotFound("Không tìm thấy Work Item.")
		raise Forbidden("Bạn không có quyền truy cập Work Item này.")

	savepoint = f"cskh_close_wi_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked = _lock_work_item(wi_val)
		existing_hist = frappe.db.get_value(
			"CSKH Assignment History",
			{"event_id": event_id},
			_history_idempotency_fields(),
			as_dict=True,
		)
		if existing_hist:
			if not ownership.matches_history_payload_hash(existing_hist, req_hash):
				raise IdempotencyConflict("Khóa idempotency_key đã được dùng với thông tin đóng khác.")
			closed_fields = ["status", "closed_at", "closed_by", "revision"]
			if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
				closed_fields.append("owner_at_close")
			wi_closed = frappe.db.get_value("CSKH Work Item", wi_val, closed_fields, as_dict=True)
			return {
				"status": "closed",
				"idempotent_replay": True,
				"work_item": wi_val,
				"work_item_status": wi_closed.status,
				"closed_at": wi_closed.closed_at or existing_hist.event_at,
				"closed_by": wi_closed.closed_by or actor,
				"owner_at_close": wi_closed.get("owner_at_close")
				or existing_hist.get("from_user")
				or wi_closed.closed_by
				or actor,
				"canceled_followup_task": None,
				"revision": cint(wi_closed.revision or 1),
			}

		if locked.status not in OWNED_ACTIVE_WORK_ITEM_STATES or not locked.assigned_to:
			if not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
				if not is_work_item_readable_for_workflow(locked, user=actor):
					if _is_staff(actor):
						last_h = frappe.db.get_value(
							"CSKH Assignment History",
							{"work_item": wi_val},
							["action", "from_user"],
							order_by="event_at desc, creation desc",
							as_dict=True,
						)
						if last_h and last_h.action == "close" and last_h.from_user == actor:
							raise WorkItemNotActive(
								"Work Item đã ở trạng thái đóng.", details={"status": locked.status}
							)
					raise Forbidden("Bạn không có quyền thao tác trên Work Item này.")
			raise WorkItemNotActive(
				"Chỉ Work Item đang hoạt động mới có thể đóng.",
				details={"status": locked.status},
			)

		if not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
			if _is_leader(actor):
				if not (locked.assigned_to == actor or _in_team(actor, locked.assigned_to)):
					raise OwnershipChanged("Work Item đã chuyển ra ngoài phạm vi team phụ trách.")
			elif _is_staff(actor):
				if locked.assigned_to != actor:
					raise OwnershipChanged("Work Item đã được chuyển người phụ trách hoặc thu hồi.")
			else:
				raise Forbidden("Bạn không có quyền thao tác trên Work Item này.")

		current_rev = cint(locked.revision or 1)
		if exp_rev is not None and current_rev != exp_rev:
			raise StaleWorkItem(
				"Work Item đã thay đổi (lệch revision); vui lòng tải lại trước khi đóng.",
				details={"expected_revision": exp_rev, "current_revision": current_rev},
			)

		policy = resolve_effective_policy(campaign=locked.campaign, owner_user=locked.assigned_to)
		projected = compute_effective_attempts(wi_val)
		summary = summarize_effective_attempts(projected)

		lead_dnc = False
		if frappe.db.has_column("CRM Lead", "cskh_do_not_contact"):
			lead_dnc = bool(cint(frappe.db.get_value("CRM Lead", locked.lead, "cskh_do_not_contact")))

		if summary["attempt_count"] == 0:
			if lead_dnc:
				if not clean_reason:
					raise NoteRequired("Đóng Work Item do DNC bắt buộc nhập lý do hoàn tất.")
			elif cat_val == "invalid_or_duplicate" and policy["allow_close_without_contact_for_invalid"]:
				if not clean_reason:
					raise NoteRequired(
						"Đóng Work Item dữ liệu sai/trùng không có liên hệ bắt buộc nhập lý do."
					)
			elif not policy["require_attempt_before_close"]:
				if not clean_reason:
					raise NoteRequired("Đóng Work Item không có nhật ký liên hệ bắt buộc nhập lý do.")
			else:
				raise OutcomeNotCompatible(
					"Chính sách yêu cầu có ít nhất 01 nhật ký liên hệ hợp lệ trước khi đóng Work Item."
				)
		else:
			last_code = summary["last_outcome_code"]
			if last_code:
				last_out = frappe.db.get_value(
					"CSKH Outcome Code",
					last_code,
					["requires_followup", "requires_completion_reason"],
					as_dict=True,
				)
				if last_out:
					if cint(last_out.requires_followup):
						raise OutcomeNotCompatible(
							"Kết quả liên hệ gần nhất yêu cầu chăm sóc/hẹn lại, chưa đủ điều kiện đóng."
						)
					if cint(last_out.requires_completion_reason) and not clean_reason:
						raise NoteRequired(
							"Kết quả gần nhất yêu cầu nhập lý do hoàn tất (completion_reason)."
						)

		now_dt = now_datetime()
		next_rev = current_rev + 1
		recompute_work_item_read_model(wi_val, bump_revision=False, update_status_when_active=False)
		close_pub_reason = ownership.public_history_reason(
			"close",
			is_dnc=bool(lead_dnc and summary["attempt_count"] == 0),
			close_category=cat_val,
		)
		old_owner_at_close = locked.assigned_to
		canceled_task = _close_work_item_in_tx(
			locked=locked,
			actor=actor,
			now_dt=now_dt,
			clean_reason=clean_reason,
			event_id=event_id,
			next_revision=next_rev,
			policy_version=policy["version"],
			public_reason=close_pub_reason,
			payload_hash=req_hash,
			close_category=cat_val,
			is_dnc=bool(lead_dnc),
		)

		from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation

		schedule_post_commit_dirty_invalidation(
			work_item=wi_val,
			campaign=locked.campaign,
			owner_user=old_owner_at_close,
			event_dt=now_dt,
			reason="work_item_closed",
		)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()
	return {
		"status": "closed",
		"idempotent_replay": False,
		"work_item": wi_val,
		"work_item_status": "closed",
		"closed_at": now_dt,
		"closed_by": actor,
		"owner_at_close": old_owner_at_close,
		"canceled_followup_task": canceled_task,
		"revision": next_rev,
	}


def reopen_work_item(
	work_item: str,
	target_user: str,
	reason: str | None = None,
	override_dnc=False,
	expected_revision=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Reopen a `closed` `CSKH Work Item` with full audit & claim restoration (`docs/api-contract-p2.md` §4.11)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	roles = _roles(actor)
	is_admin = _is_system(actor) or ROLE_ADMIN in roles
	is_leader = _is_leader(actor)
	if not (is_admin or is_leader):
		raise Forbidden("Nhân viên không được tự mở lại Work Item đã đóng.")

	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not wi_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	target_val = parse_optional_str(target_user, "Người nhận phụ trách (target_user) không hợp lệ.")
	if not target_val:
		raise InvalidRequest("Người nhận phụ trách (target_user) là bắt buộc khi mở lại.")
	clean_reason = sanitize_note_text(
		parse_optional_str(reason, "Lý do mở lại không hợp lệ."), max_length=1000
	)
	if not clean_reason:
		raise NoteRequired("Mở lại Work Item bắt buộc ghi rõ lý do kiểm toán.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	override_dnc_b = parse_bool(override_dnc, default=False, field="override_dnc")
	if override_dnc_b and not is_admin:
		raise Forbidden("Chỉ CSKH CRM Admin được phép ghi đè cờ DNC khi mở lại.")
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)

	assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	req_hash = _payload_hash(
		{
			"op": "reopen_work_item",
			"work_item": wi_val,
			"target_user": target_val,
			"reason": clean_reason,
			"override_dnc": override_dnc_b,
		}
	)
	event_id = f"reopen:{wi_val}:{actor}:{idem_val}"

	savepoint = f"cskh_reopen_wi_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked = _lock_work_item(wi_val)
		existing_hist = frappe.db.get_value(
			"CSKH Assignment History",
			{"event_id": event_id},
			_history_idempotency_fields(),
			as_dict=True,
		)
		if existing_hist:
			if not ownership.matches_history_payload_hash(existing_hist, req_hash):
				raise IdempotencyConflict("Khóa idempotency_key đã được dùng với thông tin mở lại khác.")
			return {
				"status": "reopened",
				"idempotent_replay": True,
				"work_item": wi_val,
				"work_item_status": locked.status,
				"assigned_to": locked.assigned_to or existing_hist.to_user,
				"revision": cint(locked.revision or 1),
			}

		if locked.status != "closed":
			raise WorkItemNotActive(
				"Chỉ Work Item ở trạng thái đã đóng (closed) mới được phép mở lại.",
				details={"status": locked.status},
			)

		current_rev = cint(locked.revision or 1)
		if exp_rev is not None and current_rev != exp_rev:
			raise StaleWorkItem(
				"Work Item đã thay đổi (lệch revision); vui lòng tải lại trước khi mở lại.",
				details={"expected_revision": exp_rev, "current_revision": current_rev},
			)

		policy = resolve_effective_policy(campaign=locked.campaign, owner_user=target_val)
		if not is_admin:
			if not policy["allow_leader_reopen"]:
				raise Forbidden("Chính sách hiện tại không cho phép Team Leader mở lại Work Item.")
			if not (target_val == actor or _in_team(actor, target_val)):
				raise Forbidden("Team Leader chỉ được giao mở lại cho nhân viên thuộc team của mình.")

		dnc_overridden = False
		if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and cint(
			frappe.db.get_value("CRM Lead", locked.lead, "cskh_do_not_contact")
		):
			if not (is_admin and override_dnc_b):
				raise DoNotContact("Khách hàng đang có cờ ngừng liên hệ (DNC); không được mở lại.")
			dnc_overridden = True

		# Ensure no other active claim or active owned work item holds this Lead
		other_claim = frappe.db.get_value(
			"CSKH Active Claim", {"lead": locked.lead}, ["work_item", "campaign"], as_dict=True
		)
		if other_claim and other_claim.work_item != wi_val:
			raise Conflict(
				"Khách hàng hiện đang có Active Claim ở một chiến dịch/Work Item khác.",
				code="ALREADY_ASSIGNED",
				details={"campaign": other_claim.campaign, "work_item": other_claim.work_item},
			)
		other_owned = frappe.db.get_value(
			"CSKH Work Item",
			{
				"lead": locked.lead,
				"status": ["in", list(OWNED_ACTIVE_WORK_ITEM_STATES)],
				"name": ["!=", wi_val],
			},
			["name", "campaign"],
			as_dict=True,
		)
		if other_owned:
			raise Conflict(
				"Khách hàng hiện đang được chăm sóc ở một Work Item khác.",
				code="ALREADY_ASSIGNED",
				details={"campaign": other_owned.campaign, "work_item": other_owned.name},
			)

		target_val = ownership._require_campaign_target_eligibility(locked.campaign, target_val)

		if dnc_overridden:
			set_lead_dnc_fields(
				locked.lead,
				{
					"cskh_do_not_contact": 0,
					"cskh_dnc_reason": f"Gỡ DNC khi Admin mở lại {wi_val}: {clean_reason}",
					"cskh_dnc_at": None,
				},
				update_modified=True,
			)
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Info",
					"reference_doctype": "CRM Lead",
					"reference_name": locked.lead,
					"comment_email": actor,
					"content": f"[CSKH_DNC_OVERRIDE:{wi_val}:{idem_val}:{req_hash}] Gỡ cờ DNC khi mở lại Work Item: {clean_reason}",
				}
			).insert(ignore_permissions=True)

		summary = summarize_effective_attempts(compute_effective_attempts(wi_val))
		new_status = "in_progress" if summary["attempt_count"] > 0 else "assigned"
		now_dt = now_datetime()
		next_rev = current_rev + 1

		reopen_updates = {
			**summary,
			"status": new_status,
			"assigned_to": target_val,
			"assigned_at": now_dt,
			"open_callback_task": None,
			"next_action_due": None,
			"closed_at": None,
			"closed_by": None,
			"reopened_at": now_dt,
			"reopened_by": actor,
			"reopen_reason": clean_reason,
			"last_activity_by": actor,
			"policy_version": policy["version"],
			"revision": next_rev,
		}
		if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
			reopen_updates["owner_at_close"] = None
		frappe.db.set_value(
			"CSKH Work Item",
			wi_val,
			reopen_updates,
			update_modified=True,
		)

		if not frappe.db.exists("CSKH Active Claim", {"lead": locked.lead}):
			try:
				frappe.get_doc(
					{
						"doctype": "CSKH Active Claim",
						"lead": locked.lead,
						"campaign": locked.campaign,
						"work_item": wi_val,
					}
				).insert(ignore_permissions=True)
			except Exception as exc:
				raise Conflict(
					"Khách hàng đã có Active Claim ở chiến dịch khác.",
					code="ALREADY_ASSIGNED",
				) from exc

		reopen_hist = ownership._insert_history(
			{
				"doctype": "CSKH Assignment History",
				"work_item": wi_val,
				"action": "reopen",
				"from_user": None,
				"to_user": target_val,
				"actor": actor,
				"event_at": now_dt,
				"reason": ownership.public_history_reason("reopen", override_dnc=override_dnc_b),
				"private_reason": clean_reason,
				"payload_hash": req_hash,
				"ownership_start": now_dt,
				"ownership_end": None,
				"event_id": event_id,
				"distribution_run": ownership._original_distribution_run(wi_val),
			}
		)

		from crm_cskh.services import sla_engine

		sla_engine.on_interval_opened_in_tx(
			work_item=wi_val,
			lead=locked.lead,
			campaign=locked.campaign,
			interval_event=reopen_hist.name,
			owner_user=target_val,
			started_at=now_dt,
			actor=actor,
			is_reopen=True,
		)

		ownership._sync_lead_owner(locked.lead, target_val)
		ownership._sync_docshares(locked.lead, [target_val])
		if not ownership._todo_for(locked.lead, target_val):
			ownership._create_todo(locked.lead, target_val, f"CSKH assignment ({wi_val})")
		ownership._sync_lead_owner(locked.lead, target_val)
		ownership._assert_final_owner(locked.lead, target_val)

		from crm_cskh.services.kpi_runs import schedule_post_commit_dirty_invalidation

		schedule_post_commit_dirty_invalidation(
			work_item=wi_val,
			campaign=locked.campaign,
			owner_user=target_val,
			event_dt=now_dt,
			reason="work_item_reopened",
		)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()
	notifications.deliver_assignment(
		wi_val,
		target_val,
		event_id=event_id,
		actor=actor,
	)
	frappe.db.commit()
	return {
		"status": "reopened",
		"idempotent_replay": False,
		"work_item": wi_val,
		"work_item_status": new_status,
		"assigned_to": target_val,
		"revision": next_rev,
	}
