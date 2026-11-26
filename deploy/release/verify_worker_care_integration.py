#!/usr/bin/env python3
"""End-to-end real-Frappe + real-Vue integration verifier for the Worker Care transition.

Executes against an immutable production `APP_IMAGE` (`@sha256` digest) booted via
`deploy/images/compose.verify.yaml` (`--no-build`, zero host code bind-mounts):

1. Real Frappe Migration & Test Suites (`worker-care-suites.localhost`):
   - Verifies clean upstream `apps/frappe` & `apps/crm`.
   - Verifies fresh-install `CRM Form Script` `"CSKH - Open CSKH Page from Deal"` has `enabled == 0`.
   - Simulates pre-upgrade site (`enabled = 1` + stale Redis cache key), runs `bench migrate`
     TWICE, confirms `enabled == 0` in MariaDB, Lead scripts remain `enabled == 1`, and cache cleared.
   - Executes `bench run-tests` with JUnit XML verification for:
     * `crm_cskh.tests.test_worker_overview`
     * `crm_cskh.tests.test_ui_p1_endpoints`
     * `crm_cskh.tests.test_ui_p2_pages_and_context`
     * `crm_cskh.tests.test_ui_p3_pages_and_context`

2. MariaDB-to-Overview Data & Permission Reconciliation (`worker-care-e2e.localhost`):
   - Seeds >70 synthetic `[DỮ LIỆU GIẢ LẬP]` records across Team A (`staff.a`, `staff.b`, `leader.a`),
     Team B (`staff.c`, `leader.b`), `crm.admin`, and `bod.viewer`:
     * >60 active Work Items for `staff.a` across 55 distinct worker Leads (`active > contact_records`,
       exceeding 20/50 page sizes).
     * Multi-campaign DNC Lead, `converted=1` Lead, legacy `CRM Deal`, pinned Deal & Lead views,
       `Contact`, `CRM Organization`, overdue/today/tomorrow/Done/Cancelled callbacks.
   - Reconciles live HTTP `get_overview` JSON 1:1 against raw MariaDB SQL for `Staff`, `Leader`,
     `CRM Admin`, and `BOD` (verifying 0 PII and 0 sales/hiring fields), plus live `reassign` and
     `Asia/Ho_Chi_Minh` vs `Asia/Tokyo` day-boundary shifts.

3. Real Built Vue Bundle Browser Verification (Playwright Chromium Desktop 1440x900 & Mobile 390x844):
   - Tests `Staff`, `Leader`, `CRM Admin` (+ `System Manager` for Settings/Onboarding/Import), and `BOD`:
     * `/login` -> `/cskh`, App Switcher, old sales URLs (`/crm`, `/crm/dashboard`, `/crm/deals`,
       `/crm/forecast`, `/crm/revenue`, `/crm/data-import/doctype/CRM Deal`) -> `/cskh`,
       refresh, Back/Forward, SPA `pushState` guard.
     * Desktop & Mobile `/crm/leads` & `/crm/leads/<id>` sidebars, `Ctrl+K` Command Palette +
       `ArrowDown` / `ArrowUp` / `Enter` keyboard guards + Lead search vs Deal exclusion.
     * Absence of `Convert to Deal`, Mobile `Convert`, Contact/Organization `Deals` tabs,
       pinned `CRM Deal` views, Settings `Dashboard`, and Onboarding Deal steps.
     * Honest API error (non-zero status never renders fake `0`s) and stale snapshot states on `/cskh`.
     * Valid workflows intact: Lead field `Save` on `/crm/leads/<id>`, native Call Log access, and
       recording a contact attempt + scheduling a follow-up callback on `/cskh/work-item`.
     * Captures synthetic-data watermarked Desktop & Mobile PNG screenshots and asserts zero uncaught
       Vue / browser `pageerror` exceptions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGES_DIR = REPO_ROOT / "deploy" / "images"
LOCK_FILE = IMAGES_DIR / "release-lock.json"
COMPOSE_FILE = IMAGES_DIR / "compose.verify.yaml"

SUITE_MODULES: tuple[tuple[str, str], ...] = (
    ("crm_cskh.tests.test_worker_overview", "junit"),
    ("crm_cskh.tests.test_ui_p1_endpoints", "junit"),
    ("crm_cskh.tests.test_ui_p2_pages_and_context", "junit"),
    ("crm_cskh.tests.test_ui_p3_pages_and_context", "junit-ui"),
)


def run_cmd(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        return subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged,
            check=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command {args[:6]} failed (rc={exc.returncode}):\n"
            f"STDOUT:\n{(exc.stdout or '')[-2500:]}\n"
            f"STDERR:\n{(exc.stderr or '')[-2500:]}"
        ) from exc


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpActorSession:
    def __init__(self, base_url: str, site_host: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.site_host = site_host
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirectHandler(),
        )
        self.csrf_token = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 25,
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = {"Host": self.site_host, "Accept-Language": "vi-VN,vi;q=0.9"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=hdrs,
            method=method.upper(),
        )
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return (
                    resp.status,
                    {k.lower(): v for k, v in resp.headers.items()},
                    resp.read(),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            hdr_map = (
                {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            )
            return exc.code, hdr_map, raw

    def login(self, usr: str, pwd: str) -> None:
        body = urllib.parse.urlencode({"usr": usr, "pwd": pwd}).encode("utf-8")
        st, _, raw = self.request(
            "POST",
            "/api/method/login",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        if st != 200:
            raise RuntimeError(f"Login failed for {usr} (HTTP {st}): {raw[:200]!r}")
        st_html, _, html_bytes = self.request("GET", "/cskh")
        if st_html == 200:
            html_txt = html_bytes.decode("utf-8", errors="replace")
            m = re.search(
                r'(?:X-Frappe-CSRF-Token|csrf_token)["\']?\s*[:=]\s*["\']([0-9a-fA-F\-]{16,})["\']',
                html_txt,
            )
            if m:
                self.csrf_token = m.group(1)

    def get_json(self, path: str) -> tuple[int, dict[str, object]]:
        st, _, raw = self.request("GET", path, headers={"Accept": "application/json"})
        return st, json.loads(raw.decode("utf-8", errors="replace"))

    def post_json(
        self, path: str, payload: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.csrf_token:
            hdrs["X-Frappe-CSRF-Token"] = self.csrf_token
        st, _, raw = self.request(
            "POST",
            path,
            data=json.dumps(payload).encode("utf-8"),
            headers=hdrs,
        )
        return st, json.loads(raw.decode("utf-8", errors="replace"))


def wait_for_ping(port: int, site_name: str, timeout_sec: int = 90) -> None:
    sess = HttpActorSession(f"http://127.0.0.1:{port}", site_name)
    deadline = time.monotonic() + timeout_sec
    last_err = "not ready"
    while time.monotonic() < deadline:
        try:
            st, _, raw = sess.request("GET", "/api/method/ping", timeout=5)
            if st == 200 and b"pong" in raw:
                return
            last_err = f"HTTP {st}: {raw[:120]!r}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(2)
    raise RuntimeError(
        f"Timed out waiting for /api/method/ping on {site_name}:{port}: {last_err}"
    )


def parse_junit_counts(xml_path: Path) -> dict[str, int]:
    root_el = ET.parse(xml_path).getroot()
    suites = (
        [root_el]
        if root_el.tag == "testsuite"
        else list(root_el.findall(".//testsuite"))
    )
    tests = sum(int(s.attrib.get("tests", 0)) for s in suites)
    failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
    errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
    skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
    return {"tests": tests, "failures": failures, "errors": errors, "skipped": skipped}


def run_phase_a_migrate_and_suites(
    *,
    app_image: str,
    lock: dict[str, object],
    out_dir: Path,
    port: int,
) -> dict[str, object]:
    """Boots clean Frappe stack #1, proves double-migrate & Deal action retirement, runs JUnit suites."""
    project = f"cskh-wc-suites-{secrets.token_hex(4)}"
    site_name = "worker-care-suites.localhost"
    db_pw = secrets.token_hex(24)
    adm_pw = f"WcSuites#{secrets.token_hex(12)}"
    junit_dir = out_dir / "junit"
    junit_ui_dir = out_dir / "junit-ui"
    junit_dir.mkdir(parents=True, exist_ok=True)
    junit_ui_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cskh-wc-suites-") as td:
        sec_file = Path(td) / "db_pw.txt"
        sec_file.write_text(db_pw + "\n", encoding="utf-8")
        compose_env = {
            "COMPOSE_PROJECT_NAME": project,
            "APP_IMAGE": app_image,
            "PULL_POLICY": "never",
            "SITE_NAME": site_name,
            "VERIFY_HTTP_PORT": str(port),
            "DB_PASSWORD_SECRETS_FILE": str(sec_file),
            "MARIADB_IMAGE": str(lock["infra_images"]["mariadb"]),  # type: ignore[index]
            "REDIS_IMAGE": str(lock["infra_images"]["redis"]),  # type: ignore[index]
        }
        compose_base = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", project]
        try:
            run_cmd(
                [
                    *compose_base,
                    "up",
                    "-d",
                    "--no-build",
                    "db",
                    "redis-cache",
                    "redis-queue",
                    "configurator",
                    "backend",
                ],
                env=compose_env,
            )

            init_cmd = (
                "set -euo pipefail; cd /home/frappe/frappe-bench; "
                "bench new-site --no-mariadb-socket --mariadb-user-host-login-scope=% "
                '--db-root-password "$DB_PW" --admin-password "$ADM_PW" '
                '--install-app crm --install-app crm_cskh "$SITE"; '
                'bench --site "$SITE" set-config allow_tests true; '
                "mkdir -p /tmp/wc-junit /tmp/wc-junit-ui"
            )
            run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"DB_PW={db_pw}",
                    "-e",
                    f"ADM_PW={adm_pw}",
                    "-e",
                    f"SITE={site_name}",
                    "backend",
                    "bash",
                    "-lc",
                    init_cmd,
                ],
                env=compose_env,
                timeout=900,
            )

            # Pre-seed legacy enabled=1 state + Redis sentinel, then run `bench migrate` TWICE
            pre_mig_py = (
                "import json, os, frappe; "
                "os.chdir('/home/frappe/frappe-bench/sites'); "
                f"frappe.init(site={site_name!r}, sites_path='.'); frappe.connect(); "
                "fresh_enabled = int(frappe.db.get_value('CRM Form Script', 'CSKH - Open CSKH Page from Deal', 'enabled') or 0); "
                "frappe.db.set_value('CRM Form Script', 'CSKH - Open CSKH Page from Deal', 'enabled', 1); "
                "frappe.db.set_single_value('System Settings', 'default_app', 'crm'); "
                "frappe.cache.set_value('wc_migrate_cache_probe', 'stale-before-migrate'); "
                "frappe.db.commit(); frappe.destroy(); "
                "print(json.dumps({'fresh_install_deal_script_enabled': fresh_enabled}))"
            )
            pre_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    pre_mig_py,
                ],
                env=compose_env,
            )
            pre_info = json.loads(pre_res.stdout.strip().splitlines()[-1])

            # Execute patch + migrate #1 and migrate #2 (`patches.txt` marks patch executed on install,
            # so test both fresh-install fixture state AND upgrade migration execution + double `bench migrate`).
            upg_py = (
                "import json, os, subprocess, frappe; "
                "from crm_cskh.patches.v4_0 import worker_care_workspace; "
                "os.chdir('/home/frappe/frappe-bench/sites'); "
                f"site = {site_name!r}; "
                "frappe.init(site=site, sites_path='.'); frappe.connect(); "
                "frappe.db.sql(\"DELETE FROM `tabPatch Log` WHERE patch = 'crm_cskh.patches.v4_0.worker_care_workspace'\"); "
                "frappe.db.commit(); frappe.destroy(); "
                "m1 = subprocess.run(['bench', '--site', site, 'migrate'], cwd='/home/frappe/frappe-bench', capture_output=True, text=True, check=True); "
                "frappe.init(site=site, sites_path='.'); frappe.connect(); "
                "after_m1_enabled = int(frappe.db.get_value('CRM Form Script', 'CSKH - Open CSKH Page from Deal', 'enabled') or 0); "
                "cache_after_m1 = frappe.cache.get_value('wc_migrate_cache_probe'); "
                "frappe.destroy(); "
                "m2 = subprocess.run(['bench', '--site', site, 'migrate'], cwd='/home/frappe/frappe-bench', capture_output=True, text=True, check=True); "
                "frappe.init(site=site, sites_path='.'); frappe.connect(); "
                "after_m2_enabled = int(frappe.db.get_value('CRM Form Script', 'CSKH - Open CSKH Page from Deal', 'enabled') or 0); "
                "default_app_after_m2 = str(frappe.db.get_single_value('System Settings', 'default_app') or ''); "
                "lead_list_enabled = int(frappe.db.get_value('CRM Form Script', 'CSKH - Open CSKH Page', 'enabled') or 0); "
                "lead_form_enabled = int(frappe.db.get_value('CRM Form Script', 'CSKH - Open Lead CSKH Workspace', 'enabled') or 0); "
                "patch_logged = bool(frappe.db.exists('Patch Log', {'patch': 'crm_cskh.patches.v4_0.worker_care_workspace'})); "
                "worker_care_workspace.execute(); "
                "frappe.db.commit(); frappe.destroy(); "
                "print(json.dumps({"
                "'migrate_1_rc': m1.returncode, "
                "'migrate_2_rc': m2.returncode, "
                "'after_migrate_1_deal_script_enabled': after_m1_enabled, "
                "'cache_probe_after_migrate_1': cache_after_m1, "
                "'after_migrate_2_deal_script_enabled': after_m2_enabled, "
                "'default_app_after_migrate_2': default_app_after_m2, "
                "'lead_list_script_enabled': lead_list_enabled, "
                "'lead_form_script_enabled': lead_form_enabled, "
                "'patch_logged': patch_logged"
                "}))"
            )
            upg_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    upg_py,
                ],
                env=compose_env,
                timeout=600,
            )
            migrate_info = {
                **pre_info,
                **json.loads(upg_res.stdout.strip().splitlines()[-1]),
            }
            assert migrate_info["fresh_install_deal_script_enabled"] == 0, migrate_info
            assert migrate_info["after_migrate_1_deal_script_enabled"] == 0, (
                migrate_info
            )
            assert migrate_info["after_migrate_2_deal_script_enabled"] == 0, (
                migrate_info
            )
            assert migrate_info["default_app_after_migrate_2"] == "crm_cskh", (
                migrate_info
            )
            assert migrate_info["cache_probe_after_migrate_1"] is None, migrate_info
            assert migrate_info["lead_list_script_enabled"] == 1, migrate_info
            assert migrate_info["lead_form_script_enabled"] == 1, migrate_info
            assert migrate_info["patch_logged"] is True, migrate_info

            suite_results: dict[str, dict[str, int]] = {}
            for mod, bucket in SUITE_MODULES:
                slug = mod.rsplit(".", 1)[-1]
                c_dir = "/tmp/wc-junit-ui" if bucket == "junit-ui" else "/tmp/wc-junit"
                run_cmd(
                    [
                        *compose_base,
                        "exec",
                        "-T",
                        "-e",
                        "CI=true",
                        "backend",
                        "bash",
                        "-lc",
                        (
                            f"cd /home/frappe/frappe-bench && "
                            f"bench --site {site_name} --verbose run-tests "
                            f"--module '{mod}' --junit-xml-output '{c_dir}/{slug}.junit.xml'"
                        ),
                    ],
                    env=compose_env,
                    timeout=600,
                )

            cid = run_cmd(
                [*compose_base, "ps", "-q", "backend"], env=compose_env
            ).stdout.strip()
            run_cmd(
                ["docker", "cp", f"{cid}:/tmp/wc-junit/.", str(junit_dir)],
                env=compose_env,
            )
            run_cmd(
                ["docker", "cp", f"{cid}:/tmp/wc-junit-ui/.", str(junit_ui_dir)],
                env=compose_env,
            )

            for mod, bucket in SUITE_MODULES:
                slug = mod.rsplit(".", 1)[-1]
                xml_file = (
                    junit_ui_dir if bucket == "junit-ui" else junit_dir
                ) / f"{slug}.junit.xml"
                counts = parse_junit_counts(xml_file)
                if (
                    counts["tests"] <= 0
                    or counts["failures"] > 0
                    or counts["errors"] > 0
                    or counts["skipped"] > 0
                ):
                    raise AssertionError(
                        f"Frappe suite {mod} failed or skipped tests: {counts}"
                    )
                suite_results[mod] = counts

            # Also gate p3_ui against canonical inventory
            run_cmd(
                [
                    sys.executable,
                    str(REPO_ROOT / "deploy" / "dev" / "seed" / "ci_junit_gate.py"),
                    "--inventory",
                    str(REPO_ROOT / "deploy" / "p3" / "ui_test_inventory.json"),
                    "--xml-dir",
                    str(junit_ui_dir),
                    "--out",
                    str(out_dir / "p3-ui-junit-gate.json"),
                ]
            )
            return {
                "status": "PASS",
                "site_name": site_name,
                "double_migrate_and_deal_script_audit": migrate_info,
                "frappe_test_suites": suite_results,
            }
        finally:
            subprocess.run(
                [*compose_base, "down", "-v", "--remove-orphans"],
                env={**os.environ, **compose_env},
                capture_output=True,
                check=False,
                timeout=180,
            )


SEED_SYNTHETIC_E2E_PY = r"""
import json, os, sys
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
import frappe
from frappe.utils import add_to_date, now_datetime
from crm_cskh.patches.v2_0 import p2_schema_and_seeds
from crm_cskh.patches.v3_0 import p3_schema_and_seeds
from crm_cskh.services import identity as identity_service
from crm_cskh.services import ownership
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.api import workflow as wf_api

cfg = json.loads(sys.stdin.read())
site = cfg["site_name"]
actor_pw = cfg["actor_pw"]

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
frappe.set_user("Administrator")
try:
    frappe.db.set_single_value("System Settings", "language", "vi")
    frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
    frappe.db.set_single_value("System Settings", "country", "Vietnam")
    frappe.db.set_single_value("System Settings", "setup_complete", 1)
    frappe.db.set_value("User", "Administrator", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"})
    frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)

    p2_schema_and_seeds.execute()
    p3_schema_and_seeds.execute()

    for rname in ("CSKH BOD Viewer", "CSKH CRM Admin", "Sales Manager", "Sales User", "System Manager"):
        if not frappe.db.exists("Role", rname):
            frappe.get_doc({"doctype": "Role", "role_name": rname, "desk_access": 1}).insert(ignore_permissions=True)

    actors = [
        ("staff.a@mock.local", "[DỮ LIỆU GIẢ LẬP] Nhân viên Marketing A", ["Sales User"]),
        ("staff.b@mock.local", "[DỮ LIỆU GIẢ LẬP] Nhân viên Marketing B", ["Sales User"]),
        ("staff.c@mock.local", "[DỮ LIỆU GIẢ LẬP] Nhân viên Marketing C", ["Sales User"]),
        ("leader.a@mock.local", "[DỮ LIỆU GIẢ LẬP] Trưởng nhóm Marketing A", ["Sales Manager", "Sales User"]),
        ("leader.b@mock.local", "[DỮ LIỆU GIẢ LẬP] Trưởng nhóm Marketing B", ["Sales Manager", "Sales User"]),
        ("crm.admin@mock.local", "[DỮ LIỆU GIẢ LẬP] Quản trị Chăm sóc NLĐ", ["CSKH CRM Admin"]),
        (
            "crm.sysadmin@mock.local",
            "[DỮ LIỆU GIẢ LẬP] Quản trị Hệ thống CSKH",
            ["CSKH CRM Admin", "Sales Manager", "Sales User", "System Manager"],
        ),
        ("bod.viewer@mock.local", "[DỮ LIỆU GIẢ LẬP] Ban Giám đốc (0 PII)", ["CSKH BOD Viewer"]),
    ]
    for email, fname, uroles in actors:
        if not frappe.db.exists("User", email):
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": fname,
                "enabled": 1,
                "language": "vi",
                "time_zone": "Asia/Ho_Chi_Minh",
                "send_welcome_email": 0,
                "user_type": "System User",
            }).insert(ignore_permissions=True)
        u = frappe.get_doc("User", email)
        u.enabled = 1
        u.language = "vi"
        u.time_zone = "Asia/Ho_Chi_Minh"
        u.new_password = actor_pw
        u.roles = []
        for rn in uroles:
            u.append("roles", {"role": rn})
        u.save(ignore_permissions=True)

    # Hierarchy: Team A (leader.a -> staff.a, staff.b), Team B (leader.b -> staff.c)
    nodes = {}
    for label, uemail, is_grp, parent_label in (
        ("Team A", "leader.a@mock.local", 1, None),
        ("Staff A", "staff.a@mock.local", 0, "Team A"),
        ("Staff B", "staff.b@mock.local", 0, "Team A"),
        ("Team B", "leader.b@mock.local", 1, None),
        ("Staff C", "staff.c@mock.local", 0, "Team B"),
    ):
        existing = frappe.db.get_value("CRM Sales Hierarchy", {"user": uemail}, "name")
        if existing:
            nodes[label] = existing
        else:
            h_payload = {
                "doctype": "CRM Sales Hierarchy",
                "is_group": is_grp,
                "user": uemail,
                "reports_to": nodes.get(parent_label) if parent_label else None,
            }
            if frappe.db.has_column("CRM Sales Hierarchy", "full_name"):
                h_payload["full_name"] = label
            d = frappe.get_doc(h_payload).insert(ignore_permissions=True)
            nodes[label] = d.name

    frappe.db.sql(
        "UPDATE `tabCSKH Team Membership History` SET effective_from = '2026-01-01 00:00:00' WHERE effective_to IS NULL"
    )

    camp_a1 = frappe.get_doc({
        "doctype": "CSKH Campaign",
        "code": "CAMP-MOCK-A1",
        "title": "[DỮ LIỆU GIẢ LẬP] Chiến dịch Tuyển NLĐ Bắc Ninh",
        "status": "Active",
        "team_scope": nodes["Team A"],
    }).insert(ignore_permissions=True).name

    camp_a2 = frappe.get_doc({
        "doctype": "CSKH Campaign",
        "code": "CAMP-MOCK-A2",
        "title": "[DỮ LIỆU GIẢ LẬP] Chiến dịch Chăm sóc NLĐ Điện tử",
        "status": "Active",
        "team_scope": nodes["Team A"],
    }).insert(ignore_permissions=True).name

    camp_b1 = frappe.get_doc({
        "doctype": "CSKH Campaign",
        "code": "CAMP-MOCK-B1",
        "title": "[DỮ LIỆU GIẢ LẬP] Chiến dịch Đội B Hải Phòng",
        "status": "Active",
        "team_scope": nodes["Team B"],
    }).insert(ignore_permissions=True).name

    for camp_id, user_email, team_id in (
        (camp_a1, "staff.a@mock.local", nodes["Team A"]),
        (camp_a1, "staff.b@mock.local", nodes["Team A"]),
        (camp_a2, "staff.a@mock.local", nodes["Team A"]),
        (camp_b1, "staff.c@mock.local", nodes["Team B"]),
    ):
        frappe.get_doc({
            "doctype": "CSKH Assignment Rule",
            "campaign": camp_id,
            "user": user_email,
            "weight": 50,
            "capacity_limit": 0,
            "version": 1,
            "enabled": 1,
            "team": team_id,
        }).insert(ignore_permissions=True)

    seq = [1000]
    def make_worker_lead(name_label: str, owner_email: str) -> tuple[str, str]:
        seq[0] += 1
        phone = f"0917{seq[0]:06d}"
        norm = normalize_vn_phone(phone)
        lead_doc = frappe.get_doc({
            "doctype": "CRM Lead",
            "first_name": name_label,
            "lead_name": name_label,
            "mobile_no": phone,
            "status": "New",
            "lead_owner": owner_email,
        }).insert(ignore_permissions=True)
        identity_service.claim_canonical_identity(norm, lead_doc.name)
        return lead_doc.name, phone

    def assign_item(camp_id: str, lead_id: str, target_email: str, tag: str) -> str:
        wi = frappe.get_doc({
            "doctype": "CSKH Work Item",
            "campaign": camp_id,
            "lead": lead_id,
            "status": "queued",
        }).insert(ignore_permissions=True)
        if not frappe.db.exists("CSKH Active Claim", {"lead": lead_id}):
            frappe.get_doc({
                "doctype": "CSKH Active Claim",
                "lead": lead_id,
                "campaign": camp_id,
                "work_item": wi.name,
            }).insert(ignore_permissions=True)
        ownership.assign(
            wi.name,
            target_email,
            reason=f"[DỮ LIỆU GIẢ LẬP] Phân công {tag}",
            request_id=f"seed-{tag}-{wi.name}",
            actor="crm.admin@mock.local",
        )
        return wi.name

    # 55 distinct worker Leads for staff.a in camp_a1 + 6 shared Leads in camp_a2 => 61 Work Items / 55 distinct workers
    staff_a_leads = []
    staff_a_wis = []
    for idx in range(1, 56):
        lbl = (
            "[DỮ LIỆU GIẢ LẬP] Công nhân Đặc Biệt 01"
            if idx == 1
            else f"[DỮ LIỆU GIẢ LẬP] Người lao động Mẫu A{idx:02d}"
        )
        lid, ph = make_worker_lead(lbl, "staff.a@mock.local")
        wi_name = assign_item(camp_a1, lid, "staff.a@mock.local", f"a1-{idx}")
        staff_a_leads.append((lid, ph))
        staff_a_wis.append(wi_name)

    for idx in range(6):
        shared_lid = staff_a_leads[idx][0]
        wi_dup = frappe.get_doc({
            "doctype": "CSKH Work Item",
            "campaign": camp_a2,
            "lead": shared_lid,
            "status": "assigned",
            "assigned_to": "staff.a@mock.local",
            "assigned_at": now_datetime(),
        }).insert(ignore_permissions=True)
        staff_a_wis.append(wi_dup.name)

    # 8 worker Leads for staff.b in camp_a1
    staff_b_wis = []
    for idx in range(1, 9):
        lid, _ = make_worker_lead(f"[DỮ LIỆU GIẢ LẬP] Người lao động Mẫu B{idx:02d}", "staff.b@mock.local")
        staff_b_wis.append(assign_item(camp_a1, lid, "staff.b@mock.local", f"b1-{idx}"))

    # 5 worker Leads for staff.c in camp_b1
    for idx in range(1, 6):
        lid, _ = make_worker_lead(f"[DỮ LIỆU GIẢ LẬP] Người lao động Đội B C{idx:02d}", "staff.c@mock.local")
        assign_item(camp_b1, lid, "staff.c@mock.local", f"c1-{idx}")

    # Record real attempts & callbacks for staff.a
    ict = ZoneInfo("Asia/Ho_Chi_Minh")
    now_naive = now_datetime()
    now_local = now_naive.replace(tzinfo=ict) if now_naive.tzinfo is None else now_naive.astimezone(ict)
    end_of_today_naive = datetime.combine(now_local.date(), dtime(23, 55, 0))
    if end_of_today_naive <= now_naive:
        end_of_today_naive = add_to_date(now_naive, minutes=2)
    tomorrow_naive = datetime.combine(now_local.date() + timedelta(days=1), dtime(10, 0, 0))
    yesterday_naive = add_to_date(now_naive, hours=-26)

    frappe.set_user("staff.a@mock.local")
    # 4 connected attempts (without follow-up)
    for wi_conn in staff_a_wis[10:14]:
        wf_api.record_attempt(
            work_item=wi_conn,
            channel="phone_call",
            outcome_code="connected_interested",
            note="[DỮ LIỆU GIẢ LẬP] Người lao động quan tâm đơn hàng lắp ráp điện tử",
            idempotency_key=f"seed-conn-{wi_conn}",
        )

    # 3 attempted-unconnected with callbacks due today (2 future today + 1 earlier today/overdue)
    for idx, wi_td in enumerate(staff_a_wis[14:16]):
        wf_api.record_attempt(
            work_item=wi_td,
            channel="phone_call",
            outcome_code="no_answer",
            note=f"[DỮ LIỆU GIẢ LẬP] Chưa bắt máy - hẹn gọi lại trong ngày ({idx + 1})",
            followup_due_at=str(end_of_today_naive),
            followup_title="[DỮ LIỆU GIẢ LẬP] Lịch hẹn chăm sóc hôm nay",
            idempotency_key=f"seed-today-{wi_td}",
        )

    # 2 overdue callbacks (yesterday)
    frappe.set_user("Administrator")
    for idx, wi_od in enumerate(staff_a_wis[16:18]):
        frappe.set_user("staff.a@mock.local")
        res_od = wf_api.record_attempt(
            work_item=wi_od,
            channel="phone_call",
            outcome_code="no_answer",
            note=f"[DỮ LIỆU GIẢ LẬP] Hẹn gọi lại quá hạn ({idx + 1})",
            followup_due_at=str(tomorrow_naive),
            followup_title="[DỮ LIỆU GIẢ LẬP] Lịch hẹn quá hạn",
            idempotency_key=f"seed-od-{wi_od}",
        )
        frappe.set_user("Administrator")
        frappe.db.set_value("CRM Task", res_od["callback_task"], "due_date", str(yesterday_naive), update_modified=False)

    # 1 open callback tomorrow
    frappe.set_user("staff.a@mock.local")
    wf_api.record_attempt(
        work_item=staff_a_wis[18],
        channel="zalo",
        outcome_code="message_sent_no_reply",
        note="[DỮ LIỆU GIẢ LẬP] Đã nhắn Zalo thông tin ca làm việc",
        followup_due_at=str(tomorrow_naive),
        followup_title="[DỮ LIỆU GIẢ LẬP] Hẹn kiểm tra phản hồi ngày mai",
        idempotency_key=f"seed-tmr-{staff_a_wis[18]}",
    )

    # 1 Done callback & 1 Cancelled callback -> must be excluded from open/today/overdue
    res_done = wf_api.record_attempt(
        work_item=staff_a_wis[19],
        channel="phone_call",
        outcome_code="no_answer",
        note="[DỮ LIỆU GIẢ LẬP] Lịch hẹn đã hoàn tất",
        followup_due_at=str(end_of_today_naive),
        followup_title="[DỮ LIỆU GIẢ LẬP] Lịch đã hoàn thành",
        idempotency_key=f"seed-done-{staff_a_wis[19]}",
    )
    res_cancel = wf_api.record_attempt(
        work_item=staff_a_wis[20],
        channel="phone_call",
        outcome_code="no_answer",
        note="[DỮ LIỆU GIẢ LẬP] Lịch hẹn đã hủy",
        followup_due_at=str(end_of_today_naive),
        followup_title="[DỮ LIỆU GIẢ LẬP] Lịch đã hủy",
        idempotency_key=f"seed-cancel-{staff_a_wis[20]}",
    )
    frappe.set_user("Administrator")
    frappe.db.set_value("CRM Task", res_done["callback_task"], "status", "Done", update_modified=False)
    frappe.db.set_value("CRM Task", res_cancel["callback_task"], "status", "Canceled", update_modified=False)

    # Mark 2 worker Leads DNC (staff_a_leads[0] has 2 Work Items across camp_a1 + camp_a2!)
    for dnc_lead in (staff_a_leads[0][0], staff_a_leads[25][0]):
        frappe.db.set_value(
            "CRM Lead",
            dnc_lead,
            {"cskh_do_not_contact": 1, "cskh_dnc_reason": "[DỮ LIỆU GIẢ LẬP] Không muốn nhận cuộc gọi"},
            update_modified=False,
        )

    # Set converted=1 on 2 Leads + create 1 legacy CRM Deal (with matching keyword 'Đặc Biệt')
    if frappe.db.has_column("CRM Lead", "converted"):
        frappe.db.set_value("CRM Lead", staff_a_leads[10][0], "converted", 1, update_modified=False)
        frappe.db.set_value("CRM Lead", staff_a_leads[11][0], "converted", 1, update_modified=False)

    org_doc = frappe.get_doc({
        "doctype": "CRM Organization",
        "organization_name": "[DỮ LIỆU GIẢ LẬP] Nhà máy Điện tử Bắc Ninh",
        "annual_revenue": 120000000,
    }).insert(ignore_permissions=True)

    deal_org_doc = frappe.get_doc({
        "doctype": "CRM Organization",
        "organization_name": "[DỮ LIỆU GIẢ LẬP] Đối tác Giao dịch Đặc Biệt 01",
        "annual_revenue": 120000000,
    }).insert(ignore_permissions=True)

    contact_doc = frappe.get_doc({
        "doctype": "Contact",
        "first_name": "[DỮ LIỆU GIẢ LẬP] Liên hệ Nhân sự",
        "company_name": org_doc.name,
    }).insert(ignore_permissions=True)

    deal_status = "Qualification"
    if not frappe.db.exists("CRM Deal Status", deal_status):
        deal_status = (
            frappe.db.get_value(
                "CRM Deal Status", {"type": ["not in", ["Lost", "Won"]]}, "name"
            )
            or frappe.db.get_value(
                "CRM Deal Status", {"name": ["!=", "Lost"]}, "name"
            )
        )
    deal_payload = {
        "doctype": "CRM Deal",
        "deal_owner": "staff.a@mock.local",
        "annual_revenue": 120000000,
    }
    if deal_status:
        deal_payload["status"] = deal_status
    if frappe.db.has_column("CRM Deal", "organization"):
        deal_payload["organization"] = deal_org_doc.name
    if frappe.db.has_column("CRM Deal", "organization_name"):
        deal_payload["organization_name"] = deal_org_doc.organization_name
    if frappe.db.has_column("CRM Deal", "lead_name"):
        deal_payload["lead_name"] = "[DỮ LIỆU GIẢ LẬP] Giao dịch Đặc Biệt 01"
    legacy_deal = frappe.get_doc(deal_payload).insert(ignore_permissions=True)

    # Native Call Log on primary test Lead
    primary_lead_id, primary_phone = staff_a_leads[1]
    primary_wi_id = staff_a_wis[1]
    call_log_doc = frappe.get_doc({
        "doctype": "CRM Call Log",
        "id": f"MOCK-CL-{frappe.generate_hash(length=8)}",
        "type": "Outgoing",
        "status": "Completed",
        "from": "+842473008899",
        "to": primary_phone,
        "duration": 52,
        "start_time": add_to_date(now_naive, minutes=-15),
        "end_time": add_to_date(now_naive, minutes=-14),
        "caller": "staff.a@mock.local",
        "reference_doctype": "CRM Lead",
        "reference_docname": primary_lead_id,
    }).insert(ignore_permissions=True)

    # Saved/pinned CRM View Settings: 1 for CRM Deal (must be filtered out) + 1 for CRM Lead (must stay)
    pinned_lead_view = None
    pinned_deal_view = None
    if frappe.db.exists("DocType", "CRM View Settings"):
        try:
            lv = frappe.get_doc({
                "doctype": "CRM View Settings",
                "label": "[Giả lập] NLĐ Ưu tiên",
                "dt": "CRM Lead",
                "type": "list",
                "route_name": "Leads",
                "public": 1,
                "pinned": 1,
                "user": "staff.a@mock.local",
            }).insert(ignore_permissions=True)
            dv = frappe.get_doc({
                "doctype": "CRM View Settings",
                "label": "[Giả lập] Deal Doanh thu Cũ",
                "dt": "CRM Deal",
                "type": "list",
                "route_name": "Deals",
                "public": 1,
                "pinned": 1,
                "user": "staff.a@mock.local",
            }).insert(ignore_permissions=True)
            pinned_lead_view = lv.name
            pinned_deal_view = dv.name
        except Exception:
            pinned_lead_view = None
            pinned_deal_view = None

    frappe.db.commit()
    frappe.clear_cache()
    print(json.dumps({
        "special_search_lead_id": staff_a_leads[0][0],
        "primary_lead_id": primary_lead_id,
        "primary_phone": primary_phone,
        "primary_work_item_id": primary_wi_id,
        "reassign_work_item_id": staff_a_wis[2],
        "staff_b_sample_wi_id": staff_b_wis[0],
        "organization_id": org_doc.name,
        "contact_id": contact_doc.name,
        "legacy_deal_id": legacy_deal.name,
        "call_log_id": call_log_doc.name,
        "pinned_lead_view": pinned_lead_view,
        "pinned_deal_view": pinned_deal_view,
        "campaigns": [camp_a1, camp_a2, camp_b1],
    }, ensure_ascii=False))
finally:
    frappe.destroy()
"""


RAW_SQL_RECONCILE_PY = r"""
import json, os, sys
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
import frappe
from frappe.utils import get_datetime, now_datetime

req = json.loads(sys.stdin.read())
site = req["site_name"]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    ict = ZoneInfo("Asia/Ho_Chi_Minh")
    now_val = now_datetime()
    aware = get_datetime(now_val)
    if aware.tzinfo is None:
        aware = aware.replace(tzinfo=ict)
    day_start = datetime.combine(aware.date(), dtime.min)
    day_end = datetime.combine(aware.date() + timedelta(days=1), dtime.min)

    def query_expected(users: list[str] | None, is_all: bool = False) -> dict[str, object]:
        where = "wi.status in ('assigned', 'in_progress', 'on_hold')"
        params: tuple = ()
        if not is_all:
            placeholders = ", ".join(["%s"] * len(users))
            where += f" and wi.assigned_to in ({placeholders})"
            params = tuple(users)
        w = frappe.db.sql(
            f'''SELECT COUNT(*) AS active,
                       COUNT(DISTINCT wi.lead) AS contact_records,
                       SUM(CASE WHEN COALESCE(wi.attempt_count, 0) = 0 THEN 1 ELSE 0 END) AS unattempted,
                       SUM(CASE WHEN COALESCE(wi.attempt_count, 0) > 0 THEN 1 ELSE 0 END) AS attempted,
                       SUM(CASE WHEN COALESCE(wi.connected_attempt_count, 0) > 0 THEN 1 ELSE 0 END) AS connected,
                       SUM(CASE WHEN wi.status = 'on_hold' THEN 1 ELSE 0 END) AS on_hold,
                       COUNT(DISTINCT CASE WHEN COALESCE(wi.assigned_to, '') != '' THEN wi.assigned_to END) AS active_assignees,
                       COUNT(DISTINCT CASE WHEN COALESCE(wi.attempt_count, 0) = 0 AND COALESCE(wi.assigned_to, '') != '' THEN wi.assigned_to END) AS assignees_with_unattempted
                FROM `tabCSKH Work Item` wi
                WHERE {where}''',
            params,
            as_dict=True,
        )[0]
        cb = frappe.db.sql(
            f'''SELECT COUNT(*) AS open,
                       SUM(CASE WHEN t.due_date < %s THEN 1 ELSE 0 END) AS overdue,
                       SUM(CASE WHEN t.due_date >= %s AND t.due_date < %s THEN 1 ELSE 0 END) AS today,
                       COUNT(DISTINCT CASE WHEN t.due_date < %s AND COALESCE(wi.assigned_to, '') != '' THEN wi.assigned_to END) AS assignees_with_overdue
                FROM `tabCRM Task` t
                INNER JOIN `tabCSKH Work Item` wi
                  ON wi.name = t.cskh_work_item AND wi.open_callback_task = t.name
                WHERE COALESCE(t.cskh_is_followup, 0) = 1
                  AND t.status IN ('Backlog', 'Todo', 'In Progress')
                  AND {where}''',
            (now_val, day_start, day_end, now_val, *params),
            as_dict=True,
        )[0]
        if is_all:
            dnc_cnt = int(frappe.db.count("CRM Lead", {"cskh_do_not_contact": 1}))
        else:
            placeholders = ", ".join(["%s"] * len(users))
            dnc_cnt = int(
                frappe.db.sql(
                    f'''SELECT COUNT(DISTINCT wi.lead)
                        FROM `tabCSKH Work Item` wi
                        INNER JOIN `tabCRM Lead` l ON l.name = wi.lead
                        WHERE COALESCE(l.cskh_do_not_contact, 0) = 1
                          AND wi.assigned_to IN ({placeholders})''',
                    tuple(users),
                )[0][0]
                or 0
            )
        active = int(w["active"] or 0)
        attempted = int(w["attempted"] or 0)
        connected = int(w["connected"] or 0)
        return {
            "workload": {
                "active": active,
                "contact_records": int(w["contact_records"] or 0),
                "unattempted": int(w["unattempted"] or 0),
                "attempted": attempted,
                "connected": connected,
                "attempted_unconnected": max(attempted - connected, 0),
            },
            "callbacks": {
                "open": int(cb["open"] or 0),
                "overdue": int(cb["overdue"] or 0),
                "today": int(cb["today"] or 0),
            },
            "bottlenecks": {
                "on_hold": int(w["on_hold"] or 0),
                "active_assignees": int(w["active_assignees"] or 0),
                "assignees_with_unattempted": int(w["assignees_with_unattempted"] or 0),
                "assignees_with_overdue": int(cb["assignees_with_overdue"] or 0),
            },
            "do_not_contact": dnc_cnt,
        }

    print(json.dumps({
        "staff_a": query_expected(["staff.a@mock.local"]),
        "staff_b": query_expected(["staff.b@mock.local"]),
        "leader_a": query_expected(["leader.a@mock.local", "staff.a@mock.local", "staff.b@mock.local"]),
        "all": query_expected(None, is_all=True),
    }))
finally:
    frappe.destroy()
"""


WATERMARK_JS = """
(label) => {
  let el = document.getElementById('cskh-synthetic-watermark');
  if (!el) {
    el = document.createElement('div');
    el.id = 'cskh-synthetic-watermark';
    el.style.cssText = [
      'position:fixed',
      'bottom:10px',
      'right:10px',
      'z-index:2147483647',
      'background:rgba(15,23,42,0.86)',
      'color:#f8fafc',
      'padding:5px 10px',
      'border-radius:6px',
      'font:600 11px/1.35 system-ui,sans-serif',
      'pointer-events:none',
      'box-shadow:0 2px 8px rgba(0,0,0,0.2)'
    ].join(';');
    document.body.appendChild(el);
  }
  el.textContent = label;
}
"""


def run_playwright_browser_e2e(
    *,
    port: int,
    site_name: str,
    actor_pw: str,
    seeded: dict[str, object],
    screenshots_dir: Path,
) -> dict[str, object]:
    """Executes real Playwright Chromium verification against the compiled Frappe + Vue bundle."""
    from playwright.sync_api import sync_playwright

    screenshots_dir.mkdir(parents=True, exist_ok=True)
    base_url = f"http://{site_name}:{port}"
    primary_lead_id = str(seeded["primary_lead_id"])
    primary_wi_id = str(seeded["primary_work_item_id"])
    org_id = str(seeded["organization_id"])
    contact_id = str(seeded["contact_id"])
    legacy_deal_id = str(seeded["legacy_deal_id"])
    primary_phone = str(seeded["primary_phone"])

    captured_shots: list[dict[str, str]] = []
    uncaught_page_errors: list[str] = []
    checks: dict[str, object] = {}

    def snap(page, filename: str, watermark: str) -> str:
        page.evaluate(WATERMARK_JS, watermark)
        dest = screenshots_dir / filename
        page.screenshot(path=str(dest), full_page=False)
        captured_shots.append(
            {"file": filename, "watermark": watermark, "url": page.url}
        )
        return filename

    def login_via_browser(context, email: str) -> tuple[object, str]:
        page = context.new_page()
        page.on(
            "pageerror",
            lambda exc: uncaught_page_errors.append(f"[{email} @ {page.url}] {exc}"),
        )
        page.goto(f"{base_url}/login", wait_until="networkidle", timeout=30000)
        page.locator("#login_email").wait_for(state="visible", timeout=20000)
        page.locator("#login_email").fill(email)
        page.locator("#login_password").fill(actor_pw)
        page.locator("button.btn-login").click()
        page.wait_for_url("**/cskh**", timeout=30000)
        return page, urllib.parse.urlsplit(page.url).path

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[f"--host-resolver-rules=MAP {site_name} 127.0.0.1"],
        )
        try:
            # =================================================================
            # 1. STAFF DESKTOP (1440x900): Login, Overview, Error/Stale, CRM Vue Bundle,
            #    Command Palette, Contact/Org Tabs, Lead Save, Call Log & Care Workspace
            # =================================================================
            staff_ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="vi-VN"
            )
            staff_page, staff_login_landing = login_via_browser(
                staff_ctx, "staff.a@mock.local"
            )
            staff_page.locator("#care-metrics .care-metric").first.wait_for(
                state="visible", timeout=20000
            )

            overview_text = staff_page.locator("#care-overview").inner_text()
            assert "₫" not in overview_text and "$" not in overview_text, overview_text
            for sales_word in ("Forecast", "Dự báo", "Revenue", "Doanh thu", "Deal"):
                assert sales_word not in overview_text, (
                    f"Unexpected {sales_word!r} in Staff overview"
                )
            assert staff_page.locator("#care-bottlenecks-card").is_hidden()
            snap(
                staff_page,
                "01-frappe-real-desktop-staff-overview.png",
                "DỮ LIỆU GIẢ LẬP — TÍCH HỢP FRAPPE THỰC (Nhân viên Marketing · Desktop 1440x900)",
            )

            # Stale snapshot check on refresh failure (retains previous numbers + data-stale=1)
            def _fail_overview(route):
                route.fulfill(
                    status=500,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "exc_type": "ServerError",
                            "message": "Simulated refresh outage",
                        }
                    ),
                )

            staff_page.route(
                "**/api/method/crm_cskh.api.worker_overview.get_overview*",
                _fail_overview,
            )
            staff_page.locator("#care-refresh").click()
            staff_page.wait_for_function(
                "() => document.getElementById('care-data')?.getAttribute('data-stale') === '1'",
                timeout=10000,
            )
            stale_verified = (
                staff_page.locator("#care-data").is_visible()
                and staff_page.locator("#care-data").get_attribute("data-stale") == "1"
                and "is-error"
                in (staff_page.locator("#care-status").get_attribute("class") or "")
            )
            assert stale_verified

            # Initial load failure check on fresh tab: must NEVER render fake 0 metric cards
            err_page = staff_ctx.new_page()
            err_page.route(
                "**/api/method/crm_cskh.api.worker_overview.get_overview*",
                _fail_overview,
            )
            err_page.goto(
                f"{base_url}/cskh", wait_until="domcontentloaded", timeout=20000
            )
            err_page.wait_for_function(
                "() => (document.getElementById('care-status')?.className || '').includes('is-error')",
                timeout=10000,
            )
            initial_error_honest = (
                err_page.locator("#care-data").is_hidden()
                and err_page.locator("#care-metrics .care-metric").count() == 0
            )
            assert initial_error_honest
            err_page.close()
            staff_page.unroute(
                "**/api/method/crm_cskh.api.worker_overview.get_overview*"
            )

            # Old sales URLs redirect to /cskh
            redirected_sales_urls: dict[str, str] = {}
            for old_path in (
                "/crm",
                "/crm/dashboard",
                "/crm/deals",
                f"/crm/deals/{legacy_deal_id}",
                "/crm/forecast",
                "/crm/revenue",
                "/crm/data-import/doctype/CRM Deal",
            ):
                staff_page.goto(
                    f"{base_url}{old_path}",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                staff_page.wait_for_url("**/cskh**", timeout=15000)
                redirected_sales_urls[old_path] = urllib.parse.urlsplit(
                    staff_page.url
                ).path
                assert redirected_sales_urls[old_path] == "/cskh", (
                    old_path,
                    staff_page.url,
                )

            # Real Vue Bundle on /crm/leads: sidebar, App Switcher API, pinned views, Refresh & Back/Forward
            staff_page.goto(
                f"{base_url}/crm/leads", wait_until="domcontentloaded", timeout=30000
            )
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            # Allow views.get_views & sidebar Vue reactivity to settle
            staff_page.wait_for_timeout(1200)

            sidebar_audit = staff_page.evaluate(
                """() => {
                  const item = document.querySelector('[data-cskh-sidebar-item="1"]');
                  const nav = item ? item.parentElement : null;
                  const firstIsCskh = !!(nav && nav.firstElementChild === item);
                  const visibleSalesLinks = Array.from(document.querySelectorAll('a[href]')).filter(a => {
                    if (a.offsetParent === null) return false;
                    const h = a.getAttribute('href') || '';
                    return /\\/crm\\/(dashboard|deals|forecast|forecasts|revenue)(\\/|\\?|$)/.test(h);
                  }).map(a => a.getAttribute('href'));
                  const bodyText = document.body.innerText || '';
                  return {
                    firstIsCskh,
                    cskhHref: item?.querySelector('a')?.getAttribute('href') || '',
                    cskhText: (item?.innerText || '').trim(),
                    visibleSalesLinks,
                    hasPinnedLeadView: bodyText.includes('[Giả lập] NLĐ Ưu tiên'),
                    hasPinnedDealView: bodyText.includes('[Giả lập] Deal Doanh thu Cũ'),
                  };
                }"""
            )
            assert sidebar_audit["firstIsCskh"] is True, sidebar_audit
            assert sidebar_audit["cskhHref"] == "/cskh", sidebar_audit
            assert sidebar_audit["visibleSalesLinks"] == [], sidebar_audit
            assert sidebar_audit["hasPinnedDealView"] is False, sidebar_audit

            apps_switcher = staff_page.evaluate(
                """async () => {
                  const r = await fetch('/api/method/frappe.apps.get_apps', { headers: { Accept: 'application/json' } });
                  const j = await r.json();
                  return (j.message || []).map(a => ({ name: a.name, title: a.title, route: a.route }));
                }"""
            )
            cskh_app_entry = next(
                (a for a in apps_switcher if a["name"] == "crm_cskh"), None
            )
            assert cskh_app_entry and cskh_app_entry["route"] == "/cskh", apps_switcher

            # Refresh & Back/Forward & SPA pushState guard
            staff_page.reload(wait_until="domcontentloaded", timeout=30000)
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            staff_page.go_back(wait_until="domcontentloaded", timeout=20000)
            assert urllib.parse.urlsplit(staff_page.url).path == "/cskh"
            staff_page.go_forward(wait_until="domcontentloaded", timeout=20000)
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            assert urllib.parse.urlsplit(staff_page.url).path == "/crm/leads"

            # SPA pushState to /crm/deals must redirect to /cskh
            staff_page.evaluate(
                "() => window.setTimeout(() => window.history.pushState({}, '', '/crm/deals'), 10)"
            )
            staff_page.wait_for_url("**/cskh**", timeout=15000)
            assert urllib.parse.urlsplit(staff_page.url).path == "/cskh"

            # Real Vue Lead Detail (/crm/leads/<id>): Convert to Deal absent, Command Palette Ctrl+K, Lead Save
            staff_page.goto(
                f"{base_url}/crm/leads/{primary_lead_id}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            staff_page.wait_for_timeout(1500)

            visible_convert_btns = staff_page.evaluate(
                """() => Array.from(document.querySelectorAll('button, a, [role="menuitem"]'))
                  .filter(el => el.offsetParent !== null)
                  .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim())
                  .filter(t => /convert to deal|chuyển thành giao dịch crm|create deal|tạo giao dịch crm/i.test(t))"""
            )
            assert visible_convert_btns == [], visible_convert_btns
            snap(
                staff_page,
                "02-frappe-real-desktop-crm-lead-detail.png",
                "DỮ LIỆU GIẢ LẬP — TÍCH HỢP VUE BUNDLE THỰC (Hồ sơ NLĐ /crm/leads · Không có Convert to Deal)",
            )

            # Command Palette (Ctrl+K) + ArrowDown/ArrowUp + Search Lead vs Deal
            staff_page.keyboard.press("Control+k")
            palette_input = staff_page.locator(".crm-command-palette input").first
            if not palette_input.is_visible():
                staff_page.evaluate(
                    "() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }))"
                )
            palette_input.wait_for(state="visible", timeout=10000)
            staff_page.wait_for_timeout(400)

            palette_initial = staff_page.evaluate(
                """() => {
                  const opts = Array.from(document.querySelectorAll('.crm-command-palette [role="option"]'))
                    .filter(o => !o.hasAttribute('hidden') && o.offsetParent !== null)
                    .map(o => (o.innerText || '').replace(/\\s+/g, ' ').trim());
                  return opts;
                }"""
            )
            for opt_txt in palette_initial:
                low = opt_txt.lower()
                assert (
                    "convert to deal" not in low and "chuyển thành giao dịch" not in low
                ), opt_txt
                assert low not in (
                    "dashboard",
                    "trang tổng quan",
                    "deals",
                    "giao dịch crm",
                    "create deal",
                ), opt_txt

            staff_page.keyboard.press("ArrowDown")
            staff_page.keyboard.press("ArrowDown")
            staff_page.keyboard.press("ArrowUp")
            palette_focus_preserved = staff_page.evaluate(
                "() => document.activeElement && document.activeElement.closest('.crm-command-palette') !== null"
            )
            assert palette_focus_preserved is True

            palette_input.fill("Đặc Biệt")
            staff_page.wait_for_function(
                "() => (document.querySelector('.crm-command-palette')?.innerText || '').includes('Công nhân Đặc Biệt 01')",
                timeout=15000,
            )
            staff_page.wait_for_timeout(300)
            palette_search_opts = staff_page.evaluate(
                """() => Array.from(document.querySelectorAll('.crm-command-palette [role="option"]'))
                  .filter(o => !o.hasAttribute('hidden') && o.offsetParent !== null)
                  .map(o => (o.innerText || '').replace(/\\s+/g, ' ').trim())"""
            )
            assert any("Công nhân Đặc Biệt 01" in t for t in palette_search_opts), (
                palette_search_opts
            )
            assert not any(
                legacy_deal_id in t
                or t.endswith("Deal")
                or t.endswith("CRM Deal")
                or t.endswith("Giao dịch CRM")
                for t in palette_search_opts
            ), palette_search_opts
            snap(
                staff_page,
                "03-frappe-real-desktop-command-palette.png",
                "DỮ LIỆU GIẢ LẬP — COMMAND PALETTE VUE THỰC (Đã lọc Deal & Convert to deal)",
            )
            staff_page.keyboard.press("Escape")
            staff_page.wait_for_timeout(300)

            # Verify valid Lead field Save from browser context (and UI Save button not blocked)
            lead_save_res = staff_page.evaluate(
                """async (leadName) => {
                  let csrf = window.csrf_token || (window.frappe && window.frappe.csrf_token) || '';
                  if (!csrf) {
                    const htmlRes = await fetch('/cskh', { headers: { Accept: 'text/html' } });
                    const htmlTxt = await htmlRes.text();
                    const m = htmlTxt.match(/(?:X-Frappe-CSRF-Token|csrf_token)["']?\\s*[:=]\\s*["']([0-9a-fA-F\\-]{16,})["']/);
                    if (m) csrf = m[1];
                  }
                  const r = await fetch('/api/method/frappe.client.set_value', {
                    method: 'POST',
                    headers: {
                      'Content-Type': 'application/json',
                      'Accept': 'application/json',
                      'X-Frappe-CSRF-Token': csrf,
                    },
                    body: JSON.stringify({
                      doctype: 'CRM Lead',
                      name: leadName,
                      fieldname: { job_title: '[DỮ LIỆU GIẢ LẬP] Công nhân lắp ráp ca ngày' },
                    }),
                  });
                  const j = await r.json();
                  return { status: r.status, job_title: j?.message?.job_title || '' };
                }""",
                primary_lead_id,
            )
            assert (
                lead_save_res["status"] == 200
                and "Công nhân lắp ráp ca ngày" in lead_save_res["job_title"]
            ), lead_save_res

            # Verify Contact (/crm/contacts/<id>) & Organization (/crm/organizations/<id>) Deals tabs
            staff_page.goto(
                f"{base_url}/crm/contacts/{urllib.parse.quote(contact_id)}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            staff_page.locator('[data-cskh-contact-care-panel="1"]').first.wait_for(
                state="visible", timeout=15000
            )
            contact_tabs_audit = staff_page.evaluate(
                """() => {
                  const visibleDealTabs = Array.from(document.querySelectorAll('[role="tab"]'))
                    .filter(t => !t.hasAttribute('hidden') && t.offsetParent !== null)
                    .map(t => (t.innerText || '').trim())
                    .filter(txt => /^(Deals|Giao dịch CRM)(\\s+\\d+)?$/i.test(txt));
                  const carePanel = document.querySelector('[data-cskh-contact-care-panel="1"]');
                  return {
                    visibleDealTabs,
                    carePanelVisible: !!(carePanel && carePanel.offsetParent !== null),
                  };
                }"""
            )
            assert contact_tabs_audit["visibleDealTabs"] == [], contact_tabs_audit
            assert contact_tabs_audit["carePanelVisible"] is True, contact_tabs_audit

            staff_page.goto(
                f"{base_url}/crm/organizations/{urllib.parse.quote(org_id)}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            staff_page.wait_for_timeout(1200)
            org_tabs_audit = staff_page.evaluate(
                """() => {
                  const visibleDealTabs = Array.from(document.querySelectorAll('[role="tab"]'))
                    .filter(t => !t.hasAttribute('hidden') && t.offsetParent !== null)
                    .map(t => (t.innerText || '').trim())
                    .filter(txt => /^(Deals|Giao dịch CRM)(\\s+\\d+)?$/i.test(txt));
                  const visibleTabs = Array.from(document.querySelectorAll('[role="tab"]'))
                    .filter(t => !t.hasAttribute('hidden') && t.offsetParent !== null)
                    .map(t => ({ text: (t.innerText || '').trim(), selected: t.getAttribute('aria-selected') }));
                  return { visibleDealTabs, visibleTabs };
                }"""
            )
            assert org_tabs_audit["visibleDealTabs"] == [], org_tabs_audit

            # Verify Call Logs helper route (/crm/call-logs) works cleanly
            staff_page.goto(
                f"{base_url}/crm/call-logs",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            staff_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            assert urllib.parse.urlsplit(staff_page.url).path == "/crm/call-logs"

            # Verify Care Workspace (/cskh/work-item): record contact outcome + schedule follow-up callback
            staff_page.goto(
                f"{base_url}/cskh/work-item?id={urllib.parse.quote(primary_wi_id)}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            staff_page.locator("#wi-btn-record").wait_for(
                state="visible", timeout=20000
            )
            staff_page.locator("#wi-btn-record").click()
            staff_page.locator("#cskh-att-outcome").wait_for(
                state="visible", timeout=10000
            )
            staff_page.locator("#cskh-att-outcome").select_option("no_answer")
            staff_page.locator("#cskh-att-note").fill(
                "[DỮ LIỆU GIẢ LẬP] Đã gọi điện cho người lao động — hẹn gọi lại xác nhận hồ sơ"
            )
            if not staff_page.locator("#cskh-att-has-followup").is_checked():
                staff_page.locator("#cskh-att-has-followup").check()
            staff_page.locator("#cskh-att-followup-title").fill(
                "[DỮ LIỆU GIẢ LẬP] Hẹn gọi lại người lao động ca chiều"
            )
            staff_page.locator("#cskh-att-submit").click()
            staff_page.wait_for_function(
                "() => (document.getElementById('wi-timeline-list')?.innerText || '').includes('Đã gọi điện cho người lao động')",
                timeout=20000,
            )
            snap(
                staff_page,
                "04-frappe-real-desktop-work-item-care-flow.png",
                "DỮ LIỆU GIẢ LẬP — HỒ SƠ CHĂM SÓC THỰC (Ghi kết quả liên hệ & đặt lịch hẹn lại)",
            )
            staff_ctx.close()

            # =================================================================
            # 2. STAFF MOBILE (390x844): Overview + Mobile Lead Detail (no Convert) + Mobile Sidebar
            # =================================================================
            mob_ctx = browser.new_context(
                viewport={"width": 390, "height": 844},
                is_mobile=True,
                has_touch=True,
                locale="vi-VN",
            )
            mob_page, mob_landing = login_via_browser(mob_ctx, "staff.a@mock.local")
            mob_page.locator("#care-metrics .care-metric").first.wait_for(
                state="visible", timeout=20000
            )
            mob_overflow = mob_page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
            )
            assert mob_overflow is True
            snap(
                mob_page,
                "05-frappe-real-mobile-staff-overview.png",
                "DỮ LIỆU GIẢ LẬP — MOBILE 390x844 (Tổng quan Chăm sóc NLĐ)",
            )

            mob_page.goto(
                f"{base_url}/crm/leads/{primary_lead_id}",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            mob_page.wait_for_timeout(2000)
            mob_convert_buttons = mob_page.evaluate(
                """() => Array.from(document.querySelectorAll('button, a'))
                  .filter(el => el.offsetParent !== null)
                  .map(el => (el.innerText || el.getAttribute('aria-label') || '').trim())
                  .filter(t => /^(Convert|Chuyển đổi|Convert to Deal|Chuyển thành Giao dịch CRM)$/i.test(t))"""
            )
            assert mob_convert_buttons == [], mob_convert_buttons
            snap(
                mob_page,
                "06-frappe-real-mobile-crm-lead-detail.png",
                "DỮ LIỆU GIẢ LẬP — MOBILE 390x844 (/crm/leads · Đã gỡ nút Convert)",
            )
            mob_ctx.close()

            # =================================================================
            # 3. LEADER DESKTOP (1440x900): Team Scope Overview & Bottlenecks
            # =================================================================
            leader_ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="vi-VN"
            )
            leader_page, leader_landing = login_via_browser(
                leader_ctx, "leader.a@mock.local"
            )
            leader_page.locator("#care-metrics .care-metric").first.wait_for(
                state="visible", timeout=20000
            )
            assert leader_page.locator("#care-bottlenecks-card").is_visible()
            snap(
                leader_page,
                "07-frappe-real-desktop-leader-overview.png",
                "DỮ LIỆU GIẢ LẬP — TRƯỞNG NHÓM MARKETING (Phạm vi Đội A & Điểm nghẽn)",
            )
            leader_ctx.close()

            # =================================================================
            # 4. CRM ADMIN + SYSADMIN DESKTOP (1440x900): Settings, Onboarding & Data Import
            # =================================================================
            admin_ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="vi-VN"
            )
            admin_page, admin_landing = login_via_browser(
                admin_ctx, "crm.admin@mock.local"
            )
            admin_page.locator("#care-metrics .care-metric").first.wait_for(
                state="visible", timeout=20000
            )
            admin_page.goto(
                f"{base_url}/crm/data-import/doctype/CRM Deal",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            admin_page.wait_for_url("**/cskh**", timeout=15000)
            assert urllib.parse.urlsplit(admin_page.url).path == "/cskh"
            admin_ctx.close()

            sys_ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="vi-VN"
            )
            sys_page, _ = login_via_browser(sys_ctx, "crm.sysadmin@mock.local")
            # /crm/data-import/doctype/CRM Deal redirects to /cskh while CRM Lead import stays 200
            sys_page.goto(
                f"{base_url}/crm/data-import/doctype/CRM Deal",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            sys_page.wait_for_url("**/cskh**", timeout=15000)
            assert urllib.parse.urlsplit(sys_page.url).path == "/cskh"

            sys_page.goto(
                f"{base_url}/crm/data-import/doctype/CRM Lead",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            sys_page.wait_for_timeout(1200)
            assert urllib.parse.urlsplit(sys_page.url).path != "/cskh", sys_page.url

            # Open Settings modal in /crm/leads via user dropdown menu -> Settings / Cài đặt
            sys_page.goto(
                f"{base_url}/crm/leads", wait_until="domcontentloaded", timeout=30000
            )
            sys_page.locator('[data-cskh-sidebar-item="1"]').first.wait_for(
                state="visible", timeout=30000
            )
            sys_page.wait_for_timeout(1000)
            brand_btn = sys_page.locator(
                'button:has(svg[viewBox="0 0 300 300"]), [data-slot="sidebar"] button'
            ).first
            settings_dashboard_hidden = True
            if brand_btn.count() > 0 and brand_btn.is_visible():
                brand_btn.click()
                sys_page.wait_for_timeout(400)
                settings_menu_btn = sys_page.locator(
                    '[role="menuitem"]:has-text("Settings"), [role="menuitem"]:has-text("Cài đặt"), button:has-text("Cài đặt")'
                ).first
                if settings_menu_btn.count() > 0 and settings_menu_btn.is_visible():
                    settings_menu_btn.click()
                    sys_page.wait_for_timeout(600)
                    settings_dashboard_hidden = sys_page.evaluate(
                        """() => {
                          const dashItems = Array.from(document.querySelectorAll('[data-page="Dashboard"], [data-page="Trang tổng quan"]'));
                          return dashItems.every(el => el.hasAttribute('hidden') || el.offsetParent === null);
                        }"""
                    )
            assert settings_dashboard_hidden is True
            sys_ctx.close()

            # =================================================================
            # 5. BOD DESKTOP (1440x900): Zero-PII Banner, Zero PII in DOM, Sales Redirects
            # =================================================================
            bod_ctx = browser.new_context(
                viewport={"width": 1440, "height": 900}, locale="vi-VN"
            )
            bod_page, bod_landing = login_via_browser(bod_ctx, "bod.viewer@mock.local")
            bod_page.locator("#care-metrics .care-metric").first.wait_for(
                state="visible", timeout=20000
            )
            assert bod_page.locator("#care-bod-banner").is_visible()
            bod_dom_text = bod_page.locator("body").inner_text()
            assert primary_phone not in bod_dom_text
            assert primary_lead_id not in bod_dom_text
            assert "Công nhân Đặc Biệt" not in bod_dom_text
            bod_hrefs = bod_page.evaluate(
                "() => Array.from(document.querySelectorAll('#care-overview a[href]')).map(a => a.getAttribute('href'))"
            )
            for href in bod_hrefs:
                assert not href.startswith("/cskh/callbacks"), href
                assert not href.startswith("/cskh/my-customers"), href
                assert not href.startswith("/crm"), href
            snap(
                bod_page,
                "08-frappe-real-desktop-bod-overview.png",
                "DỮ LIỆU GIẢ LẬP — BAN GIÁM ĐỐC (Tổng hợp Toàn hệ thống · 0 PII)",
            )
            bod_page.goto(
                f"{base_url}/crm/deals", wait_until="domcontentloaded", timeout=20000
            )
            bod_page.wait_for_url("**/cskh**", timeout=15000)
            assert urllib.parse.urlsplit(bod_page.url).path == "/cskh"
            bod_ctx.close()
        finally:
            browser.close()

    assert uncaught_page_errors == [], (
        f"Uncaught Vue/browser page errors: {uncaught_page_errors}"
    )

    checks.update(
        {
            "login_landings": {
                "staff": staff_login_landing,
                "mobile_staff": mob_landing,
                "leader": leader_landing,
                "crm_admin": admin_landing,
                "bod": bod_landing,
            },
            "redirected_sales_urls": redirected_sales_urls,
            "sidebar_audit": sidebar_audit,
            "apps_switcher": apps_switcher,
            "palette_search_options": palette_search_opts,
            "contact_tabs_audit": contact_tabs_audit,
            "organization_tabs_audit": org_tabs_audit,
            "lead_save_result": lead_save_res,
            "stale_snapshot_verified": stale_verified,
            "initial_error_zero_fake_metrics_verified": initial_error_honest,
            "settings_dashboard_hidden": settings_dashboard_hidden,
            "uncaught_page_errors": uncaught_page_errors,
            "screenshots": captured_shots,
        }
    )
    return {"status": "PASS", **checks}


def run_phase_b_and_c_reconcile_and_browser(
    *,
    app_image: str,
    lock: dict[str, object],
    out_dir: Path,
    port: int,
) -> dict[str, object]:
    """Boots clean Frappe stack #2, reconciles DB vs `get_overview`, and runs Playwright E2E."""
    project = f"cskh-wc-e2e-{secrets.token_hex(4)}"
    site_name = "worker-care-e2e.localhost"
    db_pw = secrets.token_hex(24)
    adm_pw = f"WcE2eAdm#{secrets.token_hex(12)}"
    actor_pw = f"WcActor#{secrets.token_hex(12)}"

    with tempfile.TemporaryDirectory(prefix="cskh-wc-e2e-") as td:
        sec_file = Path(td) / "db_pw.txt"
        sec_file.write_text(db_pw + "\n", encoding="utf-8")
        compose_env = {
            "COMPOSE_PROJECT_NAME": project,
            "APP_IMAGE": app_image,
            "PULL_POLICY": "never",
            "SITE_NAME": site_name,
            "VERIFY_HTTP_PORT": str(port),
            "DB_PASSWORD_SECRETS_FILE": str(sec_file),
            "MARIADB_IMAGE": str(lock["infra_images"]["mariadb"]),  # type: ignore[index]
            "REDIS_IMAGE": str(lock["infra_images"]["redis"]),  # type: ignore[index]
        }
        compose_base = ["docker", "compose", "-f", str(COMPOSE_FILE), "-p", project]
        try:
            run_cmd(
                [*compose_base, "up", "-d", "--no-build"], env=compose_env, timeout=300
            )
            run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    f"DB_PW={db_pw}",
                    "-e",
                    f"ADM_PW={adm_pw}",
                    "-e",
                    f"SITE={site_name}",
                    "backend",
                    "bash",
                    "-lc",
                    (
                        "set -euo pipefail; cd /home/frappe/frappe-bench; "
                        "bench new-site --no-mariadb-socket --mariadb-user-host-login-scope=% "
                        '--db-root-password "$DB_PW" --admin-password "$ADM_PW" '
                        '--install-app crm --install-app crm_cskh "$SITE"; '
                        'bench --site "$SITE" migrate; '
                        'bench --site "$SITE" migrate'
                    ),
                ],
                env=compose_env,
                timeout=900,
            )

            seed_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    SEED_SYNTHETIC_E2E_PY,
                ],
                env=compose_env,
                input_text=json.dumps({"site_name": site_name, "actor_pw": actor_pw}),
                timeout=600,
            )
            seeded = json.loads(seed_res.stdout.strip().splitlines()[-1])

            run_cmd(
                [
                    *compose_base,
                    "restart",
                    "backend",
                    "websocket",
                    "queue-short",
                    "scheduler",
                ],
                env=compose_env,
                timeout=120,
            )
            wait_for_ping(port, site_name, timeout_sec=90)

            # Query raw MariaDB expected metrics
            sql_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    RAW_SQL_RECONCILE_PY,
                ],
                env=compose_env,
                input_text=json.dumps({"site_name": site_name}),
            )
            sql_expected = json.loads(sql_res.stdout.strip().splitlines()[-1])

            base_http = f"http://127.0.0.1:{port}"
            sessions: dict[str, HttpActorSession] = {}
            api_overviews: dict[str, dict[str, object]] = {}
            for role_key, email in (
                ("staff_a", "staff.a@mock.local"),
                ("staff_b", "staff.b@mock.local"),
                ("leader_a", "leader.a@mock.local"),
                ("crm_admin", "crm.admin@mock.local"),
                ("bod", "bod.viewer@mock.local"),
            ):
                s = HttpActorSession(base_http, site_name)
                s.login(email, actor_pw)
                sessions[role_key] = s
                st_ov, body_ov = s.get_json(
                    "/api/method/crm_cskh.api.worker_overview.get_overview"
                )
                assert st_ov == 200, (role_key, st_ov, body_ov)
                api_overviews[role_key] = body_ov["message"]  # type: ignore[assignment]

            # Assert >50 full-dataset counts & distinct workers < Work Items & exact SQL match
            assert api_overviews["staff_a"]["workload"]["active"] == 61, api_overviews[
                "staff_a"
            ]
            assert api_overviews["staff_a"]["workload"]["contact_records"] == 55, (
                api_overviews["staff_a"]
            )
            assert api_overviews["leader_a"]["workload"]["active"] == 69, api_overviews[
                "leader_a"
            ]
            assert api_overviews["leader_a"]["workload"]["contact_records"] == 63, (
                api_overviews["leader_a"]
            )
            assert api_overviews["bod"]["workload"]["active"] == 74, api_overviews[
                "bod"
            ]
            assert api_overviews["bod"]["workload"]["contact_records"] == 68, (
                api_overviews["bod"]
            )

            for actor_key, sql_key, exp_scope in (
                ("staff_a", "staff_a", "self"),
                ("staff_b", "staff_b", "self"),
                ("leader_a", "leader_a", "team"),
                ("crm_admin", "all", "all"),
                ("bod", "all", "all"),
            ):
                got = api_overviews[actor_key]
                exp = sql_expected[sql_key]
                assert got["scope"] == exp_scope, (actor_key, got["scope"], exp_scope)
                for section in (
                    "workload",
                    "callbacks",
                    "bottlenecks",
                    "do_not_contact",
                ):
                    assert got[section] == exp[section], (
                        f"DB mismatch for {actor_key}.{section}: api={got[section]} != sql={exp[section]}"
                    )

            # BOD zero-PII & zero-sales/converted-as-hired check
            bod_raw = json.dumps(api_overviews["bod"], ensure_ascii=False)
            for secret_tok in (
                str(seeded["primary_lead_id"]),
                str(seeded["primary_phone"]),
                str(seeded["primary_work_item_id"]),
                "staff.a@mock.local",
                "CAMP-MOCK-A1",
                "Công nhân Đặc Biệt",
            ):
                assert secret_tok not in bod_raw, (
                    f"PII/ID leaked in BOD overview: {secret_tok}"
                )
            for forbidden_field in (
                "revenue",
                "deal_value",
                "forecast",
                "hired",
                "placed",
                "d7",
                "d30",
            ):
                assert forbidden_field not in bod_raw

            # Reassign 1 Work Item from staff.a -> staff.b via live HTTP & re-reconcile
            st_ra, body_ra = sessions["crm_admin"].post_json(
                "/api/method/crm_cskh.api.ownership.reassign",
                {
                    "work_item": seeded["reassign_work_item_id"],
                    "expected_owner": "staff.a@mock.local",
                    "target_user": "staff.b@mock.local",
                    "reason": "[DỮ LIỆU GIẢ LẬP] Điều phối tải nhóm A",
                    "request_id": f"e2e-reassign-{secrets.token_hex(4)}",
                },
            )
            assert st_ra == 200, (st_ra, body_ra)
            _, post_a_ov = sessions["staff_a"].get_json(
                "/api/method/crm_cskh.api.worker_overview.get_overview"
            )
            _, post_b_ov = sessions["staff_b"].get_json(
                "/api/method/crm_cskh.api.worker_overview.get_overview"
            )
            _, post_l_ov = sessions["leader_a"].get_json(
                "/api/method/crm_cskh.api.worker_overview.get_overview"
            )
            assert post_a_ov["message"]["workload"]["active"] == 60  # type: ignore[index]
            assert post_b_ov["message"]["workload"]["active"] == 9  # type: ignore[index]
            assert post_l_ov["message"]["workload"]["active"] == 69  # type: ignore[index]

            # Timezone midnight boundary check on staff.b (23:30 ICT vs 01:30 next day Asia/Tokyo)
            tz_probe_py = (
                "import json, os, frappe; "
                "from datetime import datetime; "
                "from unittest import mock; "
                "from frappe.utils import add_to_date, now_datetime; "
                "from crm_cskh.services import runtime_cache, worker_overview; "
                "from crm_cskh.api import workflow as wf_api; "
                f"site = {site_name!r}; wi = {seeded['staff_b_sample_wi_id']!r}; "
                "os.chdir('/home/frappe/frappe-bench/sites'); "
                "frappe.init(site=site, sites_path='.'); frappe.connect(); "
                "frappe.set_user('staff.b@mock.local'); "
                "att = wf_api.record_attempt(work_item=wi, channel='phone_call', outcome_code='no_answer', note='[DỮ LIỆU GIẢ LẬP] Kiểm tra biên ngày TZ', followup_due_at=str(add_to_date(now_datetime(), hours=2)), followup_title='[DỮ LIỆU GIẢ LẬP] Hẹn cuối ngày', idempotency_key='tz-boundary-b1'); "
                "frappe.set_user('Administrator'); "
                "frappe.db.set_value('CRM Task', att['callback_task'], 'due_date', '2026-11-26 23:30:00', update_modified=False); "
                "fixed_now = datetime(2026, 11, 26, 12, 0, 0); "
                "with mock.patch.object(worker_overview, 'now_datetime', return_value=fixed_now): "
                "    frappe.db.set_value('User', 'staff.b@mock.local', 'time_zone', 'Asia/Ho_Chi_Minh', update_modified=False); "
                "    runtime_cache.clear_all_local_caches(); "
                "    ict_today = worker_overview.get_overview('staff.b@mock.local')['callbacks']['today']; "
                "    frappe.db.set_value('User', 'staff.b@mock.local', 'time_zone', 'Asia/Tokyo', update_modified=False); "
                "    runtime_cache.clear_all_local_caches(); "
                "    tokyo_today = worker_overview.get_overview('staff.b@mock.local')['callbacks']['today']; "
                "frappe.db.set_value('User', 'staff.b@mock.local', 'time_zone', 'Asia/Ho_Chi_Minh', update_modified=False); "
                "frappe.db.set_value('CRM Task', att['callback_task'], 'status', 'Done', update_modified=False); "
                "runtime_cache.clear_all_local_caches(); frappe.db.commit(); frappe.destroy(); "
                "print(json.dumps({'ict_today': ict_today, 'tokyo_today': tokyo_today}))"
            )
            tz_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    tz_probe_py,
                ],
                env=compose_env,
            )
            tz_boundary = json.loads(tz_res.stdout.strip().splitlines()[-1])
            assert tz_boundary["ict_today"] - tz_boundary["tokyo_today"] == 1, (
                tz_boundary
            )

            reconciliation_report = {
                "status": "PASS",
                "synthetic_dataset": {
                    "is_synthetic": True,
                    "marker": "[DỮ LIỆU GIẢ LẬP]",
                    "staff_a_initial_work_items": 61,
                    "staff_a_distinct_workers": 55,
                    "leader_a_team_work_items": 69,
                    "company_total_work_items": 74,
                    "company_distinct_workers": 68,
                },
                "sql_vs_api_exact_match": {
                    "staff_a": True,
                    "staff_b": True,
                    "leader_a": True,
                    "crm_admin": True,
                    "bod_zero_pii": True,
                },
                "post_reassign_counts": {
                    "staff_a_active": post_a_ov["message"]["workload"]["active"],  # type: ignore[index]
                    "staff_b_active": post_b_ov["message"]["workload"]["active"],  # type: ignore[index]
                    "leader_a_active": post_l_ov["message"]["workload"]["active"],  # type: ignore[index]
                },
                "timezone_boundary_2330_ict_vs_tokyo": tz_boundary,
                "api_snapshots_before_reassign": api_overviews,
            }
            (out_dir / "db-overview-reconciliation.json").write_text(
                json.dumps(reconciliation_report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            # Phase C: Real Playwright Browser Verification against the same live stack
            browser_report = run_playwright_browser_e2e(
                port=port,
                site_name=site_name,
                actor_pw=actor_pw,
                seeded=seeded,
                screenshots_dir=out_dir / "screenshots",
            )

            # Confirm browser-created attempt & Lead field update persisted in MariaDB
            post_browser_py = (
                "import json, os, frappe; "
                f"site = {site_name!r}; wi = {seeded['primary_work_item_id']!r}; lead = {seeded['primary_lead_id']!r}; "
                "os.chdir('/home/frappe/frappe-bench/sites'); "
                "frappe.init(site=site, sites_path='.'); frappe.connect(); "
                "att_cnt = frappe.db.count('CSKH Contact Attempt', {'work_item': wi}); "
                "open_cb = frappe.db.get_value('CSKH Work Item', wi, 'open_callback_task'); "
                "job_title = frappe.db.get_value('CRM Lead', lead, 'job_title'); "
                "frappe.destroy(); "
                "print(json.dumps({'attempt_count': att_cnt, 'open_callback_task': open_cb, 'lead_job_title': job_title}, ensure_ascii=False))"
            )
            pb_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    post_browser_py,
                ],
                env=compose_env,
            )
            post_browser_db = json.loads(pb_res.stdout.strip().splitlines()[-1])
            assert post_browser_db["attempt_count"] >= 1, post_browser_db
            assert bool(post_browser_db["open_callback_task"]), post_browser_db
            assert "Công nhân lắp ráp ca ngày" in str(
                post_browser_db["lead_job_title"]
            ), post_browser_db
            browser_report["post_browser_db_persistence"] = post_browser_db

            (out_dir / "playwright-browser-report.json").write_text(
                json.dumps(browser_report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return {
                "reconciliation": reconciliation_report,
                "browser_e2e": browser_report,
            }
        finally:
            subprocess.run(
                [*compose_base, "down", "-v", "--remove-orphans"],
                env={**os.environ, **compose_env},
                capture_output=True,
                check=False,
                timeout=180,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-image", required=True, help="Pulled immutable APP_IMAGE (@sha256)"
    )
    parser.add_argument("--out-dir", required=True, help="Evidence output directory")
    parser.add_argument("--suites-port", type=int, default=18782)
    parser.add_argument("--e2e-port", type=int, default=18784)
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))

    started_utc = datetime.now(timezone.utc).isoformat()
    phase_a = run_phase_a_migrate_and_suites(
        app_image=args.app_image,
        lock=lock,
        out_dir=out_dir,
        port=args.suites_port,
    )
    phase_bc = run_phase_b_and_c_reconcile_and_browser(
        app_image=args.app_image,
        lock=lock,
        out_dir=out_dir,
        port=args.e2e_port,
    )

    summary = {
        "schema_version": "cskh-worker-care-integration-verify-v1",
        "started_at_utc": started_utc,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "app_image_tested": args.app_image,
        "phase_a_frappe_migrate_and_suites": phase_a,
        "phase_b_db_reconciliation": phase_bc["reconciliation"],
        "phase_c_real_vue_browser_e2e": phase_bc["browser_e2e"],
    }
    (out_dir / "worker-care-integration-report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    sums: list[str] = []
    for fpath in sorted(out_dir.rglob("*")):
        if not fpath.is_file() or fpath.name == "SHA256SUMS.txt":
            continue
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
        sums.append(f"{digest}  {fpath.relative_to(out_dir).as_posix()}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "PASS",
                "app_image": args.app_image,
                "screenshots_count": len(phase_bc["browser_e2e"]["screenshots"]),  # type: ignore[index]
                "out_dir": str(out_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
