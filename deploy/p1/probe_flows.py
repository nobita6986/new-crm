#!/usr/bin/env python3
"""End-to-end probe of the P1 UI flows (used while bringing the UI up).

Not part of the delivery: it drives the **real** P1 APIs over HTTP with the
session of a seeded role, exactly like the browser does:

  1. import: upload a private file -> preview -> confirm -> status/results;
  2. campaigns: list -> rules -> save_rule;
  3. distribution: preview -> execute -> get_run;
  4. customers: pool_list / my_list;
  5. dashboard: distribution / campaigns.

It never fabricates a result: every number in the report comes from the
server response. No phone number, cookie or token is written to the output.
"""

import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = os.environ.get("P1_BASE", "http://127.0.0.1:8120")
HOST = os.environ.get("P1_HOST", "p1.localhost")
PASSWORD = os.environ.get("P1_PASSWORD", "P1Seed-Only-2026!")

# The `CRM Sales Hierarchy` node of the **probe team** (the leader of
# `staff.a@p1.local` / `staff.b@p1.local` / `staff.c@p1.local`).
#
# Every campaign the probe creates is scoped to this node: a campaign
# without a team scope is a *shared* campaign, so its runs and its failures
# would be counted in the scope of **every** team of the site.
PROBE_TEAM_SCOPE = os.environ.get("P1_TEAM_SCOPE", "")

PAGES = [
    "/cskh",
    "/cskh/pool",
    "/cskh/import",
    "/cskh/campaigns",
    "/cskh/my-customers",
    "/cskh/dashboard",
]

# The CSV carries **fake** numbers of the `09xx xxx xxx` form: they are
# placeholders that cannot be dialled, and they are never printed.
#
# The suffix makes every probe run use *fresh* phone numbers, so the import
# really creates new leads in the customer pool instead of hitting the
# "duplicate in CRM" branch of the previous run.
def csv_rows(suffix):
    """Four fake phone numbers: `0912 <4 digits> <2 digits>` = 10 digits.

    The middle block is the run suffix, so every probe run uses fresh
    numbers and the import really creates new leads in the pool.
    """
    return [
        "mobile_no,source",
        f"0912{suffix}01,Website",
        f"0912{suffix}02,Facebook",
        f"0912{suffix}01,Facebook",
        f"0912{suffix}03,Walk In",
        "not-a-phone,Email",
    ]


def opener():
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def login(user):
    op, jar = opener()
    req = urllib.request.Request(
        BASE + "/api/method/login",
        data=json.dumps({"usr": user, "pwd": PASSWORD}).encode("utf-8"),
        headers={"Host": HOST, "Content-Type": "application/json"},
        method="POST",
    )
    with op.open(req, timeout=60) as response:
        response.read()
    return op, jar


def get_with(op, path):
    req = urllib.request.Request(BASE + path, headers={"Host": HOST}, method="GET")
    try:
        with op.open(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def csrf(op, path="/cskh"):
    """Read the CSRF token of the session out of a rendered P1 page.

    The page publishes the token twice: as the JSON header object
    `window.cskhCsrfHeader = {"X-Frappe-CSRF-Token": "..."}` and as the
    Frappe web alias `window.frappe.csrf_token = "..."`. Both are parsed
    here, so the probe keeps working whichever form the page uses.
    """
    status, body = get_with(op, path)
    text = body.decode("utf-8", errors="replace")
    match = re.search(r'window\.frappe\.csrf_token\s*=\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    match = re.search(
        r'window\.cskhCsrfHeader\s*=\s*\{\s*"X-Frappe-CSRF-Token":\s*"([^"]+)"', text
    )
    if match:
        return match.group(1)
    match = re.search(r'csrf-token" content="([^"]+)"', text)
    return match.group(1) if match else None


def api(op, method_path, payload=None, token=None, method=None):
    """Call a whitelisted method.

    `method="GET"` forces a GET (the payload becomes the query string), which
    the read endpoints of the contract require (`imports.get_status`).
    """
    url = BASE + "/api/method/" + method_path
    verb = method or ("POST" if payload is not None else "GET")
    data = None
    hdrs = {"Host": HOST, "Accept": "application/json"}
    if verb == "GET":
        query = urllib.parse.urlencode(payload or {})
        if query:
            url += "?" + query
    else:
        data = json.dumps(payload or {}).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
        if token:
            hdrs["X-Frappe-CSRF-Token"] = token
    req = urllib.request.Request(url, data=data, headers=hdrs, method=verb)
    try:
        with op.open(req, timeout=180) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body[:400]


def unwrap(body):
    """Frappe wraps the payload of `/api/method/*` in a `message` envelope."""
    if isinstance(body, dict) and "message" in body and len(body) <= 3:
        return body["message"]
    return body


def upload(op, filename, content, token):
    """Upload a **private** file, exactly like `cskh.uploadPrivateFile`."""
    import base64

    payload = {"file_name": filename, "content": base64.b64encode(content).decode("ascii")}
    status, body = api(
        op,
        "crm_cskh.api.import_files.upload_import_file",
        payload,
        token=token,
    )
    return status, unwrap(body)


def probe_pages(op):
    out = {}
    for page in PAGES:
        status, body = get_with(op, page)
        text = body.decode("utf-8", errors="replace")
        title = re.search(r"<title>(.*?)</title>", text, re.S)
        out[page] = {
            "status": status,
            "title": title.group(1).strip() if title else "",
            "has_error": "page-error" in text or "traceback" in text.lower(),
        }
    return out


def probe_reads(op, token):
    out = {}
    for name in (
        "crm_cskh.api.customers.pool_list",
        "crm_cskh.api.customers.my_list",
        "crm_cskh.api.dashboard.distribution",
        "crm_cskh.api.dashboard.campaigns",
        "crm_cskh.api.campaigns.list",
    ):
        status, body = api(op, name, token=token)
        summary = unwrap(body)
        if isinstance(summary, dict):
            summary = {
                "scope": summary.get("scope"),
                "total": summary.get("total"),
                "items": len(
                    summary.get("items")
                    or summary.get("campaigns")
                    or summary.get("per_user")
                    or []
                ),
                "cards": summary.get("cards"),
                "p1_code": summary.get("p1_code"),
            }
        out[name] = {"status": status, "body": summary}
    return out


def probe_import(op, token):
    """upload -> preview -> confirm -> status."""
    out = {}
    suffix = f"{int(time.time() * 1000) % 10000:04d}"
    status, body = upload(
        op, "p1-probe-import.csv", "\n".join(csv_rows(suffix)).encode("utf-8"), token
    )
    uploaded = unwrap(body)
    file_name = None
    if isinstance(uploaded, dict):
        file_name = uploaded.get("name")
        out["upload"] = {
            "status": status,
            "is_private": uploaded.get("is_private"),
            "size": uploaded.get("size"),
        }
    else:
        out["upload"] = {"status": status, "body": str(uploaded)[:200]}
    if not file_name:
        return out

    status, body = api(
        op,
        "crm_cskh.api.imports.create_preview",
        {
            "file_name": file_name,
            "mapping": {"mobile_no": "mobile_no"},
            "idempotency_key": "probe-import-" + str(int(time.time() * 1000)),
        },
        token=token,
    )
    preview = unwrap(body)
    if isinstance(preview, dict):
        out["preview"] = {
            "status": status,
            "batch": preview.get("batch"),
            "state": preview.get("status"),
            "totals": preview.get("totals"),
            "preview_rows": len(preview.get("preview") or []),
            "p1_code": preview.get("p1_code"),
        }
    else:
        out["preview"] = {"status": status, "body": str(preview)[:200]}
        return out

    batch = out["preview"].get("batch")
    status, body = api(op, "crm_cskh.api.imports.confirm", {"batch": batch}, token=token)
    confirmed = unwrap(body)
    out["confirm"] = {
        "status": status,
        "state": confirmed.get("status") if isinstance(confirmed, dict) else None,
    }

    # The worker runs in the background; the UI polls the status endpoint.
    last = None
    for _ in range(30):
        status, body = api(
            op, "crm_cskh.api.imports.get_status", {"batch": batch}, token=token, method="GET"
        )
        last = unwrap(body)
        if isinstance(last, dict) and last.get("status") in (
            "COMPLETED",
            "COMPLETED_WITH_ERRORS",
            "FAILED",
        ):
            break
        time.sleep(2)
    out["status"] = {
        "http": status,
        "state": last.get("status") if isinstance(last, dict) else None,
        "totals": last.get("totals") if isinstance(last, dict) else None,
    }
    return out


def probe_distribution(op, token):
    """preview -> execute -> run.

    The probe creates its **own** campaign (and its own rules) so the run is
    reproducible: a campaign whose leads are already assigned has nothing
    left to distribute, and the preview would be refused with
    `NO_ELIGIBLE_LEADS`.

    The campaign is scoped to the **probe team** (`PROBE_TEAM_SCOPE`): a
    campaign without a team scope is a *shared* campaign, and its runs would
    then land in the counters of **every** team of the site (the scope
    assertion of the regression suite counts the failed runs of a leader).
    """
    out = {}
    suffix = f"{int(time.time() * 1000) % 100000:05d}"
    code = f"PROBE{suffix}"

    status, body = api(
        op,
        "crm_cskh.api.campaigns.save",
        {
            "code": code,
            "title": "Probe campaign " + code,
            "status": "Active",
            "team_scope": PROBE_TEAM_SCOPE,
            "selection": {"source": None},
        },
        token=token,
    )
    campaign_doc = unwrap(body)
    if not isinstance(campaign_doc, dict) or not campaign_doc.get("campaign"):
        out["create_campaign"] = {"status": status, "body": str(campaign_doc)[:300]}
        return out
    campaign = campaign_doc["campaign"]
    out["create_campaign"] = {"status": status, "campaign": campaign, "code": code}

    rules = []
    for user, weight in (
        ("staff.a@p1.local", 50),
        ("staff.b@p1.local", 30),
        ("staff.c@p1.local", 20),
    ):
        status, body = api(
            op,
            "crm_cskh.api.campaigns.save_rule",
            {
                "campaign": campaign,
                "user": user,
                "weight": weight,
                "capacity_limit": 100,
                "enabled": 1,
            },
            token=token,
        )
        rule = unwrap(body)
        rules.append(
            {"user": user, "status": status, "weight": (rule or {}).get("weight")}
        )
    out["rules"] = rules

    status, body = api(
        op, "crm_cskh.api.distribution.preview", {"campaign": campaign}, token=token
    )
    preview = unwrap(body)
    if not isinstance(preview, dict) or not preview.get("preview_id"):
        out["preview"] = {"status": status, "body": str(preview)[:300]}
        return out
    out["preview"] = {
        "status": status,
        "preview_id": preview.get("preview_id"),
        "rule_version": preview.get("rule_version"),
        "fingerprint": (preview.get("selection_fingerprint") or "")[:16],
        "expires_at": preview.get("expires_at"),
        "summary": preview.get("summary"),
    }

    status, body = api(
        op,
        "crm_cskh.api.distribution.execute",
        {
            "preview_id": preview["preview_id"],
            "expected_rule_version": preview.get("rule_version"),
        },
        token=token,
    )
    run = unwrap(body)
    if isinstance(run, dict):
        out["execute"] = {
            "status": status,
            "run_id": run.get("run_id"),
            "state": run.get("status"),
            "created": run.get("created"),
            "errors": run.get("errors"),
            "assigned": run.get("assigned"),
            "p1_code": run.get("p1_code"),
        }
    else:
        out["execute"] = {"status": status, "body": str(run)[:300]}
    return out


def main():
    users = sys.argv[1:] or ["crm.admin@p1.local"]
    report = {}
    for user in users:
        op, jar = login(user)
        token = csrf(op)
        entry = {"csrf": bool(token), "pages": probe_pages(op)}
        entry["reads"] = probe_reads(op, token)
        entry["import"] = probe_import(op, token)
        entry["distribution"] = probe_distribution(op, token)
        report[user] = entry
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
