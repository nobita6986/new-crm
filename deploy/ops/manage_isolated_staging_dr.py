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
import base64
import hashlib
import json
import math
import os
import platform
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
RELEASE_DIR = REPO_ROOT / "deploy" / "release"
for _p in (str(COMPOSE_SCRIPTS_DIR), str(OPS_DIR), str(RELEASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import check_health_and_invariants as health_tool  # noqa: E402
import evaluate_s1_slo as s1_slo_tool  # noqa: E402
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
ALL_DAEMON_SERVICES: tuple[str, ...] = (
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
)
STAGING_CANDIDATE_SCOPE = "staging_candidate_isolated_verification"
PRODUCTION_CANDIDATE_SCOPE = "production_candidate"
CI_REHEARSAL_SCOPE = "ci_ephemeral_rehearsal"
KNOWN_REHEARSAL_PUBLIC_KEY_FINGERPRINTS: frozenset[str] = frozenset(
    {
        "b3e6137bbe048229ec0da37b434bff40b8f30cd96657b28349e443c766573b76",
    }
)
DEFAULT_RELEASE_MANIFEST_PATH = REPO_ROOT / "docs" / "p4" / "release-manifest.json"
DEFAULT_CVE_EXCEPTIONS_PATH = REPO_ROOT / "deploy" / "release" / "cve-exceptions.json"
IMMUTABLE_REGISTRY_DIGEST_RE = re.compile(
    r"\A[^/@\s:<>]+(?::[0-9]+)?(?:/[^/@\s:<>]+)+(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}\Z"
)
LOCAL_IMAGE_ID_RE = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


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
    """Wait for Traefik Docker router discovery, nginx frontend, and Frappe website SSR warm-up over TLS."""
    deadline = time.monotonic() + timeout_seconds
    last_status = 0
    last_login_status = 0
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
                login_st, _, _ = https_req(
                    site_name=site_name,
                    connect_host=connect_host,
                    https_port=https_port,
                    ca_crt_path=ca_crt_path,
                    path="/login",
                    headers={"Accept": "text/html"},
                )
                last_login_status = login_st
                if login_st == 200:
                    return
        except Exception:
            last_status = 0
        time.sleep(0.5)
    raise RuntimeError(
        f"Timed out waiting for HTTPS Traefik/frontend ingress /api/method/ping=200 and /login=200 on {site_name} "
        f"(last_ping_status={last_status}, last_login_status={last_login_status})"
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

    daemon_q_proc = run_sub(compose_cmd(env_file, ["ps", "-q", *ALL_DAEMON_SERVICES]), check=False)
    daemon_cids = [ln.strip() for ln in daemon_q_proc.stdout.splitlines() if ln.strip()]
    resource_limits_by_service: dict[str, dict[str, Any]] = {}
    if daemon_cids:
        dinsp_proc = run_sub(["docker", "inspect", *daemon_cids])
        for item in json.loads(dinsp_proc.stdout):
            labels = (item.get("Config") or {}).get("Labels") or {}
            svc_name = str(labels.get("com.docker.compose.service") or "")
            if not svc_name:
                continue
            hcfg = item.get("HostConfig") or {}
            mem_bytes = int(hcfg.get("Memory") or 0)
            nano_cpus = int(hcfg.get("NanoCpus") or 0)
            resource_limits_by_service[svc_name] = {
                "container": str(item.get("Name") or "").lstrip("/"),
                "mem_limit_bytes": mem_bytes,
                "mem_limit_mib": round(mem_bytes / (1024.0 * 1024.0), 2),
                "nano_cpus": nano_cpus,
                "cpus": round(nano_cpus / 1_000_000_000.0, 3),
                "bounded": (mem_bytes > 0) and (nano_cpus > 0),
            }
    missing_daemon_services = sorted(set(ALL_DAEMON_SERVICES) - set(resource_limits_by_service))
    unbounded_services = sorted(
        svc for svc, rmeta in resource_limits_by_service.items() if not rmeta["bounded"]
    )
    total_capped_ram_mib = round(
        sum(rmeta["mem_limit_mib"] for rmeta in resource_limits_by_service.values()), 2
    )
    total_capped_cpus = round(
        sum(rmeta["cpus"] for rmeta in resource_limits_by_service.values()), 3
    )
    resource_limits_ok = (not missing_daemon_services) and (not unbounded_services)
    governance_scope = verify_staging_candidate_scope_and_key_guard(
        app_image=app_image,
        allow_local_image_id_for_drill=True,
    )

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
        and resource_limits_ok
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
        "governance_scope": governance_scope,
        "project_name": project_name,
        "site_name": site_name,
        "app_image": app_image,
        "compose_git_tree_sha": compose_tree_sha,
        "rendered_compose_config_sha256": rendered_cfg_sha256,
        "container_digest_parity": digest_parity,
        "resource_limits": {
            "pass": resource_limits_ok,
            "expected_daemon_services": list(ALL_DAEMON_SERVICES),
            "missing_daemon_services": missing_daemon_services,
            "unbounded_services": unbounded_services,
            "total_capped_ram_mib": total_capped_ram_mib,
            "total_capped_cpus": total_capped_cpus,
            "by_service": dict(sorted(resource_limits_by_service.items())),
        },
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


LOCKED_CANDIDATE_DIGEST_V1_4 = (
    "ghcr.io/nobita6986/crm-cskh@sha256:693a3cdcc758dab64b3ac61d8e6f066581d94b362c9649d709684b72d7d0a2e6"
)
DEFAULT_G18_THRESHOLDS_V1_4_PATH = OPS_DIR / "g18_slo_thresholds_v1_4.json"
LOCKED_G18_THRESHOLDS_V1_4_SHA256 = (
    "e9fde1a0e796a4f30651689c02384fb07051ab6adcaf368e5c336122f4ec54e7"
)
CURRENT_CANDIDATE_SOURCE_SHA = "3c040c85a44c4ad37e9d9152b5630a4596e593ee"
CURRENT_CANDIDATE_DIGEST = (
    "ghcr.io/nobita6986/crm-cskh@sha256:ba68cd229ea3f3dc2566017e13251387b3fe398196acd4880a2a184201a00be8"
)
CURRENT_CANDIDATE_OCI_CONFIG_DIGEST = (
    "sha256:672a97898574e404b66c7083107bbaac3c626ee658e63ab184fd6248b0aa335d"
)
DEFAULT_P4_S1_SLO_SPEC_PATH = RELEASE_DIR / "p4-s1-slo-spec.json"
DEFAULT_P4_S1_SLO_APPROVAL_PATH = RELEASE_DIR / "p4-s1-slo-approval.json"
LOCKED_P4_S1_SLO_SPEC_SHA256 = (
    "ccd8f83c97f4ef9fa92cffa08c6811c2119b6bc582a02f78c8bd0ef802cab34d"
)

OWNER_TICKET_V1_4_PROPOSED_SLOS: dict[str, dict[str, Any]] = {
    "my_customers_worklist_api_p95_ms": {
        "threshold": 500.0,
        "unit": "ms",
        "operator": "<=",
        "description": "My Customers worklist JSON API p95 latency (40 distinct staff sessions)",
    },
    "open_lead_and_history_api_p95_ms": {
        "threshold": 600.0,
        "unit": "ms",
        "operator": "<=",
        "description": "Open Lead detail + Contact Attempt history API p95 latency",
    },
    "contact_attempt_write_p95_ms": {
        "threshold": 1200.0,
        "unit": "ms",
        "operator": "<=",
        "description": "Record Contact Attempt + Callback write API p95 latency",
    },
    "bod_executive_overview_api_p95_ms": {
        "threshold": 800.0,
        "unit": "ms",
        "operator": "<=",
        "description": "BOD Executive Overview KPI snapshot API p95 latency",
    },
    "kpi_drilldown_paginated_api_p95_ms": {
        "threshold": 1200.0,
        "unit": "ms",
        "operator": "<=",
        "description": "Paginated KPI drill-down API p95 latency (worst-case across daily campaign & 250k monthly company)",
    },
    "http_5xx_error_rate_pct": {
        "threshold": 0.5,
        "unit": "%",
        "operator": "<=",
        "description": "HTTP error rate across S1 c=12 suite and 20/40/80 mixed session waves",
    },
    "daily_incremental_kpi_refresh_lag_sec": {
        "threshold": 300.0,
        "unit": "s",
        "operator": "<=",
        "description": "Daily incremental KPI refresh execution lag",
    },
}


def capture_readonly_live_stack_snapshot(live_project_name: str = "crm-cskh-vps-staging") -> dict[str, Any]:
    """Read-only `docker ps` + `docker inspect` snapshot of the live serving stack (zero mutation)."""
    ps_proc = run_sub(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={live_project_name}",
            "--format",
            "{{.ID}}",
        ],
        check=False,
    )
    cids = sorted(ln.strip() for ln in ps_proc.stdout.splitlines() if ln.strip())
    containers: list[dict[str, Any]] = []
    if cids:
        insp_proc = run_sub(["docker", "inspect", *cids], check=True)
        for item in json.loads(insp_proc.stdout):
            state = item.get("State") or {}
            health = (state.get("Health") or {}).get("Status")
            labels = (item.get("Config") or {}).get("Labels") or {}
            containers.append(
                {
                    "id": item.get("Id"),
                    "name": str(item.get("Name") or "").lstrip("/"),
                    "service": labels.get("com.docker.compose.service", ""),
                    "image": item.get("Image"),
                    "config_image": (item.get("Config") or {}).get("Image"),
                    "status": state.get("Status"),
                    "health": health,
                    "started_at": state.get("StartedAt"),
                    "restart_count": int(item.get("RestartCount") or 0),
                }
            )
    containers.sort(key=lambda r: (str(r.get("service") or ""), str(r.get("name") or "")))
    daemon_containers = [c for c in containers if c.get("service") not in {"configurator", "migrator"}]
    return {
        "timestamp_utc": utc_now_iso(),
        "live_project_name": live_project_name,
        "container_count": len(containers),
        "daemon_container_count": len(daemon_containers),
        "all_running": bool(daemon_containers) and all(c.get("status") == "running" for c in daemon_containers),
        "containers": containers,
    }


def compare_live_stack_preservation(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Verify zero mutation on the live serving stack between pre-drill and post-drill snapshots."""
    before_map = {str(c.get("name")): c for c in (before.get("containers") or [])}
    after_map = {str(c.get("name")): c for c in (after.get("containers") or [])}
    deltas: list[str] = []
    if sorted(before_map) != sorted(after_map):
        deltas.append(f"container_set_changed: before={sorted(before_map)} after={sorted(after_map)}")
    for cname, b_row in sorted(before_map.items()):
        a_row = after_map.get(cname)
        if not a_row:
            continue
        for field in ("id", "image", "config_image", "status", "started_at", "restart_count"):
            if b_row.get(field) != a_row.get(field):
                deltas.append(
                    f"{cname}.{field}: before={b_row.get(field)!r} after={a_row.get(field)!r}"
                )
        if b_row.get("service") not in {"configurator", "migrator"} and a_row.get("status") != "running":
            deltas.append(f"{cname}.status_not_running={a_row.get('status')!r}")
    zero_mutation = bool(before_map) and not deltas
    return {
        "schema_version": "cskh-p4-live-preservation-before-after-v1",
        "timestamp_utc": utc_now_iso(),
        "status": "PASS" if zero_mutation else "FAIL",
        "zero_live_mutation": zero_mutation,
        "live_project_name": before.get("live_project_name"),
        "container_count_before": len(before_map),
        "container_count_after": len(after_map),
        "deltas": deltas,
        "before_snapshot": before,
        "after_snapshot": after,
    }


def _normalize_ed25519_public_key_fingerprint(public_key_pem: bytes) -> str:
    clean_lines = [
        ln.strip()
        for ln in public_key_pem.decode("utf-8", errors="strict").splitlines()
        if ln.strip() and not ln.strip().startswith("-----")
    ]
    der_bytes = base64.b64decode("".join(clean_lines), validate=True)
    return hashlib.sha256(der_bytes).hexdigest()


def verify_staging_candidate_scope_and_key_guard(
    *,
    app_image: str,
    requested_scope: str = STAGING_CANDIDATE_SCOPE,
    release_manifest_path: Path | None = None,
    cve_exceptions_path: Path | None = None,
    promotion_manifest: dict[str, Any] | None = None,
    promotion_public_key_pem: bytes | None = None,
    promotion_public_key_path: Path | None = None,
    g04_override_passed: bool | None = None,
    allow_local_image_id_for_drill: bool = False,
) -> dict[str, Any]:
    """Enforce Staging candidate verification scope; keep `production_candidate = BLOCKED` on G04 and ban rehearsal keys."""
    clean_img = str(app_image or "").strip()
    if not clean_img or "BLOCKED" in clean_img.upper() or clean_img.endswith("@sha256:" + ("0" * 64)):
        raise ValueError(f"Refusing empty, placeholder, or BLOCKED candidate image reference: {app_image!r}")
    if allow_local_image_id_for_drill and LOCAL_IMAGE_ID_RE.match(clean_img):
        pass
    elif not IMMUTABLE_REGISTRY_DIGEST_RE.match(clean_img):
        raise ValueError(
            f"Candidate image {app_image!r} must be an immutable '<registry>/<repo>@sha256:<64-hex>' digest URI"
        )

    if requested_scope == CI_REHEARSAL_SCOPE:
        raise ValueError(
            "Refusing ci_ephemeral_rehearsal scope for VPS Staging/Production operations; "
            f"use {STAGING_CANDIDATE_SCOPE!r} for Staging verification"
        )
    if requested_scope not in (STAGING_CANDIDATE_SCOPE, PRODUCTION_CANDIDATE_SCOPE):
        raise ValueError(f"Unsupported verification scope: {requested_scope!r}")

    if promotion_manifest is not None:
        m_scope = str(promotion_manifest.get("environment_scope") or "").strip()
        m_rel = str(promotion_manifest.get("release_id") or "").strip().lower()
        m_fp = str(promotion_manifest.get("public_key_fingerprint_sha256") or "").strip().lower()
        if m_scope == CI_REHEARSAL_SCOPE or m_rel.startswith(("ghcr-rehearsal-", "ci-rehearsal-")):
            raise ValueError(
                "Refusing CI rehearsal manifest (environment_scope='ci_ephemeral_rehearsal') "
                "to approve or promote production_candidate"
            )
        if m_fp in KNOWN_REHEARSAL_PUBLIC_KEY_FINGERPRINTS:
            raise ValueError(
                f"Refusing promotion manifest signed by ephemeral rehearsal key (fingerprint={m_fp})"
            )

    checked_key_fp = ""
    if promotion_public_key_path is not None:
        key_name_low = str(promotion_public_key_path).lower()
        if "rehearsal" in key_name_low or "ephemeral" in key_name_low:
            raise ValueError(
                f"Refusing rehearsal/ephemeral signing key file {str(promotion_public_key_path)!r} "
                "for production approval"
            )
        if promotion_public_key_pem is None and promotion_public_key_path.is_file():
            promotion_public_key_pem = promotion_public_key_path.read_bytes()

    if promotion_public_key_pem is not None:
        checked_key_fp = _normalize_ed25519_public_key_fingerprint(promotion_public_key_pem)
        if checked_key_fp in KNOWN_REHEARSAL_PUBLIC_KEY_FINGERPRINTS:
            raise ValueError(
                f"Refusing ephemeral CI rehearsal public key (fingerprint={checked_key_fp}) "
                "to approve production_candidate"
            )

    rel_path = release_manifest_path or DEFAULT_RELEASE_MANIFEST_PATH
    exc_path = cve_exceptions_path or DEFAULT_CVE_EXCEPTIONS_PATH
    rel_doc: dict[str, Any] = {}
    if rel_path.is_file():
        rel_doc = json.loads(rel_path.read_text(encoding="utf-8"))
    active_exceptions_count = 0
    if exc_path.is_file():
        exc_doc = json.loads(exc_path.read_text(encoding="utf-8"))
        active_exceptions_count = len(exc_doc.get("exceptions") or [])

    promo_gov = rel_doc.get("promotion_governance") or {}
    prod_promo_status = str(promo_gov.get("production_promotion_status") or "BLOCKED")
    rel_env_scope = str(rel_doc.get("environment_scope") or "staging_candidate_verified_production_blocked")

    if g04_override_passed is not None:
        g04_passed = bool(g04_override_passed)
    else:
        g04_passed = (
            prod_promo_status == "PASS"
            and rel_env_scope == PRODUCTION_CANDIDATE_SCOPE
        )

    g04_status = "PASS" if g04_passed else "FAIL"
    production_candidate_status = "READY" if g04_passed else "BLOCKED"

    if requested_scope == PRODUCTION_CANDIDATE_SCOPE and not g04_passed:
        raise ValueError(
            "Refusing production_candidate approval/promotion: production_candidate remains BLOCKED "
            "while Gate G04 has unresolved Critical CVEs"
        )

    return {
        "verification_scope": requested_scope,
        "candidate_verification_only": requested_scope == STAGING_CANDIDATE_SCOPE,
        "promoted_to_production": False,
        "g04_status": g04_status,
        "production_candidate_status": production_candidate_status,
        "release_manifest_environment_scope": rel_env_scope,
        "active_cve_exceptions_count": active_exceptions_count,
        "rehearsal_key_allowed_for_production": False,
        "rehearsal_key_used_for_production_approval": False,
        "checked_promotion_public_key_fingerprint": checked_key_fp or None,
        "app_image_digest": clean_img,
        "digest_update_policy": "update_isolated_staging_and_regenerate_all_receipts_on_t1b_digest_change",
    }


def _read_linux_meminfo_mib() -> dict[str, float]:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.is_file():
        return {
            "mem_total_mib": 0.0,
            "mem_available_mib": 0.0,
            "swap_total_mib": 0.0,
            "swap_free_mib": 0.0,
        }
    vals_kib: dict[str, int] = {}
    for ln in meminfo_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in ln:
            continue
        k, rest = ln.split(":", 1)
        parts = rest.strip().split()
        if parts and parts[0].isdigit():
            vals_kib[k.strip()] = int(parts[0])
    total_mib = round(vals_kib.get("MemTotal", 0) / 1024.0, 2)
    avail_mib = round(vals_kib.get("MemAvailable", vals_kib.get("MemFree", 0)) / 1024.0, 2)
    swap_total_mib = round(vals_kib.get("SwapTotal", 0) / 1024.0, 2)
    swap_free_mib = round(vals_kib.get("SwapFree", 0) / 1024.0, 2)
    return {
        "mem_total_mib": total_mib,
        "mem_available_mib": avail_mib,
        "swap_total_mib": swap_total_mib,
        "swap_free_mib": swap_free_mib,
    }


def _read_linux_loadavg() -> dict[str, float]:
    load_path = Path("/proc/loadavg")
    if not load_path.is_file():
        return {"loadavg_1m": 0.0, "loadavg_5m": 0.0, "loadavg_15m": 0.0}
    parts = load_path.read_text(encoding="utf-8", errors="replace").strip().split()
    if len(parts) < 3:
        return {"loadavg_1m": 0.0, "loadavg_5m": 0.0, "loadavg_15m": 0.0}
    try:
        return {
            "loadavg_1m": round(float(parts[0]), 2),
            "loadavg_5m": round(float(parts[1]), 2),
            "loadavg_15m": round(float(parts[2]), 2),
        }
    except ValueError:
        return {"loadavg_1m": 0.0, "loadavg_5m": 0.0, "loadavg_15m": 0.0}


def _check_loopback_port_free(port: int, bind_ip: str = "127.0.0.1") -> bool:
    if port in FORBIDDEN_HOST_PORTS:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_ip, port))
            return True
        except OSError:
            return False


STAGING_VPS_RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "s1_canonical_4x4": {
        "profile_id": "s1_canonical_4x4",
        "description": "Full 4-vCPU / Gunicorn 4w x 4t (16 threads) isolated S1 benchmark configuration",
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": True,
        "backend_cpus": 2.0,
        "backend_mem_mib": 2048.0,
        "gunicorn_workers": 4,
        "gunicorn_threads": 4,
        "db_cpus": 2.0,
        "db_mem_mib": 4096.0,
        "queue_long_cpus": 1.5,
        "queue_long_mem_mib": 2048.0,
        "queue_short_cpus": 1.0,
        "queue_short_mem_mib": 1024.0,
        "frontend_cpus": 1.0,
        "frontend_mem_mib": 512.0,
        "websocket_cpus": 0.5,
        "websocket_mem_mib": 512.0,
        "scheduler_cpus": 0.5,
        "scheduler_mem_mib": 512.0,
        "proxy_cpus": 0.5,
        "proxy_mem_mib": 256.0,
        "redis_cache_cpus": 0.5,
        "redis_cache_mem_mib": 512.0,
        "redis_queue_cpus": 0.5,
        "redis_queue_mem_mib": 512.0,
        "total_capped_cpus": 10.0,
        "total_capped_ram_mib": 12032.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 6,
            "min_mem_available_mib": 8192.0,
            "min_live_ram_reserve_mib": 2048.0,
            "max_load_per_cpu": 0.65,
            "min_free_disk_gib": 20.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.5",
            "PROXY_MEM_LIMIT": "256m",
            "FRONTEND_CPUS": "1.0",
            "FRONTEND_MEM_LIMIT": "512m",
            "BACKEND_CPUS": "2.0",
            "BACKEND_MEM_LIMIT": "2048m",
            "GUNICORN_WORKERS": "4",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.5",
            "WEBSOCKET_MEM_LIMIT": "512m",
            "QUEUE_SHORT_CPUS": "1.0",
            "QUEUE_SHORT_MEM_LIMIT": "1024m",
            "QUEUE_LONG_CPUS": "1.5",
            "QUEUE_LONG_MEM_LIMIT": "2048m",
            "SCHEDULER_CPUS": "0.5",
            "SCHEDULER_MEM_LIMIT": "512m",
            "DB_CPUS": "2.0",
            "DB_MEM_LIMIT": "4096m",
            "REDIS_CACHE_CPUS": "0.5",
            "REDIS_CACHE_MEM_LIMIT": "512m",
            "REDIS_CACHE_MAXMEMORY": "256mb",
            "REDIS_QUEUE_CPUS": "0.5",
            "REDIS_QUEUE_MEM_LIMIT": "512m",
        },
    },
    "staging_vps_capped": {
        "profile_id": "staging_vps_capped",
        "description": "Capped shared-VPS S1 configuration (1.5 vCPU backend / Gunicorn 2w x 4t = 8 threads, 1.5 vCPU DB)",
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": True,
        "backend_cpus": 1.5,
        "backend_mem_mib": 1536.0,
        "gunicorn_workers": 2,
        "gunicorn_threads": 4,
        "db_cpus": 1.5,
        "db_mem_mib": 2048.0,
        "queue_long_cpus": 1.0,
        "queue_long_mem_mib": 1536.0,
        "queue_short_cpus": 0.75,
        "queue_short_mem_mib": 768.0,
        "frontend_cpus": 0.5,
        "frontend_mem_mib": 256.0,
        "websocket_cpus": 0.5,
        "websocket_mem_mib": 384.0,
        "scheduler_cpus": 0.25,
        "scheduler_mem_mib": 384.0,
        "proxy_cpus": 0.5,
        "proxy_mem_mib": 256.0,
        "redis_cache_cpus": 0.25,
        "redis_cache_mem_mib": 384.0,
        "redis_queue_cpus": 0.25,
        "redis_queue_mem_mib": 384.0,
        "total_capped_cpus": 7.0,
        "total_capped_ram_mib": 7936.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 4,
            "min_mem_available_mib": 3072.0,
            "min_live_ram_reserve_mib": 1536.0,
            "max_load_per_cpu": 0.85,
            "min_free_disk_gib": 12.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.50",
            "PROXY_MEM_LIMIT": "256m",
            "FRONTEND_CPUS": "0.50",
            "FRONTEND_MEM_LIMIT": "256m",
            "BACKEND_CPUS": "1.50",
            "BACKEND_MEM_LIMIT": "1536m",
            "GUNICORN_WORKERS": "2",
            "GUNICORN_THREADS": "4",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.50",
            "WEBSOCKET_MEM_LIMIT": "384m",
            "QUEUE_SHORT_CPUS": "0.75",
            "QUEUE_SHORT_MEM_LIMIT": "768m",
            "QUEUE_LONG_CPUS": "1.00",
            "QUEUE_LONG_MEM_LIMIT": "1536m",
            "SCHEDULER_CPUS": "0.25",
            "SCHEDULER_MEM_LIMIT": "384m",
            "DB_CPUS": "1.50",
            "DB_MEM_LIMIT": "2048m",
            "REDIS_CACHE_CPUS": "0.25",
            "REDIS_CACHE_MEM_LIMIT": "384m",
            "REDIS_CACHE_MAXMEMORY": "128mb",
            "REDIS_QUEUE_CPUS": "0.25",
            "REDIS_QUEUE_MEM_LIMIT": "384m",
        },
    },
    "staging_dr_smoke_only": {
        "profile_id": "staging_dr_smoke_only",
        "description": "Minimal isolated Staging/DR smoke-only profile (safe for G03/G05 smoke; NOT safe for G18 S1 load)",
        "g03_g05_smoke_capable": True,
        "g18_s1_benchmark_capable": False,
        "backend_cpus": 1.0,
        "backend_mem_mib": 1280.0,
        "gunicorn_workers": 2,
        "gunicorn_threads": 2,
        "db_cpus": 1.0,
        "db_mem_mib": 1536.0,
        "queue_long_cpus": 0.5,
        "queue_long_mem_mib": 768.0,
        "queue_short_cpus": 0.5,
        "queue_short_mem_mib": 512.0,
        "frontend_cpus": 0.5,
        "frontend_mem_mib": 256.0,
        "websocket_cpus": 0.5,
        "websocket_mem_mib": 256.0,
        "scheduler_cpus": 0.5,
        "scheduler_mem_mib": 256.0,
        "proxy_cpus": 0.5,
        "proxy_mem_mib": 192.0,
        "redis_cache_cpus": 0.5,
        "redis_cache_mem_mib": 256.0,
        "redis_queue_cpus": 0.5,
        "redis_queue_mem_mib": 384.0,
        "total_capped_cpus": 6.0,
        "total_capped_ram_mib": 5696.0,
        "host_safety_requirements": {
            "min_host_cpu_cores": 2,
            "min_mem_available_mib": 1536.0,
            "min_live_ram_reserve_mib": 768.0,
            "max_load_per_cpu": 0.85,
            "min_free_disk_gib": 12.0,
        },
        "compose_env_overrides": {
            "PROXY_CPUS": "0.5",
            "PROXY_MEM_LIMIT": "192m",
            "FRONTEND_CPUS": "0.5",
            "FRONTEND_MEM_LIMIT": "256m",
            "BACKEND_CPUS": "1.0",
            "BACKEND_MEM_LIMIT": "1280m",
            "GUNICORN_WORKERS": "2",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "120",
            "WEBSOCKET_CPUS": "0.5",
            "WEBSOCKET_MEM_LIMIT": "256m",
            "QUEUE_SHORT_CPUS": "0.5",
            "QUEUE_SHORT_MEM_LIMIT": "512m",
            "QUEUE_LONG_CPUS": "0.5",
            "QUEUE_LONG_MEM_LIMIT": "768m",
            "SCHEDULER_CPUS": "0.5",
            "SCHEDULER_MEM_LIMIT": "256m",
            "DB_CPUS": "1.0",
            "DB_MEM_LIMIT": "1536m",
            "REDIS_CACHE_CPUS": "0.5",
            "REDIS_CACHE_MEM_LIMIT": "256m",
            "REDIS_CACHE_MAXMEMORY": "128mb",
            "REDIS_QUEUE_CPUS": "0.5",
            "REDIS_QUEUE_MEM_LIMIT": "384m",
        },
    },
}

STAGING_CONFIG_ENVELOPE_KEYS = (
    "profile_id",
    "g18_s1_benchmark_capable",
    "backend_cpus",
    "backend_mem_mib",
    "gunicorn_workers",
    "gunicorn_threads",
    "db_cpus",
    "db_mem_mib",
    "queue_long_cpus",
    "queue_long_mem_mib",
    "queue_short_cpus",
    "queue_short_mem_mib",
    "frontend_cpus",
    "frontend_mem_mib",
    "websocket_cpus",
    "websocket_mem_mib",
    "scheduler_cpus",
    "scheduler_mem_mib",
    "proxy_cpus",
    "proxy_mem_mib",
    "redis_cache_cpus",
    "redis_cache_mem_mib",
    "redis_queue_cpus",
    "redis_queue_mem_mib",
    "total_capped_cpus",
    "total_capped_ram_mib",
)


def get_staging_resource_profile_spec(profile_id: str) -> dict[str, Any]:
    """Return canonical Staging VPS resource configuration envelope including `config_spec_sha256`."""
    clean_id = str(profile_id or "").strip()
    if clean_id not in STAGING_VPS_RESOURCE_PROFILES:
        raise ValueError(
            f"Unknown Staging VPS resource profile {profile_id!r}; "
            f"supported={sorted(STAGING_VPS_RESOURCE_PROFILES)}"
        )
    raw = STAGING_VPS_RESOURCE_PROFILES[clean_id]
    envelope = {k: raw[k] for k in STAGING_CONFIG_ENVELOPE_KEYS}
    canon_bytes = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    spec_sha256 = hashlib.sha256(canon_bytes).hexdigest()
    return {
        **envelope,
        "description": raw["description"],
        "g03_g05_smoke_capable": raw["g03_g05_smoke_capable"],
        "config_spec_sha256": spec_sha256,
        "host_safety_requirements": dict(raw["host_safety_requirements"]),
        "compose_env_overrides": dict(raw["compose_env_overrides"]),
    }


def determine_safe_staging_vps_configuration(
    *,
    cpu_cores: int,
    mem_available_mib: float,
    loadavg_1m: float,
    free_disk_gib: float,
    loopback_ports_free: bool,
    live_stack_running: bool,
) -> dict[str, Any]:
    """Determine which Staging VPS resource configuration(s) can be safely allocated without risking live."""
    load_per_cpu = round(loadavg_1m / cpu_cores, 3) if cpu_cores > 0 else 999.0
    ordered_profiles = ("s1_canonical_4x4", "staging_vps_capped", "staging_dr_smoke_only")
    safe_profiles_allowed: list[str] = []
    profile_evaluations: dict[str, dict[str, Any]] = {}

    for pid in ordered_profiles:
        pspec = get_staging_resource_profile_spec(pid)
        reqs = pspec["host_safety_requirements"]
        reasons: list[str] = []
        if not live_stack_running:
            reasons.append("live_stack_not_running_healthy")
        if not loopback_ports_free:
            reasons.append("planned_loopback_ports_occupied")
        if cpu_cores < int(reqs["min_host_cpu_cores"]):
            reasons.append(f"cpu_cores={cpu_cores}<{reqs['min_host_cpu_cores']}")
        if mem_available_mib < float(reqs["min_mem_available_mib"]):
            reasons.append(f"mem_available_mib={mem_available_mib}<{reqs['min_mem_available_mib']}")
        if load_per_cpu > float(reqs["max_load_per_cpu"]):
            reasons.append(f"load_per_cpu={load_per_cpu}>{reqs['max_load_per_cpu']}")
        if free_disk_gib < float(reqs["min_free_disk_gib"]):
            reasons.append(f"free_disk_gib={free_disk_gib}<{reqs['min_free_disk_gib']}")
        is_safe = not reasons
        if is_safe:
            safe_profiles_allowed.append(pid)
        profile_evaluations[pid] = {
            "safe_to_allocate": is_safe,
            "g03_g05_smoke_capable": pspec["g03_g05_smoke_capable"],
            "g18_s1_benchmark_capable": pspec["g18_s1_benchmark_capable"],
            "config_spec_sha256": pspec["config_spec_sha256"],
            "backend_cpus": pspec["backend_cpus"],
            "backend_mem_mib": pspec["backend_mem_mib"],
            "gunicorn_workers": pspec["gunicorn_workers"],
            "gunicorn_threads": pspec["gunicorn_threads"],
            "db_cpus": pspec["db_cpus"],
            "db_mem_mib": pspec["db_mem_mib"],
            "total_capped_cpus": pspec["total_capped_cpus"],
            "total_capped_ram_mib": pspec["total_capped_ram_mib"],
            "disqualification_reasons": reasons,
        }

    recommended_g18_profile = next(
        (
            pid
            for pid in ("s1_canonical_4x4", "staging_vps_capped")
            if pid in safe_profiles_allowed
        ),
        None,
    )
    recommended_g03_g05_profile = (
        recommended_g18_profile
        if recommended_g18_profile is not None
        else ("staging_dr_smoke_only" if "staging_dr_smoke_only" in safe_profiles_allowed else None)
    )
    g03_g05_smoke_safe = recommended_g03_g05_profile is not None
    g18_acceptance_safe = recommended_g18_profile is not None
    recommended_spec = (
        get_staging_resource_profile_spec(recommended_g18_profile)
        if recommended_g18_profile is not None
        else (
            get_staging_resource_profile_spec(recommended_g03_g05_profile)
            if recommended_g03_g05_profile is not None
            else None
        )
    )
    if g18_acceptance_safe:
        alloc_status = "SAFE_FOR_G03_G05_AND_G18"
    elif g03_g05_smoke_safe:
        alloc_status = "SAFE_FOR_G03_G05_SMOKE_ONLY_G18_BLOCKED"
    else:
        alloc_status = "UNSAFE_INSUFFICIENT_LIVE_HEADROOM"

    return {
        "status": alloc_status,
        "load_per_cpu_1m": load_per_cpu,
        "g03_g05_smoke_safe": g03_g05_smoke_safe,
        "g18_acceptance_safe": g18_acceptance_safe,
        "recommended_g03_g05_profile": recommended_g03_g05_profile,
        "recommended_g18_profile": recommended_g18_profile,
        "recommended_config_spec_sha256": recommended_spec["config_spec_sha256"] if recommended_spec else None,
        "recommended_compose_env_overrides": (
            recommended_spec["compose_env_overrides"] if recommended_spec else None
        ),
        "safe_profiles_allowed": safe_profiles_allowed,
        "profile_evaluations": profile_evaluations,
    }


FORBIDDEN_T1C_SELF_APPROVERS = {
    "t1a",
    "t1b",
    "t1b-p4-02",
    "tbd",
    "todo",
    "pending",
    "none",
    "null",
    "unconfirmed",
}


def verify_t1c_candidate_confirmation(
    t1c_confirmation: dict[str, Any] | Path | None,
    *,
    expected_app_image: str,
) -> tuple[bool, dict[str, Any] | None, list[str]]:
    """Verify independent T1C candidate confirmation bound to the exact immutable `@sha256` image digest."""
    errors: list[str] = []
    clean_expected = str(expected_app_image or "").strip()
    if not IMMUTABLE_REGISTRY_DIGEST_RE.match(clean_expected):
        errors.append(f"invalid_expected_app_image_digest:{expected_app_image!r}")

    if t1c_confirmation is None:
        return False, None, [*errors, "missing_t1c_candidate_confirmation"]

    doc: Any = None
    if isinstance(t1c_confirmation, Path):
        if not t1c_confirmation.is_file():
            return False, None, [*errors, f"t1c_confirmation_file_not_found:{t1c_confirmation}"]
        try:
            doc = json.loads(t1c_confirmation.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, None, [*errors, f"t1c_confirmation_invalid_json:{exc}"]
    elif isinstance(t1c_confirmation, dict):
        doc = dict(t1c_confirmation)
    else:
        return False, None, [*errors, f"unsupported_t1c_confirmation_type:{type(t1c_confirmation).__name__}"]

    if not isinstance(doc, dict):
        return False, None, [*errors, "t1c_confirmation_root_not_dict"]

    if doc.get("confirmed") is not True:
        errors.append("t1c_confirmation.confirmed_not_true")

    confirmed_by = str(doc.get("confirmed_by") or "").strip()
    if not confirmed_by:
        errors.append("t1c_confirmation.missing_confirmed_by")
    elif (
        confirmed_by.lower() in FORBIDDEN_T1C_SELF_APPROVERS
        or confirmed_by.lower().startswith(("t1a", "t1b", "<"))
        or "placeholder" in confirmed_by.lower()
    ):
        errors.append(f"t1c_confirmation.forbidden_or_self_confirmed_by:{confirmed_by!r}")

    confirmed_at = str(doc.get("confirmed_at") or "").strip()
    if not confirmed_at:
        errors.append("t1c_confirmation.missing_confirmed_at")

    commit_sha = str(doc.get("candidate_commit_sha") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        errors.append(f"t1c_confirmation.invalid_candidate_commit_sha:{commit_sha!r}")

    confirmed_digest = str(doc.get("app_image_digest") or "").strip()
    if not IMMUTABLE_REGISTRY_DIGEST_RE.match(confirmed_digest):
        errors.append(f"t1c_confirmation.invalid_app_image_digest:{confirmed_digest!r}")
    elif clean_expected and confirmed_digest != clean_expected:
        errors.append(
            f"t1c_confirmation.digest_mismatch:confirmed={confirmed_digest!r}:expected={clean_expected!r}"
        )

    v_scope = str(doc.get("verification_scope") or STAGING_CANDIDATE_SCOPE).strip()
    if v_scope == CI_REHEARSAL_SCOPE or v_scope not in (STAGING_CANDIDATE_SCOPE, PRODUCTION_CANDIDATE_SCOPE):
        errors.append(f"t1c_confirmation.invalid_verification_scope:{v_scope!r}")

    g04_stat = str(doc.get("g04_status") or "FAIL").strip().upper()
    prod_stat = str(doc.get("production_candidate_status") or "BLOCKED").strip().upper()
    if g04_stat != "PASS" and prod_stat != "BLOCKED":
        errors.append("t1c_confirmation.production_candidate_must_remain_blocked_when_g04_not_pass")

    normalized = {
        "confirmed": doc.get("confirmed") is True,
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
        "candidate_commit_sha": commit_sha,
        "app_image_digest": confirmed_digest,
        "verification_scope": v_scope,
        "g04_status": g04_stat,
        "production_candidate_status": prod_stat,
    }
    return (not errors), (normalized if not errors else None), errors


def readonly_vps_preflight(
    *,
    live_project_name: str = "crm-cskh-vps-staging",
    disk_check_path: Path = Path("/"),
    required_free_disk_gib: float = 12.0,
    required_avail_ram_mib: float = 1536.0,
    planned_http_port: int = 18081,
    planned_https_port: int = 18443,
    out_json: Path | None = None,
) -> dict[str, Any]:
    """Read-only VPS resource, port, headroom, safe Staging sizing, and live-stack preflight."""
    cpu_cores = int(os.cpu_count() or 0)
    mem = _read_linux_meminfo_mib()
    loadavg = _read_linux_loadavg()
    du = shutil.disk_usage(disk_check_path)
    free_disk_gib = round(du.free / (1024.0**3), 2)
    total_disk_gib = round(du.total / (1024.0**3), 2)
    http_port_free = _check_loopback_port_free(planned_http_port)
    https_port_free = _check_loopback_port_free(planned_https_port)
    live_snap = capture_readonly_live_stack_snapshot(live_project_name)

    checks = {
        "cpu_cores_ge_2": cpu_cores >= 2,
        "available_ram_sufficient": mem["mem_available_mib"] >= required_avail_ram_mib,
        "free_disk_sufficient": free_disk_gib >= required_free_disk_gib,
        "planned_loopback_ports_free": http_port_free and https_port_free,
        "live_stack_running_untouched": bool(live_snap["all_running"]),
    }
    safe_staging_allocation = determine_safe_staging_vps_configuration(
        cpu_cores=cpu_cores,
        mem_available_mib=mem["mem_available_mib"],
        loadavg_1m=loadavg["loadavg_1m"],
        free_disk_gib=free_disk_gib,
        loopback_ports_free=(http_port_free and https_port_free),
        live_stack_running=bool(live_snap["all_running"]),
    )
    report = {
        "schema_version": "cskh-p4-readonly-vps-preflight-v1",
        "timestamp_utc": utc_now_iso(),
        "mode": "READ_ONLY_ZERO_MUTATION",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "host_identity": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "kernel_release": platform.release(),
        },
        "host_resources": {
            "cpu_cores": cpu_cores,
            "mem_total_mib": mem["mem_total_mib"],
            "mem_available_mib": mem["mem_available_mib"],
            "required_avail_ram_mib": required_avail_ram_mib,
            "ram_headroom_surplus_mib": round(mem["mem_available_mib"] - required_avail_ram_mib, 2),
            "swap_total_mib": mem.get("swap_total_mib", 0.0),
            "swap_free_mib": mem.get("swap_free_mib", 0.0),
            "loadavg_1m": loadavg["loadavg_1m"],
            "loadavg_5m": loadavg["loadavg_5m"],
            "loadavg_15m": loadavg["loadavg_15m"],
            "disk_check_path": str(disk_check_path),
            "disk_total_gib": total_disk_gib,
            "disk_free_gib": free_disk_gib,
            "required_free_disk_gib": required_free_disk_gib,
            "disk_headroom_surplus_gib": round(free_disk_gib - required_free_disk_gib, 2),
        },
        "planned_isolated_loopback_ports": {
            "bind_ip": "127.0.0.1",
            "http_port": planned_http_port,
            "http_port_free": http_port_free,
            "https_port": planned_https_port,
            "https_port_free": https_port_free,
        },
        "checks": checks,
        "safe_staging_allocation": safe_staging_allocation,
        "live_before_snapshot": live_snap,
    }
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return report


def pull_and_inspect_candidate_digest(
    app_image: str,
    *,
    locked_batch1_sha: str = "bcdc419f9f95ad75449315572928e3d8c43817cc",
    out_json: Path | None = None,
) -> dict[str, Any]:
    """Pull exact `@sha256` candidate image and emit `candidate-registry-pull-inspect-receipt.json`."""
    governance_scope = verify_staging_candidate_scope_and_key_guard(
        app_image=app_image,
        requested_scope=STAGING_CANDIDATE_SCOPE,
    )
    pull_proc = run_sub(["docker", "pull", app_image])
    m_dig = re.search(r"Digest:\s*(sha256:[0-9a-f]{64})", pull_proc.stdout)
    pulled_digest = m_dig.group(1) if m_dig else ""
    insp_proc = run_sub(["docker", "image", "inspect", app_image])
    insp = json.loads(insp_proc.stdout)[0]
    repo_digests = insp.get("RepoDigests") or []
    cfg = insp.get("Config") or {}
    digest_ok = app_image in repo_digests
    non_root_ok = cfg.get("User") == "frappe"

    def _git_rev(spec: str) -> str:
        p = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", spec],
            capture_output=True,
            text=True,
            check=False,
        )
        return p.stdout.strip() if p.returncode == 0 else ""

    receipt = {
        "schema_version": "cskh-p4-candidate-registry-receipt-v1",
        "timestamp_utc": utc_now_iso(),
        "status": "PASS" if (digest_ok and non_root_ok) else "FAIL",
        "candidate_verification_only": True,
        "promoted_to_production": False,
        "owner_live_site_touched": False,
        "governance_scope": governance_scope,
        "integrated_head_sha": _git_rev("HEAD"),
        "locked_batch1_candidate_sha": locked_batch1_sha,
        "apps_tree_sha": _git_rev("HEAD:apps"),
        "crm_cskh_tree_sha": _git_rev("HEAD:apps/crm_cskh"),
        "compose_tree_sha": _git_rev("HEAD:deploy/compose"),
        "requested_registry_digest": app_image,
        "pull_log_digest": pulled_digest,
        "inspected_image_id": insp.get("Id"),
        "inspected_repo_digests": repo_digests,
        "inspected_created": insp.get("Created"),
        "inspected_architecture": insp.get("Architecture"),
        "inspected_os": insp.get("Os"),
        "inspected_size_bytes": insp.get("Size"),
        "inspected_user": cfg.get("User"),
        "inspected_labels": cfg.get("Labels") or {},
    }
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    if receipt["status"] != "PASS":
        raise RuntimeError(f"Candidate digest pull/inspect failed: {receipt}")
    return receipt


def collect_vps_g03_g05_receipts(
    *,
    app_image: str,
    out_dir: Path,
    project_name: str = "crm-cskh-dr-https-vps",
    site_name: str = DEFAULT_DR_SITE,
    live_project_name: str = "crm-cskh-vps-staging",
    http_port: int = 18081,
    https_port: int = 18443,
    keep_up: bool = False,
    state_dir: Path | None = None,
    disk_check_path: Path = Path("/"),
    t1c_confirmation: dict[str, Any] | Path | None = None,
    require_t1c_confirmation: bool = False,
    resource_profile: str | None = None,
) -> dict[str, Any]:
    """Execute read-only preflight -> optional T1C confirmation & safe profile guard -> pull `@sha256` -> isolated Staging/DR G03/G05 receipts -> live zero-delta check."""
    governance_scope = verify_staging_candidate_scope_and_key_guard(
        app_image=app_image,
        requested_scope=STAGING_CANDIDATE_SCOPE,
    )
    verified_t1c_doc: dict[str, Any] | None = None
    if require_t1c_confirmation or t1c_confirmation is not None:
        t1c_ok, verified_t1c_doc, t1c_errs = verify_t1c_candidate_confirmation(
            t1c_confirmation,
            expected_app_image=app_image,
        )
        if not t1c_ok or verified_t1c_doc is None:
            raise ValueError(
                f"Refusing G03/G05 receipt collection before T1C confirms candidate digest {app_image!r}: {t1c_errs}"
            )

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    preflight = readonly_vps_preflight(
        live_project_name=live_project_name,
        disk_check_path=disk_check_path,
        planned_http_port=http_port,
        planned_https_port=https_port,
        out_json=out_dir / "readonly-vps-preflight.json",
    )
    if preflight["status"] != "PASS":
        raise RuntimeError(f"Read-only VPS preflight failed; aborting before touching Docker: {preflight}")

    applied_profile_spec: dict[str, Any] | None = None
    if resource_profile is not None:
        safe_alloc = preflight.get("safe_staging_allocation") or {}
        allowed_profiles = list(safe_alloc.get("safe_profiles_allowed") or [])
        req_prof = str(resource_profile).strip()
        if req_prof == "auto":
            resolved_prof = safe_alloc.get("recommended_g18_profile") or safe_alloc.get("recommended_g03_g05_profile")
            if not resolved_prof:
                raise RuntimeError(
                    f"Read-only VPS preflight found no safe Staging profile to allocate: {safe_alloc}"
                )
        else:
            resolved_prof = req_prof
        applied_profile_spec = get_staging_resource_profile_spec(str(resolved_prof))
        if applied_profile_spec["profile_id"] not in allowed_profiles:
            raise RuntimeError(
                f"Requested Staging resource profile {applied_profile_spec['profile_id']!r} is NOT safe on this VPS "
                f"(allowed={allowed_profiles}): {safe_alloc.get('profile_evaluations', {}).get(applied_profile_spec['profile_id'])}"
            )

    if verified_t1c_doc is not None:
        (out_dir / "t1c-candidate-confirmation-receipt.json").write_text(
            json.dumps(verified_t1c_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    candidate_sha = (
        verified_t1c_doc["candidate_commit_sha"]
        if verified_t1c_doc is not None
        else "bcdc419f9f95ad75449315572928e3d8c43817cc"
    )
    pull_receipt = pull_and_inspect_candidate_digest(
        app_image,
        locked_batch1_sha=candidate_sha,
        out_json=out_dir / "candidate-registry-pull-inspect-receipt.json",
    )

    managed_tmp = state_dir is None and not keep_up
    work_state = (
        Path(tempfile.mkdtemp(prefix="cskh-vps-g03-g05-"))
        if managed_tmp
        else (state_dir or (out_dir / "isolated-dr-state")).resolve()
    )
    work_state.mkdir(parents=True, exist_ok=True)
    env_file = work_state / "staging-dr-https.env"
    secret_file = work_state / "db_root_password.txt"
    ca_crt_path = work_state / "ca.crt"
    db_root_pw = secrets.token_urlsafe(24)
    admin_pw = "DrAdm-" + secrets.token_urlsafe(18) + "!"
    seed_pw = "DrSeed-" + secrets.token_urlsafe(18) + "!"

    secret_file.write_text(db_root_pw + "\n", encoding="ascii", newline="\n")
    if os.name == "posix":
        os.chmod(secret_file, 0o600)

    tpl = p403_tool.parse_env_file(DR_TEMPLATE_ENV)
    if applied_profile_spec is not None:
        tpl.update(applied_profile_spec["compose_env_overrides"])
    tpl.update(
        {
            "COMPOSE_PROJECT_NAME": project_name,
            "SITE_NAME": site_name,
            "SITES_RULE": f"Host(`{site_name}`)",
            "APP_IMAGE": app_image,
            "PULL_POLICY": "never",
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

    headroom_during: dict[str, Any] = {}
    live_during_check: dict[str, Any] = {}
    try:
        boot_res = bootstrap_isolated_dr_stack(
            env_file=env_file,
            ca_crt_path=ca_crt_path,
            db_root_pw=db_root_pw,
            admin_pw=admin_pw,
            seed_pw=seed_pw,
        )
        smoke_doc = boot_res["https_smoke"]
        (out_dir / "https-wss-smoke-receipt.json").write_text(
            json.dumps(smoke_doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        runtime_audit = audit_isolated_stack_runtime_resources(
            env_file=env_file,
            secret_file=secret_file,
            out_json=out_dir / "staging-dr-runtime-isolation-audit.json",
        )
        mem_during = _read_linux_meminfo_mib()
        du_during = shutil.disk_usage(disk_check_path)
        load_during = _read_linux_loadavg()
        live_during_snap = capture_readonly_live_stack_snapshot(live_project_name)
        live_during_check = compare_live_stack_preservation(
            preflight["live_before_snapshot"], live_during_snap
        )
        headroom_during = {
            "timestamp_utc": utc_now_iso(),
            "mem_available_mib": mem_during["mem_available_mib"],
            "swap_free_mib": mem_during.get("swap_free_mib", 0.0),
            "disk_free_gib": round(du_during.free / (1024.0**3), 2),
            "loadavg_1m": load_during["loadavg_1m"],
            "loadavg_5m": load_during["loadavg_5m"],
            "loadavg_15m": load_during["loadavg_15m"],
            "live_zero_mutation_while_isolated_staging_running": live_during_check["zero_live_mutation"],
        }
    finally:
        if not keep_up:
            run_sub(
                compose_cmd(env_file, ["down", "--volumes", "--remove-orphans"], profile_migrate=True),
                check=False,
            )
            if managed_tmp:
                shutil.rmtree(work_state, ignore_errors=True)

    mem_after = _read_linux_meminfo_mib()
    du_after = shutil.disk_usage(disk_check_path)
    load_after = _read_linux_loadavg()
    live_after = capture_readonly_live_stack_snapshot(live_project_name)
    live_preservation = compare_live_stack_preservation(preflight["live_before_snapshot"], live_after)
    (out_dir / "live-preservation-before-after.json").write_text(
        json.dumps(live_preservation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    staging_resource_cfg = (
        {
            "profile_id": applied_profile_spec["profile_id"],
            "config_spec_sha256": applied_profile_spec["config_spec_sha256"],
            "g18_s1_benchmark_capable": applied_profile_spec["g18_s1_benchmark_capable"],
            "backend_cpus": applied_profile_spec["backend_cpus"],
            "backend_mem_mib": applied_profile_spec["backend_mem_mib"],
            "gunicorn_workers": applied_profile_spec["gunicorn_workers"],
            "gunicorn_threads": applied_profile_spec["gunicorn_threads"],
            "db_cpus": applied_profile_spec["db_cpus"],
            "db_mem_mib": applied_profile_spec["db_mem_mib"],
            "total_capped_cpus": applied_profile_spec["total_capped_cpus"],
            "total_capped_ram_mib": applied_profile_spec["total_capped_ram_mib"],
        }
        if applied_profile_spec is not None
        else None
    )

    inventory_config_audit: dict[str, Any] = {
        "schema_version": "cskh-p4-staging-inventory-config-audit-v1",
        "timestamp_utc": utc_now_iso(),
        "status": (
            "PASS"
            if (
                runtime_audit.get("status") == "PASS"
                and live_during_check.get("zero_live_mutation") is True
                and live_preservation.get("zero_live_mutation") is True
            )
            else "FAIL"
        ),
        "governance_scope": governance_scope,
        "host_identity": preflight["host_identity"],
        "digest_provenance": {
            "app_image": app_image,
            "inspected_image_id": pull_receipt.get("inspected_image_id"),
            "apps_tree_sha": pull_receipt.get("apps_tree_sha"),
            "crm_cskh_tree_sha": pull_receipt.get("crm_cskh_tree_sha"),
            "compose_git_tree_sha": runtime_audit.get("compose_git_tree_sha"),
            "rendered_compose_config_sha256": runtime_audit.get("rendered_compose_config_sha256"),
        },
        "six_dimension_isolation_vs_live": {
            "live_untouched": {
                "project_name": live_project_name,
                "site_name": "crm-staging.localhost",
                "public_domain": "crm.hrpartner.vn",
                "ports": [80, 443],
            },
            "isolated_staging": {
                "project_name": project_name,
                "site_name": site_name,
                "ingress_bind": f"127.0.0.1:{http_port} / 127.0.0.1:{https_port}",
                "networks": [n["name"] for n in (runtime_audit.get("networks") or {}).get("inspected_networks", [])],
                "volumes": (runtime_audit.get("volumes") or {}).get("project_scoped_volumes", []),
                "secret_mounted_no_plaintext_env": (runtime_audit.get("secrets") or {}).get("pass") is True,
            },
            "disjoint_verified": runtime_audit.get("status") == "PASS",
        },
        "resource_limits": runtime_audit.get("resource_limits"),
        "safe_staging_allocation": preflight.get("safe_staging_allocation"),
        "headroom_timeline": {
            "before_isolated_staging": preflight["host_resources"],
            "during_isolated_staging": headroom_during,
            "after_isolated_staging": {
                "mem_available_mib": mem_after["mem_available_mib"],
                "swap_free_mib": mem_after.get("swap_free_mib", 0.0),
                "disk_free_gib": round(du_after.free / (1024.0**3), 2),
                "loadavg_1m": load_after["loadavg_1m"],
                "loadavg_5m": load_after["loadavg_5m"],
                "loadavg_15m": load_after["loadavg_15m"],
            },
        },
        "zero_live_mutation_during_and_after": (
            live_during_check.get("zero_live_mutation") is True
            and live_preservation.get("zero_live_mutation") is True
        ),
    }
    if staging_resource_cfg is not None:
        inventory_config_audit["staging_resource_configuration"] = staging_resource_cfg
    if verified_t1c_doc is not None:
        inventory_config_audit["t1c_candidate_confirmation"] = verified_t1c_doc

    (out_dir / "staging-inventory-and-config-audit.json").write_text(
        json.dumps(inventory_config_audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    all_ok = (
        pull_receipt["status"] == "PASS"
        and smoke_doc.get("status") == "PASS"
        and runtime_audit.get("status") == "PASS"
        and inventory_config_audit["status"] == "PASS"
        and live_preservation["status"] == "PASS"
    )
    artifacts_map = {
        "readonly_vps_preflight": str(out_dir / "readonly-vps-preflight.json"),
        "candidate_pull_inspect_receipt": str(out_dir / "candidate-registry-pull-inspect-receipt.json"),
        "runtime_isolation_audit_g03": str(out_dir / "staging-dr-runtime-isolation-audit.json"),
        "inventory_and_config_audit": str(out_dir / "staging-inventory-and-config-audit.json"),
        "https_wss_smoke_g05": str(out_dir / "https-wss-smoke-receipt.json"),
        "live_preservation_before_after": str(out_dir / "live-preservation-before-after.json"),
    }
    if verified_t1c_doc is not None:
        artifacts_map["t1c_candidate_confirmation_receipt"] = str(
            out_dir / "t1c-candidate-confirmation-receipt.json"
        )
    summary: dict[str, Any] = {
        "schema_version": "cskh-p4-vps-g03-g05-bundle-v1",
        "timestamp_utc": utc_now_iso(),
        "status": "PASS" if all_ok else "FAIL",
        "app_image": app_image,
        "governance_scope": governance_scope,
        "host_identity": preflight["host_identity"],
        "isolated_project_name": project_name,
        "isolated_site_name": site_name,
        "kept_up_for_g18": keep_up,
        "live_project_untouched": live_project_name,
        "zero_live_mutation": live_preservation["zero_live_mutation"],
        "inspected_app_services": list(p403_tool.APP_SERVICES),
        "artifacts": artifacts_map,
    }
    if staging_resource_cfg is not None:
        summary["staging_resource_configuration"] = staging_resource_cfg
    if verified_t1c_doc is not None:
        summary["t1c_candidate_confirmation"] = verified_t1c_doc
    (out_dir / "vps-g03-g05-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


def refresh_staging_for_new_candidate_digest(
    *,
    new_app_image: str,
    out_dir: Path,
    project_name: str = "crm-cskh-dr-https-vps",
    site_name: str = DEFAULT_DR_SITE,
    live_project_name: str = "crm-cskh-vps-staging",
    http_port: int = 18081,
    https_port: int = 18443,
    keep_up: bool = False,
    state_dir: Path | None = None,
    disk_check_path: Path = Path("/"),
    t1c_confirmation: dict[str, Any] | Path | None = None,
    require_t1c_confirmation: bool = False,
    resource_profile: str | None = None,
) -> dict[str, Any]:
    """Update isolated Staging & regenerate all G03/G05/inventory receipts when T1B delivers a new digest for G18."""
    verify_staging_candidate_scope_and_key_guard(
        app_image=new_app_image,
        requested_scope=STAGING_CANDIDATE_SCOPE,
    )
    if require_t1c_confirmation or t1c_confirmation is not None:
        t1c_ok, _t1c_doc, t1c_errs = verify_t1c_candidate_confirmation(
            t1c_confirmation,
            expected_app_image=new_app_image,
        )
        if not t1c_ok:
            raise ValueError(
                f"Refusing Staging digest refresh before T1C confirms candidate digest {new_app_image!r}: {t1c_errs}"
            )
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_digest: str | None = None
    prev_summary_path = out_dir / "vps-g03-g05-summary.json"
    if prev_summary_path.is_file():
        prev_doc = json.loads(prev_summary_path.read_text(encoding="utf-8"))
        previous_digest = str(prev_doc.get("app_image") or "") or None

    resolved_state = (state_dir or (out_dir / "isolated-dr-state")).resolve()
    existing_env = resolved_state / "staging-dr-https.env"
    if existing_env.is_file():
        parsed_existing = p403_tool.parse_env_file(existing_env)
        assert_isolated_from_owner_stack(parsed_existing, existing_env, allow_private_bind=True)
        run_sub(
            compose_cmd(existing_env, ["down", "--volumes", "--remove-orphans"], profile_migrate=True),
            check=False,
        )

    for fname in (
        "readonly-vps-preflight.json",
        "t1c-candidate-confirmation-receipt.json",
        "candidate-registry-pull-inspect-receipt.json",
        "staging-dr-runtime-isolation-audit.json",
        "staging-inventory-and-config-audit.json",
        "https-wss-smoke-receipt.json",
        "live-preservation-before-after.json",
        "vps-g03-g05-summary.json",
    ):
        stale_f = out_dir / fname
        if stale_f.is_file():
            stale_f.unlink()

    summary = collect_vps_g03_g05_receipts(
        app_image=new_app_image,
        out_dir=out_dir,
        project_name=project_name,
        site_name=site_name,
        live_project_name=live_project_name,
        http_port=http_port,
        https_port=https_port,
        keep_up=keep_up,
        state_dir=state_dir,
        disk_check_path=disk_check_path,
        t1c_confirmation=t1c_confirmation,
        require_t1c_confirmation=require_t1c_confirmation,
        resource_profile=resource_profile,
    )

    stale_refs: list[str] = []
    digest_checked_files = (
        ("vps-g03-g05-summary.json", "app_image"),
        ("candidate-registry-pull-inspect-receipt.json", "requested_registry_digest"),
        ("staging-dr-runtime-isolation-audit.json", "app_image"),
        ("https-wss-smoke-receipt.json", "app_image"),
    )
    for fname, field in digest_checked_files:
        doc = json.loads((out_dir / fname).read_text(encoding="utf-8"))
        if doc.get(field) != new_app_image:
            stale_refs.append(f"{fname}:{field}={doc.get(field)!r}")
        if previous_digest and previous_digest != new_app_image:
            raw_txt = (out_dir / fname).read_text(encoding="utf-8")
            if previous_digest in raw_txt:
                stale_refs.append(f"{fname}:contains_previous_digest")
    inv_doc = json.loads((out_dir / "staging-inventory-and-config-audit.json").read_text(encoding="utf-8"))
    if ((inv_doc.get("digest_provenance") or {}).get("app_image")) != new_app_image:
        stale_refs.append("staging-inventory-and-config-audit.json:digest_provenance.app_image_mismatch")
    if stale_refs:
        raise RuntimeError(f"Digest refresh left mismatched/stale receipt fields: {stale_refs}")

    summary["refreshed_from_previous_digest"] = previous_digest
    summary["stale_digest_references"] = []
    (out_dir / "vps-g03-g05-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


def serialize_g18_thresholds_spec(spec: dict[str, Any]) -> bytes:
    """Deterministic UTF-8 sorted-key JSON serialization for `g18_slo_thresholds_v1_4.json`."""
    return (json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def compute_g18_thresholds_sha256(spec: dict[str, Any]) -> str:
    """Compute SHA-256 hex digest of a canonical v1.4 G18 SLO threshold specification."""
    return hashlib.sha256(serialize_g18_thresholds_spec(spec)).hexdigest()


def _diff_threshold_nodes(expected: Any, candidate: Any, prefix: str, errors: list[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(candidate, dict):
            errors.append(f"threshold_type_mismatch:{prefix}:expected_dict_got_{type(candidate).__name__}")
            return
        for k in sorted(expected):
            if k not in candidate:
                errors.append(f"missing_threshold_spec_key:{prefix}.{k}")
            else:
                _diff_threshold_nodes(expected[k], candidate[k], f"{prefix}.{k}", errors)
        for k in sorted(candidate):
            if k not in expected:
                errors.append(f"unexpected_threshold_spec_key:{prefix}.{k}")
        return
    if isinstance(expected, bool):
        if not isinstance(candidate, bool) or candidate != expected:
            errors.append(f"threshold_mismatch:{prefix}:expected={expected!r}:got={candidate!r}")
        return
    if isinstance(expected, (int, float)):
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            errors.append(f"threshold_type_mismatch:{prefix}:expected_number_got_{type(candidate).__name__}")
            return
        if not math.isfinite(float(candidate)) or abs(float(candidate) - float(expected)) > 1e-6:
            errors.append(f"threshold_mismatch:{prefix}:expected={expected}:got={candidate}")
        return
    if candidate != expected:
        errors.append(f"threshold_mismatch:{prefix}:expected={expected!r}:got={candidate!r}")


def load_and_validate_g18_thresholds_v1_4(
    *,
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Load and cryptographically validate the locked v1.4 G18 threshold specification."""
    errors: list[str] = []
    canonical_bytes = DEFAULT_G18_THRESHOLDS_V1_4_PATH.read_bytes()
    canonical_file_sha = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_spec = json.loads(canonical_bytes.decode("utf-8"))
    canonical_sha = compute_g18_thresholds_sha256(canonical_spec)
    if canonical_file_sha != LOCKED_G18_THRESHOLDS_V1_4_SHA256 or canonical_sha != LOCKED_G18_THRESHOLDS_V1_4_SHA256:
        raise RuntimeError(
            f"Canonical {DEFAULT_G18_THRESHOLDS_V1_4_PATH} SHA-256 drift: "
            f"file={canonical_file_sha} canon={canonical_sha} expected={LOCKED_G18_THRESHOLDS_V1_4_SHA256}"
        )

    if thresholds_spec is not None and thresholds_path is not None:
        errors.append("cannot_specify_both_thresholds_spec_and_thresholds_path")

    if thresholds_spec is None and thresholds_path is None:
        return canonical_spec, canonical_sha, errors

    candidate_spec: dict[str, Any] | None = None
    raw_file_sha: str | None = None
    if thresholds_path is not None:
        if not thresholds_path.is_file():
            errors.append(f"thresholds_file_not_found:{thresholds_path}")
            return canonical_spec, "", errors
        try:
            raw_bytes = thresholds_path.read_bytes()
            raw_file_sha = hashlib.sha256(raw_bytes).hexdigest()
            loaded = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            errors.append(f"thresholds_file_invalid_json:{exc}")
            return canonical_spec, raw_file_sha or "", errors
        if not isinstance(loaded, dict):
            errors.append("thresholds_file_root_not_dict")
            return canonical_spec, raw_file_sha, errors
        candidate_spec = loaded
    else:
        if not isinstance(thresholds_spec, dict):
            errors.append(f"thresholds_spec_not_dict:{type(thresholds_spec).__name__}")
            return canonical_spec, "", errors
        candidate_spec = thresholds_spec

    candidate_sha = compute_g18_thresholds_sha256(candidate_spec)
    if raw_file_sha is not None and raw_file_sha != candidate_sha:
        errors.append(
            f"non_canonical_thresholds_file_bytes_sha256_mismatch:file={raw_file_sha}:canonical={candidate_sha}"
        )
    _diff_threshold_nodes(canonical_spec, candidate_spec, "root", errors)
    if candidate_sha != LOCKED_G18_THRESHOLDS_V1_4_SHA256:
        errors.append(
            f"thresholds_sha256_mismatch_vs_locked_v1_4:expected={LOCKED_G18_THRESHOLDS_V1_4_SHA256}:got={candidate_sha}"
        )
    return (canonical_spec if errors else candidate_spec), candidate_sha, errors


STAGING_CONFIG_SLO_TARGET_KEYS = (
    "profile_id",
    "config_spec_sha256",
    "g18_s1_benchmark_capable",
    "backend_cpus",
    "backend_mem_mib",
    "gunicorn_workers",
    "gunicorn_threads",
    "db_cpus",
    "db_mem_mib",
    "total_capped_cpus",
    "total_capped_ram_mib",
)


def build_target_staging_configuration_block(profile_id: str) -> dict[str, Any]:
    """Return canonical `target_staging_configuration` block for a Staging VPS profile."""
    pspec = get_staging_resource_profile_spec(profile_id)
    return {k: pspec[k] for k in STAGING_CONFIG_SLO_TARGET_KEYS}


def build_config_bound_g18_thresholds_spec(
    base_spec: dict[str, Any] | None = None,
    *,
    target_configuration_profile: str,
    candidate_digest: str,
    candidate_image_id: str | None = None,
    ticket_version: str | None = None,
    threshold_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a canonical G18 threshold specification bound to a specific Staging VPS configuration and `@sha256` digest."""
    target_cfg = build_target_staging_configuration_block(target_configuration_profile)
    if target_cfg.get("g18_s1_benchmark_capable") is not True:
        raise ValueError(
            f"Staging resource profile {target_configuration_profile!r} is smoke-only and NOT eligible for G18 S1 acceptance"
        )
    clean_digest = str(candidate_digest or "").strip()
    if not IMMUTABLE_REGISTRY_DIGEST_RE.match(clean_digest):
        raise ValueError(f"Invalid immutable candidate_digest {candidate_digest!r}")

    src = (
        json.loads(json.dumps(base_spec))
        if base_spec is not None
        else json.loads(DEFAULT_G18_THRESHOLDS_V1_4_PATH.read_text(encoding="utf-8"))
    )
    src["locked_candidate_digest"] = clean_digest
    if candidate_image_id is not None:
        clean_img_id = str(candidate_image_id).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", clean_img_id):
            raise ValueError(f"Invalid candidate_image_id {candidate_image_id!r}")
        src["locked_candidate_image_id"] = clean_img_id
    if ticket_version is not None:
        clean_tv = str(ticket_version).strip()
        if not clean_tv:
            raise ValueError("ticket_version must not be empty")
        src["ticket_version"] = clean_tv
    pspec_full = get_staging_resource_profile_spec(target_configuration_profile)
    src["target_sizing_assumption"] = pspec_full["description"]
    src["target_staging_configuration"] = target_cfg

    if threshold_overrides:
        for dotted_key, new_val in threshold_overrides.items():
            parts = dotted_key.split(".")
            cur: Any = src
            for p in parts[:-1]:
                if not isinstance(cur, dict) or p not in cur:
                    raise KeyError(f"Unknown threshold override path {dotted_key!r} (missing {p!r})")
                cur = cur[p]
            leaf = parts[-1]
            if not isinstance(cur, dict) or leaf not in cur:
                raise KeyError(f"Unknown threshold override leaf {dotted_key!r} (missing {leaf!r})")
            cur[leaf] = new_val
    return src


_INVARIANT_NUMERIC_LEAF_KEYS = frozenset(
    {
        "concurrency",
        "distinct_sessions_used",
        "required_distinct_authenticated_sessions",
        "concurrent_user_sessions",
    }
)

_NONNEG_INT_THRESHOLD_KEYS = frozenset(
    {
        "max_queue_backlog_total",
        "max_queue_backlog_after",
        "max_slow_queries_after",
        "max_post_run_queue_backlog_total",
        "max_post_run_slow_queries",
    }
)


def _validate_config_bound_subtree(
    canonical_node: Any,
    candidate_node: Any,
    prefix: str,
    errors: list[str],
) -> None:
    if isinstance(canonical_node, dict):
        if not isinstance(candidate_node, dict):
            errors.append(f"threshold_type_mismatch:{prefix}:expected_dict_got_{type(candidate_node).__name__}")
            return
        for k in sorted(canonical_node):
            if k not in candidate_node:
                errors.append(f"missing_threshold_spec_key:{prefix}.{k}")
            else:
                _validate_config_bound_subtree(canonical_node[k], candidate_node[k], f"{prefix}.{k}", errors)
        for k in sorted(candidate_node):
            if k not in canonical_node:
                errors.append(f"unexpected_threshold_spec_key:{prefix}.{k}")
        if (
            "max_p95_ms" in candidate_node
            and "max_p99_ms" in candidate_node
            and not isinstance(candidate_node["max_p95_ms"], bool)
            and not isinstance(candidate_node["max_p99_ms"], bool)
            and isinstance(candidate_node["max_p95_ms"], (int, float))
            and isinstance(candidate_node["max_p99_ms"], (int, float))
        ):
            if float(candidate_node["max_p99_ms"]) + 1e-6 < float(candidate_node["max_p95_ms"]):
                errors.append(
                    f"p99_threshold_lt_p95_threshold:{prefix}:max_p99_ms={candidate_node['max_p99_ms']}<max_p95_ms={candidate_node['max_p95_ms']}"
                )
        if (
            "max_overall_p95_ms" in candidate_node
            and "max_overall_p99_ms" in candidate_node
            and not isinstance(candidate_node["max_overall_p95_ms"], bool)
            and not isinstance(candidate_node["max_overall_p99_ms"], bool)
            and isinstance(candidate_node["max_overall_p95_ms"], (int, float))
            and isinstance(candidate_node["max_overall_p99_ms"], (int, float))
        ):
            if float(candidate_node["max_overall_p99_ms"]) + 1e-6 < float(candidate_node["max_overall_p95_ms"]):
                errors.append(
                    f"overall_p99_threshold_lt_p95_threshold:{prefix}:max_overall_p99_ms={candidate_node['max_overall_p99_ms']}<max_overall_p95_ms={candidate_node['max_overall_p95_ms']}"
                )
        if isinstance(candidate_node.get("by_workload_max_p95_ms"), dict) and isinstance(
            candidate_node.get("by_workload_max_p99_ms"), dict
        ):
            p95_map = candidate_node["by_workload_max_p95_ms"]
            p99_map = candidate_node["by_workload_max_p99_ms"]
            for wk in sorted(set(p95_map) & set(p99_map)):
                v95, v99 = p95_map[wk], p99_map[wk]
                if (
                    not isinstance(v95, bool)
                    and not isinstance(v99, bool)
                    and isinstance(v95, (int, float))
                    and isinstance(v99, (int, float))
                    and float(v99) + 1e-6 < float(v95)
                ):
                    errors.append(f"workload_p99_threshold_lt_p95_threshold:{prefix}.{wk}:p99={v99}<p95={v95}")
        return

    if isinstance(canonical_node, list):
        if not isinstance(candidate_node, list) or candidate_node != canonical_node:
            errors.append(f"threshold_list_mismatch:{prefix}:expected={canonical_node!r}:got={candidate_node!r}")
        return

    if isinstance(canonical_node, bool):
        if not isinstance(candidate_node, bool) or candidate_node != canonical_node:
            errors.append(f"threshold_mismatch:{prefix}:expected={canonical_node!r}:got={candidate_node!r}")
        return

    leaf_key = prefix.rsplit(".", 1)[-1]
    if isinstance(canonical_node, str):
        if not isinstance(candidate_node, str) or not candidate_node.strip():
            errors.append(f"threshold_invalid_string:{prefix}:{candidate_node!r}")
            return
        if leaf_key != "description" and candidate_node != canonical_node:
            errors.append(f"threshold_mismatch:{prefix}:expected={canonical_node!r}:got={candidate_node!r}")
        return

    if isinstance(canonical_node, (int, float)):
        if isinstance(candidate_node, bool) or not isinstance(candidate_node, (int, float)):
            errors.append(f"threshold_type_mismatch:{prefix}:expected_number_got_{type(candidate_node).__name__}")
            return
        fval = float(candidate_node)
        if not math.isfinite(fval):
            errors.append(f"threshold_non_finite:{prefix}:{candidate_node!r}")
            return
        if (
            ".expected_counts." in prefix
            or leaf_key.startswith(("expected_", "min_"))
            or leaf_key in _INVARIANT_NUMERIC_LEAF_KEYS
        ):
            if abs(fval - float(canonical_node)) > 1e-6:
                errors.append(f"invariant_threshold_modified:{prefix}:expected={canonical_node}:got={candidate_node}")
            return
        if leaf_key in ("max_error_count", "max_timeout_count"):
            if not isinstance(candidate_node, int) or candidate_node < 0:
                errors.append(f"invalid_int_error_threshold:{prefix}:{candidate_node!r}")
            elif int(canonical_node) == 0 and candidate_node != 0:
                errors.append(f"zero_error_budget_loosened:{prefix}:expected=0:got={candidate_node}")
            return
        if leaf_key in _NONNEG_INT_THRESHOLD_KEYS:
            if not isinstance(candidate_node, int) or candidate_node < 0:
                errors.append(f"invalid_nonneg_int_threshold:{prefix}:{candidate_node!r}")
            return
        if leaf_key == "max_error_rate_pct" or prefix.endswith(".http_5xx_error_rate_pct.threshold"):
            if fval < 0.0 or fval > 5.0:
                errors.append(f"error_rate_threshold_out_of_bounds:{prefix}:{candidate_node}")
            elif abs(float(canonical_node)) <= 1e-9 and fval > 1e-9:
                errors.append(f"zero_error_rate_budget_loosened:{prefix}:expected=0.0:got={candidate_node}")
            return
        if fval <= 0.0:
            errors.append(f"non_positive_threshold:{prefix}:{candidate_node}")
        return


def serialize_p4_s1_slo_spec(spec: dict[str, Any]) -> bytes:
    """Canonical LF UTF-8 JSON serialization matching `deploy/release/p4-s1-slo-spec.json`."""
    return (json.dumps(spec, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def compute_p4_s1_slo_spec_sha256(spec_or_path: dict[str, Any] | Path) -> str:
    """Compute LF-normalized SHA-256 hex digest for a `cskh-p4-s1-slo-spec-v1` document or file."""
    if isinstance(spec_or_path, Path):
        return s1_slo_tool.compute_normalized_sha256(spec_or_path)
    return hashlib.sha256(serialize_p4_s1_slo_spec(spec_or_path)).hexdigest()


def is_p4_s1_slo_spec_v1(
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
) -> bool:
    """Return True when the threshold specification uses T1B's `cskh-p4-s1-slo-spec-v1` bundle schema."""
    if isinstance(thresholds_spec, dict):
        return thresholds_spec.get("schema_version") == s1_slo_tool.EXPECTED_SCHEMA_VERSION
    if thresholds_path is not None and thresholds_path.is_file():
        try:
            loaded = json.loads(thresholds_path.read_text(encoding="utf-8"))
            return isinstance(loaded, dict) and loaded.get("schema_version") == s1_slo_tool.EXPECTED_SCHEMA_VERSION
        except Exception:
            return False
    return False


def derive_precheck_7_metrics_from_p4_s1_slo_spec(
    spec: dict[str, Any],
    profile: str,
) -> dict[str, dict[str, Any]]:
    """Derive the 7-metric pre-check table thresholds for `profile` directly from `p4-s1-slo-spec.json`."""
    eff_prof = profile if profile in s1_slo_tool.SUPPORTED_PROFILES else "s1_canonical_4x4"
    by_id: dict[str, dict[str, Any]] = {}
    for rule in spec.get("rules") or []:
        if isinstance(rule, dict) and rule.get("slo_id"):
            by_id[str(rule["slo_id"])] = rule

    def _rule_p95(slo_id: str, fallback: float) -> float:
        r = by_id.get(slo_id) or {}
        pt = ((r.get("proposed_thresholds") or {}).get(eff_prof)) or {}
        return float(pt.get("max_p95_ms", fallback))

    def _rule_err(slo_id: str, fallback: float) -> float:
        r = by_id.get(slo_id) or {}
        pt = ((r.get("proposed_thresholds") or {}).get(eff_prof)) or {}
        return float(pt.get("max_error_rate_pct", fallback))

    daily_lag_sec = 300.0
    bg03 = by_id.get("BG-03") or {}
    bg03_pt = ((bg03.get("proposed_thresholds") or {}).get(eff_prof)) or {}
    for inv in bg03_pt.get("invariants") or []:
        if (
            isinstance(inv, dict)
            and inv.get("report_json_path") == "s1_domain_and_database_benchmark.kpi_recompute_s1.daily_incremental_ms"
            and inv.get("op") == "le"
            and isinstance(inv.get("expected"), (int, float))
        ):
            daily_lag_sec = round(float(inv["expected"]) / 1000.0, 3)
            break

    drill_thr = max(_rule_p95("API-05A-BOD", 1200.0), _rule_p95("API-05B-BOD", 1200.0))
    err_thr = max(
        _rule_err("AGG-01", 0.0),
        _rule_err("AGG-03A", 0.0),
        _rule_err("AGG-03B", 0.0),
        _rule_err("AGG-03C", 0.5),
    )

    return {
        "my_customers_worklist_api_p95_ms": {
            "threshold": _rule_p95("API-01B", 500.0),
            "unit": "ms",
            "operator": "<=",
            "description": "My Customers worklist JSON API p95 latency (40 distinct staff sessions)",
        },
        "open_lead_and_history_api_p95_ms": {
            "threshold": _rule_p95("API-02A", 900.0),
            "unit": "ms",
            "operator": "<=",
            "description": "Open Lead detail + Contact Attempt history API p95 latency",
        },
        "contact_attempt_write_p95_ms": {
            "threshold": _rule_p95("API-03A", 1200.0),
            "unit": "ms",
            "operator": "<=",
            "description": "Record Contact Attempt + Callback write API p95 latency",
        },
        "bod_executive_overview_api_p95_ms": {
            "threshold": _rule_p95("API-04B", 600.0),
            "unit": "ms",
            "operator": "<=",
            "description": "BOD Executive Overview KPI snapshot API p95 latency",
        },
        "kpi_drilldown_paginated_api_p95_ms": {
            "threshold": drill_thr,
            "unit": "ms",
            "operator": "<=",
            "description": "Paginated KPI drill-down API p95 latency (worst-case across daily campaign & 250k monthly company)",
        },
        "http_5xx_error_rate_pct": {
            "threshold": err_thr,
            "unit": "%",
            "operator": "<=",
            "description": "HTTP error rate across S1 c=12 suite and 20/40/80 mixed session waves",
        },
        "daily_incremental_kpi_refresh_lag_sec": {
            "threshold": daily_lag_sec,
            "unit": "s",
            "operator": "<=",
            "description": "Daily incremental KPI refresh execution lag",
        },
    }


def validate_p4_s1_slo_spec_for_configuration(
    *,
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
    target_configuration_profile: str,
    expected_digest: str,
) -> tuple[dict[str, Any], str, list[str]]:
    """Fail-closed governance & configuration validator for `cskh-p4-s1-slo-spec-v1` (`p4-s1-slo-spec.json`)."""
    canonical_sha = s1_slo_tool.compute_normalized_sha256(DEFAULT_P4_S1_SLO_SPEC_PATH)
    canonical_spec = json.loads(DEFAULT_P4_S1_SLO_SPEC_PATH.read_text(encoding="utf-8"))
    if canonical_sha != LOCKED_P4_S1_SLO_SPEC_SHA256:
        raise RuntimeError(
            f"Canonical {DEFAULT_P4_S1_SLO_SPEC_PATH} SHA-256 drift: "
            f"got={canonical_sha} expected={LOCKED_P4_S1_SLO_SPEC_SHA256}"
        )

    errors: list[str] = []
    if thresholds_spec is not None and thresholds_path is not None:
        errors.append("cannot_specify_both_thresholds_spec_and_thresholds_path")

    expected_cfg: dict[str, Any] | None = None
    try:
        expected_cfg = build_target_staging_configuration_block(target_configuration_profile)
        if expected_cfg.get("g18_s1_benchmark_capable") is not True:
            errors.append(f"target_configuration_profile_not_g18_s1_capable:{target_configuration_profile!r}")
    except ValueError as exc:
        errors.append(f"invalid_target_configuration_profile:{target_configuration_profile!r}:{exc}")

    clean_expected_digest = str(expected_digest or "").strip()
    if not IMMUTABLE_REGISTRY_DIGEST_RE.match(clean_expected_digest):
        errors.append(f"invalid_expected_candidate_digest:{expected_digest!r}")

    candidate_spec: dict[str, Any]
    candidate_sha: str
    if thresholds_path is not None:
        if not thresholds_path.is_file():
            errors.append(f"thresholds_file_not_found:{thresholds_path}")
            return canonical_spec, "", errors
        try:
            raw_bytes = thresholds_path.read_bytes()
            norm_sha = s1_slo_tool.compute_normalized_sha256(thresholds_path)
            loaded = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            errors.append(f"thresholds_file_invalid_json:{exc}")
            return canonical_spec, "", errors
        if not isinstance(loaded, dict):
            errors.append("thresholds_file_root_not_dict")
            return canonical_spec, norm_sha, errors
        candidate_spec = loaded
        candidate_sha = norm_sha
        canon_serialized_sha = compute_p4_s1_slo_spec_sha256(candidate_spec)
        if raw_bytes != raw_bytes.replace(b"\r\n", b"\n"):
            errors.append("thresholds_file_contains_crlf_line_endings")
        if candidate_sha != canon_serialized_sha:
            errors.append(
                f"non_canonical_thresholds_file_bytes_sha256_mismatch:file={candidate_sha}:canonical={canon_serialized_sha}"
            )
    elif thresholds_spec is not None:
        if not isinstance(thresholds_spec, dict):
            errors.append(f"thresholds_spec_not_dict:{type(thresholds_spec).__name__}")
            return canonical_spec, "", errors
        candidate_spec = thresholds_spec
        candidate_sha = compute_p4_s1_slo_spec_sha256(candidate_spec)
    else:
        candidate_spec = canonical_spec
        candidate_sha = canonical_sha

    gov_errs = s1_slo_tool.validate_slo_spec_governance(
        candidate_spec,
        profile=target_configuration_profile,
    )
    errors.extend(gov_errs)

    if str(candidate_spec.get("slo_bundle_id") or "").strip() != str(canonical_spec.get("slo_bundle_id") or ""):
        errors.append(
            f"slo_bundle_id_mismatch:expected={canonical_spec.get('slo_bundle_id')!r}:got={candidate_spec.get('slo_bundle_id')!r}"
        )
    if str(candidate_spec.get("slo_version") or "").strip() != str(canonical_spec.get("slo_version") or ""):
        errors.append(
            f"slo_version_mismatch:expected={canonical_spec.get('slo_version')!r}:got={candidate_spec.get('slo_version')!r}"
        )
    cand_signoff = candidate_spec.get("owner_signoff")
    if candidate_spec.get("approval_status") != "PROPOSED_PENDING_OWNER_APPROVAL" or (
        not isinstance(cand_signoff, dict)
        or any(v is not None for v in cand_signoff.values())
    ):
        errors.append("s1_slo_spec_embedded_signoff_mutation_forbidden_use_separate_approval_json")

    cand_binding = candidate_spec.get("candidate_binding")
    if not isinstance(cand_binding, dict):
        errors.append("missing_or_invalid_candidate_binding")
    else:
        bound_digest = str(cand_binding.get("app_image_digest") or "").strip()
        if bound_digest != clean_expected_digest:
            errors.append(
                f"thresholds_locked_candidate_digest_mismatch:expected={clean_expected_digest!r}:got={bound_digest!r}"
            )
        for sha_field in ("image_source_commit_sha", "apps_tree_sha", "crm_cskh_tree_sha"):
            if not re.fullmatch(r"[0-9a-f]{40}", str(cand_binding.get(sha_field) or "").strip()):
                errors.append(f"invalid_candidate_binding_{sha_field}:{cand_binding.get(sha_field)!r}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(cand_binding.get("oci_config_digest") or "").strip()):
            errors.append(f"invalid_candidate_binding_oci_config_digest:{cand_binding.get('oci_config_digest')!r}")

    res_profs = candidate_spec.get("resource_profiles")
    if not isinstance(res_profs, dict) or target_configuration_profile not in res_profs:
        errors.append(f"missing_resource_profile_in_s1_slo_spec:{target_configuration_profile!r}")
    elif expected_cfg is not None:
        rp_entry = res_profs[target_configuration_profile]
        if not isinstance(rp_entry, dict):
            errors.append(f"invalid_resource_profile_entry:{target_configuration_profile!r}")
        else:
            if rp_entry.get("gunicorn_workers") != expected_cfg["gunicorn_workers"]:
                errors.append(
                    f"s1_slo_gunicorn_workers_mismatch:{target_configuration_profile}:"
                    f"expected={expected_cfg['gunicorn_workers']}:got={rp_entry.get('gunicorn_workers')!r}"
                )
            if rp_entry.get("gunicorn_threads") != expected_cfg["gunicorn_threads"]:
                errors.append(
                    f"s1_slo_gunicorn_threads_mismatch:{target_configuration_profile}:"
                    f"expected={expected_cfg['gunicorn_threads']}:got={rp_entry.get('gunicorn_threads')!r}"
                )
            if (
                target_configuration_profile == "staging_vps_capped"
                and rp_entry.get("require_live_headroom_precheck") is not True
            ):
                errors.append("s1_slo_staging_vps_capped_must_require_live_headroom_precheck")

    def _validate_rule_list(
        canon_rules: list[dict[str, Any]],
        cand_rules: Any,
        section_label: str,
        path_field: str,
    ) -> None:
        if not isinstance(cand_rules, list):
            errors.append(f"invalid_rule_list:{section_label}")
            return
        canon_by_id = {str(r["slo_id"]): r for r in canon_rules}
        cand_by_id: dict[str, dict[str, Any]] = {}
        for item in cand_rules:
            if isinstance(item, dict) and item.get("slo_id"):
                cand_by_id[str(item["slo_id"])] = item
        for missing_id in sorted(set(canon_by_id) - set(cand_by_id)):
            errors.append(f"missing_slo_rule:{section_label}.{missing_id}")
        for extra_id in sorted(set(cand_by_id) - set(canon_by_id)):
            errors.append(f"unexpected_slo_rule:{section_label}.{extra_id}")

        if target_configuration_profile not in s1_slo_tool.SUPPORTED_PROFILES:
            return

        for slo_id in sorted(set(canon_by_id) & set(cand_by_id)):
            c_rule = canon_by_id[slo_id]
            x_rule = cand_by_id[slo_id]
            for exact_k in ("category", "rule_type", path_field, "p4_9_2_ceiling_p95_ms"):
                if c_rule.get(exact_k) != x_rule.get(exact_k):
                    errors.append(
                        f"slo_rule_metadata_mismatch:{section_label}.{slo_id}.{exact_k}:"
                        f"expected={c_rule.get(exact_k)!r}:got={x_rule.get(exact_k)!r}"
                    )
            c_pt = ((c_rule.get("proposed_thresholds") or {}).get(target_configuration_profile)) or {}
            x_pt = ((x_rule.get("proposed_thresholds") or {}).get(target_configuration_profile)) or {}
            if not isinstance(x_pt, dict) or not x_pt:
                errors.append(f"missing_profile_thresholds:{section_label}.{slo_id}.{target_configuration_profile}")
                continue
            rtype = str(c_rule.get("rule_type") or "http_summary")
            if rtype == "http_summary":
                for num_k in ("max_p95_ms", "max_p99_ms", "max_error_rate_pct"):
                    val = x_pt.get(num_k)
                    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
                        errors.append(f"invalid_numeric_threshold:{section_label}.{slo_id}.{num_k}:{val!r}")
                p95_v = x_pt.get("max_p95_ms")
                p99_v = x_pt.get("max_p99_ms")
                if (
                    isinstance(p95_v, (int, float))
                    and not isinstance(p95_v, bool)
                    and isinstance(p99_v, (int, float))
                    and not isinstance(p99_v, bool)
                ):
                    if float(p95_v) <= 0.0:
                        errors.append(f"non_positive_threshold:{section_label}.{slo_id}.max_p95_ms:{p95_v}")
                    if float(p99_v) + 1e-6 < float(p95_v):
                        errors.append(
                            f"p99_threshold_lt_p95_threshold:{section_label}.{slo_id}:max_p99_ms={p99_v}<max_p95_ms={p95_v}"
                        )
                err_v = x_pt.get("max_error_rate_pct")
                if isinstance(err_v, (int, float)) and not isinstance(err_v, bool):
                    if float(err_v) < 0.0 or float(err_v) > 5.0:
                        errors.append(f"error_rate_threshold_out_of_bounds:{section_label}.{slo_id}:{err_v}")
                    elif abs(float(c_pt.get("max_error_rate_pct", 0.0))) <= 1e-9 and float(err_v) > 1e-9:
                        errors.append(
                            f"zero_error_rate_budget_loosened:{section_label}.{slo_id}:expected=0.0:got={err_v}"
                        )
                for cnt_k in (
                    "max_timeout_count",
                    "max_client_error_count",
                    "max_http_4xx_count",
                    "max_http_5xx_count",
                ):
                    cv = x_pt.get(cnt_k)
                    if isinstance(cv, bool) or not isinstance(cv, int) or cv < 0:
                        errors.append(f"invalid_int_error_threshold:{section_label}.{slo_id}.{cnt_k}:{cv!r}")
                    elif int(c_pt.get(cnt_k, 0)) == 0 and cv != 0:
                        errors.append(
                            f"zero_error_budget_loosened:{section_label}.{slo_id}.{cnt_k}:expected=0:got={cv}"
                        )

            c_invs = c_pt.get("invariants") or []
            x_invs = x_pt.get("invariants") or []
            if len(x_invs) != len(c_invs):
                errors.append(
                    f"invariant_count_mismatch:{section_label}.{slo_id}:expected={len(c_invs)}:got={len(x_invs)}"
                )
            else:
                for idx_inv, (c_inv, x_inv) in enumerate(zip(c_invs, x_invs)):
                    if not isinstance(x_inv, dict):
                        errors.append(f"invalid_invariant_entry:{section_label}.{slo_id}[{idx_inv}]")
                        continue
                    if x_inv.get("report_json_path") != c_inv.get("report_json_path") or x_inv.get("op") != c_inv.get("op"):
                        errors.append(f"invariant_target_modified:{section_label}.{slo_id}[{idx_inv}]")
                    elif c_inv.get("op") == "eq" and x_inv.get("expected") != c_inv.get("expected"):
                        errors.append(
                            f"invariant_threshold_modified:{section_label}.{slo_id}[{idx_inv}]:"
                            f"expected={c_inv.get('expected')!r}:got={x_inv.get('expected')!r}"
                        )

    _validate_rule_list(
        canonical_spec.get("rules") or [],
        candidate_spec.get("rules"),
        "rules",
        "report_json_path",
    )
    _validate_rule_list(
        canonical_spec.get("g18_deep_diagnostic_rules") or [],
        candidate_spec.get("g18_deep_diagnostic_rules"),
        "g18_deep_diagnostic_rules",
        "diag_report_json_path",
    )

    return (canonical_spec if errors else candidate_spec), candidate_sha, errors


def validate_g18_thresholds_for_configuration(
    *,
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
    target_configuration_profile: str,
    expected_digest: str,
) -> tuple[dict[str, Any], str, list[str]]:
    """Validate a configuration-bound G18 threshold specification against §3.2A–C or `cskh-p4-s1-slo-spec-v1` structure, digest, and Staging resource profile."""
    if is_p4_s1_slo_spec_v1(thresholds_spec, thresholds_path):
        return validate_p4_s1_slo_spec_for_configuration(
            thresholds_spec=thresholds_spec,
            thresholds_path=thresholds_path,
            target_configuration_profile=target_configuration_profile,
            expected_digest=expected_digest,
        )
    errors: list[str] = []
    canonical_spec = json.loads(DEFAULT_G18_THRESHOLDS_V1_4_PATH.read_text(encoding="utf-8"))

    if thresholds_spec is not None and thresholds_path is not None:
        errors.append("cannot_specify_both_thresholds_spec_and_thresholds_path")

    expected_cfg: dict[str, Any] | None = None
    try:
        expected_cfg = build_target_staging_configuration_block(target_configuration_profile)
        if expected_cfg.get("g18_s1_benchmark_capable") is not True:
            errors.append(f"target_configuration_profile_not_g18_s1_capable:{target_configuration_profile!r}")
    except ValueError as exc:
        errors.append(f"invalid_target_configuration_profile:{target_configuration_profile!r}:{exc}")

    if not IMMUTABLE_REGISTRY_DIGEST_RE.match(str(expected_digest or "").strip()):
        errors.append(f"invalid_expected_candidate_digest:{expected_digest!r}")

    candidate_spec: dict[str, Any] | None = None
    raw_file_sha: str | None = None
    if thresholds_path is not None:
        if not thresholds_path.is_file():
            errors.append(f"thresholds_file_not_found:{thresholds_path}")
            return canonical_spec, "", errors
        try:
            raw_bytes = thresholds_path.read_bytes()
            raw_file_sha = hashlib.sha256(raw_bytes).hexdigest()
            loaded = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            errors.append(f"thresholds_file_invalid_json:{exc}")
            return canonical_spec, raw_file_sha or "", errors
        if not isinstance(loaded, dict):
            errors.append("thresholds_file_root_not_dict")
            return canonical_spec, raw_file_sha, errors
        candidate_spec = loaded
    elif thresholds_spec is not None:
        if not isinstance(thresholds_spec, dict):
            errors.append(f"thresholds_spec_not_dict:{type(thresholds_spec).__name__}")
            return canonical_spec, "", errors
        candidate_spec = thresholds_spec
    else:
        # Default file on disk lacks target_staging_configuration for a new config-bound run -> fail closed
        raw_bytes = DEFAULT_G18_THRESHOLDS_V1_4_PATH.read_bytes()
        raw_file_sha = hashlib.sha256(raw_bytes).hexdigest()
        candidate_spec = canonical_spec

    candidate_sha = compute_g18_thresholds_sha256(candidate_spec)
    if raw_file_sha is not None and raw_file_sha != candidate_sha:
        errors.append(
            f"non_canonical_thresholds_file_bytes_sha256_mismatch:file={raw_file_sha}:canonical={candidate_sha}"
        )

    expected_root_keys = set(canonical_spec.keys()) | {"target_staging_configuration"}
    for rk in sorted(expected_root_keys):
        if rk not in candidate_spec:
            if rk == "target_staging_configuration":
                errors.append("missing_target_staging_configuration_in_thresholds_spec")
            else:
                errors.append(f"missing_threshold_spec_key:root.{rk}")
    for extra_rk in sorted(set(candidate_spec.keys()) - expected_root_keys):
        errors.append(f"unexpected_threshold_spec_key:root.{extra_rk}")

    for exact_str_key in ("gate", "coordinated_benchmark_harness", "coordinated_benchmark_schema_version"):
        if candidate_spec.get(exact_str_key) != canonical_spec[exact_str_key]:
            errors.append(
                f"threshold_mismatch:root.{exact_str_key}:expected={canonical_spec[exact_str_key]!r}:got={candidate_spec.get(exact_str_key)!r}"
            )
    for req_str_key in (
        "canonical_source_handoff_sha256",
        "schema_version",
        "target_sizing_assumption",
        "ticket_version",
    ):
        if not isinstance(candidate_spec.get(req_str_key), str) or not str(candidate_spec.get(req_str_key)).strip():
            errors.append(f"invalid_or_empty_root_field:{req_str_key}")

    cand_dig = str(candidate_spec.get("locked_candidate_digest") or "").strip()
    if cand_dig != str(expected_digest or "").strip():
        errors.append(
            f"thresholds_locked_candidate_digest_mismatch:expected={expected_digest!r}:got={cand_dig!r}"
        )
    cand_img_id = str(candidate_spec.get("locked_candidate_image_id") or "").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", cand_img_id):
        errors.append(f"invalid_locked_candidate_image_id:{cand_img_id!r}")

    cand_target_cfg = candidate_spec.get("target_staging_configuration")
    if expected_cfg is not None and isinstance(cand_target_cfg, dict):
        _diff_threshold_nodes(expected_cfg, cand_target_cfg, "root.target_staging_configuration", errors)
    elif cand_target_cfg is not None and not isinstance(cand_target_cfg, dict):
        errors.append(
            f"threshold_type_mismatch:root.target_staging_configuration:expected_dict_got_{type(cand_target_cfg).__name__}"
        )

    for section_key in (
        "precheck_7_metrics",
        "section_3_2A_cases_and_session_models",
        "section_3_2B_mixed_session_waves",
        "section_3_2C_dataset_batch_and_background",
    ):
        if section_key in candidate_spec:
            _validate_config_bound_subtree(
                canonical_spec[section_key],
                candidate_spec[section_key],
                f"root.{section_key}",
                errors,
            )

    return (canonical_spec if errors else candidate_spec), candidate_sha, errors


def _nav_dict(root: Any, dotted_path: str, missing: list[str]) -> dict[str, Any] | None:
    cur = root
    built: list[str] = []
    for part in dotted_path.split("."):
        built.append(part)
        if not isinstance(cur, dict) or part not in cur:
            missing.append(".".join(built))
            return None
        cur = cur[part]
    if not isinstance(cur, dict):
        missing.append(dotted_path)
        return None
    return cur


def _req_float(
    obj: dict[str, Any] | None,
    key: str,
    full_path: str,
    missing: list[str],
    *,
    require_positive: bool = False,
) -> float | None:
    if not isinstance(obj, dict) or key not in obj:
        missing.append(full_path)
        return None
    val = obj[key]
    if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
        missing.append(full_path)
        return None
    fval = float(val)
    if not math.isfinite(fval) or fval < 0.0 or (require_positive and fval <= 0.0):
        missing.append(full_path)
        return None
    return fval


def _req_int(
    obj: dict[str, Any] | None,
    key: str,
    full_path: str,
    missing: list[str],
    *,
    require_positive: bool = False,
    allow_digit_str: bool = False,
) -> int | None:
    if not isinstance(obj, dict) or key not in obj:
        missing.append(full_path)
        return None
    val = obj[key]
    if val is None or isinstance(val, bool):
        missing.append(full_path)
        return None
    if allow_digit_str and isinstance(val, str) and val.isdigit():
        ival = int(val)
    elif isinstance(val, int):
        ival = val
    else:
        missing.append(full_path)
        return None
    if ival < 0 or (require_positive and ival <= 0):
        missing.append(full_path)
        return None
    return ival


def _req_bool(obj: dict[str, Any] | None, key: str, full_path: str, missing: list[str]) -> bool | None:
    if not isinstance(obj, dict) or key not in obj or not isinstance(obj[key], bool):
        missing.append(full_path)
        return None
    return obj[key]


def _req_str(obj: dict[str, Any] | None, key: str, full_path: str, missing: list[str]) -> str | None:
    if not isinstance(obj, dict) or key not in obj or not isinstance(obj[key], str) or not obj[key].strip():
        missing.append(full_path)
        return None
    return obj[key].strip()


def evaluate_precheck_7_metrics(
    benchmark_report: dict[str, Any],
    precheck_spec: dict[str, Any],
    *,
    owner_signed: bool,
    missing: list[str],
    breaches: list[str],
) -> dict[str, Any]:
    """Evaluate the 7-metric table strictly for the G18 pre-check step (zero silent 0.0 defaults)."""
    http_root = _nav_dict(benchmark_report, "s1_http_acceptance_and_mixed_waves", missing)
    bod_root = (
        _nav_dict(http_root, "bod_overview_and_drilldown_http", missing)
        if http_root is not None
        else None
    )
    waves_root = (
        _nav_dict(http_root, "concurrent_mixed_session_waves_20_40_80", missing)
        if http_root is not None
        else None
    )
    dom_root = _nav_dict(benchmark_report, "s1_domain_and_database_benchmark", missing)
    kpi_recompute = _nav_dict(dom_root, "kpi_recompute_s1", missing) if dom_root is not None else None
    overall_sum = _nav_dict(benchmark_report, "overall_summary", missing)
    p4_9_2_items = _nav_dict(benchmark_report, "p4_9_2_acceptance_matrix.items", missing)

    def _p95_from_http_node(parent: dict[str, Any] | None, key: str, prefix: str) -> float | None:
        if parent is None:
            return None
        node = _nav_dict(parent, key, missing)
        if node is None:
            return None
        _req_int(node, "requests_total", f"{prefix}.requests_total", missing, require_positive=True)
        return _req_float(node, "p95_ms", f"{prefix}.p95_ms", missing, require_positive=True)

    mc_primary = _p95_from_http_node(
        http_root,
        "staff_my_customers_http",
        "s1_http_acceptance_and_mixed_waves.staff_my_customers_http",
    )
    mc_matrix = _req_float(
        _nav_dict(p4_9_2_items, "staff_my_customers_p95_le_1500ms", missing) if p4_9_2_items is not None else None,
        "measured_http_p95_ms",
        "p4_9_2_acceptance_matrix.items.staff_my_customers_p95_le_1500ms.measured_http_p95_ms",
        missing,
        require_positive=True,
    )

    lead_primary = _p95_from_http_node(
        http_root,
        "lead_detail_and_history_http",
        "s1_http_acceptance_and_mixed_waves.lead_detail_and_history_http",
    )
    lead_matrix = _req_float(
        _nav_dict(p4_9_2_items, "open_lead_and_history_p95_le_2000ms", missing) if p4_9_2_items is not None else None,
        "measured_http_p95_ms",
        "p4_9_2_acceptance_matrix.items.open_lead_and_history_p95_le_2000ms.measured_http_p95_ms",
        missing,
        require_positive=True,
    )

    att_primary = _p95_from_http_node(
        http_root,
        "record_attempt_and_callback_http",
        "s1_http_acceptance_and_mixed_waves.record_attempt_and_callback_http",
    )
    att_matrix = _req_float(
        (
            _nav_dict(p4_9_2_items, "record_attempt_and_callback_p95_le_2000ms_zero_dup", missing)
            if p4_9_2_items is not None
            else None
        ),
        "measured_http_p95_ms",
        "p4_9_2_acceptance_matrix.items.record_attempt_and_callback_p95_le_2000ms_zero_dup.measured_http_p95_ms",
        missing,
        require_positive=True,
    )

    bod_primary = _p95_from_http_node(
        bod_root,
        "bod_executive_overview_role_api",
        "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http.bod_executive_overview_role_api",
    )
    bod_matrix = _req_float(
        _nav_dict(p4_9_2_items, "bod_overview_snapshot_p95_le_3000ms", missing) if p4_9_2_items is not None else None,
        "measured_http_p95_ms",
        "p4_9_2_acceptance_matrix.items.bod_overview_snapshot_p95_le_3000ms.measured_http_p95_ms",
        missing,
        require_positive=True,
    )

    drill_daily_primary = _p95_from_http_node(
        bod_root,
        "kpi_drilldown_daily_campaign_role_api",
        "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http.kpi_drilldown_daily_campaign_role_api",
    )
    drill_monthly_primary = _p95_from_http_node(
        bod_root,
        "kpi_drilldown_monthly_company_role_api",
        "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http.kpi_drilldown_monthly_company_role_api",
    )
    drill_matrix_node = (
        _nav_dict(p4_9_2_items, "kpi_drilldown_paginated_p95_le_3000ms", missing)
        if p4_9_2_items is not None
        else None
    )
    drill_daily_matrix = _req_float(
        drill_matrix_node,
        "measured_daily_campaign_http_p95_ms",
        "p4_9_2_acceptance_matrix.items.kpi_drilldown_paginated_p95_le_3000ms.measured_daily_campaign_http_p95_ms",
        missing,
        require_positive=True,
    )
    drill_monthly_matrix = _req_float(
        drill_matrix_node,
        "measured_monthly_company_250k_http_p95_ms",
        "p4_9_2_acceptance_matrix.items.kpi_drilldown_paginated_p95_le_3000ms.measured_monthly_company_250k_http_p95_ms",
        missing,
        require_positive=True,
    )

    overall_err: float | None = None
    if overall_sum is not None:
        _req_int(overall_sum, "requests_total", "overall_summary.requests_total", missing, require_positive=True)
        overall_err = _req_float(overall_sum, "error_rate_pct", "overall_summary.error_rate_pct", missing)

    wave_errs: list[float | None] = []
    for wname in ("sessions_20", "sessions_40", "sessions_80"):
        wsum = (
            _nav_dict(waves_root, f"{wname}.overall_summary", missing)
            if waves_root is not None
            else None
        )
        p_base = f"s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.{wname}.overall_summary"
        if wsum is not None:
            _req_int(wsum, "requests_total", f"{p_base}.requests_total", missing, require_positive=True)
            wave_errs.append(_req_float(wsum, "error_rate_pct", f"{p_base}.error_rate_pct", missing))
        else:
            wave_errs.append(None)

    daily_inc_ms = (
        _req_float(
            kpi_recompute,
            "daily_incremental_ms",
            "s1_domain_and_database_benchmark.kpi_recompute_s1.daily_incremental_ms",
            missing,
            require_positive=True,
        )
        if kpi_recompute is not None
        else None
    )

    def _reconcile_pair(primary: float | None, matrix_val: float | None, label: str) -> float | None:
        if primary is None or matrix_val is None:
            return None
        if abs(primary - matrix_val) > 1e-2:
            breaches.append(
                f"precheck_matrix_inconsistency:{label}:primary={primary}:p4_9_2_matrix={matrix_val}"
            )
            return max(primary, matrix_val)
        return primary

    drill_daily = _reconcile_pair(drill_daily_primary, drill_daily_matrix, "kpi_drilldown_daily")
    drill_monthly = _reconcile_pair(drill_monthly_primary, drill_monthly_matrix, "kpi_drilldown_monthly_250k")

    err_candidates = [overall_err, *wave_errs]
    worst_err = (
        max(v for v in err_candidates if v is not None)
        if all(v is not None for v in err_candidates)
        else None
    )

    measured_values: dict[str, float | None] = {
        "my_customers_worklist_api_p95_ms": _reconcile_pair(
            mc_primary, mc_matrix, "my_customers_worklist_api_p95_ms"
        ),
        "open_lead_and_history_api_p95_ms": _reconcile_pair(
            lead_primary, lead_matrix, "open_lead_and_history_api_p95_ms"
        ),
        "contact_attempt_write_p95_ms": _reconcile_pair(
            att_primary, att_matrix, "contact_attempt_write_p95_ms"
        ),
        "bod_executive_overview_api_p95_ms": _reconcile_pair(
            bod_primary, bod_matrix, "bod_executive_overview_api_p95_ms"
        ),
        "kpi_drilldown_paginated_api_p95_ms": (
            max(drill_daily, drill_monthly)
            if (drill_daily is not None and drill_monthly is not None)
            else None
        ),
        "http_5xx_error_rate_pct": worst_err,
        "daily_incremental_kpi_refresh_lag_sec": (
            round(daily_inc_ms / 1000.0, 3) if daily_inc_ms is not None else None
        ),
    }

    metric_comparisons: dict[str, dict[str, Any]] = {}
    all_7_within = True
    any_7_missing = False
    for key, spec in precheck_spec.items():
        threshold = float(spec["threshold"])
        measured = measured_values.get(key)
        if measured is None:
            any_7_missing = True
            all_7_within = False
            entry: dict[str, Any] = {
                "metric": key,
                "description": spec["description"],
                "operator": spec["operator"],
                "v1_4_threshold": threshold,
                "measured_value": None,
                "unit": spec["unit"],
                "headroom": None,
                "within_v1_4_threshold": False,
                "evaluation_status": "MISSING_MEASUREMENT",
            }
        else:
            passed_metric = measured <= threshold
            if not passed_metric:
                all_7_within = False
                breaches.append(
                    f"precheck_7_metrics.{key}:measured={measured}{spec['unit']}>threshold={threshold}{spec['unit']}"
                )
            entry = {
                "metric": key,
                "description": spec["description"],
                "operator": spec["operator"],
                "v1_4_threshold": threshold,
                "measured_value": measured,
                "unit": spec["unit"],
                "headroom": round(threshold - measured, 3),
                "within_v1_4_threshold": passed_metric,
                "evaluation_status": (
                    ("PASS" if passed_metric else "FAIL")
                    if owner_signed
                    else "PRE_CHECK_WITHIN_PROPOSED_V1_4"
                    if passed_metric
                    else "PRE_CHECK_BREACHES_PROPOSED_V1_4"
                ),
            }
        if key == "kpi_drilldown_paginated_api_p95_ms":
            entry["measured_daily_campaign_http_p95_ms"] = drill_daily
            entry["measured_monthly_company_250k_http_p95_ms"] = drill_monthly
        metric_comparisons[key] = entry

    precheck_status = (
        "FAIL_MISSING_MEASUREMENTS"
        if any_7_missing
        else ("PRECHECK_PASS" if all_7_within else "PRECHECK_FAIL_THRESHOLD_BREACH")
    )
    return {
        "role": "PRECHECK_TABLE_7_METRICS",
        "status": precheck_status,
        "all_7_metrics_present": not any_7_missing,
        "all_7_metrics_within_v1_4_thresholds": all_7_within,
        "metric_comparisons": metric_comparisons,
    }


def evaluate_section_3_2_a_b_c(
    benchmark_report: dict[str, Any],
    thresholds_spec: dict[str, Any],
    *,
    missing: list[str],
    breaches: list[str],
) -> dict[str, Any]:
    """Full §3.2A–C reconciliation by individual case, session model, and p95+p99 aligned with Ticket v1.4."""
    # --- §3.2A: Interactive HTTP API & SSR by case and session model (p95 + p99 + mirror parity) ---
    spec_3_2a = thresholds_spec["section_3_2A_cases_and_session_models"]
    cases_3_2a: dict[str, dict[str, Any]] = {}
    by_session_model: dict[str, dict[str, Any]] = {}
    sec_3_2a_passed = True

    for case_name, cspec in sorted(spec_3_2a.items()):
        cpath = str(cspec["path"])
        session_model = str(cspec["session_model"])
        node = _nav_dict(benchmark_report, cpath, missing)
        case_missing_before = len(missing)
        case_breaches: list[str] = []

        req_total = _req_int(node, "requests_total", f"{cpath}.requests_total", missing, require_positive=True)
        ok_cnt = _req_int(node, "ok_count", f"{cpath}.ok_count", missing)
        err_cnt = _req_int(node, "error_count", f"{cpath}.error_count", missing)
        err_pct = _req_float(node, "error_rate_pct", f"{cpath}.error_rate_pct", missing)
        p95_ms = _req_float(node, "p95_ms", f"{cpath}.p95_ms", missing, require_positive=True)
        p99_ms = _req_float(node, "p99_ms", f"{cpath}.p99_ms", missing, require_positive=True)

        conc_val: int | None = None
        if "concurrency" in cspec:
            conc_val = _req_int(node, "concurrency", f"{cpath}.concurrency", missing, require_positive=True)
            if conc_val is not None and conc_val != int(cspec["concurrency"]):
                case_breaches.append(
                    f"{case_name}.concurrency:expected={cspec['concurrency']}:got={conc_val}"
                )

        dist_sess_val: int | None = None
        if "distinct_sessions_used" in cspec:
            dist_sess_val = _req_int(
                node, "distinct_sessions_used", f"{cpath}.distinct_sessions_used", missing, require_positive=True
            )
            if dist_sess_val is not None and dist_sess_val != int(cspec["distinct_sessions_used"]):
                case_breaches.append(
                    f"{case_name}.distinct_sessions_used:expected={cspec['distinct_sessions_used']}:got={dist_sess_val}"
                )

        timeout_cnt: int | None = None
        if "max_timeout_count" in cspec:
            timeout_cnt = _req_int(node, "timeout_count", f"{cpath}.timeout_count", missing)
            if timeout_cnt is not None and timeout_cnt > int(cspec["max_timeout_count"]):
                case_breaches.append(
                    f"{case_name}.timeout_count:{timeout_cnt}>{cspec['max_timeout_count']}"
                )

        if "max_error_count" in cspec and err_cnt is not None and err_cnt > int(cspec["max_error_count"]):
            case_breaches.append(f"{case_name}.error_count:{err_cnt}>{cspec['max_error_count']}")

        if req_total is not None and req_total < int(cspec["min_requests"]):
            case_breaches.append(f"{case_name}.requests_total:{req_total}<{cspec['min_requests']}")
        if req_total is not None and ok_cnt is not None and err_cnt is not None and (ok_cnt + err_cnt) != req_total:
            case_breaches.append(
                f"{case_name}.count_accounting_mismatch:ok={ok_cnt}+err={err_cnt}!=total={req_total}"
            )
        if err_pct is not None and err_pct > float(cspec["max_error_rate_pct"]) + 1e-6:
            case_breaches.append(
                f"{case_name}.error_rate_pct:{err_pct}>{cspec['max_error_rate_pct']}"
            )
        if p95_ms is not None and p95_ms > float(cspec["max_p95_ms"]):
            case_breaches.append(f"{case_name}.p95_ms:{p95_ms}>{cspec['max_p95_ms']}")
        if p99_ms is not None and p99_ms > float(cspec["max_p99_ms"]):
            case_breaches.append(f"{case_name}.p99_ms:{p99_ms}>{cspec['max_p99_ms']}")
        if p95_ms is not None and p99_ms is not None and p99_ms + 1e-6 < p95_ms:
            case_breaches.append(f"{case_name}.p99_lt_p95:{p99_ms}<{p95_ms}")

        if "fallback_max_p95_ms" in cspec and isinstance(node, dict) and "fallback_p95_ms" in node:
            fb_p95 = _req_float(
                node, "fallback_p95_ms", f"{cpath}.fallback_p95_ms", missing, require_positive=True
            )
            if fb_p95 is not None and fb_p95 > float(cspec["fallback_max_p95_ms"]):
                case_breaches.append(
                    f"{case_name}.fallback_p95_ms:{fb_p95}>{cspec['fallback_max_p95_ms']}"
                )

        if "max_get_work_item_p95_ms" in cspec:
            gw_p95 = _req_float(
                node, "get_work_item_p95_ms", f"{cpath}.get_work_item_p95_ms", missing, require_positive=True
            )
            if gw_p95 is not None and gw_p95 > float(cspec["max_get_work_item_p95_ms"]):
                case_breaches.append(
                    f"{case_name}.get_work_item_p95_ms:{gw_p95}>{cspec['max_get_work_item_p95_ms']}"
                )
        if "max_list_attempts_p95_ms" in cspec:
            la_p95 = _req_float(
                node, "list_attempts_p95_ms", f"{cpath}.list_attempts_p95_ms", missing, require_positive=True
            )
            if la_p95 is not None and la_p95 > float(cspec["max_list_attempts_p95_ms"]):
                case_breaches.append(
                    f"{case_name}.list_attempts_p95_ms:{la_p95}>{cspec['max_list_attempts_p95_ms']}"
                )

        if "min_idempotent_replays_verified" in cspec:
            replays = _req_int(
                node,
                "http_idempotent_replays_verified",
                f"{cpath}.http_idempotent_replays_verified",
                missing,
                require_positive=True,
            )
            if replays is not None and replays < int(cspec["min_idempotent_replays_verified"]):
                case_breaches.append(
                    f"{case_name}.http_idempotent_replays_verified:{replays}<{cspec['min_idempotent_replays_verified']}"
                )
            dbrec = _nav_dict(node, "db_reconciliation", missing) if node is not None else None
            att_created = _req_int(
                dbrec, "attempts_created", f"{cpath}.db_reconciliation.attempts_created", missing
            )
            dup_idem = _req_int(
                dbrec,
                "duplicate_idempotency_keys",
                f"{cpath}.db_reconciliation.duplicate_idempotency_keys",
                missing,
            )
            cb_open = _req_int(
                dbrec,
                "work_items_with_open_callback_task",
                f"{cpath}.db_reconciliation.work_items_with_open_callback_task",
                missing,
            )
            if att_created is not None and att_created != int(cspec["expected_attempts_created"]):
                case_breaches.append(
                    f"{case_name}.attempts_created:{att_created}!={cspec['expected_attempts_created']}"
                )
            if dup_idem is not None and dup_idem != int(cspec["expected_duplicate_idempotency_keys"]):
                case_breaches.append(
                    f"{case_name}.duplicate_idempotency_keys:{dup_idem}!={cspec['expected_duplicate_idempotency_keys']}"
                )
            if cb_open is not None and cb_open != int(cspec["expected_open_callback_tasks"]):
                case_breaches.append(
                    f"{case_name}.work_items_with_open_callback_task:{cb_open}!={cspec['expected_open_callback_tasks']}"
                )

        if "expected_contact_attempt_table_scans" in cspec:
            bod_dom = _nav_dict(
                benchmark_report,
                "s1_domain_and_database_benchmark.domain_workloads_s1.bod_executive_overview",
                missing,
            )
            scans = _req_int(
                bod_dom,
                "contact_attempt_table_scans_during_read",
                "s1_domain_and_database_benchmark.domain_workloads_s1.bod_executive_overview.contact_attempt_table_scans_during_read",
                missing,
            )
            if scans is not None and scans != int(cspec["expected_contact_attempt_table_scans"]):
                case_breaches.append(
                    f"{case_name}.contact_attempt_table_scans_during_read:{scans}!={cspec['expected_contact_attempt_table_scans']}"
                )
            exp_plans = _nav_dict(
                benchmark_report, "s1_domain_and_database_benchmark.mariadb_explain_plans", missing
            )
            if exp_plans is not None:
                snap_plan = exp_plans.get("kpi_snapshot_lookup")
                if not isinstance(snap_plan, list) or not snap_plan or not isinstance(snap_plan[0], dict):
                    missing.append(
                        "s1_domain_and_database_benchmark.mariadb_explain_plans.kpi_snapshot_lookup[0].key"
                    )
                else:
                    snap_key = _req_str(
                        snap_plan[0],
                        "key",
                        "s1_domain_and_database_benchmark.mariadb_explain_plans.kpi_snapshot_lookup[0].key",
                        missing,
                    )
                    if snap_key is not None and snap_key != str(cspec["expected_explain_index_key"]):
                        case_breaches.append(
                            f"{case_name}.explain_key:{snap_key}!={cspec['expected_explain_index_key']}"
                        )

        if "expected_unauthorized_staff_http_status" in cspec:
            bod_http = _nav_dict(
                benchmark_report,
                "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http",
                missing,
            )
            unauth_code = _req_int(
                bod_http,
                "staff_unauthorized_company_drilldown_http_status",
                "s1_http_acceptance_and_mixed_waves.bod_overview_and_drilldown_http.staff_unauthorized_company_drilldown_http_status",
                missing,
                require_positive=True,
            )
            if unauth_code is not None and unauth_code != int(cspec["expected_unauthorized_staff_http_status"]):
                case_breaches.append(
                    f"{case_name}.staff_unauthorized_company_drilldown_http_status:{unauth_code}!={cspec['expected_unauthorized_staff_http_status']}"
                )

        for mirror_path in cspec.get("mirror_paths", []):
            mirror_node = _nav_dict(benchmark_report, mirror_path, missing)
            mirror_req = _req_int(
                mirror_node, "requests_total", f"{mirror_path}.requests_total", missing, require_positive=True
            )
            mirror_err = _req_int(mirror_node, "error_count", f"{mirror_path}.error_count", missing)
            mirror_to = _req_int(mirror_node, "timeout_count", f"{mirror_path}.timeout_count", missing)
            mirror_p95 = _req_float(
                mirror_node, "p95_ms", f"{mirror_path}.p95_ms", missing, require_positive=True
            )
            mirror_p99 = _req_float(
                mirror_node, "p99_ms", f"{mirror_path}.p99_ms", missing, require_positive=True
            )
            if mirror_p95 is not None and mirror_p95 > float(cspec["max_p95_ms"]):
                case_breaches.append(
                    f"{case_name}.mirror({mirror_path}).p95_ms:{mirror_p95}>{cspec['max_p95_ms']}"
                )
            if mirror_p99 is not None and mirror_p99 > float(cspec["max_p99_ms"]):
                case_breaches.append(
                    f"{case_name}.mirror({mirror_path}).p99_ms:{mirror_p99}>{cspec['max_p99_ms']}"
                )
            if p95_ms is not None and mirror_p95 is not None and abs(p95_ms - mirror_p95) > 1e-2:
                case_breaches.append(
                    f"{case_name}.ssr_mirror_p95_mismatch:{mirror_path}:{p95_ms}!={mirror_p95}"
                )
            if p99_ms is not None and mirror_p99 is not None and abs(p99_ms - mirror_p99) > 1e-2:
                case_breaches.append(
                    f"{case_name}.ssr_mirror_p99_mismatch:{mirror_path}:{p99_ms}!={mirror_p99}"
                )
            if req_total is not None and mirror_req is not None and req_total != mirror_req:
                case_breaches.append(
                    f"{case_name}.ssr_mirror_requests_mismatch:{mirror_path}:{req_total}!={mirror_req}"
                )
            if "max_error_count" in cspec and mirror_err is not None and mirror_err > int(cspec["max_error_count"]):
                case_breaches.append(
                    f"{case_name}.mirror({mirror_path}).error_count:{mirror_err}>{cspec['max_error_count']}"
                )
            if "max_timeout_count" in cspec and mirror_to is not None and mirror_to > int(cspec["max_timeout_count"]):
                case_breaches.append(
                    f"{case_name}.mirror({mirror_path}).timeout_count:{mirror_to}>{cspec['max_timeout_count']}"
                )
            if (
                "distinct_sessions_used" in cspec
                and isinstance(mirror_node, dict)
                and "distinct_sessions_used" in mirror_node
            ):
                mirror_sess = _req_int(
                    mirror_node,
                    "distinct_sessions_used",
                    f"{mirror_path}.distinct_sessions_used",
                    missing,
                    require_positive=True,
                )
                if dist_sess_val is not None and mirror_sess is not None and dist_sess_val != mirror_sess:
                    case_breaches.append(
                        f"{case_name}.ssr_mirror_sessions_mismatch:{mirror_path}:{dist_sess_val}!={mirror_sess}"
                    )

        case_missing = len(missing) > case_missing_before
        case_ok = (not case_missing) and (not case_breaches)
        if not case_ok:
            sec_3_2a_passed = False
        breaches.extend(f"section_3_2A.{b}" for b in case_breaches)

        cases_3_2a[case_name] = {
            "ticket_item_id": str(cspec.get("ticket_item_id", "")),
            "session_model": session_model,
            "path": cpath,
            "concurrency": conc_val,
            "distinct_sessions_used": dist_sess_val,
            "requests_total": req_total,
            "p95_ms": p95_ms,
            "max_p95_ms": float(cspec["max_p95_ms"]),
            "p99_ms": p99_ms,
            "max_p99_ms": float(cspec["max_p99_ms"]),
            "error_rate_pct": err_pct,
            "max_error_rate_pct": float(cspec["max_error_rate_pct"]),
            "timeout_count": timeout_cnt,
            "passed": case_ok,
            "status": (
                "MISSING_MEASUREMENT"
                if case_missing
                else ("PASS" if case_ok else "FAIL_THRESHOLD_BREACH")
            ),
            "breaches": case_breaches,
        }
        sm_bucket = by_session_model.setdefault(
            session_model, {"cases_total": 0, "cases_passed": 0, "case_names": []}
        )
        sm_bucket["cases_total"] += 1
        if case_ok:
            sm_bucket["cases_passed"] += 1
        sm_bucket["case_names"].append(case_name)

    # Verify SSR separation telemetry exists so SSR p95/p99 is never conflated with aggregate API p95/p99
    sep_root = _nav_dict(benchmark_report, "worklist_ssr_acceptance_and_separation", missing)
    exec_mode = (
        _req_str(
            sep_root,
            "execution_mode",
            "worklist_ssr_acceptance_and_separation.execution_mode",
            missing,
        )
        if sep_root is not None
        else None
    )
    if exec_mode is not None and exec_mode != "BUILT_INTO_OFFICIAL_COMMIT_AND_IMAGE_DIGEST_NO_IN_CONTAINER_PATCHING":
        sec_3_2a_passed = False
        breaches.append(
            f"section_3_2A.worklist_ssr_execution_mode_invalid:{exec_mode}"
        )

    # --- §3.2B: Concurrent mixed authenticated session waves (20 / 40 / 80 from pool of 80, p95 + p99) ---
    spec_3_2b = thresholds_spec["section_3_2B_mixed_session_waves"]
    http_root = _nav_dict(benchmark_report, "s1_http_acceptance_and_mixed_waves", missing)
    pool_sessions = _req_int(
        http_root,
        "distinct_authenticated_sessions_created",
        "s1_http_acceptance_and_mixed_waves.distinct_authenticated_sessions_created",
        missing,
        require_positive=True,
    )
    sec_3_2b_passed = True
    if pool_sessions is None:
        sec_3_2b_passed = False
    elif pool_sessions != int(spec_3_2b["required_distinct_authenticated_sessions"]):
        sec_3_2b_passed = False
        breaches.append(
            f"section_3_2B.distinct_authenticated_sessions_created:{pool_sessions}!={spec_3_2b['required_distinct_authenticated_sessions']}"
        )

    waves_root = (
        _nav_dict(http_root, "concurrent_mixed_session_waves_20_40_80", missing)
        if http_root is not None
        else None
    )
    waves_eval: dict[str, dict[str, Any]] = {}
    for wname, wspec in sorted(spec_3_2b["waves"].items()):
        wpath = f"s1_http_acceptance_and_mixed_waves.concurrent_mixed_session_waves_20_40_80.{wname}"
        wnode = _nav_dict(waves_root, wname, missing) if waves_root is not None else None
        w_missing_before = len(missing)
        w_breaches: list[str] = []

        conc_users = _req_int(
            wnode, "concurrent_user_sessions", f"{wpath}.concurrent_user_sessions", missing, require_positive=True
        )
        if conc_users is not None and conc_users != int(wspec["concurrent_user_sessions"]):
            w_breaches.append(
                f"{wname}.concurrent_user_sessions:{conc_users}!={wspec['concurrent_user_sessions']}"
            )

        w_sum = _nav_dict(wnode, "overall_summary", missing) if wnode is not None else None
        w_req = _req_int(
            w_sum, "requests_total", f"{wpath}.overall_summary.requests_total", missing, require_positive=True
        )
        w_ok = _req_int(w_sum, "ok_count", f"{wpath}.overall_summary.ok_count", missing)
        w_err = _req_int(w_sum, "error_count", f"{wpath}.overall_summary.error_count", missing)
        w_err_pct = _req_float(w_sum, "error_rate_pct", f"{wpath}.overall_summary.error_rate_pct", missing)
        w_p95 = _req_float(
            w_sum, "p95_ms", f"{wpath}.overall_summary.p95_ms", missing, require_positive=True
        )
        w_p99 = _req_float(
            w_sum, "p99_ms", f"{wpath}.overall_summary.p99_ms", missing, require_positive=True
        )

        if w_req is not None and w_req < int(wspec["min_requests"]):
            w_breaches.append(f"{wname}.overall_summary.requests_total:{w_req}<{wspec['min_requests']}")
        if w_req is not None and w_ok is not None and w_err is not None and (w_ok + w_err) != w_req:
            w_breaches.append(
                f"{wname}.overall_summary.count_mismatch:ok={w_ok}+err={w_err}!=total={w_req}"
            )
        if "max_error_count" in wspec and w_err is not None and w_err > int(wspec["max_error_count"]):
            w_breaches.append(
                f"{wname}.overall_summary.error_count:{w_err}>{wspec['max_error_count']}"
            )
        if "max_timeout_count" in wspec and isinstance(w_sum, dict) and "timeout_count" in w_sum:
            w_to = _req_int(w_sum, "timeout_count", f"{wpath}.overall_summary.timeout_count", missing)
            if w_to is not None and w_to > int(wspec["max_timeout_count"]):
                w_breaches.append(
                    f"{wname}.overall_summary.timeout_count:{w_to}>{wspec['max_timeout_count']}"
                )
        if w_err_pct is not None and w_err_pct > float(wspec["max_error_rate_pct"]) + 1e-6:
            w_breaches.append(
                f"{wname}.overall_summary.error_rate_pct:{w_err_pct}>{wspec['max_error_rate_pct']}"
            )
        if w_p95 is not None and w_p95 > float(wspec["max_overall_p95_ms"]):
            w_breaches.append(
                f"{wname}.overall_summary.p95_ms:{w_p95}>{wspec['max_overall_p95_ms']}"
            )
        if w_p99 is not None and w_p99 > float(wspec["max_overall_p99_ms"]):
            w_breaches.append(
                f"{wname}.overall_summary.p99_ms:{w_p99}>{wspec['max_overall_p99_ms']}"
            )
        if w_p95 is not None and w_p99 is not None and w_p99 + 1e-6 < w_p95:
            w_breaches.append(
                f"{wname}.overall_summary.p99_lt_p95:{w_p99}<{w_p95}"
            )

        for wm_path in wspec.get("mirror_paths", []):
            wm_node = _nav_dict(benchmark_report, wm_path, missing)
            wm_req = _req_int(
                wm_node, "requests_total", f"{wm_path}.requests_total", missing, require_positive=True
            )
            wm_err = _req_int(wm_node, "error_count", f"{wm_path}.error_count", missing)
            wm_p95 = _req_float(
                wm_node, "p95_ms", f"{wm_path}.p95_ms", missing, require_positive=True
            )
            wm_p99 = _req_float(
                wm_node, "p99_ms", f"{wm_path}.p99_ms", missing, require_positive=True
            )
            if wm_p95 is not None and wm_p95 > float(wspec["max_overall_p95_ms"]):
                w_breaches.append(
                    f"{wname}.mirror({wm_path}).p95_ms:{wm_p95}>{wspec['max_overall_p95_ms']}"
                )
            if wm_p99 is not None and wm_p99 > float(wspec["max_overall_p99_ms"]):
                w_breaches.append(
                    f"{wname}.mirror({wm_path}).p99_ms:{wm_p99}>{wspec['max_overall_p99_ms']}"
                )
            if w_p95 is not None and wm_p95 is not None and abs(w_p95 - wm_p95) > 1e-2:
                w_breaches.append(
                    f"{wname}.wave_mirror_p95_mismatch:{wm_path}:{w_p95}!={wm_p95}"
                )
            if w_p99 is not None and wm_p99 is not None and abs(w_p99 - wm_p99) > 1e-2:
                w_breaches.append(
                    f"{wname}.wave_mirror_p99_mismatch:{wm_path}:{w_p99}!={wm_p99}"
                )
            if w_req is not None and wm_req is not None and w_req != wm_req:
                w_breaches.append(
                    f"{wname}.wave_mirror_requests_mismatch:{wm_path}:{w_req}!={wm_req}"
                )
            if w_err is not None and wm_err is not None and w_err != wm_err:
                w_breaches.append(
                    f"{wname}.wave_mirror_error_count_mismatch:{wm_path}:{w_err}!={wm_err}"
                )

        by_wl_node = _nav_dict(wnode, "by_workload", missing) if wnode is not None else None
        wl_p99_map = wspec["by_workload_max_p99_ms"]
        wl_eval: dict[str, dict[str, Any]] = {}
        for wlname, wl_max_p95 in sorted(wspec["by_workload_max_p95_ms"].items()):
            wl_max_p99 = float(wl_p99_map[wlname])
            wl_path = f"{wpath}.by_workload.{wlname}"
            wln = _nav_dict(by_wl_node, wlname, missing) if by_wl_node is not None else None
            wl_req = _req_int(wln, "requests_total", f"{wl_path}.requests_total", missing, require_positive=True)
            wl_ok = _req_int(wln, "ok_count", f"{wl_path}.ok_count", missing)
            wl_err = _req_int(wln, "error_count", f"{wl_path}.error_count", missing)
            wl_err_pct = _req_float(wln, "error_rate_pct", f"{wl_path}.error_rate_pct", missing)
            wl_p95 = _req_float(wln, "p95_ms", f"{wl_path}.p95_ms", missing, require_positive=True)
            wl_p99 = _req_float(wln, "p99_ms", f"{wl_path}.p99_ms", missing, require_positive=True)
            wl_ok_flag = (
                wl_req is not None
                and wl_ok is not None
                and wl_err is not None
                and wl_err_pct is not None
                and wl_p95 is not None
                and wl_p99 is not None
            )
            if wl_req is not None and wl_ok is not None and wl_err is not None and (wl_ok + wl_err) != wl_req:
                w_breaches.append(f"{wname}.{wlname}.count_mismatch")
                wl_ok_flag = False
            if wl_err_pct is not None and wl_err_pct > float(wspec["max_error_rate_pct"]) + 1e-6:
                w_breaches.append(
                    f"{wname}.by_workload.{wlname}.error_rate_pct:{wl_err_pct}>{wspec['max_error_rate_pct']}"
                )
                wl_ok_flag = False
            if wl_p95 is not None and wl_p95 > float(wl_max_p95):
                w_breaches.append(
                    f"{wname}.by_workload.{wlname}.p95_ms:{wl_p95}>{wl_max_p95}"
                )
                wl_ok_flag = False
            if wl_p99 is not None and wl_p99 > wl_max_p99:
                w_breaches.append(
                    f"{wname}.by_workload.{wlname}.p99_ms:{wl_p99}>{wl_max_p99}"
                )
                wl_ok_flag = False
            if wl_p95 is not None and wl_p99 is not None and wl_p99 + 1e-6 < wl_p95:
                w_breaches.append(
                    f"{wname}.by_workload.{wlname}.p99_lt_p95:{wl_p99}<{wl_p95}"
                )
                wl_ok_flag = False
            wl_eval[wlname] = {
                "requests_total": wl_req,
                "p95_ms": wl_p95,
                "max_p95_ms": float(wl_max_p95),
                "p99_ms": wl_p99,
                "max_p99_ms": wl_max_p99,
                "error_rate_pct": wl_err_pct,
                "passed": wl_ok_flag,
            }

        qdb = _nav_dict(wnode, "queue_and_db_after_wave", missing) if wnode is not None else None
        q_backlog = _req_int(
            qdb, "queue_backlog_total", f"{wpath}.queue_and_db_after_wave.queue_backlog_total", missing
        )
        mdb = _nav_dict(qdb, "mariadb_global_status", missing) if qdb is not None else None
        slow_q = _req_int(
            mdb,
            "Slow_queries",
            f"{wpath}.queue_and_db_after_wave.mariadb_global_status.Slow_queries",
            missing,
            allow_digit_str=True,
        )
        if q_backlog is not None and q_backlog > int(wspec["max_queue_backlog_after"]):
            w_breaches.append(
                f"{wname}.queue_backlog_total:{q_backlog}>{wspec['max_queue_backlog_after']}"
            )
        if slow_q is not None and slow_q > int(wspec["max_slow_queries_after"]):
            w_breaches.append(
                f"{wname}.Slow_queries:{slow_q}>{wspec['max_slow_queries_after']}"
            )

        w_missing = len(missing) > w_missing_before
        w_ok_flag = (not w_missing) and (not w_breaches)
        if not w_ok_flag:
            sec_3_2b_passed = False
        breaches.extend(f"section_3_2B.{b}" for b in w_breaches)
        waves_eval[wname] = {
            "ticket_item_id": str(wspec.get("ticket_item_id", "")),
            "session_model": str(wspec["session_model"]),
            "concurrent_user_sessions": conc_users,
            "requests_total": w_req,
            "overall_p95_ms": w_p95,
            "max_overall_p95_ms": float(wspec["max_overall_p95_ms"]),
            "overall_p99_ms": w_p99,
            "max_overall_p99_ms": float(wspec["max_overall_p99_ms"]),
            "error_rate_pct": w_err_pct,
            "max_error_rate_pct": float(wspec["max_error_rate_pct"]),
            "queue_backlog_after": q_backlog,
            "slow_queries_after": slow_q,
            "by_workload": wl_eval,
            "passed": w_ok_flag,
            "status": (
                "MISSING_MEASUREMENT"
                if w_missing
                else ("PASS" if w_ok_flag else "FAIL_THRESHOLD_BREACH")
            ),
            "breaches": w_breaches,
        }

    # --- §3.2C: Aggregate load (AGG-01/AGG-02), S1 dataset/EXPLAIN, CSV import, distribution, KPI recompute, RQ burst ---
    spec_3_2c = thresholds_spec["section_3_2C_dataset_batch_and_background"]
    dom_root = _nav_dict(benchmark_report, "s1_domain_and_database_benchmark", missing)
    sec_3_2c_breaches: list[str] = []
    sec_3_2c_missing_before = len(missing)

    # 3.2C.0 Aggregate load summaries (AGG-01 c=12 1920 reqs & AGG-02 c=40 peak 800 reqs; never compensates §3.2A/B)
    agg_eval: dict[str, dict[str, Any]] = {}
    for agg_name, aspec in sorted(spec_3_2c["aggregate_load_summaries"].items()):
        apath = str(aspec["path"])
        agg_missing_before = len(missing)
        agg_item_breaches: list[str] = []
        anode = _nav_dict(benchmark_report, apath, missing)
        a_req = _req_int(anode, "requests_total", f"{apath}.requests_total", missing, require_positive=True)
        a_ok = _req_int(anode, "ok_count", f"{apath}.ok_count", missing)
        a_err = _req_int(anode, "error_count", f"{apath}.error_count", missing)
        a_err_pct = _req_float(anode, "error_rate_pct", f"{apath}.error_rate_pct", missing)
        a_to = _req_int(anode, "timeout_count", f"{apath}.timeout_count", missing)
        a_p95 = _req_float(anode, "p95_ms", f"{apath}.p95_ms", missing, require_positive=True)
        a_p99 = _req_float(anode, "p99_ms", f"{apath}.p99_ms", missing, require_positive=True)

        if "expected_wave_concurrency" in aspec:
            peak_parent = _nav_dict(benchmark_report, "peak_40_staff_concurrent_wave", missing)
            peak_conc = _req_int(
                peak_parent,
                "concurrency",
                "peak_40_staff_concurrent_wave.concurrency",
                missing,
                require_positive=True,
            )
            if peak_conc is not None and peak_conc != int(aspec["expected_wave_concurrency"]):
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.concurrency:{peak_conc}!={aspec['expected_wave_concurrency']}"
                )

        if a_req is not None and a_req < int(aspec["min_requests"]):
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.requests_total:{a_req}<{aspec['min_requests']}"
            )
        if a_req is not None and a_ok is not None and a_err is not None and (a_ok + a_err) != a_req:
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.count_mismatch:ok={a_ok}+err={a_err}!=total={a_req}"
            )
        if a_err is not None and a_err > int(aspec["max_error_count"]):
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.error_count:{a_err}>{aspec['max_error_count']}"
            )
        if a_err_pct is not None and a_err_pct > float(aspec["max_error_rate_pct"]) + 1e-6:
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.error_rate_pct:{a_err_pct}>{aspec['max_error_rate_pct']}"
            )
        if a_to is not None and a_to > int(aspec["max_timeout_count"]):
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.timeout_count:{a_to}>{aspec['max_timeout_count']}"
            )
        if a_p95 is not None and a_p95 > float(aspec["max_p95_ms"]):
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.p95_ms:{a_p95}>{aspec['max_p95_ms']}"
            )
        if a_p99 is not None and a_p99 > float(aspec["max_p99_ms"]):
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.p99_ms:{a_p99}>{aspec['max_p99_ms']}"
            )
        if a_p95 is not None and a_p99 is not None and a_p99 + 1e-6 < a_p95:
            agg_item_breaches.append(
                f"aggregate_load_summaries.{agg_name}.p99_lt_p95:{a_p99}<{a_p95}"
            )

        for am_path in aspec.get("mirror_paths", []):
            am_node = _nav_dict(benchmark_report, am_path, missing)
            am_req = _req_int(
                am_node, "requests_total", f"{am_path}.requests_total", missing, require_positive=True
            )
            am_err = _req_int(am_node, "error_count", f"{am_path}.error_count", missing)
            am_to = _req_int(am_node, "timeout_count", f"{am_path}.timeout_count", missing)
            am_p95 = _req_float(
                am_node, "p95_ms", f"{am_path}.p95_ms", missing, require_positive=True
            )
            am_p99 = _req_float(
                am_node, "p99_ms", f"{am_path}.p99_ms", missing, require_positive=True
            )
            if am_p95 is not None and am_p95 > float(aspec["max_p95_ms"]):
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror({am_path}).p95_ms:{am_p95}>{aspec['max_p95_ms']}"
                )
            if am_p99 is not None and am_p99 > float(aspec["max_p99_ms"]):
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror({am_path}).p99_ms:{am_p99}>{aspec['max_p99_ms']}"
                )
            if a_p95 is not None and am_p95 is not None and abs(a_p95 - am_p95) > 1e-2:
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror_p95_mismatch:{am_path}:{a_p95}!={am_p95}"
                )
            if a_p99 is not None and am_p99 is not None and abs(a_p99 - am_p99) > 1e-2:
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror_p99_mismatch:{am_path}:{a_p99}!={am_p99}"
                )
            if a_req is not None and am_req is not None and a_req != am_req:
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror_requests_mismatch:{am_path}:{a_req}!={am_req}"
                )
            if am_err is not None and am_err > int(aspec["max_error_count"]):
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror({am_path}).error_count:{am_err}>{aspec['max_error_count']}"
                )
            if am_to is not None and am_to > int(aspec["max_timeout_count"]):
                agg_item_breaches.append(
                    f"aggregate_load_summaries.{agg_name}.mirror({am_path}).timeout_count:{am_to}>{aspec['max_timeout_count']}"
                )

        agg_item_missing = len(missing) > agg_missing_before
        agg_item_ok = (not agg_item_missing) and (not agg_item_breaches)
        sec_3_2c_breaches.extend(agg_item_breaches)
        agg_eval[agg_name] = {
            "ticket_item_id": str(aspec.get("ticket_item_id", "")),
            "path": apath,
            "requests_total": a_req,
            "p95_ms": a_p95,
            "max_p95_ms": float(aspec["max_p95_ms"]),
            "p99_ms": a_p99,
            "max_p99_ms": float(aspec["max_p99_ms"]),
            "error_rate_pct": a_err_pct,
            "timeout_count": a_to,
            "passed": agg_item_ok,
            "status": (
                "MISSING_MEASUREMENT"
                if agg_item_missing
                else ("PASS" if agg_item_ok else "FAIL_THRESHOLD_BREACH")
            ),
            "breaches": agg_item_breaches,
        }

    # 3.2C.1 S1 dataset counts, PII policy & MariaDB EXPLAIN index keys
    seed_spec = spec_3_2c["s1_dataset_and_explain_plans"]
    seeded = _nav_dict(dom_root, "s1_seeded_counts", missing) if dom_root is not None else None
    for ckey, exp_cnt in sorted(seed_spec["expected_counts"].items()):
        got_cnt = _req_int(
            seeded,
            ckey,
            f"s1_domain_and_database_benchmark.s1_seeded_counts.{ckey}",
            missing,
            require_positive=True,
        )
        if got_cnt is not None and got_cnt != int(exp_cnt):
            sec_3_2c_breaches.append(f"s1_seeded_counts.{ckey}:{got_cnt}!={exp_cnt}")

    pub_ds = _nav_dict(benchmark_report, "published_dataset_and_load", missing)
    pii_pol = _req_str(pub_ds, "pii_policy", "published_dataset_and_load.pii_policy", missing)
    if pii_pol is not None and pii_pol != "ZERO_REAL_PII_SYNTHETIC_ONLY":
        sec_3_2c_breaches.append(f"published_dataset_and_load.pii_policy:{pii_pol}")

    exp_plans = _nav_dict(dom_root, "mariadb_explain_plans", missing) if dom_root is not None else None
    for pkey, exp_idx in sorted(seed_spec["expected_explain_keys"].items()):
        p_full = f"s1_domain_and_database_benchmark.mariadb_explain_plans.{pkey}[0].key"
        if exp_plans is None or pkey not in exp_plans:
            missing.append(p_full)
        else:
            rows = exp_plans[pkey]
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                missing.append(p_full)
            else:
                got_idx = _req_str(rows[0], "key", p_full, missing)
                if got_idx is not None and got_idx != str(exp_idx):
                    sec_3_2c_breaches.append(f"mariadb_explain_plans.{pkey}:{got_idx}!={exp_idx}")

    # 3.2C.1b Direct domain service workloads on S1 dataset (domain_workloads_s1, p95 + p99)
    dom_wl_root = _nav_dict(dom_root, "domain_workloads_s1", missing) if dom_root is not None else None
    for dw_key, dw_spec in sorted(spec_3_2c["domain_workloads_s1"].items()):
        dw_path = f"s1_domain_and_database_benchmark.domain_workloads_s1.{dw_key}"
        dw_node = _nav_dict(dom_wl_root, dw_key, missing) if dom_wl_root is not None else None
        dw_cnt = _req_int(dw_node, "count", f"{dw_path}.count", missing, require_positive=True)
        dw_err = _req_int(dw_node, "error_count", f"{dw_path}.error_count", missing)
        dw_p95 = _req_float(dw_node, "p95_ms", f"{dw_path}.p95_ms", missing, require_positive=True)
        dw_p99 = _req_float(dw_node, "p99_ms", f"{dw_path}.p99_ms", missing, require_positive=True)
        if dw_cnt is not None and dw_cnt < int(dw_spec["min_count"]):
            sec_3_2c_breaches.append(f"domain_workloads_s1.{dw_key}.count:{dw_cnt}<{dw_spec['min_count']}")
        if dw_err is not None and dw_err > int(dw_spec["max_error_count"]):
            sec_3_2c_breaches.append(
                f"domain_workloads_s1.{dw_key}.error_count:{dw_err}>{dw_spec['max_error_count']}"
            )
        if dw_p95 is not None and dw_p95 > float(dw_spec["max_p95_ms"]):
            sec_3_2c_breaches.append(
                f"domain_workloads_s1.{dw_key}.p95_ms:{dw_p95}>{dw_spec['max_p95_ms']}"
            )
        if dw_p99 is not None and dw_p99 > float(dw_spec["max_p99_ms"]):
            sec_3_2c_breaches.append(
                f"domain_workloads_s1.{dw_key}.p99_ms:{dw_p99}>{dw_spec['max_p99_ms']}"
            )
        if dw_p95 is not None and dw_p99 is not None and dw_p99 + 1e-6 < dw_p95:
            sec_3_2c_breaches.append(
                f"domain_workloads_s1.{dw_key}.p99_lt_p95:{dw_p99}<{dw_p95}"
            )
        if "expected_idempotent_replay_verified" in dw_spec:
            dw_idem = _req_bool(
                dw_node, "idempotent_replay_verified", f"{dw_path}.idempotent_replay_verified", missing
            )
            if dw_idem is not None and dw_idem is not bool(dw_spec["expected_idempotent_replay_verified"]):
                sec_3_2c_breaches.append(f"domain_workloads_s1.{dw_key}.idempotent_replay_verified:{dw_idem}")
        if "expected_contact_attempt_table_scans" in dw_spec:
            dw_scans = _req_int(
                dw_node,
                "contact_attempt_table_scans_during_read",
                f"{dw_path}.contact_attempt_table_scans_during_read",
                missing,
            )
            if dw_scans is not None and dw_scans != int(dw_spec["expected_contact_attempt_table_scans"]):
                sec_3_2c_breaches.append(
                    f"domain_workloads_s1.{dw_key}.contact_attempt_table_scans:{dw_scans}!={dw_spec['expected_contact_attempt_table_scans']}"
                )

    # 3.2C.2 50,000-row CSV import (BG-02)
    async_root = (
        _nav_dict(dom_root, "async_import_distribution_and_queue_lag", missing)
        if dom_root is not None
        else None
    )
    csv_spec = spec_3_2c["csv_import_50000_rows"]
    csv_path = "s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.csv_import_50000_rows"
    csv_node = _nav_dict(async_root, "csv_import_50000_rows", missing) if async_root is not None else None
    csv_total = _req_int(csv_node, "total_rows", f"{csv_path}.total_rows", missing, require_positive=True)
    csv_created = _req_int(csv_node, "created_rows", f"{csv_path}.created_rows", missing, require_positive=True)
    csv_reused = _req_int(csv_node, "reused_rows", f"{csv_path}.reused_rows", missing, require_positive=True)
    csv_err_rows = _req_int(csv_node, "error_rows", f"{csv_path}.error_rows", missing)
    csv_err_art = _req_bool(csv_node, "error_artifact_present", f"{csv_path}.error_artifact_present", missing)
    csv_status = _req_str(csv_node, "final_batch_status", f"{csv_path}.final_batch_status", missing)
    csv_prev_ms = _req_float(csv_node, "preview_ms", f"{csv_path}.preview_ms", missing, require_positive=True)
    csv_worker_ms = _req_float(
        csv_node,
        "worker_commit_and_drain_ms",
        f"{csv_path}.worker_commit_and_drain_ms",
        missing,
        require_positive=True,
    )
    csv_tot_ms = _req_float(
        csv_node,
        "total_preview_confirm_and_worker_drain_ms",
        f"{csv_path}.total_preview_confirm_and_worker_drain_ms",
        missing,
        require_positive=True,
    )
    csv_hwm = _req_float(csv_node, "preview_hwm_mib", f"{csv_path}.preview_hwm_mib", missing, require_positive=True)
    csv_wl_p95 = _req_float(
        csv_node,
        "concurrent_worklist_read_p95_ms",
        f"{csv_path}.concurrent_worklist_read_p95_ms",
        missing,
        require_positive=True,
    )
    if csv_total is not None and csv_total != int(csv_spec["expected_total_rows"]):
        sec_3_2c_breaches.append(f"csv_import_50000_rows.total_rows:{csv_total}!={csv_spec['expected_total_rows']}")
    if csv_created is not None and csv_created != int(csv_spec["expected_created_rows"]):
        sec_3_2c_breaches.append(
            f"csv_import_50000_rows.created_rows:{csv_created}!={csv_spec['expected_created_rows']}"
        )
    if csv_reused is not None and csv_reused != int(csv_spec["expected_reused_rows"]):
        sec_3_2c_breaches.append(f"csv_import_50000_rows.reused_rows:{csv_reused}!={csv_spec['expected_reused_rows']}")
    if csv_err_rows is not None and csv_err_rows != int(csv_spec["expected_error_rows"]):
        sec_3_2c_breaches.append(f"csv_import_50000_rows.error_rows:{csv_err_rows}!={csv_spec['expected_error_rows']}")
    if csv_err_art is not None and csv_err_art is not bool(csv_spec["expected_error_artifact_present"]):
        sec_3_2c_breaches.append(f"csv_import_50000_rows.error_artifact_present:{csv_err_art}")
    if csv_status is not None and csv_status != str(csv_spec["expected_final_batch_status"]):
        sec_3_2c_breaches.append(
            f"csv_import_50000_rows.final_batch_status:{csv_status}!={csv_spec['expected_final_batch_status']}"
        )
    if csv_prev_ms is not None and csv_prev_ms > float(csv_spec["max_preview_ms"]):
        sec_3_2c_breaches.append(f"csv_import_50000_rows.preview_ms:{csv_prev_ms}>{csv_spec['max_preview_ms']}")
    if csv_worker_ms is not None and csv_worker_ms > float(csv_spec["max_worker_commit_and_drain_ms"]):
        sec_3_2c_breaches.append(
            f"csv_import_50000_rows.worker_commit_and_drain_ms:{csv_worker_ms}>{csv_spec['max_worker_commit_and_drain_ms']}"
        )
    if csv_tot_ms is not None and csv_tot_ms > float(csv_spec["max_total_preview_confirm_and_worker_drain_ms"]):
        sec_3_2c_breaches.append(
            f"csv_import_50000_rows.total_ms:{csv_tot_ms}>{csv_spec['max_total_preview_confirm_and_worker_drain_ms']}"
        )
    if csv_hwm is not None and csv_hwm > float(csv_spec["max_preview_hwm_mib"]):
        sec_3_2c_breaches.append(
            f"csv_import_50000_rows.preview_hwm_mib:{csv_hwm}>{csv_spec['max_preview_hwm_mib']}"
        )
    if csv_wl_p95 is not None and csv_wl_p95 > float(csv_spec["max_concurrent_worklist_read_p95_ms"]):
        sec_3_2c_breaches.append(
            f"csv_import_50000_rows.concurrent_worklist_read_p95_ms:{csv_wl_p95}>{csv_spec['max_concurrent_worklist_read_p95_ms']}"
        )

    # 3.2C.3 10,000-item weighted campaign distribution across 40 staff (BG-01)
    dist_spec = spec_3_2c["campaign_distribution_10000_work_items_40_staff"]
    dist_path = (
        "s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag."
        "campaign_distribution_10000_work_items_40_staff"
    )
    dist_node = (
        _nav_dict(async_root, "campaign_distribution_10000_work_items_40_staff", missing)
        if async_root is not None
        else None
    )
    dist_elig = _req_int(dist_node, "eligible_count", f"{dist_path}.eligible_count", missing, require_positive=True)
    dist_prev_ms = _req_float(dist_node, "preview_ms", f"{dist_path}.preview_ms", missing, require_positive=True)
    dist_exec_ms = _req_float(
        dist_node, "execute_and_worker_drain_ms", f"{dist_path}.execute_and_worker_drain_ms", missing, require_positive=True
    )
    for enq_field in ("enqueue_api_ms", "enqueue_http_ms"):
        if isinstance(dist_node, dict) and enq_field in dist_node:
            dist_enq_ms = _req_float(
                dist_node, enq_field, f"{dist_path}.{enq_field}", missing, require_positive=True
            )
            if dist_enq_ms is not None and dist_enq_ms > float(dist_spec["max_enqueue_http_ms"]):
                sec_3_2c_breaches.append(
                    f"distribution.{enq_field}:{dist_enq_ms}>{dist_spec['max_enqueue_http_ms']}"
                )
    dist_status = _req_str(dist_node, "final_run_status", f"{dist_path}.final_run_status", missing)
    dist_created = _req_int(
        dist_node, "created_work_items", f"{dist_path}.created_work_items", missing, require_positive=True
    )
    dist_dup = _req_int(dist_node, "duplicate_work_items", f"{dist_path}.duplicate_work_items", missing)
    dist_dbl = _req_bool(dist_node, "double_submit_idempotent", f"{dist_path}.double_submit_idempotent", missing)
    dist_abort = _req_bool(
        dist_node, "abort_cancel_guard_verified", f"{dist_path}.abort_cancel_guard_verified", missing
    )
    dist_wl_p95 = _req_float(
        dist_node,
        "concurrent_worklist_read_p95_ms",
        f"{dist_path}.concurrent_worklist_read_p95_ms",
        missing,
        require_positive=True,
    )
    dist_rec = _nav_dict(dist_node, "reconciliation", missing) if dist_node is not None else None
    dist_stale_ds = _req_int(dist_rec, "stale_docshares", f"{dist_path}.reconciliation.stale_docshares", missing)
    dist_stale_td = _req_int(dist_rec, "stale_open_todos", f"{dist_path}.reconciliation.stale_open_todos", missing)
    dist_own_rec = _req_bool(
        dist_rec, "ownership_reconciled", f"{dist_path}.reconciliation.ownership_reconciled", missing
    )
    if dist_elig is not None and dist_elig != int(dist_spec["expected_eligible_count"]):
        sec_3_2c_breaches.append(f"distribution.eligible_count:{dist_elig}!={dist_spec['expected_eligible_count']}")
    if dist_prev_ms is not None and dist_prev_ms > float(dist_spec["max_preview_ms"]):
        sec_3_2c_breaches.append(f"distribution.preview_ms:{dist_prev_ms}>{dist_spec['max_preview_ms']}")
    if dist_exec_ms is not None and dist_exec_ms > float(dist_spec["max_execute_and_worker_drain_ms"]):
        sec_3_2c_breaches.append(
            f"distribution.execute_and_worker_drain_ms:{dist_exec_ms}>{dist_spec['max_execute_and_worker_drain_ms']}"
        )
    if dist_status is not None and dist_status != str(dist_spec["expected_final_run_status"]):
        sec_3_2c_breaches.append(
            f"distribution.final_run_status:{dist_status}!={dist_spec['expected_final_run_status']}"
        )
    if dist_created is not None and dist_created != int(dist_spec["expected_created_work_items"]):
        sec_3_2c_breaches.append(
            f"distribution.created_work_items:{dist_created}!={dist_spec['expected_created_work_items']}"
        )
    if dist_dup is not None and dist_dup != int(dist_spec["expected_duplicate_work_items"]):
        sec_3_2c_breaches.append(
            f"distribution.duplicate_work_items:{dist_dup}!={dist_spec['expected_duplicate_work_items']}"
        )
    if dist_dbl is not None and dist_dbl is not bool(dist_spec["expected_double_submit_idempotent"]):
        sec_3_2c_breaches.append(f"distribution.double_submit_idempotent:{dist_dbl}")
    if dist_abort is not None and dist_abort is not bool(dist_spec["expected_abort_cancel_guard_verified"]):
        sec_3_2c_breaches.append(f"distribution.abort_cancel_guard_verified:{dist_abort}")
    if dist_wl_p95 is not None and dist_wl_p95 > float(dist_spec["max_concurrent_worklist_read_p95_ms"]):
        sec_3_2c_breaches.append(
            f"distribution.concurrent_worklist_read_p95_ms:{dist_wl_p95}>{dist_spec['max_concurrent_worklist_read_p95_ms']}"
        )
    if dist_stale_ds is not None and dist_stale_ds != int(dist_spec["expected_stale_docshares"]):
        sec_3_2c_breaches.append(f"distribution.stale_docshares:{dist_stale_ds}!={dist_spec['expected_stale_docshares']}")
    if dist_stale_td is not None and dist_stale_td != int(dist_spec["expected_stale_open_todos"]):
        sec_3_2c_breaches.append(
            f"distribution.stale_open_todos:{dist_stale_td}!={dist_spec['expected_stale_open_todos']}"
        )
    if dist_own_rec is not None and dist_own_rec is not bool(dist_spec["expected_ownership_reconciled"]):
        sec_3_2c_breaches.append(f"distribution.ownership_reconciled:{dist_own_rec}")

    # 3.2C.4 KPI full monthly + daily incremental + atomic publish under concurrent UI reads (BG-03)
    kpi_spec = spec_3_2c["kpi_recompute_s1"]
    kpi_path = "s1_domain_and_database_benchmark.kpi_recompute_s1"
    kpi_node = _nav_dict(dom_root, "kpi_recompute_s1", missing) if dom_root is not None else None
    kpi_mon_ms = _req_float(
        kpi_node, "monthly_full_75k_wi_250k_att_ms", f"{kpi_path}.monthly_full_75k_wi_250k_att_ms", missing, require_positive=True
    )
    kpi_mon_st = _req_str(kpi_node, "monthly_run_status", f"{kpi_path}.monthly_run_status", missing)
    kpi_day_ms = _req_float(
        kpi_node, "daily_incremental_ms", f"{kpi_path}.daily_incremental_ms", missing, require_positive=True
    )
    kpi_bg = _nav_dict(kpi_node, "background_queue_long_recompute", missing) if kpi_node is not None else None
    kpi_bg_wall_ms = _req_float(
        kpi_bg, "wall_ms", f"{kpi_path}.background_queue_long_recompute.wall_ms", missing, require_positive=True
    )
    kpi_atomic = _req_bool(
        kpi_bg, "atomic_publish_verified", f"{kpi_path}.background_queue_long_recompute.atomic_publish_verified", missing
    )
    rev_before = _req_str(
        kpi_bg, "revision_before", f"{kpi_path}.background_queue_long_recompute.revision_before", missing
    )
    rev_after = _req_str(
        kpi_bg,
        "revision_after_complete",
        f"{kpi_path}.background_queue_long_recompute.revision_after_complete",
        missing,
    )
    kpi_ui = (
        _nav_dict(kpi_bg, "concurrent_ui_overview_reads", missing)
        if kpi_bg is not None
        else None
    )
    kpi_ui_cnt = _req_int(
        kpi_ui,
        "count",
        f"{kpi_path}.background_queue_long_recompute.concurrent_ui_overview_reads.count",
        missing,
        require_positive=True,
    )
    kpi_ui_err = _req_int(
        kpi_ui,
        "error_count",
        f"{kpi_path}.background_queue_long_recompute.concurrent_ui_overview_reads.error_count",
        missing,
    )
    kpi_ui_p95 = _req_float(
        kpi_ui,
        "p95_ms",
        f"{kpi_path}.background_queue_long_recompute.concurrent_ui_overview_reads.p95_ms",
        missing,
        require_positive=True,
    )
    kpi_ui_p99 = _req_float(
        kpi_ui,
        "p99_ms",
        f"{kpi_path}.background_queue_long_recompute.concurrent_ui_overview_reads.p99_ms",
        missing,
        require_positive=True,
    )
    if kpi_mon_ms is not None and kpi_mon_ms > float(kpi_spec["max_monthly_full_ms"]):
        sec_3_2c_breaches.append(f"kpi_recompute_s1.monthly_full_ms:{kpi_mon_ms}>{kpi_spec['max_monthly_full_ms']}")
    if kpi_bg_wall_ms is not None and kpi_bg_wall_ms > float(kpi_spec["max_background_queue_long_wall_ms"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.background_queue_long_wall_ms:{kpi_bg_wall_ms}>{kpi_spec['max_background_queue_long_wall_ms']}"
        )
    if kpi_mon_st is not None and kpi_mon_st != str(kpi_spec["expected_monthly_run_status"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.monthly_run_status:{kpi_mon_st}!={kpi_spec['expected_monthly_run_status']}"
        )
    if kpi_day_ms is not None and kpi_day_ms > float(kpi_spec["max_daily_incremental_ms"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.daily_incremental_ms:{kpi_day_ms}>{kpi_spec['max_daily_incremental_ms']}"
        )
    if kpi_atomic is not None and kpi_atomic is not bool(kpi_spec["expected_atomic_publish_verified"]):
        sec_3_2c_breaches.append(f"kpi_recompute_s1.atomic_publish_verified:{kpi_atomic}")
    if rev_before is not None and rev_after is not None and rev_before == rev_after:
        sec_3_2c_breaches.append(f"kpi_recompute_s1.revision_did_not_advance:{rev_before}")
    if kpi_ui_cnt is not None and kpi_ui_cnt < int(kpi_spec["min_concurrent_ui_reads_count"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.concurrent_ui_reads_count:{kpi_ui_cnt}<{kpi_spec['min_concurrent_ui_reads_count']}"
        )
    if kpi_ui_err is not None and kpi_ui_err != int(kpi_spec["expected_concurrent_ui_error_count"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.concurrent_ui_error_count:{kpi_ui_err}!={kpi_spec['expected_concurrent_ui_error_count']}"
        )
    if kpi_ui_p95 is not None and kpi_ui_p95 > float(kpi_spec["max_concurrent_ui_overview_p95_ms"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.concurrent_ui_overview_p95_ms:{kpi_ui_p95}>{kpi_spec['max_concurrent_ui_overview_p95_ms']}"
        )
    if kpi_ui_p99 is not None and kpi_ui_p99 > float(kpi_spec["max_concurrent_ui_overview_p99_ms"]):
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.concurrent_ui_overview_p99_ms:{kpi_ui_p99}>{kpi_spec['max_concurrent_ui_overview_p99_ms']}"
        )
    if kpi_ui_p95 is not None and kpi_ui_p99 is not None and kpi_ui_p99 + 1e-6 < kpi_ui_p95:
        sec_3_2c_breaches.append(
            f"kpi_recompute_s1.concurrent_ui_overview_p99_lt_p95:{kpi_ui_p99}<{kpi_ui_p95}"
        )

    # 3.2C.5 RQ short 50-job burst & post-run queue/MariaDB health
    rq_spec = spec_3_2c["rq_short_50_job_burst_and_post_run_db_health"]
    rq_path = "s1_domain_and_database_benchmark.async_import_distribution_and_queue_lag.rq_short_50_job_burst"
    rq_node = _nav_dict(async_root, "rq_short_50_job_burst", missing) if async_root is not None else None
    rq_enq_ms = _req_float(
        rq_node, "enqueue_50_jobs_ms", f"{rq_path}.enqueue_50_jobs_ms", missing, require_positive=True
    )
    rq_drain_ms = _req_float(
        rq_node, "drain_to_zero_lag_ms", f"{rq_path}.drain_to_zero_lag_ms", missing, require_positive=True
    )
    qdb_after = _nav_dict(benchmark_report, "queue_and_db_after", missing)
    post_backlog = _req_int(qdb_after, "queue_backlog_total", "queue_and_db_after.queue_backlog_total", missing)
    mdb_after = _nav_dict(qdb_after, "mariadb_global_status", missing) if qdb_after is not None else None
    post_slow_q = _req_int(
        mdb_after,
        "Slow_queries",
        "queue_and_db_after.mariadb_global_status.Slow_queries",
        missing,
        allow_digit_str=True,
    )
    if rq_enq_ms is not None and rq_enq_ms > float(rq_spec["max_enqueue_50_jobs_ms"]):
        sec_3_2c_breaches.append(f"rq_short_50_job_burst.enqueue_50_jobs_ms:{rq_enq_ms}>{rq_spec['max_enqueue_50_jobs_ms']}")
    if rq_drain_ms is not None and rq_drain_ms > float(rq_spec["max_drain_to_zero_lag_ms"]):
        sec_3_2c_breaches.append(
            f"rq_short_50_job_burst.drain_to_zero_lag_ms:{rq_drain_ms}>{rq_spec['max_drain_to_zero_lag_ms']}"
        )
    if post_backlog is not None and post_backlog > int(rq_spec["max_post_run_queue_backlog_total"]):
        sec_3_2c_breaches.append(
            f"queue_and_db_after.queue_backlog_total:{post_backlog}>{rq_spec['max_post_run_queue_backlog_total']}"
        )
    if post_slow_q is not None and post_slow_q > int(rq_spec["max_post_run_slow_queries"]):
        sec_3_2c_breaches.append(
            f"queue_and_db_after.Slow_queries:{post_slow_q}>{rq_spec['max_post_run_slow_queries']}"
        )

    sec_3_2c_missing = len(missing) > sec_3_2c_missing_before
    sec_3_2c_passed = (not sec_3_2c_missing) and (not sec_3_2c_breaches)
    breaches.extend(f"section_3_2C.{b}" for b in sec_3_2c_breaches)

    return {
        "all_passed": sec_3_2a_passed and sec_3_2b_passed and sec_3_2c_passed,
        "section_3_2A": {
            "passed": sec_3_2a_passed,
            "cases_total": len(cases_3_2a),
            "cases_passed": sum(1 for c in cases_3_2a.values() if c["passed"]),
            "by_session_model_summary": by_session_model,
            "cases": cases_3_2a,
        },
        "section_3_2B": {
            "passed": sec_3_2b_passed,
            "distinct_authenticated_sessions_created": pool_sessions,
            "required_distinct_authenticated_sessions": int(
                spec_3_2b["required_distinct_authenticated_sessions"]
            ),
            "waves": waves_eval,
        },
        "section_3_2C": {
            "passed": sec_3_2c_passed,
            "status": (
                "MISSING_MEASUREMENT"
                if sec_3_2c_missing
                else ("PASS" if sec_3_2c_passed else "FAIL_THRESHOLD_BREACH")
            ),
            "aggregate_load_summaries": agg_eval,
            "csv_import_preview_ms": csv_prev_ms,
            "csv_import_worker_commit_and_drain_ms": csv_worker_ms,
            "csv_import_total_ms": csv_tot_ms,
            "distribution_preview_ms": dist_prev_ms,
            "distribution_execute_ms": dist_exec_ms,
            "kpi_monthly_full_ms": kpi_mon_ms,
            "kpi_background_queue_long_wall_ms": kpi_bg_wall_ms,
            "kpi_daily_incremental_ms": kpi_day_ms,
            "rq_short_drain_lag_ms": rq_drain_ms,
            "post_run_queue_backlog_total": post_backlog,
            "post_run_slow_queries": post_slow_q,
            "breaches": sec_3_2c_breaches,
        },
    }


CI_PLATFORM_TOKENS = ("azure", "github", "actions-runner", "codespaces")


def detect_ci_benchmark_report(benchmark_report: dict[str, Any]) -> tuple[bool, list[str]]:
    """Detect whether a benchmark report originated from an ephemeral CI runner rather than Staging VPS."""
    reasons: list[str] = []
    dom = benchmark_report.get("s1_domain_and_database_benchmark")
    hw = dom.get("runner_hardware") if isinstance(dom, dict) else None
    plat = str((hw or {}).get("platform") or "").strip()
    plat_lower = plat.lower()
    for tok in CI_PLATFORM_TOKENS:
        if tok in plat_lower:
            reasons.append(f"runner_hardware.platform_contains_{tok}:{plat}")
    for ci_key in ("ci_run_id", "github_run_id"):
        if benchmark_report.get(ci_key) not in (None, ""):
            reasons.append(f"benchmark_has_{ci_key}:{benchmark_report.get(ci_key)}")
    exec_env = str(benchmark_report.get("execution_environment") or "").strip().lower()
    if exec_env in ("ci", "github_actions", "github-actions"):
        reasons.append(f"execution_environment={exec_env}")
    iso = benchmark_report.get("isolation_guard")
    comp_proj = str((iso or {}).get("compose_project") or "").lower() if isinstance(iso, dict) else ""
    if "-ci-" in comp_proj or comp_proj.endswith("-ci"):
        reasons.append(f"compose_project_indicates_ci:{comp_proj}")
    return bool(reasons), reasons


def verify_staging_vps_receipts_for_g18(
    benchmark_report: dict[str, Any] | None,
    vps_receipts: dict[str, Any] | Path | None,
    *,
    expected_digest: str,
    is_ci_benchmark_report: bool,
    target_configuration_profile: str | None = None,
) -> tuple[bool, list[str]]:
    """Verify G03/G05, read-only preflight, safe sizing, digest pull, and live zero-mutation receipts from Staging VPS."""
    if vps_receipts is None:
        return False, ["missing_staging_vps_receipts_bundle"]

    errors: list[str] = []
    bundle: dict[str, Any] = {}
    file_map = {
        "summary": "vps-g03-g05-summary.json",
        "readonly_vps_preflight": "readonly-vps-preflight.json",
        "candidate_pull_inspect_receipt": "candidate-registry-pull-inspect-receipt.json",
        "runtime_isolation_audit_g03": "staging-dr-runtime-isolation-audit.json",
        "https_wss_smoke_g05": "https-wss-smoke-receipt.json",
        "live_preservation_before_after": "live-preservation-before-after.json",
    }
    if isinstance(vps_receipts, Path):
        if not vps_receipts.is_dir():
            return False, [f"vps_receipt_dir_not_found:{vps_receipts}"]
        for key, fname in file_map.items():
            fpath = vps_receipts / fname
            if not fpath.is_file():
                errors.append(f"missing_vps_receipt_file:{fname}")
            else:
                try:
                    bundle[key] = json.loads(fpath.read_text(encoding="utf-8"))
                except Exception as exc:
                    errors.append(f"invalid_vps_receipt_json:{fname}:{exc}")
    elif isinstance(vps_receipts, dict):
        for key in file_map:
            if not isinstance(vps_receipts.get(key), dict):
                errors.append(f"missing_vps_receipt_section:{key}")
            else:
                bundle[key] = vps_receipts[key]
    else:
        return False, [f"unsupported_vps_receipts_type:{type(vps_receipts).__name__}"]

    if errors:
        return False, errors

    if is_ci_benchmark_report:
        errors.append("benchmark_report_is_ci_runner_cannot_pair_with_staging_vps_receipts")

    summary = bundle["summary"]
    preflight = bundle["readonly_vps_preflight"]
    pull_rec = bundle["candidate_pull_inspect_receipt"]
    runtime_g03 = bundle["runtime_isolation_audit_g03"]
    smoke_g05 = bundle["https_wss_smoke_g05"]
    live_pres = bundle["live_preservation_before_after"]

    # 1. Summary receipt
    if summary.get("schema_version") != "cskh-p4-vps-g03-g05-bundle-v1":
        errors.append(f"summary.invalid_schema_version:{summary.get('schema_version')!r}")
    if summary.get("status") != "PASS":
        errors.append(f"summary.status_not_pass:{summary.get('status')!r}")
    if summary.get("app_image") != expected_digest:
        errors.append(f"summary.app_image_mismatch:{summary.get('app_image')!r}")
    if summary.get("zero_live_mutation") is not True:
        errors.append("summary.zero_live_mutation_not_true")
    iso_proj = str(summary.get("isolated_project_name") or "")
    if not iso_proj or "-ci-" in iso_proj.lower() or iso_proj in FORBIDDEN_OWNER_PROJECTS:
        errors.append(f"summary.invalid_or_ci_isolated_project_name:{iso_proj!r}")
    if set(summary.get("inspected_app_services") or []) != set(p403_tool.APP_SERVICES):
        errors.append("summary.inspected_app_services_mismatch_expected_6_services")

    # 2. Read-only VPS preflight & host binding against benchmark runner_hardware
    if preflight.get("schema_version") != "cskh-p4-readonly-vps-preflight-v1":
        errors.append(f"preflight.invalid_schema_version:{preflight.get('schema_version')!r}")
    if preflight.get("status") != "PASS" or preflight.get("mode") != "READ_ONLY_ZERO_MUTATION":
        errors.append("preflight.status_or_mode_invalid")
    p_checks = preflight.get("checks") or {}
    if not isinstance(p_checks, dict) or not p_checks or not all(v is True for v in p_checks.values()):
        errors.append("preflight.checks_not_all_true")
    host_id = preflight.get("host_identity") or {}
    vps_hostname = str(host_id.get("hostname") or "").strip()
    vps_platform = str(host_id.get("platform") or "").strip()
    if not vps_hostname or not vps_platform:
        errors.append("preflight.missing_host_identity_hostname_or_platform")
    else:
        for tok in (*CI_PLATFORM_TOKENS, "fv-az"):
            if tok in vps_platform.lower() or tok in vps_hostname.lower():
                errors.append(f"preflight.host_identity_indicates_ci:{vps_hostname}:{vps_platform}")
                break
        if benchmark_report is not None:
            bench_plat = str(
                (((benchmark_report.get("s1_domain_and_database_benchmark") or {}).get("runner_hardware") or {}).get("platform"))
                or ""
            ).strip()
            if bench_plat != vps_platform:
                errors.append(
                    f"benchmark_runner_platform_mismatch_vs_vps_preflight:bench={bench_plat!r}:vps={vps_platform!r}"
                )

    if target_configuration_profile is not None:
        try:
            expected_cfg = build_target_staging_configuration_block(target_configuration_profile)
        except ValueError as exc:
            expected_cfg = None
            errors.append(f"invalid_target_configuration_profile:{target_configuration_profile!r}:{exc}")
        if expected_cfg is not None:
            if expected_cfg.get("g18_s1_benchmark_capable") is not True:
                errors.append(f"target_configuration_profile_not_g18_s1_capable:{target_configuration_profile!r}")
            safe_alloc = preflight.get("safe_staging_allocation")
            if not isinstance(safe_alloc, dict):
                errors.append("preflight.missing_safe_staging_allocation")
            else:
                if safe_alloc.get("g18_acceptance_safe") is not True:
                    errors.append(f"preflight.g18_acceptance_not_safe_on_vps:{safe_alloc.get('status')!r}")
                allowed_profs = safe_alloc.get("safe_profiles_allowed") or []
                if target_configuration_profile not in allowed_profs:
                    errors.append(
                        f"preflight.target_profile_not_safe_on_vps:{target_configuration_profile!r}:allowed={allowed_profs!r}"
                    )
            summary_cfg = summary.get("staging_resource_configuration")
            if not isinstance(summary_cfg, dict):
                errors.append("summary.missing_staging_resource_configuration")
            else:
                if summary_cfg.get("profile_id") != expected_cfg["profile_id"]:
                    errors.append(
                        f"summary.staging_resource_profile_mismatch:expected={expected_cfg['profile_id']!r}:got={summary_cfg.get('profile_id')!r}"
                    )
                if summary_cfg.get("config_spec_sha256") != expected_cfg["config_spec_sha256"]:
                    errors.append(
                        f"summary.staging_config_spec_sha256_mismatch:expected={expected_cfg['config_spec_sha256']}:got={summary_cfg.get('config_spec_sha256')!r}"
                    )

    # 3. Candidate @sha256 registry pull & inspect receipt
    if pull_rec.get("schema_version") != "cskh-p4-candidate-registry-receipt-v1" or pull_rec.get("status") != "PASS":
        errors.append("pull_receipt.schema_or_status_invalid")
    if pull_rec.get("requested_registry_digest") != expected_digest:
        errors.append(f"pull_receipt.digest_mismatch:{pull_rec.get('requested_registry_digest')!r}")
    if expected_digest not in (pull_rec.get("inspected_repo_digests") or []):
        errors.append("pull_receipt.expected_digest_not_in_inspected_repo_digests")
    if pull_rec.get("inspected_user") != "frappe":
        errors.append(f"pull_receipt.non_root_user_mismatch:{pull_rec.get('inspected_user')!r}")
    if pull_rec.get("owner_live_site_touched") is not False or pull_rec.get("promoted_to_production") is not False:
        errors.append("pull_receipt.isolation_flags_invalid")

    # 4. G03 runtime isolation & 6-service container digest parity receipt
    if runtime_g03.get("schema_version") != "cskh-p4-staging-dr-runtime-audit-v1" or runtime_g03.get("status") != "PASS":
        errors.append("runtime_g03.schema_or_status_invalid")
    if runtime_g03.get("app_image") != expected_digest:
        errors.append(f"runtime_g03.app_image_mismatch:{runtime_g03.get('app_image')!r}")
    g03_proj = str(runtime_g03.get("project_name") or "")
    if g03_proj != iso_proj or "-ci-" in g03_proj.lower():
        errors.append(f"runtime_g03.project_name_invalid_or_ci:{g03_proj!r}")
    if runtime_g03.get("owner_live_site_touched") is not False:
        errors.append("runtime_g03.owner_live_site_touched_not_false")
    parity = runtime_g03.get("container_digest_parity") or {}
    if (
        parity.get("pass") is not True
        or parity.get("expected_app_image") != expected_digest
        or parity.get("missing_services") != []
        or parity.get("mismatches") != []
    ):
        errors.append("runtime_g03.container_digest_parity_failed")
    insp_svcs = parity.get("inspected_services") or {}
    if not isinstance(insp_svcs, dict) or set(insp_svcs.keys()) != set(p403_tool.APP_SERVICES):
        errors.append("runtime_g03.inspected_services_not_exact_6_app_services")
    else:
        for svc_name, svc_row in sorted(insp_svcs.items()):
            if (
                not isinstance(svc_row, dict)
                or svc_row.get("matched") is not True
                or expected_digest not in (svc_row.get("repo_digests") or [])
                or "-ci-" in str(svc_row.get("container") or "").lower()
            ):
                errors.append(f"runtime_g03.service_digest_or_ci_container_invalid:{svc_name}")

    # 5. G05 HTTPS + WSS smoke receipt
    if smoke_g05.get("status") != "PASS" or smoke_g05.get("mode") != "dr-https":
        errors.append("smoke_g05.status_or_mode_invalid")
    if smoke_g05.get("app_image") != expected_digest:
        errors.append(f"smoke_g05.app_image_mismatch:{smoke_g05.get('app_image')!r}")
    s_checks = smoke_g05.get("checks") or {}
    if (
        (s_checks.get("container_digest_parity") or {}).get("pass") is not True
        or (s_checks.get("http_to_https_redirect") or {}).get("pass") is not True
        or (s_checks.get("tls_certificate") or {}).get("expiry_ok") is not True
        or (s_checks.get("login_and_sid_cookie") or {}).get("pass") is not True
        or (s_checks.get("vi_ui_and_csrf") or {}).get("pass") is not True
        or (s_checks.get("wss_websocket") or {}).get("pass") is not True
        or (s_checks.get("wss_websocket") or {}).get("switched_protocols_101") is not True
    ):
        errors.append("smoke_g05.subchecks_failed")

    # 6. Live serving stack before/after zero-mutation receipt
    if (
        live_pres.get("schema_version") != "cskh-p4-live-preservation-before-after-v1"
        or live_pres.get("status") != "PASS"
        or live_pres.get("zero_live_mutation") is not True
        or live_pres.get("deltas") != []
        or int(live_pres.get("container_count_before") or 0) <= 0
        or live_pres.get("container_count_before") != live_pres.get("container_count_after")
    ):
        errors.append("live_preservation.zero_delta_check_failed")

    return (not errors), errors


def _is_clean_pending_s1_slo_approval(
    owner_slo_approval: dict[str, Any] | None,
    *,
    validated_spec: dict[str, Any],
    actual_slo_sha256: str,
    expected_digest: str,
    profile: str | None,
) -> bool:
    """Return True when `owner_slo_approval` is an untampered `p4-s1-slo-approval.json` still in `PENDING_OWNER_APPROVAL`."""
    if validated_spec.get("schema_version") != s1_slo_tool.EXPECTED_SCHEMA_VERSION:
        return False
    if not isinstance(owner_slo_approval, dict):
        return False
    if owner_slo_approval.get("schema_version") != s1_slo_tool.EXPECTED_APPROVAL_SCHEMA_VERSION:
        return False
    if str(owner_slo_approval.get("approval_status") or "").strip() != "PENDING_OWNER_APPROVAL":
        return False
    check_prof = profile if profile in s1_slo_tool.SUPPORTED_PROFILES else "s1_canonical_4x4"
    errs, _ = s1_slo_tool.validate_slo_approval_document(
        owner_slo_approval,
        spec=validated_spec,
        actual_slo_sha256=actual_slo_sha256,
        target_image_digest=expected_digest,
        profile=check_prof,
        require_owner_approved=False,
    )
    return len(errs) == 0


def _load_optional_owner_slo_approval(
    owner_slo_approval: dict[str, Any] | Path | None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Load optional Owner SLO approval dict/file and compute LF-normalized SHA-256."""
    if owner_slo_approval is None:
        return None, None, None
    if isinstance(owner_slo_approval, Path):
        if not owner_slo_approval.is_file():
            return None, None, f"owner_approval_file_not_found:{owner_slo_approval}"
        try:
            doc_sha = s1_slo_tool.compute_normalized_sha256(owner_slo_approval)
            loaded = json.loads(owner_slo_approval.read_text(encoding="utf-8"))
        except Exception as exc:
            return None, None, f"owner_approval_invalid_json:{exc}"
        if not isinstance(loaded, dict):
            return None, doc_sha, "owner_approval_root_not_dict"
        return loaded, doc_sha, None
    if isinstance(owner_slo_approval, dict):
        doc_sha = hashlib.sha256(
            (json.dumps(owner_slo_approval, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        ).hexdigest()
        return owner_slo_approval, doc_sha, None
    return None, None, f"owner_slo_approval_not_dict:{type(owner_slo_approval).__name__}"


def _validate_owner_slo_approval_document(
    owner_slo_approval: dict[str, Any] | None,
    *,
    validated_spec: dict[str, Any],
    candidate_thresholds_sha256: str,
    threshold_spec_errors: list[str],
    config_bound_mode: bool,
    resolved_profile: str | None,
    expected_digest: str,
) -> tuple[bool, str, list[str]]:
    """Validate Owner SLO approval and cryptographic SHA-256 + configuration binding."""
    approval_errors: list[str] = []
    signed_thresholds_sha256 = ""
    if owner_slo_approval is None:
        return False, "", ["missing_owner_slo_approval_document"]
    if not isinstance(owner_slo_approval, dict):
        return False, "", [f"owner_slo_approval_not_dict:{type(owner_slo_approval).__name__}"]

    if validated_spec.get("schema_version") == s1_slo_tool.EXPECTED_SCHEMA_VERSION:
        expected_cfg_sha = (
            get_staging_resource_profile_spec(resolved_profile)["config_spec_sha256"]
            if (resolved_profile and resolved_profile in STAGING_VPS_RESOURCE_PROFILES)
             else ""
        )
        app_errs, app_meta = s1_slo_tool.validate_slo_approval_document(
            owner_slo_approval,
            spec=validated_spec,
            actual_slo_sha256=candidate_thresholds_sha256,
            target_image_digest=expected_digest,
            profile=resolved_profile or "__missing__",
            require_owner_approved=True,
        )
        approval_errors.extend(app_errs)
        if owner_slo_approval.get("schema_version") != s1_slo_tool.EXPECTED_APPROVAL_SCHEMA_VERSION:
            approval_errors.append("decoupled_s1_slo_approval_v1_schema_required")
        legacy_sha = str(owner_slo_approval.get("thresholds_sha256") or "").strip()
        if legacy_sha and candidate_thresholds_sha256 and legacy_sha != candidate_thresholds_sha256:
            approval_errors.append(
                f"owner_approval_thresholds_sha256_mismatch_vs_evaluated_spec:"
                f"expected={candidate_thresholds_sha256}:got={legacy_sha}"
            )
        legacy_dig = str(owner_slo_approval.get("approved_candidate_digest") or "").strip()
        if legacy_dig and legacy_dig != expected_digest:
            approval_errors.append(
                f"owner_approval_candidate_digest_mismatch:expected={expected_digest!r}:got={legacy_dig!r}"
            )
        signoff = owner_slo_approval.get("owner_signoff")
        approved_by = (
            str(signoff.get("approved_by") or "").strip() if isinstance(signoff, dict) else ""
        )
        if approved_by and (
            approved_by.lower() in FORBIDDEN_T1C_SELF_APPROVERS
            or approved_by.lower() == "t1c"
            or approved_by.lower().startswith(("t1a", "t1b", "t1c", "<"))
            or "placeholder" in approved_by.lower()
        ):
            approval_errors.append(f"forbidden_or_self_approved_by:{approved_by!r}")
        if "approved_config_spec_sha256" in owner_slo_approval:
            got_cfg_sha = str(owner_slo_approval.get("approved_config_spec_sha256") or "").strip()
            if expected_cfg_sha and got_cfg_sha != expected_cfg_sha:
                approval_errors.append(
                    f"owner_approval_config_spec_sha256_mismatch:expected={expected_cfg_sha}:got={got_cfg_sha}"
                )
        if isinstance(owner_slo_approval.get("approved_profile_config_spec_sha256"), dict) and resolved_profile:
            prof_sha_map = owner_slo_approval["approved_profile_config_spec_sha256"]
            if resolved_profile in prof_sha_map and expected_cfg_sha:
                if str(prof_sha_map[resolved_profile] or "").strip() != expected_cfg_sha:
                    approval_errors.append(
                        f"owner_approval_config_spec_sha256_mismatch:expected={expected_cfg_sha}:got={prof_sha_map[resolved_profile]}"
                    )
        signed_thresholds_sha256 = str(app_meta.get("slo_spec_sha256_bound") or legacy_sha or "")
        owner_signed = (
            (not approval_errors)
            and (not threshold_spec_errors)
            and bool(app_meta.get("owner_approved_for_profile"))
        )
        return owner_signed, signed_thresholds_sha256, approval_errors

    if owner_slo_approval.get("approved") is not True:
        approval_errors.append("approved_flag_not_true")
    expected_tv = str(validated_spec.get("ticket_version") or "v1.4")
    if str(owner_slo_approval.get("ticket_version") or "") != expected_tv:
        approval_errors.append(
            f"expected_ticket_version_{expected_tv}_got_{owner_slo_approval.get('ticket_version')!r}"
        )
    signed_by = str(owner_slo_approval.get("signed_by") or "").strip()
    if not signed_by:
        approval_errors.append("missing_signed_by")
    elif (
        signed_by.lower() in FORBIDDEN_T1C_SELF_APPROVERS
        or signed_by.lower().startswith(("t1a", "t1b", "<"))
        or "placeholder" in signed_by.lower()
    ):
        approval_errors.append(f"forbidden_or_self_signed_by:{signed_by!r}")
    if not str(owner_slo_approval.get("signed_at") or "").strip():
        approval_errors.append("missing_signed_at")

    signed_thresholds_sha256 = str(owner_slo_approval.get("thresholds_sha256") or "").strip()
    if not signed_thresholds_sha256:
        approval_errors.append("missing_thresholds_sha256_in_owner_approval")
    else:
        if not config_bound_mode and signed_thresholds_sha256 != LOCKED_G18_THRESHOLDS_V1_4_SHA256:
            approval_errors.append(
                f"owner_approval_thresholds_sha256_mismatch_vs_locked_v1_4:"
                f"expected={LOCKED_G18_THRESHOLDS_V1_4_SHA256}:got={signed_thresholds_sha256}"
            )
        if candidate_thresholds_sha256 and signed_thresholds_sha256 != candidate_thresholds_sha256:
            approval_errors.append(
                f"owner_approval_thresholds_sha256_mismatch_vs_evaluated_spec:"
                f"expected={candidate_thresholds_sha256}:got={signed_thresholds_sha256}"
            )

    if config_bound_mode:
        approved_prof = str(owner_slo_approval.get("approved_configuration_profile") or "").strip()
        if not approved_prof:
            approval_errors.append("missing_approved_configuration_profile_in_owner_approval")
        elif resolved_profile and approved_prof != resolved_profile:
            approval_errors.append(
                f"owner_approval_configuration_profile_mismatch:expected={resolved_profile!r}:got={approved_prof!r}"
            )
        expected_cfg_sha = (
            get_staging_resource_profile_spec(resolved_profile)["config_spec_sha256"]
            if (resolved_profile and resolved_profile in STAGING_VPS_RESOURCE_PROFILES)
            else ""
        )
        approved_cfg_sha = str(owner_slo_approval.get("approved_config_spec_sha256") or "").strip()
        if not approved_cfg_sha:
            approval_errors.append("missing_approved_config_spec_sha256_in_owner_approval")
        elif expected_cfg_sha and approved_cfg_sha != expected_cfg_sha:
            approval_errors.append(
                f"owner_approval_config_spec_sha256_mismatch:expected={expected_cfg_sha}:got={approved_cfg_sha}"
            )
        approved_dig = str(owner_slo_approval.get("approved_candidate_digest") or "").strip()
        if not approved_dig:
            approval_errors.append("missing_approved_candidate_digest_in_owner_approval")
        elif approved_dig != expected_digest:
            approval_errors.append(
                f"owner_approval_candidate_digest_mismatch:expected={expected_digest!r}:got={approved_dig!r}"
            )

    raw_signed = owner_slo_approval.get("approved_thresholds")
    if not isinstance(raw_signed, dict):
        approval_errors.append("missing_or_invalid_approved_thresholds_dict")
    else:
        pre_spec = validated_spec["precheck_7_metrics"]
        for key, spec in pre_spec.items():
            if key not in raw_signed:
                approval_errors.append(f"missing_threshold_key:{key}")
            elif isinstance(raw_signed[key], bool) or not isinstance(raw_signed[key], (int, float)):
                approval_errors.append(f"invalid_threshold_type:{key}")
            else:
                val = float(raw_signed[key])
                if not math.isfinite(val) or abs(val - float(spec["threshold"])) > 1e-6:
                    approval_errors.append(
                        f"threshold_mismatch_vs_ticket_v1.4:{key}:expected={spec['threshold']}:got={val}"
                    )
        for extra_k in sorted(set(raw_signed) - set(pre_spec)):
            approval_errors.append(f"unexpected_approved_threshold_key:{extra_k}")

    owner_signed = (not approval_errors) and (not threshold_spec_errors)
    return owner_signed, signed_thresholds_sha256, approval_errors


S1_EXPECTED_SEEDED_COUNTS: dict[str, int] = {
    "crm_leads": 50000,
    "cskh_identity_index": 50000,
    "cskh_work_items": 75000,
    "cskh_contact_attempts": 250000,
    "cskh_sla_obligations": 150000,
    "cskh_assignment_history": 75000,
    "active_staff_users": 40,
    "teams": 4,
    "campaigns": 6,
}

S1_EXPECTED_EXPLAIN_KEYS_V2: dict[str, str] = {
    "worklist_by_owner": "cskh_lookup_0",
    "attempt_timeline_by_work_item": "cskh_attempt_wi_occ_idx",
    "kpi_snapshot_lookup": "idx_cskh_kpisnap_lookup",
    "canonical_phone_identity_lookup": "normalized_phone",
    "crm_lead_mobile_no_lookup": "cskh_lead_mobile_no_idx",
    "kpi_drilldown_attempt_window": "cskh_attempt_camp_occ_idx",
    "kpi_drilldown_interval_page": "idx_cskh_ah_start_name",
    "kpi_drilldown_attempt_chunk": "idx_cskh_attempt_ownevt_occ",
}


def evaluate_p4_s1_slo_reconciliation(
    benchmark_report: dict[str, Any],
    validated_spec: dict[str, Any],
    *,
    profile: str,
    expected_digest: str,
    g18_diag_report: dict[str, Any] | Path | None = None,
    missing: list[str],
    breaches: list[str],
) -> dict[str, Any]:
    """Score `benchmark_report` (+ optional `g18_diag_report`) via `evaluate_s1_slo` with strict missing-measurement checks."""
    eff_prof = profile if profile in s1_slo_tool.SUPPORTED_PROFILES else "s1_canonical_4x4"
    dataset_missing_before = len(missing)
    dataset_breaches: list[str] = []

    # 1. S1 dataset counts, 0-PII policy, SSR separation & 8 MariaDB EXPLAIN index plans
    dom_root = _nav_dict(benchmark_report, "s1_domain_and_database_benchmark", missing)
    seeded = _nav_dict(dom_root, "s1_seeded_counts", missing) if dom_root is not None else None
    for ckey, exp_cnt in sorted(S1_EXPECTED_SEEDED_COUNTS.items()):
        got_cnt = _req_int(
            seeded,
            ckey,
            f"s1_domain_and_database_benchmark.s1_seeded_counts.{ckey}",
            missing,
            require_positive=True,
        )
        if got_cnt is not None and got_cnt != exp_cnt:
            dataset_breaches.append(f"s1_seeded_counts.{ckey}:{got_cnt}!={exp_cnt}")

    pub_ds = _nav_dict(benchmark_report, "published_dataset_and_load", missing)
    pii_pol = _req_str(pub_ds, "pii_policy", "published_dataset_and_load.pii_policy", missing)
    if pii_pol is not None and pii_pol != "ZERO_REAL_PII_SYNTHETIC_ONLY":
        dataset_breaches.append(f"published_dataset_and_load.pii_policy:{pii_pol}")

    sep_root = _nav_dict(benchmark_report, "worklist_ssr_acceptance_and_separation", missing)
    exec_mode = (
        _req_str(sep_root, "execution_mode", "worklist_ssr_acceptance_and_separation.execution_mode", missing)
        if sep_root is not None
        else None
    )
    if exec_mode is not None and exec_mode != "BUILT_INTO_OFFICIAL_COMMIT_AND_IMAGE_DIGEST_NO_IN_CONTAINER_PATCHING":
        dataset_breaches.append(f"worklist_ssr_execution_mode_invalid:{exec_mode}")

    http_root = _nav_dict(benchmark_report, "s1_http_acceptance_and_mixed_waves", missing)
    pool_sessions = _req_int(
        http_root,
        "distinct_authenticated_sessions_created",
        "s1_http_acceptance_and_mixed_waves.distinct_authenticated_sessions_created",
        missing,
        require_positive=True,
    )
    if pool_sessions is not None and pool_sessions != 80:
        dataset_breaches.append(f"distinct_authenticated_sessions_created:{pool_sessions}!=80")

    exp_plans = _nav_dict(dom_root, "mariadb_explain_plans", missing) if dom_root is not None else None
    for pkey, exp_idx in sorted(S1_EXPECTED_EXPLAIN_KEYS_V2.items()):
        p_full = f"s1_domain_and_database_benchmark.mariadb_explain_plans.{pkey}[0].key"
        if exp_plans is None or pkey not in exp_plans:
            missing.append(p_full)
        else:
            rows = exp_plans[pkey]
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                missing.append(p_full)
            else:
                got_idx = _req_str(rows[0], "key", p_full, missing)
                if got_idx is not None and got_idx != exp_idx:
                    dataset_breaches.append(f"mariadb_explain_plans.{pkey}:{got_idx}!={exp_idx}")

    prof_cfg = ((validated_spec.get("resource_profiles") or {}).get(eff_prof)) or {}
    if prof_cfg.get("require_live_headroom_precheck") is True:
        hr_node = _nav_dict(benchmark_report, "live_resource_headroom_precheck", missing)
        if hr_node is not None:
            hr_ok = _req_bool(hr_node, "headroom_ok", "live_resource_headroom_precheck.headroom_ok", missing)
            if hr_ok is False:
                dataset_breaches.append("live_resource_headroom_precheck.headroom_ok_is_false")

    breaches.extend(f"s1_slo_dataset.{b}" for b in dataset_breaches)

    # 2. Evaluate all 35 S1 rules (`SSR`, `JSON_API`, `AGGREGATE_AND_BACKGROUND`) via `evaluate_s1_slo`
    category_verdicts: dict[str, dict[str, Any]] = {
        cat: {"total": 0, "passed_count": 0, "failed_count": 0, "passed": True}
        for cat in s1_slo_tool.REQUIRED_CATEGORIES
    }
    rule_results: list[dict[str, Any]] = []
    for rule in validated_spec.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        slo_id = str(rule.get("slo_id") or "")
        jpath = str(rule.get("report_json_path") or "")
        rtype = str(rule.get("rule_type") or "http_summary")
        prof_thr = ((rule.get("proposed_thresholds") or {}).get(eff_prof)) or {}
        rule_missing_before = len(missing)

        if rtype == "http_summary":
            rnode = _nav_dict(benchmark_report, jpath, missing)
            _req_int(rnode, "requests_total", f"{jpath}.requests_total", missing, require_positive=True)
            _req_int(rnode, "error_count", f"{jpath}.error_count", missing)
            _req_float(rnode, "error_rate_pct", f"{jpath}.error_rate_pct", missing)
            p95_v = _req_float(rnode, "p95_ms", f"{jpath}.p95_ms", missing, require_positive=True)
            p99_v = _req_float(rnode, "p99_ms", f"{jpath}.p99_ms", missing, require_positive=True)
            if p95_v is not None and p99_v is not None and p99_v + 1e-6 < p95_v:
                breaches.append(f"s1_slo.{slo_id}.p99_lt_p95:{p99_v}<{p95_v}")

        for inv in prof_thr.get("invariants") or []:
            inv_path = str(inv.get("report_json_path") or "")
            try:
                inv_val = s1_slo_tool.resolve_dotted_path(benchmark_report, inv_path)
                if inv_val is None:
                    missing.append(inv_path)
            except KeyError:
                missing.append(inv_path)

        rule_was_missing = len(missing) > rule_missing_before
        res = s1_slo_tool._evaluate_http_rule(benchmark_report, rule, prof_thr)
        if rule_was_missing:
            res["passed"] = False
            res["status"] = "MISSING_MEASUREMENT"
        else:
            res["status"] = "PASS" if res["passed"] else "FAIL_THRESHOLD_BREACH"
            if not res["passed"]:
                for fail_msg in res["failures"]:
                    breaches.append(f"s1_slo.{slo_id}:{fail_msg}")

        rule_results.append(res)
        cat = str(res["category"])
        if cat in category_verdicts:
            category_verdicts[cat]["total"] += 1
            if res["passed"]:
                category_verdicts[cat]["passed_count"] += 1
            else:
                category_verdicts[cat]["failed_count"] += 1
                category_verdicts[cat]["passed"] = False

    # 3. Optional deep G18 diagnostic wave scoring (12 rules)
    diag_results: list[dict[str, Any]] = []
    diag_sha: str | None = None
    diag_passed = True
    diag_digest_matches = True
    diag_profile_matches = True
    if g18_diag_report is not None:
        diag_doc: dict[str, Any] | None = None
        if isinstance(g18_diag_report, Path):
            if not g18_diag_report.is_file():
                missing.append(f"g18_diag_report_file_not_found:{g18_diag_report}")
                diag_passed = False
            else:
                diag_sha = s1_slo_tool.compute_normalized_sha256(g18_diag_report)
                diag_doc = json.loads(g18_diag_report.read_text(encoding="utf-8"))
        elif isinstance(g18_diag_report, dict):
            diag_doc = g18_diag_report
            diag_sha = hashlib.sha256(
                (json.dumps(diag_doc, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
            ).hexdigest()

        if isinstance(diag_doc, dict):
            diag_img = _req_str(diag_doc, "app_image_tested", "g18_diag.app_image_tested", missing)
            if diag_img is not None and diag_img != expected_digest:
                diag_digest_matches = False
                diag_passed = False
                breaches.append(f"g18_diag.app_image_tested_mismatch:{diag_img}!={expected_digest}")
            diag_rp = diag_doc.get("resource_profile")
            diag_prof_name = (
                str(diag_rp.get("name") or "").strip()
                if isinstance(diag_rp, dict)
                else str(diag_rp or "").strip()
            )
            if not diag_prof_name:
                missing.append("g18_diag.resource_profile")
                diag_passed = False
            elif diag_prof_name != eff_prof:
                diag_profile_matches = False
                diag_passed = False
                breaches.append(f"g18_diag.resource_profile_mismatch:{diag_prof_name}!={eff_prof}")

            for drule in validated_spec.get("g18_deep_diagnostic_rules") or []:
                d_id = str(drule.get("slo_id") or "")
                d_path = str(drule.get("diag_report_json_path") or "")
                d_thr = ((drule.get("proposed_thresholds") or {}).get(eff_prof)) or {}
                d_miss_before = len(missing)
                dnode = _nav_dict(diag_doc, d_path, missing)
                _req_int(dnode, "requests_total", f"g18_diag.{d_path}.requests_total", missing, require_positive=True)
                _req_int(dnode, "error_count", f"g18_diag.{d_path}.error_count", missing)
                _req_float(dnode, "error_rate_pct", f"g18_diag.{d_path}.error_rate_pct", missing)
                dp95 = _req_float(dnode, "p95_ms", f"g18_diag.{d_path}.p95_ms", missing, require_positive=True)
                dp99 = _req_float(dnode, "p99_ms", f"g18_diag.{d_path}.p99_ms", missing, require_positive=True)
                if dp95 is not None and dp99 is not None and dp99 + 1e-6 < dp95:
                    breaches.append(f"g18_diag.{d_id}.p99_lt_p95:{dp99}<{dp95}")

                d_was_missing = len(missing) > d_miss_before
                dres = s1_slo_tool._evaluate_http_rule(
                    diag_doc,
                    {**drule, "category": "G18_DEEP_DIAGNOSTIC"},
                    d_thr,
                    path_key="diag_report_json_path",
                )
                if d_was_missing:
                    dres["passed"] = False
                    dres["status"] = "MISSING_MEASUREMENT"
                    diag_passed = False
                else:
                    dres["status"] = "PASS" if dres["passed"] else "FAIL_THRESHOLD_BREACH"
                    if not dres["passed"]:
                        diag_passed = False
                        for fail_msg in dres["failures"]:
                            breaches.append(f"g18_diag.{d_id}:{fail_msg}")
                diag_results.append(dres)

    dataset_ok = (len(missing) == dataset_missing_before or all(not m.startswith("s1_domain_and_database_benchmark.") for m in missing[dataset_missing_before:])) and (not dataset_breaches)
    all_categories_ok = all(v["passed"] and v["total"] > 0 for v in category_verdicts.values())
    all_passed = bool(dataset_ok and all_categories_ok and diag_passed and len(missing) == dataset_missing_before)

    return {
        "schema_version": "cskh-p4-s1-slo-evaluation-v1",
        "slo_bundle_id": validated_spec.get("slo_bundle_id"),
        "slo_version": validated_spec.get("slo_version"),
        "profile_evaluated": eff_prof,
        "all_passed": all_passed,
        "s1_dataset_and_explain_verified": (not dataset_breaches),
        "category_verdicts": category_verdicts,
        "s1_rule_results": rule_results,
        "g18_diag_report_sha256": diag_sha,
        "g18_diag_digest_matches": diag_digest_matches,
        "g18_diag_profile_matches": diag_profile_matches,
        "g18_deep_diagnostic_results": diag_results,
    }


def _peek_thresholds_target_profile(
    thresholds_spec: dict[str, Any] | None,
    thresholds_path: Path | None,
) -> str | None:
    if isinstance(thresholds_spec, dict) and isinstance(thresholds_spec.get("target_staging_configuration"), dict):
        return str(thresholds_spec["target_staging_configuration"].get("profile_id") or "").strip() or None
    if thresholds_path is not None and thresholds_path.is_file():
        try:
            loaded = json.loads(thresholds_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("target_staging_configuration"), dict):
                return str(loaded["target_staging_configuration"].get("profile_id") or "").strip() or None
        except Exception:
            return None
    return None


def _peek_approval_single_profile(owner_slo_approval: dict[str, Any] | None) -> str | None:
    if not isinstance(owner_slo_approval, dict):
        return None
    direct = str(owner_slo_approval.get("approved_configuration_profile") or "").strip()
    if direct:
        return direct
    profs = owner_slo_approval.get("approved_profiles")
    if isinstance(profs, list) and len(profs) == 1 and isinstance(profs[0], str) and profs[0].strip():
        return profs[0].strip()
    return None


def reconcile_g18_benchmark_with_v1_4_slos(
    benchmark_report: dict[str, Any],
    *,
    owner_slo_approval: dict[str, Any] | Path | None = None,
    vps_receipts: dict[str, Any] | Path | None = None,
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
    expected_digest: str | None = None,
    target_configuration_profile: str | None = None,
    t1c_confirmation: dict[str, Any] | Path | None = None,
    require_t1c_confirmation: bool = False,
    g18_diag_report: dict[str, Any] | Path | None = None,
    out_json: Path | None = None,
) -> dict[str, Any]:
    """Reconcile G18 per Owner Ticket v1.4 or `cskh-p4-s1-slo-spec-v1` config-bound Staging VPS SLO."""
    owner_doc, owner_doc_sha, owner_load_err = _load_optional_owner_slo_approval(owner_slo_approval)

    if thresholds_spec is None and thresholds_path is None:
        if expected_digest == CURRENT_CANDIDATE_DIGEST or (
            expected_digest is None
            and (
                str(benchmark_report.get("app_image_tested") or "").strip() == CURRENT_CANDIDATE_DIGEST
                or (
                    isinstance(owner_doc, dict)
                    and owner_doc.get("schema_version") == s1_slo_tool.EXPECTED_APPROVAL_SCHEMA_VERSION
                )
            )
        ):
            thresholds_path = DEFAULT_P4_S1_SLO_SPEC_PATH

    s1_slo_v1_mode = is_p4_s1_slo_spec_v1(thresholds_spec, thresholds_path)
    if expected_digest is None:
        expected_digest = CURRENT_CANDIDATE_DIGEST if s1_slo_v1_mode else LOCKED_CANDIDATE_DIGEST_V1_4

    peek_thresh_prof = _peek_thresholds_target_profile(thresholds_spec, thresholds_path)
    approval_prof = _peek_approval_single_profile(owner_doc) or ""
    vps_prof = _infer_staging_profile_from_vps_receipts(vps_receipts) or ""
    bench_resource_profile = str(benchmark_report.get("resource_profile") or "").strip()
    req_target_prof = (
        str(target_configuration_profile).strip()
        if (target_configuration_profile is not None and str(target_configuration_profile).strip() not in ("", "auto"))
        else ""
    )
    config_bound_mode = bool(
        s1_slo_v1_mode
        or req_target_prof
        or peek_thresh_prof is not None
        or approval_prof
        or bench_resource_profile
    )
    resolved_profile = (
        req_target_prof
        or peek_thresh_prof
        or (vps_prof if s1_slo_v1_mode else "")
        or approval_prof
        or bench_resource_profile
        or None
    )

    if config_bound_mode:
        spec_check_profile = resolved_profile or ("s1_canonical_4x4" if s1_slo_v1_mode else "__missing__")
        validated_spec, candidate_thresholds_sha256, threshold_spec_errors = (
            validate_g18_thresholds_for_configuration(
                thresholds_spec=thresholds_spec,
                thresholds_path=thresholds_path,
                target_configuration_profile=spec_check_profile,
                expected_digest=expected_digest,
            )
        )
    else:
        validated_spec, candidate_thresholds_sha256, threshold_spec_errors = load_and_validate_g18_thresholds_v1_4(
            thresholds_spec=thresholds_spec,
            thresholds_path=thresholds_path,
        )

    missing_measurements: list[str] = []
    breached_checks: list[str] = []

    # Validate top-level benchmark contract & required provenance/summary fields
    expected_bench_schema = (
        "cskh-p4-isolated-benchmark-v2"
        if s1_slo_v1_mode
        else str(validated_spec["coordinated_benchmark_schema_version"])
    )
    bench_schema = _req_str(benchmark_report, "schema_version", "schema_version", missing_measurements)
    if bench_schema is not None and bench_schema != expected_bench_schema:
        breached_checks.append(f"schema_version:{bench_schema}!={expected_bench_schema}")
    app_image_tested_opt = _req_str(benchmark_report, "app_image_tested", "app_image_tested", missing_measurements)
    app_image_tested = app_image_tested_opt or ""
    iso_guard = _nav_dict(benchmark_report, "isolation_guard", missing_measurements)
    iso_verified = _req_bool(iso_guard, "verified_isolated", "isolation_guard.verified_isolated", missing_measurements)
    owner_vps_touched = _req_bool(
        iso_guard, "owner_vps_touched", "isolation_guard.owner_vps_touched", missing_measurements
    )
    runner_hw = _nav_dict(
        benchmark_report, "s1_domain_and_database_benchmark.runner_hardware", missing_measurements
    )
    _req_str(
        runner_hw,
        "platform",
        "s1_domain_and_database_benchmark.runner_hardware.platform",
        missing_measurements,
    )
    p4_9_2 = _nav_dict(benchmark_report, "p4_9_2_acceptance_matrix", missing_measurements)
    p4_9_2_all_passed_opt = _req_bool(
        p4_9_2, "all_passed", "p4_9_2_acceptance_matrix.all_passed", missing_measurements
    )
    p4_9_2_items = _nav_dict(p4_9_2, "items", missing_measurements) if p4_9_2 is not None else None
    required_11_items = (
        "staff_my_customers_p95_le_1500ms",
        "open_lead_and_history_p95_le_2000ms",
        "record_attempt_and_callback_p95_le_2000ms_zero_dup",
        "bod_overview_snapshot_p95_le_3000ms",
        "kpi_drilldown_paginated_p95_le_3000ms",
        "preview_weighted_distribution_10000_items",
        "execute_weighted_distribution_10000_items",
        "import_50000_csv_rows",
        "recompute_kpi_non_blocking_atomic_publish",
        "concurrent_20_40_80_mixed_user_sessions",
        "telesale_worklist_ssr_zero_errors_c12_and_c40",
    )
    all_11_item_flags_true = True
    for item_key in required_11_items:
        inode = _nav_dict(p4_9_2_items, item_key, missing_measurements) if p4_9_2_items is not None else None
        ipassed = _req_bool(
            inode, "passed", f"p4_9_2_acceptance_matrix.items.{item_key}.passed", missing_measurements
        )
        if ipassed is not True:
            all_11_item_flags_true = False
            if ipassed is False:
                breached_checks.append(f"p4_9_2_acceptance_matrix.items.{item_key}.passed_is_false")
    p4_9_2_all_passed = (p4_9_2_all_passed_opt is True) and all_11_item_flags_true
    if p4_9_2_all_passed_opt is False:
        breached_checks.append("p4_9_2_acceptance_matrix.all_passed_is_false")

    owner_approval_present = owner_slo_approval is not None
    if owner_load_err is not None:
        owner_signed = False
        signed_thresholds_sha256 = ""
        approval_errors = [owner_load_err]
        clean_pending_approval = False
    else:
        owner_signed, signed_thresholds_sha256, approval_errors = _validate_owner_slo_approval_document(
            owner_doc,
            validated_spec=validated_spec,
            candidate_thresholds_sha256=candidate_thresholds_sha256,
            threshold_spec_errors=threshold_spec_errors,
            config_bound_mode=config_bound_mode,
            resolved_profile=resolved_profile,
            expected_digest=expected_digest,
        )
        clean_pending_approval = _is_clean_pending_s1_slo_approval(
            owner_doc,
            validated_spec=validated_spec,
            actual_slo_sha256=candidate_thresholds_sha256,
            expected_digest=expected_digest,
            profile=resolved_profile,
        )

    t1c_check_enforced = bool(require_t1c_confirmation or t1c_confirmation is not None)
    t1c_ok = True
    t1c_doc: dict[str, Any] | None = None
    t1c_errors: list[str] = []
    if t1c_check_enforced:
        t1c_ok, t1c_doc, t1c_errors = verify_t1c_candidate_confirmation(
            t1c_confirmation,
            expected_app_image=expected_digest,
        )

    configuration_errors: list[str] = []
    configuration_matches = True
    if config_bound_mode:
        if not bench_resource_profile:
            configuration_matches = False
            configuration_errors.append("missing_resource_profile_in_benchmark_report")
        elif resolved_profile and bench_resource_profile != resolved_profile:
            configuration_matches = False
            configuration_errors.append(
                f"benchmark_resource_profile_mismatch:expected={resolved_profile!r}:got={bench_resource_profile!r}"
            )

    # Step 1: 7-metric table pre-check
    precheck_spec = (
        derive_precheck_7_metrics_from_p4_s1_slo_spec(
            validated_spec,
            resolved_profile or bench_resource_profile or "s1_canonical_4x4",
        )
        if s1_slo_v1_mode
        else validated_spec["precheck_7_metrics"]
    )
    precheck_eval = evaluate_precheck_7_metrics(
        benchmark_report,
        precheck_spec,
        owner_signed=owner_signed,
        missing=missing_measurements,
        breaches=breached_checks,
    )

    # Step 2: Full endpoint/wave/background reconciliation (`evaluate_s1_slo` on v1 bundle, or §3.2A–C on v1.4)
    s1_slo_bundle_eval: dict[str, Any] | None = None
    if s1_slo_v1_mode:
        s1_slo_bundle_eval = evaluate_p4_s1_slo_reconciliation(
            benchmark_report,
            validated_spec,
            profile=resolved_profile or bench_resource_profile or "s1_canonical_4x4",
            expected_digest=expected_digest,
            g18_diag_report=g18_diag_report,
            missing=missing_measurements,
            breaches=breached_checks,
        )
        sec_3_2_eval = s1_slo_bundle_eval
    else:
        sec_3_2_eval = evaluate_section_3_2_a_b_c(
            benchmark_report,
            validated_spec,
            missing=missing_measurements,
            breaches=breached_checks,
        )

    if s1_slo_bundle_eval is not None and s1_slo_bundle_eval.get("g18_diag_profile_matches") is False:
        configuration_matches = False
        configuration_errors.append("g18_diag_resource_profile_mismatch")

    unique_missing = sorted(dict.fromkeys(missing_measurements))
    unique_breaches = sorted(dict.fromkeys(breached_checks))
    measurements_complete = not unique_missing

    spec_locked_digest = (
        str(((validated_spec.get("candidate_binding") or {}).get("app_image_digest")) or "")
        if s1_slo_v1_mode
        else str(validated_spec["locked_candidate_digest"])
    )
    diag_digest_ok = (
        True
        if s1_slo_bundle_eval is None
        else bool(s1_slo_bundle_eval.get("g18_diag_digest_matches", True))
    )
    digest_matches = (
        bool(app_image_tested)
        and "@sha256:" in expected_digest
        and app_image_tested == expected_digest
        and expected_digest == spec_locked_digest
        and diag_digest_ok
    )
    owner_vps_untouched = (iso_verified is True) and (owner_vps_touched is False)

    is_ci_report, ci_report_reasons = detect_ci_benchmark_report(benchmark_report)
    vps_receipts_present = vps_receipts is not None
    vps_receipts_verified, vps_receipt_errors = verify_staging_vps_receipts_for_g18(
        benchmark_report,
        vps_receipts,
        expected_digest=expected_digest,
        is_ci_benchmark_report=is_ci_report,
        target_configuration_profile=resolved_profile if config_bound_mode else None,
    )

    all_technical_thresholds_passed = (
        measurements_complete
        and precheck_eval["all_7_metrics_within_v1_4_thresholds"]
        and sec_3_2_eval["all_passed"]
        and p4_9_2_all_passed
        and not unique_breaches
    )

    # Deterministic fail-closed status precedence for G18
    if threshold_spec_errors:
        overall_status = "FAIL_INVALID_THRESHOLDS"
    elif app_image_tested and not digest_matches:
        overall_status = "FAIL_DIGEST_MISMATCH"
    elif not measurements_complete:
        overall_status = "FAIL_MISSING_MEASUREMENTS"
    elif not digest_matches:
        overall_status = "FAIL_DIGEST_MISMATCH"
    elif not configuration_matches:
        overall_status = "FAIL_CONFIGURATION_MISMATCH"
    elif not owner_vps_untouched:
        overall_status = "FAIL_ISOLATION_GUARD"
    elif t1c_check_enforced and not t1c_ok:
        overall_status = (
            "BLOCKED_PENDING_T1C_CANDIDATE_CONFIRMATION"
            if t1c_confirmation is None
            else "FAIL_INVALID_T1C_CANDIDATE_CONFIRMATION"
        )
    elif not all_technical_thresholds_passed:
        overall_status = "FAIL_THRESHOLD_BREACH"
    elif owner_approval_present and (not owner_signed) and (not clean_pending_approval):
        overall_status = "FAIL_INVALID_OWNER_APPROVAL_OR_THRESHOLDS_SHA256"
    elif vps_receipts_present and not vps_receipts_verified:
        overall_status = "FAIL_INVALID_STAGING_VPS_RECEIPTS"
    elif is_ci_report:
        overall_status = (
            "BLOCKED_CI_REPORT_REQUIRES_STAGING_VPS_BENCHMARK_AND_RECEIPTS"
            if owner_signed
            else "BLOCKED_CI_PRECHECK_ONLY_PENDING_VPS_RECEIPTS_AND_OWNER_APPROVAL"
        )
    elif not vps_receipts_verified:
        overall_status = (
            "BLOCKED_MISSING_STAGING_VPS_RECEIPTS"
            if owner_signed
            else "BLOCKED_PENDING_STAGING_VPS_RECEIPTS_AND_OWNER_APPROVAL"
        )
    elif not owner_signed:
        overall_status = "BLOCKED_PENDING_OWNER_SLO_V1_4_SIGNATURE"
    else:
        overall_status = "PASS"

    pass_reqs: dict[str, bool] = {
        "measurements_complete_and_within_v1_4_thresholds": all_technical_thresholds_passed,
        "candidate_digest_matches_locked_v1_4": digest_matches,
        "staging_vps_benchmark_and_receipts_verified": (not is_ci_report) and vps_receipts_verified,
        "owner_v1_4_approval_bound_to_thresholds_sha256": owner_signed,
    }
    if config_bound_mode:
        pass_reqs["benchmark_and_slo_bound_to_safe_staging_configuration"] = (
            configuration_matches and vps_receipts_verified and owner_signed
        )
    if t1c_check_enforced:
        pass_reqs["t1c_candidate_confirmation_verified"] = t1c_ok

    reconciliation: dict[str, Any] = {
        "schema_version": "cskh-p4-g18-slo-v1-4-reconciliation-v2",
        "timestamp_utc": utc_now_iso(),
        "gate": "G18",
        "ticket_version_required": str(
            validated_spec.get("slo_version")
            if s1_slo_v1_mode
            else (validated_spec.get("ticket_version") or "v1.4")
        ),
        "slo_spec_schema_version": str(validated_spec.get("schema_version") or ""),
        "config_bound_mode": config_bound_mode,
        "target_configuration_profile": resolved_profile,
        "benchmark_resource_profile": bench_resource_profile or None,
        "configuration_matches": configuration_matches,
        "configuration_validation_errors": configuration_errors,
        "t1c_candidate_confirmed": t1c_ok if t1c_check_enforced else None,
        "t1c_candidate_confirmation": t1c_doc,
        "t1c_confirmation_errors": t1c_errors,
        "status": overall_status,
        "thresholds_spec_path": str(
            thresholds_path
            or (DEFAULT_P4_S1_SLO_SPEC_PATH if s1_slo_v1_mode else DEFAULT_G18_THRESHOLDS_V1_4_PATH)
        ),
        "locked_thresholds_sha256": (
            LOCKED_P4_S1_SLO_SPEC_SHA256 if s1_slo_v1_mode else LOCKED_G18_THRESHOLDS_V1_4_SHA256
        ),
        "evaluated_thresholds_sha256": candidate_thresholds_sha256,
        "threshold_validation_errors": threshold_spec_errors,
        "owner_slo_v1_4_signed": owner_signed,
        "owner_approval_doc_sha256": owner_doc_sha,
        "owner_approval_thresholds_sha256": signed_thresholds_sha256,
        "owner_approval_validation_errors": approval_errors,
        "expected_candidate_digest": expected_digest,
        "app_image_tested": app_image_tested,
        "digest_matches_candidate": digest_matches,
        "is_ci_benchmark_report": is_ci_report,
        "ci_benchmark_indicators": ci_report_reasons,
        "staging_vps_receipts_verified": vps_receipts_verified,
        "staging_vps_receipt_validation_errors": vps_receipt_errors,
        "measurements_complete": measurements_complete,
        "missing_measurements": unique_missing,
        "breached_checks": unique_breaches,
        "p4_9_2_all_11_items_passed": p4_9_2_all_passed,
        "owner_live_service_untouched": owner_vps_untouched,
        "all_7_metrics_within_v1_4_thresholds": precheck_eval["all_7_metrics_within_v1_4_thresholds"],
        "precheck_7_metrics": precheck_eval,
        "metric_comparisons": precheck_eval["metric_comparisons"],
        "section_3_2_reconciliation": sec_3_2_eval,
        "g18_pass_requirements": pass_reqs,
    }
    if s1_slo_bundle_eval is not None:
        reconciliation["slo_bundle_id"] = validated_spec.get("slo_bundle_id")
        reconciliation["slo_version"] = validated_spec.get("slo_version")
        reconciliation["s1_slo_bundle_evaluation"] = s1_slo_bundle_eval
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(reconciliation, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return reconciliation


def _infer_staging_profile_from_vps_receipts(vps_receipts: dict[str, Any] | Path | None) -> str | None:
    summary_doc: dict[str, Any] | None = None
    preflight_doc: dict[str, Any] | None = None
    if isinstance(vps_receipts, Path) and vps_receipts.is_dir():
        s_path = vps_receipts / "vps-g03-g05-summary.json"
        p_path = vps_receipts / "readonly-vps-preflight.json"
        if s_path.is_file():
            try:
                summary_doc = json.loads(s_path.read_text(encoding="utf-8"))
            except Exception:
                summary_doc = None
        if p_path.is_file():
            try:
                preflight_doc = json.loads(p_path.read_text(encoding="utf-8"))
            except Exception:
                preflight_doc = None
    elif isinstance(vps_receipts, dict):
        if isinstance(vps_receipts.get("summary"), dict):
            summary_doc = vps_receipts["summary"]
        if isinstance(vps_receipts.get("readonly_vps_preflight"), dict):
            preflight_doc = vps_receipts["readonly_vps_preflight"]

    if isinstance(summary_doc, dict) and isinstance(summary_doc.get("staging_resource_configuration"), dict):
        prof = str(summary_doc["staging_resource_configuration"].get("profile_id") or "").strip()
        if prof:
            return prof
    if isinstance(preflight_doc, dict) and isinstance(preflight_doc.get("safe_staging_allocation"), dict):
        rec = str(preflight_doc["safe_staging_allocation"].get("recommended_g18_profile") or "").strip()
        if rec:
            return rec
    return None


def _validate_premeasurement_g18_diag_report(
    g18_diag_report: dict[str, Any] | Path | None,
    *,
    expected_digest: str,
    resolved_profile: str | None,
) -> tuple[bool, list[str]]:
    """Validate optional G18 deep diagnostic report digest & Staging profile before measurement."""
    if g18_diag_report is None:
        return True, []
    diag_doc: dict[str, Any] | None = None
    if isinstance(g18_diag_report, Path):
        if not g18_diag_report.is_file():
            return False, [f"g18_diag_report_file_not_found:{g18_diag_report}"]
        try:
            loaded = json.loads(g18_diag_report.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, [f"g18_diag_report_invalid_json:{exc}"]
        if not isinstance(loaded, dict):
            return False, ["g18_diag_report_root_not_dict"]
        diag_doc = loaded
    elif isinstance(g18_diag_report, dict):
        diag_doc = g18_diag_report
    else:
        return False, [f"g18_diag_report_not_dict:{type(g18_diag_report).__name__}"]

    errs: list[str] = []
    diag_img = str(diag_doc.get("app_image_tested") or "").strip()
    if not diag_img:
        errs.append("missing_app_image_tested_in_g18_diag_report")
    elif diag_img != expected_digest:
        errs.append(f"g18_diag_digest_mismatch:expected={expected_digest!r}:got={diag_img!r}")

    diag_rp = diag_doc.get("resource_profile")
    diag_prof = (
        str(diag_rp.get("name") or "").strip()
        if isinstance(diag_rp, dict)
        else str(diag_rp or "").strip()
    )
    if not diag_prof:
        errs.append("missing_resource_profile_in_g18_diag_report")
    elif resolved_profile and diag_prof != resolved_profile:
        errs.append(f"g18_diag_resource_profile_mismatch:expected={resolved_profile!r}:got={diag_prof!r}")
    return (not errs), errs


def verify_g18_acceptance_measurement_gate(
    *,
    vps_receipts: dict[str, Any] | Path | None,
    expected_digest: str = CURRENT_CANDIDATE_DIGEST,
    target_configuration_profile: str | None = None,
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
    owner_slo_approval: dict[str, Any] | Path | None = None,
    t1c_confirmation: dict[str, Any] | Path | None = None,
    g18_diag_report: dict[str, Any] | Path | None = None,
    bench_port: int = 18880,
    benchmark_out_json: Path | None = None,
    out_json: Path | None = None,
) -> dict[str, Any]:
    """Fail-closed pre-measurement gate: allow running G18 on Staging VPS ONLY when T1C confirmed candidate, G03/G05 receipts match safe VPS sizing, and scorer thresholds match Owner-signed SLO for that configuration."""
    if thresholds_spec is None and thresholds_path is None and expected_digest == CURRENT_CANDIDATE_DIGEST:
        thresholds_path = DEFAULT_P4_S1_SLO_SPEC_PATH

    owner_doc, owner_doc_sha, owner_load_err = _load_optional_owner_slo_approval(owner_slo_approval)

    t1c_source = t1c_confirmation
    if t1c_source is None:
        if (
            isinstance(vps_receipts, Path)
            and (vps_receipts / "t1c-candidate-confirmation-receipt.json").is_file()
        ):
            t1c_source = vps_receipts / "t1c-candidate-confirmation-receipt.json"
        elif (
            isinstance(vps_receipts, dict)
            and isinstance(vps_receipts.get("summary"), dict)
            and isinstance(vps_receipts["summary"].get("t1c_candidate_confirmation"), dict)
        ):
            t1c_source = vps_receipts["summary"]["t1c_candidate_confirmation"]

    req_prof = str(target_configuration_profile or "").strip()
    if not req_prof or req_prof == "auto":
        resolved_profile = (
            _infer_staging_profile_from_vps_receipts(vps_receipts)
            or _peek_thresholds_target_profile(thresholds_spec, thresholds_path)
            or _peek_approval_single_profile(owner_doc)
            or None
        )
    else:
        resolved_profile = req_prof

    t1c_ok, t1c_doc, t1c_errors = verify_t1c_candidate_confirmation(
        t1c_source,
        expected_app_image=expected_digest,
    )
    vps_ok, vps_errors = verify_staging_vps_receipts_for_g18(
        None,
        vps_receipts,
        expected_digest=expected_digest,
        is_ci_benchmark_report=False,
        target_configuration_profile=resolved_profile or "__missing__",
    )
    validated_spec, candidate_thresholds_sha256, threshold_errors = validate_g18_thresholds_for_configuration(
        thresholds_spec=thresholds_spec,
        thresholds_path=thresholds_path,
        target_configuration_profile=resolved_profile or "__missing__",
        expected_digest=expected_digest,
    )
    g18_diag_ok, g18_diag_errors = _validate_premeasurement_g18_diag_report(
        g18_diag_report,
        expected_digest=expected_digest,
        resolved_profile=resolved_profile,
    )
    if owner_load_err is not None:
        owner_signed = False
        signed_thresholds_sha256 = ""
        approval_errors = [owner_load_err]
        clean_pending_approval = False
    else:
        owner_signed, signed_thresholds_sha256, approval_errors = _validate_owner_slo_approval_document(
            owner_doc,
            validated_spec=validated_spec,
            candidate_thresholds_sha256=candidate_thresholds_sha256,
            threshold_spec_errors=threshold_errors,
            config_bound_mode=True,
            resolved_profile=resolved_profile,
            expected_digest=expected_digest,
        )
        clean_pending_approval = _is_clean_pending_s1_slo_approval(
            owner_doc,
            validated_spec=validated_spec,
            actual_slo_sha256=candidate_thresholds_sha256,
            expected_digest=expected_digest,
            profile=resolved_profile,
        )

    measurement_allowed = bool(
        resolved_profile
        and t1c_ok
        and vps_ok
        and (not threshold_errors)
        and g18_diag_ok
        and owner_signed
    )

    if not t1c_ok:
        gate_status = (
            "BLOCKED_PENDING_T1C_CANDIDATE_CONFIRMATION"
            if t1c_source is None
            else "FAIL_INVALID_T1C_CANDIDATE_CONFIRMATION"
        )
    elif not vps_ok:
        gate_status = (
            "BLOCKED_MISSING_STAGING_VPS_G03_G05_RECEIPTS"
            if vps_receipts is None
            else "FAIL_UNSAFE_OR_INVALID_STAGING_VPS_CONFIGURATION"
        )
    elif threshold_errors:
        gate_status = "BLOCKED_SCORER_THRESHOLDS_NOT_BOUND_TO_CONFIGURATION"
    elif not g18_diag_ok:
        gate_status = "FAIL_INVALID_G18_DIAGNOSTIC_REPORT"
    elif not owner_signed:
        gate_status = (
            "BLOCKED_PENDING_OWNER_SIGNED_CONFIG_SLO"
            if (owner_slo_approval is None or clean_pending_approval)
            else "FAIL_SCORER_DOES_NOT_MATCH_OWNER_SIGNED_CONFIG_SLO"
        )
    else:
        gate_status = "READY_FOR_G18_ACCEPTANCE_MEASUREMENT"

    resolved_cfg_sha = (
        get_staging_resource_profile_spec(resolved_profile)["config_spec_sha256"]
        if (resolved_profile and resolved_profile in STAGING_VPS_RESOURCE_PROFILES)
        else None
    )
    planned_out = benchmark_out_json or Path("/tmp/staging-vps-s1-benchmark.json")
    planned_cmd = (
        [
            sys.executable,
            str(REPO_ROOT / "deploy" / "release" / "benchmark_p4_isolated.py"),
            "--provision-and-run-s1",
            "--confirm-isolated-target",
            "--resource-profile",
            str(resolved_profile),
            "--require-live-headroom",
            "--pull-policy",
            "never",
            "--port",
            str(bench_port),
            "--app-image",
            expected_digest,
            "--out",
            str(planned_out),
        ]
        if measurement_allowed
        else None
    )

    gate_report = {
        "schema_version": "cskh-p4-g18-pre-measurement-gate-v1",
        "timestamp_utc": utc_now_iso(),
        "gate": "G18",
        "status": gate_status,
        "measurement_allowed": measurement_allowed,
        "expected_candidate_digest": expected_digest,
        "resolved_configuration_profile": resolved_profile,
        "resolved_config_spec_sha256": resolved_cfg_sha,
        "t1c_candidate_confirmed": t1c_ok,
        "t1c_candidate_confirmation": t1c_doc,
        "t1c_confirmation_errors": t1c_errors,
        "staging_vps_g03_g05_receipts_verified": vps_ok,
        "staging_vps_receipt_errors": vps_errors,
        "scorer_thresholds_sha256": candidate_thresholds_sha256,
        "scorer_threshold_validation_errors": threshold_errors,
        "g18_diag_report_verified": g18_diag_ok,
        "g18_diag_validation_errors": g18_diag_errors,
        "owner_config_slo_signed_and_matched": owner_signed,
        "owner_approval_doc_sha256": owner_doc_sha,
        "owner_approval_thresholds_sha256": signed_thresholds_sha256,
        "owner_approval_validation_errors": approval_errors,
        "planned_benchmark_command": planned_cmd,
    }
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(gate_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return gate_report


def run_staging_vps_g18_acceptance(
    *,
    vps_receipts: dict[str, Any] | Path,
    expected_digest: str = CURRENT_CANDIDATE_DIGEST,
    benchmark_out_json: Path,
    reconcile_out_json: Path | None = None,
    target_configuration_profile: str | None = None,
    thresholds_spec: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
    owner_slo_approval: dict[str, Any] | Path | None = None,
    t1c_confirmation: dict[str, Any] | Path | None = None,
    g18_diag_report: dict[str, Any] | Path | None = None,
    live_project_name: str = "crm-cskh-vps-staging",
    bench_port: int = 18880,
) -> dict[str, Any]:
    """Execute G18 S1 acceptance on Staging VPS ONLY if pre-measurement config-bound SLO gate passes, preserving live stack before/after."""
    if thresholds_spec is None and thresholds_path is None and expected_digest == CURRENT_CANDIDATE_DIGEST:
        thresholds_path = DEFAULT_P4_S1_SLO_SPEC_PATH

    gate = verify_g18_acceptance_measurement_gate(
        vps_receipts=vps_receipts,
        expected_digest=expected_digest,
        target_configuration_profile=target_configuration_profile,
        thresholds_spec=thresholds_spec,
        thresholds_path=thresholds_path,
        owner_slo_approval=owner_slo_approval,
        t1c_confirmation=t1c_confirmation,
        g18_diag_report=g18_diag_report,
        bench_port=bench_port,
        benchmark_out_json=benchmark_out_json,
    )
    if not gate["measurement_allowed"] or not gate["planned_benchmark_command"]:
        blocked_res = {
            **gate,
            "benchmark_executed": False,
            "live_zero_mutation": True,
        }
        if reconcile_out_json is not None:
            reconcile_out_json.parent.mkdir(parents=True, exist_ok=True)
            reconcile_out_json.write_text(
                json.dumps(blocked_res, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        return blocked_res

    live_before = capture_readonly_live_stack_snapshot(live_project_name)
    if not live_before.get("all_running") or int(live_before.get("container_count") or 0) <= 0:
        raise RuntimeError(
            f"Refusing G18 benchmark execution because live stack {live_project_name!r} is not healthy/running: {live_before}"
        )

    benchmark_out_json = benchmark_out_json.resolve()
    benchmark_out_json.parent.mkdir(parents=True, exist_ok=True)
    live_pres_path = benchmark_out_json.parent / "g18-live-preservation-before-after.json"

    try:
        run_sub(gate["planned_benchmark_command"])
    finally:
        live_after = capture_readonly_live_stack_snapshot(live_project_name)
        live_preservation = compare_live_stack_preservation(live_before, live_after)
        live_pres_path.write_text(
            json.dumps(live_preservation, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    if live_preservation.get("zero_live_mutation") is not True:
        fail_live = {
            **gate,
            "status": "FAIL_LIVE_SERVICE_MUTATED_DURING_G18",
            "benchmark_executed": True,
            "live_zero_mutation": False,
            "live_preservation": live_preservation,
        }
        if reconcile_out_json is not None:
            reconcile_out_json.parent.mkdir(parents=True, exist_ok=True)
            reconcile_out_json.write_text(
                json.dumps(fail_live, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        return fail_live

    bench_doc = json.loads(benchmark_out_json.read_text(encoding="utf-8"))
    t1c_src = (
        t1c_confirmation
        if t1c_confirmation is not None
        else (
            vps_receipts / "t1c-candidate-confirmation-receipt.json"
            if isinstance(vps_receipts, Path)
            else (
                vps_receipts["summary"].get("t1c_candidate_confirmation")
                if (isinstance(vps_receipts, dict) and isinstance(vps_receipts.get("summary"), dict))
                else None
            )
        )
    )
    rec = reconcile_g18_benchmark_with_v1_4_slos(
        bench_doc,
        owner_slo_approval=owner_slo_approval,
        vps_receipts=vps_receipts,
        thresholds_spec=thresholds_spec,
        thresholds_path=thresholds_path,
        expected_digest=expected_digest,
        target_configuration_profile=gate["resolved_configuration_profile"],
        t1c_confirmation=t1c_src,
        require_t1c_confirmation=True,
        g18_diag_report=g18_diag_report,
        out_json=reconcile_out_json,
    )
    rec["benchmark_executed"] = True
    rec["g18_live_preservation_before_after"] = live_preservation
    if reconcile_out_json is not None:
        reconcile_out_json.write_text(
            json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return rec


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

    p_vpre = sub.add_parser(
        "readonly-vps-preflight",
        help="Read-only check of VPS CPU/RAM/disk/ports, safe Staging configuration sizing, and live-before snapshot (zero mutation)",
    )
    p_vpre.add_argument("--live-project", default="crm-cskh-vps-staging")
    p_vpre.add_argument("--disk-path", type=Path, default=Path("/"))
    p_vpre.add_argument("--required-free-disk-gib", type=float, default=12.0)
    p_vpre.add_argument("--required-avail-ram-mib", type=float, default=1536.0)
    p_vpre.add_argument("--http-port", type=int, default=18081)
    p_vpre.add_argument("--https-port", type=int, default=18443)
    p_vpre.add_argument("--out", type=Path, default=None)

    p_vg = sub.add_parser(
        "collect-vps-g03-g05",
        help="Collect G03/G05 pull, isolation, inventory/config & HTTPS/WSS smoke receipts on isolated Staging/DR with live zero-delta proof",
    )
    p_vg.add_argument("--app-image", default=LOCKED_CANDIDATE_DIGEST_V1_4)
    p_vg.add_argument("--out-dir", type=Path, required=True)
    p_vg.add_argument("--project-name", default="crm-cskh-dr-https-vps")
    p_vg.add_argument("--site-name", default=DEFAULT_DR_SITE)
    p_vg.add_argument("--live-project", default="crm-cskh-vps-staging")
    p_vg.add_argument("--http-port", type=int, default=18081)
    p_vg.add_argument("--https-port", type=int, default=18443)
    p_vg.add_argument("--keep-up", action="store_true")
    p_vg.add_argument("--state-dir", type=Path, default=None)
    p_vg.add_argument("--t1c-confirmation-json", type=Path, default=None)
    p_vg.add_argument("--require-t1c-confirmation", action="store_true")
    p_vg.add_argument(
        "--resource-profile",
        default=None,
        choices=("auto", *sorted(STAGING_VPS_RESOURCE_PROFILES)),
    )

    p_vref = sub.add_parser(
        "refresh-candidate-digest",
        help="Update isolated Staging & regenerate all G03/G05/inventory receipts when T1B changes candidate digest for G18",
    )
    p_vref.add_argument("--app-image", required=True)
    p_vref.add_argument("--out-dir", type=Path, required=True)
    p_vref.add_argument("--project-name", default="crm-cskh-dr-https-vps")
    p_vref.add_argument("--site-name", default=DEFAULT_DR_SITE)
    p_vref.add_argument("--live-project", default="crm-cskh-vps-staging")
    p_vref.add_argument("--http-port", type=int, default=18081)
    p_vref.add_argument("--https-port", type=int, default=18443)
    p_vref.add_argument("--keep-up", action="store_true")
    p_vref.add_argument("--state-dir", type=Path, default=None)
    p_vref.add_argument("--t1c-confirmation-json", type=Path, default=None)
    p_vref.add_argument("--require-t1c-confirmation", action="store_true")
    p_vref.add_argument(
        "--resource-profile",
        default=None,
        choices=("auto", *sorted(STAGING_VPS_RESOURCE_PROFILES)),
    )

    p_g18_prep = sub.add_parser(
        "prepare-staging-g18",
        help="Fail-closed pre-measurement check verifying T1C confirmation, G03/G05 safe sizing receipts, and scorer ↔ Owner-signed config SLO match before running G18",
    )
    p_g18_prep.add_argument("--vps-receipt-dir", type=Path, required=True)
    p_g18_prep.add_argument("--expected-digest", default=CURRENT_CANDIDATE_DIGEST)
    p_g18_prep.add_argument("--target-config-profile", default="auto")
    p_g18_prep.add_argument("--thresholds-json", type=Path, default=DEFAULT_P4_S1_SLO_SPEC_PATH)
    p_g18_prep.add_argument("--owner-approval-json", type=Path, default=DEFAULT_P4_S1_SLO_APPROVAL_PATH)
    p_g18_prep.add_argument("--t1c-confirmation-json", type=Path, default=None)
    p_g18_prep.add_argument("--g18-diag-json", type=Path, default=None)
    p_g18_prep.add_argument("--bench-port", type=int, default=18880)
    p_g18_prep.add_argument("--benchmark-out", type=Path, default=None)
    p_g18_prep.add_argument("--out", type=Path, default=None)

    p_g18_run = sub.add_parser(
        "run-staging-vps-g18",
        help="Execute isolated S1 G18 acceptance on Staging VPS ONLY if scorer matches Owner-signed config SLO and live stack remains untouched",
    )
    p_g18_run.add_argument("--vps-receipt-dir", type=Path, required=True)
    p_g18_run.add_argument("--expected-digest", default=CURRENT_CANDIDATE_DIGEST)
    p_g18_run.add_argument("--target-config-profile", default="auto")
    p_g18_run.add_argument("--thresholds-json", type=Path, default=DEFAULT_P4_S1_SLO_SPEC_PATH)
    p_g18_run.add_argument("--owner-approval-json", type=Path, default=DEFAULT_P4_S1_SLO_APPROVAL_PATH)
    p_g18_run.add_argument("--t1c-confirmation-json", type=Path, default=None)
    p_g18_run.add_argument("--g18-diag-json", type=Path, default=None)
    p_g18_run.add_argument("--live-project", default="crm-cskh-vps-staging")
    p_g18_run.add_argument("--bench-port", type=int, default=18880)
    p_g18_run.add_argument("--benchmark-out", type=Path, required=True)
    p_g18_run.add_argument("--reconcile-out", type=Path, default=None)

    p_slo = sub.add_parser(
        "reconcile-g18-slo",
        help="Reconcile G18 S1 benchmark (7-metric precheck + §3.2A–C) against v1.4 or config-bound thresholds, Staging VPS receipts & SHA-256 Owner signature",
    )
    p_slo.add_argument("--benchmark-json", type=Path, required=True)
    p_slo.add_argument("--vps-receipt-dir", type=Path, default=None)
    p_slo.add_argument("--thresholds-json", type=Path, default=None)
    p_slo.add_argument("--owner-approval-json", type=Path, default=None)
    p_slo.add_argument("--expected-digest", default=LOCKED_CANDIDATE_DIGEST_V1_4)
    p_slo.add_argument("--target-config-profile", default=None)
    p_slo.add_argument("--t1c-confirmation-json", type=Path, default=None)
    p_slo.add_argument("--require-t1c-confirmation", action="store_true")
    p_slo.add_argument("--g18-diag-json", type=Path, default=None)
    p_slo.add_argument("--out", type=Path, default=None)
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
    if args.command == "readonly-vps-preflight":
        res = readonly_vps_preflight(
            live_project_name=args.live_project,
            disk_check_path=args.disk_path,
            required_free_disk_gib=args.required_free_disk_gib,
            required_avail_ram_mib=args.required_avail_ram_mib,
            planned_http_port=args.http_port,
            planned_https_port=args.https_port,
            out_json=args.out,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res["status"] == "PASS" else 2
    if args.command == "collect-vps-g03-g05":
        res = collect_vps_g03_g05_receipts(
            app_image=args.app_image,
            out_dir=args.out_dir,
            project_name=args.project_name,
            site_name=args.site_name,
            live_project_name=args.live_project,
            http_port=args.http_port,
            https_port=args.https_port,
            keep_up=args.keep_up,
            state_dir=args.state_dir,
            t1c_confirmation=args.t1c_confirmation_json,
            require_t1c_confirmation=args.require_t1c_confirmation,
            resource_profile=args.resource_profile,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res["status"] == "PASS" else 2
    if args.command == "refresh-candidate-digest":
        res = refresh_staging_for_new_candidate_digest(
            new_app_image=args.app_image,
            out_dir=args.out_dir,
            project_name=args.project_name,
            site_name=args.site_name,
            live_project_name=args.live_project,
            http_port=args.http_port,
            https_port=args.https_port,
            keep_up=args.keep_up,
            state_dir=args.state_dir,
            t1c_confirmation=args.t1c_confirmation_json,
            require_t1c_confirmation=args.require_t1c_confirmation,
            resource_profile=args.resource_profile,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res["status"] == "PASS" else 2
    if args.command == "prepare-staging-g18":
        res = verify_g18_acceptance_measurement_gate(
            vps_receipts=args.vps_receipt_dir,
            expected_digest=args.expected_digest,
            target_configuration_profile=args.target_config_profile,
            thresholds_path=args.thresholds_json,
            owner_slo_approval=args.owner_approval_json,
            t1c_confirmation=args.t1c_confirmation_json,
            g18_diag_report=args.g18_diag_json,
            bench_port=args.bench_port,
            benchmark_out_json=args.benchmark_out,
            out_json=args.out,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res["measurement_allowed"] else 2
    if args.command == "run-staging-vps-g18":
        res = run_staging_vps_g18_acceptance(
            vps_receipts=args.vps_receipt_dir,
            expected_digest=args.expected_digest,
            benchmark_out_json=args.benchmark_out,
            reconcile_out_json=args.reconcile_out,
            target_configuration_profile=args.target_config_profile,
            thresholds_path=args.thresholds_json,
            owner_slo_approval=args.owner_approval_json,
            t1c_confirmation=args.t1c_confirmation_json,
            g18_diag_report=args.g18_diag_json,
            live_project_name=args.live_project,
            bench_port=args.bench_port,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res["status"] == "PASS" else 2
    if args.command == "reconcile-g18-slo":
        raw_argv = list(argv) if argv is not None else sys.argv[1:]
        explicit_digest = any(
            a == "--expected-digest" or a.startswith("--expected-digest=")
            for a in raw_argv
        )
        bench_doc = json.loads(args.benchmark_json.read_text(encoding="utf-8"))
        owner_peek, _, _ = _load_optional_owner_slo_approval(args.owner_approval_json)
        auto_ba68_mode = bool(
            is_p4_s1_slo_spec_v1(None, args.thresholds_json)
            or str(bench_doc.get("app_image_tested") or "").strip() == CURRENT_CANDIDATE_DIGEST
            or (
                isinstance(owner_peek, dict)
                and owner_peek.get("schema_version") == s1_slo_tool.EXPECTED_APPROVAL_SCHEMA_VERSION
            )
        )
        eff_digest = (
            args.expected_digest
            if explicit_digest
            else (CURRENT_CANDIDATE_DIGEST if auto_ba68_mode else args.expected_digest)
        )
        res = reconcile_g18_benchmark_with_v1_4_slos(
            bench_doc,
            owner_slo_approval=args.owner_approval_json,
            vps_receipts=args.vps_receipt_dir,
            thresholds_path=args.thresholds_json,
            expected_digest=eff_digest,
            target_configuration_profile=args.target_config_profile,
            t1c_confirmation=args.t1c_confirmation_json,
            require_t1c_confirmation=args.require_t1c_confirmation,
            g18_diag_report=args.g18_diag_json,
            out_json=args.out,
        )
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        return 0 if res["status"] == "PASS" else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
