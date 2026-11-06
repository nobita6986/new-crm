#!/usr/bin/env python3
"""Hermetic clean-site & HTTP frontend asset verifier for the P4-01 custom image (G02).

Boots an isolated frappe_docker-style stack (`deploy/images/compose.verify.yaml`)
using `APP_IMAGE`, provisions a brand-new Frappe site on empty volumes, installs
`crm` + `crm_cskh`, runs `migrate` twice, verifies all 3 apps via `list-apps`,
checks upstream Git SHAs and embedded dependency locks, and probes static & dynamic
routes over HTTP through `frontend` (nginx) -> `backend` (gunicorn).

Ephemeral credentials exist only in a temporary directory outside the repository
and are destroyed in `finally` together with all containers and volumes.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGES_DIR = Path(__file__).resolve().parent
LOCK_FILE = IMAGES_DIR / "release-lock.json"
COMPOSE_FILE = IMAGES_DIR / "compose.verify.yaml"


def run_cmd(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
        check=check,
        timeout=timeout,
    )


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener(
    *, follow_redirects: bool = True, cookie_jar: http.cookiejar.CookieJar | None = None
):
    handlers: list[urllib.request.BaseHandler] = []
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    if not follow_redirects:
        handlers.append(NoRedirectHandler())
    return urllib.request.build_opener(*handlers)


def http_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    host_header: str,
    method: str = "GET",
    form_data: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, str], bytes]:
    body_bytes = None
    headers = {"Host": host_header, "Accept-Language": "vi-VN,vi;q=0.9"}
    if form_data is not None:
        body_bytes = urllib.parse.urlencode(form_data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return (
                resp.status,
                {k.lower(): v for k, v in resp.headers.items()},
                resp.read(),
            )
    except urllib.error.HTTPError as exc:
        data = exc.read() if exc.fp else b""
        hdrs = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, hdrs, data


def wait_for_http_ping(
    port: int, site_name: str, timeout_sec: int = 90
) -> dict[str, object]:
    opener = make_opener(follow_redirects=False)
    deadline = time.monotonic() + timeout_sec
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            status, headers, raw = http_request(
                opener,
                f"http://127.0.0.1:{port}/api/method/ping",
                host_header=site_name,
                timeout=5,
            )
            if status == 200:
                payload = json.loads(raw.decode("utf-8"))
                if payload.get("message") == "pong":
                    return {
                        "status": status,
                        "message": payload.get("message"),
                        "x_frame_options": headers.get("x-frame-options"),
                        "x_content_type_options": headers.get("x-content-type-options"),
                        "strict_transport_security": headers.get(
                            "strict-transport-security"
                        ),
                    }
            last_error = f"HTTP {status}: {raw[:120]!r}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(
        f"Frontend->Backend ping did not become ready within {timeout_sec}s: {last_error}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-image",
        required=True,
        help="APP_IMAGE ref (tag, local sha256 ID, or repo@sha256)",
    )
    parser.add_argument(
        "--pull-policy", default="never", choices=("never", "missing", "always")
    )
    parser.add_argument("--port", type=int, default=18780)
    parser.add_argument("--site-name", default="p4-clean-verify.localhost")
    parser.add_argument(
        "--out",
        default=str(IMAGES_DIR / "evidence" / "clean-site-verify.json"),
        help="Output JSON verification report path",
    )
    args = parser.parse_args()

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    expected_frappe_sha = lock["apps"]["frappe"]["commit_sha"]
    expected_crm_sha = lock["apps"]["crm"]["commit_sha"]
    expected_apps_tree = lock["apps_tree_sha"]
    expected_cskh_tree = lock["crm_cskh_tree_sha"]
    expected_load_order = lock["apps"]["load_order"]

    project_name = f"cskh-p4-verify-{secrets.token_hex(4)}"
    db_root_pw = secrets.token_hex(24)
    admin_pw = secrets.token_hex(24)
    started_at = datetime.now(timezone.utc).isoformat()
    assertions: list[dict[str, object]] = []

    def record(check_id: str, passed: bool, expected: object, actual: object) -> None:
        assertions.append(
            {
                "id": check_id,
                "verdict": "PASS" if passed else "FAIL",
                "expected": expected,
                "actual": actual,
            }
        )
        if not passed:
            raise AssertionError(
                f"Check {check_id} FAILED: expected={expected!r} actual={actual!r}"
            )

    with tempfile.TemporaryDirectory(prefix="cskh-p4-verify-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        db_secret_file = tmp_dir / "db_root_password.txt"
        db_secret_file.write_text(db_root_pw, encoding="utf-8")

        compose_env = {
            "COMPOSE_PROJECT_NAME": project_name,
            "APP_IMAGE": args.app_image,
            "PULL_POLICY": args.pull_policy,
            "SITE_NAME": args.site_name,
            "VERIFY_HTTP_PORT": str(args.port),
            "DB_PASSWORD_SECRETS_FILE": str(db_secret_file),
            "MARIADB_IMAGE": lock["infra_images"]["mariadb"],
            "REDIS_IMAGE": lock["infra_images"]["redis"],
        }
        compose_base = [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "-p",
            project_name,
        ]

        try:
            # 1. Inspect image metadata before booting containers
            inspect_res = run_cmd(
                ["docker", "image", "inspect", args.app_image], env=compose_env
            )
            img_info = json.loads(inspect_res.stdout)[0]
            labels = img_info.get("Config", {}).get("Labels") or {}
            record(
                "image.user_non_root",
                img_info.get("Config", {}).get("User") == "frappe",
                "frappe",
                img_info.get("Config", {}).get("User"),
            )
            record(
                "image.label.frappe_sha",
                labels.get("io.cskh.frappe_sha") == expected_frappe_sha,
                expected_frappe_sha,
                labels.get("io.cskh.frappe_sha"),
            )
            record(
                "image.label.crm_sha",
                labels.get("io.cskh.crm_sha") == expected_crm_sha,
                expected_crm_sha,
                labels.get("io.cskh.crm_sha"),
            )
            record(
                "image.label.apps_tree_sha",
                labels.get("io.cskh.apps_tree_sha") == expected_apps_tree,
                expected_apps_tree,
                labels.get("io.cskh.apps_tree_sha"),
            )
            record(
                "image.label.crm_cskh_tree_sha",
                labels.get("io.cskh.crm_cskh_tree_sha") == expected_cskh_tree,
                expected_cskh_tree,
                labels.get("io.cskh.crm_cskh_tree_sha"),
            )

            # 2. Boot isolated verification topology (zero /src bind-mounts)
            run_cmd([*compose_base, "up", "-d"], env=compose_env, timeout=300)

            # Verify container mounts (no /src or host bind-mount) & port exposure boundaries
            ps_res = run_cmd([*compose_base, "ps", "--format", "json"], env=compose_env)
            ps_rows = [
                json.loads(line) for line in ps_res.stdout.splitlines() if line.strip()
            ]
            published_by_service: dict[str, list[str]] = {}
            app_bind_mounts: dict[str, list[str]] = {}
            for row in ps_rows:
                svc = row.get("Service", "")
                cid = row.get("ID", "")
                pubs = [
                    f"{p.get('URL')}:{p.get('PublishedPort')}->{p.get('TargetPort')}"
                    for p in (row.get("Publishers") or [])
                    if p.get("PublishedPort")
                ]
                published_by_service[svc] = pubs
                if (
                    svc
                    in ("backend", "frontend", "websocket", "queue-short", "scheduler")
                    and cid
                ):
                    c_insp = json.loads(
                        run_cmd(["docker", "inspect", cid], env=compose_env).stdout
                    )[0]
                    binds = [
                        f"{m.get('Source')}->{m.get('Destination')}"
                        for m in (c_insp.get("Mounts") or [])
                        if m.get("Type") == "bind"
                    ]
                    if binds:
                        app_bind_mounts[svc] = binds
            non_frontend_published = {
                k: v for k, v in published_by_service.items() if k != "frontend" and v
            }
            record(
                "topology.no_host_bind_mounts",
                app_bind_mounts == {},
                {},
                app_bind_mounts,
            )
            record(
                "topology.internal_services_unexposed",
                non_frontend_published == {},
                {},
                non_frontend_published,
            )
            record(
                "topology.frontend_loopback_only",
                published_by_service.get("frontend")
                == [f"127.0.0.1:{args.port}->8080"],
                [f"127.0.0.1:{args.port}->8080"],
                published_by_service.get("frontend"),
            )

            # 3. Create clean site + install crm + install crm_cskh + migrate x2 (capture logs for schema/module audit)
            def exec_backend(
                shell_cmd: str, extra_env: dict[str, str] | None = None
            ) -> subprocess.CompletedProcess[str]:
                env_flags: list[str] = []
                for k, v in (extra_env or {}).items():
                    env_flags.extend(["-e", f"{k}={v}"])
                return run_cmd(
                    [
                        *compose_base,
                        "exec",
                        "-T",
                        *env_flags,
                        "backend",
                        "bash",
                        "-lc",
                        shell_cmd,
                    ],
                    env=compose_env,
                    timeout=600,
                )

            secret_env = {
                "VERIFY_DB_ROOT_PW": db_root_pw,
                "VERIFY_ADMIN_PW": admin_pw,
                "VERIFY_SITE": args.site_name,
            }
            exec_backend(
                "set -euo pipefail; cd /home/frappe/frappe-bench; "
                "bench new-site --no-mariadb-socket --mariadb-user-host-login-scope=% "
                '--db-root-password "$VERIFY_DB_ROOT_PW" --admin-password "$VERIFY_ADMIN_PW" "$VERIFY_SITE"; '
                'bench --site "$VERIFY_SITE" set-config time_zone Asia/Ho_Chi_Minh; '
                'bench --site "$VERIFY_SITE" set-config default_language vi',
                secret_env,
            )
            exec_backend(
                'set -euo pipefail; cd /home/frappe/frappe-bench; bench --site "$VERIFY_SITE" install-app crm',
                secret_env,
            )
            install_cskh_res = exec_backend(
                'set -euo pipefail; cd /home/frappe/frappe-bench; bench --site "$VERIFY_SITE" install-app crm_cskh',
                secret_env,
            )
            migrate_1_res = exec_backend(
                'set -euo pipefail; cd /home/frappe/frappe-bench; bench --site "$VERIFY_SITE" migrate',
                secret_env,
            )
            migrate_2_res = exec_backend(
                'set -euo pipefail; cd /home/frappe/frappe-bench; bench --site "$VERIFY_SITE" migrate; '
                'bench --site "$VERIFY_SITE" execute frappe.db.set_single_value --args \'["System Settings", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"}]\'; '
                'bench --site "$VERIFY_SITE" execute frappe.db.set_value --args \'["User", "Administrator", {"language": "vi", "time_zone": "Asia/Ho_Chi_Minh"}]\'; '
                'bench --site "$VERIFY_SITE" clear-cache',
                secret_env,
            )

            # Restart backend so preloaded Gunicorn workers pick up the newly created site cleanly
            run_cmd(
                [
                    *compose_base,
                    "restart",
                    "backend",
                    "websocket",
                    "queue-short",
                    "scheduler",
                ],
                env=compose_env,
                timeout=120,
            )

            # 4. Inspect runtime bench state + CSKH Export Job schema/module audit inside container
            probe_py = (
                "import json, subprocess, frappe; "
                "from pathlib import Path; "
                f"site = {args.site_name!r}; "
                "list_apps = json.loads(subprocess.check_output(['bench', '--site', site, 'list-apps', '--format', 'json'], text=True)); "
                "apps_txt = [x.strip() for x in Path('/home/frappe/frappe-bench/sites/apps.txt').read_text().splitlines() if x.strip()]; "
                "f_sha = subprocess.check_output(['git', '-C', '/home/frappe/frappe-bench/apps/frappe', 'rev-parse', 'HEAD'], text=True).strip(); "
                "c_sha = subprocess.check_output(['git', '-C', '/home/frappe/frappe-bench/apps/crm', 'rev-parse', 'HEAD'], text=True).strip(); "
                "crm_modified = [x.strip() for x in subprocess.check_output(['git', '-C', '/home/frappe/frappe-bench/apps/crm', 'diff', '--name-only'], text=True).splitlines() if x.strip()]; "
                "frappe_navbar = Path('/home/frappe/frappe-bench/apps/frappe/frappe/templates/includes/navbar/navbar.html').read_text(); "
                "frappe_doc = Path('/home/frappe/frappe-bench/apps/frappe/frappe/templates/doc.html').read_text(); "
                "manifest = json.loads(Path('/home/frappe/frappe-bench/release-metadata/build-manifest.json').read_text()); "
                "assets_map = json.loads(Path('/home/frappe/frappe-bench/sites/assets/assets.json').read_text()); "
                "modules_txt = [x.strip() for x in Path('/home/frappe/frappe-bench/apps/crm_cskh/crm_cskh/modules.txt').read_text().splitlines() if x.strip()]; "
                "export_json = json.loads(Path('/home/frappe/frappe-bench/apps/crm_cskh/crm_cskh/crm_cskh/doctype/cskh_export_job/cskh_export_job.json').read_text()); "
                "frappe.init(site=site, sites_path='/home/frappe/frappe-bench/sites'); "
                "frappe.connect(); "
                "cskh_rows = frappe.get_all('DocType', filters=[['name', 'like', 'CSKH %']], fields=['name', 'module'], order_by='name asc'); "
                "mod_defs = frappe.get_all('Module Def', filters=[['app_name', '=', 'crm_cskh']], fields=['name', 'module_name', 'app_name']); "
                "all_mod_crm_cskh = frappe.db.exists('Module Def', 'Crm Cskh'); "
                "export_table_exists = bool(frappe.db.sql(\"SHOW TABLES LIKE 'tabCSKH Export Job'\")); "
                "dev_mode = bool(frappe.conf.get('developer_mode')); "
                "frappe.destroy(); "
                "print(json.dumps({'list_apps': list_apps.get(site), 'apps_txt': apps_txt, 'frappe_sha': f_sha, 'crm_sha': c_sha, 'crm_modified': crm_modified, 'frappe_jinja_patched': ('_(\\'Toggle navigation\\')|e' in frappe_navbar and '_(\\'Toggle navigation\\')|e' in frappe_doc), 'manifest': manifest, 'cskh_doctypes_rows': cskh_rows, 'module_defs_for_app': mod_defs, 'module_def_crm_cskh_titlecase_exists': bool(all_mod_crm_cskh), 'modules_txt': modules_txt, 'export_job_json_module': export_json.get('module'), 'export_job_table_exists': export_table_exists, 'developer_mode': dev_mode, 'desk_js': assets_map.get('desk.bundle.js'), 'desk_css': assets_map.get('desk.bundle.css'), 'web_js': assets_map.get('frappe-web.bundle.js')}))"
            )
            probe_res = run_cmd(
                [
                    *compose_base,
                    "exec",
                    "-T",
                    "-e",
                    "FRAPPE_STREAM_LOGGING=1",
                    "backend",
                    "/home/frappe/frappe-bench/env/bin/python",
                    "-c",
                    probe_py,
                ],
                env=compose_env,
                timeout=60,
            )
            runtime_info = json.loads(probe_res.stdout.strip().splitlines()[-1])

            record(
                "site.list_apps",
                runtime_info["list_apps"] == expected_load_order,
                expected_load_order,
                runtime_info["list_apps"],
            )
            record(
                "site.apps_txt_order",
                runtime_info["apps_txt"] == expected_load_order,
                expected_load_order,
                runtime_info["apps_txt"],
            )
            record(
                "runtime.frappe_sha",
                runtime_info["frappe_sha"] == expected_frappe_sha,
                expected_frappe_sha,
                runtime_info["frappe_sha"],
            )
            record(
                "runtime.crm_sha",
                runtime_info["crm_sha"] == expected_crm_sha,
                expected_crm_sha,
                runtime_info["crm_sha"],
            )
            record(
                "runtime.crm_no_vite_dev_patch",
                set(runtime_info["crm_modified"]).issubset(
                    {"frontend/auto-imports.d.ts"}
                ),
                "only auto-generated frontend/auto-imports.d.ts or empty (no Vite DEV patch)",
                runtime_info["crm_modified"],
            )
            record(
                "runtime.frappe_jinja_vi_patched",
                runtime_info["frappe_jinja_patched"] is True,
                True,
                runtime_info["frappe_jinja_patched"],
            )
            record(
                "runtime.cskh_tree_sha",
                runtime_info["manifest"]["apps"]["crm_cskh"]["crm_cskh_tree_sha"]
                == expected_cskh_tree,
                expected_cskh_tree,
                runtime_info["manifest"]["apps"]["crm_cskh"]["crm_cskh_tree_sha"],
            )
            cskh_by_name = {
                r["name"]: r["module"] for r in runtime_info["cskh_doctypes_rows"]
            }
            record(
                "site.cskh_doctypes_synced",
                len(cskh_by_name) >= 22,
                ">=22 (23 defined in source)",
                {
                    "synced_count": len(cskh_by_name),
                    "export_job_in_doctype": "CSKH Export Job" in cskh_by_name,
                    "export_job_module_in_db": cskh_by_name.get("CSKH Export Job"),
                    "export_job_table_exists": runtime_info["export_job_table_exists"],
                },
            )
            record(
                "site.developer_mode_disabled",
                runtime_info["developer_mode"] is False,
                False,
                runtime_info["developer_mode"],
            )

            # 5. Probe HTTP routes and frontend static assets via `frontend` nginx -> `backend` gunicorn
            ping_info = wait_for_http_ping(args.port, args.site_name, timeout_sec=90)
            record(
                "http.ping",
                ping_info["message"] == "pong",
                "pong",
                ping_info["message"],
            )
            record(
                "http.security_header.x_frame_options",
                ping_info["x_frame_options"] == "SAMEORIGIN",
                "SAMEORIGIN",
                ping_info["x_frame_options"],
            )
            record(
                "http.security_header.nosniff",
                ping_info["x_content_type_options"] == "nosniff",
                "nosniff",
                ping_info["x_content_type_options"],
            )

            base_url = f"http://127.0.0.1:{args.port}"
            anon_no_redirect = make_opener(follow_redirects=False)

            # Frappe Desk & Web static bundles served directly by nginx `/assets`
            for bundle_key in ("desk_js", "desk_css", "web_js"):
                asset_path = str(runtime_info[bundle_key])
                st, _hdrs, body = http_request(
                    anon_no_redirect,
                    f"{base_url}{asset_path}",
                    host_header=args.site_name,
                )
                record(
                    f"http.asset.{bundle_key}",
                    st == 200 and len(body) > 5000,
                    "HTTP 200 & >5KB",
                    f"HTTP {st} ({len(body)} bytes)",
                )

            # CSKH static JS/CSS assets served directly by nginx `/assets/crm_cskh/cskh/*`
            for cskh_asset in (
                "/assets/crm_cskh/cskh/cskh.js",
                "/assets/crm_cskh/cskh/cskh.css",
                "/assets/crm_cskh/cskh/p2-api.js",
                "/assets/crm_cskh/cskh/p3-api.js",
                "/assets/crm_cskh/cskh/quality-overview.js",
            ):
                st, _, body = http_request(
                    anon_no_redirect,
                    f"{base_url}{cskh_asset}",
                    host_header=args.site_name,
                )
                slug = cskh_asset.rsplit("/", 1)[-1]
                record(
                    f"http.asset.cskh.{slug}",
                    st == 200 and len(body) > 200,
                    "HTTP 200 & >200B",
                    f"HTTP {st} ({len(body)} bytes)",
                )

            # Guest login page (Vietnamese default language hook check) & Guest `/cskh/quality-overview` guard
            st_login, _, login_body = http_request(
                anon_no_redirect, f"{base_url}/login", host_header=args.site_name
            )
            login_html = login_body.decode("utf-8", errors="replace")
            record(
                "http.guest.login_200",
                st_login == 200 and len(login_html) > 500,
                "HTTP 200 & non-empty HTML",
                f"HTTP {st_login} (len={len(login_html)})",
            )

            st_guest_cskh, _, _ = http_request(
                anon_no_redirect,
                f"{base_url}/cskh/quality-overview",
                host_header=args.site_name,
            )
            record(
                "http.guest.cskh_denied",
                st_guest_cskh in (302, 303, 401, 403),
                "302/303/401/403",
                st_guest_cskh,
            )

            # Authenticated session checks for `/crm` SPA + its hashed Vite chunks + `/cskh` + `/cskh/quality-overview`
            jar = http.cookiejar.CookieJar()
            auth_opener = make_opener(follow_redirects=True, cookie_jar=jar)
            st_auth, _, _auth_raw = http_request(
                auth_opener,
                f"{base_url}/api/method/login",
                host_header=args.site_name,
                method="POST",
                form_data={"usr": "Administrator", "pwd": admin_pw},
            )
            record("http.auth.login", st_auth == 200, 200, st_auth)

            st_crm, _, crm_raw = http_request(
                auth_opener, f"{base_url}/crm", host_header=args.site_name
            )
            crm_html = crm_raw.decode("utf-8", errors="replace")
            record(
                "http.auth.crm_page_200",
                st_crm == 200 and 'id="app"' in crm_html,
                "HTTP 200 with #app",
                f"HTTP {st_crm} (len={len(crm_html)})",
            )

            crm_js_matches = re.findall(
                r'src="(/assets/crm/frontend/assets/[^"]+\.js)"', crm_html
            )
            crm_css_matches = re.findall(
                r'href="(/assets/crm/frontend/assets/[^"]+\.css)"', crm_html
            )
            record(
                "http.auth.crm_spa_entry_refs",
                bool(crm_js_matches) and bool(crm_css_matches),
                "non-empty JS & CSS refs",
                {"js": crm_js_matches[:2], "css": crm_css_matches[:2]},
            )

            for spa_asset in (crm_js_matches[0], crm_css_matches[0]):
                st_spa, _, spa_bytes = http_request(
                    anon_no_redirect,
                    f"{base_url}{spa_asset}",
                    host_header=args.site_name,
                )
                record(
                    f"http.asset.crm_spa.{Path(spa_asset).suffix.lstrip('.')}",
                    st_spa == 200 and len(spa_bytes) > 1000,
                    "HTTP 200 & >1KB",
                    f"HTTP {st_spa} ({len(spa_bytes)} bytes)",
                )

            for cskh_route in ("/cskh/dashboard", "/cskh/quality-overview"):
                st_page, _, page_raw = http_request(
                    auth_opener, f"{base_url}{cskh_route}", host_header=args.site_name
                )
                page_html = page_raw.decode("utf-8", errors="replace")
                has_vi_label = any(
                    term in page_html
                    for term in (
                        "Tổng quan",
                        "Chất lượng",
                        "Phân bổ",
                        "Múi giờ",
                        "Đang đăng nhập",
                    )
                )
                slug_route = cskh_route.strip("/").replace("/", "_")
                record(
                    f"http.auth.route.{slug_route}",
                    st_page == 200
                    and 'id="cskh-root"' in page_html
                    and "Asia/Ho_Chi_Minh" in page_html,
                    "HTTP 200 with #cskh-root & Asia/Ho_Chi_Minh",
                    f"HTTP {st_page} (len={len(page_html)})",
                )
                record(
                    f"http.vi_labels.{slug_route}",
                    has_vi_label,
                    "Vietnamese UI labels rendered",
                    has_vi_label,
                )

            # Check Vietnamese translation dictionary injected into /crm boot HTML (raw UTF-8 or \uXXXX JSON-escaped)
            crm_unescaped = re.sub(
                r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), crm_html
            )
            crm_has_vi_boot = any(
                term in crm_unescaped
                for term in (
                    "Khách tiềm năng",
                    "Cơ hội",
                    "Cuộc gọi",
                    "Công việc",
                    "Ghi chú",
                )
            )
            record(
                "http.vi_labels.crm_boot_messages",
                crm_has_vi_boot,
                "Vietnamese translated_messages in /crm boot",
                crm_has_vi_boot,
            )

            # 6. Check background worker/scheduler/websocket container health
            ps_after = run_cmd(
                [*compose_base, "ps", "--format", "json"], env=compose_env
            )
            states = {
                json.loads(line)["Service"]: json.loads(line)["State"]
                for line in ps_after.stdout.splitlines()
                if line.strip()
            }
            for svc in ("backend", "frontend", "websocket", "queue-short", "scheduler"):
                record(
                    f"runtime.service.{svc}",
                    states.get(svc) == "running",
                    "running",
                    states.get(svc),
                )

            def filter_export_lines(
                proc: subprocess.CompletedProcess[str],
            ) -> list[str]:
                combined = f"{proc.stdout}\n{proc.stderr}".splitlines()
                return [
                    ln.strip()
                    for ln in combined
                    if any(
                        k in ln.lower()
                        for k in (
                            "export job",
                            "crm cskh",
                            "module",
                            "warning",
                            "error",
                            "issue",
                            "skip",
                        )
                    )
                ]

            schema_audit = {
                "modules_txt": runtime_info["modules_txt"],
                "module_defs_for_crm_cskh": runtime_info["module_defs_for_app"],
                "module_def_titlecase_crm_cskh_exists": runtime_info[
                    "module_def_crm_cskh_titlecase_exists"
                ],
                "cskh_export_job_json_module": runtime_info["export_job_json_module"],
                "cskh_export_job_db_module": cskh_by_name.get("CSKH Export Job"),
                "cskh_export_job_table_exists": runtime_info["export_job_table_exists"],
                "cskh_doctypes_by_module": {
                    mod: sorted(name for name, m in cskh_by_name.items() if m == mod)
                    for mod in sorted(set(cskh_by_name.values()))
                },
                "install_app_crm_cskh_filtered_lines": filter_export_lines(
                    install_cskh_res
                ),
                "migrate_1_filtered_lines": filter_export_lines(migrate_1_res),
                "migrate_2_filtered_lines": filter_export_lines(migrate_2_res),
            }

            report = {
                "schema_version": "cskh-p4-01-clean-site-verify-v1",
                "work_package": "P4-01",
                "gate_alignment": "G02",
                "started_at_utc": started_at,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "app_image_tested": args.app_image,
                "image_id": img_info.get("Id"),
                "site_name": args.site_name,
                "http_loopback_port": args.port,
                "list_apps": runtime_info["list_apps"],
                "upstream_revisions": {
                    "frappe": runtime_info["frappe_sha"],
                    "crm": runtime_info["crm_sha"],
                    "crm_tracked_modified_files": runtime_info["crm_modified"],
                    "crm_cskh_tree_sha": runtime_info["manifest"]["apps"]["crm_cskh"][
                        "crm_cskh_tree_sha"
                    ],
                    "apps_tree_sha": runtime_info["manifest"]["apps"]["crm_cskh"][
                        "apps_tree_sha"
                    ],
                },
                "frontend_bundles_verified": {
                    "frappe_desk_js": runtime_info["desk_js"],
                    "frappe_desk_css": runtime_info["desk_css"],
                    "frappe_web_js": runtime_info["web_js"],
                    "crm_spa_js": crm_js_matches[0],
                    "crm_spa_css": crm_css_matches[0],
                    "cskh_assets": [
                        "/assets/crm_cskh/cskh/cskh.js",
                        "/assets/crm_cskh/cskh/cskh.css",
                        "/assets/crm_cskh/cskh/p2-api.js",
                        "/assets/crm_cskh/cskh/p3-api.js",
                        "/assets/crm_cskh/cskh/quality-overview.js",
                    ],
                },
                "schema_audit": schema_audit,
                "summary": {
                    "total_assertions": len(assertions),
                    "passed": sum(1 for a in assertions if a["verdict"] == "PASS"),
                    "failed": sum(1 for a in assertions if a["verdict"] != "PASS"),
                    "verdict": "PASS",
                },
                "assertions": assertions,
            }
            out_file = Path(args.out)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(
                f"[verify-clean-site] PASS ({report['summary']['passed']}/{report['summary']['total_assertions']} assertions) -> {out_file}"
            )
            return 0
        finally:
            run_cmd(
                [*compose_base, "down", "-v", "--remove-orphans"],
                env=compose_env,
                check=False,
                timeout=180,
            )


if __name__ == "__main__":
    raise SystemExit(main())
