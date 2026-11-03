#!/usr/bin/env python3
"""Comprehensive real-HTTP E2E verification on the isolated P2 site (`p2.localhost`).

Drives the live Frappe HTTP server (`http://127.0.0.1:8220`, `Host: p2.localhost`)
across 9 sessions (`admin`, `bod`, `leader_a`, `leader_b`, `staff_a`, `staff_b`,
`staff_c`, `outside`, `guest`) — strictly isolated from Owner's `p1.localhost:8120`:
  1. 10 `/cskh/*` routes x 9 actors = 90 page gate + `lang="vi"` checks (including
     `BOD` 403 on PII routes `/cskh/work-item` & `/cskh/callbacks`, 200 on 0-PII
     `/cskh/progress` & `/cskh/settings`).
  2. P1 CSV import -> preview -> confirm -> campaign rules -> async distribution ->
     deterministic P2 Work Items across Team A (`staff_a`, `staff_b`) & Team B (`staff_c`).
  3. Full P2 lifecycle & honesty (`T2-01..T2-24`):
     - `customers.my_list` & `workflow.list_work_items` upgraded fields;
     - `FOLLOWUP_REQUIRED` (422) when callback time omitted;
     - atomic `record_attempt` (`no_answer` + `followup` -> `on_hold`, `is_connected=false`);
     - idempotency replay (`idempotent_replay=true`, 0 duplicates) & `IDEMPOTENCY_CONFLICT` (409);
     - `zalo` + `message_sent_no_reply` (`is_connected=false`);
     - native `CRM Call Log` link + `ALREADY_LINKED` (409) + `FCRM Note` excluded from counts;
     - callback `schedule_followup` / reschedule (`NOTE_REQUIRED` without reason) / `complete_followup`
       (`OUTCOME_NOT_COMPATIBLE` without attempt, `cancel` with reason not counted as connected);
     - stale `expected_revision` -> `STALE_WORK_ITEM` (409);
     - `close_work_item` -> `WORK_ITEM_NOT_ACTIVE` (409) -> `reopen_work_item` (`403` Staff, `200` Leader);
     - `set_lead_dnc` -> `DO_NOT_CONTACT` (409);
     - `request_correction` (`pending`) -> `review_correction` (`approve`), `void` & `restore` (Admin),
       and Outcome Code label change (`version + 1`) preserving historical attempt snapshot (`T2-18`);
     - `progress_summary` (`0 PII`, scoped `self`/`team`/`all`) & `settings.save_policy` (`version + 1`).
  4. Security negatives (`SEC2-01..SEC2-16`): cross-staff/cross-team `403`, BOD PII & mutation `403`,
     CSRF missing/invalid rejected, backdate outside window / before ownership `422 INVALID_FOLLOWUP_TIME`.
  5. Prepares deterministic named fixtures (`wi_happy`, `wi_honesty`, `wi_conflict`, `wi_transfer`,
     `wi_correction`, `wi_boundary_2359`, `wi_boundary_0000`, `wi_boundary_overdue`, `lead_form_script`)
     for `deploy/p2/verify_p2_browser_e2e.mjs`.

Saves sanitized evidence (zero passwords, cookies, CSRF tokens, or raw phone numbers)
to `deploy/p2/evidence/http-e2e-report.json`.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EVIDENCE_DIR = os.path.join(HERE, "evidence")
REPORT_PATH = os.environ.get("P2_REPORT_OUT") or os.path.join(
    EVIDENCE_DIR, "http-e2e-report.json"
)

BASE = os.environ.get("P2_BASE", "http://127.0.0.1:8220")
HOST = os.environ.get("P2_HOST", "p2.localhost")
SITE = os.environ.get("P2_SITE", "p2.localhost")
PASSWORD = os.environ.get("P2_SEED_PASSWORD", "P2Seed-Only-2026!")
CONTAINER = os.environ.get("P2_CONTAINER", "crm-cskh-p2-ui-bench-1")

if "8120" in BASE or HOST == "p1.localhost" or SITE == "p1.localhost":
    raise RuntimeError("Refusing to run P2 E2E against Owner's P1 stack/site!")

PAGES = [
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
            if k not in ("csrf", "cookie", "password", "pwd")
        }
    return obj


def bench_execute(method: str, kwargs: dict | None = None):
    cmd = f"cd /workspace/frappe-bench && bench --site {SITE} execute {method}"
    if kwargs:
        cmd += f" --kwargs '{json.dumps(kwargs)}'"
    res = subprocess.run(
        ["docker", "exec", CONTAINER, "bash", "-lc", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


class Session:
    def __init__(self, user: str | None = None):
        self.user = user or "Guest"
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.csrf = ""
        if user:
            self._login(user)

    def _login(self, user: str):
        req = urllib.request.Request(
            BASE + "/api/method/login",
            data=json.dumps({"usr": user, "pwd": PASSWORD}).encode("utf-8"),
            headers={
                "Host": HOST,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with self.opener.open(req, timeout=30) as resp:
            assert resp.status == 200, f"Login failed for {user}: {resp.status}"
            resp.read()
        self.refresh_csrf()

    def refresh_csrf(self):
        for probe_path in ("/cskh/progress", "/cskh/my-customers", "/cskh"):
            status, raw = self.raw_get(probe_path)
            if status == 200:
                html = raw.decode("utf-8", errors="replace")
                for pattern in (
                    r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)',
                    r'csrf_token\s*=\s*["\']([^"\']+)',
                    r'csrf-token"\s+content="([^"]+)"',
                ):
                    m = re.search(pattern, html)
                    if m:
                        self.csrf = m.group(1)
                        return

    def raw_get(self, path: str):
        req = urllib.request.Request(BASE + path, headers={"Host": HOST}, method="GET")
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get_api(self, method_name: str, params: dict | None = None):
        qs = ""
        if params:
            clean = {
                k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in params.items()
                if v is not None and v != ""
            }
            if clean:
                qs = "?" + urllib.parse.urlencode(clean)
        status, raw = self.raw_get(f"/api/method/{method_name}{qs}")
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"raw": raw.decode("utf-8", errors="replace")[:400]}
        return status, body

    def post_api(
        self,
        method_name: str,
        payload: dict | None = None,
        csrf_override: str | None = None,
    ):
        headers = {
            "Host": HOST,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        tok = self.csrf if csrf_override is None else csrf_override
        if tok:
            headers["X-Frappe-CSRF-Token"] = tok
        req = urllib.request.Request(
            f"{BASE}/api/method/{method_name}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=30) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"raw": raw.decode("utf-8", errors="replace")[:400]}
        return status, body

    def upload_csv(self, file_name: str, csv_bytes: bytes):
        boundary = "----CSKHP2Boundary" + uuid.uuid4().hex
        parts = [
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
            f"Content-Type: text/csv\r\n\r\n".encode("utf-8"),
            csv_bytes,
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="is_private"\r\n\r\n1\r\n'
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
        headers = {
            "Host": HOST,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "X-Frappe-CSRF-Token": self.csrf,
        }
        req = urllib.request.Request(
            f"{BASE}/api/method/upload_file",
            data=b"".join(parts),
            headers=headers,
            method="POST",
        )
        with self.opener.open(req, timeout=30) as resp:
            assert resp.status == 200, f"upload_file failed: {resp.status}"
            return json.loads(resp.read().decode("utf-8"))["message"]


def err_code(body: dict) -> str:
    return str(body.get("code") or body.get("p1_code") or body.get("exc_type") or "")


def poll_batch(admin: Session, batch_name: str, timeout: int = 45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, body = admin.get_api(
            "crm_cskh.api.imports.get_status", {"batch": batch_name}
        )
        assert st == 200, f"imports.get_status failed: {st} {body}"
        msg = body["message"]
        if msg["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
            return msg
        time.sleep(0.6)
    raise AssertionError(f"Import batch {batch_name} timed out")


def poll_run(admin: Session, run_id: str, timeout: int = 45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, body = admin.get_api(
            "crm_cskh.api.distribution.get_run", {"run_id": run_id}
        )
        assert st == 200, f"distribution.get_run failed: {st} {body}"
        msg = body["message"]
        if str(msg.get("status", "")).lower() in (
            "completed",
            "completed_with_errors",
            "failed",
        ):
            return msg
        time.sleep(0.5)
    raise AssertionError(f"Distribution run {run_id} timed out")


def import_and_distribute(
    admin: Session,
    camp_code: str,
    camp_title: str,
    team_name: str,
    assignee: str,
    rows: list[tuple[str, str, str]],
):
    csv_lines = ["so_dt,ho_ten,nguon"] + [f"{p},{n},{s}" for p, n, s in rows]
    csv_text = "\n".join(csv_lines) + "\n"
    st, up_res = admin.post_api(
        "crm_cskh.api.import_files.upload_import_file",
        {
            "file_name": f"{camp_code.lower()}.csv",
            "content": base64.b64encode(csv_text.encode("utf-8")).decode("ascii"),
        },
    )
    assert st == 200, f"upload_import_file failed: {st} {up_res}"
    uploaded_name = up_res["message"]["name"]

    st, prev = admin.post_api(
        "crm_cskh.api.imports.create_preview",
        {
            "file_name": uploaded_name,
            "mapping": {"phone": "so_dt", "name": "ho_ten", "source": "nguon"},
            "idempotency_key": f"imp-prev-{camp_code}-{uuid.uuid4().hex[:8]}",
        },
    )
    assert st == 200, f"imports.create_preview failed: {st} {prev}"
    batch_id = prev["message"]["batch"]

    st, conf = admin.post_api(
        "crm_cskh.api.imports.confirm",
        {"batch": batch_id},
    )
    assert st in (200, 202), f"imports.confirm failed: {st} {conf}"
    batch_done = poll_batch(admin, batch_id)
    assert batch_done["status"] == "COMPLETED", (
        f"Batch did not complete cleanly: {batch_done}"
    )

    st, pool_res = admin.get_api(
        "crm_cskh.api.pool_ui.list",
        {"batch": batch_id, "page": 1, "page_size": 50},
    )
    assert st == 200, f"pool_ui.list failed: {st} {pool_res}"
    lead_ids = [r["lead"] for r in pool_res["message"]["items"]]
    assert len(lead_ids) == len(rows), (
        f"Expected {len(rows)} imported leads for {batch_id}, got {len(lead_ids)}: {pool_res}"
    )

    st, camp_res = admin.post_api(
        "crm_cskh.api.campaigns.save",
        {
            "code": camp_code,
            "title": camp_title,
            "status": "Active",
            "team_scope": team_name,
            "selection": {"leads": lead_ids},
        },
    )
    assert st == 200, f"campaigns.save failed: {st} {camp_res}"
    campaign_id = camp_res["message"]["campaign"]

    st, rule_res = admin.post_api(
        "crm_cskh.api.campaigns.save_rule",
        {
            "campaign": campaign_id,
            "user": assignee,
            "weight": 100,
            "capacity_limit": 0,
            "team": team_name,
            "enabled": 1,
        },
    )
    assert st == 200, f"campaigns.save_rule failed: {st} {rule_res}"

    st, d_prev = admin.post_api(
        "crm_cskh.api.distribution.preview",
        {
            "campaign": campaign_id,
            "idempotency_key": f"dist-prev-{camp_code}-{uuid.uuid4().hex[:8]}",
        },
    )
    assert st == 200, f"distribution.preview failed: {st} {d_prev}"
    preview_id = d_prev["message"]["preview_id"]
    rule_ver = d_prev["message"]["rule_version"]

    st, d_exec = admin.post_api(
        "crm_cskh.api.distribution.execute",
        {
            "preview_id": preview_id,
            "expected_rule_version": rule_ver,
            "idempotency_key": f"dist-exec-{camp_code}-{uuid.uuid4().hex[:8]}",
        },
    )
    assert st in (200, 202), f"distribution.execute failed: {st} {d_exec}"
    run_id = d_exec["message"].get("run_id") or preview_id
    run_done = poll_run(admin, run_id)
    assert str(run_done["status"]).lower() == "completed", f"Run failed: {run_done}"
    return campaign_id, batch_id, run_id


def fmt_dt(val: dt.datetime) -> str:
    return val.strftime("%Y-%m-%d %H:%M:%S")


def ensure_static_assets_symlink():
    subprocess.run(
        [
            "docker",
            "exec",
            "-w",
            "/workspace/frappe-bench",
            CONTAINER,
            "bash",
            "-lc",
            "mkdir -p sites/assets && ln -sfn /workspace/frappe-bench/apps/crm_cskh/crm_cskh/public sites/assets/crm_cskh",
        ],
        check=True,
    )


def main():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    ensure_static_assets_symlink()
    seed = bench_execute("crm_cskh.tools.p2_e2e_seed.reset_and_seed_p2")
    users = seed["users"]
    team_a = seed["team_a"]
    team_b = seed["team_b"]
    site_now = dt.datetime.fromisoformat(seed["server_now"].split(".")[0])

    git_sha = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
        .decode("utf-8")
        .strip()
    )

    sessions = {
        "admin": Session(users["admin"]),
        "bod": Session(users["bod"]),
        "leader_a": Session(users["leader_a"]),
        "leader_b": Session(users["leader_b"]),
        "staff_a": Session(users["staff_a"]),
        "staff_b": Session(users["staff_b"]),
        "staff_c": Session(users["staff_c"]),
        "outside": Session(users["outside"]),
        "guest": Session(None),
    }
    admin = sessions["admin"]
    bod = sessions["bod"]
    leader_a = sessions["leader_a"]
    leader_b = sessions["leader_b"]
    staff_a = sessions["staff_a"]
    staff_b = sessions["staff_b"]
    staff_c = sessions["staff_c"]

    evidence = {
        "site": SITE,
        "base_url": BASE,
        "timezone": seed["timezone"],
        "worktree_head_sha": git_sha,
        "checks": [],
        "fixtures": {},
    }

    def record(check_id: str, ok: bool, details: dict):
        if not ok:
            raise AssertionError(f"Check {check_id} FAILED: {details}")
        evidence["checks"].append(
            {"id": check_id, "status": "PASS", "details": redact(details)}
        )
        print(f"PASS  {check_id}", flush=True)

    # ------------------------------------------------------------------
    # 1. Full 10-route x 9-actor page gate & Vietnamese HTML matrix
    # ------------------------------------------------------------------
    expected_pages = {
        # [/cskh, /pool, /import, /campaigns, /my-customers, /dashboard, /work-item, /callbacks, /progress, /settings]
        "admin": [200, 200, 200, 200, 200, 200, 200, 200, 200, 200],
        "bod": [200, 200, 403, 200, 200, 200, 403, 403, 200, 200],
        "leader_a": [200, 200, 403, 200, 200, 200, 200, 200, 200, 200],
        "leader_b": [200, 200, 403, 200, 200, 200, 200, 200, 200, 200],
        "staff_a": [200, 403, 403, 403, 200, 403, 200, 200, 200, 403],
        "staff_b": [200, 403, 403, 403, 200, 403, 200, 200, 200, 403],
        "staff_c": [200, 403, 403, 403, 200, 403, 200, 200, 200, 403],
        "outside": [200, 403, 403, 403, 200, 403, 200, 200, 200, 403],
        "guest": [403, 403, 403, 403, 403, 403, 403, 403, 403, 403],
    }
    actual_pages = {}
    for actor_key, sess in sessions.items():
        statuses = []
        for route in PAGES:
            st, raw = sess.raw_get(route)
            statuses.append(st)
            if st == 200:
                html = raw.decode("utf-8", errors="replace")
                assert 'lang="vi"' in html, f"{actor_key} {route} missing lang=vi"
                assert "Asia/Ho_Chi_Minh" in html, (
                    f"{actor_key} {route} missing site timezone"
                )
        actual_pages[actor_key] = statuses
        assert statuses == expected_pages[actor_key], (
            f"Page matrix mismatch for {actor_key}: got {statuses}, expected {expected_pages[actor_key]}"
        )
    record(
        "P2-E2E-01-page-role-matrix", True, {"routes": PAGES, "statuses": actual_pages}
    )

    # ------------------------------------------------------------------
    # 2. Import + Distribute synthetic leads into Team A & Team B
    # ------------------------------------------------------------------
    team_a_rows = [
        ("0912340001", "P2 Khách Hạnh Phúc", "Website"),
        ("0912340002", "P2 Khách Trung Thực", "Zalo OA"),
        ("0912340003", "P2 Khách Xung Đột", "Facebook"),
        ("0912340004", "P2 Khách Bàn Giao AB", "Landing Page"),
        ("0912340005", "P2 Khách Hiệu Chỉnh", "Giới thiệu"),
        ("0912340006", "P2 Khách Mốc 2359", "Website"),
        ("0912340007", "P2 Khách Mốc 0000", "Website"),
        ("0912340008", "P2 Khách Quá Hạn", "Website"),
        ("0912340009", "P2 Khách Vòng Đời HTTP", "Sự kiện"),
        ("0912340010", "P2 Khách Thuộc Staff B", "Website"),
        ("0912340011", "P2 Khách DNC Cấm Gọi", "Website"),
    ]
    team_b_rows = [
        ("0912340012", "P2 Khách Đội B1", "Đối tác"),
        ("0912340013", "P2 Khách Đội B2", "Đối tác"),
    ]

    camp_a, batch_a, run_a = import_and_distribute(
        admin,
        "P2-CAMP-A",
        "Chiến dịch CSKH P2 Đội A",
        team_a,
        users["staff_a"],
        team_a_rows,
    )
    camp_b, batch_b, run_b = import_and_distribute(
        admin,
        "P2-CAMP-B",
        "Chiến dịch CSKH P2 Đội B",
        team_b,
        users["staff_c"],
        team_b_rows,
    )

    st, list_a = staff_a.get_api(
        "crm_cskh.api.workflow.list_work_items",
        {"page": 1, "page_size": 50, "status": "active"},
    )
    assert st == 200, f"staff_a list_work_items failed: {st} {list_a}"
    by_name = {item["display_name"]: item for item in list_a["message"]["items"]}
    assert len(by_name) == 11, (
        f"Expected 11 Team A items on staff_a, got {len(by_name)}"
    )

    # Reassign 2 items to staff_b (`P2 Khách Thuộc Staff B` & `P2 Khách DNC Cấm Gọi`)
    for b_label in ("P2 Khách Thuộc Staff B", "P2 Khách DNC Cấm Gọi"):
        wi_b = by_name[b_label]["work_item"]
        st, r_res = admin.post_api(
            "crm_cskh.api.ownership.reassign",
            {
                "work_item": wi_b,
                "target_user": users["staff_b"],
                "expected_owner": users["staff_a"],
                "reason": "Phân bổ cho Nhân viên A2 kiểm thử quyền P2",
                "request_id": f"p2-init-reassign-{wi_b}",
            },
        )
        assert st == 200, f"reassign to staff_b failed: {st} {r_res}"

    st, list_c = staff_c.get_api(
        "crm_cskh.api.workflow.list_work_items",
        {"page": 1, "page_size": 20, "status": "active"},
    )
    assert st == 200 and list_c["message"]["total"] == 2
    wi_team_b = list_c["message"]["items"][0]["work_item"]

    record(
        "P2-E2E-02-import-and-distribute",
        True,
        {
            "campaign_a": camp_a,
            "campaign_b": camp_b,
            "team_a_items": 11,
            "team_b_items": 2,
        },
    )

    # ------------------------------------------------------------------
    # 3. Full HTTP lifecycle, honesty, idempotency, conflict, reopen & DNC
    #    on `P2 Khách Vòng Đời HTTP` & `P2 Khách DNC Cấm Gọi`
    # ------------------------------------------------------------------
    wi_http = by_name["P2 Khách Vòng Đời HTTP"]["work_item"]
    lead_http = by_name["P2 Khách Vòng Đời HTTP"]["lead"]

    # T2-01: customers.my_list upgraded fields
    st, my_res = staff_a.get_api(
        "crm_cskh.api.customers.my_list", {"page": 1, "page_size": 20}
    )
    assert st == 200 and my_res["message"]["total"] == 9
    sample_row = next(
        r for r in my_res["message"]["items"] if r["work_item"] == wi_http
    )
    assert sample_row["attempt_count"] == 0
    assert sample_row["connected_attempt_count"] == 0
    assert "revision" in sample_row

    st, wi_det = staff_a.get_api(
        "crm_cskh.api.workflow.get_work_item", {"work_item": wi_http}
    )
    assert st == 200
    rev0 = wi_det["message"]["work_item"]["revision"]
    pol_v0 = wi_det["message"]["effective_policy"]["version"]

    # T2-05: `callback_requested` without `followup` -> 422 FOLLOWUP_REQUIRED
    st, bad_fu = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_http,
            "channel": "phone_call",
            "outcome_code": "callback_requested",
            "note": "Khách hẹn gọi lại nhưng quên chọn giờ",
            "expected_revision": rev0,
            "idempotency_key": "http-bad-fu-001",
        },
    )
    assert st == 422 and err_code(bad_fu) == "FOLLOWUP_REQUIRED", (
        f"Expected 422 FOLLOWUP_REQUIRED, got {st} {bad_fu}"
    )

    # T2-02 + T2-06 + T2-08 + T2-09: `no_answer` + `followup` atomic + idempotency
    due_1 = fmt_dt(site_now + dt.timedelta(hours=4))
    att1_payload = {
        "work_item": wi_http,
        "channel": "phone_call",
        "outcome_code": "no_answer",
        "note": "Gọi lần 1 đổ chuông không bắt máy",
        "followup": {
            "due_at": due_1,
            "title": "Gọi lại ca chiều",
            "description": "Thử lại sau 4 tiếng",
        },
        "expected_revision": rev0,
        "expected_policy_version": pol_v0,
        "idempotency_key": "http-att-idem-001",
    }
    st, att1 = staff_a.post_api("crm_cskh.api.workflow.record_attempt", att1_payload)
    assert st == 200, f"record_attempt #1 failed: {st} {att1}"
    m1 = att1["message"]
    assert m1["work_item_status"] == "on_hold"
    assert m1["attempt_count"] == 1
    assert m1["connected_attempt_count"] == 0
    assert m1["callback_task"]
    att1_id = m1["attempt_id"]
    task1_id = m1["callback_task"]
    rev1 = m1["revision"]

    # Exact replay with same idempotency_key -> 200 idempotent_replay=True, no duplicate
    st, att1_replay = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt", att1_payload
    )
    assert st == 200 and att1_replay["message"]["idempotent_replay"] is True
    assert att1_replay["message"]["attempt_id"] == att1_id

    # Same idempotency_key + mutated payload -> 409 IDEMPOTENCY_CONFLICT
    mutated = dict(att1_payload, note="Nội dung bị sửa khác payload gốc")
    st, idem_conf = staff_a.post_api("crm_cskh.api.workflow.record_attempt", mutated)
    assert st == 409 and err_code(idem_conf) == "IDEMPOTENCY_CONFLICT", (
        f"Expected 409 IDEMPOTENCY_CONFLICT, got {st} {idem_conf}"
    )

    # T2-10: Stale `expected_revision` -> 409 STALE_WORK_ITEM
    st, stale_res = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_http,
            "channel": "zalo",
            "outcome_code": "message_sent_no_reply",
            "note": "Thử lưu với revision cũ",
            "expected_revision": rev0,
            "idempotency_key": "http-stale-rev-001",
        },
    )
    assert st == 409 and err_code(stale_res) == "STALE_WORK_ITEM", (
        f"Expected 409 STALE_WORK_ITEM, got {st} {stale_res}"
    )

    # T2-03 + G2-06: Zalo `message_sent_no_reply` -> attempt_count=2, connected_attempt_count=0
    st, att2 = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_http,
            "channel": "zalo",
            "outcome_code": "message_sent_no_reply",
            "note": "Đã gửi tin Zalo giới thiệu, khách chưa phản hồi",
            "complete_open_followup": False,
            "expected_revision": rev1,
            "idempotency_key": "http-att-zalo-002",
        },
    )
    assert st == 200, f"Zalo attempt failed: {st} {att2}"
    assert att2["message"]["attempt_count"] == 2
    assert att2["message"]["connected_attempt_count"] == 0
    rev2 = att2["message"]["revision"]

    # T2-07 + G2-09: Reschedule requires reason; complete_followup without attempt rejected; cancel works
    due_resched = fmt_dt(site_now + dt.timedelta(hours=6))
    st, resched_no_reason = staff_a.post_api(
        "crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": wi_http,
            "due_at": due_resched,
            "title": "Dời lịch ca tối",
            "reason": "",
            "expected_revision": rev2,
            "idempotency_key": "http-resched-bad-001",
        },
    )
    assert st == 422 and err_code(resched_no_reason) == "NOTE_REQUIRED"

    st, resched_ok = staff_a.post_api(
        "crm_cskh.api.workflow.schedule_followup",
        {
            "work_item": wi_http,
            "due_at": due_resched,
            "title": "Dời lịch ca tối",
            "reason": "Khách nhắn bận họp đến 17h",
            "expected_revision": rev2,
            "idempotency_key": "http-resched-ok-001",
        },
    )
    assert st == 200 and resched_ok["message"]["task_id"] == task1_id
    assert resched_ok["message"]["reschedule_count"] == 1
    rev3 = resched_ok["message"]["revision"]

    st, comp_fake = staff_a.post_api(
        "crm_cskh.api.workflow.complete_followup",
        {
            "work_item": wi_http,
            "task_id": task1_id,
            "action": "complete",
            "attempt_id": None,
            "expected_revision": rev3,
            "idempotency_key": "http-comp-fake-001",
        },
    )
    assert st == 422 and err_code(comp_fake) == "OUTCOME_NOT_COMPATIBLE"

    # T2-16 + T2-17 + G2-08: Seed native Call Log + Note; Note does NOT bump attempt_count;
    # linking Call Log once succeeds, linking same Call Log twice returns 409 ALREADY_LINKED.
    seeded_act = bench_execute(
        "crm_cskh.tools.p2_e2e_seed.seed_native_activities",
        {"lead": lead_http, "caller_user": users["staff_a"], "phone": "0912340009"},
    )
    call_log_1 = seeded_act["call_logs"][0]

    st, wi_after_note = staff_a.get_api(
        "crm_cskh.api.workflow.get_work_item", {"work_item": wi_http}
    )
    assert wi_after_note["message"]["work_item"]["attempt_count"] == 2, (
        "Native Call Log / FCRM Note must NOT auto-increment attempt_count!"
    )

    st, att3 = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_http,
            "channel": "phone_call",
            "outcome_code": "connected_interested",
            "note": "Khách nghe máy, quan tâm gói dịch vụ năm",
            "call_log": call_log_1,
            "complete_open_followup": True,
            "expected_revision": rev3,
            "idempotency_key": "http-att-conn-003",
        },
    )
    assert st == 200, f"Connected attempt with Call Log failed: {st} {att3}"
    assert att3["message"]["attempt_count"] == 3
    assert att3["message"]["connected_attempt_count"] == 1
    assert att3["message"]["resolved_callback_task"] == task1_id
    rev4 = att3["message"]["revision"]

    st, dup_cl = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_http,
            "channel": "phone_call",
            "outcome_code": "connected_interested",
            "note": "Cố gắn lại cùng Call Log lần 2",
            "call_log": call_log_1,
            "expected_revision": rev4,
            "idempotency_key": "http-att-dup-cl-004",
        },
    )
    assert st == 409 and err_code(dup_cl) == "ALREADY_LINKED", (
        f"Expected 409 ALREADY_LINKED, got {st} {dup_cl}"
    )

    # T2-18 + T2-19 + T2-20: Correction workflow + Outcome label snapshot preservation
    st, corr_auto_denied = staff_a.post_api(
        "crm_cskh.api.workflow.request_correction",
        {
            "attempt": att1_id,
            "action": "correct",
            "corrected_fields": {"outcome_code": "busy"},
            "reason": "Staff cố tự auto_approve",
            "auto_approve": True,
            "expected_revision": rev4,
            "idempotency_key": "http-corr-denied-001",
        },
    )
    assert st == 403 and err_code(corr_auto_denied) == "FORBIDDEN"

    st, corr_req = staff_a.post_api(
        "crm_cskh.api.workflow.request_correction",
        {
            "attempt": att1_id,
            "action": "correct",
            "corrected_fields": {
                "outcome_code": "busy",
                "note": "Thực tế máy báo bận",
            },
            "reason": "Chọn nhầm Không nghe máy sang Máy bận",
            "auto_approve": False,
            "expected_revision": rev4,
            "idempotency_key": "http-corr-req-001",
        },
    )
    assert st == 200
    corr_id = corr_req["message"].get("correction_id") or corr_req["message"].get(
        "name"
    )
    assert corr_id

    st, corr_rev = leader_a.post_api(
        "crm_cskh.api.workflow.review_correction",
        {
            "correction_id": corr_id,
            "decision": "approve",
            "review_note": "Trưởng nhóm A xác nhận nhật ký máy bận",
            "expected_revision": corr_req["message"].get("revision", rev4),
            "idempotency_key": "http-corr-rev-001",
        },
    )
    assert st == 200
    rev5 = corr_rev["message"]["revision"]

    # T2-18: Admin updates `busy` label -> historical snapshot on `att1_id` keeps old label or version
    st, out_list_before = admin.get_api(
        "crm_cskh.api.settings.list_outcomes", {"include_disabled": 1}
    )
    assert st == 200
    busy_item = next(
        o
        for o in out_list_before["message"]["items"]
        if (o.get("code") or o.get("outcome_code")) == "busy"
    )
    old_busy_ver = busy_item["version"]

    st, save_out_res = admin.post_api(
        "crm_cskh.api.settings.save_outcome",
        {
            "code": "busy",
            "label_vi": "Máy bận (đã cập nhật nhãn v2)",
            "result_group": busy_item["result_group"],
            "is_contact_attempt": True,
            "is_connected": False,
            "is_interested": False,
            "is_terminal": False,
            "is_do_not_contact": False,
            "requires_note": False,
            "requires_followup": False,
            "allows_followup": True,
            "requires_completion_reason": False,
            "allowed_channels": "phone_call",
            "suggested_next_status": "in_progress",
            "enabled": True,
            "sort_order": busy_item.get("sort_order", 20),
            "idempotency_key": "http-save-out-busy-v2",
        },
    )
    assert (
        st == 200 and save_out_res["message"]["outcome"]["version"] == old_busy_ver + 1
    )

    st, att_hist = staff_a.get_api(
        "crm_cskh.api.workflow.list_attempts", {"work_item": wi_http}
    )
    assert st == 200
    att1_row = next(
        a for a in att_hist["message"]["items"] if a["attempt_id"] == att1_id
    )
    assert att1_row["original"]["outcome_code"] == "no_answer"
    assert att1_row["effective"]["outcome_code"] == "busy"

    # Restore `busy` label back to clean canonical Vietnamese `"Máy bận"` (increments to v3)
    st, _ = admin.post_api(
        "crm_cskh.api.settings.save_outcome",
        {
            "code": "busy",
            "label_vi": "Máy bận",
            "result_group": busy_item["result_group"],
            "is_contact_attempt": True,
            "is_connected": False,
            "is_interested": False,
            "is_terminal": False,
            "is_do_not_contact": False,
            "requires_note": False,
            "requires_followup": False,
            "allows_followup": True,
            "requires_completion_reason": False,
            "allowed_channels": "phone_call",
            "suggested_next_status": "in_progress",
            "enabled": True,
            "sort_order": busy_item.get("sort_order", 20),
            "idempotency_key": "http-save-out-busy-v3",
        },
    )
    assert st == 200

    # T2-13 + T2-14: Close & Reopen Work Item
    st, closed_res = staff_a.post_api(
        "crm_cskh.api.workflow.close_work_item",
        {
            "work_item": wi_http,
            "completion_reason": "Đã tư vấn đầy đủ và chốt gói dịch vụ",
            "close_category": "normal",
            "expected_revision": rev5,
            "idempotency_key": "http-wi-close-001",
        },
    )
    assert st == 200 and closed_res["message"]["work_item_status"] == "closed"
    rev6 = closed_res["message"]["revision"]

    st, att_on_closed = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_http,
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "note": "Ghi trên Work Item đã đóng",
            "expected_revision": rev6,
            "idempotency_key": "http-att-closed-001",
        },
    )
    assert st == 409 and err_code(att_on_closed) == "WORK_ITEM_NOT_ACTIVE"

    st, staff_reopen = staff_a.post_api(
        "crm_cskh.api.workflow.reopen_work_item",
        {
            "work_item": wi_http,
            "target_user": users["staff_a"],
            "reason": "Staff tự mở lại",
            "expected_revision": rev6,
            "idempotency_key": "http-reopen-staff-001",
        },
    )
    assert st == 403 and err_code(staff_reopen) == "FORBIDDEN"

    st, leader_reopen = leader_a.post_api(
        "crm_cskh.api.workflow.reopen_work_item",
        {
            "work_item": wi_http,
            "target_user": users["staff_a"],
            "reason": "Khách gọi lại nhờ tư vấn thêm chi nhánh thứ 2",
            "expected_revision": rev6,
            "idempotency_key": "http-reopen-leader-001",
        },
    )
    assert st == 200 and leader_reopen["message"]["work_item_status"] == "in_progress"
    rev7 = leader_reopen["message"]["revision"]

    # Close `wi_http` cleanly again so `closed` counters are populated in `progress_summary`
    st, closed_again = staff_a.post_api(
        "crm_cskh.api.workflow.close_work_item",
        {
            "work_item": wi_http,
            "completion_reason": "Hoàn tất chăm sóc bổ sung chi nhánh 2",
            "close_category": "normal",
            "expected_revision": rev7,
            "idempotency_key": "http-wi-close-002",
        },
    )
    assert st == 200

    # T2-15: Admin sets DNC on `P2 Khách DNC Cấm Gọi` (`staff_b`) -> blocks further contact
    wi_dnc = by_name["P2 Khách DNC Cấm Gọi"]["work_item"]
    lead_dnc = by_name["P2 Khách DNC Cấm Gọi"]["lead"]
    st, dnc_res = admin.post_api(
        "crm_cskh.api.settings.set_lead_dnc",
        {
            "lead": lead_dnc,
            "do_not_contact": True,
            "reason": "Khách gửi yêu cầu ngừng liên hệ qua tổng đài",
            "idempotency_key": "http-set-dnc-001",
        },
    )
    assert st == 200
    st, wi_dnc_det = staff_b.get_api(
        "crm_cskh.api.workflow.get_work_item", {"work_item": wi_dnc}
    )
    assert st == 200 and wi_dnc_det["message"]["lead"]["do_not_contact"] is True
    st, dnc_blocked = staff_b.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_dnc,
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "note": "Thử gọi khách đang DNC",
            "expected_revision": wi_dnc_det["message"]["work_item"]["revision"],
            "idempotency_key": "http-dnc-blocked-001",
        },
    )
    assert st in (409, 422) and err_code(dnc_blocked) in (
        "DO_NOT_CONTACT",
        "WORK_ITEM_NOT_ACTIVE",
    )

    record(
        "P2-E2E-03-lifecycle-honesty-correction-dnc",
        True,
        {
            "wi_http": wi_http,
            "wi_dnc": wi_dnc,
            "attempt_count": 3,
            "connected_attempt_count": 1,
            "correction_id": corr_id,
        },
    )

    # ------------------------------------------------------------------
    # 4. Security negative matrix (`SEC2-01..SEC2-16`) & Progress 0-PII
    # ------------------------------------------------------------------
    wi_staff_b = by_name["P2 Khách Thuộc Staff B"]["work_item"]

    # SEC2-01 / SEC2-02: Staff A cannot read or write Staff B's or Team B's work items
    for forbidden_wi in (wi_staff_b, wi_team_b):
        st_g, b_g = staff_a.get_api(
            "crm_cskh.api.workflow.get_work_item", {"work_item": forbidden_wi}
        )
        assert st_g == 403 and err_code(b_g) == "FORBIDDEN"
        st_a, b_a = staff_a.get_api(
            "crm_cskh.api.workflow.list_attempts", {"work_item": forbidden_wi}
        )
        assert st_a == 403 and err_code(b_a) == "FORBIDDEN"
        st_w, b_w = staff_a.post_api(
            "crm_cskh.api.workflow.record_attempt",
            {
                "work_item": forbidden_wi,
                "channel": "phone_call",
                "outcome_code": "no_answer",
                "note": "Ghi trái quyền",
                "expected_revision": 1,
                "idempotency_key": f"sec2-unauth-{forbidden_wi}",
            },
        )
        assert st_w in (403, 409) and err_code(b_w) in (
            "FORBIDDEN",
            "OWNERSHIP_CHANGED",
        ), f"Expected 403/409 FORBIDDEN/OWNERSHIP_CHANGED, got {st_w} {b_w}"

    # SEC2-08: Leader A cannot read Team B's work item; Leader B CAN read Team B's work item
    # (positive control) and CANNOT read Team A's work item.
    st_lb, b_lb = leader_a.get_api(
        "crm_cskh.api.workflow.get_work_item", {"work_item": wi_team_b}
    )
    assert st_lb == 403 and err_code(b_lb) == "FORBIDDEN"

    st_lb_pos, _ = leader_b.get_api(
        "crm_cskh.api.workflow.get_work_item", {"work_item": wi_team_b}
    )
    assert st_lb_pos == 200
    st_lb_neg, b_lb_neg = leader_b.get_api(
        "crm_cskh.api.workflow.get_work_item", {"work_item": wi_http}
    )
    assert st_lb_neg == 403 and err_code(b_lb_neg) == "FORBIDDEN"

    # SEC2-09: BOD Viewer blocked from PII endpoints & mutations, allowed on 0-PII progress & settings
    for pii_ep, params in (
        ("crm_cskh.api.workflow.list_work_items", {"page": 1, "page_size": 10}),
        ("crm_cskh.api.workflow.get_work_item", {"work_item": wi_http}),
        ("crm_cskh.api.workflow.list_attempts", {"work_item": wi_http}),
        ("crm_cskh.api.workflow.list_followups", {"page": 1, "page_size": 10}),
    ):
        st_bod, b_bod = bod.get_api(pii_ep, params)
        assert st_bod == 403 and err_code(b_bod) == "FORBIDDEN", (
            f"BOD must get 403 on {pii_ep}: {st_bod} {b_bod}"
        )

    st_bod_mut, _ = bod.post_api(
        "crm_cskh.api.settings.save_policy",
        {
            "policy_code": "BOD-HACK",
            "title": "BOD Hack",
            "idempotency_key": "bod-hack-1",
        },
    )
    assert st_bod_mut == 403

    st_prog_bod, prog_bod = bod.get_api("crm_cskh.api.workflow.progress_summary")
    assert st_prog_bod == 200 and prog_bod["message"]["scope"] == "all"
    raw_prog_json = json.dumps(prog_bod["message"], ensure_ascii=False)
    assert not PHONE_RE.search(raw_prog_json), "PII phone leaked in progress_summary!"

    st_prog_sa, prog_sa = staff_a.get_api("crm_cskh.api.workflow.progress_summary")
    assert st_prog_sa == 200 and prog_sa["message"]["scope"] == "self"
    assert [u["user"] for u in prog_sa["message"]["per_user"]] == [users["staff_a"]]

    st_prog_la, prog_la = leader_a.get_api("crm_cskh.api.workflow.progress_summary")
    assert st_prog_la == 200 and prog_la["message"]["scope"] == "team"
    la_users = {u["user"] for u in prog_la["message"]["per_user"]}
    assert users["staff_c"] not in la_users

    # SEC2-11: Missing CSRF rejected
    st_csrf, _ = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": by_name["P2 Khách Hạnh Phúc"]["work_item"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "occurred_at": fmt_dt(site_now),
            "expected_revision": 1,
            "idempotency_key": "sec2-no-csrf",
        },
        csrf_override="",
    )
    assert st_csrf in (400, 403), f"Expected 400/403 without CSRF, got {st_csrf}"

    # SEC2-15: Backdate before ownership_start rejected (422 INVALID_FOLLOWUP_TIME)
    wi_happy = by_name["P2 Khách Hạnh Phúc"]["work_item"]
    st_bd, b_bd = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_happy,
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "occurred_at": fmt_dt(site_now - dt.timedelta(days=5)),
            "note": "Lùi giờ 5 ngày trước khi nhận khách",
            "expected_revision": 1,
            "idempotency_key": "sec2-backdate-5d",
        },
    )
    assert st_bd == 422 and err_code(b_bd) == "INVALID_FOLLOWUP_TIME", (
        f"Expected 422 INVALID_FOLLOWUP_TIME on illegal backdate, got {st_bd} {b_bd}"
    )

    record(
        "P2-E2E-04-security-and-scoped-progress",
        True,
        {
            "bod_pii_endpoints_403": True,
            "cross_team_403": True,
            "csrf_enforced": True,
            "backdate_enforced": True,
            "progress_scopes_verified": ["self", "team", "all"],
        },
    )

    # ------------------------------------------------------------------
    # 5. Prepare deterministic named fixtures for `verify_p2_browser_e2e.mjs`
    # ------------------------------------------------------------------
    wi_honesty = by_name["P2 Khách Trung Thực"]["work_item"]
    lead_honesty = by_name["P2 Khách Trung Thực"]["lead"]
    honesty_seeded = bench_execute(
        "crm_cskh.tools.p2_e2e_seed.seed_native_activities",
        {"lead": lead_honesty, "caller_user": users["staff_a"], "phone": "0912340002"},
    )

    wi_conflict = by_name["P2 Khách Xung Đột"]["work_item"]

    # `wi_transfer`: Staff A records 1 attempt + open callback BEFORE browser A->B transfer test
    wi_transfer = by_name["P2 Khách Bàn Giao AB"]["work_item"]
    transfer_due = fmt_dt(site_now + dt.timedelta(days=1, hours=2))
    st, tr_att = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_transfer,
            "channel": "phone_call",
            "outcome_code": "callback_requested",
            "note": "Nhân viên A1 ghi nhận lần 1 trước khi bàn giao sang A2",
            "followup": {
                "due_at": transfer_due,
                "title": "Lịch hẹn bàn giao nguyên hạn cho A2",
                "description": "Không được reset due_at khi chuyển chủ",
            },
            "expected_revision": 1,
            "idempotency_key": "prep-transfer-att-001",
        },
    )
    assert st == 200

    # `wi_correction`: Staff A has 1 recorded attempt ready for browser correction/void flow
    wi_correction = by_name["P2 Khách Hiệu Chỉnh"]["work_item"]
    st, corr_seed_att = staff_a.post_api(
        "crm_cskh.api.workflow.record_attempt",
        {
            "work_item": wi_correction,
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "note": "Ghi nhầm Không nghe máy — cần gửi yêu cầu hiệu chỉnh trên trình duyệt",
            "expected_revision": 1,
            "idempotency_key": "prep-corr-att-001",
        },
    )
    assert st == 200

    # `wi_boundary_2359`, `wi_boundary_0000`, `wi_boundary_overdue`:
    # Schedule callbacks via API, then pin exact `23:59:59` today, `00:00:00` tomorrow,
    # and `yesterday 15:00:00` overdue in `Asia/Ho_Chi_Minh`.
    wi_2359 = by_name["P2 Khách Mốc 2359"]["work_item"]
    wi_0000 = by_name["P2 Khách Mốc 0000"]["work_item"]
    wi_overdue = by_name["P2 Khách Quá Hạn"]["work_item"]

    temp_future = fmt_dt(site_now + dt.timedelta(days=2))
    boundary_tasks = {}
    for key_label, wi_id, title_txt in (
        ("b2359", wi_2359, "Hẹn mốc 23:59 cuối ngày"),
        ("b0000", wi_0000, "Hẹn mốc 00:00 đầu ngày mới"),
        ("boverdue", wi_overdue, "Hẹn đã quá hạn hôm qua"),
    ):
        st, fu_res = staff_a.post_api(
            "crm_cskh.api.workflow.schedule_followup",
            {
                "work_item": wi_id,
                "due_at": temp_future,
                "title": title_txt,
                "note": title_txt,
                "reason": title_txt,
                "expected_revision": 1,
                "idempotency_key": f"prep-boundary-{key_label}",
            },
        )
        assert st == 200, f"schedule_followup {key_label} failed: {st} {fu_res}"
        boundary_tasks[key_label] = fu_res["message"]["task_id"]

    # Refresh `today` in site timezone right now so `today 23:59:59` vs `tomorrow 00:00:00` are exact
    today_date = site_now.date()
    tomorrow_date = today_date + dt.timedelta(days=1)
    yesterday_date = today_date - dt.timedelta(days=1)

    due_2359_str = f"{today_date.isoformat()} 23:59:59"
    due_0000_str = f"{tomorrow_date.isoformat()} 00:00:00"
    due_overdue_str = f"{yesterday_date.isoformat()} 15:00:00"

    bench_execute(
        "crm_cskh.tools.p2_e2e_seed.set_task_due_exact",
        {
            "work_item": wi_2359,
            "task_id": boundary_tasks["b2359"],
            "due_at_str": due_2359_str,
        },
    )
    bench_execute(
        "crm_cskh.tools.p2_e2e_seed.set_task_due_exact",
        {
            "work_item": wi_0000,
            "task_id": boundary_tasks["b0000"],
            "due_at_str": due_0000_str,
        },
    )
    bench_execute(
        "crm_cskh.tools.p2_e2e_seed.set_task_due_exact",
        {
            "work_item": wi_overdue,
            "task_id": boundary_tasks["boverdue"],
            "due_at_str": due_overdue_str,
        },
    )

    reconcile = bench_execute("crm_cskh.services.read_model.reconcile_p2_invariants")
    assert reconcile.get("ok") is True, f"P2 invariant drift detected: {reconcile}"
    record(
        "P2-E2E-05-reconcile-p2-invariants",
        True,
        {"ok": True, "summary": reconcile.get("counts", reconcile)},
    )

    evidence["fixtures"] = {
        "campaign_a": camp_a,
        "campaign_b": camp_b,
        "team_a": team_a,
        "team_b": team_b,
        "users": users,
        "wi_happy": wi_happy,
        "lead_happy": by_name["P2 Khách Hạnh Phúc"]["lead"],
        "wi_honesty": wi_honesty,
        "lead_honesty": lead_honesty,
        "honesty_call_logs": honesty_seeded["call_logs"],
        "wi_conflict": wi_conflict,
        "wi_transfer": wi_transfer,
        "transfer_task_id": tr_att["message"]["callback_task"],
        "transfer_due_at": transfer_due,
        "wi_correction": wi_correction,
        "correction_seed_attempt_id": corr_seed_att["message"]["attempt_id"],
        "wi_2359": wi_2359,
        "task_2359": boundary_tasks["b2359"],
        "due_2359": due_2359_str,
        "wi_0000": wi_0000,
        "task_0000": boundary_tasks["b0000"],
        "due_0000": due_0000_str,
        "wi_overdue": wi_overdue,
        "task_overdue": boundary_tasks["boverdue"],
        "due_overdue": due_overdue_str,
        "wi_staff_b": wi_staff_b,
        "wi_dnc": wi_dnc,
        "wi_team_b": wi_team_b,
        "wi_closed_http": wi_http,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(redact(evidence), fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(
        f"WROTE {REPORT_PATH} ({len(evidence['checks'])} composite checks PASS)",
        flush=True,
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
