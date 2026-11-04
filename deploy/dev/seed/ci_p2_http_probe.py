#!/usr/bin/env python3
"""Runtime HTTP transport probe for P2 workflow, security & non-regression (`P2.md` §15.3-§15.5).

Executes real HTTP requests (`urllib.request` with session cookies and `X-Frappe-CSRF-Token`)
against a live `bench serve` instance on a seeded site across all business actors:
  * `Guest`
  * `staff.a@dev.local`, `staff.b@dev.local` (Team 1)
  * `staff.c@dev.local` (Team 2)
  * `team1.leader@dev.local` (Leader A), `team2.leader@dev.local` (Leader B)
  * `bod.viewer@dev.local` (`CSKH BOD Viewer`)
  * `crm.admin@dev.local` (`CSKH CRM Admin`)

Cross-checks positive mutations against MariaDB SQL truth and verifies zero secrets,
cookies, CSRF tokens, private file URLs, or Vietnamese phone numbers reach the artifact.
"""

from __future__ import annotations

import ast
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("P2_HTTP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = os.environ.get("P2_HTTP_SITE", "p2-ci.localhost")
OUTPUT = Path(os.environ.get("P2_HTTP_EVIDENCE_DIR", "/tmp/p2-http"))
_CONTAINER_SEED_SOURCE = Path("/src/deploy/dev/seed/seed_demo.py")
SEED_SOURCE = Path(
    os.environ.get(
        "P2_SEED_SOURCE",
        str(
            _CONTAINER_SEED_SOURCE
            if _CONTAINER_SEED_SOURCE.exists()
            else Path(__file__).resolve().with_name("seed_demo.py")
        ),
    )
)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

VALID_RECORD_ROLES = ("assertion", "setup_observation")

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
PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")

records: list[dict[str, object]] = []
failures: list[str] = []


def seed_password_from_source(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SEED_PASSWORD"
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str) and value:
                return value
    raise RuntimeError("SEED_PASSWORD literal not found in seed source")


SEED_PASSWORD = seed_password_from_source(SEED_SOURCE)


def _secret_variants() -> list[str]:
    variants: list[str] = []
    for raw in (ADMIN_PASSWORD, SEED_PASSWORD):
        if not raw:
            continue
        for candidate in (
            raw,
            urllib.parse.quote(raw, safe=""),
            urllib.parse.quote_plus(raw),
        ):
            if candidate and candidate not in variants:
                variants.append(candidate)
    return variants


SECRET_VARIANTS = _secret_variants()


def redact_text(value: str) -> str:
    for secret in SECRET_VARIANTS:
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)(pwd=)[^&\s\"']+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(password=)[^&\s\"']+", r"\1[REDACTED]", value)
    value = re.sub(
        r'(?i)(["\']X-Frappe-CSRF-Token["\']\s*:\s*["\'])[^"\']+',
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"(?i)(csrf[_-]?token[\"'=:\s]+)[^\"'&\s<]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(sid[\"'=:\s]+)[^\"'&\s<;]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(/private/files/)[^\"'\s,}]+", r"\1[REDACTED_FILE_URL]", value)
    value = re.sub(r"(?i)(/files/)[^\"'\s,}]+", r"\1[REDACTED_FILE_URL]", value)
    value = PHONE_RE.sub("[REDACTED_PHONE]", value)
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


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def summarize_probe_records(
    records_list: list[dict[str, object]],
    explicit_failures: list[str] | tuple[str, ...] = (),
) -> dict[str, object]:
    """Classify recorded HTTP entries and enforce 100% assertion completeness on verification cases."""
    seen_ids: set[str] = set()
    duplicate_case_ids: list[str] = []
    invalid_role_cases: list[str] = []
    setup_transport_error_cases: list[str] = []
    unasserted_cases: list[str] = []
    failed_assertion_cases: list[str] = []

    verification_records = 0
    setup_observation_records = 0
    asserted_cases = 0
    passed_assertions = 0
    failed_assertions = 0

    for index, entry in enumerate(records_list):
        raw_id = entry.get("id") if isinstance(entry, dict) else None
        case_id = (
            raw_id.strip()
            if isinstance(raw_id, str) and raw_id.strip()
            else f"<unnamed-{index}>"
        )
        if case_id in seen_ids:
            _append_unique(duplicate_case_ids, case_id)
        seen_ids.add(case_id)

        role = entry.get("record_role") if isinstance(entry, dict) else None
        if role not in VALID_RECORD_ROLES:
            _append_unique(invalid_role_cases, case_id)
            continue

        if role == "setup_observation":
            setup_observation_records += 1
            if entry.get("transport_error") is not None:
                _append_unique(setup_transport_error_cases, case_id)
            if entry.get("assertion") is not None:
                _append_unique(invalid_role_cases, case_id)
            continue

        verification_records += 1
        assertion = entry.get("assertion")
        has_valid_verdict = (
            isinstance(assertion, dict)
            and assertion.get("result") in ("PASS", "FAIL")
            and isinstance(assertion.get("expected"), str)
            and bool(assertion["expected"].strip())
        )
        if not has_valid_verdict:
            _append_unique(unasserted_cases, case_id)
            continue

        asserted_cases += 1
        assert isinstance(assertion, dict)
        if assertion.get("result") == "PASS" and entry.get("transport_error") is None:
            passed_assertions += 1
        else:
            failed_assertions += 1
            _append_unique(failed_assertion_cases, case_id)

    gate_failures: list[str] = []
    for item in explicit_failures:
        _append_unique(gate_failures, str(item))
    for cid in failed_assertion_cases:
        _append_unique(gate_failures, cid)
    for cid in unasserted_cases:
        _append_unique(gate_failures, f"unasserted:{cid}")
    for cid in duplicate_case_ids:
        _append_unique(gate_failures, f"duplicate_id:{cid}")
    for cid in invalid_role_cases:
        _append_unique(gate_failures, f"invalid_role:{cid}")
    for cid in setup_transport_error_cases:
        _append_unique(gate_failures, f"setup_transport_error:{cid}")
    if verification_records == 0 or asserted_cases == 0:
        _append_unique(gate_failures, "no_asserted_verification_cases")

    probe_ok = (
        not gate_failures
        and verification_records > 0
        and asserted_cases == verification_records
        and passed_assertions == asserted_cases
        and failed_assertions == 0
        and not unasserted_cases
    )
    return {
        "counts": {
            "total_records": len(records_list),
            "verification_records": verification_records,
            "setup_observation_records": setup_observation_records,
            "asserted_cases": asserted_cases,
            "unasserted_cases": len(unasserted_cases),
            "passed_assertions": passed_assertions,
            "failed_assertions": failed_assertions,
        },
        "unasserted_cases": unasserted_cases,
        "failed_assertion_cases": failed_assertion_cases,
        "duplicate_case_ids": duplicate_case_ids,
        "gate_failures": gate_failures,
        "probe_result": "PASS" if probe_ok else "FAIL",
    }


def write_evidence(*, final: bool = False) -> dict[str, object]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summary = summarize_probe_records(records, failures)
    counts = summary["counts"]
    assert isinstance(counts, dict)
    payload = {
        "site": SITE_NAME,
        "base_url": BASE_URL,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
        "evidence_class": "http",
        "coverage_scope": (
            "direct HTTP against bench serve with session cookies and CSRF validation across "
            "P2 workflow.*, settings.*, P1 ownership.reassign handover, generic /api/resource/* & "
            "frappe.client.* guards, BOD 0-PII summary, and P0 File-oracle 404 non-regression"
        ),
        "total_cases": counts["total_records"],
        "counts": counts,
        "cases": records,
        "unasserted_cases": summary["unasserted_cases"],
        "failed_cases": summary["failed_assertion_cases"],
        "gate_failures": summary["gate_failures"],
        "probe_result": summary["probe_result"] if final else "IN_PROGRESS",
    }
    (OUTPUT / "p2-http-transport.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


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
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    if record_role not in VALID_RECORD_ROLES:
        raise ValueError(f"Unsupported record_role: {record_role!r}")
    url = BASE_URL + path
    request_headers = {
        "Accept": "application/json",
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
        with opener.open(req, timeout=60) as response:
            status = response.status
            response_headers = dict(response.headers.items())
            response_text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = dict(error.headers.items()) if error.headers else {}
        response_text = error.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        transport_error = type(error).__name__

    try:
        parsed_response: object = json.loads(response_text) if response_text else None
    except json.JSONDecodeError:
        parsed_response = None
    safe_body = (
        json.dumps(sanitize(parsed_response), ensure_ascii=False)
        if parsed_response is not None
        else redact_text(response_text)[:600]
    )
    for key in list(response_headers):
        if key.lower() in {"set-cookie", "authorization", "x-frappe-csrf-token"}:
            response_headers[key] = "[REDACTED]"

    entry: dict[str, object] = {
        "id": case_id,
        "record_role": record_role,
        "evidence_class": "http" if record_role == "assertion" else "setup_observation",
        "request": {
            "method": method,
            "url": redact_text(url),
            "headers": {
                key: ("[REDACTED]" if key.lower() == "x-frappe-csrf-token" else value)
                for key, value in request_headers.items()
                if key.lower()
                in {
                    "accept",
                    "content-type",
                    "x-frappe-site-name",
                    "x-frappe-csrf-token",
                }
            },
            "cookie": "[REDACTED]",
            "body": redact_text(body.decode("utf-8", errors="replace")) if body else "",
        },
        "response": {
            "status": status,
            "headers": sanitize(response_headers),
            "body": safe_body,
            "exc_type": parsed_response.get("exc_type")
            if isinstance(parsed_response, dict)
            else None,
            "error_code": parsed_response.get("code")
            if isinstance(parsed_response, dict)
            else None,
            "p1_code": parsed_response.get("p1_code")
            if isinstance(parsed_response, dict)
            else None,
        },
        "transport_error": transport_error,
    }
    if record:
        records.append(entry)
        write_evidence(final=False)
    return {
        "status": status,
        "json": parsed_response,
        "text": response_text,
        "entry": entry,
        "transport_error": transport_error,
    }


def body_payload(response: dict[str, object]) -> dict:
    data = response.get("json")
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        return data["message"]
    return data if isinstance(data, dict) else {}


def error_envelope(response: dict[str, object]) -> dict:
    data = response.get("json")
    return data if isinstance(data, dict) else {}


def check(
    case_id: str,
    response: dict[str, object],
    condition: bool,
    detail: str,
    actor: str = "",
) -> bool:
    entry = response.get("entry")
    if isinstance(entry, dict) and entry.get("record_role") == "setup_observation":
        raise ValueError(
            f"Cannot attach check() assertion to setup_observation record {case_id!r}"
        )
    passed = bool(condition) and response.get("transport_error") is None
    if isinstance(entry, dict):
        entry["assertion"] = {
            "expected": redact_text(detail),
            "actor": actor,
            "result": "PASS" if passed else "FAIL",
        }
    if not passed:
        _append_unique(failures, case_id)
    print(f"{case_id}: {'PASS' if passed else 'FAIL'}")
    return passed


def login(label: str, username: str, password: str) -> urllib.request.OpenerDirector:
    opener, _ = make_opener()
    body = urllib.parse.urlencode({"usr": username, "pwd": password}).encode()
    case_id = f"p2-http-login-{label}"
    response = request(
        case_id,
        opener,
        "POST",
        "/api/method/login",
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    check(
        case_id,
        response,
        response["status"] == 200 and isinstance(response["json"], dict),
        "POST /api/method/login returns HTTP 200",
        actor=username,
    )
    return opener


def csrf_token(
    label: str, opener: urllib.request.OpenerDirector, actor: str, path: str = "/app"
) -> str:
    case_id = f"p2-http-csrf-{label}"
    page = request(case_id, opener, "GET", path)
    match = re.search(
        r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)["\']', str(page["text"])
    )
    if not match:
        match = re.search(r'csrf_token\s*[:=]\s*["\']([^"\']+)["\']', str(page["text"]))
    token = match.group(1) if match else ""
    check(
        case_id,
        page,
        page["status"] == 200 and bool(token),
        "GET /app yields HTTP 200 and a non-empty CSRF token",
        actor=actor,
    )
    return token


def post(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    token: str,
    path: str,
    payload: dict[str, object],
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(
        case_id,
        opener,
        "POST",
        path,
        body,
        headers,
        record=record,
        record_role=record_role,
    )


def post_json(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    token: str,
    path: str,
    payload: dict[str, object],
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(
        case_id,
        opener,
        "POST",
        path,
        body,
        headers,
        record=record,
        record_role=record_role,
    )


def put_json(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    token: str,
    path: str,
    payload: dict[str, object],
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(
        case_id,
        opener,
        "PUT",
        path,
        body,
        headers,
        record=record,
        record_role=record_role,
    )


def patch_json(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    token: str,
    path: str,
    payload: dict[str, object],
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(
        case_id,
        opener,
        "PATCH",
        path,
        body,
        headers,
        record=record,
        record_role=record_role,
    )


def get(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    path: str,
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    return request(
        case_id,
        opener,
        "GET",
        path,
        record=record,
        record_role=record_role,
    )


def fetch_close_work_item_db_state(
    frappe, work_item_name: str, lead_name: str
) -> dict[str, object]:
    """Query MariaDB ground truth for a closed `CSKH Work Item` and its linked invariants."""
    wi_row = frappe.db.get_value(
        "CSKH Work Item",
        work_item_name,
        [
            "name",
            "lead",
            "status",
            "assigned_to",
            "assigned_at",
            "open_callback_task",
            "next_action_due",
            "closed_at",
            "closed_by",
            "completion_reason",
            "revision",
        ],
        as_dict=True,
    )
    open_callback_count = int(
        frappe.db.sql(
            """
            select count(*)
            from `tabCRM Task`
            where cskh_work_item = %s
              and coalesce(cskh_is_followup, 0) = 1
              and status in ('Backlog', 'Todo', 'In Progress')
            """,
            (work_item_name,),
        )[0][0]
        or 0
    )
    active_claim_for_work_item = int(
        frappe.db.sql(
            "select count(*) from `tabCSKH Active Claim` where work_item = %s",
            (work_item_name,),
        )[0][0]
        or 0
    )
    active_claim_for_lead = int(
        frappe.db.sql(
            "select count(*) from `tabCSKH Active Claim` where lead = %s",
            (lead_name,),
        )[0][0]
        or 0
    )
    open_interval_count = int(
        frappe.db.sql(
            """
            select count(*)
            from `tabCSKH Assignment History`
            where work_item = %s
              and ownership_end is null
            """,
            (work_item_name,),
        )[0][0]
        or 0
    )
    close_events = frappe.db.sql(
        """
        select actor, ownership_start, ownership_end
        from `tabCSKH Assignment History`
        where work_item = %s
          and action = 'close'
        order by creation desc, name desc
        """,
        (work_item_name,),
        as_dict=True,
    )
    lead_owner = frappe.db.get_value("CRM Lead", lead_name, "lead_owner")
    open_lead_todo_count = int(
        frappe.db.sql(
            """
            select count(*)
            from `tabToDo`
            where reference_type = 'CRM Lead'
              and reference_name = %s
              and status = 'Open'
            """,
            (lead_name,),
        )[0][0]
        or 0
    )
    return {
        "work_item": dict(wi_row) if wi_row else None,
        "open_callback_count": open_callback_count,
        "active_claim_for_work_item": active_claim_for_work_item,
        "active_claim_for_lead": active_claim_for_lead,
        "open_interval_count": open_interval_count,
        "close_events": [dict(row) for row in close_events],
        "lead_owner": lead_owner,
        "open_lead_todo_count": open_lead_todo_count,
    }


def verify_close_work_item_outcome(
    response: dict[str, object],
    db_state: dict[str, object] | None,
    *,
    expected_work_item: str,
    expected_actor: str,
    expected_reason: str | None = None,
    min_revision: int = 1,
) -> tuple[bool, str]:
    """Validate `workflow.close_work_item` HTTP contract (§4.10) + MariaDB invariant truth."""
    violations: list[str] = []

    if response.get("transport_error") is not None:
        violations.append(f"transport_error={response.get('transport_error')}")
    if response.get("status") != 200:
        violations.append(f"http_status={response.get('status')}")

    raw_json = response.get("json")
    if not isinstance(raw_json, dict) or not isinstance(raw_json.get("message"), dict):
        violations.append("missing_json_message_envelope")
    payload = body_payload(response)

    if payload.get("status") != "closed":
        violations.append(f"payload.status={payload.get('status')!r}")
    if payload.get("idempotent_replay") is not False:
        violations.append(
            f"payload.idempotent_replay={payload.get('idempotent_replay')!r}"
        )
    if payload.get("work_item") != expected_work_item:
        violations.append(f"payload.work_item={payload.get('work_item')!r}")
    if payload.get("work_item_status") != "closed":
        violations.append(
            f"payload.work_item_status={payload.get('work_item_status')!r}"
        )
    if payload.get("closed_by") != expected_actor:
        violations.append(f"payload.closed_by={payload.get('closed_by')!r}")

    closed_at_val = payload.get("closed_at")
    if not isinstance(closed_at_val, str) or not closed_at_val.strip():
        violations.append("payload.closed_at_empty")

    if "canceled_followup_task" not in payload or not (
        payload.get("canceled_followup_task") is None
        or isinstance(payload.get("canceled_followup_task"), str)
    ):
        violations.append("payload.canceled_followup_task_invalid")

    rev_val = payload.get("revision")
    valid_rev = (
        not isinstance(rev_val, bool)
        and isinstance(rev_val, int)
        and rev_val >= min_revision
    )
    if not valid_rev:
        violations.append(f"payload.revision={rev_val!r}<{min_revision}")

    if not isinstance(db_state, dict):
        violations.append("db_state_missing")
    else:
        wi = db_state.get("work_item")
        if not isinstance(wi, dict):
            violations.append("db.work_item_missing")
        else:
            if wi.get("status") != "closed":
                violations.append(f"db.wi.status={wi.get('status')!r}")
            if wi.get("assigned_to") not in (None, ""):
                violations.append(f"db.wi.assigned_to={wi.get('assigned_to')!r}")
            if wi.get("assigned_at") is not None:
                violations.append("db.wi.assigned_at_not_null")
            if wi.get("open_callback_task") not in (None, ""):
                violations.append(
                    f"db.wi.open_callback_task={wi.get('open_callback_task')!r}"
                )
            if wi.get("next_action_due") is not None:
                violations.append("db.wi.next_action_due_not_null")
            if wi.get("closed_by") != expected_actor:
                violations.append(f"db.wi.closed_by={wi.get('closed_by')!r}")
            if not wi.get("closed_at"):
                violations.append("db.wi.closed_at_empty")
            if (
                expected_reason is not None
                and wi.get("completion_reason") != expected_reason
            ):
                violations.append(
                    f"db.wi.completion_reason={wi.get('completion_reason')!r}"
                )
            if valid_rev and int(wi.get("revision") or 0) != rev_val:
                violations.append(
                    f"db.wi.revision={wi.get('revision')!r}!=payload.revision={rev_val!r}"
                )

        if int(db_state.get("open_callback_count", -1)) != 0:
            violations.append(
                f"db.open_callback_count={db_state.get('open_callback_count')!r}"
            )
        if int(db_state.get("active_claim_for_work_item", -1)) != 0:
            violations.append(
                f"db.active_claim_for_work_item={db_state.get('active_claim_for_work_item')!r}"
            )
        if int(db_state.get("active_claim_for_lead", -1)) != 0:
            violations.append(
                f"db.active_claim_for_lead={db_state.get('active_claim_for_lead')!r}"
            )
        if int(db_state.get("open_interval_count", -1)) != 0:
            violations.append(
                f"db.open_interval_count={db_state.get('open_interval_count')!r}"
            )

        close_events = db_state.get("close_events")
        has_close_event = isinstance(close_events, list) and any(
            isinstance(ev, dict)
            and ev.get("actor") == expected_actor
            and ev.get("ownership_end") is not None
            for ev in close_events
        )
        if not has_close_event:
            violations.append("db.close_history_event_missing")

        if db_state.get("lead_owner") not in (None, ""):
            violations.append(f"db.lead_owner={db_state.get('lead_owner')!r}")
        if int(db_state.get("open_lead_todo_count", -1)) != 0:
            violations.append(
                f"db.open_lead_todo_count={db_state.get('open_lead_todo_count')!r}"
            )

    if violations:
        return (
            False,
            f"POST close_work_item failed for {expected_work_item}: "
            + ", ".join(violations),
        )
    return (
        True,
        f"POST close_work_item returns HTTP 200 contract status=closed and DB confirms "
        f"{expected_work_item} closed with 0 open callbacks, 0 active claims, and 0 open intervals",
    )


def run_close_work_item_case(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    token: str,
    *,
    work_item: str,
    lead: str,
    actor: str,
    completion_reason: str,
    idempotency_key: str,
    db_state_fetcher: Callable[[str, str], dict[str, object]],
    min_revision: int = 1,
) -> dict[str, object]:
    """Execute `workflow.close_work_item` over HTTP and check both contract payload & DB invariants."""
    response = post_json(
        case_id,
        opener,
        token,
        "/api/method/crm_cskh.api.workflow.close_work_item",
        {
            "work_item": work_item,
            "completion_reason": completion_reason,
            "idempotency_key": idempotency_key,
        },
    )
    db_state: dict[str, object] | None = None
    db_fetch_error: str | None = None
    try:
        db_state = db_state_fetcher(work_item, lead)
    except Exception as error:
        db_fetch_error = type(error).__name__
    ok, detail = verify_close_work_item_outcome(
        response,
        db_state,
        expected_work_item=work_item,
        expected_actor=actor,
        expected_reason=completion_reason,
        min_revision=min_revision,
    )
    if db_fetch_error is not None:
        ok = False
        detail = f"{detail}; db_fetch_error={db_fetch_error}"
    check(case_id, response, ok, detail, actor)
    return response


def _with_frappe(fn):
    import frappe

    bench_dir = os.environ.get("BENCH_DIR", "/workspace/frappe-bench")
    sites_path = os.environ.get("SITES_PATH", os.path.join(bench_dir, "sites"))
    if os.path.isdir(sites_path):
        os.chdir(sites_path)
    frappe.init(site=SITE_NAME, sites_path=sites_path, force=True)
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        return fn(frappe)
    finally:
        frappe.destroy()


def setup_probe_fixtures() -> dict[str, str]:
    def _create(frappe):
        from frappe.utils import add_to_date, now_datetime

        from crm_cskh.services import identity as identity_service
        from crm_cskh.services import ownership, policies
        from crm_cskh.services.phone_identity import normalize_vn_phone

        def fresh_phone() -> str:
            while True:
                digits = f"{int(frappe.generate_hash(length=10), 16) % 80000000:08d}"
                cand = f"09{digits}"[:10]
                canon = normalize_vn_phone(cand)
                if not frappe.db.exists(
                    "CRM Lead", {"mobile_no": ["in", (cand, canon)]}
                ) and not frappe.db.exists(
                    "CSKH Identity Index", {"normalized_phone": canon}
                ):
                    return cand

        def make_lead(label: str) -> str:
            ph = fresh_phone()
            doc = frappe.get_doc(
                {
                    "doctype": "CRM Lead",
                    "first_name": f"Khách HTTP P2 {label}",
                    "lead_name": f"Khách HTTP P2 {label}",
                    "mobile_no": ph,
                    "status": "New",
                }
            ).insert(ignore_permissions=True)
            identity_service.claim_canonical_identity(normalize_vn_phone(ph), doc.name)
            return doc.name

        team1_node = frappe.db.get_value(
            "CRM Sales Hierarchy", {"user": "team1.leader@dev.local"}, "name"
        )
        team2_node = frappe.db.get_value(
            "CRM Sales Hierarchy", {"user": "team2.leader@dev.local"}, "name"
        )

        camp_t1 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P2H-T1-{secrets.token_hex(3).upper()}",
                "title": "Chiến dịch HTTP Team 1",
                "status": "Active",
                "team_scope": team1_node,
            }
        ).insert(ignore_permissions=True)
        camp_t2 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P2H-T2-{secrets.token_hex(3).upper()}",
                "title": "Chiến dịch HTTP Team 2",
                "status": "Active",
                "team_scope": team2_node,
            }
        ).insert(ignore_permissions=True)

        for u in ("staff.a@dev.local", "staff.b@dev.local"):
            frappe.get_doc(
                {
                    "doctype": "CSKH Assignment Rule",
                    "campaign": camp_t1.name,
                    "user": u,
                    "weight": 50,
                    "capacity_limit": 20,
                    "version": 1,
                    "enabled": 1,
                }
            ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Assignment Rule",
                "campaign": camp_t2.name,
                "user": "staff.c@dev.local",
                "weight": 100,
                "capacity_limit": 20,
                "version": 1,
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)

        def assign_item(camp: str, lead_name: str, target: str) -> str:
            wi = frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": camp,
                    "lead": lead_name,
                    "status": "queued",
                }
            ).insert(ignore_permissions=True)
            frappe.get_doc(
                {
                    "doctype": "CSKH Active Claim",
                    "lead": lead_name,
                    "campaign": camp,
                    "work_item": wi.name,
                }
            ).insert(ignore_permissions=True)
            ownership.assign(
                wi.name,
                target,
                actor="crm.admin@dev.local",
                reason="Seed P2 HTTP probe",
                request_id=f"p2h-assign-{secrets.token_hex(5)}",
            )
            return wi.name

        lead_a1 = make_lead("A1")
        lead_b1 = make_lead("B1")
        lead_c1 = make_lead("C1")
        wi_a1 = assign_item(camp_t1.name, lead_a1, "staff.a@dev.local")
        wi_b1 = assign_item(camp_t1.name, lead_b1, "staff.b@dev.local")
        wi_c1 = assign_item(camp_t2.name, lead_c1, "staff.c@dev.local")

        # Unlinked native `CRM Call Log` on `lead_a1`
        phone_a1 = frappe.db.get_value("CRM Lead", lead_a1, "mobile_no")
        cl_a1 = frappe.get_doc(
            {
                "doctype": "CRM Call Log",
                "id": f"P2HCL-{secrets.token_hex(5)}",
                "type": "Outgoing",
                "status": "Completed",
                "from": "+842873001101",
                "to": phone_a1,
                "duration": 52,
                "start_time": add_to_date(now_datetime(), minutes=-3),
                "end_time": add_to_date(now_datetime(), minutes=-2),
                "caller": "staff.a@dev.local",
                "reference_doctype": "CRM Lead",
                "reference_docname": lead_a1,
            }
        ).insert(ignore_permissions=True)

        # Team 2 scoped Policy for generic `/api/resource/CSKH Policy` isolation test (idempotent on re-runs)
        existing_pol_t2 = frappe.db.get_value(
            "CSKH Policy",
            {"scope_type": "Team", "team": team2_node, "enabled": 1},
            "policy_code",
        )
        pol_t2_code = str(existing_pol_t2 or f"POL-T2-{secrets.token_hex(3).upper()}")
        if not existing_pol_t2:
            policies.save_policy(
                policy_code=pol_t2_code,
                title="Chính sách riêng Team 2",
                scope_type="Team",
                team=team2_node,
                idempotency_key=f"p2h-pol-{secrets.token_hex(4)}",
                actor="crm.admin@dev.local",
            )

        # Private File attached to `lead_a1` for P0 File-oracle 404 non-regression test
        secret_fname = f"p2h-secret-{secrets.token_hex(6)}.txt"
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": secret_fname,
                "content": b"Tai lieu noi bo nhay cam Team 1",
                "is_private": 1,
                "attached_to_doctype": "CRM Lead",
                "attached_to_name": lead_a1,
            }
        ).insert(ignore_permissions=True)

        # Dedicated T1C remediation fixtures (`F-P2-01`, `F-P2-02`, `F-P2-04`, `F-P2-05`, `F-P2-06`)
        from crm_cskh.services import contact_attempt as ca_service

        camp_f06 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P2H-F06-{secrets.token_hex(3).upper()}",
                "title": "Chiến dịch HTTP F-P2-01 & F-P2-06",
                "status": "Active",
                "team_scope": team1_node,
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Assignment Rule",
                "campaign": camp_f06.name,
                "user": "staff.a@dev.local",
                "weight": 100,
                "capacity_limit": 20,
                "version": 1,
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)
        lead_dnc_http = make_lead("DNC-F06")
        wi_dnc_http = assign_item(camp_f06.name, lead_dnc_http, "staff.a@dev.local")

        camp_f02 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P2H-F02-{secrets.token_hex(3).upper()}",
                "title": "Chiến dịch HTTP F-P2-02 & F-P2-04",
                "status": "Active",
                "team_scope": team1_node,
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Assignment Rule",
                "campaign": camp_f02.name,
                "user": "staff.a@dev.local",
                "weight": 100,
                "capacity_limit": 20,
                "version": 1,
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)
        run_f02 = frappe.get_doc(
            {
                "doctype": "CSKH Distribution Run",
                "campaign": camp_f02.name,
                "status": "completed",
                "rule_version": 1,
                "selection_fingerprint": f"fp-p2h-{secrets.token_hex(4)}",
                "idempotency_key": f"idem-p2h-{secrets.token_hex(4)}",
                "progress": 3,
                "preview_json": json.dumps(
                    {
                        "eligible_count": 3,
                        "excluded_count": 0,
                        "targets": [
                            {"user": "staff.a@dev.local", "weight": 100, "planned": 3}
                        ],
                        "simulated": {"staff.a@dev.local": 3},
                        "excluded_assignees": [],
                    }
                ),
                "actual_json": None,
            }
        ).insert(ignore_permissions=True)

        def alloc_run_item(label: str) -> str:
            ld = make_lead(label)
            wi_doc = frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": camp_f02.name,
                    "lead": ld,
                    "status": "queued",
                    "allocation_run": run_f02.name,
                }
            ).insert(ignore_permissions=True)
            frappe.get_doc(
                {
                    "doctype": "CSKH Active Claim",
                    "lead": ld,
                    "campaign": camp_f02.name,
                    "work_item": wi_doc.name,
                }
            ).insert(ignore_permissions=True)
            ownership.assign(
                wi_doc.name,
                "staff.a@dev.local",
                actor="crm.admin@dev.local",
                reason=f"Distribution run {run_f02.name}",
                request_id=f"p2h-f02-{secrets.token_hex(4)}",
                distribution_run=run_f02.name,
            )
            return wi_doc.name

        wi_q1 = alloc_run_item("Q1-CONN")
        ca_service.record_attempt(
            work_item=wi_q1,
            channel="phone_call",
            outcome_code="connected_interested",
            note="Khách nghe máy quan tâm trên trang sau",
            idempotency_key=f"p2h-q1-att-{secrets.token_hex(4)}",
            actor="staff.a@dev.local",
        )
        older_ts = add_to_date(now_datetime(), hours=-2)
        frappe.db.sql(
            "update `tabCSKH Work Item` set modified = %s, assigned_at = %s where name = %s",
            (older_ts, older_ts, wi_q1),
        )
        wi_q2 = alloc_run_item("Q2-UNATT")
        wi_q3 = alloc_run_item("Q3-UNATT")

        frappe.db.commit()
        return {
            "team1_node": team1_node,
            "team2_node": team2_node,
            "camp_t1": camp_t1.name,
            "camp_t2": camp_t2.name,
            "camp_f02": camp_f02.name,
            "run_f02": run_f02.name,
            "lead_a1": lead_a1,
            "lead_b1": lead_b1,
            "lead_c1": lead_c1,
            "lead_dnc_http": lead_dnc_http,
            "wi_a1": wi_a1,
            "wi_b1": wi_b1,
            "wi_c1": wi_c1,
            "wi_dnc_http": wi_dnc_http,
            "wi_q1": wi_q1,
            "wi_q2": wi_q2,
            "wi_q3": wi_q3,
            "system_today": str(now_datetime().date()),
            "cl_a1": cl_a1.name,
            "pol_t2": pol_t2_code,
            "private_file_name": file_doc.name,
            "private_file_url": file_doc.file_url,
            "future_due_1": str(add_to_date(now_datetime(), hours=4)),
            "future_due_2": str(add_to_date(now_datetime(), hours=8)),
        }

    return _with_frappe(_create)


def main() -> int:
    fx = setup_probe_fixtures()

    guest_opener, _ = make_opener()
    staff_a = login("staff-a", "staff.a@dev.local", SEED_PASSWORD)
    tok_staff_a = csrf_token("staff-a", staff_a, "staff.a@dev.local")

    staff_b = login("staff-b", "staff.b@dev.local", SEED_PASSWORD)
    tok_staff_b = csrf_token("staff-b", staff_b, "staff.b@dev.local")

    staff_c = login("staff-c", "staff.c@dev.local", SEED_PASSWORD)
    _tok_staff_c = csrf_token("staff-c", staff_c, "staff.c@dev.local")

    leader_a = login("leader-a", "team1.leader@dev.local", SEED_PASSWORD)
    tok_leader_a = csrf_token("leader-a", leader_a, "team1.leader@dev.local")

    leader_b = login("leader-b", "team2.leader@dev.local", SEED_PASSWORD)
    tok_leader_b = csrf_token("leader-b", leader_b, "team2.leader@dev.local")

    bod = login("bod", "bod.viewer@dev.local", SEED_PASSWORD)
    tok_bod = csrf_token("bod", bod, "bod.viewer@dev.local")

    admin = login("crm-admin", "crm.admin@dev.local", SEED_PASSWORD)
    tok_admin = csrf_token("crm-admin", admin, "crm.admin@dev.local")

    # 1. Guest denials & transport verb/CSRF enforcement
    r = get(
        "p2-http-guest-queue-denied",
        guest_opener,
        "/api/method/crm_cskh.api.workflow.list_work_items",
    )
    check(
        "p2-http-guest-queue-denied",
        r,
        r["status"] in (401, 403),
        "Guest denied on list_work_items",
        "Guest",
    )

    r = get(
        "p2-http-guest-summary-denied",
        guest_opener,
        "/api/method/crm_cskh.api.workflow.progress_summary",
    )
    check(
        "p2-http-guest-summary-denied",
        r,
        r["status"] in (401, 403),
        "Guest denied on progress_summary",
        "Guest",
    )

    r = post_json(
        "p2-http-missing-csrf-rejected",
        staff_a,
        "",
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "idempotency_key": "no-csrf",
        },
    )
    check(
        "p2-http-missing-csrf-rejected",
        r,
        r["status"] in (400, 403),
        "POST without CSRF token is rejected over real HTTP",
        "staff.a@dev.local",
    )

    r = get(
        "p2-http-get-on-post-mutation-rejected",
        staff_a,
        f"/api/method/crm_cskh.api.workflow.record_attempt?work_item={fx['wi_a1']}&channel=phone_call&outcome_code=no_answer&idempotency_key=get-mut",
    )
    check(
        "p2-http-get-on-post-mutation-rejected",
        r,
        r["status"] in (400, 403, 405),
        "GET on POST-only workflow.record_attempt is rejected",
        "staff.a@dev.local",
    )

    # 2. Outcome catalog & Staff queue scoping (`SEC2-01`, `SEC2-08`)
    r = get(
        "p2-http-staff-a-outcomes",
        staff_a,
        "/api/method/crm_cskh.api.workflow.get_outcome_codes",
    )
    out_payload = body_payload(r)
    out_codes = {x["code"]: x for x in out_payload.get("items", [])}
    check(
        "p2-http-staff-a-outcomes",
        r,
        r["status"] == 200
        and out_payload.get("total", 0) >= 12
        and out_codes.get("no_answer", {}).get("is_connected") is False
        and bool(out_codes.get("no_answer", {}).get("label_vi")),
        "get_outcome_codes returns >=12 Vietnamese outcomes with no_answer.is_connected=False",
        "staff.a@dev.local",
    )

    r = get(
        "p2-http-staff-a-queue",
        staff_a,
        f"/api/method/crm_cskh.api.workflow.list_work_items?campaign={fx['camp_t1']}",
    )
    q_items = [x["work_item"] for x in body_payload(r).get("items", [])]
    check(
        "p2-http-staff-a-queue",
        r,
        r["status"] == 200 and q_items == [fx["wi_a1"]],
        "Staff A sees only own wi_a1 in camp_t1, never Staff B's wi_b1",
        "staff.a@dev.local",
    )

    r = get(
        "p2-http-staff-a-spoof-assignee",
        staff_a,
        "/api/method/crm_cskh.api.workflow.list_work_items?assignee=staff.c%40dev.local",
    )
    check(
        "p2-http-staff-a-spoof-assignee",
        r,
        r["status"] == 200 and body_payload(r).get("total") == 0,
        "Staff A passing assignee=staff.c@dev.local gets 0 rows (SEC2-08)",
        "staff.a@dev.local",
    )

    r = get(
        "p2-http-staff-a-foreign-wi-denied",
        staff_a,
        f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={fx['wi_c1']}",
    )
    check(
        "p2-http-staff-a-foreign-wi-denied",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Staff A denied on Team 2 wi_c1 with 403 FORBIDDEN",
        "staff.a@dev.local",
    )

    # 3. Anti-spoofing over HTTP (`SEC2-03`, `SEC2-04`)
    r = post_json(
        "p2-http-anti-spoof-performed-by",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "performed_by": "staff.b@dev.local",
            "idempotency_key": f"spoof-perf-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-anti-spoof-performed-by",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Spoofing performed_by returns 403 FORBIDDEN",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-anti-spoof-verification-status",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "verification_status": "verified",
            "idempotency_key": f"spoof-ver-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-anti-spoof-verification-status",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Spoofing verification_status returns 403 FORBIDDEN",
        "staff.a@dev.local",
    )

    # 4. Form-urlencoded `record_attempt` (`close_work_item="false"`), XSS stripping, idempotent replay & conflict
    idem_att1 = f"http-att1-{secrets.token_hex(5)}"
    r = post(
        "p2-http-staff-a-record-attempt-form",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "note": "<script>alert(1)</script> Gọi lần 1 chuông đổ không nghe",
            "close_work_item": "false",
            "idempotency_key": idem_att1,
        },
    )
    att1_body = body_payload(r)
    att1_id = str(att1_body.get("attempt_id") or "")
    db_note = (
        _with_frappe(lambda f: f.db.get_value("CSKH Contact Attempt", att1_id, "note"))
        if att1_id
        else ""
    )
    check(
        "p2-http-staff-a-record-attempt-form",
        r,
        r["status"] == 200
        and att1_body.get("work_item_status") == "in_progress"
        and att1_body.get("attempt_count") == 1
        and att1_body.get("connected_attempt_count") == 0
        and "<script" not in str(db_note).lower(),
        "Form-urlencoded record_attempt moves to in_progress (connected=0) and strips <script> in DB",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-staff-a-record-attempt-replay",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "note": "<script>alert(1)</script> Gọi lần 1 chuông đổ không nghe",
            "close_work_item": False,
            "idempotency_key": idem_att1,
        },
    )
    check(
        "p2-http-staff-a-record-attempt-replay",
        r,
        r["status"] == 200
        and body_payload(r).get("idempotent_replay") is True
        and body_payload(r).get("attempt_id") == att1_id,
        "Idempotent replay over HTTP returns idempotent_replay=True and same attempt_id",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-staff-a-record-attempt-idem-conflict",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "busy",
            "idempotency_key": idem_att1,
        },
    )
    check(
        "p2-http-staff-a-record-attempt-idem-conflict",
        r,
        r["status"] == 409 and error_envelope(r).get("code") == "IDEMPOTENCY_CONFLICT",
        "Reusing idempotency_key with mutated payload returns 409 IDEMPOTENCY_CONFLICT",
        "staff.a@dev.local",
    )

    # 5. Stale revision (`409 STALE_WORK_ITEM`) & `schedule_followup` + `complete_followup` guard (`T2-07`)
    r = post_json(
        "p2-http-schedule-followup-stale-rev",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": fx["wi_a1"],
            "due_at": fx["future_due_1"],
            "title": "Hẹn gọi lại chiều",
            "expected_revision": 1,
            "idempotency_key": f"fu-stale-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-schedule-followup-stale-rev",
        r,
        r["status"] == 409 and error_envelope(r).get("code") == "STALE_WORK_ITEM",
        "Stale expected_revision=1 returns 409 STALE_WORK_ITEM",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-schedule-followup-ok",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": fx["wi_a1"],
            "due_at": fx["future_due_1"],
            "title": "Hẹn gọi lại chiều",
            "expected_revision": att1_body.get("revision"),
            "idempotency_key": f"fu-ok-{secrets.token_hex(4)}",
        },
    )
    fu_body = body_payload(r)
    task_a1 = str(fu_body.get("task_id") or "")
    check(
        "p2-http-schedule-followup-ok",
        r,
        r["status"] == 200
        and fu_body.get("work_item_status") == "on_hold"
        and bool(task_a1),
        "schedule_followup moves Work Item to on_hold and creates callback CRM Task",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-complete-followup-without-attempt-blocked",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.complete_followup",
        {
            "work_item": fx["wi_a1"],
            "task_id": task_a1,
            "action": "complete",
            "idempotency_key": f"fu-empty-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-complete-followup-without-attempt-blocked",
        r,
        r["status"] == 422
        and error_envelope(r).get("code") == "OUTCOME_NOT_COMPATIBLE",
        "Marking callback Done without attempt_id is rejected with 422 OUTCOME_NOT_COMPATIBLE",
        "staff.a@dev.local",
    )

    # 6. `ownership.reassign(Staff A -> Staff B)` over HTTP & post-reassign revocation (`SEC2-06`, `SEC2-07`, `T2-14`)
    r = post_json(
        "p2-http-admin-reassign-a-to-b",
        admin,
        tok_admin,
        "/api/method/crm_cskh.api.ownership.reassign",
        {
            "work_item": fx["wi_a1"],
            "expected_owner": "staff.a@dev.local",
            "target_user": "staff.b@dev.local",
            "reason": "Điều phối ca chiều qua HTTP",
            "request_id": f"http-reass-{secrets.token_hex(5)}",
        },
    )
    check(
        "p2-http-admin-reassign-a-to-b",
        r,
        r["status"] == 200 and body_payload(r).get("work_item_status") == "on_hold",
        "ownership.reassign preserves on_hold status and transfers open callback to Staff B",
        "crm.admin@dev.local",
    )

    for case_suffix, path in (
        (
            "wi",
            f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={fx['wi_a1']}",
        ),
        (
            "att-rest",
            f"/api/resource/CSKH%20Contact%20Attempt/{urllib.parse.quote(att1_id, safe='')}",
        ),
        (
            "task-rest",
            f"/api/resource/CRM%20Task/{urllib.parse.quote(task_a1, safe='')}",
        ),
        (
            "calllog-rest",
            f"/api/resource/CRM%20Call%20Log/{urllib.parse.quote(fx['cl_a1'], safe='')}",
        ),
    ):
        r = get(f"p2-http-staff-a-revoked-{case_suffix}", staff_a, path)
        check(
            f"p2-http-staff-a-revoked-{case_suffix}",
            r,
            r["status"] == 403,
            f"Former owner Staff A loses read access ({case_suffix}) immediately after reassign",
            "staff.a@dev.local",
        )

    r = post_json(
        "p2-http-staff-a-stale-owner-attempt-blocked",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "idempotency_key": f"stale-own-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-staff-a-stale-owner-attempt-blocked",
        r,
        r["status"] == 409 and error_envelope(r).get("code") == "OWNERSHIP_CHANGED",
        "Former owner Staff A attempting record_attempt receives 409 OWNERSHIP_CHANGED",
        "staff.a@dev.local",
    )

    # Staff B lists unlinked Call Logs, links `cl_a1`, and resolves `task_a1` in one atomic `record_attempt`
    r = get(
        "p2-http-staff-b-unlinked-call-logs",
        staff_b,
        f"/api/method/crm_cskh.api.workflow.list_unlinked_call_logs?work_item={fx['wi_a1']}",
    )
    check(
        "p2-http-staff-b-unlinked-call-logs",
        r,
        r["status"] == 200
        and fx["cl_a1"] in [x["name"] for x in body_payload(r).get("items", [])],
        "New owner Staff B sees unlinked CRM Call Log cl_a1",
        "staff.b@dev.local",
    )

    r = post_json(
        "p2-http-staff-b-record-connected-attempt",
        staff_b,
        tok_staff_b,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_a1"],
            "channel": "phone_call",
            "outcome_code": "connected_interested",
            "call_log": fx["cl_a1"],
            "note": "Đã trao đổi với khách sau bàn giao, khách quan tâm",
            "complete_open_followup": True,
            "idempotency_key": f"att-b-{secrets.token_hex(5)}",
        },
    )
    att2_body = body_payload(r)
    att2_id = str(att2_body.get("attempt_id") or "")
    db_task_status = _with_frappe(
        lambda f: f.db.get_value("CRM Task", task_a1, "status")
    )
    check(
        "p2-http-staff-b-record-connected-attempt",
        r,
        r["status"] == 200
        and att2_body.get("work_item_status") == "in_progress"
        and att2_body.get("attempt_count") == 2
        and att2_body.get("connected_attempt_count") == 1
        and att2_body.get("resolved_callback_task") == task_a1
        and db_task_status == "Done",
        "Staff B links cl_a1, resolves callback CRM Task to Done, and updates connected_attempt_count=1",
        "staff.b@dev.local",
    )

    # 7. Correction request + Leader cross-team block + Leader same-team approval (`T2-20`, `SEC2-01`)
    r = post_json(
        "p2-http-staff-b-self-approve-denied",
        staff_b,
        tok_staff_b,
        "/api/method/crm_cskh.api.workflow.request_correction",
        {
            "attempt": att2_id,
            "action": "correct",
            "corrected_fields": {"note": "Tự duyệt"},
            "reason": "Thử auto_approve",
            "auto_approve": True,
            "idempotency_key": f"corr-self-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-staff-b-self-approve-denied",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Staff B passing auto_approve=True gets 403 FORBIDDEN",
        "staff.b@dev.local",
    )

    r = post_json(
        "p2-http-staff-b-request-correction",
        staff_b,
        tok_staff_b,
        "/api/method/crm_cskh.api.workflow.request_correction",
        {
            "attempt": att2_id,
            "action": "correct",
            "corrected_fields": {"note": "Khách quan tâm gói doanh nghiệp"},
            "reason": "Bổ sung chính xác tên gói dịch vụ",
            "idempotency_key": f"corr-ok-{secrets.token_hex(4)}",
        },
    )
    corr_id = str(body_payload(r).get("correction_id") or "")
    check(
        "p2-http-staff-b-request-correction",
        r,
        r["status"] == 200
        and body_payload(r).get("status") == "pending"
        and bool(corr_id),
        "Staff B creates pending CSKH Attempt Correction",
        "staff.b@dev.local",
    )

    r = post_json(
        "p2-http-leader-b-foreign-correction-denied",
        leader_b,
        tok_leader_b,
        "/api/method/crm_cskh.api.workflow.review_correction",
        {
            "correction_id": corr_id,
            "decision": "approve",
            "review_note": "Leader B duyệt chéo team",
            "idempotency_key": f"rev-ldrb-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-leader-b-foreign-correction-denied",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Leader B (Team 2) denied reviewing Team 1 correction",
        "team2.leader@dev.local",
    )

    r = post_json(
        "p2-http-leader-a-approve-correction",
        leader_a,
        tok_leader_a,
        "/api/method/crm_cskh.api.workflow.review_correction",
        {
            "correction_id": corr_id,
            "decision": "approve",
            "review_note": "Đồng ý bổ sung nội dung tư vấn",
            "idempotency_key": f"rev-ldra-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-leader-a-approve-correction",
        r,
        r["status"] == 200 and body_payload(r).get("status") == "approved",
        "Leader A approves Team 1 correction",
        "team1.leader@dev.local",
    )

    # 8. Generic Frappe API (`/api/resource/*`, `frappe.client.*`, `getdoc`) isolation (`SEC2-02`, `SEC2-05`)
    r = get(
        "p2-http-leader-a-rest-read-team1-attempt",
        leader_a,
        f"/api/resource/CSKH%20Contact%20Attempt/{urllib.parse.quote(att2_id, safe='')}",
    )
    check(
        "p2-http-leader-a-rest-read-team1-attempt",
        r,
        r["status"] == 200,
        "Leader A positive control: reads Team 1 CSKH Contact Attempt via /api/resource",
        "team1.leader@dev.local",
    )

    r = get(
        "p2-http-leader-b-rest-denied-team1-attempt",
        leader_b,
        f"/api/resource/CSKH%20Contact%20Attempt/{urllib.parse.quote(att2_id, safe='')}",
    )
    check(
        "p2-http-leader-b-rest-denied-team1-attempt",
        r,
        r["status"] == 403,
        "Leader B negative control: denied Team 1 CSKH Contact Attempt via /api/resource",
        "team2.leader@dev.local",
    )

    r = get(
        "p2-http-leader-a-rest-denied-team2-policy",
        leader_a,
        f"/api/resource/CSKH%20Policy/{urllib.parse.quote(fx['pol_t2'], safe='')}",
    )
    check(
        "p2-http-leader-a-rest-denied-team2-policy",
        r,
        r["status"] == 403,
        "Leader A denied Team 2 CSKH Policy via /api/resource",
        "team1.leader@dev.local",
    )

    r = get(
        "p2-http-leader-b-getdoc-denied-team1-correction",
        leader_b,
        f"/api/method/frappe.desk.form.load.getdoc?doctype=CSKH%20Attempt%20Correction&name={urllib.parse.quote(corr_id, safe='')}",
    )
    check(
        "p2-http-leader-b-getdoc-denied-team1-correction",
        r,
        r["status"] == 403,
        "Leader B denied Team 1 CSKH Attempt Correction via Desk getdoc",
        "team2.leader@dev.local",
    )

    r = post_json(
        "p2-http-direct-rest-insert-attempt-blocked",
        staff_b,
        tok_staff_b,
        "/api/resource/CSKH%20Contact%20Attempt",
        {
            "work_item": fx["wi_a1"],
            "lead": fx["lead_a1"],
            "campaign": fx["camp_t1"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "idempotency_key": "rest-bypass",
        },
    )
    check(
        "p2-http-direct-rest-insert-attempt-blocked",
        r,
        r["status"] in (403, 417),
        "Direct POST /api/resource/CSKH Contact Attempt is blocked",
        "staff.b@dev.local",
    )

    # 9. Close, Reopen & `L-23` cross-team closed-item protection over HTTP
    def _fetch_close_db_state(work_item_name: str, lead_name: str) -> dict[str, object]:
        return _with_frappe(
            lambda fr: fetch_close_work_item_db_state(fr, work_item_name, lead_name)
        )

    r = run_close_work_item_case(
        "p2-http-staff-b-close-wi",
        staff_b,
        tok_staff_b,
        work_item=fx["wi_a1"],
        lead=fx["lead_a1"],
        actor="staff.b@dev.local",
        completion_reason="Hoàn tất chăm sóc đợt 1",
        idempotency_key=f"close-b-{secrets.token_hex(4)}",
        db_state_fetcher=_fetch_close_db_state,
        min_revision=2,
    )
    close1_rev = (
        body_payload(r).get("revision")
        if isinstance(body_payload(r).get("revision"), int)
        else 2
    )

    r = post_json(
        "p2-http-staff-b-reopen-denied",
        staff_b,
        tok_staff_b,
        "/api/method/crm_cskh.api.workflow.reopen_work_item",
        {
            "work_item": fx["wi_a1"],
            "target_user": "staff.b@dev.local",
            "reason": "Nhân viên tự mở lại",
            "idempotency_key": f"reopen-sb-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-staff-b-reopen-denied",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Staff B denied reopening closed Work Item",
        "staff.b@dev.local",
    )

    r = post_json(
        "p2-http-leader-a-reopen-wi",
        leader_a,
        tok_leader_a,
        "/api/method/crm_cskh.api.workflow.reopen_work_item",
        {
            "work_item": fx["wi_a1"],
            "target_user": "staff.b@dev.local",
            "reason": "Khách gọi lại hỏi hợp đồng",
            "idempotency_key": f"reopen-ldra-{secrets.token_hex(4)}",
        },
    )
    reopen_rev_raw = body_payload(r).get("revision")
    reopen_rev = (
        reopen_rev_raw
        if isinstance(reopen_rev_raw, int) and not isinstance(reopen_rev_raw, bool)
        else int(close1_rev) + 1
    )
    check(
        "p2-http-leader-a-reopen-wi",
        r,
        r["status"] == 200 and body_payload(r).get("work_item_status") == "in_progress",
        "Leader A reopens wi_a1 with audit reason",
        "team1.leader@dev.local",
    )

    # Close `wi_a1` again (verifying full contract + DB closed/claim/callback invariants),
    # then transfer `lead_a1` into Team 2 (`camp_t2`, `staff.c@dev.local`) -> Team 1 loses read access (`L-23`)
    r = run_close_work_item_case(
        "p2-http-staff-b-close-wi-final",
        staff_b,
        tok_staff_b,
        work_item=fx["wi_a1"],
        lead=fx["lead_a1"],
        actor="staff.b@dev.local",
        completion_reason="Kết thúc tại Team 1",
        idempotency_key=f"close-b2-{secrets.token_hex(4)}",
        db_state_fetcher=_fetch_close_db_state,
        min_revision=max(reopen_rev + 1, 2),
    )

    def _move_lead_to_team2(frappe):
        from crm_cskh.services import ownership

        wi_t2 = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "campaign": fx["camp_t2"],
                "lead": fx["lead_a1"],
                "status": "queued",
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": fx["lead_a1"],
                "campaign": fx["camp_t2"],
                "work_item": wi_t2.name,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi_t2.name,
            "staff.c@dev.local",
            actor="crm.admin@dev.local",
            reason="Chuyển khách sang Team 2",
            request_id=f"p2h-t2-{secrets.token_hex(4)}",
        )
        frappe.db.commit()
        return wi_t2.name

    try:
        _ = _with_frappe(_move_lead_to_team2)
    except Exception as error:
        _append_unique(
            failures,
            f"p2-http-move-lead-to-team2-setup:{type(error).__name__}",
        )
        print(
            f"p2-http-move-lead-to-team2-setup: FAIL ({type(error).__name__})",
            file=sys.stderr,
        )

    r = get(
        "p2-http-l23-leader-a-closed-wi-denied-after-team2-claim",
        leader_a,
        f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={fx['wi_a1']}",
    )
    check(
        "p2-http-l23-leader-a-closed-wi-denied-after-team2-claim",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Leader A loses read access to closed wi_a1 once Lead is claimed by Team 2 (L-23 / SEC2-07)",
        "team1.leader@dev.local",
    )

    # 10. `BOD Viewer` 0-PII summary + detail block (`SEC2-09`)
    r = get(
        "p2-http-bod-progress-summary",
        bod,
        "/api/method/crm_cskh.api.workflow.progress_summary",
    )
    bod_payload = body_payload(r)
    raw_bod_text = str(r.get("text") or "")
    check(
        "p2-http-bod-progress-summary",
        r,
        r["status"] == 200
        and isinstance(bod_payload.get("cards"), dict)
        and bod_payload["cards"].get("effective_attempts", 0) >= 2
        and PHONE_RE.search(raw_bod_text) is None
        and fx["lead_a1"] not in raw_bod_text
        and "Khách HTTP P2" not in raw_bod_text,
        "BOD progress_summary returns live aggregate counts with 0 PII (no phones, Lead IDs, or names)",
        "bod.viewer@dev.local",
    )

    r = get(
        "p2-http-bod-queue-denied",
        bod,
        "/api/method/crm_cskh.api.workflow.list_work_items",
    )
    check(
        "p2-http-bod-queue-denied",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "BOD Viewer is denied PII-bearing list_work_items",
        "bod.viewer@dev.local",
    )

    # 11. Settings mutations & Reconciliation gate over HTTP (`SEC2-11`, `P2.md` §15.5)
    r = post_json(
        "p2-http-leader-a-save-policy-denied",
        leader_a,
        tok_leader_a,
        "/api/method/crm_cskh.api.settings.save_policy",
        {
            "policy_code": "LDR-HACK",
            "title": "Trái phép",
            "scope_type": "Global",
            "idempotency_key": f"ldr-pol-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-leader-a-save-policy-denied",
        r,
        r["status"] == 403 and error_envelope(r).get("code") == "FORBIDDEN",
        "Leader A denied mutating CSKH Policy",
        "team1.leader@dev.local",
    )

    r = post_json(
        "p2-http-admin-save-policy",
        admin,
        tok_admin,
        "/api/method/crm_cskh.api.settings.save_policy",
        {
            "policy_code": fx["pol_t2"],
            "title": "Chính sách riêng Team 2 (v2)",
            "scope_type": "Team",
            "team": fx["team2_node"],
            "allowed_backdate_minutes": 90,
            "idempotency_key": f"adm-pol-{secrets.token_hex(4)}",
        },
    )
    check(
        "p2-http-admin-save-policy",
        r,
        r["status"] == 200 and body_payload(r).get("policy", {}).get("version", 0) >= 2,
        "CRM Admin updates CSKH Policy and increments version",
        "crm.admin@dev.local",
    )

    # 11B. T1C Remediations over live HTTP (`F-P2-06`, `F-P2-01`, `F-P2-04`, `F-P2-02`, `F-P2-05`)
    hash_tag_re = re.compile(r"\[H:[0-9a-fA-F]{16,64}\]")
    lead_dnc_q = urllib.parse.quote(fx["lead_dnc_http"], safe="")

    # `F-P2-06`: Generic REST v1/v2 & `frappe.client.set_value` DNC mutations blocked, normal Lead PUT works
    r = put_json(
        "p2-http-f06-staff-a-put-lead-dnc-blocked",
        staff_a,
        tok_staff_a,
        f"/api/resource/CRM%20Lead/{lead_dnc_q}",
        {"cskh_do_not_contact": 1, "ignore_permissions": 1},
    )
    db_dnc_flag = _with_frappe(
        lambda f: int(
            f.db.get_value("CRM Lead", fx["lead_dnc_http"], "cskh_do_not_contact") or 0
        )
    )
    check(
        "p2-http-f06-staff-a-put-lead-dnc-blocked",
        r,
        r["status"] == 403 and db_dnc_flag == 0,
        "Staff A (with DocShare write=1) denied mutating cskh_do_not_contact via PUT /api/resource/CRM Lead",
        "staff.a@dev.local",
    )

    r = patch_json(
        "p2-http-f06-staff-a-v2-patch-lead-dnc-blocked",
        staff_a,
        tok_staff_a,
        f"/api/v2/document/CRM%20Lead/{lead_dnc_q}",
        {"cskh_dnc_reason": "Giả mạo qua REST v2"},
    )
    check(
        "p2-http-f06-staff-a-v2-patch-lead-dnc-blocked",
        r,
        r["status"] == 403,
        "Staff A denied mutating cskh_dnc_reason via PATCH /api/v2/document/CRM Lead",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-f06-admin-set-value-dnc-blocked",
        admin,
        tok_admin,
        "/api/method/frappe.client.set_value",
        {
            "doctype": "CRM Lead",
            "name": fx["lead_dnc_http"],
            "fieldname": "cskh_do_not_contact",
            "value": 1,
        },
    )
    check(
        "p2-http-f06-admin-set-value-dnc-blocked",
        r,
        r["status"] == 403,
        "CRM Admin denied setting cskh_do_not_contact via generic frappe.client.set_value",
        "crm.admin@dev.local",
    )

    r = put_json(
        "p2-http-f06-staff-a-put-lead-normal-edit-ok",
        staff_a,
        tok_staff_a,
        f"/api/resource/CRM%20Lead/{lead_dnc_q}",
        {"first_name": "Khách HTTP Đã Sửa Hợp Lệ"},
    )
    db_first_name = _with_frappe(
        lambda f: f.db.get_value("CRM Lead", fx["lead_dnc_http"], "first_name")
    )
    check(
        "p2-http-f06-staff-a-put-lead-normal-edit-ok",
        r,
        r["status"] == 200 and db_first_name == "Khách HTTP Đã Sửa Hợp Lệ",
        "Normal non-DNC Lead field edit via PUT /api/resource/CRM Lead succeeds",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-f06-staff-a-record-dnc-attempt",
        staff_a,
        tok_staff_a,
        "/api/method/crm_cskh.api.workflow.record_attempt",
        {
            "work_item": fx["wi_dnc_http"],
            "channel": "phone_call",
            "outcome_code": "do_not_contact",
            "note": "Khách Trần Bí Mật yêu cầu ngừng gọi khẩn",
            "idempotency_key": f"http-dnc-{secrets.token_hex(4)}",
        },
    )
    dnc_body = body_payload(r)
    db_dnc_after = _with_frappe(
        lambda f: int(
            f.db.get_value("CRM Lead", fx["lead_dnc_http"], "cskh_do_not_contact") or 0
        )
    )
    check(
        "p2-http-f06-staff-a-record-dnc-attempt",
        r,
        r["status"] == 200
        and dnc_body.get("do_not_contact") is True
        and dnc_body.get("work_item_status") == "closed"
        and db_dnc_after == 1,
        "Authorized workflow.record_attempt DNC auto-closes Work Item and marks Lead DNC=1",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-f06-admin-set-value-clear-dnc-blocked",
        admin,
        tok_admin,
        "/api/method/frappe.client.set_value",
        {
            "doctype": "CRM Lead",
            "name": fx["lead_dnc_http"],
            "fieldname": "cskh_do_not_contact",
            "value": 0,
        },
    )
    db_dnc_still_set = _with_frappe(
        lambda f: int(
            f.db.get_value("CRM Lead", fx["lead_dnc_http"], "cskh_do_not_contact") or 0
        )
    )
    check(
        "p2-http-f06-admin-set-value-clear-dnc-blocked",
        r,
        r["status"] == 403 and db_dnc_still_set == 1,
        "CRM Admin cannot clear cskh_do_not_contact=0 via generic frappe.client.set_value",
        "crm.admin@dev.local",
    )

    # `F-P2-01`: 5-plane `BOD` privacy + filter oracle block + positive milestone check without `[H:<16hex>]`
    dnc_hist_name = str(
        _with_frappe(
            lambda f: f.db.get_value(
                "CSKH Assignment History",
                {"work_item": fx["wi_dnc_http"], "action": "close"},
                "name",
            )
        )
        or ""
    )

    r = get(
        "p2-http-f01-bod-ownership-history-no-leak",
        bod,
        f"/api/method/crm_cskh.api.ownership.history?work_item={urllib.parse.quote(fx['wi_dnc_http'], safe='')}",
    )
    raw_txt = str(r.get("text") or "")
    check(
        "p2-http-f01-bod-ownership-history-no-leak",
        r,
        r["status"] == 200
        and len(body_payload(r).get("items", [])) >= 2
        and "Trần Bí Mật" not in raw_txt
        and "private_reason" not in raw_txt
        and hash_tag_re.search(raw_txt) is None,
        "BOD ownership.history returns neutral public reason only (0 customer note, 0 private_reason, 0 [H:<16hex>])",
        "bod.viewer@dev.local",
    )

    hist_flt_q = urllib.parse.quote(
        json.dumps([["CSKH Assignment History", "work_item", "=", fx["wi_dnc_http"]]]),
        safe="",
    )
    hist_flds_q = urllib.parse.quote(
        json.dumps(["name", "action", "reason", "private_reason", "payload_hash"]),
        safe="",
    )
    r = get(
        "p2-http-f01-bod-rest-history-fields-no-leak",
        bod,
        f"/api/resource/CSKH%20Assignment%20History?filters={hist_flt_q}&fields={hist_flds_q}",
    )
    raw_rest_txt = str(r.get("text") or "")
    check(
        "p2-http-f01-bod-rest-history-fields-no-leak",
        r,
        r["status"] == 200
        and "Trần Bí Mật" not in raw_rest_txt
        and "private_reason" not in raw_rest_txt
        and "payload_hash" not in raw_rest_txt,
        "BOD /api/resource/CSKH Assignment History strips permlevel=1 fields and hides customer note",
        "bod.viewer@dev.local",
    )

    r = get(
        "p2-http-f01-bod-client-get-list-no-leak",
        bod,
        f"/api/method/frappe.client.get_list?doctype=CSKH%20Assignment%20History&filters={hist_flt_q}&fields={hist_flds_q}",
    )
    raw_cgl_txt = str(r.get("text") or "")
    check(
        "p2-http-f01-bod-client-get-list-no-leak",
        r,
        r["status"] == 200
        and "Trần Bí Mật" not in raw_cgl_txt
        and "private_reason" not in raw_cgl_txt,
        "BOD frappe.client.get_list strips private_reason and hides customer note",
        "bod.viewer@dev.local",
    )

    r = post_json(
        "p2-http-f01-bod-reportview-no-leak",
        bod,
        tok_bod,
        "/api/method/frappe.desk.reportview.get_list",
        {
            "doctype": "CSKH Assignment History",
            "fields": ["name", "action", "reason"],
            "filters": [
                ["CSKH Assignment History", "work_item", "=", fx["wi_dnc_http"]]
            ],
        },
    )
    raw_rv_txt = str(r.get("text") or "")
    check(
        "p2-http-f01-bod-reportview-no-leak",
        r,
        r["status"] == 200
        and "Trần Bí Mật" not in raw_rv_txt
        and hash_tag_re.search(raw_rv_txt) is None,
        "BOD reportview.get_list returns neutral public reason with 0 customer note and 0 [H:<16hex>]",
        "bod.viewer@dev.local",
    )

    r = get(
        "p2-http-f01-bod-getdoc-history-no-leak",
        bod,
        f"/api/method/frappe.desk.form.load.getdoc?doctype=CSKH%20Assignment%20History&name={urllib.parse.quote(dnc_hist_name, safe='')}",
    )
    raw_gd_txt = str(r.get("text") or "")
    gd_docs = (
        (r.get("json") or {}).get("docs") if isinstance(r.get("json"), dict) else []
    )
    check(
        "p2-http-f01-bod-getdoc-history-no-leak",
        r,
        r["status"] == 200
        and isinstance(gd_docs, list)
        and len(gd_docs) == 1
        and gd_docs[0].get("private_reason") is None
        and "Trần Bí Mật" not in raw_gd_txt,
        "BOD Desk getdoc on CSKH Assignment History hides private_reason and customer note",
        "bod.viewer@dev.local",
    )

    oracle_flt_q = urllib.parse.quote(
        json.dumps([["CSKH Assignment History", "private_reason", "like", "%"]]),
        safe="",
    )
    r = get(
        "p2-http-f01-bod-filter-private-reason-denied",
        bod,
        f"/api/resource/CSKH%20Assignment%20History?filters={oracle_flt_q}",
    )
    check(
        "p2-http-f01-bod-filter-private-reason-denied",
        r,
        r["status"] == 403,
        "BOD filtering on restricted private_reason over HTTP returns 403",
        "bod.viewer@dev.local",
    )

    r = get(
        "p2-http-f01-leader-a-attempts-milestones-clean",
        leader_a,
        f"/api/method/crm_cskh.api.workflow.list_attempts?work_item={urllib.parse.quote(fx['wi_dnc_http'], safe='')}&include_milestones=true",
    )
    ldr_att_txt = str(r.get("text") or "")
    ldr_milestones = body_payload(r).get("ownership_milestones", [])
    check(
        "p2-http-f01-leader-a-attempts-milestones-clean",
        r,
        r["status"] == 200
        and len(ldr_milestones) >= 2
        and "Trần Bí Mật" not in json.dumps(ldr_milestones, ensure_ascii=False)
        and hash_tag_re.search(ldr_att_txt) is None,
        "Authorized Leader A reads workflow.list_attempts ownership_milestones with 0 customer note and 0 [H:<16hex>] display tags",
        "team1.leader@dev.local",
    )

    # `F-P2-04`: Off-first-page SQL `care_state` filter + `owner_at_close` vs `closed_by`
    r = get(
        "p2-http-f04-staff-a-care-state-connected-off-page1",
        staff_a,
        f"/api/method/crm_cskh.api.workflow.list_work_items?campaign={urllib.parse.quote(fx['camp_f02'], safe='')}&care_state=connected&page=1&page_size=1",
    )
    f04_conn_payload = body_payload(r)
    check(
        "p2-http-f04-staff-a-care-state-connected-off-page1",
        r,
        r["status"] == 200
        and f04_conn_payload.get("total") == 1
        and [x.get("work_item") for x in f04_conn_payload.get("items", [])]
        == [fx["wi_q1"]],
        "SQL care_state=connected finds wi_q1 on page=1&page_size=1 despite 2 newer unattempted items",
        "staff.a@dev.local",
    )

    r = get(
        "p2-http-f04-staff-a-invalid-care-state-400",
        staff_a,
        "/api/method/crm_cskh.api.workflow.list_work_items?care_state=invalid_care_state",
    )
    check(
        "p2-http-f04-staff-a-invalid-care-state-400",
        r,
        r["status"] == 400 and error_envelope(r).get("code") == "INVALID_REQUEST",
        "Invalid care_state returns 400 INVALID_REQUEST",
        "staff.a@dev.local",
    )

    r = post_json(
        "p2-http-f04-leader-a-close-staff-a-wi",
        leader_a,
        tok_leader_a,
        "/api/method/crm_cskh.api.workflow.close_work_item",
        {
            "work_item": fx["wi_q1"],
            "completion_reason": "Leader A duyệt đóng hồ sơ của Staff A qua HTTP",
            "idempotency_key": f"f04-ldr-close-{secrets.token_hex(4)}",
        },
    )
    f04_close_body = body_payload(r)
    check(
        "p2-http-f04-leader-a-close-staff-a-wi",
        r,
        r["status"] == 200
        and f04_close_body.get("owner_at_close") == "staff.a@dev.local"
        and f04_close_body.get("closed_by") == "team1.leader@dev.local",
        "Leader A closing Staff A's Work Item records owner_at_close=staff.a != closed_by=team1.leader",
        "team1.leader@dev.local",
    )

    r = get(
        "p2-http-f04-leader-a-filter-closed-owner-vs-actor",
        leader_a,
        f"/api/method/crm_cskh.api.workflow.list_work_items?campaign={urllib.parse.quote(fx['camp_f02'], safe='')}&status=closed&owner_at_close=staff.a%40dev.local&closed_by=team1.leader%40dev.local",
    )
    f04_closed_q = body_payload(r)
    check(
        "p2-http-f04-leader-a-filter-closed-owner-vs-actor",
        r,
        r["status"] == 200
        and f04_closed_q.get("total") == 1
        and [x.get("work_item") for x in f04_closed_q.get("items", [])]
        == [fx["wi_q1"]],
        "Leader A filters closed Work Items distinguishing owner_at_close vs closed_by",
        "team1.leader@dev.local",
    )

    # `F-P2-02`: `run_history.runs` preserves historical allocation count (`3`) after `wi_q1` became `in_progress` -> `closed`
    r = get(
        "p2-http-f02-leader-a-run-history-preserves-p2-states",
        leader_a,
        f"/api/method/crm_cskh.api.run_history.runs?run_id={urllib.parse.quote(fx['run_f02'], safe='')}",
    )
    f02_items = body_payload(r).get("items", [])
    f02_row = f02_items[0] if f02_items else {}
    check(
        "p2-http-f02-leader-a-run-history-preserves-p2-states",
        r,
        r["status"] == 200
        and f02_row.get("allocation_semantics") == "historical_run_allocation"
        and f02_row.get("assigned") == {"staff.a@dev.local": 3}
        and f02_row.get("created") == 3
        and f02_row.get("active_owned") == {"staff.a@dev.local": 2}
        and f02_row.get("active_owned_total") == 2,
        "run_history.runs preserves historical assigned=3 while reporting live active_owned=2",
        "team1.leader@dev.local",
    )

    # `F-P2-05`: Same-day date-only `progress_summary` (`[start_of_day, start_of_next_day)`) + DISTINCT Work Item counters + 400 validation
    r = get(
        "p2-http-f05-bod-same-day-progress-summary",
        bod,
        f"/api/method/crm_cskh.api.workflow.progress_summary?date_from={fx['system_today']}&date_to={fx['system_today']}",
    )
    f05_sum = body_payload(r)
    f05_cards = f05_sum.get("cards") if isinstance(f05_sum.get("cards"), dict) else {}
    f05_period = (
        f05_sum.get("period") if isinstance(f05_sum.get("period"), dict) else {}
    )
    raw_f05_txt = str(r.get("text") or "")
    check(
        "p2-http-f05-bod-same-day-progress-summary",
        r,
        r["status"] == 200
        and f05_period.get("end_bound_mode") == "exclusive"
        and int(f05_cards.get("effective_attempts", 0)) >= 3
        and int(f05_cards.get("attempted_work_items", 0)) >= 2
        and int(f05_cards.get("attempted_work_items", -1))
        + int(f05_cards.get("unattempted_work_items", -1))
        == int(f05_cards.get("cohort_work_items", -999))
        and int(f05_cards.get("connected_work_items", 0)) >= 1
        and int(f05_cards.get("dnc_work_items", 0))
        >= int(f05_cards.get("dnc_leads", 0))
        >= 1
        and PHONE_RE.search(raw_f05_txt) is None,
        "Same-day date_from=date_to progress_summary uses half-open exclusive end bound and returns reconciled DISTINCT Work Item counters with 0 PII",
        "bod.viewer@dev.local",
    )

    r = get(
        "p2-http-f05-bod-invalid-date-range-400",
        bod,
        "/api/method/crm_cskh.api.workflow.progress_summary?date_from=2026-12-10&date_to=2026-12-01",
    )
    check(
        "p2-http-f05-bod-invalid-date-range-400",
        r,
        r["status"] == 400 and error_envelope(r).get("code") == "INVALID_REQUEST",
        "Inverted date_from > date_to on progress_summary returns 400 INVALID_REQUEST",
        "bod.viewer@dev.local",
    )

    r = get(
        "p2-http-admin-reconcile", admin, "/api/method/crm_cskh.api.workflow.reconcile"
    )
    check(
        "p2-http-admin-reconcile",
        r,
        r["status"] == 200 and body_payload(r).get("ok") is True,
        "workflow.reconcile confirms 0 P2 invariant violations across DB",
        "crm.admin@dev.local",
    )

    # 12. P0 File-oracle non-regression (`SEC2-12`):
    #     a) `/api/resource/File/<real>` vs `/api/resource/File/<missing>` -> both 404 byte-identical!
    #     b) `/private/files/<real>` vs `/private/files/<missing>` -> both 403 byte-identical!
    r_res_file_real = get(
        "p2-http-file-oracle-resource-real",
        staff_b,
        f"/api/resource/File/{urllib.parse.quote(fx['private_file_name'], safe='')}",
    )
    r_res_file_missing = get(
        "p2-http-file-oracle-resource-missing",
        staff_b,
        "/api/resource/File/deadbeef00missing",
    )
    check(
        "p2-http-file-oracle-resource-real",
        r_res_file_real,
        r_res_file_real["status"] == 404
        and fx["private_file_name"] not in str(r_res_file_real["text"]),
        "GET /api/resource/File/<real_private> returns 404 without leaking docname",
        "staff.b@dev.local",
    )
    check(
        "p2-http-file-oracle-resource-missing",
        r_res_file_missing,
        r_res_file_missing["status"] == 404
        and r_res_file_real["text"] == r_res_file_missing["text"]
        and "deadbeef00missing" not in str(r_res_file_missing["text"]),
        "P0 File-oracle returns byte-identical 404 on real vs missing /api/resource/File routes",
        "staff.b@dev.local",
    )

    r_unauth_file = get(
        "p2-http-file-oracle-existing-private", staff_b, fx["private_file_url"]
    )
    r_missing_file = get(
        "p2-http-file-oracle-nonexistent-private",
        staff_b,
        f"/private/files/does-not-exist-{secrets.token_hex(6)}.txt",
    )
    check(
        "p2-http-file-oracle-existing-private",
        r_unauth_file,
        r_unauth_file["status"] in (403, 404),
        "Unauthorized actor (Staff B after Lead moved to Team 2) is denied private file download",
        "staff.b@dev.local",
    )
    check(
        "p2-http-file-oracle-nonexistent-private",
        r_missing_file,
        r_missing_file["status"] == r_unauth_file["status"]
        and r_missing_file["text"] == r_unauth_file["text"],
        "Non-existent /private/files/* returns byte-identical denial (no File existence oracle)",
        "staff.b@dev.local",
    )

    final_summary = write_evidence(final=True)
    counts = final_summary["counts"]
    assert isinstance(counts, dict)
    print(
        "\nP2 HTTP probe summary: "
        f"total_records={counts['total_records']}, "
        f"verification_records={counts['verification_records']}, "
        f"setup_observation_records={counts['setup_observation_records']}, "
        f"asserted_cases={counts['asserted_cases']}, "
        f"passed_assertions={counts['passed_assertions']}, "
        f"failed_assertions={counts['failed_assertions']}, "
        f"unasserted_cases={counts['unasserted_cases']}, "
        f"probe_result={final_summary['probe_result']}"
    )
    return 0 if final_summary["probe_result"] == "PASS" else 1


def run_cli() -> int:
    try:
        return main()
    except Exception as error:
        _append_unique(failures, f"unhandled_exception:{type(error).__name__}")
        write_evidence(final=True)
        raise


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(run_cli())
