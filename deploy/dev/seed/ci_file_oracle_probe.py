#!/usr/bin/env python3
"""Fail-closed HTTP characterization for the direct File existence oracle.

Runs only against the seeded fake-data P0 site. Authentication material, File
IDs, private URLs, and response bodies are used in memory and never serialized.
The 99-method security inventory remains unchanged; this is an auxiliary HTTP
gate for the two direct File endpoints changed by ``crm_cskh.errors``.
"""

from __future__ import annotations

import ast
import hashlib
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("P0_HTTP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = os.environ.get("P0_HTTP_SITE", "p0-ci.localhost")
OUTPUT = Path(
    os.environ.get("P0_FILE_ORACLE_EVIDENCE", "/tmp/p0-g007-http/file-oracle.json")
)
SEED_SOURCE = Path(
    os.environ.get("P0_SEED_SOURCE", "/src/deploy/dev/seed/seed_demo.py")
)
LEAD_NAME = "CRM-LEAD-00001"
PRIVATE_FILE_NAME = f"DEMO-{LEAD_NAME}.txt"
RANDOM_FILE_ID = f"p0-nonexistent-{uuid.uuid4().hex}"


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
    raise RuntimeError("SEED_PASSWORD literal not found in seed source")


def opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(client, method: str, path: str, data: bytes | None = None, headers=None):
    request_headers = {"Accept": "application/json", "X-Frappe-Site-Name": SITE_NAME}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=request_headers, method=method
    )
    try:
        with client.open(req, timeout=20) as response:
            return (
                response.status,
                response.read(),
                response.headers.get("Content-Type", ""),
            )
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get("Content-Type", "")
    except urllib.error.URLError:
        raise RuntimeError("HTTP transport failed; request details withheld") from None


def login(username: str):
    client = opener()
    body = urllib.parse.urlencode({"usr": username, "pwd": seed_password()}).encode()
    status, _, _ = request(
        client,
        "POST",
        "/api/method/login",
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200:
        raise RuntimeError(f"login failed for fixture actor ({status})")
    return client


def json_message(body: bytes):
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return None
    return payload.get("message") if isinstance(payload, dict) else None


def find_private_file(client) -> str:
    query = urllib.parse.urlencode({"doctype": "CRM Lead", "name": LEAD_NAME})
    status, body, _ = request(
        client, "GET", f"/api/method/crm_cskh.api.guarded_crm.get_attachments?{query}"
    )
    entries = json_message(body)
    if status != 200 or not isinstance(entries, list):
        raise RuntimeError("could not resolve the seeded private attachment")
    for entry in entries:
        if (
            isinstance(entry, dict)
            and entry.get("file_name") == PRIVATE_FILE_NAME
            and entry.get("is_private")
            and isinstance(entry.get("name"), str)
        ):
            return entry["name"]
    raise RuntimeError("seeded private attachment was not present")


def file_route(route: str, file_id: str) -> str:
    if route == "client-get":
        query = urllib.parse.urlencode({"doctype": "File", "name": file_id})
        return f"/api/method/frappe.client.get?{query}"
    if route == "rest-get":
        return f"/api/resource/File/{urllib.parse.quote(file_id, safe='')}"
    raise ValueError("unknown File route")


def body_fingerprint(status: int, body: bytes, supplied_ids: tuple[str, ...]):
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    return {
        "status": status,
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_keys": sorted(payload) if isinstance(payload, dict) else [],
        "exc_type": payload.get("exc_type") if isinstance(payload, dict) else None,
        "echoes_supplied_file_id": any(
            value and value in text for value in supplied_ids
        ),
        "contains_file_url": "/private/files/" in text,
    }


def run():
    started = datetime.now(timezone.utc).isoformat()
    staff = login("staff.a@dev.local")
    out_of_scope = login("staff.c@dev.local")
    file_id = find_private_file(staff)
    unknown_id = RANDOM_FILE_ID
    cases: list[dict[str, object]] = []
    failures: list[str] = []

    for route in ("client-get", "rest-get"):
        existing_status, existing_body, _ = request(
            out_of_scope, "GET", file_route(route, file_id)
        )
        unknown_status, unknown_body, _ = request(
            out_of_scope, "GET", file_route(route, unknown_id)
        )
        existing = body_fingerprint(
            existing_status, existing_body, (file_id, unknown_id)
        )
        unknown = body_fingerprint(unknown_status, unknown_body, (file_id, unknown_id))
        equivalent = (existing_status, existing_body) == (unknown_status, unknown_body)
        case_prefix = f"{route}-out-of-scope"
        cases.extend(
            [
                {"id": f"{case_prefix}-existing-file", **existing},
                {"id": f"{case_prefix}-unknown-id", **unknown},
                {
                    "id": f"{case_prefix}-indistinguishable",
                    "result": equivalent,
                    "expected_status": 404,
                },
            ]
        )
        if not (
            equivalent
            and existing_status == 404
            and unknown_status == 404
            and not existing["echoes_supplied_file_id"]
            and not unknown["echoes_supplied_file_id"]
            and not existing["contains_file_url"]
            and not unknown["contains_file_url"]
        ):
            failures.append(f"{route}-out-of-scope-existing-vs-unknown")

        positive_status, positive_body, _ = request(
            staff, "GET", file_route(route, file_id)
        )
        try:
            positive_document = json.loads(
                positive_body.decode("utf-8", errors="replace")
            )
        except ValueError:
            positive_document = None
        if isinstance(positive_document, dict):
            positive_payload = positive_document.get(
                "message"
            ) or positive_document.get("data")
        else:
            positive_payload = None
        positive_has_url = (
            isinstance(positive_payload, dict)
            and isinstance(positive_payload.get("file_url"), str)
            and positive_payload["file_url"].startswith("/private/files/")
        )
        cases.append(
            {
                "id": f"{route}-in-scope-positive",
                "status": positive_status,
                "body_length": len(positive_body),
                "body_sha256": hashlib.sha256(positive_body).hexdigest(),
                "private_file_url_present": positive_has_url,
            }
        )
        if positive_status != 200 or not positive_has_url:
            failures.append(f"{route}-in-scope-positive")

    payload = {
        "site": SITE_NAME,
        "head_sha": os.environ.get("P0_CI_HEAD_SHA", "unknown"),
        "merge_sha": os.environ.get("P0_CI_MERGE_SHA", "unknown"),
        "started_at_utc": started,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "direct File REST and frappe.client.get endpoints; response bodies and identifiers are not stored",
        "case_count": len(cases),
        "cases": cases,
        "failed_cases": failures,
        "probe_result": "PASS" if not failures else "FAIL",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "probe_result": payload["probe_result"],
                "case_count": len(cases),
                "failed_cases": failures,
            }
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
