import re

import frappe

from crm_cskh.permissions import LEAD_LINKS, _is_system, user_in_scope


def _check_read(doctype, name):
	if not frappe.has_permission(doctype, "read", name, throw=False):
		frappe.throw("Not permitted", frappe.PermissionError)


def _require_system_phone_lookup():
	if not _is_system(frappe.session.user):
		frappe.throw(
			"Phone lookup is disabled until it can be restricted to the user's scope.", frappe.PermissionError
		)


@frappe.whitelist()
def get_contact_by_phone_number(phone_number):
	_require_system_phone_lookup()
	return frappe.get_attr("crm.integrations.api.get_contact_by_phone_number")(phone_number)


@frappe.whitelist()
def get_contact_lead_or_deal_from_number(number):
	_require_system_phone_lookup()
	return frappe.get_attr("crm.integrations.api.get_contact_lead_or_deal_from_number")(number)


@frappe.whitelist()
def get_assigned_users(doctype, name, default_assigned_to=None):
	_check_read(doctype, name)
	users = frappe.get_attr("crm.api.doc.get_assigned_users")(doctype, name, default_assigned_to)
	return [user for user in users if user_in_scope(frappe.session.user, user)]


@frappe.whitelist()
def add_assignment(args=None):
	"""Guard generic Frappe assignment for CRM Leads.

	Generic assignment must not change Lead ownership or grant DocShare access.
	Only assignment to the current owner is allowed; ownership changes go through
	the CRM Lead document path, where its permission hooks and share cleanup run.
	"""
	from frappe.desk.form.assign_to import add as upstream_add

	args = args or frappe.local.form_dict
	doctype = args.get("doctype")
	name = args.get("name")
	assignees = frappe.parse_json(args.get("assign_to"))
	owner_before = None
	if doctype == "CRM Lead":
		owner_before = frappe.db.get_value("CRM Lead", name, "lead_owner")
		if not owner_before or any(assignee != owner_before for assignee in (assignees or [])):
			frappe.throw(
				"CRM Lead assignments cannot change ownership; use the controlled Lead ownership path.",
				frappe.PermissionError,
			)

	result = upstream_add(args)
	return result


# Field level redaction cho entry có `is_private` URL — áp dụng cho non-system caller
# nhằm chặn rò rỉ URL của file private. SEC-26.
#
# Bao gồm `content_hash` để defense-in-depth: source `crm.api.activities.get_attachments`
# CRM v1.87.0 (commit `0d3910b456ef4fb913e77b29db011b3debc4f778`, xem evidence
# `t1b-sec26-upstream-contract-v1.87.0.md`) chỉ liệt kê
# `[name, file_name, file_type, file_url, file_size, is_private, modified, creation,
# owner]` trong `frappe.db.get_all("File", ...)` — không chọn `content_hash`, nên
# upstream không trả field này ở v1.87.0. Helper vẫn loại bỏ `content_hash` nếu field
# này xuất hiện trong payload khác (ví dụ kết quả từ endpoint trả dict có key khác)
# hoặc khi upstream đổi shape. Nếu upstream thực sự bổ sung `content_hash`, cần viết
# test riêng để pin redaction (TODO T1B-ext+1).
_FILE_URL_FIELDS = ("file_url", "content_hash")
_PRIVATE_FILE_URL_RE = re.compile(r"(?i)(?:https?://[^/\s\"'<>]+)?/private/files/[^\s\"'<>]*")
_PUBLIC_FILE_URL_RE = re.compile(r"(?i)^(?:https?://[^/\s\"'<>]+)?/files/")


def _redact_private_file_urls(entries):
	"""Trả về list các entry giống `entries` nhưng đã redact URL/hash cho non-system caller.

	- System caller (Administrator / System Manager): trả nguyên bản.
	- Non-system caller: với mỗi entry có `is_private` truthy, loại bỏ `file_url`;
	  đồng thời loại bỏ `content_hash` **nếu key này xuất hiện trong entry**.
	  Các field khác (file_name, name, is_private, file_size) được giữ nguyên giá
	  trị upstream — helper không đánh giá độ nhạy cảm của chúng.

	Lưu ý về contract: CRM v1.87.0 `crm.api.activities.get_attachments` không chọn
	`content_hash` trong `fields=...` (đã xác minh tĩnh), nên việc redact
	`content_hash` hiện là no-op đối với payload của `get_attachments`. Helper giữ
	field này trong `_FILE_URL_FIELDS` để áp dụng khi payload đến từ nguồn khác
	hoặc khi upstream đổi shape (xem evidence
	`t1b-sec26-upstream-contract-v1.87.0.md`).
	"""
	if _is_system(frappe.session.user):
		return entries
	redacted = []
	for entry in entries:
		if not isinstance(entry, dict):
			redacted.append(entry)
			continue
		# Nếu entry là File có is_private=1 → redact URL.
		is_file = entry.get("is_private") is not None or "file_url" in entry
		if is_file and entry.get("is_private"):
			new_entry = {k: v for k, v in entry.items() if k not in _FILE_URL_FIELDS}
			redacted.append(new_entry)
		else:
			redacted.append(entry)
	return redacted


def _redact_private_file_payload(value):
	"""Đệ quy loại URL file private khỏi payload hoạt động/DocInfo.

	`get_activities` gộp attachment, attachment log dạng HTML và `docinfo` vào
	một response. Không thể chỉ lọc list top-level như `get_attachments`.
	Non-system caller không nhận URL private/không xác định; URL public có đường
	dẫn `/files/` rõ ràng được giữ. URL private nhúng trong HTML/văn bản bị thay
	thế. System caller giữ nguyên.
	"""
	if _is_system(frappe.session.user):
		return value
	if isinstance(value, dict):
		is_private = bool(value.get("is_private"))
		clean = {}
		for key, item in value.items():
			if key == "file_url":
				url = item if isinstance(item, str) else ""
				if is_private or _PRIVATE_FILE_URL_RE.search(url):
					continue
				if not _PUBLIC_FILE_URL_RE.match(url):
					continue
			elif key == "content_hash" and is_private:
				continue
			clean[key] = _redact_private_file_payload(item)
		return clean
	if isinstance(value, list):
		return [_redact_private_file_payload(item) for item in value]
	if isinstance(value, tuple):
		return tuple(_redact_private_file_payload(item) for item in value)
	if isinstance(value, str):
		return _PRIVATE_FILE_URL_RE.sub("[REDACTED_PRIVATE_FILE_URL]", value)
	return value


@frappe.whitelist()
def get_linked_docs_of_document(doctype, docname):
	_check_read(doctype, docname)
	docs = frappe.get_attr("crm.api.doc.get_linked_docs_of_document")(doctype, docname)
	filtered = [
		linked
		for linked in docs
		if frappe.has_permission(linked["doc"], "read", linked["reference_docname"], throw=False)
	]
	# SEC-26: nếu linked doc là File private, redact URL cho non-system caller.
	return _redact_private_file_urls(filtered)


@frappe.whitelist()
def get_linked_calls(name):
	_check_read("CRM Lead", name)
	return frappe.get_attr("crm.api.activities.get_linked_calls")(name)


@frappe.whitelist()
def get_linked_notes(name):
	_check_read("CRM Lead", name)
	return frappe.get_attr("crm.api.activities.get_linked_notes")(name)


@frappe.whitelist()
def get_linked_tasks(name):
	_check_read("CRM Lead", name)
	return frappe.get_attr("crm.api.activities.get_linked_tasks")(name)


@frappe.whitelist()
def get_attachments(doctype, name):
	_check_read(doctype, name)
	attachments = frappe.get_attr("crm.api.activities.get_attachments")(doctype, name)
	# SEC-26: redact `file_url`/`content_hash` của file private cho non-system caller
	# để chặn rò rỉ URL private. System caller nhận payload nguyên bản.
	# (CRM v1.87.0 không chọn `content_hash` trong `fields=...` của
	# `crm.api.activities.get_attachments`, nên redaction trên `content_hash` ở
	# đây hiện là no-op — xem evidence `t1b-sec26-upstream-contract-v1.87.0.md`.)
	return _redact_private_file_urls(attachments)


@frappe.whitelist()
def get_activities(name: str):
	"""Guard CRM activity timeline and its side-channel `frappe.response.docinfo`."""
	if frappe.db.exists("CRM Deal", name):
		doctype = "CRM Deal"
	elif frappe.db.exists("CRM Lead", name):
		doctype = "CRM Lead"
	else:
		# Keep the upstream not-found behavior and message.
		return frappe.get_attr("crm.api.activities.get_activities")(name)

	_check_read(doctype, name)
	activities = frappe.get_attr("crm.api.activities.get_activities")(name)
	if not _is_system(frappe.session.user):
		activities = _redact_private_file_payload(activities)
		response = getattr(frappe, "response", None)
		if isinstance(response, dict) and response.get("docinfo") is not None:
			response["docinfo"] = _redact_private_file_payload(response["docinfo"])
	return activities


@frappe.whitelist()
def delete_bulk_docs(doctype: str, items: str | list, delete_linked: bool = False):
	items = frappe.parse_json(items)
	if doctype in ("CRM Lead", "CRM Deal", *LEAD_LINKS):
		for name in items:
			frappe.has_permission(doctype, "delete", name, throw=True)
	return frappe.get_attr("crm.api.doc.delete_bulk_docs")(doctype, items, delete_linked)


@frappe.whitelist()
def get_call_log(name: str):
	"""Override của `crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log`.

	Upstream `parse_call_log(call)` gọi `get_call_contact(call, phone_number)` →
	`get_contact_by_phone_number(phone_number)` khi Call Log không tham chiếu Lead/Deal.
	Nhánh này lộ `_caller`/`_receiver` cho user có DocType-level read trên CRM Call Log —
	rò rỉ contact theo số điện thoại. SEC-27.

	Fail-closed:
	  - System caller: delegate nguyên bản.
	  - Non-system + ref là CRM Lead/Deal: kiểm tra scope qua hook permission rồi delegate.
	  - Non-system + ref KHÔNG phải CRM Lead/Deal (kể cả None): từ chối để chặn lộ contact."""
	frappe.has_permission("CRM Call Log", "read", name, throw=True)
	if not _is_system(frappe.session.user):
		# Nhánh fallback chỉ an toàn khi Call Log có `reference_doctype` là Lead/Deal
		# (vì parse_call_log sẽ dùng parent contact). Khi ref rỗng/khác, nhánh fallback
		# gọi `get_contact_by_phone_number` → rò rỉ contact. Chặn non-system caller.
		ref_doctype = frappe.db.get_value("CRM Call Log", name, "reference_doctype")
		if ref_doctype not in ("CRM Lead", "CRM Deal"):
			frappe.throw(
				"Call Log fallback contact lookup is restricted to system callers "
				"until record-scoped reporting is available.",
				frappe.PermissionError,
			)
	return frappe.get_attr("crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log")(name)
