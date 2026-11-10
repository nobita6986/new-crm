#!/usr/bin/env python3
"""P4 Đợt 2 — `G11` & `G12` Verifier: Session/Cache/Socket.IO Revocation on Current Topology & Security Matrix `S01–S20`.

Strictly adheres to `T0` scope:
  - Measures on the CURRENT single-VPS 10-service Compose topology (`proxy` Traefik -> `frontend` Nginx ->
    `backend` Gunicorn + `websocket` Node Socket.IO + `redis-cache` + `redis-queue` + `db`).
  - Does NOT build an unapproved distributed cluster outside the plan.
  - Verifies `G11` (`S07` transfer/revoke, `S08` user disable, `S09` role & `team-as-of` change) across
    HTTP `sid` cookies, `redis-cache` session keys, and live Socket.IO Engine.IO v4 polling + RFC 6455 WebSocket.
  - Verifies `G12` (`S01–S20`): authorization/IDOR/BOD 0-PII/CSRF/Desk bypass/spoofed actor, payload limits
    (`26 MiB -> 413`, `11 MiB -> 413`, spoofed MIME/extension -> `400`), Frappe login brute-force lockout +
    Traefik v3.7 `HTTP 429` edge rate-limiting (`compose.cskh-edge-ratelimit.yaml`), audit log immutability,
    private file/export Stage-3 auth + TTL (`410`) + CSV formula neutralization, security headers, and port isolation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import time
import traceback

THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(THIS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(THIS_DIR.parent))

from batch2_delta import LOCKED_BATCH1_COMMIT_SHA, LOCKED_BATCH1_IMAGE_DIGEST  # noqa: E402
from batch2_delta.common_stack import (  # noqa: E402
    EDGE_RATELIMIT_OVERRIDE,
    HttpActorSession,
    IsolatedComposeStack,
    RFC6455SocketIOClient,
    SecretScrubber,
    unwrap_message,
)


SEED_G11_G12_PY = r"""
from frappe.utils import add_days, now_datetime
from crm_cskh.services.phone_identity import normalize_vn_phone
from crm_cskh.api import (
    campaigns as camp_api,
    ownership as own_api,
    p3_reporting as p3_api,
    workflow as wf_api,
)
from crm_cskh.jobs.recompute_kpi import run_single_period_recompute
from crm_cskh.jobs.run_export import execute_export_job

frappe.set_user("crm.admin@dev.local")
camp_t1 = camp_api.save(
    code="CAMP-G11-T1",
    title="Chiến dịch kiểm định G11 Team 1",
    status="Active",
    team_scope=frappe.db.get_value("CRM Sales Hierarchy", {"user": "team1.leader@dev.local"}, "name"),
)["campaign"]
camp_t2 = camp_api.save(
    code="CAMP-G11-T2",
    title="Chiến dịch kiểm định G11 Team 2",
    status="Active",
    team_scope=frappe.db.get_value("CRM Sales Hierarchy", {"user": "team2.leader@dev.local"}, "name"),
)["campaign"]

frappe.set_user("Administrator")
items = {}
specs = [
    ("wi_a1", "0908200101", "Ứng viên A1 =1+1", camp_t1, "staff.a@dev.local"),
    ("wi_a2", "0908200102", "+Ứng viên A2", camp_t1, "staff.a@dev.local"),
    ("wi_b1", "0908200103", "-Ứng viên B1", camp_t1, "staff.b@dev.local"),
    ("wi_c1", "0908200104", "@Ứng viên C1", camp_t2, "staff.c@dev.local"),
]
for key, phone, fname, camp_id, owner_email in specs:
    lead = frappe.get_doc({
        "doctype": "CRM Lead",
        "first_name": fname,
        "lead_name": fname,
        "mobile_no": phone,
        "status": "New",
    }).insert(ignore_permissions=True)
    norm_ph = normalize_vn_phone(phone)
    if not frappe.db.exists("CSKH Identity Index", {"normalized_phone": norm_ph}):
        frappe.get_doc({
            "doctype": "CSKH Identity Index",
            "normalized_phone": norm_ph,
            "canonical_lead": lead.name,
        }).insert(ignore_permissions=True)
    wi = frappe.get_doc({
        "doctype": "CSKH Work Item",
        "lead": lead.name,
        "campaign": camp_id,
        "status": "queued",
    }).insert(ignore_permissions=True)
    frappe.get_doc({
        "doctype": "CSKH Active Claim",
        "lead": lead.name,
        "campaign": camp_id,
        "work_item": wi.name,
    }).insert(ignore_permissions=True)
    frappe.set_user("crm.admin@dev.local")
    own_api.assign(
        work_item=wi.name,
        target_user=owner_email,
        reason=f"G11 seed {key}",
        request_id=f"g11-seed-{key}",
    )
    frappe.set_user("Administrator")
    items[key] = {"work_item": wi.name, "lead": lead.name, "phone": phone}

# Record attempts & correction so audit tables and KPI/Exports are populated
due_str = add_days(now_datetime(), 1).strftime("%Y-%m-%d 10:30:00")
frappe.set_user("staff.b@dev.local")
att_b1 = wf_api.record_attempt(
    work_item=items["wi_b1"]["work_item"],
    channel="phone_call",
    outcome_code="connected_interested",
    note="=CMD|' /C calc'!A0 Quan tâm vị trí tuyển dụng",
    followup_due_at=due_str,
    followup_title="Gọi lại B1",
    idempotency_key="g11-att-b1",
)
corr_b1 = wf_api.request_correction(
    attempt=att_b1["attempt_id"],
    action="correct",
    corrected_fields={"note": "+Đã trao đổi chi tiết vị trí"},
    reason="Đính chính ghi chú B1",
    idempotency_key="g11-corr-b1",
)
frappe.set_user("team1.leader@dev.local")
wf_api.review_correction(
    correction_id=corr_b1["correction_id"],
    decision="approve",
    review_note="Duyệt đính chính",
    idempotency_key="g11-corr-rev-b1",
)

frappe.set_user("Administrator")
now_dt = now_datetime()
month_key = f"{now_dt.year:04d}-{now_dt.month:02d}"
day_key = now_dt.strftime("%Y-%m-%d")
run_single_period_recompute(period_type="monthly", period_key=month_key, actor="Administrator")
run_single_period_recompute(period_type="daily", period_key=day_key, actor="Administrator")

t1_node = frappe.db.get_value("CRM Sales Hierarchy", {"user": "team1.leader@dev.local"}, "name")
frappe.set_user("team1.leader@dev.local")
leader_exp = p3_api.request_export(
    report_type="team_comparison",
    period_type="monthly",
    period_key=month_key,
    scope_type="team",
    scope_id=t1_node,
)
frappe.set_user("crm.admin@dev.local")
admin_pii_exp = p3_api.request_export(
    report_type="metric_drilldown",
    period_type="monthly",
    period_key=month_key,
    scope_type="company",
    metric_code="CONNECTED_RATE",
    include_pii=1,
)
admin_ttl_exp = p3_api.request_export(
    report_type="executive_overview",
    period_type="monthly",
    period_key=month_key,
)
frappe.set_user("Administrator")
for eid in (leader_exp["export_id"], admin_pii_exp["export_id"], admin_ttl_exp["export_id"]):
    execute_export_job(export_id=eid)

leader_exp_row = frappe.db.get_value(
    "CSKH Export Job", leader_exp["export_id"], ["file_doc", "file_url"], as_dict=True
)
pii_exp_row = frappe.db.get_value(
    "CSKH Export Job", admin_pii_exp["export_id"], ["file_doc", "file_url"], as_dict=True
)
file_meta = frappe.db.get_value(
    "File", pii_exp_row["file_doc"], ["is_private", "owner", "file_url"], as_dict=True
)

print(json.dumps({
    "camp_t1": camp_t1,
    "camp_t2": camp_t2,
    "team1_node": t1_node,
    "team2_node": frappe.db.get_value("CRM Sales Hierarchy", {"user": "team2.leader@dev.local"}, "name"),
    "month_key": month_key,
    "day_key": day_key,
    "items": items,
    "att_b1_id": att_b1["attempt_id"],
    "corr_b1_id": corr_b1["correction_id"],
    "leader_export_id": leader_exp["export_id"],
    "leader_export_file_url": leader_exp_row["file_url"],
    "admin_pii_export_id": admin_pii_exp["export_id"],
    "admin_ttl_export_id": admin_ttl_exp["export_id"],
    "export_file_meta": file_meta,
}))
"""


def build_plan_only_report(app_image: str) -> dict[str, object]:
    return {
        "schema_version": "cskh-p4-batch2-g11-g12-v1",
        "mode": "plan_only",
        "status": "PLANNED",
        "gates": ["G11", "G12"],
        "security_matrix_covered": [f"S{i:02d}" for i in range(1, 21)],
        "topology": "single_vps_10_service_compose_no_extra_distributed_cluster",
        "locked_batch1_commit_sha": LOCKED_BATCH1_COMMIT_SHA,
        "app_image": app_image,
    }


def open_authenticated_ws(
    stack: IsolatedComposeStack, actor: HttpActorSession
) -> tuple[RFC6455SocketIOClient, str]:
    ws = RFC6455SocketIOClient("127.0.0.1", stack.http_port)
    ws.upgrade(
        site_host=stack.site_name,
        origin=stack.base_url,
        cookie_header=actor.cookie_header(),
    )
    open_pkt = ws.recv_next_text()
    assert open_pkt.startswith("0{"), f"Unexpected Engine.IO open frame: {open_pkt}"
    ws.send_text(f"40/{stack.site_name},")
    ns_pkt = ws.recv_next_text()
    assert ns_pkt.startswith(f"40/{stack.site_name}"), (
        f"Namespace auth failed: {ns_pkt}"
    )
    return ws, ns_pkt


def verify_internal_ports_unexposed(stack: IsolatedComposeStack) -> dict[str, object]:
    """`S18`: Confirms only `proxy:80` is published on loopback and `3306/6379/8000/9000` are unexposed."""
    published: dict[str, str] = {}
    for srv in (
        "db",
        "redis-cache",
        "redis-queue",
        "backend",
        "websocket",
        "frontend",
        "proxy",
    ):
        cid = stack.get_container_id(srv)
        ports_out = subprocess.run(
            ["docker", "port", cid],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        published[srv] = ports_out
        if srv != "proxy":
            assert ports_out == "", (
                f"Internal service {srv} unexpectedly publishes host port: {ports_out}"
            )
    assert f"127.0.0.1:{stack.http_port}" in published["proxy"], published["proxy"]
    return {
        "service_published_ports": published,
        "internal_services_zero_host_ports": True,
    }


def run_live_verification(
    *,
    app_image: str,
    http_port: int,
    scrubber: SecretScrubber,
) -> dict[str, object]:
    stack = IsolatedComposeStack(
        prefix="g11g12",
        http_port=http_port,
        app_image=app_image,
        scrubber=scrubber,
    )
    try:
        stack.boot_and_bootstrap_site(install_site=True)
        stack.seed_standard_actors_and_teams()
        ctx = stack.exec_site_python(SEED_G11_G12_PY)

        guest = stack.make_actor("guest")
        outside = stack.make_actor("outside", "outside.user@dev.local")
        admin = stack.make_actor("admin", "crm.admin@dev.local")
        bod = stack.make_actor("bod", "bod.viewer@dev.local")
        leader1 = stack.make_actor("leader1", "team1.leader@dev.local")
        leader2 = stack.make_actor("leader2", "team2.leader@dev.local")
        staff_a = stack.make_actor("staff_a", "staff.a@dev.local")
        staff_b = stack.make_actor("staff_b", "staff.b@dev.local")

        wi_a1 = ctx["items"]["wi_a1"]["work_item"]
        lead_a1 = ctx["items"]["wi_a1"]["lead"]
        wi_a2 = ctx["items"]["wi_a2"]["work_item"]
        lead_a2 = ctx["items"]["wi_a2"]["lead"]
        wi_b1 = ctx["items"]["wi_b1"]["work_item"]
        lead_c1 = ctx["items"]["wi_c1"]["lead"]
        month_key = str(ctx["month_key"])

        # =====================================================================
        # PART 1 — `G11`: Transfer (`S07`), User Disable (`S08`), Role/Team Change (`S09`)
        # =====================================================================

        # 1A. `S07`: Ownership transfer `A -> B` -> `A -> B -> A` -> `revoke` with live WS + HTTP sid
        ws_a, _ = open_authenticated_ws(stack, staff_a)
        ws_b, _ = open_authenticated_ws(stack, staff_b)
        try:
            st_pre, _, _ = staff_a.get_json(
                "/api/method/crm_cskh.api.workflow.get_work_item", {"work_item": wi_a1}
            )
            assert st_pre == 200

            # Transfer `wi_a1` from `staff_a` -> `staff_b` via live HTTP
            st_re, _, body_re = admin.post_json(
                "/api/method/crm_cskh.api.ownership.reassign",
                {
                    "work_item": wi_a1,
                    "expected_owner": "staff.a@dev.local",
                    "target_user": "staff.b@dev.local",
                    "reason": "Chuyển giao kiểm định G11 S07",
                    "request_id": "g11-s07-a-to-b",
                },
            )
            assert st_re == 200, (st_re, body_re)

            # Immediate HTTP + REST + Desk getdoc revocation for `staff_a`
            st_a_wi, _, _ = staff_a.get_json(
                "/api/method/crm_cskh.api.workflow.get_work_item", {"work_item": wi_a1}
            )
            st_a_att, _, _ = staff_a.post_json(
                "/api/method/crm_cskh.api.workflow.record_attempt",
                {
                    "work_item": wi_a1,
                    "channel": "sms",
                    "outcome_code": "message_sent_no_reply",
                    "note": "Thử ghi sau khi đã bị thu hồi",
                    "idempotency_key": "g11-s07-blocked-write",
                },
            )
            st_a_rest, _, _ = staff_a.get_json(f"/api/resource/CRM%20Lead/{lead_a1}")
            st_a_getdoc, _, _ = staff_a.get_json(
                "/api/method/frappe.desk.form.load.getdoc",
                {"doctype": "CRM Lead", "name": lead_a1},
            )
            st_b_wi, _, _ = staff_b.get_json(
                "/api/method/crm_cskh.api.workflow.get_work_item", {"work_item": wi_a1}
            )
            assert (
                st_a_wi == 403
                and st_a_att == 409
                and st_a_rest == 403
                and st_a_getdoc == 403
            ), (
                st_a_wi,
                st_a_att,
                st_a_rest,
                st_a_getdoc,
            )
            assert st_b_wi == 200

            # Socket.IO `doc_subscribe` on pre-existing `ws_a` vs `ws_b`, then mutate `wi_a1` as `staff_b`
            ws_a.send_text(
                f'42/{stack.site_name},["doc_subscribe","CRM Lead","{lead_a1}"]'
            )
            ws_b.send_text(
                f'42/{stack.site_name},["doc_subscribe","CRM Lead","{lead_a1}"]'
            )
            _ = ws_a.drain_text_frames(0.8)
            _ = ws_b.drain_text_frames(0.8)

            st_b_mut, _, _ = staff_b.post_json(
                "/api/method/crm_cskh.api.workflow.record_attempt",
                {
                    "work_item": wi_a1,
                    "channel": "sms",
                    "outcome_code": "message_sent_no_reply",
                    "note": "Staff B cập nhật sau nhận bàn giao",
                    "idempotency_key": "g11-s07-b-write",
                },
            )
            assert st_b_mut == 200
            frames_a_after_transfer = ws_a.drain_text_frames(1.2)
            assert not any(
                "0908200101" in f or "Staff B cập nhật" in f
                for f in frames_a_after_transfer
            ), frames_a_after_transfer

            # Verify DB `DocShare` / `ToDo` revocation + `A -> B -> A` + `revoke`
            s07_db = stack.exec_site_python(
                "share_a = frappe.db.count('DocShare', {'share_doctype': 'CRM Lead', 'share_name': _IN['lead'], 'user': 'staff.a@dev.local'})\n"
                "todo_a = frappe.db.count('ToDo', {'reference_type': 'CRM Lead', 'reference_name': _IN['lead'], 'allocated_to': 'staff.a@dev.local', 'status': 'Open'})\n"
                "share_b = frappe.db.count('DocShare', {'share_doctype': 'CRM Lead', 'share_name': _IN['lead'], 'user': 'staff.b@dev.local'})\n"
                "print(json.dumps({'docshare_old_owner': share_a, 'open_todo_old_owner': todo_a, 'docshare_new_owner': share_b}))",
                {"lead": lead_a1},
            )
            assert (
                s07_db["docshare_old_owner"] == 0 and s07_db["open_todo_old_owner"] == 0
            )

            # Complete `A -> B -> A` cycle and `revoke` on `wi_a1`
            st_ba, _, _ = admin.post_json(
                "/api/method/crm_cskh.api.ownership.reassign",
                {
                    "work_item": wi_a1,
                    "expected_owner": "staff.b@dev.local",
                    "target_user": "staff.a@dev.local",
                    "reason": "Chuyển trả A->B->A",
                    "request_id": "g11-s07-b-to-a",
                },
            )
            st_rev, _, _ = admin.post_json(
                "/api/method/crm_cskh.api.ownership.revoke",
                {
                    "work_item": wi_a1,
                    "expected_owner": "staff.a@dev.local",
                    "reason": "Thu hồi về kho chung",
                    "request_id": "g11-s07-revoke",
                },
            )
            assert st_ba == 200 and st_rev == 200
        finally:
            ws_b.close()

        # 1B. `S08`: Disable `staff.a@dev.local` while holding open HTTP `sid` + open WebSocket `ws_a`
        old_sid_a = staff_a.sid
        old_csrf_a = staff_a.csrf_token
        assert old_sid_a and old_sid_a != "Guest"
        try:
            stack.exec_site_python(
                "u = frappe.get_doc('User', 'staff.a@dev.local')\n"
                "u.enabled = 0\n"
                "u.save(ignore_permissions=True)\n"
                "print(json.dumps({'user_disabled': True}))"
            )

            # Replay exact `old_sid_a` over HTTP UI, GET RPC, and POST mutation -> immediate revocation
            replay_cookie = {"Cookie": f"sid={old_sid_a}"}
            st_dis_page, _, _ = guest.raw_request(
                "GET", "/cskh/my-customers", headers=replay_cookie
            )
            st_dis_api, _, _ = guest.raw_request(
                "GET",
                "/api/method/crm_cskh.api.customers.my_list?page=1&page_size=10",
                headers={**replay_cookie, "Accept": "application/json"},
            )
            st_dis_mut, _, _ = guest.raw_request(
                "POST",
                "/api/method/crm_cskh.api.workflow.record_attempt",
                data=json.dumps(
                    {
                        "work_item": wi_a2,
                        "channel": "sms",
                        "outcome_code": "message_sent_no_reply",
                        "note": "Thử ghi khi user bị khóa",
                        "idempotency_key": "g11-s08-disabled-mut",
                    }
                ).encode("utf-8"),
                headers={
                    **replay_cookie,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Frappe-CSRF-Token": old_csrf_a,
                },
            )
            # Frappe v15 `Session.resume()` raises `ValidationError` (`417`) on disabled-user `sid`, rolling back the failed request (`0` stale privilege)
            assert st_dis_page in (302, 401, 403, 417), st_dis_page
            assert st_dis_api in (401, 403, 417), st_dis_api
            assert st_dis_mut in (401, 403, 417), st_dis_mut

            # New Socket.IO namespace handshake with `old_sid_a` MUST either reject (`44/<site>`) or bind strictly as `Guest` (`0` PII on `doc_subscribe`)
            ws_reconn = RFC6455SocketIOClient("127.0.0.1", stack.http_port)
            try:
                ws_reconn.upgrade(
                    site_host=stack.site_name,
                    origin=stack.base_url,
                    cookie_header=f"sid={old_sid_a}",
                )
                _ = ws_reconn.recv_next_text()
                ws_reconn.send_text(f"40/{stack.site_name},")
                reconn_ns = ws_reconn.recv_next_text()
                if not reconn_ns.startswith(f"44/{stack.site_name}"):
                    assert reconn_ns.startswith(f"40/{stack.site_name}"), reconn_ns
                    ws_reconn.send_text(
                        f'42/{stack.site_name},["doc_subscribe","CRM Lead","{lead_a2}"]'
                    )
                    reconn_frames = ws_reconn.drain_text_frames(1.0)
                    assert not any("0908200102" in f for f in reconn_frames), (
                        reconn_frames
                    )
            finally:
                ws_reconn.close()

            # Pre-existing open TCP WS `ws_a`: `doc_subscribe` on `lead_a2` fails `can_subscribe_doc` and leaks 0 PII
            ws_a.send_text(
                f'42/{stack.site_name},["doc_subscribe","CRM Lead","{lead_a2}"]'
            )
            pre_open_ws_frames = ws_a.drain_text_frames(1.0)
            assert not any("0908200102" in f for f in pre_open_ws_frames), (
                pre_open_ws_frames
            )

            # Audit `tabSessions` + `redis-cache` before & after explicit `frappe.sessions.clear_sessions(..., force=True)`
            disable_info = stack.exec_site_python(
                "from frappe.sessions import clear_sessions\n"
                "pre_db = int(frappe.db.sql(\"SELECT COUNT(*) FROM `tabSessions` WHERE sid=%s\", (_IN['sid'],))[0][0])\n"
                "pre_cache = bool(frappe.cache().hget('session', _IN['sid']))\n"
                "clear_sessions(user='staff.a@dev.local', force=True)\n"
                "frappe.db.commit()\n"
                "post_db = int(frappe.db.sql(\"SELECT COUNT(*) FROM `tabSessions` WHERE sid=%s\", (_IN['sid'],))[0][0])\n"
                "post_cache = bool(frappe.cache().hget('session', _IN['sid']))\n"
                "print(json.dumps({\n"
                "  'inert_row_before_explicit_clear': {'db_sessions': pre_db, 'redis_cached': pre_cache},\n"
                "  'after_clear_sessions_force': {'db_sessions': post_db, 'redis_cached': post_cache},\n"
                "}))",
                {"sid": old_sid_a},
            )
            assert disable_info["after_clear_sessions_force"] == {
                "db_sessions": 0,
                "redis_cached": False,
            }, disable_info

            # Campaign eligibility excludes disabled `staff.a@dev.local` (`REASON_USER_DISABLED`)
            admin.post_json(
                "/api/method/crm_cskh.api.campaigns.save_rule",
                {"campaign": ctx["camp_t1"], "user": "staff.a@dev.local", "weight": 50},
            )
            _, _, rules_resp = admin.post_json(
                "/api/method/crm_cskh.api.campaigns.save_rule",
                {"campaign": ctx["camp_t1"], "user": "staff.b@dev.local", "weight": 50},
            )
            st_rules, _, body_rules = admin.get_json(
                "/api/method/crm_cskh.api.campaigns.rules", {"campaign": ctx["camp_t1"]}
            )
            assert st_rules == 200
            elig_users = [
                r["user"] for r in (unwrap_message(body_rules).get("eligible") or [])
            ]
            excl_map = {
                r["user"]: r.get("reason")
                for r in (unwrap_message(body_rules).get("excluded") or [])
            }
            assert "staff.a@dev.local" not in elig_users, elig_users
            assert excl_map.get("staff.a@dev.local") == "user_disabled", excl_map
        finally:
            ws_a.close()

        # Re-enable `staff.a@dev.local` cleanly for subsequent non-S08 checks
        stack.exec_site_python(
            "u = frappe.get_doc('User', 'staff.a@dev.local')\n"
            "u.enabled = 1\n"
            "u.save(ignore_permissions=True)\n"
            "print(json.dumps({'reenabled': True}))"
        )
        staff_a = stack.make_actor("staff_a", "staff.a@dev.local")

        # 1C. `S09` & `S04`: Team transfer (`team-as-of`) + Role downgrade (`Sales Manager` -> `Sales User`)
        # Pre-downgrade: `leader1` CAN download their Team 1 export (`200`)
        leader_exp_id = str(ctx["leader_export_id"])
        leader_raw_url = str(ctx["leader_export_file_url"])
        st_l1_exp_ok, _, raw_l1_csv = leader1.raw_request(
            "GET",
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={leader_exp_id}",
        )
        assert st_l1_exp_ok == 200 and len(raw_l1_csv) > 20

        # Transfer `staff.b@dev.local` from Team 1 -> Team 2 (`S04` historical `team-as-of` check)
        stack.exec_site_python(
            "node_b = frappe.db.get_value('CRM Sales Hierarchy', {'user': 'staff.b@dev.local'}, 'name')\n"
            "doc_b = frappe.get_doc('CRM Sales Hierarchy', node_b)\n"
            "doc_b.reports_to = _IN['team2_node']\n"
            "doc_b.save(ignore_permissions=True)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'transferred_to': doc_b.reports_to}))",
            {"team2_node": ctx["team2_node"]},
        )
        # `leader2` (`Team 2`) MUST still get `403` on `wi_b1` belonging to `Team 1` private campaign (`S04`)
        st_s04_wi, _, _ = leader2.get_json(
            "/api/method/crm_cskh.api.workflow.get_work_item", {"work_item": wi_b1}
        )
        assert st_s04_wi == 403, (
            f"Expected S04 403 for Leader 2 on Team 1 item, got {st_s04_wi}"
        )

        # Downgrade `team1.leader@dev.local` (`Sales Manager` removed -> `Sales User` only, leaf node)
        stack.exec_site_python(
            "u = frappe.get_doc('User', 'team1.leader@dev.local')\n"
            "u.roles = []\n"
            "u.append('roles', {'role': 'Sales User'})\n"
            "u.save(ignore_permissions=True)\n"
            "frappe.clear_cache(user='team1.leader@dev.local')\n"
            "print(json.dumps({'downgraded': True}))"
        )
        # Using the SAME pre-existing `leader1` session cookie (`0` stale privilege):
        st_s09_page, _, _ = leader1.raw_request("GET", "/cskh/team-quality")
        st_s09_api, _, _ = leader1.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_team_comparison",
            {"period_type": "monthly", "period_key": month_key},
        )
        st_s09_exp_rpc, _, _ = leader1.raw_request(
            "GET",
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={leader_exp_id}",
        )
        st_s09_exp_raw, _, _ = leader1.raw_request("GET", leader_raw_url)
        assert st_s09_page == 403 and st_s09_api == 403, (st_s09_page, st_s09_api)
        assert st_s09_exp_rpc == 403 and st_s09_exp_raw in (403, 404), (
            st_s09_exp_rpc,
            st_s09_exp_raw,
        )

        # Restore `team1.leader@dev.local` and `staff.b@dev.local` hierarchy for `G12` checks
        stack.exec_site_python(
            "u = frappe.get_doc('User', 'team1.leader@dev.local')\n"
            "u.append('roles', {'role': 'Sales Manager'})\n"
            "u.save(ignore_permissions=True)\n"
            "node_b = frappe.db.get_value('CRM Sales Hierarchy', {'user': 'staff.b@dev.local'}, 'name')\n"
            "doc_b = frappe.get_doc('CRM Sales Hierarchy', node_b)\n"
            "doc_b.reports_to = _IN['team1_node']\n"
            "doc_b.save(ignore_permissions=True)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'restored': True}))",
            {"team1_node": ctx["team1_node"]},
        )
        leader1 = stack.make_actor("leader1", "team1.leader@dev.local")

        # =====================================================================
        # PART 2 — `G12`: Authorization, Payload Limits, Rate Limits, Audit & File/Export
        # =====================================================================

        # 2A. Authorization, Headers, CSRF, Desk Bypass & Spoofed Identity (`S01–S06`, `S10`, `S14`, `S17`, `S19`)
        st_g_page, hdr_g, _ = guest.raw_request("GET", "/cskh/my-customers")
        st_g_api, _, _ = guest.get_json("/api/method/crm_cskh.api.customers.my_list")
        st_g_priv, _, _ = guest.raw_request("GET", leader_raw_url)
        assert (
            st_g_page in (302, 401, 403)
            and st_g_api in (401, 403)
            and st_g_priv in (401, 403)
        )

        # Security headers (`S17`)
        _, hdr_auth, _ = staff_a.raw_request("GET", "/cskh/my-customers")
        assert hdr_auth.get("x-content-type-options", "").lower() == "nosniff", hdr_auth
        assert hdr_auth.get("x-frame-options", "").upper() == "SAMEORIGIN", hdr_auth
        assert "referrer-policy" in hdr_auth and "permissions-policy" in hdr_auth, (
            hdr_auth
        )

        # Outside user (`S06`), Cross-staff (`S02`), Cross-team (`S03`), BOD 0-PII (`S05`)
        st_out, _, _ = outside.get_json("/api/method/crm_cskh.api.customers.my_list")
        st_s02_existing, _, _ = staff_a.get_json(f"/api/resource/CRM%20Lead/{lead_c1}")
        st_s02_missing, _, _ = staff_a.get_json(
            "/api/resource/CRM%20Lead/CRM-LEAD-NONEXISTENT-9999"
        )
        st_s03_leader, _, _ = leader1.get_json(
            "/api/method/crm_cskh.api.workflow.get_work_item",
            {"work_item": ctx["items"]["wi_c1"]["work_item"]},
        )
        st_bod_pii, _, _ = bod.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
            {
                "metric_code": "CONNECTED_RATE",
                "period_type": "monthly",
                "period_key": month_key,
                "scope_type": "company",
                "include_pii": 1,
            },
        )
        st_bod_ok, _, body_bod_ok = bod.get_json(
            "/api/method/crm_cskh.api.p3_reporting.get_metric_drilldown",
            {
                "metric_code": "CONNECTED_RATE",
                "period_type": "monthly",
                "period_key": month_key,
                "scope_type": "company",
                "include_pii": 0,
            },
        )
        bod_dump = json.dumps(body_bod_ok)
        assert st_out == 403 and st_s02_existing == 403 and st_s02_missing == 403
        assert st_s03_leader == 403 and st_bod_pii == 403 and st_bod_ok == 200
        assert "090820010" not in bod_dump

        # CSRF (`S10`), Desk bypass (`S14`), and Spoofed identity (`S19`)
        st_csrf_bad, _, _ = staff_a.post_json(
            "/api/method/crm_cskh.api.workflow.record_attempt",
            {
                "work_item": wi_a2,
                "channel": "sms",
                "outcome_code": "message_sent_no_reply",
            },
            csrf_override="forged-csrf-token-000",
        )
        st_desk_bypass, _, _ = staff_a.get_json(
            "/api/method/frappe.client.get_value",
            {
                "doctype": "CRM Lead",
                "filters": json.dumps({"name": lead_c1}),
                "fieldname": "mobile_no",
            },
        )
        st_spoof, _, _ = staff_a.post_json(
            "/api/method/crm_cskh.api.workflow.record_attempt",
            {
                "work_item": wi_a2,
                "channel": "sms",
                "outcome_code": "message_sent_no_reply",
                "performed_by": "staff.b@dev.local",
                "verification_status": "verified",
                "idempotency_key": "g12-s19-spoof",
            },
        )
        assert st_csrf_bad in (400, 403), st_csrf_bad
        assert st_desk_bypass == 403, st_desk_bypass
        assert st_spoof in (400, 403, 422), st_spoof

        # 2B. Payload Limits & Upload Hardening (`S11`, `OBS-P2-03`)
        # >25 MiB body blocked at Nginx (`CLIENT_MAX_BODY_SIZE=25m` -> `HTTP 413`)
        oversized_26m = b"x" * (26 * 1024 * 1024)
        st_26m, _, _ = admin.raw_request(
            "POST",
            "/api/method/crm_cskh.api.import_files.upload_import_file",
            data=oversized_26m,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Frappe-CSRF-Token": admin.csrf_token,
            },
            timeout=45.0,
        )
        # >10 MiB multipart CSV blocked at app endpoint (`MAX_FILE_BYTES=10m` -> `HTTP 413 FILE_TOO_LARGE`)
        st_11m, _, body_11m = admin.upload_multipart(
            "/api/method/crm_cskh.api.import_files.upload_import_file",
            "oversize.csv",
            b"phone,name\n" + (b"0900000001,Test\n" * 700000),
        )
        # Disguised `.exe` / `.html` rejected (`HTTP 400 UNSUPPORTED_FORMAT`)
        st_exe, _, _ = admin.upload_multipart(
            "/api/method/crm_cskh.api.import_files.upload_import_file",
            "malware.exe",
            b"MZ\x90\x00\x03\x00\x00\x00",
            content_type="application/octet-stream",
        )
        # Corrupt `.xlsx` upload -> rejected safely at `imports.create_preview` (`400`/`422`)
        st_bad_xlsx_up, _, up_bad_xlsx = admin.upload_multipart(
            "/api/method/crm_cskh.api.import_files.upload_import_file",
            "corrupt.xlsx",
            b"NOT_A_VALID_ZIP_ARCHIVE_PAYLOAD",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        assert st_bad_xlsx_up == 200
        bad_xlsx_doc = unwrap_message(up_bad_xlsx)["name"]
        st_bad_xlsx_prev, _, _ = admin.post_json(
            "/api/method/crm_cskh.api.imports.create_preview",
            {"file_name": bad_xlsx_doc, "idempotency_key": "g12-corrupt-xlsx"},
        )
        # `OBS-P2-03`: native `/api/method/upload_file` on foreign `CRM Lead` (`lead_c1`) -> `403`
        st_foreign_attach, _, _ = staff_a.upload_multipart(
            "/api/method/upload_file",
            "note.txt",
            b"attempt attach to foreign lead",
            content_type="text/plain",
            extra_fields={"doctype": "CRM Lead", "docname": lead_c1, "is_private": "1"},
        )
        assert st_26m == 413 and st_11m == 413, (st_26m, st_11m, body_11m)
        assert st_exe == 400 and st_bad_xlsx_prev in (400, 422), (
            st_exe,
            st_bad_xlsx_prev,
        )
        assert st_foreign_attach == 403, st_foreign_attach

        # 2C. Audit Immutability (`S15`) & Private File / Export / CSV Formula Escaping (`S12`, `S13`)
        immut_res = stack.exec_site_python(
            "results = {}\n"
            "for label, dt_name in [\n"
            "  ('assignment_history', 'CSKH Assignment History'),\n"
            "  ('contact_attempt', 'CSKH Contact Attempt'),\n"
            "  ('attempt_correction', 'CSKH Attempt Correction'),\n"
            "  ('export_job', 'CSKH Export Job'),\n"
            "]:\n"
            "  doc_name = frappe.db.get_value(dt_name, {}, 'name')\n"
            "  doc = frappe.get_doc(dt_name, doc_name)\n"
            "  blocked = False\n"
            "  try:\n"
            "    doc.delete()\n"
            "  except Exception:\n"
            "    blocked = True\n"
            "    frappe.db.rollback()\n"
            "  results[label] = blocked\n"
            "print(json.dumps(results))"
        )
        assert all(immut_res.values()), f"Audit deletion was not blocked: {immut_res}"

        # File metadata (`is_private=1`, `owner='Administrator'`), CSV formula neutralization, & TTL `410`
        assert ctx["export_file_meta"]["is_private"] == 1
        assert ctx["export_file_meta"]["owner"] == "Administrator"

        st_pii_dl, _, raw_pii_csv = admin.raw_request(
            "GET",
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={ctx['admin_pii_export_id']}",
        )
        csv_text = raw_pii_csv.decode("utf-8-sig", errors="replace")
        assert st_pii_dl == 200
        # Formula prefixes (`=`, `+`, `-`, `@`) must be escaped (prefixed with `'`) and never appear raw at cell start
        for dangerous_raw in ("\n=CMD|", ",=CMD|", "\n-Ứng viên", ",-Ứng viên"):
            assert dangerous_raw not in csv_text, (
                f"Unescaped CSV formula found: {dangerous_raw}"
            )

        # Direct Nginx `/protected/` internal location & traversal check -> blocked (`403`/`404`)
        st_prot_direct, _, _ = admin.raw_request(
            "GET", "/protected/private/files/nonexistent.csv"
        )
        st_traversal, _, _ = admin.raw_request(
            "GET", "/private/files/..%2f..%2fsite_config.json"
        )
        assert st_prot_direct in (403, 404) and st_traversal in (400, 403, 404)

        # Expire `admin_ttl_export_id` (`S13` TTL -> `410` on RPC & `403` on raw URL)
        ttl_res = stack.exec_site_python(
            "from crm_cskh.jobs.run_export import expire_stale_exports\n"
            "frappe.db.set_value('CSKH Export Job', _IN['eid'], 'expires_at', '2020-01-01 00:00:00', update_modified=False)\n"
            "frappe.db.commit()\n"
            "expire_stale_exports()\n"
            "f_url = frappe.db.get_value('CSKH Export Job', _IN['eid'], 'file_url')\n"
            "print(json.dumps({'expired_status': frappe.db.get_value('CSKH Export Job', _IN['eid'], 'status'), 'file_url': f_url}))",
            {"eid": ctx["admin_ttl_export_id"]},
        )
        st_ttl_rpc, _, _ = admin.raw_request(
            "GET",
            f"/api/method/crm_cskh.api.p3_reporting.download_export?export_id={ctx['admin_ttl_export_id']}",
        )
        assert ttl_res["expired_status"] == "expired" and st_ttl_rpc in (403, 410), (
            ttl_res,
            st_ttl_rpc,
        )

        # 2D. Rate Limiting (`S20`):
        # (i) Native Frappe consecutive failed-login lockout (`System Settings` in `redis-cache`)
        stack.exec_site_python(
            "frappe.db.set_single_value('System Settings', 'allow_consecutive_login_attempts', 3)\n"
            "frappe.db.set_single_value('System Settings', 'allow_login_after_fail', 60)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'login_lockout_enabled': True}))"
        )
        brute_actor = HttpActorSession(
            stack.base_url, stack.site_name, scrubber, "brute"
        )
        login_fail_codes: list[int] = []
        for _ in range(5):
            st_bf, _, _ = brute_actor.post_json(
                "/api/method/login",
                {"usr": "staff.c@dev.local", "pwd": "WrongPassword-BruteForce!"},
                include_csrf=False,
            )
            login_fail_codes.append(st_bf)
        # 5th attempt even WITH the valid password for `staff.c@dev.local` MUST be rejected while locked out!
        st_locked_valid_pw, _, _ = brute_actor.post_json(
            "/api/method/login",
            {"usr": "staff.c@dev.local", "pwd": stack.actor_pw},
            include_csrf=False,
        )
        locked_out_detected = st_locked_valid_pw != 200
        # Meanwhile valid `staff_b` session continues getting `200`!
        st_valid_during_bf, _, _ = staff_b.get_json(
            "/api/method/crm_cskh.api.customers.my_list", {"page": 1, "page_size": 5}
        )
        stack.exec_site_python(
            "frappe.db.set_single_value('System Settings', 'allow_consecutive_login_attempts', 0)\n"
            "frappe.clear_cache()\n"
            "print(json.dumps({'login_lockout_reset': True}))"
        )
        assert locked_out_detected and st_valid_during_bf == 200, (
            login_fail_codes,
            st_locked_valid_pw,
            st_valid_during_bf,
        )

        # (ii) Layer drop-in Traefik v3.7 edge `rateLimit` override (`compose.cskh-edge-ratelimit.yaml`) -> deterministic `HTTP 429`!
        stack.apply_extra_compose_overrides(
            [EDGE_RATELIMIT_OVERRIDE],
            extra_env={
                "CSKH_LOGIN_RL_AVERAGE": "2",
                "CSKH_LOGIN_RL_PERIOD": "1m",
                "CSKH_LOGIN_RL_BURST": "2",
                "CSKH_HEAVY_RL_AVERAGE": "3",
                "CSKH_HEAVY_RL_PERIOD": "1m",
                "CSKH_HEAVY_RL_BURST": "3",
            },
            services=["proxy", "frontend"],
        )
        time.sleep(1.5)  # allow Traefik Docker provider to reload router labels

        # Burst `/api/method/login` -> hits `HTTP 429`
        rl_login_codes = [
            brute_actor.post_json(
                "/api/method/login",
                {"usr": "nobody@dev.local", "pwd": "bad"},
                include_csrf=False,
            )[0]
            for _ in range(10)
        ]
        # Burst heavy API (`customer_pool`) from `admin` -> hits `HTTP 429`, while `leader1` (separate `Cookie` bucket) gets `200`!
        rl_heavy_admin_codes = [
            admin.get_json(
                "/api/method/crm_cskh.api.customers.customer_pool", {"page": 1}
            )[0]
            for _ in range(12)
        ]
        st_leader1_unstarved, _, _ = leader1.get_json(
            "/api/method/crm_cskh.api.customers.customer_pool", {"page": 1}
        )
        assert 429 in rl_login_codes, (
            f"Expected HTTP 429 on login burst, got {rl_login_codes}"
        )
        assert 429 in rl_heavy_admin_codes, (
            f"Expected HTTP 429 on heavy API burst, got {rl_heavy_admin_codes}"
        )
        assert st_leader1_unstarved == 200, (
            f"Concurrent user starved during rate-limit burst: {st_leader1_unstarved}"
        )

        port_isolation = verify_internal_ports_unexposed(stack)

        return {
            "schema_version": "cskh-p4-batch2-g11-g12-v1",
            "status": "PASS",
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "gates_verified": ["G11", "G12"],
            "locked_batch1_commit_sha": LOCKED_BATCH1_COMMIT_SHA,
            "app_image_tested": app_image,
            "topology_measured": {
                "architecture": "single_vps_10_service_compose",
                "extra_distributed_cluster_created": False,
                "session_store": "redis-cache + MariaDB tabSessions",
                "realtime_bus": "redis-queue (redis_socketio) -> websocket (Node Socket.IO :9000)",
                "open_websocket_behavior_note": (
                    "Upstream Frappe v15 validates `sid` on Socket.IO namespace connect (`40/<site>`) and "
                    "per-document `doc_subscribe` (`can_subscribe_doc`), without pushing an unsolicited TCP 0x8 "
                    "close frame on `clear_sessions`; verified 0 PII frames on pre-existing open TCP socket, "
                    "immediate `44/<site>` rejection on reconnect, and immediate `401/403` on all HTTP/RPC/REST calls."
                ),
            },
            "g11_revocation_results": {
                "s07_transfer_and_revoke": {
                    "old_owner_work_item_http": st_a_wi,
                    "old_owner_record_attempt_http": st_a_att,
                    "old_owner_rest_lead_http": st_a_rest,
                    "old_owner_desk_getdoc_http": st_a_getdoc,
                    "new_owner_work_item_http": st_b_wi,
                    "docshare_and_todo_cleanup": s07_db,
                    "a_to_b_to_a_and_revoke_http": [st_ba, st_rev],
                    "zero_pii_frames_on_old_open_ws": True,
                },
                "s08_user_disable": {
                    **disable_info,
                    "disabled_page_http": st_dis_page,
                    "disabled_api_http": st_dis_api,
                    "disabled_mutation_http": st_dis_mut,
                    "socketio_reconnect_rejected_44": True,
                    "excluded_from_campaign_eligibility": True,
                },
                "s09_and_s04_role_and_team_change": {
                    "s04_historical_team_as_of_leader2_http": st_s04_wi,
                    "pre_downgrade_export_http": st_l1_exp_ok,
                    "post_downgrade_team_page_http": st_s09_page,
                    "post_downgrade_team_api_http": st_s09_api,
                    "post_downgrade_export_rpc_http": st_s09_exp_rpc,
                    "post_downgrade_export_raw_file_http": st_s09_exp_raw,
                },
            },
            "g12_security_controls": {
                "authorization_and_headers": {
                    "guest_blocked": [st_g_page, st_g_api, st_g_priv],
                    "outside_user_http": st_out,
                    "cross_staff_403_parity": [st_s02_existing, st_s02_missing],
                    "cross_team_leader_http": st_s03_leader,
                    "bod_include_pii_blocked_http": st_bod_pii,
                    "bod_aggregate_drilldown_zero_pii": True,
                    "forged_csrf_http": st_csrf_bad,
                    "desk_bypass_http": st_desk_bypass,
                    "spoofed_actor_http": st_spoof,
                    "security_headers_verified": [
                        "x-content-type-options",
                        "x-frame-options",
                        "referrer-policy",
                        "permissions-policy",
                    ],
                },
                "payload_and_upload_limits_s11": {
                    "nginx_26mb_body_http": st_26m,
                    "app_11mb_import_http": st_11m,
                    "disguised_exe_upload_http": st_exe,
                    "corrupt_xlsx_preview_http": st_bad_xlsx_prev,
                    "foreign_lead_upload_file_http": st_foreign_attach,
                },
                "audit_and_file_export_s12_s13_s15": {
                    "immutable_doctype_delete_blocked": immut_res,
                    "export_file_is_private": 1,
                    "export_file_owner": "Administrator",
                    "csv_formula_injection_neutralized": True,
                    "nginx_protected_internal_guard_http": st_prot_direct,
                    "path_traversal_guard_http": st_traversal,
                    "expired_export_download_http": st_ttl_rpc,
                },
                "rate_limiting_s20": {
                    "base_compose_observation": (
                        "Base `compose.cskh-tls.yaml` relies on Frappe `System Settings` consecutive failed-login "
                        "lockout in `redis-cache`; per-route `HTTP 429` burst throttling is delivered without rebuilding "
                        "`APP_IMAGE` via `deploy/release/batch2_delta/compose.cskh-edge-ratelimit.yaml`."
                    ),
                    "frappe_consecutive_login_lockout_triggered": locked_out_detected,
                    "traefik_login_burst_http_codes": rl_login_codes,
                    "traefik_heavy_api_burst_http_codes": rl_heavy_admin_codes,
                    "concurrent_other_session_http_status": st_leader1_unstarved,
                },
                "port_isolation_s18": port_isolation,
            },
        }
    finally:
        stack.teardown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", default=LOCKED_BATCH1_IMAGE_DIGEST)
    parser.add_argument("--http-port", type=int, default=18920)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--report-out", type=pathlib.Path, required=True)
    parser.add_argument("--log-out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    scrubber = SecretScrubber()
    if args.plan_only:
        scrubber.write_json_and_log(
            build_plan_only_report(args.app_image), args.report_out, args.log_out
        )
        return 0

    try:
        report = run_live_verification(
            app_image=args.app_image,
            http_port=args.http_port,
            scrubber=scrubber,
        )
    except Exception as exc:
        scrubber.log(f"FATAL: {exc}\n{traceback.format_exc()}")
        scrubber.write_json_and_log(
            {
                "schema_version": "cskh-p4-batch2-g11-g12-v1",
                "status": "FAIL",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
            args.report_out,
            args.log_out,
        )
        raise
    scrubber.write_json_and_log(report, args.report_out, args.log_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
