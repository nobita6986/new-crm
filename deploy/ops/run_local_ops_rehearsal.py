#!/usr/bin/env python3
"""Isolated Local Operational Rehearsal Runner for P4-03, P4-07, P4-08 & P4-10.

Executes real, zero-residue rehearsals against the locked P4-01 custom image and Compose
topology on an ephemeral isolated project (`crm-cskh-p4-ops-reh-<run_id>`), without
touching any Owner stack or fabricating remote Registry/Staging/Production gates:

  - Phase A (P4-03 local rehearsal): `snapshot-counts` -> `migrator` #1 -> `snapshot-counts`
    -> `migrator` #2 -> `compare-counts` (`0` drift + live `BLG-P4-MIG-01` audit).
  - Phase B (P4-07 local rehearsal): execute all 4 `crm_cskh` cron jobs, run live
    `check_health_and_invariants.py` (`10/10` healthy services, RQ queues, scheduler lag,
    `INV-01..06` = `0` violations), and test HTTPS webhook alert delivery + `900s` cooldown
    against an ephemeral loopback TLS receiver.
  - Phase C (P4-08 pre-UAT automated rehearsal): verify all 15 `/cskh/*` Vietnamese routes,
    role authorization (`BOD`, `Admin`, `Leader A` vs `Leader B`, `Staff A1`, `Outside`),
    `QA = N/A` (`86.47` provisional), `is_restated`, drill-down `0` raw PII, and immediate
    session rejection upon `User.enabled = 0`.
  - Phase D (P4-10 local rollback/restore rehearsal): Tier 1 `site_config.json`
    `cskh_p3_enabled = false` kill switch (`503` on P3 while P1/P2 stays `200`) and
    Tier 4 `bench backup --with-files` -> post-backup write -> `--since-utc` delta audit
    -> `bench --force restore` + `migrator` -> `INV-01..06` reconciliation.
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_SCRIPTS_DIR = REPO_ROOT / "deploy" / "compose" / "scripts"
OPS_DIR = REPO_ROOT / "deploy" / "ops"
sys.path.insert(0, str(COMPOSE_SCRIPTS_DIR))
sys.path.insert(0, str(OPS_DIR))

import check_health_and_invariants as health_tool  # noqa: E402
import run_isolated_smoke as smoke_lib  # noqa: E402
import staging_https_smoke_and_reconcile as p403_tool  # noqa: E402

ISOLATED_COMPOSE_FILES = p403_tool.COMPOSE_MODES["isolated-smoke"]
REHEARSAL_SITE = "p3-test.localhost"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pick_free_loopback_port(preferred: int = 18180) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def compose_cmd(env_file: Path, args: list[str], profile_migrate: bool = False) -> list[str]:
    cmd = ["docker", "compose", "--env-file", str(env_file)]
    for cf in ISOLATED_COMPOSE_FILES:
        cmd.extend(["-f", str(cf)])
    if profile_migrate:
        cmd.extend(["--profile", "migrate"])
    cmd.extend(args)
    return cmd


def http_req(
    port: int,
    host_header: str,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 20,
) -> tuple[int, list[tuple[str, str]], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    req_headers = {"Host": host_header}
    if headers:
        req_headers.update(headers)
    conn.request(method, path, body=body, headers=req_headers)
    resp = conn.getresponse()
    raw = resp.read()
    hdrs = list(resp.getheaders())
    st = resp.status
    conn.close()
    return st, hdrs, raw


def login_session(
    port: int,
    site: str,
    usr: str,
    pwd: str,
    scrubber: smoke_lib.SmokeLogger,
) -> tuple[int, str, str]:
    form = urllib.parse.urlencode({"usr": usr, "pwd": pwd}).encode("utf-8")
    st, hdrs, _ = http_req(
        port,
        site,
        "/api/method/login",
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        body=form,
    )
    sid_pair = ""
    for k, v in hdrs:
        if k.lower() == "set-cookie":
            first = v.split(";", 1)[0].strip()
            if first.startswith("sid="):
                sid_pair = first
                scrubber.register_secret(first.split("=", 1)[1], "<REDACTED_FRAPPE_SID>")
                break
    csrf_token = ""
    if sid_pair:
        _, _, html_bytes = http_req(port, site, "/cskh/my-customers", headers={"Cookie": sid_pair})
        html = html_bytes.decode("utf-8", errors="replace")
        csrf_token = p403_tool.extract_csrf_token(html)
        if csrf_token:
            scrubber.register_secret(csrf_token, "<REDACTED_CSRF>")
    return st, sid_pair, csrf_token


def generate_self_signed_cert_via_backend(env_file: Path, out_dir: Path) -> tuple[Path, Path]:
    """Generate a temporary 1-day localhost TLS cert using openssl inside `backend`."""
    py = r"""
import json, subprocess, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
    cert = Path(d) / "cert.pem"
    key = Path(d) / "key.pem"
    subprocess.check_call([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=127.0.0.1",
        "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("CERT_JSON=" + json.dumps({"cert": cert.read_text(), "key": key.read_text()}))
"""
    proc = subprocess.run(
        compose_cmd(env_file, ["exec", "-T", "backend", "python3", "-c", py]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("CERT_JSON="):
            data = json.loads(line.split("=", 1)[1])
            cert_path = out_dir / "webhook_cert.pem"
            key_path = out_dir / "webhook_key.pem"
            cert_path.write_text(data["cert"], encoding="utf-8", newline="\n")
            key_path.write_text(data["key"], encoding="utf-8", newline="\n")
            return cert_path, key_path
    raise RuntimeError("Failed to generate ephemeral TLS cert via backend openssl")


def run_sub(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    env = dict(kwargs.pop("env", None) or os.environ)
    env["PYTHONUTF8"] = "1"
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    kwargs["text"] = True
    kwargs["encoding"] = "utf-8"
    kwargs["errors"] = "replace"
    return subprocess.run(cmd, env=env, **kwargs)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", default=os.environ.get("APP_IMAGE", ""), required=False)
    parser.add_argument("--image-git-sha", required=True)
    parser.add_argument(
        "--out-json",
        type=Path,
        default=OPS_DIR / "evidence" / "p4-ops-local-rehearsal-report.json",
    )
    parser.add_argument(
        "--out-log",
        type=Path,
        default=OPS_DIR / "evidence" / "p4-ops-local-rehearsal.log",
    )
    args = parser.parse_args()

    if not args.app_image:
        raise SystemExit("Pass --app-image sha256:<64hex> (or set APP_IMAGE)")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)
    project_name = f"crm-cskh-p4-ops-reh-{run_id}"
    http_port = pick_free_loopback_port(18180)

    preexisting = smoke_lib.inspect_project_resources(project_name)
    if any(preexisting.values()):
        raise RuntimeError(f"Refusing to run: preexisting resources for {project_name}: {preexisting}")

    host_before = smoke_lib.snapshot_host_docker_resources()
    ok_img, img_summary, img_err = smoke_lib.inspect_image_metadata(args.app_image)
    if not ok_img:
        raise RuntimeError(f"Cannot inspect local APP_IMAGE {args.app_image}: {img_err}")

    scrubber = smoke_lib.SmokeLogger()
    db_root_pw = secrets.token_urlsafe(24)
    admin_pw = "Adm-" + secrets.token_urlsafe(18) + "!"
    seed_pw = "Seed-" + secrets.token_urlsafe(18) + "!"
    webhook_token = secrets.token_urlsafe(16)
    for sec in (db_root_pw, admin_pw, seed_pw, webhook_token):
        scrubber.register_secret(sec, "<REDACTED_SECRET>")

    log_lines: list[str] = []

    def log(msg: str) -> None:
        line = f"[{utc_now_iso()}] {scrubber.scrub(msg)}"
        log_lines.append(line)
        print(line, flush=True)

    tmpdir = Path(tempfile.mkdtemp(prefix="p4-ops-reh-"))
    stack_started = False
    report: dict[str, Any] = {
        "timestamp_utc": utc_now_iso(),
        "evidence_scope": "LOCAL_ISOLATED_COMPOSE_REHEARSAL",
        "remote_staging_and_production_gates_status": "BLOCKED",
        "run_id": run_id,
        "project_name": project_name,
        "site_name": REHEARSAL_SITE,
        "loopback_http_port": http_port,
        "compose_baseline_sha": "492423c43a3ddb15e8f5fea23124c4bc70a6dbb8",
        "image_git_sha": args.image_git_sha,
        "app_image": args.app_image,
        "image_summary": img_summary,
        "phases": {},
    }

    try:
        secret_file = tmpdir / "db_root_password.txt"
        secret_file.write_text(db_root_pw + "\n", encoding="ascii", newline="\n")
        raw_sec = secret_file.read_bytes()
        assert raw_sec.endswith(b"\n") and b"\r" not in raw_sec

        template_env = p403_tool.parse_env_file(
            REPO_ROOT / "deploy" / "compose" / "env" / "isolated-smoke.env.example"
        )
        template_env.update(
            {
                "COMPOSE_PROJECT_NAME": project_name,
                "SITE_NAME": REHEARSAL_SITE,
                "SITES_RULE": f"Host(`{REHEARSAL_SITE}`)",
                "APP_IMAGE": args.app_image,
                "PULL_POLICY": "never",
                "INFRA_PULL_POLICY": "missing",
                "INGRESS_BIND_IP": "127.0.0.1",
                "HTTP_PUBLISH_PORT": str(http_port),
                "DB_PASSWORD_SECRETS_FILE": str(secret_file),
            }
        )
        env_file = tmpdir / "rehearsal.env"
        env_lines = [f"{k}={v}" for k, v in template_env.items()]
        env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8", newline="\n")

        log(f"Starting isolated rehearsal stack {project_name} on 127.0.0.1:{http_port}...")
        stack_started = True
        subprocess.run(
            compose_cmd(env_file, ["up", "-d", "db", "redis-cache", "redis-queue"]),
            check=True,
            capture_output=True,
            text=True,
        )
        # Wait for db & redis health
        for _ in range(60):
            ps_out = subprocess.run(
                compose_cmd(env_file, ["ps", "--format", "json"]),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            rows = p403_tool._parse_compose_ps_json(ps_out)
            by_svc = {r["Service"]: r for r in rows}
            if all(
                by_svc.get(s, {}).get("Health") == "healthy"
                for s in ("db", "redis-cache", "redis-queue")
            ):
                break
            time.sleep(2)

        subprocess.run(
            compose_cmd(env_file, ["run", "--rm", "configurator"]),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            compose_cmd(env_file, ["up", "-d", "backend"]),
            check=True,
            capture_output=True,
            text=True,
        )

        log("Bootstrapping isolated site p3-test.localhost and seeding deterministic P1-P3 dataset...")
        boot_py = r"""
import json, os, subprocess, sys
payload = json.loads(sys.stdin.read())
site = payload["site"]
subprocess.check_call([
    "bench", "new-site", site,
    "--mariadb-user-host-login-scope=%",
    "--db-root-password", payload["db_pw"],
    "--admin-password", payload["adm_pw"],
    "--install-app", "crm",
    "--install-app", "crm_cskh",
], stdout=subprocess.DEVNULL)
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
    from crm_cskh.tools.p3_e2e_seed import reset_and_seed_p3
    from crm_cskh.jobs.evaluate_sla import evaluate_sla_obligations
    from crm_cskh.jobs.recompute_kpi import drain_dirty_kpi_scopes
    seed_info = reset_and_seed_p3()
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
    print("SEED_RESULT=" + json.dumps(seed_info))
finally:
    frappe.destroy()
"""
        boot_proc = subprocess.run(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", boot_py],
            ),
            input=json.dumps(
                {
                    "site": REHEARSAL_SITE,
                    "db_pw": db_root_pw,
                    "adm_pw": admin_pw,
                    "seed_pw": seed_pw,
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        seed_info: dict[str, Any] = {}
        for ln in boot_proc.stdout.splitlines():
            if ln.startswith("SEED_RESULT="):
                seed_info = json.loads(ln.split("=", 1)[1])
                break
        if not seed_info:
            raise RuntimeError("Did not receive SEED_RESULT from p3_e2e_seed")

        subprocess.run(
            compose_cmd(
                env_file,
                [
                    "up",
                    "-d",
                    "frontend",
                    "websocket",
                    "queue-short",
                    "queue-long",
                    "scheduler",
                    "proxy",
                ],
            ),
            check=True,
            capture_output=True,
            text=True,
        )

        # Wait until all 10 daemon services report healthy (backend & frontend ping Host: p3-test.localhost)
        log("Waiting for all 10 daemon services to reach running/healthy...")
        for _ in range(45):
            ps_out = subprocess.run(
                compose_cmd(env_file, ["ps", "--format", "json"]),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            rows = p403_tool._parse_compose_ps_json(ps_out)
            by_svc = {r["Service"]: r for r in rows}
            if len(by_svc) >= 10 and all(
                by_svc.get(s, {}).get("State") == "running"
                and by_svc.get(s, {}).get("Health") == "healthy"
                for s in (
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
            ):
                break
            time.sleep(2)

        # =====================================================================
        # PHASE A: P4-03 Migration x 2 & Count/Invariant/BLG-P4-MIG-01 Reconcile
        # =====================================================================
        log("Phase A (P4-03): Running snapshot-counts -> migrator #1 -> migrator #2 -> compare-counts...")
        snap_before = tmpdir / "mig_before.json"
        snap_r1 = tmpdir / "mig_after_r1.json"
        snap_r2 = tmpdir / "mig_after_r2.json"
        mig_reconcile = tmpdir / "mig_reconcile.json"

        run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                "snapshot-counts",
                "--env-file",
                str(env_file),
                "--site",
                REHEARSAL_SITE,
                "--mode",
                "isolated-smoke",
                "--out",
                str(snap_before),
            ]
        )

        mig_env = os.environ.copy()
        mig_env["MIGRATE_SITES"] = "true"
        t0 = time.monotonic()
        run_sub(
            compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
            env=mig_env,
        )
        mig1_sec = round(time.monotonic() - t0, 2)

        run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                "snapshot-counts",
                "--env-file",
                str(env_file),
                "--site",
                REHEARSAL_SITE,
                "--mode",
                "isolated-smoke",
                "--out",
                str(snap_r1),
            ]
        )

        t1 = time.monotonic()
        run_sub(
            compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
            env=mig_env,
        )
        mig2_sec = round(time.monotonic() - t1, 2)

        run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                "snapshot-counts",
                "--env-file",
                str(env_file),
                "--site",
                REHEARSAL_SITE,
                "--mode",
                "isolated-smoke",
                "--out",
                str(snap_r2),
            ]
        )

        cmp_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                "compare-counts",
                "--before",
                str(snap_before),
                "--after-run1",
                str(snap_r1),
                "--after-run2",
                str(snap_r2),
                "--out",
                str(mig_reconcile),
            ]
        )
        cmp_json = json.loads(cmp_proc.stdout)
        report["phases"]["p4_03_migration_x2_rehearsal"] = {
            "status": cmp_json["status"],
            "migrate_run1_seconds": mig1_sec,
            "migrate_run2_seconds": mig2_sec,
            "reconciliation": cmp_json,
        }

        # =====================================================================
        # FINDINGS F-P4-OPS-01..05 LIVE VERIFICATION + PHASE B (P4-07)
        # =====================================================================
        report["findings_verification"] = {}

        # --- F-P4-OPS-01: Verify all 8 Compose files in both scripts & both runbooks via `docker compose config` ---
        log("Verifying F-P4-OPS-01: 8 Compose files in 2 scripts + 2 runbooks via docker compose config...")
        stg_example_env = REPO_ROOT / "deploy" / "compose" / "env" / "staging.env.example"
        prod_example_env = REPO_ROOT / "deploy" / "compose" / "env" / "production.env.example"
        f01_checks: dict[str, Any] = {}
        for label, cmd_base in (
            ("staging_script_tls", p403_tool._compose_cmd(stg_example_env, "tls")),
            ("staging_script_isolated", p403_tool._compose_cmd(env_file, "isolated-smoke")),
            ("health_script_tls", health_tool._compose_cmd(prod_example_env, "tls")),
            ("health_script_isolated", health_tool._compose_cmd(env_file, "isolated-smoke")),
        ):
            f_count = sum(1 for tok in cmd_base if tok == "-f")
            cfg_full = run_sub(cmd_base + ["config"])
            cfg_def = run_sub(cmd_base + ["config", "--services"])
            cfg_mig = run_sub(cmd_base + ["--profile", "migrate", "config", "--services"])
            svcs_def = [s.strip() for s in cfg_def.stdout.splitlines() if s.strip()]
            svcs_mig = [s.strip() for s in cfg_mig.stdout.splitlines() if s.strip()]
            f01_checks[label] = {
                "compose_file_count": f_count,
                "config_render_exit": cfg_full.returncode,
                "default_services": svcs_def,
                "migrate_profile_services": svcs_mig,
                "pass": (
                    f_count == 8
                    and cfg_full.returncode == 0
                    and len(svcs_def) == 11
                    and "migrator" not in svcs_def
                    and len(svcs_mig) == 12
                    and "migrator" in svcs_mig
                ),
            }

        for doc_key, doc_rel, doc_env in (
            ("runbook_p4_03", "docs/p4/p4-03-staging-checklist-and-rehearsal.md", stg_example_env),
            ("runbook_release", "docs/p4/release-runbook.md", prod_example_env),
        ):
            doc_txt = (REPO_ROOT / doc_rel).read_text(encoding="utf-8")
            doc_files = [
                str((REPO_ROOT / m.group(1)).resolve())
                for m in re.finditer(r"-f\s+(deploy/compose/[^\s\"\\]+)", doc_txt)
            ]
            doc_cmd = ["docker", "compose", "--env-file", str(doc_env)]
            for df in doc_files:
                doc_cmd.extend(["-f", df])
            cfg_doc = run_sub(doc_cmd + ["config", "--services"])
            cfg_doc_mig = run_sub(doc_cmd + ["--profile", "migrate", "config", "--services"])
            svcs_doc = [s.strip() for s in cfg_doc.stdout.splitlines() if s.strip()]
            svcs_doc_mig = [s.strip() for s in cfg_doc_mig.stdout.splitlines() if s.strip()]
            f01_checks[doc_key] = {
                "compose_file_count": len(doc_files),
                "default_services_count": len(svcs_doc),
                "migrate_services_count": len(svcs_doc_mig),
                "pass": len(doc_files) == 8 and len(svcs_doc) == 11 and len(svcs_doc_mig) == 12,
            }

        f01_pass = all(v["pass"] for v in f01_checks.values())
        report["findings_verification"]["F-P4-OPS-01"] = {
            "checks": f01_checks,
            "pass": f01_pass,
        }
        log(f"F-P4-OPS-01 result: pass={f01_pass}")

        # --- F-P4-OPS-02: Verify running container image ID -> `docker image inspect` RepoDigests (match & mismatch) ---
        log("Verifying F-P4-OPS-02: container .Image ID -> docker image inspect RepoDigests (match & wrong digest)...")
        repo_digests_local = img_summary.get("repo_digests") or []
        matching_repo_digest = (
            repo_digests_local[0]
            if repo_digests_local
            else args.app_image
        )
        wrong_repo_digest = "crm-cskh@sha256:" + ("f" * 64)
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
                "isolated-smoke",
                "--expected-image",
                matching_repo_digest,
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
                "isolated-smoke",
                "--expected-image",
                wrong_repo_digest,
            ],
            check=False,
        )
        dig_bad_json = json.loads(dig_bad_proc.stdout)
        f02_pass = (
            dig_ok_proc.returncode == 0
            and dig_ok_json["status"] == "PASS"
            and len(dig_ok_json["container_digest_parity"]["inspected_services"]) == 6
            and dig_bad_proc.returncode == 2
            and dig_bad_json["status"] == "FAIL"
            and len(dig_bad_json["container_digest_parity"]["mismatches"]) == 6
        )
        report["findings_verification"]["F-P4-OPS-02"] = {
            "matching_digest_test": {
                "expected_image": matching_repo_digest,
                "exit_code": dig_ok_proc.returncode,
                "status": dig_ok_json["status"],
                "inspected_services": dig_ok_json["container_digest_parity"]["inspected_services"],
            },
            "wrong_digest_test": {
                "expected_image": wrong_repo_digest,
                "exit_code": dig_bad_proc.returncode,
                "status": dig_bad_json["status"],
                "mismatch_count": len(dig_bad_json["container_digest_parity"]["mismatches"]),
                "mismatches": dig_bad_json["container_digest_parity"]["mismatches"],
            },
            "pass": f02_pass,
        }
        log(f"F-P4-OPS-02 result: pass={f02_pass} (match=PASS/0, wrong=FAIL/2 with 6 mismatches)")

        # --- F-P4-OPS-05 (Scheduler part) + Phase B: Startup grace vs never Complete vs Complete vs latest Failed ---
        log("Phase B & F-P4-OPS-05 (cron): Testing startup grace, never Complete, 4 cron Complete, and latest Failed...")
        clear_cron_logs_py = r"""
import os, frappe
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
frappe.connect()
try:
    frappe.db.sql(
        "DELETE sjl FROM `tabScheduled Job Log` sjl "
        "INNER JOIN `tabScheduled Job Type` sjt ON sjt.name = sjl.scheduled_job_type "
        "WHERE sjt.method LIKE 'crm_cskh.%'"
    )
    frappe.db.commit()
finally:
    frappe.destroy()
"""
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", clear_cron_logs_py],
            )
        )

        # 1) Within startup grace (3600s) -> PASS with 4 jobs in `in_startup_grace_cron_jobs`
        grace_ok_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "isolated-smoke",
                "--site",
                REHEARSAL_SITE,
                "--startup-grace-seconds",
                "3600",
            ]
        )
        grace_ok_json = json.loads(grace_ok_proc.stdout)

        # 2) Past startup grace (0s) when crons have NEVER completed -> MUST FAIL (exit 2)
        never_done_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "isolated-smoke",
                "--site",
                REHEARSAL_SITE,
                "--startup-grace-seconds",
                "0",
            ],
            check=False,
        )
        never_done_json = json.loads(never_done_proc.stdout)

        # 3) Execute all 4 crons -> MUST PASS with `--startup-grace-seconds 0`
        cron_py = r"""
import os, frappe
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
frappe.connect()
try:
    methods = [
        "crm_cskh.jobs.evaluate_sla.evaluate_sla_obligations",
        "crm_cskh.jobs.recompute_kpi.drain_dirty_kpi_scopes",
        "crm_cskh.jobs.run_export.expire_stale_exports",
        "crm_cskh.jobs.recompute_kpi.refresh_current_standard_periods",
    ]
    executed = []
    for m in methods:
        sjt_name = frappe.db.get_value("Scheduled Job Type", {"method": m}, "name")
        if sjt_name:
            frappe.get_doc("Scheduled Job Type", sjt_name).execute()
            executed.append(m)
    frappe.db.commit()
    print("CRON_EXEC=" + ",".join(executed))
finally:
    frappe.destroy()
"""
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", cron_py],
            )
        )

        health_out = tmpdir / "p4_07_health.json"
        run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "isolated-smoke",
                "--site",
                REHEARSAL_SITE,
                "--startup-grace-seconds",
                "0",
                "--environment-label",
                "local-isolated-rehearsal",
                "--out",
                str(health_out),
            ]
        )
        health_json = json.loads(health_out.read_text(encoding="utf-8"))

        # 4) Inject newest `Failed` Scheduled Job Log row -> MUST FAIL even with grace=3600
        inject_failed_sjl_py = r"""
import os, sys, frappe
action = sys.argv[1]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
frappe.connect()
try:
    if action == "inject":
        sjt_name = frappe.db.get_value(
            "Scheduled Job Type",
            {"method": "crm_cskh.jobs.run_export.expire_stale_exports"},
            "name",
        )
        frappe.db.sql(
            "INSERT INTO `tabScheduled Job Log` "
            "(name, creation, modified, modified_by, owner, docstatus, idx, status, scheduled_job_type) "
            "VALUES ('F-OPS-05-SJL-FAIL', DATE_ADD(NOW(), INTERVAL 120 SECOND), DATE_ADD(NOW(), INTERVAL 120 SECOND), "
            "'Administrator', 'Administrator', 0, 0, 'Failed', %s)",
            (sjt_name,),
        )
    else:
        frappe.db.sql("DELETE FROM `tabScheduled Job Log` WHERE name = 'F-OPS-05-SJL-FAIL'")
    frappe.db.commit()
finally:
    frappe.destroy()
"""
        try:
            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_failed_sjl_py,
                        "inject",
                    ],
                )
            )
            latest_failed_proc = run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "check_health_and_invariants.py"),
                    "--env-file",
                    str(env_file),
                    "--mode",
                    "isolated-smoke",
                    "--site",
                    REHEARSAL_SITE,
                    "--startup-grace-seconds",
                    "3600",
                ],
                check=False,
            )
            latest_failed_json = json.loads(latest_failed_proc.stdout)
        finally:
            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_failed_sjl_py,
                        "cleanup",
                    ],
                )
            )

        # --- F-P4-OPS-03: Inject overdue SLA Obligation & stuck Dirty Scope to prove live probe FAIL ---
        log("Verifying F-P4-OPS-03: injecting overdue SLA Obligation + stuck Dirty Scope and verifying probe FAIL...")
        while datetime.now(timezone.utc).second >= 53:
            time.sleep(1)
        inject_f03_py = r"""
import os, sys, frappe
action = sys.argv[1]
sample_wi = sys.argv[2]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
frappe.connect()
try:
    if action == "inject":
        frappe.db.sql(
            "INSERT INTO `tabCSKH SLA Obligation` "
            "(name, creation, modified, modified_by, owner, docstatus, idx, idempotency_key, work_item, obligation_type, "
            "status, is_paused, original_due_at, effective_due_at, policy_snapshot_json, calendar_snapshot_json, audit_events_json) "
            "VALUES ('F-OPS-03-SLA-OVERDUE', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, 'f-ops-03-sla-idem', %s, 'FIRST_ATTEMPT', "
            "'open', 0, DATE_SUB(NOW(), INTERVAL 30 MINUTE), DATE_SUB(NOW(), INTERVAL 30 MINUTE), '{}', '{}', '[]')",
            (sample_wi,),
        )
        frappe.db.sql(
            "INSERT INTO `tabCSKH Dirty Scope` "
            "(name, creation, modified, modified_by, owner, docstatus, idx, scope_key, status, first_dirtied_at) "
            "VALUES ('F-OPS-03-DIRTY-STUCK', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, 'f-ops-03-dirty-scope', 'pending', "
            "DATE_SUB(NOW(), INTERVAL 45 MINUTE))"
        )
    else:
        frappe.db.sql("DELETE FROM `tabCSKH SLA Obligation` WHERE name = 'F-OPS-03-SLA-OVERDUE'")
        frappe.db.sql("DELETE FROM `tabCSKH Dirty Scope` WHERE name = 'F-OPS-03-DIRTY-STUCK'")
    frappe.db.commit()
finally:
    frappe.destroy()
"""
        try:
            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_f03_py,
                        "inject",
                        seed_info["aba_work_item"],
                    ],
                )
            )
            f03_fail_proc = run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "check_health_and_invariants.py"),
                    "--env-file",
                    str(env_file),
                    "--mode",
                    "isolated-smoke",
                    "--site",
                    REHEARSAL_SITE,
                ],
                check=False,
            )
            f03_fail_json = json.loads(f03_fail_proc.stdout)
        finally:
            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_f03_py,
                        "cleanup",
                        seed_info["aba_work_item"],
                    ],
                )
            )

        f03_recover_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "isolated-smoke",
                "--site",
                REHEARSAL_SITE,
            ]
        )
        f03_recover_json = json.loads(f03_recover_proc.stdout)
        f03_inv = f03_fail_json["business_invariants"]
        f03_pass = (
            f03_fail_proc.returncode == 2
            and f03_fail_json["status"] == "FAIL"
            and f03_inv["effective_p3_flags"] == {"p3_sla_enabled": True, "p3_reporting_enabled": True}
            and f03_inv["details"]["INV-04_sla_engine_integrity"]["stale_unevaluated_overdue_sla"] >= 1
            and f03_inv["details"]["INV-05_kpi_snapshot_and_dirty_scope_integrity"]["stuck_dirty_scopes_over_30m"] >= 1
            and f03_recover_proc.returncode == 0
            and f03_recover_json["status"] == "PASS"
        )
        report["findings_verification"]["F-P4-OPS-03"] = {
            "effective_p3_flags": f03_inv["effective_p3_flags"],
            "injected_probe_exit_code": f03_fail_proc.returncode,
            "injected_probe_status": f03_fail_json["status"],
            "injected_failed_invariants": f03_inv["failed_invariants"],
            "injected_stale_overdue_sla": f03_inv["details"]["INV-04_sla_engine_integrity"][
                "stale_unevaluated_overdue_sla"
            ],
            "injected_stuck_dirty_scopes": f03_inv["details"][
                "INV-05_kpi_snapshot_and_dirty_scope_integrity"
            ]["stuck_dirty_scopes_over_30m"],
            "recovered_status": f03_recover_json["status"],
            "pass": f03_pass,
        }
        log(f"F-P4-OPS-03 result: pass={f03_pass}")

        # --- F-P4-OPS-04: Structured QA_PASS_RATE JSON (`null` on 0 denom) & published QA Review by contact_attempt ---
        log("Verifying F-P4-OPS-04: structured QA_PASS_RATE JSON & published QA Review by contact_attempt...")
        inject_f04_py = r"""
import os, sys, frappe
stage = sys.argv[1]
sample_wi = sys.argv[2]
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
frappe.connect()
try:
    if stage == "inject_invalid":
        # 1) False-zero QA_PASS_RATE with reversed JSON keys & float 0.0
        bad_json = '{"metrics":{"QA_PASS_RATE":{"denominator":0,"status":"ok","value":0.0}}}'
        frappe.db.sql(
            "INSERT INTO `tabCSKH KPI Snapshot` "
            "(name, creation, modified, modified_by, owner, docstatus, idx, metrics_json) "
            "VALUES ('F-OPS-04-BAD-SNAP', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, %s)",
            (bad_json,),
        )
        # 2) Verified Contact Attempt without call_log + DRAFT QA Review on it + PUBLISHED QA Review on a DIFFERENT attempt of SAME work_item
        frappe.db.sql(
            "INSERT INTO `tabCSKH Contact Attempt` "
            "(name, creation, modified, modified_by, owner, docstatus, idx, work_item, performed_by, idempotency_key, verification_status, call_log, callback_task, resolved_callback_task) "
            "VALUES ('F-OPS-04-CA-VER', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, %s, 'Administrator', 'f-ops-04-ca-ver', 'verified', NULL, NULL, NULL), "
            "('F-OPS-04-CA-OTHER', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, %s, 'Administrator', 'f-ops-04-ca-other', 'self_reported', NULL, NULL, NULL)",
            (sample_wi, sample_wi),
        )
        frappe.db.sql(
            "INSERT INTO `tabCSKH QA Review` "
            "(name, creation, modified, modified_by, owner, docstatus, idx, work_item, contact_attempt, status) "
            "VALUES ('F-OPS-04-QR-DRAFT', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, %s, 'F-OPS-04-CA-VER', 'draft'), "
            "('F-OPS-04-QR-OTHER-PUB', NOW(), NOW(), 'Administrator', 'Administrator', 0, 0, %s, 'F-OPS-04-CA-OTHER', 'published')",
            (sample_wi, sample_wi),
        )
    elif stage == "publish_matching_attempt":
        frappe.db.sql("DELETE FROM `tabCSKH KPI Snapshot` WHERE name = 'F-OPS-04-BAD-SNAP'")
        frappe.db.sql("UPDATE `tabCSKH QA Review` SET status = 'published' WHERE name = 'F-OPS-04-QR-DRAFT'")
    elif stage == "cleanup":
        frappe.db.sql("DELETE FROM `tabCSKH KPI Snapshot` WHERE name = 'F-OPS-04-BAD-SNAP'")
        frappe.db.sql("DELETE FROM `tabCSKH QA Review` WHERE name IN ('F-OPS-04-QR-DRAFT', 'F-OPS-04-QR-OTHER-PUB')")
        frappe.db.sql("DELETE FROM `tabCSKH Contact Attempt` WHERE name IN ('F-OPS-04-CA-VER', 'F-OPS-04-CA-OTHER')")
    frappe.db.commit()
finally:
    frappe.destroy()
"""
        f04_snap_bad = tmpdir / "f04_snap_bad.json"
        f04_snap_pub = tmpdir / "f04_snap_pub.json"
        try:
            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_f04_py,
                        "inject_invalid",
                        seed_info["aba_work_item"],
                    ],
                )
            )
            run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                    "snapshot-counts",
                    "--env-file",
                    str(env_file),
                    "--site",
                    REHEARSAL_SITE,
                    "--mode",
                    "isolated-smoke",
                    "--out",
                    str(f04_snap_bad),
                ]
            )
            f04_bad_counts = json.loads(f04_snap_bad.read_text(encoding="utf-8"))
            f04_bad_health_proc = run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "check_health_and_invariants.py"),
                    "--env-file",
                    str(env_file),
                    "--mode",
                    "isolated-smoke",
                    "--site",
                    REHEARSAL_SITE,
                ],
                check=False,
            )
            f04_bad_health = json.loads(f04_bad_health_proc.stdout)

            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_f04_py,
                        "publish_matching_attempt",
                        seed_info["aba_work_item"],
                    ],
                )
            )
            run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "staging_https_smoke_and_reconcile.py"),
                    "snapshot-counts",
                    "--env-file",
                    str(env_file),
                    "--site",
                    REHEARSAL_SITE,
                    "--mode",
                    "isolated-smoke",
                    "--out",
                    str(f04_snap_pub),
                ]
            )
            f04_pub_counts = json.loads(f04_snap_pub.read_text(encoding="utf-8"))
            f04_pub_health_proc = run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "check_health_and_invariants.py"),
                    "--env-file",
                    str(env_file),
                    "--mode",
                    "isolated-smoke",
                    "--site",
                    REHEARSAL_SITE,
                ]
            )
            f04_pub_health = json.loads(f04_pub_health_proc.stdout)
        finally:
            run_sub(
                compose_cmd(
                    env_file,
                    [
                        "exec",
                        "-T",
                        "backend",
                        "/home/frappe/frappe-bench/env/bin/python",
                        "-c",
                        inject_f04_py,
                        "cleanup",
                        seed_info["aba_work_item"],
                    ],
                )
            )

        clean_qa_audit = health_json["business_invariants"]["qa_snapshot_audit"]
        f04_pass = (
            clean_qa_audit["qa_zero_denom_null_snapshots"] > 0
            and clean_qa_audit["false_zero_qa_snapshots"] == 0
            and f04_bad_counts["counts"]["cskh_qa_review_published"] == 1
            and f04_bad_counts["invariants"]["unverified_marked_verified"] == 1
            and f04_bad_counts["invariants"]["false_zero_qa_snapshots"] == 1
            and f04_bad_health_proc.returncode == 2
            and f04_pub_counts["counts"]["cskh_qa_review_published"] == 2
            and f04_pub_counts["invariants"]["unverified_marked_verified"] == 0
            and f04_pub_counts["invariants"]["false_zero_qa_snapshots"] == 0
            and f04_pub_health_proc.returncode == 0
            and f04_pub_health["status"] == "PASS"
        )
        report["findings_verification"]["F-P4-OPS-04"] = {
            "seeded_qa_snapshot_audit": clean_qa_audit,
            "when_draft_on_attempt_and_false_zero_injected": {
                "published_qa_reviews_by_contact_attempt": f04_bad_counts["counts"][
                    "cskh_qa_review_published"
                ],
                "unverified_marked_verified": f04_bad_counts["invariants"][
                    "unverified_marked_verified"
                ],
                "false_zero_qa_snapshots": f04_bad_counts["invariants"][
                    "false_zero_qa_snapshots"
                ],
                "probe_exit_code": f04_bad_health_proc.returncode,
                "failed_invariants": f04_bad_health["business_invariants"]["failed_invariants"],
            },
            "when_published_on_matching_contact_attempt_and_clean_json": {
                "published_qa_reviews_by_contact_attempt": f04_pub_counts["counts"][
                    "cskh_qa_review_published"
                ],
                "unverified_marked_verified": f04_pub_counts["invariants"][
                    "unverified_marked_verified"
                ],
                "false_zero_qa_snapshots": f04_pub_counts["invariants"][
                    "false_zero_qa_snapshots"
                ],
                "probe_exit_code": f04_pub_health_proc.returncode,
                "probe_status": f04_pub_health["status"],
            },
            "pass": f04_pass,
        }
        log(f"F-P4-OPS-04 result: pass={f04_pass}")

        # Ephemeral loopback HTTPS webhook server for end-to-end TLS alert + cooldown test
        cert_path, key_path = generate_self_signed_cert_via_backend(env_file, tmpdir)
        received_alerts: list[dict[str, Any]] = []

        class _AlertHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                raw_b = self.rfile.read(n)
                received_alerts.append(json.loads(raw_b.decode("utf-8")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, fmt: str, *a: Any) -> None:
                return

        webhook_port = pick_free_loopback_port(18243)
        httpd = http.server.HTTPServer(("127.0.0.1", webhook_port), _AlertHandler)
        srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        srv_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        httpd.socket = srv_ctx.wrap_socket(httpd.socket, server_side=True)
        srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        srv_thread.start()

        dedup_file = tmpdir / "alert_dedup.json"
        alert_env = os.environ.copy()
        alert_env["SSL_CERT_FILE"] = str(cert_path)
        webhook_url = f"https://127.0.0.1:{webhook_port}/hooks/{webhook_token}"
        try:
            a1_proc = run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "check_health_and_invariants.py"),
                    "--send-test-alert",
                    "--environment-label",
                    "local-isolated-rehearsal",
                    "--operator-contact",
                    "ops-rehearsal@local.test",
                    "--webhook-url",
                    webhook_url,
                    "--dedup-state-file",
                    str(dedup_file),
                ],
                env=alert_env,
            )
            a2_proc = run_sub(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(OPS_DIR / "check_health_and_invariants.py"),
                    "--send-test-alert",
                    "--environment-label",
                    "local-isolated-rehearsal",
                    "--operator-contact",
                    "ops-rehearsal@local.test",
                    "--webhook-url",
                    webhook_url,
                    "--dedup-state-file",
                    str(dedup_file),
                ],
                env=alert_env,
            )
        finally:
            httpd.shutdown()
            httpd.server_close()

        a1_json = json.loads(a1_proc.stdout)["receipt"]
        a2_json = json.loads(a2_proc.stdout)["receipt"]
        alert_drill_ok = (
            a1_json["status"] == "DELIVERED"
            and a2_json["status"] == "SUPPRESSED_COOLDOWN"
            and len(received_alerts) == 1
            and received_alerts[0].get("alert_code") == "ALT-TEST-SYNTHETIC"
            and webhook_token not in json.dumps(received_alerts[0])
        )
        report["phases"]["p4_07_observability_and_invariants"] = {
            "status": "PASS" if (health_json["status"] == "PASS" and alert_drill_ok) else "FAIL",
            "live_probe": health_json,
            "local_https_alert_drill": {
                "first_dispatch": a1_json,
                "second_dispatch_within_cooldown": a2_json,
                "received_count": len(received_alerts),
                "received_sample": received_alerts[0] if received_alerts else None,
                "pass": alert_drill_ok,
            },
        }

        # =====================================================================
        # PHASE C: P4-08 Automated Pre-UAT Role Matrix, 15 Screens & Drill-Down
        # =====================================================================
        log("Phase C (P4-08): Auditing 15 /cskh/* Vietnamese screens, 6 role scopes, and offboarding...")
        users = seed_info["users"]
        period_key = seed_info["period_key"]
        team_a = seed_info["team_a"]
        team_b = seed_info["team_b"]
        sample_wi = seed_info["aba_work_item"]

        _, admin_sid, _ = login_session(http_port, REHEARSAL_SITE, users["admin"], seed_pw, scrubber)
        _, bod_sid, bod_csrf = login_session(http_port, REHEARSAL_SITE, users["bod"], seed_pw, scrubber)
        _, ldr_a_sid, _ = login_session(http_port, REHEARSAL_SITE, users["leader_a"], seed_pw, scrubber)
        _, stf_a_sid, _ = login_session(http_port, REHEARSAL_SITE, users["staff_a"], seed_pw, scrubber)
        _, out_sid, _ = login_session(http_port, REHEARSAL_SITE, users["outside"], seed_pw, scrubber)

        # --- F-P4-OPS-05 (UI + CSRF + Cron consolidated verification) ---
        log("Verifying F-P4-OPS-05: live /cskh/dashboard Vietnamese heading + X-Frappe-CSRF-Token extract/redact...")
        dash_st, _, dash_b = http_req(
            http_port,
            REHEARSAL_SITE,
            "/cskh/dashboard",
            headers={"Cookie": admin_sid},
        )
        dash_html = dash_b.decode("utf-8", errors="replace")
        admin_csrf = p403_tool.extract_csrf_token(dash_html)
        if admin_csrf:
            scrubber.register_secret(admin_csrf, "<REDACTED_CSRF>")
        bad_csrf_st, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.health.ping",
            method="POST",
            headers={"Cookie": admin_sid, "X-Frappe-CSRF-Token": "forged-csrf-token-000"},
            body=b"",
        )
        good_csrf_st, _, good_csrf_b = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.health.ping",
            method="POST",
            headers={"Cookie": admin_sid, "X-Frappe-CSRF-Token": admin_csrf},
            body=b"",
        )
        live_vi_csrf_eval = p403_tool.evaluate_vi_ui_and_csrf_check(
            dash_code=dash_st,
            dash_html=dash_html,
            csrf_token=admin_csrf,
            bad_csrf_code=bad_csrf_st,
            good_csrf_code=good_csrf_st,
            good_csrf_bytes=good_csrf_b,
        )
        neg_en_eval = p403_tool.evaluate_vi_ui_and_csrf_check(
            dash_code=200,
            dash_html='<html><body><h1 class="cskh-title">Distribution dashboard</h1></body></html>',
            csrf_token=admin_csrf,
            bad_csrf_code=bad_csrf_st,
            good_csrf_code=good_csrf_st,
            good_csrf_bytes=good_csrf_b,
        )
        sample_csrf_log = p403_tool.redact_sensitive(
            f'X-Frappe-CSRF-Token: {admin_csrf} | {{"X-Frappe-CSRF-Token": "{admin_csrf}"}}',
            csrf_tokens=[admin_csrf],
        )
        log(f"F-P4-OPS-05 scrubbed header/json sample: {sample_csrf_log}")

        f05_pass = (
            live_vi_csrf_eval["pass"] is True
            and live_vi_csrf_eval["contains_vi_heading"] is True
            and live_vi_csrf_eval["csrf_token_extracted"] is True
            and neg_en_eval["pass"] is False
            and admin_csrf not in sample_csrf_log
            and grace_ok_proc.returncode == 0
            and grace_ok_json["status"] == "PASS"
            and len(grace_ok_json["queues_and_scheduler"]["in_startup_grace_cron_jobs"]) >= 2
            and never_done_proc.returncode == 2
            and never_done_json["status"] == "FAIL"
            and len(never_done_json["queues_and_scheduler"]["never_completed_cron_jobs"]) >= 2
            and health_json["status"] == "PASS"
            and latest_failed_proc.returncode == 2
            and latest_failed_json["status"] == "FAIL"
            and latest_failed_json["queues_and_scheduler"]["failed_latest_cron_jobs"]
            == ["crm_cskh.jobs.run_export.expire_stale_exports"]
        )
        report["findings_verification"]["F-P4-OPS-05"] = {
            "live_dashboard_vi_and_csrf": live_vi_csrf_eval,
            "negative_missing_vi_heading_check": neg_en_eval,
            "redacted_x_frappe_csrf_sample": sample_csrf_log,
            "cron_in_startup_grace": {
                "exit_code": grace_ok_proc.returncode,
                "status": grace_ok_json["status"],
                "in_startup_grace_cron_jobs": grace_ok_json["queues_and_scheduler"][
                    "in_startup_grace_cron_jobs"
                ],
            },
            "cron_never_completed_past_grace": {
                "exit_code": never_done_proc.returncode,
                "status": never_done_json["status"],
                "never_completed_cron_jobs": never_done_json["queues_and_scheduler"][
                    "never_completed_cron_jobs"
                ],
            },
            "cron_after_4_jobs_complete": {
                "status": health_json["queues_and_scheduler"]["status"],
                "cron_last_complete_lag_seconds": health_json["queues_and_scheduler"][
                    "cron_last_complete_lag_seconds"
                ],
            },
            "cron_latest_run_failed": {
                "exit_code": latest_failed_proc.returncode,
                "status": latest_failed_json["status"],
                "failed_latest_cron_jobs": latest_failed_json["queues_and_scheduler"][
                    "failed_latest_cron_jobs"
                ],
            },
            "pass": f05_pass,
        }
        log(f"F-P4-OPS-05 result: pass={f05_pass}")

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
        route_results: dict[str, dict[str, Any]] = {}
        for rt in routes_15:
            cookie = stf_a_sid if rt == "/cskh/scorecard" else admin_sid
            rc, _, rbytes = http_req(http_port, REHEARSAL_SITE, rt, headers={"Cookie": cookie})
            rhtml = rbytes.decode("utf-8", errors="replace")
            has_lang_vi = 'lang="vi"' in rhtml or "lang='vi'" in rhtml
            route_results[rt.split("?", 1)[0]] = {
                "http_status": rc,
                "html_lang_vi": has_lang_vi,
                "pass": rc == 200 and has_lang_vi,
            }

        # BOD checks: overview 200, golden employee QA=N/A (86.47 provisional), drill-down 0 raw phones, recompute 403
        q_params = urllib.parse.urlencode({"period_type": "monthly", "period_key": period_key})
        bod_ov_code, _, bod_ov_b = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
            headers={"Cookie": bod_sid, "Accept": "application/json"},
        )
        bod_emp_code, _, bod_emp_b = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_employee_quality?{q_params}&page_size=50",
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
        dd_params = urllib.parse.urlencode(
            {
                "metric_code": "FIRST_ATTEMPT_SLA_RATE",
                "period_type": "monthly",
                "period_key": period_key,
                "include_pii": 0,
            }
        )
        bod_dd_code, _, bod_dd_b = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_metric_drilldown?{dd_params}",
            headers={"Cookie": bod_sid, "Accept": "application/json"},
        )
        bod_dd_text = bod_dd_b.decode("utf-8", errors="replace")
        raw_phones_in_dd = len(PHONE_RE.findall(bod_dd_text))

        bod_recompute_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.reporting.request_recompute",
            method="POST",
            headers={
                "Cookie": bod_sid,
                "X-Frappe-CSRF-Token": bod_csrf,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=json.dumps({"period_type": "monthly", "period_key": period_key, "reason": "test"}).encode("utf-8"),
        )

        # Leader A: Team A -> 200, Team B -> 403
        ldr_own_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_employee_quality?{q_params}&team={urllib.parse.quote(team_a)}",
            headers={"Cookie": ldr_a_sid, "Accept": "application/json"},
        )
        ldr_other_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_employee_quality?{q_params}&team={urllib.parse.quote(team_b)}",
            headers={"Cookie": ldr_a_sid, "Accept": "application/json"},
        )

        # Staff A1: own customers & scorecard -> 200; pool_list & executive_overview -> 403
        stf_my_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.customers.my_list",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
        stf_sc_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_my_scorecard?{q_params}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
        stf_pool_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.customers.pool_list",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
        stf_exec_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )

        # Outside user -> 403
        out_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.customers.my_list",
            headers={"Cookie": out_sid, "Accept": "application/json"},
        )

        # Offboarding immediate session rejection check on staff_c
        _, stf_c_sid, _ = login_session(http_port, REHEARSAL_SITE, users["staff_c"], seed_pw, scrubber)
        stf_c_before, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.customers.my_list",
            headers={"Cookie": stf_c_sid, "Accept": "application/json"},
        )
        toggle_user_py = r"""
import os, sys, frappe
user_email, enabled_val = sys.argv[1], int(sys.argv[2])
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
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
        subprocess.run(
            compose_cmd(
                env_file,
                [
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    toggle_user_py,
                    users["staff_c"],
                    "0",
                ],
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        stf_c_disabled_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.customers.my_list",
            headers={"Cookie": stf_c_sid, "Accept": "application/json"},
        )
        subprocess.run(
            compose_cmd(
                env_file,
                [
                    "exec",
                    "-T",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    toggle_user_py,
                    users["staff_c"],
                    "1",
                ],
            ),
            check=True,
            capture_output=True,
            text=True,
        )

        all_15_ok = all(v["pass"] for v in route_results.values())
        role_checks_ok = (
            all_15_ok
            and bod_ov_code == 200
            and bod_emp_code == 200
            and bool(golden_row)
            and bod_dd_code == 200
            and raw_phones_in_dd == 0
            and bod_recompute_code == 403
            and ldr_own_code == 200
            and ldr_other_code == 403
            and stf_my_code == 200
            and stf_sc_code == 200
            and stf_pool_code == 403
            and stf_exec_code == 403
            and out_code == 403
            and stf_c_before == 200
            and stf_c_disabled_code in (401, 403)
        )
        report["phases"]["p4_08_pre_uat_role_and_vi_audit"] = {
            "status": "PASS" if role_checks_ok else "FAIL",
            "routes_15_vi_audit": route_results,
            "role_scope_checks": {
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
        }

        # =====================================================================
        # PHASE D: P4-10 Tier 1 Kill Switch & Tier 4 Backup + Delta + Restore
        # =====================================================================
        log("Phase D (P4-10): Rehearsing Tier 1 kill switch and Tier 4 backup/delta/restore...")
        run_sub(
            compose_cmd(
                env_file,
                [
                    "exec",
                    "-T",
                    "backend",
                    "bench",
                    "--site",
                    REHEARSAL_SITE,
                    "set-config",
                    "cskh_p3_enabled",
                    "false",
                ],
            )
        )
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "bench", "--site", REHEARSAL_SITE, "clear-cache"],
            )
        )
        kill_p3_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
            headers={"Cookie": bod_sid, "Accept": "application/json"},
        )
        kill_p1_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            "/api/method/crm_cskh.api.customers.my_list",
            headers={"Cookie": stf_a_sid, "Accept": "application/json"},
        )
        run_sub(
            compose_cmd(
                env_file,
                [
                    "exec",
                    "-T",
                    "backend",
                    "bench",
                    "--site",
                    REHEARSAL_SITE,
                    "set-config",
                    "cskh_p3_enabled",
                    "true",
                ],
            )
        )
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "bench", "--site", REHEARSAL_SITE, "clear-cache"],
            )
        )
        recover_p3_code, _, _ = http_req(
            http_port,
            REHEARSAL_SITE,
            f"/api/method/crm_cskh.api.reporting.get_executive_overview?{q_params}",
            headers={"Cookie": bod_sid, "Accept": "application/json"},
        )
        tier1_ok = kill_p3_code == 503 and kill_p1_code == 200 and recover_p3_code == 200

        # Tier 4 backup -> post-backup mutation -> --since-utc delta -> restore + migrator -> reconcile
        backup_py = r"""
import glob, json, os, subprocess
from datetime import datetime, timezone
os.chdir("/home/frappe/frappe-bench")
subprocess.check_call(["bench", "--site", "p3-test.localhost", "backup", "--with-files"], stdout=subprocess.DEVNULL)
bdir = "/home/frappe/frappe-bench/sites/p3-test.localhost/private/backups"
sql_f = sorted(glob.glob(f"{bdir}/*-database.sql.gz"))[-1]
pub_f = sorted(glob.glob(f"{bdir}/*-files.tar"))[-1]
priv_f = sorted(glob.glob(f"{bdir}/*-private-files.tar"))[-1]
db_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print("BACKUP_JSON=" + json.dumps({"db_now": db_now, "sql": sql_f, "pub": pub_f, "priv": priv_f}))
"""
        bk_proc = run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", backup_py],
            )
        )
        bk_info = json.loads(
            [ln.split("=", 1)[1] for ln in bk_proc.stdout.splitlines() if ln.startswith("BACKUP_JSON=")][-1]
        )
        time.sleep(1.5)

        mutate_py = r"""
import os, frappe
os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="p3-test.localhost", sites_path=".")
frappe.connect()
try:
    frappe.get_doc({
        "doctype": "CRM Lead",
        "first_name": "PostBackup",
        "last_name": "DeltaCheck",
        "mobile_no": "0900099999",
        "email": "post-backup-delta@example.invalid",
    }).insert(ignore_permissions=True)
    frappe.db.commit()
finally:
    frappe.destroy()
"""
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", mutate_py],
            )
        )

        pre_restore_delta_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "isolated-smoke",
                "--site",
                REHEARSAL_SITE,
                "--since-utc",
                bk_info["db_now"],
            ]
        )
        pre_restore_delta = json.loads(pre_restore_delta_proc.stdout)["data_loss_window_delta"]

        t_rest = time.monotonic()
        run_sub(compose_cmd(env_file, ["stop", "queue-short", "queue-long", "scheduler"]))
        restore_py = r"""
import json, subprocess, sys
payload = json.loads(sys.stdin.read())
subprocess.check_call([
    "bench", "--site", "p3-test.localhost", "--force", "restore", payload["sql"],
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
            input=json.dumps({**bk_info, "db_pw": db_root_pw}),
        )
        run_sub(
            compose_cmd(env_file, ["run", "--rm", "-e", "MIGRATE_SITES=true", "migrator"], profile_migrate=True),
            env=mig_env,
        )
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "bench", "--site", REHEARSAL_SITE, "enable-scheduler"],
            )
        )
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "bench", "--site", REHEARSAL_SITE, "clear-cache"],
            )
        )
        run_sub(compose_cmd(env_file, ["up", "-d", "queue-short", "queue-long", "scheduler"]))
        for _ in range(30):
            ps_out = subprocess.run(
                compose_cmd(env_file, ["ps", "--format", "json"]),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            rows = p403_tool._parse_compose_ps_json(ps_out)
            by_svc = {r["Service"]: r for r in rows}
            if all(
                by_svc.get(s, {}).get("State") == "running"
                and by_svc.get(s, {}).get("Health") == "healthy"
                for s in ("queue-short", "queue-long", "scheduler")
            ):
                break
            time.sleep(2)
        restore_sec = round(time.monotonic() - t_rest, 2)

        # Re-trigger cron logs after restore (restore resets DB back to backup point)
        run_sub(
            compose_cmd(
                env_file,
                ["exec", "-T", "backend", "/home/frappe/frappe-bench/env/bin/python", "-c", cron_py],
            )
        )

        post_restore_proc = run_sub(
            [
                sys.executable,
                "-X",
                "utf8",
                str(OPS_DIR / "check_health_and_invariants.py"),
                "--env-file",
                str(env_file),
                "--mode",
                "isolated-smoke",
                "--site",
                REHEARSAL_SITE,
                "--since-utc",
                bk_info["db_now"],
            ]
        )
        post_restore_json = json.loads(post_restore_proc.stdout)
        tier4_ok = (
            pre_restore_delta["new_leads"] == 1
            and post_restore_json["data_loss_window_delta"]["new_leads"] == 0
            and post_restore_json["business_invariants"]["status"] == "PASS"
        )

        report["phases"]["p4_10_rollback_and_restore_rehearsal"] = {
            "status": "PASS" if (tier1_ok and tier4_ok) else "FAIL",
            "tier1_kill_switch": {
                "p3_overview_when_disabled_http": kill_p3_code,
                "p1_my_customers_when_p3_disabled_http": kill_p1_code,
                "p3_overview_after_reenable_http": recover_p3_code,
                "pass": tier1_ok,
            },
            "tier4_backup_delta_and_restore": {
                "pre_restore_delta": pre_restore_delta,
                "post_restore_delta": post_restore_json["data_loss_window_delta"],
                "post_restore_invariants_status": post_restore_json["business_invariants"]["status"],
                "restore_and_migrate_seconds": restore_sec,
                "pass": tier4_ok,
            },
        }

    except subprocess.CalledProcessError as exc:
        log(f"Subprocess failed (exit={exc.returncode}): stdout={exc.stdout!r} stderr={exc.stderr!r}")
        raise
    finally:
        if stack_started:
            log(f"Tearing down isolated rehearsal stack {project_name} (--volumes --remove-orphans)...")
            subprocess.run(
                compose_cmd(env_file, ["down", "--volumes", "--remove-orphans"], profile_migrate=True),
                capture_output=True,
                text=True,
            )
        shutil.rmtree(tmpdir, ignore_errors=True)

    leftover_proj = smoke_lib.inspect_project_resources(project_name)
    host_after = smoke_lib.snapshot_host_docker_resources()
    leaked_volumes = sorted(host_after["volumes"] - host_before["volumes"])
    leaked_containers = sorted(host_after["containers"] - host_before["containers"])
    leaked_networks = sorted(host_after["networks"] - host_before["networks"])
    cleanup_ok = (
        not any(leftover_proj.values())
        and not leaked_volumes
        and not leaked_containers
        and not leaked_networks
    )
    report["cleanup_verification"] = {
        "project_leftovers": leftover_proj,
        "host_leaked_volumes": leaked_volumes,
        "host_leaked_containers": leaked_containers,
        "host_leaked_networks": leaked_networks,
        "pass": cleanup_ok,
    }

    findings_map = report.get("findings_verification", {})
    all_findings_ok = len(findings_map) == 5 and all(
        bool(v.get("pass")) for v in findings_map.values()
    )
    all_phases_ok = (
        len(report["phases"]) == 4
        and all(p.get("status") == "PASS" for p in report["phases"].values())
        and all_findings_ok
        and cleanup_ok
    )
    report["status"] = "PASS" if all_phases_ok else "FAIL"
    log(
        f"Rehearsal complete: status={report['status']}, findings_pass={all_findings_ok}, cleanup_pass={cleanup_ok}"
    )

    scrubbed_json_str = p403_tool.redact_sensitive(
        scrubber.scrub(json.dumps(report, indent=2, ensure_ascii=False)) + "\n"
    )
    scrubbed_log_str = p403_tool.redact_sensitive(scrubber.scrub("\n".join(log_lines)) + "\n")

    for forbidden in (db_root_pw, admin_pw, seed_pw, webhook_token):
        if forbidden and (forbidden in scrubbed_json_str or forbidden in scrubbed_log_str):
            raise RuntimeError("Secret leak detected in rehearsal output; aborting write!")

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(scrubbed_json_str, encoding="utf-8", newline="\n")
    args.out_log.write_text(scrubbed_log_str, encoding="utf-8", newline="\n")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
