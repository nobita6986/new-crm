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

        # L-23 cross-team fixture:
        # 1) `l23_lead_b` assigned in Team 1 (`camp_t1`) -> permanently revoked (`return_to_pool=False`)
        #    -> assigned to `staff.c@dev.local` in Team 2 (`camp_t2`).
        # 2) `l23_stay_a` assigned in Team 1 (`camp_t1`) -> permanently revoked (`return_to_pool=False`)
        #    and NOT active anywhere else (positive control for Team 1).
        l23_src_b = f"L23HttpSrcB-{run_tag}"
        l23_src_a = f"L23HttpSrcA-{run_tag}"
        l23_lead_b = create_canonical_lead(f"L23-ToTeam2-{run_tag}")
        l23_raw_phone_b = str(
            frappe.db.get_value("CRM Lead", l23_lead_b, "mobile_no") or ""
        )
        l23_local_phone_b = (
            "0" + l23_raw_phone_b[3:]
            if l23_raw_phone_b.startswith("+84")
            else l23_raw_phone_b
        )
        frappe.db.set_value(
            "CRM Lead",
            l23_lead_b,
            {
                "first_name": f"Khach Doi2 {run_tag}",
                "lead_name": f"Khach Doi2 {run_tag}",
                "source": l23_src_b,
            },
            update_modified=False,
        )
        wi_l23_a = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "campaign": camp_t1.name,
                "lead": l23_lead_b,
                "status": "queued",
            }
        )
        wi_l23_a.insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": l23_lead_b,
                "campaign": camp_t1.name,
                "work_item": wi_l23_a.name,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi_l23_a.name,
            "staff.a@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 HTTP Team1 initial",
            request_id=f"http-l23-asg-a-{run_tag}",
        )
        ownership.revoke(
            wi_l23_a.name,
            expected_owner="staff.a@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 HTTP Team1 permanent revoke",
            request_id=f"http-l23-rev-a-{run_tag}",
            return_to_pool=False,
        )
        wi_l23_b = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "campaign": camp_t2.name,
                "lead": l23_lead_b,
                "status": "queued",
            }
        )
        wi_l23_b.insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": l23_lead_b,
                "campaign": camp_t2.name,
                "work_item": wi_l23_b.name,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi_l23_b.name,
            "staff.c@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 HTTP Team2 active",
            request_id=f"http-l23-asg-b-{run_tag}",
        )

        l23_stay_a = create_canonical_lead(f"L23-StayTeam1-{run_tag}")
        frappe.db.set_value(
            "CRM Lead", l23_stay_a, "source", l23_src_a, update_modified=False
        )
        wi_l23_stay = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "campaign": camp_t1.name,
                "lead": l23_stay_a,
                "status": "queued",
            }
        )
        wi_l23_stay.insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": l23_stay_a,
                "campaign": camp_t1.name,
                "work_item": wi_l23_stay.name,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi_l23_stay.name,
            "staff.a@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 HTTP stay Team1 assign",
            request_id=f"http-l23-stay-asg-{run_tag}",
        )
        ownership.revoke(
            wi_l23_stay.name,
            expected_owner="staff.a@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 HTTP stay Team1 revoke",
            request_id=f"http-l23-stay-rev-{run_tag}",
            return_to_pool=False,
        )

        # 3) `l23_pool_return_b`: returned to pool (`return_to_pool=True` -> `queued`) in Team 1,
        #    then claimed & assigned to `staff.c@dev.local` in Team 2 (`pool_list` L-23 check).
        l23_pool_return_b = create_canonical_lead(f"L23-PoolRetToTeam2-{run_tag}")
        wi_l23_pr_a = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "campaign": camp_t1.name,
                "lead": l23_pool_return_b,
                "status": "queued",
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": l23_pool_return_b,
                "campaign": camp_t1.name,
                "work_item": wi_l23_pr_a.name,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi_l23_pr_a.name,
            "staff.a@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 pool return initial",
            request_id=f"http-l23-pr-asg-a-{run_tag}",
        )
        ownership.revoke(
            wi_l23_pr_a.name,
            expected_owner="staff.a@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 pool return Team1",
            request_id=f"http-l23-pr-rev-a-{run_tag}",
            return_to_pool=True,
        )
        wi_l23_pr_b = frappe.get_doc(
            {
                "doctype": "CSKH Work Item",
                "campaign": camp_t2.name,
                "lead": l23_pool_return_b,
                "status": "queued",
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": l23_pool_return_b,
                "campaign": camp_t2.name,
                "work_item": wi_l23_pr_b.name,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            wi_l23_pr_b.name,
            "staff.c@dev.local",
            actor="crm.admin@dev.local",
            reason="L-23 pool return Team2 claim",
            request_id=f"http-l23-pr-asg-b-{run_tag}",
        )

        # L-29 fixture: private Team1/Team2 + shared campaign/rules/runs with raw JSON markers.
        l29_lead_a = create_canonical_lead(f"L29-A-{run_tag}")
        l29_lead_b = create_canonical_lead(f"L29-B-{run_tag}")
        l29_lead_sh_a = create_canonical_lead(f"L29-ShA-{run_tag}")
        l29_lead_sh_b = create_canonical_lead(f"L29-ShB-{run_tag}")

        l29_camp_a = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-L29-A-{run_tag}",
                "title": f"L29 Private Team1 {run_tag}",
                "status": "Active",
                "team_scope": team1_node,
                "selection_json": json.dumps(
                    {"leads": [l29_lead_a], "secret": "L29_HTTP_SEL_A"},
                    ensure_ascii=False,
                ),
            }
        ).insert(ignore_permissions=True)
        l29_camp_b = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-L29-B-{run_tag}",
                "title": f"L29 Private Team2 {run_tag}",
                "status": "Active",
                "team_scope": team2_node,
                "selection_json": json.dumps(
                    {"leads": [l29_lead_b], "secret": "L29_HTTP_SEL_B"},
                    ensure_ascii=False,
                ),
            }
        ).insert(ignore_permissions=True)
        l29_camp_shared = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-L29-SH-{run_tag}",
                "title": f"L29 Shared {run_tag}",
                "status": "Active",
                "team_scope": None,
                "selection_json": json.dumps(
                    {
                        "leads": [l29_lead_sh_a, l29_lead_sh_b],
                        "secret": "L29_HTTP_SEL_SHARED",
                    },
                    ensure_ascii=False,
                ),
            }
        ).insert(ignore_permissions=True)

        def _mk_rule(camp_name: str, usr: str, tm: str | None, wt: int) -> str:
            return (
                frappe.get_doc(
                    {
                        "doctype": "CSKH Assignment Rule",
                        "campaign": camp_name,
                        "user": usr,
                        "team": tm,
                        "weight": wt,
                        "capacity_limit": 0,
                        "version": 1,
                        "enabled": 1,
                    }
                )
                .insert(ignore_permissions=True)
                .name
            )

        l29_rule_a = _mk_rule(l29_camp_a.name, "staff.a@dev.local", team1_node, 50)
        l29_rule_b = _mk_rule(l29_camp_b.name, "staff.c@dev.local", team2_node, 50)
        l29_rule_sh_a = _mk_rule(
            l29_camp_shared.name, "staff.a@dev.local", team1_node, 60
        )
        l29_rule_sh_b = _mk_rule(
            l29_camp_shared.name, "staff.c@dev.local", team2_node, 40
        )

        def _mk_run(
            camp_name: str,
            tag: str,
            preview_obj: dict[str, object],
            actual_obj: dict[str, object],
            state_obj: dict[str, object],
            err_msg: str | None = None,
        ) -> str:
            return (
                frappe.get_doc(
                    {
                        "doctype": "CSKH Distribution Run",
                        "campaign": camp_name,
                        "idempotency_key": f"l29-http-{tag}-{run_tag}",
                        "rule_version": 1,
                        "selection_fingerprint": f"fp-{tag}-{run_tag}",
                        "status": "completed",
                        "progress": 2,
                        "preview_json": json.dumps(preview_obj, ensure_ascii=False),
                        "actual_json": json.dumps(actual_obj, ensure_ascii=False),
                        "state_json": json.dumps(state_obj, ensure_ascii=False),
                        "error": err_msg,
                    }
                )
                .insert(ignore_permissions=True)
                .name
            )

        l29_run_a = _mk_run(
            l29_camp_a.name,
            "a",
            {
                "eligible_count": 1,
                "excluded_count": 0,
                "targets": [{"user": "staff.a@dev.local", "weight": 50, "planned": 1}],
                "simulated": {"staff.a@dev.local": 1},
                "leads": [l29_lead_a],
                "secret": "L29_HTTP_PREVIEW_A",
            },
            {
                "total": 1,
                "created": 1,
                "errors": 0,
                "assigned": {"staff.a@dev.local": 1},
                "skipped": [],
                "secret": "L29_HTTP_ACTUAL_A",
            },
            {"epoch": 1, "total": 1, "secret": "L29_HTTP_STATE_A"},
        )
        l29_run_b = _mk_run(
            l29_camp_b.name,
            "b",
            {
                "eligible_count": 1,
                "excluded_count": 0,
                "targets": [{"user": "staff.c@dev.local", "weight": 50, "planned": 1}],
                "simulated": {"staff.c@dev.local": 1},
                "leads": [l29_lead_b],
                "secret": "L29_HTTP_PREVIEW_B",
            },
            {
                "total": 1,
                "created": 1,
                "errors": 0,
                "assigned": {"staff.c@dev.local": 1},
                "skipped": [],
                "secret": "L29_HTTP_ACTUAL_B",
            },
            {"epoch": 1, "total": 1, "secret": "L29_HTTP_STATE_B"},
            err_msg="L29_HTTP_ERR_B",
        )
        l29_run_shared = _mk_run(
            l29_camp_shared.name,
            "sh",
            {
                "eligible_count": 2,
                "excluded_count": 0,
                "targets": [
                    {"user": "staff.a@dev.local", "weight": 60, "planned": 1},
                    {"user": "staff.c@dev.local", "weight": 40, "planned": 1},
                ],
                "simulated": {"staff.a@dev.local": 1, "staff.c@dev.local": 1},
                "leads": [l29_lead_sh_a, l29_lead_sh_b],
                "secret": "L29_HTTP_PREVIEW_SHARED",
            },
            {
                "total": 2,
                "created": 2,
                "errors": 0,
                "assigned": {"staff.a@dev.local": 1, "staff.c@dev.local": 1},
                "skipped": [{"lead": l29_lead_b, "reason": "already_claimed"}],
                "secret": "L29_HTTP_ACTUAL_SHARED",
            },
            {"epoch": 1, "total": 2, "secret": "L29_HTTP_STATE_SHARED"},
            err_msg="L29_HTTP_ERR_SHARED",
        )

        # Direct ownership mutation eligibility fixture (`assign` & `reassign`).
        mut_camp_t1 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-MUT-T1-{run_tag}",
                "title": f"Direct Mut T1 {run_tag}",
                "status": "Active",
                "team_scope": team1_node,
            }
        ).insert(ignore_permissions=True)
        mut_camp_t2 = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-MUT-T2-{run_tag}",
                "title": f"Direct Mut T2 {run_tag}",
                "status": "Active",
                "team_scope": team2_node,
            }
        ).insert(ignore_permissions=True)
        mut_camp_closed = frappe.get_doc(
            {
                "doctype": "CSKH Campaign",
                "code": f"P1-MUT-CL-{run_tag}",
                "title": f"Direct Mut Closed {run_tag}",
                "status": "Closed",
                "team_scope": team1_node,
            }
        ).insert(ignore_permissions=True)

        # `staff.a@dev.local`: v1 cap=3 -> v2 cap=1; `staff.b@dev.local`: v1 enabled=1 -> v2 enabled=0.
        for usr, ver, cap, en in (
            ("staff.a@dev.local", 1, 3, 1),
            ("staff.a@dev.local", 2, 1, 1),
            ("staff.b@dev.local", 1, 5, 1),
            ("staff.b@dev.local", 2, 5, 0),
        ):
            frappe.get_doc(
                {
                    "doctype": "CSKH Assignment Rule",
                    "campaign": mut_camp_t1.name,
                    "user": usr,
                    "team": team1_node,
                    "weight": 50,
                    "capacity_limit": cap,
                    "version": ver,
                    "enabled": en,
                }
            ).insert(ignore_permissions=True)

        mut_wi_1 = (
            frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": mut_camp_t1.name,
                    "lead": create_canonical_lead(f"Mut1-{run_tag}"),
                    "status": "queued",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
        mut_wi_2 = (
            frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": mut_camp_t1.name,
                    "lead": create_canonical_lead(f"Mut2-{run_tag}"),
                    "status": "queued",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
        mut_wi_closed = (
            frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": mut_camp_closed.name,
                    "lead": create_canonical_lead(f"MutClosed-{run_tag}"),
                    "status": "queued",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
        mut_cross_lead = create_canonical_lead(f"MutCross-{run_tag}")
        mut_wi_cross_t1 = (
            frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": mut_camp_t1.name,
                    "lead": mut_cross_lead,
                    "status": "queued",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
        mut_wi_cross_t2 = (
            frappe.get_doc(
                {
                    "doctype": "CSKH Work Item",
                    "campaign": mut_camp_t2.name,
                    "lead": mut_cross_lead,
                    "status": "queued",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
        frappe.get_doc(
            {
                "doctype": "CSKH Active Claim",
                "lead": mut_cross_lead,
                "campaign": mut_camp_t2.name,
                "work_item": mut_wi_cross_t2,
            }
        ).insert(ignore_permissions=True)
        ownership.assign(
            mut_wi_cross_t2,
            "staff.c@dev.local",
            actor="crm.admin@dev.local",
            reason="Mut cross-campaign claim T2",
            request_id=f"http-mut-cross-t2-{run_tag}",
        )

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
            "l23": {
                "lead_b": l23_lead_b,
                "local_phone_b": l23_local_phone_b,
                "src_b": l23_src_b,
                "lead_stay_a": l23_stay_a,
                "src_a": l23_src_a,
                "lead_pool_return_b": l23_pool_return_b,
            },
            "l29": {
                "lead_a": l29_lead_a,
                "lead_b": l29_lead_b,
                "lead_sh_a": l29_lead_sh_a,
                "lead_sh_b": l29_lead_sh_b,
                "camp_a": l29_camp_a.name,
                "camp_b": l29_camp_b.name,
                "camp_shared": l29_camp_shared.name,
                "rule_a": l29_rule_a,
                "rule_b": l29_rule_b,
                "rule_sh_a": l29_rule_sh_a,
                "rule_sh_b": l29_rule_sh_b,
                "run_a": l29_run_a,
                "run_b": l29_run_b,
                "run_shared": l29_run_shared,
            },
            "direct_mut": {
                "wi_1": mut_wi_1,
                "wi_2": mut_wi_2,
                "wi_closed": mut_wi_closed,
                "wi_cross_t1": mut_wi_cross_t1,
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
    leader_2 = login("leader-2", "team2.leader@dev.local", SEED_PASSWORD)
    staff_a = login("staff-a", "staff.a@dev.local", SEED_PASSWORD)
    staff_b = login("staff-b", "staff.b@dev.local", SEED_PASSWORD)

    admin_token = csrf_token("admin", admin, "crm.admin@dev.local")
    bod_token = csrf_token("bod", bod, "bod.viewer@dev.local")
    leader_token = csrf_token("leader", leader, "team1.leader@dev.local")
    leader_2_token = csrf_token("leader-2", leader_2, "team2.leader@dev.local")
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
    camp_t1 = str(fixtures.get("camp_t1") or "")
    camp_t2 = str(fixtures.get("camp_t2") or "")
    l23_fx = dict(fixtures.get("l23") or {})
    l29_fx = dict(fixtures.get("l29") or {})
    direct_mut_fx = dict(fixtures.get("direct_mut") or {})
    admin_roles = set(fixtures.get("admin_roles") or [])
    if (
        not campaign
        or not import_file
        or not large_import_file
        or len(l21_items) != 8
        or not l25_batch1_file
        or not l25_batch2_file
        or not camp_t1
        or not camp_t2
        or not l23_fx.get("lead_b")
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

    # -- 14. L-23: Direct `customers.customer_pool` cross-team isolation ----
    l23_lead_b = str(l23_fx.get("lead_b") or "")
    l23_local_phone_b = str(l23_fx.get("local_phone_b") or "")
    l23_src_b = str(l23_fx.get("src_b") or "")
    l23_lead_stay_a = str(l23_fx.get("lead_stay_a") or "")
    l23_src_a = str(l23_fx.get("src_a") or "")

    l23_t1_src_b = get(
        "p1-http-l23-customer-pool-leader1-foreign-source-zero",
        leader,
        f"/api/method/crm_cskh.api.customers.customer_pool?source={urllib.parse.quote(l23_src_b)}",
    )
    l23_t1_src_b_payload = body_payload(l23_t1_src_b)
    check(
        "p1-http-l23-customer-pool-leader1-foreign-source-zero",
        l23_t1_src_b,
        l23_t1_src_b["status"] == 200
        and l23_t1_src_b_payload.get("items") == []
        and int(l23_t1_src_b_payload.get("total", -1)) == 0
        and int(l23_t1_src_b_payload.get("unique_customers", -1)) == 0
        and int(l23_t1_src_b_payload.get("work_items", -1)) == 0,
        "GET customers.customer_pool?source=<Team2-source> returns 0 items/totals for Team1 Leader despite old Team1 revoked history",
        actor="team1.leader@dev.local",
    )

    l23_t1_search_b = get(
        "p1-http-l23-customer-pool-leader1-foreign-phone-zero",
        leader,
        f"/api/method/crm_cskh.api.customers.customer_pool?search={l23_local_phone_b}",
    )
    l23_t1_search_b_payload = body_payload(l23_t1_search_b)
    check(
        "p1-http-l23-customer-pool-leader1-foreign-phone-zero",
        l23_t1_search_b,
        l23_t1_search_b["status"] == 200
        and l23_t1_search_b_payload.get("items") == []
        and int(l23_t1_search_b_payload.get("total", -1)) == 0
        and int(l23_t1_search_b_payload.get("work_items", -1)) == 0,
        "GET customers.customer_pool?search=<Team2-phone> returns 0 items/totals for Team1 Leader (zero PII/owner/campaign leak)",
        actor="team1.leader@dev.local",
    )

    l23_t1_camp_a = get(
        "p1-http-l23-customer-pool-leader1-own-campaign",
        leader,
        f"/api/method/crm_cskh.api.customers.customer_pool?campaign={camp_t1}&page_size=100",
    )
    l23_t1_camp_a_payload = body_payload(l23_t1_camp_a)
    l23_t1_camp_a_leads = {
        str(r.get("lead")): r for r in (l23_t1_camp_a_payload.get("items") or [])
    }
    l23_t1_raw_json = json.dumps(l23_t1_camp_a_payload, ensure_ascii=False, default=str)
    check(
        "p1-http-l23-customer-pool-leader1-own-campaign",
        l23_t1_camp_a,
        l23_t1_camp_a["status"] == 200
        and l23_lead_b not in l23_t1_camp_a_leads
        and l23_lead_stay_a in l23_t1_camp_a_leads
        and l23_t1_camp_a_leads[l23_lead_stay_a].get("state") == "revoked"
        and "staff.c@dev.local" not in l23_t1_raw_json
        and camp_t2 not in l23_t1_raw_json,
        "GET customers.customer_pool?campaign=<Team1> excludes lead now active in Team2 while keeping Team1 revoked-only lead",
        actor="team1.leader@dev.local",
    )

    l23_t1_stay_pos = get(
        "p1-http-l23-customer-pool-leader1-own-revoked-positive",
        leader,
        f"/api/method/crm_cskh.api.customers.customer_pool?source={urllib.parse.quote(l23_src_a)}",
    )
    l23_t1_stay_payload = body_payload(l23_t1_stay_pos)
    check(
        "p1-http-l23-customer-pool-leader1-own-revoked-positive",
        l23_t1_stay_pos,
        l23_t1_stay_pos["status"] == 200
        and int(l23_t1_stay_payload.get("total", -1)) == 1
        and (l23_t1_stay_payload.get("items") or [{}])[0].get("lead") == l23_lead_stay_a
        and (l23_t1_stay_payload.get("items") or [{}])[0].get("state") == "revoked",
        "GET customers.customer_pool positive control returns Team1 revoked-only lead for Team1 Leader",
        actor="team1.leader@dev.local",
    )

    l23_t2_pos = get(
        "p1-http-l23-customer-pool-leader2-positive",
        leader_2,
        f"/api/method/crm_cskh.api.customers.customer_pool?source={urllib.parse.quote(l23_src_b)}",
    )
    l23_t2_payload = body_payload(l23_t2_pos)
    l23_t2_item = (l23_t2_payload.get("items") or [{}])[0]
    check(
        "p1-http-l23-customer-pool-leader2-positive",
        l23_t2_pos,
        l23_t2_pos["status"] == 200
        and int(l23_t2_payload.get("total", -1)) == 1
        and int(l23_t2_payload.get("work_items", -1)) == 1
        and l23_t2_item.get("lead") == l23_lead_b
        and l23_t2_item.get("state") == "assigned"
        and l23_t2_item.get("owner") == "staff.c@dev.local"
        and l23_t2_item.get("campaign") == camp_t2,
        "GET customers.customer_pool positive control returns reassigned lead with work_items=1 for Team2 Leader",
        actor="team2.leader@dev.local",
    )

    # -- 15. L-26: Direct backend 400 INVALID_REQUEST on bad pagination/limit
    for cid, opener, actor_email, url in (
        (
            "p1-http-l26-ownership-history-bad-limit-alpha",
            admin,
            "crm.admin@dev.local",
            f"/api/method/crm_cskh.api.ownership.history?work_item={target_work_item}&limit=abc",
        ),
        (
            "p1-http-l26-ownership-history-bad-limit-zero",
            admin,
            "crm.admin@dev.local",
            f"/api/method/crm_cskh.api.ownership.history?work_item={target_work_item}&limit=0",
        ),
        (
            "p1-http-l26-ownership-history-bad-limit-over-max",
            admin,
            "crm.admin@dev.local",
            f"/api/method/crm_cskh.api.ownership.history?work_item={target_work_item}&limit=201",
        ),
        (
            "p1-http-l26-pool-list-bad-page-alpha",
            admin,
            "crm.admin@dev.local",
            "/api/method/crm_cskh.api.customers.pool_list?page=abc",
        ),
        (
            "p1-http-l26-pool-list-bad-page-size-alpha",
            leader,
            "team1.leader@dev.local",
            "/api/method/crm_cskh.api.customers.pool_list?page_size=xyz",
        ),
        (
            "p1-http-l26-pool-list-bad-page-size-over-max",
            bod,
            "bod.viewer@dev.local",
            "/api/method/crm_cskh.api.customers.pool_list?page=1&page_size=101",
        ),
        (
            "p1-http-l26-customer-pool-bad-page-alpha",
            admin,
            "crm.admin@dev.local",
            "/api/method/crm_cskh.api.customers.customer_pool?page=abc",
        ),
        (
            "p1-http-l26-customer-pool-bad-page-size-zero",
            leader,
            "team1.leader@dev.local",
            "/api/method/crm_cskh.api.customers.customer_pool?page=1&page_size=0",
        ),
        (
            "p1-http-l26-customer-pool-bad-page-float",
            bod,
            "bod.viewer@dev.local",
            "/api/method/crm_cskh.api.customers.customer_pool?page=1.5&page_size=20",
        ),
        (
            "p1-http-l26-my-list-bad-page-alpha",
            staff_a,
            "staff.a@dev.local",
            "/api/method/crm_cskh.api.customers.my_list?page=abc",
        ),
    ):
        bad_resp = get(cid, opener, url)
        bad_env = error_envelope(bad_resp)
        check(
            cid,
            bad_resp,
            bad_resp["status"] == 400
            and bad_env.get("code") == "INVALID_REQUEST"
            and bad_env.get("p1_code") == "INVALID_REQUEST",
            f"GET {url.split('/api/method/')[-1]} returns HTTP 400 INVALID_REQUEST (never 500 ValueError)",
            actor=actor_email,
        )

    l26_hist_ok = get(
        "p1-http-l26-ownership-history-valid-limit",
        admin,
        f"/api/method/crm_cskh.api.ownership.history?work_item={target_work_item}&limit=10",
    )
    check(
        "p1-http-l26-ownership-history-valid-limit",
        l26_hist_ok,
        l26_hist_ok["status"] == 200
        and int(body_payload(l26_hist_ok).get("total", 0)) >= 2,
        "GET ownership.history?limit=10 returns HTTP 200 positive control",
        actor="crm.admin@dev.local",
    )

    # -- 14b. L-23 residual: `customers.pool_list` after `return_to_pool=True` -> Team2 claim
    l23_pool_ret_b = str(l23_fx.get("lead_pool_return_b") or "")
    l23_plist_t1 = get(
        "p1-http-l23-pool-list-leader1-excludes-reclaimed-team2",
        leader,
        f"/api/method/crm_cskh.api.customers.pool_list?campaign={camp_t1}&page_size=100",
    )
    l23_plist_payload = body_payload(l23_plist_t1)
    l23_plist_leads = {
        str(r.get("lead")) for r in (l23_plist_payload.get("items") or [])
    }
    l23_plist_raw = json.dumps(l23_plist_payload, ensure_ascii=False, default=str)
    check(
        "p1-http-l23-pool-list-leader1-excludes-reclaimed-team2",
        l23_plist_t1,
        l23_plist_t1["status"] == 200
        and l23_pool_ret_b not in l23_plist_leads
        and int(l23_plist_payload.get("total", 0)) >= 1
        and "staff.c@dev.local" not in l23_plist_raw,
        "GET customers.pool_list?campaign=<Team1> excludes return_to_pool lead now claimed by Team2 while keeping Team1 queued item",
        actor="team1.leader@dev.local",
    )

    # -- 16. L-29: Generic Frappe REST, `frappe.client.*`, Desk & raw Run protection --
    l29_camp_a = str(l29_fx.get("camp_a") or "")
    l29_camp_b = str(l29_fx.get("camp_b") or "")
    l29_camp_sh = str(l29_fx.get("camp_shared") or "")
    l29_rule_a = str(l29_fx.get("rule_a") or "")
    l29_rule_b = str(l29_fx.get("rule_b") or "")
    l29_rule_sh_a = str(l29_fx.get("rule_sh_a") or "")
    l29_rule_sh_b = str(l29_fx.get("rule_sh_b") or "")
    l29_run_a = str(l29_fx.get("run_a") or "")
    l29_run_b = str(l29_fx.get("run_b") or "")
    l29_run_sh = str(l29_fx.get("run_shared") or "")
    l29_lead_b = str(l29_fx.get("lead_b") or "")
    l29_lead_sh_b = str(l29_fx.get("lead_sh_b") or "")
    l29_lead_sh_a = str(l29_fx.get("lead_sh_a") or "")

    raw_run_fields = (
        "preview_json",
        "actual_json",
        "state_json",
        "progress",
        "error",
        "lease_expires_at",
    )
    non_int_raw_run_fields = (
        "preview_json",
        "actual_json",
        "state_json",
        "error",
        "lease_expires_at",
    )

    def _rest_url(
        dt: str, docname: str = "", query: dict[str, object] | None = None
    ) -> str:
        base = f"/api/resource/{urllib.parse.quote(dt)}"
        if docname:
            base += f"/{urllib.parse.quote(docname)}"
        if query:
            base += "?" + urllib.parse.urlencode(query)
        return base

    def _rest_data(resp: dict[str, object]):
        js = resp.get("json")
        return js.get("data") if isinstance(js, dict) else None

    def _no_raw_run_leak(doc_or_row: dict[str, object], raw_text: str) -> bool:
        if any(doc_or_row.get(k) is not None for k in non_int_raw_run_fields):
            return False
        if doc_or_row.get("progress") not in (None, 0):
            return False
        return "L29_HTTP_" not in raw_text

    # 16a. Leader 1 negative direct REST GETs (foreign Campaign, foreign Rule, foreign Rule in shared Campaign, foreign Run)
    for slug, dt, dname in (
        ("camp-b", "CSKH Campaign", l29_camp_b),
        ("rule-b", "CSKH Assignment Rule", l29_rule_b),
        ("rule-sh-b", "CSKH Assignment Rule", l29_rule_sh_b),
        ("run-b", "CSKH Distribution Run", l29_run_b),
    ):
        cid = f"p1-http-l29-rest-get-denied-leader1-{slug}"
        r_den = get(cid, leader, _rest_url(dt, dname))
        check(
            cid,
            r_den,
            r_den["status"] == 403
            and "L29_HTTP_" not in r_den["text"]
            and "staff.c@dev.local" not in r_den["text"],
            f"GET /api/resource/{dt}/{dname} denies Team1 Leader with HTTP 403",
            actor="team1.leader@dev.local",
        )

    # 16b. Leader 1 positive direct REST GETs (own & shared Campaign/Rule/Run) with raw JSON stripped
    for slug, cid_doc in (("camp-a", l29_camp_a), ("camp-sh", l29_camp_sh)):
        cid = f"p1-http-l29-rest-get-leader1-{slug}"
        r_ok = get(cid, leader, _rest_url("CSKH Campaign", cid_doc))
        r_doc = _rest_data(r_ok)
        check(
            cid,
            r_ok,
            r_ok["status"] == 200
            and isinstance(r_doc, dict)
            and r_doc.get("name") == cid_doc
            and r_doc.get("selection_json") is None
            and "L29_HTTP_SEL" not in r_ok["text"],
            f"GET /api/resource/CSKH Campaign/{cid_doc} allows Team1 Leader metadata while stripping selection_json",
            actor="team1.leader@dev.local",
        )

    for slug, rid in (("rule-a", l29_rule_a), ("rule-sh-a", l29_rule_sh_a)):
        cid = f"p1-http-l29-rest-get-leader1-{slug}"
        r_ok = get(cid, leader, _rest_url("CSKH Assignment Rule", rid))
        r_doc = _rest_data(r_ok)
        check(
            cid,
            r_ok,
            r_ok["status"] == 200
            and isinstance(r_doc, dict)
            and r_doc.get("name") == rid
            and r_doc.get("user") == "staff.a@dev.local",
            f"GET /api/resource/CSKH Assignment Rule/{rid} allows Team1 Leader own-team rule",
            actor="team1.leader@dev.local",
        )

    for slug, run_id in (("run-a", l29_run_a), ("run-sh", l29_run_sh)):
        cid = f"p1-http-l29-rest-get-leader1-{slug}"
        r_ok = get(cid, leader, _rest_url("CSKH Distribution Run", run_id))
        r_doc = _rest_data(r_ok)
        check(
            cid,
            r_ok,
            r_ok["status"] == 200
            and isinstance(r_doc, dict)
            and r_doc.get("name") == run_id
            and _no_raw_run_leak(r_doc, r_ok["text"])
            and "staff.c@dev.local" not in r_ok["text"]
            and l29_lead_b not in r_ok["text"]
            and l29_lead_sh_b not in r_ok["text"],
            f"GET /api/resource/CSKH Distribution Run/{run_id} strips preview/actual/state/progress/error for Team1 Leader",
            actor="team1.leader@dev.local",
        )

    # 16c. Leader 1 REST list GET with explicit `fields=[...]` across all 3 DocTypes
    rest_camp_list_l1 = get(
        "p1-http-l29-rest-list-campaigns-leader1",
        leader,
        _rest_url(
            "CSKH Campaign",
            query={
                "fields": json.dumps(["name", "title", "team_scope", "selection_json"]),
                "filters": json.dumps(
                    [["name", "in", [l29_camp_a, l29_camp_b, l29_camp_sh]]]
                ),
            },
        ),
    )
    rc_rows = _rest_data(rest_camp_list_l1) or []
    check(
        "p1-http-l29-rest-list-campaigns-leader1",
        rest_camp_list_l1,
        rest_camp_list_l1["status"] == 200
        and isinstance(rc_rows, list)
        and {r.get("name") for r in rc_rows} == {l29_camp_a, l29_camp_sh}
        and all("selection_json" not in r for r in rc_rows)
        and "L29_HTTP_SEL" not in rest_camp_list_l1["text"],
        "GET /api/resource/CSKH Campaign?fields=[...,selection_json] hides Team2 campaign and strips selection_json",
        actor="team1.leader@dev.local",
    )

    rest_rule_list_l1 = get(
        "p1-http-l29-rest-list-rules-leader1",
        leader,
        _rest_url(
            "CSKH Assignment Rule",
            query={
                "fields": json.dumps(["name", "campaign", "user", "team", "weight"]),
                "filters": json.dumps(
                    [
                        [
                            "name",
                            "in",
                            [l29_rule_a, l29_rule_b, l29_rule_sh_a, l29_rule_sh_b],
                        ]
                    ]
                ),
            },
        ),
    )
    rr_rows = _rest_data(rest_rule_list_l1) or []
    check(
        "p1-http-l29-rest-list-rules-leader1",
        rest_rule_list_l1,
        rest_rule_list_l1["status"] == 200
        and isinstance(rr_rows, list)
        and {r.get("name") for r in rr_rows} == {l29_rule_a, l29_rule_sh_a}
        and "staff.c@dev.local" not in rest_rule_list_l1["text"],
        "GET /api/resource/CSKH Assignment Rule?fields=[...] returns only Team1 rules (even inside shared campaign)",
        actor="team1.leader@dev.local",
    )

    rest_run_list_l1 = get(
        "p1-http-l29-rest-list-runs-leader1",
        leader,
        _rest_url(
            "CSKH Distribution Run",
            query={
                "fields": json.dumps(["name", "campaign", "status", *raw_run_fields]),
                "filters": json.dumps(
                    [["name", "in", [l29_run_a, l29_run_b, l29_run_sh]]]
                ),
            },
        ),
    )
    rrun_rows = _rest_data(rest_run_list_l1) or []
    check(
        "p1-http-l29-rest-list-runs-leader1",
        rest_run_list_l1,
        rest_run_list_l1["status"] == 200
        and isinstance(rrun_rows, list)
        and {r.get("name") for r in rrun_rows} == {l29_run_a, l29_run_sh}
        and all(all(f not in r for f in raw_run_fields) for r in rrun_rows)
        and "L29_HTTP_" not in rest_run_list_l1["text"]
        and "staff.c@dev.local" not in rest_run_list_l1["text"],
        "GET /api/resource/CSKH Distribution Run?fields=[...,preview_json,actual_json,state_json] hides Team2 run and strips raw JSON",
        actor="team1.leader@dev.local",
    )

    # 16d. Generic REST write/create/delete denials (CRM-ADMIN & Leader 1) + filter-oracle denials
    rest_post_admin = post_json(
        "p1-http-l29-rest-post-denied-admin",
        admin,
        admin_token,
        _rest_url("CSKH Campaign"),
        {"code": "P1-REST-BYPASS", "title": "Bypass attempt", "status": "Draft"},
    )
    check(
        "p1-http-l29-rest-post-denied-admin",
        rest_post_admin,
        rest_post_admin["status"] == 403,
        "POST /api/resource/CSKH Campaign rejects direct generic creation even for CSKH CRM Admin",
        actor="crm.admin@dev.local",
    )

    rest_put_l1 = request(
        "p1-http-l29-rest-put-denied-leader1",
        leader,
        "PUT",
        _rest_url("CSKH Campaign", l29_camp_a),
        json.dumps({"title": "Tampered"}).encode("utf-8"),
        {"Content-Type": "application/json", "X-Frappe-CSRF-Token": leader_token},
    )
    check(
        "p1-http-l29-rest-put-denied-leader1",
        rest_put_l1,
        rest_put_l1["status"] == 403,
        "PUT /api/resource/CSKH Campaign/<own> rejects direct generic update for Team1 Leader",
        actor="team1.leader@dev.local",
    )

    rest_del_admin = request(
        "p1-http-l29-rest-delete-denied-admin",
        admin,
        "DELETE",
        _rest_url("CSKH Distribution Run", l29_run_a),
        None,
        {"X-Frappe-CSRF-Token": admin_token},
    )
    check(
        "p1-http-l29-rest-delete-denied-admin",
        rest_del_admin,
        rest_del_admin["status"] == 403,
        "DELETE /api/resource/CSKH Distribution Run/<id> rejects direct generic delete for CSKH CRM Admin",
        actor="crm.admin@dev.local",
    )

    for cid, op, actor_email, url in (
        (
            "p1-http-l29-filter-oracle-run-actual-leader1",
            leader,
            "team1.leader@dev.local",
            _rest_url(
                "CSKH Distribution Run",
                query={"filters": json.dumps([["actual_json", "like", "%staff%"]])},
            ),
        ),
        (
            "p1-http-l29-filter-oracle-camp-selection-leader1",
            leader,
            "team1.leader@dev.local",
            _rest_url(
                "CSKH Campaign",
                query={"filters": json.dumps([["selection_json", "like", "%L29%"]])},
            ),
        ),
        (
            "p1-http-l29-filter-oracle-run-preview-bod",
            bod,
            "bod.viewer@dev.local",
            "/api/method/frappe.client.get_list?"
            + urllib.parse.urlencode(
                {
                    "doctype": "CSKH Distribution Run",
                    "filters": json.dumps([["preview_json", "like", "%L29%"]]),
                }
            ),
        ),
    ):
        fo_resp = get(cid, op, url)
        check(
            cid,
            fo_resp,
            fo_resp["status"] == 403,
            "Filtering on permlevel=1 raw JSON field returns HTTP 403 (blocks blind filter oracle)",
            actor=actor_email,
        )

    # 16e. `frappe.client.get`, `frappe.client.get_list`, `frappe.client.get_value`, `frappe.client.validate_link`
    fc_get_den_l1 = get(
        "p1-http-l29-client-get-denied-leader1-run-b",
        leader,
        "/api/method/frappe.client.get?"
        + urllib.parse.urlencode(
            {"doctype": "CSKH Distribution Run", "name": l29_run_b}
        ),
    )
    check(
        "p1-http-l29-client-get-denied-leader1-run-b",
        fc_get_den_l1,
        fc_get_den_l1["status"] == 403 and "L29_HTTP_" not in fc_get_den_l1["text"],
        "GET frappe.client.get(CSKH Distribution Run, <Team2>) returns HTTP 403 for Team1 Leader",
        actor="team1.leader@dev.local",
    )

    fc_get_sh_l1 = get(
        "p1-http-l29-client-get-leader1-shared-run-scrubbed",
        leader,
        "/api/method/frappe.client.get?"
        + urllib.parse.urlencode(
            {"doctype": "CSKH Distribution Run", "name": l29_run_sh}
        ),
    )
    fc_sh_doc = body_payload(fc_get_sh_l1)
    check(
        "p1-http-l29-client-get-leader1-shared-run-scrubbed",
        fc_get_sh_l1,
        fc_get_sh_l1["status"] == 200
        and fc_sh_doc.get("name") == l29_run_sh
        and _no_raw_run_leak(fc_sh_doc, fc_get_sh_l1["text"])
        and "staff.c@dev.local" not in fc_get_sh_l1["text"],
        "GET frappe.client.get(CSKH Distribution Run, <shared>) strips permlevel=1 raw JSON for Team1 Leader",
        actor="team1.leader@dev.local",
    )

    fc_list_l1 = get(
        "p1-http-l29-client-get-list-leader1-runs",
        leader,
        "/api/method/frappe.client.get_list?"
        + urllib.parse.urlencode(
            {
                "doctype": "CSKH Distribution Run",
                "fields": json.dumps(["name", "status", *raw_run_fields]),
                "filters": json.dumps(
                    [["name", "in", [l29_run_a, l29_run_b, l29_run_sh]]]
                ),
            }
        ),
    )
    fc_list_rows = (fc_list_l1.get("json") or {}).get("message") or []
    check(
        "p1-http-l29-client-get-list-leader1-runs",
        fc_list_l1,
        fc_list_l1["status"] == 200
        and isinstance(fc_list_rows, list)
        and {r.get("name") for r in fc_list_rows} == {l29_run_a, l29_run_sh}
        and all(all(f not in r for f in raw_run_fields) for r in fc_list_rows)
        and "L29_HTTP_" not in fc_list_l1["text"],
        "GET frappe.client.get_list(CSKH Distribution Run, fields=[...,raw]) scopes rows and strips raw JSON",
        actor="team1.leader@dev.local",
    )

    fc_val_den_l1 = get(
        "p1-http-l29-client-get-value-leader1-foreign-camp",
        leader,
        "/api/method/frappe.client.get_value?"
        + urllib.parse.urlencode(
            {
                "doctype": "CSKH Campaign",
                "fieldname": json.dumps(["name", "title", "selection_json"]),
                "filters": l29_camp_b,
            }
        ),
    )
    check(
        "p1-http-l29-client-get-value-leader1-foreign-camp",
        fc_val_den_l1,
        fc_val_den_l1["status"] == 200
        and body_payload(fc_val_den_l1) == {}
        and l29_camp_b not in json.dumps(body_payload(fc_val_den_l1)),
        "GET frappe.client.get_value(CSKH Campaign, <Team2>) returns empty dict for Team1 Leader",
        actor="team1.leader@dev.local",
    )

    vlink_den_l1 = get(
        "p1-http-l29-validate-link-leader1-foreign-camp",
        leader,
        "/api/method/frappe.client.validate_link?"
        + urllib.parse.urlencode(
            {
                "doctype": "CSKH Campaign",
                "docname": l29_camp_b,
                "fields": json.dumps(["title", "selection_json"]),
            }
        ),
    )
    check(
        "p1-http-l29-validate-link-leader1-foreign-camp",
        vlink_den_l1,
        vlink_den_l1["status"] == 200
        and body_payload(vlink_den_l1) == {}
        and l29_camp_b not in vlink_den_l1["text"],
        "GET frappe.client.validate_link(CSKH Campaign, <Team2>) returns {} without leaking existence or title",
        actor="team1.leader@dev.local",
    )

    # 16f. Desk HTTP paths (`getdoc`, `reportview.get`, `search_link` with `ignore_user_permissions=1`)
    desk_getdoc_den_l1 = get(
        "p1-http-l29-desk-getdoc-denied-leader1-camp-b",
        leader,
        "/api/method/frappe.desk.form.load.getdoc?"
        + urllib.parse.urlencode({"doctype": "CSKH Campaign", "name": l29_camp_b}),
    )
    check(
        "p1-http-l29-desk-getdoc-denied-leader1-camp-b",
        desk_getdoc_den_l1,
        desk_getdoc_den_l1["status"] == 403
        and "L29_HTTP_" not in desk_getdoc_den_l1["text"],
        "GET frappe.desk.form.load.getdoc(CSKH Campaign, <Team2>) denies Team1 Leader with HTTP 403",
        actor="team1.leader@dev.local",
    )

    desk_getdoc_sh_l1 = get(
        "p1-http-l29-desk-getdoc-leader1-shared-run",
        leader,
        "/api/method/frappe.desk.form.load.getdoc?"
        + urllib.parse.urlencode(
            {"doctype": "CSKH Distribution Run", "name": l29_run_sh}
        ),
    )
    gd_docs = ((desk_getdoc_sh_l1.get("json") or {}).get("docs")) or []
    check(
        "p1-http-l29-desk-getdoc-leader1-shared-run",
        desk_getdoc_sh_l1,
        desk_getdoc_sh_l1["status"] == 200
        and len(gd_docs) == 1
        and gd_docs[0].get("name") == l29_run_sh
        and _no_raw_run_leak(gd_docs[0], desk_getdoc_sh_l1["text"])
        and "staff.c@dev.local" not in desk_getdoc_sh_l1["text"],
        "GET frappe.desk.form.load.getdoc(CSKH Distribution Run, <shared>) strips raw JSON for Team1 Leader",
        actor="team1.leader@dev.local",
    )

    desk_rv_l1 = post(
        "p1-http-l29-desk-reportview-leader1-runs",
        leader,
        leader_token,
        "/api/method/frappe.desk.reportview.get",
        {
            "doctype": "CSKH Distribution Run",
            "fields": json.dumps(["name", "status", *raw_run_fields]),
            "filters": json.dumps(
                [
                    [
                        "CSKH Distribution Run",
                        "name",
                        "in",
                        [l29_run_a, l29_run_b, l29_run_sh],
                    ]
                ]
            ),
            "limit_page_length": "20",
        },
    )
    rv_msg = body_payload(desk_rv_l1)
    rv_keys = rv_msg.get("keys") or []
    rv_vals = rv_msg.get("values") or []
    rv_rows = [dict(zip(rv_keys, row)) for row in rv_vals]
    check(
        "p1-http-l29-desk-reportview-leader1-runs",
        desk_rv_l1,
        desk_rv_l1["status"] == 200
        and {r.get("name") for r in rv_rows} == {l29_run_a, l29_run_sh}
        and all(f not in rv_keys for f in raw_run_fields)
        and "L29_HTTP_" not in desk_rv_l1["text"],
        "POST frappe.desk.reportview.get(CSKH Distribution Run) scopes rows and strips permlevel=1 columns",
        actor="team1.leader@dev.local",
    )

    desk_rv_l2 = post(
        "p1-http-l29-desk-reportview-leader2-runs",
        leader_2,
        leader_2_token,
        "/api/method/frappe.desk.reportview.get",
        {
            "doctype": "CSKH Distribution Run",
            "fields": json.dumps(["name", "status", *raw_run_fields]),
            "filters": json.dumps(
                [
                    [
                        "CSKH Distribution Run",
                        "name",
                        "in",
                        [l29_run_a, l29_run_b, l29_run_sh],
                    ]
                ]
            ),
            "limit_page_length": "20",
        },
    )
    rv2_msg = body_payload(desk_rv_l2)
    rv2_keys = rv2_msg.get("keys") or []
    rv2_vals = rv2_msg.get("values") or []
    rv2_rows = [dict(zip(rv2_keys, row)) for row in rv2_vals]
    check(
        "p1-http-l29-desk-reportview-leader2-runs",
        desk_rv_l2,
        desk_rv_l2["status"] == 200
        and {r.get("name") for r in rv2_rows} == {l29_run_b, l29_run_sh}
        and all(f not in rv2_keys for f in raw_run_fields)
        and "L29_HTTP_" not in desk_rv_l2["text"],
        "POST frappe.desk.reportview.get(CSKH Distribution Run) scopes rows and strips permlevel=1 columns for Team2 Leader",
        actor="team2.leader@dev.local",
    )

    desk_sl_l1 = post(
        "p1-http-l29-desk-search-link-ignore-user-perms-leader1",
        leader,
        leader_token,
        "/api/method/frappe.desk.search.search_link",
        {
            "doctype": "CSKH Campaign",
            "txt": "",
            "filters": json.dumps(
                [["name", "in", [l29_camp_a, l29_camp_b, l29_camp_sh]]]
            ),
            "ignore_user_permissions": "1",
        },
    )
    sl_rows = (desk_sl_l1.get("json") or {}).get("message") or []
    check(
        "p1-http-l29-desk-search-link-ignore-user-perms-leader1",
        desk_sl_l1,
        desk_sl_l1["status"] == 200
        and isinstance(sl_rows, list)
        and {r.get("value") for r in sl_rows} == {l29_camp_a, l29_camp_sh},
        "POST frappe.desk.search.search_link(ignore_user_permissions=1) still enforces Team1 scope",
        actor="team1.leader@dev.local",
    )

    # 16g. Leader 2 symmetric positive/negative controls + Shared campaign rule/run isolation
    l2_den_camp_a = get(
        "p1-http-l29-rest-get-denied-leader2-camp-a",
        leader_2,
        _rest_url("CSKH Campaign", l29_camp_a),
    )
    check(
        "p1-http-l29-rest-get-denied-leader2-camp-a",
        l2_den_camp_a,
        l2_den_camp_a["status"] == 403,
        "GET /api/resource/CSKH Campaign/<Team1> denies Team2 Leader with HTTP 403",
        actor="team2.leader@dev.local",
    )

    l2_den_rule_sh_a = get(
        "p1-http-l29-rest-get-denied-leader2-rule-sh-a",
        leader_2,
        _rest_url("CSKH Assignment Rule", l29_rule_sh_a),
    )
    check(
        "p1-http-l29-rest-get-denied-leader2-rule-sh-a",
        l2_den_rule_sh_a,
        l2_den_rule_sh_a["status"] == 403,
        "GET /api/resource/CSKH Assignment Rule/<shared-Team1-rule> denies Team2 Leader with HTTP 403",
        actor="team2.leader@dev.local",
    )

    l2_ok_run_b = get(
        "p1-http-l29-rest-get-leader2-run-b",
        leader_2,
        _rest_url("CSKH Distribution Run", l29_run_b),
    )
    l2_run_b_doc = _rest_data(l2_ok_run_b)
    check(
        "p1-http-l29-rest-get-leader2-run-b",
        l2_ok_run_b,
        l2_ok_run_b["status"] == 200
        and isinstance(l2_run_b_doc, dict)
        and l2_run_b_doc.get("name") == l29_run_b
        and _no_raw_run_leak(l2_run_b_doc, l2_ok_run_b["text"]),
        "GET /api/resource/CSKH Distribution Run/<Team2> allows Team2 Leader metadata while stripping raw JSON",
        actor="team2.leader@dev.local",
    )

    l2_ok_run_sh = get(
        "p1-http-l29-desk-getdoc-leader2-shared-run",
        leader_2,
        "/api/method/frappe.desk.form.load.getdoc?"
        + urllib.parse.urlencode(
            {"doctype": "CSKH Distribution Run", "name": l29_run_sh}
        ),
    )
    l2_sh_docs = ((l2_ok_run_sh.get("json") or {}).get("docs")) or []
    check(
        "p1-http-l29-desk-getdoc-leader2-shared-run",
        l2_ok_run_sh,
        l2_ok_run_sh["status"] == 200
        and len(l2_sh_docs) == 1
        and _no_raw_run_leak(l2_sh_docs[0], l2_ok_run_sh["text"])
        and "staff.a@dev.local" not in l2_ok_run_sh["text"]
        and l29_lead_sh_a not in l2_ok_run_sh["text"],
        "GET Desk getdoc on shared Run as Team2 Leader strips Team1 assignee/lead raw JSON",
        actor="team2.leader@dev.local",
    )

    # 16h. BOD, CRM-ADMIN, Staff A, and Guest across generic APIs + permitted projection APIs
    bod_rest_runs = get(
        "p1-http-l29-rest-list-runs-bod-scrubbed",
        bod,
        _rest_url(
            "CSKH Distribution Run",
            query={
                "fields": json.dumps(["name", "status", *raw_run_fields]),
                "filters": json.dumps(
                    [["name", "in", [l29_run_a, l29_run_b, l29_run_sh]]]
                ),
            },
        ),
    )
    bod_rr_rows = _rest_data(bod_rest_runs) or []
    check(
        "p1-http-l29-rest-list-runs-bod-scrubbed",
        bod_rest_runs,
        bod_rest_runs["status"] == 200
        and {r.get("name") for r in bod_rr_rows} == {l29_run_a, l29_run_b, l29_run_sh}
        and all(all(f not in r for f in raw_run_fields) for r in bod_rr_rows)
        and "L29_HTTP_" not in bod_rest_runs["text"],
        "GET /api/resource/CSKH Distribution Run?fields=[...] lets BOD read all run rows while stripping raw JSON",
        actor="bod.viewer@dev.local",
    )

    bod_proj_run = get(
        "p1-http-l29-projection-get-run-bod",
        bod,
        f"/api/method/crm_cskh.api.distribution.get_run?run_id={l29_run_sh}",
    )
    bod_proj_payload = body_payload(bod_proj_run)
    check(
        "p1-http-l29-projection-get-run-bod",
        bod_proj_run,
        bod_proj_run["status"] == 200
        and bod_proj_payload.get("run_id") == l29_run_sh
        and bod_proj_payload.get("assigned")
        == {"staff.a@dev.local": 1, "staff.c@dev.local": 1}
        and "L29_HTTP_STATE" not in bod_proj_run["text"]
        and "L29_HTTP_PREVIEW" not in bod_proj_run["text"],
        "GET distribution.get_run preserves permitted summary projection for BOD without leaking state_json/leads",
        actor="bod.viewer@dev.local",
    )

    l1_rules_den = get(
        "p1-http-l29-campaigns-rules-denied-leader1-foreign",
        leader,
        f"/api/method/crm_cskh.api.campaigns.rules?campaign={l29_camp_b}",
    )
    check(
        "p1-http-l29-campaigns-rules-denied-leader1-foreign",
        l1_rules_den,
        l1_rules_den["status"] == 403
        and error_envelope(l1_rules_den).get("code") == "FORBIDDEN",
        "GET campaigns.rules?campaign=<Team2> denies Team1 Leader with HTTP 403 FORBIDDEN",
        actor="team1.leader@dev.local",
    )

    l1_rules_sh = get(
        "p1-http-l29-campaigns-rules-leader1-shared-scoped",
        leader,
        f"/api/method/crm_cskh.api.campaigns.rules?campaign={l29_camp_sh}",
    )
    l1_sh_elig = body_payload(l1_rules_sh).get("eligible") or []
    check(
        "p1-http-l29-campaigns-rules-leader1-shared-scoped",
        l1_rules_sh,
        l1_rules_sh["status"] == 200
        and [r.get("user") for r in l1_sh_elig] == ["staff.a@dev.local"],
        "GET campaigns.rules?campaign=<shared> returns only Team1 rules to Team1 Leader",
        actor="team1.leader@dev.local",
    )

    admin_rest_run = get(
        "p1-http-l29-rest-get-admin-run-b",
        admin,
        _rest_url("CSKH Distribution Run", l29_run_b),
    )
    admin_run_doc = _rest_data(admin_rest_run)
    check(
        "p1-http-l29-rest-get-admin-run-b",
        admin_rest_run,
        admin_rest_run["status"] == 200
        and isinstance(admin_run_doc, dict)
        and "L29_HTTP_ACTUAL_B" in str(admin_run_doc.get("actual_json") or "")
        and int(admin_run_doc.get("progress") or 0) == 2,
        "GET /api/resource/CSKH Distribution Run/<Team2> grants read-only permlevel=1 access to CSKH CRM Admin",
        actor="crm.admin@dev.local",
    )

    for actor_slug, op, actor_email in (
        ("staff", staff_a, "staff.a@dev.local"),
        ("guest", guest, "Guest"),
    ):
        for dt_slug, dt, doc_id in (
            ("camp", "CSKH Campaign", l29_camp_a),
            ("rule", "CSKH Assignment Rule", l29_rule_a),
            ("run", "CSKH Distribution Run", l29_run_a),
        ):
            cid = f"p1-http-l29-rest-denied-{actor_slug}-{dt_slug}"
            den_resp = get(cid, op, _rest_url(dt, doc_id))
            check(
                cid,
                den_resp,
                den_resp["status"] in (401, 403)
                and "L29_HTTP_" not in den_resp["text"],
                f"GET /api/resource/{dt}/{doc_id} denies {actor_email} with HTTP 401/403",
                actor=actor_email,
            )

    # -- 17. Direct HTTP mutation eligibility (`ownership.assign` & `ownership.reassign`) --
    mut_wi_1 = str(direct_mut_fx.get("wi_1") or "")
    mut_wi_2 = str(direct_mut_fx.get("wi_2") or "")
    mut_wi_closed = str(direct_mut_fx.get("wi_closed") or "")
    mut_wi_cross_t1 = str(direct_mut_fx.get("wi_cross_t1") or "")

    mut_asg_wrong_team = post_json(
        "p1-http-mut-assign-denied-wrong-team",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": mut_wi_1,
            "target_user": "staff.c@dev.local",
            "reason": "wrong team direct HTTP assign",
            "request_id": f"http-mut-wt-asg-{run_token}",
        },
    )
    check(
        "p1-http-mut-assign-denied-wrong-team",
        mut_asg_wrong_team,
        mut_asg_wrong_team["status"] == 400
        and error_envelope(mut_asg_wrong_team).get("code") == "INVALID_REQUEST",
        "POST ownership.assign rejects target_user outside campaign team_scope with HTTP 400 INVALID_REQUEST",
        actor="crm.admin@dev.local",
    )

    mut_asg_disabled_rule = post_json(
        "p1-http-mut-assign-denied-disabled-rule",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": mut_wi_1,
            "target_user": "staff.b@dev.local",
            "reason": "disabled v2 rule direct HTTP assign",
            "request_id": f"http-mut-dis-asg-{run_token}",
        },
    )
    check(
        "p1-http-mut-assign-denied-disabled-rule",
        mut_asg_disabled_rule,
        mut_asg_disabled_rule["status"] == 400
        and error_envelope(mut_asg_disabled_rule).get("code") == "INVALID_REQUEST",
        "POST ownership.assign rejects target_user whose latest rule version is disabled with HTTP 400 INVALID_REQUEST",
        actor="crm.admin@dev.local",
    )

    mut_asg_ok = post_json(
        "p1-http-mut-assign-positive-within-capacity",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": mut_wi_1,
            "target_user": "staff.a@dev.local",
            "reason": "valid Team1 direct HTTP assign",
            "request_id": f"http-mut-ok-asg-{run_token}",
        },
    )
    check(
        "p1-http-mut-assign-positive-within-capacity",
        mut_asg_ok,
        mut_asg_ok["status"] == 200
        and body_payload(mut_asg_ok).get("owner") == "staff.a@dev.local",
        "POST ownership.assign succeeds for eligible in-team assignee with remaining v2 capacity",
        actor="crm.admin@dev.local",
    )

    mut_asg_cap = post_json(
        "p1-http-mut-assign-denied-capacity-exceeded",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": mut_wi_2,
            "target_user": "staff.a@dev.local",
            "reason": "exceeds v2 capacity=1",
            "request_id": f"http-mut-cap-asg-{run_token}",
        },
    )
    check(
        "p1-http-mut-assign-denied-capacity-exceeded",
        mut_asg_cap,
        mut_asg_cap["status"] == 409
        and error_envelope(mut_asg_cap).get("code") == "CAPACITY_EXCEEDED",
        "POST ownership.assign rejects full latest-version capacity with HTTP 409 CAPACITY_EXCEEDED",
        actor="crm.admin@dev.local",
    )

    mut_reasg_wrong_team = post_json(
        "p1-http-mut-reassign-denied-wrong-team",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.reassign",
        {
            "work_item": mut_wi_1,
            "expected_owner": "staff.a@dev.local",
            "target_user": "staff.c@dev.local",
            "reason": "wrong team direct HTTP reassign",
            "request_id": f"http-mut-wt-reasg-{run_token}",
        },
    )
    check(
        "p1-http-mut-reassign-denied-wrong-team",
        mut_reasg_wrong_team,
        mut_reasg_wrong_team["status"] == 400
        and error_envelope(mut_reasg_wrong_team).get("code") == "INVALID_REQUEST",
        "POST ownership.reassign rejects target_user outside campaign team_scope with HTTP 400 INVALID_REQUEST",
        actor="crm.admin@dev.local",
    )

    mut_reasg_dis = post_json(
        "p1-http-mut-reassign-denied-disabled-rule",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.reassign",
        {
            "work_item": mut_wi_1,
            "expected_owner": "staff.a@dev.local",
            "target_user": "staff.b@dev.local",
            "reason": "disabled v2 rule direct HTTP reassign",
            "request_id": f"http-mut-dis-reasg-{run_token}",
        },
    )
    check(
        "p1-http-mut-reassign-denied-disabled-rule",
        mut_reasg_dis,
        mut_reasg_dis["status"] == 400
        and error_envelope(mut_reasg_dis).get("code") == "INVALID_REQUEST",
        "POST ownership.reassign rejects target_user whose latest rule version is disabled with HTTP 400 INVALID_REQUEST",
        actor="crm.admin@dev.local",
    )

    mut_reasg_ok = post_json(
        "p1-http-mut-reassign-positive-in-team",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.reassign",
        {
            "work_item": mut_wi_1,
            "expected_owner": "staff.a@dev.local",
            "target_user": "team1.leader@dev.local",
            "reason": "escalate to Team1 leader",
            "request_id": f"http-mut-ok-reasg-{run_token}",
        },
    )
    check(
        "p1-http-mut-reassign-positive-in-team",
        mut_reasg_ok,
        mut_reasg_ok["status"] == 200
        and body_payload(mut_reasg_ok).get("owner") == "team1.leader@dev.local",
        "POST ownership.reassign succeeds for eligible in-team Sales User",
        actor="crm.admin@dev.local",
    )

    mut_closed = post_json(
        "p1-http-mut-assign-denied-closed-campaign",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": mut_wi_closed,
            "target_user": "staff.a@dev.local",
            "reason": "closed campaign assign",
            "request_id": f"http-mut-closed-asg-{run_token}",
        },
    )
    check(
        "p1-http-mut-assign-denied-closed-campaign",
        mut_closed,
        mut_closed["status"] == 409
        and error_envelope(mut_closed).get("code") == "CONFLICT",
        "POST ownership.assign rejects Closed campaign with HTTP 409 CONFLICT",
        actor="crm.admin@dev.local",
    )

    mut_cross_claim = post_json(
        "p1-http-mut-assign-denied-cross-campaign-claim",
        admin,
        admin_token,
        "/api/method/crm_cskh.api.ownership.assign",
        {
            "work_item": mut_wi_cross_t1,
            "target_user": "staff.a@dev.local",
            "reason": "attempt assign lead claimed by Team2",
            "request_id": f"http-mut-cross-asg-{run_token}",
        },
    )
    check(
        "p1-http-mut-assign-denied-cross-campaign-claim",
        mut_cross_claim,
        mut_cross_claim["status"] == 409
        and error_envelope(mut_cross_claim).get("code") == "ALREADY_ASSIGNED",
        "POST ownership.assign rejects queued item whose lead holds an Active Claim in another campaign with HTTP 409 ALREADY_ASSIGNED",
        actor="crm.admin@dev.local",
    )

    write_evidence()
    print(f"P1 HTTP probe: {'FAIL' if failures else 'PASS'} ({len(records)} records)")
    return 1 if failures else 0


if __name__ == "__main__":
    if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run())
