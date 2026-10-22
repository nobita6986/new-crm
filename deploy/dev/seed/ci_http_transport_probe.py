#!/usr/bin/env python3
"""Run sanitized HTTP transport checks for the P0 CI evidence job.

The probe talks only to the seeded fake-data site. Credentials, cookies, CSRF
tokens, and file URLs are used in memory and redacted from the evidence file.

Nhãn case SEC-16 vs SEC-26 (sửa theo T1C lần 17)
------------------------------------------------
- **SEC-16 = field exposure theo permlevel.** Probe gọi `crm.api.doc.get_data`
  với một Custom Field synthetic `permlevel=1` được ghi sentinel secret, và kiểm
  sentinel **không** xuất hiện trong response của non-system user. Khớp đúng
  semantics của test nguồn `test_crm_api_get_data_filters_permlevel_field`
  (field không có giá trị cho user thiếu System Manager).
- **SEC-26 = rò rỉ `file_url` của file private.** Các case gọi `get_attachments`
  và kiểm `file_url` **không** phải SEC-16; trước đây chúng bị gắn nhãn
  `sec16-*` khiến SEC-16 không hề được probe.

Việc chuẩn bị field permlevel (tạo Custom Field + ghi sentinel) chạy trong
container bench vì cần `frappe`; đó là **setup**, không phải bằng chứng — mọi
assertion SEC-16 đều đi qua HTTP endpoint.
"""

from __future__ import annotations

import ast
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("P0_HTTP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = os.environ.get("P0_HTTP_SITE", "p0-ci.localhost")
OUTPUT = Path(os.environ.get("P0_HTTP_EVIDENCE_DIR", "/tmp/p0-g007-http"))
SEED_SOURCE = Path(os.environ.get("P0_SEED_SOURCE", "/src/deploy/dev/seed/seed_demo.py"))
LEAD_NAME = "CRM-LEAD-00001"
PRIVATE_FILE_NAME = f"DEMO-{LEAD_NAME}.txt"

# --- SEC-16 permlevel probe -------------------------------------------------
# Field synthetic có `permlevel=1` trên CRM Lead. `permlevel > 0` nghĩa là field
# chỉ đọc được bởi user có System Manager role; mọi user khác phải không thấy.
SEC16_FIELDNAME = "custom_cskh_sec16_restricted"
SEC16_SENTINEL = "RESTRICTED-SEC-16-SENTINEL"
# `staff.a@dev.local` có role `Sales User` (không phải System Manager) và là
# `lead_owner` của CRM-LEAD-00001 ⇒ non-system caller có quyền đọc lead.
SEC16_USER = "staff.a@dev.local"


def seed_password_from_source(path: Path) -> str:
	"""Read the fake seed password as a literal without importing the seed."""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	for statement in tree.body:
		if isinstance(statement, ast.Assign) and any(
			isinstance(target, ast.Name) and target.id == "SEED_PASSWORD" for target in statement.targets
		):
			value = ast.literal_eval(statement.value)
			if isinstance(value, str) and value:
				return value
	raise RuntimeError("SEED_PASSWORD literal not found in seed source")


ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SEED_PASSWORD = seed_password_from_source(SEED_SOURCE)
SENSITIVE_KEYS = {
	"authorization",
	"cookie",
	"csrf_token",
	"file_url",
	"password",
	"pwd",
	"sid",
	"token",
}
records: list[dict[str, object]] = []
failures: list[str] = []


def redact_text(value: str) -> str:
	value = value.replace(ADMIN_PASSWORD, "[REDACTED]") if ADMIN_PASSWORD else value
	value = value.replace(SEED_PASSWORD, "[REDACTED]") if SEED_PASSWORD else value
	value = re.sub(
		r'(?i)(["\']X-Frappe-CSRF-Token["\']\s*:\s*["\'])[^"\']+',
		r"\1[REDACTED]",
		value,
	)
	value = re.sub(r"(?i)(csrf[_-]?token[\"'=:\s]+)[^\"'&\s<]+", r"\1[REDACTED]", value)
	value = re.sub(r"(?i)(sid[\"'=:\s]+)[^\"'&\s<]+", r"\1[REDACTED]", value)
	value = re.sub(r"(?i)(/private/files/)[^\"'\s,}]+", r"\1[REDACTED]", value)
	value = re.sub(r"(?i)(/files/)[^\"'\s,}]+", r"\1[REDACTED]", value)
	return value


def sanitize(value: object, key: str = "") -> object:
	if key.lower() in SENSITIVE_KEYS:
		return "[REDACTED]"
	if isinstance(value, dict):
		return {str(k): sanitize(v, str(k)) for k, v in value.items()}
	if isinstance(value, list):
		return [sanitize(item) for item in value]
	if isinstance(value, str):
		return redact_text(value)
	return value


def write_evidence() -> None:
	OUTPUT.mkdir(parents=True, exist_ok=True)
	payload = {
		"site": SITE_NAME,
		"base_url": BASE_URL,
		"recorded_at_utc": datetime.now(timezone.utc).isoformat(),
		"commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
		"frappe_sha": os.environ.get("FRAPPE_SHA", "unknown"),
		"crm_sha": os.environ.get("CRM_SHA", "unknown"),
		"coverage_scope": "direct HTTP to bench serve; reverse-proxy and CORS behavior are not covered",
		"cases": records,
		"failed_cases": failures,
		"probe_result": "FAIL" if failures else "PASS",
	}
	(OUTPUT / "http-transport.json").write_text(
		json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
	)


def make_opener() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
	jar = http.cookiejar.CookieJar()
	return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def request(
	case_id: str,
	opener: urllib.request.OpenerDirector,
	method: str,
	path: str,
	body: bytes | None = None,
	headers: dict[str, str] | None = None,
) -> dict[str, object]:
	url = BASE_URL + path
	request_headers = {
		"Accept": "application/json, text/html",
		"X-Frappe-Site-Name": SITE_NAME,
	}
	if headers:
		request_headers.update(headers)
	req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
	status: int | None = None
	response_headers: dict[str, str] = {}
	response_text = ""
	transport_error = None
	try:
		with opener.open(req, timeout=15) as response:
			status = response.status
			response_headers = dict(response.headers.items())
			response_text = response.read().decode("utf-8", errors="replace")
	except urllib.error.HTTPError as error:
		status = error.code
		response_headers = dict(error.headers.items()) if error.headers else {}
		response_text = error.read().decode("utf-8", errors="replace")
	except (urllib.error.URLError, TimeoutError, OSError) as error:
		transport_error = type(error).__name__

	request_body = body.decode("utf-8", errors="replace") if body else ""
	try:
		parsed_response: object = json.loads(response_text) if response_text else None
	except json.JSONDecodeError:
		parsed_response = None
	if parsed_response is not None:
		safe_response: object = sanitize(parsed_response)
		safe_body = json.dumps(safe_response, ensure_ascii=False)
	else:
		safe_body = redact_text(response_text)
	try:
		parsed_request = urllib.parse.parse_qs(request_body, keep_blank_values=True)
		if parsed_request:
			safe_request_body = json.dumps(sanitize(parsed_request), ensure_ascii=False)
		else:
			safe_request_body = redact_text(request_body)
	except (TypeError, ValueError):
		safe_request_body = "[REDACTED]"

	allowed_headers = {"accept", "content-type", "x-frappe-site-name", "x-frappe-csrf-token"}
	safe_request_headers = {
		key: ("[REDACTED]" if key.lower() == "x-frappe-csrf-token" else value)
		for key, value in request_headers.items()
		if key.lower() in allowed_headers
	}
	cookie_handler = next(
		(handler for handler in opener.handlers if isinstance(handler, urllib.request.HTTPCookieProcessor)),
		None,
	)
	has_cookies = bool(cookie_handler and any(cookie_handler.cookiejar))
	for key in list(response_headers):
		if key.lower() in {"set-cookie", "authorization", "x-frappe-csrf-token"}:
			response_headers[key] = "[REDACTED]"

	entry: dict[str, object] = {
		"id": case_id,
		"request": {
			"method": method,
			"url": url,
			"headers": safe_request_headers,
			"cookie": "[REDACTED]" if has_cookies else "",
			"body": safe_request_body,
		},
		"response": {
			"status": status,
			"headers": sanitize(response_headers),
			"body": safe_body,
			"exc_type": parsed_response.get("exc_type") if isinstance(parsed_response, dict) else None,
		},
		"transport_error": transport_error,
	}
	records.append(entry)
	write_evidence()
	# `transport_error` phai nam o day: `check()` doc `response["transport_error"]`
	# de phan biet "HTTP loi thuoc ve mo-duong" (assertion co the dung) voi
	# "khong goi duoc server" (transport_error khac None => case FAIL).
	# CI run #39793215900: thieu key nay lam moi case sau `sec13-guest` ném
	# KeyError va khong case nao duoc ghi nhan.
	return {
		"status": status,
		"json": parsed_response,
		"text": response_text,
		"entry": entry,
		"transport_error": transport_error,
	}


def check(case_id: str, response: dict[str, object], condition: bool, detail: str) -> bool:
	passed = bool(condition) and response["transport_error"] is None
	response["entry"]["assertion"] = {"expected": detail, "result": "PASS" if passed else "FAIL"}
	if not passed:
		failures.append(case_id)
		response["entry"]["assertion"]["detail"] = detail
	write_evidence()
	print(f"{case_id}: {'PASS' if passed else 'FAIL'}")
	return passed


def login(label: str, username: str, password: str) -> urllib.request.OpenerDirector:
	opener, _ = make_opener()
	body = urllib.parse.urlencode({"usr": username, "pwd": password}).encode()
	response = request(
		f"{label}-login",
		opener,
		"POST",
		"/api/method/login",
		body,
		{"Content-Type": "application/x-www-form-urlencoded"},
	)
	data = response["json"]
	check(
		f"{label}-login",
		response,
		response["status"] == 200 and isinstance(data, dict),
		"login returns HTTP 200",
	)
	return opener


def setup_sec16_permlevel_field() -> bool:
	"""Tạo field synthetic `permlevel=1` + ghi sentinel vào lead đã seed.

	Probe chạy **trong container bench** (`docker compose exec bench ... python`),
	nên có thể import `frappe` để chuẩn bị dữ liệu — giống `seed_demo.py` làm.
	Việc này là **setup**, không phải bằng chứng: assertion của SEC-16 nằm ở
	các case HTTP bên dưới, chạy hoàn toàn qua `crm.api.doc.get_data`.

	Trả về False nếu không thiết lập được (case SEC-16 sẽ FAIL fail-closed thay
	vì im lặng bỏ qua).
	"""
	try:
		import frappe
	except ImportError as error:
		print(f"sec16-setup: không import được frappe ({error})")
		return False

	bench_dir = os.environ.get("BENCH_DIR", "/workspace/frappe-bench")
	try:
		frappe.init(site=SITE_NAME, sites_path=os.path.join(bench_dir, "sites"))
		frappe.connect()
		frappe.set_user("Administrator")
		try:
			existing = frappe.db.exists(
				"Custom Field", {"dt": "CRM Lead", "fieldname": SEC16_FIELDNAME}
			)
			if not existing:
				frappe.get_doc(
					{
						"doctype": "Custom Field",
						"dt": "CRM Lead",
						"fieldname": SEC16_FIELDNAME,
						"label": "CSKH Sec16 Restricted",
						"fieldtype": "Data",
						"insert_after": "first_name",
						# permlevel > 0 ⇒ chỉ System Manager đọc được field này.
						"permlevel": 1,
					}
				).insert(ignore_permissions=True)
			frappe.clear_cache(doctype="CRM Lead")
			frappe.db.set_value("CRM Lead", LEAD_NAME, SEC16_FIELDNAME, SEC16_SENTINEL)
			frappe.db.commit()
			# Sanity: sentinel thực sự nằm trong DB (nếu không, case HTTP sẽ
			# "pass" một cách giả do dữ liệu vắng mặt).
			stored = frappe.db.get_value("CRM Lead", LEAD_NAME, SEC16_FIELDNAME)
			if stored != SEC16_SENTINEL:
				print(f"sec16-setup: sentinel không được ghi (got {stored!r})")
				return False
			return True
		except Exception as error:  # noqa: BLE001 - setup lỗi phải thành case FAIL
			frappe.db.rollback()
			print(f"sec16-setup: lỗi khi chuẩn bị permlevel field: {error}")
			return False
		finally:
			frappe.destroy()
	except Exception as error:  # noqa: BLE001
		print(f"sec16-setup: không kết nối được site: {error}")
		return False


def run() -> int:
	if not ADMIN_PASSWORD:
		raise RuntimeError("ADMIN_PASSWORD is not available in the bench container environment")

	guest, _ = make_opener()
	guest_response = request("sec13-guest", guest, "GET", "/api/method/crm_cskh.api.health.ping")
	guest_data = guest_response["json"]
	check(
		"sec13-guest",
		guest_response,
		guest_response["status"] == 403
		and isinstance(guest_data, dict)
		and guest_data.get("exc_type") == "PermissionError",
		"Guest request is denied with HTTP 403 PermissionError",
	)

	staff = login("staff", "staff.a@dev.local", SEED_PASSWORD)
	staff_ping = request("sec13-user", staff, "GET", "/api/method/crm_cskh.api.health.ping")
	staff_data = staff_ping["json"]
	check(
		"sec13-user",
		staff_ping,
		staff_ping["status"] == 200
		and isinstance(staff_data, dict)
		and isinstance(staff_data.get("message"), dict)
		and staff_data["message"].get("user") == "staff.a@dev.local",
		"authenticated staff receives own identity in HTTP response",
	)

	page = request("csrf-page", staff, "GET", "/cskh")
	token_match = re.search(
		r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)["\']',
		str(page["text"]),
	)
	csrf_token = token_match.group(1) if token_match else ""
	check(
		"csrf-page",
		page,
		page["status"] == 200 and bool(csrf_token),
		"authenticated /cskh returns a CSRF token for the browser",
	)

	ping_path = "/api/method/crm_cskh.api.health.ping"
	get_ping = request("sec14-get", staff, "GET", ping_path)
	check("sec14-get", get_ping, get_ping["status"] == 200, "safe GET succeeds without a CSRF header")

	post_without_token = request("sec14-post-nocsrf", staff, "POST", ping_path, b"")
	data = post_without_token["json"]
	check(
		"sec14-post-nocsrf",
		post_without_token,
		post_without_token["status"] == 400
		and isinstance(data, dict)
		and data.get("exc_type") == "CSRFTokenError",
		"unsafe POST without CSRF token returns HTTP 400 CSRFTokenError",
	)

	post_bad_token = request(
		"sec14-post-badtok", staff, "POST", ping_path, b"", {"X-Frappe-CSRF-Token": "invalid-ci-probe-token"}
	)
	data = post_bad_token["json"]
	check(
		"sec14-post-badtok",
		post_bad_token,
		post_bad_token["status"] == 400
		and isinstance(data, dict)
		and data.get("exc_type") == "CSRFTokenError",
		"unsafe POST with incorrect CSRF token returns HTTP 400 CSRFTokenError",
	)

	post_valid = request("sec14-post-ok", staff, "POST", ping_path, b"", {"X-Frappe-CSRF-Token": csrf_token})
	data = post_valid["json"]
	check(
		"sec14-post-ok",
		post_valid,
		post_valid["status"] == 200
		and isinstance(data, dict)
		and isinstance(data.get("message"), dict)
		and data["message"].get("status") == "ok",
		"unsafe POST with session CSRF token succeeds",
	)

	admin = login("administrator", "Administrator", ADMIN_PASSWORD)
	admin_ping = request("sec13-admin", admin, "GET", ping_path)
	admin_data = admin_ping["json"]
	check(
		"sec13-admin",
		admin_ping,
		admin_ping["status"] == 200
		and isinstance(admin_data, dict)
		and isinstance(admin_data.get("message"), dict)
		and admin_data["message"].get("user") == "Administrator",
		"Administrator receives own identity in HTTP response",
	)

	query = urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME})
	attachments_path = "/api/method/crm_cskh.api.guarded_crm.get_attachments?" + query

	# --- SEC-16: field exposure (permlevel) qua `crm.api.doc.get_data` --------
	# SEC-16 KHÔNG phải về `file_url`. Các case gọi `get_attachments` và kiểm
	# `file_url` thuộc SEC-26 (xem `sec26-*` bên dưới) — trước đây chúng bị gắn
	# nhãn `sec16-*` và làm SEC-16 không hề được probe.
	sec16_ready = setup_sec16_permlevel_field()
	if not sec16_ready:
		check(
			"sec16-setup",
			{"entry": {}, "transport_error": "sec16-setup-failed"},
			False,
			"permlevel=1 field with sentinel secret is prepared on the seeded lead",
		)
	else:
		# `crm.api.doc.get_data` là endpoint upstream có trả permlevel field; gọi
		# nó với `rows=[name, <field permlevel=1>]` để pin hành vi lọc.
		rows_param = urllib.parse.urlencode(
			{"doctype": "CRM Lead", "name": LEAD_NAME, "rows": f'["name","{SEC16_FIELDNAME}"]'}
		)
		get_data_path = "/api/method/crm.api.doc.get_data?" + rows_param
		staff_get_data = request("sec16-permlevel-get-data", staff, "GET", get_data_path)
		staff_get_data_payload = staff_get_data["json"]
		message = (
			staff_get_data_payload.get("message")
			if isinstance(staff_get_data_payload, dict)
			else None
		)
		rows = message.get("data") if isinstance(message, dict) else None
		row = rows[0] if isinstance(rows, list) and rows else None
		check(
			"sec16-permlevel-get-data",
			staff_get_data,
			staff_get_data["status"] == 200
			and isinstance(row, dict)
			# Khớp semantics test nguồn `test_crm_api_get_data_filters_permlevel_field`:
			# field permlevel phải **vắng/falsy** trong data trả về cho user
			# không có System Manager.
			and not row.get(SEC16_FIELDNAME),
			f"non-system user receives no value for permlevel=1 field {SEC16_FIELDNAME}",
		)

		# Chứng minh sentinel KHÔNG lộ ở **bất kỳ** nơi nào trong response, kể cả
		# khi client không yêu cầu cột permlevel — bảo vệ chống rò rì qua field khác.
		staff_get_all = request(
			"sec16-permlevel-sentinel-scan",
			staff,
			"GET",
			"/api/method/crm.api.doc.get_data?"
			+ urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME}),
		)
		check(
			"sec16-permlevel-sentinel-scan",
			staff_get_all,
			staff_get_all["status"] == 200
			# So trên **raw text** của response để bắt sentinel ở *bất kỳ* field
			# nào, kể cả khi client không yêu cầu cột permlevel.
			# Không assert key `SEC16_FIELDNAME` vắng mặt: test nguồn chỉ chứng
			# minh **giá trị** vắng/falsy, và "key bị loại" là kết luận mạnh hơn.
			and SEC16_SENTINEL not in str(staff_get_all["text"]),
			f"sentinel secret {SEC16_FIELDNAME} never appears in non-system response",
		)

	# --- SEC-26: `file_url` của file private không lộ cho non-system caller --
	# Nhãn `sec16-*` ở đây là sai (T1C lần 17): nội dung là SEC-26.
	staff_files = request("sec26-staff-private", staff, "GET", attachments_path)
	staff_data = staff_files["json"]
	staff_entries = staff_data.get("message") if isinstance(staff_data, dict) else None
	staff_private = next(
		(
			entry
			for entry in staff_entries or []
			if isinstance(entry, dict) and entry.get("file_name") == PRIVATE_FILE_NAME
		),
		None,
	)
	check(
		"sec26-staff-private",
		staff_files,
		staff_files["status"] == 200
		and isinstance(staff_private, dict)
		and bool(staff_private.get("is_private"))
		# Wrapper `_redact_private_file_urls` **loại bỏ key** `file_url` cho
		# non-system caller + file private. Kiểm key vắng mặt, không phải falsy.
		and "file_url" not in staff_private,
		"non-system response redacts file_url for seeded private attachment",
	)

	admin_files = request("sec26-admin-private", admin, "GET", attachments_path)
	admin_data = admin_files["json"]
	admin_entries = admin_data.get("message") if isinstance(admin_data, dict) else None
	admin_private = next(
		(
			entry
			for entry in admin_entries or []
			if isinstance(entry, dict) and entry.get("file_name") == PRIVATE_FILE_NAME
		),
		None,
	)
	check(
		"sec26-admin-private",
		admin_files,
		admin_files["status"] == 200
		and isinstance(admin_private, dict)
		and bool(admin_private.get("is_private"))
		# System caller: wrapper trả payload nguyên bản ⇒ `file_url` còn nguyên.
		and isinstance(admin_private.get("file_url"), str)
		and bool(admin_private["file_url"]),
		"system caller retains file_url for seeded private attachment",
	)

	return 1 if failures else 0


if __name__ == "__main__":
	try:
		exit_code = run()
	except Exception as error:  # evidence should still be written on an unexpected probe error
		failures.append(f"probe-error:{type(error).__name__}")
		print(f"HTTP probe failed: {type(error).__name__}")
		exit_code = 1
	finally:
		write_evidence()
	sys.exit(exit_code)
