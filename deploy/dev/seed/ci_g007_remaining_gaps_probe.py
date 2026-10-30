#!/usr/bin/env python3
"""Fail-closed direct-HTTP characterization for remaining P0 matrix gaps.

Runs only against the disposable seeded p0-ci site. Response bodies, user
credentials, CSRF tokens, File IDs, private URLs, and seed text stay in memory;
artifact output contains statuses, lengths, hashes, and booleans only.
"""

from __future__ import annotations

import ast
import atexit
import hashlib
import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

BASE_URL = os.environ.get("P0_HTTP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = os.environ.get("P0_HTTP_SITE", "p0-ci.localhost")
OUTPUT = Path(
    os.environ.get("P0_G007_GAPS_EVIDENCE", "/tmp/p0-g007-http/remaining-gaps.json")
)
SEED_SOURCE = Path(
    os.environ.get("P0_SEED_SOURCE", "/src/deploy/dev/seed/seed_demo.py")
)
BENCH_DIR = os.environ.get("BENCH_DIR", "/workspace/frappe-bench")
LEAD_NAME = "CRM-LEAD-00001"
CALL_NAME = "DEMO-CALL-003"
PRIVATE_FILE_NAME = f"DEMO-{LEAD_NAME}.txt"
SEED_MARKER = b"DEMO"
FIELD_NAME = "custom_cskh_sec16_restricted"
FIELD_SENTINEL = "RESTRICTED-SEC-16-SENTINEL"
SEED_USER = "staff.a@dev.local"
OUT_USER = "staff.c@dev.local"

failures: list[str] = []
cases: list[dict[str, object]] = []
field_created_by_probe = False
field_was_touched = False
field_original_value = None


def seed_password() -> str:
    tree = ast.parse(SEED_SOURCE.read_text(encoding="utf-8"), filename=str(SEED_SOURCE))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SEED_PASSWORD"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise RuntimeError("SEED_PASSWORD literal not found")


def new_client():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def send(client, method: str, path: str, body: bytes | None = None, headers=None):
    request_headers = {
        "Accept": "application/json, text/html",
        "X-Frappe-Site-Name": SITE_NAME,
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        BASE_URL + path, data=body, headers=request_headers, method=method
    )
    try:
        with client.open(request, timeout=20) as response:
            return (
                response.status,
                response.read(),
                dict(response.headers.items()),
                None,
            )
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items()), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, b"", {}, type(error).__name__


def login(username: str, password: str):
    client = new_client()
    body = urllib.parse.urlencode({"usr": username, "pwd": password}).encode()
    status, _, _, transport_error = send(
        client,
        "POST",
        "/api/method/login",
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if transport_error or status != 200:
        raise RuntimeError(f"fixture actor login failed ({status or transport_error})")
    return client


def csrf_token(client) -> str:
    status, body, _, error = send(client, "GET", "/cskh")
    match = re.search(rb'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)', body)
    if error or status != 200 or not match:
        raise RuntimeError("could not obtain CSRF token for authenticated probe")
    return match.group(1).decode("utf-8")


def setup_permlevel_field() -> bool:
    """Set a synthetic permlevel=1 value; HTTP responses below are the evidence."""
    global field_created_by_probe, field_was_touched, field_original_value
    try:
        import frappe
    except ImportError:
        return False

    previous_cwd = os.getcwd()
    sites_dir = os.path.join(BENCH_DIR, "sites")
    connected = False
    if os.path.isdir(sites_dir):
        os.chdir(sites_dir)
    try:
        frappe.init(site=SITE_NAME, sites_path=sites_dir)
        frappe.connect()
        connected = True
        frappe.set_user("Administrator")
        field = frappe.db.get_value(
            "Custom Field", {"dt": "CRM Lead", "fieldname": FIELD_NAME}, "permlevel"
        )
        if field is None:
            frappe.get_doc(
                {
                    "doctype": "Custom Field",
                    "dt": "CRM Lead",
                    "fieldname": FIELD_NAME,
                    "label": "CSKH P0 restricted field",
                    "fieldtype": "Data",
                    "insert_after": "first_name",
                    "permlevel": 1,
                }
            ).insert(ignore_permissions=True)
            field_created_by_probe = True
        elif int(field) != 1:
            return False
        else:
            field_original_value = frappe.db.get_value(
                "CRM Lead", LEAD_NAME, FIELD_NAME
            )
        frappe.clear_cache(doctype="CRM Lead")
        field_was_touched = True
        frappe.db.set_value("CRM Lead", LEAD_NAME, FIELD_NAME, FIELD_SENTINEL)
        frappe.db.commit()
        return frappe.db.get_value("CRM Lead", LEAD_NAME, FIELD_NAME) == FIELD_SENTINEL
    except Exception:
        try:
            if connected:
                frappe.db.rollback()
        except Exception:
            pass
        return False
    finally:
        if connected:
            try:
                frappe.destroy()
            except Exception:
                pass
        os.chdir(previous_cwd)


def cleanup_permlevel_field() -> bool:
    """Restore the seeded record and remove only a field created by this probe."""
    global field_created_by_probe, field_was_touched, field_original_value
    if not field_created_by_probe and not field_was_touched:
        return True

    try:
        import frappe
    except ImportError:
        return False

    previous_cwd = os.getcwd()
    sites_dir = os.path.join(BENCH_DIR, "sites")
    connected = False
    if os.path.isdir(sites_dir):
        os.chdir(sites_dir)
    try:
        frappe.init(site=SITE_NAME, sites_path=sites_dir)
        frappe.connect()
        connected = True
        frappe.set_user("Administrator")
        field_name = frappe.db.get_value(
            "Custom Field", {"dt": "CRM Lead", "fieldname": FIELD_NAME}, "name"
        )
        if field_created_by_probe and field_name:
            frappe.delete_doc("Custom Field", field_name, ignore_permissions=True)
            frappe.clear_cache(doctype="CRM Lead")
        elif field_name and field_was_touched:
            frappe.db.set_value("CRM Lead", LEAD_NAME, FIELD_NAME, field_original_value)
        frappe.db.commit()
        field_created_by_probe = False
        field_was_touched = False
        field_original_value = None
        return True
    except Exception:
        try:
            if connected:
                frappe.db.rollback()
        except Exception:
            pass
        return False
    finally:
        if connected:
            try:
                frappe.destroy()
            except Exception:
                pass
        os.chdir(previous_cwd)


def payload(body: bytes):
    try:
        result = json.loads(body.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def private_url_present(body: bytes) -> bool:
    return b"/private/files/" in body


def record(
    case_id: str,
    status: int | None,
    body: bytes,
    transport_error: str | None,
    expected: str,
    passed: bool,
    *,
    supplied_markers: tuple[str, ...] = (),
    sentinel_expected: bool | None = None,
    file_url_expected: bool | None = None,
    extra: dict[str, object] | None = None,
):
    parsed = payload(body)
    text = body.decode("utf-8", errors="replace")
    entry: dict[str, object] = {
        "id": case_id,
        "status": status,
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "exc_type": parsed.get("exc_type") if parsed else None,
        "transport_error": transport_error,
        "sentinel_present": FIELD_SENTINEL in text,
        "private_file_url_present": private_url_present(body),
        "identifier_echo": any(
            marker and marker in text for marker in supplied_markers
        ),
        "assertion": {"expected": expected, "result": "PASS" if passed else "FAIL"},
    }
    if sentinel_expected is not None:
        entry["sentinel_expected"] = sentinel_expected
    if file_url_expected is not None:
        entry["file_url_expected"] = file_url_expected
    if extra:
        entry.update(extra)
    if not passed or transport_error:
        failures.append(case_id)
    cases.append(entry)
    return entry


def response_case(case_id, response, predicate, expected, **kwargs):
    status, body, _, error = response
    passed = error is None and bool(predicate(status, body))
    return record(case_id, status, body, error, expected, passed, **kwargs)


def extract_file_record(client, filters):
    query = urllib.parse.urlencode(
        {
            "dt": "CRM Lead",
            "dn": LEAD_NAME,
            "filters": json.dumps(filters, separators=(",", ":")),
        }
    )
    return send(
        client,
        "GET",
        "/api/method/frappe.desk.form.load.get_filtered_attachments?" + query,
    )


def extract_names(body: bytes, *, get_data: bool):
    root = payload(body)
    message = root.get("message") if root else None
    if get_data:
        rows = message.get("data") if isinstance(message, dict) else None
    else:
        rows = message
    if not isinstance(rows, list):
        return None
    names = [
        row.get("name")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    ]
    return sorted(names)


def names_digest(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()


def nt04_create_doc_case(actor, actor_csrf, admin, admin_csrf, doctype: str):
    """Verify an out-of-scope actor cannot create a linked Note or Comment.

    A unique synthetic marker is used only in memory. The admin-side query checks
    whether the attempted insert created a row; any unexpected row is deleted and
    the query is repeated so the disposable site is left clean. Artifact output
    contains only the response summary and counts, never the marker or doc name.
    """
    marker = f"P0-NT04-{uuid.uuid4().hex}"
    if doctype == "Comment":
        doc = {
            "doctype": doctype,
            "comment_type": "Comment",
            "content": marker,
            "reference_doctype": "CRM Lead",
            "reference_name": LEAD_NAME,
        }
        marker_field = "content"
    elif doctype == "FCRM Note":
        doc = {
            "doctype": doctype,
            "title": marker,
            "content": "Synthetic NT-04 permission probe",
            "reference_doctype": "CRM Lead",
            "reference_docname": LEAD_NAME,
        }
        marker_field = "title"
    else:
        raise ValueError("NT-04 supports only linked Comment and FCRM Note")

    insert_body = json.dumps({"doc": doc}, separators=(",", ":")).encode("utf-8")
    insert_response = send(
        actor,
        "POST",
        "/api/method/frappe.client.insert",
        insert_body,
        {
            "Content-Type": "application/json",
            "X-Frappe-CSRF-Token": actor_csrf,
        },
    )
    insert_status, insert_response_body, _, insert_error = insert_response
    insert_payload = payload(insert_response_body)
    inserted_doc = insert_payload.get("message") if insert_payload else None
    inserted_name = (
        inserted_doc.get("name")
        if isinstance(inserted_doc, dict) and isinstance(inserted_doc.get("name"), str)
        else None
    )

    def find_matches():
        query = urllib.parse.urlencode(
            {
                "doctype": doctype,
                "fields": json.dumps(["name"], separators=(",", ":")),
                "filters": json.dumps(
                    [[doctype, marker_field, "=", marker]], separators=(",", ":")
                ),
                "limit_page_length": 20,
            }
        )
        response = send(admin, "GET", "/api/method/frappe.client.get_list?" + query)
        status, body, _, error = response
        names = extract_names(body, get_data=False)
        return response, names

    before_response, before_names = find_matches()
    before_status, before_body, _, before_error = before_response
    cleanup_ok = (
        before_error is None and before_status == 200 and before_names is not None
    )
    cleanup_names = set(before_names or [])
    if inserted_name:
        cleanup_names.add(inserted_name)

    admin_form_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Frappe-CSRF-Token": admin_csrf,
    }
    for name in sorted(cleanup_names):
        delete_response = send(
            admin,
            "POST",
            "/api/method/frappe.client.delete",
            urllib.parse.urlencode({"doctype": doctype, "name": name}).encode(),
            admin_form_headers,
        )
        delete_status, _, _, delete_error = delete_response
        if delete_error or delete_status != 200:
            cleanup_ok = False

    after_response, after_names = find_matches()
    after_status, after_body, _, after_error = after_response
    cleanup_ok = (
        cleanup_ok and after_error is None and after_status == 200 and after_names == []
    )
    insert_exc_type = (insert_payload or {}).get("exc_type")
    denied = (
        insert_error is None
        and insert_status == 403
        and isinstance(insert_exc_type, str)
        and insert_exc_type.endswith("PermissionError")
    )
    no_side_effect = (
        before_error is None and before_status == 200 and before_names == []
    )
    passed = denied and no_side_effect and cleanup_ok
    return record(
        f"nt04-create-{doctype.lower().replace(' ', '-')}-out-of-scope",
        insert_status,
        insert_response_body,
        insert_error,
        "out-of-scope insert is denied and creates no linked record",
        passed,
        supplied_markers=(LEAD_NAME, marker, inserted_name or ""),
        extra={
            "doctype": doctype,
            "linked_target": "synthetic seeded CRM Lead",
            "insert_exc_type": insert_exc_type,
            "matching_rows_before_cleanup": len(before_names)
            if before_names is not None
            else None,
            "cleanup_verified": cleanup_ok,
            "matching_rows_after_cleanup": len(after_names)
            if after_names is not None
            else None,
        },
    )


def tk04_create_task_out_of_scope_case(actor, actor_csrf, admin, admin_csrf):
    """Fail closed if a staff user can link a new ToDo to another team's Lead."""
    target_query = urllib.parse.urlencode(
        {
            "doctype": "CRM Lead",
            "fields": json.dumps(["name"], separators=(",", ":")),
            "filters": json.dumps({"lead_owner": OUT_USER}, separators=(",", ":")),
            "order_by": "name asc",
            "limit_page_length": 20,
        }
    )
    target_response = send(
        admin, "GET", "/api/method/frappe.client.get_list?" + target_query
    )
    target_status, target_body, _, target_error = target_response
    target_names = extract_names(target_body, get_data=False)
    target_lead = target_names[0] if target_names else None
    target_found = target_error is None and target_status == 200 and bool(target_lead)
    record(
        "tk04-out-of-scope-target-fixture",
        target_status,
        target_body,
        target_error,
        "Administrator resolves a seeded Lead owned by another team",
        target_found,
        supplied_markers=(target_lead or "",),
        extra={
            "target_found": target_found,
            "matching_target_count": len(target_names or []),
        },
    )
    if not target_found:
        record(
            "tk04-read-out-of-scope-target",
            None,
            b"",
            "TargetFixtureUnavailable",
            "out-of-scope Lead is denied to the staff actor",
            False,
        )
        record(
            "tk04-create-task-out-of-scope",
            None,
            b"",
            "TargetFixtureUnavailable",
            "out-of-scope linked Task insert is denied without creating a ToDo",
            False,
        )
        return None

    denied_read = send(
        actor,
        "GET",
        "/api/method/frappe.client.get?"
        + urllib.parse.urlencode({"doctype": "CRM Lead", "name": target_lead}),
    )
    read_status, read_body, _, read_error = denied_read
    read_text = read_body.decode("utf-8", errors="replace")
    read_denied = (
        read_error is None and read_status == 403 and target_lead not in read_text
    )
    record(
        "tk04-read-out-of-scope-target",
        read_status,
        read_body,
        read_error,
        "out-of-scope Lead is denied to the staff actor",
        read_denied,
        supplied_markers=(target_lead,),
        extra={"target_found_by_admin": target_found},
    )

    marker = f"P0-TK04-{uuid.uuid4().hex}"

    def find_matches():
        query = urllib.parse.urlencode(
            {
                "doctype": "ToDo",
                "fields": json.dumps(["name"], separators=(",", ":")),
                "filters": json.dumps({"description": marker}, separators=(",", ":")),
                "limit_page_length": 20,
            }
        )
        response = send(admin, "GET", "/api/method/frappe.client.get_list?" + query)
        status, body, _, error = response
        names = extract_names(body, get_data=False)
        return response, names, error is None and status == 200

    _, before_names, before_query_ok = find_matches()
    task_doc = {
        "doctype": "ToDo",
        "allocated_to": SEED_USER,
        "reference_type": "CRM Lead",
        "reference_name": target_lead,
        "description": marker,
        "status": "Open",
    }
    insert_response = send(
        actor,
        "POST",
        "/api/method/frappe.client.insert",
        json.dumps({"doc": task_doc}, separators=(",", ":")).encode("utf-8"),
        {
            "Content-Type": "application/json",
            "X-Frappe-CSRF-Token": actor_csrf,
        },
    )
    insert_status, insert_body, _, insert_error = insert_response
    insert_payload = payload(insert_body)
    inserted_doc = insert_payload.get("message") if insert_payload else None
    inserted_name = (
        inserted_doc.get("name")
        if isinstance(inserted_doc, dict) and isinstance(inserted_doc.get("name"), str)
        else None
    )
    _, after_names, after_query_ok = find_matches()
    delete_names = set(after_names or [])
    if inserted_name:
        delete_names.add(inserted_name)
    cleanup_ok = before_query_ok and after_query_ok and before_names == []
    for name in sorted(delete_names):
        delete_response = send(
            admin,
            "POST",
            "/api/method/frappe.client.delete",
            urllib.parse.urlencode({"doctype": "ToDo", "name": name}).encode(),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Frappe-CSRF-Token": admin_csrf,
            },
        )
        delete_status, _, _, delete_error = delete_response
        cleanup_ok = cleanup_ok and delete_error is None and delete_status == 200
    _, remaining_names, final_query_ok = find_matches()
    cleanup_ok = cleanup_ok and final_query_ok and remaining_names == []
    insert_exc_type = insert_payload.get("exc_type") if insert_payload else None
    body_text = insert_body.decode("utf-8", errors="replace")
    no_identifier_echo = target_lead not in body_text and marker not in body_text
    passed = (
        cleanup_ok
        and insert_error is None
        and insert_status == 403
        and isinstance(insert_exc_type, str)
        and insert_exc_type.endswith("PermissionError")
        and after_names == []
        and no_identifier_echo
    )
    record(
        "tk04-create-task-out-of-scope",
        insert_status,
        insert_body,
        insert_error,
        "out-of-scope linked Task insert is denied without creating a ToDo",
        passed,
        supplied_markers=(target_lead, marker, inserted_name or ""),
        extra={
            "target_found_by_admin": target_found,
            "matching_rows_before": len(before_names or []),
            "matching_rows_after_insert": len(after_names or []),
            "matching_rows_after_cleanup": len(remaining_names or []),
            "cleanup_verified": cleanup_ok,
        },
    )
    return target_lead


def ls04_search_link_case(
    actor, actor_csrf, lead_name: str, case_id: str, *, in_scope: bool
):
    """Probe the HTTP Link-field endpoint used by Frappe's get_link_options()."""
    request_body = json.dumps(
        {
            "doctype": "CRM Lead",
            "txt": lead_name,
            "page_length": 20,
            "searchfield": "name",
            "reference_doctype": "ToDo",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    response = send(
        actor,
        "POST",
        "/api/method/frappe.desk.search.search_link",
        request_body,
        {
            "Content-Type": "application/json",
            "X-Frappe-CSRF-Token": actor_csrf,
        },
    )
    status, body, _, error = response
    root = payload(body)
    results = root.get("message") if root else None
    values = [
        row.get("value")
        for row in results or []
        if isinstance(row, dict) and isinstance(row.get("value"), str)
    ]
    matched = lead_name in values
    text = body.decode("utf-8", errors="replace")
    passed = error is None and (
        (status == 200 and matched)
        if in_scope
        else status in (200, 403) and not matched and lead_name not in text
    )
    record(
        case_id,
        status,
        body,
        error,
        "Link-field lookup returns the in-scope Lead and omits the out-of-scope Lead",
        passed,
        supplied_markers=() if in_scope else (lead_name,),
        extra={"result_count": len(values), "target_match_present": matched},
    )


def setup_error_case(detail="synthetic permlevel field exists with sentinel"):
    record(
        "sec16-field-setup",
        None,
        b"",
        "setup-failed",
        detail,
        False,
    )


def run() -> int:
    started = datetime.now(timezone.utc).isoformat()
    password = seed_password()
    staff = login(SEED_USER, password)
    out_of_scope = login(OUT_USER, password)
    administrator_password = os.environ.get("ADMIN_PASSWORD", "")
    if not administrator_password:
        raise RuntimeError("ADMIN_PASSWORD unavailable")
    admin = login("Administrator", administrator_password)
    guest = new_client()
    staff_csrf = csrf_token(staff)
    out_of_scope_csrf = csrf_token(out_of_scope)
    admin_csrf = csrf_token(admin)

    if not setup_permlevel_field():
        setup_error_case()
    atexit.register(cleanup_permlevel_field)
    json_headers_staff = {
        "Content-Type": "application/json",
        "X-Frappe-CSRF-Token": staff_csrf,
    }

    # NT-04: prevent a non-owner from creating linked child records on the Lead.
    for linked_doctype in ("FCRM Note", "Comment"):
        nt04_create_doc_case(
            out_of_scope,
            out_of_scope_csrf,
            admin,
            admin_csrf,
            linked_doctype,
        )

    # TK-04 / LS-04: direct HTTP checks for task creation and Link-field lookup.
    out_of_scope_lead = tk04_create_task_out_of_scope_case(
        staff, staff_csrf, admin, admin_csrf
    )
    if out_of_scope_lead:
        ls04_search_link_case(
            staff,
            staff_csrf,
            LEAD_NAME,
            "ls04-search-link-in-scope-positive",
            in_scope=True,
        )
        ls04_search_link_case(
            staff,
            staff_csrf,
            out_of_scope_lead,
            "ls04-search-link-out-of-scope",
            in_scope=False,
        )
    else:
        for case_id in (
            "ls04-search-link-in-scope-positive",
            "ls04-search-link-out-of-scope",
        ):
            record(
                case_id,
                None,
                b"",
                "TargetFixtureUnavailable",
                "Link-field scope is checked with in-scope and out-of-scope controls",
                False,
            )

    # LEAD-15 / LS-05: cross-doctype calls must match ordinary Frappe get_list
    # visibility for the same actor. Only counts and opaque name-set digests leave memory.
    doctype_filters = {
        "CRM Deal": {},
        "ToDo": {},
        "CRM Call Log": {},
        "File": {"attached_to_name": LEAD_NAME},
        "User": {},
        "Custom Field": {"dt": "CRM Lead"},
    }
    for doctype, filters in doctype_filters.items():
        common = {"doctype": doctype, "filters": filters, "order_by": "name asc"}
        get_data_body = json.dumps(
            {**common, "rows": ["name"], "page_length": 100, "page_length_count": 100}
        ).encode("utf-8")
        get_data_response = send(
            staff,
            "POST",
            "/api/method/crm.api.doc.get_data",
            get_data_body,
            json_headers_staff,
        )
        list_query = urllib.parse.urlencode(
            {
                "doctype": doctype,
                "fields": json.dumps(["name"], separators=(",", ":")),
                "filters": json.dumps(filters, separators=(",", ":")),
                "order_by": "name asc",
                "limit_page_length": 100,
            }
        )
        get_list_response = send(
            staff, "GET", "/api/method/frappe.client.get_list?" + list_query
        )
        gd_status, gd_body, _, gd_error = get_data_response
        gl_status, gl_body, _, gl_error = get_list_response
        gd_names = extract_names(gd_body, get_data=True)
        gl_names = extract_names(gl_body, get_data=False)
        both_denied = gd_status == gl_status == 403
        same_rows = (
            gd_names is not None and gl_names is not None and gd_names == gl_names
        )
        passed = (
            gd_error is None
            and gl_error is None
            and ((both_denied) or (gd_status == gl_status == 200 and same_rows))
            and FIELD_SENTINEL.encode() not in gd_body
            and FIELD_SENTINEL.encode() not in gl_body
        )
        record(
            f"cross-doctype-get-data-vs-get-list-{doctype.lower().replace(' ', '-')}",
            gd_status,
            gd_body,
            gd_error,
            "same doctype permission boundary and visible name set as frappe.client.get_list",
            passed,
            extra={
                "control_status": gl_status,
                "get_data_row_count": len(gd_names) if gd_names is not None else None,
                "get_list_row_count": len(gl_names) if gl_names is not None else None,
                "get_data_names_sha256": names_digest(gd_names)
                if gd_names is not None
                else None,
                "get_list_names_sha256": names_digest(gl_names)
                if gl_names is not None
                else None,
                "same_visible_name_set": same_rows,
            },
        )

    # Resolve the seeded private file using the authorized standard attachment route.
    gallery_filters = [["File", "file_name", "=", PRIVATE_FILE_NAME]]
    gallery_response = extract_file_record(staff, gallery_filters)
    gallery_status, gallery_body, _, gallery_error = gallery_response
    gallery_payload = payload(gallery_body)
    gallery_rows = gallery_payload.get("message") if gallery_payload else None
    file_entry = next(
        (
            row
            for row in gallery_rows or []
            if isinstance(row, dict)
            and row.get("file_name") == PRIVATE_FILE_NAME
            and row.get("is_private")
            and isinstance(row.get("name"), str)
            and isinstance(row.get("file_url"), str)
            and row["file_url"].startswith("/private/files/")
        ),
        None,
    )
    if gallery_error or gallery_status != 200 or not file_entry:
        response_case(
            "sec26-in-scope-file-fixture",
            gallery_response,
            lambda status, body: False,
            "authorized gallery resolves the synthetic private File without persisting its id or URL",
            supplied_markers=(LEAD_NAME, PRIVATE_FILE_NAME),
        )
        file_id = ""
        file_url = ""
    else:
        file_id = file_entry["name"]
        file_url = file_entry["file_url"]
        record(
            "sec26-in-scope-file-fixture",
            gallery_status,
            gallery_body,
            gallery_error,
            "authorized gallery resolves the synthetic private File without persisting its id or URL",
            True,
            supplied_markers=(LEAD_NAME, PRIVATE_FILE_NAME, file_id, file_url),
            file_url_expected=True,
            extra={"matching_private_file_count": 1},
        )

    if file_id and file_url:
        # Residual standard Frappe routes: parent permission is enforced before
        # returning attachment metadata; only the in-scope caller may see the URL.
        for actor_name, actor in (("in-scope", staff), ("out-of-scope", out_of_scope)):
            filtered = extract_file_record(actor, gallery_filters)
            status, body, _, error = filtered
            root = payload(body)
            rows = root.get("message") if root else None
            has_expected_file = any(
                isinstance(row, dict) and row.get("name") == file_id
                for row in rows or []
            )
            has_url = private_url_present(body)
            passed = error is None and (
                (
                    actor_name == "in-scope"
                    and status == 200
                    and has_expected_file
                    and has_url
                )
                or (
                    actor_name == "out-of-scope"
                    and status == 403
                    and not has_url
                    and file_id not in body.decode("utf-8", errors="replace")
                    and LEAD_NAME not in body.decode("utf-8", errors="replace")
                )
            )
            record(
                f"sec26-get-filtered-attachments-{actor_name}",
                status,
                body,
                error,
                "in-scope gets only its private attachment; out-of-scope receives a scrubbed 403",
                passed,
                supplied_markers=(LEAD_NAME, PRIVATE_FILE_NAME, file_id, file_url),
                file_url_expected=actor_name == "in-scope",
                extra={"matching_private_file_present": has_expected_file},
            )

        # Direct docinfo and getdoc are separate surfaces from get_activities.
        doc_query = urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME})
        for route in ("get_docinfo", "getdoc"):
            for actor_name, actor in (
                ("in-scope", staff),
                ("out-of-scope", out_of_scope),
            ):
                response = send(
                    actor,
                    "GET",
                    f"/api/method/frappe.desk.form.load.{route}?{doc_query}",
                )
                status, body, _, error = response
                root = payload(body)
                docinfo = root.get("docinfo") if root else None
                if route == "getdoc" and isinstance(root, dict):
                    docinfo = root.get("docinfo")
                attachments = (
                    docinfo.get("attachments") if isinstance(docinfo, dict) else []
                )
                file_visible = any(
                    isinstance(row, dict) and row.get("name") == file_id
                    for row in attachments or []
                )
                has_url = private_url_present(body)
                text = body.decode("utf-8", errors="replace")
                passed = error is None and (
                    (
                        actor_name == "in-scope"
                        and status == 200
                        and file_visible
                        and has_url
                    )
                    or (
                        actor_name == "out-of-scope"
                        and status == 403
                        and not has_url
                        and file_id not in text
                        and LEAD_NAME not in text
                    )
                )
                record(
                    f"sec26-{route}-{actor_name}",
                    status,
                    body,
                    error,
                    "docinfo attachment is visible only after parent-document read permission",
                    passed,
                    supplied_markers=(LEAD_NAME, PRIVATE_FILE_NAME, file_id, file_url),
                    file_url_expected=actor_name == "in-scope",
                    extra={"matching_private_file_present": file_visible},
                )

        # Standard File listing must match its permission-filtered positive/negative controls.
        file_list_query = urllib.parse.urlencode(
            {
                "doctype": "File",
                "fields": json.dumps(
                    [
                        "name",
                        "file_name",
                        "file_url",
                        "attached_to_doctype",
                        "attached_to_name",
                    ],
                    separators=(",", ":"),
                ),
                "filters": json.dumps({"name": file_id}, separators=(",", ":")),
                "limit_page_length": 20,
            }
        )
        for actor_name, actor in (("in-scope", staff), ("out-of-scope", out_of_scope)):
            response = send(
                actor, "GET", "/api/method/frappe.client.get_list?" + file_list_query
            )
            status, body, _, error = response
            root = payload(body)
            rows = root.get("message") if root else None
            has_file = any(
                isinstance(row, dict) and row.get("name") == file_id
                for row in rows or []
            )
            has_url = private_url_present(body)
            text = body.decode("utf-8", errors="replace")
            passed = error is None and (
                (actor_name == "in-scope" and status == 200 and has_file and has_url)
                or (
                    actor_name == "out-of-scope"
                    and status in (200, 403)
                    and not has_file
                    and not has_url
                    and file_id not in text
                )
            )
            record(
                f"sec26-client-get-list-{actor_name}",
                status,
                body,
                error,
                "File list respects record scope; out-of-scope caller receives no record or URL",
                passed,
                supplied_markers=(LEAD_NAME, PRIVATE_FILE_NAME, file_id, file_url),
                file_url_expected=actor_name == "in-scope",
                extra={"matching_private_file_present": has_file},
            )

        # GET /private/files is the actual browser download hand-off (AT-06).
        download_path = urlsplit(file_url).path
        unknown_path = "/private/files/p0-missing-" + uuid.uuid4().hex + ".txt"
        downloads = {}
        for actor_name, actor, path in (
            ("in-scope", staff, download_path),
            ("out-of-scope", out_of_scope, download_path),
            ("guest", guest, download_path),
            ("out-of-scope-unknown", out_of_scope, unknown_path),
        ):
            response = send(actor, "GET", path, headers={"Accept": "*/*"})
            status, body, headers, error = response
            downloads[actor_name] = (status, body)
            passed = error is None and (
                (actor_name == "in-scope" and status == 200 and SEED_MARKER in body)
                or (
                    actor_name != "in-scope"
                    and status == 403
                    and SEED_MARKER not in body
                )
            )
            record(
                f"at06-private-download-{actor_name}",
                status,
                body,
                error,
                "only in-scope receives seeded bytes; guest/out-of-scope are denied",
                passed,
                supplied_markers=(LEAD_NAME, PRIVATE_FILE_NAME, file_id, file_url),
                extra={
                    "content_length": len(body),
                    "content_sha256": hashlib.sha256(body).hexdigest(),
                    "content_type": headers.get("Content-Type", ""),
                    "contains_seed_marker": SEED_MARKER in body,
                },
            )
        actual_status, actual_body = downloads.get("out-of-scope", (None, b""))
        unknown_status, unknown_body = downloads.get(
            "out-of-scope-unknown", (None, b"")
        )
        equivalent = actual_status == unknown_status and actual_body == unknown_body
        record(
            "sec26-private-download-out-of-scope-existing-vs-unknown",
            actual_status,
            actual_body,
            None,
            "existing and random private file paths are indistinguishable to out-of-scope caller",
            equivalent and actual_status == 403,
            extra={"indistinguishable_status_and_body": equivalent},
        )

    # CL-09: guest and out-of-scope denial, with an in-scope positive control.
    call_query = urllib.parse.urlencode({"name": CALL_NAME})
    for actor_name, actor in (
        ("guest", guest),
        ("out-of-scope", out_of_scope),
        ("in-scope", staff),
    ):
        response = send(
            actor,
            "GET",
            "/api/method/crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log?"
            + call_query,
        )
        status, body, _, error = response
        expected_status = 200 if actor_name == "in-scope" else 403
        record(
            f"cl09-get-call-log-{actor_name}",
            status,
            body,
            error,
            f"{actor_name} call-log access returns HTTP {expected_status}",
            error is None
            and status == expected_status
            and FIELD_SENTINEL.encode() not in body,
            supplied_markers=(CALL_NAME,),
        )

    # SEC-16: audit 12 real data surfaces for a live permlevel=1 sentinel.
    endpoint_specs = {
        "crm-get-data": (
            "POST",
            "/api/method/crm.api.doc.get_data",
            json.dumps(
                {
                    "doctype": "CRM Lead",
                    "filters": {"name": LEAD_NAME},
                    "order_by": "name asc",
                    "rows": ["name", FIELD_NAME],
                    "page_length": 20,
                }
            ).encode("utf-8"),
            True,
        ),
        "frappe-client-get": (
            "GET",
            "/api/method/frappe.client.get?"
            + urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME}),
            None,
            False,
        ),
        "rest-resource-get": (
            "GET",
            "/api/resource/CRM%20Lead/"
            + urllib.parse.quote(LEAD_NAME, safe="")
            + "?fields="
            + urllib.parse.quote(
                json.dumps(["name", FIELD_NAME], separators=(",", ":"))
            ),
            None,
            False,
        ),
        "desk-getdoc": (
            "GET",
            "/api/method/frappe.desk.form.load.getdoc?"
            + urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME}),
            None,
            False,
        ),
        "desk-get-docinfo": (
            "GET",
            "/api/method/frappe.desk.form.load.get_docinfo?"
            + urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME}),
            None,
            False,
        ),
        "frappe-client-get-list": (
            "GET",
            "/api/method/frappe.client.get_list?"
            + urllib.parse.urlencode(
                {
                    "doctype": "CRM Lead",
                    "fields": json.dumps(["name", FIELD_NAME], separators=(",", ":")),
                    "filters": json.dumps({"name": LEAD_NAME}, separators=(",", ":")),
                }
            ),
            None,
            False,
        ),
        "frappe-client-get-value": (
            "GET",
            "/api/method/frappe.client.get_value?"
            + urllib.parse.urlencode(
                {
                    "doctype": "CRM Lead",
                    "fieldname": json.dumps([FIELD_NAME], separators=(",", ":")),
                    "filters": LEAD_NAME,
                }
            ),
            None,
            False,
        ),
        "crm-activities": (
            "GET",
            "/api/method/crm.api.activities.get_activities?"
            + urllib.parse.urlencode({"name": LEAD_NAME}),
            None,
            False,
        ),
        "crm-linked-calls": (
            "GET",
            "/api/method/crm.api.activities.get_linked_calls?"
            + urllib.parse.urlencode({"name": LEAD_NAME}),
            None,
            False,
        ),
        "crm-linked-notes": (
            "GET",
            "/api/method/crm.api.activities.get_linked_notes?"
            + urllib.parse.urlencode({"name": LEAD_NAME}),
            None,
            False,
        ),
        "crm-linked-tasks": (
            "GET",
            "/api/method/crm.api.activities.get_linked_tasks?"
            + urllib.parse.urlencode({"name": LEAD_NAME}),
            None,
            False,
        ),
        "crm-linked-docs": (
            "GET",
            "/api/method/crm.api.doc.get_linked_docs_of_document?"
            + urllib.parse.urlencode({"doctype": "CRM Lead", "docname": LEAD_NAME}),
            None,
            False,
        ),
    }
    actors = (
        ("in-scope", staff, staff_csrf),
        ("out-of-scope", out_of_scope, out_of_scope_csrf),
    )
    for actor_name, actor, actor_token in actors:
        for endpoint_name, (method, path, body, is_json) in endpoint_specs.items():
            headers = {"X-Frappe-CSRF-Token": actor_token}
            if is_json:
                headers["Content-Type"] = "application/json"
            response = send(actor, method, path, body, headers)
            status, response_body, _, error = response
            allowed_status = (
                (status == 200) if actor_name == "in-scope" else status in (200, 403)
            )
            record(
                f"sec16-{endpoint_name}-{actor_name}",
                status,
                response_body,
                error,
                "non-system response does not contain permlevel=1 sentinel; route remains authorized/denied as scoped",
                error is None
                and allowed_status
                and FIELD_SENTINEL.encode() not in response_body,
                supplied_markers=(LEAD_NAME,),
                sentinel_expected=False,
            )

    # Positive controls prove the sentinel exists and the detector catches it.
    admin_endpoints = (
        (
            "frappe-client-get",
            "/api/method/frappe.client.get?"
            + urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME}),
        ),
        (
            "rest-resource-get",
            "/api/resource/CRM%20Lead/"
            + urllib.parse.quote(LEAD_NAME, safe="")
            + "?fields="
            + urllib.parse.quote(
                json.dumps(["name", FIELD_NAME], separators=(",", ":"))
            ),
        ),
        (
            "desk-getdoc",
            "/api/method/frappe.desk.form.load.getdoc?"
            + urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME}),
        ),
    )
    for endpoint_name, path in admin_endpoints:
        response = send(admin, "GET", path)
        status, body, _, error = response
        record(
            f"sec16-system-positive-control-{endpoint_name}",
            status,
            body,
            error,
            "System Manager positive control can read the restricted sentinel",
            error is None and status == 200 and FIELD_SENTINEL.encode() in body,
            supplied_markers=(LEAD_NAME,),
            sentinel_expected=True,
        )

    cleanup_ok = cleanup_permlevel_field()
    atexit.unregister(cleanup_permlevel_field)
    record(
        "sec16-field-cleanup",
        None,
        b"",
        None,
        "restore the seeded record and remove only the synthetic field created by this probe",
        cleanup_ok,
    )

    result = {
        "site": SITE_NAME,
        "head_sha": os.environ.get("P0_CI_HEAD_SHA", "unknown"),
        "merge_sha": os.environ.get("P0_CI_MERGE_SHA", "unknown"),
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "direct HTTP on seeded P0 site; cross-doctype permission parity, 12 permlevel surfaces, standard attachment surfaces, CL-09, AT-06, and NT-04 linked Note/Comment create denial",
        "case_count": len(cases),
        "cases": cases,
        "failed_cases": failures,
        "probe_result": "PASS" if not failures else "FAIL",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "probe_result": result["probe_result"],
                "case_count": len(cases),
                "failed_cases": failures,
            }
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
