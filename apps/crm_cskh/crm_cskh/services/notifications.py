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

# The notification type of an assignment. It is the CRM bell entry that tells
# the assignee that a customer was handed over to them.
NOTIFICATION_TYPE = "Assignment"


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
			"message": f"You were assigned a CRM Lead {lead}.",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
		}
	)
	notification.insert(ignore_permissions=True)
	return notification.name
