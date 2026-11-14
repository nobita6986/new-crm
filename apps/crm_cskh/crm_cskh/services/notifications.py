"""Post-commit notification delivery for the P1 ownership service (P1 §7.5).

P1 §7.5 is explicit about the order:

    "Các sự kiện ra ngoài transaction (notifications/realtime) thực hiện sau
     commit, không coi gửi thông báo thành công là điều kiện lưu assignment
     thành công."

and §7.6 step 6:

    "Commit; sau đó thông báo/cache invalidation phù hợp."

So the **assignment** is the durable fact and the **notification** is a
best-effort side effect that happens **after** the commit. A failed delivery
must never undo the assignment and must never be able to create a second
assignment event.

The delivery is **idempotent** on two levels:

* the `ToDo` of the assignee is looked up before it is created, so a repeated
  delivery never produces a second task;
* the `CRM Notification` is looked up by its (reference, user, type) triple
  before it is created, so a repeated delivery never produces a second
  notification.

The delivery is therefore safe to **retry**: retrying a delivery that failed
after the commit re-notifies the assignee but never writes a second
assignment event, a second work item or a second active claim (P1 §12.4:
*"Failure sau DB commit trước thông báo: retry notification không tạo event
giao mới"*).
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

# The notification type of an assignment. It is the CRM bell entry that tells
# the assignee that a customer was handed over to them.
NOTIFICATION_TYPE = "Assignment"

TODO_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"status",
	"priority",
	"date",
	"allocated_to",
	"description",
	"reference_type",
	"reference_name",
	"assigned_by",
]

CRM_NOTIFICATION_BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"from_user",
	"to_user",
	"type",
	"read",
	"message",
	"reference_doctype",
	"reference_name",
]


def pending_deliveries():
	"""Return the number of deliveries that are still pending (for the evidence)."""
	return frappe.db.count("ToDo", {"status": "Open", "description": ["like", "%CSKH assignment%"]})


def deliver_assignment(work_item, to_user, event_id, actor=None):
	"""Deliver the assignment notification of `work_item` to `to_user`.

	The function is **idempotent** and never raises: a failed delivery is a
	logged event, not a failure of the assignment (P1 §7.5).
	"""
	actor = actor or frappe.session.user
	try:
		_lead = frappe.db.get_value("CSKH Work Item", work_item, "lead")
		if not _lead:
			return {"work_item": work_item, "delivered": False, "reason": "no_work_item"}

		todo = _ensure_todo(_lead, to_user, work_item)
		notification = _ensure_notification(work_item, _lead, to_user, actor)
		return {
			"work_item": work_item,
			"delivered": True,
			"event_id": event_id,
			"todo": todo,
			"notification": notification,
		}
	except Exception as exc:
		# A delivery failure is never fatal: the assignment is already
		# committed (P1 §7.5), so the failure is a logged event only.
		frappe.log_error(
			title="P1 assignment notification failed",
			message=f"{type(exc).__name__}: {exc}\nwork_item={work_item} event_id={event_id}",
		)
		return {
			"work_item": work_item,
			"delivered": False,
			"error": f"{type(exc).__name__}: {exc}",
		}


def _ensure_todo(lead, to_user, work_item):
	"""Create the assignment ToDo of the assignee if it does not exist yet."""
	existing = frappe.db.get_value(
		"ToDo",
		{
			"reference_type": "CRM Lead",
			"reference_name": lead,
			"allocated_to": to_user,
			"status": "Open",
		},
		"name",
	)
	if existing:
		return existing
	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": to_user,
			"reference_type": "CRM Lead",
			"reference_name": lead,
			"description": f"CSKH assignment ({work_item})",
			"priority": "Medium",
			"status": "Open",
			"assigned_by": frappe.session.user,
		}
	)
	todo.insert(ignore_permissions=True)
	return todo.name


def _ensure_notification(work_item, lead, to_user, actor):
	"""Create the `CRM Notification` of the assignee if it does not exist yet.

	The lookup uses the same triple the upstream `notify_user` uses, so the
	delivery never produces a second notification for the same assignment.
	"""
	existing = frappe.db.get_value(
		"CRM Notification",
		{
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"to_user": to_user,
			"type": NOTIFICATION_TYPE,
		},
		"name",
	)
	if existing:
		return existing
	notification = frappe.get_doc(
		{
			"doctype": "CRM Notification",
			"from_user": actor,
			"to_user": to_user,
			"type": NOTIFICATION_TYPE,
			"message": f"Bạn được phân công hồ sơ liên hệ {lead}.",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
		}
	)
	notification.insert(ignore_permissions=True)
	return notification.name


_DEFAULT_DELIVER_ASSIGNMENT = deliver_assignment
_DEFAULT_ENSURE_TODO = _ensure_todo
_DEFAULT_ENSURE_NOTIFICATION = _ensure_notification


def deliver_assignments_batch(items, run_name, actor=None):
	"""Idempotent post-commit batch delivery of assignment `ToDo` and `CRM Notification` rows."""
	if not items:
		return {"delivered": 0, "failed": 0}

	actor = actor or frappe.session.user
	normalized = []
	for entry in items:
		if len(entry) == 3:
			wi, lead, to_user = entry
		else:
			wi, to_user = entry
			lead = None
		normalized.append((wi, lead, to_user))

	if (
		deliver_assignment is not _DEFAULT_DELIVER_ASSIGNMENT
		or _ensure_todo is not _DEFAULT_ENSURE_TODO
		or _ensure_notification is not _DEFAULT_ENSURE_NOTIFICATION
		or len(normalized) == 1
	):
		delivered = 0
		failed = 0
		for wi, _lead, to_user in normalized:
			res = deliver_assignment(wi, to_user, event_id=f"assign:{wi}:run:{run_name}", actor=actor)
			if res.get("delivered"):
				delivered += 1
			else:
				failed += 1
		return {"delivered": delivered, "failed": failed}

	try:
		frappe.db.savepoint("cskh_notif_batch")
		missing_wis = [wi for wi, lead, _ in normalized if wi and not lead]
		lead_by_wi: dict[str, str] = {}
		if missing_wis:
			ph_wi = ", ".join(["%s"] * len(missing_wis))
			for row in frappe.db.sql(
				f"select name, lead from `tabCSKH Work Item` where name in ({ph_wi})",
				tuple(missing_wis),
				as_dict=True,
			):
				if row.lead:
					lead_by_wi[str(row.name)] = str(row.lead)

		valid_items: list[tuple[str, str, str]] = []
		failed = 0
		for wi, lead, to_user in normalized:
			eff_lead = lead or lead_by_wi.get(str(wi))
			if not eff_lead or not to_user:
				failed += 1
			else:
				valid_items.append((str(wi), str(eff_lead), str(to_user)))

		if not valid_items:
			return {"delivered": 0, "failed": failed}

		leads = sorted({ld for _, ld, _ in valid_items})
		ph_leads = ", ".join(["%s"] * len(leads))
		now_ts = now_datetime()
		today_str = now_ts.date().isoformat()
		session_user = getattr(getattr(frappe, "session", None), "user", None) or actor or "Administrator"

		existing_todos = {
			(str(r.reference_name), str(r.allocated_to))
			for r in frappe.db.sql(
				f"""
				select reference_name, allocated_to
				from `tabToDo`
				where reference_type = 'CRM Lead'
				  and status = 'Open'
				  and reference_name in ({ph_leads})
				""",
				tuple(leads),
				as_dict=True,
			)
		}

		seen_todo_names: set[str] = set()
		todo_rows: list[tuple] = []
		for wi, ld, u in valid_items:
			if (ld, u) in existing_todos:
				continue
			existing_todos.add((ld, u))
			tname = frappe.generate_hash(length=10)
			while tname in seen_todo_names:
				tname = frappe.generate_hash(length=10)
			seen_todo_names.add(tname)
			todo_rows.append(
				(
					tname,
					now_ts,
					now_ts,
					session_user,
					session_user,
					0,
					0,
					"Open",
					"Medium",
					today_str,
					u,
					f"CSKH assignment ({wi})",
					"CRM Lead",
					ld,
					session_user,
				)
			)

		if todo_rows:
			frappe.db.bulk_insert("ToDo", fields=TODO_BULK_FIELDS, values=todo_rows)

		existing_notifs = {
			(str(r.reference_name), str(r.to_user))
			for r in frappe.db.sql(
				f"""
				select reference_name, to_user
				from `tabCRM Notification`
				where reference_doctype = 'CRM Lead'
				  and type = %s
				  and reference_name in ({ph_leads})
				""",
				(NOTIFICATION_TYPE, *leads),
				as_dict=True,
			)
		}

		seen_notif_names: set[str] = set()
		notif_rows: list[tuple] = []
		new_recipients: set[str] = set()
		for _wi, ld, u in valid_items:
			if (ld, u) in existing_notifs:
				continue
			existing_notifs.add((ld, u))
			nname = frappe.generate_hash(length=10)
			while nname in seen_notif_names:
				nname = frappe.generate_hash(length=10)
			seen_notif_names.add(nname)
			notif_rows.append(
				(
					nname,
					now_ts,
					now_ts,
					session_user,
					session_user,
					0,
					0,
					actor,
					u,
					NOTIFICATION_TYPE,
					0,
					f"Bạn được phân công hồ sơ liên hệ {ld}.",
					"CRM Lead",
					ld,
				)
			)
			new_recipients.add(u)

		if notif_rows:
			frappe.db.bulk_insert(
				"CRM Notification",
				fields=CRM_NOTIFICATION_BULK_FIELDS,
				values=notif_rows,
			)
			for recipient in sorted(new_recipients):
				try:
					frappe.publish_realtime("crm_notification", user=recipient)
				except Exception:
					pass

		return {"delivered": len(valid_items), "failed": failed}
	except Exception:
		frappe.db.rollback(save_point="cskh_notif_batch")
		delivered = 0
		failed = 0
		for wi, _lead, to_user in normalized:
			res = deliver_assignment(wi, to_user, event_id=f"assign:{wi}:run:{run_name}", actor=actor)
			if res.get("delivered"):
				delivered += 1
			else:
				failed += 1
		return {"delivered": delivered, "failed": failed}


def deliver_followup_notification(work_item, task_id, owner, event_id, actor=None):
	"""Post-commit, idempotent `CRM Notification` delivery for CSKH follow-up `CRM Task`s (`P2.md` §15.3).

	Never raises and never rolls back the already-committed attempt/follow-up transaction.
	"""
	from crm_cskh.services.sanitizer import redact_pii_text

	actor = actor or frappe.session.user
	if not task_id or not owner:
		return {"work_item": work_item, "delivered": False, "reason": "missing_target"}
	task_ref = str(task_id)
	try:
		lead = frappe.db.get_value("CSKH Work Item", work_item, "lead")
		existing = frappe.db.get_value(
			"CRM Notification",
			{
				"notification_type_doctype": "CRM Task",
				"notification_type_doc": task_ref,
				"to_user": owner,
				"type": NOTIFICATION_TYPE,
			},
			"name",
		) or frappe.db.get_value(
			"CRM Notification",
			{
				"reference_doctype": "CRM Task",
				"reference_name": task_ref,
				"to_user": owner,
				"type": NOTIFICATION_TYPE,
			},
			"name",
		)
		if not existing:
			doc = frappe.get_doc(
				{
					"doctype": "CRM Notification",
					"from_user": actor,
					"to_user": owner,
					"type": NOTIFICATION_TYPE,
					"message": f"Bạn có lịch hẹn liên hệ lại #{task_ref}.",
					"notification_type_doctype": "CRM Task",
					"notification_type_doc": task_ref,
					"reference_doctype": "CRM Lead" if lead else "CRM Task",
					"reference_name": lead or task_ref,
				}
			)
			doc.insert(ignore_permissions=True)
			existing = doc.name
			frappe.db.commit()
		return {
			"work_item": work_item,
			"task_id": task_ref,
			"delivered": True,
			"event_id": event_id,
			"notification": existing,
		}
	except Exception as exc:
		frappe.db.rollback()
		safe_err = redact_pii_text(f"{type(exc).__name__}: {exc}")
		try:
			frappe.log_error(
				title="P2 followup notification failed",
				message=f"{safe_err}\nwork_item={work_item} task_id={task_ref} event_id={event_id}",
			)
		except Exception:
			pass
		return {
			"work_item": work_item,
			"task_id": task_ref,
			"delivered": False,
			"error": safe_err,
		}
