"""Policy resolution (`Campaign > Team > Global`), Outcome catalog & DNC admin service.

Implements `docs/followup-policy.md`, `docs/outcome-catalog.md`, and
`docs/api-contract-p2.md` (§4.6, §4.14).
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import cint, now_datetime

from crm_cskh.api._params import parse_bool, parse_int, parse_optional_str
from crm_cskh.p2_models import (
	DEFAULT_GLOBAL_POLICY,
	DNC_BEHAVIORS,
	RESULT_GROUPS,
	UNREACHED_DISCONNECTED_CODES,
	VALID_CHANNELS,
	parse_channels_csv,
	set_lead_dnc_fields,
)
from crm_cskh.p2_permissions import (
	ROLE_ADMIN,
	ROLE_BOD,
	_is_system,
	_roles,
	assert_settings_admin,
	assert_work_item_workflow_access,
	assert_workflow_actor,
)
from crm_cskh.p3_models import DEFAULT_P3_POLICY_FIELDS
from crm_cskh.services.errors import (
	IdempotencyConflict,
	InvalidChannel,
	InvalidRequest,
	NoteRequired,
	NotFound,
	OutcomeNotCompatible,
)
from crm_cskh.services.sanitizer import sanitize_note_text


def _payload_hash(payload: dict) -> str:
	canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_P3_BOOL_POLICY_KEYS = (
	"p3_sla_enabled",
	"p3_reporting_enabled",
	"qa_enabled",
	"track_first_connected",
	"min_attempts_enabled",
	"allow_leader_waive_sla",
	"allow_leader_pause_sla",
)

_P3_INT_POLICY_KEYS = (
	"first_attempt_sla_minutes",
	"followup_grace_minutes",
	"handoff_min_grace_minutes",
	"max_handoff_grace_resets",
	"due_soon_warning_minutes",
	"first_connected_target_minutes",
	"min_attempts_before_close",
	"min_attempt_gap_minutes",
	"max_reschedules_without_approval",
	"weight_first_attempt_sla",
	"weight_followup_sla",
	"weight_data_hygiene",
	"weight_callback_discipline",
	"weight_qa_quality",
	"min_valid_score_weight",
	"min_eligible_events_for_score",
	"min_qa_sample_size",
)


def serialize_policy(row_or_doc) -> dict:
	channels = [ch.strip() for ch in str(row_or_doc.get("enabled_channels") or "").split(",") if ch.strip()]
	payload = {
		"name": row_or_doc.get("name") or row_or_doc.get("policy_code"),
		"policy_code": row_or_doc.get("policy_code") or row_or_doc.get("name"),
		"title": row_or_doc.get("title") or "",
		"scope_type": row_or_doc.get("scope_type") or "Global",
		"team": row_or_doc.get("team"),
		"campaign": row_or_doc.get("campaign"),
		"enabled": bool(cint(row_or_doc.get("enabled", 1))),
		"version": cint(row_or_doc.get("version") or 1),
		"allowed_backdate_minutes": cint(row_or_doc.get("allowed_backdate_minutes") or 0),
		"max_open_followup_tasks_per_item": cint(row_or_doc.get("max_open_followup_tasks_per_item") or 1),
		"require_attempt_before_close": bool(cint(row_or_doc.get("require_attempt_before_close", 1))),
		"allow_close_without_contact_for_invalid": bool(
			cint(row_or_doc.get("allow_close_without_contact_for_invalid", 1))
		),
		"do_not_contact_behavior": row_or_doc.get("do_not_contact_behavior") or "block_and_auto_close",
		"enabled_channels": channels,
		"allow_leader_reopen": bool(cint(row_or_doc.get("allow_leader_reopen", 1))),
		"allow_leader_review_correction": bool(cint(row_or_doc.get("allow_leader_review_correction", 1))),
		"working_calendar": row_or_doc.get("working_calendar")
		or DEFAULT_P3_POLICY_FIELDS["working_calendar"],
		"is_dev_configurable_policy": True,
	}
	for b_key in _P3_BOOL_POLICY_KEYS:
		raw_b = row_or_doc.get(b_key)
		if raw_b is None:
			raw_b = DEFAULT_P3_POLICY_FIELDS[b_key]
		payload[b_key] = bool(cint(raw_b))
	for i_key in _P3_INT_POLICY_KEYS:
		raw_i = row_or_doc.get(i_key)
		if raw_i is None:
			raw_i = DEFAULT_P3_POLICY_FIELDS[i_key]
		payload[i_key] = cint(raw_i)
	return payload


def serialize_outcome(row_or_doc) -> dict:
	channels = [ch.strip() for ch in str(row_or_doc.get("allowed_channels") or "").split(",") if ch.strip()]
	return {
		"code": row_or_doc.get("code") or row_or_doc.get("name"),
		"label_vi": row_or_doc.get("label_vi") or "",
		"description": row_or_doc.get("description") or "",
		"result_group": row_or_doc.get("result_group") or "other",
		"is_contact_attempt": bool(cint(row_or_doc.get("is_contact_attempt", 1))),
		"is_connected": bool(cint(row_or_doc.get("is_connected", 0))),
		"is_interested": bool(cint(row_or_doc.get("is_interested", 0))),
		"is_terminal": bool(cint(row_or_doc.get("is_terminal", 0))),
		"is_do_not_contact": bool(cint(row_or_doc.get("is_do_not_contact", 0))),
		"requires_note": bool(cint(row_or_doc.get("requires_note", 0))),
		"requires_followup": bool(cint(row_or_doc.get("requires_followup", 0))),
		"allows_followup": bool(cint(row_or_doc.get("allows_followup", 0))),
		"requires_completion_reason": bool(cint(row_or_doc.get("requires_completion_reason", 0))),
		"allowed_channels": channels,
		"suggested_next_status": row_or_doc.get("suggested_next_status") or "in_progress",
		"version": cint(row_or_doc.get("version") or 1),
		"enabled": bool(cint(row_or_doc.get("enabled", 1))),
		"sort_order": cint(row_or_doc.get("sort_order") or 100),
	}


def _policy_fields() -> list[str]:
	base = [
		"name",
		"policy_code",
		"title",
		"scope_type",
		"team",
		"campaign",
		"enabled",
		"version",
		"allowed_backdate_minutes",
		"max_open_followup_tasks_per_item",
		"require_attempt_before_close",
		"allow_close_without_contact_for_invalid",
		"do_not_contact_behavior",
		"enabled_channels",
		"allow_leader_reopen",
		"allow_leader_review_correction",
	]
	if frappe.db.table_exists("CSKH Policy"):
		for col in ("working_calendar", *_P3_BOOL_POLICY_KEYS, *_P3_INT_POLICY_KEYS):
			if frappe.db.has_column("CSKH Policy", col):
				base.append(col)
	return base


def _resolve_team_policy_along_ancestors(leaf_node: str | None) -> dict | None:
	if not leaf_node:
		return None
	bounds = frappe.db.get_value("CRM Sales Hierarchy", leaf_node, ["lft", "rgt"], as_dict=True)
	if not bounds:
		return None
	ancestor_nodes = frappe.db.sql(
		"select name from `tabCRM Sales Hierarchy` where lft <= %s and rgt >= %s order by lft desc",
		(bounds.lft, bounds.rgt),
		pluck=True,
	)
	for node_name in ancestor_nodes or []:
		rows = frappe.db.get_all(
			"CSKH Policy",
			filters={"scope_type": "Team", "team": node_name, "enabled": 1},
			fields=_policy_fields(),
			order_by="version desc, modified desc",
			limit=1,
		)
		if rows:
			return rows[0]
	return None


def resolve_effective_policy(
	campaign: str | None = None,
	work_item: str | None = None,
	owner_user: str | None = None,
) -> dict:
	"""Resolve `Campaign > Team > Global` effective policy (`ADR-P2-05`)."""
	if work_item and (not campaign or not owner_user):
		wi_row = frappe.db.get_value("CSKH Work Item", work_item, ["campaign", "assigned_to"], as_dict=True)
		if wi_row:
			campaign = campaign or wi_row.campaign
			owner_user = owner_user or wi_row.assigned_to

	if frappe.db.table_exists("CSKH Policy"):
		# 1. Campaign-scoped policy
		if campaign:
			camp_rows = frappe.db.get_all(
				"CSKH Policy",
				filters={"scope_type": "Campaign", "campaign": campaign, "enabled": 1},
				fields=_policy_fields(),
				order_by="version desc, modified desc",
				limit=1,
			)
			if camp_rows:
				return serialize_policy(camp_rows[0])

		# 2. Team-scoped policy (campaign team_scope first, then assignee's team node)
		team_node = frappe.db.get_value("CSKH Campaign", campaign, "team_scope") if campaign else None
		if not team_node and owner_user:
			team_node = frappe.db.get_value("CRM Sales Hierarchy", {"user": owner_user}, "name")
		team_row = _resolve_team_policy_along_ancestors(team_node)
		if team_row:
			return serialize_policy(team_row)

		# 3. Global-scoped policy
		global_rows = frappe.db.get_all(
			"CSKH Policy",
			filters={"scope_type": "Global", "enabled": 1},
			fields=_policy_fields(),
			order_by="version desc, modified desc",
			limit=1,
		)
		if global_rows:
			return serialize_policy(global_rows[0])

	return serialize_policy({"name": DEFAULT_GLOBAL_POLICY["policy_code"], **DEFAULT_GLOBAL_POLICY})


def get_outcome_codes_for_workflow(
	channel: str | None = None,
	work_item: str | None = None,
	campaign: str | None = None,
	include_disabled=False,
	actor: str | None = None,
) -> dict:
	"""Return active Outcome Codes and resolved effective policy (`docs/api-contract-p2.md` §4.6)."""
	actor = assert_workflow_actor(actor, allow_bod=True)
	inc_dis = parse_bool(include_disabled, default=False, field="include_disabled")
	channel_val = parse_optional_str(channel, "Kênh liên hệ không hợp lệ.")
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	camp_val = parse_optional_str(campaign, "Mã chiến dịch không hợp lệ.")

	if inc_dis and not (_is_system(actor) or ROLE_ADMIN in _roles(actor)):
		from crm_cskh.services.errors import Forbidden

		raise Forbidden("Chỉ CSKH CRM Admin được xem các mã kết quả đang tắt.")

	owner_user = None
	if wi_val:
		wi_doc = assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)
		camp_val = camp_val or wi_doc.get("campaign")
		owner_user = wi_doc.get("assigned_to")

	policy = resolve_effective_policy(campaign=camp_val, owner_user=owner_user)

	if channel_val:
		channel_val = channel_val.lower()
		if channel_val not in VALID_CHANNELS:
			raise InvalidChannel("Kênh liên hệ không hợp lệ.")
		if channel_val not in policy["enabled_channels"]:
			raise InvalidChannel("Kênh liên hệ đang bị tắt theo chính sách hiệu lực.")

	filters = {} if inc_dis else {"enabled": 1}
	rows = frappe.db.get_all(
		"CSKH Outcome Code",
		filters=filters,
		fields=[
			"name",
			"code",
			"label_vi",
			"description",
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
			"sort_order",
		],
		order_by="sort_order asc, code asc",
	)

	items = []
	for row in rows:
		serialized = serialize_outcome(row)
		if channel_val and channel_val not in serialized["allowed_channels"]:
			continue
		items.append(serialized)

	return {
		"items": items,
		"effective_policy": policy,
		"total": len(items),
		"as_of": now_datetime(),
	}


def list_policies(
	scope_type: str | None = None,
	campaign: str | None = None,
	team: str | None = None,
	actor: str | None = None,
) -> dict:
	actor = assert_workflow_actor(actor, allow_bod=True)
	scope_val = parse_optional_str(scope_type, "Tham số scope_type không hợp lệ.")
	if scope_val and scope_val not in ("Global", "Team", "Campaign"):
		raise InvalidRequest("Tham số scope_type chỉ chấp nhận Global | Team | Campaign.")
	camp_val = parse_optional_str(campaign, "Tham số campaign không hợp lệ.")
	team_val = parse_optional_str(team, "Tham số team không hợp lệ.")

	prev_user = frappe.session.user
	try:
		if prev_user != actor:
			frappe.set_user(actor)
		filters: dict = {}
		if scope_val:
			filters["scope_type"] = scope_val
		if camp_val:
			filters["campaign"] = camp_val
		if team_val:
			filters["team"] = team_val
		rows = frappe.get_list(
			"CSKH Policy",
			filters=filters,
			fields=_policy_fields(),
			order_by="scope_type asc, policy_code asc",
			limit_page_length=0,
		)
	finally:
		if frappe.session.user != prev_user:
			frappe.set_user(prev_user)

	items = [serialize_policy(row) for row in rows]
	return {"items": items, "total": len(items), "as_of": now_datetime()}


def _parse_p3_policy_overrides(p3_options: dict) -> dict:
	overrides: dict = {}
	if not p3_options:
		return overrides
	if "working_calendar" in p3_options and p3_options["working_calendar"] is not None:
		cal_val = parse_optional_str(p3_options["working_calendar"], "Mã lịch làm việc không hợp lệ.")
		if cal_val and frappe.db.table_exists("CSKH Working Calendar"):
			if not frappe.db.exists("CSKH Working Calendar", cal_val):
				raise InvalidRequest(f"Lịch làm việc không tồn tại: {cal_val}")
		overrides["working_calendar"] = cal_val
	for b_key in _P3_BOOL_POLICY_KEYS:
		if b_key in p3_options and p3_options[b_key] is not None:
			overrides[b_key] = 1 if parse_bool(p3_options[b_key], field=b_key) else 0
	for i_key in _P3_INT_POLICY_KEYS:
		if i_key in p3_options and p3_options[i_key] is not None and str(p3_options[i_key]).strip() != "":
			overrides[i_key] = parse_int(p3_options[i_key], minimum=0, maximum=100800, field=i_key)
	return overrides


def save_policy(
	policy_code: str,
	title: str,
	scope_type: str = "Global",
	team: str | None = None,
	campaign: str | None = None,
	enabled=True,
	allowed_backdate_minutes=120,
	require_attempt_before_close=True,
	allow_close_without_contact_for_invalid=True,
	do_not_contact_behavior: str = "block_and_auto_close",
	enabled_channels: str | list = "phone_call,sms,zalo,facebook,other",
	allow_leader_reopen=True,
	allow_leader_review_correction=True,
	idempotency_key: str | None = None,
	actor: str | None = None,
	**p3_options,
) -> dict:
	actor = assert_settings_admin(actor)
	code_val = parse_optional_str(policy_code, "Mã chính sách không hợp lệ.")
	title_val = sanitize_note_text(parse_optional_str(title, "Tên chính sách không hợp lệ."), max_length=140)
	scope_val = parse_optional_str(scope_type, "Phạm vi chính sách không hợp lệ.") or "Global"
	team_val = parse_optional_str(team, "Nhánh team không hợp lệ.")
	camp_val = parse_optional_str(campaign, "Mã chiến dịch không hợp lệ.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")

	if not code_val or not title_val:
		raise InvalidRequest("Mã chính sách và tên chính sách là bắt buộc.")
	if scope_val not in ("Global", "Team", "Campaign"):
		raise InvalidRequest("Phạm vi chính sách chỉ chấp nhận Global | Team | Campaign.")
	if scope_val == "Team" and not team_val:
		raise InvalidRequest("Chính sách cấp Team bắt buộc truyền mã nhánh team.")
	if scope_val == "Campaign" and not camp_val:
		raise InvalidRequest("Chính sách cấp Campaign bắt buộc truyền mã chiến dịch.")
	if do_not_contact_behavior not in DNC_BEHAVIORS:
		raise InvalidRequest("Hành vi DNC không hợp lệ.")

	enabled_b = parse_bool(enabled, default=True, field="enabled")
	backdate_i = parse_int(
		allowed_backdate_minutes, minimum=0, maximum=10080, field="allowed_backdate_minutes"
	)
	req_att_b = parse_bool(require_attempt_before_close, default=True, field="require_attempt_before_close")
	allow_inv_b = parse_bool(
		allow_close_without_contact_for_invalid,
		default=True,
		field="allow_close_without_contact_for_invalid",
	)
	leader_reopen_b = parse_bool(allow_leader_reopen, default=True, field="allow_leader_reopen")
	leader_rev_b = parse_bool(
		allow_leader_review_correction, default=True, field="allow_leader_review_correction"
	)
	try:
		channels_list = parse_channels_csv(enabled_channels)
	except frappe.ValidationError as exc:
		raise InvalidChannel(str(exc)) from exc
	if not channels_list:
		raise InvalidChannel("Chính sách phải bật ít nhất một kênh liên hệ.")
	channels_csv = ",".join(channels_list)
	p3_overrides = _parse_p3_policy_overrides(p3_options)

	prev_flag = getattr(frappe.flags, "cskh_in_settings_service", False)
	frappe.flags.cskh_in_settings_service = True
	try:
		if frappe.db.exists("CSKH Policy", code_val):
			doc = frappe.get_doc("CSKH Policy", code_val)
			target_fields = {
				"title": title_val,
				"scope_type": scope_val,
				"team": team_val if scope_val == "Team" else None,
				"campaign": camp_val if scope_val == "Campaign" else None,
				"enabled": 1 if enabled_b else 0,
				"allowed_backdate_minutes": backdate_i,
				"max_open_followup_tasks_per_item": 1,
				"require_attempt_before_close": 1 if req_att_b else 0,
				"allow_close_without_contact_for_invalid": 1 if allow_inv_b else 0,
				"do_not_contact_behavior": do_not_contact_behavior,
				"enabled_channels": channels_csv,
				"allow_leader_reopen": 1 if leader_reopen_b else 0,
				"allow_leader_review_correction": 1 if leader_rev_b else 0,
			}
			for k, v in p3_overrides.items():
				if frappe.db.has_column("CSKH Policy", k):
					target_fields[k] = v
			changed = any(
				(cint(doc.get(k)) != cint(v) if isinstance(v, int) else (doc.get(k) or None) != (v or None))
				for k, v in target_fields.items()
			)
			if changed:
				for k, v in target_fields.items():
					doc.set(k, v)
				doc.version = cint(doc.version or 1) + 1
				try:
					doc.save(ignore_permissions=True)
				except frappe.ValidationError as exc:
					raise InvalidRequest(str(exc)) from exc
			status_str = "updated" if changed else "unchanged"
		else:
			create_fields = {
				"doctype": "CSKH Policy",
				"policy_code": code_val,
				"title": title_val,
				"scope_type": scope_val,
				"team": team_val if scope_val == "Team" else None,
				"campaign": camp_val if scope_val == "Campaign" else None,
				"enabled": 1 if enabled_b else 0,
				"version": 1,
				"allowed_backdate_minutes": backdate_i,
				"max_open_followup_tasks_per_item": 1,
				"require_attempt_before_close": 1 if req_att_b else 0,
				"allow_close_without_contact_for_invalid": 1 if allow_inv_b else 0,
				"do_not_contact_behavior": do_not_contact_behavior,
				"enabled_channels": channels_csv,
				"allow_leader_reopen": 1 if leader_reopen_b else 0,
				"allow_leader_review_correction": 1 if leader_rev_b else 0,
			}
			for k, default_v in DEFAULT_P3_POLICY_FIELDS.items():
				if frappe.db.has_column("CSKH Policy", k):
					if k == "working_calendar" and not frappe.db.exists("CSKH Working Calendar", default_v):
						continue
					create_fields[k] = default_v
			for k, v in p3_overrides.items():
				if frappe.db.has_column("CSKH Policy", k):
					create_fields[k] = v
			doc = frappe.get_doc(create_fields)
			try:
				doc.insert(ignore_permissions=True)
			except frappe.ValidationError as exc:
				raise InvalidRequest(str(exc)) from exc
			status_str = "created"
	finally:
		frappe.flags.cskh_in_settings_service = prev_flag

	frappe.db.commit()
	return {"status": status_str, "policy": serialize_policy(doc), "idempotency_key": idem_val}


def list_outcomes(include_disabled=True, actor: str | None = None) -> dict:
	actor = assert_workflow_actor(actor, allow_bod=True)
	inc_dis = parse_bool(include_disabled, default=True, field="include_disabled")
	can_see_disabled = _is_system(actor) or bool({ROLE_ADMIN, ROLE_BOD} & _roles(actor))
	filters = {} if (inc_dis and can_see_disabled) else {"enabled": 1}
	rows = frappe.db.get_all(
		"CSKH Outcome Code",
		filters=filters,
		fields=[
			"name",
			"code",
			"label_vi",
			"description",
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
			"sort_order",
		],
		order_by="sort_order asc, code asc",
	)
	items = [serialize_outcome(r) for r in rows]
	return {"items": items, "total": len(items), "as_of": now_datetime()}


def save_outcome(
	code: str,
	label_vi: str,
	result_group: str,
	description: str | None = None,
	is_contact_attempt=True,
	is_connected=False,
	is_interested=False,
	is_terminal=False,
	is_do_not_contact=False,
	requires_note=False,
	requires_followup=False,
	allows_followup=True,
	requires_completion_reason=False,
	allowed_channels: str | list = "phone_call,sms,zalo,facebook,other",
	suggested_next_status: str = "in_progress",
	enabled=True,
	sort_order=100,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	actor = assert_settings_admin(actor)
	code_val = (parse_optional_str(code, "Mã kết quả không hợp lệ.") or "").lower()
	label_val = sanitize_note_text(parse_optional_str(label_vi, "Nhãn kết quả không hợp lệ."), max_length=140)
	desc_val = sanitize_note_text(parse_optional_str(description, "Mô tả không hợp lệ."), max_length=1000)
	group_val = parse_optional_str(result_group, "Nhóm kết quả không hợp lệ.") or ""
	next_status_val = (
		parse_optional_str(suggested_next_status, "Trạng thái gợi ý không hợp lệ.") or "in_progress"
	)
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")

	if not code_val or not label_val:
		raise InvalidRequest("Mã kết quả (code) và nhãn tiếng Việt (label_vi) là bắt buộc.")
	if group_val not in RESULT_GROUPS:
		raise InvalidRequest("Nhóm kết quả (result_group) không hợp lệ.")
	if next_status_val not in ("in_progress", "on_hold", "closed"):
		raise InvalidRequest("Trạng thái gợi ý tiếp theo chỉ chấp nhận in_progress | on_hold | closed.")

	is_att_b = parse_bool(is_contact_attempt, default=True, field="is_contact_attempt")
	is_conn_b = parse_bool(is_connected, default=False, field="is_connected")
	is_int_b = parse_bool(is_interested, default=False, field="is_interested")
	is_term_b = parse_bool(is_terminal, default=False, field="is_terminal")
	is_dnc_b = parse_bool(is_do_not_contact, default=False, field="is_do_not_contact")
	req_note_b = parse_bool(requires_note, default=False, field="requires_note")
	req_fu_b = parse_bool(requires_followup, default=False, field="requires_followup")
	allow_fu_b = parse_bool(allows_followup, default=True, field="allows_followup")
	req_comp_b = parse_bool(requires_completion_reason, default=False, field="requires_completion_reason")
	enabled_b = parse_bool(enabled, default=True, field="enabled")
	sort_i = parse_int(sort_order, minimum=0, maximum=10000, field="sort_order")

	if code_val in UNREACHED_DISCONNECTED_CODES and is_conn_b:
		raise OutcomeNotCompatible(
			f"Mã kết quả không kết nối hai chiều ({code_val}) không được đặt is_connected = true."
		)
	if req_fu_b and not allow_fu_b:
		raise OutcomeNotCompatible(
			"Mã kết quả yêu cầu hẹn lại bắt buộc cho phép hẹn lại (allows_followup = true)."
		)

	try:
		channels_list = parse_channels_csv(allowed_channels)
	except frappe.ValidationError as exc:
		raise InvalidChannel(str(exc)) from exc
	if not channels_list:
		raise InvalidChannel("Mã kết quả phải cho phép ít nhất một kênh liên hệ.")
	channels_csv = ",".join(channels_list)

	target_fields = {
		"label_vi": label_val,
		"description": desc_val,
		"result_group": group_val,
		"is_contact_attempt": 1 if is_att_b else 0,
		"is_connected": 1 if is_conn_b else 0,
		"is_interested": 1 if is_int_b else 0,
		"is_terminal": 1 if is_term_b else 0,
		"is_do_not_contact": 1 if is_dnc_b else 0,
		"requires_note": 1 if req_note_b else 0,
		"requires_followup": 1 if req_fu_b else 0,
		"allows_followup": 1 if allow_fu_b else 0,
		"requires_completion_reason": 1 if req_comp_b else 0,
		"allowed_channels": channels_csv,
		"suggested_next_status": next_status_val,
		"enabled": 1 if enabled_b else 0,
		"sort_order": sort_i,
	}

	prev_flag = getattr(frappe.flags, "cskh_in_settings_service", False)
	frappe.flags.cskh_in_settings_service = True
	try:
		if frappe.db.exists("CSKH Outcome Code", code_val):
			doc = frappe.get_doc("CSKH Outcome Code", code_val)
			changed = any(
				(cint(doc.get(k)) != cint(v) if isinstance(v, int) else (doc.get(k) or "") != (v or ""))
				for k, v in target_fields.items()
			)
			if changed:
				for k, v in target_fields.items():
					doc.set(k, v)
				doc.version = cint(doc.version or 1) + 1
				try:
					doc.save(ignore_permissions=True)
				except frappe.ValidationError as exc:
					raise InvalidRequest(str(exc)) from exc
			status_str = "updated" if changed else "unchanged"
		else:
			doc = frappe.get_doc(
				{"doctype": "CSKH Outcome Code", "code": code_val, "version": 1, **target_fields}
			)
			try:
				doc.insert(ignore_permissions=True)
			except frappe.ValidationError as exc:
				raise InvalidRequest(str(exc)) from exc
			status_str = "created"
	finally:
		frappe.flags.cskh_in_settings_service = prev_flag

	frappe.db.commit()
	return {"status": status_str, "outcome": serialize_outcome(doc), "idempotency_key": idem_val}


def set_lead_dnc(
	lead: str,
	do_not_contact,
	reason: str | None = None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Admin-only audited setter/clearer for `CRM Lead.cskh_do_not_contact` (`docs/api-contract-p2.md` §4.14)."""
	actor = assert_settings_admin(actor)
	lead_val = parse_optional_str(lead, "Mã Lead không hợp lệ.")
	if not lead_val:
		raise InvalidRequest("Mã Lead là bắt buộc.")
	dnc_b = parse_bool(do_not_contact, field="do_not_contact")
	clean_reason = sanitize_note_text(parse_optional_str(reason, "Lý do không hợp lệ."), max_length=1000)
	if not clean_reason:
		raise NoteRequired("Cập nhật trạng thái DNC bắt buộc ghi rõ lý do kiểm toán.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")

	if not frappe.db.exists("CRM Lead", lead_val):
		raise NotFound("Không tìm thấy khách hàng (CRM Lead).")

	req_hash = _payload_hash(
		{"op": "set_lead_dnc", "lead": lead_val, "do_not_contact": dnc_b, "reason": clean_reason}
	)
	marker_prefix = f"[CSKH_DNC_AUDIT:{idem_val}:"
	existing_comments = frappe.db.get_all(
		"Comment",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_name": lead_val,
			"comment_type": "Info",
			"content": ["like", f"{marker_prefix}%"],
		},
		fields=["content"],
		limit=1,
	)
	if existing_comments:
		content = existing_comments[0].content or ""
		if f"{marker_prefix}{req_hash}]" not in content:
			raise IdempotencyConflict("Khóa idempotency_key đã được dùng với nội dung DNC khác.")
		return {
			"status": "updated",
			"idempotent_replay": True,
			"lead": lead_val,
			"do_not_contact": bool(cint(frappe.db.get_value("CRM Lead", lead_val, "cskh_do_not_contact"))),
		}

	now_dt = now_datetime()
	set_lead_dnc_fields(
		lead_val,
		{
			"cskh_do_not_contact": 1 if dnc_b else 0,
			"cskh_dnc_reason": clean_reason,
			"cskh_dnc_at": now_dt if dnc_b else None,
			"cskh_dnc_work_item": None
			if not dnc_b
			else frappe.db.get_value("CRM Lead", lead_val, "cskh_dnc_work_item"),
		},
		update_modified=True,
	)
	if dnc_b:
		from crm_cskh.services import sla_engine

		sla_engine.on_lead_dnc_set_in_tx(
			lead=lead_val,
			dnc_at=now_dt,
			actor=actor,
			reason=clean_reason,
		)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "CRM Lead",
			"reference_name": lead_val,
			"comment_email": actor,
			"content": f"{marker_prefix}{req_hash}] {'Bật' if dnc_b else 'Gỡ'} cờ DNC: {clean_reason}",
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	return {
		"status": "updated",
		"idempotent_replay": False,
		"lead": lead_val,
		"do_not_contact": dnc_b,
		"dnc_at": now_dt if dnc_b else None,
	}
