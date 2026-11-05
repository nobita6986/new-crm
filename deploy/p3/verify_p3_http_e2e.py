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
     - `CONNECTED_RATE` grain = `ownership_interval` (`7 / 8 = 87.50%` on `staff_b` with `18` calls);
     - All `docs/root-plan/P3.md` §7 metrics present (`ELIGIBLE_COVERAGE`, `CONVERSION_RATE`,
       `OVERDUE_BACKLOG`, `OPEN_FOLLOWUPS_OVERDUE`, `SLA_BREACH_COUNT`, `SLA_WAIVER_COUNT`,
       `VERIFICATION_RATE`, etc.);
     - Canonical `P3.md` §7.5 Golden Vector (`90%` w=25 -> `22.50`, `80%` w=20 -> `16.00`,
       `100%` w=20 -> `20.00`, `75%` w=20 -> `15.00` => `73.50 / 85 = 86.47` provisional)
       with `0 <= raw_weighted_points <= weight` (`F-P3-01`, `F-P3-09`);
     - `qa_enabled = false`: `QA_PASS_RATE` is `null` (`status: "disabled"`, never `0%`),
       while system-verified `CSKH Contact Attempt` (`verified > 0`) is preserved (`F-P3-03`);
     - `denominator == 0` (`staff_c` / empty cohort) returns `value: null`, `status: "insufficient_data"`
       (never `0%`), whereas valid `0 / N` returns `0.0`;
     - `Company DISTINCT Work Item` vs employee `ownership_intervals` (`A -> B -> A` transfer);
     - Drill-down reconciliation across >= 6 core metrics with server-side pagination, `search`,
       and `sort_by`/`sort_dir`, plus `get_filter_options` and `lead_source` scope filtering (`F-P3-10`);
     - Historical `team-as-of` isolation (`F-P3-04`): `Leader B` blocked on transferred employee's
       `Team A` obligations & closed `Team A` private-campaign Lead, while `BOD` gets 0-PII
       `sla.list_obligations` (`200`) and `403` on `waive_obligation`;
     - Unified `CSKH Policy` feature-flag rollback (`F-P3-08`): turning P3 flags off blocks
       `/cskh/quality-overview` & P3 APIs (`403`) while `/cskh/my-customers` stays `200`;
     - Security matrix `S01..S18`: cross-staff/cross-team `403 FORBIDDEN_SCOPE`, BOD `0` PII &
       `include_pii=1 -> 403`, Staff `waive_obligation -> 403`, generic REST `403`,
       CSV formula-injection escaping (`S18`), and post-revocation `403` on BOTH
       `download_export` and raw `/private/files/...` URL!

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

BASE = os.environ.get("P3_BASE", "http://127.0.0.1:18420")
HOST = os.environ.get("P3_HOST", "p3-e2e.localhost")
SITE = os.environ.get("P3_SITE", "p3-e2e.localhost")
PASSWORD = os.environ.get("P3_SEED_PASSWORD", "P2Seed-Only-2026!")
CONTAINER = os.environ.get("P3_CONTAINER", "cskh-p3-t1a-e2e-bench-1")

if (
    any(p in BASE for p in ("8120", "8220", "8320", "18320"))
    or HOST in ("p1.localhost", "p2.localhost", "p3ui.localhost")
    or SITE in ("p1.localhost", "p2.localhost", "p3ui.localhost")
    or CONTAINER == "crm-cskh-p3-ui-bench-1"
):
    raise RuntimeError(
        "Refusing to run destructive P3 E2E against Owner stacks (8120/8220/8320/p3ui.localhost)! "
        "Use an isolated test site such as p3-e2e.localhost:18420."
    )

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


def pick_metric(metrics: dict, *keys: str) -> dict:
    for k in keys:
        if isinstance(metrics.get(k), dict):
            return metrics[k]
    return {}


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
    # 2. BOD Executive Overview, Required `P3.md` §7 Metrics, `F-P3-03` Verified Attempts, & Trend Series
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

    # F-P3-01: Required `P3.md` §7 metrics must be exposed
    required_metric_groups = [
        ("ELIGIBLE_COVERAGE", "ELIGIBLE_COVERAGE_RATE"),
        ("CONVERSION_RATE",),
        ("OVERDUE_BACKLOG", "OVERDUE_WORK_ITEM_BACKLOG"),
        ("OPEN_FOLLOWUPS_OVERDUE",),
        ("SLA_BREACH_COUNT", "BREACH_COUNT"),
        ("SLA_WAIVER_COUNT",),
        ("VERIFICATION_RATE",),
    ]
    missing_groups = [
        grp for grp in required_metric_groups if not pick_metric(ov_metrics, *grp)
    ]
    rec.check(
        "P3-F01-REQUIRED-METRICS",
        len(missing_groups) == 0,
        "Executive overview exposes all mandatory P3.md §7 metrics",
        {"missing": [list(g) for g in missing_groups]},
    )

    # F-P3-03: System-verified P2 attempts (`verified > 0`) preserved even when `qa_enabled=false`
    ver_metric = pick_metric(ov_metrics, "VERIFICATION_RATE")
    att_breakdown = ov.get("attempt_breakdown") or {}
    verified_cnt = int(
        att_breakdown.get("verified") or ver_metric.get("numerator") or 0
    )
    rec.check(
        "P3-F03-VERIFIED-ATTEMPTS-PRESERVED",
        verified_cnt >= 4 and (ver_metric.get("value") or 0) > 0,
        "System-verified CSKH Contact Attempt count (>0) preserved when qa_enabled=false",
        {"attempt_breakdown": att_breakdown, "verification_rate": ver_metric},
    )

    # F-P3-10: Trend series has real numeric points (not all N/A)
    trend_pts = ov.get("trend_series") or ov.get("trends") or []
    valid_trend_pts = [
        pt
        for pt in trend_pts
        if any(
            pt.get(k) is not None
            for k in (
                "first_attempt_sla_rate",
                "first_touch_sla_rate",
                "connected_rate",
                "process_score",
            )
        )
    ]
    rec.check(
        "P3-F10-TREND-SERIES-NON-NULL",
        len(valid_trend_pts) >= 1,
        "Executive overview trend_series contains real numeric KPI points",
        {"points": len(trend_pts), "sample": valid_trend_pts[:2]},
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
    # 3. Canonical `86.47` Golden Vector (`F-P3-01`, `F-P3-06`, `F-P3-09`) & Missing Sample (`N/A` vs `0%`)
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
    golden_emp = seed_info.get("golden_employee")
    if golden_emp in emp_rows:
        g_metrics = emp_rows[golden_emp]["metrics"]
        g_score = g_metrics["PROCESS_SCORE"]
        g_conn = g_metrics["CONNECTED_RATE"]
        g_att = g_metrics.get("CONTACT_ATTEMPT_COUNT", {})
        rec.check(
            "P3-GOLDEN-8647",
            abs(float(g_score["value"]) - 86.47) < 0.01
            and g_score["status"] == "provisional",
            "Golden employee PROCESS_SCORE equals 86.47 (73.50 / 85) provisional",
            g_score,
        )
        # F-P3-01 & F-P3-06: CONNECTED_RATE grain is ownership_interval (`7 / 8 = 87.50%`), NOT attempts (`18`)!
        rec.check(
            "P3-F01-CONNECTED-RATE-INTERVAL-GRAIN",
            int(g_conn.get("numerator") or 0) == 7
            and int(g_conn.get("denominator") or 0) == 8
            and abs(float(g_conn.get("value") or 0) - 87.5) < 0.01
            and int(g_att.get("value") or 0) > int(g_conn.get("denominator") or 0),
            "CONNECTED_RATE uses ownership_interval grain (7/8 = 87.50%), never divided by total calls",
            {"connected_rate": g_conn, "attempts_count": g_att},
        )

    # F-P3-09: Verify `staff_b` Personal Scorecard component contribution bounds & canonical rates
    st_sc, sc_body = actors["staff_b"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_my_scorecard",
        {"period_type": "monthly", "period_key": period_key},
    )
    sc_msg = (sc_body or {}).get("message") or {}
    sc_comps = sc_msg.get("score_components") or sc_msg.get("components") or []
    active_comps = [
        c
        for c in sc_comps
        if c.get("participates") is True or c.get("included") is True
    ]
    raw_pts_list = []
    bounds_ok = len(active_comps) == 4
    for comp in active_comps:
        w_cfg = float(comp.get("configured_weight") or comp.get("weight") or 0)
        raw_pt = comp.get("raw_weighted_points")
        if raw_pt is None:
            raw_pt = comp.get("weighted_points")
        raw_pt_f = float(raw_pt) if raw_pt is not None else -1.0
        if not (0.0 <= raw_pt_f <= w_cfg + 1e-6):
            bounds_ok = False
        raw_pts_list.append(round(raw_pt_f, 2))
    rec.check(
        "P3-F09-SCORE-COMPONENT-BOUNDS",
        st_sc == 200
        and bounds_ok
        and sorted(raw_pts_list) == [15.0, 16.0, 20.0, 22.5]
        and abs(sum(raw_pts_list) - 73.5) < 0.01,
        "Score components use canonical P3.md §7.5 rates (22.50 + 16.00 + 20.00 + 15.00 = 73.50 / 85 = 86.47) with 0 <= raw_points <= weight",
        {"raw_points": raw_pts_list, "components": active_comps},
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
    # 4. Security Matrix `S01..S09`, `S13`, `S14`, `S16`, `S17`, `F-P3-04` & BOD SLA Positive Control
    # ------------------------------------------------------------------
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

    # BOD SLA contract (`F-P3-10` / `S09`): `sla.list_obligations` & `waive_obligation` deny BOD (`403`),
    # while BOD's `/cskh/sla-operations` backing APIs (`get_executive_overview` + `get_metric_drilldown`)
    # return `200` with `>=1` redacted row and `0` PII!
    st_bod_raw_sla, _ = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.sla.list_obligations",
        {"page": 1, "page_size": 20},
    )
    st_bod_sla_dd, b_bod_sla_dd = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "scope_type": "company",
            "period_type": "monthly",
            "period_key": period_key,
            "page": 1,
            "page_size": 20,
        },
    )
    bod_sla_msg = (b_bod_sla_dd or {}).get("message") or {}
    bod_sla_rows = bod_sla_msg.get("rows") or []
    st_bod_waive, _ = actors["bod"].request(
        "POST",
        "/api/method/crm_cskh.api.sla.waive_obligation",
        {
            "obligation_id": seed_info["sample_obligation_id"],
            "reason": "BOD must remain strictly read-only",
        },
    )
    rec.check(
        "SEC-BOD-SLA-READONLY-ZERO-PII",
        st_bod_raw_sla == 403
        and st_bod_sla_dd == 200
        and len(bod_sla_rows) >= 1
        and not contains_raw_pii(bod_sla_msg)
        and st_bod_waive == 403,
        "BOD denied raw list_obligations & waive_obligation (403) while receiving 200 zero-PII SLA drilldown rows",
        {
            "raw_list_http": st_bod_raw_sla,
            "drilldown_http": st_bod_sla_dd,
            "row_count": len(bod_sla_rows),
            "waive_http": st_bod_waive,
        },
    )

    # F-P3-04: Leader B cannot see Team A historical obligations or closed private Team A Lead of transferred `staff_d`
    team_a_ob_d = seed_info.get("transferred_emp_team_a_obligation")
    priv_lead_a = seed_info.get("private_team_a_lead")
    st_la_obs, b_la_obs = actors["leader_a"].request(
        "GET",
        "/api/method/crm_cskh.api.sla.list_obligations",
        {"page": 1, "page_size": 100},
    )
    st_lb_obs, b_lb_obs = actors["leader_b"].request(
        "GET",
        "/api/method/crm_cskh.api.sla.list_obligations",
        {"page": 1, "page_size": 100},
    )
    la_ob_ids = {
        r.get("name") or r.get("obligation_id")
        for r in (((b_la_obs or {}).get("message") or {}).get("rows") or [])
    }
    lb_rows = ((b_lb_obs or {}).get("message") or {}).get("rows") or []
    lb_ob_ids = {r.get("name") or r.get("obligation_id") for r in lb_rows}
    lb_leaked_team_a = [
        r for r in lb_rows if (r.get("team_at_obligation") or r.get("team")) == team_a
    ]
    st_lb_priv_lead = 403
    if priv_lead_a:
        st_lb_priv_lead, _ = actors["leader_b"].request(
            "GET", f"/api/resource/CRM%20Lead/{urllib.parse.quote(priv_lead_a)}"
        )
    rec.check(
        "SEC-F04-TRANSFER-TEAM-ISOLATION",
        st_la_obs == 200
        and st_lb_obs == 200
        and (
            not team_a_ob_d
            or (team_a_ob_d in la_ob_ids and team_a_ob_d not in lb_ob_ids)
        )
        and len(lb_leaked_team_a) == 0
        and st_lb_priv_lead == 403,
        "Leader B blocked from Team A historical SLA obligations & closed private Team A Lead after employee transfer",
        {
            "team_a_ob_in_leader_a": team_a_ob_d in la_ob_ids,
            "team_a_ob_in_leader_b": team_a_ob_d in lb_ob_ids,
            "leader_b_leaked_team_a_rows": len(lb_leaked_team_a),
            "leader_b_private_lead_http": st_lb_priv_lead,
        },
    )

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
    # 5. Filter Options, Lead Source Filtering, & Drill-Down Reconciliation + Search/Sort (`F-P3-10`)
    # ------------------------------------------------------------------
    st_fo_bod, b_fo_bod = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_filter_options",
        {"period_type": "monthly", "period_key": period_key},
    )
    st_fo_la, b_fo_la = actors["leader_a"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_filter_options",
        {"period_type": "monthly", "period_key": period_key},
    )
    fo_bod = (b_fo_bod or {}).get("message") or {}
    fo_la = (b_fo_la or {}).get("message") or {}
    la_team_ids = {
        (t.get("id") or t.get("name") or t.get("team_id"))
        for t in (fo_la.get("teams") or [])
        if isinstance(t, dict)
    }
    rec.check(
        "P3-F10-FILTER-OPTIONS-SCOPED",
        st_fo_bod == 200
        and st_fo_la == 200
        and len(fo_bod.get("campaigns") or []) >= 1
        and len(fo_bod.get("teams") or []) >= 2
        and len(fo_bod.get("lead_sources") or []) >= 2
        and team_a in la_team_ids
        and team_b not in la_team_ids,
        "get_filter_options returns real campaigns, teams, and lead_sources strictly scoped per actor",
        {
            "bod_teams": len(fo_bod.get("teams") or []),
            "bod_sources": fo_bod.get("lead_sources"),
            "leader_a_teams": list(la_team_ids),
        },
    )

    # Verify `lead_source="Website"` filter measurably narrows server results
    st_src_ov, b_src_ov = actors["bod"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
        {
            "period_type": "monthly",
            "period_key": period_key,
            "lead_source": "Website",
        },
    )
    src_ov_m = (((b_src_ov or {}).get("message") or {}).get("metrics")) or {}
    all_fa_den = int(
        (ov_metrics.get("FIRST_ATTEMPT_SLA_RATE") or {}).get("denominator") or 0
    )
    web_fa_den = int(
        (src_ov_m.get("FIRST_ATTEMPT_SLA_RATE") or {}).get("denominator") or 0
    )
    rec.check(
        "P3-F10-LEAD-SOURCE-FILTER-EFFECT",
        st_src_ov == 200 and 0 < web_fa_den < all_fa_den,
        f"lead_source='Website' narrows server FIRST_ATTEMPT denominator ({web_fa_den} < {all_fa_den})",
        {"website_denominator": web_fa_den, "all_denominator": all_fa_den},
    )

    core_drill_metrics = [
        "FIRST_ATTEMPT_SLA_RATE",
        "WORK_ITEM_FIRST_ATTEMPT_SLA_RATE",
        "FOLLOWUP_SLA_RATE",
        "CONNECTED_RATE",
        "VALID_OUTCOME_LOGGING_RATE",
        "ELIGIBLE_COVERAGE",
    ]
    for m_code in core_drill_metrics:
        if m_code not in ov_metrics:
            continue
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

    # Drill-down server `search` and `sort_dir` (`asc` vs `desc`) verification
    st_dd_asc, b_dd_asc = actors["admin"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "scope_type": "company",
            "period_type": "monthly",
            "period_key": period_key,
            "filter_bucket": "all_denominator",
            "sort_by": "effective_due_at",
            "sort_dir": "asc",
            "page": 1,
            "page_size": 5,
        },
    )
    st_dd_desc, b_dd_desc = actors["admin"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "scope_type": "company",
            "period_type": "monthly",
            "period_key": period_key,
            "filter_bucket": "all_denominator",
            "sort_by": "effective_due_at",
            "sort_dir": "desc",
            "page": 1,
            "page_size": 5,
        },
    )
    asc_rows = ((b_dd_asc or {}).get("message") or {}).get("rows") or []
    desc_rows = ((b_dd_desc or {}).get("message") or {}).get("rows") or []
    sample_wi = asc_rows[0].get("work_item") if asc_rows else ""
    st_dd_srch, b_dd_srch = actors["admin"].request(
        "GET",
        "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "scope_type": "company",
            "period_type": "monthly",
            "period_key": period_key,
            "filter_bucket": "all_denominator",
            "search": sample_wi,
            "page": 1,
            "page_size": 20,
        },
    )
    srch_tot = (
        ((b_dd_srch or {}).get("message") or {}).get("pagination", {}).get("total", 0)
    )
    rec.check(
        "P3-F10-DRILLDOWN-SEARCH-SORT",
        st_dd_asc == 200
        and st_dd_desc == 200
        and st_dd_srch == 200
        and len(asc_rows) >= 2
        and len(desc_rows) >= 2
        and asc_rows[0].get("work_item") != desc_rows[0].get("work_item")
        and 1 <= srch_tot < all_fa_den,
        "Drilldown server sort_dir (asc vs desc) reorders rows and search narrows cohort",
        {
            "asc_first_wi": asc_rows[0].get("work_item") if asc_rows else None,
            "desc_first_wi": desc_rows[0].get("work_item") if desc_rows else None,
            "search_total": srch_tot,
        },
    )

    # S03: Staff A drills down own historical obligation after Lead transferred -> 0 PII
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
        st_tc == 200
        and team_a in tc_rows
        and team_b in tc_rows
        and bool(tc_rows[team_a].get("team_name"))
        and tc_rows[team_a].get("team_name") != team_a,
        "Team comparison attributes obligations by historical team-as-of and resolves human-readable team_name",
        {
            "teams": {
                tid: tc_rows[tid].get("team_name")
                for tid in (team_a, team_b)
                if tid in tc_rows
            }
        },
    )

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

    # ------------------------------------------------------------------
    # 8. Unified Single-Source Feature-Flag Rollback (`F-P3-08`)
    # ------------------------------------------------------------------
    bench_execute(
        "crm_cskh.tools.p3_e2e_seed.set_p3_feature_flags_for_test",
        {"enabled": False},
    )
    try:
        st_rb_page, _ = actors["bod"].get_html("/cskh/quality-overview")
        st_rb_api, b_rb_api = actors["bod"].request(
            "GET",
            "/api/method/crm_cskh.api.p3_reporting.get_executive_overview",
            {"period_type": "monthly", "period_key": period_key},
        )
        st_rb_p2, html_rb_p2 = actors["staff_a"].get_html("/cskh/my-customers")
        rec.check(
            "P3-F08-UNIFIED-FLAG-ROLLBACK",
            st_rb_page == 403
            and st_rb_api in (403, 503)
            and st_rb_p2 == 200
            and 'href="/cskh/scorecard"' not in html_rb_p2,
            "Disabling CSKH Policy P3 flags blocks P3 routes & APIs while keeping P1/P2 (/cskh/my-customers) 200 and hiding P3 nav",
            {
                "quality_overview_http": st_rb_page,
                "overview_api_http": st_rb_api,
                "api_code": extract_code(b_rb_api),
                "p2_my_customers_http": st_rb_p2,
            },
        )
    finally:
        bench_execute(
            "crm_cskh.tools.p3_e2e_seed.set_p3_feature_flags_for_test",
            {"enabled": True},
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
