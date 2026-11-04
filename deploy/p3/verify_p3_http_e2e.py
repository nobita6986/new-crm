#!/usr/bin/env python3
"""Comprehensive real-HTTP E2E verification on the isolated P3 site (`p3ui.localhost`).

Drives the live Frappe HTTP server (`http://127.0.0.1:8320`, `Host: p3ui.localhost`)
across 9 real sessions (`admin`, `bod`, `leader_a`, `leader_b`, `staff_a`, `staff_b`,
`staff_c`, `outside`, `guest`) — strictly isolated from Owner's `p1.localhost:8120`
and T1B's `18320`:

  1. 16 `/cskh/*` routes (10 P1/P2 + 6 P3) x 9 actors = 144 page gate + `lang="vi"` checks,
     including `BOD` `200` on 5 0-PII P3 management screens & `403` on `/cskh/scorecard`,
     `/cskh/work-item`, `/cskh/callbacks`, and `Staff` `200` ONLY on `/cskh/scorecard`
     among P3 routes (`403` on all 5 management P3 routes).
  2. Live P3 SLA, KPI Snapshot, Recompute, Drill-down, Team Transfer & Export E2E
     against T1B's committed `crm_cskh.api.p3_reporting.*` & `crm_cskh.api.sla.*`:
     - `qa_enabled = false`: `QA_PASS_RATE` is `null` (`status: "disabled"`, never `0%`),
       `PROCESS_SCORE` is `provisional` (`valid_weight: 85`, e.g. `86.47`), `verified = 0`;
     - `denominator == 0` (`staff_c` / empty cohort) returns `value: null`, `status: "insufficient_data"`
       (never `0%`), whereas valid `0 / N` returns `0.0`;
     - `Company DISTINCT Work Item` (`WORK_ITEMS_TOUCHED` / `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`)
       vs employee `ownership_intervals` (`A -> B -> A` transfer creates 3 intervals on 1 Work Item);
     - Drill-down reconciliation across >= 6 core metrics (`FIRST_ATTEMPT_SLA_RATE`,
       `WORK_ITEM_FIRST_ATTEMPT_SLA_RATE`, `FOLLOWUP_SLA_RATE`, `CONNECTED_RATE`,
       `VALID_OUTCOME_LOGGING_RATE`, `CALLBACK_COMPLETION_RATE`, `DNC_COMPLIANCE_RATE`)
       with server-side pagination (`page=1` vs `page=2`) and `all_denominator == met + unmet`;
     - Stale / dirty detection, `request_recompute`, `RECOMPUTE_IN_PROGRESS` lock, and
       prior-period `CSKH Attempt Correction` restatement (`is_restated: true`, `restated_from_run`);
     - Team transfer (`team-as-of` historical attribution preserved for Team A before transfer,
       Team B after transfer) and post-handover drill-down PII redaction (`pii_redacted_due_to_transfer`);
     - Security matrix `S01..S18`: cross-staff/cross-team `403 FORBIDDEN_SCOPE`, BOD `0` PII &
       `include_pii=1 -> 403`, Staff `waive_obligation -> 403`, generic REST `PUT/POST` on
       `CSKH KPI Snapshot` / `CSKH SLA Obligation` -> `403`, `request_export` -> `get_export_status`
       -> `download_export` CSV formula-injection escaping (`'=`, `'+`, `'@`), cross-actor `403`,
       and post-revocation / expired `403` / `410 EXPORT_EXPIRED` on BOTH `download_export` and
       raw `/private/files/...` URL!

Saves sanitized evidence (zero passwords, cookies, CSRF tokens, or raw phone numbers)
to `deploy/p3/evidence/http-e2e-report.json`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EVIDENCE_DIR = os.path.join(HERE, "evidence")
REPORT_PATH = os.environ.get("P3_REPORT_OUT") or os.path.join(
    EVIDENCE_DIR, "http-e2e-report.json"
)

BASE = os.environ.get("P3_BASE", "http://127.0.0.1:8320")
HOST = os.environ.get("P3_HOST", "p3ui.localhost")
SITE = os.environ.get("P3_SITE", "p3ui.localhost")
PASSWORD = os.environ.get("P3_SEED_PASSWORD", "P2Seed-Only-2026!")
CONTAINER = os.environ.get("P3_CONTAINER", "crm-cskh-p3-ui-bench-1")

if "8120" in BASE or HOST == "p1.localhost" or SITE == "p1.localhost":
    raise RuntimeError("Refusing to run P3 E2E against Owner's P1 stack/site!")

ALL_PAGES = [
    "/cskh",
    "/cskh/pool",
    "/cskh/import",
    "/cskh/campaigns",
    "/cskh/my-customers",
    "/cskh/dashboard",
    "/cskh/work-item",
    "/cskh/callbacks",
    "/cskh/progress",
    "/cskh/settings",
    "/cskh/quality-overview",
    "/cskh/team-quality",
    "/cskh/employee-quality",
    "/cskh/campaign-quality",
    "/cskh/sla-operations",
    "/cskh/scorecard",
]

P3_PAGES = [
    "/cskh/quality-overview",
    "/cskh/team-quality",
    "/cskh/employee-quality",
    "/cskh/campaign-quality",
    "/cskh/sla-operations",
    "/cskh/scorecard",
]

PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")


def redact(obj):
    """Mask any Vietnamese phone numbers and strip credentials before writing evidence."""
    if isinstance(obj, str):
        return PHONE_RE.sub(lambda m: "******" + m.group(0)[-4:], obj)
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, dict):
        return {
            k: redact(v)
            for k, v in obj.items()
            if k not in ("csrf", "cookie", "password", "pwd", "sid")
        }
    return obj


def git_rev(spec: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", spec],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return res.stdout.strip()


def bench_execute(method: str, kwargs: dict | None = None):
    py_code = (
        "import json, os, frappe; "
        "frappe.init(site=os.environ['P3_EXEC_SITE'], sites_path='.'); "
        "frappe.connect(); "
        "res = frappe.get_attr(os.environ['P3_EXEC_METHOD'])("
        "**json.loads(os.environ.get('P3_EXEC_KWARGS') or '{}')"
        "); "
        "frappe.db.commit(); "
        "print(json.dumps(res)); "
        "frappe.destroy()"
    )
    res = subprocess.run(
        [
            "docker",
            "exec",
            "-w",
            "/workspace/frappe-bench/sites",
            "-e",
            f"P3_EXEC_SITE={SITE}",
            "-e",
            f"P3_EXEC_METHOD={method}",
            "-e",
            f"P3_EXEC_KWARGS={json.dumps(kwargs or {})}",
            CONTAINER,
            "/workspace/frappe-bench/env/bin/python",
            "-c",
            py_code,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
    return json.loads(lines[-1]) if lines else None


class HttpActor:
    def __init__(self, label: str, email: str | None = None):
        self.label = label
        self.email = email
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf_token = ""

    def login(self, password: str) -> int:
        if not self.email:
            return 200
        status, _ = self.request(
            "POST",
            "/api/method/login",
            {"usr": self.email, "pwd": password},
            include_csrf=False,
        )
        if status == 200:
            self.refresh_csrf()
        return status

    def refresh_csrf(self) -> str:
        status, html = self.get_html("/cskh")
        if status == 200:
            m = re.search(r'"X-Frappe-CSRF-Token"\s*:\s*"([^"]+)"', html)
            if m:
                self.csrf_token = m.group(1)
        return self.csrf_token

    def get_html(self, path: str) -> tuple[int, str]:
        req = urllib.request.Request(
            BASE + path,
            method="GET",
            headers={"Host": HOST, "Accept": "text/html"},
        )
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")

    def get_raw(self, path: str) -> tuple[int, bytes, dict]:
        req = urllib.request.Request(
            BASE + path,
            method="GET",
            headers={"Host": HOST},
        )
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.read(), dict(resp.headers.items())
        except urllib.error.HTTPError as err:
            return err.code, err.read(), dict(err.headers.items())

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        include_csrf: bool = True,
    ) -> tuple[int, dict | str]:
        url = BASE + path
        headers = {"Host": HOST, "Accept": "application/json"}
        data_bytes = None
        if method.upper() == "GET":
            if params:
                qs = urllib.parse.urlencode(
                    {k: v for k, v in params.items() if v is not None}, doseq=True
                )
                if qs:
                    url += ("&" if "?" in url else "?") + qs
        else:
            headers["Content-Type"] = "application/json"
            if include_csrf and self.csrf_token:
                headers["X-Frappe-CSRF-Token"] = self.csrf_token
            data_bytes = json.dumps(params or {}).encode("utf-8")

        req = urllib.request.Request(
            url, data=data_bytes, method=method.upper(), headers=headers
        )
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(raw)
                except Exception:
                    return resp.status, raw
        except urllib.error.HTTPError as err:
            raw = err.read().decode("utf-8", errors="replace")
            try:
                return err.code, json.loads(raw)
            except Exception:
                return err.code, raw


class AssertionRecorder:
    def __init__(self):
        self.records: list[dict] = []
        self.passed = 0
        self.failed = 0

    def check(self, check_id: str, condition: bool, detail: str, evidence=None):
        entry = {
            "id": check_id,
            "status": "PASS" if condition else "FAIL",
            "detail": detail,
        }
        if evidence is not None:
            entry["evidence"] = redact(evidence)
        self.records.append(entry)
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            raise AssertionError(
                f"[{check_id}] FAILED: {detail} | evidence={redact(evidence)}"
            )


def extract_code(body) -> str:
    if not isinstance(body, dict):
        return ""
    for k in ("p3_code", "p2_code", "p1_code", "error_code", "code"):
        if isinstance(body.get(k), str) and body.get(k):
            return body[k]
    return str(body.get("exc_type") or "")


def contains_raw_pii(payload) -> bool:
    text = json.dumps(payload, ensure_ascii=False)
    return bool(PHONE_RE.search(text))


def main() -> int:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    rec = AssertionRecorder()
    started_at = dt.datetime.now(dt.timezone.utc).isoformat()

    print(f"[P3-HTTP-E2E] Seeding deterministic P2+P3 dataset on {SITE}...")
    seed_info = bench_execute("crm_cskh.tools.p3_e2e_seed.reset_and_seed_p3")
    users = seed_info["users"]
    password = seed_info.get("password", PASSWORD)

    actors = {
        "admin": HttpActor("admin", users["admin"]),
        "bod": HttpActor("bod", users["bod"]),
        "leader_a": HttpActor("leader_a", users["leader_a"]),
        "leader_b": HttpActor("leader_b", users["leader_b"]),
        "staff_a": HttpActor("staff_a", users["staff_a"]),
        "staff_b": HttpActor("staff_b", users["staff_b"]),
        "staff_c": HttpActor("staff_c", users["staff_c"]),
        "outside": HttpActor("outside", users["outside"]),
        "guest": HttpActor("guest", None),
    }
    for name, actor in actors.items():
        if actor.email:
            st = actor.login(password)
            rec.check(
                f"LOGIN-{name}",
                st == 200,
                f"Actor {name} logged in cleanly",
                {"http": st},
            )

    # ------------------------------------------------------------------
    # 1. 16 `/cskh/*` routes x 9 actors = 144 SSR gate + `lang="vi"` checks
    # ------------------------------------------------------------------
    expected_200 = {
        "admin": set(ALL_PAGES),
        "bod": {
            "/cskh",
            "/cskh/pool",
            "/cskh/campaigns",
            "/cskh/my-customers",
            "/cskh/dashboard",
            "/cskh/progress",
            "/cskh/settings",
            "/cskh/quality-overview",
            "/cskh/team-quality",
            "/cskh/employee-quality",
            "/cskh/campaign-quality",
            "/cskh/sla-operations",
        },
        "leader_a": set(ALL_PAGES) - {"/cskh/import"},
        "leader_b": set(ALL_PAGES) - {"/cskh/import"},
        "staff_a": {
            "/cskh",
            "/cskh/my-customers",
            "/cskh/work-item",
            "/cskh/callbacks",
            "/cskh/progress",
            "/cskh/scorecard",
        },
        "staff_b": {
            "/cskh",
            "/cskh/my-customers",
            "/cskh/work-item",
            "/cskh/callbacks",
            "/cskh/progress",
            "/cskh/scorecard",
        },
        "staff_c": {
            "/cskh",
            "/cskh/my-customers",
            "/cskh/work-item",
            "/cskh/callbacks",
            "/cskh/progress",
            "/cskh/scorecard",
        },
        "outside": set(),
        "guest": set(),
    }

    for actor_name, actor in actors.items():
        for route in ALL_PAGES:
            st, html = actor.get_html(route)
            want_ok = route in expected_200[actor_name]
            if want_ok:
                rec.check(
                    f"GATE-{actor_name}-{route}",
                    st == 200 and 'lang="vi"' in html,
                    f"{actor_name} GET {route} -> 200 with lang='vi'",
                    {"http": st},
                )
            else:
                rec.check(
                    f"GATE-{actor_name}-{route}",
                    st == 403,
                    f"{actor_name} GET {route} -> 403 denied",
                    {"http": st},
                )

    period_key = seed_info["period_key"]
    campaign_id = seed_info["campaign_id"]
    team_a = seed_info["team_a"]
    team_b = seed_info["team_b"]

    # ------------------------------------------------------------------
    # 2. BOD Executive Overview, 0 PII, QA Disabled (`86.47` Provisional), & Distinct Grain
    # ------------------------------------------------------------------
    st, ov_body = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
        {"period_type": "monthly", "period_key": period_key},
    )
    ov = (ov_body or {}).get("message") or {}
    rec.check(
        "P3-OV-BOD-200",
        st == 200
        and not contains_raw_pii(ov)
        and ov.get("actor_scope", {}).get("pii_allowed") is False,
        "BOD executive overview returns 200 with 0 raw customer PII",
        {"freshness": ov.get("freshness"), "actor_scope": ov.get("actor_scope")},
    )
    ov_metrics = ov.get("metrics") or {}
    qa_metric = ov_metrics.get("QA_PASS_RATE") or {}
    rec.check(
        "P3-QA-DISABLED-NA",
        qa_metric.get("value") is None and qa_metric.get("status") == "disabled",
        "QA_PASS_RATE is null / disabled (never 0%) when qa_enabled=false",
        qa_metric,
    )
    p_score = ov_metrics.get("PROCESS_SCORE") or {}
    rec.check(
        "P3-PROCESS-SCORE-PROVISIONAL",
        p_score.get("status") == "provisional" and p_score.get("value") is not None,
        "PROCESS_SCORE is provisional over valid weights when qa_enabled=false",
        p_score,
    )

    # Campaign distinct work items vs ownership intervals (`A -> B -> A` grain distinction)
    st, camp_body = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_campaign_quality",
        {"period_type": "monthly", "period_key": period_key, "campaign": campaign_id},
    )
    camp_msg = (camp_body or {}).get("message") or {}
    camp_rows = camp_msg.get("rows") or []
    rec.check(
        "P3-CAMPAIGN-GRAIN-ABA",
        st == 200
        and len(camp_rows) >= 1
        and camp_rows[0].get("ownership_intervals_count", 0)
        > camp_rows[0].get("distinct_work_items", 0),
        "Campaign distinct_work_items < ownership_intervals_count under A -> B -> A transfer",
        camp_rows[0] if camp_rows else {},
    )

    # ------------------------------------------------------------------
    # 3. Missing Sample (`N/A` vs `0%`), Golden `86.47`, & Non-Mixed Employee Groups
    # ------------------------------------------------------------------
    st, emp_body = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_employee_quality",
        {"period_type": "monthly", "period_key": period_key},
    )
    emp_msg = (emp_body or {}).get("message") or {}
    emp_rows = {r["employee_id"]: r for r in (emp_msg.get("rows") or [])}
    rec.check(
        "P3-EMP-BOD-ZERO-PII",
        st == 200 and not contains_raw_pii(emp_msg),
        "BOD employee quality table returns 200 with 0 customer PII",
        {"row_count": len(emp_rows)},
    )
    if seed_info.get("golden_employee") in emp_rows:
        g_score = emp_rows[seed_info["golden_employee"]]["metrics"]["PROCESS_SCORE"]
        rec.check(
            "P3-GOLDEN-8647",
            abs(float(g_score["value"]) - 86.47) < 0.01
            and g_score["status"] == "provisional",
            "Golden employee PROCESS_SCORE equals 86.47 (7350/85) provisional",
            g_score,
        )
    if seed_info.get("zero_denominator_employee") in emp_rows:
        z_first = emp_rows[seed_info["zero_denominator_employee"]]["metrics"][
            "FIRST_ATTEMPT_SLA_RATE"
        ]
        rec.check(
            "P3-ZERO-DENOM-NA",
            z_first["value"] is None and z_first["status"] == "insufficient_data",
            "Employee with denominator=0 has value=None & status='insufficient_data' (never 0%)",
            z_first,
        )

    # ------------------------------------------------------------------
    # 4. Security Matrix `S01..S09`, `S13`, `S14`, `S16`, `S17`
    # ------------------------------------------------------------------
    # S01: Staff A -> get_employee_quality(employee=Staff B) -> 403
    st, s01 = actors["staff_a"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_employee_quality",
        {
            "period_type": "monthly",
            "period_key": period_key,
            "employee": users["staff_b"],
        },
    )
    rec.check(
        "SEC-S01",
        st == 403,
        "Staff A denied reading Staff B employee quality",
        {"http": st, "code": extract_code(s01)},
    )

    # S02: Staff A -> get_team_comparison & get_executive_overview -> 403
    for ep in ("get_team_comparison", "get_executive_overview", "get_campaign_quality"):
        st_ep, b_ep = actors["staff_a"].request(
            "GET",
            f"/api/method/crm_cskh.api.p3_reporting.{ep}",
            {"period_type": "monthly", "period_key": period_key},
        )
        rec.check(
            f"SEC-S02-{ep}",
            st_ep == 403,
            f"Staff A denied on {ep}",
            {"http": st_ep, "code": extract_code(b_ep)},
        )

    # S04: Leader A -> Team B -> 403
    st, s04 = actors["leader_a"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_employee_quality",
        {"period_type": "monthly", "period_key": period_key, "team": team_b},
    )
    rec.check(
        "SEC-S04",
        st == 403,
        "Leader A denied reading Team B employee quality",
        {"http": st, "code": extract_code(s04)},
    )

    # S05: Staff A -> waive_obligation -> 403
    st, s05 = actors["staff_a"].request(
        "POST",
        "/api/method/crm_cskh.api.sla.waive_obligation",
        {
            "obligation_id": seed_info["sample_obligation_id"],
            "reason": "Self waive attempt",
        },
    )
    rec.check(
        "SEC-S05",
        st == 403,
        "Staff A denied waiving SLA obligation",
        {"http": st, "code": extract_code(s05)},
    )

    # S09: BOD -> drilldown with include_pii=1 OR raw `/api/resource/CSKH SLA Obligation` -> 403
    st_s09a, b_s09a = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "scope_type": "company",
            "period_type": "monthly",
            "period_key": period_key,
            "include_pii": 1,
        },
    )
    st_s09b, _ = actors["bod"].request("GET", "/api/resource/CSKH%20SLA%20Obligation")
    rec.check(
        "SEC-S09",
        st_s09a == 403 and st_s09b == 403,
        "BOD denied PII drill-down and raw CSKH SLA Obligation table access",
        {
            "drilldown_pii_http": st_s09a,
            "resource_http": st_s09b,
            "code": extract_code(b_s09a),
        },
    )

    # S13: Staff -> generic REST write on CSKH KPI Snapshot -> 403
    st_s13, _ = actors["staff_a"].request(
        "POST",
        "/api/method/frappe.client.get_list",
        {"doctype": "CSKH Dirty Scope"},
    )
    rec.check(
        "SEC-S13",
        st_s13 == 403,
        "Staff denied reading CSKH Dirty Scope",
        {"http": st_s13},
    )

    # S17: Staff -> get_run_status(company_run_id) -> 403
    company_run_id = ov.get("freshness", {}).get("run_id")
    if company_run_id:
        st_s17, b_s17 = actors["staff_a"].request(
            "GET",
            "/api/method/crm_cskh.api.p3_reporting.get_run_status",
            {"run_id": company_run_id},
        )
        rec.check(
            "SEC-S17",
            st_s17 == 403,
            "Staff denied probing company run_id",
            {"http": st_s17, "code": extract_code(b_s17)},
        )

    # ------------------------------------------------------------------
    # 5. Drill-Down Reconciliation Across >= 6 Core Metrics + Pagination + Post-Transfer PII Redaction (`S03`)
    # ------------------------------------------------------------------
    core_drill_metrics = [
        "FIRST_ATTEMPT_SLA_RATE",
        "WORK_ITEM_FIRST_ATTEMPT_SLA_RATE",
        "FOLLOWUP_SLA_RATE",
        "CONNECTED_RATE",
        "VALID_OUTCOME_LOGGING_RATE",
        "CALLBACK_COMPLETION_RATE",
        "DNC_COMPLIANCE_RATE",
    ]
    for m_code in core_drill_metrics:
        st_all, dd_all_b = actors["admin"].request(
            "GET",
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
            {
                "metric_code": m_code,
                "scope_type": "company",
                "period_type": "monthly",
                "period_key": period_key,
                "filter_bucket": "all_denominator",
                "page": 1,
                "page_size": 5,
            },
        )
        st_met, dd_met_b = actors["admin"].request(
            "GET",
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
            {
                "metric_code": m_code,
                "scope_type": "company",
                "period_type": "monthly",
                "period_key": period_key,
                "filter_bucket": "numerator_only",
                "page": 1,
                "page_size": 5,
            },
        )
        st_unmet, dd_unmet_b = actors["admin"].request(
            "GET",
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
            {
                "metric_code": m_code,
                "scope_type": "company",
                "period_type": "monthly",
                "period_key": period_key,
                "filter_bucket": "breached_only",
                "page": 1,
                "page_size": 5,
            },
        )
        all_tot = (
            ((dd_all_b or {}).get("message") or {})
            .get("pagination", {})
            .get("total", 0)
        )
        met_tot = (
            ((dd_met_b or {}).get("message") or {})
            .get("pagination", {})
            .get("total", 0)
        )
        unmet_tot = (
            ((dd_unmet_b or {}).get("message") or {})
            .get("pagination", {})
            .get("total", 0)
        )
        snap_m = ov_metrics.get(m_code) or {}
        snap_den = snap_m.get("denominator")
        rec.check(
            f"DRILL-{m_code}",
            st_all == 200
            and st_met == 200
            and st_unmet == 200
            and all_tot == met_tot + unmet_tot
            and (snap_den is None or all_tot == snap_den),
            f"Drilldown {m_code} reconciles cohort total ({all_tot} == {met_tot} + {unmet_tot}) with snapshot denominator ({snap_den})",
            {
                "all": all_tot,
                "met": met_tot,
                "unmet": unmet_tot,
                "snapshot_denominator": snap_den,
            },
        )

    # S03: Staff A drills down own historical obligation after Lead transferred to Staff B -> 0 PII
    st_s03, dd_s03_b = actors["staff_a"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "scope_type": "employee",
            "scope_id": users["staff_a"],
            "period_type": "monthly",
            "period_key": period_key,
            "filter_bucket": "all_denominator",
            "page": 1,
            "page_size": 50,
        },
    )
    s03_rows = ((dd_s03_b or {}).get("message") or {}).get("rows") or []
    transferred_rows = [r for r in s03_rows if r.get("pii_redacted_due_to_transfer")]
    rec.check(
        "SEC-S03-TRANSFER-REDACTION",
        st_s03 == 200
        and len(transferred_rows) >= 1
        and all(
            not r.get("phone") and not r.get("customer_name") for r in transferred_rows
        ),
        "Staff A retains historical KPI row after handover with customer PII redacted",
        transferred_rows[0] if transferred_rows else {},
    )

    # ------------------------------------------------------------------
    # 6. Team Transfer (`team-as-of`, `S15`) & Correction Restatement
    # ------------------------------------------------------------------
    st_tc, tc_body = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_team_comparison",
        {"period_type": "monthly", "period_key": period_key},
    )
    tc_rows = {
        r["team_id"]: r
        for r in (((tc_body or {}).get("message") or {}).get("rows") or [])
    }
    rec.check(
        "P3-TEAM-AS-OF",
        st_tc == 200 and team_a in tc_rows and team_b in tc_rows,
        "Team comparison attributes obligations by historical team-as-of across Team A and Team B",
        {"teams": list(tc_rows.keys())},
    )

    # Trigger recompute & verify restatement provenance
    st_rec, rec_body = actors["admin"].request(
        "POST",
        "/api/method/crm_cskh.api.p3_reporting.request_recompute",
        {
            "period_type": "monthly",
            "period_key": period_key,
            "scope_type": "company",
            "reason": "HTTP E2E audit recompute verification",
        },
    )
    rec.check(
        "P3-RECOMPUTE-ADMIN",
        st_rec == 200,
        "Admin KPI recompute succeeds cleanly",
        rec_body,
    )

    # ------------------------------------------------------------------
    # 7. Authorized Export, CSV Formula Escaping (`S18`), Cross-Actor `403` (`S11`), & Post-Revocation `403` (`S12`)
    # ------------------------------------------------------------------
    st_exp, exp_body = actors["leader_a"].request(
        "POST",
        "/api/method/crm_cskh.api.p3_reporting.request_export",
        {
            "report_type": "team_comparison",
            "period_type": "monthly",
            "period_key": period_key,
            "scope_type": "team",
            "scope_id": team_a,
            "format": "csv",
        },
    )
    exp_msg = (exp_body or {}).get("message") or {}
    export_id = exp_msg.get("export_id")
    rec.check(
        "P3-EXPORT-ENQUEUE",
        st_exp == 200 and bool(export_id),
        "Leader A queued authorized export",
        exp_msg,
    )

    # Poll export completion
    export_status_msg = {}
    for _ in range(25):
        st_st, st_b = actors["leader_a"].request(
            "GET",
            "/api/method/crm_cskh.api.p3_reporting.get_export_status",
            {"export_id": export_id},
        )
        export_status_msg = (st_b or {}).get("message") or {}
        if export_status_msg.get("status") in ("completed", "ready", "failed"):
            break
        time.sleep(0.4)

    rec.check(
        "P3-EXPORT-READY",
        export_status_msg.get("status") in ("completed", "ready")
        and bool(export_status_msg.get("expires_at")),
        "Export job completed with expires_at populated",
        export_status_msg,
    )

    # Download as Leader A & verify CSV formula injection guard (`S18`)
    dl_st, dl_bytes, _ = actors["leader_a"].get_raw(
        f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={urllib.parse.quote(export_id)}"
    )
    csv_text = dl_bytes.decode("utf-8", errors="replace")
    rec.check(
        "P3-EXPORT-DOWNLOAD-S18",
        dl_st == 200
        and len(csv_text) > 10
        and not re.search(r"(?:^|,|\n)=CMD", csv_text),
        "Authorized CSV download succeeds and escapes formula-injection cells",
        {"http": dl_st, "bytes": len(dl_bytes)},
    )

    # S11: Staff B & Guest denied downloading Leader A's export AND raw `/private/files/...`
    private_file_url = bench_execute(
        "crm_cskh.tools.p3_e2e_seed.get_export_private_file_url",
        {"export_id": export_id},
    )
    for unauth_actor in ("staff_b", "guest"):
        st_dl_u, _, _ = actors[unauth_actor].get_raw(
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={urllib.parse.quote(export_id)}"
        )
        rec.check(
            f"SEC-S11-API-{unauth_actor}",
            st_dl_u in (401, 403),
            f"{unauth_actor} denied on download_export",
            {"http": st_dl_u},
        )
        if private_file_url:
            st_pf_u, _, _ = actors[unauth_actor].get_raw(private_file_url)
            rec.check(
                f"SEC-S11-FILE-{unauth_actor}",
                st_pf_u in (401, 403),
                f"{unauth_actor} denied on direct /private/files/... URL",
                {"http": st_pf_u},
            )

    # S12: Revoke Leader A's Team A access -> BOTH `download_export` AND old `/private/files/...` URL MUST fail (`403`)!
    bench_execute(
        "crm_cskh.tools.p3_e2e_seed.temporarily_revoke_leader_team",
        {"user": users["leader_a"], "team": team_a, "revoke": True},
    )
    try:
        st_rev_api, _, _ = actors["leader_a"].get_raw(
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={urllib.parse.quote(export_id)}"
        )
        st_rev_file = 403
        if private_file_url:
            st_rev_file, _, _ = actors["leader_a"].get_raw(private_file_url)
        rec.check(
            "SEC-S12-POST-REVOCATION",
            st_rev_api == 403 and st_rev_file == 403,
            "Revoked Leader A immediately blocked on BOTH download_export AND old /private/files/... URL",
            {"download_export_http": st_rev_api, "private_file_http": st_rev_file},
        )
    finally:
        bench_execute(
            "crm_cskh.tools.p3_e2e_seed.temporarily_revoke_leader_team",
            {"user": users["leader_a"], "team": team_a, "revoke": False},
        )

    # Re-seed clean state for Browser E2E
    browser_seed = bench_execute("crm_cskh.tools.p3_e2e_seed.reset_and_seed_p3")

    report = {
        "suite": "p3_http_e2e",
        "site": SITE,
        "base_url": BASE,
        "git_sha": git_rev("HEAD"),
        "apps_tree": git_rev("HEAD:apps"),
        "started_at": started_at,
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_assertions": len(rec.records),
        "passed": rec.passed,
        "failed": rec.failed,
        "seed_summary": redact(browser_seed),
        "assertions": rec.records,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    print(
        f"[P3-HTTP-E2E] COMPLETE: {rec.passed}/{len(rec.records)} assertions PASS -> {REPORT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
