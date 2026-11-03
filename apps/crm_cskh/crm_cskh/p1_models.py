"""P1 domain model: validation, state machines and DB constraints.

Every P1 invariant that can be expressed as a DB constraint lives in the DocType
JSON (unique flags) and is re-asserted here as an application-level check so a
misuse fails closed with a readable message instead of a raw IntegrityError.

Naming follows the app convention chosen at P0: `p1_*` modules hold the domain
logic for the P1 layer, `services/` holds the controlled service layer.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from crm_cskh.services.phone_identity import InvalidPhone, normalize_vn_phone

# P1 §1.3: the states actually used by P1 are only queued / assigned / revoked /
# closed. `in_progress` and `on_hold` are reserved for P2 and MUST NOT be used
# by P1 code, otherwise the "one active work item" invariant becomes ambiguous.
ACTIVE_WORK_ITEM_STATES = ("queued", "assigned")
TERMINAL_WORK_ITEM_STATES = ("revoked", "closed")

IMPORT_STATUS_TRANSITIONS = {
	"DRY_RUN": ("CONFIRMED", "FAILED"),
	"CONFIRMED": ("QUEUED", "FAILED"),
	"QUEUED": ("RUNNING", "FAILED"),
	"RUNNING": ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"),
	"COMPLETED": (),
	"COMPLETED_WITH_ERRORS": ("QUEUED",),
	"FAILED": ("QUEUED",),
}

RUN_STATUS_TRANSITIONS = {
	"planned": ("queued", "running", "failed"),
	"queued": ("running", "failed"),
	"running": ("queued", "completed", "completed_with_errors", "failed"),
	"completed": (),
	"completed_with_errors": ("planned", "queued"),
	"failed": ("planned", "queued"),
}


def ensure_indexes(doctype):
	"""Create the P1 constraints that Frappe's DocType sync cannot express.

	`unique: 1` on a DocField already produces a unique index; the composite
	uniqueness rules of P1 §5.6 need `frappe.db.add_unique` and therefore live
	here. Every call is idempotent (Frappe checks the constraint name first),
	which is what makes `bench migrate` safe to run twice.
	"""
	unique = {
		"CSKH Work Item": ("campaign", "lead"),
		"CSKH Assignment Rule": ("campaign", "user", "version"),
		"CSKH Import Batch Lead": ("import_batch", "lead"),
	}
	if doctype in unique:
		frappe.db.add_unique(doctype, unique[doctype], "cskh_domain_unique")
	indexes = {
		"CSKH Work Item": (
			("assigned_to", "status", "assigned_at"),
			("campaign", "status"),
			("lead", "status"),
		),
		"CSKH Assignment History": (
			("work_item", "event_at"),
			("to_user", "ownership_start", "ownership_end"),
		),
		"CSKH Distribution Run": (("campaign", "status"),),
		"CSKH Import Batch Lead": (
			("lead", "import_batch"),
			("import_batch", "outcome"),
		),
	}
	for i, fields in enumerate(indexes.get(doctype, ())):
		frappe.db.add_index(doctype, fields, f"cskh_lookup_{i}")


def _reject_status_change(doc, transitions, label):
	status = doc.status
	if doc.is_new():
		return
	previous = doc.get_doc_before_save()
	if not previous or previous.status == status:
		return
	if status not in transitions.get(previous.status, ()):
		frappe.throw(
			_("{0} cannot move from {1} to {2}.").format(label, previous.status, status),
			frappe.ValidationError,
		)


class Campaign(Document):
	def validate(self):
		if self.started_at and self.ended_at and get_datetime(self.ended_at) < get_datetime(self.started_at):
			frappe.throw(_("The end date must not precede the start date."))
		if self.rule_version is not None and self.rule_version < 1:
			frappe.throw(_("The rule version must be positive."))
		if self.selection_json:
			try:
				selection = json.loads(self.selection_json)
			except (ValueError, TypeError):
				frappe.throw(_("The selection must be a JSON object."))
			if not isinstance(selection, dict):
				frappe.throw(_("The selection must be a JSON object."))
		if self.status == "Closed" and frappe.db.exists(
			"CSKH Work Item", {"campaign": self.name, "status": ["in", ACTIVE_WORK_ITEM_STATES]}
		):
			frappe.throw(
				_(
					"Resolve the active work items before closing the campaign; nothing is released implicitly."
				)
			)


class AssignmentRule(Document):
	def validate(self):
		if self.weight is None or self.weight <= 0 or self.capacity_limit < 0 or self.version < 1:
			frappe.throw(_("Weight and version must be positive; capacity must not be negative."))
		if not frappe.db.get_value("User", self.user, "enabled") or "Sales User" not in frappe.get_roles(
			self.user
		):
			frappe.throw(_("The assignee must be an enabled Sales User."))
		if self.effective_from and self.effective_to:
			if get_datetime(self.effective_to) < get_datetime(self.effective_from):
				frappe.throw(_("The end date must not precede the start date."))
		if frappe.db.exists(
			"CSKH Assignment Rule",
			{
				"campaign": self.campaign,
				"user": self.user,
				"version": self.version,
				"name": ["!=", self.name],
			},
		):
			frappe.throw(_("This campaign, assignee and version already has a rule."))


class WorkItem(Document):
	def validate(self):
		if self.status == "assigned" and not self.assigned_to:
			frappe.throw(_("An assigned work item requires an assignee."))
		if self.status in ("queued", "revoked", "closed") and self.assigned_to:
			frappe.throw(_("A queued, revoked or closed work item must not have an assignee."))
		if self.status == "assigned" and not self.assigned_at:
			frappe.throw(_("An assigned work item requires an assignment timestamp."))


class ActiveClaim(Document):
	def validate(self):
		item = frappe.db.get_value(
			"CSKH Work Item", self.work_item, ["lead", "campaign", "status"], as_dict=True
		)
		if not item or item.lead != self.lead or item.campaign != self.campaign:
			frappe.throw(_("The active claim must reference the same lead and campaign as its work item."))
		if item.status not in ACTIVE_WORK_ITEM_STATES:
			frappe.throw(_("Only an active work item may hold an active claim."))


class IdentityIndex(Document):
	def validate(self):
		try:
			self.normalized_phone = normalize_vn_phone(self.normalized_phone)
		except InvalidPhone:
			frappe.throw(_("The phone identity must be a canonical Vietnamese mobile number."))
		lead_phone = frappe.db.get_value("CRM Lead", self.canonical_lead, "mobile_no")
		if not lead_phone:
			frappe.throw(_("The canonical lead must have a mobile number."))
		try:
			if normalize_vn_phone(lead_phone) != self.normalized_phone:
				raise InvalidPhone("Identity mismatch")
		except InvalidPhone:
			frappe.throw(_("The phone identity must match the canonical lead mobile number."))


class ImportBatch(Document):
	def validate(self):
		if not frappe.db.get_value("File", self.source_file, "is_private"):
			frappe.throw(_("Import source files must be private."))
		for field in ("total_rows", "valid_rows", "new_rows", "duplicate_rows", "error_rows"):
			if self.get(field, 0) < 0:
				frappe.throw(_("Import counters must not be negative."))
		if self.mapping_json:
			try:
				mapping = json.loads(self.mapping_json)
			except (ValueError, TypeError):
				frappe.throw(_("Column mapping must be a JSON object."))
			if not isinstance(mapping, dict):
				frappe.throw(_("Column mapping must be a JSON object."))
		_reject_status_change(self, IMPORT_STATUS_TRANSITIONS, _("The import batch"))


class ImportBatchLead(Document):
	def validate(self):
		if self.outcome not in ("created", "reused"):
			frappe.throw(_("Import batch lead outcome must be created or reused."))
		if int(self.row_number or 0) < 0:
			frappe.throw(_("Import batch lead row number must not be negative."))
		if frappe.db.exists(
			"CSKH Import Batch Lead",
			{
				"import_batch": self.import_batch,
				"lead": self.lead,
				"name": ["!=", self.name],
			},
		):
			frappe.throw(_("This import batch and lead link already exists."))


class DistributionRun(Document):
	def validate(self):
		if self.rule_version < 1:
			frappe.throw(_("The rule version must be positive."))
		if not self.selection_fingerprint:
			frappe.throw(_("The selection fingerprint is required."))
		if self.preview_json:
			try:
				json.loads(self.preview_json)
			except (ValueError, TypeError):
				frappe.throw(_("The preview payload must be valid JSON."))
		if self.actual_json:
			try:
				json.loads(self.actual_json)
			except (ValueError, TypeError):
				frappe.throw(_("The actual payload must be valid JSON."))
		if self.state_json:
			try:
				state = json.loads(self.state_json)
			except (ValueError, TypeError):
				frappe.throw(_("The allocator state must be valid JSON."))
			if not isinstance(state, dict):
				frappe.throw(_("The allocator state must be a JSON object."))
		_reject_status_change(self, RUN_STATUS_TRANSITIONS, _("The distribution run"))


class AssignmentHistory(Document):
	def before_insert(self):
		if not self.actor:
			self.actor = frappe.session.user
		if not self.event_at:
			self.event_at = now_datetime()
		if not self.event_id:
			frappe.throw(_("The event id is required so the event stays idempotent."))

	def before_save(self):
		if not self.is_new():
			frappe.throw(_("Assignment history is append-only; use a compensating event."))
		if self.ownership_start and self.ownership_end:
			if get_datetime(self.ownership_end) < get_datetime(self.ownership_start):
				frappe.throw(_("The end date must not precede the start date."))
		if self.action == "assign" and not self.to_user:
			frappe.throw(_("An assignment event requires a target user."))
		if self.action == "revoke" and self.to_user:
			frappe.throw(_("A revocation event must not have a target user."))

	def on_trash(self):
		frappe.throw(_("Assignment history is append-only; use a compensating event."))
