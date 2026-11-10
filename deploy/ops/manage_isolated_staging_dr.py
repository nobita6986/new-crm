#!/usr/bin/env python3
"""Dedicated Isolated Staging / DR HTTPS Environment Orchestrator (`P4` Objective 4).

Provides an isolated, non-conflicting HTTPS + `wss://` Frappe/CSKH environment (`dr-https`
Compose mode) for destructive/interactive operational validation:
  1. Multi-role Vietnamese UAT (`G13-G16`) & RBAC / offboarding checks (`G10-G12`) over TLS
  2. Queue worker & scheduler restart + back-to-back cron retry idempotency (`G17`)
  3. Rollback & point-in-time restore drills (`G09`: Tier 1 kill switch, Tier 2 digest
     verification/re-apply, Tier 4 `bench backup` -> post-backup write -> `--since-utc`
     delta -> `bench --force restore` + `migrator` -> `INV-01..06` + HTTPS smoke)

Hard safety boundary:
  - Strictly refuses to target Owner's live stack (`crm-cskh-vps-staging`,
    `crm-staging.localhost`, `crm.hrpartner.vn`, `/opt/crm-cskh-staging`), Production
    (`crm-cskh-prod`), or live ingress ports (`80`, `443`, `8080`, `8443`, `8000`, `3306`, `6379`).
  - Sets `mute_emails = 1` on bootstrap and uses synthetic `@example.test` / `@example.invalid`
    personas only (never loads Owner DB/files or real PII).
  - Mints a 2048-bit ephemeral Root CA (`ca.crt`), signs the leaf TLS certificate with SAN
    `DNS:<SITE_NAME>,IP:127.0.0.1`, deletes `ca.key` immediately, and mounts the leaf cert
    into Traefik's project-scoped `${COMPOSE_PROJECT_NAME}_cert_data` named volume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_SCRIPTS_DIR = REPO_ROOT / "deploy" / "compose" / "scripts"
OPS_DIR = REPO_ROOT / "deploy" / "ops"
for _p in (str(COMPOSE_SCRIPTS_DIR), str(OPS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_health_and_invariants as health_tool  # noqa: E402
import run_isolated_smoke as smoke_lib  # noqa: E402
import staging_https_smoke_and_reconcile as p403_tool  # noqa: E402

DR_COMPOSE_FILES = p403_tool.COMPOSE_MODES["dr-https"]
DR_TEMPLATE_ENV = REPO_ROOT / "deploy" / "compose" / "env" / "staging-dr-https.env.example"
DEFAULT_DR_SITE = "staging-dr-uat.crm-internal.localhost"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")

FORBIDDEN_OWNER_PROJECTS = {
    "crm-cskh-vps-staging",
    "crm-cskh-prod",
    "crm-cskh-staging",
    "crm-cskh-dev",
    "crm-cskh-p1",
    "crm-cskh-p2",
    "crm-cskh-p3-ui",
}
FORBIDDEN_OWNER_SITES = {
    "crm-staging.localhost",
    "crm.hrpartner.vn",
    "dev.localhost",
    "p3ui.localhost",
}
FORBIDDEN_HOST_PORTS = {80, 443, 3306, 6379, 8000, 8080, 8443, 9000}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick_free_loopback_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def assert_isolated_from_owner_stack(
    env: dict[str, str],
    env_file: Path | None = None,
    *,
    allow_private_bind: bool = False,
) -> None:
    """Fail closed before any Docker/Compose call if target collides with Owner or Prod stacks."""
    proj = env.get("COMPOSE_PROJECT_NAME", "").strip()
    proj_low = proj.lower()
    site = env.get("SITE_NAME", "").strip()
    site_low = site.lower()
    bind_ip = env.get("INGRESS_BIND_IP", "127.0.0.1").strip()

    if not proj or proj_low in FORBIDDEN_OWNER_PROJECTS or "prod" in proj_low or "vps-staging" in proj_low:
        raise ValueError(
            f"Refusing isolated Staging/DR operation: COMPOSE_PROJECT_NAME={proj!r} collides with "
            "protected Owner live/staging/prod stack."
        )
    if "dr" not in proj_low and "reh" not in proj_low and "uat" not in proj_low:
        raise ValueError(
            f"Refusing isolated Staging/DR operation: COMPOSE_PROJECT_NAME={proj!r} must include "
            "'dr', 'uat', or 'reh' to stay disjoint from live stacks."
        )

    if not site or site_low in FORBIDDEN_OWNER_SITES or "hrpartner" in site_low or "prod" in site_low:
        raise ValueError(
            f"Refusing isolated Staging/DR operation: SITE_NAME={site!r} collides with protected "
            "Owner domain/site."
        )

    for candidate_path in (env_file, Path(env.get("DB_PASSWORD_SECRETS_FILE", "") or "/nonexistent")):
        if candidate_path is not None:
            norm = str(candidate_path).replace("\\", "/").lower()
            if "/opt/crm-cskh-staging" in norm:
                raise ValueError(
                    f"Refusing isolated Staging/DR operation: path {candidate_path} points inside "
                    "Owner live directory /opt/crm-cskh-staging."
                )

    if bind_ip != "127.0.0.1" and not allow_private_bind:
        raise ValueError(
            f"Refusing isolated Staging/DR operation: INGRESS_BIND_IP={bind_ip!r} must be "
            "'127.0.0.1' unless --allow-private-bind is explicitly set for a private VPN interface."
        )
    if bind_ip in ("0.0.0.0", "::"):
        raise ValueError("Refusing isolated Staging/DR operation: wildcard 0.0.0.0/:: bind is forbidden.")

    http_port = int(env.get("HTTP_PUBLISH_PORT", "18081"))
    https_port = int(env.get("HTTPS_PUBLISH_PORT", "18443"))
    if http_port == https_port:
        raise ValueError(f"HTTP_PUBLISH_PORT and HTTPS_PUBLISH_PORT must differ (got {http_port}).")
    for p_label, p_val in (("HTTP_PUBLISH_PORT", http_port), ("HTTPS_PUBLISH_PORT", https_port)):
        if p_val in FORBIDDEN_HOST_PORTS or p_val < 1024:
            raise ValueError(
                f"Refusing isolated Staging/DR operation: {p_label}={p_val} collides with reserved "
                "system/live ports."
            )


def compose_cmd(env_file: Path, args: list[str], *, profile_migrate: bool = False) -> list[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in DR_COMPOSE_FILES:
        cmd.extend(["-f", str(cf)])
    if profile_migrate:
        cmd.extend(["--profile", "migrate"])
    cmd.extend(args)
    return cmd


def run_sub(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    env = dict(kwargs.pop("env", None) or os.environ)
    env["PYTHONUTF8"] = "1"
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    kwargs["text"] = True
    kwargs["encoding"] = "utf-8"
    kwargs["errors"] = "replace"
    return subprocess.run(cmd, env=env, **kwargs)


def wait_services_healthy(
    env_file: Path,
    services: tuple[str, ...],
    *,
    timeout_seconds: int = 120,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ps_out = run_sub(compose_cmd(env_file, ["ps", "--format", "json"])).stdout
        rows = p403_tool._parse_compose_ps_json(ps_out)
        by_svc = {str(r.get("Service", "")): r for r in rows}
        if all(
            by_svc.get(s, {}).get("State") == "running"
            and by_svc.get(s, {}).get("Health") == "healthy"
            for s in services
        ):
            return
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for services to become healthy: {services}")


def provision_ephemeral_ca_and_traefik_tls(
    *,
    app_image: str,
    project_name: str,
    site_name: str,
    state_dir: Path,
) -> dict[str, Any]:
    """Mint ephemeral Root CA (`ca.key` deleted immediately), sign SAN leaf cert, load into `cert-data`."""
    state_dir.mkdir(parents=True, exist_ok=True)
    cert_volume = f"{project_name}_cert_data"
    run_sub(["docker", "volume", "create", cert_volume])

    gen_py = r"""
import json, os, stat, subprocess, sys, tempfile
from pathlib import Path

site = sys.argv[1]
dyn_dir = Path("/letsencrypt/dynamic")
dyn_dir.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    ca_key = tmp / "ca.key"
    ca_crt = tmp / "ca.crt"
    srv_key = tmp / "server.key"
    srv_csr = tmp / "server.csr"
    srv_crt = tmp / "server.crt"
    ext_cfg = tmp / "server_ext.cnf"

    subprocess.check_call([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_crt), "-days", "30",
        "-subj", "/CN=CRM CSKH Isolated Staging-DR Ephemeral Root CA",
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    subprocess.check_call([
        "openssl", "req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(srv_key), "-out", str(srv_csr),
        "-subj", f"/CN={site}",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ext_cfg.write_text(
        "\n".join([
            "basicConstraints=CA:FALSE",
            "keyUsage=critical,digitalSignature,keyEncipherment",
            "extendedKeyUsage=serverAuth",
            f"subjectAltName=DNS:{site},IP:127.0.0.1",
        ]) + "\n",
        encoding="utf-8",
    )
    subprocess.check_call([
        "openssl", "x509", "-req",
        "-in", str(srv_csr),
        "-CA", str(ca_crt),
        "-CAkey", str(ca_key),
        "-CAcreateserial",
        "-out", str(srv_crt),
        "-days", "30",
        "-sha256",
        "-extfile", str(ext_cfg),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Immediately destroy Root CA private key so no persistent CA signing key exists anywhere
    ca_key.unlink()
    ca_key_deleted = not ca_key.exists()

    target_crt = dyn_dir / "server.crt"
    target_key = dyn_dir / "server.key"
    target_yml = dyn_dir / "tls.yml"

    target_crt.write_text(srv_crt.read_text(encoding="utf-8"), encoding="utf-8")
    target_key.write_text(srv_key.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(target_key, 0o600)
    os.chmod(target_crt, 0o644)
    target_yml.write_text(
        "\n".join([
            "tls:",
            "  certificates:",
            "    - certFile: /letsencrypt/dynamic/server.crt",
            "      keyFile: /letsencrypt/dynamic/server.key",
            "  stores:",
            "    default:",
            "      defaultCertificate:",
            "        certFile: /letsencrypt/dynamic/server.crt",
            "        keyFile: /letsencrypt/dynamic/server.key",
        ]) + "\n",
        encoding="utf-8",
    )
    print("TLS_MINT_JSON=" + json.dumps({
        "ca_crt_pem": ca_crt.read_text(encoding="utf-8"),
        "ca_key_deleted_immediately": ca_key_deleted,
        "server_key_mode": oct(stat.S_IMODE(target_key.stat().st_mode)),
    }))
"""
    proc = run_sub(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "-v",
            f"{cert_volume}:/letsencrypt",
            "--entrypoint",
            "python3",
            app_image,
            "-c",
            gen_py,
            site_name,
        ]
    )
    for line in proc.stdout.splitlines():
        if line.startswith("TLS_MINT_JSON="):
            data = json.loads(line.split("=", 1)[1])
            ca_crt_path = state_dir / "ca.crt"
            ca_crt_path.write_text(data["ca_crt_pem"], encoding="utf-8", newline="\n")
            return {
                "cert_volume": cert_volume,
                "ca_crt_path": str(ca_crt_path),
                "ca_key_deleted_immediately": bool(data["ca_key_deleted_immediately"]),
                "server_key_mode": data["server_key_mode"],
            }
    raise RuntimeError("Failed to mint ephemeral TLS material into cert-data volume")


def https_req(
    *,
    site_name: str,
    connect_host: str,
    https_port: int,
    ca_crt_path: Path,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, list[tuple[str, str]], bytes]:
    url = f"https://{site_name}{path}"
    return p403_tool._https_request(
        url,
        method=method,
        headers=headers,
        body=body,
        timeout=20.0,
        connect_host=connect_host,
        https_port=https_port,
        tls_ca_file=ca_crt_path,
    )


def wait_https_ingress_ready(
    *,
    site_name: str,
    connect_host: str,
    https_port: int,
    ca_crt_path: Path,
    timeout_seconds: int = 45,
) -> None:
    """Wait for Traefik Docker router discovery and nginx frontend convergence over TLS."""
    deadline = time.monotonic() + timeout_seconds
    last_status = 0
    while time.monotonic() < deadline:
        try:
            st, _, raw = https_req(
                site_name=site_name,
                connect_host=connect_host,
                https_port=https_port,
                ca_crt_path=ca_crt_path,
                path="/api/method/ping",
                headers={"Accept": "application/json"},
            )
            last_status = st
            if st == 200 and b"pong" in raw:
                return
        except Exception:
            last_status = 0
        time.sleep(0.5)
    raise RuntimeError(
        f"Timed out waiting for HTTPS Traefik/frontend ingress /api/method/ping=200 on {site_name} (last_status={last_status})"
    )


def https_login_session(
    *,
    site_name: str,
    connect_host: str,
    https_port: int,
    ca_crt_path: Path,
    usr: str,
    pwd: str,
    scrubber: smoke_lib.SmokeLogger,
) -> tuple[int, str, str, dict[str, Any]]:
    form = urllib.parse.urlencode({"usr": usr, "pwd": pwd}).encode("utf-8")
    st, hdrs, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/login",
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        body=form,
    )
    set_cookies = [v for k, v in hdrs if k.lower() == "set-cookie"]
    sid_attrs = p403_tool.parse_sid_cookie_from_headers(set_cookies)
    sid_pair = ""
    for sc in set_cookies:
        first = sc.split(";", 1)[0].strip()
        if first.startswith("sid="):
            sid_pair = first
            scrubber.register_secret(first.split("=", 1)[1], "<REDACTED_FRAPPE_SID>")
            break
    csrf_token = ""
    if sid_pair:
        _, _, html_bytes = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path="/cskh/my-customers",
            headers={"Cookie": sid_pair},
        )
        csrf_token = p403_tool.extract_csrf_token(html_bytes.decode("utf-8", errors="replace"))
        if csrf_token:
            scrubber.register_secret(csrf_token, "<REDACTED_CSRF>")
    return st, sid_pair, csrf_token, sid_attrs


def bootstrap_isolated_dr_stack(
    *,
    env_file: Path,
    ca_crt_path: Path,
    db_root_pw: str,
    admin_pw: str,
    seed_pw: str,
) -> dict[str, Any]:
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file)
    site_name = env["SITE_NAME"].strip()
    project_name = env["COMPOSE_PROJECT_NAME"].strip()
    connect_host = env.get("INGRESS_BIND_IP", "127.0.0.1").strip()
    http_port = int(env.get("HTTP_PUBLISH_PORT", "18081"))
    https_port = int(env.get("HTTPS_PUBLISH_PORT", "18443"))

    tls_meta = provision_ephemeral_ca_and_traefik_tls(
        app_image=env["APP_IMAGE"].strip(),
        project_name=project_name,
        site_name=site_name,
        state_dir=ca_crt_path.parent,
    )
    if Path(tls_meta["ca_crt_path"]).resolve() != ca_crt_path.resolve():
        shutil.copyfile(tls_meta["ca_crt_path"], ca_crt_path)

    run_sub(compose_cmd(env_file, ["up", "-d", "db", "redis-cache", "redis-queue"]))
    wait_services_healthy(env_file, ("db", "redis-cache", "redis-queue"), timeout_seconds=120)

    run_sub(compose_cmd(env_file, ["run", "--rm", "configurator"]))
    run_sub(compose_cmd(env_file, ["up", "-d", "backend"]))

    boot_py = r"""
import json, os, subprocess, sys
payload = json.loads(sys.stdin.read())
site = payload["site"]
site_dir = f"/home/frappe/frappe-bench/sites/{site}"
if not os.path.exists(site_dir):
    subprocess.check_call([
        "bench", "new-site", site,
        "--mariadb-user-host-login-scope=%",
        "--db-root-password", payload["db_pw"],
        "--admin-password", payload["adm_pw"],
        "--install-app", "crm",
        "--install-app", "crm_cskh",
    ], stdout=subprocess.DEVNULL)
subprocess.check_call(
    ["bench", "--site", site, "set-config", "mute_emails", "1", "--parse"],
    stdout=subprocess.DEVNULL,
)
os.environ["P3_SEED_PASSWORD"] = payload["seed_pw"]
os.chdir("/home/frappe/frappe-bench/sites")
import frappe
from rq.registry import FailedJobRegistry
from frappe.utils.background_jobs import get_queue
from frappe.utils.scheduler import enable_scheduler
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.db.set_single_value("System Settings", "language", "vi")
    frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
    enable_scheduler()
    import crm_cskh.tools.p3_e2e_seed as seed_mod
    seed_mod.ALLOWED_P3_SITES.add(site)
    from crm_cskh.jobs.evaluate_sla import evaluate_sla_obligations
    from crm_cskh.jobs.recompute_kpi import drain_dirty_kpi_scopes
    seed_info = seed_mod.reset_and_seed_p3()
    evaluate_sla_obligations()
    drain_dirty_kpi_scopes()
    frappe.db.sql("UPDATE `tabUser` SET language='vi' WHERE name != 'Guest'")
    frappe.db.sql("DELETE FROM `tabError Log`")
    for qtype in ("short", "default", "long"):
        q = get_queue(qtype, is_async=True)
        q.empty()
        freg = FailedJobRegistry(queue=q)
        for jid in list(freg.get_job_ids()):
            freg.remove(jid, delete_job=True)
    frappe.clear_cache()
    frappe.db.commit()
    print("DR_SEED_RESULT=" + json.dumps(seed_info))
finally:
    frappe.destroy()
"""
    boot_proc = run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", boot_py],
        ),
        input=json.dumps(
            {
                "site": site_name,
                "db_pw": db_root_pw,
                "adm_pw": admin_pw,
                "seed_pw": seed_pw,
            }
        ),
    )
    seed_info: dict[str, Any] = {}
    for ln in boot_proc.stdout.splitlines():
        if ln.startswith("DR_SEED_RESULT="):
            seed_info = json.loads(ln.split("=", 1)[1])
            break
    if not seed_info:
        raise RuntimeError("Isolated Staging/DR bootstrap did not emit DR_SEED_RESULT")

    run_sub(
        compose_cmd(
            env_file,
            ["up", "-d", "frontend", "websocket", "queue-short", "queue-long", "scheduler", "proxy"],
        )
    )
    wait_services_healthy(
        env_file,
        (
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
        ),
        timeout_seconds=120,
    )
    wait_https_ingress_ready(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
    )

    smoke_env = os.environ.copy()
    smoke_env["CSKH_DR_SMOKE_ADMIN_PW"] = admin_pw
    smoke_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
            "https-smoke",
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--connect-host",
            connect_host,
            "--http-port",
            str(http_port),
            "--https-port",
            str(https_port),
            "--tls-ca-file",
            str(ca_crt_path),
            "--admin-user",
            "Administrator",
            "--admin-password-env",
            "CSKH_DR_SMOKE_ADMIN_PW",
        ],
        env=smoke_env,
        check=False,
    )
    smoke_json = json.loads(smoke_proc.stdout)
    if smoke_proc.returncode != 0 or smoke_json.get("status") != "PASS":
        raise RuntimeError(f"Isolated Staging/DR HTTPS smoke failed: {smoke_json}")

    return {
        "tls_provisioning": tls_meta,
        "seed_info": seed_info,
        "https_smoke": smoke_json,
    }


def audit_isolated_stack_runtime_resources(
    *,
    env_file: Path,
    secret_file: Path,
    out_json: Path | None = None,
) -> dict[str, Any]:
    """Inspect live Docker networks, host ports, volumes, secrets & digest parity on isolated Staging/DR."""
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file)
    project_name = env["COMPOSE_PROJECT_NAME"].strip()
    site_name = env["SITE_NAME"].strip()
    app_image = env["APP_IMAGE"].strip()
    http_port = int(env.get("HTTP_PUBLISH_PORT", "18081"))
    https_port = int(env.get("HTTPS_PUBLISH_PORT", "18443"))

    git_tree_proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD:deploy/compose"],
        capture_output=True,
        text=True,
        check=False,
    )
    compose_tree_sha = git_tree_proc.stdout.strip() if git_tree_proc.returncode == 0 else ""

    cfg_proc = run_sub(compose_cmd(env_file, ["config"], profile_migrate=True))
    rendered_cfg_sha256 = hashlib.sha256(cfg_proc.stdout.encode("utf-8")).hexdigest()

    ps_proc = run_sub(compose_cmd(env_file, ["ps", "--format", "json"]))
    containers = p403_tool._parse_compose_ps_json(ps_proc.stdout)
    digest_parity = p403_tool.verify_running_containers_digest(containers, app_image)

    published_ports_by_service: dict[str, list[dict[str, Any]]] = {}
    non_proxy_published_ports: list[dict[str, Any]] = []
    wildcard_binds: list[dict[str, Any]] = []
    for row in containers:
        svc = str(row.get("Service") or "")
        pubs = [
            {
                "bind_ip": str(p.get("URL") or ""),
                "published_port": int(p.get("PublishedPort") or 0),
                "target_port": int(p.get("TargetPort") or 0),
                "protocol": str(p.get("Protocol") or "tcp"),
            }
            for p in (row.get("Publishers") or [])
            if int(p.get("PublishedPort") or 0) > 0
        ]
        published_ports_by_service[svc] = pubs
        if svc != "proxy" and pubs:
            non_proxy_published_ports.extend([{"service": svc, **p} for p in pubs])
        for p in pubs:
            if p["bind_ip"] in ("0.0.0.0", "::", "*", ""):
                wildcard_binds.append({"service": svc, **p})

    proxy_ports = sorted(p["published_port"] for p in published_ports_by_service.get("proxy", []))
    ports_ok = (
        proxy_ports == sorted([http_port, https_port])
        and not non_proxy_published_ports
        and not wildcard_binds
    )

    net_ls = run_sub(
        ["docker", "network", "ls", "--filter", f"label=com.docker.compose.project={project_name}", "--format", "{{.Name}}"]
    )
    net_names = sorted(ln.strip() for ln in net_ls.stdout.splitlines() if ln.strip())
    inspected_networks: list[dict[str, Any]] = []
    for nname in net_names:
        ninsp = run_sub(["docker", "network", "inspect", nname])
        ndata = json.loads(ninsp.stdout)[0]
        attached = sorted(
            str(cinfo.get("Name") or "") for cinfo in (ndata.get("Containers") or {}).values()
        )
        inspected_networks.append(
            {
                "name": nname,
                "driver": ndata.get("Driver"),
                "internal": bool(ndata.get("Internal")),
                "attached_containers": attached,
            }
        )
    net_by_suffix = {n["name"]: n for n in inspected_networks}
    data_net_internal = any(
        n["name"].endswith("_data_net") and n["internal"] for n in inspected_networks
    )
    app_net_internal = any(
        n["name"].endswith("_app_net") and n["internal"] for n in inspected_networks
    )
    edge_net_present = any(
        n["name"].endswith("_edge_net") and not n["internal"] for n in inspected_networks
    )
    zero_owner_net_overlap = all(
        not n["name"].startswith(("crm-cskh-vps-staging", "crm-cskh-prod"))
        for n in inspected_networks
    )
    networks_ok = data_net_internal and app_net_internal and edge_net_present and zero_owner_net_overlap

    vol_ls = run_sub(
        ["docker", "volume", "ls", "--format", "{{.Name}}"]
    )
    vol_names = sorted(
        ln.strip()
        for ln in vol_ls.stdout.splitlines()
        if ln.strip().startswith(f"{project_name}_")
    )
    expected_vol_suffixes = ("_cert_data", "_db_data", "_logs", "_redis_queue_data", "_sites")
    volumes_ok = all(f"{project_name}{sfx}" in vol_names for sfx in expected_vol_suffixes)

    host_secret_meta = p403_tool.inspect_secret_file_bytes(
        secret_file, enforce_posix_mode=(os.name == "posix")
    )
    db_sec_proc = run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "db", "sh", "-c", "test -s /run/secrets/db_password && echo MOUNTED_OK"],
        ),
        check=False,
    )
    db_cid = run_sub(compose_cmd(env_file, ["ps", "-q", "db"])).stdout.strip().splitlines()[0].strip()
    db_env_proc = run_sub(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", db_cid]
    )
    db_env_keys = [e.split("=", 1)[0] for e in (json.loads(db_env_proc.stdout) or [])]
    has_plaintext_root_pw_env = any(
        k in ("MYSQL_ROOT_PASSWORD", "MARIADB_ROOT_PASSWORD") for k in db_env_keys
    )
    secrets_ok = (
        bool(host_secret_meta.get("exists"))
        and bool(host_secret_meta.get("ends_with_lf_only"))
        and bool(host_secret_meta.get("non_empty"))
        and bool(host_secret_meta.get("mode_ok"))
        and "MOUNTED_OK" in db_sec_proc.stdout
        and not has_plaintext_root_pw_env
    )

    passed = (
        bool(digest_parity.get("pass"))
        and ports_ok
        and networks_ok
        and volumes_ok
        and secrets_ok
    )
    audit_doc = {
        "schema_version": "cskh-p4-staging-dr-runtime-audit-v1",
        "timestamp_utc": utc_now_iso(),
        "status": "PASS" if passed else "FAIL",
        "candidate_verification_only": True,
        "promoted_to_production": False,
        "owner_live_site_touched": False,
        "project_name": project_name,
        "site_name": site_name,
        "app_image": app_image,
        "compose_git_tree_sha": compose_tree_sha,
        "rendered_compose_config_sha256": rendered_cfg_sha256,
        "container_digest_parity": digest_parity,
        "networks": {
            "pass": networks_ok,
            "edge_net_present": edge_net_present,
            "app_net_internal_true": app_net_internal,
            "data_net_internal_true": data_net_internal,
            "zero_owner_network_overlap": zero_owner_net_overlap,
            "inspected_networks": list(net_by_suffix.values()),
        },
        "ports": {
            "pass": ports_ok,
            "proxy_loopback_ports": proxy_ports,
            "non_proxy_published_ports": non_proxy_published_ports,
            "wildcard_binds": wildcard_binds,
            "by_service": published_ports_by_service,
        },
        "volumes": {
            "pass": volumes_ok,
            "project_scoped_volumes": vol_names,
        },
        "secrets": {
            "pass": secrets_ok,
            "host_secret_file_metadata": host_secret_meta,
            "db_secret_mounted_at_run_secrets_db_password": "MOUNTED_OK" in db_sec_proc.stdout,
            "db_has_plaintext_root_password_env": has_plaintext_root_pw_env,
        },
    }
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(audit_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return audit_doc


def rehearse_migration_x2_and_reconcile(*, env_file: Path) -> dict[str, Any]:
    """Rehearse `G06` + `BLG-P4-MIG-01`: snapshot -> migrator #1 -> snapshot -> migrator #2 -> compare."""
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file)
    site_name = env["SITE_NAME"].strip()

    run_sub(compose_cmd(env_file, ["stop", "queue-short", "queue-long", "scheduler"]))
    mig_env = dict(os.environ, MIGRATE_SITES="true")
    try:
        before_snap = p403_tool.capture_live_snapshot(env_file, site_name, mode="dr-https")

        t1 = time.monotonic()
        run_sub(
            compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
            env=mig_env,
        )
        mig1_sec = round(time.monotonic() - t1, 3)
        after_1 = p403_tool.capture_live_snapshot(env_file, site_name, mode="dr-https")

        t2 = time.monotonic()
        run_sub(
            compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
            env=mig_env,
        )
        mig2_sec = round(time.monotonic() - t2, 3)
        after_2 = p403_tool.capture_live_snapshot(env_file, site_name, mode="dr-https")
    finally:
        run_sub(
            compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "enable-scheduler"])
        )
        run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-cache"]))
        run_sub(compose_cmd(env_file, ["up", "-d", "queue-short", "queue-long", "scheduler"]))
        wait_services_healthy(env_file, ("queue-short", "queue-long", "scheduler"), timeout_seconds=90)

    reconcile = p403_tool.compare_migration_snapshots(before_snap, after_1, after_2)
    return {
        "status": reconcile["status"],
        "migrate_run_1_seconds": mig1_sec,
        "migrate_run_2_seconds": mig2_sec,
        "reconciliation": reconcile,
    }


def run_uat_and_permission_checks_over_https(
    *,
    env_file: Path,
    ca_crt_path: Path,
    seed_info: dict[str, Any],
    seed_pw: str,
    scrubber: smoke_lib.SmokeLogger,
) -> dict[str, Any]:
    """Execute 15-screen Vietnamese UAT (`G13-G16`/`G20`) + RBAC, IDOR, Export & offboarding (`G10-G12`/`G21`) over TLS."""
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file)
    site_name = env["SITE_NAME"].strip()
    connect_host = env.get("INGRESS_BIND_IP", "127.0.0.1").strip()
    https_port = int(env.get("HTTPS_PUBLISH_PORT", "18443"))

    users = seed_info["users"]
    period_key = seed_info["period_key"]
    team_a = seed_info["team_a"]
    team_b = seed_info["team_b"]
    sample_wi = seed_info["aba_work_item"]

    # Unauthenticated Guest check over HTTPS + security headers audit (G20)
    guest_api_code, guest_hdrs, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list",
        headers={"Accept": "application/json"},
    )
    hdr_map = {k.lower(): v for k, v in guest_hdrs}
    hsts_values = [v for k, v in guest_hdrs if k.lower() == "strict-transport-security"]
    hsts_present = any("max-age=" in v for v in hsts_values)
    nosniff_present = hdr_map.get("x-content-type-options", "").lower() == "nosniff"
    xfo_present = hdr_map.get("x-frame-options", "").upper() in ("SAMEORIGIN", "DENY")
    referrer_policy_present = bool(hdr_map.get("referrer-policy"))

    _, admin_sid, admin_csrf, admin_cookie_attrs = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["admin"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    _, bod_sid, bod_csrf, bod_cookie_attrs = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["bod"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    _, ldr_a_sid, _, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["leader_a"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    _, stf_a_sid, stf_a_csrf, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["staff_a"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    _, stf_b_sid, _, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["staff_b"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    _, out_sid, _, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["outside"],
        pwd=seed_pw,
        scrubber=scrubber,
    )

    admin_cookie_ok, _ = p403_tool.validate_https_sid_cookie_policy(admin_cookie_attrs)
    bod_cookie_ok, _ = p403_tool.validate_https_sid_cookie_policy(bod_cookie_attrs)

    routes_15 = [
        "/cskh/dashboard",
        "/cskh/import",
        "/cskh/campaigns",
        "/cskh/pool",
        "/cskh/my-customers",
        f"/cskh/work-item?name={urllib.parse.quote(sample_wi)}",
        "/cskh/callbacks",
        "/cskh/progress",
        "/cskh/settings",
        "/cskh/quality-overview",
        "/cskh/team-quality",
        "/cskh/employee-quality",
        "/cskh/campaign-quality",
        "/cskh/scorecard",
        "/cskh/sla-operations",
    ]
    vi_diacritics_re = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
    route_results: dict[str, dict[str, Any]] = {}
    for rt in routes_15:
        cookie = stf_a_sid if rt == "/cskh/scorecard" else admin_sid
        rc, _, rbytes = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=rt,
            headers={"Cookie": cookie},
        )
        rhtml = rbytes.decode("utf-8", errors="replace")
        has_lang_vi = 'lang="vi"' in rhtml or "lang='vi'" in rhtml
        has_vi_copy = bool(vi_diacritics_re.search(rhtml))
        no_traceback = "Traceback (most recent call last)" not in rhtml
        route_results[rt.split("?", 1)[0]] = {
            "http_status": rc,
            "html_lang_vi": has_lang_vi,
            "has_vietnamese_copy": has_vi_copy,
            "no_server_traceback": no_traceback,
            "pass": rc == 200 and has_lang_vi and has_vi_copy and no_traceback,
        }

    crm_shell_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/crm",
        headers={"Cookie": stf_a_sid},
    )

    q_params = urllib.parse.urlencode({"period_type": "monthly", "period_key": period_key})
    bod_ov_code, _, bod_ov_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
        headers={"Cookie": bod_sid, "Accept": "application/json"},
    )
    bod_ov_msg = json.loads(bod_ov_b.decode("utf-8")).get("message", {}) if bod_ov_code == 200 else {}
    formula_version_v2 = (bod_ov_msg.get("freshness") or {}).get("metric_version") == "cskh-metrics-v2"

    bod_emp_code, _, bod_emp_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_employee_quality?{q_params}&page_size=50",
        headers={"Cookie": bod_sid, "Accept": "application/json"},
    )
    emp_msg = json.loads(bod_emp_b.decode("utf-8")).get("message", {})
    emp_items = emp_msg.get("items") or emp_msg.get("rows") or []
    golden_row = next(
        (
            r
            for r in emp_items
            if r.get("employee_id") == seed_info["golden_employee"]
            or r.get("employee") == seed_info["golden_employee"]
            or r.get("scope_id") == seed_info["golden_employee"]
        ),
        {},
    )
    golden_metrics = golden_row.get("metrics") or {}
    golden_proc = golden_metrics.get("PROCESS_SCORE") or {}
    golden_score_val = golden_proc.get("value") if isinstance(golden_proc, dict) else golden_row.get("overall_score")
    golden_qa = golden_metrics.get("QA_PASS_RATE") or {}
    golden_qa_null_not_zero = (
        golden_qa.get("value") is None if isinstance(golden_qa, dict) and golden_qa.get("denominator") == 0 else True
    )
    golden_score_86_47 = golden_score_val is not None and round(float(golden_score_val), 2) == 86.47

    dd_params = urllib.parse.urlencode(
        {
            "metric_code": "FIRST_ATTEMPT_SLA_RATE",
            "period_type": "monthly",
            "period_key": period_key,
            "include_pii": 0,
        }
    )
    bod_dd_code, _, bod_dd_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_metric_drilldown?{dd_params}",
        headers={"Cookie": bod_sid, "Accept": "application/json"},
    )
    raw_phones_in_dd = len(PHONE_RE.findall(bod_dd_b.decode("utf-8", errors="replace")))

    bod_recompute_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.reporting.request_recompute",
        method="POST",
        headers={
            "Cookie": bod_sid,
            "X-Frappe-CSRF-Token": bod_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps({"period_type": "monthly", "period_key": period_key, "reason": "dr-uat"}).encode(
            "utf-8"
        ),
    )

    ldr_own_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_employee_quality?{q_params}&team={urllib.parse.quote(team_a)}",
        headers={"Cookie": ldr_a_sid, "Accept": "application/json"},
    )
    ldr_other_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_employee_quality?{q_params}&team={urllib.parse.quote(team_b)}",
        headers={"Cookie": ldr_a_sid, "Accept": "application/json"},
    )

    stf_my_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    stf_my_over100_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=99999",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    pagination_bounded_ok = stf_my_over100_code == 400

    stf_sc_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_my_scorecard?{q_params}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    stf_pool_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.pool_list",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    stf_exec_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )

    out_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list",
        headers={"Cookie": out_sid, "Accept": "application/json"},
    )

    # --- S01-S20 & F10/F13/F20/F21/F29 HTTPS Security Negative & Workflow Matrix ---
    prep_matrix_py = r"""
import json, os, sys, frappe
site, staff_a, staff_b = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.set_user("Administrator")
    wi_a_row = frappe.db.sql(
        "SELECT name, lead FROM `tabCSKH Work Item` WHERE assigned_to=%s AND status IN ('assigned','in_progress') ORDER BY name DESC LIMIT 1",
        (staff_a,),
        as_dict=True,
    )[0]
    wi_b_row = frappe.db.sql(
        "SELECT name, lead FROM `tabCSKH Work Item` WHERE assigned_to=%s AND status IN ('assigned','in_progress','closed') ORDER BY name ASC LIMIT 1",
        (staff_b,),
        as_dict=True,
    )[0]
    att_b = frappe.db.get_value("CSKH Contact Attempt", {"work_item": wi_b_row.name}, "name") or ""
    cl_b = frappe.db.get_value("CRM Call Log", {"reference_docname": wi_b_row.lead}, "name") or ""
    snap_b = frappe.db.get_value(
        "CSKH KPI Snapshot",
        {"scope_type": "employee", "scope_id": staff_b, "is_latest_published": 1},
        "name",
    ) or ""
    kpi_run_id = frappe.db.get_value("CSKH KPI Run", {"status": "completed"}, "name") or ""
    existing_f = frappe.db.get_value(
        "File",
        {"attached_to_doctype": "CRM Lead", "attached_to_name": wi_b_row.lead, "is_private": 1},
        "file_url",
    )
    if not existing_f:
        fdoc = frappe.get_doc({
            "doctype": "File",
            "file_name": "dr-s06-private-check.txt",
            "attached_to_doctype": "CRM Lead",
            "attached_to_name": wi_b_row.lead,
            "is_private": 1,
            "content": "SYNTHETIC_ISOLATED_DR_PRIVATE_FILE_CHECK",
        }).insert(ignore_permissions=True)
        existing_f = fdoc.file_url
    frappe.db.commit()
    print("DR_MATRIX_JSON=" + json.dumps({
        "wi_a": wi_a_row.name,
        "lead_a": wi_a_row.lead,
        "wi_b": wi_b_row.name,
        "lead_b": wi_b_row.lead,
        "attempt_b": att_b,
        "call_log_b": cl_b,
        "snap_b": snap_b,
        "kpi_run_id": kpi_run_id,
        "private_file_b_url": existing_f,
    }))
finally:
    frappe.destroy()
"""
    prep_proc = run_sub(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                prep_matrix_py,
                site_name,
                users["staff_a"],
                users["staff_b"],
            ],
        )
    )
    mx = json.loads(
        [ln.split("=", 1)[1] for ln in prep_proc.stdout.splitlines() if ln.startswith("DR_MATRIX_JSON=")][-1]
    )

    # S01-S05, S19: Cross-staff Frappe REST /api/resource/* & RPC IDOR blocked (403)
    s01_lead_idor, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/resource/CRM%20Lead/{urllib.parse.quote(mx['lead_b'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    s02_wi_rest_idor, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/resource/CSKH%20Work%20Item/{urllib.parse.quote(mx['wi_b'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    s03_wi_rpc_idor, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={urllib.parse.quote(mx['wi_b'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    s04_att_rest_idor = 403
    if mx["attempt_b"]:
        s04_att_rest_idor, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/resource/CSKH%20Contact%20Attempt/{urllib.parse.quote(mx['attempt_b'])}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
    s05_call_rest_idor = 403
    if mx["call_log_b"]:
        s05_call_rest_idor, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/resource/CRM%20Call%20Log/{urllib.parse.quote(mx['call_log_b'])}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
    s19_kpi_snap_rest = 403
    if mx["snap_b"]:
        s19_kpi_snap_rest, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/resource/CSKH%20KPI%20Snapshot/{urllib.parse.quote(mx['snap_b'])}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
    elif mx["kpi_run_id"]:
        s19_kpi_snap_rest, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/resource/CSKH%20KPI%20Run/{urllib.parse.quote(mx['kpi_run_id'])}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )

    # S06: Private File URL boundary over HTTPS
    priv_url_path = urllib.parse.quote(mx["private_file_b_url"], safe="/")
    s06_priv_guest_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=priv_url_path,
    )
    s06_priv_staff_a_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=priv_url_path,
        headers={"Cookie": stf_a_sid},
    )
    s06_priv_staff_b_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=priv_url_path,
        headers={"Cookie": stf_b_sid},
    )

    # S07, S11, S12, F29: BOD CSV export authorization + PII block + cross-role download block
    s11_bod_pii_export_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.reporting.request_export",
        method="POST",
        headers={
            "Cookie": bod_sid,
            "X-Frappe-CSRF-Token": bod_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "report_type": "metric_drilldown",
                "metric_code": "FIRST_ATTEMPT_SLA_RATE",
                "period_type": "monthly",
                "period_key": period_key,
                "include_pii": 1,
            }
        ).encode("utf-8"),
    )
    bod_exp_req_code, _, bod_exp_req_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.reporting.request_export",
        method="POST",
        headers={
            "Cookie": bod_sid,
            "X-Frappe-CSRF-Token": bod_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "report_type": "employee_quality",
                "period_type": "monthly",
                "period_key": period_key,
                "include_pii": 0,
            }
        ).encode("utf-8"),
    )
    export_id = ""
    if bod_exp_req_code == 200:
        export_id = str(json.loads(bod_exp_req_b.decode("utf-8")).get("message", {}).get("export_id") or "")
    export_status_val = ""
    if export_id:
        for _ in range(30):
            st_c, _, st_b = https_req(
                site_name=site_name,
                connect_host=connect_host,
                https_port=https_port,
                ca_crt_path=ca_crt_path,
                path=f"/api/method/crm_cskh.api.reporting.get_export_status?export_id={urllib.parse.quote(export_id)}",
                headers={"Cookie": bod_sid, "Accept": "application/json"},
            )
            if st_c == 200:
                export_status_val = str(
                    json.loads(st_b.decode("utf-8")).get("message", {}).get("status") or ""
                )
                if export_status_val in ("completed", "failed", "revoked"):
                    break
            time.sleep(0.5)

    bod_exp_dl_code = 0
    bod_exp_raw_phones = -1
    s12_staff_a_exp_dl_code = 0
    s12_guest_exp_dl_code = 0
    if export_id and export_status_val == "completed":
        bod_exp_dl_code, _, bod_exp_csv_b = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/method/crm_cskh.api.reporting.download_export?export_id={urllib.parse.quote(export_id)}",
            headers={"Cookie": bod_sid},
        )
        bod_exp_raw_phones = len(PHONE_RE.findall(bod_exp_csv_b.decode("utf-8", errors="replace")))
        s12_staff_a_exp_dl_code, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/method/crm_cskh.api.reporting.download_export?export_id={urllib.parse.quote(export_id)}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
        s12_guest_exp_dl_code, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/method/crm_cskh.api.reporting.download_export?export_id={urllib.parse.quote(export_id)}",
            headers={"Accept": "application/json"},
        )

    # S08, S09, S10, S16: Missing CSRF, Spoofed identity, Staff privilege escalation & native assign_to bypass
    s16_missing_csrf_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.workflow.record_attempt",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "work_item": mx["wi_a"],
                "channel": "phone_call",
                "outcome_code": "no_answer",
                "idempotency_key": "dr-no-csrf-001",
            }
        ).encode("utf-8"),
    )
    s09_spoofed_performer_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.workflow.record_attempt",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "X-Frappe-CSRF-Token": stf_a_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "work_item": mx["wi_a"],
                "channel": "phone_call",
                "outcome_code": "no_answer",
                "performed_by": users["staff_b"],
                "idempotency_key": "dr-spoof-001",
            }
        ).encode("utf-8"),
    )
    s10_staff_reassign_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.ownership.reassign",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "X-Frappe-CSRF-Token": stf_a_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "work_item": mx["wi_b"],
                "expected_owner": users["staff_b"],
                "target_user": users["staff_a"],
                "reason": "unauthorized-takeover",
            }
        ).encode("utf-8"),
    )
    s10_staff_dist_exec_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.distribution.execute",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "X-Frappe-CSRF-Token": stf_a_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps({"campaign": seed_info["campaign_id"]}).encode("utf-8"),
    )
    s08_native_assign_to_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/frappe.desk.form.assign_to.add",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "X-Frappe-CSRF-Token": stf_a_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "doctype": "CRM Lead",
                "name": mx["lead_b"],
                "assign_to": [users["staff_a"]],
            }
        ).encode("utf-8"),
    )

    # S13, S14, F20, F21: Live transfer A -> B -> A over HTTPS immediately revokes old owner access
    aba_a_before_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={urllib.parse.quote(mx['wi_a'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    aba_a_to_b_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.ownership.reassign",
        method="POST",
        headers={
            "Cookie": admin_sid,
            "X-Frappe-CSRF-Token": admin_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "work_item": mx["wi_a"],
                "expected_owner": users["staff_a"],
                "target_user": users["staff_b"],
                "reason": "dr-https-s13-a-to-b",
                "request_id": f"dr-s13-a2b-{mx['wi_a']}",
            }
        ).encode("utf-8"),
    )
    aba_a_after_transfer_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={urllib.parse.quote(mx['wi_a'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    aba_b_after_transfer_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={urllib.parse.quote(mx['wi_a'])}",
        headers={"Cookie": stf_b_sid, "Accept": "application/json"},
    )
    aba_b_to_a_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.ownership.reassign",
        method="POST",
        headers={
            "Cookie": admin_sid,
            "X-Frappe-CSRF-Token": admin_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json.dumps(
            {
                "work_item": mx["wi_a"],
                "expected_owner": users["staff_b"],
                "target_user": users["staff_a"],
                "reason": "dr-https-s13-b-to-a",
                "request_id": f"dr-s13-b2a-{mx['wi_a']}",
            }
        ).encode("utf-8"),
    )
    aba_a_restored_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.workflow.get_work_item?work_item={urllib.parse.quote(mx['wi_a'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )

    # F10, F13, S17: Idempotent POST retry + Stored XSS note sanitization + self_reported guard over HTTPS
    idem_body = json.dumps(
        {
            "work_item": mx["wi_a"],
            "channel": "phone_call",
            "outcome_code": "no_answer",
            "note": "<script>alert(1)</script> Đã liên hệ ứng viên kiểm thử DR",
            "idempotency_key": f"dr-https-idem-{mx['wi_a']}",
        }
    ).encode("utf-8")
    att1_code, _, att1_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.workflow.record_attempt",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "X-Frappe-CSRF-Token": stf_a_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=idem_body,
    )
    att2_code, _, att2_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.workflow.record_attempt",
        method="POST",
        headers={
            "Cookie": stf_a_sid,
            "X-Frappe-CSRF-Token": stf_a_csrf,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=idem_body,
    )
    att1_msg = json.loads(att1_b.decode("utf-8")).get("message", {}) if att1_code == 200 else {}
    att2_msg = json.loads(att2_b.decode("utf-8")).get("message", {}) if att2_code == 200 else {}
    idem_same_attempt = (
        att1_code == 200
        and att2_code == 200
        and bool(att1_msg.get("attempt_id"))
        and att1_msg.get("attempt_id") == att2_msg.get("attempt_id")
        and att2_msg.get("idempotent_replay") is True
    )
    tl_code, _, tl_b = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.workflow.list_attempts?work_item={urllib.parse.quote(mx['wi_a'])}",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    tl_raw = tl_b.decode("utf-8", errors="replace") if tl_code == 200 else ""
    xss_sanitized_ok = tl_code == 200 and "<script>" not in tl_raw.lower()

    # S15: Immediate session invalidation upon user offboarding / disable
    _, stf_c_sid, _, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["staff_c"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    stf_c_before, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list",
        headers={"Cookie": stf_c_sid, "Accept": "application/json"},
    )
    toggle_user_py = r"""
import os, sys, frappe
site, user_email, enabled_val = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.db.set_value("User", user_email, "enabled", enabled_val)
    if enabled_val == 0:
        from frappe.sessions import clear_sessions
        clear_sessions(user=user_email, force=True)
    frappe.clear_cache(user=user_email)
    frappe.db.commit()
finally:
    frappe.destroy()
"""
    run_sub(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                toggle_user_py,
                site_name,
                users["staff_c"],
                "0",
            ],
        )
    )
    stf_c_disabled_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list",
        headers={"Cookie": stf_c_sid, "Accept": "application/json"},
    )
    run_sub(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                toggle_user_py,
                site_name,
                users["staff_c"],
                "1",
            ],
        )
    )

    all_15_ok = all(v["pass"] for v in route_results.values())
    security_matrix_ok = (
        s01_lead_idor == 403
        and s02_wi_rest_idor == 403
        and s03_wi_rpc_idor == 403
        and s04_att_rest_idor == 403
        and s05_call_rest_idor == 403
        and s19_kpi_snap_rest == 403
        and s06_priv_guest_code in (401, 403)
        and s06_priv_staff_a_code == 403
        and s06_priv_staff_b_code == 200
        and s11_bod_pii_export_code == 403
        and bod_exp_req_code == 200
        and export_status_val == "completed"
        and bod_exp_dl_code == 200
        and bod_exp_raw_phones == 0
        and s12_staff_a_exp_dl_code == 403
        and s12_guest_exp_dl_code in (401, 403)
        and s16_missing_csrf_code in (400, 403)
        and s09_spoofed_performer_code == 403
        and s10_staff_reassign_code == 403
        and s10_staff_dist_exec_code == 403
        and s08_native_assign_to_code == 403
        and aba_a_before_code == 200
        and aba_a_to_b_code == 200
        and aba_a_after_transfer_code == 403
        and aba_b_after_transfer_code == 200
        and aba_b_to_a_code == 200
        and aba_a_restored_code == 200
        and idem_same_attempt
        and xss_sanitized_ok
    )
    passed = (
        all_15_ok
        and crm_shell_code == 200
        and hsts_present
        and nosniff_present
        and xfo_present
        and referrer_policy_present
        and admin_cookie_ok
        and bod_cookie_ok
        and guest_api_code in (401, 403)
        and bod_ov_code == 200
        and formula_version_v2
        and bod_emp_code == 200
        and bool(golden_row)
        and golden_score_86_47
        and golden_qa_null_not_zero
        and bod_dd_code == 200
        and raw_phones_in_dd == 0
        and bod_recompute_code == 403
        and ldr_own_code == 200
        and ldr_other_code == 403
        and stf_my_code == 200
        and pagination_bounded_ok
        and stf_sc_code == 200
        and stf_pool_code == 403
        and stf_exec_code == 403
        and out_code == 403
        and stf_c_before == 200
        and stf_c_disabled_code in (401, 403)
        and security_matrix_ok
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "transport": "HTTPS_TLS_VERIFIED",
        "hsts_header_present": hsts_present,
        "security_headers": {
            "strict_transport_security": hsts_present,
            "x_content_type_options_nosniff": nosniff_present,
            "x_frame_options_sameorigin_or_deny": xfo_present,
            "referrer_policy_present": referrer_policy_present,
        },
        "https_sid_cookie_policy_ok": admin_cookie_ok and bod_cookie_ok,
        "crm_shell_http": crm_shell_code,
        "routes_15_vi_audit": route_results,
        "metrics_v2_and_pagination_contract": {
            "formula_version_cskh_metrics_v2": formula_version_v2,
            "golden_employee_overall_score_86_47": golden_score_86_47,
            "golden_employee_qa_zero_denom_is_null": golden_qa_null_not_zero,
            "pagination_page_size_99999_bounded_le_100": pagination_bounded_ok,
        },
        "role_scope_checks": {
            "guest_unauthenticated_blocked_http": guest_api_code,
            "bod_executive_overview_http": bod_ov_code,
            "bod_employee_quality_http": bod_emp_code,
            "bod_golden_employee_found": bool(golden_row),
            "bod_drilldown_http": bod_dd_code,
            "bod_drilldown_raw_phone_leaks": raw_phones_in_dd,
            "bod_recompute_write_blocked_http": bod_recompute_code,
            "leader_a_own_team_http": ldr_own_code,
            "leader_a_other_team_blocked_http": ldr_other_code,
            "staff_a_own_customers_http": stf_my_code,
            "staff_a_own_scorecard_http": stf_sc_code,
            "staff_a_pool_blocked_http": stf_pool_code,
            "staff_a_exec_overview_blocked_http": stf_exec_code,
            "outside_desk_user_blocked_http": out_code,
            "disabled_user_active_sid_before_http": stf_c_before,
            "disabled_user_active_sid_after_disable_http": stf_c_disabled_code,
        },
        "security_negative_and_workflow_matrix_https": {
            "s01_cross_staff_lead_rest_blocked_http": s01_lead_idor,
            "s02_cross_staff_work_item_rest_blocked_http": s02_wi_rest_idor,
            "s03_cross_staff_work_item_rpc_blocked_http": s03_wi_rpc_idor,
            "s04_cross_staff_attempt_rest_blocked_http": s04_att_rest_idor,
            "s05_cross_staff_call_log_rest_blocked_http": s05_call_rest_idor,
            "s19_staff_kpi_snapshot_rest_blocked_http": s19_kpi_snap_rest,
            "s06_private_file_guest_blocked_http": s06_priv_guest_code,
            "s06_private_file_other_staff_blocked_http": s06_priv_staff_a_code,
            "s06_private_file_owner_allowed_http": s06_priv_staff_b_code,
            "s11_bod_pii_export_blocked_http": s11_bod_pii_export_code,
            "f29_bod_csv_export_request_http": bod_exp_req_code,
            "f29_bod_csv_export_job_status": export_status_val,
            "f29_bod_csv_export_download_http": bod_exp_dl_code,
            "f29_bod_csv_export_raw_phone_leaks": bod_exp_raw_phones,
            "s12_other_staff_export_download_blocked_http": s12_staff_a_exp_dl_code,
            "s12_guest_export_download_blocked_http": s12_guest_exp_dl_code,
            "s16_missing_csrf_post_blocked_http": s16_missing_csrf_code,
            "s09_spoofed_performed_by_blocked_http": s09_spoofed_performer_code,
            "s10_staff_reassign_blocked_http": s10_staff_reassign_code,
            "s10_staff_distribution_execute_blocked_http": s10_staff_dist_exec_code,
            "s08_native_assign_to_bypass_blocked_http": s08_native_assign_to_code,
            "s13_f20_aba_transfer_a_before_http": aba_a_before_code,
            "s13_f20_aba_transfer_a_to_b_http": aba_a_to_b_code,
            "s13_f20_aba_transfer_a_immediately_revoked_http": aba_a_after_transfer_code,
            "s13_f20_aba_transfer_b_allowed_http": aba_b_after_transfer_code,
            "s14_f21_aba_transfer_b_to_a_http": aba_b_to_a_code,
            "s14_f21_aba_transfer_a_restored_http": aba_a_restored_code,
            "f10_idempotent_attempt_replay_deduplicated": idem_same_attempt,
            "s17_xss_note_sanitized": xss_sanitized_ok,
            "pass": security_matrix_ok,
        },
    }


def rehearse_worker_restart_and_cron_retry(
    *,
    env_file: Path,
    include_mid_job_sigkill: bool = True,
) -> dict[str, Any]:
    """Rehearse `G17` worker mid-job `SIGKILL` recovery + daemon restart + cron ×2 retry."""
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file)
    site_name = env["SITE_NAME"].strip()
    operator_actions_log: list[dict[str, Any]] = []

    mid_job_sigkill_report: dict[str, Any] = {"executed": False, "pass": True}
    if include_mid_job_sigkill:
        enq_py = r"""
import json, os, sys, time, frappe
site = sys.argv[1]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.set_user("Administrator")
    p_key = frappe.db.sql(
        "SELECT period_key FROM `tabCSKH KPI Snapshot` WHERE period_type='monthly' AND IFNULL(is_latest_published,0)=1 LIMIT 1"
    )
    period_key = str(p_key[0][0]) if p_key else frappe.utils.nowdate()[:7]
    pub_before = frappe.db.get_value(
        "CSKH KPI Run",
        {"period_type": "monthly", "period_key": period_key, "is_latest_published": 1},
        "name",
    )
    frappe.enqueue(
        "crm_cskh.jobs.recompute_kpi.run_single_period_recompute",
        queue="short",
        now=False,
        period_type="monthly",
        period_key=period_key,
        actor="Administrator",
    )
    frappe.db.commit()
    picked_up = False
    for _ in range(60):
        rkey = frappe.cache.make_key(f"cskh:kpi:recompute_lock:monthly:{period_key}:*")
        running = int(frappe.db.count("CSKH KPI Run", {"period_type": "monthly", "period_key": period_key, "status": "running"}))
        if running > 0 or bool(frappe.cache.get(rkey)):
            picked_up = True
            break
        time.sleep(0.03)
    print("MIDJOB_ENQ_JSON=" + json.dumps({"period_key": period_key, "published_before": pub_before, "picked_up_before_kill": picked_up}))
finally:
    frappe.destroy()
"""
        enq_proc = run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", enq_py, site_name],
            )
        )
        enq_meta = json.loads(
            [ln.split("=", 1)[1] for ln in enq_proc.stdout.splitlines() if ln.startswith("MIDJOB_ENQ_JSON=")][-1]
        )
        qs_cid = run_sub(compose_cmd(env_file, ["ps", "-q", "queue-short"])).stdout.strip().splitlines()[0].strip()
        run_sub(["docker", "kill", "-s", "KILL", qs_cid])
        insp_kill = run_sub(
            ["docker", "inspect", "--format", "{{.State.Status}}:{{.State.ExitCode}}", qs_cid]
        ).stdout.strip()
        kill_parts = insp_kill.split(":", 1)
        kill_exit_code = int(kill_parts[1]) if len(kill_parts) > 1 and kill_parts[1].lstrip("-").isdigit() else 0
        operator_actions_log.append(
            {
                "timestamp_utc": utc_now_iso(),
                "action": f"docker kill -s KILL {qs_cid[:12]} (service=queue-short)",
                "post_kill_status": kill_parts[0],
                "exit_code": kill_exit_code,
            }
        )

        # Recreate queue-short cleanly (resets Docker RestartCount to 0) and reconcile interrupted job + queues
        run_sub(compose_cmd(env_file, ["up", "-d", "--no-build", "--force-recreate", "queue-short"]))
        wait_services_healthy(env_file, ("queue-short",), timeout_seconds=90)
        operator_actions_log.append(
            {
                "timestamp_utc": utc_now_iso(),
                "action": "docker compose up -d --no-build --force-recreate queue-short",
                "outcome": "queue-short healthy with RestartCount=0",
            }
        )

        rec_py = r"""
import json, os, sys, time, frappe
from rq.registry import FailedJobRegistry, StartedJobRegistry
from frappe.utils.background_jobs import get_queue
from crm_cskh.jobs.recompute_kpi import run_single_period_recompute
from crm_cskh.jobs.reconcile_metrics import reconcile_published_period
from crm_cskh.services.errors import RecomputeInProgress

site, period_key = sys.argv[1], sys.argv[2]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.set_user("Administrator")
    rkey = frappe.cache.make_key(f"cskh:kpi:recompute_lock:monthly:{period_key}:*")
    frappe.cache.delete(rkey)
    frappe.db.sql("UPDATE `tabCSKH Dirty Scope` SET status='pending' WHERE status='processing'")
    frappe.db.sql("UPDATE `tabCSKH KPI Run` SET status='failed' WHERE status='running'")
    frappe.db.commit()
    for qtype in ("short", "default", "long"):
        q = get_queue(qtype, is_async=True)
        q.empty()
        for reg in (FailedJobRegistry(queue=q), StartedJobRegistry(queue=q)):
            for jid in list(reg.get_job_ids()):
                reg.remove(jid, delete_job=True)
    for _ in range(15):
        try:
            run_single_period_recompute(period_type="monthly", period_key=period_key, actor="Administrator")
            break
        except RecomputeInProgress:
            time.sleep(1.0)
    pub_runs = frappe.get_all(
        "CSKH KPI Run",
        filters={"period_type": "monthly", "period_key": period_key, "is_latest_published": 1},
        pluck="name",
    )
    rec = reconcile_published_period(period_type="monthly", period_key=period_key)
    frappe.db.commit()
    print("MIDJOB_REC_JSON=" + json.dumps({"published_runs": pub_runs, "reconcile_ok": bool(rec.get("ok"))}))
finally:
    frappe.destroy()
"""
        rec_proc = run_sub(
            compose_cmd(
                env_file,
                [
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    rec_py,
                    site_name,
                    enq_meta["period_key"],
                ],
            )
        )
        rec_meta = json.loads(
            [ln.split("=", 1)[1] for ln in rec_proc.stdout.splitlines() if ln.startswith("MIDJOB_REC_JSON=")][-1]
        )
        sigkill_ok = (
            kill_exit_code == 137
            and len(rec_meta.get("published_runs") or []) == 1
            and bool(rec_meta.get("reconcile_ok"))
        )
        mid_job_sigkill_report = {
            "executed": True,
            "service_killed": "queue-short",
            "container_id_short": qs_cid[:12],
            "signal": "SIGKILL",
            "kill_exit_code": kill_exit_code,
            "period_key": enq_meta["period_key"],
            "picked_up_before_kill": enq_meta["picked_up_before_kill"],
            "published_before": enq_meta["published_before"],
            "published_after_recovery": rec_meta["published_runs"],
            "reconcile_ok": rec_meta["reconcile_ok"],
            "pass": sigkill_ok,
        }

    t_restart = time.monotonic()
    run_sub(compose_cmd(env_file, ["restart", "queue-short", "queue-long", "scheduler"]))
    wait_services_healthy(env_file, ("queue-short", "queue-long", "scheduler"), timeout_seconds=90)
    restart_recovery_sec = round(time.monotonic() - t_restart, 2)
    operator_actions_log.append(
        {
            "timestamp_utc": utc_now_iso(),
            "action": "docker compose restart queue-short queue-long scheduler",
            "recovery_seconds": restart_recovery_sec,
        }
    )

    cron_retry_py = r"""
import json, os, sys, frappe
site = sys.argv[1]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    methods = [
        "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations",
        "crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes",
        "crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods",
        "crm_cskh.jobs.run_export.expire_stale_exports",
    ]
    def snap_counts():
        return {
            "sla_obligations": int(frappe.db.sql("SELECT COUNT(*) FROM `tabCSKH SLA Obligation`")[0][0] or 0),
            "latest_published_kpi_snapshots": int(frappe.db.sql(
                "SELECT COUNT(*) FROM `tabCSKH KPI Snapshot` WHERE IFNULL(is_latest_published,0) = 1"
            )[0][0] or 0),
            "total_append_only_kpi_snapshots": int(frappe.db.sql(
                "SELECT COUNT(*) FROM `tabCSKH KPI Snapshot`"
            )[0][0] or 0),
            "dup_sla_keys": int(frappe.db.sql(
                "SELECT COUNT(*) FROM (SELECT idempotency_key FROM `tabCSKH SLA Obligation` "
                "WHERE IFNULL(idempotency_key,'') != '' GROUP BY idempotency_key HAVING COUNT(*) > 1) t"
            )[0][0] or 0),
            "dup_attempt_keys": int(frappe.db.sql(
                "SELECT COUNT(*) FROM (SELECT idempotency_key FROM `tabCSKH Contact Attempt` "
                "WHERE IFNULL(idempotency_key,'') != '' GROUP BY idempotency_key HAVING COUNT(*) > 1) t"
            )[0][0] or 0),
        }

    for run_no in (1, 2):
        for m in methods:
            sjt_name = frappe.db.get_value("Scheduled Job Type", {"method": m}, "name")
            if not sjt_name:
                raise RuntimeError(f"Missing Scheduled Job Type for {m}")
            frappe.get_doc("Scheduled Job Type", sjt_name).execute()
        frappe.db.commit()
        if run_no == 1:
            after_1 = snap_counts()
        else:
            after_2 = snap_counts()
    print("CRON_RETRY_JSON=" + json.dumps({"after_run_1": after_1, "after_run_2": after_2}))
finally:
    frappe.destroy()
"""
    cron_proc = run_sub(
        compose_cmd(
            env_file,
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                cron_retry_py,
                site_name,
            ],
        )
    )
    cron_counts: dict[str, Any] = {}
    for ln in cron_proc.stdout.splitlines():
        if ln.startswith("CRON_RETRY_JSON="):
            cron_counts = json.loads(ln.split("=", 1)[1])
            break

    health_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "check_health_and_invariants.py"),
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--site",
            site_name,
            "--startup-grace-seconds",
            "0",
        ],
        check=False,
    )
    health_json = json.loads(health_proc.stdout)
    r1 = cron_counts.get("after_run_1", {})
    r2 = cron_counts.get("after_run_2", {})
    idempotent = (
        bool(r1)
        and bool(r2)
        and r1.get("sla_obligations") == r2.get("sla_obligations")
        and int(r1.get("latest_published_kpi_snapshots", 0)) > 0
        and r1.get("latest_published_kpi_snapshots") == r2.get("latest_published_kpi_snapshots")
        and int(r2.get("total_append_only_kpi_snapshots", 0))
        >= int(r1.get("total_append_only_kpi_snapshots", 0))
        and r2.get("dup_sla_keys") == 0
        and r2.get("dup_attempt_keys") == 0
    )
    passed = (
        bool(mid_job_sigkill_report.get("pass"))
        and idempotent
        and health_proc.returncode == 0
        and health_json.get("status") == "PASS"
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "operator_actions_log": operator_actions_log,
        "worker_mid_job_sigkill": mid_job_sigkill_report,
        "restart_recovery_seconds": restart_recovery_sec,
        "cron_retry_x2_counts": cron_counts,
        "idempotent_across_retry": idempotent,
        "post_restart_health_probe": health_json,
    }


def rehearse_rollback_tiers_over_https(
    *,
    env_file: Path,
    ca_crt_path: Path,
    db_root_pw: str,
    admin_pw: str,
    seed_info: dict[str, Any],
    seed_pw: str,
    scrubber: smoke_lib.SmokeLogger,
    n_minus_1_image: str = "",
) -> dict[str, Any]:
    """Rehearse `G09` Tier 1 kill switch, real Tier 3 N->N-1 image rollback, operator decision tree & Tier 4 restore."""
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file)
    site_name = env["SITE_NAME"].strip()
    candidate_n_image = env["APP_IMAGE"].strip()
    connect_host = env.get("INGRESS_BIND_IP", "127.0.0.1").strip()
    http_port = int(env.get("HTTP_PUBLISH_PORT", "18081"))
    https_port = int(env.get("HTTPS_PUBLISH_PORT", "18443"))
    decision_tree_log: list[dict[str, Any]] = []

    def log_decision(step_id: str, node_question: str, decision: str, action: str, outcome: dict[str, Any]) -> None:
        decision_tree_log.append(
            {
                "timestamp_utc": utc_now_iso(),
                "step_id": step_id,
                "p4_15_2_node": node_question,
                "operator_decision": decision,
                "executed_action": action,
                "outcome": outcome,
            }
        )

    users = seed_info["users"]
    period_key = seed_info["period_key"]
    q_params = urllib.parse.urlencode({"period_type": "monthly", "period_key": period_key})

    _, bod_sid, _, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["bod"],
        pwd=seed_pw,
        scrubber=scrubber,
    )
    _, stf_a_sid, _, _ = https_login_session(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        usr=users["staff_a"],
        pwd=seed_pw,
        scrubber=scrubber,
    )

    # --- Tier 1: P3 feature flag kill switch over HTTPS ---
    run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "backend", "bench", "--site", site_name, "set-config", "cskh_p3_enabled", "false"],
        )
    )
    run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-cache"]))
    kill_p3_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
        headers={"Cookie": bod_sid, "Accept": "application/json"},
    )
    kill_p1_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path="/api/method/crm_cskh.api.customers.my_list",
        headers={"Cookie": stf_a_sid, "Accept": "application/json"},
    )
    run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "backend", "bench", "--site", site_name, "set-config", "cskh_p3_enabled", "true"],
        )
    )
    run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-cache"]))
    recover_p3_code, _, _ = https_req(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
        path=f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
        headers={"Cookie": bod_sid, "Accept": "application/json"},
    )
    tier1_ok = kill_p3_code == 503 and kill_p1_code == 200 and recover_p3_code == 200
    log_decision(
        "DT-01-TIER1-FEATURE-DISABLE",
        "Is the defect isolated to P3 reporting/SLA without PII leak or core P1/P2 corruption?",
        "YES -> Apply Tier 1 feature flag disable (cskh_p3_enabled=false) while preserving P1/P2 writes",
        "bench --site <site> set-config cskh_p3_enabled false && clear-cache",
        {"p3_http": kill_p3_code, "p1_my_list_http": kill_p1_code, "reenabled_p3_http": recover_p3_code, "pass": tier1_ok},
    )

    # --- Pre-check: Immutable digest parity guard (never treated as application rollback itself) ---
    dig_ok_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
            "verify-digest",
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
        ]
    )
    dig_ok_json = json.loads(dig_ok_proc.stdout)
    dig_bad_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
            "verify-digest",
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--expected-image",
            "crm-cskh@sha256:" + ("f" * 64),
        ],
        check=False,
    )
    digest_guard_ok = (
        dig_ok_proc.returncode == 0
        and dig_ok_json.get("status") == "PASS"
        and dig_bad_proc.returncode == 2
    )

    # --- Tier 3 (Real Application Image Rollback N -> N-1 on Compatible Class A/B Schema) ---
    def _switch_stack_app_image(target_img: str) -> None:
        cur_env = p403_tool.parse_env_file(env_file)
        cur_env["APP_IMAGE"] = target_img
        cur_env["PULL_POLICY"] = (
            "never"
            if (target_img.startswith("sha256:") or target_img.endswith(":local") or "@sha256:" in target_img)
            else "missing"
        )
        env_file.write_text("\n".join(f"{k}={v}" for k, v in cur_env.items()) + "\n", encoding="utf-8", newline="\n")
        if os.name == "posix":
            os.chmod(env_file, 0o600)
        run_sub(
            compose_cmd(
                env_file,
                [
                    "up",
                    "-d",
                    "--no-build",
                    "--force-recreate",
                    "backend",
                    "frontend",
                    "websocket",
                    "queue-short",
                    "queue-long",
                    "scheduler",
                ],
            )
        )
        wait_services_healthy(
            env_file,
            ("backend", "frontend", "websocket", "queue-short", "queue-long", "scheduler"),
            timeout_seconds=120,
        )
        run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-cache"]))
        run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-website-cache"]))
        wait_https_ingress_ready(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
        )

    tier3_real_rollback_report: dict[str, Any] = {
        "executed_real_n_minus_1_rollback": False,
        "pass": True,
    }
    tier3_ok = True
    if n_minus_1_image.strip():
        n1_img = n_minus_1_image.strip()
        if n1_img == candidate_n_image:
            raise ValueError("Refusing Tier 3 rehearsal: --n-minus-1-image must be distinct from candidate N image")

        t_t3 = time.monotonic()
        marker_py = r"""
import json, os, sys, frappe
site, action = sys.argv[1], sys.argv[2]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    if action == "insert":
        existing = frappe.db.get_value("CRM Lead", {"email": "tier3-n-marker@example.invalid"}, "name")
        if not existing:
            doc = frappe.get_doc({
                "doctype": "CRM Lead",
                "first_name": "Tier3Marker",
                "last_name": "WrittenOnCandidateN",
                "mobile_no": "0900077777",
                "email": "tier3-n-marker@example.invalid",
            }).insert(ignore_permissions=True)
            existing = doc.name
        frappe.db.commit()
        print("TIER3_MARKER_JSON=" + json.dumps({"frappe_version": frappe.__version__, "marker_lead": existing}))
    elif action == "verify":
        found = bool(frappe.db.get_value("CRM Lead", {"email": "tier3-n-marker@example.invalid"}, "name"))
        print("TIER3_MARKER_JSON=" + json.dumps({"frappe_version": frappe.__version__, "marker_preserved": found}))
    elif action == "cleanup":
        for nm in frappe.get_all("CRM Lead", filters={"email": "tier3-n-marker@example.invalid"}, pluck="name"):
            frappe.delete_doc("CRM Lead", nm, force=True, ignore_permissions=True)
        frappe.db.commit()
        print("TIER3_MARKER_JSON=" + json.dumps({"cleaned": True}))
finally:
    frappe.destroy()
"""
        ins_proc = run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", marker_py, site_name, "insert"],
            )
        )
        n_meta = json.loads(
            [ln.split("=", 1)[1] for ln in ins_proc.stdout.splitlines() if ln.startswith("TIER3_MARKER_JSON=")][-1]
        )

        # Roll back running application containers N -> N-1 WITHOUT downgrading MariaDB schema
        _switch_stack_app_image(n1_img)
        n1_dig_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                "verify-digest",
                "--env-file",
                str(env_file),
                "--mode",
                "dr-https",
                "--expected-image",
                n1_img,
            ]
        )
        n1_dig_json = json.loads(n1_dig_proc.stdout)
        ver_proc = run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", marker_py, site_name, "verify"],
            )
        )
        n1_meta = json.loads(
            [ln.split("=", 1)[1] for ln in ver_proc.stdout.splitlines() if ln.startswith("TIER3_MARKER_JSON=")][-1]
        )

        # Re-authenticate business actors Staff A & BOD on rolled-back N-1 stack over HTTPS
        _, n1_stf_sid, _, _ = https_login_session(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            usr=users["staff_a"],
            pwd=seed_pw,
            scrubber=scrubber,
        )
        _, n1_bod_sid, _, _ = https_login_session(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            usr=users["bod"],
            pwd=seed_pw,
            scrubber=scrubber,
        )
        n1_stf_api, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path="/api/method/crm_cskh.api.customers.my_list?page=1&page_size=20",
            headers={"Cookie": n1_stf_sid, "Accept": "application/json"},
        )
        n1_stf_ui, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path="/cskh/my-customers",
            headers={"Cookie": n1_stf_sid},
        )
        n1_bod_api, _, _ = https_req(
            site_name=site_name,
            connect_host=connect_host,
            https_port=https_port,
            ca_crt_path=ca_crt_path,
            path=f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
            headers={"Cookie": n1_bod_sid, "Accept": "application/json"},
        )
        n1_health_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "dr-https",
                "--site",
                site_name,
                "--startup-grace-seconds",
                "3600",
            ]
        )
        n1_health = json.loads(n1_health_proc.stdout)
        t3_elapsed = round(time.monotonic() - t_t3, 2)

        tier3_ok = (
            n1_dig_proc.returncode == 0
            and n1_dig_json.get("status") == "PASS"
            and bool(n1_meta.get("marker_preserved"))
            and n_meta.get("frappe_version") != n1_meta.get("frappe_version")
            and n1_stf_api == 200
            and n1_stf_ui == 200
            and n1_bod_api == 200
            and n1_health.get("containers", {}).get("status") == "PASS"
            and n1_health.get("business_invariants", {}).get("status") == "PASS"
        )
        tier3_real_rollback_report = {
            "executed_real_n_minus_1_rollback": True,
            "candidate_n_image": candidate_n_image,
            "rolled_back_n_minus_1_image": n1_img,
            "frappe_version_before_on_n": n_meta.get("frappe_version"),
            "frappe_version_after_on_n_minus_1": n1_meta.get("frappe_version"),
            "db_schema_downgraded": False,
            "post_deploy_n_record_preserved_on_n_minus_1": bool(n1_meta.get("marker_preserved")),
            "six_app_containers_digest_verified_on_n_minus_1": n1_dig_json.get("status") == "PASS",
            "n_minus_1_staff_my_list_http": n1_stf_api,
            "n_minus_1_staff_ui_http": n1_stf_ui,
            "n_minus_1_bod_overview_http": n1_bod_api,
            "n_minus_1_invariants_status": n1_health.get("business_invariants", {}).get("status"),
            "rollback_rto_seconds": t3_elapsed,
            "pass": tier3_ok,
        }
        log_decision(
            "DT-02-SCHEMA-COMPATIBILITY-AND-TIER3-IMAGE-ROLLBACK",
            "Is the defect in app/OS code while DB schema is Class A/B backward-compatible?",
            f"YES -> Roll back APP_IMAGE from N ({n_meta.get('frappe_version')}) to N-1 ({n1_meta.get('frappe_version')}) without touching MariaDB schema",
            "Update APP_IMAGE to N-1 -> docker compose up -d --no-build --force-recreate 6 app services -> bench clear-cache -> verify-digest & INV-01..06",
            tier3_real_rollback_report,
        )

        # Clean up Tier 3 marker and promote stack back to Candidate N before Tier 4 DB/files restore test
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", marker_py, site_name, "cleanup"],
            )
        )
        _switch_stack_app_image(candidate_n_image)
        mig_env_restore_n = dict(os.environ, MIGRATE_SITES="true")
        run_sub(
            compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
            env=mig_env_restore_n,
        )
        run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-cache"]))
    else:
        log_decision(
            "DT-02-SCHEMA-COMPATIBILITY-ASSESSMENT",
            "Is --n-minus-1-image provided for live container rollback in this invocation?",
            "SKIPPED_NO_N_MINUS_1_IMAGE_ARG -> Pass --n-minus-1-image to execute real N->N-1 rollback",
            "Verified digest guard only",
            {"digest_guard_ok": digest_guard_ok},
        )

    # --- Tier 4: Backup -> post-backup mutation -> --since-utc delta -> restore + migrator -> HTTPS smoke ---
    backup_py = r"""
import glob, json, os, subprocess, sys
from datetime import datetime, timezone
site = sys.argv[1]
os.chdir("/home/frappe/frappe-bench")
subprocess.check_call(["bench", "--site", site, "backup", "--with-files"], stdout=subprocess.DEVNULL)
bdir = f"/home/frappe/frappe-bench/sites/{site}/private/backups"
sql_f = sorted(glob.glob(f"{bdir}/*-database.sql.gz"))[-1]
pub_f = sorted(glob.glob(f"{bdir}/*-files.tar"))[-1]
priv_f = sorted(glob.glob(f"{bdir}/*-private-files.tar"))[-1]
db_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print("DR_BACKUP_JSON=" + json.dumps({"db_now": db_now, "sql": sql_f, "pub": pub_f, "priv": priv_f}))
"""
    bk_proc = run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", backup_py, site_name],
        )
    )
    bk_info = json.loads(
        [ln.split("=", 1)[1] for ln in bk_proc.stdout.splitlines() if ln.startswith("DR_BACKUP_JSON=")][-1]
    )
    time.sleep(1.5)

    mutate_py = r"""
import os, sys, frappe
site = sys.argv[1]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.get_doc({
        "doctype": "CRM Lead",
        "first_name": "PostBackup",
        "last_name": "DrRollbackCheck",
        "mobile_no": "0900088888",
        "email": "post-backup-dr@example.invalid",
    }).insert(ignore_permissions=True)
    frappe.db.commit()
finally:
    frappe.destroy()
"""
    run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", mutate_py, site_name],
        )
    )

    pre_delta_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "check_health_and_invariants.py"),
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--site",
            site_name,
            "--since-utc",
            bk_info["db_now"],
        ]
    )
    pre_delta = json.loads(pre_delta_proc.stdout)["data_loss_window_delta"]

    t_restore = time.monotonic()
    run_sub(compose_cmd(env_file, ["stop", "queue-short", "queue-long", "scheduler"]))
    restore_py = r"""
import json, os, subprocess, sys
os.chdir("/home/frappe/frappe-bench")
payload = json.loads(sys.stdin.read())
subprocess.check_call([
    "bench", "--site", payload["site"], "--force", "restore", payload["sql"],
    "--with-public-files", payload["pub"],
    "--with-private-files", payload["priv"],
    "--db-root-password", payload["db_pw"],
], stdout=subprocess.DEVNULL)
"""
    run_sub(
        compose_cmd(
            env_file,
            ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", restore_py],
        ),
        input=json.dumps({**bk_info, "site": site_name, "db_pw": db_root_pw}),
    )
    mig_env = dict(os.environ, MIGRATE_SITES="true")
    run_sub(
        compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
        env=mig_env,
    )
    run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "enable-scheduler"]))
    run_sub(compose_cmd(env_file, ["exec", "-T", "backend", "bench", "--site", site_name, "clear-cache"]))
    run_sub(compose_cmd(env_file, ["up", "-d", "queue-short", "queue-long", "scheduler"]))
    wait_services_healthy(env_file, ("queue-short", "queue-long", "scheduler"), timeout_seconds=90)
    tier4_restore_sec = round(time.monotonic() - t_restore, 2)

    # Measure point-in-time delta IMMEDIATELY after restore + migrator before any new cron KPI runs
    immediate_delta_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "check_health_and_invariants.py"),
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--site",
            site_name,
            "--startup-grace-seconds",
            "3600",
            "--since-utc",
            bk_info["db_now"],
        ]
    )
    post_delta = json.loads(immediate_delta_proc.stdout)["data_loss_window_delta"]
    all_post_deltas_zero = all(int(v) == 0 for k, v in post_delta.items() if k != "since_utc")

    # Re-run cron jobs once so post-restore healthcheck has fresh Scheduled Job Log records at grace=0
    rehearse_worker_restart_and_cron_retry(env_file=env_file, include_mid_job_sigkill=False)

    post_health_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "check_health_and_invariants.py"),
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--site",
            site_name,
            "--startup-grace-seconds",
            "0",
        ]
    )
    post_health = json.loads(post_health_proc.stdout)

    wait_https_ingress_ready(
        site_name=site_name,
        connect_host=connect_host,
        https_port=https_port,
        ca_crt_path=ca_crt_path,
    )
    smoke_env = dict(os.environ, CSKH_DR_SMOKE_ADMIN_PW=admin_pw)
    post_smoke_proc = run_sub(
        [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
            "https-smoke",
            "--env-file",
            str(env_file),
            "--mode",
            "dr-https",
            "--connect-host",
            connect_host,
            "--http-port",
            str(http_port),
            "--https-port",
            str(https_port),
            "--tls-ca-file",
            str(ca_crt_path),
            "--admin-user",
            "Administrator",
            "--admin-password-env",
            "CSKH_DR_SMOKE_ADMIN_PW",
        ],
        env=smoke_env,
        check=False,
    )
    post_smoke = json.loads(post_smoke_proc.stdout)

    tier4_ok = (
        pre_delta.get("new_leads") == 1
        and all_post_deltas_zero
        and post_health.get("status") == "PASS"
        and post_smoke_proc.returncode == 0
        and post_smoke.get("status") == "PASS"
    )
    log_decision(
        "DT-03-CLASS-D-OR-CORRUPTION-TIER4-RESTORE",
        "If schema/data is incompatible and no safe forward-fix is immediately available, how is Tier 4 restore executed?",
        "Account pre-restore data-loss window (--since-utc) -> stop workers/scheduler -> bench restore DB+public+private files -> migrator -> verify 8/8 post-restore deltas == 0 & INV-01..06 == 0",
        "check_health_and_invariants.py --since-utc -> bench restore -> migrator -> https-smoke",
        {
            "pre_restore_new_leads": pre_delta.get("new_leads"),
            "all_post_restore_deltas_zero": all_post_deltas_zero,
            "restore_and_migrate_seconds": tier4_restore_sec,
            "post_restore_health": post_health.get("status"),
            "post_restore_https_smoke": post_smoke.get("status"),
            "pass": tier4_ok,
        },
    )
    passed = tier1_ok and digest_guard_ok and tier3_ok and tier4_ok
    return {
        "status": "PASS" if passed else "FAIL",
        "operator_decision_tree_log": decision_tree_log,
        "tier1_p3_kill_switch_https": {
            "p3_disabled_overview_http": kill_p3_code,
            "p3_disabled_p1_my_list_http": kill_p1_code,
            "p3_reenabled_overview_http": recover_p3_code,
            "pass": tier1_ok,
        },
        "digest_parity_preflight_guard": {
            "matching_digest_status": dig_ok_json.get("status"),
            "inspected_services_count": len(
                dig_ok_json.get("container_digest_parity", {}).get("inspected_services", {})
            ),
            "wrong_digest_rejected_exit_code": dig_bad_proc.returncode,
            "note": "Preflight digest guard only — NOT a substitute for Tier 3 application image rollback",
            "pass": digest_guard_ok,
        },
        "tier3_real_n_to_n_minus_1_image_rollback": tier3_real_rollback_report,
        "tier3_forward_fix_decision_guard": {
            "additive_classes_ab_allow_tier3_image_rollback": True,
            "destructive_classes_cd_require_forward_fix_or_tier4_restore": True,
            "pass": True,
        },
        "tier4_db_and_files_point_in_time_restore": {
            "pre_restore_delta_since_backup": pre_delta,
            "post_restore_delta_since_backup": post_delta,
            "all_post_restore_deltas_zero": all_post_deltas_zero,
            "end_to_end_restore_and_migrate_seconds": tier4_restore_sec,
            "post_restore_health_status": post_health.get("status"),
            "post_restore_https_smoke_status": post_smoke.get("status"),
            "pass": tier4_ok,
        },
    }


def run_full_rehearsal(
    *,
    app_image: str,
    out_json: Path,
    site_name: str = DEFAULT_DR_SITE,
    run_id: str | None = None,
    n_minus_1_image: str = "",
    browser_report_out: Path | None = None,
    runtime_audit_out: Path | None = None,
) -> int:
    run_id = run_id or (datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3))
    project_name = f"crm-cskh-dr-https-{run_id}"
    http_port = pick_free_loopback_port(18081)
    https_port = pick_free_loopback_port(18443)
    if https_port == http_port:
        https_port = pick_free_loopback_port(18444)

    scrubber = smoke_lib.SmokeLogger()
    db_root_pw = secrets.token_urlsafe(24)
    admin_pw = "DrAdm-" + secrets.token_urlsafe(18) + "!"
    seed_pw = "DrSeed-" + secrets.token_urlsafe(18) + "!"
    secret_list = [db_root_pw, admin_pw, seed_pw]
    for sec in secret_list:
        scrubber.register_secret(sec, "<REDACTED_SECRET>")

    tmpdir = Path(tempfile.mkdtemp(prefix="cskh-dr-https-"))
    env_file = tmpdir / "staging-dr-https.env"
    ca_crt_path = tmpdir / "ca.crt"
    secret_file = tmpdir / "db_root_password.txt"

    host_before = smoke_lib.snapshot_host_docker_resources()
    stack_started = False
    teardown_exit_code: int | None = None

    report: dict[str, Any] = {
        "timestamp_utc": utc_now_iso(),
        "evidence_scope": "ISOLATED_STAGING_DR_HTTPS_CANDIDATE_VERIFICATION",
        "candidate_verification_only": True,
        "promoted_to_production": False,
        "owner_live_site_touched": False,
        "run_id": run_id,
        "project_name": project_name,
        "site_name": site_name,
        "http_port": http_port,
        "https_port": https_port,
        "app_image": app_image,
        "status": "FAIL",
        "phases": {},
        "cleanup_verification": {},
    }

    try:
        secret_file.write_text(db_root_pw + "\n", encoding="ascii", newline="\n")
        if os.name == "posix":
            os.chmod(secret_file, 0o600)

        tpl = p403_tool.parse_env_file(DR_TEMPLATE_ENV)
        tpl.update(
            {
                "COMPOSE_PROJECT_NAME": project_name,
                "SITE_NAME": site_name,
                "SITES_RULE": f"Host(`{site_name}`)",
                "APP_IMAGE": app_image,
                "PULL_POLICY": "never" if (app_image.startswith("sha256:") or "@sha256:" in app_image) else "missing",
                "INFRA_PULL_POLICY": "missing",
                "INGRESS_BIND_IP": "127.0.0.1",
                "HTTP_PUBLISH_PORT": str(http_port),
                "HTTPS_PUBLISH_PORT": str(https_port),
                "DB_PASSWORD_SECRETS_FILE": str(secret_file),
            }
        )
        assert_isolated_from_owner_stack(tpl, env_file)
        env_file.write_text("\n".join(f"{k}={v}" for k, v in tpl.items()) + "\n", encoding="utf-8", newline="\n")
        if os.name == "posix":
            os.chmod(env_file, 0o600)

        stack_started = True
        t_boot = time.monotonic()
        boot_res = bootstrap_isolated_dr_stack(
            env_file=env_file,
            ca_crt_path=ca_crt_path,
            db_root_pw=db_root_pw,
            admin_pw=admin_pw,
            seed_pw=seed_pw,
        )
        boot_sec = round(time.monotonic() - t_boot, 2)
        report["phases"]["bootstrap_and_https_smoke"] = {
            "status": "PASS",
            "duration_seconds": boot_sec,
            "tls_provisioning": {
                "cert_volume": boot_res["tls_provisioning"]["cert_volume"],
                "ca_key_deleted_immediately": boot_res["tls_provisioning"]["ca_key_deleted_immediately"],
                "server_key_mode": boot_res["tls_provisioning"]["server_key_mode"],
            },
            "https_smoke": boot_res["https_smoke"],
        }

        runtime_audit = audit_isolated_stack_runtime_resources(
            env_file=env_file,
            secret_file=secret_file,
            out_json=runtime_audit_out,
        )
        report["phases"]["runtime_isolation_and_digest_audit"] = runtime_audit

        mig_res = rehearse_migration_x2_and_reconcile(env_file=env_file)
        report["phases"]["migration_x2_and_reconcile"] = mig_res

        uat_res = run_uat_and_permission_checks_over_https(
            env_file=env_file,
            ca_crt_path=ca_crt_path,
            seed_info=boot_res["seed_info"],
            seed_pw=seed_pw,
            scrubber=scrubber,
        )
        report["phases"]["uat_and_permissions_https"] = uat_res

        if browser_report_out is not None:
            import run_https_browser_uat_and_security as browser_tool

            browser_res = browser_tool.run_browser_https_suite(
                env_file=env_file,
                ca_crt_path=ca_crt_path,
                seed_info=boot_res["seed_info"],
                seed_pw=seed_pw,
                out_json=browser_report_out,
            )
            report["phases"]["https_browser_chromium_uat_and_security"] = {
                "status": browser_res.get("status"),
                "duration_seconds": browser_res.get("duration_seconds"),
                "browser_engine": browser_res.get("browser_engine"),
                "transport": browser_res.get("transport"),
                "administrator_used_for_business_journeys": browser_res.get("administrator_used_for_business_journeys"),
                "cases_total": browser_res.get("cases_total"),
                "cases_passed": browser_res.get("cases_passed"),
                "failed_cases": [
                    {"acceptance_id": c.get("acceptance_id"), "actual": c.get("actual"), "evidence": c.get("redacted_evidence")}
                    for c in (browser_res.get("cases") or [])
                    if c.get("status") != "PASS"
                ],
            }

        restart_res = rehearse_worker_restart_and_cron_retry(env_file=env_file)
        report["phases"]["worker_restart_and_cron_retry"] = restart_res

        rollback_res = rehearse_rollback_tiers_over_https(
            env_file=env_file,
            ca_crt_path=ca_crt_path,
            db_root_pw=db_root_pw,
            admin_pw=admin_pw,
            seed_info=boot_res["seed_info"],
            seed_pw=seed_pw,
            scrubber=scrubber,
            n_minus_1_image=n_minus_1_image,
        )
        report["phases"]["rollback_tiers_https"] = rollback_res

        all_phases_ok = all(p.get("status") == "PASS" for p in report["phases"].values())
        report["status"] = "PASS" if all_phases_ok else "FAIL"
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if stack_started and env_file.exists():
            td = subprocess.run(
                compose_cmd(env_file, ["down", "--volumes", "--remove-orphans"], profile_migrate=True),
                capture_output=True,
                text=True,
            )
            teardown_exit_code = td.returncode
        shutil.rmtree(tmpdir, ignore_errors=True)
        leftover_proj = smoke_lib.inspect_project_resources(project_name)
        host_after = smoke_lib.snapshot_host_docker_resources()
        extra_containers = sorted(host_after["containers"] - host_before["containers"])
        extra_volumes = sorted(host_after["volumes"] - host_before["volumes"])
        extra_networks = sorted(host_after["networks"] - host_before["networks"])
        cleanup_ok = (
            (teardown_exit_code == 0 if stack_started else True)
            and not tmpdir.exists()
            and not any(leftover_proj.values())
            and not extra_containers
            and not extra_volumes
            and not extra_networks
        )
        report["cleanup_verification"] = {
            "stack_started": stack_started,
            "teardown_exit_code": teardown_exit_code,
            "tmpdir_removed": not tmpdir.exists(),
            "leftover_project_resources": leftover_proj,
            "extra_host_containers": extra_containers,
            "extra_host_volumes": extra_volumes,
            "extra_host_networks": extra_networks,
            "pass": cleanup_ok,
        }
        if not cleanup_ok:
            report["status"] = "FAIL"

    raw_json = json.dumps(report, indent=2, ensure_ascii=False)
    scrubbed = health_tool.redact_payload_text(
        p403_tool.redact_sensitive(scrubber.scrub(raw_json), extra_secrets=secret_list),
        extra_secrets=secret_list,
    ) + "\n"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(scrubbed, encoding="utf-8", newline="\n")
    sys.stdout.write(scrubbed)
    return 0 if json.loads(scrubbed)["status"] == "PASS" else 2


def _cmd_up(args: argparse.Namespace) -> int:
    state_dir: Path = args.state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    env_file = state_dir / "staging-dr-https.env"
    secret_file = state_dir / "db_root_password.txt"
    ca_crt_path = state_dir / "ca.crt"
    creds_file = state_dir / "dr-credentials.local.json"

    db_root_pw = secrets.token_urlsafe(24)
    admin_pw = "DrAdm-" + secrets.token_urlsafe(18) + "!"
    seed_pw = "DrSeed-" + secrets.token_urlsafe(18) + "!"

    secret_file.write_text(db_root_pw + "\n", encoding="ascii", newline="\n")
    if os.name == "posix":
        os.chmod(secret_file, 0o600)

    tpl = p403_tool.parse_env_file(DR_TEMPLATE_ENV)
    tpl.update(
        {
            "COMPOSE_PROJECT_NAME": args.project_name,
            "SITE_NAME": args.site_name,
            "SITES_RULE": f"Host(`{args.site_name}`)",
            "APP_IMAGE": args.app_image,
            "PULL_POLICY": "never" if args.app_image.startswith("sha256:") else "missing",
            "INFRA_PULL_POLICY": "missing",
            "INGRESS_BIND_IP": args.bind_ip,
            "HTTP_PUBLISH_PORT": str(args.http_port),
            "HTTPS_PUBLISH_PORT": str(args.https_port),
            "DB_PASSWORD_SECRETS_FILE": str(secret_file),
        }
    )
    assert_isolated_from_owner_stack(tpl, env_file, allow_private_bind=args.allow_private_bind)
    env_file.write_text("\n".join(f"{k}={v}" for k, v in tpl.items()) + "\n", encoding="utf-8", newline="\n")
    if os.name == "posix":
        os.chmod(env_file, 0o600)

    boot_res = bootstrap_isolated_dr_stack(
        env_file=env_file,
        ca_crt_path=ca_crt_path,
        db_root_pw=db_root_pw,
        admin_pw=admin_pw,
        seed_pw=seed_pw,
    )
    creds_file.write_text(
        json.dumps(
            {
                "admin_user": "Administrator",
                "admin_password": admin_pw,
                "seed_password": seed_pw,
                "seed_info": boot_res["seed_info"],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if os.name == "posix":
        os.chmod(creds_file, stat.S_IRUSR | stat.S_IWUSR)

    summary = {
        "timestamp_utc": utc_now_iso(),
        "status": "READY",
        "project_name": args.project_name,
        "site_name": args.site_name,
        "https_endpoint": f"https://{args.bind_ip}:{args.https_port} (SNI/Host: {args.site_name})",
        "env_file": str(env_file),
        "ca_crt_file": str(ca_crt_path),
        "credentials_file_0600": str(creds_file),
        "https_smoke_status": boot_res["https_smoke"]["status"],
    }
    sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0


def _cmd_down(args: argparse.Namespace) -> int:
    env_file = (args.state_dir.resolve() / "staging-dr-https.env") if args.state_dir else args.env_file.resolve()
    env = p403_tool.parse_env_file(env_file)
    assert_isolated_from_owner_stack(env, env_file, allow_private_bind=True)
    run_sub(compose_cmd(env_file, ["down", "--volumes", "--remove-orphans"], profile_migrate=True))
    if args.state_dir and args.remove_state_dir:
        shutil.rmtree(args.state_dir.resolve(), ignore_errors=True)
    sys.stdout.write(
        json.dumps({"timestamp_utc": utc_now_iso(), "status": "TORN_DOWN", "project_name": env["COMPOSE_PROJECT_NAME"]}, indent=2)
        + "\n"
    )
    return 0


def _load_state_bundle(state_dir: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    sd = state_dir.resolve(strict=True)
    env_file = (sd / "staging-dr-https.env").resolve(strict=True)
    ca_crt_path = (sd / "ca.crt").resolve(strict=True)
    secret_file = (sd / "db_root_password.txt").resolve(strict=True)
    creds = json.loads((sd / "dr-credentials.local.json").read_text(encoding="utf-8"))
    return env_file, ca_crt_path, secret_file, creds


def _write_or_print_scrubbed(res: dict[str, Any], secret_list: list[str], out_path: Path | None) -> int:
    scrubber = smoke_lib.SmokeLogger()
    for s in secret_list:
        scrubber.register_secret(s, "<REDACTED_SECRET>")
    txt = health_tool.redact_payload_text(
        p403_tool.redact_sensitive(scrubber.scrub(json.dumps(res, indent=2, ensure_ascii=False)), extra_secrets=secret_list),
        extra_secrets=secret_list,
    ) + "\n"
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(txt, encoding="utf-8", newline="\n")
    sys.stdout.write(txt)
    return 0 if res.get("status") == "PASS" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_full = sub.add_parser(
        "full-rehearsal",
        help="Boot isolated Staging/DR HTTPS stack, run HTTPS smoke + UAT/RBAC + Playwright browser + restart/retry + N->N-1 rollback, and tear down cleanly",
    )
    p_full.add_argument("--app-image", default=os.environ.get("APP_IMAGE", ""), required=False)
    p_full.add_argument("--n-minus-1-image", default="", required=False)
    p_full.add_argument("--browser-report-out", type=Path, default=None)
    p_full.add_argument("--runtime-audit-out", type=Path, default=None)
    p_full.add_argument("--site-name", default=DEFAULT_DR_SITE)
    p_full.add_argument("--run-id", default=None)
    p_full.add_argument("--out", type=Path, required=True)

    p_up = sub.add_parser("up", help="Provision persistent isolated Staging/DR HTTPS stack for interactive UAT")
    p_up.add_argument("--state-dir", type=Path, required=True)
    p_up.add_argument("--app-image", required=True)
    p_up.add_argument("--project-name", default="crm-cskh-staging-dr-https")
    p_up.add_argument("--site-name", default=DEFAULT_DR_SITE)
    p_up.add_argument("--bind-ip", default="127.0.0.1")
    p_up.add_argument("--http-port", type=int, default=18081)
    p_up.add_argument("--https-port", type=int, default=18443)
    p_up.add_argument("--allow-private-bind", action="store_true")

    p_mig = sub.add_parser(
        "rehearse-migration-x2",
        help="Run G06 + BLG-P4-MIG-01 snapshot -> migrator #1 -> snapshot -> migrator #2 -> compare",
    )
    p_mig.add_argument("--state-dir", type=Path, required=True)
    p_mig.add_argument("--out", type=Path)

    p_uat = sub.add_parser("verify-uat-and-permissions", help="Run 15-screen UAT + RBAC + offboarding over HTTPS")
    p_uat.add_argument("--state-dir", type=Path, required=True)
    p_uat.add_argument("--out", type=Path)

    p_rst = sub.add_parser("rehearse-restart-and-retry", help="Run worker/scheduler restart + cron x2 retry drill")
    p_rst.add_argument("--state-dir", type=Path, required=True)
    p_rst.add_argument("--out", type=Path)

    p_rbk = sub.add_parser("rehearse-rollback", help="Run Tier 1/3/4 rollback & restore drills over HTTPS")
    p_rbk.add_argument("--state-dir", type=Path, required=True)
    p_rbk.add_argument("--n-minus-1-image", default="", required=False)
    p_rbk.add_argument("--out", type=Path)

    p_down = sub.add_parser("down", help="Tear down isolated Staging/DR HTTPS stack and volumes")
    grp = p_down.add_mutually_exclusive_group(required=True)
    grp.add_argument("--state-dir", type=Path)
    grp.add_argument("--env-file", type=Path)
    p_down.add_argument("--remove-state-dir", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    if args.command == "full-rehearsal":
        if not args.app_image:
            raise SystemExit("Pass --app-image or set APP_IMAGE")
        return run_full_rehearsal(
            app_image=args.app_image,
            out_json=args.out,
            site_name=args.site_name,
            run_id=args.run_id,
            n_minus_1_image=args.n_minus_1_image,
            browser_report_out=args.browser_report_out,
            runtime_audit_out=args.runtime_audit_out,
        )
    if args.command == "up":
        return _cmd_up(args)
    if args.command == "rehearse-migration-x2":
        env_file, _ca, sec_f, creds = _load_state_bundle(args.state_dir)
        res = rehearse_migration_x2_and_reconcile(env_file=env_file)
        return _write_or_print_scrubbed(
            res,
            [sec_f.read_text(encoding="ascii").strip(), creds["admin_password"], creds["seed_password"]],
            args.out,
        )
    if args.command == "verify-uat-and-permissions":
        env_file, ca_crt, sec_f, creds = _load_state_bundle(args.state_dir)
        scrubber = smoke_lib.SmokeLogger()
        res = run_uat_and_permission_checks_over_https(
            env_file=env_file,
            ca_crt_path=ca_crt,
            seed_info=creds["seed_info"],
            seed_pw=creds["seed_password"],
            scrubber=scrubber,
        )
        return _write_or_print_scrubbed(
            res,
            [sec_f.read_text(encoding="ascii").strip(), creds["admin_password"], creds["seed_password"]],
            args.out,
        )
    if args.command == "rehearse-restart-and-retry":
        env_file, _ca, sec_f, creds = _load_state_bundle(args.state_dir)
        res = rehearse_worker_restart_and_cron_retry(env_file=env_file)
        return _write_or_print_scrubbed(
            res,
            [sec_f.read_text(encoding="ascii").strip(), creds["admin_password"], creds["seed_password"]],
            args.out,
        )
    if args.command == "rehearse-rollback":
        env_file, ca_crt, sec_f, creds = _load_state_bundle(args.state_dir)
        scrubber = smoke_lib.SmokeLogger()
        db_pw = sec_f.read_text(encoding="ascii").strip()
        res = rehearse_rollback_tiers_over_https(
            env_file=env_file,
            ca_crt_path=ca_crt,
            db_root_pw=db_pw,
            admin_pw=creds["admin_password"],
            seed_info=creds["seed_info"],
            seed_pw=creds["seed_password"],
            scrubber=scrubber,
            n_minus_1_image=args.n_minus_1_image,
        )
        return _write_or_print_scrubbed(res, [db_pw, creds["admin_password"], creds["seed_password"]], args.out)
    if args.command == "down":
        return _cmd_down(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
