#!/usr/bin/env python3
"""Runtime HTTP evidence for the P1 endpoints (P1 §12.3, §12.4).

Unlike the P0 probe (`ci_http_transport_probe.py`), which proves the P0
transport rules, this probe proves the **P1** endpoints over **real HTTP**
(`bench serve`) with **real seeded actors** (`CSKH CRM Admin`, `CSKH BOD Viewer`,
`Sales Manager`, `Sales User` A & B, and `Guest`), recording for every case:

* the unique `id` and `evidence_class: "http"`,
* the HTTP method and path,
* the authenticated **actor**,
* the HTTP status and P1 error code,
* the positive non-empty data assertion or negative denial assertion.

The distinction from unit tests is explicit:

* `evidence_class: "http"` — requests went through the real HTTP server stack,
  session cookies and `X-Frappe-CSRF-Token` validation;
* in-process `crm_cskh.services.*` / `crm_cskh.api.*` calls inside unit tests
  belong to the `service_unit_test` class and are **not** counted here.

Zero passwords (plain or URL-encoded), session cookies, CSRF tokens, private
file URLs or phone numbers are written to the artifact.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = os.environ.get("P1_HTTP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SITE_NAME = os.environ.get("P1_HTTP_SITE", "p1-ci.localhost")
OUTPUT = Path(os.environ.get("P1_HTTP_EVIDENCE_DIR", "/tmp/p1-http"))
SEED_SOURCE = Path(
    os.environ.get("P1_SEED_SOURCE", "/src/deploy/dev/seed/seed_demo.py")
)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

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
    """Read the fake seed password as a literal without importing the seed."""
    import ast

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


def write_evidence() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "site": SITE_NAME,
        "base_url": BASE_URL,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
        "evidence_class": "http",
        "coverage_scope": (
            "direct HTTP against bench serve with real session cookies and CSRF validation "
            "across P1 health, campaigns, distribution, ownership, customers, dashboard and "
            "imports endpoints; reverse-proxy/TLS/CORS termination is outside this probe"
        ),
        "total_cases": len(records),
        "cases": records,
        "failed_cases": failures,
        "probe_result": "FAIL" if failures else "PASS",
    }
    (OUTPUT / "p1-http-transport.json").write_text(
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
    record: bool = True,
) -> dict[str, object]:
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
        "evidence_class": "http",
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
        write_evidence()
    return {
        "status": status,
        "json": parsed_response,
        "text": response_text,
        "entry": entry,
        "transport_error": transport_error,
    }


def body_payload(response: dict[str, object]) -> dict:
    """Unwrap Frappe's `message` envelope from a JSON response."""
    data = response.get("json")
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        return data["message"]
    return data if isinstance(data, dict) else {}


def check(
    case_id: str,
    response: dict[str, object],
    condition: bool,
    detail: str,
    actor: str = "",
) -> bool:
    passed = bool(condition) and response.get("transport_error") is None
    entry = response.get("entry")
    if isinstance(entry, dict):
        entry["assertion"] = {
            "expected": detail,
            "actor": actor,
            "result": "PASS" if passed else "FAIL",
        }
    if not passed:
        failures.append(case_id)
    print(f"{case_id}: {'PASS' if passed else 'FAIL'}")
    return passed


def login(label: str, username: str, password: str) -> urllib.request.OpenerDirector:
    opener, _ = make_opener()
    body = urllib.parse.urlencode({"usr": username, "pwd": password}).encode()
    case_id = f"p1-http-login-{label}"
    response = request(
        case_id,
        opener,
        "POST",
        "/api/method/login",
        body,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    data = response["json"]
    check(
        case_id,
        response,
        response["status"] == 200 and isinstance(data, dict),
        "POST /api/method/login returns HTTP 200",
        actor=username,
    )
    return opener


def csrf_token(
    label: str,
    opener: urllib.request.OpenerDirector,
    actor: str,
    path: str = "/app",
) -> str:
    """Acquire the session CSRF token over HTTP from `/app`."""
    case_id = f"p1-http-csrf-{label}"
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
) -> dict[str, object]:
    body = urllib.parse.urlencode(payload).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(case_id, opener, "POST", path, body, headers)


def get(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    path: str,
) -> dict[str, object]:
    return request(case_id, opener, "GET", path)


def setup_probe_fixtures() -> dict[str, str]:
    """Create isolated canonical leads, an active campaign, and a private CSV file."""
    try:
        import frappe
        from crm_cskh.services.phone_identity import normalize_vn_phone

        from crm_cskh.services import identity as identity_service
    except ImportError as error:
        print(f"p1-http-setup: frappe is not importable ({error})")
        return {}

    bench_dir = os.environ.get("BENCH_DIR", "/workspace/frappe-bench")
    sites_path = os.environ.get("SITES_PATH", os.path.join(bench_dir, "sites"))
    if os.path.isdir(sites_path):
        os.chdir(sites_path)
    frappe.init(site=SITE_NAME, sites_path=sites_path)
    frappe.connect()
    frappe.set_user("Administrator")

    def fresh_phone() -> str:
        while True:
            digits = f"{int(frappe.generate_hash(length=10), 16) % 100000000:08d}"
            candidate = f"09{digits}"
            canonical = normalize_vn_phone(candidate)
            if not frappe.db.exists(
                "CRM Lead", {"mobile_no": ["in", (candidate, canonical)]}
            ) and not frappe.db.exists(
                "CSKH Identity Index", {"normalized_phone": canonical}
            ):
                return candidate

    def create_canonical_lead(label: str) -> str:
        phone = fresh_phone()
        canonical = normalize_vn_phone(phone)
        lead = frappe.get_doc(
            {
                "doctype": "CRM Lead",
                "first_name": f"P1 HTTP {label}",
                "mobile_no": canonical,
                "status": "New",
            }
        )
        lead.insert(ignore_permissions=True)
        identity_service.claim_canonical_identity(canonical, lead.name)
        return lead.name

    lead_1 = create_canonical_lead("Target 1")
    lead_2 = create_canonical_lead("Target 2")
    pool_lead = create_canonical_lead("Unassigned Pool")

    code = f"P1-HTTP-{os.environ.get('GITHUB_RUN_ID', 'local')}-{frappe.generate_hash(length=4)}"
    campaign = frappe.get_doc(
        {
            "doctype": "CSKH Campaign",
            "code": code,
            "title": "P1 HTTP evidence",
            "status": "Active",
            "selection_json": json.dumps({"leads": [lead_1, lead_2]}),
        }
    )
    campaign.insert(ignore_permissions=True)

    csv_phones = [fresh_phone(), fresh_phone()]
    csv_bytes = ("\n".join(["Số điện thoại", *csv_phones]) + "\n").encode("utf-8")
    private_file = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"p1-http-import-{frappe.generate_hash(length=6)}.csv",
            "is_private": 1,
            "content": csv_bytes,
        }
    )
    private_file.insert(ignore_permissions=True)

    frappe.db.commit()
    frappe.destroy()
    return {
        "campaign": campaign.name,
        "pool_lead": pool_lead,
        "import_file": private_file.name,
    }


def run() -> int:
    if not ADMIN_PASSWORD:
        raise RuntimeError(
            "ADMIN_PASSWORD is not available in the bench container environment"
        )

    guest, _ = make_opener()
    admin = login("admin", "crm.admin@dev.local", SEED_PASSWORD)
    bod = login("bod", "bod.viewer@dev.local", SEED_PASSWORD)
    leader = login("leader", "team1.leader@dev.local", SEED_PASSWORD)
    staff_a = login("staff-a", "staff.a@dev.local", SEED_PASSWORD)
    staff_b = login("staff-b", "staff.b@dev.local", SEED_PASSWORD)

    admin_token = csrf_token("admin", admin, "crm.admin@dev.local")
    bod_token = csrf_token("bod", bod, "bod.viewer@dev.local")
    leader_token = csrf_token("leader", leader, "team1.leader@dev.local")
    staff_a_token = csrf_token("staff-a", staff_a, "staff.a@dev.local")
    staff_b_token = csrf_token("staff-b", staff_b, "staff.b@dev.local")

    # -- 1. Transport negatives: Guest and invalid CSRF -------------------
    guest_denied = get(
        "p1-http-guest-campaigns-denied",
        guest,
        "/api/method/crm_cskh.api.campaigns.list",
    )
    check(
        "p1-http-guest-campaigns-denied",
        guest_denied,
        guest_denied["status"] in (401, 403),
        "GET campaigns.list rejects unauthenticated Guest with HTTP 401/403",
        actor="Guest",
    )

    bad_csrf = post(
        "p1-http-csrf-invalid-denied",
        admin,
        "invalid-csrf-token-probe",
        "/api/method/crm_cskh.api.campaigns.save",
        {"code": "P1-BAD-CSRF", "title": "Rejected CSRF"},
    )
    check(
        "p1-http-csrf-invalid-denied",
        bad_csrf,
        bad_csrf["status"] in (400, 403),
        "POST campaigns.save with invalid CSRF token returns HTTP 400/403",
        actor="crm.admin@dev.local",
    )

    # -- 2. Health smoke --------------------------------------------------
    health = get(
        "p1-http-health-admin",
        admin,
        "/api/method/crm_cskh.api.health.ping",
    )
    check(
        "p1-http-health-admin",
        health,
        health["status"] == 200,
        "GET health.ping returns HTTP 200 for CSKH CRM Admin",
        actor="crm.admin@dev.local",
    )

    # -- 3. Campaigns: write denials (staff, BOD, leader) + admin write ---
    for label, actor_opener, token, actor_email in (
        ("staff", staff_a, staff_a_token, "staff.a@dev.local"),
        ("bod", bod, bod_token, "bod.viewer@dev.local"),
        ("leader", leader, leader_token, "team1.leader@dev.local"),
    ):
        cid = f"p1-http-campaigns-save-denied-{label}"
        denied = post(
            cid,
            actor_opener,
            token,
            "/api/method/crm_cskh.api.campaigns.save",
            {"code": f"P1-HTTP-DENY-{label.upper()}", "title": "denied"},
        )
        check(
            cid,
            denied,
            denied["status"] == 403,
            f"POST campaigns.save returns HTTP 403 for {actor_email}",
            actor=actor_email,
        )

    run_token = os.environ.get("GITHUB_RUN_ID") or f"{int(time.time())}"
    code = f"P1-HTTP-{run_token}-{secrets.token_hex(3)}"
    created = post(
        "p1-http-campaigns-save-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.campaigns.save",
        {"code": code, "title": "P1 HTTP evidence", "status": "Active"},
    )
    check(
        "p1-http-campaigns-save-admin",
        created,
        created["status"] == 200 and bool(body_payload(created).get("campaign")),
        "POST campaigns.save returns HTTP 200 and campaign id for CRM Admin",
        actor="crm.admin@dev.local",
    )

    campaigns_bod = get(
        "p1-http-campaigns-list-bod",
        bod,
        "/api/method/crm_cskh.api.campaigns.list",
    )
    bod_items = body_payload(campaigns_bod).get("items") or []
    check(
        "p1-http-campaigns-list-bod",
        campaigns_bod,
        campaigns_bod["status"] == 200 and len(bod_items) >= 1,
        "GET campaigns.list returns HTTP 200 with >= 1 campaign for CSKH BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    campaigns_leader = get(
        "p1-http-campaigns-list-leader",
        leader,
        "/api/method/crm_cskh.api.campaigns.list",
    )
    leader_items = body_payload(campaigns_leader).get("items") or []
    check(
        "p1-http-campaigns-list-leader",
        campaigns_leader,
        campaigns_leader["status"] == 200 and len(leader_items) >= 1,
        "GET campaigns.list returns HTTP 200 with >= 1 campaign for Sales Manager",
        actor="team1.leader@dev.local",
    )

    campaigns_denied_staff = get(
        "p1-http-campaigns-list-denied-staff",
        staff_a,
        "/api/method/crm_cskh.api.campaigns.list",
    )
    check(
        "p1-http-campaigns-list-denied-staff",
        campaigns_denied_staff,
        campaigns_denied_staff["status"] == 403,
        "GET campaigns.list returns HTTP 403 for plain Sales User",
        actor="staff.a@dev.local",
    )

    fixtures = setup_probe_fixtures()
    campaign = fixtures.get("campaign", "")
    import_file = fixtures.get("import_file", "")
    if not campaign or not import_file:
        failures.append("p1-http-fixture-setup")
        write_evidence()
        return 1

    # -- 4. Assignment rules + Distribution preview / execute / reconcile -
    rule_resp = post(
        "p1-http-save-rule-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.campaigns.save_rule",
        {"campaign": campaign, "user": "staff.a@dev.local", "weight": 100},
    )
    check(
        "p1-http-save-rule-admin",
        rule_resp,
        rule_resp["status"] == 200 and bool(body_payload(rule_resp).get("rule")),
        "POST campaigns.save_rule returns HTTP 200 for CRM Admin",
        actor="crm.admin@dev.local",
    )

    rules_bod = get(
        "p1-http-rules-bod",
        bod,
        f"/api/method/crm_cskh.api.campaigns.rules?campaign={campaign}",
    )
    check(
        "p1-http-rules-bod",
        rules_bod,
        rules_bod["status"] == 200
        and len(body_payload(rules_bod).get("eligible") or []) >= 1,
        "GET campaigns.rules returns HTTP 200 with >= 1 eligible assignee for BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    preview = post(
        "p1-http-distribution-preview-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.distribution.preview",
        {"campaign": campaign},
    )
    preview_id = str(body_payload(preview).get("preview_id") or "")
    check(
        "p1-http-distribution-preview-admin",
        preview,
        preview["status"] == 200
        and bool(preview_id)
        and int(body_payload(preview).get("summary", {}).get("eligible_count", 0)) == 2,
        "POST distribution.preview returns HTTP 200 with eligible_count == 2",
        actor="crm.admin@dev.local",
    )

    for label, actor_opener, token, actor_email in (
        ("staff", staff_a, staff_a_token, "staff.a@dev.local"),
        ("bod", bod, bod_token, "bod.viewer@dev.local"),
    ):
        cid = f"p1-http-distribution-execute-denied-{label}"
        denied_exec = post(
            cid,
            actor_opener,
            token,
            "/api/method/crm_cskh.api.distribution.execute",
            {"preview_id": preview_id},
        )
        check(
            cid,
            denied_exec,
            denied_exec["status"] == 403,
            f"POST distribution.execute returns HTTP 403 for {actor_email}",
            actor=actor_email,
        )

    executed = post(
        "p1-http-distribution-execute-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.distribution.execute",
        {"preview_id": preview_id},
    )
    exec_payload = body_payload(executed)
    run_id = str(exec_payload.get("run_id") or "")
    check(
        "p1-http-distribution-execute-admin",
        executed,
        executed["status"] == 200
        and bool(run_id)
        and int(exec_payload.get("created", 0)) == 2,
        "POST distribution.execute returns HTTP 200 and creates 2 work items",
        actor="crm.admin@dev.local",
    )

    run_status = get(
        "p1-http-distribution-get-run-admin",
        admin,
        f"/api/method/crm_cskh.api.distribution.get_run?run_id={run_id}",
    )
    check(
        "p1-http-distribution-get-run-admin",
        run_status,
        run_status["status"] == 200
        and body_payload(run_status).get("run_id") == run_id
        and body_payload(run_status).get("status") == "completed",
        "GET distribution.get_run returns HTTP 200 with status == completed",
        actor="crm.admin@dev.local",
    )

    reconcile = get(
        "p1-http-distribution-reconcile-admin",
        admin,
        f"/api/method/crm_cskh.api.distribution.reconcile?campaign={campaign}",
    )
    rec_payload = body_payload(reconcile)
    check(
        "p1-http-distribution-reconcile-admin",
        reconcile,
        reconcile["status"] == 200
        and int(rec_payload.get("active_claims", 0)) == 2
        and int(rec_payload.get("active_work_items", 0)) == 2,
        "GET distribution.reconcile returns HTTP 200 with 2 active claims and work items",
        actor="crm.admin@dev.local",
    )

    # -- 5. Scoped customers + Ownership transfer / history / revoke / assign
    my_list_a = get(
        "p1-http-customers-my-list-staff-a",
        staff_a,
        f"/api/method/crm_cskh.api.customers.my_list?campaign={campaign}",
    )
    staff_a_items = body_payload(my_list_a).get("items") or []
    check(
        "p1-http-customers-my-list-staff-a",
        my_list_a,
        my_list_a["status"] == 200 and len(staff_a_items) == 2,
        "GET customers.my_list returns HTTP 200 with the 2 assigned items for staff.a",
        actor="staff.a@dev.local",
    )

    my_list_b_before = get(
        "p1-http-customers-my-list-staff-b-before-transfer",
        staff_b,
        f"/api/method/crm_cskh.api.customers.my_list?campaign={campaign}",
    )
    check(
        "p1-http-customers-my-list-staff-b-before-transfer",
        my_list_b_before,
        my_list_b_before["status"] == 200
        and len(body_payload(my_list_b_before).get("items") or []) == 0,
        "GET customers.my_list for staff.b shows 0 items in campaign before transfer",
        actor="staff.b@dev.local",
    )

    target_work_item = (
        str(staff_a_items[0].get("work_item") or "") if staff_a_items else ""
    )

    for label, actor_opener, token, actor_email in (
        ("staff", staff_a, staff_a_token, "staff.a@dev.local"),
        ("bod", bod, bod_token, "bod.viewer@dev.local"),
    ):
        cid = f"p1-http-ownership-reassign-denied-{label}"
        denied_reassign = post(
            cid,
            actor_opener,
            token,
            "/api/method/crm_cskh.api.ownership.reassign",
            {
                "work_item": target_work_item,
                "expected_owner": "staff.a@dev.local",
                "target_user": "staff.b@dev.local",
                "reason": "Denied HTTP probe transfer",
                "request_id": f"http-deny-{label}-{secrets.token_hex(4)}",
            },
        )
        check(
            cid,
            denied_reassign,
            denied_reassign["status"] == 403,
            f"POST ownership.reassign returns HTTP 403 for {actor_email}",
            actor=actor_email,
        )

    reassigned = post(
        "p1-http-ownership-reassign-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.reassign",
        {
            "work_item": target_work_item,
            "expected_owner": "staff.a@dev.local",
            "target_user": "staff.b@dev.local",
            "reason": "HTTP probe transfer to staff.b",
            "request_id": f"http-reassign-{secrets.token_hex(4)}",
        },
    )
    check(
        "p1-http-ownership-reassign-admin",
        reassigned,
        reassigned["status"] == 200
        and body_payload(reassigned).get("status") == "reassigned"
        and body_payload(reassigned).get("owner") == "staff.b@dev.local",
        "POST ownership.reassign returns HTTP 200 and transfers work item to staff.b",
        actor="crm.admin@dev.local",
    )

    my_list_b_after = get(
        "p1-http-customers-my-list-staff-b-after-transfer",
        staff_b,
        f"/api/method/crm_cskh.api.customers.my_list?campaign={campaign}",
    )
    check(
        "p1-http-customers-my-list-staff-b-after-transfer",
        my_list_b_after,
        my_list_b_after["status"] == 200
        and len(body_payload(my_list_b_after).get("items") or []) == 1,
        "GET customers.my_list for staff.b shows 1 transferred item immediately",
        actor="staff.b@dev.local",
    )

    history_resp = get(
        "p1-http-ownership-history-admin",
        admin,
        f"/api/method/crm_cskh.api.ownership.history?work_item={target_work_item}",
    )
    check(
        "p1-http-ownership-history-admin",
        history_resp,
        history_resp["status"] == 200
        and int(body_payload(history_resp).get("total", 0)) >= 2,
        "GET ownership.history returns HTTP 200 with >= 2 events (assign + reassign)",
        actor="crm.admin@dev.local",
    )

    revoke_denied = post(
        "p1-http-ownership-revoke-denied-staff",
        staff_b,
        staff_b_token,
        "/api/method/crm_cskh.api.ownership.revoke",
        {
            "work_item": target_work_item,
            "expected_owner": "staff.b@dev.local",
            "reason": "Denied revoke",
            "request_id": f"http-revoke-deny-{secrets.token_hex(4)}",
        },
    )
    check(
        "p1-http-ownership-revoke-denied-staff",
        revoke_denied,
        revoke_denied["status"] == 403,
        "POST ownership.revoke returns HTTP 403 for Sales User",
        actor="staff.b@dev.local",
    )

    revoked = post(
        "p1-http-ownership-revoke-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.revoke",
        {
            "work_item": target_work_item,
            "expected_owner": "staff.b@dev.local",
            "reason": "HTTP probe revoke back to queued",
            "request_id": f"http-revoke-{secrets.token_hex(4)}",
        },
    )
    check(
        "p1-http-ownership-revoke-admin",
        revoked,
        revoked["status"] == 200 and body_payload(revoked).get("status") == "queued",
        "POST ownership.revoke returns HTTP 200 and sets status == queued",
        actor="crm.admin@dev.local",
    )

    pool_bod = get(
        "p1-http-customers-pool-list-bod",
        bod,
        f"/api/method/crm_cskh.api.customers.pool_list?campaign={campaign}",
    )
    check(
        "p1-http-customers-pool-list-bod",
        pool_bod,
        pool_bod["status"] == 200
        and len(body_payload(pool_bod).get("items") or []) >= 1,
        "GET customers.pool_list returns HTTP 200 with >= 1 revoked queued item for BOD",
        actor="bod.viewer@dev.local",
    )

    re_assigned = post(
        "p1-http-ownership-assign-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": target_work_item,
            "target_user": "staff.a@dev.local",
            "reason": "HTTP probe assign queued item",
            "request_id": f"http-assign-{secrets.token_hex(4)}",
        },
    )
    check(
        "p1-http-ownership-assign-admin",
        re_assigned,
        re_assigned["status"] == 200
        and body_payload(re_assigned).get("status") == "assigned"
        and body_payload(re_assigned).get("owner") == "staff.a@dev.local",
        "POST ownership.assign returns HTTP 200 and assigns queued item to staff.a",
        actor="crm.admin@dev.local",
    )

    # -- 6. Pool list denial & Dashboard: positive non-empty + staff denials
    pool_denied_staff = get(
        "p1-http-customers-pool-list-denied-staff",
        staff_a,
        "/api/method/crm_cskh.api.customers.pool_list",
    )
    check(
        "p1-http-customers-pool-list-denied-staff",
        pool_denied_staff,
        pool_denied_staff["status"] == 403,
        "GET customers.pool_list returns HTTP 403 for plain Sales User",
        actor="staff.a@dev.local",
    )

    for label, actor_opener, actor_email in (
        ("admin", admin, "crm.admin@dev.local"),
        ("bod", bod, "bod.viewer@dev.local"),
        ("leader", leader, "team1.leader@dev.local"),
    ):
        cid = f"p1-http-dashboard-distribution-{label}"
        dash = get(
            cid,
            actor_opener,
            f"/api/method/crm_cskh.api.dashboard.distribution?campaign={campaign}",
        )
        check(
            cid,
            dash,
            dash["status"] == 200 and isinstance(body_payload(dash).get("cards"), dict),
            f"GET dashboard.distribution returns HTTP 200 with cards for {actor_email}",
            actor=actor_email,
        )

    dash_campaigns_bod = get(
        "p1-http-dashboard-campaigns-bod",
        bod,
        "/api/method/crm_cskh.api.dashboard.campaigns",
    )
    check(
        "p1-http-dashboard-campaigns-bod",
        dash_campaigns_bod,
        dash_campaigns_bod["status"] == 200
        and len(body_payload(dash_campaigns_bod).get("campaigns") or []) >= 1,
        "GET dashboard.campaigns returns HTTP 200 with >= 1 campaign for BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    dash_denied_staff = get(
        "p1-http-dashboard-distribution-denied-staff",
        staff_a,
        "/api/method/crm_cskh.api.dashboard.distribution",
    )
    check(
        "p1-http-dashboard-distribution-denied-staff",
        dash_denied_staff,
        dash_denied_staff["status"] == 403,
        "GET dashboard.distribution returns HTTP 403 for plain Sales User",
        actor="staff.a@dev.local",
    )

    # -- 7. Imports: create_preview, confirm & get_status + role denials --
    for label, actor_opener, token, actor_email in (
        ("staff", staff_a, staff_a_token, "staff.a@dev.local"),
        ("bod", bod, bod_token, "bod.viewer@dev.local"),
    ):
        cid = f"p1-http-imports-create-preview-denied-{label}"
        imp_denied = post(
            cid,
            actor_opener,
            token,
            "/api/method/crm_cskh.api.imports.create_preview",
            {"file_name": import_file},
        )
        check(
            cid,
            imp_denied,
            imp_denied["status"] == 403,
            f"POST imports.create_preview returns HTTP 403 for {actor_email}",
            actor=actor_email,
        )

    imp_preview = post(
        "p1-http-imports-create-preview-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": import_file,
            "idempotency_key": f"http-imp-{secrets.token_hex(6)}",
        },
    )
    imp_batch = str(body_payload(imp_preview).get("batch") or "")
    check(
        "p1-http-imports-create-preview-admin",
        imp_preview,
        imp_preview["status"] == 200
        and bool(imp_batch)
        and int(body_payload(imp_preview).get("totals", {}).get("valid_rows", 0)) == 2,
        "POST imports.create_preview returns HTTP 200 with valid_rows == 2 for CRM Admin",
        actor="crm.admin@dev.local",
    )

    imp_confirm_denied = post(
        "p1-http-imports-confirm-denied-bod",
        bod,
        bod_token,
        "/api/method/crm_cskh.api.imports.confirm",
        {"batch": imp_batch},
    )
    check(
        "p1-http-imports-confirm-denied-bod",
        imp_confirm_denied,
        imp_confirm_denied["status"] == 403,
        "POST imports.confirm returns HTTP 403 for CSKH BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    imp_confirm = post(
        "p1-http-imports-confirm-admin",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.confirm",
        {"batch": imp_batch},
    )
    check(
        "p1-http-imports-confirm-admin",
        imp_confirm,
        imp_confirm["status"] == 200
        and body_payload(imp_confirm).get("status")
        in ("CONFIRMED", "QUEUED", "RUNNING", "COMPLETED"),
        "POST imports.confirm returns HTTP 200 and transitions batch status",
        actor="crm.admin@dev.local",
    )

    imp_status_admin = get(
        "p1-http-imports-get-status-admin",
        admin,
        f"/api/method/crm_cskh.api.imports.get_status?batch={imp_batch}",
    )
    check(
        "p1-http-imports-get-status-admin",
        imp_status_admin,
        imp_status_admin["status"] == 200
        and body_payload(imp_status_admin).get("batch") == imp_batch,
        "GET imports.get_status returns HTTP 200 and batch metadata for CRM Admin",
        actor="crm.admin@dev.local",
    )

    imp_status_denied_bod = get(
        "p1-http-imports-get-status-denied-bod",
        bod,
        f"/api/method/crm_cskh.api.imports.get_status?batch={imp_batch}",
    )
    check(
        "p1-http-imports-get-status-denied-bod",
        imp_status_denied_bod,
        imp_status_denied_bod["status"] == 403,
        "GET imports.get_status returns HTTP 403 for CSKH BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    write_evidence()
    print(f"P1 HTTP probe: {'FAIL' if failures else 'PASS'} ({len(records)} records)")
    return 1 if failures else 0


if __name__ == "__main__":
    if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run())
