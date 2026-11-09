#!/usr/bin/env python3
"""Shared isolated Compose, HTTP+CSRF, RFC 6455 WebSocket, bench, and scrubber utilities for P4 Đợt 2."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.cookiejar
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from xml.sax.saxutils import escape as xml_escape

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]
COMPOSE_ROOT = REPO_ROOT / "deploy" / "compose"
UPSTREAM_DIR = COMPOSE_ROOT / "upstream" / "frappe_docker"
OVERRIDES_DIR = COMPOSE_ROOT / "overrides"
SMOKE_ENV_TEMPLATE = COMPOSE_ROOT / "env" / "isolated-smoke.env.example"
EDGE_RATELIMIT_OVERRIDE = PACKAGE_DIR / "compose.cskh-edge-ratelimit.yaml"

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

LONG_RUNNING_SERVICES = [
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

APP_RUNTIME_SERVICES = [
    "backend",
    "frontend",
    "queue-long",
    "queue-short",
    "scheduler",
    "websocket",
]

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PHONE_RE = re.compile(r"(?:\+?84|0)\d{8,10}")
SENSITIVE_KEYS = {
    "admin_password",
    "authorization",
    "cookie",
    "csrf",
    "csrf_token",
    "db_root_password",
    "encryption_key",
    "password",
    "pwd",
    "sid",
    "token",
}


class SecretScrubber:
    """Scrubs ephemeral passwords, cookies, CSRF tokens, private URLs, and phone numbers."""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._logs: list[str] = []

    def register(self, value: str, placeholder: str = "[REDACTED_SECRET]") -> None:
        if value and len(value) >= 6:
            self._secrets[value] = placeholder
            enc = urllib.parse.quote(value, safe="")
            if enc and len(enc) >= 6:
                self._secrets[enc] = placeholder

    def log(self, message: str) -> None:
        ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        line = f"[{ts}] {message}"
        self._logs.append(line)
        print(self.scrub_text(line), flush=True)

    def scrub_text(self, text: str) -> str:
        out = text
        for raw_val, placeholder in sorted(
            self._secrets.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            out = out.replace(raw_val, placeholder)
        out = re.sub(r"(?i)(\bsid=)([^;\s\"',&]+)", r"\1[REDACTED_SID]", out)
        out = re.sub(
            r'(?i)(["\']X-Frappe-CSRF-Token["\']\s*:\s*["\'])([^"\']+)(["\'])',
            r"\1[REDACTED_CSRF]\3",
            out,
        )
        out = re.sub(
            r'(?i)("encryption_key"\s*:\s*")([^"]+)(")',
            r"\1[REDACTED_ENCRYPTION_KEY]\3",
            out,
        )
        out = re.sub(
            r"(?i)(/private/files/)[^\"'\s,}&?]+", r"\1[REDACTED_PRIVATE_FILE]", out
        )
        out = PHONE_RE.sub(lambda m: "******" + m.group(0)[-4:], out)
        return out

    def sanitize_obj(self, obj: object, key: str = "") -> object:
        if key.lower() in SENSITIVE_KEYS:
            return "[REDACTED]"
        if isinstance(obj, dict):
            return {str(k): self.sanitize_obj(v, str(k)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.sanitize_obj(v) for v in obj]
        if isinstance(obj, str):
            return self.scrub_text(obj)
        return obj

    def assert_no_leaks(self, rendered: str) -> None:
        for raw_val, placeholder in self._secrets.items():
            if raw_val in rendered:
                raise RuntimeError(f"Secret leak blocked before write ({placeholder})")

    def write_json_and_log(
        self,
        payload: dict[str, object],
        json_path: pathlib.Path,
        log_path: pathlib.Path | None = None,
    ) -> None:
        clean_obj = self.sanitize_obj(payload)
        raw_json = json.dumps(clean_obj, indent=2, ensure_ascii=False) + "\n"
        scrubbed_json = self.scrub_text(raw_json)
        self.assert_no_leaks(scrubbed_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(scrubbed_json, encoding="utf-8", newline="\n")

        if log_path is not None:
            raw_log = "\n".join(self._logs) + "\n"
            scrubbed_log = self.scrub_text(raw_log)
            self.assert_no_leaks(scrubbed_log)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(scrubbed_log, encoding="utf-8", newline="\n")


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def ensure_port_free(port: int) -> None:
    if port in (80, 443, 8000, 8120, 8220, 8320, 18320):
        raise RuntimeError(f"Refusing reserved port {port}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port 127.0.0.1:{port} already has an active listener")


def build_minimal_xlsx(rows: list[list[object]]) -> bytes:
    """Builds a minimal valid OOXML `.xlsx` archive in memory using stdlib only."""
    col_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sheet_rows_xml: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells_xml: list[str] = []
        for c_idx, val in enumerate(row):
            ref = f"{col_letters[c_idx]}{r_idx}"
            cells_xml.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(str(val))}</t></is></c>'
            )
        sheet_rows_xml.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(sheet_rows_xml)}</sheetData>"
            "</worksheet>",
        )
    return buf.getvalue()


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpActorSession:
    """Cookie + CSRF HTTP client hitting `http://127.0.0.1:<port>` with explicit `Host` header."""

    def __init__(
        self,
        base_url: str,
        site_host: str,
        scrubber: SecretScrubber,
        label: str = "guest",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.site_host = site_host
        self.scrubber = scrubber
        self.label = label
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            NoRedirectHandler(),
        )
        self.csrf_token = ""
        self.user_email = ""

    @property
    def sid(self) -> str:
        for cookie in self.jar:
            if cookie.name == "sid":
                return cookie.value
        return ""

    def cookie_header(self) -> str:
        sid_val = self.sid
        return f"sid={sid_val}" if sid_val else ""

    def login(
        self, usr: str, pwd: str, timeout: float = 25.0
    ) -> tuple[int, dict[str, object]]:
        self.user_email = usr
        self.scrubber.register(pwd, f"<REDACTED_PWD_{self.label.upper()}>")
        status, _, body = self.post_json(
            "/api/method/login",
            {"usr": usr, "pwd": pwd},
            include_csrf=False,
            timeout=timeout,
        )
        if self.sid and self.sid != "Guest":
            self.scrubber.register(self.sid, f"<REDACTED_SID_{self.label.upper()}>")
        if status == 200:
            self.refresh_csrf()
        return status, body if isinstance(body, dict) else {"raw": str(body)[:300]}

    def refresh_csrf(self) -> str:
        for path in (
            "/cskh",
            "/cskh/my-customers",
            "/cskh/quality-overview",
            "/cskh/scorecard",
        ):
            st, _, raw_bytes = self.raw_request("GET", path)
            if st == 200:
                html = raw_bytes.decode("utf-8", errors="replace")
                for pat in (
                    r'["\']X-Frappe-CSRF-Token["\']\s*:\s*["\']([^"\']+)',
                    r'csrf_token\s*=\s*["\']([^"\']+)',
                ):
                    m = re.search(pat, html)
                    if m and m.group(1) and m.group(1) != "None":
                        self.csrf_token = m.group(1)
                        self.scrubber.register(
                            self.csrf_token, f"<REDACTED_CSRF_{self.label.upper()}>"
                        )
                        return self.csrf_token
        # Fallback for non-CSKH accounts (`outside`) via Desk `/app`
        st, _, raw_bytes = self.raw_request("GET", "/app")
        if st == 200:
            html = raw_bytes.decode("utf-8", errors="replace")
            m = re.search(r'csrf_token\s*[:=]\s*["\']([^"\']+)', html)
            if m and m.group(1) and m.group(1) != "None":
                self.csrf_token = m.group(1)
                self.scrubber.register(
                    self.csrf_token, f"<REDACTED_CSRF_{self.label.upper()}>"
                )
        return self.csrf_token

    def raw_request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, dict[str, str], bytes]:
        hdrs = {"Host": self.site_host}
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

    def get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        full_path = path
        if params:
            clean = {
                k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                for k, v in params.items()
                if v is not None
            }
            if clean:
                qs = urllib.parse.urlencode(clean, doseq=True)
                full_path += ("&" if "?" in full_path else "?") + qs
        st, hdrs, raw = self.raw_request(
            "GET",
            full_path,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        return st, hdrs, _decode_json(raw)

    def post_json(
        self,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        include_csrf: bool = True,
        csrf_override: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        hdrs = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        tok = self.csrf_token if csrf_override is None else csrf_override
        if include_csrf and tok:
            hdrs["X-Frappe-CSRF-Token"] = tok
        raw_body = json.dumps(payload or {}).encode("utf-8")
        st, resp_hdrs, raw = self.raw_request(
            "POST", path, data=raw_body, headers=hdrs, timeout=timeout
        )
        return st, resp_hdrs, _decode_json(raw)

    def upload_multipart(
        self,
        path: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "text/csv",
        extra_fields: dict[str, str] | None = None,
        timeout: float = 45.0,
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        boundary = "----CSKHBatch2Boundary" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for fk, fv in (extra_fields or {}).items():
            chunks.append(f"--{boundary}\r\n".encode("ascii"))
            chunks.append(
                f'Content-Disposition: form-data; name="{fk}"\r\n\r\n{fv}\r\n'.encode(
                    "utf-8"
                )
            )
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(file_bytes)
        chunks.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
        body = b"".join(chunks)
        hdrs = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        if self.csrf_token:
            hdrs["X-Frappe-CSRF-Token"] = self.csrf_token
        st, resp_hdrs, raw = self.raw_request(
            "POST", path, data=body, headers=hdrs, timeout=timeout
        )
        return st, resp_hdrs, _decode_json(raw)


def _decode_json(raw: bytes) -> dict[str, object]:
    txt = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(txt)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except Exception:
        return {"raw": txt[:500]}


def unwrap_message(body: dict[str, object]) -> object:
    if isinstance(body, dict) and "message" in body:
        return body["message"]
    return body


class RFC6455SocketIOClient:
    """Minimal RFC 6455 WebSocket + Engine.IO v4 / Socket.IO v4 client over raw TCP."""

    def __init__(self, host: str, port: int, timeout: float = 12.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()

    def set_timeout(self, timeout: float) -> None:
        self._sock.settimeout(timeout)

    def close(self) -> None:
        try:
            self.send_frame(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def upgrade(
        self,
        site_host: str,
        origin: str,
        cookie_header: str = "",
        path: str = "/socket.io/?EIO=4&transport=websocket",
    ) -> dict[str, object]:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        expected_accept = base64.b64encode(
            hashlib.sha1((ws_key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {site_host}",
            f"Origin: {origin}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Version: 13",
            f"Sec-WebSocket-Key: {ws_key}",
        ]
        if cookie_header:
            lines.append(f"Cookie: {cookie_header}")
        self._sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        hdr_buf = bytearray()
        while b"\r\n\r\n" not in hdr_buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("EOF before WebSocket HTTP 101 headers completed")
            hdr_buf.extend(chunk)
        sep = hdr_buf.index(b"\r\n\r\n")
        raw_hdr = bytes(hdr_buf[:sep]).decode("iso-8859-1")
        self._buf.extend(hdr_buf[sep + 4 :])
        hdr_lines = raw_hdr.split("\r\n")
        status_line = hdr_lines[0]
        headers: dict[str, str] = {}
        for ln in hdr_lines[1:]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        if not status_line.startswith("HTTP/1.1 101"):
            raise AssertionError(f"Expected HTTP 101, got {status_line}")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise AssertionError("Sec-WebSocket-Accept mismatch")
        return {"status_line": status_line, "sec_accept_ok": True}

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EOFError("EOF reading WebSocket frame")
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
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(hdr + mask + masked)

    def send_text(self, text: str) -> None:
        self.send_frame(0x1, text.encode("utf-8"))

    def recv_frame(self) -> tuple[int, bytes]:
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
        return opcode, payload

    def recv_next_text(self, max_frames: int = 8) -> str:
        for _ in range(max_frames):
            opcode, payload = self.recv_frame()
            if opcode == 0x9:
                self.send_frame(0xA, payload)
                continue
            if opcode == 0x1:
                txt = payload.decode("utf-8", errors="replace")
                if txt == "2":
                    self.send_text("3")
                    continue
                return txt
            if opcode == 0x8:
                return "<WS_CLOSE_FRAME>"
        raise TimeoutError("Frame budget exhausted")

    def drain_text_frames(self, duration_sec: float = 1.5) -> list[str]:
        frames: list[str] = []
        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            rem = max(0.15, deadline - time.monotonic())
            self.set_timeout(rem)
            try:
                opcode, payload = self.recv_frame()
            except (TimeoutError, socket.timeout):
                break
            except (EOFError, OSError):
                frames.append("<WS_EOF>")
                break
            if opcode == 0x9:
                self.send_frame(0xA, payload)
            elif opcode == 0x1:
                txt = payload.decode("utf-8", errors="replace")
                if txt == "2":
                    self.send_text("3")
                else:
                    frames.append(txt)
            elif opcode == 0x8:
                frames.append("<WS_CLOSE_FRAME>")
                break
        self.set_timeout(12.0)
        return frames


class IsolatedComposeStack:
    """Manages an isolated 10-service Compose stack (`proxy` -> `frontend` -> `backend` / `queue-*` / `websocket`)."""

    def __init__(
        self,
        *,
        prefix: str,
        http_port: int,
        app_image: str,
        scrubber: SecretScrubber,
        extra_compose_files: list[pathlib.Path] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        slug = f"{int(time.time()) % 100000}-{secrets.token_hex(3)}"
        self.run_id = f"{prefix}-{slug}"
        self.project_name = f"cskh-b2-{self.run_id}"
        if self.project_name in RESERVED_PROJECTS:
            raise RuntimeError(f"Refusing reserved project {self.project_name}")
        self.site_name = f"p4-b2-{self.run_id}.localhost"
        self.http_port = http_port
        self.base_url = f"http://127.0.0.1:{http_port}"
        self.app_image = app_image
        self.scrubber = scrubber
        self.extra_compose_files = list(extra_compose_files or [])
        self.extra_env = dict(extra_env or {})

        self.db_root_pw = f"DbRoot-{secrets.token_urlsafe(16)}"
        self.admin_pw = f"Admin-{secrets.token_urlsafe(16)}"
        self.actor_pw = f"Actor-{secrets.token_urlsafe(16)}"
        self.scrubber.register(self.db_root_pw, "<REDACTED_DB_ROOT_PW>")
        self.scrubber.register(self.admin_pw, "<REDACTED_ADMIN_PW>")
        self.scrubber.register(self.actor_pw, "<REDACTED_ACTOR_PW>")

        self._tmpdir = pathlib.Path(tempfile.mkdtemp(prefix=f"{self.project_name}-"))
        self.secret_file = self._tmpdir / "db_root_password.txt"
        self.env_file = self._tmpdir / "stack.env"
        self._owned_started = False

    def _write_env_files(self) -> None:
        self.secret_file.write_text(
            self.db_root_pw + "\n", encoding="utf-8", newline="\n"
        )
        try:
            os.chmod(self.secret_file, 0o600)
        except OSError:
            pass

        tpl = parse_env_file(SMOKE_ENV_TEMPLATE)
        pull_policy = "never" if self.app_image.startswith("sha256:") else "missing"
        tpl.update(
            {
                "COMPOSE_PROJECT_NAME": self.project_name,
                "SITE_NAME": self.site_name,
                "APP_IMAGE": self.app_image,
                "PULL_POLICY": pull_policy,
                "DB_PASSWORD_SECRETS_FILE": self.secret_file.as_posix(),
                "HTTP_PUBLISH_PORT": str(self.http_port),
                "SITES_RULE": f"Host(`{self.site_name}`)",
                "MIGRATE_SITES": "false",
                "GUNICORN_WORKERS": "2",
                "GUNICORN_THREADS": "4",
                "BACKEND_MEM_LIMIT": "2g",
                "QUEUE_SHORT_MEM_LIMIT": "1g",
                "QUEUE_LONG_MEM_LIMIT": "1536m",
                "DB_MEM_LIMIT": "2g",
            }
        )
        tpl.update(self.extra_env)
        rendered = "\n".join(f"{k}={v}" for k, v in tpl.items()) + "\n"
        self.env_file.write_text(rendered, encoding="utf-8", newline="\n")

    def compose_cmd(self, args: list[str], *, migrate_sites: bool = False) -> list[str]:
        if migrate_sites:
            # Render transient env copy with MIGRATE_SITES=true
            mig_env = self._tmpdir / "stack-migrate.env"
            txt = self.env_file.read_text(encoding="utf-8").replace(
                "MIGRATE_SITES=false", "MIGRATE_SITES=true"
            )
            mig_env.write_text(txt, encoding="utf-8", newline="\n")
            env_target = mig_env
        else:
            env_target = self.env_file

        cmd = [
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "--env-file",
            str(env_target),
        ]
        for cf in [*SMOKE_COMPOSE_FILES, *self.extra_compose_files]:
            cmd.extend(["-f", str(cf)])
        cmd.extend(args)
        return cmd

    def run_compose(
        self,
        args: list[str],
        *,
        migrate_sites: bool = False,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            self.compose_cmd(args, migrate_sites=migrate_sites),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"compose {' '.join(args[:4])} failed (rc={proc.returncode}):\n"
                f"STDOUT:\n{proc.stdout[-1800:]}\nSTDERR:\n{proc.stderr[-1800:]}"
            )
        return proc

    def boot_and_bootstrap_site(
        self, *, install_site: bool = True
    ) -> dict[str, object]:
        ensure_port_free(self.http_port)
        self._write_env_files()
        self._owned_started = True
        self.scrubber.log(
            f"Booting stack {self.project_name} ({self.site_name}) on 127.0.0.1:{self.http_port} with {self.app_image}"
        )
        self.run_compose(["up", "-d", "--no-build", "db", "redis-cache", "redis-queue"])
        self.wait_services_healthy(
            ["db", "redis-cache", "redis-queue"], timeout_sec=120
        )

        # Run one-shot configurator
        self.run_compose(["run", "--rm", "configurator"])

        # Start backend first to run bench new-site cleanly before workers attach
        self.run_compose(["up", "-d", "--no-build", "backend"])
        boot_info: dict[str, object] = {}
        if install_site:
            boot_info = self.create_site(self.site_name)

        # Start remaining runtime services
        self.run_compose(["up", "-d", "--no-build", *LONG_RUNNING_SERVICES])
        self.wait_services_healthy(LONG_RUNNING_SERVICES, timeout_sec=180)
        self.wait_http_ping(timeout_sec=90)
        return boot_info

    def set_app_image(
        self,
        new_image: str,
        *,
        extra_compose_files: list[pathlib.Path] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.app_image = new_image
        if extra_compose_files is not None:
            self.extra_compose_files = list(extra_compose_files)
        if extra_env is not None:
            self.extra_env.update(extra_env)
        self._write_env_files()

    def apply_extra_compose_overrides(
        self,
        extra_compose_files: list[pathlib.Path],
        extra_env: dict[str, str] | None = None,
        services: list[str] | None = None,
    ) -> None:
        self.extra_compose_files = list(extra_compose_files)
        if extra_env:
            self.extra_env.update(extra_env)
        self._write_env_files()
        targets = services or ["proxy", "frontend"]
        self.run_compose(["up", "-d", "--no-build", "--force-recreate", *targets])
        self.wait_services_healthy(targets, timeout_sec=90)
        self.wait_http_ping(timeout_sec=60)

    def create_site(self, site_name: str) -> dict[str, object]:
        code = r"""
import json, os, subprocess, sys
payload = json.loads(sys.stdin.read())
site = payload["site_name"]
db_pw = payload["db_root_pw"]
adm_pw = payload["admin_pw"]
os.chdir("/home/frappe/frappe-bench")
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
os.chdir("/home/frappe/frappe-bench/sites")
import frappe
frappe.init(site=site, sites_path=".")
frappe.connect()
try:
    frappe.db.set_single_value("System Settings", "language", "vi")
    frappe.db.set_single_value("System Settings", "time_zone", "Asia/Ho_Chi_Minh")
    frappe.db.set_value("User", "Administrator", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"})
    if frappe.db.exists("DocType", "FCRM Settings"):
        frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
    frappe.db.commit()
    frappe.clear_cache()
    enc_key = frappe.local.conf.get("encryption_key", "")
finally:
    frappe.destroy()
print(json.dumps({"site": site, "installed_apps": apps, "has_encryption_key": bool(enc_key)}))
"""
        proc = self.run_compose(
            [
                "exec",
                "-T",
                "backend",
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                code,
            ],
            input_text=json.dumps(
                {
                    "site_name": site_name,
                    "db_root_pw": self.db_root_pw,
                    "admin_pw": self.admin_pw,
                }
            ),
        )
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        return json.loads(lines[-1])

    def exec_site_python(
        self,
        py_body: str,
        payload: dict[str, object] | None = None,
        *,
        site_name: str | None = None,
        service: str = "backend",
    ) -> dict[str, object]:
        target_site = site_name or self.site_name
        wrapper = (
            "import json, os, sys, frappe\n"
            "_IN = json.loads(sys.stdin.read() or '{}')\n"
            "_SITE = _IN.get('_site_name')\n"
            "os.chdir('/home/frappe/frappe-bench/sites')\n"
            "frappe.init(site=_SITE, sites_path='.')\n"
            "frappe.connect()\n"
            "frappe.set_user('Administrator')\n"
            "try:\n"
            + "\n".join("    " + ln for ln in py_body.splitlines())
            + "\n    frappe.db.commit()\n"
            "finally:\n"
            "    frappe.destroy()\n"
        )
        full_in = dict(payload or {})
        full_in["_site_name"] = target_site
        proc = self.run_compose(
            [
                "exec",
                "-T",
                service,
                "/home/frappe/frappe-bench/env/bin/python",
                "-c",
                wrapper,
            ],
            input_text=json.dumps(full_in),
        )
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            return {}
        return json.loads(lines[-1])

    def seed_standard_actors_and_teams(self) -> dict[str, object]:
        """Seeds standard P1–P3 business roles, 8 actors, `CRM Sales Hierarchy`, and working calendar."""
        body = r"""
from crm_cskh.patches.v2_0 import p2_schema_and_seeds
from crm_cskh.patches.v3_0 import p3_schema_and_seeds

p2_schema_and_seeds.execute()
p3_schema_and_seeds.execute()

pw = _IN["actor_pw"]
roles = ["CSKH BOD Viewer", "CSKH CRM Admin", "Sales Manager", "Sales User"]
for rname in roles:
    if not frappe.db.exists("Role", rname):
        frappe.get_doc({"doctype": "Role", "role_name": rname, "desk_access": 1}).insert(ignore_permissions=True)

users = [
    ("crm.admin@dev.local", "CRM Admin", ["CSKH CRM Admin"]),
    ("bod.viewer@dev.local", "BOD Viewer", ["CSKH BOD Viewer"]),
    ("team1.leader@dev.local", "Team1 Leader", ["Sales Manager", "Sales User"]),
    ("team2.leader@dev.local", "Team2 Leader", ["Sales Manager", "Sales User"]),
    ("staff.a@dev.local", "Staff A", ["Sales User"]),
    ("staff.b@dev.local", "Staff B", ["Sales User"]),
    ("staff.c@dev.local", "Staff C", ["Sales User"]),
    ("outside.user@dev.local", "Outside Internal", ["Desk User"]),
]
if not frappe.db.exists("Role", "Desk User"):
    frappe.get_doc({"doctype": "Role", "role_name": "Desk User", "desk_access": 1}).insert(ignore_permissions=True)

for email, fname, uroles in users:
    if not frappe.db.exists("User", email):
        u = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": fname,
            "enabled": 1,
            "language": "vi",
            "time_zone": "Asia/Ho_Chi_Minh",
            "send_welcome_email": 0,
            "user_type": "System User",
        })
        u.insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.enabled = 1
    u.language = "vi"
    u.time_zone = "Asia/Ho_Chi_Minh"
    u.new_password = pw
    u.roles = []
    for rn in uroles:
        u.append("roles", {"role": rn})
    u.save(ignore_permissions=True)

frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)
hierarchy = [
    ("Team1 Leader", "team1.leader@dev.local", 1, None),
    ("Staff A", "staff.a@dev.local", 0, "Team1 Leader"),
    ("Staff B", "staff.b@dev.local", 0, "Team1 Leader"),
    ("Team2 Leader", "team2.leader@dev.local", 1, None),
    ("Staff C", "staff.c@dev.local", 0, "Team2 Leader"),
]
nodes = {}
for full_name, uemail, is_grp, rep_to in hierarchy:
    existing = frappe.db.get_value("CRM Sales Hierarchy", {"user": uemail}, "name")
    parent_id = nodes.get(rep_to) if rep_to else None
    if not existing:
        doc = frappe.get_doc({
            "doctype": "CRM Sales Hierarchy",
            "full_name": full_name,
            "is_group": is_grp,
            "user": uemail,
            "reports_to": parent_id,
        })
        doc.insert(ignore_permissions=True)
        nodes[full_name] = doc.name
    else:
        nodes[full_name] = existing

# Backdate initial membership effective_from to 2026-01-01 so current-year fixtures attribute cleanly
frappe.db.sql(
    "UPDATE `tabCSKH Team Membership History` SET effective_from = '2026-01-01 00:00:00' WHERE effective_to IS NULL"
)
frappe.clear_cache()
print(json.dumps({"users_seeded": len(users), "team_nodes": nodes}))
"""
        return self.exec_site_python(body, {"actor_pw": self.actor_pw})

    def get_container_id(self, service: str) -> str:
        proc = self.run_compose(["ps", "-q", service])
        cid = proc.stdout.strip().splitlines()
        if not cid or not cid[0].strip():
            raise RuntimeError(f"No container ID found for service {service}")
        return cid[0].strip()

    def kill_service(self, service: str, sig: str = "KILL") -> dict[str, object]:
        cid = self.get_container_id(service)
        subprocess.run(
            ["docker", "kill", "-s", sig, cid], check=True, capture_output=True
        )
        insp = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}:{{.State.ExitCode}}",
                cid,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        parts = insp.split(":", 1)
        return {
            "service": service,
            "container_id_short": cid[:12],
            "signal": sig,
            "post_kill_status": parts[0],
            "exit_code": int(parts[1])
            if len(parts) > 1 and parts[1].lstrip("-").isdigit()
            else None,
        }

    def query_states(self) -> dict[str, dict[str, str]]:
        proc = self.run_compose(["ps", "-a", "--format", "json"], check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        raw = proc.stdout.strip()
        rows = (
            json.loads(raw)
            if raw.startswith("[")
            else [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
        )
        out: dict[str, dict[str, str]] = {}
        for row in rows:
            srv = str(row.get("Service") or "")
            if srv:
                out[srv] = {
                    "state": str(row.get("State") or "").lower(),
                    "health": str(row.get("Health") or "").lower(),
                    "name": str(row.get("Name") or ""),
                }
        return out

    def wait_services_healthy(
        self, services: list[str], timeout_sec: float = 150.0
    ) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            states = self.query_states()
            all_ok = True
            for srv in services:
                info = states.get(srv)
                if not info or info["state"] != "running":
                    all_ok = False
                    break
                if info["health"] and info["health"] != "healthy":
                    all_ok = False
                    break
            if all_ok:
                return
            time.sleep(2.0)
        raise TimeoutError(
            f"Services {services} did not become healthy within {timeout_sec}s: {self.query_states()}"
        )

    def wait_http_ping(
        self, site_name: str | None = None, timeout_sec: float = 90.0
    ) -> None:
        target_host = site_name or self.site_name
        actor = HttpActorSession(self.base_url, target_host, self.scrubber, "ping")
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                st, _, body = actor.get_json("/api/method/ping", timeout=6.0)
                if st == 200 and unwrap_message(body) == "pong":
                    return
            except Exception:
                pass
            time.sleep(1.5)
        raise TimeoutError(
            f"HTTP /api/method/ping for {target_host} failed after {timeout_sec}s"
        )

    def make_actor(
        self, label: str, email: str | None = None, password: str | None = None
    ) -> HttpActorSession:
        sess = HttpActorSession(self.base_url, self.site_name, self.scrubber, label)
        if email:
            pw = (
                password
                if password is not None
                else (self.admin_pw if email == "Administrator" else self.actor_pw)
            )
            st, body = sess.login(email, pw)
            if st != 200:
                raise RuntimeError(
                    f"Actor {label} ({email}) login failed ({st}): {body}"
                )
        return sess

    def teardown(self) -> None:
        try:
            if self._owned_started:
                self.scrubber.log(f"Tearing down isolated stack {self.project_name}")
                self.run_compose(
                    ["down", "--volumes", "--remove-orphans", "--timeout", "15"],
                    check=False,
                )
        finally:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
