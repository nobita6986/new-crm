import re

import frappe

from crm_cskh.hooks import P1_DOCTYPES, P2_DOCTYPES, P3_DOCTYPES
from crm_cskh.p1_permissions import (
	PERMLEVEL_PROTECTED_FIELDS,
	can_read_protected_field,
	validate_p1_filter_fields,
)
from crm_cskh.p2_models import assert_no_lead_dnc_mutation_fields
from crm_cskh.permissions import LEAD_LINKS, _is_system, user_in_scope

GUARDED_SEARCH_DOCTYPES = frozenset(
	(*P1_DOCTYPES, *P2_DOCTYPES, *P3_DOCTYPES, "CRM Holiday List", "CRM Lead", "CRM Deal", *LEAD_LINKS)
)


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


def _link_target_is_readable(link_doctype: str, link_name: str) -> bool:
	"""`True` khi caller hiện tại được đọc `link_doctype`/`link_name`."""
	if not link_doctype or not link_name:
		return False
	return bool(frappe.has_permission(link_doctype, "read", link_name, throw=False))


def _call_log_in_scope(call: dict) -> bool:
	"""`True` khi Call Log được phép hiển thị cho caller hiện tại (non-system).

	Fail-closed:
	  - Bỏ mọi row mang `link_doctype`/`link_name` (do upstream join Dynamic Link).
	    Row Dynamic Link không chứng minh `from`/`to`, `note`, `recording_url` hay
	    nhãn contact (phone lookup) đều an toàn; giữ nó có thể lộ `link_name` ngoài
	    scope (khi một Call Log có nhiều Dynamic Link) hoặc lộ dữ liệu của Call Log
	    orphan (thiếu `reference_doctype`/`reference_docname`).
	  - Chỉ giữ Call Log có `reference_doctype`/`reference_docname` trỏ tới
	    Lead/Deal mà caller đọc được — đường hợp lệ, upstream dùng
	    `get_reference_contact` (không phone lookup).
	"""
	if call.get("link_doctype") or call.get("link_name"):
		return False

	ref_doctype = call.get("reference_doctype")
	ref_docname = call.get("reference_docname")
	if ref_doctype in ("CRM Lead", "CRM Deal") and ref_docname:
		return _link_target_is_readable(ref_doctype, ref_docname)

	return False


def _filter_linked_calls_for_scope(result: dict) -> dict:
	"""Lọc `get_linked_calls` của upstream theo scope của caller (non-system).

	Giữ nguyên shape của upstream (`calls`/`notes`/`tasks`) để không làm hỏng
	hợp đồng của endpoint.
	"""
	if not isinstance(result, dict):
		return result

	kept_calls = []
	for call in result.get("calls") or []:
		if not isinstance(call, dict):
			continue
		if not _call_log_in_scope(call):
			continue
		clean = dict(call)
		# Dùng cùng nhận diện URL private như payload hoạt động, kể cả URL
		# tuyệt đối/protocol-relative. Tải file vẫn cần authorization riêng;
		# redaction ở đây bảo toàn chính sách URL của custom CRM endpoint.
		recording_url = clean.get("recording_url")
		if isinstance(recording_url, str) and _PRIVATE_FILE_URL_RE.search(recording_url):
			clean.pop("recording_url", None)
		kept_calls.append(clean)

	kept_notes = []
	for note in result.get("notes") or []:
		if not isinstance(note, dict):
			continue
		if _link_target_is_readable("FCRM Note", note.get("name")):
			kept_notes.append(note)

	kept_tasks = []
	for task in result.get("tasks") or []:
		if not isinstance(task, dict):
			continue
		if _link_target_is_readable("CRM Task", task.get("name")):
			kept_tasks.append(task)

	return {"calls": kept_calls, "notes": kept_notes, "tasks": kept_tasks}


def _filter_linked_calls_payload(payload):
	"""Lọc tuple `(activities, calls, notes, tasks, attachments)` của
	`crm.api.activities.get_activities` theo scope của caller (non-system).

	Upstream `get_lead_activities`/`get_deal_activities` gọi
	`get_linked_calls`/`get_linked_notes`/`get_linked_tasks` **nội bộ** (không qua
	wrapper `crm_cskh`), nên payload của `get_activities` cũng đi qua cùng đường
	Dynamic Link. Vì vậy phải lọc `calls`/`notes`/`tasks` ở đây bằng CÙNG tiêu chí
	với `get_linked_calls` — nếu không, `get_activities` là đường vòng để đọc lại
	dữ liệu ngoài scope đã bị chặn ở `get_linked_calls`.
	"""
	if not isinstance(payload, (list, tuple)) or len(payload) < 3:
		return payload
	calls = payload[1]
	notes = payload[2]
	tasks = payload[3] if len(payload) > 3 else []
	attachments = payload[4] if len(payload) > 4 else []

	filtered = _filter_linked_calls_for_scope(
		{"calls": calls or [], "notes": notes or [], "tasks": tasks or []}
	)
	out = list(payload)
	out[1] = filtered["calls"]
	out[2] = filtered["notes"]
	out[3] = filtered["tasks"]
	if len(payload) > 4:
		out[4] = attachments
	return out


@frappe.whitelist()
def get_linked_calls(name):
	_check_read("CRM Lead", name)
	result = frappe.get_attr("crm.api.activities.get_linked_calls")(name)
	if _is_system(frappe.session.user):
		return result
	# T1B-ext+1 — fail-closed theo từng bản ghi.
	#
	# Upstream CRM v1.87.0 `crm.api.activities.get_linked_calls` (commit
	# `0d3910b456ef4fb913e77b29db011b3debc4f778`) dùng `frappe.db.get_all` cho
	# CẢ ba nhánh, tức bỏ qua `has_permission` của app:
	#   1. `calls` — lấy theo `reference_docname` VÀ qua `Dynamic Link`
	#      (`parenttype = "CRM Call Log"`, `link_name = <Lead>`).
	#   2. `notes` — lấy Note mà Dynamic Link trỏ tới.
	#   3. `tasks` — lấy Task mà Dynamic Link trỏ tới.
	# Vì `db.get_all` không áp permission query condition, Call Log chỉ nối bằng
	# Dynamic Link (không có `reference_doctype`/`reference_docname`) vẫn lọt vào
	# response; `parse_call_log` gọi `get_call_contact` → khi `reference_doctype`
	# rỗng thì rơi vào `get_contact_by_phone_number` ⇒ lộ Contact NGOÀN scope theo
	# số điện thoại; `recording_url` private cũng được trả nguyên văn.
	#
	# Vá hẹp (chỉ trong `crm_cskh`, KHÔNG sửa upstream):
	#   - `calls`: chỉ giữ Call Log có `reference_doctype`/`reference_docname` trỏ
	#     tới Lead/Deal trong scope. Bỏ mọi row Dynamic Link (`link_doctype`/
	#     `link_name`) và Call Log orphan — vì không thể chứng minh `from`/`to`,
	#     `note`, `recording_url` hay nhãn contact (phone lookup) đều an toàn.
	#   - `notes`/`tasks`: giữ lại Note/Task mà caller đọc được (dùng
	#     `frappe.has_permission` với `throw=False` ⇒ cùng tiêu chí với hook
	#     `linked_lead_has_permission` đang áp cho `frappe.client.get_list`).
	#   - `recording_url`: với Call Log được giữ lại, gỡ URL file private
	#     (tương đối hoặc tuyệt đối, theo SEC-26); URL public giữ nguyên.
	return _filter_linked_calls_for_scope(result)


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
		# T1B-ext+1: lọc `calls`/`notes`/`tasks` theo scope TRƯỚC khi redact URL
		# private. Upstream `get_activities` gọi `get_linked_calls` nội bộ nên
		# payload này đi qua cùng đường Dynamic Link — lọc ở đây để
		# `get_activities` không trở thành đường vòng đọc dữ liệu ngoài scope.
		activities = _filter_linked_calls_payload(activities)
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


@frappe.whitelist()
def search_link(
	doctype: str,
	txt: str,
	query: str | None = None,
	filters: str | dict | list | None = None,
	page_length: int = 10,
	searchfield: str | None = None,
	reference_doctype: str | None = None,
	ignore_user_permissions: bool = False,
):
	"""Guard `frappe.desk.search.search_link` against `ignore_user_permissions=1` bypass (`L-29`)."""
	if not _is_system(frappe.session.user):
		validate_p1_filter_fields(doctype, filters=filters, searchfield=searchfield)
		if doctype in GUARDED_SEARCH_DOCTYPES:
			ignore_user_permissions = False
	return frappe.get_attr("frappe.desk.search.search_link")(
		doctype=doctype,
		txt=txt,
		query=query,
		filters=filters,
		page_length=page_length,
		searchfield=searchfield,
		reference_doctype=reference_doctype,
		ignore_user_permissions=ignore_user_permissions,
	)


@frappe.whitelist()
def search_widget(
	doctype: str,
	txt: str,
	query: str | None = None,
	searchfield: str | None = None,
	start: int = 0,
	page_length: int = 10,
	filters: str | None | dict | list = None,
	filter_fields: str | None = None,
	as_dict: bool = False,
	reference_doctype: str | None = None,
	ignore_user_permissions: bool = False,
):
	"""Guard `frappe.desk.search.search_widget` against `ignore_user_permissions=1` bypass (`L-29`)."""
	if not _is_system(frappe.session.user):
		validate_p1_filter_fields(
			doctype,
			filters=filters,
			searchfield=searchfield,
			filter_fields=filter_fields,
		)
		if doctype in GUARDED_SEARCH_DOCTYPES:
			ignore_user_permissions = False
	return frappe.get_attr("frappe.desk.search.search_widget")(
		doctype=doctype,
		txt=txt,
		query=query,
		searchfield=searchfield,
		start=start,
		page_length=page_length,
		filters=filters,
		filter_fields=filter_fields,
		as_dict=as_dict,
		reference_doctype=reference_doctype,
		ignore_user_permissions=ignore_user_permissions,
	)


@frappe.whitelist()
def validate_link(doctype: str, docname: str, fields: list[str] | str | None = None):
	"""Guard `frappe.client.validate_link` against leaking foreign document names (`L-29`)."""
	upstream = frappe.get_attr("frappe.client.validate_link")
	if _is_system(frappe.session.user) or doctype not in GUARDED_SEARCH_DOCTYPES:
		return upstream(doctype, docname, fields=fields)
	if not isinstance(doctype, str) or not isinstance(docname, str):
		return upstream(doctype, docname, fields=fields)
	if not (
		frappe.has_permission(doctype, "select", throw=False)
		or frappe.has_permission(doctype, "read", throw=False)
	):
		return upstream(doctype, docname, fields=fields)
	existing_name = frappe.db.get_value(doctype, docname, cache=True)
	if not existing_name or not frappe.has_permission(doctype, "read", doc=existing_name, throw=False):
		return frappe._dict()
	return upstream(doctype, docname, fields=fields)


def _is_field_readable_by_user(doctype: str | None, fieldname: str, user: str) -> bool:
	if not doctype:
		return True
	protected = PERMLEVEL_PROTECTED_FIELDS.get(doctype)
	if not protected:
		return True
	base = fieldname.split(".", 1)[-1].strip("` ")
	if base == "*":
		return False
	if base in protected and not can_read_protected_field(doctype, base, user):
		return False
	return True


def _sanitize_get_value_fieldname(doctype: str, parent: str | None, fieldname):
	user = frappe.session.user
	if _is_system(user):
		return fieldname, True
	has_protected = bool(PERMLEVEL_PROTECTED_FIELDS.get(doctype)) or bool(
		isinstance(parent, str) and PERMLEVEL_PROTECTED_FIELDS.get(parent)
	)
	if not has_protected:
		return fieldname, True

	parsed = fieldname
	was_list = isinstance(fieldname, list | tuple | set)
	if isinstance(parsed, str) and parsed.strip().startswith("["):
		try:
			maybe_list = frappe.parse_json(parsed)
			if isinstance(maybe_list, list):
				parsed = maybe_list
				was_list = True
		except Exception:
			parsed = fieldname

	candidates: list[str] = []
	if isinstance(parsed, str):
		candidates.append(parsed.strip())
	elif isinstance(parsed, list | tuple | set):
		candidates.extend(str(item).strip() for item in parsed if item is not None)
	else:
		return fieldname, True

	allowed: list[str] = []
	for raw in candidates:
		if not raw:
			continue
		if "(" in raw:
			validate_p1_filter_fields(doctype, explicit_fields=raw, user=user)
			if isinstance(parent, str):
				validate_p1_filter_fields(parent, explicit_fields=raw, user=user)
		if _is_field_readable_by_user(doctype, raw, user) and _is_field_readable_by_user(parent, raw, user):
			allowed.append(raw)

	if candidates and not allowed:
		return ([] if was_list else ""), False
	return (allowed if was_list else allowed[0]), True


@frappe.whitelist()
def client_get_value(doctype, fieldname, filters=None, as_dict=True, debug=False, parent=None):
	"""Guard `frappe.client.get_value` against permlevel field leaks & filter oracles (`F-P2-01`, `L-29`)."""
	user = frappe.session.user
	if isinstance(doctype, str) and not _is_system(user):
		validate_p1_filter_fields(doctype, filters=filters, user=user)
		if isinstance(parent, str):
			validate_p1_filter_fields(parent, filters=filters, user=user)
		sanitized_fieldname, has_any = _sanitize_get_value_fieldname(
			doctype, parent if isinstance(parent, str) else None, fieldname
		)
		if not has_any:
			want_dict = bool(frappe.parse_json(as_dict)) if isinstance(as_dict, str) else bool(as_dict)
			return frappe._dict() if want_dict else None
		fieldname = sanitized_fieldname
	res = frappe.get_attr("frappe.client.get_value")(
		doctype,
		fieldname,
		filters=filters,
		as_dict=as_dict,
		debug=debug,
		parent=parent,
	)
	if isinstance(res, dict) and isinstance(doctype, str) and not _is_system(user):
		for key in list(res.keys()):
			if not _is_field_readable_by_user(doctype, str(key), user) or (
				isinstance(parent, str) and not _is_field_readable_by_user(parent, str(key), user)
			):
				res.pop(key, None)
	return res


@frappe.whitelist()
def client_get_count(doctype, filters=None, debug=False, cache=False):
	"""Guard `frappe.client.get_count` against permlevel filter oracles (`F-P2-01`, `L-29`)."""
	if isinstance(doctype, str) and not _is_system(frappe.session.user):
		validate_p1_filter_fields(doctype, filters=filters)
	return frappe.get_attr("frappe.client.get_count")(
		doctype,
		filters=filters,
		debug=debug,
		cache=cache,
	)


@frappe.whitelist()
def client_set_value(doctype, name, fieldname, value=None):
	"""Guard `frappe.client.set_value` against direct `CRM Lead` DNC mutations (`F-P2-06`)."""
	if isinstance(doctype, str):
		assert_no_lead_dnc_mutation_fields(doctype, fieldname)
	return frappe.get_attr("frappe.client.set_value")(doctype, name, fieldname, value=value)


@frappe.whitelist()
def client_bulk_update(docs):
	"""Guard `frappe.client.bulk_update` against direct `CRM Lead` DNC mutations (`F-P2-06`)."""
	parsed = frappe.parse_json(docs) if isinstance(docs, str) else docs
	if isinstance(parsed, list):
		for entry in parsed:
			if isinstance(entry, dict) and entry.get("doctype") == "CRM Lead":
				assert_no_lead_dnc_mutation_fields("CRM Lead", entry)
	return frappe.get_attr("frappe.client.bulk_update")(docs)
