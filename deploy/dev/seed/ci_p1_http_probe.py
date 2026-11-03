#!/usr/bin/env python3
"""Runtime HTTP evidence for the P1 endpoints (P1 §12.3, §12.4).

Unlike the P0 probe (`ci_http_transport_probe.py`), which proves the P0
transport rules, this probe proves the **P1** endpoints over **real HTTP**
(`bench serve`) and **real RQ background workers** (`bench worker`) with **real
seeded actors** (`CSKH CRM Admin`, `CSKH BOD Viewer`, `Sales Manager`,
`Sales User` A & B, and `Guest`), recording for every case:

* the unique `id` and `evidence_class: "http"`,
* the HTTP method and path,
* the authenticated **actor**,
* the HTTP status and P1 error code (`code` / `p1_code` / `exc_type`),
* the positive non-empty data assertion (cross-checked against SQL DB truth)
  or negative denial / File-oracle assertion.

The distinction from unit tests is explicit:

* `evidence_class: "http"` — requests went through the real HTTP server stack,
  session cookies and `X-Frappe-CSRF-Token` validation;
* in-process `crm_cskh.services.*` / `crm_cskh.api.*` calls inside unit tests
  belong to the `service_unit_test` class and are **not** counted here.

Zero passwords (plain or URL-encoded), session cookies, CSRF tokens, private
file URLs or phone numbers are written to the artifact.
"""

from __future__ import annotations

import csv
import http.cookiejar
import io
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
            "direct HTTP against bench serve + real RQ bench worker with session cookies "
            "and CSRF validation across P1 health, campaigns, distribution, ownership, "
            "customers, dashboard, imports and P0 File-oracle endpoints; reverse-proxy/"
            "TLS/CORS termination is outside this probe"
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
            "p1_code": parsed_response.get("p1_code")
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
    record: bool = True,
) -> dict[str, object]:
    body = urllib.parse.urlencode(payload).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(case_id, opener, "POST", path, body, headers, record=record)


def post_json(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    token: str,
    path: str,
    payload: dict[str, object],
    record: bool = True,
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Frappe-CSRF-Token"] = token
    return request(case_id, opener, "POST", path, body, headers, record=record)


def get(
    case_id: str,
    opener: urllib.request.OpenerDirector,
    path: str,
    record: bool = True,
) -> dict[str, object]:
    return request(case_id, opener, "GET", path, record=record)


def _with_frappe(fn):
    """Run a short read/fixture callback against MariaDB and close cleanly."""
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


def setup_probe_fixtures() -> dict[str, object]:
    """Create isolated canonical leads, campaigns, work items, and private CSV files."""

    def _create(frappe):
        from crm_cskh.services import identity as identity_service
        from crm_cskh.services import ownership
        from crm_cskh.services.phone_identity import normalize_vn_phone

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
            for attempt in range(4):
                frappe.db.commit()
                lead = frappe.get_doc(
                    {
                        "doctype": "CRM Lead",
                        "first_name": f"P1 HTTP {label}",
                        "mobile_no": canonical,
                        "status": "New",
                    }
                )
                try:
                    lead.insert(ignore_permissions=True)
                    identity_service.claim_canonical_identity(canonical, lead.name)
                    frappe.db.commit()
                    return lead.name
                except frappe.QueryDeadlockError:
                    frappe.db.rollback()
                    if attempt == 3:
                        raise
                    time.sleep(0.1)
            raise RuntimeError("unreachable")

        lead_1 = create_canonical_lead("Target 1")
        lead_2 = create_canonical_lead("Target 2")
        pool_lead = create_canonical_lead("Unassigned Pool")

        run_tag = f"{os.environ.get('GITHUB_RUN_ID', 'local')}-{frappe.generate_hash(length=4)}"
        campaign = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-HTTP-{run_tag}",
                "title": "P1 HTTP evidence",
                "status": "Active",
                "selection_json": json.dumps({"leads": [lead_1, lead_2]}),
            }
        )
        campaign.insert(ignore_permissions=True)

        # Small 2-row CSV for existing smoke checks.
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

        # Large 250-row CSV (`245` valid + `3` invalid <= 200 + `2` invalid > 200) for L-18/L-19/L-20.
        large_valid_phones = [fresh_phone() for _ in range(245)]
        large_valid_canonicals = [normalize_vn_phone(p) for p in large_valid_phones]
        invalid_row_numbers = {12, 95, 188, 215, 251}  # CSV row numbers 2..251
        large_rows: list[str] = ["Số điện thoại"]
        v_idx = 0
        for row_number in range(2, 252):
            if row_number in invalid_row_numbers:
                large_rows.append(f"bad-http-cell-{row_number}")
            else:
                large_rows.append(large_valid_phones[v_idx])
                v_idx += 1
        large_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"p1-http-import-250-{frappe.generate_hash(length=6)}.csv",
                "is_private": 1,
                "content": ("\n".join(large_rows) + "\n").encode("utf-8"),
            }
        )
        large_file.insert(ignore_permissions=True)

        # 8 assigned work items (with Active Claim + ToDo + DocShare + History) for L-21 boolean matrix.
        revoke_camp = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-HTTP-L21-{run_tag}",
                "title": "P1 HTTP L-21 boolean matrix",
                "status": "Active",
            }
        )
        revoke_camp.insert(ignore_permissions=True)
        l21_items: list[dict[str, str]] = []
        for idx in range(8):
            lead_name = create_canonical_lead(f"L21-{idx}")
            wi = frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": revoke_camp.name,
                    "lead": lead_name,
                    "status": "queued",
                }
            )
            wi.insert(ignore_permissions=True)
            frappe.get_doc(
                {
                    "doctype": "CSKH Active Claim",
                    "lead": lead_name,
                    "campaign": revoke_camp.name,
                    "work_item": wi.name,
                }
            ).insert(ignore_permissions=True)
            ownership.assign(
                wi.name,
                "staff.a@dev.local",
                actor="crm.admin@dev.local",
                reason="L-21 HTTP probe seed",
                request_id=f"http-l21-seed-{run_tag}-{idx}",
            )
            l21_items.append({"lead": lead_name, "work_item": wi.name})

        # Team1 & Team2 campaigns with unassigned `queued` (+claim), `revoked`, `closed` items for L-17 & L-22.
        team1_node = frappe.db.get_value(
            "CRM Sales Hierarchy", {"user": "team1.leader@dev.local"}, "name"
        )
        team2_node = frappe.db.get_value(
            "CRM Sales Hierarchy", {"user": "team2.leader@dev.local"}, "name"
        )
        camp_t1 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-HTTP-T1-{run_tag}",
                "title": "P1 HTTP Team1 scope",
                "status": "Active",
                "team_scope": team1_node,
            }
        )
        camp_t1.insert(ignore_permissions=True)
        camp_t2 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-HTTP-T2-{run_tag}",
                "title": "P1 HTTP Team2 scope",
                "status": "Active",
                "team_scope": team2_node,
            }
        )
        camp_t2.insert(ignore_permissions=True)

        for camp_doc, staff_email in (
            (camp_t1, "staff.a@dev.local"),
            (camp_t2, "staff.c@dev.local"),
        ):
            # Assigned + claim
            l_asg = create_canonical_lead(f"ScopeAsg-{camp_doc.name}")
            wi_asg = frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": camp_doc.name,
                    "lead": l_asg,
                    "status": "queued",
                }
            )
            wi_asg.insert(ignore_permissions=True)
            frappe.get_doc(
                {
                    "doctype": "CSKH Active Claim",
                    "lead": l_asg,
                    "campaign": camp_doc.name,
                    "work_item": wi_asg.name,
                }
            ).insert(ignore_permissions=True)
            ownership.assign(
                wi_asg.name,
                staff_email,
                actor="crm.admin@dev.local",
                reason="L-22 scope seed",
                request_id=f"http-l22-seed-{camp_doc.name}",
            )
            # Queued (unassigned) + Active Claim
            l_q = create_canonical_lead(f"ScopeQueued-{camp_doc.name}")
            wi_q = frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": camp_doc.name,
                    "lead": l_q,
                    "status": "queued",
                }
            )
            wi_q.insert(ignore_permissions=True)
            frappe.get_doc(
                {
                    "doctype": "CSKH Active Claim",
                    "lead": l_q,
                    "campaign": camp_doc.name,
                    "work_item": wi_q.name,
                }
            ).insert(ignore_permissions=True)
            # Unassigned `revoked` and `closed`
            for terminal_state in ("revoked", "closed"):
                l_term = create_canonical_lead(
                    f"Scope-{terminal_state}-{camp_doc.name}"
                )
                frappe.get_doc(
                    {
                        "doctype": "CSKH Work Item",
                        "campaign": camp_doc.name,
                        "lead": l_term,
                        "status": terminal_state,
                    }
                ).insert(ignore_permissions=True)

        # 20-lead pool for background RQ 50/30/20 distribution test (exact 10/6/4).
        rq_leads = [create_canonical_lead(f"RQ-{i}") for i in range(20)]
        rq_camp = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-HTTP-RQ20-{run_tag}",
                "title": "P1 HTTP RQ 20-lead distribution",
                "status": "Active",
                "selection_json": json.dumps({"leads": rq_leads}),
            }
        )
        rq_camp.insert(ignore_permissions=True)

        # Two private CSV files with custom headers for L-25 (name/source mapping, placeholder,
        # non-overwrite on canonical Lead reuse, and Import Batch <-> Lead pool filtering).
        l25_p_named = fresh_phone()
        l25_p_missing = fresh_phone()
        l25_p_b2 = fresh_phone()

        def _csv_bytes(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> bytes:
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="\n")
            writer.writerow(header)
            for r in rows:
                writer.writerow(r)
            return buf.getvalue().encode("utf-8")

        l25_b1_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"p1-http-l25-b1-{frappe.generate_hash(length=6)}.csv",
                "is_private": 1,
                "content": _csv_bytes(
                    ("SDT_KH", "TEN_KH", "KENH_KH"),
                    [
                        (l25_p_named, "Nguyễn Văn L25", "Zalo L25"),
                        (l25_p_missing, "", '=HYPERLINK("http://evil")'),
                        (l25_p_named, "Duplicate In File", "Other"),
                    ],
                ),
            }
        )
        l25_b1_file.insert(ignore_permissions=True)

        l25_b2_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": f"p1-http-l25-b2-{frappe.generate_hash(length=6)}.csv",
                "is_private": 1,
                "content": _csv_bytes(
                    ("SDT_KH", "TEN_KH", "KENH_KH"),
                    [
                        (
                            l25_p_named,
                            "ATTEMPT OVERWRITE NAME",
                            "ATTEMPT OVERWRITE SOURCE",
                        ),
                        (l25_p_b2, "<b>Trần Thị L25B</b>", "TikTok L25"),
                    ],
                ),
            }
        )
        l25_b2_file.insert(ignore_permissions=True)

        frappe.db.commit()
        return {
            "campaign": campaign.name,
            "pool_lead": pool_lead,
            "import_file": private_file.name,
            "large_import_file": large_file.name,
            "large_valid_canonicals": large_valid_canonicals,
            "large_invalid_rows": sorted(invalid_row_numbers),
            "l21_items": l21_items,
            "camp_t1": camp_t1.name,
            "camp_t2": camp_t2.name,
            "rq_campaign": rq_camp.name,
            "l25_batch1_file": l25_b1_file.name,
            "l25_batch2_file": l25_b2_file.name,
            "l25_canonicals": {
                "named": normalize_vn_phone(l25_p_named),
                "missing": normalize_vn_phone(l25_p_missing),
                "b2": normalize_vn_phone(l25_p_b2),
            },
            "admin_roles": frappe.get_roles("crm.admin@dev.local"),
        }

    return _with_frappe(_create)


def poll_until(fetch_fn, predicate, timeout_s: float = 45.0, interval_s: float = 0.4):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = fetch_fn()
        if predicate(last):
            return last
        time.sleep(interval_s)
    return last


def verify_l21_db_truth(items_expect: list[tuple[str, str, str]]) -> tuple[bool, str]:
    """Verify DB state for all 8 L-21 revoked/queued items."""

    def _inspect(frappe):
        for lead, wi, expected_status in items_expect:
            wi_row = frappe.db.get_value(
                "CSKH Work Item", wi, ["status", "assigned_to"], as_dict=True
            )
            if (
                not wi_row
                or wi_row.status != expected_status
                or wi_row.assigned_to is not None
            ):
                return False, f"work_item {wi} status/owner mismatch: {wi_row}"
            if frappe.db.get_value("CRM Lead", lead, "lead_owner") is not None:
                return False, f"lead {lead} still has lead_owner"
            if frappe.db.count("CSKH Active Claim", {"lead": lead}) != 0:
                return False, f"lead {lead} still has active claim"
            if (
                frappe.db.count(
                    "ToDo",
                    {
                        "reference_type": "CRM Lead",
                        "reference_name": lead,
                        "status": "Open",
                    },
                )
                != 0
            ):
                return False, f"lead {lead} still has Open ToDo"
            if (
                frappe.db.count(
                    "ToDo",
                    {
                        "reference_type": "CRM Lead",
                        "reference_name": lead,
                        "status": "Cancelled",
                    },
                )
                < 1
            ):
                return False, f"lead {lead} missing Cancelled ToDo"
            if (
                frappe.db.count(
                    "DocShare", {"share_doctype": "CRM Lead", "share_name": lead}
                )
                != 0
            ):
                return False, f"lead {lead} still has DocShare"
            events = frappe.get_all(
                "CSKH Assignment History",
                filters={"work_item": wi},
                fields=["action", "to_user", "ownership_end"],
                order_by="event_at asc",
            )
            if [e.action for e in events] != ["assign", "revoke"]:
                return False, f"work_item {wi} unexpected history actions: {events}"
            if events[0].ownership_end is None or events[1].to_user is not None:
                return False, f"work_item {wi} history interval/to_user invalid"
        return True, "all 8 items match DB truth"

    return _with_frappe(_inspect)


def verify_large_import_db_truth(
    batch_name: str, canonicals: list[str], expected_invalid_rows: list[int]
) -> tuple[bool, str]:
    """Verify 245 canonical leads in DB and 5 error rows (including >200) in private artifact."""

    def _inspect(frappe):
        batch_doc = frappe.get_doc("CSKH Import Batch", batch_name)
        id_count = frappe.db.count(
            "CSKH Identity Index", {"normalized_phone": ["in", canonicals]}
        )
        if id_count != 245:
            return False, f"expected 245 identities, found {id_count}"
        if not batch_doc.error_artifact:
            return False, "missing error_artifact on batch"
        art = frappe.get_doc("File", batch_doc.error_artifact)
        if int(art.is_private or 0) != 1:
            return False, "error_artifact is not private"
        raw = art.get_content()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(raw)))
        row_nums = sorted(int(r["row_number"]) for r in rows)
        if row_nums != sorted(expected_invalid_rows):
            return False, f"artifact row_numbers {row_nums} != {expected_invalid_rows}"
        return True, "245 identities and 5 artifact error rows verified"

    return _with_frappe(_inspect)


def verify_l25_db_truth(
    batch1_name: str, batch2_name: str, canonicals: dict[str, str]
) -> tuple[bool, str, dict[str, str]]:
    """Verify L-25 mapped Lead fields, placeholder flag, non-overwrite, and batch-lead links."""

    def _inspect(frappe):
        leads: dict[str, str] = {}
        for key, phone in canonicals.items():
            lead_id = frappe.db.get_value(
                "CSKH Identity Index", {"normalized_phone": phone}, "canonical_lead"
            )
            if not lead_id:
                return False, f"missing canonical_lead for {key}", {}
            leads[key] = str(lead_id)

        named = frappe.db.get_value(
            "CRM Lead",
            leads["named"],
            ["first_name", "lead_name", "cskh_is_placeholder_name", "source"],
            as_dict=True,
        )
        if (
            named.first_name != "Nguyễn Văn L25"
            or named.lead_name != "Nguyễn Văn L25"
            or int(named.cskh_is_placeholder_name or 0) != 0
            or named.source != "Zalo L25"
        ):
            return False, f"named lead mutated or wrong: {named}", leads

        missing = frappe.db.get_value(
            "CRM Lead",
            leads["missing"],
            ["first_name", "lead_name", "cskh_is_placeholder_name", "source"],
            as_dict=True,
        )
        if (
            missing.first_name != "Chưa rõ tên"
            or missing.lead_name != "Chưa rõ tên"
            or int(missing.cskh_is_placeholder_name or 0) != 1
            or missing.source != '\'=HYPERLINK("http://evil")'
        ):
            return False, f"placeholder/formula lead wrong: {missing}", leads

        b2 = frappe.db.get_value(
            "CRM Lead",
            leads["b2"],
            ["first_name", "lead_name", "cskh_is_placeholder_name", "source"],
            as_dict=True,
        )
        if (
            b2.first_name != "Trần Thị L25B"
            or b2.lead_name != "Trần Thị L25B"
            or int(b2.cskh_is_placeholder_name or 0) != 0
            or b2.source != "TikTok L25"
        ):
            return False, f"batch2 HTML-sanitized lead wrong: {b2}", leads

        wi_count = frappe.db.count(
            "CSKH Work Item", {"lead": ["in", list(leads.values())]}
        )
        if wi_count != 0:
            return False, f"expected 0 work items on L-25 leads, got {wi_count}", leads

        b1_links = {
            r.lead: r.outcome
            for r in frappe.get_all(
                "CSKH Import Batch Lead",
                filters={"import_batch": batch1_name},
                fields=["lead", "outcome"],
            )
        }
        b2_links = {
            r.lead: r.outcome
            for r in frappe.get_all(
                "CSKH Import Batch Lead",
                filters={"import_batch": batch2_name},
                fields=["lead", "outcome"],
            )
        }
        if b1_links != {leads["named"]: "created", leads["missing"]: "created"}:
            return False, f"unexpected batch1 links: {b1_links}", leads
        if b2_links != {leads["named"]: "reused", leads["b2"]: "created"}:
            return False, f"unexpected batch2 links: {b2_links}", leads

        return (
            True,
            "L-25 leads, sanitization, non-overwrite, and batch links match DB truth",
            leads,
        )

    return _with_frappe(_inspect)


def read_dashboard_sql_truth() -> dict[str, dict[str, int]]:
    """Compute exact SQL dashboard and pool counters for CRM Admin, BOD, and Team1 Leader."""

    def _query(frappe):
        global_cards = {
            "unique_customers": int(
                frappe.db.sql("select count(distinct lead) from `tabCSKH Work Item`")[
                    0
                ][0]
            ),
            "assigned_work_items": int(
                frappe.db.count("CSKH Work Item", {"status": "assigned"})
            ),
            "unassigned_work_items": int(
                frappe.db.count("CSKH Work Item", {"status": "queued"})
            ),
            "revoked_work_items": int(
                frappe.db.count("CSKH Work Item", {"status": "revoked"})
            ),
            "closed_work_items": int(
                frappe.db.count("CSKH Work Item", {"status": "closed"})
            ),
            "active_claims": int(
                frappe.db.sql(
                    """select count(*) from `tabCSKH Active Claim` ac
                       inner join `tabCSKH Work Item` wi on wi.name = ac.work_item
                       where wi.status in ('queued', 'assigned')"""
                )[0][0]
            ),
            "pool_total": int(
                frappe.db.sql(
                    "select count(*) from `tabCSKH Work Item` where status = 'queued' and assigned_to is null"
                )[0][0]
            ),
        }

        leader = "team1.leader@dev.local"
        t1_users = frappe.db.sql(
            """select `user` from `tabCRM Sales Hierarchy`
               where lft >= (select lft from `tabCRM Sales Hierarchy` where `user` = %s)
                 and rgt <= (select rgt from `tabCRM Sales Hierarchy` where `user` = %s)""",
            (leader, leader),
            pluck=True,
        )
        t1_camps = frappe.db.sql(
            """select name from `tabCSKH Campaign`
               where team_scope is null or team_scope in (
                 select name from `tabCRM Sales Hierarchy`
                 where lft >= (select lft from `tabCRM Sales Hierarchy` where `user` = %s)
                   and rgt <= (select rgt from `tabCRM Sales Hierarchy` where `user` = %s))""",
            (leader, leader),
            pluck=True,
        )
        u_ph = ",".join(["%s"] * len(t1_users))
        c_ph = ",".join(["%s"] * len(t1_camps))
        pred = f"(wi.assigned_to in ({u_ph}) or (wi.assigned_to is null and wi.campaign in ({c_ph})))"
        params = (*t1_users, *t1_camps)

        leader_cards = {
            "unique_customers": int(
                frappe.db.sql(
                    f"select count(distinct wi.lead) from `tabCSKH Work Item` wi where {pred}",
                    params,
                )[0][0]
            ),
            "assigned_work_items": int(
                frappe.db.sql(
                    f"select count(*) from `tabCSKH Work Item` wi where wi.status = 'assigned' and {pred}",
                    params,
                )[0][0]
            ),
            "unassigned_work_items": int(
                frappe.db.sql(
                    f"select count(*) from `tabCSKH Work Item` wi where wi.status = 'queued' and {pred}",
                    params,
                )[0][0]
            ),
            "revoked_work_items": int(
                frappe.db.sql(
                    f"select count(*) from `tabCSKH Work Item` wi where wi.status = 'revoked' and {pred}",
                    params,
                )[0][0]
            ),
            "closed_work_items": int(
                frappe.db.sql(
                    f"select count(*) from `tabCSKH Work Item` wi where wi.status = 'closed' and {pred}",
                    params,
                )[0][0]
            ),
            "active_claims": int(
                frappe.db.sql(
                    f"""select count(*) from `tabCSKH Active Claim` ac
                        inner join `tabCSKH Work Item` wi on wi.name = ac.work_item
                        where wi.status in ('queued', 'assigned') and {pred}""",
                    params,
                )[0][0]
            ),
        }
        return {"global": global_cards, "leader": leader_cards}

    return _with_frappe(_query)


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

    # -- 3. Campaigns: write denials (L-16 FORBIDDEN code) + admin write --
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
        env = error_envelope(denied)
        check(
            cid,
            denied,
            denied["status"] == 403
            and env.get("code") == "FORBIDDEN"
            and env.get("p1_code") == "FORBIDDEN",
            f"POST campaigns.save returns HTTP 403 FORBIDDEN for {actor_email}",
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
        campaigns_denied_staff["status"] == 403
        and error_envelope(campaigns_denied_staff).get("code") == "FORBIDDEN",
        "GET campaigns.list returns HTTP 403 FORBIDDEN for plain Sales User",
        actor="staff.a@dev.local",
    )

    fixtures = setup_probe_fixtures()
    campaign = str(fixtures.get("campaign") or "")
    import_file = str(fixtures.get("import_file") or "")
    large_import_file = str(fixtures.get("large_import_file") or "")
    large_valid_canonicals = list(fixtures.get("large_valid_canonicals") or [])
    large_invalid_rows = list(fixtures.get("large_invalid_rows") or [])
    l21_items = list(fixtures.get("l21_items") or [])
    rq_campaign = str(fixtures.get("rq_campaign") or "")
    l25_batch1_file = str(fixtures.get("l25_batch1_file") or "")
    l25_batch2_file = str(fixtures.get("l25_batch2_file") or "")
    l25_canonicals = dict(fixtures.get("l25_canonicals") or {})
    admin_roles = set(fixtures.get("admin_roles") or [])
    if (
        not campaign
        or not import_file
        or not large_import_file
        or len(l21_items) != 8
        or not l25_batch1_file
        or not l25_batch2_file
    ):
        failures.append("p1-http-fixture-setup")
        write_evidence()
        return 1

    # -- 4. Assignment rules + Background RQ Distribution -----------------
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
            denied_exec["status"] == 403
            and error_envelope(denied_exec).get("code") == "FORBIDDEN",
            f"POST distribution.execute returns HTTP 403 FORBIDDEN for {actor_email}",
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
        and exec_payload.get("status") in ("queued", "running", "completed")
        and int(exec_payload.get("total", 0)) == 2,
        "POST distribution.execute enqueues RQ job and returns early status (queued/running/completed)",
        actor="crm.admin@dev.local",
    )

    # Poll unrecorded until the real RQ worker finishes `run_id`, then record final `get_run`.
    poll_until(
        lambda: get(
            "poll-dist-1",
            admin,
            f"/api/method/crm_cskh.api.distribution.get_run?run_id={run_id}",
            record=False,
        ),
        lambda r: body_payload(r).get("status")
        in ("completed", "completed_with_errors", "failed"),
    )

    run_status = get(
        "p1-http-distribution-get-run-admin",
        admin,
        f"/api/method/crm_cskh.api.distribution.get_run?run_id={run_id}",
    )
    run_status_payload = body_payload(run_status)
    check(
        "p1-http-distribution-get-run-admin",
        run_status,
        run_status["status"] == 200
        and run_status_payload.get("run_id") == run_id
        and run_status_payload.get("status") == "completed"
        and int(run_status_payload.get("progress", 0)) == 2
        and int(run_status_payload.get("created", 0)) == 2,
        "GET distribution.get_run returns HTTP 200 with status == completed, progress == 2, created == 2",
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
            denied_reassign["status"] == 403
            and error_envelope(denied_reassign).get("code") == "FORBIDDEN",
            f"POST ownership.reassign returns HTTP 403 FORBIDDEN for {actor_email}",
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
        revoke_denied["status"] == 403
        and error_envelope(revoke_denied).get("code") == "FORBIDDEN",
        "POST ownership.revoke returns HTTP 403 FORBIDDEN for Sales User",
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
        pool_denied_staff["status"] == 403
        and error_envelope(pool_denied_staff).get("code") == "FORBIDDEN",
        "GET customers.pool_list returns HTTP 403 FORBIDDEN for plain Sales User",
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
        dash_denied_staff["status"] == 403
        and error_envelope(dash_denied_staff).get("code") == "FORBIDDEN",
        "GET dashboard.distribution returns HTTP 403 FORBIDDEN for plain Sales User",
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
            imp_denied["status"] == 403
            and error_envelope(imp_denied).get("code") == "FORBIDDEN",
            f"POST imports.create_preview returns HTTP 403 FORBIDDEN for {actor_email}",
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
        imp_confirm_denied["status"] == 403
        and error_envelope(imp_confirm_denied).get("code") == "FORBIDDEN",
        "POST imports.confirm returns HTTP 403 FORBIDDEN for CSKH BOD Viewer",
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
        in ("QUEUED", "RUNNING", "COMPLETED"),
        "POST imports.confirm moves batch straight to QUEUED (never stuck at CONFIRMED)",
        actor="crm.admin@dev.local",
    )

    poll_until(
        lambda: get(
            "poll-imp-1",
            admin,
            f"/api/method/crm_cskh.api.imports.get_status?batch={imp_batch}",
            record=False,
        ),
        lambda r: body_payload(r).get("status")
        in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"),
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
        and body_payload(imp_status_admin).get("batch") == imp_batch
        and body_payload(imp_status_admin).get("status") == "COMPLETED"
        and int(body_payload(imp_status_admin).get("created_rows", 0)) == 2,
        "GET imports.get_status returns HTTP 200 with status == COMPLETED and created_rows == 2",
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
        imp_status_denied_bod["status"] == 403
        and error_envelope(imp_status_denied_bod).get("code") == "FORBIDDEN",
        "GET imports.get_status returns HTTP 403 FORBIDDEN for CSKH BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    # -- 8. L-18 + L-19 + L-20: 250-row CSV first-time confirm -> RQ worker
    large_preview = post(
        "p1-http-l18-l19-l20-preview-250rows",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": large_import_file,
            "idempotency_key": f"http-imp-250-{secrets.token_hex(6)}",
        },
    )
    large_prev_body = body_payload(large_preview)
    large_batch = str(large_prev_body.get("batch") or "")
    check(
        "p1-http-l18-l19-l20-preview-250rows",
        large_preview,
        large_preview["status"] == 200
        and bool(large_batch)
        and large_prev_body.get("preview_truncated") is True
        and len(large_prev_body.get("preview") or []) == 200
        and int(large_prev_body.get("totals", {}).get("total_rows", 0)) == 250
        and int(large_prev_body.get("totals", {}).get("valid_rows", 0)) == 245
        and int(large_prev_body.get("totals", {}).get("error_rows", 0)) == 5,
        "POST imports.create_preview on 250 rows sets preview_truncated=True and classifies 245 valid + 5 error",
        actor="crm.admin@dev.local",
    )

    large_confirm = post(
        "p1-http-l18-first-confirm-250rows",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.confirm",
        {"batch": large_batch},
    )
    check(
        "p1-http-l18-first-confirm-250rows",
        large_confirm,
        large_confirm["status"] == 200
        and body_payload(large_confirm).get("status")
        in ("QUEUED", "RUNNING", "COMPLETED_WITH_ERRORS"),
        "Single first-time POST imports.confirm transitions batch to QUEUED for RQ worker",
        actor="crm.admin@dev.local",
    )

    poll_until(
        lambda: get(
            "poll-imp-250",
            admin,
            f"/api/method/crm_cskh.api.imports.get_status?batch={large_batch}",
            record=False,
        ),
        lambda r: body_payload(r).get("status")
        in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"),
        timeout_s=60.0,
    )

    large_final = get(
        "p1-http-l19-l20-completed-250rows",
        admin,
        f"/api/method/crm_cskh.api.imports.get_status?batch={large_batch}",
    )
    large_final_body = body_payload(large_final)
    large_db_ok, large_db_msg = verify_large_import_db_truth(
        large_batch, large_valid_canonicals, large_invalid_rows
    )
    check(
        "p1-http-l19-l20-completed-250rows",
        large_final,
        large_final["status"] == 200
        and large_final_body.get("status") == "COMPLETED_WITH_ERRORS"
        and int(large_final_body.get("created_rows", 0)) == 245
        and int(large_final_body.get("totals", {}).get("error_rows", 0)) == 5
        and bool(large_final_body.get("error_artifact"))
        and large_db_ok,
        f"250-row RQ import creates 245 leads, keeps error_rows==5, and writes 5 private artifact rows ({large_db_msg})",
        actor="crm.admin@dev.local",
    )

    # -- 9. L-21: Boolean matrix across JSON and x-www-form-urlencoded ----
    l21_matrix: list[tuple[str, str, object, str]] = [
        ("json-false-bool", "json", False, "revoked"),
        ("form-false-str", "form", "false", "revoked"),
        ("json-zero-int", "json", 0, "revoked"),
        ("form-zero-str", "form", "0", "revoked"),
        ("json-true-bool", "json", True, "queued"),
        ("form-true-str", "form", "true", "queued"),
        ("json-one-int", "json", 1, "queued"),
        ("form-one-str", "form", "1", "queued"),
    ]
    l21_expectations: list[tuple[str, str, str]] = []
    for idx, (slug, mode, raw_bool, expected_state) in enumerate(l21_matrix):
        item_info = l21_items[idx]
        cid = f"p1-http-l21-revoke-{slug}"
        req_payload: dict[str, object] = {
            "work_item": item_info["work_item"],
            "expected_owner": "staff.a@dev.local",
            "reason": f"L-21 HTTP {slug}",
            "request_id": f"http-l21-{slug}-{secrets.token_hex(4)}",
            "return_to_pool": raw_bool,
        }
        resp = (
            post_json(
                cid,
                admin,
                admin_token,
                "/api/method/crm_cskh.api.ownership.revoke",
                req_payload,
            )
            if mode == "json"
            else post(
                cid,
                admin,
                admin_token,
                "/api/method/crm_cskh.api.ownership.revoke",
                req_payload,
            )
        )
        check(
            cid,
            resp,
            resp["status"] == 200
            and body_payload(resp).get("status") == expected_state
            and body_payload(resp).get("owner") is None,
            f"POST ownership.revoke ({mode} return_to_pool={raw_bool!r}) -> status == {expected_state}",
            actor="crm.admin@dev.local",
        )
        l21_expectations.append(
            (item_info["lead"], item_info["work_item"], expected_state)
        )

    l21_db_ok, l21_db_msg = verify_l21_db_truth(l21_expectations)
    # Record a summary check paired with an invalid boolean rejection (`400 INVALID_REQUEST`).
    bad_bool_resp = post(
        "p1-http-l21-revoke-invalid-bool-rejected",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.revoke",
        {
            "work_item": target_work_item,
            "expected_owner": "staff.a@dev.local",
            "reason": "Invalid boolean probe",
            "request_id": f"http-l21-bad-{secrets.token_hex(4)}",
            "return_to_pool": "not-a-boolean",
        },
    )
    check(
        "p1-http-l21-revoke-invalid-bool-rejected",
        bad_bool_resp,
        bad_bool_resp["status"] == 400
        and error_envelope(bad_bool_resp).get("code") == "INVALID_REQUEST"
        and l21_db_ok,
        f"Invalid return_to_pool fails with 400 INVALID_REQUEST and all 8 revoked/queued items match DB ({l21_db_msg})",
        actor="crm.admin@dev.local",
    )

    # -- 10. L-17 & L-22: CRM-ADMIN, BOD & Leader SQL-reconciled dashboards
    sql_truth = read_dashboard_sql_truth()
    global_sql = sql_truth["global"]
    leader_sql = sql_truth["leader"]

    admin_dash_all = get(
        "p1-http-l17-dashboard-admin-matches-db",
        admin,
        "/api/method/crm_cskh.api.dashboard.distribution",
    )
    admin_dash_body = body_payload(admin_dash_all)
    admin_cards = admin_dash_body.get("cards") or {}
    check(
        "p1-http-l17-dashboard-admin-matches-db",
        admin_dash_all,
        admin_dash_all["status"] == 200
        and "System Manager" not in admin_roles
        and "CSKH CRM Admin" in admin_roles
        and admin_dash_body.get("scope") == "system"
        and int(admin_cards.get("assigned_work_items", 0)) > 0
        and int(admin_cards.get("unassigned_work_items", 0)) > 0
        and int(admin_cards.get("revoked_work_items", 0)) > 0
        and int(admin_cards.get("closed_work_items", 0)) > 0
        and all(
            int(admin_cards.get(k, -1)) == global_sql[k]
            for k in (
                "unique_customers",
                "assigned_work_items",
                "unassigned_work_items",
                "revoked_work_items",
                "closed_work_items",
                "active_claims",
            )
        ),
        "CRM Admin (without System Manager) receives scope=system with non-zero cards matching SQL DB truth",
        actor="crm.admin@dev.local",
    )

    admin_pool_all = get(
        "p1-http-l17-pool-list-admin-matches-db",
        admin,
        "/api/method/crm_cskh.api.customers.pool_list?page_size=100",
    )
    admin_pool_body = body_payload(admin_pool_all)
    check(
        "p1-http-l17-pool-list-admin-matches-db",
        admin_pool_all,
        admin_pool_all["status"] == 200
        and admin_pool_body.get("scope") == "system"
        and int(admin_pool_body.get("total", 0)) > 0
        and int(admin_pool_body.get("total", -1)) == global_sql["pool_total"],
        "CRM Admin pool_list returns scope=system and non-zero total matching SQL DB truth",
        actor="crm.admin@dev.local",
    )

    bod_dash_all = get(
        "p1-http-l22-dashboard-bod-matches-db",
        bod,
        "/api/method/crm_cskh.api.dashboard.distribution",
    )
    bod_cards = body_payload(bod_dash_all).get("cards") or {}
    check(
        "p1-http-l22-dashboard-bod-matches-db",
        bod_dash_all,
        bod_dash_all["status"] == 200
        and body_payload(bod_dash_all).get("scope") == "all"
        and int(bod_cards.get("unique_customers", -1)) == global_sql["unique_customers"]
        and int(bod_cards.get("unique_customers", -1))
        == int(admin_cards.get("unique_customers", -2)),
        "BOD dashboard shares unified unique_customers == COUNT(DISTINCT wi.lead) with CRM Admin",
        actor="bod.viewer@dev.local",
    )

    leader_dash_all = get(
        "p1-http-l22-dashboard-leader-matches-db",
        leader,
        "/api/method/crm_cskh.api.dashboard.distribution",
    )
    leader_dash_body = body_payload(leader_dash_all)
    leader_cards = leader_dash_body.get("cards") or {}
    check(
        "p1-http-l22-dashboard-leader-matches-db",
        leader_dash_all,
        leader_dash_all["status"] == 200
        and leader_dash_body.get("scope") == "team"
        and int(leader_cards.get("revoked_work_items", 0)) > 0
        and int(leader_cards.get("closed_work_items", 0)) > 0
        and int(leader_cards.get("active_claims", 0)) > 0
        and int(leader_cards.get("revoked_work_items", 0))
        < global_sql["revoked_work_items"]
        and all(
            int(leader_cards.get(k, -1)) == leader_sql[k]
            for k in (
                "unique_customers",
                "assigned_work_items",
                "unassigned_work_items",
                "revoked_work_items",
                "closed_work_items",
                "active_claims",
            )
        ),
        "Team1 Leader dashboard counts unassigned revoked/closed/claims in readable campaigns and matches SQL DB truth",
        actor="team1.leader@dev.local",
    )

    # -- 11. Item 5: 20-lead 50/30/20 RQ background distribution + double-submit
    for staff_email, w in (
        ("staff.a@dev.local", 50),
        ("staff.b@dev.local", 30),
        ("staff.c@dev.local", 20),
    ):
        post(
            f"setup-rq-rule-{staff_email}",
            admin,
            admin_token,
            "/api/method/crm_cskh.api.campaigns.save_rule",
            {"campaign": rq_campaign, "user": staff_email, "weight": w, "version": 1},
            record=False,
        )

    rq_preview = post(
        "p1-http-rq20-distribution-preview",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.distribution.preview",
        {"campaign": rq_campaign},
    )
    rq_preview_id = str(body_payload(rq_preview).get("preview_id") or "")
    check(
        "p1-http-rq20-distribution-preview",
        rq_preview,
        rq_preview["status"] == 200
        and bool(rq_preview_id)
        and int(body_payload(rq_preview).get("summary", {}).get("eligible_count", 0))
        == 20,
        "POST distribution.preview prepares 20-lead 50/30/20 campaign",
        actor="crm.admin@dev.local",
    )

    rq_exec_1 = post(
        "p1-http-rq20-distribution-execute-first",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.distribution.execute",
        {"preview_id": rq_preview_id},
    )
    rq_exec_2 = post(
        "p1-http-rq20-distribution-execute-double-submit",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.distribution.execute",
        {"preview_id": rq_preview_id},
    )
    rq_run_id = str(body_payload(rq_exec_1).get("run_id") or "")
    check(
        "p1-http-rq20-distribution-execute-first",
        rq_exec_1,
        rq_exec_1["status"] == 200
        and bool(rq_run_id)
        and body_payload(rq_exec_1).get("status") in ("queued", "running", "completed")
        and int(body_payload(rq_exec_1).get("total", 0)) == 20,
        "POST distribution.execute accepts 20-lead RQ job and returns run_id + early status",
        actor="crm.admin@dev.local",
    )
    check(
        "p1-http-rq20-distribution-execute-double-submit",
        rq_exec_2,
        rq_exec_2["status"] == 200
        and body_payload(rq_exec_2).get("run_id") == rq_run_id,
        "Double-submit POST distribution.execute is idempotent and returns the same run_id",
        actor="crm.admin@dev.local",
    )

    poll_until(
        lambda: get(
            "poll-rq20",
            admin,
            f"/api/method/crm_cskh.api.distribution.get_run?run_id={rq_run_id}",
            record=False,
        ),
        lambda r: body_payload(r).get("status")
        in ("completed", "completed_with_errors", "failed"),
        timeout_s=60.0,
    )

    rq_done = get(
        "p1-http-rq20-distribution-get-run-completed",
        admin,
        f"/api/method/crm_cskh.api.distribution.get_run?run_id={rq_run_id}",
    )
    rq_done_body = body_payload(rq_done)
    check(
        "p1-http-rq20-distribution-get-run-completed",
        rq_done,
        rq_done["status"] == 200
        and rq_done_body.get("status") == "completed"
        and int(rq_done_body.get("progress", 0)) == 20
        and int(rq_done_body.get("created", 0)) == 20
        and int(rq_done_body.get("errors", -1)) == 0
        and rq_done_body.get("assigned")
        == {
            "staff.a@dev.local": 10,
            "staff.b@dev.local": 6,
            "staff.c@dev.local": 4,
        },
        "RQ worker completes 20-lead distribution with exact 10/6/4 (50/30/20) fairness and zero duplicates",
        actor="crm.admin@dev.local",
    )

    # -- 12. L-16: NOT_FOUND error code + P0 File-oracle byte-identical 404
    not_found_run = get(
        "p1-http-l16-not-found-code",
        admin,
        "/api/method/crm_cskh.api.distribution.get_run?run_id=NONEXISTENT-RUN-9999",
    )
    check(
        "p1-http-l16-not-found-code",
        not_found_run,
        not_found_run["status"] == 404
        and error_envelope(not_found_run).get("code") == "NOT_FOUND"
        and error_envelope(not_found_run).get("p1_code") == "NOT_FOUND",
        "GET distribution.get_run on missing run returns HTTP 404 with stable code=NOT_FOUND",
        actor="crm.admin@dev.local",
    )

    file_oracle_real = get(
        "p1-http-l16-file-oracle-real-private",
        staff_b,
        f"/api/resource/File/{import_file}",
    )
    file_oracle_missing = get(
        "p1-http-l16-file-oracle-missing",
        staff_b,
        "/api/resource/File/deadbeef00missing",
    )
    check(
        "p1-http-l16-file-oracle-real-private",
        file_oracle_real,
        file_oracle_real["status"] == 404,
        "GET /api/resource/File/<real_private> returns HTTP 404 for unauthorized Sales User",
        actor="staff.b@dev.local",
    )
    check(
        "p1-http-l16-file-oracle-missing",
        file_oracle_missing,
        file_oracle_missing["status"] == 404
        and file_oracle_real["text"] == file_oracle_missing["text"]
        and import_file not in str(file_oracle_real["text"])
        and "deadbeef00missing" not in str(file_oracle_missing["text"]),
        "P0 File-oracle protection remains byte-identical on real vs missing File routes alongside L-16",
        actor="staff.b@dev.local",
    )

    # -- 13. L-25: Import mapping, non-overwrite, batch↔lead link & customer_pool
    l25_mapping = {"phone": "SDT_KH", "name": "TEN_KH", "source": "KENH_KH"}
    l25_b1_idem = f"http-l25-b1-{secrets.token_hex(4)}"
    l25_b1_prev = post_json(
        "p1-http-l25-batch1-preview",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": l25_batch1_file,
            "mapping": l25_mapping,
            "idempotency_key": l25_b1_idem,
        },
    )
    l25_b1_prev_body = body_payload(l25_b1_prev)
    l25_b1_name = str(l25_b1_prev_body.get("batch") or "")
    l25_b1_rows = list(l25_b1_prev_body.get("preview") or [])
    check(
        "p1-http-l25-batch1-preview",
        l25_b1_prev,
        l25_b1_prev["status"] == 200
        and bool(l25_b1_name)
        and l25_b1_prev_body.get("resolved_mapping") == l25_mapping
        and l25_b1_prev_body.get("totals")
        == {
            "total_rows": 3,
            "valid_rows": 2,
            "new_rows": 2,
            "duplicate_rows": 1,
            "error_rows": 0,
        }
        and len(l25_b1_rows) == 3
        and l25_b1_rows[2].get("class") == "duplicate_in_file"
        and str(l25_b1_rows[1].get("source") or "").startswith("'="),
        "POST imports.create_preview resolves custom name/source mapping and sanitizes formula cells in preview",
        actor="crm.admin@dev.local",
    )

    l25_map_conflict = post_json(
        "p1-http-l25-mapping-conflict-409",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": l25_batch1_file,
            "mapping": {"phone": "SDT_KH", "name": "KENH_KH"},
            "idempotency_key": l25_b1_idem,
        },
    )
    check(
        "p1-http-l25-mapping-conflict-409",
        l25_map_conflict,
        l25_map_conflict["status"] == 409
        and error_envelope(l25_map_conflict).get("code") == "CONFLICT"
        and error_envelope(l25_map_conflict).get("p1_code") == "CONFLICT",
        "Reusing idempotency_key with a different mapping returns HTTP 409 CONFLICT (P1 §6.4)",
        actor="crm.admin@dev.local",
    )

    l25_b1_confirm = post_json(
        "p1-http-l25-batch1-confirm",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.confirm",
        {"batch": l25_b1_name},
    )
    check(
        "p1-http-l25-batch1-confirm",
        l25_b1_confirm,
        l25_b1_confirm["status"] == 200
        and body_payload(l25_b1_confirm).get("status")
        in ("QUEUED", "RUNNING", "COMPLETED"),
        "POST imports.confirm enqueues L-25 Batch 1 to RQ worker",
        actor="crm.admin@dev.local",
    )

    poll_until(
        lambda: get(
            "poll-l25-b1",
            admin,
            f"/api/method/crm_cskh.api.imports.get_status?batch={l25_b1_name}",
            record=False,
        ),
        lambda r: body_payload(r).get("status")
        in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"),
        timeout_s=60.0,
    )

    l25_b1_done = get(
        "p1-http-l25-batch1-completed",
        admin,
        f"/api/method/crm_cskh.api.imports.get_status?batch={l25_b1_name}",
    )
    l25_b1_done_body = body_payload(l25_b1_done)
    check(
        "p1-http-l25-batch1-completed",
        l25_b1_done,
        l25_b1_done["status"] == 200
        and l25_b1_done_body.get("status") == "COMPLETED"
        and int(l25_b1_done_body.get("created_rows", -1)) == 2
        and int(l25_b1_done_body.get("reused_rows", -1)) == 0
        and int(l25_b1_done_body.get("skipped_rows", -1)) == 0
        and int(l25_b1_done_body.get("totals", {}).get("duplicate_rows", -1)) == 1,
        "RQ worker completes L-25 Batch 1 with created_rows=2, reused_rows=0, duplicate_rows=1, skipped_rows=0",
        actor="crm.admin@dev.local",
    )

    l25_b2_prev = post_json(
        "p1-http-l25-batch2-preview",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.create_preview",
        {
            "file_name": l25_batch2_file,
            "mapping": l25_mapping,
            "idempotency_key": f"http-l25-b2-{secrets.token_hex(4)}",
        },
    )
    l25_b2_prev_body = body_payload(l25_b2_prev)
    l25_b2_name = str(l25_b2_prev_body.get("batch") or "")
    l25_b2_rows = list(l25_b2_prev_body.get("preview") or [])
    check(
        "p1-http-l25-batch2-preview",
        l25_b2_prev,
        l25_b2_prev["status"] == 200
        and bool(l25_b2_name)
        and int(l25_b2_prev_body.get("totals", {}).get("duplicate_rows", 0)) == 1
        and int(l25_b2_prev_body.get("totals", {}).get("new_rows", 0)) == 1
        and len(l25_b2_rows) == 2
        and l25_b2_rows[0].get("class") == "duplicate_in_crm"
        and l25_b2_rows[1].get("name") == "Trần Thị L25B",
        "POST imports.create_preview on L-25 Batch 2 classifies 1 duplicate_in_crm + 1 sanitized HTML new_row",
        actor="crm.admin@dev.local",
    )

    l25_b2_confirm = post_json(
        "p1-http-l25-batch2-confirm",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.imports.confirm",
        {"batch": l25_b2_name},
    )
    check(
        "p1-http-l25-batch2-confirm",
        l25_b2_confirm,
        l25_b2_confirm["status"] == 200
        and body_payload(l25_b2_confirm).get("status")
        in ("QUEUED", "RUNNING", "COMPLETED"),
        "POST imports.confirm enqueues L-25 Batch 2 to RQ worker",
        actor="crm.admin@dev.local",
    )

    poll_until(
        lambda: get(
            "poll-l25-b2",
            admin,
            f"/api/method/crm_cskh.api.imports.get_status?batch={l25_b2_name}",
            record=False,
        ),
        lambda r: body_payload(r).get("status")
        in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"),
        timeout_s=60.0,
    )

    l25_b2_done = get(
        "p1-http-l25-batch2-completed",
        admin,
        f"/api/method/crm_cskh.api.imports.get_status?batch={l25_b2_name}",
    )
    l25_b2_done_body = body_payload(l25_b2_done)
    l25_db_ok, l25_db_msg, l25_leads = verify_l25_db_truth(
        l25_b1_name, l25_b2_name, l25_canonicals
    )
    check(
        "p1-http-l25-batch2-completed",
        l25_b2_done,
        l25_b2_done["status"] == 200
        and l25_b2_done_body.get("status") == "COMPLETED"
        and int(l25_b2_done_body.get("created_rows", -1)) == 1
        and int(l25_b2_done_body.get("reused_rows", -1)) == 1
        and int(l25_b2_done_body.get("skipped_rows", -1)) == 0
        and l25_db_ok,
        f"RQ worker completes L-25 Batch 2 without overwriting existing canonical Lead ({l25_db_msg})",
        actor="crm.admin@dev.local",
    )

    pool_b1_admin = get(
        "p1-http-l25-customer-pool-batch1-admin",
        admin,
        f"/api/method/crm_cskh.api.customers.customer_pool?batch={l25_b1_name}",
    )
    pool_b1_items = {
        str(row.get("lead")): row
        for row in (body_payload(pool_b1_admin).get("items") or [])
    }
    check(
        "p1-http-l25-customer-pool-batch1-admin",
        pool_b1_admin,
        pool_b1_admin["status"] == 200
        and int(body_payload(pool_b1_admin).get("total", -1)) == 2
        and set(pool_b1_items) == {l25_leads.get("named"), l25_leads.get("missing")}
        and pool_b1_items.get(l25_leads.get("named", ""), {}).get("work_item") is None
        and pool_b1_items.get(l25_leads.get("named", ""), {}).get("state") == "eligible"
        and pool_b1_items.get(l25_leads.get("named", ""), {}).get("display_name")
        == "Nguyễn Văn L25"
        and pool_b1_items.get(l25_leads.get("named", ""), {}).get("source")
        == "Zalo L25",
        "GET customers.customer_pool?batch=<batch1> returns imported customers without Work Items",
        actor="crm.admin@dev.local",
    )

    pool_b2_admin = get(
        "p1-http-l25-customer-pool-batch2-admin",
        admin,
        f"/api/method/crm_cskh.api.customers.customer_pool?batch={l25_b2_name}",
    )
    pool_b2_items = {
        str(row.get("lead")): row
        for row in (body_payload(pool_b2_admin).get("items") or [])
    }
    check(
        "p1-http-l25-customer-pool-batch2-admin",
        pool_b2_admin,
        pool_b2_admin["status"] == 200
        and int(body_payload(pool_b2_admin).get("total", -1)) == 2
        and set(pool_b2_items) == {l25_leads.get("named"), l25_leads.get("b2")}
        and pool_b2_items.get(l25_leads.get("named", ""), {}).get("display_name")
        == "Nguyễn Văn L25"
        and pool_b2_items.get(l25_leads.get("b2", ""), {}).get("display_name")
        == "Trần Thị L25B",
        "GET customers.customer_pool?batch=<batch2> returns both newly created and multi-batch reused canonical Lead",
        actor="crm.admin@dev.local",
    )

    pool_b1_bod = get(
        "p1-http-l25-customer-pool-batch1-bod",
        bod,
        f"/api/method/crm_cskh.api.customers.customer_pool?batch={l25_b1_name}",
    )
    pool_b1_bod_items = list(body_payload(pool_b1_bod).get("items") or [])
    check(
        "p1-http-l25-customer-pool-batch1-bod",
        pool_b1_bod,
        pool_b1_bod["status"] == 200
        and int(body_payload(pool_b1_bod).get("total", -1)) == 2
        and len(pool_b1_bod_items) == 2
        and all(
            r.get("phone") is None and r.get("display_name") == ""
            for r in pool_b1_bod_items
        ),
        "GET customers.customer_pool?batch=<batch1> redacts phone and display_name for BOD Viewer",
        actor="bod.viewer@dev.local",
    )

    pool_denied_staff = get(
        "p1-http-l25-customer-pool-denied-staff",
        staff_a,
        f"/api/method/crm_cskh.api.customers.customer_pool?batch={l25_b1_name}",
    )
    check(
        "p1-http-l25-customer-pool-denied-staff",
        pool_denied_staff,
        pool_denied_staff["status"] == 403
        and error_envelope(pool_denied_staff).get("code") == "FORBIDDEN",
        "GET customers.customer_pool rejects plain Sales User with HTTP 403 FORBIDDEN",
        actor="staff.a@dev.local",
    )

    write_evidence()
    print(f"P1 HTTP probe: {'FAIL' if failures else 'PASS'} ({len(records)} records)")
    return 1 if failures else 0


if __name__ == "__main__":
    if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run())
