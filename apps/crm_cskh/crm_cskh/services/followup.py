"""Follow-up / callback lifecycle reusing native `CRM Task` (`P2.md` §7, `ADR-P2-04`).

Enforces:
  * At most 01 open callback `CRM Task` (`cskh_is_followup=1`, `status in ('Backlog', 'Todo', 'In Progress')`) per `CSKH Work Item`.
  * Rescheduling updates the existing open `CRM Task` (`cskh_reschedule_count += 1`) with mandatory audit reason instead of spawning a second open task.
  * Marking a callback `Done` requires a valid `CSKH Contact Attempt` (`cskh_completed_attempt`) and NEVER auto-creates a contact attempt or increments `connected_attempt_count`.
  * Ownership `reassign` (`A -> B`) moves the open callback `CRM Task` and its `ToDo` from `A` to `B` while preserving `due_date`.
  * Ownership `revoke` and `close_work_item` cancel any open callback `CRM Task` in the same DB transaction (`0` orphan open callbacks).
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime

from crm_cskh.api._params import parse_int, parse_optional_str, parse_pagination
from crm_cskh.p1_models import OWNED_ACTIVE_WORK_ITEM_STATES
from crm_cskh.p2_models import OPEN_TASK_STATES
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
	Conflict,
	DoNotContact,
	Forbidden,
	IdempotencyConflict,
	InvalidFollowupTime,
	InvalidRequest,
	NoteRequired,
	NotFound,
	OutcomeNotCompatible,
	OwnershipChanged,
	StaleWorkItem,
	WorkItemNotActive,
	rollback_savepoint_safely,
)
from crm_cskh.services.sanitizer import sanitize_note_text


def _payload_hash(payload: dict) -> str:
	canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_history(raw: str | None) -> list[dict]:
	if not raw:
		return []
	try:
		parsed = json.loads(raw)
	except (TypeError, ValueError):
		return []
	return parsed if isinstance(parsed, list) else []


def _parse_due_datetime(raw_due_at, now=None):
	if raw_due_at is None or (isinstance(raw_due_at, str) and not raw_due_at.strip()):
		raise InvalidFollowupTime("Thời điểm hẹn liên hệ lại (due_at) là bắt buộc.")
	try:
		due_dt = get_datetime(raw_due_at)
	except Exception as exc:
		raise InvalidFollowupTime("Định dạng thời gian hẹn liên hệ lại không hợp lệ.") from exc
	if due_dt is None:
		raise InvalidFollowupTime("Định dạng thời gian hẹn liên hệ lại không hợp lệ.")
	now_dt = get_datetime(now or now_datetime())
	if due_dt <= now_dt:
		raise InvalidFollowupTime("Thời điểm hẹn liên hệ lại phải nằm ở tương lai.")
	return due_dt


def _cancel_task_todos(task_name: str, allocated_to: str | None = None) -> None:
	filters: dict = {
		"reference_type": "CRM Task",
		"reference_name": str(task_name),
		"status": "Open",
	}
	if allocated_to:
		filters["allocated_to"] = allocated_to
	open_todos = frappe.db.get_all("ToDo", filters=filters, pluck="name")
	for todo_name in open_todos:
		frappe.db.set_value("ToDo", todo_name, "status", "Cancelled", update_modified=False)


def _ensure_task_todo(task_name: str, user: str, description: str) -> str | None:
	if not user:
		return None
	existing = frappe.db.get_value(
		"ToDo",
		{
			"reference_type": "CRM Task",
			"reference_name": str(task_name),
			"allocated_to": user,
			"status": "Open",
		},
		"name",
	)
	if existing:
		return existing
	now_ts = now_datetime()
	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": user,
			"reference_type": "CRM Task",
			"reference_name": str(task_name),
			"description": sanitize_note_text(description, max_length=255) or f"Callback CSKH ({task_name})",
			"priority": "Medium",
			"status": "Open",
			"assigned_by": frappe.session.user,
			"creation": now_ts,
			"modified": now_ts,
			"owner": frappe.session.user,
			"modified_by": frappe.session.user,
		}
	)
	# Persist ToDo inside the transaction without triggering upstream `crm.api.todo.after_insert`
	# (`notify_assigned_user`), so `CRM Notification` is emitted strictly post-commit (`P2.md` §15.4).
	todo.set_new_name()
	todo.db_insert()
	return todo.name


def get_open_followup_task(work_item: str) -> dict | None:
	if not work_item or not frappe.db.has_column("CRM Task", "cskh_work_item"):
		return None
	rows = frappe.db.sql(
		"""
		select name, title, description, status, due_date, assigned_to,
		       cskh_work_item, cskh_source_attempt, cskh_reschedule_count, cskh_task_history_json
		from `tabCRM Task`
		where cskh_work_item = %s
		  and coalesce(cskh_is_followup, 0) = 1
		  and status in ('Backlog', 'Todo', 'In Progress')
		order by creation desc, name desc
		""",
		(work_item,),
		as_dict=True,
	)
	if not rows:
		return None
	row = rows[0]
	row["name"] = str(row["name"])
	return row


def count_open_followup_tasks(work_item: str) -> int:
	if not work_item or not frappe.db.has_column("CRM Task", "cskh_work_item"):
		return 0
	return int(
		frappe.db.sql(
			"""
			select count(*) from `tabCRM Task`
			where cskh_work_item = %s
			  and coalesce(cskh_is_followup, 0) = 1
			  and status in ('Backlog', 'Todo', 'In Progress')
			""",
			(work_item,),
		)[0][0]
		or 0
	)


def _find_followup_idempotent_entry(
	work_item: str, actor: str, idempotency_key: str
) -> tuple[dict, dict] | None:
	if not idempotency_key or not frappe.db.has_column("CRM Task", "cskh_task_history_json"):
		return None
	rows = frappe.db.sql(
		"""
		select name, status, due_date, cskh_reschedule_count, cskh_task_history_json
		from `tabCRM Task`
		where cskh_work_item = %s and coalesce(cskh_is_followup, 0) = 1
		order by modified desc, name desc
		""",
		(work_item,),
		as_dict=True,
	)
	for row in rows:
		for entry in reversed(_parse_history(row.cskh_task_history_json)):
			if entry.get("idempotency_key") == idempotency_key and entry.get("actor") == actor:
				row["name"] = str(row["name"])
				return row, entry
	return None


def create_followup_in_tx(
	work_item_row: dict,
	due_at,
	actor: str,
	title: str | None = None,
	description: str | None = None,
	source_attempt: str | None = None,
	idempotency_key: str | None = None,
	payload_hash: str | None = None,
	now=None,
) -> str:
	"""Create the single open callback `CRM Task` for `work_item_row` inside caller's transaction."""
	now_dt = get_datetime(now or now_datetime())
	due_dt = _parse_due_datetime(due_at, now=now_dt)
	work_item = work_item_row["name"]
	lead = work_item_row["lead"]
	owner = work_item_row.get("assigned_to")
	if not owner:
		raise WorkItemNotActive("Work Item chưa có người phụ trách hợp lệ để đặt lịch hẹn lại.")

	if count_open_followup_tasks(work_item) > 0:
		raise Conflict(
			"Work Item đang có lịch hẹn mở; vui lòng dời lịch hoặc hoàn tất lịch hẹn hiện tại.",
			code="CONFLICT",
		)

	lead_display = (
		frappe.db.get_value("CRM Lead", lead, "lead_name")
		or frappe.db.get_value("CRM Lead", lead, "first_name")
		or lead
	)
	clean_title = sanitize_note_text(title, max_length=140) or f"Hẹn liên hệ lại — {lead_display}"
	clean_desc = sanitize_note_text(description, max_length=2000)

	history = [
		{
			"action": "create",
			"actor": actor,
			"at": str(now_dt),
			"due_date": str(due_dt),
			"assigned_to": owner,
			"source_attempt": source_attempt,
			"idempotency_key": idempotency_key,
			"payload_hash": payload_hash,
		}
	]

	prev_flag = getattr(frappe.flags, "cskh_in_followup_service", False)
	frappe.flags.cskh_in_followup_service = True
	try:
		# Insert with `assigned_to=None` so upstream `CRMTask.after_insert` neither runs
		# `frappe.desk.form.assign_to.add` (which demands generic write permission) nor
		# sends an un-committed notification before `frappe.db.commit()`.
		task = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": clean_title,
				"description": clean_desc,
				"status": "Todo",
				"priority": "Medium",
				"due_date": due_dt,
				"assigned_to": None,
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
				"cskh_work_item": work_item,
				"cskh_is_followup": 1,
				"cskh_source_attempt": source_attempt,
				"cskh_reschedule_count": 0,
				"cskh_task_history_json": json.dumps(history, ensure_ascii=False),
			}
		)
		task.insert(ignore_permissions=True)
		task_id = str(task.name)
		frappe.db.set_value("CRM Task", task_id, "assigned_to", owner, update_modified=False)
		_ensure_task_todo(task_id, owner, clean_title)

		from crm_cskh.services import sla_engine

		sla_engine.on_followup_created_in_tx(
			work_item=work_item,
			lead=lead,
			campaign=work_item_row.get("campaign"),
			owner_user=owner,
			task_id=task_id,
			due_dt=due_dt,
			actor=actor,
			now_dt=now_dt,
		)
		return task_id
	finally:
		frappe.flags.cskh_in_followup_service = prev_flag


def reschedule_followup_in_tx(
	task_row: dict,
	due_at,
	reason: str | None,
	actor: str,
	title: str | None = None,
	note: str | None = None,
	idempotency_key: str | None = None,
	payload_hash: str | None = None,
	now=None,
) -> tuple[str, int]:
	"""Reschedule an existing open callback `CRM Task` without creating a second task."""
	now_dt = get_datetime(now or now_datetime())
	due_dt = _parse_due_datetime(due_at, now=now_dt)
	clean_reason = sanitize_note_text(reason, max_length=1000)
	if not clean_reason:
		raise NoteRequired("Dời lịch hẹn bắt buộc ghi rõ lý do.")

	task_id = str(task_row["name"])
	prev_flag = getattr(frappe.flags, "cskh_in_followup_service", False)
	frappe.flags.cskh_in_followup_service = True
	try:
		doc = frappe.get_doc("CRM Task", task_id)
		old_due = str(doc.due_date) if doc.due_date else None
		new_count = cint(doc.cskh_reschedule_count) + 1
		history = _parse_history(doc.cskh_task_history_json)
		history.append(
			{
				"action": "reschedule",
				"actor": actor,
				"at": str(now_dt),
				"from_due_date": old_due,
				"to_due_date": str(due_dt),
				"reason": clean_reason,
				"reschedule_count": new_count,
				"idempotency_key": idempotency_key,
				"payload_hash": payload_hash,
			}
		)
		doc.due_date = due_dt
		if title:
			clean_title = sanitize_note_text(title, max_length=140)
			if clean_title:
				doc.title = clean_title
		if note is not None:
			clean_note = sanitize_note_text(note, max_length=2000)
			if clean_note:
				doc.description = clean_note
		doc.cskh_reschedule_count = new_count
		doc.cskh_task_history_json = json.dumps(history, ensure_ascii=False)

		from crm_cskh.services import sla_engine

		sla_engine.on_followup_rescheduled_in_tx(
			work_item=doc.cskh_work_item,
			task_id=task_id,
			new_due_dt=due_dt,
			new_reschedule_count=new_count,
			actor=actor,
			reason=clean_reason,
			now_dt=now_dt,
		)
		doc.save(ignore_permissions=True)
		return task_id, new_count
	finally:
		frappe.flags.cskh_in_followup_service = prev_flag


def resolve_open_followup_in_tx(
	work_item: str,
	attempt_id: str,
	actor: str,
	now=None,
	occurred_at=None,
	reason: str | None = None,
	task_id: str | None = None,
	idempotency_key: str | None = None,
	payload_hash: str | None = None,
) -> str | None:
	"""Mark the open callback `CRM Task` as `Done` linked to `attempt_id`."""
	if not attempt_id:
		raise OutcomeNotCompatible("Hoàn tất lịch hẹn bắt buộc gắn mã nhật ký liên hệ (attempt_id) hợp lệ.")
	open_task = get_open_followup_task(work_item)
	if not open_task:
		if task_id:
			raise Conflict("Lịch hẹn không còn ở trạng thái mở.", code="CONFLICT")
		return None
	if task_id and str(open_task["name"]) != str(task_id):
		raise Conflict("Mã lịch hẹn không khớp với lịch hẹn đang mở của Work Item.", code="CONFLICT")

	resolved_id = str(open_task["name"])
	now_dt = get_datetime(now or now_datetime())
	att_occ_dt = get_datetime(
		occurred_at or frappe.db.get_value("CSKH Contact Attempt", attempt_id, "occurred_at") or now_dt
	)
	clean_reason = sanitize_note_text(reason, max_length=1000) or f"Hoàn tất qua lần liên hệ {attempt_id}"

	prev_flag = getattr(frappe.flags, "cskh_in_followup_service", False)
	frappe.flags.cskh_in_followup_service = True
	try:
		doc = frappe.get_doc("CRM Task", resolved_id)
		history = _parse_history(doc.cskh_task_history_json)
		history.append(
			{
				"action": "complete",
				"actor": actor,
				"at": str(now_dt),
				"attempt_id": attempt_id,
				"reason": clean_reason,
				"idempotency_key": idempotency_key,
				"payload_hash": payload_hash,
			}
		)
		doc.status = "Done"
		doc.cskh_completed_attempt = attempt_id
		doc.cskh_completion_reason = clean_reason
		doc.cskh_task_history_json = json.dumps(history, ensure_ascii=False)
		doc.save(ignore_permissions=True)
		_cancel_task_todos(resolved_id)

		from crm_cskh.services import sla_engine

		sla_engine.on_followup_resolved_in_tx(
			work_item=work_item,
			task_id=resolved_id,
			attempt_id=attempt_id,
			occurred_at=att_occ_dt,
			actor=actor,
		)
		return resolved_id
	finally:
		frappe.flags.cskh_in_followup_service = prev_flag


def cancel_open_followup_in_tx(
	work_item: str,
	actor: str,
	reason: str,
	now=None,
	task_id: str | None = None,
	idempotency_key: str | None = None,
	payload_hash: str | None = None,
) -> str | None:
	"""Cancel any open callback `CRM Task` on `work_item` with mandatory audit reason."""
	clean_reason = sanitize_note_text(reason, max_length=1000)
	if not clean_reason:
		raise NoteRequired("Hủy lịch hẹn bắt buộc ghi rõ lý do.")
	if not frappe.db.has_column("CRM Task", "cskh_work_item"):
		return None

	open_rows = frappe.db.sql(
		"""
		select name from `tabCRM Task`
		where cskh_work_item = %s
		  and coalesce(cskh_is_followup, 0) = 1
		  and status in ('Backlog', 'Todo', 'In Progress')
		order by creation desc, name desc
		""",
		(work_item,),
		as_dict=True,
	)
	if not open_rows:
		if task_id:
			raise Conflict("Lịch hẹn không còn ở trạng thái mở.", code="CONFLICT")
		return None
	if task_id and all(str(r.name) != str(task_id) for r in open_rows):
		raise Conflict("Mã lịch hẹn không khớp với lịch hẹn đang mở của Work Item.", code="CONFLICT")

	now_dt = get_datetime(now or now_datetime())
	is_sys_cancel = bool(getattr(frappe.flags, "cskh_system_cancel_followup", False))
	prev_flag = getattr(frappe.flags, "cskh_in_followup_service", False)
	frappe.flags.cskh_in_followup_service = True
	first_canceled = None
	try:
		from crm_cskh.services import sla_engine

		for row in open_rows:
			tid = str(row.name)
			doc = frappe.get_doc("CRM Task", tid)
			history = _parse_history(doc.cskh_task_history_json)
			history.append(
				{
					"action": "cancel",
					"actor": actor,
					"at": str(now_dt),
					"reason": clean_reason,
					"idempotency_key": idempotency_key,
					"payload_hash": payload_hash,
				}
			)
			doc.status = "Canceled"
			doc.cskh_completion_reason = clean_reason
			doc.cskh_task_history_json = json.dumps(history, ensure_ascii=False)
			doc.save(ignore_permissions=True)
			_cancel_task_todos(tid)
			sla_engine.on_followup_canceled_in_tx(
				work_item=work_item,
				task_id=tid,
				canceled_at=now_dt,
				actor=actor,
				reason=clean_reason,
				is_valid_system_cancel=is_sys_cancel,
			)
			if first_canceled is None:
				first_canceled = tid
		return first_canceled
	finally:
		frappe.flags.cskh_in_followup_service = prev_flag


def reassign_open_followup_in_tx(
	work_item: str,
	lead: str,
	from_user: str | None,
	to_user: str,
	actor: str,
	reason: str,
	now=None,
) -> str | None:
	"""Transfer open callback `CRM Task` from `from_user` to `to_user`, preserving `due_date` (`T2-07`, `T2-14`)."""
	open_task = get_open_followup_task(work_item)
	if not open_task:
		return None
	task_id = str(open_task["name"])
	now_dt = get_datetime(now or now_datetime())
	clean_reason = sanitize_note_text(reason, max_length=1000) or "Chuyển người phụ trách Work Item"

	history = _parse_history(open_task.get("cskh_task_history_json"))
	history.append(
		{
			"action": "reassign",
			"actor": actor,
			"at": str(now_dt),
			"from_user": from_user,
			"to_user": to_user,
			"due_date": str(open_task.get("due_date")) if open_task.get("due_date") else None,
			"reason": clean_reason,
		}
	)
	frappe.db.set_value(
		"CRM Task",
		task_id,
		{
			"assigned_to": to_user,
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
			"cskh_task_history_json": json.dumps(history, ensure_ascii=False),
		},
		update_modified=False,
	)
	_cancel_task_todos(task_id, allocated_to=from_user)
	# Also clean up any stray open ToDo on the task belonging to anyone other than `to_user`.
	other_todos = frappe.db.get_all(
		"ToDo",
		filters={
			"reference_type": "CRM Task",
			"reference_name": task_id,
			"status": "Open",
			"allocated_to": ["!=", to_user],
		},
		pluck="name",
	)
	for td in other_todos:
		frappe.db.set_value("ToDo", td, "status", "Cancelled", update_modified=False)
	_ensure_task_todo(task_id, to_user, open_task.get("title") or f"Callback CSKH ({task_id})")
	return task_id


def _lock_work_item_for_followup(work_item: str) -> dict:
	rows = frappe.db.sql(
		"""
		select name, campaign, lead, status, revision, assigned_to, assigned_at,
		       attempt_count, connected_attempt_count, open_callback_task, next_action_due
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


def _check_locked_actor_and_revision(locked: dict, actor: str, expected_revision: int | None) -> None:
	if locked.status not in OWNED_ACTIVE_WORK_ITEM_STATES or not locked.assigned_to:
		raise WorkItemNotActive(
			"Work Item không ở trạng thái đang chăm sóc hoạt động.",
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
	if expected_revision is not None and current_rev != expected_revision:
		raise StaleWorkItem(
			"Dữ liệu Work Item đã thay đổi (lệch revision); vui lòng tải lại trước khi thao tác.",
			details={"expected_revision": expected_revision, "current_revision": current_rev},
		)


def schedule_followup(
	work_item: str,
	due_at,
	title: str | None = None,
	note: str | None = None,
	reason: str | None = None,
	expected_revision=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Create or reschedule the open callback `CRM Task` on `work_item` (`docs/api-contract-p2.md` §4.7)."""
	from crm_cskh.services import notifications

	actor = assert_workflow_actor(actor, allow_bod=False)
	work_item_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not work_item_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)
	title_val = parse_optional_str(title, "Tiêu đề lịch hẹn không hợp lệ.")
	note_val = parse_optional_str(note, "Ghi chú lịch hẹn không hợp lệ.")
	reason_val = parse_optional_str(reason, "Lý do dời lịch không hợp lệ.")

	assert_work_item_workflow_access(work_item_val, user=actor, require_active_mutation=False)

	req_hash = _payload_hash(
		{
			"op": "schedule_followup",
			"work_item": work_item_val,
			"due_at": str(due_at or "").strip(),
			"title": title_val or "",
			"note": note_val or "",
			"reason": reason_val or "",
		}
	)

	savepoint = f"cskh_sched_fu_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked = _lock_work_item_for_followup(work_item_val)
		prior = _find_followup_idempotent_entry(work_item_val, actor, idem_val)
		if prior:
			task_row, entry = prior
			if entry.get("payload_hash") and entry.get("payload_hash") != req_hash:
				raise IdempotencyConflict("Khóa idempotency_key đã được dùng với dữ liệu lịch hẹn khác.")
			return {
				"status": "rescheduled" if entry.get("action") == "reschedule" else "scheduled",
				"idempotent_replay": True,
				"task_id": str(task_row["name"]),
				"work_item": work_item_val,
				"work_item_status": locked.status,
				"due_at": entry.get("to_due_date") or entry.get("due_date") or task_row.get("due_date"),
				"reschedule_count": cint(
					entry.get("reschedule_count", task_row.get("cskh_reschedule_count") or 0)
				),
				"revision": cint(locked.revision or 1),
			}

		_check_locked_actor_and_revision(locked, actor, exp_rev)

		if frappe.db.has_column("CRM Lead", "cskh_do_not_contact") and cint(
			frappe.db.get_value("CRM Lead", locked.lead, "cskh_do_not_contact")
		):
			raise DoNotContact("Khách hàng đã yêu cầu ngừng liên hệ (DNC); không được đặt lịch hẹn lại.")

		now_dt = now_datetime()
		due_dt = _parse_due_datetime(due_at, now=now_dt)
		open_task = get_open_followup_task(work_item_val)

		if open_task:
			task_id, resched_count = reschedule_followup_in_tx(
				open_task,
				due_at=due_dt,
				reason=reason_val,
				actor=actor,
				title=title_val,
				note=note_val,
				idempotency_key=idem_val,
				payload_hash=req_hash,
				now=now_dt,
			)
			op_status = "rescheduled"
		else:
			task_id = create_followup_in_tx(
				locked,
				due_at=due_dt,
				actor=actor,
				title=title_val,
				description=note_val,
				source_attempt=None,
				idempotency_key=idem_val,
				payload_hash=req_hash,
				now=now_dt,
			)
			resched_count = 0
			op_status = "scheduled"

		next_rev = cint(locked.revision or 1) + 1
		frappe.db.set_value(
			"CSKH Work Item",
			work_item_val,
			{
				"status": "on_hold",
				"open_callback_task": task_id,
				"next_action_due": due_dt,
				"last_activity_by": actor,
				"revision": next_rev,
			},
			update_modified=True,
		)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()
	notifications.deliver_followup_notification(
		work_item=work_item_val,
		task_id=task_id,
		owner=locked.assigned_to,
		event_id=f"followup:{work_item_val}:{idem_val}",
		actor=actor,
	)
	return {
		"status": op_status,
		"idempotent_replay": False,
		"task_id": task_id,
		"work_item": work_item_val,
		"work_item_status": "on_hold",
		"due_at": due_dt,
		"reschedule_count": resched_count,
		"revision": next_rev,
	}


def complete_followup(
	work_item: str,
	task_id: str | None = None,
	action: str = "complete",
	attempt_id: str | None = None,
	completion_reason: str | None = None,
	expected_revision=None,
	idempotency_key: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Complete (with mandatory `attempt_id`) or cancel (with mandatory `completion_reason`) an open callback."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	work_item_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	if not work_item_val:
		raise InvalidRequest("Mã Work Item là bắt buộc.")
	action_val = (parse_optional_str(action, "Hành động không hợp lệ.") or "complete").lower()
	if action_val not in ("complete", "cancel"):
		raise InvalidRequest("Hành động xử lý lịch hẹn chỉ chấp nhận 'complete' hoặc 'cancel'.")
	idem_val = parse_optional_str(idempotency_key, "Khóa idempotency không hợp lệ.")
	if not idem_val:
		raise InvalidRequest("Khóa idempotency_key là bắt buộc.")
	task_id_val = parse_optional_str(str(task_id) if task_id is not None else None)
	attempt_val = parse_optional_str(attempt_id, "Mã nhật ký liên hệ không hợp lệ.")
	reason_val = parse_optional_str(completion_reason, "Lý do không hợp lệ.")
	exp_rev = (
		parse_int(expected_revision, minimum=1, field="expected_revision")
		if expected_revision is not None and str(expected_revision).strip() != ""
		else None
	)

	assert_work_item_workflow_access(work_item_val, user=actor, require_active_mutation=False)

	req_hash = _payload_hash(
		{
			"op": "complete_followup",
			"work_item": work_item_val,
			"task_id": task_id_val or "",
			"action": action_val,
			"attempt_id": attempt_val or "",
			"completion_reason": reason_val or "",
		}
	)

	savepoint = f"cskh_comp_fu_{frappe.generate_hash(length=6)}"
	frappe.db.savepoint(savepoint)
	try:
		locked = _lock_work_item_for_followup(work_item_val)
		prior = _find_followup_idempotent_entry(work_item_val, actor, idem_val)
		if prior:
			task_row, entry = prior
			if entry.get("payload_hash") and entry.get("payload_hash") != req_hash:
				raise IdempotencyConflict("Khóa idempotency_key đã được dùng với yêu cầu hoàn tất khác.")
			return {
				"status": "completed" if entry.get("action") == "complete" else "canceled",
				"idempotent_replay": True,
				"task_id": str(task_row["name"]),
				"work_item": work_item_val,
				"work_item_status": locked.status,
				"revision": cint(locked.revision or 1),
			}

		_check_locked_actor_and_revision(locked, actor, exp_rev)

		now_dt = now_datetime()
		if action_val == "complete":
			if not attempt_val:
				raise OutcomeNotCompatible(
					"Không được đánh dấu hoàn tất lịch hẹn suông; bắt buộc truyền attempt_id hợp lệ."
				)
			att = frappe.db.get_value(
				"CSKH Contact Attempt",
				attempt_val,
				["name", "work_item"],
				as_dict=True,
			)
			if not att or att.work_item != work_item_val:
				raise OutcomeNotCompatible("Mã nhật ký liên hệ không tồn tại hoặc không thuộc Work Item này.")
			resolved_task = resolve_open_followup_in_tx(
				work_item=work_item_val,
				attempt_id=attempt_val,
				actor=actor,
				now=now_dt,
				reason=reason_val,
				task_id=task_id_val,
				idempotency_key=idem_val,
				payload_hash=req_hash,
			)
			if not resolved_task:
				raise Conflict("Work Item hiện không có lịch hẹn mở để hoàn tất.", code="CONFLICT")
			out_status = "completed"
		else:
			if not reason_val or not sanitize_note_text(reason_val, max_length=1000):
				raise NoteRequired("Hủy lịch hẹn bắt buộc nhập lý do kiểm toán (completion_reason).")
			resolved_task = cancel_open_followup_in_tx(
				work_item=work_item_val,
				actor=actor,
				reason=reason_val,
				now=now_dt,
				task_id=task_id_val,
				idempotency_key=idem_val,
				payload_hash=req_hash,
			)
			if not resolved_task:
				raise Conflict("Work Item hiện không có lịch hẹn mở để hủy.", code="CONFLICT")
			out_status = "canceled"

		next_status = "in_progress" if cint(locked.attempt_count or 0) > 0 else "assigned"
		next_rev = cint(locked.revision or 1) + 1
		frappe.db.set_value(
			"CSKH Work Item",
			work_item_val,
			{
				"status": next_status,
				"open_callback_task": None,
				"next_action_due": None,
				"last_activity_by": actor,
				"revision": next_rev,
			},
			update_modified=True,
		)
	except Exception:
		rollback_savepoint_safely(savepoint)
		raise

	frappe.db.commit()
	return {
		"status": out_status,
		"idempotent_replay": False,
		"task_id": resolved_task,
		"work_item": work_item_val,
		"work_item_status": next_status,
		"revision": next_rev,
	}


def serialize_followup_task(row: dict, now=None, include_history: bool = False) -> dict:
	now_dt = get_datetime(now or now_datetime())
	is_open = row.get("status") in OPEN_TASK_STATES
	is_overdue = False
	if is_open and row.get("due_date"):
		try:
			is_overdue = get_datetime(row["due_date"]) < now_dt
		except Exception:
			is_overdue = False
	payload = {
		"task_id": str(row["name"]),
		"work_item": row.get("cskh_work_item"),
		"title": row.get("title") or "",
		"description": row.get("description") or "",
		"due_at": row.get("due_date"),
		"status": row.get("status"),
		"assigned_to": row.get("assigned_to"),
		"source_attempt": row.get("cskh_source_attempt"),
		"completed_attempt": row.get("cskh_completed_attempt"),
		"completion_reason": row.get("cskh_completion_reason"),
		"reschedule_count": cint(row.get("cskh_reschedule_count") or 0),
		"is_overdue": is_overdue,
	}
	if include_history:
		payload["history"] = [
			{k: v for k, v in item.items() if k != "payload_hash"}
			for item in _parse_history(row.get("cskh_task_history_json"))
		]
	return payload


def list_followups(
	page=1,
	page_size=20,
	work_item: str | None = None,
	campaign: str | None = None,
	bucket: str | None = None,
	status: str | None = "open",
	assignee: str | None = None,
	actor: str | None = None,
) -> dict:
	"""Scoped list of CSKH follow-up `CRM Task`s (`docs/api-contract-p2.md` §4.8)."""
	actor = assert_workflow_actor(actor, allow_bod=False)
	page_num, limit, offset = parse_pagination(page, page_size)
	wi_val = parse_optional_str(work_item, "Mã Work Item không hợp lệ.")
	campaign_val = parse_optional_str(campaign, "Mã chiến dịch không hợp lệ.")
	bucket_val = (parse_optional_str(bucket, "Bộ lọc bucket không hợp lệ.") or "all").lower()
	if bucket_val not in ("overdue", "today", "upcoming", "all"):
		raise InvalidRequest("Tham số bucket chỉ chấp nhận overdue | today | upcoming | all.")
	status_val = (parse_optional_str(status, "Bộ lọc status không hợp lệ.") or "open").lower()
	if status_val not in ("open", "done", "canceled", "all"):
		raise InvalidRequest("Tham số status chỉ chấp nhận open | done | canceled | all.")
	assignee_val = parse_optional_str(assignee, "Tham số assignee không hợp lệ.")

	if wi_val:
		assert_work_item_workflow_access(wi_val, user=actor, require_active_mutation=False)

	scope = reporting._scope(actor)
	scope_label = "all" if scope["scope"] in ("system", "all") else scope["scope"]
	now_dt = now_datetime()
	today = getdate(now_dt)
	day_start = f"{today} 00:00:00"
	day_end = f"{today} 23:59:59"

	clauses = ["coalesce(t.cskh_is_followup, 0) = 1"]
	params: list = []

	if scope["scope"] in ("system", "all"):
		pass
	elif scope["scope"] == "team":
		clauses.append(leader_authorized_work_item_sql(actor, "wi"))
	else:
		clauses.append("wi.assigned_to = %s")
		params.append(actor)
		clauses.append("wi.status in ('assigned', 'in_progress', 'on_hold')")

	if assignee_val:
		allowed_users = reporting._scope_users(scope)
		if allowed_users is not None and assignee_val not in allowed_users:
			return {
				"scope": scope_label,
				"items": [],
				"total": 0,
				"page": page_num,
				"page_size": limit,
				"as_of": now_dt,
			}
		clauses.append("t.assigned_to = %s")
		params.append(assignee_val)

	if wi_val:
		clauses.append("t.cskh_work_item = %s")
		params.append(wi_val)
	if campaign_val:
		clauses.append("wi.campaign = %s")
		params.append(campaign_val)

	if status_val == "open":
		clauses.append("t.status in ('Backlog', 'Todo', 'In Progress')")
	elif status_val == "done":
		clauses.append("t.status = 'Done'")
	elif status_val == "canceled":
		clauses.append("t.status = 'Canceled'")

	if bucket_val == "overdue":
		clauses.append("t.status in ('Backlog', 'Todo', 'In Progress') and t.due_date < %s")
		params.append(now_dt)
	elif bucket_val == "today":
		clauses.append("t.due_date >= %s and t.due_date <= %s")
		params.extend([day_start, day_end])
	elif bucket_val == "upcoming":
		clauses.append("t.due_date > %s")
		params.append(day_end)

	from_where = (
		"from `tabCRM Task` t "
		"inner join `tabCSKH Work Item` wi on wi.name = t.cskh_work_item "
		"inner join `tabCRM Lead` l on l.name = wi.lead "
		"left join `tabCSKH Campaign` c on c.name = wi.campaign "
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
			"as_of": now_dt,
		}

	rows = frappe.db.sql(
		f"""
		select t.name, t.title, t.description, t.status, t.due_date, t.assigned_to,
		       t.cskh_work_item, t.cskh_source_attempt, t.cskh_completed_attempt,
		       t.cskh_completion_reason, t.cskh_reschedule_count, t.cskh_task_history_json,
		       wi.lead as lead, wi.campaign as campaign, wi.status as work_item_status,
		       wi.revision as work_item_revision,
		       c.title as campaign_title,
		       l.lead_name as lead_name, l.first_name as first_name, l.mobile_no as mobile_no
		{from_where}
		order by case when t.status in ('Backlog', 'Todo', 'In Progress') and t.due_date < %s then 0 else 1 end asc,
		         t.due_date asc, t.name desc
		limit %s offset %s
		""",
		(*tuple(params), now_dt, limit, offset),
		as_dict=True,
	)

	items = []
	for row in rows:
		entry = serialize_followup_task(row, now=now_dt, include_history=True)
		entry.update(
			{
				"lead": row.lead,
				"display_name": row.lead_name or row.first_name or "",
				"phone": row.mobile_no,
				"campaign": row.campaign,
				"campaign_title": row.campaign_title,
				"work_item_status": row.work_item_status,
				"work_item_revision": cint(row.work_item_revision or 1),
			}
		)
		items.append(entry)

	return {
		"scope": scope_label,
		"items": items,
		"total": total,
		"page": page_num,
		"page_size": limit,
		"as_of": now_dt,
	}
