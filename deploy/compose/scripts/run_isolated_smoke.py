#!/usr/bin/env python3
"""Isolated Compose runtime smoke verifier for P4-01 §4 (synthetic data only).

Safety invariants enforced BEFORE any container/volume action:
1. Unique per-run project (`crm-cskh-p4-smoke-<run_id>`) and site (`p4-smoke-<run_id>.localhost`).
   Never reuses a fixed project name and never touches Owner DEV/P1/P2/P3 stacks.
2. Fails closed (`exit 3`) if ANY container, volume, network for the target project or the target
   loopback port (`127.0.0.1:<http_port>`) already exists — without invoking `down --volumes`.
3. Executes `docker compose --project-name <owned_project> ... down --volumes --remove-orphans`
   ONLY when THIS run itself created the stack (`owned_stack_created == True`) and
   `--keep-running` is false.
4. Note for `--preflight-only`: process exit code `0` means preflight evaluation completed;
   callers MUST read `report["status"]` (`"PREFLIGHT_READY"` vs `"BLOCKED"`).
5. Scrubs all ephemeral credentials, session cookies, and CSRF tokens from JSON report and log.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import pathlib
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

COMPOSE_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = COMPOSE_ROOT.parent.parent
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

IMMUTABLE_IMAGE_RE = re.compile(r"^(?:[^@\s]+@)?sha256:[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")
PROJECT_NAME_RE = re.compile(r"^crm-cskh-p4-smoke-[a-z0-9][a-z0-9-]{2,31}$")
SITE_NAME_RE = re.compile(r"^p4-smoke-[a-z0-9][a-z0-9-]{2,31}\.localhost$")
PLACEHOLDER_DIGEST = "sha256:" + ("0" * 64)
DEFAULT_P4_00_BASELINE_SHA = "be33e6c931337c42b94184b2fe292e094bd51c97"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

RESERVED_PROJECTS = {
    "crm-cskh-dev",
    "crm-cskh-p1",
    "crm-cskh-p1-dev",
    "crm-cskh-p2",
    "crm-cskh-p2-ui",
    "crm-cskh-p3-ui",
    "crm-cskh-p4-smoke",
    "crm-cskh-staging",
    "crm-cskh-prod",
}

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


class SmokeLogger:
    """Collects structured timestamped log entries and scrubs secrets before output."""

    def __init__(self) -> None:
        self._entries: list[str] = []
        self._secrets: dict[str, str] = {}

    def register_secret(self, value: str, placeholder: str) -> None:
        if value and len(value) >= 6:
            self._secrets[value] = placeholder

    def info(self, message: str) -> None:
        ts = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")
        self._entries.append(f"[{ts}] {message}")

    def scrub(self, text: str) -> str:
        out = text
        # Replace longest secrets first to avoid partial token overlap
        for raw_val, placeholder in sorted(
            self._secrets.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            out = out.replace(raw_val, placeholder)
        out = re.sub(r"(?i)(sid=)([0-9a-fA-F]{16,})", r"\1<REDACTED_SID>", out)
        out = re.sub(
            r'("X-Frappe-CSRF-Token"\s*:\s*")([^"]+)(")',
            r"\1<REDACTED_CSRF_TOKEN>\3",
            out,
        )
        out = re.sub(
            r'("encryption_key"\s*:\s*")([^"]+)(")',
            r"\1<REDACTED_ENCRYPTION_KEY>\3",
            out,
        )
        out = re.sub(r'("pwd"\s*:\s*")([^"]+)(")', r"\1<REDACTED_PASSWORD>\3", out)
        return out

    def assert_clean(self, text: str) -> None:
        for raw_val, placeholder in self._secrets.items():
            if raw_val in text:
                raise RuntimeError(f"Secret leak detected prior to output ({placeholder})")

    def render(self) -> str:
        text = "\n".join(self._entries) + "\n"
        scrubbed = self.scrub(text)
        self.assert_clean(scrubbed)
        return scrubbed


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def compose_cmd(project_name: str, env_file: pathlib.Path, extra: list[str]) -> list[str]:
    cmd = ["docker", "compose", "--project-name", project_name, "--env-file", str(env_file)]
    for cf in SMOKE_COMPOSE_FILES:
        cmd.extend(["-f", str(cf)])
    cmd.extend(extra)
    return cmd


def resolve_git_provenance(repo_dir: pathlib.Path) -> dict[str, object]:
    head = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    branch = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    status = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return {
        "head_sha": head.stdout.strip() if head.returncode == 0 else None,
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "worktree_clean": (status.returncode == 0 and not status.stdout.strip()),
    }


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


def inspect_image_metadata(image_ref: str) -> tuple[bool, dict[str, object], str]:
    res = subprocess.run(
        ["docker", "image", "inspect", image_ref],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if res.returncode != 0 or not res.stdout.strip():
        return False, {}, (res.stderr or res.stdout).strip()
    data = json.loads(res.stdout)[0]
    cfg = data.get("Config") or {}
    labels = cfg.get("Labels") or {}
    summary = {
        "image_id": data.get("Id"),
        "repo_tags": data.get("RepoTags") or [],
        "repo_digests": data.get("RepoDigests") or [],
        "created": data.get("Created"),
        "architecture": data.get("Architecture"),
        "os": data.get("Os"),
        "user": cfg.get("User"),
        "labels": {
            k: labels.get(k)
            for k in sorted(labels)
            if k.startswith("io.cskh.") or k.startswith("org.opencontainers.image.")
        },
    }
    return True, summary, ""


def inspect_project_resources(project_name: str) -> dict[str, list[str]]:
    """Returns existing containers, volumes, and networks matching `project_name`."""
    containers: set[str] = set()
    c_lbl = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if c_lbl.returncode == 0:
        containers.update(ln.strip() for ln in c_lbl.stdout.splitlines() if ln.strip())

    c_all = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if c_all.returncode == 0:
        for ln in c_all.stdout.splitlines():
            name = ln.strip()
            if name.startswith(f"{project_name}-") or name.startswith(f"{project_name}_"):
                containers.add(name)

    volumes: set[str] = set()
    v_lbl = subprocess.run(
        [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--format",
            "{{.Name}}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if v_lbl.returncode == 0:
        volumes.update(ln.strip() for ln in v_lbl.stdout.splitlines() if ln.strip())

    v_all = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if v_all.returncode == 0:
        for ln in v_all.stdout.splitlines():
            name = ln.strip()
            if name.startswith(f"{project_name}_") or name.startswith(f"{project_name}-"):
                volumes.add(name)

    networks: set[str] = set()
    n_lbl = subprocess.run(
        [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--format",
            "{{.Name}}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if n_lbl.returncode == 0:
        networks.update(ln.strip() for ln in n_lbl.stdout.splitlines() if ln.strip())

    n_all = subprocess.run(
        ["docker", "network", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if n_all.returncode == 0:
        for ln in n_all.stdout.splitlines():
            name = ln.strip()
            if name.startswith(f"{project_name}_") or name.startswith(f"{project_name}-"):
                networks.add(name)

    return {
        "containers": sorted(containers),
        "volumes": sorted(volumes),
        "networks": sorted(networks),
    }


def check_loopback_port_free(port: int) -> tuple[bool, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False, f"127.0.0.1:{port} already has an active TCP listener"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as binder:
        excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if excl is not None:
            binder.setsockopt(socket.SOL_SOCKET, excl, 1)
        try:
            binder.bind(("127.0.0.1", port))
        except OSError as exc:
            return False, f"127.0.0.1:{port} cannot be bound exclusively: {exc}"
    return True, f"127.0.0.1:{port} free"


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
        with opener.open(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, dict(resp.headers.items()), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, body


class MinimalRFC6455Client:
    """Standards-compliant RFC 6455 WebSocket client over raw TCP for local loopback smoke."""

    def __init__(self, host: str, port: int, timeout: float = 15.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()

    def close_tcp(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def upgrade(
        self,
        path_and_query: str,
        site_host: str,
        origin: str,
        cookie_header: str | None = None,
    ) -> dict[str, object]:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        expected_accept = base64.b64encode(
            hashlib.sha1((ws_key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")

        lines = [
            f"GET {path_and_query} HTTP/1.1",
            f"Host: {site_host}",
            f"Origin: {origin}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Version: 13",
            f"Sec-WebSocket-Key: {ws_key}",
        ]
        if cookie_header:
            lines.append(f"Cookie: {cookie_header}")
        raw_req = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
        self._sock.sendall(raw_req)

        header_buf = bytearray()
        while b"\r\n\r\n" not in header_buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("EOF before WebSocket HTTP 101 headers completed")
            header_buf.extend(chunk)
            if len(header_buf) > 65536:
                raise RuntimeError("Oversized HTTP upgrade response header")

        sep_idx = header_buf.index(b"\r\n\r\n")
        raw_hdr_block = bytes(header_buf[:sep_idx]).decode("iso-8859-1")
        self._buf.extend(header_buf[sep_idx + 4 :])

        hdr_lines = raw_hdr_block.split("\r\n")
        status_line = hdr_lines[0]
        parsed_headers: dict[str, str] = {}
        for hline in hdr_lines[1:]:
            if ":" in hline:
                hk, hv = hline.split(":", 1)
                parsed_headers[hk.strip().lower()] = hv.strip()

        if not status_line.startswith("HTTP/1.1 101"):
            raise AssertionError(f"Expected HTTP/1.1 101 upgrade, got: {status_line}")
        if parsed_headers.get("upgrade", "").lower() != "websocket":
            raise AssertionError(f"Missing Upgrade: websocket header: {parsed_headers}")
        if "upgrade" not in parsed_headers.get("connection", "").lower():
            raise AssertionError(f"Missing Connection: Upgrade header: {parsed_headers}")
        actual_accept = parsed_headers.get("sec-websocket-accept", "")
        if actual_accept != expected_accept:
            raise AssertionError(
                f"Sec-WebSocket-Accept mismatch: {actual_accept} != {expected_accept}"
            )

        return {
            "status_line": status_line,
            "upgrade": parsed_headers.get("upgrade"),
            "connection": parsed_headers.get("connection"),
            "sec_websocket_accept_verified": True,
        }

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EOFError("Unexpected EOF reading RFC 6455 frame")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def send_frame(self, opcode: int, payload: bytes) -> None:
        b0 = 0x80 | (opcode & 0x0F)
        n = len(payload)
        mask = os.urandom(4)
        if n < 126:
            hdr = bytes([b0, 0x80 | n])
        elif n < 65536:
            hdr = bytes([b0, 0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr = bytes([b0, 0x80 | 127]) + struct.pack("!Q", n)
        masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(hdr + mask + masked_payload)

    def send_text(self, text: str) -> None:
        self.send_frame(0x1, text.encode("utf-8"))

    def recv_frame(self) -> tuple[int, bool, bytes]:
        b0, b1 = self._read_exact(2)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask_key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return opcode, masked, payload

    def recv_next_text(self, max_frames: int = 8) -> str:
        for _ in range(max_frames):
            opcode, _, payload = self.recv_frame()
            if opcode == 0x9:  # Ping -> Pong
                self.send_frame(0xA, payload)
                continue
            if opcode == 0x1:
                text = payload.decode("utf-8", errors="replace")
                if text == "2":  # Engine.IO heartbeat ping
                    self.send_text("3")
                    continue
                return text
            if opcode == 0x8:
                raise RuntimeError("Server closed WebSocket unexpectedly")
        raise TimeoutError("Did not receive expected text frame within frame budget")

    def graceful_close(self) -> None:
        try:
            self.send_frame(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        finally:
            self.close_tcp()


def bootstrap_synthetic_site(
    project_name: str,
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

import frappe
frappe.init(site=site, sites_path="sites")
frappe.connect()
try:
    frappe.db.set_single_value("System Settings", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"})
    frappe.db.set_value("User", "Administrator", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"})
    synth_lead = frappe.db.get_value("CRM Lead", {"mobile_no": "0900000123"}, "name")
    if not synth_lead:
        doc = frappe.get_doc({
            "doctype": "CRM Lead",
            "first_name": "Khách Giả Lập",
            "last_name": "Smoke P4",
            "mobile_no": "0900000123",
            "email": "synthetic-smoke@example.invalid",
        })
        doc.insert(ignore_permissions=True)
        synth_lead = doc.name
    frappe.db.commit()
    frappe.clear_cache()
finally:
    frappe.destroy()

print(json.dumps({
    "site": site,
    "installed_apps": apps,
    "synthetic_data": {
        "lead_name": synth_lead,
        "mobile_no": "0900000123",
        "email": "synthetic-smoke@example.invalid",
        "is_synthetic": True,
    },
}))
"""
    proc = subprocess.run(
        compose_cmd(project_name, env_file, ["exec", "-T", "backend", "python3", "-c", py_code]),
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


def query_compose_states(project_name: str, env_file: pathlib.Path) -> dict[str, dict]:
    res = subprocess.run(
        compose_cmd(project_name, env_file, ["ps", "-a", "--format", "json"]),
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


def wait_for_bootstrap_ready(
    project_name: str, env_file: pathlib.Path, timeout_sec: int = 120
) -> None:
    """Waits until db/redis are healthy, configurator exited 0, and backend is running."""
    deadline = time.time() + timeout_sec
    last_states: dict[str, dict] = {}
    while time.time() < deadline:
        last_states = query_compose_states(project_name, env_file)
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


def wait_for_stack_health(
    project_name: str, env_file: pathlib.Path, timeout_sec: int = 180
) -> dict:
    deadline = time.time() + timeout_sec
    last_states: dict[str, dict] = {}
    while time.time() < deadline:
        last_states = query_compose_states(project_name, env_file)
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


def redact_socket_packet(pkt: str, logger: SmokeLogger) -> str:
    def _repl(match: re.Match[str]) -> str:
        sid_val = match.group(2)
        logger.register_secret(sid_val, "<REDACTED_SOCKET_SID>")
        return f'{match.group(1)}<REDACTED_SOCKET_SID>{match.group(3)}'

    return re.sub(r'("sid"\s*:\s*")([^"]+)(")', _repl, pkt)


def run_http_and_socketio_smoke(
    http_port: int,
    site_name: str,
    admin_pw: str,
    logger: SmokeLogger,
) -> dict:
    base_url = f"http://127.0.0.1:{http_port}"
    origin = f"http://{site_name}"

    # 1. Guest access enforcement
    _, guest_opener = make_http_client()
    g_crm_code, _, _ = http_call(guest_opener, f"{base_url}/crm", site_name)
    g_dash_code, _, _ = http_call(guest_opener, f"{base_url}/cskh/dashboard", site_name)
    g_cskh_code, _, _ = http_call(guest_opener, f"{base_url}/cskh/quality-overview", site_name)
    g_api_code, _, _ = http_call(
        guest_opener,
        f"{base_url}/api/method/crm_cskh.api.health.ping",
        site_name,
        headers={"Accept": "application/json"},
    )
    assert g_crm_code in (301, 302, 303, 403), f"Expected Guest /crm block/redirect, got {g_crm_code}"
    assert g_dash_code in (301, 302, 303, 403), (
        f"Expected Guest /cskh/dashboard 403/redirect, got {g_dash_code}"
    )
    assert g_cskh_code in (301, 302, 303, 403), (
        f"Expected Guest /cskh/quality-overview 403/redirect, got {g_cskh_code}"
    )
    assert g_api_code == 403, f"Expected Guest health.ping 403, got {g_api_code}"
    logger.info(
        f"Guest access checks passed: /crm={g_crm_code}, /cskh/dashboard={g_dash_code}, "
        f"/cskh/quality-overview={g_cskh_code}, health.ping={g_api_code}"
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
    frappe_sid = sid_cookie.value
    logger.register_secret(frappe_sid, "<REDACTED_FRAPPE_SID>")

    raw_set_cookie = " ".join(v for k, v in l_hdrs.items() if k.lower() == "set-cookie")
    sid_httponly = "httponly" in raw_set_cookie.lower()
    sid_samesite = "samesite=lax" in raw_set_cookie.lower() or "samesite=strict" in raw_set_cookie.lower()

    u_code, _, u_raw = http_call(
        auth_opener,
        f"{base_url}/api/method/frappe.auth.get_logged_user",
        site_name,
        headers={"Accept": "application/json"},
    )
    assert u_code == 200 and "Administrator" in u_raw, f"get_logged_user failed: {u_raw}"
    logger.info(
        f"Authenticated login passed: user=Administrator, sid_httponly={sid_httponly}, sid_samesite={sid_samesite}"
    )

    # 3. Vietnamese UI + timezone + CSRF token extraction
    dash_code, _, dash_html = http_call(auth_opener, f"{base_url}/cskh/dashboard", site_name)
    assert dash_code == 200, f"Expected 200 on /cskh/dashboard, got {dash_code}"
    assert "Asia/Ho_Chi_Minh" in dash_html, "Missing Asia/Ho_Chi_Minh timezone in /cskh/dashboard"

    ui_code, _, ui_html = http_call(auth_opener, f"{base_url}/cskh/quality-overview", site_name)
    assert ui_code == 200, f"Expected 200 on /cskh/quality-overview, got {ui_code}"
    assert "Asia/Ho_Chi_Minh" in ui_html, "Missing Asia/Ho_Chi_Minh timezone in /cskh/quality-overview"

    candidate_vi_labels = [
        "Đã đăng nhập với tư cách",
        "Về danh sách khách hàng tiềm năng",
        "Tổng quan",
        "Chất lượng",
        "Phân bổ",
        "Chiến dịch",
        "Làm mới",
        "Xuất báo cáo",
    ]
    matched_vi_labels = [
        lbl for lbl in candidate_vi_labels if (lbl in ui_html or lbl in dash_html)
    ]
    h1_dash = re.search(r'<h1[^>]*class="cskh-title"[^>]*>\s*([^<]+?)\s*</h1>', dash_html)
    h1_qual = re.search(r'<h1[^>]*class="cskh-title"[^>]*>\s*([^<]+?)\s*</h1>', ui_html)
    assert len(matched_vi_labels) >= 2, (
        f"Expected >= 2 Vietnamese UI labels on CSKH pages, matched={matched_vi_labels}"
    )

    csrf_match = re.search(
        r'"X-Frappe-CSRF-Token"\s*:\s*"([0-9a-fA-F\-]{16,})"', ui_html
    ) or re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fA-F\-]{16,})["\']', ui_html)
    assert csrf_match, "Could not locate CSRF token in authenticated CSKH HTML"
    csrf_token = csrf_match.group(1)
    logger.register_secret(csrf_token, "<REDACTED_CSRF_TOKEN>")

    crm_code, _, _ = http_call(auth_opener, f"{base_url}/crm", site_name)
    assert crm_code == 200, f"Expected 200 on authenticated /crm, got {crm_code}"
    logger.info(
        f"Vietnamese UI & SPA checks passed: /cskh/dashboard={dash_code}, "
        f"/cskh/quality-overview={ui_code}, /crm={crm_code}, matched_labels={matched_vi_labels}"
    )

    # 4. CSRF negative (forged + missing) & positive smoke
    forged_csrf_code, _, forged_csrf_body = http_call(
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
    assert forged_csrf_code in (400, 403), (
        f"Forged CSRF token must be rejected with 400/403, got {forged_csrf_code}: {forged_csrf_body[:200]}"
    )

    missing_csrf_code, _, missing_csrf_body = http_call(
        auth_opener,
        f"{base_url}/api/method/crm_cskh.api.health.ping",
        site_name,
        method="POST",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    assert missing_csrf_code in (400, 403), (
        f"Missing CSRF token on cookie-authenticated POST must be rejected (400/403), got {missing_csrf_code}: "
        f"{missing_csrf_body[:200]}"
    )

    good_csrf_code, _, good_csrf_body = http_call(
        auth_opener,
        f"{base_url}/api/method/crm_cskh.api.health.ping",
        site_name,
        method="POST",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Frappe-CSRF-Token": csrf_token,
        },
    )
    assert good_csrf_code == 200 and "crm_cskh" in good_csrf_body, (
        f"Valid CSRF POST failed ({good_csrf_code}): {good_csrf_body[:200]}"
    )
    good_csrf_json = json.loads(good_csrf_body).get("message") or {}
    logger.info(
        f"CSRF checks passed: forged={forged_csrf_code}, missing={missing_csrf_code}, "
        f"valid={good_csrf_code} (app_version={good_csrf_json.get('app_version')})"
    )

    # 5. Socket.IO EIO=4 HTTP Polling check through proxy -> frontend -> websocket
    _, unauth_sio = make_http_client()
    eio_code1, _, eio_open1 = http_call(
        unauth_sio,
        f"{base_url}/socket.io/?EIO=4&transport=polling",
        site_name,
        headers={"Origin": origin},
    )
    assert eio_code1 == 200 and eio_open1.startswith("0{"), f"Socket.IO open failed: {eio_open1}"
    eio_open1_obj = json.loads(eio_open1[1:])
    eio_sid1 = eio_open1_obj["sid"]
    logger.register_secret(eio_sid1, "<REDACTED_SOCKET_SID>")

    http_call(
        unauth_sio,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid1}",
        site_name,
        method="POST",
        data=f"40/{site_name},".encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": origin},
    )
    unauth_poll_code, _, unauth_pkt = http_call(
        unauth_sio,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid1}",
        site_name,
        headers={"Origin": origin},
    )
    assert f"44/{site_name}" in unauth_pkt, (
        f"Expected unauthenticated Socket.IO polling namespace rejection (44), got: {unauth_pkt}"
    )

    eio_code2, _, eio_open2 = http_call(
        auth_opener,
        f"{base_url}/socket.io/?EIO=4&transport=polling",
        site_name,
        headers={"Origin": origin},
    )
    assert eio_code2 == 200 and eio_open2.startswith("0{"), f"Auth Socket.IO open failed: {eio_open2}"
    eio_open2_obj = json.loads(eio_open2[1:])
    eio_sid2 = eio_open2_obj["sid"]
    logger.register_secret(eio_sid2, "<REDACTED_SOCKET_SID>")

    http_call(
        auth_opener,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid2}",
        site_name,
        method="POST",
        data=f"40/{site_name},".encode("utf-8"),
        headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": origin},
    )
    auth_poll_code, _, auth_pkt = http_call(
        auth_opener,
        f"{base_url}/socket.io/?EIO=4&transport=polling&sid={eio_sid2}",
        site_name,
        headers={"Origin": origin},
    )
    assert f"40/{site_name}" in auth_pkt, (
        f"Expected authenticated Socket.IO polling namespace accept (40/{site_name}), got: {auth_pkt}"
    )
    redacted_auth_poll_pkt = redact_socket_packet(auth_pkt, logger)
    redacted_unauth_poll_pkt = redact_socket_packet(unauth_pkt, logger)
    logger.info(
        f"Socket.IO HTTP polling passed: unauth='{redacted_unauth_poll_pkt}', auth='{redacted_auth_poll_pkt}'"
    )

    # 6. Real RFC 6455 WebSocket Upgrade & Frame Exchange through proxy -> frontend -> websocket
    # 6a. Engine.IO polling-to-websocket transport upgrade (`2probe` -> `3probe` -> `5`)
    ws_upgrade_client = MinimalRFC6455Client("127.0.0.1", http_port)
    try:
        upg_handshake = ws_upgrade_client.upgrade(
            path_and_query=f"/socket.io/?EIO=4&transport=websocket&sid={eio_sid2}",
            site_host=site_name,
            origin=origin,
            cookie_header=f"sid={frappe_sid}",
        )
        ws_upgrade_client.send_text("2probe")
        probe_reply = ""
        observed_upgrade_rx: list[str] = []
        for _ in range(5):
            rx = ws_upgrade_client.recv_next_text()
            observed_upgrade_rx.append(redact_socket_packet(rx, logger))
            if rx == "3probe":
                probe_reply = rx
                break
        assert probe_reply == "3probe", (
            f"Expected Engine.IO WebSocket '3probe' reply, got: {observed_upgrade_rx}"
        )
        ws_upgrade_client.send_text("5")
    finally:
        ws_upgrade_client.graceful_close()

    # 6b. Direct WebSocket-only Socket.IO namespace authentication (`0{...}` -> `40/<site>` -> `40/<site>,{...}`)
    ws_direct_client = MinimalRFC6455Client("127.0.0.1", http_port)
    try:
        direct_handshake = ws_direct_client.upgrade(
            path_and_query="/socket.io/?EIO=4&transport=websocket",
            site_host=site_name,
            origin=origin,
            cookie_header=f"sid={frappe_sid}",
        )
        ws_open_frame = ws_direct_client.recv_next_text()
        assert ws_open_frame.startswith("0{"), (
            f"Expected Engine.IO open frame over WebSocket, got: {ws_open_frame}"
        )
        redacted_ws_open = redact_socket_packet(ws_open_frame, logger)

        ws_direct_client.send_text(f"40/{site_name},")
        ws_ns_frame = ws_direct_client.recv_next_text()
        assert ws_ns_frame.startswith(f"40/{site_name}"), (
            f"Expected authenticated WebSocket namespace accept 40/{site_name}, got: {ws_ns_frame}"
        )
        redacted_ws_ns = redact_socket_packet(ws_ns_frame, logger)

        # Control frame ping/pong check (RFC 6455 opcode 0x9 -> 0xA)
        ws_direct_client.send_frame(0x9, b"cskh-p4-ping")
        pong_opcode, pong_masked, pong_payload = ws_direct_client.recv_frame()
        assert pong_opcode == 0xA and not pong_masked and pong_payload == b"cskh-p4-ping", (
            f"Expected RFC 6455 Pong (0xA, 'cskh-p4-ping'), got opcode={pong_opcode} payload={pong_payload!r}"
        )
    finally:
        ws_direct_client.graceful_close()

    logger.info(
        f"Socket.IO WebSocket RFC 6455 upgrade & frame exchange passed: "
        f"status='{direct_handshake['status_line']}', probe='2probe->3probe->5', "
        f"direct_ns='{redacted_ws_ns}', rfc6455_ping_pong='cskh-p4-ping'"
    )

    return {
        "guest_access": {
            "crm_status": g_crm_code,
            "cskh_dashboard_status": g_dash_code,
            "cskh_quality_overview_status": g_cskh_code,
            "health_ping_api_status": g_api_code,
        },
        "login": {
            "status": l_code,
            "logged_user": "Administrator",
            "sid_cookie_present": True,
            "sid_httponly": sid_httponly,
            "sid_samesite": sid_samesite,
        },
        "vietnamese_ui": {
            "cskh_dashboard_status": dash_code,
            "cskh_quality_overview_status": ui_code,
            "crm_spa_status": crm_code,
            "timezone_present": "Asia/Ho_Chi_Minh",
            "dashboard_h1": h1_dash.group(1) if h1_dash else None,
            "quality_overview_h1": h1_qual.group(1) if h1_qual else None,
            "matched_vietnamese_labels": matched_vi_labels,
        },
        "csrf": {
            "forged_token_rejected_status": forged_csrf_code,
            "missing_token_rejected_status": missing_csrf_code,
            "valid_token_accepted_status": good_csrf_code,
            "health_ping_payload": good_csrf_json,
        },
        "socketio_polling": {
            "status": "PASS",
            "transport": "polling",
            "upgrades_advertised": eio_open2_obj.get("upgrades", []),
            "unauthenticated_namespace_status": unauth_poll_code,
            "unauthenticated_packet": redacted_unauth_poll_pkt,
            "authenticated_namespace_status": auth_poll_code,
            "authenticated_packet": redacted_auth_poll_pkt,
        },
        "socketio_websocket": {
            "status": "PASS",
            "transport": "websocket",
            "polling_to_ws_upgrade": {
                "handshake": upg_handshake,
                "frames_exchanged": [
                    {"direction": "client->server", "opcode": 1, "masked": True, "payload": "2probe"},
                    *[
                        {
                            "direction": "server->client",
                            "opcode": 1,
                            "masked": False,
                            "payload": pkt,
                        }
                        for pkt in observed_upgrade_rx
                    ],
                    {"direction": "client->server", "opcode": 1, "masked": True, "payload": "5"},
                ],
            },
            "direct_ws_namespace": {
                "handshake": direct_handshake,
                "frames_exchanged": [
                    {
                        "direction": "server->client",
                        "opcode": 1,
                        "masked": False,
                        "payload": redacted_ws_open,
                    },
                    {
                        "direction": "client->server",
                        "opcode": 1,
                        "masked": True,
                        "payload": f"40/{site_name},",
                    },
                    {
                        "direction": "server->client",
                        "opcode": 1,
                        "masked": False,
                        "payload": redacted_ws_ns,
                    },
                    {
                        "direction": "client->server",
                        "opcode": 9,
                        "masked": True,
                        "payload": "cskh-p4-ping",
                    },
                    {
                        "direction": "server->client",
                        "opcode": 10,
                        "masked": False,
                        "payload": "cskh-p4-ping",
                    },
                ],
            },
        },
    }


def write_scrubbed_artifacts(
    report: dict,
    logger: SmokeLogger,
    report_out: pathlib.Path | None,
    log_out: pathlib.Path | None,
) -> str:
    raw_json = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    scrubbed_json = logger.scrub(raw_json)
    logger.assert_clean(scrubbed_json)

    if report_out:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(scrubbed_json, encoding="utf-8")

    if log_out:
        log_out.parent.mkdir(parents=True, exist_ok=True)
        log_out.write_text(logger.render(), encoding="utf-8")

    return scrubbed_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-image",
        default=os.environ.get("APP_IMAGE", ""),
        help=(
            "Immutable custom image handed off by T1B: either <repo>@sha256:<64-hex> "
            "or local Docker image ID sha256:<64-hex>."
        ),
    )
    parser.add_argument(
        "--image-git-sha",
        default=os.environ.get("IMAGE_GIT_SHA", ""),
        help="Git commit SHA of T1B's image build branch (codex/p4-01-image) for provenance.",
    )
    parser.add_argument(
        "--p4-00-approved-sha",
        default=os.environ.get("P4_00_APPROVED_SHA", DEFAULT_P4_00_BASELINE_SHA),
        help="Commit SHA of P4-00 entry audit reference (defaults to be33e6c).",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Optional unique slug [a-z0-9-] for per-run isolation (default: auto timestamp+hex).",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=int(os.environ.get("HTTP_PUBLISH_PORT", "18080")),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Evaluate prerequisites and render `config --services` without starting containers. "
            "Exit 0 means preflight completed; inspect JSON `status` for PREFLIGHT_READY vs BLOCKED."
        ),
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep isolated smoke containers up after verification (default: tear down owned stack).",
    )
    parser.add_argument("--report-out", type=pathlib.Path, default=None)
    parser.add_argument("--log-out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    logger = SmokeLogger()
    run_id = args.run_id or f"{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    if not RUN_ID_RE.match(run_id):
        raise SystemExit(f"SAFETY STOP: invalid --run-id '{run_id}' (must match {RUN_ID_RE.pattern})")

    project_name = f"crm-cskh-p4-smoke-{run_id}"
    site_name = f"p4-smoke-{run_id}.localhost"

    if (
        project_name in RESERVED_PROJECTS
        or not PROJECT_NAME_RE.match(project_name)
        or not SITE_NAME_RE.match(site_name)
    ):
        raise SystemExit(
            f"SAFETY STOP: refusing non-ephemeral target project={project_name} site={site_name}"
        )

    compose_git = resolve_git_provenance(COMPOSE_ROOT)
    template_env = parse_env_file(SMOKE_ENV_TEMPLATE)
    app_image = args.app_image or template_env["APP_IMAGE"]

    logger.info(
        f"Starting isolated smoke runner: run_id={run_id}, project={project_name}, "
        f"site={site_name}, port=127.0.0.1:{args.http_port}, preflight_only={args.preflight_only}"
    )
    logger.info(
        f"Compose git provenance: branch={compose_git['branch']}, sha={compose_git['head_sha']}, "
        f"clean={compose_git['worktree_clean']}"
    )

    blockers: list[dict[str, str]] = []
    if not IMMUTABLE_IMAGE_RE.match(app_image) or PLACEHOLDER_DIGEST in app_image:
        blockers.append(
            {
                "id": "BLK-RUNTIME-01",
                "component": "Custom Production Image Digest / ID (T1B)",
                "status": "BLOCKED",
                "detail": (
                    "T1B has not yet handed off a built APP_IMAGE (<repo>@sha256:<64-hex> "
                    "or local sha256:<64-hex>)."
                ),
            }
        )

    daemon_ok, daemon_detail = check_docker_daemon()
    image_meta: dict[str, object] = {}
    effective_pull_policy = (
        "never" if app_image.startswith("sha256:") else template_env.get("PULL_POLICY", "missing")
    )

    if not daemon_ok:
        blockers.append(
            {
                "id": "BLK-RUNTIME-02",
                "component": "Host Docker Engine Daemon",
                "status": "BLOCKED",
                "detail": f"Docker Engine daemon is not running: {daemon_detail}",
            }
        )
    elif not blockers:
        img_ok, image_meta, img_err = inspect_image_metadata(app_image)
        if img_ok:
            # Image is already verified in local Docker store; lock PULL_POLICY=never
            # so Compose never attempts to contact an external registry during local smoke.
            effective_pull_policy = "never"
            logger.info(
                f"Verified local image: app_image={app_image}, image_id={image_meta.get('image_id')}, "
                f"pull_policy={effective_pull_policy}"
            )
        elif effective_pull_policy in ("missing", "never"):
            blockers.append(
                {
                    "id": "BLK-RUNTIME-03",
                    "component": "APP_IMAGE Availability",
                    "status": "BLOCKED",
                    "detail": f"Image {app_image} not present locally: {img_err}",
                }
            )

    # Pre-existing resource & port guard (MUST run before creating stack; NEVER runs down --volumes on collision!)
    preexisting_resources = (
        inspect_project_resources(project_name)
        if daemon_ok
        else {"containers": [], "volumes": [], "networks": []}
    )
    has_preexisting = any(preexisting_resources.values())
    port_free, port_detail = check_loopback_port_free(args.http_port)

    if has_preexisting or (not args.preflight_only and not port_free):
        collision_detail = {
            "status": "REFUSED_PREEXISTING_RESOURCES",
            "run_id": run_id,
            "project_name": project_name,
            "site_name": site_name,
            "preexisting_resources": preexisting_resources,
            "port_check": {"port": args.http_port, "free": port_free, "detail": port_detail},
            "cleanup_invoked": False,
        }
        logger.info(
            f"SAFETY REFUSAL (no cleanup invoked): {json.dumps(collision_detail, ensure_ascii=False)}"
        )
        scrubbed = write_scrubbed_artifacts(collision_detail, logger, args.report_out, args.log_out)
        print(scrubbed, end="")
        return 3

    with tempfile.TemporaryDirectory(prefix=f"cskh-p4-smoke-{run_id}-") as td:
        tmp = pathlib.Path(td)
        db_pw = secrets.token_urlsafe(24)
        adm_pw = "SmokeP4#" + secrets.token_hex(8)
        logger.register_secret(db_pw, "<REDACTED_DB_ROOT_PASSWORD>")
        logger.register_secret(adm_pw, "<REDACTED_ADMIN_PASSWORD>")

        sec_file = tmp / "db_root_password.txt"
        sec_file.write_text(db_pw + "\n", encoding="utf-8")
        try:
            os.chmod(sec_file, 0o600)
        except OSError:
            pass

        run_env = dict(template_env)
        run_env["COMPOSE_PROJECT_NAME"] = project_name
        run_env["SITE_NAME"] = site_name
        run_env["SITES_RULE"] = f"Host(`{site_name}`)"
        run_env["APP_IMAGE"] = app_image
        run_env["PULL_POLICY"] = effective_pull_policy
        run_env["DB_PASSWORD_SECRETS_FILE"] = str(sec_file)
        run_env["HTTP_PUBLISH_PORT"] = str(args.http_port)
        env_file = tmp / "isolated-smoke.env"
        env_file.write_text(
            "\n".join(f"{k}={v}" for k, v in run_env.items()) + "\n", encoding="utf-8"
        )

        srv_res = subprocess.run(
            compose_cmd(project_name, env_file, ["config", "--services"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert srv_res.returncode == 0, f"config --services failed: {srv_res.stderr}"
        config_services = [s.strip() for s in srv_res.stdout.splitlines() if s.strip()]

        srv_mig_res = subprocess.run(
            compose_cmd(project_name, env_file, ["--profile", "migrate", "config", "--services"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert srv_mig_res.returncode == 0, f"migrate config --services failed: {srv_mig_res.stderr}"
        config_services_migrate = [
            s.strip() for s in srv_mig_res.stdout.splitlines() if s.strip()
        ]
        logger.info(f"Rendered config --services ({len(config_services)}): {config_services}")

        if args.preflight_only or blockers:
            status_val = "BLOCKED" if blockers else "PREFLIGHT_READY"
            logger.info(f"Preflight completed with status={status_val}, blockers={len(blockers)}")
            report = {
                "status": status_val,
                "preflight_only": bool(args.preflight_only),
                "exit_code_note": (
                    "When --preflight-only is used, process exit code 0 only indicates preflight "
                    "evaluation finished; callers MUST inspect report['status'] "
                    "('PREFLIGHT_READY' vs 'BLOCKED')."
                ),
                "run_id": run_id,
                "project_name": project_name,
                "site_name": site_name,
                "compose_provenance": compose_git,
                "image_provenance": {
                    "image_git_sha": args.image_git_sha or None,
                    "app_image": app_image,
                    "inspect": image_meta or None,
                },
                "preexisting_resources_check": {
                    "passed": True,
                    "found": preexisting_resources,
                    "port_check": {"port": args.http_port, "free": port_free, "detail": port_detail},
                },
                "config_services": config_services,
                "config_services_with_migrate_profile": config_services_migrate,
                "blockers": blockers,
            }
            scrubbed = write_scrubbed_artifacts(report, logger, args.report_out, args.log_out)
            print(scrubbed, end="")
            return 2 if (blockers and not args.preflight_only) else 0

        # Ownership gate: ONLY set True immediately before creating THIS run's isolated stack
        owned_project_name = project_name
        owned_stack_created = False
        teardown_completed = False
        teardown_audit: dict[str, object] = {"executed": False}

        try:
            owned_stack_created = True
            logger.info(f"Bringing up isolated Compose project '{owned_project_name}'")
            up_proc = subprocess.run(
                compose_cmd(owned_project_name, env_file, ["up", "-d"]),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if up_proc.returncode != 0:
                raise RuntimeError(
                    f"docker compose up -d failed ({up_proc.returncode}): {up_proc.stderr}\n{up_proc.stdout}"
                )

            wait_for_bootstrap_ready(owned_project_name, env_file)
            logger.info("Day-0 infra (db, redis-cache, redis-queue, configurator) ready; bootstrapping synthetic site")
            site_info = bootstrap_synthetic_site(
                owned_project_name, env_file, site_name, db_pw, adm_pw
            )
            logger.info(f"Synthetic site bootstrapped: {json.dumps(site_info, ensure_ascii=False)}")

            health_states = wait_for_stack_health(owned_project_name, env_file)
            logger.info("All 10 long-running services reached health='healthy' and configurator exited 0")

            # Verify published ports on running stack: ONLY proxy 127.0.0.1:<port>->80/tcp
            port_audit: dict[str, list] = {}
            for sname, sstate in sorted(health_states.items()):
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
            logger.info(f"Port exposure audit passed: {json.dumps(port_audit)}")

            # Controlled migrator idempotency rehearsal (x2)
            mig_env = dict(run_env)
            mig_env["MIGRATE_SITES"] = "true"
            mig_env_file = tmp / "isolated-smoke-migrate.env"
            mig_env_file.write_text(
                "\n".join(f"{k}={v}" for k, v in mig_env.items()) + "\n", encoding="utf-8"
            )
            migrator_runs: list[dict[str, object]] = []
            for idx in (1, 2):
                t0 = time.monotonic()
                m_proc = subprocess.run(
                    compose_cmd(
                        owned_project_name,
                        mig_env_file,
                        ["--profile", "migrate", "run", "--rm", "migrator"],
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                elapsed = round(time.monotonic() - t0, 2)
                if m_proc.returncode != 0:
                    raise RuntimeError(
                        f"Migrator run #{idx} failed ({m_proc.returncode}): {m_proc.stderr}\n{m_proc.stdout}"
                    )
                out_lines = [
                    ln.strip() for ln in (m_proc.stdout or "").splitlines() if ln.strip()
                ]
                migrator_runs.append(
                    {
                        "run_number": idx,
                        "exit_code": m_proc.returncode,
                        "duration_sec": elapsed,
                        "output_tail": out_lines[-3:],
                    }
                )
                logger.info(
                    f"Migrator rehearsal #{idx} exited 0 in {elapsed}s: {out_lines[-2:]}"
                )

            smoke_results = run_http_and_socketio_smoke(
                args.http_port, site_name, adm_pw, logger
            )

            if not args.keep_running:
                logger.info(
                    f"Tearing down owned isolated stack '{owned_project_name}' (down --volumes --remove-orphans)"
                )
                down_proc = subprocess.run(
                    compose_cmd(
                        owned_project_name,
                        env_file,
                        ["down", "--volumes", "--remove-orphans"],
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                teardown_completed = down_proc.returncode == 0
                remaining = inspect_project_resources(owned_project_name)
                assert not any(remaining.values()), (
                    f"Leftover resources after scoped teardown of {owned_project_name}: {remaining}"
                )
                teardown_audit = {
                    "executed": True,
                    "scoped_project": owned_project_name,
                    "exit_code": down_proc.returncode,
                    "remaining_resources": remaining,
                }
                logger.info(
                    f"Scoped teardown verified clean for '{owned_project_name}': 0 remaining containers/volumes/networks"
                )

            report = {
                "status": "PASS",
                "preflight_only": False,
                "run_id": run_id,
                "project_name": owned_project_name,
                "site_name": site_name,
                "p4_00_approved_sha": args.p4_00_approved_sha,
                "compose_provenance": compose_git,
                "image_provenance": {
                    "image_git_sha": args.image_git_sha
                    or (image_meta.get("labels") or {}).get("org.opencontainers.image.revision"),
                    "app_image": app_image,
                    "pull_policy_used": effective_pull_policy,
                    "image_id": image_meta.get("image_id"),
                    "repo_digests": image_meta.get("repo_digests"),
                    "labels": image_meta.get("labels"),
                },
                "preexisting_resources_check": {
                    "passed": True,
                    "found": preexisting_resources,
                    "port_check": {"port": args.http_port, "free": port_free, "detail": port_detail},
                },
                "config_services": config_services,
                "config_services_with_migrate_profile": config_services_migrate,
                "healthchecks": {
                    k: {"state": v["state"], "health": v["health"], "exit_code": v["exit_code"]}
                    for k, v in sorted(health_states.items())
                },
                "port_exposure_audit": port_audit,
                "site_bootstrap": site_info,
                "migrator_rehearsal_x2": migrator_runs,
                "smoke_verification": smoke_results,
                "teardown_audit": teardown_audit,
            }
            scrubbed = write_scrubbed_artifacts(report, logger, args.report_out, args.log_out)
            print(scrubbed, end="")
            return 0
        finally:
            if (
                owned_stack_created
                and owned_project_name
                and PROJECT_NAME_RE.match(owned_project_name)
                and not teardown_completed
                and not args.keep_running
            ):
                logger.info(
                    f"Fallback cleanup in finally for owned stack '{owned_project_name}'"
                )
                subprocess.run(
                    compose_cmd(
                        owned_project_name,
                        env_file,
                        ["down", "--volumes", "--remove-orphans"],
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )


if __name__ == "__main__":
    sys.exit(main())
