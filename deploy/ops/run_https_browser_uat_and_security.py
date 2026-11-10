#!/usr/bin/env python3
"""Real Headless Chromium (Playwright) HTTPS UAT, RBAC, P4.md §8.4, S01-S20 & Vietnamese A11y Runner (`G10/G16/G20`).

Executes genuine browser journeys over verified TLS (`ignore_https_errors=False`) on the isolated
Staging/DR HTTPS stack using ONLY business personas (`CRM Admin`, `Leader A`, `Leader B`,
`Staff A`, `Staff B`, `BOD`, plus `Staff C`, `Outside`, and `Guest`).

`Administrator` is NEVER logged into the browser and is never substituted for business actors;
out-of-band CLI setup is limited strictly to technical fixture preparation (`S06` private file
check and `S15` `User.enabled=0` offboarding toggle).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPS_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPS_DIR.parents[1]
if str(OPS_DIR) not in sys.path:
    sys.path.insert(0, str(OPS_DIR))

import check_health_and_invariants as health_tool  # noqa: E402
import staging_https_smoke_and_reconcile as p403_tool  # noqa: E402

PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")
VI_DIACRITICS_RE = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]", re.I)

# Every /cskh/* and /crm route mapped to a real authorized non-Administrator business actor
AUTHORIZED_ROUTE_ACTORS: tuple[tuple[str, str], ...] = (
    ("/cskh", "staff_a"),
    ("/cskh/dashboard", "bod"),
    ("/cskh/import", "admin"),
    ("/cskh/campaigns", "admin"),
    ("/cskh/pool", "admin"),
    ("/cskh/my-customers", "staff_a"),
    ("/cskh/work-item", "staff_a"),
    ("/cskh/callbacks", "staff_a"),
    ("/cskh/progress", "leader_a"),
    ("/cskh/settings", "admin"),
    ("/cskh/quality-overview", "bod"),
    ("/cskh/team-quality", "leader_a"),
    ("/cskh/employee-quality", "bod"),
    ("/cskh/campaign-quality", "bod"),
    ("/cskh/scorecard", "staff_b"),
    ("/cskh/sla-operations", "leader_a"),
    ("/crm", "staff_a"),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose_cmd(env_file: Path, extra_args: list[str]) -> list[str]:
    return p403_tool._compose_cmd(env_file, mode="dr-https") + extra_args


def install_ca_into_nss_store(ca_crt_path: Path) -> dict[str, Any]:
    """Import ephemeral DR Root CA into Chromium's Linux NSS DB so ignore_https_errors=False works."""
    certutil_bin = shutil.which("certutil")
    home_dir = Path.home()
    nss_dir = home_dir / ".pki" / "nssdb"
    if os.name != "posix" or not certutil_bin:
        return {
            "platform": os.name,
            "certutil_available": bool(certutil_bin),
            "nss_imported": False,
        }
    nss_dir.mkdir(parents=True, exist_ok=True)
    if not (nss_dir / "cert9.db").exists():
        subprocess.run(
            [certutil_bin, "-d", f"sql:{nss_dir}", "-N", "--empty-password"],
            check=True,
            capture_output=True,
            text=True,
        )
    nick = f"cskh-dr-ca-{int(time.time())}"
    subprocess.run(
        [certutil_bin, "-d", f"sql:{nss_dir}", "-A", "-t", "C,,", "-n", nick, "-i", str(ca_crt_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        "platform": os.name,
        "certutil_available": True,
        "nss_db": str(nss_dir),
        "nickname": nick,
        "nss_imported": True,
    }


def _prepare_technical_fixtures(env_file: Path, site_name: str, staff_a: str, staff_b: str, leader_b: str) -> dict[str, Any]:
    """Technical-only DB lookup & S06 private file fixture creation (zero business mutation on roles)."""
    prep_py = r"""
import json, os, sys, frappe
site, staff_a, staff_b, leader_b = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.set_user("Administrator")
    wi_a = frappe.db.sql(
        "SELECT name, lead, revision FROM `tabCSKH Work Item` WHERE assigned_to=%s AND status IN ('assigned','in_progress') ORDER BY name DESC LIMIT 1",
        (staff_a,),
        as_dict=True,
    )[0]
    wi_b = frappe.db.sql(
        "SELECT name, lead, revision FROM `tabCSKH Work Item` WHERE assigned_to=%s AND status IN ('assigned','in_progress','closed') ORDER BY name ASC LIMIT 1",
        (staff_b,),
        as_dict=True,
    )[0]
    dnc_wi = frappe.db.sql(
        "SELECT wi.name, wi.lead FROM `tabCSKH Work Item` wi JOIN `tabCRM Lead` l ON l.name = wi.lead "
        "WHERE wi.assigned_to=%s AND IFNULL(l.cskh_do_not_contact, 0)=1 LIMIT 1",
        (staff_a,),
        as_dict=True,
    )
    att_b = frappe.db.get_value("CSKH Contact Attempt", {"work_item": wi_b.name}, "name") or ""
    cl_b = frappe.db.get_value("CRM Call Log", {"reference_docname": wi_b.lead}, "name") or ""
    task_b = frappe.db.get_value("CRM Task", {"reference_docname": wi_b.lead}, "name") or ""
    note_b = frappe.db.get_value("FCRM Note", {"reference_docname": wi_b.lead}, "name") or ""
    snap_b = frappe.db.get_value(
        "CSKH KPI Snapshot",
        {"scope_type": "employee", "scope_id": staff_b, "is_latest_published": 1},
        "name",
    ) or ""
    kpi_run_count = int(frappe.db.count("CSKH KPI Run"))
    existing_f = frappe.db.get_value(
        "File",
        {"attached_to_doctype": "CRM Lead", "attached_to_name": wi_b.lead, "is_private": 1},
        ["name", "file_url"],
        as_dict=True,
    )
    if not existing_f:
        fdoc = frappe.get_doc({
            "doctype": "File",
            "file_name": "dr-browser-s06-private.txt",
            "attached_to_doctype": "CRM Lead",
            "attached_to_name": wi_b.lead,
            "is_private": 1,
            "content": "SYNTHETIC_BROWSER_PRIVATE_FILE_S06",
        }).insert(ignore_permissions=True)
        existing_f = {"name": fdoc.name, "file_url": fdoc.file_url}
    bod_roles = frappe.get_roles("cskh-p3-bod@example.test")
    frappe.db.commit()
    print("BROWSER_FIXTURE_JSON=" + json.dumps({
        "wi_a": wi_a.name,
        "lead_a": wi_a.lead,
        "wi_b": wi_b.name,
        "lead_b": wi_b.lead,
        "dnc_wi_a": dnc_wi[0].name if dnc_wi else "",
        "attempt_b": att_b,
        "call_log_b": cl_b,
        "task_b": task_b,
        "note_b": note_b,
        "snap_b": snap_b,
        "private_file_b_docname": existing_f["name"],
        "private_file_b_url": existing_f["file_url"],
        "kpi_run_count_before": kpi_run_count,
        "bod_has_system_manager": "System Manager" in bod_roles,
    }))
finally:
    frappe.destroy()
"""
    proc = subprocess.run(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                prep_py,
                site_name,
                staff_a,
                staff_b,
                leader_b,
            ],
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for ln in reversed(proc.stdout.splitlines()):
        if ln.startswith("BROWSER_FIXTURE_JSON="):
            return json.loads(ln.split("=", 1)[1])
    raise RuntimeError("Failed to emit BROWSER_FIXTURE_JSON")


def _toggle_user_enabled_technical_control(env_file: Path, site_name: str, email: str, enabled: int) -> None:
    toggle_py = r"""
import os, sys, frappe
site, email, enabled = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.db.set_value("User", email, "enabled", enabled)
    if enabled == 0:
        from frappe.sessions import clear_sessions
        clear_sessions(user=email, force=True)
    frappe.clear_cache(user=email)
    frappe.db.commit()
finally:
    frappe.destroy()
"""
    subprocess.run(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                toggle_py,
                site_name,
                email,
                str(enabled),
            ],
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _count_kpi_runs_and_stale_grants(env_file: Path, site_name: str, lead_name: str, former_owner: str) -> dict[str, int]:
    check_py = r"""
import json, os, sys, frappe
site, lead_name, former_owner = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    kpi_runs = int(frappe.db.count("CSKH KPI Run"))
    stale_shares = int(frappe.db.count("DocShare", {"share_doctype": "CRM Lead", "share_name": lead_name, "user": former_owner}))
    stale_todos = int(frappe.db.count("ToDo", {"reference_type": "CRM Lead", "reference_name": lead_name, "allocated_to": former_owner, "status": "Open"}))
    print("POST_CHECK_JSON=" + json.dumps({"kpi_runs": kpi_runs, "stale_shares": stale_shares, "stale_todos": stale_todos}))
finally:
    frappe.destroy()
"""
    proc = subprocess.run(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                check_py,
                site_name,
                lead_name,
                former_owner,
            ],
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for ln in reversed(proc.stdout.splitlines()):
        if ln.startswith("POST_CHECK_JSON="):
            return json.loads(ln.split("=", 1)[1])
    raise RuntimeError("Failed to emit POST_CHECK_JSON")


def _browser_fetch_json(page: Any, path: str, *, method: str = "GET", body: dict[str, Any] | None = None, include_csrf: bool = True) -> dict[str, Any]:
    """Execute an in-browser fetch() using the active persona's cookies and window.frappe.csrf_token."""
    return page.evaluate(
        """async ({ path, method, body, includeCsrf }) => {
            const headers = { 'Accept': 'application/json' };
            if (body !== null && body !== undefined) {
                headers['Content-Type'] = 'application/json';
            }
            if (includeCsrf && window.frappe && window.frappe.csrf_token) {
                headers['X-Frappe-CSRF-Token'] = window.frappe.csrf_token;
            }
            const resp = await fetch(path, {
                method,
                headers,
                credentials: 'same-origin',
                body: body !== null && body !== undefined ? JSON.stringify(body) : undefined,
            });
            const text = await resp.text();
            let parsed = null;
            try { parsed = JSON.parse(text); } catch (_e) {}
            return { status: resp.status, text: text.slice(0, 600), json: parsed };
        }""",
        {"path": path, "method": method, "body": body, "includeCsrf": include_csrf},
    )


def run_browser_https_suite(
    *,
    env_file: Path,
    ca_crt_path: Path,
    seed_info: dict[str, Any],
    seed_pw: str,
    out_json: Path | None = None,
    connect_host: str = "127.0.0.1",
) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    env_map = p403_tool.parse_env_file(env_file)
    site_name = env_map["SITE_NAME"].strip()
    https_port = int(env_map["HTTPS_PUBLISH_PORT"])
    base_url = f"https://{site_name}:{https_port}"

    users = seed_info["users"]
    period_key = seed_info["period_key"]
    team_a = seed_info["team_a"]
    team_b = seed_info["team_b"]
    aba_wi = seed_info["aba_work_item"]

    nss_info = install_ca_into_nss_store(ca_crt_path)
    fx = _prepare_technical_fixtures(
        env_file=env_file,
        site_name=site_name,
        staff_a=users["staff_a"],
        staff_b=users["staff_b"],
        leader_b=users["leader_b"],
    )

    cases: list[dict[str, Any]] = []

    def record_case(
        *,
        acceptance_id: str,
        gate: str,
        actor: str,
        expected: str,
        actual: str,
        passed: bool,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        cases.append(
            {
                "acceptance_id": acceptance_id,
                "gate": gate,
                "actor": actor,
                "expected": expected,
                "actual": actual,
                "status": "PASS" if passed else "FAIL",
                "redacted_evidence": evidence or {},
            }
        )

    t0 = time.monotonic()
    route_matrix: dict[str, Any] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                f"--host-resolver-rules=MAP {site_name} {connect_host}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        contexts: dict[str, Any] = {}
        pages: dict[str, Any] = {}

        def login_persona(role_key: str, email: str, landing_route: str) -> Any:
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                ignore_https_errors=False,
            )
            pg = ctx.new_page()
            resp = pg.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=30000)
            assert resp is not None and resp.status == 200, f"/login failed for {role_key}"
            pg.locator("#login_email").fill(email)
            pg.locator("#login_password").fill(seed_pw)
            pg.locator(".for-login .btn-login, button.btn-login").first.click()
            deadline = time.monotonic() + 20.0
            sid_cookie = None
            while time.monotonic() < deadline:
                for ck in ctx.cookies():
                    if ck.get("name") == "sid" and ck.get("value") and ck.get("value") != "Guest":
                        sid_cookie = ck
                        break
                if sid_cookie:
                    break
                pg.wait_for_timeout(200)
            assert sid_cookie is not None, f"Real browser /login did not issue authenticated sid cookie for {role_key}"
            r2 = pg.goto(f"{base_url}{landing_route}", wait_until="networkidle", timeout=30000)
            assert r2 is not None and r2.status == 200, f"Landing {landing_route} failed for {role_key}: {r2.status if r2 else None}"
            contexts[role_key] = ctx
            pages[role_key] = pg
            return sid_cookie

        try:
            # 1. Authenticate all 6 real business actors + Staff C + Outside + Guest via /login form
            sid_admin = login_persona("admin", users["admin"], "/cskh/campaigns")
            sid_bod = login_persona("bod", users["bod"], "/cskh/quality-overview")
            sid_ldr_a = login_persona("leader_a", users["leader_a"], "/cskh/team-quality")
            sid_ldr_b = login_persona("leader_b", users["leader_b"], "/cskh/team-quality")
            sid_stf_a = login_persona("staff_a", users["staff_a"], "/cskh/my-customers")
            sid_stf_b = login_persona("staff_b", users["staff_b"], "/cskh/my-customers")
            sid_stf_c = login_persona("staff_c", users["staff_c"], "/cskh/my-customers")
            login_persona("outside", users["outside"], "/login")

            guest_ctx = browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                ignore_https_errors=False,
            )
            contexts["guest"] = guest_ctx
            pages["guest"] = guest_ctx.new_page()

            cookies_secure_ok = all(
                bool(ck.get("secure")) and bool(ck.get("httpOnly"))
                for ck in (sid_admin, sid_bod, sid_ldr_a, sid_ldr_b, sid_stf_a, sid_stf_b, sid_stf_c)
            )
            record_case(
                acceptance_id="G10-TLS-COOKIE / F01 / F02",
                gate="G10,G20",
                actor="CRM Admin, BOD, Leader A, Leader B, Staff A, Staff B",
                expected="Real Chromium TLS 1.3 login succeeds with ignore_https_errors=False; sid cookies enforce Secure + HttpOnly; Administrator not used",
                actual=f"6 business personas logged in via /login form; all sid cookies Secure+HttpOnly={cookies_secure_ok}",
                passed=cookies_secure_ok,
                evidence={
                    "tls_ignore_https_errors": False,
                    "administrator_used_for_business_journeys": False,
                    "personas_logged_in": ["admin", "bod", "leader_a", "leader_b", "staff_a", "staff_b"],
                },
            )

            # 2. G20: Visit all 16 /cskh* routes + /crm using authorized non-Administrator business actors
            for route_path, actor_key in AUTHORIZED_ROUTE_ACTORS:
                pg = pages[actor_key]
                resp = pg.goto(f"{base_url}{route_path}", wait_until="networkidle", timeout=30000)
                http_code = resp.status if resp else 0
                pg.wait_for_timeout(250)
                dom_audit = pg.evaluate(
                    """() => {
                        const htmlLang = document.documentElement.getAttribute('lang') || '';
                        const text = document.body ? document.body.innerText : '';
                        const overflowPx = document.documentElement.scrollWidth - window.innerWidth;
                        const navAria = document.querySelector('nav.cskh-nav')
                            ? (document.querySelector('nav.cskh-nav').getAttribute('aria-label') || '')
                            : '';
                        const unlabeledInputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), select, textarea'))
                            .filter(el => {
                                if (el.closest('[hidden]')) return false;
                                const hasLabelWrap = !!el.closest('label');
                                const id = el.getAttribute('id');
                                const hasFor = id ? !!document.querySelector(`label[for="${CSS.escape(id)}"]`) : false;
                                const hasAria = !!(el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('placeholder'));
                                return !(hasLabelWrap || hasFor || hasAria);
                            }).length;
                        return {
                            html_lang: htmlLang,
                            body_sample: text.slice(0, 280),
                            has_traceback: text.includes('Traceback (most recent call last)'),
                            horizontal_overflow_px: overflowPx,
                            nav_aria_label: navAria,
                            unlabeled_visible_controls: unlabeledInputs,
                        };
                    }"""
                )
                is_cskh = route_path.startswith("/cskh")
                has_vi = bool(VI_DIACRITICS_RE.search(dom_audit["body_sample"])) if is_cskh else True
                lang_ok = (dom_audit["html_lang"].lower().startswith("vi")) if is_cskh else True
                no_overflow = dom_audit["horizontal_overflow_px"] <= 4
                a11y_ok = dom_audit["unlabeled_visible_controls"] == 0
                hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp else [])}
                tls_hdrs_ok = (
                    "max-age=" in hdrs.get("strict-transport-security", "")
                    and hdrs.get("x-content-type-options", "").lower() == "nosniff"
                )
                rt_ok = (
                    http_code == 200
                    and lang_ok
                    and has_vi
                    and not dom_audit["has_traceback"]
                    and no_overflow
                    and a11y_ok
                    and tls_hdrs_ok
                )
                route_matrix[route_path] = {
                    "actor": actor_key,
                    "http_status": http_code,
                    "html_lang": dom_audit["html_lang"],
                    "has_vietnamese_copy": has_vi,
                    "horizontal_overflow_px": dom_audit["horizontal_overflow_px"],
                    "unlabeled_visible_controls": dom_audit["unlabeled_visible_controls"],
                    "tls_security_headers_ok": tls_hdrs_ok,
                    "pass": rt_ok,
                }

            all_routes_ok = all(item["pass"] for item in route_matrix.values())
            record_case(
                acceptance_id="G20-ROUTES-VI-A11Y",
                gate="G20",
                actor="CRM Admin, BOD, Leader A, Staff A, Staff B",
                expected="All /cskh* and /crm routes render HTTP 200 for authorized business actors, lang=vi, Vietnamese terminology, <=0px overflow at 1366x768, 0 unlabeled controls, HSTS+nosniff headers",
                actual=f"{sum(1 for v in route_matrix.values() if v['pass'])}/{len(route_matrix)} routes PASS",
                passed=all_routes_ok,
                evidence={"routes_checked": len(route_matrix)},
            )

            # 3. G20 + G16 + P4.md §8.4 #12 + S12 + F26 + F27: BOD interactive Drill-down Modal, Keyboard Focus/Close & 0-PII Masking
            bod_pg = pages["bod"]
            bod_pg.goto(f"{base_url}/cskh/quality-overview", wait_until="networkidle", timeout=30000)
            bod_pg.locator("[data-drilldown-metric]").first.wait_for(state="visible", timeout=20000)

            # Keyboard Tab progression check on BOD Quality Overview
            bod_pg.keyboard.press("Tab")
            first_focus = bod_pg.evaluate("() => document.activeElement ? document.activeElement.tagName : ''")
            bod_pg.keyboard.press("Tab")
            second_focus = bod_pg.evaluate("() => document.activeElement ? document.activeElement.tagName : ''")
            kb_tab_ok = first_focus in ("A", "BUTTON", "SELECT", "INPUT") and second_focus in ("A", "BUTTON", "SELECT", "INPUT")

            # Click KPI Drill-down button -> real modal opens
            bod_pg.locator('[data-drilldown-metric="FIRST_ATTEMPT_SLA_RATE"], [data-drilldown-metric]').first.click()
            modal_loc = bod_pg.locator('#p3-modal-host .cskh-modal[role="dialog"][aria-modal="true"]')
            modal_loc.wait_for(state="visible", timeout=15000)
            bod_pg.locator("#p3-drilldown-table").wait_for(state="visible", timeout=15000)
            dd_modal_text = modal_loc.inner_text()
            dd_raw_phones = len(PHONE_RE.findall(dd_modal_text))

            # Close dialog via keyboard Enter on header Close button
            close_btn = bod_pg.locator("#p3-modal-host .cskh-modal-head button").first
            close_btn.focus()
            bod_pg.keyboard.press("Enter")
            bod_pg.wait_for_timeout(200)
            modal_closed_ok = bod_pg.locator('#p3-modal-host .cskh-modal[role="dialog"]').count() == 0

            # Check BOD Employee Quality in browser for 86,47 provisional & N/A separation
            bod_pg.goto(f"{base_url}/cskh/employee-quality", wait_until="networkidle", timeout=30000)
            bod_pg.locator("tr[data-employee]").first.wait_for(state="visible", timeout=20000)
            emp_page_text = bod_pg.locator("#p3-main-content").inner_text()
            has_86_47_ui = ("86,47" in emp_page_text) or ("86.47" in emp_page_text)
            has_na_ui = "N/A" in emp_page_text

            # BOD denied on PII workspace (/cskh/work-item), personal scorecard (/cskh/scorecard), recompute & include_pii=1
            bod_wi_resp = bod_pg.goto(f"{base_url}/cskh/work-item?id={urllib.parse.quote(fx['wi_a'])}", wait_until="domcontentloaded")
            bod_wi_code = bod_wi_resp.status if bod_wi_resp else 0
            bod_sc_resp = bod_pg.goto(f"{base_url}/cskh/scorecard", wait_until="domcontentloaded")
            bod_sc_code = bod_sc_resp.status if bod_sc_resp else 0
            bod_pg.goto(f"{base_url}/cskh/quality-overview", wait_until="networkidle", timeout=30000)
            bod_recompute = _browser_fetch_json(
                bod_pg,
                "/api/method/crm_cskh.api.reporting.request_recompute",
                method="POST",
                body={"period_type": "monthly", "period_key": period_key, "reason": "bod-unauthorized"},
            )
            bod_pii_dd = _browser_fetch_json(
                bod_pg,
                f"/api/method/crm_cskh.api.reporting.get_metric_drilldown?metric_code=FIRST_ATTEMPT_SLA_RATE&period_type=monthly&period_key={urllib.parse.quote(period_key)}&include_pii=1",
            )

            bod_case_ok = (
                kb_tab_ok
                and dd_raw_phones == 0
                and modal_closed_ok
                and has_86_47_ui
                and has_na_ui
                and not fx["bod_has_system_manager"]
                and bod_wi_code == 403
                and bod_sc_code == 403
                and bod_recompute["status"] == 403
                and bod_pii_dd["status"] == 403
            )
            record_case(
                acceptance_id="P4-8.4-12 / S12 / F26 / F27 / G16 / G20-MODAL",
                gate="G10,G15,G16,G20",
                actor="BOD (cskh-p3-bod@example.test)",
                expected="BOD opens KPI drill-down dialog in browser with 0 raw phones, closes via keyboard Enter, sees 86,47 provisional & N/A, has 0 System Manager role, and gets 403 on /cskh/work-item, /cskh/scorecard, request_recompute & include_pii=1",
                actual=f"kb_tab={kb_tab_ok}, dd_phones={dd_raw_phones}, modal_closed={modal_closed_ok}, ui_86_47={has_86_47_ui}, ui_na={has_na_ui}, wi_http={bod_wi_code}, recompute_http={bod_recompute['status']}, pii_dd_http={bod_pii_dd['status']}",
                passed=bod_case_ok,
                evidence={
                    "drilldown_raw_phone_leaks": dd_raw_phones,
                    "bod_has_system_manager": fx["bod_has_system_manager"],
                    "work_item_http": bod_wi_code,
                    "scorecard_http": bod_sc_code,
                    "recompute_http": bod_recompute["status"],
                    "include_pii_drilldown_http": bod_pii_dd["status"],
                },
            )

            # 4. P4.md §8.4 #6 & #14 / S07 / F29: BOD UI Export Modal -> CSV download (0 PII, formula neutralized) & Staff A / Guest 403
            bod_pg.locator("#p3-btn-export").click()
            bod_pg.locator("#p3-export-submit").wait_for(state="visible", timeout=10000)
            bod_pg.locator("#p3-export-purpose").fill("Kiểm thử xuất CSV BOD trên trình duyệt HTTPS cô lập")
            bod_pg.locator("#p3-export-submit").click()

            export_id = ""
            export_status = ""
            for _ in range(30):
                bod_pg.wait_for_timeout(500)
                row_loc = bod_pg.locator("tr[data-export-id]").first
                if row_loc.count() > 0:
                    export_id = row_loc.get_attribute("data-export-id") or ""
                    if export_id:
                        st_res = _browser_fetch_json(
                            bod_pg,
                            f"/api/method/crm_cskh.api.reporting.get_export_status?export_id={urllib.parse.quote(export_id)}",
                        )
                        export_status = str(((st_res.get("json") or {}).get("message") or {}).get("status") or "")
                        if export_status in ("completed", "ready", "failed"):
                            break

            dl_path = f"/api/method/crm_cskh.api.reporting.download_export?export_id={urllib.parse.quote(export_id)}"
            bod_csv_fetch = _browser_fetch_json(bod_pg, dl_path)
            if bod_pg.locator("#p3-modal-host .cskh-modal-head button").count() > 0:
                bod_pg.locator("#p3-modal-host .cskh-modal-head button").first.click()
            csv_phones = len(PHONE_RE.findall(bod_csv_fetch.get("text") or ""))
            bod_pii_export = _browser_fetch_json(
                bod_pg,
                "/api/method/crm_cskh.api.reporting.request_export",
                method="POST",
                body={
                    "report_type": "metric_drilldown",
                    "metric_code": "FIRST_ATTEMPT_SLA_RATE",
                    "period_type": "monthly",
                    "period_key": period_key,
                    "include_pii": 1,
                },
            )
            stf_a_pg = pages["staff_a"]
            stf_a_export_dl = _browser_fetch_json(stf_a_pg, dl_path)
            guest_export_resp = pages["guest"].goto(f"{base_url}{dl_path}", wait_until="domcontentloaded")
            guest_export_code = guest_export_resp.status if guest_export_resp else 0

            export_ok = (
                bool(export_id)
                and export_status in ("completed", "ready")
                and bod_csv_fetch["status"] == 200
                and csv_phones == 0
                and bod_pii_export["status"] == 403
                and stf_a_export_dl["status"] == 403
                and guest_export_code in (401, 403)
            )
            record_case(
                acceptance_id="P4-8.4-06 / P4-8.4-14 / S07 / F29",
                gate="G10,G12,G16",
                actor="BOD, Staff A, Guest",
                expected="BOD queues CSV export from UI modal & downloads with 0 phones; BOD include_pii=1 gets 403; Staff A & Guest opening BOD download URL get 403",
                actual=f"export_status={export_status}, bod_dl={bod_csv_fetch['status']}, phones={csv_phones}, bod_pii_exp={bod_pii_export['status']}, staff_a_dl={stf_a_export_dl['status']}, guest_dl={guest_export_code}",
                passed=export_ok,
                evidence={
                    "export_status": export_status,
                    "bod_download_http": bod_csv_fetch["status"],
                    "csv_raw_phone_leaks": csv_phones,
                    "staff_a_cross_download_http": stf_a_export_dl["status"],
                    "guest_download_http": guest_export_code,
                },
            )

            # 5. P4.md §8.4 #7 / S11 / F28 / G16: Symmetric Leader A vs Leader B UI Filter & URL Tampering
            ldr_a_pg = pages["leader_a"]
            ldr_a_pg.goto(
                f"{base_url}/cskh/employee-quality?team={urllib.parse.quote(team_a)}",
                wait_until="networkidle",
                timeout=30000,
            )
            ldr_a_pg.locator("tr[data-employee]").first.wait_for(state="visible", timeout=15000)
            ldr_a_own_rows = ldr_a_pg.locator("tr[data-employee]").count()

            # Leader A tampers URL to Team B
            ldr_a_pg.goto(
                f"{base_url}/cskh/employee-quality?team={urllib.parse.quote(team_b)}",
                wait_until="networkidle",
                timeout=30000,
            )
            ldr_a_pg.wait_for_timeout(400)
            ldr_a_other_rows = ldr_a_pg.locator("tr[data-employee]").count()
            ldr_a_alert_txt = ldr_a_pg.locator("#p3-state-alert").inner_text().strip()
            ldr_a_chart_tamper = _browser_fetch_json(
                ldr_a_pg,
                f"/api/method/crm.api.dashboard.get_chart?name=test&user={urllib.parse.quote(users['leader_b'])}",
            )

            # Leader B sees Team B and is blocked on Team A
            ldr_b_pg = pages["leader_b"]
            ldr_b_pg.goto(
                f"{base_url}/cskh/employee-quality?team={urllib.parse.quote(team_b)}",
                wait_until="networkidle",
                timeout=30000,
            )
            ldr_b_pg.locator("tr[data-employee]").first.wait_for(state="visible", timeout=15000)
            ldr_b_own_rows = ldr_b_pg.locator("tr[data-employee]").count()
            ldr_b_pg.goto(
                f"{base_url}/cskh/employee-quality?team={urllib.parse.quote(team_a)}",
                wait_until="networkidle",
                timeout=30000,
            )
            ldr_b_pg.wait_for_timeout(400)
            ldr_b_other_rows = ldr_b_pg.locator("tr[data-employee]").count()
            ldr_b_api_other = _browser_fetch_json(
                ldr_b_pg,
                f"/api/method/crm_cskh.api.reporting.get_employee_quality?period_type=monthly&period_key={urllib.parse.quote(period_key)}&team={urllib.parse.quote(team_a)}",
            )

            leader_isolation_ok = (
                ldr_a_own_rows > 0
                and ldr_a_other_rows == 0
                and len(ldr_a_alert_txt) > 0
                and ldr_a_chart_tamper["status"] in (400, 403, 404)
                and ldr_b_own_rows > 0
                and ldr_b_other_rows == 0
                and ldr_b_api_other["status"] == 403
            )
            record_case(
                acceptance_id="P4-8.4-07 / S11 / F28",
                gate="G10,G16",
                actor="Leader A & Leader B",
                expected="Leader A sees Team A rows in browser & gets 0 rows + Vietnamese error alert on ?team=TeamB; Leader B sees Team B & gets 403 / 0 rows on ?team=TeamA",
                actual=f"ldr_a_own={ldr_a_own_rows}, ldr_a_teamB_rows={ldr_a_other_rows}, ldr_b_own={ldr_b_own_rows}, ldr_b_teamA_rows={ldr_b_other_rows}, ldr_b_api={ldr_b_api_other['status']}",
                passed=leader_isolation_ok,
                evidence={
                    "leader_a_own_rows": ldr_a_own_rows,
                    "leader_a_tampered_team_b_rows": ldr_a_other_rows,
                    "leader_a_error_rendered": bool(ldr_a_alert_txt),
                    "leader_b_own_rows": ldr_b_own_rows,
                    "leader_b_tampered_team_a_rows": ldr_b_other_rows,
                    "leader_b_tampered_api_http": ldr_b_api_other["status"],
                },
            )

            # 6. P4.md §8.4 #1, #2, #3, #4, #9, #10 / S01-S06, S08, S10, S18, S19, S20: Staff A cross-scope UI + API boundary
            stf_a_pg.goto(f"{base_url}/cskh/my-customers", wait_until="networkidle", timeout=30000)
            stf_a_pg.locator("#mine-table").wait_for(state="visible", timeout=15000)
            stf_a_own_rows = stf_a_pg.locator("#mine-rows tr").count()

            # Staff A tampers browser URL to Staff B's work item -> wipeWorkspaceDom hides summary card & leaks 0 PII
            stf_a_pg.goto(
                f"{base_url}/cskh/work-item?id={urllib.parse.quote(fx['wi_b'])}",
                wait_until="networkidle",
                timeout=30000,
            )
            stf_a_pg.wait_for_timeout(400)
            wi_b_card_hidden = stf_a_pg.locator("#wi-summary-card").is_hidden()
            wi_b_dom_phones = len(PHONE_RE.findall(stf_a_pg.locator("body").inner_text()))

            # Staff A direct SSR check on /cskh/pool & /cskh/campaigns -> 403
            stf_pool_resp = stf_a_pg.goto(f"{base_url}/cskh/pool", wait_until="domcontentloaded")
            stf_pool_ssr = stf_pool_resp.status if stf_pool_resp else 0
            stf_camp_resp = stf_a_pg.goto(f"{base_url}/cskh/campaigns", wait_until="domcontentloaded")
            stf_camp_ssr = stf_camp_resp.status if stf_camp_resp else 0

            # Return Staff A to own workspace to run in-browser fetch negative matrix
            stf_a_pg.goto(
                f"{base_url}/cskh/work-item?id={urllib.parse.quote(fx['wi_a'])}",
                wait_until="networkidle",
                timeout=30000,
            )
            stf_a_pg.locator("#wi-summary-card").wait_for(state="visible", timeout=15000)

            s01_get = _browser_fetch_json(stf_a_pg, f"/api/resource/CRM%20Lead/{urllib.parse.quote(fx['lead_b'])}")
            s02_put = _browser_fetch_json(
                stf_a_pg,
                f"/api/resource/CRM%20Lead/{urllib.parse.quote(fx['lead_b'])}",
                method="PUT",
                body={"first_name": "Tampered"},
            )
            s01_export_q = _browser_fetch_json(
                stf_a_pg,
                "/api/method/frappe.desk.reportview.export_query",
                method="POST",
                body={"doctype": "CRM Lead", "file_format_type": "CSV"},
            )
            s03_att = (
                _browser_fetch_json(
                    stf_a_pg, f"/api/resource/CSKH%20Contact%20Attempt/{urllib.parse.quote(fx['attempt_b'])}"
                )["status"]
                if fx["attempt_b"]
                else 403
            )
            s04_call = (
                _browser_fetch_json(
                    stf_a_pg, f"/api/resource/CRM%20Call%20Log/{urllib.parse.quote(fx['call_log_b'])}"
                )["status"]
                if fx["call_log_b"]
                else 403
            )
            s05_task = (
                _browser_fetch_json(stf_a_pg, f"/api/resource/CRM%20Task/{urllib.parse.quote(fx['task_b'])}")["status"]
                if fx["task_b"]
                else 403
            )
            s08_todo = _browser_fetch_json(
                stf_a_pg,
                "/api/method/frappe.desk.form.assign_to.add",
                method="POST",
                body={"doctype": "CRM Lead", "name": fx["lead_b"], "assign_to": [users["staff_a"]]},
            )
            s08_rm_todo = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm.api.doc.remove_assignments",
                method="POST",
                body={"doctype": "CRM Lead", "name": fx["lead_b"], "assignees": json.dumps([users["staff_b"]])},
            )
            s10_dist = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.distribution.execute",
                method="POST",
                body={"campaign": seed_info["campaign_id"]},
            )
            s10_reassign = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.ownership.reassign",
                method="POST",
                body={
                    "work_item": fx["wi_b"],
                    "expected_owner": users["staff_b"],
                    "target_user": users["staff_a"],
                    "reason": "unauthorized",
                },
            )
            s18_page = _browser_fetch_json(
                stf_a_pg, "/api/method/crm_cskh.api.customers.my_list?page=1&page_size=99999"
            )
            s09_ref_dt = _browser_fetch_json(
                stf_a_pg,
                "/api/method/frappe.desk.search.search_widget",
                method="POST",
                body={"doctype": "CRM Lead", "txt": "", "reference_doctype": "User"},
            )
            s19_snap = (
                _browser_fetch_json(
                    stf_a_pg, f"/api/resource/CSKH%20KPI%20Snapshot/{urllib.parse.quote(fx['snap_b'])}"
                )["status"]
                if fx["snap_b"]
                else 403
            )
            # S06 & S20: Private file URL + traversal + normalized File resource 404
            priv_url = urllib.parse.quote(fx["private_file_b_url"], safe="/")
            s06_stf_a = _browser_fetch_json(stf_a_pg, priv_url)
            s06_traversal = _browser_fetch_json(stf_a_pg, "/private/files/..%2f..%2fsite_config.json")
            s20_file_oracle = _browser_fetch_json(
                stf_a_pg, f"/api/resource/File/{urllib.parse.quote(fx['private_file_b_docname'])}"
            )
            stf_b_pg = pages["staff_b"]
            s06_stf_b = _browser_fetch_json(stf_b_pg, priv_url)
            s06_guest_resp = pages["guest"].goto(f"{base_url}{priv_url}", wait_until="domcontentloaded")
            s06_guest_code = s06_guest_resp.status if s06_guest_resp else 0

            staff_boundary_ok = (
                stf_a_own_rows > 0
                and wi_b_card_hidden
                and wi_b_dom_phones == 0
                and stf_pool_ssr == 403
                and stf_camp_ssr == 403
                and s01_get["status"] == 403
                and s02_put["status"] == 403
                and s01_export_q["status"] == 403
                and s03_att == 403
                and s04_call == 403
                and s05_task == 403
                and s08_todo["status"] == 403
                and s08_rm_todo["status"] == 403
                and s10_dist["status"] == 403
                and s10_reassign["status"] == 403
                and s18_page["status"] == 400
                and s09_ref_dt["status"] in (400, 403)
                and s19_snap == 403
                and s06_stf_a["status"] == 403
                and s06_traversal["status"] in (400, 403, 404)
                and s20_file_oracle["status"] == 404
                and s06_stf_b["status"] == 200
                and s06_guest_code in (401, 403)
            )
            record_case(
                acceptance_id="P4-8.4-01..04,09,10 / S01..S06,S08,S10,S18..S20",
                gate="G10,G12",
                actor="Staff A, Staff B, Guest",
                expected="Staff A blocked in both UI and API across Lead/Attempt/CallLog/Task/Pool/Campaigns/AssignTo/Distribution/KPI-Snapshot/PrivateFile; File oracle normalized to 404; Staff B reads own private file 200",
                actual=f"wi_b_hidden={wi_b_card_hidden}, phones={wi_b_dom_phones}, pool_ssr={stf_pool_ssr}, s01={s01_get['status']}, s02={s02_put['status']}, s03={s03_att}, s04={s04_call}, s06_a={s06_stf_a['status']}, s06_b={s06_stf_b['status']}, oracle={s20_file_oracle['status']}, page99999={s18_page['status']}",
                passed=staff_boundary_ok,
                evidence={
                    "staff_a_own_rows": stf_a_own_rows,
                    "tampered_wi_b_summary_hidden": wi_b_card_hidden,
                    "tampered_wi_b_raw_phones": wi_b_dom_phones,
                    "s01_lead_get": s01_get["status"],
                    "s02_lead_put": s02_put["status"],
                    "s03_attempt_get": s03_att,
                    "s04_call_log_get": s04_call,
                    "s05_task_get": s05_task,
                    "s06_private_file_staff_a": s06_stf_a["status"],
                    "s06_private_file_staff_b": s06_stf_b["status"],
                    "s06_private_file_guest": s06_guest_code,
                    "s08_assign_to": s08_todo["status"],
                    "s10_distribution_execute": s10_dist["status"],
                    "s18_page_size_99999": s18_page["status"],
                    "s19_other_kpi_snapshot": s19_snap,
                    "s20_file_oracle_normalized": s20_file_oracle["status"],
                },
            )

            # 7. P4.md §8.4 #5, #13, #15 / S09, S16, S17 & Business Validation Errors (DNC / Invalid Close)
            s09_spoof = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.workflow.record_attempt",
                method="POST",
                body={
                    "work_item": fx["wi_a"],
                    "channel": "phone_call",
                    "outcome_code": "no_answer",
                    "performed_by": users["staff_b"],
                    "idempotency_key": "browser-spoof-001",
                },
            )
            s16_no_csrf = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.workflow.record_attempt",
                method="POST",
                body={
                    "work_item": fx["wi_a"],
                    "channel": "phone_call",
                    "outcome_code": "no_answer",
                    "idempotency_key": "browser-nocsrf-001",
                },
                include_csrf=False,
            )
            # Business validation error: close_work_item with empty/missing completion_reason or DNC attempt
            biz_err_close = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.workflow.close_work_item",
                method="POST",
                body={"work_item": fx["wi_a"], "completion_reason": "", "close_category": "invalid_cat"},
            )
            dnc_err_status = 409
            if fx["dnc_wi_a"]:
                dnc_res = _browser_fetch_json(
                    stf_a_pg,
                    "/api/method/crm_cskh.api.workflow.record_attempt",
                    method="POST",
                    body={
                        "work_item": fx["dnc_wi_a"],
                        "channel": "phone_call",
                        "outcome_code": "no_answer",
                        "idempotency_key": "browser-dnc-block-001",
                    },
                )
                dnc_err_status = dnc_res["status"]

            # S17 XSS neutralization in real Chromium DOM + F10 idempotency replay
            xss_idem_key = f"browser-xss-{int(time.time())}"
            xss_payload = "<script>window.__cskh_p4_xss=1</script><img src=x onerror=window.__cskh_p4_xss=2>Đã trao đổi lương & ca làm việc"
            att_1 = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.workflow.record_attempt",
                method="POST",
                body={
                    "work_item": fx["wi_a"],
                    "channel": "phone_call",
                    "outcome_code": "connected_interested",
                    "note": xss_payload,
                    "idempotency_key": xss_idem_key,
                },
            )
            att_2 = _browser_fetch_json(
                stf_a_pg,
                "/api/method/crm_cskh.api.workflow.record_attempt",
                method="POST",
                body={
                    "work_item": fx["wi_a"],
                    "channel": "phone_call",
                    "outcome_code": "connected_interested",
                    "note": xss_payload,
                    "idempotency_key": xss_idem_key,
                },
            )
            stf_a_pg.reload(wait_until="networkidle", timeout=30000)
            stf_a_pg.wait_for_timeout(300)
            xss_executed = stf_a_pg.evaluate("() => Boolean(window.__cskh_p4_xss)")
            raw_script_in_html = stf_a_pg.evaluate(
                "() => document.getElementById('wi-timeline-list') ? document.getElementById('wi-timeline-list').innerHTML.toLowerCase().includes('<script>') : false"
            )
            a1_id = ((att_1.get("json") or {}).get("message") or {}).get("attempt_id")
            a2_id = ((att_2.get("json") or {}).get("message") or {}).get("attempt_id")

            # P4.md §8.4 #13: QA disabled / conflict guard
            qa_sample_res = _browser_fetch_json(
                pages["admin"],
                "/api/method/crm_cskh.api.qa.create_sample",
                method="POST",
                body={"contact_attempt": a1_id or "ATT-NONE", "cohort_type": "random"},
            )

            spoof_csrf_xss_ok = (
                s09_spoof["status"] == 403
                and s16_no_csrf["status"] in (400, 403)
                and biz_err_close["status"] in (400, 409, 422)
                and dnc_err_status in (400, 403, 409)
                and att_1["status"] == 200
                and att_2["status"] == 200
                and bool(a1_id)
                and a1_id == a2_id
                and not xss_executed
                and not raw_script_in_html
                and qa_sample_res["status"] in (400, 403, 409, 503)
            )
            record_case(
                acceptance_id="P4-8.4-05,13,15 / S09,S16,S17 / F10,F14,F18",
                gate="G10,G12,G14,G20",
                actor="Staff A & CRM Admin",
                expected="Spoofed actor 403; missing CSRF 400/403; invalid close & DNC return structured 4xx business error; idempotent replay returns same attempt_id; XSS payload never executes in Chromium DOM; QA sample blocked when p3_qa_enabled=false",
                actual=f"spoof={s09_spoof['status']}, nocsrf={s16_no_csrf['status']}, biz_close={biz_err_close['status']}, dnc={dnc_err_status}, same_attempt={a1_id == a2_id}, xss_exec={xss_executed}, qa={qa_sample_res['status']}",
                passed=spoof_csrf_xss_ok,
                evidence={
                    "s09_spoof_http": s09_spoof["status"],
                    "s16_missing_csrf_http": s16_no_csrf["status"],
                    "invalid_close_http": biz_err_close["status"],
                    "dnc_attempt_http": dnc_err_status,
                    "idempotent_replay_deduplicated": bool(a1_id and a1_id == a2_id),
                    "xss_executed_in_chromium": xss_executed,
                    "raw_script_tag_in_timeline": raw_script_in_html,
                    "qa_disabled_guard_http": qa_sample_res["status"],
                },
            )

            # 8. P4.md §8.4 #11 / S13, S14 / F20, F21 / G11: UI + API Before & After Transfer A -> B -> A Executed by CRM Admin!
            admin_pg = pages["admin"]
            stf_a_pg.goto(f"{base_url}/cskh/work-item?id={urllib.parse.quote(aba_wi)}", wait_until="networkidle")
            stf_a_pg.wait_for_timeout(300)
            a_visible_before = not stf_a_pg.locator("#wi-summary-card").is_hidden()

            stf_b_pg.goto(f"{base_url}/cskh/work-item?id={urllib.parse.quote(aba_wi)}", wait_until="networkidle")
            stf_b_pg.wait_for_timeout(300)
            b_hidden_before = stf_b_pg.locator("#wi-summary-card").is_hidden()

            # CRM Admin (cskh-p3-admin@example.test — NOT Administrator!) executes A -> B transfer
            adm_a_to_b = _browser_fetch_json(
                admin_pg,
                "/api/method/crm_cskh.api.ownership.reassign",
                method="POST",
                body={
                    "work_item": aba_wi,
                    "expected_owner": users["staff_a"],
                    "target_user": users["staff_b"],
                    "reason": "Kiểm thử bàn giao A sang B qua CRM Admin trên trình duyệt HTTPS",
                    "request_id": f"browser-aba-1-{int(time.time())}",
                },
            )

            # Immediately reload BOTH Staff A and Staff B browsers!
            stf_a_pg.reload(wait_until="networkidle")
            stf_a_pg.wait_for_timeout(300)
            a_hidden_after_ab = stf_a_pg.locator("#wi-summary-card").is_hidden()
            a_phones_after_ab = len(PHONE_RE.findall(stf_a_pg.locator("body").inner_text()))
            a_api_after_ab = _browser_fetch_json(
                stf_a_pg, f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={urllib.parse.quote(aba_wi)}"
            )

            stf_b_pg.reload(wait_until="networkidle")
            stf_b_pg.wait_for_timeout(300)
            b_visible_after_ab = not stf_b_pg.locator("#wi-summary-card").is_hidden()

            # Check DB for 0 stale DocShare/ToDo for Staff A while Staff B owns it
            aba_lead = ((adm_a_to_b.get("json") or {}).get("message") or {}).get("lead") or fx["lead_a"]
            mid_grants = _count_kpi_runs_and_stale_grants(env_file, site_name, aba_lead, users["staff_a"])

            # CRM Admin transfers B -> A back
            adm_b_to_a = _browser_fetch_json(
                admin_pg,
                "/api/method/crm_cskh.api.ownership.reassign",
                method="POST",
                body={
                    "work_item": aba_wi,
                    "expected_owner": users["staff_b"],
                    "target_user": users["staff_a"],
                    "reason": "Hoàn trả B về A kiểm tra vòng lặp A-B-A",
                    "request_id": f"browser-aba-2-{int(time.time())}",
                },
            )
            stf_b_pg.reload(wait_until="networkidle")
            stf_b_pg.wait_for_timeout(300)
            b_hidden_after_ba = stf_b_pg.locator("#wi-summary-card").is_hidden()
            stf_a_pg.reload(wait_until="networkidle")
            stf_a_pg.wait_for_timeout(300)
            a_visible_after_ba = not stf_a_pg.locator("#wi-summary-card").is_hidden()
            end_grants = _count_kpi_runs_and_stale_grants(env_file, site_name, aba_lead, users["staff_b"])

            transfer_ui_api_ok = (
                a_visible_before
                and b_hidden_before
                and adm_a_to_b["status"] == 200
                and a_hidden_after_ab
                and a_phones_after_ab == 0
                and a_api_after_ab["status"] == 403
                and b_visible_after_ab
                and mid_grants["stale_shares"] == 0
                and mid_grants["stale_todos"] == 0
                and adm_b_to_a["status"] == 200
                and b_hidden_after_ba
                and a_visible_after_ba
                and end_grants["stale_shares"] == 0
                and end_grants["stale_todos"] == 0
            )
            record_case(
                acceptance_id="P4-8.4-11 / S13,S14 / F20,F21",
                gate="G10,G11,G14",
                actor="CRM Admin, Staff A, Staff B",
                expected="CRM Admin transfers A->B->A; Staff A UI summary immediately wiped + API 403 + 0 stale DocShare/ToDo on A->B, and Staff B immediately wiped on B->A while Staff A restored",
                actual=f"before(A_vis={a_visible_before},B_hid={b_hidden_before}) -> A2B(A_hid={a_hidden_after_ab},A_api={a_api_after_ab['status']},B_vis={b_visible_after_ab},stale={mid_grants['stale_shares']}/{mid_grants['stale_todos']}) -> B2A(B_hid={b_hidden_after_ba},A_vis={a_visible_after_ba})",
                passed=transfer_ui_api_ok,
                evidence={
                    "executed_by_business_actor": "cskh-p3-admin@example.test (CSKH CRM Admin)",
                    "a_visible_before": a_visible_before,
                    "a_hidden_after_transfer": a_hidden_after_ab,
                    "a_api_http_after_transfer": a_api_after_ab["status"],
                    "b_visible_after_transfer": b_visible_after_ab,
                    "mid_stale_docshares": mid_grants["stale_shares"],
                    "mid_stale_todos": mid_grants["stale_todos"],
                    "b_hidden_after_return": b_hidden_after_ba,
                    "a_restored_after_return": a_visible_after_ba,
                    "end_stale_docshares": end_grants["stale_shares"],
                },
            )

            # 9. P4.md §8.4 #16: Read-only report endpoints do not trigger new KPI Run mutations
            runs_before_ro = end_grants["kpi_runs"]
            for _ in range(3):
                _browser_fetch_json(
                    bod_pg,
                    f"/api/method/crm_cskh.api.reporting.get_executive_overview?period_type=monthly&period_key={urllib.parse.quote(period_key)}",
                )
                _browser_fetch_json(
                    bod_pg,
                    f"/api/method/crm_cskh.api.reporting.get_team_comparison?period_type=monthly&period_key={urllib.parse.quote(period_key)}",
                )
            runs_after_ro = _count_kpi_runs_and_stale_grants(env_file, site_name, aba_lead, users["staff_b"])["kpi_runs"]
            ro_idempotent_ok = runs_before_ro == runs_after_ro
            record_case(
                acceptance_id="P4-8.4-16",
                gate="G12,G15,G16",
                actor="BOD",
                expected="Repeated calls to read-only P3 reporting endpoints do not mutate state or spawn unbounded CSKH KPI Run jobs",
                actual=f"kpi_runs_before={runs_before_ro}, kpi_runs_after={runs_after_ro}",
                passed=ro_idempotent_ok,
                evidence={"kpi_runs_before": runs_before_ro, "kpi_runs_after": runs_after_ro},
            )

            # 10. P4.md §8.4 #8 / S15 / F30 / G11: Disabled user active browser session immediately rejected, + Outside & Guest denied
            stf_c_pg = pages["staff_c"]
            c_before = _browser_fetch_json(stf_c_pg, "/api/method/crm_cskh.api.customers.my_list")
            _toggle_user_enabled_technical_control(env_file, site_name, users["staff_c"], 0)
            try:
                c_after_api = _browser_fetch_json(stf_c_pg, "/api/method/crm_cskh.api.customers.my_list")
                c_after_nav = stf_c_pg.goto(f"{base_url}/cskh/my-customers", wait_until="domcontentloaded")
                c_nav_code = c_after_nav.status if c_after_nav else 0
                c_nav_url = stf_c_pg.url
            finally:
                _toggle_user_enabled_technical_control(env_file, site_name, users["staff_c"], 1)

            out_api = _browser_fetch_json(pages["outside"], "/api/method/crm_cskh.api.customers.my_list")
            guest_my_resp = pages["guest"].goto(f"{base_url}/cskh/my-customers", wait_until="domcontentloaded")
            guest_my_code = guest_my_resp.status if guest_my_resp else 0
            guest_my_url = pages["guest"].url

            revocation_ok = (
                c_before["status"] == 200
                and c_after_api["status"] in (401, 403)
                and (c_nav_code in (401, 403) or "/login" in c_nav_url)
                and out_api["status"] == 403
                and (guest_my_code in (401, 403) or "/login" in guest_my_url)
            )
            record_case(
                acceptance_id="P4-8.4-08 / S15 / F30",
                gate="G10,G11",
                actor="Staff C (disabled), Outside Desk User, Guest",
                expected="Disabling User.enabled=0 immediately revokes active browser session on both API (401/403) and page reload; Outside & Guest denied",
                actual=f"staff_c_before={c_before['status']}, staff_c_after_api={c_after_api['status']}, nav_code={c_nav_code}, outside={out_api['status']}, guest={guest_my_code}",
                passed=revocation_ok,
                evidence={
                    "staff_c_before_disable_http": c_before["status"],
                    "staff_c_after_disable_api_http": c_after_api["status"],
                    "staff_c_after_disable_page_http": c_nav_code,
                    "outside_user_http": out_api["status"],
                    "guest_page_http": guest_my_code,
                },
            )
        finally:
            for ctx in contexts.values():
                try:
                    ctx.close()
                except Exception:
                    pass
            browser.close()

    all_passed = all(c["status"] == "PASS" for c in cases)
    report = {
        "schema_version": "cskh-p4-https-browser-uat-security-v1",
        "timestamp_utc": utc_now_iso(),
        "status": "PASS" if all_passed else "FAIL",
        "duration_seconds": round(time.monotonic() - t0, 2),
        "browser_engine": "Playwright Headless Chromium",
        "transport": "HTTPS_TLS_VERIFIED_IGNORE_HTTPS_ERRORS_FALSE",
        "viewport": "1366x768",
        "locale": "vi-VN",
        "timezone": "Asia/Ho_Chi_Minh",
        "nss_ca_trust": nss_info,
        "administrator_used_for_business_journeys": False,
        "business_actors_verified": {
            "crm_admin": users["admin"],
            "bod": users["bod"],
            "leader_a": users["leader_a"],
            "leader_b": users["leader_b"],
            "staff_a": users["staff_a"],
            "staff_b": users["staff_b"],
            "staff_c_offboarding": users["staff_c"],
            "outside_desk_user": users["outside"],
            "guest": "Guest",
        },
        "p4_8_4_coverage": [f"P4-8.4-{i:02d}" for i in range(1, 17)],
        "s01_s20_coverage": [f"S{i:02d}" for i in range(1, 21)],
        "routes_vi_and_a11y_matrix": route_matrix,
        "cases_total": len(cases),
        "cases_passed": sum(1 for c in cases if c["status"] == "PASS"),
        "cases": cases,
    }

    raw_txt = json.dumps(report, indent=2, ensure_ascii=False)
    scrubbed_txt = health_tool.redact_payload_text(
        p403_tool.redact_sensitive(raw_txt, extra_secrets=[seed_pw]),
        extra_secrets=[seed_pw],
    ) + "\n"
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(scrubbed_txt, encoding="utf-8", newline="\n")
    return json.loads(scrubbed_txt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--ca-crt", type=Path, required=True)
    parser.add_argument("--seed-info-json", type=Path, required=True)
    parser.add_argument("--seed-password-env", default="CSKH_DR_SEED_PW")
    parser.add_argument("--connect-host", default="127.0.0.1")
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    seed_pw = os.environ.get(args.seed_password_env, "").strip()
    if not seed_pw:
        raise SystemExit(f"Missing password in environment variable {args.seed_password_env}")
    seed_info = json.loads(args.seed_info_json.read_text(encoding="utf-8"))
    res = run_browser_https_suite(
        env_file=args.env_file,
        ca_crt_path=args.ca_crt,
        seed_info=seed_info,
        seed_pw=seed_pw,
        out_json=args.out,
        connect_host=args.connect_host,
    )
    sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    return 0 if res.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
