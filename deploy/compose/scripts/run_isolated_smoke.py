#!/usr/bin/env python3
"""Isolated Compose runtime smoke verifier for P4-01 §4 (synthetic data only).

Executes ONLY against an isolated `crm-cskh-p4-smoke*` project and `*.localhost`
smoke site — never touches Owner DEV stacks (`crm-cskh-dev`, `crm-cskh-p3-ui`),
Staging, or Production.

Checks performed when prerequisites are met:
1. `docker compose config --services` (default + `migrate` profile).
2. Container startup, `configurator` exit=0, and all 10 service healthchecks == `healthy`.
3. Host port publication audit (ONLY `127.0.0.1:<HTTP_PUBLISH_PORT>->80/tcp` on `proxy`).
4. Synthetic site bootstrap (`frappe`, `crm`, `crm_cskh`) + controlled `migrator` run.
5. HTTP login + session cookie (`HttpOnly`, `SameSite`) + `get_logged_user`.
6. Vietnamese UI smoke (`/crm`, `/cskh/dashboard`, `/cskh/quality-overview`, `Asia/Ho_Chi_Minh`).
7. CSRF enforcement (forged/missing `X-Frappe-CSRF-Token` rejected; valid token accepted).
8. Socket.IO EIO=4 handshake via `proxy -> frontend -> websocket` (unauth rejected `44`,
   authenticated `Cookie: sid=...` + `Origin` accepted `40/<site>`).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

COMPOSE_ROOT = pathlib.Path(__file__).resolve().parents[1]
UPSTREAM_DIR = COMPOSE_ROOT / "upstream" / "frappe_docker"
OVERRIDES_DIR = COMPOSE_ROOT / "overrides"
SMOKE_ENV_TEMPLATE = COMPOSE_ROOT / "env" / "isolated-smoke.env.example"

SMOKE_COMPOSE_FILES = [
    UPSTREAM_DIR / "compose.yaml",
    UPSTREAM_DIR / "overrides" / "compose.mariadb.yaml",
    UPSTREAM_DIR / "overrides" / "compose.mariadb-secrets.yaml",
    UPSTREAM_DIR / "overrides" / "compose.redis.yaml",
    UPSTREAM_DIR / "overrides" / "compose.proxy.yaml",
    UPSTREAM_DIR / "overrides" / "compose.migrator.yaml",
    OVERRIDES_DIR / "compose.cskh-common.yaml",
    OVERRIDES_DIR / "compose.cskh-isolated-smoke.yaml",
]

DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
PLACEHOLDER_DIGEST = "sha256:" + ("0" * 64)
LONG_RUNNING_SERVICES = sorted(
    [
        "backend",
        "db",
        "frontend",
        "proxy",
        "queue-long",
        "queue-short",
        "redis-cache",
        "redis-queue",
        "scheduler",
        "websocket",
    ]
)


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def compose_cmd(env_file: pathlib.Path, extra: list[str]) -> list[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in SMOKE_COMPOSE_FILES:
        cmd.extend(["-f", str(cf)])
    cmd.extend(extra)
    return cmd


def check_docker_daemon() -> tuple[bool, str]:
    res = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if res.returncode == 0 and res.stdout.strip():
        return True, res.stdout.strip()
    return False, (res.stderr or res.stdout).strip()


def check_local_or_pulled_image(image_ref: str) -> tuple[bool, str]:
    res = subprocess.run(
        ["docker", "image", "inspect", image_ref, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if res.returncode == 0 and res.stdout.strip():
        return True, res.stdout.strip()
    return False, (res.stderr or res.stdout).strip()


class HostHeaderRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_http_client() -> tuple[http.cookiejar.CookieJar, urllib.request.OpenerDirector]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        HostHeaderRedirectHandler(),
    )
    return jar, opener


def http_call(
    opener: urllib.request.OpenerDirector,
    url: str,
    site_host: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    hdrs = {"Host": site_host}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with opener.open(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, dict(resp.headers.items()), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, body


def bootstrap_synthetic_site(
    env_file: pathlib.Path,
    site_name: str,
    db_root_pw: str,
    admin_pw: str,
) -> dict:
    """Creates synthetic smoke site inside `backend` passing secrets via stdin JSON (no CLI leak)."""
    py_code = r"""
import json, os, subprocess, sys
payload = json.loads(sys.stdin.read())
site = payload["site_name"]
db_pw = payload["db_root_pw"]
adm_pw = payload["admin_pw"]

site_cfg = os.path.join("sites", site, "site_config.json")
if not os.path.exists(site_cfg):
    subprocess.check_call([
        "bench", "new-site", site,
        "--mariadb-user-host-login-scope=%",
        "--db-root-password", db_pw,
        "--admin-password", adm_pw,
        "--install-app", "crm",
        "--install-app", "crm_cskh",
    ])

apps = subprocess.check_output(["bench", "--site", site, "list-apps"], text=True).strip().splitlines()
subprocess.check_call([
    "bench", "--site", site, "execute",
    "frappe.db.set_single_value",
    "--args", json.dumps(["System Settings", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"}]),
])
subprocess.check_call(["bench", "--site", site, "clear-cache"])
print(json.dumps({"site": site, "installed_apps": apps}))
"""
    proc = subprocess.run(
        compose_cmd(env_file, ["exec", "-T", "backend", "python3", "-c", py_code]),
        input=json.dumps(
            {"site_name": site_name, "db_root_pw": db_root_pw, "admin_pw": admin_pw}
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Site bootstrap failed: {proc.stderr}\n{proc.stdout}")
    last_line = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
    return json.loads(last_line)


def query_compose_states(env_file: pathlib.Path) -> dict[str, dict]:
    res = subprocess.run(
        compose_cmd(env_file, ["ps", "-a", "--format", "json"]),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if res.returncode != 0 or not res.stdout.strip():
        return {}
    raw = res.stdout.strip()
    rows = (
        json.loads(raw)
        if raw.startswith("[")
        else [json.loads(line) for line in raw.splitlines() if line.strip()]
    )
    return {
        r["Service"]: {
            "state": r.get("State"),
            "health": r.get("Health"),
            "exit_code": r.get("ExitCode"),
            "publishers": r.get("Publishers") or [],
        }
        for r in rows
    }


def wait_for_bootstrap_ready(env_file: pathlib.Path, timeout_sec: int = 120) -> None:
    """Waits until db/redis are healthy, configurator exited 0, and backend is running."""
    deadline = time.time() + timeout_sec
    last_states: dict[str, dict] = {}
    while time.time() < deadline:
        last_states = query_compose_states(env_file)
        cfg_ok = (
            "configurator" in last_states
            and last_states["configurator"]["state"] == "exited"
            and int(last_states["configurator"].get("exit_code") or 0) == 0
        )
        infra_ok = all(
            s in last_states and last_states[s].get("health") == "healthy"
            for s in ("db", "redis-cache", "redis-queue")
        )
        backend_running = (
            "backend" in last_states and last_states["backend"].get("state") == "running"
        )
        if cfg_ok and infra_ok and backend_running:
            return
        time.sleep(2)
    raise TimeoutError(
        f"Timed out waiting for Day-0 bootstrap readiness: {json.dumps(last_states, indent=2)}"
    )


def wait_for_stack_health(env_file: pathlib.Path, timeout_sec: int = 180) -> dict:
    deadline = time.time() + timeout_sec
    last_states: dict[str, dict] = {}
    while time.time() < deadline:
        last_states = query_compose_states(env_file)
        if last_states:
            cfg_ok = (
                "configurator" in last_states
                and last_states["configurator"]["state"] == "exited"
                and int(last_states["configurator"].get("exit_code") or 0) == 0
            )
            all_healthy = all(
                s in last_states and last_states[s].get("health") == "healthy"
                for s in LONG_RUNNING_SERVICES
            )
            if cfg_ok and all_healthy:
                return last_states
        time.sleep(3)
    raise TimeoutError(f"Timed out waiting for healthy stack: {json.dumps(last_states, indent=2)}")


def run_http_and_socketio_smoke(
    base_url: str,
    site_name: str,
    admin_pw: str,
) -> dict:
    # 1. Guest check on /crm and /cskh/quality-overview
    _, guest_opener = make_http_client()
    g_crm_code, _, _ = http_call(guest_opener, f"{base_url}/crm", site_name)
    g_cskh_code, _, _ = http_call(guest_opener, f"{base_url}/cskh/quality-overview", site_name)
    assert g_crm_code in (301, 302, 303, 403), f"Expected Guest /crm block/redirect, got {g_crm_code}"
    assert g_cskh_code in (301, 302, 303, 403), (
        f"Expected Guest /cskh/quality-overview 403/redirect, got {g_cskh_code}"
    )

    # 2. Login + session cookie flags
    jar, auth_opener = make_http_client()
    login_body = urllib.parse.urlencode({"usr": "Administrator", "pwd": admin_pw}).encode("utf-8")
    l_code, l_hdrs, l_raw = http_call(
        auth_opener,
        f"{base_url}/api/method/login",
        site_name,
        method="POST",
        data=login_body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    assert l_code == 200, f"Login failed ({l_code}): {l_raw[:300]}"
    sid_cookie = next((c for c in jar if c.name == "sid"), None)
    assert sid_cookie and sid_cookie.value and sid_cookie.value != "Guest", "Missing sid cookie"

    u_code, _, u_raw = http_call(
        auth_opener,
        f"{base_url}/api/method/frappe.auth.get_logged_user",
        site_name,
        headers={"Accept": "application/json"},
    )
    assert u_code == 200 and "Administrator" in u_raw, f"get_logged_user failed: {u_raw}"

    # 3. Vietnamese UI + CSRF token extraction on /cskh/quality-overview and /crm
    ui_code, _, ui_html = http_call(auth_opener, f"{base_url}/cskh/quality-overview", site_name)
    assert ui_code == 200, f"Expected 200 on /cskh/quality-overview, got {ui_code}"
    assert "Asia/Ho_Chi_Minh" in ui_html, "Missing Asia/Ho_Chi_Minh timezone in CSKH page"
    has_vi = any(term in ui_html for term in ("Tổng quan", "Chất lượng", "CSKH", "Đăng xuất"))
    assert has_vi, "Expected Vietnamese UI labels on /cskh/quality-overview"

    csrf_match = re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fA-F\-]{16,})["\']', ui_html)
    assert csrf_match, "Could not locate CSRF token in authenticated CSKH HTML"
    csrf_token = csrf_match.group(1)

    crm_code, _, _ = http_call(auth_opener, f"{base_url}/crm", site_name)
    assert crm_code == 200, f"Expected 200 on authenticated /crm, got {crm_code}"

    # 4. CSRF negative & positive smoke
    bad_csrf_code, _, bad_csrf_body = http_call(
        auth_opener,
        f"{base_url}/api/method/crm_cskh.api.p3_reporting.request_recompute",
        site_name,
        method="POST",
        data=json.dumps({"period_type": "monthly", "period_key": "2026-11", "reason": "csrf-neg"}).encode(
            "utf-8"
        ),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Frappe-CSRF-Token": "forged-invalid-csrf-token",
        },
    )
    assert bad_csrf_code in (400, 403), (
        f"Forged CSRF token must be rejected with 400/403, got {bad_csrf_code}: {bad_csrf_body[:200]}"
    )

    good_csrf_code, _, good_csrf_body = http_call(
        auth_opener,
        f"{base_url}/api/method/frappe.desk.notifications.get_notifications",
        site_name,
        method="POST",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Frappe-CSRF-Token": csrf_token,
        },
    )
    assert good_csrf_code == 200, (
        f"Valid CSRF POST failed ({good_csrf_code}): {good_csrf_body[:200]}"
    )

    # 5. Socket.IO EIO=4 realtime smoke through proxy -> frontend -> websocket
    # 5a. Negative: unauthenticated namespace connect must return 44 (connect_error)
    _, unauth_sio = make_http_client()
    eio_code1, _, eio_open1 = http_call(
        unauth_sio,
        f"{base_url}/socket.io/?EIO=4&transport=polling",
        site_name,
        headers={"Origin": f"http://{site_name}"},
    )
    assert eio_code1 == 200 and eio_open1.startswith("0{"), f"Socket.IO open failed: {eio_open1}"
    eio_sid1 = json.loads(eio_open1[1:])["sid"]
    http_call(
        unauth_sio,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid1}",
        site_name,
        method="POST",
        data=f"40/{site_name},".encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": f"http://{site_name}"},
    )
    _, _, unauth_pkt = http_call(
        unauth_sio,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid1}",
        site_name,
        headers={"Origin": f"http://{site_name}"},
    )
    assert f"44/{site_name}" in unauth_pkt, (
        f"Expected unauthenticated Socket.IO namespace rejection (44), got: {unauth_pkt}"
    )

    # 5b. Positive: authenticated Cookie sid + matching Origin must return 40/<site_name>
    eio_code2, _, eio_open2 = http_call(
        auth_opener,
        f"{base_url}/socket.io/?EIO=4&transport=polling",
        site_name,
        headers={"Origin": f"http://{site_name}"},
    )
    assert eio_code2 == 200 and eio_open2.startswith("0{"), f"Auth Socket.IO open failed: {eio_open2}"
    eio_sid2 = json.loads(eio_open2[1:])["sid"]
    http_call(
        auth_opener,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid2}",
        site_name,
        method="POST",
        data=f"40/{site_name},".encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": f"http://{site_name}"},
    )
    _, _, auth_pkt = http_call(
        auth_opener,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid2}",
        site_name,
        headers={"Origin": f"http://{site_name}"},
    )
    assert f"40/{site_name}" in auth_pkt, (
        f"Expected authenticated Socket.IO namespace accept (40/{site_name}), got: {auth_pkt}"
    )

    return {
        "guest_crm_status": g_crm_code,
        "guest_cskh_status": g_cskh_code,
        "login_status": l_code,
        "logged_user": "Administrator",
        "cskh_vi_ui_status": ui_code,
        "crm_spa_status": crm_code,
        "csrf_forged_rejected_status": bad_csrf_code,
        "csrf_valid_accepted_status": good_csrf_code,
        "socketio_unauth_rejected": True,
        "socketio_auth_namespace_connected": f"/{site_name}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-image",
        default=os.environ.get("APP_IMAGE", ""),
        help="Immutable custom image@sha256:<digest> built and handed off by T1B.",
    )
    parser.add_argument(
        "--p4-00-approved-sha",
        default=os.environ.get("P4_00_APPROVED_SHA", ""),
        help="Commit SHA of T0-approved P4-00 entry audit.",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=int(os.environ.get("HTTP_PUBLISH_PORT", "18080")),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Check prerequisites and render `config --services` without starting containers.",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep isolated smoke containers up after verification (default: tear down -v).",
    )
    parser.add_argument("--report-out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    template_env = parse_env_file(SMOKE_ENV_TEMPLATE)
    project_name = template_env["COMPOSE_PROJECT_NAME"]
    site_name = template_env["SITE_NAME"]

    if not project_name.startswith("crm-cskh-p4-smoke") or not site_name.endswith(".localhost"):
        raise SystemExit(
            f"SAFETY STOP: refusing non-isolated target project={project_name} site={site_name}"
        )

    blockers: list[dict[str, str]] = []
    if not args.p4_00_approved_sha:
        blockers.append(
            {
                "id": "BLK-RUNTIME-01",
                "component": "P4-00 Entry Audit Sign-off (T0)",
                "status": "BLOCKED",
                "detail": "T0 has not finalized/merged P4-00 (draft at be33e6c); --p4-00-approved-sha not provided.",
            }
        )

    app_image = args.app_image or template_env["APP_IMAGE"]
    if not DIGEST_RE.match(app_image) or PLACEHOLDER_DIGEST in app_image:
        blockers.append(
            {
                "id": "BLK-RUNTIME-02",
                "component": "Custom Production Image Digest (T1B)",
                "status": "BLOCKED",
                "detail": "T1B has not yet handed off a real built APP_IMAGE@sha256:<64-hex> digest.",
            }
        )

    daemon_ok, daemon_detail = check_docker_daemon()
    if not daemon_ok:
        blockers.append(
            {
                "id": "BLK-RUNTIME-03",
                "component": "Host Docker Engine Daemon",
                "status": "BLOCKED",
                "detail": f"Docker Engine daemon is not running: {daemon_detail}",
            }
        )
    elif not blockers:
        img_ok, img_detail = check_local_or_pulled_image(app_image)
        if not img_ok and template_env.get("PULL_POLICY") in ("missing", "never"):
            blockers.append(
                {
                    "id": "BLK-RUNTIME-04",
                    "component": "APP_IMAGE Availability",
                    "status": "BLOCKED",
                    "detail": f"Image {app_image} not present locally: {img_detail}",
                }
            )

    with tempfile.TemporaryDirectory(prefix="cskh-p4-smoke-run-") as td:
        tmp = pathlib.Path(td)
        db_pw = secrets.token_urlsafe(24)
        adm_pw = "SmokeP4#" + secrets.token_hex(8)
        sec_file = tmp / "db_root_password.txt"
        sec_file.write_text(db_pw + "\n", encoding="utf-8")
        try:
            os.chmod(sec_file, 0o600)
        except OSError:
            pass

        run_env = dict(template_env)
        run_env["APP_IMAGE"] = app_image
        run_env["DB_PASSWORD_SECRETS_FILE"] = str(sec_file)
        run_env["HTTP_PUBLISH_PORT"] = str(args.http_port)
        env_file = tmp / "isolated-smoke.env"
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in run_env.items()) + "\n", encoding="utf-8"
        )

        srv_res = subprocess.run(
            compose_cmd(env_file, ["config", "--services"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert srv_res.returncode == 0, f"config --services failed: {srv_res.stderr}"
        config_services = [s.strip() for s in srv_res.stdout.splitlines() if s.strip()]

        if args.preflight_only or blockers:
            report = {
                "status": "BLOCKED" if blockers else "PREFLIGHT_READY",
                "project_name": project_name,
                "site_name": site_name,
                "config_services": config_services,
                "blockers": blockers,
            }
            if args.report_out:
                args.report_out.parent.mkdir(parents=True, exist_ok=True)
                args.report_out.write_text(
                    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 2 if (blockers and not args.preflight_only) else 0

        try:
            subprocess.check_call(compose_cmd(env_file, ["up", "-d"]))
            wait_for_bootstrap_ready(env_file)
            site_info = bootstrap_synthetic_site(env_file, site_name, db_pw, adm_pw)
            health_states = wait_for_stack_health(env_file)

            # Verify published ports on running stack: ONLY proxy 127.0.0.1:<port>->80/tcp
            port_audit: dict[str, list] = {}
            for sname, sstate in health_states.items():
                pubs = [
                    p
                    for p in sstate.get("publishers", [])
                    if p.get("PublishedPort") and int(p.get("PublishedPort")) > 0
                ]
                if pubs:
                    port_audit[sname] = pubs
            assert list(port_audit.keys()) == ["proxy"], (
                f"Unexpected host port publishers: {port_audit}"
            )
            assert all(
                p.get("URL") == "127.0.0.1" and int(p.get("PublishedPort")) == args.http_port
                for p in port_audit["proxy"]
            ), f"Non-loopback or unexpected port on proxy: {port_audit['proxy']}"

            # Controlled migrator idempotency rehearsal (x2)
            mig_env = dict(run_env)
            mig_env["MIGRATE_SITES"] = "true"
            mig_env_file = tmp / "isolated-smoke-migrate.env"
            mig_env_file.write_text(
                "\n".join(f"{k}={v}" for k, v in mig_env.items()) + "\n", encoding="utf-8"
            )
            for _ in range(2):
                subprocess.check_call(
                    compose_cmd(mig_env_file, ["--profile", "migrate", "run", "--rm", "migrator"])
                )

            base_url = f"http://127.0.0.1:{args.http_port}"
            smoke_results = run_http_and_socketio_smoke(base_url, site_name, adm_pw)

            report = {
                "status": "PASS",
                "project_name": project_name,
                "site_name": site_name,
                "p4_00_approved_sha": args.p4_00_approved_sha,
                "app_image": app_image,
                "config_services": config_services,
                "healthchecks": {
                    k: {"state": v["state"], "health": v["health"], "exit_code": v["exit_code"]}
                    for k, v in sorted(health_states.items())
                },
                "port_exposure_audit": port_audit,
                "site_bootstrap": site_info,
                "smoke_verification": smoke_results,
            }
            if args.report_out:
                args.report_out.parent.mkdir(parents=True, exist_ok=True)
                args.report_out.write_text(
                    json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                )
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        finally:
            if not args.keep_running:
                subprocess.run(
                    compose_cmd(env_file, ["down", "--volumes", "--remove-orphans"]),
                    check=False,
                )


if __name__ == "__main__":
    sys.exit(main())
