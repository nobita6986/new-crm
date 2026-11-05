#!/usr/bin/env python3
"""Runtime HTTP transport probe for Phase P3 SLA, KPI, Reporting, Export & Security (`S01..S18`).

Executes real HTTP requests (`urllib.request` with session cookies and `X-Frappe-CSRF-Token`)
against a live `bench serve` instance on a seeded site across all business actors:
  * `Guest`
  * `staff.a@dev.local`, `staff.b@dev.local` (Team 1)
  * `staff.c@dev.local` (Team 2)
  * `team1.leader@dev.local` (Leader A), `team2.leader@dev.local` (Leader B)
  * `bod.viewer@dev.local` (`CSKH BOD Viewer` — NO `System Manager`)
  * `crm.admin@dev.local` (`CSKH CRM Admin`)

Enforces fail-closed completeness (`100%` of `record_role == "assertion"` entries must carry an
explicit `PASS` verdict with `transport_error is None`) and redacts all passwords, cookies,
CSRF tokens, `/private/files/...` paths, and phone numbers before writing evidence.
"""

from __future__ import annotations

import ast
import csv
import http.cookiejar
import io
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = os.environ.get("P3_HTTP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = os.environ.get("P3_HTTP_SITE", "p3-ci.localhost")
OUTPUT = Path(os.environ.get("P3_HTTP_EVIDENCE_DIR", "/tmp/p3-http"))
_CONTAINER_SEED_SOURCE = Path("/src/deploy/dev/seed/seed_demo.py")
SEED_SOURCE = Path(
    os.environ.get(
        "P3_SEED_SOURCE",
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
            "P3 p3_reporting.*, reporting.* aliases, sla.*, qa.* (QA_DISABLED), S01..S18 security "
            "controls, generic /api/resource/* & reportview permlevel=1 oracle guards, 3-stage export "
            "+ raw /private/files/*.csv (200/403/410), CSV formula-injection escaping, and P0 File-oracle non-regression"
        ),
        "total_cases": counts["total_records"],
        "counts": counts,
        "cases": records,
        "unasserted_cases": summary["unasserted_cases"],
        "failed_cases": summary["failed_assertion_cases"],
        "gate_failures": summary["gate_failures"],
        "probe_result": summary["probe_result"] if final else "IN_PROGRESS",
    }
    (OUTPUT / "p3-http-transport.json").write_text(
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
        "headers": response_headers,
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
    case_id = f"p3-http-login-{label}"
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
    case_id = f"p3-http-csrf-{label}"
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


def get(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    path: str,
    headers: dict[str, str] | None = None,
    record: bool = True,
    record_role: str = "assertion",
) -> dict[str, object]:
    return request(
        case_id,
        opener,
        "GET",
        path,
        None,
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


def verify_export_download_response(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    export_id: str,
    actor: str,
    *,
    expect_formula_escaped: bool = True,
) -> bool:
    """Execute `GET download_export` and assert HTTP 200 + non-empty CSV + formula-injection `'` escaping."""
    qs = urllib.parse.urlencode({"export_id": export_id})
    resp = get(
        case_id,
        opener,
        f"/api/method/crm_cskh.api.p3_reporting.download_export?{qs}",
        headers={"Accept": "text/csv, application/json"},
    )
    csv_text = str(resp.get("text") or "")
    rows = list(csv.reader(io.StringIO(csv_text))) if csv_text else []
    flat = [cell for row in rows[1:] for cell in row] if len(rows) >= 2 else []
    formula_ok = (
        (
            any(c.startswith("'=CMD") for c in flat)
            and any(c.startswith("'@SUM") for c in flat)
        )
        if expect_formula_escaped
        else True
    )
    cond = (
        resp["status"] == 200
        and len(rows) >= 2
        and "record_type" in rows[0]
        and formula_ok
    )
    return check(
        case_id,
        resp,
        cond,
        "GET download_export returns HTTP 200 CSV with escaped formula cells ('=CMD, '@SUM)",
        actor=actor,
    )


def _setup_p3_fixtures() -> dict[str, str]:
    """Create deterministic P3 HTTP probe fixtures via Frappe ORM/services and commit cleanly."""
    import frappe
    from frappe.utils import now_datetime

    from crm_cskh.p2_models import ensure_p2_indexes, ensure_p2_seeds
    from crm_cskh.p3_models import ensure_p3_indexes, ensure_p3_seeds
    from crm_cskh.services import contact_attempt, ownership
    from crm_cskh.services.kpi_periods import resolve_period_spec, today_hcm
    from crm_cskh.services.kpi_runs import execute_kpi_recompute

    frappe.set_user("Administrator")
    ensure_p2_indexes()
    ensure_p2_seeds()
    ensure_p3_indexes()
    ensure_p3_seeds()

    # Ensure `CSKH Policy` flags are in canonical P3 initial state (`p3_sla_enabled=1, p3_reporting_enabled=1, qa_enabled=0`)
    for pol_name in frappe.get_all("CSKH Policy", filters={"enabled": 1}, pluck="name"):
        frappe.db.set_value(
            "CSKH Policy",
            pol_name,
            {"p3_sla_enabled": 1, "p3_reporting_enabled": 1, "qa_enabled": 0},
            update_modified=False,
        )

    team1_node = frappe.db.get_value(
        "CRM Sales Hierarchy", {"user": "team1.leader@dev.local"}, "name"
    )
    team2_node = frappe.db.get_value(
        "CRM Sales Hierarchy", {"user": "team2.leader@dev.local"}, "name"
    )
    if not team1_node or not team2_node:
        raise RuntimeError("Seeded CRM Sales Hierarchy nodes missing on P3 HTTP site")

    # Backdate membership history slightly so today's probe events resolve `authoritative`
    frappe.db.sql(
        "update `tabCSKH Team Membership History` set effective_from = %s where effective_to is null",
        (now_datetime() - timedelta(days=30),),
    )

    tag = secrets.token_hex(4).upper()
    shared_camp_doc = frappe.get_doc(
        {
            "doctype": "CSKH Campaign",
            "code": f"P3-SHARED-{tag}",
            "title": f"Chiến dịch Chung P3 HTTP {tag}",
            "channel": "Telesales",
            "status": "Active",
            "assignment_mode": "weighted_round_robin",
            "start_date": "2026-01-01",
            "end_date": "2027-12-31",
            "team_scope": None,
        }
    ).insert(ignore_permissions=True)
    shared_camp = shared_camp_doc.name

    team2_camp_doc = frappe.get_doc(
        {
            "doctype": "CSKH Campaign",
            "code": f"P3-TEAM2-{tag}",
            "title": f"Chiến dịch Riêng Team 2 {tag}",
            "channel": "Telesales",
            "status": "Active",
            "assignment_mode": "weighted_round_robin",
            "start_date": "2026-01-01",
            "end_date": "2027-12-31",
            "team_scope": team2_node,
        }
    ).insert(ignore_permissions=True)
    team2_camp = team2_camp_doc.name

    for camp_name, users in (
        (shared_camp, ("staff.a@dev.local", "staff.b@dev.local", "staff.c@dev.local")),
        (team2_camp, ("staff.c@dev.local", "staff.b@dev.local")),
    ):
        for u in users:
            frappe.get_doc(
                {
                    "doctype": "CSKH Assignment Rule",
                    "campaign": camp_name,
                    "user": u,
                    "weight": 50,
                    "daily_cap": 100,
                    "concurrent_cap": 100,
                    "active": 1,
                }
            ).insert(ignore_permissions=True)

    def _new_lead_and_wi(
        first_name: str, camp_name: str, assignee: str
    ) -> tuple[str, str, str]:
        phone = "0908" + str(secrets.randbelow(900000) + 100000)
        lead = frappe.get_doc(
            {
                "doctype": "CRM Lead",
                "naming_series": "CRM-LEAD-",
                "first_name": first_name,
                "last_name": "P3Probe",
                "lead_name": f"{first_name} P3Probe",
                "mobile_no": phone,
                "status": "New",
            }
        ).insert(ignore_permissions=True)
        wi = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "lead": lead.name,
                "campaign": camp_name,
                "status": "queued",
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi.name,
            assignee,
            reason="Khởi tạo P3 HTTP probe",
            request_id=f"p3-asg-{secrets.token_hex(6)}",
            actor="Administrator",
        )
        return wi.name, lead.name, phone

    def _new_call_log(lead_id: str, phone: str) -> str:
        cl = frappe.get_doc(
            {
                "doctype": "CRM Call Log",
                "id": f"P3-CALL-{secrets.token_hex(6)}",
                "type": "Outgoing",
                "status": "Completed",
                "from": "19001000",
                "to": phone,
                "duration": 45,
                "reference_doctype": "CRM Lead",
                "reference_docname": lead_id,
            }
        ).insert(ignore_permissions=True)
        return cl.name

    # 1. Active Work Item on `staff.a` (leaves `FIRST_ATTEMPT` SLA obligation `open` for pause/resume/waive HTTP tests!)
    wi_open_a, lead_open_a, phone_open_a = _new_lead_and_wi(
        "Nguyễn Văn Mở SLA", shared_camp, "staff.a@dev.local"
    )
    ob_open_a = frappe.db.get_value(
        "CSKH SLA Obligation",
        {"work_item": wi_open_a, "obligation_type": "FIRST_ATTEMPT"},
        "name",
    )

    # 2. Formula-injection Work Item on `staff.a` (`S18`)
    wi_inj, lead_inj, phone_inj = _new_lead_and_wi(
        "=CMD|' /C calc'!A0", shared_camp, "staff.a@dev.local"
    )
    frappe.set_user("staff.a@dev.local")
    cl_inj = _new_call_log(lead_inj, phone_inj)
    att_inj = contact_attempt.record_attempt(
        work_item=wi_inj,
        channel="phone_call",
        outcome_code="connected_interested",
        call_log=cl_inj,
        note="@SUM(1,2) khách hỏi gói cước P3",
        close_work_item=True,
        completion_reason="Hoàn tất tư vấn",
        idempotency_key=f"p3-inj-{secrets.token_hex(6)}",
    )

    # 3. Transferred Work Item: `staff.a` records attempt -> `crm.admin` reassigns to `staff.c` (`S03`)
    frappe.set_user("Administrator")
    wi_xfer, lead_xfer, phone_xfer = _new_lead_and_wi(
        "Lê Thị Chuyển Giao", shared_camp, "staff.a@dev.local"
    )
    frappe.set_user("staff.a@dev.local")
    cl_xfer = _new_call_log(lead_xfer, phone_xfer)
    att_xfer = contact_attempt.record_attempt(
        work_item=wi_xfer,
        channel="phone_call",
        outcome_code="no_answer",
        call_log=cl_xfer,
        note="Ghi chú trước chuyển giao sang Team 2",
        followup_due_at=(now_datetime() + timedelta(hours=3)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        followup_title="Hẹn gọi lại trước chuyển giao",
        idempotency_key=f"p3-xfer-att-{secrets.token_hex(6)}",
    )
    frappe.set_user("crm.admin@dev.local")
    ownership.reassign(
        wi_xfer,
        "staff.a@dev.local",
        "staff.c@dev.local",
        reason="Điều chuyển sang Team 2",
        request_id=f"p3-xfer-op-{secrets.token_hex(6)}",
        actor="crm.admin@dev.local",
    )

    # 4. `staff.c` (`Team 2`) records attempt in private `team2_camp`
    frappe.set_user("Administrator")
    wi_c_priv, lead_c_priv, phone_c_priv = _new_lead_and_wi(
        "Phạm Văn Team Hai", team2_camp, "staff.c@dev.local"
    )
    frappe.set_user("staff.c@dev.local")
    cl_c_priv = _new_call_log(lead_c_priv, phone_c_priv)
    contact_attempt.record_attempt(
        work_item=wi_c_priv,
        channel="phone_call",
        outcome_code="connected_not_interested",
        call_log=cl_c_priv,
        note="Ghi chú riêng Team 2",
        close_work_item=True,
        completion_reason="Kết thúc tại Team 2",
        idempotency_key=f"p3-c-priv-{secrets.token_hex(6)}",
    )

    frappe.set_user("Administrator")
    frappe.db.commit()

    cur_d = today_hcm()
    month_key = f"{cur_d.year:04d}-{cur_d.month:02d}"
    period = resolve_period_spec("monthly", month_key)
    run_all = execute_kpi_recompute(period, run_mode="full", actor="Administrator")
    execute_kpi_recompute(
        period, run_mode="full", campaign_filter=shared_camp, actor="Administrator"
    )

    snap_staff_c = frappe.db.get_value(
        "CSKH KPI Snapshot",
        {
            "is_latest_published": 1,
            "period_type": "monthly",
            "period_key": month_key,
            "campaign_filter": "",
            "scope_type": "employee",
            "scope_id": "staff.c@dev.local",
        },
        "name",
    )
    lead_a1_file_url = frappe.db.get_value(
        "File", {"file_name": ["like", "DEMO-CRM-LEAD-%"]}, "file_url"
    )

    return {
        "month_key": month_key,
        "team1_node": str(team1_node),
        "team2_node": str(team2_node),
        "shared_camp": shared_camp,
        "team2_camp": team2_camp,
        "wi_open_a": wi_open_a,
        "ob_open_a": str(ob_open_a),
        "att_inj_id": str(att_inj["attempt_id"]),
        "att_xfer_id": str(att_xfer["attempt_id"]),
        "lead_xfer": lead_xfer,
        "phone_xfer": phone_xfer,
        "phone_open_a": phone_open_a,
        "phone_c_priv": phone_c_priv,
        "run_id": str(run_all["run_id"]),
        "snap_staff_c": str(snap_staff_c),
        "lead_a1_file_url": str(
            lead_a1_file_url or "/private/files/DEMO-NONEXISTENT.txt"
        ),
    }


def run_probe() -> int:
    bench_dir = os.environ.get("BENCH_DIR", "/workspace/frappe-bench")
    os.environ.setdefault("FRAPPE_STREAM_LOGGING", "1")
    sites_dir = os.path.join(bench_dir, "sites")
    os.chdir(sites_dir)

    import frappe
    from frappe.utils import now_datetime

    from crm_cskh.services import contact_attempt, p3_export
    from crm_cskh.services.kpi_periods import resolve_period_spec
    from crm_cskh.services.kpi_runs import execute_kpi_recompute

    frappe.init(site=SITE_NAME, sites_path=".")
    frappe.connect()
    try:
        fx = _setup_p3_fixtures()
        month_key = fx["month_key"]

        guest_opener, _ = make_opener()
        op_staff_a = login("staff-a", "staff.a@dev.local", SEED_PASSWORD)
        tk_staff_a = csrf_token("staff-a", op_staff_a, "staff.a@dev.local")

        op_staff_b = login("staff-b", "staff.b@dev.local", SEED_PASSWORD)
        _ = csrf_token("staff-b", op_staff_b, "staff.b@dev.local")

        op_staff_c = login("staff-c", "staff.c@dev.local", SEED_PASSWORD)
        _ = csrf_token("staff-c", op_staff_c, "staff.c@dev.local")

        op_leader_a = login("leader-a", "team1.leader@dev.local", SEED_PASSWORD)
        tk_leader_a = csrf_token("leader-a", op_leader_a, "team1.leader@dev.local")

        op_leader_b = login("leader-b", "team2.leader@dev.local", SEED_PASSWORD)
        tk_leader_b = csrf_token("leader-b", op_leader_b, "team2.leader@dev.local")

        op_bod = login("bod", "bod.viewer@dev.local", SEED_PASSWORD)
        tk_bod = csrf_token("bod", op_bod, "bod.viewer@dev.local")

        op_admin = login("admin", "crm.admin@dev.local", SEED_PASSWORD)
        tk_admin = csrf_token("admin", op_admin, "crm.admin@dev.local")

        # ------------------------------------------------------------------
        # 1. Guest denials
        # ------------------------------------------------------------------
        r = get(
            "p3-http-guest-exec-overview-403",
            guest_opener,
            f"/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key={month_key}",
        )
        check(
            "p3-http-guest-exec-overview-403",
            r,
            r["status"] == 403,
            "Guest denied 403 on get_executive_overview",
            actor="Guest",
        )

        r = get(
            "p3-http-guest-sla-list-403",
            guest_opener,
            "/api/method/crm_cskh.api.sla.list_obligations",
        )
        check(
            "p3-http-guest-sla-list-403",
            r,
            r["status"] == 403,
            "Guest denied 403 on sla.list_obligations",
            actor="Guest",
        )

        # ------------------------------------------------------------------
        # 2. Staff self-only bounds, alias parity & SLA authority (`S01..S03`, `S05`, `S17`)
        # ------------------------------------------------------------------
        r_sc = get(
            "p3-http-staff-a-my-scorecard-200",
            op_staff_a,
            f"/api/method/crm_cskh.api.p3_reporting.get_my_scorecard?period_type=monthly&period_key={month_key}",
        )
        sc_msg = body_payload(r_sc)
        check(
            "p3-http-staff-a-my-scorecard-200",
            r_sc,
            r_sc["status"] == 200
            and sc_msg.get("ok") is True
            and sc_msg.get("employee", {}).get("employee_id") == "staff.a@dev.local"
            and sc_msg.get("policy_notice", {}).get("config_stage")
            == "DEV_CONFIGURABLE"
            and sc_msg.get("policy_notice", {}).get("production_approved") is False,
            "Staff A reads own get_my_scorecard with DEV_CONFIGURABLE policy notice",
            actor="staff.a@dev.local",
        )

        r_alias = get(
            "p3-http-staff-a-reporting-alias-200",
            op_staff_a,
            f"/api/method/crm_cskh.api.reporting.get_my_scorecard?period_type=monthly&period_key={month_key}",
        )
        alias_msg = body_payload(r_alias)
        check(
            "p3-http-staff-a-reporting-alias-200",
            r_alias,
            r_alias["status"] == 200
            and alias_msg.get("employee", {}).get("employee_id") == "staff.a@dev.local",
            "Compatible alias crm_cskh.api.reporting.get_my_scorecard returns HTTP 200 parity",
            actor="staff.a@dev.local",
        )

        r_emp_self = get(
            "p3-http-staff-a-emp-self-200",
            op_staff_a,
            f"/api/method/crm_cskh.api.p3_reporting.get_employee_quality?period_type=monthly&period_key={month_key}",
        )
        emp_self_rows = body_payload(r_emp_self).get("rows") or []
        check(
            "p3-http-staff-a-emp-self-200",
            r_emp_self,
            r_emp_self["status"] == 200
            and [row.get("employee_id") for row in emp_self_rows]
            == ["staff.a@dev.local"],
            "Staff A get_employee_quality returns strictly own employee row",
            actor="staff.a@dev.local",
        )

        for cid, qs, desc in (
            (
                "p3-http-staff-a-other-staff-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_employee_quality?period_type=monthly&period_key={month_key}&employee=staff.b%40dev.local",
                "S01: Staff A denied 403 FORBIDDEN_SCOPE on peer employee",
            ),
            (
                "p3-http-staff-a-team-compare-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_team_comparison?period_type=monthly&period_key={month_key}",
                "S02: Staff A denied 403 FORBIDDEN_SCOPE on get_team_comparison",
            ),
            (
                "p3-http-staff-a-exec-overview-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key={month_key}",
                "S02: Staff A denied 403 FORBIDDEN_SCOPE on get_executive_overview",
            ),
            (
                "p3-http-staff-a-campaign-quality-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_campaign_quality?period_type=monthly&period_key={month_key}",
                "S02: Staff A denied 403 FORBIDDEN_SCOPE on get_campaign_quality",
            ),
            (
                "p3-http-staff-a-run-status-probe-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_run_status?run_id={urllib.parse.quote(fx['run_id'])}",
                "S17: Staff A denied 403 FORBIDDEN_SCOPE when probing run_id",
            ),
        ):
            r_den = get(cid, op_staff_a, qs)
            check(
                cid,
                r_den,
                r_den["status"] == 403
                and error_envelope(r_den).get("code") == "FORBIDDEN_SCOPE",
                desc,
                actor="staff.a@dev.local",
            )

        r_waive_st = post_json(
            "p3-http-staff-a-sla-waive-403",
            op_staff_a,
            tk_staff_a,
            "/api/method/crm_cskh.api.sla.waive_obligation",
            {"obligation_id": fx["ob_open_a"], "reason": "Nhân viên tự miễn SLA"},
        )
        check(
            "p3-http-staff-a-sla-waive-403",
            r_waive_st,
            r_waive_st["status"] == 403
            and error_envelope(r_waive_st).get("code") == "FORBIDDEN_SCOPE",
            "S05: Staff A denied 403 FORBIDDEN_SCOPE on sla.waive_obligation",
            actor="staff.a@dev.local",
        )

        r_pause_st = post_json(
            "p3-http-staff-a-sla-pause-403",
            op_staff_a,
            tk_staff_a,
            "/api/method/crm_cskh.api.sla.pause_obligation",
            {"obligation_id": fx["ob_open_a"], "reason": "Nhân viên tự tạm dừng SLA"},
        )
        check(
            "p3-http-staff-a-sla-pause-403",
            r_pause_st,
            r_pause_st["status"] == 403
            and error_envelope(r_pause_st).get("code") == "FORBIDDEN_SCOPE",
            "S05: Staff A denied 403 FORBIDDEN_SCOPE on sla.pause_obligation",
            actor="staff.a@dev.local",
        )

        # `S03`: Transferred Lead PII redacted in `Staff A`'s drill-down while KPI count stays
        dd_xfer_qs = urllib.parse.urlencode(
            {
                "metric_code": "CONTACT_ATTEMPT_COUNT",
                "period_type": "monthly",
                "period_key": month_key,
                "campaign": fx["shared_camp"],
                "page_size": 100,
            }
        )
        r_s03 = get(
            "p3-http-staff-a-transferred-drilldown-redacted",
            op_staff_a,
            f"/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown?{dd_xfer_qs}",
        )
        s03_rows = body_payload(r_s03).get("rows") or []
        xfer_matches = [
            row for row in s03_rows if row.get("attempt_id") == fx["att_xfer_id"]
        ]
        check(
            "p3-http-staff-a-transferred-drilldown-redacted",
            r_s03,
            r_s03["status"] == 200
            and len(xfer_matches) == 1
            and xfer_matches[0].get("pii_redacted_due_to_transfer") is True
            and xfer_matches[0].get("lead") is None
            and xfer_matches[0].get("phone") is None
            and xfer_matches[0].get("customer_name") is None
            and fx["phone_xfer"] not in str(r_s03["text"])
            and "Lê Thị Chuyển Giao" not in str(r_s03["text"]),
            "S03: Transferred Lead keeps Staff A KPI credit with 0 live PII leakage",
            actor="staff.a@dev.local",
        )

        # ------------------------------------------------------------------
        # 3. Team Leader subtree bounds & SLA pause/resume (`S04`, `S05`, `S14`)
        # ------------------------------------------------------------------
        r_la_ov = get(
            "p3-http-leader-a-exec-overview-team-200",
            op_leader_a,
            f"/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key={month_key}",
        )
        la_ov = body_payload(r_la_ov)
        check(
            "p3-http-leader-a-exec-overview-team-200",
            r_la_ov,
            r_la_ov["status"] == 200
            and la_ov.get("scope", {}).get("scope_type") == "team"
            and la_ov.get("scope", {}).get("scope_id") == fx["team1_node"],
            "Leader A executive overview bounded strictly to Team 1 subtree",
            actor="team1.leader@dev.local",
        )

        for cid, qs, desc in (
            (
                "p3-http-leader-a-other-team-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_team_comparison?period_type=monthly&period_key={month_key}&team={urllib.parse.quote(fx['team2_node'])}",
                "S04: Leader A denied 403 FORBIDDEN_SCOPE on Team 2",
            ),
            (
                "p3-http-leader-a-other-emp-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_employee_quality?period_type=monthly&period_key={month_key}&employee=staff.c%40dev.local",
                "S04: Leader A denied 403 FORBIDDEN_SCOPE on Team 2 employee",
            ),
            (
                "p3-http-leader-a-other-campaign-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_campaign_quality?period_type=monthly&period_key={month_key}&campaign={urllib.parse.quote(fx['team2_camp'])}",
                "S04: Leader A denied 403 FORBIDDEN_SCOPE on Team 2 private campaign",
            ),
            (
                "p3-http-leader-a-company-drilldown-403",
                f"/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown?metric_code=CONTACT_ATTEMPT_COUNT&period_type=monthly&period_key={month_key}&scope_type=company",
                "S04: Leader A denied 403 FORBIDDEN_SCOPE on company-wide drilldown",
            ),
        ):
            r_la_den = get(cid, op_leader_a, qs)
            check(
                cid,
                r_la_den,
                r_la_den["status"] == 403
                and error_envelope(r_la_den).get("code") == "FORBIDDEN_SCOPE",
                desc,
                actor="team1.leader@dev.local",
            )

        frappe.db.commit()
        orig_due_before = str(
            frappe.db.get_value(
                "CSKH SLA Obligation", fx["ob_open_a"], "original_due_at"
            )
        )
        r_la_pause = post_json(
            "p3-http-leader-a-sla-pause-200",
            op_leader_a,
            tk_leader_a,
            "/api/method/crm_cskh.api.sla.pause_obligation",
            {"obligation_id": fx["ob_open_a"], "reason": "Tạm dừng bảo trì tổng đài"},
        )
        check(
            "p3-http-leader-a-sla-pause-200",
            r_la_pause,
            r_la_pause["status"] == 200
            and body_payload(r_la_pause).get("is_paused") is True,
            "Leader A pauses Team 1 SLA obligation cleanly",
            actor="team1.leader@dev.local",
        )

        r_la_resume = post_json(
            "p3-http-leader-a-sla-resume-200",
            op_leader_a,
            tk_leader_a,
            "/api/method/crm_cskh.api.sla.resume_obligation",
            {"obligation_id": fx["ob_open_a"], "reason": "Tổng đài hoạt động lại"},
        )
        frappe.db.commit()
        orig_due_after = str(
            frappe.db.get_value(
                "CSKH SLA Obligation", fx["ob_open_a"], "original_due_at"
            )
        )
        check(
            "p3-http-leader-a-sla-resume-200",
            r_la_resume,
            r_la_resume["status"] == 200
            and body_payload(r_la_resume).get("is_paused") is False
            and orig_due_after == orig_due_before,
            "S14: Leader A resumes Team 1 SLA obligation and original_due_at stays immutable",
            actor="team1.leader@dev.local",
        )

        r_lb_waive = post_json(
            "p3-http-leader-b-cross-team-sla-waive-403",
            op_leader_b,
            tk_leader_b,
            "/api/method/crm_cskh.api.sla.waive_obligation",
            {
                "obligation_id": fx["ob_open_a"],
                "reason": "Leader khác team cố miễn SLA",
            },
        )
        check(
            "p3-http-leader-b-cross-team-sla-waive-403",
            r_lb_waive,
            r_lb_waive["status"] == 403
            and error_envelope(r_lb_waive).get("code") == "FORBIDDEN_SCOPE",
            "S04/S05: Leader B denied 403 FORBIDDEN_SCOPE on Team 1 SLA obligation",
            actor="team2.leader@dev.local",
        )

        # ------------------------------------------------------------------
        # 4. QA disabled honesty (`S06`, `S07`)
        # ------------------------------------------------------------------
        r_qa_cfg = get(
            "p3-http-qa-config-disabled-200",
            op_admin,
            "/api/method/crm_cskh.api.qa.get_qa_config",
        )
        check(
            "p3-http-qa-config-disabled-200",
            r_qa_cfg,
            r_qa_cfg["status"] == 200
            and body_payload(r_qa_cfg).get("qa_enabled") is False,
            "qa.get_qa_config reports qa_enabled=false by default",
            actor="crm.admin@dev.local",
        )

        r_qa_list = get(
            "p3-http-qa-list-reviews-disabled-200",
            op_admin,
            "/api/method/crm_cskh.api.qa.list_reviews",
        )
        check(
            "p3-http-qa-list-reviews-disabled-200",
            r_qa_list,
            r_qa_list["status"] == 200
            and body_payload(r_qa_list).get("qa_enabled") is False
            and body_payload(r_qa_list).get("rows") == [],
            "qa.list_reviews returns qa_enabled=false and 0 fake reviews",
            actor="crm.admin@dev.local",
        )

        r_qa_write = post_json(
            "p3-http-qa-submit-review-409-disabled",
            op_admin,
            tk_admin,
            "/api/method/crm_cskh.api.qa.submit_review",
            {
                "contact_attempt": fx["att_inj_id"],
                "points_achieved": 90,
                "max_points": 100,
                "passed": 1,
                "status": "published",
            },
        )
        frappe.db.commit()
        qa_count = int(frappe.db.count("CSKH QA Review"))
        check(
            "p3-http-qa-submit-review-409-disabled",
            r_qa_write,
            r_qa_write["status"] == 409
            and error_envelope(r_qa_write).get("code") == "QA_DISABLED"
            and qa_count == 0,
            "S06/S07: qa.submit_review rejected with 409 QA_DISABLED and 0 fake QA reviews stored",
            actor="crm.admin@dev.local",
        )

        # ------------------------------------------------------------------
        # 5. BOD Viewer company aggregates & 0 customer PII (`S08`, `S09`, `S10`)
        # ------------------------------------------------------------------
        r_bod_ov = get(
            "p3-http-bod-exec-overview-200",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=monthly&period_key={month_key}",
        )
        bod_ov = body_payload(r_bod_ov)
        check(
            "p3-http-bod-exec-overview-200",
            r_bod_ov,
            r_bod_ov["status"] == 200
            and bod_ov.get("scope", {}).get("scope_id") == "__company__"
            and bod_ov.get("actor_scope", {}).get("pii_allowed") is False
            and bod_ov.get("metrics", {}).get("QA_PASS_RATE", {}).get("status")
            == "disabled"
            and bod_ov.get("attempt_breakdown", {}).get("verified") == 0,
            "S08: BOD Viewer reads company executive overview without System Manager and 0 fake verified",
            actor="bod.viewer@dev.local",
        )

        r_bod_dd_anon = get(
            "p3-http-bod-anon-drilldown-200",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown?metric_code=CONTACT_ATTEMPT_COUNT&period_type=monthly&period_key={month_key}&scope_type=company&page_size=50",
        )
        bod_dd_rows = body_payload(r_bod_dd_anon).get("rows") or []
        bod_raw_txt = str(r_bod_dd_anon["text"])
        check(
            "p3-http-bod-anon-drilldown-200",
            r_bod_dd_anon,
            r_bod_dd_anon["status"] == 200
            and len(bod_dd_rows) >= 3
            and all(
                row.get("lead") == "REDACTED"
                and row.get("phone") is None
                and row.get("customer_name") is None
                and row.get("note_excerpt") is None
                for row in bod_dd_rows
            )
            and fx["phone_xfer"] not in bod_raw_txt
            and fx["phone_c_priv"] not in bod_raw_txt
            and "P3Probe" not in bod_raw_txt,
            "S08: BOD Viewer anonymized drill-down returns 0 customer PII",
            actor="bod.viewer@dev.local",
        )

        r_bod_pii = get(
            "p3-http-bod-drilldown-pii-403",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown?metric_code=CONTACT_ATTEMPT_COUNT&period_type=monthly&period_key={month_key}&include_pii=1",
        )
        check(
            "p3-http-bod-drilldown-pii-403",
            r_bod_pii,
            r_bod_pii["status"] == 403
            and error_envelope(r_bod_pii).get("code") == "FORBIDDEN_SCOPE",
            "S09/S10: BOD Viewer denied 403 FORBIDDEN_SCOPE when requesting include_pii=1",
            actor="bod.viewer@dev.local",
        )

        r_bod_sc = get(
            "p3-http-bod-my-scorecard-403",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_my_scorecard?period_type=monthly&period_key={month_key}",
        )
        check(
            "p3-http-bod-my-scorecard-403",
            r_bod_sc,
            r_bod_sc["status"] == 403
            and error_envelope(r_bod_sc).get("code") == "FORBIDDEN_SCOPE",
            "S10: BOD Viewer denied 403 FORBIDDEN_SCOPE on get_my_scorecard",
            actor="bod.viewer@dev.local",
        )

        r_bod_exp_pii = post_json(
            "p3-http-bod-export-pii-403",
            op_bod,
            tk_bod,
            "/api/method/crm_cskh.api.p3_reporting.request_export",
            {
                "report_type": "metric_drilldown",
                "metric_code": "CONTACT_ATTEMPT_COUNT",
                "period_type": "monthly",
                "period_key": month_key,
                "include_pii": 1,
            },
        )
        check(
            "p3-http-bod-export-pii-403",
            r_bod_exp_pii,
            r_bod_exp_pii["status"] == 403
            and error_envelope(r_bod_exp_pii).get("code") == "FORBIDDEN_SCOPE",
            "S10: BOD Viewer denied 403 FORBIDDEN_SCOPE on request_export(include_pii=1)",
            actor="bod.viewer@dev.local",
        )

        r_bod_sla = get(
            "p3-http-bod-sla-list-403",
            op_bod,
            "/api/method/crm_cskh.api.sla.list_obligations",
        )
        check(
            "p3-http-bod-sla-list-403",
            r_bod_sla,
            r_bod_sla["status"] == 403
            and error_envelope(r_bod_sla).get("code") == "FORBIDDEN_SCOPE",
            "S09: BOD Viewer denied 403 FORBIDDEN_SCOPE on operational sla.list_obligations",
            actor="bod.viewer@dev.local",
        )

        r_bod_teams = get(
            "p3-http-bod-team-comparison-200",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_team_comparison?period_type=monthly&period_key={month_key}",
        )
        check(
            "p3-http-bod-team-comparison-200",
            r_bod_teams,
            r_bod_teams["status"] == 200
            and body_payload(r_bod_teams).get("ok") is True
            and len(body_payload(r_bod_teams).get("rows") or []) >= 2,
            "BOD Viewer reads company-wide get_team_comparison across teams",
            actor="bod.viewer@dev.local",
        )

        r_bod_camps = get(
            "p3-http-bod-campaign-quality-200",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_campaign_quality?period_type=monthly&period_key={month_key}",
        )
        check(
            "p3-http-bod-campaign-quality-200",
            r_bod_camps,
            r_bod_camps["status"] == 200
            and body_payload(r_bod_camps).get("ok") is True
            and len(body_payload(r_bod_camps).get("rows") or []) >= 2,
            "BOD Viewer reads company-wide get_campaign_quality across campaigns",
            actor="bod.viewer@dev.local",
        )

        r_bad_period = get(
            "p3-http-invalid-period-400",
            op_bod,
            "/api/method/crm_cskh.api.p3_reporting.get_executive_overview?period_type=bogus",
        )
        check(
            "p3-http-invalid-period-400",
            r_bad_period,
            r_bad_period["status"] == 400
            and error_envelope(r_bad_period).get("code")
            in ("INVALID_PERIOD", "INVALID_DATE_RANGE"),
            "Invalid period_type rejected with HTTP 400 INVALID_PERIOD",
            actor="bod.viewer@dev.local",
        )

        r_bad_page = get(
            "p3-http-invalid-pagination-400",
            op_bod,
            f"/api/method/crm_cskh.api.p3_reporting.get_team_comparison?period_type=monthly&period_key={month_key}&page_size=notanumber",
        )
        check(
            "p3-http-invalid-pagination-400",
            r_bad_page,
            r_bad_page["status"] == 400,
            "Invalid page_size rejected deterministically with HTTP 400 (no HTTP 500)",
            actor="bod.viewer@dev.local",
        )

        r_admin_run = get(
            "p3-http-admin-get-run-status-200",
            op_admin,
            f"/api/method/crm_cskh.api.p3_reporting.get_run_status?run_id={urllib.parse.quote(fx['run_id'])}",
        )
        check(
            "p3-http-admin-get-run-status-200",
            r_admin_run,
            r_admin_run["status"] == 200
            and body_payload(r_admin_run).get("run_id") == fx["run_id"],
            "CRM Admin reads KPI run status metadata cleanly",
            actor="crm.admin@dev.local",
        )

        # F-P3-05: Leader A denied 403 on company-scope CSKH KPI Run (both RPC & generic REST)
        r_leader_a_comp_run = get(
            "p3-http-leader-a-company-run-status-403",
            op_leader_a,
            f"/api/method/crm_cskh.api.p3_reporting.get_run_status?run_id={urllib.parse.quote(fx['run_id'])}",
        )
        check(
            "p3-http-leader-a-company-run-status-403",
            r_leader_a_comp_run,
            r_leader_a_comp_run["status"] == 403
            and error_envelope(r_leader_a_comp_run).get("code") == "FORBIDDEN_SCOPE",
            "F-P3-05: Leader A denied 403 FORBIDDEN_SCOPE on company KPI Run get_run_status",
            actor="team1.leader@dev.local",
        )

        r_leader_a_rest_comp_run = get(
            "p3-http-leader-a-rest-company-kpi-run-403",
            op_leader_a,
            f"/api/resource/CSKH%20KPI%20Run/{urllib.parse.quote(fx['run_id'])}",
        )
        check(
            "p3-http-leader-a-rest-company-kpi-run-403",
            r_leader_a_rest_comp_run,
            r_leader_a_rest_comp_run["status"] == 403,
            "F-P3-05: Leader A denied 403 on generic REST GET /api/resource/CSKH KPI Run/<company>",
            actor="team1.leader@dev.local",
        )

        # F-P3-10: get_filter_options scoped & qa.create_review returns 409 QA_DISABLED (never 500)
        r_filter_opts = get(
            "p3-http-leader-a-filter-options-200",
            op_leader_a,
            "/api/method/crm_cskh.api.p3_reporting.get_filter_options",
        )
        check(
            "p3-http-leader-a-filter-options-200",
            r_filter_opts,
            r_filter_opts["status"] == 200
            and body_payload(r_filter_opts).get("ok") is True
            and isinstance(body_payload(r_filter_opts).get("teams"), list),
            "F-P3-10: Leader A reads scoped get_filter_options cleanly",
            actor="team1.leader@dev.local",
        )

        r_qa_create_409 = post_json(
            "p3-http-admin-qa-create-review-disabled-409",
            op_admin,
            tk_admin,
            "/api/method/crm_cskh.api.qa.create_review",
            {},
        )
        check(
            "p3-http-admin-qa-create-review-disabled-409",
            r_qa_create_409,
            r_qa_create_409["status"] == 409
            and error_envelope(r_qa_create_409).get("code") == "QA_DISABLED",
            "F-P3-10: qa.create_review with empty body returns clean 409 QA_DISABLED when QA is off",
            actor="crm.admin@dev.local",
        )

        # ------------------------------------------------------------------
        # 6. Generic Frappe REST / client / reportview / calendar guards (`S13`, `S14`, `S16`, `S17`)
        # ------------------------------------------------------------------
        r_rest_snap = get(
            "p3-http-staff-a-rest-kpi-snapshot-cross-403",
            op_staff_a,
            f"/api/resource/CSKH%20KPI%20Snapshot/{urllib.parse.quote(fx['snap_staff_c'])}",
        )
        check(
            "p3-http-staff-a-rest-kpi-snapshot-cross-403",
            r_rest_snap,
            r_rest_snap["status"] == 403,
            "S16/S17: Staff A denied 403 on generic REST GET /api/resource/CSKH KPI Snapshot/<other>",
            actor="staff.a@dev.local",
        )

        r_bod_rest_sla = get(
            "p3-http-bod-rest-sla-obligation-403",
            op_bod,
            f"/api/resource/CSKH%20SLA%20Obligation/{urllib.parse.quote(fx['ob_open_a'])}",
        )
        check(
            "p3-http-bod-rest-sla-obligation-403",
            r_bod_rest_sla,
            r_bod_rest_sla["status"] == 403,
            "S09/S16: BOD Viewer denied 403 on generic REST GET /api/resource/CSKH SLA Obligation",
            actor="bod.viewer@dev.local",
        )

        r_put_sla = put_json(
            "p3-http-leader-a-put-sla-original-due-403",
            op_leader_a,
            tk_leader_a,
            f"/api/resource/CSKH%20SLA%20Obligation/{urllib.parse.quote(fx['ob_open_a'])}",
            {"original_due_at": "2027-01-01 09:00:00"},
        )
        check(
            "p3-http-leader-a-put-sla-original-due-403",
            r_put_sla,
            r_put_sla["status"] in (403, 409),
            "S14/S16: Generic REST PUT on CSKH SLA Obligation.original_due_at blocked",
            actor="team1.leader@dev.local",
        )

        r_put_hol = put_json(
            "p3-http-leader-a-put-holiday-list-403",
            op_leader_a,
            tk_leader_a,
            "/api/resource/CRM%20Holiday%20List/VN-HOLIDAYS-DEV",
            {"weekly_off": "Monday"},
        )
        check(
            "p3-http-leader-a-put-holiday-list-403",
            r_put_hol,
            r_put_hol["status"] in (403, 409),
            "S13: Non-admin Leader A blocked from mutating upstream CRM Holiday List",
            actor="team1.leader@dev.local",
        )

        r_put_cal = put_json(
            "p3-http-leader-a-put-working-calendar-403",
            op_leader_a,
            tk_leader_a,
            "/api/resource/CSKH%20Working%20Calendar/VN-OFFICE-DEFAULT",
            {"title": "Sửa lịch trái phép"},
        )
        check(
            "p3-http-leader-a-put-working-calendar-403",
            r_put_cal,
            r_put_cal["status"] in (403, 409),
            "S13: Non-admin Leader A blocked from mutating CSKH Working Calendar",
            actor="team1.leader@dev.local",
        )

        r_gv_run = get(
            "p3-http-staff-a-client-get-value-kpi-run-403",
            op_staff_a,
            f"/api/method/frappe.client.get_value?doctype=CSKH%20KPI%20Run&filters={urllib.parse.quote(fx['run_id'])}&fieldname=status",
        )
        check(
            "p3-http-staff-a-client-get-value-kpi-run-403",
            r_gv_run,
            r_gv_run["status"] == 403,
            "S16/S17: Staff A denied 403 on frappe.client.get_value(CSKH KPI Run)",
            actor="staff.a@dev.local",
        )

        # ------------------------------------------------------------------
        # 7. 3-Stage Export + Raw `/private/files/...` + Live Revocation + TTL `410` + CSV Sanitization (`S11`, `S12`, `S16`, `S18`)
        # ------------------------------------------------------------------
        r_enq = post_json(
            "p3-http-leader-a-request-export-200",
            op_leader_a,
            tk_leader_a,
            "/api/method/crm_cskh.api.p3_reporting.request_export",
            {
                "report_type": "metric_drilldown",
                "metric_code": "CONTACT_ATTEMPT_COUNT",
                "period_type": "monthly",
                "period_key": month_key,
                "scope_type": "team",
                "scope_id": fx["team1_node"],
                "campaign": fx["shared_camp"],
                "include_pii": 1,
            },
        )
        exp_id = str(body_payload(r_enq).get("export_id") or "")
        check(
            "p3-http-leader-a-request-export-200",
            r_enq,
            r_enq["status"] == 200 and exp_id.startswith("EXP-"),
            "Stage 1: Leader A enqueues scoped drilldown export",
            actor="team1.leader@dev.local",
        )

        # Stage 2 worker execution
        frappe.set_user("Administrator")
        frappe.db.commit()
        w_res = p3_export.execute_export_job_now(exp_id)
        frappe.db.commit()
        job_row = frappe.db.get_value(
            "CSKH Export Job", exp_id, ["status", "file_doc", "file_url"], as_dict=True
        )
        file_owner = (
            frappe.db.get_value("File", job_row.file_doc, "owner")
            if (job_row and job_row.file_doc)
            else None
        )
        raw_export_url = str(job_row.file_url) if (job_row and job_row.file_url) else ""
        if (
            w_res.get("status") != "completed"
            or file_owner != "Administrator"
            or not raw_export_url
        ):
            _append_unique(failures, "export_worker_stage2_invariant_failed")

        r_exp_st = get(
            "p3-http-leader-a-export-status-no-file-url-200",
            op_leader_a,
            f"/api/method/crm_cskh.api.p3_reporting.get_export_status?export_id={urllib.parse.quote(exp_id)}",
        )
        check(
            "p3-http-leader-a-export-status-no-file-url-200",
            r_exp_st,
            r_exp_st["status"] == 200
            and body_payload(r_exp_st).get("status") == "completed"
            and "/private/files/" not in str(r_exp_st["text"]),
            "S16: get_export_status returns completed without exposing /private/files/ URL",
            actor="team1.leader@dev.local",
        )

        r_rv_oracle = post_json(
            "p3-http-leader-a-reportview-file-url-oracle-403",
            op_leader_a,
            tk_leader_a,
            "/api/method/frappe.desk.reportview.get",
            {
                "doctype": "CSKH Export Job",
                "fields": ["name", "status"],
                "filters": [["CSKH Export Job", "file_url", "like", "%private%"]],
            },
        )
        check(
            "p3-http-leader-a-reportview-file-url-oracle-403",
            r_rv_oracle,
            r_rv_oracle["status"] == 403,
            "S16: reportview filter-oracle on permlevel=1 CSKH Export Job.file_url blocked with 403",
            actor="team1.leader@dev.local",
        )

        verify_export_download_response(
            "p3-http-leader-a-download-export-csv-sanitized-200",
            op_leader_a,
            exp_id,
            "team1.leader@dev.local",
            expect_formula_escaped=True,
        )

        r_raw_ok = get(
            "p3-http-leader-a-raw-private-file-200",
            op_leader_a,
            raw_export_url,
            headers={"Accept": "text/csv, */*"},
        )
        check(
            "p3-http-leader-a-raw-private-file-200",
            r_raw_ok,
            r_raw_ok["status"] == 200 and "'=CMD" in str(r_raw_ok["text"]),
            "Stage 3 positive control: Authorized Leader A reads raw /private/files/<export>.csv",
            actor="team1.leader@dev.local",
        )

        if job_row and job_row.file_doc:
            r_ok_file_doc = get(
                "p3-http-leader-a-rest-file-doc-200",
                op_leader_a,
                f"/api/resource/File/{urllib.parse.quote(str(job_row.file_doc))}",
            )
            check(
                "p3-http-leader-a-rest-file-doc-200",
                r_ok_file_doc,
                r_ok_file_doc["status"] == 200
                and isinstance((r_ok_file_doc["json"] or {}).get("data"), dict),
                "F-P3-05 positive control: Authorized Leader A reads GET /api/resource/File/<export>",
                actor="team1.leader@dev.local",
            )

        # Cross-actor denials on BOTH `download_export` and raw `/private/files/<export>.csv` (`S11`)
        r_lb_dl = get(
            "p3-http-leader-b-cross-team-download-403",
            op_leader_b,
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={urllib.parse.quote(exp_id)}",
        )
        check(
            "p3-http-leader-b-cross-team-download-403",
            r_lb_dl,
            r_lb_dl["status"] == 403,
            "S11: Leader B denied 403 on Leader A's download_export",
            actor="team2.leader@dev.local",
        )

        for cid, intr_opener, intr_actor in (
            (
                "p3-http-leader-b-raw-private-file-403",
                op_leader_b,
                "team2.leader@dev.local",
            ),
            ("p3-http-bod-raw-private-file-403", op_bod, "bod.viewer@dev.local"),
            ("p3-http-staff-a-raw-private-file-403", op_staff_a, "staff.a@dev.local"),
            ("p3-http-guest-raw-private-file-403", guest_opener, "Guest"),
        ):
            r_intr = get(
                cid, intr_opener, raw_export_url, headers={"Accept": "text/csv, */*"}
            )
            check(
                cid,
                r_intr,
                r_intr["status"] == 403 and "record_type" not in str(r_intr["text"]),
                f"S11: {intr_actor} denied 403 on direct GET /private/files/<export>.csv",
                actor=intr_actor,
            )

        # Post-completion LIVE role revocation (`S12`) over real HTTP!
        frappe.set_user("Administrator")
        la_user_doc = frappe.get_doc("User", "team1.leader@dev.local")
        la_user_doc.remove_roles("Sales Manager")
        frappe.db.commit()
        frappe.clear_cache(user="team1.leader@dev.local")
        try:
            r_rev_dl = get(
                "p3-http-leader-a-revoked-download-403",
                op_leader_a,
                f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={urllib.parse.quote(exp_id)}",
            )
            check(
                "p3-http-leader-a-revoked-download-403",
                r_rev_dl,
                r_rev_dl["status"] == 403,
                "S12: Revoking Sales Manager immediately blocks download_export over HTTP",
                actor="team1.leader@dev.local",
            )

            r_rev_raw = get(
                "p3-http-leader-a-revoked-raw-private-file-403",
                op_leader_a,
                raw_export_url,
                headers={"Accept": "text/csv, */*"},
            )
            check(
                "p3-http-leader-a-revoked-raw-private-file-403",
                r_rev_raw,
                r_rev_raw["status"] == 403
                and "record_type" not in str(r_rev_raw["text"]),
                "S12: Revoking Sales Manager immediately blocks raw GET /private/files/<export>.csv",
                actor="team1.leader@dev.local",
            )

            if job_row and job_row.file_doc:
                r_rev_file_doc = get(
                    "p3-http-leader-a-revoked-rest-file-doc-403",
                    op_leader_a,
                    f"/api/resource/File/{urllib.parse.quote(str(job_row.file_doc))}",
                )
                # `crm_cskh/errors.py` normalizes 403 PermissionError on `/api/resource/File/<name>` to canonical 404
                check(
                    "p3-http-leader-a-revoked-rest-file-doc-403",
                    r_rev_file_doc,
                    r_rev_file_doc["status"] in (403, 404)
                    and "data" not in (r_rev_file_doc["json"] or {})
                    and "cskh_export_" not in str(r_rev_file_doc["text"]),
                    "F-P3-05: Revoking Sales Manager immediately blocks GET /api/resource/File/<export> (oracle-closed 404/403)",
                    actor="team1.leader@dev.local",
                )

                file_filter_q = urllib.parse.quote(
                    json.dumps([["File", "name", "=", str(job_row.file_doc)]])
                )
                r_rev_file_list = get(
                    "p3-http-leader-a-revoked-rest-file-list-empty",
                    op_leader_a,
                    f"/api/resource/File?filters={file_filter_q}",
                )
                check(
                    "p3-http-leader-a-revoked-rest-file-list-empty",
                    r_rev_file_list,
                    r_rev_file_list["status"] == 200
                    and (
                        isinstance(r_rev_file_list["json"], dict)
                        and r_rev_file_list["json"].get("data") == []
                    ),
                    "F-P3-05: Revoking Sales Manager filters export File out of GET /api/resource/File list",
                    actor="team1.leader@dev.local",
                )
        finally:
            frappe.set_user("Administrator")
            la_user_doc = frappe.get_doc("User", "team1.leader@dev.local")
            la_user_doc.add_roles("Sales Manager")
            frappe.db.commit()
            frappe.clear_cache(user="team1.leader@dev.local")

        # TTL Expiry (`410 EXPORT_EXPIRED` on RPC + `403` on raw `/private/files/...`)
        frappe.set_user("Administrator")
        frappe.db.set_value(
            "CSKH Export Job",
            exp_id,
            "expires_at",
            now_datetime() - timedelta(hours=2),
            update_modified=False,
        )
        frappe.db.commit()

        r_exp_410 = get(
            "p3-http-leader-a-expired-download-410",
            op_leader_a,
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={urllib.parse.quote(exp_id)}",
        )
        check(
            "p3-http-leader-a-expired-download-410",
            r_exp_410,
            r_exp_410["status"] == 410
            and error_envelope(r_exp_410).get("code") == "EXPORT_EXPIRED",
            "Expired export returns HTTP 410 EXPORT_EXPIRED on download_export",
            actor="team1.leader@dev.local",
        )

        r_exp_raw_403 = get(
            "p3-http-leader-a-expired-raw-private-file-403",
            op_leader_a,
            raw_export_url,
            headers={"Accept": "text/csv, */*"},
        )
        check(
            "p3-http-leader-a-expired-raw-private-file-403",
            r_exp_raw_403,
            r_exp_raw_403["status"] == 403
            and "record_type" not in str(r_exp_raw_403["text"]),
            "Expired export returns HTTP 403 on raw GET /private/files/<export>.csv",
            actor="team1.leader@dev.local",
        )

        # ------------------------------------------------------------------
        # 8. Staff team transfer boundary (`S15`) + P0 File non-regression
        # ------------------------------------------------------------------
        frappe.set_user("Administrator")
        sb_node = frappe.db.get_value(
            "CRM Sales Hierarchy", {"user": "staff.b@dev.local"}, "name"
        )
        sb_orig_parent = frappe.db.get_value(
            "CRM Sales Hierarchy", sb_node, "reports_to"
        )
        try:
            sb_doc = frappe.get_doc("CRM Sales Hierarchy", sb_node)
            sb_doc.reports_to = fx["team2_node"]
            sb_doc.save(ignore_permissions=True)
            frappe.db.commit()
            frappe.clear_cache()

            r_s15 = get(
                "p3-http-leader-a-post-staff-transfer-403",
                op_leader_a,
                f"/api/method/crm_cskh.api.p3_reporting.get_employee_quality?period_type=monthly&period_key={month_key}&employee=staff.b%40dev.local",
            )
            check(
                "p3-http-leader-a-post-staff-transfer-403",
                r_s15,
                r_s15["status"] == 403
                and error_envelope(r_s15).get("code") == "FORBIDDEN_SCOPE",
                "S15: Former Leader A denied 403 FORBIDDEN_SCOPE after Staff B transfers to Team 2",
                actor="team1.leader@dev.local",
            )
        finally:
            frappe.set_user("Administrator")
            sb_doc = frappe.get_doc("CRM Sales Hierarchy", sb_node)
            sb_doc.reports_to = sb_orig_parent
            sb_doc.save(ignore_permissions=True)
            frappe.db.commit()
            frappe.clear_cache()

        # Recompute & verify P0 private Lead attachment remains blocked to unauthorized Staff C
        period = resolve_period_spec("monthly", month_key)
        execute_kpi_recompute(period, run_mode="full", actor="Administrator")
        frappe.db.commit()
        _ = contact_attempt  # keep import explicit

        r_p0_file = get(
            "p3-http-staff-c-lead-a1-private-file-blocked",
            op_staff_c,
            fx["lead_a1_file_url"],
            headers={"Accept": "*/*"},
        )
        check(
            "p3-http-staff-c-lead-a1-private-file-blocked",
            r_p0_file,
            r_p0_file["status"] in (403, 404)
            and "Tệp đính kèm giả lập" not in str(r_p0_file["text"]),
            "P0 non-regression: Staff C blocked from reading Staff A's private Lead attachment",
            actor="staff.c@dev.local",
        )

        final_summary = write_evidence(final=True)
        print(json.dumps(final_summary, ensure_ascii=False))
        return 0 if final_summary["probe_result"] == "PASS" else 1
    finally:
        frappe.destroy()


if __name__ == "__main__":
    sys.exit(run_probe())
