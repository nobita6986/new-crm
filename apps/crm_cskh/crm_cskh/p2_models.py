"""P2 domain model: state constants, DocType controllers, seeds, Custom Fields & DB indexes.

Implements `docs/data-model-p2.md`, `docs/state-machine.md`, `docs/outcome-catalog.md`,
and `docs/attempt-immutability-correction.md` (`ADR-P2-01..11`).
"""

from __future__ import annotations

import contextvars
import json
import re
from urllib.parse import unquote

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, now_datetime

from crm_cskh.services.sanitizer import sanitize_note_text

LEAD_DNC_FIELDS = (
	"cskh_do_not_contact",
	"cskh_dnc_reason",
	"cskh_dnc_at",
	"cskh_dnc_work_item",
)
_LEAD_DNC_FIELD_SET = frozenset(LEAD_DNC_FIELDS)
_CSKH_AUTHORIZED_LEAD_DNC_MUTATION: contextvars.ContextVar[bool] = contextvars.ContextVar(
	"_CSKH_AUTHORIZED_LEAD_DNC_MUTATION",
	default=False,
)

OWNED_ACTIVE_WORK_ITEM_STATES = ("assigned", "in_progress", "on_hold")
ACTIVE_WORK_ITEM_STATES = ("queued", "assigned", "in_progress", "on_hold")
TERMINAL_WORK_ITEM_STATES = ("revoked", "closed")
ALL_WORK_ITEM_STATES = ("queued", "assigned", "in_progress", "on_hold", "closed", "revoked")

# Native `CRM Task.status` options in pinned `frappe/crm@v1.87.0`:
# `Backlog | Todo | In Progress | Done | Canceled`.
OPEN_TASK_STATES = ("Backlog", "Todo", "In Progress")
TERMINAL_TASK_STATES = ("Done", "Canceled")

VALID_CHANNELS = ("phone_call", "sms", "zalo", "facebook", "other")
RESULT_GROUPS = (
	"unreached",
	"engaged",
	"positive",
	"negative",
	"converted",
	"invalid_contact",
	"compliance",
	"other",
)
CORRECTION_ACTIONS = ("correct", "void", "restore")
CORRECTION_STATUSES = ("pending", "approved", "rejected")
VERIFICATION_STATUSES = ("self_reported", "verified", "disputed")
DNC_BEHAVIORS = ("block_and_auto_close", "block_future_outreach")

# Non-negotiable domain rule (`P2.md` §0.9, §6.2): non-connected outcomes MUST keep `is_connected = 0`.
UNREACHED_DISCONNECTED_CODES = frozenset(
	("no_answer", "busy", "unreachable", "message_sent_no_reply", "wrong_number")
)

_OUTCOME_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_POLICY_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")

P2_DOCTYPES = (
	"CSKH Outcome Code",
	"CSKH Policy",
	"CSKH Contact Attempt",
	"CSKH Attempt Correction",
)

# 12 canonical Vietnamese Outcome Codes (`docs/outcome-catalog.md` §2).
DEFAULT_OUTCOME_CODES = (
	{
		"code": "no_answer",
		"label_vi": "Không nghe máy",
		"description": "Cuộc gọi đổ chuông hết hồi nhưng khách hàng không bắt máy.",
		"result_group": "unreached",
		"is_contact_attempt": 1,
		"is_connected": 0,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 0,
		"requires_followup": 0,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call",
		"suggested_next_status": "in_progress",
		"version": 1,
		"enabled": 1,
		"sort_order": 10,
	},
	{
		"code": "busy",
		"label_vi": "Máy bận",
		"description": "Khách hàng đang bận đường dây hoặc từ chối cuộc gọi tức thời.",
		"result_group": "unreached",
		"is_contact_attempt": 1,
		"is_connected": 0,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 0,
		"requires_followup": 0,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call",
		"suggested_next_status": "in_progress",
		"version": 1,
		"enabled": 1,
		"sort_order": 20,
	},
	{
		"code": "unreachable",
		"label_vi": "Thuê bao không liên lạc được",
		"description": "Thuê bao tắt máy, ngoài vùng phủ sóng hoặc tạm khóa.",
		"result_group": "unreached",
		"is_contact_attempt": 1,
		"is_connected": 0,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 0,
		"requires_followup": 0,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "in_progress",
		"version": 1,
		"enabled": 1,
		"sort_order": 30,
	},
	{
		"code": "message_sent_no_reply",
		"label_vi": "Đã gửi tin nhắn, chưa phản hồi",
		"description": "Đã gửi tin nhắn qua SMS/Zalo/MXH nhưng khách chưa phản hồi hai chiều.",
		"result_group": "unreached",
		"is_contact_attempt": 1,
		"is_connected": 0,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 0,
		"requires_followup": 0,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "sms,zalo,facebook,other",
		"suggested_next_status": "in_progress",
		"version": 1,
		"enabled": 1,
		"sort_order": 40,
	},
	{
		"code": "wrong_number",
		"label_vi": "Sai số / Không đúng người",
		"description": "Số điện thoại không thuộc về khách hàng mục tiêu hoặc không tồn tại.",
		"result_group": "invalid_contact",
		"is_contact_attempt": 1,
		"is_connected": 0,
		"is_interested": 0,
		"is_terminal": 1,
		"is_do_not_contact": 0,
		"requires_note": 1,
		"requires_followup": 0,
		"allows_followup": 0,
		"requires_completion_reason": 1,
		"allowed_channels": "phone_call,sms,zalo,other",
		"suggested_next_status": "closed",
		"version": 1,
		"enabled": 1,
		"sort_order": 50,
	},
	{
		"code": "connected_needs_followup",
		"label_vi": "Đã trao đổi — Cần chăm sóc tiếp",
		"description": "Đã trao đổi hai chiều với khách, cần theo dõi hoặc gửi thêm thông tin.",
		"result_group": "engaged",
		"is_contact_attempt": 1,
		"is_connected": 1,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 1,
		"requires_followup": 1,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "on_hold",
		"version": 1,
		"enabled": 1,
		"sort_order": 60,
	},
	{
		"code": "connected_interested",
		"label_vi": "Đã trao đổi — Quan tâm",
		"description": "Khách hàng phản hồi tích cực và thể hiện sự quan tâm rõ ràng.",
		"result_group": "positive",
		"is_contact_attempt": 1,
		"is_connected": 1,
		"is_interested": 1,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 1,
		"requires_followup": 0,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "in_progress",
		"version": 1,
		"enabled": 1,
		"sort_order": 70,
	},
	{
		"code": "callback_requested",
		"label_vi": "Khách hẹn gọi / liên hệ lại",
		"description": "Khách xác nhận bận tại thời điểm liên hệ và chủ động hẹn khung giờ gọi lại.",
		"result_group": "engaged",
		"is_contact_attempt": 1,
		"is_connected": 1,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 0,
		"requires_followup": 1,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "on_hold",
		"version": 1,
		"enabled": 1,
		"sort_order": 80,
	},
	{
		"code": "connected_not_interested",
		"label_vi": "Đã trao đổi — Không nhu cầu",
		"description": "Khách hàng đã nghe tư vấn và xác nhận hiện chưa có nhu cầu.",
		"result_group": "negative",
		"is_contact_attempt": 1,
		"is_connected": 1,
		"is_interested": 0,
		"is_terminal": 1,
		"is_do_not_contact": 0,
		"requires_note": 0,
		"requires_followup": 0,
		"allows_followup": 0,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "closed",
		"version": 1,
		"enabled": 1,
		"sort_order": 90,
	},
	{
		"code": "converted",
		"label_vi": "Hoàn tất mục tiêu chiến dịch",
		"description": "Đã đạt mục tiêu chăm sóc / chốt thành công theo tiêu chí chiến dịch.",
		"result_group": "converted",
		"is_contact_attempt": 1,
		"is_connected": 1,
		"is_interested": 1,
		"is_terminal": 1,
		"is_do_not_contact": 0,
		"requires_note": 1,
		"requires_followup": 0,
		"allows_followup": 0,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "closed",
		"version": 1,
		"enabled": 1,
		"sort_order": 100,
	},
	{
		"code": "do_not_contact",
		"label_vi": "Yêu cầu ngừng liên hệ (DNC)",
		"description": "Khách hàng yêu cầu ngừng liên hệ; gắn cờ tuân thủ DNC toàn cục trên Lead.",
		"result_group": "compliance",
		"is_contact_attempt": 1,
		"is_connected": 1,
		"is_interested": 0,
		"is_terminal": 1,
		"is_do_not_contact": 1,
		"requires_note": 1,
		"requires_followup": 0,
		"allows_followup": 0,
		"requires_completion_reason": 1,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "closed",
		"version": 1,
		"enabled": 1,
		"sort_order": 110,
	},
	{
		"code": "other",
		"label_vi": "Khác (ghi rõ nội dung)",
		"description": "Trường hợp ngoại lệ khác, bắt buộc ghi rõ chi tiết trong ghi chú.",
		"result_group": "other",
		"is_contact_attempt": 1,
		"is_connected": 0,
		"is_interested": 0,
		"is_terminal": 0,
		"is_do_not_contact": 0,
		"requires_note": 1,
		"requires_followup": 0,
		"allows_followup": 1,
		"requires_completion_reason": 0,
		"allowed_channels": "phone_call,sms,zalo,facebook,other",
		"suggested_next_status": "in_progress",
		"version": 1,
		"enabled": 1,
		"sort_order": 120,
	},
)

DEFAULT_GLOBAL_POLICY = {
	"policy_code": "GLOBAL-DEFAULT",
	"title": "Chính sách Vận hành CSKH Mặc định",
	"scope_type": "Global",
	"team": None,
	"campaign": None,
	"enabled": 1,
	"version": 1,
	"allowed_backdate_minutes": 120,
	"max_open_followup_tasks_per_item": 1,
	"require_attempt_before_close": 1,
	"allow_close_without_contact_for_invalid": 1,
	"do_not_contact_behavior": "block_and_auto_close",
	"enabled_channels": "phone_call,sms,zalo,facebook,other",
	"allow_leader_reopen": 1,
	"allow_leader_review_correction": 1,
}

P2_CUSTOM_FIELDS = (
	# --- CRM Lead DNC compliance fields (`docs/data-model-p2.md` §3.3, `F-P2-06`) ---
	{
		"dt": "CRM Lead",
		"fieldname": "cskh_do_not_contact",
		"fieldtype": "Check",
		"label": "CSKH Do Not Contact",
		"insert_after": "cskh_is_placeholder_name",
		"default": "0",
		"read_only": 1,
		"print_hide": 1,
		"no_copy": 1,
		"description": "Cờ tuân thủ ngừng liên hệ toàn cục (DNC); không bị xóa khi import lại hoặc tạo chiến dịch mới.",
	},
	{
		"dt": "CRM Lead",
		"fieldname": "cskh_dnc_reason",
		"fieldtype": "Small Text",
		"label": "CSKH DNC Reason",
		"insert_after": "cskh_do_not_contact",
		"read_only": 1,
		"print_hide": 1,
		"no_copy": 1,
		"description": "Lý do khách hàng yêu cầu ngừng liên hệ hoặc Admin cập nhật.",
	},
	{
		"dt": "CRM Lead",
		"fieldname": "cskh_dnc_at",
		"fieldtype": "Datetime",
		"label": "CSKH DNC At",
		"insert_after": "cskh_dnc_reason",
		"read_only": 1,
		"print_hide": 1,
		"no_copy": 1,
		"description": "Thời điểm đánh dấu DNC gần nhất.",
	},
	{
		"dt": "CRM Lead",
		"fieldname": "cskh_dnc_work_item",
		"fieldtype": "Link",
		"options": "CSKH Work Item",
		"label": "CSKH DNC Work Item",
		"insert_after": "cskh_dnc_at",
		"read_only": 1,
		"print_hide": 1,
		"no_copy": 1,
		"description": "Work Item phát sinh yêu cầu DNC.",
	},
	# --- CRM Task callback extension fields (`docs/data-model-p2.md` §3.4) ---
	{
		"dt": "CRM Task",
		"fieldname": "cskh_work_item",
		"fieldtype": "Link",
		"options": "CSKH Work Item",
		"label": "CSKH Work Item",
		"insert_after": "reference_docname",
		"print_hide": 1,
		"no_copy": 1,
		"description": "Work Item gắn với lịch hẹn chăm sóc lại.",
	},
	{
		"dt": "CRM Task",
		"fieldname": "cskh_is_followup",
		"fieldtype": "Check",
		"label": "CSKH Is Followup",
		"insert_after": "cskh_work_item",
		"default": "0",
		"print_hide": 1,
		"no_copy": 1,
		"description": "Đánh dấu CRM Task thuộc quy trình hẹn chăm sóc lại CSKH P2.",
	},
	{
		"dt": "CRM Task",
		"fieldname": "cskh_source_attempt",
		"fieldtype": "Link",
		"options": "CSKH Contact Attempt",
		"label": "CSKH Source Attempt",
		"insert_after": "cskh_is_followup",
		"print_hide": 1,
		"no_copy": 1,
		"description": "Bản ghi CSKH Contact Attempt đã tạo ra lịch hẹn này.",
	},
	{
		"dt": "CRM Task",
		"fieldname": "cskh_completed_attempt",
		"fieldtype": "Link",
		"options": "CSKH Contact Attempt",
		"label": "CSKH Completed Attempt",
		"insert_after": "cskh_source_attempt",
		"print_hide": 1,
		"no_copy": 1,
		"description": "Bản ghi CSKH Contact Attempt đã giải quyết lịch hẹn này.",
	},
	{
		"dt": "CRM Task",
		"fieldname": "cskh_completion_reason",
		"fieldtype": "Small Text",
		"label": "CSKH Completion Reason",
		"insert_after": "cskh_completed_attempt",
		"print_hide": 1,
		"no_copy": 1,
		"description": "Lý do hoàn tất hoặc hủy lịch hẹn chăm sóc lại.",
	},
	{
		"dt": "CRM Task",
		"fieldname": "cskh_reschedule_count",
		"fieldtype": "Int",
		"label": "CSKH Reschedule Count",
		"insert_after": "cskh_completion_reason",
		"default": "0",
		"print_hide": 1,
		"no_copy": 1,
		"description": "Số lần dời lịch hẹn trên cùng CRM Task.",
	},
	{
		"dt": "CRM Task",
		"fieldname": "cskh_task_history_json",
		"fieldtype": "Long Text",
		"label": "CSKH Task History Json",
		"insert_after": "cskh_reschedule_count",
		"permlevel": 1,
		"print_hide": 1,
		"no_copy": 1,
		"description": "Nhật ký kiểm toán dời lịch / chuyển giao / hủy Task.",
	},
)


def parse_channels_csv(raw_channels: str | list | tuple | None) -> list[str]:
	if isinstance(raw_channels, list | tuple):
		parts = [str(item).strip().lower() for item in raw_channels if str(item).strip()]
	elif isinstance(raw_channels, str):
		parts = [part.strip().lower() for part in raw_channels.split(",") if part.strip()]
	else:
		parts = []
	seen = set()
	ordered = []
	for ch in parts:
		if ch not in VALID_CHANNELS:
			frappe.throw(_("Kênh liên hệ không hợp lệ: {0}.").format(ch), frappe.ValidationError)
		if ch not in seen:
			seen.add(ch)
			ordered.append(ch)
	return ordered


def _index_exists(table_name: str, index_name: str) -> bool:
	clean_table = table_name.replace("`", "")
	rows = frappe.db.sql(
		f"show index from `{clean_table}` where Key_name = %s",
		(index_name,),
	)
	return bool(rows)


def _ensure_unique_index(doctype: str, fields: tuple[str, ...], index_name: str) -> None:
	table = f"tab{doctype}"
	if not frappe.db.table_exists(doctype):
		return
	if _index_exists(table, index_name):
		return
	cols = ", ".join(f"`{col}`" for col in fields)
	frappe.db.sql_ddl(f"alter table `{table}` add unique index `{index_name}` ({cols})")


def ensure_p2_indexes(doctype: str | None = None) -> None:
	"""Create/verify idempotent P2 unique constraints and lookup indexes (`docs/data-model-p2.md` §4)."""
	targets = (
		(doctype,)
		if doctype
		else (
			"CSKH Outcome Code",
			"CSKH Policy",
			"CSKH Contact Attempt",
			"CSKH Attempt Correction",
			"CSKH Work Item",
			"CRM Task",
		)
	)
	for dt in targets:
		if not frappe.db.table_exists(dt):
			continue
		if dt == "CSKH Outcome Code":
			frappe.db.add_index(dt, ["enabled", "sort_order"], "cskh_outcome_enabled_sort_idx")
		elif dt == "CSKH Policy":
			frappe.db.add_index(dt, ["scope_type", "enabled", "campaign", "team"], "cskh_policy_scope_idx")
		elif dt == "CSKH Contact Attempt":
			_ensure_unique_index(dt, ("work_item", "performed_by", "idempotency_key"), "cskh_attempt_idem_uq")
			_ensure_unique_index(dt, ("call_log",), "cskh_attempt_call_log_uq")
			frappe.db.add_index(dt, ["work_item", "occurred_at"], "cskh_attempt_wi_occ_idx")
			frappe.db.add_index(dt, ["lead", "occurred_at"], "cskh_attempt_lead_occ_idx")
			frappe.db.add_index(dt, ["campaign", "occurred_at"], "cskh_attempt_camp_occ_idx")
			frappe.db.add_index(dt, ["performed_by", "occurred_at"], "cskh_attempt_perf_occ_idx")
			frappe.db.add_index(dt, ["outcome_code", "occurred_at"], "cskh_attempt_out_occ_idx")
		elif dt == "CSKH Attempt Correction":
			_ensure_unique_index(dt, ("requested_by", "idempotency_key"), "cskh_corr_req_idem_uq")
			frappe.db.add_index(dt, ["attempt", "status", "creation"], "cskh_corr_attempt_status_idx")
			frappe.db.add_index(dt, ["work_item", "status", "creation"], "cskh_corr_wi_status_idx")
		elif dt == "CSKH Work Item":
			frappe.db.add_index(dt, ["assigned_to", "status", "next_action_due"], "cskh_wi_queue_due_idx")
			frappe.db.add_index(dt, ["campaign", "status", "last_attempt_at"], "cskh_wi_camp_last_idx")
			if frappe.db.has_column("CSKH Work Item", "owner_at_close"):
				frappe.db.add_index(dt, ["status", "owner_at_close", "closed_at"], "cskh_wi_owner_close_idx")
		elif dt == "CRM Task":
			# Only create indexes once the Custom Fields exist as columns on `tabCRM Task`.
			if frappe.db.has_column("CRM Task", "cskh_work_item") and frappe.db.has_column(
				"CRM Task", "cskh_is_followup"
			):
				frappe.db.add_index(
					dt,
					["cskh_work_item", "cskh_is_followup", "status"],
					"cskh_task_wi_open_idx",
				)
				frappe.db.add_index(
					dt,
					["assigned_to", "cskh_is_followup", "status", "due_date"],
					"cskh_task_assignee_due_idx",
				)
				frappe.db.add_index(
					dt,
					["cskh_is_followup", "status", "due_date"],
					"cskh_task_followup_due_idx",
				)


def ensure_p2_custom_fields() -> None:
	"""Idempotently create or sync all P2 Custom Fields on `CRM Lead` and `CRM Task`."""
	cleared_dts: set[str] = set()
	for spec in P2_CUSTOM_FIELDS:
		if not frappe.db.exists("DocType", spec["dt"]):
			continue
		cf_name = f"{spec['dt']}-{spec['fieldname']}"
		existing = frappe.db.exists("Custom Field", cf_name) or frappe.db.exists(
			"Custom Field", {"dt": spec["dt"], "fieldname": spec["fieldname"]}
		)
		if existing:
			desired_ro = cint(spec.get("read_only", 0))
			current_ro = cint(frappe.db.get_value("Custom Field", existing, "read_only") or 0)
			if current_ro != desired_ro:
				frappe.db.set_value("Custom Field", existing, "read_only", desired_ro, update_modified=False)
				cleared_dts.add(spec["dt"])
			continue
		doc = frappe.get_doc({"doctype": "Custom Field", **spec})
		doc.insert(ignore_permissions=True)
		cleared_dts.add(spec["dt"])
	for dt in cleared_dts:
		frappe.clear_cache(doctype=dt)


def ensure_p2_seeds() -> None:
	"""Idempotently seed the 12 Vietnamese Outcome Codes and `GLOBAL-DEFAULT` Policy."""
	prev_flag = getattr(frappe.flags, "cskh_in_settings_service", False)
	frappe.flags.cskh_in_settings_service = True
	try:
		if frappe.db.table_exists("CSKH Outcome Code"):
			for row in DEFAULT_OUTCOME_CODES:
				if not frappe.db.exists("CSKH Outcome Code", row["code"]):
					frappe.get_doc({"doctype": "CSKH Outcome Code", **row}).insert(ignore_permissions=True)
		if frappe.db.table_exists("CSKH Policy"):
			if not frappe.db.exists("CSKH Policy", DEFAULT_GLOBAL_POLICY["policy_code"]):
				frappe.get_doc({"doctype": "CSKH Policy", **DEFAULT_GLOBAL_POLICY}).insert(
					ignore_permissions=True
				)
	finally:
		frappe.flags.cskh_in_settings_service = prev_flag


class OutcomeCode(Document):
	def validate(self):
		self.code = (self.code or "").strip().lower()
		if not _OUTCOME_CODE_RE.match(self.code):
			frappe.throw(_("Mã kết quả liên hệ không đúng định dạng snake_case."), frappe.ValidationError)
		self.label_vi = sanitize_note_text(self.label_vi, max_length=140)
		if not self.label_vi:
			frappe.throw(_("Nhãn tiếng Việt của mã kết quả là bắt buộc."), frappe.ValidationError)
		if self.description:
			self.description = sanitize_note_text(self.description, max_length=1000)
		if self.result_group not in RESULT_GROUPS:
			frappe.throw(_("Nhóm kết quả liên hệ không hợp lệ."), frappe.ValidationError)
		if self.code in UNREACHED_DISCONNECTED_CODES and cint(self.is_connected):
			frappe.throw(
				_("Mã không kết nối hai chiều ({0}) không được đặt is_connected = 1.").format(self.code),
				frappe.ValidationError,
			)
		if cint(self.requires_followup) and not cint(self.allows_followup):
			frappe.throw(
				_("Mã kết quả bắt buộc hẹn lại phải bật cho phép hẹn lại (allows_followup = 1)."),
				frappe.ValidationError,
			)
		channels = parse_channels_csv(self.allowed_channels)
		if not channels:
			frappe.throw(_("Phải cấu hình ít nhất một kênh liên hệ hợp lệ."), frappe.ValidationError)
		self.allowed_channels = ",".join(channels)
		if self.suggested_next_status not in ("in_progress", "on_hold", "closed"):
			frappe.throw(_("Trạng thái gợi ý tiếp theo không hợp lệ."), frappe.ValidationError)
		if cint(self.version or 0) < 1:
			self.version = 1
		if not self.is_new():
			prev = self.get_doc_before_save()
			if prev and prev.code != self.code:
				frappe.throw(_("Không được đổi mã máy của Outcome Code."), frappe.ValidationError)

	def on_trash(self):
		if frappe.db.exists("CSKH Contact Attempt", {"outcome_code": self.name}):
			frappe.throw(
				_("Không được xóa mã kết quả đã phát sinh nhật ký liên hệ; hãy tắt enabled = 0."),
				frappe.ValidationError,
			)


class Policy(Document):
	def validate(self):
		self.policy_code = (self.policy_code or "").strip()
		if not _POLICY_CODE_RE.match(self.policy_code):
			frappe.throw(_("Mã chính sách CSKH không hợp lệ."), frappe.ValidationError)
		self.title = sanitize_note_text(self.title, max_length=140)
		if not self.title:
			frappe.throw(_("Tên chính sách CSKH là bắt buộc."), frappe.ValidationError)
		if self.scope_type not in ("Global", "Team", "Campaign"):
			frappe.throw(_("Phạm vi chính sách (scope_type) không hợp lệ."), frappe.ValidationError)
		if self.scope_type == "Global":
			self.team = None
			self.campaign = None
		elif self.scope_type == "Team":
			if not self.team:
				frappe.throw(
					_("Chính sách cấp Team bắt buộc chọn nhánh cây bán hàng."), frappe.ValidationError
				)
			self.campaign = None
		elif self.scope_type == "Campaign":
			if not self.campaign:
				frappe.throw(_("Chính sách cấp Campaign bắt buộc chọn chiến dịch."), frappe.ValidationError)
			self.team = None

		if cint(self.allowed_backdate_minutes) < 0:
			frappe.throw(
				_("Số phút lùi thời gian (allowed_backdate_minutes) không được âm."), frappe.ValidationError
			)
		if cint(self.max_open_followup_tasks_per_item or 1) != 1:
			frappe.throw(
				_("Trong P2, max_open_followup_tasks_per_item bắt buộc bằng 1."),
				frappe.ValidationError,
			)
		self.max_open_followup_tasks_per_item = 1
		if self.do_not_contact_behavior not in DNC_BEHAVIORS:
			frappe.throw(_("Cấu hình hành vi DNC không hợp lệ."), frappe.ValidationError)
		channels = parse_channels_csv(self.enabled_channels)
		if not channels:
			frappe.throw(_("Chính sách phải bật ít nhất một kênh liên hệ."), frappe.ValidationError)
		self.enabled_channels = ",".join(channels)
		if cint(self.version or 0) < 1:
			self.version = 1

		if cint(self.enabled):
			dup_filters = {
				"scope_type": self.scope_type,
				"enabled": 1,
				"name": ["!=", self.name],
			}
			if self.scope_type == "Team":
				dup_filters["team"] = self.team
			elif self.scope_type == "Campaign":
				dup_filters["campaign"] = self.campaign
			if frappe.db.exists("CSKH Policy", dup_filters):
				frappe.throw(
					_("Đã có một chính sách đang bật cho cùng phạm vi cấu hình."),
					frappe.ValidationError,
				)

	def on_trash(self):
		if self.name == "GLOBAL-DEFAULT" or frappe.db.exists(
			"CSKH Contact Attempt", {"policy_name_snapshot": self.name}
		):
			frappe.throw(
				_("Không được xóa chính sách mặc định hoặc chính sách đã phát sinh nhật ký liên hệ."),
				frappe.ValidationError,
			)


class ContactAttempt(Document):
	"""Strictly append-only contact attempt event (`P2.md` §6.1, `ADR-P2-02`)."""

	def before_insert(self):
		if not getattr(frappe.flags, "cskh_in_attempt_service", False):
			frappe.throw(
				_("Nhật ký liên hệ chỉ được tạo qua dịch vụ điều phối CSKH."),
				frappe.PermissionError,
			)
		if not self.recorded_at:
			self.recorded_at = now_datetime()
		# Ensure nullable Link columns store SQL `NULL` rather than `""` so MariaDB
		# `UNIQUE(call_log)` permits multiple unlinked attempts cleanly.
		if not self.call_log:
			self.call_log = None
		if not self.callback_task:
			self.callback_task = None
		if not self.resolved_callback_task:
			self.resolved_callback_task = None

	def before_save(self):
		if not self.is_new():
			frappe.throw(
				_("Nhật ký liên hệ là bất biến (append-only); vui lòng dùng yêu cầu hiệu chỉnh."),
				frappe.ValidationError,
			)
		if not self.work_item or not self.lead or not self.campaign or not self.ownership_event:
			frappe.throw(
				_("Thiếu thông tin tham chiếu bắt buộc của nhật ký liên hệ."), frappe.ValidationError
			)
		if not self.performed_by or not self.owner_at_attempt:
			frappe.throw(_("Thiếu thông tin người thực hiện hoặc người sở hữu."), frappe.ValidationError)
		if self.channel not in VALID_CHANNELS:
			frappe.throw(_("Kênh liên hệ không hợp lệ."), frappe.ValidationError)
		if self.verification_status not in VERIFICATION_STATUSES:
			frappe.throw(_("Trạng thái xác minh không hợp lệ."), frappe.ValidationError)
		if not self.idempotency_key or not self.payload_hash:
			frappe.throw(_("Khóa idempotency và payload_hash là bắt buộc."), frappe.ValidationError)
		if self.note:
			self.note = sanitize_note_text(self.note, max_length=4000)
		if not self.call_log:
			self.call_log = None
		if not self.callback_task:
			self.callback_task = None
		if not self.resolved_callback_task:
			self.resolved_callback_task = None

	def on_trash(self):
		frappe.throw(
			_("Nhật ký liên hệ là bất biến (append-only); không được phép xóa."),
			frappe.ValidationError,
		)


class AttemptCorrection(Document):
	"""Audited correction / void / restore overlay (`P2.md` §6.4, `ADR-P2-03`)."""

	def before_insert(self):
		if not getattr(frappe.flags, "cskh_in_correction_service", False):
			frappe.throw(
				_("Yêu cầu hiệu chỉnh chỉ được tạo qua dịch vụ hiệu chỉnh CSKH."),
				frappe.PermissionError,
			)
		if not self.requested_at:
			self.requested_at = now_datetime()

	def before_save(self):
		if not getattr(frappe.flags, "cskh_in_correction_service", False):
			frappe.throw(
				_("Yêu cầu hiệu chỉnh chỉ được cập nhật qua dịch vụ hiệu chỉnh CSKH."),
				frappe.PermissionError,
			)
		if self.action not in CORRECTION_ACTIONS:
			frappe.throw(_("Hành động hiệu chỉnh không hợp lệ."), frappe.ValidationError)
		if self.status not in CORRECTION_STATUSES:
			frappe.throw(_("Trạng thái hiệu chỉnh không hợp lệ."), frappe.ValidationError)
		self.reason = sanitize_note_text(self.reason, max_length=1000)
		if not self.reason:
			frappe.throw(_("Lý do hiệu chỉnh là bắt buộc."), frappe.ValidationError)
		if self.review_note:
			self.review_note = sanitize_note_text(self.review_note, max_length=1000)
		if self.corrected_fields_json:
			try:
				parsed = json.loads(self.corrected_fields_json)
			except (TypeError, ValueError):
				frappe.throw(_("Dữ liệu trường hiệu chỉnh phải là JSON hợp lệ."), frappe.ValidationError)
			if not isinstance(parsed, dict):
				frappe.throw(_("Dữ liệu trường hiệu chỉnh phải là JSON object."), frappe.ValidationError)

		if not self.is_new():
			prev = self.get_doc_before_save()
			if not prev:
				frappe.throw(
					_("Không đọc được trạng thái trước khi lưu của yêu cầu hiệu chỉnh."),
					frappe.ValidationError,
				)
			if prev.status in ("approved", "rejected"):
				frappe.throw(
					_("Yêu cầu hiệu chỉnh đã phê duyệt/từ chối là bất biến, không được sửa lại."),
					frappe.ValidationError,
				)
			if prev.status == "pending" and self.status not in ("approved", "rejected"):
				frappe.throw(_("Chuyển trạng thái hiệu chỉnh không hợp lệ."), frappe.ValidationError)

	def on_trash(self):
		frappe.throw(
			_("Lịch sử hiệu chỉnh là hồ sơ kiểm toán bất biến; không được phép xóa."),
			frappe.ValidationError,
		)


def validate_cskh_followup_task(doc, method=None):
	"""`doc_events["CRM Task"]["validate"]` guard blocking direct CRUD bypass (`SEC2-05`)."""
	is_cskh = bool(cint(doc.get("cskh_is_followup")) or doc.get("cskh_work_item"))
	if not is_cskh and not doc.is_new():
		prev = doc.get_doc_before_save()
		if prev and (cint(prev.get("cskh_is_followup")) or prev.get("cskh_work_item")):
			is_cskh = True
	if not is_cskh:
		return
	if not getattr(frappe.flags, "cskh_in_followup_service", False):
		frappe.throw(
			_("Tác vụ hẹn lại CSKH chỉ được tạo hoặc cập nhật qua quy trình nghiệp vụ CSKH."),
			frappe.PermissionError,
		)
	if doc.get("status") == "Done" and not doc.get("cskh_completed_attempt"):
		frappe.throw(
			_("Không được đánh dấu hoàn tất lịch hẹn CSKH khi chưa gắn nhật ký liên hệ hợp lệ."),
			frappe.ValidationError,
		)
	if doc.get("status") == "Canceled" and not (doc.get("cskh_completion_reason") or "").strip():
		frappe.throw(
			_("Hủy lịch hẹn CSKH bắt buộc có lý do kiểm toán."),
			frappe.ValidationError,
		)
	if doc.get("due_date") and doc.is_new():
		get_datetime(doc.get("due_date"))


def prevent_cskh_followup_task_trash(doc, method=None):
	"""`doc_events["CRM Task"]["on_trash"]` guard blocking deletion of CSKH followup tasks (`SEC2-05`)."""
	if cint(doc.get("cskh_is_followup")) or doc.get("cskh_work_item"):
		frappe.throw(
			_("Không được xóa tác vụ hẹn lại CSKH; hãy hủy có lý do qua quy trình nghiệp vụ."),
			frappe.PermissionError,
		)


def _normalize_lead_dnc_tuple(mapping) -> tuple:
	if not mapping:
		return (0, "", None, "")
	getter = mapping.get if hasattr(mapping, "get") else lambda k: getattr(mapping, k, None)
	dnc_flag = 1 if cint(getter("cskh_do_not_contact") or 0) else 0
	dnc_reason = str(getter("cskh_dnc_reason") or "").strip()
	raw_at = getter("cskh_dnc_at")
	dnc_at = get_datetime(raw_at).replace(microsecond=0) if raw_at else None
	dnc_wi = str(getter("cskh_dnc_work_item") or "").strip()
	return (dnc_flag, dnc_reason, dnc_at, dnc_wi)


def set_lead_dnc_fields(lead_name: str, values: dict, *, update_modified: bool = True) -> None:
	"""Authorized internal gateway for updating `CRM Lead` DNC compliance fields (`F-P2-06`).

	Uses a module-private `ContextVar` (never settable from HTTP payloads or `frappe.flags`)
	and may ONLY be called by audited CSKH domain services (`record_attempt`, `reopen_work_item`
	with Admin `override_dnc`, and `policies.set_lead_dnc`).
	"""
	if not lead_name:
		raise ValueError("lead_name is required")
	unknown = set(values) - _LEAD_DNC_FIELD_SET
	if unknown:
		raise ValueError(f"Unsupported DNC fields: {sorted(unknown)}")
	clean_values = {field: val for field, val in values.items() if frappe.db.has_column("CRM Lead", field)}
	if not clean_values:
		return
	token = _CSKH_AUTHORIZED_LEAD_DNC_MUTATION.set(True)
	try:
		frappe.db.set_value("CRM Lead", lead_name, clean_values, update_modified=update_modified)
	finally:
		_CSKH_AUTHORIZED_LEAD_DNC_MUTATION.reset(token)


def validate_crm_lead_dnc_fields(doc, method=None) -> None:
	"""`doc_events["CRM Lead"]` guard blocking direct DNC mutations across ORM/REST/Desk (`F-P2-06`)."""
	if _CSKH_AUTHORIZED_LEAD_DNC_MUTATION.get():
		return
	cur_tuple = _normalize_lead_dnc_tuple(doc)
	empty_tuple = (0, "", None, "")

	if method == "before_insert" or doc.is_new() or not getattr(doc, "name", None):
		if cur_tuple != empty_tuple:
			frappe.throw(
				_(
					"Trạng thái ngừng liên hệ (DNC) chỉ được thiết lập hoặc thay đổi qua dịch vụ nghiệp vụ CSKH được ủy quyền."
				),
				frappe.PermissionError,
			)
		return

	if method == "on_update":
		prev_doc = doc.get_doc_before_save()
		baseline = _normalize_lead_dnc_tuple(prev_doc) if prev_doc is not None else empty_tuple
		if cur_tuple != baseline:
			frappe.throw(
				_(
					"Trạng thái ngừng liên hệ (DNC) chỉ được thiết lập hoặc thay đổi qua dịch vụ nghiệp vụ CSKH được ủy quyền."
				),
				frappe.PermissionError,
			)
		return

	existing_cols = [col for col in LEAD_DNC_FIELDS if frappe.db.has_column("CRM Lead", col)]
	if not existing_cols or not frappe.db.exists("CRM Lead", doc.name):
		if cur_tuple != empty_tuple:
			frappe.throw(
				_(
					"Trạng thái ngừng liên hệ (DNC) chỉ được thiết lập hoặc thay đổi qua dịch vụ nghiệp vụ CSKH được ủy quyền."
				),
				frappe.PermissionError,
			)
		return

	db_row = frappe.db.get_value("CRM Lead", doc.name, existing_cols, as_dict=True) or {}
	if cur_tuple != _normalize_lead_dnc_tuple(db_row):
		frappe.throw(
			_(
				"Trạng thái ngừng liên hệ (DNC) chỉ được thiết lập hoặc thay đổi qua dịch vụ nghiệp vụ CSKH được ủy quyền."
			),
			frappe.PermissionError,
		)


def _payload_touches_lead_dnc(raw) -> bool:
	if raw is None:
		return False
	if isinstance(raw, str):
		stripped = raw.strip()
		if stripped in _LEAD_DNC_FIELD_SET:
			return True
		if stripped.startswith(("{", "[")):
			try:
				parsed = json.loads(stripped)
			except (TypeError, ValueError):
				return False
			return _payload_touches_lead_dnc(parsed)
		return False
	if isinstance(raw, dict):
		for k in raw:
			if str(k).strip() in _LEAD_DNC_FIELD_SET:
				return True
		return False
	if isinstance(raw, list | tuple | set):
		return any(_payload_touches_lead_dnc(item) for item in raw)
	return False


def assert_no_lead_dnc_mutation_fields(doctype: str | None, payload_or_fields) -> None:
	"""Fail closed with `PermissionError` if `doctype == 'CRM Lead'` and `payload_or_fields` touches DNC."""
	if (doctype or "").strip() != "CRM Lead":
		return
	if _CSKH_AUTHORIZED_LEAD_DNC_MUTATION.get():
		return
	if _payload_touches_lead_dnc(payload_or_fields):
		frappe.throw(
			_(
				"Trạng thái ngừng liên hệ (DNC) chỉ được thiết lập hoặc thay đổi qua dịch vụ nghiệp vụ CSKH được ủy quyền."
			),
			frappe.PermissionError,
		)


def enforce_lead_dnc_request_guard() -> None:
	"""`auth_hooks` callback rejecting generic HTTP writes to `CRM Lead` DNC fields (`F-P2-06`)."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return
	http_method = (getattr(request, "method", "") or "").upper()
	if http_method not in ("POST", "PUT", "PATCH"):
		return

	path = getattr(request, "path", "") or ""
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	try:
		json_body = request.get_json(silent=True)
	except Exception:
		json_body = None

	merged: dict = {}
	if isinstance(json_body, dict):
		merged.update(json_body)
	if isinstance(form_dict, dict):
		merged.update(form_dict)

	# 1. REST v1 (`/api/resource/CRM Lead...`) & REST v2 (`/api/v2/document/CRM Lead...`)
	for prefix in ("/api/resource/", "/api/v2/document/"):
		if path.startswith(prefix):
			rest = path[len(prefix) :].strip("/")
			parts = rest.split("/", 1) if rest else [""]
			target_dt = unquote(parts[0])
			if target_dt == "CRM Lead":
				payload = merged.get("data") if "data" in merged else merged
				if isinstance(payload, str):
					try:
						payload = json.loads(payload)
					except (TypeError, ValueError):
						payload = merged
				if isinstance(payload, dict) and _payload_touches_lead_dnc(payload):
					is_full_roundtrip_doc = (
						http_method in ("PUT", "PATCH")
						and len(parts) > 1
						and "doctype" in payload
						and "creation" in payload
					)
					if not is_full_roundtrip_doc:
						assert_no_lead_dnc_mutation_fields("CRM Lead", payload)
			return

	# 2. Generic partial-write / batch RPCs (`set_value`, `bulk_update`, `insert_many`)
	if path.startswith("/api/method/"):
		cmd = unquote(path[len("/api/method/") :].strip("/"))
	else:
		cmd = str(merged.get("cmd") or "")

	if cmd.endswith("set_value"):
		dt = merged.get("doctype")
		dt_str = dt if isinstance(dt, str) else None
		assert_no_lead_dnc_mutation_fields(dt_str, merged.get("fieldname"))
		assert_no_lead_dnc_mutation_fields(dt_str, merged.get("data"))
		assert_no_lead_dnc_mutation_fields(dt_str, merged)
	elif cmd.endswith(("bulk_update", "insert_many")):
		docs_raw = merged.get("docs")
		parsed_docs = docs_raw
		if isinstance(parsed_docs, str):
			try:
				parsed_docs = json.loads(parsed_docs)
			except (TypeError, ValueError):
				parsed_docs = None
		if isinstance(parsed_docs, list):
			for entry in parsed_docs:
				if isinstance(entry, dict) and entry.get("doctype") == "CRM Lead":
					assert_no_lead_dnc_mutation_fields("CRM Lead", entry)
